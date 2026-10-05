"""The macOS menu-bar item, plus the two app-level hooks a tray app needs.

pywebview's Cocoa loop owns the main thread and AppKit refuses status items
from any other, so everything here is scheduled onto that loop.
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path
from typing import Callable


def _run_off_main(callback: Callable[[], None]) -> None:
    """Menu actions arrive on the main thread, but the callbacks drive pywebview
    window calls that dispatch to the main thread and wait, which would deadlock."""
    def run():
        try:
            callback()
        except Exception as e:
            print(f"tray callback failed: {e!r}", file=sys.stderr)

    threading.Thread(target=run, daemon=True, name="eggie-tray-action").start()


_target_class = None


def _menu_target_class():
    """Created once: ObjC class names are global, so a second definition is rejected."""
    global _target_class
    if _target_class is None:
        import objc
        from Foundation import NSObject

        class EggieTrayTarget(NSObject):
            @objc.signature(b"v@:@")
            def open_(self, sender):
                _run_off_main(self.on_open)

            @objc.signature(b"v@:@")
            def settings_(self, sender):
                _run_off_main(self.on_settings)

            @objc.signature(b"v@:@")
            def quit_(self, sender):
                _run_off_main(self.on_quit)

        _target_class = EggieTrayTarget
    return _target_class


class MacTray:
    def __init__(self, *, icon: Path, on_open, on_settings, on_quit):
        self._icon_path = icon
        self._on_open, self._on_settings, self._on_quit = on_open, on_settings, on_quit
        self._item = None
        self._target = None

    def start(self) -> None:
        from PyObjCTools import AppHelper
        self._install_app_hooks()
        AppHelper.callAfter(self._build)

    def _build(self) -> None:
        from AppKit import NSImage, NSMenu, NSMenuItem, NSStatusBar, NSVariableStatusItemLength

        self._target = _menu_target_class().alloc().init()
        self._target.on_open = self._on_open
        self._target.on_settings = self._on_settings
        self._target.on_quit = self._on_quit

        menu = NSMenu.alloc().init()
        for title, action in (("Open Eggie", b"open:"), ("Settings", b"settings:"),
                              (None, None), ("Quit Eggie", b"quit:")):
            if title is None:
                menu.addItem_(NSMenuItem.separatorItem())
                continue
            entry = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, action, "")
            entry.setTarget_(self._target)
            menu.addItem_(entry)

        self._item = NSStatusBar.systemStatusBar().statusItemWithLength_(NSVariableStatusItemLength)
        self._item.setMenu_(menu)
        self._retarget_cmd_q()
        image = NSImage.alloc().initWithContentsOfFile_(str(self._icon_path))
        if image is None:
            self._item.button().setTitle_("Eggie")
            return
        image.setSize_((18, 18))
        self._item.button().setImage_(image)

    def _retarget_cmd_q(self) -> None:
        # pywebview's Quit item calls terminate:, which runs every window's
        # closing handler -- and ours turns a close into a hide. Cmd+Q must
        # mean Quit Eggie instead.
        from AppKit import NSApplication
        main_menu = NSApplication.sharedApplication().mainMenu()
        if main_menu is None:
            return
        for top in main_menu.itemArray():
            submenu = top.submenu()
            for entry in (submenu.itemArray() if submenu else []):
                if entry.keyEquivalent() == "q":
                    entry.setTarget_(self._target)
                    entry.setAction_(b"quit:")

    def _install_app_hooks(self) -> None:
        import objc
        from webview.platforms.cocoa import BrowserView

        on_open = self._on_open

        @objc.signature(b"Z@:@Z")
        def applicationShouldHandleReopen_hasVisibleWindows_(self, app, visible):
            _run_off_main(on_open)
            return True

        # pywebview's terminate handler runs the windows' closing handlers,
        # and ours refuses (close means hide). Dock Quit, logout and restart
        # must go through, and they end the VM at OS level, so they exit
        # without stopping it here. Cmd+Q is retargeted to Quit Eggie.
        @objc.signature(b"Q@:@")
        def applicationShouldTerminate_(self, app):
            return 1  # NSTerminateNow

        objc.classAddMethods(BrowserView.AppDelegate,
                             [applicationShouldHandleReopen_hasVisibleWindows_,
                              applicationShouldTerminate_])

    def stop(self) -> None:
        if self._item is None:
            return
        from AppKit import NSStatusBar
        from PyObjCTools import AppHelper
        item, self._item = self._item, None
        AppHelper.callAfter(NSStatusBar.systemStatusBar().removeStatusItem_, item)

    def notify(self, text: str) -> bool:
        # UNUserNotificationCenter needs a signed app; until then the caller
        # falls back to showing the window where it matters.
        return False
