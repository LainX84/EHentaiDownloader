"""GUILiveManager: replaces the Rich terminal display of the CLI by pushing progress/log messages onto a queue."""

# pylint: disable=protected-access,too-many-lines
from __future__ import annotations

import datetime
import logging
import queue
import shutil
import threading
import time




# Same interface as the CLI LiveManager: some arguments are only used by the terminal display.
class GUILiveManager:  # pylint: disable=unused-argument
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
        def start(self):
            """Do nothing."""
        def stop(self):
            """Do nothing."""
        def update(self, _):
            """Do nothing."""

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

    def count_downloaded_image(self) -> None:
        """One image was really downloaded and saved (not skipped): bump the session counter."""
        self._log_queue.put(("IMAGE_SAVED",))

    def start(self) -> None:
        """No-op: kept for API compatibility with the terminal LiveManager."""

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


class QueueLogHandler(logging.Handler):
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
