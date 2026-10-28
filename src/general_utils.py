"""General utilities module.

This module provides utilities for fetching web pages, managing directories, and
clearing the terminal screen. It includes functions to handle common tasks such as
sending HTTP requests, parsing HTML, creating download directories, and clearing the
terminal, making it reusable across projects.
"""

import logging
import os
import random
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from src.config import (
    CONNECTION_TIMEOUT,
    PAGE_FETCH_RETRIES,
    RATE_LIMIT_SLEEPING_TIME,
    prepare_user_agent,
)

# Cache cookies to avoid repeated disk reads
_cached_cookies = None


def _compact_text(value: str, limit: int = 240) -> str:
    """Normalize whitespace and shorten diagnostic text, removing unprintable characters."""
    cleaned = "".join(ch if ch.isprintable() or ch in " \t\n" else " " for ch in value)
    compacted = " ".join(cleaned.split())
    return compacted[:limit] if len(compacted) <= limit else f"{compacted[:limit]}..."


def _extract_page_details(html: str, soup: BeautifulSoup | None = None) -> tuple[str, str, bool]:
    """Extract page title, diagnostic body text, and whether the page has valid gallery/reader content."""
    if not html or not html.strip():
        return "", "", False
    if soup is None:
        soup = BeautifulSoup(html, "html.parser")
    title_tag = soup.find("title")
    page_title = title_tag.get_text(strip=True) if title_tag else ""

    # Check for structural markers indicating a valid gallery, reader, or index page
    has_valid_content = bool(
        soup.find(id=["gdt", "gd1", "gd2", "gdd", "gn", "gj", "i1", "img", "loadfail"])
        or soup.find(class_=["itg", "gl1t", "itc"])
    )

    body_text = soup.body.get_text(" ", strip=True) if soup.body else ""
    return page_title, body_text, has_valid_content


def _classify_page_problem(
    url: str,
    status_code: int,
    html: str,
    has_cookies: bool,
    response_headers: dict | None = None,
    soup: BeautifulSoup | None = None,
) -> str | None:
    """Return a precise human-readable diagnosis when a page indicates access issues."""
    lowered_url = url.lower()

    # Check for empty response (specifically on ExHentai or general)
    if not html.strip():
        if "exhentai" in lowered_url:
            return (
                "Reason: ExHentai access unauthorized or rejected. "
                "The server replied with 0 bytes (typical when the 'igneous', "
                "'ipb_member_id' or 'ipb_pass_hash' cookies are expired, wrong or missing). "
                f"HTTP {status_code}."
            )
        return f"Reason: empty response from the server for {url}. HTTP {status_code}."

    # Check for cookie rejection in response headers (e.g. Set-Cookie: igneous=mystery)
    if response_headers:
        cookie_header = response_headers.get("Set-Cookie", "") or response_headers.get("set-cookie", "")
        if "mystery" in cookie_header.lower():
            return (
                "Reason: ExHentai cookies rejected by the server (received 'igneous=mystery'). "
                "Your cookies in cookies.json / cookies.txt are expired or invalid."
            )

    page_title, body_text, has_valid_content = _extract_page_details(html, soup=soup)

    # If HTTP 200 and page has valid gallery, reader, or index structures, it is a valid page
    if status_code == 200 and has_valid_content:
        return None

    lowered_title = page_title.lower()
    lowered_body = body_text.lower()
    lowered_html = html.lower()
    snippet_source = body_text or html
    snippet = _compact_text(snippet_source) if snippet_source else "Empty response"

    if "temporarily banned" in lowered_body or "ip address has been" in lowered_body:
        return (
            "Reason: IP restricted or temporarily banned by E-Hentai. "
            f"HTTP {status_code}. Page title: {page_title or 'N/A'}. Snippet: {snippet}"
        )

    if status_code in (429, 509) or "bandwidth exceeded" in lowered_body or "rate limit" in lowered_body:
        return (
            "Reason: site rate limit reached. "
            f"HTTP {status_code}. Page title: {page_title or 'N/A'}. Snippet: {snippet}"
        )

    if "sad panda" in lowered_title or (
        "exhentai" in lowered_url and ("sad panda" in lowered_body or "sad panda" in lowered_html)
    ):
        return (
            "Reason: ExHentai access denied; cookies missing, expired or invalid. "
            f"HTTP {status_code}. Page title: {page_title or 'N/A'}. Snippet: {snippet}"
        )

    if "login" in lowered_title or "please log in" in lowered_body or "you must be logged in" in lowered_body:
        return (
            "Reason: authentication required; cookies missing or expired. "
            f"HTTP {status_code}. Page title: {page_title or 'N/A'}. Snippet: {snippet}"
        )

    if "captcha" in lowered_body or "automated queries" in lowered_body or "check your browser" in lowered_body:
        return (
            "Reason: anti-bot protection or site challenge. "
            f"HTTP {status_code}. Page title: {page_title or 'N/A'}. Snippet: {snippet}"
        )

    if status_code == 404:
        if "gallery has been removed or is unavailable" in lowered_body or "gallery not available" in lowered_title:
            return (
                "Reason: gallery unavailable or removed from E-Hentai (it may be ExHentai-only and need valid cookies). "
                f"HTTP {status_code}. Page title: {page_title or 'N/A'}. Snippet: {snippet}"
            )
        return (
            "Reason: invalid or expired URL, or content removed. "
            f"HTTP {status_code}. Page title: {page_title or 'N/A'}. Snippet: {snippet}"
        )

    if status_code == 403:
        reason = "cookies expired/invalid or access/IP restricted" if has_cookies else "cookies missing or access denied by the site"
        return (
            f"Reason: {reason}. "
            f"HTTP {status_code}. Page title: {page_title or 'N/A'}. Snippet: {snippet}"
        )

    if status_code >= 400:
        return (
            "Reason: HTTP error returned by the site. "
            f"HTTP {status_code}. Page title: {page_title or 'N/A'}. Snippet: {snippet}"
        )

    if "e-hentai galleries" == lowered_title and "forums" not in lowered_body and "gallery" not in lowered_body and "thumbnail" not in lowered_body:
        return (
            "Reason: unexpected or incomplete page returned by the site. "
            f"HTTP {status_code}. Page title: {page_title or 'N/A'}. Snippet: {snippet}"
        )

    return None


