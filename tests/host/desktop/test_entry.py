"""Opening the window, and what happens when the platform cannot.

The WebView2 runtime is evergreen but absent on un-updated Windows 10. A
traceback there is a dead end for a non-technical user; the fallback has to
name the command that still works.
"""
from __future__ import annotations

import threading
from pathlib import Path

import pytest

from host.core.install import InstallState
from host.desktop.settings import Settings
from host.desktop.__main__ import WEBVIEW_MISSING, run, ui_dir


class FakeTray:
    def __init__(self, fail=False):
        self.started = self.stopped = False
        self.fail = fail
    def start(self):
        if self.fail:
            raise ImportError("pystray")
        self.started = True
    def stop(self): self.stopped = True
    def notify(self, text): return True


class FakeProvider:
    def __init__(self, exists=True, primary=True, tray_fails=False):
        self._exists, self._primary = exists, primary
        self._tray_fails = tray_fails
        self.vm_started = threading.Event()
        self.tray_made, self.started, self.login_watch = None, 0, None
        self.announce = None

    def exists(self): return self._exists
    def start(self):
        self.started += 1
        self.vm_started.set()
    def single_instance(self, on_show, *, announce):
        self.announce = announce
        return self._primary
    def watch_login_launch(self, on_login): self.login_watch = on_login
    def on_window_shown(self, visible): pass
    def tray(self, **kwargs):
        self.tray_made = (kwargs, FakeTray(self._tray_fails))
        return self.tray_made[1]


def _no_update_check():
    # run() defaults to a real network check for the desktop app's own
    # update; every call here stubs it so the suite never dials out.
    return None


LOCAL = "http://127.0.0.1:53817/index.html"


class FakeEvent:
    def __init__(self): self.handlers = []
    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self


class FakeEvents:
    def __init__(self): self.closing = FakeEvent()


class FakeWindow:
    def __init__(self, url=LOCAL):
        self.url = url
        self.loaded, self.evaluated, self.exposed = [], [], {}
        self.events = FakeEvents()
        self.calls = []

    def get_current_url(self): return self.url
    def load_url(self, url): self.loaded.append(url); self.url = url
    def evaluate_js(self, script): self.evaluated.append(script)
    def expose(self, *functions): self.exposed.update({f.__name__: f for f in functions})
    def show(self): self.calls.append("show")
    def hide(self): self.calls.append("hide")
    def destroy(self): self.calls.append("destroy")


def test_ui_dir_points_at_the_bundled_assets():
    assert (ui_dir() / "index.html").is_file()


def test_a_missing_webview_runtime_is_a_sentence_not_a_traceback(tmp_path, capsys):
    def create(**kwargs):
        raise RuntimeError("WebView2 runtime not found")

    code = run(FakeProvider(), InstallState(tmp_path / "s.json"),
               create=create, start=lambda **kwargs: None,
               app_update_fn=_no_update_check,
        settings=Settings(tmp_path / "settings.json"))

    assert code == 3
    assert WEBVIEW_MISSING in capsys.readouterr().err


def test_the_fallback_names_the_headless_command():
    # Without this the user is told the app is broken and nothing else.
    assert "omelet setup --headless" in WEBVIEW_MISSING


def test_a_working_window_starts_the_loop_and_returns_zero(tmp_path):
    started = []
    code = run(FakeProvider(), InstallState(tmp_path / "s.json"),
               create=lambda **kwargs: FakeWindow(),
               start=lambda **kwargs: started.append(kwargs),
               app_update_fn=_no_update_check,
        settings=Settings(tmp_path / "settings.json"))

    assert code == 0
    # debug must be off in a shipped build: it exposes devtools and a context
    # menu over the bridge.
    assert started[0]["debug"] is False


def test_the_real_failure_reaches_stderr_under_the_runtime_message(tmp_path, capsys):
    """The same handler catches missing-runtime AND ordinary bugs, so the
    cause has to be recoverable -- otherwise a typo reads as "install
    WebView2" and the user chases a runtime they already have."""
    def create(**kwargs):
        raise TypeError("create_window() got an unexpected keyword argument 'widht'")

    code = run(FakeProvider(), InstallState(tmp_path / "s.json"),
               create=create, start=lambda **kwargs: None,
               app_update_fn=_no_update_check,
        settings=Settings(tmp_path / "settings.json"))

    err = capsys.readouterr().err
    assert code == 3
    assert WEBVIEW_MISSING in err
    assert "widht" in err


def test_a_resumed_launch_is_recorded_for_the_install_screen(tmp_path):
    """RunOnce relaunches with --resume after a restart; the screen has to
    be able to say why it opened by itself."""
    window = FakeWindow()
    run(FakeProvider(), InstallState(tmp_path / "s.json"),
        create=lambda **kwargs: window, start=lambda **kwargs: None, resumed=True,
        app_update_fn=_no_update_check,
        settings=Settings(tmp_path / "settings.json"))

    assert window.exposed["home"]()["resumed"] is True


