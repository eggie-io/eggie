from __future__ import annotations

# SSH remote connections in the Codex desktop app's codex-app/config.json,
# which Codex imports on its next start. Eggie owns the connections it adds
# (by sshAlias); every other key and connection is left as it was, and a file
# Eggie cannot parse is never rewritten.

import json
from collections.abc import Mapping
from pathlib import Path

from . import ssh_alias

_EMPTY = {"version": 1, "remoteConnections": []}


def config_path(codex_home: Path) -> Path:
    return codex_home / "codex-app" / "config.json"


def default_codex_home(env: Mapping[str, str], home: Path) -> Path:
    return Path(env["CODEX_HOME"]) if env.get("CODEX_HOME") else home / ".codex"


def _load(path: Path) -> dict:
    try:
        text = path.read_text()
    except FileNotFoundError:
        text = ""
    if not text.strip():
        return json.loads(json.dumps(_EMPTY))
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"{path} is not valid JSON ({e.msg})") from None
    if not isinstance(data, dict):
        raise ValueError(f"{path} does not hold a JSON object")
    if data.get("version") != 1:
        raise ValueError(f"{path} has an unsupported 'version'")
    if not isinstance(data.get("remoteConnections"), list):
        raise ValueError(f"'remoteConnections' in {path} is not a list")
    return data


def _norm(remote_path: str) -> str:
    p = remote_path.strip().replace("\\", "/")
    return p.rstrip("/") or "/"


def _is(conn, alias: str) -> bool:
    return isinstance(conn, dict) and conn.get("sshAlias") == alias


def _save(path: Path, data: dict) -> None:
    ssh_alias._write(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                     mode=0o600)


def has(codex_home: Path, alias: str) -> bool:
    try:
        data = _load(config_path(codex_home))
    except (OSError, ValueError):
        return False
    return any(_is(c, alias) for c in data["remoteConnections"])


def aliases(codex_home: Path) -> list[str]:
    try:
        data = _load(config_path(codex_home))
    except (OSError, ValueError):
        return []
    return [c["sshAlias"] for c in data["remoteConnections"]
            if isinstance(c, dict) and isinstance(c.get("sshAlias"), str)]


def add(codex_home: Path, alias: str, remote_path: str, label: str) -> bool:
    """Add the connection and its project folder; True when the file changed.
    A label already on that folder is the user's and stays."""
    path = config_path(codex_home)
    if path.exists():
        path = path.resolve()  # write through a dotfiles symlink
    data = _load(path)
    before = json.loads(json.dumps(data))
    folder = _norm(remote_path)
    conns = data["remoteConnections"]
    conn = next((c for c in conns if _is(c, alias)), None)
    if conn is None:
        conn = {"sshAlias": alias, "projects": []}
        conns.append(conn)
    projects = conn.setdefault("projects", [])
    if not any(isinstance(p, dict) and p.get("remotePath") == folder for p in projects):
        projects.append({"remotePath": folder, "label": label})
    if path.exists() and data == before:
        return False
    _save(path, data)
    return True


def remove(codex_home: Path, alias: str) -> bool:
    path = config_path(codex_home)
    if not path.exists():
        return False
    path = path.resolve()
    data = _load(path)
    kept = [c for c in data["remoteConnections"] if not _is(c, alias)]
    if len(kept) == len(data["remoteConnections"]):
        return False
    data["remoteConnections"] = kept
    _save(path, data)
    return True
