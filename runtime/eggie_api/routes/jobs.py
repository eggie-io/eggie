from __future__ import annotations

import threading
import time
import uuid
from typing import Callable, Iterator

RUNNING = "running"
DONE = "done"
FAILED = "failed"

Logger = Callable[[str], None]
Work = Callable[[Logger], dict | None]


class JobFailed(Exception):
    """Work that failed for a reason the caller can act on. The message carries
    the guest's own output; `result` keeps whatever was learned before it
    failed (a compose status, say), because a bare failure is unactionable."""

    def __init__(self, message: str, result: dict | None = None):
        super().__init__(message)
        self.result = result


class Job:
    def __init__(self, job_id: str, kind: str | None = None,
                 project_id: str | None = None):
        self.id = job_id
        self.kind = kind
        self.project_id = project_id
        self.state = RUNNING
        self.phase = "preparing"
        self.detail = ""
        self.result: dict | None = None
        self.logs: list[str] = []
        self.started_at = time.time()
        self.finished_at: float | None = None
        self.cond = threading.Condition()

    def append(self, chunk: str) -> None:
        with self.cond:
            self.logs.append(chunk)
            self.cond.notify_all()

    def set_phase(self, phase: str) -> None:
        with self.cond:
            self.phase = phase

    def finish(self, state: str, detail: str = "", result: dict | None = None) -> None:
        with self.cond:
            self.state = state
            self.detail = detail
            self.result = result
            self.finished_at = time.time()
            self.cond.notify_all()

    def text(self) -> str:
        with self.cond:
            return "".join(self.logs)

    def as_dict(self) -> dict:
        with self.cond:
            return {
                "job_id": self.id,
                "kind": self.kind,
                "phase": self.phase,
                "state": self.state,
                "detail": self.detail,
                "result": self.result,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
            }


class _Output:
    """What a work function receives: call it to log, `.phase()` to say where
    it is. Callable so every existing `write(...)` keeps working."""

    def __init__(self, job: Job):
        self._job = job

    def __call__(self, chunk: str) -> None:
        self._job.append(chunk)

    def phase(self, name: str) -> None:
        self._job.set_phase(name)


class JobRegistry:
    """Slow work runs on its own thread so the request that started it returns
    at once — a first image build takes minutes.

    Jobs live in memory only and are deliberately not persisted: an API
    restart loses them, and the host re-reads project status from the API.
    """

    def __init__(self, max_finished: int = 100):
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._max_finished = max_finished

    def submit(self, work: Work, *, kind: str | None = None,
               project_id: str | None = None) -> str:
        job = Job(uuid.uuid4().hex[:12], kind, project_id)
        with self._lock:
            self._jobs[job.id] = job
            self._evict_finished()
        threading.Thread(target=self._run, args=(job, work), daemon=True).start()
        return job.id

    def _run(self, job: Job, work: Work) -> None:
        try:
            result = work(_Output(job))
        except JobFailed as e:
            job.finish(FAILED, detail=str(e), result=e.result)
        except Exception as e:  # a crashed worker must not stay "running" forever
            job.finish(FAILED, detail=f"{type(e).__name__}: {e}")
        else:
            job.finish(DONE, result=result)

    def _evict_finished(self) -> None:
        """Finished jobs keep their whole log buffer alive; a long-lived API
        would grow one per compose operation forever. Runs when a job is
        submitted, so the newest finished job always survives. Caller holds the
        lock."""
        finished = sorted((j for j in self._jobs.values() if j.finished_at is not None),
                          key=lambda j: j.finished_at)
        for job in finished[:max(0, len(finished) - self._max_finished)]:
            del self._jobs[job.id]

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def require(self, job_id: str) -> Job:
        job = self.get(job_id)
        if job is None:
            raise KeyError(job_id)
        return job

    def active_for(self, project_id: str) -> Job | None:
        with self._lock:
            running = [j for j in self._jobs.values()
                       if j.project_id == project_id and j.state == RUNNING]
        return running[-1] if running else None

    def wait(self, job_id: str, timeout: float | None = None) -> Job:
        job = self.require(job_id)
        deadline = None if timeout is None else time.monotonic() + timeout
        with job.cond:
            while job.state == RUNNING:
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    break
                job.cond.wait(remaining)
        return job

    def follow(self, job_id: str) -> Iterator[str]:
        """Yields output as it is produced, then ends when the job does."""
        job = self.require(job_id)
        sent = 0
        while True:
            with job.cond:
                while sent >= len(job.logs) and job.state == RUNNING:
                    # Timed wait as defence in depth: a reader must not hang
                    # forever if a worker ever fails to notify.
                    job.cond.wait(0.25)
                chunk = job.logs[sent:]
                sent += len(chunk)
                finished = job.state != RUNNING and sent >= len(job.logs)
            yield from chunk
            if finished:
                return
