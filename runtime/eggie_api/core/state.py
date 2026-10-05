from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from .migrate import migrate

ACCOUNT_FIELDS = frozenset({
    "email", "org_id", "access_token", "refresh_token", "access_expires_at",
    "device_code", "user_code", "verification_url", "code_expires_at",
    "poll_interval", "last_error", "sync_ok_at", "sync_error"})

GITHUB_FIELDS = frozenset({
    "generation", "desired_at", "login", "gh_id", "name", "email",
    "needs_reconnect", "last_error", "checked_at"})


class State:
    """The API's project list, on one connection shared by every thread.

    FastAPI serves sync routes from a threadpool and jobs run on threads of
    their own, so a thread-bound connection fails as soon as a second thread
    touches it. One connection with `check_same_thread=False` behind a lock
    holds for both, and — unlike a connection per thread — nothing accumulates
    an open handle for every worker the threadpool ever created.
    """

    def __init__(self, db_path):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            migrate(self._conn)

    def add_project(self, id, guest_path, domain, status="stopped"):
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO projects(id, guest_path, domain, status) "
                "VALUES (?,?,?,?)", (id, guest_path, domain, status))
            self._conn.commit()

    def get_project(self, id):
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM projects WHERE id=?", (id,)).fetchone()
        return dict(row) if row else None

    def list_projects(self):
        with self._lock:
            return [dict(r) for r in
                    self._conn.execute("SELECT * FROM projects ORDER BY id")]

    def set_status(self, id, status):
        with self._lock:
            self._conn.execute("UPDATE projects SET status=? WHERE id=?",
                               (status, id))
            self._conn.commit()

    def set_problem(self, id, code=None, message=None):
        """`code=None` clears it: a problem that outlives the fix is worse than
        none, so every `up` writes this whether or not it found something."""
        with self._lock:
            self._conn.execute(
                "UPDATE projects SET problem_code=?, problem_message=? WHERE id=?",
                (code, message, id))
            self._conn.commit()

    def set_compose_name(self, id, compose_name):
        with self._lock:
            self._conn.execute(
                "UPDATE projects SET compose_name=? WHERE id=?",
                (compose_name, id))
            self._conn.commit()

    def mark_started(self, id, at):
        with self._lock:
            self._conn.execute(
                "UPDATE projects SET last_started_at=? WHERE id=?", (at, id))
            self._conn.commit()

    def remove_project(self, id):
        with self._lock:
            self._conn.execute("DELETE FROM projects WHERE id=?", (id,))
            self._conn.commit()

    def add_session(self, id_hash, expires_at):
        with self._lock:
            self._conn.execute(
                "INSERT INTO sessions(id_hash, expires_at) VALUES (?,?)",
                (id_hash, expires_at))
            self._conn.commit()

    def get_session(self, id_hash):
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM sessions WHERE id_hash=?", (id_hash,)).fetchone()
        return dict(row) if row else None

    def set_session_expiry(self, id_hash, expires_at):
        with self._lock:
            self._conn.execute(
                "UPDATE sessions SET expires_at=? WHERE id_hash=?",
                (expires_at, id_hash))
            self._conn.commit()

    def remove_session(self, id_hash):
        with self._lock:
            self._conn.execute("DELETE FROM sessions WHERE id_hash=?", (id_hash,))
            self._conn.commit()

    def get_account(self) -> dict:
        with self._lock:
            return dict(self._conn.execute(
                "SELECT * FROM account WHERE id=1").fetchone())

    def update_account(self, **fields) -> None:
        # Field names become SQL text below, so only known names get there.
        unknown = set(fields) - ACCOUNT_FIELDS
        if unknown:
            raise ValueError(f"unknown account fields: {sorted(unknown)}")
        if not fields:
            return
        assignments = ", ".join(f"{name}=?" for name in fields)
        with self._lock:
            self._conn.execute(f"UPDATE account SET {assignments} WHERE id=1",
                               tuple(fields.values()))
            self._conn.commit()

    def get_github(self) -> dict:
        with self._lock:
            return dict(self._conn.execute(
                "SELECT * FROM github WHERE id=1").fetchone())

    def update_github(self, **fields) -> None:
        unknown = set(fields) - GITHUB_FIELDS
        if unknown:
            raise ValueError(f"unknown github fields: {sorted(unknown)}")
        if not fields:
            return
        assignments = ", ".join(f"{name}=?" for name in fields)
        with self._lock:
            self._conn.execute(f"UPDATE github SET {assignments} WHERE id=1",
                               tuple(fields.values()))
            self._conn.commit()

    def cloud_mapping(self) -> dict[str, dict]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM cloud_projects").fetchall()
        return {r["local_id"]: {"cloud_id": r["cloud_id"], "org_id": r["org_id"]}
                for r in rows}

    def map_cloud_project(self, local_id, cloud_id, org_id) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO cloud_projects(local_id, cloud_id, org_id) "
                "VALUES (?,?,?)", (local_id, cloud_id, org_id))
            self._conn.commit()

    def unmap_cloud_project(self, local_id) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM cloud_projects WHERE local_id=?",
                               (local_id,))
            self._conn.commit()

    def clear_cloud_projects(self) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM cloud_projects")
            self._conn.commit()

    def close(self):
        with self._lock:
            self._conn.close()
