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

from src.config import RENAME_SEQUENTIAL, SESSION_LOG
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

        self._setup_styles()
        self._build_ui()
        self._install_logging()
        self._poll_queue()
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
                  text="One URL per line. Changes are saved to URLs.txt before starting.",
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

        # Buttons row
        btn_row = tk.Frame(card, bg=PANEL)
        btn_row.grid(row=3, column=0, columnspan=2,
                     padx=PAD, pady=(6, PAD), sticky="ew")

        _btn(btn_row, "🔄  Reload", self._load_urls_txt,
             style="Ghost.TButton").pack(side="left", padx=(0, 6))
        _btn(btn_row, "💾  Save", self._save_urls_txt,
             style="Ghost.TButton").pack(side="left", padx=(0, 6))
        _btn(btn_row, "🗑  Clear All", self._clear_urls_txt,
             style="Ghost.TButton").pack(side="left")

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
                    self._status_var.set(f"[{cur_idx}/{total_cnt}] Downloading line {line_no}: {url}")
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

        total_failed_items = sum(len(items) if items else 1 for items in failed_map.values())

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
            append_session_banner(f"DOWNLOAD PARTIALLY FAILED — {n} items not downloaded")
            num_links = len([k for k in failed_map.keys() if k])
            suffix_links = f" (across {num_links} albums)" if num_links > 1 else ""
            img_word = "image" if n == 1 else "images"

            # ── Orange banner ─────────────────────────────────────────
            self._log_box.insert(END, f"\n[{ts}] {sep}\n", "warn_banner")
            self._log_box.insert(END,
                f"[{ts}] ⚠  DOWNLOAD PARTIALLY FAILED — {n} {img_word} not downloaded{suffix_links}:\n",
                "warn_banner",
            )
            for album_url, items in failed_map.items():
                if album_url:
                    self._log_box.insert(END, f"[{ts}] 🔗 {album_url}\n", "warn_url")
                if not items:
                    self._log_box.insert(END, f"[{ts}]    • (Could not download the album or find any images)\n", "warn_item")
                else:
                    for item in items:
                        # Show only the last meaningful segment (filename or page URL)
                        label = item.split("/")[-1] or item
                        self._log_box.insert(END, f"[{ts}]    • {label}\n", "warn_item")
            self._log_box.insert(END, f"[{ts}] {sep}\n", "warn_banner")
            self._log_box.insert(END,
                f"[{ts}] Use 'Retry Failed' to retry the missing files.\n",
                "warn_banner",
            )
            file_word = "missing file" if n == 1 else "missing files"
            self._status_var.set(
                f"⚠ Partial download: {n} {file_word} — use Retry Failed."
            )

        self._log_box.see(END)
        self._log_box.configure(state="disabled")

    def _set_buttons_state(self, state: str) -> None:
        """Enable or disable every control that must not change during a download."""
        for btn in (self._single_btn, self._single_paste_btn, self._batch_btn, self._retry_btn):
            btn.configure(state=state)
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
        self._closed = True
        self.destroy()

    def _on_exit(self) -> None:
        """Fallback hook when the application process exits."""
        if not getattr(self, "_closed", False):
            self._closed = True
            append_session_banner("APPLICATION CLOSED")

    # ------------------------------------------------------------------ URLs.txt

    def _load_urls_txt(self) -> None:
        """Load URLs.txt into the batch editor."""
        path = Path("URLs.txt")
        content = path.read_text(encoding="utf-8") if path.exists() else ""
        self._batch_editor.delete("1.0", END)
        self._batch_editor.insert("1.0", content)

    def _save_urls_txt(self) -> None:
        """Save the batch editor content to URLs.txt."""
        content = self._batch_editor.get("1.0", END).rstrip("\n") + "\n"
        Path("URLs.txt").write_text(content, encoding="utf-8")
        self._status_var.set("URLs.txt saved.")

    def _clear_urls_txt(self) -> None:
        """Empty both the batch editor and URLs.txt."""
        self._batch_editor.delete("1.0", END)
        Path("URLs.txt").write_text("", encoding="utf-8")
        self._status_var.set("URLs.txt cleared.")

    # ------------------------------------------------------------------ failed list

    def _load_retry_list(self) -> None:
        """Collect retry candidates and show them (read-only)."""
        # Import lazily to avoid triggering heavy imports at startup
        from retry import collect_retry_urls
        urls = collect_retry_urls()
        self._retry_list.configure(state="normal")
        self._retry_list.delete("1.0", END)
        if urls:
            self._retry_list.insert("1.0", "\n".join(urls))
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

        lines = self._batch_editor.get("1.0", "end-1c").splitlines()
        tasks = [
            (idx, line.strip())
            for idx, line in enumerate(lines, start=1)
            if line.strip() and not line.strip().startswith("#")
        ]
        if not tasks:
            messagebox.showwarning(
                "No URLs",
                "URLs.txt is empty.\nAdd at least one URL before starting.",
            )
            return

        fmt = self._get_selected_format()
        self._begin_download()
        self._bar1_lbl.configure(text="Batch Total:")
        self._bar2_lbl.configure(text="Current Album:")
        self._batch_editor.tag_remove("active_batch_line", "1.0", END)
        self._batch_editor.tag_remove("done_batch_line", "1.0", END)
        self._batch_editor.tag_remove("fail_batch_line", "1.0", END)
        self._status_var.set(f"Batch download [{fmt}]: {len(tasks)} URL(s)")
        threading.Thread(target=self._run_batch, args=(fmt, self._rename_var.get()), daemon=True).start()

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

    def _begin_download(self) -> None:
        """Reset progress bars and log, and lock the controls."""
        self._running = True
        self._set_buttons_state("disabled")
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

    def _run_batch(self, image_format: str = "original", rename: bool = RENAME_SEQUENTIAL) -> None:
        """Worker thread: download every album of the batch list, one after another."""
        success = True
        failed_by_url: dict[str, list[str]] = {}
        try:
            from src.config import URLS_FILE
            from src.file_utils import write_file
            from downloader import (
                download_album,
                save_failed_urls,
            )

            lines = self._batch_editor.get("1.0", "end-1c").splitlines()
            tasks: list[tuple[int, str]] = []
            for line_idx, line in enumerate(lines, start=1):
                u = line.strip()
                if u and not u.startswith("#"):
                    tasks.append((line_idx, u))

            total_items = len(tasks)
            append_session_banner(f"BATCH DOWNLOAD START ({total_items} albums)")
            failed_albums: list[str] = []

            for idx, (line_no, url) in enumerate(tasks):
                self._q.put(("BATCH_HIGHLIGHT", line_no, idx + 1, total_items, url))

                lm = GUILiveManager(
                    log_queue=self._q,
                    mode="batch",
                    batch_idx=idx,
                    batch_total=total_items,
                    line_no=line_no,
                )
                ok, failed_items = download_album(url, lm, image_format=image_format, rename_sequential=rename)
                if not ok or failed_items:
                    failed_albums.append(url)
                    failed_by_url[url] = failed_items
                    success = False
                    self._q.put(("BATCH_LINE_DONE", line_no, False))
                else:
                    self._q.put(("BATCH_LINE_DONE", line_no, True))

            save_failed_urls(failed_albums)

            # Clear URLs.txt after batch completes; failed items are kept in failed_downloads.txt
            write_file(URLS_FILE, "")

            self._q.put((
                "PROGRESS_BARS",
                1.0, f"{total_items}/{total_items} (100%)",
                1.0, "100%",
            ))

            self.after(0, self._load_retry_list)
            self.after(0, self._load_urls_txt)
        except Exception as exc:
            self._q.put(("LOG", _ts(), "ERROR", str(exc)))
            success = False
        finally:
            self._q.put(("DONE", success, failed_by_url))

    def _run_retry(self, image_format: str = "original", rename: bool = RENAME_SEQUENTIAL) -> None:
        """Worker thread: retry every album of the failed queue and keep only those still failing."""
        success = True
        failed_by_url: dict[str, list[str]] = {}
        try:
            from retry import collect_retry_urls
            from downloader import download_album, save_failed_urls

            retry_urls = collect_retry_urls()
            total = len(retry_urls)
            append_session_banner(f"RETRY START ({total} albums)")
            remaining: list[str] = []

            for idx, url in enumerate(retry_urls):
                line_no = idx + 1
                self._q.put(("RETRY_HIGHLIGHT", line_no, idx + 1, total, url))

                lm = GUILiveManager(
                    log_queue=self._q,
                    mode="retry",
                    batch_idx=idx,
                    batch_total=total,
                    line_no=line_no,
                )
                ok, failed_items = download_album(url, lm, image_format=image_format, rename_sequential=rename)
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

            self.after(0, self._load_retry_list)
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
