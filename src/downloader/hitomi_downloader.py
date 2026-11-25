"""hitomi.la album downloader.

Reuses everything of AlbumDownloader that is not site-specific: temp folder and resume,
format conversion (original / WEBP / JPEG / JPEG XL / PNG / AVIF), sequential renaming, CBZ archive,
moving the finished album to Downloads. Only reading the gallery and fetching the images
are different (see src/hitomi.py).

Cautious with the site: images in parallel by Speed level as in HITOMI_WORKERS (1, 2, 2, 3, 4),
a pause after each image, and when hitomi answers 503 / 429 ("soft ban") every hitomi download
pauses (plus a random 45-60 s extra) and the same image is asked again. After
HITOMI_SLOWDOWN_LIMIT soft bans in the same gallery, the rest of it goes one image at a time.
"""

from __future__ import annotations

import random
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from src import hitomi, image_formats, stats, tags as tag_utils, throttle
from src.config import (
    CHUNK_SIZE,
    CONNECTION_TIMEOUT,
    CREATE_CBZ,
    HITOMI_IMAGE_DELAY,
    HITOMI_SLOWDOWN_LIMIT,
    HITOMI_SLOWDOWN_TRIES,
    HITOMI_SUBFOLDER,
    HITOMI_WORKERS,
    IMAGE_FETCH_RETRIES,
    MAX_WORKERS,
    RENAME_SEQUENTIAL,
)
from src.file_utils import write_on_session_log
from src.managers.live_manager import LiveManager

from .album_downloader import AlbumDownloader


class _Refused(Exception):
    """The image server answered 403 / 404: the address is wrong (old gg.js) or the format is missing."""


class _TooBusy(requests.RequestException):
    """hitomi kept asking to slow down (503 / 429) for the same image: leave it to 'Retry Failed'."""


class _Gate:
    """How many images of the gallery may download at the same time (can be lowered meanwhile)."""

    def __init__(self, limit: int) -> None:
        self.limit = max(1, limit)
        self._active = 0
        self._cond = threading.Condition()

    def __enter__(self) -> "_Gate":
        with self._cond:
            while self._active >= self.limit:
                self._cond.wait()
            self._active += 1
        return self

    def __exit__(self, *_exc) -> None:
        with self._cond:
            self._active -= 1
            self._cond.notify_all()

    def lower_to(self, limit: int) -> None:
        """New, lower limit: images already downloading finish, the next ones follow it."""
        with self._cond:
            self.limit = max(1, min(self.limit, limit))


