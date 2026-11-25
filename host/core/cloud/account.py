"""The desktop app's own sign-in to the Eggie service (device-code flow)."""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from host.client import ApiError, ApiUnavailableError

REFRESH_MARGIN = 60.0
SLOW_DOWN_STEP = 5.0
_ENDED = {"access_denied": "denied", "expired_token": "expired", "invalid_grant": "expired"}


class NotSignedIn(Exception):
    def __init__(self, message: str = "Open the Eggie app and sign in first."):
        super().__init__(message)


@dataclass(frozen=True)
class SignIn:
    user_code: str
    url: str
    expires_at: float
    interval: float


class CloudAccount:
    def __init__(self, path: Path, *, call, clock=time.time, sleep=time.sleep):
        self._path = path
        self._call = call
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._pending: SignIn | None = None
        self._device_code = ""

    def _read(self) -> dict:
        try:
            raw = json.loads(self._path.read_text())
            if not raw.get("refresh_token"):
                return {}
            return raw
        except (OSError, ValueError, AttributeError):
            return {}

    def _write(self, data: dict) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # mkstemp makes the file 0600 from the start and gives `eggie connect` its own name.
        fd, tmp = tempfile.mkstemp(dir=self._path.parent, prefix=f".{self._path.name}.")
        try:
            with os.fdopen(fd, "w") as f:
                f.write(json.dumps(data))
            os.replace(tmp, self._path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    @property
    def signed_in(self) -> bool:
        return bool(self._read())

    @property
    def email(self) -> str:
        return self._read().get("email", "")

    @property
    def first_name(self) -> str:
        return self._read().get("first_name", "")

    @property
    def device_id(self) -> str:
        return self._read().get("device_id", "")

    @property
    def pending(self) -> SignIn | None:
        if self._pending and self._clock() >= self._pending.expires_at:
            self._pending = None
        return self._pending

    def start(self, client_name: str = "Eggie Desktop") -> SignIn:
        if self.pending:
            return self._pending
        out = self._call("POST", "/v1/auth/device/code", {"client_name": client_name}, None)
        self._device_code = out["device_code"]
        self._pending = SignIn(
            user_code=out["user_code"], url=out["verification_uri_complete"],
            expires_at=self._clock() + out["expires_in"], interval=float(out["interval"]))
        return self._pending

    def poll_once(self) -> str:
        sign_in = self.pending
        if sign_in is None:
            return "expired"
        try:
            tokens = self._call("POST", "/v1/auth/device/token",
                                {"device_code": self._device_code}, None)
        except ApiUnavailableError:
            return "pending"
        except ApiError as e:
            if e.api_code == "slow_down":
                self._pending = SignIn(sign_in.user_code, sign_in.url, sign_in.expires_at,
                                       sign_in.interval + SLOW_DOWN_STEP)
                return "pending"
            if e.api_code in _ENDED:
                self._pending = None
                return _ENDED[e.api_code]
            return "pending"
        data = {"device_id": tokens["device_id"], "access_token": tokens["access_token"],
                "refresh_token": tokens["refresh_token"],
                "access_expires_at": self._clock() + tokens["expires_in"],
                "email": "", "first_name": ""}
        with self._lock:
            self._write(data)
        self._pending = None
        try:
            me = self._call("GET", "/v1/identity/me", None, tokens["access_token"])
            user = me.get("user") or {}
            data["email"] = user.get("email") or ""
            data["first_name"] = user.get("first_name") or ""
            with self._lock:
                self._write(data)
        except (ApiError, ApiUnavailableError):
            # Signed in all the same; email and first name stay empty.
            pass
        return "signed_in"

    def access_token(self) -> str:
        data = self._read()
        if not data:
            raise NotSignedIn()
        if data["access_expires_at"] - self._clock() >= REFRESH_MARGIN:
            return data["access_token"]
        return self.renew(data["access_token"])

    def renew(self, stale: str) -> str:
        """An access token other than `stale`: the one on file if another caller
        already refreshed, else a refreshed pair. NotSignedIn only for a dead refresh token."""
        with self._lock:
            data = self._read()
            if not data:
                raise NotSignedIn()
            if data["access_token"] != stale:
                return data["access_token"]
            try:
                out = self._call("POST", "/v1/auth/token/refresh",
                                 {"refresh_token": data["refresh_token"]}, None)
            except ApiError as e:
                if e.status == 401 and e.api_code == "invalid_token":
                    # `eggie connect` shares the file: it may have spent this refresh token first.
                    now = self._read()
                    if now and now["refresh_token"] != data["refresh_token"]:
                        return now["access_token"]
                    self._path.unlink(missing_ok=True)
                    raise NotSignedIn() from None
                raise
            data.update(access_token=out["access_token"], refresh_token=out["refresh_token"],
                        access_expires_at=self._clock() + out["expires_in"])
            self._write(data)
            return data["access_token"]

    def forget(self) -> None:
        with self._lock:
            self._pending = None
            self._path.unlink(missing_ok=True)

    def sign_out(self) -> None:
        device_id = self.device_id
        try:
            if device_id:
                self._call("DELETE", f"/v1/auth/devices/{device_id}", None,
                           self.access_token())
        except (ApiError, ApiUnavailableError, NotSignedIn):
            pass
        finally:
            self.forget()
