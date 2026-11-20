from __future__ import annotations

import hashlib
import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass

ACTIVE = "active"
REDIRECT_MAX_AGE = 600
CHALLENGE_PREFIX = "/.well-known/acme-challenge/"

_HOSTNAME = re.compile(
    r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9-]{2,63}$")
# Redirects outrank the project's own Eggie-host router; the ACME challenge
# outranks both so certificate checks are never redirected.
_REDIRECT_PRIORITY = 50000
_CHALLENGE_PRIORITY = 200000


@dataclass(frozen=True)
class Pair:
    id: str
    kind: str
    cert_file: str
    key_file: str
    names: tuple[str, ...]


@dataclass(frozen=True)
class Domain:
    hostname: str
    project_id: str
    primary: bool
    status: str
    certificate_id: str | None


@dataclass(frozen=True)
class ProjectEdge:
    eggie_host: str
    service: str


@dataclass(frozen=True)
class Plan:
    address: str
    direct: tuple[str, ...]
    redirects: tuple[str, ...]


def is_hostname(value: str) -> bool:
    return bool(_HOSTNAME.match(value))


def covered_hosts(pairs: Sequence[Pair]) -> frozenset[str]:
    return frozenset(n for p in pairs if p.kind == "domain" for n in p.names)


def serving(domain: Domain, covered: Collection[str]) -> bool:
    return domain.status == ACTIVE and domain.hostname in covered


def plan(eggie_host: str, domains: Sequence[Domain],
         covered: Collection[str]) -> Plan:
    live = [d for d in domains if serving(d, covered)]
    primary = next((d for d in live if d.primary), None)
    if primary is None:
        return Plan(eggie_host, tuple(d.hostname for d in live), ())
    others = tuple(d.hostname for d in live if d is not primary)
    return Plan(primary.hostname, (), others + (eggie_host,))


def _digest(value: str) -> str:
    return hashlib.sha1(value.encode()).hexdigest()[:12]


def build_edge(pairs: Sequence[Pair], domains: Sequence[Domain],
               projects: Mapping[str, ProjectEdge]) -> dict:
    domains = [d for d in domains if is_hostname(d.hostname)]
    covered = covered_hosts(pairs)
    routers: dict = {}
    middlewares: dict = {}

    by_project: dict[str, list[Domain]] = {}
    for d in domains:
        by_project.setdefault(d.project_id, []).append(d)

    for project_id, mine in by_project.items():
        project = projects.get(project_id)
        if project is None:
            continue
        got = plan(project.eggie_host, mine, covered)
        own = () if got.address == project.eggie_host else (got.address,)
        for host in own + got.direct:
            routers[f"custom-{_digest(host)}"] = {
                "rule": f"Host(`{host}`)",
                "entryPoints": ["web"],
                "service": f"{project.service}@docker",
            }
        if not got.redirects:
            continue
        target = f"to-{_digest(got.address)}"
        middlewares[target] = {"redirectRegex": {
            "regex": "^https?://[^/]+/(.*)",
            "replacement": f"https://{got.address}/${{1}}",
            "permanent": True,
        }}
        for host in got.redirects:
            routers[f"redirect-{_digest(host)}"] = {
                "rule": f"Host(`{host}`)",
                "entryPoints": ["web"],
                "priority": _REDIRECT_PRIORITY,
                "middlewares": ["redirect-cache", target],
                "service": "noop@internal",
            }

    if middlewares:
        middlewares["redirect-cache"] = {"headers": {"customResponseHeaders": {
            "Cache-Control": f"max-age={REDIRECT_MAX_AGE}"}}}
    if domains:
        hosts = " || ".join(f"Host(`{d.hostname}`)" for d in domains)
        routers["acme-challenge"] = {
            "rule": f"({hosts}) && PathPrefix(`{CHALLENGE_PREFIX}`)",
            "entryPoints": ["http", "web"],
            "priority": _CHALLENGE_PRIORITY,
            "service": "eggie-api@docker",
        }

    edge: dict = {}
    if routers:
        edge["http"] = {"routers": routers}
        if middlewares:
            edge["http"]["middlewares"] = middlewares
    if pairs:
        tls: dict = {"certificates": [
            {"certFile": p.cert_file, "keyFile": p.key_file} for p in pairs]}
        default = next((p for p in pairs if p.kind == "instance"), None)
        if default is not None:
            tls["stores"] = {"default": {"defaultCertificate": {
                "certFile": default.cert_file, "keyFile": default.key_file}}}
        edge["tls"] = tls
    return edge
