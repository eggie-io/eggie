from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter

if TYPE_CHECKING:
    from ...wiring import Services


def build(s: Services) -> APIRouter:
    router = APIRouter()

    @router.get("/health")
    def health() -> dict:
        return s.system.health()

    @router.get("/version")
    def version() -> dict:
        return s.system.version()

    @router.get("/disk")
    def disk_usage() -> dict:
        return s.system.disk()

    @router.get("/connect")
    def connect_facts() -> dict:
        return s.system.connect()

    @router.get("/agents/status")
    def agents_status() -> dict:
        return s.agents.status()

    @router.post("/agents/{agent_id}/setup")
    def agent_setup(agent_id: str) -> dict:
        return s.agents.ensure_setup(agent_id)

    return router
