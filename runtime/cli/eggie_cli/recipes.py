from __future__ import annotations

from .env import Env, project_here
from .errors import EggieError
from .project import prepare_overlay_dir


def cmd_recipe_list(env: Env) -> None:
    recipes = env.client().recipes()
    if not recipes:
        print("No recipes are published yet.", file=env.out)
        return
    width = max(len(r["slug"]) for r in recipes)
    for r in recipes:
        print(f"{r['slug']:<{width}}  {r['summary']}", file=env.out)


def cmd_recipe_get(env: Env, slug: str) -> None:
    recipe = env.client().get_recipe(slug)
    print(f"Recipe {recipe['slug']} (version {recipe['version']}) is in {recipe['path']}",
          file=env.out)
    print("Its RECIPE.md follows. Files it mentions are in that folder.\n", file=env.out)
    print(recipe["instructions"], end="", file=env.out)


def cmd_recipe_add(env: Env, slug: str, project: str | None = None) -> None:
    if project:
        folder, project_id = env.root / project, project
    else:
        found = project_here(env, None)
        if found is None:
            raise EggieError("Run this inside a project folder in "
                             f"~/projects ({env.root}), or pass --project.")
        folder, project_id = found
    if folder.is_dir():
        prepare_overlay_dir(folder, env.gid(), "project.yml")
    env.client().add_recipe(project_id, slug)
    print(f"{slug} added to {project_id}.", file=env.out)
