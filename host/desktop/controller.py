"""The window and the tray, as one app that lives past its window."""
from __future__ import annotations

import json
import sys
import threading

from .settings import TRAY_NOTICE_SHOWN, Settings

TRAY_NOTICE = "Omelet is still running. Find it in the system tray."
START_FAILED = "Omelet could not start. Open Omelet to see why."


class Controller:
    def __init__(self, provider, settings: Settings, shell):
        self.provider = provider
        self.settings = settings
        self.shell = shell
        self.window = None
        self.tray = None
        self._exiting = False
        self._background = None

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
            print(f"Omelet could not show the tray notice: {e!r}", file=sys.stderr)
        return False

    def allow_exit(self) -> None:
        """Sign-out, restart and installers close the window themselves;
        cancelling that close would block them."""
        self._exiting = True

    def hide(self) -> None:
        if self.window is not None:
            self.window.hide()
        self.provider.on_window_shown(False)

    def show(self) -> None:
        if self.window is None:
            return
        self.provider.on_window_shown(True)
        self.window.show()

    def open_route(self, route: str) -> None:
        if self.window is None:
            return
        self.show()
        if self.shell.is_local():
            self.window.evaluate_js(f"window.omelet.route({json.dumps(route)})")
            return
        # The console is a page from the VM: no window.omelet there, and the
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
            print(f"Omelet could not show a notification: {e!r}", file=sys.stderr)
            return False

    def start_vm_in_background(self) -> threading.Thread:
        def run():
            try:
                self.provider.start()
            except Exception as e:
                print(f"Omelet could not start the virtual machine: {e!r}", file=sys.stderr)
                if not self._notify(START_FAILED):
                    self.show()

        self._background = threading.Thread(target=run, daemon=True, name="omelet-autostart")
        self._background.start()
        return self._background
