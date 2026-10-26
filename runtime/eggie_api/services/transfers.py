"""The kitchen's side of a push: who may open one, where its bundle lands, and
the import that puts the pushed files in place of the project's own."""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets as tokens
import shutil
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable

import yaml

from .. import constants
from ..config import ApiConfig
from ..domain import push as rules
from ..domain.secrets import SecretError, check_hint
from ..errors import (Conflict, DiskFull, EggieError, Forbidden, Invalid, LengthRequired,
                      NotFound, TooLarge, TooMany, Unavailable, Upstream)
from ..infra import docker
from ..infra.bundle import restore_argv, unpack_argv
from ..infra.cloud import CloudError, CloudUnavailable
from ..infra.docker import DOCKER
from ..infra.repos.projects import ProjectRepo
from ..infra.repos.pushed_projects import PushedProjectRepo
from .account import Account, NotSignedIn
from .jobs import JobFailed, JobRegistry
from .lifecycle import LifecycleService
from .loader import ProjectLoader
from .locks import ProjectLocks
from .ratelimit import KeyedWindow, Window
from .secrets import SecretService

log = logging.getLogger("eggie.api")

OPENS_PER_MINUTE = 10
OPENS_PER_MINUTE_ALL = 60
MAX_SENDERS = 1024
UPLOAD_WINDOW = 600
KEEP_ENDED = 3600
DISK_MARGIN = 1024 ** 3
COMPLETE_DELAYS = (1, 3, 9)
MAX_TICKET = 512
MAX_MANIFEST = 1024 ** 2

OPENED, UPLOADING, IMPORTING, ENDED = "opened", "uploading", "importing", "ended"
REPLACE_MODES = (rules.REPLACE_NONE, rules.REPLACE_FILES, rules.REPLACE_ALL)

BUSY = "This kitchen is busy with this project. Try again in a minute."
NOT_READY = "This kitchen isn't signed in to Eggie."
CLOUD_UNAVAILABLE = "The kitchen couldn't reach the Eggie service."
REFUSALS = {
    "already_on_kitchen": "This project is already on this kitchen. Pushing again replaces it.",
    "confirm_replace_data": "Confirm that the kitchen's data should be replaced.",
}


