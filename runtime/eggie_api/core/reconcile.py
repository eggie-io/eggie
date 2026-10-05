from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .constants import COMPOSE_FILE, VERIFY_PROJECT_ID
from .project import _slug


@dataclass(frozen=True)
class Discovered:
    name: str
    seen_at: float
    adoptable: bool
    reason: str | None


def examine(folder: Path) -> Discovered:
    reason = None
    if _slug(folder.name) != folder.name:
        reason = "bad_name"
    elif not (folder / COMPOSE_FILE).is_file():
        reason = "compose_missing"
    return Discovered(folder.name, folder.stat().st_mtime, reason is None, reason)


def discover(root: Path, known: set[str]) -> list[Discovered]:
    """Folders under `root` with no project row. Files are skipped, which
    also skips the API's `*.upload` staging files."""
    if not root.is_dir():
        return []
    with os.scandir(root) as entries:
        folders = sorted(e.name for e in entries
                         if e.is_dir(follow_symlinks=False)
                         and not e.name.startswith(".")
                         and e.name not in known
                         and e.name != VERIFY_PROJECT_ID)
    found = []
    for name in folders:
        try:
            found.append(examine(root / name))
        except FileNotFoundError:
            # Removed between the scandir above and this stat -- a coding
            # agent's own scratch folder churn, not a project to report.
            continue
    return found
