"""Folder / CBZ names made from gallery titles (src.file_utils.sanitize_directory_name)."""

import pytest

from src.file_utils import MAX_NAME_BYTES, sanitize_directory_name


@pytest.mark.parametrize("title, expected", [
    ("Plain title", "Plain title"),
    ('What? A "test": <1/2>|*', "What_ A _test_ _1_2"),
    ("  trailing dots...  ", "trailing dots"),
    ("tab\there", "tab_here"),
])
def test_invalid_characters_are_replaced_everywhere(title, expected):
    assert sanitize_directory_name(title) == expected


@pytest.mark.parametrize("title", ["CON", "con", "NUL", "com1", "LPT9", "aux.txt"])
def test_windows_device_names_get_an_underscore(title):
    assert sanitize_directory_name(title) == f"{title}_"


def test_names_that_only_start_like_device_names_are_kept():
    assert sanitize_directory_name("Console wars") == "Console wars"
    assert sanitize_directory_name("COM10") == "COM10"


def test_cjk_title_fits_the_255_byte_limit_of_linux_and_macos():
    title = "原神大放送" * 40          # 200 characters, 600 bytes in UTF-8
    name = sanitize_directory_name(title)
    assert len(name.encode("utf-8")) <= MAX_NAME_BYTES
    assert title.startswith(name)    # cut, never mangled


def test_cut_never_splits_a_multibyte_character():
    name = sanitize_directory_name("a" + "é" * 200, max_length=500, max_bytes=100)
    name.encode("utf-8")             # no half character
    assert len(name.encode("utf-8")) <= 100


def test_emoji_titles_stay_valid():
    title = "[NCDX]这塔什干一定特别紧实，🍵🈲去8️都8️不➗来！ [AI Generated]"
    assert sanitize_directory_name(title) == title


def test_gallery_suffix_is_kept_when_shortened():
    title = "x" * 300 + "_g_2068624_abcdef0123"
    name = sanitize_directory_name(title)
    assert name.endswith("_g_2068624_abcdef0123")
    assert len(name) <= 150


def test_short_names_are_unchanged():
    assert sanitize_directory_name("Fern 5 [AI Generated]") == "Fern 5 [AI Generated]"
