"""Only one copy of the desktop program: lock, second start, show request."""

# pylint: disable=protected-access

import subprocess
import sys
import threading
import textwrap

from src.gui.single_instance import INFO_FILE, SingleInstance


def test_second_copy_is_refused_and_shows_the_first(tmp_path):
    first = SingleInstance(tmp_path)
    assert first.acquire()
    shown = threading.Event()
    first.listen(shown.set)
    try:
        # A real second process: the lock is per process on every system
        code = textwrap.dedent(f"""
            import sys
            sys.path.insert(0, {str(__import__('pathlib').Path(__file__).resolve().parent.parent)!r})
            from src.gui.single_instance import SingleInstance
            second = SingleInstance({str(tmp_path)!r})
            sys.exit(10 + (not second.acquire()) * 10 + second.notify_running())
        """)
        result = subprocess.run([sys.executable, "-c", code], check=False, timeout=30)
        assert result.returncode == 21          # lock refused, first copy answered
        assert shown.wait(5)
    finally:
        first.release()
    assert not (tmp_path / INFO_FILE).exists()


def test_lock_is_free_again_after_release(tmp_path):
    first = SingleInstance(tmp_path)
    assert first.acquire()
    first.release()
    again = SingleInstance(tmp_path)
    assert again.acquire()
    again.release()


def test_wrong_token_does_not_show(tmp_path):
    first = SingleInstance(tmp_path)
    assert first.acquire()
    shown = threading.Event()
    first.listen(shown.set)
    try:
        info = (tmp_path / INFO_FILE).read_text(encoding="utf-8").replace(first._token, "0" * 32)
        (tmp_path / INFO_FILE).write_text(info, encoding="utf-8")
        assert not SingleInstance(tmp_path).notify_running()
        assert not shown.wait(0.5)
    finally:
        first.release()


def test_no_running_copy_answers_false(tmp_path):
    assert not SingleInstance(tmp_path).notify_running()
