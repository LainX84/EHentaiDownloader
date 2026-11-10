"""EHentai Downloader – Graphical User Interface (single-file launcher).

Double-click this file on Windows to start the program without a console window
(.pyw files are opened by pythonw.exe). It also works from a terminal:

    python EHentaiDownloader.pyw

The window wraps the three command-line modes of the project:
  • Single Download  – one gallery URL            (same logic as src/cli/downloader.py)
  • Batch Download   – every URL in URLs.txt      (same logic as src/cli/main.py)
  • Retry Failed     – albums in failed_downloads.txt (same logic as src/cli/retry.py)

How it works:
  - Downloads run in a background thread, so the window never freezes.
  - GUILiveManager replaces the Rich terminal display used by the CLI: instead of
    drawing in a terminal, it pushes progress/log messages onto a thread-safe queue.
  - The window polls that queue every 100 ms (_poll_queue) and updates the widgets.
"""

from __future__ import annotations

import asyncio
import atexit
import datetime
import json
import logging
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from tkinter import (
    END,
    BooleanVar,
    StringVar,
    Text,
    Tk,
    filedialog,
    messagebox,
    scrolledtext,
)
import tkinter as tk
import tkinter.ttk as ttk

from src.config import (
    AUTOSAVE_INTERVAL,
    CREATE_CBZ,
    DEFAULT_SPEED,
    DONE_LINE_REMOVE_DELAY,
    RENAME_SEQUENTIAL,
    SESSION_LOG,
    SETTINGS_FILE,
)
from src import gui_dialogs, paths, throttle, ui_theme
from src.config import ISSUES_URL, PROJECT_URL
from src.file_utils import append_session_banner, append_session_log

# ---------------------------------------------------------------------------
# Colour / style constants
# ---------------------------------------------------------------------------
# Colours are module globals named like the keys of the src.ui_theme palettes. They start
# with the light palette; EHentaiGUI._apply_theme() swaps them (and every widget) at runtime.
BG = ui_theme.LIGHT["BG"]
PANEL = ui_theme.LIGHT["PANEL"]
ACCENT = ui_theme.LIGHT["ACCENT"]
ACCENT2 = ui_theme.LIGHT["ACCENT2"]
SUCCESS = ui_theme.LIGHT["SUCCESS"]
WARNING = ui_theme.LIGHT["WARNING"]
ERROR = ui_theme.LIGHT["ERROR"]
MUTED = ui_theme.LIGHT["MUTED"]
BORDER = ui_theme.LIGHT["BORDER"]
TEXT = ui_theme.LIGHT["TEXT"]
TEXT_SEC = ui_theme.LIGHT["TEXT_SEC"]
TAB_HOVER = ui_theme.LIGHT["TAB_HOVER"]
TASK_BAR = ui_theme.LIGHT["TASK_BAR"]
ON_ACCENT = ui_theme.LIGHT["ON_ACCENT"]
DANGER_ACTIVE = ui_theme.LIGHT["DANGER_ACTIVE"]
SELECT_BG = ui_theme.LIGHT["SELECT_BG"]
RETRY_BG = ui_theme.LIGHT["RETRY_BG"]
LINE_QUEUED_BG = ui_theme.LIGHT["LINE_QUEUED_BG"]
LINE_ACTIVE_BG = ui_theme.LIGHT["LINE_ACTIVE_BG"]
LINE_ACTIVE_FG = ui_theme.LIGHT["LINE_ACTIVE_FG"]
LINE_DONE_BG = ui_theme.LIGHT["LINE_DONE_BG"]
LINE_DONE_FG = ui_theme.LIGHT["LINE_DONE_FG"]
LINE_FAIL_BG = ui_theme.LIGHT["LINE_FAIL_BG"]
LINE_FAIL_FG = ui_theme.LIGHT["LINE_FAIL_FG"]
OK_BANNER = ui_theme.LIGHT["OK_BANNER"]
WARN_BANNER = ui_theme.LIGHT["WARN_BANNER"]

_UI_FAMILY, _MONO_FAMILY = ui_theme.font_families()
FONT_UI    = (_UI_FAMILY,   10)
FONT_SMALL = (_UI_FAMILY,    9)
FONT_MONO  = (_MONO_FAMILY,  9)
FONT_MONO_BOLD = (_MONO_FAMILY, 9, "bold")
FONT_TITLE = (_UI_FAMILY,   16, "bold")
FONT_HEAD  = (_UI_FAMILY,   11, "bold")
FONT_BTN   = (_UI_FAMILY,   10, "bold")
DIALOG_FONTS = {"ui": FONT_UI, "small": FONT_SMALL, "mono": FONT_MONO, "title": FONT_TITLE, "head": FONT_HEAD}

PAD = 12
TAB_WIDTH = 18  # characters: the three tabs are always the same size

# "When done" choices (status bar). Never remembered between runs: a forgotten
# "Shut down PC" must not switch the computer off at the next, unrelated download.
WHEN_DONE_NOTHING = "Do nothing"
WHEN_DONE_CLOSE = "Close program"
WHEN_DONE_SHUTDOWN = "Shut down PC"
WHEN_DONE_OPTIONS = (WHEN_DONE_NOTHING, WHEN_DONE_CLOSE, WHEN_DONE_SHUTDOWN)
WHEN_DONE_COUNTDOWN = 60  # seconds to cancel before closing / shutting down


# ---------------------------------------------------------------------------
# GUI-compatible Live Manager
# ---------------------------------------------------------------------------

class GUILiveManager:
    """Drop-in replacement for LiveManager that routes events to the GUI.

    The real LiveManager drives a Rich Live terminal display; this one
    instead puts messages onto a thread-safe queue that the GUI polls.
    It exposes the same public API used by AlbumDownloader / src/cli/downloader.py,
    plus intuitive progress tracking for single and batch downloads.
    """

    class _FakeLive:
        """Context-manager stub so 'with live_manager.live:' still works."""
        def __enter__(self):
            """Do nothing (context-manager stub)."""
            return self
        def __exit__(self, *_):
            """Do nothing (context-manager stub)."""
            pass
        def start(self):
            """Do nothing."""
            pass
        def stop(self):
            """Do nothing."""
            pass
        def update(self, _):
            """Do nothing."""
            pass

    def __init__(
        self,
        log_queue: queue.Queue,
        mode: str = "single",  # "single", "batch", or "retry"
        batch_idx: int = 0,    # 0-based index of album in batch
        batch_total: int = 1,  # total albums in batch
        line_no: int | None = None,
        progress_callback=None,
    ) -> None:
        """Set up counters and remember which mode (single / batch / retry) this album belongs to."""
        self.live = self._FakeLive()
        self._log_queue = log_queue
        self.mode = mode
        self.batch_idx = batch_idx
        self.batch_total = max(1, batch_total)
        self.line_no = line_no
        self._progress_callback = progress_callback

        self._lock = threading.Lock()
        self._start_time = time.time()

        # Detailed album & page tracking
        self._album_title: str = ""
        self._album_total: int = 0
        self._album_done: int = 0
        self._active_stream_page: int | None = None

        # Legacy CLI compatibility counters
        self._overall_total: int = 0
        self._overall_done: int = 0
        self._task_totals: dict[int, int] = {}
        self._task_done:   dict[int, int] = {}
        self._next_task_id: int = 0

        magick = shutil.which("magick") or shutil.which("convert")
        engine = f"ImageMagick ({magick})" if magick else "Pillow (PIL)"
        self._log("Engine", f"Image engine: {engine}")

    def start_album(self, album_title: str, total_images: int) -> None:
        """Called when an album begins downloading."""
        with self._lock:
            self._album_title = album_title
            self._album_total = max(1, total_images)
            self._album_done = 0
            self._active_stream_page = None

        if self.mode == "single":
            self._log_queue.put(("SET_LABELS", "Album Total:", "Page Download:"))
            self._log_queue.put((
                "PROGRESS_BARS",
                0.0, f"0/{self._album_total} (0%)",
                0.0, "0%",
            ))
        else:
            line_str = f" (Line {self.line_no})" if self.line_no else ""
            lbl1 = "Batch Total:" if self.mode == "batch" else "Retry Total:"
            lbl2 = f"Current Album{line_str}:"
            self._log_queue.put(("SET_LABELS", lbl1, lbl2))
            batch_pct = (self.batch_idx / self.batch_total) * 100
            self._log_queue.put((
                "PROGRESS_BARS",
                self.batch_idx / self.batch_total,
                f"{self.batch_idx}/{self.batch_total} ({batch_pct:.0f}%)",
                0.0,
                f"0/{self._album_total} (0%)",
            ))

    def update_album_progress(self, done: int, total: int, page_idx: int = 0) -> None:
        """Called whenever an image finishes downloading or is skipped."""
        with self._lock:
            self._album_done = done
            self._album_total = max(total, self._album_total, 1)

            album_frac = min(1.0, self._album_done / self._album_total)
            album_text = f"{self._album_done}/{self._album_total} ({album_frac * 100:.0f}%)"

            if self.mode == "single":
                # Bar 1 tracks album progress
                self._log_queue.put(("UPDATE_BAR1", album_frac, album_text))
            else:
                # Bar 1 is batch progress, Bar 2 is current album!
                batch_frac = min(1.0, (self.batch_idx + album_frac) / self.batch_total)
                batch_text = f"{self.batch_idx}/{self.batch_total} ({batch_frac * 100:.0f}%)"
                self._log_queue.put((
                    "PROGRESS_BARS",
                    batch_frac,
                    batch_text,
                    album_frac,
                    album_text,
                ))

    def update_page_stream(self, page_idx: int, downloaded: int, total: int) -> None:
        """Stream byte progress of the single page currently being downloaded."""
        with self._lock:
            if self.mode != "single":
                return  # In batch mode, Bar 2 is dedicated to the current album progress
            if self._active_stream_page is None or self._active_stream_page == page_idx:
                self._active_stream_page = page_idx
                pct = (downloaded / total) if total > 0 else 0.0
                if total > 0:
                    text = f"{downloaded // 1024} KB / {total // 1024} KB ({pct * 100:.0f}%)"
                else:
                    text = f"{downloaded // 1024} KB"
                self._log_queue.put(("UPDATE_BAR2", pct, text))

    def finish_page_stream(self, page_idx: int) -> None:
        """Mark single page download as complete."""
        with self._lock:
            if self.mode != "single":
                return
            if self._active_stream_page == page_idx:
                self._active_stream_page = None
                self._log_queue.put(("UPDATE_BAR2", 1.0, "100%"))

    # ---- legacy compatibility methods ----

    def add_overall_task(self, description: str, num_tasks: int) -> None:
        """Legacy API: remember how many gallery pages the album has."""
        with self._lock:
            self._overall_total = num_tasks
            self._overall_done  = 0

    def add_task(self, current_task: int = 0, total: int = 100) -> int:
        """Legacy API: register a gallery page and return its task id."""
        with self._lock:
            tid = self._next_task_id
            self._next_task_id += 1
            self._task_totals[tid] = total
            self._task_done[tid]   = 0
        return tid

    def update_task(
        self,
        task_id: int,
        completed: int | None = None,
        advance: int = 0,
        *,
        visible: bool = True,
    ) -> None:
        """Legacy API: advance the counter of a gallery page."""
        with self._lock:
            if task_id not in self._task_done:
                return
            if completed is not None:
                self._task_done[task_id] = completed
            else:
                self._task_done[task_id] += advance

    def update_log(self, event: str, details: str) -> None:
        """Send a log event to the GUI."""
        self._log(event, details)

    def start(self) -> None:
        """No-op: kept for API compatibility with the terminal LiveManager."""
        pass

    def stop(self) -> None:
        """Log how long the album took."""
        elapsed = time.time() - self._start_time
        td = datetime.timedelta(seconds=int(elapsed))
        h  = td.seconds // 3600
        m  = (td.seconds % 3600) // 60
        s  = td.seconds % 60
        self._log(
            "Time",
            f"Elapsed time: {h:02}h {m:02}m {s:02}s",
        )

    # ---- private helpers ----

    def _log(self, event: str, details: str) -> None:
        """Put a timestamped log message on the GUI queue."""
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        self._log_queue.put(("LOG", ts, event, details))


