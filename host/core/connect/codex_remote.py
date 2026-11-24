"""Codex on a remote instance: install it, then sign in through the browser here."""

from __future__ import annotations

import queue
import re
import socket
import subprocess
import threading
import time
import urllib.parse
import webbrowser

from . import ssh
from .ssh import Probe

CALLBACK_PORT = 1455
# The instance's runtime says how Codex installs there, so a change to it needs no host release.
READ_SETUP = ("python3 -c 'import json;print(json.load(open("
              "\"/opt/eggie/runtime/agents/codex/agent.json\"))[\"setup\"][\"run\"])'")

_URL = re.compile(r"https://\S+")
_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_SIGN_IN_ORIGIN = ("https", "auth.openai.com")


def ensure_installed(run, alias: str) -> Probe:
    if ssh.login_shell(run, alias, "command -v codex").ok:
        return Probe(True, "")
    read = ssh.login_shell(run, alias, READ_SETUP)
    # The last line: a login shell's profile may print above it.
    setup = read.stdout.strip().splitlines()[-1:] if read.ok else []
    if not setup:
        return Probe(False, "This Eggie can't install Codex; update Eggie and try again.")
    installed = ssh.login_shell(run, alias, setup[0])
    if not installed.ok:
        lines = (installed.stderr or installed.stdout).splitlines()
        return Probe(False, lines[-1] if lines else f"install exited {installed.returncode}")
    if ssh.login_shell(run, alias, "command -v codex").ok:
        return Probe(True, "")
    return Probe(False, "Codex was installed but is not on the PATH")


def signed_in(run, alias: str) -> bool:
    return ssh.login_shell(run, alias, "codex login status").ok


def _popen(argv):
    return subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, bufsize=1)


def _port_free(port: int) -> bool:
    with socket.socket() as s:
        try:
            s.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def sign_in(alias: str, *, popen=_popen, open_url=webbrowser.open,
            port_free=_port_free, timeout: float = 600,
            url_timeout: float = 60) -> Probe:
    if not port_free(CALLBACK_PORT):
        return Probe(False, f"Port {CALLBACK_PORT} on this computer is in use; "
                            "close what uses it (often another Codex sign-in) "
                            "and run this again.")
    try:
        proc = popen(["ssh", "-T", "-o", "ExitOnForwardFailure=yes",
                      "-L", f"{CALLBACK_PORT}:127.0.0.1:{CALLBACK_PORT}",
                      alias, "bash -lc 'codex login'"])
    except OSError as e:
        return Probe(False, str(e))
    try:
        return _await_login(proc, open_url, timeout, url_timeout)
    finally:
        if proc.poll() is None:
            proc.kill()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass


def _await_login(proc, open_url, timeout: float, url_timeout: float) -> Probe:
    lines: queue.Queue = queue.Queue()

    def pump():
        for line in proc.stdout:
            lines.put(line)
        lines.put(None)

    threading.Thread(target=pump, daemon=True).start()

    start = time.monotonic()

    def remaining() -> float:
        return max(timeout - (time.monotonic() - start), 1)

    last = ""
    url = None
    while url is None:
        left = url_timeout - (time.monotonic() - start)
        try:
            if left <= 0:
                raise queue.Empty
            line = lines.get(timeout=left)
        except queue.Empty:
            return Probe(False, last or "No sign-in link appeared")
        if line is None:
            break
        line = _ANSI.sub("", line).strip()
        if line:
            last = line
        found = _URL.search(line)
        if found:
            url = found.group(0).rstrip(".,)")
    try:
        if url is None:
            code = proc.wait(timeout=remaining())
            return Probe(False, last or f"codex login exited {code}")
        if urllib.parse.urlsplit(url)[:2] != _SIGN_IN_ORIGIN:
            return Probe(False, "Codex printed an unexpected sign-in address; nothing was opened.")
        open_url(url)
        code = proc.wait(timeout=remaining())
    except subprocess.TimeoutExpired:
        return Probe(False, "Sign-in was not finished in time")
    return Probe(True, "") if code == 0 else Probe(False, last or f"codex login exited {code}")
