"""The host client driven against the real API app.

The fake-opener tests in `test_client.py` assert the requests the client builds;
this file proves those requests are the ones the API actually serves. A route
renamed, a verb changed, or an error body reshaped fails here rather than on a
real VM. Nothing spawns a process or opens a socket: the app runs in-process
behind an opener adapter, over a fake Docker runner.
"""
from __future__ import annotations

import email.message
import io
import time
import urllib.error

import pytest
from fastapi.testclient import TestClient

from eggie_api.routes.app import create_app
from eggie_api.config import ApiConfig
from eggie_api.infra.runner import Completed
from host.client import ApiClient, ApiError

from tests.runtime.api.conftest import COMPOSE_ONE_WEB, FakeProbe, FakeRunner

TOKEN = "test-token"


class _Response(io.BytesIO):
    def __init__(self, body: bytes, status: int):
        super().__init__(body)
        self.status = status


class AppOpener:
    """Serves urllib Requests with the ASGI app, raising the same HTTPError
    urllib raises so the client's error handling is exercised for real."""

    def __init__(self, client: TestClient):
        self._client = client

    def open(self, request, timeout=None):
        body = request.data
        if hasattr(body, "read"):
            body = body.read()
        headers = {k: v for k, v in request.header_items()
                   if k.lower() != "content-length"}
        response = self._client.request(request.get_method(), request.selector,
                                        content=body, headers=headers)
        if response.status_code >= 400:
            raise urllib.error.HTTPError(request.full_url, response.status_code,
                                         response.reason_phrase,
                                         email.message.Message(),
                                         io.BytesIO(response.content))
        return _Response(response.content, response.status_code)


@pytest.fixture
def seam(tmp_path):
    (tmp_path / "api.token").write_text(TOKEN)
    config = ApiConfig(domain="test.local", edge_port=41080,
                         projects_root=tmp_path / "projects",
                         state_db=tmp_path / "state.db",
                         token_path=tmp_path / "api.token",
                         ready_timeout=0.0)
    runner = FakeRunner()
    # Injected: the real probe would open a socket to a Traefik that does not
    # exist here, and then wait out the whole readiness window doing it.
    app = create_app(config=config, runner=runner, http_probe=FakeProbe())
    with TestClient(app) as test_client:
        # A short real sleep, not the wall-clock poll interval: the job runs on
        # a thread here and finishes in milliseconds.
        client = ApiClient(TOKEN, opener=AppOpener(test_client),
                             sleep=lambda _s: time.sleep(0.01))
        yield client, runner, tmp_path


def test_the_whole_up_flow_works_against_the_real_api(seam, tmp_path):
    client, _runner, _root = seam
    local = tmp_path / "blog"
    local.mkdir()
    (local / "docker-compose.yml").write_text(COMPOSE_ONE_WEB)

    client.ensure_project("blog")
    uploaded = client.upload_directory("blog", local)
    assert [f["path"] for f in uploaded["files"]] == ["docker-compose.yml"]

    job = client.wait_for_job(client.project_up("blog"))
    assert job["result"]["status"] == "started_ok"
    assert job["result"]["urls"] == ["http://blog.test.local:41080"]

    listed = client.list_projects()
    assert [p["id"] for p in listed] == ["blog"]
    assert client.get_project("blog")["status"] == "started_ok"


def test_an_unknown_project_comes_back_as_the_apis_own_message(seam):
    client, _runner, _root = seam
    with pytest.raises(ApiError) as excinfo:
        client.get_project("nope")
    assert excinfo.value.code == "project_not_found"
    assert str(excinfo.value) == "no project with id 'nope'"


def test_a_logs_failure_reaches_the_caller_with_the_guests_stderr(seam):
    client, runner, _root = seam
    runner.logs = Completed(1, "", "no such service: web")
    client.ensure_project("blog")
    with pytest.raises(ApiError) as excinfo:
        client.logs("blog")
    assert excinfo.value.code == "logs_unavailable"
    assert "no such service: web" in str(excinfo.value)


def test_the_connect_step_accepts_the_real_api(seam):
    # Pins the contract: /health carries an api number this host speaks, and
    # /version accepts the host's token.
    from host.core import constants
    from host.core.install import connect_step

    client, _runner, _root = seam
    assert client.health()["api"] in constants.SUPPORTED_API
    assert connect_step(None, client=client) is None
