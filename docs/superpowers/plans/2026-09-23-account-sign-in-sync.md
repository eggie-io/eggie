# Account Sign-in and Project Registry Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The web console is locked until the user signs in to the Eggie service by code/QR, and every local project gets a record on that service that is created and deleted with it.

**Architecture:** Everything lives in the runtime API (`runtime/eggie_api`). `core/cloud.py` is a stdlib HTTP client for the service; `core/account.py` runs the RFC 8628 device flow and keeps tokens in `state.db`; `core/sync.py` is a reconcile loop on a daemon thread that creates and deletes service records. The console asks the local API `GET /api/account` at boot and shows a sign-in screen for anything but `signed_in`. No host change.

**Tech Stack:** Python 3.12, FastAPI, sqlite3, `urllib.request`; React 19 + Vite + Vitest + msw; `qrcode-generator` 2.0.4.

**Spec:** `docs/superpowers/specs/2026-09-23-account-sign-in-sync-design.md`

## Global Constraints

- Service base URL default: `https://app.eggie.io/api`, overridable by env `EGGIE_CLOUD_URL`. Nothing else may hardcode it.
- **No client-side workarounds for service gaps** (spec decision 10). Never rewrite, repair or substitute a URL or response from the service. The one allowed check is refusing a non-`https:` sign-in link in the console.
- `host/` is not touched. `host/` never imports `eggie_api` and vice versa (existing AST tests).
- No `sys.platform` / `platform.system()` / `os.name` anywhere in `runtime/eggie_api/` (`tests/test_no_platform_leak.py`).
- The API's only new dependency is none: `cloud.py` uses stdlib `urllib` only.
- `create_app()` must not start a thread. Threads start from `routes/__main__.py`.
- `API_VERSION` does not change; the new routes are additive and the host never calls them.
- The console page must work offline: no load from another host (`npm run check-offline`), assets as files.
- Tests: no network, no real VM. Follow the repo's testing rules in `CLAUDE.md` — test logic with branches, not glue.
- Comments: only for non-obvious edge cases; never mention tickets, issues or docs.
- Run Python tests with `TMPDIR=<writable dir>` in this WSL sandbox (e.g. `TMPDIR=$PWD/.tmp`, create it first).
- Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **API restarted while a sign-in code is pending** — the user approves on their phone and nothing polls; expected: the poller resumes at startup (`Account.resume()`), test in Task 3.
2. **The install smoke-test project `eggie-selftest`** — every install creates and deletes it; expected: never sent to the service, test in Task 4.
3. **Access token rejected mid-use (`401 invalid_token`)** — expected: one refresh and one retry, and a second 401 surfaces as an error rather than looping, test in Task 3.
4. **A service record deleted by someone else** — local delete then answers 404; expected: the mapping is dropped as if deleted, test in Task 4.
5. **A proxy in front of the service answering 502/503/504 with an HTML page** — expected: treated as "unreachable" (account stays signed in), not as a service refusal, test in Task 2.

## File Structure

| File | Responsibility |
|---|---|
| `runtime/eggie_api/core/migrate.py` (modify) | `_v4_account`: `account` row with `device_id`, `cloud_projects` mapping |
| `runtime/eggie_api/core/state.py` (modify) | read/update the account row; read/write the mapping |
| `runtime/eggie_api/core/config.py` (modify) | `cloud_url` |
| `runtime/eggie_api/core/cloud.py` (create) | the service client; error body → `CloudError`, unreachable → `CloudUnavailable` |
| `runtime/eggie_api/core/account.py` (create) | device flow, poller, token refresh, sign-out |
| `runtime/eggie_api/core/sync.py` (create) | `plan()`, `apply()`, `run_pass()`, `SyncLoop` |
| `runtime/eggie_api/routes/app.py` (modify) | `/account` routes, wiring, wake sync on create/adopt/delete |
| `runtime/eggie_api/routes/__main__.py` (modify) | resume poller, start sync thread |
| `tests/runtime/api/fake_cloud.py` (create) | scripted `FakeCloud` shared by account/sync/route tests |
| `tests/runtime/api/test_cloud.py`, `test_account.py`, `test_sync.py`, `test_api_account.py` (create) | tests |
| `tests/runtime/api/test_migrate.py` (modify) | v3 → v4 keeps projects |
| `runtime/web/apps/console/src/account/account.ts` (create) | `Account` type, `signInLink()`, `signInError()` |
| `runtime/web/apps/console/src/account/account.test.ts` (create) | link validation |
| `runtime/web/apps/console/src/boot/boot.ts` (modify) | `needsAccount` |
| `runtime/web/apps/console/src/boot/boot.test.ts` (modify) | new outcomes |
| `runtime/web/apps/console/src/components/Qr.tsx` (create) | QR as inline SVG |
| `runtime/web/apps/console/src/screens/account/SignIn.tsx` + `SignIn.module.css` (create) | the sign-in screen |
| `runtime/web/apps/console/src/shell/AccountMenu.tsx` (create) | email + Sign out in the header |
| `runtime/web/apps/console/src/shell/Shell.tsx`, `Shell.module.css` (modify) | header slot for the menu |
| `runtime/web/apps/console/src/App.tsx` (modify) | render `SignIn`, pass the menu |
| `runtime/web/apps/console/src/mocks/handlers.ts` (modify) | `/api/account*` and four scenarios |
| `CLAUDE.md` (modify) | architecture notes for account and sync |

---

### Task 1: Storage — account row and project mapping

**Files:**
- Modify: `runtime/eggie_api/core/migrate.py`
- Modify: `runtime/eggie_api/core/state.py`
- Test: `tests/runtime/api/test_migrate.py`, `tests/runtime/api/test_state.py`

**Interfaces:**
- Produces:
  - `State.get_account() -> dict` — the single `account` row as a dict (always exists after migration). Keys: `device_id, email, org_id, access_token, refresh_token, access_expires_at, device_code, user_code, verification_url, code_expires_at, poll_interval, last_error, sync_ok_at, sync_error`.
  - `State.update_account(**fields) -> None` — `ValueError` on an unknown field name.
  - `State.cloud_mapping() -> dict[str, dict]` — `{local_id: {"cloud_id": str, "org_id": str}}`.
  - `State.map_cloud_project(local_id: str, cloud_id: str, org_id: str) -> None`
  - `State.unmap_cloud_project(local_id: str) -> None`
  - `State.clear_cloud_projects() -> None`

- [ ] **Step 1: Write the failing tests**

Append to `tests/runtime/api/test_migrate.py`:

```python
def _v3_database(path):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
    for step in migrate.MIGRATIONS[:3]:
        step(conn)
    conn.execute("INSERT INTO schema_version(version) VALUES (3)")
    conn.execute("INSERT INTO projects(id, guest_path, domain, status) "
                 "VALUES ('blog', '/g/blog', 'd.io', 'started_ok')")
    conn.commit()
    conn.close()


def test_a_v3_database_gains_an_account_and_keeps_its_projects(tmp_path):
    db = tmp_path / "state.db"
    _v3_database(db)

    state = State(db)
    assert state.get_project("blog")["status"] == "started_ok"
    account = state.get_account()
    assert account["device_id"], "a device id is minted once, by the migration"
    assert account["access_token"] is None
    assert state.cloud_mapping() == {}
```

Append to `tests/runtime/api/test_state.py` (keep its existing imports; add `import pytest` if missing):

```python
def test_the_device_id_survives_reopening_the_database(tmp_path):
    first = State(tmp_path / "state.db").get_account()["device_id"]
    assert State(tmp_path / "state.db").get_account()["device_id"] == first


def test_update_account_refuses_a_field_it_does_not_know(tmp_path):
    state = State(tmp_path / "state.db")
    with pytest.raises(ValueError):
        state.update_account(**{"email=NULL; --": "x"})
```

- [ ] **Step 2: Run them to see them fail**

Run: `mkdir -p .tmp && TMPDIR=$PWD/.tmp python3 -m pytest tests/runtime/api/test_migrate.py tests/runtime/api/test_state.py -q`
Expected: FAIL with `AttributeError: 'State' object has no attribute 'get_account'`.

- [ ] **Step 3: Add the migration**

In `runtime/eggie_api/core/migrate.py`, add `import uuid` next to `import sqlite3`, add after `_v3_web_ui`:

