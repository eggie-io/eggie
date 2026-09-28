# Public URL (VM side) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a signed-in user turn a temporary public URL on and off for a project from the web console, with the tunnel client, token and state all inside the VM.

**Architecture:** A new platform-free `runtime/omelet_api/core/public.py` owns the state machine (enable / disable / reconcile), the tunnel token file and a thin `TunnelClient` that drives a profile-gated `tunnel` service in `runtime/stack.yml` through the existing runner. `core/cloud.py` gains the three tunnel calls. `routes/app.py` exposes `/projects/{id}/public`, adds `public` to the project payload, and hooks delete, sign-out and the sync loop. The console maps the `public` field to one view in a pure `projects/public.ts` and renders it in the tile, a modal, the address rows and the project list.

**Tech Stack:** Python 3.12 (FastAPI, sqlite3, stdlib urllib), Docker Compose profiles, cloudflared, React + TanStack Query + MSW + Vitest.

**Spec:** `docs/superpowers/specs/2026-09-24-public-url-design.md`

## Global Constraints

- Everything lives in `runtime/`. No change under `host/`. `API_VERSION` stays `1`. `get.sh` is unchanged.
- `host/` never imports `omelet_api`, and `omelet_api` never imports `host` (existing AST tests).
- No platform branching (`sys.platform`, `platform.system()`, `os.name`) anywhere in `runtime/omelet_api/`.
- No test calls the Omelet service, Cloudflare or a real `docker`: use `FakeCloud`, fake runners and fake clocks.
- Repo files are reached from tests via `Path(__file__).resolve()`, never cwd-relative paths.
- The VM hardcodes no limits: no duration constant and no "one at a time" check. `expires_at` and the 409 come from the service.
- Public URLs are stored only while the row is `on`; leaving `on` clears `urls` and `expires_at`.
- Token file: `/opt/omelet/tunnel.token`, mode `0640`, written temp-then-rename, present only while a URL is on.
- The in-VM `omelet` CLI does not print or change public URLs.
- Service error body: `{"error": {"code", "message"}}`; our API's error body is the same shape via `ApiError`.
- cloudflared image pinned: `cloudflare/cloudflared:2026.9.3`.
- Release version for this work: `0.3.0` (api package, Dockerfile `SERVICE_VERSION`, `pyproject.toml`, stack api and web image tags).
- Comments: only for non-obvious edge cases, no ticket/doc references, short.
- Run Python tests with a writable temp dir: `TMPDIR=$PWD/.tmp python3 -m pytest ...` (create `.tmp` once with `mkdir -p .tmp`; it is git-ignored if listed, otherwise do not commit it).

## Review Focus

1. **Sign-out or delete while `enabling`** — the enable thread finishes after its row was cleared. Expected: it undoes itself (stops the client, removes the token, releases on the service) instead of writing `on`. Test in Task 3.
2. **Two projects, one `on`** — turning the other one off must not stop the tunnel client the first one needs. Test in Task 3.
3. **API restart with a URL on** — the first sync pass must restart the client if it is down, and end the URL if it expired while the API was down. Test in Task 4.
4. **Service answers 409 `public_url_active`** — the wording names the project that is on in this VM, and says "another project or computer" when none is. Test in Task 3.
5. **Countdown reaching zero in the console with no refetch** — the view flips to off with the expired note on the client clock alone. Test in Task 7.

---

## File Structure

| File | Responsibility |
|---|---|
| `runtime/omelet_api/core/cloud.py` (modify) | Three tunnel calls on the service client |
| `runtime/omelet_api/core/migrate.py` (modify) | `_v5_public_urls` |
| `runtime/omelet_api/core/state.py` (modify) | `public_urls` row access |
| `runtime/omelet_api/core/public.py` (create) | Token file, `TunnelClient`, `Public` state machine, wording |
| `runtime/omelet_api/core/account.py` (modify) | `on_forget` hook |
| `runtime/omelet_api/core/config.py` (modify) | `stack_file`, `tunnel_token_path` |
| `runtime/omelet_api/routes/app.py` (modify) | Routes, payload field, delete/sign-out/sync hooks |
| `runtime/stack.yml` (modify) | `tunnel` service and network |
| `runtime/install/install.sh` (modify) | Pull with `--profile tunnel` |
| `runtime/web/apps/console/src/projects/types.ts` (modify) | `PublicStatus` |
| `runtime/web/apps/console/src/projects/public.ts` (create) | `publicView`, `timeLeft`, `tileLine` |
| `runtime/web/apps/console/src/projects/queries.ts` (modify) | Poll while enabling, `usePublic` mutation |
| `runtime/web/apps/console/src/mocks/handlers.ts` (modify) | Public routes and scenarios |
| `runtime/web/apps/console/src/screens/project/PublicModal.tsx` (create) | The modal |
| `runtime/web/apps/console/src/screens/project/{Tiles,AddressRows,ProjectPage}.tsx` (modify) | Tile, public lines, wiring |
| `runtime/web/apps/console/src/screens/list/ProjectRow.tsx` (modify) | "Public" marker |
| Tests | `tests/runtime/api/test_cloud.py`, `test_migrate.py`, `test_public.py` (new), `test_api_public.py` (new), `tests/runtime/test_stack_yml.py`, `tests/runtime/test_install_shell.py`, `projects/public.test.ts` (new) |

---

### Task 1: Service client tunnel calls

**Files:**
- Modify: `runtime/omelet_api/core/cloud.py`
- Modify: `tests/runtime/api/fake_cloud.py`
- Test: `tests/runtime/api/test_cloud.py`

**Interfaces:**
- Produces on `Cloud`:
  - `create_public_url(token: str, cloud_id: str, hostnames: list[str], origin: str) -> dict`
  - `get_public_url(token: str, cloud_id: str) -> dict`
  - `release_public_url(token: str, cloud_id: str) -> None`
- Produces the same three methods on `FakeCloud`, recorded as `("create_public_url", token, cloud_id, hostnames, origin)`, `("get_public_url", token, cloud_id)`, `("release_public_url", token, cloud_id)`.

- [ ] **Step 1: Write the failing tests** (append to `tests/runtime/api/test_cloud.py`)

```python
def test_turning_a_public_url_on_sends_the_hostnames_and_origin():
    opener = Opener((201, json.dumps({"urls": []}).encode()))
    cloud = Cloud("https://svc/api", opener=opener)

    cloud.create_public_url("t", "c/1", ["blog.d.io"], "http://traefik:39080")

    request = opener.requests[0]
    assert (request.get_method(), request.full_url) == (
        "POST", "https://svc/api/v1/tunnels/projects/c%2F1/url")
    assert json.loads(request.data) == {"hostnames": ["blog.d.io"],
                                        "origin": "http://traefik:39080"}
    assert request.get_header("Authorization") == "Bearer t"


def test_a_refused_public_url_keeps_the_services_code():
    cloud = Cloud("https://svc/api", opener=Opener(
        (409, _error("public_url_active", "one is on"))))
    with pytest.raises(CloudError) as raised:
        cloud.create_public_url("t", "c1", ["blog.d.io"], "http://traefik:39080")
    assert (raised.value.code, raised.value.status) == ("public_url_active", 409)


def test_releasing_a_public_url_accepts_an_empty_204():
    opener = Opener((204, b""))
    cloud = Cloud("https://svc/api", opener=opener)

    assert cloud.release_public_url("t", "c1") is None
    assert opener.requests[0].get_method() == "DELETE"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `TMPDIR=$PWD/.tmp python3 -m pytest tests/runtime/api/test_cloud.py -q`
Expected: 3 failures with `AttributeError: 'Cloud' object has no attribute 'create_public_url'`.

- [ ] **Step 3: Implement** (append to the `Cloud` class in `runtime/omelet_api/core/cloud.py`)

```python
    def _public_url_path(self, cloud_id: str) -> str:
        return f"/v1/tunnels/projects/{urllib.parse.quote(cloud_id, safe='')}/url"

    def create_public_url(self, token: str, cloud_id: str, hostnames: list[str],
                          origin: str):
        return self.call("POST", self._public_url_path(cloud_id), token=token,
                         body={"hostnames": hostnames, "origin": origin})

    def get_public_url(self, token: str, cloud_id: str):
        return self.call("GET", self._public_url_path(cloud_id), token=token)

    def release_public_url(self, token: str, cloud_id: str):
        return self.call("DELETE", self._public_url_path(cloud_id), token=token)
```

And in `tests/runtime/api/fake_cloud.py`, add to `FakeCloud`:

```python
    def create_public_url(self, token, cloud_id, hostnames, origin):
        return self._next("create_public_url", token, cloud_id, hostnames, origin)

    def get_public_url(self, token, cloud_id):
        return self._next("get_public_url", token, cloud_id)

    def release_public_url(self, token, cloud_id):
        return self._next("release_public_url", token, cloud_id)
