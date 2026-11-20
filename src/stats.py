"""Download statistics: since the first start (saved in stats.json) and for this session.

For each site (E-Hentai, ExHentai, nhentai) it counts the galleries completed, the images
really downloaded (pages already on disk are not counted) and the bytes received, plus the
time spent downloading (when at least one gallery is in progress) for the average speed.
The tags of every completed gallery are counted too, to show what is downloaded the most.

Every function is thread-safe: the download threads of both sites call them at once.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import deque
from pathlib import Path

from src import config, tags as tag_utils

SITES = ("e-hentai", "exhentai", "nhentai")
SITE_LABELS = {"e-hentai": "E-Hentai", "exhentai": "ExHentai", "nhentai": "nhentai"}
SPEED_WINDOW = 5      # seconds averaged for the current speed
SAVE_EVERY = 30       # seconds between saves of stats.json while downloading
TOP_TAGS_KEPT = 500   # tags remembered in stats.json (the most frequent ones)

_lock = threading.RLock()


def site_of_url(url: str) -> str:
    """'e-hentai', 'exhentai' or 'nhentai' from a gallery link."""
    host = (url or "").lower()
    if "nhentai" in host:
        return "nhentai"
    if "exhentai" in host:
        return "exhentai"
    return "e-hentai"


def _empty_sites() -> dict:
    return {site: {"galleries": 0, "images": 0, "bytes": 0} for site in SITES}


def _empty_totals() -> dict:
    return {"first_start": time.strftime("%Y-%m-%d %H:%M"), "sites": _empty_sites(),
            "busy_seconds": 0.0, "peak_speed": 0.0, "tags": {}}


class _Stats:  # pylint: disable=too-few-public-methods
    """State of the statistics (one instance per program)."""

    def __init__(self) -> None:
        self.total = _empty_totals()
        self.loaded_from = ""
        self.session = {"started": time.time(), "sites": _empty_sites(), "busy_seconds": 0.0,
                        "peak_speed": 0.0, "tags": {}}
        self.active_albums = 0
        self.busy_since: float | None = None
        self.buckets: deque = deque()   # (second, bytes) of the last SPEED_WINDOW seconds
        self.dirty = False
        self.saved_at = time.monotonic()


_state = _Stats()


# ---------------------------------------------------------------------------- file

def _path() -> Path:
    return Path(config.STATS_FILE)


def load() -> None:
    """Read stats.json (call once at start, after the data folder is set)."""
    path = _path()
    with _lock:
        _state.loaded_from = str(path)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            _state.total = _empty_totals()
            _state.dirty = True
            save()
            return
        except (OSError, ValueError) as err:
            logging.warning(f"Could not read {path.name}: {err} (statistics start from zero)")
            _state.total = _empty_totals()
            return
        total = _empty_totals()
        if isinstance(data, dict):
            total["first_start"] = str(data.get("first_start") or total["first_start"])
            for site in SITES:
                saved = (data.get("sites") or {}).get(site) or {}
                for key in ("galleries", "images", "bytes"):
                    try:
                        total["sites"][site][key] = max(0, int(saved.get(key, 0)))
                    except (TypeError, ValueError):
                        pass
            for key in ("busy_seconds", "peak_speed"):
                try:
                    total[key] = max(0.0, float(data.get(key, 0)))
                except (TypeError, ValueError):
                    pass
            if isinstance(data.get("tags"), dict):
                total["tags"] = {str(k): int(v) for k, v in data["tags"].items()
                                 if isinstance(v, int) and v > 0}
        _state.total = total


def save(force: bool = False) -> None:
    """Write stats.json if something changed (atomic replace)."""
    _ensure_loaded()
    with _lock:
        if not (_state.dirty or force):
            return
        total = dict(_state.total)
        total["busy_seconds"] = round(_busy_seconds(total["busy_seconds"]), 1)
        tags = sorted(total["tags"].items(), key=lambda item: (-item[1], item[0]))[:TOP_TAGS_KEPT]
        total["tags"] = dict(tags)
        _state.dirty = False
        _state.saved_at = time.monotonic()
    path = _path()
    tmp = path.with_name(f"{path.name}.part")
    try:
        tmp.write_text(json.dumps(total, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as err:
        logging.warning(f"Could not save {path.name}: {err}")


def _maybe_save() -> None:
    """Save now and then while downloading (lock not held)."""
    if _state.dirty and time.monotonic() - _state.saved_at >= SAVE_EVERY:
        save()


def reset() -> None:
    """Statistics → Reset: everything starts again from zero (also the session)."""
    _ensure_loaded()
    with _lock:
        _state.total = _empty_totals()
        _state.session = {"started": time.time(), "sites": _empty_sites(), "busy_seconds": 0.0,
                          "peak_speed": 0.0, "tags": {}}
        if _state.busy_since is not None:
            _state.busy_since = time.monotonic()
        _state.dirty = True
    save()


# ---------------------------------------------------------------------------- counting

def _site(site: str) -> str:
    _ensure_loaded()
    return site if site in SITES else site_of_url(site)


def _ensure_loaded() -> None:
    """Read stats.json the first time the statistics are used (never overwrite it unread)."""
    if not _state.loaded_from:
        load()


def add_bytes(site: str, count: int) -> None:
    """Bytes of an image received from the image server."""
    if count <= 0:
        return
    site = _site(site)
    now = time.monotonic()
    second = int(now)
    with _lock:
        _state.total["sites"][site]["bytes"] += count
        _state.session["sites"][site]["bytes"] += count
        if _state.buckets and _state.buckets[-1][0] == second:
            _state.buckets[-1][1] += count
        else:
            _state.buckets.append([second, count])
            _update_peak(now)
        _state.dirty = True


def add_image(site: str) -> None:
    """One image was really downloaded and saved."""
    site = _site(site)
    with _lock:
        _state.total["sites"][site]["images"] += 1
        _state.session["sites"][site]["images"] += 1
        _state.dirty = True
    _maybe_save()


def add_gallery(site: str, gallery_tags: list[str] | None) -> None:
    """A gallery was downloaded completely (not when it was already on disk)."""
    site = _site(site)
    with _lock:
        _state.total["sites"][site]["galleries"] += 1
        _state.session["sites"][site]["galleries"] += 1
        for tag in gallery_tags or []:
            if tag_utils.is_content_tag(tag):
                name = tag_utils.split(tag)[1]
                for bucket in (_state.total["tags"], _state.session["tags"]):
                    bucket[name] = bucket.get(name, 0) + 1
        _state.dirty = True
    save()


def album_started() -> None:
    """A gallery started downloading (the busy time runs while at least one is in progress)."""
    with _lock:
        if _state.active_albums == 0:
            _state.busy_since = time.monotonic()
        _state.active_albums += 1


def album_finished() -> None:
    """A gallery is over (whatever the result)."""
    with _lock:
        if _state.active_albums <= 0:
            return
        _state.active_albums -= 1
        if _state.active_albums == 0 and _state.busy_since is not None:
            elapsed = time.monotonic() - _state.busy_since
            _state.total["busy_seconds"] += elapsed
            _state.session["busy_seconds"] += elapsed
            _state.busy_since = None
            _state.dirty = True
    save()


# ---------------------------------------------------------------------------- reading

def _busy_seconds(base: float) -> float:
    """base plus the time of the downloads in progress (lock held)."""
    if _state.busy_since is None:
        return base
    return base + (time.monotonic() - _state.busy_since)


def _current_speed(now: float) -> float:
    """Bytes per second over the last SPEED_WINDOW seconds (lock held)."""
    second = int(now)
    while _state.buckets and _state.buckets[0][0] < second - SPEED_WINDOW:
        _state.buckets.popleft()
    # the current second is still filling up: only the complete ones are counted
    received = sum(count for start, count in _state.buckets if start < second)
    return received / SPEED_WINDOW


def _update_peak(now: float) -> None:
    """Remember the highest speed seen (lock held)."""
    speed = _current_speed(now)
    if speed > _state.session["peak_speed"]:
        _state.session["peak_speed"] = speed
    if speed > _state.total["peak_speed"]:
        _state.total["peak_speed"] = speed


def current_speed() -> float:
    """Download speed now, in bytes per second (0 when idle)."""
    with _lock:
        if not _state.active_albums and not _state.buckets:
            return 0.0
        return _current_speed(time.monotonic())


def _summary(block: dict, top: int) -> dict:
    """Totals of one block (total or session), for the windows (lock held)."""
    busy = _busy_seconds(block["busy_seconds"])
    sites = {site: dict(block["sites"][site]) for site in SITES}
    all_bytes = sum(item["bytes"] for item in sites.values())
    tags = sorted(block["tags"].items(), key=lambda item: (-item[1], item[0]))[:top]
    return {
        "sites": sites,
        "galleries": sum(item["galleries"] for item in sites.values()),
        "images": sum(item["images"] for item in sites.values()),
        "bytes": all_bytes,
        "busy_seconds": busy,
        "average_speed": all_bytes / busy if busy >= 1 else 0.0,
        "peak_speed": block["peak_speed"],
        "top_tags": [{"tag": name, "count": count} for name, count in tags],
    }


def snapshot(top: int = 15) -> dict:
    """Everything the Statistics tab shows."""
    _ensure_loaded()
    with _lock:
        now = time.monotonic()
        speed = _current_speed(now) if (_state.active_albums or _state.buckets) else 0.0
        return {
            "first_start": _state.total["first_start"],
            "session_started": time.strftime("%Y-%m-%d %H:%M", time.localtime(_state.session["started"])),
            "downloading": _state.active_albums,
            "current_speed": speed,
            "total": _summary(_state.total, top),
            "session": _summary(_state.session, top),
            "site_labels": dict(SITE_LABELS),
        }


# ---------------------------------------------------------------------------- formatting

def fmt_bytes(count: float) -> str:
    """1536 -> '1.5 KB'."""
    value = float(count or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


def fmt_speed(bytes_per_second: float) -> str:
    """Speed as '1.2 MB/s' ('—' when 0)."""
    return f"{fmt_bytes(bytes_per_second)}/s" if bytes_per_second > 0 else "—"


def fmt_duration(seconds: float) -> str:
    """3725 -> '1h 02m'."""
    seconds = int(seconds or 0)
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"
