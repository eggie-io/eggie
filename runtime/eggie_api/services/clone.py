from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path
from typing import Callable

from .. import constants
from ..config import ApiConfig
from ..domain.project import _slug
from ..errors import Conflict, EggieError, Invalid
from ..infra.github import auth_failed, clone_argv, redact, valid_repo
from ..infra.repos.projects import ProjectRepo
from .github_link import GitHubLink
from .jobs import JobFailed
from .lifecycle import LifecycleService
from .loader import ProjectLoader
from .locks import ProjectLocks


class CloneService:
    def __init__(self, config: ApiConfig, runner, loader: ProjectLoader,
                 projects: ProjectRepo, locks: ProjectLocks,
                 github_link: GitHubLink, lifecycle: LifecycleService,
                 wake: Callable[[], None]):
        self._config = config
        self._runner = runner
        self._loader = loader
        self._projects = projects
        self._locks = locks
        self._github_link = github_link
        self._lifecycle = lifecycle
        self._wake = wake

    def clone(self, repo: str, id: str | None) -> dict:
        if not valid_repo(repo):
            raise Invalid("invalid_repo",
                          f"'{repo}' is not an owner/name repository")
        project_id = _slug(id or repo.split("/")[1])
        if not project_id:
            raise Invalid("invalid_project",
                          f"'{repo}' has no usable project name")
        directory = self._loader.dir(project_id)
        if self._projects.get(project_id) is not None or directory.exists():
            raise Conflict("project_exists",
                           f"project '{project_id}' already exists")
        token = self._github_link.require_token()

        def work(write):
            handed_over = False
            # Cloned next to the real folder, not into it: nothing here holds
            # `directory`'s name reserved while git runs, so another request
            # (POST /projects, an adopt, a coding agent's own mkdir) can claim
            # it first. Cloning into a dot-prefixed staging dir -- which
            # `reconcile.discover` already skips -- and renaming in only once
            # the name is still free means a failure can never touch a folder
            # this job did not create.
            staging = Path(self._config.projects_root) / f".clone-{uuid.uuid4().hex[:12]}"
            try:
                write.phase("cloning")
                write(f"git clone https://github.com/{repo}.git\n")
                result = self._runner.exec(clone_argv(repo, staging),
                                           env={"EGGIE_GH_TOKEN": token,
                                                "GIT_TERMINAL_PROMPT": "0"})
                if not result.ok:
                    output = redact((result.stderr or result.stdout).strip(), token)
                    shutil.rmtree(staging, ignore_errors=True)
                    if auth_failed(output):
                        self._github_link.confirm_bad(token)
                    raise JobFailed(output or "git clone failed")
                if directory.exists() or self._projects.get(project_id) is not None:
                    shutil.rmtree(staging, ignore_errors=True)
                    raise JobFailed(f"a project called '{project_id}' appeared "
                                    "while downloading; nothing was changed")
                os.rename(staging, directory)
                self._projects.add(project_id, str(directory), self._config.domain)
                self._wake()
                if not (directory / constants.COMPOSE_FILE).exists():
                    write(f"no {constants.COMPOSE_FILE} yet; left stopped\n")
                    return {"id": project_id, "status": "stopped"}
                try:
                    up = self._lifecycle.start_work(project_id, stop_first=False)
                except EggieError as e:
                    write(f"{e.message}\n")
                    return {"id": project_id, "status": "stopped"}
                # start_work's job releases the lock in its own finally.
                handed_over = True
                return {"id": project_id, **up(write)}
            finally:
                if not handed_over:
                    self._locks.release(project_id)

        job_id = self._lifecycle.submit_locked(project_id, work, "clone")
        return {"job_id": job_id, "id": project_id}
