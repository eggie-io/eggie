from __future__ import annotations

import threading

from host.core.install import InstallState, Step
from host.core.status import Readiness
from host.desktop.api import DesktopApi
from host.desktop.settings import Settings

EXE = r"C:\Eggie\setup.exe"


class FakeProvider:
    def __init__(self, running=True, stop_error=None):
        self.autostart, self.stops = {}, 0
        self._running, self._stop_error = running, stop_error

    def autostart_enabled(self, exe):
        return self.autostart.get(exe, False)

    def set_autostart(self, on, exe):
        self.autostart[exe] = on

    def running(self):
        return self._running

    def stop(self):
        self.stops += 1
        if self._stop_error:
            raise self._stop_error


def _api(tmp_path, provider=None, *, exe=EXE, steps=None, quits=None, **kwargs):
    return DesktopApi(provider or FakeProvider(), InstallState(tmp_path / "state.json"),
                      push=lambda e: None, probe_fn=lambda p: Readiness(),
                      steps_factory=lambda: steps or [Step("preflight", lambda: None)],
                      settings=Settings(tmp_path / "settings.json"), autostart_exe=exe,
                      quit_app=(lambda: quits.append(True)) if quits is not None else lambda: None,
                      **kwargs)


def test_settings_read_the_real_state(tmp_path):
    provider = FakeProvider()
    api = _api(tmp_path, provider)
    assert api.get_settings() == {"autostart": False, "autostart_available": True}
    provider.autostart[EXE] = True     # turned on outside Eggie
    assert api.get_settings()["autostart"] is True


def test_a_source_checkout_cannot_register_itself(tmp_path):
    api = _api(tmp_path, exe=None)
    assert api.get_settings() == {"autostart": False, "autostart_available": False}
    assert api.set_autostart(True)["ok"] is False


def test_set_autostart_reports_a_refusal_as_text(tmp_path):
    class Refusing(FakeProvider):
        def set_autostart(self, on, exe):
            raise RuntimeError("macOS refused to add Eggie to Login Items")
    result = _api(tmp_path, Refusing()).set_autostart(True)
    assert result == {"ok": False, "error": "macOS refused to add Eggie to Login Items"}


def test_the_first_successful_install_turns_autostart_on(tmp_path):
    provider = FakeProvider()
    api = _api(tmp_path, provider)
    api.start_install()
    api.jobs.join(timeout=5)
    assert provider.autostart == {EXE: True}


def test_a_second_successful_install_does_not_turn_autostart_back_on(tmp_path):
    provider = FakeProvider()
    api = _api(tmp_path, provider)
    api.start_install(); api.jobs.join(timeout=5)
    api.set_autostart(False)
    InstallState(tmp_path / "state.json").clear()      # reset / repair path
    api.start_install(); api.jobs.join(timeout=5)
    assert provider.autostart == {EXE: False}


def test_a_failed_install_does_not_turn_autostart_on(tmp_path):
    def boom():
        raise RuntimeError("download failed")
    provider = FakeProvider()
    api = _api(tmp_path, provider, steps=[Step("fetch_image", boom)])
    api.start_install(); api.jobs.join(timeout=5)
    assert provider.autostart == {}


def test_quit_stops_a_running_vm_then_exits(tmp_path):
    provider, quits = FakeProvider(), []
    api = _api(tmp_path, provider, quits=quits)
    assert api.quit(False) == {"quitting": True}
    api._quit_thread.join(timeout=5)
    assert provider.stops == 1 and quits == [True]


def test_quit_leaves_a_stopped_vm_alone(tmp_path):
    provider, quits = FakeProvider(running=False), []
    api = _api(tmp_path, provider, quits=quits)
    api.quit(False); api._quit_thread.join(timeout=5)
    assert provider.stops == 0 and quits == [True]


def test_quit_exits_even_when_the_vm_will_not_stop(tmp_path):
    provider, quits = FakeProvider(stop_error=RuntimeError("wsl hung")), []
    api = _api(tmp_path, provider, quits=quits)
    api.quit(False); api._quit_thread.join(timeout=5)
    assert quits == [True]


def test_quit_during_a_job_asks_first(tmp_path):
    release = threading.Event()
    provider, quits = FakeProvider(), []
    api = _api(tmp_path, provider, quits=quits,
               steps=[Step("create_vm", lambda: release.wait(5))])
    api.start_install()
    try:
        assert api.quit(False) == {"confirm": True}
        assert provider.stops == 0 and quits == []
    finally:
        release.set()
        api.jobs.join(timeout=5)


def test_quit_anyway_runs_while_a_job_is_still_running(tmp_path):
    release = threading.Event()
    provider, quits = FakeProvider(), []
    api = _api(tmp_path, provider, quits=quits,
               steps=[Step("create_vm", lambda: release.wait(5))])
    api.start_install()
    try:
        assert api.quit(True) == {"quitting": True}
        api._quit_thread.join(timeout=5)
        assert quits == [True]
    finally:
        release.set()
        api.jobs.join(timeout=5)


def test_an_install_still_ends_done_when_the_once_flag_cannot_be_saved(tmp_path, capsys):
    pushed = []
    settings = Settings(tmp_path / "settings.json")

    def broken_set(key, value):
        raise OSError("disk full")

    settings.set = broken_set
    api = DesktopApi(FakeProvider(), InstallState(tmp_path / "state.json"),
                     push=pushed.append, probe_fn=lambda p: Readiness(),
                     steps_factory=lambda: [Step("preflight", lambda: None)],
                     settings=settings, autostart_exe=EXE, quit_app=lambda: None)
    api.start_install(); api.jobs.join(timeout=5)
    assert pushed[-1]["type"] == "done"
    assert "disk full" in capsys.readouterr().err


def test_quit_exits_when_stopping_the_vm_hangs(tmp_path, capsys):
    release = threading.Event()

    class Hanging(FakeProvider):
        def stop(self):
            release.wait(10)

    quits = []
    api = _api(tmp_path, Hanging(), quits=quits, stop_timeout=0.1)
    try:
        api.quit(False); api._quit_thread.join(timeout=5)
        assert quits == [True]
        assert "timed out" in capsys.readouterr().err
    finally:
        release.set()
