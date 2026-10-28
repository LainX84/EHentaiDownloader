"""Utility functions for processing picture pages and generating reloaded URLs.

This module provides helper functions for extracting picture page URLs and modifying
them to include reloaded parameters.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlencode, urlparse

from bs4 import BeautifulSoup, Tag

PICTURE_PAGE_PATTERN = re.compile(r"/s/[0-9a-fA-F]+/\d+-\d+", re.IGNORECASE)


def get_picture_pages(containers: list[Tag]) -> list[str]:
    """Extract picture page URLs from a list of container elements, preserving order without duplicates."""
    seen: set[str] = set()
    pages: list[str] = []
    for container in containers:
        href = container.get("href")
        if href and PICTURE_PAGE_PATTERN.search(href) and href not in seen:
            seen.add(href)
            pages.append(href)
    return pages


NL_PATTERN = re.compile(r"nl\('([^']+)'\)")


def extract_nl_value(soup: BeautifulSoup) -> str | None:
    """Return the 'nl' token of the "Reload broken image" link, used to ask for another image server."""
    container = soup.find("a", {"id": "loadfail", "onclick": True})
    if not container:
        return None
    match = NL_PATTERN.search(container.get("onclick", ""))
    return match.group(1) if match else None


def generate_reloaded_page(picture_page: str, nl_value: str) -> str:
    """Generate a reloaded version of a picture page URL with an 'nl' parameter."""
    parsed_url = urlparse(picture_page)
    query_params = parse_qs(parsed_url.query)
    query_params["nl"] = nl_value
    return parsed_url._replace(query=urlencode(query_params, doseq=True)).geturl()
