"""nhentai.net album downloader.

Reuses everything of AlbumDownloader that is not site-specific: temp folder and resume,
format conversion (AUTO / JPG / PNG / WEBP / original), sequential renaming, CBZ archive,
moving the finished album to Downloads. Only reading the gallery and fetching the images
are different.
"""

from __future__ import annotations

import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from src import nhentai, throttle
from src.config import (
    CHUNK_SIZE,
    CONNECTION_TIMEOUT,
    CREATE_CBZ,
    IMAGE_FETCH_RETRIES,
    MAX_WORKERS,
    NHENTAI_IMAGE_DELAY,
    NHENTAI_MAX_MIRRORS,
    RENAME_SEQUENTIAL,
)
from src.file_utils import write_on_session_log
from src.managers.live_manager import LiveManager

from .album_downloader import AlbumDownloader

# Page number appended to failed items, e.g. "https://nhentai.net/g/123/7/"
_FAILED_ITEM = "{base}{page}/"


class _NotFound(Exception):
    """The mirror answered 404 for this file name (wrong file type)."""


class NHentaiAlbumDownloader(AlbumDownloader):
    """Download an nhentai.net gallery with the same output as an E-Hentai one."""

    def __init__(  # pylint: disable=super-init-not-called
        self,
        url: str,
        live_manager: LiveManager,
        image_format: str = "original",
        rename_sequential: bool | None = None,
        create_cbz: bool | None = None,
    ) -> None:
        """Read the gallery data (one request) and prepare the album folder."""
        self.rename_sequential = RENAME_SEQUENTIAL if rename_sequential is None else bool(rename_sequential)
        self.create_cbz = CREATE_CBZ if create_cbz is None else bool(create_cbz)
        url_error = nhentai.validate_nhentai_url(url)
        if url_error:
            raise ValueError(url_error)
        self.url = nhentai.canonical_url(url)
        self.live_manager = live_manager
        self.image_format = (image_format or "original").lower().strip()
        self.session = nhentai.create_nhentai_session()
        self.info = nhentai.fetch_gallery_info(self.url, session=self.session)

        self.gallery_id = self.info["id"]
        self.clean_album_name = self.info["title"]
        # Same pattern as E-Hentai ("<title>_g_<id>_<token>"): unique working folder name
        self.album_name = f"{self.clean_album_name}_nhentai_{self.gallery_id}"
        self.page_exts: list[str] = self.info["pages"]
        self.total_images = len(self.page_exts)
        self.extra_info = {
            "Writer": ", ".join(self.info.get("artists", [])),
            "Series": ", ".join(self.info.get("parodies", [])),
            "Tags": ", ".join(self.info.get("tags", [])),
        }
        # Mirrors that failed with a network error are moved to the back of the list
        self._mirrors = nhentai.image_servers(self.session)
        self._mirrors_lock = threading.Lock()

        self.live_manager.update_log(
            "Source",
            f"\"{self.clean_album_name}\" — {self.total_images} images — from nhentai.net",
        )
        self._init_storage()

    # ------------------------------------------------------------------ images

    def _mirror_order(self) -> list[str]:
        """Mirrors to try for one image, best first."""
        with self._mirrors_lock:
            return self._mirrors[: max(1, NHENTAI_MAX_MIRRORS)]

    def _demote_mirror(self, mirror: str) -> None:
        """Move a mirror that just failed to the end of the list (for every thread)."""
        with self._mirrors_lock:
            if mirror in self._mirrors and self._mirrors[0] == mirror and len(self._mirrors) > 1:
                self._mirrors.remove(mirror)
                self._mirrors.append(mirror)

    def _get_image(self, url: str, page_number: int) -> requests.Response:
        """Download one image URL (streamed, with progress). Raises on failure."""
        last_error: Exception | None = None
        for attempt in range(max(1, IMAGE_FETCH_RETRIES)):
            throttle.wait_while_paused()
            throttle.wait_while_banned()
            try:
                response = self.session.get(
                    url, timeout=CONNECTION_TIMEOUT, headers=nhentai.image_headers(), stream=True,
                )
                if response.status_code == 404:
                    response.close()
                    raise _NotFound(url)
                if response.status_code == 429:
                    response.close()
                    nhentai.report_rate_limit(response, f"image server ({url})")
                    continue
                response.raise_for_status()
                content_type = response.headers.get("Content-Type", "")
                if content_type and not content_type.startswith(("image/", "application/octet-stream")):
                    response.close()
                    raise requests.RequestException(f"not an image ({content_type})")

                total_bytes = int(response.headers.get("content-length", 0) or 0)
                chunks, downloaded = [], 0
                for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
                    if chunk:
                        chunks.append(chunk)
                        downloaded += len(chunk)
                        if hasattr(self.live_manager, "update_page_stream"):
                            self.live_manager.update_page_stream(page_number, downloaded, total_bytes)
                if total_bytes and downloaded < total_bytes:
                    raise requests.RequestException(f"incomplete image ({downloaded}/{total_bytes} bytes)")
                response._content = b"".join(chunks)  # pylint: disable=protected-access
                if hasattr(self.live_manager, "finish_page_stream"):
                    self.live_manager.finish_page_stream(page_number)
                return response
            except requests.ConnectionError as err:
                # Server unreachable (DNS, refused): switch mirror at once instead of waiting
                raise requests.RequestException(f"server unreachable: {err}") from err
            except requests.RequestException as err:
                last_error = err
                if attempt < IMAGE_FETCH_RETRIES - 1:
                    time.sleep(2 ** (attempt + 1) + random.uniform(0.5, 1.5))  # noqa: S311
        raise requests.RequestException(str(last_error or "download failed"))

    def _fetch_page_image(self, page_number: int) -> tuple[requests.Response | None, str]:
        """Find and download the image of one page: every mirror, and every file type on 404.

        Returns (response, file name) or (None, last error).
        """
        path = nhentai.page_path(self.info, page_number)
        last_error = ""
        for mirror in self._mirror_order():
            for candidate in nhentai.path_candidates(path):
                url = nhentai.image_url(mirror, candidate)
                try:
                    return self._get_image(url, page_number), f"{page_number}.{candidate.rsplit('.', 1)[-1]}"
                except _NotFound:
                    last_error = f"404 on {url}"
                    continue  # this mirror answers: try the next file type
                except requests.RequestException as err:
                    last_error = f"{mirror}: {err}"
                    self._demote_mirror(mirror)
                    self.live_manager.update_log(
                        "Switching server",
                        f"Image server {mirror} failed for page {page_number} ({err}): trying another one...",
                    )
                    break  # server problem: next mirror
        return None, last_error

    # ------------------------------------------------------------------ album

    def _download_pages(self) -> list[str]:
        """Download every page of the gallery; return the failed items."""
        self._clean_stale_parts()
        self._local_index = self._scan_local_pages()

        if hasattr(self.live_manager, "start_album"):
            self.live_manager.start_album(
                album_title=self.clean_album_name or self.album_name,
                total_images=self.total_images,
            )
        self.live_manager.update_log(
            "Start",
            f"Starting album download: \"{self.clean_album_name}\" ({self.total_images} images)",
        )
        self.live_manager.add_overall_task(description=self.album_name, num_tasks=1)
        task = self.live_manager.add_task(current_task=0, total=self.total_images)

        failed: list[str] = []
        failed_lock = threading.Lock()

        def worker(page_number: int) -> None:
            def _mark_done() -> None:
                with self._processed_images_lock:
                    self._processed_images_count += 1
                    done_count = self._processed_images_count
                if hasattr(self.live_manager, "update_album_progress"):
                    self.live_manager.update_album_progress(done_count, self.total_images, page_number)
                self.live_manager.update_task(task, advance=1)

            def _fail(reason: str) -> None:
                item = _FAILED_ITEM.format(base=self.url, page=page_number)
                self.live_manager.update_log("Download error", f"Page {page_number}: {reason}")
                write_on_session_log(item)
                with failed_lock:
                    failed.append(item)
                _mark_done()

            padded = self.format_page_index(page_number)
            if self._find_and_prepare_local_page(page_number):
                self.live_manager.update_log(
                    "Already present", f"Page {padded}/{self.total_images} already on disk.",
                )
                _mark_done()
                return

            response, name_or_error = self._fetch_page_image(page_number)
            if response is None:
                _fail(f"no image server answered ({name_or_error}).")
                return

            original_filename = f"{padded}_{name_or_error}"
            try:
                self.download_picture(response, original_filename)
            except Exception as err:  # pylint: disable=broad-except
                _fail(f"could not save the image: {err}")
                return

            self.live_manager.update_log(
                "Download",
                f"Downloaded page {padded}/{self.total_images}: {self._get_target_filename(original_filename)}",
            )
            _mark_done()
            time.sleep(random.uniform(*NHENTAI_IMAGE_DELAY))  # noqa: S311

        workers = max(1, min(MAX_WORKERS, throttle.current_workers()))
        self.live_manager.update_log("Speed", f"Speed: {throttle.speed_name()} ({workers} parallel download(s)).")
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(worker, n): n for n in range(1, self.total_images + 1)}
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as err:  # pylint: disable=broad-except
                    item = _FAILED_ITEM.format(base=self.url, page=futures[future])
                    self.live_manager.update_log("Worker error", f"Unhandled error for {item}: {err}")
                    write_on_session_log(item)
                    with failed_lock:
                        failed.append(item)
                    self.live_manager.update_task(task, advance=1)
        return failed
