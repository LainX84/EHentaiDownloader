"""Request pacing and IP-ban handling shared by every part of the program.

Two things live here:

* Speed level (1 = very slow ... 5 = very fast). It sets the minimum pause between two
  requests to the gallery / picture HTML pages (shared by all threads, so 5 workers do
  not mean 5x the requests) and how many images are downloaded in parallel.
  The GUI slider changes it while a download runs: the pause applies immediately, the
  number of parallel downloads from the next album.

* Temporary IP ban. When E-Hentai answers "This IP address has been temporarily banned
  ... The ban expires in X minutes and Y seconds", the ban time is read from the page,
  a safety margin is added and every request waits until it is over. The current album
  is paused, not skipped: nothing moves on to the next gallery while the ban lasts.

* User pause (GUI "Pause" button): no new request starts until "Resume"; images already
  being received finish normally.
"""

from __future__ import annotations

import logging
import random
import re
import threading
import time
from typing import Callable

from src.config import (
    BAN_DEFAULT_WAIT,
    BAN_EXTRA_WAIT,
    DEFAULT_SPEED,
)

# level: (name, seconds between page requests, parallel image downloads)
SPEED_LEVELS: dict[int, tuple[str, float, int]] = {
    1: ("Very slow", 4.0, 1),
    2: ("Slow", 2.5, 2),
    3: ("Normal", 1.5, 3),
    4: ("Fast", 0.8, 4),
    5: ("Very fast", 0.3, 5),
}
MIN_SPEED = min(SPEED_LEVELS)
MAX_SPEED = max(SPEED_LEVELS)

_lock = threading.Lock()
_speed = DEFAULT_SPEED if DEFAULT_SPEED in SPEED_LEVELS else 3
_next_slot = 0.0          # monotonic time of the next allowed page request
_ban_until = 0.0          # monotonic time when the current ban (plus margin) ends
_ban_listeners: list[Callable[[float], None]] = []
_running = threading.Event()  # cleared = paused by the user
_running.set()

_BAN_RE = re.compile(r"ban expires in(.{0,120})", re.IGNORECASE | re.DOTALL)
_UNIT_RE = re.compile(r"(\d+)\s*(day|hour|minute|second)", re.IGNORECASE)
_UNIT_SECONDS = {"day": 86400, "hour": 3600, "minute": 60, "second": 1}


# ---------------------------------------------------------------- speed

def set_speed(level: int) -> None:
    """Change the speed level (clamped to MIN_SPEED..MAX_SPEED)."""
    global _speed
    with _lock:
        _speed = max(MIN_SPEED, min(MAX_SPEED, int(level)))


def get_speed() -> int:
    """Return the current speed level."""
    return _speed


def speed_name(level: int | None = None) -> str:
    """Human name of a speed level, e.g. 'Normal'."""
    return SPEED_LEVELS[_speed if level is None else level][0]


def current_workers() -> int:
    """Number of images to download in parallel at the current speed."""
    return SPEED_LEVELS[_speed][2]


def wait_turn() -> None:
    """Block until this thread may send the next page request (global pacing)."""
    global _next_slot
    wait_while_paused()
    with _lock:
        interval = SPEED_LEVELS[_speed][1]
        now = time.monotonic()
        slot = max(now, _next_slot)
        _next_slot = slot + interval * random.uniform(0.8, 1.25)  # noqa: S311
    delay = slot - now
    if delay > 0:
        time.sleep(delay)


# ---------------------------------------------------------------- user pause

def set_paused(paused: bool) -> None:
    """Pause (True) or resume (False) every download thread."""
    if paused:
        _running.clear()
    else:
        _running.set()


def is_paused() -> bool:
    """True while the user has paused the downloads."""
    return not _running.is_set()


def wait_while_paused() -> None:
    """Block the calling thread while the downloads are paused."""
    _running.wait()


# ---------------------------------------------------------------- ban

def ban_seconds_from_page(text: str) -> int | None:
    """Return the ban length in seconds if the page is the 'IP temporarily banned' page.

    None means the page is not a ban page. When the page is a ban page but the time can't
    be read, BAN_DEFAULT_WAIT is returned.
    """
    if not text:
        return None
    lowered = text.lower()
    if "temporarily banned" not in lowered and "ip address has been" not in lowered:
        return None
    match = _BAN_RE.search(text)
    if match:
        seconds = sum(
            int(num) * _UNIT_SECONDS[unit.lower()] for num, unit in _UNIT_RE.findall(match.group(1))
        )
        if seconds > 0:
            return seconds
    return BAN_DEFAULT_WAIT


def report_ban(seconds: int) -> None:
    """Record a ban of the given length; every request waits until it ends (plus a margin)."""
    global _ban_until, _next_slot
    margin = random.uniform(*BAN_EXTRA_WAIT)  # noqa: S311
    until = time.monotonic() + seconds + margin
    with _lock:
        is_new = until > _ban_until + 5  # several threads report the same ban at once
        _ban_until = max(_ban_until, until)
        _next_slot = max(_next_slot, _ban_until)
        listeners = list(_ban_listeners)
        current_until = _ban_until
    if not is_new:
        return
    total = int(current_until - time.monotonic())
    logging.warning(
        "IP temporarily banned by E-Hentai (too many requests). Downloads paused for "
        f"{total // 60}m {total % 60:02d}s ({seconds}s ban + {margin:.0f}s margin), then they resume "
        "from where they stopped. Tip: lower the speed, or switch IP with a VPN and press 'Resume now'."
    )
    for callback in listeners:
        try:
            callback(current_until)
        except Exception:  # a GUI callback must never break a download thread
            pass


def ban_remaining() -> float:
    """Seconds left before the current ban ends (0 when not banned)."""
    return max(0.0, _ban_until - time.monotonic())


def clear_ban() -> None:
    """Forget the current ban (e.g. the user switched IP with a VPN)."""
    global _ban_until, _next_slot
    with _lock:
        _ban_until = 0.0
        _next_slot = 0.0
        listeners = list(_ban_listeners)
    logging.info("Ban wait cancelled by the user: downloads resume now.")
    for callback in listeners:
        try:
            callback(0.0)
        except Exception:
            pass


def wait_while_banned() -> None:
    """Block while a ban is active (checks every second, so 'Resume now' is quick)."""
    while True:
        remaining = ban_remaining()
        if remaining <= 0:
            return
        time.sleep(min(1.0, remaining))


def add_ban_listener(callback: Callable[[float], None]) -> None:
    """Call callback(ban_until_monotonic) when a ban starts, or callback(0.0) when cleared."""
    with _lock:
        _ban_listeners.append(callback)
