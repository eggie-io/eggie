"""One slow thing at a time, off the thread that owns the window.

pywebview's `start()` blocks the main thread, so anything that takes longer
than a frame runs here and reports back through `push`. The shape is the
queue-and-thread one host/setup_app/wizard.py used, minus Tk's after() pump:
there, the UI drained a queue on a timer; here, the worker pushes.

Only one job runs at a time. Not a performance choice -- InstallState is a
JSON file, and two installs writing it would race.
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from typing import Callable

log = logging.getLogger(__name__)


class JobBusy(RuntimeError):
    """Something slow is already running."""


class JobRegistry:
    def __init__(self, push: Callable[[dict], None], *,
                 clock: Callable[[], float] = time.monotonic,
                 min_interval: float = 0.1):
        self._push = push
        self._clock = clock
        self._min_interval = min_interval
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def start(self, kind: str, work: Callable[[Callable[[dict], None]], dict]) -> str:
        job_id = uuid.uuid4().hex
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                raise JobBusy(f"{kind} cannot start: another job is running")
            thread = threading.Thread(
                target=self._run, args=(job_id, kind, work), daemon=True)
            self._thread = thread
            thread.start()
        return job_id

    def running(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    def join(self, timeout: float | None = None) -> None:
        """Wait for the running job. For tests and for shutdown."""
        thread = self._thread
        if thread is not None:
            thread.join(timeout)

    def _run(self, job_id: str, kind: str, work) -> None:
        last = [None]
        pending = [None]

        def send(event: dict) -> None:
            self._push({**event, "job": job_id, "kind": kind})

        def emit(event: dict) -> None:
            # Only progress is coalesced. A terminal or state-change event
            # held back for a tenth of a second is a screen that lies; a
            # dropped one is a screen that never recovers.
            if event.get("type") != "progress":
                # A dropped progress event must not be the last word: the
                # final one usually carries done == total, and a bar frozen
                # short of full under a "done" reads as a job that stalled.
                if pending[0] is not None:
                    send(pending[0])
                    pending[0] = None
                return send(event)
            now = self._clock()
            if last[0] is not None and now - last[0] < self._min_interval:
                pending[0] = event
                return
            last[0] = now
            pending[0] = None
            send(event)

        log.info("%s job started", kind)
        try:
            result = work(emit)
        except Exception as e:
            log.exception("%s job crashed", kind)
            # Never let a worker die into a daemon thread's silence: the
            # screen that started it would spin forever.
            if pending[0] is not None:
                send(pending[0])
            send({"type": "crashed", "message": f"{e}"})
            return
        if pending[0] is not None:
            send(pending[0])
        result = result if result is not None else {"type": "done"}
        log.info("%s job ended: %s", kind, result.get("type"))
        send(result)