def test_javascript_gets_the_guarded_bridge_not_the_api(tmp_path):
    from host.desktop.shell import NotLocalPage
    captured = {}
    window = FakeWindow()

    def create(**kwargs):
        captured.update(kwargs)
        return window

    run(FakeProvider(), InstallState(tmp_path / "s.json"),
        create=create, start=lambda **kwargs: None,
        app_update_fn=_no_update_check,
        settings=Settings(tmp_path / "settings.json"))
    # pywebview resolves a dotted call name from js_api with plain getattr, so
    # any object there lets a page walk "home.__func__.__globals__" past the
    # guard. Named functions are looked up by exact name only.
    assert captured["js_api"] is None
    window.exposed["reset_install"]()
    window.url = "http://localhost:39080/"
    with pytest.raises(NotLocalPage):
        window.exposed["reset_install"]()


def _run(tmp_path, provider, **kwargs):
    captured = {}
    window = FakeWindow()

    def create(**kw):
        captured.update(kw)
        return window

    code = run(provider, InstallState(tmp_path / "s.json"), create=create,
               start=lambda **kw: None, app_update_fn=_no_update_check,
               settings=Settings(tmp_path / "settings.json"), **kwargs)
    return code, captured, window


def test_a_login_launch_with_a_vm_stays_in_the_tray(tmp_path):
    provider = FakeProvider(exists=True)
    code, captured, _ = _run(tmp_path, provider, background=True)
    assert code == 0
    assert captured["hidden"] is True
    assert provider.tray_made[1].started is True


def test_a_login_launch_before_setup_opens_the_window(tmp_path):
    _, captured, _ = _run(tmp_path, FakeProvider(exists=False), background=True)
    assert captured["hidden"] is False


def test_closing_the_window_is_wired_to_hide(tmp_path):
    _, _, window = _run(tmp_path, FakeProvider())
    assert [h() for h in window.events.closing.handlers] == [False]
    assert window.calls == ["hide"]


def test_a_second_instance_exits_without_a_window(tmp_path):
    provider = FakeProvider(primary=False)
    code, captured, _ = _run(tmp_path, provider)
    assert code == 0 and captured == {}
    assert provider.announce is True


def test_a_second_background_instance_does_not_ask_for_the_window(tmp_path):
    provider = FakeProvider(primary=False)
    _run(tmp_path, provider, background=True)
    assert provider.announce is False


def test_the_tray_menu_carries_open_settings_and_quit(tmp_path):
    provider = FakeProvider()
    _, _, window = _run(tmp_path, provider)
    kwargs, _tray = provider.tray_made
    assert set(kwargs) == {"icon", "on_open", "on_settings", "on_quit"}
    assert kwargs["icon"].is_file()
    kwargs["on_settings"]()
    assert window.evaluated[-1] == 'window.omelet.route("settings")'
    kwargs["on_quit"]()
    assert window.evaluated[-1] == 'window.omelet.route("quit")'


def test_the_tray_stops_when_the_loop_ends(tmp_path):
    provider = FakeProvider()
    _run(tmp_path, provider)
    assert provider.tray_made[1].stopped is True


def test_a_tray_that_cannot_start_falls_back_to_a_plain_window(tmp_path, capsys):
    provider = FakeProvider(exists=True, tray_fails=True)
    started = []
    captured = {}
    window = FakeWindow()

    def create(**kw):
        captured.update(kw)
        return window

    code = run(provider, InstallState(tmp_path / "s.json"), create=create,
               start=lambda **kw: started.append(kw), background=True,
               app_update_fn=_no_update_check,
               settings=Settings(tmp_path / "settings.json"))
    assert code == 0
    assert captured["hidden"] is False
    assert started
    assert window.events.closing.handlers == []
    assert "pystray" in capsys.readouterr().err
    assert not provider.vm_started.wait(0.2)


def test_a_login_launch_starts_the_vm(tmp_path):
    provider = FakeProvider(exists=True)
    _run(tmp_path, provider, background=True)
    assert provider.vm_started.wait(5)


def test_a_normal_launch_watches_for_login_launches(tmp_path):
    provider = FakeProvider()
    _run(tmp_path, provider)
    assert provider.login_watch is not None
    assert not provider.vm_started.wait(0.2)


def test_resumed_and_login_launches_do_not_watch_for_login_launches(tmp_path):
    resumed, login = FakeProvider(), FakeProvider()
    _run(tmp_path, resumed, resumed=True)
    _run(tmp_path, login, background=True)
    assert resumed.login_watch is None and login.login_watch is None
