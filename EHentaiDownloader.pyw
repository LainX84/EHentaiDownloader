"""EHentai Downloader – Graphical User Interface (launcher).

Double-click this file on Windows to start the program without a console window
(.pyw files are opened by pythonw.exe). It also works from a terminal:

    python EHentaiDownloader.pyw

The window wraps the three command-line modes of the project:
  • Single Download  – one gallery URL            (same logic as src/cli/downloader.py)
  • Batch Download   – every URL in URLs.txt      (same logic as src/cli/main.py)
  • Retry Failed     – albums in failed_downloads.txt (same logic as src/cli/retry.py)

How it works:
  - Downloads run in a background thread, so the window never freezes.
  - GUILiveManager replaces the Rich terminal display used by the CLI: instead of
    drawing in a terminal, it pushes progress/log messages onto a thread-safe queue.
  - The window polls that queue every 100 ms (_poll_queue) and updates the widgets.
"""


from __future__ import annotations

from src import paths
from src.gui.app import EHentaiGUI


def main() -> None:
    """Start the GUI from the project folder so relative paths (Downloads, URLs.txt, ...) resolve correctly."""
    # (or, for the packaged app, next to the executable / in ~/EHentaiDownloader: see src/paths.py)
    paths.use_app_dir()

    app = EHentaiGUI()
    app.update_idletasks()
    if not app.show_startup_notice():
        app._closed = True  # pylint: disable=protected-access
        app.destroy()
        return
    app.after(1500, app.check_for_updates)  # at most once a day, if enabled in Help
    app.mainloop()


if __name__ == "__main__":
    main()
