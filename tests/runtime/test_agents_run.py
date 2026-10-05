import json
import os
import stat
import subprocess
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "runtime" / "install" / "lib" / "agents-run.sh"

FAKE_RUNUSER = """#!/usr/bin/env bash
# runuser -u NAME -- CMD...: log who, then run CMD as us.
name=$2; shift 3
echo "$name" >> "$LOG/runuser"
exec "$@"
"""
FAKE_GETENT = """#!/usr/bin/env bash
echo "ada:x:1001:1001::$ADA_HOME:/bin/bash"
"""
SETUP = '[[ ! -e "$HOME/setup-fails" ]] && touch "$HOME/installed"'
MANIFESTS = {
    "codex": {"home": ".codex", "detect": {"ignore": ["AGENTS.md"]}, "setup": {"run": SETUP}},
    "claude-code": {"home": ".claude", "detect": {"ignore": ["skills"]}},
}


def make(tmp_path):
    bin_dir, log, status = tmp_path / "bin", tmp_path / "log", tmp_path / "agent-status"
    root, ada, agents = tmp_path / "root", tmp_path / "ada", tmp_path / "agents"
    for d in (bin_dir, log, status / "setup", root, ada, agents):
        d.mkdir(parents=True)
    for name, text in {"runuser": FAKE_RUNUSER, "getent": FAKE_GETENT}.items():
        (bin_dir / name).write_text(text)
        (bin_dir / name).chmod(0o755)
    (agents / "index.json").write_text(json.dumps({"agents": list(MANIFESTS)}))
    for agent_id, manifest in MANIFESTS.items():
        (agents / agent_id).mkdir()
        (agents / agent_id / "agent.json").write_text(json.dumps(manifest))
    shells = tmp_path / "shells"
    shells.write_text("/bin/bash\n")
    path = f"{bin_dir}:/usr/bin:/bin"
    env = {**os.environ, "PATH": path, "OMELET_APPLY_PATH": path, "LOG": str(log),
           "OMELET_ROOT_HOME": str(root), "OMELET_SHELLS_FILE": str(shells),
           "ADA_HOME": str(ada), "OMELET_AGENTS_DIR": str(agents)}
    return SimpleNamespace(env=env, status=status, root=root, ada=ada, log=log)


def run(t):
    result = subprocess.run(["bash", str(SCRIPT), str(t.status)], env=t.env,
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return json.loads((t.status / "status.json").read_text())["agents"]


def request(t, agent_id, number):
    (t.status / "setup" / agent_id).write_text(str(number))


def setups_run(t):
    log = t.log / "runuser"
    return log.read_text().split() if log.exists() else []


def test_a_dir_holding_only_omelets_own_files_is_not_connected(tmp_path):
    t = make(tmp_path)
    (t.root / ".codex").mkdir()
    (t.root / ".codex" / "AGENTS.md").write_text("ours")
    (t.root / ".claude" / "skills").mkdir(parents=True)
    agents = run(t)
    assert agents["codex"]["connected"] is False
    assert agents["claude-code"]["connected"] is False


def test_anything_else_in_any_accounts_dir_means_connected(tmp_path):
    t = make(tmp_path)
    (t.ada / ".claude" / "sessions").mkdir(parents=True)
    agents = run(t)
    assert agents["claude-code"]["connected"] is True
    assert agents["codex"]["connected"] is False


def test_a_requested_setup_runs_once_for_every_account(tmp_path):
    t = make(tmp_path)
    request(t, "codex", 1)
    assert run(t)["codex"]["setup"] == "ready"
    assert (t.root / "installed").exists() and (t.ada / "installed").exists()
    run(t)
    assert sorted(setups_run(t)) == ["ada", "root"]


def test_two_requests_before_a_pass_run_setup_once(tmp_path):
    t = make(tmp_path)
    request(t, "codex", 1)
    request(t, "codex", 2)
    run(t)
    assert sorted(setups_run(t)) == ["ada", "root"]


def test_a_failed_setup_says_so_and_a_new_request_retries(tmp_path):
    t = make(tmp_path)
    (t.ada / "setup-fails").touch()
    request(t, "codex", 1)
    assert run(t)["codex"]["setup"] == "failed"
    (t.ada / "setup-fails").unlink()
    request(t, "codex", 2)
    assert run(t)["codex"]["setup"] == "ready"


def test_an_agent_already_connected_is_ready_without_running_setup(tmp_path):
    t = make(tmp_path)
    (t.root / ".codex" / "sessions").mkdir(parents=True)
    request(t, "codex", 1)
    assert run(t)["codex"] == {"connected": True, "setup": "ready", "setup_generation": 1}
    assert setups_run(t) == []


def test_a_setup_left_installing_by_an_interrupted_pass_becomes_failed(tmp_path):
    t = make(tmp_path)
    request(t, "codex", 1)
    (t.status / "status.json").write_text(json.dumps({"generation": 0, "agents": {
        "codex": {"connected": False, "setup": "installing", "setup_generation": 1}}}))
    assert run(t)["codex"]["setup"] == "failed"
    assert setups_run(t) == []


def test_the_setup_log_is_readable_by_root_only(tmp_path):
    t = make(tmp_path)
    request(t, "codex", 1)
    (t.status / "setup-codex.log").write_text("old")
    (t.status / "setup-codex.log").chmod(0o660)
    run(t)
    mode = stat.S_IMODE((t.status / "setup-codex.log").stat().st_mode)
    assert mode == 0o600


def test_a_check_bumped_during_a_pass_triggers_another_pass(tmp_path):
    t = make(tmp_path)
    manifest = {"home": ".codex", "setup": {"run": 'echo 7 > "$STATUS/check"'}}
    (tmp_path / "agents" / "codex" / "agent.json").write_text(json.dumps(manifest))
    t.env["STATUS"] = str(t.status)
    request(t, "codex", 1)
    run(t)
    assert json.loads((t.status / "status.json").read_text())["generation"] == 7
