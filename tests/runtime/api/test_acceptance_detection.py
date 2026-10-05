from pathlib import Path
from eggie_api.core.compose import load_compose
from eggie_api.core.detect import detect_web

FIX = Path("tests/fixtures/compose")


def test_php_nginx_mysql_picks_nginx_only():
    webs = detect_web(load_compose(FIX / "php_nginx_mysql.yml"))
    assert [(w.service, w.port) for w in webs] == [("nginx", 80)]


def test_node_postgres_picks_app():
    webs = detect_web(load_compose(FIX / "node_postgres.yml"))
    assert [(w.service, w.port) for w in webs] == [("app", 3000)]


def test_python_redis_picks_web_build_service():
    webs = detect_web(load_compose(FIX / "python_redis.yml"))
    assert [(w.service, w.port) for w in webs] == [("web", 5000)]


def test_build_only_single_service():
    webs = detect_web(load_compose(FIX / "build_only.yml"))
    assert [(w.service, w.port) for w in webs] == [("app", 8000)]


def test_two_http_services_both():
    webs = sorted(detect_web(load_compose(FIX / "two_http.yml")), key=lambda w: w.service)
    assert [(w.service, w.port) for w in webs] == [("api", 4000), ("frontend", 3000)]
