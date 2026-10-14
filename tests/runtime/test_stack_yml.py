import json
import re
import shutil
import subprocess

import pytest
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
    # environment injects its own domain and ports through EGGIE_DOMAIN /
    # EGGIE_EDGE_PORT / EGGIE_API_PORT, so nothing here may assume the
    # local values.
    stripped = _without_interpolation()
    assert "39080" not in stripped
    assert "39099" not in stripped
    assert "local.eggie.space" not in stripped


def test_the_api_service_binds_the_port_it_is_published_on():
    # ApiConfig already reads EGGIE_API_PORT; publishing a fixed port
    # while the process binds a configured one forwards the host to nothing.
    # The bind address is a variable too: a site keeps the bearer port on
    # loopback, WSL2 needs all interfaces for localhostForwarding.
    api = yaml.safe_load(_text())["services"]["api"]
    assert api["ports"] == ["${EGGIE_API_BIND:-0.0.0.0}:${EGGIE_API_PORT:-39099}:"
                            "${EGGIE_API_PORT:-39099}"]
    assert "EGGIE_API_PORT=${EGGIE_API_PORT:-39099}" in api["environment"]


def test_the_edge_port_bind_address_is_a_variable_so_a_site_can_keep_it_on_loopback():
    # The site's plain-HTTP probe entrypoint answers on this port; published on
    # all interfaces it would bypass TLS for every project host.
    traefik = yaml.safe_load(_text())["services"]["traefik"]
    assert traefik["ports"] == ["${EGGIE_EDGE_BIND:-0.0.0.0}:${EGGIE_EDGE_PORT:-39080}:"
                                "${EGGIE_EDGE_PORT:-39080}"]


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


def test_stack_yml_mounts_opt_eggie_at_the_identical_path_on_both_sides():
    # The api service parses compose files, but dockerd on the VM resolves the
    # bind-mount paths inside them. A mismatched path here silently mounts
    # every user project's relative volume onto an empty directory.
    volumes = yaml.safe_load(_text())["services"]["api"]["volumes"]
    assert "/opt/eggie:/opt/eggie" in volumes


def test_stack_yml_pins_exact_image_tags():
    doc = yaml.safe_load(_text())
    assert ":latest" not in doc["services"]["traefik"]["image"]
    assert ":latest" not in _text()


def test_both_eggie_images_run_the_version_the_install_recorded():
    services = yaml.safe_load(_text())["services"]
    for name, image in (("api", "eggie-api"), ("web", "eggie-web")):
        assert services[name]["image"].endswith(f"/{image}:${{EGGIE_VERSION}}}}"), \
            services[name]["image"]


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
    assert int(web["traefik.http.routers.eggie-web.priority"]) \
        < int(api["traefik.http.routers.eggie-api.priority"])
    assert "ports" not in services["web"]


def test_the_web_service_port_label_matches_the_port_nginx_listens_on():
    # A drift here is a silent 502 on the whole page: Traefik would keep
    # routing to a port nginx never binds.
    web = _labels(yaml.safe_load(_text())["services"]["web"])
    labeled_port = web["traefik.http.services.eggie-web.loadbalancer.server.port"]
    match = re.search(r"listen\s+(\d+);", NGINX_CONF.read_text())
    assert match, "web/nginx.conf must have a `listen <port>;` directive"
    assert labeled_port == match[1]


def test_the_tunnel_client_runs_only_on_demand_from_a_token_file():
    tunnel = yaml.safe_load(_text())["services"]["tunnel"]
    assert tunnel["profiles"] == ["tunnel"]
    assert "--token-file /run/eggie/token" in tunnel["command"]
    assert tunnel["volumes"] == ["/opt/eggie/tunnel:/run/eggie:ro"]
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
    assert ("EGGIE_GITHUB_CLIENT_ID=${EGGIE_GITHUB_CLIENT_ID:-Ov23lie5k9VqSCKI52Ci}"
            in api["environment"])


