import getpass
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "runtime" / "install" / "lib" / "eggie-actions.py"

pytestmark = pytest.mark.skipif(shutil.which("tmux") is None, reason="needs tmux")

FAKE_RUNUSER = """#!/usr/bin/env bash
# runuser -u NAME -- CMD...: log who, then run CMD as us.
echo "$2" >> "$RUNUSER_LOG"
shift 3
exec "$@"
"""


def eventually(check, timeout=10.0):
    deadline = time.monotonic() + timeout
    while True:
        value = check()
        if value or time.monotonic() > deadline:
            return value
        time.sleep(0.1)


class Terminal:
    def __init__(self, sock: socket.socket, rfile):
        self.sock, self.rfile = sock, rfile

    def send(self, data: bytes) -> None:
        self.sock.sendall(struct.pack("!BI", 0, len(data)) + data)

    def resize(self, cols: int, rows: int) -> None:
        self.sock.sendall(struct.pack("!BI", 1, 4) + struct.pack("!HH", cols, rows))

    def read_until(self, needle: bytes, timeout: float = 10.0) -> bytes:
        seen = b""
        deadline = time.monotonic() + timeout
        while needle not in seen:
            self.sock.settimeout(max(deadline - time.monotonic(), 0.01))
            header = self.rfile.read(5)
            assert len(header) == 5, f"stream ended before {needle!r}; saw {seen!r}"
            kind, length = struct.unpack("!BI", header)
            payload = self.rfile.read(length)
            if kind == 0:
                seen += payload
        return seen

    def close(self) -> None:
        self.rfile.close()
        self.sock.close()


class Helper:
    def __init__(self, tmp_path: Path):
        self.projects = tmp_path / "projects"
        for name in ("app", "other"):
            (self.projects / name).mkdir(parents=True)
        self.logs = tmp_path / "logs"
        self.logs.mkdir()
        self.connect = tmp_path / "connect.json"
        self.set_user(getpass.getuser())
        # Short on purpose: tmux and the helper both bind unix sockets here.
        self.tmux_tmpdir = tmp_path / "t"
        self.tmux_tmpdir.mkdir()
        self.socket = tmp_path / "s"
        self.runuser_log = tmp_path / "runuser.log"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        (bin_dir / "runuser").write_text(FAKE_RUNUSER)
        (bin_dir / "runuser").chmod(0o755)
        self.env = {**os.environ, "PATH": f"{bin_dir}:/usr/bin:/bin",
                    "RUNUSER_LOG": str(self.runuser_log)}
        self.stderr = tmp_path / "helper.err"
        self.proc: subprocess.Popen | None = None

    def set_user(self, user: str) -> None:
        self.connect.write_text(json.dumps({"vm": "wsl", "user": user}))

    def start(self) -> None:
        with self.stderr.open("a") as err:
            self.proc = subprocess.Popen(
                [sys.executable, str(HELPER), "--socket", str(self.socket),
                 "--projects-root", str(self.projects), "--logs", str(self.logs),
                 "--connect", str(self.connect), "--tmux-tmpdir", str(self.tmux_tmpdir)],
                env=self.env, stdout=subprocess.DEVNULL, stderr=err)
        assert eventually(self._answers, 5), self.stderr.read_text()

    def _answers(self) -> bool:
        try:
            self.ask(op="status", project="app")
            return True
        except OSError:
            return False

    def kill(self) -> None:
        if self.proc:
            self.proc.kill()
            self.proc.wait()
            self.proc = None

    def _connect(self, request: dict):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(15)
        sock.connect(str(self.socket))
        sock.sendall(json.dumps(request).encode() + b"\n")
        rfile = sock.makefile("rb")
        return sock, rfile, json.loads(rfile.readline())

    def ask(self, **request) -> dict:
        sock, rfile, reply = self._connect(request)
        rfile.close()
        sock.close()
        return reply

    def attach(self, action: str, project: str = "app", cols: int = 80, rows: int = 24) -> Terminal:
        sock, rfile, reply = self._connect(
            {"op": "attach", "project": project, "action": action, "cols": cols, "rows": rows})
        assert reply == {"ok": True}, reply
        return Terminal(sock, rfile)

    def start_action(self, action: str, command: str, project: str = "app", log: bool = False) -> dict:
        reply = self.ask(op="start", project=project, action=action, command=command, log=log)
        assert reply["ok"], reply
        return reply["run"]

    def run(self, action: str, project: str = "app") -> dict | None:
        reply = self.ask(op="status", project=project)
        assert reply["ok"], reply
        return reply["runs"].get(action)

    def wait_exited(self, action: str, project: str = "app") -> dict | None:
        return eventually(lambda: (r := self.run(action, project)) and r["state"] == "exited" and r)

    def tmux(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["tmux", "-L", "eggie-actions", *args], capture_output=True, text=True,
                              env={"PATH": "/usr/bin:/bin", "TMUX_TMPDIR": str(self.tmux_tmpdir)})


@pytest.fixture
def helper(tmp_path):
    h = Helper(tmp_path)
    h.start()
    yield h
    h.kill()
    h.tmux("kill-server")


def test_a_one_off_runs_in_the_project_folder_and_reports_its_exit_code(helper):
    helper.start_action("build", "pwd > out; exit 3")
    run = helper.wait_exited("build")
    assert run and run["exit_code"] == 3
    assert isinstance(run["started_at"], int)
    assert (helper.projects / "app" / "out").read_text().strip() == str(helper.projects / "app")


