# eggie_api refactor — layered modules

Issue #57. Restructures `runtime/eggie_api/` without changing what it does.

## 1. Why

- `routes/app.py` is one 1.2k-line `create_app` closure: dependency wiring, auth middleware,
  error handlers, pydantic schemas, project loading, the project read model, start/stop/clone
  orchestration, per-project locks, request streaming and ~45 routes sharing ~15 captured
  variables.
- `core/` is a flat bag of 24 modules mixing pure logic (`detect`, `overlay`, `project`,
  secret rules, `sync.plan`), I/O adapters (`exec`, `state`, `files`, `disk`), external clients
  (`cloud`, `github`) and stateful services (`account`, `public`, `github_link`, `uploads`).
- `State` holds six unrelated tables.
- Failures are reported five ways: `ApiError` (with an HTTP status) raised from inside services,
  four other exception classes with their own handlers, and inline `try/except → ApiError` in
  routes.

Adding a feature today means editing `app.py`, `state.py` and `core/`, and reading all three to
know where its logic ends.

## 2. What stays fixed

- The wire contract: every route, request and response body, the `{"error": {"code",
  "message"}}` shape and every status code, the `/` + `/api` double mount, the console-only
  router, `API_VERSION`, and the seam paths in `constants.py`. The host, the console and the
  in-VM CLI depend on it; nothing here needs a host release.
- Behaviour, including the concurrency rules documented in comments (lock handover from clone to
  up, stamp-before-read for secrets, staging-dir clone, finish-under-lock for uploads). Comments
  move with their code.
- The existing test suite under `tests/runtime/api/` passes with import-path and construction
  changes only. It is the regression net; no test is rewritten to fit the new shape.
- `create_app` remains a factory: importing the package opens no sqlite file and starts no
  thread.

Internals have no compatibility obligation (no released users).

## 3. Layout

```
runtime/eggie_api/
  __init__.py        version placeholder (unchanged)
  __main__.py        uvicorn entrypoint: python -m eggie_api
  config.py          ApiConfig (unchanged)
  constants.py       shared constants (unchanged)
  errors.py          EggieError and its subclasses
  wiring.py          composition root: build(config, ...) -> Services
  domain/            pure logic, no I/O
    compose.py detect.py overlay.py project.py secrets.py sync.py
  infra/             I/O adapters, no business rules
    runner.py db.py migrate.py docker.py cloud.py github.py files.py disk.py
    health.py connect.py uploads.py tunnel.py
    repos/ projects.py secrets.py sessions.py account.py github.py cloud_projects.py
  services/          use cases; hold state, locks and threads; no FastAPI
    loader.py projects.py lifecycle.py clone.py files.py uploads.py secrets.py
    github_link.py account.py public.py sessions.py agents.py sync.py system.py
    jobs.py locks.py
  http/              the only tree importing fastapi / starlette / pydantic
    app.py auth.py errors.py schemas.py
    routers/ system.py projects.py lifecycle.py files.py uploads.py secrets.py
             jobs.py account.py github.py public.py agents.py sessions.py
```

**One dependency edge:** `http → services → domain | infra`. `domain` imports nothing from the
other three. `infra` never imports `services` or `http`. `wiring.py` and `__main__.py` are the
only modules outside `http/` that import `services`. A test enforces this (§8).

Where today's modules go:

| today | tomorrow |
|---|---|
| `core/compose.py`, `detect.py`, `overlay.py`, `project.py` | `domain/` unchanged |
| `core/secrets.py` (rules, `declared`, reserved names) | `domain/secrets.py` |
| `core/sync.py` `plan` | `domain/sync.py`; `apply`, `run_pass`, `SyncLoop` → `services/sync.py` |
| `core/exec.py` | `infra/runner.py` |
| `core/state.py`, `migrate.py` | `infra/db.py` + `infra/repos/`, `infra/migrate.py` |
| `core/lifecycle.py` (compose/label commands) | `infra/docker.py` |
| `core/cloud.py`, `github.py`, `files.py`, `disk.py`, `health.py`, `connect.py` | `infra/` same names |
| `core/uploads.py` `UploadStore` | `infra/uploads.py` |
| `core/public.py` `TunnelClient`, `write_token`, `remove_token` | `infra/tunnel.py` |
| `core/public.py` `Public`, `account.py`, `github_link.py`, `sessions.py`, `agents.py` | `services/` same names |
| `routes/jobs.py` | `services/jobs.py` |
| `routes/app.py` | `services/*` (§5), `http/*` (§6), `wiring.py` |
| `routes/__main__.py` | `__main__.py` |

