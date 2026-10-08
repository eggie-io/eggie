from __future__ import annotations

from ..domain import secrets as rules
from ..domain.project import CRASH_LOOPING, STARTED_OK
from ..domain.secrets import SecretError
from ..errors import NotFound
from ..infra.repos.secrets import SecretRepo
from .loader import ProjectLoader

# Statuses whose containers exist and were handed the project's secrets.
RUNNING = (STARTED_OK, CRASH_LOOPING)


class SecretService:
    def __init__(self, secrets: SecretRepo, loader: ProjectLoader):
        self._secrets = secrets
        self._loader = loader

    def list(self, project_id: str) -> dict:
        row = self._loader.require(project_id)
        return {"secrets": self._secrets.names(project_id),
                "requested": self._secrets.requests(project_id),
                "restart_needed": self.restart_needed(row)}

    # No project lock: one sqlite statement, and a start in flight is caught
    # by restart_needed because the start is stamped before it reads values.
    def put(self, project_id: str, name: str, value: str) -> None:
        self._loader.require(project_id)
        if value == "":
            raise SecretError("secret_invalid_value",
                              "a value can't be empty; delete the secret instead")
        rules.check_name(name)
        rules.check_value(value)
        rules.check_total({**self._secrets.values(project_id), name: value})
        self._secrets.set(project_id, {name: value})

    def request(self, project_id: str, name: str, hint: str) -> None:
        self._loader.require(project_id)
        rules.check_name(name)
        rules.check_hint(hint)
        self._secrets.request(project_id, name, hint)

    def delete(self, project_id: str, name: str) -> None:
        self._loader.require(project_id)
        removed = self._secrets.delete(project_id, name)
        dismissed = self._secrets.delete_request(project_id, name)
        if not (removed or dismissed):
            raise NotFound("secret_not_found",
                           f"project '{project_id}' has no secret '{name}'")

    def values(self, project_id: str) -> dict[str, str] | None:
        return self._secrets.values(project_id) or None

    def requested_count(self, project_id: str) -> int:
        return len(self._secrets.requests(project_id))

    def restart_needed(self, row: dict) -> bool:
        changed = row.get("secrets_changed_at")
        started = row.get("last_started_at")
        return (row["status"] in RUNNING and changed is not None
                and (started is None or changed > started))

    def declared(self, compose: dict):
        return rules.declared(compose)

    def drop(self, project_id: str) -> None:
        self._secrets.drop(project_id)
