"""Open at login on macOS, and telling a login launch from a user launch.

SMAppService (macOS 13+, our minimum) registers the app itself as a Login
Item. It launches the app with no arguments, so a login launch is recognised
from the open-application Apple event instead of a --background flag.
"""
from __future__ import annotations

import sys
import threading
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
        from ServiceManagement import SMAppServiceStatusRequiresApproval
        service = self._service()
        ok, error = service.registerAndReturnError_(None)
        if not ok:
            raise RuntimeError(f"macOS refused to add Omelet to Login Items: {error}")
        # Registration succeeds but stays off until the user allows it.
        if service.status() == SMAppServiceStatusRequiresApproval:
            raise RuntimeError("macOS needs your permission to open Omelet when you sign in. "
                               "Allow Omelet in System Settings → General → Login Items.")

    def unregister(self) -> None:
        from ServiceManagement import SMAppServiceStatusNotRegistered
        service = self._service()
        if service.status() == SMAppServiceStatusNotRegistered:
            return
        ok, error = service.unregisterAndReturnError_(None)
        if not ok:
            raise RuntimeError(f"macOS refused to remove Omelet from Login Items: {error}")


_open_handler = None


def _make_open_handler(on_login: Callable[[], None]):
    """One ObjC handler instance; the class is created once, as ObjC class names are global."""
    global _open_handler
    if _open_handler is None:
        import objc
        from Foundation import NSObject

        class OmeletOpenHandler(NSObject):
            @objc.signature(b"v@:@@")
            def handleOpen_withReply_(self, event, reply):
                descriptor = event.paramDescriptorForKeyword_(_PROP_DATA)
                if descriptor is None or descriptor.enumCodeValue() != _LAUNCHED_AS_LOGIN_ITEM:
                    return
                # Off the main thread: the callback probes the VM and must not
                # stall launch, and an exception must not surface in AppKit dispatch.
                threading.Thread(target=self._run, daemon=True).start()

            def _run(self):
                try:
                    self.on_login()
                except Exception as e:
                    print(f"login-launch handler failed: {e!r}", file=sys.stderr)

        _open_handler = OmeletOpenHandler.alloc().init()
    _open_handler.on_login = on_login
    return _open_handler


def install_login_launch_handler(on_login: Callable[[], None]) -> None:
    """Replace the open-application handler for this launch.

    AppKit installs its own oapp handler in finishLaunching, so ours goes in
    from applicationWillFinishLaunching_, added to pywebview's app delegate.
    That only fires if this runs before pywebview creates the delegate.
    """
    import objc
    from Foundation import NSAppleEventManager
    from webview.platforms.cocoa import BrowserView

    if BrowserView._shared_app_delegate is not None:
        raise RuntimeError("install_login_launch_handler must run before webview.start()")

    handler = _make_open_handler(on_login)

    def applicationWillFinishLaunching_(self, notification):
        NSAppleEventManager.sharedAppleEventManager() \
            .setEventHandler_andSelector_forEventClass_andEventID_(
                handler, b"handleOpen:withReply:", _CORE_EVENT_CLASS, _OPEN_APPLICATION)

    objc.classAddMethods(BrowserView.AppDelegate, [applicationWillFinishLaunching_])


def set_dock_visible(visible: bool) -> None:
    from Foundation import NSThread
    if NSThread.isMainThread():
        _apply_dock_policy(visible)
    else:
        from PyObjCTools import AppHelper
        AppHelper.callAfter(_apply_dock_policy, visible)


def _apply_dock_policy(visible: bool) -> None:
    from AppKit import (NSApplication, NSApplicationActivationPolicyAccessory,
                        NSApplicationActivationPolicyRegular)
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyRegular if visible
                             else NSApplicationActivationPolicyAccessory)
    if visible:
        app.activateIgnoringOtherApps_(True)
