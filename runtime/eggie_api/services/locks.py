from __future__ import annotations

import threading
from contextlib import contextmanager

from ..errors import Conflict


def busy(project_id: str) -> Conflict:
    return Conflict("project_busy",
                    f"another operation on '{project_id}' is still running")


class ProjectLocks:
    """One lifecycle operation per project at a time. Non-blocking on purpose:
    two `up` jobs would race on the same `.eggie/overlay.yml`, and a blocking
    lock would only move a multi-minute hold onto whoever waits."""

    def __init__(self):
        self._lock = threading.Lock()
        self._held: set[str] = set()

    def acquire(self, project_id: str) -> bool:
        with self._lock:
            if project_id in self._held:
                return False
            self._held.add(project_id)
            return True

    def release(self, project_id: str) -> None:
        with self._lock:
            self._held.discard(project_id)

    def acquire_or_raise(self, project_id: str) -> None:
        if not self.acquire(project_id):
            raise busy(project_id)

    @contextmanager
    def held(self, project_id: str):
        self.acquire_or_raise(project_id)
        try:
            yield
        finally:
            self.release(project_id)
