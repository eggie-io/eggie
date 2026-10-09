from __future__ import annotations

import re

from .api import ApiClient
from .env import Env, require_project
from .errors import EggieError

_APPLY_HINT = "Run `eggie up` to apply it."


_SECRET_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_NO_ARGV_VALUE = ("The value can't go on the command line; run `eggie secret set "
                  "NAME` and paste it at the prompt, or pipe it in.")
_BAD_NAME = ("That isn't a valid secret name: use letters, digits and underscores, "
             "not starting with a digit.")


# Mirrors eggie_api's reserved names; tests hold the two equal.
RESERVED_PREFIXES = ("COMPOSE_", "DOCKER_", "LD_", "BUILDX_", "BUILDKIT_")
RESERVED_NAMES = frozenset({"PATH", "HOME"})


def _secret_project(env: Env, name: str) -> tuple[ApiClient, str]:
    # A mistyped `NAME=value` must fail here, before anything echoes or sends it.
    if "=" in name:
        raise EggieError(_NO_ARGV_VALUE)
    if not _SECRET_NAME.fullmatch(name):
        raise EggieError(_BAD_NAME)
    upper = name.upper()
    if upper in RESERVED_NAMES or upper.startswith(RESERVED_PREFIXES):
        raise EggieError("That name is reserved: Eggie and Docker read it themselves.")
    project_id = require_project(env)
    client = env.client()
    client.ensure_project(project_id)
    return client, project_id


def cmd_secret_set(env: Env, name: str, extra: list[str]) -> None:
    if extra:
        raise EggieError(_NO_ARGV_VALUE)
    client, project_id = _secret_project(env, name)
    # Never from argv: that ends up in shell history and `ps`.
    if env.stdin.isatty():
        value = env.getpass(f"Value for {name} (not shown): ")
    else:
        value = env.stdin.read().removesuffix("\n")
    if value == "":
        raise EggieError(f"No value given for {name}; nothing was saved.")
    client.set_secret(project_id, name, value)
    print(f"Saved {name} for {project_id}.", file=env.out)
    if client.secrets(project_id).get("restart_needed"):
        print(_APPLY_HINT, file=env.out)


def cmd_secret_request(env: Env, name: str, hint: str) -> None:
    client, project_id = _secret_project(env, name)
    if any(s["name"] == name for s in client.secrets(project_id).get("secrets", [])):
        print(f"{name} already has a value; ask the owner to Replace it on "
              f"{project_id}'s Secrets page in Eggie if it's wrong.", file=env.out)
        return
    client.request_secret(project_id, name, hint)
    print(f"Requested {name}. Ask the owner to fill it on {project_id}'s Secrets "
          "page in Eggie.", file=env.out)


def cmd_secret_list(env: Env) -> None:
    project_id = require_project(env)
    client = env.client()
    client.ensure_project(project_id)
    data = client.secrets(project_id)
    names = [s["name"] for s in data.get("secrets", [])]
    for name in names:
        print(name, file=env.out)
    requested = data.get("requested") or []
    if requested:
        print("Requested:", file=env.out)
        for r in requested:
            print(f"  {r['name']} — {r['hint']}", file=env.out)
    if not names and not requested:
        print(f"{project_id} has no secrets.", file=env.out)
    if data.get("restart_needed"):
        print("Run `eggie up` to apply the changes.", file=env.out)


def cmd_secret_rm(env: Env, name: str) -> None:
    client, project_id = _secret_project(env, name)
    had_value = any(s["name"] == name for s in client.secrets(project_id).get("secrets", []))
    client.delete_secret(project_id, name)
    if had_value:
        print(f"Removed {name} from {project_id}.", file=env.out)
    else:
        print(f"Dismissed the request for {name}.", file=env.out)
