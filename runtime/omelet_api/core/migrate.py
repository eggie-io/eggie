"""The state database's schema, as an ordered list applied at API startup.

Each entry is applied exactly once, in order, and the version it produces is
recorded as soon as it finishes. Every step is written so that re-running it is
harmless: the first APIs shipped without a `schema_version` table at all, so
their databases arrive here looking like version 0 with the tables already in
place.
"""
from __future__ import annotations

import sqlite3
import uuid


class SchemaTooNew(RuntimeError):
    """The database was written by a newer API than this one."""


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _add_column(conn: sqlite3.Connection, table: str, column: str, decl: str) -> None:
    # ALTER TABLE has no IF NOT EXISTS, and the column may already be there:
    # state.py created it directly before this module existed.
    if column not in _columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def _v1_projects(conn: sqlite3.Connection) -> None:
    # One statement per execute(), not executescript(): that commits any open
    # transaction first, which would break the one migrate() holds.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS projects (
            id TEXT PRIMARY KEY,
            guest_path TEXT NOT NULL,
            domain TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'stopped'
        )""")


def _v2_project_problem(conn: sqlite3.Connection) -> None:
    _add_column(conn, "projects", "problem_code", "TEXT")
    _add_column(conn, "projects", "problem_message", "TEXT")


def _v3_web_ui(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id_hash TEXT PRIMARY KEY,
            expires_at REAL NOT NULL
        )""")
    _add_column(conn, "projects", "last_started_at", "REAL")
    _add_column(conn, "projects", "compose_name", "TEXT")


def _v4_account(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS account (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            device_id TEXT NOT NULL,
            email TEXT,
            org_id TEXT,
            access_token TEXT,
            refresh_token TEXT,
            access_expires_at REAL,
            device_code TEXT,
            user_code TEXT,
            verification_url TEXT,
            code_expires_at REAL,
            poll_interval REAL,
            last_error TEXT,
            sync_ok_at REAL,
            sync_error TEXT
        )""")
    # Minted here, once: a device id that changed on restart would orphan
    # every record this device made on the service.
    conn.execute("INSERT OR IGNORE INTO account(id, device_id) VALUES (1, ?)",
                 (str(uuid.uuid4()),))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS cloud_projects (
            local_id TEXT PRIMARY KEY,
            cloud_id TEXT NOT NULL,
            org_id TEXT NOT NULL
        )""")


def _v5_github(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS github (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            generation INTEGER NOT NULL DEFAULT 0,
            desired_at REAL,
            login TEXT,
            gh_id INTEGER,
            name TEXT,
            email TEXT,
            needs_reconnect INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            checked_at REAL
        )""")
    conn.execute("INSERT OR IGNORE INTO github(id) VALUES (1)")


# Append only. Editing an entry that has already shipped changes nothing on a
# database that ran it -- add the next one instead.
MIGRATIONS = [
    _v1_projects,
    _v2_project_problem,
    _v3_web_ui,
    _v4_account,
    _v5_github,
]
SCHEMA_VERSION = len(MIGRATIONS)


def _read_version(conn: sqlite3.Connection) -> int:
    """Reads without writing: nothing may touch a database until the too-new
    check has passed on it."""
    known = conn.execute("SELECT name FROM sqlite_master WHERE type='table' "
                         "AND name='schema_version'").fetchone()
    if not known:
        return 0
    row = conn.execute("SELECT version FROM schema_version").fetchone()
    return int(row[0]) if row else 0


def _write_version(conn: sqlite3.Connection, version: int) -> None:
    conn.execute("DELETE FROM schema_version")
    conn.execute("INSERT INTO schema_version(version) VALUES (?)", (version,))


def migrate(conn: sqlite3.Connection) -> int:
    """Brings `conn` to `SCHEMA_VERSION` and returns it. Raises `SchemaTooNew`
    without touching anything if the database is ahead of this API."""
    version = _read_version(conn)
    if version > SCHEMA_VERSION:
        raise SchemaTooNew(
            f"the state database is at schema version {version}, but this "
            f"API only knows version {SCHEMA_VERSION}. It was written by a "
            f"newer API; run the newer one, or delete the database — the "
            f"project list is rebuilt from the projects directory.")
    if version == SCHEMA_VERSION:
        return version
    conn.execute("CREATE TABLE IF NOT EXISTS schema_version "
                 "(version INTEGER NOT NULL)")
    conn.commit()
    for index in range(version, SCHEMA_VERSION):
        # A step and the version marker recording it are one transaction. Every
        # step today is idempotent, but the first backfill anyone adds must not
        # be able to run twice after a crash between the two.
        conn.execute("BEGIN")
        try:
            MIGRATIONS[index](conn)
            _write_version(conn, index + 1)
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
    return SCHEMA_VERSION
