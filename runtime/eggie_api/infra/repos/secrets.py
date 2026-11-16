from __future__ import annotations

import time

from ..db import Database


class SecretRepo:
    def __init__(self, db: Database):
        self._db = db

    def names(self, project_id) -> list[dict]:
        return self._db.all(
            "SELECT name, kind, filename, updated_at, unused_at FROM secrets WHERE project_id=? "
            "ORDER BY name", (project_id,))

    def mark_unused(self, project_id, names, at) -> None:
        with self._db.tx() as conn:
            conn.executemany(
                "UPDATE secrets SET unused_at=? WHERE project_id=? AND name=?",
                [(at, project_id, n) for n in names])

    def clear_unused(self, project_id, names) -> None:
        with self._db.tx() as conn:
            conn.executemany(
                "UPDATE secrets SET unused_at=NULL WHERE project_id=? AND name=?",
                [(project_id, n) for n in names])

    def entries(self, project_id) -> dict[str, tuple[str, str]]:
        return {r["name"]: (r["value"], r["kind"]) for r in self._db.all(
            "SELECT name, value, kind FROM secrets WHERE project_id=?",
            (project_id,))}

    def set(self, project_id,
            entries: dict[str, tuple[str, str, str | None]],
            at: float | None = None) -> None:
        with self._db.tx() as conn:
            at = time.time() if at is None else at
            conn.executemany(
                "INSERT OR REPLACE INTO secrets"
                "(project_id, name, value, kind, filename, updated_at) "
                "VALUES (?,?,?,?,?,?)",
                [(project_id, name, value, kind, filename, at)
                 for name, (value, kind, filename) in entries.items()])
            # A request for a file stays open until a file is saved.
            conn.executemany(
                "DELETE FROM secret_requests WHERE project_id=? AND name=? AND kind=?",
                [(project_id, name, kind)
                 for name, (_, kind, _) in entries.items()])
            conn.execute("UPDATE projects SET secrets_changed_at=? WHERE id=?",
                         (at, project_id))

    def delete(self, project_id, name, at: float | None = None) -> bool:
        with self._db.tx() as conn:
            at = time.time() if at is None else at
            gone = conn.execute(
                "DELETE FROM secrets WHERE project_id=? AND name=?",
                (project_id, name)).rowcount > 0
            if gone:
                conn.execute(
                    "UPDATE projects SET secrets_changed_at=? WHERE id=?",
                    (at, project_id))
        return gone

    def requests(self, project_id) -> list[dict]:
        return self._db.all(
            "SELECT name, hint, kind FROM secret_requests r WHERE project_id=? "
            "AND NOT EXISTS (SELECT 1 FROM secrets s WHERE s.project_id=r.project_id "
            "AND s.name=r.name AND s.kind=r.kind) ORDER BY name", (project_id,))

    def request(self, project_id, name, hint, kind) -> None:
        with self._db.tx() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO secret_requests"
                "(project_id, name, hint, kind, created_at) VALUES (?,?,?,?,?)",
                (project_id, name, hint, kind, time.time()))

    def delete_request(self, project_id, name) -> bool:
        with self._db.tx() as conn:
            gone = conn.execute(
                "DELETE FROM secret_requests WHERE project_id=? AND name=?",
                (project_id, name)).rowcount > 0
        return gone

    def drop(self, project_id) -> None:
        with self._db.tx() as conn:
            conn.execute("DELETE FROM secrets WHERE project_id=?", (project_id,))
            conn.execute("DELETE FROM secret_requests WHERE project_id=?",
                         (project_id,))
