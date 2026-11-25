"""Request pacing and IP-ban handling shared by every part of the program.

Two things live here:

* Speed level (1 = very slow ... 5 = very fast). It sets the minimum pause between two
  requests to the gallery / picture HTML pages (shared by all threads of the same site,
  so 5 workers do not mean 5x the requests) and how many images are downloaded in
  parallel. E-Hentai / ExHentai, nhentai and hitomi are different servers with different
  rules: each one has its own pace and its own ban, so a batch can download one gallery
  from each site at the same time.
  The GUI slider changes it while a download runs: the pause applies immediately, the
  number of parallel downloads from the next album.

* Temporary IP ban. When E-Hentai answers "This IP address has been temporarily banned
  ... The ban expires in X minutes and Y seconds", the ban time is read from the page,
  a safety margin is added and every E-Hentai request waits until it is over. The current
  album is paused, not skipped: nothing moves on to the next gallery while the ban lasts
  (nhentai and hitomi downloads go on).

* nhentai "Too Many Requests" (HTTP 429): same pause, only for nhentai, for the time the site
  asks (Retry-After) plus a random 10-15 s margin (NHENTAI_BLOCK_EXTRA_WAIT).

* hitomi "soft ban" (HTTP 503 / 429): same pause, only for hitomi, for the time asked (or
  30-60 s) plus a random 45-60 s (HITOMI_SOFTBAN_EXTRA_WAIT), see src/hitomi.py.

* User pause (GUI "Pause" button): no new request starts until "Resume"; images already
  being received finish normally.

* User cancel (GUI "Cancel" button): every waiting download thread (pause, ban, pacing,
  courtesy sleeps, lost connection) wakes up at once and raises DownloadCancelled, and so
  does every new request. DownloadCancelled is a BaseException on purpose: the many
  "except Exception" of the downloaders (which count an error as a failed image) let it
  through, so a cancelled gallery stops instead of being recorded as failed.

* Internet connection lost: when a request fails with a connection error and no site can
  be reached at all, every download waits (like the user pause) until the connection is
  back, checked every NETWORK_CHECK_INTERVAL seconds; the request is then sent again
  instead of being counted as failed. The gallery in progress is checked once more when
  it ends (see AlbumDownloader.download_album).
"""

from __future__ import annotations

import logging
import random
import re
import socket
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

# Sites with their own pacing and ban: E-Hentai / ExHentai (same servers, same IP ban),
# nhentai and hitomi. Each site has its own clock, so they can download side by side.
SITE_EH = "E-Hentai"
SITE_NH = "nhentai"
SITE_HI = "hitomi"
SITES = (SITE_EH, SITE_NH, SITE_HI)

_lock = threading.Lock()
_speed = DEFAULT_SPEED if DEFAULT_SPEED in SPEED_LEVELS else 3
_next_slot: dict[str, float] = {site: 0.0 for site in SITES}  # next allowed page request
_ban_until: dict[str, float] = {site: 0.0 for site in SITES}  # end of the current ban (plus margin)
_ban_listeners: list[Callable[[float], None]] = []
_running = threading.Event()  # cleared = paused by the user
_running.set()
_cancel = threading.Event()   # set = the user cancelled every download
_online = threading.Event()   # cleared = internet connection lost
_online.set()
_outages = 0                  # connection losses since the start (galleries compare it)
_network_listeners: list[Callable[[bool], None]] = []
NETWORK_CHECK_INTERVAL = 10   # seconds between checks while the connection is down
# Hosts tried to tell "no internet" from "one site is down" (any answer = online)
_PROBE_HOSTS = (("1.1.1.1", 443), ("8.8.8.8", 53), ("e-hentai.org", 443), ("nhentai.net", 443))

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


def _site(site: str) -> str:
    """Known site name (anything else counts as E-Hentai)."""
    return site if site in _next_slot else SITE_EH


def wait_turn(site: str = SITE_EH) -> None:
    """Block until this thread may send the next page request to site (pacing per site)."""
    site = _site(site)
    wait_while_paused()
    with _lock:
        interval = SPEED_LEVELS[_speed][1]
        now = time.monotonic()
        slot = max(now, _next_slot[site])
        _next_slot[site] = slot + interval * random.uniform(0.8, 1.25)  # noqa: S311
    delay = slot - now
    if delay > 0:
        sleep(delay)


# ---------------------------------------------------------------- user cancel

class DownloadCancelled(BaseException):
    """Raised in a download thread after the user pressed Cancel.

    A BaseException (like KeyboardInterrupt), so the "except Exception" blocks that turn an
    error into a failed image or gallery do not catch it: the download simply stops.
    """


def request_cancel() -> None:
    """Cancel every download: waiting threads wake up and raise DownloadCancelled."""
    _cancel.set()


def clear_cancel() -> None:
    """Forget a previous cancel (call it when a new download starts)."""
    _cancel.clear()


def is_cancelled() -> bool:
    """True after the user cancelled the downloads (until clear_cancel)."""
    return _cancel.is_set()


def check_cancelled() -> None:
    """Raise DownloadCancelled when the user cancelled the downloads."""
    if _cancel.is_set():
        raise DownloadCancelled()


def sleep(seconds: float) -> None:
    """time.sleep() that a Cancel interrupts at once (raising DownloadCancelled)."""
    check_cancelled()
    if seconds > 0 and _cancel.wait(seconds):
        raise DownloadCancelled()


