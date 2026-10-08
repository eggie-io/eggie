from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Create:
    local_id: str


@dataclass(frozen=True)
class Delete:
    local_id: str
    cloud_id: str


@dataclass(frozen=True)
class Forget:
    local_id: str


def plan(local_ids: set[str], mapping: dict[str, dict], org_id: str) -> list:
    actions: list = []
    ours: dict[str, str] = {}
    for local_id, entry in sorted(mapping.items()):
        # Another account's records are not ours to delete.
        if entry["org_id"] != org_id:
            actions.append(Forget(local_id))
        else:
            ours[local_id] = entry["cloud_id"]
    for local_id, cloud_id in sorted(ours.items()):
        if local_id not in local_ids:
            actions.append(Delete(local_id, cloud_id))
    for local_id in sorted(local_ids - ours.keys()):
        actions.append(Create(local_id))
    return actions


def client_ref(device_id: str, local_id: str) -> str:
    return f"{device_id}/{local_id}"
