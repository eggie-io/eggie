"""Pushes a local project to a kitchen: pack it, mint a ticket, send the
bundle straight to the kitchen and wait for its import."""
from __future__ import annotations

import json
import logging
import math
import time
import urllib.parse
from dataclasses import asdict
from pathlib import Path
from typing import Callable

from ..config import ApiConfig
from ..domain import push as rules
from ..domain.project import STARTED_OK
from ..errors import Conflict, DiskFull, EggieError, Unavailable, Upstream
from ..infra import disk, docker, files
from ..infra.bundle import pack_argv
from ..infra.cloud import CloudError, CloudUnavailable
from ..infra.kitchen import KitchenClient, KitchenError, KitchenUnreachable
from ..infra.repos.cloud_projects import CloudProjectRepo
from ..infra.repos.kitchen_pushes import KitchenPushRepo
from ..infra.repos.projects import ProjectRepo
from .account import Account, NotSignedIn
from .jobs import DONE, FAILED, JobFailed
from .lifecycle import LifecycleService
from .loader import ProjectLoader
from .locks import ProjectLocks
from .secrets import SecretService

log = logging.getLogger("eggie.api")

REGISTRATION_WAIT = 15
STATUS_INTERVAL = 2
RETRY_DELAY = 5
UNREACHABLE_ATTEMPTS = 3
PACK_MARGIN = 512 * 1024 ** 2

NOT_SIGNED_IN = "Push needs an Eggie account. Sign in to use it."
CLOUD_UNAVAILABLE = "The Eggie service couldn't be reached. Check the internet connection."
NOT_REGISTERED = "This project isn't linked to your account yet. Try again in a minute."
CONFIRM_REPLACE = "Confirm that the kitchen's data should be replaced."
DISK_FULL = "This computer doesn't have room to pack the project."

MINT_CODES = {"project_not_found", "instance_not_running", "instance_forbidden",
              "instance_not_found", "transfer_rate_limited"}
KITCHEN_CODES = {"transfer_ticket_invalid", "kitchen_busy", "already_on_kitchen",
                 "confirm_replace_data"}


def _wait_text(retry_after: int | None) -> str:
    if retry_after is None:
        return "a few minutes"
    minutes = max(1, math.ceil(retry_after / 60))
    return f"{minutes} minute" + ("" if minutes == 1 else "s")


def _copy(code: str, *, kitchen: str, project: str, retry_after: int | None) -> str | None:
    return {
        "not_signed_in": NOT_SIGNED_IN,
        "cloud_unavailable": CLOUD_UNAVAILABLE,
        "not_registered": NOT_REGISTERED,
        "project_not_found": NOT_REGISTERED,
        "instance_not_running": f"{kitchen} isn't ready. Pick it again once it is running.",
        "instance_forbidden": "Only the kitchen's owner can push to it.",
        "instance_not_found": "That kitchen is gone.",
        "transfer_rate_limited": f"Too many pushes. Try again in {_wait_text(retry_after)}.",
        "transfer_ticket_invalid": "The kitchen didn't accept the push. Try again.",
        "kitchen_busy": f"{kitchen} is busy with this project. Try again in a minute.",
        "already_on_kitchen": (f"{project} is already on {kitchen}. "
                               "Pushing again replaces it there."),
        "confirm_replace_data": CONFIRM_REPLACE,
        "kitchen_unreachable": (f"{kitchen} isn't answering, or runs an older Eggie. "
                                "Try again in a minute."),
        "kitchen_restarted": "The kitchen restarted during the push. Push again.",
    }.get(code)


class _LogOnly:
    """Forwards log lines but not phases, so the restart inside a push keeps
    the push's own phase captions."""

    def __init__(self, write):
        self._write = write

    def __call__(self, chunk: str) -> None:
        self._write(chunk)

    def phase(self, _name: str) -> None:
        pass