def _build_request_error_message(
    url: str,
    response: requests.Response | None,
    error: requests.RequestException,
    has_cookies: bool,
) -> str:
    """Build a detailed request failure message with the most likely cause."""
    if response is not None:
        diagnosis = _classify_page_problem(
            url=url,
            status_code=response.status_code,
            html=response.text,
            has_cookies=has_cookies,
            response_headers=getattr(response, "headers", None),
        )
        if diagnosis:
            return diagnosis
        return f"HTTP {response.status_code}: {error}"

    return f"network/request error: {error}"


def load_cookies() -> dict[str, str]:
    """Load cookies from cookies.json or cookies.txt if they exist."""
    global _cached_cookies
    if _cached_cookies is not None:
        return _cached_cookies

    cookies = {}

    # Try cookies.json first
    json_path = Path("cookies.json")
    if json_path.exists():
        try:
            import json
            with json_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    _cached_cookies = {str(k): str(v) for k, v in data.items()}
                    return _cached_cookies
        except Exception as err:
            logging.warning(f"Error reading cookies.json: {err}")

    # Try cookies.txt
    txt_path = Path("cookies.txt")
    if txt_path.exists():
        try:
            with txt_path.open("r", encoding="utf-8") as f:
                content = f.read().strip()
                # Check if it looks like a netscape cookie file
                if "exhentai.org" in content or "e-hentai.org" in content or "# Netscape" in content:
                    for line in content.splitlines():
                        if not line.strip() or line.startswith("#"):
                            continue
                        parts = line.split("\t")
                        if len(parts) >= 7:
                            domain, flag, path, secure, expiration, name, value = parts[:7]
                            if "exhentai.org" in domain or "e-hentai.org" in domain:
                                cookies[name] = value
                else:
                    # Treat as raw Cookie header or simple key-value lines
                    if ";" in content or "=" in content:
                        pairs = content.replace("Cookie:", "").strip().split(";")
                        for pair in pairs:
                            if "=" in pair:
                                k, v = pair.strip().split("=", 1)
                                cookies[k.strip()] = v.strip()
        except Exception as err:
            logging.warning(f"Error reading cookies.txt: {err}")

    _cached_cookies = cookies
    return cookies


def create_session() -> requests.Session:
    """Create a requests Session configured with user agent and cookies."""
    session = requests.Session()
    session.headers.update({
        "User-Agent": prepare_user_agent(),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
    })

    cookies = load_cookies()
    if cookies:
        session.cookies.update(cookies)

    return session


def get_alternate_domain_url(url: str) -> str | None:
    """Return equivalent URL on alternate domain (e-hentai.org <-> exhentai.org)."""
    if "e-hentai.org" in url:
        return url.replace("e-hentai.org", "exhentai.org")
    if "exhentai.org" in url:
        return url.replace("exhentai.org", "e-hentai.org")
    return None


