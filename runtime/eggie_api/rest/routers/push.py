from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter

from ..schemas import PushRequest

if TYPE_CHECKING:
    from ...wiring import Services


def build_console(s: Services) -> APIRouter:
    # Mounted only at /api: pushing is the console's action; the CLI never pushes.
    router = APIRouter()

    @router.get("/projects/{project_id}/push")
    def push_targets(project_id: str) -> dict:
        return s.push.targets(project_id)

    @router.post("/projects/{project_id}/push", status_code=202)
    def push_start(project_id: str, body: PushRequest) -> dict:
        return {"job_id": s.push.start(project_id, body.instance_id, replace=body.replace)}

    return router
