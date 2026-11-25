"""3a's agent cards and Home's agent chips."""
from __future__ import annotations

from host.desktop.agents import Manifest
from host.desktop.view import agent_cards, connect_targets, home_view


def _manifest(agent_id, name):
    return Manifest(id=agent_id, name=name, icon=f"agents/{agent_id}/icon.webp",
                    get_url="https://example.com", connect=None, steps={})


MANIFESTS = [_manifest("claude-code", "Claude Code"), _manifest("codex", "Codex"),
             _manifest("cursor", "Cursor")]


def test_card_states():
    # Connected beats installed beats missing; an agent no fact names is missing.
    cards = agent_cards(MANIFESTS, kind="cloud", alias="eggie-bakery",
                        installed={"claude-code": False, "codex": True},
                        connected={"claude-code": True, "codex": False, "cursor": False})
    assert [(c["id"], c["state"]) for c in cards] == [
        ("claude-code", "connected"), ("codex", "installed"), ("cursor", "missing")]
    assert cards[0]["ssh_host"] == "eggie-bakery"


def test_this_computer_on_windows_has_no_ssh_host():
    [card, *_] = agent_cards(MANIFESTS, kind="local", alias=None, installed={}, connected={})
    assert card["ssh_host"] == ""


def test_home_cards_carry_their_own_agent_chips():
    rows = [{"id": "k1", "name": "a", "status": "running", "is_owner": True},
            {"id": "k2", "name": "b", "status": "running", "is_owner": True}]
    view = home_view(rows, "", "running", True,
                     agents={"k1": ["Codex"], "local": ["Claude Code", "Cursor"]})
    assert [(c.get("id"), c["agents"]) for c in view["cards"]] == [
        ("k1", ["Codex"]), ("k2", []), ("local", ["Claude Code", "Cursor"])]


def test_connect_to_lists_owned_kitchens_and_disables_those_not_running():
    rows = [{"id": "k1", "name": "a", "status": "running", "is_owner": True},
            {"id": "k2", "name": "b", "status": "stopped", "is_owner": True},
            {"id": "k3", "name": "c", "status": "running", "is_owner": False}]
    targets = connect_targets(rows, "stopped")
    assert [(t["id"], t["state"], t["enabled"]) for t in targets] == [
        ("k1", "running", True), ("k2", "stopped", False), ("local", "stopped", False)]
    assert [t["id"] for t in connect_targets(rows, "none")] == ["k1", "k2"]


def test_signed_in_shows_only_on_an_installed_card():
    cards = agent_cards(MANIFESTS, kind="cloud", alias="eggie-a",
                        installed={"codex": True, "claude-code": True},
                        connected={"claude-code": True}, signed_in={"codex": True, "claude-code": True})
    assert [c["signed_in"] for c in cards] == [False, True, False]
