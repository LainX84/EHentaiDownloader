"""Main application window (EHentaiGUI), assembled from the mixins of src/gui/."""

# pylint: disable=protected-access,too-many-lines
from __future__ import annotations

import atexit
import json
import logging
import os
import queue
import threading
from pathlib import Path
from tkinter import (
    END,
    messagebox,
)
import tkinter as tk

from src.config import (
    AUTOSAVE_INTERVAL,
    DOWNLOAD_FOLDER,
    DEFAULT_SPEED,
    SESSION_LOG,
    SETTINGS_FILE,
)
from src import gui_dialogs, paths, throttle, ui_theme
from src.downloader.cbz_archive import set_comic_info
from src.file_utils import append_session_banner, append_session_log

from src.gui.layout import LayoutMixin
from src.gui.url_list import UrlListMixin
from src.gui.link_intake import LinkIntakeMixin
from src.gui.updates import UpdatesMixin
from src.gui.workers import WorkersMixin
from src.gui.when_done import WhenDoneMixin
from src.gui.live_manager import QueueLogHandler
from src.gui import style
from src.gui.style import (
    DIALOG_FONTS, PAD, open_with_system, ts as _ts,
)


class EHentaiGUI(  # pylint: disable=too-many-ancestors,too-many-instance-attributes
    LayoutMixin, UrlListMixin, LinkIntakeMixin, WorkersMixin, WhenDoneMixin, UpdatesMixin, tk.Tk,
):
    """Main application window: header, three tabs (Single / Batch / Retry), progress bars and log."""

    def __init__(self) -> None:
        """Create the window, build the widgets and start polling the worker queue."""
        super().__init__()

        self.title("EHentai Downloader")
        try:  # window / taskbar icon: the system picks the size (16/32 px = "EHD" letters)
            self._icons = [tk.PhotoImage(file=str(paths.resource(f"assets/{name}")))
                           for name in ("icon.png", "icon_32.png", "icon_16.png")]
            self.iconphoto(True, *self._icons)
        except (tk.TclError, OSError):
            pass
        self.configure(bg=style.BG)
        self.minsize(760, 520)
        self.resizable(True, True)

        # Centre on screen
        self.update_idletasks()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        w, h = 920, 660
        self._initial_h = h
        self.geometry(f"{w}x{h}+{(sw - w)//2}+{(sh - h)//2}")

        # Thread communication
        self._q: queue.Queue = queue.Queue()
        self._running = False  # True while a download is in progress
        self._closed = False

        # Batch download with a live queue (links can be added while it runs).
        # Every claimed editor line carries a "task_<id>" tag, so it can be found again even
        # when lines above it are added or removed.
        self._batch_lock = threading.Lock()
        self._batch_queue: queue.Queue = queue.Queue()    # tasks waiting for the downloader
        self._verify_queue: queue.Queue = queue.Queue()   # tasks waiting for the link check
        self._batch_scan_done = threading.Event()
        self._batch_stop = threading.Event()
        self._batch_accepting = False   # GUI may still hand new lines to the running batch
        self._batch_seq = 0             # last task id
        self._batch_total = 0           # tasks claimed in the current batch
        self._batch_keys: dict[str, bool] = {}   # gallery key -> claimed link is valid
        self._task_state: dict[int, str] = {}    # id -> queued / active / done
        self._verify_bad: set[int] = set()       # ids found BAD by the parallel link check
        self._failed_task_ids: list[int] = []    # ids that failed (removed when the batch ends)
        self._pending_removals: dict[int, str] = {}  # id -> after() handle of green lines

        # Images really downloaded since the program was opened (starts from 0 every time)
        self._session_images = 0
        self._estimate_text = ""

        # Clipboard watcher (Settings → Watch clipboard for links)
        self._clipboard_seen = ""
        self._clipboard_job = None

        # GUI choices saved at the last run (format, Auto Rename, CBZ archive)
        self._settings = self._load_settings()

        # Download speed (slider) and temporary IP-ban pause, shared with the worker threads
        try:
            throttle.set_speed(int(self._settings.get("speed", DEFAULT_SPEED)))
        except (TypeError, ValueError):
            throttle.set_speed(DEFAULT_SPEED)
        self._ban_until = 0.0        # monotonic end of the current ban (0 = not banned)
        self._ban_tick_job = None
        throttle.add_ban_listener(lambda until: self._q.put(("BAN", until)))

        # Theme: "auto" (follow the system), "light" or "dark"
        self._theme_choice = tk.StringVar(
            value=self._settings.get("theme") if self._settings.get("theme") in ui_theme.THEME_CHOICES
            else ui_theme.THEME_AUTO
        )
        self._theme_active = ui_theme.THEME_LIGHT   # palette the widgets are drawn with

        self._setup_styles()
        self._build_menu()
        self._build_ui()
        self._refresh_speed_label()
        self._apply_theme(ui_theme.resolve(self._theme_choice.get()), initial=True)
        self.after(60_000, self._follow_system_theme)
        self._install_logging()
        self._poll_queue()
        self.after(AUTOSAVE_INTERVAL * 1000, self._autosave_tick)
        self._estimate_tick()
        if not self._setup_drag_and_drop():
            logging.info("Drag and drop is off (tkinterdnd2 not installed or not supported here).")
        if self._watch_clipboard_var.get():
            self._clipboard_seen = self._clipboard_text()
            self._clipboard_job = self.after(1000, self._clipboard_tick)
        self.bind("<Configure>", self._on_window_resize)
        self.protocol("WM_DELETE_WINDOW", self._on_window_close)
        atexit.register(self._on_exit)

    def _open_account_dialog(self) -> None:
        """File → Account & cookies…"""
        gui_dialogs.AccountDialog(self, self._palette(), DIALOG_FONTS, self._on_cookies_saved)

    def _on_cookies_saved(self) -> None:
        """cookies.json changed: make the next requests use the new values."""
        from src.cli import downloader as cli_downloader  # pylint: disable=import-outside-toplevel
        from src import general_utils  # pylint: disable=import-outside-toplevel

        general_utils._cached_cookies = None  # pylint: disable=protected-access
        cli_downloader._nhentai_session = None  # pylint: disable=protected-access
        self._append_log(_ts(), "Account", "cookies.json saved: the new values are used from the next gallery.")

    def show_startup_notice(self) -> bool:
        """Show the disclaimer unless it was snoozed; False when the user declines."""
        if not gui_dialogs.disclaimer_needed(self._settings):
            return True
        return gui_dialogs.show_disclaimer(
            self, self._palette(), DIALOG_FONTS, self._settings, self._save_settings, startup=True,
        )

    def _install_logging(self) -> None:
        """Redirect Python logging to the GUI log box, suppressing external debug spam."""
        for noisy in (
            "urllib3",
            "urllib3.connectionpool",
            "requests",
            "PIL",
            "PIL.Image",
            "PIL.PngImagePlugin",
        ):
            logging.getLogger(noisy).setLevel(logging.WARNING)

        handler = QueueLogHandler(self._q)
        handler.setFormatter(logging.Formatter("%(message)s"))
        root_logger = logging.getLogger()
        root_logger.addHandler(handler)
        root_logger.setLevel(logging.INFO)
        append_session_banner("GUI APPLICATION START")

    def _poll_queue(self) -> None:
        """Drain the inter-thread queue and update widgets. Called every 100 ms."""
        try:
            while True:
                msg = self._q.get_nowait()
                msg_type = msg[0]

                if msg_type == "LOG":
                    _, ts, event, details = msg
                    self._append_log(ts, event, details)
                elif msg_type == "SET_LABELS":
                    _, lbl1, lbl2 = msg
                    self._bar1_lbl.configure(text=lbl1)
                    self._bar2_lbl.configure(text=lbl2)
                elif msg_type == "PROGRESS_BARS":
                    _, b1_val, b1_txt, b2_val, b2_txt = msg
                    if b1_val is not None:
                        self._bar1["value"] = b1_val * 100
                    if b1_txt is not None:
                        self._bar1_pct.configure(text=b1_txt)
                    if b2_val is not None:
                        self._bar2["value"] = b2_val * 100
                    if b2_txt is not None:
                        self._bar2_pct.configure(text=b2_txt)
                elif msg_type == "UPDATE_BAR1":
                    _, val, txt = msg
                    self._bar1["value"] = val * 100
                    self._bar1_pct.configure(text=txt)
                elif msg_type == "UPDATE_BAR2":
                    _, val, txt = msg
                    self._bar2["value"] = val * 100
                    self._bar2_pct.configure(text=txt)
                elif msg_type == "BATCH_HIGHLIGHT":
                    _, line_no, cur_idx, total_cnt, url = msg
                    self._batch_editor.tag_remove("active_batch_line", "1.0", END)
                    self._batch_editor.tag_add("active_batch_line", f"{line_no}.0", f"{line_no}.end")
                    self._batch_editor.see(f"{line_no}.0")
                    self._status_var.set(f"[{cur_idx}/{total_cnt}] Line {line_no}: {url}")
                elif msg_type == "BATCH_ANNOTATE":
                    # Replace a batch line with "URL  # Gallery name"
                    _, line_no, text = msg
                    self._replace_editor_line(line_no, text)
                elif msg_type == "RELOAD_LISTS":
                    # Worker threads must not touch widgets: they ask the GUI thread to do it
                    self._load_retry_list()
                    self._load_urls_txt()
                elif msg_type == "VERIFY_DONE":
                    self._on_verify_done(*msg[1:])
                elif msg_type == "BATCH_SCAN":
                    self._scan_batch_editor()
                    self._batch_scan_done.set()
                elif msg_type == "TASK_ACTIVE":
                    _, task_id, cur_idx, total_cnt, label = msg
                    self._on_task_active(task_id, cur_idx, total_cnt, label)
                elif msg_type == "TASK_ANNOTATE":
                    _, task_id, text = msg
                    line_no = self._task_line(task_id)
                    if line_no:
                        self._replace_editor_line(line_no, text)
                elif msg_type == "TASK_DONE":
                    _, task_id, is_ok = msg
                    self._on_task_done(task_id, is_ok)
                elif msg_type == "TASK_VERIFIED_BAD":
                    line_no = self._task_line(msg[1])
                    if line_no:
                        self._batch_editor.tag_add("fail_batch_line", f"{line_no}.0", f"{line_no}.end")
                elif msg_type == "BATCH_FINISHED":
                    self._on_batch_finished()
                elif msg_type == "BATCH_LINE_DONE":
                    _, line_no, is_ok = msg
                    self._batch_editor.tag_remove("active_batch_line", f"{line_no}.0", f"{line_no}.end")
                    tag = "done_batch_line" if is_ok else "fail_batch_line"
                    self._batch_editor.tag_add(tag, f"{line_no}.0", f"{line_no}.end")
                elif msg_type == "RETRY_HIGHLIGHT":
                    _, line_no, cur_idx, total_cnt, url = msg
                    self._retry_list.configure(state="normal")
                    self._retry_list.tag_remove("active_batch_line", "1.0", END)
                    self._retry_list.tag_add("active_batch_line", f"{line_no}.0", f"{line_no}.end")
                    self._retry_list.see(f"{line_no}.0")
                    self._retry_list.configure(state="disabled")
                    self._status_var.set(f"[{cur_idx}/{total_cnt}] Retrying line {line_no}: {url}")
                elif msg_type == "RETRY_LINE_DONE":
                    _, line_no, is_ok = msg
                    self._retry_list.configure(state="normal")
                    self._retry_list.tag_remove("active_batch_line", f"{line_no}.0", f"{line_no}.end")
                    tag = "done_batch_line" if is_ok else "fail_batch_line"
                    self._retry_list.tag_add(tag, f"{line_no}.0", f"{line_no}.end")
                    self._retry_list.configure(state="disabled")
                elif msg_type == "PROGRESS":
                    _, overall, task = msg
                    self._update_progress(overall, task)
                elif msg_type == "DONE":
                    success = msg[1]
                    failed = msg[2] if len(msg) > 2 else []
                    self._on_download_done(success, failed)
                elif msg_type == "STATUS":
                    self._status_var.set(msg[1])
                elif msg_type == "BAN":
                    self._on_ban_changed(msg[1])
                elif msg_type == "IMAGE_SAVED":
                    self._session_images += 1
                    self._refresh_session_counter()
                elif msg_type == "UPDATE":
                    self._on_update_result(msg[1], msg[2], msg[3])
        except queue.Empty:
            pass
        # While a batch runs, hand newly added lines to it (about twice a second)
        if self._batch_accepting:
            self._poll_ticks = getattr(self, "_poll_ticks", 0) + 1
            if self._poll_ticks % 5 == 0:
                self._scan_batch_editor()
        self.after(100, self._poll_queue)

    def _append_log(self, ts: str, event: str, details: str) -> None:
        """Write one line to the log box (and session.log), coloured by its severity keywords."""
        append_session_log(event, details)
        self._log_box.configure(state="normal")
        self._log_box.insert(END, f"[{ts}] ", "ts")
        self._log_box.insert(END, f"{event}: ", "event")
        # colour the detail line by severity keyword
        tag = "INFO"
        low = (event + " " + details).lower()
        if any(w in low for w in ("error", "fail", "exception", "critical")):
            tag = "ERROR"
        elif any(w in low for w in ("warn", "notice", "skip", "already present", "pause", "rate limit", "waiting", "limit")):
            tag = "WARNING"
        elif any(w in low for w in ("success", "complete", "downloaded", "renamed", "created", "ok")):
            tag = "ok"
        self._log_box.insert(END, f"{details}\n", tag)
        self._log_box.see(END)
        self._log_box.configure(state="disabled")

    def _update_progress(self, overall: float, task: float) -> None:
        """Set both progress bars from 0.0-1.0 fractions."""
        self._bar1["value"] = overall * 100
        self._bar1_pct.configure(text=f"{overall*100:.0f}%")
        self._bar2["value"] = task * 100
        self._bar2_pct.configure(text=f"{task*100:.0f}%")

    def _on_format_changed(self, _event=None) -> None:
        """Warn the user that forcing a fixed format drops animations."""
        new_format = self._format_var.get()
        if new_format in ("JPG (.jpg)", "PNG (.png)", "WEBP (.webp)"):
            messagebox.showwarning(
                "Animated Images Warning",
                "You selected a fixed image format.\n"
                "Any animated images in the album (animated WebP/GIF) will lose their animation "
                "and only the first static frame will be saved.\n\n"
                "Tip: use 'AUTO' to convert static images to JPG while keeping animations.",
            )
        self._save_settings()

    def _on_speed_changed(self, _value=None) -> None:
        """Speed chosen in the menu: apply it at once (parallel downloads from the next album)."""
        level = int(self._speed_var.get())
        if level == throttle.get_speed():
            return
        throttle.set_speed(level)
        self._refresh_speed_label()
        self._save_settings()
        if self._running:
            self._append_log(
                _ts(), "Speed",
                f"Speed set to {throttle.speed_name(level)}: request pace changes now, "
                "parallel downloads from the next album.",
            )

    def _refresh_speed_label(self) -> None:
        """Show the speed name, in orange when it is risky for an IP ban."""
        level = throttle.get_speed()
        workers = throttle.SPEED_LEVELS[level][2]
        if not hasattr(self, "_speed_lbl"):
            return
        self._speed_lbl.configure(
            text=f"Speed: {level}x {throttle.speed_name(level)} ({workers} parallel)",
            fg=style.WARNING if level >= 5 else style.TEXT_SEC, bg=style.BG,
        )

    def _on_ban_changed(self, until: float) -> None:
        """A worker reported an IP ban (until > 0) or the ban was cleared (until == 0)."""
        self._ban_until = until
        if until > 0:
            self._resume_btn.pack(side="right", padx=(0, PAD), before=self._status_lbl)
            self._status_lbl.configure(foreground=style.WARNING)
            if self._ban_tick_job is None:
                self._ban_tick()
        else:
            self._end_ban_display("▶ Downloads resumed.")

    def _ban_tick(self) -> None:
        """Update the ban countdown in the status bar once per second."""
        self._ban_tick_job = None
        remaining = int(throttle.ban_remaining())
        if throttle.is_paused() and remaining > 0:
            self._ban_tick_job = self.after(1000, self._ban_tick)
            return
        if remaining <= 0:
            self._end_ban_display("▶ Ban expired: downloads resumed.")
            return
        source = throttle.ban_source()
        what = "IP banned by E-Hentai" if source == "E-Hentai" else f"Blocked by {source} (too many requests)"
        self._status_var.set(
            f"⏸ {what}: paused, resuming in {remaining // 60}:{remaining % 60:02d} "
            "(changed IP with a VPN? press Resume now)"
        )
        self._ban_tick_job = self.after(1000, self._ban_tick)

    def _end_ban_display(self, message: str) -> None:
        """Hide the ban countdown and the 'Resume now' button."""
        if self._ban_tick_job is not None:
            self.after_cancel(self._ban_tick_job)
            self._ban_tick_job = None
        self._ban_until = 0.0
        self._resume_btn.pack_forget()
        self._status_lbl.configure(foreground=style.TEXT_SEC)
        if self._running:
            self._status_var.set(message)

    def _toggle_pause(self) -> None:
        """Pause or resume the running download; on pause the batch list is saved to disk."""
        if not self._running:
            return
        if not throttle.is_paused():
            throttle.set_paused(True)
            self._save_urls_txt(quiet=True)
            append_session_banner("DOWNLOAD PAUSED BY THE USER")
            self._pause_btn.configure(text="▶  Resume")
            self._status_var.set(
                "⏸ Paused: list and partial album saved. Press Resume, or close the program "
                "and press Start Batch next time."
            )
            self._append_log(
                _ts(), "Pause",
                "Downloads paused (images already being received finish first). URLs.txt is "
                "saved and the album in progress stays in temp/: you can close the program, "
                "the next Start Batch resumes from this album skipping the pages already on disk.",
            )
        else:
            throttle.set_paused(False)
            append_session_banner("DOWNLOAD RESUMED BY THE USER")
            self._pause_btn.configure(text="⏸  Pause")
            self._status_var.set("▶ Downloads resumed.")
            self._append_log(_ts(), "Resume", "Downloads resumed.")

    def _resume_after_ban(self) -> None:
        """'Resume now': stop waiting (useful after switching IP). If the ban is still on,
        the next request detects it again and the pause restarts."""
        throttle.clear_ban()

    @staticmethod
    def _load_settings() -> dict:
        """Read the GUI choices saved by the last run (empty dict if missing or unreadable)."""
        try:
            data = json.loads(Path(SETTINGS_FILE).read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_settings(self) -> None:
        """Save the current GUI choices so they are restored at the next start."""
        data = self._settings  # keeps the other keys too (theme, start-up notice, ...)
        if hasattr(self, "_format_var"):
            data["image_format"] = self._format_var.get()
        data.update({
            "auto_rename": bool(self._rename_var.get()),
            "cbz_archive": bool(self._cbz_var.get()),
            "speed": int(self._speed_var.get()),
            "theme": self._theme_choice.get(),
            "comic_info": bool(self._comic_info_var.get()),
            "watch_clipboard": bool(self._watch_clipboard_var.get()),
            "check_updates": bool(self._check_updates_var.get()),
        })
        path = Path(SETTINGS_FILE)
        tmp = path.with_name(f"{path.name}.part")
        try:
            tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
            os.replace(tmp, path)
        except OSError as err:
            logging.warning(f"Could not save {SETTINGS_FILE}: {err}")

    def _on_rename_changed(self) -> None:
        """Settings → Auto Rename toggled."""
        self._save_settings()
        state = "ON" if self._rename_var.get() else "OFF"
        self._append_log(_ts(), "Settings", f"Auto Rename {state}: images renamed 01…N when an album is complete."
                         if state == "ON" else "Auto Rename OFF: images keep their download names.")

    def _on_cbz_changed(self) -> None:
        """Settings → CBZ archive toggled."""
        self._save_settings()
        self._append_log(_ts(), "Settings", "CBZ archive ON: each completed album is saved as a .cbz file."
                         if self._cbz_var.get() else "CBZ archive OFF: albums are saved as folders of images.")

    def _on_comic_info_changed(self) -> None:
        """Settings → ComicInfo.xml in CBZ toggled."""
        set_comic_info(self._comic_info_var.get())
        self._save_settings()
        self._append_log(_ts(), "Settings", "ComicInfo.xml ON: new CBZ archives include title, tags and source link."
                         if self._comic_info_var.get() else "ComicInfo.xml OFF: new CBZ archives hold only the images.")

    def _get_selected_format(self) -> str:
        """Map the combobox label to the internal format name (auto / original / jpg / png / webp)."""
        val = self._format_var.get().lower()
        if "auto" in val:
            return "auto"
        if "jpg" in val or "jpeg" in val:
            return "jpg"
        if "png" in val:
            return "png"
        if "webp" in val and "native" not in val:
            return "webp"
        return "original"

    def _set_buttons_state(self, state: str, editor_editable: bool = False) -> None:
        """Enable or disable every control that must not change during a download.

        editor_editable keeps the batch list editable (batch download: new links can be added).
        """
        for btn in (
            self._single_btn, self._single_paste_btn, self._batch_btn, self._retry_btn,
            self._verify_btn, self._dedupe_btn, self._reload_btn, self._clear_btn,
        ):
            btn.configure(state=state)
        # Verify / retry follow lines by number: the list must not change while they run
        self._batch_editor.configure(state="normal" if editor_editable else state)
        self._format_cb.configure(state="readonly" if state == "normal" else "disabled")
        # Auto Rename / CBZ are read when a download starts: lock them while one is running
        self._set_menu_options_state(state)

    def _clear_log(self) -> None:
        """Empty the log box (session.log is not touched)."""
        self._log_box.configure(state="normal")
        self._log_box.delete("1.0", END)
        self._log_box.configure(state="disabled")

    def _open_downloads_folder(self) -> None:
        """Open the Downloads folder in the system file manager."""
        folder = Path(DOWNLOAD_FOLDER)
        folder.mkdir(exist_ok=True)
        try:
            open_with_system(folder.resolve())
        except Exception as err:
            messagebox.showerror("Error", f"Could not open the Downloads folder:\n{err}")

    def _open_session_log(self) -> None:
        """Open the session.log file in the default system text viewer."""
        log_path = Path(SESSION_LOG).resolve()
        if not log_path.exists():
            log_path.touch()
        try:
            open_with_system(log_path)
        except Exception as err:
            messagebox.showerror("Error", f"Could not open the log file:\n{err}")

    def _guard_running(self) -> bool:
        """Return True (blocked) if a download is already in progress."""
        if self._running:
            messagebox.showwarning(
                "Busy",
                "A download is already in progress.\nWait for it to finish first.",
            )
            return True
        return False

    def _start_single(self) -> None:
        """Validate the URL and start a single-album download in a background thread."""
        if self._guard_running():
            return
        url = self._single_url.get().strip()
        if not url:
            messagebox.showwarning("Missing URL", "Please enter a gallery URL.")
            return
        from src.general_utils import validate_gallery_url
        url_error = validate_gallery_url(url)
        if url_error:
            messagebox.showerror("Invalid link", url_error)
            return

        fmt = self._get_selected_format()
        self._begin_download()
        self._bar1_lbl.configure(text="Album Total:")
        self._bar2_lbl.configure(text="Page Download:")
        self._status_var.set(f"Download [{fmt}]: {url}")
        threading.Thread(
            target=self._run_single, args=(url, fmt, self._rename_var.get(), self._cbz_var.get()), daemon=True,
        ).start()

    def _start_batch(self) -> None:
        """Save URLs.txt and start downloading every non-comment line in a background thread."""
        if self._guard_running():
            return
        self._save_urls_txt()
        self._clear_batch_marks()

        if not self._editor_entries():
            messagebox.showwarning(
                "No URLs",
                "URLs.txt is empty.\nAdd at least one URL before starting.",
            )
            return
        # 1) duplicates (with confirmation)  2) malformed links (offline check)
        if self._offer_duplicate_cleanup() == "cancel":
            return
        tasks = self._editor_entries()
        if not self._confirm_invalid_links(tasks, "Start the download"):
            return

        fmt = self._get_selected_format()
        self._begin_download(editor_editable=True)
        self._bar1_lbl.configure(text="Batch Total:")
        self._bar2_lbl.configure(text="Current Album:")
        self._clear_batch_marks()

        # Fresh live queue; the first scan claims every link currently in the list
        with self._batch_lock:
            self._batch_queue = queue.Queue()
            self._verify_queue = queue.Queue()
            self._batch_total = 0
            self._batch_keys = {}
            self._task_state = {}
            self._verify_bad = set()
            self._failed_task_ids = []
            self._batch_accepting = True
        self._batch_stop.clear()
        self._scan_batch_editor(live=False)
        to_check = self._verify_queue.qsize()

        self._status_var.set(f"Batch download [{fmt}]: {self._batch_total} URL(s)")
        if to_check:
            self._q.put(("LOG", _ts(), "Verify",
                         f"{to_check} link(s) not verified yet: checking them in parallel with the download."))
            threading.Thread(target=self._run_batch_verify, daemon=True).start()
        threading.Thread(
            target=self._run_batch, args=(fmt, self._rename_var.get(), self._cbz_var.get()), daemon=True,
        ).start()

    def _start_verify(self) -> None:
        """'Verify Links' button: check every link online without downloading anything."""
        if self._guard_running():
            return
        self._save_urls_txt()
        self._clear_batch_marks()
        if not self._editor_entries():
            messagebox.showwarning("No URLs", "The list is empty.")
            return
        if self._offer_duplicate_cleanup() == "cancel":
            return
        entries = self._editor_entries()
        self._begin_download()
        self._bar1_lbl.configure(text="Verified:")
        self._bar2_lbl.configure(text="")
        self._status_var.set(f"Verifying {len(entries)} link(s)…")
        threading.Thread(target=self._run_verify, args=(entries,), daemon=True).start()

    def _start_retry(self) -> None:
        """Start re-downloading every album in the failed queue in a background thread."""
        if self._guard_running():
            return
        from src.cli.retry import collect_retry_urls
        urls = collect_retry_urls()
        if not urls:
            messagebox.showinfo(
                "Nothing to Retry",
                "No failed URLs found in the queue.",
            )
            return

        fmt = self._get_selected_format()
        self._begin_download()
        self._bar1_lbl.configure(text="Retry Total:")
        self._bar2_lbl.configure(text="Current Album:")
        self._retry_list.configure(state="normal")
        self._retry_list.tag_remove("active_batch_line", "1.0", END)
        self._retry_list.tag_remove("done_batch_line", "1.0", END)
        self._retry_list.tag_remove("fail_batch_line", "1.0", END)
        self._retry_list.configure(state="disabled")
        self._status_var.set(f"Retrying [{fmt}] {len(urls)} failed URL(s)…")
        threading.Thread(
            target=self._run_retry, args=(fmt, self._rename_var.get(), self._cbz_var.get()), daemon=True,
        ).start()
