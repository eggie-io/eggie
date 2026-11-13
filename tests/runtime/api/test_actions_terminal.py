import json
import threading

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from tests.runtime.api.conftest import AUTH, BROWSER
from tests.runtime.api.test_actions import IOS, make  # noqa: F401
from tests.runtime.api.test_api_public import console

TERMINAL = "/api/projects/app/actions/ios-build/terminal"
ORIGIN = {"Origin": "http://localhost:41080"}


@pytest.fixture
def running(make):
    env = make(recipe_actions=[IOS])
    assert env.client.post("/projects/app/actions/ios-build/start").status_code == 200
    env.browser = console(env.app, env.client)
    return env


def _pumps():
    return [t for t in threading.enumerate() if t.name == "eggie-terminal"]


def _pumps_end():
    for pump in _pumps():
        pump.join(timeout=2)
    return _pumps() == []


def test_the_terminal_relays_both_ways(running):
    with running.browser.websocket_connect(TERMINAL) as ws:
        ws.send_bytes(b"x")
        assert ws.receive_bytes() == b"x"
    assert running.helper.terminals[-1].written == [b"x"]


def test_a_resize_message_reaches_the_terminal(running):
    with running.browser.websocket_connect(TERMINAL) as ws:
        ws.send_text(json.dumps({"resize": [100, 40]}))
        ws.send_bytes(b"sync")
        ws.receive_bytes()
    assert running.helper.terminals[-1].resizes == [(100, 40)]


def test_resize_sizes_are_clamped_and_malformed_frames_ignored(running):
    with running.browser.websocket_connect(TERMINAL) as ws:
        for frame in ("not json", "[1, 2]", '{"resize": [80]}', '{"resize": ["a", 2]}',
                      '{"resize": [true, 2]}', '{"resize": [0, 9999]}'):
            ws.send_text(frame)
        ws.send_bytes(b"sync")
        ws.receive_bytes()
    assert running.helper.terminals[-1].resizes == [(1, 500)]


def test_the_opening_size_comes_from_the_query_clamped(running):
    with running.browser.websocket_connect(TERMINAL + "?cols=0&rows=abc") as ws:
        ws.send_bytes(b"sync")
        ws.receive_bytes()
    assert running.helper.calls[-1] == ("attach", "app", "ios-build", 1, 32)


def test_a_wrong_origin_is_refused_even_with_a_cookie(running):
    running.browser.headers["Origin"] = "http://blog.test.local:41080"
    with pytest.raises(WebSocketDisconnect):
        with running.browser.websocket_connect(TERMINAL):
            pass
    assert "attach" not in running.helper.names()


def test_no_origin_is_refused_even_with_a_cookie(running):
    del running.browser.headers["Origin"]
    with pytest.raises(WebSocketDisconnect):
        with running.browser.websocket_connect(TERMINAL):
            pass
    assert "attach" not in running.helper.names()


def test_the_bearer_port_host_is_refused_even_with_a_cookie(running):
    # Cookies ignore the port, so a page could replay one at localhost:39099.
    running.browser.headers["Host"] = "localhost:39099"
    with pytest.raises(WebSocketDisconnect):
        with running.browser.websocket_connect(TERMINAL):
            pass
    assert "attach" not in running.helper.names()


def test_no_cookie_is_refused(running):
    browser = TestClient(running.app, headers={**BROWSER, **ORIGIN})
    with pytest.raises(WebSocketDisconnect):
        with browser.websocket_connect(TERMINAL):
            pass
    assert "attach" not in running.helper.names()


def test_an_unknown_action_closes_with_4404(running):
    # A well-formed id with no run is the helper's 4409; this one can't be an action at all.
    with running.browser.websocket_connect("/api/projects/app/actions/Bad_Id/terminal") as ws:
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_bytes()
    assert (closed.value.code, closed.value.reason) == (4404, "action_not_found")


def test_an_action_that_is_not_running_closes_with_4409(make):
    env = make(recipe_actions=[IOS])
    with console(env.app, env.client).websocket_connect(TERMINAL) as ws:
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_bytes()
    assert (closed.value.code, closed.value.reason) == (4409, "action_not_running")


def test_the_terminal_hanging_up_closes_the_socket_normally(running):
    with running.browser.websocket_connect(TERMINAL) as ws:
        running.helper.terminals[-1].hang_up()
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_bytes()
    assert closed.value.code == 1000
    assert running.helper.terminals[-1].closed
    assert _pumps_end()


def test_the_browser_leaving_closes_the_terminal_and_its_pump(running):
    with running.browser.websocket_connect(TERMINAL) as ws:
        ws.send_bytes(b"x")
        ws.receive_bytes()
        assert _pumps()
    assert running.helper.terminals[-1].closed
    assert _pumps_end()


def test_there_is_no_terminal_on_the_bearer_side(running):
    bearer = TestClient(running.app, headers=AUTH)
    with pytest.raises(WebSocketDisconnect):
        with bearer.websocket_connect("/projects/app/actions/ios-build/terminal"):
            pass
    assert "attach" not in running.helper.names()
