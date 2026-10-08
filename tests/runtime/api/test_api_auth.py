import asyncio
import re

from fastapi import FastAPI
from fastapi.testclient import TestClient

from eggie_api.http.app import create_app
from eggie_api.config import ApiConfig
from tests.runtime.api.route_sweep import every_route


def _app(tmp_path, token: str | None) -> FastAPI:
    """`token=None` means no file at all; `""` means an empty file -- both
    must be treated as unconfigured, never as "allow"."""
    token_path = tmp_path / "api.token"
    if token is not None:
        token_path.write_text(token)
    config = ApiConfig(projects_root=tmp_path / "projects",
                         state_db=tmp_path / "state.db", token_path=token_path)
    return create_app(config=config)


def _client(tmp_path, token: str | None) -> TestClient:
    return TestClient(_app(tmp_path, token), raise_server_exceptions=False)


def test_health_answers_with_no_authorization_header(tmp_path):
    client = _client(tmp_path, "secret")
    assert client.get("/health").status_code == 200


def test_health_answers_even_when_the_api_has_no_token_configured(tmp_path):
    client = _client(tmp_path, None)
    assert client.get("/health").status_code == 200


def test_version_is_not_exempt_from_auth(tmp_path):
    # /health is the only exemption -- an unauthenticated /version is a free
    # fingerprint of the VM's contents.
    client = _client(tmp_path, "secret")
    resp = client.get("/version")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "unauthorized"


def test_a_request_without_a_token_is_rejected(tmp_path):
    client = _client(tmp_path, "secret")
    resp = client.get("/projects")
    assert resp.status_code == 401
    assert resp.json()["error"] == {"code": "unauthorized",
                                    "message": resp.json()["error"]["message"]}


def test_a_request_with_the_wrong_token_is_rejected(tmp_path):
    client = _client(tmp_path, "secret")
    resp = client.get("/projects", headers={"Authorization": "Bearer wrong"})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "unauthorized"


def test_a_request_with_the_right_token_is_accepted(tmp_path):
    client = _client(tmp_path, "secret")
    resp = client.get("/projects", headers={"Authorization": "Bearer secret"})
    assert resp.status_code == 200


def test_a_non_bearer_scheme_is_rejected(tmp_path):
    client = _client(tmp_path, "secret")
    resp = client.get("/projects", headers={"Authorization": "secret"})
    assert resp.status_code == 401


def test_missing_token_file_never_falls_back_to_allowing_requests(tmp_path):
    # A missing token must never mean "allow", even when the caller supplies
    # something that looks like a credential.
    client = _client(tmp_path, None)
    resp = client.get("/projects", headers={"Authorization": "Bearer anything"})
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "api_unconfigured"


def test_empty_token_file_is_also_treated_as_unconfigured(tmp_path):
    client = _client(tmp_path, "")
    resp = client.get("/projects")
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "api_unconfigured"


def test_a_token_file_with_only_whitespace_is_treated_as_unconfigured(tmp_path):
    client = _client(tmp_path, "\n")
    resp = client.get("/projects")
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "api_unconfigured"


def test_every_route_except_health_requires_a_token(tmp_path):
    # Hardcoding a handful of paths lets a future route slip onto the exempt
    # list unnoticed with the suite still green. Sweeping every route FastAPI
    # actually registered -- including the auto-added /openapi.json and
    # /docs -- is what keeps this property true after this test is written.
    client = _client(tmp_path, "secret")
    checked = 0
    for route in every_route(client.app):
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None) or set()
        # The /api mount answers to the browser allowlist, not the bearer
        # token; its own sweep lives in test_api_browser_auth.py.
        if not path or path == "/health" or path.startswith("/api/"):
            continue
        concrete = re.sub(r"\{[^}]+\}", "x", path)
        for method in methods - {"HEAD", "OPTIONS"}:
            checked += 1
            resp = client.request(method, concrete)
            assert resp.status_code == 401, (
                f"{method} {concrete} answered {resp.status_code} without a token")
    assert checked >= 15, "the sweep found fewer routes than this app actually has"


def test_a_non_ascii_authorization_header_fails_closed_without_a_bare_500(tmp_path):
    # Starlette decodes headers as latin-1, so a header carrying a byte above
    # 0x7f is perfectly valid on the wire -- httpx refuses to send one, so
    # this drives the ASGI callable directly. secrets.compare_digest raises
    # TypeError on two `str` args where either has a non-ASCII character; a
    # middleware that lets that escape returns a bare 500 in the wrong error
    # shape (and logs a traceback) for a request whose only fault was being
    # unauthenticated.
    app = _app(tmp_path, "secret")
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "path": "/projects",
        "raw_path": b"/projects",
        "query_string": b"",
        "headers": [(b"authorization", "Bearer sécret".encode("latin-1"))],
        "client": ("test", 1234),
        "server": ("test", 80),
        "scheme": "http",
        "root_path": "",
    }
    messages = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    asyncio.run(app(scope, receive, send))

    start = next(m for m in messages if m["type"] == "http.response.start")
    body = b"".join(m["body"] for m in messages if m["type"] == "http.response.body")
    assert start["status"] == 401, body
    assert b"unauthorized" in body
