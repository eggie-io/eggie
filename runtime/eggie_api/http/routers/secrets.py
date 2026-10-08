from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter
from fastapi.responses import Response

from ..schemas import SecretHint, SecretValue

if TYPE_CHECKING:
    from ...wiring import Services


def build(s: Services) -> APIRouter:
    router = APIRouter()

    @router.get("/projects/{project_id}/secrets")
    def list_secrets(project_id: str) -> dict:
        return s.secrets.list(project_id)

    @router.put("/projects/{project_id}/secrets/{name}", status_code=204)
    def put_secret(project_id: str, name: str, body: SecretValue) -> Response:
        s.secrets.put(project_id, name, body.value)
        return Response(status_code=204)

    @router.put("/projects/{project_id}/secret-requests/{name}", status_code=204)
    def request_secret(project_id: str, name: str, body: SecretHint) -> Response:
        s.secrets.request(project_id, name, body.hint)
        return Response(status_code=204)

    @router.delete("/projects/{project_id}/secrets/{name}", status_code=204)
    def delete_secret(project_id: str, name: str) -> Response:
        s.secrets.delete(project_id, name)
        return Response(status_code=204)

    return router
