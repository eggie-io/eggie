from eggie_api.domain.project import (
    load_project, classify, overlay_yaml,
    STARTED_OK, FAILED_TO_START, CRASH_LOOPING,
)
from host.core.provider import Completed
import yaml


def test_load_project_uses_explicit_web_over_detection():
    compose = {"services": {"a": {"image": "x", "ports": ["1:2"]},
                            "b": {"image": "y", "ports": ["3:4"]}}}
    proj = load_project(compose, {"id": "custom", "web": [{"service": "b", "port": 4}]}, "dir")
    assert proj.id == "custom"
    assert [w.service for w in proj.webs] == ["b"]


def test_load_project_falls_back_to_detection_and_dirname():
    compose = {"services": {"only": {"image": "nginx", "ports": ["8080:80"]}}}
    proj = load_project(compose, None, "My Dir")
    assert proj.id == "my-dir"          # sanitized directory name
    assert proj.webs[0].service == "only"


def test_classify_failed_to_start_on_nonzero_up():
    assert classify(Completed(1, "", "boom"), "[]") == FAILED_TO_START


def test_classify_crash_looping_on_restarting_container():
    ps = '[{"Service":"web","State":"restarting","ExitCode":1}]'
    assert classify(Completed(0, "", ""), ps) == CRASH_LOOPING


def test_classify_started_ok_when_running():
    ps = '[{"Service":"web","State":"running","ExitCode":0}]'
    assert classify(Completed(0, "", ""), ps) == STARTED_OK


def test_overlay_yaml_roundtrips_to_expected_structure():
    compose = {"services": {"web": {"image": "nginx", "ports": ["8080:80"]}}}
    proj = load_project(compose, None, "p")
    parsed = yaml.safe_load(overlay_yaml(proj, "d.io"))
    assert parsed["services"]["web"]["labels"]["traefik.enable"] == "true"


def test_load_project_assigns_distinct_subdomains_to_multi_web():
    compose = {"services": {
        "frontend": {"image": "x", "ports": ["3000:3000"]},
        "api": {"image": "y", "ports": ["4000:4000"]},
    }}
    proj = load_project(compose, None, "shop")
    assert proj.webs[0].subdomain is None
    assert proj.webs[1].subdomain == proj.webs[1].service


def test_load_project_single_web_stays_bare():
    compose = {"services": {"only": {"image": "nginx", "ports": ["8080:80"]}}}
    proj = load_project(compose, None, "shop")
    assert proj.webs[0].subdomain is None


def test_load_project_multi_web_hosts_are_distinct_in_overlay():
    compose = {"services": {
        "frontend": {"image": "x", "ports": ["3000:3000"]},
        "api": {"image": "y", "ports": ["4000:4000"]},
    }}
    proj = load_project(compose, None, "shop")
    parsed = yaml.safe_load(overlay_yaml(proj, "d.io"))
    rules = []
    for svc in parsed["services"].values():
        for key, value in svc["labels"].items():
            if key.startswith("traefik.http.routers.") and key.endswith(".rule"):
                rules.append(value)
    assert len(rules) == 2
    assert len(set(rules)) == 2


def test_classify_crash_looping_from_ndjson():
    ps = ('{"Service":"web","State":"running","ExitCode":0}\n'
          '{"Service":"worker","State":"restarting","ExitCode":1}')
    assert classify(Completed(0, "", ""), ps) == CRASH_LOOPING


def test_classify_started_ok_from_ndjson_all_running():
    ps = ('{"Service":"web","State":"running","ExitCode":0}\n'
          '{"Service":"worker","State":"running","ExitCode":0}')
    assert classify(Completed(0, "", ""), ps) == STARTED_OK


def test_load_project_slugs_explicit_id():
    compose = {"services": {"a": {"image": "x", "ports": ["1:2"]}}}
    proj = load_project(compose, {"id": "My Proj!", "web": [{"service": "a", "port": 80}]}, "dir")
    assert proj.id == "my-proj"
