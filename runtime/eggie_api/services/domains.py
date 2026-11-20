from __future__ import annotations

import json
import logging
import re
import threading
import time
from collections.abc import Callable, Collection

from ..config import ApiConfig
from ..domain.edge import ACTIVE, Domain, plan, serving
from ..domain.hostname import tidy
from ..domain.overlay import host_for
from ..errors import Conflict, EggieError, Invalid, NotFound, TooMany, Unavailable, Upstream
from ..infra.cloud import CloudError, CloudUnavailable
from ..infra.repos.domains import DomainRepo
from .account import NotSignedIn
from .loader import ProjectLoader
from .ratelimit import KeyedWindow, Window, client_address

log = logging.getLogger("eggie.domains")

SYNC_PENDING = 60.0
SYNC_IDLE = 600.0
PAGE_FRESH = 10.0
CHALLENGE_TOKEN = re.compile(r"[A-Za-z0-9_-]{20,200}")
CHALLENGES_PER_CLIENT = 20
CHALLENGES_ALL = 300
CHALLENGE_WINDOW = 60.0

_BY_STATUS = {404: NotFound, 409: Conflict, 422: Invalid, 429: TooMany}


def _row(d: dict) -> dict:
    """A domain object from the service as a mirror row; raises on a shape
    that is not the contract's."""
    hostname, project, status = d["hostname"], d["project"], d["status"]
    certificate_id = d.get("certificate_id")
    if not all(isinstance(v, str) and v for v in (hostname, project, status)) or not (
            certificate_id is None or isinstance(certificate_id, str)):
        raise TypeError(f"the domain {hostname!r} is not understood")
    problem = d.get("problem")
    return {"hostname": hostname, "display": str(d.get("display") or hostname),
            "project_id": project, "is_primary": 1 if d.get("primary") else 0,
            "status": status,
            "problem_json": None if problem is None else json.dumps(problem),
            "dns_json": json.dumps(d.get("dns") or []), "certificate_id": certificate_id,
            "checked_at": d.get("checked_at"), "next_check_at": d.get("next_check_at")}


def _domain(row: dict) -> Domain:
    return Domain(row["hostname"], row["project_id"], bool(row["is_primary"]),
                  row["status"], row["certificate_id"])


def _answer(read: Callable[[], object]):
    try:
        return read()
    except (KeyError, TypeError, ValueError):
        raise Upstream("bad_response", "The Eggie service sent an answer this kitchen "
                                       "could not read. Try again in a minute.") from None


