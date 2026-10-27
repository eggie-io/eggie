"""tunnel-recreate.sh moves a running public-URL client onto the image the new
stack.yml names. Runs under real bash with docker faked."""
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "runtime" / "install" / "lib" / "tunnel-recreate.sh"


def _run(tmp_path: Path, *, running: bool, up_rc: int = 0):
    log = tmp_path / "calls.log"
    docker = tmp_path / "docker"
    docker.write_text(
        "#!/bin/sh\n"
        f'echo "$*" >> "{log}"\n'
        'case "$*" in\n'
        f'  *" ps -q tunnel") {"echo 3f2a9c" if running else ":"} ;;\n'
        f'  *" up "*) exit {up_rc} ;;\n'
        "esac\n")
    docker.chmod(0o755)
    text = SCRIPT.read_text()
    assert "/usr/bin/docker" in text
    script = text.replace("/usr/bin/docker", str(docker))
    result = subprocess.run(
        ["bash", "-c", script, "tunnel-recreate.sh",
         "-f", "/opt/eggie/stack.yml", "-f", "/opt/eggie/runtime/stack.site.yml"],
        capture_output=True, text=True, env=os.environ)
    calls = log.read_text().splitlines() if log.exists() else []
    return result, calls


def test_a_running_tunnel_is_recreated_from_the_installed_stack(tmp_path):
    result, calls = _run(tmp_path, running=True)
    assert result.returncode == 0, result.stderr
    (up,) = [c for c in calls if " up " in c]
    assert up.startswith("compose -f /opt/eggie/stack.yml -f /opt/eggie/runtime/stack.site.yml "
                         "--profile tunnel up ")
    # Forced, so the new image does not hinge on compose noticing the change;
    # --no-deps, so Traefik is not restarted with it.
    assert "--force-recreate" in up and "--no-deps" in up
    assert up.endswith(" tunnel")


def test_a_tunnel_that_is_not_running_is_left_for_the_api_to_start(tmp_path):
    result, calls = _run(tmp_path, running=False)
    assert result.returncode == 0, result.stderr
    assert calls, "the script must have asked compose whether the tunnel runs"
    assert not [c for c in calls if " up " in c]


def test_a_failed_recreate_does_not_fail_the_install(tmp_path):
    # A failing install rolls the whole update back to the old runtime, which
    # keeps the old cloudflared too.
    result, _ = _run(tmp_path, running=True, up_rc=1)
    assert result.returncode == 0
    assert "public URL" in result.stderr
