"""Connect an agent (3a–3d) through the bridge: which kitchens it offers, and
how one connect job's rows and ending reach the page."""
from __future__ import annotations

from pathlib import Path

from host.client import ApiUnavailableError
from host.core.install import InstallState
from host.core.status import Readiness
from host.desktop.api import DesktopApi
from host.desktop.settings import AGENTS_DONE, Settings, done_flag

UI = Path(__file__).resolve().parents[3] / "host" / "desktop" / "ui"
READY = Readiness(vm_exists=True, vm_reachable=True,
                  runtime_version="runtime-v0.1.0", api_version=1)
NO_VM = Readiness()
SHOP = {"id": "k1", "name": "shop", "display_name": "Shop", "status": "running",
        "is_owner": True, "console_hostname": "shop.eggie.app", "login_name": "ihor"}
THEIRS = dict(SHOP, id="k2", name="team", display_name="Team", is_owner=False)


class WindowsLike:
    local_ssh_alias = None
    agent_connections = ("codex",)
    guide_os = "windows"

    def __init__(self, order):
        self.order = order

    def agent_installed(self, agent_id):
        return agent_id == "codex"

    def agent_app(self, agent_id):
        return None

    def agent_connected(self, agent_id):
        return False

    def connect_agent(self, agent_id):
        self.order.append(("connect", agent_id))
        return "eggie-vm is now your default WSL distro."

    def computer_name(self):
        return "Ihor's PC"


class Account:
    signed_in, pending, email, first_name = True, None, "a@b.c", "A"


class Cloud:
    def __init__(self, rows, later=None):
        self.rows, self.later, self.calls = rows, later, 0

    def instances(self):
        self.calls += 1
        if self.later and self.calls > 1:
            raise self.later
        return self.rows


class VmClient:
    def __init__(self, order, error=None):
        self.order, self.error = order, error

    def agent_setup(self, agent_id):
        self.order.append(("setup", agent_id))
        if self.error:
            raise self.error
        return {"requested": True}


def _api(tmp_path, *, cloud=None, readiness=READY, client_error=None, opened=None):
    order, pushed = [], []
    api = DesktopApi(WindowsLike(order), InstallState(tmp_path / "s.json"), push=pushed.append,
                     probe_fn=lambda p: readiness, settings=Settings(tmp_path / "settings.json"),
                     cloud=(Account(), cloud or Cloud([SHOP, THEIRS])), ui_dir=UI,
                     home=tmp_path / "home", env={},
                     client_factory=lambda p: VmClient(order, client_error),
                     open_url=(opened if opened is not None else []).append)
    return api, order, pushed


def _run(api, pushed, agent, kitchen):
    started = api.start_connect(agent, kitchen)
    assert started["ok"], started
    api.jobs.join(5)
    events = [e for e in pushed if e["kind"] == "connect"]
    return started, [(e["step"], e["status"]) for e in events if e["type"] == "step"], events[-1]


def test_connect_to_never_offers_a_kitchen_someone_else_owns(tmp_path):
    api, order, _ = _api(tmp_path)

    assert [t["id"] for t in api.agents("k1")["targets"]] == ["k1", "local"]
    assert api.agents("k2") == {"ok": False}
    assert api.start_connect("cursor", "k2")["ok"] is False
    assert not api.jobs.running()


def test_this_computer_is_offered_only_once_it_has_a_vm(tmp_path):
    api, _, _ = _api(tmp_path, readiness=NO_VM)

    assert [t["id"] for t in api.agents("k1")["targets"]] == ["k1"]
    assert api.start_connect("codex", "local")["ok"] is False


def test_codex_local_runs_vm_setup_first(tmp_path):
    api, order, pushed = _api(tmp_path)

    started, rows, end = _run(api, pushed, "codex", "local")

    assert order == [("setup", "codex"), ("connect", "codex")]
    assert started["rows"] == ["access", "agent", "entry"]
    assert rows == [("access", "running"), ("access", "done"), ("agent", "running"),
                    ("agent", "done"), ("entry", "running"), ("entry", "done")]
    assert end["type"] == "done"
    assert end["steps"][0]["title"] == "Run Codex in WSL"


def test_a_failed_vm_setup_fails_the_agent_row(tmp_path):
    api, order, pushed = _api(tmp_path, client_error=ApiUnavailableError("the VM is not answering"))

    _, rows, end = _run(api, pushed, "codex", "local")

    assert rows[-1] == ("agent", "failed")
    assert end["type"] == "failed" and end["message"] == "the VM is not answering"
    assert ("connect", "codex") not in order


def test_failed_access_fails_the_job_with_its_line(tmp_path):
    cloud = Cloud([SHOP], later=ApiUnavailableError("could not reach app.eggie.io"))
    api, _, pushed = _api(tmp_path, cloud=cloud)
    api.load_kitchens()

    started, rows, end = _run(api, pushed, "cursor", "k1")

    assert started["rows"] == ["access", "check"]
    assert rows == [("access", "running"), ("access", "failed")]
    assert end["type"] == "failed"
    assert end["message"] == "Could not load your kitchens: could not reach app.eggie.io"
    assert end["lines"] == [f"✗ Kitchens: {end['message']}"]


def test_done_is_kept_only_for_a_known_agent_and_an_owned_kitchen(tmp_path):
    api, _, _ = _api(tmp_path)
    api.load_kitchens()

    assert api.mark_done("cursor", "k1") == {"ok": True}
    assert api.mark_done("cursor", "k2") == {"ok": False}
    assert api.mark_done("../cursor", "local") == {"ok": False}
    assert Settings(tmp_path / "settings.json").members(AGENTS_DONE) == {done_flag("cursor", "k1")}


def test_get_agent_opens_only_the_manifests_own_page(tmp_path):
    opened = []
    api, _, _ = _api(tmp_path, opened=opened)

    assert api.open_agent_download("cursor") == {"ok": True}
    assert api.open_agent_download("https://evil.example")["ok"] is False
    assert opened == ["https://cursor.com/download"]
