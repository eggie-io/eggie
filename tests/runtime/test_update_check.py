"""update-check.sh: what the console is told is available, and that an
unreachable repo never overwrites the last answer."""
import json
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INSTALL = ROOT / "runtime" / "install"
REPO = "https://example.invalid/eggie"


def _run(tmp_path, *, installed="runtime-v0.1.0", tags=("0.1.0", "0.2.0"), apis=None,
         git_code=0, previous=None):
    opt = tmp_path / "opt-eggie"
    lib = opt / "runtime" / "install" / "lib"
    shutil.copytree(INSTALL / "lib", lib)
    shutil.copy(INSTALL / "get.sh", lib.parent / "get.sh")
    script = lib / "update-check.sh"
    script.write_text(script.read_text().replace("/opt/eggie", str(opt)))
    boot = lib / "boot-update.sh"
    boot.write_text(boot.read_text().replace("/opt/eggie", str(opt)))
    (opt / "host.json").write_text('{"supported_api": [1]}')
    (opt / "runtime.env").write_text(f"EGGIE_RUNTIME_URL={REPO}/get.sh\nEGGIE_RUNTIME_REPO={REPO}\n")
    if installed:
        (opt / "runtime.version").write_text(installed + "\n")
    update = opt / "update"
    update.mkdir()
    if previous is not None:
        (update / "available.json").write_text(previous)

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    git_log = tmp_path / "git.log"
    listing = "".join(f"{'0' * 40}\trefs/tags/runtime-v{t}\n" for t in tags)
    (tmp_path / "tags.txt").write_text(listing)
    (bin_dir / "git").write_text(
        f'#!/bin/sh\necho "$*" >> "{git_log}"\ncat "{tmp_path / "tags.txt"}"\nexit {git_code}\n')
    # release.json per tag; every release speaks api 1 unless told otherwise.
    cases = "".join(f'*{t}/*) echo \'{{"api": {a}}}\' ;;\n' for t, a in (apis or {}).items())
    (bin_dir / "curl").write_text(
        f'#!/bin/sh\nfor last; do :; done\ncase "$last" in\n{cases}*) echo \'{{"api": 1}}\' ;;\nesac\n')
    for f in ("git", "curl"):
        (bin_dir / f).chmod(0o755)
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}
    for name in ("EGGIE_RUNTIME_REF", "EGGIE_RUNTIME_REPAIR", "EGGIE_RUNTIME_API"):
        env.pop(name, None)
    result = subprocess.run(["bash", str(script), str(update)], env=env,
                            capture_output=True, text=True)
    return result, update / "available.json", git_log


def test_a_newer_accepted_release_is_offered(tmp_path):
    result, available, _ = _run(tmp_path)
    assert result.returncode == 0, result.stderr
    data = json.loads(available.read_text())
    assert data["available"] == "runtime-v0.2.0"
    assert isinstance(data["checked_at"], int)


def test_the_installed_release_is_not_offered(tmp_path):
    result, available, _ = _run(tmp_path, tags=("0.1.0",))
    assert result.returncode == 0, result.stderr
    assert json.loads(available.read_text())["available"] is None


def test_a_release_the_host_does_not_accept_is_not_offered(tmp_path):
    result, available, _ = _run(tmp_path, apis={"runtime-v0.2.0": 2})
    assert result.returncode == 0, result.stderr
    assert json.loads(available.read_text())["available"] is None


def test_a_branch_pin_is_never_offered_and_git_is_not_asked(tmp_path):
    result, available, git_log = _run(tmp_path, installed="dev")
    assert result.returncode == 0, result.stderr
    assert json.loads(available.read_text())["available"] is None
    assert not git_log.exists() or git_log.read_text() == ""


def test_an_unreachable_repo_keeps_the_last_answer(tmp_path):
    previous = '{"available": "runtime-v0.2.0", "checked_at": 1}'
    result, available, _ = _run(tmp_path, git_code=128, previous=previous)
    assert result.returncode != 0
    assert available.read_text() == previous


def test_a_fallback_to_an_older_tag_keeps_the_offer(tmp_path):
    previous = '{"available": "runtime-v0.2.0", "checked_at": 1}'
    result, available, _ = _run(
        tmp_path, tags=("0.0.9", "0.1.0", "0.2.0"), previous=previous,
        apis={"runtime-v0.1.0": 2, "runtime-v0.2.0": 2})
    assert result.returncode != 0
    assert available.read_text() == previous
