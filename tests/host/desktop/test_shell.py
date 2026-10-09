"""The bridge belongs to the local UI, not to whatever page is showing.

pywebview injects window.pywebview.api into every page the window loads,
including the console served from inside the VM, where coding agents run as
root. These pin that such a page can see the bridge and use none of it.
"""
from __future__ import annotations

import json

import pytest

from host.core.install import InstallState
from host.desktop.api import DesktopApi
from host.desktop.shell import NotLocalPage, Shell, guarded, public_methods, web_links_only

# pywebview serves an absolute local path through its own bottle server.
LOCAL = "http://127.0.0.1:53817/index.html"


class FakeWindow:
    def __init__(self, url):
        self.url = url
        self.evaluated = []

    def get_current_url(self):
        return self.url

    def evaluate_js(self, script):
        self.evaluated.append(script)


class FakeProvider:
    pass


class HandoffClient:
    def handoff_code(self):
        return "abc"


def _api(tmp_path):
    return DesktopApi(FakeProvider(), InstallState(tmp_path / "s.json"),
                      push=lambda event: None,
                      client_factory=lambda provider: HandoffClient())


def test_the_page_the_window_opened_with_is_local():
    assert Shell(FakeWindow(LOCAL)).is_local() is True


def _enter_console(tmp_path, window, shell):
    # The only way off the local page: a guarded enter_console, then the
    # page itself navigates.
    window.url = _bridge(tmp_path, shell)["enter_console"]()["url"]


def test_a_first_call_from_the_console_is_refused(tmp_path):
    # Nothing asked is_local() while the local UI showed before entering;
    # the guard on that call must have remembered the local URL, or the
    # console would be recorded as ours.
    window = FakeWindow(LOCAL)
    shell = Shell(window)
    _enter_console(tmp_path, window, shell)
    assert shell.is_local() is False


def test_the_fragment_does_not_make_the_local_page_foreign():
    window = FakeWindow(LOCAL)
    shell = Shell(window)
    assert shell.is_local()
    window.url = LOCAL + "#install"
    assert shell.is_local() is True


def test_events_reach_the_local_page_as_one_json_argument():
    window = FakeWindow(LOCAL)
    Shell(window).push({"kind": "vm", "message": 'a "quote"'})
    assert window.evaluated == [
        f"window.eggie.on({json.dumps({'kind': 'vm', 'message': 'a \"quote\"'})})"]


def test_events_never_reach_the_console(tmp_path):
    # A console page can define its own window.eggie.on and read job events.
    window = FakeWindow(LOCAL)
    shell = Shell(window)
    _enter_console(tmp_path, window, shell)
    shell.push({"kind": "import", "type": "progress"})
    assert window.evaluated == []


def _bridge(tmp_path, shell):
    return {f.__name__: f for f in guarded(_api(tmp_path), shell)}


def test_every_public_method_is_refused_from_the_console(tmp_path):
    window = FakeWindow(LOCAL)
    shell = Shell(window)
    bridge = _bridge(tmp_path, shell)
    _enter_console(tmp_path, window, shell)
    names = public_methods(DesktopApi)
    assert "reboot_now" in names and "start_vm" in names
    for name in names:
        with pytest.raises(NotLocalPage):
            bridge[name]()


def test_the_bridge_exposes_only_the_public_methods(tmp_path):
    # JobRegistry sits on DesktopApi as .jobs; nothing but the guarded
    # public methods may reach pywebview.
    assert sorted(_bridge(tmp_path, Shell(FakeWindow(LOCAL)))) == public_methods(DesktopApi)


def test_the_local_page_reaches_the_real_method(tmp_path):
    assert _bridge(tmp_path, Shell(FakeWindow(LOCAL)))["reset_install"]() == {"ok": True}


@pytest.mark.parametrize("before_first_load", [None, "None", "about:blank"])
def test_nothing_is_recorded_before_the_local_page_has_loaded(before_first_load):
    # WebView2 reports None and WKWebView the string "None" until the first
    # load; recording either would make the console's first call "ours".
    shell = Shell(FakeWindow(before_first_load))
    assert shell.local_url() is None


@pytest.mark.parametrize("url", [
    "file:///C:/Windows/System32/calc.exe",
    "ms-settings:privacy",
    "javascript:alert(1)",
    "\\\\server\\share\\run.exe",
])
def test_a_page_cannot_hand_the_os_anything_but_a_web_link(url):
    # pywebview gives every new-window request from any page, the console
    # included, to webbrowser.open; on Windows that is os.startfile.
    opened = []
    web_links_only(opened.append)(url)
    assert opened == []


def test_web_links_still_reach_the_browser():
    opened = []
    open_ = web_links_only(lambda url, *args: opened.append((url, args)))
    open_("http://recipe-box.localhost:39080/", 2, True)
    assert opened == [("http://recipe-box.localhost:39080/", (2, True))]
