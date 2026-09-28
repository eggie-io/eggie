from __future__ import annotations

import logging
import os
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .account import NotSignedIn
from .cloud import CloudError, CloudUnavailable
from .lifecycle import DOCKER

log = logging.getLogger("omelet.public")

MESSAGES = {
    "signed_out": "Public addresses need an Omelet account. Sign in to use them.",
    "not_registered": "This project isn't linked to your account yet. Try again in a minute.",
    "no_web": "This project has no web page to share.",
    "public_url_active": "One is already on for another project or computer. "
                         "Turn it off there first.",
    "public_url_unavailable": "Your plan doesn't include public addresses.",
    "cloud_unavailable": "The Omelet service couldn't be reached. Check the "
                         "internet connection and try again.",
    "client_failed": "The public connection couldn't start on this computer.",
    "interrupted": "Omelet restarted while turning this on. Try again.",
    "expired": "The public address expired. Start a new one; it will be a "
               "different address.",
    "released_elsewhere": "The public address was turned off from the Omelet website.",
    "project_not_found": "This project isn't linked to your account yet. Try again in a minute.",
    "device_required": "This computer's sign-in can't make public addresses. "
                       "Sign out and sign in again.",
    "tunnel_provider_error": "The public address couldn't be set up. "
                             "Try again in a few minutes.",
    "public_urls_disabled": "Public addresses are switched off on the Omelet "
                            "service right now.",
    "validation_error": "The Omelet service couldn't accept this project's addresses.",
}


class PublicBusy(Exception):
    pass


class Unavailable(Exception):
    def __init__(self, code: str):
        super().__init__(MESSAGES[code])
        self.code = code
        self.message = MESSAGES[code]


def _daemon(fn) -> None:
    threading.Thread(target=fn, name="omelet-public", daemon=True).start()


def write_token(path: Path, token: str) -> bool:
    """False when the file already holds this token (nothing written)."""
    try:
        if path.read_text() == token:
            # The installer's group-write sweep may have widened it.
            os.chmod(path, 0o640)
            return False
    except OSError:
        pass
    path.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
    # fchmod sets the mode before any byte is written, independent of umask.
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tunnel-")
    try:
        with os.fdopen(fd, "w") as f:
            os.fchmod(f.fileno(), 0o640)
            f.write(token)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return True


def remove_token(path: Path) -> None:
    path.unlink(missing_ok=True)


class TunnelClient:
    def __init__(self, runner, stack_file: Path):
        self._runner = runner
        self._base = [DOCKER, "compose", "-f", str(stack_file), "--profile", "tunnel"]

    def start(self, *, recreate: bool = False):
        # cloudflared reads its token once; a running client needs recreating
        # to pick up a new one.
        extra = ["--force-recreate"] if recreate else []
        return self._runner.exec([*self._base, "up", "-d", "--no-deps", *extra,
                                  "tunnel"])

    def stop(self):
        return self._runner.exec([*self._base, "rm", "-sf", "tunnel"])

    def running(self) -> bool:
        out = self._runner.exec([*self._base, "ps", "-q", "tunnel"])
        return out.ok and bool(out.stdout.strip())


def _epoch(value: str | None) -> float | None:
    if value is None:
        return None
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _expired(row: dict, now: float) -> bool:
    # A null expiry means the service set no time limit.
    return row["expires_at"] is not None and row["expires_at"] <= now


def _reason(code: str, message: str | None = None) -> dict:
    return {"code": code, "message": message or MESSAGES[code]}


