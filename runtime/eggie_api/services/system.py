from __future__ import annotations

from pathlib import Path

from .. import constants
from ..config import ApiConfig
from ..infra import connect, disk
from ..infra.docker import DOCKER


class SystemService:
    def __init__(self, config: ApiConfig, runner):
        self._config = config
        self._runner = runner

    def health(self) -> dict:
        probe = self._runner.exec([DOCKER, "version", "--format", "{{.Server.Version}}"])
        return {
            "status": "ok",
            "version": self._config.version,
            "api": constants.API_VERSION,
            "docker": {
                "reachable": probe.ok,
                "version": probe.stdout.strip() if probe.ok else "",
                "detail": "" if probe.ok else (probe.stderr or probe.stdout).strip(),
            },
        }

    def version(self) -> dict:
        return {"version": self._config.version}

    def disk(self) -> dict:
        return disk.usage(Path(self._config.projects_root))

    def connect(self) -> dict:
        return connect.facts(Path(self._config.connect_path))
