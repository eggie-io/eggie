"""What the desktop app asks of macOS: the menu-bar item, Login Items, the
Dock, and the two app-delegate hooks a tray app needs.

Nothing here touches the VM; that is LimaProvider's job. Cocoa imports stay
function-local so the suite imports this on Linux.
"""
from __future__ import annotations


class MacDesktop:
    def __init__(self, login_items=None):
        # Resolved lazily so importing this module never needs ServiceManagement.
        self._login_items = login_items

    def _items(self):
        if self._login_items is None:
            from .mac_login import MainAppLoginItem
            self._login_items = MainAppLoginItem()
        return self._login_items

    def autostart_enabled(self, exe_path: str) -> bool:
        return self._items().enabled()

    def set_autostart(self, on: bool, exe_path: str) -> None:
        """exe_path is unused: SMAppService registers this .app bundle itself."""
        if on:
            self._items().register()
        else:
            self._items().unregister()

    def watch_login_launch(self, on_login) -> None:
        """Must be called before webview.start(): it hooks pywebview's app delegate."""
        from .mac_login import install_login_launch_handler
        install_login_launch_handler(on_login)

    def let_session_end_close(self, on_session_end) -> None:
        """Nothing to do: tray_mac's NSTerminateNow already lets logout through."""

    def on_window_shown(self, visible: bool) -> None:
        from .mac_login import set_dock_visible
        set_dock_visible(visible)

    def tray(self, *, icon, on_open, on_settings, on_quit):
        from .tray_mac import MacTray
        return MacTray(icon=icon, on_open=on_open, on_settings=on_settings, on_quit=on_quit)

    def single_instance(self, on_show, *, announce: bool) -> bool:
        """LaunchServices keeps one instance of a .app; a second open arrives
        as the reopen event, which the tray turns into Open Eggie."""
        return True
