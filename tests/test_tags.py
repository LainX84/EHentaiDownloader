"""Gallery tags: reading them, favorite tags, badges and tag folders."""

from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from src import config, tags
from src.config import EHENTAI_SUBFOLDER, NHENTAI_SUBFOLDER
from src.downloader.album_downloader import AlbumDownloader
from src.downloader.nhentai_downloader import NHentaiAlbumDownloader

EHENTAI_TAGLIST = """
<div id="taglist"><table>
<tr><td class="tc">language:</td><td><div id="td_language:english" class="gt"><a id="ta_language:english">english</a></div></td></tr>
<tr><td class="tc">female:</td><td>
  <div id="td_female:big_breasts" class="gt"><a id="ta_female:big_breasts">big breasts</a></div>
  <div id="td_female:netorare" class="gtl"><a id="ta_female:netorare">netorare</a></div></td></tr>
<tr><td class="tc">other:</td><td><div id="td_other:ai_generated" class="gt"><a id="ta_other:ai_generated">ai generated</a></div></td></tr>
</table></div>
"""


@pytest.fixture(autouse=True)
def _reset_favorites():
    yield
    tags.set_favorites([])
    tags.set_folders_enabled(False)
    tags.set_nested_folders(False)


def test_ehentai_tags_are_read_from_the_taglist():
    soup = BeautifulSoup(EHENTAI_TAGLIST, "html.parser")
    assert tags.from_ehentai_soup(soup) == [
        "language:english", "female:big breasts", "female:netorare", "other:ai generated",
    ]


def test_nhentai_tags():
    info = {"tags": ["netorare"], "all_tags": ["tag:netorare", "language:english", "artist:someone"]}
    assert tags.from_nhentai_info(info) == ["tag:netorare", "language:english", "artist:someone"]
    assert tags.from_nhentai_info({"tags": ["big breasts"]}) == ["tag:big breasts"]


def test_parse_favorites_with_labels_and_comments():
    favorites = tags.parse_favorites("netorare\n# a comment\nAI_Generated = AI\nfemale:big breasts = Big Boobs\nnetorare\n")
    assert [(f["tag"], f["label"]) for f in favorites] == [
        ("netorare", "netorare"), ("ai generated", "AI"), ("female:big breasts", "Big Boobs"),
    ]
    assert [f["color"] for f in favorites] == list(tags.DEFAULT_COLORS[:3])
    assert tags.parse_favorites(tags.favorites_text(favorites)) == favorites


def test_favorite_colours_are_kept_and_checked():
    favorites = tags.clean_favorites([
        {"tag": "netorare", "label": "NTR", "color": "#12ab34"},
        {"tag": "ai generated", "color": "red"},  # not a #RRGGBB colour: a default one
    ])
    assert favorites[0]["color"] == "#12AB34"
    assert favorites[1]["color"] == tags.DEFAULT_COLORS[1] and favorites[1]["label"] == "ai generated"
    tags.set_favorites(favorites)
    assert tags.badge_color("NTR") == "#12AB34"
    assert tags.badge_width() == len("AI GENERATED")
    assert tags.text_color("#FFEE58") == "#1A1A1A" and tags.text_color("#283593") == "#FFFFFF"


def test_first_favorite_wins_and_namespace_is_optional():
    tags.set_favorites(tags.parse_favorites("gender morph\nnetorare\nai generated = AI"))
    gallery = ["female:netorare", "other:ai generated", "language:english"]
    assert tags.badge_for(gallery) == "NETORARE"
    assert tags.badge_for(["tag:ai generated"]) == "AI"
    assert tags.badge_for(["female:sole female"]) == ""


def test_namespaced_favorite_matches_only_that_namespace():
    tags.set_favorites(tags.parse_favorites("female:big breasts = Big Boobs"))
    assert tags.badge_for(["female:big breasts"]) == "BIG BOOBS"
    assert tags.badge_for(["male:big breasts"]) == ""


def test_badge_is_at_most_twenty_characters():
    assert tags.badge_text("gender change") == "GENDER CHANGE"
    assert len(tags.badge_text("x" * 40)) == tags.BADGE_MAX


def test_tag_folder_only_when_enabled():
    tags.set_favorites(tags.parse_favorites("netorare = Netorare"))
    assert tags.folder_for(["female:netorare"]) == ""
    tags.set_folders_enabled(True)
    assert tags.folder_for(["female:netorare"]) == "Netorare"
    assert tags.folder_for(["female:glasses"]) == ""


def _bare(cls, title, gallery_tags):
    obj = cls.__new__(cls)
    obj.clean_album_name = obj.album_name = title
    obj.gallery_tags = gallery_tags
    return obj


