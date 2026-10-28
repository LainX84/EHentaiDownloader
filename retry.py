"""Retry utility for failed album downloads.

This script reloads failed album URLs from failed_downloads.txt,
retries downloads, and updates the queue with only unresolved failures.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from urllib.parse import urlparse

from downloader import download_album, initialize_managers, load_failed_urls, save_failed_urls
from src.file_utils import rotate_session_log
from src.general_utils import clear_terminal


def _dedupe_keep_order(items: list[str]) -> list[str]:
    """Return deduplicated values while preserving order."""
    seen: set[str] = set()
    output: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            output.append(item)
    return output


def _is_album_url(url: str) -> bool:
    """Return True when URL appears to be a gallery album URL."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return False

    if parsed.scheme not in {"http", "https"}:
        return False
    if not parsed.netloc:
        return False

    # E-Hentai and ExHentai album pages are under /g/<id>/<token>/
    return "/g/" in parsed.path


def collect_retry_urls() -> list[str]:
    """Collect retry URLs from failed queue and sanitize."""
    queue_urls = load_failed_urls()
    return [url for url in queue_urls if _is_album_url(url)]


async def process_retry_urls(urls: list[str]) -> list[str]:
    """Retry downloads and return URLs that still fail."""
    live_manager = initialize_managers()
    remaining_failed: list[str] = []

    for url in urls:
        success, _ = download_album(url, live_manager)
        if not success:
            remaining_failed.append(url)

    return remaining_failed


async def main() -> None:
    """Run retry queue cleanup and redownload flow."""
    clear_terminal()

    retry_urls = collect_retry_urls()
    if not retry_urls:
        save_failed_urls([])
        rotate_session_log(force=True)
        print("No retryable album URLs found. Queue cleaned.")
        return

    # Start a fresh session log for the retry; the previous one is kept as session.log.1
    rotate_session_log(force=True)

    remaining_failed = await process_retry_urls(retry_urls)
    save_failed_urls(remaining_failed)

    print(
        "Retry complete. "
        f"Processed: {len(retry_urls)} | Remaining failed: {len(remaining_failed)}"
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(1)