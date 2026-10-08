from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter

from ..schemas import CloneRepo

if TYPE_CHECKING:
    from ...wiring import Services


def build(s: Services) -> APIRouter:
    router = APIRouter()

    @router.get("/github")
    def github_status() -> dict:
        s.github_link.check_token()
        return s.github_link.status()

    @router.post("/github/connect")
    def github_connect() -> dict:
        return s.github_link.connect()

    @router.post("/github/disconnect")
    def github_disconnect() -> dict:
        return s.github_link.disconnect()

    @router.post("/github/reapply")
    def github_reapply() -> dict:
        return s.github_link.reapply()

    @router.get("/github/repos")
    def github_repos(page: int = 1) -> dict:
        return s.github_link.repos(page)

    @router.post("/github/clone", status_code=202)
    def github_clone(body: CloneRepo) -> dict:
        return s.clone.clone(body.repo, body.id)

    return router
