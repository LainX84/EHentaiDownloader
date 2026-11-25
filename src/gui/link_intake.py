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

# tkinterdnd2 gives the drop methods to every widget, but not to the main window (tk.Tk):
# the mixin brings them, so the window itself (its borders, the space between widgets) is a
# drop target too.
_DnDBase = TkinterDnD.DnDWrapper if TkinterDnD is not None else object


class LinkIntakeMixin(_DnDBase):  # pylint: disable=too-few-public-methods,no-member
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
        """Accept links (and text files of links) dropped anywhere on the main window.

        Every widget of the window is a drop target, not only the batch list: the header, the
        tabs, the progress bars, the log, the status bar... Widgets created later (a tab built
        on first open, a rebuilt panel) become drop targets when they appear (<Map>). Dialog
        windows are left out. False if drag and drop is unavailable.
        """
        if TkinterDnD is None:
            return False
        try:
            TkinterDnD._require(self)  # pylint: disable=protected-access
        except (RuntimeError, tk.TclError) as err:
            logging.info("Drag and drop not available: %s", err)
            return False
        self._dnd_targets: set[str] = set()
        self._dnd_hover_job = None
        self._dnd_status_before: str | None = None
        self._register_drop_tree(self)
        # New widgets: registered the first time they are shown
        self.bind_all("<Map>", self._on_widget_mapped, add="+")
        return True

    def _register_drop_tree(self, widget) -> None:
        """Make widget and all its children (same window only) drop targets."""
        stack = [widget]
        while stack:
            current = stack.pop()
            self._register_drop_target(current)
            try:
                stack.extend(current.winfo_children())
            except tk.TclError:
                continue

    def _register_drop_target(self, widget) -> None:
        """Make one widget a drop target (once). Menus and other windows are skipped."""
        if not isinstance(widget, tk.Misc) or isinstance(widget, tk.Menu):
            return
        try:
            path = str(widget)
            if path in self._dnd_targets or not widget.winfo_exists():
                return
            if widget is not self and widget.winfo_toplevel() is not self:
                return  # a dialog (Settings, Account...) or a popup
            widget.drop_target_register(DND_TEXT, DND_FILES)
            widget.dnd_bind("<<DropEnter>>", self._on_drag_enter)
            widget.dnd_bind("<<DropPosition>>", self._on_drag_position)
            widget.dnd_bind("<<DropLeave>>", self._on_drag_leave)
            widget.dnd_bind("<<Drop>>", self._on_drop)
        except (AttributeError, KeyError, tk.TclError):
            return
        self._dnd_targets.add(path)

    def _on_widget_mapped(self, event) -> None:
        """<Map> of any widget: register it (and its children) if it is new."""
        widget = event.widget
        if isinstance(widget, tk.Misc) and str(widget) not in self._dnd_targets:
            self._register_drop_tree(widget)

    @staticmethod
    def _drop_action(event) -> str:
        """The action to answer to the drag source: always a copy (the link stays where it is)."""
        actions = str(getattr(event, "actions", "") or "")
        if actions and "copy" not in actions and "link" in actions:
            return "link"
        return "copy"

    def _on_drag_enter(self, event) -> str:
        """Something is dragged over the window: say what will happen."""
        try:
            if self._dnd_hover_job is not None:
                self.after_cancel(self._dnd_hover_job)
                self._dnd_hover_job = None
            if self._dnd_status_before is None:
                self._dnd_status_before = self._status_var.get()
                self._status_var.set("⬇ Release to add the gallery links to the batch list.")
        except tk.TclError:
            pass
        return self._drop_action(event)

    def _on_drag_position(self, event) -> str:
        """Answered at every mouse move of a drag: must stay instant."""
        return self._drop_action(event)

    def _on_drag_leave(self, _event=None) -> None:
        """The drag left a widget: restore the status bar unless it entered another one."""
        try:
            if self._dnd_hover_job is None:
                self._dnd_hover_job = self.after(150, self._end_drag_hover)
        except tk.TclError:
            pass

    def _end_drag_hover(self, restore: bool = True) -> None:
        """The drag is over (left the window or dropped)."""
        if self._dnd_hover_job is not None:
            try:
                self.after_cancel(self._dnd_hover_job)
            except tk.TclError:
                pass
        self._dnd_hover_job = None
        before, self._dnd_status_before = self._dnd_status_before, None
        if restore and before is not None:
            self._status_var.set(before)

    def _on_drop(self, event) -> str:
        """Links dragged from a browser, a text selection or .txt / .url files.

        The drop is answered at once and the links are added a moment later: the drag
        source (the browser) waits for this answer, and a slow answer (big list, a file to
        read, a busy window during a download) leaves the next drags refused until it
        times out.
        """
        data = str(getattr(event, "data", "") or "")
        try:
            self._end_drag_hover(restore=False)
            self.after(1, lambda: self._process_drop(data))
        except tk.TclError:
            pass
        return self._drop_action(event)

    def _process_drop(self, data: str) -> None:
        """Take the gallery links out of what was dropped and add them to the batch list."""
        try:
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
        except Exception as err:  # pylint: disable=broad-except
            logging.warning("Drop: could not add the links: %s", err)

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
