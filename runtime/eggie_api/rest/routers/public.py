from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter

if TYPE_CHECKING:
    from ...wiring import Services


def build(s: Services) -> APIRouter:
    router = APIRouter()

    @router.get("/projects/{project_id}/public")
    def public_status(project_id: str) -> dict:
        s.loader.require(project_id)
        return s.public.status(project_id)

    return router


def build_console(s: Services) -> APIRouter:
    # Mounted only at /api: turning a public URL on or off is the console's
    # decision, never the guest token's (CLI, coding agents).
    router = APIRouter()

    @router.post("/projects/{project_id}/public", status_code=202)
    def public_on(project_id: str) -> dict:
        s.loader.require(project_id)
        return s.public.enable(project_id)

    @router.delete("/projects/{project_id}/public")
    def public_off(project_id: str) -> dict:
        s.loader.require(project_id)
        return s.public.disable(project_id)

    return router
