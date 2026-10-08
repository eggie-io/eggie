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


def test_missing_lists_expected_names_that_have_no_secret(env):
    folder = _project(env)
    (folder / ".env.example").write_text("API_KEY=\nSMTP_PASSWORD=\n")
    env.client.put("/projects/blog/secrets/SMTP_PASSWORD", json={"value": "x"})
    assert env.client.get("/projects/blog/secrets").json()["missing"] == ["API_KEY"]


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


def test_import_moves_dotenv_values_into_secrets_and_empties_the_file(env):
    # Emptied, not deleted: a service with `env_file: .env` fails to start
    # when the file is gone.
    folder = _project(env)
    (folder / ".env").write_text('API_KEY=from-file\nexport PEM="a\\nb"\n')
    listed = env.client.get("/projects/blog/secrets").json()
    assert listed["dotenv"] == {"names": ["API_KEY", "PEM"], "error": None}
    env.client.put("/projects/blog/secrets/API_KEY", json={"value": "old"})
    resp = env.client.post("/projects/blog/secrets/import-dotenv")
    assert resp.json() == {"imported": ["API_KEY", "PEM"]}
    assert (folder / ".env").read_bytes() == b""
    assert env.state.secret_values("blog") == {"API_KEY": "from-file", "PEM": "a\nb"}
    assert env.client.get("/projects/blog/secrets").json()["dotenv"] is None


def test_import_of_an_invalid_dotenv_stores_nothing_and_keeps_the_file(env):
    folder = _project(env)
    (folder / ".env").write_text("GOOD=1\nnot a pair\n")
    listed = env.client.get("/projects/blog/secrets").json()
    assert listed["dotenv"]["names"] == []
    assert "line 2" in listed["dotenv"]["error"]
    resp = env.client.post("/projects/blog/secrets/import-dotenv")
    assert (resp.status_code, resp.json()["error"]["code"]) == (400, "dotenv_invalid")
    assert (folder / ".env").exists()
    assert env.state.secret_values("blog") == {}


def test_a_dotenv_without_values_is_not_offered_and_cannot_be_imported(env):
    folder = _project(env)
    (folder / ".env").write_text("# nothing here\n\n")
    assert env.client.get("/projects/blog/secrets").json()["dotenv"] is None
    resp = env.client.post("/projects/blog/secrets/import-dotenv")
    assert (resp.status_code, resp.json()["error"]["code"]) == (404, "dotenv_missing")


def test_a_dotenv_eggie_cannot_write_is_emptied_as_root(env):
    folder = _project(env)
    dotenv = folder / ".env"
    dotenv.write_text("API_KEY=v\n")
    dotenv.chmod(0o444)
    resp = env.client.post("/projects/blog/secrets/import-dotenv")
    assert resp.status_code == 200
    run = env.runner.argv_containing("run")[-1]
    assert ["--user", "0"] == run[run.index("--user"):run.index("--user") + 2]
    assert run[-1] == str(dotenv)


def test_a_dotenv_that_cannot_be_emptied_keeps_the_values_and_says_so(env):
    folder = _project(env)
    (folder / ".env").write_text("API_KEY=v\n")
    (folder / ".env").chmod(0o444)
    real = env.runner.exec

    def failing_run(argv, **kw):
        if argv[1:2] == ["run"]:
            env.runner.calls.append(argv)
            return Completed(1, "", "denied")
        return real(argv, **kw)

    env.runner.exec = failing_run
    resp = env.client.post("/projects/blog/secrets/import-dotenv")
    assert (resp.status_code, resp.json()["error"]["code"]) == (409, "dotenv_not_removed")
    assert env.state.secret_values("blog") == {"API_KEY": "v"}


def test_a_dotenv_symlink_is_never_followed(env):
    folder = _project(env)
    outside = folder.parent / "outside.env"
    outside.write_text("STOLEN=value\n")
    (folder / ".env").symlink_to(outside)
    listed = env.client.get("/projects/blog/secrets").json()
    assert listed["dotenv"]["names"] == []
    assert "link" in listed["dotenv"]["error"]
    resp = env.client.post("/projects/blog/secrets/import-dotenv")
    assert (resp.status_code, resp.json()["error"]["code"]) == (400, "dotenv_invalid")
    assert env.state.secret_values("blog") == {}
    assert outside.read_text() == "STOLEN=value\n"


def test_a_reserved_name_is_never_reported_missing(env):
    folder = _project(env)
    (folder / ".env.example").write_text("DOCKER_HOST=\nAPI_KEY=\n")
    assert env.client.get("/projects/blog/secrets").json()["missing"] == ["API_KEY"]


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


