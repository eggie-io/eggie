"""The installer's smoke test, driven against the real API app.

`verify_step` is the last thing setup does before it tells a non-technical user
everything works, so it is worth proving through the whole seam rather than
against a stub client: the project is created, uploaded, started and torn down
over HTTP, by the same routes a real VM serves. Nothing spawns a process, a
container or a socket -- the app runs in-process over a fake Docker runner.
"""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from eggie_api.routes.app import create_app
from eggie_api.config import ApiConfig
from eggie_api.infra.runner import Completed
from host.client import ApiClient, ApiError
from host.core.constants import DEFAULT_DOMAIN, EDGE_PORT
from host.core.install import VerificationFailed, verify_step

from tests.runtime.api.conftest import PS_RESTARTING, FakeProbe, FakeRunner
from tests.host.test_client_seam import AppOpener

TOKEN = "test-token"
SELFTEST_URL = f"http://eggie-selftest.{DEFAULT_DOMAIN}:{EDGE_PORT}"


@pytest.fixture
def template(tmp_path):
    d = tmp_path / "nginx-hello"
    d.mkdir()
    (d / "docker-compose.yml").write_text(
        'services:\n  web:\n    image: nginx:alpine\n    ports: ["8080:80"]\n')
    return d


@pytest.fixture
def api(tmp_path):
    """The real API app behind a real `ApiClient`."""
    (tmp_path / "api.token").write_text(TOKEN)
    config = ApiConfig(projects_root=tmp_path / "projects",
                         state_db=tmp_path / "state.db",
                         token_path=tmp_path / "api.token",
                         # The API's own readiness window is covered in
                         # test_health.py; this file is about the host's.
                         ready_timeout=0.0)
    runner = FakeRunner()
    probe = FakeProbe()
    app = create_app(config=config, runner=runner, http_probe=probe)
    with TestClient(app) as test_client:
        client = ApiClient(TOKEN, opener=AppOpener(test_client),
                             sleep=lambda _s: time.sleep(0.01))
        yield client, runner, probe


def test_verify_passes_on_200_and_removes_the_smoke_test_project(api, template):
    client, runner, _probe = api
    seen = []

    verify_step(None, template, DEFAULT_DOMAIN, client=client,
                http_get=lambda url: seen.append(url) or 200)

    assert seen == [SELFTEST_URL], \
        "the smoke test must use a reserved id, not the template's folder name"
    assert client.list_projects() == [], \
        "the smoke-test project must not be left behind in the VM"
    assert any("label=com.docker.compose.project=eggie-selftest" in a
               for a in runner.calls), \
        "the smoke-test containers must not be left running"


def test_verify_fails_on_a_non_200_status(api, template):
    client, _runner, _probe = api
    with pytest.raises(VerificationFailed, match="502"):
        verify_step(None, template, DEFAULT_DOMAIN, client=client,
                    http_get=lambda url: 502, ready_timeout=0)


def test_verify_fails_without_requesting_when_the_stack_is_crash_looping(api,
                                                                        template):
    client, runner, _probe = api
    runner.ps = Completed(0, PS_RESTARTING, "")
    requested = []

    with pytest.raises(VerificationFailed, match="crash_looping"):
        verify_step(None, template, DEFAULT_DOMAIN, client=client,
                    http_get=lambda url: requested.append(url) or 200)

    assert requested == [], \
        "a container that never came up must fail before anything is requested"
    assert client.list_projects() == []


def test_verify_tears_down_even_when_the_request_fails(api, template):
    client, _runner, _probe = api

    def http_get(url):
        raise OSError("connection refused")

    with pytest.raises(VerificationFailed):
        verify_step(None, template, DEFAULT_DOMAIN, client=client,
                    http_get=http_get, ready_timeout=0)
    assert client.list_projects() == []


def test_verify_waits_out_the_404_before_traefik_publishes_the_router(api,
                                                                     template):
    # Traefik registers a new router a beat after the container starts. A
    # single-shot request fails a healthy stack with
    # "...did not respond: HTTP Error 404: Not Found".
    from urllib.error import HTTPError

    client, _runner, _probe = api
    codes = iter([404, 404, 200])

    def http_get(url):
        code = next(codes)
        if code != 200:
            raise HTTPError(url, code, "Not Found", {}, None)
        return code

    slept = []
    verify_step(None, template, DEFAULT_DOMAIN, client=client,
                http_get=http_get, sleep=slept.append)
    assert slept, "the smoke test must retry rather than fail on the first 404"


class RefusesTeardown:
    """The client, with `delete_project` broken."""

    def __init__(self, client):
        self._client = client

    def __getattr__(self, name):
        return getattr(self._client, name)

    def delete_project(self, project_id):
        raise ApiError("http_error", "the API answered HTTP 500", 500)


def test_a_teardown_failure_on_the_success_path_is_reported_not_swallowed(
        api, template):
    # Otherwise setup says "finished successfully" while the smoke-test
    # containers keep running and eggie-selftest sits in `eggie status`
    # with nothing to explain it.
    client, _runner, _probe = api
    with pytest.raises(VerificationFailed, match="could not be removed"):
        verify_step(None, template, DEFAULT_DOMAIN, client=RefusesTeardown(client),
                    http_get=lambda url: 200)


def test_a_teardown_failure_never_replaces_the_reason_verification_failed(
        api, template):
    client, _runner, _probe = api
    with pytest.raises(VerificationFailed, match="502"):
        verify_step(None, template, DEFAULT_DOMAIN, client=RefusesTeardown(client),
                    http_get=lambda url: 502, ready_timeout=0)


def test_a_failure_leads_with_a_sentence_and_keeps_the_detail_below(api,
                                                                   template):
    # These are the words someone reads at the moment their install failed.
    # The status enum and the HTTP code are for whoever they send it to.
    client, runner, _probe = api

    with pytest.raises(VerificationFailed) as excinfo:
        verify_step(None, template, DEFAULT_DOMAIN, client=client,
                    http_get=lambda url: 502, ready_timeout=0)
    first, rest = str(excinfo.value).split("\n", 1)
    assert "502" not in first and "502" in rest

    runner.ps = Completed(0, PS_RESTARTING, "")
    with pytest.raises(VerificationFailed) as excinfo:
        verify_step(None, template, DEFAULT_DOMAIN, client=client,
                    http_get=lambda url: 200)
    first, rest = str(excinfo.value).split("\n", 1)
    assert "crash_looping" not in first and "crash_looping" in rest


def test_verify_reports_the_apis_diagnosis_instead_of_polling_a_dead_url(
        api, template):
    # The containers start, but the routed service is bound to 127.0.0.1, so
    # the URL will never answer. The API already knows why; waiting out the
    # readiness window and reporting "did not respond" would throw that away.
    client, _runner, probe = api
    probe.status = 502
    requested = []

    with pytest.raises(VerificationFailed, match="127.0.0.1"):
        verify_step(None, template, DEFAULT_DOMAIN, client=client,
                    http_get=lambda url: requested.append(url) or 200)

    assert requested == []
    assert client.list_projects() == []


def test_smoke_test_template_publishes_no_host_port():
    # The template reaches the browser through Traefik on the edge network, so a
    # published port buys nothing and collides: the first real Windows run died
    # with "failed to bind host port 0.0.0.0:8080/tcp: address already in use".
    import yaml
    from host.core.install import VERIFY_TEMPLATE

    compose = yaml.safe_load((VERIFY_TEMPLATE / "docker-compose.yml").read_text())
    for name, svc in compose["services"].items():
        assert not svc.get("ports"), f"{name} publishes a host port"
