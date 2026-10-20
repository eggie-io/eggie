from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Request

from ...errors import LengthRequired
from ...services.transfers import bad_transfer
from .files import stream_to_tempfile

if TYPE_CHECKING:
    from ...wiring import Services

TICKET = "Eggie-Transfer-Ticket"
SIZE = "Eggie-Transfer-Size"
DATA = "Eggie-Transfer-Data"
REPLACE = "Eggie-Transfer-Replace"
TOKEN = "Eggie-Transfer-Token"
MAX_DIGITS = 19


def _number(value: str | None) -> int | None:
    if value is None or not (0 < len(value) <= MAX_DIGITS and value.isascii()
                             and value.isdigit()):
        return None
    return int(value)


def _header(request: Request, name: str) -> str:
    value = request.headers.get(name)
    if value is None:
        raise bad_transfer(name)
    return value


def _size(request: Request) -> int:
    if SIZE not in request.headers:
        raise LengthRequired("length_required", f"the push request needs an {SIZE} header")
    size = _number(request.headers[SIZE])
    if size is None:
        raise bad_transfer(SIZE)
    return size


def _data(request: Request) -> bool:
    value = _header(request, DATA)
    if value not in ("yes", "no"):
        raise bad_transfer(DATA)
    return value == "yes"


def build(s: Services) -> APIRouter:
    # Mounted only on a site. rest/auth.py lets these through with no session:
    # the ticket and the push token are the only credentials they take.
    router = APIRouter()
    max_bytes = s.config.max_upload_bytes

    @router.post("/api/transfers", status_code=201)
    def open_transfer(request: Request) -> dict:
        return s.transfers.open(size=_size(request), ticket=_header(request, TICKET),
                                data=_data(request), replace=_header(request, REPLACE),
                                forwarded_for=request.headers.get("x-forwarded-for", ""),
                                peer=getattr(request.client, "host", ""))

    @router.put("/api/transfers/{push_id}/bundle", status_code=202)
    async def receive_bundle(push_id: str, request: Request) -> dict:
        return await s.transfers.receive(
            push_id, request.headers.get(TOKEN, ""),
            lambda dir_: stream_to_tempfile(request, dir_, max_bytes=max_bytes),
            _number(request.headers.get("content-length")))

    @router.get("/api/transfers/{push_id}")
    def transfer_status(push_id: str, request: Request) -> dict:
        return s.transfers.status(push_id, request.headers.get(TOKEN, ""))

    @router.get("/api/ready")
    def ready() -> dict:
        s.transfers.require_ready()
        return {"ready": True}

    return router
