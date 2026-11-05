"""claude-reaper.sh stops only Desktop session processes whose transcript is stale.
Runs under real bash against a fake /proc tree; each fake entry is named after a real
`sleep` this test started, so a kill is observed on that process and nothing else.
`getent` and `logger` are fakes on PATH."""
import os
import subprocess
import time
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "runtime" / "install" / "lib" / "claude-reaper.sh"
SID = "0b6a3c1e-9f2d-4c7a-8e51-2d4f6a7b8c9d"
CCD = "/home/u/.claude/remote/ccd-cli/2.1.30"


@pytest.fixture
def box(tmp_path):
    bin_dir, proc, home = tmp_path / "bin", tmp_path / "proc", tmp_path / "home"
    for d in (bin_dir, proc, home):
        d.mkdir()
    log = tmp_path / "reaper.log"
    (bin_dir / "getent").write_text(f'#!/bin/bash\necho "u:x:$2:$2::{home}:/bin/bash"\n')
    (bin_dir / "logger").write_text(f'#!/bin/bash\nshift 2\necho "$*" >> "{log}"\n')
    for f in bin_dir.iterdir():
        f.chmod(0o755)
    sleepers = []
    yield _Box(bin_dir, proc, home, log, sleepers)
    for p in sleepers:
        p.kill()
        p.wait()


class _Box:
    def __init__(self, bin_dir, proc, home, log, sleepers):
        self.bin_dir, self.proc, self.home, self.log = bin_dir, proc, home, log
        self.sleepers = sleepers

    def process(self, exe=CCD, args=("--output-format=stream-json", f"--session-id={SID}")):
        p = subprocess.Popen(["sleep", "300"])
        self.sleepers.append(p)
        entry = self.proc / str(p.pid)
        entry.mkdir()
        (entry / "exe").symlink_to(exe)
        (entry / "cmdline").write_bytes(b"\0".join(a.encode() for a in (exe, *args)) + b"\0")
        return p

    def transcript(self, sid=SID, age_min=0):
        f = self.home / ".claude" / "projects" / "-home-u-projects-shop" / f"{sid}.jsonl"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("{}\n")
        t = time.time() - age_min * 60
        os.utime(f, (t, t))

    def run(self, **env):
        result = subprocess.run(
            ["bash", str(SCRIPT)], capture_output=True, text=True,
            env={**os.environ, "PATH": f"{self.bin_dir}:{os.environ['PATH']}",
                 "EGGIE_REAPER_PROC": str(self.proc), **env})
        return result, self.log.read_text() if self.log.exists() else ""


def _killed(p):
    try:
        p.wait(timeout=5)
    except subprocess.TimeoutExpired:
        return False
    return True


def test_a_process_that_is_not_ccd_cli_is_ignored(box):
    p = box.process(exe="/usr/bin/node")
    box.transcript(age_min=600)
    result, log = box.run()
    assert result.returncode == 0
    assert p.poll() is None and log == ""


def test_a_process_without_a_session_id_is_skipped_and_logged(box):
    p = box.process(args=("--output-format=stream-json",))
    result, log = box.run()
    assert result.returncode == 0
    assert p.poll() is None and "no session id, skip" in log


def test_a_process_without_a_transcript_is_skipped_and_logged(box):
    p = box.process()
    result, log = box.run()
    assert result.returncode == 0
    assert p.poll() is None and f"session={SID} no transcript, skip" in log


def test_a_session_with_a_fresh_transcript_is_kept(box):
    p = box.process()
    box.transcript(age_min=119)
    result, log = box.run()
    assert result.returncode == 0
    assert p.poll() is None and "idle_min=119" in log and "reap" not in log


def test_a_session_with_a_stale_transcript_is_stopped(box):
    p = box.process()
    box.transcript(age_min=120)
    _, log = box.run()
    assert _killed(p) and f"reap pid={p.pid} session={SID}" in log


def test_a_session_id_given_as_a_separate_argument_is_matched(box):
    p = box.process(args=("--resume", SID))
    box.transcript(age_min=600)
    box.run()
    assert _killed(p)


def test_dry_run_logs_the_reap_but_kills_nothing(box):
    p = box.process()
    box.transcript(age_min=600)
    result, log = box.run(EGGIE_REAPER_DRY="1")
    assert result.returncode == 0
    assert p.poll() is None and "dry=1" in log


@pytest.mark.parametrize("value", ["2h", "0", "010"])
def test_an_unusable_idle_setting_kills_nothing(box, value):
    p = box.process()
    box.transcript(age_min=600)
    result, log = box.run(EGGIE_REAPER_IDLE_MIN=value)
    assert result.returncode != 0
    assert p.poll() is None and "nothing reaped" in log
