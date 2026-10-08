from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .account import NotSignedIn
from ..errors import Conflict
from ..infra.cloud import CloudError, CloudUnavailable
from ..infra.tunnel import TunnelClient, remove_token, write_token

log = logging.getLogger("eggie.public")

MESSAGES = {
    "signed_out": "Public addresses need an Eggie account. Sign in to use them.",
    "not_registered": "This project isn't linked to your account yet. Try again in a minute.",
    "no_web": "This project has no web page to share.",
    "public_url_active": "One is already on for another project or computer. "
                         "Turn it off there first.",
    "public_url_unavailable": "Your plan doesn't include public addresses.",
    "cloud_unavailable": "The Eggie service couldn't be reached. Check the "
                         "internet connection and try again.",
    "client_failed": "The public connection couldn't start on this computer.",
    "expired": "The public address expired. Start a new one; it will be a "
               "different address.",
    "released_elsewhere": "The public address was turned off from the Eggie website.",
    "project_not_found": "This project isn't linked to your account yet. Try again in a minute.",
    "device_required": "This computer's sign-in can't make public addresses. "
                       "Sign out and sign in again.",
    "tunnel_provider_error": "The public address couldn't be set up. "
                             "Try again in a few minutes.",
    "public_urls_disabled": "Public addresses are switched off on the Eggie "
                            "service right now.",
    "validation_error": "The Eggie service couldn't accept this project's addresses.",
}


class PublicBusy(Conflict):
    def __init__(self, local_id: str):
        super().__init__("project_busy",
                         f"another operation on '{local_id}' is still running")


class PublicUnavailable(Conflict):
    def __init__(self, code: str):
        super().__init__(code, MESSAGES[code])


def _daemon(fn) -> None:
    threading.Thread(target=fn, name="eggie-public", daemon=True).start()


def _epoch(value: str | None) -> float | None:
    if value is None:
        return None
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _expired(record: dict, now: float) -> bool:
    # A null expiry means the service set no time limit.
    return record["expires_at"] is not None and record["expires_at"] <= now


def _reason(code: str, message: str | None = None) -> dict:
    return {"code": code, "message": message or MESSAGES[code]}


class _Attempt:
    """One turn-on; compared by identity so a stale thread can't commit."""

    def __init__(self, cloud_id: str):
        self.cloud_id = cloud_id


