from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

from .account import NotSignedIn
from .cloud import CloudError, CloudUnavailable
from .constants import VERIFY_PROJECT_ID

log = logging.getLogger("eggie.sync")


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


def apply(actions, *, account, cloud, state, org_id: str) -> list[str]:
    errors: list[str] = []
    device_id = account.device_id
    for action in actions:
        try:
            if isinstance(action, Forget):
                state.unmap_cloud_project(action.local_id)
            elif isinstance(action, Create):
                ref = client_ref(device_id, action.local_id)
                out = account.authed(lambda token, a=action, r=ref:
                                     cloud.create_project(token, a.local_id, r))
                state.map_cloud_project(action.local_id, out["id"], org_id)
            else:
                try:
                    account.authed(lambda token, a=action:
                                   cloud.delete_project(token, a.cloud_id))
                except CloudError as e:
                    if e.status != 404:
                        raise
                state.unmap_cloud_project(action.local_id)
        except NotSignedIn:
            raise
        except Exception as e:
            log.warning("sync of %s failed: %s", action.local_id, e)
            errors.append(f"{action.local_id}: {e}")
    return errors


def run_pass(account, cloud, state, clock=time.time) -> None:
    if not account.signed_in:
        return
    try:
        org_id = account.load_identity()
        local_ids = {row["id"] for row in state.list_projects()} - {VERIFY_PROJECT_ID}
        actions = plan(local_ids, state.cloud_mapping(), org_id)
        errors = apply(actions, account=account, cloud=cloud, state=state,
                       org_id=org_id)
    except NotSignedIn:
        return
    except (CloudError, CloudUnavailable) as e:
        account.record_sync(error=str(e))
        return
    if errors:
        account.record_sync(error="; ".join(errors))
    else:
        account.record_sync(ok_at=clock())


class SyncLoop:
    def __init__(self, run, *, interval: float = 60.0):
        self._run = run
        self._interval = interval
        self._wake = threading.Event()

    def wake(self) -> None:
        self._wake.set()

    def run_forever(self) -> None:
        while True:
            # Cleared before the pass: a change made during it runs another.
            self._wake.clear()
            try:
                self._run()
            except Exception:
                log.exception("sync pass failed")
            self._wake.wait(self._interval)

    def start(self) -> None:
        threading.Thread(target=self.run_forever, name="eggie-sync",
                         daemon=True).start()