# ---------------------------------------------------------------------------
# Logging handler that forwards to the GUI queue
# ---------------------------------------------------------------------------

class _QueueLogHandler(logging.Handler):
    """Route Python logging records to the GUI message queue."""

    def __init__(self, q: queue.Queue) -> None:
        """Store the GUI queue that receives log records."""
        super().__init__()
        self._q = q

    def emit(self, record: logging.LogRecord) -> None:
        # Ignore DEBUG logs completely to eliminate noisy requests / PIL imports
        """Forward one logging record to the GUI queue."""
        if record.levelno < logging.INFO:
            return
        level = record.levelname  # INFO / WARNING / ERROR / CRITICAL
        ts    = datetime.datetime.fromtimestamp(record.created).strftime("%H:%M:%S")
        self._q.put(("LOG", ts, level, self.format(record)))


# ---------------------------------------------------------------------------
# Styled helpers
# ---------------------------------------------------------------------------

def _card(parent, **kw) -> ttk.Frame:
    """Return a white rounded-looking card frame."""
    f = ttk.Frame(parent, style="Card.TFrame", **kw)
    return f


def _btn(parent, text: str, command, style="Accent.TButton", **kw) -> ttk.Button:
    """Create a themed ttk button (blue 'Accent' style by default)."""
    return ttk.Button(parent, text=text, command=command, style=style, **kw)


# ---------------------------------------------------------------------------
# Main application window
# ---------------------------------------------------------------------------

