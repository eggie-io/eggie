import os

import pytest
import yaml

from eggie_api.infra.actions_helper import HelperUnavailable
from eggie_api.infra.cloud import CloudError, CloudUnavailable
from tests.runtime.api.conftest import _make_env, _run_to_completion, _write_compose
from tests.runtime.api.fake_cloud import FakeCloud
from tests.runtime.api.fake_helper import FakeHelper
from tests.runtime.api.test_api_public import console

IOS = {"actions": [{"id": "ios-build", "name": "Build for iPhone", "description": None,
                    "command": "eas build -p ios", "type": "long-running"}]}
LOGIN = {"actions": [{"id": "expo-login", "name": "Sign in to Expo", "description": None,
                      "command": "eas login", "type": "one-off"}]}


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def make(tmp_path):
    gens = []

    def _make(recipes=("mobile-app",), **replies):
        cloud, helper, clock = FakeCloud(**replies), FakeHelper(), Clock()
        gen = _make_env(tmp_path, cloud=cloud, actions_helper=helper, clock=clock)
        env = next(gen)
        gens.append(gen)
        env.cloud, env.helper, env.clock = cloud, helper, clock
        _write_compose(env, "app")
        assert env.client.post("/projects",
                               json={"id": "app", "origin": "folder"}).status_code == 201
        eggie = env.config.projects_root / "app" / ".eggie"
        eggie.mkdir(exist_ok=True)
        (eggie / "project.yml").write_text(yaml.safe_dump({"recipes": list(recipes)}))
        return env
    yield _make
    for gen in gens:
        gen.close()


def _error(resp):
    return resp.status_code, resp.json()["error"]["code"]


def test_actions_come_from_the_service_not_the_project_folder(make):
    env = make(recipe_actions=[IOS])
    folder = env.config.projects_root / "app"
    planted = [{"id": "rm-all", "name": "x", "command": "rm -rf ~", "type": "one-off"}]
    (folder / ".eggie" / "project.yml").write_text(
        yaml.safe_dump({"recipes": ["mobile-app"], "actions": planted}))
    (folder / ".eggie" / "actions.yml").write_text(yaml.safe_dump({"actions": planted}))
    (folder / "actions.yml").write_text(yaml.safe_dump({"actions": planted}))
    out = env.client.get("/projects/app/actions").json()
    assert [(a["id"], a["command"], a["run"]) for a in out["actions"]] == [
        ("ios-build", "eas build -p ios", None)]
    assert out["problems"] == []


def test_start_sends_the_helper_the_services_command_for_the_id(make):
    env = make(recipe_actions=[IOS])
    resp = env.client.post("/projects/app/actions/ios-build/start")
    assert resp.status_code == 200, resp.text
    assert resp.json()["run"]["state"] == "running"
    assert env.helper.calls[-1] == ("start", "app", "ios-build", "eas build -p ios", True)


def test_an_id_this_projects_recipes_do_not_define_is_action_not_found(make):
    env = make(recipe_actions=[IOS])
    resp = env.client.post("/projects/app/actions/rm-all/start")
    assert _error(resp) == (404, "action_not_found")
    assert "start" not in env.helper.names()


def test_a_service_answer_is_reused_for_five_minutes(make):
    env = make(recipe_actions=[IOS, IOS])
    env.client.get("/projects/app/actions")
    env.clock.now += 299
    env.client.get("/projects/app/actions")
    assert env.cloud.names().count("recipe_actions") == 1
    env.clock.now += 2
    env.client.get("/projects/app/actions")
    assert env.cloud.names().count("recipe_actions") == 2


def test_an_unreachable_service_is_a_problem_not_a_failure(make):
    env = make(recipe_actions=[CloudUnavailable("down")])
    resp = env.client.get("/projects/app/actions")
    assert resp.status_code == 200
    assert resp.json()["actions"] == []
    assert resp.json()["problems"][0]["code"] == "cloud_unavailable"
    assert resp.json()["problems"][0]["recipe"] == "mobile-app"


def test_a_failed_answer_is_not_cached(make):
    env = make(recipe_actions=[CloudUnavailable("down"), IOS])
    env.client.get("/projects/app/actions")
    out = env.client.get("/projects/app/actions").json()
    assert [a["id"] for a in out["actions"]] == ["ios-build"]


def test_start_while_the_service_is_unreachable_is_503_cloud_unavailable(make):
    env = make(recipe_actions=[CloudUnavailable("down")])
    resp = env.client.post("/projects/app/actions/ios-build/start")
    assert _error(resp) == (503, "cloud_unavailable")


def test_a_project_yml_that_cannot_be_read_is_a_problem_not_an_empty_list(make):
    env = make(recipe_actions=[IOS])
    (env.config.projects_root / "app/.eggie/project.yml").write_text("recipes: [unclosed\n")
    out = env.client.get("/projects/app/actions").json()
    assert out["actions"] == []
    assert [(p["recipe"], p["code"]) for p in out["problems"]] == [(None, "invalid_project_yml")]
    assert "project.yml" in out["problems"][0]["message"]
    assert _error(env.client.post("/projects/app/actions/ios-build/start")) == (
        422, "invalid_project_yml")


