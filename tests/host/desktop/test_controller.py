from __future__ import annotations

from host.desktop.controller import START_FAILED, TRAY_NOTICE, Controller
from host.desktop.settings import TRAY_NOTICE_SHOWN, Settings
from host.desktop.shell import Shell

LOCAL = "http://127.0.0.1:53817/index.html"


class FakeWindow:
    def __init__(self, url=LOCAL):
        self.url, self.calls = url, []

    def get_current_url(self): return self.url
    def show(self): self.calls.append("show")
    def hide(self): self.calls.append("hide")
    def destroy(self): self.calls.append("destroy")
    def load_url(self, url): self.calls.append(("load", url)); self.url = url
    def evaluate_js(self, script): self.calls.append(("js", script))


class FakeTray:
    def __init__(self, can_notify=True):
        self.can_notify, self.notes, self.stopped = can_notify, [], False

    def notify(self, text):
        if self.can_notify:
            self.notes.append(text)
        return self.can_notify

    def stop(self): self.stopped = True


class FakeProvider:
    def __init__(self, start_error=None, exists=True):
        self.shown, self.started = [], 0
        self._start_error, self._exists = start_error, exists

    def on_window_shown(self, visible): self.shown.append(visible)
    def exists(self): return self._exists

    def start(self):
        self.started += 1
        if self._start_error:
            raise self._start_error


def _controller(tmp_path, *, provider=None, tray=None, url=LOCAL):
    window = FakeWindow(url)
    shell = Shell(window)
    controller = Controller(provider or FakeProvider(), Settings(tmp_path / "s.json"), shell)
    controller.window, controller.tray = window, tray or FakeTray()
    return controller, window


def test_closing_hides_instead_of_closing(tmp_path):
    controller, window = _controller(tmp_path)
    assert controller.on_closing() is False
    assert window.calls == ["hide"]


def test_the_first_hide_tells_the_user_where_omelet_went_once(tmp_path):
    controller, _ = _controller(tmp_path)
    controller.on_closing()
    controller.on_closing()
    assert controller.tray.notes == [TRAY_NOTICE]
    assert controller.settings.get(TRAY_NOTICE_SHOWN) is True


def test_a_platform_without_notifications_tries_again_next_time(tmp_path):
    controller, _ = _controller(tmp_path, tray=FakeTray(can_notify=False))
    controller.on_closing()
    assert controller.settings.get(TRAY_NOTICE_SHOWN) is False


def test_exit_is_not_turned_into_a_hide(tmp_path):
    controller, window = _controller(tmp_path)
    controller.exit()
    assert controller.on_closing() is True
    assert "hide" not in window.calls
    assert window.calls[-1] == "destroy"
    assert controller.tray.stopped is True


def test_show_brings_the_window_and_dock_back(tmp_path):
    controller, window = _controller(tmp_path)
    controller.on_closing()
    controller.show()
    assert window.calls[-1] == "show"
    assert controller.provider.shown == [False, True]


def test_a_route_on_the_local_ui_is_handed_to_the_page(tmp_path):
    controller, window = _controller(tmp_path)
    controller.open_route("settings")
    assert ("js", 'window.omelet.route("settings")') in window.calls


def test_a_route_from_the_console_reloads_the_local_ui(tmp_path):
    controller, window = _controller(tmp_path)
    controller.shell.local_url()          # the local page was seen first
    window.url = "http://localhost:39080/"
    controller.open_route("quit")
    assert ("load", LOCAL + "#quit") in window.calls


def test_a_failed_background_start_is_announced(tmp_path):
    provider = FakeProvider(start_error=RuntimeError("wsl.exe failed"))
    controller, _ = _controller(tmp_path, provider=provider)
    controller.start_vm_in_background().join(timeout=5)
    assert controller.tray.notes == [START_FAILED]


def test_a_failed_background_start_shows_the_window_where_it_cannot_notify(tmp_path):
    provider = FakeProvider(start_error=RuntimeError("limactl failed"))
    controller, window = _controller(tmp_path, provider=provider, tray=FakeTray(can_notify=False))
    controller.start_vm_in_background().join(timeout=5)
    assert window.calls[-1] == "show"


def test_a_login_launch_hides_and_starts_the_vm(tmp_path):
    controller, window = _controller(tmp_path)
    controller.on_login_launch()
    controller._background.join(timeout=5)
    assert window.calls[0] == "hide"
    assert controller.provider.started == 1


def test_a_login_launch_before_setup_keeps_the_window(tmp_path):
    controller, window = _controller(tmp_path, provider=FakeProvider(exists=False))
    controller.on_login_launch()
    assert window.calls == []


class RaisingTray(FakeTray):
    def __init__(self, *, notify=False, stop=False):
        super().__init__()
        self._raise_notify, self._raise_stop = notify, stop

    def notify(self, text):
        if self._raise_notify:
            raise RuntimeError("notify failed")
        return super().notify(text)

    def stop(self):
        if self._raise_stop:
            raise RuntimeError("stop failed")
        super().stop()


def test_closing_still_hides_when_the_notice_cannot_be_saved(tmp_path):
    controller, window = _controller(tmp_path)

    def broken_set(key, value):
        raise OSError("disk full")

    controller.settings.set = broken_set
    assert controller.on_closing() is False
    assert window.calls == ["hide"]


def test_closing_still_hides_when_the_tray_notice_raises(tmp_path):
    controller, window = _controller(tmp_path, tray=RaisingTray(notify=True))
    assert controller.on_closing() is False
    assert window.calls == ["hide"]


def test_exit_destroys_the_window_even_if_the_tray_will_not_stop(tmp_path):
    controller, window = _controller(tmp_path, tray=RaisingTray(stop=True))
    try:
        controller.exit()
    except RuntimeError:
        pass
    assert window.calls[-1] == "destroy"


def test_a_failed_background_start_shows_the_window_when_notify_raises(tmp_path):
    provider = FakeProvider(start_error=RuntimeError("limactl failed"))
    controller, window = _controller(tmp_path, provider=provider, tray=RaisingTray(notify=True))
    controller.start_vm_in_background().join(timeout=5)
    assert window.calls[-1] == "show"


def test_a_login_launch_keeps_the_window_when_the_vm_check_fails(tmp_path):
    class Broken(FakeProvider):
        def exists(self): raise RuntimeError("wsl.exe failed")

    provider = Broken()
    controller, window = _controller(tmp_path, provider=provider)
    controller.on_login_launch()
    assert window.calls == [] and provider.started == 0


def test_a_session_end_close_is_let_through_without_hiding(tmp_path):
    controller, window = _controller(tmp_path)
    controller.allow_exit()
    assert controller.on_closing() is True
    assert window.calls == []
    assert controller.tray.notes == []
