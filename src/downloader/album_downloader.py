"""Album downloading module.

This module provides the `AlbumDownloader` class for downloading image albums from a
given URL. It handles page crawling, image extraction, and downloading with progress
tracking.
"""

import os
import random
import re
import shutil
import subprocess
import threading
import time
import zipfile
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse

from requests import Response, Session

from src.config import (
    CREATE_CBZ,
    DOWNLOAD_FOLDER,
    IMAGE_FETCH_RETRIES,
    IMAGE_SERVER_RETRIES,
    MAX_WORKERS,
    RENAME_SEQUENTIAL,
    TEMP_FOLDER,
)
from src import throttle
from src.crawler.crawler import Crawler
from src.crawler.crawler_utils import extract_nl_value, get_picture_pages
from src.file_utils import (
    create_download_directory,
    sanitize_directory_name,
    write_on_session_log,
)
from src.general_utils import create_session, fetch_page, validate_gallery_url
from src.managers.live_manager import LiveManager

from .download_utils import fetch_with_retries, prepare_headers

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".avif"}

# Leading page index of a local file: "07_abc.jpg" -> 7, "007.jpg" -> 7
_LOCAL_INDEX_RE = re.compile(r"^(\d+)(?:_|$)")

# E-Hentai serves this placeholder instead of the real image when the image quota is exhausted
_QUOTA_EXCEEDED_MARKERS = ("/509.gif", "/509s.gif")

# Suffix of temporary files: an image is written as "<name>.part" and renamed only when complete,
# so an interrupted run can never leave a truncated file that looks like a finished page.
PART_SUFFIX = ".part"

# Format names for ImageMagick's "FORMAT:path" output syntax (needed because ".part" hides the type)
_MAGICK_FORMATS = {"jpg": "JPEG", "jpeg": "JPEG", "png": "PNG", "webp": "WEBP"}
_PIL_FORMATS = {"jpg": "JPEG", "jpeg": "JPEG", "png": "PNG", "webp": "WEBP"}


