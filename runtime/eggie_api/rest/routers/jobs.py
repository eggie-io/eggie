from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse, StreamingResponse

from ...errors import NotFound
from .. import TEXT

if TYPE_CHECKING:
    from ...wiring import Services


def build(s: Services) -> APIRouter:
    router = APIRouter()

    def require_job(job_id: str):
        job = s.jobs.get(job_id)
        if job is None:
            raise NotFound("job_not_found", f"no job with id '{job_id}'")
        return job

    @router.get("/jobs/{job_id}")
    def job_status(job_id: str) -> dict:
        return require_job(job_id).as_dict()

    @router.get("/jobs/{job_id}/logs")
    def job_logs(job_id: str, follow: bool = False):
        job = require_job(job_id)
        if not follow:
            return PlainTextResponse(job.text(), media_type=TEXT)
        return StreamingResponse(s.jobs.follow(job_id), media_type=TEXT)

    return router
