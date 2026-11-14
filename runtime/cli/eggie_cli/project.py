from __future__ import annotations

import grp
import os
import pwd
import re
from pathlib import Path

from .constants import ALT_COMPOSE_FILES, DOCKER_GROUP
from .errors import EggieError


def project_id_for(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")


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


def prepare_overlay_dir(project: Path, gid: int, name: str = "overlay.yml") -> None:
    """Let the API write .eggie/<name> (overlay.yml, or project.yml for recipes).

    The API runs as a non-root member of the docker group (uid 1000), while
    a coding agent working as root leaves directories 755 and files 644.
    When the API creates overlay.yml itself, the file belongs to it, and only
    a file's owner may chown or chmod it -- so nothing is changed that is
    already right, and a foreign overlay that is wrong is replaced instead.
    """
    eggie_dir = project / ".eggie"
    overlay = eggie_dir / name
    for path in (eggie_dir, overlay):
        if path.is_symlink():
            raise EggieError(
                f"{path} is a symbolic link; Eggie will not change "
                "permissions through it. Remove the link and run the "
                "command again.")
    try:
        eggie_dir.mkdir(exist_ok=True)
    except PermissionError as e:
        raise EggieError(
            f"Eggie could not create {eggie_dir} ({e.strerror}). "
            "Run the command as the folder's owner.") from None
    try:
        _make_group_writable(eggie_dir, gid, 0o2775)
    except PermissionError as e:
        raise _not_writable(eggie_dir, e) from None
    if not overlay.exists():
        return
    try:
        _make_group_writable(overlay, gid, 0o664)
    except PermissionError:
        _replace_with_own_copy(overlay, gid)


def _make_group_writable(path: Path, gid: int, mode: int) -> None:
    st = path.stat()
    if st.st_gid == gid and st.st_mode & mode == mode:
        return
    os.chown(path, -1, gid)
    os.chmod(path, mode)


def _replace_with_own_copy(overlay: Path, gid: int) -> None:
    # The folder is group-writable without the sticky bit, so a member of the
    # docker group may replace a file there that it is not allowed to chmod.
    tmp = overlay.with_name(f".{overlay.name}.tmp")
    try:
        tmp.write_bytes(overlay.read_bytes())
        os.chown(tmp, -1, gid)
        os.chmod(tmp, 0o664)
        os.replace(tmp, overlay)
    except PermissionError as e:
        tmp.unlink(missing_ok=True)
        raise _not_writable(overlay, e) from None


def _not_writable(path: Path, e: PermissionError) -> EggieError:
    st = path.stat()
    return EggieError(
        f"Eggie could not make {path} writable for itself ({e.strerror}): "
        f"it is owned by {_user(st.st_uid)} with mode "
        f"{oct(st.st_mode & 0o7777)[2:]}. Ask its owner, or root, to remove "
        "it or run the command again.")


def _user(uid: int) -> str:
    try:
        return f"{pwd.getpwuid(uid).pw_name} (uid {uid})"
    except KeyError:
        return f"uid {uid}"


def alt_compose_file(folder: Path) -> str | None:
    for name in ALT_COMPOSE_FILES:
        if (folder / name).is_file():
            return name
    return None


def repo_name(url: str) -> str:
    """The last path segment without `.git`, for https and scp-style URLs alike."""
    return re.split(r"[/:]", url.rstrip("/"))[-1].removesuffix(".git")
