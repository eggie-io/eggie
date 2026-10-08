import threading

from eggie_api.core.exec import Completed
from tests.runtime.api.conftest import (PS_RESTARTING, _create, _run_to_completion,
                                        _write_compose)

COMPOSE_TWO = """
services:
  web:
    image: nginx
    ports: ["8080:80"]
    environment:
      API_KEY: ${API_KEY}
      MODE: ${MODE:-dev}
  worker:
    image: busybox
"""

SECRET = "s3cr3t-value-never-echoed"


def _project(env, pid="blog", compose=COMPOSE_TWO):
    _create(env, pid)
    _write_compose(env, pid, compose)
    return env.config.projects_root / pid


def _every_response_text(env, pid):
    paths = [f"/projects/{pid}/secrets", f"/projects/{pid}", "/projects"]
    return "".join(env.client.get(p).text for p in paths)


def test_a_set_secret_is_listed_by_name_and_its_value_never_comes_back(env):
    _project(env)
    put = env.client.put("/projects/blog/secrets/API_KEY", json={"value": SECRET})
    assert put.status_code == 204
    assert put.text == ""
    body = env.client.get("/projects/blog/secrets").json()
    assert [s["name"] for s in body["secrets"]] == ["API_KEY"]
    assert SECRET not in _every_response_text(env, "blog")


def test_an_invalid_or_reserved_name_is_a_400_with_its_code(env):
    _project(env)
    bad = env.client.put("/projects/blog/secrets/1BAD", json={"value": "x"})
    assert (bad.status_code, bad.json()["error"]["code"]) == (400, "secret_name_invalid")
    reserved = env.client.put("/projects/blog/secrets/DOCKER_HOST", json={"value": "x"})
    assert reserved.json()["error"]["code"] == "secret_name_reserved"


def test_the_project_total_counts_secrets_already_stored(env):
    _project(env)
    for i in range(8):
        assert env.client.put(f"/projects/blog/secrets/K{i}",
                              json={"value": "x" * 60000}).status_code == 204
    over = env.client.put("/projects/blog/secrets/K8", json={"value": "x" * 60000})
    assert over.json()["error"]["code"] == "secrets_too_large"


def test_deleting_an_unknown_secret_is_a_404(env):
    _project(env)
    resp = env.client.delete("/projects/blog/secrets/NOPE")
    assert (resp.status_code, resp.json()["error"]["code"]) == (404, "secret_not_found")


def test_start_hands_every_secret_to_compose_up_and_writes_no_value(env):
    folder = _project(env)
    env.client.put("/projects/blog/secrets/API_KEY", json={"value": SECRET})
    _run_to_completion(env, env.client.post("/projects/blog/up"))
    ups = [e for a, e in zip(env.runner.calls, env.runner.envs) if a[-2:] == ["up", "-d"]]
    assert ups == [{"API_KEY": SECRET}]
    assert all(SECRET not in word for argv in env.runner.calls for word in argv)
    assert SECRET not in "".join(p.read_text(errors="replace")
                                 for p in folder.rglob("*") if p.is_file())


def test_every_compose_call_gets_the_secrets_and_no_other_call_does(env):
    # compose interpolates the whole file for ps, down and logs too, so a
    # `${KEY:?}` that only `up` can resolve breaks every other command.
    _project(env)
    env.client.put("/projects/blog/secrets/API_KEY", json={"value": SECRET})
    env.probe.status = 502
    _run_to_completion(env, env.client.post("/projects/blog/up"))
    _run_to_completion(env, env.client.post("/projects/blog/restart"))
    _run_to_completion(env, env.client.post("/projects/blog/down"))
    env.client.get("/projects/blog/logs")
    env.client.get("/projects/blog/logs", params={"follow": True})
    calls = list(zip(env.runner.calls, env.runner.envs))
    compose = [(a, e) for a, e in calls if a[1:2] == ["compose"]]
    verbs = {next(w for w in ("up", "-q", "ps", "down", "logs") if w in a)
             for a, _ in compose}
    assert verbs == {"up", "-q", "ps", "down", "logs"}
    assert all(e == {"API_KEY": SECRET} for _, e in compose), compose
    assert all(e is None for a, e in calls if a[1:2] != ["compose"])


def test_compose_calls_of_a_project_without_secrets_get_no_env(env):
    _project(env)
    _run_to_completion(env, env.client.post("/projects/blog/up"))
    env.client.get("/projects/blog/logs")
    assert all(e is None for e in env.runner.envs)


def test_restart_needed_follows_changes_after_the_last_start(env):
    _project(env)
    _run_to_completion(env, env.client.post("/projects/blog/up"))
    assert env.client.get("/projects/blog").json()["restart_needed"] is False
    env.client.put("/projects/blog/secrets/API_KEY", json={"value": "v"})
    assert env.client.get("/projects/blog").json()["restart_needed"] is True
    assert env.client.get("/projects/blog/secrets").json()["restart_needed"] is True
    _run_to_completion(env, env.client.post("/projects/blog/restart"))
    assert env.client.get("/projects/blog").json()["restart_needed"] is False


def test_a_stopped_project_never_needs_a_restart(env):
    _project(env)
    env.client.put("/projects/blog/secrets/API_KEY", json={"value": "v"})
    assert env.client.get("/projects/blog").json()["restart_needed"] is False


def test_a_secret_set_during_a_start_still_needs_a_restart(env):
    _project(env)
    env.runner.up_gate = threading.Event()
    started = env.client.post("/projects/blog/up")
    # compose up is now blocked: the values it got were read before this set.
    env.client.put("/projects/blog/secrets/API_KEY", json={"value": "late"})
    env.runner.up_gate.set()
    _run_to_completion(env, started)
    assert env.client.get("/projects/blog").json()["restart_needed"] is True


