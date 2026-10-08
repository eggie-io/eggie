from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

from .migrate import migrate


class Database:
    """One sqlite connection shared by every thread.

    FastAPI serves sync routes from a threadpool and jobs run on threads of
    their own, so a thread-bound connection fails as soon as a second thread
    touches it. One connection with `check_same_thread=False` behind a lock
    holds for both, and — unlike a connection per thread — nothing accumulates
    an open handle for every worker the threadpool ever created.
    """

    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            migrate(self._conn)

    def one(self, sql: str, params=()) -> dict | None:
        with self._lock:
            row = self._conn.execute(sql, params).fetchone()
        return dict(row) if row else None

    def all(self, sql: str, params=()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, params)]

    @contextmanager
    def tx(self):
        with self._lock:
            yield self._conn
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()