Test filenames do not move; `test_lifecycle.py` keeps testing what is now `infra/docker.py`.

## 4. Errors

`errors.py`:

```python
class EggieError(Exception):
    """A failure the caller can act on. `code` is the wire code."""
    def __init__(self, code: str, message: str, *, extra: dict | None = None)

class NotFound(EggieError): ...      # 404
class Conflict(EggieError): ...      # 409
class Invalid(EggieError): ...       # 422
class BadRequest(EggieError): ...    # 400
class Unauthorized(EggieError): ...  # 401
class Forbidden(EggieError): ...     # 403
class TooLarge(EggieError): ...      # 413
class DiskFull(EggieError): ...      # 507
class Upstream(EggieError): ...      # 502
class Unavailable(EggieError): ...   # 503
```

Rules:

- Anything below `http/` that wants the caller to act raises one of these with today's wire
  code. The status is not in the exception; `http/errors.py` holds the only class → status map.
- Named subclasses exist only where code catches by type: `PathTraversalError(BadRequest)`,
  `BadArchiveError(BadRequest)` in `infra/files.py`; `SecretError(BadRequest)` in
  `domain/secrets.py`; `PublicBusy(Conflict)` and `PublicUnavailable(Conflict)` in
  `services/public.py`; `NotConnected(Conflict)` in `services/github_link.py`;
  `UploadError(EggieError)` is replaced by the matching subclass per raise site
  (`upload_not_found` → `NotFound`, `not_enough_space`/`disk_full` → `DiskFull`,
  `offset_mismatch`/`incomplete` → `Conflict`, `too_much_data` → `BadRequest`), keeping `extra`.
- Transport-level exceptions stay as they are and never reach HTTP: `CloudError`,
  `CloudUnavailable`, `GitHubError`, `GitHubUnavailable` (infra clients, carrying the upstream
  code), `NotSignedIn`, `JobFailed`, `AmbiguousError`, `SchemaTooNew`. The service that calls
  them translates.
