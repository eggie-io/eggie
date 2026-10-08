from __future__ import annotations

import os
from pathlib import Path
from typing import Awaitable, Callable

from ..errors import Conflict, DiskFull, NotFound
from ..infra import disk, files
from .loader import ProjectLoader
from .locks import ProjectLocks

Receive = Callable[[Path], Awaitable[Path]]


def disk_full() -> DiskFull:
    return DiskFull("disk_full", "Eggie's disk is full. Free up space in "
                    "the desktop app, then try again.")


class FileService:
    def __init__(self, loader: ProjectLoader, locks: ProjectLocks):
        self._loader = loader
        self._locks = locks

    def path(self, project_id: str, rel_path: str) -> Path:
        self._loader.require(project_id)
        return files.resolve_within(self._loader.dir(project_id), rel_path)

    # Held across the whole body, not just the extract: an archive that lands
    # between the overlay being written and compose reading docker-compose.yml
    # starts a project from two different versions of itself. Refused rather
    # than queued, like every other lock holder here -- the host client retries
    # a `project_busy` on its own, where the wait can be bounded and reported.
    async def extract(self, project_id: str, receive: Receive) -> dict:
        self._loader.require(project_id)
        d = self._loader.dir(project_id)
        with self._locks.held(project_id):
            tmp = await receive(d.parent)
            try:
                try:
                    files.extract_archive(tmp, d)
                except OSError as e:
                    if disk.is_disk_full(e):
                        raise disk_full() from e
                    raise
            finally:
                tmp.unlink(missing_ok=True)
        return {"id": project_id, "files": files.list_tree(d)}

    def list(self, project_id: str, dir: str | None) -> dict:
        self._loader.require(project_id)
        if dir is None:
            return {"files": files.list_tree(self._loader.dir(project_id))}
        try:
            return {"dir": dir,
                    "entries": files.list_dir(self._loader.dir(project_id), dir)}
        except FileNotFoundError:
            raise NotFound("folder_not_found",
                           f"no folder '{dir}' in project '{project_id}'") from None
        except PermissionError as e:
            raise Conflict("permission_denied",
                           "Eggie can't look inside that folder; a program "
                           "in the project owns it.") from e

    async def write(self, project_id: str, rel_path: str, receive: Receive) -> dict:
        target = self.path(project_id, rel_path)
        d = self._loader.dir(project_id)
        with self._locks.held(project_id):
            # Staged next to the project directory, not inside it, so a listing
            # never catches the upload half-written.
            tmp = await receive(d.parent)
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(tmp, target)
            finally:
                tmp.unlink(missing_ok=True)
        return {"path": rel_path, "size": target.stat().st_size}

    def file(self, project_id: str, rel_path: str) -> Path:
        target = self.path(project_id, rel_path)
        if not target.is_file():
            raise NotFound("file_not_found",
                           f"no file '{rel_path}' in project '{project_id}'")
        return target

    def delete(self, project_id: str, rel_path: str) -> dict:
        target = self.file(project_id, rel_path)
        with self._locks.held(project_id):
            target.unlink()
        return {"path": rel_path, "deleted": True}
