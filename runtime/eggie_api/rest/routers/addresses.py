from __future__ import annotations

import re
from typing import TYPE_CHECKING

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

from ...errors import NotFound
from ...services.ratelimit import Window
from ..schemas import AddAddress

if TYPE_CHECKING:
    from ...wiring import Services

CHALLENGE_TOKEN = re.compile(r"[A-Za-z0-9_-]{20,200}")


def build(s: Services) -> APIRouter:
    router = APIRouter()

    @router.get("/projects/{project_id}/addresses")
    def addresses(project_id: str) -> dict:
        return s.domains.addresses(project_id)

    return router


def build_console(s: Services) -> APIRouter:
    # Mounted only at /api: attaching a domain is the owner's decision, never
    # the guest token's (CLI, coding agents).
    router = APIRouter()

    @router.post("/projects/{project_id}/addresses", status_code=201)
    def add_address(project_id: str, body: AddAddress) -> dict:
        return s.domains.add(project_id, body.hostname)

    @router.delete("/projects/{project_id}/addresses/{hostname}")
    def remove_address(project_id: str, hostname: str) -> dict:
        return s.domains.remove(project_id, hostname)

    @router.post("/projects/{project_id}/addresses/{hostname}/primary")
    def make_primary(project_id: str, hostname: str) -> dict:
        return s.domains.make_primary(project_id, hostname)

    @router.post("/projects/{project_id}/addresses/{hostname}/check")
    def check_address(project_id: str, hostname: str) -> dict:
        return s.domains.check(project_id, hostname)

    return router


def build_challenge(s: Services) -> APIRouter:
    # Open to anyone on the internet (rest/auth.py): only a well-formed token,
    # within the window, costs a call to the service.
    router = APIRouter()
    window = Window(60, 60.0, what="certificate checks")

    @router.get("/.well-known/acme-challenge/{token}", response_class=PlainTextResponse)
    def challenge(token: str) -> PlainTextResponse:
        if not CHALLENGE_TOKEN.fullmatch(token) or not window.take():
            raise NotFound("challenge_not_found", "no such challenge")
        return PlainTextResponse(s.domains.challenge(token))

    return router
