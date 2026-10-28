"""Main module of the project.

This module provides functionality to read URLs from a specified file, validate them,
and download the associated content. It manages the entire download process by
leveraging asynchronous operations, allowing for efficient handling of multiple URLs.

Usage:
    To run the module, execute the script directly. It will process URLs listed in
    'URLs.txt' and log the session activities in 'session.log'.
"""

import asyncio
import sys
from pathlib import Path

from downloader import (
    _dedupe_keep_order,
    download_album,
    initialize_managers,
    load_failed_urls,
    save_failed_urls,
)
from src.config import URLS_FILE
from src.file_utils import append_session_banner, read_file, write_file
from src.general_utils import clear_terminal


def merge_urls(urls: list[str], retry_urls: list[str]) -> list[str]:
    """Merge regular and retry URL lists with de-duplication and stable ordering.

    Uses the same album-aware normalization as the failed queue, so the same gallery
    written with/without trailing slash (or on another domain path) is processed once.
    """
    return _dedupe_keep_order(urls + retry_urls)


async def process_urls(urls: list[str]) -> list[str]:
    """Validate and downloads items for a list of URLs."""
    live_manager = initialize_managers()
    failed_urls: list[str] = []

    for url in urls:
        is_success, _ = download_album(url, live_manager)
        if not is_success:
            failed_urls.append(url)

    return failed_urls


async def main() -> None:
    """Run the script."""
    # Clear the terminal and record session start
    clear_terminal()
    append_session_banner("BATCH CLI START (main.py)")

    # Read and process URLs, ignoring empty lines
    # URLs.txt is optional: create it empty on first run
    if not Path(URLS_FILE).exists():
        write_file(URLS_FILE)
    # One URL per line; blank lines and lines starting with '#' are ignored
    urls = [url.strip() for url in read_file(URLS_FILE) if url.strip() and not url.strip().startswith("#")]
    retry_urls = load_failed_urls()
    urls_to_process = merge_urls(urls, retry_urls)

    failed_urls = await process_urls(urls_to_process)
    save_failed_urls(failed_urls)

    # Clear URLs file
    write_file(URLS_FILE)
    append_session_banner(f"BATCH CLI END (main.py) - Failed: {len(failed_urls)}")


if __name__ == "__main__":
    try:
        asyncio.run(main())

    except KeyboardInterrupt:
        sys.exit(1)
