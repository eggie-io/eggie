"""Desktop preferences that must outlive the VM.

Kept out of install-state.json on purpose: resetting or removing the VM deletes
that file, which would re-arm "turn autostart on once" for a user who had
turned it off.
"""
from __future__ import annotations

import json
from pathlib import Path

AUTOSTART_SET_ONCE = "autostart_set_once"
TRAY_NOTICE_SHOWN = "tray_notice_shown"


class Settings:
    def __init__(self, path):
        self._path = Path(path)

    def _read(self) -> dict:
        try:
            data = json.loads(self._path.read_text())
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def get(self, key: str) -> bool:
        return self._read().get(key) is True

    def set(self, key: str, value: bool) -> None:
        data = self._read()
        data[key] = bool(value)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(data, sort_keys=True))
