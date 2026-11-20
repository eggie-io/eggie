"""A project's addresses over REST: what the console, the guest token and an
anonymous caller on the internet can each reach, and where the address shows up."""
import json
import re

import pytest
from fastapi.testclient import TestClient

from eggie_api.services.instance_projects import InstanceProjects
from tests.runtime.api.conftest import COMPOSE_ONE_WEB, SITE_BROWSER, SITE_ORIGIN, _make_env
from tests.runtime.api.fake_cloud import FakeCloud
from tests.runtime.api.route_sweep import every_route

CHALLENGE = "/.well-known/acme-challenge/"
TOKEN = "a" * 43


def wire(hostname="example.com", *, primary=True, status="waiting_dns", certificate_id=None):
    return {"hostname": hostname, "display": hostname, "project": "shop",
            "primary": primary, "status": status, "problem": None, "dns": [],
            "certificate_id": certificate_id, "checked_at": None, "next_check_at": None}


@pytest.fixture
def site_env(tmp_path):
    # Signed in, with one running project that has a web service.
    cloud = FakeCloud()
    for ns in _make_env(tmp_path, cloud=cloud, site_host="foo.eggie.io",
                        domain="foo.eggie.space"):
        ns.repos.account.update(access_token="at", refresh_token="rt",
                                access_expires_at=10**12, org_id="org-1")
        folder = ns.config.projects_root / "shop"
        folder.mkdir(parents=True)
        (folder / "docker-compose.yml").write_text(COMPOSE_ONE_WEB)
        ns.repos.projects.add("shop", str(folder), "foo.eggie.space", status="started_ok")
        ns.cloud = cloud
        ns.services = ns.app.state.services
        ns.bare = TestClient(ns.app)
        yield ns


def console(k):
    code = k.client.post("/sessions/handoff").json()["code"]
    headers = {**SITE_BROWSER, **SITE_ORIGIN}
    resp = TestClient(k.app, headers=headers).post("/api/session", json={"code": code})
    # The cookie is Secure and the test client speaks http: send it by hand.
    cookie = resp.headers["set-cookie"].split(";", 1)[0]
    return TestClient(k.app, headers={**headers, "Cookie": cookie})


def mirror(k, *domains):
    k.cloud.replies["instance_domains"] = [{"domains": list(domains)}]
    k.services.domains.sync()


def install_certificate(k, cid):
    folder = k.config.edge_dir / "certs" / cid
    folder.mkdir(parents=True)
    (folder / "cert.pem").write_text("CERT")
    (folder / "key.pem").write_text("KEY")
    (folder / "meta.json").write_text(json.dumps({"id": cid, "kind": "domain"}))


def serve(k):
    install_certificate(k, "c1")
    mirror(k, wire(status="active", certificate_id="c1"))


def test_on_a_local_vm_the_routes_do_not_exist(env):
    env.client.post("/projects", json={"id": "blog"})
    resp = env.client.get("/projects/blog/addresses")
    assert (resp.status_code, resp.json()["error"]["code"]) == (404, "not_found")
    assert TestClient(env.app).get(CHALLENGE + TOKEN).status_code == 401
    assert env.client.get("/projects/blog").json()["domains"] == []


def test_the_guest_token_reads_addresses_but_cannot_add(site_env):
    assert site_env.client.get("/projects/shop/addresses").status_code == 200
    assert site_env.client.post("/projects/shop/addresses",
                                json={"hostname": "example.com"}).status_code in (404, 405)
    assert "add_instance_domain" not in site_env.cloud.names()


def test_the_console_reads_the_addresses_too(site_env):
    resp = console(site_env).get("/api/projects/shop/addresses")
    assert resp.status_code == 200
    assert resp.json()["addresses"] == [{"kind": "eggie", "hostname": "shop.foo.eggie.space",
                                         "url": "https://shop.foo.eggie.space",
                                         "serving": "direct"}]


def test_the_console_adds_a_domain(site_env):
    site_env.cloud.replies["add_instance_domain"] = [
        {"domain": wire(), "suggested": "www.example.com"}]
    resp = console(site_env).post("/api/projects/shop/addresses",
                                  json={"hostname": "https://Example.com/"})
    assert resp.status_code == 201, resp.text
    assert resp.json()["suggested"] == "www.example.com"
    assert [a["hostname"] for a in resp.json()["addresses"]] == [
        "example.com", "shop.foo.eggie.space"]
    assert site_env.cloud.calls[-1] == ("add_instance_domain", "at", "example.com", "shop",
                                        "shop.foo.eggie.space")


@pytest.mark.parametrize("method, suffix, call, reply", [
    ("DELETE", "", "delete_instance_domain", {}),
    ("POST", "/primary", "primary_instance_domain", {"domains": [wire()]}),
    ("POST", "/check", "check_instance_domain", {"domain": wire()}),
])
def test_each_console_action_reaches_its_own_service_call(site_env, method, suffix, call,
                                                          reply):
    mirror(site_env, wire())
    site_env.cloud.replies[call] = [reply]
    resp = console(site_env).request(
        method, f"/api/projects/shop/addresses/example.com{suffix}")
    assert resp.status_code == 200, resp.text
    assert site_env.cloud.calls[-1] == (call, "at", "example.com")


