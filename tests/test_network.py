"""Internet connection lost: downloads wait for it instead of failing."""

import threading
import time

import pytest
import requests

from src import throttle
from src.downloader import download_utils


class _Log:  # pylint: disable=too-few-public-methods
    def __init__(self):
        self.events = []

    def update_log(self, event, details):
        self.events.append((event, details))


class _Response:  # pylint: disable=too-few-public-methods
    headers = {"content-length": "3"}
    _content = b""

    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size):  # pylint: disable=unused-argument
        yield b"abc"


class _FlakySession:  # pylint: disable=too-few-public-methods
    """Fails with a connection error until 'online' becomes True."""

    def __init__(self, state):
        self.state = state
        self.calls = 0

    def get(self, *_args, **_kwargs):
        self.calls += 1
        if not self.state["online"]:
            raise requests.ConnectionError("network is unreachable")
        return _Response()


@pytest.fixture(name="state")
def fixture_state(monkeypatch):
    state = {"online": True}
    monkeypatch.setattr(throttle, "internet_available", lambda timeout=3.0: state["online"])
    monkeypatch.setattr(throttle, "NETWORK_CHECK_INTERVAL", 0.05)
    yield state
    throttle._online.set()  # pylint: disable=protected-access


def test_outage_waits_and_does_not_use_up_the_attempts(state):
    state["online"] = False
    session = _FlakySession(state)
    events = []
    throttle.add_network_listener(events.append)
    before = throttle.network_outages()
    threading.Timer(0.3, lambda: state.update(online=True)).start()
    started = time.monotonic()
    response = download_utils._fetch_with_retries_single(  # pylint: disable=protected-access
        session, "https://example.org/x.jpg", _Log(), retries=1)
    assert response is not None and response._content == b"abc"  # pylint: disable=protected-access
    assert time.monotonic() - started >= 0.25
    assert throttle.network_outages() == before + 1
    assert events[-2:] == [False, True]
    assert throttle.is_online()


def test_real_error_with_internet_working_is_not_an_outage(state):
    assert state["online"]
    before = throttle.network_outages()
    assert throttle.wait_if_offline(requests.ConnectionError("refused")) is False
    assert throttle.network_outages() == before


def test_gallery_is_checked_again_after_an_outage(monkeypatch):
    from src.downloader.album_downloader import AlbumDownloader

    album = AlbumDownloader.__new__(AlbumDownloader)
    album.clean_album_name = album.album_name = "A"
    album.live_manager = _Log()
    album._processed_images_lock = threading.Lock()  # pylint: disable=protected-access
    album._processed_images_count = 7  # pylint: disable=protected-access
    passes = []
    outages = {"n": 0}
    monkeypatch.setattr(throttle, "network_outages", lambda: outages["n"])

    def download_pages():
        passes.append(len(passes))
        if len(passes) == 1:
            outages["n"] += 1  # the connection dropped during the first pass
            return ["https://e-hentai.org/s/abc/1-5"]
        return []

    album._download_pages = download_pages  # pylint: disable=protected-access
    album._skip_if_archived = lambda: False  # pylint: disable=protected-access
    album._complete_album = lambda failed: failed  # pylint: disable=protected-access
    assert not album.download_album()
    assert passes == [0, 1]
    assert any(event == "Connection check" for event, _ in album.live_manager.events)
