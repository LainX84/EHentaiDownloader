"""hitomi.la: links, gallery data, gg.js, image addresses and a full download (no network)."""

from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from src import config, hitomi, stats, tags, throttle
from src.cli import downloader as d
from src.downloader.hitomi_downloader import HitomiAlbumDownloader, _Gate
from src.general_utils import extract_gallery_links, validate_gallery_url

HI = "https://hitomi.la/galleries/3105447.html"

GALLERY_JS = """var galleryinfo = {"id":"3105447","title":"Some  Title","japanese_title":null,
"language":"english","type":"doujinshi",
"files":[{"hash":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa21f","name":"01.jpg",
          "haswebp":1,"hasavif":1,"width":1000,"height":1400},
         {"hash":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb0c3","name":"02.gif",
          "haswebp":1,"hasavif":0}],
"tags":[{"tag":"big breasts","female":"1","male":""},{"tag":"shota","male":"1","female":""},
        {"tag":"full color","female":"","male":""}],
"artists":[{"artist":"someone","url":"/artist/someone-all.html"}],
"groups":[{"group":"circle"}],"parodys":[{"parody":"original"}],"characters":null}"""

# Same shape as the live gg.js (Nov 2026), shortened
GG_JS = """'use strict';
gg = { m: function(g) {
var o = 0;
switch (g) {
case 1303:
case 3979:
case 543:
o = 1; break;
}
return o;
},
s: function(h) { var m = /(..)(.)$/.exec(h); return parseInt(m[2]+m[1], 16).toString(10); },
b: '1795449601/'
};"""


@pytest.fixture(autouse=True)
def _no_pacing(monkeypatch):
    """No waits between the fake requests."""
    monkeypatch.setattr(throttle, "wait_turn", lambda *_: None)
    monkeypatch.setattr(hitomi, "HITOMI_DATA_INTERVAL", 0)
    monkeypatch.setattr("src.downloader.hitomi_downloader.HITOMI_IMAGE_DELAY", (0, 0))


# ---------------------------------------------------------------- links

@pytest.mark.parametrize("url", [
    HI,
    "https://hitomi.la/reader/3105447.html",
    "https://hitomi.la/reader/3105447.html#7",
    "https://hitomi.la/doujinshi/some-title-english-3105447.html",
    "https://hitomi.la/manga/a-b-3105447.html?x=1",
    "https://hitomi.la/gamecg/x-3105447.html",
])
def test_gallery_links_are_recognised(url):
    assert hitomi.gallery_id(url) == "3105447"
    assert validate_gallery_url(url) is None
    assert d._normalize_album_url(url) == HI
    assert d.gallery_key(url) == "hi3105447"


@pytest.mark.parametrize("url", [
    "https://hitomi.la/tag/female%3Abig%20breasts-all.html",
    "https://hitomi.la/search.html?abc",
    "https://hitomi.la/",
])
def test_other_hitomi_pages_are_not_galleries(url):
    assert hitomi.is_hitomi_url(url)
    assert "Invalid hitomi URL" in validate_gallery_url(url)


def test_links_in_text():
    text = (f"look https://hitomi.la/doujinshi/x-y-3105447.html and {HI} again, "
            "https://hitomi.la/reader/12.html#3, https://nhentai.net/g/5/")
    assert extract_gallery_links(text) == [HI, "https://hitomi.la/galleries/12.html", "https://nhentai.net/g/5/"]


def test_hitomi_ids_differ_from_nhentai_ones():
    assert d.gallery_key("https://hitomi.la/galleries/7.html") != d.gallery_key("https://nhentai.net/g/7/")


# ---------------------------------------------------------------- gallery data and gg.js

def test_gallery_data():
    info = hitomi.parse_gallery_js(GALLERY_JS, "3105447")
    assert info["title"] == "[Circle (Someone)] Some Title" and info["raw_title"] == "Some Title"
    assert [p["formats"] for p in info["pages"]] == [["webp", "avif"], ["webp"]]
    assert info["artists"] == ["Someone"] and info["parodies"] == ["original"]
    assert tags.from_hitomi_info(info) == [
        "female:big breasts", "male:shota", "tag:full color", "artist:someone", "group:circle",
        "parody:original", "language:english",
    ]


