import threading

from fastapi import FastAPI

from eggie_api.infra.runner import Completed
from eggie_api.infra.state import State
from tests.runtime.api.conftest import (COMPOSE_AMBIGUOUS, COMPOSE_MALFORMED,
                                  PS_RESTARTING, _create, _run_to_completion,
                                  _write_compose)


def test_importing_the_app_module_builds_nothing(env):
    # A module-level app would open sqlite under /opt/eggie at import time and
    # drag the whole suite onto the real filesystem.
    import eggie_api.routes.app as module
    assert not [name for name, value in vars(module).items()
                if isinstance(value, (FastAPI, State))]


def test_missing_project_returns_a_structured_404(env):
    resp = env.client.get("/projects/nope")
    assert resp.status_code == 404
    assert resp.json() == {"error": {"code": "project_not_found",
                                     "message": "no project with id 'nope'"}}


def test_unknown_job_returns_a_structured_404(env):
    resp = env.client.get("/jobs/deadbeef")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "job_not_found"


def test_framework_errors_use_the_same_error_body(env):
    # The host client parses one shape; a route or the framework inventing a
    # second one breaks it.
    unknown = env.client.get("/no-such-route")
    assert unknown.status_code == 404
    assert set(unknown.json()["error"]) == {"code", "message"}

    invalid = env.client.post("/projects", json={})
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "invalid_request"


def test_an_unexpected_failure_still_answers_the_one_error_shape(env):
    env.runner.docker_version = RuntimeError("the runner exploded")
    resp = env.raw_client.get("/health")
    assert resp.status_code == 500
    assert resp.json()["error"]["code"] == "internal_error"
    assert "exploded" not in resp.text, "an internal message must not leak out"


def test_a_malformed_compose_file_is_a_4xx_not_a_500(env):
    # Uploading a broken compose file is the most ordinary thing a user does.
    _create(env)
    _write_compose(env, "blog", COMPOSE_MALFORMED)

    up = env.raw_client.post("/projects/blog/up")
    assert up.status_code == 422
    assert up.json()["error"]["code"] == "invalid_compose"
    assert "line 4" in up.json()["error"]["message"], "the parser must locate it"

    one = env.raw_client.get("/projects/blog")
    assert one.status_code == 200
    assert one.json()["problem"]["code"] == "invalid_compose"


def test_one_broken_project_does_not_take_the_listing_down(env):
    _create(env, "blog")
    _write_compose(env, "blog")
    _create(env, "broken")
    _write_compose(env, "broken", COMPOSE_MALFORMED)

    listed = env.raw_client.get("/projects")
    assert listed.status_code == 200
    by_id = {p["id"]: p for p in listed.json()["projects"]}
    assert by_id["blog"]["urls"] == ["http://blog.test.local:41080"]
    assert by_id["blog"]["problem"] is None
    assert by_id["broken"]["problem"]["code"] == "invalid_compose"


def test_a_second_lifecycle_operation_on_a_busy_project_is_refused(env):
    _create(env)
    _write_compose(env, "blog")
    env.runner.up_gate = threading.Event()

    first = env.client.post("/projects/blog/up")
    assert first.status_code == 202

    for resp in (env.client.post("/projects/blog/up"),
                 env.client.post("/projects/blog/down"),
                 env.client.delete("/projects/blog")):
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "project_busy"

    env.runner.up_gate.set()
    env.jobs.wait(first.json()["job_id"], timeout=5)
    # The lock is released with the job, not leaked.
    assert env.client.post("/projects/blog/down").status_code == 202


def test_a_file_write_during_a_running_job_is_refused_but_a_read_is_not(env):
    # An archive landing between the overlay being written and compose reading
    # docker-compose.yml starts a project from two different versions of
    # itself. Reads carry no such risk and must stay available while a job runs.
    from tests.runtime.api.test_files import _tar_bytes

    _create(env)
    _write_compose(env, "blog")
    env.runner.up_gate = threading.Event()
    first = env.client.post("/projects/blog/up")
    assert first.status_code == 202

    for resp in (env.client.post("/projects/blog/files", content=_tar_bytes()),
                 env.client.put("/projects/blog/files/a.txt", content=b"hi"),
                 env.client.delete("/projects/blog/files/docker-compose.yml")):
        assert resp.status_code == 409, resp.text
        assert resp.json()["error"]["code"] == "project_busy"

    assert env.client.get("/projects/blog/files").status_code == 200

    env.runner.up_gate.set()
    env.jobs.wait(first.json()["job_id"], timeout=5)
    assert env.client.post("/projects/blog/files",
                           content=_tar_bytes()).status_code == 200


def test_creating_the_same_project_twice_conflicts(env):
    assert _create(env).status_code == 201
    resp = _create(env)
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "project_exists"


