from __future__ import annotations

import threading
import time

from ..domain.connections import Connection, is_slug, parse_connections
from ..domain.prompts import find_code, find_link
from ..errors import BadRequest, Conflict, NotFound, Unavailable, Upstream
from ..infra.actions_helper import HelperError, HelperUnavailable, Terminal, connection_target
from .actions import HELPER_DOWN
from .prompting import Prompter
from .recipes import RecipeService

CATALOG_SECONDS = 300
STATE_SECONDS = 120
LOGIN_DEADLINE = 900


def _daemon(fn) -> None:
    threading.Thread(target=fn, name="eggie-connection-check", daemon=True).start()


class ConnectionService:
    """Sign-ins from the Eggie service's catalog, run in the helper's `+<id>` sessions."""

    def __init__(self, recipes: RecipeService, helper, *, clock=time.monotonic, spawn=None):
        self._recipes = recipes
        self._helper = helper
        self._clock = clock
        self._spawn = spawn or _daemon
        self.prompter = Prompter(helper)
        self._lock = threading.Lock()
        self._catalog: tuple[float, dict[str, Connection], list[str]] | None = None
        self._states: dict[str, tuple[float, str]] = {}
        self._checking: set[str] = set()
        # Bumped whenever the cached state is dropped, so a check already in flight can't refill it.
        self._generation: dict[str, int] = {}
        self._started: dict[str, object] = {}
        self._ended: dict[str, object] = {}
        self._logged_in: set[str] = set()

    def catalog(self) -> dict[str, Connection]:
        return self._fetch()[0]

    def catalog_problems(self) -> list[str]:
        return list(self._fetch()[1])

    def _fetch(self) -> tuple[dict[str, Connection], list[str]]:
        now = self._clock()
        with self._lock:
            hit = self._catalog
        if hit is not None and now - hit[0] < CATALOG_SECONDS:
            return hit[1], hit[2]
        found, problems = parse_connections(self._recipes.connections())
        with self._lock:
            self._catalog = (now, found, problems)
        return found, problems

    def _connection(self, cid: str) -> Connection:
        found = self.catalog().get(cid) if is_slug(cid) else None
        if found is None:
            raise NotFound("connection_not_found", f"Eggie has no connection '{cid}'.")
        return found

    @staticmethod
    def _call(fn):
        try:
            return fn()
        except HelperUnavailable:
            raise Unavailable("actions_unavailable", HELPER_DOWN)
        except HelperError as e:
            if e.code == "action_running":
                raise Conflict("connection_running", "This sign-in is already running.")
            if e.code == "not_running":
                raise Conflict("connection_not_running", "This sign-in has not been started.")
            raise Upstream("actions_failed", e.message)

    def _drop(self, cid: str) -> None:
        with self._lock:
            self._states.pop(cid, None)
            self._generation[cid] = self._generation.get(cid, 0) + 1

    def _account_for(self, cid: str, run: dict | None) -> None:
        if not isinstance(run, dict) or run.get("state") != "exited":
            return
        started = run.get("started_at")
        with self._lock:
            if cid in self._ended and self._ended[cid] == started:
                return
            self._ended[cid] = started
            if run.get("exit_code") == 0 and self._started.get(cid) == started:
                self._logged_in.add(cid)
        self._drop(cid)

    def state(self, conn: Connection) -> str:
        if conn.check is None:
            return "connected" if conn.id in self._logged_in else "unknown"
        with self._lock:
            hit = self._states.get(conn.id)
            if hit is not None and self._clock() - hit[0] < STATE_SECONDS:
                return hit[1]
            if conn.id in self._checking:
                return "checking"
            self._checking.add(conn.id)
            generation = self._generation.get(conn.id, 0)
        self._spawn(lambda: self._check(conn, generation))
        with self._lock:
            hit = self._states.get(conn.id)
        return hit[1] if hit is not None else "checking"

    def _check(self, conn: Connection, generation: int) -> None:
        state = "unknown"
        try:
            code = self._helper.check(conn.check)
            if code is not None:
                state = "connected" if code == 0 else "signed-out"
        except (HelperError, HelperUnavailable):
            pass
        finally:
            with self._lock:
                self._checking.discard(conn.id)
                if self._generation.get(conn.id, 0) == generation:
                    self._states[conn.id] = (self._clock(), state)

    def get(self, cid: str) -> dict:
        conn = self._connection(cid)
        target = connection_target(cid)
        run = self._call(lambda: self._helper.run(target))
        self._account_for(cid, run)
        if isinstance(run, dict):
            screen, question = self._call(
                lambda: self.prompter.look(target, run, [(cid, conn.questions)]))
            link = code = None
            if screen is not None and conn.login is not None:
                link = find_link(screen.text, conn.login.link_after)
                code = find_code(screen.text, conn.login.code_after)
            run = {**run, "link": link, "code": code, "asking": question}
        return {"id": conn.id, "name": conn.name, "state": self.state(conn), "run": run}

    def login(self, cid: str) -> dict:
        conn = self._connection(cid)
        if conn.login is None:
            raise BadRequest("no_login", f"{conn.name} has no sign-in to start.")
        run = self._call(lambda: self._helper.start(
            connection_target(cid), conn.login.command, log=False, deadline=LOGIN_DEADLINE))
        with self._lock:
            self._started[cid] = run.get("started_at") if isinstance(run, dict) else None
            self._ended.pop(cid, None)
        self._drop(cid)
        return {"run": run}

    def cancel(self, cid: str) -> dict:
        self._connection(cid)
        return {"run": self._call(lambda: self._helper.stop(connection_target(cid)))}

    def answer(self, cid: str, body: object) -> dict:
        conn = self._connection(cid)
        target = connection_target(cid)
        run = self._call(lambda: self._helper.run(target))
        self._call(lambda: self.prompter.answer(target, run, [(cid, conn.questions)], body))
        return {}

    def attach(self, cid: str, cols: int, rows: int) -> Terminal:
        self._connection(cid)
        return self._call(lambda: self._helper.attach(connection_target(cid), cols, rows))
