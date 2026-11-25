"""eggie:// links on macOS: LaunchServices sends each as a kAEGetURL Apple event,
both to the running app and to the one a link launched."""
from __future__ import annotations

import logging
import threading
from typing import Callable

log = logging.getLogger(__name__)


def _code(four: str) -> int:
    return int.from_bytes(four.encode("ascii"), "big")


GET_URL = _code("GURL")  # kInternetEventClass and kAEGetURL
DIRECT_OBJECT = _code("----")  # keyDirectObject

_handler = None


def watch_links(on_link: Callable[[str], None]) -> None:
    """Must run before webview.start(): the launching link is delivered once the loop runs."""
    global _handler
    import objc
    from Foundation import NSAppleEventManager, NSObject

    class EggieLinkHandler(NSObject):
        @objc.signature(b"v@:@@")
        def handleURL_withReply_(self, event, reply):
            url = event.paramDescriptorForKeyword_(DIRECT_OBJECT)
            url = str(url.stringValue()) if url is not None else ""

            def run():
                try:
                    on_link(url)
                except Exception:
                    log.exception("could not open the link")

            # Off the main thread: the window calls it makes wait on that thread.
            threading.Thread(target=run, daemon=True, name="eggie-link").start()

    # Held for the app's life: the event manager keeps no strong reference.
    _handler = EggieLinkHandler.alloc().init()
    NSAppleEventManager.sharedAppleEventManager() \
        .setEventHandler_andSelector_forEventClass_andEventID_(
            _handler, b"handleURL:withReply:", GET_URL, GET_URL)
