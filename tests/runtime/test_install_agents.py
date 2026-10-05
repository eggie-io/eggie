import json
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "runtime" / "install" / "lib" / "install-agents.sh"
SOURCE = ROOT / "runtime"


def _install(home: Path, source: Path = SOURCE) -> subprocess.CompletedProcess:
    result = subprocess.run(
        ["bash", str(SCRIPT), str(source), str(home), f"{os.getuid()}:{os.getgid()}"],
        capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return result


def test_skills_are_left_to_npx(tmp_path):
    _install(tmp_path)
    assert not (tmp_path / ".claude" / "skills").exists()
    assert not (tmp_path / ".agents" / "skills").exists()


def _source(tmp_path, manifests):
    source = tmp_path / "src"
    (source / "instructions").mkdir(parents=True)
    shutil.copy(SOURCE / "instructions" / "omelet.md", source / "instructions" / "omelet.md")
    agents = source / "agents"
    agents.mkdir()
    (agents / "index.json").write_text(json.dumps({"agents": list(manifests)}))
    for agent_id, manifest in manifests.items():
        (agents / agent_id).mkdir()
        (agents / agent_id / "agent.json").write_text(json.dumps(manifest))
    return source


def test_the_block_is_replaced_and_the_users_own_text_kept(tmp_path):
    home = tmp_path / "home"
    (home / ".codex").mkdir(parents=True)
    agents_md = home / ".codex" / "AGENTS.md"
    agents_md.write_text("# my notes\nkeep me")  # no trailing newline on purpose
    source = _source(tmp_path, {"codex": {"home": ".codex", "instructions": ["~/.codex/AGENTS.md"]}})

    _install(home, source)
    (source / "instructions" / "omelet.md").write_text("new instructions\n")
    _install(home, source)

    text = agents_md.read_text()
    assert text.startswith("# my notes\nkeep me\n")
    assert text.count("<!-- omelet:begin -->") == 1
    assert "new instructions" in text
    assert "You are working inside an Omelet VM" not in text


def test_every_per_account_target_gets_the_block_and_system_ones_are_left_to_install(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    source = _source(tmp_path, {
        "codex": {"home": ".codex", "instructions": ["~/.codex/AGENTS.md"]},
        "cursor": {"home": ".cursor", "instructions": ["~/.cursor/AGENTS.md"]},
        "claude-code": {"home": ".claude", "instructions": ["/etc/claude-code/CLAUDE.md"]},
    })
    _install(home, source)
    for target in (home / ".codex" / "AGENTS.md", home / ".cursor" / "AGENTS.md"):
        assert "<!-- omelet:begin -->" in target.read_text()
    assert not (home / ".claude").exists()


def test_the_projects_link_is_made_once_and_a_real_folder_is_left_alone(tmp_path):
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    _install(fresh)
    _install(fresh)
    assert os.readlink(fresh / "projects") == "/opt/omelet/projects"

    taken = tmp_path / "taken"
    (taken / "projects").mkdir(parents=True)
    result = _install(taken)
    assert (taken / "projects").is_dir() and not (taken / "projects").is_symlink()
    assert "left" in result.stdout


def test_a_projects_link_pointing_elsewhere_is_left_alone_and_reported(tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    (home / "projects").symlink_to(elsewhere)
    result = _install(home)
    assert os.readlink(home / "projects") == str(elsewhere)
    assert "left" in result.stdout
