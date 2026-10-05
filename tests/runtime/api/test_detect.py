import pytest
from eggie_api.core.detect import detect_web, WebSpec, AmbiguousError


def test_single_service_with_published_port():
    compose = {"services": {"web": {"image": "nginx", "ports": ["8080:80"]}}}
    assert detect_web(compose) == [WebSpec(service="web", port=80)]


def test_single_service_uses_expose_when_no_ports():
    compose = {"services": {"app": {"build": ".", "expose": [3000]}}}
    assert detect_web(compose) == [WebSpec(service="app", port=3000)]


def test_single_service_defaults_to_80_when_nothing_declared():
    compose = {"services": {"app": {"build": "."}}}
    assert detect_web(compose) == [WebSpec(service="app", port=80)]


def test_non_http_services_are_ignored():
    compose = {"services": {
        "web": {"image": "nginx", "ports": ["8080:80"]},
        "redis": {"image": "redis"},
        "db": {"image": "postgres", "expose": [5432]},
    }}
    result = detect_web(compose)
    # only services that publish a host port are treated as web
    assert result == [WebSpec(service="web", port=80)]


def test_two_http_services_both_detected():
    compose = {"services": {
        "frontend": {"image": "node", "ports": ["3000:3000"]},
        "api": {"image": "node", "ports": ["4000:4000"]},
    }}
    result = sorted(detect_web(compose), key=lambda w: w.service)
    assert result == [WebSpec(service="api", port=4000),
                      WebSpec(service="frontend", port=3000)]


def test_multi_service_none_published_is_ambiguous():
    compose = {"services": {
        "a": {"image": "x", "expose": [1000]},
        "b": {"image": "y", "expose": [2000]},
    }}
    with pytest.raises(AmbiguousError):
        detect_web(compose)
