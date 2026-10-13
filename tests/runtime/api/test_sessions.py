import pytest
import hashlib

from fastapi.testclient import TestClient

from eggie_api.rest.app import create_app
from eggie_api.config import ApiConfig
from eggie_api.services.sessions import COOKIE, SESSION_TTL, Sessions
from eggie_api.infra.db import Database
from eggie_api.infra.repos import Repos
from tests.runtime.api.conftest import AUTH, BROWSER

ORIGIN = {"Origin": "http://localhost:41080"}


class Clock:
    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self):
        return self.now


def _signed_in(env) -> TestClient:
    code = env.client.post("/sessions/handoff").json()["code"]
    browser = TestClient(env.app, headers={**BROWSER, **ORIGIN})
    assert browser.post("/api/session", json={"code": code}).status_code == 200
    return browser


def test_a_handoff_code_signs_the_browser_in(env):
    browser = _signed_in(env)
    assert browser.get("/api/projects").status_code == 200


def test_a_handoff_code_works_only_once(env):
    code = env.client.post("/sessions/handoff").json()["code"]
    browser = TestClient(env.app, headers={**BROWSER, **ORIGIN})
    browser.post("/api/session", json={"code": code})
    again = TestClient(env.app, headers={**BROWSER, **ORIGIN}).post(
        "/api/session", json={"code": code})
    assert again.status_code == 401
    assert again.json()["error"]["code"] == "handoff_invalid"


def test_handoff_needs_a_session_on_the_browser_mount(env):
    resp = TestClient(env.app, headers={**BROWSER, **ORIGIN}).post(
        "/api/sessions/handoff")
    assert resp.status_code == 401


def test_a_signed_in_page_can_sign_a_second_browser_in(env):
    # The desktop window's "open in browser" button: the page mints a code
    # and the system browser redeems it.
    code = _signed_in(env).post("/api/sessions/handoff").json()["code"]
    other = TestClient(env.app, headers={**BROWSER, **ORIGIN})
    assert other.post("/api/session", json={"code": code}).status_code == 200
    assert other.get("/api/projects").status_code == 200


def test_a_foreign_origin_cannot_mint_a_code_with_the_cookie(env):
    browser = _signed_in(env)
    resp = browser.post("/api/sessions/handoff",
                        headers={"Origin": "http://evil.example"})
    assert resp.status_code == 403


def test_the_cookie_is_scoped_to_the_api_path_and_script_proof(env):
    code = env.client.post("/sessions/handoff").json()["code"]
    resp = TestClient(env.app, headers={**BROWSER, **ORIGIN}).post(
        "/api/session", json={"code": code})
    header = resp.headers["set-cookie"].lower()
    assert "httponly" in header and "samesite=strict" in header
    assert "path=/api" in header


def test_the_bearer_mount_ignores_a_valid_session_cookie(env):
    browser = _signed_in(env)
    bare = TestClient(env.app, cookies={COOKIE: browser.cookies[COOKIE]})
    assert bare.get("/projects").status_code == 401


def test_an_expired_code_is_refused(tmp_path):
    clock = Clock()
    sessions = Sessions(Repos.open(Database(tmp_path / "s.db")).sessions, clock=clock)
    code = sessions.issue_handoff()
    clock.now += 61
    assert sessions.redeem(code) is None


def test_an_expired_session_reads_as_expired_not_missing(tmp_path):
    # The UI shows "we've lost track of you" for both, but only an expired one
    # may say "that happens after a while".
    clock = Clock()
    sessions = Sessions(Repos.open(Database(tmp_path / "s.db")).sessions, clock=clock)
    sid = sessions.redeem(sessions.issue_handoff())
    clock.now += SESSION_TTL + 1
    assert sessions.check(sid) == "expired"
    assert sessions.check(sid) == "missing"


def test_use_slides_the_expiry_forward(tmp_path):
    clock = Clock()
    sessions = Sessions(Repos.open(Database(tmp_path / "s.db")).sessions, clock=clock)
    sid = sessions.redeem(sessions.issue_handoff())
    clock.now += SESSION_TTL - 10
    assert sessions.check(sid) == "ok"
    clock.now += SESSION_TTL - 10
    assert sessions.check(sid) == "ok"


