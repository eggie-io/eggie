from __future__ import annotations

import logging
import threading
import time

from .cloud import CloudError, CloudUnavailable

CLIENT_NAME = "Omelet"
REFRESH_MARGIN = 60.0
SLOW_DOWN_STEP = 5.0
DEFAULT_INTERVAL = 5.0
_REFUSED = {"access_denied", "expired_token", "invalid_grant"}
_CLEARED_CODE = {"device_code": None, "user_code": None, "verification_url": None,
                 "code_expires_at": None, "poll_interval": None}

log = logging.getLogger("omelet.account")


class NotSignedIn(Exception):
    pass


def _daemon(fn) -> None:
    threading.Thread(target=fn, name="omelet-sign-in", daemon=True).start()


class Account:
    def __init__(self, state, cloud, *, clock=time.time, sleep=time.sleep,
                 spawn=_daemon):
        self._state = state
        self._cloud = cloud
        self._clock = clock
        self._sleep = sleep
        self._spawn = spawn
        self.on_signed_in = lambda: None
        self.on_forget = lambda: None
        self._refresh_lock = threading.Lock()
        self._poll_lock = threading.Lock()
        # Reentrant: _refresh's invalid_grant branch calls _forget while
        # already holding this lock.
        self._write_lock = threading.RLock()
        self._polling = False

    @property
    def signed_in(self) -> bool:
        return bool(self._state.get_account()["access_token"])

    @property
    def device_id(self) -> str:
        return self._state.get_account()["device_id"]

    def status(self) -> dict:
        row = self._state.get_account()
        if row["access_token"]:
            return {"state": "signed_in", "email": row["email"],
                    "sync": {"last_ok_at": row["sync_ok_at"],
                             "last_error": row["sync_error"]}}
        if row["device_code"]:
            return {"state": "pending", "user_code": row["user_code"],
                    "url": row["verification_url"],
                    "expires_at": row["code_expires_at"]}
        return {"state": "signed_out", "error": row["last_error"]}

    def start_sign_in(self) -> dict:
        row = self._state.get_account()
        if row["access_token"]:
            return self.status()
        if not (row["device_code"] and row["code_expires_at"] > self._clock()):
            out = self._cloud.device_code(CLIENT_NAME)
            with self._write_lock:
                # A sign-in may have landed while the service call was in
                # flight; a fresh code must not overwrite a signed-in row.
                if self._state.get_account()["access_token"]:
                    return self.status()
                self._state.update_account(
                    device_code=out["device_code"], user_code=out["user_code"],
                    verification_url=out["verification_uri_complete"],
                    code_expires_at=self._clock() + out["expires_in"],
                    poll_interval=float(out["interval"]), last_error=None)
        self._ensure_poller()
        return self.status()

    def resume(self) -> None:
        if self._state.get_account()["device_code"]:
            self._ensure_poller()

    def _ensure_poller(self) -> None:
        with self._poll_lock:
            if self._polling:
                return
            self._polling = True
        self._spawn(self._run_poller)

    def _run_poller(self) -> None:
        while True:
            try:
                wait = self.poll_once()
            except Exception:
                log.exception("sign-in poll failed")
                wait = DEFAULT_INTERVAL
            if wait is not None:
                self._sleep(wait)
                continue
            # Checked under the lock start_sign_in's spawn takes: a new code
            # stored after the last poll must not be left with no poller.
            with self._poll_lock:
                if not self._state.get_account()["device_code"]:
                    self._polling = False
                    return

    def poll_once(self) -> float | None:
        with self._write_lock:
            row = self._state.get_account()
            if not row["device_code"]:
                return None
            polled_code = row["device_code"]
            interval = row["poll_interval"] or DEFAULT_INTERVAL
            if self._clock() >= row["code_expires_at"]:
                self._state.update_account(**_CLEARED_CODE, last_error="expired_token")
                return None

        try:
            tokens = self._cloud.device_token(polled_code)
            error = None
        except CloudUnavailable:
            return interval
        except CloudError as e:
            tokens, error = None, e

        with self._write_lock:
            row = self._state.get_account()
            if row["device_code"] != polled_code:
                # A sign-out or a fresh start_sign_in moved past this code
                # while the service call was in flight: this reply belongs to
                # a code no one is waiting on any more.
                return None if not row["device_code"] else (
                    row["poll_interval"] or DEFAULT_INTERVAL)
            if error is not None:
                if error.code == "slow_down":
                    interval += SLOW_DOWN_STEP
                    self._state.update_account(poll_interval=interval)
                    return interval
                if error.code in _REFUSED:
                    self._state.update_account(**_CLEARED_CODE, last_error=error.code)
                    return None
                return interval
            self._state.update_account(
                **_CLEARED_CODE, last_error=None, email=None, org_id=None,
                access_token=tokens["access_token"],
                refresh_token=tokens["refresh_token"],
                access_expires_at=self._clock() + tokens["expires_in"])

        try:
            self.load_identity()
        except (CloudError, CloudUnavailable, NotSignedIn):
            # The sync pass asks again; the sign-in itself has succeeded.
            log.warning("signed in, but could not read the account yet")
        finally:
            self.on_signed_in()
        return None

    def load_identity(self) -> str:
        row = self._state.get_account()
        if row["org_id"]:
            return row["org_id"]
        used: list[str] = []

        def call(token: str):
            used.append(token)
            return self._cloud.me(token)

        me = self.authed(call)
        with self._write_lock:
            # A sign-out (or a sign-in as someone else) during the call means
            # this reply belongs to a session that is no longer the stored one.
            if self._state.get_account()["access_token"] != used[-1]:
                raise NotSignedIn()
            self._state.update_account(email=me["user"]["email"],
                                       org_id=me["current_org_id"])
        return me["current_org_id"]

    def authed(self, fn):
        row = self._state.get_account()
        token = row["access_token"]
        if not token:
            raise NotSignedIn()
        if row["access_expires_at"] - self._clock() < REFRESH_MARGIN:
            token = self._refresh(token)
        try:
            return fn(token)
        except CloudError as e:
            if e.status != 401 or e.code != "invalid_token":
                raise
        return fn(self._refresh(token))

    def _refresh(self, stale: str) -> str:
        with self._refresh_lock:
            with self._write_lock:
                row = self._state.get_account()
                if not row["access_token"]:
                    raise NotSignedIn()
                # Another thread refreshed while this one waited; refreshing
                # again would spend a refresh token the service may already
                # have rotated.
                if row["access_token"] != stale:
                    return row["access_token"]
                refresh_token = row["refresh_token"]

            try:
                out = self._cloud.refresh(refresh_token)
                error = None
            except CloudError as e:
                out, error = None, e

            with self._write_lock:
                row = self._state.get_account()
                if not row["access_token"]:
                    raise NotSignedIn()
                if row["access_token"] != stale:
                    # A sign-out or another refresh landed while the service
                    # call was in flight; this reply is no longer ours to act on.
                    return row["access_token"]
                if error is not None:
                    if error.code == "invalid_grant":
                        self._forget("revoked")
                        raise NotSignedIn() from None
                    raise error
                self._state.update_account(
                    access_token=out["access_token"],
                    refresh_token=out["refresh_token"],
                    access_expires_at=self._clock() + out["expires_in"])
                return out["access_token"]

    def record_sync(self, *, ok_at: float | None = None, error: str | None = None) -> None:
        with self._write_lock:
            if not self._state.get_account()["access_token"]:
                return
            fields = {"sync_error": error}
            if ok_at is not None:
                fields["sync_ok_at"] = ok_at
            self._state.update_account(**fields)

    def sign_out(self) -> dict:
        token = self._state.get_account()["access_token"]
        if token:
            try:
                self._cloud.logout(token)
            except (CloudError, CloudUnavailable):
                log.warning("the service did not confirm the sign-out")
        self._forget(None)
        return self.status()

    def _forget(self, error: str | None) -> None:
        with self._write_lock:
            self._state.update_account(
                **_CLEARED_CODE, email=None, org_id=None, access_token=None,
                refresh_token=None, access_expires_at=None, last_error=error,
                sync_ok_at=None, sync_error=None)
            self._state.clear_cloud_projects()
        try:
            self.on_forget()
        except Exception:
            log.exception("the forget hook failed")
