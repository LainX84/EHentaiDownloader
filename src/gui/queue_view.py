"""How the batch / retry lists look, and changing the order of the download queue.

A list line is stored as text ("✅ NHENTAI  |  NETORARE  | 123 | Title | link", see
src.cli.downloader); in the window it is shown as

    ✅ [nhentai logo] |  NETORARE  | Title

the site name is replaced by its logo, the favorite tag stands out, and the image count and
the link are hidden (Tk "elide"): they are still in the line, so saving, Retry and the log
keep working with the real link.

Lines not downloading yet can be moved (▲ ▼, Alt+Up / Alt+Down) or removed (✖, Ctrl+Delete):
the batch downloads the queued galleries of each site in the order of the list.
"""

# pylint: disable=protected-access
from __future__ import annotations

from pathlib import Path
from tkinter import font as tkfont, messagebox
import tkinter as tk

from src import paths
from src.gui import style
from src.gui.style import ts as _ts

LINE_TAGS_SUFFIX = "_batch_line"         # state colours of a whole line
DECO_TAGS = ("deco_hidden", "deco_badge", "deco_ok", "deco_ko")
LOGO_FILES = {"EHENTAI": "ehentai.png", "EXHENTAI": "exhentai.png", "NHENTAI": "nhentai.png"}
LOGO_RATIO = 4  # width = 4 × height (40×10 at the smallest size)


def logo_path(site: str) -> Path | None:
    """Logo of a site: the user's own file in <data folder>/logos/ first, then the bundled one."""
    name = LOGO_FILES.get(site)
    if not name:
        return None
    for candidate in (paths.app_dir() / "logos" / name, paths.resource(f"assets/sites/{name}")):
        if candidate.is_file():
            return candidate
    return None


