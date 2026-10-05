from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from typing import Iterator


@dataclass(frozen=True)
class Completed:
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


class LocalRunner:
    """Runs commands where they belong once the API is inside the VM: here.

    Mirrors the host `VmProvider.exec` contract on purpose — a failure comes
    back as a `Completed` with `.ok` False and is never raised — so the
    duck-typed callers in `core/lifecycle.py` work unchanged on either side.
    """

    def exec(self, argv: list[str], *, root: bool = False,
             env: dict | None = None) -> Completed:
        # `root` is accepted and ignored: it exists only for signature
        # compatibility with the host's `VmProvider.exec`. The API runs as
        # a non-root user (Task 5) -- its ability to reach the daemon comes
        # from the mounted socket and docker-group membership, not from uid 0.
        try:
            proc = subprocess.run(argv, capture_output=True, text=True,
                                  env=None if env is None else {**os.environ, **env})
        except OSError as e:
            # A missing binary must read like a failed command, not a 500.
            return Completed(127, "", str(e))
        return Completed(proc.returncode, proc.stdout, proc.stderr)

    def stream(self, argv: list[str], *, root: bool = False) -> Iterator[str]:
        """Line-by-line output for follow mode; the buffered `exec` above would
        never return on `docker compose logs --follow`."""
        proc = subprocess.Popen(argv, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, bufsize=1)
        try:
            yield from proc.stdout
        finally:
            # The reader disconnected: don't leave a `logs -f` child behind.
            proc.terminate()
            proc.wait()
