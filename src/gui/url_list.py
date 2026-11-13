"""The batch list editor and the retry queue: load / save, duplicates, line colours, estimates."""

# pylint: disable=protected-access,too-many-lines
from __future__ import annotations

import os
from pathlib import Path
from tkinter import (
    END,
    messagebox,
)

from src.config import (
    AUTOSAVE_INTERVAL,
    DONE_LINE_REMOVE_DELAY,
)

from src.gui.style import (
    ts as _ts,
)


class UrlListMixin:  # pylint: disable=too-few-public-methods,no-member
    """The batch list editor and the retry queue: load / save, duplicates, line colours, estimates. Mixed into EHentaiGUI."""

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

    def _clear_batch_marks(self) -> None:
        """Remove the line highlights (and the task tags of a finished batch) from the editor."""
        self._flush_pending_removals()
        for tag in ("queued_batch_line", "active_batch_line", "done_batch_line", "fail_batch_line"):
            self._batch_editor.tag_remove(tag, "1.0", END)
        for tag in self._batch_editor.tag_names():
            if tag.startswith("task_"):
                self._batch_editor.tag_delete(tag)

    def _task_line(self, task_id: int) -> int | None:
        """Current line number of a batch task, or None if the user deleted its line."""
        ranges = self._batch_editor.tag_ranges(f"task_{task_id}")
        if not ranges:
            return None
        return int(str(ranges[0]).split(".", maxsplit=1)[0])

    def _scan_batch_editor(self, live: bool = True) -> None:
        """GUI thread: give every link line not yet claimed to the running batch.

        Claimed lines get a "task_<id>" tag. Links that were never verified also go to the
        parallel link check. A second link to a gallery already in this batch is skipped.

        live=True is the periodic scan while the batch runs: there a line that is not a
        valid gallery link is left alone, because the user may still be typing or pasting
        it (a half-finished line such as "v" must not become a failed gallery). It is
        picked up as soon as it becomes a valid link. The first scan (live=False) takes
        every line: invalid links were already confirmed by the user at start.
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
                    first = int(str(ranges[i]).split(".", maxsplit=1)[0])
                    last = int(str(ranges[i + 1]).split(".", maxsplit=1)[0])
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
                if live and not valid:
                    continue  # not (yet) a gallery link: wait until the line is complete
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

    def _refresh_session_counter(self) -> None:
        """Show how many images were downloaded since the program was opened."""
        n = self._session_images
        self._session_var.set(f"🖼  This session: {n:,} image{'s' if n != 1 else ''}")

    def _batch_estimate(self) -> dict:
        """Estimate the images of the batch list, leaving out lines the running batch has finished."""
        from src.cli.downloader import estimate_list_images
        lines = self._batch_editor.get("1.0", "end-1c").splitlines()
        finished: set[int] = set()
        for task_id, state in list(self._task_state.items()):
            if state == "done":
                line_no = self._task_line(task_id)
                if line_no:
                    finished.add(line_no)
        return estimate_list_images(
            [line for no, line in enumerate(lines, start=1) if no not in finished]
        )

    def _estimate_tick(self) -> None:
        """Refresh the image estimate under the batch list (once a second)."""
        if self._closed:
            return
        try:
            est = self._batch_estimate()
        except Exception:  # pylint: disable=broad-except
            est = None
        if est is not None:
            known, unverified, bad = est["known"], est["unverified"], est["bad"]
            prefix = "Still to download" if self._batch_accepting else "Estimate"
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
            if text != self._estimate_text:
                self._estimate_text = text
                self._estimate_var.set(text)
        self.after(1000, self._estimate_tick)

    def _on_verify_done(self, ok_count: int, bad_count: int, domains: dict) -> None:
        """Re-enable the UI, save the annotated list and print the verification summary."""
        self._running = False
        self._set_buttons_state("normal")
        self._save_urls_txt()
        sources = ", ".join(f"{d}: {n}" for d, n in sorted(domains.items())) or "none"
        summary = f"{ok_count} valid, {bad_count} with problems. Download source → {sources}"
        # The image estimate goes in the log only: the status bar shows the session counter
        images = self._batch_estimate()["images"]
        log_summary = summary + (f". About {images:,} image(s) to download" if images else "")
        self._q.put(("LOG", _ts(), "Verify completed", log_summary))
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
