"""EHentai Downloader – Graphical User Interface (launcher).

Double-click this file on Windows to start the program without a console window
(.pyw files are opened by pythonw.exe). It also works from a terminal:

    python EHentaiDownloader.pyw

The window wraps the three command-line modes of the project:
  • Single Download  – one gallery URL            (same logic as src/cli/downloader.py)
  • Batch Download   – every URL in URLs.txt      (same logic as src/cli/main.py)
  • Retry Failed     – albums in failed_downloads.txt (same logic as src/cli/retry.py)

How it works:
  - Only one copy runs at a time (src/gui/single_instance.py): starting it again brings the
    open window to the front (also from the system tray).
  - Downloads run in a background thread, so the window never freezes.
  - GUILiveManager replaces the Rich terminal display used by the CLI: instead of
    drawing in a terminal, it pushes progress/log messages onto a thread-safe queue.
  - The window polls that queue every 100 ms (_poll_queue) and updates the widgets.
"""


from __future__ import annotations

from src import paths
from src.gui.app import EHentaiGUI
from src.gui.single_instance import SingleInstance, show_already_running


def main() -> None:
    """Start the GUI from the project folder so relative paths (Downloads, URLs.txt, ...) resolve correctly."""
    # (or, for the packaged app, next to the executable / in ~/EHentaiDownloader: see src/paths.py)
    folder = paths.use_app_dir()

    # Only one copy at a time: a second start brings the open window back and closes itself
    instance = SingleInstance(folder)
    if not instance.acquire():
        if not instance.notify_running():
            show_already_running()
        return

    try:
        app = EHentaiGUI()
        instance.listen(app.request_show)
        app.update_idletasks()
        if not app.show_startup_notice():
            app._closed = True  # pylint: disable=protected-access
            app.destroy()
            return
        app.after(1500, app.check_for_updates)  # at most once a day, if enabled in Help
        app.mainloop()
    finally:
        instance.release()


if __name__ == "__main__":
    main()