@pytest.mark.parametrize("artists, groups, expected", [
    (["hiiragi popura"], [], "[Hiiragi Popura] 彼女と入れ替わった僕は寝取られる"),  # group N/A, as on the site
    (["a b", "c"], ["circle x"], "[Circle X (A B, C)] 彼女と入れ替わった僕は寝取られる"),
    ([], ["some circle"], "[Some Circle] 彼女と入れ替わった僕は寝取られる"),
    (["a", "b", "c", "d"], [], "[Various] 彼女と入れ替わった僕は寝取られる"),
    (["DATE"], [], "[DATE] 彼女と入れ替わった僕は寝取られる"),               # names not in lower case kept
    ([], [], "彼女と入れ替わった僕は寝取られる"),
])
def test_authors_in_front_of_the_title(artists, groups, expected):
    assert hitomi.with_credits("彼女と入れ替わった僕は寝取られる", artists, groups) == expected


def test_title_that_already_has_the_authors_is_kept():
    assert hitomi.with_credits("[Someone] Title", ["someone"], []) == "[Someone] Title"


def test_video_gallery_is_refused():
    with pytest.raises(ValueError, match="anime"):
        hitomi.parse_gallery_js('var galleryinfo = {"id":"1","type":"anime","files":[]}', "1")


def test_gg_and_image_address():
    gg = hitomi.parse_gg(GG_JS)
    assert gg == ({1303: 1, 3979: 1, 543: 1}, "1795449601", 0)
    # last char + the two before it: "21f" -> "f" + "21" -> 0xf21 = 3873 (not in the list: server 1)
    assert hitomi.image_number("x21f") == 0xF21
    first = "a" * 61 + "21f"
    assert hitomi.image_url(first, "webp", gg, "example.net") == \
        f"https://w1.example.net/1795449601/{0xF21}/{first}.webp"
    # 543 = 0x21f in the list: a hash ending "1f2" ("2" + "1f") is on server 2
    assert hitomi.image_url("abc1f2", "avif", gg, "example.net") == "https://a2.example.net/1795449601/543/abc1f2.avif"


def test_gg_with_if_lines_and_default():
    text = "var o = 1; if (g === 12) { o = 0; } b: '99/'"
    assert hitomi.parse_gg(text) == ({12: 0}, "99", 1)


def test_format_asked_to_the_site():
    page = {"formats": ["webp", "avif"]}
    assert hitomi.formats_to_try(page, "avif") == ["avif", "webp"]
    assert hitomi.formats_to_try(page, "original") == ["webp", "avif"]
    assert hitomi.formats_to_try(page, "jpg") == ["webp", "avif"]          # converted from WebP
    assert hitomi.formats_to_try({"formats": ["webp"]}, "avif") == ["webp"]  # no AVIF: WebP, then converted


def test_data_domain_read_from_a_page():
    html = '<script src="//ltn.new-domain.example/reader.js"></script>'
    assert hitomi.domain_from_page(html) == "new-domain.example"
    assert hitomi.domain_from_page("<html></html>") == ""


# ---------------------------------------------------------------- lists, stats, folders

def test_list_line_and_order():
    d._gallery_info.clear()
    try:
        d.remember_gallery_info(HI, title="T", images=28, status="GOOD")
        line = d.format_url_line(HI)
        assert line.startswith("✅ HITOMI") and line.endswith(f"| {HI}")
        assert d.parse_table_line(line)["site"] == "HITOMI"
        nh, eh = "https://nhentai.net/g/1/", "https://e-hentai.org/g/2/abcdef0123/"
        d.remember_gallery_info(nh, title="N", images=5, status="GOOD")
        d.remember_gallery_info(eh, title="E", images=50, status="GOOD")
        ordered = d.sort_lines_by_size([d.format_url_line(u) for u in (HI, nh, eh)])
        assert [d.parse_url_line(x)[0] for x in ordered] == [eh, nh, HI]
    finally:
        d._gallery_info.clear()


def test_site_of_the_link():
    assert stats.site_of_url(HI) == "hitomi"
    assert d.site_name(HI) == "HITOMI"
    from src.web.engine import site_lane
    assert site_lane(HI) == throttle.SITE_HI
    assert site_lane("https://nhentai.net/g/1/") == throttle.SITE_NH


def test_folder_of_hitomi_albums():
    album = HitomiAlbumDownloader.__new__(HitomiAlbumDownloader)
    album.clean_album_name = album.album_name = "A"
    album.gallery_tags = []
    assert album.site_download_folder == Path(config.DOWNLOAD_FOLDER) / config.HITOMI_SUBFOLDER


# ---------------------------------------------------------------- full download (fake servers)

