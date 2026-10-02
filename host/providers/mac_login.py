"""Open at login on macOS, and telling a login launch from a user launch.

SMAppService (macOS 13+, our minimum) registers the app itself as a Login
Item. It launches the app with no arguments, so a login launch is recognised
from the open-application Apple event instead of a --background flag.
"""
from __future__ import annotations

from typing import Callable

# keyAEPropData / keyAELaunchedAsLogInItem, as four-char codes.
_PROP_DATA = int.from_bytes(b"prdt", "big")
_LAUNCHED_AS_LOGIN_ITEM = int.from_bytes(b"lgit", "big")
_CORE_EVENT_CLASS = int.from_bytes(b"aevt", "big")
_OPEN_APPLICATION = int.from_bytes(b"oapp", "big")


class MainAppLoginItem:
    def _service(self):
        from ServiceManagement import SMAppService
        return SMAppService.mainAppService()

    def enabled(self) -> bool:
        from ServiceManagement import SMAppServiceStatusEnabled
        return self._service().status() == SMAppServiceStatusEnabled

    def register(self) -> None:
        ok, error = self._service().registerAndReturnError_(None)
        if not ok:
            raise RuntimeError(f"macOS refused to add Omelet to Login Items: {error}")

    def unregister(self) -> None:
        ok, error = self._service().unregisterAndReturnError_(None)
        if not ok:
            raise RuntimeError(f"macOS refused to remove Omelet from Login Items: {error}")


def install_login_launch_handler(on_login: Callable[[], None]) -> None:
    """Replace the open-application handler for this launch.

    AppKit installs its own oapp handler in finishLaunching, so ours goes in
    from applicationWillFinishLaunching_, added to pywebview's app delegate,
    which is the documented point where an app may override it.
    """
    import objc
    from Foundation import NSAppleEventManager, NSObject
    from webview.platforms.cocoa import BrowserView

    class _OpenHandler(NSObject):
        def handleOpen_withReply_(self, event, reply):
            descriptor = event.paramDescriptorForKeyword_(_PROP_DATA)
            if descriptor is not None and descriptor.enumCodeValue() == _LAUNCHED_AS_LOGIN_ITEM:
                on_login()

    handler = _OpenHandler.alloc().init()

    def applicationWillFinishLaunching_(self, notification):
        NSAppleEventManager.sharedAppleEventManager() \
            .setEventHandler_andSelector_forEventClass_andEventID_(
                handler, b"handleOpen:withReply:", _CORE_EVENT_CLASS, _OPEN_APPLICATION)

    objc.classAddMethods(BrowserView.AppDelegate, [applicationWillFinishLaunching_])
    # Kept alive for the life of the app: the event manager does not retain it.
    install_login_launch_handler._handler = handler


def set_dock_visible(visible: bool) -> None:
    from AppKit import (NSApplication, NSApplicationActivationPolicyAccessory,
                        NSApplicationActivationPolicyRegular)
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyRegular if visible
                             else NSApplicationActivationPolicyAccessory)
    if visible:
        app.activateIgnoringOtherApps_(True)
