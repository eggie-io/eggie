# eggie_api Layered Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restructure `runtime/eggie_api/` into `domain/`, `infra/`, `services/`, `http/` with one composition root, typed errors and one repository per table — without changing one byte of the wire contract.

**Architecture:** One dependency edge, `http → services → domain | infra`. Routers parse the request, call one service method, shape the response; services own locks, jobs and error translation; `errors.py` subclasses carry wire codes and `http/errors.py` holds the only class→status map; `wiring.build()` constructs the whole object graph.

**Tech Stack:** Python 3.12, FastAPI/Starlette, pydantic, sqlite3, pytest with `TestClient`.

**Spec:** `docs/superpowers/specs/2026-10-08-api-refactor-design.md`

## Global Constraints

- Branch `feature/57-api-refactor` (already created from `main`; spec committed as `b6d9044`). Commit after every task; never commit on `main`.
- Run tests with the repo venv: `cd /home/ihor/projects/local-environment-for-non-tech/poc && TMPDIR=/home/ihor/tmp .venv/bin/python -m pytest -q` (create `/home/ihor/tmp` once with `mkdir -p`; `/tmp/pytest-of-$USER` is root-owned in this WSL). The full suite must be green at the end of every task.
- Wire contract is frozen: every route path, method, status code, body, the `{"error": {"code", "message", ...}}` shape, the `/` + `/api` double mount, the console-only mount of public POST/DELETE, cookie names and TTLs. If a test asserting a status code or body fails, the refactor is wrong, not the test.
- Existing tests change only in import paths, `State` construction and `env.state`/`app.state` access. Never rewrite an assertion to fit.
- Moved code moves **verbatim**, including comments. Comments explain concurrency rules that are easy to break; keep every one next to the code it describes.
- No new comments that restate code; no references to tickets or docs in comments.
- No `sys.platform` / `platform.system()` / `os.name` anywhere (`tests/test_no_platform_leak.py`).
- `from __future__ import annotations` at the top of every new module.
- Never run `wsl.exe`, `limactl`, or touch a real VM.

## Review Focus

1. **Lock handover clone → up** (`CloneService.clone` → `LifecycleService.start_work`): the clone job must release the project lock exactly once — in its own `finally` unless `start_work` took it over. Pinned by the existing `test_api_github.py` clone tests plus the new `test_clone_lock_is_released_when_start_work_refuses` in Task 6.
2. **Error bodies for `extra` fields** (`offset_mismatch` carries `offset`, `not_enough_space` carries `free_bytes`): the single `EggieError` handler must merge `extra` into the body. Pinned by `test_api_uploads.py` (existing) and the new `test_error_body_merges_extra` in Task 2.
3. **Middleware refusals keep their bodies**: the auth middleware returns `JSONResponse` bodies directly (it cannot raise into the handlers). `test_api_auth.py` / `test_api_browser_auth.py` pin every code; Task 7 adds nothing new because they already cover each branch.
4. **`finish` under the lock re-checks the project row** (delete racing an upload): pinned by existing `test_api_uploads.py::test_finishing_an_upload_for_a_deleted_project_cancels_it` (verify the name exists; if not, the behaviour is covered by the inline comment moved verbatim — add the test in Task 5).
5. **A broken `public.status()` must not take the listing down**: the `try/except Exception` around `public.status(row["id"])` in `payload` moves verbatim; pinned by the new `test_listing_survives_a_public_status_crash` in Task 6.

---

### Task 1: Mechanical moves — `core/` → `domain/` + `infra/`, services and jobs into `services/`

**Files:**
- Move (git mv): see table below
- Create: `runtime/eggie_api/domain/__init__.py`, `infra/__init__.py`, `infra/repos/__init__.py` (empty), `services/__init__.py`, `domain/sync.py`, `infra/tunnel.py`
- Modify: every moved module's imports; `runtime/eggie_api/routes/app.py`, `routes/__main__.py`; `runtime/eggie_api/pyproject.toml`; `runtime/eggie_api/Dockerfile`; `tests/runtime/api/*.py`, `tests/test_constants_agree.py`
- Test: whole suite

**Interfaces:**
- Produces: the module paths every later task imports (`eggie_api.config`, `eggie_api.constants`, `eggie_api.domain.*`, `eggie_api.infra.*`, `eggie_api.services.*`).

- [ ] **Step 1: Move the files**

```bash
cd /home/ihor/projects/local-environment-for-non-tech/poc/runtime/eggie_api
mkdir -p domain infra/repos services
touch domain/__init__.py infra/__init__.py infra/repos/__init__.py services/__init__.py
git mv core/config.py config.py
git mv core/constants.py constants.py
for m in compose detect overlay project secrets; do git mv core/$m.py domain/$m.py; done
git mv core/exec.py infra/runner.py
git mv core/state.py infra/state.py
git mv core/migrate.py infra/migrate.py
git mv core/lifecycle.py infra/docker.py
for m in cloud github files disk health connect uploads; do git mv core/$m.py infra/$m.py; done
for m in public account github_link sessions agents sync; do git mv core/$m.py services/$m.py; done
git mv routes/jobs.py services/jobs.py
git rm -q core/__init__.py
rm -rf core
```

- [ ] **Step 2: Split `plan` out of `services/sync.py` into `domain/sync.py`**

Create `domain/sync.py` with the three dataclasses and `plan` moved verbatim from `services/sync.py` (lines `@dataclass(frozen=True) class Create` through the end of `plan`), plus `client_ref`:

```python
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Create:
    local_id: str


@dataclass(frozen=True)
class Delete:
    local_id: str
    cloud_id: str


@dataclass(frozen=True)
class Forget:
    local_id: str


def plan(local_ids: set[str], mapping: dict[str, dict], org_id: str) -> list:
    ...  # verbatim


def client_ref(device_id: str, local_id: str) -> str:
    return f"{device_id}/{local_id}"
```

In `services/sync.py` delete those definitions and import them: `from ..domain.sync import Create, Delete, Forget, client_ref, plan`.

- [ ] **Step 3: Split `TunnelClient` and the token helpers out of `services/public.py` into `infra/tunnel.py`**

Create `infra/tunnel.py` with `write_token`, `remove_token` and `TunnelClient` moved verbatim (they need `os`, `tempfile`, `Path`, and `from .docker import DOCKER`). In `services/public.py` remove them and add `from ..infra.tunnel import TunnelClient, remove_token, write_token`; drop the now-unused `os`, `tempfile` imports and `from .lifecycle import DOCKER`.

- [ ] **Step 4: Fix every relative import inside the package**

Rewrite the import lines in each moved file. The rule: `domain/` modules import only `..constants` and siblings; `infra/` modules import `..constants`, `..domain.*`, siblings; `services/` modules import `..constants`, `..config`, `..domain.*`, `..infra.*`, siblings.

| file | old import | new import |
|---|---|---|
| `config.py` | `from . import constants` / `from .health import READY_TIMEOUT` / `from .. import __version__` | `from . import constants` / `from .infra.health import READY_TIMEOUT` / `from . import __version__` |
| `domain/project.py` | `from .detect import …` / `from .overlay import …` | unchanged (siblings) |
| `infra/docker.py` | `from .constants import COMPOSE_FILE` / `from .exec import Completed` / `from .project import …` | `from ..constants import COMPOSE_FILE` / `from .runner import Completed` / `from ..domain.project import Project, FAILED_TO_START, STARTED_OK, classify, overlay_yaml` |
| `infra/health.py` | `from . import constants` / `from .detect import WebSpec` / `from .overlay import host_for` / `from .project import Project` / `from .lifecycle import DOCKER, container_id` (check the actual lines) | `from .. import constants` / `from ..domain.detect import WebSpec` / `from ..domain.overlay import host_for` / `from ..domain.project import Project` / `from .docker import DOCKER, container_id` |
| `infra/state.py` | `from .migrate import migrate` | unchanged |
| `infra/uploads.py` | `from .disk import is_disk_full` | unchanged |
| `services/account.py` | `from .cloud import …` | `from ..infra.cloud import CloudError, CloudUnavailable` |
| `services/sync.py` | `from .account import NotSignedIn` / `from .cloud import …` / `from .constants import VERIFY_PROJECT_ID` | `from .account import NotSignedIn` / `from ..infra.cloud import …` / `from ..constants import VERIFY_PROJECT_ID` |
| `services/public.py` | `from .account import NotSignedIn` / `from .cloud import …` | `from .account import NotSignedIn` / `from ..infra.cloud import CloudError, CloudUnavailable` |
| `services/github_link.py` | `from .github import …` | `from ..infra.github import GitHubError, GitHubUnavailable, identity_from` |
| `services/sessions.py`, `services/agents.py`, `services/jobs.py` | none to the package | unchanged |
| `routes/__main__.py` | `from ..core.config import ApiConfig` / `from ..core.migrate import SchemaTooNew` | `from ..config import ApiConfig` / `from ..infra.migrate import SchemaTooNew` |

For `routes/app.py` replace the import block (lines 25–48) with:

```python
from .. import constants
from ..config import ApiConfig
from ..domain import secrets as secret_rules
from ..domain.detect import AmbiguousError
from ..domain.overlay import host_for
from ..domain.project import (CRASH_LOOPING, STARTED_OK, Project, _slug,
                              load_project)
from ..domain.secrets import SecretError
from ..infra import connect, disk, files
from ..infra import docker as lifecycle
from ..infra.cloud import Cloud, CloudError, CloudUnavailable
from ..infra.github import (GitHub, GitHubError, GitHubUnavailable, auth_failed,
                            clone_argv, redact, valid_repo)
from ..infra.health import answers, default_probe, diagnose
from ..infra.runner import LocalRunner
from ..infra.state import State
from ..infra.tunnel import TunnelClient
from ..infra.uploads import CHUNK_SIZE, UploadError, UploadStore
from ..services.account import Account
from ..services.agents import AgentStatus, UnknownAgent
from ..services.github_link import GitHubLink, NotConnected
from ..services.jobs import JobFailed, JobRegistry
from ..services.public import Public, PublicBusy, Unavailable
from ..services.reconcile import discover, examine
from ..services.sessions import COOKIE, HANDOFF_TTL, SESSION_TTL, Sessions
from ..services.sync import SyncLoop, run_pass
```

(`reconcile.py` — the folder scanner for adopt — goes to `infra/reconcile.py`, it is pure filesystem reading: `git mv core/reconcile.py infra/reconcile.py` in Step 1 and import it as `from ..infra.reconcile import discover, examine`.)

`domain/compose.py`'s `load_compose(path)` reads a file, which `domain/` must not do. Its only caller is `tests/runtime/api/test_acceptance_detection.py`: delete the function (and the `Path`/`yaml` imports it needed) and have the test do `yaml.safe_load((FIX / name).read_text())` through a two-line local helper.

Then: `cd runtime && python -c "import eggie_api.routes.app"` must succeed (use `../.venv/bin/python`). Fix any import the table missed by reading the traceback — every module in the package must import.

- [ ] **Step 5: Update packaging**

`runtime/eggie_api/pyproject.toml`:
```toml
packages = ["eggie_api", "eggie_api.domain", "eggie_api.infra", "eggie_api.infra.repos",
            "eggie_api.services", "eggie_api.routes"]
```
`runtime/eggie_api/Dockerfile` lines 57–58 become:
```dockerfile
COPY config.py constants.py /app/
COPY domain /app/domain
COPY infra /app/infra
COPY services /app/services
COPY routes /app/routes
```

- [ ] **Step 6: Update the tests' imports**