def test_a_recipe_the_service_no_longer_has_is_a_problem(make):
    env = make(recipes=("mobile-app", "gone"),
               recipe_actions=[IOS, CloudError("recipe_not_found", "m", 404)])
    out = env.client.get("/projects/app/actions").json()
    assert [a["id"] for a in out["actions"]] == ["ios-build"]
    assert [(p["recipe"], p["code"]) for p in out["problems"]] == [("gone", "recipe_not_found")]


def test_an_id_two_recipes_define_is_not_runnable(make):
    env = make(recipes=("mobile-app", "web-app"), recipe_actions=[IOS, IOS])
    out = env.client.get("/projects/app/actions").json()
    assert out["actions"] == []
    assert out["problems"][0]["code"] == "duplicate_action"
    resp = env.client.post("/projects/app/actions/ios-build/start")
    assert _error(resp) == (404, "action_not_found")


def test_a_bad_entry_from_the_service_is_dropped_with_a_problem(make):
    bad = {"actions": [*IOS["actions"], {"id": "Bad Id", "name": "x", "command": "x",
                                         "type": "one-off"}]}
    env = make(recipe_actions=[bad])
    out = env.client.get("/projects/app/actions").json()
    assert [a["id"] for a in out["actions"]] == ["ios-build"]
    assert [(p["recipe"], p["code"]) for p in out["problems"]] == [
        ("mobile-app", "invalid_action")]


def test_list_shows_the_current_run(make):
    env = make(recipe_actions=[IOS])
    env.client.post("/projects/app/actions/ios-build/start")
    out = env.client.get("/projects/app/actions").json()
    assert out["actions"][0]["run"]["state"] == "running"


def test_list_still_shows_actions_when_the_helper_is_down(make):
    env = make(recipe_actions=[IOS])
    env.helper.fail = HelperUnavailable("no socket")
    resp = env.client.get("/projects/app/actions")
    assert resp.status_code == 200
    assert [(a["id"], a["run"]) for a in resp.json()["actions"]] == [("ios-build", None)]
    assert resp.json()["problems"] == [
        {"recipe": None, "code": "actions_unavailable",
         "message": resp.json()["problems"][0]["message"]}]


def test_start_while_running_is_409(make):
    env = make(recipe_actions=[IOS])
    env.client.post("/projects/app/actions/ios-build/start")
    resp = env.client.post("/projects/app/actions/ios-build/start")
    assert _error(resp) == (409, "action_running")


def test_stop_needs_no_service(make):
    env = make(recipe_actions=[CloudUnavailable("down")])
    env.helper.runs[("app", "ios-build")] = {"state": "running", "exit_code": None,
                                             "started_at": 1}
    resp = env.client.post("/projects/app/actions/ios-build/stop")
    assert resp.status_code == 200, resp.text
    assert resp.json()["run"]["state"] == "exited"
    assert "recipe_actions" not in env.cloud.names()


def test_a_long_running_start_asks_for_a_log_and_a_one_off_does_not(make):
    env = make(recipes=("mobile-app", "expo"), recipe_actions=[IOS, LOGIN])
    env.client.post("/projects/app/actions/ios-build/start")
    env.client.post("/projects/app/actions/expo-login/start")
    starts = {c[2]: c[4] for c in env.helper.calls if c[0] == "start"}
    assert starts == {"ios-build": True, "expo-login": False}


def test_stopping_the_project_stops_its_actions(make):
    env = make()
    _run_to_completion(env, env.client.post("/projects/app/down"))
    assert ("stop_project", "app") in env.helper.calls


def test_deleting_the_project_stops_its_actions(make):
    env = make()
    assert env.client.delete("/projects/app").status_code == 200
    assert ("stop_project", "app") in env.helper.calls


def test_a_project_stops_even_when_the_helper_is_down(make):
    env = make()
    env.helper.fail = HelperUnavailable("no socket")
    job = _run_to_completion(env, env.client.post("/projects/app/down"))
    assert job["state"] == "done", job
    assert env.client.delete("/projects/app").status_code == 200


def test_a_helper_that_is_down_is_503_actions_unavailable_on_start(make):
    env = make(recipe_actions=[IOS])
    env.helper.fail = HelperUnavailable("no socket")
    resp = env.client.post("/projects/app/actions/ios-build/start")
    assert _error(resp) == (503, "actions_unavailable")


def test_the_log_is_the_tail_of_the_actions_log_file(make):
    env = make()
    folder = env.config.actions_logs / "app"
    folder.mkdir(parents=True)
    (folder / "ios-build.log").write_bytes(b"x" * 100 + b"last line\n")
    browser = console(env.app, env.client)
    resp = browser.get("/api/projects/app/actions/ios-build/log?tail=10")
    assert resp.status_code == 200
    assert resp.text == "last line\n"
    assert browser.get("/api/projects/app/actions/expo-login/log").text == ""


def test_a_log_that_is_not_a_regular_file_reads_as_empty(make):
    env = make()
    folder = env.config.actions_logs / "app"
    folder.mkdir(parents=True)
    os.mkfifo(folder / "ios-build.log")
    assert env.app.state.services.actions.log("app", "ios-build") == ""


def test_the_log_route_is_console_only(make):
    env = make()
    resp = env.client.get("/projects/app/actions/ios-build/log")
    assert resp.status_code in (404, 405)
