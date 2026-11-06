"""Album downloader script.

This module provides functionality to download an album from a given URL while tracking
progress and logging events. It initializes necessary managers for live updates and
handles command-line execution.

Usage:
    python downloader.py <album_url>
"""

from __future__ import annotations

import logging
import random
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

from src.downloader.album_downloader import AlbumDownloader
from src.config import DEFAULT_IMAGE_FORMAT, FAILED_URLS_FILE
from src.file_utils import append_session_banner, read_file, write_file
from src.general_utils import clear_terminal, create_session, fetch_page, validate_gallery_url
from src.managers.live_manager import LiveManager
from src.managers.log_manager import LoggerTable
from src.managers.progress_manager import ProgressManager


def _normalize_album_url(url: str) -> str:
    """Normalize album URL by stripping whitespace and ensuring trailing slash."""
    url = url.strip()
    if not url:
        return ""
    try:
        parsed = urlparse(url)
        if parsed.scheme and parsed.netloc and "/g/" in parsed.path:
            path = parsed.path if parsed.path.endswith("/") else f"{parsed.path}/"
            return f"{parsed.scheme}://{parsed.netloc}{path}"
    except Exception:
        pass
    return url


def _album_key(url: str) -> str | None:
    """Extract a canonical album key: the gallery id (e.g. '2068624').

    The id alone identifies a gallery, so the same album is recognised even if one copy
    of the link has a mistyped or truncated token, or uses the other domain.
    """
    try:
        parsed = urlparse(url)
        parts = [p for p in parsed.path.split("/") if p]
        if len(parts) >= 2 and parts[0] == "g" and parts[1].isdigit():
            return parts[1]
    except Exception:
        pass
    return None


def _dedupe_keep_order(urls: list[str]) -> list[str]:
    """Return URLs deduplicated while preserving insertion order and album uniqueness.

    When the same gallery appears twice, a well-formed link wins over a malformed one
    (e.g. a token missing a character), whatever their order.
    """
    seen_urls: set[str] = set()
    key_position: dict[str, int] = {}
    output: list[str] = []

    for raw_url in urls:
        norm_url = _normalize_album_url(raw_url)
        if not norm_url or norm_url in seen_urls:
            continue
        seen_urls.add(norm_url)
        key = _album_key(norm_url)
        if key:
            if key in key_position:
                idx = key_position[key]
                if validate_gallery_url(output[idx]) and not validate_gallery_url(norm_url):
                    output[idx] = norm_url  # replace the broken copy with the good one
                continue
            key_position[key] = len(output)
        output.append(norm_url)
    return output


# ---------------------------------------------------------------------------
# List lines:  STATUS | IMAGES | TITLE | LINK
# ---------------------------------------------------------------------------
# URLs.txt, the batch editor and failed_downloads.txt show one gallery per line as
#
#     GOOD |   35 | [Artist] Gallery title | https://e-hentai.org/g/1908257/7261eadc3e/
#     BAD  |    - | (invalid link)         | https://exhentai.org/g/4077626/00cb5bea6/
#
#   STATUS  GOOD = the gallery page was read correctly, BAD = it could not be read
#   IMAGES  number of images in the gallery ('-' when unknown)
#   TITLE   gallery title, or a short reason in brackets when the link is BAD
#   LINK    the gallery URL (always the last column)
#
# The program fills these columns in itself (Verify Links / after a download). A plain
# URL per line is also accepted, as is the older "URL  # title" form.

_LEGACY_LINE_RE = re.compile(r"^(\S+)(?:\s+#\s*(.*))?$")
_STATUS_VALUES = ("GOOD", "BAD")

# What is known about each gallery, keyed by gallery id (see _album_key):
# {"title": str, "images": int, "status": "GOOD"/"BAD", "problem": str}
_gallery_info: dict[str, dict] = {}


def _name_key(url: str) -> str:
    """Key used to store gallery info: the gallery id, or the normalized URL."""
    return _album_key(url) or _normalize_album_url(url)


