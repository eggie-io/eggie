from __future__ import annotations

from ..db import Database

ACCOUNT_FIELDS = frozenset({
    "email", "org_id", "access_token", "refresh_token", "access_expires_at",
    "device_code", "user_code", "verification_url", "code_expires_at",
    "poll_interval", "last_error", "sync_ok_at", "sync_error"})


class AccountRepo:
    def __init__(self, db: Database):
        self._db = db

    def get(self) -> dict:
        return self._db.one("SELECT * FROM account WHERE id=1")

    def update(self, **fields) -> None:
        # Field names become SQL text below, so only known names get there.
        unknown = set(fields) - ACCOUNT_FIELDS
        if unknown:
            raise ValueError(f"unknown account fields: {sorted(unknown)}")
        if not fields:
            return
        assignments = ", ".join(f"{name}=?" for name in fields)
        with self._db.tx() as conn:
            conn.execute(f"UPDATE account SET {assignments} WHERE id=1",
                         tuple(fields.values()))
