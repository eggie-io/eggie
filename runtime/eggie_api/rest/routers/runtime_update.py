from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter

if TYPE_CHECKING:
    from ...wiring import Services


def build_console(s: Services) -> APIRouter:
    # Mounted only at /api: the update notice is a console feature.
    router = APIRouter()

    @router.get("/runtime/update")
    def get_update() -> dict:
        return s.runtime_update.status()

    @router.post("/runtime/update")
    def request_update() -> dict:
        return s.runtime_update.request()

    return router
