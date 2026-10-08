import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from eggie_api.infra.runner import Completed
from eggie_api.infra.github import GitHubUnavailable
from eggie_api.services.github_link import GitHubLink
from eggie_api.infra.db import Database
from eggie_api.infra.repos import Repos
from eggie_api.rest.app import create_app
from tests.runtime.api.conftest import (AUTH, COMPOSE_MALFORMED, COMPOSE_ONE_WEB,
                                        FakeProbe, FakeRunner)
from tests.runtime.api.fake_github import CODE, TOKEN, USER, FakeGitHub, err

REPO = {"full_name": "octo/app", "private": True, "description": "An app",
        "updated_at": "2026-09-20T10:00:00Z", "clone_url": "ignored"}


def make(env, github, *, connect=True):
    repos = Repos.open(Database(env.config.state_db.with_name("gh.db")))
    link = GitHubLink(repos.github, github, client_id="cid",
                      directory=env.config.state_db.with_name("github"),
                      spawn=lambda fn: None)
    if connect:
        link.connect()
        link.poll_once()
    runner = FakeRunner()
    app = create_app(config=env.config, runner=runner, repos=repos,
                     http_probe=FakeProbe(), github=github, github_link=link)
    return TestClient(app, headers=AUTH), runner, app, link


def connected_github(**scripts):
    return FakeGitHub(device_code=[CODE], device_token=[{"access_token": TOKEN}],
                      user=[USER], **scripts)


def finish(app, client, resp):
    job_id = resp.json()["job_id"]
    app.state.services.jobs.wait(job_id, timeout=5)
    return client.get(f"/jobs/{job_id}").json()


def test_connect_says_when_github_cannot_be_reached(env):
    client, *_ = make(env, FakeGitHub(device_code=[GitHubUnavailable("x")]), connect=False)
    resp = client.post("/github/connect")
    assert (resp.status_code, resp.json()["error"]["code"]) == (503, "github_unavailable")


def test_reapply_reports_a_github_error_that_is_not_bad_credentials(env):
    # The first `user` reply is consumed by connect()+poll_once() inside
    # make(); the second is what reapply() itself sees.
    github = FakeGitHub(device_code=[CODE], device_token=[{"access_token": TOKEN}],
                        user=[USER, err("service_down")])
    client, *_ = make(env, github)
    resp = client.post("/github/reapply")
    assert (resp.status_code, resp.json()["error"]["code"]) == (502, "github_error")


