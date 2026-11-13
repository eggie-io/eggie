from __future__ import annotations

from .env import Env, require_project


def cmd_actions(env: Env, project: str | None = None) -> None:
    project_id = project or require_project(env)
    found = env.client().actions(project_id)
    actions, problems = found.get("actions", []), found.get("problems", [])
    if not actions:
        print(f"No actions in {project_id}.", file=env.out)
    id_width = max((len(a["id"]) for a in actions), default=0)
    type_width = max((len(a["type"]) for a in actions), default=0)
    for a in actions:
        print(f"{a['id']:<{id_width}}  {a['type']:<{type_width}}  {a['name']}", file=env.out)
    for p in problems:
        print(f"! {p['message']}", file=env.out)
