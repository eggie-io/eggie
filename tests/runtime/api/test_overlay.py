from eggie_api.core.overlay import build_overlay, host_for
from eggie_api.core.detect import WebSpec


def test_host_primary_is_bare_id_additional_prefixed():
    assert host_for("myproj", WebSpec("app", 80), "d.io") == "myproj.d.io"
    assert host_for("myproj", WebSpec("api", 4000, subdomain="api"), "d.io") == "api.myproj.d.io"


def test_overlay_attaches_edge_network_and_router_labels():
    ov = build_overlay("myproj", [WebSpec("app", 8080)], "d.io")
    svc = ov["services"]["app"]
    assert svc["networks"] == ["default", "edge"]
    labels = svc["labels"]
    assert labels["traefik.enable"] == "true"
    assert labels["traefik.http.routers.myproj-app.rule"] == "Host(`myproj.d.io`)"
    assert labels["traefik.http.services.myproj-app.loadbalancer.server.port"] == "8080"
    assert ov["networks"]["edge"] == {"external": True}


def test_overlay_only_contains_web_services():
    ov = build_overlay("p", [WebSpec("web", 80)], "d.io")
    # redis/db never appear — the overlay is additive and touches only web svcs
    assert list(ov["services"].keys()) == ["web"]


def test_two_web_services_get_distinct_hosts_and_routers():
    ov = build_overlay("shop", [
        WebSpec("frontend", 3000),
        WebSpec("api", 4000, subdomain="api"),
    ], "d.io")
    fe = ov["services"]["frontend"]["labels"]
    api = ov["services"]["api"]["labels"]
    assert fe["traefik.http.routers.shop-frontend.rule"] == "Host(`shop.d.io`)"
    assert api["traefik.http.routers.shop-api.rule"] == "Host(`api.shop.d.io`)"


def test_every_service_gets_the_secret_names_and_web_routing_is_kept():
    ov = build_overlay("p", [WebSpec("web", 80)], "d.io",
                       services=["web", "worker"], secret_names={"B", "A"})
    assert ov["services"]["web"]["environment"] == ["A", "B"]
    assert "traefik.enable" in ov["services"]["web"]["labels"]
    assert ov["services"]["worker"] == {"environment": ["A", "B"]}


def test_non_web_services_get_the_secret_names_too():
    ov = build_overlay("p", [], "d.io", services=["db", "worker"],
                       secret_names=["KEY"])
    assert ov["services"] == {"db": {"environment": ["KEY"]},
                              "worker": {"environment": ["KEY"]}}


def test_no_secrets_leaves_the_overlay_as_before():
    ov = build_overlay("p", [WebSpec("web", 80)], "d.io", services=["web", "db"])
    assert "environment" not in ov["services"]["web"]
    assert "db" not in ov["services"]
