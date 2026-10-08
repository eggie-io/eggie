from __future__ import annotations

import grp
import os
import re
from pathlib import Path

from .constants import ALT_COMPOSE_FILES, DOCKER_GROUP
from .errors import EggieError


def project_id_for(name: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", name.strip().lower()).strip("-")


def project_of(directory: Path, root: Path) -> Path | None:
    """The project folder holding `directory`, or None outside `root`.

    Resolved first, so ~/projects/x (a symlink) and /opt/eggie/projects/x are
    one folder; a subfolder counts, so `eggie up` from src/ runs the project.
    """
    root = root.resolve()
    path = directory.resolve()
    for candidate in (path, *path.parents):
        if candidate.parent == root:
            return candidate
    return None


def require_id(folder: Path) -> str:
    project_id = project_id_for(folder.name)
    if not project_id:
        raise EggieError(
            f"The folder name '{folder.name}' cannot be a project name. "
            "Rename it using letters, digits and dashes.")
    if project_id != folder.name:
        # The API derives the folder from the slugged id, so any other name
        # would register a different, empty folder.
        raise EggieError(
            f"Rename the folder '{folder.name}' to '{project_id}' first, "
            "then run the command again.")
    return project_id


def docker_gid() -> int:
    try:
        return grp.getgrnam(DOCKER_GROUP).gr_gid
    except KeyError:
        raise EggieError(
            "This VM has no docker group, so Eggie is not set up here. "
            "Run `eggie setup` on your computer.") from None


def prepare_overlay_dir(project: Path, gid: int) -> None:
    """Let the API write .eggie/overlay.yml, the one file it writes here.

    It runs as a non-root member of the docker group, while a coding agent
    working as root leaves directories 755 and files 644.
    """
    eggie_dir = project / ".eggie"
    overlay = eggie_dir / "overlay.yml"
    for path in (eggie_dir, overlay):
        if path.is_symlink():
            raise EggieError(
                f"{path} is a symbolic link; Eggie will not change "
                "permissions through it. Remove the link and run the "
                "command again.")
    try:
        eggie_dir.mkdir(exist_ok=True)
        os.chown(eggie_dir, -1, gid)
        os.chmod(eggie_dir, 0o2775)
        if overlay.exists():
            os.chown(overlay, -1, gid)
            os.chmod(overlay, 0o664)
    except PermissionError as e:
        raise EggieError(
            f"Eggie could not make {eggie_dir} writable for itself "
            f"({e.strerror}). Run the command as the folder's owner.") from None


def alt_compose_file(folder: Path) -> str | None:
    for name in ALT_COMPOSE_FILES:
        if (folder / name).is_file():
            return name
    return None


def repo_name(url: str) -> str:
    """The last path segment without `.git`, for https and scp-style URLs alike."""
    return re.split(r"[/:]", url.rstrip("/"))[-1].removesuffix(".git")