def remember_gallery_info(
    url: str,
    title: str | None = None,
    images: int | None = None,
    status: str | None = None,
    problem: str | None = None,
) -> None:
    """Store what is known about a gallery (only the given, non-empty values are updated)."""
    if not url:
        return
    info = _gallery_info.setdefault(_name_key(url), {})
    if title and title.strip():
        info["title"] = " ".join(title.split())  # one line, single spaces
    if images:
        info["images"] = int(images)
    if status in _STATUS_VALUES:
        info["status"] = status
    if problem:
        info["problem"] = problem


def remember_gallery_name(url: str, name: str) -> None:
    """Remember the title of a gallery so it can be shown in lists and errors."""
    remember_gallery_info(url, title=name)


def gallery_name(url: str) -> str:
    """Return the known title of a gallery, or '' if it was never seen."""
    return _gallery_info.get(_name_key(url), {}).get("title", "") if url else ""


def gallery_status(url: str) -> str:
    """Return "GOOD" / "BAD" if the link was already checked, or '' if never checked."""
    return _gallery_info.get(_name_key(url), {}).get("status", "") if url else ""


def gallery_key(url: str) -> str:
    """Key that identifies a gallery (its id), used to spot duplicate links."""
    return _name_key(url) if url else ""


def describe_gallery(url: str) -> str:
    """Return '"Title" (url)' when the title is known, otherwise just the URL."""
    name = gallery_name(url)
    return f'"{name}" ({url})' if name else url


def short_problem(error_text: str) -> str:
    """Turn a long error message into a few words for the TITLE column of a BAD line."""
    text = (error_text or "").lower()
    if "invalid gallery url" in text or "key missing" in text or "incomplete link" in text:
        return "invalid link"
    if "removed" in text or "unavailable" in text:
        return "gallery removed or unavailable"
    if "banned" in text:
        return "IP temporarily banned"
    if "cookie" in text or "exhentai access" in text or "sad panda" in text:
        return "ExHentai access denied (cookies)"
    if "rate limit" in text:
        return "rate limit"
    return "gallery page not readable"


def parse_url_line(line: str) -> tuple[str, str]:
    """Split a list line into (url, title). Blank and '#' comment lines give ('', '').

    Understands "STATUS | IMAGES | TITLE | LINK", "URL  # title" and a bare URL. What the
    line says about the gallery (status, image count, title) is remembered.
    """
    text = line.strip()
    if not text or text.startswith("#"):
        return "", ""

    parts = text.split("|")
    if len(parts) >= 4 and parts[0].strip().upper() in _STATUS_VALUES and "://" in parts[-1]:
        url = parts[-1].strip()
        status = parts[0].strip().upper()
        count = parts[1].strip()
        title = "|".join(parts[2:-1]).strip()  # a title may itself contain '|'
        if title.startswith("(") and title.endswith(")"):  # "(reason)" of a BAD line
            remember_gallery_info(url, status=status, problem=title[1:-1])
            title = ""
        remember_gallery_info(
            url, title=title, status=status, images=int(count) if count.isdigit() else None,
        )
        return url, title

    match = _LEGACY_LINE_RE.match(text)
    if not match:
        return text.split()[0], ""
    url, title = match.group(1), (match.group(2) or "").strip()
    remember_gallery_info(url, title=title)
    return url, title


def format_url_line(url: str, name: str = "") -> str:
    """Build a list line "STATUS | IMAGES | TITLE | LINK" from what is known about the gallery.

    A link the program knows nothing about yet stays a bare URL.
    """
    if name:
        remember_gallery_info(url, title=name)
    info = _gallery_info.get(_name_key(url), {})
    if not info:
        return url
    status = info.get("status") or ("GOOD" if info.get("title") else "BAD")
    images = str(info["images"]) if info.get("images") else "-"
    title = info.get("title") or f"({info.get('problem') or 'not checked'})"
    return f"{status:<4} | {images:>4} | {title} | {url}"