class QueueViewMixin:  # pylint: disable=too-few-public-methods,no-member
    """Look of the list lines and reordering of the queue. Mixed into EHentaiGUI."""

    # ------------------------------------------------------------------ logos

    def _logo_height(self) -> int:
        """Logo height: the height of the list text (at least 10 px)."""
        if not hasattr(self, "_logo_h"):
            try:
                metrics = tkfont.Font(font=style.FONT_MONO).metrics()
                self._logo_h = max(10, int(metrics["ascent"] + metrics["descent"]) - 2)
            except tk.TclError:
                self._logo_h = 10
        return self._logo_h

    def _site_logo(self, site: str):
        """PhotoImage of a site logo sized on the text (None if it can't be loaded)."""
        cache = self.__dict__.setdefault("_logo_cache", {})
        if site in cache:
            return cache[site]
        image = None
        path = logo_path(site)
        if path is not None:
            height = self._logo_height()
            try:
                from PIL import Image, ImageTk  # pylint: disable=import-outside-toplevel
                with Image.open(path) as src:
                    resized = src.convert("RGBA").resize((height * LOGO_RATIO, height), Image.LANCZOS)
                image = ImageTk.PhotoImage(resized, master=self)
            except Exception:  # pylint: disable=broad-except
                image = None
        cache[site] = image
        return image

    def _badge_image(self, label: str, color: str, chars: int):
        """PhotoImage of a favorite-tag label: a pill in its colour, as wide as the tag column.

        An empty label gives a transparent image of the same size (keeps the columns aligned).
        """
        cache = self.__dict__.setdefault("_badge_cache", {})
        key = (label, color, chars)
        if key in cache:
            return cache[key]
        image = None
        try:
            from PIL import Image, ImageDraw, ImageFont, ImageTk  # pylint: disable=import-outside-toplevel
            from src import tags as tag_utils  # pylint: disable=import-outside-toplevel
            scale = 3  # drawn larger, then reduced: smooth edges and text
            height = self._logo_height()
            width = max(height * 2, tkfont.Font(font=style.FONT_MONO).measure("0" * chars) - 4)
            big = Image.new("RGBA", (width * scale, height * scale), (0, 0, 0, 0))
            if label:
                draw = ImageDraw.Draw(big)
                draw.rounded_rectangle((0, 0, width * scale - 1, height * scale - 1),
                                       radius=height * scale // 3, fill=color)
                size = int(height * scale * 0.72)
                font = ImageFont.load_default(size=size)
                while size > 8 and draw.textlength(label, font=font) > (width - 6) * scale:
                    size -= 2
                    font = ImageFont.load_default(size=size)
                box = draw.textbbox((0, 0), label, font=font, stroke_width=1)
                x = (width * scale - (box[2] - box[0])) / 2 - box[0]
                y = (height * scale - (box[3] - box[1])) / 2 - box[1]
                ink = tag_utils.text_color(color)
                draw.text((x, y), label, font=font, fill=ink, stroke_width=1, stroke_fill=ink)
            image = ImageTk.PhotoImage(big.resize((width, height), Image.LANCZOS), master=self)
        except Exception:  # pylint: disable=broad-except
            image = None  # old Pillow: the label stays as coloured text
        cache[key] = image
        return image

    # ------------------------------------------------------------------ decoration

    def _setup_line_tags(self, widget: tk.Text) -> None:
        """Tags used by the decoration of a list widget."""
        widget.tag_configure("deco_hidden", elide=True)
        widget.tag_configure("deco_badge", background=style.BADGE_BG, foreground=style.BADGE_FG,
                             font=style.FONT_MONO_BOLD)
        widget.tag_configure("deco_ok", foreground=style.SUCCESS, font=style.FONT_MONO_BOLD)
        widget.tag_configure("deco_ko", foreground=style.ERROR, font=style.FONT_MONO_BOLD)
        widget.configure(spacing1=1, spacing3=1)  # a thin gap between the favorite-tag labels
        widget.tag_raise("deco_badge")

    def _decorate_line(self, widget: tk.Text, line_no: int) -> None:
        """Show one line as columns: ✅ | logo | favorite tag | images | title (the link hidden).

        The widget must be editable. Images are inserted from right to left, so the positions
        computed on the plain text stay valid.
        """
        from src import tags as tag_utils  # pylint: disable=import-outside-toplevel
        from src.cli.downloader import parse_table_line  # pylint: disable=import-outside-toplevel
        start, end = f"{line_no}.0", f"{line_no}.end"
        for _kind, _name, index in reversed(widget.dump(start, end, image=True)):
            widget.delete(index)
        for tag in widget.tag_names():
            if tag in DECO_TAGS or tag.startswith("deco_badge_"):
                widget.tag_remove(tag, start, end)
        text = widget.get(start, end)
        columns = parse_table_line(text)
        if not columns:
            return
        bars = [i for i, char in enumerate(text) if char == "|"]
        widget.tag_add("deco_ok" if columns["status"] == "GOOD" else "deco_ko", f"{line_no}.0", f"{line_no}.1")
        # " | LINK" at the end: hidden (kept in the line for downloads, Retry and the log)
        last = bars[-1]
        widget.tag_add("deco_hidden", f"{line_no}.{last - 1 if text[last - 1] == ' ' else last}", end)
        if len(bars) < 4:
            return
        tag_from, tag_to = bars[0] + 1, bars[1]
        badge = columns["badge"]
        color = tag_utils.badge_color(badge) if badge else ""
        chars = max(tag_to - tag_from, tag_utils.badge_width() + 2)
        image = self._badge_image(badge if color else "", color or "#000000", chars) if (color or not badge) else None
        if image is not None:
            widget.tag_add("deco_hidden", f"{line_no}.{tag_from}", f"{line_no}.{tag_to}")
            widget.image_create(f"{line_no}.{tag_from}", image=image, align="center")
        elif badge:  # not a favorite any more (or no Pillow): plain coloured text
            widget.tag_add("deco_badge", f"{line_no}.{tag_from}", f"{line_no}.{tag_to}")
        # Site name -> logo (the name stays in the text, hidden, and is saved with the line)
        site = columns["site"]
        site_at = text.find(site, 0, bars[0]) if site else -1
        logo = self._site_logo(site) if site_at >= 0 else None
        if logo is not None:
            hide_to = max(site_at + len(site), bars[0] - 1)
            widget.tag_add("deco_hidden", f"{line_no}.{site_at}", f"{line_no}.{hide_to}")
            widget.image_create(f"{line_no}.{site_at}", image=logo, align="center", padx=1)

    def _decorate_all(self, widget: tk.Text) -> None:
        """Decorate every line of a list widget."""
        previous = str(widget.cget("state"))
        widget.configure(state="normal")
        try:
            last = int(widget.index("end-1c").split(".")[0])
            for line_no in range(1, last + 1):
                self._decorate_line(widget, line_no)
        finally:
            if str(widget.cget("undo")) in ("1", "true", "True"):
                widget.edit_reset()  # Ctrl+Z must not take the logos away
            widget.configure(state=previous)

    # ------------------------------------------------------------------ queue order

    def _current_line(self) -> int:
        """Line of the batch list with the cursor."""
        return int(self._batch_editor.index("insert").split(".")[0])

    def _line_count(self) -> int:
        return int(self._batch_editor.index("end-1c").split(".")[0])

    def _line_task(self, line_no: int) -> int | None:
        """Id of the batch task of a line (None if the line is not claimed by a batch)."""
        for tag in self._batch_editor.tag_names(f"{line_no}.0"):
            if tag.startswith("task_"):
                return int(tag[5:])
        return None

    def _line_state(self, line_no: int) -> str:
        """queued / active / done / '' for a line of the batch list."""
        task_id = self._line_task(line_no)
        if task_id is None:
            return ""
        return self._task_state.get(task_id, "")

    def _line_level_tags(self, line_no: int) -> list[str]:
        """Tags that cover a whole line (state colour, batch task)."""
        return [tag for tag in self._batch_editor.tag_names(f"{line_no}.0")
                if tag.endswith(LINE_TAGS_SUFFIX) or tag.startswith("task_")]

    def _queue_editable(self) -> bool:
        """The list can be changed now (not during Verify Links / Retry / a single download)."""
        return str(self._batch_editor.cget("state")) == "normal"

    def _move_batch_line(self, delta: int) -> str:
        """▲ / ▼: move the line with the cursor up or down (not a gallery downloading or done)."""
        if not self._queue_editable():
            return "break"
        line_no = self._current_line()
        target = line_no + delta
        if target < 1 or target > self._line_count():
            return "break"
        if not self._batch_editor.get(f"{line_no}.0", f"{line_no}.end").strip():
            return "break"
        state = self._line_state(line_no)
        if state in ("active", "done"):
            self._status_var.set("This gallery has already started: only lines still waiting can be moved.")
            self.bell()
            return "break"
        self._flush_pending_removals()
        line_no, target = self._current_line(), self._current_line() + delta
        if target < 1 or target > self._line_count():
            return "break"
        a_text = self._batch_editor.get(f"{line_no}.0", f"{line_no}.end")
        b_text = self._batch_editor.get(f"{target}.0", f"{target}.end")
        a_tags, b_tags = self._line_level_tags(line_no), self._line_level_tags(target)

        def action() -> None:
            for row, text, tags in ((line_no, b_text, b_tags), (target, a_text, a_tags)):
                self._batch_editor.delete(f"{row}.0", f"{row}.end")
                self._batch_editor.insert(f"{row}.0", text)
                for tag in tags:
                    self._batch_editor.tag_add(tag, f"{row}.0", f"{row}.end")
                self._decorate_line(self._batch_editor, row)
        self._editor_edit(action)
        self._batch_editor.mark_set("insert", f"{target}.0")
        self._batch_editor.see(f"{target}.0")
        self._save_urls_txt(quiet=True)
        self._sync_batch_order()
        return "break"

    def _remove_batch_line(self) -> str:
        """✖: remove the line with the cursor from the list (and from the running batch)."""
        if not self._queue_editable():
            return "break"
        line_no = self._current_line()
        text = self._batch_editor.get(f"{line_no}.0", f"{line_no}.end")
        if not text.strip():
            return "break"
        if self._line_state(line_no) == "active":
            messagebox.showinfo("Remove", "This gallery is downloading now: it can't be removed.\n"
                                          "Use Pause if you want to stop the downloads.")
            return "break"
        from src.cli.downloader import parse_url_line, gallery_name  # pylint: disable=import-outside-toplevel
        url, name = parse_url_line(text)
        task_id = self._line_task(line_no)
        handle = self._pending_removals.pop(task_id, None) if task_id else None
        if handle:
            try:
                self.after_cancel(handle)
            except tk.TclError:
                pass
        self._editor_edit(lambda: self._batch_editor.delete(f"{line_no}.0", f"{line_no + 1}.0"))
        self._save_urls_txt(quiet=True)
        self._sync_batch_order()
        if url:
            self._status_var.set(f"Removed from the list: {name or gallery_name(url) or url}")
        return "break"

    def _sync_batch_order(self) -> None:
        """GUI thread: the queue of each site follows the order of the list; removed lines are cancelled."""
        if not getattr(self, "_batch_queues", None):
            return
        cancelled = []
        with self._batch_lock:
            for lane_queue in self._batch_queues.values():
                keep = []
                for task in lane_queue:
                    line_no = self._task_line(task["id"])
                    if line_no is None:
                        cancelled.append(task)
                    else:
                        keep.append((line_no, task))
                keep.sort(key=lambda item: item[0])
                lane_queue.clear()
                lane_queue.extend(task for _, task in keep)
            for task in cancelled:
                self._task_state[task["id"]] = "cancelled"
                self._batch_total = max(0, self._batch_total - 1)
                if not task["dup"]:  # the same gallery may be added again later
                    self._batch_keys.pop(task.get("key"), None)
        for task in cancelled:
            self._batch_editor.tag_delete(f"task_{task['id']}")
            self._q.put(("LOG", _ts(), "Removed",
                         f"{task['name'] or task['url']}: removed from the queue, not downloaded."))

    def _build_queue_buttons(self, parent) -> None:
        """▲ ▼ ✖ column at the right of the batch list."""
        column = tk.Frame(parent, bg=style.PANEL)
        column.grid(row=0, column=2, sticky="ns", padx=(4, 0))
        self._queue_buttons = []
        for text, tip, command in (
            ("▲", "Move up (Alt+Up)", lambda: self._move_batch_line(-1)),
            ("▼", "Move down (Alt+Down)", lambda: self._move_batch_line(1)),
            ("✖", "Remove from the list (Ctrl+Delete)", self._remove_batch_line),
        ):
            button = tk.Button(column, text=text, command=command, bg=style.PANEL, fg=style.ACCENT,
                               activebackground=style.TAB_HOVER, activeforeground=style.ACCENT,
                               font=style.FONT_UI, relief="flat", bd=0, cursor="hand2", width=2)
            button.pack(side="top", pady=(0, 2))
            _Tooltip(button, tip)
            self._queue_buttons.append(button)
        self._batch_editor.bind("<Alt-Up>", lambda _e: self._move_batch_line(-1))
        self._batch_editor.bind("<Alt-Down>", lambda _e: self._move_batch_line(1))
        self._batch_editor.bind("<Control-Delete>", lambda _e: self._remove_batch_line())

    def _redecorate_batch(self) -> None:
        """Favorites changed: rewrite every list line (tag, column width), then decorate."""
        from src.cli.downloader import normalize_line  # pylint: disable=import-outside-toplevel
        self.__dict__.pop("_badge_cache", None)
        lines = self._batch_editor.get("1.0", "end-1c").splitlines()
        for line_no, line in enumerate(lines, start=1):
            new_text = normalize_line(line)
            if new_text != line:
                self._replace_editor_line(line_no, new_text)
        self._decorate_all(self._batch_editor)
        self._save_urls_txt(quiet=True)
        self._load_retry_list()


class _Tooltip:  # pylint: disable=too-few-public-methods
    """Small hint shown when the mouse rests on a widget."""

    def __init__(self, widget, text: str) -> None:
        self.widget, self.text, self.tip = widget, text, None
        widget.bind("<Enter>", self._show, add="+")
        widget.bind("<Leave>", self._hide, add="+")

    def _show(self, _event=None) -> None:
        if self.tip is not None:
            return
        x = self.widget.winfo_rootx() - 10
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 2
        self.tip = tk.Toplevel(self.widget)
        self.tip.wm_overrideredirect(True)
        self.tip.wm_geometry(f"+{x}+{y}")
        tk.Label(self.tip, text=self.text, bg=style.PANEL, fg=style.TEXT, font=style.FONT_SMALL,
                 relief="solid", bd=1, padx=6, pady=2).pack()

    def _hide(self, _event=None) -> None:
        if self.tip is not None:
            self.tip.destroy()
            self.tip = None
