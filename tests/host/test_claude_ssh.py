"""The "Eggie" SSH connection macOS setup adds to Claude Code's settings.

Claude Code keeps SSH connections in `sshConfigs` of ~/.claude/settings.json.
Eggie owns one entry, by id, and leaves the rest of the file alone.
"""
import json

import pytest

from host.providers import claude_ssh
from host.providers.lima import LimaProvider

USER_SETTINGS = {
    "model": "opus",
    "permissions": {"allow": ["Bash(ls:*)"]},
    "sshConfigs": [{"id": "work", "name": "Work", "sshHost": "work"}],
}


def _claude(tmp_path, settings=None):
    claude = tmp_path / ".claude"
    claude.mkdir()
    if settings is not None:
        (claude / "settings.json").write_text(json.dumps(settings, indent=2))
    return claude


def _read(claude):
    return json.loads((claude / "settings.json").read_text())


def test_the_entry_points_at_the_eggie_ssh_alias(tmp_path):
    claude = _claude(tmp_path)
    assert claude_ssh.install(claude)
    assert _read(claude) == {"sshConfigs": [{
        "id": "eggie", "name": "Eggie", "sshHost": "eggie",
        "startDirectory": "/opt/eggie/projects"}]}
    assert claude_ssh.installed(claude)


def test_the_users_settings_and_connections_are_kept(tmp_path):
    claude = _claude(tmp_path, USER_SETTINGS)
    claude_ssh.install(claude)
    data = _read(claude)
    assert data["model"] == "opus"
    assert data["permissions"] == USER_SETTINGS["permissions"]
    assert [e["id"] for e in data["sshConfigs"]] == ["work", "eggie"]


def test_installing_again_replaces_the_entry_in_place(tmp_path):
    stale = {"id": "eggie", "name": "Old", "sshHost": "old"}
    later = {"id": "later", "name": "Later", "sshHost": "later"}
    claude = _claude(tmp_path, {"sshConfigs": [stale, later]})
    claude_ssh.install(claude)
    configs = _read(claude)["sshConfigs"]
    assert configs == [claude_ssh.ENTRY, later]


def test_nothing_is_written_without_claude_code(tmp_path):
    assert not claude_ssh.install(tmp_path / ".claude")
    assert not (tmp_path / ".claude").exists()


@pytest.mark.parametrize("text", ["{not json", "[]", '{"sshConfigs": {}}'])
def test_a_file_eggie_cannot_read_is_left_alone(tmp_path, text):
    claude = _claude(tmp_path)
    (claude / "settings.json").write_text(text)
    with pytest.raises(ValueError):
        claude_ssh.install(claude)
    assert (claude / "settings.json").read_text() == text
    assert not claude_ssh.installed(claude)


def test_a_new_settings_file_is_private(tmp_path):
    claude = _claude(tmp_path)
    claude_ssh.install(claude)
    assert (claude / "settings.json").stat().st_mode & 0o777 == 0o600
    assert [f.name for f in claude.iterdir()] == ["settings.json"]   # no temp left


def test_a_symlinked_settings_file_is_written_through(tmp_path):
    real = tmp_path / "dotfiles" / "claude.json"
    real.parent.mkdir()
    real.write_text(json.dumps(USER_SETTINGS))
    claude = _claude(tmp_path)
    (claude / "settings.json").symlink_to(real)
    claude_ssh.install(claude)
    assert (claude / "settings.json").is_symlink()
    assert "eggie" in [e["id"] for e in json.loads(real.read_text())["sshConfigs"]]


def test_remove_restores_the_users_connections(tmp_path):
    claude = _claude(tmp_path, USER_SETTINGS)
    claude_ssh.install(claude)
    claude_ssh.remove(claude)
    assert _read(claude) == USER_SETTINGS


def test_remove_drops_a_list_eggie_made(tmp_path):
    claude = _claude(tmp_path, {"model": "opus"})
    claude_ssh.install(claude)
    claude_ssh.remove(claude)
    assert _read(claude) == {"model": "opus"}


def test_remove_without_an_install_changes_nothing(tmp_path):
    claude = _claude(tmp_path, USER_SETTINGS)
    before = (claude / "settings.json").read_text()
    claude_ssh.remove(claude)
    claude_ssh.remove(tmp_path / "absent")
    assert (claude / "settings.json").read_text() == before


# --- wired into the Lima provider ---

def test_the_provider_looks_next_to_the_ssh_dir_it_was_given(tmp_path):
    provider = LimaProvider(runner=lambda argv: None, ssh_dir=tmp_path / ".ssh")
    assert provider.claude_dir == tmp_path / ".claude"
