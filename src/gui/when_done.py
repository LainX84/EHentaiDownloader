"""End of the downloads: summary, 'When done' action (close / shut down) and exit."""

# pylint: disable=protected-access,too-many-lines
from __future__ import annotations

import os
import subprocess
import sys
from tkinter import (
    END,
    StringVar,
    messagebox,
)
import tkinter as tk

from src import throttle
from src.file_utils import append_session_banner

from src.gui import style
from src.gui.style import (
    FONT_UI, PAD, WHEN_DONE_COUNTDOWN, WHEN_DONE_CLOSE, WHEN_DONE_NOTHING, WHEN_DONE_SHUTDOWN, btn as _btn,
    ts as _ts,
)


class WhenDoneMixin:  # pylint: disable=too-few-public-methods,no-member
    """End of the downloads: summary, 'When done' action (close / shut down) and exit. Mixed into EHentaiGUI."""

    def _on_download_done(self, success: bool, failed: dict[str, list[str]] | list[str]) -> None:
        """Re-enable the UI and print a success or failure summary in the log."""
        self._running = False
        self._set_buttons_state("normal")
        throttle.set_paused(False)
        self._pause_btn.configure(state="disabled", text="⏸  Pause")
        if success:
            self._bar1["value"] = 100
            self._bar1_pct.configure(text="100%")
            self._bar2["value"] = 100
            self._bar2_pct.configure(text="100%")
        self._log_box.configure(state="normal")

        sep = "─" * 60
        ts = _ts()

        # Normalize failed into a dict[str, list[str]]
        if isinstance(failed, dict):
            failed_map = failed
        elif isinstance(failed, list):
            failed_map = {"": failed} if failed else {}
        else:
            failed_map = {}

        from src.cli.downloader import gallery_name
        from src.general_utils import validate_gallery_url

        def is_image_item(item: str) -> bool:
            """True for a missing image (picture page URL or '(page NN)'), False for an album error."""
            return "/s/" in item or "(page " in item

        failed_albums = [url for url in failed_map if url]
        missing_images = sum(
            1 for items in failed_map.values() for item in (items or []) if is_image_item(item)
        )
        total_failed_items = len(failed_albums) or sum(len(items) or 1 for items in failed_map.values())

        if success and not failed_map:
            append_session_banner("DOWNLOAD COMPLETED SUCCESSFULLY — no missing files.")
            # ── Green banner ──────────────────────────────────────────
            self._log_box.insert(END, f"\n[{ts}] {sep}\n", "success_banner")
            self._log_box.insert(END,
                f"[{ts}] ✔  DOWNLOAD COMPLETED SUCCESSFULLY — no missing files.\n",
                "success_banner",
            )
            self._log_box.insert(END, f"[{ts}] {sep}\n", "success_banner")
            self._status_var.set("✔ Download completed successfully.")
        else:
            n = total_failed_items
            gal_word = "gallery" if n == 1 else "galleries"
            img_part = f", {missing_images} image(s) missing" if missing_images else ""
            append_session_banner(f"DOWNLOAD NOT COMPLETE — {n} {gal_word} with problems{img_part}")

            # ── Orange banner ─────────────────────────────────────────
            self._log_box.insert(END, f"\n[{ts}] {sep}\n", "warn_banner")
            self._log_box.insert(END,
                f"[{ts}] ⚠  DOWNLOAD NOT COMPLETE — {n} {gal_word} with problems{img_part}:\n",
                "warn_banner",
            )
            for album_url, items in failed_map.items():
                if album_url:
                    # Gallery name first (when known), then its link
                    title = gallery_name(album_url)
                    label = f"{title}  —  {album_url}" if title else album_url
                    self._log_box.insert(END, f"[{ts}] 🔗 {label}\n", "warn_url")
                image_items = [item for item in (items or []) if is_image_item(item)]
                if album_url and validate_gallery_url(album_url):
                    self._log_box.insert(
                        END,
                        f"[{ts}]    • Not a valid gallery link — nothing was downloaded "
                        "(check that line of the list)\n",
                        "warn_item",
                    )
                elif not image_items:
                    self._log_box.insert(
                        END,
                        f"[{ts}]    • Gallery not downloaded: its page could not be read "
                        "(the reason is in the log above)\n",
                        "warn_item",
                    )
                else:
                    for item in image_items:
                        # Show only the last meaningful segment (filename or page URL)
                        label = item.split("/")[-1].strip() or item
                        self._log_box.insert(END, f"[{ts}]    • missing image: {label}\n", "warn_item")
            self._log_box.insert(END, f"[{ts}] {sep}\n", "warn_banner")
            self._log_box.insert(END,
                f"[{ts}] Use 'Retry Failed' to retry the missing files.\n",
                "warn_banner",
            )
            self._status_var.set(
                f"⚠ Download not complete: {n} {gal_word} with problems{img_part} — use Retry Failed."
            )

        self._log_box.see(END)
        self._log_box.configure(state="disabled")

        if self._when_done_var.get() != WHEN_DONE_NOTHING:
            self.after(500, self._start_when_done_countdown)

    def _on_when_done_changed(self, _event=None) -> None:
        """Confirm the choice in the log."""
        choice = self._when_done_var.get()
        if hasattr(self, "_when_done_cb"):
            self._when_done_cb.selection_clear()
        if choice == WHEN_DONE_NOTHING:
            self._append_log(_ts(), "When done", "Nothing will happen when the downloads finish.")
            return
        when = "when the current downloads finish" if self._running else "when the next download finishes"
        action = "The program will close" if choice == WHEN_DONE_CLOSE else "The PC will shut down"
        self._append_log(
            _ts(), "When done",
            f"{action} {when} (you get {WHEN_DONE_COUNTDOWN} s to cancel).",
        )

    def _start_when_done_countdown(self) -> None:
        """Show a cancellable countdown, then close the program or shut down the PC."""
        if self._running or self._when_done_dialog is not None:
            return  # a new download was started in the meantime
        choice = self._when_done_var.get()
        if choice == WHEN_DONE_NOTHING:
            return
        action = "Closing the program" if choice == WHEN_DONE_CLOSE else "Shutting down the PC"
        append_session_banner(f"WHEN DONE: {choice.upper()} (countdown {WHEN_DONE_COUNTDOWN}s)")

        dlg = tk.Toplevel(self)
        self._when_done_dialog = dlg
        dlg.title("Downloads finished")
        dlg.configure(bg=style.PANEL)
        dlg.resizable(False, False)
        dlg.transient(self)
        dlg.attributes("-topmost", True)
        msg_var = StringVar()
        tk.Label(dlg, textvariable=msg_var, bg=style.PANEL, fg=style.TEXT, font=FONT_UI,
                 justify="left").pack(padx=PAD * 2, pady=(PAD * 2, PAD))
        btns = tk.Frame(dlg, bg=style.PANEL)
        btns.pack(padx=PAD * 2, pady=(0, PAD * 2), fill="x")
        state = {"left": WHEN_DONE_COUNTDOWN, "job": None}

        def close_dialog() -> None:
            if state["job"] is not None:
                self.after_cancel(state["job"])
                state["job"] = None
            self._when_done_dialog = None
            dlg.destroy()

        def cancel() -> None:
            close_dialog()
            append_session_banner("WHEN DONE: cancelled by the user")
            self._append_log(_ts(), "When done", f"{action} cancelled.")
            self._status_var.set(f"{action} cancelled.")

        def do_it() -> None:
            close_dialog()
            self._execute_when_done(choice)

        def tick() -> None:
            if state["left"] <= 0:
                do_it()
                return
            msg_var.set(
                f"All downloads are finished.\n\n{action} in {state['left']} s…\n"
                "The lists and settings are saved first."
            )
            state["left"] -= 1
            state["job"] = self.after(1000, tick)

        _btn(btns, "Cancel", cancel, style="Ghost.TButton").pack(side="right")
        now_text = "Close now" if choice == WHEN_DONE_CLOSE else "Shut down now"
        _btn(btns, now_text, do_it).pack(side="right", padx=(0, 6))
        dlg.protocol("WM_DELETE_WINDOW", cancel)
        tick()
        dlg.update_idletasks()
        x = self.winfo_rootx() + (self.winfo_width() - dlg.winfo_width()) // 2
        y = self.winfo_rooty() + (self.winfo_height() - dlg.winfo_height()) // 3
        dlg.geometry(f"+{max(0, x)}+{max(0, y)}")
        dlg.lift()

    def _save_state_for_exit(self) -> None:
        """Save the batch list (downloaded lines removed now) and the settings."""
        try:
            self._flush_pending_removals()
            self._save_urls_txt(quiet=True)
            self._save_settings()
        except Exception:
            pass

    def _execute_when_done(self, choice: str) -> None:
        """Close the program, or shut down the PC and then close the program."""
        self._save_state_for_exit()
        if choice == WHEN_DONE_SHUTDOWN:
            if os.name == "nt":
                cmd = ["shutdown", "/s", "/t", "0"]
            elif sys.platform == "darwin":
                cmd = ["osascript", "-e", 'tell application "System Events" to shut down']
            else:
                cmd = ["systemctl", "poweroff"]
            try:
                flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
                subprocess.run(cmd, check=True, creationflags=flags, timeout=30)  # noqa: S603
            except (OSError, subprocess.SubprocessError) as err:
                append_session_banner(f"WHEN DONE: shutdown failed ({err})")
                self._append_log(_ts(), "ERROR", f"Could not shut down the PC: {err}")
                self._status_var.set("⚠ Could not shut down the PC (see the log).")
                return
            append_session_banner("APPLICATION CLOSED (PC shutting down after the downloads)")
        else:
            append_session_banner("APPLICATION CLOSED (downloads finished)")
        self._closed = True
        self.destroy()

    def _on_window_close(self) -> None:
        """Handler called when user closes the GUI window."""
        if self._running and throttle.is_paused():
            append_session_banner("APPLICATION CLOSED (download paused, list saved)")
        elif self._running:
            if not messagebox.askyesno(
                "Close Application",
                "A download is in progress. Do you really want to stop it and close the program?",
            ):
                return
            append_session_banner("APPLICATION CLOSED (running download interrupted by the user)")
        else:
            append_session_banner("APPLICATION CLOSED")
        # Keep the list on disk: downloaded lines go away now, everything else is saved
        self._save_state_for_exit()
        self._closed = True
        self.destroy()

    def _on_exit(self) -> None:
        """Fallback hook when the application process exits."""
        if not getattr(self, "_closed", False):
            self._closed = True
            append_session_banner("APPLICATION CLOSED")
