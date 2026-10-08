from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse, StreamingResponse

from .. import TEXT

if TYPE_CHECKING:
    from ...wiring import Services


def build(s: Services) -> APIRouter:
    router = APIRouter()

    @router.post("/projects/{project_id}/up", status_code=202)
    def project_up(project_id: str) -> dict:
        return {"job_id": s.lifecycle.start(project_id)}

    @router.post("/projects/{project_id}/restart", status_code=202)
    def project_restart(project_id: str) -> dict:
        return {"job_id": s.lifecycle.start(project_id, stop_first=True)}

    @router.post("/projects/{project_id}/down", status_code=202)
    def project_down(project_id: str) -> dict:
        return {"job_id": s.lifecycle.stop(project_id)}

    @router.get("/projects/{project_id}/logs")
    def project_logs(project_id: str, follow: bool = False,
                     service: str | None = None):
        if not follow:
            return PlainTextResponse(s.lifecycle.logs(project_id, service),
                                     media_type=TEXT)
        return StreamingResponse(s.lifecycle.logs_stream(project_id, service),
                                 media_type=TEXT)

    return router