def test_import_without_a_dotenv_is_a_404(env):
    _project(env)
    resp = env.client.post("/projects/blog/secrets/import-dotenv")
    assert resp.json()["error"]["code"] == "dotenv_missing"


def test_purge_drops_secrets_and_plain_delete_keeps_them(env):
    _project(env, "kept")
    env.client.put("/projects/kept/secrets/A", json={"value": "1"})
    env.client.delete("/projects/kept")
    assert env.state.secret_values("kept") == {"A": "1"}

    _project(env, "gone")
    env.client.put("/projects/gone/secrets/A", json={"value": "1"})
    env.client.delete("/projects/gone?purge=true")
    assert env.state.secret_values("gone") == {}


def test_secrets_routes_are_served_to_both_the_cli_and_the_console(env):
    from tests.runtime.api.route_sweep import every_route
    paths = {getattr(r, "path", None) for r in every_route(env.app)}
    for path in ("/projects/{project_id}/secrets",
                 "/projects/{project_id}/secrets/{name}",
                 "/projects/{project_id}/secrets/import-dotenv"):
        assert path in paths and "/api" + path in paths, path


def test_a_malformed_dotenv_line_never_leaks_its_text_into_responses(env):
    folder = _project(env)
    (folder / ".env").write_text("abc+/SECRETPART=x\n")
    listed = env.client.get("/projects/blog/secrets")
    imported = env.client.post("/projects/blog/secrets/import-dotenv")
    assert "SECRETPART" not in listed.text
    assert "SECRETPART" not in imported.text
    assert imported.json()["error"]["code"] == "dotenv_invalid"
    assert (folder / ".env").exists()


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
    (folder / ".env.example").write_text("DATABASE_URL=postgres://localhost/app\n")
    env.client.put("/projects/blog/secrets/DATABASE_URL", json={"value": "x"})
    _run_to_completion(env, env.client.post("/projects/blog/up"))
    assert "environment" not in _overlay_names(env, folder).get("web", {})


def test_defaults_reach_compose_and_stored_values_override_them(env):
    folder = _project(env)
    (folder / ".env.example").write_text("APP_NAME=Blog\nMODE=dev\nAPI_KEY=\n")
    env.client.put("/projects/blog/secrets/MODE", json={"value": "prod"})
    _run_to_completion(env, env.client.post("/projects/blog/up"))
    ups = [e for a, e in zip(env.runner.calls, env.runner.envs) if a[-2:] == ["up", "-d"]]
    assert ups == [{"APP_NAME": "Blog", "MODE": "prod"}]
    assert _overlay_names(env, folder)["worker"]["environment"] == ["APP_NAME", "MODE"]


def test_the_listing_shows_defaults_in_clear_and_values_never(env):
    folder = _project(env)
    (folder / ".env.example").write_text("APP_NAME=Blog\nMODE=dev\nAPI_KEY=\n")
    env.client.put("/projects/blog/secrets/MODE", json={"value": SECRET})
    body = env.client.get("/projects/blog/secrets").json()
    assert body["defaults"] == [{"name": "APP_NAME", "value": "Blog", "overridden": False,
                                 "shadowed": False, "compose_default": False},
                                {"name": "MODE", "value": "dev", "overridden": True,
                                 "shadowed": False, "compose_default": True}]
    assert body["secrets"] == [{"name": "MODE", "updated_at": body["secrets"][0]["updated_at"],
                                "overrides_default": True}]
    assert body["missing"] == ["API_KEY"]
    assert SECRET not in env.client.get("/projects/blog/secrets").text


def test_an_empty_value_is_refused(env):
    _project(env)
    resp = env.client.put("/projects/blog/secrets/API_KEY", json={"value": ""})
    assert (resp.status_code, resp.json()["error"]["code"]) == (400, "secret_invalid_value")


def test_a_dotenv_copied_from_the_example_is_not_offered(env):
    folder = _project(env)
    (folder / ".env.example").write_text("APP_NAME=Blog\nAPI_KEY=\n")
    (folder / ".env").write_text("APP_NAME=Blog\nAPI_KEY=\n")
    assert env.client.get("/projects/blog/secrets").json()["dotenv"] is None
    resp = env.client.post("/projects/blog/secrets/import-dotenv")
    assert resp.json()["error"]["code"] == "dotenv_missing"
    assert env.state.secret_values("blog") == {}


def test_import_stores_only_values_that_differ_from_defaults(env):
    folder = _project(env)
    (folder / ".env.example").write_text("APP_NAME=Blog\nAPP_ENV=local\nAPI_KEY=\n")
    (folder / ".env").write_text("APP_NAME=Blog\nAPP_ENV=production\nAPI_KEY=k\nEMPTY=\n")
    assert env.client.get("/projects/blog/secrets").json()["dotenv"]["names"] == ["API_KEY", "APP_ENV"]
    assert env.client.post("/projects/blog/secrets/import-dotenv").json() == {
        "imported": ["API_KEY", "APP_ENV"]}
    assert env.state.secret_values("blog") == {"API_KEY": "k", "APP_ENV": "production"}
    assert (folder / ".env").read_text() == ""