def test_a_session_survives_an_api_restart(env):
    browser = _signed_in(env)
    cookie = browser.cookies[COOKIE]
    env.repos.db.close()
    restarted = create_app(config=env.config, runner=env.runner,
                           http_probe=env.probe)
    again = TestClient(restarted, headers=BROWSER, cookies={COOKIE: cookie})
    assert again.get("/api/session").status_code == 200


def test_the_raw_session_id_is_never_stored(tmp_path):
    repos = Repos.open(Database(tmp_path / "s.db"))
    sessions = Sessions(repos.sessions)
    sid = sessions.redeem(sessions.issue_handoff())
    assert repos.sessions.get(sid) is None


def test_an_expired_session_answers_session_expired_through_the_middleware(env):
    browser = _signed_in(env)
    cookie = browser.cookies[COOKIE]
    # Backdating the stored row is the only way to force expiry here without
    # waiting out the real SESSION_TTL -- the app builds its own Sessions
    # with the real clock.
    id_hash = hashlib.sha256(cookie.encode()).hexdigest()
    env.repos.sessions.set_expiry(id_hash, 0)
    resp = browser.get("/api/session")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "session_expired"


def test_the_cookie_is_not_reissued_right_after_sign_in(tmp_path):
    clock = Clock()
    token_path = tmp_path / "api.token"
    token_path.write_text("test-token")
    config = ApiConfig(projects_root=tmp_path / "projects",
                         state_db=tmp_path / "state.db", token_path=token_path,
                         edge_port=41080)
    repos = Repos.open(Database(config.state_db))
    sessions = Sessions(repos.sessions, clock=clock)
    app = create_app(config=config, repos=repos, sessions=sessions)
    browser = TestClient(app, headers={**BROWSER, **ORIGIN})
    code = sessions.issue_handoff()
    browser.post("/api/session", json={"code": code})

    resp = browser.get("/api/session")
    assert resp.status_code == 200
    assert "set-cookie" not in resp.headers


def test_the_cookie_is_reissued_once_the_slide_window_is_crossed(tmp_path):
    clock = Clock()
    token_path = tmp_path / "api.token"
    token_path.write_text("test-token")
    config = ApiConfig(projects_root=tmp_path / "projects",
                         state_db=tmp_path / "state.db", token_path=token_path,
                         edge_port=41080)
    repos = Repos.open(Database(config.state_db))
    sessions = Sessions(repos.sessions, clock=clock)
    app = create_app(config=config, repos=repos, sessions=sessions)
    browser = TestClient(app, headers={**BROWSER, **ORIGIN})
    code = sessions.issue_handoff()
    browser.post("/api/session", json={"code": code})

    clock.now += 3601
    resp = browser.get("/api/session")
    assert resp.status_code == 200
    header = resp.headers["set-cookie"].lower()
    assert f"max-age={SESSION_TTL}" in header
    assert "httponly" in header and "samesite=strict" in header
    assert "path=/api" in header


def test_signing_out_ends_the_session(env):
    browser = _signed_in(env)
    assert browser.delete("/api/session").status_code == 200
    resp = browser.get("/api/projects")
    assert resp.json()["error"]["code"] == "not_signed_in"


from tests.runtime.api.conftest import SITE_BROWSER, SITE_ORIGIN


def _cookie_header(env, headers):
    code = env.client.post("/sessions/handoff").json()["code"]
    resp = TestClient(env.app, headers=headers).post("/api/session", json={"code": code})
    assert resp.status_code == 200
    return resp.headers["set-cookie"]


def test_the_cookie_is_not_secure_locally(env):
    # The local console is plain http: a Secure cookie would never come back.
    assert "Secure" not in _cookie_header(env, {**BROWSER, **ORIGIN})


def test_the_cookie_is_secure_on_a_site(site_env):
    assert "Secure" in _cookie_header(site_env, {**SITE_BROWSER, **SITE_ORIGIN})


import dataclasses

