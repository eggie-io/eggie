import pytest

from eggie_api.domain.push import (
    DEFAULT_HINT, DataPlan, Floating, Manifest, build_manifest, check_replace, data_plan,
    docker_volume_name, floating, is_excluded, is_floating, instance_project_id,
    merge_secret_names, parse_manifest, safe_return_path)
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


def test_data_is_every_named_volume_a_service_mounts_and_binds_are_files():
    compose = {"services": {
        "db": {"image": "postgres:16", "volumes": ["./pgdata:/var/lib/postgresql/data", "dbv:/x"]},
        "web": {"image": "node", "volumes": ["./src:/app", "~/cache:/c", "/anon",
                                             {"type": "volume", "source": "uploads", "target": "/u"}]},
        "ext": {"image": "x", "volumes": ["shared:/s"]}},
        "volumes": {"dbv": {}, "uploads": None, "shared": {"external": True}, "unused": {}}}
    assert data_plan(compose) == DataPlan(volumes=("dbv", "uploads"), external=("shared",))


def test_floating_warns_only_where_a_copied_volume_meets_a_moving_tag():
    compose = {"services": {
        "db": {"image": "postgres", "volumes": ["dbv:/var/lib/postgresql/data"]},
        "pinned": {"image": "redis:7", "volumes": ["cache:/data"]},
        "web": {"image": "nginx:latest", "volumes": ["./site:/usr/share/nginx/html"]},
        "ext": {"image": "mongo", "volumes": ["shared:/data/db"]}},
        "volumes": {"dbv": {}, "cache": {}, "shared": {"external": True}}}
    assert floating(compose) == [Floating("db", "postgres")]


def test_docker_volume_name_honours_compose_and_volume_names():
    compose = {"name": "shop", "volumes": {"dbv": {}, "pinned": {"name": "real-name"}}}
    assert docker_volume_name(compose, "shop", "dbv") == "shop_dbv"
    assert docker_volume_name(compose, "shop", "pinned") == "real-name"


def test_only_an_unannounced_replace_is_refused():
    assert check_replace(True, "none") == "already_on_instance"
    assert check_replace(True, "all") is None
    assert check_replace(False, "none") is None and check_replace(False, "all") is None


def test_secret_merge_table():
    m = merge_secret_names({"A": "", "B": "from local", "C": ""}, stored={"A", "OLD"}, requested={"C", "GONE"})
    assert m.keep == ("A",) and m.unused == ("OLD",) and m.drop_requests == ("GONE",)
    assert m.request == {"B": "from local", "C": DEFAULT_HINT}


def test_instance_project_id_suffixes_taken_ids():
    assert instance_project_id("My Shop", set()) == "my-shop"
    assert instance_project_id("My Shop", {"my-shop", "my-shop-2"}) == "my-shop-3"


@pytest.mark.parametrize("bad", [None, 42, ["/x"], {"path": "/x"}, "", "x", "//evil.com", "/\\evil", "/a\\b", "/a#b", "/a b", "/a\x01", "/" + "a" * 1024])
def test_unsafe_return_paths_become_none(bad):
    assert safe_return_path(bad) is None


def test_safe_return_path_keeps_a_query():
    assert safe_return_path("/p/shop/secrets?x=1") == "/p/shop/secrets?x=1"


def test_manifest_round_trip_and_refusals():
    compose = {"services": {"db": {"image": "postgres:16", "volumes": ["./pgdata:/d", "dbv:/x"]}},
               "volumes": {"dbv": {}}}
    raw = build_manifest(volumes={"dbv": "shop_dbv"}, secrets={"API_KEY": "hint"})
    assert parse_manifest(raw, compose) == Manifest(("dbv",), {"API_KEY": "hint"})
    for broken in ({**raw, "format": 2}, {**raw, "volumes": ["nope"]}, {**raw, "volumes": "dbv"},
                   {**raw, "secrets": [{"name": "PATH", "hint": ""}]}):
        with pytest.raises(Invalid):
            parse_manifest(broken, compose)
