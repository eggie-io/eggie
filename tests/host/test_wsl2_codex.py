"""Codex on this computer's kitchen: Eggie opens it on the projects folder and
marks that folder trusted in Codex's config.toml."""
from __future__ import annotations

import tomllib

import pytest

from host.providers import codex_trust
from host.providers.wsl2 import Wsl2Provider


def _no_wsl(argv):
    raise AssertionError(f"ran {argv}")


def make(opened=None, spawned=None, env=None):
    return Wsl2Provider(distro="eggie-vm", runner=_no_wsl,
                        opener=(opened if opened is not None else []).append,
                        spawner=(spawned if spawned is not None else []).append,
                        env=env or {"CODEX_HOME": "/dev/null/codex"})


def test_codex_opens_on_the_projects_folder_only_for_this_computers_kitchen(tmp_path):
    app = tmp_path / "Microsoft" / "WindowsApps" / "OpenAI.Codex_2p2nqsd0c76g0" / "chatgpt-desktop.exe"
    app.parent.mkdir(parents=True)
    app.write_bytes(b"")
    opened, spawned = [], []
    provider = make(opened, spawned,
                    env={"LOCALAPPDATA": str(tmp_path), "CODEX_HOME": "/dev/null/codex"})

    assert provider.open_agent("codex", local=True) is True
    assert provider.open_agent("codex") is True

    assert opened == ["codex://threads/new?path=%5C%5Cwsl.localhost%5Ceggie-vm"
                      "%5Copt%5Ceggie%5Cprojects"]
    assert spawned == [[str(app)]]


# --- config.toml ------------------------------------------------------------

FOLDERS = ["/opt/eggie/projects"]


def test_trust_appends_and_keeps_everything_already_there(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text('model = "gpt-5"  # mine\n\n[projects."/srv"]\ntrust_level = "untrusted"\n')

    assert codex_trust.trust(tmp_path, FOLDERS + ["/srv"]) is True

    text = config.read_text()
    assert text.startswith('model = "gpt-5"  # mine\n')
    assert tomllib.loads(text)["projects"] == {"/srv": {"trust_level": "untrusted"},
                                               "/opt/eggie/projects": {"trust_level": "trusted"}}
    assert codex_trust.trust(tmp_path, FOLDERS) is False


def test_trust_creates_the_file_when_codex_has_none(tmp_path):
    assert codex_trust.trust(tmp_path / "codex", FOLDERS) is True
    assert tomllib.loads((tmp_path / "codex" / "config.toml").read_text())["projects"] == {
        "/opt/eggie/projects": {"trust_level": "trusted"}}


@pytest.mark.parametrize("text", ['projects = { "/x" = { trust_level = "trusted" } }\n',
                                  "model = \n"])
def test_trust_never_rewrites_a_file_it_would_break_or_cannot_read(tmp_path, text):
    config = tmp_path / "config.toml"
    config.write_text(text)
    with pytest.raises(ValueError):
        codex_trust.trust(tmp_path, FOLDERS)
    assert config.read_text() == text


def test_trust_keeps_text_that_is_not_ascii(tmp_path):
    config = tmp_path / "config.toml"
    config.write_bytes('# Ігор’s settings\n'.encode("utf-8"))
    codex_trust.trust(tmp_path, FOLDERS)
    assert config.read_bytes().startswith('# Ігор’s settings\n'.encode("utf-8"))


def test_codex_still_opens_when_its_link_has_no_handler(tmp_path):
    app = tmp_path / "Microsoft" / "WindowsApps" / "OpenAI.Codex_2p2nqsd0c76g0" / "chatgpt-desktop.exe"
    app.parent.mkdir(parents=True)
    app.write_bytes(b"")
    spawned = []

    def no_handler(url):
        raise OSError("[WinError 1155] No application is associated")

    provider = Wsl2Provider(distro="eggie-vm", runner=_no_wsl,
                            opener=no_handler, spawner=spawned.append,
                            env={"LOCALAPPDATA": str(tmp_path), "CODEX_HOME": "/dev/null/codex"})
    assert provider.open_agent("codex", local=True) is True
    assert spawned == [[str(app)]]


def test_trust_keeps_windows_line_endings(tmp_path):
    config = tmp_path / "config.toml"
    config.write_bytes(b'model = "gpt-5"\r\n')
    codex_trust.trust(tmp_path, FOLDERS)
    raw = config.read_bytes()
    assert raw.startswith(b'model = "gpt-5"\r\n') and b"\n" not in raw.replace(b"\r\n", b"")