def test_up_returns_a_job_id_and_runs_compose_with_both_files(env):
    _create(env)
    _write_compose(env, "blog")
    job = _run_to_completion(env, env.client.post("/projects/blog/up"))

    assert job["state"] == "done"
    assert job["result"]["status"] == "started_ok"
    assert env.client.get("/projects/blog").json()["status"] == "started_ok"

    up = env.runner.argv_containing("up")[-1]
    assert up.count("-f") == 2 and up[-2:] == ["up", "-d"]
    ps = env.runner.argv_containing("ps")[-1]
    assert ps.count("-f") == 1, "compose ps runs against the base file only"


def test_compose_runs_against_the_configured_projects_root(env):
    # The trap this closes: the API wrote uploads to config.projects_root while
    # lifecycle built every compose `-f` path from a constant of its own, so a
    # non-default root uploaded to one directory and ran compose against
    # another, with nothing anywhere saying so.
    _create(env)
    _write_compose(env, "blog")
    _run_to_completion(env, env.client.post("/projects/blog/up"))
    env.client.get("/projects/blog/logs")
    env.client.delete("/projects/blog")

    paths = [word for argv in env.runner.calls for word in argv if ".yml" in word]
    assert paths, "no compose file reached the runner at all"
    for word in paths:
        assert str(env.config.projects_root) in word, word
        assert "/opt/eggie/projects" not in word, word


def test_up_without_a_compose_file_is_compose_missing(env):
    _create(env)
    resp = env.client.post("/projects/blog/up")
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "compose_missing"


def test_up_on_an_ambiguous_compose_carries_the_detector_message(env):
    _create(env)
    _write_compose(env, "blog", COMPOSE_AMBIGUOUS)
    resp = env.client.post("/projects/blog/up")
    assert resp.status_code == 422
    body = resp.json()["error"]
    assert body["code"] == "invalid_project"
    assert "declare `web:`" in body["message"]


def test_a_failed_compose_up_fails_the_job_with_the_guests_own_stderr(env):
    _create(env)
    _write_compose(env, "blog")
    env.runner.up = Completed(1, "", "network edge declared as external, but could not be found")

    job = _run_to_completion(env, env.client.post("/projects/blog/up"))
    assert job["state"] == "failed"
    assert "network edge" in job["detail"]
    assert env.client.get("/projects/blog").json()["status"] == "failed_to_start"


def test_ndjson_ps_output_still_classifies_a_crash_loop(env):
    _create(env)
    _write_compose(env, "blog")
    env.runner.ps = Completed(0, PS_RESTARTING, "")

    job = _run_to_completion(env, env.client.post("/projects/blog/up"))
    assert job["result"]["status"] == "crash_looping"
    assert job["state"] == "failed"
    assert job["detail"], "a crash loop must say something actionable"
    assert env.client.get("/projects/blog").json()["status"] == "crash_looping"


def test_urls_come_from_the_configured_domain_and_edge_port(env):
    _create(env)
    _write_compose(env, "blog")
    listed = env.client.get("/projects").json()["projects"]
    assert listed[0]["urls"] == ["http://blog.test.local:41080"]


def test_explicit_web_override_beats_detection(env):
    _create(env, web=[{"service": "api", "port": 8000}])
    _write_compose(env, "blog", COMPOSE_AMBIGUOUS)
    proj = env.client.get("/projects/blog").json()
    assert proj["urls"] == ["http://blog.test.local:41080"]

    job = _run_to_completion(env, env.client.post("/projects/blog/up"))
    assert job["state"] == "done"


def test_listing_tolerates_a_project_whose_compose_cannot_be_read(env):
    # Status must stay readable even when the project's files are broken.
    _create(env)
    _write_compose(env, "blog", COMPOSE_AMBIGUOUS)
    proj = env.client.get("/projects/blog").json()
    assert proj["urls"] == []
    assert proj["status"] == "stopped"


def test_down_stops_the_stack_and_records_stopped(env):
    _create(env)
    _write_compose(env, "blog")
    _run_to_completion(env, env.client.post("/projects/blog/up"))

    job = _run_to_completion(env, env.client.post("/projects/blog/down"))
    assert job["state"] == "done"
    assert env.client.get("/projects/blog").json()["status"] == "stopped"


def test_delete_stops_the_containers_and_forgets_the_project(env):
    _create(env)
    _write_compose(env, "blog")
    assert env.client.delete("/projects/blog").status_code == 200
    assert any("label=com.docker.compose.project=blog" in a
               for a in env.runner.calls), "delete must remove by compose label"
    assert env.client.get("/projects/blog").status_code == 404


def test_project_logs_return_compose_output_and_follow_streams(env):
    _create(env)
    _write_compose(env, "blog")

    plain = env.client.get("/projects/blog/logs")
    assert plain.text == "web-1 | listening on 80\n"

    # Compose exits non-zero when the project was never created; an empty 200
    # would hide the reason.
    env.runner.logs = Completed(1, "", "no configuration file provided")
    failed = env.client.get("/projects/blog/logs")
    assert failed.status_code == 409
    assert failed.json()["error"]["code"] == "logs_unavailable"
    assert "no configuration file" in failed.json()["error"]["message"]

    followed = env.client.get("/projects/blog/logs", params={"follow": True})
    assert followed.text == "web-1 | one\nweb-1 | two\n"
    assert any("--follow" in a for a in env.runner.argv_containing("logs"))


