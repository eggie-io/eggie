from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .constants import API_PORT, API_UNCONFIGURED, GUEST_STACK, GUEST_TOKEN
from .errors import ApiError, EggieError, JobFailed

REQUEST_TIMEOUT = 30.0
LOGS_TIMEOUT = 120.0
# `up` pulls or builds images, which is minutes, not seconds.
JOB_TIMEOUT = 1800.0
JOB_POLL_INTERVAL = 1.0
BUSY_RETRY_TIMEOUT = 60.0
BUSY_RETRY_INTERVAL = 1.0

START_STACK = f"sudo /usr/bin/docker compose -f {GUEST_STACK} up -d"
# The API reads its token once, at startup, so only a recreate picks up a new one.
RESTART_API = f"{START_STACK} --force-recreate api"
_GUIDANCE = {
    "unauthorized": f"The Eggie service needs a restart. Run: {RESTART_API}",
    API_UNCONFIGURED: f"The Eggie service needs a restart. Run: {RESTART_API}",
}


def read_token(path: Path) -> str:
    try:
        token = path.read_text().strip()
    except PermissionError:
        raise EggieError(
            "This user can't reach Eggie: it must be in the docker group. "
            "Run `sudo usermod -aG docker $USER` and start a new session.") from None
    except OSError:
        token = ""
    if not token:
        raise EggieError("Eggie is not set up in this VM yet. "
                          "Run `eggie setup` on your computer.")
    return token


def _api_error(exc: urllib.error.HTTPError) -> ApiError:
    """Every non-2xx body from the API is {"error": {"code", "message"}};
    anything else on this port must still read as a sentence."""
    try:
        error = json.loads(exc.read().decode("utf-8", "replace"))["error"]
        code, message = str(error["code"]), str(error["message"])
    except (OSError, ValueError, KeyError, TypeError):
        return ApiError("http_error", f"The Eggie service answered HTTP "
                                        f"{exc.code} ({exc.reason}).")
    guidance = _GUIDANCE.get(code)
    return ApiError(code, f"{guidance} (the service said: {message})"
                      if guidance else message)


class ApiClient:
    """The same HTTP contract host/client.py speaks, from inside the VM."""

    def __init__(self, token: str, *, base_url: str = f"http://127.0.0.1:{API_PORT}",
                 opener=None, sleep=time.sleep, monotonic=time.monotonic):
        self._token = token
        self._base = base_url
        self._opener = opener or urllib.request.build_opener()
        self._sleep = sleep
        self._monotonic = monotonic

    def _open(self, method: str, path: str, *, payload: dict | None = None,
              timeout: float = REQUEST_TIMEOUT) -> str:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(self._base + path, data=data, method=method)
        request.add_header("Authorization", f"Bearer {self._token}")
        if data is not None:
            request.add_header("Content-Type", "application/json")
        try:
            with self._opener.open(request, timeout=timeout) as response:
                return response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            raise _api_error(e) from None
        except (urllib.error.URLError, OSError):
            raise EggieError("The Eggie service in this VM is not answering. "
                              f"Start it with: {START_STACK}") from None

    def _call(self, method: str, path: str, payload: dict | None = None) -> dict:
        body = self._open(method, path, payload=payload)
        return json.loads(body) if body.strip() else {}

    def _while_busy(self, call):
        """`project_busy`: another operation holds the project's lock."""
        deadline = self._monotonic() + BUSY_RETRY_TIMEOUT
        while True:
            try:
                return call()
            except ApiError as e:
                if e.code != "project_busy" or self._monotonic() >= deadline:
                    raise
            self._sleep(BUSY_RETRY_INTERVAL)

    def _wait(self, job_id: str) -> dict:
        deadline = self._monotonic() + JOB_TIMEOUT
        while True:
            job = self._call("GET", f"/jobs/{job_id}")
            state = job.get("state")
            if state == "done":
                return job
            if state == "failed":
                raise JobFailed(job.get("detail") or "the operation failed inside the VM",
                                job.get("result"))
            if self._monotonic() >= deadline:
                raise EggieError(f"Eggie was still working after "
                                  f"{JOB_TIMEOUT / 60:.0f} minutes. Run "
                                  "`eggie status` to see where it got to.")
            self._sleep(JOB_POLL_INTERVAL)

    def ensure_project(self, project_id: str) -> None:
        try:
            self._call("POST", "/projects", {"id": project_id})
        except ApiError as e:
            if e.code != "project_exists":
                raise

    def project(self, project_id: str) -> dict:
        return self._call("GET", f"/projects/{project_id}")

    def projects(self) -> list[dict]:
        return self._call("GET", "/projects").get("projects", [])

    def up(self, project_id: str) -> dict:
        started = self._while_busy(
            lambda: self._call("POST", f"/projects/{project_id}/up"))
        return self._wait(started["job_id"])

    def down(self, project_id: str) -> dict:
        started = self._while_busy(
            lambda: self._call("POST", f"/projects/{project_id}/down"))
        return self._wait(started["job_id"])

    def logs(self, project_id: str, service: str | None = None) -> str:
        path = f"/projects/{project_id}/logs"
        if service:
            path += f"?service={urllib.parse.quote(service)}"
        return self._open("GET", path, timeout=LOGS_TIMEOUT)

    def secrets(self, project_id: str) -> dict:
        return self._call("GET", f"/projects/{project_id}/secrets")

    def set_secret(self, project_id: str, name: str, value: str) -> None:
        self._call("PUT", f"/projects/{project_id}/secrets/"
                          f"{urllib.parse.quote(name, safe='')}", {"value": value})

    def delete_secret(self, project_id: str, name: str) -> None:
        self._call("DELETE", f"/projects/{project_id}/secrets/"
                             f"{urllib.parse.quote(name, safe='')}")

    def request_secret(self, project_id: str, name: str, hint: str) -> None:
        self._call("PUT", f"/projects/{project_id}/secret-requests/"
                          f"{urllib.parse.quote(name, safe='')}", {"hint": hint})


def default_client() -> ApiClient:
    return ApiClient(read_token(Path(GUEST_TOKEN)))
