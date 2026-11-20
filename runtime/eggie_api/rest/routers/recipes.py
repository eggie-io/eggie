from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter

if TYPE_CHECKING:
    from ...wiring import Services


def build(s: Services) -> APIRouter:
    router = APIRouter()

    @router.get("/recipes")
    def list_recipes() -> dict:
        return s.recipes.list()

    @router.post("/recipes/{slug}/get")
    def get_recipe(slug: str) -> dict:
        return s.recipes.get(slug)

    return router