def _digest(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


def _gb(n: int) -> str:
    return f"{n / 1024 ** 3:.1f} GB"


def bad_transfer(header: str) -> Invalid:
    return Invalid("bad_transfer", f"the push request needs a valid {header} header")


def _failed(code: str, message: str) -> JobFailed:
    return JobFailed(message, {"code": code})


def _restored(volume: str, result) -> None:
    if not result.ok:
        raise _failed("restore_failed", f"Volume {volume} could not be restored: "
                                        f"{(result.stderr or result.stdout).strip()}")


def _real_dir(path: Path) -> bool:
    return path.is_dir() and not path.is_symlink()


def _real_file(path: Path) -> bool:
    return path.is_file() and not path.is_symlink()


def _no_links_above(root: Path, rel: str) -> bool:
    current = root
    for part in rel.split("/")[:-1]:
        current = current / part
        if current.is_symlink():
            return False
    return True


def _daemon_timer(seconds: float, fn: Callable[[], None]) -> threading.Timer:
    timer = threading.Timer(seconds, fn)
    timer.daemon = True
    return timer


@dataclass(frozen=True)
class _Grant:
    ticket_id: str
    cloud_id: str
    name: str
    source_device_id: str


@dataclass
class _Push:
    id: str
    token_hash: bytes
    local_id: str
    cloud_id: str
    ticket_id: str
    size: int
    data: bool
    created: bool
    state: str = OPENED
    timer: object | None = None
    job_id: str | None = None
    ended_at: float | None = None


class Transfers:
    def __init__(self, config: ApiConfig, runner, loader: ProjectLoader,
                 projects: ProjectRepo, pushed_projects: PushedProjectRepo,
                 secrets: SecretService, lifecycle: LifecycleService, jobs: JobRegistry,
                 locks: ProjectLocks, account: Account, cloud, *,
                 free_bytes: Callable[[], int], clock=time.time, sleep=time.sleep,
                 timer=None):
        self._config = config
        self._runner = runner
        self._loader = loader
        self._projects = projects
        self._pushed = pushed_projects
        self._secrets = secrets
        self._lifecycle = lifecycle
        self._jobs = jobs
        self._locks = locks
        self._account = account
        self._cloud = cloud
        self._free_bytes = free_bytes
        self._clock = clock
        self._sleep = sleep
        self._timer = timer or _daemon_timer
        self._per_sender = KeyedWindow(OPENS_PER_MINUTE, 60.0, lambda: self._clock(),
                                       what="push attempts from one address",
                                       max_keys=MAX_SENDERS)
        self._window = Window(OPENS_PER_MINUTE_ALL, 60.0, lambda: self._clock(),
                              what="push attempts")
        self._pushes: dict[str, _Push] = {}
        self._lock = threading.Lock()

    @property
    def _inbox(self) -> Path:
        return Path(self._config.transfers_root) / "in"

    def sweep(self) -> None:
        """Bundles and unpacked trees left behind by an API that stopped mid-import."""
        if self._inbox.is_dir():
            for path in self._inbox.iterdir():
                self._remove(path)

    def _remove(self, path: Path) -> None:
        if not os.path.lexists(path):
            return
        if not _real_dir(path):
            path.unlink(missing_ok=True)
            return
        shutil.rmtree(path, ignore_errors=True)
        # The root helper unpacked these with the pushed owners.
        if os.path.lexists(path):
            result = docker.remove_tree_as_root(self._runner, path)
            if not result.ok:
                log.warning("could not remove %s: %s", path,
                            (result.stderr or result.stdout).strip())

    def ready(self) -> bool:
        return self._account.signed_in and self._runner.exec(
            [DOCKER, "version", "--format", "{{.Server.Version}}"], root=True).ok

    def require_ready(self) -> None:
        if not self.ready():
            raise Unavailable("not_ready", "This kitchen isn't ready for a push.")

    def open(self, *, ticket: str, size: int, data: bool, replace: str,
             forwarded_for: str, peer: str) -> dict:
        if not (0 < len(ticket) <= MAX_TICKET and ticket.isascii() and ticket.isprintable()
                and " " not in ticket):
            raise bad_transfer("Eggie-Transfer-Ticket")
        if size <= 0:
            raise bad_transfer("Eggie-Transfer-Size")
        if replace not in REPLACE_MODES:
            raise bad_transfer("Eggie-Transfer-Replace")
        if size > self._config.max_upload_bytes:
            raise TooLarge("payload_too_large",
                           f"A push may be at most {_gb(self._config.max_upload_bytes)}.")
        # Traefik replaces a client's own X-Forwarded-For with the address it came from.
        sender = forwarded_for.split(",", 1)[0].strip() or peer
        # A full kitchen refuses before tracking a sender; a refused sender
        # takes nothing from the shared window.
        if not (self._window.has_room() and self._per_sender.take(sender)
                and self._window.take()):
            raise TooMany("kitchen_busy", "Too many pushes to this kitchen right now. "
                                          "Try again in a minute.")
        grant = self._redeem(ticket)
        with self._lock:
            now = self._clock()
            self._prune(now)
            self._drop_waiting(grant.cloud_id)
            row = self._pushed.by_cloud_id(grant.cloud_id)
            exists = row is not None
            local_id = row["local_id"] if exists else rules.kitchen_project_id(
                grant.name, self._taken())
            if not self._locks.acquire(local_id):
                raise Conflict("kitchen_busy", BUSY)
            try:
                refusal = rules.check_replace(exists, data, replace)
                if refusal:
                    raise Conflict(refusal, REFUSALS[refusal])
                need, free = 2 * size + DISK_MARGIN, self._free_bytes()
                if free < need:
                    raise DiskFull("kitchen_disk_full",
                                   f"it needs {_gb(need)} and has {_gb(free)} free.")
                # Claimed first: a sync pass must never see an unclaimed new project.
                self._pushed.claim(local_id, grant.cloud_id, grant.source_device_id, now)
                if self._projects.get(local_id) is None:
                    folder = self._loader.dir(local_id)
                    folder.mkdir(parents=True, exist_ok=True)
                    self._projects.add(local_id, str(folder), self._config.domain,
                                       origin="folder")
            except BaseException:
                if not exists:
                    self._pushed.remove(local_id)
                self._locks.release(local_id)
                raise
            token = tokens.token_urlsafe(32)
            push = _Push(id=tokens.token_hex(8), token_hash=_digest(token),
                         local_id=local_id, cloud_id=grant.cloud_id,
                         ticket_id=grant.ticket_id, size=size, data=data,
                         created=not exists)
            self._pushes[push.id] = push
            push.timer = self._timer(UPLOAD_WINDOW, lambda: self._expire(push.id))
            push.timer.start()
        log.info("push %s opened for %s", push.id, local_id)
        return {"push_id": push.id, "token": token}

    def _redeem(self, ticket: str) -> _Grant:
        try:
            grant = self._account.authed(
                lambda token: self._cloud.redeem_transfer_ticket(token, ticket))
        except NotSignedIn:
            raise Unavailable("not_ready", NOT_READY) from None
        except CloudUnavailable:
            raise Unavailable("cloud_unavailable", CLOUD_UNAVAILABLE) from None
        except CloudError as e:
            if e.code == "transfer_ticket_invalid":
                raise Forbidden(e.code, e.message) from None
            raise Upstream("cloud_error", e.message) from None
        try:
            project = grant["project"]
            fields = (grant["ticket_id"], project["id"], project["name"],
                      grant["source_device_id"])
        except (KeyError, TypeError):
            fields = ()
        if len(fields) != 4 or not all(isinstance(f, str) for f in fields) \
                or not fields[0] or not fields[1]:
            raise Upstream("cloud_error",
                           "The Eggie service sent a grant this kitchen can't read.")
        return _Grant(*fields)

    def _taken(self) -> set[str]:
        taken = {r["id"] for r in self._projects.list()} | self._pushed.local_ids()
        root = Path(self._config.projects_root)
        if root.is_dir():
            taken |= {p.name for p in root.iterdir()}
        return taken

    # The helpers below run with self._lock held.

    def _prune(self, now: float) -> None:
        for push in list(self._pushes.values()):
            if push.state == ENDED and now - push.ended_at > KEEP_ENDED:
                del self._pushes[push.id]

    def _expire(self, push_id: str) -> None:
        with self._lock:
            push = self._pushes.get(push_id)
            if push is not None and push.state == OPENED:
                log.info("push %s for %s got no upload in time", push.id, push.local_id)
                self._drop(push)

    def _drop_waiting(self, cloud_id: str) -> None:
        for push in list(self._pushes.values()):
            if push.cloud_id == cloud_id and push.state == OPENED:
                self._drop(push)

    def _drop(self, push: _Push) -> None:
        """Ends a push that never reached its import."""
        self._pushes.pop(push.id, None)
        if push.timer is not None:
            push.timer.cancel()
        if push.created:
            # Nothing landed, so the next push of this project is a first push again.
            self._projects.remove(push.local_id)
            self._pushed.remove(push.local_id)
            try:
                self._loader.dir(push.local_id).rmdir()
            except OSError:
                pass
        self._locks.release(push.local_id)

    def _find(self, push_id: str, token: str) -> _Push:
        push = self._pushes.get(push_id)
        expected = push.token_hash if push is not None else bytes(32)
        if not hmac.compare_digest(_digest(token), expected) or push is None:
            raise NotFound("transfer_not_found", "There is no such push on this kitchen.")
        return push

    def _ended(self, push: _Push) -> None:
        with self._lock:
            push.state = ENDED
            push.ended_at = self._clock()

    async def receive(self, push_id: str, token: str,
                      save: Callable[[Path], Awaitable[Path]],
                      content_length: int | None) -> dict:
        with self._lock:
            self._prune(self._clock())
            push = self._find(push_id, token)
            if push.state != OPENED:
                raise Conflict("transfer_started", "This push already has its bundle.")
            push.state = UPLOADING
            push.timer.cancel()
        bundle = self._inbox / f"{push.id}.tar"
        try:
            if content_length is None:
                raise LengthRequired("length_required",
                                     "Send the bundle with a Content-Length.")
            if content_length != push.size:
                raise Invalid("bad_transfer",
                              "Content-Length must equal Eggie-Transfer-Size.")
            temp = await save(self._inbox)
            try:
                if temp.stat().st_size != push.size:
                    raise Invalid("bad_transfer", "The bundle is not the declared size.")
                os.replace(temp, bundle)
            finally:
                temp.unlink(missing_ok=True)
            with self._lock:
                push.state = IMPORTING
            push.job_id = self._jobs.submit(_ImportJob(self, push, bundle).run,
                                            kind="import", project_id=push.local_id)
        except BaseException:
            bundle.unlink(missing_ok=True)
            with self._lock:
                self._drop(push)
            raise
        return {"push_id": push.id}

    def status(self, push_id: str, token: str) -> dict:
        with self._lock:
            self._prune(self._clock())
            push = self._find(push_id, token)
            state, job_id = push.state, push.job_id
        job = self._jobs.get(job_id) if job_id else None
        if job is None:
            if state == ENDED:
                raise NotFound("transfer_not_found", "There is no such push on this kitchen.")
            return {"state": "running",
                    "phase": "unpacking" if state == IMPORTING else "receiving",
                    "detail": "", "result": None}
        out = job.as_dict()
        return {key: out[key] for key in ("state", "phase", "detail", "result")}


class _ImportJob:
    """One import, on the job's thread, with the project lock that open() took."""

    def __init__(self, service: Transfers, push: _Push, bundle: Path):
        self.s = service
        self.push = push
        self.pid = push.local_id
        self.bundle = bundle
        self.staging = service._inbox / push.id
        self.image: str | None = None
        self.stopped = False
        self.keep_staging = False

    def run(self, write) -> dict:
        try:
            try:
                result = self._steps(write)
            except EggieError as e:
                raise _failed(e.code, e.message) from None
            except JobFailed as e:
                if (e.result or {}).get("code"):
                    raise
                raise _failed("import_failed", str(e)) from None
            except Exception:
                log.exception("importing a push into %s failed", self.pid)
                raise _failed("import_failed",
                              "The kitchen could not import the project.") from None
            finally:
                self.bundle.unlink(missing_ok=True)
                if not self.keep_staging:
                    self.s._remove(self.staging)
                self.s._locks.release(self.pid)
                self.s._ended(self.push)
        except JobFailed:
            # It was up before: start whatever is in place now, rolled back or kept.
            if self.stopped:
                self._start(write)
            raise
        self._start(write)
        return result

    def _start(self, write) -> None:
        try:
            self.s._lifecycle.start_when_ready(self.pid)
        except EggieError as e:
            write(f"not starting {self.pid}: {e.message}\n")

    def _steps(self, write) -> dict:
        write.phase("unpacking")
        write(f"unpacking the bundle for {self.pid}\n")
        manifest, compose = self._unpack()
        row = self.s._projects.get(self.pid)
        # Compose down is idempotent, and a crash-looping stack still holds its volumes.
        if row["status"] != "stopped":
            write.phase("stopping")
            write(f"stopping {self.pid}\n")
            self.stopped = True
            try:
                self.s._lifecycle.down_now(self.pid)
            except JobFailed as e:
                raise _failed("stop_failed", f"{self.pid} could not be stopped: {e}") from None
        write.phase("replacing")
        self._replace(self.s._loader.dir(self.pid), manifest)
        if manifest.data:
            write.phase("restoring data")
            self._restore(compose, manifest, write)
        missing = self.s._secrets.apply_pushed(self.pid, manifest.secrets)
        write.phase("completing")
        self._complete()
        self.s._pushed.touch(self.pid, self.s._clock())
        if missing:
            self.s._projects.set_start_when_ready(self.pid, True)
            write(f"{self.pid} starts once its {missing} secret(s) are set\n")
        return {"kitchen_project": self.pid, "urls": self._urls(row["domain"]),
                "secrets_missing": missing}

    def _unpack(self) -> tuple[rules.Manifest, dict]:
        self.staging.mkdir(parents=True)
        self.image = docker.own_image(self.s._runner)
        if not self.image:
            raise _failed("import_failed", "The kitchen could not find its own image "
                                           "to unpack the bundle with.")
        result = self.s._runner.exec(
            unpack_argv(self.image, bundle_path=self.bundle, dest_dir=self.staging), root=True)
        if not result.ok:
            reason = ((result.stderr or result.stdout).strip().splitlines() or ["unknown"])[-1]
            raise Invalid("bad_bundle", f"The bundle could not be unpacked: {reason}")
        # open() reserved room for the bundle and one copy of it, not two.
        self.bundle.unlink(missing_ok=True)
        files = self.staging / "files"
        manifest_path = self.staging / "manifest.json"
        compose_path = files / constants.COMPOSE_FILE
        if not _real_dir(files) or not _real_file(compose_path):
            raise Invalid("bad_bundle", f"The bundle has no {constants.COMPOSE_FILE}.")
        if not _real_file(manifest_path) or manifest_path.stat().st_size > MAX_MANIFEST:
            raise Invalid("bad_manifest", "The bundle has no readable manifest.")
        try:
            raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise Invalid("bad_manifest", "The bundle's manifest is not JSON.") from None
        compose = self.s._loader.parse_yaml(compose_path)
        manifest = rules.parse_manifest(raw, compose)
        # The overwrite rules were checked against what open() was told.
        if manifest.data != self.push.data:
            raise Invalid("bad_bundle", "The bundle's data does not match what the push "
                                        "was opened with.")
        for hint in manifest.secrets.values():
            if hint:
                try:
                    check_hint(hint)
                except SecretError as e:
                    raise Invalid("bad_manifest", e.message) from None
        # Compose takes `name:` as its project: another project's would have this
        # import stop and start that project's containers.
        compose_name = self._own_compose_name(compose)
        if manifest.data:
            self._check_volumes(compose, compose_name, manifest)
        self._pin_project_id(files)
        return manifest, compose

    def _pin_project_id(self, files: Path) -> None:
        rel = ".eggie/project.yml"
        path = files / rel
        if not os.path.lexists(path):
            return
        if not _real_file(path) or not _no_links_above(files, rel):
            raise Invalid("bad_bundle", f"The bundle's {rel} is not a plain file.")
        try:
            pinned = rules.pinned_project_yml(self.s._loader.parse_yaml(path), self.pid)
        except Invalid as e:
            raise Invalid("bad_bundle", e.message) from None
        if pinned is not None:
            path.write_text(yaml.safe_dump(pinned, sort_keys=False), encoding="utf-8")

    def _own_compose_name(self, compose: dict) -> str:
        compose_name = str(compose.get("name") or self.pid)
        others = {name for r in self.s._projects.list() if r["id"] != self.pid
                  for name in (r["id"], r.get("compose_name")) if name}
        if compose_name in others:
            raise Invalid("bad_bundle", f"The compose name {compose_name} belongs to "
                                        "another project on this kitchen.")
        return compose_name

    def _check_volumes(self, compose: dict, compose_name: str,
                       manifest: rules.Manifest) -> None:
        """Restoring removes each volume first, so a bundle must only ever name
        this project's own."""
        outside = rules.volume_outside(compose, compose_name, manifest.volumes)
        if outside:
            raise Invalid("bad_bundle", f"Volume {outside[0]} is named {outside[1]}, outside "
                                        "this project.")
        for key in manifest.volumes:
            if not _real_dir(self.staging / "volumes" / key):
                raise Invalid("bad_bundle", f"The bundle has no data for volume {key}.")

    def _move(self, src: Path, dst: Path) -> None:
        try:
            os.rename(src, dst)
        except PermissionError:
            result = docker.move_as_root(self.s._runner, src, dst)
            if not result.ok:
                raise OSError(f"could not move {src} to {dst}: "
                              f"{(result.stderr or result.stdout).strip()}") from None

    def _replace(self, target: Path, manifest: rules.Manifest) -> None:
        new, aside = self.staging / "files", self.staging / "old"
        had_old = os.path.lexists(target)
        placed, kept = False, []
        if had_old:
            self._move(target, aside)
        try:
            self._move(new, target)
            placed = True
            if had_old and not manifest.data:
                for rel in manifest.data_paths:
                    src, dst = aside / rel, target / rel
                    if not os.path.lexists(src) or not _no_links_above(aside, rel):
                        continue
                    # A link in the pushed tree would carry the kitchen's data out of it.
                    if not _no_links_above(target, rel) or os.path.lexists(dst):
                        raise Invalid("bad_bundle", f"The pushed files have their own {rel}, "
                                                    "which the kitchen keeps.")
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    self._move(src, dst)
                    kept.append(rel)
        except BaseException as e:
            self._put_back(target, new, aside, kept, placed=placed, had_old=had_old)
            if os.path.lexists(aside):
                raise self._keep_old(aside, e) from None
            raise
        if had_old:
            self.s._remove(aside)

    def _keep_old(self, aside: Path, error: BaseException) -> JobFailed:
        """The import's cleanup would delete what could not be put back."""
        code = error.code if isinstance(error, EggieError) else "import_failed"
        reason = error.message if isinstance(error, EggieError) \
            else "The kitchen could not replace the project's files."
        kept_at = Path(self.s._config.transfers_root) / "kept" / f"{self.pid}-{self.push.id}"
        try:
            kept_at.parent.mkdir(parents=True, exist_ok=True)
            self._move(aside, kept_at)
        except OSError:
            log.exception("could not keep %s's previous files out of the import's way", self.pid)
            self.keep_staging, kept_at = True, aside
        log.error("%s's previous files could not be put back; they are in %s", self.pid, kept_at)
        return _failed(code, f"{reason} The project's previous files could not be put back; "
                             f"they are kept in {kept_at}.")

    def _put_back(self, target: Path, new: Path, aside: Path, kept: list[str], *,
                  placed: bool, had_old: bool) -> None:
        try:
            for rel in reversed(kept):
                self._move(target / rel, aside / rel)
            if placed:
                self._move(target, new)
            if had_old:
                self._move(aside, target)
        except Exception:
            log.exception("could not put %s back after a failed push", target)

    def _restore(self, compose: dict, manifest: rules.Manifest, write) -> None:
        runner = self.s._runner
        compose_name = str(compose.get("name") or self.pid)
        for key in manifest.volumes:
            name = rules.docker_volume_name(compose, compose_name, key)
            write(f"restoring volume {name}\n")
            _restored(name, docker.remove_volume(runner, name))
            _restored(name, docker.create_volume(runner, name, project=compose_name, key=key))
            _restored(name, runner.exec(restore_argv(
                self.image, source_dir=self.staging / "volumes" / key, volume_name=name),
                root=True))

    def _complete(self) -> None:
        ticket_id = self.push.ticket_id
        for delay in (*COMPLETE_DELAYS, None):
            try:
                self.s._account.authed(
                    lambda token: self.s._cloud.complete_transfer(token, ticket_id))
                return
            except CloudUnavailable:
                if delay is None:
                    raise _failed("cloud_unavailable", CLOUD_UNAVAILABLE) from None
                self.s._sleep(delay)
            except NotSignedIn:
                raise _failed("not_ready", NOT_READY) from None
            except CloudError as e:
                if e.code == "project_not_found":
                    raise _failed(e.code, "This project was deleted from your account "
                                          "during the push.") from None
                raise _failed(e.code, e.message) from None

    def _urls(self, domain: str) -> list[str]:
        try:
            return self.s._loader.urls_for(self.s._loader.load(self.pid), domain)
        except EggieError:
            return []
