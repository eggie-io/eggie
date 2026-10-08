import threading

from tests.runtime.api.conftest import _create, _run_to_completion, _write_compose

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
    (folder / ".env.example").write_text("API_KEY=\nSMTP_PASSWORD=changeme\n")
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


def test_import_moves_dotenv_values_into_secrets_and_removes_the_file(env):
    folder = _project(env)
    (folder / ".env").write_text('API_KEY=from-file\nexport PEM="a\\nb"\n')
    listed = env.client.get("/projects/blog/secrets").json()
    assert listed["dotenv"] == {"names": ["API_KEY", "PEM"], "error": None}
    env.client.put("/projects/blog/secrets/API_KEY", json={"value": "old"})
    resp = env.client.post("/projects/blog/secrets/import-dotenv")
    assert resp.json() == {"imported": ["API_KEY", "PEM"]}
    assert not (folder / ".env").exists()
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
