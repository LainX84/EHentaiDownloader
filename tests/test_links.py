"""Gallery links: validation, extraction from any text, duplicates."""

import pytest

from src.cli.downloader import find_duplicates, gallery_key, parse_url_line
from src.general_utils import extract_gallery_links, validate_gallery_url


@pytest.mark.parametrize("url", [
    "https://e-hentai.org/g/2068624/abcdef0123/",
    "https://exhentai.org/g/2068624/abcdef0123",
    "https://nhentai.net/g/694850/",
    "https://nhentai.net/g/694850/3/",
])
def test_valid_links(url):
    assert validate_gallery_url(url) is None


@pytest.mark.parametrize("url", [
    "https://e-hentai.org/g/2068624/abcdef012/",   # token one character short
    "https://e-hentai.org/s/abcdef0123/2068624-1",  # picture page, not a gallery
    "https://example.com/g/1/abcdef0123/",
    "https://nhentai.net/search/?q=x",
])
def test_invalid_links(url):
    assert validate_gallery_url(url)


def test_extract_links_from_mixed_text():
    text = (
        "Check these: https://e-hentai.org/g/1/abcdef0123/?p=2, and\n"
        "<a href=\"https://exhentai.org/g/2/0123456789\">two</a> "
        "plus https://nhentai.net/g/555/7/ (reader page) and https://nhentai.net/g/555/ again."
    )
    assert extract_gallery_links(text) == [
        "https://e-hentai.org/g/1/abcdef0123/",
        "https://exhentai.org/g/2/0123456789/",
        "https://nhentai.net/g/555/",
    ]


def test_extract_links_keeps_malformed_tokens_for_the_link_check():
    assert extract_gallery_links("https://e-hentai.org/g/1/abc/") == ["https://e-hentai.org/g/1/abc/"]


def test_extract_links_without_links():
    assert not extract_gallery_links("no links here https://example.com/g/1/x/")
    assert not extract_gallery_links("")


def test_same_gallery_on_both_domains_has_the_same_key():
    assert gallery_key("https://e-hentai.org/g/7/abcdef0123/") == gallery_key("https://exhentai.org/g/7/zzz/")
    assert gallery_key("https://nhentai.net/g/7/") != gallery_key("https://e-hentai.org/g/7/abcdef0123/")


def test_duplicates_keep_the_valid_link():
    urls = [
        "https://e-hentai.org/g/7/abcdef012/",     # malformed copy
        "https://e-hentai.org/g/8/0123456789/",
        "https://exhentai.org/g/7/abcdef0123/",    # valid copy of the first one
    ]
    assert find_duplicates(urls) == [(0, 2)]


@pytest.mark.parametrize("line, expected", [
    ("", ("", "")),
    ("# comment", ("", "")),
    ("https://nhentai.net/g/1/", ("https://nhentai.net/g/1/", "")),
    ("https://nhentai.net/g/1/  # My title", ("https://nhentai.net/g/1/", "My title")),
    ("GOOD | 24 | A | B title | https://nhentai.net/g/2/", ("https://nhentai.net/g/2/", "A | B title")),
])
def test_parse_url_line(line, expected):
    assert parse_url_line(line) == expected
