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
