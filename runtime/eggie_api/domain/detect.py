from __future__ import annotations

from dataclasses import dataclass

from .compose import exposed_ports


@dataclass(frozen=True)
class WebSpec:
    service: str
    port: int
    subdomain: str | None = None


class AmbiguousError(Exception):
    """Auto-detection cannot decide which service serves HTTP."""


def _published(service: dict) -> bool:
    return bool(service.get("ports"))


def detect_web(compose: dict) -> list[WebSpec]:
    services: dict = compose.get("services", {}) or {}
    if not services:
        raise AmbiguousError("compose file declares no services")

    # A service that publishes a host port is a web candidate.
    published = {name: svc for name, svc in services.items() if _published(svc)}
    if published:
        return [WebSpec(service=name, port=exposed_ports(svc)[0])
                for name, svc in published.items()]

    # No published ports. A single service is unambiguous.
    if len(services) == 1:
        name, svc = next(iter(services.items()))
        ports = exposed_ports(svc)
        return [WebSpec(service=name, port=ports[0] if ports else 80)]

    raise AmbiguousError(
        "multiple services and none publish a port; "
        "declare `web:` explicitly in .eggie/project.yml")
