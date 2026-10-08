from __future__ import annotations

import logging
import time
from typing import Iterator

from ..config import ApiConfig
from ..domain.project import STARTED_OK
from ..errors import Conflict, EggieError
from ..infra import docker
from ..infra.health import diagnose
from ..infra.repos.projects import ProjectRepo
from .jobs import JobFailed, JobRegistry
from .loader import ProjectLoader
from .locks import ProjectLocks
from .secrets import RUNNING, SecretService

log = logging.getLogger("eggie.api")


class LifecycleService:
    def __init__(self, config: ApiConfig, runner, loader: ProjectLoader,
                 projects: ProjectRepo, secrets: SecretService, jobs: JobRegistry,
                 locks: ProjectLocks, http_probe):
        self._config = config
        self._runner = runner
        self._loader = loader
        self._projects = projects
        self._secrets = secrets
        self._jobs = jobs
        self._locks = locks
        self._probe = http_probe

    def submit_locked(self, project_id: str, work, kind: str | None = None) -> str:
        """The job releases the lock itself, in its own `finally`."""
        self._locks.acquire_or_raise(project_id)
        try:
            return self._jobs.submit(work, kind=kind, project_id=project_id)
        except BaseException:
            self._locks.release(project_id)
            raise

    def start_work(self, project_id: str, *, stop_first: bool):
        row = self._loader.require(project_id)
        # Parsing happens here, not in the job, so a broken compose file comes
        # back as an error code the caller can read instead of a failed job.
        project = self._loader.load(project_id)
        compose = self._loader.compose(project_id)
        services, declared_by = self._secrets.declared(compose)
        name = str(compose.get("name") or project_id)
        domain = row["domain"]
        directory = self._loader.dir(project_id)

        def work(write):
            try:
                if stop_first:
                    write(f"compose down {project_id}\n")
                    down_result = docker.compose_down(
                        self._runner, directory, env=self._secrets.values(project_id))
                    if not down_result.ok:
                        raise JobFailed(
                            (down_result.stderr or down_result.stdout).strip()
                            or "compose down failed")
                # Recorded before compose runs, not after a successful start:
                # delete must be able to find these containers by name even
                # when `up` never reaches STARTED_OK.
                self._projects.set_compose_name(project_id, name)
                write(f"compose up {project_id}\n")
                # Stamped before the values are read, so a secret changed while
                # compose runs still shows as needing a restart.
                began = time.time()
                values = self._secrets.values(project_id) or {}
                status, detail = docker.compose_up(
                    self._runner, project, directory, domain, on_phase=write.phase,
                    services=services, secrets=values, declared=declared_by)
                diagnosis = None
                if status in RUNNING:
                    # A crash-looping stack still got these values.
                    self._projects.mark_started(project_id, began)
                if status == STARTED_OK:
                    write.phase("checking")
                    write("waiting for the project to answer through Traefik\n")
                    diagnosis = diagnose(
                        self._runner, project, domain, directory=directory,
                        env=values or None,
                        edge_port=self._config.edge_port,
                        traefik_host=self._config.traefik_host,
                        http_probe=self._probe,
                        timeout=self._config.ready_timeout)
                self._projects.set_status(project_id, status)
                if diagnosis is None:
                    self._projects.set_problem(project_id)
                else:
                    self._projects.set_problem(project_id, diagnosis.code,
                                               diagnosis.message)
                result = {"status": status,
                          "urls": self._loader.urls_for(project, domain),
                          "problem": diagnosis.as_dict() if diagnosis else None}
                write(f"status: {status}\n")
                if diagnosis:
                    # The containers did start, so the job succeeds; the reason
                    # the URL will not answer belongs in its log all the same.
                    write(f"{diagnosis.message}\n")
                if detail:
                    write(f"{detail}\n")
                if status != STARTED_OK:
                    raise JobFailed(
                        detail or f"containers did not stay up (status: {status})",
                        result=result)
                return result
            finally:
                self._locks.release(project_id)

        return work

    def start(self, project_id: str, *, stop_first: bool = False) -> str:
        return self.submit_locked(project_id,
                                  self.start_work(project_id, stop_first=stop_first),
                                  "restart" if stop_first else "up")

    def stop(self, project_id: str) -> str:
        self._loader.require(project_id)

        def work(write):
            try:
                write.phase("stopping")
                write(f"compose down {project_id}\n")
                result = docker.compose_down(self._runner,
                                             self._loader.dir(project_id),
                                             env=self._secrets.values(project_id))
                if not result.ok:
                    raise JobFailed((result.stderr or result.stdout).strip()
                                    or "compose down failed")
                self._projects.set_status(project_id, "stopped")
                return {"status": "stopped"}
            finally:
                self._locks.release(project_id)

        return self.submit_locked(project_id, work, "down")

    def resume_all(self) -> list[str]:
        """Projects carry no restart policy, so a VM reboot leaves them stopped
        while state.db still says started and Traefik answers 404."""
        job_ids = []
        for row in self._projects.list():
            if row["status"] != STARTED_OK:
                continue
            try:
                job_ids.append(self.start(row["id"]))
            except EggieError as e:
                log.warning("not resuming %s: %s", row["id"], e.message)
        return job_ids

    def logs(self, project_id: str, service: str | None) -> str:
        self._loader.require(project_id)
        result = docker.project_logs(self._runner, self._loader.dir(project_id),
                                     service, env=self._secrets.values(project_id))
        if not result.ok:
            raise Conflict("logs_unavailable",
                           (result.stderr or result.stdout).strip()
                           or "docker compose logs failed")
        return result.stdout

    def logs_stream(self, project_id: str, service: str | None) -> Iterator[str]:
        self._loader.require(project_id)
        argv = docker.logs_argv(self._loader.dir(project_id), service, follow=True)
        return self._runner.stream(argv, root=True, env=self._secrets.values(project_id))
