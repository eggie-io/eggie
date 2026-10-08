from __future__ import annotations

from ..db import Database


class CloudProjectRepo:
    def __init__(self, db: Database):
        self._db = db

    def mapping(self) -> dict[str, dict]:
        return {r["local_id"]: {"cloud_id": r["cloud_id"], "org_id": r["org_id"]}
                for r in self._db.all("SELECT * FROM cloud_projects")}

    def map(self, local_id, cloud_id, org_id) -> None:
        with self._db.tx() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO cloud_projects(local_id, cloud_id, org_id) "
                "VALUES (?,?,?)", (local_id, cloud_id, org_id))

    def unmap(self, local_id) -> None:
        with self._db.tx() as conn:
            conn.execute("DELETE FROM cloud_projects WHERE local_id=?", (local_id,))

    def clear(self) -> None:
        with self._db.tx() as conn:
            conn.execute("DELETE FROM cloud_projects")
