# Web UI agent prerequisites — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the agent everything the browser UI at `localhost:39080` needs: a cookie-authenticated `/api` mount with a Host/Origin allowlist and desktop handoff, project reconcile/adopt, richer project payloads with job phases, delete that survives a broken project, free-space reporting and a resumable chunked upload API.

**Architecture:** Every route moves from `@app` onto one `APIRouter` that `create_app` includes twice: at `/` (bearer token, unchanged for host and CLI) and at `/api` (session cookie + allowlist, for the browser). One HTTP middleware picks the auth mode by path prefix. New logic lives in small platform-free modules under `agent/core/` (`sessions.py`, `reconcile.py`, `disk.py`, `uploads.py`); `agent/api/app.py` only wires them to routes.

**Tech Stack:** Python 3.12, FastAPI/Starlette, sqlite3, pytest + `fastapi.testclient`. Host side: stdlib `urllib` only.

**Spec:** `docs/superpowers/specs/2026-09-21-web-ui-agent-prerequisites-design.md`

## Global Constraints

- `host/` never imports `agent/` and vice versa (`tests/host/test_no_agent_import.py`, `tests/agent/test_no_host_import.py`).
- No `sys.platform` / `platform.system()` / `os.name` outside `host/providers/` (`tests/test_no_platform_leak.py`).
- Every non-2xx body is `{"error": {"code": ..., "message": ...}}`; extra fields may sit inside `error`.
- `API_VERSION` stays `1`; every change adds a route or a field. `DELETE /projects/{id}` keeps its synchronous `{id, stopped, detail}` response.
- Cookie: name `omelet_session`, `HttpOnly`, `SameSite=Strict`, `Path=/api`, no `Secure`. Session lifetime 7 days, sliding. Handoff code lifetime 60 s, single use, memory only.
- Allowed hosts: `localhost:<edge_port>`, `127.0.0.1:<edge_port>`; allowed origins: `http://` + each.
- Upload chunk size 8 MiB; free-space reserve 1 GiB; abandoned uploads swept after 7 days; staging root `/opt/omelet/uploads`.
- Python conventions: `from __future__ import annotations`, frozen dataclasses for value types, comments only for non-obvious edge cases.
- Tests follow the user's rules: each test names the bug it catches; no tests of framework behaviour.
- Run tests with `TMPDIR=$PWD/.tmp python3 -m pytest -q` (the sandbox's `/tmp/pytest-of-$USER` is root-owned). Create `.tmp` once: `mkdir -p .tmp`.

## File map

| File | Responsibility |
|---|---|
| `agent/api/app.py` (modify) | Router split, auth middleware, all route wiring |
| `agent/api/jobs.py` (modify) | Job `kind`, `project_id`, `phase`; `active_for()` |
| `agent/core/sessions.py` (create) | Handoff codes, session issue/check/end |
| `agent/core/migrate.py` (modify) | v3: `sessions` table, `last_started_at`, `compose_name` |
| `agent/core/state.py` (modify) | Session rows; `mark_started()` |
| `agent/core/reconcile.py` (create) | Scan `projects_root` for unregistered folders |
| `agent/core/lifecycle.py` (modify) | Label-based removal, resource listing, root rm fallback |
| `agent/core/disk.py` (create) | `statvfs` → free/total bytes |
| `agent/core/uploads.py` (create) | Resumable upload staging store |
| `agent/core/files.py` (modify) | One-level directory listing, tree stats |
| `agent/core/config.py` (modify) | `uploads_root` |
| `host/client.py` (modify) | `handoff_code()` |
| `host/desktop/api.py` (modify) | `open_omelet` uses the handoff |
| `engine/stack.yml` (modify) | Agent Traefik labels for `/api`; image tag |
| `agent/__init__.py`, `agent/Dockerfile` (modify) | Version 0.2.0 |
| `tests/agent/test_api_browser_auth.py` (create) | Allowlist and mount separation |
| `tests/agent/test_sessions.py` (create) | Handoff and session lifecycle |
| `tests/agent/test_reconcile.py` (create) | Discovery and adopt |
| `tests/agent/test_api_projects_ui.py` (create) | Payload fields, phases, restart, delete |
| `tests/agent/test_disk.py` (create) | Free-space arithmetic |
| `tests/agent/test_uploads.py` (create) | Upload store |
| `tests/agent/test_api_uploads.py` (create) | Upload routes |
| `tests/agent/test_files.py` (modify) | `list_dir` |
| `tests/host/desktop/test_api_home.py` (modify) | Handoff URL |

Shared test helper: several new API test files need the same fixture as `tests/agent/test_api_routes.py`. Task 1 moves `FakeRunner`, `FakeProbe`, `AUTH`, the `env` fixture, `_create`, `_write_compose` and `_run_to_completion` into `tests/agent/conftest.py` so later files use them without copying.

---

### Task 1: One router, two mounts, and the browser allowlist

**Files:**
- Modify: `agent/api/app.py`
- Create: `tests/agent/conftest.py` (moved helpers)
- Modify: `tests/agent/test_api_routes.py` (drop moved helpers, import from conftest)
- Create: `tests/agent/test_api_browser_auth.py`

**Interfaces:**
- Produces: every existing route also answers under `/api/...`. `create_app` returns an app whose middleware:
  - `/api/*`: 403 `forbidden_host` when `Host` is not allowed; 403 `forbidden_origin` when method is not GET/HEAD and `Origin` is not allowed; `/api/health` and `/api/session` skip the session check; everything else 401 `not_signed_in` (Task 2 replaces this with a real check).
  - everything else: today's bearer check, unchanged.
- Produces for tests: `tests/agent/conftest.py` fixture `env` (same fields as today: `client, config, runner, probe, state, jobs, raw_client`) plus a new `app` field, and helpers `_create`, `_write_compose`, `_run_to_completion`, constants `AUTH`, `COMPOSE_ONE_WEB`, `COMPOSE_MALFORMED`, `COMPOSE_AMBIGUOUS`, `PS_RUNNING`, `PS_RESTARTING`, `PROC_NET_LOOPBACK`, classes `FakeRunner`, `FakeProbe`. `BROWSER = {"Host": "localhost:41080"}` (the fixture's edge port is 41080).

- [ ] **Step 1: Move shared test helpers into `tests/agent/conftest.py`**

Cut everything in `tests/agent/test_api_routes.py` from `PS_RUNNING = ...` down to and including `_run_to_completion` and paste it into a new `tests/agent/conftest.py` with the same imports (`threading`, `SimpleNamespace`, `pytest`, `TestClient`, `create_app`, `AgentConfig`, `Completed`, `State`). Add `app=app` to the `SimpleNamespace` the `env` fixture yields, and add at module level:

```python
BROWSER = {"Host": "localhost:41080"}
```

At the top of `tests/agent/test_api_routes.py` add:

```python
from tests.agent.conftest import (AUTH, COMPOSE_AMBIGUOUS, COMPOSE_MALFORMED,
                                  COMPOSE_ONE_WEB, PROC_NET_LOOPBACK,
                                  PS_RESTARTING, PS_RUNNING, FakeProbe,
                                  FakeRunner, _create, _run_to_completion,
                                  _write_compose)
```

Keep only the names the file actually uses (Python will not complain about unused ones, but keep it tidy).

- [ ] **Step 2: Run the existing suite to prove the move changed nothing**

Run: `TMPDIR=$PWD/.tmp python3 -m pytest tests/agent -q`
Expected: same pass count as before the move.

- [ ] **Step 3: Write the failing allowlist tests**

Create `tests/agent/test_api_browser_auth.py`:

```python
from fastapi.testclient import TestClient

from tests.agent.conftest import BROWSER


def _browser(env, **headers):
    # A browser never sends the bearer token; start from a bare client.
    return TestClient(env.app, headers={**BROWSER, **headers})


def test_the_api_mount_refuses_the_agent_port_host(env):
    # Cookies ignore the port: without this check a page could replay the
    # session cookie straight at localhost:39099.
    resp = _browser(env, Host="localhost:39099").get("/api/projects")
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "forbidden_host"


def test_the_api_mount_refuses_a_rebound_hostname(env):
    resp = _browser(env, Host="evil.example:41080").get("/api/health")
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "forbidden_host"


def test_a_write_from_a_project_app_origin_is_refused(env):
    resp = _browser(env, Origin="http://blog.test.local:41080").post(
        "/api/projects", json={"id": "x"})
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "forbidden_origin"


def test_a_write_without_an_origin_is_refused(env):
    resp = _browser(env).post("/api/projects", json={"id": "x"})
    assert resp.json()["error"]["code"] == "forbidden_origin"


def test_the_api_mount_does_not_accept_the_bearer_token(env):
    resp = _browser(env, Authorization="Bearer test-token").get("/api/projects")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "not_signed_in"


def test_api_health_answers_without_a_session_for_the_version_check(env):
    resp = _browser(env).get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["api"] == 1


def test_the_bearer_mount_is_unchanged_by_a_browser_host(env):
    # The host dials 127.0.0.1:39099 with whatever Host urllib sends; the
    # allowlist must never apply to the bearer mount.
    resp = env.client.get("/projects", headers={"Host": "anything:1"})
    assert resp.status_code == 200
```

- [ ] **Step 4: Run to verify they fail**

Run: `TMPDIR=$PWD/.tmp python3 -m pytest tests/agent/test_api_browser_auth.py -q`
Expected: FAIL — `/api/...` answers 404 `not_found`.

- [ ] **Step 5: Move routes onto a router and add the two-mode middleware**

In `agent/api/app.py`:

1. Import: `from fastapi import APIRouter, FastAPI, Request`.
2. Right after `app.state.jobs = jobs`, add `router = APIRouter()`.
3. Replace every route decorator `@app.get(` / `@app.post(` / `@app.put(` / `@app.delete(` with `@router.get(` / etc. Exception handlers and the middleware stay on `app`.
4. Replace `_require_bearer_token` with the block below.
5. At the very end of `create_app`, before `return app`:

```python
    app.include_router(router)
    app.include_router(router, prefix="/api")
```

Middleware block (replaces the existing `@app.middleware("http")` function; keep the comment above `token = _read_token(...)`):

```python
    allowed_hosts = {f"localhost:{config.edge_port}",
                     f"127.0.0.1:{config.edge_port}"}
    allowed_origins = {f"http://{host}" for host in allowed_hosts}
    # Reachable before sign-in: the page checks the API version, and trades
    # a handoff code for a cookie.
    open_browser_paths = {"/api/health", "/api/session"}

    def _bearer_ok(request: Request) -> bool:
        scheme, _, supplied = request.headers.get("authorization", "").partition(" ")
        # Starlette decodes headers as latin-1, so a header value can carry
        # bytes that are not valid ASCII; compare_digest raises TypeError on
        # two `str` args if either has a non-ASCII character. Comparing the
        # encoded bytes instead means every wire-valid header reaches a
        # normal true/false answer, never an exception out of the one guard
        # that must never throw.
        return (scheme.lower() == "bearer"
                and secrets.compare_digest(supplied.encode(), token.encode()))

    def _browser_refusal(request: Request) -> JSONResponse | None:
        if request.headers.get("host", "") not in allowed_hosts:
            return _body("forbidden_host",
                         "this address is not where Omelet's page lives", 403)
        if (request.method not in ("GET", "HEAD")
                and request.headers.get("origin", "") not in allowed_origins):
            return _body("forbidden_origin",
                         "requests that change something must come from "
                         "Omelet's own page", 403)
        return None

    @app.middleware("http")
    async def _authenticate(request: Request, call_next):
        path = request.url.path
        if path == "/api" or path.startswith("/api/"):
            refusal = _browser_refusal(request)
            if refusal is not None:
                return refusal
            if path in open_browser_paths:
                return await call_next(request)
            return _body("not_signed_in", "open Omelet from the desktop app "
                         "to sign in", 401)
        if path == "/health":
            return await call_next(request)
        if not token:
            return _body("agent_unconfigured",
                         "the agent has no token configured; run setup again", 503)
        if not _bearer_ok(request):
            return _body("unauthorized", "missing or invalid bearer token", 401)
        return await call_next(request)
```

- [ ] **Step 6: Run the new and existing agent tests**

Run: `TMPDIR=$PWD/.tmp python3 -m pytest tests/agent -q`
Expected: PASS, including `test_api_auth.py` unchanged.

- [ ] **Step 7: Commit**

```bash
git add agent/api/app.py tests/agent/conftest.py tests/agent/test_api_routes.py tests/agent/test_api_browser_auth.py
git commit -m "Mount the agent API a second time under /api behind a host and origin allowlist"
```

---

### Task 2: Handoff codes and sessions

**Files:**
- Create: `agent/core/sessions.py`
- Modify: `agent/core/migrate.py`, `agent/core/state.py`, `agent/api/app.py`
- Create: `tests/agent/test_sessions.py`
- Modify: `tests/agent/test_migrate.py` (only if it asserts `SCHEMA_VERSION == 2`; change to `len(MIGRATIONS)`)

**Interfaces:**
- Consumes: Task 1's middleware and `open_browser_paths`.
- Produces:
  - `agent.core.sessions.Sessions(state, *, clock=time.time)` with `issue_handoff() -> str`, `redeem(code: str) -> str | None` (new session id), `check(session_id: str | None) -> str` returning `"ok" | "expired" | "missing"` (slides expiry on `"ok"`), `end(session_id: str | None) -> None`. Constants `COOKIE = "omelet_session"`, `SESSION_TTL = 7 * 24 * 3600`, `HANDOFF_TTL = 60`.
  - `State.add_session(id_hash, expires_at)`, `State.get_session(id_hash) -> dict | None`, `State.set_session_expiry(id_hash, expires_at)`, `State.remove_session(id_hash)`.
  - Migration v3 also adds `projects.last_started_at REAL` and `projects.compose_name TEXT` (used in Task 5).
  - Routes: `POST /sessions/handoff` (bearer only) → `{"code", "expires_in"}`; `POST /api/session` `{code}` → 200 `{"signed_in": true}` + cookie, or 401 `handoff_invalid`; `GET /api/session` → 200 `{"signed_in": true}` or 401 `not_signed_in` / `session_expired`; `DELETE /api/session` → 200 `{"signed_in": false}` and clears the cookie.
  - `app.state.sessions` holds the `Sessions` instance.

- [ ] **Step 1: Write the failing tests**

Create `tests/agent/test_sessions.py`:

```python
from fastapi.testclient import TestClient

from agent.api.app import create_app
from agent.core.sessions import COOKIE, SESSION_TTL, Sessions
from agent.core.state import State
from tests.agent.conftest import AUTH, BROWSER

ORIGIN = {"Origin": "http://localhost:41080"}


class Clock:
    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self):
        return self.now


def _signed_in(env) -> TestClient:
    code = env.client.post("/sessions/handoff").json()["code"]
    browser = TestClient(env.app, headers={**BROWSER, **ORIGIN})
    assert browser.post("/api/session", json={"code": code}).status_code == 200
    return browser


def test_a_handoff_code_signs_the_browser_in(env):
    browser = _signed_in(env)
    assert browser.get("/api/projects").status_code == 200


def test_a_handoff_code_works_only_once(env):
    code = env.client.post("/sessions/handoff").json()["code"]
    browser = TestClient(env.app, headers={**BROWSER, **ORIGIN})
    browser.post("/api/session", json={"code": code})
    again = TestClient(env.app, headers={**BROWSER, **ORIGIN}).post(
        "/api/session", json={"code": code})
    assert again.status_code == 401
    assert again.json()["error"]["code"] == "handoff_invalid"


def test_handoff_is_not_reachable_from_the_browser_mount(env):
    resp = TestClient(env.app, headers={**BROWSER, **ORIGIN}).post(
        "/api/sessions/handoff")
    assert resp.status_code == 401


def test_the_cookie_is_scoped_to_the_api_path_and_script_proof(env):
    code = env.client.post("/sessions/handoff").json()["code"]
    resp = TestClient(env.app, headers={**BROWSER, **ORIGIN}).post(
        "/api/session", json={"code": code})
    header = resp.headers["set-cookie"].lower()
    assert "httponly" in header and "samesite=strict" in header
    assert "path=/api" in header


def test_the_bearer_mount_ignores_a_valid_session_cookie(env):
    browser = _signed_in(env)
    bare = TestClient(env.app, cookies={COOKIE: browser.cookies[COOKIE]})
    assert bare.get("/projects").status_code == 401


def test_an_expired_code_is_refused(tmp_path):
    clock = Clock()
    sessions = Sessions(State(tmp_path / "s.db"), clock=clock)
    code = sessions.issue_handoff()
    clock.now += 61
    assert sessions.redeem(code) is None


def test_an_expired_session_reads_as_expired_not_missing(tmp_path):
    # The UI shows "we've lost track of you" for both, but only an expired one
    # may say "that happens after a while".
    clock = Clock()
    sessions = Sessions(State(tmp_path / "s.db"), clock=clock)
    sid = sessions.redeem(sessions.issue_handoff())
    clock.now += SESSION_TTL + 1
    assert sessions.check(sid) == "expired"
    assert sessions.check(sid) == "missing"


def test_use_slides_the_expiry_forward(tmp_path):
    clock = Clock()
    sessions = Sessions(State(tmp_path / "s.db"), clock=clock)
    sid = sessions.redeem(sessions.issue_handoff())
    clock.now += SESSION_TTL - 10
    assert sessions.check(sid) == "ok"
    clock.now += SESSION_TTL - 10
    assert sessions.check(sid) == "ok"


def test_a_session_survives_an_agent_restart(env):
    browser = _signed_in(env)
    cookie = browser.cookies[COOKIE]
    env.state.close()
    restarted = create_app(config=env.config, runner=env.runner,
                           http_probe=env.probe)
    again = TestClient(restarted, headers=BROWSER, cookies={COOKIE: cookie})
    assert again.get("/api/session").status_code == 200


def test_the_raw_session_id_is_never_stored(tmp_path):
    state = State(tmp_path / "s.db")
    sessions = Sessions(state)
    sid = sessions.redeem(sessions.issue_handoff())
    assert state.get_session(sid) is None


def test_signing_out_ends_the_session(env):
    browser = _signed_in(env)
    assert browser.delete("/api/session").status_code == 200
    resp = browser.get("/api/projects")
    assert resp.json()["error"]["code"] == "not_signed_in"
```

- [ ] **Step 2: Run to verify they fail**

Run: `TMPDIR=$PWD/.tmp python3 -m pytest tests/agent/test_sessions.py -q`
Expected: FAIL — `ModuleNotFoundError: agent.core.sessions`.

- [ ] **Step 3: Add migration v3**

In `agent/core/migrate.py`, before `MIGRATIONS`:

```python
def _v3_web_ui(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id_hash TEXT PRIMARY KEY,
            expires_at REAL NOT NULL
        )""")
    _add_column(conn, "projects", "last_started_at", "REAL")
    _add_column(conn, "projects", "compose_name", "TEXT")
```

and append `_v3_web_ui` to `MIGRATIONS`.

- [ ] **Step 4: Add session rows to `State`**

In `agent/core/state.py`, before `close`:

```python
    def add_session(self, id_hash, expires_at):
        with self._lock:
            self._conn.execute(
                "INSERT INTO sessions(id_hash, expires_at) VALUES (?,?)",
                (id_hash, expires_at))
            self._conn.commit()

    def get_session(self, id_hash):
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM sessions WHERE id_hash=?", (id_hash,)).fetchone()
        return dict(row) if row else None

    def set_session_expiry(self, id_hash, expires_at):
        with self._lock:
            self._conn.execute(
                "UPDATE sessions SET expires_at=? WHERE id_hash=?",
                (expires_at, id_hash))
            self._conn.commit()

    def remove_session(self, id_hash):
        with self._lock:
            self._conn.execute("DELETE FROM sessions WHERE id_hash=?", (id_hash,))
            self._conn.commit()
```

- [ ] **Step 5: Write `agent/core/sessions.py`**

```python
from __future__ import annotations

import hashlib
import secrets
import threading
import time

COOKIE = "omelet_session"
SESSION_TTL = 7 * 24 * 3600
HANDOFF_TTL = 60


def _hash(session_id: str) -> str:
    return hashlib.sha256(session_id.encode()).hexdigest()


class Sessions:
    """Browser sign-in. Handoff codes live in memory only: one that outlives an
    agent restart would be a bearer credential sitting in the database."""

    def __init__(self, state, *, clock=time.time):
        self._state = state
        self._clock = clock
        self._codes: dict[str, float] = {}
        self._lock = threading.Lock()

    def issue_handoff(self) -> str:
        code = secrets.token_urlsafe(32)
        now = self._clock()
        with self._lock:
            self._codes = {c: t for c, t in self._codes.items() if t > now}
            self._codes[code] = now + HANDOFF_TTL
        return code

    def redeem(self, code: str) -> str | None:
        with self._lock:
            expires = self._codes.pop(code, None)
        if expires is None or expires <= self._clock():
            return None
        session_id = secrets.token_urlsafe(32)
        self._state.add_session(_hash(session_id), self._clock() + SESSION_TTL)
        return session_id

    def check(self, session_id: str | None) -> str:
        if not session_id:
            return "missing"
        key = _hash(session_id)
        row = self._state.get_session(key)
        if row is None:
            return "missing"
        now = self._clock()
        if row["expires_at"] <= now:
            self._state.remove_session(key)
            return "expired"
        self._state.set_session_expiry(key, now + SESSION_TTL)
        return "ok"

    def end(self, session_id: str | None) -> None:
        if session_id:
            self._state.remove_session(_hash(session_id))
```

- [ ] **Step 6: Wire sessions into `create_app`**

In `agent/api/app.py`:

1. Import: `from ..core.sessions import COOKIE, SESSION_TTL, Sessions` and `from fastapi.responses import Response` (add to the existing `fastapi.responses` import).
2. After `locks = ProjectLocks()`: `sessions = Sessions(state)`; after `app.state.jobs = jobs`: `app.state.sessions = sessions`.
3. In `_authenticate`, replace the `not_signed_in` return with:

```python
            verdict = sessions.check(request.cookies.get(COOKIE))
            if verdict == "ok":
                return await call_next(request)
            if verdict == "expired":
                return _body("session_expired", "your sign-in ran out; open "
                             "Omelet from the desktop app again", 401)
            return _body("not_signed_in", "open Omelet from the desktop app "
                         "to sign in", 401)
```

4. Add the routes directly on `app` (not `router`), next to the other route definitions:

```python
    class Handoff(BaseModel):
        code: str

    @app.post("/sessions/handoff")
    def issue_handoff() -> dict:
        return {"code": sessions.issue_handoff(), "expires_in": 60}

    @app.post("/api/session")
    def start_session(body: Handoff, response: Response) -> dict:
        session_id = sessions.redeem(body.code)
        if session_id is None:
            raise ApiError("handoff_invalid", "that sign-in link has already "
                           "been used or has run out; open Omelet from the "
                           "desktop app again", 401)
        response.set_cookie(COOKIE, session_id, max_age=SESSION_TTL,
                            httponly=True, samesite="strict", path="/api")
        return {"signed_in": True}

    @app.get("/api/session")
    def read_session(request: Request) -> dict:
        verdict = sessions.check(request.cookies.get(COOKIE))
        if verdict == "expired":
            raise ApiError("session_expired", "your sign-in ran out", 401)
        if verdict != "ok":
            raise ApiError("not_signed_in", "not signed in", 401)
        return {"signed_in": True}

    @app.delete("/api/session")
    def end_session(request: Request, response: Response) -> dict:
        sessions.end(request.cookies.get(COOKIE))
        response.delete_cookie(COOKIE, path="/api")
        return {"signed_in": False}
```

- [ ] **Step 7: Run tests**

Run: `TMPDIR=$PWD/.tmp python3 -m pytest tests/agent -q`
Expected: PASS. If `test_migrate.py` pins the version number to 2, change that assertion to `len(MIGRATIONS)`.

- [ ] **Step 8: Commit**

```bash
git add agent/core/sessions.py agent/core/migrate.py agent/core/state.py agent/api/app.py tests/agent/test_sessions.py tests/agent/test_migrate.py
git commit -m "Sign the browser in with a one-time handoff code and a session cookie"
```

---

### Task 3: The desktop's "Open Omelet" hands a session over

**Files:**
- Modify: `host/client.py`, `host/desktop/api.py`
- Modify: `tests/host/desktop/test_api_home.py`

**Interfaces:**
- Consumes: `POST /sessions/handoff` → `{"code": str, "expires_in": int}`.
- Produces: `AgentClient.handoff_code() -> str`. `DesktopApi.open_omelet()` opens `http://localhost:<EDGE_PORT>/#handoff=<code>`, or the bare URL when no code could be had.

- [ ] **Step 1: Write the failing tests**

Append to `tests/host/desktop/test_api_home.py`:

```python
class _HandoffClient:
    def __init__(self, code=None, error=None):
        self._code, self._error = code, error

    def handoff_code(self):
        if self._error:
            raise self._error
        return self._code


def _api_with_client(tmp_path, client, opened):
    state = InstallState(tmp_path / "install-state.json")
    return DesktopApi(FakeProvider(), state, push=lambda event: None,
                      probe_fn=lambda provider: READY,
                      browser_open=opened.append,
                      client_factory=lambda provider: client)


def test_open_omelet_carries_a_handoff_code_in_the_fragment(tmp_path):
    from host.core import constants
    opened = []
    _api_with_client(tmp_path, _HandoffClient(code="abc"), opened).open_omelet()
    assert opened == [f"http://localhost:{constants.EDGE_PORT}/#handoff=abc"]


def test_open_omelet_still_opens_the_page_on_an_agent_without_handoff(tmp_path):
    # An agent older than the route answers 404; the page must still open and
    # show its own sign-in screen.
    from host.client import AgentError
    from host.core import constants
    opened = []
    client = _HandoffClient(error=AgentError("not_found", "no route", 404))
    _api_with_client(tmp_path, client, opened).open_omelet()
    assert opened == [f"http://localhost:{constants.EDGE_PORT}"]
```

- [ ] **Step 2: Run to verify they fail**

Run: `TMPDIR=$PWD/.tmp python3 -m pytest tests/host/desktop/test_api_home.py -q`
Expected: FAIL — URL has no fragment.

- [ ] **Step 3: Implement**

In `host/client.py`, in the routes section after `version`:

```python
    def handoff_code(self) -> str:
        return str(self._call("POST", "/sessions/handoff")["code"])
```

In `host/desktop/api.py`, replace `open_omelet`:

```python
    def open_omelet(self) -> dict:
        # The edge port, never the agent port: the page and its /api live
        # behind Traefik.
        url = f"http://localhost:{constants.EDGE_PORT}"
        try:
            code = self._client_factory(self._provider).handoff_code()
        except Exception:
            # An older agent, a stopped VM, an unreadable token: the bare page
            # shows its own "open from the desktop app" screen, so opening it
            # is always better than an error here.
            code = None
        self._open(f"{url}/#handoff={code}" if code else url)
        return {"ok": True}
```

- [ ] **Step 4: Run tests**

Run: `TMPDIR=$PWD/.tmp python3 -m pytest tests/host -q`
Expected: PASS, including the existing `test_open_omelet_opens_the_edge_port_not_the_agent_port` (its `FakeProvider` makes the default client factory fail, so it gets the bare URL).

- [ ] **Step 5: Commit**

```bash
git add host/client.py host/desktop/api.py tests/host/desktop/test_api_home.py
git commit -m "Hand the browser a sign-in code when the desktop opens Omelet"
```

---

### Task 4: Reconcile, adopt, and folders that went missing

**Files:**
- Create: `agent/core/reconcile.py`
- Modify: `agent/api/app.py`
- Create: `tests/agent/test_reconcile.py`

**Interfaces:**
- Produces:
  - `agent.core.reconcile.Discovered(name: str, seen_at: float, adoptable: bool, reason: str | None)` (frozen dataclass), `examine(folder: Path) -> Discovered`, `discover(root: Path, known: set[str]) -> list[Discovered]`.
  - `GET /projects` → `{"projects": [...], "discovered": [{"name", "seen_at", "adoptable", "reason"}]}`.
  - `POST /projects/{id}/adopt` → 201 project payload; 409 `project_exists`; 404 `folder_not_found`; 409 `not_adoptable`.
  - Project payload gains `empty: bool`; a row whose folder is gone gets `problem.code == "folder_missing"`.

- [ ] **Step 1: Write the failing tests**

Create `tests/agent/test_reconcile.py`:

```python
import shutil

from tests.agent.conftest import COMPOSE_ONE_WEB, _create, _write_compose


def _folder(env, name, compose=True):
    d = env.config.projects_root / name
    d.mkdir(parents=True, exist_ok=True)
    if compose:
        (d / "docker-compose.yml").write_text(COMPOSE_ONE_WEB)
    return d


def test_a_folder_the_coding_agent_made_is_listed_as_discovered(env):
    _folder(env, "invoice-helper")
    listing = env.client.get("/projects").json()
    assert [d["name"] for d in listing["discovered"]] == ["invoice-helper"]
    assert listing["discovered"][0]["adoptable"] is True


def test_registered_hidden_and_staging_entries_are_not_discovered(env):
    _create(env, "blog")
    _folder(env, ".cache")
    (env.config.projects_root / "tmpab12.upload").write_bytes(b"partial")
    assert env.client.get("/projects").json()["discovered"] == []


def test_a_folder_without_compose_cannot_be_adopted(env):
    _folder(env, "spice-rack", compose=False)
    found = env.client.get("/projects").json()["discovered"][0]
    assert (found["adoptable"], found["reason"]) == (False, "compose_missing")
    resp = env.client.post("/projects/spice-rack/adopt")
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "not_adoptable"


def test_a_folder_whose_name_is_not_a_project_id_cannot_be_adopted(env):
    # Adopting "My App" would register an id compose and Traefik disagree on.
    _folder(env, "My App")
    found = env.client.get("/projects").json()["discovered"][0]
    assert found["reason"] == "bad_name"


def test_adopt_registers_without_starting(env):
    _folder(env, "invoice-helper")
    resp = env.client.post("/projects/invoice-helper/adopt")
    assert resp.status_code == 201
    assert resp.json()["status"] == "stopped"
    assert not env.runner.argv_containing("up")
    assert env.client.get("/projects").json()["discovered"] == []


def test_a_project_whose_folder_was_removed_reports_folder_missing(env):
    _create(env, "blog")
    _write_compose(env, "blog")
    shutil.rmtree(env.config.projects_root / "blog")
    project = env.client.get("/projects").json()["projects"][0]
    assert project["problem"]["code"] == "folder_missing"


def test_a_new_empty_project_reads_as_empty_not_broken(env):
    _create(env, "blog")
    project = env.client.get("/projects/blog").json()
    assert project["empty"] is True
    assert project["problem"]["code"] == "compose_missing"
```

- [ ] **Step 2: Run to verify they fail**

Run: `TMPDIR=$PWD/.tmp python3 -m pytest tests/agent/test_reconcile.py -q`
Expected: FAIL — `KeyError: 'discovered'`.

- [ ] **Step 3: Write `agent/core/reconcile.py`**

```python
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .constants import COMPOSE_FILE
from .project import _slug


@dataclass(frozen=True)
class Discovered:
    name: str
    seen_at: float
    adoptable: bool
    reason: str | None


def examine(folder: Path) -> Discovered:
    reason = None
    if _slug(folder.name) != folder.name:
        reason = "bad_name"
    elif not (folder / COMPOSE_FILE).is_file():
        reason = "compose_missing"
    return Discovered(folder.name, folder.stat().st_mtime, reason is None, reason)


def discover(root: Path, known: set[str]) -> list[Discovered]:
    """Folders under `root` with no project row. Files are skipped, which
    also skips the API's `*.upload` staging files."""
    if not root.is_dir():
        return []
    with os.scandir(root) as entries:
        folders = sorted(e.name for e in entries
                         if e.is_dir(follow_symlinks=False)
                         and not e.name.startswith(".")
                         and e.name not in known)
    return [examine(root / name) for name in folders]
```

- [ ] **Step 4: Wire it into `app.py`**

1. Imports: `from dataclasses import asdict` and `from ..core.reconcile import discover, examine`.
2. In `payload()`, replace the `try: project = load(...) ... except ApiError` block with a version that skips loading a folder that is gone, so the one return statement at the end still builds every field (Task 5 adds more):

```python
        folder = project_dir(row["id"])
        if not folder.is_dir():
            problem = {"code": "folder_missing",
                       "message": "this project's folder is gone"}
        else:
            try:
                project = load(row["id"])
                urls = urls_for(project, row["domain"])
            except ApiError as e:
                problem = {"code": e.code, "message": e.message}
```

   and add `"empty": folder.is_dir() and not (folder / constants.COMPOSE_FILE).exists(),` to the dict the function returns.
3. Replace `list_projects`:

```python
    @router.get("/projects")
    def list_projects() -> dict:
        # `omelet status` is the surface users actually read, so a stale
        # diagnosis has to clear here too. payload() only probes a row that
        # carries a stored problem -- normally none -- so an ordinary listing
        # still pays no round trips at all.
        rows = state.list_projects()
        known = {row["id"] for row in rows}
        return {"projects": [payload(row, recheck=True) for row in rows],
                "discovered": [asdict(d) for d in
                               discover(Path(config.projects_root), known)]}
```

4. Add after `create_project`:

```python
    @router.post("/projects/{project_id}/adopt", status_code=201)
    def adopt_project(project_id: str) -> dict:
        if state.get_project(project_id) is not None:
            raise ApiError("project_exists",
                           f"project '{project_id}' already exists", 409)
        folder = project_dir(project_id)
        if not folder.is_dir():
            raise ApiError("folder_not_found",
                           f"no folder '{project_id}' in the projects folder", 404)
        found = examine(folder)
        if not found.adoptable:
            raise ApiError("not_adoptable",
                           f"'{project_id}' cannot be adopted: {found.reason}", 409)
        state.add_project(project_id, str(folder), config.domain)
        return payload(state.get_project(project_id))
```

- [ ] **Step 5: Run tests**

Run: `TMPDIR=$PWD/.tmp python3 -m pytest tests/agent tests/test_project_cli.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add agent/core/reconcile.py agent/api/app.py tests/agent/test_reconcile.py
git commit -m "List folders the coding agent made and let them be adopted"
```

---

### Task 5: Payload fields for the UI, job phases, and restart

**Files:**
- Modify: `agent/api/jobs.py`, `agent/core/state.py`, `agent/api/app.py`
- Create: `tests/agent/test_api_projects_ui.py`
- Modify: `tests/agent/test_jobs.py` only if it constructs `Job` positionally with changed arguments (it should not need to)

**Interfaces:**
- Consumes: migration v3 columns `last_started_at`, `compose_name`.
- Produces:
  - `JobRegistry.submit(work, *, kind: str | None = None, project_id: str | None = None) -> str`; `JobRegistry.active_for(project_id: str) -> Job | None`. `Job.as_dict()` gains `kind`, `phase`. Work receives an output object that is callable (`write(text)`) and has `.phase(name)`.
  - `State.mark_started(id, compose_name, at)`.
  - Project payload gains `web: [{"url", "service", "primary"}]`, `job: {"id", "kind", "phase", "started_at"} | None`, `first_run: bool`. `urls` stays.
  - `POST /projects/{id}/restart` → 202 `{"job_id"}`; job kind `"restart"`. `up` job kind `"up"`, `down` job kind `"down"`.
  - Up/restart phases in order: `preparing` → `starting` → `checking`.

- [ ] **Step 1: Write the failing tests**

Create `tests/agent/test_api_projects_ui.py`:

```python
import threading

from tests.agent.conftest import (_create, _run_to_completion, _write_compose)

COMPOSE_TWO_WEBS = """
services:
  web:
    image: nginx
    ports: ["8080:80"]
  api:
    image: api
    ports: ["9000:9000"]
"""


def test_the_first_web_is_the_primary_address(env):
    _create(env, "shop")
    _write_compose(env, "shop", COMPOSE_TWO_WEBS)
    web = env.client.get("/projects/shop").json()["web"]
    assert [(w["service"], w["primary"]) for w in web] == [("web", True),
                                                           ("api", False)]


def test_a_running_up_is_visible_on_the_project(env):
    # A reopened page must find the start that is still going.
    _create(env, "blog")
    _write_compose(env, "blog")
    env.runner.up_gate = threading.Event()
    job_id = env.client.post("/projects/blog/up").json()["job_id"]
    try:
        job = env.client.get("/projects/blog").json()["job"]
        assert (job["id"], job["kind"]) == (job_id, "up")
    finally:
        env.runner.up_gate.set()
        env.jobs.wait(job_id, timeout=5)
    assert env.client.get("/projects/blog").json()["job"] is None


def test_up_reports_its_phases_in_order(env):
    _create(env, "blog")
    _write_compose(env, "blog")
    seen = []
    env.runner.up_gate = threading.Event()
    job_id = env.client.post("/projects/blog/up").json()["job_id"]
    seen.append(env.client.get(f"/jobs/{job_id}").json()["phase"])
    env.runner.up_gate.set()
    final = env.jobs.wait(job_id, timeout=5)
    seen.append(final.phase)
    assert seen == ["starting", "checking"]


def test_first_run_holds_until_one_start_succeeds(env):
    _create(env, "blog")
    _write_compose(env, "blog")
    assert env.client.get("/projects/blog").json()["first_run"] is True
    _run_to_completion(env, env.client.post("/projects/blog/up"))
    assert env.client.get("/projects/blog").json()["first_run"] is False


def test_a_start_records_the_compose_project_name(env):
    # Delete finds containers by this name; a compose file with its own
    # `name:` would otherwise be deleted by the wrong label.
    _create(env, "blog")
    _write_compose(env, "blog", "name: fancy\n" + (
        "services:\n  web:\n    image: nginx\n    ports: ['8080:80']\n"))
    _run_to_completion(env, env.client.post("/projects/blog/up"))
    assert env.state.get_project("blog")["compose_name"] == "fancy"


def test_restart_stops_before_it_starts(env):
    _create(env, "blog")
    _write_compose(env, "blog")
    _run_to_completion(env, env.client.post("/projects/blog/restart"))
    compose = [a for a in env.runner.calls
               if "compose" in a and ("down" in a or a[-2:] == ["up", "-d"])]
    assert "down" in compose[0]
    assert compose[-1][-2:] == ["up", "-d"]
```

Note on `test_up_reports_its_phases_in_order`: while `up_gate` blocks inside `compose up`, the phase is `starting`; after the job finishes, the last phase set was `checking`. The `preparing` phase is set at submission and replaced before the test can observe it reliably, so it is not asserted.

- [ ] **Step 2: Run to verify they fail**

Run: `TMPDIR=$PWD/.tmp python3 -m pytest tests/agent/test_api_projects_ui.py -q`
Expected: FAIL — `KeyError: 'web'`.

- [ ] **Step 3: Extend `agent/api/jobs.py`**

1. `Job.__init__(self, job_id: str, kind: str | None = None, project_id: str | None = None)`: store `self.kind = kind`, `self.project_id = project_id`, `self.phase = "preparing"`.
2. Add to `Job`:

```python
    def set_phase(self, phase: str) -> None:
        with self.cond:
            self.phase = phase
```

3. `as_dict` adds `"kind": self.kind, "phase": self.phase`.
4. Add before `JobRegistry`:

```python
class _Output:
    """What a work function receives: call it to log, `.phase()` to say where
    it is. Callable so every existing `write(...)` keeps working."""

    def __init__(self, job: Job):
        self._job = job

    def __call__(self, chunk: str) -> None:
        self._job.append(chunk)

    def phase(self, name: str) -> None:
        self._job.set_phase(name)
```

5. `submit(self, work, *, kind=None, project_id=None)` builds `Job(uuid.uuid4().hex[:12], kind, project_id)`; `_run` calls `work(_Output(job))`.
6. Add:

```python
    def active_for(self, project_id: str) -> Job | None:
        with self._lock:
            running = [j for j in self._jobs.values()
                       if j.project_id == project_id and j.state == RUNNING]
        return running[-1] if running else None
```

- [ ] **Step 4: Add `State.mark_started`**

```python
    def mark_started(self, id, compose_name, at):
        with self._lock:
            self._conn.execute(
                "UPDATE projects SET last_started_at=?, compose_name=? WHERE id=?",
                (at, compose_name, id))
            self._conn.commit()
```

- [ ] **Step 5: Update `app.py`**

1. `import time`.
2. In `payload()`, after `urls = urls_for(project, row["domain"])`, build the web list; and add fields to the returned dict:

```python
        web = [{"url": url, "service": spec.service, "primary": index == 0}
               for index, (url, spec) in enumerate(
                   zip(urls, project.webs if project else []))]
        active = jobs.active_for(row["id"])
```

   Initialise `web: list[dict] = []` next to `urls`. Returned dict adds:

```python
                "web": web,
                "first_run": row.get("last_started_at") is None,
                "job": None if active is None else {
                    "id": active.id, "kind": active.kind,
                    "phase": active.phase, "started_at": active.started_at},
```

3. `submit_locked(project_id, work, kind)` passes `kind=kind, project_id=project_id` to `jobs.submit`.
4. Replace the body of `project_up` with a shared helper plus two routes:

```python
    def compose_name_of(project_id: str) -> str:
        data = parse_yaml(project_dir(project_id) / constants.COMPOSE_FILE)
        return str(data.get("name") or project_id)

    def start_work(project_id: str, *, stop_first: bool):
        row = require_row(project_id)
        # Parsing happens here, not in the job, so a broken compose file comes
        # back as an error code the caller can read instead of a failed job.
        project = load(project_id)
        name = compose_name_of(project_id)
        domain = row["domain"]
        directory = project_dir(project_id)

        def work(write):
            try:
                if stop_first:
                    write(f"compose down {project_id}\n")
                    lifecycle.compose_down(runner, directory)
                write.phase("starting")
                write(f"compose up {project_id}\n")
                status, detail = lifecycle.compose_up(
                    runner, project, directory, domain)
                diagnosis = None
                if status == STARTED_OK:
                    state.mark_started(project_id, name, time.time())
                    write.phase("checking")
                    write("waiting for the project to answer through Traefik\n")
                    diagnosis = diagnose(
                        runner, project, domain, directory=directory,
                        edge_port=config.edge_port,
                        traefik_host=config.traefik_host, http_probe=http_probe,
                        timeout=config.ready_timeout)
                # ... the rest of today's `work` body, unchanged, from
                # `state.set_status(project_id, status)` to `return result`
            finally:
                locks.release(project_id)

        return work

    @router.post("/projects/{project_id}/up", status_code=202)
    def project_up(project_id: str) -> dict:
        return {"job_id": submit_locked(
            project_id, start_work(project_id, stop_first=False), "up")}

    @router.post("/projects/{project_id}/restart", status_code=202)
    def project_restart(project_id: str) -> dict:
        return {"job_id": submit_locked(
            project_id, start_work(project_id, stop_first=True), "restart")}
```

   Copy the remaining lines of today's `work` verbatim where the comment says so (from `state.set_status(project_id, status)` through `return result`, including the `JobFailed` raise). `project_down` passes `"down"` as the `kind`.

- [ ] **Step 6: Run tests**

Run: `TMPDIR=$PWD/.tmp python3 -m pytest tests/agent -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add agent/api/jobs.py agent/core/state.py agent/api/app.py tests/agent/test_api_projects_ui.py
git commit -m "Report job phases, the running job and web addresses on each project"
```

---

### Task 6: Delete that survives a broken project

**Files:**
- Modify: `agent/core/lifecycle.py`, `agent/core/files.py`, `agent/api/app.py`
- Modify: `tests/agent/test_api_projects_ui.py`, `tests/agent/test_files.py`

**Interfaces:**
- Consumes: `compose_name` column (Task 5).
- Produces:
  - `lifecycle.PROJECT_LABEL = "com.docker.compose.project"`.
  - `lifecycle.remove_by_label(runner, name: str, *, volumes: bool) -> Completed`.
  - `lifecycle.project_resources(runner, name: str) -> dict` → `{"containers": [str], "volumes": [str]}`.
  - `lifecycle.remove_tree_as_root(runner, path) -> Completed`.
  - `files.tree_stats(root: Path) -> dict` → `{"files": int, "bytes": int}`.
  - `GET /projects/{id}/delete-preview` → `{"files", "bytes", "containers", "volumes"}`.
  - `DELETE /projects/{id}?purge=<bool>` → `{"id", "stopped", "detail"}` (unchanged shape).

- [ ] **Step 1: Write the failing tests**

Append to `tests/agent/test_api_projects_ui.py`:

```python
from agent.core.exec import Completed
from tests.agent.conftest import COMPOSE_MALFORMED


def test_delete_never_reads_the_compose_file(env):
    # A broken docker-compose.yml used to make delete fail and leave
    # containers behind that nothing would list again.
    _create(env, "blog")
    _write_compose(env, "blog", COMPOSE_MALFORMED)
    resp = env.client.delete("/projects/blog")
    assert resp.status_code == 200
    removal = [a for a in env.runner.calls if "compose" not in a]
    assert removal, "delete issued no docker commands"
    assert not [a for a in env.runner.calls if "compose" in a]
    assert any("label=com.docker.compose.project=blog" in a for a in removal)


def test_delete_without_purge_keeps_files_and_volumes(env):
    # The host CLI's `destroy` and install verification rely on this.
    _create(env, "blog")
    _write_compose(env, "blog")
    env.client.delete("/projects/blog")
    assert (env.config.projects_root / "blog").is_dir()
    assert not env.runner.argv_containing("volume")


def test_purge_removes_folder_volumes_and_record(env):
    _create(env, "blog")
    _write_compose(env, "blog")
    env.client.delete("/projects/blog", params={"purge": "true"})
    assert not (env.config.projects_root / "blog").exists()
    assert env.runner.argv_containing("volume")
    assert env.state.get_project("blog") is None


def test_delete_uses_the_recorded_compose_name(env):
    _create(env, "blog")
    _write_compose(env, "blog")
    env.state.mark_started("blog", "fancy", 1.0)
    env.client.delete("/projects/blog")
    assert any("label=com.docker.compose.project=fancy" in a
               for a in env.runner.calls)


def test_delete_preview_counts_the_real_tree(env):
    _create(env, "blog")
    _write_compose(env, "blog")
    (env.config.projects_root / "blog" / "data").mkdir()
    (env.config.projects_root / "blog" / "data" / "a.bin").write_bytes(b"x" * 10)
    preview = env.client.get("/projects/blog/delete-preview").json()
    compose_size = len((env.config.projects_root / "blog" /
                        "docker-compose.yml").read_bytes())
    assert (preview["files"], preview["bytes"]) == (2, 10 + compose_size)
```

Append to `tests/agent/test_files.py`:

```python
def test_remove_tree_falls_back_to_root_for_files_a_container_owns(tmp_path):
    from agent.core import lifecycle

    class Runner:
        def __init__(self):
            self.calls = []

        def exec(self, argv, *, root=False):
            self.calls.append(argv)
            if argv[:2] == [lifecycle.DOCKER, "inspect"]:
                return Completed(0, "ghcr.io/x/omelet-agent:9\n", "")
            return Completed(0, "", "")

    runner = Runner()
    lifecycle.remove_tree_as_root(runner, tmp_path / "projects" / "blog")
    run = runner.calls[-1]
    assert run[:3] == [lifecycle.DOCKER, "run", "--rm"]
    assert ["--user", "0"] == run[run.index("--user"):run.index("--user") + 2]
    assert f"{tmp_path / 'projects'}:{tmp_path / 'projects'}" in run
    assert run[-3:] == ["rm", "-rf", str(tmp_path / "projects" / "blog")]
```

(`Completed` comes from `from agent.core.exec import Completed`; add that import at the top of `test_files.py` if it is not there.)

- [ ] **Step 2: Run to verify they fail**

Run: `TMPDIR=$PWD/.tmp python3 -m pytest tests/agent/test_api_projects_ui.py tests/agent/test_files.py -q`
Expected: FAIL.

- [ ] **Step 3: Add to `agent/core/lifecycle.py`**

```python
import os

from .exec import Completed

PROJECT_LABEL = "com.docker.compose.project"


def _labelled(runner, kind: list[str], name: str, fmt: str) -> list[str]:
    result = runner.exec([DOCKER, *kind, "--filter",
                          f"label={PROJECT_LABEL}={name}", "--format", fmt],
                         root=True)
    return result.stdout.split() if result.ok else []


def project_resources(runner, name: str) -> dict:
    return {"containers": _labelled(runner, ["ps", "-a"], name, "{{.Names}}"),
            "volumes": _labelled(runner, ["volume", "ls"], name, "{{.Name}}")}


def remove_by_label(runner, name: str, *, volumes: bool) -> Completed:
    """Compose-free teardown: the labels compose stamped on everything it
    created outlive a compose file that no longer parses."""
    steps = [(["ps", "-a"], "{{.ID}}", ["rm", "-f"]),
             (["network", "ls"], "{{.ID}}", ["network", "rm"])]
    if volumes:
        steps.append((["volume", "ls"], "{{.Name}}", ["volume", "rm", "-f"]))
    for listing, fmt, remove in steps:
        ids = _labelled(runner, listing, name, fmt)
        if not ids:
            continue
        result = runner.exec([DOCKER, *remove, *ids], root=True)
        if not result.ok:
            return result
    return Completed(0, "", "")


def remove_tree_as_root(runner, path) -> Completed:
    """The agent runs as uid 1000, and containers write root-owned files into
    bind-mounted project folders. Removes `path` from a throwaway container of
    this agent's own image (already on the VM, so no pull) running as root."""
    me = os.environ.get("HOSTNAME", "")
    image = runner.exec([DOCKER, "inspect", "--format", "{{.Config.Image}}", me],
                        root=True)
    if not image.ok:
        return image
    parent = str(os.path.dirname(str(path)))
    return runner.exec([DOCKER, "run", "--rm", "--user", "0",
                        "-v", f"{parent}:{parent}", "--entrypoint", "",
                        image.stdout.strip(), "rm", "-rf", str(path)], root=True)
```

Put the imports at the top of the module with the others.

- [ ] **Step 4: Add `tree_stats` to `agent/core/files.py`**

```python
def tree_stats(root: Path) -> dict:
    """Unreadable folders (root-owned, written by a container) are skipped
    rather than failing the whole count."""
    count = size = 0
    for dirpath, _dirs, names in os.walk(root, onerror=lambda e: None):
        for name in names:
            try:
                size += os.lstat(os.path.join(dirpath, name)).st_size
                count += 1
            except OSError:
                continue
    return {"files": count, "bytes": size}
```

Add `import os` at the top.

- [ ] **Step 5: Update the routes in `app.py`**

Add `import shutil`. Replace `delete_project` and add the preview:

```python
    def compose_name_for(row: dict) -> str:
        return row.get("compose_name") or row["id"]

    @router.get("/projects/{project_id}/delete-preview")
    def delete_preview(project_id: str) -> dict:
        row = require_row(project_id)
        return {**files.tree_stats(project_dir(project_id)),
                **lifecycle.project_resources(runner, compose_name_for(row))}

    @router.delete("/projects/{project_id}")
    def delete_project(project_id: str, purge: bool = False) -> dict:
        row = require_row(project_id)
        folder = project_dir(project_id)
        # Synchronous: the host CLI, install verification and the desktop's
        # replace-import all wait on this answer.
        with locks.held(project_id):
            result = lifecycle.remove_by_label(runner, compose_name_for(row),
                                               volumes=purge)
            if purge and folder.exists():
                shutil.rmtree(folder, ignore_errors=True)
                if folder.exists():
                    removed = lifecycle.remove_tree_as_root(runner, folder)
                    if not removed.ok and result.ok:
                        result = removed
            state.remove_project(project_id)
        return {"id": project_id, "stopped": result.ok,
                "detail": "" if result.ok else (result.stderr or result.stdout).strip()}
```

- [ ] **Step 6: Run tests**

Run: `TMPDIR=$PWD/.tmp python3 -m pytest tests/agent tests/test_project_cli.py -q`
Expected: PASS. If an existing test in `test_api_routes.py` asserted that delete issues `compose ... down`, update it to assert the label-based `rm -f` instead — that behaviour change is the point of this task.

- [ ] **Step 7: Commit**

```bash
git add agent/core/lifecycle.py agent/core/files.py agent/api/app.py tests/agent/test_api_projects_ui.py tests/agent/test_files.py tests/agent/test_api_routes.py
git commit -m "Delete projects by compose label so a broken compose file cannot block it"
```

---

### Task 7: Free space and disk-full errors

**Files:**
- Create: `agent/core/disk.py`, `tests/agent/test_disk.py`
- Modify: `agent/api/app.py`

**Interfaces:**
- Produces: `disk.usage(path: Path, *, statvfs=os.statvfs) -> dict` → `{"free_bytes": int, "total_bytes": int}`; `disk.is_disk_full(exc: BaseException) -> bool`; `GET /disk`; `disk_full` (507) from the tar.gz and PUT file routes.

- [ ] **Step 1: Write the failing tests**

```python
import errno
import os
from types import SimpleNamespace

from agent.core import disk


def _fake_statvfs(_path):
    return SimpleNamespace(f_frsize=4096, f_blocks=1000, f_bfree=300,
                           f_bavail=200)


def test_free_space_excludes_blocks_reserved_for_root(tmp_path):
    # f_bfree counts the root reserve; the agent runs as uid 1000 and cannot
    # write into it, so reporting it would let an upload start that must fail.
    assert disk.usage(tmp_path, statvfs=_fake_statvfs) == {
        "free_bytes": 200 * 4096, "total_bytes": 1000 * 4096}


def test_usage_of_a_folder_not_created_yet_reads_its_parent(tmp_path):
    seen = []
    disk.usage(tmp_path / "projects", statvfs=lambda p: seen.append(p) or
               _fake_statvfs(p))
    assert seen == [str(tmp_path)]


def test_only_enospc_counts_as_disk_full():
    assert disk.is_disk_full(OSError(errno.ENOSPC, "full"))
    assert not disk.is_disk_full(OSError(errno.EACCES, "denied"))
```

- [ ] **Step 2: Run to verify they fail**

Run: `TMPDIR=$PWD/.tmp python3 -m pytest tests/agent/test_disk.py -q`
Expected: FAIL — module missing.

- [ ] **Step 3: Write `agent/core/disk.py`**

```python
from __future__ import annotations

import errno
import os
from pathlib import Path


def usage(path: Path, *, statvfs=os.statvfs) -> dict:
    target = Path(path)
    while not target.exists() and target != target.parent:
        target = target.parent
    st = statvfs(str(target))
    return {"free_bytes": st.f_bavail * st.f_frsize,
            "total_bytes": st.f_blocks * st.f_frsize}


def is_disk_full(exc: BaseException) -> bool:
    return isinstance(exc, OSError) and exc.errno in (errno.ENOSPC, errno.EDQUOT)
```

- [ ] **Step 4: Wire into `app.py`**

1. `from ..core import constants, disk, files, lifecycle`.
2. Route:

```python
    @router.get("/disk")
    def disk_usage() -> dict:
        return disk.usage(Path(config.projects_root))
```

3. A helper and its use:

```python
    def _disk_full() -> ApiError:
        return ApiError("disk_full", "Omelet's disk is full. Free up space in "
                        "the desktop app, then try again.", 507)
```

   In `_stream_to_tempfile`, wrap `f.write(chunk)`:

```python
                    try:
                        f.write(chunk)
                    except OSError as e:
                        if disk.is_disk_full(e):
                            raise _disk_full() from e
                        raise
```

   In `upload_files`, add to the inner `try` around `files.extract_archive`:

```python
                except OSError as e:
                    if disk.is_disk_full(e):
                        raise _disk_full() from e
                    raise
```

- [ ] **Step 5: Run tests and commit**

Run: `TMPDIR=$PWD/.tmp python3 -m pytest tests/agent -q` — Expected: PASS.

```bash
git add agent/core/disk.py agent/api/app.py tests/agent/test_disk.py
git commit -m "Report free space and answer disk_full instead of internal_error"
```

---

### Task 8: The resumable upload store

**Files:**
- Create: `agent/core/uploads.py`, `tests/agent/test_uploads.py`

**Interfaces:**
- Produces:
  - Constants `CHUNK_SIZE = 8 * 1024 * 1024`, `RESERVE = 1024 ** 3`, `MAX_AGE = 7 * 24 * 3600`.
  - `UploadError(code: str, message: str, status: int, **extra)` with attributes `code, message, status, extra`.
  - `Upload` frozen dataclass: `id, project_id, path, size, offset, fingerprint, replace, updated_at`; `as_dict() -> dict`.
  - `UploadStore(root: Path, *, free_bytes: Callable[[], int], clock=time.time, opener=open)` with `start(project_id, path, size, fingerprint, replace) -> Upload`, `get(upload_id) -> Upload`, `append(upload_id, offset: int, data: bytes) -> Upload`, `finish(upload_id, target: Path) -> None`, `cancel(upload_id) -> None`, `list_for(project_id) -> list[Upload]`, `sweep() -> None`, `drop_project(project_id) -> None`.
  - Error codes: `not_enough_space` (507, extra `free_bytes`), `upload_not_found` (404), `offset_mismatch` (409, extra `offset`), `too_much_data` (400), `disk_full` (507, extra `offset`), `incomplete` (409).

- [ ] **Step 1: Write the failing tests**

Create `tests/agent/test_uploads.py`:

```python
import errno
import os

import pytest

from agent.core.uploads import RESERVE, UploadError, UploadStore

GIB = 1024 ** 3


def _store(tmp_path, free=100 * GIB, **kw):
    return UploadStore(tmp_path / "uploads", free_bytes=lambda: free, **kw)


def test_a_file_that_will_not_fit_is_refused_before_any_byte(tmp_path):
    store = _store(tmp_path, free=5 * GIB)
    with pytest.raises(UploadError) as e:
        store.start("blog", "data/big.zip", 5 * GIB - RESERVE + 1, "fp", False)
    assert e.value.code == "not_enough_space"
    assert e.value.extra["free_bytes"] == 5 * GIB
    assert not (tmp_path / "uploads").exists() or not os.listdir(tmp_path / "uploads")


def test_chunks_append_and_the_offset_is_the_bytes_held(tmp_path):
    store = _store(tmp_path)
    up = store.start("blog", "a.bin", 6, "fp", False)
    assert store.append(up.id, 0, b"abc").offset == 3
    assert store.append(up.id, 3, b"def").offset == 6


def test_a_wrong_offset_is_refused_with_the_real_one(tmp_path):
    # A client that resent a chunk after a dropped reply would otherwise
    # write it twice.
    store = _store(tmp_path)
    up = store.start("blog", "a.bin", 6, "fp", False)
    store.append(up.id, 0, b"abc")
    with pytest.raises(UploadError) as e:
        store.append(up.id, 0, b"abc")
    assert (e.value.code, e.value.extra["offset"]) == ("offset_mismatch", 3)


def test_more_bytes_than_declared_are_refused(tmp_path):
    store = _store(tmp_path)
    up = store.start("blog", "a.bin", 2, "fp", False)
    with pytest.raises(UploadError) as e:
        store.append(up.id, 0, b"abc")
    assert e.value.code == "too_much_data"


def test_a_disk_full_mid_chunk_rolls_back_to_the_chunk_start(tmp_path):
    class HalfWriter:
        def __init__(self, f):
            self._f = f

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self._f.close()

        def seek(self, *a):
            return self._f.seek(*a)

        def write(self, data):
            self._f.write(data[: len(data) // 2])
            self._f.flush()
            raise OSError(errno.ENOSPC, "No space left on device")

        def flush(self):
            self._f.flush()

        def fileno(self):
            return self._f.fileno()

    store = _store(tmp_path)
    up = store.start("blog", "a.bin", 10, "fp", False)
    store.append(up.id, 0, b"abcd")
    failing = _store(tmp_path, opener=lambda p, m: HalfWriter(open(p, m)))
    with pytest.raises(UploadError) as e:
        failing.append(up.id, 4, b"efgh")
    assert (e.value.code, e.value.extra["offset"]) == ("disk_full", 4)
    assert store.get(up.id).offset == 4


def test_finish_moves_the_file_into_place_and_forgets_the_upload(tmp_path):
    store = _store(tmp_path)
    up = store.start("blog", "data/a.bin", 3, "fp", False)
    store.append(up.id, 0, b"abc")
    target = tmp_path / "projects" / "blog" / "data" / "a.bin"
    store.finish(up.id, target)
    assert target.read_bytes() == b"abc"
    assert store.list_for("blog") == []


def test_an_unfinished_upload_cannot_be_finished(tmp_path):
    store = _store(tmp_path)
    up = store.start("blog", "a.bin", 3, "fp", False)
    with pytest.raises(UploadError) as e:
        store.finish(up.id, tmp_path / "a.bin")
    assert e.value.code == "incomplete"


def test_an_upload_id_cannot_name_a_path(tmp_path):
    with pytest.raises(UploadError) as e:
        _store(tmp_path).get("../../etc")
    assert e.value.code == "upload_not_found"


def test_uploads_untouched_for_a_week_are_swept(tmp_path):
    now = [1_000_000.0]
    store = _store(tmp_path, clock=lambda: now[0])
    old = store.start("blog", "a.bin", 3, "fp", False)
    os.utime(tmp_path / "uploads" / old.id / "data", (now[0], now[0]))
    now[0] += 7 * 24 * 3600 + 1
    fresh = store.start("blog", "b.bin", 3, "fp", False)
    os.utime(tmp_path / "uploads" / fresh.id / "data", (now[0], now[0]))
    store.sweep()
    assert [u.id for u in store.list_for("blog")] == [fresh.id]
```

- [ ] **Step 2: Run to verify they fail**

Run: `TMPDIR=$PWD/.tmp python3 -m pytest tests/agent/test_uploads.py -q`
Expected: FAIL — module missing.

- [ ] **Step 3: Write `agent/core/uploads.py`**

```python
from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from .disk import is_disk_full

CHUNK_SIZE = 8 * 1024 * 1024
RESERVE = 1024 ** 3
MAX_AGE = 7 * 24 * 3600
_ID = re.compile(r"^[0-9a-f]{32}$")


class UploadError(Exception):
    def __init__(self, code: str, message: str, status: int, **extra):
        super().__init__(message)
        self.code, self.message, self.status, self.extra = code, message, status, extra


@dataclass(frozen=True)
class Upload:
    id: str
    project_id: str
    path: str
    size: int
    offset: int
    fingerprint: str
    replace: bool
    updated_at: float

    def as_dict(self) -> dict:
        return asdict(self)


class UploadStore:
    """Partial uploads, one folder each: `data` plus `meta.json`. The size of
    `data` is the offset -- there is no second counter to fall out of step
    with it after a crash."""

    def __init__(self, root: Path, *, free_bytes: Callable[[], int],
                 clock=time.time, opener=open):
        self._root = Path(root)
        self._free_bytes = free_bytes
        self._clock = clock
        self._open = opener

    def _dir(self, upload_id: str) -> Path:
        if not _ID.match(upload_id or ""):
            raise UploadError("upload_not_found", "no such upload", 404)
        folder = self._root / upload_id
        if not (folder / "meta.json").is_file():
            raise UploadError("upload_not_found", "no such upload", 404)
        return folder

    def _load(self, folder: Path) -> Upload:
        meta = json.loads((folder / "meta.json").read_text())
        data = folder / "data"
        return Upload(id=folder.name, offset=data.stat().st_size,
                      updated_at=data.stat().st_mtime, **meta)

    def start(self, project_id: str, path: str, size: int, fingerprint: str,
              replace: bool) -> Upload:
        free = self._free_bytes()
        if size + RESERVE > free:
            raise UploadError("not_enough_space",
                              "this file is bigger than the room Omelet has left",
                              507, free_bytes=free)
        upload_id = secrets.token_hex(16)
        folder = self._root / upload_id
        folder.mkdir(parents=True)
        (folder / "data").touch()
        (folder / "meta.json").write_text(json.dumps({
            "project_id": project_id, "path": path, "size": size,
            "fingerprint": fingerprint, "replace": replace}))
        return self._load(folder)

    def get(self, upload_id: str) -> Upload:
        return self._load(self._dir(upload_id))

    def append(self, upload_id: str, offset: int, data: bytes) -> Upload:
        folder = self._dir(upload_id)
        up = self._load(folder)
        if offset != up.offset:
            raise UploadError("offset_mismatch", "the upload is at a different "
                              "offset", 409, offset=up.offset)
        if up.offset + len(data) > up.size:
            raise UploadError("too_much_data", "more bytes than the upload "
                              "declared", 400)
        target = folder / "data"
        try:
            with self._open(target, "r+b") as f:
                f.seek(offset)
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
        except OSError as e:
            # Torn chunks must not survive: the next PATCH resumes from the
            # offset the client was told, not from wherever the write died.
            os.truncate(target, offset)
            if is_disk_full(e):
                raise UploadError("disk_full", "Omelet ran out of room", 507,
                                  offset=offset) from e
            raise
        return self._load(folder)

    def finish(self, upload_id: str, target: Path) -> None:
        folder = self._dir(upload_id)
        up = self._load(folder)
        if up.offset != up.size:
            raise UploadError("incomplete", "the upload is not complete yet", 409,
                              offset=up.offset)
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(folder / "data", target)
        shutil.rmtree(folder, ignore_errors=True)

    def cancel(self, upload_id: str) -> None:
        shutil.rmtree(self._dir(upload_id), ignore_errors=True)

    def _all(self) -> list[Upload]:
        if not self._root.is_dir():
            return []
        found = []
        for folder in sorted(self._root.iterdir()):
            try:
                found.append(self._load(folder))
            except (OSError, ValueError, TypeError):
                continue
        return found

    def list_for(self, project_id: str) -> list[Upload]:
        return [u for u in self._all() if u.project_id == project_id]

    def sweep(self) -> None:
        cutoff = self._clock() - MAX_AGE
        for up in self._all():
            if up.updated_at < cutoff:
                shutil.rmtree(self._root / up.id, ignore_errors=True)

    def drop_project(self, project_id: str) -> None:
        for up in self.list_for(project_id):
            shutil.rmtree(self._root / up.id, ignore_errors=True)
```

Note: `os.replace` across `uploads/` and `projects/` is atomic because both sit under `/opt/omelet` on one filesystem; `finish` raises `OSError` (`EXDEV`) otherwise, which the route reports as an internal error. In production both are the same mount.

- [ ] **Step 4: Run tests**

Run: `TMPDIR=$PWD/.tmp python3 -m pytest tests/agent/test_uploads.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add agent/core/uploads.py tests/agent/test_uploads.py
git commit -m "Add a resumable upload store whose offset is the bytes on disk"
```

---

### Task 9: Upload routes, one-level listing and attachment downloads

**Files:**
- Modify: `agent/core/config.py`, `agent/core/files.py`, `agent/api/app.py`
- Create: `tests/agent/test_api_uploads.py`
- Modify: `tests/agent/test_files.py`, `tests/agent/test_config.py` (only if it enumerates every field)

**Interfaces:**
- Consumes: `UploadStore`, `UploadError`, `CHUNK_SIZE` (Task 8), `disk.usage` (Task 7), `locks`, `resolve_path`, `require_row`.
- Produces:
  - `AgentConfig.uploads_root: Path = Path(f"{constants.GUEST_ROOT}/uploads")`, env `OMELET_UPLOADS_ROOT`.
  - `files.list_dir(root: Path, rel: str) -> list[dict]` → `[{"name", "kind": "folder"|"file", "size": int|None, "items": int|None, "modified": float}]`, folders first then by name, `.omelet` hidden. Raises `PathTraversalError` on escape, `FileNotFoundError` when not a folder.
  - Routes: `POST /projects/{id}/uploads`, `GET /projects/{id}/uploads`, `GET /uploads/{uid}`, `PATCH /uploads/{uid}`, `DELETE /uploads/{uid}`; `GET /projects/{id}/files?dir=` one level; `GET /projects/{id}/files/{path}` sends `Content-Disposition: attachment`.
  - `UploadError` bodies: `{"error": {"code", "message", **extra}}`.
  - `DELETE /projects/{id}?purge=true` also drops the project's pending uploads.

- [ ] **Step 1: Write the failing tests**

Create `tests/agent/test_api_uploads.py`:

```python
import pytest

from agent.core.uploads import RESERVE
from tests.agent.conftest import _create


@pytest.fixture
def blog(env):
    _create(env, "blog")
    return env


def _start(env, path="data/a.bin", size=6, **extra):
    return env.client.post("/projects/blog/uploads", json={
        "path": path, "size": size, "fingerprint": "a.bin:6:1", **extra})


def _patch(env, uid, offset, data):
    return env.client.patch(f"/uploads/{uid}", content=data,
                            headers={"Upload-Offset": str(offset)})


def test_a_chunked_upload_lands_in_the_chosen_folder(blog):
    uid = _start(blog).json()["upload_id"]
    _patch(blog, uid, 0, b"abc")
    done = _patch(blog, uid, 3, b"def").json()
    assert done["done"] is True
    assert (blog.config.projects_root / "blog" / "data" / "a.bin").read_bytes() == b"abcdef"


def test_a_partial_upload_is_invisible_to_the_file_listing(blog):
    # The coding agent reads the project folder; a half file there is a
    # corrupt dump it will happily try to import.
    uid = _start(blog).json()["upload_id"]
    _patch(blog, uid, 0, b"abc")
    assert blog.client.get("/projects/blog/files").json()["files"] == []


def test_a_reopened_page_can_find_the_unfinished_upload(blog):
    uid = _start(blog).json()["upload_id"]
    _patch(blog, uid, 0, b"abc")
    pending = blog.client.get("/projects/blog/uploads").json()["uploads"]
    assert [(u["id"], u["offset"], u["fingerprint"]) for u in pending] == [
        (uid, 3, "a.bin:6:1")]


def test_an_upload_path_cannot_escape_the_project(blog):
    resp = _start(blog, path="../other/x")
    assert resp.json()["error"]["code"] == "path_traversal"


def test_an_existing_file_is_not_replaced_unless_asked(blog):
    (blog.config.projects_root / "blog").mkdir(parents=True, exist_ok=True)
    (blog.config.projects_root / "blog" / "a.bin").write_bytes(b"old")
    assert _start(blog, path="a.bin").json()["error"]["code"] == "file_exists"
    assert _start(blog, path="a.bin", replace=True).status_code == 201


def test_the_offset_mismatch_body_carries_the_real_offset(blog):
    uid = _start(blog).json()["upload_id"]
    _patch(blog, uid, 0, b"abc")
    err = _patch(blog, uid, 0, b"abc").json()["error"]
    assert (err["code"], err["offset"]) == ("offset_mismatch", 3)


def test_a_file_bigger_than_free_space_is_refused_up_front(blog):
    free = blog.client.get("/disk").json()["free_bytes"]
    resp = _start(blog, size=free - RESERVE + 1)
    assert resp.status_code == 507
    assert resp.json()["error"]["code"] == "not_enough_space"


def test_one_folder_level_is_listed_with_counts(blog):
    root = blog.config.projects_root / "blog"
    (root / "data").mkdir(parents=True)
    (root / "data" / "x.csv").write_bytes(b"12345")
    (root / ".omelet").mkdir()
    (root / "README.md").write_text("hi")
    entries = blog.client.get("/projects/blog/files", params={"dir": ""}).json()["entries"]
    assert [(e["name"], e["kind"], e["items"], e["size"]) for e in entries] == [
        ("data", "folder", 1, None), ("README.md", "file", None, 2)]


def test_a_download_is_offered_as_an_attachment(blog):
    root = blog.config.projects_root / "blog"
    root.mkdir(parents=True, exist_ok=True)
    (root / "notes.txt").write_text("hi")
    resp = blog.client.get("/projects/blog/files/notes.txt")
    assert resp.headers["content-disposition"].startswith("attachment")
```

- [ ] **Step 2: Run to verify they fail**

Run: `TMPDIR=$PWD/.tmp python3 -m pytest tests/agent/test_api_uploads.py -q`
Expected: FAIL — 404 on `/projects/blog/uploads`.

- [ ] **Step 3: Config field**

In `AgentConfig`, after `token_path`:

```python
    # Partial uploads, outside projects_root so neither a listing, the
    # reconcile scan nor the coding agent ever sees a half-written file.
    uploads_root: Path = Path(f"{constants.GUEST_ROOT}/uploads")
```

and in `from_env`: `uploads_root=Path(env.get("OMELET_UPLOADS_ROOT", f"{constants.GUEST_ROOT}/uploads")),`.

In `tests/agent/conftest.py`'s `env` fixture config add `uploads_root=tmp_path / "uploads",`.

- [ ] **Step 4: `files.list_dir`**

```python
_HIDDEN = {".omelet"}


def list_dir(root: Path, rel: str) -> list[dict]:
    folder = resolve_within(root, rel) if rel else root.resolve()
    if not folder.is_dir():
        raise FileNotFoundError(rel)
    entries = []
    for entry in os.scandir(folder):
        if entry.name in _HIDDEN:
            continue
        info = entry.stat(follow_symlinks=False)
        if entry.is_dir(follow_symlinks=False):
            try:
                items = len(os.listdir(entry.path))
            except OSError:
                items = None
            entries.append({"name": entry.name, "kind": "folder", "size": None,
                            "items": items, "modified": info.st_mtime})
        else:
            entries.append({"name": entry.name, "kind": "file",
                            "size": info.st_size, "items": None,
                            "modified": info.st_mtime})
    return sorted(entries, key=lambda e: (e["kind"] != "folder", e["name"].lower()))
```

- [ ] **Step 5: Routes in `app.py`**

1. Imports: `from ..core.uploads import CHUNK_SIZE, UploadError, UploadStore`.
2. After `sessions = Sessions(state)`:

```python
    uploads = UploadStore(config.uploads_root,
                          free_bytes=lambda: disk.usage(
                              Path(config.projects_root))["free_bytes"])
    uploads.sweep()
```

3. Exception handler next to the others:

```python
    @app.exception_handler(UploadError)
    async def _upload_error(_request, exc: UploadError):
        return JSONResponse({"error": {"code": exc.code, "message": exc.message,
                                       **exc.extra}}, status_code=exc.status)
```

4. Body model near `CreateProject`:

```python
class StartUpload(BaseModel):
    path: str
    size: int = Field(ge=0)
    fingerprint: str = ""
    replace: bool = False
```

   (import `Field` from `pydantic`).
5. Routes:

```python
    def finish_upload(upload_id: str) -> dict:
        up = uploads.get(upload_id)
        with locks.held(up.project_id):
            uploads.finish(upload_id, resolve_path(up.project_id, up.path))
        return {"upload_id": upload_id, "offset": up.size, "size": up.size,
                "done": True}

    @router.post("/projects/{project_id}/uploads", status_code=201)
    def start_upload(project_id: str, body: StartUpload) -> dict:
        require_row(project_id)
        target = resolve_path(project_id, body.path)
        if target.exists() and not body.replace:
            raise ApiError("file_exists",
                           f"'{body.path}' is already in the project", 409)
        up = uploads.start(project_id, body.path, body.size, body.fingerprint,
                           body.replace)
        if up.size == 0:
            return finish_upload(up.id)
        return {"upload_id": up.id, "offset": 0, "size": up.size,
                "chunk_size": CHUNK_SIZE, "done": False}

    @router.get("/projects/{project_id}/uploads")
    def pending_uploads(project_id: str) -> dict:
        require_row(project_id)
        uploads.sweep()
        return {"uploads": [u.as_dict() for u in uploads.list_for(project_id)]}

    @router.get("/uploads/{upload_id}")
    def upload_status(upload_id: str) -> dict:
        return uploads.get(upload_id).as_dict()

    @router.patch("/uploads/{upload_id}")
    async def upload_chunk(upload_id: str, request: Request) -> dict:
        try:
            offset = int(request.headers.get("upload-offset", ""))
        except ValueError:
            raise ApiError("invalid_request", "Upload-Offset must be a number",
                           400) from None
        body = bytearray()
        async for piece in request.stream():
            body += piece
            if len(body) > 2 * CHUNK_SIZE:
                raise ApiError("payload_too_large", "send chunks of at most "
                               f"{CHUNK_SIZE} bytes", 413)
        up = uploads.append(upload_id, offset, bytes(body))
        if up.offset == up.size:
            return finish_upload(upload_id)
        return {"upload_id": upload_id, "offset": up.offset, "size": up.size,
                "done": False}

    @router.delete("/uploads/{upload_id}")
    def cancel_upload(upload_id: str) -> dict:
        uploads.cancel(upload_id)
        return {"upload_id": upload_id, "cancelled": True}
```

6. `list_files` gains the one-level mode:

```python
    @router.get("/projects/{project_id}/files")
    def list_files(project_id: str, dir: str | None = None) -> dict:
        require_row(project_id)
        if dir is None:
            return {"files": files.list_tree(project_dir(project_id))}
        try:
            return {"dir": dir,
                    "entries": files.list_dir(project_dir(project_id), dir)}
        except files.PathTraversalError as e:
            raise ApiError("path_traversal", str(e), 400) from e
        except FileNotFoundError:
            raise ApiError("folder_not_found",
                           f"no folder '{dir}' in project '{project_id}'",
                           404) from None
```

7. `read_file` returns `FileResponse(target, filename=target.name)`.
8. In `delete_project`, inside `if purge ...` before removing the folder: `uploads.drop_project(project_id)`.

- [ ] **Step 6: Run the full suite**

Run: `TMPDIR=$PWD/.tmp python3 -m pytest -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add agent/core/config.py agent/core/files.py agent/api/app.py tests/agent/conftest.py tests/agent/test_api_uploads.py tests/agent/test_config.py
git commit -m "Add resumable upload routes, one-level listings and attachment downloads"
```

---

### Task 10: Route /api through Traefik and release the agent as 0.2.0

**Files:**
- Modify: `engine/stack.yml`, `agent/__init__.py`, `agent/Dockerfile`, `CLAUDE.md`

**Interfaces:**
- Consumes: everything above.
- Produces: agent image tag `0.2.0` in `stack.yml`; Traefik router `omelet-api` for `/api` on the page's host.

- [ ] **Step 1: Bump the version in all three places**

- `agent/__init__.py`: `__version__ = "0.2.0"`
- `agent/Dockerfile`: `ARG AGENT_VERSION=0.2.0`
- `engine/stack.yml`: `image: ${OMELET_AGENT_IMAGE:-ghcr.io/omelet-app/omelet-agent:0.2.0}`

- [ ] **Step 2: Add the agent's Traefik labels and the uploads root**

Under the `agent:` service in `engine/stack.yml`, after `networks:`:

```yaml
    # The browser UI's API. Same origin as the page (added with the web
    # service), so the session cookie needs no CORS. Priority beats the
    # page's catch-all route; project hosts never match these two names.
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.omelet-api.rule=(Host(`localhost`) || Host(`127.0.0.1`)) && PathPrefix(`/api`)"
      - "traefik.http.routers.omelet-api.entrypoints=web"
      - "traefik.http.routers.omelet-api.priority=1000"
      - "traefik.http.services.omelet-api.loadbalancer.server.port=${OMELET_AGENT_PORT:-39099}"
```

No new environment entry is needed: `uploads_root` defaults under `/opt/omelet`, which is already bind-mounted at the identical path.

- [ ] **Step 3: Run the constants and engine tests**

Run: `TMPDIR=$PWD/.tmp python3 -m pytest tests/test_constants_agree.py tests/engine -q`
Expected: PASS (the three version strings agree).

- [ ] **Step 4: Update `CLAUDE.md`**

In the `agent/api/` bullet, after "produced by one exception handler.", add:

```markdown
  Every route is on one `APIRouter` mounted twice: at `/` behind the bearer token
  (host, in-VM CLI) and at `/api` behind the `omelet_session` cookie plus a
  `Host`/`Origin` allowlist (the browser UI, reached through Traefik on the edge
  port). The desktop gets the browser a session through `POST /sessions/handoff`;
  see `docs/superpowers/specs/2026-09-21-web-ui-agent-prerequisites-design.md`.
```

And a new layer bullet after `agent/core/files.py`:

```markdown
- `agent/core/uploads.py` — resumable chunked uploads staged in `/opt/omelet/uploads`,
  outside `projects_root`; the staged file's size is the offset.
  `agent/core/reconcile.py` lists project folders with no state row (made by a
  coding agent) for the UI's adopt flow.
```

- [ ] **Step 5: Full suite, then commit**

Run: `TMPDIR=$PWD/.tmp python3 -m pytest -q`
Expected: PASS.

```bash
git add engine/stack.yml agent/__init__.py agent/Dockerfile CLAUDE.md
git commit -m "Route /api to the agent through Traefik and bump the agent to 0.2.0"
```

---

## Untested on purpose

- Traefik label syntax and nginx: covered by the live-VM acceptance run.
- `statvfs` itself, and `remove_tree_as_root` against a real daemon.
- ENOSPC inside the legacy PUT and tar.gz routes: the classification is `disk.is_disk_full`, tested in Task 7; the wiring is two `except` clauses.
