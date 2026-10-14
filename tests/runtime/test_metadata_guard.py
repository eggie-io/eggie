"""The guard's one decision: add the DROP rule unless it is already there.
Runs under real bash with a fake iptables recording its argv."""
import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "runtime" / "install" / "lib" / "metadata-guard.sh"


def _run(tmp_path, check_exit):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.log"
    fake = bin_dir / "iptables"
    fake.write_text(f'#!/bin/bash\necho "$*" >> "{log}"\n'
                    f'[[ " $* " == *" -C "* ]] && exit {check_exit}\nexit 0\n')
    fake.chmod(0o755)
    result = subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True,
                            env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"})
    assert result.returncode == 0, result.stderr
    return log.read_text().splitlines()


def test_a_missing_rule_is_inserted_into_docker_user(tmp_path):
    calls = _run(tmp_path, check_exit=1)
    assert "-w -I DOCKER-USER -d 169.254.169.254 -j DROP" in calls


def test_an_existing_rule_is_not_added_again(tmp_path):
    calls = _run(tmp_path, check_exit=0)
    assert not any(" -I " in f" {c} " for c in calls), calls
