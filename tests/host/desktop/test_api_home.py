"""The bridge's answer to "what should I draw".

The first-run rule is carried over from host/setup_app/app.py and is the one
piece of routing that is not a function of Readiness alone.
"""
from __future__ import annotations

from host.core.install import InstallState
from host.core.status import Readiness
from host.desktop.api import DesktopApi

READY = Readiness(vm_exists=True, vm_reachable=True,
                  runtime_version="runtime-v0.1.0", api_version=1)


class FakeProvider:
    pass


def _api(tmp_path, readiness):
    state = InstallState(tmp_path / "install-state.json")
    return DesktopApi(FakeProvider(), state, push=lambda event: None,
                      probe_fn=lambda provider: readiness)


def test_a_virgin_machine_is_first_run(tmp_path):
    assert _api(tmp_path, Readiness()).home()["first_run"] is True


def test_a_machine_that_got_partway_is_not_first_run(tmp_path):
    # The loop this guards: create_vm failing forever leaves vm_exists False
    # on every relaunch. Without the state check the app would re-enter the
    # wizard every time and the user could never reach Home's Doctor button.
    state = InstallState(tmp_path / "install-state.json")
    state.mark("preflight")
    api = DesktopApi(FakeProvider(), state, push=lambda event: None,
                     probe_fn=lambda provider: Readiness())
    home = api.home()
    assert home["first_run"] is False
    assert (home["route"], home["state"]) == ("home", "not_installed")


def test_a_ready_machine_is_never_first_run(tmp_path):
    assert _api(tmp_path, READY).home()["first_run"] is False


def test_home_carries_the_versions_the_odds_and_ends_row_shows(tmp_path):
    from host.core import constants
    home = _api(tmp_path, READY).home()
    assert home["app_version"] == constants.APP_VERSION
    assert home["runtime_version"] == "runtime-v0.1.0"


def test_home_passes_the_probe_problem_through_for_the_log(tmp_path):
    readiness = Readiness(vm_exists=True, vm_reachable=True,
                          runtime_version="runtime-v0.1.0",
                          problem="connection refused")
    home = _api(tmp_path, readiness).home()
    assert home["route"] == "unreachable"
    assert home["problem"] == "connection refused"


def test_home_reports_resumed_false_by_default(tmp_path):
    assert _api(tmp_path, READY).home()["resumed"] is False


def test_home_reports_resumed_when_the_bridge_was_relaunched(tmp_path):
    # __main__.run() sets this attribute after a RunOnce relaunch; home()
    # only needs to read it back.
    api = _api(tmp_path, READY)
    api.resumed = True
    assert api.home()["resumed"] is True


def test_the_resume_flag_is_consumed_by_the_first_home_call(tmp_path):
    """RunOnce relaunches with --resume after a restart and the first screen
    starts the install. If the flag survived, every later refresh would start
    it again -- and most steps are always_run, so that is an endless
    re-install the user cannot escape."""
    api = _api(tmp_path, READY)
    api.resumed = True
    assert api.home()["resumed"] is True
    assert api.home()["resumed"] is False


class RecordingProvider:
    def __init__(self):
        self.execs = []

    def exec(self, argv, *, root=False):
        from host.core.provider import Completed

        self.execs.append(argv)
        return Completed(0, "", "")


def test_home_declares_the_hosts_apis_once_the_vm_answers(tmp_path):
    from host.core import constants

    provider = RecordingProvider()
    api = DesktopApi(provider, InstallState(tmp_path / "s.json"), push=lambda e: None,
                     probe_fn=lambda p: READY)
    api.home()
    api.home()
    writes = [a for a in provider.execs if constants.HOST_JSON in a[-1]]
    assert len(writes) == 1, "once per session is enough"


def test_home_never_declares_to_a_vm_that_is_not_answering(tmp_path):
    provider = RecordingProvider()
    api = DesktopApi(provider, InstallState(tmp_path / "s.json"), push=lambda e: None,
                     probe_fn=lambda p: Readiness(vm_exists=True))
    api.home()
    assert provider.execs == []


def test_a_hidden_tray_launch_keeps_the_console_entry_for_the_first_visible_home(tmp_path):
    """A login launch draws Home in a window nobody sees, while the VM boots.
    That call must not spend the one-shot, or the user opens a running Omelet
    and is never taken into the console. Once used, it stays used."""
    shown = {"yet": False}
    state = InstallState(tmp_path / "install-state.json")
    api = DesktopApi(FakeProvider(), state, push=lambda event: None,
                     probe_fn=lambda provider: READY,
                     window_shown_once=lambda: shown["yet"])
    assert api.home()["enter_console"] is False
    assert api.home()["enter_console"] is False
    shown["yet"] = True
    assert api.home()["enter_console"] is True
    assert api.home()["enter_console"] is False
