"""'Check for updates': ask GitHub (in a background thread) whether a newer release exists.

At start-up the check runs at most once a day and only when Help → "Check for updates at
start-up" is on; it says nothing when there is no news or GitHub can't be reached.
Help → "Check for updates now" always answers.
"""

from __future__ import annotations

import threading
import time
from tkinter import messagebox

from src import update_check
from src.config import APP_VERSION
from src import gui_dialogs
from src.gui.style import ts as _ts

UPDATE_CHECK_INTERVAL = 24 * 3600   # seconds between automatic checks


class UpdatesMixin:  # pylint: disable=too-few-public-methods,no-member
    """Update check. Mixed into EHentaiGUI."""

    def check_for_updates(self, manual: bool = False) -> None:
        """Check GitHub for a newer release (manual: always, and always report the result)."""
        if not manual:
            if not self._check_updates_var.get():
                return
            last = float(self._settings.get("update_last_check", 0) or 0)
            if time.time() - last < UPDATE_CHECK_INTERVAL:
                return
        self._settings["update_last_check"] = time.time()
        self._save_settings()
        if manual:
            self._status_var.set("Checking for updates…")

        def worker() -> None:
            try:
                result = update_check.latest_release()
                self._q.put(("UPDATE", manual, result, ""))
            except Exception as err:
                self._q.put(("UPDATE", manual, None, str(err)))

        threading.Thread(target=worker, daemon=True, name="update-check").start()

    def _on_update_result(self, manual: bool, result: dict | None, error: str) -> None:
        """Show the answer of the update check (called in the GUI thread)."""
        if result is None:
            self._append_log(_ts(), "Update", f"Could not check for updates: {error}")
            if manual:
                messagebox.showwarning("Check for updates", f"Could not reach GitHub:\n{error}")
            return
        if not result["newer"]:
            if manual:
                self._status_var.set("No updates.")
                messagebox.showinfo("Check for updates",
                                    f"You have the latest version ({APP_VERSION}).")
            return
        version, url = result["version"], result["url"]
        self._append_log(_ts(), "Update", f"Version {version} is available (you have {APP_VERSION}): {url}")
        self._status_var.set(f"Version {version} is available.")
        if self._running and not manual:
            return  # don't interrupt a download with a dialog: the log line is enough
        if messagebox.askyesno(
            "Update available",
            f"Version {version} of EHentai Downloader is available (you have {APP_VERSION}).\n\n"
            "Open the download page?",
        ):
            gui_dialogs.open_url(url)
