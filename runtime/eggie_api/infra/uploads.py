from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from ..errors import BadRequest, Conflict, DiskFull, NotFound
from .disk import is_disk_full

CHUNK_SIZE = 8 * 1024 * 1024
RESERVE = 1024 ** 3
MAX_AGE = 7 * 24 * 3600
_ID = re.compile(r"^[0-9a-f]{32}$")


@dataclass(frozen=True)
class Upload:
    id: str
    project_id: str
    path: str
    size: int
    offset: int
    fingerprint: str
    replace: bool
    updated_at: float

    def as_dict(self) -> dict:
        return asdict(self)


class UploadStore:
    """Partial uploads, one folder each: `data` plus `meta.json`. The size of
    `data` is the offset -- there is no second counter to fall out of step
    with it after a crash. `append` and `finish` on the same upload id
    serialize through a per-id lock, so two concurrent chunks can never both
    pass the offset check and both write."""

    def __init__(self, root: Path, *, free_bytes: Callable[[], int],
                 clock=time.time, opener=open):
        self._root = Path(root)
        self._free_bytes = free_bytes
        self._clock = clock
        self._open = opener
        self._locks_guard = threading.Lock()
        self._locks: dict[str, threading.Lock] = {}

    def _dir(self, upload_id: str) -> Path:
        if not _ID.match(upload_id or ""):
            raise NotFound("upload_not_found", "no such upload")
        folder = self._root / upload_id
        if not (folder / "meta.json").is_file():
            raise NotFound("upload_not_found", "no such upload")
        return folder

    def _load(self, folder: Path) -> Upload:
        meta = json.loads((folder / "meta.json").read_text())
        data = folder / "data"
        return Upload(id=folder.name, offset=data.stat().st_size,
                      updated_at=data.stat().st_mtime, **meta)

    def _load_checked(self, folder: Path) -> Upload:
        # A meta.json with a stray or missing key is as unreadable as a
        # missing upload -- every public method should only ever raise
        # NotFound for a bad upload, never a raw parsing exception.
        try:
            return self._load(folder)
        except (OSError, ValueError, TypeError, KeyError) as e:
            raise NotFound("upload_not_found", "no such upload") from e

    def _lock_for(self, upload_id: str) -> threading.Lock:
        with self._locks_guard:
            lock = self._locks.get(upload_id)
            if lock is None:
                lock = threading.Lock()
                self._locks[upload_id] = lock
            return lock

    def _forget_lock(self, upload_id: str) -> None:
        with self._locks_guard:
            self._locks.pop(upload_id, None)

    def start(self, project_id: str, path: str, size: int, fingerprint: str,
              replace: bool) -> Upload:
        free = self._free_bytes()
        if size + RESERVE > free:
            raise DiskFull("not_enough_space",
                           "this file is bigger than the room Eggie has left",
                           extra={"free_bytes": free})
        upload_id = secrets.token_hex(16)
        folder = self._root / upload_id
        folder.mkdir(parents=True)
        (folder / "data").touch()
        (folder / "meta.json").write_text(json.dumps({
            "project_id": project_id, "path": path, "size": size,
            "fingerprint": fingerprint, "replace": replace}))
        return self._load(folder)

    def get(self, upload_id: str) -> Upload:
        return self._load_checked(self._dir(upload_id))

    def append(self, upload_id: str, offset: int, data: bytes) -> Upload:
        folder = self._dir(upload_id)
        with self._lock_for(upload_id):
            up = self._load_checked(folder)
            if offset != up.offset:
                raise Conflict("offset_mismatch", "the upload is at a "
                               "different offset", extra={"offset": up.offset})
            if up.offset + len(data) > up.size:
                raise BadRequest("too_much_data", "more bytes than the upload "
                                 "declared")
            target = folder / "data"
            try:
                with self._open(target, "r+b") as f:
                    f.seek(offset)
                    f.write(data)
                    f.flush()
                    os.fsync(f.fileno())
            except OSError as e:
                # Torn chunks must not survive: the next PATCH resumes from the
                # offset the client was told, not from wherever the write died.
                real_offset = offset
                try:
                    os.truncate(target, offset)
                except OSError:
                    # The rollback itself failed -- report what actually
                    # landed on disk, since a sequential partial write is
                    # still a correct prefix of the chunk.
                    try:
                        real_offset = target.stat().st_size
                    except OSError:
                        real_offset = offset
                if is_disk_full(e):
                    raise DiskFull("disk_full", "Eggie ran out of room",
                                   extra={"offset": real_offset}) from e
                raise
            return self._load(folder)

    def finish(self, upload_id: str, target: Path) -> None:
        folder = self._dir(upload_id)
        with self._lock_for(upload_id):
            up = self._load_checked(folder)
            if up.offset != up.size:
                raise Conflict("incomplete", "the upload is not complete yet",
                               extra={"offset": up.offset})
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(folder / "data", target)
            shutil.rmtree(folder, ignore_errors=True)
        self._forget_lock(upload_id)

    def cancel(self, upload_id: str) -> None:
        folder = self._dir(upload_id)
        with self._lock_for(upload_id):
            shutil.rmtree(folder, ignore_errors=True)
        self._forget_lock(upload_id)

    def _all(self) -> list[Upload]:
        if not self._root.is_dir():
            return []
        found = []
        for folder in sorted(self._root.iterdir()):
            try:
                found.append(self._load(folder))
            except (OSError, ValueError, TypeError):
                continue
        return found

    def list_for(self, project_id: str) -> list[Upload]:
        return [u for u in self._all() if u.project_id == project_id]

    def sweep(self) -> None:
        cutoff = self._clock() - MAX_AGE
        for up in self._all():
            if up.updated_at < cutoff:
                shutil.rmtree(self._root / up.id, ignore_errors=True)
                self._forget_lock(up.id)

    def drop_project(self, project_id: str) -> None:
        for up in self.list_for(project_id):
            shutil.rmtree(self._root / up.id, ignore_errors=True)
            self._forget_lock(up.id)
