from __future__ import annotations

import logging
import shutil
from dataclasses import asdict
from pathlib import Path
from typing import Callable

import yaml

from .. import constants
from ..config import ApiConfig
from ..domain.project import _slug
from ..errors import Conflict, EggieError, Invalid, NotFound
from ..infra import docker, files
from ..infra.health import answers
from ..infra.reconcile import discover, examine
from ..infra.repos.projects import ProjectRepo
from .jobs import JobRegistry
from .loader import ProjectLoader
from .locks import ProjectLocks
from .public import Public
from .secrets import SecretService
from .uploads import UploadService

log = logging.getLogger("eggie.api")


class ProjectService:
    def __init__(self, config: ApiConfig, runner, loader: ProjectLoader,
                 projects: ProjectRepo, secrets: SecretService, jobs: JobRegistry,
                 locks: ProjectLocks, uploads: UploadService, public: Public,
                 http_probe, wake: Callable[[], None]):
        self._config = config
        self._runner = runner
        self._loader = loader
        self._projects = projects
        self._secrets = secrets
        self._jobs = jobs
        self._locks = locks
        self._uploads = uploads
        self._public = public
        self._probe = http_probe
        self._wake = wake

    def payload(self, row: dict, *, recheck: bool = False) -> dict:
        # A project with broken or missing files still has a status, and one
        # broken project must never take the whole listing down with it.
        problem = None
        urls: list[str] = []
        web: list[dict] = []
        project = None
        folder = self._loader.dir(row["id"])
        if not folder.is_dir():
            problem = {"code": "folder_missing",
                       "message": "this project's folder is gone"}
        else:
            try:
                project = self._loader.load(row["id"])
                urls = self._loader.urls_for(project, row["domain"])
                web = [{"url": url, "service": spec.service, "primary": index == 0}
                       for index, (url, spec) in enumerate(zip(urls, project.webs))]
            except EggieError as e:
                problem = {"code": e.code, "message": e.message}
        if problem is None and row.get("problem_code"):
            # A file that will not parse outranks a routing fault: it is why
            # the project has no URLs to be unreachable on.
            problem = {"code": row["problem_code"],
                       "message": row["problem_message"]}
            # One request with a short timeout, never the readiness window:
            # this runs inside a read the CLI is waiting on.
            if recheck and project is not None and answers(
                    project, row["domain"], edge_port=self._config.edge_port,
                    traefik_host=self._config.traefik_host, http_probe=self._probe):
                # An entrypoint slower than the readiness window stores a
                # diagnosis that is true for a minute and false forever after.
                self._projects.set_problem(row["id"])
                problem = None
        active = self._jobs.active_for(row["id"])
        try:
            public_status = self._public.status(row["id"])
        except Exception:
            # A broken public-URL row must not take the whole listing down
            # with it; the rest of the project's status is still good.
            log.exception("reading the public URL status of %s failed", row["id"])
            public_status = {"state": "off", "note": None}
        return {"id": row["id"], "status": row["status"], "domain": row["domain"],
                "path": row["guest_path"], "urls": urls, "problem": problem,
                "empty": folder.is_dir() and not (folder / constants.COMPOSE_FILE).exists(),
                "web": web,
                "public": public_status,
                "first_run": row.get("last_started_at") is None,
                "restart_needed": self._secrets.restart_needed(row),
                "secrets_requested": self._secrets.requested_count(row["id"]),
                "job": None if active is None else {
                    "id": active.id, "kind": active.kind,
                    "phase": active.phase, "started_at": active.started_at}}

    def create(self, id: str, web: list[dict] | None, domain: str | None) -> dict:
        # Same slug rule load_project applies to a directory name, so an id
        # survives the round trip host -> API -> compose project name.
        project_id = _slug(id)
        if not project_id:
            raise Invalid("invalid_project",
                          f"'{id}' is not a usable project id")
        if self._projects.get(project_id) is not None:
            raise Conflict("project_exists",
                           f"project '{project_id}' already exists")

        d = self._loader.dir(project_id)
        d.mkdir(parents=True, exist_ok=True)
        if web:
            (d / ".eggie").mkdir(exist_ok=True)
            (d / ".eggie" / "project.yml").write_text(yaml.safe_dump(
                {"id": project_id, "web": web}, sort_keys=False))
        self._projects.add(project_id, str(d), domain or self._config.domain)
        self._wake()
        return self.payload(self._projects.get(project_id))

    def adopt(self, project_id: str) -> dict:
        if self._projects.get(project_id) is not None:
            raise Conflict("project_exists",
                           f"project '{project_id}' already exists")
        folder = self._loader.dir(project_id)
        if not folder.is_dir():
            raise NotFound("folder_not_found",
                           f"no folder '{project_id}' in the projects folder")
        found = examine(folder)
        if not found.adoptable:
            raise Conflict("not_adoptable",
                           f"'{project_id}' cannot be adopted: {found.reason}")
        self._projects.add(project_id, str(folder), self._config.domain)
        self._wake()
        return self.payload(self._projects.get(project_id))

    def list(self) -> dict:
        # `eggie status` is the surface users actually read, so a stale
        # diagnosis has to clear here too. payload() only probes a row that
        # carries a stored problem -- normally none -- so an ordinary listing
        # still pays no round trips at all.
        rows = self._projects.list()
        known = {row["id"] for row in rows}
        return {"projects": [self.payload(row, recheck=True) for row in rows],
                "discovered": [asdict(d) for d in
                               discover(Path(self._config.projects_root), known)]}

    def get(self, project_id: str) -> dict:
        return self.payload(self._loader.require(project_id), recheck=True)

    def _compose_name(self, project_id: str, row: dict) -> str:
        return docker.resolve_compose_name(
            self._runner, self._loader.dir(project_id),
            row.get("compose_name") or row["id"])

    def delete_preview(self, project_id: str) -> dict:
        row = self._loader.require(project_id)
        return {**files.tree_stats(self._loader.dir(project_id)),
                **docker.project_resources(
                    self._runner, self._compose_name(project_id, row))}

    def delete(self, project_id: str, *, purge: bool) -> dict:
        row = self._loader.require(project_id)
        folder = self._loader.dir(project_id)
        # Synchronous: the host CLI, install verification and the desktop's
        # replace-import all wait on this answer. Removal is by compose label,
        # not `compose down`, so a broken compose file can never block it.
        with self._locks.held(project_id):
            self._public.disable(project_id, force=True)
            result = docker.remove_by_label(
                self._runner, self._compose_name(project_id, row), volumes=purge)
            if purge:
                self._uploads.drop_project(project_id)
                self._secrets.drop(project_id)
            if purge and folder.exists():
                shutil.rmtree(folder, ignore_errors=True)
                if folder.exists():
                    removed = docker.remove_tree_as_root(self._runner, folder)
                    if not removed.ok and result.ok:
                        result = removed
            self._projects.remove(project_id)
            self._wake()
        return {"id": project_id, "stopped": result.ok,
                "detail": "" if result.ok else (result.stderr or result.stdout).strip()}