```python
def _v4_account(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS account (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            device_id TEXT NOT NULL,
            email TEXT,
            org_id TEXT,
            access_token TEXT,
            refresh_token TEXT,
            access_expires_at REAL,
            device_code TEXT,
            user_code TEXT,
            verification_url TEXT,
            code_expires_at REAL,
            poll_interval REAL,
            last_error TEXT,
            sync_ok_at REAL,
            sync_error TEXT
        )""")
    # Minted here, once: a device id that changed on restart would orphan
    # every record this device made on the service.
    conn.execute("INSERT OR IGNORE INTO account(id, device_id) VALUES (1, ?)",
                 (str(uuid.uuid4()),))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS cloud_projects (
            local_id TEXT PRIMARY KEY,
            cloud_id TEXT NOT NULL,
            org_id TEXT NOT NULL
        )""")
```

and append `_v4_account,` to `MIGRATIONS`.

- [ ] **Step 4: Add the State methods**

In `runtime/eggie_api/core/state.py`, add at module level after the imports:

```python
ACCOUNT_FIELDS = frozenset({
    "email", "org_id", "access_token", "refresh_token", "access_expires_at",
    "device_code", "user_code", "verification_url", "code_expires_at",
    "poll_interval", "last_error", "sync_ok_at", "sync_error"})
```

and in `class State`, before `close()`:

```python
    def get_account(self) -> dict:
        with self._lock:
            return dict(self._conn.execute(
                "SELECT * FROM account WHERE id=1").fetchone())

    def update_account(self, **fields) -> None:
        # Field names become SQL text below, so only known names get there.
        unknown = set(fields) - ACCOUNT_FIELDS
        if unknown:
            raise ValueError(f"unknown account fields: {sorted(unknown)}")
        if not fields:
            return
        assignments = ", ".join(f"{name}=?" for name in fields)
        with self._lock:
            self._conn.execute(f"UPDATE account SET {assignments} WHERE id=1",
                               tuple(fields.values()))
            self._conn.commit()

    def cloud_mapping(self) -> dict[str, dict]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM cloud_projects").fetchall()
        return {r["local_id"]: {"cloud_id": r["cloud_id"], "org_id": r["org_id"]}
                for r in rows}

    def map_cloud_project(self, local_id, cloud_id, org_id) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO cloud_projects(local_id, cloud_id, org_id) "
                "VALUES (?,?,?)", (local_id, cloud_id, org_id))
            self._conn.commit()

    def unmap_cloud_project(self, local_id) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM cloud_projects WHERE local_id=?",
                               (local_id,))
            self._conn.commit()

    def clear_cloud_projects(self) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM cloud_projects")
            self._conn.commit()
```

- [ ] **Step 5: Run the tests**

Run: `TMPDIR=$PWD/.tmp python3 -m pytest tests/runtime/api/test_migrate.py tests/runtime/api/test_state.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add runtime/eggie_api/core/migrate.py runtime/eggie_api/core/state.py tests/runtime/api/test_migrate.py tests/runtime/api/test_state.py
git commit -m "API: store the account and the project mapping

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: The service client

**Files:**
- Create: `runtime/eggie_api/core/cloud.py`
- Modify: `runtime/eggie_api/core/config.py`
- Test: `tests/runtime/api/test_cloud.py`

**Interfaces:**
- Produces:
  - `class CloudError(Exception)` with `.code: str`, `.message: str`, `.status: int`; `str(e)` is `"<code>: <message>"`.
  - `class CloudUnavailable(Exception)`
  - `class Cloud(base_url: str, *, opener=urllib.request.urlopen, timeout: float = 10.0)` with methods, each returning the decoded JSON (`dict`) or `None` for an empty body:
    - `device_code(client_name: str)`, `device_token(device_code: str)`, `refresh(refresh_token: str)`,
    - `logout(token: str)`, `me(token: str)`,
    - `create_project(token: str, name: str, client_ref: str)`, `delete_project(token: str, cloud_id: str)`.
  - `ApiConfig.cloud_url: str`, env `EGGIE_CLOUD_URL`.

- [ ] **Step 1: Write the failing tests**

Create `tests/runtime/api/test_cloud.py`:

```python
import io
import json
import urllib.error

import pytest

from eggie_api.core.cloud import Cloud, CloudError, CloudUnavailable


