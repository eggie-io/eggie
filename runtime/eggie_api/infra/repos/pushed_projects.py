from __future__ import annotations

from ..db import Database


class PushedProjectRepo:
    def __init__(self, db: Database):
        self._db = db

    def by_cloud_id(self, cloud_id) -> dict | None:
        return self._db.one(
            "SELECT * FROM pushed_projects WHERE cloud_project_id=?", (cloud_id,))

    def local_ids(self) -> set[str]:
        return {r["local_id"] for r in self._db.all(
            "SELECT local_id FROM pushed_projects")}

    def claim(self, local_id, cloud_project_id, source_device_id, at) -> None:
        with self._db.tx() as conn:
            conn.execute(
                "INSERT INTO pushed_projects(local_id, cloud_project_id, "
                "source_device_id, first_pushed_at) VALUES (?,?,?,?) "
                "ON CONFLICT(local_id) DO UPDATE SET "
                "cloud_project_id=excluded.cloud_project_id, "
                "source_device_id=excluded.source_device_id",
                (local_id, cloud_project_id, source_device_id, at))

    def touch(self, local_id, at) -> None:
        with self._db.tx() as conn:
            conn.execute("UPDATE pushed_projects SET last_pushed_at=? "
                         "WHERE local_id=?", (at, local_id))

    def remove(self, local_id) -> None:
        with self._db.tx() as conn:
            conn.execute("DELETE FROM pushed_projects WHERE local_id=?",
                         (local_id,))