```

- [ ] **Step 4: Run to verify they pass**

Run: `TMPDIR=$PWD/.tmp python3 -m pytest tests/runtime/api/test_cloud.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add runtime/omelet_api/core/cloud.py tests/runtime/api/fake_cloud.py tests/runtime/api/test_cloud.py
git commit -m "Add the service's public URL calls to the cloud client"
```

---

### Task 2: `public_urls` table and state access

**Files:**
- Modify: `runtime/omelet_api/core/migrate.py`
- Modify: `runtime/omelet_api/core/state.py`
- Test: `tests/runtime/api/test_migrate.py`

**Interfaces:**
- Produces on `State`:
  - `get_public(local_id: str) -> dict | None` — keys `local_id, cloud_id, state, urls (list | None), expires_at, reason_code, reason_message`; `urls` is decoded from JSON.
  - `list_public() -> list[dict]` — same dicts, ordered by `local_id`.
  - `put_public(local_id: str, *, cloud_id: str, state: str, urls: list | None = None, expires_at: float | None = None, reason_code: str | None = None, reason_message: str | None = None) -> None` — replaces the whole row.
  - `delete_public(local_id: str) -> None`
  - `clear_public() -> None`

- [ ] **Step 1: Write the failing test** (append to `tests/runtime/api/test_migrate.py`)

```python
def test_a_v4_database_gains_public_urls_and_keeps_its_rows(tmp_path):
    conn = connect(tmp_path)
    for step in migrate.MIGRATIONS[:4]:
        step(conn)
    conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
    conn.execute("INSERT INTO schema_version(version) VALUES (4)")
    conn.execute("INSERT INTO projects(id, guest_path, domain) VALUES ('blog', '/g', 'd')")
    conn.execute("INSERT INTO cloud_projects VALUES ('blog', 'c1', 'org-1')")
    conn.commit()

    migrate.migrate(conn)

    assert "public_urls" in tables(conn)
    assert conn.execute("SELECT id FROM projects").fetchall() == [("blog",)]
    assert conn.execute("SELECT cloud_id FROM cloud_projects").fetchall() == [("c1",)]


def test_public_rows_round_trip_their_urls(tmp_path):
    state = State(tmp_path / "state.db")
    urls = [{"url": "https://k.example", "service": "web", "local_url": "http://b"}]

    state.put_public("blog", cloud_id="c1", state="on", urls=urls, expires_at=5.0)

    assert state.get_public("blog")["urls"] == urls
    state.put_public("blog", cloud_id="c1", state="ended", reason_code="expired")
    assert state.get_public("blog")["urls"] is None