def test_the_challenge_answers_plain_text_without_any_credential(site_env):
    site_env.cloud.replies["acme_challenge"] = [{"key_authorization": "a.b"}]
    r = site_env.bare.get(CHALLENGE + TOKEN, headers={"Host": "example.com"})
    assert (r.status_code, r.text, r.headers["content-type"].split(";")[0]) == (
        200, "a.b", "text/plain")
    assert site_env.cloud.calls == [("acme_challenge", "at", TOKEN)]


def test_a_malformed_challenge_token_never_reaches_the_service(site_env):
    bare = site_env.bare
    assert bare.get(CHALLENGE + "..%2Fhealth").status_code == 404
    for bad in ("too-short", "a" * 201, "a" * 42 + ".", "a" * 42 + "%20"):
        resp = bare.get(CHALLENGE + bad)
        assert (resp.status_code, resp.json()["error"]["code"]) == (
            404, "challenge_not_found"), bad
    assert "acme_challenge" not in site_env.cloud.names()


def from_client(k, address):
    return k.bare.get(CHALLENGE + TOKEN, headers={"X-Forwarded-For": address})


def test_the_twenty_first_challenge_from_one_address_is_refused_but_not_others(site_env):
    site_env.cloud.replies["acme_challenge"] = [{"key_authorization": "a.b"}] * 21
    for _ in range(20):
        assert from_client(site_env, "203.0.113.9").status_code == 200
    refused = from_client(site_env, "203.0.113.9")
    assert (refused.status_code, refused.json()["error"]["code"]) == (
        404, "challenge_not_found")
    assert from_client(site_env, "198.51.100.1").status_code == 200
    assert site_env.cloud.names().count("acme_challenge") == 21


def test_the_three_hundred_and_first_challenge_in_a_minute_is_refused(site_env):
    site_env.cloud.replies["acme_challenge"] = [{"key_authorization": "a.b"}] * 300
    for n in range(300):
        assert from_client(site_env, f"203.0.113.{n % 20}").status_code == 200, n
    refused = from_client(site_env, "198.51.100.1")
    assert (refused.status_code, refused.json()["error"]["code"]) == (
        404, "challenge_not_found")
    assert site_env.cloud.names().count("acme_challenge") == 300


def test_a_custom_host_reaches_nothing_else_on_the_api(site_env):
    custom = {"Host": "example.com"}
    assert site_env.bare.get("/api/health", headers=custom).status_code == 403
    assert site_env.bare.get("/projects/shop/addresses", headers=custom).status_code == 401


def test_on_a_site_only_the_challenge_path_skips_the_bearer_token(site_env):
    checked = 0
    for route in every_route(site_env.app):
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None) or set()
        if not path or path == "/health" or path.startswith(("/api/", CHALLENGE)):
            continue
        concrete = re.sub(r"\{[^}]+\}", "x", path)
        for method in methods - {"HEAD", "OPTIONS"}:
            checked += 1
            resp = site_env.bare.request(method, concrete)
            assert resp.status_code == 401, f"{method} {concrete} answered {resp.status_code}"
    assert checked >= 15


def test_the_first_url_is_the_custom_domain_while_it_serves(site_env):
    serve(site_env)
    payload = site_env.client.get("/projects/shop").json()
    assert payload["urls"][0] == "https://example.com" and \
        payload["web"][0]["url"] == "https://example.com"
    assert payload["domains"] == [{"hostname": "example.com", "display": "example.com",
                                   "status": "active", "primary": True}]


def test_a_domain_not_serving_yet_leaves_the_eggie_address_first(site_env):
    mirror(site_env, wire(status="waiting_dns"))
    payload = site_env.client.get("/projects/shop").json()
    assert payload["urls"] == ["https://shop.foo.eggie.space"]
    assert payload["domains"][0]["status"] == "waiting_dns"


def test_deleting_a_project_forgets_its_domains(site_env):
    serve(site_env)
    assert site_env.client.delete("/projects/shop").status_code == 200
    assert site_env.repos.domains.list() == []
    assert site_env.repos.domains.queued() == ["example.com"]


def test_a_replace_delete_keeps_them(site_env):
    serve(site_env)
    resp = site_env.client.delete("/projects/shop",
                                  params={"purge": "true", "keep_domains": "true"})
    assert resp.status_code == 200
    assert [r["hostname"] for r in site_env.repos.domains.list()] == ["example.com"]
    assert site_env.repos.domains.queued() == []


def test_the_report_to_the_service_carries_the_custom_address(site_env):
    serve(site_env)
    s = site_env.services
    report = InstanceProjects(site_env.repos.projects, s.loader, s.account, site_env.cloud)
    assert report.snapshot() == [{"ref": "shop", "name": "shop",
                                  "public_url": "https://example.com"}]
