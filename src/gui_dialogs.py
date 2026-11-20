"""Secondary windows of the GUI: start-up notice, account cookies, license, about.

The windows take the current colour palette (dict from src.ui_theme) and use the ttk
styles configured by the main window, so they follow the light / dark theme.
"""

from __future__ import annotations

import os
import platform
import sys
import time
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from src import cookie_import, image_formats, paths, tags as tag_utils
from src.image_formats import OPEN_FORMATS_NOTE
from src.config import (APP_VERSION, DISCLAIMER_SNOOZE_DAYS, FORMAT_NOTICE_SNOOZE_DAYS, ISSUES_URL, PROJECT_URL,
                        UPSTREAM_URL)
from src.texts import APP_NAME, DISCLAIMER_TEXT, DISCLAIMER_TITLE, THIRD_PARTY_TEXT  # noqa: F401


def open_url(url: str) -> None:
    """Open a link in the default browser (any browser: also Flatpak / Snap ones)."""
    try:
        webbrowser.open(url, new=2)
    except Exception as err:  # pylint: disable=broad-except
        messagebox.showerror("Open link", f"Could not open the browser:\n{err}\n\nLink: {url}")


def open_store_page(product_id: str) -> None:
    """Microsoft Store product: on Windows only the small install window of the Store app opens,
    elsewhere (or if the Store app is missing) its web page opens in the browser."""
    if sys.platform == "win32":
        try:
            os.startfile(image_formats.store_app_url(product_id))  # pylint: disable=no-member
            return
        except OSError:
            pass
    open_url(image_formats.store_page_url(product_id))


def open_link(url: str) -> None:
    """Open a link of the log or of a window (Microsoft Store pages go to the Store install window)."""
    product_id = image_formats.product_id_from_url(url)
    if product_id:
        open_store_page(product_id)
    else:
        open_url(url)


