"""snapshot-prep.sh strips what makes an installed runtime one machine's, so a
server image made from it gives every droplet its own token, database and
certificate. Runs under real bash with docker and cloud-init faked."""
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "runtime" / "install" / "snapshot-prep.sh"

PER_INSTANCE = ["api.token", "state.db", "state.db-wal", "state.db-shm", "connect.json", "site.env",
                "account.json", "tls/site.crt", "tls/site.key", "tls/tls.yml",
                "tunnel/token", "uploads/partial", "github/token", "github/desired.json",
                "github/applied.json", "projects/blog/docker-compose.yml",
                "agent-status/status.json"]
KEPT = ["runtime/install/install.sh", ".env", "runtime.env", "runtime.version",
        "stack.yml"]
BUILDER_ONLY = [".ssh/authorized_keys", ".bash_history"]


def _run(tmp_path: Path):
    root = tmp_path / "opt-eggie"
    for rel in PER_INSTANCE + KEPT:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x")
    root_home = tmp_path / "root-home"
    for rel in BUILDER_ONLY:
        path = root_home / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.log"
    for name in ("docker", "cloud-init"):
        fake = bin_dir / name
        fake.write_text(f'#!/bin/sh\necho "{name} $*" >> "{log}"\n')
        fake.chmod(0o755)
    text = SCRIPT.read_text()
    assert "ROOT_HOME=/root\n" in text
    script = text.replace("ROOT_HOME=/root\n", f"ROOT_HOME={root_home}\n").replace(
        "/opt/eggie", str(root)).replace("/usr/bin/docker", str(bin_dir / "docker"))
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}
    result = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True)
    return result, root, log.read_text() if log.exists() else ""


def test_prep_is_valid_bash():
    assert subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True).returncode == 0


def test_prep_removes_every_per_instance_file_and_keeps_the_runtime(tmp_path):
    result, root, calls = _run(tmp_path)
    assert result.returncode == 0, result.stderr
    for rel in PER_INSTANCE:
        assert not (root / rel).exists(), rel
    for rel in KEPT:
        assert (root / rel).exists(), rel


def test_prep_stops_the_stack_and_resets_cloud_init(tmp_path):
    # A container left running would come back at boot before install.sh
    # reconfigures it; cloud-init must run user data again on the new droplet.
    _, _, calls = _run(tmp_path)
    assert "compose" in calls and " down" in calls
    assert "cloud-init clean --logs" in calls
    # Droplets from one image must not share /etc/machine-id.
    assert "--machine-id" in calls


def test_prep_removes_the_image_builders_key_and_shell_history(tmp_path):
    result, _, _ = _run(tmp_path)
    assert result.returncode == 0, result.stderr
    for rel in BUILDER_ONLY:
        assert not (tmp_path / "root-home" / rel).exists(), rel
