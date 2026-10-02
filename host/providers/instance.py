"""One running Omelet per user.

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
          announce: bool) -> bool:
    """Connect to an existing instance or become the first one.

    Returns True if this process becomes the first instance (and now listens).
    Returns False if another instance answered. Raises are caught and handled:
    if the listener cannot be created, retries connecting as a client.
    """
    try:
        conn = Client(address, family=family)
    except Exception:
        # Nothing listening. Try to become the listener.
        return _listen(address, family, on_show)
    else:
        # Successfully connected to an existing instance.
        try:
            conn.send_bytes(SHOW if announce else HELLO)
        except Exception:
            # Connection died; treat as best-effort and exit.
            pass
        finally:
            conn.close()
        return False


def _listen(address, family, on_show) -> bool:
    """Start listening for incoming show requests.

    If Listener creation fails (address in use or stale file), retry connecting
    as a client once. If that succeeds, return False (another instance won).
    Otherwise return True (run as first instance without listening).
    """
    try:
        listener = Listener(address, family=family)
    except Exception:
        # Listener creation failed (address in use, stale socket, etc.).
        # Retry connecting once in case another instance just started.
        try:
            conn = Client(address, family=family)
            conn.close()
            return False
        except Exception:
            # Still nothing listening; we're the first.
            return True

    def handle_conn(conn):
        """Handle one incoming connection on a separate thread."""
        try:
            # Use poll() to avoid blocking if the client connects but sends nothing.
            # A well-behaved client will have data ready immediately.
            if conn.poll(1.0):
                msg = conn.recv_bytes()
                if msg == SHOW:
                    on_show()
        except Exception:
            # Ignore per-connection errors (recv timeout, etc.).
            pass
        finally:
            conn.close()

    def serve():
        """Accept connections in a loop, spawning each on its own thread."""
        while True:
            try:
                conn = listener.accept()
                threading.Thread(target=handle_conn, args=(conn,), daemon=True).start()
            except OSError:
                # Listener-level error (closed, system limit, etc.). Back off
                # to avoid busy-spinning at 100% CPU.
                time.sleep(0.5)
            except Exception:
                # Other unexpected errors. Back off to avoid spinning.
                time.sleep(0.5)

    threading.Thread(target=serve, daemon=True, name="omelet-instance").start()
    return True
