"""Cancel button: every wait wakes up at once, and temp/ is emptied."""

import threading
import time

import pytest

from src import throttle
from src.file_utils import clear_folder


@pytest.fixture(autouse=True)
def _reset_throttle():
    throttle.clear_cancel()
    throttle.set_paused(False)
    yield
    throttle.clear_cancel()
    throttle.set_paused(False)
    throttle.clear_ban()


def _raises_soon(func, *args) -> float:
    """Run func in a thread, cancel after 0.2 s; return how long it took to stop."""
    caught = []

    def target():
        try:
            func(*args)
        except throttle.DownloadCancelled:
            caught.append(True)

    thread = threading.Thread(target=target, daemon=True)
    start = time.monotonic()
    thread.start()
    time.sleep(0.2)
    throttle.request_cancel()
    thread.join(timeout=3)
    assert caught, "DownloadCancelled was not raised"
    return time.monotonic() - start


def test_cancel_is_not_an_ordinary_exception():
    # The downloaders' "except Exception" (failed image / gallery) must let it through
    assert not issubclass(throttle.DownloadCancelled, Exception)


def test_sleep_is_interrupted():
    assert _raises_soon(throttle.sleep, 30) < 2


def test_user_pause_is_interrupted():
    throttle.set_paused(True)
    assert _raises_soon(throttle.wait_while_paused) < 2


def test_ban_wait_is_interrupted():
    throttle.report_ban(600, throttle.SITE_NH, margin_range=(0, 0))
    assert _raises_soon(throttle.wait_while_banned, throttle.SITE_NH) < 2


def test_new_requests_stop_after_cancel():
    throttle.request_cancel()
    with pytest.raises(throttle.DownloadCancelled):
        throttle.wait_turn(throttle.SITE_EH)
    throttle.clear_cancel()
    throttle.wait_turn(throttle.SITE_HI)  # a new download works again


def test_clear_folder_keeps_the_folder(tmp_path):
    temp = tmp_path / "temp"
    (temp / "Album A").mkdir(parents=True)
    (temp / "Album A" / "01.webp.part").write_bytes(b"x")
    (temp / "loose.txt").write_text("x")
    removed, errors = clear_folder(temp)
    assert (removed, errors) == (2, [])
    assert temp.is_dir() and not any(temp.iterdir())
    assert clear_folder(tmp_path / "missing") == (0, [])
