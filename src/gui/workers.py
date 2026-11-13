"""Background threads: single, batch, link check and retry downloads."""

# pylint: disable=protected-access,too-many-lines
from __future__ import annotations

import queue

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
        self._bar2["value"] = 0
        self._bar2_pct.configure(text="0%")

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
