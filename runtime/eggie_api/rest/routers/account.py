from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter

if TYPE_CHECKING:
    from ...wiring import Services


def build(s: Services) -> APIRouter:
    router = APIRouter()

    @router.get("/account")
    def account_status() -> dict:
        return s.account.status()

    @router.post("/account/sign-in")
    def account_sign_in() -> dict:
        return s.account.start_sign_in()

    @router.post("/account/sign-out")
    def account_sign_out() -> dict:
        return s.account.sign_out()

    return router
