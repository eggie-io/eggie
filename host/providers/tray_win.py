"""The Windows notification-area icon."""
from __future__ import annotations

import sys
import threading
from pathlib import Path


def _guarded(callback):
    def run():
        try:
            callback()
        except Exception as e:
            print(f"tray callback failed: {e!r}", file=sys.stderr)
    return run


class WinTray:
    def __init__(self, *, icon: Path, on_open, on_settings, on_quit):
        self._paths = icon
        self._callbacks = (on_open, on_settings, on_quit)
        self._icon = None
        self._visible = threading.Event()

    def start(self) -> None:
        import pystray
        from PIL import Image

        on_open, on_settings, on_quit = self._callbacks
        menu = pystray.Menu(
            # default=True is what a left-click on the icon runs.
            pystray.MenuItem("Open Omelet", _guarded(on_open), default=True),
            pystray.MenuItem("Settings", _guarded(on_settings)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit Omelet", _guarded(on_quit)),
        )
        self._icon = pystray.Icon("Omelet", Image.open(self._paths), "Omelet", menu)
        # The Win32 backend runs its own message loop, so it can live off the
        # main thread that webview.start() owns.
        threading.Thread(target=self._icon.run, kwargs={"setup": self._setup},
                         daemon=True, name="omelet-tray").start()

    def _setup(self, icon) -> None:
        icon.visible = True
        self._visible.set()

    def stop(self) -> None:
        icon = self._icon
        if icon is None:
            return
        # stop() only posts a message; the icon is removed on the tray thread,
        # which may never get to it if the process exits first.
        for step in (lambda: setattr(icon, "visible", False), icon.stop):
            try:
                step()
            except Exception as e:
                print(f"tray stop failed: {e!r}", file=sys.stderr)

    def notify(self, text: str) -> bool:
        # Shell_NotifyIcon fails silently until the icon has been added.
        if self._icon is None or not self._visible.wait(2):
            return False
        self._icon.notify(text, "Omelet")
        return True
