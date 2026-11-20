"""System tray icon (desktop): minimize the window to the tray and keep downloading in the background.

The icon is drawn by pystray (Windows notification area, Linux AppIndicator / XEmbed tray).
pystray runs its own thread: its menu callbacks never touch Tk, they put ("TRAY", action)
messages in the GUI queue, handled by EHentaiGUI._poll_queue on the GUI thread.

macOS: pystray and Tk both need the main thread there, so the tray is not used: the window
is minimized to the Dock as usual (downloads go on the same way).
When pystray is missing or the desktop has no tray, minimizing works as it always did.
"""

# pylint: disable=protected-access
from __future__ import annotations

import logging
import os
import sys
import threading

from src import paths, throttle
from src.gui.style import ts as _ts

TRAY_REFRESH_MS = 2000      # tooltip refresh while the window is hidden
TOOLTIP_MAX = 120           # Windows truncates tooltips at 127 characters
APP_NAME = "EHentai Downloader"


_PYSTRAY: list = []          # [module] once loaded, [None] when it can't be used


def _load_pystray():
    """Import pystray once (None when missing or without a working backend).

    On Linux pystray tries AppIndicator, then GTK: when PyGObject is installed without the GTK
    libraries that import fails, so the plain X11 backend (XEmbed tray) is tried next.
    """
    if _PYSTRAY:
        return _PYSTRAY[0]
    module = None
    if sys.platform != "darwin":
        try:
            import pystray  # pylint: disable=import-outside-toplevel
            module = pystray
        except Exception:  # pylint: disable=broad-except  # no backend on this desktop raises too
            if sys.platform.startswith("linux") and not os.environ.get("PYSTRAY_BACKEND"):
                for name in [m for m in sys.modules if m == "pystray" or m.startswith("pystray.")]:
                    del sys.modules[name]
                os.environ["PYSTRAY_BACKEND"] = "xorg"
                try:
                    import pystray  # pylint: disable=import-outside-toplevel,reimported
                    module = pystray
                except Exception:  # pylint: disable=broad-except
                    module = None
    _PYSTRAY.append(module)
    return module


def _x11_tray_owner() -> bool:
    """X11: True when a system tray (XEmbed) owns the _NET_SYSTEM_TRAY_S<screen> selection."""
    from Xlib import display as xdisplay  # pylint: disable=import-outside-toplevel

    disp = xdisplay.Display()
    try:
        atom = disp.intern_atom(f"_NET_SYSTEM_TRAY_S{disp.get_default_screen()}")
        return disp.get_selection_owner(atom) != 0
    finally:
        disp.close()


def _status_notifier_watcher() -> bool:
    """Linux AppIndicator: True when a StatusNotifier host runs on the session bus."""
    from gi.repository import Gio, GLib  # pylint: disable=import-outside-toplevel,import-error  # optional (PyGObject)

    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    reply = bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                          "NameHasOwner", GLib.Variant("(s)", ("org.kde.StatusNotifierWatcher",)),
                          GLib.VariantType("(b)"), Gio.DBusCallFlags.NONE, 1000, None)
    return bool(reply.unpack()[0])


def tray_area_available() -> bool:
    """True when the desktop really shows tray icons right now.

    Without this check a Linux desktop with no tray (e.g. GNOME without the AppIndicator
    extension) would hide the window behind an icon nobody can see.
    """
    pystray = _load_pystray()
    if pystray is None:
        return False
    if not sys.platform.startswith("linux"):
        return True
    backend = pystray.Icon.__module__.rsplit(".", 1)[-1]
    try:
        if backend == "_appindicator":
            return _status_notifier_watcher()
        return _x11_tray_owner()
    except Exception:  # pylint: disable=broad-except  # no X display / bus: assume no tray
        return False


def tray_supported() -> bool:
    """True when this system can show a tray icon (pystray usable, not macOS)."""
    return _load_pystray() is not None


