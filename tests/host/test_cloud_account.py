from __future__ import annotations

import json
import threading

import pytest

from host.client import ApiError, ApiUnavailableError
from host.core.cloud.account import CloudAccount, NotSignedIn

CODE = {"device_code": "dc", "user_code": "ABCD-1234", "verification_uri": "https://x/y",
        "verification_uri_complete": "https://x/y?c=1", "expires_in": 600, "interval": 5}
TOKENS = {"access_token": "a1", "refresh_token": "r1", "token_type": "Bearer",
          "expires_in": 3600, "device_id": "d1"}


class Fake:
    """Answers by (method, path) from a queue of replies or exceptions."""

    def __init__(self, **routes):
        self.routes = {k: list(v) for k, v in routes.items()}
        self.calls = []

    def __call__(self, method, path, body, token):
        self.calls.append((method, path, body, token))
        queue = self.routes[f"{method} {path}"]
        reply = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(reply, Exception):
            raise reply
        return reply

    def count(self, key):
        method, path = key.split(" ", 1)
        return sum(1 for c in self.calls if (c[0], c[1]) == (method, path))


def err(code, status=400):
    return ApiError(code, code, status)


def signed_in_file(path, *, expires_at, access="a1"):
    path.write_text(json.dumps({"device_id": "d1", "access_token": access,
                                "refresh_token": "r1", "access_expires_at": expires_at,
                                "email": "ihor@eggie.io"}))


def started(tmp_path, token_replies):
    fake = Fake(**{"POST /v1/auth/device/code": [CODE],
                   "POST /v1/auth/device/token": token_replies,
                   "GET /v1/identity/me": [{"email": "ihor@eggie.io"}]})
    account = CloudAccount(tmp_path / "cloud.json", call=fake, clock=lambda: 1000.0)
    account.start()
    return account, fake


def test_poll_stores_the_pair_and_email_when_approved(tmp_path):
    account, _ = started(tmp_path, [err("authorization_pending"), TOKENS])
    assert account.poll_once() == "pending"
    assert not account.signed_in
    assert account.poll_once() == "signed_in"
    assert account.email == "ihor@eggie.io"
    path = tmp_path / "cloud.json"
    assert json.loads(path.read_text())["refresh_token"] == "r1"
    assert path.stat().st_mode & 0o777 == 0o600
    assert account.pending is None


def test_start_reuses_an_unexpired_code(tmp_path):
    account, fake = started(tmp_path, [err("authorization_pending")])
    assert account.start() == account.pending
    assert fake.count("POST /v1/auth/device/code") == 1
    assert account.pending.url == "https://x/y?c=1"


def test_slow_down_adds_five_seconds(tmp_path):
    account, _ = started(tmp_path, [err("slow_down")])
    assert account.poll_once() == "pending"
    assert account.pending.interval == 10.0


@pytest.mark.parametrize("code,outcome", [("access_denied", "denied"), ("expired_token", "expired")])
def test_denied_and_expired_end_the_code(tmp_path, code, outcome):
    account, _ = started(tmp_path, [err(code)])
    assert account.poll_once() == outcome
    assert account.pending is None


def test_refresh_runs_once_for_two_callers(tmp_path):
    path = tmp_path / "cloud.json"
    signed_in_file(path, expires_at=1000.0)
    calls = []

    def call(method, path_, body, token):
        calls.append((method, path_))
        return {"access_token": "a2", "refresh_token": "r2", "expires_in": 3600}

    class Overlapping(CloudAccount):
        # Each thread's first read waits for the other, so both hold the
        # stale token before either takes the refresh lock.
        barrier = threading.Barrier(2, timeout=5)
        seen = threading.local()

        def _read(self):
            data = super()._read()
            if not getattr(self.seen, "done", False):
                self.seen.done = True
                self.barrier.wait()
            return data

    account = Overlapping(path, call=call, clock=lambda: 1000.0)
    results = []
    threads = [threading.Thread(target=lambda: results.append(account.access_token()))
               for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results == ["a2", "a2"]
    assert len(calls) == 1
    assert json.loads(path.read_text())["refresh_token"] == "r2"


def test_dead_refresh_token_signs_out(tmp_path):
    path = tmp_path / "cloud.json"
    signed_in_file(path, expires_at=1000.0)
    fake = Fake(**{"POST /v1/auth/token/refresh": [err("invalid_token", 401)]})
    account = CloudAccount(path, call=fake, clock=lambda: 1000.0)
    with pytest.raises(NotSignedIn):
        account.access_token()
    assert not path.exists()


def test_refresh_outage_keeps_the_pair(tmp_path):
    path = tmp_path / "cloud.json"
    signed_in_file(path, expires_at=1000.0)
    fake = Fake(**{"POST /v1/auth/token/refresh": [ApiUnavailableError("down")]})
    account = CloudAccount(path, call=fake, clock=lambda: 1000.0)
    with pytest.raises(ApiUnavailableError):
        account.access_token()
    assert path.exists()


def test_sign_out_forgets_even_when_revoke_fails(tmp_path):
    path = tmp_path / "cloud.json"
    signed_in_file(path, expires_at=1e9)
    fake = Fake(**{"DELETE /v1/auth/devices/d1": [ApiUnavailableError("down")]})
    account = CloudAccount(path, call=fake, clock=lambda: 1000.0)
    account.sign_out()
    assert not path.exists()
    assert not account.signed_in
    assert fake.count("DELETE /v1/auth/devices/d1") == 1