def test_the_api_receives_the_site_host():
    # .env interpolates the stack but is never injected into containers.
    api = _services()["api"]
    assert "EGGIE_SITE_HOST=${EGGIE_SITE_HOST:-}" in api["environment"]


SITE_STACK = STACK.with_name("stack.site.yml")


def test_the_console_routers_also_answer_on_the_site_host():
    services = yaml.safe_load(_text())["services"]
    for name, router in (("web", "eggie-web"), ("api", "eggie-api")):
        rule = _labels(services[name])[f"traefik.http.routers.{router}.rule"]
        assert "Host(`${EGGIE_SITE_HOST:-localhost}`)" in rule, rule
        assert "Host(`localhost`)" in rule and "Host(`127.0.0.1`)" in rule


def test_the_site_override_moves_the_web_entrypoint_to_443_with_tls():
    # Keeping the entrypoint name `web` is what lets every router label in the
    # stack and in the project overlay stay untouched.
    traefik = yaml.safe_load(SITE_STACK.read_text())["services"]["traefik"]
    command = traefik["command"]
    assert "--entryPoints.web.address=:443" in command
    assert "--entryPoints.web.http.tls=true" in command
    assert "--entryPoints.http.address=:80" in command
    assert "--entryPoints.http.http.redirections.entryPoint.to=web" in command
    assert "--providers.file.directory=/opt/eggie/tls" in command
    assert "--providers.file.watch=true" in command
    # compose replaces `command` wholesale, so the base flags must be repeated.
    base = yaml.safe_load(_text())["services"]["traefik"]["command"]
    for flag in base:
        if "entryPoints.web.address" in flag:
            continue
        assert flag in command, f"base flag dropped by the override: {flag}"
    assert traefik["ports"] == ["80:80", "443:443"]
    assert "/opt/eggie/tls:/opt/eggie/tls:ro" in traefik["volumes"]
    assert set(yaml.safe_load(SITE_STACK.read_text())["services"]) == {"traefik"}


def test_the_probe_port_stays_reachable_on_a_site_and_unpublished():
    # The API's readiness probe dials Traefik on the edge port; a site that
    # drops the entrypoint makes every project start wait out the timeout.
    traefik = yaml.safe_load(SITE_STACK.read_text())["services"]["traefik"]
    assert "--entryPoints.probe.address=:${EGGIE_EDGE_PORT:-39080}" in traefik["command"]
    assert not any("39080" in p or "EGGIE_EDGE_PORT" in p for p in traefik["ports"])


@pytest.mark.skipif(shutil.which("docker") is None, reason="docker compose not installed")
def test_the_merged_site_stack_publishes_only_the_edge_ports_to_the_world(tmp_path):
    env = tmp_path / ".env"
    env.write_text("EGGIE_DOCKER_GID=999\nEGGIE_VERSION=0.0.0\nEGGIE_SITE_HOST=foo.eggie.io\n"
                   "EGGIE_DOMAIN=foo.eggie.space\nEGGIE_API_BIND=127.0.0.1\nEGGIE_EDGE_BIND=127.0.0.1\n")
    result = subprocess.run(
        ["docker", "compose", "--env-file", str(env), "-f", str(STACK), "-f", str(SITE_STACK),
         "config", "--format", "json"], capture_output=True, text=True)
    if result.returncode != 0:
        pytest.skip(f"docker compose config unavailable: {result.stderr.strip()[:120]}")
    services = json.loads(result.stdout)["services"]
    published = {(p.get("host_ip", ""), p["published"]) for p in services["traefik"]["ports"]}
    assert {("", "80"), ("", "443")} <= published
    assert ("", "39080") not in published and ("0.0.0.0", "39080") not in published
    (api_port,) = services["api"]["ports"]
    assert api_port["host_ip"] == "127.0.0.1"
    rule = services["web"]["labels"]["traefik.http.routers.eggie-web.rule"]
    assert "Host(`foo.eggie.io`)" in rule
    assert services["api"]["environment"]["EGGIE_SITE_HOST"] == "foo.eggie.io"
