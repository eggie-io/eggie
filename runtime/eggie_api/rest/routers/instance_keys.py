from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Response
from pydantic import BaseModel

if TYPE_CHECKING:
    from ...wiring import Services


class KeyRequest(BaseModel):
    public_key: str
    computer_name: str


def build(s: Services) -> APIRouter:
    # Bearer only, never under /api and never on a site: the browser has no use
    # for these, and a site holds no desktop device.
    router = APIRouter()

    @router.get("/instances")
    def list_instances() -> dict:
        return s.instance_keys.list()

    @router.post("/instances/{instance_id}/ssh-key-requests", status_code=201)
    def request_key(instance_id: str, body: KeyRequest) -> dict:
        return s.instance_keys.request(instance_id, body.public_key, body.computer_name)

    @router.get("/instances/{instance_id}/ssh-key-requests/{request_id}")
    def key_request(instance_id: str, request_id: str) -> dict:
        return s.instance_keys.status(instance_id, request_id)

    @router.delete("/instances/{instance_id}/ssh-keys", status_code=204)
    def remove_key(instance_id: str, fingerprint: str) -> Response:
        s.instance_keys.remove(instance_id, fingerprint)
        return Response(status_code=204)

    return router
