"""The host's side of the host/API seam: the token read, and the HTTP client.

Built on `urllib.request` deliberately. The host ships as a PyInstaller-frozen
binary, so every dependency it declares lands in that binary; the API, which
ships as a Docker image, is the side that is free to grow one.

Nothing here imports `eggie_api/` -- the API is reached over HTTP, and its
routes are the only contract between the two.
"""

from __future__ import annotations

import json
import re
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .core import constants
from .core.provider import VmProvider

# WSL2's localhostForwarding (and Lima's portForwards) surface the API's
# guest socket on the host at the same port, so the host always dials loopback.
API_URL = f"http://127.0.0.1:{constants.API_PORT}"

# Ordinary calls are metadata-sized and should fail fast when the VM is wedged.
REQUEST_TIMEOUT = 30.0
# An upload is a whole project directory over the loopback forward, and a log
# read waits on compose; both are legitimately slower than a metadata call.
UPLOAD_TIMEOUT = 600.0
LOGS_TIMEOUT = 120.0
# `up` pulls or builds images inside the VM, which is minutes, not seconds.
JOB_TIMEOUT = 1800.0
JOB_POLL_INTERVAL = 1.0
# `project_busy` means another lifecycle operation holds the project's lock.
# Long enough to ride out a `down`, short enough not to look like a hang.
BUSY_RETRY_TIMEOUT = 60.0
BUSY_RETRY_INTERVAL = 1.0


class ApiUnavailableError(RuntimeError):
    """The API could not be reached at all: no token to authenticate with,
    or nothing listening. Raised instead of a socket error or a bare 401 so
    the first symptom a user sees names the VM, not the transport."""


class ApiError(RuntimeError):
    """A structured failure from the API: its own code and its own sentence."""

    def __init__(self, code: str, message: str, status: int):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


class JobFailedError(RuntimeError):
    """A background job ended in `failed`. The message is the guest's own
    output; `result` carries whatever the job still learned (the status a
    stack reached, for instance), so a caller can tell the failure modes
    apart instead of reporting "it failed"."""

    def __init__(self, message: str, result: dict | None = None):
        super().__init__(message)
        self.result = result or {}


class JobTimeoutError(RuntimeError):
    """A job was still running when the client's deadline passed."""


def read_token(provider: VmProvider) -> str:
    """Read `/opt/eggie/api.token` fresh, once, via `provider.exec()`.

    Never cached to the host filesystem: a copy at rest is a second secret to
    protect and a second thing to go stale after a VM rebuild. One `wsl.exe`
    round trip per CLI invocation is an acceptable price.

    Phase 3 replaces this shared, VM-wide token with a service-issued device
    token; this function is the one place that changes.
    """
    result = provider.exec(["cat", constants.GUEST_TOKEN], root=True)
    token = result.stdout.strip() if result.ok else ""
    if not token:
        raise ApiUnavailableError(
            "the VM has no API token -- it may not be provisioned yet; "
            "run setup and try again")
    return token


def auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def project_id_for(name: str) -> str:
    """The same slug rule the API applies to an id or a directory name.
    Duplicated rather than imported (nothing under `host/` imports
    `eggie_api/`) and held equal by a test: the host needs the id before it
    can ask for the project it just tried to create."""
    return re.sub(r"[^a-z0-9-]+", "-", name.strip().lower()).strip("-")


# Two of the API's error codes have a known real-world cause the API cannot
# know about, and their own wording ("missing or invalid bearer token") tells
# a non-technical user nothing they can act on.
_GUIDANCE = {
    "unauthorized": "The VM no longer accepts this token, which usually means "
                    "the VM was rebuilt. Run `eggie setup` to reconnect.",
    constants.API_UNCONFIGURED: "The VM has not finished setting itself up. "
                                "Run `eggie setup`.",
}


def _api_error(exc: urllib.error.HTTPError) -> ApiError:
    """Every non-2xx body from the API is `{"error": {"code", "message"}}`.
    Anything else answering on this port (a proxy, a crashed server) must
    still come out as a readable failure rather than a JSONDecodeError."""
    try:
        error = json.loads(exc.read().decode("utf-8", "replace"))["error"]
        code, message = str(error["code"]), str(error["message"])
        guidance = _GUIDANCE.get(code)
        if guidance:
            message = f"{guidance} (the API said: {message})"
        return ApiError(code, message, exc.code)
    except (OSError, ValueError, KeyError, TypeError):
        return ApiError("http_error",
                          f"the API answered HTTP {exc.code} ({exc.reason})",
                          exc.code)


