"""The CI runtime install must start where a real VM's does.

A command from the host reaches a WSL VM as `wsl -u root --cd ~`, so the
runtime installs as root from /root. An install from anywhere else hid #141:
runuser kept /root as the cwd and npx, as the login account, could not spawn
its shell there.
"""
from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "ci" / "runtime_acceptance.py"
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


def _module():
    spec = importlib.util.spec_from_file_location("runtime_acceptance", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_root_commands_run_from_root_home_as_on_wsl(monkeypatch):
    seen = []

    def fake_run(argv, **kwargs):
        seen.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, "", "")

    acceptance = _module()
    monkeypatch.setattr(acceptance.subprocess, "run", fake_run)
    acceptance.RootShell({"EGGIE_SKIP_PULL": "1"}).exec(["true"], root=True)

    (argv, kwargs), = seen
    assert argv[:4] == ["sudo", "env", "--chdir=/root", "HOME=/root"]
    assert argv[-1] == "true"
    # The host gives the guest no stdin either.
    assert kwargs["stdin"] is subprocess.DEVNULL


def test_ci_runs_the_acceptance_script_with_the_images_it_built():
    steps = yaml.safe_load(WORKFLOW.read_text())["jobs"]["runtime"]["steps"]
    runs = [step.get("run", "") for step in steps]
    build = next(i for i, run in enumerate(runs) if "packaging/images/build.sh --tag ci" in run)
    accept = next(i for i, run in enumerate(runs)
                  if "scripts/ci/runtime_acceptance.py --images ci" in run)
    assert build < accept
