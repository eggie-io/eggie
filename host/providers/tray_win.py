"""The Windows notification-area icon."""
from __future__ import annotations

import threading
from pathlib import Path


class WinTray:
    def __init__(self, *, icon: Path, on_open, on_settings, on_quit):
        self._paths = icon
        self._callbacks = (on_open, on_settings, on_quit)
        self._icon = None

    def start(self) -> None:
        import pystray
        from PIL import Image

        on_open, on_settings, on_quit = self._callbacks
        menu = pystray.Menu(
            # default=True is what a left-click on the icon runs.
            pystray.MenuItem("Open Omelet", lambda: on_open(), default=True),
            pystray.MenuItem("Settings", lambda: on_settings()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit Omelet", lambda: on_quit()),
        )
        self._icon = pystray.Icon("Omelet", Image.open(self._paths), "Omelet", menu)
        # The Win32 backend runs its own message loop, so it can live off the
        # main thread that webview.start() owns.
        threading.Thread(target=self._icon.run, daemon=True, name="omelet-tray").start()

    def stop(self) -> None:
        if self._icon is not None:
            self._icon.stop()

    def notify(self, text: str) -> bool:
        if self._icon is None:
            return False
        self._icon.notify(text, "Omelet")
        return True
