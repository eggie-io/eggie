from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter

from ..schemas import CreateProject

if TYPE_CHECKING:
    from ...wiring import Services


def build(s: Services) -> APIRouter:
    router = APIRouter()

    @router.post("/projects", status_code=201)
    def create_project(body: CreateProject) -> dict:
        return s.projects.create(
            body.id, [w.model_dump() for w in body.web] if body.web else None,
            body.domain)

    @router.post("/projects/{project_id}/adopt", status_code=201)
    def adopt_project(project_id: str) -> dict:
        return s.projects.adopt(project_id)

    @router.get("/projects")
    def list_projects() -> dict:
        return s.projects.list()

    @router.get("/projects/{project_id}")
    def get_project(project_id: str) -> dict:
        return s.projects.get(project_id)

    @router.get("/projects/{project_id}/delete-preview")
    def delete_preview(project_id: str) -> dict:
        return s.projects.delete_preview(project_id)

    @router.delete("/projects/{project_id}")
    def delete_project(project_id: str, purge: bool = False) -> dict:
        return s.projects.delete(project_id, purge=purge)

    return router
