from __future__ import annotations

from ..db import Database

GITHUB_FIELDS = frozenset({
    "generation", "desired_at", "login", "gh_id", "name", "email",
    "needs_reconnect", "last_error", "checked_at"})


class GitHubRepo:
    def __init__(self, db: Database):
        self._db = db

    def get(self) -> dict:
        return self._db.one("SELECT * FROM github WHERE id=1")

    def update(self, **fields) -> None:
        unknown = set(fields) - GITHUB_FIELDS
        if unknown:
            raise ValueError(f"unknown github fields: {sorted(unknown)}")
        if not fields:
            return
        assignments = ", ".join(f"{name}=?" for name in fields)
        with self._db.tx() as conn:
            conn.execute(f"UPDATE github SET {assignments} WHERE id=1",
                         tuple(fields.values()))