def test_import_keeps_reserved_lines_in_dotenv(env):
    folder = _project(env)
    (folder / ".env").write_text("COMPOSE_PROJECT_NAME=shop\nAPI_KEY=k\n")
    assert env.client.post("/projects/blog/secrets/import-dotenv").json() == {"imported": ["API_KEY"]}
    from eggie_api.core.secrets import parse_dotenv
    assert parse_dotenv((folder / ".env").read_text()) == {"COMPOSE_PROJECT_NAME": "shop"}


def test_import_rewrites_a_reserved_value_with_quotes_and_backslashes_intact(env):
    folder = _project(env)
    (folder / ".env").write_text('COMPOSE_X="a\\"b\\\\c"\nAPI_KEY=k\n')
    from eggie_api.core.secrets import parse_dotenv
    assert parse_dotenv((folder / ".env").read_text()) == {"COMPOSE_X": 'a"b\\c', "API_KEY": "k"}
    assert env.client.post("/projects/blog/secrets/import-dotenv").json() == {"imported": ["API_KEY"]}
    assert parse_dotenv((folder / ".env").read_text()) == {"COMPOSE_X": 'a"b\\c'}
    assert env.state.secret_values("blog") == {"API_KEY": "k"}


def _up_envs(env):
    return [e for a, e in zip(env.runner.calls, env.runner.envs) if a[-2:] == ["up", "-d"]]


def test_a_dotenv_not_yet_imported_beats_the_defaults_it_sets(env):
    folder = _project(env)
    (folder / ".env.example").write_text("X=b\nY=c\n")
    (folder / ".env").write_text("X=a\n")
    _run_to_completion(env, env.client.post("/projects/blog/up"))
    assert _up_envs(env) == [{"Y": "c"}]
    assert _overlay_names(env, folder)["worker"]["environment"] == ["Y"]
    defaults = env.client.get("/projects/blog/secrets").json()["defaults"]
    assert [(d["name"], d["shadowed"]) for d in defaults] == [("X", True), ("Y", False)]


def test_a_stored_value_still_beats_the_dotenv(env):
    folder = _project(env)
    (folder / ".env.example").write_text("X=b\nY=c\n")
    (folder / ".env").write_text("X=a\n")
    env.client.put("/projects/blog/secrets/X", json={"value": "stored"})
    _run_to_completion(env, env.client.post("/projects/blog/up"))
    assert _up_envs(env) == [{"X": "stored", "Y": "c"}]


def test_an_unparseable_dotenv_still_shadows_and_never_stops_a_start(env):
    folder = _project(env)
    (folder / ".env.example").write_text("X=b\nY=c\n")
    (folder / ".env").write_text("X=\nnot a line\n")
    _run_to_completion(env, env.client.post("/projects/blog/up"))
    assert _up_envs(env) == [{"Y": "c"}]


def test_a_dotenv_example_symlink_is_treated_as_absent(env):
    folder = _project(env)
    outside = folder.parent / "outside.example"
    outside.write_text("LEAKED=value\nAPI_KEY=\n")
    (folder / ".env.example").symlink_to(outside)
    body = env.client.get("/projects/blog/secrets").json()
    assert body["defaults"] == []
    assert "LEAKED" not in env.client.get("/projects/blog/secrets").text
    _run_to_completion(env, env.client.post("/projects/blog/up"))
    assert _up_envs(env) == [None]


COMPOSE_DEFAULTS_X = """
services:
  web:
    image: nginx
    ports: ["8080:80"]
    environment:
      X: ${X:-c}
"""


def test_the_compose_files_own_default_beats_the_example_and_a_stored_value_beats_both(env):
    folder = _project(env, compose=COMPOSE_DEFAULTS_X)
    (folder / ".env.example").write_text("X=a\nY=b\n")
    _run_to_completion(env, env.client.post("/projects/blog/up"))
    assert _up_envs(env) == [{"Y": "b"}]
    assert _overlay_names(env, folder)["web"]["environment"] == ["Y"]
    defaults = env.client.get("/projects/blog/secrets").json()["defaults"]
    assert [(d["name"], d["compose_default"]) for d in defaults] == [("X", True), ("Y", False)]
    env.client.put("/projects/blog/secrets/X", json={"value": "stored"})
    _run_to_completion(env, env.client.post("/projects/blog/up"))
    assert _up_envs(env)[-1] == {"X": "stored", "Y": "b"}
