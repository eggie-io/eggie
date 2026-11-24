"""Sign the VM's own Eggie account in during install, with one Approve click."""
from __future__ import annotations

from host.client import ApiError, ApiUnavailableError

POLL_SECONDS = 2.0


def sign_in_vm(client, *, open_url, sleep, monotonic, limit: float = 600.0,
                on_waiting=lambda: None) -> str:
    """Returns "signed_in", "timed_out" or "refused"."""
    if client.account().get("state") == "signed_in":
        return "signed_in"
    reply = client.account_sign_in()
    if reply.get("state") == "signed_in":
        return "signed_in"
    if reply.get("state") != "pending":
        return "refused"
    if reply.get("url"):
        open_url(reply["url"])
    on_waiting()
    deadline = monotonic() + limit
    while monotonic() < deadline:
        sleep(POLL_SECONDS)
        try:
            status = client.account()
        except (ApiError, ApiUnavailableError):
            continue    # the person may still be approving; keep waiting
        if status.get("state") == "signed_in":
            return "signed_in"
        # We asked, so a signed-out reply with an error is the service saying no
        # (access_denied, expired_token).
        if status.get("state") == "signed_out" and status.get("error"):
            return "refused"
    return "timed_out"
