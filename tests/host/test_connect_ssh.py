import subprocess

import pytest

from host.core.connect import keys, ssh


class FakeRunner:
    def __init__(self, *results):
        self.calls = []
        self._results = list(results)

    def __call__(self, argv):
        self.calls.append(argv)
        out, err, code = self._results.pop(0) if self._results else (b"", b"", 0)
        return subprocess.CompletedProcess(argv, code, out, err)


def keygen(directory):
    def run(argv):
        (directory / "eggie_ed25519").write_text("private")
        (directory / "eggie_ed25519.pub").write_text("ssh-ed25519 AAAA host\n")
        return subprocess.CompletedProcess(argv, 0, b"", b"")
    return run


def test_key_is_generated_once(tmp_path):
    directory = tmp_path / "ssh"
    calls = []

    def run(argv):
        calls.append(argv)
        return keygen(directory)(argv)

    private, public = keys.ensure_key(directory, "me@pc", run)
    assert private == directory / "eggie_ed25519"
    assert public == "ssh-ed25519 AAAA host"
    assert calls[0][:8] == ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "me@pc"]
    assert directory.stat().st_mode & 0o777 == 0o700

    assert keys.ensure_key(directory, "me@pc", run) == (private, public)
    assert len(calls) == 1


def test_key_failure_raises_keygen_stderr(tmp_path):
    runner = FakeRunner((b"", b"no such program\n", 1))
    with pytest.raises(RuntimeError, match="no such program"):
        keys.ensure_key(tmp_path / "ssh", "c", runner)


def test_reach_reports_last_stderr_line():
    runner = FakeRunner((b"", b"warning\nPermission denied (publickey).\n\n", 255))
    probe = ssh.reach(runner, "eggie-x")
    assert probe == ssh.Probe(False, "Permission denied (publickey).")
    assert runner.calls[0] == ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=20",
                               "-T", "eggie-x", "true"]
    assert ssh.reach(FakeRunner(), "eggie-x").ok


def test_login_shell_quotes_the_script():
    runner = FakeRunner((b"hi\n", b"", 0))
    result = ssh.login_shell(runner, "eggie", "echo 'a b'")
    assert result.stdout == "hi"
    assert runner.calls[0] == ["ssh", "-o", "BatchMode=yes", "-T", "eggie",
                               "bash -lc 'echo '\"'\"'a b'\"'\"''"]


def test_wait_reachable_gives_up_after_timeout():
    now = [0.0]
    sleeps = []

    def sleep(s):
        sleeps.append(s)
        now[0] += s

    runner = FakeRunner(*[(b"", b"refused\n", 255)] * 100)
    probe = ssh.wait_reachable(runner, "a", sleep=sleep, monotonic=lambda: now[0])
    assert probe == ssh.Probe(False, "refused")
    assert sum(sleeps) == 60 and set(sleeps) == {2}


def test_wait_reachable_returns_as_soon_as_ssh_works():
    runner = FakeRunner((b"", b"no\n", 255), (b"", b"", 0))
    probe = ssh.wait_reachable(runner, "a", sleep=lambda s: None, monotonic=lambda: 0.0)
    assert probe.ok and len(runner.calls) == 2
