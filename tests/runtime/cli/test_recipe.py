import hashlib

from eggie_api.infra.cloud import CloudError
from tests.runtime.api.conftest import AUTH, COMPOSE_ONE_WEB
from tests.runtime.api.recipe_zips import RECIPE_MD, recipe_zip


def test_recipe_get_prints_the_folder_then_recipe_md(recipe_guest):
    body = recipe_zip()
    recipe_guest.cloud.replies["recipe"] = [{"slug": "blog", "bundle": {
        "version": 3, "sha256": hashlib.sha256(body).hexdigest(),
        "size_bytes": len(body), "commit": "abc"}}]
    recipe_guest.cloud.replies["recipe_bundle"] = [body]
    code, out, err = recipe_guest.run("recipe", "get", "blog", cwd=recipe_guest.root)
    assert (code, err) == (0, "")
    assert out.startswith(f"Recipe blog (version 3) is in {recipe_guest.root}/.recipes/blog/3\n")
    assert out.endswith(RECIPE_MD)


def test_recipe_get_of_an_unknown_recipe_says_so(recipe_guest):
    recipe_guest.cloud.replies["recipe"] = [CloudError("recipe_not_found", "m", 404)]
    code, _out, err = recipe_guest.run("recipe", "get", "nope", cwd=recipe_guest.root)
    assert code == 1
    assert "There is no published recipe 'nope'." in err


def test_recipe_add_records_the_recipe_on_the_project_you_are_in(recipe_guest):
    folder = recipe_guest.root / "app"
    folder.mkdir()
    (folder / "docker-compose.yml").write_text(COMPOSE_ONE_WEB)
    recipe_guest._client.post("/projects", json={"id": "app", "origin": "folder"},
                              headers=AUTH)
    recipe_guest.cloud.replies["recipe"] = [{"slug": "mobile-app"}]
    code, out, err = recipe_guest.run("recipe", "add", "mobile-app", cwd=folder)
    assert (code, out, err) == (0, "mobile-app added to app.\n", "")
    assert "mobile-app" in (folder / ".eggie/project.yml").read_text()


def test_recipe_add_makes_the_eggie_folder_writable_for_the_api_first(recipe_guest):
    folder = recipe_guest.root / "app"
    (folder / ".eggie").mkdir(parents=True)
    (folder / ".eggie").chmod(0o755)
    (folder / "docker-compose.yml").write_text(COMPOSE_ONE_WEB)
    recipe_guest._client.post("/projects", json={"id": "app", "origin": "folder"},
                              headers=AUTH)
    recipe_guest.cloud.replies["recipe"] = [{"slug": "mobile-app"}]
    assert recipe_guest.run("recipe", "add", "mobile-app", cwd=folder)[0] == 0
    assert (folder / ".eggie").stat().st_mode & 0o2070 == 0o2070
