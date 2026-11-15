import threading
import time

import pytest
from starlette.websockets import WebSocketDisconnect

from eggie_api.infra.actions_helper import HelperError, HelperUnavailable
from eggie_api.infra.cloud import CloudUnavailable
from tests.runtime.api.conftest import _make_env
from tests.runtime.api.fake_cloud import FakeCloud
from tests.runtime.api.fake_helper import FakeHelper
from tests.runtime.api.test_actions import Clock
from tests.runtime.api.test_api_public import console

CHECK = "npx eas-cli whoami"
LOGIN = "npx eas-cli login --sso"
EXPO = {"id": "expo", "name": "Expo", "description": None,
        "login": {"command": LOGIN, "link_after": "Open ", "code_after": "Code:"},
        "check": CHECK,
        "questions": [{"id": "continue", "match": "Do you want to continue?", "reply": ""},
                      {"id": "otp", "match": "One-time password:", "label": "Code from your app",
                       "type": "secret"}]}
GITHUB = {"id": "github", "name": "GitHub", "description": None,
          "login": {"command": "gh auth login", "link_after": None, "code_after": None},
          "check": None, "questions": []}
APPLE = {"id": "apple", "name": "Apple", "description": None, "login": None, "check": None,
         "questions": [{"id": "apple-id", "match": "Apple ID: ", "label": "Apple ID"}]}
KEY = ("connection", "expo")


class Spawn:
    """Runs the check inline, or keeps it until `release()` while `hold` is set."""

    def __init__(self):
        self.hold = False
        self.held = []

    def __call__(self, fn):
        if self.hold:
            self.held.append(fn)
        else:
            fn()

    def release(self):
        for fn in self.held:
            fn()
        self.held.clear()


@pytest.fixture
def make(tmp_path):
    gens = []

    def _make(*connections, helper=None, catalog=None):
        cloud = FakeCloud(connections=catalog or [{"connections": list(connections)}])
        helper, clock, spawn = helper or FakeHelper(), Clock(), Spawn()
        gen = _make_env(tmp_path, cloud=cloud, actions_helper=helper, clock=clock,
                        connection_spawn=spawn)
        env = next(gen)
        gens.append(gen)
        env.cloud, env.helper, env.clock, env.spawn = cloud, helper, clock, spawn
        return env
    yield _make
    for gen in gens:
        gen.close()


def _error(resp):
    return resp.status_code, resp.json()["error"]["code"]


def _screen(rows, cursor_y, history=0, text=None):
    return {"rows": list(rows), "cursor_y": cursor_y, "history_size": history,
            "text": "\n".join(rows) if text is None else text}


def _state(env, cid="expo"):
    resp = env.client.get(f"/connections/{cid}")
    assert resp.status_code == 200, resp.text
    return resp.json()["state"]


def _login(env, cid="expo"):
    resp = env.client.post(f"/connections/{cid}/login")
    assert resp.status_code == 200, resp.text


def _checks(env):
    return env.helper.names().count("check")


@pytest.mark.parametrize("exit_code, state", [(0, "connected"), (1, "signed-out"),
                                              (None, "unknown")])
def test_the_check_exit_code_sets_the_state(make, exit_code, state):
    env = make(EXPO)
    env.helper.checks[CHECK] = exit_code
    assert _state(env) == state


def test_while_the_check_runs_the_state_is_checking(make):
    env = make(EXPO)
    env.spawn.hold = True
    assert _state(env) == "checking"
    assert _state(env) == "checking"
    assert len(env.spawn.held) == 1
    env.spawn.release()
    assert _state(env) == "connected"


def test_without_a_check_a_login_that_exits_zero_reads_connected(make):
    env = make(GITHUB)
    assert _state(env, "github") == "unknown"
    _login(env, "github")
    env.helper.runs[("connection", "github")].update(state="exited", exit_code=0)
    assert _state(env, "github") == "connected"


def test_the_state_is_checked_again_once_a_login_starts_and_ends(make):
    env = make(EXPO)
    _state(env)
    _state(env)
    assert _checks(env) == 1
    _login(env)
    _state(env)
    _state(env)
    assert _checks(env) == 2
    env.helper.runs[KEY].update(state="exited", exit_code=0)
    _state(env)
    _state(env)
    assert _checks(env) == 3


