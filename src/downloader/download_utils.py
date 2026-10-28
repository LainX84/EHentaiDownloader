"""HTTP request utilities with retry logic.

This module provides functions for preparing HTTP headers and fetching URLs with retry
mechanisms and exponential backoff.
"""

from __future__ import annotations

import random
import time
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from requests.exceptions import ConnectTimeout, HTTPError, RequestException, Timeout

from src.config import (
    CHUNK_SIZE,
    CONNECTION_TIMEOUT,
    RATE_LIMIT_SLEEPING_TIME,
    prepare_user_agent,
)

if TYPE_CHECKING:
    from requests import Response, Session

    from src.managers.live_manager import LiveManager


def prepare_headers(url: str) -> dict:
    """Prepare a random HTTP headers with a user-agent string for making requests."""
    host = urlparse(url).netloc
    user_agent = prepare_user_agent()
    return {
        "Host": host,
        "User-Agent": user_agent,
        "Accept": (
            "image/avif,image/webp,image/png,image/svg+xml,image/*;q=0.8,*/*;q=0.5"
        ),
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
        "Referer": "https://e-hentai.org/",
        "Sec-Fetch-Dest": "image",
        "Sec-Fetch-Mode": "no-cors",
        "Sec-Fetch-Site": "cross-site",
    }


def _fetch_with_retries_single(
    session: Session,
    url: str,
    live_manager: LiveManager,
    retries: int = 5,
    headers: dict | None = None,
    page_idx: int = 0,
) -> Response | None:
    """Perform single-domain retries with exponential backoff and chunk streaming."""
    for attempt in range(retries):
        try:
            response = session.get(url, timeout=CONNECTION_TIMEOUT, headers=headers, stream=True)
            response.raise_for_status()

            total_bytes = int(response.headers.get("content-length", 0))
            chunks = []
            downloaded = 0
            for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
                if chunk:
                    chunks.append(chunk)
                    downloaded += len(chunk)
                    if hasattr(live_manager, "update_page_stream"):
                        live_manager.update_page_stream(page_idx, downloaded, total_bytes)

            response._content = b"".join(chunks)
            if hasattr(live_manager, "finish_page_stream"):
                live_manager.finish_page_stream(page_idx)
            return response

        except HTTPError as http_err:
            status_code = (
                http_err.response.status_code
                if http_err.response is not None
                else 0
            )
            if status_code in (429, 509):
                live_manager.update_log(
                    "Rate limit",
                    f"Request limit hit ({status_code}). Waiting {RATE_LIMIT_SLEEPING_TIME}s...",
                )
                time.sleep(RATE_LIMIT_SLEEPING_TIME)
                continue
            else:
                live_manager.update_log(
                    "HTTP error",
                    f"HTTP error {status_code} for page {page_idx}: {http_err}",
                )
                break

        except ConnectTimeout as conn_timeout:
            live_manager.update_log(
                "Timeout",
                f"Connection timeout for page {page_idx}. Attempt {attempt + 1}/{retries}...",
            )

        except Timeout as timeout_err:
            live_manager.update_log(
                "Timeout",
                f"Request timeout for page {page_idx}. Attempt {attempt + 1}/{retries}...",
            )

        except RequestException as req_err:
            live_manager.update_log(
                "Network error",
                f"Network error for page {page_idx}: {req_err}",
            )
            break

        if attempt < retries - 1:
            live_manager.update_log(
                "Retry",
                f"Download failed for page {page_idx}. Retrying ({attempt + 1}/{retries})...",
            )
            delay = 2 ** (attempt + 1) + random.uniform(1, 2)  # noqa: S311
            time.sleep(delay)

    live_manager.update_log(
        "Download error",
        f"Maximum number of attempts reached for page {page_idx}.",
    )
    return None


def fetch_with_retries(
    session: Session,
    url: str,
    live_manager: LiveManager,
    retries: int = 5,
    headers: dict | None = None,
    page_idx: int = 0,
) -> Response | None:
    """Fetch a URL with retry logic, exponential backoff, and alternate domain fallback."""
    response = _fetch_with_retries_single(session, url, live_manager, retries, headers, page_idx)
    if response is not None:
        return response

    alt_url = None
    if "e-hentai.org" in url:
        alt_url = url.replace("e-hentai.org", "exhentai.org")
    elif "exhentai.org" in url:
        alt_url = url.replace("exhentai.org", "e-hentai.org")

    if alt_url and alt_url != url:
        live_manager.update_log(
            "Alternate domain",
            f"Trying the alternate domain for page {page_idx}...",
        )
        alt_headers = headers.copy() if headers else None
        if alt_headers and "Host" in alt_headers:
            alt_headers["Host"] = urlparse(alt_url).netloc
        return _fetch_with_retries_single(session, alt_url, live_manager, retries, alt_headers, page_idx)

    return None
