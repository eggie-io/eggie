"""The install job as the bridge runs it."""
from __future__ import annotations

import threading

from host.core.install import DeadEnd, InstallError, InstallState, RebootRequired, Step
from host.core.provider import ProviderError
from host.core.status import Readiness
from host.desktop.api import DesktopApi


class FakeProvider:
    pass


def _api(tmp_path, steps, pushed):
    return DesktopApi(FakeProvider(), InstallState(tmp_path / "s.json"),
                      push=pushed.append,
                      probe_fn=lambda provider: Readiness(),
                      steps_factory=lambda: steps)


def _drain(api):
    api.jobs.join(timeout=5)


def test_start_install_hands_back_the_rows_to_draw(tmp_path):
    steps = [Step("preflight", lambda: None), Step("finish", lambda: None)]
    started = _api(tmp_path, steps, []).start_install()
    assert [r["name"] for r in started["rows"]] == ["preflight", "finish"]
    assert "job" in started


def test_a_successful_install_ends_in_done(tmp_path):
    pushed = []
    api = _api(tmp_path, [Step("preflight", lambda: None)], pushed)
    api.start_install()
    _drain(api)
    assert pushed[-1]["type"] == "done"
    assert [e["type"] for e in pushed if e["type"] == "step"]


def test_a_failing_step_ends_in_failed_carrying_its_action(tmp_path):
    def boom():
        raise RuntimeError("connection reset")

    pushed = []
    api = _api(tmp_path, [Step("fetch_image", boom, action="Try again later.")], pushed)
    api.start_install()
    _drain(api)

    assert pushed[-1]["type"] == "failed"
    assert pushed[-1]["action"] == "Try again later."


def test_the_steps_factory_is_called_per_run_not_once(tmp_path):
    # The steps close over provider state -- provider.rootfs is assigned while
    # the list is built -- so a second run against the first run's list would
    # install against stale bindings.
    calls = []

    def factory():
        calls.append(1)
        return [Step("preflight", lambda: None)]

    api = DesktopApi(FakeProvider(), InstallState(tmp_path / "s.json"),
                     push=lambda e: None, probe_fn=lambda p: Readiness(),
                     steps_factory=factory)
    api.start_install()
    _drain(api)
    api.start_install()
    _drain(api)
    assert len(calls) == 2


def test_resume_is_registered_before_the_machine_goes_down(tmp_path):
    """A reboot fired before RunOnce is written never comes back to setup."""
    order = []

    class RebootingProvider:
        def register_resume(self, exe_path):
            order.append("register")

        def reboot(self):
            order.append("reboot")

    api = DesktopApi(RebootingProvider(), InstallState(tmp_path / "s.json"),
                     push=lambda e: None, probe_fn=lambda p: Readiness(),
                     steps_factory=lambda: [])
    api.reboot_now()

    assert order == ["register", "reboot"]


def _reporting(tmp_path, steps, events, clock=None):
    kwargs = {"clock": clock} if clock else {}
    api = DesktopApi(FakeProvider(), InstallState(tmp_path / "s.json"),
                     push=lambda e: None, probe_fn=lambda p: Readiness(),
                     steps_factory=lambda: steps, events=events.sender, **kwargs)
    api.start_install()
    _drain(api)
    return events.sent()


def _raises(exc):
    def run():
        raise exc
    return run


def test_failed_step_reports_its_name_and_code(tmp_path, events):
    steps = [Step("preflight", lambda: None),
             Step("create_vm", _raises(ProviderError("vm_import_failed", "import failed")))]
    sent = _reporting(tmp_path, steps, events)
    assert [name for name, _ in sent] == ["env_setup_started", "env_setup_step_failed"]
    assert sent[1][1] == {"step": "create_vm", "error_code": "vm_import_failed"}


def test_a_dead_end_reports_as_preflight_with_its_checks_code(tmp_path, events):
    steps = [Step("preflight", _raises(DeadEnd("Turn on virtualization", "cpu_virtualization_off")))]
    sent = _reporting(tmp_path, steps, events)
    assert sent[-1] == ("env_setup_step_failed",
                        {"step": "preflight", "error_code": "cpu_virtualization_off"})


def test_reboot_reports_neither_failure_nor_success(tmp_path, events):
    steps = [Step("preflight", lambda: None), Step("reboot_gate", _raises(RebootRequired()))]
    assert _reporting(tmp_path, steps, events) == [("env_setup_started", {})]


def test_success_reports_whole_seconds(tmp_path, events):
    times = iter([100.0, 142.7])
    sent = _reporting(tmp_path, [Step("preflight", lambda: None)], events,
                      clock=lambda: next(times))
    assert sent == [("env_setup_started", {}), ("env_setup_succeeded", {"duration_sec": 42})]
