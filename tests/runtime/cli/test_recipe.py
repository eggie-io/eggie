import hashlib

from eggie_api.infra.cloud import CloudError
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
