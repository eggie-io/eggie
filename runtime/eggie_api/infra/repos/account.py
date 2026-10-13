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

    def seed(self, *, device_id: str, access_token: str, refresh_token: str,
             access_expires_at: float) -> None:
        # The one write allowed to change device_id: the service minted this
        # device before first boot, and nothing has been recorded under the
        # placeholder id yet. A seed supersedes any sign-in still pending.
        with self._db.tx() as conn:
            conn.execute("UPDATE account SET device_id=?, access_token=?, refresh_token=?, "
                         "access_expires_at=?, last_error=NULL, device_code=NULL, user_code=NULL, "
                         "verification_url=NULL, code_expires_at=NULL, poll_interval=NULL "
                         "WHERE id=1",
                         (device_id, access_token, refresh_token, access_expires_at))
