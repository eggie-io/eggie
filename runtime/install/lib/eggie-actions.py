#!/usr/bin/env python3
"""Runs recipe actions in tmux as the VM's login account, for the API, over a unix socket.

Each request is one JSON line answered by one JSON line; `attach` then turns the
connection into a terminal stream of `type:u8 | length:u32 | payload` frames.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import pty
import pwd
import re
import select
import shlex
import signal
import socket
import socketserver
import struct
import subprocess
import termios
import threading
import time
import traceback
from dataclasses import dataclass
from pathlib import Path

SERVER = "eggie-actions"
CONF = Path(__file__).with_name("eggie-actions.tmux.conf")
SLUG = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
MAX_ID = 63
MAX_COMMAND = 4000
MAX_LINE = 64 * 1024
MAX_FRAME = 1024 * 1024
LOG_CAP = 10 * 1024 * 1024
STOP_GRACE = 5.0
FRAME_DATA, FRAME_RESIZE = 0x00, 0x01
PANES = ("#{session_name}\t#{pane_dead}\t#{@exit}\t#{pane_dead_status}"
         "\t#{session_created}\t#{pane_pid}")


class Refused(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


@dataclass(frozen=True)
class Account:
    name: str
    uid: int
    home: str


@dataclass(frozen=True)
class Pane:
    dead: bool
    exit_code: int | None
    created: int
    pid: int

    def run(self) -> dict:
        return {"state": "exited" if self.dead else "running",
                "exit_code": self.exit_code, "started_at": self.created}


def session(project: str, action: str) -> str:
    return f"{project}--{action}"


class Actions:
    def __init__(self, projects: Path, logs: Path, connect: Path, tmux_tmpdir: str | None,
                 log_cap: int = LOG_CAP):
        self.projects, self.logs, self.connect = projects, logs, connect
        self.tmux_tmpdir, self.log_cap = tmux_tmpdir, log_cap
        self.starting = threading.Lock()

    def account(self) -> Account:
        try:
            name = json.loads(self.connect.read_text()).get("user")
        except (OSError, ValueError, AttributeError):
            name = None
        if not isinstance(name, str) or not name:
            name = "root"
        try:
            entry = pwd.getpwnam(name)
        except KeyError:
            raise Refused("failed", f"no account {name!r} in this machine") from None
        return Account(name, entry.pw_uid, entry.pw_dir)

    def tmux_argv(self, *args: str) -> list[str]:
        a = self.account()
        env = [f"HOME={a.home}", f"USER={a.name}", f"LOGNAME={a.name}", "SHELL=/bin/bash",
               "PATH=/usr/local/bin:/usr/bin:/bin", "TERM=xterm-256color", "LANG=C.UTF-8"]
        if self.tmux_tmpdir:
            env.append(f"TMUX_TMPDIR={self.tmux_tmpdir}")
        return ["runuser", "-u", a.name, "--", "env", "-i", *env,
                "tmux", "-L", SERVER, "-f", str(CONF), *args]

    def tmux(self, *args: str, check: bool = True) -> str:
        result = subprocess.run(self.tmux_argv(*args), capture_output=True,
                                encoding="utf-8", errors="replace")
        if check and result.returncode != 0:
            raise Refused("failed", result.stderr.strip() or f"tmux {args[0]} failed")
        return result.stdout

    def panes(self) -> dict[str, Pane]:
        # No server yet (nothing ever started) answers non-zero: no runs.
        out = self.tmux("list-panes", "-a", "-F", PANES, check=False)
        panes = {}
        for line in out.splitlines():
            name, dead, recorded, status, created, pid = (line.split("\t") + [""] * 6)[:6]
            if not (created.isdigit() and pid.isdigit()):
                continue
            # Only the session's first pane is the action's; a stray one never overwrites it.
            if name in panes:
                continue
            code = recorded if recorded.isdigit() else status
            panes[name] = Pane(dead == "1", int(code) if dead == "1" and code.isdigit() else None,
                               int(created), int(pid))
        return panes

    def project_runs(self, project: str) -> dict[str, Pane]:
        prefix = f"{project}--"
        return {name[len(prefix):]: pane for name, pane in self.panes().items()
                if name.startswith(prefix)}

    def start(self, project: str, action: str, command: str, log: bool) -> dict:
        name = session(project, action)
        with self.starting:
            pane = self.panes().get(name)
            if pane and not pane.dead:
                raise Refused("action_running", f"{action} is already running")
            if pane:
                self.tmux("kill-session", "-t", f"={name}")
            # The command waits for `go` so pipe-pane is in place before its first byte.
            # Its exit code is recorded in @exit, not left to pane_dead_status: tmux
            # sometimes never reaps a pane's process, and then has no status at all.
            # The newline: tmux reads an argument ending in `;` as a command separator.
            self.tmux("new-session", "-d", "-s", name, "-c", str(self.projects / project),
                      "-x", "120", "-y", "32", "-e", f"EGGIE_ACTION_COMMAND={command}\n",
                      f'tmux -L {SERVER} wait-for go-{name}; bash -lc "$EGGIE_ACTION_COMMAND"; '
                      f'tmux -L {SERVER} set-option -p -t "$TMUX_PANE" @exit $?')
            try:
                if log:
                    path = self.fresh_log(project, action)
                    # Past the cap, output is dropped until the next start.
                    self.tmux("pipe-pane", "-o", "-t", f"={name}:",
                              f"stdbuf -o0 head -c {self.log_cap} >> {shlex.quote(str(path))}")
            except BaseException:
                self.tmux("kill-session", "-t", f"={name}", check=False)
                raise
            self.tmux("wait-for", "-S", f"go-{name}")
            pane = self.panes().get(name)
        if pane is None:
            raise Refused("failed", f"{action} did not start")
        return pane.run()

    def fresh_log(self, project: str, action: str) -> Path:
        folder = self.logs / project
        folder.mkdir(exist_ok=True)
        # The docker group can write logs/: never truncate or chown through a planted link.
        dir_fd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            fd = os.open(f"{action}.log", os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW,
                         0o640, dir_fd=dir_fd)
        finally:
            os.close(dir_fd)
        try:
            # tmux's pipe runs as the account, so it must own the file it appends to.
            os.fchown(fd, self.account().uid, -1)
            os.fchmod(fd, 0o640)
        finally:
            os.close(fd)
        return folder / f"{action}.log"

    def stop(self, names: list[str], *, remove: bool = False) -> None:
        panes = self.panes()
        groups = {n: panes[n].pid for n in names if n in panes and not panes[n].dead}
        for pid in groups.values():
            signal_group(pid, signal.SIGTERM)
        # The group, not pane_dead: the pane's shell can die on SIGTERM while
        # the command it started ignores it and keeps running.
        deadline = time.monotonic() + STOP_GRACE
        while True:
            alive = [n for n, pid in groups.items() if signal_group(pid, 0)]
            if not alive or time.monotonic() >= deadline:
                break
            time.sleep(0.1)
        for name in names:
            if name in alive:
                signal_group(groups[name], signal.SIGKILL)
            if name in alive or (remove and name in panes):
                self.tmux("kill-session", "-t", f"={name}", check=False)
        # tmux marks a pane dead a moment after its last process goes.
        ended = [n for n in groups if n not in alive and not remove]
        deadline = time.monotonic() + 1
        while ended and time.monotonic() < deadline:
            panes = self.panes()
            ended = [n for n in ended if n in panes and not panes[n].dead]
            if ended:
                time.sleep(0.05)

    def output(self, name: str) -> str:
        if name not in self.panes():
            raise Refused("not_running", "this action has no run to show")
        # -J joins wrapped lines but keeps their padding; the screen's empty rows below the
        # output go too.
        text = self.tmux("capture-pane", "-p", "-J", "-S", "-", "-E", "-", "-t", f"={name}:")
        text = "\n".join(line.rstrip() for line in text.splitlines()).rstrip()
        return text + "\n" if text else ""

    def attach(self, handler: socketserver.StreamRequestHandler, name: str, cols: int, rows: int) -> None:
        if name not in self.panes():
            raise Refused("not_running", "this action has no run to attach to")
        argv = self.tmux_argv("attach-session", "-t", f"={name}")
        pid, fd = pty.fork()
        if pid == 0:
            try:
                os.closerange(3, 65536)
                set_size(0, cols, rows)
                os.execvp(argv[0], argv)
            finally:
                os._exit(127)
        try:
            reply(handler, {"ok": True})
            relay(handler, fd)
        finally:
            # Only this attach client goes: the session and its run carry on.
            end_child(pid)
            os.close(fd)


def signal_group(pid: int, sig: int) -> bool:
    try:
        os.killpg(pid, sig)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def set_size(fd: int, cols: int, rows: int) -> None:
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


def end_child(pid: int) -> None:
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            pass
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if os.waitpid(pid, os.WNOHANG) != (0, 0):
                return
            time.sleep(0.05)


def relay(handler: socketserver.StreamRequestHandler, fd: int) -> None:
    done = threading.Event()

    def screen_to_socket() -> None:
        try:
            while not done.is_set():
                if not select.select([fd], [], [], 0.5)[0]:
                    continue
                data = os.read(fd, 65536)
                if not data:
                    break
                handler.request.sendall(struct.pack("!BI", FRAME_DATA, len(data)) + data)
        except OSError:
            pass
        finally:
            # Wakes the reader below when tmux goes first.
            try:
                handler.request.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    pump = threading.Thread(target=screen_to_socket, daemon=True)
    pump.start()
    try:
        while True:
            header = handler.rfile.read(5)
            if len(header) < 5:
                break
            kind, length = struct.unpack("!BI", header)
            if length > MAX_FRAME:
                break
            payload = handler.rfile.read(length)
            if len(payload) < length:
                break
            if kind == FRAME_DATA:
                while payload:
                    payload = payload[os.write(fd, payload):]
            elif kind == FRAME_RESIZE and length == 4:
                set_size(fd, *struct.unpack("!HH", payload))
    except OSError:
        pass
    finally:
        done.set()
        pump.join(2)


def reply(handler: socketserver.StreamRequestHandler, message: dict) -> None:
    handler.wfile.write(json.dumps(message).encode() + b"\n")


class Handler(socketserver.StreamRequestHandler):
    server: Server

    def handle(self) -> None:
        line = self.rfile.readline(MAX_LINE)
        if not line:
            return
        try:
            try:
                request = json.loads(line)
            except ValueError:
                raise Refused("invalid", "the request is not one JSON line") from None
            answer = self.answer(request)
        except Refused as e:
            answer = {"ok": False, "code": e.code, "message": e.message}
        except Exception as e:
            traceback.print_exc()
            answer = {"ok": False, "code": "failed", "message": str(e)}
        if answer is not None:
            try:
                reply(self, answer)
            except OSError:
                pass

    def answer(self, request: object) -> dict | None:
        actions = self.server.actions
        op = request.get("op") if isinstance(request, dict) else None
        if op == "status":
            runs = actions.project_runs(self.slug(request, "project"))
            return {"ok": True, "runs": {a: pane.run() for a, pane in runs.items()}}
        if op == "stop-project":
            project = self.slug(request, "project")
            actions.stop([session(project, a) for a in actions.project_runs(project)], remove=True)
            return {"ok": True}
        if op not in ("start", "stop", "attach", "output"):
            raise Refused("invalid", f"unknown op {op!r}")
        project, action = self.slug(request, "project"), self.slug(request, "action")
        name = session(project, action)
        if op == "stop":
            actions.stop([name])
            pane = actions.panes().get(name)
            return {"ok": True, "run": pane.run() if pane else None}
        if op == "output":
            return {"ok": True, "text": actions.output(name)}
        if op == "start":
            command, log = request.get("command"), request.get("log")
            if not isinstance(command, str) or not command.strip() or len(command) > MAX_COMMAND \
                    or "\0" in command or not isinstance(log, bool):
                raise Refused("invalid", "start needs a command and a log flag")
            if not (actions.projects / project).is_dir():
                raise Refused("invalid", f"no project {project!r}")
            return {"ok": True, "run": actions.start(project, action, command, log)}
        cols, rows = request.get("cols"), request.get("rows")
        if not all(type(v) is int and 1 <= v <= 1000 for v in (cols, rows)):
            raise Refused("invalid", "attach needs cols and rows")
        actions.attach(self, name, cols, rows)
        return None

    def slug(self, request: dict, key: str) -> str:
        value = request.get(key)
        if not isinstance(value, str) or len(value) > MAX_ID or not SLUG.match(value):
            raise Refused("invalid", f"{key} must be a slug")
        return value


class Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True
    actions: Actions


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--socket", default="/opt/eggie/actions/actions.sock")
    parser.add_argument("--projects-root", default="/opt/eggie/projects")
    parser.add_argument("--logs", default="/opt/eggie/actions/logs")
    parser.add_argument("--connect", default="/opt/eggie/connect.json")
    parser.add_argument("--tmux-tmpdir")
    parser.add_argument("--log-cap", type=int, default=LOG_CAP)
    args = parser.parse_args()
    sock = Path(args.socket)
    sock.unlink(missing_ok=True)
    # 0660 from the start; the directory's setgid gives it the docker group.
    previous = os.umask(0o117)
    try:
        server = Server(str(sock), Handler)
    finally:
        os.umask(previous)
    server.actions = Actions(Path(args.projects_root), Path(args.logs), Path(args.connect),
                             args.tmux_tmpdir, args.log_cap)
    server.serve_forever()


if __name__ == "__main__":
    main()
