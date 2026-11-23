"""Codex on a remote instance: install it, then sign in through the browser here."""

from __future__ import annotations

import queue
import re
import socket
import subprocess
import threading
import time
import webbrowser

from . import ssh
from .ssh import Probe

CALLBACK_PORT = 1455
SETUP_COMMAND = "curl -fsSL https://chatgpt.com/codex/install.sh | CODEX_NON_INTERACTIVE=1 sh"

_URL = re.compile(r"https://\S+")


def ensure_installed(run, alias: str, setup_command: str = SETUP_COMMAND) -> Probe:
    if ssh.login_shell(run, alias, "command -v codex").ok:
        return Probe(True, "")
    installed = ssh.login_shell(run, alias, setup_command)
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
    proc = popen(["ssh", "-T", "-o", "ExitOnForwardFailure=yes",
                  "-L", f"{CALLBACK_PORT}:127.0.0.1:{CALLBACK_PORT}",
                  alias, "bash -lc 'codex login'"])
    lines: queue.Queue = queue.Queue()

    def pump():
        for line in proc.stdout:
            lines.put(line)
        lines.put(None)

    threading.Thread(target=pump, daemon=True).start()

    start = time.monotonic()
    last = ""
    url = None
    while url is None:
        left = url_timeout - (time.monotonic() - start)
        try:
            line = lines.get(timeout=max(left, 0.01)) if left > 0 else None
            if left <= 0:
                raise queue.Empty
        except queue.Empty:
            proc.kill()
            return Probe(False, last or "No sign-in link appeared")
        if line is None:
            break
        line = line.strip()
        if line:
            last = line
        found = _URL.search(line)
        if found:
            url = found.group(0).rstrip(".,)")
    if url is None:
        code = proc.wait()
        return Probe(False, last or f"codex login exited {code}")

    open_url(url)
    try:
        code = proc.wait(timeout=max(timeout - (time.monotonic() - start), 1))
    except subprocess.TimeoutExpired:
        proc.kill()
        return Probe(False, "Sign-in was not finished in time")
    return Probe(True, "") if code == 0 else Probe(False, last or f"codex login exited {code}")
