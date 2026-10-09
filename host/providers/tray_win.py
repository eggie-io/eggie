"""The Windows notification-area icon."""
from __future__ import annotations

import logging
import threading
from pathlib import Path

log = logging.getLogger(__name__)


def _guarded(callback):
    def run():
        try:
            callback()
        except Exception:
            log.exception("tray callback failed")
    return run


def session_end_aware(original, reasons, on_session_end):
    def on_closing(form, sender, args):
        if args.CloseReason in reasons:
            on_session_end()
        return original(form, sender, args)
    on_closing.eggie_original = original
    return on_closing


def let_session_end_close(on_session_end) -> None:
    """pywebview cancels every close our handler refuses, whatever its
    CloseReason; sign-out, restart and Restart Manager (installer update,
    uninstall) would be blocked. Patched on the class before the form exists:
    its constructor binds self.on_closing to FormClosing."""
    # pywebview's module loads pythonnet and references WinForms; System.*
    # cannot be imported before it.
    from webview.platforms.winforms import BrowserView
    import System.Windows.Forms as WinForms

    form = BrowserView.BrowserForm
    if hasattr(form.on_closing, "eggie_original"):
        return
    reasons = (WinForms.CloseReason.WindowsShutDown,
               WinForms.CloseReason.TaskManagerClosing)
    form.on_closing = session_end_aware(form.on_closing, reasons, on_session_end)


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
            pystray.MenuItem("Open Eggie", _guarded(on_open), default=True),
            pystray.MenuItem("Settings", _guarded(on_settings)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit Eggie", _guarded(on_quit)),
        )
        self._icon = pystray.Icon("Eggie", Image.open(self._paths), "Eggie", menu)
        # The Win32 backend runs its own message loop, so it can live off the
        # main thread that webview.start() owns.
        threading.Thread(target=self._icon.run, kwargs={"setup": self._setup},
                         daemon=True, name="eggie-tray").start()

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
                log.warning("tray stop failed: %r", e)

    def notify(self, text: str) -> bool:
        # Shell_NotifyIcon fails silently until the icon has been added.
        if self._icon is None or not self._visible.wait(2):
            return False
        self._icon.notify(text, "Eggie")
        return True