def test_job_logs_are_readable_over_http(env):
    _create(env)
    _write_compose(env, "blog")
    resp = env.client.post("/projects/blog/up")
    job_id = resp.json()["job_id"]
    env.jobs.wait(job_id, timeout=5)
    assert "compose up" in env.client.get(f"/jobs/{job_id}/logs").text


def test_health_answers_even_when_docker_is_unreachable(env):
    env.runner.docker_version = Completed(1, "", "Cannot connect to the Docker daemon")
    body = env.client.get("/health").json()
    assert body["status"] == "ok"
    assert body["version"] == "9.9.9"
    assert body["docker"]["reachable"] is False
    assert "Cannot connect" in body["docker"]["detail"]


def test_version_reports_the_configured_version(env):
    assert env.client.get("/version").json() == {"version": "9.9.9"}


def test_a_started_project_traefik_cannot_reach_reports_a_problem(env):
    # The containers stayed up, so the job succeeds and the URL is printed --
    # without this the user gets a working-looking URL that answers a proxy
    # error, and nothing anywhere says why.
    _create(env)
    _write_compose(env, "blog")
    env.probe.status = 502

    job = _run_to_completion(env, env.client.post("/projects/blog/up"))
    assert job["state"] == "done"

    body = env.client.get("/projects/blog").json()
    assert body["status"] == "started_ok"
    assert body["problem"]["code"] == "bound_to_loopback"
    assert "0.0.0.0" in body["problem"]["message"]
    assert env.probe.calls[0] == ("http://traefik:41080/", "blog.test.local")


def test_a_fixed_project_clears_its_problem_on_the_next_up(env):
    _create(env)
    _write_compose(env, "blog")
    env.probe.status = 502
    _run_to_completion(env, env.client.post("/projects/blog/up"))
    assert env.client.get("/projects/blog").json()["problem"] is not None

    env.probe.status = 200
    _run_to_completion(env, env.client.post("/projects/blog/up"))
    assert env.client.get("/projects/blog").json()["problem"] is None


def test_a_broken_compose_file_outranks_a_routing_problem(env):
    # Both can be true at once; the one the user has to fix first wins.
    _create(env)
    _write_compose(env, "blog")
    env.probe.status = 502
    _run_to_completion(env, env.client.post("/projects/blog/up"))
    _write_compose(env, "blog", COMPOSE_MALFORMED)

    assert env.raw_client.get("/projects/blog").json()["problem"]["code"] == \
        "invalid_compose"


def test_a_stored_problem_clears_when_the_project_answers_on_a_re_read(env):
    # An entrypoint slower than the readiness window stores a diagnosis that is
    # true for a minute and false for as long as the project lives afterwards.
    _create(env)
    _write_compose(env, "blog")
    env.probe.status = 502
    _run_to_completion(env, env.client.post("/projects/blog/up"))
    assert env.state.get_project("blog")["problem_code"] == "bound_to_loopback"

    env.probe.status = 200
    assert env.client.get("/projects/blog").json()["problem"] is None
    assert env.state.get_project("blog")["problem_code"] is None, \
        "the stale row must be cleared, not just hidden from one response"


def test_a_stored_problem_survives_a_re_read_that_still_fails(env):
    _create(env)
    _write_compose(env, "blog")
    env.probe.status = 502
    _run_to_completion(env, env.client.post("/projects/blog/up"))

    body = env.client.get("/projects/blog").json()
    assert body["problem"]["code"] == "bound_to_loopback"


def test_a_healthy_listing_probes_nothing(env):
    # One round trip per project would make `eggie status` slow in proportion
    # to how much the tool is used. A project with no stored problem has
    # nothing to re-check, so it costs nothing.
    _create(env)
    _write_compose(env, "blog")
    _run_to_completion(env, env.client.post("/projects/blog/up"))
    env.probe.calls.clear()

    listed = env.client.get("/projects").json()["projects"]
    assert listed[0]["problem"] is None
    assert env.probe.calls == []


def test_a_stale_problem_clears_in_the_listing_too(env):
    # `eggie status` calls the listing, never GET /projects/{id}: re-probing
    # only there left the one surface users read stale forever.
    _create(env)
    _write_compose(env, "blog")
    env.probe.status = 502
    _run_to_completion(env, env.client.post("/projects/blog/up"))

    listed = env.client.get("/projects").json()["projects"]
    assert listed[0]["problem"]["code"] == "bound_to_loopback"

    env.probe.status = 200
    assert env.client.get("/projects").json()["projects"][0]["problem"] is None
    assert env.state.get_project("blog")["problem_code"] is None, \
        "the stale row must be cleared, not just hidden from one response"