class PushService:
    def __init__(self, config: ApiConfig, runner, loader: ProjectLoader,
                 projects: ProjectRepo, secrets: SecretService,
                 lifecycle: LifecycleService, locks: ProjectLocks, account: Account,
                 cloud, kitchen: KitchenClient, cloud_projects: CloudProjectRepo,
                 kitchen_pushes: KitchenPushRepo, wake: Callable[[], None], *,
                 clock=time.time, sleep=time.sleep):
        self._config = config
        self._runner = runner
        self._loader = loader
        self._projects = projects
        self._secrets = secrets
        self._lifecycle = lifecycle
        self._locks = locks
        self._account = account
        self._cloud = cloud
        self._kitchen = kitchen
        self._cloud_projects = cloud_projects
        self._kitchen_pushes = kitchen_pushes
        self._wake = wake
        self._clock = clock
        self._sleep = sleep

    @property
    def _staging(self) -> Path:
        return Path(self._config.transfers_root) / "out"

    def sweep(self) -> None:
        """Bundles left behind by an API that stopped mid-push."""
        if not self._staging.is_dir():
            return
        for path in self._staging.iterdir():
            try:
                path.unlink()
            except OSError as e:
                log.warning("could not remove %s: %s", path, e)

    def targets(self, project_id: str) -> dict:
        row = self._loader.require(project_id)
        compose = self._loader.compose(project_id)
        try:
            reply = self._account.authed(self._cloud.transfer_targets)
        except NotSignedIn:
            raise Conflict("not_signed_in", NOT_SIGNED_IN) from None
        except CloudUnavailable:
            raise Unavailable("cloud_unavailable", CLOUD_UNAVAILABLE) from None
        except CloudError as e:
            raise Upstream("cloud_error", e.message) from None
        return {"kitchens": reply.get("kitchens") or [],
                "create_kitchen_url": reply.get("create_kitchen_url"),
                "pushed": self._kitchen_pushes.for_project(project_id),
                "databases": [asdict(d) for d in rules.databases(compose)],
                "running": row["status"] == STARTED_OK}

    def start(self, project_id: str, instance_id: str, *, data: bool,
              replace_data: bool) -> str:
        row = self._loader.require(project_id)
        compose = self._loader.compose(project_id)
        pushed_before = instance_id in self._kitchen_pushes.for_project(project_id)
        if data and pushed_before and not replace_data:
            raise Conflict("confirm_replace_data", CONFIRM_REPLACE)
        job = _PushJob(self, row, compose, instance_id, data=data,
                       replace=rules.replace_mode(pushed_before, data))
        return self._lifecycle.submit_locked(project_id, job.run, "push")

    def summary(self, project_id: str) -> dict:
        if self._config.site_host:
            blocked = "site"
        else:
            blocked = None if self._account.signed_in else "signed_out"
        records = self._kitchen_pushes.for_project(project_id)
        last = None
        if records:
            instance_id, rec = max(records.items(), key=lambda kv: kv[1]["pushed_at"])
            last = {"instance_id": instance_id, "console_hostname": rec["console_hostname"],
                    "pushed_at": rec["pushed_at"]}
        return {"blocked": blocked, "last": last}


