from __future__ import annotations

# Trusted project folders in Codex's config.toml, so Codex doesn't ask to
# trust a folder Eggie opens in it. Eggie only appends tables: a file it can't
# parse, or one the addition would break, is never rewritten.

import tomllib
from pathlib import Path

from . import ssh_alias


def config_path(codex_home: Path) -> Path:
    return codex_home / "config.toml"


def _read(path: Path) -> str:
    try:
        with path.open(encoding="utf-8", newline="") as f:
            return f.read()
    except FileNotFoundError:
        return ""


def trusted(codex_home: Path, folders: list[str]) -> bool:
    """Whether config.toml has an entry for each folder; an unreadable file has none."""
    try:
        projects = tomllib.loads(_read(config_path(codex_home))).get("projects", {})
    except (OSError, ValueError):
        return False
    return isinstance(projects, dict) and all(f in projects for f in folders)


def trust(codex_home: Path, folders: list[str]) -> bool:
    """Mark each folder trusted unless config.toml already has an entry for it
    (an "untrusted" one is the person's choice). True when the file changed."""
    path = config_path(codex_home)
    if path.exists():
        path = path.resolve()  # write through a dotfiles symlink
    text = _read(path)
    projects = tomllib.loads(text).get("projects", {})
    if not isinstance(projects, dict):
        raise ValueError(f"'projects' in {path} is not a table")
    missing = [f for f in folders if f not in projects]
    if not missing:
        return False
    if any("'" in f or "\n" in f for f in missing):
        raise ValueError("a folder name can't be written as a TOML key")
    eol = "\r\n" if "\r\n" in text else "\n"
    added = "".join(f"{eol}[projects.'{f}']{eol}trust_level = \"trusted\"{eol}" for f in missing)
    result = text + (eol if text and not text.endswith("\n") else "") + added
    # An inline `projects = {...}` can't take a [projects.x] table after it.
    tomllib.loads(result)
    ssh_alias._write(path, result, mode=0o600, encoding="utf-8", newline="")
    return True