def test_login_runs_the_catalog_command_for_fifteen_minutes(make):
    env = make(EXPO)
    _login(env)
    assert ("start", KEY, LOGIN, False, 900) in env.helper.calls


def test_the_link_and_code_come_from_the_login_screen(make):
    env = make(EXPO)
    _login(env)
    rows = ["npm warn deprecated inflight@1.0.6", "see https://docs.expo.dev",
            "Open https://expo.dev/device?code=AB", "Code: AB12-CD34", ""]
    env.helper.screens[KEY] = _screen(rows, 4)
    run = env.client.get("/connections/expo").json()["run"]
    assert (run["link"], run["code"], run["asking"]) == (
        "https://expo.dev/device?code=AB", "AB12-CD34", None)


def test_a_reply_question_is_typed_once(make):
    env = make(EXPO)
    _login(env)
    env.helper.screens[KEY] = _screen(["Do you want to continue? › (Y/n)"], 0)
    env.client.get("/connections/expo")
    run = env.client.get("/connections/expo").json()["run"]
    assert env.helper.typed == [(KEY, "")]
    assert run["asking"] is None


def test_a_reflowed_screen_does_not_type_the_reply_again(make):
    env = make(EXPO)
    _login(env)
    rows = ["Do you want to continue? › (Y/n)"]
    env.helper.screens[KEY] = _screen(rows, 0)
    env.client.get("/connections/expo")
    env.helper.screens[KEY] = _screen(["", "", "", *rows], 3, text="\n".join(rows))
    env.client.get("/connections/expo")
    assert env.helper.typed == [(KEY, "")]


def test_an_echo_then_a_reflow_does_not_type_the_reply_again(make):
    env = make(EXPO)
    _login(env)
    env.helper.screens[KEY] = _screen(["Do you want to continue? › (Y/n)"], 0)
    env.client.get("/connections/expo")
    echoed = ["Do you want to continue? › (Y/n) y"]
    env.helper.screens[KEY] = _screen(echoed, 0)
    env.client.get("/connections/expo")
    env.helper.screens[KEY] = _screen(["", "", "", *echoed], 3, text="\n".join(echoed))
    env.client.get("/connections/expo")
    assert env.helper.typed == [(KEY, "")]


def test_a_new_login_answers_its_prompts_afresh(make):
    env = make(EXPO)
    _login(env)
    env.helper.screens[KEY] = _screen(["Do you want to continue? › (Y/n)"], 0)
    env.client.get("/connections/expo")
    env.helper.runs[KEY].update(state="exited", exit_code=1)
    # The fake's started_at never changes, like a real restart within the same second.
    _login(env)
    env.client.get("/connections/expo")
    assert env.helper.typed == [(KEY, ""), (KEY, "")]


def test_a_reply_the_helper_refuses_to_type_is_actions_failed(make):
    class RefusingHelper(FakeHelper):
        def type(self, target, text):
            raise HelperError("failed", "tmux said no")

    env = make(EXPO, helper=RefusingHelper())
    _login(env)
    env.helper.screens[KEY] = _screen(["Do you want to continue? › (Y/n)"], 0)
    assert _error(env.client.get("/connections/expo")) == (502, "actions_failed")


def test_two_readers_at_once_type_a_reply_once(make):
    class SlowHelper(FakeHelper):
        # Slow between seeing the question and remembering the reply: the race window.
        def type(self, target, text):
            time.sleep(0.05)
            super().type(target, text)

    env = make(EXPO, helper=SlowHelper())
    _login(env)
    env.helper.screens[KEY] = _screen(["Do you want to continue? › (Y/n)"], 0)
    service = env.app.state.services.connections
    readers = [threading.Thread(target=service.get, args=("expo",)) for _ in range(2)]
    for t in readers:
        t.start()
    for t in readers:
        t.join()
    assert env.helper.typed == [(KEY, "")]


