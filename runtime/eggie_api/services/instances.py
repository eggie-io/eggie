from __future__ import annotations

from ..errors import Conflict, Unavailable, Upstream
from ..infra.cloud import CloudError, CloudUnavailable
from .account import NotSignedIn


class Instances:
    """The account's kitchens, trimmed to what the console's switcher shows."""

    def __init__(self, account, cloud, site_host: str | None):
        self._account = account
        self._cloud = cloud
        self._site_host = site_host
        self._own_login: str | None = None

    def list(self) -> dict:
        try:
            rows = self._account.authed(lambda token: self._cloud.list_instances(token))
        except NotSignedIn:
            raise Conflict("not_signed_in", "sign in to Eggie to see your kitchens")
        except CloudUnavailable:
            raise Unavailable("cloud_unavailable", "the Eggie service is unreachable")
        except CloudError as e:
            raise Upstream("cloud_error", e.message)
        return {"instances": [
            {"id": r["id"], "display_name": r.get("display_name"),
             "console_hostname": r["console_hostname"], "status": r.get("status"),
             "signed_out": bool(r.get("signed_out")), "is_owner": bool(r.get("is_owner")),
             "current": r["console_hostname"] == self._site_host}
            for r in rows if r.get("id") and r.get("console_hostname")]}

    def own_login(self) -> str | None:
        """This server's login account as the service records it; None when
        the service cannot say, so the caller keeps what install recorded."""
        # A server's login never changes, and the console waits on this
        # before it shows any agent: ask once.
        if self._own_login or not self._site_host:
            return self._own_login
        try:
            rows = self._account.authed(lambda token: self._cloud.list_instances(token))
        except (NotSignedIn, CloudUnavailable, CloudError):
            return None
        for r in rows if isinstance(rows, list) else []:
            if not isinstance(r, dict) or r.get("console_hostname") != self._site_host:
                continue
            login = r.get("login_name")
            if isinstance(login, str) and login:
                self._own_login = login
        return self._own_login
