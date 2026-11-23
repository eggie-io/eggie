from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Body

if TYPE_CHECKING:
    from ...wiring import Services


def build(s: Services) -> APIRouter:
    router = APIRouter()

    @router.post("/desktop-events", status_code=202)
    def desktop_event(body: Any = Body(None)) -> dict:
        s.desktop_events.relay(body)
        return {}

    return router