class Public:
    def __init__(self, *, state, account, cloud, client: TunnelClient,
                 token_path: Path, origin: str,
                 hosts_for: Callable[[str], list[dict]],
                 clock=time.time, spawn=_daemon):
        self._state = state
        self._account = account
        self._cloud = cloud
        self._client = client
        self._token_path = Path(token_path)
        self._origin = origin
        self._hosts_for = hosts_for
        self._clock = clock
        self._spawn = spawn
        self._lock = threading.Lock()
        # Serialises the client's stop-and-remove-token against reconcile's
        # read-then-start. Never taken while holding _lock.
        self._client_lock = threading.Lock()
        self._enabling: set[str] = set()

    # --- reading ---------------------------------------------------------

    def _blocked(self, local_id: str) -> str | None:
        if not self._account.signed_in:
            return "signed_out"
        if local_id not in self._state.cloud_mapping():
            return "not_registered"
        if not self._hosts_for(local_id):
            return "no_web"
        return None

    def status(self, local_id: str) -> dict:
        row = self._state.get_public(local_id)
        if row is not None:
            if row["state"] == "on":
                if _expired(row, self._clock()):
                    return {"state": "off", "note": _reason("expired")}
                return {"state": "on", "urls": row["urls"],
                        "expires_at": row["expires_at"]}
            if row["state"] == "enabling":
                return {"state": "enabling"}
            if row["state"] == "failed" or (row["state"] == "releasing"
                                            and row["reason_code"]):
                return {"state": "failed", "reason": _reason(
                    row["reason_code"], row["reason_message"])}
        blocked = self._blocked(local_id)
        if blocked:
            return {"state": "unavailable", "reason": _reason(blocked)}
        if row is not None and row["state"] == "ended":
            return {"state": "off", "note": _reason(row["reason_code"])}
        return {"state": "off", "note": None}

    # --- turning on ------------------------------------------------------

    def enable(self, local_id: str) -> dict:
        row = self._state.get_public(local_id)
        if row is not None and row["state"] == "on" and _expired(row, self._clock()):
            self._end(row, "expired")
        with self._lock:
            if local_id in self._enabling:
                raise PublicBusy(local_id)
            row = self._state.get_public(local_id)
            if row is not None and row["state"] == "on":
                return self.status(local_id)
            blocked = self._blocked(local_id)
            if blocked:
                raise Unavailable(blocked)
            cloud_id = self._state.cloud_mapping()[local_id]["cloud_id"]
            prior = row
            self._enabling.add(local_id)
            self._state.put_public(local_id, cloud_id=cloud_id, state="enabling")
        self._spawn(lambda: self._run_enable(local_id, cloud_id, prior))
        return self.status(local_id)

    def _run_enable(self, local_id: str, cloud_id: str, prior: dict | None) -> None:
        try:
            if prior is not None and prior["state"] == "releasing":
                if not self._release(prior["cloud_id"]):
                    self._state.transition_public(local_id, "enabling",
                                                   state="releasing",
                                                   reason_code="cloud_unavailable")
                    return
            self._turn_on(local_id, cloud_id)
        except Exception:
            log.exception("turning on the public URL of %s failed", local_id)
            self._fail(local_id, cloud_id, "client_failed")
        finally:
            with self._lock:
                self._enabling.discard(local_id)

    def _turn_on(self, local_id: str, cloud_id: str) -> None:
        hosts = self._hosts_for(local_id)
        try:
            routes = [{"local_hostname": h["hostname"], "service": h["service"]}
                      for h in hosts]
            out = self._account.authed(lambda token: self._cloud.create_public_url(
                token, cloud_id, routes, self._origin))
        except NotSignedIn:
            self._state.delete_public(local_id)
            return
        except CloudUnavailable:
            self._fail(local_id, cloud_id, "cloud_unavailable")
            return
        except CloudError as e:
            self._fail_from_service(local_id, cloud_id, e)
            return

        # Everything past this point holds a live service-side URL: any
        # failure here -- a bad client start, a malformed reply -- must
        # release it, not just mark the row failed.
        try:
            credentials = out.get("credentials")
            if credentials:
                changed = write_token(self._token_path, credentials["token"])
            elif self._token_path.exists():
                # An active-session reply may omit the token this VM already holds.
                changed = False
            else:
                raise RuntimeError("the service sent no tunnel token")
            started = self._client.start(recreate=changed)
            if not started.ok:
                raise RuntimeError("tunnel client failed to start")
            by_host = {h["hostname"]: h for h in hosts}
            urls = [{"url": u["url"],
                     "service": by_host[u["local_hostname"]]["service"],
                     "local_url": by_host[u["local_hostname"]]["local_url"]}
                    for u in out["urls"] if u["local_hostname"] in by_host]
            expires_at = _epoch(out["expires_at"])
        except Exception:
            self._stop_unless_needed(local_id)
            self._release(cloud_id)
            self._fail(local_id, cloud_id, "client_failed")
            return

        # The row may have been force-disabled or cleared (sign-out) while
        # the create/start calls were in flight; only take the "on" write if
        # it is still the same "enabling" attempt, or a wanted-off row would
        # come back on.
        committed = self._state.transition_public(
            local_id, "enabling", state="on", urls=urls, expires_at=expires_at)
        if not committed:
            self._stop_unless_needed(local_id)
            self._release(cloud_id)

    def _fail(self, local_id: str, cloud_id: str, code: str,
              message: str | None = None) -> None:
        # Only takes if the row is still this attempt's "enabling" row, so a
        # row already moved or deleted by a disable/sign-out is left alone.
        self._state.transition_public(local_id, "enabling", state="failed",
                                      reason_code=code,
                                      reason_message=message or MESSAGES.get(code))

    def _fail_from_service(self, local_id: str, cloud_id: str, e: CloudError) -> None:
        # The UI shows our wording only; the service's own reason lives here.
        log.warning("the service refused a public URL for %s (%s): %s %s",
                    local_id, cloud_id, e.status, e)
        if e.code == "public_url_active":
            holder = next((r["local_id"] for r in self._state.list_public()
                           if r["state"] == "on" and r["local_id"] != local_id), None)
            message = (f'Only one public address can be on at a time. Turn off the '
                       f'one on "{holder}" first.') if holder else None
            self._fail(local_id, cloud_id, e.code, message)
        elif e.code in MESSAGES:
            self._fail(local_id, cloud_id, e.code)
        else:
            self._fail(local_id, cloud_id, e.code,
                       f"The Omelet service refused: {e.message}")

    # --- turning off -----------------------------------------------------

    def disable(self, local_id: str, *, force: bool = False) -> dict:
        with self._lock:
            if local_id in self._enabling and not force:
                raise PublicBusy(local_id)
        row = self._state.get_public(local_id)
        if row is None:
            return self.status(local_id)
        self._state.delete_public(local_id)
        self._stop_unless_needed(local_id)
        if row["state"] in ("on", "releasing", "enabling"):
            if not self._release(row["cloud_id"]):
                self._state.put_public(local_id, cloud_id=row["cloud_id"],
                                       state="releasing")
        return self.status(local_id)

    def _release(self, cloud_id: str) -> bool:
        """True once the service has no live URL for it."""
        try:
            self._account.authed(
                lambda token: self._cloud.release_public_url(token, cloud_id))
        except NotSignedIn:
            return True  # nothing can release it now; the service's expiry will
        except CloudError as e:
            if e.status != 404:
                log.warning("releasing public URL %s failed: %s", cloud_id, e)
                return False
        except CloudUnavailable:
            return False
        return True

    def _stop_unless_needed(self, local_id: str) -> None:
        with self._lock:
            if self._enabling - {local_id}:
                return  # another project may be about to need the client
        with self._client_lock:
            if any(r["state"] == "on" and r["local_id"] != local_id
                   for r in self._state.list_public()):
                return
            self._client.stop()
            remove_token(self._token_path)

    # --- keeping it true -------------------------------------------------

    def reconcile(self) -> None:
        projects = {row["id"] for row in self._state.list_projects()}
        for row in self._state.list_public():
            try:
                self._reconcile_row(row, projects)
            except Exception:
                log.exception("reconciling the public URL of %s failed",
                              row["local_id"])
        with self._lock:
            # A concurrent enable may commit "on" and start the client right
            # after this check; deciding the client's state here too could
            # race it (stop what it just started, or skip a start it needs).
            if self._enabling:
                return
        self._reconcile_client()

    def _reconcile_client(self) -> None:
        with self._client_lock:
            on_rows = [r for r in self._state.list_public() if r["state"] == "on"]
            running = self._client.running()
            if not on_rows:
                if running or self._token_path.exists():
                    self._client.stop()
                    remove_token(self._token_path)
                return
            if running:
                return
            if self._token_path.exists():
                started = self._client.start()
                if not started.ok:
                    log.warning("restarting the tunnel client failed: %s",
                                started.stderr)
                return
        # Outside the lock: _end stops the client through _stop_unless_needed.
        for row in on_rows:
            if self._end(row, "client_failed"):
                log.warning("public URL of %s had no tunnel token; ended it",
                            row["local_id"])
                self._release(row["cloud_id"])

    def _reconcile_row(self, row: dict, projects: set[str]) -> None:
        local_id, state = row["local_id"], row["state"]
        with self._lock:
            if local_id in self._enabling:
                return
        # An enable may have finished since the snapshot; act only on the row
        # as it still is.
        current = self._state.get_public(local_id)
        if current is None or current["state"] != state:
            return
        if local_id not in projects:
            self.disable(local_id, force=True)
            return
        if state == "on":
            if _expired(row, self._clock()):
                self._end(row, "expired")
                return
            try:
                self._account.authed(lambda token: self._cloud.get_public_url(
                    token, row["cloud_id"]))
            except CloudError as e:
                if e.status == 404:
                    self._end(row, "released_elsewhere")
                else:
                    log.warning("checking public URL %s failed: %s",
                                row["cloud_id"], e)
            except (CloudUnavailable, NotSignedIn):
                pass
        elif state == "releasing":
            if self._release(row["cloud_id"]):
                self._state.delete_public_if(local_id, "releasing")
        elif state == "enabling":
            if self._release(row["cloud_id"]):
                self._fail(local_id, row["cloud_id"], "interrupted")
            else:
                self._state.transition_public(local_id, "enabling",
                                               state="releasing",
                                               reason_code="interrupted")

    def _end(self, row: dict, code: str) -> bool:
        # Guarded: the row may have moved (a fresh enable, a disable) since
        # this snapshot was read, e.g. across the get_public_url call above.
        ended = self._state.transition_public(row["local_id"], "on",
                                              state="ended", reason_code=code)
        if ended:
            self._stop_unless_needed(row["local_id"])
        return ended

    def release_all(self) -> None:
        for row in self._state.list_public():
            if row["state"] in ("on", "enabling", "releasing"):
                self._release(row["cloud_id"])

    def forget_local(self) -> None:
        self._state.clear_public()
        with self._client_lock:
            self._client.stop()
            remove_token(self._token_path)