class AlbumDownloader:
    """Handles the process of downloading an image album.

    This class fetches the album's pages, extracts image links, and downloads the images
    while tracking progress.
    """

    def __init__(
        self,
        url: str,
        live_manager: LiveManager,
        image_format: str = "original",
        rename_sequential: bool | None = None,
        create_cbz: bool | None = None,
    ) -> None:
        """Initialize the AlbumDownloader with album URL, live manager, and optional format."""
        self.rename_sequential = RENAME_SEQUENTIAL if rename_sequential is None else bool(rename_sequential)
        self.create_cbz = CREATE_CBZ if create_cbz is None else bool(create_cbz)
        # Fail fast with a clear message on malformed links (e.g. a truncated token)
        url_error = validate_gallery_url(url)
        if url_error:
            raise ValueError(url_error)
        parsed = urlparse(url)
        path = parsed.path if parsed.path.endswith("/") else f"{parsed.path}/"
        self.url = f"{parsed.scheme}://{parsed.netloc}{path}"
        self.live_manager = live_manager
        self.image_format = (image_format or "original").lower().strip()
        self.session = create_session()
        self.initial_soup = fetch_page(
            self.url,
            session=self.session,
            referer="https://e-hentai.org/",
        )
        if hasattr(self.initial_soup, "fetched_url"):
            self.url = self.initial_soup.fetched_url

        self.crawler = Crawler(
            url=self.url,
            initial_soup=self.initial_soup,
            live_manager=self.live_manager,
            session=self.session,
        )
        self.album_name = self.crawler.get_album_name()
        self.clean_album_name = self.crawler.get_clean_album_name()
        self.total_images = self.crawler.get_total_images()

        # Tell the user which gallery this is and which site it is really downloaded from
        requested_domain = urlparse(url).netloc.lower()
        used_domain = urlparse(self.url).netloc.lower()
        fallback_note = (
            f" ({requested_domain} did not answer correctly, using {used_domain})"
            if used_domain != requested_domain else ""
        )
        self.live_manager.update_log(
            "Source",
            f"\"{self.clean_album_name}\" — {self.total_images} images — from {used_domain}{fallback_note}",
        )
        self._init_storage()

    def _init_storage(self) -> None:
        """Pick (or create) the album folder and reset the per-album counters.

        Needs self.album_name, self.clean_album_name and self.total_images.
        """
        self.extra_info: dict[str, str] = getattr(self, "extra_info", {})
        self.index_width = max(2, len(str(self.total_images))) if self.total_images > 0 else 3
        # Albums are downloaded into TEMP_FOLDER and moved to DOWNLOAD_FOLDER only once complete.
        # A partial download is resumed from wherever it already is: first the temp folder,
        # then Downloads (albums started before the temp folder existed are finished in place).
        self.download_path = ""
        for base in (TEMP_FOLDER, DOWNLOAD_FOLDER):
            for name in (self.clean_album_name, self.album_name):
                candidate = Path(base) / sanitize_directory_name(name)
                if candidate.is_dir() and any(candidate.iterdir()):
                    self.download_path = str(candidate)
                    break
            if self.download_path:
                break
        if not self.download_path:
            self.download_path = create_download_directory(self.album_name, base_folder=TEMP_FOLDER)

        self._processed_images_count = 0
        self._processed_images_lock = threading.Lock()
        self._quota_exceeded = threading.Event()
        # Snapshot of local files grouped by page index, built once per album
        self._local_index: dict[int, list[Path]] = {}

    def format_page_index(self, page_idx: int) -> str:
        """Format page index with dynamic zero padding based on total images (e.g. 01, 001, 0001)."""
        return f"{page_idx:0{self.index_width}d}"

    @staticmethod
    def _part_path(target_path: Path) -> Path:
        """Return the temporary ".part" path used while target_path is being written."""
        return target_path.with_name(f"{target_path.name}{PART_SUFFIX}")

    @staticmethod
    def _discard(path: Path) -> None:
        """Delete a file if it exists, ignoring errors."""
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass

    def _save_bytes(self, target_path: Path, content: bytes) -> None:
        """Write bytes to "<target>.part" and rename it to target_path only once fully written."""
        target_path.parent.mkdir(parents=True, exist_ok=True)
        part = self._part_path(target_path)
        try:
            with part.open("wb") as fh:
                fh.write(content)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(part, target_path)
        except BaseException:
            self._discard(part)
            raise

    def _clean_stale_parts(self) -> None:
        """Remove ".part" leftovers of an interrupted run (they are never complete images)."""
        folder = Path(self.download_path)
        if not folder.is_dir():
            return
        removed = 0
        for f in folder.glob(f"*{PART_SUFFIX}"):
            if f.is_file():
                self._discard(f)
                removed += 1
        if removed:
            self.live_manager.update_log(
                "Resume",
                f"Removed {removed} incomplete file(s) left by an interrupted download.",
            )

    def _is_image_animated(self, content: bytes) -> bool:
        """Check if image content has animation / multiple frames."""
        try:
            import io
            from PIL import Image
            with Image.open(io.BytesIO(content)) as img:
                return bool(getattr(img, "is_animated", False) and getattr(img, "n_frames", 1) > 1)
        except Exception:
            return False

    def _get_target_filename(self, original_filename: str) -> str:
        """Return the target filename according to configured image format."""
        if self.image_format in ("original", "native", ""):
            return original_filename
        stem = Path(original_filename).stem
        if self.image_format == "auto":
            return f"{stem}.jpg"
        ext = self.image_format.lstrip(".")
        if ext in ("jpeg", "jpg"):
            return f"{stem}.jpg"
        if ext == "png":
            return f"{stem}.png"
        if ext == "webp":
            return f"{stem}.webp"
        return original_filename

    def _convert_with_imagemagick(self, content: bytes, target_path: Path, ext: str) -> bool:
        """Attempt conversion using system ImageMagick executable."""
        magick_bin = shutil.which("magick") or shutil.which("convert")
        if not magick_bin:
            return False

        part = self._part_path(target_path)
        try:
            cmd = [magick_bin, "-"]
            if ext in ("jpeg", "jpg"):
                cmd.extend(["-background", "white", "-alpha", "remove", "-alpha", "off", "-quality", "95"])
            elif ext == "webp":
                cmd.extend(["-quality", "95"])
            magick_fmt = _MAGICK_FORMATS.get(ext)
            # The ".part" extension hides the type, so name the output format explicitly
            cmd.append(f"{magick_fmt}:{part}" if magick_fmt else str(part))

            kwargs = {}
            if os.name == "nt":
                kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= getattr(subprocess, "STARTF_USESHOWWINDOW", 1)
                startupinfo.wShowWindow = 0
                kwargs["startupinfo"] = startupinfo

            result = subprocess.run(cmd, input=content, capture_output=True, timeout=30, check=False, **kwargs)
            if result.returncode == 0 and part.exists() and part.stat().st_size > 0:
                os.replace(part, target_path)
                return True
            self._discard(part)
            return False
        except Exception:
            self._discard(part)
            return False

    def _convert_with_pillow(self, content: bytes, target_path: Path, ext: str) -> bool:
        """Attempt conversion using Pillow (PIL)."""
        if ext not in _PIL_FORMATS:
            self._save_bytes(target_path, content)
            return True
        part = self._part_path(target_path)
        try:
            import io
            from PIL import Image

            with Image.open(io.BytesIO(content)) as img:
                if ext in ("jpeg", "jpg"):
                    # Handle transparency for JPEG
                    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
                        bg = Image.new("RGB", img.size, (255, 255, 255))
                        img_rgba = img.convert("RGBA")
                        bg.paste(img_rgba, mask=img_rgba.split()[3])
                        bg.save(part, "JPEG", quality=95)
                    else:
                        img.convert("RGB").save(part, "JPEG", quality=95)
                elif ext == "png":
                    img.save(part, "PNG")
                else:
                    img.save(part, "WEBP", quality=95)
            if part.exists() and part.stat().st_size > 0:
                os.replace(part, target_path)
                return True
            self._discard(part)
            return False
        except Exception:
            self._discard(part)
            return False

    def _detect_image_format(self, content: bytes) -> str:
        """Detect actual image format from binary magic bytes or Pillow."""
        if len(content) >= 3 and content.startswith(b"\xff\xd8\xff"):
            return "jpeg"
        if len(content) >= 8 and content.startswith(b"\x89PNG\r\n\x1a\n"):
            return "png"
        if len(content) >= 12 and content.startswith(b"RIFF") and content[8:12] == b"WEBP":
            return "webp"
        if len(content) >= 6 and (content.startswith(b"GIF87a") or content.startswith(b"GIF89a")):
            return "gif"
        if len(content) >= 2 and content.startswith(b"BM"):
            return "bmp"
        if len(content) >= 12 and content[4:8] == b"ftyp" and content[8:12] in (b"avif", b"avis"):
            return "avif"

        try:
            import io
            from PIL import Image
            with Image.open(io.BytesIO(content)) as img:
                return (img.format or "").lower()
        except Exception:
            return ""

    def _convert_and_save(self, content: bytes, original_filename: str) -> None:
        """Convert image bytes according to requested format, preserving animated images in AUTO mode."""
        stem = Path(original_filename).stem
        actual_fmt = self._detect_image_format(content)
        ext_map = {
            "jpeg": ".jpg",
            "jpg": ".jpg",
            "png": ".png",
            "webp": ".webp",
            "gif": ".gif",
            "bmp": ".bmp",
            "avif": ".avif",
        }
        actual_ext = ext_map.get(actual_fmt, Path(original_filename).suffix.lower() or ".jpg")

        # 1. AUTO mode: check if image is animated
        if self.image_format == "auto":
            if self._is_image_animated(content):
                target_path = Path(self.download_path) / f"{stem}{actual_ext}"
                self._save_bytes(target_path, content)
                self.live_manager.update_log(
                    "Animated image",
                    f"Preserved original animation for {target_path.name}",
                )
                return
            ext = "jpg"
            target_path = Path(self.download_path) / f"{stem}.jpg"
        elif self.image_format in ("original", "native", ""):
            target_path = Path(self.download_path) / f"{stem}{actual_ext}"
            self._save_bytes(target_path, content)
            return
        else:
            # Explicit format chosen by user (force conversion)
            ext = self.image_format.lstrip(".").lower()
            if ext in ("jpeg", "jpg"):
                target_path = Path(self.download_path) / f"{stem}.jpg"
                ext = "jpg"
            elif ext == "png":
                target_path = Path(self.download_path) / f"{stem}.png"
            elif ext == "webp":
                target_path = Path(self.download_path) / f"{stem}.webp"
            else:
                target_path = Path(self.download_path) / f"{stem}{actual_ext}"

        target_path.parent.mkdir(parents=True, exist_ok=True)

        # Check if the content is ALREADY in the target format
        # (Compare true actual format with requested format, NOT the URL file extension!)
        target_fmt = "jpeg" if ext in ("jpg", "jpeg") else ext
        if actual_fmt == target_fmt or (target_fmt == "jpeg" and actual_fmt in ("jpeg", "jpg")):
            self._save_bytes(target_path, content)
            return

        # 1. Try ImageMagick if installed
        if self._convert_with_imagemagick(content, target_path, ext):
            return

        # 2. Fallback to Pillow
        if self._convert_with_pillow(content, target_path, ext):
            return

        # 3. Fallback to saving original raw file with its true extension if conversion failed
        self.live_manager.update_log(
            "Conversion warning",
            f"Failed to convert {original_filename} to {ext} with ImageMagick/Pillow. Saving as {stem}{actual_ext}.",
        )
        fallback_path = Path(self.download_path) / f"{stem}{actual_ext}"
        self._save_bytes(fallback_path, content)

    def download_album(self) -> list[str]:
        """Download all images from the album while tracking progress.

        Returns the list of items (picture pages / image URLs) that failed.
        """
        if self._skip_if_archived():
            return []
        return self._complete_album(self._download_pages())

    def _skip_if_archived(self) -> bool:
        """True (and progress shown as complete) when the album is already a complete CBZ."""
        existing_cbz = self._find_complete_cbz()
        if existing_cbz is not None:
            if hasattr(self.live_manager, "start_album"):
                self.live_manager.start_album(
                    album_title=self.clean_album_name or self.album_name,
                    total_images=self.total_images,
                )
            self.live_manager.update_log(
                "Already archived",
                f"Album already packed in \"{existing_cbz.name}\": skipped.",
            )
            if hasattr(self.live_manager, "update_album_progress"):
                self.live_manager.update_album_progress(self.total_images, self.total_images)
            return True
        return False

    def _download_pages(self) -> list[str]:
        """Download every page of an E-Hentai / ExHentai gallery; return the failed items."""
        album_pages_soups = self.crawler.collect_album_pages_soups()
        num_pages = len(album_pages_soups)
        total_failed_downloads: list[str] = []

        # If total_images wasn't found from metadata, determine it from collected picture pages
        if self.total_images <= 0 and album_pages_soups:
            max_idx = 0
            for soup in album_pages_soups:
                gdt = soup.find("div", {"id": "gdt"})
                containers = gdt.find_all("a", {"href": True}) if gdt else soup.find_all("a", {"href": True})
                for p in get_picture_pages(containers):
                    m = re.search(r"/(\d+)-(\d+)(?:\?|$)", p)
                    if m:
                        max_idx = max(max_idx, int(m.group(2)))
            if max_idx > 0:
                self.total_images = max_idx
                self.index_width = max(2, len(str(self.total_images)))

        # Drop truncated leftovers of an interrupted run, then snapshot existing files once
        self._clean_stale_parts()
        self._local_index = self._scan_local_pages()

        if hasattr(self.live_manager, "start_album"):
            self.live_manager.start_album(
                album_title=self.clean_album_name or self.album_name,
                total_images=self.total_images,
            )
        self.live_manager.update_log(
            "Start",
            f"Starting album download: \"{self.clean_album_name or self.album_name}\" ({self.total_images} images)",
        )

        self.live_manager.add_overall_task(
            description=self.album_name,
            num_tasks=num_pages,
        )

        for current_task, soup in enumerate(album_pages_soups):
            gdt = soup.find("div", {"id": "gdt"})
            containers = gdt.find_all("a", {"href": True}) if gdt else soup.find_all("a", {"href": True})
            picture_pages = get_picture_pages(containers)

            failed_downloads = self._extract_and_download(
                self.session, picture_pages, current_task,
            )
            total_failed_downloads.extend(failed_downloads)

            if failed_downloads:
                self.live_manager.update_log(
                    "Error",
                    f"Some images were not downloaded from gallery page {current_task + 1}.",
                )

            if current_task < num_pages - 1:
                self.live_manager.update_log(
                    "Pause",
                    "Short courtesy pause between gallery pages (anti rate-limit)...",
                )
                time.sleep(random.uniform(1, 5))  # noqa: S311

        if self._quota_exceeded.is_set():
            self.live_manager.update_log(
                "Image limit",
                "E-Hentai image quota exhausted (509): try again later with 'Retry Failed'.",
            )
        return total_failed_downloads

    def _complete_album(self, total_failed_downloads: list[str]) -> list[str]:
        """Check every page is on disk, then make the CBZ or rename/move the folder."""
        # Treat the album as complete only if every page 1..N is actually on disk
        if not total_failed_downloads:
            missing = self._missing_pages()
            if missing:
                self.live_manager.update_log(
                    "Incomplete",
                    f"{len(missing)} pages missing on disk even though no error was reported: {missing[:10]}"
                    f"{'…' if len(missing) > 10 else ''}",
                )
                total_failed_downloads.extend(
                    f"{self.url} (page {self.format_page_index(i)})" for i in missing
                )

        if not total_failed_downloads:
            # CBZ mode: pack the images straight into Downloads/<title>.cbz, no album folder.
            # If the archive cannot be made, fall back to the normal folder so nothing is lost.
            if not (self.create_cbz and self._create_cbz()):
                # Rename while still in the temp folder, then move the finished album to Downloads
                if self.rename_sequential:
                    self._rename_sequential()
                self._finalize_successful_download()
            if hasattr(self.live_manager, "update_album_progress"):
                self.live_manager.update_album_progress(self.total_images, self.total_images)

        return total_failed_downloads

    def _finalize_successful_download(self) -> None:
        """Move the complete album to Downloads/<clean title> (out of the temp folder)."""
        try:
            current_path = Path(self.download_path)
            clean_name = sanitize_directory_name(self.clean_album_name)
            target_path = Path(DOWNLOAD_FOLDER) / clean_name

            if current_path.resolve() == target_path.resolve():
                self.live_manager.update_log(
                    "Completed",
                    f"Album completed in folder: \"{clean_name}\"",
                )
                return

            target_path.parent.mkdir(parents=True, exist_ok=True)
            if target_path.exists():
                for item in current_path.iterdir():
                    dest = target_path / item.name
                    if dest.exists():
                        dest.unlink()
                    shutil.move(str(item), str(dest))
                shutil.rmtree(str(current_path), ignore_errors=True)
            else:
                # shutil.move also works if temp and Downloads are on different drives
                shutil.move(str(current_path), str(target_path))
            self.download_path = str(target_path)

            self.live_manager.update_log(
                "Completed",
                f"Album completed and moved to: \"{target_path}\"",
            )
        except Exception as err:
            self.live_manager.update_log(
                "Notice",
                f"Completed successfully (folder: {self.download_path}); could not rename the folder: {err}",
            )

    # ------------------------------------------------------------------ CBZ archive

    def _cbz_path(self) -> Path:
        """Return "Downloads/<full gallery title>.cbz" (shortened only if the path would be too long)."""
        folder = Path(DOWNLOAD_FOLDER)
        title = self.clean_album_name or self.album_name or "album"
        # 255 chars is the file-name limit; on Windows also stay below the 260-char MAX_PATH
        room = 255 - len(".cbz")
        if os.name == "nt":
            room = min(room, 259 - len(str(folder.resolve())) - 1 - len(".cbz"))
        name = sanitize_directory_name(title, max_length=max(20, room)) or "album"
        return folder / f"{name}.cbz"

    @staticmethod
    def _cbz_page_count(cbz: Path) -> int:
        """Return how many images a CBZ holds (0 if it is missing or unreadable)."""
        try:
            with zipfile.ZipFile(cbz) as zf:
                return sum(1 for n in zf.namelist() if Path(n).suffix.lower() in IMAGE_EXTS)
        except (OSError, zipfile.BadZipFile):
            return 0

    def _find_complete_cbz(self) -> Path | None:
        """Return the album's CBZ in Downloads if it exists and holds every page, else None.

        Also recognizes archives made by earlier versions inside "Downloads/<album>/".
        """
        candidates = [self._cbz_path()]
        old_folder = Path(DOWNLOAD_FOLDER) / sanitize_directory_name(self.clean_album_name)
        if old_folder.is_dir():
            candidates.extend(sorted(old_folder.glob("*.cbz")))
        for cbz in candidates:
            if not cbz.is_file():
                continue
            pages = self._cbz_page_count(cbz)
            if pages > 0 and (self.total_images <= 0 or pages >= self.total_images):
                return cbz
        return None

    def _comic_info_xml(self, page_count: int) -> str:
        """Build a minimal ComicInfo.xml (read by most comic readers: title, page count, source)."""
        from xml.sax.saxutils import escape

        title = escape(self.clean_album_name or self.album_name or "")
        return (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<ComicInfo xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
            'xmlns:xsd="http://www.w3.org/2001/XMLSchema">\n'
            f"  <Title>{title}</Title>\n"
            + "".join(
                f"  <{key}>{escape(value)}</{key}>\n"
                for key, value in getattr(self, "extra_info", {}).items() if value
            )
            + f"  <PageCount>{page_count}</PageCount>\n"
            f"  <Web>{escape(self.url)}</Web>\n"
            "</ComicInfo>\n"
        )

    def _create_cbz(self) -> bool:
        """Pack the album images into "Downloads/<title>.cbz", then delete them and their folder.

        Returns True when the archive was created; on False the images are left untouched.

        Pages are stored as 01.ext..N.ext (padding sized on N) so every reader shows them in
        order, whatever the names on disk. The archive is written to a ".part" file, verified,
        and only then renamed; the images are removed only after a successful verification.
        """
        folder = Path(self.download_path)
        index = self._scan_local_pages()
        if not index:
            self.live_manager.update_log("CBZ skipped", "No images found to archive.")
            return False

        pages = sorted(index)
        duplicated = [i for i in pages if len(index[i]) > 1]
        if duplicated:
            self.live_manager.update_log(
                "CBZ skipped",
                f"Duplicate pages on disk ({duplicated[:10]}{'…' if len(duplicated) > 10 else ''}): "
                "images left as they are.",
            )
            return False

        width = max(2, len(str(max(self.total_images, pages[-1]))))
        cbz_path = self._cbz_path()
        cbz_path.parent.mkdir(parents=True, exist_ok=True)
        part = self._part_path(cbz_path)
        sources: list[Path] = []
        try:
            # Images are already compressed: ZIP_STORED is faster and is what readers expect
            with zipfile.ZipFile(part, "w", compression=zipfile.ZIP_STORED) as zf:
                for idx in pages:
                    src = index[idx][0]
                    ext = ".jpg" if src.suffix.lower() == ".jpeg" else src.suffix.lower()
                    zf.write(src, arcname=f"{idx:0{width}d}{ext}")
                    sources.append(src)
                zf.writestr("ComicInfo.xml", self._comic_info_xml(len(pages)))

            with zipfile.ZipFile(part) as zf:
                bad = zf.testzip()
                stored = sum(1 for n in zf.namelist() if Path(n).suffix.lower() in IMAGE_EXTS)
            if bad is not None or stored != len(sources):
                raise OSError(f"archive verification failed ({bad or f'{stored}/{len(sources)} pages'})")
            os.replace(part, cbz_path)
        except Exception as err:
            self._discard(part)
            self.live_manager.update_log(
                "CBZ failed",
                f"Could not create the CBZ archive ({err}): album saved as a normal folder.",
            )
            return False

        not_removed = 0
        for src in sources:
            try:
                src.unlink()
            except OSError:
                not_removed += 1
        # Remove the now-empty album folder (temp/<album> or an old Downloads/<album>)
        folder_note = ""
        try:
            leftovers = [f for f in folder.iterdir() if f.resolve() != cbz_path.resolve()]
            if not leftovers:
                folder.rmdir()
            else:
                folder_note = f"; folder kept ({len(leftovers)} other file(s) inside)"
        except OSError:
            pass
        note = f" ({not_removed} image(s) could not be deleted)" if not_removed else ""
        self.download_path = str(cbz_path.parent)
        self.live_manager.update_log(
            "CBZ",
            f"Created \"{cbz_path}\" with {len(sources)} pages; extracted images removed{note}{folder_note}.",
        )
        return True

    def download_picture(
            self,
            response: Response,
            filename: str,
        ) -> None:
        """Save an image response (or converted image) to a file."""
        content = response.content
        self._convert_and_save(content, filename)

    def _is_same_file(self, final_path: Path, response: Response, original_filename: str = "") -> bool:
        """Check whether a local file matches the server content length when available."""
        if self.image_format == "auto" and original_filename:
            stem = Path(original_filename).stem
            for ext in (".jpg", ".jpeg", ".webp", ".gif", ".png"):
                p = Path(self.download_path) / f"{stem}{ext}"
                if p.exists() and p.stat().st_size > 0:
                    return True
            return False

        if not final_path.exists():
            return False

        if self.image_format in ("original", "native", "") or (original_filename and final_path.name == original_filename):
            remote_size = response.headers.get("Content-Length")
            if not remote_size:
                # If the remote size is not provided, assume the existing file is valid
                # only if it is non-empty (>= 1 KB — a real image will never be smaller).
                try:
                    return final_path.stat().st_size >= 1024
                except OSError:
                    return False

            try:
                return final_path.stat().st_size == int(remote_size)
            except (OSError, ValueError):
                return False
        else:
            return final_path.stat().st_size > 0

    def _scan_local_pages(self) -> dict[int, list[Path]]:
        """Group image files in the album folder by their leading page index.

        Recognizes both the download naming ("07_abc.jpg") and the final sequential
        naming ("007.jpg"), whatever the zero padding.
        """
        index: dict[int, list[Path]] = {}
        download_dir = Path(self.download_path)
        if not download_dir.exists():
            return index
        try:
            for f in download_dir.iterdir():
                if not f.is_file() or f.suffix.lower() not in IMAGE_EXTS:
                    continue
                match = _LOCAL_INDEX_RE.match(f.stem)
                if not match:
                    continue
                try:
                    if f.stat().st_size <= 0:
                        continue
                except OSError:
                    continue
                index.setdefault(int(match.group(1)), []).append(f)
        except OSError:
            pass
        return index

    def _is_acceptable_local(self, f: Path, expected_name: str) -> bool:
        """Return True if a local file can be kept as-is for the selected format."""
        if f.name == expected_name:
            return True
        # AUTO keeps animated images in their original format instead of converting to JPG
        if self.image_format == "auto" and Path(expected_name).stem == f.stem and f.suffix.lower() in (".gif", ".webp"):
            try:
                return self._is_image_animated(f.read_bytes())
            except OSError:
                return False
        return False

    def _find_and_prepare_local_page(self, page_idx: int) -> bool:
        """Check if image file for page_idx already exists locally (previous run or manual download).

        If found with different padding or format, normalizes/converts it to the target format.
        Returns True if a valid local image exists and is ready, skipping web download.
        """
        candidates = self._local_index.get(page_idx, [])
        if not candidates:
            return False

        padded = self.format_page_index(page_idx)

        def normalized_name(f: Path) -> str:
            """Return the file name re-padded to the current index width (e.g. '7_x.jpg' -> '07_x.jpg')."""
            rest = f.stem[len(_LOCAL_INDEX_RE.match(f.stem).group(1)):]  # "" or "_abc"
            return f"{padded}{rest}{f.suffix}"

        # 1. A file already in its final form
        for f in candidates:
            if f.exists() and self._is_acceptable_local(f, self._get_target_filename(normalized_name(f))):
                return True

        # 2. A file that needs re-padding and/or format conversion
        for f in candidates:
            if not f.exists():
                continue
            try:
                norm = normalized_name(f)
                content = f.read_bytes()
                self._convert_and_save(content, norm)
                target_stem = Path(self._get_target_filename(norm)).stem
                produced = [
                    Path(self.download_path) / f"{target_stem}{ext}"
                    for ext in IMAGE_EXTS
                ]
                produced = [p for p in produced if p.exists() and p.stat().st_size > 0]
                if produced:
                    if all(f.resolve() != p.resolve() for p in produced) and f.exists():
                        f.unlink()
                    return True
            except Exception:
                pass

        return False

    def _missing_pages(self) -> list[int]:
        """Return the page indices (1..total_images) that have no image on disk."""
        if self.total_images <= 0:
            return []
        present = self._scan_local_pages()
        return [idx for idx in range(1, self.total_images + 1) if idx not in present]

    def _rename_sequential(self) -> None:
        """Rename album images to 1..N with zero padding sized on N (min 2 digits).

        Example: 45 images -> 01..45, 120 images -> 001..120, 1500 images -> 0001..1500.
        Runs only when every page 1..N is present exactly once.
        """
        total = self.total_images
        if total <= 0:
            return

        index = self._scan_local_pages()
        missing = [i for i in range(1, total + 1) if i not in index]
        duplicated = [i for i in range(1, total + 1) if len(index.get(i, [])) > 1]
        if missing or duplicated:
            details = []
            if missing:
                details.append(f"missing: {missing[:10]}{'…' if len(missing) > 10 else ''}")
            if duplicated:
                details.append(f"duplicate: {duplicated[:10]}{'…' if len(duplicated) > 10 else ''}")
            self.live_manager.update_log(
                "Rename skipped",
                f"Images not renamed ({'; '.join(details)}).",
            )
            return

        width = max(2, len(str(total)))
        plan: list[tuple[Path, Path]] = []
        for idx in range(1, total + 1):
            src = index[idx][0]
            ext = src.suffix.lower()
            if ext == ".jpeg":
                ext = ".jpg"
            dst = src.with_name(f"{idx:0{width}d}{ext}")
            if src.name != dst.name:
                plan.append((src, dst))

        if not plan:
            return

        # Two-phase rename through temporary names: no clash even if names overlap
        temp_moves: list[tuple[Path, Path]] = []
        try:
            for n, (src, dst) in enumerate(plan):
                tmp = src.with_name(f".__renaming_{n}{dst.suffix}")
                src.rename(tmp)
                temp_moves.append((tmp, src))
            for (tmp, _), (_, dst) in zip(temp_moves, plan):
                tmp.rename(dst)
        except OSError as err:
            # Best-effort rollback of files still holding a temporary name
            for tmp, original in temp_moves:
                if tmp.exists() and not original.exists():
                    try:
                        tmp.rename(original)
                    except OSError:
                        pass
            self.live_manager.update_log(
                "Rename failed",
                f"Error while renaming images: {err}",
            )
            return

        self.live_manager.update_log(
            "Rename",
            f"Renamed {len(plan)} images to {1:0{width}d}…{total:0{width}d}.",
        )

    # Private methods
    def _extract_and_download(
        self,
        session: Session,
        picture_pages: list[str],
        current_task: int,
    ) -> list[str]:
        """Extract image links and download them concurrently."""
        failed_downloads = []
        num_pictures = len(picture_pages)
        task = self.live_manager.add_task(current_task=current_task, total=num_pictures)

        # Thread lock for safely appending to failed_downloads list
        failed_lock = threading.Lock()

        def download_worker(picture_page: str) -> None:
            """Download one picture page (runs inside the thread pool)."""
            nonlocal failed_downloads

            page_idx = 0
            page_match = re.search(r"/(\d+)-(\d+)(?:\?|$)", picture_page)
            if page_match:
                page_idx = int(page_match.group(2))

            def _mark_done() -> None:
                """Count this page as processed and update the progress bars."""
                with self._processed_images_lock:
                    self._processed_images_count += 1
                    done_count = self._processed_images_count
                if hasattr(self.live_manager, "update_album_progress"):
                    self.live_manager.update_album_progress(done_count, self.total_images, page_idx)
                self.live_manager.update_task(task, advance=1)

            # 0. Check if page image was already downloaded or placed manually locally
            if page_idx > 0 and self._find_and_prepare_local_page(page_idx):
                self.live_manager.update_log(
                    "Already present",
                    f"Page {self.format_page_index(page_idx)}/{self.total_images} already on disk.",
                )
                _mark_done()
                return

            # Image quota already exhausted: don't hammer the site, just mark as failed
            if self._quota_exceeded.is_set():
                with failed_lock:
                    failed_downloads.append(picture_page)
                _mark_done()
                return

            # 1+2. Fetch the picture page, take the image link and download it. Only if the
            #      image server fails, use the page's "reload" (nl) token to get another
            #      server and retry: one page request per image instead of two.
            response = None
            download_link = ""
            reloaded_page = picture_page
            for server_attempt in range(1, IMAGE_SERVER_RETRIES + 1):
                try:
                    soup = fetch_page(
                        reloaded_page,
                        session=self.session,
                        referer=self.url if server_attempt == 1 else picture_page,
                    )
                    download_link_container = soup.find("img", {"id": "img", "src": True})
                    if not download_link_container:
                        self.live_manager.update_log(
                            "Image not found",
                            f"Could not find the img tag on {reloaded_page}.",
                        )
                        break
                    download_link = download_link_container["src"]
                    if any(marker in download_link for marker in _QUOTA_EXCEEDED_MARKERS):
                        if not self._quota_exceeded.is_set():
                            self._quota_exceeded.set()
                            self.live_manager.update_log(
                                "Image limit",
                                "E-Hentai returned the 509 image (quota exhausted): stopping downloads for this album.",
                            )
                        break
                except Exception as err:
                    self.live_manager.update_log(
                        "Page error",
                        f"Error loading page {page_idx}: {err}",
                    )
                    break

                headers = prepare_headers(download_link)
                response = fetch_with_retries(
                    session=session,
                    url=download_link,
                    live_manager=self.live_manager,
                    retries=IMAGE_FETCH_RETRIES,
                    headers=headers,
                    page_idx=page_idx,
                )
                if response is not None:
                    break

                nl_value = extract_nl_value(soup)
                if not nl_value or server_attempt >= IMAGE_SERVER_RETRIES or self._quota_exceeded.is_set():
                    break
                self.live_manager.update_log(
                    "Switching server",
                    f"Image server unreachable for page {page_idx}: "
                    f"trying another server ({server_attempt + 1}/{IMAGE_SERVER_RETRIES})...",
                )
                # Same as the site's own nl() script: append another nl token to the current URL
                separator = "&" if "?" in reloaded_page else "?"
                reloaded_page = f"{reloaded_page}{separator}nl={nl_value}"

            if response is None:
                self.live_manager.update_log(
                    "Download error",
                    f"No response for page {page_idx} ({download_link or reloaded_page}).",
                )
                write_on_session_log(picture_page)
                with failed_lock:
                    failed_downloads.append(picture_page)
                _mark_done()
                return

            raw_filename = download_link.split("/")[-1]
            if page_idx > 0:
                original_filename = f"{self.format_page_index(page_idx)}_{raw_filename}"
            else:
                original_filename = raw_filename

            target_filename = self._get_target_filename(original_filename)
            final_path = Path(self.download_path) / target_filename

            if self._is_same_file(final_path, response, original_filename):
                self.live_manager.update_log(
                    "Already present",
                    f"Page {self.format_page_index(page_idx)}/{self.total_images} already on disk: {target_filename}",
                )
                _mark_done()
                return

            try:
                self.download_picture(response, original_filename)
            except Exception as save_err:
                self.live_manager.update_log(
                    "Save error",
                    f"Could not save page {page_idx}: {save_err}",
                )
                write_on_session_log(picture_page)
                with failed_lock:
                    failed_downloads.append(picture_page)
                _mark_done()
                return

            self.live_manager.update_log(
                "Download",
                f"Downloaded page {self.format_page_index(page_idx)}/{self.total_images}: {target_filename}",
            )
            _mark_done()

            # Polite sleep between tasks in each thread
            time.sleep(random.uniform(1.0, 3.0))  # noqa: S311

        future_to_page: dict = {}
        workers = max(1, min(MAX_WORKERS, throttle.current_workers()))
        self.live_manager.update_log(
            "Speed",
            f"Speed: {throttle.speed_name()} ({workers} parallel download(s)).",
        )
        with ThreadPoolExecutor(max_workers=workers) as executor:
            for picture_page in picture_pages:
                future = executor.submit(download_worker, picture_page)
                future_to_page[future] = picture_page
            for future in as_completed(future_to_page):
                try:
                    future.result()
                except Exception as err:
                    page_url = future_to_page[future]
                    self.live_manager.update_log(
                        "Worker error",
                        f"Unhandled exception in worker thread for {page_url}: {err}",
                    )
                    write_on_session_log(page_url)
                    with failed_lock:
                        failed_downloads.append(page_url)
                    self.live_manager.update_task(task, advance=1)

        return failed_downloads
