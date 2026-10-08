from __future__ import annotations

import logging
import threading
import time
from typing import Callable

from ..constants import VERIFY_PROJECT_ID
from ..domain.sync import Create, Delete, Forget, client_ref, plan
from ..infra.cloud import CloudError, CloudUnavailable
from ..infra.repos import Repos
from .account import NotSignedIn

log = logging.getLogger("eggie.sync")


def apply(actions, *, account, cloud, repos: Repos, org_id: str) -> list[str]:
    errors: list[str] = []
    device_id = account.device_id
    for action in actions:
        try:
            if isinstance(action, Forget):
                repos.cloud_projects.unmap(action.local_id)
            elif isinstance(action, Create):
                ref = client_ref(device_id, action.local_id)
                out = account.authed(lambda token, a=action, r=ref:
                                     cloud.create_project(token, a.local_id, r))
                repos.cloud_projects.map(action.local_id, out["id"], org_id)
            else:
                try:
                    account.authed(lambda token, a=action:
                                   cloud.delete_project(token, a.cloud_id))
                except CloudError as e:
                    if e.status != 404:
                        raise
                repos.cloud_projects.unmap(action.local_id)
        except NotSignedIn:
            raise
        except Exception as e:
            log.warning("sync of %s failed: %s", action.local_id, e)
            errors.append(f"{action.local_id}: {e}")
    return errors


def run_pass(account, cloud, repos: Repos, clock=time.time) -> None:
    if not account.signed_in:
        return
    try:
        org_id = account.load_identity()
        local_ids = {row["id"] for row in repos.projects.list()} - {VERIFY_PROJECT_ID}
        actions = plan(local_ids, repos.cloud_projects.mapping(), org_id)
        errors = apply(actions, account=account, cloud=cloud, repos=repos,
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
    def __init__(self, passes: list[Callable[[], None]], *, interval: float = 60.0):
        self._passes = list(passes)
        self._interval = interval
        self._wake = threading.Event()

    def wake(self) -> None:
        self._wake.set()

    def run_once(self) -> None:
        # Each pass fails on its own: a broken public-URL check must not
        # stop projects from being registered.
        for run in self._passes:
            try:
                run()
            except Exception:
                log.exception("sync pass %s failed",
                              getattr(run, "__qualname__", run))

    def run_forever(self) -> None:
        while True:
            # Cleared before the pass: a change made during it runs another.
            self._wake.clear()
            self.run_once()
            self._wake.wait(self._interval)

    def start(self) -> None:
        threading.Thread(target=self.run_forever, name="eggie-sync",
                         daemon=True).start()
