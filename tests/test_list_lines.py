"""List lines "✅ SITE | TAG | IMAGES | TITLE | LINK": writing them and reading them back."""

import pytest

from src import tags
from src.cli import downloader as d

NH = "https://nhentai.net/g/694850/"
EH = "https://e-hentai.org/g/2068624/abcdef0123/"
EX = "https://exhentai.org/g/4077626/00cb5bea6/"


@pytest.fixture(autouse=True)
def _clean():
    d._gallery_info.clear()
    tags.set_favorites(tags.parse_favorites("netorare\nai generated = AI\ngender change"))
    yield
    d._gallery_info.clear()
    tags.set_favorites([])


def test_columns_site_tag_images_title_link():
    # the tag column is as wide as the longest favorite label ("GENDER CHANGE": 13) + 2
    d.remember_gallery_info(NH, title="[Spiral] Title | with bar", images=123, status="GOOD", tags=["tag:netorare"])
    line = d.format_url_line(NH)
    assert line == f"✅ NHENTAI  |   NETORARE    | 123 | [Spiral] Title | with bar | {NH}"
    d._gallery_info.clear()
    assert d.parse_url_line(line) == (NH, "[Spiral] Title | with bar")
    columns = d.parse_table_line(line)
    assert columns["badge"] == "NETORARE" and columns["images"] == 123 and columns["url"] == NH


def test_favorite_with_the_highest_priority_wins():
    d.remember_gallery_info(NH, title="T", images=3, status="GOOD",
                            tags=["tag:gender change", "tag:ai generated", "tag:netorare"])
    assert d.parse_table_line(d.format_url_line(NH))["badge"] == "NETORARE"
    tags.set_favorites(tags.parse_favorites("gender change\nnetorare\nai generated = AI"))
    assert d.parse_table_line(d.format_url_line(NH))["badge"] == "GENDER CHANGE"


def test_line_without_favorite_keeps_an_empty_tag_column():
    d.remember_gallery_info(EH, title="Some title", images=10, status="GOOD", tags=["female:glasses"])
    line = d.format_url_line(EH)
    assert line == f"✅ EHENTAI  |               |  10 | Some title | {EH}"
    columns = d.parse_table_line(line)
    assert columns["badge"] == "" and columns["title"] == "Some title" and columns["images"] == 10


def test_bad_line_shows_the_link_instead_of_the_title():
    d.remember_gallery_info(EX, status="BAD", problem="invalid link")
    line = d.format_url_line(EX)
    assert line == f"❌ EXHENTAI |               |  -  | {EX} — invalid link | {EX}"
    columns = d.parse_table_line(line)
    assert columns["status"] == "BAD" and columns["title"] == "" and columns["problem"] == "invalid link"
    assert columns["url"] == EX


def test_older_lines_are_still_read_and_rewritten():
    assert d.parse_url_line(f"GOOD |   35 | [Artist] Title | {EH}") == (EH, "[Artist] Title")
    columns = d.parse_table_line(f"BAD  |    - | (invalid link) | {EX}")
    assert columns["status"] == "BAD" and columns["problem"] == "invalid link" and columns["title"] == ""
    old = f"✅ NHENTAI |  NETORARE  | Old | title | 24 | {NH}"
    columns = d.parse_table_line(old)
    assert (columns["badge"], columns["title"], columns["images"]) == ("NETORARE", "Old | title", 24)
    assert d.normalize_line(old) == f"✅ NHENTAI  |   NETORARE    |  24 | Old | title | {NH}"
    d._gallery_info.clear()
    assert d.parse_url_line(f"{NH}  # Old title") == (NH, "Old title")
    assert d.format_url_line(NH).endswith(f"|  -  | Old title | {NH}")


def test_unknown_link_stays_a_bare_url():
    assert d.format_url_line(EH) == EH
    assert d.normalize_line(EH) == EH


def test_badge_comes_back_after_a_restart_from_the_remembered_tags():
    d.remember_gallery_info(NH, title="T", images=3, status="GOOD", tags=["tag:ai generated"])
    line = d.format_url_line(NH)
    d._gallery_info.clear()  # new run: only the line and tags.json are left
    tags.set_favorites(tags.parse_favorites("ai generated = AI\nnetorare"))
    d.parse_url_line(line)
    assert d.gallery_badge(NH) == "AI"


def test_estimate_reads_the_new_lines():
    d.remember_gallery_info(NH, title="A", images=24, status="GOOD")
    d.remember_gallery_info(EH, title="B", images=10, status="GOOD")
    d.remember_gallery_info(EX, status="BAD", problem="invalid link")
    lines = [d.format_url_line(url) for url in (NH, EH, EX)] + ["https://e-hentai.org/g/1/abcdef0123/"]
    assert d.estimate_list_images(lines) == {"images": 34, "known": 2, "unverified": 1, "bad": 1}


def test_sorted_by_site_then_size():
    urls = {
        "nh_big": "https://nhentai.net/g/1/", "eh_big": "https://e-hentai.org/g/2/abcdef0123/",
        "nh_small": "https://nhentai.net/g/3/", "ex_small": "https://exhentai.org/g/4/abcdef0123/",
        "eh_bad": "https://e-hentai.org/g/5/abcdef0123/",
    }
    sizes = {"nh_big": 200, "eh_big": 150, "nh_small": 10, "ex_small": 20}
    for name, url in urls.items():
        if name in sizes:
            d.remember_gallery_info(url, title=name, images=sizes[name], status="GOOD")
        else:
            d.remember_gallery_info(url, status="BAD", problem="invalid link")
    lines = ["# my list"] + [d.format_url_line(url) for url in urls.values()]
    ordered = d.sort_lines_by_size(lines)
    assert ordered[0] == "# my list"
    assert [d.parse_url_line(line)[0] for line in ordered[1:]] == [
        urls["ex_small"], urls["eh_big"], urls["eh_bad"], urls["nh_small"], urls["nh_big"],
    ]
    assert d.sort_lines_by_size(ordered) is None
