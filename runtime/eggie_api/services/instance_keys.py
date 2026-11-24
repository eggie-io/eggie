from __future__ import annotations

from ..errors import (Conflict, EggieError, Forbidden, Invalid, NotFound, TooMany,
                      Unavailable, Upstream)
from ..infra.cloud import CloudError, CloudUnavailable
from .account import NotSignedIn

_BY_STATUS = {403: Forbidden, 404: NotFound, 409: Conflict, 422: Invalid, 429: TooMany}


class InstanceKeys:
    """Relays the desktop's SSH key requests to the service with the device
    token, which never leaves the VM. Decisions stay on the service."""

    def __init__(self, account, cloud):
        self._account = account
        self._cloud = cloud

    def _relay(self, call):
        try:
            return self._account.authed(call)
        except NotSignedIn:
            raise Conflict("not_signed_in", "sign in to Eggie first") from None
        except CloudUnavailable:
            raise Unavailable("cloud_unavailable", "the Eggie service is unreachable") from None
        except CloudError as e:
            extra = {"retry_after": e.retry_after} if e.retry_after is not None else None
            cls: type[EggieError] = _BY_STATUS.get(e.status, Upstream)
            raise cls(e.code, e.message, extra=extra) from None

    def list(self) -> dict:
        rows = self._relay(lambda token: self._cloud.list_instances(token))
        wanted = ("id", "name", "console_hostname", "login_name")
        return {"instances": [
            {"id": r["id"], "name": r["name"], "display_name": r.get("display_name"),
             "login_name": r["login_name"], "console_hostname": r["console_hostname"],
             "status": r.get("status"), "is_owner": bool(r.get("is_owner"))}
            for r in rows or [] if isinstance(r, dict) and all(r.get(k) for k in wanted)]}

    def request(self, instance_id: str, public_key: str, computer_name: str) -> dict:
        return self._relay(lambda token: self._cloud.request_instance_key(
            token, instance_id, public_key, computer_name))

    def status(self, instance_id: str, request_id: str) -> dict:
        return self._relay(lambda token: self._cloud.instance_key_request(
            token, instance_id, request_id))

    def remove(self, instance_id: str, fingerprint: str) -> None:
        self._relay(lambda token: self._cloud.remove_instance_key(
            token, instance_id, fingerprint))