def test_start_while_running_is_action_running(helper):
    helper.start_action("serve", "sleep 600")
    reply = helper.ask(op="start", project="app", action="serve", command="sleep 600", log=False)
    assert reply["ok"] is False and reply["code"] == "action_running"


def test_a_finished_run_can_start_again(helper):
    helper.start_action("build", "exit 3")
    assert helper.wait_exited("build")["exit_code"] == 3
    helper.start_action("build", "exit 5")
    assert eventually(lambda: helper.run("build")["exit_code"] == 5)


def test_stop_ends_a_running_command(helper):
    helper.start_action("serve", "sleep 600")
    started = time.monotonic()
    reply = helper.ask(op="stop", project="app", action="serve")
    assert time.monotonic() - started < 6
    assert reply["ok"] and (reply["run"] is None or reply["run"]["state"] == "exited")
    run = helper.run("serve")
    assert run is None or run["state"] == "exited"


def test_stop_ends_a_command_that_ignores_sigterm_and_sighup(helper):
    helper.start_action("stubborn", "trap '' TERM HUP; echo $$ > pid; exec sleep 600")
    pid_file = helper.projects / "app" / "pid"
    assert eventually(lambda: pid_file.exists() and pid_file.read_text().strip())
    pid = int(pid_file.read_text())
    assert helper.ask(op="stop", project="app", action="stubborn")["ok"]

    def gone() -> bool:
        try:
            os.kill(pid, 0)
            return False
        except ProcessLookupError:
            return True

    assert eventually(gone, 3)


def test_stop_project_ends_only_that_projects_runs(helper):
    helper.start_action("serve", "sleep 600")
    helper.start_action("watch", "sleep 600")
    helper.start_action("serve", "sleep 600", project="other")
    assert helper.ask(op="stop-project", project="app") == {"ok": True}
    runs = helper.ask(op="status", project="app")["runs"]
    assert [r for r in runs.values() if r["state"] == "running"] == []
    assert helper.run("serve", project="other")["state"] == "running"


def test_attach_relays_input_and_output(helper):
    helper.start_action("say", "read x; echo got:$x")
    terminal = helper.attach("say")
    terminal.send(b"hi\r")
    terminal.read_until(b"got:hi")
    terminal.close()
    assert helper.wait_exited("say")["exit_code"] == 0


def test_a_second_attach_sees_what_was_on_screen(helper):
    # The tab closed mid-prompt: the run goes on, and reopening shows the prompt.
    helper.start_action("login", "echo enter-the-code; read code; echo code:$code")
    first = helper.attach("login")
    first.read_until(b"enter-the-code")
    first.close()
    time.sleep(0.3)
    assert helper.run("login")["state"] == "running"
    second = helper.attach("login")
    second.read_until(b"enter-the-code")
    second.send(b"123\r")
    second.read_until(b"code:123")
    second.close()


def test_a_resize_reaches_the_actions_window(helper):
    helper.start_action("serve", "sleep 600")

    def size() -> str:
        return helper.tmux("display-message", "-p", "-t", "=app--serve:",
                           "#{window_width}x#{window_height}").stdout.strip()

    terminal = helper.attach("serve", cols=80, rows=24)
    assert eventually(lambda: size() == "80x24"), size()
    terminal.resize(101, 33)
    assert eventually(lambda: size() == "101x33"), size()
    terminal.close()


def test_attach_without_a_session_is_not_running(helper):
    sock, rfile, reply = helper._connect(
        {"op": "attach", "project": "app", "action": "never", "cols": 80, "rows": 24})
    rfile.close()
    sock.close()
    assert reply["ok"] is False and reply["code"] == "not_running"


def test_a_long_running_log_holds_output_and_a_one_off_writes_none(helper):
    log = helper.logs / "app" / "serve.log"
    log.parent.mkdir()
    log.write_text("stale-from-last-run\n")
    helper.start_action("serve", "echo serving-now; sleep 600", log=True)
    helper.start_action("login", "echo the-secret-prompt")
    assert eventually(lambda: "serving-now" in log.read_text())
    assert "stale-from-last-run" not in log.read_text()
    helper.wait_exited("login")
    assert not (helper.logs / "app" / "login.log").exists()


def test_runs_survive_a_helper_restart(helper):
    helper.start_action("serve", "sleep 600")
    helper.kill()
    helper.start()
    assert helper.run("serve")["state"] == "running"


def test_actions_run_as_the_connect_user_and_as_root_when_it_is_empty(helper):
    helper.start_action("first", "true")
    assert set(helper.runuser_log.read_text().split()) == {getpass.getuser()}
    helper.set_user("")
    helper.start_action("second", "true")
    assert helper.runuser_log.read_text().split()[-1] == "root"


def test_ids_that_are_not_slugs_are_refused(helper):
    for project, action in [("../x", "a"), ("a b", "a"), ("app", "../x"), ("app", "a b"),
                            ("app", "a--b"), ("missing", "a")]:
        reply = helper.ask(op="start", project=project, action=action, command="true", log=False)
        assert reply["ok"] is False and reply["code"] == "invalid", (project, action, reply)
        reply = helper.ask(op="attach", project=project, action=action, cols=80, rows=24)
        assert reply["ok"] is False and reply["code"] == "invalid", (project, action, reply)
