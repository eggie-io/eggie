"""Recipe actions: what the service may publish as a button, and a project's recipes. No I/O."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass

ACTION_TYPES = ("one-off", "long-running")
MAX_ACTIONS = 20

SLUG = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")
MAX_SLUG = 63
MAX_NAME = 80
MAX_DESCRIPTION = 300
MAX_COMMAND = 4000


@dataclass(frozen=True)
class Action:
    id: str
    recipe: str
    name: str
    description: str | None
    command: str
    type: str

    def to_wire(self) -> dict:
        return asdict(self)


def is_slug(value: object) -> bool:
    return type(value) is str and len(value) <= MAX_SLUG and SLUG.fullmatch(value) is not None


def _one_line(value: str) -> bool:
    return "\n" not in value and "\r" not in value


def _check(recipe: str, raw: object) -> Action | str:
    """The action, or why it was refused."""
    if not isinstance(raw, dict):
        return "is not an object"
    aid, name = raw.get("id"), raw.get("name")
    description, command, kind = raw.get("description"), raw.get("command"), raw.get("type")
    if not is_slug(aid):
        return "has an invalid id"
    if type(name) is not str or not name or len(name) > MAX_NAME or not _one_line(name):
        return "has an invalid name"
    if description is not None and (
            type(description) is not str or len(description) > MAX_DESCRIPTION
            or not _one_line(description)):
        return "has an invalid description"
    if type(command) is not str or not command or len(command) > MAX_COMMAND or "\0" in command:
        return "has an invalid command"
    if kind not in ACTION_TYPES:
        return "has an unknown type"
    return Action(id=aid, recipe=recipe, name=name, description=description,
                  command=command, type=kind)


def parse_actions(recipe: str, payload: object) -> tuple[list[Action], list[str]]:
    """Valid actions from the service's body, and one message per entry dropped."""
    entries = payload.get("actions") if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        return [], [f"Recipe {recipe}: the service sent actions in a shape Eggie can't read."]
    actions: list[Action] = []
    problems: list[str] = []
    for index, raw in enumerate(entries[:MAX_ACTIONS], start=1):
        result = _check(recipe, raw)
        if isinstance(result, Action):
            actions.append(result)
        else:
            problems.append(f"Recipe {recipe}: action {index} {result} and was skipped.")
    if len(entries) > MAX_ACTIONS:
        problems.append(f"Recipe {recipe}: only the first {MAX_ACTIONS} actions are used.")
    return actions, problems


def merge(per_recipe: dict[str, list[Action]]) -> tuple[dict[str, Action], list[dict]]:
    """Actions by id; an id two recipes both define is dropped from both."""
    found: dict[str, list[Action]] = {}
    for actions in per_recipe.values():
        for action in actions:
            found.setdefault(action.id, []).append(action)
    merged: dict[str, Action] = {}
    problems: list[dict] = []
    for aid, owners in found.items():
        if len(owners) == 1:
            merged[aid] = owners[0]
            continue
        names = ", ".join(a.recipe for a in owners)
        problems.append({"recipe": None, "code": "duplicate_action",
                         "message": f"Action {aid} is defined by more than one recipe ({names}), "
                                    "so it can't be run."})
    return merged, problems


def recipes_of(project_yml: dict) -> tuple[str, ...]:
    raw = project_yml.get("recipes") if isinstance(project_yml, dict) else None
    if not isinstance(raw, list):
        return ()
    seen: dict[str, None] = {}
    for item in raw:
        if is_slug(item):
            seen.setdefault(item)
    return tuple(seen)