# Never uploaded: a repository's object store, dependency trees and caches are
# megabytes to gigabytes the VM has no use for, re-sent on every `up`, and
# `.git/config` would carry credentials into the guest as a side effect.
# Matched on any path component, so a nested node_modules is excluded too.
EXCLUDED_DIRS = frozenset({".git", "node_modules", ".venv", "__pycache__"})
# The overlay is generated inside the VM on every `compose_up`. `.eggie/` as a
# whole is NOT excluded: `.eggie/project.yml` is the user's own configuration
# and the API reads it to resolve web services.
EXCLUDED_FILES = frozenset({".eggie/overlay.yml"})


def _uploadable(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
    """`tarfile.add`'s filter: dropping a directory here prunes its contents."""
    if info.name in EXCLUDED_FILES:
        return None
    if any(part in EXCLUDED_DIRS for part in info.name.split("/")):
        return None
    return info


class _CountingReader:
    """Wraps the archive so urllib's read loop reports bytes as they leave.

    urllib calls read(blocksize) until it gets b"", so the count here is what
    was actually handed to the socket. The final call reports done == total
    explicitly: a bar that stops at 99.6% reads as a hang.
    """

    def __init__(self, stream, total: int, report):
        self._stream = stream
        self._total = total
        self._report = report
        self._done = 0

    def read(self, amount: int = -1) -> bytes:
        chunk = self._stream.read(amount)
        if chunk:
            self._done += len(chunk)
            self._report("sending", self._done, self._total)
        else:
            self._report("sending", self._total, self._total)
        return chunk


class ApiClient:
    """Every route the CLI needs, and no transport detail above this line."""

    def __init__(self, token: str, *, base_url: str = API_URL, opener=None,
                 sleep=time.sleep, monotonic=time.monotonic):
        self._token = token
        self._base = base_url.rstrip("/")
        # Injected in tests; the default is a plain opener with no proxy or
        # redirect surprises beyond urllib's own.
        self._opener = opener or urllib.request.build_opener()
        self._sleep = sleep
        self._monotonic = monotonic

    @classmethod
    def for_provider(cls, provider: VmProvider, **kwargs) -> "ApiClient":
        return cls(read_token(provider), **kwargs)

    # -- transport ---------------------------------------------------------

    def _open(self, method: str, path: str, *, data=None, headers=None,
              timeout: float):
        request = urllib.request.Request(self._base + path, data=data,
                                         method=method)
        for name, value in auth_header(self._token).items():
            request.add_header(name, value)
        for name, value in (headers or {}).items():
            request.add_header(name, value)
        try:
            return self._opener.open(request, timeout=timeout)
        except urllib.error.HTTPError as e:
            raise _api_error(e) from None
        except (urllib.error.URLError, OSError) as e:
            reason = getattr(e, "reason", e)
            raise ApiUnavailableError(
                f"could not reach the Eggie API at {self._base} ({reason}). "
                "The VM may be stopped -- run `eggie vm start`, or run setup "
                "again if this is a new machine.") from e

    def _call(self, method: str, path: str, payload: dict | None = None, *,
              timeout: float = REQUEST_TIMEOUT) -> dict:
        data = headers = None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers = {"Content-Type": "application/json"}
        with self._open(method, path, data=data, headers=headers,
                        timeout=timeout) as response:
            body = response.read().decode("utf-8")
        return json.loads(body) if body.strip() else {}

    def _text(self, method: str, path: str, *,
              timeout: float = REQUEST_TIMEOUT) -> str:
        with self._open(method, path, timeout=timeout) as response:
            return response.read().decode("utf-8", "replace")

    def _while_busy(self, call):
        """`project_busy` (409) means another lifecycle operation holds the
        project's lock -- retry, not fail."""
        deadline = self._monotonic() + BUSY_RETRY_TIMEOUT
        while True:
            try:
                return call()
            except ApiError as e:
                if e.code != "project_busy" or self._monotonic() >= deadline:
                    raise
            self._sleep(BUSY_RETRY_INTERVAL)

    # -- routes ------------------------------------------------------------

    def health(self) -> dict:
        return self._call("GET", "/health")

    def version(self) -> str:
        return str(self._call("GET", "/version").get("version", ""))

    def handoff_code(self) -> str:
        return str(self._call("POST", "/sessions/handoff")["code"])

    def create_project(self, project_id: str, *, web: list[dict] | None = None,
                       domain: str | None = None) -> dict:
        body: dict = {"id": project_id}
        if web is not None:
            body["web"] = web
        if domain is not None:
            body["domain"] = domain
        return self._call("POST", "/projects", body)

    def ensure_project(self, project_id: str, **kwargs) -> dict:
        """Create the project, or return the existing one. `up` on a project
        that is already known is the ordinary case, not an error."""
        try:
            return self.create_project(project_id, **kwargs)
        except ApiError as e:
            if e.code != "project_exists":
                raise
        return self.get_project(project_id_for(project_id))

    def list_projects(self) -> list[dict]:
        return self._call("GET", "/projects").get("projects", [])

    def get_project(self, project_id: str) -> dict:
        return self._call("GET", f"/projects/{project_id}")

    def delete_project(self, project_id: str) -> dict:
        """Synchronous by design on the API side: `compose down` is bounded
        by container stop timeouts, not by an image build."""
        return self._while_busy(
            lambda: self._call("DELETE", f"/projects/{project_id}",
                               timeout=LOGS_TIMEOUT))

    def upload_directory(self, project_id: str, local_dir, *,
                         on_progress=None) -> dict:
        """Send the directory's contents as a raw tar.gz body (not multipart).

        Archived into a temp file rather than memory so a large project is
        never held twice, and streamed from there by urllib.

        `on_progress(phase, done, total)` is called for both phases the user
        waits through: "packing" counts top-level entries, "sending" counts
        bytes. A caller that passes nothing pays for nothing.
        """
        report = on_progress or (lambda phase, done, total: None)
        with tempfile.TemporaryFile() as archive:
            items = sorted(Path(local_dir).iterdir())
            with tarfile.open(fileobj=archive, mode="w:gz") as tar:
                for index, item in enumerate(items, 1):
                    tar.add(item, arcname=item.name, filter=_uploadable)
                    report("packing", index, len(items))
            size = archive.tell()

            def send():
                # Rewound per attempt: a retry after `project_busy` must send
                # the archive again, not the empty tail the last one left.
                archive.seek(0)
                body = _CountingReader(archive, size, report)
                with self._open("POST", f"/projects/{project_id}/files",
                                data=body,
                                headers={"Content-Type": "application/gzip",
                                         "Content-Length": str(size)},
                                timeout=UPLOAD_TIMEOUT) as response:
                    return response.read().decode("utf-8")

            body = self._while_busy(send)
        return json.loads(body) if body.strip() else {}

    def project_up(self, project_id: str) -> str:
        return self._while_busy(
            lambda: self._call("POST", f"/projects/{project_id}/up"))["job_id"]

    def project_down(self, project_id: str) -> str:
        return self._while_busy(
            lambda: self._call("POST", f"/projects/{project_id}/down"))["job_id"]

    def logs(self, project_id: str, service: str | None = None) -> str:
        path = f"/projects/{project_id}/logs"
        if service:
            path += f"?service={urllib.parse.quote(service)}"
        return self._text("GET", path, timeout=LOGS_TIMEOUT)

    def job(self, job_id: str) -> dict:
        return self._call("GET", f"/jobs/{job_id}")

    def job_logs(self, job_id: str) -> str:
        return self._text("GET", f"/jobs/{job_id}/logs", timeout=LOGS_TIMEOUT)

    def wait_for_job(self, job_id: str, *, timeout: float = JOB_TIMEOUT) -> dict:
        """Poll to completion. A failed job raises with the guest's own output
        rather than a state name no one can act on."""
        deadline = self._monotonic() + timeout
        while True:
            job = self.job(job_id)
            state = job.get("state")
            if state == "done":
                return job
            if state == "failed":
                raise JobFailedError(
                    job.get("detail") or "the operation failed inside the VM",
                    job.get("result"))
            if self._monotonic() >= deadline:
                raise JobTimeoutError(
                    f"job {job_id} was still running after {timeout:.0f}s "
                    "inside the VM")
            self._sleep(JOB_POLL_INTERVAL)
