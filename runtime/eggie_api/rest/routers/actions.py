from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, WebSocket
from fastapi.responses import PlainTextResponse

from ...services.actions import DEFAULT_TAIL
from .. import auth, terminal

if TYPE_CHECKING:
    from ...wiring import Services


def build(s: Services) -> APIRouter:
    router = APIRouter()

    @router.get("/projects/{project_id}/actions")
    def list_actions(project_id: str) -> dict:
        return s.actions.list(project_id)

    @router.post("/projects/{project_id}/actions/{action_id}/start")
    def start_action(project_id: str, action_id: str) -> dict:
        return s.actions.start(project_id, action_id)

    @router.post("/projects/{project_id}/actions/{action_id}/stop")
    def stop_action(project_id: str, action_id: str) -> dict:
        return s.actions.stop(project_id, action_id)

    return router


def build_console(s: Services) -> APIRouter:
    # Mounted only at /api: an action's output and terminal are for the owner's console.
    router = APIRouter()

    @router.get("/projects/{project_id}/actions/{action_id}/log",
                response_class=PlainTextResponse)
    def action_log(project_id: str, action_id: str, tail: int = DEFAULT_TAIL) -> str:
        return s.actions.log(project_id, action_id, tail)

    @router.websocket("/projects/{project_id}/actions/{action_id}/terminal")
    async def action_terminal(ws: WebSocket, project_id: str, action_id: str) -> None:
        if not auth.websocket_allowed(ws, sessions=s.sessions, config=s.config):
            await ws.close()
            return
        cols, rows = terminal.size(ws.query_params)
        await terminal.serve(ws, lambda: s.actions.attach(project_id, action_id, cols, rows))

    return router