```bash
cd /home/ihor/projects/local-environment-for-non-tech/poc
sed -i \
  -e 's/eggie_api\.core\.config/eggie_api.config/g' \
  -e 's/eggie_api\.core import constants/eggie_api import constants/g' \
  -e 's/eggie_api\.core\.constants/eggie_api.constants/g' \
  -e 's/eggie_api\.core\.\(compose\|detect\|overlay\|project\|secrets\)\b/eggie_api.domain.\1/g' \
  -e 's/eggie_api\.core\.exec/eggie_api.infra.runner/g' \
  -e 's/eggie_api\.core\.lifecycle/eggie_api.infra.docker/g' \
  -e 's/eggie_api\.core import lifecycle/eggie_api.infra import docker as lifecycle/g' \
  -e 's/eggie_api\.core\.\(state\|migrate\|cloud\|github\|files\|disk\|health\|connect\|uploads\|reconcile\)\b/eggie_api.infra.\1/g' \
  -e 's/eggie_api\.core import \(migrate\|health\|disk\|uploads\|reconcile\)/eggie_api.infra import \1/g' \
  -e 's/eggie_api\.core\.reconcile/eggie_api.infra.reconcile/g' \
  -e 's/eggie_api\.core\.\(public\|account\|github_link\|sessions\|agents\|sync\)\b/eggie_api.services.\1/g' \
  -e 's/eggie_api\.routes\.jobs/eggie_api.services.jobs/g' \
  tests/runtime/api/*.py tests/test_constants_agree.py
grep -rn "eggie_api\.core" tests/ ; # must print nothing
```
`test_sync.py` imports `Create, Delete, Forget, plan` from `eggie_api.services.sync` — they are re-exported there by the import in Step 2, so it keeps working. `test_public.py` imports `TunnelClient, write_token` from `eggie_api.services.public` — same, re-exported through the import added in Step 3.

Also edit the one message string in `tests/test_constants_agree.py:30`: `eggie_api/core/constants.py` → `eggie_api/constants.py`.

- [ ] **Step 7: Run the whole suite**

Run: `TMPDIR=/home/ihor/tmp .venv/bin/python -m pytest -q`
Expected: all pass (same count as on `main`: run it on `main` first if unsure — `git stash` is not needed, just `git log` the count from CI or run once before Step 1).

- [ ] **Step 8: Commit**

```bash
git add -A runtime/eggie_api tests
git commit -m "$(cat <<'EOF'
Move eggie_api modules into domain/, infra/ and services/

Pure moves and import rewrites; no logic changes. app.py still holds
the routes and wiring.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Typed errors — `errors.py` and one HTTP handler

**Files:**
- Create: `runtime/eggie_api/errors.py`
- Modify: `routes/app.py` (handlers + every `raise ApiError`), `infra/files.py`, `infra/uploads.py`, `domain/secrets.py`, `services/public.py`, `services/github_link.py`, `services/agents.py`, `services/account.py`
- Test: `tests/runtime/api/test_errors.py` (new), `tests/runtime/api/test_uploads.py`, `test_public.py` (imports only)

**Interfaces:**
- Produces: `eggie_api.errors.EggieError(code, message, *, extra=None)` and subclasses `NotFound, Conflict, Invalid, BadRequest, Unauthorized, Forbidden, TooLarge, DiskFull, Upstream, Unavailable`; `STATUS` map and `error_body()` (in `app.py` for now; Task 7 moves them to `http/errors.py`).

- [ ] **Step 1: Write the failing test**

`tests/runtime/api/test_errors.py`:
```python
from eggie_api.errors import Conflict, EggieError, NotFound


def test_subclasses_carry_the_wire_code_and_message():
    e = NotFound("project_not_found", "no project with id 'x'")
    assert isinstance(e, EggieError)
    assert (e.code, e.message, str(e)) == ("project_not_found", "no project with id 'x'",
                                           "no project with id 'x'")
    assert e.extra == {}


def test_extra_fields_ride_along():
    e = Conflict("offset_mismatch", "the upload is at a different offset", extra={"offset": 7})
    assert e.extra == {"offset": 7}
```

Run: `TMPDIR=/home/ihor/tmp .venv/bin/python -m pytest tests/runtime/api/test_errors.py -q`
Expected: FAIL, `ModuleNotFoundError: eggie_api.errors`.

- [ ] **Step 2: Create `errors.py`**

```python
"""Failures a caller can act on. `code` is the wire code; the HTTP status is
the http layer's business, keyed on the class."""
from __future__ import annotations


