"""Secondary windows of the GUI: start-up notice, account cookies, license, about.

The windows take the current colour palette (dict from src.ui_theme) and use the ttk
styles configured by the main window, so they follow the light / dark theme.
"""

from __future__ import annotations

import platform
import sys
import time
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from src import cookie_import, paths
from src.config import APP_VERSION, DISCLAIMER_SNOOZE_DAYS, ISSUES_URL, PROJECT_URL, UPSTREAM_URL

APP_NAME = "EHentai Downloader"

DISCLAIMER_TITLE = "Before you start"
DISCLAIMER_TEXT = f"""Please read this notice before using {APP_NAME}.

1. No responsibility. The developer takes no responsibility for bans, account restrictions, \
blocks, copyright issues or any other problem that may come from using this program. \
Respect the terms of service of the websites and the copyright of the works you download.

2. As is, free of charge. The program is provided free of charge and "as is", without \
warranty of any kind. You use it at your own risk and under your own responsibility.

3. It may stop working. The websites can change at any moment, so the program may suddenly \
stop working, completely or in part. The source code is open and free (GPL-3.0): bugs and \
problems can be reported on GitHub ({ISSUES_URL}) and will be looked at, but a quick answer \
is not guaranteed. You are free to make your own fork and fix a problem yourself, keeping the \
credits to the original developer ({UPSTREAM_URL}) and to this modified version ({PROJECT_URL}).

4. Made with AI help. This program has been developed and modified with the help of \
Claude AI (Anthropic). Part of the code was not written by hand by the developer, and this \
is stated openly.

If you do not accept these terms, you are strongly advised to delete the program and stop \
using it."""

THIRD_PARTY_TEXT = """Third-party software used by this program (each under its own license):
  • Python and Tkinter – PSF License
  • requests – Apache 2.0
  • Beautiful Soup – MIT
  • Pillow – MIT-CMU (HPND)
  • Rich – MIT
  • ImageMagick (optional, external program) – ImageMagick License
"""


def open_url(url: str) -> None:
    """Open a link in the default browser (any browser: also Flatpak / Snap ones)."""
    try:
        webbrowser.open(url, new=2)
    except Exception as err:  # pylint: disable=broad-except
        messagebox.showerror("Open link", f"Could not open the browser:\n{err}\n\nLink: {url}")


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
    win = _dialog(root, f"About {APP_NAME}", palette, 600, 460)
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
        "",
        f"Data folder: {paths.app_dir()}",
        f"Python {platform.python_version()} · Tk {root.tk.call('info', 'patchlevel')} · {platform.system()} {platform.release()}",
    )
    tk.Label(body, text="\n".join(lines), bg=palette["BG"], fg=palette["TEXT_SEC"], font=fonts["small"],
             justify="left", anchor="w").pack(anchor="w", pady=(8, 12), fill="x")
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


def platform_note() -> str:
    """Short system description for logs."""
    return f"{platform.system()} {platform.release()} · Python {sys.version.split()[0]}"
