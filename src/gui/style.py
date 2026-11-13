"""Colours, fonts and small widget helpers shared by the GUI modules.

The colour names (BG, PANEL, ...) are module attributes: the GUI reads them as style.BG and
set_palette() swaps them at runtime when the theme changes.
"""

from __future__ import annotations

import datetime
import os
import subprocess
import sys
from pathlib import Path
from tkinter import ttk

from src import ui_theme

BG = ui_theme.LIGHT["BG"]
PANEL = ui_theme.LIGHT["PANEL"]
ACCENT = ui_theme.LIGHT["ACCENT"]
ACCENT2 = ui_theme.LIGHT["ACCENT2"]
SUCCESS = ui_theme.LIGHT["SUCCESS"]
WARNING = ui_theme.LIGHT["WARNING"]
ERROR = ui_theme.LIGHT["ERROR"]
MUTED = ui_theme.LIGHT["MUTED"]
BORDER = ui_theme.LIGHT["BORDER"]
TEXT = ui_theme.LIGHT["TEXT"]
TEXT_SEC = ui_theme.LIGHT["TEXT_SEC"]
TAB_HOVER = ui_theme.LIGHT["TAB_HOVER"]
TASK_BAR = ui_theme.LIGHT["TASK_BAR"]
ON_ACCENT = ui_theme.LIGHT["ON_ACCENT"]
DANGER_ACTIVE = ui_theme.LIGHT["DANGER_ACTIVE"]
SELECT_BG = ui_theme.LIGHT["SELECT_BG"]
RETRY_BG = ui_theme.LIGHT["RETRY_BG"]
LINE_QUEUED_BG = ui_theme.LIGHT["LINE_QUEUED_BG"]
LINE_ACTIVE_BG = ui_theme.LIGHT["LINE_ACTIVE_BG"]
LINE_ACTIVE_FG = ui_theme.LIGHT["LINE_ACTIVE_FG"]
LINE_DONE_BG = ui_theme.LIGHT["LINE_DONE_BG"]
LINE_DONE_FG = ui_theme.LIGHT["LINE_DONE_FG"]
LINE_FAIL_BG = ui_theme.LIGHT["LINE_FAIL_BG"]
LINE_FAIL_FG = ui_theme.LIGHT["LINE_FAIL_FG"]
OK_BANNER = ui_theme.LIGHT["OK_BANNER"]
WARN_BANNER = ui_theme.LIGHT["WARN_BANNER"]

_UI_FAMILY, _MONO_FAMILY = ui_theme.font_families()
FONT_UI    = (_UI_FAMILY,   10)
FONT_SMALL = (_UI_FAMILY,    9)
FONT_MONO  = (_MONO_FAMILY,  9)
FONT_MONO_BOLD = (_MONO_FAMILY, 9, "bold")
FONT_TITLE = (_UI_FAMILY,   16, "bold")
FONT_HEAD  = (_UI_FAMILY,   11, "bold")
FONT_BTN   = (_UI_FAMILY,   10, "bold")
DIALOG_FONTS = {"ui": FONT_UI, "small": FONT_SMALL, "mono": FONT_MONO, "title": FONT_TITLE, "head": FONT_HEAD}

PAD = 12
TAB_WIDTH = 18  # characters: the three tabs are always the same size

# "When done" choices (status bar). Never remembered between runs: a forgotten
# "Shut down PC" must not switch the computer off at the next, unrelated download.
WHEN_DONE_NOTHING = "Do nothing"
WHEN_DONE_CLOSE = "Close program"
WHEN_DONE_SHUTDOWN = "Shut down PC"
WHEN_DONE_OPTIONS = (WHEN_DONE_NOTHING, WHEN_DONE_CLOSE, WHEN_DONE_SHUTDOWN)
WHEN_DONE_COUNTDOWN = 60  # seconds to cancel before closing / shutting down


def set_palette(theme: str) -> None:
    """Make the colours of the given theme the current ones (used by widgets created from now on)."""
    globals().update(ui_theme.PALETTES[theme])


def card(parent, **kw) -> ttk.Frame:
    """Return a white rounded-looking card frame."""
    f = ttk.Frame(parent, style="Card.TFrame", **kw)
    return f


def btn(parent, text: str, command, style="Accent.TButton", **kw) -> ttk.Button:
    """Create a themed ttk button (blue 'Accent' style by default)."""
    return ttk.Button(parent, text=text, command=command, style=style, **kw)


def ts() -> str:
    """Return the current time as HH:MM:SS for log lines."""
    return datetime.datetime.now().strftime("%H:%M:%S")


def open_with_system(path: Path | str) -> None:
    """Open a file or folder with the default application (Explorer, Finder, xdg-open)."""
    target = str(path)
    if os.name == "nt":
        os.startfile(target)  # pylint: disable=no-member
    elif sys.platform == "darwin":
        subprocess.Popen(["open", target])  # pylint: disable=consider-using-with
    else:
        subprocess.Popen(["xdg-open", target])  # pylint: disable=consider-using-with