class Domains:
    """The custom domains the Eggie service holds for this server, mirrored so
    routing survives a reboot or an outage. The service decides every status."""

    def __init__(self, repo: DomainRepo, loader: ProjectLoader, account, cloud,
                 config: ApiConfig, *, installed: Callable[[], Collection[str]],
                 changed: Callable[[], None], clock=time.time):
        self._repo = repo
        self._loader = loader
        self._account = account
        self._cloud = cloud
        self._config = config
        self._installed = installed
        self._changed = changed
        self._clock = clock
        self._synced_at: float | None = None
        self._tried_at: float | None = None
        # Network calls stay outside the lock; a store bumps the generation, and a
        # refresh whose list predates a store is dropped instead of undoing it.
        self._lock = threading.Lock()
        self._generation = 0
        self._challenges_per_client = KeyedWindow(
            CHALLENGES_PER_CLIENT, CHALLENGE_WINDOW, clock, what="certificate checks")
        self._challenges = Window(CHALLENGES_ALL, CHALLENGE_WINDOW, clock,
                                  what="certificate checks")

    def sync(self) -> None:
        if not self._account.signed_in:
            return
        try:
            self._delete_queued()
        except (NotSignedIn, CloudUnavailable) as e:
            log.info("queued domain deletes wait for the service: %r", e)
            return
        rows = self._repo.list()
        idle = all(r["status"] == ACTIVE for r in rows)
        every = SYNC_IDLE if idle else SYNC_PENDING
        if self._synced_at is None or self._clock() - self._synced_at >= every:
            self._refresh()

    def _delete_queued(self) -> None:
        for hostname in self._repo.queued():
            try:
                self._account.authed(
                    lambda t, h=hostname: self._cloud.delete_instance_domain(t, h))
            except CloudError as e:
                if e.status != 404:
                    log.warning("could not delete the domain %s: %s", hostname, e)
                    continue
            self._repo.unqueue(hostname)

    def _refresh(self) -> None:
        """Replaces the mirror with the service's list; any failure keeps it."""
        self._tried_at = self._clock()
        seen = self._generation
        try:
            out = self._account.authed(self._cloud.instance_domains)
            rows = [_row(d) for d in out["domains"]]
        except NotSignedIn:
            return
        except (CloudError, CloudUnavailable) as e:
            log.warning("could not list the domains: %s", e)
            return
        except (KeyError, TypeError) as e:
            log.warning("the domain list was not understood: %s", e)
            return
        with self._lock:
            if self._generation != seen:
                return
            queued = set(self._repo.queued())
            before = self._repo.list()
            self._repo.replace_all([r for r in rows if r["hostname"] not in queued])
            self._generation += 1
            self._synced_at = self._clock()
            moved = self._repo.list() != before
        if moved:
            self._changed()

    def _store(self, write: Callable[[], None], *, relist: bool = False) -> None:
        with self._lock:
            write()
            self._generation += 1
            if relist:
                self._synced_at = None
        self._changed()

    def rows(self) -> list[Domain]:
        return [_domain(r) for r in self._repo.list()]

    def certificate_ids(self) -> set[str]:
        return {r["certificate_id"] for r in self._repo.list() if r["certificate_id"]}

    def addresses(self, project_id: str) -> dict:
        project = self._loader.require(project_id)
        # Attempts, not successes: a polling page must not hammer a service that is down.
        stale = self._tried_at is None or self._clock() - self._tried_at >= PAGE_FRESH
        if stale and any(r["status"] != ACTIVE for r in self._repo.for_project(project_id)):
            self._refresh()
        return self._addresses(project_id, project)

    def summary(self, project_id: str) -> list[dict]:
        return [{"hostname": r["hostname"], "display": r["display"], "status": r["status"],
                 "primary": bool(r["is_primary"])}
                for r in self._repo.for_project(project_id)]

    def primary_url(self, project_id: str) -> str | None:
        rows = self._repo.for_project(project_id)
        if not rows or self._eggie_host(project_id, self._loader.require(project_id)) is None:
            return None
        installed = set(self._installed())
        for row in rows:
            domain = _domain(row)
            if domain.primary and serving(domain, installed):
                return f"https://{domain.hostname}"
        return None

    def _eggie_host(self, project_id: str, project: dict) -> str | None:
        try:
            loaded = self._loader.load(project_id)
        except (EggieError, OSError, UnicodeError):
            return None
        if not loaded.webs:
            return None
        return host_for(loaded.id, loaded.webs[0], project["domain"])

    def _addresses(self, project_id: str, project: dict) -> dict:
        rows = self._repo.for_project(project_id)
        eggie = self._eggie_host(project_id, project)
        # Without a web service the edge routes none of the project's domains.
        got = plan(eggie, [_domain(r) for r in rows], set(self._installed())) if eggie else None

        def how(hostname: str) -> str:
            if got and (hostname == got.address or hostname in got.direct):
                return "direct"
            return "redirects" if got and hostname in got.redirects else "none"

        addresses = [{"kind": "custom", "hostname": r["hostname"], "display": r["display"],
                      "primary": bool(r["is_primary"]), "status": r["status"],
                      "serving": how(r["hostname"]),
                      "problem": json.loads(r["problem_json"]) if r["problem_json"] else None,
                      "dns": json.loads(r["dns_json"]),
                      "checked_at": r["checked_at"], "next_check_at": r["next_check_at"]}
                     for r in rows]
        if eggie:
            addresses.append({"kind": "eggie", "hostname": eggie,
                              "url": self._config.public_url(eggie), "serving": how(eggie)})
        return {"addresses": addresses}

    def _call(self, fn):
        try:
            return self._account.authed(fn)
        except NotSignedIn:
            raise Conflict("not_signed_in", "This kitchen is signed out of Eggie. "
                                            "Sign it in again to manage its domains.") from None
        except CloudUnavailable:
            raise Unavailable("cloud_unavailable", "The Eggie service is unreachable. "
                                                   "Try again in a minute.") from None
        except CloudError as e:
            if e.code == "domain_not_found":
                self._synced_at = None
            elif e.status == 404:
                raise Unavailable("domains_unavailable", "Custom domains are not available "
                                                         "on this kitchen yet.") from None
            raise _BY_STATUS.get(e.status, Upstream)(e.code, e.message) from None

    def _own(self, project_id: str, hostname: str) -> None:
        if not any(r["hostname"] == hostname for r in self._repo.for_project(project_id)):
            raise NotFound("domain_not_found", "That domain is not attached to this project.")

    def add(self, project_id: str, raw: str) -> dict:
        project = self._loader.require(project_id)
        hostname = tidy(raw)
        target = self._eggie_host(project_id, project)
        if target is None:
            raise Invalid("no_web_service", "This project has no web address yet. "
                                            "Start it first, then add a domain.")
        if hostname in self._repo.queued():
            # Still attached to a deleted project on the service: let go of it first.
            self._call(lambda t: self._cloud.delete_instance_domain(t, hostname))
            self._repo.unqueue(hostname)
        out = self._call(lambda t: self._cloud.add_instance_domain(
            t, hostname, project_id, target))
        row, suggested = _answer(lambda: (_row(out["domain"]), out.get("suggested")))
        self._store(lambda: self._repo.upsert(row))
        return {**self._addresses(project_id, project), "suggested": suggested}

    def remove(self, project_id: str, hostname: str) -> dict:
        project = self._loader.require(project_id)
        self._own(project_id, hostname)
        self._call(lambda t: self._cloud.delete_instance_domain(t, hostname))
        # The service may have moved the primary to another of the project's domains.
        self._store(lambda: self._repo.remove(hostname), relist=True)
        return self._addresses(project_id, project)

    def make_primary(self, project_id: str, hostname: str) -> dict:
        project = self._loader.require(project_id)
        self._own(project_id, hostname)
        out = self._call(lambda t: self._cloud.primary_instance_domain(t, hostname))
        rows = _answer(lambda: [_row(d) for d in out["domains"]])
        self._store(lambda: self._repo.replace_project(
            project_id, [r for r in rows if r["project_id"] == project_id]))
        return self._addresses(project_id, project)

    def check(self, project_id: str, hostname: str) -> dict:
        project = self._loader.require(project_id)
        self._own(project_id, hostname)
        out = self._call(lambda t: self._cloud.check_instance_domain(t, hostname))
        row = _answer(lambda: _row(out["domain"]))
        self._store(lambda: self._repo.upsert(row))
        return self._addresses(project_id, project)

    def forget_project(self, project_id: str) -> None:
        if not self._repo.for_project(project_id):
            return
        self._store(lambda: self._repo.forget_project(project_id))

    def challenge(self, token: str, *, forwarded_for: str = "", peer: str = "") -> str:
        """Open to anyone on the internet: only a well-formed token, within both
        windows, costs a call to the service."""
        if not (CHALLENGE_TOKEN.fullmatch(token)
                and self._challenges_per_client.take(client_address(forwarded_for, peer))
                and self._challenges.take()):
            raise NotFound("challenge_not_found", "no such challenge")
        try:
            out = self._account.authed(lambda t: self._cloud.acme_challenge(t, token))
            value = out["key_authorization"]
        except (NotSignedIn, CloudError, CloudUnavailable, KeyError, TypeError):
            raise NotFound("challenge_not_found", "no such challenge") from None
        if not isinstance(value, str) or not value:
            raise NotFound("challenge_not_found", "no such challenge")
        return value