def test_a_crash_looping_project_needs_a_restart_after_a_secret_change(env):
    # A service exiting for lack of a key is the usual crash loop, and the
    # fix is exactly a secret set followed by a restart.
    _project(env)
    env.runner.ps = Completed(0, PS_RESTARTING, "")
    _run_to_completion(env, env.client.post("/projects/blog/up"))
    assert env.client.get("/projects/blog").json()["restart_needed"] is False
    env.client.put("/projects/blog/secrets/API_KEY", json={"value": "v"})
    assert env.client.get("/projects/blog").json()["restart_needed"] is True
    _run_to_completion(env, env.client.post("/projects/blog/restart"))
    assert env.client.get("/projects/blog").json()["restart_needed"] is False


def test_secrets_routes_are_served_to_both_the_cli_and_the_console(env):
    from tests.runtime.api.route_sweep import every_route
    paths = {getattr(r, "path", None) for r in every_route(env.app)}
    for path in ("/projects/{project_id}/secrets",
                 "/projects/{project_id}/secrets/{name}",
                 "/projects/{project_id}/secret-requests/{name}"):
        assert path in paths and "/api" + path in paths, path


COMPOSE_DECLARES = """
services:
  web:
    image: nginx
    ports: ["8080:80"]
    environment:
      DATABASE_URL: postgres://db/app
"""


def _overlay_names(env, folder):
    import base64, yaml
    write = [a for a in env.runner.calls if a[0] == "bash" and "overlay.yml" in a[-1]][-1]
    text = base64.b64decode(write[-1].split("echo ", 1)[1].split(" ", 1)[0]).decode()
    return yaml.safe_load(text)["services"]


def test_a_service_that_declares_a_name_keeps_its_own_value(env):
    folder = _project(env, compose=COMPOSE_DECLARES)
    env.client.put("/projects/blog/secrets/DATABASE_URL", json={"value": "x"})
    _run_to_completion(env, env.client.post("/projects/blog/up"))
    assert "environment" not in _overlay_names(env, folder).get("web", {})


def test_an_empty_value_is_refused(env):
    _project(env)
    resp = env.client.put("/projects/blog/secrets/API_KEY", json={"value": ""})
    assert (resp.status_code, resp.json()["error"]["code"]) == (400, "secret_invalid_value")


def test_a_request_is_listed_with_its_hint_until_a_value_is_saved(env):
    _project(env)
    r = env.client.put("/projects/blog/secret-requests/STRIPE_KEY",
                       json={"hint": "Stripe → Developers → API keys"})
    assert r.status_code == 204
    body = env.client.get("/projects/blog/secrets").json()
    assert body["requested"] == [{"name": "STRIPE_KEY", "hint": "Stripe → Developers → API keys"}]
    assert env.client.get("/projects/blog").json()["secrets_requested"] == 1
    env.client.put("/projects/blog/secrets/STRIPE_KEY", json={"value": SECRET})
    assert env.client.get("/projects/blog/secrets").json()["requested"] == []


def test_a_request_with_a_bad_hint_or_name_is_a_400(env):
    _project(env)
    assert env.client.put("/projects/blog/secret-requests/STRIPE_KEY",
                          json={"hint": "a\x00b"}).json()["error"]["code"] == "secret_hint_invalid"
    assert env.client.put("/projects/blog/secret-requests/DOCKER_HOST",
                          json={"hint": "x"}).json()["error"]["code"] == "secret_name_reserved"


def test_dismissing_a_request_does_not_need_a_restart(env):
    _project(env)
    _run_to_completion(env, env.client.post("/projects/blog/up"))
    env.client.put("/projects/blog/secret-requests/STRIPE_KEY", json={"hint": "x"})
    assert env.client.delete("/projects/blog/secrets/STRIPE_KEY").status_code == 204
    body = env.client.get("/projects/blog/secrets").json()
    assert body["requested"] == [] and body["restart_needed"] is False


def test_the_projects_env_files_are_neither_read_nor_touched(env):
    folder = _project(env)
    (folder / ".env").write_text("API_KEY=from-dotenv\nMODE=prod\n")
    (folder / ".env.example").write_text("API_KEY=\nEXTRA=1\n")
    env.client.put("/projects/blog/secrets/API_KEY", json={"value": SECRET})
    _run_to_completion(env, env.client.post("/projects/blog/up"))
    compose = [e for a, e in zip(env.runner.calls, env.runner.envs) if a[1:2] == ["compose"]]
    assert compose and all(e == {"API_KEY": SECRET} for e in compose)
    assert (folder / ".env").read_text() == "API_KEY=from-dotenv\nMODE=prod\n"
    assert (folder / ".env.example").read_text() == "API_KEY=\nEXTRA=1\n"


def test_purge_drops_secrets_and_requests_and_plain_delete_keeps_them(env):
    _project(env, "kept")
    env.client.put("/projects/kept/secrets/A", json={"value": "1"})
    env.client.put("/projects/kept/secret-requests/B", json={"hint": "x"})
    env.client.delete("/projects/kept")
    assert env.state.secret_values("kept") == {"A": "1"}
    assert env.state.secret_requests("kept") == [{"name": "B", "hint": "x"}]

    _project(env, "gone")
    env.client.put("/projects/gone/secrets/A", json={"value": "1"})
    env.client.put("/projects/gone/secret-requests/B", json={"hint": "x"})
    env.client.delete("/projects/gone?purge=true")
    assert env.state.secret_values("gone") == {}
    assert env.state.secret_requests("gone") == []