def sort_lines_by_size(lines: list[str]) -> list[str] | None:
    """Reorder the link lines so galleries with fewer images come first.

    Blank and '#' comment lines stay where they are; link lines are redistributed over
    the remaining positions. Order: GOOD galleries by image count (smallest first), then
    GOOD ones with unknown size, then BAD/unchecked links. Ties keep their original order.
    Returns None when the list is already in that order.
    """
    slots, links = [], []
    for idx, line in enumerate(lines):
        url, _ = parse_url_line(line)
        if not url:
            continue
        info = _gallery_info.get(_name_key(url), {})
        status = info.get("status") or ("GOOD" if info.get("title") else "")
        images = info.get("images") or 0
        if status == "GOOD" and images:
            key = (0, images)
        elif status == "GOOD":
            key = (1, 0)
        else:
            key = (2, 0)
        slots.append(idx)
        links.append((key, len(links), line))
    ordered = sorted(links)
    if [pos for _, pos, _ in ordered] == list(range(len(links))):
        return None
    result = list(lines)
    for slot, (_, _, line) in zip(slots, ordered):
        result[slot] = line
    return result


def find_duplicates(urls: list[str]) -> list[tuple[int, int]]:
    """Find links that point to a gallery already listed.

    Returns (duplicate_index, kept_index) pairs. For each gallery the kept link is the
    first well-formed one (a malformed copy, e.g. with a truncated token, is never kept
    when a valid one exists).
    """
    kept: dict[str, int] = {}
    for idx, url in enumerate(urls):
        key = _album_key(url) or _normalize_album_url(url)
        if not key:
            continue
        if key not in kept:
            kept[key] = idx
        elif validate_gallery_url(urls[kept[key]]) and not validate_gallery_url(url):
            kept[key] = idx
    pairs = []
    for idx, url in enumerate(urls):
        key = _album_key(url) or _normalize_album_url(url)
        if key and kept[key] != idx:
            pairs.append((idx, kept[key]))
    return pairs


def verify_gallery(url: str, session=None) -> dict:
    """Check a gallery link without downloading it.

    Returns a dict with:
        ok      – True if the gallery page could be read
        error   – why it failed ('' when ok)
        title   – gallery title ('' if unknown)
        images  – number of images (0 if unknown)
        domain  – site that will actually be used (e-hentai.org / exhentai.org)
        note    – extra info, e.g. that the other domain was used as a fallback
    """
    result = {"url": url, "ok": False, "error": "", "title": "", "images": 0, "domain": "", "note": ""}
    url_error = validate_gallery_url(url)
    if url_error:
        result["error"] = url_error
        remember_gallery_info(url, status="BAD", problem=short_problem(url_error))
        return result

    try:
        soup = fetch_page(_normalize_album_url(url), session=session or create_session())
    except Exception as err:
        result["error"] = str(err)
        remember_gallery_info(url, status="BAD", problem=short_problem(str(err)))
        return result

    fetched = getattr(soup, "fetched_url", url)
    requested_domain = urlparse(url).netloc.lower()
    result["domain"] = urlparse(fetched).netloc.lower()
    if result["domain"] != requested_domain:
        result["note"] = f"{requested_domain} did not answer correctly, {result['domain']} will be used"

    title_tag = soup.find("h1", {"id": "gn"}) or soup.find("h1", {"id": "gj"})
    result["title"] = title_tag.get_text(strip=True) if title_tag else ""
    match = re.search(r"(\d[\d,]*)\s*pages?", soup.get_text(" "), re.IGNORECASE)
    if match:
        result["images"] = int(match.group(1).replace(",", ""))
    result["ok"] = bool(result["title"])
    if not result["ok"]:
        result["error"] = "The page was loaded but no gallery title was found."
    remember_gallery_info(
        url,
        title=result["title"],
        images=result["images"],
        status="GOOD" if result["ok"] else "BAD",
        problem=None if result["ok"] else "no gallery title found",
    )
    return result


def courtesy_pause() -> None:
    """Short random pause between requests to different galleries (anti rate-limit)."""
    time.sleep(random.uniform(0.5, 1.5))  # noqa: S311


