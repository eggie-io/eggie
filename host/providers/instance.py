"""One running Eggie per user.

A second launch (Start menu, the login entry, the post-update relaunch, an
eggie:// link) hands the first one a "show" or its link and exits instead of
opening a second tray icon.
"""
from __future__ import annotations

import threading
import time
from multiprocessing.connection import Client, Listener
from typing import Callable

SHOW = b"show"
HELLO = b"hello"
LINK = b"link "


def claim(address: str, family: str, on_show: Callable[[], None], *,
          announce: bool, before_show: Callable[[], None] | None = None,
          link: str = "", on_link: Callable[[str], None] | None = None) -> bool:
    """True if this process is the first instance; never raises."""
    try:
        conn = Client(address, family=family)
    except Exception:
        return _listen(address, family, on_show, on_link)
    else:
        try:
            if (announce or link) and before_show is not None:
                before_show()
            if link:
                conn.send_bytes(LINK + link.encode("utf-8"))
            else:
                conn.send_bytes(SHOW if announce else HELLO)
        except Exception:
            pass
        finally:
            conn.close()
        return False


def _listen(address, family, on_show, on_link=None) -> bool:
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
                elif msg.startswith(LINK) and on_link is not None:
                    on_link(msg[len(LINK):].decode("utf-8", "replace"))
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
