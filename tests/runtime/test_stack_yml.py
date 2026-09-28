import re

import yaml

from pathlib import Path

STACK = Path(__file__).resolve().parents[2] / "runtime" / "stack.yml"
NGINX_CONF = Path(__file__).resolve().parents[2] / "runtime" / "web" / "nginx.conf"

# Compose interpolation: ${VAR} or ${VAR:-default}. Stripping the whole
# expression, not just the default, is what makes the "no bare literal" test
# below meaningful -- a naive strip that kept the default text would let a
# hardcoded 39080 slip back in disguised as someone's idea of a default.
_INTERPOLATION = re.compile(r"\$\{[^}]*\}")


def _text() -> str:
    return STACK.read_text()


def _without_interpolation() -> str:
    return _INTERPOLATION.sub("", _text())


def _services() -> dict:
    return yaml.safe_load(_text())["services"]


def test_stack_yml_hardcodes_no_domain_or_port_outside_a_default():
    # This is the one rule that keeps Phase 5 from becoming a rewrite: every
    # environment injects its own domain and ports through OMELET_DOMAIN /
    # OMELET_EDGE_PORT / OMELET_API_PORT, so nothing here may assume the
    # local values.
    stripped = _without_interpolation()
    assert "39080" not in stripped
    assert "39099" not in stripped
    assert "127-0-0-1.sslip.io" not in stripped


def test_the_api_service_binds_the_port_it_is_published_on():
    # ApiConfig already reads OMELET_API_PORT; publishing a fixed port
    # while the process binds a configured one forwards the host to nothing.
    api = yaml.safe_load(_text())["services"]["api"]
    assert api["ports"] == ["${OMELET_API_PORT:-39099}:"
                            "${OMELET_API_PORT:-39099}"]
    assert "OMELET_API_PORT=${OMELET_API_PORT:-39099}" in api["environment"]


def test_stack_yml_parses_and_has_no_version_key():
    doc = yaml.safe_load(_text())
    assert "version" not in doc


def test_stack_yml_both_services_restart_always_on_the_external_edge_network():
    doc = yaml.safe_load(_text())
    for name in ("traefik", "api", "web"):
        service = doc["services"][name]
        assert service["restart"] == "always"
        assert "edge" in service["networks"]
    assert doc["networks"]["edge"]["external"] is True


def test_stack_yml_mounts_opt_omelet_at_the_identical_path_on_both_sides():
    # The api service parses compose files, but dockerd on the VM resolves the
    # bind-mount paths inside them. A mismatched path here silently mounts
    # every user project's relative volume onto an empty directory.
    volumes = yaml.safe_load(_text())["services"]["api"]["volumes"]
    assert "/opt/omelet:/opt/omelet" in volumes


def test_stack_yml_pins_exact_image_tags():
    doc = yaml.safe_load(_text())
    assert ":latest" not in doc["services"]["traefik"]["image"]
    assert ":latest" not in _text()


def test_bootstrap_pins_a_traefik_that_docker_still_talks_to():
    # Traefik <= 3.5 asks the daemon for Docker API 1.24. docker-ce 29 raised
    # MinAPIVersion to 1.40 and rejects it, so the docker provider loads no
    # containers at all and every request answers 404 — with the routing layer
    # looking healthy. 3.6 is the first release that negotiates.
    import re
    match = re.search(r"traefik:v(\d+)\.(\d+)", _text())
    assert match, "stack.yml must pin an explicit traefik version"
    assert (int(match[1]), int(match[2])) >= (3, 6), \
        f"traefik:v{match[1]}.{match[2]} requests Docker API 1.24, which docker-ce 29 refuses"


def _labels(service: dict) -> dict:
    return dict(label.split("=", 1) for label in service["labels"])


def test_the_web_page_is_reached_only_through_traefik_below_the_api_route():
    # Both routers match Host(localhost) || Host(127.0.0.1); the page's is a
    # catch-all, so it must lose to the api service's /api router or it
    # answers every API call with index.html. No published port: only
    # Traefik fronts it.
    services = yaml.safe_load(_text())["services"]
    web, api = _labels(services["web"]), _labels(services["api"])
    assert int(web["traefik.http.routers.omelet-web.priority"]) \
        < int(api["traefik.http.routers.omelet-api.priority"])
    assert "ports" not in services["web"]


def test_the_web_service_port_label_matches_the_port_nginx_listens_on():
    # A drift here is a silent 502 on the whole page: Traefik would keep
    # routing to a port nginx never binds.
    web = _labels(yaml.safe_load(_text())["services"]["web"])
    labeled_port = web["traefik.http.services.omelet-web.loadbalancer.server.port"]
    match = re.search(r"listen\s+(\d+);", NGINX_CONF.read_text())
    assert match, "web/nginx.conf must have a `listen <port>;` directive"
    assert labeled_port == match[1]


def test_the_tunnel_client_runs_only_on_demand_from_a_token_file():
    tunnel = yaml.safe_load(_text())["services"]["tunnel"]
    assert tunnel["profiles"] == ["tunnel"]
    assert "--token-file /run/omelet/token" in tunnel["command"]
    assert tunnel["volumes"] == ["/opt/omelet/tunnel:/run/omelet:ro"]
    assert "environment" not in tunnel, "the token must not travel in the environment"


def test_the_tunnel_client_shares_a_network_only_with_traefik():
    # Cloudflare's config, not ours, picks where the client sends traffic.
    services = yaml.safe_load(_text())["services"]
    assert services["tunnel"]["networks"] == ["tunnel"]
    assert "tunnel" in services["traefik"]["networks"]
    for name in ("api", "web"):
        assert "tunnel" not in services[name]["networks"]


def test_the_api_receives_the_github_client_id_with_the_same_default():
    api = _services()["api"]
    assert ("OMELET_GITHUB_CLIENT_ID=${OMELET_GITHUB_CLIENT_ID:-Ov23lie5k9VqSCKI52Ci}"
            in api["environment"])
