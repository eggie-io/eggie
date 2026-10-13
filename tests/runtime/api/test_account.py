import os
import stat

import pytest

from eggie_api.services.account import Account, NotSignedIn
from eggie_api.infra.cloud import CloudError, CloudUnavailable
from eggie_api.infra.db import Database
from eggie_api.infra.repos import Repos
from tests.runtime.api.fake_cloud import CODE, ME, TOKENS, FakeCloud


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def err(code, status=400):
    return CloudError(code, code, status)


def make(tmp_path, cloud):
    clock = Clock()
    spawned = []
    r = Repos.open(Database(tmp_path / "state.db"))
    account = Account(r.account, r.cloud_projects, cloud, clock=clock,
                      sleep=lambda s: None, spawn=spawned.append)
    return account, clock, spawned


def signed_in(tmp_path, cloud, expires_in=900):
    account, clock, _ = make(tmp_path, cloud)
    account._account.update(access_token="at-1", refresh_token="rt-1",
                                  access_expires_at=clock.now + expires_in,
                                  email="ada@example.com", org_id="org-1")
    return account, clock


def test_starting_sign_in_shows_the_code_and_link_and_starts_one_poller(tmp_path):
    account, clock, spawned = make(tmp_path, FakeCloud(device_code=[CODE]))

    status = account.start_sign_in()

    assert status == {"state": "pending", "user_code": "ABCD-EFGH",
                      "url": "https://svc/device?user_code=ABCD-EFGH",
                      "expires_at": clock.now + 600}
    assert len(spawned) == 1


def test_starting_again_while_the_code_is_valid_reuses_it(tmp_path):
    cloud = FakeCloud(device_code=[CODE])
    account, clock, spawned = make(tmp_path, cloud)
    account.start_sign_in()
    clock.now += 100

    assert account.start_sign_in()["user_code"] == "ABCD-EFGH"
    assert cloud.names() == ["device_code"]
    assert len(spawned) == 1, "a poller is already running"


def test_a_pending_code_is_polled_again_after_the_interval(tmp_path):
    account, _, _ = make(tmp_path, FakeCloud(
        device_code=[CODE], device_token=[err("authorization_pending")]))
    account.start_sign_in()

    assert account.poll_once() == 5


def test_slow_down_lengthens_the_interval_for_good(tmp_path):
    account, _, _ = make(tmp_path, FakeCloud(
        device_code=[CODE],
        device_token=[err("slow_down"), err("authorization_pending")]))
    account.start_sign_in()

    assert account.poll_once() == 10
    assert account.poll_once() == 10


def test_an_unreachable_service_keeps_polling(tmp_path):
    account, _, _ = make(tmp_path, FakeCloud(
        device_code=[CODE], device_token=[CloudUnavailable("down")]))
    account.start_sign_in()

    assert account.poll_once() == 5


@pytest.mark.parametrize("code", ["access_denied", "expired_token", "invalid_grant"])
def test_a_refused_code_returns_to_signed_out_with_the_reason(tmp_path, code):
    account, _, _ = make(tmp_path, FakeCloud(device_code=[CODE], device_token=[err(code)]))
    account.start_sign_in()

    assert account.poll_once() is None
    assert account.status() == {"state": "signed_out", "error": code}


def test_a_code_past_its_expiry_ends_without_asking_the_service(tmp_path):
    cloud = FakeCloud(device_code=[CODE])
    account, clock, _ = make(tmp_path, cloud)
    account.start_sign_in()
    clock.now += 601

    assert account.poll_once() is None
    assert account.status() == {"state": "signed_out", "error": "expired_token"}
    assert "device_token" not in cloud.names()


def test_approval_stores_the_tokens_reads_who_it_is_and_wakes_sync(tmp_path):
    account, _, _ = make(tmp_path, FakeCloud(
        device_code=[CODE], device_token=[TOKENS], me=[ME]))
    woken = []
    account.on_signed_in = lambda: woken.append(True)
    account.start_sign_in()

    assert account.poll_once() is None
    status = account.status()
    assert (status["state"], status["email"]) == ("signed_in", "ada@example.com")
    assert account.load_identity() == "org-1"
    assert woken == [True]


def test_a_stale_identity_left_by_a_previous_account_does_not_survive_a_new_sign_in(tmp_path):
    account, _, _ = make(tmp_path, FakeCloud(
        device_code=[CODE], device_token=[TOKENS], me=[ME]))
    # Leftover from a race predating today's fix: an old account's identity
    # sitting on an otherwise signed-out row.
    account._account.update(email="old@example.com", org_id="org-old")
    account.start_sign_in()

    assert account.poll_once() is None
    status = account.status()
    assert (status["state"], status["email"]) == ("signed_in", "ada@example.com")
    assert account._account.get()["org_id"] == "org-1"


