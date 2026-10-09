"""The window and the tray, as one app that lives past its window."""
from __future__ import annotations

import json
import logging
import threading

from .settings import TRAY_NOTICE_SHOWN, Settings

log = logging.getLogger(__name__)

TRAY_NOTICE = "Eggie is still running. Find it in the system tray."
START_FAILED = "Eggie could not start. Open Eggie to see why."
# The hidden page drew Home while the VM was still booting.
BACKGROUND_DONE = {"kind": "background_start", "type": "done"}
# The page may have drawn while hidden, before the VM answered.
WINDOW_SHOWN = {"kind": "window", "type": "shown"}


class Controller:
    def __init__(self, provider, desktop, settings: Settings, shell, push=None):
        self.provider = provider
        self.desktop = desktop
        self._push = push
        self.settings = settings
        self.shell = shell
        self.window = None
        self.tray = None
        self._exiting = False
        self._background = None
        self.shown_once = True

    def mark_hidden_launch(self) -> None:
        self.shown_once = False

    def on_closing(self) -> bool:
        # destroy() and macOS terminate: both arrive here too; only a real
        # exit may get through.
        if self._exiting:
            return True
        self.hide()
        # An exception escaping a pywebview handler counts as "not False",
        # which lets the close through; hiding must never turn into quitting.
        try:
            if not self.settings.get(TRAY_NOTICE_SHOWN) and self.tray is not None:
                if self.tray.notify(TRAY_NOTICE):
                    self.settings.set(TRAY_NOTICE_SHOWN, True)
        except Exception as e:
            log.warning("could not show the tray notice: %r", e)
        return False

    def allow_exit(self) -> None:
        """Sign-out, restart and installers close the window themselves;
        cancelling that close would block them."""
        self._exiting = True

    def hide(self) -> None:
        if self.window is not None:
            self.window.hide()
        self.desktop.on_window_shown(False)

    def show(self) -> None:
        if self.window is None:
            return
        self._show_window()
        self._send(WINDOW_SHOWN)

    def _show_window(self) -> None:
        self.desktop.on_window_shown(True)
        self.window.show()
        self.shown_once = True

    def open_route(self, route: str) -> None:
        if self.window is None:
            return
        # No WINDOW_SHOWN: the route redraws the page, and a refresh racing
        # it would draw Home over the route's screen.
        self._show_window()
        if self.shell.is_local():
            self.window.evaluate_js(f"window.eggie.route({json.dumps(route)})")
            return
        # The console is a page from the VM: no window.eggie there, and the
        # bridge refuses it. Back to our own page, which reads the fragment.
        local = self.shell.local_url()
        if local:
            self.window.load_url(f"{local}#{route}")

    def exit(self) -> None:
        self._exiting = True
        try:
            if self.tray is not None:
                self.tray.stop()
        finally:
            if self.window is not None:
                self.window.destroy()

    def on_login_launch(self) -> None:
        # Hiding with no tray would leave nothing to bring the window back.
        if self.tray is None:
            return
        try:
            installed = self.provider.exists()
        except Exception:
            installed = False
        if not installed:
            return
        self.hide()
        self.start_vm_in_background()

    def _notify(self, text: str) -> bool:
        if self.tray is None:
            return False
        try:
            return bool(self.tray.notify(text))
        except Exception as e:
            log.warning("could not show a notification: %r", e)
            return False

    def _tell_page(self) -> None:
        self._send(BACKGROUND_DONE)

    def _send(self, event: dict) -> None:
        if self._push is None:
            return
        try:
            self._push(event)
        except Exception as e:
            log.warning("could not refresh the window: %r", e)

    def start_vm_in_background(self) -> threading.Thread:
        def run():
            try:
                self.provider.start()
            except Exception as e:
                log.exception("could not start the virtual machine in the background")
                if not self._notify(START_FAILED):
                    self.show()
            finally:
                self._tell_page()

        self._background = threading.Thread(target=run, daemon=True, name="eggie-autostart")
        self._background.start()
        return self._background
