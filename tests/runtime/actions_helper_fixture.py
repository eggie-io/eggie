"""Starts the real eggie-actions helper unprivileged against real tmux."""
import getpass
import json
import os
import socket
import struct
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "runtime" / "install" / "lib" / "eggie-actions.py"

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

    def start(self, *extra: str) -> None:
        with self.stderr.open("a") as err:
            self.proc = subprocess.Popen(
                [sys.executable, str(HELPER), "--socket", str(self.socket),
                 "--projects-root", str(self.projects), "--logs", str(self.logs),
                 "--connect", str(self.connect), "--tmux-tmpdir", str(self.tmux_tmpdir), *extra],
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
