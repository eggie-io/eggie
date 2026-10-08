from __future__ import annotations

import os
from pathlib import Path

from .constants import COMPOSE_FILE, GUEST_PROJECTS, VERIFY_PROJECT_ID
from .env import Env, project_here, require_project
from .errors import ApiError, EggieError, JobFailed
from .project import alt_compose_file, prepare_overlay_dir, project_id_for, repo_name


def start(env: Env, folder: Path, project_id: str) -> None:
    if not (folder / COMPOSE_FILE).is_file():
        alt = alt_compose_file(folder)
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


def print_project(env: Env, project: dict) -> None:
    urls = project.get("urls") or []
    print(f"{project['id']:<20} {project['status']:<16} "
          f"{urls[0] if urls else ''}".rstrip(), file=env.out)
    for url in urls[1:]:
        print(f"    {url}", file=env.out)
    if project.get("problem"):
        print(f"    problem: {project['problem']['message']}", file=env.out)


def cmd_up(env: Env, directory: str | None) -> None:
    found = project_here(env, directory)
    if found is None:
        raise EggieError(
            f"Projects live in ~/projects ({GUEST_PROJECTS}). Move this folder "
            "there, or start a new one with `eggie new <name>`.")
    start(env, *found)


def cmd_status(env: Env, directory: str | None) -> None:
    client = env.client()
    found = project_here(env, directory)
    if found is not None:
        project_id = found[1]
        try:
            print_project(env, client.project(project_id))
        except ApiError as e:
            if e.code != "project_not_found":
                raise
            print(f"{project_id} is not set up yet. Run `eggie up` in it.",
                  file=env.out)
        return
    projects = client.projects()
    for project in projects:
        print_project(env, project)
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
    print(env.client().logs(require_project(env), service), end="", file=env.out)


def cmd_down(env: Env) -> None:
    project_id = require_project(env)
    env.client().down(project_id)
    print(f"{project_id} stopped.", file=env.out)


def new_folder(env: Env, name: str) -> Path:
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
    folder = new_folder(env, name)
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
    folder = new_folder(env, name or repo_name(url))
    # A coding agent's shell has no terminal to answer a credential prompt,
    # so a private repository must fail instead of hanging.
    result = env.git(["git", "clone", "--", url, str(folder)],
                     {**os.environ, "GIT_TERMINAL_PROMPT": "0"})
    if result.returncode != 0:
        raise EggieError(f"Could not download {url}:\n{(result.stderr or '').strip()}")
    if (folder / COMPOSE_FILE).is_file() or alt_compose_file(folder):
        start(env, folder, folder.name)
    else:
        print(f"Downloaded to {folder}. It has no {COMPOSE_FILE} yet; one must "
              "be written before `eggie up`.", file=env.out)