class TrayMixin:  # pylint: disable=too-few-public-methods,no-member
    """Minimize to the system tray. Mixed into EHentaiGUI."""

    def _tray_init(self) -> None:
        """Called once from __init__, after the menu is built."""
        self._tray_icon = None
        self._tray_lock = threading.Lock()
        self._tray_hidden = False            # the window is hidden in the tray
        self._tray_failed = False            # the icon could not be shown: stop trying
        self._tray_was_running = False       # a download was running when last checked
        self._tray_job = None
        self._tray_area_warned = False       # "no system tray" written in the log once
        self.bind("<Unmap>", self._on_unmap, add="+")

    # ---- settings -------------------------------------------------------------------------
    def _tray_enabled(self) -> bool:
        """Settings → Minimize to system tray is on and the tray can be used here."""
        var = getattr(self, "_minimize_to_tray_var", None)
        return bool(var is not None and var.get() and not self._tray_failed and tray_supported())

    def _on_minimize_to_tray_changed(self) -> None:
        """Settings → Minimize to system tray toggled."""
        self._save_settings()
        if self._minimize_to_tray_var.get():
            if tray_supported():
                self._append_log(_ts(), "Settings",
                                 "Minimize to system tray ON: the minimized window goes to the tray "
                                 "(next to the clock) and the downloads go on in the background.")
            else:
                self._append_log(_ts(), "Settings",
                                 "Minimize to system tray: not available here (macOS, or the pystray "
                                 "package / a system tray is missing); the window is minimized as usual.")
        else:
            self._append_log(_ts(), "Settings", "Minimize to system tray OFF: the window is minimized "
                                                "to the taskbar.")

    # ---- hide / show ------------------------------------------------------------------------
    def _on_unmap(self, event) -> None:
        """The window was minimized: hide it in the tray when the setting is on."""
        if event.widget is not self or self._tray_hidden or getattr(self, "_closed", False):
            return
        if not self._tray_enabled():
            return
        # state() is "iconic" only for a real minimize (withdraw() also sends <Unmap>)
        self.after(10, self._hide_if_iconic)

    def _hide_if_iconic(self) -> None:
        try:
            if self.state() == "iconic":
                self._hide_to_tray()
        except Exception:  # pylint: disable=broad-except  # window being destroyed
            pass

    def _hide_to_tray(self, reason: str = "") -> bool:
        """Hide the window and show the tray icon. False if the icon could not be shown."""
        if not tray_area_available():
            if not self._tray_area_warned:
                self._tray_area_warned = True
                self._append_log(_ts(), "Tray", "No system tray found on this desktop: the window is "
                                                "minimized as usual (the downloads go on).")
            return False
        if not self._tray_show_icon():
            return False
        self._tray_hidden = True
        self._tray_was_running = bool(self._running)
        self.withdraw()
        self._append_log(_ts(), "Tray", reason or "Window hidden in the system tray: the downloads go on. "
                                                  "Click the tray icon to show it again.")
        self._tray_tick()
        return True

    def _show_from_tray(self) -> None:
        """Bring the window back (GUI thread)."""
        self._tray_hidden = False
        if self._tray_job is not None:
            self.after_cancel(self._tray_job)
            self._tray_job = None
        self._tray_hide_icon()
        self.deiconify()
        self.state("normal")
        self.lift()
        try:
            self.focus_force()
        except Exception:  # pylint: disable=broad-except
            pass

    # ---- the icon ---------------------------------------------------------------------------
    def _tray_menu(self):
        pystray = _load_pystray()
        post = self._tray_post
        item = pystray.MenuItem
        return pystray.Menu(
            item(f"Show {APP_NAME}", post("show"), default=True),
            pystray.Menu.SEPARATOR,
            item(lambda _i: "Resume downloads" if throttle.is_paused() else "Pause downloads",
                 post("pause"), enabled=lambda _i: bool(self._running)),
            item("Open Downloads", post("downloads")),
            pystray.Menu.SEPARATOR,
            item("Quit", post("quit")),
        )

    def _tray_post(self, action: str):
        """Menu callback (tray thread): hand the action to the GUI thread."""
        return lambda _icon=None, _item=None: self._q.put(("TRAY", action))

    def _tray_show_icon(self) -> bool:
        """Create (first time) and show the tray icon. False when the tray cannot be used."""
        with self._tray_lock:
            try:
                if self._tray_icon is None:
                    pystray = _load_pystray()
                    from PIL import Image  # pylint: disable=import-outside-toplevel

                    image = Image.open(paths.resource("assets/icon.png"))
                    image.load()
                    icon = pystray.Icon("EHentaiDownloader", image, APP_NAME, self._tray_menu())
                    self._tray_icon = icon
                    # Own daemon thread instead of run_detached(): with the X11 backend
                    # run_detached() leaves a thread behind that keeps the program from exiting.
                    threading.Thread(target=self._tray_run, args=(icon,), name="tray", daemon=True).start()
                else:
                    self._tray_icon.visible = True   # the first time run() shows it when ready
                return True
            except Exception as err:  # pylint: disable=broad-except  # no tray on this desktop
                self._tray_failed = True
                self._tray_icon = None
                logging.warning(f"System tray not available ({err}): the window is minimized as usual.")
                return False

    def _tray_run(self, icon) -> None:
        """Tray thread: event loop of the icon. A failure brings the window back."""
        try:
            icon.run()
        except Exception as err:  # pylint: disable=broad-except  # no tray on this desktop
            logging.warning(f"System tray not available ({err}): the window is minimized as usual.")
            self._q.put(("TRAY", "failed"))

    def _tray_hide_icon(self) -> None:
        with self._tray_lock:
            if self._tray_icon is not None:
                try:
                    self._tray_icon.visible = False
                except Exception:  # pylint: disable=broad-except
                    pass

    def _tray_stop(self) -> None:
        """Remove the icon for good (program closing)."""
        with self._tray_lock:
            icon, self._tray_icon = self._tray_icon, None
        if icon is not None:
            try:
                icon.visible = False
                icon.stop()
            except Exception:  # pylint: disable=broad-except
                pass

    def _tray_notify(self, message: str) -> None:
        """Balloon / desktop notification (ignored where the backend has none)."""
        icon = self._tray_icon
        if icon is None:
            return
        try:
            if getattr(icon, "HAS_NOTIFICATION", True):
                icon.notify(message, APP_NAME)
        except Exception:  # pylint: disable=broad-except
            pass

    def _tray_tick(self) -> None:
        """While hidden: tooltip with the current status; a notification when the downloads end."""
        self._tray_job = None
        if not self._tray_hidden or getattr(self, "_closed", False):
            return
        status = self._status_var.get().strip() if hasattr(self, "_status_var") else ""
        if self._running:
            state = "paused" if throttle.is_paused() else "downloading"
            text = f"{APP_NAME} – {state}" + (f": {status}" if status else "")
        else:
            text = f"{APP_NAME} – idle"
        icon = self._tray_icon
        if icon is not None:
            try:
                icon.title = text[:TOOLTIP_MAX]
                icon.update_menu()
            except Exception:  # pylint: disable=broad-except
                pass
        if self._tray_was_running and not self._running:
            self._tray_notify("Downloads finished. " + (status[:180] if status else ""))
        self._tray_was_running = bool(self._running)
        self._tray_job = self.after(TRAY_REFRESH_MS, self._tray_tick)

    def _on_tray_action(self, action: str) -> None:
        """GUI thread: a tray menu entry was chosen."""
        if action == "failed":
            self._tray_failed = True
            with self._tray_lock:
                self._tray_icon = None
            if self._tray_hidden:
                self._show_from_tray()
        elif action == "show":
            self._show_from_tray()
        elif action == "pause":
            self._toggle_pause()
            if self._tray_icon is not None:
                try:
                    self._tray_icon.update_menu()
                except Exception:  # pylint: disable=broad-except
                    pass
        elif action == "downloads":
            self._open_downloads_folder()
        elif action == "quit":
            self._show_from_tray()   # the close question (if any) needs the window
            self._on_window_close()