def test_repos_are_mapped_and_never_carry_the_token(env):
    client, *_ = make(env, connected_github(repos=[([REPO], True)]))
    resp = client.get("/github/repos?page=1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["has_more"] is True
    assert body["repos"] == [{"full_name": "octo/app", "private": True,
                              "description": "An app",
                              "updated_at": 1789898400.0}]
    assert TOKEN not in resp.text
    assert TOKEN not in client.get("/github").text


def test_a_revoked_token_on_repos_asks_for_a_reconnect(env):
    client, *_ = make(env, connected_github(repos=[err("bad_credentials")]))
    resp = client.get("/github/repos")
    assert (resp.status_code, resp.json()["error"]["code"]) == (409, "github_reconnect")
    assert client.get("/github").json() == {"state": "needs_reconnect", "login": "octo"}


def test_a_401_on_repos_racing_a_disconnect_does_not_mark_anything(env):
    holder = {}
    github = connected_github(repos=[
        lambda: (holder["link"].disconnect(), err("bad_credentials"))[1]])
    client, _, _, link = make(env, github)
    holder["link"] = link
    client.get("/github/repos")
    assert client.get("/github").json() == {"state": "disconnected", "error": None}


@pytest.mark.parametrize("repo", ["../x", "a/b/c", "https://github.com/a/b"])
def test_clone_refuses_anything_but_owner_slash_name(env, repo):
    client, *_ = make(env, connected_github())
    resp = client.post("/github/clone", json={"repo": repo})
    assert (resp.status_code, resp.json()["error"]["code"]) == (422, "invalid_repo")


def test_clone_while_disconnected_is_refused(env):
    client, *_ = make(env, FakeGitHub(), connect=False)
    resp = client.post("/github/clone", json={"repo": "octo/app"})
    assert (resp.status_code, resp.json()["error"]["code"]) == (409, "github_not_connected")


def _staging_dirs(env):
    return [p for p in env.config.projects_root.iterdir()
           if p.is_dir() and p.name.startswith(".clone-")]


def test_clone_passes_the_token_only_through_the_environment_then_starts_the_project(env):
    client, runner, app, _ = make(env, connected_github())

    def put_compose(dest):
        # `dest` is the staging directory git actually clones into; the route
        # renames it to the real project folder only once the clone succeeds.
        Path(dest).mkdir(parents=True)
        (Path(dest) / "docker-compose.yml").write_text(COMPOSE_ONE_WEB)

    runner.on_clone = put_compose
    resp = client.post("/github/clone", json={"repo": "octo/app"})
    assert resp.status_code == 202 and resp.json()["id"] == "app"
    job = finish(app, client, resp)

    assert job["state"] == "done", job
    clone = next(argv for argv in runner.calls if argv[0] == "sh")
    assert TOKEN not in " ".join(clone)
    dest = clone[-1]
    assert dest != str(env.config.projects_root / "app")
    assert "https://github.com/octo/app.git" in clone
    env_used = runner.envs[runner.calls.index(clone)]
    assert env_used == {"EGGIE_GH_TOKEN": TOKEN, "GIT_TERMINAL_PROMPT": "0"}
    assert client.get("/projects/app").status_code == 200
    assert runner.argv_containing("up")
    assert _staging_dirs(env) == []


def test_a_failed_clone_leaves_no_project_and_no_token_in_its_output(env):
    # The second `user` reply is what the clone job's confirm_bad() sees
    # after the 403; the first is consumed by connect()+poll_once() in make().
    github = FakeGitHub(device_code=[CODE], device_token=[{"access_token": TOKEN}],
                        user=[USER, err("bad_credentials")])
    client, runner, app, _ = make(env, github)

    def half_clone(dest):
        Path(dest).mkdir(parents=True)
        (Path(dest) / ".git").mkdir()

    runner.on_clone = half_clone
    runner.clone = Completed(128, "", f"fatal: Authentication failed for "
                                      f"'https://x-access-token:{TOKEN}@github.com/octo/app.git/'")
    job = finish(app, client, client.post("/github/clone", json={"repo": "octo/app"}))

    assert job["state"] == "failed"
    assert TOKEN not in json.dumps(job)
    assert "Authentication failed" in job["detail"]
    assert not (env.config.projects_root / "app").exists()
    assert client.get("/projects/app").status_code == 404
    assert client.get("/github").json()["state"] == "needs_reconnect"
    assert _staging_dirs(env) == []


def test_a_403_clone_with_a_still_valid_token_stays_connected(env):
    # A 403 also covers SSO/permission refusals on a token GitHub still
    # accepts, so confirm_bad() must not mark it dead when `user` says ok.
    github = FakeGitHub(device_code=[CODE], device_token=[{"access_token": TOKEN}],
                        user=[USER, USER])
    client, runner, app, _ = make(env, github)

    def half_clone(dest):
        Path(dest).mkdir(parents=True)
        (Path(dest) / ".git").mkdir()

    runner.on_clone = half_clone
    runner.clone = Completed(128, "", "error: 403")
    job = finish(app, client, client.post("/github/clone", json={"repo": "octo/app"}))

    assert job["state"] == "failed"
    assert not (env.config.projects_root / "app").exists()
    assert client.get("/github").json()["state"] == "connected"


def test_clone_into_a_taken_name_is_refused(env):
    client, *_ = make(env, connected_github())
    (env.config.projects_root / "app").mkdir(parents=True)
    resp = client.post("/github/clone", json={"repo": "octo/app"})
    assert (resp.status_code, resp.json()["error"]["code"]) == (409, "project_exists")


def test_a_failed_clone_never_removes_a_destination_created_after_the_202(env):
    client, runner, app, _ = make(env, connected_github())

    def race_a_folder_into_place(_dest):
        # Ignores the staging path it's handed -- simulating a folder that
        # landed at the *real* project path while the clone was in flight.
        target = env.config.projects_root / "app"
        target.mkdir(parents=True)
        (target / "keep.txt").write_text("keep me")

    runner.on_clone = race_a_folder_into_place
    runner.clone = Completed(128, "", "fatal: could not read from remote")
    job = finish(app, client, client.post("/github/clone", json={"repo": "octo/app"}))

    assert job["state"] == "failed"
    assert (env.config.projects_root / "app" / "keep.txt").read_text() == "keep me"
    assert _staging_dirs(env) == []


def test_a_clone_whose_destination_appears_mid_job_fails_without_touching_it(env):
    client, runner, app, _ = make(env, connected_github())

    def finish_clone_but_lose_the_race(dest):
        Path(dest).mkdir(parents=True)
        (Path(dest) / "docker-compose.yml").write_text(COMPOSE_ONE_WEB)
        target = env.config.projects_root / "app"
        target.mkdir(parents=True)
        (target / "existing.txt").write_text("do not touch")

    runner.on_clone = finish_clone_but_lose_the_race
    job = finish(app, client, client.post("/github/clone", json={"repo": "octo/app"}))

    assert job["state"] == "failed"
    assert "appeared" in job["detail"]
    assert (env.config.projects_root / "app" / "existing.txt").read_text() == "do not touch"
    assert client.get("/projects/app").status_code == 404
    assert _staging_dirs(env) == []


def test_a_second_clone_after_a_failed_one_is_not_blocked_by_the_lock(env):
    client, runner, app, _ = make(env, connected_github())
    runner.clone = Completed(128, "", "fatal: could not read from remote")
    first = finish(app, client, client.post("/github/clone", json={"repo": "octo/app"}))
    assert first["state"] == "failed"

    def put_compose(dest):
        Path(dest).mkdir(parents=True)
        (Path(dest) / "docker-compose.yml").write_text(COMPOSE_ONE_WEB)

    runner.clone = Completed(0, "", "")
    runner.on_clone = put_compose
    second = client.post("/github/clone", json={"repo": "octo/app"})
    assert second.status_code == 202


def test_clone_without_a_compose_file_finishes_stopped_without_starting_it(env):
    client, runner, app, _ = make(env, connected_github())

    def put_readme_only(dest):
        Path(dest).mkdir(parents=True)
        (Path(dest) / "README.md").write_text("hi")

    runner.on_clone = put_readme_only
    resp = client.post("/github/clone", json={"repo": "octo/app"})
    job = finish(app, client, resp)

    assert job["state"] == "done", job
    assert job["result"] == {"id": "app", "status": "stopped"}
    assert client.get("/projects/app").status_code == 200
    assert not runner.argv_containing("up")


def test_clone_lock_is_released_when_start_work_refuses(env):
    # A clone that lands a repo whose compose file will not parse must not
    # leave the project locked. `down` is the probe: the lock is the first
    # thing it needs, while `up` refuses on the compose file before reaching it.
    client, runner, app, _ = make(env, connected_github())

    def land(dest):
        Path(dest).mkdir(parents=True)
        (Path(dest) / "docker-compose.yml").write_text(COMPOSE_MALFORMED)

    runner.on_clone = land
    job = finish(app, client, client.post("/github/clone", json={"repo": "octo/app"}))
    assert job["state"] == "done", job
    down = client.post("/projects/app/down")
    assert down.status_code == 202, down.text