class _Response:
    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class Opener:
    """Stands in for urllib's urlopen: replays (status, body) or raises."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append(request)
        reply = self.replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        status, body = reply
        if status >= 400:
            raise urllib.error.HTTPError(request.full_url, status, "error", {},
                                         io.BytesIO(body))
        return _Response(body)


def _error(code, message="m"):
    return json.dumps({"error": {"code": code, "message": message}}).encode()


def test_the_service_error_body_becomes_a_cloud_error_with_its_code():
    cloud = Cloud("https://svc/api", opener=Opener((400, _error("slow_down"))))
    with pytest.raises(CloudError) as raised:
        cloud.device_token("dc")
    assert (raised.value.code, raised.value.status) == ("slow_down", 400)


def test_a_refused_connection_is_unavailable():
    cloud = Cloud("https://svc/api", opener=Opener(
        urllib.error.URLError(ConnectionRefusedError())))
    with pytest.raises(CloudUnavailable):
        cloud.me("t")


@pytest.mark.parametrize("status", [502, 503, 504])
def test_a_gateway_page_instead_of_the_service_is_unavailable(status):
    cloud = Cloud("https://svc/api", opener=Opener((status, b"<html>bad gateway</html>")))
    with pytest.raises(CloudUnavailable):
        cloud.me("t")


def test_a_404_without_an_error_body_keeps_its_status():
    cloud = Cloud("https://svc/api", opener=Opener((404, b"Not Found")))
    with pytest.raises(CloudError) as raised:
        cloud.delete_project("t", "abc")
    assert raised.value.status == 404


def test_create_sends_the_token_the_name_and_the_client_ref():
    opener = Opener((201, json.dumps({"id": "u-1"}).encode()))
    out = Cloud("https://svc/api/", opener=opener).create_project("tok", "blog", "dev/blog")

    assert out == {"id": "u-1"}
    request = opener.requests[0]
    assert (request.get_method(), request.full_url) == ("POST", "https://svc/api/v1/projects")
    assert request.get_header("Authorization") == "Bearer tok"
    assert json.loads(request.data) == {"name": "blog", "client_ref": "dev/blog"}
```

- [ ] **Step 2: Run them to see them fail**

Run: `TMPDIR=$PWD/.tmp python3 -m pytest tests/runtime/api/test_cloud.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'eggie_api.core.cloud'`.

- [ ] **Step 3: Write the client**

Create `runtime/eggie_api/core/cloud.py`:

```python
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

# A proxy's own error page, not the service: the service always answers with
# its JSON error body.
_GATEWAY = {502, 503, 504}


class CloudError(Exception):
    def __init__(self, code: str, message: str, status: int):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.status = status


class CloudUnavailable(Exception):
    pass


def _error_from(status: int, raw: bytes) -> Exception:
    try:
        error = json.loads(raw)["error"]
        return CloudError(str(error["code"]), str(error["message"]), status)
    except (ValueError, KeyError, TypeError):
        if status in _GATEWAY:
            return CloudUnavailable(f"the Eggie service answered {status}")
        text = raw.decode("utf-8", errors="replace").strip()[:200]
        return CloudError(f"http_{status}", text or f"status {status}", status)


class Cloud:
    def __init__(self, base_url: str, *, opener=None, timeout: float = 10.0):
        self._base = base_url.rstrip("/")
        self._open = opener or urllib.request.urlopen
        self._timeout = timeout

    def call(self, method: str, path: str, *, body: dict | None = None,
             token: str | None = None):
        data = None if body is None else json.dumps(body).encode()
        headers = {"Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(self._base + path, data=data,
                                         headers=headers, method=method)
        try:
            with self._open(request, timeout=self._timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as e:
            raise _error_from(e.code, e.read()) from None
        except OSError as e:
            raise CloudUnavailable(str(e)) from None
        if not raw:
            return None
        try:
            return json.loads(raw)
        except ValueError:
            raise CloudError("bad_response",
                             "the Eggie service sent something that is not JSON",
                             200) from None

    def device_code(self, client_name: str):
        return self.call("POST", "/v1/auth/device/code",
                         body={"client_name": client_name})

    def device_token(self, device_code: str):
        return self.call("POST", "/v1/auth/device/token",
                         body={"device_code": device_code})

    def refresh(self, refresh_token: str):
        return self.call("POST", "/v1/auth/token/refresh",
                         body={"refresh_token": refresh_token})

    def logout(self, token: str):
        return self.call("POST", "/v1/auth/logout", token=token)

    def me(self, token: str):
        return self.call("GET", "/v1/identity/me", token=token)

    def create_project(self, token: str, name: str, client_ref: str):
        return self.call("POST", "/v1/projects", token=token,
                         body={"name": name, "client_ref": client_ref})

    def delete_project(self, token: str, cloud_id: str):
        return self.call("DELETE",
                         f"/v1/projects/{urllib.parse.quote(cloud_id, safe='')}",
                         token=token)
```

- [ ] **Step 4: Add `cloud_url` to the config**

In `runtime/eggie_api/core/config.py`, add the field after `max_upload_bytes`:

```python
    cloud_url: str = "https://app.eggie.io/api"
```

and in `from_env`, after the `max_upload_bytes=` argument:

```python
            cloud_url=env.get("EGGIE_CLOUD_URL", "https://app.eggie.io/api"),
```

- [ ] **Step 5: Run the tests**

Run: `TMPDIR=$PWD/.tmp python3 -m pytest tests/runtime/api/test_cloud.py tests/runtime/api/test_config.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add runtime/eggie_api/core/cloud.py runtime/eggie_api/core/config.py tests/runtime/api/test_cloud.py
git commit -m "API: a client for the Eggie service

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Account — device flow, tokens, sign-out

**Files:**
- Create: `runtime/eggie_api/core/account.py`
- Create: `tests/runtime/api/fake_cloud.py`
- Test: `tests/runtime/api/test_account.py`

**Interfaces:**
- Consumes: Task 1 `State` account methods; Task 2 `CloudError`, `CloudUnavailable`, `Cloud` method names.
- Produces:
  - `class NotSignedIn(Exception)`
  - `class Account(state, cloud, *, clock=time.time, sleep=time.sleep, spawn=<daemon thread>)`, attribute `on_signed_in: Callable[[], None]` (default no-op; `create_app` assigns it).
  - `Account.status() -> dict` — the `GET /account` body (spec section 4).
  - `Account.signed_in -> bool` (property), `Account.device_id -> str` (property).
  - `Account.start_sign_in() -> dict` — raises `CloudError` / `CloudUnavailable` from `device_code`.
  - `Account.resume() -> None` — starts the poller if a code is pending.
  - `Account.poll_once() -> float | None` — seconds to wait before the next poll, `None` when polling is over.
  - `Account.authed(fn: Callable[[str], T]) -> T` — calls `fn(access_token)`, refreshing as the spec says; raises `NotSignedIn`.
  - `Account.load_identity() -> str` — the org id, fetched via `me` when not stored yet.
  - `Account.sign_out() -> dict`

- [ ] **Step 1: Write the shared fake**

Create `tests/runtime/api/fake_cloud.py`:

```python
class FakeCloud:
    """Replays scripted answers per method, in order; an exception is raised.
    Records every call as (method, *args)."""

    def __init__(self, **replies):
        self.replies = {name: list(values) for name, values in replies.items()}
        self.calls = []

    def _next(self, name, *args):
        self.calls.append((name, *args))
        queue = self.replies.get(name)
        assert queue, f"unexpected call to {name}{args}"
        reply = queue.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return reply

    def names(self):
        return [call[0] for call in self.calls]

    def device_code(self, client_name):
        return self._next("device_code", client_name)

    def device_token(self, device_code):
        return self._next("device_token", device_code)

    def refresh(self, refresh_token):
        return self._next("refresh", refresh_token)

    def logout(self, token):
        return self._next("logout", token)

    def me(self, token):
        return self._next("me", token)

    def create_project(self, token, name, client_ref):
        return self._next("create_project", token, name, client_ref)

    def delete_project(self, token, cloud_id):
        return self._next("delete_project", token, cloud_id)


CODE = {"device_code": "dc-1", "user_code": "ABCD-EFGH",
        "verification_uri": "https://svc/device",
        "verification_uri_complete": "https://svc/device?user_code=ABCD-EFGH",
        "expires_in": 600, "interval": 5}
TOKENS = {"access_token": "at-1", "refresh_token": "rt-1",
          "token_type": "Bearer", "expires_in": 900}
ME = {"user": {"id": "u", "email": "ada@example.com", "created_at": "x"},
      "current_org_id": "org-1", "organizations": []}
```

- [ ] **Step 2: Write the failing tests**

Create `tests/runtime/api/test_account.py`:

```python
import pytest

from eggie_api.core.account import Account, NotSignedIn
from eggie_api.core.cloud import CloudError, CloudUnavailable
from eggie_api.core.state import State
from tests.runtime.api.fake_cloud import CODE, ME, TOKENS, FakeCloud


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def err(code, status=400):
    return CloudError(code, code, status)


def make(tmp_path, cloud):
    clock = Clock()
    spawned = []
    account = Account(State(tmp_path / "state.db"), cloud, clock=clock,
                      sleep=lambda s: None, spawn=spawned.append)
    return account, clock, spawned


def signed_in(tmp_path, cloud, expires_in=900):
    account, clock, _ = make(tmp_path, cloud)
    account._state.update_account(access_token="at-1", refresh_token="rt-1",
                                  access_expires_at=clock.now + expires_in,
                                  email="ada@example.com", org_id="org-1")
    return account, clock


def test_starting_sign_in_shows_the_code_and_link_and_starts_one_poller(tmp_path):
    account, clock, spawned = make(tmp_path, FakeCloud(device_code=[CODE]))

    status = account.start_sign_in()

    assert status == {"state": "pending", "user_code": "ABCD-EFGH",
                      "url": "https://svc/device?user_code=ABCD-EFGH",
                      "expires_at": clock.now + 600}
    assert len(spawned) == 1


def test_starting_again_while_the_code_is_valid_reuses_it(tmp_path):
    cloud = FakeCloud(device_code=[CODE])
    account, clock, spawned = make(tmp_path, cloud)
    account.start_sign_in()
    clock.now += 100

    assert account.start_sign_in()["user_code"] == "ABCD-EFGH"
    assert cloud.names() == ["device_code"]
    assert len(spawned) == 1, "a poller is already running"


def test_a_pending_code_is_polled_again_after_the_interval(tmp_path):
    account, _, _ = make(tmp_path, FakeCloud(
        device_code=[CODE], device_token=[err("authorization_pending")]))
    account.start_sign_in()

    assert account.poll_once() == 5


def test_slow_down_lengthens_the_interval_for_good(tmp_path):
    account, _, _ = make(tmp_path, FakeCloud(
        device_code=[CODE],
        device_token=[err("slow_down"), err("authorization_pending")]))
    account.start_sign_in()

    assert account.poll_once() == 10
    assert account.poll_once() == 10


def test_an_unreachable_service_keeps_polling(tmp_path):
    account, _, _ = make(tmp_path, FakeCloud(
        device_code=[CODE], device_token=[CloudUnavailable("down")]))
    account.start_sign_in()

    assert account.poll_once() == 5


@pytest.mark.parametrize("code", ["access_denied", "expired_token", "invalid_grant"])
def test_a_refused_code_returns_to_signed_out_with_the_reason(tmp_path, code):
    account, _, _ = make(tmp_path, FakeCloud(device_code=[CODE], device_token=[err(code)]))
    account.start_sign_in()

    assert account.poll_once() is None
    assert account.status() == {"state": "signed_out", "error": code}


def test_a_code_past_its_expiry_ends_without_asking_the_service(tmp_path):
    cloud = FakeCloud(device_code=[CODE])
    account, clock, _ = make(tmp_path, cloud)
    account.start_sign_in()
    clock.now += 601

    assert account.poll_once() is None
    assert account.status() == {"state": "signed_out", "error": "expired_token"}
    assert "device_token" not in cloud.names()


def test_approval_stores_the_tokens_reads_who_it_is_and_wakes_sync(tmp_path):
    account, _, _ = make(tmp_path, FakeCloud(
        device_code=[CODE], device_token=[TOKENS], me=[ME]))
    woken = []
    account.on_signed_in = lambda: woken.append(True)
    account.start_sign_in()

    assert account.poll_once() is None
    status = account.status()
    assert (status["state"], status["email"]) == ("signed_in", "ada@example.com")
    assert account.load_identity() == "org-1"
    assert woken == [True]


def test_resume_starts_a_poller_only_for_a_pending_code(tmp_path):
    account, _, spawned = make(tmp_path, FakeCloud(device_code=[CODE]))
    account.resume()
    assert spawned == []

    account.start_sign_in()
    account._polling = False  # as after an API restart
    account.resume()
    assert len(spawned) == 2


def test_a_token_about_to_expire_is_refreshed_before_the_call(tmp_path):
    cloud = FakeCloud(refresh=[{**TOKENS, "access_token": "at-2", "refresh_token": "rt-2"}])
    account, _ = signed_in(tmp_path, cloud, expires_in=30)

    assert account.authed(lambda token: token) == "at-2"
    assert account._state.get_account()["refresh_token"] == "rt-2"


def test_an_invalid_token_is_refreshed_and_retried_once(tmp_path):
    cloud = FakeCloud(refresh=[{**TOKENS, "access_token": "at-2"}])
    account, _ = signed_in(tmp_path, cloud)
    seen = []

    def call(token):
        seen.append(token)
        raise err("invalid_token", 401)

    with pytest.raises(CloudError):
        account.authed(call)
    assert seen == ["at-1", "at-2"], "one retry, then the error surfaces"


def test_a_revoked_refresh_signs_out_with_revoked(tmp_path):
    account, _ = signed_in(tmp_path, FakeCloud(refresh=[err("invalid_grant")]), expires_in=0)
    account._state.map_cloud_project("blog", "c-1", "org-1")

    with pytest.raises(NotSignedIn):
        account.authed(lambda token: token)
    assert account.status() == {"state": "signed_out", "error": "revoked"}
    assert account._state.cloud_mapping() == {}


def test_an_unreachable_service_during_refresh_keeps_the_account(tmp_path):
    account, _ = signed_in(tmp_path, FakeCloud(refresh=[CloudUnavailable("down")]), expires_in=0)

    with pytest.raises(CloudUnavailable):
        account.authed(lambda token: token)
    assert account.signed_in


def test_sign_out_forgets_everything_but_the_device_even_if_logout_fails(tmp_path):
    account, _ = signed_in(tmp_path, FakeCloud(logout=[CloudUnavailable("down")]))
    device = account.device_id
    account._state.map_cloud_project("blog", "c-1", "org-1")

    assert account.sign_out() == {"state": "signed_out", "error": None}
    assert account._state.cloud_mapping() == {}
    assert account.device_id == device
```

- [ ] **Step 3: Run them to see them fail**

Run: `TMPDIR=$PWD/.tmp python3 -m pytest tests/runtime/api/test_account.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'eggie_api.core.account'`.

- [ ] **Step 4: Write the account**

Create `runtime/eggie_api/core/account.py`:

```python
from __future__ import annotations

import logging
import threading
import time

from .cloud import CloudError, CloudUnavailable

CLIENT_NAME = "Eggie"
REFRESH_MARGIN = 60.0
SLOW_DOWN_STEP = 5.0
DEFAULT_INTERVAL = 5.0
_REFUSED = {"access_denied", "expired_token", "invalid_grant"}
_CLEARED_CODE = {"device_code": None, "user_code": None, "verification_url": None,
                 "code_expires_at": None, "poll_interval": None}

log = logging.getLogger("eggie.account")


class NotSignedIn(Exception):
    pass


def _daemon(fn) -> None:
    threading.Thread(target=fn, name="eggie-sign-in", daemon=True).start()


class Account:
    def __init__(self, state, cloud, *, clock=time.time, sleep=time.sleep,
                 spawn=_daemon):
        self._state = state
        self._cloud = cloud
        self._clock = clock
        self._sleep = sleep
        self._spawn = spawn
        self.on_signed_in = lambda: None
        self._refresh_lock = threading.Lock()
        self._poll_lock = threading.Lock()
        self._polling = False

    @property
    def signed_in(self) -> bool:
        return bool(self._state.get_account()["access_token"])

    @property
    def device_id(self) -> str:
        return self._state.get_account()["device_id"]

    def status(self) -> dict:
        row = self._state.get_account()
        if row["access_token"]:
            return {"state": "signed_in", "email": row["email"],
                    "sync": {"last_ok_at": row["sync_ok_at"],
                             "last_error": row["sync_error"]}}
        if row["device_code"]:
            return {"state": "pending", "user_code": row["user_code"],
                    "url": row["verification_url"],
                    "expires_at": row["code_expires_at"]}
        return {"state": "signed_out", "error": row["last_error"]}

    def start_sign_in(self) -> dict:
        row = self._state.get_account()
        if row["access_token"]:
            return self.status()
        if not (row["device_code"] and row["code_expires_at"] > self._clock()):
            out = self._cloud.device_code(CLIENT_NAME)
            self._state.update_account(
                device_code=out["device_code"], user_code=out["user_code"],
                verification_url=out["verification_uri_complete"],
                code_expires_at=self._clock() + out["expires_in"],
                poll_interval=float(out["interval"]), last_error=None)
        self._ensure_poller()
        return self.status()

    def resume(self) -> None:
        if self._state.get_account()["device_code"]:
            self._ensure_poller()

    def _ensure_poller(self) -> None:
        with self._poll_lock:
            if self._polling:
                return
            self._polling = True
        self._spawn(self._run_poller)

    def _run_poller(self) -> None:
        while True:
            try:
                wait = self.poll_once()
            except Exception:
                log.exception("sign-in poll failed")
                wait = DEFAULT_INTERVAL
            if wait is not None:
                self._sleep(wait)
                continue
            # Checked under the lock start_sign_in's spawn takes: a new code
            # stored after the last poll must not be left with no poller.
            with self._poll_lock:
                if not self._state.get_account()["device_code"]:
                    self._polling = False
                    return

    def poll_once(self) -> float | None:
        row = self._state.get_account()
        if not row["device_code"]:
            return None
        interval = row["poll_interval"] or DEFAULT_INTERVAL
        if self._clock() >= row["code_expires_at"]:
            self._state.update_account(**_CLEARED_CODE, last_error="expired_token")
            return None
        try:
            tokens = self._cloud.device_token(row["device_code"])
        except CloudUnavailable:
            return interval
        except CloudError as e:
            if e.code == "slow_down":
                interval += SLOW_DOWN_STEP
                self._state.update_account(poll_interval=interval)
                return interval
            if e.code in _REFUSED:
                self._state.update_account(**_CLEARED_CODE, last_error=e.code)
                return None
            return interval
        self._state.update_account(
            **_CLEARED_CODE, last_error=None,
            access_token=tokens["access_token"],
            refresh_token=tokens["refresh_token"],
            access_expires_at=self._clock() + tokens["expires_in"])
        try:
            self.load_identity()
        except (CloudError, CloudUnavailable, NotSignedIn):
            # The sync pass asks again; the sign-in itself has succeeded.
            log.warning("signed in, but could not read the account yet")
        self.on_signed_in()
        return None

    def load_identity(self) -> str:
        row = self._state.get_account()
        if row["org_id"]:
            return row["org_id"]
        me = self.authed(self._cloud.me)
        self._state.update_account(email=me["user"]["email"],
                                   org_id=me["current_org_id"])
        return me["current_org_id"]

    def authed(self, fn):
        row = self._state.get_account()
        token = row["access_token"]
        if not token:
            raise NotSignedIn()
        if row["access_expires_at"] - self._clock() < REFRESH_MARGIN:
            token = self._refresh(token)
        try:
            return fn(token)
        except CloudError as e:
            if e.status != 401 or e.code != "invalid_token":
                raise
        return fn(self._refresh(token))

    def _refresh(self, stale: str) -> str:
        with self._refresh_lock:
            row = self._state.get_account()
            if not row["access_token"]:
                raise NotSignedIn()
            # Another thread refreshed while this one waited; refreshing again
            # would spend a refresh token the service may already have rotated.
            if row["access_token"] != stale:
                return row["access_token"]
            try:
                out = self._cloud.refresh(row["refresh_token"])
            except CloudError as e:
                if e.code == "invalid_grant":
                    self._forget("revoked")
                    raise NotSignedIn() from None
                raise
            self._state.update_account(
                access_token=out["access_token"],
                refresh_token=out["refresh_token"],
                access_expires_at=self._clock() + out["expires_in"])
            return out["access_token"]

    def sign_out(self) -> dict:
        token = self._state.get_account()["access_token"]
        if token:
            try:
                self._cloud.logout(token)
            except (CloudError, CloudUnavailable):
                log.warning("the service did not confirm the sign-out")
        self._forget(None)
        return self.status()

    def _forget(self, error: str | None) -> None:
        self._state.update_account(
            **_CLEARED_CODE, email=None, org_id=None, access_token=None,
            refresh_token=None, access_expires_at=None, last_error=error,
            sync_ok_at=None, sync_error=None)
        self._state.clear_cloud_projects()
```

- [ ] **Step 5: Run the tests**

Run: `TMPDIR=$PWD/.tmp python3 -m pytest tests/runtime/api/test_account.py -q`
Expected: PASS (16 tests).

- [ ] **Step 6: Commit**

```bash
git add runtime/eggie_api/core/account.py tests/runtime/api/fake_cloud.py tests/runtime/api/test_account.py
git commit -m "API: sign in to the Eggie service with a device code

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: The sync pass and loop

**Files:**
- Create: `runtime/eggie_api/core/sync.py`
- Test: `tests/runtime/api/test_sync.py`

**Interfaces:**
- Consumes: Task 1 `State` mapping + account methods, `State.list_projects()`; Task 2 errors; Task 3 `Account.authed`, `Account.load_identity`, `Account.signed_in`, `Account.device_id`, `NotSignedIn`.
- Produces:
  - `Create(local_id)`, `Delete(local_id, cloud_id)`, `Forget(local_id)` — frozen dataclasses.
  - `plan(local_ids: set[str], mapping: dict[str, dict], org_id: str) -> list`
  - `client_ref(device_id: str, local_id: str) -> str` → `"<device_id>/<local_id>"`
  - `apply(actions, *, account, cloud, state, org_id) -> list[str]` — error lines, empty on success.
  - `run_pass(account, cloud, state, clock=time.time) -> None`
  - `class SyncLoop(run: Callable[[], None], *, interval: float = 60.0)` with `wake()`, `run_forever()`, `start()`.

- [ ] **Step 1: Write the failing tests**

Create `tests/runtime/api/test_sync.py`:

```python
from eggie_api.core.account import Account
from eggie_api.core.cloud import CloudError, CloudUnavailable
from eggie_api.core.constants import VERIFY_PROJECT_ID
from eggie_api.core.state import State
from eggie_api.core.sync import Create, Delete, Forget, plan, run_pass
from tests.runtime.api.fake_cloud import FakeCloud

M = lambda cloud_id, org="org-1": {"cloud_id": cloud_id, "org_id": org}


def test_plan_creates_unmapped_deletes_orphaned_and_forgets_other_orgs():
    actions = plan({"blog", "shop"},
                   {"shop": M("c-shop"), "old": M("c-old"), "blog": M("c-x", "org-9")},
                   "org-1")
    assert actions == [Forget("blog"), Delete("old", "c-old"), Create("blog")]


def test_plan_leaves_mapped_projects_alone():
    assert plan({"blog"}, {"blog": M("c-1")}, "org-1") == []


def setup(tmp_path, cloud, projects=()):
    state = State(tmp_path / "state.db")
    for pid in projects:
        state.add_project(pid, f"/p/{pid}", "d")
    state.update_account(access_token="at", refresh_token="rt",
                         access_expires_at=10**12, email="a@x", org_id="org-1")
    return state, Account(state, cloud, spawn=lambda fn: None)


def test_a_pass_creates_records_with_this_devices_client_ref(tmp_path):
    cloud = FakeCloud(create_project=[{"id": "c-1"}])
    state, account = setup(tmp_path, cloud, ["blog"])

    run_pass(account, cloud, state)

    assert cloud.calls == [("create_project", "at", "blog", f"{account.device_id}/blog")]
    assert state.cloud_mapping() == {"blog": M("c-1")}
    assert state.get_account()["sync_ok_at"] is not None


def test_the_install_smoke_test_project_is_never_sent(tmp_path):
    cloud = FakeCloud()
    state, account = setup(tmp_path, cloud, [VERIFY_PROJECT_ID])

    run_pass(account, cloud, state)

    assert cloud.calls == []


def test_a_record_already_gone_on_the_service_counts_as_deleted(tmp_path):
    cloud = FakeCloud(delete_project=[CloudError("not_found", "gone", 404)])
    state, account = setup(tmp_path, cloud)
    state.map_cloud_project("old", "c-old", "org-1")

    run_pass(account, cloud, state)

    assert state.cloud_mapping() == {}


def test_a_failed_delete_keeps_the_mapping_and_the_rest_carries_on(tmp_path):
    cloud = FakeCloud(delete_project=[CloudError("method_not_allowed", "no", 405)],
                      create_project=[{"id": "c-1"}])
    state, account = setup(tmp_path, cloud, ["blog"])
    state.map_cloud_project("old", "c-old", "org-1")

    run_pass(account, cloud, state)

    assert state.cloud_mapping() == {"old": M("c-old"), "blog": M("c-1")}
    row = state.get_account()
    assert "old" in row["sync_error"] and row["sync_ok_at"] is None


def test_an_unreachable_service_is_recorded_and_retried_next_pass(tmp_path):
    cloud = FakeCloud(create_project=[CloudUnavailable("down"), {"id": "c-1"}])
    state, account = setup(tmp_path, cloud, ["blog"])

    run_pass(account, cloud, state)
    assert state.cloud_mapping() == {}
    run_pass(account, cloud, state)
    assert state.cloud_mapping() == {"blog": M("c-1")}
    assert state.get_account()["sync_error"] is None


def test_nothing_happens_when_signed_out(tmp_path):
    cloud = FakeCloud()
    state, account = setup(tmp_path, cloud, ["blog"])
    state.update_account(access_token=None)

    run_pass(account, cloud, state)

    assert cloud.calls == []
```

- [ ] **Step 2: Run them to see them fail**

Run: `TMPDIR=$PWD/.tmp python3 -m pytest tests/runtime/api/test_sync.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'eggie_api.core.sync'`.

- [ ] **Step 3: Write the sync module**

Create `runtime/eggie_api/core/sync.py`:

```python
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

from .account import NotSignedIn
from .cloud import CloudError, CloudUnavailable
from .constants import VERIFY_PROJECT_ID

log = logging.getLogger("eggie.sync")


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
    actions: list = []
    ours: dict[str, str] = {}
    for local_id, entry in sorted(mapping.items()):
        # Another account's records are not ours to delete.
        if entry["org_id"] != org_id:
            actions.append(Forget(local_id))
        else:
            ours[local_id] = entry["cloud_id"]
    for local_id, cloud_id in sorted(ours.items()):
        if local_id not in local_ids:
            actions.append(Delete(local_id, cloud_id))
    for local_id in sorted(local_ids - ours.keys()):
        actions.append(Create(local_id))
    return actions


def client_ref(device_id: str, local_id: str) -> str:
    return f"{device_id}/{local_id}"


def apply(actions, *, account, cloud, state, org_id: str) -> list[str]:
    errors: list[str] = []
    device_id = account.device_id
    for action in actions:
        try:
            if isinstance(action, Forget):
                state.unmap_cloud_project(action.local_id)
            elif isinstance(action, Create):
                ref = client_ref(device_id, action.local_id)
                out = account.authed(lambda token, a=action, r=ref:
                                     cloud.create_project(token, a.local_id, r))
                state.map_cloud_project(action.local_id, out["id"], org_id)
            else:
                try:
                    account.authed(lambda token, a=action:
                                   cloud.delete_project(token, a.cloud_id))
                except CloudError as e:
                    if e.status != 404:
                        raise
                state.unmap_cloud_project(action.local_id)
        except NotSignedIn:
            raise
        except Exception as e:
            log.warning("sync of %s failed: %s", action.local_id, e)
            errors.append(f"{action.local_id}: {e}")
    return errors


def run_pass(account, cloud, state, clock=time.time) -> None:
    if not account.signed_in:
        return
    try:
        org_id = account.load_identity()
        local_ids = {row["id"] for row in state.list_projects()} - {VERIFY_PROJECT_ID}
        actions = plan(local_ids, state.cloud_mapping(), org_id)
        errors = apply(actions, account=account, cloud=cloud, state=state,
                       org_id=org_id)
    except NotSignedIn:
        return
    except (CloudError, CloudUnavailable) as e:
        state.update_account(sync_error=str(e))
        return
    if errors:
        state.update_account(sync_error="; ".join(errors))
    else:
        state.update_account(sync_ok_at=clock(), sync_error=None)


class SyncLoop:
    def __init__(self, run, *, interval: float = 60.0):
        self._run = run
        self._interval = interval
        self._wake = threading.Event()

    def wake(self) -> None:
        self._wake.set()

    def run_forever(self) -> None:
        while True:
            # Cleared before the pass: a change made during it runs another.
            self._wake.clear()
            try:
                self._run()
            except Exception:
                log.exception("sync pass failed")
            self._wake.wait(self._interval)

    def start(self) -> None:
        threading.Thread(target=self.run_forever, name="eggie-sync",
                         daemon=True).start()
```

- [ ] **Step 4: Run the tests**

Run: `TMPDIR=$PWD/.tmp python3 -m pytest tests/runtime/api/test_sync.py -q`
Expected: PASS (8 tests).

- [ ] **Step 5: Commit**

```bash
git add runtime/eggie_api/core/sync.py tests/runtime/api/test_sync.py
git commit -m "API: keep a service record for every local project

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Routes and wiring

**Files:**
- Modify: `runtime/eggie_api/routes/app.py`
- Modify: `runtime/eggie_api/routes/__main__.py`
- Test: `tests/runtime/api/test_api_account.py`

**Interfaces:**
- Consumes: Task 2 `Cloud`, errors; Task 3 `Account`; Task 4 `SyncLoop`, `run_pass`.
- Produces:
  - `create_app(..., cloud=None, account: Account | None = None)` — sets `app.state.account`, `app.state.sync` (a `SyncLoop`, not started).
  - Routes `GET /account`, `POST /account/sign-in`, `POST /account/sign-out` (and the same under `/api`). `POST /account/sign-in` maps `CloudUnavailable` → 503 `cloud_unavailable`, `CloudError` → 502 `cloud_error`.

- [ ] **Step 1: Write the failing tests**

Create `tests/runtime/api/test_api_account.py`:

```python
from fastapi.testclient import TestClient

from eggie_api.core.account import Account
from eggie_api.core.cloud import CloudUnavailable
from eggie_api.core.state import State
from eggie_api.routes.app import create_app
from tests.runtime.api.conftest import AUTH, FakeRunner
from tests.runtime.api.fake_cloud import CODE, FakeCloud


def client(env, cloud):
    state = State(env.config.state_db.with_name("account.db"))
    account = Account(state, cloud, spawn=lambda fn: None)
    app = create_app(config=env.config, runner=FakeRunner(), state=state,
                     account=account)
    return TestClient(app, headers=AUTH)


def test_sign_in_answers_with_the_code_to_show(env):
    resp = client(env, FakeCloud(device_code=[CODE])).post("/account/sign-in")

    assert resp.status_code == 200
    body = resp.json()
    assert (body["state"], body["user_code"]) == ("pending", "ABCD-EFGH")


def test_sign_in_says_when_the_service_cannot_be_reached(env):
    resp = client(env, FakeCloud(device_code=[CloudUnavailable("down")])).post("/account/sign-in")

    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "cloud_unavailable"
```

- [ ] **Step 2: Run them to see them fail**

Run: `TMPDIR=$PWD/.tmp python3 -m pytest tests/runtime/api/test_api_account.py -q`
Expected: FAIL with `TypeError: create_app() got an unexpected keyword argument 'account'`.

- [ ] **Step 3: Wire the account and sync into `create_app`**

In `runtime/eggie_api/routes/app.py`, add to the imports:

```python
from ..core.account import Account
from ..core.cloud import Cloud, CloudError, CloudUnavailable
from ..core.sync import SyncLoop, run_pass
```

Change the signature to:

```python
def create_app(*, config: ApiConfig | None = None, runner=None, state=None,
               jobs: JobRegistry | None = None, http_probe=None,
               sessions: Sessions | None = None, cloud=None,
               account: Account | None = None) -> FastAPI:
```

After `uploads.sweep()` add:

```python
    cloud = cloud or Cloud(config.cloud_url)
    account = account or Account(state, cloud)
    sync = SyncLoop(lambda: run_pass(account, cloud, state))
    account.on_signed_in = sync.wake
```

and after `app.state.sessions = sessions`:

```python
    app.state.account = account
    app.state.sync = sync
```

- [ ] **Step 4: Wake the sync after project changes**

In `create_project`, just before `return payload(state.get_project(project_id))` add `sync.wake()`. Do the same in `adopt_project` before its `return`. In `delete_project`, add `sync.wake()` right after `state.remove_project(project_id)`.

- [ ] **Step 5: Add the account routes**

After the `disk_usage` route add:

```python
    @router.get("/account")
    def account_status() -> dict:
        return account.status()

    @router.post("/account/sign-in")
    def account_sign_in() -> dict:
        try:
            return account.start_sign_in()
        except CloudUnavailable:
            raise ApiError("cloud_unavailable",
                           "The Eggie service can't be reached. Check the "
                           "internet connection and try again.", 503) from None
        except CloudError as e:
            raise ApiError("cloud_error", "The Eggie service would not start "
                           f"a sign-in: {e.message}", 502) from None

    @router.post("/account/sign-out")
    def account_sign_out() -> dict:
        return account.sign_out()
```

- [ ] **Step 6: Start the threads in the entrypoint**

In `runtime/eggie_api/routes/__main__.py`, after the `try/except SchemaTooNew` block and before `uvicorn.run(...)`:

```python
    app.state.account.resume()
    app.state.sync.start()
```

- [ ] **Step 7: Run the whole Python suite**

Run: `TMPDIR=$PWD/.tmp python3 -m pytest -q`
Expected: PASS, including `test_no_platform_leak.py`, `test_no_host_import.py` and `test_host_dependencies.py`.

- [ ] **Step 8: Commit**

```bash
git add runtime/eggie_api/routes/app.py runtime/eggie_api/routes/__main__.py tests/runtime/api/test_api_account.py
git commit -m "API: account routes, and sync after every project change

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Console — the lock, the sign-in screen, sign-out

**Files:**
- Create: `runtime/web/apps/console/src/account/account.ts`, `account.test.ts`
- Create: `runtime/web/apps/console/src/components/Qr.tsx`
- Create: `runtime/web/apps/console/src/screens/account/SignIn.tsx`, `SignIn.module.css`
- Create: `runtime/web/apps/console/src/shell/AccountMenu.tsx`
- Modify: `runtime/web/apps/console/src/boot/boot.ts`, `boot.test.ts`
- Modify: `runtime/web/apps/console/src/App.tsx`
- Modify: `runtime/web/apps/console/src/shell/Shell.tsx`, `Shell.module.css`
- Modify: `runtime/web/apps/console/src/mocks/handlers.ts`
- Modify: `runtime/web/apps/console/package.json` (+ lockfile)

**Interfaces:**
- Consumes: Task 5 routes `GET /api/account`, `POST /api/account/sign-in`, `POST /api/account/sign-out` and their bodies (spec section 4).
- Produces: `BootResult` gains `{ kind: "needsAccount" }`; `signInLink(url: string): string | null`; `signInError(code: string | null): string | null`; `<SignIn onSignedIn={() => void} />`; `<AccountMenu onSignedOut={() => void} />`; `Shell` prop `trailing?: ReactNode`.

- [ ] **Step 1: Write the failing tests**

Create `runtime/web/apps/console/src/account/account.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { signInLink } from "./account";

describe("signInLink", () => {
  it("accepts an https sign-in page", () => {
    const url = "https://app.eggie.io/device?user_code=ABCD-EFGH";
    expect(signInLink(url)).toBe(url);
  });

  it("refuses anything that is not https, local dev addresses included", () => {
    expect(signInLink("http://localhost:5174/device?user_code=X")).toBeNull();
    expect(signInLink("javascript:alert(1)")).toBeNull();
    expect(signInLink("not a url")).toBeNull();
  });
});
```

In `runtime/web/apps/console/src/boot/boot.test.ts`, add after `const OK` :

```ts
const ACCOUNT = (state: string): Reply => ({ status: 200, body: { state } });
```

Change the test "signs in with a fresh handoff code without asking for the session again" to:

```ts
  it("signs in with a fresh handoff code without asking for the session again", async () => {
    const { fetch, calls } = api({
      "GET /api/health": HEALTHY,
      "POST /api/session": OK,
      "GET /api/account": ACCOUNT("signed_in"),
    });
    expect(await boot({ fetch, handoff: "code" })).toEqual({ kind: "signedIn" });
    expect(calls).toEqual(["GET /api/health", "POST /api/session", "GET /api/account"]);
  });
```

In "is still signed in when the handoff was spent but the cookie is good" add `"GET /api/account": ACCOUNT("signed_in"),` to its routes. Then add inside `describe("boot", ...)`:

```ts
  it("needs an account until the Eggie sign-in has finished", async () => {
    for (const state of ["signed_out", "pending"]) {
      const { fetch } = api({ "GET /api/health": HEALTHY, "GET /api/session": OK, "GET /api/account": ACCOUNT(state) });
      expect(await boot({ fetch, handoff: null })).toEqual({ kind: "needsAccount" });
    }
  });

  it("is notAnswering when the account check fails for another reason", async () => {
    const { fetch } = api({
      "GET /api/health": HEALTHY,
      "GET /api/session": OK,
      "GET /api/account": refusal(500, "internal_error"),
    });
    expect(await boot({ fetch, handoff: null })).toEqual({ kind: "notAnswering" });
  });
```

- [ ] **Step 2: Run them to see them fail**

Run: `cd runtime/web && npm install && npm test`
Expected: FAIL — `account.ts` not found, and the boot tests expecting `needsAccount` / the extra call.

- [ ] **Step 3: Write `account.ts`**

Create `runtime/web/apps/console/src/account/account.ts`:

```ts
export type Account =
  | { state: "signed_out"; error: string | null }
  | { state: "pending"; user_code: string; url: string; expires_at: number }
  | {
      state: "signed_in";
      email: string | null;
      sync: { last_ok_at: number | null; last_error: string | null };
    };

// The link comes from the Eggie service; anything but https is refused, never repaired.
export function signInLink(url: string): string | null {
  try {
    return new URL(url).protocol === "https:" ? url : null;
  } catch {
    return null;
  }
}

const REASONS: Record<string, string> = {
  access_denied: "The sign-in was turned down.",
  expired_token: "That code ran out before it was approved.",
  invalid_grant: "That code is no longer valid.",
  revoked: "This computer was signed out of your Eggie account.",
};

export function signInError(code: string | null): string | null {
  if (!code) return null;
  return REASONS[code] ?? "Sign-in didn't finish.";
}
```

- [ ] **Step 4: Gate boot on the account**

In `runtime/web/apps/console/src/boot/boot.ts`:

Change the imports to:

```ts
import type { Account } from "../account/account";
import { type Api, ApiError, createApi, isSessionLost } from "../api/client";
```

Add `| { kind: "needsAccount" }` to `BootResult` after `signedIn`. Add above `boot()`:

```ts
async function checkAccount(api: Api): Promise<BootResult> {
  try {
    const account = await api.get<Account>("/api/account");
    return account?.state === "signed_in" ? { kind: "signedIn" } : { kind: "needsAccount" };
  } catch (error) {
    if (isSessionLost(error)) return { kind: "signedOut", reason: error.code };
    return isWrongHost(error) ? { kind: "wrongHost" } : { kind: "notAnswering" };
  }
}
```

In `boot()`, replace both `return { kind: "signedIn" };` with `return checkAccount(api);`.

- [ ] **Step 5: Run the tests**

Run: `cd runtime/web && npm test`
Expected: PASS.

- [ ] **Step 6: Add the QR dependency and component**

Run: `cd runtime/web && npm install -w @eggie/console qrcode-generator@2.0.4 --save-exact`

Create `runtime/web/apps/console/src/components/Qr.tsx`:

```tsx
import qrcode from "qrcode-generator";

// Drawn as one SVG path of dark modules, so nothing is injected as markup.
export function Qr({ value, size = 184 }: { value: string; size?: number }) {
  const qr = qrcode(0, "M");
  qr.addData(value);
  qr.make();
  const count = qr.getModuleCount();
  const quiet = 4;
  let d = "";
  for (let row = 0; row < count; row++) {
    for (let col = 0; col < count; col++) {
      if (qr.isDark(row, col)) d += `M${col + quiet} ${row + quiet}h1v1h-1z`;
    }
  }
  const box = count + quiet * 2;
  return (
    <svg width={size} height={size} viewBox={`0 0 ${box} ${box}`} role="img" aria-label="QR code for the sign-in page" shapeRendering="crispEdges">
      <rect width={box} height={box} fill="#fff" />
      <path d={d} fill="#000" />
    </svg>
  );
}
```

- [ ] **Step 7: Write the sign-in screen**

Create `runtime/web/apps/console/src/screens/account/SignIn.module.css`:

```css
.code { margin: 0; font: 800 34px var(--font-mono); letter-spacing: .12em; color: var(--ink); }
.qr { padding: 12px; background: #fff; border-radius: 12px; border: 1px solid var(--line); line-height: 0; }
.link { color: var(--ink); font-weight: 600; }
.error { margin: 0; color: var(--ink-2); }
```

Create `runtime/web/apps/console/src/screens/account/SignIn.tsx`:

```tsx
import { useCallback, useEffect, useState } from "react";
import { Button } from "@eggie/ui";
import { type Account, signInError, signInLink } from "../../account/account";
import { ApiError, createApi } from "../../api/client";
import { Qr } from "../../components/Qr";
import { useNow } from "../../projects/useNow";
import { SleepyEgg } from "../SleepyEgg";
import { StatusScreen } from "../StatusScreen";
import s from "../StatusScreen.module.css";
import own from "./SignIn.module.css";

const api = createApi((input, init) => fetch(input, init));
const POLL_MS = 3000;

type View = { account: Account | null; unreachable: boolean; starting: boolean };

export function SignIn({ onSignedIn }: { onSignedIn: () => void }) {
  const [view, setView] = useState<View>({ account: null, unreachable: false, starting: false });

  const refresh = useCallback(async () => {
    try {
      const account = await api.get<Account>("/api/account");
      if (account.state === "signed_in") onSignedIn();
      else setView((v) => ({ ...v, account }));
    } catch {
      // A missed poll is retried on the next tick.
    }
  }, [onSignedIn]);

  useEffect(() => {
    void refresh();
    const timer = setInterval(() => void refresh(), POLL_MS);
    return () => clearInterval(timer);
  }, [refresh]);

  const start = async () => {
    setView((v) => ({ ...v, starting: true, unreachable: false }));
    try {
      const account = await api.post<Account>("/api/account/sign-in");
      setView({ account, unreachable: false, starting: false });
    } catch (error) {
      const unreachable = error instanceof ApiError && error.code === "cloud_unavailable";
      setView((v) => ({ ...v, unreachable, starting: false }));
    }
  };

  const account = view.account;
  if (account?.state === "pending") return <Pending account={account} onRestart={start} />;

  const reason = view.unreachable
    ? "The Eggie service can't be reached right now. Check the internet connection."
    : signInError(account?.state === "signed_out" ? account.error : null);
  return (
    <StatusScreen
      art={<SleepyEgg />}
      title="Sign in to Eggie"
      actions={
        <Button variant="primary" size="lg" onClick={start} disabled={view.starting}>
          {view.unreachable ? "Try again" : "Sign in"}
        </Button>
      }
      footer="Your projects keep running while you sign in."
    >
      {reason && <p className={own.error}>{reason}</p>}
      <p className={s.lead}>Your Eggie account keeps track of your projects. Sign in once on this computer.</p>
    </StatusScreen>
  );
}

function Pending({ account, onRestart }: { account: Extract<Account, { state: "pending" }>; onRestart: () => void }) {
  const now = useNow(1000);
  const left = Math.max(0, Math.round(account.expires_at - now / 1000));
  const link = signInLink(account.url);
  if (link === null) {
    return (
      <StatusScreen art={<SleepyEgg />} title="Sign-in isn't available" actions={<Button onClick={onRestart}>Try again</Button>}>
        <p className={s.lead}>The sign-in link from the Eggie service is not valid.</p>
        <p className={s.detail}>{account.url}</p>
      </StatusScreen>
    );
  }
  return (
    <StatusScreen
      tone="yolk"
      art={
        <div className={own.qr}>
          <Qr value={link} />
        </div>
      }
      title="Approve this computer"
      actions={
        <a className={own.link} href={link} target="_blank" rel="noopener noreferrer">
          Open the sign-in page
        </a>
      }
      footer={left > 0 ? `This code works for ${Math.ceil(left / 60)} more minute${left > 60 ? "s" : ""}.` : "This code has run out."}
    >
      <p className={s.lead}>Scan the code with your phone, or open the sign-in page, and check it shows:</p>
      <p className={own.code}>{account.user_code}</p>
    </StatusScreen>
  );
}
```

- [ ] **Step 8: Write the account menu and the Shell slot**

In `runtime/web/apps/console/src/shell/Shell.tsx`, change the signature and header to:

```tsx
export function Shell({
  tone = "yolk",
  trailing,
  children,
}: {
  tone?: "yolk" | "cold";
  trailing?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <div className={s.page}>
      <header className={cx(s.bar, tone === "cold" && s.muted)}>
        <Egg tone={tone} size={22} />
        <span className={s.brand}>Eggie</span>
        {trailing && <div className={s.trailing}>{trailing}</div>}
      </header>
      <main className={s.content}>{children}</main>
    </div>
  );
}
```

Append to `Shell.module.css`:

```css
.trailing { margin-left: auto; display: flex; align-items: center; gap: 12px; font-size: 14px; color: var(--ink-2); }
```

Create `runtime/web/apps/console/src/shell/AccountMenu.tsx`:

```tsx
import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Button } from "@eggie/ui";
import type { Account } from "../account/account";
import { createApi } from "../api/client";

const api = createApi((input, init) => fetch(input, init));

export function AccountMenu({ onSignedOut }: { onSignedOut: () => void }) {
  const [leaving, setLeaving] = useState(false);
  const { data } = useQuery({
    queryKey: ["account"],
    queryFn: () => api.get<Account>("/api/account"),
    refetchInterval: 60_000,
  });

  // Revoked from the web app, or signed out in another tab.
  useEffect(() => {
    if (data && data.state !== "signed_in") onSignedOut();
  }, [data, onSignedOut]);

  const signOut = async () => {
    setLeaving(true);
    try {
      await api.post("/api/account/sign-out");
    } finally {
      onSignedOut();
    }
  };

  return (
    <>
      {data?.state === "signed_in" && data.email && <span>{data.email}</span>}
      <Button variant="quiet" onClick={signOut} disabled={leaving}>
        Sign out
      </Button>
    </>
  );
}
```

- [ ] **Step 9: Render the screen in `App.tsx`**

In `runtime/web/apps/console/src/App.tsx`, add the imports:

```tsx
import { SignIn } from "./screens/account/SignIn";
import { AccountMenu } from "./shell/AccountMenu";
```

Add after the `queryClient` state:

```tsx
  const needAccount = useCallback(() => setResult({ kind: "needsAccount" }), []);
```

In the `signedIn` branch change `<Shell>` to `<Shell trailing={<AccountMenu onSignedOut={needAccount} />}>`, and add a case before `signedOut`:

```tsx
    case "needsAccount":
      return <SignIn onSignedIn={run} />;
```

- [ ] **Step 10: Add the mock account and scenarios**

In `runtime/web/apps/console/src/mocks/handlers.ts`, add to `SCENARIOS` after `"locked",`:

```ts
  "account-needed",
  "account-pending",
  "account-denied",
  "account-unreachable",
```

At the top of `handlersFor`, after `let signedIn = ...`:

```ts
  const MOCK_CODE = "WDJB-MJHT";
  const pendingAccount = () => ({
    state: "pending" as const,
    user_code: MOCK_CODE,
    url: `https://eggie.example/device?user_code=${MOCK_CODE}`,
    expires_at: nowSec() + 600,
  });
  let account: Record<string, unknown> = scenario.startsWith("account-")
    ? scenario === "account-pending"
      ? pendingAccount()
      : { state: "signed_out", error: null }
    : { state: "signed_in", email: "ada@example.com", sync: { last_ok_at: nowSec(), last_error: null } };
  let approveAt = 0;
```

In the returned array after the `http.delete("/api/session", ...)` handler:

```ts
    http.get("/api/account", () => {
      if (account.state === "pending" && approveAt === 0) approveAt = Date.now() + 6000;
      if (account.state === "pending" && Date.now() >= approveAt) {
        account =
          scenario === "account-denied"
            ? { state: "signed_out", error: "access_denied" }
            : { state: "signed_in", email: "ada@example.com", sync: { last_ok_at: nowSec(), last_error: null } };
      }
      return HttpResponse.json(account);
    }),
    http.post("/api/account/sign-in", () => {
      if (scenario === "account-unreachable") {
        return refuse("cloud_unavailable", "The Eggie service can't be reached. Check the internet connection and try again.", 503);
      }
      account = pendingAccount();
      approveAt = 0;
      return HttpResponse.json(account);
    }),
    http.post("/api/account/sign-out", () => {
      account = { state: "signed_out", error: null };
      return HttpResponse.json(account);
    }),
```

- [ ] **Step 11: Check everything the image build checks**

Run: `cd runtime/web && npm test && npm run typecheck && npm run build && npm run check-offline`
Expected: all pass.

Then `npm run dev`, open `/?scenario=account-needed`: press Sign in → the code, a QR code and the link appear → after ~6 s the project list shows, with the email and Sign out in the header; Sign out returns to the sign-in screen. `?scenario=account-denied` ends on "The sign-in was turned down."; `?scenario=account-unreachable` shows the unreachable line.

- [ ] **Step 12: Commit**

```bash
git add runtime/web
git commit -m "Console: sign in to Eggie before the projects show

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Docs

**Files:**
- Modify: `CLAUDE.md`
- Modify: `docs/superpowers/specs/2026-09-23-account-sign-in-sync-design.md`

- [ ] **Step 1: Bring the spec in line with the code**

In the spec's section 4 "Storage" block, add `poll_interval REAL`, `sync_ok_at REAL`, `sync_error TEXT` to the `account` table. In section 6 "Where it runs", change the `plan(...)` signature to `plan(local_ids, mapping, org_id) -> list[Action]` and add: "`eggie-selftest` (the install smoke test) is never sent."

- [ ] **Step 2: Add the architecture notes to `CLAUDE.md`**

Under `### Layers`, after the `runtime/eggie_api/core/uploads.py` bullet, add:

```markdown
- `runtime/eggie_api/core/cloud.py` / `account.py` / `sync.py` — the Eggie service
  (`EGGIE_CLOUD_URL`, default `https://app.eggie.io/api`). `account.py` runs the
  device-code sign-in and keeps the tokens in `state.db`; `sync.py` gives each local project
  a service record (created with `client_ref = <device_id>/<local_id>`, deleted with it) on a
  thread `routes/__main__.py` starts — `create_app()` never starts one. Only the console is
  locked until sign-in (`GET /api/account`); the CLI and coding agents never wait on it.
  Service gaps are not worked around in our code; see
  `docs/superpowers/specs/2026-09-23-account-sign-in-sync-design.md` section 7.
```

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md docs/superpowers/specs/2026-09-23-account-sign-in-sync-design.md
git commit -m "Docs: the account and project sync

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## Left untested on purpose

- The poller and sync threads, `SyncLoop` and the event wiring — glue over `poll_once()` / `run_pass()`, which are tested.
- The concurrent-refresh guard in `Account._refresh` — a two-thread race; covered by reasoning in its comment, not by a flaky timing test.
- `Qr.tsx`, `SignIn.tsx`, `AccountMenu.tsx` — rendering; checked by hand against the mock scenarios in Task 6 Step 11.
