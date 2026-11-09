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


# --- the agent guide's "connect for me" round trip ---

class ConnectingProvider:
    agent_connections = ("claude-code",)

    def __init__(self, error=None, added=False):
        self.connected = []
        self._error = error
        self._added = added

    def agent_connected(self, agent_id):
        if isinstance(self._added, Exception):
            raise self._added
        return self._added

    def connect_agent(self, agent_id):
        if self._error:
            raise self._error
        self.connected.append(agent_id)
        return "Claude Code now lists Eggie as an SSH connection."


def _connecting(tmp_path, provider, home="http://127.0.0.1:53817/"):
    return DesktopApi(provider, InstallState(tmp_path / "s.json"),
                      push=lambda event: None, probe_fn=lambda p: READY,
                      client_factory=lambda p: HandoffClient(code="abc"),
                      local_url=lambda: home)


def test_the_console_is_told_which_agents_the_window_can_connect(tmp_path):
    url = _connecting(tmp_path, ConnectingProvider()).enter_console()["url"]
    assert url.endswith("&connect=claude-code")


def test_the_console_is_told_which_agents_have_the_connection_already(tmp_path):
    url = _connecting(tmp_path, ConnectingProvider(added=True)).enter_console()["url"]
    assert url.endswith("&connect=claude-code&added=claude-code")


def test_a_failed_check_still_opens_the_console(tmp_path):
    api = _connecting(tmp_path, ConnectingProvider(added=OSError("permission denied")))
    result = api.enter_console()
    assert result["ok"] is True
    assert "added=" not in result["url"]


def test_no_window_address_means_no_connect_offer(tmp_path):
    # Without Home the console has no way back here, so it must not offer it.
    url = _connecting(tmp_path, ConnectingProvider(), home=None).enter_console()["url"]
    assert "connect=" not in url


def test_coming_back_opens_the_guide_it_came_from(tmp_path):
    api = _connecting(tmp_path, ConnectingProvider())
    url = api.enter_console("/agents/claude-code")["url"]
    assert url.startswith(f"http://localhost:{constants.EDGE_PORT}/agents/claude-code#handoff=abc")


def test_a_path_that_is_not_a_guide_opens_the_front_page(tmp_path):
    api = _connecting(tmp_path, ConnectingProvider())
    for back in ("//evil.example/x", "/agents/../p", "https://evil.example", "/p/x"):
        assert api.enter_console(back)["url"].startswith(
            f"http://localhost:{constants.EDGE_PORT}/#handoff="), back


def test_connecting_asks_the_provider(tmp_path):
    provider = ConnectingProvider()
    api = _connecting(tmp_path, provider)
    assert api.agent_connection("claude-code") == {"ok": True, "agent": "claude-code",
                                                   "claude-code": True}
    assert api.connect_agent("claude-code")["ok"] is True
    assert provider.connected == ["claude-code"]


def test_an_agent_the_provider_does_not_offer_is_refused(tmp_path):
    provider = ConnectingProvider()
    api = _connecting(tmp_path, provider)
    for agent in ("codex", "claude-code&x", None, 3):
        assert api.agent_connection(agent)["ok"] is False
        assert api.connect_agent(agent)["ok"] is False
    assert provider.connected == []


def test_a_failed_connect_reports_why(tmp_path):
    api = _connecting(tmp_path, ConnectingProvider(error=ValueError("settings.json is not valid JSON")))
    result = api.connect_agent("claude-code")
    assert result == {"ok": False, "message": "settings.json is not valid JSON"}


def test_connecting_waits_for_a_running_job(tmp_path):
    provider = ConnectingProvider()
    api = _connecting(tmp_path, provider)
    release = threading.Event()
    api.jobs.start("repair", lambda emit: release.wait(5) and {"type": "done"})
    try:
        assert api.connect_agent("claude-code")["ok"] is False
        assert provider.connected == []
    finally:
        release.set()
        api.jobs.join(5)
