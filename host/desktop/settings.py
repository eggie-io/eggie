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
FIRST_LAUNCH_SENT = "first_launch_sent"
# Agents with no entry of their own (Cursor; Claude Code on Windows' own VM)
# count as connected once the person pressed Done for that kitchen.
AGENTS_DONE = "agents_done"
# The WSL default distro that connecting Codex replaced, to offer it back.
PREVIOUS_DEFAULT = "previous_default"


def done_flag(agent_id: str, kitchen_id: str) -> str:
    """`kitchen_id` is a cloud kitchen's id, or "local" for this computer."""
    return f"{agent_id}:{kitchen_id}"


def _members(data: dict, key: str) -> set[str]:
    value = data.get(key)
    return {v for v in value if isinstance(v, str)} if isinstance(value, list) else set()


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
        self._write(data)

    def text(self, key: str) -> str:
        value = self._read().get(key)
        return value if isinstance(value, str) else ""

    def set_text(self, key: str, value: str) -> None:
        data = self._read()
        if value:
            data[key] = value
        else:
            data.pop(key, None)
        self._write(data)

    def members(self, key: str) -> set[str]:
        return _members(self._read(), key)

    def add_member(self, key: str, member: str) -> None:
        data = self._read()
        current = _members(data, key)
        if member in current:
            return
        data[key] = sorted(current | {member})
        self._write(data)

    def _write(self, data: dict) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(data, sort_keys=True))
