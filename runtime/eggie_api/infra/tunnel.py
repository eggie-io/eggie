from __future__ import annotations

import os
import tempfile
from pathlib import Path

from .docker import DOCKER


def write_token(path: Path, token: str) -> bool:
    """False when the file already holds this token (nothing written)."""
    try:
        if path.read_text() == token:
            # The installer's group-write sweep may have widened it.
            os.chmod(path, 0o640)
            return False
    except OSError:
        pass
    path.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
    # fchmod sets the mode before any byte is written, independent of umask.
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tunnel-")
    try:
        with os.fdopen(fd, "w") as f:
            os.fchmod(f.fileno(), 0o640)
            f.write(token)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return True


def remove_token(path: Path) -> None:
    path.unlink(missing_ok=True)


class TunnelClient:
    def __init__(self, runner, stack_file: Path):
        self._runner = runner
        self._base = [DOCKER, "compose", "-f", str(stack_file), "--profile", "tunnel"]

    def start(self, *, recreate: bool = False):
        # cloudflared reads its token once; a running client needs recreating
        # to pick up a new one.
        extra = ["--force-recreate"] if recreate else []
        return self._runner.exec([*self._base, "up", "-d", "--no-deps", *extra,
                                  "tunnel"])

    def stop(self):
        return self._runner.exec([*self._base, "rm", "-sf", "tunnel"])

    def running(self) -> bool:
        out = self._runner.exec([*self._base, "ps", "-q", "tunnel"])
        return out.ok and bool(out.stdout.strip())
