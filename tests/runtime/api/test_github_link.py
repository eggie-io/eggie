import json
import stat

import pytest

from eggie_api.infra.github import GitHubUnavailable
from eggie_api.services.github_link import (SETUP_TIMEOUT, GitHubLink,
                                         NotConnected, setup_state)
from eggie_api.infra.db import Database
from eggie_api.infra.repos import Repos
from tests.runtime.api.fake_github import CODE, TOKEN, USER, FakeGitHub, err


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def make(tmp_path, github):
    clock, spawned = Clock(), []
    link = GitHubLink(Repos.open(Database(tmp_path / "state.db")).github, github, client_id="cid",
                      directory=tmp_path / "github", clock=clock,
                      sleep=lambda s: None, spawn=spawned.append)
    return link, clock, spawned


def connected(tmp_path, **scripts):
    github = FakeGitHub(device_code=[CODE], device_token=[{"access_token": TOKEN}],
                        user=[USER], **scripts)
    link, clock, _ = make(tmp_path, github)
    link.connect()
    assert link.poll_once() is None
    return link, clock, github


def desired(tmp_path):
    return json.loads((tmp_path / "github" / "desired.json").read_text())


def test_connect_shows_the_code_and_starts_one_poller(tmp_path):
    link, clock, spawned = make(tmp_path, FakeGitHub(device_code=[CODE]))
    assert link.connect() == {"state": "pending", "user_code": "WDJB-MJHT",
                              "url": "https://github.com/login/device",
                              "expires_at": clock.now + 900}
    link.connect()
    assert len(spawned) == 1


def test_pending_waits_the_interval_and_slow_down_raises_it(tmp_path):
    link, _, _ = make(tmp_path, FakeGitHub(
        device_code=[CODE],
        device_token=[err("authorization_pending"), err("slow_down"),
                      err("slow_down", interval=30)]))
    link.connect()
    assert link.poll_once() == 5
    assert link.poll_once() == 10
    assert link.poll_once() == 30


@pytest.mark.parametrize("reply,code", [(err("access_denied"), "access_denied"),
                                        (err("expired_token"), "expired_token"),
                                        (err("incorrect_client_credentials"), "github_error")])
def test_a_refusal_ends_disconnected_with_its_reason(tmp_path, reply, code):
    link, _, _ = make(tmp_path, FakeGitHub(device_code=[CODE], device_token=[reply]))
    link.connect()
    assert link.poll_once() is None
    assert link.status() == {"state": "disconnected", "error": code}


def test_a_code_past_its_expiry_is_not_polled(tmp_path):
    github = FakeGitHub(device_code=[CODE], device_token=[])
    link, clock, _ = make(tmp_path, github)
    link.connect()
    clock.now += 901
    assert link.poll_once() is None
    assert link.status()["error"] == "expired_token"
    assert [c[0] for c in github.calls] == ["device_code"]


def test_a_network_failure_keeps_polling(tmp_path):
    link, _, _ = make(tmp_path, FakeGitHub(device_code=[CODE],
                                           device_token=[GitHubUnavailable("x")]))
    link.connect()
    assert link.poll_once() == 5
    assert link.status()["state"] == "pending"


def test_approval_stores_the_token_privately_and_writes_a_token_free_desired_state(tmp_path):
    link, _, _ = connected(tmp_path)
    token_file = tmp_path / "github" / "token"
    assert token_file.read_text() == TOKEN
    assert stat.S_IMODE(token_file.stat().st_mode) == 0o600
    doc = desired(tmp_path)
    assert doc == {"generation": 1, "state": "connected", "login": "octo",
                   "name": "Octo Cat", "email": "42+octo@users.noreply.github.com"}
    status = link.status()
    assert (status["state"], status["setup"]) == ("connected", "applying")
    assert TOKEN not in json.dumps(status)


def test_a_token_arriving_after_disconnect_is_dropped(tmp_path):
    holder = {}
    github = FakeGitHub(device_code=[CODE], device_token=[
        lambda: (holder["link"].disconnect(), {"access_token": TOKEN})[1]])
    link, _, _ = make(tmp_path, github)
    holder["link"] = link
    link.connect()
    assert link.poll_once() is None
    assert not (tmp_path / "github" / "token").exists()
    assert link.status()["state"] == "disconnected"


def test_generation_climbs_past_an_applied_file_left_by_a_lost_database(tmp_path):
    (tmp_path / "github").mkdir()
    (tmp_path / "github" / "applied.json").write_text(
        json.dumps({"generation": 9, "ok": True, "accounts": []}))
    link, _, _ = connected(tmp_path)
    assert desired(tmp_path)["generation"] == 10
    assert link.status()["setup"] == "applying"


