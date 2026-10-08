from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Request
from fastapi.responses import Response

from ...errors import Unauthorized
from ...services.sessions import COOKIE, HANDOFF_TTL, SESSION_TTL
from ..schemas import Handoff

if TYPE_CHECKING:
    from ...wiring import Services


def build(s: Services) -> APIRouter:
    router = APIRouter()

    @router.post("/sessions/handoff")
    def issue_handoff() -> dict:
        return {"code": s.sessions.issue_handoff(), "expires_in": HANDOFF_TTL}

    # A signed-in page signing the system browser in (the desktop window's
    # "open in browser"). The middleware has already checked cookie and Origin.
    @router.post("/api/sessions/handoff")
    def issue_browser_handoff() -> dict:
        return issue_handoff()

    @router.post("/api/session")
    def start_session(body: Handoff, response: Response) -> dict:
        session_id = s.sessions.redeem(body.code)
        if session_id is None:
            raise Unauthorized("handoff_invalid", "that sign-in link has already "
                           "been used or has run out; open Eggie from the "
                           "desktop app again")
        response.set_cookie(COOKIE, session_id, max_age=SESSION_TTL,
                            httponly=True, samesite="strict", path="/api")
        return {"signed_in": True}

    @router.get("/api/session")
    def read_session() -> dict:
        # Reaching here means the middleware's own session check already
        # returned "ok" -- GET is gated like any other /api/* route.
        return {"signed_in": True}

    @router.delete("/api/session")
    def end_session(request: Request, response: Response) -> dict:
        s.sessions.end(request.cookies.get(COOKIE))
        response.delete_cookie(COOKIE, path="/api")
        return {"signed_in": False}

    return router
