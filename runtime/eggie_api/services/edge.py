from __future__ import annotations

from collections.abc import Callable, Collection
from pathlib import Path

import yaml

from ..domain.edge import Domain, ProjectEdge, build_edge


class Edge:
    """Writes `edge.yml`, the one file Traefik parses from the edge directory,
    from the installed certificates, the mirrored domains and the projects'
    Traefik services."""

    def __init__(self, directory: Path, certificates,
                 domains: Callable[[], list[Domain]],
                 projects: Callable[[Collection[str]], dict[str, ProjectEdge]]):
        self._dir = Path(directory)
        self._certificates = certificates
        self._domains = domains
        self._projects = projects

    def write(self) -> bool:
        domains = self._domains()
        projects = self._projects({d.project_id for d in domains}) if domains else {}
        text = yaml.safe_dump(build_edge(self._certificates.installed(), domains, projects))
        target = self._dir / "edge.yml"
        try:
            if target.read_text() == text:
                return False
        except OSError:
            pass
        self._dir.mkdir(parents=True, exist_ok=True)
        staging = self._dir / "edge.yml.new"
        staging.write_text(text)
        # Traefik must never see a half-written file.
        staging.replace(target)
        return True