class _PushJob:
    """One push, run on the job's thread with the project lock held."""

    def __init__(self, service: PushService, row: dict, compose: dict, instance_id: str,
                 *, data: bool, replace: str):
        self.s = service
        self.project_id = row["id"]
        self.instance_id = instance_id
        self.data = data
        self.replace = replace
        self.plan = rules.data_plan(compose)
        compose_name = row.get("compose_name") or str(compose.get("name") or row["id"])
        self.volumes = {key: rules.docker_volume_name(compose, compose_name, key)
                        for key in self.plan.volumes}
        self.kitchen_name = "The kitchen"
        self.write = None

    def run(self, write) -> dict:
        self.write = write
        bundle = self.s._staging / f"{write.job_id}.tar"
        manifest = self.s._staging / f"{write.job_id}.json"
        try:
            return self._steps(bundle, manifest)
        except EggieError as e:
            raise JobFailed(e.message, {"code": e.code, "retry_after": None}) from None
        finally:
            bundle.unlink(missing_ok=True)
            manifest.unlink(missing_ok=True)
            self.s._locks.release(self.project_id)

    def _fail(self, code: str, message: str | None = None,
              retry_after: int | None = None) -> JobFailed:
        text = message or _copy(code, kitchen=self.kitchen_name, project=self.project_id,
                                retry_after=retry_after) or code
        return JobFailed(text, {"code": code, "retry_after": retry_after})

    def _steps(self, bundle: Path, manifest: Path) -> dict:
        write = self.write
        write.phase("preparing")
        kitchen = self._kitchen_row()
        cloud_id = self._cloud_id()
        self._check_disk()
        stopped = False
        if self.data and self.s._projects.get(self.project_id)["status"] == STARTED_OK:
            write.phase("stopping")
            write(f"stopping {self.project_id} while its data is copied\n")
            try:
                self.s._lifecycle.down_now(self.project_id)
            except JobFailed as e:
                raise self._fail("stop_failed", str(e)) from None
            stopped = True
        local_restart = None
        try:
            write.phase("packing")
            size = self._pack(bundle, manifest)
        finally:
            if stopped:
                write.phase("starting")
                local_restart = self._restart()
        write.phase("sending")
        push_id, token = self._send(kitchen, cloud_id, bundle, size)
        write.phase("waiting")
        result = self._wait(kitchen["console_hostname"], push_id, token)
        return self._finish(kitchen, result, local_restart)

    def _service(self, fn):
        try:
            return self.s._account.authed(fn)
        except NotSignedIn:
            raise self._fail("not_signed_in") from None
        except CloudUnavailable:
            raise self._fail("cloud_unavailable") from None
        except CloudError as e:
            if e.code in MINT_CODES:
                raise self._fail(e.code, retry_after=e.retry_after) from None
            raise self._fail("cloud_error", e.message) from None

    def _kitchen_row(self) -> dict:
        reply = self._service(self.s._cloud.transfer_targets)
        for row in reply.get("kitchens") or []:
            if row.get("id") == self.instance_id:
                self.kitchen_name = row.get("display_name") or row["console_hostname"]
                return row
        raise self._fail("instance_not_found")

    def _cloud_id(self) -> str:
        found = self.s._cloud_projects.mapping().get(self.project_id)
        if found is None:
            self.s._wake()
            for _ in range(REGISTRATION_WAIT):
                self.s._sleep(1)
                found = self.s._cloud_projects.mapping().get(self.project_id)
                if found is not None:
                    break
            else:
                raise self._fail("not_registered")
        return found["cloud_id"]

    def _check_disk(self) -> None:
        need = files.tree_stats(self.s._loader.dir(self.project_id))["bytes"] + PACK_MARGIN
        if self.data:
            sizes = docker.volume_sizes(self.s._runner)
            need += sum(sizes.get(name, 0) for name in self.volumes.values())
        if disk.usage(self.s._staging)["free_bytes"] < need:
            raise DiskFull("disk_full", DISK_FULL)

    def _secret_names(self) -> dict[str, str]:
        listing = self.s._secrets.list(self.project_id)
        names = {s["name"]: "" for s in listing["secrets"]}
        names.update({r["name"]: r["hint"] for r in listing["requested"]})
        return names

    def _pack(self, bundle: Path, manifest: Path) -> int:
        image = docker.own_image(self.s._runner)
        if not image:
            raise self._fail("pack_failed", "The Eggie service could not find its own "
                                            "image to pack the project with.")
        if self.data and self.plan.external:
            self.write("not copying external volumes: "
                       f"{', '.join(self.plan.external)}\n")
        self.s._staging.mkdir(parents=True, exist_ok=True)
        manifest.write_text(json.dumps(rules.build_manifest(
            data=self.data, plan=self.plan, secrets=self._secret_names())))
        self.write(f"packing {self.project_id}" + (" with its data\n" if self.data else "\n"))
        result = self.s._runner.exec(pack_argv(
            image, out_dir=self.s._staging, project_dir=self.s._loader.dir(self.project_id),
            volumes=self.volumes, out_name=bundle.name,
            manifest_path=f"/out/{manifest.name}", include_data=self.data), root=True)
        if not result.ok:
            raise self._fail("pack_failed", (result.stderr or result.stdout).strip()
                             or "packing the project failed")
        return bundle.stat().st_size

    def _restart(self) -> str:
        try:
            work = self.s._lifecycle.start_work(self.project_id, stop_first=False,
                                               release=False)
            work(_LogOnly(self.write))
        except (JobFailed, EggieError) as e:
            self.write(f"{self.project_id} did not start again: {e}\n")
            return "failed"
        return "ok"

    def _remember(self, kitchen: dict) -> None:
        before = self.s._kitchen_pushes.for_project(self.project_id).get(self.instance_id) or {}
        self.s._kitchen_pushes.record(
            self.project_id, self.instance_id, console_hostname=kitchen["console_hostname"],
            kitchen_project=before.get("kitchen_project"), urls=before.get("urls", []),
            secrets_missing=before.get("secrets_missing", 0), at=self.s._clock())

    def _kitchen_failure(self, e: KitchenError) -> JobFailed:
        if e.code == "kitchen_disk_full":
            return self._fail(e.code, f"{self.kitchen_name} doesn't have room: {e.message} "
                                      "Try without data, or free space there.")
        if e.code in KITCHEN_CODES:
            return self._fail(e.code)
        return self._fail(e.code, e.message)

    def _send(self, kitchen: dict, cloud_id: str, bundle: Path, size: int) -> tuple[str, str]:
        host = kitchen["console_hostname"]
        ticket_retried, unreachable = False, 0
        while True:
            ticket = self._service(lambda token: self.s._cloud.mint_transfer_ticket(
                token, self.instance_id, cloud_id))["ticket"]
            try:
                opened = self.s._kitchen.open(host, ticket=ticket, size=size,
                                             data=self.data, replace=self.replace)
                self.write(f"sending {size} bytes to {host}\n")
                self.write.progress(0, size)
                self.s._kitchen.upload(host, opened["push_id"], opened["token"], bundle,
                                      lambda done: self.write.progress(done, size))
            except KitchenUnreachable as e:
                unreachable += 1
                self.write(f"{host} did not answer: {e}\n")
                if unreachable >= UNREACHABLE_ATTEMPTS:
                    raise self._fail("kitchen_unreachable") from None
                self.s._sleep(RETRY_DELAY)
                continue
            except KitchenError as e:
                if e.code == "transfer_ticket_invalid" and not ticket_retried:
                    ticket_retried = True
                    continue
                if e.code in ("already_on_kitchen", "confirm_replace_data"):
                    self._remember(kitchen)
                raise self._kitchen_failure(e) from None
            self._remember(kitchen)
            return opened["push_id"], opened["token"]

    def _wait(self, host: str, push_id: str, token: str) -> dict:
        misses = 0
        while True:
            try:
                status = self.s._kitchen.status(host, push_id, token)
            except KitchenUnreachable:
                misses += 1
                if misses >= UNREACHABLE_ATTEMPTS:
                    raise self._fail("kitchen_unreachable") from None
                self.s._sleep(STATUS_INTERVAL)
                continue
            except KitchenError as e:
                # The kitchen keeps jobs in memory: a restart forgets the push.
                if e.status == 404:
                    raise self._fail("kitchen_restarted") from None
                raise self._kitchen_failure(e) from None
            misses = 0
            if status.get("state") == DONE:
                return status.get("result") or {}
            if status.get("state") == FAILED:
                code = (status.get("result") or {}).get("code") or "kitchen_failed"
                raise self._fail(code, status.get("detail")
                                 or "The kitchen could not import the project.")
            if status.get("phase"):
                self.write.phase(status["phase"])
            self.s._sleep(STATUS_INTERVAL)

    def _finish(self, kitchen: dict, result: dict, local_restart: str | None) -> dict:
        kitchen_project = result.get("kitchen_project")
        urls = list(result.get("urls") or [])
        missing = int(result.get("secrets_missing") or 0)
        self.s._kitchen_pushes.record(
            self.project_id, self.instance_id, console_hostname=kitchen["console_hostname"],
            kitchen_project=kitchen_project, urls=urls, secrets_missing=missing,
            at=self.s._clock())
        open_secrets_url = None
        if missing > 0 and kitchen_project and kitchen.get("open_console_url"):
            open_secrets_url = (kitchen["open_console_url"] + "&return_path="
                                + urllib.parse.quote(f"/p/{kitchen_project}/secrets", safe=""))
        self.write(f"pushed to {self.kitchen_name}\n")
        return {"instance_id": self.instance_id, "kitchen": self.kitchen_name,
                "kitchen_project": kitchen_project, "urls": urls,
                "secrets_missing": missing, "open_secrets_url": open_secrets_url,
                "local_restart": local_restart}
