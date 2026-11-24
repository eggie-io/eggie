"""ssh probes through the providers' runner shape (`argv -> CompletedProcess`)."""

from __future__ import annotations

import shlex
import time
from dataclasses import dataclass

from ..provider import Completed


@dataclass(frozen=True)
class Probe:
    ok: bool
    reason: str


def spawn(run, argv: list[str]) -> Completed:
    p = run(argv)
    return Completed(p.returncode,
                     (p.stdout or b"").decode("utf-8", "replace").strip("\n"),
                     (p.stderr or b"").decode("utf-8", "replace").strip("\n"))


def reach(run, alias: str) -> Probe:
    result = spawn(run, ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=20",
                         "-T", alias, "true"])
    if result.ok:
        return Probe(True, "")
    lines = [line.strip() for line in result.stderr.splitlines() if line.strip()]
    return Probe(False, lines[-1] if lines else f"ssh exited {result.returncode}")


def login_shell(run, alias: str, script: str) -> Completed:
    return spawn(run, ["ssh", "-o", "BatchMode=yes", "-T", alias,
                       "bash -lc " + shlex.quote(script)])


def wait_reachable(run, alias: str, *, sleep=time.sleep, monotonic=time.monotonic,
                   timeout: float = 60, every: float = 2) -> Probe:
    deadline = monotonic() + timeout
    while True:
        probe = reach(run, alias)
        if probe.ok or monotonic() >= deadline:
            return probe
        sleep(every)
