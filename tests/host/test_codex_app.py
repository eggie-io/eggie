"""Eggie's SSH connections in the Codex app's codex-app/config.json.

Codex imports them on its next start. Eggie owns the connections it adds, by
alias, and leaves every other key and connection as it was.
"""
import json
import os
from pathlib import Path

import pytest

from host.providers import codex_app

OTHER = {"sshAlias": "work", "projects": [{"remotePath": "/srv", "label": "Work"}]}


def _home(tmp_path, data=None, raw=None):
    home = tmp_path / ".codex"
    (home / "codex-app").mkdir(parents=True)
    if data is not None:
        raw = json.dumps(data)
    if raw is not None:
        (home / "codex-app" / "config.json").write_text(raw)
    return home


def _read(home):
    return json.loads(codex_app.config_path(home).read_text())


def test_add_keeps_unknown_keys_and_other_connections(tmp_path):
    home = _home(tmp_path, {"version": 1, "theme": "dark", "remoteConnections": [OTHER]})
    assert codex_app.add(home, "eggie-foo", "/opt/eggie/projects/", "Eggie · Foo")
    assert _read(home) == {"version": 1, "theme": "dark", "remoteConnections": [
        OTHER,
        {"sshAlias": "eggie-foo", "projects": [
            {"remotePath": "/opt/eggie/projects", "label": "Eggie · Foo"}]}]}


def test_add_on_a_missing_file_creates_it(tmp_path):
    home = tmp_path / ".codex"
    assert codex_app.add(home, "eggie-foo", "/p", "L")
    assert codex_app.has(home, "eggie-foo")


def test_add_twice_writes_once(tmp_path):
    home = _home(tmp_path)
    codex_app.add(home, "eggie-foo", "/p", "L")
    path = codex_app.config_path(home)
    os.utime(path, (1, 1))
    assert codex_app.add(home, "eggie-foo", "/p", "L") is False
    assert path.stat().st_mtime == 1


def test_user_label_survives_re_add(tmp_path):
    home = _home(tmp_path)
    codex_app.add(home, "eggie-foo", "/p", "L")
    data = _read(home)
    data["remoteConnections"][0]["projects"][0]["label"] = "Mine"
    codex_app.config_path(home).write_text(json.dumps(data))
    assert codex_app.add(home, "eggie-foo", "/p", "L") is False
    assert _read(home)["remoteConnections"][0]["projects"][0]["label"] == "Mine"


@pytest.mark.parametrize("raw", [
    "{not json", "[]",
    '{"version": 2, "remoteConnections": []}',
    '{"version": 1, "remoteConnections": {}}',
])
def test_refuses_version_2_and_bad_json(tmp_path, raw):
    home = _home(tmp_path, raw=raw)
    with pytest.raises(ValueError):
        codex_app.add(home, "eggie-foo", "/p", "L")
    assert codex_app.config_path(home).read_text() == raw


def test_remove_only_drops_our_alias(tmp_path):
    home = _home(tmp_path, {"version": 1, "remoteConnections": [OTHER]})
    codex_app.add(home, "eggie-foo", "/p", "L")
    assert codex_app.remove(home, "eggie-foo")
    assert _read(home)["remoteConnections"] == [OTHER]
    assert codex_app.remove(home, "eggie-foo") is False


def test_remove_and_has_on_a_missing_file_create_nothing(tmp_path):
    home = tmp_path / ".codex"
    assert codex_app.remove(home, "eggie-foo") is False
    assert codex_app.has(home, "eggie-foo") is False
    assert not home.exists()


def test_codex_home_env_wins_when_set_and_not_empty(tmp_path):
    assert codex_app.default_codex_home({"CODEX_HOME": "/x/c"}, tmp_path) == Path("/x/c")
    assert codex_app.default_codex_home({"CODEX_HOME": ""}, tmp_path) == tmp_path / ".codex"
    assert codex_app.default_codex_home({}, tmp_path) == tmp_path / ".codex"
