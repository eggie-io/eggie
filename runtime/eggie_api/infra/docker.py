from __future__ import annotations

import base64
import json
import os
import re

# Absolute path: Docker Desktop's WSL integration puts its own docker CLI on
# PATH, and a bare `docker` would send this VM's projects to Desktop's engine.
DOCKER = "/usr/bin/docker"

from ..constants import COMPOSE_FILE
from .runner import Completed
from ..domain.project import Project, FAILED_TO_START, STARTED_OK, classify, overlay_yaml

PROJECT_LABEL = "com.docker.compose.project"

# Every path here comes from the caller's project directory, never from
# constants: the API writes uploads to `config.projects_root`, and a second
# source of truth would upload into one directory and run compose against
# another, silently. The directory is valid on both sides because /opt/eggie
# is bind-mounted into the API at the identical path.


def _compose_argv(directory) -> list[str]:
    return [DOCKER, "compose",
            "-f", f"{directory}/{COMPOSE_FILE}",
            "-f", f"{directory}/.eggie/overlay.yml",
            "up", "-d"]


def _write_overlay(provider, project: Project, directory, domain: str, *,
                   services=(), secret_names=(), declared=None):
    text = overlay_yaml(project, domain, services=services,
                        secret_names=secret_names, declared=declared)
    encoded = base64.b64encode(text.encode("utf-8")).decode("ascii")
    return provider.exec(["bash", "-lc",
                          f"mkdir -p {directory}/.eggie && echo {encoded} | "
                          f"base64 -d > {directory}/.eggie/overlay.yml"],
                         root=True)


def compose_up(provider, project: Project, directory, domain: str, *,
              on_phase=None, services=(),
              secrets: dict[str, str] | None = None, declared=None):
    """Returns (status, detail). `detail` carries the guest's own output when
    the stack did not start, so callers never have to report a bare status code
    that no one can act on. URLs are the API layer's job -- it is the only place
    that holds the configured edge port.

    `on_phase`, when given, is called with "starting" once the overlay is
    written and before `docker compose up` itself -- the caller's phase
    report has to follow the overlay write, not precede it, or a failed
    write would be reported as "starting" a stack that never did.

    `secrets` reach compose only through its environment; the overlay names
    them."""
    written = _write_overlay(provider, project, directory, domain,
                             services=services,
                             secret_names=list(secrets or {}),
                             declared=declared)
    if not written.ok:
        # exec() never raises. Starting the stack anyway would produce a project
        # with no Traefik labels: no route, and no error naming the cause.
        return (FAILED_TO_START,
                (written.stderr or written.stdout).strip()
                or "could not write the Traefik overlay inside the VM")
    if on_phase:
        on_phase("starting")
    env = dict(secrets) if secrets else None
    up = provider.exec(_compose_argv(directory), root=True, env=env)
    ps = provider.exec([DOCKER, "compose", "-f",
                        f"{directory}/{COMPOSE_FILE}",
                        "ps", "--format", "json"], root=True, env=env)
    status = classify(up, ps.stdout)
    detail = "" if status == STARTED_OK else (up.stderr or up.stdout or ps.stderr).strip()
    return status, detail


# Every compose command interpolates the user's file, so a `${KEY:?}` fails
# it unless `env` carries the project's secrets -- not just `up`.
def compose_down(provider, directory, *, env: dict | None = None):
    return provider.exec([DOCKER, "compose", "-f", f"{directory}/{COMPOSE_FILE}",
                          "-f", f"{directory}/.eggie/overlay.yml", "down"],
                         root=True, env=env)


def _labelled(runner, kind: list[str], name: str, fmt: str) -> list[str]:
    result = runner.exec([DOCKER, *kind, "--filter",
                          f"label={PROJECT_LABEL}={name}", "--format", fmt],
                         root=True)
    return result.stdout.split() if result.ok else []


WORKING_DIR_LABEL = "com.docker.compose.project.working_dir"


def resolve_compose_name(runner, directory, fallback: str) -> str:
    """The name compose actually stamped on this project's containers, not
    what a compose file's `name:` says today -- the two can disagree after
    the file changes post-start, and delete must find what is running."""
    result = runner.exec([DOCKER, "ps", "-a", "--filter",
                          f"label={WORKING_DIR_LABEL}={directory}", "--format",
                          '{{.Label "com.docker.compose.project"}}'], root=True)
    if result.ok:
        for line in result.stdout.splitlines():
            if line.strip():
                return line.strip()
    return fallback


def project_resources(runner, name: str) -> dict:
    return {"containers": _labelled(runner, ["ps", "-a"], name, "{{.Names}}"),
            "volumes": _labelled(runner, ["volume", "ls"], name, "{{.Name}}")}


