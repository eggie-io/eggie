from __future__ import annotations

from collections.abc import Iterable, Sequence

from .detect import WebSpec
from . import constants


def host_for(project_id: str, web: WebSpec, domain: str) -> str:
    if web.subdomain:
        return f"{web.subdomain}.{project_id}.{domain}"
    return f"{project_id}.{domain}"


def build_overlay(project_id: str, webs: list[WebSpec], domain: str, *,
                  services: Sequence[str] = (),
                  secret_names: Iterable[str] = ()) -> dict:
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
        # Bare names: compose takes each value from its own environment,
        # so no value is ever written into the project folder.
        for service in [*services, *overlay_services]:
            overlay_services.setdefault(service, {})["environment"] = names
    return {
        "services": overlay_services,
        "networks": {constants.EDGE_NETWORK: {"external": True}},
    }