def test_disconnect_drops_the_token_and_asks_for_logout_at_a_new_generation(tmp_path):
    link, _, _ = connected(tmp_path)
    assert link.disconnect() == {"state": "disconnected", "error": None}
    assert not (tmp_path / "github" / "token").exists()
    assert desired(tmp_path) == {"generation": 2, "state": "disconnected"}


def test_bad_credentials_needs_a_reconnect_without_a_new_generation(tmp_path):
    link, _, _ = connected(tmp_path)
    link.mark_bad_credentials()
    assert link.status() == {"state": "needs_reconnect", "login": "octo"}
    assert not (tmp_path / "github" / "token").exists()
    assert desired(tmp_path)["generation"] == 1
    with pytest.raises(NotConnected):
        link.token()


def test_the_token_is_rechecked_at_most_every_five_minutes(tmp_path):
    link, clock, github = connected(tmp_path)
    github.scripts["user"] = [GitHubUnavailable("x"), err("bad_credentials")]
    clock.now += 301
    link.check_token()
    assert link.status()["state"] == "connected"
    link.check_token()
    assert len([c for c in github.calls if c[0] == "user"]) == 2
    clock.now += 301
    link.check_token()
    assert link.status()["state"] == "needs_reconnect"


def test_reapply_rereads_the_profile_and_bumps_the_generation(tmp_path):
    link, _, github = connected(tmp_path)
    github.scripts["user"] = [{**USER, "name": "", "email": "o@example.com"}]
    link.reapply()
    assert desired(tmp_path)["generation"] == 2
    assert (desired(tmp_path)["name"], desired(tmp_path)["email"]) == ("octo", "o@example.com")


def test_reapply_while_disconnected_is_refused(tmp_path):
    link, _, _ = make(tmp_path, FakeGitHub())
    with pytest.raises(NotConnected):
        link.reapply()


def test_reapply_racing_a_disconnect_is_dropped(tmp_path):
    holder = {}
    link, _, github = connected(tmp_path)
    holder["link"] = link
    github.scripts["user"] = [
        lambda: (holder["link"].disconnect(), USER)[1]]
    with pytest.raises(NotConnected):
        link.reapply()
    assert link.status()["state"] == "disconnected"
    assert desired(tmp_path) == {"generation": 2, "state": "disconnected"}
    assert not (tmp_path / "github" / "token").exists()


def test_check_token_racing_a_disconnect_is_dropped(tmp_path):
    holder = {}
    link, clock, github = connected(tmp_path)
    holder["link"] = link
    github.scripts["user"] = [
        lambda: (holder["link"].disconnect(), err("bad_credentials"))[1]]
    clock.now += 301
    link.check_token()
    assert link.status() == {"state": "disconnected", "error": None}


def test_confirm_bad_leaves_a_still_working_token_connected(tmp_path):
    link, _, github = connected(tmp_path)
    token = (tmp_path / "github" / "token").read_text()
    github.scripts["user"] = [USER]
    link.confirm_bad(token)
    assert link.status()["state"] == "connected"
    assert (tmp_path / "github" / "token").exists()


def test_confirm_bad_with_bad_credentials_needs_a_reconnect(tmp_path):
    link, _, github = connected(tmp_path)
    token = (tmp_path / "github" / "token").read_text()
    github.scripts["user"] = [err("bad_credentials")]
    link.confirm_bad(token)
    assert link.status() == {"state": "needs_reconnect", "login": "octo"}
    assert not (tmp_path / "github" / "token").exists()


def test_confirm_bad_racing_a_disconnect_stays_disconnected(tmp_path):
    holder = {}
    link, _, github = connected(tmp_path)
    holder["link"] = link
    token = (tmp_path / "github" / "token").read_text()
    github.scripts["user"] = [
        lambda: (holder["link"].disconnect(), err("bad_credentials"))[1]]
    link.confirm_bad(token)
    assert link.status() == {"state": "disconnected", "error": None}


ROW = {"generation": 3, "desired_at": 1000.0}


@pytest.mark.parametrize("applied,now,expected", [
    ({"generation": 3, "ok": True}, 1001, ("ready", None)),
    ({"generation": 3, "ok": False, "error": "root: gh failed"}, 1001, ("failed", "root: gh failed")),
    ({"generation": 2, "ok": True}, 1000 + SETUP_TIMEOUT - 1, ("applying", None)),
    (None, 1000 + SETUP_TIMEOUT - 1, ("applying", None)),
    (None, 1000 + SETUP_TIMEOUT, ("runtime_outdated", None)),
    ({"generation": 2, "ok": True}, 1000 + SETUP_TIMEOUT, ("failed", "setup_timeout")),
])
def test_setup_state(applied, now, expected):
    assert setup_state(ROW, applied, now) == expected