class EHentaiGUI(tk.Tk):

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
        self.configure(bg=BG)
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
        self.bind("<Configure>", self._on_window_resize)
        self.protocol("WM_DELETE_WINDOW", self._on_window_close)
        atexit.register(self._on_exit)

    # ------------------------------------------------------------------ styles

    def _setup_styles(self) -> None:
        """Configure the light ttk theme (colours, fonts, buttons, progress bars)."""
        s = ttk.Style(self)
        s.theme_use("clam")

        # General
        s.configure(".",             background=BG, foreground=TEXT,
                     font=FONT_UI,  borderwidth=0, relief="flat",
                     bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER,
                     troughcolor=BORDER, fieldbackground=PANEL, insertcolor=TEXT,
                     selectbackground=ACCENT, selectforeground=ON_ACCENT)
        s.configure("TFrame",        background=BG)
        s.configure("Card.TFrame",   background=PANEL, relief="flat")

        # Notebook / tabs
        s.configure("TNotebook", background=BG, borderwidth=0, tabmargins=(0, 0, 0, 0))
        # Every tab has the same size, selected or not (clam enlarges the selected one by default):
        # fixed width in characters, centred text, no "expand"; the selected tab is shown by colour.
        s.configure("TNotebook.Tab",
                    background=BORDER, foreground=TEXT_SEC,
                    padding=(14, 7), font=FONT_UI, width=TAB_WIDTH, anchor="center",
                    bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER)
        s.map("TNotebook.Tab",
              expand=[("selected", (0, 0, 0, 0))],
              padding=[("selected", (14, 7))],
              background=[("selected", ACCENT), ("active", TAB_HOVER)],
              foreground=[("selected", ON_ACCENT), ("active", ACCENT)],
              lightcolor=[("selected", ACCENT)],
              bordercolor=[("selected", ACCENT)])
        # Same layout without the dotted focus ring
        s.layout("TNotebook.Tab", [
            ("Notebook.tab", {"sticky": "nswe", "children": [
                ("Notebook.padding", {"side": "top", "sticky": "nswe", "children": [
                    ("Notebook.label", {"side": "top", "sticky": ""}),
                ]}),
            ]}),
        ])

        # Buttons
        s.configure("Accent.TButton",
                    background=ACCENT, foreground=ON_ACCENT,
                    font=FONT_BTN, padding=(16, 8),
                    relief="flat", borderwidth=0)
        s.map("Accent.TButton",
              background=[("active", ACCENT2), ("disabled", BORDER)],
              foreground=[("disabled", MUTED)])

        s.configure("Ghost.TButton",
                    background=PANEL, foreground=ACCENT,
                    font=FONT_UI, padding=(10, 6),
                    relief="flat", borderwidth=1)
        s.map("Ghost.TButton",
              background=[("active", TAB_HOVER), ("disabled", PANEL)],
              foreground=[("disabled", MUTED)])

        s.configure("Danger.TButton",
                    background=ERROR, foreground=ON_ACCENT,
                    font=FONT_BTN, padding=(16, 8),
                    relief="flat", borderwidth=0)
        s.map("Danger.TButton",
              background=[("active", DANGER_ACTIVE), ("disabled", BORDER)])

        # Labels
        s.configure("Title.TLabel",
                    background=BG, foreground=TEXT,   font=FONT_TITLE)
        s.configure("Head.TLabel",
                    background=PANEL, foreground=TEXT, font=FONT_HEAD)
        s.configure("Sub.TLabel",
                    background=PANEL, foreground=TEXT_SEC, font=FONT_SMALL)
        s.configure("Status.TLabel",
                    background=BG, foreground=TEXT_SEC, font=FONT_SMALL)

        # Entry
        s.configure("TEntry",
                    fieldbackground=PANEL, foreground=TEXT,
                    insertcolor=TEXT, borderwidth=1,
                    relief="solid", padding=(8, 6))

        # Progressbar — copy the qualified horizontal layout before creating a variant
        s.configure("TProgressbar",
                    troughcolor=BORDER, background=ACCENT,
                    thickness=10, borderwidth=0)
        s.layout("Horizontal.Task.TProgressbar",
                 s.layout("Horizontal.TProgressbar"))
        s.configure("Task.TProgressbar",
                    troughcolor=BORDER, background=TASK_BAR,
                    thickness=10, borderwidth=0)

        # Separator
        s.configure("TSeparator", background=BORDER)

        # Scrollbar
        s.configure("TScrollbar",
                    background=BORDER, troughcolor=BG, bordercolor=BG,
                    lightcolor=BORDER, darkcolor=BORDER,
                    arrowcolor=TEXT_SEC, borderwidth=0)
        s.map("TScrollbar",
              background=[("active", MUTED), ("disabled", BG)],
              arrowcolor=[("disabled", MUTED)])
        for orient in ("Vertical", "Horizontal"):
            s.configure(f"{orient}.TScrollbar", background=BORDER, troughcolor=BG, bordercolor=BG,
                        lightcolor=BORDER, darkcolor=BORDER, arrowcolor=TEXT_SEC, gripcount=0)
            s.map(f"{orient}.TScrollbar", background=[("active", MUTED), ("disabled", BG)])

        # Combobox (also the read-only look, used by every combobox of the window)
        s.configure("TCombobox",
                    fieldbackground=PANEL, background=PANEL, foreground=TEXT,
                    arrowcolor=TEXT, bordercolor=BORDER, lightcolor=PANEL, darkcolor=PANEL,
                    selectbackground=PANEL, selectforeground=TEXT)
        s.map("TCombobox",
              fieldbackground=[("readonly", PANEL), ("disabled", BG)],
              foreground=[("readonly", TEXT), ("disabled", MUTED)],
              background=[("active", TAB_HOVER), ("readonly", PANEL)],
              selectbackground=[("readonly", PANEL)],
              selectforeground=[("readonly", TEXT)],
              arrowcolor=[("disabled", MUTED)])

        # Check buttons in dialogs
        s.configure("TCheckbutton", background=PANEL, foreground=TEXT_SEC)

        # Entry: same border as the editors
        s.map("TEntry", bordercolor=[("focus", ACCENT)], lightcolor=[("focus", ACCENT)])

    # ------------------------------------------------------------------ menu bar

    def _menu(self, parent) -> tk.Menu:
        """A drop-down menu with the theme colours (Linux / macOS draw them with Tk)."""
        return tk.Menu(
            parent, tearoff=0, bg=PANEL, fg=TEXT, activebackground=ACCENT, activeforeground=ON_ACCENT,
            selectcolor=ACCENT, disabledforeground=MUTED, relief="flat", bd=1,
        )

    def _build_menu(self) -> None:
        """File / Settings / Help menus."""
        self._menubar_frame = None
        if sys.platform != "darwin":
            # Windows draws its own menu bar (always light): use one made of themed buttons
            self._menubar_frame = tk.Frame(self, bg=PANEL)
            self._menubar_frame.pack(side="top", fill="x")
            tk.Frame(self, bg=BORDER, height=1).pack(side="top", fill="x")
        else:
            self._menubar = self._menu(self)   # macOS: native menu bar at the top of the screen

        file_menu = self._file_menu = self._top_menu("File")
        file_menu.add_command(label="Open Downloads folder", command=self._open_downloads_folder)
        file_menu.add_command(label="Open log file", command=self._open_session_log)
        file_menu.add_separator()
        file_menu.add_command(label="Account & cookies…", command=self._open_account_dialog)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self._on_window_close)

        settings = self._settings_menu = self._top_menu("Settings")
        settings.add_checkbutton(label="Auto Rename (01…N when complete)", variable=self._rename_var_init(),
                                 command=self._on_rename_changed)
        settings.add_checkbutton(label="CBZ archive", variable=self._cbz_var_init(),
                                 command=self._on_cbz_changed)
        settings.add_separator()

        self._speed_var = tk.IntVar(value=throttle.get_speed())
        speed_menu = self._speed_menu = self._menu(settings)
        for level in range(throttle.MIN_SPEED, throttle.MAX_SPEED + 1):
            name, pause, workers = throttle.SPEED_LEVELS[level]
            speed_menu.add_radiobutton(
                label=f"{level}x  {name}  ({workers} parallel, {pause:g} s between pages)",
                variable=self._speed_var, value=level, command=self._on_speed_changed,
            )
        settings.add_cascade(label="Speed", menu=speed_menu)

        self._when_done_var = StringVar(value=WHEN_DONE_NOTHING)
        done_menu = self._menu(settings)
        for choice in WHEN_DONE_OPTIONS:
            done_menu.add_radiobutton(label=choice, variable=self._when_done_var, value=choice,
                                      command=self._on_when_done_changed)
        settings.add_cascade(label="When done", menu=done_menu)
        settings.add_separator()

        theme_menu = self._menu(settings)
        for choice in ui_theme.THEME_CHOICES:
            theme_menu.add_radiobutton(label=ui_theme.THEME_LABELS[choice], variable=self._theme_choice,
                                       value=choice, command=self._on_theme_changed)
        settings.add_cascade(label="Theme", menu=theme_menu)

        help_menu = self._top_menu("Help")
        help_menu.add_command(label="GitHub page", command=lambda: gui_dialogs.open_url(PROJECT_URL))
        help_menu.add_command(label="Report a problem…", command=lambda: gui_dialogs.open_url(ISSUES_URL))
        help_menu.add_separator()
        help_menu.add_command(label="Disclaimer", command=lambda: gui_dialogs.show_disclaimer(
            self, self._palette(), DIALOG_FONTS, self._settings, self._save_settings, startup=False))
        help_menu.add_command(label="License", command=lambda: gui_dialogs.show_license(
            self, self._palette(), DIALOG_FONTS))
        help_menu.add_command(label="About", command=lambda: gui_dialogs.show_about(
            self, self._palette(), DIALOG_FONTS))
        if self._menubar_frame is None:
            self.configure(menu=self._menubar)

    def _top_menu(self, label: str) -> tk.Menu:
        """One menu of the menu bar (themed button + drop-down, or native on macOS)."""
        if self._menubar_frame is None:
            menu = self._menu(self._menubar)
            self._menubar.add_cascade(label=label, menu=menu)
            return menu
        button = tk.Menubutton(
            self._menubar_frame, text=label, bg=PANEL, fg=TEXT, font=FONT_SMALL,
            activebackground=TAB_HOVER, activeforeground=ACCENT, relief="flat", bd=0,
            padx=10, pady=4, cursor="hand2", underline=0,
        )
        menu = self._menu(button)
        button.configure(menu=menu)
        button.pack(side="left")
        # Alt+F / Alt+S / Alt+H open the menus, like a native menu bar
        self.bind_all(f"<Alt-{label[0].lower()}>", lambda _e, b=button, m=menu: m.post(
            b.winfo_rootx(), b.winfo_rooty() + b.winfo_height()))
        return menu

    def _rename_var_init(self) -> BooleanVar:
        """Create the Auto Rename variable from the saved settings (menu is built first)."""
        self._rename_var = BooleanVar(value=bool(self._settings.get("auto_rename", RENAME_SEQUENTIAL)))
        return self._rename_var

    def _cbz_var_init(self) -> BooleanVar:
        """Create the CBZ archive variable from the saved settings."""
        self._cbz_var = BooleanVar(value=bool(self._settings.get("cbz_archive", CREATE_CBZ)))
        return self._cbz_var

    def _set_menu_options_state(self, state: str) -> None:
        """Lock Auto Rename / CBZ archive while a download runs (they are read at its start)."""
        for index in (0, 1):
            self._settings_menu.entryconfigure(index, state=state)

    # ------------------------------------------------------------------ theme

    def _palette(self) -> dict:
        """Colours of the theme in use."""
        return ui_theme.PALETTES[self._theme_active]

    def _apply_theme(self, theme: str, initial: bool = False) -> None:
        """Switch every colour of the window (and open dialogs) to the given palette."""
        if theme == self._theme_active and not initial:
            return
        old = self._theme_active
        globals().update(ui_theme.PALETTES[theme])   # used by widgets created from now on
        self._theme_active = theme
        self._setup_styles()                          # ttk styles
        self.option_add("*TCombobox*Listbox.background", PANEL)
        self.option_add("*TCombobox*Listbox.foreground", TEXT)
        self.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
        self.option_add("*TCombobox*Listbox.selectForeground", ON_ACCENT)
        if old != theme:
            ui_theme.recolor_tree(self, ui_theme.color_map(old, theme))
            for combo in (self._format_cb, self._when_done_cb):
                try:  # drop-down list already created: recolour it too
                    popdown = self.tk.eval(f"ttk::combobox::PopdownWindow {combo}")
                    self.tk.eval(f"{popdown}.f.l configure -background {PANEL} -foreground {TEXT} "
                                 f"-selectbackground {ACCENT} -selectforeground {ON_ACCENT}")
                except tk.TclError:
                    pass
        self._refresh_speed_label()
        ui_theme.set_windows_title_bar(self, theme == ui_theme.THEME_DARK)

    def _on_theme_changed(self) -> None:
        """Theme chosen in the menu."""
        self._apply_theme(ui_theme.resolve(self._theme_choice.get()))
        self._save_settings()

    def _follow_system_theme(self) -> None:
        """'Automatic': check the system theme every minute."""
        if self._closed:
            return
        if self._theme_choice.get() == ui_theme.THEME_AUTO:
            self._apply_theme(ui_theme.resolve(ui_theme.THEME_AUTO))
        self.after(60_000, self._follow_system_theme)

    # ------------------------------------------------------------------ account / start-up notice

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

    # ------------------------------------------------------------------ UI

    def _build_ui(self) -> None:
        # ── Header bar ──────────────────────────────────────────────────────
        """Build the header bar, status bar, log, progress bars and the tab notebook."""
        hdr = tk.Frame(self, bg=PANEL, height=64)
        hdr.pack(fill="x", side="top")
        hdr.pack_propagate(False)

        # Small logo next to the title
        try:
            self._header_logo = tk.PhotoImage(file=str(paths.resource("assets/icon_small.png")))
        except (tk.TclError, OSError):
            self._header_logo = None
        tk.Label(hdr, text="  EHentai Downloader", image=self._header_logo, compound="left",
                 bg=PANEL, fg=TEXT, font=FONT_TITLE).pack(
                     side="left", padx=PAD*2, pady=8)

        hdr_right = tk.Frame(hdr, bg=PANEL)
        hdr_right.pack(side="right", padx=PAD*2)

        # Global Image Format Selector
        tk.Label(hdr_right, text="Image Format:", bg=PANEL, fg=TEXT_SEC,
                 font=FONT_SMALL).pack(side="left", padx=(0, 6))

        self._format_options = [
            "AUTO (JPG / Native if animated)",
            "Native (Original)",
            "JPG (.jpg)",
            "PNG (.png)",
            "WEBP (.webp)",
        ]
        saved_format = self._settings.get("image_format")
        initial_format = saved_format if saved_format in self._format_options else self._format_options[0]
        self._format_var = StringVar(value=initial_format)
        self._prev_format = initial_format

        self._format_cb = ttk.Combobox(
            hdr_right,
            textvariable=self._format_var,
            values=self._format_options,
            state="readonly",
            width=28,
            font=FONT_SMALL,
        )
        self._format_cb.pack(side="left", padx=(0, PAD*2))
        self._format_cb.bind("<<ComboboxSelected>>", self._on_format_changed)

        # Auto Rename / CBZ archive / Speed / When done / Theme live in the "Settings" menu

        ttk.Separator(self, orient="horizontal").pack(side="top", fill="x")

        # ── Status bar (pinned to bottom) ───────────────────────────────────
        ttk.Separator(self, orient="horizontal").pack(side="bottom", fill="x")
        sb = tk.Frame(self, bg=BG)
        sb.pack(side="bottom", fill="x")
        # What to do when all downloads are finished (far right, like HandBrake)
        done_box = tk.Frame(sb, bg=BG)
        done_box.pack(side="right", padx=(0, PAD))
        tk.Label(done_box, text="When done:", bg=BG, fg=TEXT_SEC, font=FONT_SMALL).pack(side="left")
        self._when_done_cb = ttk.Combobox(
            done_box, textvariable=self._when_done_var, values=WHEN_DONE_OPTIONS,
            state="readonly", width=14, font=FONT_SMALL,
        )
        self._when_done_cb.pack(side="left", padx=(6, 0))
        self._when_done_cb.bind("<<ComboboxSelected>>", self._on_when_done_changed)
        self._when_done_dialog = None

        # Current speed (changed from Settings → Speed): fewer requests = less risk of a ban
        self._speed_lbl = tk.Label(sb, bg=BG, fg=TEXT, font=FONT_SMALL, anchor="w", cursor="hand2")
        self._speed_lbl.pack(side="right", padx=PAD)
        self._speed_lbl.bind("<Button-1>", lambda e: self._speed_menu.tk_popup(e.x_root, e.y_root))
        self._speed_box = self._speed_lbl

        # Shown only during an IP ban: skip the wait after switching IP (e.g. with a VPN)
        self._resume_btn = tk.Button(
            sb, text="▶  Resume now",
            bg=WARNING, fg=ON_ACCENT, activebackground=ERROR, activeforeground=ON_ACCENT,
            font=FONT_SMALL, relief="flat", cursor="hand2", padx=8, pady=0,
            command=self._resume_after_ban,
        )

        # Status text last: the speed controls keep their space, a long message is cut
        self._status_var = StringVar(value="Ready.")
        self._status_lbl = ttk.Label(sb, textvariable=self._status_var, style="Status.TLabel")
        self._status_lbl.pack(side="left", padx=PAD, pady=4)

        # ── Log area (pinned above status bar, expand=True) ─────────────────
        self._build_log_area()

        # ── Progress area (pinned above log area) ───────────────────────────
        self._build_progress_area()

        # ── Tab notebook (top area) ──────────────────────────────────────────
        self._nb = ttk.Notebook(self)
        self._nb.pack(side="top", fill="both", expand=True, padx=PAD, pady=(PAD, 4))

        self._tab_single = ttk.Frame(self._nb, style="TFrame")
        self._tab_batch  = ttk.Frame(self._nb, style="TFrame")
        self._tab_retry  = ttk.Frame(self._nb, style="TFrame")

        self._nb.add(self._tab_single, text="🔗  Single Download")
        self._nb.add(self._tab_batch,  text="📋  Batch Download")
        self._nb.add(self._tab_retry,  text="🔄  Retry Failed")

        # "Open Downloads" sits on the tab-bar row, right-aligned: always visible, whatever tab is open
        self._open_downloads_btn = tk.Button(
            self, text="📁  Open Downloads",
            bg=ACCENT, fg=ON_ACCENT, activebackground=ACCENT2, activeforeground=ON_ACCENT,
            font=FONT_SMALL, relief="flat", cursor="hand2", padx=10, pady=2,
            command=self._open_downloads_folder,
        )
        self._open_downloads_btn.place(in_=self._nb, relx=1.0, x=-2, y=1, anchor="ne")
        self._open_downloads_btn.lift()

        self._build_single_tab()
        self._build_batch_tab()
        self._build_retry_tab()

    # ── Single tab ──────────────────────────────────────────────────────────

    def _build_single_tab(self) -> None:
        """Build the 'Single Download' tab: one URL field plus Paste / Start buttons."""
        tab = self._tab_single
        tab.columnconfigure(0, weight=1)

        card = _card(tab)
        card.grid(row=0, column=0, padx=PAD, pady=PAD, sticky="nsew")
        tab.rowconfigure(0, weight=1)
        card.columnconfigure(1, weight=1)

        ttk.Label(card, text="Single Album Download",
                  style="Head.TLabel").grid(
                      row=0, column=0, columnspan=3,
                      padx=PAD, pady=(PAD, 4), sticky="w")
        ttk.Label(card, text="Paste an E-Hentai / ExHentai / nhentai gallery URL and click Start.",
                  style="Sub.TLabel").grid(
                      row=1, column=0, columnspan=3,
                      padx=PAD, pady=(0, PAD), sticky="w")

        ttk.Label(card, text="Gallery URL", background=PANEL,
                  font=FONT_SMALL, foreground=TEXT_SEC).grid(
                      row=2, column=0, padx=(PAD, 4), pady=4, sticky="w")

        self._single_url = StringVar()
        entry = ttk.Entry(card, textvariable=self._single_url, font=FONT_UI)
        entry.grid(row=2, column=1, padx=4, pady=4, sticky="ew")
        entry.bind("<Return>", lambda _: self._start_single())

        tk.Button(card, text="✕", bg=PANEL, fg=MUTED, font=FONT_SMALL,
                  relief="flat", cursor="hand2",
                  command=lambda: self._single_url.set("")).grid(
                      row=2, column=2, padx=(0, 4))

        btn_row = tk.Frame(card, bg=PANEL)
        btn_row.grid(row=3, column=0, columnspan=3,
                     padx=PAD, pady=(8, PAD), sticky="e")

        self._single_paste_btn = _btn(btn_row, "📋  Paste", self._paste_single_url,
                                      style="Ghost.TButton")
        self._single_paste_btn.pack(side="left", padx=(0, 8))

        self._single_btn = _btn(btn_row, "▶  Start Download", self._start_single)
        self._single_btn.pack(side="left")

    # ── Batch tab ────────────────────────────────────────────────────────────

    def _build_batch_tab(self) -> None:
        """Build the 'Batch Download' tab: an editor bound to URLs.txt."""
        tab = self._tab_batch
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(1, weight=1)

        card = _card(tab)
        card.grid(row=0, column=0, rowspan=2, padx=PAD, pady=PAD, sticky="nsew")
        card.columnconfigure(0, weight=1)
        card.rowconfigure(2, weight=1)

        ttk.Label(card, text="Batch Download (URLs.txt)",
                  style="Head.TLabel").grid(
                      row=0, column=0, columnspan=2,
                      padx=PAD, pady=(PAD, 4), sticky="w")
        ttk.Label(card,
                  text="One URL per line (saved automatically). You can keep adding links at the "
                       "bottom while the batch runs; downloaded lines disappear after a while.",
                  style="Sub.TLabel").grid(
                      row=1, column=0, columnspan=2,
                      padx=PAD, pady=(0, 6), sticky="w")

        # Editor
        editor_frame = tk.Frame(card, bg=BORDER, bd=1)
        editor_frame.grid(row=2, column=0, columnspan=2,
                          padx=PAD, pady=4, sticky="nsew")
        editor_frame.rowconfigure(0, weight=1)
        editor_frame.columnconfigure(0, weight=1)

        self._batch_editor = Text(
            editor_frame,
            font=FONT_MONO, bg=PANEL, fg=TEXT,
            insertbackground=TEXT, selectbackground=SELECT_BG,
            relief="flat", bd=0, height=6,
            wrap="none", undo=True,
        )
        self._batch_editor.grid(row=0, column=0, sticky="nsew")

        vsb = ttk.Scrollbar(editor_frame, orient="vertical",
                             command=self._batch_editor.yview)
        vsb.grid(row=0, column=1, sticky="ns")
        self._batch_editor.configure(yscrollcommand=vsb.set)

        self._load_urls_txt()
        self._batch_editor.tag_configure(
            "queued_batch_line",
            background=LINE_QUEUED_BG,
        )
        self._batch_editor.tag_configure(
            "active_batch_line",
            background=LINE_ACTIVE_BG,
            foreground=LINE_ACTIVE_FG,
        )
        self._batch_editor.tag_configure(
            "done_batch_line",
            background=LINE_DONE_BG,
            foreground=LINE_DONE_FG,
        )
        self._batch_editor.tag_configure(
            "fail_batch_line",
            background=LINE_FAIL_BG,
            foreground=LINE_FAIL_FG,
        )
        # Later states must win over "queued" when both are on a line
        for tag in ("active_batch_line", "done_batch_line", "fail_batch_line"):
            self._batch_editor.tag_raise(tag, "queued_batch_line")

        # Buttons row
        btn_row = tk.Frame(card, bg=PANEL)
        btn_row.grid(row=3, column=0, columnspan=2,
                     padx=PAD, pady=(6, PAD), sticky="ew")

        self._reload_btn = _btn(btn_row, "🔄  Reload", self._load_urls_txt,
                                style="Ghost.TButton")
        self._reload_btn.pack(side="left", padx=(0, 6))
        _btn(btn_row, "💾  Save", self._save_urls_txt,
             style="Ghost.TButton").pack(side="left", padx=(0, 6))
        self._clear_btn = _btn(btn_row, "🗑  Clear All", self._clear_urls_txt,
                               style="Ghost.TButton")
        self._clear_btn.pack(side="left", padx=(0, 6))
        self._dedupe_btn = _btn(btn_row, "🧹  Duplicates", self._remove_duplicates,
                                style="Ghost.TButton")
        self._dedupe_btn.pack(side="left", padx=(0, 6))
        self._verify_btn = _btn(btn_row, "🔍  Verify Links", self._start_verify,
                                style="Ghost.TButton")
        self._verify_btn.pack(side="left")

        self._batch_btn = _btn(btn_row, "▶  Start Batch", self._start_batch)
        self._batch_btn.pack(side="right", before=self._reload_btn)  # main actions keep their space
        # Pause / Resume: freezes every request; the list and the partial album stay saved,
        # so the program can also be closed and the batch restarted later
        self._pause_btn = _btn(btn_row, "⏸  Pause", self._toggle_pause, style="Ghost.TButton")
        self._pause_btn.pack(side="right", padx=(0, 6), before=self._reload_btn)
        self._pause_btn.configure(state="disabled")
        # Seven buttons on one row: size them on their text so all fit in the default window
        for child in btn_row.winfo_children():
            if isinstance(child, ttk.Button) and child is not self._batch_btn:
                child.configure(width=0)

    # ── Retry tab ────────────────────────────────────────────────────────────

    def _build_retry_tab(self) -> None:
        """Build the 'Retry Failed' tab: read-only list from failed_downloads.txt."""
        tab = self._tab_retry
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(1, weight=1)

        card = _card(tab)
        card.grid(row=0, column=0, rowspan=2, padx=PAD, pady=PAD, sticky="nsew")
        card.columnconfigure(0, weight=1)
        card.rowconfigure(2, weight=1)

        ttk.Label(card, text="Retry Failed Downloads",
                  style="Head.TLabel").grid(
                      row=0, column=0, columnspan=2,
                      padx=PAD, pady=(PAD, 4), sticky="w")
        ttk.Label(card,
                  text="URLs loaded from failed_downloads.txt.",
                  style="Sub.TLabel").grid(
                      row=1, column=0, columnspan=2,
                      padx=PAD, pady=(0, 6), sticky="w")

        # Read-only list
        list_frame = tk.Frame(card, bg=BORDER, bd=1)
        list_frame.grid(row=2, column=0, columnspan=2,
                        padx=PAD, pady=4, sticky="nsew")
        list_frame.rowconfigure(0, weight=1)
        list_frame.columnconfigure(0, weight=1)

        self._retry_list = Text(
            list_frame,
            font=FONT_MONO, bg=RETRY_BG, fg=TEXT,
            relief="flat", bd=0, height=6,
            state="disabled", wrap="none",
        )
        self._retry_list.grid(row=0, column=0, sticky="nsew")
        vsb2 = ttk.Scrollbar(list_frame, orient="vertical",
                              command=self._retry_list.yview)
        vsb2.grid(row=0, column=1, sticky="ns")
        self._retry_list.configure(yscrollcommand=vsb2.set)

        self._retry_list.tag_configure(
            "active_batch_line",
            background=LINE_ACTIVE_BG,
            foreground=LINE_ACTIVE_FG,
        )
        self._retry_list.tag_configure(
            "done_batch_line",
            background=LINE_DONE_BG,
            foreground=LINE_DONE_FG,
        )
        self._retry_list.tag_configure(
            "fail_batch_line",
            background=LINE_FAIL_BG,
            foreground=LINE_FAIL_FG,
        )

        # Counter label
        self._retry_count_var = StringVar(value="")
        ttk.Label(card, textvariable=self._retry_count_var,
                  style="Sub.TLabel").grid(
                      row=3, column=0,
                      padx=PAD, pady=4, sticky="w")

        # Buttons row
        btn_row = tk.Frame(card, bg=PANEL)
        btn_row.grid(row=4, column=0, columnspan=2,
                     padx=PAD, pady=(6, PAD), sticky="ew")

        _btn(btn_row, "🔄  Refresh List", self._load_retry_list,
             style="Ghost.TButton").pack(side="left", padx=(0, 6))
        _btn(btn_row, "🗑  Clear Queue", self._clear_retry_queue,
             style="Ghost.TButton").pack(side="left")

        self._retry_btn = _btn(btn_row, "🔄  Retry Failed", self._start_retry)
        self._retry_btn.pack(side="right")

        self._load_retry_list()

    # ── Progress & Log areas ──────────────────────────────────────────────────

    def _build_progress_area(self) -> None:
        """Build the two progress bars (album/batch total and page/current album)."""
        prog_frame = tk.Frame(self, bg=BG)
        prog_frame.pack(side="bottom", fill="x", padx=PAD, pady=(4, 6))

        # Row 0: Bar 1 (Album Total / Batch Total)
        row0 = tk.Frame(prog_frame, bg=BG)
        row0.pack(fill="x", pady=(0, 3))
        row0.columnconfigure(1, weight=1)

        self._bar1_lbl = tk.Label(
            row0, text="Album Total:", bg=BG, fg=TEXT,
            font=FONT_SMALL, width=17, anchor="w",
        )
        self._bar1_lbl.grid(row=0, column=0, sticky="w", padx=(0, 6))

        self._bar1 = ttk.Progressbar(
            row0, orient="horizontal",
            style="TProgressbar", length=100,
        )
        self._bar1.grid(row=0, column=1, sticky="ew", padx=(0, 10))

        self._bar1_pct = tk.Label(
            row0, text="0%", bg=BG, fg=TEXT_SEC,
            font=FONT_SMALL, width=18, anchor="e",
        )
        self._bar1_pct.grid(row=0, column=2, sticky="e")

        # Row 1: Bar 2 (Page Download / Current Album)
        row1 = tk.Frame(prog_frame, bg=BG)
        row1.pack(fill="x")
        row1.columnconfigure(1, weight=1)

        self._bar2_lbl = tk.Label(
            row1, text="Page Download:", bg=BG, fg=TEXT,
            font=FONT_SMALL, width=17, anchor="w",
        )
        self._bar2_lbl.grid(row=0, column=0, sticky="w", padx=(0, 6))

        self._bar2 = ttk.Progressbar(
            row1, orient="horizontal",
            style="Task.TProgressbar", length=100,
        )
        self._bar2.grid(row=0, column=1, sticky="ew", padx=(0, 10))

        self._bar2_pct = tk.Label(
            row1, text="0%", bg=BG, fg=TEXT_SEC,
            font=FONT_SMALL, width=18, anchor="e",
        )
        self._bar2_pct.grid(row=0, column=2, sticky="e")

        # Backward compatibility aliases
        self._overall_bar = self._bar1
        self._overall_pct = self._bar1_pct
        self._task_bar = self._bar2
        self._task_pct = self._bar2_pct

    def _build_log_area(self) -> None:
        """Build the coloured log box with 'Open Log File' and 'Clear' buttons."""
        log_container = tk.Frame(self, bg=BG)
        log_container.pack(side="bottom", fill="x", padx=PAD, pady=(0, 6))

        lbl_row = tk.Frame(log_container, bg=BG)
        lbl_row.pack(side="top", fill="x")
        tk.Label(lbl_row, text="Log", bg=BG, fg=TEXT_SEC,
                 font=FONT_SMALL).pack(side="left")
        tk.Button(lbl_row, text="Clear", bg=BG, fg=MUTED, font=FONT_SMALL,
                  relief="flat", cursor="hand2",
                  command=self._clear_log).pack(side="right")
        tk.Button(lbl_row, text="📄 Open Log File", bg=BG, fg=ACCENT, font=FONT_SMALL,
                  relief="flat", cursor="hand2",
                  command=self._open_session_log).pack(side="right", padx=(0, 8))

        log_frame = tk.Frame(log_container, bg=BORDER, bd=1, height=115)
        log_frame.pack(side="top", fill="x", pady=(2, 0))
        log_frame.pack_propagate(False)
        self._log_frame = log_frame

        self._log_box = Text(
            log_frame,
            font=FONT_MONO, bg=PANEL, fg=TEXT,
            relief="flat", bd=0,
            state="disabled", wrap="word",
        )
        self._log_box.pack(side="left", fill="both", expand=True)

        vsb = ttk.Scrollbar(log_frame, orient="vertical",
                             command=self._log_box.yview)
        vsb.pack(side="right", fill="y")
        self._log_box.configure(yscrollcommand=vsb.set)

        # Colour tags
        self._log_box.tag_configure("INFO",           foreground=TEXT_SEC)
        self._log_box.tag_configure("WARNING",        foreground=WARNING)
        self._log_box.tag_configure("ERROR",          foreground=ERROR)
        self._log_box.tag_configure("CRITICAL",       foreground=ERROR)
        self._log_box.tag_configure("DEBUG",          foreground=MUTED)
        self._log_box.tag_configure("ts",             foreground=MUTED)
        self._log_box.tag_configure("event",          foreground=ACCENT)
        self._log_box.tag_configure("ok",             foreground=SUCCESS)
        self._log_box.tag_configure("success_banner", foreground=OK_BANNER,  font=FONT_MONO_BOLD)
        self._log_box.tag_configure("warn_banner",    foreground=WARN_BANNER,  font=FONT_MONO_BOLD)
        self._log_box.tag_configure("warn_url",       foreground=WARN_BANNER,  font=FONT_MONO_BOLD)
        self._log_box.tag_configure("warn_item",      foreground=WARNING)

    def _on_window_resize(self, event=None) -> None:
        """Dynamically scale log area between ~115px and 350px max when window is resized."""
        if event and event.widget == self:
            h = event.height
            new_log_h = min(350, max(115, 115 + int((h - self._initial_h) * 0.45)))
            if hasattr(self, "_log_frame"):
                self._log_frame.configure(height=new_log_h)

    # ------------------------------------------------------------------ helpers

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

        handler = _QueueLogHandler(self._q)
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

    def _on_format_changed(self, event=None) -> None:
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
        self._prev_format = new_format
        self._save_settings()

    # ------------------------------------------------------------------ speed / IP ban

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
            fg=WARNING if level >= 5 else TEXT_SEC, bg=BG,
        )

    def _on_ban_changed(self, until: float) -> None:
        """A worker reported an IP ban (until > 0) or the ban was cleared (until == 0)."""
        self._ban_until = until
        if until > 0:
            self._resume_btn.pack(side="right", padx=(0, PAD), before=self._status_lbl)
            self._status_lbl.configure(foreground=WARNING)
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
        self._status_lbl.configure(foreground=TEXT_SEC)
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

    # ------------------------------------------------------------------ remembered settings

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

    def _on_download_done(self, success: bool, failed: dict[str, list[str]] | list[str]) -> None:
        """Re-enable the UI and print a success or failure summary in the log."""
        self._running = False
        self._set_buttons_state("normal")
        throttle.set_paused(False)
        self._pause_btn.configure(state="disabled", text="⏸  Pause")
        if success:
            self._bar1["value"] = 100
            self._bar1_pct.configure(text="100%")
            self._bar2["value"] = 100
            self._bar2_pct.configure(text="100%")
        self._log_box.configure(state="normal")

        sep = "─" * 60
        ts = _ts()

        # Normalize failed into a dict[str, list[str]]
        if isinstance(failed, dict):
            failed_map = failed
        elif isinstance(failed, list):
            failed_map = {"": failed} if failed else {}
        else:
            failed_map = {}

        from src.cli.downloader import gallery_name

        def is_image_item(item: str) -> bool:
            """True for a missing image (picture page URL or '(page NN)'), False for an album error."""
            return "/s/" in item or "(page " in item

        failed_albums = [url for url in failed_map if url]
        missing_images = sum(
            1 for items in failed_map.values() for item in (items or []) if is_image_item(item)
        )
        total_failed_items = len(failed_albums) or sum(len(items) or 1 for items in failed_map.values())

        if success and not failed_map:
            append_session_banner("DOWNLOAD COMPLETED SUCCESSFULLY — no missing files.")
            # ── Green banner ──────────────────────────────────────────
            self._log_box.insert(END, f"\n[{ts}] {sep}\n", "success_banner")
            self._log_box.insert(END,
                f"[{ts}] ✔  DOWNLOAD COMPLETED SUCCESSFULLY — no missing files.\n",
                "success_banner",
            )
            self._log_box.insert(END, f"[{ts}] {sep}\n", "success_banner")
            self._status_var.set("✔ Download completed successfully.")
        else:
            n = total_failed_items
            gal_word = "gallery" if n == 1 else "galleries"
            img_part = f", {missing_images} image(s) missing" if missing_images else ""
            append_session_banner(f"DOWNLOAD NOT COMPLETE — {n} {gal_word} with problems{img_part}")

            # ── Orange banner ─────────────────────────────────────────
            self._log_box.insert(END, f"\n[{ts}] {sep}\n", "warn_banner")
            self._log_box.insert(END,
                f"[{ts}] ⚠  DOWNLOAD NOT COMPLETE — {n} {gal_word} with problems{img_part}:\n",
                "warn_banner",
            )
            for album_url, items in failed_map.items():
                if album_url:
                    # Gallery name first (when known), then its link
                    title = gallery_name(album_url)
                    label = f"{title}  —  {album_url}" if title else album_url
                    self._log_box.insert(END, f"[{ts}] 🔗 {label}\n", "warn_url")
                image_items = [item for item in (items or []) if is_image_item(item)]
                if not image_items:
                    self._log_box.insert(
                        END,
                        f"[{ts}]    • Gallery not downloaded: its page could not be read "
                        "(the reason is in the log above)\n",
                        "warn_item",
                    )
                else:
                    for item in image_items:
                        # Show only the last meaningful segment (filename or page URL)
                        label = item.split("/")[-1].strip() or item
                        self._log_box.insert(END, f"[{ts}]    • missing image: {label}\n", "warn_item")
            self._log_box.insert(END, f"[{ts}] {sep}\n", "warn_banner")
            self._log_box.insert(END,
                f"[{ts}] Use 'Retry Failed' to retry the missing files.\n",
                "warn_banner",
            )
            self._status_var.set(
                f"⚠ Download not complete: {n} {gal_word} with problems{img_part} — use Retry Failed."
            )

        self._log_box.see(END)
        self._log_box.configure(state="disabled")

        if self._when_done_var.get() != WHEN_DONE_NOTHING:
            self.after(500, self._start_when_done_countdown)

    # ------------------------------------------------------------------ when done

    def _on_when_done_changed(self, _event=None) -> None:
        """Confirm the choice in the log."""
        choice = self._when_done_var.get()
        if hasattr(self, "_when_done_cb"):
            self._when_done_cb.selection_clear()
        if choice == WHEN_DONE_NOTHING:
            self._append_log(_ts(), "When done", "Nothing will happen when the downloads finish.")
            return
        when = "when the current downloads finish" if self._running else "when the next download finishes"
        action = "The program will close" if choice == WHEN_DONE_CLOSE else "The PC will shut down"
        self._append_log(
            _ts(), "When done",
            f"{action} {when} (you get {WHEN_DONE_COUNTDOWN} s to cancel).",
        )

    def _start_when_done_countdown(self) -> None:
        """Show a cancellable countdown, then close the program or shut down the PC."""
        if self._running or self._when_done_dialog is not None:
            return  # a new download was started in the meantime
        choice = self._when_done_var.get()
        if choice == WHEN_DONE_NOTHING:
            return
        action = "Closing the program" if choice == WHEN_DONE_CLOSE else "Shutting down the PC"
        append_session_banner(f"WHEN DONE: {choice.upper()} (countdown {WHEN_DONE_COUNTDOWN}s)")

        dlg = tk.Toplevel(self)
        self._when_done_dialog = dlg
        dlg.title("Downloads finished")
        dlg.configure(bg=PANEL)
        dlg.resizable(False, False)
        dlg.transient(self)
        dlg.attributes("-topmost", True)
        msg_var = StringVar()
        tk.Label(dlg, textvariable=msg_var, bg=PANEL, fg=TEXT, font=FONT_UI,
                 justify="left").pack(padx=PAD * 2, pady=(PAD * 2, PAD))
        btns = tk.Frame(dlg, bg=PANEL)
        btns.pack(padx=PAD * 2, pady=(0, PAD * 2), fill="x")
        state = {"left": WHEN_DONE_COUNTDOWN, "job": None}

        def close_dialog() -> None:
            if state["job"] is not None:
                self.after_cancel(state["job"])
                state["job"] = None
            self._when_done_dialog = None
            dlg.destroy()

        def cancel() -> None:
            close_dialog()
            append_session_banner("WHEN DONE: cancelled by the user")
            self._append_log(_ts(), "When done", f"{action} cancelled.")
            self._status_var.set(f"{action} cancelled.")

        def do_it() -> None:
            close_dialog()
            self._execute_when_done(choice)

        def tick() -> None:
            if state["left"] <= 0:
                do_it()
                return
            msg_var.set(
                f"All downloads are finished.\n\n{action} in {state['left']} s…\n"
                "The lists and settings are saved first."
            )
            state["left"] -= 1
            state["job"] = self.after(1000, tick)

        _btn(btns, "Cancel", cancel, style="Ghost.TButton").pack(side="right")
        now_text = "Close now" if choice == WHEN_DONE_CLOSE else "Shut down now"
        _btn(btns, now_text, do_it).pack(side="right", padx=(0, 6))
        dlg.protocol("WM_DELETE_WINDOW", cancel)
        tick()
        dlg.update_idletasks()
        x = self.winfo_rootx() + (self.winfo_width() - dlg.winfo_width()) // 2
        y = self.winfo_rooty() + (self.winfo_height() - dlg.winfo_height()) // 3
        dlg.geometry(f"+{max(0, x)}+{max(0, y)}")
        dlg.lift()

    def _save_state_for_exit(self) -> None:
        """Save the batch list (downloaded lines removed now) and the settings."""
        try:
            self._flush_pending_removals()
            self._save_urls_txt(quiet=True)
            self._save_settings()
        except Exception:
            pass

    def _execute_when_done(self, choice: str) -> None:
        """Close the program, or shut down the PC and then close the program."""
        self._save_state_for_exit()
        if choice == WHEN_DONE_SHUTDOWN:
            if os.name == "nt":
                cmd = ["shutdown", "/s", "/t", "0"]
            elif sys.platform == "darwin":
                cmd = ["osascript", "-e", 'tell application "System Events" to shut down']
            else:
                cmd = ["systemctl", "poweroff"]
            try:
                flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
                subprocess.run(cmd, check=True, creationflags=flags, timeout=30)  # noqa: S603
            except (OSError, subprocess.SubprocessError) as err:
                append_session_banner(f"WHEN DONE: shutdown failed ({err})")
                self._append_log(_ts(), "ERROR", f"Could not shut down the PC: {err}")
                self._status_var.set("⚠ Could not shut down the PC (see the log).")
                return
            append_session_banner("APPLICATION CLOSED (PC shutting down after the downloads)")
        else:
            append_session_banner("APPLICATION CLOSED (downloads finished)")
        self._closed = True
        self.destroy()

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
        folder = Path("Downloads")
        folder.mkdir(exist_ok=True)
        if os.name == "nt":
            os.startfile(str(folder.resolve()))
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(folder.resolve())])
        else:
            subprocess.Popen(["xdg-open", str(folder.resolve())])

    def _open_session_log(self) -> None:
        """Open the session.log file in the default system text viewer."""
        log_path = Path(SESSION_LOG).resolve()
        if not log_path.exists():
            log_path.touch()
        try:
            if os.name == "nt":
                os.startfile(str(log_path))
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(log_path)])
            else:
                subprocess.Popen(["xdg-open", str(log_path)])
        except Exception as err:
            messagebox.showerror("Error", f"Could not open the log file:\n{err}")

    def _on_window_close(self) -> None:
        """Handler called when user closes the GUI window."""
        if self._running and throttle.is_paused():
            append_session_banner("APPLICATION CLOSED (download paused, list saved)")
        elif self._running:
            if not messagebox.askyesno(
                "Close Application",
                "A download is in progress. Do you really want to stop it and close the program?",
            ):
                return
            append_session_banner("APPLICATION CLOSED (running download interrupted by the user)")
        else:
            append_session_banner("APPLICATION CLOSED")
        # Keep the list on disk: downloaded lines go away now, everything else is saved
        self._save_state_for_exit()
        self._closed = True
        self.destroy()

    def _on_exit(self) -> None:
        """Fallback hook when the application process exits."""
        if not getattr(self, "_closed", False):
            self._closed = True
            append_session_banner("APPLICATION CLOSED")

    # ------------------------------------------------------------------ URLs.txt

    def _editor_edit(self, action) -> None:
        """Run action() on the batch editor even while it is locked (read-only)."""
        previous = str(self._batch_editor.cget("state"))
        self._batch_editor.configure(state="normal")
        try:
            action()
        finally:
            # Programmatic edits must not be undone with Ctrl+Z (it would mix with the user's)
            self._batch_editor.edit_reset()
            self._batch_editor.configure(state=previous)

    def _replace_editor_line(self, line_no: int, text: str) -> None:
        """Replace the text of one batch line, keeping its highlight colour."""
        def action() -> None:
            tags = self._batch_editor.tag_names(f"{line_no}.0")
            self._batch_editor.delete(f"{line_no}.0", f"{line_no}.end")
            self._batch_editor.insert(f"{line_no}.0", text)
            for tag in tags:
                if tag.endswith("_batch_line") or tag.startswith("task_"):
                    self._batch_editor.tag_add(tag, f"{line_no}.0", f"{line_no}.end")
        self._editor_edit(action)

    def _editor_entries(self) -> list[tuple[int, str, str]]:
        """Return (line number, url, gallery name) for every link line of the batch editor."""
        from src.cli.downloader import parse_url_line, remember_gallery_name
        entries = []
        lines = self._batch_editor.get("1.0", "end-1c").splitlines()
        for line_no, line in enumerate(lines, start=1):
            url, name = parse_url_line(line)
            if url:
                remember_gallery_name(url, name)
                entries.append((line_no, url, name))
        return entries

    def _offer_duplicate_cleanup(self, ask_when_none: bool = False) -> str:
        """Look for links to the same gallery and offer to keep only one of each.

        Returns "removed", "kept" (user chose to keep them, or none found) or "cancel".
        """
        from src.cli.downloader import find_duplicates, format_url_line, gallery_name
        entries = self._editor_entries()
        pairs = find_duplicates([url for _, url, _ in entries])
        if not pairs:
            if ask_when_none:
                messagebox.showinfo("Duplicates", "No duplicate links found.")
            return "kept"

        lines_desc = []
        for dup_idx, kept_idx in pairs[:15]:
            dup_line, dup_url, _ = entries[dup_idx]
            kept_line, kept_url, _ = entries[kept_idx]
            name = gallery_name(kept_url) or gallery_name(dup_url)
            label = f" – {name}" if name else ""
            lines_desc.append(f"• line {dup_line} = line {kept_line}{label}")
        if len(pairs) > 15:
            lines_desc.append(f"… and {len(pairs) - 15} more")

        answer = messagebox.askyesnocancel(
            "Duplicate links",
            f"{len(pairs)} link(s) point to a gallery already in the list:\n\n"
            + "\n".join(lines_desc)
            + "\n\nRemove the duplicates and keep one link per gallery?\n"
            "(When one copy of a link is malformed, the valid one is kept.)\n\n"
            "Yes = remove   ·   No = keep them (they will be skipped)   ·   Cancel = stop",
        )
        if answer is None:
            return "cancel"
        if not answer:
            return "kept"

        # Keep the gallery name of a removed copy if the kept line has none
        for dup_idx, kept_idx in pairs:
            kept_line, kept_url, kept_name = entries[kept_idx]
            dup_name = entries[dup_idx][2]
            if dup_name and not kept_name:
                self._replace_editor_line(kept_line, format_url_line(kept_url, dup_name))

        def delete_lines() -> None:
            for dup_line in sorted((entries[d][0] for d, _ in pairs), reverse=True):
                self._batch_editor.delete(f"{dup_line}.0", f"{dup_line + 1}.0")
        self._editor_edit(delete_lines)
        self._save_urls_txt()
        self._q.put(("LOG", _ts(), "Duplicates", f"Removed {len(pairs)} duplicate link(s) from the list."))
        return "removed"

    def _remove_duplicates(self) -> None:
        """'Duplicates' button: find duplicate links and remove them after confirmation."""
        if self._guard_running():
            return
        self._offer_duplicate_cleanup(ask_when_none=True)

    def _confirm_invalid_links(self, entries: list[tuple[int, str, str]], action: str) -> bool:
        """Warn about malformed links (checked offline). Return False if the user cancels."""
        from src.general_utils import validate_gallery_url
        invalid = [(line_no, err) for line_no, url, _ in entries if (err := validate_gallery_url(url))]
        if not invalid:
            return True
        details = "\n\n".join(f"• line {line_no}: {err}" for line_no, err in invalid[:5])
        if len(invalid) > 5:
            details += f"\n\n… and {len(invalid) - 5} more"
        for line_no, _ in invalid:
            self._batch_editor.tag_add("fail_batch_line", f"{line_no}.0", f"{line_no}.end")
        return messagebox.askyesno(
            "Invalid links",
            f"{len(invalid)} link(s) are not valid (highlighted in red):\n\n{details}\n\n"
            f"{action} anyway? Invalid links will fail.",
        )

    def _load_urls_txt(self) -> None:
        """Load URLs.txt into the batch editor."""
        self._flush_pending_removals()
        path = Path("URLs.txt")
        content = path.read_text(encoding="utf-8") if path.exists() else ""
        def action() -> None:
            self._batch_editor.delete("1.0", END)
            self._batch_editor.insert("1.0", content)
        self._editor_edit(action)
        self._batch_editor.edit_modified(False)

    def _save_urls_txt(self, quiet: bool = False) -> None:
        """Save the batch editor content to URLs.txt (quiet: no status-bar message)."""
        content = self._batch_editor.get("1.0", END).strip("\n")
        content = content + "\n" if content else ""
        path = Path("URLs.txt")
        # Write to a temporary file first: a crash mid-write never leaves URLs.txt half-empty
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, path)
        self._batch_editor.edit_modified(False)
        if not quiet:
            self._status_var.set("URLs.txt saved.")

    def _autosave_tick(self) -> None:
        """Every AUTOSAVE_INTERVAL seconds: save the batch list if it changed."""
        try:
            if not self._closed and self._batch_editor.edit_modified():
                self._save_urls_txt(quiet=True)
        except Exception as err:
            self._q.put(("LOG", _ts(), "Autosave", f"Could not save URLs.txt: {err}"))
        if not self._closed:
            self.after(AUTOSAVE_INTERVAL * 1000, self._autosave_tick)

    def _clear_urls_txt(self) -> None:
        """Empty both the batch editor and URLs.txt, after a confirmation."""
        if self._guard_running():
            return
        links = len(self._editor_entries())
        if not self._batch_editor.get("1.0", "end-1c").strip():
            return
        if not messagebox.askyesno(
            "Clear All",
            f"Delete the whole batch list ({links} link{'s' if links != 1 else ''})?\n\n"
            "URLs.txt will be emptied too. This cannot be undone.",
            icon="warning",
            default="no",
        ):
            return
        self._flush_pending_removals()
        self._editor_edit(lambda: self._batch_editor.delete("1.0", END))
        self._save_urls_txt(quiet=True)
        self._status_var.set("URLs.txt cleared.")

    # ------------------------------------------------------------------ failed list

    def _load_retry_list(self) -> None:
        """Collect retry candidates and show them (read-only)."""
        # Import lazily to avoid triggering heavy imports at startup
        from src.cli.retry import collect_retry_urls
        from src.cli.downloader import format_url_line
        urls = collect_retry_urls()
        self._retry_list.configure(state="normal")
        self._retry_list.delete("1.0", END)
        if urls:
            # One line per album: "URL  # Gallery name" (name shown when known)
            self._retry_list.insert("1.0", "\n".join(format_url_line(url) for url in urls))
        else:
            self._retry_list.insert("1.0", "(no failed URLs found)")
        self._retry_list.configure(state="disabled")
        count = len(urls)
        self._retry_count_var.set(
            f"{count} URL{'s' if count != 1 else ''} in queue"
            if count else "Queue is empty."
        )

    def _clear_retry_queue(self) -> None:
        """Empty failed_downloads.txt after asking for confirmation."""
        if not messagebox.askyesno(
            "Clear Queue",
            "Clear failed_downloads.txt and the queue?\nThis cannot be undone.",
        ):
            return
        from src.cli.downloader import save_failed_urls
        save_failed_urls([])
        self._load_retry_list()
        self._status_var.set("Retry queue cleared.")

    # ------------------------------------------------------------------ download launchers

    def _guard_running(self) -> bool:
        """Return True (blocked) if a download is already in progress."""
        if self._running:
            messagebox.showwarning(
                "Busy",
                "A download is already in progress.\nWait for it to finish first.",
            )
            return True
        return False

    def _paste_single_url(self) -> None:
        """Paste URL from system clipboard into the Single Download input."""
        try:
            text = self.clipboard_get()
        except tk.TclError:
            return
        if not text:
            return
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if lines:
            self._single_url.set(lines[0])

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
        self._scan_batch_editor()
        to_check = self._verify_queue.qsize()

        self._status_var.set(f"Batch download [{fmt}]: {self._batch_total} URL(s)")
        if to_check:
            self._q.put(("LOG", _ts(), "Verify",
                         f"{to_check} link(s) not verified yet: checking them in parallel with the download."))
            threading.Thread(target=self._run_batch_verify, daemon=True).start()
        threading.Thread(
            target=self._run_batch, args=(fmt, self._rename_var.get(), self._cbz_var.get()), daemon=True,
        ).start()

    def _clear_batch_marks(self) -> None:
        """Remove the line highlights (and the task tags of a finished batch) from the editor."""
        self._flush_pending_removals()
        for tag in ("queued_batch_line", "active_batch_line", "done_batch_line", "fail_batch_line"):
            self._batch_editor.tag_remove(tag, "1.0", END)
        for tag in self._batch_editor.tag_names():
            if tag.startswith("task_"):
                self._batch_editor.tag_delete(tag)

    # ------------------------------------------------------------------ live batch queue

    def _task_line(self, task_id: int) -> int | None:
        """Current line number of a batch task, or None if the user deleted its line."""
        ranges = self._batch_editor.tag_ranges(f"task_{task_id}")
        if not ranges:
            return None
        return int(str(ranges[0]).split(".")[0])

    def _scan_batch_editor(self) -> None:
        """GUI thread: give every link line not yet claimed to the running batch.

        Claimed lines get a "task_<id>" tag. Links that were never verified also go to the
        parallel link check. A second link to a gallery already in this batch is skipped.
        """
        from src.cli.downloader import gallery_key, gallery_status, parse_url_line
        from src.general_utils import validate_gallery_url

        lines = self._batch_editor.get("1.0", "end-1c").splitlines()
        # Lines already claimed (a task tag anywhere on the line, even if the user typed at its start)
        claimed: set[int] = set()
        for tag in self._batch_editor.tag_names():
            if tag.startswith("task_"):
                ranges = self._batch_editor.tag_ranges(tag)
                for i in range(0, len(ranges), 2):
                    first = int(str(ranges[i]).split(".")[0])
                    last = int(str(ranges[i + 1]).split(".")[0])
                    claimed.update(range(first, last + 1))
        with self._batch_lock:
            if not self._batch_accepting:
                return
            for line_no, line in enumerate(lines, start=1):
                if line_no in claimed:
                    continue
                url, name = parse_url_line(line)
                if not url:
                    continue
                valid = not validate_gallery_url(url)
                key = gallery_key(url)
                dup = key in self._batch_keys and (self._batch_keys[key] or not valid)
                if not dup:
                    self._batch_keys[key] = valid

                self._batch_seq += 1
                task_id = self._batch_seq
                self._batch_total += 1
                self._task_state[task_id] = "queued"
                self._batch_editor.tag_add(f"task_{task_id}", f"{line_no}.0", f"{line_no}.end")
                self._batch_editor.tag_add("queued_batch_line", f"{line_no}.0", f"{line_no}.end")
                task = {"id": task_id, "url": url, "name": name, "dup": dup}
                self._batch_queue.put(task)
                if valid and not dup and not gallery_status(url):
                    self._verify_queue.put(task)

    def _on_task_active(self, task_id: int, cur_idx: int, total_cnt: int, label: str) -> None:
        """Highlight the line being downloaded."""
        self._batch_editor.tag_remove("active_batch_line", "1.0", END)
        line_no = self._task_line(task_id)
        if line_no:
            self._batch_editor.tag_remove("queued_batch_line", f"{line_no}.0", f"{line_no}.end")
            self._batch_editor.tag_add("active_batch_line", f"{line_no}.0", f"{line_no}.end")
            self._batch_editor.see(f"{line_no}.0")
        self._status_var.set(f"[{cur_idx}/{total_cnt}] {label}")

    def _on_task_done(self, task_id: int, is_ok: bool) -> None:
        """Colour a finished line; downloaded lines are removed after DONE_LINE_REMOVE_DELAY."""
        line_no = self._task_line(task_id)
        if line_no:
            for tag in ("queued_batch_line", "active_batch_line", "done_batch_line", "fail_batch_line"):
                self._batch_editor.tag_remove(tag, f"{line_no}.0", f"{line_no}.end")
            tag = "done_batch_line" if is_ok else "fail_batch_line"
            self._batch_editor.tag_add(tag, f"{line_no}.0", f"{line_no}.end")
        if is_ok:
            handle = self.after(DONE_LINE_REMOVE_DELAY * 1000, lambda: self._remove_task_line(task_id))
            self._pending_removals[task_id] = handle
        else:
            self._failed_task_ids.append(task_id)

    def _remove_task_line(self, task_id: int, save: bool = True) -> None:
        """Delete the editor line of a batch task (if it still exists)."""
        self._pending_removals.pop(task_id, None)
        line_no = self._task_line(task_id)
        if line_no:
            self._editor_edit(
                lambda: self._batch_editor.delete(f"{line_no}.0", f"{line_no + 1}.0")
            )
        self._batch_editor.tag_delete(f"task_{task_id}")
        if save and line_no:
            self._save_urls_txt(quiet=True)

    def _flush_pending_removals(self) -> None:
        """Remove at once every downloaded line still waiting for its timer."""
        if not getattr(self, "_pending_removals", None):
            return
        for task_id, handle in list(self._pending_removals.items()):
            try:
                self.after_cancel(handle)
            except Exception:
                pass
            self._remove_task_line(task_id, save=False)
        self._save_urls_txt(quiet=True)

    def _on_batch_finished(self) -> None:
        """GUI thread: the batch is over. Failed lines leave the list (they are in Retry Failed)."""
        for task_id in self._failed_task_ids:
            self._remove_task_line(task_id, save=False)
        self._failed_task_ids = []
        self._batch_editor.tag_remove("queued_batch_line", "1.0", END)
        self._batch_editor.tag_remove("active_batch_line", "1.0", END)
        self._save_urls_txt(quiet=True)
        self._load_retry_list()

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

    def _on_verify_done(self, ok_count: int, bad_count: int, domains: dict) -> None:
        """Re-enable the UI, save the annotated list and print the verification summary."""
        self._running = False
        self._set_buttons_state("normal")
        self._save_urls_txt()
        sources = ", ".join(f"{d}: {n}" for d, n in sorted(domains.items())) or "none"
        summary = f"{ok_count} valid, {bad_count} with problems. Download source → {sources}"
        self._q.put(("LOG", _ts(), "Verify completed", summary))
        self._status_var.set(("✔ " if not bad_count else "⚠ ") + "Verify: " + summary)
        self._offer_sort_by_size()

    def _offer_sort_by_size(self) -> None:
        """After a verification, offer to move the galleries with fewer images to the top."""
        from src.cli.downloader import gallery_status, parse_url_line, sort_lines_by_size
        lines = self._batch_editor.get("1.0", "end-1c").splitlines()
        sorted_lines = sort_lines_by_size(lines)
        if sorted_lines is None:
            return
        if not messagebox.askyesno(
            "Download order",
            "Download the galleries with fewer images first?\n\n"
            "The list will be sorted by number of images (smallest first); "
            "links with problems go to the bottom. Comment and blank lines stay where they are.",
        ):
            return

        def action() -> None:
            self._batch_editor.delete("1.0", END)
            self._batch_editor.insert("1.0", "\n".join(sorted_lines))
            # Re-apply the verify colours: line numbers have changed
            for line_no, line in enumerate(sorted_lines, start=1):
                url, _ = parse_url_line(line)
                status = gallery_status(url) if url else ""
                if status:
                    tag = "done_batch_line" if status == "GOOD" else "fail_batch_line"
                    self._batch_editor.tag_add(tag, f"{line_no}.0", f"{line_no}.end")
        self._editor_edit(action)
        self._save_urls_txt(quiet=True)
        self._q.put(("LOG", _ts(), "Download order", "List sorted: galleries with fewer images first."))

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

    def _begin_download(self, editor_editable: bool = False) -> None:
        """Reset progress bars and log, and lock the controls."""
        self._running = True
        self._set_buttons_state("disabled", editor_editable=editor_editable)
        throttle.set_paused(False)
        self._pause_btn.configure(state="normal", text="⏸  Pause")
        self._clear_log()
        self._bar1["value"] = 0
        self._bar1_pct.configure(text="0%")
        self._bar2["value"] = 0
        self._bar2_pct.configure(text="0%")

    # ------------------------------------------------------------------ background workers

    def _run_single(
        self, url: str, image_format: str = "original", rename: bool = RENAME_SEQUENTIAL, cbz: bool = CREATE_CBZ,
    ) -> None:
        """Worker thread: download one album and report the result to the GUI."""
        success = False
        failed_by_url: dict[str, list[str]] = {}
        append_session_banner(f"SINGLE DOWNLOAD START: {url}")
        try:
            from src.cli.downloader import download_album
            lm = GUILiveManager(log_queue=self._q, mode="single")
            ok, failed_items = download_album(
                url, lm, image_format=image_format, rename_sequential=rename, create_cbz=cbz,
            )
            success = ok
            if failed_items or not ok:
                failed_by_url[url] = failed_items
        except Exception as exc:
            self._q.put(("LOG", _ts(), "ERROR", str(exc)))
            if url:
                failed_by_url[url] = [str(exc)]
        finally:
            self._q.put(("DONE", success, failed_by_url))

    def _next_batch_task(self) -> dict | None:
        """Worker thread: next task of the live queue, or None when nothing is left.

        Before giving up it asks the GUI to look once more for lines added in the meantime;
        the queue is closed under the lock, so a line added later is never lost (it simply
        stays in the list for the next batch).
        """
        try:
            return self._batch_queue.get_nowait()
        except queue.Empty:
            pass
        self._batch_scan_done.clear()
        self._q.put(("BATCH_SCAN",))
        self._batch_scan_done.wait(timeout=5)
        with self._batch_lock:
            try:
                return self._batch_queue.get_nowait()
            except queue.Empty:
                self._batch_accepting = False
                return None

    def _run_batch(self, image_format: str = "original", rename: bool = RENAME_SEQUENTIAL, cbz: bool = CREATE_CBZ) -> None:
        """Worker thread: download the batch list one album after another.

        Tasks come from a live queue filled by the GUI thread, so links added at the bottom
        of the list while the batch runs are downloaded too.
        """
        success = True
        failed_by_url: dict[str, list[str]] = {}
        done_count = 0
        try:
            from src.cli.downloader import download_album, format_url_line, gallery_name, update_failed_url

            append_session_banner(f"BATCH DOWNLOAD START ({self._batch_total} albums)")

            while True:
                task = self._next_batch_task()
                if task is None:
                    break
                task_id, url, name = task["id"], task["url"], task["name"]
                with self._batch_lock:
                    self._task_state[task_id] = "active"
                    total_items = self._batch_total
                self._q.put(("TASK_ACTIVE", task_id, done_count + 1, total_items, name or gallery_name(url) or url))

                if task["dup"]:
                    self._q.put(("LOG", _ts(), "Duplicate",
                                 f"{name or url}: same gallery as a link already in the batch, skipped."))
                    ok, failed_items = True, []
                elif task_id in self._verify_bad:
                    # The parallel link check already found this gallery unreadable
                    self._q.put(("LOG", _ts(), "Skipped",
                                 f"{url}: link check failed, not downloaded (added to Retry Failed)."))
                    update_failed_url(url, has_failures=True)
                    ok, failed_items = False, []
                else:
                    lm = GUILiveManager(
                        log_queue=self._q,
                        mode="batch",
                        batch_idx=done_count,
                        batch_total=total_items,
                    )
                    ok, failed_items = download_album(
                        url, lm, image_format=image_format, rename_sequential=rename, name_hint=name, create_cbz=cbz,
                    )
                    # Rewrite the line as "STATUS | IMAGES | TITLE | LINK" with what is now known
                    self._q.put(("TASK_ANNOTATE", task_id, format_url_line(url)))

                with self._batch_lock:
                    self._task_state[task_id] = "done"
                done_count += 1
                if not ok or failed_items:
                    failed_by_url[url] = failed_items
                    success = False
                    self._q.put(("TASK_DONE", task_id, False))
                else:
                    self._q.put(("TASK_DONE", task_id, True))

            # failed_downloads.txt is already updated album by album by download_album():
            # albums that failed in earlier sessions stay in the queue.
            self._q.put((
                "PROGRESS_BARS",
                1.0, f"{done_count}/{done_count} (100%)",
                1.0, "100%",
            ))
        except Exception as exc:
            self._q.put(("LOG", _ts(), "ERROR", str(exc)))
            success = False
        finally:
            with self._batch_lock:
                self._batch_accepting = False
            self._batch_stop.set()
            self._q.put(("BATCH_FINISHED",))
            self._q.put(("DONE", success, failed_by_url))

    def _run_batch_verify(self) -> None:
        """Worker thread: check not-yet-verified links while the batch downloads.

        Only links still waiting in the queue are checked. A link found BAD is shown in red
        and skipped by the downloader; a good one gets its title and image count at once.
        """
        try:
            from src.cli.downloader import courtesy_pause, format_url_line, verify_gallery
            from src.general_utils import create_session

            session = create_session()
            checked = 0
            while not self._batch_stop.is_set():
                try:
                    task = self._verify_queue.get(timeout=0.5)
                except queue.Empty:
                    continue
                with self._batch_lock:
                    if self._task_state.get(task["id"]) != "queued":
                        continue  # already downloading or done: nothing to check
                result = verify_gallery(task["url"], session=session)
                checked += 1
                with self._batch_lock:
                    still_queued = self._task_state.get(task["id"]) == "queued"
                if result["ok"]:
                    if still_queued:
                        self._q.put(("TASK_ANNOTATE", task["id"], format_url_line(task["url"], result["title"])))
                    self._q.put(("LOG", _ts(), "Link OK",
                                 f"\"{result['title']}\" — {result['images']} images — from {result['domain']}"))
                elif still_queued:
                    self._verify_bad.add(task["id"])
                    self._q.put(("TASK_ANNOTATE", task["id"], format_url_line(task["url"])))
                    self._q.put(("TASK_VERIFIED_BAD", task["id"]))
                    self._q.put(("LOG", _ts(), "Link error", f"{task['url']}: {result['error']}"))
                if result["domain"]:
                    courtesy_pause()
        except Exception as exc:
            self._q.put(("LOG", _ts(), "ERROR", f"Link check: {exc}"))

    def _run_verify(self, entries: list[tuple[int, str, str]]) -> None:
        """Worker thread: check each link online (title, size, source site) without downloading."""
        ok_count = bad_count = 0
        domains: dict[str, int] = {}
        try:
            from src.cli.downloader import courtesy_pause, format_url_line, verify_gallery
            from src.general_utils import create_session

            session = create_session()
            total = len(entries)
            for idx, (line_no, url, _name) in enumerate(entries):
                self._q.put(("BATCH_HIGHLIGHT", line_no, idx + 1, total, url))
                result = verify_gallery(url, session=session)
                if result["ok"]:
                    ok_count += 1
                    domains[result["domain"]] = domains.get(result["domain"], 0) + 1
                    note = f" ({result['note']})" if result["note"] else ""
                    self._q.put(("LOG", _ts(), "Link OK",
                                 f"Line {line_no}: \"{result['title']}\" — {result['images']} images — "
                                 f"from {result['domain']}{note}"))
                    self._q.put(("BATCH_ANNOTATE", line_no, format_url_line(url, result["title"])))
                    self._q.put(("BATCH_LINE_DONE", line_no, True))
                else:
                    bad_count += 1
                    self._q.put(("LOG", _ts(), "Link error", f"Line {line_no}: {result['error']}"))
                    self._q.put(("BATCH_ANNOTATE", line_no, format_url_line(url)))
                    self._q.put(("BATCH_LINE_DONE", line_no, False))
                frac = (idx + 1) / total
                self._q.put(("UPDATE_BAR1", frac, f"{idx + 1}/{total} ({frac * 100:.0f}%)"))
                # Only pause between real requests (malformed links are checked offline)
                if idx < total - 1 and result["domain"]:
                    courtesy_pause()
        except Exception as exc:
            self._q.put(("LOG", _ts(), "ERROR", str(exc)))
        finally:
            self._q.put(("VERIFY_DONE", ok_count, bad_count, domains))

    def _run_retry(self, image_format: str = "original", rename: bool = RENAME_SEQUENTIAL, cbz: bool = CREATE_CBZ) -> None:
        """Worker thread: retry every album of the failed queue and keep only those still failing."""
        success = True
        failed_by_url: dict[str, list[str]] = {}
        try:
            from src.cli.retry import collect_retry_urls
            from src.cli.downloader import download_album, gallery_name, save_failed_urls

            retry_urls = collect_retry_urls()
            total = len(retry_urls)
            append_session_banner(f"RETRY START ({total} albums)")
            remaining: list[str] = []

            for idx, url in enumerate(retry_urls):
                line_no = idx + 1
                self._q.put(("RETRY_HIGHLIGHT", line_no, idx + 1, total, gallery_name(url) or url))

                lm = GUILiveManager(
                    log_queue=self._q,
                    mode="retry",
                    batch_idx=idx,
                    batch_total=total,
                    line_no=line_no,
                )
                ok, failed_items = download_album(
                    url, lm, image_format=image_format, rename_sequential=rename,
                    name_hint=gallery_name(url), create_cbz=cbz,
                )
                if not ok or failed_items:
                    remaining.append(url)
                    failed_by_url[url] = failed_items
                    success = False
                    self._q.put(("RETRY_LINE_DONE", line_no, False))
                else:
                    self._q.put(("RETRY_LINE_DONE", line_no, True))

            save_failed_urls(remaining)

            self._q.put((
                "LOG", _ts(), "Retry completed",
                f"Processed: {total} | Still failing: {len(remaining)}",
            ))

            self._q.put((
                "PROGRESS_BARS",
                1.0, f"{total}/{total} (100%)",
                1.0, "100%",
            ))

            self._q.put(("RELOAD_LISTS",))  # reload both lists on the GUI thread
        except Exception as exc:
            self._q.put(("LOG", _ts(), "ERROR", str(exc)))
            success = False
        finally:
            self._q.put(("DONE", success, failed_by_url))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ts() -> str:
    """Return the current time as HH:MM:SS for log lines."""
    return datetime.datetime.now().strftime("%H:%M:%S")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    # Make sure we run from the project root so relative paths work
    """Start the GUI from the project folder so relative paths (Downloads, URLs.txt, ...) resolve correctly."""
    # (or, for the packaged app, next to the executable / in ~/EHentaiDownloader: see src/paths.py)
    paths.use_app_dir()

    app = EHentaiGUI()
    app.update_idletasks()
    if not app.show_startup_notice():
        app._closed = True  # pylint: disable=protected-access
        app.destroy()
        return
    app.mainloop()


if __name__ == "__main__":
    main()
