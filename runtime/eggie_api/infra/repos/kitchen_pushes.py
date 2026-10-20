from __future__ import annotations

import json

from ..db import Database


class KitchenPushRepo:
    def __init__(self, db: Database):
        self._db = db

    def record(self, local_id, instance_id, *, console_hostname: str,
               kitchen_project: str | None, urls: list[str],
               secrets_missing: int, at: float) -> None:
        with self._db.tx() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO kitchen_pushes(local_id, instance_id, "
                "console_hostname, kitchen_project, pushed_at, urls_json, "
                "secrets_missing) VALUES (?,?,?,?,?,?,?)",
                (local_id, instance_id, console_hostname, kitchen_project, at,
                 json.dumps(urls), secrets_missing))

    def for_project(self, local_id) -> dict[str, dict]:
        rows = self._db.all(
            "SELECT * FROM kitchen_pushes WHERE local_id=?", (local_id,))
        return {r["instance_id"]: {
            "pushed_at": r["pushed_at"],
            "console_hostname": r["console_hostname"],
            "kitchen_project": r["kitchen_project"],
            "urls": json.loads(r["urls_json"]),
            "secrets_missing": r["secrets_missing"],
        } for r in rows}

    def pushed_ids(self) -> set[str]:
        return {r["local_id"] for r in self._db.all(
            "SELECT DISTINCT local_id FROM kitchen_pushes")}
