import json
import os

import pytest
from fastapi.testclient import TestClient

from eggie_api.infra.cloud import CloudUnavailable
from eggie_api.services.kitchen_keys import InstanceKeySync
from tests.runtime.api.conftest import _make_env
from tests.runtime.api.fake_cloud import FakeCloud

HOUR = 3600


def listing(*keys, login="ihork"):
    return {"login_name": login,
            "keys": [{"public_key": k, "name": n, "fingerprint": "SHA256:x", "source": "computer"}
                     for k, n in keys]}


class SignedIn:
    def authed(self, fn):
        return fn("tok")


class Clock:
    now = 0.0

    def __call__(self):
        return self.now


def make(tmp_path, *replies, **kw):
    cloud = FakeCloud(own_ssh_keys=list(replies))
    return InstanceKeySync(SignedIn(), cloud, tmp_path / "keys", **kw), tmp_path / "keys" / "desired.json"


def test_sync_writes_login_and_keys(tmp_path):
    sync, path = make(tmp_path, listing(("ssh-ed25519 AAA", "Ihor's MacBook\n"), ("ssh-ed25519 BBB", "")))
    sync.sync()
    assert json.loads(path.read_text()) == {
        "login": "ihork", "keys": ["ssh-ed25519 AAA Ihor's MacBook", "ssh-ed25519 BBB"]}
    assert path.stat().st_mode & 0o777 == 0o644


def test_revoked_key_disappears_on_next_sync(tmp_path):
    sync, path = make(tmp_path, listing(("ssh-ed25519 AAA", "a"), ("ssh-ed25519 BBB", "b")),
                      listing(("ssh-ed25519 BBB", "b")))
    sync.sync()
    sync.sync()
    assert json.loads(path.read_text())["keys"] == ["ssh-ed25519 BBB b"]


def test_unchanged_keys_do_not_rewrite_the_file(tmp_path):
    same = listing(("ssh-ed25519 AAA", "a"))
    sync, path = make(tmp_path, same, same)
    sync.sync()
    os.utime(path, (1, 1))
    sync.sync()
    assert path.stat().st_mtime == 1


def test_service_down_keeps_the_last_file(tmp_path):
    sync, path = make(tmp_path, listing(("ssh-ed25519 AAA", "a")), CloudUnavailable("down"))
    sync.sync()
    before = path.read_text()
    sync.sync()
    assert path.read_text() == before


def test_pass_syncs_only_when_woken_or_due(tmp_path):
    clock = Clock()
    cloud = FakeCloud(own_ssh_keys=[listing()] * 3)
    sync = InstanceKeySync(SignedIn(), cloud, tmp_path, clock=clock)
    for at, woken, calls in [(0, False, 1), (60, False, 1), (61, True, 2),
                             (6 * HOUR + 62, False, 3)]:
        clock.now = at
        if woken:
            sync.wake()
        sync.run_pass()
        assert len(cloud.calls) == calls, at


@pytest.fixture
def site(tmp_path):
    yield from _make_env(tmp_path, site_host="foo.eggie.io", domain="foo.eggie.space")


def test_ping_is_open_on_the_site_host_only(site):
    bare = TestClient(site.app)
    assert bare.post("/api/ssh-keys/sync", headers={"Host": "foo.eggie.io"}).status_code == 202
    other = bare.post("/api/ssh-keys/sync", headers={"Host": "evil.example"})
    assert other.status_code == 403
    assert other.json()["error"]["code"] == "forbidden_host"


def test_seventh_ping_in_a_minute_is_429(site):
    bare = TestClient(site.app)
    codes = [bare.post("/api/ssh-keys/sync", headers={"Host": "foo.eggie.io"}).status_code
             for _ in range(7)]
    assert codes == [202] * 6 + [429]
