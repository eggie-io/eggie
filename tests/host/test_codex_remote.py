import json
import queue
import subprocess
from pathlib import Path

from host.core.connect import codex_remote


class FakeRunner:
    def __init__(self, *codes):
        self.calls = []
        self._codes = list(codes)

    def __call__(self, argv):
        self.calls.append(argv)
        code = self._codes.pop(0) if self._codes else 0
        return subprocess.CompletedProcess(argv, code, b"", b"")


class FakeProc:
    def __init__(self, lines, returncode=0, hang=False):
        self._q = queue.Queue()
        for line in lines:
            self._q.put(line)
        self.hang = hang
        self.returncode = None if hang else returncode
        self._final = returncode
        self.killed = False
        self.stdout = self

    def __iter__(self):
        while True:
            line = self._q.get()
            if line is None:
                return
            yield line

    def wait(self, timeout=None):
        if self.hang and not self.killed:
            raise subprocess.TimeoutExpired("ssh", timeout)
        self.returncode = self._final
        return self.returncode

    def poll(self):
        return self.returncode

    def kill(self):
        self.killed = True
        self.returncode = -9
        self._q.put(None)


def test_installs_codex_when_missing():
    run = FakeRunner(1, 0, 0)
    probe = codex_remote.ensure_installed(run, "eggie-x", "setup-cmd")
    assert probe.ok
    assert "setup-cmd" in run.calls[1][-1]
    assert len(run.calls) == 3


def test_installed_codex_is_not_reinstalled():
    run = FakeRunner(0)
    assert codex_remote.ensure_installed(run, "eggie-x", "setup-cmd").ok
    assert len(run.calls) == 1


def test_busy_port_stops_before_ssh():
    spawned = []
    probe = codex_remote.sign_in("eggie-x", popen=lambda a: spawned.append(a),
                                 open_url=lambda u: None, port_free=lambda p: False)
    assert not probe.ok
    assert "1455" in probe.reason
    assert spawned == []


def test_sign_in_opens_the_printed_url():
    proc = FakeProc(["Starting local login server on http://localhost:1455.\n",
                     "Open https://auth.openai.com/oauth/authorize?x=1.\n", None])
    argvs, opened = [], []
    probe = codex_remote.sign_in("eggie-x", popen=lambda a: argvs.append(a) or proc,
                                 open_url=opened.append, port_free=lambda p: True)
    assert probe.ok
    assert opened == ["https://auth.openai.com/oauth/authorize?x=1"]
    assert argvs[0][argvs[0].index("-L") + 1] == "1455:127.0.0.1:1455"
    assert argvs[0][-1] == "bash -lc 'codex login'"


def test_failed_login_reports_not_ok():
    proc = FakeProc(["https://auth.openai.com/x\n", "denied\n", None], returncode=1)
    probe = codex_remote.sign_in("eggie-x", popen=lambda a: proc,
                                 open_url=lambda u: None, port_free=lambda p: True)
    assert not probe.ok


def test_no_url_kills_and_reports():
    proc = FakeProc(["Waiting for the server\n"], hang=True)
    opened = []
    probe = codex_remote.sign_in("eggie-x", popen=lambda a: proc, open_url=opened.append,
                                 port_free=lambda p: True, url_timeout=0.2)
    assert not probe.ok
    assert proc.killed
    assert probe.reason == "Waiting for the server"
    assert opened == []


def test_setup_command_matches_the_runtime_manifest():
    manifest = Path(__file__).resolve().parents[2] / "runtime/agents/codex/agent.json"
    run = json.loads(manifest.read_text(encoding="utf-8"))["setup"]["run"]
    assert codex_remote.SETUP_COMMAND == run


def test_open_url_failure_still_kills_the_tunnel():
    proc = FakeProc(["https://auth.openai.com/x\n"], hang=True)

    def boom(url):
        raise RuntimeError("no browser")

    try:
        codex_remote.sign_in("eggie-x", popen=lambda a: proc, open_url=boom,
                             port_free=lambda p: True)
    except RuntimeError:
        pass
    assert proc.killed


def test_url_inside_ansi_colour_codes_is_clean():
    proc = FakeProc(["\x1b[1mOpen \x1b[36mhttps://auth.openai.com/a?b=1\x1b[0m\n", None])
    opened = []
    codex_remote.sign_in("eggie-x", popen=lambda a: proc, open_url=opened.append,
                         port_free=lambda p: True)
    assert opened == ["https://auth.openai.com/a?b=1"]


def test_missing_ssh_binary_is_reported():
    def popen(argv):
        raise FileNotFoundError("ssh not found")

    probe = codex_remote.sign_in("eggie-x", popen=popen, open_url=lambda u: None,
                                 port_free=lambda p: True)
    assert not probe.ok and "ssh not found" in probe.reason


def test_a_sign_in_address_off_openai_is_never_opened():
    for url in ("https://auth.openai.com.evil.example/x", "https://auth.openai.com:8443/x",
                "https://auth.openai.com@evil.example/x", "https://evil.example/x"):
        proc = FakeProc([f"Open {url}\n"], hang=True)
        opened = []
        probe = codex_remote.sign_in("eggie-x", popen=lambda a: proc, open_url=opened.append,
                                     port_free=lambda p: True)
        assert probe == codex_remote.Probe(
            False, "Codex printed an unexpected sign-in address; nothing was opened."), url
        assert opened == [] and proc.killed
