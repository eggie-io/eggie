from __future__ import annotations

from ..errors import Conflict, NotFound
from ..infra import files
from ..infra.uploads import CHUNK_SIZE, UploadStore
from .loader import ProjectLoader
from .locks import ProjectLocks


class UploadService:
    def __init__(self, store: UploadStore, loader: ProjectLoader, locks: ProjectLocks):
        self._store = store
        self._loader = loader
        self._locks = locks

    def start(self, project_id: str, *, path: str, size: int, fingerprint: str,
              replace: bool) -> dict:
        self._loader.require(project_id)
        target = files.resolve_within(self._loader.dir(project_id), path)
        if target.is_dir():
            # `replace` means "overwrite this file", never "delete this
            # folder and put a file where it was" -- that has no undo.
            raise Conflict("path_is_folder", f"'{path}' is a folder in the project")
        if target.exists() and not replace:
            raise Conflict("file_exists", f"'{path}' is already in the project")
        up = self._store.start(project_id, path, size, fingerprint, replace)
        if up.size == 0:
            return self.finish(up.id)
        return {"upload_id": up.id, "offset": 0, "size": up.size,
                "chunk_size": CHUNK_SIZE, "done": False}

    def list(self, project_id: str) -> dict:
        self._loader.require(project_id)
        self._store.sweep()
        return {"uploads": [u.as_dict() for u in self._store.list_for(project_id)]}

    def get(self, upload_id: str) -> dict:
        return self._store.get(upload_id).as_dict()

    def append(self, upload_id: str, offset: int, data: bytes) -> dict:
        up = self._store.append(upload_id, offset, data)
        if up.offset == up.size:
            return self.finish(upload_id)
        return {"upload_id": upload_id, "offset": up.offset, "size": up.size,
                "done": False}

    def finish(self, upload_id: str) -> dict:
        up = self._store.get(upload_id)
        # The existence check has to happen inside the lock too, not just the
        # write: checked first and locked after, delete could still finish in
        # the gap between the two and this would resurrect the folder it
        # just removed.
        with self._locks.held(up.project_id):
            try:
                self._loader.require(up.project_id)
            except NotFound:
                self._store.cancel(upload_id)
                raise
            try:
                self._store.finish(upload_id, files.resolve_within(
                    self._loader.dir(up.project_id), up.path))
            except PermissionError as e:
                raise Conflict("permission_denied",
                               "Eggie can't write into that folder; a program "
                               "in the project owns it. Pick another folder.") from e
        return {"upload_id": upload_id, "offset": up.size, "size": up.size,
                "done": True}

    def cancel(self, upload_id: str) -> dict:
        self._store.cancel(upload_id)
        return {"upload_id": upload_id, "cancelled": True}

    def drop_project(self, project_id: str) -> None:
        self._store.drop_project(project_id)
