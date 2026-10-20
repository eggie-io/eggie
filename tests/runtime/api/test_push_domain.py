import pytest

from eggie_api.domain.push import (
    DEFAULT_HINT, DataPlan, Database, Manifest, build_manifest, check_replace, data_plan,
    databases, docker_volume_name, is_excluded, is_floating, kitchen_project_id,
    merge_secret_names, parse_manifest, replace_mode, safe_return_path)
from eggie_api.errors import Invalid


def test_is_excluded_matches_any_component_and_the_overlay():
    assert is_excluded("app/node_modules/x.js") and is_excluded(".git/config")
    assert is_excluded(".eggie/overlay.yml") and not is_excluded(".eggie/project.yml")
    assert not is_excluded("src/gitignore.txt")


def test_floating_tags():
    assert is_floating("postgres") and is_floating("postgres:latest") and is_floating("redis:alpine")
    assert not is_floating("postgres:16") and not is_floating("postgres@sha256:" + "a" * 64)
    assert not is_floating("registry.local:5000/postgres:16.2")
    assert is_floating("registry.local:5000/postgres")


def test_databases_found_by_last_path_segment():
    compose = {"services": {"db": {"image": "postgis/postgis:16-3.4"},
                            "cache": {"image": "docker.io/library/redis"},
                            "web": {"image": "nginx"}, "app": {"build": "."}}}
    assert databases(compose) == [Database("cache", "docker.io/library/redis", True),
                                  Database("db", "postgis/postgis:16-3.4", False)]


def test_data_plan_named_volumes_external_skipped_and_db_bind_paths_only():
    compose = {"services": {
        "db": {"image": "postgres:16", "volumes": ["./pgdata:/var/lib/postgresql/data", "dbv:/x"]},
        "web": {"image": "node", "volumes": ["./src:/app", {"type": "volume", "source": "uploads", "target": "/u"}]},
        "ext": {"image": "x", "volumes": ["shared:/s"]}},
        "volumes": {"dbv": {}, "uploads": None, "shared": {"external": True}, "unused": {}}}
    assert data_plan(compose) == DataPlan(volumes=("dbv", "uploads"), data_paths=("pgdata",), external=("shared",))


def test_db_bind_mount_outside_the_project_is_not_data():
    compose = {"services": {"db": {"image": "mysql:8", "volumes": ["/srv/mysql:/var/lib/mysql", "../up:/u"]}}}
    assert data_plan(compose).data_paths == ()


def test_docker_volume_name_honours_compose_and_volume_names():
    compose = {"name": "shop", "volumes": {"dbv": {}, "pinned": {"name": "real-name"}}}
    assert docker_volume_name(compose, "shop", "dbv") == "shop_dbv"
    assert docker_volume_name(compose, "shop", "pinned") == "real-name"


def test_replace_rules():
    assert replace_mode(False, True) == "none" and replace_mode(True, False) == "files"
    assert replace_mode(True, True) == "files-and-data"
    assert check_replace(False, True, "none") is None
    assert check_replace(True, False, "none") == "already_on_kitchen"
    assert check_replace(True, True, "files") == "confirm_replace_data"
    assert check_replace(True, True, "files-and-data") is None


def test_secret_merge_table():
    m = merge_secret_names({"A": "", "B": "from local", "C": ""}, stored={"A", "OLD"}, requested={"C", "GONE"})
    assert m.keep == ("A",) and m.unused == ("OLD",) and m.drop_requests == ("GONE",)
    assert m.request == {"B": "from local", "C": DEFAULT_HINT}


def test_kitchen_project_id_suffixes_taken_ids():
    assert kitchen_project_id("My Shop", set()) == "my-shop"
    assert kitchen_project_id("My Shop", {"my-shop", "my-shop-2"}) == "my-shop-3"


@pytest.mark.parametrize("bad", ["", "x", "//evil.com", "/\\evil", "/a\\b", "/a#b", "/a b", "/a\x01", "/" + "a" * 1024])
def test_unsafe_return_paths_become_none(bad):
    assert safe_return_path(bad) is None


def test_safe_return_path_keeps_a_query():
    assert safe_return_path("/p/shop/secrets?x=1") == "/p/shop/secrets?x=1"


def test_manifest_round_trip_and_refusals():
    compose = {"services": {"db": {"image": "postgres:16", "volumes": ["./pgdata:/d", "dbv:/x"]}}, "volumes": {"dbv": {}}}
    raw = build_manifest(data=True, plan=data_plan(compose), secrets={"API_KEY": "hint"})
    assert parse_manifest(raw, compose) == Manifest(True, ("dbv",), ("pgdata",), {"API_KEY": "hint"})
    no_data = build_manifest(data=False, plan=data_plan(compose), secrets={})
    assert parse_manifest(no_data, compose) == Manifest(False, ("dbv",), ("pgdata",), {})
    for broken in ({**raw, "format": 2}, {**raw, "volumes": ["nope"]}, {**raw, "data_paths": ["../etc"]},
                   {**raw, "secrets": [{"name": "PATH", "hint": ""}]}):
        with pytest.raises(Invalid):
            parse_manifest(broken, compose)
