"""Whether a coding agent is on this computer, and which app to open."""
from __future__ import annotations

from pathlib import Path

from host.providers.lima import LimaProvider
from host.providers.wsl2 import Wsl2Provider


def test_lima_finds_the_app_bundle(tmp_path):
    (tmp_path / "Claude.app").mkdir()
    lima = LimaProvider(applications=tmp_path)
    assert lima.agent_app("claude-code") == str(tmp_path / "Claude.app")
    assert lima.agent_installed("claude-code") is True
    assert lima.agent_installed("cursor") is False


def test_lima_counts_codex_inside_the_chatgpt_app(tmp_path):
    (tmp_path / "ChatGPT.app").mkdir()
    lima = LimaProvider(applications=tmp_path)
    assert lima.agent_app("codex") == str(tmp_path / "ChatGPT.app")


def test_a_cli_on_path_without_the_app_is_not_installed(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    _touch(tmp_path / "cursor").chmod(0o755)
    lima = LimaProvider(applications=tmp_path / "Applications")
    assert lima.agent_installed("cursor") is False


def test_an_unknown_agent_is_never_installed(tmp_path):
    assert LimaProvider(applications=tmp_path).agent_installed("aider") is False
    assert Wsl2Provider(env={"LOCALAPPDATA": str(tmp_path)}).agent_installed("aider") is False


def _touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    return path


def test_wsl_finds_each_agents_windows_app(tmp_path):
    apps = tmp_path / "Microsoft" / "WindowsApps"
    claude = _touch(apps / "Claude_pzs8sxrjxfjjc" / "claude-desktop.exe")
    codex = _touch(apps / "OpenAI.Codex_2p2nqsd0c76g0" / "chatgpt-desktop.exe")
    cursor = _touch(tmp_path / "Programs" / "cursor" / "Cursor.exe")
    wsl = Wsl2Provider(env={"LOCALAPPDATA": str(tmp_path)})
    assert wsl.agent_app("claude-code") == str(claude)
    assert wsl.agent_app("codex") == str(codex)
    assert wsl.agent_app("cursor") == str(cursor)


def test_wsl_finds_the_older_claude_installer(tmp_path):
    old = _touch(tmp_path / "AnthropicClaude" / "claude.exe")
    wsl = Wsl2Provider(env={"LOCALAPPDATA": str(tmp_path)})
    assert wsl.agent_app("claude-code") == str(old)


def test_wsl_without_the_codex_app_has_no_codex(tmp_path):
    wsl = Wsl2Provider(env={"LOCALAPPDATA": str(tmp_path)})
    assert wsl.agent_app("codex") is None
    assert wsl.agent_installed("codex") is False


def test_opening_an_agent_launches_its_app_and_nothing_without_one(tmp_path):
    import subprocess
    (tmp_path / "Cursor.app").mkdir()
    ran = []
    lima = LimaProvider(applications=tmp_path,
                        runner=lambda argv: ran.append(argv) or subprocess.CompletedProcess(argv, 0))
    assert lima.open_agent("cursor") is True
    assert lima.open_agent("claude-code") is False
    assert ran == [["open", str(tmp_path / "Cursor.app")]]

    spawned = []
    cursor = _touch(tmp_path / "Programs" / "cursor" / "Cursor.exe")
    wsl = Wsl2Provider(env={"LOCALAPPDATA": str(tmp_path)},
                       spawner=spawned.append)
    assert wsl.open_agent("cursor") is True
    assert wsl.open_agent("codex") is False
    assert spawned == [[str(cursor)]]
