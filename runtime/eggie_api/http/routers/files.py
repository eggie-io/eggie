from __future__ import annotations

from typing import TYPE_CHECKING

import os
import tempfile
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse

from ...errors import TooLarge
from ...infra import disk
from ...services.files import disk_full

if TYPE_CHECKING:
    from ...wiring import Services


def _size_words(byte_count: int) -> str:
    """Megabytes for anything a real project could reach; bytes below that, so
    a small cap never reads as "0 MB"."""
    megabytes = byte_count / (1024 * 1024)
    return f"{megabytes:.0f} MB" if megabytes >= 1 else f"{byte_count} bytes"


async def stream_to_tempfile(request: Request, dir_: Path, *,
                             max_bytes: int) -> Path:
    # Written next to its destination, never buffered whole in memory -
    # this route body is what removes the old ~24 KB command-line ceiling.
    dir_.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=dir_, suffix=".upload")
    path = Path(name)
    written = 0
    try:
        with os.fdopen(fd, "wb") as f:
            async for chunk in request.stream():
                written += len(chunk)
                # Checked before writing: the moment the limit is passed,
                # not after the whole body has already landed on disk.
                if written > max_bytes:
                    raise TooLarge(
                        "payload_too_large",
                        "This project is larger than the "
                        f"{_size_words(max_bytes)} an upload "
                        "may be. Remove the large files or folders from it "
                        "-- build output, videos and database files are the "
                        "usual cause -- and try again.")
                try:
                    f.write(chunk)
                except OSError as e:
                    if disk.is_disk_full(e):
                        raise disk_full() from e
                    raise
    except BaseException:
        # A client that disconnects mid-upload, or trips the size cap
        # above, must not leave a temp file behind.
        path.unlink(missing_ok=True)
        raise
    return path


def build(s: Services) -> APIRouter:
    router = APIRouter()
    max_bytes = s.config.max_upload_bytes

    @router.post("/projects/{project_id}/files")
    async def upload_files(project_id: str, request: Request) -> dict:
        return await s.files.extract(
            project_id,
            lambda dir_: stream_to_tempfile(request, dir_, max_bytes=max_bytes))

    @router.get("/projects/{project_id}/files")
    def list_files(project_id: str, dir: str | None = None) -> dict:
        return s.files.list(project_id, dir)

    @router.put("/projects/{project_id}/files/{file_path:path}")
    async def write_file(project_id: str, file_path: str, request: Request) -> dict:
        return await s.files.write(
            project_id, file_path,
            lambda dir_: stream_to_tempfile(request, dir_, max_bytes=max_bytes))

    @router.get("/projects/{project_id}/files/{file_path:path}")
    def read_file(project_id: str, file_path: str):
        target = s.files.file(project_id, file_path)
        # Starlette streams this from disk; the file is never read whole.
        return FileResponse(target, filename=target.name)

    @router.delete("/projects/{project_id}/files/{file_path:path}")
    def delete_file(project_id: str, file_path: str) -> dict:
        return s.files.delete(project_id, file_path)

    return router
