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

    def has_room(self) -> bool:
        now = self._clock()
        with self._lock:
            return sum(t > now - self._seconds for t in self._recent) < self._limit

    def idle(self, now: float) -> bool:
        with self._lock:
            return not self._recent or self._recent[-1] <= now - self._seconds

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


class KeyedWindow:
    """A Window per key; a key idle for a whole window is forgotten, and a new
    key is refused while `max_keys` are tracked."""

    def __init__(self, limit: int, seconds: float, clock=time.time, *,
                 what: str = "requests", max_keys: int = 1024):
        self._make = lambda: Window(limit, seconds, clock, what=what)
        self._max_keys = max_keys
        self._clock = clock
        self._windows: dict[str, Window] = {}
        self._lock = threading.Lock()

    def take(self, key: str) -> bool:
        now = self._clock()
        with self._lock:
            for idle in [k for k, w in self._windows.items() if w.idle(now)]:
                del self._windows[idle]
            if key not in self._windows and len(self._windows) >= self._max_keys:
                return False
            return self._windows.setdefault(key, self._make()).take()
