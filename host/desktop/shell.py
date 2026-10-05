"""The one window, and which page in it is ours.

pywebview injects `window.pywebview.api` into every page the window loads.
Once the projects console (served from inside the VM) is showing, anything
running in the VM can see the bridge. `guarded()` is what JS receives, and it
refuses every call unless the local UI is the page on screen.
"""
from __future__ import annotations

import json
from typing import Any
from urllib.parse import urldefrag


class NotLocalPage(PermissionError):
    """A bridge call arrived while a page other than the local UI showed."""


class Shell:
    def __init__(self, window: Any = None):
        self.window = window
        self._local: str | None = None

    def _current(self) -> str | None:
        if self.window is None:
            return None
        url = self.window.get_current_url()
        return urldefrag(url)[0] if url else None

    def _remember(self) -> None:
        # Until the console is entered the window has only ever shown the
        # local UI, so the first URL seen is ours. The guard on enter_console
        # calls this before the page leaves.
        # Before its first load WebView2 reports None and WKWebView "None".
        if self._local is None:
            current = self._current()
            if current and current.startswith(("http://", "https://")):
                self._local = current

    def is_local(self) -> bool:
        self._remember()
        current = self._current()
        return current is not None and current == self._local

    def local_url(self) -> str | None:
        self._remember()
        return self._local

    def push(self, event: dict) -> None:
        # json.dumps, never a format string: a message carrying a quote would
        # otherwise close the call and inject whatever followed.
        if self.window is not None and self.is_local():
            self.window.evaluate_js(f"window.eggie.on({json.dumps(event)})")


def public_methods(cls: type) -> list[str]:
    return sorted(name for name, value in vars(cls).items()
                  if callable(value) and not name.startswith("_"))


def guarded(api: object, shell: Shell) -> list:
    """Functions for window.expose(), never an object for js_api.

    pywebview resolves a call name like "home.__func__.__globals__.clear"
    from js_api with plain getattr, which would walk past the guard into the
    host process. Exposed functions are matched by exact name only.
    """
    return [_guard(getattr(api, name), name, shell)
            for name in public_methods(type(api))]


def _guard(method, name: str, shell: Shell):
    def call(*args, **kwargs):
        if not shell.is_local():
            raise NotLocalPage(f"{name} is only available to Eggie's own screens")
        return method(*args, **kwargs)
    call.__name__ = name
    return call


def web_links_only(open_url):
    """Wrap webbrowser.open so a page can only hand the OS a web address.

    pywebview gives every new-window request from any page in the window to
    webbrowser.open, and on Windows that ends in os.startfile: a console page
    from the VM could otherwise launch a file: path or a custom scheme.
    """
    def open_web(url, *args, **kwargs):
        if not str(url).lower().startswith(("http://", "https://")):
            return False
        return open_url(url, *args, **kwargs)
    return open_web
