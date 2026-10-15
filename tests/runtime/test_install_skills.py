import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "runtime" / "install" / "lib" / "install-skills.sh"

# Logs every call; `npx` fails until the counter reaches FAIL_UNTIL. Anything
# read from stdin is captured so the test can prove nothing was read.
FAKE_RUNUSER = """#!/usr/bin/env bash
echo "$*" >> "$LOG"
cat >> "$STDIN"
if [[ "$*" == *" npx "* ]]; then
  n=$(( $(cat "$COUNTER" 2>/dev/null || echo 0) + 1 ))
  echo "$n" > "$COUNTER"
  (( n > FAIL_UNTIL )) || exit 1
fi
"""

FAKE_SLEEP = """#!/usr/bin/env bash
echo "sleep $*" >> "$LOG"
"""


def _run(tmp_path, fail_until):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, text in {"runuser": FAKE_RUNUSER, "sleep": FAKE_SLEEP}.items():
        (bin_dir / name).write_text(text)
        (bin_dir / name).chmod(0o755)
    log, stdin, counter = tmp_path / "log", tmp_path / "stdin", tmp_path / "counter"
    log.touch()
    stdin.touch()
    env = {**os.environ, "PATH": f"{bin_dir}:/usr/bin:/bin", "LOG": str(log),
           "STDIN": str(stdin), "COUNTER": str(counter), "FAIL_UNTIL": str(fail_until)}
    result = subprocess.run(
        ["bash", str(SCRIPT), "ada", "/home/ada", "skills@1.5.26", "eggie-io/eggie-skills",
         "claude-code", "codex"],
        env=env, input="bob:1002:1002:/home/bob\n", capture_output=True, text=True)
    return result, log.read_text().splitlines(), stdin.read_text()


def test_a_transient_npm_failure_is_retried_with_a_clean_cache(tmp_path):
    result, log, _ = _run(tmp_path, fail_until=2)
    assert result.returncode == 0, result.stderr
    kinds = ["npx" if " npx " in l else "clean" if "cache clean" in l else l for l in log]
    assert kinds == ["npx", "clean", "sleep 5", "npx", "clean", "sleep 5", "npx"]


def test_the_npx_call_runs_as_the_account_with_its_home(tmp_path):
    _, log, _ = _run(tmp_path, fail_until=0)
    assert log == ["-u ada -- env HOME=/home/ada DISABLE_TELEMETRY=1 npx -y skills@1.5.26"
                   " add eggie-io/eggie-skills -s * -g -a claude-code codex -y"]


def test_the_cache_is_cleaned_as_the_account_not_as_root(tmp_path):
    # npm's cache is ~/.npm of the user running npx; root's cache is not the
    # one that holds the bad tarball.
    _, log, _ = _run(tmp_path, fail_until=1)
    assert "-u ada -- env HOME=/home/ada npm cache clean --force" in log


def test_it_gives_up_after_three_attempts(tmp_path):
    result, log, _ = _run(tmp_path, fail_until=99)
    assert result.returncode != 0
    assert sum(" npx " in l for l in log) == 3
    assert "ada" in result.stderr


def test_nothing_in_the_script_reads_stdin(tmp_path):
    # install.sh runs this inside a loop that reads the account list from
    # stdin; a child that reads it would swallow the remaining accounts.
    _, _, stdin = _run(tmp_path, fail_until=1)
    assert stdin == ""