def _dialog(root, title: str, palette: dict, width: int, height: int) -> tk.Toplevel:
    """Centred modal window with the theme background."""
    win = tk.Toplevel(root)
    win.title(title)
    win.configure(bg=palette["BG"])
    win.transient(root)
    win.minsize(min(width, 480), min(height, 300))
    root.update_idletasks()
    x = root.winfo_rootx() + max(0, (root.winfo_width() - width) // 2)
    y = root.winfo_rooty() + max(0, (root.winfo_height() - height) // 2)
    win.geometry(f"{width}x{height}+{x}+{y}")
    return win


def _text_box(parent, palette: dict, fonts: dict, text: str, height: int = 12) -> tk.Text:
    """Read-only wrapped text with a scrollbar."""
    frame = tk.Frame(parent, bg=palette["BORDER"], bd=1)
    frame.pack(fill="both", expand=True)
    box = tk.Text(
        frame, wrap="word", height=height, bg=palette["PANEL"], fg=palette["TEXT"],
        font=fonts["ui"], relief="flat", bd=0, padx=12, pady=10,
        selectbackground=palette["SELECT_BG"], insertbackground=palette["TEXT"],
    )
    scroll = ttk.Scrollbar(frame, orient="vertical", command=box.yview)
    box.configure(yscrollcommand=scroll.set)
    scroll.pack(side="right", fill="y")
    box.pack(side="left", fill="both", expand=True)
    box.insert("1.0", text)
    box.configure(state="disabled")
    return box


# ---------------------------------------------------------------- start-up notice

def disclaimer_needed(settings: dict) -> bool:
    """True when the notice must be shown at start-up (not snoozed, or snooze expired)."""
    try:
        return float(settings.get("disclaimer_hidden_until", 0)) < time.time()
    except (TypeError, ValueError):
        return True


def show_disclaimer(root, palette: dict, fonts: dict, settings: dict, save_settings, startup: bool) -> bool:
    """Show the notice. At start-up the user must accept (True) or quit (False)."""
    win = _dialog(root, f"{APP_NAME} – {DISCLAIMER_TITLE}", palette, 680, 560)
    result = {"accepted": not startup}
    body = tk.Frame(win, bg=palette["BG"])
    body.pack(fill="both", expand=True, padx=16, pady=(14, 8))
    tk.Label(body, text=f"⚠  {DISCLAIMER_TITLE}", bg=palette["BG"], fg=palette["TEXT"],
             font=fonts["head"]).pack(anchor="w", pady=(0, 8))
    _text_box(body, palette, fonts, DISCLAIMER_TEXT, height=16)

    links = tk.Frame(win, bg=palette["BG"])
    links.pack(fill="x", padx=16)
    for label, url in (("GitHub page", PROJECT_URL), ("Report a problem", ISSUES_URL)):
        tk.Button(links, text=label, bg=palette["BG"], fg=palette["ACCENT"], font=fonts["small"],
                  relief="flat", cursor="hand2", bd=0, activebackground=palette["BG"],
                  activeforeground=palette["ACCENT2"], command=lambda u=url: open_url(u)).pack(side="left", padx=(0, 12))

    bottom = tk.Frame(win, bg=palette["BG"])
    bottom.pack(fill="x", padx=16, pady=(8, 14))
    snooze = tk.BooleanVar(value=False)
    if startup:
        tk.Checkbutton(
            bottom, text=f"Don't show this again for {DISCLAIMER_SNOOZE_DAYS} days", variable=snooze,
            bg=palette["BG"], fg=palette["TEXT_SEC"], font=fonts["small"], activebackground=palette["BG"],
            activeforeground=palette["TEXT"], selectcolor=palette["PANEL"], highlightthickness=0,
        ).pack(side="left")

    def accept() -> None:
        result["accepted"] = True
        if startup:
            hidden_until = time.time() + DISCLAIMER_SNOOZE_DAYS * 86400 if snooze.get() else 0
            settings["disclaimer_hidden_until"] = hidden_until
            settings["disclaimer_accepted"] = time.strftime("%Y-%m-%d %H:%M")
            save_settings()
        win.destroy()

    def decline() -> None:
        result["accepted"] = False
        win.destroy()

    if startup:
        ttk.Button(bottom, text="I understand and accept", style="Accent.TButton", command=accept).pack(side="right")
        ttk.Button(bottom, text="Decline and exit", style="Ghost.TButton", command=decline).pack(side="right", padx=(0, 8))
        win.protocol("WM_DELETE_WINDOW", decline)
    else:
        ttk.Button(bottom, text="Close", style="Accent.TButton", command=accept).pack(side="right")
        win.protocol("WM_DELETE_WINDOW", accept)
    win.grab_set()
    win.focus_set()
    root.wait_window(win)
    return result["accepted"]


# ---------------------------------------------------------------- image format notice

def show_format_notice(root, palette: dict, fonts: dict, title: str, text: str, extension) -> bool:
    """Notice of an image format, with the Windows extension to install (if any).
    Returns True when "Don't show this again" was ticked."""
    win = _dialog(root, title, palette, 620, 430 if extension else 300)
    body = tk.Frame(win, bg=palette["BG"])
    body.pack(fill="both", expand=True, padx=16, pady=(14, 8))
    tk.Label(body, text=f"ℹ  {title}", bg=palette["BG"], fg=palette["TEXT"],
             font=fonts["head"]).pack(anchor="w", pady=(0, 8))
    _text_box(body, palette, fonts, text, height=12 if extension else 5)

    if extension:
        name, page_url, _ = extension
        product_id = image_formats.product_id_from_url(page_url)
        row = tk.Frame(win, bg=palette["BG"])
        row.pack(fill="x", padx=16)
        tk.Button(row, text=f"⬇  Install {name} (Microsoft Store, free)", bg=palette["BG"], fg=palette["ACCENT"],
                  font=fonts["small"], relief="flat", cursor="hand2", bd=0, activebackground=palette["BG"],
                  activeforeground=palette["ACCENT2"],
                  command=lambda: open_store_page(product_id)).pack(side="left")

    bottom = tk.Frame(win, bg=palette["BG"])
    bottom.pack(fill="x", padx=16, pady=(8, 14))
    snooze = tk.BooleanVar(value=False)
    tk.Checkbutton(
        bottom, text=f"Don't show this again for {FORMAT_NOTICE_SNOOZE_DAYS} days", variable=snooze,
        bg=palette["BG"], fg=palette["TEXT_SEC"], font=fonts["small"], activebackground=palette["BG"],
        activeforeground=palette["TEXT"], selectcolor=palette["PANEL"], highlightthickness=0,
    ).pack(side="left")
    ttk.Button(bottom, text="OK", style="Accent.TButton", command=win.destroy).pack(side="right")
    win.protocol("WM_DELETE_WINDOW", win.destroy)
    win.bind("<Return>", lambda _e: win.destroy())
    win.grab_set()
    win.focus_set()
    root.wait_window(win)
    return bool(snooze.get())


# ---------------------------------------------------------------- license / about

def show_license(root, palette: dict, fonts: dict) -> None:
    """GPL-3.0 text of the LICENSE file plus the third-party list."""
    win = _dialog(root, f"{APP_NAME} – License", palette, 720, 600)
    body = tk.Frame(win, bg=palette["BG"])
    body.pack(fill="both", expand=True, padx=16, pady=14)
    tk.Label(body, text="License: GNU General Public License v3.0", bg=palette["BG"], fg=palette["TEXT"],
             font=fonts["head"]).pack(anchor="w", pady=(0, 4))
    tk.Label(body, text=f"Fork of {UPSTREAM_URL}, distributed under the same license.",
             bg=palette["BG"], fg=palette["TEXT_SEC"], font=fonts["small"]).pack(anchor="w", pady=(0, 8))
    license_path = paths.resource("LICENSE")
    try:
        license_text = license_path.read_text(encoding="utf-8")
    except OSError:
        license_text = "LICENSE file not found: see https://www.gnu.org/licenses/gpl-3.0.html"
    _text_box(body, palette, fonts, THIRD_PARTY_TEXT + "\n" + "─" * 60 + "\n\n" + license_text, height=20)
    ttk.Button(body, text="Close", style="Accent.TButton", command=win.destroy).pack(anchor="e", pady=(10, 0))


def show_about(root, palette: dict, fonts: dict) -> None:
    """Name, credits, AI notice, links and versions."""
    win = _dialog(root, f"About {APP_NAME}", palette, 600, 490)
    body = tk.Frame(win, bg=palette["BG"])
    body.pack(fill="both", expand=True, padx=20, pady=16)
    top = tk.Frame(body, bg=palette["BG"])
    top.pack(fill="x")
    try:
        win.logo = tk.PhotoImage(file=str(paths.resource("assets/logo_small.png")))
        tk.Label(top, image=win.logo, bg=palette["BG"]).pack(side="left", padx=(0, 14))
    except (tk.TclError, OSError):
        pass
    tk.Label(top, text=f"{APP_NAME}\nversion {APP_VERSION}", bg=palette["BG"], fg=palette["TEXT"],
             font=fonts["title"], justify="left").pack(side="left", anchor="w")
    lines = (
        "Downloads E-Hentai, ExHentai and nhentai galleries as image folders or CBZ archives.",
        "",
        f"This version: {PROJECT_URL}",
        f"Original project: {UPSTREAM_URL}",
        "Developed and modified with the help of Claude AI (Anthropic).",
        "Free software under the GPL-3.0 license, provided as is, without warranty.",
        OPEN_FORMATS_NOTE,
        "",
        f"Data folder: {paths.app_dir()}",
        f"Python {platform.python_version()} · Tk {root.tk.call('info', 'patchlevel')} · {platform.system()} {platform.release()}",
    )
    tk.Label(body, text="\n".join(lines), bg=palette["BG"], fg=palette["TEXT_SEC"], font=fonts["small"],
             justify="left", anchor="w", wraplength=550).pack(anchor="w", pady=(8, 12), fill="x")
    row = tk.Frame(body, bg=palette["BG"])
    row.pack(fill="x", side="bottom")
    ttk.Button(row, text="GitHub page", style="Ghost.TButton", command=lambda: open_url(PROJECT_URL)).pack(side="left")
    ttk.Button(row, text="Close", style="Accent.TButton", command=win.destroy).pack(side="right")


# ---------------------------------------------------------------- account cookies

LOGIN_PAGES = (
    ("E-Hentai login", "https://forums.e-hentai.org/index.php?act=Login"),
    ("ExHentai", "https://exhentai.org/"),
    ("nhentai", "https://nhentai.net/"),
)

ACCOUNT_HELP = (
    "1. Open the site with the buttons below and log in, in your usual browser.\n"
    "2. Open the developer tools (F12, or Cmd+Option+I on macOS) → Application / Storage → Cookies → "
    "the site.\n"
    "3. Select the cookie rows, copy them (Ctrl+C / Cmd+C) and paste them in the box, or import a "
    "cookies.txt exported by a browser extension. Then press 'Read cookies'.\n"
    "Only the cookies the program needs are kept (ipb_member_id, ipb_pass_hash, igneous); "
    "everything else is ignored. They are saved in cookies.json, on this computer only."
)


class AccountDialog:  # pylint: disable=too-few-public-methods
    """Window that fills cookies.json from cookies pasted by the user."""

    def __init__(self, root, palette: dict, fonts: dict, on_saved) -> None:
        """Build the window with the current values of cookies.json."""
        self.palette, self.fonts, self.on_saved = palette, fonts, on_saved
        self.win = _dialog(root, f"{APP_NAME} – Account & cookies", palette, 760, 640)
        data = cookie_import.load_cookies_file()
        nh_section = data.get("nhentai") if isinstance(data.get("nhentai"), dict) else {}
        self.vars = {name: tk.StringVar(value=str(data.get(name, ""))) for name in cookie_import.EHENTAI_COOKIES}
        self.api_key = tk.StringVar(value=str(nh_section.get("api_key", "")))
        self.show_values = tk.BooleanVar(value=False)
        self.entries: list[ttk.Entry] = []
        self._build()
        self.win.grab_set()

    def _label(self, parent, text: str, style: str = "small", color: str = "TEXT_SEC", **pack) -> None:
        tk.Label(parent, text=text, bg=self.palette["PANEL"], fg=self.palette[color], font=self.fonts[style],
                 justify="left", anchor="w", wraplength=700).pack(anchor="w", fill="x", **pack)

    def _build(self) -> None:
        palette = self.palette
        card = ttk.Frame(self.win, style="Card.TFrame")
        card.pack(fill="both", expand=True, padx=12, pady=12)
        inner = tk.Frame(card, bg=palette["PANEL"])
        inner.pack(fill="both", expand=True, padx=14, pady=12)

        self._label(inner, "E-Hentai / ExHentai account", style="head", color="TEXT", pady=(0, 4))
        self._label(inner, ACCOUNT_HELP, pady=(0, 8))

        links = tk.Frame(inner, bg=palette["PANEL"])
        links.pack(fill="x", pady=(0, 8))
        for label, url in LOGIN_PAGES:
            ttk.Button(links, text=f"🌐  {label}", style="Ghost.TButton",
                       command=lambda u=url: open_url(u)).pack(side="left", padx=(0, 6))

        frame = tk.Frame(inner, bg=palette["BORDER"], bd=1)
        frame.pack(fill="both", expand=True)
        self.paste = tk.Text(frame, height=6, wrap="none", bg=palette["PANEL"], fg=palette["TEXT"],
                             font=self.fonts["mono"], relief="flat", bd=0, padx=6, pady=4,
                             insertbackground=palette["TEXT"], selectbackground=palette["SELECT_BG"])
        self.paste.pack(fill="both", expand=True)

        actions = tk.Frame(inner, bg=palette["PANEL"])
        actions.pack(fill="x", pady=(6, 10))
        ttk.Button(actions, text="📋  Paste", style="Ghost.TButton", command=self._paste_clipboard).pack(side="left")
        ttk.Button(actions, text="📂  Import cookies.txt…", style="Ghost.TButton",
                   command=self._import_file).pack(side="left", padx=6)
        ttk.Button(actions, text="🔍  Read cookies", style="Accent.TButton", command=self._read).pack(side="left")

        grid = tk.Frame(inner, bg=palette["PANEL"])
        grid.pack(fill="x")
        grid.columnconfigure(1, weight=1)
        rows = [(name, self.vars[name]) for name in cookie_import.EHENTAI_COOKIES]
        rows.append(("nhentai API key (optional)", self.api_key))
        for row, (name, var) in enumerate(rows):
            tk.Label(grid, text=name, bg=palette["PANEL"], fg=palette["TEXT_SEC"], font=self.fonts["small"],
                     anchor="w").grid(row=row, column=0, sticky="w", padx=(0, 8), pady=2)
            entry = ttk.Entry(grid, textvariable=var, show="•", font=self.fonts["mono"])
            entry.grid(row=row, column=1, sticky="ew", pady=2)
            self.entries.append(entry)
        tk.Checkbutton(grid, text="Show values", variable=self.show_values, command=self._toggle_show,
                       bg=palette["PANEL"], fg=palette["TEXT_SEC"], font=self.fonts["small"],
                       activebackground=palette["PANEL"], selectcolor=palette["PANEL"],
                       highlightthickness=0).grid(row=len(rows), column=1, sticky="w")
        self._label(inner, "The nhentai API key is not needed: without it the limit is 20 API requests per "
                    "minute, with it 45. 'igneous' is only needed for ExHentai.", pady=(6, 0))

        bottom = tk.Frame(self.win, bg=palette["BG"])
        bottom.pack(fill="x", padx=12, pady=(0, 12))
        self.status = tk.Label(bottom, text="", bg=palette["BG"], fg=palette["TEXT_SEC"], font=self.fonts["small"])
        self.status.pack(side="left")
        ttk.Button(bottom, text="💾  Save", style="Accent.TButton", command=self._save).pack(side="right")
        ttk.Button(bottom, text="Cancel", style="Ghost.TButton", command=self.win.destroy).pack(side="right", padx=6)

    def _toggle_show(self) -> None:
        for entry in self.entries:
            entry.configure(show="" if self.show_values.get() else "•")

    def _paste_clipboard(self) -> None:
        try:
            text = self.win.clipboard_get()
        except tk.TclError:
            text = ""
        if text:
            self.paste.delete("1.0", "end")
            self.paste.insert("1.0", text)
            self._read()

    def _import_file(self) -> None:
        path = filedialog.askopenfilename(
            parent=self.win, title="Import cookies",
            filetypes=[("Cookie files", "*.txt *.json"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            text = Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError as err:
            messagebox.showerror("Import cookies", f"Could not read the file:\n{err}", parent=self.win)
            return
        self.paste.delete("1.0", "end")
        self.paste.insert("1.0", text)
        self._read()

    def _read(self) -> None:
        cookies = cookie_import.parse_cookies(self.paste.get("1.0", "end"))
        found = cookie_import.extract_ehentai(cookies)
        for name, value in found.items():
            self.vars[name].set(value)
        # Pasted text may hold other private cookies: don't keep it on screen
        self.paste.delete("1.0", "end")
        if found:
            self.status.configure(text=f"✔ Found: {', '.join(sorted(found))}. Press Save.",
                                  fg=self.palette["SUCCESS"])
        else:
            self.status.configure(
                text="No E-Hentai / ExHentai login cookie found: are you logged in on that site?",
                fg=self.palette["WARNING"],
            )

    def _save(self) -> None:
        values = {name: var.get() for name, var in self.vars.items()}
        if values["ipb_member_id"].strip() and not values["ipb_member_id"].strip().isdigit():
            messagebox.showwarning("Account & cookies", "ipb_member_id should be a number.", parent=self.win)
            return
        try:
            cookie_import.save_cookies_file(values, self.api_key.get())
        except OSError as err:
            messagebox.showerror("Account & cookies", f"Could not write cookies.json:\n{err}", parent=self.win)
            return
        self.win.destroy()
        self.on_saved()


FAVORITE_TAGS_HELP = (
    "Click ☐ / ★ (or double-click, Space) to make a tag a favorite. The list fills up by itself with "
    "the tags of every gallery you verify or download (and how many galleries had them); a few common "
    "tags are offered from the start (\"suggested\"), and you can add any tag, e.g. artist:name.\n"
    "Favorites show as a coloured label in the lists; with more than one, the highest wins (▲ ▼). "
    f"Select a favorite to change its label (capitals, max {tag_utils.BADGE_MAX} characters, also the "
    "folder name) and its colour. Drag the column borders to resize them."
)


class FavoriteTagsDialog:  # pylint: disable=too-few-public-methods,too-many-instance-attributes
    """Settings → Favorite tags…: the tags met in the galleries, each with a favorite selector."""

    COLUMNS = (("fav", "★", 50, "center"), ("tag", "Tag", 240, "w"), ("label", "Label", 200, "w"),
               ("seen", "Galleries", 80, "e"))

    def __init__(self, root, palette: dict, fonts: dict, favorites: list[dict], folders: bool,  # pylint: disable=too-many-arguments,too-many-positional-arguments
                 on_saved) -> None:
        """on_saved(favorites, folders) is called with the new values."""
        self.palette, self.fonts, self.on_saved = palette, fonts, on_saved
        tag_utils.set_favorites(favorites)
        # tag -> {"tag", "label", "favorite", "seen", "suggested"}; favorites keep their order
        self.rows = {row["tag"]: row for row in tag_utils.catalog()}
        self.order = [row["tag"] for row in self.rows.values() if row["favorite"]]
        self.folders = tk.BooleanVar(value=folders)
        self.filter = tk.StringVar()
        self.new_tag = tk.StringVar()
        self.label = tk.StringVar()
        self._label_tag: str | None = None  # favorite whose label is in the entry
        self.win = _dialog(root, f"{APP_NAME} – Favorite tags", palette, 760, 660)
        self._build()
        self._refresh()
        self.filter.trace_add("write", lambda *_: self._refresh())
        self.win.grab_set()

    def _build(self) -> None:
        palette, fonts = self.palette, self.fonts
        card = ttk.Frame(self.win, style="Card.TFrame")
        card.pack(fill="both", expand=True, padx=12, pady=12)
        inner = tk.Frame(card, bg=palette["PANEL"])
        inner.pack(fill="both", expand=True, padx=14, pady=12)
        tk.Label(inner, text="Favorite tags", bg=palette["PANEL"], fg=palette["TEXT"], font=fonts["head"],
                 anchor="w").pack(fill="x", pady=(0, 4))
        tk.Label(inner, text=FAVORITE_TAGS_HELP, bg=palette["PANEL"], fg=palette["TEXT_SEC"], font=fonts["small"],
                 justify="left", anchor="w", wraplength=640).pack(fill="x", pady=(0, 8))

        tools = tk.Frame(inner, bg=palette["PANEL"])
        tools.pack(fill="x", pady=(0, 6))
        tk.Label(tools, text="🔍", bg=palette["PANEL"], fg=palette["TEXT_SEC"], font=fonts["small"]).pack(side="left")
        ttk.Entry(tools, textvariable=self.filter, width=22, font=fonts["ui"]).pack(side="left", padx=(4, 16))
        tk.Label(tools, text="Add a tag:", bg=palette["PANEL"], fg=palette["TEXT_SEC"],
                 font=fonts["small"]).pack(side="left")
        add_entry = ttk.Entry(tools, textvariable=self.new_tag, width=20, font=fonts["ui"])
        add_entry.pack(side="left", padx=4)
        add_entry.bind("<Return>", lambda _e: self._add_tag())
        ttk.Button(tools, text="＋ Add", style="Ghost.TButton", command=self._add_tag, width=0).pack(side="left")

        frame = tk.Frame(inner, bg=palette["BORDER"], bd=1)
        frame.pack(fill="both", expand=True)
        style = ttk.Style(self.win)
        style.configure("Fav.Treeview", background=palette["PANEL"], fieldbackground=palette["PANEL"],
                        foreground=palette["TEXT"], font=fonts["ui"], rowheight=22, borderwidth=0)
        style.configure("Fav.Treeview.Heading", background=palette["BORDER"], foreground=palette["TEXT"],
                        font=fonts["small"], relief="flat")
        style.map("Fav.Treeview", background=[("selected", palette["SELECT_BG"])],
                  foreground=[("selected", palette["TEXT"])])
        self.tree = ttk.Treeview(frame, columns=[c[0] for c in self.COLUMNS], show="headings",
                                 style="Fav.Treeview", selectmode="browse", height=9)
        for key, title, width, anchor in self.COLUMNS:
            self.tree.heading(key, text=title, anchor=anchor)
            # every column can be resized by dragging the header borders; tag and label share the space
            self.tree.column(key, width=width, minwidth=40, anchor=anchor, stretch=key in ("tag", "label"))
        self.tree.tag_configure("favorite", font=fonts["head"])
        self.tree.tag_configure("suggested", foreground=palette["MUTED"])
        scroll = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.bind("<Button-1>", self._on_click)
        self.tree.bind("<Double-1>", lambda _e: self._toggle(self._selected()))
        self.tree.bind("<space>", lambda _e: self._toggle(self._selected()))
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._on_select())

        self.count_lbl = tk.Label(inner, text="", bg=palette["PANEL"], fg=palette["TEXT_SEC"], font=fonts["small"],
                                  anchor="w")
        self.count_lbl.pack(fill="x", pady=(4, 0))

        # Selected favorite: label and priority
        edit = tk.Frame(inner, bg=palette["PANEL"])
        edit.pack(fill="x", pady=(6, 0))
        tk.Label(edit, text="Label of the selected favorite:", bg=palette["PANEL"], fg=palette["TEXT_SEC"],
                 font=fonts["small"]).pack(side="left")
        self.label_entry = ttk.Entry(edit, textvariable=self.label, width=18, font=fonts["ui"], state="disabled")
        self.label_entry.pack(side="left", padx=6)
        self.label_entry.bind("<Return>", lambda _e: self._apply_label())
        self.label_entry.bind("<FocusOut>", lambda _e: self._apply_label())
        self.up_btn = ttk.Button(edit, text="▲", style="Ghost.TButton", width=3, state="disabled",
                                 command=lambda: self._move(-1))
        self.up_btn.pack(side="left", padx=(10, 2))
        self.down_btn = ttk.Button(edit, text="▼", style="Ghost.TButton", width=3, state="disabled",
                                   command=lambda: self._move(1))
        self.down_btn.pack(side="left")
        self.color_btn = tk.Button(edit, text="  Colour…  ", relief="flat", bd=0, cursor="hand2", state="disabled",
                                   font=fonts["small"], command=self._choose_color)
        self.color_btn.pack(side="left", padx=(12, 0))

        tk.Checkbutton(inner, text="Sort downloads into folders by favorite tag "
                                   "(Downloads/<site>/<label>/<gallery>)",
                       variable=self.folders, bg=palette["PANEL"], fg=palette["TEXT"], font=fonts["small"],
                       activebackground=palette["PANEL"], selectcolor=palette["PANEL"],
                       highlightthickness=0, anchor="w").pack(fill="x", pady=(8, 0))
        tk.Label(inner, text="Galleries without a favorite tag stay in Downloads/<site>. Lines already in the "
                             "batch list get their label after Verify Links.",
                 bg=palette["PANEL"], fg=palette["TEXT_SEC"], font=fonts["small"], justify="left", anchor="w",
                 wraplength=640).pack(fill="x", pady=(4, 0))
        bottom = tk.Frame(self.win, bg=palette["BG"])
        bottom.pack(fill="x", padx=12, pady=(0, 12))
        ttk.Button(bottom, text="💾  Save", style="Accent.TButton", command=self._save).pack(side="right")
        ttk.Button(bottom, text="Cancel", style="Ghost.TButton", command=self.win.destroy).pack(side="right", padx=6)

    # ------------------------------------------------------------------ list

    def _refresh(self, select: str | None = None) -> None:
        """Rebuild the rows: favorites first (in priority order), then the others."""
        needle = self.filter.get().strip().lower()
        self.tree.delete(*self.tree.get_children())
        others = [tag for tag, row in self.rows.items() if not row["favorite"]]
        others.sort(key=lambda tag: (self.rows[tag]["suggested"], -self.rows[tag]["seen"], tag))
        for position, tag in enumerate(self.order + others):
            row = self.rows[tag]
            if needle and needle not in tag and needle not in row["label"].lower():
                continue
            if row["favorite"]:
                values = (f"★ {position + 1}", tag, tag_utils.badge_text(row["label"]), row["seen"] or "")
                color_tag = f"color_{row['color'][1:]}"
                # the row has the colour of its label in the lists
                self.tree.tag_configure(color_tag, background=row["color"], foreground=tag_utils.text_color(row["color"]))
                tags = ("favorite", color_tag)
            else:
                seen = row["seen"] or ("suggested" if row["suggested"] else "")
                values = ("☐", tag, "", seen)
                tags = ("suggested",) if row["suggested"] else ()
            self.tree.insert("", "end", iid=tag, values=values, tags=tags)
        favorites, seen = len(self.order), tag_utils.seen_count()
        self.count_lbl.configure(text=f"{favorites} favorite{'s' if favorites != 1 else ''} · "
                                      f"{seen} tag{'s' if seen != 1 else ''} met in your galleries")
        if select and self.tree.exists(select):
            self.tree.selection_set(select)
            self.tree.see(select)
        self._on_select()

    def _selected(self) -> str | None:
        selection = self.tree.selection()
        return selection[0] if selection else None

    def _on_click(self, event) -> None:
        """A click in the ★ column switches the favorite on / off."""
        if self.tree.identify_region(event.x, event.y) != "cell" or self.tree.identify_column(event.x) != "#1":
            return
        tag = self.tree.identify_row(event.y)
        if tag:
            self._toggle(tag)

    def _toggle(self, tag: str | None) -> None:
        if not tag or tag not in self.rows:
            return
        self._apply_label()
        row = self.rows[tag]
        row["favorite"] = not row["favorite"]
        if row["favorite"]:
            used = {self.rows[t]["color"] for t in self.order}
            row["color"] = row.get("color") or next(
                (c for c in tag_utils.DEFAULT_COLORS if c not in used), tag_utils.default_color(len(self.order)))
            self.order.append(tag)
        else:
            self.order.remove(tag)
        self._refresh(select=tag)

    def _on_select(self) -> None:
        tag = self._selected()
        is_fav = bool(tag and self.rows.get(tag, {}).get("favorite"))
        self.label.set(self.rows[tag]["label"] if is_fav else "")
        self._label_tag = tag if is_fav else None
        self.label_entry.configure(state="normal" if is_fav else "disabled")
        color = self.rows[tag]["color"] if is_fav else self.palette["PANEL"]
        self.color_btn.configure(state="normal" if is_fav else "disabled", bg=color,
                                 fg=tag_utils.text_color(color) if is_fav else self.palette["MUTED"],
                                 activebackground=color)
        index = self.order.index(tag) if is_fav else -1
        self.up_btn.configure(state="normal" if is_fav and index > 0 else "disabled")
        self.down_btn.configure(state="normal" if is_fav and index < len(self.order) - 1 else "disabled")

    def _apply_label(self) -> None:
        """Store the label typed for the selected favorite."""
        tag = self._label_tag
        if not tag or tag not in self.rows:
            return
        label = " ".join(self.label.get().split()) or tag_utils.split(tag)[1]
        if label != self.rows[tag]["label"]:
            self.rows[tag]["label"] = label
            self._refresh(select=tag)

    def _choose_color(self) -> None:
        """'Colour…': pick the colour of the selected favorite."""
        tag = self._label_tag
        if not tag or tag not in self.rows:
            return
        from tkinter import colorchooser  # pylint: disable=import-outside-toplevel
        _rgb, chosen = colorchooser.askcolor(color=self.rows[tag]["color"], parent=self.win,
                                             title=f"Colour of {tag}")
        if chosen:
            self.rows[tag]["color"] = chosen.upper()
            self._refresh(select=tag)

    def _move(self, delta: int) -> None:
        tag = self._selected()
        if not tag or tag not in self.order:
            return
        self._apply_label()
        index = self.order.index(tag)
        target = index + delta
        if 0 <= target < len(self.order):
            self.order[index], self.order[target] = self.order[target], self.order[index]
            self._refresh(select=tag)

    def _add_tag(self) -> None:
        """'Add': a tag typed by the user becomes a favorite."""
        parsed = tag_utils.parse_favorites(self.new_tag.get())
        if not parsed:
            return
        tag = parsed[0]["tag"]
        name = tag_utils.split(tag)[1]
        existing = tag if tag in self.rows else (name if name in self.rows and ":" not in tag else None)
        if existing is None:
            self.rows[tag] = {"tag": tag, "label": parsed[0]["label"], "color": "", "favorite": False,
                              "seen": 0, "suggested": False}
            existing = tag
        self.new_tag.set("")
        self.filter.set("")
        if not self.rows[existing]["favorite"]:
            self._toggle(existing)
        else:
            self._refresh(select=existing)

    def _save(self) -> None:
        self._apply_label()
        favorites = [{"tag": tag, "label": self.rows[tag]["label"], "color": self.rows[tag]["color"]}
                     for tag in self.order]
        self.win.destroy()
        self.on_saved(tag_utils.clean_favorites(favorites), bool(self.folders.get()))


def platform_note() -> str:
    """Short system description for logs."""
    return f"{platform.system()} {platform.release()} · Python {sys.version.split()[0]}"
