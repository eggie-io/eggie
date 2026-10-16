from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter

if TYPE_CHECKING:
    from ...wiring import Services


def build_console(s: Services) -> APIRouter:
    # Mounted only at /api: the switcher is a browser feature, and the guest
    # token has no use for the account's other servers.
    router = APIRouter()

    @router.get("/instances")
    def list_instances() -> dict:
        return s.instances.list()

    return router
