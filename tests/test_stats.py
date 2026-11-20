"""Download statistics: counting, saving, reading back."""

import json

import pytest

from src import config, stats


@pytest.fixture(name="fresh")
def fixture_fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "STATS_FILE", str(tmp_path / "stats.json"))
    monkeypatch.setattr(stats, "_state", stats._Stats())
    return tmp_path / "stats.json"


def test_counts_per_site_and_saves(fresh):
    stats.add_bytes("nhentai", 1000)
    stats.add_image("nhentai")
    stats.add_image("exhentai")
    stats.add_gallery("nhentai", ["tag:netorare", "language:english", "artist:x"])
    stats.add_gallery("e-hentai", ["female:netorare", "female:big breasts"])
    data = stats.snapshot()
    assert data["total"]["sites"]["nhentai"] == {"galleries": 1, "images": 1, "bytes": 1000}
    assert data["total"]["sites"]["exhentai"]["images"] == 1
    assert data["session"]["galleries"] == 2
    # only content tags are counted (not language / artist), whatever the site
    assert data["total"]["top_tags"][0] == {"tag": "netorare", "count": 2}
    assert {t["tag"] for t in data["total"]["top_tags"]} == {"netorare", "big breasts"}
    saved = json.loads(fresh.read_text(encoding="utf-8"))
    assert saved["sites"]["nhentai"]["galleries"] == 1


@pytest.mark.usefixtures("fresh")
def test_totals_survive_a_restart_but_the_session_does_not(monkeypatch):
    stats.add_gallery("e-hentai", [])
    stats.save(force=True)
    monkeypatch.setattr(stats, "_state", stats._Stats())
    data = stats.snapshot()
    assert data["total"]["galleries"] == 1
    assert data["session"]["galleries"] == 0


def test_existing_file_is_never_overwritten_unread(fresh):
    fresh.write_text(json.dumps({"first_start": "2020-01-01 00:00",
                                 "sites": {"e-hentai": {"galleries": 7, "images": 70, "bytes": 7}}}),
                     encoding="utf-8")
    stats.add_image("e-hentai")  # first use: the file is read before counting
    stats.save(force=True)
    saved = json.loads(fresh.read_text(encoding="utf-8"))
    assert saved["sites"]["e-hentai"]["images"] == 71
    assert saved["first_start"] == "2020-01-01 00:00"


@pytest.mark.usefixtures("fresh")
def test_reset():
    stats.add_gallery("nhentai", ["tag:x"])
    stats.reset()
    assert stats.snapshot()["total"]["galleries"] == 0


def test_site_of_url():
    assert stats.site_of_url("https://exhentai.org/g/1/abc/") == "exhentai"
    assert stats.site_of_url("https://nhentai.net/g/1/") == "nhentai"
    assert stats.site_of_url("https://e-hentai.org/g/1/abc/") == "e-hentai"


def test_formatting():
    assert stats.fmt_bytes(1536) == "1.5 KB"
    assert stats.fmt_speed(0) == "—"
    assert stats.fmt_duration(3725) == "1h 02m"
