"""A browser attached to a recipe action's terminal. The bytes carry
passwords and 2FA codes: nothing here logs or keeps them."""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import threading
from collections.abc import Callable, Mapping
from concurrent.futures import Future
from typing import TYPE_CHECKING

from fastapi import WebSocket
from starlette.concurrency import run_in_threadpool
from starlette.websockets import WebSocketDisconnect

from ..errors import EggieError
from .errors import status_of

if TYPE_CHECKING:
    from ..infra.actions_helper import Terminal

log = logging.getLogger("eggie.api")

DEFAULT_COLS, DEFAULT_ROWS = 120, 32
MAX_DIMENSION = 500
MAX_REASON = 123
# The helper hung up: it restarts on every runtime install while its runs carry on,
# so the console reconnects, and a run that has really gone answers that with 4409.
HELPER_GONE = 1012


def _clamp(n: int) -> int:
    return max(1, min(n, MAX_DIMENSION))


def _dimension(value: str | None, default: int) -> int:
    try:
        return _clamp(int(value))
    except (TypeError, ValueError):
        return default


def size(query: Mapping[str, str]) -> tuple[int, int]:
    return (_dimension(query.get("cols"), DEFAULT_COLS),
            _dimension(query.get("rows"), DEFAULT_ROWS))


def _resize(text: str) -> tuple[int, int] | None:
    try:
        message = json.loads(text)
    except (ValueError, RecursionError):
        return None
    dims = message.get("resize") if isinstance(message, dict) else None
    if (not isinstance(dims, list) or len(dims) != 2
            or not all(type(d) is int for d in dims)):
        return None
    return _clamp(dims[0]), _clamp(dims[1])


def close_for(error: EggieError) -> tuple[int, str]:
    return 4000 + status_of(error), error.code.encode()[:MAX_REASON].decode(errors="ignore")


async def serve(ws: WebSocket, attach: Callable[[], Terminal]) -> None:
    """Attach first, so an open socket always has a terminal behind it; a
    refusal still accepts, because a close before accept reaches the page as a bare 403."""
    try:
        terminal = await run_in_threadpool(attach)
    except EggieError as e:
        await ws.accept()
        await ws.close(*close_for(e))
        return
    try:
        await ws.accept()
        await relay(ws, terminal)
    finally:
        terminal.close()


async def _inbound(ws: WebSocket, terminal: Terminal) -> int | None:
    """None once the browser has gone, else the code to close it with."""
    while True:
        message = await ws.receive()
        if message["type"] == "websocket.disconnect":
            return None
        try:
            if message.get("bytes"):
                await run_in_threadpool(terminal.write, message["bytes"])
            elif message.get("text") is not None and (dims := _resize(message["text"])):
                await run_in_threadpool(terminal.resize, *dims)
        except OSError:
            return HELPER_GONE
        except Exception as e:
            # The type only: the message could quote what was typed.
            log.error("the terminal refused a write: %s", type(e).__name__)
            return 1011


async def relay(ws: WebSocket, terminal: Terminal) -> None:
    loop = asyncio.get_running_loop()
    hung_up = asyncio.Event()
    lock = threading.Lock()
    stopped = threading.Event()
    in_flight: list[Future] = []

    def pump() -> None:
        try:
            while data := terminal.read():
                with lock:
                    if stopped.is_set():
                        return
                    send = asyncio.run_coroutine_threadsafe(ws.send_bytes(data), loop)
                    in_flight[:] = [send]
                send.result()
        except Exception:
            # The browser has gone, or the teardown cancelled the send.
            pass
        finally:
            with contextlib.suppress(RuntimeError):  # the loop is already closed
                loop.call_soon_threadsafe(hung_up.set)

    thread = threading.Thread(target=pump, name="eggie-terminal", daemon=True)
    thread.start()
    inbound = asyncio.create_task(_inbound(ws, terminal))
    ended = asyncio.create_task(hung_up.wait())
    try:
        done, _ = await asyncio.wait({inbound, ended}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        # No awaits here: a cancelled task (server shutdown) may get none, and the
        # pump must end anyway. Closing the terminal ends its read; cancelling the
        # send frees it from a loop that may never run it.
        inbound.cancel()
        ended.cancel()
        with lock:
            stopped.set()
            for send in in_flight:
                send.cancel()
        terminal.close()
    code = inbound.result() if inbound in done else HELPER_GONE
    if code is not None:
        # The browser may have left in the meantime.
        with contextlib.suppress(RuntimeError, OSError, WebSocketDisconnect):
            await ws.close(code)
