"""Album downloader script.

This module provides functionality to download an album from a given URL while tracking
progress and logging events. It initializes necessary managers for live updates and
handles command-line execution.

Usage:
    python downloader.py <album_url>
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from urllib.parse import urlparse

from src.downloader.album_downloader import AlbumDownloader
from src.config import DEFAULT_IMAGE_FORMAT, FAILED_URLS_FILE
from src.file_utils import append_session_banner, read_file, write_file
from src.general_utils import clear_terminal
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
    """Extract a canonical album key (e.g. '2068624_846fc2d3b5') to identify unique albums."""
    try:
        parsed = urlparse(url)
        parts = [p for p in parsed.path.split("/") if p]
        if len(parts) >= 3 and parts[0] == "g":
            return f"{parts[1]}_{parts[2]}"
    except Exception:
        pass
    return None


def _dedupe_keep_order(urls: list[str]) -> list[str]:
    """Return URLs deduplicated while preserving insertion order and album uniqueness."""
    seen_urls: set[str] = set()
    seen_keys: set[str] = set()
    output: list[str] = []

    for raw_url in urls:
        norm_url = _normalize_album_url(raw_url)
        if not norm_url or norm_url in seen_urls:
            continue
        key = _album_key(norm_url)
        if key:
            if key in seen_keys:
                continue
            seen_keys.add(key)
        seen_urls.add(norm_url)
        output.append(norm_url)
    return output


def load_failed_urls() -> list[str]:
    """Load failed album URLs from disk if the file exists."""
    file_path = Path(FAILED_URLS_FILE)
    if not file_path.exists():
        return []
    return _dedupe_keep_order([url for url in read_file(FAILED_URLS_FILE) if url.strip()])


def save_failed_urls(urls: list[str]) -> None:
    """Persist failed album URLs for future retries."""
    normalized = _dedupe_keep_order([url for url in urls if url.strip()])
    content = "\n".join(normalized)
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
) -> tuple[bool, list[str]]:
    """Download an album using the provided live manager.

    Returns:
        (success, failed_items) – success is True when nothing failed,
        failed_items is the list of picture-page / download URLs that failed.
    """
    failed_items: list[str] = []
    try:
        album_downloader = AlbumDownloader(
            url=url,
            live_manager=live_manager,
            image_format=image_format,
            rename_sequential=rename_sequential,
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
        logging.error("Failed to process album %s — %s", url, exc)
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
