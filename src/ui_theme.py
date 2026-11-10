"""Colour themes (light / dark / automatic) and platform fonts for the GUI.

Every colour used by the window has a name (BG, PANEL, TEXT, ...). LIGHT and DARK give
the same names different values; switching theme maps each value of the old palette to
the value of the new one on every widget (recolor_tree), so the window changes theme
while it is open, without rebuilding it.

For that mapping to be unambiguous, the values inside one palette must all be different
(checked at import time).

"Automatic" follows the operating system setting:
  Windows  registry AppsUseLightTheme
  macOS    defaults read -g AppleInterfaceStyle
  Linux    GNOME/GTK (gsettings color-scheme / gtk-theme, GTK_THEME), KDE (kdeglobals)
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

THEME_AUTO = "auto"
THEME_LIGHT = "light"
THEME_DARK = "dark"
THEME_CHOICES = (THEME_AUTO, THEME_LIGHT, THEME_DARK)
THEME_LABELS = {THEME_AUTO: "Automatic (system)", THEME_LIGHT: "Light", THEME_DARK: "Dark"}

LIGHT: dict[str, str] = {
    "BG": "#F5F7FA",          # window background
    "PANEL": "#FFFFFF",       # cards, editors, header
    "ACCENT": "#4A90D9",      # primary blue
    "ACCENT2": "#357ABD",     # darker blue (hover / active)
    "SUCCESS": "#27AE60",
    "WARNING": "#E67E22",
    "ERROR": "#E74C3C",
    "MUTED": "#95A5A6",       # grey text
    "BORDER": "#DDE3EA",
    "TEXT": "#2C3E50",        # main text
    "TEXT_SEC": "#606F7B",    # secondary text
    "TAB_HOVER": "#EBF2FB",
    "TASK_BAR": "#8E44AD",    # second progress bar
    "ON_ACCENT": "#FFFFFE",   # text on coloured buttons (not #FFFFFF: that is PANEL)
    "DANGER_ACTIVE": "#C0392B",
    "SELECT_BG": "#D5E8F5",
    "RETRY_BG": "#FAFAFA",
    "LINE_QUEUED_BG": "#F4F6F8",
    "LINE_ACTIVE_BG": "#D6EAF8",
    "LINE_ACTIVE_FG": "#154360",
    "LINE_DONE_BG": "#E8F8F5",
    "LINE_DONE_FG": "#145A32",
    "LINE_FAIL_BG": "#FADBD8",
    "LINE_FAIL_FG": "#78281F",
    "OK_BANNER": "#1A6B3A",
    "WARN_BANNER": "#B8580A",
}

DARK: dict[str, str] = {
    "BG": "#1C2026",
    "PANEL": "#262B32",
    "ACCENT": "#5A9FE6",
    "ACCENT2": "#3F86CF",
    "SUCCESS": "#3CC47C",
    "WARNING": "#F0973A",
    "ERROR": "#F0605A",
    "MUTED": "#7E8A96",
    "BORDER": "#3A414B",
    "TEXT": "#E3E8EE",
    "TEXT_SEC": "#A9B4BF",
    "TAB_HOVER": "#2F3742",
    "TASK_BAR": "#A86BD1",
    "ON_ACCENT": "#FFFFFF",
    "DANGER_ACTIVE": "#C94741",
    "SELECT_BG": "#2D4A6B",
    "RETRY_BG": "#22272D",
    "LINE_QUEUED_BG": "#2B3038",
    "LINE_ACTIVE_BG": "#1F3B57",
    "LINE_ACTIVE_FG": "#BFDDF7",
    "LINE_DONE_BG": "#1E3D30",
    "LINE_DONE_FG": "#A8E6C4",
    "LINE_FAIL_BG": "#4A2525",
    "LINE_FAIL_FG": "#F5B7B1",
    "OK_BANNER": "#57D98F",
    "WARN_BANNER": "#F5A65B",
}

PALETTES = {THEME_LIGHT: LIGHT, THEME_DARK: DARK}

for _name, _palette in PALETTES.items():
    _values = [v.lower() for v in _palette.values()]
    assert len(_values) == len(set(_values)), f"duplicate colour in the {_name} palette"
assert LIGHT.keys() == DARK.keys()

# Colour options of tk widgets that the theme switch updates
_COLOR_OPTIONS = (
    "background", "foreground", "activebackground", "activeforeground",
    "disabledforeground", "highlightbackground", "highlightcolor", "insertbackground",
    "selectbackground", "selectforeground", "troughcolor", "selectcolor",
    "fieldbackground", "readonlybackground",
)
_TAG_OPTIONS = ("background", "foreground", "selectbackground", "selectforeground")


# ---------------------------------------------------------------- fonts

def font_families() -> tuple[str, str]:
    """(UI font, monospace font) that look native on this system."""
    if sys.platform.startswith("win"):
        return "Segoe UI", "Consolas"
    if sys.platform == "darwin":
        return "Helvetica Neue", "Menlo"
    return "DejaVu Sans", "DejaVu Sans Mono"


# ---------------------------------------------------------------- system theme

def _run(cmd: list[str]) -> str:
    """Output of a short command ('' on any error)."""
    try:
        result = subprocess.run(  # noqa: S603 - fixed commands, no user input
            cmd, capture_output=True, text=True, timeout=2, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return (result.stdout or "").strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def system_prefers_dark() -> bool:
    """True when the operating system is set to a dark theme (False if unknown)."""
    if sys.platform.startswith("win"):
        try:
            import winreg  # pylint: disable=import-outside-toplevel,import-error

            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
            )
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            winreg.CloseKey(key)
            return int(value) == 0
        except OSError:
            return False
    if sys.platform == "darwin":
        return _run(["defaults", "read", "-g", "AppleInterfaceStyle"]).lower() == "dark"

    # Linux / BSD desktops
    if "dark" in os.environ.get("GTK_THEME", "").lower():
        return True
    scheme = _run(["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"])
    if "dark" in scheme.lower():
        return True
    if "light" in scheme.lower() or "default" in scheme.lower():
        gtk = _run(["gsettings", "get", "org.gnome.desktop.interface", "gtk-theme"])
        return "dark" in gtk.lower()
    kde = Path.home() / ".config" / "kdeglobals"
    try:
        for line in kde.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.strip().lower().startswith("colorscheme="):
                return "dark" in line.lower()
    except OSError:
        pass
    gtk = _run(["gsettings", "get", "org.gnome.desktop.interface", "gtk-theme"])
    return "dark" in gtk.lower()


def resolve(choice: str) -> str:
    """'light' or 'dark' for a menu choice (auto follows the system)."""
    if choice == THEME_DARK:
        return THEME_DARK
    if choice == THEME_LIGHT:
        return THEME_LIGHT
    return THEME_DARK if system_prefers_dark() else THEME_LIGHT


# ---------------------------------------------------------------- applying

def color_map(old: str, new: str) -> dict[str, str]:
    """Lower-case colour of the old palette -> colour of the new palette."""
    old_p, new_p = PALETTES[old], PALETTES[new]
    mapping = {old_p[name].lower(): new_p[name] for name in old_p}
    mapping["white"] = new_p["ON_ACCENT"]  # literal "white" was used for text on buttons
    return mapping


def recolor_tree(root, mapping: dict[str, str]) -> None:
    """Swap every palette colour of root and all its child widgets (and Text tags)."""
    stack = [root]
    while stack:
        widget = stack.pop()
        stack.extend(widget.winfo_children())
        try:
            options = widget.configure() or {}
        except Exception:  # pylint: disable=broad-except
            options = {}
        changes = {}
        for option in _COLOR_OPTIONS:
            if option not in options:
                continue
            try:
                current = str(widget.cget(option)).lower()
            except Exception:  # pylint: disable=broad-except
                continue
            if current in mapping:
                changes[option] = mapping[current]
        if changes:
            try:
                widget.configure(**changes)
            except Exception:  # pylint: disable=broad-except
                pass
        _recolor_tags(widget, mapping)
        _recolor_menu_items(widget, mapping)


def _recolor_tags(widget, mapping: dict[str, str]) -> None:
    """Text widgets: recolour the tags too (coloured log lines, batch line states)."""
    if widget.winfo_class() != "Text":
        return
    for tag in widget.tag_names():
        changes = {}
        for option in _TAG_OPTIONS:
            try:
                current = str(widget.tag_cget(tag, option)).lower()
            except Exception:  # pylint: disable=broad-except
                continue
            if current in mapping:
                changes[option] = mapping[current]
        if changes:
            widget.tag_configure(tag, **changes)


def _recolor_menu_items(widget, mapping: dict[str, str]) -> None:
    """Menus: entries may carry their own colours."""
    if widget.winfo_class() != "Menu":
        return
    try:
        last = widget.index("end")
    except Exception:  # pylint: disable=broad-except
        return
    if last is None:
        return
    for index in range(int(last) + 1):
        for option in ("background", "foreground", "activebackground", "activeforeground", "selectcolor"):
            try:
                current = str(widget.entrycget(index, option)).lower()
            except Exception:  # pylint: disable=broad-except
                continue
            if current in mapping:
                try:
                    widget.entryconfigure(index, **{option: mapping[current]})
                except Exception:  # pylint: disable=broad-except
                    pass


def set_windows_title_bar(window, dark: bool) -> None:
    """Windows 10/11: dark or light title bar for a Tk window (no effect elsewhere)."""
    if not sys.platform.startswith("win"):
        return
    try:
        import ctypes  # pylint: disable=import-outside-toplevel

        window.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
        value = ctypes.c_int(1 if dark else 0)
        for attribute in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (new / old builds)
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, attribute, ctypes.byref(value), ctypes.sizeof(value),
            ) == 0:
                break
    except Exception:  # pylint: disable=broad-except
        pass
