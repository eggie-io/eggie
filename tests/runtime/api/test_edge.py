from __future__ import annotations

import yaml

from eggie_api.domain.edge import Domain, Pair, Plan, ProjectEdge, build_edge, plan, serving

EGGIE = "shop.foo.eggie.space"
PAIRS = [
    Pair("i", "instance", "/e/certs/i/cert.pem", "/e/certs/i/key.pem"),
    Pair("c1", "domain", "/e/certs/c1/cert.pem", "/e/certs/c1/key.pem"),
    Pair("c2", "domain", "/e/certs/c2/cert.pem", "/e/certs/c2/key.pem"),
]
PROJECTS = {"shop": ProjectEdge(EGGIE, "shop--web")}


def d(host, *, primary=False, status="active", cert="c1", project="shop"):
    return Domain(host, project, primary, status, cert)


def _router_for(edge, rule):
    return next(r for r in edge["http"]["routers"].values() if r["rule"] == rule)


def test_a_serving_primary_takes_the_address_and_everything_else_redirects():
    got = plan(EGGIE, [d("example.com", primary=True), d("www.example.com", cert="c2")], {"c1", "c2"})
    assert got == Plan("example.com", (), ("www.example.com", EGGIE))


def test_a_primary_that_is_not_serving_leaves_the_eggie_address_and_others_direct():
    got = plan(EGGIE, [d("example.com", primary=True, status="problem"),
                       d("www.example.com", cert="c2")], {"c1", "c2"})
    assert got == Plan(EGGIE, ("www.example.com",), ())


def test_active_without_its_certificate_installed_is_not_serving():
    assert not serving(d("example.com"), set())


def test_an_unknown_status_is_not_serving():
    assert not serving(d("example.com", status="paused"), {"c1"})


def test_no_domains_writes_only_certificates():
    edge = build_edge([Pair("i", "instance", "/e/certs/i/cert.pem", "/e/certs/i/key.pem")], [], {})
    assert edge == {"tls": {
        "certificates": [{"certFile": "/e/certs/i/cert.pem", "keyFile": "/e/certs/i/key.pem"}],
        "stores": {"default": {"defaultCertificate": {
            "certFile": "/e/certs/i/cert.pem", "keyFile": "/e/certs/i/key.pem"}}}}}


def test_nothing_installed_and_no_domains_is_an_empty_file():
    assert build_edge([], [], {}) == {}


def test_a_direct_domain_routes_to_the_projects_docker_service_on_web_only():
    edge = build_edge(PAIRS, [d("example.com", primary=True, status="problem"),
                              d("www.example.com", cert="c2")], PROJECTS)
    router = _router_for(edge, "Host(`www.example.com`)")
    assert router["service"] == "shop--web@docker" and router["entryPoints"] == ["web"]


def test_the_eggie_host_redirects_on_web_only_with_the_cache_header():
    edge = build_edge(PAIRS, [d("example.com", primary=True)], PROJECTS)
    router = _router_for(edge, f"Host(`{EGGIE}`)")
    assert router["entryPoints"] == ["web"] and router["service"] == "noop@internal"
    assert router["priority"] == 50000
    mws = edge["http"]["middlewares"]
    regex = next(m["redirectRegex"] for m in mws.values() if "redirectRegex" in m)
    assert regex == {"regex": "^https?://[^/]+/(.*)",
                     "replacement": "https://example.com/${1}", "permanent": True}
    assert {"headers": {"customResponseHeaders": {"Cache-Control": "max-age=600"}}} in mws.values()


def test_the_challenge_router_covers_every_mirrored_domain_whatever_its_status():
    edge = build_edge(PAIRS, [d("example.com", status="waiting_dns", cert=None)], PROJECTS)
    router = edge["http"]["routers"]["acme-challenge"]
    assert router == {"rule": "(Host(`example.com`)) && PathPrefix(`/.well-known/acme-challenge/`)",
                      "entryPoints": ["http", "web"], "priority": 200000,
                      "service": "eggie-api@docker"}


def test_a_name_that_is_not_a_hostname_never_reaches_a_rule():
    edge = build_edge(PAIRS, [d("exa`mple.com"), d("ok.example.com", cert="c2")], PROJECTS)
    assert "`mple" not in yaml.safe_dump(edge)


def test_a_domain_whose_project_is_not_here_gets_no_route():
    edge = build_edge(PAIRS, [d("example.com", project="gone")], PROJECTS)
    assert all("example.com" not in r["rule"] or name == "acme-challenge"
               for name, r in edge["http"]["routers"].items())