```

- [ ] **Step 2: Run to verify they fail**

Run: `TMPDIR=$PWD/.tmp python3 -m pytest tests/runtime/api/test_migrate.py -q`
Expected: FAIL — `public_urls` missing / `State` has no `put_public`.

- [ ] **Step 3: Implement**

In `runtime/omelet_api/core/migrate.py`, after `_v4_account`:

```python
def _v5_public_urls(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS public_urls (
            local_id TEXT PRIMARY KEY,
            cloud_id TEXT NOT NULL,
            state TEXT NOT NULL,
            urls TEXT,
            expires_at REAL,
            reason_code TEXT,
            reason_message TEXT
        )""")
```

and append `_v5_public_urls,` to `MIGRATIONS`.

In `runtime/omelet_api/core/state.py`, add `import json` at the top and these methods before `close`:

```python
    @staticmethod
    def _public_row(row) -> dict:
        out = dict(row)
        out["urls"] = json.loads(out["urls"]) if out["urls"] else None
        return out

    def get_public(self, local_id):
        with self._lock:
            row = self._conn.execute("SELECT * FROM public_urls WHERE local_id=?",
                                     (local_id,)).fetchone()
        return self._public_row(row) if row else None

    def list_public(self):
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM public_urls ORDER BY local_id").fetchall()
        return [self._public_row(r) for r in rows]

    def put_public(self, local_id, *, cloud_id, state, urls=None, expires_at=None,
                   reason_code=None, reason_message=None):
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO public_urls(local_id, cloud_id, state, urls, "
                "expires_at, reason_code, reason_message) VALUES (?,?,?,?,?,?,?)",
                (local_id, cloud_id, state, json.dumps(urls) if urls else None,
                 expires_at, reason_code, reason_message))
            self._conn.commit()

    def delete_public(self, local_id):
        with self._lock:
            self._conn.execute("DELETE FROM public_urls WHERE local_id=?", (local_id,))
            self._conn.commit()

    def clear_public(self):
        with self._lock:
            self._conn.execute("DELETE FROM public_urls")
            self._conn.commit()
```

- [ ] **Step 4: Run to verify they pass**

Run: `TMPDIR=$PWD/.tmp python3 -m pytest tests/runtime/api/test_migrate.py tests/runtime/api/test_state.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add runtime/omelet_api/core/migrate.py runtime/omelet_api/core/state.py tests/runtime/api/test_migrate.py
git commit -m "Store public URL rows in the state database"
```

---

### Task 3: `core/public.py` — token, tunnel client, status, enable, disable

**Files:**
- Create: `runtime/omelet_api/core/public.py`
- Test: `tests/runtime/api/test_public.py`

**Interfaces:**
- Consumes: `State` public methods (Task 2); `Cloud`/`FakeCloud` tunnel calls (Task 1); `Account.authed`, `Account.signed_in`, `NotSignedIn`; `CloudError`, `CloudUnavailable`; `lifecycle.DOCKER`; `exec.Completed`.
- Produces:
  - `write_token(path: Path, token: str) -> None`, `remove_token(path: Path) -> None`
  - `class TunnelClient(runner, stack_file: Path)` with `start() -> Completed`, `stop() -> Completed`, `running() -> bool`
  - `class PublicBusy(Exception)`
  - `class Public(*, state, account, cloud, client, token_path: Path, origin: str, hosts_for: Callable[[str], list[dict]], clock=time.time, spawn=_daemon)` where `hosts_for(local_id)` returns `[{"service", "hostname", "local_url"}]` (empty when the project has no web service or cannot be read).
  - `Public.status(local_id) -> dict`, `Public.enable(local_id) -> dict` (raises `PublicBusy`, `Unavailable`), `Public.disable(local_id, *, force: bool = False) -> dict` (raises `PublicBusy` unless `force`)
  - `class Unavailable(Exception)` with `.code`, `.message`
  - `MESSAGES: dict[str, str]`

- [ ] **Step 1: Write the failing tests** — create `tests/runtime/api/test_public.py`

```python
import os
import stat

import pytest

from omelet_api.core.account import Account
from omelet_api.core.cloud import CloudError, CloudUnavailable
from omelet_api.core.exec import Completed
from omelet_api.core.public import (MESSAGES, Public, PublicBusy, TunnelClient,
                                    Unavailable)
from omelet_api.core.state import State
from tests.runtime.api.fake_cloud import FakeCloud

HOSTS = [{"service": "web", "hostname": "blog.d.io", "local_url": "http://blog.d.io:39080"}]
ON = {"id": "u1", "project_id": "c-blog",
      "urls": [{"hostname": "blog.d.io", "url": "https://k3x9.example.dev"}],
      "expires_at": "2026-09-24T15:00:00Z",
      "credentials": {"provider": "cloudflare", "token": "tun-1"}}
EXPIRES = 1790262000.0  # 2026-09-24T15:00:00Z


class Clock:
    def __init__(self, now=EXPIRES - 3600):
        self.now = now

    def __call__(self):
        return self.now


class TunnelRunner:
    """Answers the three compose calls TunnelClient makes; records argv."""

    def __init__(self, start_ok=True):
        self.calls = []
        self.start_ok = start_ok
        self.up = False

    def exec(self, argv, *, root=False):
        self.calls.append(argv)
        if "up" in argv:
            self.up = self.start_ok
            return Completed(0 if self.start_ok else 1, "", "" if self.start_ok else "boom")
        if "rm" in argv:
            self.up = False
            return Completed(0, "", "")
        if "ps" in argv:
            return Completed(0, "abc\n" if self.up else "", "")
        return Completed(0, "", "")


def make(tmp_path, cloud, *, hosts=HOSTS, start_ok=True, projects=("blog",),
         signed_in=True, mapped=("blog",)):
    state = State(tmp_path / "state.db")
    for pid in projects:
        state.add_project(pid, f"/p/{pid}", "d.io")
    if signed_in:
        state.update_account(access_token="at", refresh_token="rt",
                             access_expires_at=10**12, org_id="org-1")
    for pid in mapped:
        state.map_cloud_project(pid, f"c-{pid}", "org-1")
    account = Account(state, cloud, spawn=lambda fn: None)
    runner = TunnelRunner(start_ok)
    clock = Clock()
    public = Public(state=state, account=account, cloud=cloud,
                    client=TunnelClient(runner, tmp_path / "stack.yml"),
                    token_path=tmp_path / "tunnel.token", origin="http://traefik:39080",
                    hosts_for=lambda pid: list(hosts), clock=clock,
                    spawn=lambda fn: fn())
    return public, state, runner, clock


def test_turning_on_writes_a_narrow_token_starts_the_client_and_shows_the_urls(tmp_path):
    cloud = FakeCloud(create_public_url=[ON])
    public, _, runner, _ = make(tmp_path, cloud)

    public.enable("blog")

    token = tmp_path / "tunnel.token"
    assert token.read_text() == "tun-1"
    assert stat.S_IMODE(os.stat(token).st_mode) == 0o640
    assert runner.up
    assert cloud.calls == [("create_public_url", "at", "c-blog", ["blog.d.io"],
                            "http://traefik:39080")]
    assert public.status("blog") == {
        "state": "on", "expires_at": EXPIRES,
        "urls": [{"url": "https://k3x9.example.dev", "service": "web",
                  "local_url": "http://blog.d.io:39080"}]}


@pytest.mark.parametrize("kwargs,code", [
    ({"signed_in": False}, "signed_out"),
    ({"mapped": ()}, "not_registered"),
    ({"hosts": []}, "no_web"),
])
def test_turning_on_is_refused_with_a_plain_reason(tmp_path, kwargs, code):
    public, _, _, _ = make(tmp_path, FakeCloud(), **kwargs)

    with pytest.raises(Unavailable) as raised:
        public.enable("blog")

    assert (raised.value.code, raised.value.message) == (code, MESSAGES[code])
    assert public.status("blog") == {"state": "unavailable",
                                     "reason": {"code": code, "message": MESSAGES[code]}}


def test_one_already_on_in_this_vm_is_named(tmp_path):
    cloud = FakeCloud(create_public_url=[ON, CloudError("public_url_active", "x", 409)])
    public, _, _, _ = make(tmp_path, cloud, projects=("blog", "shop"), mapped=("blog", "shop"))
    public.enable("blog")

    public.enable("shop")

    assert public.status("shop") == {"state": "failed", "reason": {
        "code": "public_url_active",
        "message": 'Only one public address can be on at a time. Turn off the one on "blog" first.'}}


def test_one_already_on_elsewhere_says_so(tmp_path):
    cloud = FakeCloud(create_public_url=[CloudError("public_url_active", "x", 409)])
    public, _, _, _ = make(tmp_path, cloud)

    public.enable("blog")

    assert public.status("blog")["reason"]["message"] == MESSAGES["public_url_active"]


def test_an_unknown_service_refusal_shows_the_services_own_message(tmp_path):
    cloud = FakeCloud(create_public_url=[CloudError("weird", "try later", 400)])
    public, _, _, _ = make(tmp_path, cloud)

    public.enable("blog")

    assert public.status("blog")["reason"] == {
        "code": "weird", "message": "The Omelet service refused: try later"}


def test_a_client_that_will_not_start_is_released_on_the_service(tmp_path):
    cloud = FakeCloud(create_public_url=[ON], release_public_url=[None])
    public, _, runner, _ = make(tmp_path, cloud, start_ok=False)

    public.enable("blog")

    assert cloud.names() == ["create_public_url", "release_public_url"]
    assert not (tmp_path / "tunnel.token").exists()
    assert public.status("blog")["reason"]["code"] == "client_failed"


def test_turning_off_while_the_service_is_down_is_off_here_and_retried(tmp_path):
    cloud = FakeCloud(create_public_url=[ON], release_public_url=[CloudUnavailable("down")])
    public, state, runner, _ = make(tmp_path, cloud)
    public.enable("blog")

    assert public.disable("blog") == {"state": "off", "note": None}
    assert not runner.up and not (tmp_path / "tunnel.token").exists()
    assert state.get_public("blog")["state"] == "releasing"


def test_turning_one_off_keeps_the_client_another_project_needs(tmp_path):
    other = {**ON, "urls": [{"hostname": "blog.d.io", "url": "https://z.example.dev"}]}
    cloud = FakeCloud(create_public_url=[ON, other], release_public_url=[None])
    public, _, runner, _ = make(tmp_path, cloud, projects=("blog", "shop"),
                                mapped=("blog", "shop"))
    public.enable("blog")
    public.enable("shop")

    public.disable("blog")

    assert runner.up and (tmp_path / "tunnel.token").exists()


def test_an_enable_whose_row_was_cleared_meanwhile_undoes_itself(tmp_path):
    cloud = FakeCloud(create_public_url=[ON], release_public_url=[None])
    public, state, runner, _ = make(tmp_path, cloud)
    held = []
    public._spawn = held.append
    public.enable("blog")
    state.clear_public()  # a sign-out landed while the service call was in flight

    held[0]()

    assert state.get_public("blog") is None
    assert not runner.up and not (tmp_path / "tunnel.token").exists()
    assert cloud.names() == ["create_public_url", "release_public_url"]


def test_turning_off_while_turning_on_is_busy(tmp_path):
    public, _, _, _ = make(tmp_path, FakeCloud())
    public._spawn = lambda fn: None
    public.enable("blog")

    with pytest.raises(PublicBusy):
        public.disable("blog")
    with pytest.raises(PublicBusy):
        public.enable("blog")


def test_an_expired_url_reads_as_off_before_anything_cleans_up(tmp_path):
    public, _, _, clock = make(tmp_path, FakeCloud(create_public_url=[ON]))
    public.enable("blog")
    clock.now = EXPIRES

    assert public.status("blog") == {"state": "off", "note": {
        "code": "expired", "message": MESSAGES["expired"]}}
```

- [ ] **Step 2: Run to verify they fail**

Run: `TMPDIR=$PWD/.tmp python3 -m pytest tests/runtime/api/test_public.py -q`
Expected: collection error — `No module named 'omelet_api.core.public'`.

- [ ] **Step 3: Implement** — create `runtime/omelet_api/core/public.py`

```python
from __future__ import annotations

import logging
import os
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

from .account import NotSignedIn
from .cloud import CloudError, CloudUnavailable
from .lifecycle import DOCKER

log = logging.getLogger("omelet.public")

MESSAGES = {
    "signed_out": "Public addresses need an Omelet account. Sign in to use them.",
    "not_registered": "This project isn't linked to your account yet. Try again in a minute.",
    "no_web": "This project has no web page to share.",
    "public_url_active": "One is already on for another project or computer. "
                         "Turn it off there first.",
    "public_url_unavailable": "Your plan doesn't include public addresses.",
    "cloud_unavailable": "The Omelet service couldn't be reached. Check the "
                         "internet connection and try again.",
    "client_failed": "The public connection couldn't start on this computer.",
    "interrupted": "Omelet restarted while turning this on. Try again.",
    "expired": "The public address expired. Start a new one; it will be a "
               "different address.",
    "released_elsewhere": "The public address was turned off from the Omelet website.",
}


class PublicBusy(Exception):
    pass


class Unavailable(Exception):
    def __init__(self, code: str):
        super().__init__(MESSAGES[code])
        self.code = code
        self.message = MESSAGES[code]


def _daemon(fn) -> None:
    threading.Thread(target=fn, name="omelet-public", daemon=True).start()


def write_token(path: Path, token: str) -> None:
    # mkstemp opens at 0600, so the mode is narrowed before any byte lands.
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tunnel-")
    try:
        with os.fdopen(fd, "w") as f:
            os.fchmod(f.fileno(), 0o640)
            f.write(token)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def remove_token(path: Path) -> None:
    path.unlink(missing_ok=True)


class TunnelClient:
    def __init__(self, runner, stack_file: Path):
        self._runner = runner
        self._base = [DOCKER, "compose", "-f", str(stack_file), "--profile", "tunnel"]

    def start(self):
        return self._runner.exec([*self._base, "up", "-d", "--no-deps", "tunnel"])

    def stop(self):
        return self._runner.exec([*self._base, "rm", "-sf", "tunnel"])

    def running(self) -> bool:
        out = self._runner.exec([*self._base, "ps", "-q", "tunnel"])
        return out.ok and bool(out.stdout.strip())


def _epoch(value: str) -> float:
    return datetime.fromisoformat(value).timestamp()


def _reason(code: str, message: str | None = None) -> dict:
    return {"code": code, "message": message or MESSAGES[code]}


class Public:
    def __init__(self, *, state, account, cloud, client: TunnelClient,
                 token_path: Path, origin: str,
                 hosts_for: Callable[[str], list[dict]],
                 clock=time.time, spawn=_daemon):
        self._state = state
        self._account = account
        self._cloud = cloud
        self._client = client
        self._token_path = Path(token_path)
        self._origin = origin
        self._hosts_for = hosts_for
        self._clock = clock
        self._spawn = spawn
        self._lock = threading.Lock()
        self._enabling: set[str] = set()

    # --- reading ---------------------------------------------------------

    def _blocked(self, local_id: str) -> str | None:
        if not self._account.signed_in:
            return "signed_out"
        if local_id not in self._state.cloud_mapping():
            return "not_registered"
        if not self._hosts_for(local_id):
            return "no_web"
        return None

    def status(self, local_id: str) -> dict:
        row = self._state.get_public(local_id)
        if row is not None:
            if row["state"] == "on":
                if row["expires_at"] <= self._clock():
                    return {"state": "off", "note": _reason("expired")}
                return {"state": "on", "urls": row["urls"],
                        "expires_at": row["expires_at"]}
            if row["state"] == "enabling":
                return {"state": "enabling"}
            if row["state"] == "failed" or (row["state"] == "releasing"
                                            and row["reason_code"]):
                return {"state": "failed", "reason": _reason(
                    row["reason_code"], row["reason_message"])}
        blocked = self._blocked(local_id)
        if blocked:
            return {"state": "unavailable", "reason": _reason(blocked)}
        if row is not None and row["state"] == "ended":
            return {"state": "off", "note": _reason(row["reason_code"])}
        return {"state": "off", "note": None}

    # --- turning on ------------------------------------------------------

    def enable(self, local_id: str) -> dict:
        with self._lock:
            if local_id in self._enabling:
                raise PublicBusy(local_id)
            row = self._state.get_public(local_id)
            if row is not None and row["state"] == "on":
                return self.status(local_id)
            blocked = self._blocked(local_id)
            if blocked:
                raise Unavailable(blocked)
            cloud_id = self._state.cloud_mapping()[local_id]["cloud_id"]
            prior = row
            self._enabling.add(local_id)
            self._state.put_public(local_id, cloud_id=cloud_id, state="enabling")
        self._spawn(lambda: self._run_enable(local_id, cloud_id, prior))
        return self.status(local_id)

    def _run_enable(self, local_id: str, cloud_id: str, prior: dict | None) -> None:
        try:
            if prior is not None and prior["state"] == "releasing":
                if not self._release(prior["cloud_id"]):
                    self._state.put_public(local_id, cloud_id=prior["cloud_id"],
                                           state="releasing",
                                           reason_code="cloud_unavailable")
                    return
            self._turn_on(local_id, cloud_id)
        except Exception:
            log.exception("turning on the public URL of %s failed", local_id)
            self._fail(local_id, cloud_id, "client_failed")
        finally:
            with self._lock:
                self._enabling.discard(local_id)

    def _turn_on(self, local_id: str, cloud_id: str) -> None:
        hosts = self._hosts_for(local_id)
        try:
            out = self._account.authed(lambda token: self._cloud.create_public_url(
                token, cloud_id, [h["hostname"] for h in hosts], self._origin))
        except NotSignedIn:
            self._state.delete_public(local_id)
            return
        except CloudUnavailable:
            self._fail(local_id, cloud_id, "cloud_unavailable")
            return
        except CloudError as e:
            self._fail_from_service(local_id, cloud_id, e)
            return

        write_token(self._token_path, out["credentials"]["token"])
        started = self._client.start()
        still_wanted = (self._state.get_public(local_id) or {}).get("state") == "enabling"
        if not started.ok or not still_wanted:
            self._stop_unless_needed(local_id)
            self._release(cloud_id)
            if still_wanted:
                self._fail(local_id, cloud_id, "client_failed")
            return
        by_host = {h["hostname"]: h for h in hosts}
        urls = [{"url": u["url"], "service": by_host[u["hostname"]]["service"],
                 "local_url": by_host[u["hostname"]]["local_url"]}
                for u in out["urls"] if u["hostname"] in by_host]
        self._state.put_public(local_id, cloud_id=cloud_id, state="on", urls=urls,
                               expires_at=_epoch(out["expires_at"]))

    def _fail(self, local_id: str, cloud_id: str, code: str,
              message: str | None = None) -> None:
        if self._state.get_public(local_id) is None:
            return
        self._state.put_public(local_id, cloud_id=cloud_id, state="failed",
                               reason_code=code,
                               reason_message=message or MESSAGES.get(code))

    def _fail_from_service(self, local_id: str, cloud_id: str, e: CloudError) -> None:
        if e.code == "public_url_active":
            holder = next((r["local_id"] for r in self._state.list_public()
                           if r["state"] == "on" and r["local_id"] != local_id), None)
            message = (f'Only one public address can be on at a time. Turn off the '
                       f'one on "{holder}" first.') if holder else None
            self._fail(local_id, cloud_id, e.code, message)
        elif e.code in MESSAGES:
            self._fail(local_id, cloud_id, e.code)
        else:
            self._fail(local_id, cloud_id, e.code,
                       f"The Omelet service refused: {e.message}")

    # --- turning off -----------------------------------------------------

    def disable(self, local_id: str, *, force: bool = False) -> dict:
        with self._lock:
            if local_id in self._enabling and not force:
                raise PublicBusy(local_id)
        row = self._state.get_public(local_id)
        if row is None:
            return self.status(local_id)
        self._state.delete_public(local_id)
        self._stop_unless_needed(local_id)
        if row["state"] in ("on", "releasing", "enabling"):
            if not self._release(row["cloud_id"]):
                self._state.put_public(local_id, cloud_id=row["cloud_id"],
                                       state="releasing")
        return self.status(local_id)

    def _release(self, cloud_id: str) -> bool:
        """True once the service has no live URL for it."""
        try:
            self._account.authed(
                lambda token: self._cloud.release_public_url(token, cloud_id))
        except NotSignedIn:
            return True  # nothing can release it now; the service's expiry will
        except CloudError as e:
            if e.status != 404:
                log.warning("releasing public URL %s failed: %s", cloud_id, e)
                return False
        except CloudUnavailable:
            return False
        return True

    def _stop_unless_needed(self, local_id: str) -> None:
        if any(r["state"] == "on" and r["local_id"] != local_id
               for r in self._state.list_public()):
            return
        self._client.stop()
        remove_token(self._token_path)
```

- [ ] **Step 4: Run to verify they pass**

Run: `TMPDIR=$PWD/.tmp python3 -m pytest tests/runtime/api/test_public.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add runtime/omelet_api/core/public.py tests/runtime/api/test_public.py
git commit -m "Turn a project's public URL on and off inside the runtime"
```

---

### Task 4: Reconcile, sign-out and forget

**Files:**
- Modify: `runtime/omelet_api/core/public.py`
- Modify: `runtime/omelet_api/core/account.py`
- Test: `tests/runtime/api/test_public.py`

**Interfaces:**
- Consumes: everything from Task 3.
- Produces:
  - `Public.reconcile() -> None`
  - `Public.release_all() -> None` — best-effort service release of every live row (call before `Account.sign_out`).
  - `Public.forget_local() -> None` — stop the client, remove the token, `clear_public()`.
  - `Account.on_forget: Callable[[], None]`, default no-op, called at the end of `Account._forget`.

- [ ] **Step 1: Write the failing tests** (append to `tests/runtime/api/test_public.py`)

```python
def test_reconcile_ends_an_expired_url_and_stops_the_client(tmp_path):
    public, state, runner, clock = make(tmp_path, FakeCloud(create_public_url=[ON]))
    public.enable("blog")
    clock.now = EXPIRES + 1

    public.reconcile()

    row = state.get_public("blog")
    assert (row["state"], row["reason_code"], row["urls"]) == ("ended", "expired", None)
    assert not runner.up and not (tmp_path / "tunnel.token").exists()


def test_reconcile_notices_a_url_turned_off_from_the_website(tmp_path):
    cloud = FakeCloud(create_public_url=[ON],
                      get_public_url=[CloudError("not_found", "none", 404)])
    public, _, runner, _ = make(tmp_path, cloud)
    public.enable("blog")

    public.reconcile()

    assert public.status("blog")["note"]["code"] == "released_elsewhere"
    assert not runner.up


def test_reconcile_retries_a_release_and_forgets_the_row_once_done(tmp_path):
    cloud = FakeCloud(create_public_url=[ON],
                      release_public_url=[CloudUnavailable("down"), None])
    public, state, _, _ = make(tmp_path, cloud)
    public.enable("blog")
    public.disable("blog")

    public.reconcile()

    assert state.get_public("blog") is None


def test_reconcile_releases_an_enable_the_api_restart_interrupted(tmp_path):
    cloud = FakeCloud(release_public_url=[None])
    public, state, _, _ = make(tmp_path, cloud)
    state.put_public("blog", cloud_id="c-blog", state="enabling")

    public.reconcile()

    assert cloud.names() == ["release_public_url"]
    assert public.status("blog")["reason"]["code"] == "interrupted"


def test_reconcile_restarts_a_client_that_is_down_while_a_url_is_on(tmp_path):
    cloud = FakeCloud(create_public_url=[ON], get_public_url=[ON])
    public, _, runner, _ = make(tmp_path, cloud)
    public.enable("blog")
    runner.up = False  # the VM rebooted, or someone removed the container

    public.reconcile()

    assert runner.up


def test_reconcile_stops_a_client_nothing_needs(tmp_path):
    public, _, runner, _ = make(tmp_path, FakeCloud())
    runner.up = True
    (tmp_path / "tunnel.token").write_text("stale")

    public.reconcile()

    assert not runner.up and not (tmp_path / "tunnel.token").exists()


def test_reconcile_releases_the_url_of_a_project_deleted_meanwhile(tmp_path):
    cloud = FakeCloud(create_public_url=[ON], release_public_url=[None])
    public, state, _, _ = make(tmp_path, cloud)
    public.enable("blog")
    state.remove_project("blog")

    public.reconcile()

    assert state.get_public("blog") is None
    assert "release_public_url" in cloud.names()


def test_signing_out_releases_then_forgets_every_public_url(tmp_path):
    cloud = FakeCloud(create_public_url=[ON], release_public_url=[None], logout=[None])
    public, state, runner, _ = make(tmp_path, cloud)
    public._account.on_forget = public.forget_local
    public.enable("blog")

    public.release_all()
    public._account.sign_out()

    assert cloud.names() == ["create_public_url", "release_public_url", "logout"]
    assert state.list_public() == []
    assert not runner.up and not (tmp_path / "tunnel.token").exists()
```

- [ ] **Step 2: Run to verify they fail**

Run: `TMPDIR=$PWD/.tmp python3 -m pytest tests/runtime/api/test_public.py -q`
Expected: the new tests fail with `AttributeError` (`reconcile`, `release_all`, `forget_local`, `on_forget`).

- [ ] **Step 3: Implement**

In `runtime/omelet_api/core/account.py`, in `__init__` after `self.on_signed_in = lambda: None`:

```python
        self.on_forget = lambda: None
```

and at the end of `_forget`, after `self._state.clear_cloud_projects()` and outside the `with` block:

```python
        self.on_forget()
```

Append to `Public` in `runtime/omelet_api/core/public.py`:

```python
    # --- keeping it true -------------------------------------------------

    def reconcile(self) -> None:
        projects = {row["id"] for row in self._state.list_projects()}
        for row in self._state.list_public():
            try:
                self._reconcile_row(row, projects)
            except Exception:
                log.exception("reconciling the public URL of %s failed",
                              row["local_id"])
        wanted = any(r["state"] == "on" for r in self._state.list_public())
        running = self._client.running()
        if wanted and not running:
            if self._token_path.exists():
                self._client.start()
        elif not wanted and (running or self._token_path.exists()):
            self._client.stop()
            remove_token(self._token_path)

    def _reconcile_row(self, row: dict, projects: set[str]) -> None:
        local_id, state = row["local_id"], row["state"]
        with self._lock:
            if local_id in self._enabling:
                return
        if local_id not in projects:
            self.disable(local_id, force=True)
            if (self._state.get_public(local_id) or {}).get("state") != "releasing":
                self._state.delete_public(local_id)
            return
        if state == "on":
            if row["expires_at"] <= self._clock():
                self._end(row, "expired")
                return
            try:
                self._account.authed(lambda token: self._cloud.get_public_url(
                    token, row["cloud_id"]))
            except CloudError as e:
                if e.status == 404:
                    self._end(row, "released_elsewhere")
            except (CloudUnavailable, NotSignedIn):
                pass
        elif state == "releasing":
            if self._release(row["cloud_id"]):
                self._state.delete_public(local_id)
        elif state == "enabling":
            self._release(row["cloud_id"])
            self._fail(local_id, row["cloud_id"], "interrupted")

    def _end(self, row: dict, code: str) -> None:
        self._state.put_public(row["local_id"], cloud_id=row["cloud_id"],
                               state="ended", reason_code=code)
        self._stop_unless_needed(row["local_id"])

    def release_all(self) -> None:
        for row in self._state.list_public():
            if row["state"] in ("on", "enabling", "releasing"):
                self._release(row["cloud_id"])

    def forget_local(self) -> None:
        self._state.clear_public()
        self._client.stop()
        remove_token(self._token_path)
```

- [ ] **Step 4: Run to verify they pass**

Run: `TMPDIR=$PWD/.tmp python3 -m pytest tests/runtime/api/test_public.py tests/runtime/api/test_account.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add runtime/omelet_api/core/public.py runtime/omelet_api/core/account.py tests/runtime/api/test_public.py
git commit -m "Keep public URLs true across expiry, restarts and sign-out"
```

---

### Task 5: Config, routes, payload and hooks in the API

**Files:**
- Modify: `runtime/omelet_api/core/config.py`
- Modify: `runtime/omelet_api/routes/app.py`
- Create: `tests/runtime/api/test_api_public.py`

**Interfaces:**
- Consumes: `Public`, `TunnelClient`, `PublicBusy`, `Unavailable` (Tasks 3–4); `Account.on_forget` (Task 4).
- Produces:
  - `ApiConfig.stack_file: Path` (env `OMELET_STACK_FILE`, default `/opt/omelet/stack.yml`), `ApiConfig.tunnel_token_path: Path` (env `OMELET_TUNNEL_TOKEN`, default `/opt/omelet/tunnel.token`).
  - `create_app(..., public: Public | None = None)`; `app.state.public`.
  - Routes `GET|POST|DELETE /projects/{id}/public` on the shared router.
  - Project payload key `"public"` (the `Public.status` dict).

- [ ] **Step 1: Write the failing tests** — create `tests/runtime/api/test_api_public.py`

```python
from fastapi.testclient import TestClient

from omelet_api.core.account import Account
from omelet_api.core.public import Public, TunnelClient
from omelet_api.core.state import State
from omelet_api.routes.app import create_app
from tests.runtime.api.conftest import AUTH, COMPOSE_ONE_WEB, FakeRunner
from tests.runtime.api.fake_cloud import FakeCloud
from tests.runtime.api.test_public import ON, TunnelRunner


def build(env, cloud, *, tunnel_runner=None):
    state = State(env.config.state_db.with_name("public.db"))
    state.update_account(access_token="at", refresh_token="rt",
                         access_expires_at=10**12, org_id="org-1")
    account = Account(state, cloud, spawn=lambda fn: None)
    tunnel_runner = tunnel_runner or TunnelRunner()
    public = Public(state=state, account=account, cloud=cloud,
                    client=TunnelClient(tunnel_runner, env.config.stack_file),
                    token_path=env.config.projects_root.parent / "tunnel.token",
                    origin="http://traefik:41080",
                    hosts_for=lambda pid: [{"service": "web", "hostname": f"{pid}.test.local",
                                            "local_url": f"http://{pid}.test.local:41080"}],
                    spawn=lambda fn: fn())
    app = create_app(config=env.config, runner=FakeRunner(), state=state,
                     account=account, cloud=cloud, public=public)
    client = TestClient(app, headers=AUTH)
    client.post("/projects", json={"id": "blog"})
    state.map_cloud_project("blog", "c-blog", "org-1")
    (env.config.projects_root / "blog" / "docker-compose.yml").write_text(COMPOSE_ONE_WEB)
    return client, state, tunnel_runner


def test_turning_on_answers_202_and_the_project_shows_the_public_url(env):
    client, _, _ = build(env, FakeCloud(create_public_url=[ON]))

    assert client.post("/projects/blog/public").status_code == 202
    assert client.get("/projects/blog").json()["public"]["state"] == "on"


def test_turning_on_an_unregistered_project_is_a_409_with_the_reason(env):
    client, state, _ = build(env, FakeCloud())
    state.unmap_cloud_project("blog")

    resp = client.post("/projects/blog/public")

    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "not_registered"


def test_deleting_a_project_releases_its_public_url(env):
    cloud = FakeCloud(create_public_url=[ON], release_public_url=[None])
    client, state, tunnel = build(env, cloud)
    client.post("/projects/blog/public")

    client.delete("/projects/blog")

    assert "release_public_url" in cloud.names()
    assert state.get_public("blog") is None and not tunnel.up


def test_signing_out_releases_the_public_url(env):
    cloud = FakeCloud(create_public_url=[ON], release_public_url=[None], logout=[None])
    client, state, tunnel = build(env, cloud)
    client.post("/projects/blog/public")

    client.post("/account/sign-out")

    assert cloud.names()[-2:] == ["release_public_url", "logout"]
    assert state.list_public() == [] and not tunnel.up
```

- [ ] **Step 2: Run to verify they fail**

Run: `TMPDIR=$PWD/.tmp python3 -m pytest tests/runtime/api/test_api_public.py -q`
Expected: FAIL — `ApiConfig` has no `stack_file` / `create_app()` got an unexpected keyword `public`.

- [ ] **Step 3: Implement**

`runtime/omelet_api/core/config.py` — add fields after `cloud_url`:

```python
    stack_file: Path = Path(f"{constants.GUEST_ROOT}/stack.yml")
    tunnel_token_path: Path = Path(f"{constants.GUEST_ROOT}/tunnel.token")
```

and in `from_env`:

```python
            stack_file=Path(env.get("OMELET_STACK_FILE",
                                    f"{constants.GUEST_ROOT}/stack.yml")),
            tunnel_token_path=Path(env.get("OMELET_TUNNEL_TOKEN",
                                           f"{constants.GUEST_ROOT}/tunnel.token")),
```

`runtime/omelet_api/routes/app.py`:

1. Import: `from ..core.public import Public, PublicBusy, TunnelClient, Unavailable`.
2. Signature: add `public: Public | None = None` to `create_app`.
3. Replace the two sync lines and add `public` construction. `load` and `host_for` are defined later in the factory, so `hosts_for` is a closure that looks them up at call time:

```python
    cloud = cloud or Cloud(config.cloud_url)
    account = account or Account(state, cloud)

    def public_hosts(project_id: str) -> list[dict]:
        row = state.get_project(project_id)
        if row is None:
            return []
        try:
            project = load(project_id)
        except ApiError:
            return []
        hosts = [host_for(project.id, web, row["domain"]) for web in project.webs]
        return [{"service": web.service, "hostname": host,
                 "local_url": f"http://{host}:{config.edge_port}"}
                for web, host in zip(project.webs, hosts)]

    public = public or Public(
        state=state, account=account, cloud=cloud,
        client=TunnelClient(runner, config.stack_file),
        token_path=config.tunnel_token_path,
        origin=f"http://{config.traefik_host}:{config.edge_port}",
        hosts_for=public_hosts)
    account.on_forget = public.forget_local

    def sync_pass() -> None:
        public.reconcile()
        run_pass(account, cloud, state)

    sync = SyncLoop(sync_pass)
    account.on_signed_in = sync.wake
```

   and `app.state.public = public` next to the other `app.state` assignments.

4. In `payload(...)`, add `"public": public.status(row["id"]),` to the returned dict.
5. Sign-out route becomes:

```python
    @router.post("/account/sign-out")
    def account_sign_out() -> dict:
        public.release_all()
        return account.sign_out()
```

6. In `delete_project`, first line inside `with locks.held(project_id):`:

```python
            public.disable(project_id, force=True)
```

7. New routes, after `get_project`:

```python
    @router.get("/projects/{project_id}/public")
    def public_status(project_id: str) -> dict:
        require_row(project_id)
        return public.status(project_id)

    @router.post("/projects/{project_id}/public", status_code=202)
    def public_on(project_id: str) -> dict:
        require_row(project_id)
        try:
            return public.enable(project_id)
        except PublicBusy:
            raise _busy(project_id) from None
        except Unavailable as e:
            raise ApiError(e.code, e.message, 409) from None

    @router.delete("/projects/{project_id}/public")
    def public_off(project_id: str) -> dict:
        require_row(project_id)
        try:
            return public.disable(project_id)
        except PublicBusy:
            raise _busy(project_id) from None
```

- [ ] **Step 4: Run the new tests and the whole API suite**

Run: `TMPDIR=$PWD/.tmp python3 -m pytest tests/runtime/api -q`
Expected: all pass. If an existing test asserts the exact payload keys, add `"public"` to its expectation — the payload gained a field, nothing else changed.

- [ ] **Step 5: Commit**

```bash
git add runtime/omelet_api/core/config.py runtime/omelet_api/routes/app.py tests/runtime/api/test_api_public.py
git commit -m "Expose public URL routes and release them on delete and sign-out"
```

---

### Task 6: Tunnel service in the stack, pulled at install

**Files:**
- Modify: `runtime/stack.yml`
- Modify: `runtime/install/install.sh`
- Test: `tests/runtime/test_stack_yml.py`, `tests/runtime/test_install_shell.py`

**Interfaces:**
- Consumes: `TunnelClient`'s compose invocation from Task 3 (`--profile tunnel`, service name `tunnel`), `ApiConfig.tunnel_token_path` default `/opt/omelet/tunnel.token`.
- Produces: a `tunnel` compose service and a `tunnel` network.

- [ ] **Step 1: Write the failing tests**

Append to `tests/runtime/test_stack_yml.py`:

```python
def test_the_tunnel_client_runs_only_on_demand_from_a_token_file():
    tunnel = yaml.safe_load(_text())["services"]["tunnel"]
    assert tunnel["profiles"] == ["tunnel"]
    assert "--token-file" in tunnel["command"]
    assert "/opt/omelet/tunnel.token:/run/omelet/tunnel.token:ro" in tunnel["volumes"]
    assert "environment" not in tunnel, "the token must not travel in the environment"


def test_the_tunnel_client_reaches_only_traefik():
    # Cloudflare's config, not ours, picks where the client sends traffic.
    services = yaml.safe_load(_text())["services"]
    assert services["tunnel"]["networks"] == ["tunnel"]
    assert "tunnel" in services["traefik"]["networks"]
    for name in ("api", "web"):
        assert "tunnel" not in services[name]["networks"]
```

In `tests/runtime/test_install_shell.py`, change `test_install_always_pulls_before_bringing_the_stack_up`'s assertion to:

```python
    assert _index_of(f"compose -f {STACK} --profile tunnel pull") < _index_of(" up -d")
```

and add:

```python
def test_the_stack_comes_up_without_the_tunnel_profile():
    # The API starts the tunnel client only while a public URL is on.
    ups = [l for l in _commands() if f"compose -f {STACK}" in l and " up -d" in l]
    assert ups and not any("--profile" in l for l in ups)
```

- [ ] **Step 2: Run to verify they fail**

Run: `TMPDIR=$PWD/.tmp python3 -m pytest tests/runtime/test_stack_yml.py tests/runtime/test_install_shell.py -q`
Expected: the two stack tests fail with `KeyError: 'tunnel'`; the pull test fails on the missing `--profile tunnel`.

- [ ] **Step 3: Implement**

`runtime/stack.yml` — add `- tunnel` to `traefik.networks`, add the service after `web`:

```yaml
  # Started and stopped by the api service while a public URL is on; the
  # installer's profile-less `up -d` never starts it. Its own network: the
  # service's remote config picks the origin, and Traefik is all it may reach.
  tunnel:
    image: cloudflare/cloudflared:2026.9.3
    profiles: [tunnel]
    restart: unless-stopped
    command: tunnel --no-autoupdate run --token-file /run/omelet/tunnel.token
    volumes:
      - /opt/omelet/tunnel.token:/run/omelet/tunnel.token:ro
    # cloudflared runs as a non-root user; the token is 0640 root:docker.
    group_add:
      - "${OMELET_DOCKER_GID:-999}"
    networks:
      - tunnel
```

and under the top-level `networks:`:

```yaml
  tunnel: {}
```

`runtime/install/install.sh` — change the pull line inside the `if !` to:

```bash
if ! /usr/bin/docker compose -f /opt/omelet/stack.yml --profile tunnel pull 2>&1 | tee "$PULL_LOG"; then
```

and add one line to the comment above it: `# --profile tunnel: the first public URL must not wait on an image download.`

- [ ] **Step 4: Run to verify they pass**

Run: `TMPDIR=$PWD/.tmp python3 -m pytest tests/runtime -q`
Expected: all pass (including the existing "restart always" test, which only checks traefik/api/web).

- [ ] **Step 5: Commit**

```bash
git add runtime/stack.yml runtime/install/install.sh tests/runtime/test_stack_yml.py tests/runtime/test_install_shell.py
git commit -m "Add the profile-gated tunnel client to the runtime stack"
```

---

### Task 7: Console model — types, `publicView`, queries, mocks

**Files:**
- Modify: `runtime/web/apps/console/src/projects/types.ts`
- Create: `runtime/web/apps/console/src/projects/public.ts`
- Create: `runtime/web/apps/console/src/projects/public.test.ts`
- Modify: `runtime/web/apps/console/src/projects/queries.ts`
- Modify: `runtime/web/apps/console/src/projects/view.test.ts` (its `project()` fixture gains `public`)
- Modify: `runtime/web/apps/console/src/mocks/handlers.ts`

**Interfaces:**
- Produces (TypeScript):
  - `interface PublicUrl { url: string; service: string; local_url: string }`
  - `type PublicStatus = { state: "unavailable"; reason: Problem } | { state: "off"; note: Problem | null } | { state: "enabling" } | { state: "on"; urls: PublicUrl[]; expires_at: number } | { state: "failed"; reason: Problem }`
  - `Project.public: PublicStatus`
  - `type PublicView = { kind: "unavailable"; message: string } | { kind: "off"; note: string | null } | { kind: "enabling" } | { kind: "on"; urls: PublicUrl[]; left: string } | { kind: "failed"; message: string }`
  - `publicView(status: PublicStatus, nowMs: number): PublicView`
  - `timeLeft(expiresAtSec: number, nowMs: number): string`
  - `tileLine(view: PublicView): string`
  - `usePublic(id: string)` — a mutation taking `on: boolean`.

- [ ] **Step 1: Write the failing tests** — create `runtime/web/apps/console/src/projects/public.test.ts`

```ts
import { describe, expect, it } from "vitest";
import { EXPIRED_NOTE, publicView, tileLine, timeLeft } from "./public";
import type { PublicStatus } from "./types";

const at = (sec: number) => sec * 1000;
const on: PublicStatus = {
  state: "on",
  urls: [{ url: "https://k3x9.example.dev", service: "web", local_url: "http://b" }],
  expires_at: 10_000,
};

describe("publicView", () => {
  it("flips an on URL to off with the expired note the moment it runs out", () => {
    expect(publicView(on, at(9_999)).kind).toBe("on");
    expect(publicView(on, at(10_000))).toEqual({ kind: "off", note: EXPIRED_NOTE });
  });

  it("carries the API's own words for every other state", () => {
    const reason = { code: "no_web", message: "This project has no web page to share." };
    expect(publicView({ state: "unavailable", reason }, 0)).toEqual({ kind: "unavailable", message: reason.message });
    expect(publicView({ state: "failed", reason }, 0)).toEqual({ kind: "failed", message: reason.message });
    expect(publicView({ state: "off", note: reason }, 0)).toEqual({ kind: "off", note: reason.message });
  });
});

describe("timeLeft", () => {
  it("counts down in minutes, then hours and minutes, never below a minute", () => {
    expect(timeLeft(10_000, at(10_000 - 42 * 60))).toBe("42 min left");
    expect(timeLeft(10_000, at(10_000 - 65 * 60))).toBe("1 h 5 min left");
    expect(timeLeft(10_000, at(10_000 - 20))).toBe("under a minute left");
  });
});

describe("tileLine", () => {
  it("says what the tile is doing in a couple of words", () => {
    expect(tileLine(publicView(on, at(10_000 - 42 * 60)))).toBe("42 min left");
    expect(tileLine({ kind: "enabling" })).toBe("Turning on…");
    expect(tileLine({ kind: "failed", message: "x" })).toBe("Didn't work");
    expect(tileLine({ kind: "off", note: null })).toBe("Off");
  });
});
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd runtime/web && npm test -- public`
Expected: FAIL — cannot resolve `./public`.

- [ ] **Step 3: Implement**

`projects/types.ts` — add:

```ts
export interface PublicUrl {
  url: string;
  service: string;
  local_url: string;
}

export type PublicStatus =
  | { state: "unavailable"; reason: Problem }
  | { state: "off"; note: Problem | null }
  | { state: "enabling" }
  | { state: "on"; urls: PublicUrl[]; expires_at: number }
  | { state: "failed"; reason: Problem };
```

and `public: PublicStatus;` on `Project`.

`projects/public.ts`:

```ts
import type { PublicStatus, PublicUrl } from "./types";

// The API's own wording for its `expired` note; needed here because the
// countdown reaches zero before the next refetch does.
export const EXPIRED_NOTE = "The public address expired. Start a new one; it will be a different address.";

export type PublicView =
  | { kind: "unavailable"; message: string }
  | { kind: "off"; note: string | null }
  | { kind: "enabling" }
  | { kind: "on"; urls: PublicUrl[]; left: string }
  | { kind: "failed"; message: string };

export function timeLeft(expiresAtSec: number, nowMs: number): string {
  const minutes = Math.floor((expiresAtSec - nowMs / 1000) / 60);
  if (minutes < 1) return "under a minute left";
  if (minutes < 60) return `${minutes} min left`;
  return `${Math.floor(minutes / 60)} h ${minutes % 60} min left`;
}

export function publicView(status: PublicStatus, nowMs: number): PublicView {
  switch (status.state) {
    case "on":
      return status.expires_at * 1000 <= nowMs
        ? { kind: "off", note: EXPIRED_NOTE }
        : { kind: "on", urls: status.urls, left: timeLeft(status.expires_at, nowMs) };
    case "off":
      return { kind: "off", note: status.note?.message ?? null };
    case "enabling":
      return { kind: "enabling" };
    case "unavailable":
      return { kind: "unavailable", message: status.reason.message };
    case "failed":
      return { kind: "failed", message: status.reason.message };
  }
}

export function tileLine(view: PublicView): string {
  switch (view.kind) {
    case "on":
      return view.left;
    case "enabling":
      return "Turning on…";
    case "failed":
      return "Didn't work";
    case "unavailable":
      return view.message;
    case "off":
      return "Off";
  }
}
```

`projects/queries.ts` — poll fast while enabling, and add the mutation:

```ts
const settling = (project: Project) => project.job !== null || project.public.state === "enabling";
```

use it in `useProjects` (`query.state.data?.projects.some(settling)`) and `useProject` (`query.state.data && settling(query.state.data) ? BUSY_MS : IDLE_MS`), then:

```ts
export function usePublic(id: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (on: boolean) =>
      on ? api.post<PublicStatus>(`${projectPath(id)}/public`) : api.del<PublicStatus>(`${projectPath(id)}/public`),
    onSettled: () => client.invalidateQueries({ queryKey: ALL }),
  });
}
```

(import `PublicStatus` from `./types`).

`projects/view.test.ts` — add `public: { state: "off", note: null },` to its `project()` fixture defaults.

`mocks/handlers.ts`:
- Add `"public-on"`, `"public-expiring"`, `"public-active-elsewhere"`, `"public-unavailable"` to `SCENARIOS`.
- In `project()`, default `public: { state: "off", note: null }`.
- After seeding, for the `public-on` / `public-expiring` scenarios, set `recipe-box`'s `public` to `{ state: "on", urls: [{ url: "https://k3x9.trycloudflare.example", service: "web", local_url: address("recipe-box") }], expires_at: nowSec() + (scenario === "public-expiring" ? 20 : 42 * 60) }`.
- Add routes:

```ts
    http.post("/api/projects/:id/public", ({ params }) => {
      const target = projects.get(String(params.id));
      if (!target) return notFound(String(params.id));
      if (scenario === "public-unavailable")
        return refuse("not_registered", "This project isn't linked to your account yet. Try again in a minute.", 409);
      target.public = { state: "enabling" };
      window.setTimeout(() => {
        target.public =
          scenario === "public-active-elsewhere"
            ? { state: "failed", reason: { code: "public_url_active", message: "One is already on for another project or computer. Turn it off there first." } }
            : { state: "on", urls: target.web.map((w, i) => ({ url: `https://m${i}x7.trycloudflare.example`, service: w.service, local_url: w.url })), expires_at: nowSec() + 60 * 60 };
      }, 2000);
      return HttpResponse.json(target.public, { status: 202 });
    }),
    http.delete("/api/projects/:id/public", ({ params }) => {
      const target = projects.get(String(params.id));
      if (!target) return notFound(String(params.id));
      target.public = { state: "off", note: null };
      return HttpResponse.json(target.public);
    }),
```

- [ ] **Step 4: Run tests and typecheck**

Run: `cd runtime/web && npm test && npm run typecheck`
Expected: all pass; typecheck clean (it will point at every `Project` literal missing `public` — add the default there).

- [ ] **Step 5: Commit**

```bash
git add runtime/web/apps/console/src/projects runtime/web/apps/console/src/mocks/handlers.ts
git commit -m "Map a project's public URL status to what the console shows"
```

---

### Task 8: Console screens — tile, modal, address rows, list marker

**Files:**
- Create: `runtime/web/apps/console/src/screens/project/PublicModal.tsx`
- Modify: `runtime/web/apps/console/src/screens/project/Tiles.tsx`
- Modify: `runtime/web/apps/console/src/screens/project/AddressRows.tsx`
- Modify: `runtime/web/apps/console/src/screens/project/ProjectPage.tsx`
- Modify: `runtime/web/apps/console/src/screens/project/ProjectPage.module.css`
- Modify: `runtime/web/apps/console/src/screens/list/ProjectRow.tsx`

**Interfaces:**
- Consumes: `publicView`, `tileLine`, `PublicView` (Task 7); `usePublic` (Task 7); `useNow` (`projects/useNow.ts`); `openExternal` (`desktop/desktop.ts`); `Modal`, `Button`, `Notice` from `@omelet/ui`; `hostOf` (`projects/format.ts`); `actionError` (`projects/copy.ts`).
- Produces: `PublicModal({ project, open, onClose })`; `Tiles` gains `publicLine: string`, `publicDisabled: boolean`, `onPublic: () => void`; `AddressRows` gains an optional `publicUrls?: PublicUrl[]`.

No new tests: these components are rendering glue over `publicView`, which Task 7 tests (per the repo's testing rules).

- [ ] **Step 1: `PublicModal.tsx`**

```tsx
import type { ReactNode } from "react";
import { Button, Modal, Notice } from "@omelet/ui";
import { actionError } from "../../projects/copy";
import { hostOf } from "../../projects/format";
import { publicView } from "../../projects/public";
import { usePublic } from "../../projects/queries";
import type { Project } from "../../projects/types";
import { useNow } from "../../projects/useNow";
import { openExternal } from "../../desktop/desktop";
import { ARROW } from "../icons";
import { CopyButton } from "./AddressRows";
import s from "./ProjectPage.module.css";

export function PublicModal({ project, open, onClose }: { project: Project; open: boolean; onClose: () => void }) {
  const now = useNow();
  const toggle = usePublic(project.id);
  const view = publicView(project.public, now);
  const close = () => {
    toggle.reset();
    onClose();
  };

  let body: ReactNode;
  switch (view.kind) {
    case "unavailable":
      body = <p className={s.modalSub}>{view.message}</p>;
      break;
    case "off":
      body = (
        <>
          {view.note && <Notice>{view.note}</Notice>}
          <p className={s.modalSub}>
            Anyone with the link can open {project.id} until it expires. You get a new address each time.
          </p>
          <div className={s.modalFoot}>
            <Button variant="primary" disabled={toggle.isPending} onClick={() => toggle.mutate(true)}>Make it public</Button>
          </div>
        </>
      );
      break;
    case "enabling":
      body = <p className={s.modalSub}>Turning on…</p>;
      break;
    case "on":
      body = (
        <>
          <ul className={s.addresses}>
            {view.urls.map((entry) => (
              <li key={entry.url} className={s.address}>
                <span className={s.addressLabel}>{entry.service}</span>
                <span className={s.url}>{hostOf(entry.url)}</span>
                <CopyButton text={entry.url} />
                <Button variant="quiet" onClick={() => openExternal(entry.url)}>Open{ARROW}</Button>
              </li>
            ))}
          </ul>
          {project.status !== "started_ok" && <Notice>Start the project so visitors can see it.</Notice>}
          <div className={s.modalFoot}>
            <Button variant="danger" disabled={toggle.isPending} onClick={() => toggle.mutate(false)}>Turn off</Button>
            <span className={s.note}>{view.left}</span>
          </div>
        </>
      );
      break;
    case "failed":
      body = (
        <>
          <Notice>{view.message}</Notice>
          <div className={s.modalFoot}>
            <Button variant="primary" disabled={toggle.isPending} onClick={() => toggle.mutate(true)}>Try again</Button>
          </div>
        </>
      );
      break;
  }

  return (
    <Modal open={open} onClose={close} title={`Public address for ${project.id}`}>
      {body}
      {toggle.error && <Notice>{actionError(toggle.error)}</Notice>}
    </Modal>
  );
}
```

In `AddressRows.tsx`, change `function CopyButton` to `export function CopyButton`.

- [ ] **Step 2: `Tiles.tsx`** — replace the disabled placeholder:

```tsx
export function Tiles({ id, publicLine, publicDisabled, onPublic, onAnalyze, onDelete }: {
  id: string;
  publicLine: string;
  publicDisabled: boolean;
  onPublic: () => void;
  onAnalyze: () => void;
  onDelete: () => void;
}) {
  return (
    <div className={s.tiles}>
      <button type="button" className={s.tile} onClick={onAnalyze}>{MAGNIFIER}Analyze</button>
      <Link to={filesRoute(id, "")} className={s.tile}>{FOLDER}Files</Link>
      <button type="button" className={cx(s.tile, publicDisabled && s.off)} disabled={publicDisabled} onClick={onPublic}>
        {GLOBE}Public address<small>{publicLine}</small>
      </button>
      <button type="button" className={cx(s.tile, s.danger)} onClick={onDelete}>{TRASH}Delete</button>
    </div>
  );
}
```

- [ ] **Step 3: `AddressRows.tsx`** — accept `publicUrls?: PublicUrl[]` and, inside each `<li>`, after the existing content:

```tsx
            {publicUrls
              ?.filter((p) => p.local_url === entry.url)
              .map((p) => (
                <span key={p.url} className={s.publicLine}>
                  Public · <span className={s.url}>{hostOf(p.url)}</span> <CopyButton text={p.url} />
                </span>
              ))}
```

Add to `ProjectPage.module.css`:

```css
.publicLine { flex-basis: 100%; display: flex; align-items: center; gap: 8px; padding-left: 162px; font-size: 13.5px; color: var(--ink-2); }
@media (max-width: 560px) { .publicLine { padding-left: 0; } }
```

- [ ] **Step 4: `ProjectPage.tsx`** — wire it:
  - `const now = useNow();` and `const pub = query.data ? publicView(query.data.public, now) : null;` (above the early returns, with the other hooks).
  - Modal state type becomes `"analyze" | "delete" | "public" | null`.
  - `tiles` becomes `<Tiles id={project.id} publicLine={tileLine(pub!)} publicDisabled={pub!.kind === "unavailable"} onPublic={() => setModal("public")} onAnalyze={...} onDelete={...} />`.
  - Pass `publicUrls={pub?.kind === "on" ? pub.urls : undefined}` to both `AddressRows` uses.
  - Render `<PublicModal project={project} open={modal === "public"} onClose={() => setModal(null)} />` next to the other modals.

- [ ] **Step 5: `ProjectRow.tsx`** — show a marker on the project that is public:

```tsx
  const now = useNow(30_000);
  const pub = publicView(project.public, now);
```

and inside `.who`, after `{line}`: `{pub.kind === "on" && <span className={s.quiet}>Public · {pub.left}</span>}`.

- [ ] **Step 6: Typecheck, test, build, offline check, and look at it**

Run: `cd runtime/web && npm run typecheck && npm test && npm run build && npm run check-offline`
Expected: all clean.
Then `npm run dev` and open `?scenario=public-on`, `?scenario=public-expiring` (watch it flip to off with the expired note after ~20 s), `?scenario=public-active-elsewhere` (turn on, see the failed reason), `?scenario=public-unavailable`, and plain `?scenario=ok` (turn one on, see "Public · …" under its address and in the list).

- [ ] **Step 7: Commit**

```bash
git add runtime/web/apps/console/src/screens
git commit -m "Show and control a project's public address in the console"
```

---

### Task 9: Release bump and docs

**Files:**
- Modify: `runtime/omelet_api/__init__.py`, `runtime/omelet_api/Dockerfile`, `runtime/omelet_api/pyproject.toml`, `runtime/stack.yml` (api and web tags), `runtime/omelet_api/Dockerfile.debug` if it names the version
- Modify: `CLAUDE.md`

- [ ] **Step 1: Bump 0.2.0 → 0.3.0 in all version places**

Run: `grep -rn "0\.2\.0" runtime/omelet_api runtime/stack.yml runtime/web/package.json runtime/web/apps/console/package.json 2>/dev/null` and change each release number to `0.3.0` (leave unrelated dependency versions alone).

- [ ] **Step 2: Verify the constants test holds them equal**

Run: `TMPDIR=$PWD/.tmp python3 -m pytest tests/test_constants_agree.py -q`
Expected: pass.

- [ ] **Step 3: `CLAUDE.md`** — under Architecture → Layers, add after the `cloud.py / account.py / sync.py` bullet:

```markdown
- `runtime/omelet_api/core/public.py` — a project's temporary public URL. The service owns the
  Cloudflare tunnel and its routing; the VM asks for a URL, keeps the token in
  `/opt/omelet/tunnel.token` (0640, present only while a URL is on) and starts the
  profile-gated `tunnel` service in `stack.yml` through compose. The service rewrites Host to the
  project's local hostname, so overlays are unchanged; apps that build absolute URLs from Host
  send public visitors to `*.127-0-0-1.sslip.io`. Only the console turns it on; the CLI never
  shows it. Reconciled on every sync pass.
```

and under "Things that will bite you":

```markdown
- The `tunnel` service is behind a compose profile. A plain `docker compose -f stack.yml up -d`
  or `pull` never touches it; the API's own compose calls pass `--profile tunnel`, and so must
  anything else that means to include it.
```

- [ ] **Step 4: Full suites**

Run: `TMPDIR=$PWD/.tmp python3 -m pytest -q && (cd runtime/web && npm test && npm run typecheck)`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add runtime/omelet_api runtime/stack.yml CLAUDE.md
git commit -m "Release the runtime with public URLs as 0.3.0"
```

---

## After the tasks

- Open the PR to `main` from `feature/23-public-url`, referencing #23 and listing the service changes T1–T6 as a dependency.
- Run the code review in a separate agent after the PR is created.
- Manual live checks (spec section 9) stay open until a VM run: re-running the installer leaves a running `tunnel` container alone; cloudflared reads the 0640 token; `cloudflare/cloudflared:2026.9.3` pulls on both amd64 and arm64; end to end once the service ships.
