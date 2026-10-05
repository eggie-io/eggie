"""One running Eggie per user.

A second launch (Start menu, the login entry, the post-update relaunch) hands
the first one a "show" and exits instead of opening a second tray icon.
"""
from __future__ import annotations

import threading
import time
from multiprocessing.connection import Client, Listener
from typing import Callable

SHOW = b"show"
HELLO = b"hello"


def claim(address: str, family: str, on_show: Callable[[], None], *,
          announce: bool, before_show: Callable[[], None] | None = None) -> bool:
    """True if this process is the first instance; never raises."""
    try:
        conn = Client(address, family=family)
    except Exception:
        return _listen(address, family, on_show)
    else:
        try:
            if announce and before_show is not None:
                before_show()
            conn.send_bytes(SHOW if announce else HELLO)
        except Exception:
            pass
        finally:
            conn.close()
        return False


def _listen(address, family, on_show) -> bool:
    try:
        listener = Listener(address, family=family)
    except Exception:
        # Lost a race to another instance starting now, or a stale socket
        # file; a failed connect then means run normally without listening.
        try:
            conn = Client(address, family=family)
            conn.close()
            return False
        except Exception:
            return True

    def handle_conn(conn):
        try:
            # A client that connects and sends nothing must not hold this thread.
            if conn.poll(1.0):
                msg = conn.recv_bytes()
                if msg == SHOW:
                    on_show()
        except Exception:
            pass
        finally:
            conn.close()

    def serve():
        while True:
            try:
                conn = listener.accept()
                threading.Thread(target=handle_conn, args=(conn,), daemon=True).start()
            except Exception:
                # A broken listener fails every accept at once; back off
                # instead of spinning a core.
                time.sleep(0.5)

    threading.Thread(target=serve, daemon=True, name="eggie-instance").start()
    return True