class EggieError(Exception):
    def __init__(self, code: str, message: str, *, extra: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.extra = dict(extra or {})


class NotFound(EggieError): ...
class Conflict(EggieError): ...
class Invalid(EggieError): ...
class BadRequest(EggieError): ...
class Unauthorized(EggieError): ...
class Forbidden(EggieError): ...
class TooLarge(EggieError): ...
class DiskFull(EggieError): ...
class Upstream(EggieError): ...
class Unavailable(EggieError): ...
```

Run the test: PASS.

- [ ] **Step 3: Write the failing handler test**

Append to `tests/runtime/api/test_errors.py` (uses the `env` fixture from `conftest.py`):
```python
def test_error_body_merges_extra(env):
    # A second chunk at the wrong offset answers with the offset the client
    # must resume from -- the one field the host's uploader reads.
    env.client.post("/projects", json={"id": "blog"})
    up = env.client.post("/projects/blog/uploads",
                         json={"path": "a.txt", "size": 4}).json()
    r = env.client.patch(f"/uploads/{up['upload_id']}", content=b"ab",
                         headers={"Upload-Offset": "3"})
    assert r.status_code == 409
    assert r.json()["error"] == {"code": "offset_mismatch",
                                 "message": "the upload is at a different offset",
                                 "offset": 0}
```
Run: `… -k merges_extra` → PASS already (today's `UploadError` handler does this). It stays as the pin for the new handler; proceed.

- [ ] **Step 4: Replace the exception classes below `http` with `EggieError` subclasses**

`infra/files.py`:
```python
from ..errors import BadRequest

class PathTraversalError(BadRequest):
    def __init__(self, message: str):
        super().__init__("path_traversal", message)

class BadArchiveError(BadRequest):
    def __init__(self, message: str):
        super().__init__("bad_archive", message)
```
(keep their docstrings if any; constructor call sites already pass one message).

`infra/uploads.py`: delete `UploadError`; `from ..errors import BadRequest, Conflict, DiskFull, NotFound`; rewrite each raise, keeping messages verbatim:
- `UploadError("upload_not_found", "no such upload", 404)` → `NotFound("upload_not_found", "no such upload")` (three sites)
- `UploadError("not_enough_space", "…", 507, free_bytes=free)` → `DiskFull("not_enough_space", "…", extra={"free_bytes": free})`
- `UploadError("offset_mismatch", "…", 409, offset=up.offset)` → `Conflict("offset_mismatch", "…", extra={"offset": up.offset})`
- `UploadError("too_much_data", "…", 400)` → `BadRequest("too_much_data", "…")`
- `UploadError("disk_full", "Eggie ran out of room", 507, offset=real_offset)` → `DiskFull("disk_full", "Eggie ran out of room", extra={"offset": real_offset})`
- `UploadError("incomplete", "…", 409, offset=up.offset)` → `Conflict("incomplete", "…", extra={"offset": up.offset})`

Update the comment in `_load_checked` that says "raise UploadError" to "raise NotFound".

`domain/secrets.py`: `class SecretError(BadRequest)` with `__init__(self, code, message): super().__init__(code, message)`; import `from ..errors import BadRequest`.

`services/public.py`:
```python
from ..errors import Conflict

class PublicBusy(Conflict):
    def __init__(self, local_id: str):
        super().__init__("project_busy",
                         f"another operation on '{local_id}' is still running")

class PublicUnavailable(Conflict):
    def __init__(self, code: str):
        super().__init__(code, MESSAGES[code])
```
Replace every `Unavailable(` in the module with `PublicUnavailable(`.

`services/github_link.py`: `class NotConnected(Conflict)` with `__init__(self): super().__init__("github_not_connected", "Connect GitHub first.")`.

`services/agents.py`: delete `UnknownAgent`; `ensure_setup` raises `NotFound("agent_not_found", "Eggie has nothing to set up for that agent.")` where it raised `UnknownAgent`, and wraps its two `_bump` calls: `except OSError: raise Unavailable("agent_setup_unavailable", "This VM can't set up agents yet. Restart Eggie and try again.") from None`.

- [ ] **Step 5: Rewrite `routes/app.py`'s error plumbing**

Delete `class ApiError`, `_busy`, `_disk_full`, `_github_down`, `_reconnect`. Add:

```python
from ..errors import (BadRequest, Conflict, DiskFull, EggieError, Invalid,
                      NotFound, TooLarge, Unauthorized, Unavailable, Upstream)

STATUS: dict[type[EggieError], int] = {
    NotFound: 404, Conflict: 409, Invalid: 422, BadRequest: 400,
    Unauthorized: 401, Forbidden: 403, TooLarge: 413, DiskFull: 507,
    Upstream: 502, Unavailable: 503,
}


def _status_of(exc: EggieError) -> int:
    for cls in type(exc).__mro__:
        if cls in STATUS:
            return STATUS[cls]
    return 500


def _busy(project_id: str) -> Conflict:
    return Conflict("project_busy",
                    f"another operation on '{project_id}' is still running")


def _disk_full() -> DiskFull:
    return DiskFull("disk_full", "Eggie's disk is full. Free up space in "
                    "the desktop app, then try again.")


def _github_down() -> Unavailable:
    return Unavailable("github_unavailable", "GitHub can't be reached. Check the "
                       "internet connection and try again.")


def _reconnect() -> Conflict:
    return Conflict("github_reconnect", "GitHub stopped accepting Eggie's "
                    "access. Reconnect GitHub and try again.")
```

Replace the `ApiError`, `UploadError` and `SecretError` handlers with one:
```python
    @app.exception_handler(EggieError)
    async def _eggie_error(_request, exc: EggieError):
        return JSONResponse({"error": {"code": exc.code, "message": exc.message,
                                       **exc.extra}}, status_code=_status_of(exc))
```

Then convert every `raise ApiError(code, msg, status)` by status: 404 → `NotFound(code, msg)`, 409 → `Conflict`, 422 → `Invalid`, 400 → `BadRequest`, 413 → `TooLarge`, 503 → `Unavailable`, 502 → `Upstream`. Every `except ApiError` → `except EggieError`. Drop the `try/except` wrappers that now add nothing: `resolve_path` becomes a plain call to `files.resolve_within` (the `PathTraversalError` already is a 400 with the right code); the `except files.PathTraversalError` / `except files.BadArchiveError` arms in `upload_files` and `list_files` go away; `agent_setup` loses its `try/except`; in `public_on`/`public_off` the `except PublicBusy: raise _busy(...)` and `except Unavailable as e: raise ApiError(e.code, e.message, 409)` arms go away (both already carry the right code and class). Keep `except NotConnected` in `require_github_token` only for the `needs_reconnect` branch:
```python
    def require_github_token() -> str:
        try:
            return github_link.token()
        except NotConnected:
            if github_link.status()["state"] == "needs_reconnect":
                raise _reconnect() from None
            raise
```

- [ ] **Step 6: Update the tests that import the removed names**

```bash
sed -i 's/from eggie_api.infra.uploads import RESERVE, UploadError, UploadStore/from eggie_api.errors import EggieError\nfrom eggie_api.infra.uploads import RESERVE, UploadStore/' tests/runtime/api/test_uploads.py
sed -i 's/\bUploadError\b/EggieError/g' tests/runtime/api/test_uploads.py
sed -i 's/\bUnavailable\b/PublicUnavailable/g' tests/runtime/api/test_public.py
```
If a test in `test_uploads.py` asserts `e.value.status`, change it to assert the code only (the status is the http layer's now) — check with `grep -n "\.status" tests/runtime/api/test_uploads.py`.

- [ ] **Step 7: Run the whole suite**

Run: `TMPDIR=/home/ihor/tmp .venv/bin/python -m pytest -q`
Expected: all pass. A status-code mismatch means a wrong subclass was picked in Step 5 — match today's number, listed in spec §4.

- [ ] **Step 8: Commit**

```bash
git add -A runtime/eggie_api tests
git commit -m "$(cat <<'EOF'
Report every failure through one EggieError hierarchy

Services raise typed errors with the wire code; the HTTP layer holds the
only class-to-status map.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: `Database` and one repository per table; delete `State`

**Files:**
- Create: `infra/db.py`, `infra/repos/{__init__,projects,secrets,sessions,account,github,cloud_projects}.py`
- Delete: `infra/state.py`
- Modify: `services/account.py`, `services/public.py`, `services/sessions.py`, `services/github_link.py`, `services/sync.py`, `routes/app.py`, `routes/__main__.py`
- Test: `tests/runtime/api/test_repos.py` (renamed from `test_state.py`), `test_account.py`, `test_sync.py`, `test_public.py`, `test_sessions.py`, `test_github_link.py`, `test_migrate.py`, `conftest.py` and the `env.state.*` call sites

**Interfaces:**
- Produces:
  - `Database(path)` with `one(sql, params=()) -> dict | None`, `all(sql, params=()) -> list[dict]`, `tx()` context manager yielding the connection, `close()`.
  - `ProjectRepo(db)`: `add(id, guest_path, domain, status="stopped")`, `get(id)`, `list()`, `remove(id)`, `set_status(id, status)`, `set_problem(id, code=None, message=None)`, `set_compose_name(id, name)`, `mark_started(id, at)`.
  - `SecretRepo(db)`: `names(project_id)`, `values(project_id)`, `set(project_id, values, at=None)`, `delete(project_id, name, at=None) -> bool`, `requests(project_id)`, `request(project_id, name, hint)`, `delete_request(project_id, name) -> bool`, `drop(project_id)`.
  - `SessionRepo(db)`: `add(id_hash, expires_at)`, `get(id_hash)`, `set_expiry(id_hash, expires_at)`, `remove(id_hash)`.
  - `AccountRepo(db)`: `get()`, `update(**fields)`.
  - `GitHubRepo(db)`: `get()`, `update(**fields)`.
  - `CloudProjectRepo(db)`: `mapping()`, `map(local_id, cloud_id, org_id)`, `unmap(local_id)`, `clear()`.
  - `Repos` frozen dataclass in `infra/repos/__init__.py` with fields `projects, secrets, sessions, account, github, cloud_projects` and `Repos.open(db)` classmethod.

- [ ] **Step 1: Rename and rewrite the state tests to the repos**

```bash
git mv tests/runtime/api/test_state.py tests/runtime/api/test_repos.py
```
Edit `test_repos.py`: replace the import with
```python
from eggie_api.infra.db import Database
from eggie_api.infra.repos import Repos
```
and each `State(path)` with `Repos.open(Database(path))`; then the method calls: `s.add_project` → `s.projects.add`, `s.get_project` → `s.projects.get`, `s.remove_project` → `s.projects.remove`, `s.set_status` → `s.projects.set_status`, `s.set_problem` → `s.projects.set_problem`, `s.list_projects` → `s.projects.list`, `s.close()` → `s.db.close()`, `.get_account()` → `.account.get()`, `.update_account(` → `.account.update(`, `s.set_secrets` → `s.secrets.set`, `s.secret_names` → `s.secrets.names`, `s.secret_values` → `s.secrets.values`, `s.delete_secret` → `s.secrets.delete`, `s.drop_secrets` → `s.secrets.drop`, `s.request_secret` → `s.secrets.request`, `s.secret_requests` → `s.secrets.requests`, `s.delete_request` → `s.secrets.delete_request`. Rename `test_state_serves_threads…` to `test_repos_serve_threads_other_than_the_one_that_opened_the_database`.

Run: `… tests/runtime/api/test_repos.py -q` → FAIL, `ModuleNotFoundError`.

- [ ] **Step 2: Create `infra/db.py`**

```python
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

from .migrate import migrate


class Database:
    """One sqlite connection shared by every thread.

    FastAPI serves sync routes from a threadpool and jobs run on threads of
    their own, so a thread-bound connection fails as soon as a second thread
    touches it. One connection with `check_same_thread=False` behind a lock
    holds for both, and — unlike a connection per thread — nothing accumulates
    an open handle for every worker the threadpool ever created.
    """

    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            migrate(self._conn)

    def one(self, sql: str, params=()) -> dict | None:
        with self._lock:
            row = self._conn.execute(sql, params).fetchone()
        return dict(row) if row else None

    def all(self, sql: str, params=()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, params)]

    @contextmanager
    def tx(self):
        with self._lock:
            yield self._conn
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()
```

- [ ] **Step 3: Create the repositories**

`infra/repos/projects.py`:
```python
from __future__ import annotations

from ..db import Database


class ProjectRepo:
    def __init__(self, db: Database):
        self._db = db

    def add(self, id, guest_path, domain, status="stopped") -> None:
        with self._db.tx() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO projects(id, guest_path, domain, status) "
                "VALUES (?,?,?,?)", (id, guest_path, domain, status))

    def get(self, id) -> dict | None:
        return self._db.one("SELECT * FROM projects WHERE id=?", (id,))

    def list(self) -> list[dict]:
        return self._db.all("SELECT * FROM projects ORDER BY id")

    def set_status(self, id, status) -> None:
        with self._db.tx() as conn:
            conn.execute("UPDATE projects SET status=? WHERE id=?", (status, id))

    def set_problem(self, id, code=None, message=None) -> None:
        """`code=None` clears it: a problem that outlives the fix is worse than
        none, so every `up` writes this whether or not it found something."""
        with self._db.tx() as conn:
            conn.execute(
                "UPDATE projects SET problem_code=?, problem_message=? WHERE id=?",
                (code, message, id))

    def set_compose_name(self, id, compose_name) -> None:
        with self._db.tx() as conn:
            conn.execute("UPDATE projects SET compose_name=? WHERE id=?",
                         (compose_name, id))

    def mark_started(self, id, at) -> None:
        with self._db.tx() as conn:
            conn.execute("UPDATE projects SET last_started_at=? WHERE id=?", (at, id))

    def remove(self, id) -> None:
        with self._db.tx() as conn:
            conn.execute("DELETE FROM projects WHERE id=?", (id,))
```

`infra/repos/secrets.py` — the six secret methods from `State` with the same SQL, renamed as in Interfaces; `set` and `delete` keep the `secrets_changed_at` stamping on `projects` (one `tx()` per method, `time.time()` default for `at`).

`infra/repos/sessions.py` — `add/get/set_expiry/remove` with `State`'s four session statements.

`infra/repos/account.py` and `infra/repos/github.py` — `get()` returns `self._db.one("SELECT * FROM account WHERE id=1")` (resp. `github`); `update(**fields)` keeps the allowlist (`ACCOUNT_FIELDS` / `GITHUB_FIELDS` frozensets move here) and the `ValueError` on unknown names, then one `tx()` with the f-string assignment list as today.

`infra/repos/cloud_projects.py` — `mapping()` (the dict comprehension from `State.cloud_mapping`), `map`, `unmap`, `clear`.

`infra/repos/__init__.py`:
```python
from __future__ import annotations

from dataclasses import dataclass

from ..db import Database
from .account import AccountRepo
from .cloud_projects import CloudProjectRepo
from .github import GitHubRepo
from .projects import ProjectRepo
from .secrets import SecretRepo
from .sessions import SessionRepo


@dataclass(frozen=True)
class Repos:
    db: Database
    projects: ProjectRepo
    secrets: SecretRepo
    sessions: SessionRepo
    account: AccountRepo
    github: GitHubRepo
    cloud_projects: CloudProjectRepo

    @classmethod
    def open(cls, db: Database) -> "Repos":
        return cls(db, ProjectRepo(db), SecretRepo(db), SessionRepo(db),
                   AccountRepo(db), GitHubRepo(db), CloudProjectRepo(db))
```

Run: `… tests/runtime/api/test_repos.py -q` → PASS. Then `git rm runtime/eggie_api/infra/state.py`.

- [ ] **Step 4: Switch the services to repos**

- `services/sessions.py`: `__init__(self, sessions: SessionRepo, *, clock=time.time)`, store as `self._sessions`; `add_session` → `self._sessions.add`, `get_session` → `.get`, `set_session_expiry` → `.set_expiry`, `remove_session` → `.remove`.
- `services/account.py`: `__init__(self, account: AccountRepo, cloud_projects: CloudProjectRepo, cloud, *, clock=…, sleep=…, spawn=…)`; `self._state.get_account()` → `self._account.get()`, `self._state.update_account(` → `self._account.update(`, `self._state.clear_cloud_projects()` → `self._cloud_projects.clear()`. Expose `self.repo = account` is **not** needed — tests use `account._account` (Step 6).
- `services/github_link.py`: `__init__(self, github_repo: GitHubRepo, github, *, …)`; `self._state.get_github()` → `self._repo.get()`, `update_github(` → `self._repo.update(`.
- `services/public.py`: `__init__(self, *, projects: ProjectRepo, cloud_projects: CloudProjectRepo, account, cloud, client, token_path, origin, hosts_for, clock=…, spawn=…)`; `self._state.cloud_mapping()` → `self._cloud_projects.mapping()`, `self._state.list_projects()` → `self._projects.list()`.
- `services/sync.py`: `apply(actions, *, account, cloud, repos: Repos, org_id)` and `run_pass(account, cloud, repos, clock=time.time)`; inside, `state.unmap_cloud_project` → `repos.cloud_projects.unmap`, `state.map_cloud_project` → `repos.cloud_projects.map`, `state.list_projects()` → `repos.projects.list()`, `state.cloud_mapping()` → `repos.cloud_projects.mapping()`.
- `routes/app.py`: `create_app(…, state=None, …)` becomes `repos=None`; `repos = repos if repos is not None else Repos.open(Database(config.state_db))`; `app.state.repos = repos` (delete `app.state.state`); every `state.<method>` call maps per the Interfaces list (`state.get_project` → `repos.projects.get`, `state.add_project` → `repos.projects.add`, `state.list_projects` → `repos.projects.list`, `state.remove_project` → `repos.projects.remove`, `state.set_*`/`mark_started` → `repos.projects.*`, `state.secret_values` → `repos.secrets.values`, `state.secret_requests` → `repos.secrets.requests`, `state.secret_names` → `repos.secrets.names`, `state.set_secrets` → `repos.secrets.set`, `state.request_secret` → `repos.secrets.request`, `state.delete_secret` → `repos.secrets.delete`, `state.delete_request` → `repos.secrets.delete_request`, `state.drop_secrets` → `repos.secrets.drop`). Constructors: `Sessions(repos.sessions)`, `Account(repos.account, repos.cloud_projects, cloud)`, `GitHubLink(repos.github, github, …)`, `Public(projects=repos.projects, cloud_projects=repos.cloud_projects, …)`, `run_pass(account, cloud, repos)`.

- [ ] **Step 5: Update `conftest.py` and the tests**

`conftest.py:150`: `state=app.state.state` → `repos=app.state.repos`.

```bash
cd tests/runtime/api
sed -i -e 's/env\.state\.get_project(/env.repos.projects.get(/g' \
       -e 's/env\.state\.set_status(/env.repos.projects.set_status(/g' \
       -e 's/env\.state\.set_compose_name(/env.repos.projects.set_compose_name(/g' \
       -e 's/env\.state\.secret_values(/env.repos.secrets.values(/g' \
       -e 's/env\.state\.secret_requests(/env.repos.secrets.requests(/g' \
       -e 's/env\.state\.set_session_expiry(/env.repos.sessions.set_expiry(/g' \
       -e 's/env\.state\.close()/env.repos.db.close()/g' *.py
sed -i -e 's/account\._state\.update_account(/account._account.update(/g' \
       -e 's/account\._state\.get_account()/account._account.get()/g' \
       -e 's/account\._state\.map_cloud_project(/account._cloud_projects.map(/g' \
       -e 's/account\._state\.cloud_mapping()/account._cloud_projects.mapping()/g' test_account.py
```
By hand:
- `test_account.py:24`: `Account(State(…), cloud, …)` → `r = Repos.open(Database(tmp_path / "state.db")); Account(r.account, r.cloud_projects, cloud, …)`.
- `test_sync.py`, `test_public.py`: the fixture builds `state = State(…)` then calls `state.add_project/update_account/map_cloud_project`; build `repos = Repos.open(Database(…))` and call `repos.projects.add`, `repos.account.update`, `repos.cloud_projects.map`; assertions `state.cloud_mapping()` → `repos.cloud_projects.mapping()`, `state.get_account()` → `repos.account.get()`; `run_pass(account, cloud, state)` → `run_pass(account, cloud, repos)`; `Account(state, cloud, …)` → `Account(repos.account, repos.cloud_projects, cloud, …)`; `Public(state=state, …)` → `Public(projects=repos.projects, cloud_projects=repos.cloud_projects, …)`.
- `test_sessions.py`: `Sessions(State(p), clock=clock)` → `Sessions(Repos.open(Database(p)).sessions, clock=clock)`; `state = State(db)` + `state.get_session(sid)` → `Repos.open(Database(db)).sessions.get(sid)`; the two `create_app(…, state=state)` sites → `repos=Repos.open(Database(config.state_db))`.
- `test_github_link.py:23`: `GitHubLink(State(…), github, …)` → `GitHubLink(Repos.open(Database(…)).github, github, …)`.
- `test_api_github.py` `make()`: `state = State(…)` → `repos = Repos.open(Database(…))`; `GitHubLink(state, github, …)` → `GitHubLink(repos.github, github, …)`; `create_app(…, state=state, …)` → `repos=repos`.
- `test_migrate.py`: `State(db).close()` → `Database(db).close()`; `State(db)` under `pytest.raises(SchemaTooNew)` → `Database(db)`; the `state.get_project/set_problem/get_account/cloud_mapping` lines → `r = Repos.open(Database(db))` and `r.projects.*`, `r.account.get()`, `r.cloud_projects.mapping()`.
- Every one of those files imports `from eggie_api.infra.db import Database` and `from eggie_api.infra.repos import Repos` instead of `State`.

- [ ] **Step 6: Run the whole suite**

Run: `TMPDIR=/home/ihor/tmp .venv/bin/python -m pytest -q`
Expected: all pass. `grep -rn "State\b" runtime/eggie_api tests/runtime/api` must show no reference to the deleted class.

- [ ] **Step 7: Commit**

```bash
git add -A runtime/eggie_api tests
git commit -m "$(cat <<'EOF'
Replace State with Database and one repository per table

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Small services — `locks`, `loader`, `secrets`, `system`, and service-side translations

**Files:**
- Create: `services/locks.py`, `services/loader.py`, `services/secrets.py`, `services/system.py`
- Modify: `services/github_link.py`, `services/account.py`, `routes/app.py`
- Test: whole suite (these are extractions; `test_api_secrets.py`, `test_api_github.py`, `test_api_account.py`, `test_api_routes.py` pin them)

**Interfaces:**
- Produces:
  - `services.locks.busy(project_id) -> Conflict`; `ProjectLocks.acquire(id) -> bool`, `release(id)`, `acquire_or_raise(id)`, `held(id)` context manager.
  - `ProjectLoader(config, projects: ProjectRepo)`: `dir(id) -> Path`, `require(id) -> dict`, `parse_yaml(path) -> dict`, `compose(id) -> dict`, `load(id) -> Project`, `urls_for(project, domain) -> list[str]`, `public_hosts(id) -> list[dict]`.
  - `SecretService(secrets: SecretRepo, loader: ProjectLoader)`: `list(id) -> dict`, `put(id, name, value)`, `request(id, name, hint)`, `delete(id, name)`, `values(id) -> dict | None`, `restart_needed(row) -> bool`, `declared(compose) -> tuple[list[str], dict[str, set[str]]]`, `drop(id)`.
  - `SystemService(config, runner)`: `health() -> dict`, `version() -> dict`, `disk() -> dict`, `connect() -> dict`.
  - `GitHubLink.require_token() -> str`, `GitHubLink.repos(page) -> dict`; `connect()`/`reapply()` raise `Unavailable`/`Upstream`.
  - `Account.before_sign_out` hook; `Account.start_sign_in()` raises `Unavailable`/`Upstream`.

- [ ] **Step 1: `services/locks.py`**

Move `ProjectLocks` from `app.py` verbatim (docstring included) and add:
```python
from __future__ import annotations

import threading
from contextlib import contextmanager

from ..errors import Conflict


def busy(project_id: str) -> Conflict:
    return Conflict("project_busy",
                    f"another operation on '{project_id}' is still running")


class ProjectLocks:
    ...  # acquire / release verbatim

    def acquire_or_raise(self, project_id: str) -> None:
        if not self.acquire(project_id):
            raise busy(project_id)

    @contextmanager
    def held(self, project_id: str):
        self.acquire_or_raise(project_id)
        try:
            yield
        finally:
            self.release(project_id)
```
In `app.py`: delete the class and `_busy`; `from ..services.locks import ProjectLocks, busy as _busy`.

- [ ] **Step 2: `services/loader.py`**

```python
from __future__ import annotations

from pathlib import Path

import yaml

from .. import constants
from ..config import ApiConfig
from ..domain.detect import AmbiguousError
from ..domain.overlay import host_for
from ..domain.project import Project, load_project
from ..errors import BadRequest, EggieError, Invalid, NotFound
from ..infra.repos.projects import ProjectRepo


class ProjectLoader:
    """Reads a project's row and files; depends on no other service, so
    anything may hold one without a cycle."""

    def __init__(self, config: ApiConfig, projects: ProjectRepo):
        self._config = config
        self._projects = projects

    def dir(self, project_id: str) -> Path:
        return Path(self._config.projects_root) / project_id

    def require(self, project_id: str) -> dict:
        row = self._projects.get(project_id)
        if row is None:
            raise NotFound("project_not_found", f"no project with id '{project_id}'")
        return row

    def parse_yaml(self, path: Path) -> dict:
        ...  # verbatim from app.py, ApiError -> Invalid

    def compose(self, project_id: str) -> dict:
        path = self.dir(project_id) / constants.COMPOSE_FILE
        if not path.exists():
            raise BadRequest("compose_missing",
                             f"project '{project_id}' has no {constants.COMPOSE_FILE}")
        return self.parse_yaml(path)

    def load(self, project_id: str) -> Project:
        ...  # verbatim from app.py `load`, using self.compose() for the
             # missing-file check and the parse, ApiError -> Invalid

    def urls_for(self, project: Project, domain: str) -> list[str]:
        return [f"http://{host_for(project.id, web, domain)}:{self._config.edge_port}"
                for web in project.webs]

    def public_hosts(self, project_id: str) -> list[dict]:
        ...  # verbatim from app.py `public_hosts`, `except ApiError` -> `except EggieError`
```
In `app.py`: construct `loader = ProjectLoader(config, repos.projects)` right after `repos`; delete the closures `project_dir`, `require_row`, `parse_yaml`, `load`, `urls_for`, `public_hosts` and replace their uses with `loader.dir`, `loader.require`, `loader.parse_yaml`, `loader.load`, `loader.urls_for`, `loader.public_hosts` (`Public(..., hosts_for=loader.public_hosts)`).

- [ ] **Step 3: `services/secrets.py`**

```python
from __future__ import annotations

from ..domain import secrets as rules
from ..domain.project import CRASH_LOOPING, STARTED_OK
from ..domain.secrets import SecretError
from ..errors import NotFound
from ..infra.repos.secrets import SecretRepo
from .loader import ProjectLoader

# Statuses whose containers exist and were handed the project's secrets.
RUNNING = (STARTED_OK, CRASH_LOOPING)


class SecretService:
    def __init__(self, secrets: SecretRepo, loader: ProjectLoader):
        self._secrets = secrets
        self._loader = loader

    def list(self, project_id: str) -> dict:
        row = self._loader.require(project_id)
        return {"secrets": self._secrets.names(project_id),
                "requested": self._secrets.requests(project_id),
                "restart_needed": self.restart_needed(row)}

    # No project lock: one sqlite statement, and a start in flight is caught
    # by restart_needed because the start is stamped before it reads values.
    def put(self, project_id: str, name: str, value: str) -> None:
        self._loader.require(project_id)
        if value == "":
            raise SecretError("secret_invalid_value",
                              "a value can't be empty; delete the secret instead")
        rules.check_name(name)
        rules.check_value(value)
        rules.check_total({**self._secrets.values(project_id), name: value})
        self._secrets.set(project_id, {name: value})

    def request(self, project_id: str, name: str, hint: str) -> None:
        self._loader.require(project_id)
        rules.check_name(name)
        rules.check_hint(hint)
        self._secrets.request(project_id, name, hint)

    def delete(self, project_id: str, name: str) -> None:
        self._loader.require(project_id)
        removed = self._secrets.delete(project_id, name)
        dismissed = self._secrets.delete_request(project_id, name)
        if not (removed or dismissed):
            raise NotFound("secret_not_found",
                           f"project '{project_id}' has no secret '{name}'")

    def values(self, project_id: str) -> dict[str, str] | None:
        return self._secrets.values(project_id) or None

    def requested_count(self, project_id: str) -> int:
        return len(self._secrets.requests(project_id))

    def restart_needed(self, row: dict) -> bool:
        ...  # verbatim from app.py

    def declared(self, compose: dict):
        return rules.declared(compose)

    def drop(self, project_id: str) -> None:
        self._secrets.drop(project_id)
```
In `app.py`: `secrets_svc = SecretService(repos.secrets, loader)`; the three secret routes become one-line calls (`list_secrets` returns `secrets_svc.list(project_id)`; `put_secret` calls `secrets_svc.put(project_id, name, body.value)`; `request_secret`, `delete_secret` likewise); `project_env(pid)` → `secrets_svc.values(pid)`; `restart_needed(row)` → `secrets_svc.restart_needed(row)`; `secret_rules.declared(compose)` → `secrets_svc.declared(compose)`; `repos.secrets.drop` in delete → `secrets_svc.drop`; `len(repos.secrets.requests(...))` in `payload` → `secrets_svc.requested_count(...)`. Delete the `RUNNING` constant and the `secret_rules` import from `app.py`.

- [ ] **Step 4: `services/system.py`**

```python
from __future__ import annotations

from pathlib import Path

from .. import constants
from ..config import ApiConfig
from ..infra import connect, disk
from ..infra.docker import DOCKER


class SystemService:
    def __init__(self, config: ApiConfig, runner):
        self._config = config
        self._runner = runner

    def health(self) -> dict:
        probe = self._runner.exec([DOCKER, "version", "--format", "{{.Server.Version}}"])
        return {
            "status": "ok",
            "version": self._config.version,
            "api": constants.API_VERSION,
            "docker": {
                "reachable": probe.ok,
                "version": probe.stdout.strip() if probe.ok else "",
                "detail": "" if probe.ok else (probe.stderr or probe.stdout).strip(),
            },
        }

    def version(self) -> dict:
        return {"version": self._config.version}

    def disk(self) -> dict:
        return disk.usage(Path(self._config.projects_root))

    def connect(self) -> dict:
        return connect.facts(Path(self._config.connect_path))
```
In `app.py`: `system = SystemService(config, runner)`; the four routes call it.

- [ ] **Step 5: Move the GitHub and account translations into their services**

`services/github_link.py` — add imports `from ..errors import Conflict, Unavailable, Upstream` and the module-level helpers moved from `app.py`:
```python
def _github_down() -> Unavailable: ...   # verbatim
def _reconnect() -> Conflict: ...        # verbatim
```
and methods:
```python
    def require_token(self) -> str:
        try:
            return self.token()
        except NotConnected:
            if self.status()["state"] == "needs_reconnect":
                raise _reconnect() from None
            raise

    def repos(self, page: int) -> dict:
        token = self.require_token()
        try:
            repos, more = self._github.repos(token, max(page, 1))
        except GitHubUnavailable:
            raise _github_down() from None
        except GitHubError as e:
            if e.code == "bad_credentials":
                self.mark_bad_if_current(token)
                raise _reconnect() from None
            raise Upstream("github_error", "GitHub refused the repository list: "
                           f"{e.message or e.code}") from None
        return {"has_more": more, "repos": [
            {"full_name": r["full_name"], "private": bool(r.get("private")),
             "description": r.get("description"),
             "updated_at": _epoch(r.get("updated_at"))} for r in repos]}
```
with `_epoch` moved from `app.py` (it needs `from datetime import datetime`). Wrap the `self._github.device_code(...)` call in `connect()` with `except GitHubUnavailable: raise _github_down() from None` / `except GitHubError as e: raise Upstream("github_error", f"GitHub would not start a sign-in: {e.message or e.code}") from None`, and the service call in `reapply()` with the same arms using the message `"GitHub would not confirm the connection: …"`. `reapply()`'s `NotConnected` already carries `github_not_connected`.

`services/account.py` — `from ..errors import Unavailable, Upstream`; in `start_sign_in` wrap `self._cloud.device_code(CLIENT_NAME)`:
```python
            try:
                out = self._cloud.device_code(CLIENT_NAME)
            except CloudUnavailable:
                raise Unavailable("cloud_unavailable",
                                  "The Eggie service can't be reached. Check the "
                                  "internet connection and try again.") from None
            except CloudError as e:
                raise Upstream("cloud_error", "The Eggie service would not start "
                               f"a sign-in: {e.message}") from None
```
Add `self.before_sign_out = lambda: None` in `__init__` and at the top of `sign_out()`:
```python
        try:
            self.before_sign_out()
        except Exception:
            log.exception("the sign-out hook failed")
```
In `app.py`: `account.before_sign_out = public.release_all` after `public` is built; `account_sign_out` becomes `return account.sign_out()`; `account_sign_in` becomes `return account.start_sign_in()`; the github routes become `github_link.connect()`, `github_link.reapply()`, `github_link.repos(page)`; `github_clone` uses `github_link.require_token()`. Delete `_github_down`, `_reconnect`, `_epoch`, `require_github_token` from `app.py`.

- [ ] **Step 6: Run the whole suite**

Run: `TMPDIR=/home/ihor/tmp .venv/bin/python -m pytest -q`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add -A runtime/eggie_api tests
git commit -m "$(cat <<'EOF'
Extract locks, loader, secrets and system services from app.py

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: `FileService` and `UploadService`

**Files:**
- Create: `services/files.py`, `services/uploads.py`
- Modify: `routes/app.py`
- Test: `tests/runtime/api/test_api_uploads.py`, `test_files.py` (existing), plus one new test in `test_api_uploads.py` if the race pin is missing

**Interfaces:**
- Produces:
  - `FileService(loader, locks)`: `async extract(id, receive) -> dict`, `list(id, dir) -> dict`, `async write(id, rel_path, receive) -> dict`, `path(id, rel_path) -> Path`, `delete(id, rel_path) -> dict`. `receive` is `Callable[[Path], Awaitable[Path]]`: given a staging directory, returns the temp file it wrote.
  - `UploadService(store, loader, locks)`: `start(id, *, path, size, fingerprint, replace) -> dict`, `list(id) -> dict`, `get(upload_id) -> dict`, `append(upload_id, offset, data) -> dict`, `finish(upload_id) -> dict`, `cancel(upload_id) -> dict`, `drop_project(id)`.

- [ ] **Step 1: Check the race pin exists**

Run: `grep -n "deleted\|cancel" tests/runtime/api/test_api_uploads.py`. If no test finishes an upload whose project was deleted in the meantime, add:
```python
def test_finishing_an_upload_for_a_deleted_project_cancels_it(env):
    _create(env, "blog")
    up = env.client.post("/projects/blog/uploads",
                         json={"path": "a.txt", "size": 2}).json()
    assert env.client.delete("/projects/blog?purge=true").status_code == 200
    r = env.client.patch(f"/uploads/{up['upload_id']}", content=b"ab",
                         headers={"Upload-Offset": "0"})
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "project_not_found"
    assert env.client.get(f"/uploads/{up['upload_id']}").status_code == 404
```
(Purge drops the staged upload already; the point is that a finish arriving after the delete answers 404 and leaves nothing behind.) Run it: PASS today; it stays as the pin.

- [ ] **Step 2: `services/files.py`**

```python
from __future__ import annotations

import os
from pathlib import Path
from typing import Awaitable, Callable

from ..errors import Conflict, DiskFull, NotFound
from ..infra import disk, files
from .loader import ProjectLoader
from .locks import ProjectLocks

Receive = Callable[[Path], Awaitable[Path]]


def disk_full() -> DiskFull:
    return DiskFull("disk_full", "Eggie's disk is full. Free up space in "
                    "the desktop app, then try again.")


class FileService:
    def __init__(self, loader: ProjectLoader, locks: ProjectLocks):
        self._loader = loader
        self._locks = locks

    def path(self, project_id: str, rel_path: str) -> Path:
        self._loader.require(project_id)
        return files.resolve_within(self._loader.dir(project_id), rel_path)

    # Held across the whole body, not just the extract: an archive that lands
    # between the overlay being written and compose reading docker-compose.yml
    # starts a project from two different versions of itself. Refused rather
    # than queued, like every other lock holder here -- the host client retries
    # a `project_busy` on its own, where the wait can be bounded and reported.
    async def extract(self, project_id: str, receive: Receive) -> dict:
        self._loader.require(project_id)
        d = self._loader.dir(project_id)
        with self._locks.held(project_id):
            tmp = await receive(d.parent)
            try:
                try:
                    files.extract_archive(tmp, d)
                except OSError as e:
                    if disk.is_disk_full(e):
                        raise disk_full() from e
                    raise
            finally:
                tmp.unlink(missing_ok=True)
        return {"id": project_id, "files": files.list_tree(d)}

    def list(self, project_id: str, dir: str | None) -> dict:
        ...  # verbatim from app.py `list_files`, with the NotFound/Conflict raises

    async def write(self, project_id: str, rel_path: str, receive: Receive) -> dict:
        target = self.path(project_id, rel_path)
        d = self._loader.dir(project_id)
        with self._locks.held(project_id):
            # Staged next to the project directory, not inside it, so a listing
            # never catches the upload half-written.
            tmp = await receive(d.parent)
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(tmp, target)
            finally:
                tmp.unlink(missing_ok=True)
        return {"path": rel_path, "size": target.stat().st_size}

    def file(self, project_id: str, rel_path: str) -> Path:
        target = self.path(project_id, rel_path)
        if not target.is_file():
            raise NotFound("file_not_found",
                           f"no file '{rel_path}' in project '{project_id}'")
        return target

    def delete(self, project_id: str, rel_path: str) -> dict:
        target = self.file(project_id, rel_path)
        with self._locks.held(project_id):
            target.unlink()
        return {"path": rel_path, "deleted": True}
```
In `app.py`: `files_svc = FileService(loader, locks)`; `_stream_to_tempfile` stays in `app.py` (Task 7 moves it) and `_disk_full` becomes `from ..services.files import disk_full as _disk_full`. Routes: `upload_files` → `return await files_svc.extract(project_id, lambda dir_: _stream_to_tempfile(request, dir_))`; `list_files` → `files_svc.list(project_id, dir)`; `write_file` → `await files_svc.write(project_id, file_path, lambda dir_: _stream_to_tempfile(request, dir_))`; `read_file` → `FileResponse(files_svc.file(...), filename=…)`; `delete_file` → `files_svc.delete(...)`. Delete `resolve_path`.

- [ ] **Step 3: `services/uploads.py`**

```python
from __future__ import annotations

from ..errors import Conflict, NotFound
from ..infra import files
from ..infra.uploads import CHUNK_SIZE, UploadStore
from .loader import ProjectLoader
from .locks import ProjectLocks


class UploadService:
    def __init__(self, store: UploadStore, loader: ProjectLoader, locks: ProjectLocks):
        self._store = store
        self._loader = loader
        self._locks = locks

    def start(self, project_id: str, *, path: str, size: int, fingerprint: str,
              replace: bool) -> dict:
        self._loader.require(project_id)
        target = files.resolve_within(self._loader.dir(project_id), path)
        if target.is_dir():
            # `replace` means "overwrite this file", never "delete this
            # folder and put a file where it was" -- that has no undo.
            raise Conflict("path_is_folder", f"'{path}' is a folder in the project")
        if target.exists() and not replace:
            raise Conflict("file_exists", f"'{path}' is already in the project")
        up = self._store.start(project_id, path, size, fingerprint, replace)
        if up.size == 0:
            return self.finish(up.id)
        return {"upload_id": up.id, "offset": 0, "size": up.size,
                "chunk_size": CHUNK_SIZE, "done": False}

    def list(self, project_id: str) -> dict:
        self._loader.require(project_id)
        self._store.sweep()
        return {"uploads": [u.as_dict() for u in self._store.list_for(project_id)]}

    def get(self, upload_id: str) -> dict:
        return self._store.get(upload_id).as_dict()

    def append(self, upload_id: str, offset: int, data: bytes) -> dict:
        up = self._store.append(upload_id, offset, data)
        if up.offset == up.size:
            return self.finish(upload_id)
        return {"upload_id": upload_id, "offset": up.offset, "size": up.size,
                "done": False}

    def finish(self, upload_id: str) -> dict:
        up = self._store.get(upload_id)
        # The existence check has to happen inside the lock too, not just the
        # write: checked first and locked after, delete could still finish in
        # the gap between the two and this would resurrect the folder it
        # just removed.
        with self._locks.held(up.project_id):
            try:
                self._loader.require(up.project_id)
            except NotFound:
                self._store.cancel(upload_id)
                raise
            try:
                self._store.finish(upload_id, files.resolve_within(
                    self._loader.dir(up.project_id), up.path))
            except PermissionError as e:
                raise Conflict("permission_denied",
                               "Eggie can't write into that folder; a program "
                               "in the project owns it. Pick another folder.") from e
        return {"upload_id": upload_id, "offset": up.size, "size": up.size,
                "done": True}

    def cancel(self, upload_id: str) -> dict:
        self._store.cancel(upload_id)
        return {"upload_id": upload_id, "cancelled": True}

    def drop_project(self, project_id: str) -> None:
        self._store.drop_project(project_id)
```
In `app.py`: `uploads_svc = UploadService(uploads, loader, locks)` (the `UploadStore` stays constructed in `app.py` until Task 7); routes call `uploads_svc.start(project_id, path=body.path, size=body.size, fingerprint=body.fingerprint, replace=body.replace)`, `.list`, `.get`, `.append` (inside `run_in_threadpool`, and the `finish` branch collapses into `append`), `.cancel`; `delete_project` uses `uploads_svc.drop_project`. Delete `finish_upload`.

- [ ] **Step 4: Run the whole suite**

Run: `TMPDIR=/home/ihor/tmp .venv/bin/python -m pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add -A runtime/eggie_api tests
git commit -m "$(cat <<'EOF'
Extract file and upload services from app.py

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: `LifecycleService`, `CloneService`, `ProjectService`

**Files:**
- Create: `services/lifecycle.py`, `services/clone.py`, `services/projects.py`
- Modify: `routes/app.py`, `tests/runtime/api/conftest.py`, `test_api_resume.py`
- Test: `tests/runtime/api/test_api_routes.py`, `test_api_projects_ui.py`, `test_api_github.py`, `test_api_resume.py`, `test_api_public.py` (existing) + two new tests

**Interfaces:**
- Produces:
  - `LifecycleService(config, runner, loader, projects: ProjectRepo, secrets: SecretService, jobs, locks, http_probe)`: `start_work(id, *, stop_first) -> Work`, `submit_locked(id, work, kind) -> str`, `start(id, *, stop_first=False) -> str`, `stop(id) -> str`, `resume_all() -> list[str]`, `logs(id, service) -> str`, `logs_stream(id, service) -> Iterator[str]`.
  - `CloneService(config, runner, loader, projects, locks, jobs, github_link, lifecycle, wake)`: `clone(repo, id) -> dict` (`{"job_id", "id"}`).
  - `ProjectService(config, runner, loader, projects, secrets, jobs, locks, uploads, public, http_probe, wake)`: `payload(row, *, recheck=False) -> dict`, `create(id, web, domain) -> dict`, `adopt(id) -> dict`, `list() -> dict`, `get(id) -> dict`, `delete_preview(id) -> dict`, `delete(id, purge) -> dict`.

- [ ] **Step 1: Write the two new failing tests**

Append to `tests/runtime/api/test_api_github.py` (its `make`, `connected_github` and `finish` helpers; `COMPOSE_MALFORMED` from `conftest`):
```python
def test_clone_lock_is_released_when_start_work_refuses(env):
    # A clone that lands a repo whose compose file will not parse must not
    # leave the project locked: the next `up` has to be able to run.
    client, runner, app, _ = make(env, connected_github())

    def land(dest):
        Path(dest).mkdir(parents=True)
        (Path(dest) / "docker-compose.yml").write_text(COMPOSE_MALFORMED)

    runner.on_clone = land
    job = finish(app, client, client.post("/github/clone", json={"repo": "octo/app"}))
    assert job["state"] == "done", job
    again = client.post("/projects/app/up")
    assert again.status_code == 422
    assert again.json()["error"]["code"] == "invalid_compose"
```
Append to `tests/runtime/api/test_api_projects_ui.py`:
```python
def test_listing_survives_a_public_status_crash(env):
    _create(env, "blog")
    env.app.state.services.public.status = lambda _id: 1 / 0
    r = env.client.get("/projects")
    assert r.status_code == 200
    assert r.json()["projects"][0]["public"] == {"state": "off", "note": None}
```
Run both: the second fails with `AttributeError: services` (wired in Step 5); the first passes today and pins the handover.

- [ ] **Step 2: `services/lifecycle.py`**

```python
from __future__ import annotations

import logging
import time
from typing import Iterator

from ..config import ApiConfig
from ..domain.project import STARTED_OK
from ..errors import Conflict, EggieError
from ..infra import docker
from ..infra.health import diagnose
from ..infra.repos.projects import ProjectRepo
from .jobs import JobFailed, JobRegistry
from .loader import ProjectLoader
from .locks import ProjectLocks, busy
from .secrets import RUNNING, SecretService

log = logging.getLogger("eggie.api")


class LifecycleService:
    def __init__(self, config: ApiConfig, runner, loader: ProjectLoader,
                 projects: ProjectRepo, secrets: SecretService, jobs: JobRegistry,
                 locks: ProjectLocks, http_probe):
        self._config = config
        self._runner = runner
        self._loader = loader
        self._projects = projects
        self._secrets = secrets
        self._jobs = jobs
        self._locks = locks
        self._probe = http_probe

    def submit_locked(self, project_id: str, work, kind: str | None = None) -> str:
        """The job releases the lock itself, in its own `finally`."""
        self._locks.acquire_or_raise(project_id)
        try:
            return self._jobs.submit(work, kind=kind, project_id=project_id)
        except BaseException:
            self._locks.release(project_id)
            raise

    def start_work(self, project_id: str, *, stop_first: bool):
        ...  # verbatim from app.py `start_work`, with:
             #   require_row -> self._loader.require; load -> self._loader.load
             #   parse_yaml(project_dir/COMPOSE_FILE) -> self._loader.compose(project_id)
             #   secret_rules.declared -> self._secrets.declared
             #   project_dir -> self._loader.dir; project_env -> self._secrets.values
             #   lifecycle.compose_down/compose_up -> docker.compose_down/compose_up
             #   state.* -> self._projects.*; runner -> self._runner
             #   urls_for -> self._loader.urls_for; config.* -> self._config.*
             #   http_probe -> self._probe; locks.release -> self._locks.release

    def start(self, project_id: str, *, stop_first: bool = False) -> str:
        return self.submit_locked(project_id,
                                  self.start_work(project_id, stop_first=stop_first),
                                  "restart" if stop_first else "up")

    def stop(self, project_id: str) -> str:
        self._loader.require(project_id)
        directory = self._loader.dir(project_id)

        def work(write):
            ...  # verbatim from app.py `project_down.work`

        return self.submit_locked(project_id, work, "down")

    def resume_all(self) -> list[str]:
        """Projects carry no restart policy, so a VM reboot leaves them stopped
        while state.db still says started and Traefik answers 404."""
        job_ids = []
        for row in self._projects.list():
            if row["status"] != STARTED_OK:
                continue
            try:
                job_ids.append(self.start(row["id"]))
            except EggieError as e:
                log.warning("not resuming %s: %s", row["id"], e.message)
        return job_ids

    def logs(self, project_id: str, service: str | None) -> str:
        self._loader.require(project_id)
        result = docker.project_logs(self._runner, self._loader.dir(project_id),
                                     service, env=self._secrets.values(project_id))
        if not result.ok:
            raise Conflict("logs_unavailable",
                           (result.stderr or result.stdout).strip()
                           or "docker compose logs failed")
        return result.stdout

    def logs_stream(self, project_id: str, service: str | None) -> Iterator[str]:
        self._loader.require(project_id)
        argv = docker.logs_argv(self._loader.dir(project_id), service, follow=True)
        return self._runner.stream(argv, root=True, env=self._secrets.values(project_id))
```

- [ ] **Step 3: `services/clone.py`**

```python
from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path
from typing import Callable

from .. import constants
from ..config import ApiConfig
from ..domain.project import _slug
from ..errors import Conflict, EggieError, Invalid
from ..infra.github import auth_failed, clone_argv, redact, valid_repo
from ..infra.repos.projects import ProjectRepo
from .github_link import GitHubLink
from .jobs import JobFailed, JobRegistry
from .lifecycle import LifecycleService
from .loader import ProjectLoader
from .locks import ProjectLocks


class CloneService:
    def __init__(self, config: ApiConfig, runner, loader: ProjectLoader,
                 projects: ProjectRepo, locks: ProjectLocks, jobs: JobRegistry,
                 github_link: GitHubLink, lifecycle: LifecycleService,
                 wake: Callable[[], None]):
        ...  # store each

    def clone(self, repo: str, id: str | None) -> dict:
        ...  # verbatim from app.py `github_clone` (body.repo -> repo, body.id -> id),
             # ending in `return {"job_id": job_id, "id": project_id}`; the inner
             # `work` uses self._lifecycle.start_work(project_id, stop_first=False)
             # and `self._wake()` for `sync.wake()`
```
Keep the long "Cloned next to the real folder…" comment and the `handed_over` flag exactly.

- [ ] **Step 4: `services/projects.py`**

```python
from __future__ import annotations

import logging
import shutil
from dataclasses import asdict
from pathlib import Path
from typing import Callable

import yaml

from .. import constants
from ..config import ApiConfig
from ..domain.project import _slug
from ..errors import Conflict, EggieError, Invalid, NotFound
from ..infra import docker, files
from ..infra.health import answers
from ..infra.reconcile import discover, examine
from ..infra.repos.projects import ProjectRepo
from .jobs import JobRegistry
from .loader import ProjectLoader
from .locks import ProjectLocks
from .public import Public
from .secrets import SecretService
from .uploads import UploadService

log = logging.getLogger("eggie.api")


class ProjectService:
    def __init__(self, config: ApiConfig, runner, loader: ProjectLoader,
                 projects: ProjectRepo, secrets: SecretService, jobs: JobRegistry,
                 locks: ProjectLocks, uploads: UploadService, public: Public,
                 http_probe, wake: Callable[[], None]):
        ...  # store each

    def payload(self, row: dict, *, recheck: bool = False) -> dict:
        ...  # verbatim from app.py `payload`, every closure variable -> self._*,
             # `except ApiError` -> `except EggieError`,
             # `len(state.secret_requests(...))` -> self._secrets.requested_count(...)

    def create(self, id: str, web: list[dict] | None, domain: str | None) -> dict:
        ...  # verbatim from app.py `create_project`; `web` is already a list of dicts

    def adopt(self, project_id: str) -> dict: ...      # verbatim
    def list(self) -> dict: ...                        # verbatim `list_projects`
    def get(self, project_id: str) -> dict:
        return self.payload(self._loader.require(project_id), recheck=True)

    def _compose_name(self, project_id: str, row: dict) -> str:
        return docker.resolve_compose_name(
            self._runner, self._loader.dir(project_id),
            row.get("compose_name") or row["id"])

    def delete_preview(self, project_id: str) -> dict: ...   # verbatim
    def delete(self, project_id: str, *, purge: bool) -> dict: ...  # verbatim, with
        # self._public.disable(..., force=True), self._uploads.drop_project,
        # self._secrets.drop, self._projects.remove, self._wake()
```

- [ ] **Step 5: Wire them in `app.py` and shrink the routes**

In `create_app`, after `public`, `sync`, `secrets_svc`, `uploads_svc`:
```python
    lifecycle_svc = LifecycleService(config, runner, loader, repos.projects,
                                     secrets_svc, jobs, locks, http_probe)
    clone_svc = CloneService(config, runner, loader, repos.projects, locks, jobs,
                             github_link, lifecycle_svc, sync.wake)
    projects_svc = ProjectService(config, runner, loader, repos.projects, secrets_svc,
                                  jobs, locks, uploads_svc, public, http_probe, sync.wake)
```
Expose them for the entrypoint and tests as a plain namespace for now (Task 7 replaces it with the `Services` dataclass):
```python
    app.state.services = SimpleNamespace(
        repos=repos, jobs=jobs, public=public, account=account, sync=sync,
        lifecycle=lifecycle_svc, projects=projects_svc)
```
Routes become one-liners: `project_up` → `{"job_id": lifecycle_svc.start(project_id)}`, `project_restart` → `stop_first=True`, `project_down` → `lifecycle_svc.stop`, `project_logs` → `PlainTextResponse(lifecycle_svc.logs(...))` / `StreamingResponse(lifecycle_svc.logs_stream(...))`, `github_clone` → `return clone_svc.clone(body.repo, body.id)` (the `valid_repo`, slug and exists checks live in `CloneService.clone`). `create_project` → `projects_svc.create(body.id, [w.model_dump() for w in body.web] if body.web else None, body.domain)`, `adopt_project`, `list_projects`, `get_project`, `delete_preview`, `delete_project` → the matching methods; `public_status` → `loader.require(project_id); return public.status(project_id)` and the two console routes likewise. Delete from `app.py`: `payload`, `submit_locked`, `start_work`, `resume_projects`, `compose_name_for`, `resolve_compose_name`, `restart_needed`, `project_env`, the `status_code`-less helpers; replace `app.state.resume_projects = resume_projects` with nothing (the entrypoint calls `app.state.services.lifecycle.resume_all()`).

`routes/__main__.py`: `app.state.account.resume()` → `app.state.services.account.resume()`, `app.state.sync.start()` → `app.state.services.sync.start()`, `app.state.resume_projects()` → `app.state.services.lifecycle.resume_all()`.

Tests: `conftest.py` `repos=app.state.repos` → `repos=app.state.services.repos`, `jobs=app.state.jobs` → `jobs=app.state.services.jobs`; `test_api_resume.py:10` → `env.app.state.services.lifecycle.resume_all()`; `test_api_public.py:156` → `app.state.services.sync._run()` (unchanged attribute until Task 7 renames it); `test_api_github.py:40` → `app.state.services.jobs.wait(...)`.

- [ ] **Step 6: Run the whole suite**

Run: `TMPDIR=/home/ihor/tmp .venv/bin/python -m pytest -q`
Expected: all pass, including the two tests from Step 1. `wc -l runtime/eggie_api/routes/app.py` should be around 450 lines: wiring, auth middleware, handlers, schemas, routes.

- [ ] **Step 7: Commit**

```bash
git add -A runtime/eggie_api tests
git commit -m "$(cat <<'EOF'
Extract lifecycle, clone and project services from app.py

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: `http/` package, `wiring.py`, `__main__.py`

**Files:**
- Create: `http/__init__.py`, `http/app.py`, `http/auth.py`, `http/errors.py`, `http/schemas.py`, `http/routers/__init__.py`, `http/routers/{system,projects,lifecycle,files,uploads,secrets,jobs,account,github,public,agents,sessions}.py`, `wiring.py`, `__main__.py`
- Delete: `routes/app.py`, `routes/__main__.py`, `routes/__init__.py`
- Modify: `services/sync.py` (`SyncLoop(passes)`), `pyproject.toml`, `Dockerfile`, `Dockerfile.debug`, `tests/runtime/api/conftest.py`, every test importing `eggie_api.routes.app`, `test_api_public.py`
- Test: whole suite; `tests/runtime/api/test_dockerfile.py` gets one assertion

**Interfaces:**
- Produces: `eggie_api.wiring.Services` (frozen dataclass), `eggie_api.wiring.build(config, *, runner=None, http_probe=None, cloud=None, github=None, account=None, public=None, github_link=None, sessions=None, jobs=None) -> Services`; `eggie_api.http.app.create_app(*, config=None, services=None, **overrides) -> FastAPI`; `app.state.services`.
- `SyncLoop(passes: list[Callable[[], None]], *, interval=60.0)` with `wake()`, `run_once()`, `run_forever()`, `start()`.

- [ ] **Step 1: `SyncLoop` takes a list of passes**

`services/sync.py`:
```python
class SyncLoop:
    def __init__(self, passes: list[Callable[[], None]], *, interval: float = 60.0):
        self._passes = list(passes)
        self._interval = interval
        self._wake = threading.Event()

    def wake(self) -> None:
        self._wake.set()

    def run_once(self) -> None:
        # Each pass fails on its own: a broken public-URL check must not
        # stop projects from being registered.
        for run in self._passes:
            try:
                run()
            except Exception:
                log.exception("sync pass failed")

    def run_forever(self) -> None:
        while True:
            # Cleared before the pass: a change made during it runs another.
            self._wake.clear()
            self.run_once()
            self._wake.wait(self._interval)

    def start(self) -> None:
        threading.Thread(target=self.run_forever, name="eggie-sync",
                         daemon=True).start()
```
(`from typing import Callable`.) `test_api_public.py:156` → `app.state.services.sync.run_once()`. If `test_sync.py` constructs `SyncLoop(fn)`, pass `[fn]`.

- [ ] **Step 2: `wiring.py`**

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .config import ApiConfig
from .infra import disk
from .infra.cloud import Cloud
from .infra.db import Database
from .infra.github import GitHub
from .infra.health import default_probe
from .infra.repos import Repos
from .infra.runner import LocalRunner
from .infra.tunnel import TunnelClient
from .infra.uploads import UploadStore
from .services.account import Account
from .services.agents import AgentStatus
from .services.clone import CloneService
from .services.files import FileService
from .services.github_link import GitHubLink
from .services.jobs import JobRegistry
from .services.lifecycle import LifecycleService
from .services.loader import ProjectLoader
from .services.locks import ProjectLocks
from .services.projects import ProjectService
from .services.public import Public
from .services.secrets import SecretService
from .services.sessions import Sessions
from .services.sync import SyncLoop, run_pass
from .services.system import SystemService
from .services.uploads import UploadService


@dataclass(frozen=True)
class Services:
    config: ApiConfig
    repos: Repos
    runner: object
    jobs: JobRegistry
    locks: ProjectLocks
    loader: ProjectLoader
    projects: ProjectService
    lifecycle: LifecycleService
    clone: CloneService
    files: FileService
    uploads: UploadService
    secrets: SecretService
    account: Account
    public: Public
    github_link: GitHubLink
    sessions: Sessions
    agents: AgentStatus
    sync: SyncLoop
    system: SystemService


def build(config: ApiConfig, *, runner=None, http_probe=None, cloud=None,
          github=None, account=None, public=None, github_link=None,
          sessions=None, jobs=None, repos=None) -> Services:
    """The one place that knows the whole graph. Keyword arguments are the
    fakes tests inject; production passes none."""
    runner = runner or LocalRunner()
    http_probe = http_probe or default_probe
    repos = repos if repos is not None else Repos.open(Database(config.state_db))
    jobs = jobs or JobRegistry()
    locks = ProjectLocks()
    loader = ProjectLoader(config, repos.projects)
    sessions = sessions if sessions is not None else Sessions(repos.sessions)
    store = UploadStore(config.uploads_root,
                        free_bytes=lambda: disk.usage(
                            Path(config.projects_root))["free_bytes"])
    store.sweep()
    uploads = UploadService(store, loader, locks)
    secrets = SecretService(repos.secrets, loader)
    cloud = cloud or Cloud(config.cloud_url)
    account = account or Account(repos.account, repos.cloud_projects, cloud)
    public = public or Public(
        projects=repos.projects, cloud_projects=repos.cloud_projects,
        account=account, cloud=cloud,
        client=TunnelClient(runner, config.stack_file),
        token_path=config.tunnel_token_path,
        origin=f"http://{config.traefik_host}:{config.edge_port}",
        hosts_for=loader.public_hosts)
    account.on_forget = public.forget_local
    account.before_sign_out = public.release_all
    sync = SyncLoop([public.reconcile, lambda: run_pass(account, cloud, repos)])
    account.on_signed_in = sync.wake
    github = github or GitHub(config.github_url, config.github_api_url)
    github_link = github_link or GitHubLink(
        repos.github, github, client_id=config.github_client_id,
        directory=config.github_dir)
    lifecycle = LifecycleService(config, runner, loader, repos.projects, secrets,
                                 jobs, locks, http_probe)
    return Services(
        config=config, repos=repos, runner=runner, jobs=jobs, locks=locks,
        loader=loader,
        projects=ProjectService(config, runner, loader, repos.projects, secrets,
                                jobs, locks, uploads, public, http_probe, sync.wake),
        lifecycle=lifecycle,
        clone=CloneService(config, runner, loader, repos.projects, locks, jobs,
                           github_link, lifecycle, sync.wake),
        files=FileService(loader, locks), uploads=uploads, secrets=secrets,
        account=account, public=public, github_link=github_link,
        sessions=sessions, agents=AgentStatus(config.agent_status_dir, config.agents_dir),
        sync=sync, system=SystemService(config, runner))
```

- [ ] **Step 3: `http/errors.py`, `http/auth.py`, `http/schemas.py`**

`http/errors.py`: `STATUS`, `_status_of` → `status_of`, `error_body(code, message, status, **extra) -> JSONResponse`, `_validation_message`, and `install(app)` registering the four handlers from `app.py` (`EggieError`, `RequestValidationError`, `StarletteHTTPException`, `Exception`) verbatim.

`http/auth.py`:
```python
def read_token(path: Path) -> str: ...   # verbatim `_read_token`, docstring included

def install(app: FastAPI, *, token: str, sessions: Sessions, config: ApiConfig) -> None:
    allowed_hosts = ...                   # verbatim
    allowed_origins = ...
    open_browser_routes = ...
    def _bearer_ok(request): ...          # verbatim, comment included
    def _browser_refusal(request): ...    # verbatim
    @app.middleware("http")
    async def _authenticate(request, call_next): ...  # verbatim
```
Keep the "Read once at startup, not per request…" comment above the `read_token` call in `http/app.py`.

`http/schemas.py`: the seven pydantic models verbatim.

- [ ] **Step 4: `http/routers/*.py`**

Each module: `from fastapi import APIRouter` plus what it needs, and `def build(s: Services) -> APIRouter` (import `Services` under `TYPE_CHECKING` from `...wiring` to avoid an import cycle at runtime). Bodies are the route functions from `app.py`, now one call each. The full list, so nothing is dropped:

- `system.py`: `GET /health`, `GET /version`, `GET /disk`, `GET /connect`, `GET /agents/status`, `POST /agents/{agent_id}/setup` → `s.system.*`, `s.agents.status()`, `s.agents.ensure_setup(agent_id)`.
- `account.py`: `GET /account`, `POST /account/sign-in`, `POST /account/sign-out`.
- `github.py`: `GET /github`, `POST /github/connect`, `POST /github/disconnect`, `POST /github/reapply`, `GET /github/repos`, `POST /github/clone` (202).
- `projects.py`: `POST /projects` (201), `POST /projects/{id}/adopt` (201), `GET /projects`, `GET /projects/{id}`, `GET /projects/{id}/delete-preview`, `DELETE /projects/{id}`.
- `lifecycle.py`: `POST /projects/{id}/up|restart|down` (202), `GET /projects/{id}/logs` (`TEXT = "text/plain; charset=utf-8"` lives here and in `jobs.py`).
- `files.py`: `POST /projects/{id}/files`, `GET /projects/{id}/files`, `PUT|GET|DELETE /projects/{id}/files/{file_path:path}`, with `stream_to_tempfile(request, dir_, *, max_bytes)` and `_size_words` moved here verbatim (`TooLarge` for the cap, `disk_full()` from `services.files`).
- `uploads.py`: `POST /projects/{id}/uploads` (201), `GET /projects/{id}/uploads`, `GET|PATCH|DELETE /uploads/{upload_id}`, with the chunk reader (`BadRequest("invalid_request", "Upload-Offset must be a number")`, `TooLarge("payload_too_large", …)`) verbatim.
- `secrets.py`: `GET /projects/{id}/secrets`, `PUT /projects/{id}/secrets/{name}` (204), `PUT /projects/{id}/secret-requests/{name}` (204), `DELETE /projects/{id}/secrets/{name}` (204).
- `jobs.py`: `GET /jobs/{job_id}`, `GET /jobs/{job_id}/logs` with the `require_job` helper (`NotFound("job_not_found", …)`).
- `public.py`: `build(s)` → `GET /projects/{id}/public`; `build_console(s)` → `POST` (202) and `DELETE /projects/{id}/public`, with the "Mounted only at /api…" comment.
- `sessions.py`: `build(s)` returns a router with `POST /sessions/handoff`, `POST /api/sessions/handoff`, `POST /api/session`, `GET /api/session`, `DELETE /api/session` — mounted once at `/` (these paths carry their own `/api` prefix, as today).

- [ ] **Step 5: `http/app.py` and `__main__.py`**

```python
from __future__ import annotations

from fastapi import FastAPI

from ..config import ApiConfig
from ..wiring import Services, build
from . import auth, errors
from .routers import (account, files, github, jobs, lifecycle, projects, public,
                      secrets, sessions, system, uploads)

FEATURES = (system, account, github, projects, lifecycle, files, uploads,
            secrets, jobs, public)


def create_app(*, config: ApiConfig | None = None, services: Services | None = None,
               **overrides) -> FastAPI:
    """A factory on purpose: no module-level app, so importing this opens no
    sqlite file and starts no thread."""
    config = config or ApiConfig.from_env()
    services = services or build(config, **overrides)
    app = FastAPI(title="eggie-api", version=config.version)
    app.state.services = services
    errors.install(app)
    # Read once at startup, not per request. ...  (comment verbatim)
    auth.install(app, token=auth.read_token(config.token_path),
                 sessions=services.sessions, config=config)
    for feature in FEATURES:
        router = feature.build(services)
        app.include_router(router)
        app.include_router(router, prefix="/api")
    app.include_router(public.build_console(services), prefix="/api")
    app.include_router(sessions.build(services))
    return app
```

`eggie_api/__main__.py`:
```python
from __future__ import annotations

import uvicorn

from .config import ApiConfig
from .http.app import create_app
from .infra.migrate import SchemaTooNew
from .wiring import build


def main() -> None:
    config = ApiConfig.from_env()
    try:
        services = build(config)
    except SchemaTooNew as e:
        # `restart: always` loops this container, so the log is all anyone has
        # to go on: one line that says what to do, not a traceback.
        raise SystemExit(f"eggie-api will not start: {e}") from None
    app = create_app(config=config, services=services)
    services.account.resume()
    services.sync.start()
    services.lifecycle.resume_all()
    # Defaults to 0.0.0.0 because the host reaches the API through the VM's
    # port mapping; narrowing the bind address is a later task's decision.
    uvicorn.run(app, host=config.bind_host, port=config.port)


if __name__ == "__main__":
    main()
```
Then `git rm -r runtime/eggie_api/routes`.

- [ ] **Step 6: Packaging and tests**

`pyproject.toml`: `packages = ["eggie_api", "eggie_api.domain", "eggie_api.infra", "eggie_api.infra.repos", "eggie_api.services", "eggie_api.http", "eggie_api.http.routers"]`.
`Dockerfile`: replace `COPY routes /app/routes` with `COPY http /app/http`, add `__main__.py errors.py wiring.py` to the `COPY config.py constants.py /app/` line; `CMD ["python", "-m", "eggie_api"]`. `Dockerfile.debug`: `"-m", "eggie_api"`.
Add to `tests/runtime/api/test_dockerfile.py`:
```python
def test_dockerfile_starts_the_package_entrypoint():
    assert '"-m", "eggie_api"]' in _text()
```
Tests: `sed -i 's/eggie_api\.routes\.app/eggie_api.http.app/g' tests/runtime/api/*.py`; `conftest.py` keeps `create_app(config=config, runner=runner, http_probe=probe)` and reads `app.state.services.repos` / `.jobs`.

- [ ] **Step 7: Run the whole suite and build the image**

Run: `TMPDIR=/home/ihor/tmp .venv/bin/python -m pytest -q` → all pass.
Run: `cd runtime/eggie_api && docker build -q . && cd -` (skip with a note in the PR if Docker is not available in this shell).

- [ ] **Step 8: Commit**

```bash
git add -A runtime/eggie_api tests
git commit -m "$(cat <<'EOF'
Split the HTTP layer into routers and add the composition root

create_app only builds FastAPI; wiring.build() owns the object graph;
python -m eggie_api is the entrypoint.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: Layering test and docs

**Files:**
- Create: `tests/runtime/api/test_layering.py`
- Modify: `runtime/eggie_api/CLAUDE.md`, `docs/releasing.md:32`, `runtime/stack.yml:16`, `runtime/eggie_api/config.py` docstring (mentions `core/lifecycle.py` and `routes/app.py`), `runtime/eggie_api/infra/runner.py` docstring (`core/lifecycle.py`), `tests/runtime/api/test_dockerfile.py` comment (`core/lifecycle.py`), `tests/test_no_platform_leak.py` if it names a path, `docs/architecture.md` if it names one (`grep -rn "eggie_api/core\|routes/app\|eggie_api.routes" docs/ runtime/ tests/ CLAUDE.md`).

- [ ] **Step 1: Write the layering test**

```python
"""The one dependency edge inside eggie_api: http -> services -> domain | infra."""
import ast
from pathlib import Path

API = Path(__file__).resolve().parents[3] / "runtime" / "eggie_api"
IO_LIBS = {"sqlite3", "subprocess", "urllib", "fastapi", "starlette", "pydantic"}
WEB_LIBS = {"fastapi", "starlette", "pydantic"}


def _imports(py: Path):
    tree = ast.parse(py.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                # Relative: resolve against the file's package so `..infra.db`
                # from services/x.py reads as `eggie_api.infra.db`.
                parts = py.relative_to(API).with_suffix("").parts[:-node.level]
                yield "eggie_api." + ".".join((*parts, node.module or "")).strip(".")
            else:
                yield node.module or ""


def _layer(py: Path) -> str:
    rel = py.relative_to(API).parts
    return rel[0] if len(rel) > 1 else rel[0].removesuffix(".py")


def _target(name: str) -> str:
    """The layer (`infra`, `services`, …) for a package import, the top-level
    library name for anything else."""
    parts = name.split(".")
    if parts[0] == "eggie_api":
        return parts[1] if len(parts) > 1 else ""
    return parts[0]


def test_layers_only_depend_downward():
    forbidden = {
        "domain": {"infra", "services", "http", *IO_LIBS},
        "infra": {"services", "http", *WEB_LIBS},
        "services": {"http", *WEB_LIBS},
        "config": {"services", "http"},
        "constants": {"domain", "infra", "services", "http"},
        "errors": {"domain", "infra", "services", "http"},
    }
    offenders = []
    files = [p for p in API.rglob("*.py") if "__pycache__" not in p.parts]
    assert len(files) >= 40, f"scanned only {len(files)} files under {API}"
    for py in files:
        layer = _layer(py)
        for name in _imports(py):
            if _target(name) in forbidden.get(layer, set()):
                offenders.append(f"{py.relative_to(API)} imports {name}")
    assert not offenders, "\n".join(offenders)


def test_only_http_wiring_and_main_import_services():
    allowed = {"http", "wiring", "__main__", "services"}
    offenders = [f"{py.relative_to(API)} imports {name}"
                 for py in API.rglob("*.py") if "__pycache__" not in py.parts
                 for name in _imports(py)
                 if _target(name) == "services" and _layer(py) not in allowed]
    assert not offenders, "\n".join(offenders)
```

Run: `… tests/runtime/api/test_layering.py -q` → PASS. To prove it can fail, temporarily add `import fastapi` to `services/jobs.py`, run again (FAIL naming the file), revert.

- [ ] **Step 2: Rewrite `runtime/eggie_api/CLAUDE.md` "Shape"**

Replace the "Shape" section with:

```markdown
## Shape

One dependency edge: `http → services → domain | infra`. `tests/runtime/api/test_layering.py`
fails on anything else.

- `domain/` — pure logic, no I/O: compose parsing (`compose.py`), web-service detection
  (`detect.py`), Traefik overlay generation (`overlay.py`), project identity/slug and failure
  classification (`project.py`), secret rules (`secrets.py`), the sync plan (`sync.py`).
- `infra/` — I/O adapters, no business rules: `runner.py` (`LocalRunner`, the in-VM twin of the
  host's `VmProvider.exec`: same `Completed` contract, never raises; inject a fake in tests),
  `db.py` + `repos/` (one repository per sqlite table over one shared connection), `migrate.py`,
  `docker.py` (compose and label commands), `cloud.py` / `github.py` (HTTP clients), `files.py`,
  `uploads.py` (`UploadStore`), `tunnel.py`, `health.py` (readiness probes), `disk.py`,
  `connect.py`, `reconcile.py`.
- `services/` — one class per feature area, holding state, locks and threads; takes its
  dependencies in the constructor. `loader.py` is the read-only project reader everything else
  may hold; `lifecycle.py` owns start/stop jobs and the per-project lock contract; `jobs.py` is
  the in-process job registry; `locks.py` the non-blocking per-project locks.
- `http/` — the only tree importing FastAPI. `app.py`'s `create_app` is a **factory on
  purpose**: no module-level `app`. `auth.py` is the middleware, `errors.py` the one
  class → status map, `schemas.py` the request models, `routers/<feature>.py` one `build(services)`
  per feature. A router parses the request, calls one service method and shapes the response:
  no `try/except`, no locks, no business branching.
- `wiring.py` — `build(config, **fakes) -> Services`, the composition root; `__main__.py` is the
  uvicorn entrypoint (`python -m eggie_api`) and the only place background threads start.
- `errors.py` — `EggieError(code, message)` and its subclasses (`NotFound`, `Conflict`,
  `Invalid`, …). Anything below `http/` that wants the caller to act raises one with the wire
  code; the status comes from the class. Transport exceptions (`CloudError`, `GitHubError`,
  `NotSignedIn`, `JobFailed`) never reach HTTP — the calling service translates.
- `config.py` — `ApiConfig`, the **only** place the domain and edge port may come from.
- `constants.py` — values shared with the host and the guest CLI, held equal by
  `tests/test_constants_agree.py`. `API_VERSION` here is the wire protocol, not the release
  number (see `runtime/CLAUDE.md`).

### Adding a feature area

1. A table → `infra/migrate.py` migration + `infra/repos/<name>.py`, added to `Repos`.
2. `services/<name>.py` taking the repos and services it needs; raise `errors.*` subclasses.
3. `http/routers/<name>.py` with `build(services)`; add it to `FEATURES` in `http/app.py`.
4. Construct it in `wiring.build()` and add the field to `Services`.
```

Update the remaining path mentions in that file (`core/secrets.py` → `domain/secrets.py` + `services/secrets.py`, `core/files.py` → `infra/files.py` + `services/files.py`, `core/uploads.py` → `infra/uploads.py` + `services/uploads.py`, `core/reconcile.py` → `infra/reconcile.py`, `core/connect.py` → `infra/connect.py`, `core/cloud.py, account.py, sync.py` → `infra/cloud.py`, `services/account.py`, `services/sync.py`, `core/github.py, github_link.py` → `infra/github.py`, `services/github_link.py`, `core/public.py` → `services/public.py` + `infra/tunnel.py`, `core/agents.py` → `services/agents.py`, `resume_projects()` → `LifecycleService.resume_all()`).

- [ ] **Step 3: Update the other path mentions**

`docs/releasing.md:32`: `runtime/eggie_api/core/constants.py` → `runtime/eggie_api/constants.py`. `runtime/stack.yml:16`: `eggie_api/core/overlay.py` → `eggie_api/domain/overlay.py`. `config.py` docstring: `core/lifecycle.py` → `infra/docker.py`, `eggie_api/routes/app.py's auth check` → `eggie_api/http/auth.py`. `infra/runner.py` docstring: `core/lifecycle.py` → `infra/docker.py`. `test_dockerfile.py` comment: `core/lifecycle.py` → `infra/docker.py`. Run the grep from the Files list once more; it must print nothing outside `docs/superpowers/`.

- [ ] **Step 4: Run the whole suite**

Run: `TMPDIR=/home/ihor/tmp .venv/bin/python -m pytest -q` → all pass.

- [ ] **Step 5: Commit, push, open the PR**

```bash
git add -A
git commit -m "$(cat <<'EOF'
Enforce the eggie_api layering and describe the new shape

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
EOF
)"
git push -u origin feature/57-api-refactor
gh pr create --base main --title "Refactor eggie_api into layered modules" --body "$(cat <<'EOF'
Closes #57.

`runtime/eggie_api/` is now `domain/` (pure logic), `infra/` (I/O adapters, one repository per table), `services/` (one class per feature area) and `http/` (FastAPI only), with `wiring.build()` as the composition root and `errors.py` as the single error hierarchy mapped to HTTP in one place. The wire contract is unchanged; the existing test suite is the regression net (import paths and `State` construction are the only test edits), plus `test_layering.py` for the dependency rule.

Design: `docs/superpowers/specs/2026-10-08-api-refactor-design.md`.

Left untested on purpose: the moved code is covered by the tests it already had; no tests were added for pass-through routers.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

Then dispatch the separate review agent on the PR (user rule).
