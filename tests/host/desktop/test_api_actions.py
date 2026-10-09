"""The remaining buttons, and the one that deletes things."""
from __future__ import annotations

from host.core.install import InstallState
from host.core.provider import CheckResult, Diagnosis
from host.core.status import Readiness
from host.desktop.api import DesktopApi


class FakeProvider:
    def __init__(self):
        self.started = self.stopped = False
        self.destroyed_with = None

    def preflight(self):
        return Diagnosis([CheckResult("Virtualization enabled", True),
                          CheckResult("WSL2 installed", False,
                                      fix="Run: wsl --install")])

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True


def _api(tmp_path, provider, pushed=None):
    return DesktopApi(provider, InstallState(tmp_path / "s.json"),
                      push=(pushed.append if pushed is not None else lambda e: None),
                      probe_fn=lambda p: Readiness())


def test_doctor_renders_the_diagnosis_for_the_log_pane(tmp_path):
    report = _api(tmp_path, FakeProvider()).doctor()
    assert report["ok"] is False
    assert "wsl --install" in report["text"]


def test_restart_stops_before_it_starts(tmp_path):
    order = []

    class Recording(FakeProvider):
        def stop(self): order.append("stop")
        def start(self): order.append("start")

    provider = Recording()
    api = _api(tmp_path, provider)
    api.restart_vm()
    api.jobs.join(timeout=5)
    assert order == ["stop", "start"]


def test_stopping_the_kitchen_is_a_job_not_a_blocking_call(tmp_path):
    # provider.stop() shells wsl.exe/limactl and takes seconds; running it on
    # the thread that owns the window freezes the app mid-click.
    provider = FakeProvider()
    api = _api(tmp_path, provider)
    assert "job" in api.stop_vm()
    api.jobs.join(timeout=5)
    assert provider.stopped is True


def test_uninstall_removes_the_vm_directory_but_keeps_downloads(tmp_path):
    """Purge is the app's second level: without it the cached image and the
    managed runtime survive, because they are disk space rather than state
    and re-downloading them costs hundreds of megabytes."""
    root = tmp_path / "eggie"
    install_dir = root / "vm"
    install_dir.mkdir(parents=True)
    (install_dir / "disk.vhdx").write_text("x")
    (root / "cache").mkdir()
    (root / "cache" / "ubuntu.wsl").write_text("x")
    (root / "lima").mkdir()
    (root / "install-state.json").write_text('{"completed": ["preflight"]}')

    destroyed = []

    class Provider:
        def running(self):
            return False

        def destroy(self):
            destroyed.append(True)

    events = []
    api = DesktopApi(Provider(), InstallState(tmp_path / "s.json"),
                     push=events.append, probe_fn=lambda p: Readiness(),
                     install_dir_factory=lambda: install_dir)
    api.start_uninstall(False)
    api.jobs.join(timeout=5)

    # The job must have finished, not crashed: JobRegistry swallows worker
    # exceptions into a `crashed` event, so asserting only on side effects
    # would pass against a job that died halfway.
    assert events[-1]["type"] == "done", events
    assert destroyed == [True]
    assert not install_dir.exists()
    assert not (root / "install-state.json").exists()
    assert (root / "cache" / "ubuntu.wsl").exists()
    assert (root / "lima").exists()


def test_uninstall_refuses_a_running_kitchen(tmp_path):
    """Lima will not delete a running instance; its raw `expected status
    Stopped, got Running` is no use to the user. Nothing is touched."""
    install_dir = tmp_path / "eggie" / "vm"
    install_dir.mkdir(parents=True)
    destroyed = []

    class Provider:
        def running(self):
            return True

        def destroy(self):
            destroyed.append(True)

    events = []
    api = DesktopApi(Provider(), InstallState(tmp_path / "s.json"),
                     push=events.append, probe_fn=lambda p: Readiness(),
                     install_dir_factory=lambda: install_dir)
    api.start_uninstall(False)
    api.jobs.join(timeout=5)

    assert events[-1]["type"] == "crashed", events
    assert "Stop the kitchen" in events[-1]["message"]
    assert destroyed == []
    assert install_dir.exists()


