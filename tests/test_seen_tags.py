"""Tags met in the galleries (tags.json) and the list of the favorite tags window."""

import json
from pathlib import Path

import pytest

from src import config, tags
from src.cli import downloader as d


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setattr(tags, "_seen_path", "")
    d._gallery_info.clear()
    yield
    tags.set_favorites([])
    d._gallery_info.clear()


def test_tags_of_verified_or_downloaded_galleries_are_remembered():
    d.remember_gallery_info("https://nhentai.net/g/1/", title="A", tags=["tag:netorare", "language:english"])
    d.remember_gallery_info("https://e-hentai.org/g/2/abcdef0123/", title="B",
                            tags=["female:netorare", "female:big breasts"])
    # the same gallery again (download after Verify Links, or in a later run) is not counted twice
    d.remember_gallery_info("https://nhentai.net/g/1/", tags=["tag:netorare"])
    saved = json.loads(Path(config.TAGS_FILE).read_text(encoding="utf-8"))
    assert saved["seen"] == {"netorare": 2, "big breasts": 1}  # content tags only
    assert saved["galleries"]["nh1"] == ["tag:netorare", "language:english"]


def test_catalog_lists_favorites_first_then_seen_then_suggested():
    tags.record_gallery("g1", ["female:glasses", "female:netorare"])
    tags.record_gallery("g2", ["female:glasses"])
    tags.set_favorites(tags.parse_favorites("netorare = NTR\nartist:someone"))
    rows = tags.catalog()
    assert [r["tag"] for r in rows[:3]] == ["netorare", "artist:someone", "glasses"]
    assert rows[0] == {"tag": "netorare", "label": "NTR", "color": tags.DEFAULT_COLORS[0], "favorite": True,
                       "seen": 1, "suggested": False}
    assert rows[2]["seen"] == 2 and not rows[2]["favorite"]
    suggested = [r["tag"] for r in rows if r["suggested"]]
    assert "cheating" in suggested and "netorare" not in suggested
    assert tags.seen_count() == 2


def test_tags_file_is_read_back(monkeypatch):
    tags.record_gallery("g1", ["female:sole female"])
    monkeypatch.setattr(tags, "_seen_path", "")
    tags._seen.clear()
    tags._galleries.clear()
    assert tags.seen_count() == 1
    assert tags.cached_tags("g1") == ["female:sole female"]