def _wait_event(event: threading.Event) -> None:
    """Block until event is set, raising DownloadCancelled if the user cancels meanwhile."""
    while not event.wait(0.25):
        check_cancelled()
    check_cancelled()


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
    """Block the calling thread while the downloads are paused or the internet connection is down.

    Raises DownloadCancelled when the user cancels (also while waiting).
    """
    _wait_event(_running)
    _wait_event(_online)


# ---------------------------------------------------------------- internet connection

def internet_available(timeout: float = 3.0) -> bool:
    """True when at least one well-known host answers (a single site being down is not 'offline')."""
    for host, port in _PROBE_HOSTS:
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError:
            continue
    return False


def is_online() -> bool:
    """False while the program is waiting for the internet connection to come back."""
    return _online.is_set()


def network_outages() -> int:
    """How many times the connection was lost since the start."""
    return _outages


def _notify_network(online: bool) -> None:
    with _lock:
        listeners = list(_network_listeners)
    for callback in listeners:
        try:
            callback(online)
        except Exception:
            pass


def _watch_network() -> None:
    """Thread: check the connection every NETWORK_CHECK_INTERVAL seconds until it is back."""
    while not internet_available():
        time.sleep(NETWORK_CHECK_INTERVAL)
    _online.set()
    logging.info("Internet connection is back: downloads resume.")
    _notify_network(True)


def wait_if_offline(error: Exception | None = None) -> bool:
    """After a connection error: if the internet connection is down, wait until it is back.

    Returns True when there was an outage (the caller should send the request again without
    counting it as a failed attempt), False when the connection works (a real error).
    """
    global _outages  # pylint: disable=global-statement
    if _online.is_set():
        if internet_available():
            return False
        with _lock:
            start_watch = _online.is_set()
            if start_watch:
                _online.clear()
                _outages += 1
        if start_watch:
            logging.warning("Internet connection lost%s: downloads wait until it is back (checked every "
                            f"{NETWORK_CHECK_INTERVAL} s).", f" ({error})" if error else "")
            _notify_network(False)
            threading.Thread(target=_watch_network, name="network-watch", daemon=True).start()
    _wait_event(_online)
    return True


def add_network_listener(callback: Callable[[bool], None]) -> None:
    """Call callback(False) when the connection is lost and callback(True) when it is back."""
    with _lock:
        _network_listeners.append(callback)


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


def _latest_ban_end() -> float:
    """Monotonic time when the last running ban ends (0 when no site is banned). Lock held."""
    now = time.monotonic()
    return max((until for until in _ban_until.values() if until > now), default=0.0)


def report_ban(seconds: float, source: str = SITE_EH,
               margin_range: tuple[float, float] = BAN_EXTRA_WAIT) -> None:
    """Record a ban / rate limit of the given length for one site ("E-Hentai", "nhentai", "hitomi").

    Every request to that site waits until it ends (plus a random margin); the other sites
    go on downloading.
    """
    site = _site(source)
    margin = random.uniform(*margin_range)  # noqa: S311
    until = time.monotonic() + seconds + margin
    with _lock:
        is_new = until > _ban_until[site] + 5  # several threads report the same ban at once
        _ban_until[site] = max(_ban_until[site], until)
        _next_slot[site] = max(_next_slot[site], _ban_until[site])
        listeners = list(_ban_listeners)
        current_until = _ban_until[site]
        latest = _latest_ban_end()
    if not is_new:
        return
    total = int(current_until - time.monotonic())
    what = "IP temporarily banned" if site == SITE_EH else "Too many requests: blocked for a while"
    other = " / ".join(name for name in SITES if name != site)
    logging.warning(
        f"{what} by {site}. {site} downloads paused for "
        f"{total // 60}m {total % 60:02d}s ({seconds:.0f}s asked by the site + {margin:.0f}s margin), then "
        f"they resume from where they stopped ({other} downloads are not affected). Tip: lower the speed, "
        "or switch IP with a VPN and press 'Resume now'."
    )
    for callback in listeners:
        try:
            callback(latest)
        except Exception:  # a GUI callback must never break a download thread
            pass


def banned_sites() -> list[tuple[str, float]]:
    """(site, seconds left) of every site that is paused by a ban right now."""
    now = time.monotonic()
    with _lock:
        return [(site, until - now) for site, until in _ban_until.items() if until > now]


def ban_source() -> str:
    """Site of the ban that lasts longest ("E-Hentai", "nhentai" or "hitomi"); "" when none."""
    sites = banned_sites()
    return max(sites, key=lambda item: item[1])[0] if sites else ""


def ban_remaining(site: str | None = None) -> float:
    """Seconds left before the ban of site ends; with no site, of the longest ban (0 = none)."""
    now = time.monotonic()
    if site is None:
        return max(0.0, max(_ban_until.values()) - now)
    return max(0.0, _ban_until[_site(site)] - now)


def clear_ban() -> None:
    """Forget every current ban (e.g. the user switched IP with a VPN)."""
    with _lock:
        for site in SITES:
            _ban_until[site] = 0.0
            _next_slot[site] = 0.0
        listeners = list(_ban_listeners)
    logging.info("Ban wait cancelled by the user: downloads resume now.")
    for callback in listeners:
        try:
            callback(0.0)
        except Exception:
            pass


def wait_while_banned(site: str = SITE_EH) -> None:
    """Block while site is banned (checks every second, so 'Resume now' and 'Cancel' are quick)."""
    while True:
        check_cancelled()
        remaining = ban_remaining(site)
        if remaining <= 0:
            return
        sleep(min(1.0, remaining))


def add_ban_listener(callback: Callable[[float], None]) -> None:
    """Call callback(ban_until_monotonic) when a ban starts, or callback(0.0) when cleared."""
    with _lock:
        _ban_listeners.append(callback)