class Public:
    """The service is the record of which URLs are on. This keeps only what a
    restart may lose: the last record seen per project, an in-flight turn-on,
    a failure or an ended note to show, and releases still to retry."""

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
        # Guards the dicts below. _client_lock may take it, never the reverse.
        self._lock = threading.Lock()
        self._client_lock = threading.Lock()
        self._on: dict[str, dict] = {}
        self._enabling: dict[str, _Attempt] = {}
        self._failed: dict[str, dict] = {}
        self._notes: dict[str, dict] = {}
        self._releasing: set[str] = set()

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
        with self._lock:
            enabling = local_id in self._enabling
            record = self._on.get(local_id)
            failed = self._failed.get(local_id)
            note = self._notes.get(local_id)
        if enabling:
            return {"state": "enabling"}
        if record is not None:
            if _expired(record, self._clock()):
                return {"state": "off", "note": _reason("expired")}
            return {"state": "on", "urls": record["urls"],
                    "expires_at": record["expires_at"]}
        if failed is not None:
            return {"state": "failed", "reason": failed}
        blocked = self._blocked(local_id)
        if blocked:
            return {"state": "unavailable", "reason": _reason(blocked)}
        return {"state": "off", "note": note}

    def _record(self, local_id: str, cloud_id: str, out: dict) -> dict:
        by_host = {h["hostname"]: h for h in self._hosts_for(local_id)}
        urls = [{"url": u["url"],
                 "service": by_host[u["local_hostname"]]["service"],
                 "local_url": by_host[u["local_hostname"]]["local_url"]}
                for u in out["urls"] if u["local_hostname"] in by_host]
        return {"cloud_id": cloud_id, "urls": urls,
                "expires_at": _epoch(out["expires_at"])}

    # --- turning on ------------------------------------------------------

    def enable(self, local_id: str) -> dict:
        with self._lock:
            if local_id in self._enabling:
                raise PublicBusy(local_id)
            record = self._on.get(local_id)
            live = record is not None and not _expired(record, self._clock())
        if live:
            return self.status(local_id)
        blocked = self._blocked(local_id)
        if blocked:
            raise PublicUnavailable(blocked)
        cloud_id = self._state.cloud_mapping()[local_id]["cloud_id"]
        attempt = _Attempt(cloud_id)
        with self._lock:
            if local_id in self._enabling:
                raise PublicBusy(local_id)
            self._enabling[local_id] = attempt
            self._on.pop(local_id, None)
            self._failed.pop(local_id, None)
            self._notes.pop(local_id, None)
        self._spawn(lambda: self._run_enable(local_id, attempt))
        return self.status(local_id)

    def _current(self, local_id: str, attempt: _Attempt) -> bool:
        return self._enabling.get(local_id) is attempt

    def _run_enable(self, local_id: str, attempt: _Attempt) -> None:
        try:
            self._turn_on(local_id, attempt)
        except Exception:
            log.exception("turning on the public URL of %s failed", local_id)
            self._fail(local_id, attempt, "client_failed")
        finally:
            with self._lock:
                if self._current(local_id, attempt):
                    del self._enabling[local_id]

    def _turn_on(self, local_id: str, attempt: _Attempt) -> None:
        cloud_id = attempt.cloud_id
        hosts = self._hosts_for(local_id)
        try:
            routes = [{"local_hostname": h["hostname"], "service": h["service"]}
                      for h in hosts]
            out = self._account.authed(lambda token: self._cloud.create_public_url(
                token, cloud_id, routes, self._origin))
        except NotSignedIn:
            return
        except CloudUnavailable:
            self._fail(local_id, attempt, "cloud_unavailable")
            return
        except CloudError as e:
            self._fail_from_service(local_id, attempt, e)
            return

        # Everything past this point holds a live service-side URL: any
        # failure here -- a bad client start, a malformed reply -- must
        # release it, not just report a failure.
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
            record = self._record(local_id, cloud_id, out)
        except Exception:
            log.exception("starting the public URL of %s failed", local_id)
            if not self._superseded(local_id, attempt):
                self._stop_unless_needed(local_id)
                self._release(cloud_id)
            self._fail(local_id, attempt, "client_failed")
            return

        # A disable or sign-out may have landed while the calls were in
        # flight; then this attempt is no longer current and must undo itself.
        with self._lock:
            committed = self._current(local_id, attempt)
            if committed:
                self._on[local_id] = record
        if not committed and not self._superseded(local_id, attempt):
            self._stop_unless_needed(local_id)
            self._release(cloud_id)

    def _superseded(self, local_id: str, attempt: _Attempt) -> bool:
        # A newer turn-on of the same project (after a force-disable) holds
        # the same service URL and the client; a stale attempt leaves both.
        with self._lock:
            return not self._current(local_id, attempt) and (
                local_id in self._enabling or local_id in self._on)

    def _fail(self, local_id: str, attempt: _Attempt, code: str,
              message: str | None = None) -> None:
        with self._lock:
            if self._current(local_id, attempt):
                self._failed[local_id] = _reason(code, message)

    def _fail_from_service(self, local_id: str, attempt: _Attempt,
                           e: CloudError) -> None:
        # The UI shows our wording only; the service's own reason lives here.
        log.warning("the service refused a public URL for %s (%s): %s %s",
                    local_id, attempt.cloud_id, e.status, e)
        if e.code == "public_url_active":
            with self._lock:
                holder = next((other for other in self._on if other != local_id), None)
            message = (f'Only one public address can be on at a time. Turn off the '
                       f'one on "{holder}" first.') if holder else None
            self._fail(local_id, attempt, e.code, message)
        elif e.code in MESSAGES:
            self._fail(local_id, attempt, e.code)
        else:
            self._fail(local_id, attempt, e.code,
                       f"The Eggie service refused: {e.message}")

    # --- turning off -----------------------------------------------------

    def disable(self, local_id: str, *, force: bool = False) -> dict:
        with self._lock:
            if local_id in self._enabling and not force:
                raise PublicBusy(local_id)
            self._enabling.pop(local_id, None)
            record = self._on.pop(local_id, None)
            self._failed.pop(local_id, None)
            self._notes.pop(local_id, None)
        # A dropped in-flight attempt releases its own URL when it sees that.
        if record is not None:
            self._stop_unless_needed(local_id)
            if not self._release(record["cloud_id"]):
                # The stopped client already takes the address offline; the
                # service still counts it against the one-per-account limit.
                with self._lock:
                    self._releasing.add(record["cloud_id"])
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

    def _others_on(self, local_id: str) -> bool:
        now = self._clock()
        with self._lock:
            return (any(other != local_id for other in self._enabling)
                    or any(other != local_id and not _expired(r, now)
                           for other, r in self._on.items()))

    def _stop_unless_needed(self, local_id: str) -> None:
        with self._client_lock:
            if self._others_on(local_id):
                return
            self._client.stop()
            remove_token(self._token_path)

    # --- keeping it true -------------------------------------------------

    def reconcile(self) -> None:
        if not self._account.signed_in:
            return
        with self._lock:
            pending = set(self._releasing)
        for cloud_id in pending:
            if self._release(cloud_id):
                with self._lock:
                    self._releasing.discard(cloud_id)
        if not self._refresh():
            return  # the service didn't answer; leave the client as it is
        with self._lock:
            # A turn-on starts the client before it records the URL; deciding
            # the client's state now could stop what it just started.
            if self._enabling:
                return
        self._reconcile_client()

    def _refresh(self) -> bool:
        """Re-reads the account's active URL from the service. False if it
        couldn't be read, so nothing may be concluded from this pass."""
        # Taken before the call: a turn-on or turn-off that lands during it
        # replaces these records and wins over the answer.
        with self._lock:
            befores = dict(self._on)
        try:
            out = self._account.authed(self._cloud.active_public_url)
        except CloudError as e:
            if e.status != 404:
                log.warning("checking the active public URL failed: %s", e)
                return False
            out = None
        except (CloudUnavailable, NotSignedIn):
            return False
        projects = {row["id"] for row in self._state.list_projects()}
        by_cloud = {m["cloud_id"]: local_id
                    for local_id, m in self._state.cloud_mapping().items()}
        # Another device's URL, or one being released here, is not this VM's.
        active = by_cloud.get(out["project_id"]) if out else None
        with self._lock:
            if active is not None and (active not in projects
                                       or out["project_id"] in self._releasing):
                active = None
        for local_id, before in befores.items():
            if local_id != active:
                self._forget(local_id, before, None)
        if active is None:
            return True
        try:
            record = self._record(active, out["project_id"], out)
        except Exception:
            log.exception("reading the public URL of %s failed", active)
            return False
        with self._lock:
            if active not in self._enabling and self._on.get(active) is befores.get(active):
                self._on[active] = record
                self._failed.pop(active, None)
                self._notes.pop(active, None)
        return True

    def _forget(self, local_id: str, before: dict | None, code: str | None) -> None:
        """Drops a URL the service no longer has and says why -- only if it is
        still the record `before`, not one a newer turn-on put in its place."""
        now = self._clock()
        with self._lock:
            if before is None or self._on.get(local_id) is not before:
                return
            del self._on[local_id]
            if code is None:
                code = "expired" if _expired(before, now) else "released_elsewhere"
            self._notes[local_id] = _reason(code)

    def _reconcile_client(self) -> None:
        now = self._clock()
        with self._lock:
            live = [(k, r) for k, r in self._on.items() if not _expired(r, now)]
        with self._client_lock:
            running = self._client.running()
            if not live:
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
        # A URL is on but this VM holds no token to serve it: end it.
        for local_id, record in live:
            log.warning("public URL of %s had no tunnel token; ended it", local_id)
            self._forget(local_id, record, "client_failed")
            if not self._release(record["cloud_id"]):
                with self._lock:
                    self._releasing.add(record["cloud_id"])

    def release_all(self) -> None:
        with self._lock:
            cloud_ids = ({r["cloud_id"] for r in self._on.values()}
                         | {a.cloud_id for a in self._enabling.values()}
                         | self._releasing)
        for cloud_id in cloud_ids:
            self._release(cloud_id)

    def forget_local(self) -> None:
        with self._lock:
            self._on.clear()
            self._enabling.clear()
            self._failed.clear()
            self._notes.clear()
            self._releasing.clear()
        with self._client_lock:
            self._client.stop()
            remove_token(self._token_path)
