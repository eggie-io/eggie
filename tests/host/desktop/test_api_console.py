"""Entering the projects console inside the window."""
from __future__ import annotations

import threading

from host.core import constants
from host.core.install import InstallState
from host.core.status import Readiness
from host.desktop.api import DesktopApi

READY = Readiness(vm_exists=True, vm_reachable=True,
                  runtime_version="runtime-v0.1.0", api_version=1)
STOPPED = Readiness(vm_exists=True)


class FakeProvider:
    pass


class HandoffClient:
    def __init__(self, code=None, error=None):
        self._code, self._error = code, error

    def handoff_code(self):
        if self._error:
            raise self._error
        return self._code


def _api(tmp_path, *, readiness=READY, client=None, home=None):
    return DesktopApi(FakeProvider(), InstallState(tmp_path / "s.json"),
                      push=lambda event: None,
                      probe_fn=lambda provider: readiness,
                      client_factory=lambda provider: client or HandoffClient(code="abc"),
                      local_url=lambda: home)


def test_entering_hands_back_the_edge_port_with_the_handoff_code(tmp_path):
    assert _api(tmp_path).enter_console() == {
        "ok": True, "url": f"http://localhost:{constants.EDGE_PORT}/#handoff=abc"}


def test_the_console_is_told_where_home_is(tmp_path):
    # The console's Home button navigates here; the address travels encoded
    # so its own "&" or "#" cannot end the fragment early.
    home = "http://127.0.0.1:53817/index.html"
    assert _api(tmp_path, home=home).enter_console()["url"] == (f"http://localhost:{constants.EDGE_PORT}/#handoff=abc"
                      f"&home=http%3A%2F%2F127.0.0.1%3A53817%2Findex.html")


def test_a_failed_handoff_gives_no_address(tmp_path):
    # Inside the app the console's signed-out screen says "open this from the
    # desktop app" -- a dead end for someone already in it.
    client = HandoffClient(error=ConnectionRefusedError("refused"))
    result = _api(tmp_path, client=client).enter_console()
    assert result["ok"] is False
    assert "refused" in result["message"]
    assert "url" not in result


def test_entering_is_refused_while_a_job_runs(tmp_path):
    api = _api(tmp_path)
    release = threading.Event()
    api.jobs.start("import", lambda emit: release.wait(5) and {"type": "done"})
    try:
        assert "url" not in api.enter_console()
    finally:
        release.set()
        api.jobs.join(5)


def test_a_running_machine_enters_the_console_at_launch(tmp_path):
    assert _api(tmp_path).home()["action"] == "enter_console"


def test_the_launch_flag_is_spent_by_the_first_home_call(tmp_path):
    # The console's Home link reloads the local UI, which calls home() again;
    # a second True would bounce the user straight back into the console.
    api = _api(tmp_path)
    api.home()
    assert api.home()["action"] != "enter_console"


def test_a_stopped_machine_does_not_enter_and_still_spends_the_flag(tmp_path):
    api = _api(tmp_path, readiness=STOPPED)
    assert api.home()["action"] != "enter_console"
    api._probe = lambda provider: READY
    assert api.home()["action"] != "enter_console"


def test_a_resumed_install_never_enters_the_console(tmp_path):
    api = _api(tmp_path)
    api.resumed = True
    assert api.home()["action"] != "enter_console"
