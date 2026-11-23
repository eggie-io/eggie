import copy

import pytest
from fastapi.testclient import TestClient

from eggie_api.infra.cloud import CloudUnavailable
from eggie_api.rest.app import create_app
from eggie_api.services.desktop_events import DesktopEvents
from tests.runtime.api.conftest import AUTH, BROWSER, FakeRunner

BODY = {
    "event": "signup_started",
    "session_id": "5b0c2f8e-3f0a-4b8e-9d4c-1f2e3a4b5c6d",
    "properties": {"method": "new"},
    "context": {"app_version": "0.9.1", "os": "windows",
                "os_version": "10.0.26100", "arch": "x86_64"},
}


class RecordingCloud:
    def __init__(self, error=None):
        self.sent = []
        self.error = error

    def desktop_event(self, body):
        self.sent.append(body)
        if self.error:
            raise self.error


def client(env, cloud):
    # The relay runs inline so the assertion sees the call.
    events = DesktopEvents(cloud, start=lambda fn: fn())
    return TestClient(create_app(config=env.config, runner=FakeRunner(),
                                 desktop_events=events), headers=AUTH)


def body(**changes):
    out = copy.deepcopy(BODY)
    out.update(changes)
    return out


def test_sign_up_started_is_relayed_unchanged(env):
    cloud = RecordingCloud()
    resp = client(env, cloud).post("/desktop-events", json=BODY)

    assert (resp.status_code, resp.json()) == (202, {})
    assert cloud.sent == [BODY]


def test_screen_shown_without_properties_is_relayed_with_empty_properties(env):
    cloud = RecordingCloud()
    sent = {k: v for k, v in BODY.items() if k != "properties"}
    sent["event"] = "signup_screen_shown"
    resp = client(env, cloud).post("/desktop-events", json=sent)

    assert resp.status_code == 202
    assert cloud.sent == [{**sent, "properties": {}}]


@pytest.mark.parametrize("bad", [
    body(event="app_launch", properties={"background": False}),
    body(properties={"method": "new", "email": "a@b"}),
    body(properties={"method": "other"}),
    body(properties={}),
    body(event="signup_screen_shown"),
    body(session_id="../x"),
    body(session_id=BODY["session_id"].upper()),
    body(context={**BODY["context"], "os": "linux"}),
    body(context={**BODY["context"], "extra": "x"}),
    body(context={"app_version": "0.9.1", "os": "macos", "os_version": "1"}),
    body(context={**BODY["context"], "arch": "x/86"}),
    {**BODY, "extra": 1},
    [BODY],
])
def test_a_body_the_console_did_not_build_is_refused_and_not_relayed(env, bad):
    cloud = RecordingCloud()
    resp = client(env, cloud).post("/desktop-events", json=bad)

    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid"
    assert cloud.sent == []


def test_a_service_failure_still_answers_202(env):
    cloud = RecordingCloud(CloudUnavailable("down"))

    assert client(env, cloud).post("/desktop-events", json=BODY).status_code == 202


def test_the_browser_route_needs_a_session(env):
    resp = TestClient(env.app, headers={**BROWSER, "Origin": "http://localhost:41080"}).post("/api/desktop-events", json=BODY)

    assert resp.status_code == 401
