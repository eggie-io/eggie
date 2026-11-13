import hashlib

import pytest

from eggie_api.infra.cloud import CloudError
from tests.runtime.api.conftest import _make_env
from tests.runtime.api.fake_cloud import FakeCloud
from tests.runtime.api.recipe_zips import RECIPE_MD, recipe_zip, zip_bytes

CAP = 100 * 1024 ** 2


def _meta(body: bytes, version=3, **bundle):
    return {"slug": "blog", "title": "Blog",
            "bundle": {"version": version, "sha256": hashlib.sha256(body).hexdigest(),
                       "size_bytes": len(body), "commit": "abc", **bundle}}


@pytest.fixture
def make(tmp_path):
    envs = []

    def _make(**replies):
        cloud = FakeCloud(**replies)
        gen = _make_env(tmp_path, cloud=cloud)
        env = next(gen)
        envs.append(gen)
        env.cloud = cloud
        env.recipes = env.config.projects_root / ".recipes"
        return env
    yield _make
    for gen in envs:
        gen.close()


def _code(resp):
    return resp.status_code, resp.json()["error"]["code"]


def _nothing_left(env):
    assert not (env.recipes / "blog").exists()
    assert not list(env.recipes.glob(".tmp-*"))


def test_get_unpacks_the_bundle_and_returns_its_instructions(make):
    body = recipe_zip()
    env = make(recipe=[_meta(body)], recipe_bundle=[body])
    resp = env.client.post("/recipes/blog/get")
    assert resp.status_code == 200, resp.text
    out = resp.json()
    target = env.recipes / "blog" / "3"
    assert out["recipe_md"] == str(target / "RECIPE.md")
    assert out["path"] == str(target)
    assert (out["slug"], out["version"]) == ("blog", 3)
    assert out["instructions"] == RECIPE_MD
    assert (target / "scripts/run.sh").stat().st_mode & 0o777 == 0o775


def test_a_version_already_unpacked_is_not_downloaded_again(make):
    body = recipe_zip()
    env = make(recipe=[_meta(body), _meta(body)], recipe_bundle=[body])
    assert env.client.post("/recipes/blog/get").status_code == 200
    second = env.client.post("/recipes/blog/get")
    assert second.status_code == 200
    assert second.json()["instructions"] == RECIPE_MD
    assert env.cloud.names().count("recipe_bundle") == 1


def test_a_newer_version_unpacks_beside_the_old_one(make):
    body = recipe_zip()
    env = make(recipe=[_meta(body, 3), _meta(body, 4)], recipe_bundle=[body, body])
    env.client.post("/recipes/blog/get")
    env.client.post("/recipes/blog/get")
    assert (env.recipes / "blog/3/RECIPE.md").is_file()
    assert (env.recipes / "blog/4/RECIPE.md").is_file()


def test_a_mismatched_checksum_leaves_nothing_behind(make):
    body = recipe_zip()
    env = make(recipe=[_meta(body, sha256="0" * 64)], recipe_bundle=[body])
    assert _code(env.client.post("/recipes/blog/get")) == (502, "bad_recipe_bundle")
    _nothing_left(env)


def test_a_refused_bundle_leaves_nothing_behind(make):
    body = recipe_zip(("../evil", b"x", 0o100644))
    env = make(recipe=[_meta(body)], recipe_bundle=[body])
    assert _code(env.client.post("/recipes/blog/get")) == (502, "bad_recipe_bundle")
    _nothing_left(env)
    assert not (env.config.projects_root / "evil").exists()


@pytest.mark.parametrize("slug", ["%2E%2E", "a.b", "Blog", "a_b", "-a", "a" * 64])
def test_a_slug_that_is_not_a_recipe_name_is_refused_before_any_call(make, slug):
    env = make()
    assert _code(env.client.post(f"/recipes/{slug}/get")) == (422, "invalid_recipe")
    assert env.cloud.calls == []


def test_a_recipe_without_a_bundle_says_so(make):
    env = make(recipe=[{"slug": "blog", "bundle": None}])
    assert _code(env.client.post("/recipes/blog/get")) == (409, "recipe_has_no_bundle")


def test_an_unknown_recipe_is_not_found(make):
    env = make(recipe=[CloudError("recipe_not_found", "m", 404)])
    resp = env.client.post("/recipes/blog/get")
    assert _code(resp) == (404, "recipe_not_found")
    assert resp.json()["error"]["message"] == "There is no published recipe 'blog'."


def test_a_bundle_bigger_than_the_cap_is_refused_before_download(make):
    env = make(recipe=[_meta(b"x", size_bytes=CAP + 1)])
    assert _code(env.client.post("/recipes/blog/get")) == (502, "bad_recipe_bundle")
    assert "recipe_bundle" not in env.cloud.names()


@pytest.mark.parametrize("bundle", [
    {"version": 0}, {"version": "3"}, {"sha256": "nothex"}, {"size_bytes": -1}])
def test_bundle_metadata_that_cannot_be_trusted_is_refused(make, bundle):
    env = make(recipe=[_meta(b"x", **bundle)])
    assert _code(env.client.post("/recipes/blog/get")) == (502, "bad_recipe_bundle")
    assert "recipe_bundle" not in env.cloud.names()


def test_a_signed_out_vm_gets_recipes_without_a_token(make):
    body = recipe_zip()
    env = make(recipe=[_meta(body)], recipe_bundle=[body])
    env.client.post("/recipes/blog/get")
    assert env.cloud.calls[0] == ("recipe", None, "blog")
    assert env.cloud.calls[1][:2] == ("recipe_bundle", None)


def test_list_shows_only_recipes_an_agent_can_get(make):
    env = make(recipes=[{"items": [
        {"slug": "blog", "title": "Blog", "summary": "Use when …", "author": "Eggie"},
        {"slug": "video-only", "title": "V", "summary": None},
        {"title": "no slug", "summary": "x"}]}])
    resp = env.client.get("/recipes")
    assert resp.status_code == 200
    assert resp.json() == {"recipes": [
        {"slug": "blog", "title": "Blog", "summary": "Use when …"}]}


def test_a_signed_in_vm_sends_its_token_for_the_recipe_and_its_bundle(make):
    body = recipe_zip()
    env = make(recipe=[_meta(body)], recipe_bundle=[body])
    env.repos.account.update(access_token="at-1", refresh_token="rt",
                             access_expires_at=10**12, org_id="org-1")
    assert env.client.post("/recipes/blog/get").status_code == 200
    assert env.cloud.calls[0] == ("recipe", "at-1", "blog")
    assert env.cloud.calls[1][:2] == ("recipe_bundle", "at-1")