def load_failed_entries() -> list[tuple[str, str]]:
    """Load (url, name) pairs from failed_downloads.txt."""
    if not Path(FAILED_URLS_FILE).exists():
        return []
    entries = []
    for line in read_file(FAILED_URLS_FILE):
        url, name = parse_url_line(line)
        if url:
            remember_gallery_name(url, name)
            entries.append((url, name))
    return entries


def load_failed_urls() -> list[str]:
    """Load failed album URLs from disk if the file exists."""
    return _dedupe_keep_order([url for url, _ in load_failed_entries()])


def save_failed_urls(urls: list[str]) -> None:
    """Persist failed album URLs for future retries, each followed by its gallery name if known."""
    normalized = _dedupe_keep_order([url for url in urls if url.strip()])
    content = "\n".join(format_url_line(url) for url in normalized)
    if content:
        content += "\n"
    write_file(FAILED_URLS_FILE, content)


def update_failed_url(url: str, has_failures: bool) -> None:
    """Add or remove an album URL from the failed URL registry."""
    key = _album_key(url)
    current = load_failed_urls()

    if has_failures:
        if key:
            filtered = [item for item in current if _album_key(item) != key]
        else:
            filtered = [item for item in current if _normalize_album_url(item) != _normalize_album_url(url)]
        save_failed_urls(filtered + [url])
        return

    if key:
        save_failed_urls([item for item in current if _album_key(item) != key])
    else:
        save_failed_urls([item for item in current if _normalize_album_url(item) != _normalize_album_url(url)])


def download_album(
    url: str,
    live_manager: LiveManager,
    image_format: str = DEFAULT_IMAGE_FORMAT,
    rename_sequential: bool | None = None,
    name_hint: str = "",
    create_cbz: bool | None = None,
) -> tuple[bool, list[str]]:
    """Download an album using the provided live manager.

    name_hint is the gallery name already known for this link (e.g. from URLs.txt), used
    in error messages when the gallery page itself cannot be read.

    Returns:
        (success, failed_items) – success is True when nothing failed,
        failed_items is the list of picture-page / download URLs that failed.
        The gallery title, once known, is available with gallery_name(url).
    """
    failed_items: list[str] = []
    remember_gallery_name(url, name_hint)
    try:
        album_downloader = AlbumDownloader(
            url=url,
            live_manager=live_manager,
            image_format=image_format,
            rename_sequential=rename_sequential,
            create_cbz=create_cbz,
        )
        remember_gallery_info(
            url,
            title=album_downloader.clean_album_name,
            images=album_downloader.total_images,
            status="GOOD",
        )
        with live_manager.live:
            failed_items = album_downloader.download_album()
            live_manager.stop()
            has_failures = bool(failed_items)
            update_failed_url(url, has_failures)
            return not has_failures, failed_items

    except KeyboardInterrupt:
        sys.exit(1)
    except Exception as exc:
        logging.error("Failed to process album %s — %s", describe_gallery(url), exc)
        # The gallery page itself could not be used: mark the link BAD with a short reason
        if not gallery_name(url):
            remember_gallery_info(url, status="BAD", problem=short_problem(str(exc)))
        update_failed_url(url, has_failures=True)
        return False, failed_items


def initialize_managers() -> LiveManager:
    """Initialize and returns the managers for progress tracking and logging."""
    progress_manager = ProgressManager(task_name="Album", item_description="Page")
    logger_table = LoggerTable()
    return LiveManager(progress_manager, logger_table)


def main() -> None:
    """Run the album downloader."""
    if len(sys.argv) != 2:
        script_name = Path(__file__).name
        message = f"Usage: python3 {script_name} <album_url>"
        logging.error(message)
        sys.exit(1)

    clear_terminal()
    url = sys.argv[1]
    append_session_banner(f"SINGLE CLI START (downloader.py: {url})")
    live_manager = initialize_managers()
    ok, _ = download_album(url, live_manager)
    append_session_banner(f"SINGLE CLI END (downloader.py: {url}) - Result: {'OK' if ok else 'ERRORS'}")


if __name__ == "__main__":
    main()
