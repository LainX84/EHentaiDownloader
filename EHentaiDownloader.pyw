"""EHentai Downloader – Graphical User Interface (single-file launcher).

Double-click this file on Windows to start the program without a console window
(.pyw files are opened by pythonw.exe). It also works from a terminal:

    python EHentaiDownloader.pyw

The window wraps the three command-line modes of the project:
  • Single Download  – one gallery URL            (same logic as downloader.py)
  • Batch Download   – every URL in URLs.txt      (same logic as main.py)
  • Retry Failed     – albums in failed_downloads.txt (same logic as retry.py)

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

from src.config import AUTOSAVE_INTERVAL, DONE_LINE_REMOVE_DELAY, RENAME_SEQUENTIAL, SESSION_LOG
from src.file_utils import append_session_banner, append_session_log

# ---------------------------------------------------------------------------
# Colour / style constants
# ---------------------------------------------------------------------------
BG       = "#F5F7FA"       # window background
PANEL    = "#FFFFFF"       # card/panel background
ACCENT   = "#4A90D9"       # primary blue
ACCENT2  = "#357ABD"       # darker blue (hover / active)
SUCCESS  = "#27AE60"       # green
WARNING  = "#E67E22"       # orange
ERROR    = "#E74C3C"       # red
MUTED    = "#95A5A6"       # grey text
BORDER   = "#DDE3EA"       # subtle border
TEXT     = "#2C3E50"       # main text
TEXT_SEC = "#606F7B"       # secondary text

FONT_UI    = ("Segoe UI",   10)
FONT_SMALL = ("Segoe UI",   9)
FONT_MONO  = ("Consolas",   9)
FONT_TITLE = ("Segoe UI",  16, "bold")
FONT_HEAD  = ("Segoe UI",  11, "bold")
FONT_BTN   = ("Segoe UI",  10, "bold")

PAD = 12


# ---------------------------------------------------------------------------
# GUI-compatible Live Manager
# ---------------------------------------------------------------------------

class GUILiveManager:
    """Drop-in replacement for LiveManager that routes events to the GUI.

    The real LiveManager drives a Rich Live terminal display; this one
    instead puts messages onto a thread-safe queue that the GUI polls.
    It exposes the same public API used by AlbumDownloader / downloader.py,
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

        self._setup_styles()
        self._build_ui()
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
                     font=FONT_UI,  borderwidth=0, relief="flat")
        s.configure("TFrame",        background=BG)
        s.configure("Card.TFrame",   background=PANEL, relief="flat")

        # Notebook / tabs
        s.configure("TNotebook",          background=BG, borderwidth=0)
        s.configure("TNotebook.Tab",
                    background=BORDER, foreground=TEXT_SEC,
                    padding=(18, 8), font=FONT_UI)
        s.map("TNotebook.Tab",
              background=[("selected", PANEL), ("active", "#EBF2FB")],
              foreground=[("selected", ACCENT), ("active", ACCENT)])

        # Buttons
        s.configure("Accent.TButton",
                    background=ACCENT, foreground="#FFFFFF",
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
              background=[("active", "#EBF2FB"), ("disabled", PANEL)],
              foreground=[("disabled", MUTED)])

        s.configure("Danger.TButton",
                    background="#E74C3C", foreground="#FFFFFF",
                    font=FONT_BTN, padding=(16, 8),
                    relief="flat", borderwidth=0)
        s.map("Danger.TButton",
              background=[("active", "#C0392B"), ("disabled", BORDER)])

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
                    troughcolor=BORDER, background="#8E44AD",
                    thickness=10, borderwidth=0)

        # Separator
        s.configure("TSeparator", background=BORDER)

        # Scrollbar
        s.configure("TScrollbar",
                    background=BORDER, troughcolor=BG,
                    arrowcolor=MUTED, borderwidth=0)

        # Combobox
        s.configure("TCombobox",
                    fieldbackground=PANEL, background=PANEL, foreground=TEXT,
                    arrowcolor=TEXT)

    # ------------------------------------------------------------------ UI

    def _build_ui(self) -> None:
        # ── Header bar ──────────────────────────────────────────────────────
        """Build the header bar, status bar, log, progress bars and the tab notebook."""
        hdr = tk.Frame(self, bg=PANEL, height=64)
        hdr.pack(fill="x", side="top")
        hdr.pack_propagate(False)

        tk.Label(hdr, text="⬇  EHentai Downloader",
                 bg=PANEL, fg=TEXT, font=FONT_TITLE).pack(
                     side="left", padx=PAD*2, pady=12)

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
        self._format_var = StringVar(value=self._format_options[0])
        self._prev_format = self._format_options[0]

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

        # Sequential rename (01..N) once an album is complete
        self._rename_var = BooleanVar(value=RENAME_SEQUENTIAL)
        self._rename_btn = tk.Button(
            hdr_right,
            bg=PANEL, font=FONT_SMALL,
            relief="flat", cursor="hand2",
            command=self._toggle_rename,
        )
        self._rename_btn.pack(side="left", padx=(0, PAD*2))
        self._refresh_rename_btn()

        self._open_downloads_btn = tk.Button(
            hdr_right, text="📁  Open Downloads",
            bg=PANEL, fg=ACCENT, font=FONT_SMALL,
            relief="flat", cursor="hand2",
            command=self._open_downloads_folder,
        )
        self._open_downloads_btn.pack(side="right")

        ttk.Separator(self, orient="horizontal").pack(side="top", fill="x")

        # ── Status bar (pinned to bottom) ───────────────────────────────────
        ttk.Separator(self, orient="horizontal").pack(side="bottom", fill="x")
        sb = tk.Frame(self, bg=BG)
        sb.pack(side="bottom", fill="x")
        self._status_var = StringVar(value="Ready.")
        ttk.Label(sb, textvariable=self._status_var,
                  style="Status.TLabel").pack(
                      side="left", padx=PAD, pady=4)

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

        self._nb.add(self._tab_single, text="  🔗  Single Download  ")
        self._nb.add(self._tab_batch,  text="  📋  Batch Download   ")
        self._nb.add(self._tab_retry,  text="  🔄  Retry Failed     ")

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
        ttk.Label(card, text="Paste an E-Hentai / ExHentai gallery URL and click Start.",
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
            insertbackground=TEXT, selectbackground="#D5E8F5",
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
            background="#F4F6F8",
        )
        self._batch_editor.tag_configure(
            "active_batch_line",
            background="#D6EAF8",
            foreground="#154360",
        )
        self._batch_editor.tag_configure(
            "done_batch_line",
            background="#E8F8F5",
            foreground="#145A32",
        )
        self._batch_editor.tag_configure(
            "fail_batch_line",
            background="#FADBD8",
            foreground="#78281F",
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
        self._batch_btn.pack(side="right")

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
            font=FONT_MONO, bg="#FAFAFA", fg=TEXT,
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
            background="#D6EAF8",
            foreground="#154360",
        )
        self._retry_list.tag_configure(
            "done_batch_line",
            background="#E8F8F5",
            foreground="#145A32",
        )
        self._retry_list.tag_configure(
            "fail_batch_line",
            background="#FADBD8",
            foreground="#78281F",
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
        self._log_box.tag_configure("success_banner", foreground="#1A6B3A",  font=("Consolas", 9, "bold"))
        self._log_box.tag_configure("warn_banner",    foreground="#B8580A",  font=("Consolas", 9, "bold"))
        self._log_box.tag_configure("warn_url",       foreground="#B8580A",  font=("Consolas", 9, "bold"))
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
        elif any(w in low for w in ("success", "complete", "downloaded", "renamed", "ok")):
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

    def _toggle_rename(self) -> None:
        """Flip the 'Rename 01…N' option."""
        self._rename_var.set(not self._rename_var.get())
        self._refresh_rename_btn()

    def _refresh_rename_btn(self) -> None:
        """Update the 'Rename 01…N' button text and colour to match its state."""
        if self._rename_var.get():
            self._rename_btn.configure(text="🔢  Rename 01…N: ON", fg=ACCENT, activeforeground=ACCENT2)
        else:
            self._rename_btn.configure(text="🔢  Rename 01…N: OFF", fg=MUTED, activeforeground=TEXT_SEC)

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

        from downloader import gallery_name

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
        # The rename option is read when a download starts: lock it while one is running
        self._rename_btn.configure(state=state)

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
        if self._running:
            if not messagebox.askyesno(
                "Close Application",
                "A download is in progress. Do you really want to stop it and close the program?",
            ):
                return
            append_session_banner("APPLICATION CLOSED (running download interrupted by the user)")
        else:
            append_session_banner("APPLICATION CLOSED")
        # Keep the list on disk: downloaded lines go away now, everything else is saved
        try:
            self._flush_pending_removals()
            self._save_urls_txt(quiet=True)
        except Exception:
            pass
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
        from downloader import parse_url_line, remember_gallery_name
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
        from downloader import find_duplicates, format_url_line, gallery_name
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
        from retry import collect_retry_urls
        from downloader import format_url_line
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
        from downloader import save_failed_urls
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
            target=self._run_single, args=(url, fmt, self._rename_var.get()), daemon=True,
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
            target=self._run_batch, args=(fmt, self._rename_var.get()), daemon=True,
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
        from downloader import gallery_key, gallery_status, parse_url_line
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
        from downloader import gallery_status, parse_url_line, sort_lines_by_size
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
        from retry import collect_retry_urls
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
        threading.Thread(target=self._run_retry, args=(fmt, self._rename_var.get()), daemon=True).start()

    def _begin_download(self, editor_editable: bool = False) -> None:
        """Reset progress bars and log, and lock the controls."""
        self._running = True
        self._set_buttons_state("disabled", editor_editable=editor_editable)
        self._clear_log()
        self._bar1["value"] = 0
        self._bar1_pct.configure(text="0%")
        self._bar2["value"] = 0
        self._bar2_pct.configure(text="0%")

    # ------------------------------------------------------------------ background workers

    def _run_single(self, url: str, image_format: str = "original", rename: bool = RENAME_SEQUENTIAL) -> None:
        """Worker thread: download one album and report the result to the GUI."""
        success = False
        failed_by_url: dict[str, list[str]] = {}
        append_session_banner(f"SINGLE DOWNLOAD START: {url}")
        try:
            from downloader import download_album
            lm = GUILiveManager(log_queue=self._q, mode="single")
            ok, failed_items = download_album(url, lm, image_format=image_format, rename_sequential=rename)
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

    def _run_batch(self, image_format: str = "original", rename: bool = RENAME_SEQUENTIAL) -> None:
        """Worker thread: download the batch list one album after another.

        Tasks come from a live queue filled by the GUI thread, so links added at the bottom
        of the list while the batch runs are downloaded too.
        """
        success = True
        failed_by_url: dict[str, list[str]] = {}
        done_count = 0
        try:
            from downloader import download_album, format_url_line, gallery_name, update_failed_url

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
                        url, lm, image_format=image_format, rename_sequential=rename, name_hint=name,
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
            from downloader import courtesy_pause, format_url_line, verify_gallery
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
            from downloader import courtesy_pause, format_url_line, verify_gallery
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

    def _run_retry(self, image_format: str = "original", rename: bool = RENAME_SEQUENTIAL) -> None:
        """Worker thread: retry every album of the failed queue and keep only those still failing."""
        success = True
        failed_by_url: dict[str, list[str]] = {}
        try:
            from retry import collect_retry_urls
            from downloader import download_album, gallery_name, save_failed_urls

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
                    name_hint=gallery_name(url),
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
    script_dir = Path(__file__).resolve().parent
    os.chdir(script_dir)

    app = EHentaiGUI()
    app.mainloop()


if __name__ == "__main__":
    main()
