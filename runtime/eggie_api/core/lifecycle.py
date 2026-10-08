from __future__ import annotations

import base64
import os

# Absolute path: Docker Desktop's WSL integration puts its own docker CLI on
# PATH, and a bare `docker` would send this VM's projects to Desktop's engine.
DOCKER = "/usr/bin/docker"

from .constants import COMPOSE_FILE
from .exec import Completed
from .project import Project, FAILED_TO_START, STARTED_OK, classify, overlay_yaml

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
                   services=(), secret_names=()):
    text = overlay_yaml(project, domain, services=services,
                        secret_names=secret_names)
    encoded = base64.b64encode(text.encode("utf-8")).decode("ascii")
    return provider.exec(["bash", "-lc",
                          f"mkdir -p {directory}/.eggie && echo {encoded} | "
                          f"base64 -d > {directory}/.eggie/overlay.yml"],
                         root=True)


def compose_up(provider, project: Project, directory, domain: str, *,
              on_phase=None, services=(),
              secrets: dict[str, str] | None = None):
    """Returns (status, detail). `detail` carries the guest's own output when
    the stack did not start, so callers never have to report a bare status code
    that no one can act on. URLs are the API layer's job -- it is the only place
    that holds the configured edge port.

    `on_phase`, when given, is called with "starting" once the overlay is
    written and before `docker compose up` itself -- the caller's phase
    report has to follow the overlay write, not precede it, or a failed
    write would be reported as "starting" a stack that never did.

    `secrets` reach compose only through the environment of `up`; the
    overlay names them."""
    written = _write_overlay(provider, project, directory, domain,
                             services=services,
                             secret_names=list(secrets or {}))
    if not written.ok:
        # exec() never raises. Starting the stack anyway would produce a project
        # with no Traefik labels: no route, and no error naming the cause.
        return (FAILED_TO_START,
                (written.stderr or written.stdout).strip()
                or "could not write the Traefik overlay inside the VM")
    if on_phase:
        on_phase("starting")
    up = provider.exec(_compose_argv(directory), root=True,
                       env=dict(secrets) if secrets else None)
    ps = provider.exec([DOCKER, "compose", "-f",
                        f"{directory}/{COMPOSE_FILE}",
                        "ps", "--format", "json"], root=True)
    status = classify(up, ps.stdout)
    detail = "" if status == STARTED_OK else (up.stderr or up.stdout or ps.stderr).strip()
    return status, detail


def compose_down(provider, directory):
    return provider.exec([DOCKER, "compose", "-f", f"{directory}/{COMPOSE_FILE}",
                          "-f", f"{directory}/.eggie/overlay.yml", "down"],
                         root=True)


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


def remove_tree_as_root(runner, path) -> Completed:
    """The API runs as uid 1000, and containers write root-owned files into
    bind-mounted project folders. Removes `path` from a throwaway container of
    this API's own image (already on the VM, so no pull) running as root."""
    me = os.environ.get("HOSTNAME", "")
    image = runner.exec([DOCKER, "inspect", "--format", "{{.Config.Image}}", me],
                        root=True)
    if not image.ok:
        return image
    parent = str(os.path.dirname(str(path)))
    return runner.exec([DOCKER, "run", "--rm", "--user", "0",
                        "-v", f"{parent}:{parent}", "--entrypoint", "",
                        image.stdout.strip(), "rm", "-rf", str(path)], root=True)


def container_id(provider, directory, service: str) -> str:
    """Empty string when compose cannot resolve one — the project was never
    started, or the container is already gone. Callers hedge, they don't raise."""
    result = provider.exec([DOCKER, "compose", "-f",
                            f"{directory}/{COMPOSE_FILE}",
                            "ps", "-q", service], root=True)
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


def project_logs(provider, directory, service: str | None = None):
    """Returns the `Completed`, not its stdout: compose exits non-zero when the
    project was never created or the daemon is down, and dropping that turned a
    real failure into an empty log listing."""
    return provider.exec(logs_argv(directory, service), root=True)