- `http/errors.py` registers four handlers: `EggieError` (map + body), `RequestValidationError`
  (`invalid_request`, 422, first error's location as today), Starlette `HTTPException`
  (`not_found` / `method_not_allowed` / `http_error`), and the catch-all 500 with the same
  message and logging as now.

Status codes per code are the ones in today's `app.py` and `uploads.py`; the test suite asserts
them.

## 5. Persistence

`infra/db.py`:

```python
class Database:
    def __init__(self, path): ...        # mkdir, one connection, check_same_thread=False,
                                         # Row factory, RLock, migrate()
    def one(self, sql, params=()) -> dict | None
    def all(self, sql, params=()) -> list[dict]
    @contextmanager
    def tx(self): ...                    # holds the lock, yields the connection, commits
    def close(self)
```

`infra/repos/`, one class per table, each taking a `Database`:

- `ProjectRepo` — `add`, `get`, `list`, `remove`, `set_status`, `set_problem`,
  `set_compose_name`, `mark_started`.
- `SecretRepo` — `names`, `values`, `set`, `delete`, `request`, `delete_request`, `requests`,
  `drop`. It stamps `projects.secrets_changed_at`: that column belongs to the secrets feature.
- `SessionRepo` — `add`, `get`, `set_expiry`, `remove`.
- `AccountRepo` — `get`, `update(**fields)` with the field allowlist.
- `GitHubRepo` — `get`, `update(**fields)` with the field allowlist.
- `CloudProjectRepo` — `mapping`, `map`, `unmap`, `clear`.

`State` is deleted. Services take the repos they use. `migrate.py` moves unchanged.

## 6. Services

A router parses the request, calls one service method and shapes the response. No
`try/except`, no business branching, no lock handling in `http/`. Every translation to an
`EggieError` lives in the service that knows why it failed.

| service | constructor takes | responsibility |
|---|---|---|
| `ProjectLoader` | config, `ProjectRepo` | `dir(id)`, `require(id) -> row`, `load(id) -> Project` (yaml parsing; `AmbiguousError` and malformed definitions → `Invalid`), `compose(id) -> dict`, `urls_for(project, domain)`, `public_hosts(id)`. Read-only; depends on no other service, so `Public` can take `hosts_for=loader.public_hosts` without a cycle. |
| `ProjectService` | loader, `ProjectRepo`, jobs, secrets, uploads, public, runner, config, `wake` | `create(body)`, `adopt(id)`, `list()` (rows + `discover`), `get(id)`, `delete(id, purge)`, `delete_preview(id)`, `payload(row, recheck)` — the read model (`urls`, `web`, `problem` with the recheck rule, `public`, `restart_needed`, `secrets_requested`, active `job`). |
| `LifecycleService` | config, runner, loader, `ProjectRepo`, secrets, jobs, locks, http_probe | `start(id, *, stop_first) -> job_id`, `stop(id) -> job_id`, `resume_all() -> [job_id]`, `logs(id, service) -> Completed`, `logs_stream(id, service) -> Iterator[str]`, and `start_work(id, *, stop_first)` kept public: it returns the work callable that releases the project lock in its own `finally`, which `CloneService` hands over to. |
| `CloneService` | config, runner, loader, `ProjectRepo`, locks, jobs, github client, github_link, lifecycle, `wake` | `clone(repo, id) -> (job_id, project_id)`; validation, slug, existence check, staging-dir clone and the lock handover move verbatim. |
| `FileService` | loader, locks, runner | `extract(id, archive_path) -> tree`, `list(id, dir)`, `write(id, rel_path, tmp_path) -> size`, `path(id, rel_path) -> Path` (for `FileResponse`), `delete(id, rel_path)`. OS errors → `Conflict("permission_denied")`, `DiskFull`, `NotFound`. |
| `UploadService` | `UploadStore`, loader, `ProjectRepo`, locks | `start(id, body) -> dict`, `append(upload_id, offset, bytes) -> dict`, `finish(upload_id) -> dict` (lock, then re-check the project exists), `cancel`, `list(id)`, `get(upload_id)`, `drop_project(id)`. |
| `SecretService` | `SecretRepo`, `ProjectRepo` | `list(id)`, `put(id, name, value)`, `request(id, name, hint)`, `delete(id, name)`, `values(id) -> dict | None`, `restart_needed(row)`, `declared(compose)`, `drop(id)`. Rules come from `domain/secrets.py`. |
| `GitHubLink` | `GitHubRepo`, github client, config | as today, plus `require_token()` (`NotConnected` → `Conflict("github_not_connected")` or `github_reconnect`) and `repos(page)` (bad-credentials marking, `GitHubUnavailable` → `Unavailable`, `GitHubError` → `Upstream`). `connect()` / `reapply()` translate the same way. |
| `Account` | `AccountRepo`, `CloudProjectRepo`, cloud client | as today; `start_sign_in` translates `CloudUnavailable` → `Unavailable("cloud_unavailable")`, `CloudError` → `Upstream("cloud_error")`. `sign_out` stays; the "release public URLs first" step moves to `wiring` as the existing `on_sign_out`-style hook pattern (`account.before_sign_out = public.release_all`). |
| `Public` | `ProjectRepo`, `CloudProjectRepo`, account, cloud client, `TunnelClient`, token path, origin, `hosts_for` | as today. |
| `Sessions` | `SessionRepo` | as today. |
| `AgentStatus` | config dirs | as today; `ensure_setup` raises `NotFound("agent_not_found")` / `Unavailable("agent_setup_unavailable")` itself. |
| `SyncLoop` | `passes: list[Callable[[], None]]`, interval | runs each pass under its own exception guard; `wake()`, `start()`. `run_pass(account, cloud, repos)` and `apply` live beside it. |
| `SystemService` | config, runner | `health()`, `version()`, `disk()`, `connect()`. |
| `JobRegistry`, `ProjectLocks` | — | moved unchanged; `ProjectLocks.held()` / `acquire_or_raise()` raise `Conflict("project_busy")`. |

Cross-service hooks stay attributes set in wiring (`account.on_forget = public.forget_local`,
`account.on_signed_in = sync.wake`, `account.before_sign_out = public.release_all`). No event
bus.

## 7. HTTP layer and composition root

`http/`:

- `app.py` — `create_app(services, config) -> FastAPI`. Registers handlers and middleware,
  includes every feature router at `/` and `/api`, the console-only router (public URL on/off)
  at `/api`, and the session routes (`/sessions/handoff`, `/api/sessions/handoff`,
  `/api/session`). Sets `app.state.services` for `__main__` and tests.
- `auth.py` — `install(app, *, token, sessions, config)`: the bearer / session-cookie /
  `Host` / `Origin` middleware exactly as now, including the open browser routes and the
  cookie-extension rule. Refusals are returned as bodies from the middleware as today (a
  middleware cannot raise into the exception handlers); it uses the same `error_body()` helper
  as `errors.py`.
- `errors.py` — `STATUS: dict[type[EggieError], int]`, `error_body()`, `install(app)`.
- `schemas.py` — `WebOverride`, `CreateProject`, `StartUpload`, `Handoff`, `SecretValue`,
  `SecretHint`, `CloneRepo`.
- `routers/<feature>.py` — each exports `build(services) -> APIRouter` (public URL also exports
  `build_console(services)`). Routers close over the services they receive; no `Depends()` on
  `app.state`. Request streaming (`stream_to_tempfile`, the chunk reader with its size cap and
  disk-full translation) stays in `routers/files.py` and `routers/uploads.py` because it consumes
  a `Request`.

`wiring.py`:

```python
@dataclass(frozen=True)
class Services:
    config: ApiConfig
    db: Database
    repos: Repos                   # frozen dataclass of the six repositories
    runner, jobs, locks, loader, projects, lifecycle, clone, files, uploads,
    secrets, account, public, github_link, sessions, agents, sync, system

def build(config, *, runner=None, http_probe=None, cloud=None, github=None,
          account=None, public=None, github_link=None, sessions=None,
          jobs=None) -> Services
```

`build()` constructs infra, then services in dependency order, sets the hooks and assembles
`SyncLoop([public.reconcile, lambda: run_pass(account, cloud, repos)])`. The keyword overrides
are the fakes tests inject today. `http.app.create_app(*, config=None, **overrides)` keeps the
current convenience signature by calling `build()` — `conftest.py` changes only its import.

`__main__.py` at package top (`python -m eggie_api`; both Dockerfiles' `CMD` updated). Boot
order unchanged: `account.resume()`, `sync.start()`, `lifecycle.resume_all()`, `uvicorn.run`.
`SchemaTooNew` still exits with the one-line message.

## 8. Tests

- Existing tests change only in imports and in `State` construction (17 call sites, mainly
  `test_account`, `test_sync`, `test_public`, `test_sessions`): `State(path)` becomes
  `Database(path)` plus the repos the test needs; `env.state.*` becomes `env.repos.<table>.*`.
- New: `tests/runtime/api/test_layering.py`. Walks `runtime/eggie_api/` via `__file__`, parses
  each file's imports with `ast`, and asserts: `domain/` imports none of `infra`, `services`,
  `http`, `sqlite3`, `subprocess`, `urllib`, `fastapi`; `infra/` imports neither `services` nor
  `http`; `infra/` and `services/` import none of `fastapi`, `starlette`, `pydantic`; only
  `http/`, `wiring.py` and `__main__.py` import `services`; and that it scanned at least 40
  files.
- `tests/test_constants_agree.py` and `tests/test_no_platform_leak.py` get the new paths;
  `test_no_host_import.py` is unchanged.
- No tests are written for moved code.

## 9. Docs

- `runtime/eggie_api/CLAUDE.md`: "Shape" rewritten around the four layers, the one dependency
  edge, the error rule, and an "adding a feature area" recipe (repo → service → router →
  three lines in `wiring.build`). Feature-area notes and "things that will bite you" keep their
  content with paths updated.
- Path updates: `docs/releasing.md` (`constants.py`), `runtime/stack.yml` comment
  (`overlay.py`), `config.py` docstring, root `CLAUDE.md` if it names a moved file,
  `docs/architecture.md` if it does.

## 10. Commit order

One branch (`feature/57-api-refactor`), one PR, each commit green on the full suite:

1. Mechanical moves: `core/` → `domain/` + `infra/`, `exec` → `runner`, `lifecycle` →
   `docker`, `tunnel.py` out of `public.py`, `jobs.py` out of `routes/`.
2. `errors.py`; `Database` + repos replace `State`; services raise typed errors; `http` maps.
3. Services extracted from `app.py`; `app.py` is down to routes.
4. `http/` split into routers, auth, errors, schemas; `wiring.py`; `__main__.py`; Dockerfiles.
5. Layering test; CLAUDE.md and docs.

Verification: `.venv/bin/python -m pytest -q` after each commit; `docker build
runtime/eggie_api/` at the end to confirm the `CMD` change; the separate review agent after the
PR is opened.