def test_resume_starts_a_poller_only_for_a_pending_code(tmp_path):
    account, _, spawned = make(tmp_path, FakeCloud(device_code=[CODE]))
    account.resume()
    assert spawned == []

    account.start_sign_in()
    account._polling = False  # as after an API restart
    account.resume()
    assert len(spawned) == 2


def test_a_token_about_to_expire_is_refreshed_before_the_call(tmp_path):
    cloud = FakeCloud(refresh=[{**TOKENS, "access_token": "at-2", "refresh_token": "rt-2"}])
    account, _ = signed_in(tmp_path, cloud, expires_in=30)

    assert account.authed(lambda token: token) == "at-2"
    assert account._account.get()["refresh_token"] == "rt-2"


def test_an_invalid_token_is_refreshed_and_retried_once(tmp_path):
    cloud = FakeCloud(refresh=[{**TOKENS, "access_token": "at-2"}])
    account, _ = signed_in(tmp_path, cloud)
    seen = []

    def call(token):
        seen.append(token)
        raise err("invalid_token", 401)

    with pytest.raises(CloudError):
        account.authed(call)
    assert seen == ["at-1", "at-2"], "one retry, then the error surfaces"


def test_a_revoked_refresh_signs_out_with_revoked(tmp_path):
    account, _ = signed_in(tmp_path, FakeCloud(refresh=[err("invalid_grant")]), expires_in=0)
    account._cloud_projects.map("blog", "c-1", "org-1")

    with pytest.raises(NotSignedIn):
        account.authed(lambda token: token)
    assert account.status() == {"state": "signed_out", "error": "revoked"}
    assert account._cloud_projects.mapping() == {}


def test_an_unreachable_service_during_refresh_keeps_the_account(tmp_path):
    account, _ = signed_in(tmp_path, FakeCloud(refresh=[CloudUnavailable("down")]), expires_in=0)

    with pytest.raises(CloudUnavailable):
        account.authed(lambda token: token)
    assert account.signed_in


def test_sign_out_forgets_everything_but_the_device_even_if_logout_fails(tmp_path):
    account, _ = signed_in(tmp_path, FakeCloud(logout=[CloudUnavailable("down")]))
    device = account.device_id
    account._cloud_projects.map("blog", "c-1", "org-1")

    assert account.sign_out() == {"state": "signed_out", "error": None}
    assert account._cloud_projects.mapping() == {}
    assert account.device_id == device


def test_a_sign_out_during_refresh_is_not_undone_by_the_late_tokens(tmp_path):
    class Wrapped(FakeCloud):
        def refresh(self, refresh_token):
            account._forget(None)
            return super().refresh(refresh_token)

    cloud = Wrapped(refresh=[TOKENS])
    account, _ = signed_in(tmp_path, cloud, expires_in=0)

    with pytest.raises(NotSignedIn):
        account.authed(lambda token: token)
    assert account.signed_in is False


def test_a_sign_out_during_polling_is_not_undone_by_the_late_approval(tmp_path):
    class Wrapped(FakeCloud):
        def device_token(self, device_code):
            account._forget(None)
            return super().device_token(device_code)

    cloud = Wrapped(device_code=[CODE], device_token=[TOKENS])
    account, _, _ = make(tmp_path, cloud)
    account.start_sign_in()

    assert account.poll_once() is None
    assert account.status()["state"] == "signed_out"


def test_a_sign_out_during_load_identity_leaves_no_email_or_org_written(tmp_path):
    class Wrapped(FakeCloud):
        def me(self, token):
            account._forget(None)
            return super().me(token)

    cloud = Wrapped(me=[ME])
    account, _ = signed_in(tmp_path, cloud)
    account._account.update(org_id=None)  # force load_identity to call out

    with pytest.raises(NotSignedIn):
        account.load_identity()

    row = account._account.get()
    assert row["email"] is None
    assert row["org_id"] is None
    assert account.signed_in is False


def test_starting_sign_in_skips_the_write_if_already_signed_in_by_the_time_the_code_arrives(tmp_path):
    class Wrapped(FakeCloud):
        def device_code(self, client_name):
            out = super().device_code(client_name)
            account._account.update(access_token="at-1", refresh_token="rt-1",
                                          access_expires_at=10**12)
            return out

    cloud = Wrapped(device_code=[CODE])
    account, _, spawned = make(tmp_path, cloud)

    status = account.start_sign_in()

    assert status["state"] == "signed_in"
    assert account._account.get()["device_code"] is None
    assert spawned == []


