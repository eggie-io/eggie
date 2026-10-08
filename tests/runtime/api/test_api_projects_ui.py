import threading
import time

from eggie_api.infra.runner import Completed
from tests.runtime.api.conftest import (COMPOSE_MALFORMED, _create,
                                  _run_to_completion, _write_compose)

COMPOSE_TWO_WEBS = """
services:
  web:
    image: nginx
    ports: ["8080:80"]
  api:
    image: api
    ports: ["9000:9000"]
"""


def test_the_first_web_is_the_primary_address(env):
    _create(env, "shop")
    _write_compose(env, "shop", COMPOSE_TWO_WEBS)
    web = env.client.get("/projects/shop").json()["web"]
    assert [(w["service"], w["primary"]) for w in web] == [("web", True),
                                                           ("api", False)]


def test_a_running_up_is_visible_on_the_project(env):
    # A reopened page must find the start that is still going.
    _create(env, "blog")
    _write_compose(env, "blog")
    env.runner.up_gate = threading.Event()
    job_id = env.client.post("/projects/blog/up").json()["job_id"]
    try:
        job = env.client.get("/projects/blog").json()["job"]
        assert (job["id"], job["kind"]) == (job_id, "up")
    finally:
        env.runner.up_gate.set()
        env.jobs.wait(job_id, timeout=5)
    assert env.client.get("/projects/blog").json()["job"] is None


def test_up_reports_its_phases_in_order(env):
    _create(env, "blog")
    _write_compose(env, "blog")
    seen = []
    env.runner.up_gate = threading.Event()
    job_id = env.client.post("/projects/blog/up").json()["job_id"]
    # The job's own thread does a bit of work -- recording compose_name --
    # before it reaches "starting" and blocks in `up`, so poll for it rather
    # than assuming it has already landed by the time this request returns.
    deadline = time.monotonic() + 5
    phase = env.client.get(f"/jobs/{job_id}").json()["phase"]
    while phase == "preparing" and time.monotonic() < deadline:
        phase = env.client.get(f"/jobs/{job_id}").json()["phase"]
    seen.append(phase)
    env.runner.up_gate.set()
    final = env.jobs.wait(job_id, timeout=5)
    seen.append(final.phase)
    assert seen == ["starting", "checking"]


def test_down_reports_a_stopping_phase(env):
    _create(env, "blog")
    _write_compose(env, "blog")
    job_id = env.client.post("/projects/blog/down").json()["job_id"]
    final = env.jobs.wait(job_id, timeout=5)
    assert final.phase == "stopping"


def test_first_run_holds_until_one_start_succeeds(env):
    _create(env, "blog")
    _write_compose(env, "blog")
    assert env.client.get("/projects/blog").json()["first_run"] is True
    _run_to_completion(env, env.client.post("/projects/blog/up"))
    assert env.client.get("/projects/blog").json()["first_run"] is False


def test_a_start_records_the_compose_project_name(env):
    # Delete finds containers by this name; a compose file with its own
    # `name:` would otherwise be deleted by the wrong label.
    _create(env, "blog")
    _write_compose(env, "blog", "name: fancy\n" + (
        "services:\n  web:\n    image: nginx\n    ports: ['8080:80']\n"))
    _run_to_completion(env, env.client.post("/projects/blog/up"))
    assert env.state.get_project("blog")["compose_name"] == "fancy"


def test_restart_stops_before_it_starts(env):
    _create(env, "blog")
    _write_compose(env, "blog")
    _run_to_completion(env, env.client.post("/projects/blog/restart"))
    compose = [a for a in env.runner.calls
               if "compose" in a and ("down" in a or a[-2:] == ["up", "-d"])]
    assert "down" in compose[0]
    assert compose[-1][-2:] == ["up", "-d"]


def test_a_failed_teardown_fails_restart_without_starting(env):
    # A down that fails must not be masked by an up that then succeeds --
    # the caller needs to know the teardown itself was the problem.
    _create(env, "blog")
    _write_compose(env, "blog")
    env.runner.down = Completed(1, "", "container busy")
    result = _run_to_completion(env, env.client.post("/projects/blog/restart"))
    assert result["state"] == "failed"
    assert "container busy" in result["detail"]
    assert not any(a[-2:] == ["up", "-d"] for a in env.runner.calls)


def test_delete_never_reads_the_compose_file(env):
    # A broken docker-compose.yml used to make delete fail and leave
    # containers behind that nothing would list again.
    _create(env, "blog")
    _write_compose(env, "blog", COMPOSE_MALFORMED)
    resp = env.client.delete("/projects/blog")
    assert resp.status_code == 200
    removal = [a for a in env.runner.calls if "compose" not in a]
    assert removal, "delete issued no docker commands"
    assert not [a for a in env.runner.calls if "compose" in a]
    assert any("label=com.docker.compose.project=blog" in a for a in removal)


def test_delete_without_purge_keeps_files_and_volumes(env):
    # The host CLI's `destroy` and install verification rely on this.
    _create(env, "blog")
    _write_compose(env, "blog")
    env.client.delete("/projects/blog")
    assert (env.config.projects_root / "blog").is_dir()
    assert not env.runner.argv_containing("volume")


def test_purge_removes_folder_volumes_and_record(env):
    _create(env, "blog")
    _write_compose(env, "blog")
    env.client.delete("/projects/blog", params={"purge": "true"})
    assert not (env.config.projects_root / "blog").exists()
    assert env.runner.argv_containing("volume")
    assert env.state.get_project("blog") is None


def test_delete_uses_the_recorded_compose_name(env):
    _create(env, "blog")
    _write_compose(env, "blog")
    env.state.set_compose_name("blog", "fancy")
    env.client.delete("/projects/blog")
    assert any("label=com.docker.compose.project=fancy" in a
               for a in env.runner.calls)


def test_delete_prefers_the_name_running_containers_carry_over_the_stored_one(env):
    # The stored compose_name can be stale (an edited `name:`, or a project
    # that was never started under it); the containers' own label wins.
    _create(env, "blog")
    _write_compose(env, "blog")
    env.state.set_compose_name("blog", "stale")
    env.runner.compose_name_lookup = Completed(0, "fancy\n", "")
    env.client.delete("/projects/blog")
    assert any("label=com.docker.compose.project=fancy" in a
               for a in env.runner.calls)
    assert not any("label=com.docker.compose.project=stale" in a
                   for a in env.runner.calls)


def test_a_failed_up_still_records_the_compose_name(env):
    # Delete has to find these containers even when the start itself failed.
    _create(env, "blog")
    _write_compose(env, "blog", "name: fancy\n" + (
        "services:\n  web:\n    image: nginx\n    ports: ['8080:80']\n"))
    env.runner.up = Completed(1, "", "boom")
    _run_to_completion(env, env.client.post("/projects/blog/up"))
    row = env.state.get_project("blog")
    assert row["compose_name"] == "fancy"
    assert row["last_started_at"] is None


def test_delete_preview_counts_the_real_tree(env):
    _create(env, "blog")
    _write_compose(env, "blog")
    (env.config.projects_root / "blog" / "data").mkdir()
    (env.config.projects_root / "blog" / "data" / "a.bin").write_bytes(b"x" * 10)
    preview = env.client.get("/projects/blog/delete-preview").json()
    compose_size = len((env.config.projects_root / "blog" /
                        "docker-compose.yml").read_bytes())
    assert (preview["files"], preview["bytes"]) == (2, 10 + compose_size)
