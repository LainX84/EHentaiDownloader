"""Download engine of the web interface: the same work as the desktop window, without Tkinter.

The browser only shows and asks: every list, download and setting lives here, in the
server, so the downloads go on with the browser closed and every open page sees the
same state.

What the desktop window does with widgets is done here on plain data:
  * the batch list (URLs.txt) is a list of lines with a state (queued / active / done / fail);
  * the worker threads push their messages (logs, progress bars) onto a queue, exactly as in
    the desktop version (GUILiveManager); a dispatcher thread applies them in order;
  * the browser asks for a snapshot of the state about once a second (/api/state).
"""

# pylint: disable=too-many-lines,too-many-instance-attributes,too-many-public-methods
from __future__ import annotations

import datetime
import json
import logging
import os
import queue
import threading
import time
from collections import deque
from pathlib import Path
from typing import Callable

from src import config, image_formats, stats, tags as tag_utils, throttle
from src.downloader.cbz_archive import set_comic_info
from src.file_utils import append_session_banner, append_session_log, read_file, write_file
from src.gui.live_manager import GUILiveManager, QueueLogHandler

LOG_LINES_KEPT = 3000  # log lines kept in memory for the browser (the whole log is in session.log)

FORMAT_OPTIONS = image_formats.LABELS  # default at the first start: WebP (small files, keeps animations)

LANE_LABELS = {throttle.SITE_EH: "E-Hentai/ExHentai", throttle.SITE_NH: "nhentai", throttle.SITE_HI: "hitomi"}
LANE_IDLE = "Idle"
THEMES = ("auto", "light", "dark")

BOOL_SETTINGS = ("auto_rename", "cbz_archive", "comic_info", "check_updates", "tag_folders")

COUNTER_RESET_HOUR = 6   # the "today" image counter starts again every day at 06:00
COUNTER_SAVE_EVERY = 30  # seconds between saves of the counter while images are downloading


class EngineError(Exception):
    """A request that can't be done now (shown to the user as is)."""


def ts() -> str:
    """Current time as HH:MM:SS (log lines)."""
    return datetime.datetime.now().strftime("%H:%M:%S")


def counter_day() -> str:
    """Day of the image counter: it changes at COUNTER_RESET_HOUR, not at midnight."""
    return (datetime.datetime.now() - datetime.timedelta(hours=COUNTER_RESET_HOUR)).date().isoformat()


def site_lane(url: str) -> str:
    """Download lane of a link: "nhentai" for nhentai.net, "hitomi" for hitomi.la, "E-Hentai" for the rest."""
    from src import hitomi, nhentai  # pylint: disable=import-outside-toplevel
    if nhentai.is_nhentai_url(url or ""):
        return throttle.SITE_NH
    return throttle.SITE_HI if hitomi.is_hitomi_url(url or "") else throttle.SITE_EH


def log_tag(event: str, details: str) -> str:
    """Colour of a log line, from its severity keywords (same rules as the desktop window)."""
    low = (event + " " + details).lower()
    if any(w in low for w in ("error", "fail", "exception", "critical")):
        return "error"
    if any(w in low for w in ("warn", "notice", "skip", "already present", "pause", "rate limit",
                              "waiting", "limit", "cancel", "stop")):
        return "warning"
    if any(w in low for w in ("success", "complete", "downloaded", "renamed", "created", "ok")):
        return "ok"
    return "info"


def line_view(text: str) -> dict:
    """What the page shows of a list line, in columns: status, site, favorite tag, images, title.

    {"status": "GOOD"/"BAD"/"", "site": "EHENTAI"/..., "badge": str, "color": "#RRGGBB"/"",
    "ink": text colour on it, "images": int, "title": str}. The link stays hidden. A line the
    program knows nothing about yet (a bare link) has status "" and its text as title.
    """
    from src.cli.downloader import parse_table_line, site_name  # pylint: disable=import-outside-toplevel
    columns = parse_table_line(text)
    if columns is None:
        stripped = text.strip()
        is_link = bool(stripped) and not stripped.startswith("#")
        return {"status": "", "site": site_name(stripped) if is_link and "://" in stripped else "",
                "badge": "", "color": "", "ink": "", "images": 0, "title": stripped}
    title = columns["title"] or (f"{columns['url']} — {columns['problem']}" if columns["problem"] else columns["url"])
    color = tag_utils.badge_color(columns["badge"]) if columns["badge"] else ""
    return {"status": columns["status"], "site": columns["site"], "badge": columns["badge"], "color": color,
            "ink": tag_utils.text_color(color) if color else "", "images": columns["images"], "title": title}


class BatchProgress:
    """Progress bars of a batch / retry where albums of different sites download at once.

    Bar 1 (total): albums finished plus the share already done of those in progress.
    Site bars: images of the album in progress on that site.
    """

    def __init__(self, out: queue.Queue, total_fn: Callable[[], int]) -> None:
        """total_fn() returns the number of albums of the batch (it grows while links are added)."""
        self._q = out
        self._total_fn = total_fn
        self._lock = threading.Lock()
        self._albums: dict[int, tuple[int, int]] = {}
        self._finished = 0

    def album_update(self, owner, done: int, total: int) -> None:
        """An album in progress has done of total images."""
        total = max(1, total)
        with self._lock:
            self._albums[id(owner)] = (done, total)
            self._emit_total()
            lane = getattr(owner, "lane", None)
            if lane:
                frac = min(1.0, done / total)
                self._q.put(("LANE_BAR", lane, frac, f"{done}/{total} ({frac * 100:.0f}%)"))

    def album_finished(self, owner) -> None:
        """An album is over (downloaded, failed or skipped); owner may be None."""
        with self._lock:
            if owner is not None:
                self._albums.pop(id(owner), None)
            self._finished += 1
            self._emit_total()

    def _emit_total(self) -> None:
        """Send the total bar (lock held)."""
        total = max(1, self._total_fn(), self._finished + len(self._albums))
        overall = min(1.0, (self._finished + sum(d / t for d, t in self._albums.values())) / total)
        self._q.put(("UPDATE_BAR1", overall, f"{self._finished}/{total} ({overall * 100:.0f}%)"))


