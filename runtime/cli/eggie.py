#!/usr/bin/env python3
"""`eggie` inside the VM: turns a folder in ~/projects into a routed project.

Pushed into the guest by the host and installed as /usr/local/bin/eggie, then
run by whichever coding agent the user works in. Stdlib only: it can import
neither host/ nor eggie_api/, so the names it shares with them are declared
again here and held equal by tests/test_constants_agree.py.
"""
from __future__ import annotations

import argparse
import getpass as _getpass
import grp
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, TextIO

API_PORT = 39099
GUEST_ROOT = "/opt/eggie"
GUEST_PROJECTS = f"{GUEST_ROOT}/projects"
GUEST_TOKEN = f"{GUEST_ROOT}/api.token"
GUEST_STACK = f"{GUEST_ROOT}/stack.yml"
COMPOSE_FILE = "docker-compose.yml"
# The code the API answers when it has no usable token of its own.
API_UNCONFIGURED = "api_unconfigured"
# Compose accepts these too; Eggie does not, so a project written under one of
# them must be named, not reported as if it had no compose file at all.
_ALT_COMPOSE_FILES = ("compose.yaml", "compose.yml", "docker-compose.yaml")
# The API container's only credential shared with this VM.
DOCKER_GROUP = "docker"
# Reserved for the setup smoke test; install.verify_step deletes the project
# through the API but leaves its folder behind, so this keeps it out of the
# "Not set up yet" list on every fresh VM.
VERIFY_PROJECT_ID = "eggie-selftest"


class EggieError(Exception):
    """One plain sentence for the user; `main` prints it and exits 1."""