def test_albums_go_into_the_favorite_tag_folder():
    tags.set_favorites(tags.parse_favorites("netorare = Netorare\nai generated = AI"))
    tags.set_folders_enabled(True)
    downloads = Path(config.DOWNLOAD_FOLDER)
    assert _bare(AlbumDownloader, "A", ["female:netorare"]).site_download_folder == \
        downloads / EHENTAI_SUBFOLDER / "Netorare"
    assert _bare(NHentaiAlbumDownloader, "A", ["tag:ai generated"]).site_download_folder == \
        downloads / NHENTAI_SUBFOLDER / "AI"
    assert _bare(AlbumDownloader, "A", ["female:glasses"]).site_download_folder == downloads / EHENTAI_SUBFOLDER
    cbz = _bare(AlbumDownloader, "My title", ["female:netorare"])._cbz_path()
    assert cbz == downloads / EHENTAI_SUBFOLDER / "Netorare" / "My title.cbz"


def test_download_folder_can_change_at_runtime(monkeypatch):
    monkeypatch.setattr(config, "DOWNLOAD_FOLDER", "/mnt/nas/galleries")
    assert _bare(AlbumDownloader, "A", []).site_download_folder == Path("/mnt/nas/galleries") / EHENTAI_SUBFOLDER


def test_lowest_position_wins_whatever_the_order_of_the_gallery_tags():
    tags.set_favorites(tags.parse_favorites("gender bender = Gender Bender\nnetorare = Netorare\nmind control"))
    tags.set_folders_enabled(True)
    gallery = ["female:mind control", "female:netorare", "male:gender bender"]
    assert tags.badge_for(gallery) == "GENDER BENDER"
    assert tags.folder_for(gallery) == "Gender Bender"
    assert [f["label"] for f in tags.favorites_for(gallery)] == ["Gender Bender", "Netorare", "mind control"]


def test_merged_tags_count_as_one_favorite():
    favorites = tags.parse_favorites("gender bender, gender morph, Gender_Change = Gender\n"
                                     "netorare, netorase, netori = NTR\nnetori\n")
    assert [(f["tag"], f["merged"], f["label"]) for f in favorites] == [
        ("gender bender", ["gender morph", "gender change"], "Gender"),
        ("netorare", ["netorase", "netori"], "NTR"),
    ]  # "netori" alone is already merged into NTR: dropped
    assert tags.parse_favorites(tags.favorites_text(favorites)) == favorites
    tags.set_favorites(favorites)
    tags.set_folders_enabled(True)
    assert tags.badge_for(["female:gender morph"]) == "GENDER"
    assert tags.badge_for(["tag:netori", "female:gender change"]) == "GENDER"
    assert tags.folder_for(["female:netorase"]) == "NTR"


def test_merged_favorites_from_settings_are_cleaned():
    favorites = tags.clean_favorites([
        {"tag": "gender bender", "label": "Gender", "color": "#123456", "merged": ["Gender Morph", "gender bender", ""]},
        {"tag": "gender morph", "label": "dup"},           # already merged above: dropped
        {"tag": "netorare", "merged": "netorase, netori"},  # text is accepted too
        {"tag": "glasses"},                                 # favorites saved before merging existed
    ])
    assert [(f["tag"], f["merged"]) for f in favorites] == [
        ("gender bender", ["gender morph"]), ("netorare", ["netorase", "netori"]), ("glasses", []),
    ]


def test_nested_folders_follow_the_priority():
    tags.set_favorites(tags.parse_favorites("gender bender, gender morph = Gender Bender\nnetorare = Netorare\n"
                                            "mind control = Mind Control"))
    tags.set_folders_enabled(True)
    gallery = ["female:mind control", "female:netorare", "female:gender morph"]
    assert tags.folder_parts_for(gallery) == ["Gender Bender"]
    tags.set_nested_folders(True)
    assert tags.folder_parts_for(gallery) == ["Gender Bender", "Netorare", "Mind Control"]
    assert tags.folder_for(["female:netorare"]) == "Netorare"
    downloads = Path(config.DOWNLOAD_FOLDER)
    assert _bare(AlbumDownloader, "A", gallery).site_download_folder == \
        downloads / EHENTAI_SUBFOLDER / "Gender Bender" / "Netorare" / "Mind Control"
    cbz = _bare(NHentaiAlbumDownloader, "T", ["tag:netorare", "tag:gender bender"])._cbz_path()
    assert cbz == downloads / NHENTAI_SUBFOLDER / "Gender Bender" / "Netorare" / "T.cbz"
    tags.set_folders_enabled(False)
    assert tags.folder_parts_for(gallery) == []


def test_nested_folders_are_capped():
    tags.set_favorites(tags.parse_favorites("\n".join(f"t{i}" for i in range(8))))
    tags.set_folders_enabled(True)
    tags.set_nested_folders(True)
    assert len(tags.folder_parts_for([f"tag:t{i}" for i in range(8)])) == tags.NEST_MAX_DEPTH


def test_favorites_summary_for_the_log():
    favorites = tags.parse_favorites("gender bender, gender morph = Gender\nnetorare")
    assert tags.favorites_summary(favorites) == "1 GENDER (+gender morph), 2 NETORARE"
    assert tags.favorites_summary([]) == "none"
