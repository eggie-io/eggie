import time

from fastapi.testclient import TestClient

from eggie_api.services.account import Account
from eggie_api.services.public import Public, TunnelClient
from eggie_api.infra.db import Database
from eggie_api.infra.repos import Repos
from eggie_api.http.app import create_app
from tests.runtime.api.conftest import AUTH, BROWSER, COMPOSE_ONE_WEB, FakeRunner
from tests.runtime.api.fake_cloud import FakeCloud
from tests.runtime.api.test_public import ON, Clock, TunnelRunner

ORIGIN = {"Origin": "http://localhost:41080"}


def console(app, client) -> TestClient:
    code = client.post("/sessions/handoff").json()["code"]
    browser = TestClient(app, headers={**BROWSER, **ORIGIN})
    assert browser.post("/api/session", json={"code": code}).status_code == 200
    return browser


def build(env, cloud, *, tunnel_runner=None):
    repos = Repos.open(Database(env.config.state_db.with_name("public.db")))
    repos.account.update(access_token="at", refresh_token="rt",
                         access_expires_at=10**12, org_id="org-1")
    account = Account(repos.account, repos.cloud_projects, cloud, spawn=lambda fn: None)
    tunnel_runner = tunnel_runner or TunnelRunner()
    # A fixed clock, not the real one: ON's expires_at is a hardcoded
    # timestamp, and the real wall clock outruns it before this test always
    # runs (flaky-by-time-of-day otherwise).
    public = Public(projects=repos.projects, cloud_projects=repos.cloud_projects, account=account, cloud=cloud,
                    client=TunnelClient(tunnel_runner, env.config.stack_file),
                    token_path=env.config.projects_root.parent / "tunnel.token",
                    origin="http://traefik:41080",
                    hosts_for=lambda pid: [{"service": "web", "hostname": f"{pid}.test.local",
                                            "local_url": f"http://{pid}.test.local:41080"}],
                    clock=Clock(), spawn=lambda fn: fn())
    app = create_app(config=env.config, runner=FakeRunner(), repos=repos,
                     account=account, cloud=cloud, public=public)
    client = TestClient(app, headers=AUTH)
    client.post("/projects", json={"id": "blog"})
    repos.cloud_projects.map("blog", "c-blog", "org-1")
    (env.config.projects_root / "blog" / "docker-compose.yml").write_text(COMPOSE_ONE_WEB)
    return client, console(app, client), repos, tunnel_runner


def test_turning_on_answers_202_and_the_project_shows_the_public_url(env):
    client, browser, _, _ = build(env, FakeCloud(create_public_url=[ON]))

    assert browser.post("/api/projects/blog/public").status_code == 202
    assert client.get("/projects/blog").json()["public"]["state"] == "on"


def test_turning_on_an_unregistered_project_is_a_409_with_the_reason(env):
    _, browser, repos, _ = build(env, FakeCloud())
    repos.cloud_projects.unmap("blog")

    resp = browser.post("/api/projects/blog/public")

    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "not_registered"


def test_deleting_a_project_releases_its_public_url(env):
    cloud = FakeCloud(create_public_url=[ON], release_public_url=[None])
    client, browser, repos, tunnel = build(env, cloud)
    browser.post("/api/projects/blog/public")

    client.delete("/projects/blog")

    assert "release_public_url" in cloud.names()
    assert not tunnel.up


def test_signing_out_releases_the_public_url(env):
    cloud = FakeCloud(create_public_url=[ON], release_public_url=[None], logout=[None])
    client, browser, repos, tunnel = build(env, cloud)
    browser.post("/api/projects/blog/public")

    client.post("/account/sign-out")

    assert cloud.names()[-2:] == ["release_public_url", "logout"]
    assert not tunnel.up


def test_the_default_public_wiring_reaches_the_service_with_real_hosts_and_origin(env):
    """No `public=` override here: this exercises the production
    `public_hosts` closure inside create_app -- the real `load()`/`host_for()`
    wiring that decides which routes and origin actually reach the
    service, which every other test in this file bypasses by passing its own
    `public=`."""
    # create_app builds the default Public with the real clock, not a fixed
    # one, so expires_at must be genuinely in the future rather than ON's
    # hardcoded (and by now past) timestamp.
    reply = {**ON, "urls": [{"service": "web", "local_hostname": "blog.test.local",
                             "url": "https://k3x9.example.dev"}],
             "expires_at": "2099-01-01T00:00:00Z"}
    cloud = FakeCloud(create_public_url=[reply])
    repos = Repos.open(Database(env.config.state_db.with_name("wiring.db")))
    repos.account.update(access_token="at", refresh_token="rt",
                         access_expires_at=10**12, org_id="org-1")
    account = Account(repos.account, repos.cloud_projects, cloud, spawn=lambda fn: None)
    # The default Public's own spawn is a daemon thread; FakeRunner answers
    # both the project lifecycle argv and TunnelClient's compose calls (it
    # returns ok for anything it doesn't specifically recognize).
    app = create_app(config=env.config, runner=FakeRunner(), repos=repos,
                     account=account, cloud=cloud)
    client = TestClient(app, headers=AUTH)
    client.post("/projects", json={"id": "blog"})
    repos.cloud_projects.map("blog", "c-blog", "org-1")
    (env.config.projects_root / "blog" / "docker-compose.yml").write_text(COMPOSE_ONE_WEB)

    assert console(app, client).post("/api/projects/blog/public").status_code == 202

    deadline = time.time() + 5
    status = client.get("/projects/blog/public").json()
    while status["state"] == "enabling" and time.time() < deadline:
        time.sleep(0.02)
        status = client.get("/projects/blog/public").json()

    assert status["state"] == "on"
    assert status["urls"] == [{"url": "https://k3x9.example.dev", "service": "web",
                               "local_url": "http://blog.test.local:41080"}]
    assert cloud.calls[0] == ("create_public_url", "at", "c-blog",
                              [{"local_hostname": "blog.test.local", "service": "web"}],
                              "http://traefik:41080")


def test_the_guest_token_cannot_turn_a_public_url_on_or_off(env):
    cloud = FakeCloud()
    client, _, repos, tunnel = build(env, cloud)

    assert client.post("/projects/blog/public").status_code in (404, 405)
    assert client.delete("/projects/blog/public").status_code in (404, 405)
    assert client.get("/projects/blog/public").status_code == 200
    assert cloud.calls == []


def test_a_failing_public_reconcile_does_not_stop_the_account_sync(env):
    class Broken:
        def reconcile(self):
            raise OSError("token path is a directory")

        def forget_local(self):
            pass

    cloud = FakeCloud()
    repos = Repos.open(Database(env.config.state_db.with_name("broken.db")))
    repos.account.update(access_token="at", refresh_token="rt",
                         access_expires_at=10**12, org_id="org-1")
    account = Account(repos.account, repos.cloud_projects, cloud, spawn=lambda fn: None)
    app = create_app(config=env.config, runner=FakeRunner(), repos=repos,
                     account=account, cloud=cloud, public=Broken())

    app.state.services.sync.run_once()

    assert repos.account.get()["sync_ok_at"] is not None