def test_an_answered_question_asks_again_only_once_the_tool_asks_again(make):
    env = make(EXPO)
    _login(env)
    env.helper.screens[KEY] = _screen(["One-time password:"], 0)
    assert env.client.get("/connections/expo").json()["run"]["asking"] == {
        "source": "expo", "id": "otp", "label": "Code from your app", "type": "secret"}
    resp = env.client.post("/connections/expo/answer",
                           json={"source": "expo", "question": "otp", "value": "123456"})
    assert resp.status_code == 200, resp.text
    assert env.helper.typed == [(KEY, "123456")]
    # The tool echoes the answer on the same row: still the answered prompt.
    env.helper.screens[KEY] = _screen(["One-time password: ******"], 0)
    assert env.client.get("/connections/expo").json()["run"]["asking"] is None
    rows = ["One-time password: ******", "Invalid code", "One-time password:"]
    env.helper.screens[KEY] = _screen(rows, 2)
    assert env.client.get("/connections/expo").json()["run"]["asking"]["id"] == "otp"


def test_an_answer_to_a_question_not_asking_is_not_asking(make):
    env = make(EXPO)
    _login(env)
    env.helper.screens[KEY] = _screen(["Waiting for the browser..."], 0)
    resp = env.client.post("/connections/expo/answer",
                           json={"source": "expo", "question": "otp", "value": "123456"})
    assert _error(resp) == (409, "not_asking")
    assert env.helper.typed == []


def test_an_invalid_answer_is_refused_without_echoing_it(make):
    env = make(EXPO)
    _login(env)
    env.helper.screens[KEY] = _screen(["One-time password:"], 0)
    resp = env.client.post("/connections/expo/answer",
                           json={"source": "expo", "question": "otp", "value": "hunter2\nx"})
    assert _error(resp) == (400, "invalid_answer")
    assert "hunter2" not in resp.text
    resp = env.client.post("/connections/expo/answer", content=b'{"value": "hunter2"',
                           headers={"Content-Type": "application/json"})
    assert _error(resp) == (400, "invalid_answer")
    assert "hunter2" not in resp.text
    assert env.helper.typed == []


def test_an_invalid_answer_is_refused_even_while_the_helper_is_down(make):
    env = make(EXPO)
    env.helper.fail = HelperUnavailable("no socket")
    resp = env.client.post("/connections/expo/answer",
                           json={"source": "expo", "question": "otp", "value": "a\nb"})
    assert _error(resp) == (400, "invalid_answer")


def test_an_unknown_connection_is_connection_not_found(make):
    env = make(EXPO)
    assert _error(env.client.get("/connections/nope")) == (404, "connection_not_found")
    assert _error(env.client.post("/connections/Bad_Id/login")) == (404, "connection_not_found")


def test_a_catalog_that_cannot_be_fetched_is_its_own_error(make):
    env = make(catalog=[CloudUnavailable("down")])
    assert _error(env.client.get("/connections/expo")) == (503, "cloud_unavailable")


def test_login_without_a_login_is_no_login(make):
    env = make(APPLE)
    assert _error(env.client.post("/connections/apple/login")) == (400, "no_login")
    assert "start" not in env.helper.names()


def test_a_second_login_while_one_runs_is_connection_running(make):
    env = make(EXPO)
    _login(env)
    assert _error(env.client.post("/connections/expo/login")) == (409, "connection_running")


def test_the_connection_terminal_refuses_a_wrong_origin(make):
    env = make(EXPO)
    _login(env)
    browser = console(env.app, env.client)
    browser.headers["Origin"] = "http://blog.test.local:41080"
    with pytest.raises(WebSocketDisconnect):
        with browser.websocket_connect("/api/connections/expo/terminal"):
            pass
    assert "attach" not in env.helper.names()


def test_the_connection_terminal_attaches_to_its_session(make):
    env = make(EXPO)
    _login(env)
    with console(env.app, env.client).websocket_connect("/api/connections/expo/terminal") as ws:
        ws.send_bytes(b"x")
        assert ws.receive_bytes() == b"x"
    assert env.helper.calls[-1][:2] == ("attach", KEY)
