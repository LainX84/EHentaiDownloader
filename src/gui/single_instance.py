"""Only one copy of the desktop program at a time (Windows, Linux, macOS).

How it works:
  * The first copy locks the file `.instance.lock` in the data folder (the folder of URLs.txt,
    settings.json…). The lock belongs to the running process: the system drops it when the
    program ends, even after a crash, so a stale lock never blocks the next start.
  * The same copy listens on a local socket (127.0.0.1, random port) and writes the port and a
    random token in `.instance.json`.
  * A second start finds the file locked: it sends "show" to the first copy (which brings its
    window back, also from the system tray) and closes itself. If the first copy does not
    answer, a message says that the program is already running.

Two copies in different data folders (e.g. the source code and a portable app) do not share
any file, so they may run side by side.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import socket
import sys
import threading
from pathlib import Path
from typing import Callable

LOCK_FILE = ".instance.lock"
INFO_FILE = ".instance.json"
CONNECT_TIMEOUT = 2.0        # seconds the second copy waits for the first one
_SHOW = b"show"
_OK = b"ok"


def _lock(fd: int) -> bool:
    """Lock the open file without waiting. False when another process holds it."""
    try:
        if os.name == "nt":
            import msvcrt  # pylint: disable=import-outside-toplevel

            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl  # pylint: disable=import-outside-toplevel

            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


class SingleInstance:
    """Lock held by the running copy, plus the socket that other starts talk to."""

    def __init__(self, folder: str | os.PathLike | None = None) -> None:
        self.folder = Path(folder) if folder is not None else Path.cwd()
        self._fd: int | None = None
        self._server: socket.socket | None = None
        self._token = ""

    # ---- first copy ---------------------------------------------------------------------------
    def acquire(self) -> bool:
        """True when this is the only copy (the lock is now ours)."""
        try:
            fd = os.open(self.folder / LOCK_FILE, os.O_RDWR | os.O_CREAT, 0o600)
        except OSError as err:
            # Data folder not writable: better to start than to refuse for good
            logging.warning(f"Single-instance lock not available ({err}).")
            return True
        if not _lock(fd):
            os.close(fd)
            return False
        self._fd = fd
        return True

    def listen(self, on_show: Callable[[], None]) -> None:
        """Answer later starts: on_show() is called (from a background thread) for each one."""
        if self._fd is None or self._server is not None:
            return
        try:
            server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server.bind(("127.0.0.1", 0))
            server.listen(4)
            self._token = secrets.token_hex(16)
            info = {"port": server.getsockname()[1], "token": self._token, "pid": os.getpid()}
            path = self.folder / INFO_FILE
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(json.dumps(info), encoding="utf-8")
            os.replace(tmp, path)
        except OSError as err:
            logging.warning(f"Single-instance listener not available ({err}).")
            return
        self._server = server
        threading.Thread(target=self._serve, args=(server, on_show), name="single-instance",
                         daemon=True).start()

    def _serve(self, server: socket.socket, on_show: Callable[[], None]) -> None:
        expected = self._token.encode() + b" " + _SHOW
        while True:
            try:
                conn, _addr = server.accept()
            except OSError:
                return   # socket closed: the program is ending
            try:
                conn.settimeout(CONNECT_TIMEOUT)
                data = conn.recv(256).strip()
                if secrets.compare_digest(data, expected):
                    conn.sendall(_OK)
                    on_show()
            except OSError:
                pass
            finally:
                conn.close()

    def release(self) -> None:
        """Program closing: stop listening and free the lock."""
        server, self._server = self._server, None
        if server is not None:
            try:
                server.close()
            except OSError:
                pass
            try:
                (self.folder / INFO_FILE).unlink()
            except OSError:
                pass
        fd, self._fd = self._fd, None
        if fd is not None:
            try:
                os.close(fd)   # closing the file drops the lock
            except OSError:
                pass

    # ---- second copy --------------------------------------------------------------------------
    def notify_running(self) -> bool:
        """Ask the running copy to show its window. True when it answered."""
        try:
            info = json.loads((self.folder / INFO_FILE).read_text(encoding="utf-8"))
            port, token = int(info["port"]), str(info["token"])
        except (OSError, ValueError, KeyError, TypeError):
            return False
        _allow_foreground(info.get("pid"))
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=CONNECT_TIMEOUT) as conn:
                conn.sendall(token.encode() + b" " + _SHOW)
                return conn.recv(16).strip() == _OK
        except OSError:
            return False


def _allow_foreground(pid) -> None:
    """Windows: let the running copy bring its window in front of this one."""
    if os.name != "nt":
        return
    try:
        import ctypes  # pylint: disable=import-outside-toplevel

        target = int(pid) if pid else -1   # -1 = ASFW_ANY
        ctypes.windll.user32.AllowSetForegroundWindow(target)
    except Exception:  # pylint: disable=broad-except
        pass


def show_already_running() -> None:
    """The running copy did not answer: say so in a small message box."""
    try:
        import tkinter as tk  # pylint: disable=import-outside-toplevel
        from tkinter import messagebox  # pylint: disable=import-outside-toplevel

        root = tk.Tk()
        root.withdraw()
        messagebox.showinfo(
            "EHentai Downloader",
            "EHentai Downloader is already running.\n\n"
            "Look for its window on the taskbar, or for its icon in the system tray "
            "(next to the clock).",
            parent=root,
        )
        root.destroy()
    except Exception:  # pylint: disable=broad-except  # no display: nothing else to do
        print("EHentai Downloader is already running.", file=sys.stderr)
