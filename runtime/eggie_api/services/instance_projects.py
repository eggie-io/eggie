from __future__ import annotations

import logging
import time

from ..constants import VERIFY_PROJECT_ID
from ..domain.project import STARTED_OK
from ..errors import EggieError
from ..infra.cloud import CloudError, CloudUnavailable
from ..infra.repos.projects import ProjectRepo
from .account import NotSignedIn
from .loader import ProjectLoader

log = logging.getLogger("eggie.instance_projects")

HEARTBEAT = 10 * 60
FIRST_BACKOFF = 5.0
MAX_BACKOFF = 300.0
# The service's limits; a project over them would get the whole list refused.
MAX_PROJECTS = 500
MAX_NAME = 100


def _transient(e: CloudError) -> bool:
    # A 401 here survived a refresh; it is the service's state, not the list's.
    return e.status >= 500 or e.status in (401, 429)


class InstanceProjects:
    """A kitchen's project list, reported to the service as one sync pass.
    Each report is the full list; an unchanged one is resent only as the
    heartbeat that heals a copy the service lost."""

    def __init__(self, projects: ProjectRepo, loader: ProjectLoader, account, cloud,
                 *, clock=time.time):
        self._projects = projects
        self._loader = loader
        self._account = account
        self._cloud = cloud
        self._clock = clock
        self._sent: list[dict] | None = None
        self._sent_at = 0.0
        self._refused: list[dict] | None = None
        self._retry_at = 0.0
        self._backoff = 0.0

    def _public_url(self, row: dict) -> str | None:
        # Only a running project answers; a stopped one's address is a 404.
        # A stored problem is not checked: a slow entrypoint leaves one that
        # stays long after the project answers.
        if row["status"] != STARTED_OK:
            return None
        try:
            urls = self._loader.urls_for(self._loader.load(row["id"]), row["domain"])
        except EggieError:
            return None
        return urls[0] if urls else None

    def snapshot(self) -> list[dict]:
        rows = [row for row in self._projects.list()
                if row["id"] != VERIFY_PROJECT_ID and len(row["id"]) <= MAX_NAME]
        return [{"ref": row["id"], "name": row["id"], "public_url": self._public_url(row)}
                for row in rows[:MAX_PROJECTS]]

    def report(self) -> None:
        if not self._account.signed_in:
            return
        projects = self.snapshot()
        now = self._clock()
        if projects == self._refused or now < self._retry_at:
            return
        if projects == self._sent and now - self._sent_at < HEARTBEAT:
            return
        try:
            self._account.authed(
                lambda token: self._cloud.report_instance_projects(token, projects))
        except NotSignedIn:
            return
        except CloudUnavailable as e:
            self._back_off(now, str(e))
            return
        except CloudError as e:
            if _transient(e):
                self._back_off(now, str(e))
            else:
                log.warning("the service refused the project list: %s", e)
                self._refused = projects
            return
        self._sent, self._sent_at = projects, now
        self._refused, self._backoff, self._retry_at = None, 0.0, 0.0

    def _back_off(self, now: float, why: str) -> None:
        self._backoff = min(MAX_BACKOFF, self._backoff * 2 or FIRST_BACKOFF)
        self._retry_at = now + self._backoff
        log.info("reporting projects failed (%s); retrying in %.0f s", why, self._backoff)