def remove_by_label(runner, name: str, *, volumes: bool) -> Completed:
    """Compose-free teardown: the labels compose stamped on everything it
    created outlive a compose file that no longer parses. Every step runs
    regardless of an earlier one failing, so a stuck network never stops the
    volumes from being freed too; the first failure is what's returned."""
    failure = None

    def run(remove: list[str], ids: list[str]):
        nonlocal failure
        if not ids:
            return
        result = runner.exec([DOCKER, *remove, *ids], root=True)
        if not result.ok and failure is None:
            failure = result

    container_ids = _labelled(runner, ["ps", "-a"], name, "{{.ID}}")
    # Plain DELETE must not SIGKILL a container mid-write; `rm -f` alone does.
    run(["stop"], container_ids)
    run(["rm", "-f"], container_ids)
    run(["network", "rm"], _labelled(runner, ["network", "ls"], name, "{{.ID}}"))
    if volumes:
        run(["volume", "rm", "-f"],
            _labelled(runner, ["volume", "ls"], name, "{{.Name}}"))
    return failure or Completed(0, "", "")


def inspect_own_image(runner) -> Completed:
    return runner.exec([DOCKER, "inspect", "--format", "{{.Config.Image}}",
                        os.environ.get("HOSTNAME", "")], root=True)


def own_image(runner) -> str | None:
    """The image this API runs from, already on the VM so no pull is needed."""
    result = inspect_own_image(runner)
    return result.stdout.strip() if result.ok else None


# Docker prints sizes in decimal units (go-units HumanSize).
_SIZE_UNITS = {"B": 1, "kB": 10**3, "KB": 10**3, "MB": 10**6, "GB": 10**9,
               "TB": 10**12, "PB": 10**15}


def volume_sizes(runner) -> dict[str, int]:
    """Bytes per volume name; empty when docker cannot say."""
    result = runner.exec([DOCKER, "system", "df", "-v", "--format", "json"], root=True)
    if not result.ok:
        return {}
    try:
        rows = json.loads(result.stdout).get("Volumes") or []
    except (ValueError, AttributeError):
        return {}
    sizes = {}
    for row in rows:
        match = re.fullmatch(r"([\d.]+)\s*([kKMGTP]?B)", str(row.get("Size", "")).strip())
        if match and row.get("Name"):
            sizes[row["Name"]] = int(float(match[1]) * _SIZE_UNITS[match[2]])
    return sizes


def volume_names(runner) -> set[str] | None:
    """None when docker cannot say, so a caller never mistakes that for no volumes."""
    result = runner.exec([DOCKER, "volume", "ls", "--format", "{{.Name}}"], root=True)
    if not result.ok:
        return None
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


def remove_tree_as_root(runner, path) -> Completed:
    """The API runs as uid 1000, and containers write root-owned files into
    bind-mounted project folders. Removes `path` from a throwaway container of
    this API's own image running as root."""
    found = inspect_own_image(runner)
    if not found.ok:
        return found
    parent = str(os.path.dirname(str(path)))
    return runner.exec([DOCKER, "run", "--rm", "--user", "0",
                        "-v", f"{parent}:{parent}", "--entrypoint", "",
                        found.stdout.strip(), "rm", "-rf", str(path)], root=True)


def move_as_root(runner, src, dst) -> Completed:
    """rename(2) of a folder into another parent needs write access to the
    folder itself; a database's data folder is 0700 and owned by its user."""
    found = inspect_own_image(runner)
    if not found.ok:
        return found
    # One mount over both paths keeps it a rename, not a copy across mounts.
    root = os.path.commonpath([str(src), str(dst)])
    return runner.exec([DOCKER, "run", "--rm", "--user", "0",
                        "-v", f"{root}:{root}", "--entrypoint", "",
                        found.stdout.strip(), "mv", "-T", str(src), str(dst)], root=True)


def create_volume(runner, name: str, *, project: str, key: str) -> Completed:
    # Compose's own labels, so compose adopts the volume and delete finds it.
    return runner.exec([DOCKER, "volume", "create",
                        "--label", f"{PROJECT_LABEL}={project}",
                        "--label", f"com.docker.compose.volume={key}", name], root=True)


def remove_volume(runner, name: str) -> Completed:
    return runner.exec([DOCKER, "volume", "rm", "-f", name], root=True)


def container_id(provider, directory, service: str, *,
                 env: dict | None = None) -> str:
    """Empty string when compose cannot resolve one — the project was never
    started, or the container is already gone. Callers hedge, they don't raise."""
    result = provider.exec([DOCKER, "compose", "-f",
                            f"{directory}/{COMPOSE_FILE}",
                            "ps", "-q", service], root=True, env=env)
    lines = result.stdout.split() if result.ok else []
    return lines[0] if lines else ""


def logs_argv(directory, service: str | None = None, *,
              follow: bool = False) -> list[str]:
    argv = [DOCKER, "compose", "-f", f"{directory}/{COMPOSE_FILE}",
            "logs", "--no-color"]
    if follow:
        argv.append("--follow")
    if service:
        argv.append(service)
    return argv


def project_logs(provider, directory, service: str | None = None, *,
                 env: dict | None = None):
    """Returns the `Completed`, not its stdout: compose exits non-zero when the
    project was never created or the daemon is down, and dropping that turned a
    real failure into an empty log listing."""
    return provider.exec(logs_argv(directory, service), root=True, env=env)