class HitomiAlbumDownloader(AlbumDownloader):
    """Download a hitomi.la gallery with the same output as an E-Hentai one."""

    SITE_FOLDER = HITOMI_SUBFOLDER

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
        url_error = hitomi.validate_hitomi_url(url)
        if url_error:
            raise ValueError(url_error)
        self.url = hitomi.canonical_url(url)
        self.live_manager = live_manager
        self.image_format = image_formats.normalize_name(image_format)
        self.session = hitomi.create_hitomi_session()
        self.info = hitomi.fetch_gallery_info(self.url, session=self.session)

        self.gallery_id = self.info["id"]
        self.clean_album_name = self.info["title"]
        # Same pattern as E-Hentai ("<title>_g_<id>_<token>"): unique working folder name
        self.album_name = f"{self.clean_album_name}_hitomi_{self.gallery_id}"
        self.pages: list[dict] = self.info["pages"]
        self.total_images = len(self.pages)
        self.gallery_tags = tag_utils.from_hitomi_info(self.info)
        self.extra_info = {
            "Writer": ", ".join(self.info.get("artists", [])),
            "Series": ", ".join(self.info.get("parodies", [])),
            "Tags": ", ".join(self.info.get("tags", [])),
        }
        self._headers = hitomi.image_headers(self.gallery_id)
        self._gate = _Gate(1)  # set for each pass by _download_pages
        self._slowdowns = 0
        self._slowdowns_lock = threading.Lock()

        if self.image_format == "original":
            note = " (hitomi has no original files: the WebP images are saved as they are)"
        elif self.image_format in hitomi.SERVER_FORMATS:
            note = f" ({image_formats.label_for(self.image_format)} taken from the site when available, no conversion)"
        else:
            note = ""
        self.live_manager.update_log(
            "Source",
            f"\"{self.clean_album_name}\" — {self.total_images} images — from hitomi.la{note}",
        )
        self._init_storage()

    # ------------------------------------------------------------------ images

    def _slowed_down(self, response: requests.Response, url: str) -> None:
        """hitomi answered 503 / 429 for an image: pause hitomi, and go one image at a time if it repeats."""
        ban = hitomi.report_slowdown(response, f"image server ({url})")
        if not ban.new:
            return  # another thread already started this pause
        with self._slowdowns_lock:
            self._slowdowns += 1
            count = self._slowdowns
        source = "asked by hitomi" if ban.asked else "wait"
        self.live_manager.update_log(
            "Soft ban",
            f"⚠ hitomi soft ban (HTTP {response.status_code}, too many requests): hitomi downloads paused for "
            f"{ban.total} s ({ban.wait:.0f} s {source} + {ban.extra:.0f} s extra, to be safe). Then the same "
            "images are asked again; E-Hentai and nhentai downloads go on.",
        )
        if count >= HITOMI_SLOWDOWN_LIMIT and self._gate.limit > 1:
            self._gate.lower_to(1)
            self.live_manager.update_log(
                "Soft ban",
                f"hitomi soft ban {count} times in this gallery: the rest of it is downloaded one image at a time.",
            )

    def _get_image(self, url: str, page_number: int) -> requests.Response:
        """Download one image URL (streamed, with progress). Raises on failure.

        A 503 / 429 does not use up an attempt: hitomi is paused and the same image is asked
        again, up to HITOMI_SLOWDOWN_TRIES times (then _TooBusy).
        """
        last_error: Exception | None = None
        attempt = slowdowns = 0
        while attempt < max(1, IMAGE_FETCH_RETRIES):
            throttle.wait_while_paused()
            throttle.wait_while_banned(throttle.SITE_HI)
            try:
                response = self.session.get(url, timeout=CONNECTION_TIMEOUT, headers=self._headers, stream=True)
                if response.status_code in (403, 404):
                    response.close()
                    raise _Refused(f"HTTP {response.status_code} on {url}")
                if response.status_code in hitomi.SLOWDOWN_CODES:
                    response.close()
                    slowdowns += 1
                    if slowdowns > HITOMI_SLOWDOWN_TRIES:
                        raise _TooBusy(f"hitomi kept answering HTTP {response.status_code} (too many requests)")
                    self._slowed_down(response, url)
                    continue  # wait for the pause at the top of the loop, then the same image
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
                        stats.add_bytes("hitomi", len(chunk))
                        if hasattr(self.live_manager, "update_page_stream"):
                            self.live_manager.update_page_stream(page_number, downloaded, total_bytes)
                if total_bytes and downloaded < total_bytes:
                    raise requests.RequestException(f"incomplete image ({downloaded}/{total_bytes} bytes)")
                response._content = b"".join(chunks)  # pylint: disable=protected-access
                if hasattr(self.live_manager, "finish_page_stream"):
                    self.live_manager.finish_page_stream(page_number)
                return response
            except _TooBusy:
                raise
            except requests.RequestException as err:
                if throttle.wait_if_offline(err):  # no internet: wait for it, then try again
                    continue
                last_error = err
                attempt += 1
                if attempt < IMAGE_FETCH_RETRIES:
                    throttle.sleep(2 ** attempt + random.uniform(0.5, 1.5))  # noqa: S311
        raise requests.RequestException(str(last_error or "download failed"))

    def _fetch_page_image(self, page_number: int) -> tuple[requests.Response | None, str]:
        """Download the image of one page: the chosen format first, then the others.

        A 403 / 404 usually means gg.js changed meanwhile: it is read again once and the
        page tried again. Returns (response, file name) or (None, last error).
        """
        page = self.pages[page_number - 1]
        last_error = ""
        gg = None
        for refresh in (False, True):
            try:
                gg = hitomi.gg_data(self.session, stale=gg if refresh else None)
            except (RuntimeError, ValueError) as err:
                return None, f"image addresses (gg.js) not readable: {err}"
            refused = False
            for ext in hitomi.formats_to_try(page, self.image_format):
                url = hitomi.image_url(page["hash"], ext, gg)
                try:
                    return self._get_image(url, page_number), f"{page_number}.{ext}"
                except _Refused as err:
                    last_error, refused = str(err), True
                except _TooBusy as err:
                    return None, str(err)  # another format would only ask the busy site again
                except requests.RequestException as err:
                    last_error = f"{url}: {err}"
            if not refused:
                break  # network / server problem, not a stale address: 'Retry Failed' later
            if not refresh:
                self.live_manager.update_log(
                    "Image addresses", f"Page {page_number}: the image server refused it, reading gg.js again...",
                )
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
                item = hitomi.reader_url(self.gallery_id, page_number)
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

            with self._gate:
                response, name_or_error = self._fetch_page_image(page_number)
                if response is not None:
                    throttle.sleep(random.uniform(*HITOMI_IMAGE_DELAY))  # noqa: S311  # pause inside the gate
            if response is None:
                _fail(f"image not available ({name_or_error}).")
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
            if hasattr(self.live_manager, "count_downloaded_image"):  # GUI session counter
                self.live_manager.count_downloaded_image()
            stats.add_image("hitomi")
            self.images_downloaded += 1
            _mark_done()

        workers = max(1, min(MAX_WORKERS, HITOMI_WORKERS.get(throttle.get_speed(), 2)))
        if self._slowdowns >= HITOMI_SLOWDOWN_LIMIT:
            workers = 1  # second pass of a gallery that was already slowed down
        self._gate = _Gate(workers)
        self.live_manager.update_log(
            "Speed", f"Speed: {throttle.speed_name()} ({workers} parallel download(s); fewer on hitomi, "
                     "to be cautious).")
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(worker, n): n for n in range(1, self.total_images + 1)}
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as err:  # pylint: disable=broad-except
                    item = hitomi.reader_url(self.gallery_id, futures[future])
                    self.live_manager.update_log("Worker error", f"Unhandled error for {item}: {err}")
                    write_on_session_log(item)
                    with failed_lock:
                        failed.append(item)
                    self.live_manager.update_task(task, advance=1)
        return failed