def test_a_refresh_finding_the_token_already_replaced_does_not_spend_it_again(tmp_path):
    cloud = FakeCloud()
    account, _ = signed_in(tmp_path, cloud)
    account._account.update(access_token="at-new")

    assert account._refresh("at-1") == "at-new"
    assert "refresh" not in cloud.names()


import json


def _seed_file(tmp_path, **fields):
    body = {"device_id": "dev-9", "access_token": "at-s", "refresh_token": "rt-s",
            "expires_in": 3600, **fields}
    path = tmp_path / "account.json"
    path.write_text(json.dumps(body))
    return path


ROTATED = {"access_token": "at-r", "refresh_token": "rt-r", "expires_in": 900}


def test_a_seed_file_signs_the_instance_in_with_a_rotated_pair_and_is_deleted(tmp_path):
    cloud = FakeCloud(refresh=[ROTATED])
    account, clock, _ = make(tmp_path, cloud)
    woken = []
    account.on_signed_in = lambda: woken.append(True)
    path = _seed_file(tmp_path)

    assert account.seed_from(path) is True
    assert cloud.calls == [("refresh", "rt-s")]
    row = account._account.get()
    assert (row["device_id"], row["access_token"], row["refresh_token"]) == ("dev-9", "at-r", "rt-r")
    assert row["access_expires_at"] == clock.now + 900
    assert account.signed_in and account.device_id == "dev-9"
    assert not path.exists()
    assert woken == [True]


@pytest.mark.parametrize("failure", [CloudUnavailable("down"), {}])
def test_a_seed_that_cannot_be_rotated_yet_is_rotated_before_its_first_use(tmp_path, failure):
    cloud = FakeCloud(refresh=[failure, ROTATED])
    account, _, _ = make(tmp_path, cloud)

    assert account.seed_from(_seed_file(tmp_path)) is True
    row = account._account.get()
    assert (row["access_token"], row["refresh_token"]) == ("at-s", "rt-s")

    seen = []
    account.authed(seen.append)
    assert seen == ["at-r"]
    assert cloud.calls == [("refresh", "rt-s"), ("refresh", "rt-s")]


def test_a_seed_the_service_refuses_leaves_the_instance_signed_out(tmp_path):
    account, _, _ = make(tmp_path, FakeCloud(refresh=[err("invalid_grant")]))
    woken = []
    account.on_signed_in = lambda: woken.append(True)

    assert account.seed_from(_seed_file(tmp_path)) is False
    assert not account.signed_in
    assert woken == []


def test_a_signed_in_instance_ignores_a_seed_file_and_leaves_it(tmp_path):
    account, _ = signed_in(tmp_path, FakeCloud())
    path = _seed_file(tmp_path)

    assert account.seed_from(path) is False
    assert account._account.get()["access_token"] == "at-1"
    assert path.exists()


def test_a_malformed_seed_file_changes_nothing_and_stays(tmp_path):
    account, _, _ = make(tmp_path, FakeCloud())
    path = tmp_path / "account.json"
    path.write_text('{"device_id": "dev-9", "access_token": ""}')

    assert account.seed_from(path) is False
    assert not account.signed_in
    assert path.exists()


def test_no_seed_file_is_the_ordinary_case(tmp_path):
    account, _, _ = make(tmp_path, FakeCloud())
    assert account.seed_from(tmp_path / "absent.json") is False


def test_a_seed_supersedes_a_pending_sign_in_code(tmp_path):
    account, _, _ = make(tmp_path, FakeCloud(refresh=[ROTATED]))
    account._account.update(device_code="dc", user_code="UC", verification_url="u",
                            code_expires_at=9e9, poll_interval=5.0)

    assert account.seed_from(_seed_file(tmp_path)) is True
    row = account._account.get()
    assert row["device_code"] is None and row["user_code"] is None
    assert account.status()["state"] == "signed_in"


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory modes")
def test_a_seed_that_cannot_be_deleted_is_still_imported(tmp_path):
    account, _, _ = make(tmp_path, FakeCloud(refresh=[ROTATED]))
    folder = tmp_path / "seed"
    folder.mkdir()
    path = _seed_file(folder)
    folder.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        assert account.seed_from(path) is True
        assert account.signed_in
        assert path.exists()
    finally:
        folder.chmod(stat.S_IRWXU)
