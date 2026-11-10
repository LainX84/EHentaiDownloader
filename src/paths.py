"""Where the program keeps its files, from the source code or as a packaged app.

* From the source code (python EHentaiDownloader.pyw): the project folder, as always.
* Packaged with PyInstaller (the .exe / .app / Linux binary of the GitHub releases):
    Windows, Linux  the folder that contains the executable ("portable" use: unzip it
                    anywhere and Downloads/, URLs.txt, cookies.json are created next
                    to it); if that folder can't be written (e.g. Program Files),
                    ~/EHentaiDownloader is used instead.
    macOS           ~/EHentaiDownloader (an .app must not write inside itself).

Read-only files bundled in the app (LICENSE, icon) are found with resource().
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

APP_FOLDER_NAME = "EHentaiDownloader"
_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def is_frozen() -> bool:
    """True when running as a packaged app (PyInstaller)."""
    return bool(getattr(sys, "frozen", False))


def _writable(folder: Path) -> bool:
    """True when files can be created in the folder."""
    try:
        folder.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=folder, prefix=".write_test_", delete=True):
            pass
        return True
    except OSError:
        return False


def app_dir() -> Path:
    """Folder of Downloads/, temp/, URLs.txt, cookies.json, settings.json, session.log."""
    if not is_frozen():
        return _PROJECT_ROOT
    home_folder = Path.home() / APP_FOLDER_NAME
    if sys.platform == "darwin":
        folder = home_folder
    else:
        folder = Path(sys.executable).resolve().parent
        if not _writable(folder):
            folder = home_folder
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def resource(relative: str) -> Path:
    """Path of a file shipped with the program (inside the app when packaged)."""
    base = Path(getattr(sys, "_MEIPASS", _PROJECT_ROOT))
    return base / relative


def use_app_dir() -> Path:
    """Make the data folder the current directory (all paths in config are relative)."""
    folder = app_dir()
    os.chdir(folder)
    return folder