class Engine:
    """Everything the desktop window keeps in its widgets, kept as data for the browser."""

    def __init__(self, data_dir: Path, config_dir: Path) -> None:
        """Load settings and lists, install the log handler and start the dispatcher thread."""
        self.data_dir = data_dir
        self.config_dir = config_dir
        self._lock = threading.RLock()
        self._q: queue.Queue = queue.Queue()

        # Log shown in the browser (newest last); seq lets each page ask only for new lines
        self._log: deque = deque(maxlen=LOG_LINES_KEPT)
        self._log_seq = 0
        self._log_epoch = int(time.time())  # changes on "Clear": pages reload the whole log

        self.settings = self._load_settings()
        try:
            throttle.set_speed(int(self.settings.get("speed", config.DEFAULT_SPEED)))
        except (TypeError, ValueError):
            throttle.set_speed(config.DEFAULT_SPEED)
        set_comic_info(bool(self.settings.get("comic_info", config.CBZ_COMIC_INFO)))
        tag_utils.set_favorites(self.settings.get("favorite_tags") or [])
        tag_utils.set_folders_enabled(bool(self.settings.get("tag_folders", False)))
        stats.load()

        # Running task: None, "single", "batch", "verify", "retry"
        self.mode: str | None = None
        self.status = "Ready."
        self.bar1 = {"frac": 0.0, "text": "0%", "label": "Total:"}
        self.lanes = {lane: {"frac": None, "text": LANE_IDLE} for lane in throttle.SITES}
        self.update_info: dict | None = None
        # Images downloaded "today" (06:00 → 06:00), kept in settings.json across restarts
        saved = self.settings.get("image_counter") if isinstance(self.settings.get("image_counter"), dict) else {}
        self._counter_day = str(saved.get("day", counter_day()))
        self.session_images = int(saved.get("count", 0) or 0)
        self._counter_saved_at = time.monotonic()
        self._roll_counter()
        self._run_seq = 0  # +1 at every start: a late bar reset never touches a newer run
        self._stop_requested = False     # Stop: finish the galleries in progress, start no new one
        self._cancel_requested = False   # Cancel: stop now, then empty temp/ and both lists

        # Batch list (URLs.txt): one dict per line, with a stable id
        self._lines: list[dict] = []
        self._line_seq = 0
        self._lines_rev = 0
        self._estimate_cache: tuple[int, str] = (-1, "")
        self.sort_offer = False      # after Verify Links: offer "fewer images first"
        self._batch: dict | None = None
        self._pending_removals: dict[int, threading.Timer] = {}  # task id -> timer of a green line

        # Retry list (failed_downloads.txt)
        self._retry: list[dict] = []
        self._retry_rev = 0

        self._install_logging()
        self._load_urls_txt()
        self.reload_retry_list()
        throttle.add_ban_listener(self._on_ban)
        throttle.add_network_listener(lambda online: self._set_status(
            "▶ Internet connection back: downloads resumed." if online else
            f"📡 Internet connection lost: downloads wait until it is back (checked every "
            f"{throttle.NETWORK_CHECK_INTERVAL} s)."))
        threading.Thread(target=self._dispatch, name="dispatcher", daemon=True).start()
        append_session_banner("WEB APPLICATION START")
        self.log("Ready", f"EHentai Downloader {config.APP_VERSION} (web). Data folder: {self.data_dir}")
        from src.general_utils import has_account_cookies  # pylint: disable=import-outside-toplevel
        if not has_account_cookies():
            self.log("WARNING", "No ExHentai account cookies in cookies.json: ExHentai links and galleries "
                     "removed from E-Hentai can't be downloaded. Add them from the Account window.")
        if self.settings.get("check_updates", True):
            threading.Thread(target=self._startup_update_check, daemon=True).start()

    # ------------------------------------------------------------------ settings

    def _load_settings(self) -> dict:
        """settings.json of the last run (empty dict if missing or unreadable)."""
        try:
            data = json.loads(Path(config.SETTINGS_FILE).read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_settings(self) -> None:
        """Write settings.json (atomic replace)."""
        path = Path(config.SETTINGS_FILE)
        tmp = path.with_name(f"{path.name}.part")
        try:
            tmp.write_text(json.dumps(self.settings, indent=2), encoding="utf-8")
            os.replace(tmp, path)
        except OSError as err:
            logging.warning(f"Could not save {path.name}: {err}")

    def public_settings(self) -> dict:
        """Settings shown in the page."""
        fmt = self.settings.get("image_format")
        theme = self.settings.get("theme")
        return {
            "image_format": image_formats.normalize_label(fmt) if fmt else image_formats.DEFAULT_LABEL,
            "auto_rename": bool(self.settings.get("auto_rename", config.RENAME_SEQUENTIAL)),
            "cbz_archive": bool(self.settings.get("cbz_archive", config.CREATE_CBZ)),
            "comic_info": bool(self.settings.get("comic_info", config.CBZ_COMIC_INFO)),
            "check_updates": bool(self.settings.get("check_updates", True)),
            "tag_folders": bool(self.settings.get("tag_folders", False)),
            "favorite_tags": tag_utils.get_favorites(),
            "favorite_tags_text": tag_utils.favorites_text(tag_utils.get_favorites()),
            "speed": throttle.get_speed(),
            "theme": theme if theme in THEMES else "auto",
            # formats whose notice window is snoozed ("Don't show this again for 30 days")
            "format_notices_hidden": [label for label, name in image_formats.FORMATS
                                      if image_formats.notice_hidden(self.settings, name)],
        }

    def change_settings(self, changes: dict) -> dict:
        """Apply the settings changed in the page and log them like the desktop window."""
        with self._lock:
            current = self.public_settings()
            if "image_format" in changes:
                if changes["image_format"] not in FORMAT_OPTIONS:
                    raise EngineError("Unknown image format.")
                self.settings["image_format"] = changes["image_format"]
            if "theme" in changes:
                if changes["theme"] not in THEMES:
                    raise EngineError("Unknown theme.")
                self.settings["theme"] = changes["theme"]
            for key in BOOL_SETTINGS:
                if key in changes:
                    if key == "tag_folders" and self.mode:
                        raise EngineError("A download is in progress: change the folders when it is over.")
                    self.settings[key] = bool(changes[key])
            if "favorite_tags" in changes:
                if self.mode:
                    raise EngineError("A download is in progress: change the favorite tags when it is over.")
                favorites = tag_utils.clean_favorites(changes["favorite_tags"])
                self.settings["favorite_tags"] = favorites
                tag_utils.set_favorites(favorites)
            if "favorite_tags_text" in changes:
                if self.mode:
                    raise EngineError("A download is in progress: change the favorite tags when it is over.")
                favorites = tag_utils.parse_favorites(str(changes["favorite_tags_text"] or ""))
                self.settings["favorite_tags"] = favorites
                tag_utils.set_favorites(favorites)
            tag_utils.set_folders_enabled(bool(self.settings.get("tag_folders", False)))
            if "speed" in changes:
                level = int(changes["speed"])
                if level not in throttle.SPEED_LEVELS:
                    raise EngineError("Unknown speed.")
                self.settings["speed"] = level
                if level != throttle.get_speed():
                    throttle.set_speed(level)
                    if self.mode:
                        self.log("Speed", f"Speed set to {throttle.speed_name(level)}: request pace changes "
                                          "now, parallel downloads from the next album.")
            self._save_settings()
            new = self.public_settings()
        if new["image_format"] != current["image_format"]:
            line = image_formats.log_line(self.selected_format())
            if line:
                self.log("Image Format notice", line)
        if new["auto_rename"] != current["auto_rename"]:
            self.log("Settings", "Auto Rename ON: images renamed 01…N when an album is complete."
                     if new["auto_rename"] else "Auto Rename OFF: images keep their download names.")
        if new["cbz_archive"] != current["cbz_archive"]:
            self.log("Settings", "CBZ archive ON: each completed album is saved as a .cbz file."
                     if new["cbz_archive"] else "CBZ archive OFF: albums are saved as folders of images.")
        if new["favorite_tags"] != current["favorite_tags"]:
            names = ", ".join(tag_utils.badge_text(fav["label"]) for fav in new["favorite_tags"]) or "none"
            self.log("Settings", f"Favorite tags: {names}.")
            self._call(self._refresh_badges)
        if new["tag_folders"] != current["tag_folders"]:
            self.log("Settings", "Folders by favorite tag ON: a gallery with a favorite tag goes into "
                                 "<site>/<label>/ (for example E-Hentai/Netorare/)."
                     if new["tag_folders"] else
                     "Folders by favorite tag OFF: every gallery goes into E-Hentai/, nhentai/ or hitomi/.")
        if new["comic_info"] != current["comic_info"]:
            set_comic_info(new["comic_info"])
            self.log("Settings", "ComicInfo.xml ON: new CBZ archives include title, tags and source link."
                     if new["comic_info"] else "ComicInfo.xml OFF: new CBZ archives hold only the images.")
        return new

    def selected_format(self) -> str:
        """Internal format name of the chosen image format (original / webp / jpg / jxl / png / avif)."""
        return image_formats.name_from_label(self.public_settings()["image_format"])

    def _options(self) -> tuple[str, bool, bool]:
        """(image format, auto rename, cbz) for a new download."""
        settings = self.public_settings()
        return self.selected_format(), settings["auto_rename"], settings["cbz_archive"]

    # ------------------------------------------------------------------ disclaimer

    def disclaimer_needed(self) -> bool:
        """True when the start-up notice must be shown (not accepted, or snooze expired)."""
        try:
            return float(self.settings.get("disclaimer_hidden_until", 0)) < time.time()
        except (TypeError, ValueError):
            return True

    def snooze_format_notice(self, label: str) -> dict:
        """Hide the notice window of an image format for FORMAT_NOTICE_SNOOZE_DAYS days."""
        if label not in FORMAT_OPTIONS:
            raise EngineError("Unknown image format.")
        with self._lock:
            image_formats.hide_notice(self.settings, image_formats.name_from_label(label))
            self._save_settings()
            return self.public_settings()

    def accept_disclaimer(self, snooze: bool) -> None:
        """The notice was accepted; with snooze it is hidden for DISCLAIMER_SNOOZE_DAYS days."""
        with self._lock:
            # Accepted without snooze: shown again at the next visit only after a day
            days = config.DISCLAIMER_SNOOZE_DAYS if snooze else 1
            self.settings["disclaimer_hidden_until"] = time.time() + days * 86400
            self.settings["disclaimer_accepted"] = time.strftime("%Y-%m-%d %H:%M")
            self._save_settings()

    # ------------------------------------------------------------------ log

    def _install_logging(self) -> None:
        """Send Python logging (warnings of the download code) to the log, without library noise."""
        for noisy in ("urllib3", "urllib3.connectionpool", "requests", "PIL", "PIL.Image",
                      "PIL.PngImagePlugin", "multipart", "python_multipart"):
            logging.getLogger(noisy).setLevel(logging.WARNING)
        handler = QueueLogHandler(self._q)
        handler.setFormatter(logging.Formatter("%(message)s"))
        root_logger = logging.getLogger()
        root_logger.addHandler(handler)
        root_logger.setLevel(logging.INFO)

    def log(self, event: str, details: str, tag: str | None = None) -> None:
        """Add a log line (through the queue, so it keeps its place among the worker messages)."""
        self._q.put(("LOG", ts(), event, details, tag))

    def _append_log(self, stamp: str, event: str, details: str, tag: str | None = None) -> None:
        """Dispatcher: store one log line and write it to session.log."""
        if tag is None or tag in ("info", "warning", "error", "ok"):
            append_session_log(event, details)
        with self._lock:
            self._log_seq += 1
            self._log.append({"seq": self._log_seq, "ts": stamp, "event": event, "details": details,
                              "tag": tag or log_tag(event, details)})

    def clear_log(self) -> None:
        """'Clear' button of the log (session.log is kept)."""
        with self._lock:
            self._log.clear()
            self._log_epoch += 1

    def _banner(self, text: str, tag: str) -> None:
        """A coloured summary line (no event name), like the banners of the desktop window."""
        self._q.put(("LOG", ts(), "", text, tag))

    # ------------------------------------------------------------------ dispatcher

    def _dispatch(self) -> None:
        """Apply the messages of the worker threads in order (the desktop window's _poll_queue)."""
        while True:
            msg = self._q.get()
            try:
                self._apply(msg)
            except Exception as exc:  # pylint: disable=broad-except
                self._append_log(ts(), "ERROR", f"Internal error: {exc}")

    def _apply(self, msg: tuple) -> None:
        """Apply one message."""
        kind = msg[0]
        if kind == "LOG":
            self._append_log(msg[1], msg[2], msg[3], msg[4] if len(msg) > 4 else None)
            return
        with self._lock:
            if kind == "UPDATE_BAR1":
                self.bar1["frac"], self.bar1["text"] = msg[1], msg[2]
            elif kind == "LANE_BAR":
                lane = msg[1]
                if lane in self.lanes:
                    self.lanes[lane] = {"frac": msg[2], "text": msg[3]}
            elif kind == "IMAGE_SAVED":
                self._roll_counter()
                self.session_images += 1
                if time.monotonic() - self._counter_saved_at >= COUNTER_SAVE_EVERY:
                    self._save_counter()
            elif kind == "STATUS":
                self.status = msg[1]
            elif kind == "CALL":
                msg[1](*msg[2:])

    def _roll_counter(self) -> None:
        """Start the image counter again when a new day (from 06:00) has begun (lock held)."""
        today = counter_day()
        if self._counter_day != today:
            self._counter_day = today
            self.session_images = 0
            self._save_counter()

    def _save_counter(self) -> None:
        """Write the image counter to settings.json (lock held)."""
        self.settings["image_counter"] = {"day": self._counter_day, "count": self.session_images}
        self._counter_saved_at = time.monotonic()
        self._save_settings()

    def _call(self, func: Callable, *args) -> None:
        """Run func(*args) in the dispatcher, after the messages already queued."""
        self._q.put(("CALL", func, *args))

    def _set_status(self, text: str) -> None:
        """Status line (bottom of the page)."""
        self._q.put(("STATUS", text))

    # ------------------------------------------------------------------ ban / pause

    def _on_ban(self, until: float) -> None:
        """A site banned the IP (until > 0) or the ban was cleared: the page reads banned_sites()."""
        if until <= 0 and self.mode:
            self._set_status("▶ Ban wait over: downloads resume.")

    def toggle_pause(self) -> bool:
        """Pause / resume every download; on pause the batch list is saved. Returns paused."""
        with self._lock:
            if not self.mode:
                raise EngineError("Nothing is downloading.")
            if not throttle.is_paused():
                throttle.set_paused(True)
                self._save_urls_txt()
                append_session_banner("DOWNLOAD PAUSED BY THE USER")
                self._set_status("⏸ Paused: list and partial album saved. Press Resume.")
                self.log("Pause", "Downloads paused (images already being received finish first). URLs.txt "
                                  "is saved and the albums in progress stay in temp/: if the container "
                                  "restarts, the next Start Batch resumes from them skipping the pages "
                                  "already on disk.")
                return True
            throttle.set_paused(False)
            append_session_banner("DOWNLOAD RESUMED BY THE USER")
            self._set_status("▶ Downloads resumed.")
            self.log("Resume", "Downloads resumed.")
            return False

    def request_stop(self) -> None:
        """'Stop': the galleries already downloading finish, then nothing new starts.

        The links not started yet stay in the batch list (and in Retry Failed for a retry).
        """
        with self._lock:
            if not self.mode:
                raise EngineError("Nothing is downloading.")
            if self._stop_requested or self._cancel_requested:
                return
            mode = self.mode
            if mode == "verify":
                what = "the link being checked finishes, then the check stops."
            elif mode == "single":
                what = "the gallery in progress finishes, then the download ends."
            else:
                what = ("the galleries in progress finish, then the download ends: the ones not started yet stay in "
                        + ("the retry queue." if mode == "retry" else "the list for the next Start Batch."))
            # Status and log first: the run may end (with its own summary) right after
            self.status = "⏹ Stopping: " + what
            append_session_banner("STOP REQUESTED BY THE USER")
            self.log("Stop", "Stop requested: " + what)
            self._stop_requested = True
            if self._batch:
                self._batch["accepting"] = False   # links added from now on wait for the next batch
                self._touch_lines()
            if throttle.is_paused():  # a paused gallery could never finish: resume it
                throttle.set_paused(False)

    def request_cancel(self) -> None:
        """'Cancel' (confirmed in the page): stop every download now, then empty temp/ and both lists."""
        with self._lock:
            if not self.mode:
                raise EngineError("Nothing is downloading.")
            if self.mode == "verify":
                raise EngineError("The link check can only be stopped (Stop).")
            if self._cancel_requested:
                return
            self._cancel_requested = True
            # Status first: the workers may end (and the cleanup set its own status) right after
            self.status = "✖ Cancelling: waiting for the images being received to end…"
            append_session_banner("CANCEL REQUESTED BY THE USER")
            self.log("Cancel", "Cancel requested: the downloads stop as soon as the images being received end.")
            throttle.request_cancel()   # waiting threads wake up and stop at once
            batch = self._batch
            if batch:
                batch["accepting"] = False
                for lane_queue in batch["queues"].values():
                    for task in lane_queue:
                        batch["cancelled"].add(task["id"])
                    lane_queue.clear()

    def _finish_cancel(self) -> None:
        """Dispatcher, after a cancelled run ended: empty temp/, the batch list and Retry Failed (lock held)."""
        from src.cli.downloader import save_failed_urls  # pylint: disable=import-outside-toplevel
        from src.file_utils import clear_folder  # pylint: disable=import-outside-toplevel
        throttle.clear_cancel()
        for timer in self._pending_removals.values():
            timer.cancel()
        self._pending_removals = {}
        self._lines = []
        self.sort_offer = False
        self._touch_lines()
        self._save_urls_txt()
        save_failed_urls([])
        removed, errors = clear_folder(config.TEMP_FOLDER)
        self.bar1 = {"frac": 0.0, "text": "0%", "label": "Total:"}
        append_session_banner("DOWNLOAD CANCELLED BY THE USER")
        details = (f"Downloads cancelled: temp emptied ({removed} item(s) deleted), the batch list and "
                   "the retry queue are empty now.")
        if errors:
            details += f" {len(errors)} item(s) could not be deleted (in use?): {', '.join(errors[:3])}"
        self.log("Cancelled", details)
        self.status = "✖ Downloads cancelled: temp, batch list and Retry Failed emptied."

    @staticmethod
    def resume_after_ban() -> None:
        """'Resume now': stop waiting (e.g. after switching IP). If the ban is still on, it comes back."""
        throttle.clear_ban()

    # ------------------------------------------------------------------ batch list (URLs.txt)

    def _new_line(self, text: str) -> dict:
        """A batch list line with a new id."""
        self._line_seq += 1
        return {"id": self._line_seq, "text": text, "state": "", "task": None}

    def _touch_lines(self) -> None:
        """The batch list changed: pages must reload it."""
        self._lines_rev += 1

    def _load_urls_txt(self) -> None:
        """Read URLs.txt into the batch list."""
        path = Path(config.URLS_FILE)
        if not path.exists():
            write_file(config.URLS_FILE)
        with self._lock:
            from src.cli.downloader import normalize_line  # pylint: disable=import-outside-toplevel
            self._lines = [self._new_line(normalize_line(line.rstrip("\r\n"))) for line in read_file(config.URLS_FILE)]
            while self._lines and not self._lines[-1]["text"].strip():
                self._lines.pop()
            self._touch_lines()

    def reload_urls_txt(self) -> None:
        """'Reload' button."""
        with self._lock:
            self._guard_list_editable(running_ok=False)
            self._load_urls_txt()

    def _save_urls_txt(self) -> None:
        """Write the batch list to URLs.txt (lock held)."""
        texts = [line["text"] for line in self._lines]
        content = "\n".join(texts)
        write_file(config.URLS_FILE, content + "\n" if content else "")

    def _guard_list_editable(self, running_ok: bool = True) -> None:
        """The list can't be changed while a single / verify / retry runs (as in the desktop window)."""
        if self.mode and not (running_ok and self.mode == "batch"):
            raise EngineError("A download is in progress: wait for it to finish first.")

    def _entries(self) -> list[tuple[dict, str, str]]:
        """(line, url, name) of every link line of the batch list (lock held)."""
        from src.cli.downloader import parse_url_line, remember_gallery_name  # pylint: disable=import-outside-toplevel
        entries = []
        for line in self._lines:
            url, name = parse_url_line(line["text"])
            if url:
                remember_gallery_name(url, name)
                entries.append((line, url, name))
        return entries

    def _line_no(self, line: dict) -> int:
        """1-based position of a line in the list (lock held)."""
        return self._lines.index(line) + 1

    def add_links(self, text: str, source: str = "Paste") -> dict:
        """Append the gallery links found in any text (only those not in the list yet)."""
        from src.cli.downloader import gallery_key  # pylint: disable=import-outside-toplevel
        from src.general_utils import extract_gallery_links  # pylint: disable=import-outside-toplevel
        links = extract_gallery_links(text)
        with self._lock:
            self._guard_list_editable()
            listed = {gallery_key(url) for _, url, _ in self._entries()}
            new_links = []
            for link in links:
                key = gallery_key(link)
                if key not in listed:
                    listed.add(key)
                    new_links.append(link)
            self._lines.extend(self._new_line(link) for link in new_links)
            if new_links:
                self._touch_lines()
                self._save_urls_txt()
                if self._batch and self._batch["accepting"]:
                    self._scan(live=True)  # the running batch takes them at once
        skipped = len(links) - len(new_links)
        note = f" ({skipped} already in the list)" if skipped else ""
        if links:
            self.log(source, f"Added {len(new_links)} link(s) to the batch list{note}.")
            self._set_status(f"{source}: {len(new_links)} link(s) added{note}.")
        return {"found": len(links), "added": len(new_links), "skipped": skipped}

    def set_list_text(self, text: str) -> None:
        """Text editor of the list: replace the whole list (not while a download runs)."""
        with self._lock:
            self._guard_list_editable(running_ok=False)
            self._flush_pending_removals()
            lines = text.replace("\r\n", "\n").split("\n")
            while lines and not lines[-1].strip():
                lines.pop()
            self._lines = [self._new_line(line.rstrip()) for line in lines]
            self.sort_offer = False
            self._touch_lines()
            self._save_urls_txt()

    def delete_line(self, line_id: int) -> None:
        """✕ of a line. While a batch runs, a queued line is skipped; an active one can't go."""
        with self._lock:
            self._guard_list_editable()
            line = next((item for item in self._lines if item["id"] == line_id), None)
            if line is None:
                return
            if self._batch and line["task"]:
                state = self._batch["task_state"].get(line["task"])
                if state == "active":
                    raise EngineError("This gallery is downloading now: it can't be removed.")
                if state == "queued":
                    self._batch["cancelled"].add(line["task"])
                timer = self._pending_removals.pop(line["task"], None)
                if timer:
                    timer.cancel()
            self._lines.remove(line)
            self._touch_lines()
            self._save_urls_txt()

    def move_line(self, line_id: int, to_index: int) -> None:
        """▲ ▼ / drag and drop: put a line at another place of the list (not one already started)."""
        with self._lock:
            self._guard_list_editable()
            line = self._line_by_id(line_id)
            if line is None:
                return
            if self._batch and line["task"]:
                state = self._batch["task_state"].get(line["task"])
                if state in ("active", "done"):
                    raise EngineError("This gallery has already started: only lines still waiting can be moved.")
            to_index = max(0, min(int(to_index), len(self._lines) - 1))
            if self._lines.index(line) == to_index:
                return
            self._lines.remove(line)
            self._lines.insert(to_index, line)
            self._touch_lines()
            self._save_urls_txt()

    def _refresh_badges(self) -> None:
        """Dispatcher: favorite tags changed: rewrite every list line (tag, column width)."""
        from src.cli.downloader import normalize_line  # pylint: disable=import-outside-toplevel
        with self._lock:
            changed = False
            for line in self._lines:
                text = normalize_line(line["text"])
                if text != line["text"]:
                    line["text"], changed = text, True
            if changed:
                self._touch_lines()
                self._save_urls_txt()
        self.reload_retry_list()

    def clear_list(self) -> None:
        """'Clear All' (after the confirmation in the page)."""
        with self._lock:
            self._guard_list_editable(running_ok=False)
            self._flush_pending_removals()
            self._lines = []
            self.sort_offer = False
            self._touch_lines()
            self._save_urls_txt()
        self._set_status("URLs.txt cleared.")

    def find_problems(self) -> dict:
        """Duplicates and malformed links of the list, for the confirmations before a start."""
        from src.cli.downloader import find_duplicates, gallery_name  # pylint: disable=import-outside-toplevel
        from src.general_utils import validate_gallery_url  # pylint: disable=import-outside-toplevel
        with self._lock:
            entries = self._entries()
            pairs = find_duplicates([url for _, url, _ in entries])
            duplicates = []
            for dup_idx, kept_idx in pairs:
                dup_line, dup_url, _ = entries[dup_idx]
                kept_line, kept_url, _ = entries[kept_idx]
                duplicates.append({
                    "line": self._line_no(dup_line), "kept": self._line_no(kept_line),
                    "name": gallery_name(kept_url) or gallery_name(dup_url),
                })
            invalid = [{"line": self._line_no(line), "error": err}
                       for line, url, _ in entries if (err := validate_gallery_url(url))]
            for line, url, _ in entries:
                if validate_gallery_url(url):
                    line["state"] = "fail"
            if invalid:
                self._touch_lines()
            return {"links": len(entries), "duplicates": duplicates, "invalid": invalid}

    def remove_duplicates(self) -> int:
        """Keep one link per gallery (a well-formed copy wins). Returns the lines removed."""
        from src.cli.downloader import find_duplicates, format_url_line  # pylint: disable=import-outside-toplevel
        with self._lock:
            self._guard_list_editable(running_ok=False)
            entries = self._entries()
            pairs = find_duplicates([url for _, url, _ in entries])
            if not pairs:
                return 0
            for dup_idx, kept_idx in pairs:  # keep the gallery name of a removed copy
                kept_line, kept_url, kept_name = entries[kept_idx]
                dup_name = entries[dup_idx][2]
                if dup_name and not kept_name:
                    kept_line["text"] = format_url_line(kept_url, dup_name)
            remove = {id(entries[dup_idx][0]) for dup_idx, _ in pairs}
            self._lines = [line for line in self._lines if id(line) not in remove]
            self._touch_lines()
            self._save_urls_txt()
        self.log("Duplicates", f"Removed {len(pairs)} duplicate link(s) from the list.")
        return len(pairs)

    def sort_by_size(self) -> bool:
        """Sort the list by site (E-Hentai / ExHentai, then nhentai, then hitomi), then size (fewer images first)."""
        with self._lock:
            self._guard_list_editable(running_ok=False)
            return self._sort_lines()

    def _sort_lines(self) -> bool:
        """sort_by_size() without the check (after Verify Links, at Start Batch). Lock taken here."""
        from src.cli.downloader import gallery_status, parse_url_line, sort_lines_by_size  # pylint: disable=import-outside-toplevel
        with self._lock:
            self.sort_offer = False
            self._touch_lines()
            sorted_lines = sort_lines_by_size([line["text"] for line in self._lines])
            if sorted_lines is None:
                return False
            states = {line["text"]: line["state"] for line in self._lines}
            self._lines = [self._new_line(text) for text in sorted_lines]
            for line in self._lines:  # keep the verify colours
                url, _ = parse_url_line(line["text"])
                status = gallery_status(url) if url else ""
                line["state"] = ("done" if status == "GOOD" else "fail") if status else states.get(line["text"], "")
            self._save_urls_txt()
        self.log("Download order", "List sorted: E-Hentai / ExHentai first, then nhentai, then hitomi; in each, "
                                   "galleries with fewer images first.")
        return True

    def dismiss_sort(self) -> None:
        """'No' to the sort offer."""
        with self._lock:
            self.sort_offer = False
            self._touch_lines()

    def _clear_marks(self) -> None:
        """Remove the line colours of a previous run (lock held)."""
        self._flush_pending_removals()
        for line in self._lines:
            line["state"] = ""
            line["task"] = None
        self.sort_offer = False
        self._touch_lines()

    def _estimate(self) -> str:
        """Text under the list: images still to download (exact once the links are verified)."""
        from src.cli.downloader import estimate_list_images  # pylint: disable=import-outside-toplevel
        if self._estimate_cache[0] == self._lines_rev:
            return self._estimate_cache[1]
        done_tasks = {tid for tid, st in (self._batch or {}).get("task_state", {}).items() if st == "done"}
        est = estimate_list_images([line["text"] for line in self._lines if line["task"] not in done_tasks
                                    or line["task"] is None])
        known, unverified, bad = est["known"], est["unverified"], est["bad"]
        prefix = "Still to download" if self._batch and self._batch["accepting"] else "Estimate"
        if not known and not unverified:
            text = "" if not bad else f"{prefix}: nothing to download ({bad} link(s) with problems)."
        elif not known:
            text = (f"{prefix}: {unverified} link(s) not verified yet. "
                    "Click Verify Links to see how many images you will download.")
        else:
            text = (f"{prefix}: about {est['images']:,} image{'s' if est['images'] != 1 else ''} "
                    f"in {known} galler{'ies' if known != 1 else 'y'}")
            extras = []
            if unverified:
                extras.append(f"+ {unverified} not verified")
            if bad:
                extras.append(f"{bad} with problems, skipped")
            if extras:
                text += " (" + ", ".join(extras) + ")"
            text += "."
        self._estimate_cache = (self._lines_rev, text)
        return text

    # ------------------------------------------------------------------ retry list

    def reload_retry_list(self) -> None:
        """Read failed_downloads.txt ('Refresh List')."""
        from src.cli.downloader import format_url_line  # pylint: disable=import-outside-toplevel
        from src.cli.retry import collect_retry_urls  # pylint: disable=import-outside-toplevel
        urls = collect_retry_urls()
        with self._lock:
            self._retry = [{"url": url, "text": format_url_line(url), "state": ""} for url in urls]
            self._retry_rev += 1

    def clear_retry_queue(self) -> None:
        """'Clear Queue' (after the confirmation in the page)."""
        from src.cli.downloader import save_failed_urls  # pylint: disable=import-outside-toplevel
        with self._lock:
            if self.mode == "retry":
                raise EngineError("Retry Failed is running: wait for it to finish first.")
            save_failed_urls([])
        self.reload_retry_list()
        self._set_status("Retry queue cleared.")

    def _set_retry_state(self, index: int, state: str) -> None:
        """Colour of a line of the retry list."""
        with self._lock:
            if 0 <= index < len(self._retry):
                self._retry[index]["state"] = state
                self._retry_rev += 1

    # ------------------------------------------------------------------ start / end of a download

    def _begin(self, mode: str, bar_label: str) -> None:
        """Reset bars and pause, and mark the engine busy (lock held)."""
        if self.mode:
            raise EngineError("A download is already in progress. Wait for it to finish first.")
        self.mode = mode
        self._run_seq += 1
        self._stop_requested = self._cancel_requested = False
        throttle.set_paused(False)
        throttle.clear_cancel()
        self.bar1 = {"frac": 0.0, "text": "0%", "label": bar_label}
        self.lanes = {lane: {"frac": None, "text": LANE_IDLE} for lane in throttle.SITES}

    def start_single(self, url: str) -> None:
        """Download one gallery."""
        from src.general_utils import validate_gallery_url  # pylint: disable=import-outside-toplevel
        url = (url or "").strip()
        if not url:
            raise EngineError("Please enter a gallery URL.")
        url_error = validate_gallery_url(url)
        if url_error:
            raise EngineError(url_error)
        fmt, rename, cbz = self._options()
        with self._lock:
            self._begin("single", "Album Total:")
            self.status = f"Download [{fmt}]: {url}"
        threading.Thread(target=self._run_single, args=(url, fmt, rename, cbz), daemon=True).start()

    def _run_single(self, url: str, fmt: str, rename: bool, cbz: bool) -> None:
        """Worker thread: one album."""
        from src.cli.downloader import download_album  # pylint: disable=import-outside-toplevel
        success = False
        failed_by_url: dict[str, list[str]] = {}
        append_session_banner(f"SINGLE DOWNLOAD START: {url}")
        try:
            lm = GUILiveManager(log_queue=self._q, mode="single", lane=site_lane(url))
            ok, failed_items = download_album(url, lm, image_format=fmt, rename_sequential=rename,
                                              create_cbz=cbz)
            success = ok
            if failed_items or not ok:
                failed_by_url[url] = failed_items
        except throttle.DownloadCancelled:
            pass  # _on_download_done cleans up
        except Exception as exc:  # pylint: disable=broad-except
            self.log("ERROR", str(exc))
            failed_by_url[url] = [str(exc)]
        finally:
            self._call(self._on_download_done, success, failed_by_url)

    def start_verify(self, remove_duplicates: bool = False) -> None:
        """'Verify Links': check every link online without downloading."""
        with self._lock:
            if self.mode:
                raise EngineError("A download is already in progress. Wait for it to finish first.")
            self._clear_marks()
            if remove_duplicates:
                self.remove_duplicates()
            entries = [(line["id"], url) for line, url, _ in self._entries()]
            if not entries:
                raise EngineError("The list is empty.")
            self._begin("verify", "Verified:")
            self.status = f"Verifying {len(entries)} link(s)…"
        threading.Thread(target=self._run_verify, args=(entries,), daemon=True).start()

    def _line_by_id(self, line_id: int) -> dict | None:
        """Line of the batch list with this id (lock held)."""
        return next((line for line in self._lines if line["id"] == line_id), None)

    def _set_line(self, line_id: int, state: str | None = None, text: str | None = None) -> None:
        """Change colour and/or text of a line of the batch list."""
        with self._lock:
            line = self._line_by_id(line_id)
            if line is None:
                return
            if state is not None:
                line["state"] = state
            if text is not None:
                line["text"] = text
            self._touch_lines()

    def _run_verify(self, entries: list[tuple[int, str]]) -> None:
        """Worker thread: check each link (title, size, source site)."""
        from src.cli.downloader import courtesy_pause, format_url_line, verify_gallery  # pylint: disable=import-outside-toplevel
        from src.general_utils import create_session  # pylint: disable=import-outside-toplevel
        ok_count = bad_count = 0
        domains: dict[str, int] = {}
        try:
            session = create_session()
            total = len(entries)
            for idx, (line_id, url) in enumerate(entries):
                if self._stop_requested:
                    self.log("Stopped", f"Link check stopped by the user: {total - idx} link(s) not checked.")
                    break
                self._call(self._set_line, line_id, "active")
                self._set_status(f"[{idx + 1}/{total}] {url}")
                result = verify_gallery(url, session=session)
                with self._lock:
                    line = self._line_by_id(line_id)
                    line_no = self._line_no(line) if line else "?"
                if result["ok"]:
                    ok_count += 1
                    domains[result["domain"]] = domains.get(result["domain"], 0) + 1
                    note = f" ({result['note']})" if result["note"] else ""
                    self.log("Link OK", f"Line {line_no}: \"{result['title']}\" — {result['images']} images — "
                                        f"from {result['domain']}{note}")
                    self._call(self._set_line, line_id, "done", format_url_line(url, result["title"]))
                else:
                    bad_count += 1
                    self.log("Link error", f"Line {line_no}: {result['error']}")
                    self._call(self._set_line, line_id, "fail", format_url_line(url))
                frac = (idx + 1) / total
                self._q.put(("UPDATE_BAR1", frac, f"{idx + 1}/{total} ({frac * 100:.0f}%)"))
                if idx < total - 1 and result["domain"]:
                    courtesy_pause()
        except throttle.DownloadCancelled:
            pass
        except Exception as exc:  # pylint: disable=broad-except
            self.log("ERROR", str(exc))
        finally:
            self._call(self._on_verify_done, ok_count, bad_count, domains)

    def _on_verify_done(self, ok_count: int, bad_count: int, domains: dict) -> None:
        """Dispatcher: save the annotated list, print the summary, sort the list by site and size."""
        with self._lock:
            self.mode = None
            self._stop_requested = self._cancel_requested = False
            self._save_urls_txt()
            sources = ", ".join(f"{d}: {n}" for d, n in sorted(domains.items())) or "none"
            summary = f"{ok_count} valid, {bad_count} with problems. Download source → {sources}"
            self._estimate_cache = (-1, "")
            est = self._estimate()
            self.status = ("✔ " if not bad_count else "⚠ ") + "Verify: " + summary
            self.sort_offer = False
            self._touch_lines()
            timer = threading.Timer(config.DONE_LINE_REMOVE_DELAY, self._call,
                                    args=(self._reset_bars, self._run_seq))
            timer.daemon = True
            timer.start()
        self._append_log(ts(), "Verify completed", summary + (f". {est}" if est else ""))
        self._sort_lines()

    def start_batch(self, remove_duplicates: bool = False) -> None:
        """'Start Batch': download every link of the list; links added later join the batch."""
        with self._lock:
            if self.mode:
                raise EngineError("A download is already in progress. Wait for it to finish first.")
            self._clear_marks()
            if remove_duplicates:
                self.remove_duplicates()
            if not self._entries():
                raise EngineError("URLs.txt is empty. Add at least one URL before starting.")
            self._sort_lines()  # by site, then size: the site lanes go down the list in order
            self._clear_marks()
            fmt, rename, cbz = self._options()
            self._begin("batch", "Batch Total:")
            self._batch = {
                "queues": {lane: deque() for lane in throttle.SITES},
                "lane_running": {lane: False for lane in throttle.SITES},
                "result": {"started": 0, "success": True, "failed": {}},
                "options": (fmt, rename, cbz),
                "total": 0,
                "seq": 0,
                "keys": {},
                "task_state": {},
                "verify_bad": set(),
                "cancelled": set(),
                "failed_tasks": [],
                "active": {},
                "accepting": True,
                "verify_queue": queue.Queue(),
                "stop": threading.Event(),
            }
            self._batch["progress"] = BatchProgress(self._q, lambda: self._batch["total"] if self._batch else 1)
            self._scan(live=False)
            to_check = self._batch["verify_queue"].qsize()
            nothing_to_do = not any(self._batch["lane_running"].values())
            if nothing_to_do:
                self._batch["accepting"] = False
            self.status = f"Batch download [{fmt}]: {self._batch['total']} URL(s)"
        if to_check:
            self.log("Verify", f"{to_check} link(s) not verified yet: checking them in parallel with the download.")
            threading.Thread(target=self._run_batch_verify, daemon=True).start()
        if nothing_to_do:
            threading.Thread(target=self._finish_batch, daemon=True).start()

    def _scan(self, live: bool) -> None:
        """Give every link line not yet claimed to the running batch (lock held).

        live=True (links added while the batch runs): a line that is not a valid gallery link
        is left alone. The first scan takes every line: invalid links were confirmed at start.
        """
        from src.cli.downloader import gallery_key, gallery_status, parse_url_line  # pylint: disable=import-outside-toplevel
        from src.general_utils import validate_gallery_url  # pylint: disable=import-outside-toplevel
        batch = self._batch
        if not batch or not batch["accepting"]:
            return
        for line in self._lines:
            if line["task"]:
                continue
            url, name = parse_url_line(line["text"])
            if not url:
                continue
            valid = not validate_gallery_url(url)
            if live and not valid:
                continue
            key = gallery_key(url)
            dup = key in batch["keys"] and (batch["keys"][key] or not valid)
            if not dup:
                batch["keys"][key] = valid
            batch["seq"] += 1
            task_id = batch["seq"]
            batch["total"] += 1
            batch["task_state"][task_id] = "queued"
            line["task"] = task_id
            line["state"] = "queued"
            task = {"id": task_id, "url": url, "name": name, "dup": dup}
            lane = site_lane(url)
            batch["queues"][lane].append(task)
            if not batch["lane_running"][lane]:
                batch["lane_running"][lane] = True
                threading.Thread(target=self._run_batch_lane, args=(lane,), daemon=True).start()
            if valid and not dup and not gallery_status(url):
                batch["verify_queue"].put(task)
        self._touch_lines()

    def _task_line_id(self, task_id: int) -> int | None:
        """Id of the line of a batch task (None if the line was deleted). Lock held."""
        line = next((item for item in self._lines if item["task"] == task_id), None)
        return line["id"] if line else None

    def _next_batch_task(self, lane: str) -> tuple[dict | None, bool]:
        """(next task of lane, False), or (None, last) when the lane is empty.

        The next task is the one of this site that comes first in the list (lines can be
        moved while the batch runs); tasks of removed lines are dropped.
        """
        with self._lock:
            batch = self._batch
            lane_queue = batch["queues"][lane]
            # Stop / Cancel: no new gallery starts (with Stop the lines stay in the list)
            if lane_queue and not (self._stop_requested or self._cancel_requested):
                position = {line["task"]: idx for idx, line in enumerate(self._lines) if line["task"]}
                waiting = [task for task in lane_queue if task["id"] not in batch["cancelled"]
                           and task["id"] in position]
                gone = [task for task in lane_queue if task not in waiting]
                lane_queue.clear()
                lane_queue.extend(sorted(waiting, key=lambda task: position[task["id"]]))
                for task in gone:  # line removed from the list
                    batch["task_state"][task["id"]] = "done"
                    batch["total"] = max(0, batch["total"] - 1)
                    self.log("Removed", f"{task['name'] or task['url']}: removed from the queue, not downloaded.")
                if lane_queue:
                    return lane_queue.popleft(), False
            batch["lane_running"][lane] = False
            last = not any(batch["lane_running"].values())
            if last:
                batch["accepting"] = False
                self._touch_lines()
            return None, last

    def _run_batch_lane(self, lane: str) -> None:
        """Worker thread: the galleries of one site, one after another."""
        last = False
        try:
            while True:
                task, last = self._next_batch_task(lane)
                if task is None:
                    return
                try:
                    self._run_batch_task(task, lane)
                except throttle.DownloadCancelled:
                    with self._lock:   # Cancel: the gallery stops here (temp/ is emptied at the end)
                        self._batch["task_state"][task["id"]] = "cancelled"
                except Exception as exc:  # pylint: disable=broad-except
                    self.log("ERROR", f"{task['url']}: {exc}")
                    with self._lock:
                        self._batch["result"]["success"] = False
                        self._batch["task_state"][task["id"]] = "done"
                    self._call(self._on_task_done, task["id"], False)
        finally:
            if last:
                self._finish_batch()

    def _run_batch_task(self, task: dict, lane: str) -> None:
        """Worker thread: download (or skip) one gallery of the batch."""
        from src.cli.downloader import download_album, format_url_line, gallery_name, update_failed_url  # pylint: disable=import-outside-toplevel
        batch = self._batch
        fmt, rename, cbz = batch["options"]
        task_id, url, name = task["id"], task["url"], task["name"]
        with self._lock:
            if task_id in batch["cancelled"]:
                batch["task_state"][task_id] = "done"
                batch["total"] = max(0, batch["total"] - 1)
                self.log("Skipped", f"{name or url}: removed from the list, not downloaded.")
                return
            batch["task_state"][task_id] = "active"
            batch["result"]["started"] += 1
            started, total_items = batch["result"]["started"], batch["total"]
        self._call(self._on_task_active, task_id, started, total_items, name or gallery_name(url) or url)

        lm = None
        if task["dup"]:
            self.log("Duplicate", f"{name or url}: same gallery as a link already in the batch, skipped.")
            ok, failed_items = True, []
        elif task_id in batch["verify_bad"]:
            self.log("Skipped", f"{url}: link check failed, not downloaded (added to Retry Failed).")
            update_failed_url(url, has_failures=True)
            ok, failed_items = False, []
        else:
            lm = GUILiveManager(log_queue=self._q, mode="batch", batch_total=total_items,
                                progress=batch["progress"], log_prefix=f"[{lane}] ", lane=lane)
            ok, failed_items = download_album(url, lm, image_format=fmt, rename_sequential=rename,
                                              name_hint=name, create_cbz=cbz)
            self._call(self._annotate_task, task_id, format_url_line(url))
        batch["progress"].album_finished(lm)
        with self._lock:
            batch["task_state"][task_id] = "done"
            if not ok or failed_items:
                batch["result"]["failed"][url] = failed_items
                batch["result"]["success"] = False
        self._call(self._on_task_done, task_id, ok and not failed_items)

    def _annotate_task(self, task_id: int, text: str) -> None:
        """Dispatcher: rewrite a batch line as STATUS | IMAGES | TITLE | LINK."""
        with self._lock:
            line_id = self._task_line_id(task_id)
            if line_id:
                self._set_line(line_id, text=text)
                self._save_urls_txt()

    def _on_task_active(self, task_id: int, cur_idx: int, total_cnt: int, label: str) -> None:
        """Dispatcher: highlight a line being downloaded (one per site at a time)."""
        with self._lock:
            line_id = self._task_line_id(task_id)
            if line_id:
                self._set_line(line_id, "active")
            if self._batch:
                self._batch["active"][task_id] = label
                self._batch["counter"] = (cur_idx, total_cnt)
                self._show_active_tasks()

    def _show_active_tasks(self) -> None:
        """Status line: the galleries downloading right now (lock held)."""
        if not self._batch or not self._batch["active"]:
            return
        cur_idx, total_cnt = self._batch.get("counter", (0, 0))
        self.status = f"[{cur_idx}/{total_cnt}] " + "  ‖  ".join(self._batch["active"].values())

    def _on_task_done(self, task_id: int, is_ok: bool) -> None:
        """Dispatcher: colour a finished line; downloaded lines go away after a while."""
        with self._lock:
            if self._batch and self._batch["active"].pop(task_id, None) is not None:
                self._show_active_tasks()
            line_id = self._task_line_id(task_id)
            if line_id:
                self._set_line(line_id, "done" if is_ok else "fail")
            if is_ok:
                timer = threading.Timer(config.DONE_LINE_REMOVE_DELAY, self._call,
                                        args=(self._remove_task_line, task_id))
                timer.daemon = True
                self._pending_removals[task_id] = timer
                timer.start()
            elif self._batch:
                self._batch["failed_tasks"].append(task_id)

    def _remove_task_line(self, task_id: int, save: bool = True) -> None:
        """Delete the line of a batch task (if it still exists)."""
        with self._lock:
            self._pending_removals.pop(task_id, None)
            before = len(self._lines)
            self._lines = [line for line in self._lines if line["task"] != task_id]
            if len(self._lines) != before:
                self._touch_lines()
                if save:
                    self._save_urls_txt()

    def _flush_pending_removals(self) -> None:
        """Remove at once every downloaded line still waiting for its timer (lock held)."""
        if not self._pending_removals:
            return
        for task_id, timer in list(self._pending_removals.items()):
            timer.cancel()
            self._remove_task_line(task_id, save=False)
        self._save_urls_txt()

    def _finish_batch(self) -> None:
        """Worker thread (last lane): close the batch."""
        with self._lock:
            batch = self._batch
            batch["accepting"] = False
            done = batch["result"]["started"]
            success = batch["result"]["success"]
            failed_by_url = dict(batch["result"]["failed"])
            batch["stop"].set()
        self._q.put(("UPDATE_BAR1", 1.0, f"{done}/{done} (100%)"))
        self._call(self._on_batch_finished)
        self._call(self._on_download_done, success, failed_by_url)

    def _on_batch_finished(self) -> None:
        """Dispatcher: failed lines leave the list (they are in Retry Failed)."""
        with self._lock:
            for task_id in self._batch["failed_tasks"]:
                self._remove_task_line(task_id, save=False)
            for line in self._lines:
                if line["state"] in ("queued", "active"):
                    line["state"] = ""
            self._touch_lines()
            self._save_urls_txt()

    def _run_batch_verify(self) -> None:
        """Worker thread: check the not-yet-verified links while the batch downloads."""
        from src.cli.downloader import courtesy_pause, format_url_line, verify_gallery  # pylint: disable=import-outside-toplevel
        from src.general_utils import create_session  # pylint: disable=import-outside-toplevel
        batch = self._batch
        try:
            session = create_session()
            while not (batch["stop"].is_set() or self._stop_requested or self._cancel_requested):
                try:
                    task = batch["verify_queue"].get(timeout=0.5)
                except queue.Empty:
                    continue
                with self._lock:
                    if batch["task_state"].get(task["id"]) != "queued" or task["id"] in batch["cancelled"]:
                        continue
                result = verify_gallery(task["url"], session=session)
                with self._lock:
                    still_queued = batch["task_state"].get(task["id"]) == "queued"
                if result["ok"]:
                    if still_queued:
                        self._call(self._annotate_task, task["id"], format_url_line(task["url"], result["title"]))
                    self.log("Link OK", f"\"{result['title']}\" — {result['images']} images — from {result['domain']}")
                elif still_queued:
                    with self._lock:
                        batch["verify_bad"].add(task["id"])
                    self._call(self._annotate_task, task["id"], format_url_line(task["url"]))
                    self._call(self._mark_task_bad, task["id"])
                    self.log("Link error", f"{task['url']}: {result['error']}")
                if result["domain"]:
                    courtesy_pause()
        except throttle.DownloadCancelled:
            pass
        except Exception as exc:  # pylint: disable=broad-except
            self.log("ERROR", f"Link check: {exc}")

    def _mark_task_bad(self, task_id: int) -> None:
        """Dispatcher: a queued line found BAD by the parallel link check."""
        with self._lock:
            line_id = self._task_line_id(task_id)
            if line_id:
                self._set_line(line_id, "fail")

    def start_retry(self) -> None:
        """'Retry Failed': download again every album of failed_downloads.txt."""
        from src.cli.retry import collect_retry_urls  # pylint: disable=import-outside-toplevel
        urls = collect_retry_urls()
        if not urls:
            raise EngineError("No failed URLs found in the queue.")
        options = self._options()
        with self._lock:
            self._begin("retry", "Retry Total:")
            self.status = f"Retrying [{options[0]}] {len(urls)} failed URL(s)…"
        self.reload_retry_list()
        threading.Thread(target=self._run_retry, args=(options,), daemon=True).start()

    def _run_retry(self, options: tuple) -> None:
        """Worker thread: retry every failed album, one lane per site, keep only those still failing."""
        from src.cli.downloader import save_failed_urls  # pylint: disable=import-outside-toplevel
        success = True
        failed_by_url: dict[str, list[str]] = {}
        try:
            with self._lock:
                retry_urls = [item["url"] for item in self._retry]
            total = len(retry_urls)
            append_session_banner(f"RETRY START ({total} albums)")
            lanes: dict[str, list[tuple[int, str]]] = {}
            for idx, url in enumerate(retry_urls):
                lanes.setdefault(site_lane(url), []).append((idx, url))
            progress = BatchProgress(self._q, lambda: total)
            remaining: dict[int, str] = {}
            counter = {"started": 0}
            lock = threading.Lock()
            threads = [threading.Thread(target=self._run_retry_lane,
                                        args=(lane, items, total, progress, options, remaining,
                                              failed_by_url, counter, lock), daemon=True)
                       for lane, items in lanes.items()]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            still_failing = [remaining[idx] for idx in sorted(remaining)]
            success = not failed_by_url
            if not self._cancel_requested:  # after Cancel the list is emptied anyway
                save_failed_urls(still_failing)
            not_retried = len(still_failing) - len(failed_by_url)
            self.log("Retry completed", f"Processed: {total - not_retried} | Still failing: {len(failed_by_url)}"
                     + (f" | Not retried (Stop): {not_retried}, still in the queue" if not_retried > 0 else ""))
            self._q.put(("UPDATE_BAR1", 1.0, f"{total}/{total} (100%)"))
        except Exception as exc:  # pylint: disable=broad-except
            self.log("ERROR", str(exc))
            success = False
        finally:
            self._call(self._on_download_done, success, failed_by_url)

    def _run_retry_lane(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self, lane: str, items: list[tuple[int, str]], total: int, progress: BatchProgress,
        options: tuple, remaining: dict[int, str], failed_by_url: dict[str, list[str]],
        counter: dict[str, int], lock: threading.Lock,
    ) -> None:
        """Worker thread: retry the albums of one site, one after another."""
        from src.cli.downloader import download_album, gallery_name  # pylint: disable=import-outside-toplevel
        fmt, rename, cbz = options
        for pos, (idx, url) in enumerate(items):
            if self._stop_requested or self._cancel_requested:
                with lock:  # Stop: the albums not tried yet stay in failed_downloads.txt
                    for rest_idx, rest_url in items[pos:]:
                        remaining[rest_idx] = rest_url
                return
            with lock:
                counter["started"] += 1
                started = counter["started"]
            self._call(self._set_retry_state, idx, "active")
            self._set_status(f"[{started}/{total}] Retrying: {gallery_name(url) or url}")
            lm = GUILiveManager(log_queue=self._q, mode="retry", batch_total=total, line_no=idx + 1,
                                progress=progress, log_prefix=f"[{lane}] ", lane=lane)
            try:
                ok, failed_items = download_album(url, lm, image_format=fmt, rename_sequential=rename,
                                                  name_hint=gallery_name(url), create_cbz=cbz)
            except throttle.DownloadCancelled:
                with lock:
                    for rest_idx, rest_url in items[pos:]:
                        remaining[rest_idx] = rest_url
                return
            except Exception as exc:  # pylint: disable=broad-except
                self.log("ERROR", f"{url}: {exc}")
                ok, failed_items = False, [str(exc)]
            progress.album_finished(lm)
            if not ok or failed_items:
                with lock:
                    remaining[idx] = url
                    failed_by_url[url] = failed_items
                self._call(self._set_retry_state, idx, "fail")
            else:
                self._call(self._set_retry_state, idx, "done")

    def _on_download_done(self, success: bool, failed_map: dict[str, list[str]]) -> None:
        """Dispatcher: the run is over. Unlock, print the summary, reload the retry list."""
        from src.cli.downloader import gallery_name  # pylint: disable=import-outside-toplevel
        from src.general_utils import validate_gallery_url  # pylint: disable=import-outside-toplevel
        with self._lock:
            was_batch = self.mode == "batch"
            run_mode = self.mode
            self.mode = None
            self._save_counter()
            self._batch = None
            throttle.set_paused(False)
            cancelled, stopped = self._cancel_requested, self._stop_requested
            self._stop_requested = self._cancel_requested = False
            if cancelled:
                # Cancel: no summary, just the cleanup
                self.lanes = {lane: {"frac": None, "text": LANE_IDLE} for lane in throttle.SITES}
                self._finish_cancel()
        if cancelled:
            self.reload_retry_list()
            return
        with self._lock:
            if success:
                self.bar1["frac"], self.bar1["text"] = 1.0, "100%"
            self.lanes = {lane: {"frac": None, "text": LANE_IDLE} for lane in throttle.SITES}
            if was_batch:
                self._touch_lines()
            # The page is seen later too: after a while the bars go back to idle (the status line stays)
            timer = threading.Timer(config.DONE_LINE_REMOVE_DELAY, self._call,
                                    args=(self._reset_bars, self._run_seq))
            timer.daemon = True
            timer.start()

        def is_image_item(item: str) -> bool:
            return "/s/" in item or "(page " in item

        sep = "─" * 60
        failed_albums = [url for url in failed_map if url]
        missing_images = sum(1 for items in failed_map.values() for item in (items or []) if is_image_item(item))
        if success and not failed_map:
            append_session_banner("DOWNLOAD COMPLETED SUCCESSFULLY — no missing files.")
            self._append_log(ts(), "", sep, "success_banner")
            self._append_log(ts(), "", "✔  DOWNLOAD COMPLETED SUCCESSFULLY — no missing files.", "success_banner")
            self._append_log(ts(), "", sep, "success_banner")
            status = "✔ Download completed successfully."
        else:
            count = len(failed_albums) or sum(len(items) or 1 for items in failed_map.values())
            gal_word = "gallery" if count == 1 else "galleries"
            img_part = f", {missing_images} image(s) missing" if missing_images else ""
            append_session_banner(f"DOWNLOAD NOT COMPLETE — {count} {gal_word} with problems{img_part}")
            self._append_log(ts(), "", sep, "warn_banner")
            self._append_log(ts(), "", f"⚠  DOWNLOAD NOT COMPLETE — {count} {gal_word} with problems{img_part}:",
                             "warn_banner")
            for album_url, items in failed_map.items():
                if album_url:
                    title = gallery_name(album_url)
                    self._append_log(ts(), "", f"🔗 {title}  —  {album_url}" if title else f"🔗 {album_url}",
                                     "warn_url")
                image_items = [item for item in (items or []) if is_image_item(item)]
                if album_url and validate_gallery_url(album_url):
                    self._append_log(ts(), "", "   • Not a valid gallery link — nothing was downloaded "
                                               "(check that line of the list)", "warn_item")
                elif not image_items:
                    self._append_log(ts(), "", "   • Gallery not downloaded: its page could not be read "
                                               "(the reason is in the log above)", "warn_item")
                else:
                    for item in image_items:
                        label = item.split("/")[-1].strip() or item
                        self._append_log(ts(), "", f"   • missing image: {label}", "warn_item")
            self._append_log(ts(), "", sep, "warn_banner")
            self._append_log(ts(), "", "Use 'Retry Failed' to retry the missing files.", "warn_banner")
            status = f"⚠ Download not complete: {count} {gal_word} with problems{img_part} — use Retry Failed."
        if stopped:
            if run_mode == "batch":
                with self._lock:
                    waiting = max(0, len(self._entries()) - len(self._pending_removals))
                left = f"{waiting} link(s) left in the list for the next Start Batch."
            elif run_mode == "retry":
                left = "The albums not retried are still in Retry Failed."
            else:
                left = ""
            append_session_banner("DOWNLOAD STOPPED BY THE USER")
            self._append_log(ts(), "", f"⏹  Stopped by the user. {left}", "warn_banner")
            status = f"⏹ Stopped. {left}".strip()
        with self._lock:
            self.status = status
        self.reload_retry_list()

    def _reset_bars(self, run_seq: int) -> None:
        """Dispatcher: empty the total bar of a finished run (unless another run started since)."""
        with self._lock:
            if self.mode or run_seq != self._run_seq:
                return
            self.bar1 = {"frac": 0.0, "text": "0%", "label": "Total:"}

    # ------------------------------------------------------------------ cookies

    def cookies_info(self) -> dict:
        """Content of cookies.json for the account window (raw text, or the template if missing)."""
        from src import cookie_import  # pylint: disable=import-outside-toplevel
        path = self.data_dir / cookie_import.COOKIES_FILE
        exists = path.exists()
        raw = ""
        if exists:
            try:
                raw = path.read_text(encoding="utf-8")
            except OSError as err:
                raw = f"(could not read cookies.json: {err})"
        data = cookie_import.load_cookies_file(path) if exists else {}
        nh = data.get("nhentai") if isinstance(data.get("nhentai"), dict) else {}
        template = json.dumps({"ipb_member_id": "", "ipb_pass_hash": "", "igneous": "",
                               "nhentai": {"api_key": ""}}, indent=2)
        return {
            "exists": exists,
            "path": str(path),
            "content": raw if exists else template,
            "values": {name: data.get(name, "") for name in cookie_import.EHENTAI_COOKIES},
            "nhentai_api_key": nh.get("api_key", ""),
        }

    def save_cookies_paste(self, text: str, nhentai_api_key: str | None) -> dict:
        """'Read cookies': keep the needed cookies of the pasted text and write cookies.json."""
        from src import cookie_import  # pylint: disable=import-outside-toplevel
        found = cookie_import.extract_ehentai(cookie_import.parse_cookies(text or ""))
        if (text or "").strip() and not found:
            raise EngineError("No E-Hentai / ExHentai cookie (ipb_member_id, ipb_pass_hash, igneous) "
                              "found in the pasted text.")
        current = cookie_import.load_cookies_file(self.data_dir / cookie_import.COOKIES_FILE)
        values = {name: current.get(name, "") for name in cookie_import.EHENTAI_COOKIES}
        values.update(found)
        cookie_import.save_cookies_file(values, nhentai_api_key, self.data_dir / cookie_import.COOKIES_FILE)
        self._cookies_changed()
        return {"found": sorted(found)}

    def save_cookies_raw(self, content: str) -> None:
        """Text editor of cookies.json: must be a JSON object."""
        from src import cookie_import  # pylint: disable=import-outside-toplevel
        try:
            data = json.loads(content or "{}")
        except ValueError as err:
            raise EngineError(f"cookies.json is not valid JSON: {err}") from err
        if not isinstance(data, dict):
            raise EngineError("cookies.json must be a JSON object: { \"name\": \"value\", ... }")
        path = self.data_dir / cookie_import.COOKIES_FILE
        tmp = path.with_name(f"{path.name}.part")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(tmp, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        self._cookies_changed()

    def _cookies_changed(self) -> None:
        """cookies.json changed: the next requests use the new values."""
        from src import general_utils  # pylint: disable=import-outside-toplevel
        from src.cli import downloader as cli_downloader  # pylint: disable=import-outside-toplevel
        general_utils._cached_cookies = None  # pylint: disable=protected-access
        cli_downloader._nhentai_session = None  # pylint: disable=protected-access
        self.log("Account", "cookies.json saved: the new values are used from the next gallery.")

    # ------------------------------------------------------------------ updates

    def _startup_update_check(self) -> None:
        """At most once a day, if enabled."""
        try:
            last = float(self.settings.get("update_last_check", 0))
        except (TypeError, ValueError):
            last = 0
        if time.time() - last < 86400:
            return
        try:
            self.check_updates()
        except Exception:  # pylint: disable=broad-except
            pass

    def check_updates(self) -> dict:
        """Ask GitHub for the latest release."""
        from src.update_check import latest_release  # pylint: disable=import-outside-toplevel
        result = latest_release()
        with self._lock:
            self.update_info = result
            self.settings["update_last_check"] = time.time()
            self._save_settings()
        if result["newer"]:
            self.log("Update", f"Version {result['version']} is available: {result['url']} "
                               "(update the Docker image to get it).")
        return result

    def session_log_tail(self, max_bytes: int = 300_000) -> str:
        """End of session.log ('Open log file')."""
        path = Path(config.SESSION_LOG)
        try:
            with path.open("rb") as file:
                file.seek(0, os.SEEK_END)
                size = file.tell()
                file.seek(max(0, size - max_bytes))
                data = file.read()
        except OSError:
            return "(session.log is empty)"
        text = data.decode("utf-8", errors="replace")
        if size > max_bytes:
            text = "…(older lines cut)…\n" + text.split("\n", 1)[-1]
        return text

    # ------------------------------------------------------------------ statistics

    @staticmethod
    def tag_catalog() -> dict:
        """Favorite tags window: every tag met in the galleries, with the favorite ones first."""
        return {"tags": tag_utils.catalog(), "seen": tag_utils.seen_count(), "badge_max": tag_utils.BADGE_MAX,
                "colors": list(tag_utils.DEFAULT_COLORS)}

    @staticmethod
    def statistics() -> dict:
        """Statistics tab."""
        return stats.snapshot(top=25)

    def reset_statistics(self) -> None:
        """'Reset' of the Statistics tab."""
        stats.reset()
        self.log("Statistics", "Statistics reset.")

    # ------------------------------------------------------------------ snapshot

    def snapshot(self, since: int, epoch: int, lines_rev: int, retry_rev: int) -> dict:
        """Everything the page shows; lists and log only when they changed."""
        with self._lock:
            self._roll_counter()
            reset_log = epoch != self._log_epoch
            logs = [item for item in self._log if reset_log or item["seq"] > since]
            data = {
                "mode": self.mode,
                "paused": throttle.is_paused() and bool(self.mode),
                "stopping": self._stop_requested and bool(self.mode),
                "cancelling": self._cancel_requested and bool(self.mode),
                "status": self.status,
                "bar1": dict(self.bar1),
                "lanes": [{"site": lane, "label": LANE_LABELS[lane], **self.lanes[lane]} for lane in throttle.SITES],
                "ban": [{"site": site, "remaining": int(left)} for site, left in throttle.banned_sites()],
                "speed": {"level": throttle.get_speed(), "name": throttle.speed_name(),
                          "workers": throttle.SPEED_LEVELS[throttle.get_speed()][2]},
                "session_images": self.session_images,
                "counter_reset_hour": COUNTER_RESET_HOUR,
                "settings": self.public_settings(),
                "log": {"epoch": self._log_epoch, "reset": reset_log, "items": logs},
                "lines_rev": self._lines_rev,
                "retry_rev": self._retry_rev,
                "update": self.update_info,
            }
            data["speed_now"] = stats.current_speed()
            data["badge_width"] = tag_utils.badge_width()
            if lines_rev != self._lines_rev:
                data["lines"] = [{"id": line["id"], "text": line["text"], "state": line["state"],
                                  **line_view(line["text"])} for line in self._lines]
                data["estimate"] = self._estimate()
                data["sort_offer"] = self.sort_offer
                data["accepting"] = bool(self._batch and self._batch["accepting"])
            if retry_rev != self._retry_rev:
                data["retry"] = [{"text": item["text"], "state": item["state"], **line_view(item["text"])}
                                 for item in self._retry]
            return data
