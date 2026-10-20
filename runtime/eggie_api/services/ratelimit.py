from __future__ import annotations

import logging
import threading
import time
from collections import deque

log = logging.getLogger("eggie.api")


class Window:
    """At most `limit` takes in any rolling `seconds`. Logs one warning per
    run of refusals, not one per refused request."""

    def __init__(self, limit: int, seconds: float, clock=time.time, *,
                 what: str = "requests"):
        self._limit = limit
        self._seconds = seconds
        self._clock = clock
        self._what = what
        self._recent: deque[float] = deque()
        self._warned = False
        self._lock = threading.Lock()

    def take(self) -> bool:
        now = self._clock()
        with self._lock:
            while self._recent and self._recent[0] <= now - self._seconds:
                self._recent.popleft()
            if len(self._recent) >= self._limit:
                warn, self._warned = not self._warned, True
            else:
                self._recent.append(now)
                self._warned = False
                return True
        if warn:
            log.warning("more than %d %s in %.0f s; holding the rest back",
                        self._limit, self._what, self._seconds)
        return False
