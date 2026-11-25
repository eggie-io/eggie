"""Which agents Home shows as connected on each card, read from the files
the connect steps write (the seam with ssh_alias / claude_ssh / codex_app)."""
from __future__ import annotations

from pathlib import Path

from host.core.install import InstallState
from host.core.status import Readiness
from host.desktop.api import DesktopApi
from host.desktop.settings import AGENTS_DONE, Settings, done_flag
from host.providers import claude_ssh, codex_app, ssh_alias

UI = Path(__file__).resolve().parents[3] / "host" / "desktop" / "ui"
READY = Readiness(vm_exists=True, vm_reachable=True,
                  runtime_version="runtime-v0.1.0", api_version=1)
BAKERY = {"id": "k1", "name": "bakery", "display_name": "Bakery", "status": "running",
          "is_owner": True, "console_hostname": "bakery.eggie.app"}


ALL = ("claude-code", "codex", "cursor")


class MacLike:
    local_ssh_alias = "eggie"
    agent_connections = ("claude-code",)

    def __init__(self, installed=ALL):
        self._installed = installed

    def agent_installed(self, agent_id):
        return agent_id in self._installed

    def agent_app(self, agent_id):
        return None


class WindowsLike:
    local_ssh_alias = None
    agent_connections = ("codex",)

    def __init__(self, default=False, error=None):
        self._default, self._error = default, error

    def agent_installed(self, agent_id):
        return True

    def agent_connected(self, agent_id):
        if self._error:
            raise self._error
        return agent_id == "codex" and self._default


class Account:
    signed_in, pending, email, first_name = True, None, "a@b.c", "A"


class Cloud:
    def instances(self):
        return [BAKERY]


def _api(tmp_path, provider):
    api = DesktopApi(provider, InstallState(tmp_path / "s.json"), push=lambda e: None,
                     probe_fn=lambda p: READY, settings=Settings(tmp_path / "settings.json"),
                     cloud=(Account(), Cloud()), ui_dir=UI, home=tmp_path / "home", env={})
    api.load_kitchens()
    return api


def _chips(api):
    return {c.get("id"): c["agents"] for c in api.home()["home"]["cards"]}


def _block(home, alias):
    ssh_alias.install_host(home / ".ssh" / "config", alias, [("HostName", "h.example")])


def test_mac_local_counts_each_agents_own_entry(tmp_path):
    home = tmp_path / "home"
    _block(home, "eggie")
    claude_ssh.install(home / ".claude")
    codex_app.add(home / ".codex", "eggie", "/opt/eggie/projects", "Eggie · This computer")
    assert _chips(_api(tmp_path, MacLike()))["local"] == ["Claude Code", "ChatGPT (Codex)"]


def test_an_agent_whose_app_is_gone_is_not_connected_whatever_it_left_behind(tmp_path):
    home = tmp_path / "home"
    _block(home, "eggie")
    claude_ssh.install(home / ".claude")
    codex_app.add(home / ".codex", "eggie", "/opt/eggie/projects", "Eggie · This computer")
    api = _api(tmp_path, MacLike(installed=("claude-code",)))
    assert _chips(api)["local"] == ["Claude Code"]
    codex = next(c for c in api.agents("local")["cards"] if c["id"] == "codex")
    assert codex["state"] == "missing"


def test_an_entry_without_its_host_block_is_not_connected(tmp_path):
    home = tmp_path / "home"
    claude_ssh.install_entry(home / ".claude", claude_ssh.entry_for("eggie-bakery", "Bakery"))
    api = _api(tmp_path, MacLike())
    assert _chips(api)["k1"] == []
    _block(home, "eggie-bakery")
    assert _chips(api)["k1"] == ["Claude Code"]


def test_cursor_needs_its_host_block_and_done_for_that_kitchen(tmp_path):
    home = tmp_path / "home"
    settings = Settings(tmp_path / "settings.json")
    settings.add_member(AGENTS_DONE, done_flag("cursor", "k1"))
    api = _api(tmp_path, MacLike())
    assert _chips(api)["k1"] == []
    _block(home, "eggie-bakery")
    assert _chips(api)["k1"] == ["Cursor"]
    # Done is per kitchen: this computer's block alone is not enough.
    _block(home, "eggie")
    assert _chips(api)["local"] == []
    settings.add_member(AGENTS_DONE, done_flag("cursor", "local"))
    assert _chips(api) == {"k1": ["Cursor"], "local": ["Cursor"]}


def test_windows_local_asks_the_provider_for_codex_and_done_for_the_rest(tmp_path):
    Settings(tmp_path / "settings.json").add_member(AGENTS_DONE, done_flag("claude-code", "local"))
    assert _chips(_api(tmp_path, WindowsLike(default=True)))["local"] == ["Claude Code", "ChatGPT (Codex)"]
    assert _chips(_api(tmp_path, WindowsLike(default=False)))["local"] == ["Claude Code"]


def test_a_failing_provider_check_reads_as_not_connected(tmp_path):
    api = _api(tmp_path, WindowsLike(error=OSError("wsl.exe hung")))
    assert _chips(api)["local"] == []
