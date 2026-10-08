from __future__ import annotations

import secrets
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .. import constants
from ..config import ApiConfig
from ..services.sessions import COOKIE, SESSION_TTL, Sessions
from .errors import error_body


def read_token(path: Path) -> str:
    """Empty string for "missing", "unreadable", and "unparseable" alike --
    callers only need to know whether they have a credential to compare
    against, and this must fail closed on anything unexpected rather than
    crash-loop the API."""
    try:
        return path.read_text().strip()
    except (OSError, UnicodeDecodeError):
        return ""


def install(app: FastAPI, *, token: str, sessions: Sessions,
            config: ApiConfig) -> None:
    allowed_hosts = {f"localhost:{config.edge_port}",
                     f"127.0.0.1:{config.edge_port}"}
    allowed_origins = {f"http://{host}" for host in allowed_hosts}
    # Reachable before sign-in: checking the API version, and trading a
    # handoff code for a cookie. GET/DELETE /api/session must still go
    # through the session check below -- only the exchange itself is open.
    open_browser_routes = {("GET", "/api/health"), ("HEAD", "/api/health"),
                           ("POST", "/api/session")}

    def _bearer_ok(request: Request) -> bool:
        scheme, _, supplied = request.headers.get("authorization", "").partition(" ")
        # Starlette decodes headers as latin-1, so a header value can carry
        # bytes that are not valid ASCII; compare_digest raises TypeError on
        # two `str` args if either has a non-ASCII character. Comparing the
        # encoded bytes instead means every wire-valid header reaches a
        # normal true/false answer, never an exception out of the one guard
        # that must never throw.
        return (scheme.lower() == "bearer"
                and secrets.compare_digest(supplied.encode(), token.encode()))

    def _browser_refusal(request: Request) -> JSONResponse | None:
        if request.headers.get("host", "") not in allowed_hosts:
            return error_body("forbidden_host",
                              "this address is not where Eggie's page lives", 403)
        if (request.method not in ("GET", "HEAD")
                and request.headers.get("origin", "") not in allowed_origins):
            return error_body("forbidden_origin",
                              "requests that change something must come from "
                              "Eggie's own page", 403)
        return None

    @app.middleware("http")
    async def _authenticate(request: Request, call_next):
        path = request.url.path
        if path == "/api" or path.startswith("/api/"):
            refusal = _browser_refusal(request)
            if refusal is not None:
                return refusal
            if (request.method, path) in open_browser_routes:
                return await call_next(request)
            session_id = request.cookies.get(COOKIE)
            verdict = sessions.check(session_id)
            if verdict == "ok":
                response = await call_next(request)
                if verdict.extended:
                    response.set_cookie(COOKIE, session_id, max_age=SESSION_TTL,
                                        httponly=True, samesite="strict",
                                        path="/api")
                return response
            if verdict == "expired":
                return error_body("session_expired", "your sign-in ran out; open "
                                  "Eggie from the desktop app again", 401)
            return error_body("not_signed_in", "open Eggie from the desktop app "
                              "to sign in", 401)
        if path == "/health":
            return await call_next(request)
        if not token:
            return error_body(constants.API_UNCONFIGURED,
                              "the API has no token configured; run setup again", 503)
        if not _bearer_ok(request):
            return error_body("unauthorized", "missing or invalid bearer token", 401)
        return await call_next(request)
