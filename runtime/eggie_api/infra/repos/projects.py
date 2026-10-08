from __future__ import annotations

from ..db import Database


class ProjectRepo:
    def __init__(self, db: Database):
        self._db = db

    def add(self, id, guest_path, domain, status="stopped") -> None:
        with self._db.tx() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO projects(id, guest_path, domain, status) "
                "VALUES (?,?,?,?)", (id, guest_path, domain, status))

    def get(self, id) -> dict | None:
        return self._db.one("SELECT * FROM projects WHERE id=?", (id,))

    def list(self) -> list[dict]:
        return self._db.all("SELECT * FROM projects ORDER BY id")

    def set_status(self, id, status) -> None:
        with self._db.tx() as conn:
            conn.execute("UPDATE projects SET status=? WHERE id=?", (status, id))

    def set_problem(self, id, code=None, message=None) -> None:
        """`code=None` clears it: a problem that outlives the fix is worse than
        none, so every `up` writes this whether or not it found something."""
        with self._db.tx() as conn:
            conn.execute(
                "UPDATE projects SET problem_code=?, problem_message=? WHERE id=?",
                (code, message, id))

    def set_compose_name(self, id, compose_name) -> None:
        with self._db.tx() as conn:
            conn.execute("UPDATE projects SET compose_name=? WHERE id=?",
                         (compose_name, id))

    def mark_started(self, id, at) -> None:
        with self._db.tx() as conn:
            conn.execute("UPDATE projects SET last_started_at=? WHERE id=?", (at, id))

    def remove(self, id) -> None:
        with self._db.tx() as conn:
            conn.execute("DELETE FROM projects WHERE id=?", (id,))