def project_id_for(name: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", name.strip().lower()).strip("-")


def project_of(directory: Path, root: Path) -> Path | None:
    """The project folder holding `directory`, or None outside `root`.

    Resolved first, so ~/projects/x (a symlink) and /opt/eggie/projects/x are
    one folder; a subfolder counts, so `eggie up` from src/ runs the project.
    """
    root = root.resolve()
    path = directory.resolve()
    for candidate in (path, *path.parents):
        if candidate.parent == root:
            return candidate
    return None


def require_id(folder: Path) -> str:
    project_id = project_id_for(folder.name)
    if not project_id:
        raise EggieError(
            f"The folder name '{folder.name}' cannot be a project name. "
            "Rename it using letters, digits and dashes.")
    if project_id != folder.name:
        # The API derives the folder from the slugged id, so any other name
        # would register a different, empty folder.
        raise EggieError(
            f"Rename the folder '{folder.name}' to '{project_id}' first, "
            "then run the command again.")
    return project_id


def docker_gid() -> int:
    try:
        return grp.getgrnam(DOCKER_GROUP).gr_gid
    except KeyError:
        raise EggieError(
            "This VM has no docker group, so Eggie is not set up here. "
            "Run `eggie setup` on your computer.") from None


def prepare_overlay_dir(project: Path, gid: int) -> None:
    """Let the API write .eggie/overlay.yml, the one file it writes here.

    It runs as a non-root member of the docker group, while a coding agent
    working as root leaves directories 755 and files 644.
    """
    eggie_dir = project / ".eggie"
    overlay = eggie_dir / "overlay.yml"
    for path in (eggie_dir, overlay):
        if path.is_symlink():
            raise EggieError(
                f"{path} is a symbolic link; Eggie will not change "
                "permissions through it. Remove the link and run the "
                "command again.")
    try:
        eggie_dir.mkdir(exist_ok=True)
        os.chown(eggie_dir, -1, gid)
        os.chmod(eggie_dir, 0o2775)
        if overlay.exists():
            os.chown(overlay, -1, gid)
            os.chmod(overlay, 0o664)
    except PermissionError as e:
        raise EggieError(
            f"Eggie could not make {eggie_dir} writable for itself "
            f"({e.strerror}). Run the command as the folder's owner.") from None


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


class ApiError(EggieError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class JobFailed(EggieError):
    def __init__(self, message: str, result: dict | None):
        super().__init__(message)
        self.result = result or {}


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


def _default_client() -> ApiClient:
    return ApiClient(read_token(Path(GUEST_TOKEN)))


def _run_git(argv: list[str], env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(argv, env=env, capture_output=True, text=True)


@dataclass
class Env:
    """Everything a command touches besides its arguments, so tests can point
    it at a temporary projects root and an in-process client."""
    root: Path = Path(GUEST_PROJECTS)
    cwd: Path = field(default_factory=Path.cwd)
    client: Callable[[], ApiClient] = _default_client
    gid: Callable[[], int] = docker_gid
    git: Callable[[list[str], dict], subprocess.CompletedProcess] = _run_git
    out: TextIO = field(default_factory=lambda: sys.stdout)
    err: TextIO = field(default_factory=lambda: sys.stderr)
    stdin: TextIO = field(default_factory=lambda: sys.stdin)
    getpass: Callable[[str], str] = _getpass.getpass


def _project_here(env: Env, directory: str | None) -> tuple[Path, str] | None:
    folder = project_of(env.cwd / directory if directory else env.cwd, env.root)
    return (folder, require_id(folder)) if folder else None


def _require_project(env: Env) -> str:
    found = _project_here(env, None)
    if found is None:
        raise EggieError("Run this inside a project folder in "
                          f"~/projects ({GUEST_PROJECTS}).")
    return found[1]


def _alt_compose_file(folder: Path) -> str | None:
    for name in _ALT_COMPOSE_FILES:
        if (folder / name).is_file():
            return name
    return None


def _start(env: Env, folder: Path, project_id: str) -> None:
    if not (folder / COMPOSE_FILE).is_file():
        alt = _alt_compose_file(folder)
        if alt:
            raise EggieError(
                f"This project's compose file is {alt}; Eggie reads only "
                f"{COMPOSE_FILE}. Rename it and run the command again.")
        raise EggieError(f"There is no {COMPOSE_FILE} in {folder}.")
    prepare_overlay_dir(folder, env.gid())
    client = env.client()
    client.ensure_project(project_id)
    print(f"Starting {project_id}…", file=env.out)
    try:
        job = client.up(project_id)
    except JobFailed as e:
        status = e.result.get("status", "failed")
        raise EggieError(f"{project_id} did not start (status: {status}).\n{e}\n"
                          "To see what it printed, run: eggie logs") from None
    result = job.get("result") or {}
    urls = result.get("urls") or []
    for url in urls:
        print(f"  {url}", file=env.out)
    if not urls:
        print(f"{project_id} started. No service is exposed over HTTP.", file=env.out)
    problem = result.get("problem")
    if problem:
        # The containers are up, so this is no failure -- but the URL above
        # will not answer until this is fixed.
        print(problem["message"], file=env.out)


def _print_project(env: Env, project: dict) -> None:
    urls = project.get("urls") or []
    print(f"{project['id']:<20} {project['status']:<16} "
          f"{urls[0] if urls else ''}".rstrip(), file=env.out)
    for url in urls[1:]:
        print(f"    {url}", file=env.out)
    if project.get("problem"):
        print(f"    problem: {project['problem']['message']}", file=env.out)


def cmd_up(env: Env, directory: str | None) -> None:
    found = _project_here(env, directory)
    if found is None:
        raise EggieError(
            f"Projects live in ~/projects ({GUEST_PROJECTS}). Move this folder "
            "there, or start a new one with `eggie new <name>`.")
    _start(env, *found)


def cmd_status(env: Env, directory: str | None) -> None:
    client = env.client()
    found = _project_here(env, directory)
    if found is not None:
        project_id = found[1]
        try:
            _print_project(env, client.project(project_id))
        except ApiError as e:
            if e.code != "project_not_found":
                raise
            print(f"{project_id} is not set up yet. Run `eggie up` in it.",
                  file=env.out)
        return
    projects = client.projects()
    for project in projects:
        _print_project(env, project)
    known = {project["id"] for project in projects}
    waiting = sorted(d.name for d in env.root.iterdir()
                     if d.is_dir() and not d.name.startswith(".")
                     and d.name not in known
                     and d.name != VERIFY_PROJECT_ID) if env.root.is_dir() else []
    if waiting:
        print("Not set up yet (run `eggie up` in each): " + ", ".join(waiting),
              file=env.out)
    elif not projects:
        print("No projects yet.", file=env.out)


def cmd_logs(env: Env, service: str | None) -> None:
    print(env.client().logs(_require_project(env), service), end="", file=env.out)


def cmd_down(env: Env) -> None:
    project_id = _require_project(env)
    env.client().down(project_id)
    print(f"{project_id} stopped.", file=env.out)


_APPLY_HINT = "Run `eggie up` to apply it."


def cmd_secret_set(env: Env, name: str) -> None:
    project_id = _require_project(env)
    # Never from argv: that ends up in shell history and `ps`.
    if env.stdin.isatty():
        value = env.getpass(f"Value for {name} (not shown): ")
    else:
        value = env.stdin.read().removesuffix("\n")
    if value == "":
        raise EggieError(f"No value given for {name}; nothing was saved.")
    client = env.client()
    client.set_secret(project_id, name, value)
    print(f"Saved {name} for {project_id}.", file=env.out)
    if client.secrets(project_id).get("restart_needed"):
        print(_APPLY_HINT, file=env.out)


def cmd_secret_list(env: Env) -> None:
    project_id = _require_project(env)
    data = env.client().secrets(project_id)
    names = [s["name"] for s in data.get("secrets", [])]
    for name in names:
        print(name, file=env.out)
    missing = data.get("missing") or []
    if missing:
        print("Needs a value: " + ", ".join(missing), file=env.out)
    count = len(data.get("defaults") or [])
    if count:
        print(f"{count} defaults from .env.example; change them on the "
              "project's Secrets page in Eggie.", file=env.out)
    if not names and not missing and not count:
        print(f"{project_id} has no secrets.", file=env.out)
    if data.get("dotenv"):
        print("This project has a .env file; move it into secrets on the "
              "project's Secrets page in Eggie.", file=env.out)
    if data.get("restart_needed"):
        print("Run `eggie up` to apply the changes.", file=env.out)


def cmd_secret_rm(env: Env, name: str) -> None:
    project_id = _require_project(env)
    env.client().delete_secret(project_id, name)
    print(f"Removed {name} from {project_id}.", file=env.out)


def repo_name(url: str) -> str:
    """The last path segment without `.git`, for https and scp-style URLs alike."""
    return re.split(r"[/:]", url.rstrip("/"))[-1].removesuffix(".git")


def _new_folder(env: Env, name: str) -> Path:
    project_id = project_id_for(name)
    if not project_id:
        raise EggieError(f"'{name}' cannot be a project name. "
                          "Use letters, digits and dashes.")
    folder = env.root / project_id
    if folder.exists():
        raise EggieError(f"{folder} already exists. Pick another name, "
                          "or run `eggie up` in it.")
    return folder


def cmd_new(env: Env, name: str) -> None:
    # Reads the token first, so a user without docker-group access gets that
    # sentence instead of an empty folder no API call can ever use.
    env.client()
    folder = _new_folder(env, name)
    try:
        folder.mkdir()
    except OSError as e:
        raise EggieError(
            f"Eggie could not create {folder} ({e.strerror}).") from None
    print(f"Created {folder}. Put the project's files there, "
          "then run `eggie up` in it.", file=env.out)


def cmd_clone(env: Env, url: str, name: str | None) -> None:
    # Same reason as cmd_new: fail on the docker-group sentence before git
    # ever runs, rather than have git's own permission error read as "private".
    env.client()
    folder = _new_folder(env, name or repo_name(url))
    # A coding agent's shell has no terminal to answer a credential prompt,
    # so a private repository must fail instead of hanging.
    result = env.git(["git", "clone", "--", url, str(folder)],
                     {**os.environ, "GIT_TERMINAL_PROMPT": "0"})
    if result.returncode != 0:
        raise EggieError(f"Could not download {url}:\n{(result.stderr or '').strip()}")
    if (folder / COMPOSE_FILE).is_file() or _alt_compose_file(folder):
        _start(env, folder, folder.name)
    else:
        print(f"Downloaded to {folder}. It has no {COMPOSE_FILE} yet; one must "
              "be written before `eggie up`.", file=env.out)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="eggie",
        description="Run projects in this VM behind Eggie's router. Projects "
                    "live in ~/projects, one folder each. Start them only with "
                    "`eggie up`, never `docker compose up`.")
    sub = parser.add_subparsers(dest="command", required=True, metavar="<command>")
    up = sub.add_parser("up", help="start or restart the project in this folder "
                                   "and print its URL")
    up.add_argument("directory", nargs="?")
    status = sub.add_parser("status", help="show this project, or every project")
    status.add_argument("directory", nargs="?")
    logs = sub.add_parser("logs", help="show what this project's containers printed")
    logs.add_argument("service", nargs="?")
    sub.add_parser("down", help="stop the project in this folder")
    new = sub.add_parser("new", help="create an empty project folder in ~/projects")
    new.add_argument("name")
    clone = sub.add_parser("clone", help="download a git repository into "
                                         "~/projects and start it")
    clone.add_argument("url")
    clone.add_argument("name", nargs="?")
    secret = sub.add_parser("secret", help="keys and passwords this project's "
                                           "containers get as environment variables")
    secret_sub = secret.add_subparsers(dest="secret_command", required=True,
                                       metavar="<action>")
    secret_set = secret_sub.add_parser(
        "set", help="save a secret; the value is read from the terminal or stdin")
    secret_set.add_argument("name")
    secret_sub.add_parser("list", help="show secret names and the ones still missing")
    secret_rm = secret_sub.add_parser("rm", help="remove a secret")
    secret_rm.add_argument("name")
    return parser


def main(argv: list[str] | None = None, env: Env | None = None) -> int:
    args = _parser().parse_args(argv)
    env = env or Env()
    commands = {
        "up": lambda: cmd_up(env, args.directory),
        "status": lambda: cmd_status(env, args.directory),
        "logs": lambda: cmd_logs(env, args.service),
        "down": lambda: cmd_down(env),
        "new": lambda: cmd_new(env, args.name),
        "clone": lambda: cmd_clone(env, args.url, args.name),
        "secret": lambda: {
            "set": lambda: cmd_secret_set(env, args.name),
            "list": lambda: cmd_secret_list(env),
            "rm": lambda: cmd_secret_rm(env, args.name),
        }[args.secret_command](),
    }
    try:
        commands[args.command]()
    except EggieError as e:
        print(str(e), file=env.err)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
