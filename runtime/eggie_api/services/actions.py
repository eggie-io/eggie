from __future__ import annotations

import logging
import os
import stat
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

from ..domain.actions import Action, is_slug, merge, parse_actions
from ..domain.connections import Connection
from ..errors import Conflict, EggieError, Invalid, NotFound, Unavailable, Upstream
from ..infra.actions_helper import HelperError, HelperUnavailable, Terminal, project_target
from .loader import ProjectLoader
from .prompting import parse_answer
from .recipes import RecipeService

if TYPE_CHECKING:
    from .connections import ConnectionService

log = logging.getLogger("eggie.api")

CACHE_SECONDS = 300
DEFAULT_TAIL = 64 * 1024
MAX_TAIL = 1024 * 1024
HELPER_DOWN = ("Eggie can't reach the part of this machine that runs actions. "
                "Restart the machine, or run setup again if it keeps happening.")


class ActionService:
    """Recipe actions, resolved from the Eggie service only and run by the
    root `eggie-actions` helper. Nothing in a project folder defines one."""

    def __init__(self, loader: ProjectLoader, recipes: RecipeService, helper, logs_dir: Path,
                 connections: ConnectionService, *, clock=time.monotonic):
        self._loader = loader
        self._recipes = recipes
        self._helper = helper
        self._connections = connections
        self._logs = Path(logs_dir)
        self._clock = clock
        self._cache: dict[str, tuple[float, list[Action], list[str]]] = {}
        self._cache_lock = threading.Lock()

    def _recipe(self, slug: str) -> tuple[list[Action], list[str]]:
        now = self._clock()
        with self._cache_lock:
            hit = self._cache.get(slug)
        if hit is not None and now - hit[0] < CACHE_SECONDS:
            return hit[1], hit[2]
        actions, dropped = parse_actions(slug, self._recipes.actions(slug))
        with self._cache_lock:
            self._cache[slug] = (now, actions, dropped)
        return actions, dropped

    def _resolve(self, project_id: str):
        """Runnable actions by id, problems for the list, the service errors behind them,
        and the connections catalog (empty unless some action uses connections)."""
        per_recipe: dict[str, list[Action]] = {}
        problems: list[dict] = []
        failures: list[EggieError] = []
        try:
            slugs = self._loader.recipes(project_id)
        except Invalid as e:
            return {}, [{"recipe": None, "code": e.code, "message": e.message}], [e], {}
        for slug in slugs:
            try:
                actions, dropped = self._recipe(slug)
            except (NotFound, Upstream, Unavailable) as e:
                failures.append(e)
                problems.append({"recipe": slug, "code": e.code, "message": e.message})
                continue
            per_recipe[slug] = actions
            problems += [{"recipe": slug, "code": "invalid_action", "message": m}
                         for m in dropped]
        merged, duplicates = merge(per_recipe)
        problems += duplicates
        catalog: dict[str, Connection] = {}
        rejected: set[str] = set()
        if any(a.connections for a in merged.values()):
            try:
                catalog, rejected = self._connections.catalog_and_rejected()
            except (Upstream, Unavailable) as e:
                failures.append(e)
                for recipe in dict.fromkeys(a.recipe for a in merged.values() if a.connections):
                    problems.append({"recipe": recipe, "code": e.code, "message": e.message})
                return ({aid: a for aid, a in merged.items() if not a.connections},
                        problems, failures, catalog)
        runnable: dict[str, Action] = {}
        for aid, action in merged.items():
            missing = [c for c in action.connections if c not in catalog]
            invalid = [c for c in missing if c in rejected]
            if invalid:
                problems.append({"recipe": action.recipe, "code": "invalid_connection",
                                 "action": aid,
                                 "message": f"Action {aid} can't be run: the Eggie service sent "
                                            f"a definition of {', '.join(invalid)} that Eggie "
                                            f"can't use."})
            elif missing:
                problems.append({"recipe": action.recipe, "code": "unknown_connection",
                                 "action": aid,
                                 "message": f"Action {aid} uses a connection Eggie doesn't know "
                                            f"({', '.join(missing)}), so it can't be run."})
            else:
                runnable[aid] = action
        return runnable, problems, failures, catalog

    def _runnable(self, project_id: str, action_id: str) -> tuple[Action, dict[str, Connection]]:
        actions, _, failures, catalog = self._resolve(project_id)
        action = actions.get(action_id)
        if action is None:
            # The id may well exist; the service just could not say so.
            for e in failures:
                if not isinstance(e, NotFound):
                    raise e
            raise self._not_found(action_id)
        return action, catalog

    @staticmethod
    def _sources(action: Action, catalog: dict[str, Connection]) -> list:
        # A connection with a login is answered in its own session; outside it a generic
        # question of its own ("Do you want to continue?") would answer the wrong prompt.
        return [("action", action.questions),
                *((cid, catalog[cid].questions) for cid in action.connections
                  if catalog[cid].login is None)]

    def _call(self, fn):
        try:
            return fn()
        except HelperUnavailable:
            raise Unavailable("actions_unavailable", HELPER_DOWN)
        except HelperError as e:
            if e.code == "action_running":
                raise Conflict("action_running", "This action is already running.")
            if e.code == "not_running":
                raise Conflict("action_not_running", "This action has not been started.")
            raise Upstream("actions_failed", e.message)

    def _action_id(self, project_id: str, action_id: str) -> None:
        self._loader.require(project_id)
        if not is_slug(action_id):
            raise self._not_found(action_id)

    @staticmethod
    def _not_found(action_id: str) -> NotFound:
        return NotFound("action_not_found", f"This project has no action '{action_id}'.")

    def list(self, project_id: str) -> dict:
        actions, problems, _, catalog = self._resolve(project_id)
        runs: dict[str, dict] = {}
        if actions:
            try:
                runs = self._helper.status(project_id)
            except HelperUnavailable:
                problems.append({"recipe": None, "code": "actions_unavailable",
                                 "message": HELPER_DOWN})
            except HelperError as e:
                problems.append({"recipe": None, "code": "actions_failed", "message": e.message})
        used = dict.fromkeys(c for a in actions.values() for c in a.connections)
        return {"actions": [{**a.to_wire(), "run": runs.get(aid),
                             "asks": any(q for _, q in self._sources(a, catalog))}
                            for aid, a in actions.items()],
                "connections": [catalog[cid].to_wire() for cid in used],
                "problems": problems}

    def start(self, project_id: str, action_id: str) -> dict:
        action, _ = self._runnable(project_id, action_id)
        target = project_target(project_id, action.id)
        run = self._call(lambda: self._helper.start(
            target, action.command, log=action.type == "long-running"))
        self._connections.prompter.forget(target)
        return {"run": run}

    def run(self, project_id: str, action_id: str) -> dict:
        action, catalog = self._runnable(project_id, action_id)
        target = project_target(project_id, action.id)
        run = self._call(lambda: self._helper.run(target))
        asking = self._call(lambda: self._connections.prompter.read(
            target, run, self._sources(action, catalog)))
        return {"run": run, "asking": asking}

    def answer(self, project_id: str, action_id: str, body: object) -> dict:
        action, catalog = self._runnable(project_id, action_id)
        parse_answer(body)
        target = project_target(project_id, action.id)
        run = self._call(lambda: self._helper.run(target))
        self._call(lambda: self._connections.prompter.answer(
            target, run, self._sources(action, catalog), body))
        return {}

    def stop(self, project_id: str, action_id: str) -> dict:
        self._action_id(project_id, action_id)
        return {"run": self._call(lambda: self._helper.stop(project_target(project_id, action_id)))}

    def attach(self, project_id: str, action_id: str, cols: int, rows: int) -> Terminal:
        self._action_id(project_id, action_id)
        return self._call(lambda: self._helper.attach(project_target(project_id, action_id), cols, rows))

    def output(self, project_id: str, action_id: str) -> str:
        self._action_id(project_id, action_id)
        return self._call(lambda: self._helper.output(project_target(project_id, action_id)))

    def log(self, project_id: str, action_id: str, tail: int = DEFAULT_TAIL) -> str:
        self._action_id(project_id, action_id)
        tail = max(0, min(tail, MAX_TAIL))
        try:
            # logs/ is group-writable: never follow a link planted there.
            dir_fd = os.open(self._logs / project_id, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                # Non-blocking: a FIFO planted there would otherwise hang the open.
                fd = os.open(f"{action_id}.log", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                             dir_fd=dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            return ""
        with os.fdopen(fd, "rb") as f:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                return ""
            size = info.st_size
            f.seek(max(0, size - tail))
            return f.read(tail).decode("utf-8", errors="replace")

    def stop_project(self, project_id: str) -> None:
        try:
            self._helper.stop_project(project_id)
        except (HelperUnavailable, HelperError) as e:
            log.warning("could not stop the actions of %s: %s", project_id, e)
