"""Window layout: ttk styles, menu bar, theme, tabs, progress and log areas."""

# pylint: disable=protected-access,too-many-lines
from __future__ import annotations

import sys
from tkinter import (
    BooleanVar,
    StringVar,
    Text,
)
import tkinter as tk
from tkinter import ttk

from src.config import (
    APP_VERSION,
    CBZ_COMIC_INFO,
    CREATE_CBZ,
    RENAME_SEQUENTIAL,
)
from src.downloader.cbz_archive import set_comic_info
from src import gui_dialogs, paths, throttle, ui_theme
from src.config import ISSUES_URL, PROJECT_URL

from src.gui import style
from src.gui.style import (
    DIALOG_FONTS, FONT_BTN, FONT_HEAD, FONT_MONO, FONT_MONO_BOLD, FONT_SMALL, FONT_TITLE,
    FONT_UI, PAD, TAB_WIDTH, WHEN_DONE_NOTHING, WHEN_DONE_OPTIONS, btn as _btn,
    card as _card,
)


class LayoutMixin:  # pylint: disable=too-few-public-methods,no-member
    """Window layout: ttk styles, menu bar, theme, tabs, progress and log areas. Mixed into EHentaiGUI."""

    def _setup_styles(self) -> None:
        """Configure the light ttk theme (colours, fonts, buttons, progress bars)."""
        s = ttk.Style(self)
        s.theme_use("clam")

        # General
        s.configure(".",             background=style.BG, foreground=style.TEXT,
                     font=FONT_UI,  borderwidth=0, relief="flat",
                     bordercolor=style.BORDER, lightcolor=style.BORDER, darkcolor=style.BORDER,
                     troughcolor=style.BORDER, fieldbackground=style.PANEL, insertcolor=style.TEXT,
                     selectbackground=style.ACCENT, selectforeground=style.ON_ACCENT)
        s.configure("TFrame",        background=style.BG)
        s.configure("Card.TFrame",   background=style.PANEL, relief="flat")

        # Notebook / tabs
        s.configure("TNotebook", background=style.BG, borderwidth=0, tabmargins=(0, 0, 0, 0))
        # Every tab has the same size, selected or not (clam enlarges the selected one by default):
        # fixed width in characters, centred text, no "expand"; the selected tab is shown by colour.
        s.configure("TNotebook.Tab",
                    background=style.BORDER, foreground=style.TEXT_SEC,
                    padding=(14, 7), font=FONT_UI, width=TAB_WIDTH, anchor="center",
                    bordercolor=style.BORDER, lightcolor=style.BORDER, darkcolor=style.BORDER)
        s.map("TNotebook.Tab",
              expand=[("selected", (0, 0, 0, 0))],
              padding=[("selected", (14, 7))],
              background=[("selected", style.ACCENT), ("active", style.TAB_HOVER)],
              foreground=[("selected", style.ON_ACCENT), ("active", style.ACCENT)],
              lightcolor=[("selected", style.ACCENT)],
              bordercolor=[("selected", style.ACCENT)])
        # Same layout without the dotted focus ring
        s.layout("TNotebook.Tab", [
            ("Notebook.tab", {"sticky": "nswe", "children": [
                ("Notebook.padding", {"side": "top", "sticky": "nswe", "children": [
                    ("Notebook.label", {"side": "top", "sticky": ""}),
                ]}),
            ]}),
        ])

        # Buttons
        s.configure("Accent.TButton",
                    background=style.ACCENT, foreground=style.ON_ACCENT,
                    font=FONT_BTN, padding=(16, 8),
                    relief="flat", borderwidth=0)
        s.map("Accent.TButton",
              background=[("active", style.ACCENT2), ("disabled", style.BORDER)],
              foreground=[("disabled", style.MUTED)])

        s.configure("Ghost.TButton",
                    background=style.PANEL, foreground=style.ACCENT,
                    font=FONT_UI, padding=(10, 6),
                    relief="flat", borderwidth=1)
        s.map("Ghost.TButton",
              background=[("active", style.TAB_HOVER), ("disabled", style.PANEL)],
              foreground=[("disabled", style.MUTED)])

        s.configure("Danger.TButton",
                    background=style.ERROR, foreground=style.ON_ACCENT,
                    font=FONT_BTN, padding=(16, 8),
                    relief="flat", borderwidth=0)
        s.map("Danger.TButton",
              background=[("active", style.DANGER_ACTIVE), ("disabled", style.BORDER)])

        # Labels
        s.configure("Title.TLabel",
                    background=style.BG, foreground=style.TEXT,   font=FONT_TITLE)
        s.configure("Head.TLabel",
                    background=style.PANEL, foreground=style.TEXT, font=FONT_HEAD)
        s.configure("Sub.TLabel",
                    background=style.PANEL, foreground=style.TEXT_SEC, font=FONT_SMALL)
        s.configure("Status.TLabel",
                    background=style.BG, foreground=style.TEXT_SEC, font=FONT_SMALL)

        # Entry
        s.configure("TEntry",
                    fieldbackground=style.PANEL, foreground=style.TEXT,
                    insertcolor=style.TEXT, borderwidth=1,
                    relief="solid", padding=(8, 6))

        # Progressbar — copy the qualified horizontal layout before creating a variant
        s.configure("TProgressbar",
                    troughcolor=style.BORDER, background=style.ACCENT,
                    thickness=10, borderwidth=0)
        s.layout("Horizontal.Task.TProgressbar",
                 s.layout("Horizontal.TProgressbar"))
        s.configure("Task.TProgressbar",
                    troughcolor=style.BORDER, background=style.TASK_BAR,
                    thickness=10, borderwidth=0)

        # Separator
        s.configure("TSeparator", background=style.BORDER)

        # Scrollbar
        s.configure("TScrollbar",
                    background=style.BORDER, troughcolor=style.BG, bordercolor=style.BG,
                    lightcolor=style.BORDER, darkcolor=style.BORDER,
                    arrowcolor=style.TEXT_SEC, borderwidth=0)
        s.map("TScrollbar",
              background=[("active", style.MUTED), ("disabled", style.BG)],
              arrowcolor=[("disabled", style.MUTED)])
        for orient in ("Vertical", "Horizontal"):
            s.configure(f"{orient}.TScrollbar", background=style.BORDER, troughcolor=style.BG, bordercolor=style.BG,
                        lightcolor=style.BORDER, darkcolor=style.BORDER, arrowcolor=style.TEXT_SEC, gripcount=0)
            s.map(f"{orient}.TScrollbar", background=[("active", style.MUTED), ("disabled", style.BG)])

        # Combobox (also the read-only look, used by every combobox of the window)
        s.configure("TCombobox",
                    fieldbackground=style.PANEL, background=style.PANEL, foreground=style.TEXT,
                    arrowcolor=style.TEXT, bordercolor=style.BORDER, lightcolor=style.PANEL, darkcolor=style.PANEL,
                    selectbackground=style.PANEL, selectforeground=style.TEXT)
        s.map("TCombobox",
              fieldbackground=[("readonly", style.PANEL), ("disabled", style.BG)],
              foreground=[("readonly", style.TEXT), ("disabled", style.MUTED)],
              background=[("active", style.TAB_HOVER), ("readonly", style.PANEL)],
              selectbackground=[("readonly", style.PANEL)],
              selectforeground=[("readonly", style.TEXT)],
              arrowcolor=[("disabled", style.MUTED)])

        # Check buttons in dialogs
        s.configure("TCheckbutton", background=style.PANEL, foreground=style.TEXT_SEC)

        # Entry: same border as the editors
        s.map("TEntry", bordercolor=[("focus", style.ACCENT)], lightcolor=[("focus", style.ACCENT)])

    def _menu(self, parent) -> tk.Menu:
        """A drop-down menu with the theme colours (Linux / macOS draw them with Tk)."""
        return tk.Menu(
            parent, tearoff=0, bg=style.PANEL, fg=style.TEXT, activebackground=style.ACCENT, activeforeground=style.ON_ACCENT,
            selectcolor=style.ACCENT, disabledforeground=style.MUTED, relief="flat", bd=1,
        )

    def _build_menu(self) -> None:
        """File / Settings / Help menus."""
        self._menubar_frame = None
        if sys.platform != "darwin":
            # Windows draws its own menu bar (always light): use one made of themed buttons
            self._menubar_frame = tk.Frame(self, bg=style.PANEL)
            self._menubar_frame.pack(side="top", fill="x")
            tk.Frame(self, bg=style.BORDER, height=1).pack(side="top", fill="x")
        else:
            self._menubar = self._menu(self)   # macOS: native menu bar at the top of the screen

        file_menu = self._file_menu = self._top_menu("File")
        file_menu.add_command(label="Open Downloads folder", command=self._open_downloads_folder)
        file_menu.add_command(label="Open log file", command=self._open_session_log)
        file_menu.add_separator()
        file_menu.add_command(label="Account & cookies…", command=self._open_account_dialog)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self._on_window_close)

        settings = self._settings_menu = self._top_menu("Settings")
        settings.add_checkbutton(label="Auto Rename (01…N when complete)", variable=self._rename_var_init(),
                                 command=self._on_rename_changed)
        settings.add_checkbutton(label="CBZ archive", variable=self._cbz_var_init(),
                                 command=self._on_cbz_changed)
        settings.add_checkbutton(label="ComicInfo.xml in CBZ", variable=self._comic_info_var_init(),
                                 command=self._on_comic_info_changed)
        settings.add_separator()
        self._watch_clipboard_var = BooleanVar(value=bool(self._settings.get("watch_clipboard", False)))
        settings.add_checkbutton(label="Watch clipboard for links", variable=self._watch_clipboard_var,
                                 command=self._on_watch_clipboard_changed)
        settings.add_separator()

        self._speed_var = tk.IntVar(value=throttle.get_speed())
        speed_menu = self._speed_menu = self._menu(settings)
        for level in range(throttle.MIN_SPEED, throttle.MAX_SPEED + 1):
            name, pause, workers = throttle.SPEED_LEVELS[level]
            speed_menu.add_radiobutton(
                label=f"{level}x  {name}  ({workers} parallel, {pause:g} s between pages)",
                variable=self._speed_var, value=level, command=self._on_speed_changed,
            )
        settings.add_cascade(label="Speed", menu=speed_menu)

        self._when_done_var = StringVar(value=WHEN_DONE_NOTHING)
        done_menu = self._menu(settings)
        for choice in WHEN_DONE_OPTIONS:
            done_menu.add_radiobutton(label=choice, variable=self._when_done_var, value=choice,
                                      command=self._on_when_done_changed)
        settings.add_cascade(label="When done", menu=done_menu)
        settings.add_separator()

        theme_menu = self._menu(settings)
        for choice in ui_theme.THEME_CHOICES:
            theme_menu.add_radiobutton(label=ui_theme.THEME_LABELS[choice], variable=self._theme_choice,
                                       value=choice, command=self._on_theme_changed)
        settings.add_cascade(label="Theme", menu=theme_menu)

        help_menu = self._top_menu("Help")
        help_menu.add_command(label="GitHub page", command=lambda: gui_dialogs.open_url(PROJECT_URL))
        help_menu.add_command(label="Report a problem…", command=lambda: gui_dialogs.open_url(ISSUES_URL))
        help_menu.add_separator()
        help_menu.add_command(label="Check for updates now", command=lambda: self.check_for_updates(manual=True))
        self._check_updates_var = BooleanVar(value=bool(self._settings.get("check_updates", True)))
        help_menu.add_checkbutton(label="Check for updates at start-up", variable=self._check_updates_var,
                                  command=self._save_settings)
        help_menu.add_separator()
        help_menu.add_command(label="Disclaimer", command=lambda: gui_dialogs.show_disclaimer(
            self, self._palette(), DIALOG_FONTS, self._settings, self._save_settings, startup=False))
        help_menu.add_command(label="License", command=lambda: gui_dialogs.show_license(
            self, self._palette(), DIALOG_FONTS))
        help_menu.add_command(label="About", command=lambda: gui_dialogs.show_about(
            self, self._palette(), DIALOG_FONTS))
        if self._menubar_frame is None:
            self.configure(menu=self._menubar)

    def _top_menu(self, label: str) -> tk.Menu:
        """One menu of the menu bar (themed button + drop-down, or native on macOS)."""
        if self._menubar_frame is None:
            menu = self._menu(self._menubar)
            self._menubar.add_cascade(label=label, menu=menu)
            return menu
        button = tk.Menubutton(
            self._menubar_frame, text=label, bg=style.PANEL, fg=style.TEXT, font=FONT_SMALL,
            activebackground=style.TAB_HOVER, activeforeground=style.ACCENT, relief="flat", bd=0,
            padx=10, pady=4, cursor="hand2", underline=0,
        )
        menu = self._menu(button)
        button.configure(menu=menu)
        button.pack(side="left")
        # Alt+F / Alt+S / Alt+H open the menus, like a native menu bar
        self.bind_all(f"<Alt-{label[0].lower()}>", lambda _e, b=button, m=menu: m.post(
            b.winfo_rootx(), b.winfo_rooty() + b.winfo_height()))
        return menu

    def _rename_var_init(self) -> BooleanVar:
        """Create the Auto Rename variable from the saved settings (menu is built first)."""
        self._rename_var = BooleanVar(value=bool(self._settings.get("auto_rename", RENAME_SEQUENTIAL)))
        return self._rename_var

    def _cbz_var_init(self) -> BooleanVar:
        """Create the CBZ archive variable from the saved settings."""
        self._cbz_var = BooleanVar(value=bool(self._settings.get("cbz_archive", CREATE_CBZ)))
        return self._cbz_var

    def _comic_info_var_init(self) -> BooleanVar:
        """Create the 'ComicInfo.xml in CBZ' variable from the saved settings (off by default)."""
        self._comic_info_var = BooleanVar(value=bool(self._settings.get("comic_info", CBZ_COMIC_INFO)))
        set_comic_info(self._comic_info_var.get())
        return self._comic_info_var

    def _set_menu_options_state(self, state: str) -> None:
        """Lock Auto Rename / CBZ archive / ComicInfo while a download runs (read during it)."""
        for index in (0, 1, 2):
            self._settings_menu.entryconfigure(index, state=state)

    def _palette(self) -> dict:
        """Colours of the theme in use."""
        return ui_theme.PALETTES[self._theme_active]

    def _apply_theme(self, theme: str, initial: bool = False) -> None:
        """Switch every colour of the window (and open dialogs) to the given palette."""
        if theme == self._theme_active and not initial:
            return
        old = self._theme_active
        style.set_palette(theme)   # colours used by widgets created from now on
        self._theme_active = theme
        self._setup_styles()                          # ttk styles
        self.option_add("*TCombobox*Listbox.background", style.PANEL)
        self.option_add("*TCombobox*Listbox.foreground", style.TEXT)
        self.option_add("*TCombobox*Listbox.selectBackground", style.ACCENT)
        self.option_add("*TCombobox*Listbox.selectForeground", style.ON_ACCENT)
        if old != theme:
            ui_theme.recolor_tree(self, ui_theme.color_map(old, theme))
            for combo in (self._format_cb, self._when_done_cb):
                try:  # drop-down list already created: recolour it too
                    popdown = self.tk.eval(f"ttk::combobox::PopdownWindow {combo}")
                    self.tk.eval(f"{popdown}.f.l configure -background {style.PANEL} -foreground {style.TEXT} "
                                 f"-selectbackground {style.ACCENT} -selectforeground {style.ON_ACCENT}")
                except tk.TclError:
                    pass
        self._refresh_speed_label()
        ui_theme.set_windows_title_bar(self, theme == ui_theme.THEME_DARK)

    def _on_theme_changed(self) -> None:
        """Theme chosen in the menu."""
        self._apply_theme(ui_theme.resolve(self._theme_choice.get()))
        self._save_settings()

    def _follow_system_theme(self) -> None:
        """'Automatic': check the system theme every minute."""
        if self._closed:
            return
        if self._theme_choice.get() == ui_theme.THEME_AUTO:
            self._apply_theme(ui_theme.resolve(ui_theme.THEME_AUTO))
        self.after(60_000, self._follow_system_theme)

    def _build_ui(self) -> None:
        # ── Header bar ──────────────────────────────────────────────────────
        """Build the header bar, status bar, log, progress bars and the tab notebook."""
        hdr = tk.Frame(self, bg=style.PANEL, height=64)
        hdr.pack(fill="x", side="top")
        hdr.pack_propagate(False)

        # Small logo next to the title
        try:
            self._header_logo = tk.PhotoImage(file=str(paths.resource("assets/icon_small.png")))
        except (tk.TclError, OSError):
            self._header_logo = None
        tk.Label(hdr, text="  EHentai Downloader", image=self._header_logo, compound="left",
                 bg=style.PANEL, fg=style.TEXT, font=FONT_TITLE).pack(
                     side="left", padx=(PAD*2, 0), pady=8)
        # Version next to the title (also in the title bar and Help → About)
        tk.Label(hdr, text=f"v{APP_VERSION}", bg=style.PANEL, fg=style.TEXT_SEC,
                 font=FONT_SMALL).pack(side="left", padx=(6, 0), pady=(14, 8))

        hdr_right = tk.Frame(hdr, bg=style.PANEL)
        hdr_right.pack(side="right", padx=PAD*2)

        # Global Image Format Selector
        tk.Label(hdr_right, text="Image Format:", bg=style.PANEL, fg=style.TEXT_SEC,
                 font=FONT_SMALL).pack(side="left", padx=(0, 6))

        self._format_options = [
            "AUTO (JPG / Native if animated)",
            "Native (Original)",
            "JPG (.jpg)",
            "PNG (.png)",
            "WEBP (.webp)",
        ]
        saved_format = self._settings.get("image_format")
        initial_format = saved_format if saved_format in self._format_options else self._format_options[0]
        self._format_var = StringVar(value=initial_format)

        self._format_cb = ttk.Combobox(
            hdr_right,
            textvariable=self._format_var,
            values=self._format_options,
            state="readonly",
            width=28,
            font=FONT_SMALL,
        )
        self._format_cb.pack(side="left", padx=(0, PAD*2))
        self._format_cb.bind("<<ComboboxSelected>>", self._on_format_changed)

        # Auto Rename / CBZ archive / Speed / When done / Theme live in the "Settings" menu

        ttk.Separator(self, orient="horizontal").pack(side="top", fill="x")

        # ── Status bar (pinned to bottom) ───────────────────────────────────
        ttk.Separator(self, orient="horizontal").pack(side="bottom", fill="x")
        sb = tk.Frame(self, bg=style.BG)
        sb.pack(side="bottom", fill="x")
        # What to do when all downloads are finished (far right, like HandBrake)
        done_box = tk.Frame(sb, bg=style.BG)
        done_box.pack(side="right", padx=(0, PAD))
        tk.Label(done_box, text="When done:", bg=style.BG, fg=style.TEXT_SEC, font=FONT_SMALL).pack(side="left")
        self._when_done_cb = ttk.Combobox(
            done_box, textvariable=self._when_done_var, values=WHEN_DONE_OPTIONS,
            state="readonly", width=14, font=FONT_SMALL,
        )
        self._when_done_cb.pack(side="left", padx=(6, 0))
        self._when_done_cb.bind("<<ComboboxSelected>>", self._on_when_done_changed)
        self._when_done_dialog = None

        # Current speed (changed from Settings → Speed): fewer requests = less risk of a ban
        self._speed_lbl = tk.Label(sb, bg=style.BG, fg=style.TEXT, font=FONT_SMALL, anchor="w", cursor="hand2")
        self._speed_lbl.pack(side="right", padx=PAD)
        self._speed_lbl.bind("<Button-1>", lambda e: self._speed_menu.tk_popup(e.x_root, e.y_root))
        self._speed_box = self._speed_lbl

        # Images downloaded since the program was opened (resets at every start)
        self._session_var = StringVar()
        tk.Label(sb, textvariable=self._session_var, bg=style.BG, fg=style.TEXT_SEC,
                 font=FONT_SMALL, anchor="e").pack(side="right", padx=PAD)
        self._refresh_session_counter()

        # Shown only during an IP ban: skip the wait after switching IP (e.g. with a VPN)
        self._resume_btn = tk.Button(
            sb, text="▶  Resume now",
            bg=style.WARNING, fg=style.ON_ACCENT, activebackground=style.ERROR, activeforeground=style.ON_ACCENT,
            font=FONT_SMALL, relief="flat", cursor="hand2", padx=8, pady=0,
            command=self._resume_after_ban,
        )

        # Status text last: the speed controls keep their space, a long message is cut
        self._status_var = StringVar(value="Ready.")
        self._status_lbl = ttk.Label(sb, textvariable=self._status_var, style="Status.TLabel")
        self._status_lbl.pack(side="left", padx=PAD, pady=4)

        # ── Log area (pinned above status bar, expand=True) ─────────────────
        self._build_log_area()

        # ── Progress area (pinned above log area) ───────────────────────────
        self._build_progress_area()

        # ── Tab notebook (top area) ──────────────────────────────────────────
        self._nb = ttk.Notebook(self)
        self._nb.pack(side="top", fill="both", expand=True, padx=PAD, pady=(PAD, 4))

        self._tab_single = ttk.Frame(self._nb, style="TFrame")
        self._tab_batch  = ttk.Frame(self._nb, style="TFrame")
        self._tab_retry  = ttk.Frame(self._nb, style="TFrame")

        self._nb.add(self._tab_single, text="🔗  Single Download")
        self._nb.add(self._tab_batch,  text="📋  Batch Download")
        self._nb.add(self._tab_retry,  text="🔄  Retry Failed")

        # "Open Downloads" sits on the tab-bar row, right-aligned: always visible, whatever tab is open
        self._open_downloads_btn = tk.Button(
            self, text="📁  Open Downloads",
            bg=style.ACCENT, fg=style.ON_ACCENT, activebackground=style.ACCENT2, activeforeground=style.ON_ACCENT,
            font=FONT_SMALL, relief="flat", cursor="hand2", padx=10, pady=2,
            command=self._open_downloads_folder,
        )
        self._open_downloads_btn.place(in_=self._nb, relx=1.0, x=-2, y=1, anchor="ne")
        self._open_downloads_btn.lift()

        self._build_single_tab()
        self._build_batch_tab()
        self._build_retry_tab()

    def _build_single_tab(self) -> None:
        """Build the 'Single Download' tab: one URL field plus Paste / Start buttons."""
        tab = self._tab_single
        tab.columnconfigure(0, weight=1)

        card = _card(tab)
        card.grid(row=0, column=0, padx=PAD, pady=PAD, sticky="nsew")
        tab.rowconfigure(0, weight=1)
        card.columnconfigure(1, weight=1)

        ttk.Label(card, text="Single Album Download",
                  style="Head.TLabel").grid(
                      row=0, column=0, columnspan=3,
                      padx=PAD, pady=(PAD, 4), sticky="w")
        ttk.Label(card, text="Paste an E-Hentai / ExHentai / nhentai gallery URL and click Start.",
                  style="Sub.TLabel").grid(
                      row=1, column=0, columnspan=3,
                      padx=PAD, pady=(0, PAD), sticky="w")

        ttk.Label(card, text="Gallery URL", background=style.PANEL,
                  font=FONT_SMALL, foreground=style.TEXT_SEC).grid(
                      row=2, column=0, padx=(PAD, 4), pady=4, sticky="w")

        self._single_url = StringVar()
        entry = ttk.Entry(card, textvariable=self._single_url, font=FONT_UI)
        entry.grid(row=2, column=1, padx=4, pady=4, sticky="ew")
        entry.bind("<Return>", lambda _: self._start_single())

        tk.Button(card, text="✕", bg=style.PANEL, fg=style.MUTED, font=FONT_SMALL,
                  relief="flat", cursor="hand2",
                  command=lambda: self._single_url.set("")).grid(
                      row=2, column=2, padx=(0, 4))

        btn_row = tk.Frame(card, bg=style.PANEL)
        btn_row.grid(row=3, column=0, columnspan=3,
                     padx=PAD, pady=(8, PAD), sticky="e")

        self._single_paste_btn = _btn(btn_row, "📋  Paste", self._paste_single_url,
                                      style="Ghost.TButton")
        self._single_paste_btn.pack(side="left", padx=(0, 8))

        self._single_btn = _btn(btn_row, "▶  Start Download", self._start_single)
        self._single_btn.pack(side="left")

    def _build_batch_tab(self) -> None:
        """Build the 'Batch Download' tab: an editor bound to URLs.txt."""
        tab = self._tab_batch
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(1, weight=1)

        card = _card(tab)
        card.grid(row=0, column=0, rowspan=2, padx=PAD, pady=PAD, sticky="nsew")
        card.columnconfigure(0, weight=1)
        card.rowconfigure(2, weight=1)

        ttk.Label(card, text="Batch Download (URLs.txt)",
                  style="Head.TLabel").grid(
                      row=0, column=0, columnspan=2,
                      padx=PAD, pady=(PAD, 4), sticky="w")
        ttk.Label(card,
                  text="One URL per line, saved automatically. Paste or drag any text with links: "
                       "only gallery links are kept.\nYou can keep adding links while the batch runs; "
                       "downloaded lines disappear after a while.",
                  justify="left",
                  style="Sub.TLabel").grid(
                      row=1, column=0, columnspan=2,
                      padx=PAD, pady=(0, 6), sticky="w")

        # Editor
        editor_frame = tk.Frame(card, bg=style.BORDER, bd=1)
        editor_frame.grid(row=2, column=0, columnspan=2,
                          padx=PAD, pady=4, sticky="nsew")
        editor_frame.rowconfigure(0, weight=1)
        editor_frame.columnconfigure(0, weight=1)

        self._batch_editor = Text(
            editor_frame,
            font=FONT_MONO, bg=style.PANEL, fg=style.TEXT,
            insertbackground=style.TEXT, selectbackground=style.SELECT_BG,
            relief="flat", bd=0, height=6,
            wrap="none", undo=True,
        )
        self._batch_editor.grid(row=0, column=0, sticky="nsew")
        # Ctrl+V keeps only the gallery links of the copied text (a page, a message...)
        self._batch_editor.bind("<<Paste>>", self._on_batch_paste)

        vsb = ttk.Scrollbar(editor_frame, orient="vertical",
                             command=self._batch_editor.yview)
        vsb.grid(row=0, column=1, sticky="ns")
        self._batch_editor.configure(yscrollcommand=vsb.set)

        self._load_urls_txt()
        self._batch_editor.tag_configure(
            "queued_batch_line",
            background=style.LINE_QUEUED_BG,
        )
        self._batch_editor.tag_configure(
            "active_batch_line",
            background=style.LINE_ACTIVE_BG,
            foreground=style.LINE_ACTIVE_FG,
        )
        self._batch_editor.tag_configure(
            "done_batch_line",
            background=style.LINE_DONE_BG,
            foreground=style.LINE_DONE_FG,
        )
        self._batch_editor.tag_configure(
            "fail_batch_line",
            background=style.LINE_FAIL_BG,
            foreground=style.LINE_FAIL_FG,
        )
        # Later states must win over "queued" when both are on a line
        for tag in ("active_batch_line", "done_batch_line", "fail_batch_line"):
            self._batch_editor.tag_raise(tag, "queued_batch_line")

        # Estimate of the images still to download (exact once the links are verified)
        self._estimate_var = StringVar(value="")
        ttk.Label(card, textvariable=self._estimate_var,
                  style="Sub.TLabel").grid(
                      row=3, column=0, columnspan=2,
                      padx=PAD, pady=(2, 0), sticky="w")

        # Buttons row
        btn_row = tk.Frame(card, bg=style.PANEL)
        btn_row.grid(row=4, column=0, columnspan=2,
                     padx=PAD, pady=(6, PAD), sticky="ew")

        self._reload_btn = _btn(btn_row, "🔄  Reload", self._load_urls_txt,
                                style="Ghost.TButton")
        self._reload_btn.pack(side="left", padx=(0, 6))
        _btn(btn_row, "💾  Save", self._save_urls_txt,
             style="Ghost.TButton").pack(side="left", padx=(0, 6))
        self._clear_btn = _btn(btn_row, "🗑  Clear All", self._clear_urls_txt,
                               style="Ghost.TButton")
        self._clear_btn.pack(side="left", padx=(0, 6))
        self._dedupe_btn = _btn(btn_row, "🧹  Duplicates", self._remove_duplicates,
                                style="Ghost.TButton")
        self._dedupe_btn.pack(side="left", padx=(0, 6))
        self._verify_btn = _btn(btn_row, "🔍  Verify Links", self._start_verify,
                                style="Ghost.TButton")
        self._verify_btn.pack(side="left")

        self._batch_btn = _btn(btn_row, "▶  Start Batch", self._start_batch)
        self._batch_btn.pack(side="right", before=self._reload_btn)  # main actions keep their space
        # Pause / Resume: freezes every request; the list and the partial album stay saved,
        # so the program can also be closed and the batch restarted later
        self._pause_btn = _btn(btn_row, "⏸  Pause", self._toggle_pause, style="Ghost.TButton")
        self._pause_btn.pack(side="right", padx=(0, 6), before=self._reload_btn)
        self._pause_btn.configure(state="disabled")
        # Seven buttons on one row: size them on their text so all fit in the default window
        for child in btn_row.winfo_children():
            if isinstance(child, ttk.Button) and child is not self._batch_btn:
                child.configure(width=0)

    def _build_retry_tab(self) -> None:
        """Build the 'Retry Failed' tab: read-only list from failed_downloads.txt."""
        tab = self._tab_retry
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(1, weight=1)

        card = _card(tab)
        card.grid(row=0, column=0, rowspan=2, padx=PAD, pady=PAD, sticky="nsew")
        card.columnconfigure(0, weight=1)
        card.rowconfigure(2, weight=1)

        ttk.Label(card, text="Retry Failed Downloads",
                  style="Head.TLabel").grid(
                      row=0, column=0, columnspan=2,
                      padx=PAD, pady=(PAD, 4), sticky="w")
        ttk.Label(card,
                  text="URLs loaded from failed_downloads.txt.",
                  style="Sub.TLabel").grid(
                      row=1, column=0, columnspan=2,
                      padx=PAD, pady=(0, 6), sticky="w")

        # Read-only list
        list_frame = tk.Frame(card, bg=style.BORDER, bd=1)
        list_frame.grid(row=2, column=0, columnspan=2,
                        padx=PAD, pady=4, sticky="nsew")
        list_frame.rowconfigure(0, weight=1)
        list_frame.columnconfigure(0, weight=1)

        self._retry_list = Text(
            list_frame,
            font=FONT_MONO, bg=style.RETRY_BG, fg=style.TEXT,
            relief="flat", bd=0, height=6,
            state="disabled", wrap="none",
        )
        self._retry_list.grid(row=0, column=0, sticky="nsew")
        vsb2 = ttk.Scrollbar(list_frame, orient="vertical",
                              command=self._retry_list.yview)
        vsb2.grid(row=0, column=1, sticky="ns")
        self._retry_list.configure(yscrollcommand=vsb2.set)

        self._retry_list.tag_configure(
            "active_batch_line",
            background=style.LINE_ACTIVE_BG,
            foreground=style.LINE_ACTIVE_FG,
        )
        self._retry_list.tag_configure(
            "done_batch_line",
            background=style.LINE_DONE_BG,
            foreground=style.LINE_DONE_FG,
        )
        self._retry_list.tag_configure(
            "fail_batch_line",
            background=style.LINE_FAIL_BG,
            foreground=style.LINE_FAIL_FG,
        )

        # Counter label
        self._retry_count_var = StringVar(value="")
        ttk.Label(card, textvariable=self._retry_count_var,
                  style="Sub.TLabel").grid(
                      row=3, column=0,
                      padx=PAD, pady=4, sticky="w")

        # Buttons row
        btn_row = tk.Frame(card, bg=style.PANEL)
        btn_row.grid(row=4, column=0, columnspan=2,
                     padx=PAD, pady=(6, PAD), sticky="ew")

        _btn(btn_row, "🔄  Refresh List", self._load_retry_list,
             style="Ghost.TButton").pack(side="left", padx=(0, 6))
        _btn(btn_row, "🗑  Clear Queue", self._clear_retry_queue,
             style="Ghost.TButton").pack(side="left")

        self._retry_btn = _btn(btn_row, "🔄  Retry Failed", self._start_retry)
        self._retry_btn.pack(side="right")

        self._load_retry_list()

    def _build_progress_area(self) -> None:
        """Build the two progress bars (album/batch total and page/current album)."""
        prog_frame = tk.Frame(self, bg=style.BG)
        prog_frame.pack(side="bottom", fill="x", padx=PAD, pady=(4, 6))

        # Row 0: Bar 1 (Album Total / Batch Total)
        row0 = tk.Frame(prog_frame, bg=style.BG)
        row0.pack(fill="x", pady=(0, 3))
        row0.columnconfigure(1, weight=1)

        self._bar1_lbl = tk.Label(
            row0, text="Album Total:", bg=style.BG, fg=style.TEXT,
            font=FONT_SMALL, width=17, anchor="w",
        )
        self._bar1_lbl.grid(row=0, column=0, sticky="w", padx=(0, 6))

        self._bar1 = ttk.Progressbar(
            row0, orient="horizontal",
            style="TProgressbar", length=100,
        )
        self._bar1.grid(row=0, column=1, sticky="ew", padx=(0, 10))

        self._bar1_pct = tk.Label(
            row0, text="0%", bg=style.BG, fg=style.TEXT_SEC,
            font=FONT_SMALL, width=18, anchor="e",
        )
        self._bar1_pct.grid(row=0, column=2, sticky="e")

        # Row 1: Bar 2 (Page Download / Current Album)
        row1 = tk.Frame(prog_frame, bg=style.BG)
        row1.pack(fill="x")
        row1.columnconfigure(1, weight=1)

        self._bar2_lbl = tk.Label(
            row1, text="Page Download:", bg=style.BG, fg=style.TEXT,
            font=FONT_SMALL, width=17, anchor="w",
        )
        self._bar2_lbl.grid(row=0, column=0, sticky="w", padx=(0, 6))

        self._bar2 = ttk.Progressbar(
            row1, orient="horizontal",
            style="Task.TProgressbar", length=100,
        )
        self._bar2.grid(row=0, column=1, sticky="ew", padx=(0, 10))

        self._bar2_pct = tk.Label(
            row1, text="0%", bg=style.BG, fg=style.TEXT_SEC,
            font=FONT_SMALL, width=18, anchor="e",
        )
        self._bar2_pct.grid(row=0, column=2, sticky="e")

        # Backward compatibility aliases
        self._overall_bar = self._bar1
        self._overall_pct = self._bar1_pct
        self._task_bar = self._bar2
        self._task_pct = self._bar2_pct

    def _build_log_area(self) -> None:
        """Build the coloured log box with 'Open Log File' and 'Clear' buttons."""
        log_container = tk.Frame(self, bg=style.BG)
        log_container.pack(side="bottom", fill="x", padx=PAD, pady=(0, 6))

        lbl_row = tk.Frame(log_container, bg=style.BG)
        lbl_row.pack(side="top", fill="x")
        tk.Label(lbl_row, text="Log", bg=style.BG, fg=style.TEXT_SEC,
                 font=FONT_SMALL).pack(side="left")
        tk.Button(lbl_row, text="Clear", bg=style.BG, fg=style.MUTED, font=FONT_SMALL,
                  relief="flat", cursor="hand2",
                  command=self._clear_log).pack(side="right")
        tk.Button(lbl_row, text="📄 Open Log File", bg=style.BG, fg=style.ACCENT, font=FONT_SMALL,
                  relief="flat", cursor="hand2",
                  command=self._open_session_log).pack(side="right", padx=(0, 8))

        log_frame = tk.Frame(log_container, bg=style.BORDER, bd=1, height=115)
        log_frame.pack(side="top", fill="x", pady=(2, 0))
        log_frame.pack_propagate(False)
        self._log_frame = log_frame

        self._log_box = Text(
            log_frame,
            font=FONT_MONO, bg=style.PANEL, fg=style.TEXT,
            relief="flat", bd=0,
            state="disabled", wrap="word",
        )
        self._log_box.pack(side="left", fill="both", expand=True)

        vsb = ttk.Scrollbar(log_frame, orient="vertical",
                             command=self._log_box.yview)
        vsb.pack(side="right", fill="y")
        self._log_box.configure(yscrollcommand=vsb.set)

        # Colour tags
        self._log_box.tag_configure("INFO",           foreground=style.TEXT_SEC)
        self._log_box.tag_configure("WARNING",        foreground=style.WARNING)
        self._log_box.tag_configure("ERROR",          foreground=style.ERROR)
        self._log_box.tag_configure("CRITICAL",       foreground=style.ERROR)
        self._log_box.tag_configure("DEBUG",          foreground=style.MUTED)
        self._log_box.tag_configure("ts",             foreground=style.MUTED)
        self._log_box.tag_configure("event",          foreground=style.ACCENT)
        self._log_box.tag_configure("ok",             foreground=style.SUCCESS)
        self._log_box.tag_configure("success_banner", foreground=style.OK_BANNER,  font=FONT_MONO_BOLD)
        self._log_box.tag_configure("warn_banner",    foreground=style.WARN_BANNER,  font=FONT_MONO_BOLD)
        self._log_box.tag_configure("warn_url",       foreground=style.WARN_BANNER,  font=FONT_MONO_BOLD)
        self._log_box.tag_configure("warn_item",      foreground=style.WARNING)

    def _on_window_resize(self, event=None) -> None:
        """Dynamically scale log area between ~115px and 350px max when window is resized."""
        if event and event.widget == self:
            h = event.height
            new_log_h = min(350, max(115, 115 + int((h - self._initial_h) * 0.45)))
            if hasattr(self, "_log_frame"):
                self._log_frame.configure(height=new_log_h)
