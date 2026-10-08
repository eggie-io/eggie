from __future__ import annotations

from pathlib import Path

import yaml

from .. import constants
from ..config import ApiConfig
from ..domain.detect import AmbiguousError
from ..domain.overlay import host_for
from ..domain.project import Project, load_project
from ..errors import BadRequest, EggieError, Invalid, NotFound
from ..infra.repos.projects import ProjectRepo


class ProjectLoader:
    """Reads a project's row and files; depends on no other service, so
    anything may hold one without a cycle."""

    def __init__(self, config: ApiConfig, projects: ProjectRepo):
        self._config = config
        self._projects = projects

    def dir(self, project_id: str) -> Path:
        return Path(self._config.projects_root) / project_id

    def require(self, project_id: str) -> dict:
        row = self._projects.get(project_id)
        if row is None:
            raise NotFound("project_not_found", f"no project with id '{project_id}'")
        return row

    def parse_yaml(self, path: Path) -> dict:
        try:
            data = yaml.safe_load(path.read_text()) or {}
        except yaml.YAMLError as e:
            # The parser already says where the mistake is; keep it on one line.
            raise Invalid("invalid_compose",
                          f"{path.name} is not valid YAML: {' '.join(str(e).split())}") from e
        if not isinstance(data, dict):
            raise Invalid("invalid_compose",
                          f"{path.name} must be a mapping, not a "
                          f"{type(data).__name__}")
        return data

    def compose(self, project_id: str) -> dict:
        path = self.dir(project_id) / constants.COMPOSE_FILE
        if not path.exists():
            raise BadRequest("compose_missing",
                             f"project '{project_id}' has no {constants.COMPOSE_FILE}")
        return self.parse_yaml(path)

    def load(self, project_id: str) -> Project:
        compose = self.compose(project_id)
        project_yml = self.dir(project_id) / ".eggie" / "project.yml"
        overrides = self.parse_yaml(project_yml) if project_yml.exists() else None
        try:
            return load_project(compose, overrides, project_id)
        except AmbiguousError as e:
            # The detector's message is already written for a human.
            raise Invalid("invalid_project", str(e)) from e
        except (AttributeError, KeyError, TypeError, ValueError) as e:
            raise Invalid("invalid_project",
                          f"the project definition cannot be read: {e}") from e

    def urls_for(self, project: Project, domain: str) -> list[str]:
        return [f"http://{host_for(project.id, web, domain)}:{self._config.edge_port}"
                for web in project.webs]

    def public_hosts(self, project_id: str) -> list[dict]:
        row = self._projects.get(project_id)
        if row is None:
            return []
        try:
            project = self.load(project_id)
        except EggieError:
            return []
        hosts = [host_for(project.id, web, row["domain"]) for web in project.webs]
        return [{"service": web.service, "hostname": host,
                 "local_url": f"http://{host}:{self._config.edge_port}"}
                for web, host in zip(project.webs, hosts)]
