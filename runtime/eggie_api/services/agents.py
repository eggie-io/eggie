from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import threading
from pathlib import Path

log = logging.getLogger("eggie.api")

_ID = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_STATES = {"installing", "ready", "failed"}
_SETTLED = {"installing", "ready"}


class UnknownAgent(Exception):
    pass


class AgentStatus:
    """The API's half of the root-side agent runner (install/lib/agents.py):
    it only bumps request counters and reads the runner's status.json, so it
    never needs to see a user's home."""

    def __init__(self, status_dir: Path, agents_dir: Path):
        self._dir = Path(status_dir)
        self._agents_dir = Path(agents_dir)
        self._lock = threading.Lock()

    def status(self) -> dict:
        try:
            self._bump(self._dir / "check")
        except OSError:
            log.exception("could not ask the agent runner for a fresh check")
        return {"agents": self._read()}

    def ensure_setup(self, agent_id: str) -> dict:
        if not self._has_setup(agent_id):
            raise UnknownAgent(agent_id)
        entry = self._read().get(agent_id, {})
        if entry.get("connected") or entry.get("setup") in _SETTLED:
            return {"requested": False}
        self._bump(self._dir / "setup" / agent_id)
        self._bump(self._dir / "check")
        return {"requested": True}

    def _has_setup(self, agent_id: str) -> bool:
        if not _ID.fullmatch(agent_id):
            return False
        try:
            manifest = json.loads((self._agents_dir / agent_id / "agent.json").read_text())
        except (OSError, ValueError):
            return False
        setup = manifest.get("setup") if isinstance(manifest, dict) else None
        return isinstance(setup, dict) and isinstance(setup.get("run"), str) and bool(setup["run"].strip())

    def _read(self) -> dict:
        try:
            data = json.loads((self._dir / "status.json").read_text())
        except (OSError, ValueError):
            return {}
        agents = data.get("agents") if isinstance(data, dict) else None
        if not isinstance(agents, dict):
            return {}
        return {agent_id: {"connected": entry.get("connected") is True,
                           "setup": entry["setup"] if isinstance(entry.get("setup"), str) and entry["setup"] in _STATES else None}
                for agent_id, entry in agents.items()
                if isinstance(agent_id, str) and _ID.fullmatch(agent_id) and isinstance(entry, dict)}

    def _bump(self, path: Path) -> None:
        with self._lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                current = int(path.read_text().strip())
            except (OSError, ValueError):
                current = 0
            fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".count-")
            with os.fdopen(fd, "w") as f:
                f.write(str(current + 1))
            os.chmod(tmp, 0o660)
            os.replace(tmp, path)
