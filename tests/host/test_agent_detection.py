"""Whether a coding agent is on this computer, and which app to open."""
from __future__ import annotations

from pathlib import Path

from host.providers.lima import LimaProvider
from host.providers.wsl2 import Wsl2Provider


def _which(found):
    return lambda name: found.get(name)


def test_lima_finds_the_app_bundle(tmp_path):
    (tmp_path / "Claude.app").mkdir()
    lima = LimaProvider(applications=tmp_path, which=_which({}))
    assert lima.agent_app("claude-code") == str(tmp_path / "Claude.app")
    assert lima.agent_installed("claude-code") is True
    assert lima.agent_installed("cursor") is False


def test_lima_counts_codex_inside_the_chatgpt_app(tmp_path):
    (tmp_path / "ChatGPT.app").mkdir()
    lima = LimaProvider(applications=tmp_path, which=_which({}))
    assert lima.agent_app("codex") == str(tmp_path / "ChatGPT.app")


def test_a_cli_on_path_is_installed_but_has_no_app_to_open(tmp_path):
    lima = LimaProvider(applications=tmp_path, which=_which({"cursor": "/usr/local/bin/cursor"}))
    assert lima.agent_installed("cursor") is True
    assert lima.agent_app("cursor") is None


def test_an_unknown_agent_is_never_installed(tmp_path):
    lima = LimaProvider(applications=tmp_path, which=lambda name: "/bin/" + name)
    assert lima.agent_installed("aider") is False
    wsl = Wsl2Provider(env={"LOCALAPPDATA": str(tmp_path)}, which=lambda name: "C:/" + name)
    assert wsl.agent_installed("aider") is False


def _touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    return path


def test_wsl_finds_each_agents_windows_app(tmp_path):
    apps = tmp_path / "Microsoft" / "WindowsApps"
    claude = _touch(apps / "Claude_pzs8sxrjxfjjc" / "claude-desktop.exe")
    codex = _touch(apps / "OpenAI.Codex_2p2nqsd0c76g0" / "chatgpt-desktop.exe")
    cursor = _touch(tmp_path / "Programs" / "cursor" / "Cursor.exe")
    wsl = Wsl2Provider(env={"LOCALAPPDATA": str(tmp_path)}, which=_which({}))
    assert wsl.agent_app("claude-code") == str(claude)
    assert wsl.agent_app("codex") == str(codex)
    assert wsl.agent_app("cursor") == str(cursor)


def test_wsl_finds_the_older_claude_installer(tmp_path):
    old = _touch(tmp_path / "AnthropicClaude" / "claude.exe")
    wsl = Wsl2Provider(env={"LOCALAPPDATA": str(tmp_path)}, which=_which({}))
    assert wsl.agent_app("claude-code") == str(old)


def test_wsl_without_localappdata_still_checks_path(tmp_path):
    wsl = Wsl2Provider(env={}, which=_which({"codex": r"C:\npm\codex.cmd"}))
    assert wsl.agent_app("codex") is None
    assert wsl.agent_installed("codex") is True
    assert wsl.agent_installed("claude-code") is False
