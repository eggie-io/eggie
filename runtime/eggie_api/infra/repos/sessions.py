from __future__ import annotations

from ..db import Database


class SessionRepo:
    def __init__(self, db: Database):
        self._db = db

    def add(self, id_hash, expires_at) -> None:
        with self._db.tx() as conn:
            conn.execute(
                "INSERT INTO sessions(id_hash, expires_at) VALUES (?,?)",
                (id_hash, expires_at))

    def get(self, id_hash) -> dict | None:
        return self._db.one("SELECT * FROM sessions WHERE id_hash=?", (id_hash,))

    def set_expiry(self, id_hash, expires_at) -> None:
        with self._db.tx() as conn:
            conn.execute(
                "UPDATE sessions SET expires_at=? WHERE id_hash=?",
                (expires_at, id_hash))

    def remove(self, id_hash) -> None:
        with self._db.tx() as conn:
            conn.execute("DELETE FROM sessions WHERE id_hash=?", (id_hash,))
