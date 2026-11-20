"""The Statistics tab: totals since the first start and for this session, speeds, top tags."""

# pylint: disable=protected-access
from __future__ import annotations

from tkinter import StringVar, messagebox, ttk
import tkinter as tk

from src import stats
from src.gui import style
from src.gui.style import FONT_HEAD, FONT_MONO, FONT_SMALL, FONT_UI, PAD, card as _card

STAT_ROWS = (
    ("count", "Galleries · images"),
    ("size", "Downloaded"),
    ("speed", "Speed average · peak"),
)
TOP_TAGS_SHOWN = 25


class StatsTabMixin:  # pylint: disable=too-few-public-methods,no-member
    """Statistics tab and the speed shown in the status bar. Mixed into EHentaiGUI."""

    def _build_stats_tab(self) -> None:
        """A table (since the first start / this session) and the most downloaded tags."""
        tab = self._tab_stats
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(0, weight=1)
        outer = _card(tab)
        outer.grid(row=0, column=0, padx=PAD, pady=PAD, sticky="nsew")
        outer.columnconfigure(0, weight=3)
        outer.columnconfigure(1, weight=2)
        outer.rowconfigure(1, weight=1)

        self._stats_now_var = StringVar(value="")
        head = tk.Frame(outer, bg=style.PANEL)
        head.grid(row=0, column=0, columnspan=2, sticky="ew", padx=PAD, pady=(PAD, 4))
        ttk.Label(head, text="Statistics", style="Head.TLabel").pack(side="left")
        tk.Label(head, textvariable=self._stats_now_var, bg=style.PANEL, fg=style.ACCENT,
                 font=FONT_UI).pack(side="left", padx=(16, 0))
        ttk.Button(head, text="🗑  Reset", style="Ghost.TButton", command=self._reset_stats,
                   width=0).pack(side="right")

        # Table: one row per number, a column for each period
        table = tk.Frame(outer, bg=style.PANEL)
        table.grid(row=1, column=0, sticky="nsew", padx=(PAD, 6), pady=(0, PAD))
        table.columnconfigure((1, 2), weight=1, uniform="period")
        self._stats_vars: dict[str, StringVar] = {}
        for col, block in enumerate(("total", "session"), start=1):
            var = StringVar()
            self._stats_vars[f"{block}.title"] = var
            tk.Label(table, textvariable=var, bg=style.PANEL, fg=style.TEXT, font=FONT_HEAD,
                     anchor="w").grid(row=0, column=col, sticky="w", padx=(0, 8))
        rows = list(STAT_ROWS) + [(f"site.{site}", stats.SITE_LABELS[site]) for site in stats.SITES]
        for row, (key, label) in enumerate(rows, start=1):
            is_site = key.startswith("site.")
            tk.Label(table, text=label, bg=style.PANEL, fg=style.TEXT if is_site else style.TEXT_SEC,
                     font=FONT_SMALL, anchor="w").grid(row=row, column=0, sticky="w", padx=(0, 12),
                                                       pady=(6 if key == "site.e-hentai" else 0, 0))
            for col, block in enumerate(("total", "session"), start=1):
                var = StringVar(value="—")
                self._stats_vars[f"{block}.{key}"] = var
                tk.Label(table, textvariable=var, bg=style.PANEL, fg=style.TEXT,
                         font=FONT_SMALL if is_site else FONT_UI, anchor="w").grid(
                             row=row, column=col, sticky="w", padx=(0, 8),
                             pady=(6 if key == "site.e-hentai" else 0, 0))
        tk.Label(table, text="Sites: galleries · images · size", bg=style.PANEL, fg=style.MUTED,
                 font=FONT_SMALL, anchor="w").grid(row=len(rows) + 1, column=0, columnspan=3, sticky="w",
                                                    pady=(4, 0))

        # Most downloaded tags (since the first start), scrollable
        tags_box = tk.Frame(outer, bg=style.PANEL)
        tags_box.grid(row=1, column=1, sticky="nsew", padx=(6, PAD), pady=(0, PAD))
        tags_box.rowconfigure(1, weight=1)
        tags_box.columnconfigure(0, weight=1)
        tk.Label(tags_box, text="Most downloaded tags", bg=style.PANEL, fg=style.TEXT, font=FONT_HEAD,
                 anchor="w").grid(row=0, column=0, columnspan=2, sticky="w")
        frame = tk.Frame(tags_box, bg=style.BORDER, bd=1)
        frame.grid(row=1, column=0, sticky="nsew", pady=(4, 0))
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        self._stats_tags = tk.Text(frame, font=FONT_MONO, bg=style.PANEL, fg=style.TEXT, relief="flat", bd=0,
                                   height=6, width=30, wrap="none", state="disabled", padx=6, pady=4)
        self._stats_tags.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(frame, orient="vertical", command=self._stats_tags.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self._stats_tags.configure(yscrollcommand=scroll.set)
        self._stats_tags.tag_configure("bar", foreground=style.ACCENT)
        self._stats_tags.tag_configure("count", foreground=style.TEXT_SEC)
        self._stats_tags_text = None
        self._refresh_stats()

    def _refresh_stats(self) -> None:
        """Fill the tab with the current numbers."""
        data = stats.snapshot(top=TOP_TAGS_SHOWN)
        downloading = data["downloading"]
        now = f"⬇ Now: {stats.fmt_speed(data['current_speed'])}"
        if downloading:
            now += f" · {downloading} galler{'ies' if downloading != 1 else 'y'} downloading"
        self._stats_now_var.set(now)
        titles = {"total": f"Since {data['first_start'][:10]}",
                  "session": f"This session ({data['session_started'][-5:]})"}
        for block in ("total", "session"):
            values = data[block]

            def put(key: str, text: str, block: str = block) -> None:
                self._stats_vars[f"{block}.{key}"].set(text)
            put("title", titles[block])
            put("count", f"{values['galleries']:,} · {values['images']:,}")
            put("size", f"{stats.fmt_bytes(values['bytes'])} in {stats.fmt_duration(values['busy_seconds'])}")
            put("speed", f"{stats.fmt_speed(values['average_speed'])} · {stats.fmt_speed(values['peak_speed'])}")
            for site, item in values["sites"].items():
                put(f"site.{site}", f"{item['galleries']:,} · {item['images']:,} · {stats.fmt_bytes(item['bytes'])}")
        top = data["total"]["top_tags"]
        if top != self._stats_tags_text:
            self._stats_tags_text = top
            self._show_top_tags(top)

    def _show_top_tags(self, top: list[dict]) -> None:
        """Write the tag ranking (name, count, small bar)."""
        box = self._stats_tags
        box.configure(state="normal")
        box.delete("1.0", "end")
        if not top:
            box.insert("end", "No completed gallery yet.\nThe tags of every downloaded\ngallery are counted here.")
        else:
            width = min(24, max(len(item["tag"]) for item in top))
            most = max(item["count"] for item in top) or 1
            for item in top:
                box.insert("end", f"{item['tag'][:width]:<{width}} ")
                box.insert("end", f"{item['count']:>5} ", "count")
                box.insert("end", "█" * max(1, round(item["count"] / most * 10)) + "\n", "bar")
        box.configure(state="disabled")

    def _stats_tick(self) -> None:
        """Once a second: speed in the status bar, and the tab when it is open."""
        if self._closed:
            return
        try:
            speed = stats.current_speed()
            self._speed_now_var.set(f"⬇ {stats.fmt_speed(speed)}" if speed > 0 else "")
            if self._nb.select() == str(self._tab_stats):
                self._refresh_stats()
            stats.save()  # only writes when something changed
        except Exception:  # pylint: disable=broad-except
            pass
        self.after(1000, self._stats_tick)

    def _reset_stats(self) -> None:
        """'Reset' button of the Statistics tab."""
        if not messagebox.askyesno("Reset statistics",
                                   "Delete every statistic (since the first start and this session)?\n"
                                   "This cannot be undone.", icon="warning", default="no"):
            return
        stats.reset()
        self._refresh_stats()
