"""The Eggie service over HTTPS, as the signed-in desktop app."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from pathlib import Path

from host.client import ApiError, ApiUnavailableError
from host.core import constants, tls
from host.core.cloud.account import CloudAccount, NotSignedIn


def service_api_url(env: Mapping[str, str] = os.environ) -> str:
    return (env.get("EGGIE_SERVICE_API_URL") or constants.SERVICE_API_URL).rstrip("/")


def web_app_url(env: Mapping[str, str] = os.environ) -> str:
    return (env.get("EGGIE_WEB_APP_URL") or constants.WEB_APP_URL).rstrip("/")


def _error(status: int, raw: bytes) -> ApiError:
    try:
        error = json.loads(raw.decode("utf-8", "replace"))["error"]
        return ApiError(str(error["code"]), str(error["message"]), status)
    except (ValueError, KeyError, TypeError):
        return ApiError("cloud_error", f"The Eggie service answered {status}", status)


def http_call(base: str, *, opener=None, timeout: float = 15.0):
    base = base.rstrip("/")
    opener = opener or urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=tls.context()))

    def call(method: str, path: str, body: dict | None, token: str | None) -> dict:
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(base + path, data=data, method=method)
        if data is not None:
            request.add_header("Content-Type", "application/json")
        if token:
            request.add_header("Authorization", f"Bearer {token}")
        try:
            with opener.open(request, timeout=timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as e:
            raise _error(e.code, e.read()) from None
        except (urllib.error.URLError, OSError) as e:
            raise ApiUnavailableError(
                f"could not reach the Eggie service ({getattr(e, 'reason', e)})") from e
        return json.loads(raw.decode("utf-8")) if raw.strip() else {}

    return call


def _q(value: str) -> str:
    return urllib.parse.quote(value, safe="")


class CloudClient:
    def __init__(self, account: CloudAccount, call):
        self._account = account
        self._call = call

    def _send(self, method: str, path: str, body: dict | None = None):
        token = self._account.access_token()
        try:
            return self._call(method, f"/v1{path}", body, token)
        except ApiError as e:
            if e.status == 401 and e.api_code == "invalid_token":
                self._account.forget()
                raise NotSignedIn() from None
            raise

    def instances(self) -> list[dict]:
        out = self._send("GET", "/instances")
        return out if isinstance(out, list) else out.get("instances", [])

    def instance(self, instance_id: str) -> dict:
        return self._send("GET", f"/instances/{_q(instance_id)}")

    def instance_catalog(self) -> dict:
        return self._send("GET", "/instances/catalog")

    def billing_catalog(self) -> dict:
        return self._send("GET", "/billing/catalog")

    def request_instance_key(self, instance_id: str, public_key: str,
                             computer_name: str) -> dict:
        return self._send("POST", f"/instances/{_q(instance_id)}/ssh-key-requests",
                          {"public_key": public_key, "computer_name": computer_name})

    def instance_key_request(self, instance_id: str, request_id: str) -> dict:
        return self._send(
            "GET", f"/instances/{_q(instance_id)}/ssh-key-requests/{_q(request_id)}")

    def remove_instance_key(self, instance_id: str, fingerprint: str) -> None:
        self._send("DELETE",
                   f"/instances/{_q(instance_id)}/ssh-keys?fingerprint={_q(fingerprint)}")


def default_cloud(path: Path) -> tuple[CloudAccount, CloudClient]:
    call = http_call(service_api_url())
    account = CloudAccount(path, call=call)
    return account, CloudClient(account, call)