from eggie_api.services.account import Account
from eggie_api.infra.cloud import CloudError, CloudUnavailable
from tests.runtime.api.conftest import FakeRunner
from tests.runtime.api.fake_cloud import FakeCloud


def _site_app(env, cloud, *, site=True, signed_in=True):
    repos = Repos.open(Database(env.config.state_db.with_name("site.db")))
    if signed_in:
        repos.account.update(access_token="at", refresh_token="rt",
                             access_expires_at=10**12, org_id="org-1")
    account = Account(repos.account, repos.cloud_projects, cloud, spawn=lambda fn: None)
    config = dataclasses.replace(env.config, site_host="foo.eggie.io",
                                 domain="foo.eggie.space") if site else env.config
    app = create_app(config=config, runner=FakeRunner(), repos=repos,
                     account=account, cloud=cloud)
    headers = {**SITE_BROWSER, **SITE_ORIGIN} if site else {**BROWSER, **ORIGIN}
    # The site cookie is Secure: the client must be https to send it back.
    base = "https://foo.eggie.io" if site else "http://testserver"
    return app, TestClient(app, base_url=base, headers=headers), TestClient(app, headers=AUTH)


def test_a_code_the_service_vouches_for_signs_the_browser_in_on_a_site(env):
    cloud = FakeCloud(redeem_instance_handoff=[{"ok": True}])
    _, browser, _ = _site_app(env, cloud)
    assert browser.post("/api/session", json={"code": "from-web-app"}).status_code == 200
    assert browser.get("/api/projects").status_code == 200
    assert cloud.calls == [("redeem_instance_handoff", "at", "from-web-app")]


def test_a_code_the_service_refuses_is_handoff_invalid(env):
    cloud = FakeCloud(redeem_instance_handoff=[CloudError("handoff_invalid", "no", 404)])
    _, browser, _ = _site_app(env, cloud)
    resp = browser.post("/api/session", json={"code": "stale"})
    assert (resp.status_code, resp.json()["error"]["code"]) == (401, "handoff_invalid")


def test_an_unreachable_service_is_handoff_invalid_too(env):
    _, browser, _ = _site_app(env, FakeCloud(redeem_instance_handoff=[CloudUnavailable("down")]))
    assert browser.post("/api/session", json={"code": "x"}).status_code == 401


def test_a_locally_minted_code_never_goes_to_the_service_on_a_site(env):
    cloud = FakeCloud()
    _, browser, bearer = _site_app(env, cloud)
    code = bearer.post("/sessions/handoff").json()["code"]
    assert browser.post("/api/session", json={"code": code}).status_code == 200
    assert cloud.calls == []


def test_an_unknown_code_is_not_sent_to_the_service_outside_a_site(env):
    cloud = FakeCloud()
    _, browser, _ = _site_app(env, cloud, site=False)
    assert browser.post("/api/session", json={"code": "x"}).status_code == 401
    assert cloud.calls == []


@pytest.mark.parametrize("answer", [{"ok": False}, None, {}])
def test_a_service_answer_without_ok_true_does_not_sign_the_browser_in(env, answer):
    _, browser, _ = _site_app(env, FakeCloud(redeem_instance_handoff=[answer]))
    resp = browser.post("/api/session", json={"code": "c"})
    assert (resp.status_code, resp.json()["error"]["code"]) == (401, "handoff_invalid")
    assert browser.get("/api/projects").status_code == 401


def test_a_site_that_is_not_signed_in_refuses_a_code_without_calling_the_service(env):
    cloud = FakeCloud(redeem_instance_handoff=[{"ok": True}])
    _, browser, _ = _site_app(env, cloud, signed_in=False)
    resp = browser.post("/api/session", json={"code": "c"})
    assert (resp.status_code, resp.json()["error"]["code"]) == (401, "handoff_invalid")
    assert cloud.calls == []


def test_a_code_over_256_characters_is_rejected_as_invalid_request(env):
    cloud = FakeCloud()
    _, browser, _ = _site_app(env, cloud)
    resp = browser.post("/api/session", json={"code": "x" * 257})
    assert (resp.status_code, resp.json()["error"]["code"]) == (422, "invalid_request")
    assert cloud.calls == []
