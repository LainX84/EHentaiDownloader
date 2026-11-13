"""Quick ways to add gallery links: paste many at once, drag and drop, clipboard watcher.

Every way ends in _add_links_to_batch(): links are taken out of any text (a web page, a
chat message, a forum post...), links to galleries already in the batch list are skipped,
and the new ones are appended at the bottom of the list (also while a batch is running:
it picks them up like links typed by hand).
"""

from __future__ import annotations

import logging
import tkinter as tk
from pathlib import Path

from src.general_utils import extract_gallery_links
from src.gui.style import ts as _ts

CLIPBOARD_POLL_MS = 1000      # how often the clipboard is read while the watcher is on
_MAX_DROPPED_FILE_BYTES = 2_000_000   # a dropped text file larger than this is ignored

try:  # Drag and drop needs the optional tkinterdnd2 package (in requirements.txt)
    from tkinterdnd2 import DND_FILES, DND_TEXT, TkinterDnD
except ImportError:  # pragma: no cover - the program works without it
    TkinterDnD = None
    DND_FILES = DND_TEXT = ""


class LinkIntakeMixin:  # pylint: disable=too-few-public-methods,no-member
    """Paste / drop / clipboard handling. Mixed into EHentaiGUI."""

    # ------------------------------------------------------------ common

    def _add_links_to_batch(self, links: list[str], source: str, show_tab: bool = True) -> int:
        """Append the links that are not in the batch list yet; return how many were added."""
        from src.cli.downloader import gallery_key
        if not links:
            return 0
        listed = {gallery_key(url) for _, url, _ in self._editor_entries()}
        new_links = []
        for link in links:
            key = gallery_key(link)
            if key not in listed:
                listed.add(key)
                new_links.append(link)
        skipped = len(links) - len(new_links)
        if new_links:
            def append() -> None:
                content = self._batch_editor.get("1.0", "end-1c")
                prefix = "\n" if content and not content.endswith("\n") else ""
                self._batch_editor.insert("end", prefix + "\n".join(new_links) + "\n")
            self._editor_edit(append)
            self._batch_editor.see("end")
            self._save_urls_txt(quiet=True)
        note = f" ({skipped} already in the list)" if skipped else ""
        self._q.put(("LOG", _ts(), source, f"Added {len(new_links)} link(s) to the batch list{note}."))
        self._status_var.set(f"{source}: {len(new_links)} link(s) added{note}.")
        if show_tab and new_links:
            self._nb.select(self._tab_batch)
        return len(new_links)

    def _clipboard_text(self) -> str:
        """Text in the clipboard ("" when empty or not text)."""
        try:
            return self.clipboard_get()
        except tk.TclError:
            return ""

    # ------------------------------------------------------------ paste

    def _on_batch_paste(self, _event=None) -> str | None:
        """Ctrl+V in the batch list: keep only the gallery links of the copied text.

        Copied text without any gallery link is pasted as usual.
        """
        links = extract_gallery_links(self._clipboard_text())
        if not links:
            return None
        self._add_links_to_batch(links, "Paste", show_tab=False)
        return "break"

    def _paste_links_button(self) -> None:
        """'Paste links' button of the batch tab."""
        text = self._clipboard_text()
        links = extract_gallery_links(text)
        if not links:
            self._status_var.set("No gallery links in the clipboard.")
            return
        self._add_links_to_batch(links, "Paste")

    def _paste_single_url(self) -> None:
        """Paste button of the Single tab: the first link goes in the field.

        When the clipboard holds more than one gallery link, offer to add all of them to the
        batch list instead.
        """
        from tkinter import messagebox
        text = self._clipboard_text()
        links = extract_gallery_links(text)
        if len(links) > 1:
            if messagebox.askyesno(
                "Several links",
                f"The clipboard holds {len(links)} gallery links.\n\n"
                "Add all of them to the Batch Download list?\n"
                "(No = put only the first one here)",
            ):
                self._add_links_to_batch(links, "Paste")
                return
        if links:
            self._single_url.set(links[0])
            return
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if lines:
            self._single_url.set(lines[0])

    # ------------------------------------------------------------ drag and drop

    def _setup_drag_and_drop(self) -> bool:
        """Accept links (and text files of links) dropped on the window. False if unavailable."""
        if TkinterDnD is None:
            return False
        try:
            TkinterDnD._require(self)  # pylint: disable=protected-access
        except (RuntimeError, tk.TclError) as err:
            logging.info("Drag and drop not available: %s", err)
            return False
        targets = [self._nb, self._tab_single, self._tab_batch, self._tab_retry, self._batch_editor]
        targets += self._tab_single.winfo_children() + self._tab_batch.winfo_children()
        for widget in targets:
            try:
                widget.drop_target_register(DND_TEXT, DND_FILES)
                widget.dnd_bind("<<Drop>>", self._on_drop)
            except (AttributeError, tk.TclError):
                continue
        return True

    def _on_drop(self, event) -> str:
        """Links dragged from a browser, a text selection or .txt / .url files."""
        data = getattr(event, "data", "") or ""
        texts = [data]
        try:
            items = self.tk.splitlist(data)
        except tk.TclError:
            items = ()
        for item in items:
            path = Path(item)
            try:
                if path.is_file() and path.stat().st_size <= _MAX_DROPPED_FILE_BYTES:
                    texts.append(path.read_text(encoding="utf-8", errors="replace"))
            except (OSError, ValueError):
                continue
        links = extract_gallery_links("\n".join(texts))
        if links:
            self._add_links_to_batch(links, "Drop")
        else:
            self._status_var.set("Nothing to add: no gallery links in what was dropped.")
        return getattr(event, "action", "copy")

    # ------------------------------------------------------------ clipboard watcher

    def _on_watch_clipboard_changed(self) -> None:
        """Settings → Watch clipboard toggled."""
        self._save_settings()
        if self._watch_clipboard_var.get():
            # Links copied before turning it on are not taken
            self._clipboard_seen = self._clipboard_text()
            self._status_var.set("Clipboard watcher ON: copied gallery links go to the batch list.")
            if self._clipboard_job is None:
                self._clipboard_job = self.after(CLIPBOARD_POLL_MS, self._clipboard_tick)
        else:
            self._status_var.set("Clipboard watcher OFF.")

    def _clipboard_tick(self) -> None:
        """While the watcher is on: add the gallery links of newly copied text."""
        self._clipboard_job = None
        if self._closed or not self._watch_clipboard_var.get():
            return
        text = self._clipboard_text()
        if text and text != self._clipboard_seen:
            self._clipboard_seen = text
            links = extract_gallery_links(text)
            if links:
                self._add_links_to_batch(links, "Clipboard", show_tab=not self._running)
        self._clipboard_job = self.after(CLIPBOARD_POLL_MS, self._clipboard_tick)
