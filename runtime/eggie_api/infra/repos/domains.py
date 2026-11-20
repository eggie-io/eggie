from __future__ import annotations

from ..db import Database

_COLUMNS = ("hostname", "display", "project_id", "is_primary", "status", "problem_json",
            "dns_json", "certificate_id", "checked_at", "next_check_at")
_INSERT = (f"INSERT INTO domains({', '.join(_COLUMNS)}) "
           f"VALUES ({', '.join('?' * len(_COLUMNS))})")
# rowid keeps the service's order within a project.
_ORDER = "ORDER BY project_id, is_primary DESC, rowid"


def _values(row: dict) -> tuple:
    return tuple(row[c] for c in _COLUMNS)


class DomainRepo:
    """The local copy of the service's domain list, and the hostnames still
    to be deleted there."""

    def __init__(self, db: Database):
        self._db = db

    def list(self) -> list[dict]:
        return self._db.all(f"SELECT * FROM domains {_ORDER}")

    def for_project(self, project_id: str) -> list[dict]:
        return self._db.all(f"SELECT * FROM domains WHERE project_id=? {_ORDER}",
                            (project_id,))

    def replace_all(self, rows: list[dict]) -> None:
        with self._db.tx() as conn:
            conn.execute("DELETE FROM domains")
            conn.executemany(_INSERT, [_values(r) for r in rows])

    def replace_project(self, project_id: str, rows: list[dict]) -> None:
        with self._db.tx() as conn:
            conn.execute("DELETE FROM domains WHERE project_id=?", (project_id,))
            conn.executemany(_INSERT, [_values(r) for r in rows])

    def upsert(self, row: dict) -> None:
        updates = ", ".join(f"{c}=excluded.{c}" for c in _COLUMNS[1:])
        with self._db.tx() as conn:
            conn.execute(f"{_INSERT} ON CONFLICT(hostname) DO UPDATE SET {updates}",
                         _values(row))

    def remove(self, hostname: str) -> None:
        with self._db.tx() as conn:
            conn.execute("DELETE FROM domains WHERE hostname=?", (hostname,))

    def forget_project(self, project_id: str) -> None:
        with self._db.tx() as conn:
            conn.execute("INSERT OR IGNORE INTO domain_deletes(hostname) "
                         "SELECT hostname FROM domains WHERE project_id=?", (project_id,))
            conn.execute("DELETE FROM domains WHERE project_id=?", (project_id,))

    def queue(self, hostname: str) -> None:
        with self._db.tx() as conn:
            conn.execute("INSERT OR IGNORE INTO domain_deletes(hostname) VALUES (?)",
                         (hostname,))

    def queued(self) -> list[str]:
        return [r["hostname"] for r in self._db.all(
            "SELECT hostname FROM domain_deletes ORDER BY rowid")]

    def unqueue(self, hostname: str) -> None:
        with self._db.tx() as conn:
            conn.execute("DELETE FROM domain_deletes WHERE hostname=?", (hostname,))
