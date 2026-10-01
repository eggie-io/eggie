import re

from fastapi.testclient import TestClient

from tests.runtime.api.conftest import BROWSER
from tests.runtime.api.route_sweep import every_route

ORIGIN = {"Origin": "http://localhost:41080"}

# The only /api routes the auth middleware lets through with no session.
OPEN_API_ROUTES = {("GET", "/api/health"), ("HEAD", "/api/health"),
                   ("POST", "/api/session")}


def _browser(env, **headers):
    # A browser never sends the bearer token; start from a bare client.
    return TestClient(env.app, headers={**BROWSER, **headers})


def test_the_api_mount_refuses_the_bearer_port_host(env):
    # Cookies ignore the port: without this check a page could replay the
    # session cookie straight at localhost:39099.
    resp = _browser(env, Host="localhost:39099").get("/api/projects")
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "forbidden_host"


def test_the_api_mount_refuses_a_rebound_hostname(env):
    resp = _browser(env, Host="evil.example:41080").get("/api/health")
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "forbidden_host"


def test_a_write_from_a_project_app_origin_is_refused(env):
    resp = _browser(env, Origin="http://blog.test.local:41080").post(
        "/api/projects", json={"id": "x"})
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "forbidden_origin"


def test_a_write_without_an_origin_is_refused(env):
    resp = _browser(env).post("/api/projects", json={"id": "x"})
    assert resp.json()["error"]["code"] == "forbidden_origin"


def test_the_api_mount_does_not_accept_the_bearer_token(env):
    resp = _browser(env, Authorization="Bearer test-token").get("/api/projects")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "not_signed_in"


def test_api_health_answers_without_a_session_for_the_version_check(env):
    resp = _browser(env).get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["api"] == 1


def test_the_bearer_mount_is_unchanged_by_a_browser_host(env):
    # The host dials 127.0.0.1:39099 with whatever Host urllib sends; the
    # allowlist must never apply to the bearer mount.
    resp = env.client.get("/projects", headers={"Host": "anything:1"})
    assert resp.status_code == 200


def test_every_api_route_requires_a_session_except_the_open_ones(env):
    # Hardcoding a handful of paths lets a future route slip onto the open
    # list unnoticed with the suite still green -- modelled on the bearer
    # sweep in test_api_auth.py, but over the /api mount and its session
    # check instead of the bearer token.
    client = _browser(env, **ORIGIN)
    checked = 0
    for route in every_route(env.app):
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None) or set()
        if not path or not path.startswith("/api/"):
            continue
        concrete = re.sub(r"\{[^}]+\}", "x", path)
        for method in methods - {"OPTIONS"}:
            if (method, concrete) in OPEN_API_ROUTES:
                continue
            checked += 1
            resp = client.request(method, concrete)
            assert resp.status_code == 401, (
                f"{method} {concrete} answered {resp.status_code} without a session")
    assert checked >= 15, "the sweep found fewer routes than this app actually has"
