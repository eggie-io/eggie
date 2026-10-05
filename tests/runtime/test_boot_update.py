"""The boot unit's choice of what to accept, and what it hands get.sh."""
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BOOT = ROOT / "runtime" / "install" / "lib" / "boot-update.sh"


def _root(tmp_path, *, host_json=None, release='{"api": 1}', installed="runtime-v0.1.0"):
    root = tmp_path / "opt-eggie"
    (root / "runtime").mkdir(parents=True)
    if host_json is not None:
        (root / "host.json").write_text(host_json)
    if release is not None:
        (root / "runtime" / "release.json").write_text(release)
    if installed:
        (root / "runtime.version").write_text(installed + "\n")
    (root / "runtime.env").write_text(
        "EGGIE_RUNTIME_URL=https://example.invalid/get.sh\n"
        "EGGIE_RUNTIME_REPO=https://example.invalid/repo\n")
    return root


def _accepted(tmp_path, **kw):
    root = _root(tmp_path, **kw)
    script = BOOT.read_text().replace("/opt/eggie", str(root))
    return subprocess.run(["bash", "-c", script + "\naccepted_api"],
                          env={**os.environ, "EGGIE_BOOT_UPDATE_SOURCED": "1"},
                          capture_output=True, text=True)


def test_boot_update_is_valid_bash():
    assert subprocess.run(["bash", "-n", str(BOOT)]).returncode == 0


def test_the_hosts_list_is_what_gets_accepted(tmp_path):
    result = _accepted(tmp_path, host_json='{"supported_api": [1, 2]}')
    assert result.stdout.strip() == "1,2"


def test_without_a_host_the_installed_api_is_kept(tmp_path):
    result = _accepted(tmp_path, host_json=None, release='{"api": 3}')
    assert result.stdout.strip() == "3"


def test_a_malformed_host_file_falls_back_to_the_installed_api(tmp_path):
    result = _accepted(tmp_path, host_json='{"supported_api": "all"}', release='{"api": 1}')
    assert result.stdout.strip() == "1"


def test_nothing_to_go_on_is_a_failure_not_an_empty_list(tmp_path):
    result = _accepted(tmp_path, host_json=None, release=None)
    assert result.returncode != 0
    assert result.stdout.strip() == ""


def _run(tmp_path, *, installed="runtime-v0.1.0", prev_version=None):
    root = _root(tmp_path, host_json='{"supported_api": [1]}', installed=installed)
    if prev_version:
        (root / "runtime.prev.version").write_text(prev_version + "\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    curl = bin_dir / "curl"
    # The fetched "get.sh" reports what it was handed.
    curl.write_text("#!/bin/sh\necho 'echo \"url=$EGGIE_RUNTIME_URL api=$EGGIE_RUNTIME_API "
                    "update=$EGGIE_RUNTIME_UPDATE repo=$EGGIE_RUNTIME_REPO ref=$EGGIE_RUNTIME_REF\"'\n")
    curl.chmod(0o755)
    script = BOOT.read_text().replace("/opt/eggie", str(root))
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}",
           "EGGIE_RUNTIME_REF": "leaked"}
    return subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True)


def test_the_update_runs_get_sh_in_update_mode_from_the_recorded_source(tmp_path):
    result = _run(tmp_path)
    assert result.returncode == 0, result.stderr
    assert ("url=https://example.invalid/get.sh api=1 update=1 "
            "repo=https://example.invalid/repo ref=") in result.stdout


def test_a_vm_installed_from_a_branch_is_left_alone(tmp_path):
    result = _run(tmp_path, installed="feature/x")
    assert result.returncode == 0
    assert "update=1" not in result.stdout


def test_a_vm_left_without_a_marker_by_an_interrupted_update_is_updated(tmp_path):
    # get.sh puts the previous release back before it updates again.
    result = _run(tmp_path, installed="", prev_version="runtime-v0.1.0")
    assert result.returncode == 0, result.stderr
    assert "update=1" in result.stdout


def test_the_boot_update_reads_the_file_the_host_writes():
    from host.core import constants
    assert constants.HOST_JSON in BOOT.read_text()
