"""One running Omelet per user.

A second launch (Start menu, the login entry, the post-update relaunch) hands
the first one a "show" and exits instead of opening a second tray icon.
"""
from __future__ import annotations

import threading
from multiprocessing.connection import Client, Listener
from typing import Callable

AUTHKEY = b"omelet-single-instance"
SHOW = "show"
HELLO = "hello"


def claim(address: str, family: str, on_show: Callable[[], None], *,
          announce: bool, authkey: bytes = AUTHKEY) -> bool:
    try:
        conn = Client(address, family=family, authkey=authkey)
    except Exception:
        # Nothing listening (FileNotFoundError / ConnectionRefusedError), or a
        # stranger on the address failing the handshake: either way, run.
        return _listen(address, family, on_show, authkey)
    with conn:
        conn.send(SHOW if announce else HELLO)
    return False


def _listen(address, family, on_show, authkey) -> bool:
    listener = Listener(address, family=family, authkey=authkey)

    def serve():
        while True:
            try:
                with listener.accept() as conn:
                    if conn.recv() == SHOW:
                        on_show()
            except Exception:
                continue

    threading.Thread(target=serve, daemon=True, name="omelet-instance").start()
    return True
