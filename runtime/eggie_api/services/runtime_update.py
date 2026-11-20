from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable

from ..errors import Conflict, Unavailable

log = logging.getLogger("eggie.api")

_TAG = re.compile(r"runtime-v(\d+)\.(\d+)\.(\d+)")
# The runner picks a request up within seconds; a run is minutes at most.
_PICKUP_SECONDS = 120
_RUN_SECONDS = 1800


def _parse(tag: object) -> tuple[int, int, int] | None:
    m = _TAG.fullmatch(tag) if isinstance(tag, str) else None
    return tuple(int(g) for g in m.groups()) if m else None


def _show(v: tuple[int, int, int] | None) -> str | None:
    return ".".join(map(str, v)) if v else None


class RuntimeUpdate:
    """The API's half of the root-side updater (install/lib/update-now.sh):
    it bumps `request` and reads the files the root scripts write. Nothing is
    kept in memory, because the update recreates this very container."""

    def __init__(self, update_dir: Path, marker: Path, *,
                 clock: Callable[[], float] = time.time):
        self._dir = Path(update_dir)
        self._marker = Path(marker)
        self._clock = clock
        self._lock = threading.Lock()

    def status(self) -> dict:
        installed = self._installed()
        available = self._available()
        if installed is None or available is None or available <= installed:
            available = None
        state, error = self._state()
        return {"installed": _show(installed), "available": _show(available),
                "state": state, "error": error}

    def request(self) -> dict:
        current = self.status()
        if current["state"] == "running":
            raise Conflict("update_running", "Eggie is already updating.")
        if current["available"] is None:
            raise Conflict("update_not_available", "This Eggie is already up to date.")
        try:
            self._bump(self._dir / "request")
        except OSError:
            log.exception("could not ask the updater to run")
            raise Unavailable("update_unavailable",
                              "This Eggie can't update itself yet. Restart it to update.") from None
        return self.status()

    def _installed(self) -> tuple[int, int, int] | None:
        try:
            return _parse(self._marker.read_text().strip())
        except OSError:
            return None

    def _available(self) -> tuple[int, int, int] | None:
        data = self._json(self._dir / "available.json")
        return _parse(data.get("available"))

    def _state(self) -> tuple[str, str | None]:
        now = self._clock()
        counter = self._count(self._dir / "request")
        status = self._json(self._dir / "status.json")
        handled = status.get("request")
        handled = handled if isinstance(handled, int) and not isinstance(handled, bool) else 0
        # install.sh writes the marker last, on every install, so a marker newer
        # than an outcome means a later install overtook it. No marker: mid-install.
        installed_at = self._mtime(self._marker)
        if counter > handled:
            if installed_at > self._mtime(self._dir / "request"):
                return "idle", None
            if now - self._mtime(self._dir / "request") > _PICKUP_SECONDS:
                return "failed", "The updater inside Eggie did not start."
            return "running", None
        if status.get("state") in ("running", "failed") and installed_at > self._mtime(self._dir / "status.json"):
            return "idle", None
        if status.get("state") == "running":
            if now - self._mtime(self._dir / "status.json") > _RUN_SECONDS:
                return "failed", "The update did not finish."
            return "running", None
        if status.get("state") == "failed":
            error = status.get("error")
            return "failed", error if isinstance(error, str) else None
        return "idle", None

    @staticmethod
    def _json(path: Path) -> dict:
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _count(path: Path) -> int:
        try:
            return int(path.read_text().strip())
        except (OSError, ValueError):
            return 0

    @staticmethod
    def _mtime(path: Path) -> float:
        try:
            return path.stat().st_mtime
        except OSError:
            return 0.0

    def _bump(self, path: Path) -> None:
        with self._lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".count-")
            with os.fdopen(fd, "w") as f:
                f.write(str(self._count(path) + 1))
            os.chmod(tmp, 0o660)
            os.replace(tmp, path)
