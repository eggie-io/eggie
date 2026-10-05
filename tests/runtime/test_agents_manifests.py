import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "runtime" / "install" / "lib" / "agents.py"
AGENTS = ROOT / "runtime" / "agents"


def agents_py(*args, agents_dir=AGENTS):
    return subprocess.run([sys.executable, str(SCRIPT), "--agents-dir", str(agents_dir), *args],
                          capture_output=True, text=True)


def write_agents(tmp_path, manifests):
    root = tmp_path / "agents"
    root.mkdir()
    (root / "index.json").write_text(json.dumps({"agents": list(manifests)}))
    for agent_id, manifest in manifests.items():
        (root / agent_id).mkdir()
        (root / agent_id / "agent.json").write_text(json.dumps(manifest))
    return root


def test_every_shipped_manifest_is_valid():
    ids = json.loads((AGENTS / "index.json").read_text())["agents"]
    assert ids, "scanned no agents"
    result = agents_py("skills")
    assert result.returncode == 0
    assert result.stderr == ""


def test_system_wide_and_per_account_instruction_files_are_kept_apart(tmp_path):
    root = write_agents(tmp_path, {
        "a": {"home": ".a", "instructions": ["/etc/a/RULES.md"]},
        "b": {"home": ".b", "instructions": ["~/.b/AGENTS.md"]},
    })
    assert agents_py("instructions", "--system", agents_dir=root).stdout.split() == ["/etc/a/RULES.md"]
    assert agents_py("instructions", "--home", "/home/ada", agents_dir=root).stdout.split() == \
        ["/home/ada/.b/AGENTS.md"]


@pytest.mark.parametrize("bad", [
    {"home": "../etc"},
    {"home": "/root"},
    {"home": ".x", "instructions": ["~/../../etc/passwd"]},
    {"home": ".x", "instructions": ["relative.md"]},
    {"home": ".x", "detect": {"ignore": ["a/b"]}},
    {"home": ".x", "setup": {"run": "  "}},
    {},
    {"home": "..\n"},
    {"home": ".x", "instructions": ["~/..\n"]},
    {"home": ".x", "skills": "x\n"},
    {"home": []},
    {"home": [".x", "../etc"]},
])
def test_a_manifest_that_could_escape_or_is_incomplete_is_skipped_by_name(tmp_path, bad):
    root = write_agents(tmp_path, {"good": {"home": ".g", "skills": "good"},
                                   "bad": {"skills": "bad", **bad}})
    result = agents_py("skills", agents_dir=root)
    assert result.returncode == 0
    assert result.stdout.split() == ["good"]
    assert "bad" in result.stderr


def test_an_unreadable_index_fails(tmp_path):
    root = tmp_path / "agents"
    root.mkdir()
    (root / "index.json").write_text("{nope")
    assert agents_py("skills", agents_dir=root).returncode == 1


def test_a_non_string_agent_id_in_the_index_is_skipped(tmp_path):
    root = write_agents(tmp_path, {"good": {"home": ".g", "skills": "good"}})
    (root / "index.json").write_text(json.dumps({"agents": [1, "good"]}))
    result = agents_py("skills", agents_dir=root)
    assert result.returncode == 0
    assert result.stdout.split() == ["good"]
    assert "skipping agent 1" in result.stderr