class _LiveManager:  # pylint: disable=missing-function-docstring
    """The few LiveManager methods a downloader calls."""

    def __init__(self):
        self.logs = []

    def update_log(self, event, details):
        self.logs.append((event, details))

    def add_overall_task(self, **_):
        pass

    def add_task(self, **_):
        return 0

    def update_task(self, *_, **__):
        pass


def _image_bytes(fmt: str) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (8, 8), "red").save(buffer, format=fmt)
    return buffer.getvalue()


class _Response:  # pylint: disable=missing-function-docstring
    def __init__(self, status, content=b"", content_type="image/webp", text=""):
        self.status_code, self.content, self.text = status, content, text
        self.headers = {"Content-Type": content_type, "content-length": str(len(content))}

    def iter_content(self, chunk_size):
        for i in range(0, len(self.content), chunk_size):
            yield self.content[i:i + chunk_size]

    def raise_for_status(self):
        pass

    def close(self):
        pass


def test_download_whole_gallery(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(hitomi, "_gg", {})
    requested = []

    def fake_get(self, url, **_):  # pylint: disable=unused-argument
        requested.append(url)
        if url.endswith("/galleries/3105447.js"):
            return _Response(200, text=GALLERY_JS)
        if url.endswith("/gg.js"):
            return _Response(200, text=GG_JS)
        if url.endswith(".avif"):
            return _Response(200, _image_bytes("AVIF"), "image/avif")
        if url.endswith(".webp"):
            return _Response(200, _image_bytes("WEBP"))
        return _Response(404)

    monkeypatch.setattr("requests.Session.get", fake_get)
    lm = _LiveManager()
    album = HitomiAlbumDownloader("https://hitomi.la/reader/3105447.html#2", lm, image_format="avif",
                                  rename_sequential=True, create_cbz=False)
    assert album.url == HI and album.total_images == 2
    assert not album.download_album()
    folder = tmp_path / config.DOWNLOAD_FOLDER / config.HITOMI_SUBFOLDER / "[Circle (Someone)] Some Title"
    # page 1 has AVIF on the site (asked directly), page 2 only WebP (converted to AVIF)
    assert sorted(p.name for p in folder.iterdir()) == ["01.avif", "02.avif"]
    image_urls = [u for u in requested if "/1795449601/" in u]
    assert image_urls[0].startswith("https://a1.") or image_urls[1].startswith("https://a1.")
    assert any(u.endswith(".webp") for u in image_urls)


def test_stale_gg_is_read_again(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(hitomi, "_gg", {})
    gg_versions = iter([GG_JS.replace("1795449601", "111"), GG_JS])

    def fake_get(self, url, **_):  # pylint: disable=unused-argument
        if url.endswith(".js") and "/galleries/" in url:
            return _Response(200, text=GALLERY_JS)
        if url.endswith("/gg.js"):
            return _Response(200, text=next(gg_versions))
        if "/1795449601/" in url and url.endswith(".webp"):
            return _Response(200, _image_bytes("WEBP"))
        return _Response(404)

    monkeypatch.setattr("requests.Session.get", fake_get)
    album = HitomiAlbumDownloader(HI, _LiveManager(), image_format="original", create_cbz=False)
    response, name = album._fetch_page_image(1)
    assert response is not None and name == "1.webp"


# ---------------------------------------------------------------- caution: 503 / 429, parallel images

@pytest.fixture(name="pauses")
def _pauses(monkeypatch):
    """Record the hitomi pauses instead of waiting for them."""
    recorded = []
    monkeypatch.setattr(throttle, "report_ban", lambda seconds, source, margin_range: recorded.append(
        (source, seconds, margin_range)))
    monkeypatch.setattr(throttle, "ban_remaining", lambda site=None: 0.0)
    monkeypatch.setattr(throttle, "wait_while_banned", lambda site=None: None)
    return recorded


def _gallery_server(monkeypatch, image_answers):
    """Fake hitomi: gallery data, gg.js, and images answered from image_answers (status codes, then 200)."""
    answers = iter(image_answers)
    calls = []

    def fake_get(self, url, **_):  # pylint: disable=unused-argument
        if "/galleries/" in url:
            return _Response(200, text=GALLERY_JS)
        if url.endswith("/gg.js"):
            return _Response(200, text=GG_JS)
        calls.append(url)
        status = next(answers, 200)
        return _Response(200, _image_bytes("WEBP")) if status == 200 else _Response(status)

    monkeypatch.setattr("requests.Session.get", fake_get)
    monkeypatch.setattr(hitomi, "_gg", {})
    return calls


def test_503_pauses_hitomi_and_asks_the_same_image_again(tmp_path, monkeypatch, pauses):
    monkeypatch.chdir(tmp_path)
    calls = _gallery_server(monkeypatch, [503, 200])
    album = HitomiAlbumDownloader(HI, _LiveManager(), image_format="webp", create_cbz=False)
    response, name = album._fetch_page_image(1)
    assert response is not None and name == "1.webp"
    assert calls[0] == calls[1]                       # same image, not another format
    source, seconds, margin = pauses[0]
    assert source == throttle.SITE_HI
    assert config.HITOMI_SLOWDOWN_WAIT[0] <= seconds <= config.HITOMI_SLOWDOWN_WAIT[1]
    low, high = config.HITOMI_SOFTBAN_EXTRA_WAIT
    assert margin[0] == margin[1] and low <= margin[0] <= high      # the soft-ban extra of 45-60 s


def test_429_with_retry_after_waits_the_time_asked_plus_extra(pauses):
    response = _Response(429)
    response.headers["Retry-After"] = "120"
    ban = hitomi.report_slowdown(response, "x")
    assert ban.new and ban.asked and ban.wait == 120.0 and pauses[0][1] == 120.0
    assert 120 + 45 <= ban.total <= 120 + 60


def test_soft_ban_is_written_in_the_log(tmp_path, monkeypatch, pauses):
    monkeypatch.chdir(tmp_path)
    _gallery_server(monkeypatch, [503, 200])
    lm = _LiveManager()
    album = HitomiAlbumDownloader(HI, lm, image_format="webp", create_cbz=False)
    album._fetch_page_image(1)
    text = next(text for event, text in lm.logs if event == "Soft ban")
    assert "hitomi soft ban (HTTP 503" in text and "s extra" in text and len(pauses) == 1


def test_repeated_slowdowns_go_one_image_at_a_time(tmp_path, monkeypatch, pauses):
    monkeypatch.chdir(tmp_path)
    _gallery_server(monkeypatch, [503, 503, 200])
    lm = _LiveManager()
    album = HitomiAlbumDownloader(HI, lm, image_format="webp", create_cbz=False)
    album._gate = _Gate(2)
    album._fetch_page_image(1)
    assert len(pauses) == 2 and album._gate.limit == 1
    assert any("one image at a time" in text for _, text in lm.logs)


def test_site_that_stays_busy_leaves_the_page_to_retry_failed(tmp_path, monkeypatch, pauses):
    monkeypatch.chdir(tmp_path)
    calls = _gallery_server(monkeypatch, [503] * 50)
    album = HitomiAlbumDownloader(HI, _LiveManager(), image_format="avif", create_cbz=False)
    response, error = album._fetch_page_image(1)
    assert response is None and "too many requests" in error
    assert len(calls) == config.HITOMI_SLOWDOWN_TRIES + 1 and all(u.endswith(".avif") for u in calls)
    assert len(pauses) == config.HITOMI_SLOWDOWN_TRIES


def test_already_paused_is_not_a_new_slowdown(monkeypatch, pauses):
    monkeypatch.setattr(throttle, "ban_remaining", lambda site=None: 25.0)
    assert not hitomi.report_slowdown(_Response(503), "x").new
    assert len(pauses) == 1  # the pause is still extended, but not counted again


def test_data_request_503_pauses_then_retries(monkeypatch, pauses):
    answers = iter([_Response(503), _Response(200, text=GALLERY_JS)])
    monkeypatch.setattr("requests.Session.get", lambda self, url, **_: next(answers))
    info = hitomi.fetch_gallery_info(HI)
    assert info["title"] == "[Circle (Someone)] Some Title" and len(pauses) == 1


@pytest.mark.usefixtures("pauses")
@pytest.mark.parametrize("level, workers", [(1, 1), (2, 2), (3, 2), (4, 3), (5, 4)])
def test_images_in_parallel_by_speed_level(tmp_path, monkeypatch, level, workers):
    monkeypatch.chdir(tmp_path)
    _gallery_server(monkeypatch, [])
    monkeypatch.setattr(throttle, "get_speed", lambda: level)
    lm = _LiveManager()
    album = HitomiAlbumDownloader(HI, lm, image_format="webp", create_cbz=False)
    assert not album.download_album()
    assert album._gate.limit == workers
    assert any(f"{workers} parallel download(s)" in text for _, text in lm.logs)