def test_purge_also_removes_the_downloads(tmp_path):
    root = tmp_path / "eggie"
    install_dir = root / "vm"
    install_dir.mkdir(parents=True)
    (root / "cache").mkdir()
    (root / "cache" / "ubuntu.wsl").write_text("x")
    (root / "lima").mkdir()

    class Provider:
        def running(self):
            return False

        def destroy(self):
            pass

    events = []
    api = DesktopApi(Provider(), InstallState(tmp_path / "s.json"),
                     push=events.append, probe_fn=lambda p: Readiness(),
                     install_dir_factory=lambda: install_dir)
    api.start_uninstall(True)
    api.jobs.join(timeout=5)

    assert events[-1]["type"] == "done", events
    assert not (root / "cache").exists()
    assert not (root / "lima").exists()


def test_uninstall_touches_nothing_outside_the_injected_directory(tmp_path, monkeypatch):
    """A regression here deletes a real user's VM when the suite runs."""
    def explode():
        raise AssertionError("uninstall resolved the real install directory")

    monkeypatch.setattr("host.providers.default_install_dir", explode)

    root = tmp_path / "eggie"
    (root / "vm").mkdir(parents=True)

    class Provider:
        def running(self):
            return False

        def destroy(self):
            pass

    events = []
    api = DesktopApi(Provider(), InstallState(tmp_path / "s.json"),
                     push=events.append, probe_fn=lambda p: Readiness(),
                     install_dir_factory=lambda: root / "vm")
    api.start_uninstall(True)
    api.jobs.join(timeout=5)
    assert events[-1]["type"] == "done", events


class Recovering(FakeProvider):
    recover_warning = "other things stop too"

    def __init__(self, fails=()):
        super().__init__()
        self.recovered = []
        self._fails = fails

    def recover(self, *, everything=False):
        from host.core.provider import VmUnresponsive
        self.recovered.append(everything)
        if everything in self._fails:
            raise VmUnresponsive("still hung")


def _recover(tmp_path, provider, everything):
    pushed = []
    api = _api(tmp_path, provider, pushed)
    api.recover_vm(everything)
    api.jobs.join(timeout=5)
    return pushed[-1]


def test_a_recovery_that_did_not_help_asks_before_going_wider(tmp_path):
    # Going wider stops everything else on the same platform, so the UI must
    # get the warning to show rather than a crash notice.
    event = _recover(tmp_path, Recovering(fails=(False,)), False)
    assert event["type"] == "unresponsive"
    assert event["everything"] is False
    assert event["warning"] == "other things stop too"


def test_recovery_passes_through_how_wide_to_go(tmp_path):
    provider = Recovering()
    assert _recover(tmp_path, provider, True)["type"] == "done"
    assert provider.recovered == [True]


def test_the_runtime_update_job_reports_failure_in_the_installers_words(tmp_path, monkeypatch):
    from host.core import install
    from host.core.install import ApiIncompatible

    def failing(provider):
        raise ApiIncompatible("no runtime-v* release speaks api 2")

    monkeypatch.setattr(install, "connect_with_updates", failing)
    pushed = []
    api = _api(tmp_path, FakeProvider(), pushed)
    api.start_runtime_update()
    api.jobs.join(timeout=5)
    assert pushed[-1]["kind"] == "runtime_update"
    assert pushed[-1]["type"] == "crashed"
    assert "speaks api 2" in pushed[-1]["message"]


def test_a_successful_runtime_update_goes_into_the_console(tmp_path, monkeypatch):
    from host.core import install

    monkeypatch.setattr(install, "connect_with_updates", lambda provider: None)
    ready = Readiness(vm_exists=True, vm_reachable=True,
                      runtime_version="runtime-v0.2.0", api_version=1)
    api = DesktopApi(FakeProvider(), InstallState(tmp_path / "s.json"), push=lambda e: None,
                     probe_fn=lambda p: ready)
    api.home()
    api.start_runtime_update()
    api.jobs.join(timeout=5)
    assert api.home()["enter_console"] is True
    assert api.home()["enter_console"] is False


def test_doctor_names_the_log_file(tmp_path):
    """The modal is what a user reads out to whoever helps them; it has to
    say where the file with the actual failures is."""
    api = DesktopApi(FakeProvider(), InstallState(tmp_path / "s.json"),
                     push=lambda e: None, probe_fn=lambda p: Readiness(),
                     log_path=tmp_path / "eggie.log")
    assert str(tmp_path / "eggie.log") in api.doctor()["text"]
