from __future__ import annotations

# The "Eggie" SSH connection in Claude Code's ~/.claude/settings.json, so the
# Claude desktop app lists the VM without the user adding it by hand. Written
# when the user clicks "Add Eggie to Claude Code" in its guide in the console
# (the desktop app's agent-connect screen does the work), never at setup: Claude Code may be
# installed long after Eggie.
#
# Claude Code keeps its SSH connections in the `sshConfigs` list of that file.
# Ours points at the `eggie` alias ssh_alias.py writes into ~/.ssh/config, so
# the address, port and key live in one place. Eggie owns only the entry with
# its own id; every other key and entry in the file is left as it was.

import json
from pathlib import Path

from . import ssh_alias

ENTRY_ID = "eggie"
ENTRY = {
    "id": ENTRY_ID,
    "name": "Eggie",
    "sshHost": ssh_alias.ALIAS,
    # Where the runtime keeps projects (install.sh).
    "startDirectory": "/opt/eggie/projects",
}


def settings_path(claude_dir: Path) -> Path:
    return claude_dir / "settings.json"


def _load(path: Path) -> dict:
    """The settings as a dict. Raises ValueError on anything Eggie must not
    rewrite: a file that is not a JSON object, or `sshConfigs` that is not a
    list. Rewriting those would lose what the user has there."""
    try:
        text = path.read_text()
    except FileNotFoundError:
        return {}
    if not text.strip():
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"{path} is not valid JSON ({e.msg})") from None
    if not isinstance(data, dict):
        raise ValueError(f"{path} does not hold a JSON object")
    if not isinstance(data.get("sshConfigs", []), list):
        raise ValueError(f"'sshConfigs' in {path} is not a list")
    return data


def _ours(entry) -> bool:
    return isinstance(entry, dict) and entry.get("id") == ENTRY_ID


def _save(path: Path, data: dict) -> None:
    ssh_alias._write(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                     mode=0o600)


def installed(claude_dir: Path) -> bool:
    try:
        return any(_ours(e) for e in _load(settings_path(claude_dir)).get("sshConfigs", []))
    except (OSError, ValueError):
        return False


def install(claude_dir: Path) -> None:
    """Add or refresh the Eggie entry. A missing ~/.claude is created: the
    user asked for the entry, and Claude Code reads it once it starts."""
    claude_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    # Resolved so a dotfiles symlink is written through, not replaced.
    path = settings_path(claude_dir).resolve()
    data = _load(path)
    configs = list(data.get("sshConfigs", []))
    at = next((i for i, e in enumerate(configs) if _ours(e)), None)
    if at is None:
        configs.append(dict(ENTRY))
    else:
        configs[at] = dict(ENTRY)
    if configs == data.get("sshConfigs"):
        return
    data["sshConfigs"] = configs
    _save(path, data)


def remove(claude_dir: Path) -> None:
    path = settings_path(claude_dir)
    if not path.exists():
        return
    path = path.resolve()
    data = _load(path)
    configs = data.get("sshConfigs", [])
    kept = [e for e in configs if not _ours(e)]
    if len(kept) == len(configs):
        return
    if kept:
        data["sshConfigs"] = kept
    else:
        del data["sshConfigs"]
    _save(path, data)
