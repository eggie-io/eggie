from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

from .detect import WebSpec
from . import constants


def host_for(project_id: str, web: WebSpec, domain: str) -> str:
    if web.subdomain:
        return f"{web.subdomain}.{project_id}.{domain}"
    return f"{project_id}.{domain}"


def build_overlay(project_id: str, webs: list[WebSpec], domain: str, *,
                  services: Sequence[str] = (),
                  secret_names: Iterable[str] = (),
                  declared: Mapping[str, set[str]] | None = None) -> dict:
    overlay_services: dict = {}
    for web in webs:
        router = f"{project_id}-{web.service}"
        host = host_for(project_id, web, domain)
        overlay_services[web.service] = {
            "networks": ["default", constants.EDGE_NETWORK],
            "labels": {
                "traefik.enable": "true",
                f"traefik.http.routers.{router}.rule": f"Host(`{host}`)",
                f"traefik.http.services.{router}.loadbalancer.server.port":
                    str(web.port),
            },
        }
    names = sorted(secret_names)
    if names:
        declared = declared or {}
        # Bare names: compose takes each value from its own environment,
        # so no value is ever written into the project folder.
        for service in dict.fromkeys([*services, *overlay_services]):
            # A value the service sets itself is the project's explicit choice.
            own = [n for n in names if n not in declared.get(service, set())]
            if own:
                overlay_services.setdefault(service, {})["environment"] = own
    return {
        "services": overlay_services,
        "networks": {constants.EDGE_NETWORK: {"external": True}},
    }
