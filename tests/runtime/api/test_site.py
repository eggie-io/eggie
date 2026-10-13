"""What changes when the API runs on a site (a server with its own hostnames)."""
from eggie_api.config import ApiConfig
from tests.runtime.api.conftest import COMPOSE_ONE_WEB, _write_compose


def test_site_host_comes_from_the_environment_and_is_absent_locally():
    assert ApiConfig.from_env({}).site_host is None
    assert ApiConfig.from_env({"EGGIE_SITE_HOST": ""}).site_host is None
    assert ApiConfig.from_env({"EGGIE_SITE_HOST": "foo.eggie.io"}).site_host == "foo.eggie.io"


def test_public_url_has_no_port_and_https_on_a_site():
    local = ApiConfig(domain="d.io", edge_port=41080)
    assert local.public_url("blog.d.io") == "http://blog.d.io:41080"
    site = ApiConfig(domain="foo.eggie.space", site_host="foo.eggie.io")
    assert site.public_url("blog.foo.eggie.space") == "https://blog.foo.eggie.space"


def test_project_urls_on_a_site_are_https_under_the_site_domain(site_env):
    site_env.client.post("/projects", json={"id": "blog"})
    _write_compose(site_env, "blog", COMPOSE_ONE_WEB)
    project = site_env.client.get("/projects/blog").json()
    assert project["urls"] == ["https://blog.foo.eggie.space"]


def test_health_has_no_site_locally(env):
    assert env.client.get("/health").json()["site"] is None


def test_health_names_the_site(site_env):
    assert site_env.client.get("/health").json()["site"] == "foo.eggie.io"


from eggie_api.infra.db import Database
from eggie_api.infra.repos import Repos
from eggie_api.services.account import Account
from eggie_api.services.sessions import Sessions
from eggie_api.services.site import REMOTE_PER_MINUTE, SiteSignIn
from tests.runtime.api.fake_cloud import FakeCloud


class Clock:
    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self):
        return self.now


def test_remote_redemptions_resume_once_the_minute_has_moved_on(tmp_path):
    clock = Clock()
    repos = Repos.open(Database(tmp_path / "state.db"))
    repos.account.update(access_token="at", refresh_token="rt",
                         access_expires_at=10**12, org_id="org-1")
    cloud = FakeCloud(redeem_instance_handoff=[{"ok": False}] * REMOTE_PER_MINUTE + [{"ok": True}])
    account = Account(repos.account, repos.cloud_projects, cloud, spawn=lambda fn: None)
    site = SiteSignIn(Sessions(repos.sessions, clock=clock), account, cloud,
                      remote=True, clock=clock)
    for n in range(REMOTE_PER_MINUTE):
        clock.now += 1
        assert site.redeem(f"guess-{n}") is None
    assert site.redeem("held-back") is None
    assert len(cloud.calls) == REMOTE_PER_MINUTE

    # The oldest attempt leaves the window first; one slot opens, not ten.
    clock.now += 60 - REMOTE_PER_MINUTE + 1
    assert site.redeem("real") is not None
    assert len(cloud.calls) == REMOTE_PER_MINUTE + 1
