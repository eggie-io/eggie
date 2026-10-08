from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Request
from starlette.concurrency import run_in_threadpool

from ...errors import BadRequest, TooLarge
from ...infra.uploads import CHUNK_SIZE
from ..schemas import StartUpload

if TYPE_CHECKING:
    from ...wiring import Services


def build(s: Services) -> APIRouter:
    router = APIRouter()

    @router.post("/projects/{project_id}/uploads", status_code=201)
    def start_upload(project_id: str, body: StartUpload) -> dict:
        return s.uploads.start(project_id, path=body.path, size=body.size,
                               fingerprint=body.fingerprint, replace=body.replace)

    @router.get("/projects/{project_id}/uploads")
    def pending_uploads(project_id: str) -> dict:
        return s.uploads.list(project_id)

    @router.get("/uploads/{upload_id}")
    def upload_status(upload_id: str) -> dict:
        return s.uploads.get(upload_id)

    @router.patch("/uploads/{upload_id}")
    async def upload_chunk(upload_id: str, request: Request) -> dict:
        try:
            offset = int(request.headers.get("upload-offset", ""))
        except ValueError:
            raise BadRequest("invalid_request", "Upload-Offset must be a number") from None
        body = bytearray()
        async for piece in request.stream():
            body += piece
            if len(body) > 2 * CHUNK_SIZE:
                raise TooLarge("payload_too_large", "send chunks of at most "
                               f"{CHUNK_SIZE} bytes")
        # Both append() and finish() do blocking file I/O; run them off the
        # event loop so one slow upload can't stall every other request.
        return await run_in_threadpool(s.uploads.append, upload_id, offset,
                                       bytes(body))

    @router.delete("/uploads/{upload_id}")
    def cancel_upload(upload_id: str) -> dict:
        return s.uploads.cancel(upload_id)

    return router
