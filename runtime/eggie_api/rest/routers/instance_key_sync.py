from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter

from ...services import ratelimit

if TYPE_CHECKING:
    from ...wiring import Services


def build(s: Services) -> APIRouter:
    # Mounted only on a site. rest/auth.py lets it through with no session:
    # it carries nothing and only makes this server ask the service again.
    router = APIRouter()
    window = ratelimit.Window(6, 60, what="key sync pings")

    @router.post("/api/ssh-keys/sync", status_code=202)
    def ping() -> dict:
        # Always owed, so strangers cannot starve a real ping; the limit
        # only stops pings from waking the loop ahead of its own interval.
        s.instance_key_sync.wake()
        if window.take():
            s.sync.wake()
        return {}

    return router
