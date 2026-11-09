"""update-now.sh: runs the updater once per request number and records the outcome."""
import json
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "runtime" / "install" / "lib" / "update-now.sh"
LAST_LINE = ("could not download the images for Eggie runtime runtime-v0.2.0; "
             "staying on runtime-v0.1.0")


def _run(tmp_path, *, request, status=None, boot_body="exit 0"):
    lib = tmp_path / "lib"
    lib.mkdir()
    shutil.copy(SCRIPT, lib / "update-now.sh")
    (lib / "boot-update.sh").write_text(f'echo ran >> "{tmp_path}/boot.log"\n{boot_body}\n')
    (lib / "update-check.sh").write_text(f'echo "$1" >> "{tmp_path}/check.log"\n')
    update = tmp_path / "update"
    update.mkdir()
    if request is not None:
        (update / "request").write_text(request)
    if status is not None:
        (update / "status.json").write_text(status)
    result = subprocess.run(["bash", str(lib / "update-now.sh"), str(update)],
                            capture_output=True, text=True)
    ran = (tmp_path / "boot.log").read_text().count("ran") if (tmp_path / "boot.log").exists() else 0
    return result, update / "status.json", ran, (tmp_path / "check.log").exists()


def test_update_now_is_valid_bash():
    assert subprocess.run(["bash", "-n", str(SCRIPT)]).returncode == 0


def test_a_new_request_runs_the_updater_and_records_done(tmp_path):
    result, status, ran, checked = _run(tmp_path, request="3\n")
    assert result.returncode == 0, result.stderr
    assert ran == 1
    assert json.loads(status.read_text()) == {"request": 3, "state": "done", "error": None}
    assert checked


def test_a_failed_update_records_the_last_error_line(tmp_path):
    body = f'echo x >&2\necho "{LAST_LINE}" >&2\nexit 1'
    result, status, _, checked = _run(tmp_path, request="3", boot_body=body)
    assert result.returncode == 0, result.stderr
    data = json.loads(status.read_text())
    assert data["state"] == "failed"
    assert data["error"] == LAST_LINE
    assert checked


def test_a_failure_with_no_output_says_so(tmp_path):
    _, status, _, _ = _run(tmp_path, request="1", boot_body="exit 1")
    assert json.loads(status.read_text())["error"] == "no reason given"


def test_an_already_handled_request_does_nothing(tmp_path):
    done = '{"request":3,"state":"done","error":null}'
    result, status, ran, checked = _run(tmp_path, request="3", status=done)
    assert result.returncode == 0
    assert ran == 0 and not checked
    assert status.read_text() == done


def test_a_garbage_request_is_not_a_request(tmp_path):
    _, status, ran, _ = _run(tmp_path, request="soon")
    assert ran == 0
    assert not status.exists()