_TRANSIENT_STATUS = {429, 500, 502, 503, 504, 509}


def fetch_page(
    url: str,
    timeout: int = CONNECTION_TIMEOUT,
    session: requests.Session | None = None,
    referer: str | None = None,
    retries: int = PAGE_FETCH_RETRIES,
) -> BeautifulSoup:
    """Fetch the HTML content of a webpage with cookies and browser headers.

    If primary domain fails or returns an error page (e.g. 404, rate limit, restricted access),
    attempts fallback to equivalent URL on alternate domain (e-hentai.org <-> exhentai.org).
    Transient problems (network errors, timeouts, 429/5xx) are retried with backoff;
    permanent ones (404, ban, missing cookies) fail immediately.
    """
    request_session = session or create_session()
    has_cookies = bool(request_session.cookies)

    urls_to_try = [url]
    alt_url = get_alternate_domain_url(url)
    if alt_url and alt_url != url:
        urls_to_try.append(alt_url)

    last_exception: Exception | None = None
    last_diagnosis: str | None = None

    for attempt in range(max(1, retries)):
        transient = False
        rate_limited = False

        for attempt_idx, current_url in enumerate(urls_to_try):
            current_referer = referer
            if current_referer and attempt_idx > 0 and alt_url:
                alt_ref = get_alternate_domain_url(current_referer)
                if alt_ref:
                    current_referer = alt_ref

            headers = {"Referer": current_referer} if current_referer else None

            try:
                response = request_session.get(current_url, timeout=timeout, headers=headers)

                soup = BeautifulSoup(response.text, "html.parser") if response.text.strip() else None

                diagnosis = _classify_page_problem(
                    url=current_url,
                    status_code=response.status_code,
                    html=response.text,
                    has_cookies=has_cookies,
                    response_headers=response.headers,
                    soup=soup,
                )

                if response.status_code == 200 and not diagnosis and soup:
                    if attempt_idx > 0:
                        logging.info(f"Fallback to alternate domain succeeded: {current_url}")
                    soup.fetched_url = current_url
                    return soup

                if response.status_code in _TRANSIENT_STATUS:
                    # Only the primary domain decides: the alternate one often just lacks cookies
                    if attempt_idx == 0:
                        transient = True
                        rate_limited = response.status_code in (429, 509)

                if not diagnosis and not response.text.strip():
                    if "exhentai" in current_url.lower():
                        diagnosis = (
                            f"Reason: ExHentai access rejected for {current_url} "
                            "(empty server response; 'igneous', 'ipb_member_id' or 'ipb_pass_hash' cookies missing or invalid)."
                        )
                    else:
                        diagnosis = f"Reason: empty response from the server for {current_url}."

                last_diagnosis = diagnosis or f"HTTP {response.status_code}"
                if attempt_idx < len(urls_to_try) - 1:
                    logging.warning(
                        f"Fetch failed for {current_url} ({last_diagnosis}). Trying alternate domain: {urls_to_try[attempt_idx + 1]}"
                    )

            except requests.RequestException as req_err:
                response = req_err.response if hasattr(req_err, "response") else None
                last_diagnosis = _build_request_error_message(
                    url=current_url,
                    response=response,
                    error=req_err,
                    has_cookies=has_cookies,
                )
                last_exception = req_err
                if attempt_idx == 0:
                    transient = True
                if attempt_idx < len(urls_to_try) - 1:
                    logging.warning(
                        f"Request error for {current_url} ({req_err}). Trying alternate domain: {urls_to_try[attempt_idx + 1]}"
                    )

        if not transient or attempt >= retries - 1:
            break

        delay = RATE_LIMIT_SLEEPING_TIME if rate_limited else 2 ** (attempt + 1) + random.uniform(0.5, 1.5)  # noqa: S311
        logging.warning(
            f"Temporary error on {url} ({last_diagnosis}). Retry {attempt + 2}/{retries} in {delay:.0f}s..."
        )
        time.sleep(delay)

    message = f"Error fetching page {url}: {last_diagnosis}"
    logging.error(message)
    if last_exception:
        raise RuntimeError(message) from last_exception
    raise RuntimeError(message)


def clear_terminal() -> None:
    """Clear the terminal screen based on the operating system."""
    commands = {
        "nt": "cls",       # Windows
        "posix": "clear",  # macOS and Linux
    }

    command = commands.get(os.name)
    if command:
        os.system(command)  # noqa: S605
