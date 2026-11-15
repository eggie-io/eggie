from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Request, WebSocket
from starlette.concurrency import run_in_threadpool

from .. import auth, terminal

if TYPE_CHECKING:
    from ...wiring import Services


async def json_body(request: Request) -> object:
    """The body parsed by hand: FastAPI's own 422 would echo an answer back.
    Anything that isn't JSON reads as None for the service to refuse."""
    try:
        return await request.json()
    except ValueError:
        return None


def build(s: Services) -> APIRouter:
    router = APIRouter()

    @router.get("/connections/{connection_id}")
    def get_connection(connection_id: str) -> dict:
        return s.connections.get(connection_id)

    @router.post("/connections/{connection_id}/login")
    def login(connection_id: str) -> dict:
        return s.connections.login(connection_id)

    @router.post("/connections/{connection_id}/cancel")
    def cancel(connection_id: str) -> dict:
        return s.connections.cancel(connection_id)

    @router.post("/connections/{connection_id}/answer")
    async def answer(connection_id: str, request: Request) -> dict:
        body = await json_body(request)
        return await run_in_threadpool(s.connections.answer, connection_id, body)

    return router


def build_console(s: Services) -> APIRouter:
    router = APIRouter()

    @router.websocket("/connections/{connection_id}/terminal")
    async def connection_terminal(ws: WebSocket, connection_id: str) -> None:
        if not auth.websocket_allowed(ws, sessions=s.sessions, config=s.config):
            await ws.close()
            return
        cols, rows = terminal.size(ws.query_params)
        await terminal.serve(ws, lambda: s.connections.attach(connection_id, cols, rows))

    return router
