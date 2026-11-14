"""Background threads: single, batch, link check and retry downloads.

Batch and retry run one lane per site (E-Hentai / ExHentai and nhentai) at the same time.
"""

# pylint: disable=protected-access,too-many-lines
from __future__ import annotations

import queue
import threading

from src.config import (
    CREATE_CBZ,
    RENAME_SEQUENTIAL,
)
from src import throttle
from src.file_utils import append_session_banner
from src.gui.live_manager import GUILiveManager

from src.gui.style import (
    ts as _ts,
)


class WorkersMixin:  # pylint: disable=too-few-public-methods,no-member
    """Background threads: single, batch, link check and retry downloads. Mixed into EHentaiGUI."""

    def _begin_download(self, editor_editable: bool = False) -> None:
        """Reset progress bars and log, and lock the controls."""
        self._running = True
        self._set_buttons_state("disabled", editor_editable=editor_editable)
        throttle.set_paused(False)
        self._pause_btn.configure(state="normal", text="⏸  Pause")
        self._clear_log()
        self._bar1["value"] = 0
        self._bar1_pct.configure(text="0%")
        self._reset_lane_bars()

    def _run_single(
        self, url: str, image_format: str = "original", rename: bool = RENAME_SEQUENTIAL, cbz: bool = CREATE_CBZ,
    ) -> None:
        """Worker thread: download one album and report the result to the GUI."""
        success = False
        failed_by_url: dict[str, list[str]] = {}
        append_session_banner(f"SINGLE DOWNLOAD START: {url}")
        try:
            from src.cli.downloader import download_album
            lm = GUILiveManager(log_queue=self._q, mode="single", lane=site_lane(url))
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

    # ------------------------------------------------------------ batch: one lane per site
    #
    # E-Hentai / ExHentai and nhentai are different servers with their own limits, so the
    # batch runs one "lane" (worker thread) per site: a gallery of each site downloads at
    # the same time, while galleries of the same site still go one after another. A lane
    # starts when its first link is queued and stops when its queue is empty; the last
    # lane to stop closes the batch.

    def _ensure_lane(self, lane: str) -> None:
        """Start the worker of lane if it is not running. Call with _batch_lock held."""
        if self._lane_running.get(lane) or not self._batch_accepting:
            return
        self._lane_running[lane] = True
        threading.Thread(target=self._run_batch_lane, args=(lane,), daemon=True).start()

    def _next_batch_task(self, lane: str) -> tuple[dict | None, bool]:
        """Worker thread: (next task of lane, False), or (None, last) when the lane is empty.

        Before giving up it asks the GUI to look once more for lines added in the meantime.
        The lane is stopped under the lock, so a line added later is never lost: the GUI
        starts the lane again. last is True when no other lane is running: the batch is over.
        """
        lane_queue = self._batch_queues[lane]
        try:
            return lane_queue.get_nowait(), False
        except queue.Empty:
            pass
        scanned = threading.Event()
        self._q.put(("BATCH_SCAN", scanned))
        scanned.wait(timeout=5)
        with self._batch_lock:
            try:
                return lane_queue.get_nowait(), False
            except queue.Empty:
                self._lane_running[lane] = False
                last = not any(self._lane_running.values())
                if last:
                    self._batch_accepting = False
                return None, last

    def _run_batch_lane(self, lane: str) -> None:
        """Worker thread: download the galleries of one site, one after another."""
        last = False
        try:
            while True:
                task, last = self._next_batch_task(lane)
                if task is None:
                    return
                try:
                    self._run_batch_task(task, lane)
                except Exception as exc:  # one gallery must not stop the lane
                    self._q.put(("LOG", _ts(), "ERROR", f"{task['url']}: {exc}"))
                    with self._batch_lock:
                        self._batch_result["success"] = False
                        self._task_state[task["id"]] = "done"
                    self._q.put(("TASK_DONE", task["id"], False))
        finally:
            if last:
                self._finish_batch()

    def _run_batch_task(self, task: dict, lane: str) -> None:
        """Worker thread: download (or skip) one gallery of the batch."""
        from src.cli.downloader import download_album, format_url_line, gallery_name, update_failed_url

        image_format, rename, cbz = self._batch_options
        task_id, url, name = task["id"], task["url"], task["name"]
        with self._batch_lock:
            self._task_state[task_id] = "active"
            self._batch_result["started"] += 1
            started = self._batch_result["started"]
            total_items = self._batch_total
        self._q.put(("TASK_ACTIVE", task_id, started, total_items, name or gallery_name(url) or url))

        lm = None
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
                batch_total=total_items,
                progress=self._batch_progress,
                log_prefix=f"[{lane}] ",
                lane=lane,
            )
            ok, failed_items = download_album(
                url, lm, image_format=image_format, rename_sequential=rename, name_hint=name, create_cbz=cbz,
            )
            # Rewrite the line as "STATUS | IMAGES | TITLE | LINK" with what is now known
            self._q.put(("TASK_ANNOTATE", task_id, format_url_line(url)))
        self._batch_progress.album_finished(lm)

        with self._batch_lock:
            self._task_state[task_id] = "done"
            if not ok or failed_items:
                self._batch_result["failed"][url] = failed_items
                self._batch_result["success"] = False
        self._q.put(("TASK_DONE", task_id, ok and not failed_items))

    def _finish_batch(self) -> None:
        """Worker thread (last lane): close the batch and tell the GUI."""
        # failed_downloads.txt is already updated album by album by download_album():
        # albums that failed in earlier sessions stay in the queue.
        with self._batch_lock:
            self._batch_accepting = False
            done = self._batch_result["started"]
            success = self._batch_result["success"]
            failed_by_url = dict(self._batch_result["failed"])
        self._q.put(("UPDATE_BAR1", 1.0, f"{done}/{done} (100%)"))
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
        """Worker thread: retry every album of the failed queue and keep only those still failing.

        As in the batch, E-Hentai / ExHentai and nhentai albums are retried in two lanes at
        the same time.
        """
        success = True
        failed_by_url: dict[str, list[str]] = {}
        try:
            from src.cli.retry import collect_retry_urls
            from src.cli.downloader import save_failed_urls

            retry_urls = collect_retry_urls()
            total = len(retry_urls)
            append_session_banner(f"RETRY START ({total} albums)")
            lanes: dict[str, list[tuple[int, str]]] = {}
            for idx, url in enumerate(retry_urls):
                lanes.setdefault(site_lane(url), []).append((idx + 1, url))

            progress = BatchProgress(self._q, lambda: total, "Retry Total:")
            remaining: dict[int, str] = {}
            counter = {"started": 0}
            lock = threading.Lock()
            threads = [
                threading.Thread(
                    target=self._run_retry_lane,
                    args=(lane, items, total, progress, (image_format, rename, cbz), remaining, failed_by_url,
                          counter, lock),
                    daemon=True,
                )
                for lane, items in lanes.items()
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

            still_failing = [remaining[line_no] for line_no in sorted(remaining)]
            success = not still_failing
            save_failed_urls(still_failing)

            self._q.put((
                "LOG", _ts(), "Retry completed",
                f"Processed: {total} | Still failing: {len(still_failing)}",
            ))

            self._q.put(("UPDATE_BAR1", 1.0, f"{total}/{total} (100%)"))

            self._q.put(("RELOAD_LISTS",))  # reload both lists on the GUI thread
        except Exception as exc:
            self._q.put(("LOG", _ts(), "ERROR", str(exc)))
            success = False
        finally:
            self._q.put(("DONE", success, failed_by_url))

    def _run_retry_lane(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self, lane: str, items: list[tuple[int, str]], total: int, progress: "BatchProgress",
        options: tuple, remaining: dict[int, str], failed_by_url: dict[str, list[str]],
        counter: dict[str, int], lock: threading.Lock,
    ) -> None:
        """Worker thread: retry the albums of one site, one after another."""
        from src.cli.downloader import download_album, gallery_name

        image_format, rename, cbz = options
        for line_no, url in items:
            with lock:
                counter["started"] += 1
                started = counter["started"]
            self._q.put(("RETRY_HIGHLIGHT", line_no, started, total, gallery_name(url) or url))
            lm = GUILiveManager(
                log_queue=self._q,
                mode="retry",
                batch_total=total,
                line_no=line_no,
                progress=progress,
                log_prefix=f"[{lane}] ",
                lane=lane,
            )
            try:
                ok, failed_items = download_album(
                    url, lm, image_format=image_format, rename_sequential=rename,
                    name_hint=gallery_name(url), create_cbz=cbz,
                )
            except Exception as exc:  # one album must not stop the lane
                self._q.put(("LOG", _ts(), "ERROR", f"{url}: {exc}"))
                ok, failed_items = False, [str(exc)]
            progress.album_finished(lm)
            if not ok or failed_items:
                with lock:
                    remaining[line_no] = url
                    failed_by_url[url] = failed_items
                self._q.put(("RETRY_LINE_DONE", line_no, False))
            else:
                self._q.put(("RETRY_LINE_DONE", line_no, True))


def site_lane(url: str) -> str:
    """Download lane of a link: "nhentai" for nhentai.net, "E-Hentai" for everything else."""
    from src import nhentai
    return throttle.SITE_NH if nhentai.is_nhentai_url(url or "") else throttle.SITE_EH


class BatchProgress:
    """Progress bars of a batch / retry where albums of different sites download at once.

    Bar 1 (total): albums finished plus the share already done of those in progress.
    Site bars: images of the album in progress on that site (E-Hentai / ExHentai or nhentai).
    """

    def __init__(self, gui_queue: queue.Queue, total_fn, total_label: str = "Batch Total:") -> None:
        """total_fn() returns the number of albums of the batch (it grows while links are added)."""
        self._q = gui_queue
        self._total_fn = total_fn
        self._total_label = total_label
        self._lock = threading.Lock()
        self._albums: dict[int, tuple[int, int]] = {}  # id(live manager) -> (done, total)
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
        """Send the total bar to the GUI (lock held)."""
        total = max(1, self._total_fn(), self._finished + len(self._albums))
        active = list(self._albums.values())
        overall = min(1.0, (self._finished + sum(d / t for d, t in active)) / total)
        self._q.put(("UPDATE_BAR1", overall, f"{self._finished}/{total} ({overall * 100:.0f}%)"))
