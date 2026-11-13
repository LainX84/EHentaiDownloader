"""CBZ (comic book archive) creation, shared by every album downloader.

AlbumDownloader (and NHentaiAlbumDownloader) inherit these methods. They use these
attributes of the downloader: album_name, clean_album_name, total_images, url,
download_path, extra_info, live_manager, site_download_folder, and its _scan_local_pages /
_part_path / _discard helpers.
"""

import os
import zipfile
from pathlib import Path

from src.config import CBZ_COMIC_INFO, DOWNLOAD_FOLDER
from src.file_utils import MAX_NAME_BYTES, sanitize_directory_name

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".avif"}

# Whether ComicInfo.xml is written into new CBZ files. The GUI sets it from
# Settings → "ComicInfo.xml in CBZ"; the CLI uses CBZ_COMIC_INFO from src/config.py.
_comic_info_enabled = CBZ_COMIC_INFO


def set_comic_info(enabled: bool) -> None:
    """Turn the ComicInfo.xml file inside new CBZ archives on or off."""
    global _comic_info_enabled  # pylint: disable=global-statement
    _comic_info_enabled = bool(enabled)


def comic_info_enabled() -> bool:
    """True if new CBZ archives get a ComicInfo.xml."""
    return _comic_info_enabled


class CbzArchiveMixin:  # pylint: disable=too-few-public-methods
    """Pack a finished album into "Downloads/<site>/<title>.cbz" (ComicInfo.xml only if enabled)."""

    def _cbz_path(self, folder: Path | None = None) -> Path:
        """Return "Downloads/<site>/<full gallery title>.cbz" (shortened only if the name is too long).

        folder: put the archive there instead (used to find archives of older versions).
        """
        folder = self.site_download_folder if folder is None else folder
        title = self.clean_album_name or self.album_name or "album"
        # 255 is the file-name limit (bytes on Linux / macOS); on Windows also stay below
        # the 260-char MAX_PATH
        room = 255 - len(".cbz")
        if os.name == "nt":
            room = min(room, 259 - len(str(folder.resolve())) - 1 - len(".cbz"))
        name = sanitize_directory_name(title, max_length=max(20, room),
                                       max_bytes=MAX_NAME_BYTES - len(".cbz")) or "album"
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

        Also recognizes archives made by earlier versions: "Downloads/<title>.cbz" and
        "Downloads/<album>/*.cbz".
        """
        candidates = [self._cbz_path(), self._cbz_path(Path(DOWNLOAD_FOLDER))]
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
                if _comic_info_enabled:
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
