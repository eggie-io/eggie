# Connect GitHub Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One "Connect GitHub" button in the console signs every VM login account into `gh` and git over https as the user's GitHub account, and lets the user clone one of their repos as an Omelet project.

**Architecture:** The API (uid 1000, in a container) runs GitHub's OAuth Device Flow and keeps the token in a `0600` file. It writes a token-free `desired.json` with a monotonic generation. A root systemd path unit runs `github-apply.sh`, which applies that state to each account through `runuser` and echoes the generation back in `applied.json`. The console polls `/api/github` and shows "ready" only when the generations match. The clone runs as an API job with the token only in the child's environment.

**Tech Stack:** Python 3.12 / FastAPI / stdlib urllib (API); bash + systemd (guest); React + TanStack Query + msw (console); pytest, Vitest.

**Spec:** `docs/superpowers/specs/2026-09-24-connect-github-design.md`

## Global Constraints

- No host change. Nothing under `host/` is touched. `API_VERSION` stays `1`.
- No client secret anywhere. `client_id` default `Ov23lie5k9VqSCKI52Ci`, overridable with `OMELET_GITHUB_CLIENT_ID`.
- Scopes exactly `repo read:org workflow`.
- `user.name` = GitHub profile `name`, else `login`. `user.email` = the profile's public `email`, else `<id>+<login>@users.noreply.github.com`.
- The token never appears in an API response, a log line, job output, argv, a remote URL or `.git/config`.
- Token file `/opt/omelet/github/token` mode `0600`. `desired.json` `0640`. `applied.json` `0644`. The directory is `root:docker 2770`.
- Every write in a user home goes through `runuser -u <name>` with that account's `HOME`.
- `SETUP_TIMEOUT` = 30 s; token re-check interval = 300 s; UI poll = 2 s while pending or applying.
- No test reaches the network or GitHub. HTTP goes through fakes; shell scripts run with `bash` against fakes on `PATH`.
- `from __future__ import annotations`; comments only for edge cases (see `~/.claude/CLAUDE.md`).
- Tests use `TMPDIR=<writable dir>` in this WSL sandbox (`/tmp/pytest-of-$USER` is root-owned).

## Review Focus

1. **The `install.sh` permission sweep re-widens the token.** Step 4 runs `chmod -R g+rwX /opt/omelet` on every install, which would turn `0600` into `0660`. Expect the modes to be reasserted after the sweep. Pinned in Task 6's install test.
2. **`state.db` recreated while `applied.json` survives.** Someone deleting the db resets `generation` to 0. A new desired state must still go above the applied generation, or the UI shows "ready" for a state that was never applied. Pinned in Task 3.
3. **First install with no `desired.json`.** The apply script must not log out a `gh` the user signed into by hand before this feature existed. Pinned in Task 6.
4. **A clone that fails on a private repo.** The partial folder must be removed and no project row left. git's error text must reach the UI with the token redacted, and an auth failure moves the status to `needs_reconnect`. Pinned in Task 4.
5. **Disconnect while a device-code poll is in flight.** The late token must be dropped, not stored. Pinned in Task 3.

---

## File Structure

| File | Responsibility |
|---|---|
| `runtime/omelet_api/core/constants.py` (modify) | `GITHUB_DIR`, `GITHUB_CLIENT_ID` |
| `runtime/omelet_api/core/config.py` (modify) | `github_client_id`, `github_url`, `github_api_url`, `github_dir` |
| `runtime/omelet_api/core/migrate.py`, `state.py` (modify) | `github` row, `get_github()` / `update_github()` |
| `runtime/omelet_api/core/github.py` (create) | GitHub HTTP client, `identity_from`, `clone_argv`, `redact`, repo-name check |
| `runtime/omelet_api/core/github_link.py` (create) | Device-flow state machine, token/desired/applied files, setup state |
| `runtime/omelet_api/core/exec.py` (modify) | `LocalRunner.exec(..., env=None)` |
| `runtime/omelet_api/routes/app.py` (modify) | `/github*` routes, clone job |
| `runtime/omelet_api/Dockerfile` (modify) | `git` in the image |
| `runtime/stack.yml` (modify) | pass `OMELET_GITHUB_CLIENT_ID` |
| `runtime/install/lib/github-apply.sh` (create) | root apply of the desired state per account |
| `runtime/install/systemd/omelet-github.{path,service}` (create) | trigger on `desired.json` |
| `runtime/install/install.sh` (modify) | dir + modes, `safe.directory`, units, one apply |
| `runtime/instructions/omelet.md` (modify) | "never `gh auth login`" rule |
| `runtime/web/apps/console/src/github/github.ts` (create) | types, queries, mutations |
| `runtime/web/apps/console/src/github/view.ts` (create) | status → screen state + copy |
| `runtime/web/apps/console/src/screens/github/GitHubModal.tsx`, `RepoPicker.tsx`, `GitHubModal.module.css` (create) | connect flow, repo list, clone progress |
| `runtime/web/apps/console/src/screens/list/EmptyCounter.tsx`, `ProjectList.tsx`, `shell/AccountMenu.tsx` (modify) | entry points |
| `runtime/web/apps/console/src/projects/types.ts`, `copy.ts` (modify) | `"clone"` job kind, `"cloning"` phase caption |
| `runtime/web/apps/console/src/mocks/handlers.ts` (modify) | GitHub scenarios |

---

### Task 1: Config, constants and the `github` state row

**Files:**
- Modify: `runtime/omelet_api/core/constants.py`, `runtime/omelet_api/core/config.py`, `runtime/omelet_api/core/migrate.py`, `runtime/omelet_api/core/state.py`, `runtime/stack.yml`
- Test: `tests/runtime/api/test_config.py`, `tests/runtime/test_stack_yml.py`

**Interfaces:**
- Produces: `constants.GITHUB_DIR = "/opt/omelet/github"`, `constants.GITHUB_CLIENT_ID = "Ov23lie5k9VqSCKI52Ci"`; `ApiConfig.github_client_id: str`, `.github_url: str`, `.github_api_url: str`, `.github_dir: Path`; `State.get_github() -> dict` with keys `generation, desired_at, login, gh_id, name, email, needs_reconnect, last_error, checked_at`; `State.update_github(**fields) -> None` (raises `ValueError` on unknown fields).

- [ ] **Step 1: Write the failing tests**

Append to `tests/runtime/api/test_config.py`:

```python
def test_github_client_id_can_be_overridden_for_a_fork():
    config = ApiConfig.from_env({"OMELET_GITHUB_CLIENT_ID": "Iv1.fork"})
    assert config.github_client_id == "Iv1.fork"
    assert ApiConfig.from_env({}).github_client_id == "Ov23lie5k9VqSCKI52Ci"
```

Append to `tests/runtime/test_stack_yml.py`, reusing whatever helper that file already uses to load the `api` service as `api`:

```python
def test_the_api_receives_the_github_client_id_with_the_same_default():
    api = _services()["api"]
    assert ("OMELET_GITHUB_CLIENT_ID=${OMELET_GITHUB_CLIENT_ID:-Ov23lie5k9VqSCKI52Ci}"
            in api["environment"])
```

(If the file has no `_services()` helper, load it the way its existing tests do; do not add a second YAML loader.)

- [ ] **Step 2: Run to verify they fail**

Run: `TMPDIR=$PWD/.tmp python3 -m pytest tests/runtime/api/test_config.py tests/runtime/test_stack_yml.py -q`
Expected: FAIL — `AttributeError: 'ApiConfig' object has no attribute 'github_client_id'`, and the stack assertion fails.

- [ ] **Step 3: Implement**

`core/constants.py`, after `GUEST_TOKEN`:

```python
GITHUB_DIR = f"{GUEST_ROOT}/github"
# Omelet's OAuth App. Public by design: the Device Flow needs no secret.
GITHUB_CLIENT_ID = "Ov23lie5k9VqSCKI52Ci"
```

`core/config.py`: add fields after `cloud_url`:

```python
    github_client_id: str = constants.GITHUB_CLIENT_ID
    github_url: str = "https://github.com"
    github_api_url: str = "https://api.github.com"
    github_dir: Path = Path(constants.GITHUB_DIR)
```

and in `from_env` after `cloud_url=...`:

```python
            github_client_id=env.get("OMELET_GITHUB_CLIENT_ID",
                                     constants.GITHUB_CLIENT_ID),
            github_url=env.get("OMELET_GITHUB_URL", "https://github.com"),
            github_api_url=env.get("OMELET_GITHUB_API_URL",
                                   "https://api.github.com"),
            github_dir=Path(env.get("OMELET_GITHUB_DIR", constants.GITHUB_DIR)),
```

`core/migrate.py`: add before `MIGRATIONS`, and append `_v5_github` to the list:

```python
def _v5_github(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS github (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            generation INTEGER NOT NULL DEFAULT 0,
            desired_at REAL,
            login TEXT,
            gh_id INTEGER,
            name TEXT,
            email TEXT,
            needs_reconnect INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            checked_at REAL
        )""")
    conn.execute("INSERT OR IGNORE INTO github(id) VALUES (1)")
```

`core/state.py`: add next to `ACCOUNT_FIELDS`:

```python
GITHUB_FIELDS = frozenset({
    "generation", "desired_at", "login", "gh_id", "name", "email",
    "needs_reconnect", "last_error", "checked_at"})
```

and methods after `update_account`:

```python
    def get_github(self) -> dict:
        with self._lock:
            return dict(self._conn.execute(
                "SELECT * FROM github WHERE id=1").fetchone())

    def update_github(self, **fields) -> None:
        unknown = set(fields) - GITHUB_FIELDS
        if unknown:
            raise ValueError(f"unknown github fields: {sorted(unknown)}")
        if not fields:
            return
        assignments = ", ".join(f"{name}=?" for name in fields)
        with self._lock:
            self._conn.execute(f"UPDATE github SET {assignments} WHERE id=1",
                               tuple(fields.values()))
            self._conn.commit()
```

`runtime/stack.yml`, in the `api` service's `environment:` list after `OMELET_EDGE_PORT`:

```yaml
      - OMELET_GITHUB_CLIENT_ID=${OMELET_GITHUB_CLIENT_ID:-Ov23lie5k9VqSCKI52Ci}
```

- [ ] **Step 4: Run to verify they pass, plus the migration suite**

Run: `TMPDIR=$PWD/.tmp python3 -m pytest tests/runtime/api/test_config.py tests/runtime/test_stack_yml.py tests/runtime/api/test_migrate.py tests/runtime/api/test_state.py -q`
Expected: PASS. If `test_migrate.py` pins `SCHEMA_VERSION == 4`, update that literal to `5`. That is the one expected edit.

- [ ] **Step 5: Commit**

```bash
git add runtime/omelet_api/core/constants.py runtime/omelet_api/core/config.py runtime/omelet_api/core/migrate.py runtime/omelet_api/core/state.py runtime/stack.yml tests/runtime/api/test_config.py tests/runtime/test_stack_yml.py tests/runtime/api/test_migrate.py
git commit -m "Add GitHub config and state row"
```

---

### Task 2: GitHub HTTP client and pure helpers

**Files:**
- Create: `runtime/omelet_api/core/github.py`
- Test: `tests/runtime/api/test_github.py`

**Interfaces:**
- Produces:
  - `SCOPES = "repo read:org workflow"`
  - `class GitHubError(Exception)`: attrs `code: str`, `message: str`, `status: int`, `interval: float | None`
  - `class GitHubUnavailable(Exception)`
  - `class GitHub(web_url: str, api_url: str, *, opener=None, timeout=10.0)`, with methods:
    - `device_code(client_id) -> dict` (keys `device_code, user_code, verification_uri, expires_in, interval`)
    - `device_token(client_id, device_code) -> dict` (key `access_token`)
    - `user(token) -> dict`
    - `repos(token, page) -> tuple[list[dict], bool]`
  - `identity_from(user: dict) -> dict` (keys `login, gh_id, name, email`)
  - `valid_repo(full_name: str) -> bool`
  - `clone_argv(full_name: str, dest: Path) -> list[str]`
  - `redact(text: str, secret: str) -> str`
  - `auth_failed(git_output: str) -> bool`

- [ ] **Step 1: Write the failing tests**

`tests/runtime/api/test_github.py`:

```python
import io
import json
import urllib.error
from email.message import Message
from pathlib import Path

import pytest

from omelet_api.core.github import (GitHub, GitHubError, GitHubUnavailable,
                                    auth_failed, clone_argv, identity_from,
                                    redact, valid_repo)


class Reply:
    def __init__(self, body, status=200, headers=None):
        self.status = status
        self._raw = json.dumps(body).encode()
        self.headers = Message()
        for key, value in (headers or {}).items():
            self.headers[key] = value

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class Opener:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append(request)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def http_error(status, body):
    return urllib.error.HTTPError("https://x", status, "err", Message(),
                                  io.BytesIO(json.dumps(body).encode()))


def gh(*replies):
    opener = Opener(*replies)
    return GitHub("https://gh.test", "https://api.gh.test", opener=opener), opener


def test_a_device_token_error_sent_with_status_200_is_raised_with_its_code():
    client, _ = gh(Reply({"error": "slow_down", "interval": 10}))
    with pytest.raises(GitHubError) as caught:
        client.device_token("cid", "dc")
    assert (caught.value.code, caught.value.interval) == ("slow_down", 10)


def test_a_401_is_bad_credentials():
    client, _ = gh(http_error(401, {"message": "Bad credentials"}))
    with pytest.raises(GitHubError) as caught:
        client.user("tok")
    assert caught.value.code == "bad_credentials"


def test_a_network_failure_is_unavailable_not_an_error():
    client, _ = gh(OSError("no route"))
    with pytest.raises(GitHubUnavailable):
        client.user("tok")


def test_repos_reports_more_pages_from_the_link_header():
    page = [{"full_name": "octo/app", "private": True, "description": None,
             "updated_at": "2026-09-20T10:00:00Z"}]
    client, opener = gh(
        Reply(page, headers={"Link": '<https://api.gh.test/user/repos?page=2>; rel="next"'}),
        Reply(page))
    assert client.repos("tok", 1) == (page, True)
    assert client.repos("tok", 2) == (page, False)
    url = opener.requests[0].full_url
    assert "sort=updated" in url and "tok" not in url


def test_identity_falls_back_to_login_and_the_noreply_address():
    assert identity_from({"login": "octo", "id": 42, "name": "", "email": None}) == {
        "login": "octo", "gh_id": 42, "name": "octo",
        "email": "42+octo@users.noreply.github.com"}
    assert identity_from({"login": "octo", "id": 42, "name": "Octo Cat",
                          "email": "octo@example.com"})["email"] == "octo@example.com"


@pytest.mark.parametrize("name", ["../x", "a/b/c", "https://github.com/a/b",
                                  "a/..", "", "a b/c", "-x/y"])
def test_repo_names_that_are_not_owner_slash_name_are_refused(name):
    assert not valid_repo(name)


def test_clone_argv_keeps_the_token_out_of_argv_and_the_url():
    argv = clone_argv("octo/app", Path("/opt/omelet/projects/app"))
    assert "https://github.com/octo/app.git" in argv
    assert not any("@github.com" in word for word in argv)
    assert "$OMELET_GH_TOKEN" in " ".join(argv)
    assert argv[-1] == "/opt/omelet/projects/app"


def test_redact_and_auth_failed():
    assert redact("x gho_abc y", "gho_abc") == "x [token] y"
    assert redact("nothing", "") == "nothing"
    assert auth_failed("fatal: Authentication failed for 'https://github.com/a/b.git/'")
    assert not auth_failed("fatal: repository 'x' not found")
```

- [ ] **Step 2: Run to verify it fails**

Run: `TMPDIR=$PWD/.tmp python3 -m pytest tests/runtime/api/test_github.py -q`
Expected: FAIL — `ModuleNotFoundError: omelet_api.core.github`.

- [ ] **Step 3: Implement `runtime/omelet_api/core/github.py`**

```python
from __future__ import annotations

import http.client
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

SCOPES = "repo read:org workflow"
DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
_REPO = re.compile(r"[A-Za-z0-9_.][A-Za-z0-9_.-]*/[A-Za-z0-9_.][A-Za-z0-9_.-]*")
# The helper reads the token from the child's environment at call time, so it
# is never in argv, the remote URL or .git/config.
_HELPER = ('!f() { test "$1" = get && printf "username=x-access-token\\npassword=%s\\n" '
           '"$OMELET_GH_TOKEN"; }; f')


class GitHubError(Exception):
    def __init__(self, code: str, message: str = "", status: int = 0,
                 interval: float | None = None):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.status = status
        self.interval = interval


class GitHubUnavailable(Exception):
    pass


class GitHub:
    def __init__(self, web_url: str, api_url: str, *, opener=None,
                 timeout: float = 10.0):
        self._web = web_url.rstrip("/")
        self._api = api_url.rstrip("/")
        self._open = opener or urllib.request.urlopen
        self._timeout = timeout

    def _call(self, method: str, url: str, *, form: dict | None = None,
              token: str | None = None):
        data = None if form is None else urllib.parse.urlencode(form).encode()
        headers = {"Accept": "application/json", "User-Agent": "omelet",
                   "X-GitHub-Api-Version": "2022-11-28"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(url, data=data, headers=headers,
                                         method=method)
        try:
            with self._open(request, timeout=self._timeout) as response:
                raw, link = response.read(), response.headers.get("Link", "")
        except urllib.error.HTTPError as e:
            body = _json(e.read())
            message = str(body.get("message", "")) if isinstance(body, dict) else ""
            code = "bad_credentials" if e.code == 401 else f"http_{e.code}"
            raise GitHubError(code, message, e.code) from None
        except (OSError, http.client.HTTPException) as e:
            raise GitHubUnavailable(str(e)) from None
        body = _json(raw)
        # The device endpoints report pending/denied/expired as 200 + "error".
        if isinstance(body, dict) and "error" in body:
            interval = body.get("interval")
            raise GitHubError(str(body["error"]),
                              str(body.get("error_description", "")), 200,
                              float(interval) if interval else None)
        return body, link

    def device_code(self, client_id: str) -> dict:
        body, _ = self._call("POST", f"{self._web}/login/device/code",
                             form={"client_id": client_id, "scope": SCOPES})
        return body

    def device_token(self, client_id: str, device_code: str) -> dict:
        body, _ = self._call("POST", f"{self._web}/login/oauth/access_token",
                             form={"client_id": client_id,
                                   "device_code": device_code,
                                   "grant_type": DEVICE_GRANT})
        return body

    def user(self, token: str) -> dict:
        body, _ = self._call("GET", f"{self._api}/user", token=token)
        return body

    def repos(self, token: str, page: int) -> tuple[list[dict], bool]:
        query = urllib.parse.urlencode({
            "sort": "updated", "per_page": 50, "page": page,
            "affiliation": "owner,collaborator,organization_member"})
        body, link = self._call("GET", f"{self._api}/user/repos?{query}",
                                token=token)
        return body, 'rel="next"' in link


def _json(raw: bytes):
    try:
        return json.loads(raw) if raw else {}
    except ValueError:
        raise GitHubError("bad_response",
                          "GitHub sent something that is not JSON") from None


def identity_from(user: dict) -> dict:
    login = user["login"]
    return {"login": login, "gh_id": user["id"],
            "name": (user.get("name") or "").strip() or login,
            "email": ((user.get("email") or "").strip()
                      or f"{user['id']}+{login}@users.noreply.github.com")}


def valid_repo(full_name: str) -> bool:
    return (bool(_REPO.fullmatch(full_name))
            and ".." not in full_name.split("/"))


def clone_argv(full_name: str, dest: Path) -> list[str]:
    # umask 002 + sharedRepository=group: the agent's account is root on WSL2
    # and the macOS uid on Lima, never this API's uid 1000 -- it writes
    # through the docker group the projects folder hands down.
    return ["sh", "-c", 'umask 002 && exec "$@"', "sh",
            "git", "-c", "credential.helper=", "-c", f"credential.helper={_HELPER}",
            "-c", "core.sharedRepository=group",
            "clone", "--", f"https://github.com/{full_name}.git", str(dest)]


def redact(text: str, secret: str) -> str:
    return text.replace(secret, "[token]") if secret else text


def auth_failed(git_output: str) -> bool:
    lowered = git_output.lower()
    return ("authentication failed" in lowered
            or "could not read username" in lowered
            or "error: 403" in lowered)
```

- [ ] **Step 4: Run to verify it passes**

Run: `TMPDIR=$PWD/.tmp python3 -m pytest tests/runtime/api/test_github.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add runtime/omelet_api/core/github.py tests/runtime/api/test_github.py
git commit -m "Add GitHub HTTP client and clone helpers"
```

---

### Task 3: `GitHubLink` — device flow, files, setup state

**Files:**
- Create: `runtime/omelet_api/core/github_link.py`, `tests/runtime/api/fake_github.py`
- Test: `tests/runtime/api/test_github_link.py`

**Interfaces:**
- Consumes: `GitHub`, `GitHubError`, `GitHubUnavailable`, `identity_from` (Task 2); `State.get_github/update_github` (Task 1).
- Produces:
  - `SETUP_TIMEOUT = 30.0`, `CHECK_EVERY = 300.0`
  - `class NotConnected(Exception)`
  - `setup_state(row: dict, applied: dict | None, now: float) -> tuple[str, str | None]`
  - `read_applied(directory: Path) -> dict | None`
  - `class GitHubLink(state, github, *, client_id: str, directory: Path, clock=time.time, sleep=time.sleep, spawn=_daemon)`, with methods:
    - `status() -> dict`
    - `connect() -> dict`, which raises `GitHubUnavailable` / `GitHubError`
    - `disconnect() -> dict`
    - `reapply() -> dict`, which raises `NotConnected`
    - `poll_once() -> float | None`
    - `token() -> str`, which raises `NotConnected`
    - `mark_bad_credentials() -> None`
    - `check_token() -> None`

- [ ] **Step 1: Write the fake and the failing tests**

`tests/runtime/api/fake_github.py`:

```python
from omelet_api.core.github import GitHubError

CODE = {"device_code": "dc-1", "user_code": "WDJB-MJHT",
        "verification_uri": "https://github.com/login/device",
        "expires_in": 900, "interval": 5}
TOKEN = "gho_secret123"
USER = {"login": "octo", "id": 42, "name": "Octo Cat", "email": None}


class FakeGitHub:
    """Each method pops its next scripted reply; an Exception is raised, a
    callable is called (to act mid-request) and its result used."""

    def __init__(self, **scripts):
        self.scripts = {name: list(replies) for name, replies in scripts.items()}
        self.calls = []

    def _next(self, name, *args):
        self.calls.append((name, *args))
        reply = self.scripts[name].pop(0)
        if callable(reply) and not isinstance(reply, type):
            reply = reply()
        if isinstance(reply, Exception):
            raise reply
        return reply

    def device_code(self, client_id):
        return self._next("device_code", client_id)

    def device_token(self, client_id, device_code):
        return self._next("device_token", client_id, device_code)

    def user(self, token):
        return self._next("user", token)

    def repos(self, token, page):
        return self._next("repos", token, page)


def err(code, interval=None):
    return GitHubError(code, code, 401 if code == "bad_credentials" else 200, interval)
```

`tests/runtime/api/test_github_link.py`:

```python
import json
import stat

import pytest

from omelet_api.core.github import GitHubUnavailable
from omelet_api.core.github_link import (SETUP_TIMEOUT, GitHubLink,
                                         NotConnected, setup_state)
from omelet_api.core.state import State
from tests.runtime.api.fake_github import CODE, TOKEN, USER, FakeGitHub, err


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def make(tmp_path, github):
    clock, spawned = Clock(), []
    link = GitHubLink(State(tmp_path / "state.db"), github, client_id="cid",
                      directory=tmp_path / "github", clock=clock,
                      sleep=lambda s: None, spawn=spawned.append)
    return link, clock, spawned


def connected(tmp_path, **scripts):
    github = FakeGitHub(device_code=[CODE], device_token=[{"access_token": TOKEN}],
                        user=[USER], **scripts)
    link, clock, _ = make(tmp_path, github)
    link.connect()
    assert link.poll_once() is None
    return link, clock, github


def desired(tmp_path):
    return json.loads((tmp_path / "github" / "desired.json").read_text())


def test_connect_shows_the_code_and_starts_one_poller(tmp_path):
    link, clock, spawned = make(tmp_path, FakeGitHub(device_code=[CODE]))
    assert link.connect() == {"state": "pending", "user_code": "WDJB-MJHT",
                              "url": "https://github.com/login/device",
                              "expires_at": clock.now + 900}
    link.connect()
    assert len(spawned) == 1


def test_pending_waits_the_interval_and_slow_down_raises_it(tmp_path):
    link, _, _ = make(tmp_path, FakeGitHub(
        device_code=[CODE],
        device_token=[err("authorization_pending"), err("slow_down"),
                      err("slow_down", interval=30)]))
    link.connect()
    assert link.poll_once() == 5
    assert link.poll_once() == 10
    assert link.poll_once() == 30


@pytest.mark.parametrize("reply,code", [(err("access_denied"), "access_denied"),
                                        (err("expired_token"), "expired_token"),
                                        (err("incorrect_client_credentials"), "github_error")])
def test_a_refusal_ends_disconnected_with_its_reason(tmp_path, reply, code):
    link, _, _ = make(tmp_path, FakeGitHub(device_code=[CODE], device_token=[reply]))
    link.connect()
    assert link.poll_once() is None
    assert link.status() == {"state": "disconnected", "error": code}


def test_a_code_past_its_expiry_is_not_polled(tmp_path):
    github = FakeGitHub(device_code=[CODE], device_token=[])
    link, clock, _ = make(tmp_path, github)
    link.connect()
    clock.now += 901
    assert link.poll_once() is None
    assert link.status()["error"] == "expired_token"
    assert [c[0] for c in github.calls] == ["device_code"]


def test_a_network_failure_keeps_polling(tmp_path):
    link, _, _ = make(tmp_path, FakeGitHub(device_code=[CODE],
                                           device_token=[GitHubUnavailable("x")]))
    link.connect()
    assert link.poll_once() == 5
    assert link.status()["state"] == "pending"


def test_approval_stores_the_token_privately_and_writes_a_token_free_desired_state(tmp_path):
    link, _, _ = connected(tmp_path)
    token_file = tmp_path / "github" / "token"
    assert token_file.read_text() == TOKEN
    assert stat.S_IMODE(token_file.stat().st_mode) == 0o600
    doc = desired(tmp_path)
    assert doc == {"generation": 1, "state": "connected", "login": "octo",
                   "name": "Octo Cat", "email": "42+octo@users.noreply.github.com"}
    status = link.status()
    assert (status["state"], status["setup"]) == ("connected", "applying")
    assert TOKEN not in json.dumps(status)


def test_a_token_arriving_after_disconnect_is_dropped(tmp_path):
    holder = {}
    github = FakeGitHub(device_code=[CODE], device_token=[
        lambda: (holder["link"].disconnect(), {"access_token": TOKEN})[1]])
    link, _, _ = make(tmp_path, github)
    holder["link"] = link
    link.connect()
    assert link.poll_once() is None
    assert not (tmp_path / "github" / "token").exists()
    assert link.status()["state"] == "disconnected"


def test_generation_climbs_past_an_applied_file_left_by_a_lost_database(tmp_path):
    (tmp_path / "github").mkdir()
    (tmp_path / "github" / "applied.json").write_text(
        json.dumps({"generation": 9, "ok": True, "accounts": []}))
    link, _, _ = connected(tmp_path)
    assert desired(tmp_path)["generation"] == 10
    assert link.status()["setup"] == "applying"


def test_disconnect_drops_the_token_and_asks_for_logout_at_a_new_generation(tmp_path):
    link, _, _ = connected(tmp_path)
    assert link.disconnect() == {"state": "disconnected", "error": None}
    assert not (tmp_path / "github" / "token").exists()
    assert desired(tmp_path) == {"generation": 2, "state": "disconnected"}


def test_bad_credentials_needs_a_reconnect_without_a_new_generation(tmp_path):
    link, _, _ = connected(tmp_path)
    link.mark_bad_credentials()
    assert link.status() == {"state": "needs_reconnect", "login": "octo"}
    assert not (tmp_path / "github" / "token").exists()
    assert desired(tmp_path)["generation"] == 1
    with pytest.raises(NotConnected):
        link.token()


def test_the_token_is_rechecked_at_most_every_five_minutes(tmp_path):
    link, clock, github = connected(tmp_path, )
    github.scripts["user"] = [GitHubUnavailable("x"), err("bad_credentials")]
    clock.now += 301
    link.check_token()
    assert link.status()["state"] == "connected"
    link.check_token()
    assert len([c for c in github.calls if c[0] == "user"]) == 2
    clock.now += 301
    link.check_token()
    assert link.status()["state"] == "needs_reconnect"


def test_reapply_rereads_the_profile_and_bumps_the_generation(tmp_path):
    link, _, github = connected(tmp_path)
    github.scripts["user"] = [{**USER, "name": "", "email": "o@example.com"}]
    link.reapply()
    assert desired(tmp_path)["generation"] == 2
    assert (desired(tmp_path)["name"], desired(tmp_path)["email"]) == ("octo", "o@example.com")


def test_reapply_while_disconnected_is_refused(tmp_path):
    link, _, _ = make(tmp_path, FakeGitHub())
    with pytest.raises(NotConnected):
        link.reapply()


ROW = {"generation": 3, "desired_at": 1000.0}


@pytest.mark.parametrize("applied,now,expected", [
    ({"generation": 3, "ok": True}, 1001, ("ready", None)),
    ({"generation": 3, "ok": False, "error": "root: gh failed"}, 1001, ("failed", "root: gh failed")),
    ({"generation": 2, "ok": True}, 1000 + SETUP_TIMEOUT - 1, ("applying", None)),
    (None, 1000 + SETUP_TIMEOUT - 1, ("applying", None)),
    (None, 1000 + SETUP_TIMEOUT, ("runtime_outdated", None)),
    ({"generation": 2, "ok": True}, 1000 + SETUP_TIMEOUT, ("failed", "setup_timeout")),
])
def test_setup_state(applied, now, expected):
    assert setup_state(ROW, applied, now) == expected
```

- [ ] **Step 2: Run to verify it fails**

Run: `TMPDIR=$PWD/.tmp python3 -m pytest tests/runtime/api/test_github_link.py -q`
Expected: FAIL — `ModuleNotFoundError: omelet_api.core.github_link`.

- [ ] **Step 3: Implement `runtime/omelet_api/core/github_link.py`**

```python
from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path

from .github import GitHubError, GitHubUnavailable, identity_from

SETUP_TIMEOUT = 30.0
CHECK_EVERY = 300.0
SLOW_DOWN_STEP = 5.0
_REFUSED = {"access_denied", "expired_token"}

log = logging.getLogger("omelet.github")


class NotConnected(Exception):
    pass


def _daemon(fn) -> None:
    threading.Thread(target=fn, name="omelet-github", daemon=True).start()


def _write(path: Path, text: str, mode: int) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "w") as f:
        os.fchmod(f.fileno(), mode)
        f.write(text)
    os.replace(tmp, path)


def read_applied(directory: Path) -> dict | None:
    try:
        data = json.loads((directory / "applied.json").read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def setup_state(row: dict, applied: dict | None, now: float) -> tuple[str, str | None]:
    if applied is not None and applied.get("generation") == row["generation"]:
        if applied.get("ok"):
            return "ready", None
        return "failed", applied.get("error") or "setup_failed"
    if now - (row["desired_at"] or 0) < SETUP_TIMEOUT:
        return "applying", None
    # install.sh runs the apply script once, so a VM that can apply always
    # has applied.json; none at all means the runtime predates this feature.
    if applied is None:
        return "runtime_outdated", None
    return "failed", "setup_timeout"


class GitHubLink:
    def __init__(self, state, github, *, client_id: str, directory: Path,
                 clock=time.time, sleep=time.sleep, spawn=_daemon):
        self._state = state
        self._github = github
        self._client_id = client_id
        self._dir = Path(directory)
        self._clock = clock
        self._sleep = sleep
        self._spawn = spawn
        self._lock = threading.RLock()
        self._poll_lock = threading.Lock()
        self._polling = False
        # Memory only: the device code plus the public client_id is enough to
        # claim the token.
        self._pending: dict | None = None

    @property
    def _token_path(self) -> Path:
        return self._dir / "token"

    def token(self) -> str:
        with self._lock:
            row = self._state.get_github()
            if not row["login"] or row["needs_reconnect"]:
                raise NotConnected()
            try:
                return self._token_path.read_text().strip()
            except OSError:
                raise NotConnected() from None

    def status(self) -> dict:
        with self._lock:
            if self._pending:
                return {"state": "pending", "user_code": self._pending["user_code"],
                        "url": self._pending["url"],
                        "expires_at": self._pending["expires_at"]}
            row = self._state.get_github()
            if row["needs_reconnect"]:
                return {"state": "needs_reconnect", "login": row["login"]}
            if row["login"] and self._token_path.exists():
                setup, error = setup_state(row, read_applied(self._dir), self._clock())
                return {"state": "connected", "login": row["login"],
                        "name": row["name"], "email": row["email"],
                        "setup": setup, "setup_error": error}
            return {"state": "disconnected", "error": row["last_error"]}

    def connect(self) -> dict:
        with self._lock:
            if self._pending and self._pending["expires_at"] > self._clock():
                return self.status()
            if self.status()["state"] == "connected":
                return self.status()
        out = self._github.device_code(self._client_id)
        with self._lock:
            self._pending = {"device_code": out["device_code"],
                             "user_code": out["user_code"],
                             "url": out["verification_uri"],
                             "expires_at": self._clock() + out["expires_in"],
                             "interval": float(out.get("interval") or 5)}
            self._state.update_github(last_error=None)
        self._ensure_poller()
        return self.status()

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
                log.exception("GitHub poll failed")
                wait = 5.0
            if wait is not None:
                self._sleep(wait)
                continue
            with self._poll_lock:
                if not self._pending:
                    self._polling = False
                    return

    def poll_once(self) -> float | None:
        with self._lock:
            pending = self._pending
            if not pending:
                return None
            if self._clock() >= pending["expires_at"]:
                self._end(pending, "expired_token")
                return None
        try:
            out = self._github.device_token(self._client_id, pending["device_code"])
            error = None
        except GitHubUnavailable:
            return pending["interval"]
        except GitHubError as e:
            out, error = None, e

        with self._lock:
            if self._pending is not pending:
                return self._pending["interval"] if self._pending else None
            if error is not None:
                if error.code == "authorization_pending":
                    return pending["interval"]
                if error.code == "slow_down":
                    pending["interval"] = max(pending["interval"] + SLOW_DOWN_STEP,
                                              error.interval or 0)
                    return pending["interval"]
                self._end(pending, error.code if error.code in _REFUSED
                          else "github_error")
                return None
        token = out["access_token"]
        try:
            identity = identity_from(self._github.user(token))
        except (GitHubError, GitHubUnavailable):
            with self._lock:
                if self._pending is pending:
                    self._end(pending, "github_error")
            return None
        with self._lock:
            if self._pending is not pending:
                return None
            self._pending = None
            self._dir.mkdir(parents=True, exist_ok=True)
            _write(self._token_path, token, 0o600)
            self._state.update_github(needs_reconnect=0, last_error=None,
                                      checked_at=self._clock(), **identity)
            self._write_desired("connected")
        return None

    def _end(self, pending: dict, error: str) -> None:
        if self._pending is pending:
            self._pending = None
            self._state.update_github(last_error=error)

    def _write_desired(self, state: str) -> None:
        row = self._state.get_github()
        applied = read_applied(self._dir) or {}
        applied_gen = applied.get("generation")
        # A lost state.db restarts at 0; the next generation must still pass
        # whatever the script last applied, or "ready" would match stale work.
        generation = max(row["generation"],
                         applied_gen if isinstance(applied_gen, int) else 0) + 1
        doc = {"generation": generation, "state": state}
        if state == "connected":
            doc.update(login=row["login"], name=row["name"], email=row["email"])
        self._dir.mkdir(parents=True, exist_ok=True)
        _write(self._dir / "desired.json", json.dumps(doc), 0o640)
        self._state.update_github(generation=generation, desired_at=self._clock())

    def disconnect(self) -> dict:
        with self._lock:
            self._pending = None
            self._token_path.unlink(missing_ok=True)
            self._state.update_github(login=None, gh_id=None, name=None, email=None,
                                      needs_reconnect=0, last_error=None,
                                      checked_at=None)
            self._write_desired("disconnected")
            return self.status()

    def reapply(self) -> dict:
        token = self.token()
        try:
            identity = identity_from(self._github.user(token))
        except GitHubError as e:
            if e.code == "bad_credentials":
                self.mark_bad_credentials()
                raise NotConnected() from None
            raise
        with self._lock:
            self._state.update_github(**identity)
            self._write_desired("connected")
            return self.status()

    def mark_bad_credentials(self) -> None:
        # No new generation: the accounts keep the dead token until the user
        # reconnects or disconnects, and either writes a fresh desired state.
        with self._lock:
            self._token_path.unlink(missing_ok=True)
            self._state.update_github(needs_reconnect=1)

    def check_token(self) -> None:
        with self._lock:
            row = self._state.get_github()
            if (self.status()["state"] != "connected"
                    or self._clock() - (row["checked_at"] or 0) < CHECK_EVERY):
                return
            self._state.update_github(checked_at=self._clock())
            token = self._token_path.read_text().strip()
        try:
            self._github.user(token)
        except GitHubError as e:
            if e.code == "bad_credentials":
                self.mark_bad_credentials()
        except GitHubUnavailable:
            pass
```

Note on `token()` refusing during `needs_reconnect`: the token file is already gone then. The check is explicit so a stray file can't bring back a revoked link.

- [ ] **Step 4: Run to verify it passes**

Run: `TMPDIR=$PWD/.tmp python3 -m pytest tests/runtime/api/test_github_link.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add runtime/omelet_api/core/github_link.py tests/runtime/api/fake_github.py tests/runtime/api/test_github_link.py
git commit -m "Add GitHub device-flow link and desired-state files"
```

---

### Task 4: Routes, the clone job, `LocalRunner` env, `git` in the image

**Files:**
- Modify: `runtime/omelet_api/routes/app.py`, `runtime/omelet_api/core/exec.py`, `runtime/omelet_api/Dockerfile`, `tests/runtime/api/conftest.py`
- Test: `tests/runtime/api/test_api_github.py`, `tests/runtime/api/test_dockerfile.py`

**Interfaces:**
- Consumes: Tasks 2–3.
- Produces:
  - HTTP routes: `GET /github`, `POST /github/connect`, `POST /github/disconnect`, `POST /github/reapply`, `GET /github/repos?page=n` → `{repos: [{full_name, private, description, updated_at: float epoch seconds}], has_more}`, `POST /github/clone {repo, id?}` → 202 `{job_id, id}`.
  - Error codes: `github_unavailable` 503, `github_error` 502, `github_not_connected` 409, `github_reconnect` 409, `invalid_repo` 422, `project_exists` 409.
  - Job kind `"clone"`, phase `"cloning"`.
  - `create_app(..., github=None, github_link=None)`.
  - `LocalRunner.exec(argv, *, root=False, env=None)`.

- [ ] **Step 1: Teach `FakeRunner` about `env` and the clone**

In `tests/runtime/api/conftest.py`, in `FakeRunner.__init__` add:

```python
        self.envs = []
        self.clone = Completed(0, "", "")
        # Called with the destination path before `clone` is returned, so a
        # test can put files where a real clone would.
        self.on_clone = None
```

change the signature and add the clone branch first in `exec`:

```python
    def exec(self, argv, *, root=False, env=None):
        self.calls.append(argv)
        self.envs.append(env)
        if argv[0] == "sh" and "clone" in argv:
            if self.on_clone is not None:
                self.on_clone(argv[-1])
            return self._reply(self.clone)
```

- [ ] **Step 2: Write the failing route tests**

`tests/runtime/api/test_api_github.py`:

```python
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from omelet_api.core.exec import Completed
from omelet_api.core.github import GitHubUnavailable
from omelet_api.core.github_link import GitHubLink
from omelet_api.core.state import State
from omelet_api.routes.app import create_app
from tests.runtime.api.conftest import AUTH, COMPOSE_ONE_WEB, FakeProbe, FakeRunner
from tests.runtime.api.fake_github import CODE, TOKEN, USER, FakeGitHub, err

REPO = {"full_name": "octo/app", "private": True, "description": "An app",
        "updated_at": "2026-09-20T10:00:00Z", "clone_url": "ignored"}


def make(env, github, *, connect=True):
    state = State(env.config.state_db.with_name("gh.db"))
    link = GitHubLink(state, github, client_id="cid",
                      directory=env.config.state_db.with_name("github"),
                      spawn=lambda fn: None)
    if connect:
        link.connect()
        link.poll_once()
    runner = FakeRunner()
    app = create_app(config=env.config, runner=runner, state=state,
                     http_probe=FakeProbe(), github=github, github_link=link)
    return TestClient(app, headers=AUTH), runner, app, link


def connected_github(**scripts):
    return FakeGitHub(device_code=[CODE], device_token=[{"access_token": TOKEN}],
                      user=[USER], **scripts)


def finish(app, client, resp):
    job_id = resp.json()["job_id"]
    app.state.jobs.wait(job_id, timeout=5)
    return client.get(f"/jobs/{job_id}").json()


def test_connect_says_when_github_cannot_be_reached(env):
    client, *_ = make(env, FakeGitHub(device_code=[GitHubUnavailable("x")]), connect=False)
    resp = client.post("/github/connect")
    assert (resp.status_code, resp.json()["error"]["code"]) == (503, "github_unavailable")


def test_repos_are_mapped_and_never_carry_the_token(env):
    client, *_ = make(env, connected_github(repos=[([REPO], True)]))
    resp = client.get("/github/repos?page=1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["has_more"] is True
    assert body["repos"] == [{"full_name": "octo/app", "private": True,
                              "description": "An app",
                              "updated_at": 1789898400.0}]
    assert TOKEN not in resp.text
    assert TOKEN not in client.get("/github").text


def test_a_revoked_token_on_repos_asks_for_a_reconnect(env):
    client, *_ = make(env, connected_github(repos=[err("bad_credentials")]))
    resp = client.get("/github/repos")
    assert (resp.status_code, resp.json()["error"]["code"]) == (409, "github_reconnect")
    assert client.get("/github").json() == {"state": "needs_reconnect", "login": "octo"}


@pytest.mark.parametrize("repo", ["../x", "a/b/c", "https://github.com/a/b"])
def test_clone_refuses_anything_but_owner_slash_name(env, repo):
    client, *_ = make(env, connected_github())
    resp = client.post("/github/clone", json={"repo": repo})
    assert (resp.status_code, resp.json()["error"]["code"]) == (422, "invalid_repo")


def test_clone_while_disconnected_is_refused(env):
    client, *_ = make(env, FakeGitHub(), connect=False)
    resp = client.post("/github/clone", json={"repo": "octo/app"})
    assert (resp.status_code, resp.json()["error"]["code"]) == (409, "github_not_connected")


def test_clone_passes_the_token_only_through_the_environment_then_starts_the_project(env):
    client, runner, app, _ = make(env, connected_github())

    def put_compose(dest):
        Path(dest).mkdir(parents=True)
        (Path(dest) / "docker-compose.yml").write_text(COMPOSE_ONE_WEB)

    runner.on_clone = put_compose
    resp = client.post("/github/clone", json={"repo": "octo/app"})
    assert resp.status_code == 202 and resp.json()["id"] == "app"
    job = finish(app, client, resp)

    assert job["state"] == "done", job
    clone = next(argv for argv in runner.calls if argv[0] == "sh")
    assert TOKEN not in " ".join(clone)
    assert "https://github.com/octo/app.git" in clone
    env_used = runner.envs[runner.calls.index(clone)]
    assert env_used == {"OMELET_GH_TOKEN": TOKEN, "GIT_TERMINAL_PROMPT": "0"}
    assert client.get("/projects/app").status_code == 200
    assert runner.argv_containing("up")


def test_a_failed_clone_leaves_no_project_and_no_token_in_its_output(env):
    client, runner, app, _ = make(env, connected_github())

    def half_clone(dest):
        Path(dest).mkdir(parents=True)
        (Path(dest) / ".git").mkdir()

    runner.on_clone = half_clone
    runner.clone = Completed(128, "", f"fatal: Authentication failed for "
                                      f"'https://x-access-token:{TOKEN}@github.com/octo/app.git/'")
    job = finish(app, client, client.post("/github/clone", json={"repo": "octo/app"}))

    assert job["state"] == "failed"
    assert TOKEN not in json.dumps(job)
    assert "Authentication failed" in job["detail"]
    assert not (env.config.projects_root / "app").exists()
    assert client.get("/projects/app").status_code == 404
    assert client.get("/github").json()["state"] == "needs_reconnect"


def test_clone_into_a_taken_name_is_refused(env):
    client, *_ = make(env, connected_github())
    (env.config.projects_root / "app").mkdir(parents=True)
    resp = client.post("/github/clone", json={"repo": "octo/app"})
    assert (resp.status_code, resp.json()["error"]["code"]) == (409, "project_exists")
```

`1789898400.0` is `2026-09-20T10:00:00Z` in epoch seconds (checked).

Append to `tests/runtime/api/test_dockerfile.py`:

```python
def test_dockerfile_installs_git_for_the_github_clone_job():
    assert "apt-get install" in _text() and " git" in _text()
```

- [ ] **Step 3: Run to verify they fail**

Run: `TMPDIR=$PWD/.tmp python3 -m pytest tests/runtime/api/test_api_github.py tests/runtime/api/test_dockerfile.py -q`
Expected: FAIL — `create_app() got an unexpected keyword argument 'github'`, plus the Dockerfile assertion.

- [ ] **Step 4: Implement**

`core/exec.py` — `LocalRunner.exec`:

```python
    def exec(self, argv: list[str], *, root: bool = False,
             env: dict | None = None) -> Completed:
        ...
        try:
            proc = subprocess.run(argv, capture_output=True, text=True,
                                  env=None if env is None else {**os.environ, **env})
```

(add `import os` at the top; keep the existing docstring and comments).

`Dockerfile` — before `WORKDIR /app`:

```dockerfile
# git for the GitHub clone job; ca-certificates so it can reach github.com.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
```

`routes/app.py`:

Imports:

```python
from datetime import datetime

from ..core.github import (GitHub, GitHubError, GitHubUnavailable, auth_failed,
                           clone_argv, redact, valid_repo)
from ..core.github_link import GitHubLink, NotConnected
```

Model next to `Handoff`:

```python
class CloneRepo(BaseModel):
    repo: str
    id: str | None = None
```

Helpers at module level:

```python
def _epoch(iso: str | None) -> float | None:
    if not iso:
        return None
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


def _github_down() -> ApiError:
    return ApiError("github_unavailable", "GitHub can't be reached. Check the "
                    "internet connection and try again.", 503)


def _reconnect() -> ApiError:
    return ApiError("github_reconnect", "GitHub stopped accepting Omelet's "
                    "access. Reconnect GitHub and try again.", 409)
```

`create_app` signature gains `github=None, github_link: GitHubLink | None = None`, and after `account = ...`:

```python
    github = github or GitHub(config.github_url, config.github_api_url)
    github_link = github_link or GitHubLink(
        state, github, client_id=config.github_client_id,
        directory=config.github_dir)
```

with `app.state.github = github_link` next to `app.state.account`.

Routes, placed after `account_sign_out`:

```python
    def require_github_token() -> str:
        try:
            return github_link.token()
        except NotConnected:
            if github_link.status()["state"] == "needs_reconnect":
                raise _reconnect() from None
            raise ApiError("github_not_connected",
                           "Connect GitHub first.", 409) from None

    @router.get("/github")
    def github_status() -> dict:
        github_link.check_token()
        return github_link.status()

    @router.post("/github/connect")
    def github_connect() -> dict:
        try:
            return github_link.connect()
        except GitHubUnavailable:
            raise _github_down() from None
        except GitHubError as e:
            raise ApiError("github_error", "GitHub would not start a sign-in: "
                           f"{e.message or e.code}", 502) from None

    @router.post("/github/disconnect")
    def github_disconnect() -> dict:
        return github_link.disconnect()

    @router.post("/github/reapply")
    def github_reapply() -> dict:
        try:
            return github_link.reapply()
        except NotConnected:
            raise ApiError("github_not_connected", "Connect GitHub first.", 409) from None
        except GitHubUnavailable:
            raise _github_down() from None

    @router.get("/github/repos")
    def github_repos(page: int = 1) -> dict:
        token = require_github_token()
        try:
            repos, more = github.repos(token, max(page, 1))
        except GitHubUnavailable:
            raise _github_down() from None
        except GitHubError as e:
            if e.code == "bad_credentials":
                github_link.mark_bad_credentials()
                raise _reconnect() from None
            raise ApiError("github_error", f"GitHub refused the repository list: "
                           f"{e.message or e.code}", 502) from None
        return {"has_more": more, "repos": [
            {"full_name": r["full_name"], "private": bool(r.get("private")),
             "description": r.get("description"),
             "updated_at": _epoch(r.get("updated_at"))} for r in repos]}

    @router.post("/github/clone", status_code=202)
    def github_clone(body: CloneRepo) -> dict:
        if not valid_repo(body.repo):
            raise ApiError("invalid_repo",
                           f"'{body.repo}' is not an owner/name repository", 422)
        project_id = _slug(body.id or body.repo.split("/")[1])
        if not project_id:
            raise ApiError("invalid_project",
                           f"'{body.repo}' has no usable project name", 422)
        directory = project_dir(project_id)
        if state.get_project(project_id) is not None or directory.exists():
            raise ApiError("project_exists",
                           f"project '{project_id}' already exists", 409)
        token = require_github_token()
        if not locks.acquire(project_id):
            raise _busy(project_id)

        def work(write):
            handed_over = False
            try:
                write.phase("cloning")
                write(f"git clone https://github.com/{body.repo}.git\n")
                result = runner.exec(clone_argv(body.repo, directory),
                                     env={"OMELET_GH_TOKEN": token,
                                          "GIT_TERMINAL_PROMPT": "0"})
                if not result.ok:
                    output = redact((result.stderr or result.stdout).strip(), token)
                    shutil.rmtree(directory, ignore_errors=True)
                    if auth_failed(output):
                        github_link.mark_bad_credentials()
                    raise JobFailed(output or "git clone failed")
                state.add_project(project_id, str(directory), config.domain)
                sync.wake()
                if not (directory / constants.COMPOSE_FILE).exists():
                    write(f"no {constants.COMPOSE_FILE} yet; left stopped\n")
                    return {"id": project_id, "status": "stopped"}
                try:
                    up = start_work(project_id, stop_first=False)
                except ApiError as e:
                    write(f"{e.message}\n")
                    return {"id": project_id, "status": "stopped"}
                # start_work's job releases the lock in its own finally.
                handed_over = True
                return {"id": project_id, **up(write)}
            finally:
                if not handed_over:
                    locks.release(project_id)

        try:
            job_id = jobs.submit(work, kind="clone", project_id=project_id)
        except BaseException:
            locks.release(project_id)
            raise
        return {"job_id": job_id, "id": project_id}
```

`start_work` is defined further down in `create_app`. Python resolves the name when the job runs, so the order does not matter. Confirm that `shutil` is already imported (it is).

- [ ] **Step 5: Run to verify they pass, then the whole API suite**

Run: `TMPDIR=$PWD/.tmp python3 -m pytest tests/runtime/api -q`
Expected: PASS (all API tests, including the existing ones against the changed `FakeRunner`).

- [ ] **Step 6: Commit**

```bash
git add runtime/omelet_api/routes/app.py runtime/omelet_api/core/exec.py runtime/omelet_api/Dockerfile tests/runtime/api/conftest.py tests/runtime/api/test_api_github.py tests/runtime/api/test_dockerfile.py
git commit -m "Add GitHub routes and the clone job"
```

---

### Task 5: `github-apply.sh` — root apply per account

**Files:**
- Create: `runtime/install/lib/github-apply.sh`, `runtime/install/systemd/omelet-github.path`, `runtime/install/systemd/omelet-github.service`
- Test: `tests/runtime/test_github_apply.py`

**Interfaces:**
- Consumes: `desired.json` / `token` written by Task 3; `runtime/install/lib/login-users.sh`.
- Produces: `applied.json` `{"generation": int, "ok": bool, "error": str|null, "name": str|null, "email": str|null, "accounts": [{"name", "ok"}]}`. Test knobs (env): `OMELET_ROOT_HOME`, `OMELET_SHELLS_FILE`, `OMELET_APPLY_PATH`.

- [ ] **Step 1: Write the failing tests**

`tests/runtime/test_github_apply.py`:

```python
import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "runtime" / "install" / "lib" / "github-apply.sh"
TOKEN = "gho_secret123"

FAKE_RUNUSER = """#!/usr/bin/env bash
# runuser -u NAME -- CMD...: log who and which HOME, then run CMD as us.
name=$2; shift 3
echo "$name $*" >> "$LOG/runuser"
exec "$@"
"""
FAKE_GH = """#!/usr/bin/env bash
if [[ -e "$HOME/gh-fails" ]]; then echo "boom $(cat "$TOKEN_FILE")" >&2; exit 1; fi
stdin=""
if [[ " $* " == *" --with-token "* ]]; then stdin=$(cat); fi
echo "$HOME gh $* stdin=$stdin" >> "$LOG/gh"
"""
FAKE_GETENT = """#!/usr/bin/env bash
echo "ada:x:1001:1001::$ADA_HOME:/bin/bash"
"""


def setup(tmp_path, desired=None, applied=None):
    bin_dir, log, gh_dir = tmp_path / "bin", tmp_path / "log", tmp_path / "github"
    for d in (bin_dir, log, gh_dir, tmp_path / "root", tmp_path / "ada"):
        d.mkdir()
    for name, text in {"runuser": FAKE_RUNUSER, "gh": FAKE_GH,
                       "getent": FAKE_GETENT}.items():
        (bin_dir / name).write_text(text)
        (bin_dir / name).chmod(0o755)
    shells = tmp_path / "shells"
    shells.write_text("/bin/bash\n")
    (gh_dir / "token").write_text(TOKEN)
    if desired is not None:
        (gh_dir / "desired.json").write_text(json.dumps(desired))
    if applied is not None:
        (gh_dir / "applied.json").write_text(json.dumps(applied))
    path = f"{bin_dir}:/usr/bin:/bin"
    env = {**os.environ, "PATH": path, "OMELET_APPLY_PATH": path,
           "OMELET_ROOT_HOME": str(tmp_path / "root"),
           "OMELET_SHELLS_FILE": str(shells), "LOG": str(log),
           "ADA_HOME": str(tmp_path / "ada"),
           "TOKEN_FILE": str(gh_dir / "token")}
    return env, gh_dir, log


def run(env, gh_dir):
    result = subprocess.run(["bash", str(SCRIPT), str(gh_dir)], env=env,
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return json.loads((gh_dir / "applied.json").read_text())


def git_get(home, key):
    return subprocess.run(["git", "config", "--global", "--get", key],
                          env={**os.environ, "HOME": str(home)},
                          capture_output=True, text=True).stdout.strip()


CONNECTED = {"generation": 4, "state": "connected", "login": "octo",
             "name": "Octo Cat", "email": "42+octo@users.noreply.github.com"}


def test_the_script_is_valid_bash():
    assert subprocess.run(["bash", "-n", str(SCRIPT)]).returncode == 0


def test_connected_signs_in_every_account_as_itself_with_the_token_on_stdin(tmp_path):
    env, gh_dir, log = setup(tmp_path, desired=CONNECTED)
    applied = run(env, gh_dir)

    assert applied["generation"] == 4 and applied["ok"] is True
    assert [a["name"] for a in applied["accounts"]] == ["root", "ada"]
    runuser = (log / "runuser").read_text()
    assert f"HOME={tmp_path / 'root'}" in runuser and "root env" in runuser
    assert f"HOME={tmp_path / 'ada'}" in runuser and "ada env" in runuser
    gh = (log / "gh").read_text()
    assert f"--with-token stdin={TOKEN}" in gh.replace("--insecure-storage ", "")
    assert TOKEN not in runuser, "the token must never be an argument"
    assert "auth setup-git --hostname github.com" in gh
    for home in (tmp_path / "root", tmp_path / "ada"):
        assert git_get(home, "user.name") == "Octo Cat"
        assert git_get(home, "user.email") == "42+octo@users.noreply.github.com"


def test_one_failing_account_does_not_stop_the_others_and_its_error_has_no_token(tmp_path):
    env, gh_dir, _ = setup(tmp_path, desired=CONNECTED)
    (tmp_path / "root" / "gh-fails").touch()
    applied = run(env, gh_dir)

    assert applied["ok"] is False
    assert applied["accounts"] == [{"name": "root", "ok": False}, {"name": "ada", "ok": True}]
    assert "root" in applied["error"] and TOKEN not in applied["error"]
    assert git_get(tmp_path / "ada", "user.name") == "Octo Cat"


def test_disconnected_logs_out_and_keeps_an_identity_the_user_set_themselves(tmp_path):
    env, gh_dir, log = setup(
        tmp_path, desired={"generation": 5, "state": "disconnected"},
        applied={"generation": 4, "ok": True, "name": "Octo Cat",
                 "email": "42+octo@users.noreply.github.com", "accounts": []})
    for home, name in ((tmp_path / "root", "Octo Cat"), (tmp_path / "ada", "Ada L")):
        subprocess.run(["git", "config", "--global", "user.name", name],
                       env={**os.environ, "HOME": str(home)}, check=True)
    applied = run(env, gh_dir)

    assert (applied["generation"], applied["ok"]) == (5, True)
    assert (log / "gh").read_text().count("auth logout --hostname github.com") == 2
    assert git_get(tmp_path / "root", "user.name") == ""
    assert git_get(tmp_path / "ada", "user.name") == "Ada L"


def test_no_desired_state_yet_touches_nothing_and_reports_generation_zero(tmp_path):
    env, gh_dir, log = setup(tmp_path)
    applied = run(env, gh_dir)

    assert (applied["generation"], applied["ok"], applied["accounts"]) == (0, True, [])
    assert not (log / "gh").exists(), "a gh the user signed into by hand stays signed in"
```

- [ ] **Step 2: Run to verify it fails**

Run: `TMPDIR=$PWD/.tmp python3 -m pytest tests/runtime/test_github_apply.py -q`
Expected: FAIL — the script does not exist.

- [ ] **Step 3: Implement `runtime/install/lib/github-apply.sh`**

```bash
#!/usr/bin/env bash
# Applies the API's GitHub desired state to every login account: gh signed in
# or out, and git's identity. Run as root by omelet-github.service and once by
# install.sh; re-running it is safe.
#   github-apply.sh [github-dir]
# Not -e: one account failing must not leave the others unapplied.
set -uo pipefail

DIR="${1:-/opt/omelet/github}"
LIB="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DESIRED="$DIR/desired.json"
APPLIED="$DIR/applied.json"
TOKEN="$DIR/token"
ROOT_HOME="${OMELET_ROOT_HOME:-/root}"
SHELLS="${OMELET_SHELLS_FILE:-/etc/shells}"
SAFE_PATH="${OMELET_APPLY_PATH:-/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin}"

field() {
  python3 -c 'import json, sys
try:
    v = json.load(open(sys.argv[1])).get(sys.argv[2])
except (OSError, ValueError):
    v = None
print("" if v is None else v)' "$1" "$2"
}

write_applied() {
  python3 - "$APPLIED.tmp" "$@" <<'PY'
import json, sys
out, gen, ok, error, name, email, *rows = sys.argv[1:]
accounts = [{"name": r.rsplit(":", 1)[0], "ok": r.rsplit(":", 1)[1] == "ok"} for r in rows]
with open(out, "w") as f:
    json.dump({"generation": int(gen), "ok": ok == "1", "error": error or None,
               "name": name or None, "email": email or None,
               "accounts": accounts}, f)
PY
  chmod 644 "$APPLIED.tmp" && mv -f "$APPLIED.tmp" "$APPLIED"
}

# Before this feature a user may have run `gh auth login` by hand; with no
# desired state yet there is nothing of ours to apply or undo.
if [[ ! -f "$DESIRED" ]]; then
  write_applied 0 1 "" "" ""
  exit 0
fi

GEN="$(field "$DESIRED" generation)"
STATE="$(field "$DESIRED" state)"
NAME="$(field "$DESIRED" name)"
EMAIL="$(field "$DESIRED" email)"
PREV_NAME="$(field "$APPLIED" name)"
PREV_EMAIL="$(field "$APPLIED" email)"
SECRET=""
[[ -r "$TOKEN" ]] && SECRET="$(cat "$TOKEN")"

accounts() {
  echo "root:0:0:$ROOT_HOME"
  getent passwd | bash "$LIB/login-users.sh" "$SHELLS"
}

as_user() {
  local name=$1 home=$2
  shift 2
  runuser -u "$name" -- env -i HOME="$home" PATH="$SAFE_PATH" \
    GH_PROMPT_DISABLED=1 GH_NO_UPDATE_NOTIFIER=1 "$@"
}

connect() {
  local name=$1 home=$2
  [[ -n "$SECRET" ]] || { echo "the token file is missing"; return 1; }
  as_user "$name" "$home" gh auth login --hostname github.com \
      --git-protocol https --insecure-storage --with-token < "$TOKEN" &&
    as_user "$name" "$home" gh auth setup-git --hostname github.com &&
    as_user "$name" "$home" git config --global user.name "$NAME" &&
    as_user "$name" "$home" git config --global user.email "$EMAIL"
}

unset_if_ours() {
  local name=$1 home=$2 key=$3 ours=$4
  [[ -n "$ours" ]] || return 0
  if [[ "$(as_user "$name" "$home" git config --global --get "$key")" == "$ours" ]]; then
    as_user "$name" "$home" git config --global --unset "$key"
  fi
}

disconnect() {
  local name=$1 home=$2
  as_user "$name" "$home" gh auth logout --hostname github.com || true
  unset_if_ours "$name" "$home" user.name "$PREV_NAME" &&
    unset_if_ours "$name" "$home" user.email "$PREV_EMAIL"
}

OK=1
ERROR=""
ROWS=()
while IFS=: read -r name _uid _gid home; do
  if [[ "$STATE" == connected ]]; then
    out="$(connect "$name" "$home" 2>&1)"
  else
    out="$(disconnect "$name" "$home" 2>&1)"
  fi
  if (( $? == 0 )); then
    ROWS+=("$name:ok")
  else
    OK=0
    ROWS+=("$name:failed")
    if [[ -z "$ERROR" ]]; then
      ERROR="$name: $(printf '%s' "$out" | tail -n 3)"
      [[ -n "$SECRET" ]] && ERROR="${ERROR//"$SECRET"/[token]}"
    fi
  fi
done < <(accounts)

if [[ "$STATE" == connected ]]; then
  write_applied "$GEN" "$OK" "$ERROR" "$NAME" "$EMAIL" "${ROWS[@]}"
else
  write_applied "$GEN" "$OK" "$ERROR" "" "" "${ROWS[@]}"
fi
```

Note: `out="$(...)"` followed by `$?` reads the command substitution's status. Keep the assignment on its own line exactly as written; a `local out=...` would swallow the status.

`runtime/install/systemd/omelet-github.path`:

```ini
[Unit]
Description=Apply Omelet's GitHub connection when it changes

[Path]
PathChanged=/opt/omelet/github/desired.json

[Install]
WantedBy=multi-user.target
```

`runtime/install/systemd/omelet-github.service`:

```ini
[Unit]
Description=Apply Omelet's GitHub connection to this machine's accounts

[Service]
Type=oneshot
ExecStart=/bin/bash /opt/omelet/runtime/install/lib/github-apply.sh
```

- [ ] **Step 4: Run to verify it passes**

Run: `TMPDIR=$PWD/.tmp python3 -m pytest tests/runtime/test_github_apply.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add runtime/install/lib/github-apply.sh runtime/install/systemd tests/runtime/test_github_apply.py
git commit -m "Add root apply of the GitHub desired state per account"
```

---

### Task 6: `install.sh` wiring and the agent rule

**Files:**
- Modify: `runtime/install/install.sh`, `runtime/instructions/omelet.md`
- Test: `tests/runtime/test_install_shell.py`

**Interfaces:**
- Consumes: Task 5's script and units.

- [ ] **Step 1: Write the failing tests**

Append to `tests/runtime/test_install_shell.py`:

```python
def test_install_reasserts_the_github_file_modes_after_the_permission_sweep():
    text = INSTALL.read_text()
    sweep = text.index("chmod -R g+rwX /opt/omelet")
    assert text.index("chmod 600 /opt/omelet/github/token") > sweep
    assert "install -d -m 2770 -o root -g docker /opt/omelet/github" in text


def test_install_enables_the_github_path_unit_and_applies_before_the_marker():
    text = INSTALL.read_text()
    assert "systemctl enable --now omelet-github.path" in text
    apply = text.index('bash "$INSTALL_DIR/lib/github-apply.sh"')
    assert apply < text.index(f"> {constants.RUNTIME_MARKER}")


def test_install_trusts_repositories_owned_by_the_api():
    assert "safe.directory '*'" in INSTALL.read_text()


def test_the_agent_instructions_never_ask_for_a_github_login():
    text = (ROOT / "runtime" / "instructions" / "omelet.md").read_text()
    assert "Connect GitHub" in text
    assert "run\n  `gh auth login`" not in text and "run `gh auth login`" not in text
```

- [ ] **Step 2: Run to verify they fail**

Run: `TMPDIR=$PWD/.tmp python3 -m pytest tests/runtime/test_install_shell.py -q`
Expected: the four new tests FAIL.

- [ ] **Step 3: Implement**

In `install.sh`, directly after step 4's `find /opt/omelet -type d -exec chmod g+s {} +`:

```bash
# The GitHub token is the API's alone: the sweep above just widened it, so
# its modes are put back every run.
install -d -m 2770 -o root -g docker /opt/omelet/github
chmod 2770 /opt/omelet/github
[[ -e /opt/omelet/github/token ]] && chmod 600 /opt/omelet/github/token
[[ -e /opt/omelet/github/desired.json ]] && chmod 640 /opt/omelet/github/desired.json
```

In step 8, after the `gh` install block:

```bash
# The GitHub clone runs as the API's uid; the agents' accounts differ from it
# and are all docker-group, root-equivalent here, so the ownership check
# guards nothing. git 2.43 has no prefix form of this setting.
if ! git config --system --get-all safe.directory 2>/dev/null | grep -qxF '*'; then
  git config --system --add safe.directory '*'
fi
```

Between step 11's loop and the marker, renumbering the marker step to 13:

```bash
# 12. GitHub: apply on every change of the API's desired state, and once now
# so a repair or a newly added account catches up.
install -m 644 "$INSTALL_DIR/systemd/omelet-github.path" \
  "$INSTALL_DIR/systemd/omelet-github.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now omelet-github.path
if ! bash "$INSTALL_DIR/lib/github-apply.sh"; then
  echo "could not apply the GitHub connection to this machine's accounts" >&2
  exit 1
fi
```

`runtime/instructions/omelet.md` — replace the last bullet (three lines starting `- GitHub —`) with:

```markdown
- GitHub — repositories, pull requests, issues: use `gh` and plain `git` over https.
  If `gh auth status` fails or GitHub says unauthorized, do not run `gh auth login`
  and do not ask for a token: tell the user to click **Connect GitHub** in Omelet,
  then try again.
```

- [ ] **Step 4: Run to verify they pass, plus the other runtime script tests**

Run: `TMPDIR=$PWD/.tmp python3 -m pytest tests/runtime -q`
Expected: PASS (`test_install_agents.py` re-reads `omelet.md` and must still pass).

- [ ] **Step 5: Commit**

```bash
git add runtime/install/install.sh runtime/instructions/omelet.md tests/runtime/test_install_shell.py
git commit -m "Wire GitHub apply into install and tell agents never to log in"
```

---

### Task 7: Console — GitHub types, queries and view mapping

**Files:**
- Create: `runtime/web/apps/console/src/github/github.ts`, `runtime/web/apps/console/src/github/view.ts`
- Modify: `runtime/web/apps/console/src/projects/types.ts`, `runtime/web/apps/console/src/projects/copy.ts`
- Test: `runtime/web/apps/console/src/github/view.test.ts`

**Interfaces:**
- Produces:
  - `type GitHubStatus` (spec §5.4 shapes)
  - `type Repo = { full_name: string; private: boolean; description: string | null; updated_at: number | null }`
  - `type RepoPage = { repos: Repo[]; has_more: boolean }`
  - Hooks: `useGitHub()`, `useConnectGitHub()`, `useDisconnectGitHub()`, `useReapplyGitHub()`, `useRepos(enabled: boolean)` (infinite), `useCloneRepo()` → `{ job_id: string; id: string }`
  - `polling(status?: GitHubStatus): boolean`
  - `DEVICE_URL`, `REVOKE_URL`
  - `type GitHubView`, `githubView(status: GitHubStatus): GitHubView`
  - `JobKind` gains `"clone"`; `phaseCaption("cloning")` = `"Downloading from GitHub"`.

- [ ] **Step 1: Write the failing test**

`runtime/web/apps/console/src/github/view.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { polling } from "./github";
import { githubView } from "./view";

const connected = (setup: string, setup_error: string | null = null) =>
  ({ state: "connected", login: "octo", name: "Octo", email: "e", setup, setup_error }) as const;

describe("githubView", () => {
  it("offers a plain connect when nothing has happened", () => {
    expect(githubView({ state: "disconnected", error: null })).toEqual({
      kind: "connect", problem: null, action: "Connect GitHub",
    });
  });

  it.each([
    ["access_denied", "Try again"],
    ["expired_token", "Get a new code"],
    ["github_error", "Try again"],
    ["something_new", "Try again"],
  ])("explains %s and offers a way back", (error, action) => {
    const view = githubView({ state: "disconnected", error });
    expect(view).toMatchObject({ kind: "connect", action });
    expect(view.kind === "connect" && view.problem).toBeTruthy();
  });

  it("shows the code while pending", () => {
    expect(githubView({ state: "pending", user_code: "AB-CD", url: "u", expires_at: 5 })).toEqual({
      kind: "code", code: "AB-CD", expiresAt: 5,
    });
  });

  it("is only ready once the machine has applied it", () => {
    expect(githubView(connected("applying")).kind).toBe("applying");
    expect(githubView(connected("ready"))).toEqual({ kind: "ready", login: "octo" });
  });

  it("says a runtime update is needed without offering a pointless retry", () => {
    const view = githubView(connected("runtime_outdated"));
    expect(view).toMatchObject({ kind: "setupFailed", canRetry: false });
    expect(view.kind === "setupFailed" && view.message).toMatch(/update/);
  });

  it("names a timeout and a script failure differently, both retryable", () => {
    const timeout = githubView(connected("failed", "setup_timeout"));
    const failed = githubView(connected("failed", "root: gh broke"));
    expect(timeout).toMatchObject({ kind: "setupFailed", canRetry: true });
    expect(failed).toMatchObject({ kind: "setupFailed", canRetry: true });
    expect(failed.kind === "setupFailed" && failed.message).toContain("root: gh broke");
    expect(timeout.kind === "setupFailed" && timeout.message).not.toContain("setup_timeout");
  });

  it("asks for a reconnect when the token stopped working", () => {
    expect(githubView({ state: "needs_reconnect", login: "octo" })).toEqual({ kind: "reconnect", login: "octo" });
  });
});

describe("polling", () => {
  it("polls only while something is about to change", () => {
    expect(polling({ state: "pending", user_code: "x", url: "u", expires_at: 1 })).toBe(true);
    expect(polling(connected("applying"))).toBe(true);
    expect(polling(connected("ready"))).toBe(false);
    expect(polling({ state: "disconnected", error: null })).toBe(false);
    expect(polling(undefined)).toBe(false);
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd runtime/web && npx vitest run apps/console/src/github/view.test.ts`
Expected: FAIL — cannot resolve `./github`.

- [ ] **Step 3: Implement**

`src/github/github.ts`:

```ts
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";

export const DEVICE_URL = "https://github.com/login/device";
export const REVOKE_URL = "https://github.com/settings/applications";
const POLL_MS = 2000;
const GITHUB = ["github"] as const;

export type SetupState = "applying" | "ready" | "failed" | "runtime_outdated";

export type GitHubStatus =
  | { state: "disconnected"; error: string | null }
  | { state: "pending"; user_code: string; url: string; expires_at: number }
  | { state: "connected"; login: string; name: string; email: string; setup: SetupState; setup_error: string | null }
  | { state: "needs_reconnect"; login: string };

export type Repo = { full_name: string; private: boolean; description: string | null; updated_at: number | null };
export type RepoPage = { repos: Repo[]; has_more: boolean };

export function polling(status?: GitHubStatus): boolean {
  return status?.state === "pending" || (status?.state === "connected" && status.setup === "applying");
}

export function useGitHub() {
  return useQuery({
    queryKey: GITHUB,
    queryFn: () => api.get<GitHubStatus>("/api/github"),
    refetchInterval: (query) => (polling(query.state.data) ? POLL_MS : false),
  });
}

function useStatusMutation(path: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<GitHubStatus>(path),
    onSuccess: (status) => client.setQueryData(GITHUB, status),
  });
}

export const useConnectGitHub = () => useStatusMutation("/api/github/connect");
export const useDisconnectGitHub = () => useStatusMutation("/api/github/disconnect");
export const useReapplyGitHub = () => useStatusMutation("/api/github/reapply");

export function useRepos(enabled: boolean) {
  return useInfiniteQuery({
    queryKey: [...GITHUB, "repos"],
    enabled,
    initialPageParam: 1,
    queryFn: ({ pageParam }) => api.get<RepoPage>(`/api/github/repos?page=${pageParam}`),
    getNextPageParam: (last, all) => (last.has_more ? all.length + 1 : undefined),
  });
}

export function useCloneRepo() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (repo: string) => api.post<{ job_id: string; id: string }>("/api/github/clone", { repo }),
    // A revoked token turns the status to needs_reconnect server-side.
    onError: () => void client.invalidateQueries({ queryKey: GITHUB }),
  });
}
```

`src/github/view.ts`:

```ts
import type { GitHubStatus } from "./github";

export type GitHubView =
  | { kind: "connect"; problem: string | null; action: string }
  | { kind: "code"; code: string; expiresAt: number }
  | { kind: "applying"; login: string }
  | { kind: "ready"; login: string }
  | { kind: "setupFailed"; login: string; message: string; canRetry: boolean }
  | { kind: "reconnect"; login: string };

const PROBLEMS: Record<string, { problem: string; action: string }> = {
  access_denied: { problem: "You said no on GitHub, so nothing was connected.", action: "Try again" },
  expired_token: { problem: "That code ran out before it was entered on GitHub.", action: "Get a new code" },
};
const UNEXPECTED = { problem: "GitHub answered with something unexpected.", action: "Try again" };

export function githubView(status: GitHubStatus): GitHubView {
  switch (status.state) {
    case "disconnected":
      if (!status.error) return { kind: "connect", problem: null, action: "Connect GitHub" };
      return { kind: "connect", ...(PROBLEMS[status.error] ?? UNEXPECTED) };
    case "pending":
      return { kind: "code", code: status.user_code, expiresAt: status.expires_at };
    case "needs_reconnect":
      return { kind: "reconnect", login: status.login };
    case "connected":
      if (status.setup === "ready") return { kind: "ready", login: status.login };
      if (status.setup === "applying") return { kind: "applying", login: status.login };
      if (status.setup === "runtime_outdated") {
        return {
          kind: "setupFailed", login: status.login, canRetry: false,
          message: "This machine's Omelet runtime needs an update before GitHub can be set up. " +
            "Open the desktop app and choose Repair.",
        };
      }
      return {
        kind: "setupFailed", login: status.login, canRetry: true,
        message: status.setup_error === "setup_timeout"
          ? "GitHub setup inside Omelet didn't finish."
          : `GitHub setup inside Omelet failed: ${status.setup_error ?? "no reason given"}`,
      };
  }
}
```

`projects/types.ts`: `export type JobKind = "up" | "restart" | "down" | "clone";`

`projects/copy.ts`, in `phaseCaption` before `default`:

```ts
    case "cloning":
      return "Downloading from GitHub";
```

- [ ] **Step 4: Run to verify it passes, then typecheck**

Run: `cd runtime/web && npx vitest run apps/console/src/github && npm run typecheck`
Expected: PASS; typecheck clean (if an exhaustive `switch` over `JobKind` elsewhere now errors, add the `"clone"` case with the same handling as `"up"`).

- [ ] **Step 5: Commit**

```bash
git add runtime/web/apps/console/src/github runtime/web/apps/console/src/projects/types.ts runtime/web/apps/console/src/projects/copy.ts
git commit -m "Add console GitHub queries and status mapping"
```

---

### Task 8: Console — modal, repo picker, entry points, mocks

**Files:**
- Create: `runtime/web/apps/console/src/screens/github/GitHubModal.tsx`, `RepoPicker.tsx`, `GitHubModal.module.css`
- Modify: `screens/list/EmptyCounter.tsx`, `screens/list/ProjectList.tsx`, `shell/AccountMenu.tsx`, `mocks/handlers.ts`

**Interfaces:**
- Consumes: Task 7; `openExternal` (`desktop/desktop.ts`); `useJob` (`projects/queries.ts`); `relativeTime` (`projects/format.ts`); `Modal`, `Button`, `Notice` from `@omelet/ui`.
- Produces: `<GitHubModal open onClose />`.

No component tests (spec §9): every decision lives in `view.ts`. Verification is typecheck plus clicking through the mock scenarios.

- [ ] **Step 1: `GitHubModal.tsx`**

```tsx
import { useState } from "react";
import { Button, Modal } from "@omelet/ui";
import { openExternal } from "../../desktop/desktop";
import { DEVICE_URL, useConnectGitHub, useGitHub, useReapplyGitHub } from "../../github/github";
import { githubView } from "../../github/view";
import { useNow } from "../../projects/useNow";
import { RepoPicker } from "./RepoPicker";
import s from "./GitHubModal.module.css";

export function GitHubModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const status = useGitHub();
  const connect = useConnectGitHub();
  const reapply = useReapplyGitHub();

  // Opened inside the click so no popup blocker trips; the URL never varies.
  const start = () => {
    openExternal(DEVICE_URL);
    connect.mutate();
  };

  let body;
  if (!status.data) {
    body = <p className={s.muted}>{status.isError ? status.error.message : "Checking GitHub…"}</p>;
  } else {
    const view = githubView(status.data);
    switch (view.kind) {
      case "connect":
        body = (
          <>
            {view.problem && <p className={s.error}>{view.problem}</p>}
            <p>Connect once and your coding agent can pull and push your GitHub repositories.</p>
            {connect.error && <p className={s.error}>{connect.error.message}</p>}
            <Button variant="primary" onClick={start} disabled={connect.isPending}>{view.action}</Button>
          </>
        );
        break;
      case "code":
        body = <Code code={view.code} expiresAt={view.expiresAt} />;
        break;
      case "applying":
        body = <p>Connected as @{view.login}. Setting up GitHub inside Omelet…</p>;
        break;
      case "ready":
        body = <RepoPicker login={view.login} onDone={onClose} />;
        break;
      case "setupFailed":
        body = (
          <>
            <p className={s.error}>{view.message}</p>
            {view.canRetry && (
              <Button onClick={() => reapply.mutate()} disabled={reapply.isPending}>Try again</Button>
            )}
          </>
        );
        break;
      case "reconnect":
        body = (
          <>
            <p>GitHub stopped accepting Omelet's access for @{view.login}.</p>
            <Button variant="primary" onClick={start} disabled={connect.isPending}>Reconnect GitHub</Button>
          </>
        );
        break;
    }
  }

  return (
    <Modal open={open} onClose={onClose} title="GitHub">
      <div className={s.body}>{body}</div>
    </Modal>
  );
}

function Code({ code, expiresAt }: { code: string; expiresAt: number }) {
  const now = useNow(1000);
  const [copied, setCopied] = useState(false);
  const left = Math.max(0, Math.round(expiresAt - now / 1000));
  return (
    <>
      <p>On the GitHub page that just opened, enter this code and click Authorize:</p>
      <p className={s.code}>{code}</p>
      <div className={s.actions}>
        <Button
          variant="primary"
          onClick={() => void navigator.clipboard.writeText(code).then(() => setCopied(true))}
        >
          {copied ? "Copied" : "Copy code"}
        </Button>
        <Button variant="quiet" onClick={() => openExternal(DEVICE_URL)}>Open GitHub again</Button>
      </div>
      <p className={s.muted}>
        {left > 0 ? `This code works for ${Math.ceil(left / 60)} more minute${left > 60 ? "s" : ""}.` : "This code has run out."}
      </p>
    </>
  );
}
```

- [ ] **Step 2: `RepoPicker.tsx`**

```tsx
import { useEffect, useState } from "react";
import { useNavigate } from "react-router";
import { Button } from "@omelet/ui";
import { useCloneRepo, useRepos } from "../../github/github";
import { relativeTime } from "../../projects/format";
import { useJob } from "../../projects/queries";
import s from "./GitHubModal.module.css";

export function RepoPicker({ login, onDone }: { login: string; onDone: () => void }) {
  const repos = useRepos(true);
  const clone = useCloneRepo();
  const [started, setStarted] = useState<{ job_id: string; id: string } | null>(null);

  if (started) return <Cloning jobId={started.job_id} id={started.id} onDone={onDone} />;

  const all = repos.data?.pages.flatMap((page) => page.repos) ?? [];
  return (
    <>
      <p>Connected as @{login}. Pick a repository to make it a project:</p>
      {repos.isError && <p className={s.error}>{repos.error.message}</p>}
      {clone.error && <p className={s.error}>{clone.error.message}</p>}
      <ul className={s.repos}>
        {all.map((repo) => (
          <li key={repo.full_name}>
            <button
              className={s.repo}
              disabled={clone.isPending}
              onClick={() => clone.mutate(repo.full_name, { onSuccess: setStarted })}
            >
              <span className={s.repoName}>{repo.full_name}</span>
              {repo.private && <span className={s.badge}>Private</span>}
              {repo.updated_at !== null && (
                <span className={s.muted}>updated {relativeTime(repo.updated_at, Date.now())}</span>
              )}
            </button>
          </li>
        ))}
      </ul>
      {repos.isLoading && <p className={s.muted}>Fetching your repositories…</p>}
      {repos.hasNextPage && (
        <Button variant="quiet" onClick={() => void repos.fetchNextPage()} disabled={repos.isFetchingNextPage}>
          Load more
        </Button>
      )}
    </>
  );
}

// The project exists only once the clone lands, so the page opens after
// the "cloning" phase, not on submit.
function Cloning({ jobId, id, onDone }: { jobId: string; id: string; onDone: () => void }) {
  const job = useJob(jobId).data;
  const navigate = useNavigate();
  const cloned = job !== undefined && job.state !== "failed" && (job.phase !== "cloning" || job.state === "done");
  useEffect(() => {
    if (cloned) {
      onDone();
      navigate(`/p/${encodeURIComponent(id)}`);
    }
  }, [cloned, id, navigate, onDone]);
  if (job?.state === "failed") return <p className={s.error}>Couldn't download it: {job.detail}</p>;
  return <p>Downloading {id} from GitHub…</p>;
}
```

(Check that `relativeTime`'s wording works after "updated " — it takes seconds and ms per `projects/format.ts:44`.)

- [ ] **Step 3: `GitHubModal.module.css`**

```css
.body { display: grid; gap: 12px; }
.muted { color: var(--ink-3); }
.error { color: var(--danger); }
.code { font-family: var(--font-mono); font-size: 28px; letter-spacing: 0.12em; text-align: center; }
.actions { display: flex; gap: 8px; flex-wrap: wrap; }
.repos { list-style: none; margin: 0; padding: 0; display: grid; gap: 4px; max-height: 50vh; overflow-y: auto; }
.repo { width: 100%; display: flex; gap: 8px; align-items: baseline; flex-wrap: wrap; text-align: left;
  padding: 8px 10px; border: 1px solid var(--line); border-radius: 8px; background: none; cursor: pointer; font: inherit; }
.repo:disabled { cursor: progress; opacity: 0.6; }
.repoName { font-weight: 600; overflow-wrap: anywhere; }
.badge { font-size: 12px; padding: 0 6px; border-radius: 999px; border: 1px solid var(--line); }
```

Before committing, replace each `var(--…)` with the token names `packages/ui`'s stylesheet actually defines (grep `--` in `runtime/web/packages/ui/src`). Do not invent tokens.

- [ ] **Step 4: Entry points**

`EmptyCounter.tsx`: add prop `onGitHub: () => void`; the GitHub card becomes

```tsx
          <h2 className={s.cardTitle}>From GitHub</h2>
          <p className={s.cardBody}>Pull in a repo you already have. Connect GitHub once and pick it.</p>
          <Button onClick={onGitHub}>Pick a repo</Button>
```

`ProjectList.tsx`: `const [github, setGitHub] = useState(false);`, render `<GitHubModal open={github} onClose={() => setGitHub(false)} />` next to `modal`, pass `onGitHub={() => setGitHub(true)}` to `EmptyCounter`, and add `<Button onClick={() => setGitHub(true)}>From GitHub</Button>` before the "New project" button in the header.

`AccountMenu.tsx`: add a `GitHubLine` component rendered before the email:

```tsx
function GitHubLine() {
  const status = useGitHub().data;
  const disconnect = useDisconnectGitHub();
  const [open, setOpen] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [left, setLeft] = useState(false);
  if (!status) return null;
  const view = githubView(status);
  const modal = <GitHubModal open={open} onClose={() => setOpen(false)} />;
  if (left && view.kind === "connect") {
    return (
      <span>
        GitHub disconnected.{" "}
        <a href={REVOKE_URL} onClick={(e) => { e.preventDefault(); openExternal(REVOKE_URL); }}>
          Remove Omelet's access on GitHub
        </a>{" "}
        to revoke it fully.
      </span>
    );
  }
  if (view.kind === "ready" || view.kind === "applying" || view.kind === "setupFailed") {
    return confirming ? (
      <>
        <span>Disconnect @{view.login}?</span>
        <Button variant="quiet" onClick={() => disconnect.mutate(undefined, { onSuccess: () => { setLeft(true); setConfirming(false); } })}>
          Disconnect
        </Button>
        <Button variant="quiet" onClick={() => setConfirming(false)}>Keep</Button>
      </>
    ) : (
      <>
        <span>GitHub: @{view.login}</span>
        <Button variant="quiet" onClick={() => setConfirming(true)}>Disconnect GitHub</Button>
      </>
    );
  }
  return (
    <>
      <Button variant="quiet" onClick={() => setOpen(true)}>
        {view.kind === "reconnect" ? "Reconnect GitHub" : "Connect GitHub"}
      </Button>
      {modal}
    </>
  );
}
```

Imports: `useGitHub`, `useDisconnectGitHub`, `REVOKE_URL` from `../github/github`; `githubView` from `../github/view`; `openExternal` from `../desktop/desktop`; `GitHubModal` from `../screens/github/GitHubModal`. Any non-link confirmation uses the inline two-step above, never `window.confirm`.

- [ ] **Step 5: Mock scenarios**

In `mocks/handlers.ts` add to `SCENARIOS`: `"github-pending", "github-denied", "github-expired", "github-applying", "github-outdated", "github-reconnect"`. Inside `handlersFor`, next to the account state:

```ts
  const GH_CODE = "C0DE-F00D";
  const ghConnected = (setup: string, setup_error: string | null = null) =>
    ({ state: "connected", login: "ada", name: "Ada", email: "1+ada@users.noreply.github.com", setup, setup_error });
  const ghPending = () => ({ state: "pending", user_code: GH_CODE, url: "https://github.com/login/device", expires_at: nowSec() + 900 });
  let github: Record<string, unknown> =
    scenario === "github-pending" ? ghPending()
    : scenario === "github-applying" ? ghConnected("applying")
    : scenario === "github-outdated" ? ghConnected("runtime_outdated")
    : scenario === "github-reconnect" ? { state: "needs_reconnect", login: "ada" }
    : { state: "disconnected", error: null };
  let ghSettleAt = 0;
```

and handlers:

```ts
    http.get("/api/github", () => {
      if ((github.state === "pending" || github.setup === "applying") && ghSettleAt === 0) ghSettleAt = Date.now() + 5000;
      if (ghSettleAt && Date.now() >= ghSettleAt) {
        ghSettleAt = 0;
        github =
          github.state === "connected" ? ghConnected("ready")
          : scenario === "github-denied" ? { state: "disconnected", error: "access_denied" }
          : scenario === "github-expired" ? { state: "disconnected", error: "expired_token" }
          : ghConnected("applying");
      }
      return HttpResponse.json(github);
    }),
    http.post("/api/github/connect", () => {
      github = ghPending();
      return HttpResponse.json(github);
    }),
    http.post("/api/github/disconnect", () => {
      github = { state: "disconnected", error: null };
      return HttpResponse.json(github);
    }),
    http.post("/api/github/reapply", () => {
      github = ghConnected("applying");
      return HttpResponse.json(github);
    }),
    http.get("/api/github/repos", ({ request }) => {
      const page = Number(new URL(request.url).searchParams.get("page") ?? "1");
      const names = page === 1 ? ["ada/recipe-site", "ada/garden-log", "kitchen-co/menu"] : ["ada/old-notes"];
      return HttpResponse.json({
        has_more: page === 1,
        repos: names.map((full_name, i) => ({ full_name, private: i % 2 === 0, description: null, updated_at: nowSec() - (i + page) * 86400 })),
      });
    }),
    http.post("/api/github/clone", async ({ request }) => {
      const { repo } = (await request.json()) as { repo: string };
      const id = slugify(repo.split("/")[1]);
      // Create the project and start a job exactly the way this file's
      // POST /api/projects + up handlers do, with kind "clone" and
      // "cloning" as its first phase.
      ...
    }),
```

For the clone handler body: read the existing `POST /api/projects` and `startJob` code in this file, reuse them rather than duplicating, and extend `startJob`'s `steps` so `kind === "clone"` gives `["cloning", "starting", "checking"]`. Return `{ job_id, id }`. For the connect-and-clone mocks to work, `JobKind` from Task 7 must include `"clone"`.

- [ ] **Step 6: Verify**

Run: `cd runtime/web && npm run typecheck && npm test && npm run build && npm run check-offline`
Expected: all PASS.

Then `npm run dev` and, in the browser, walk through `?scenario=` `ok`, `github-pending`, `github-denied`, `github-expired`, `github-applying`, `github-outdated` and `github-reconnect`:
- `ok` → From GitHub → Connect → code → ready → pick → project page.
- `github-pending` → ready.
- `github-denied` → "You said no" + Try again.
- `github-expired` → Get a new code.
- `github-applying` → ready.
- `github-outdated` → the update message, no retry.
- `github-reconnect` → the account menu shows Reconnect GitHub.

- [ ] **Step 7: Commit**

```bash
git add runtime/web/apps/console/src
git commit -m "Add Connect GitHub modal, repo picker and entry points"
```

---

### Task 9: Docs, full suite, PR

**Files:**
- Modify: `CLAUDE.md`, `docs/superpowers/specs/2026-09-24-connect-github-design.md` (only if the implementation drifted)

- [ ] **Step 1: `CLAUDE.md`**

Add a layer bullet after the `core/cloud.py / account.py / sync.py` bullet:

```markdown
- `runtime/omelet_api/core/github.py` / `github_link.py` — Connect GitHub. The API runs GitHub's
  Device Flow (`OMELET_GITHUB_CLIENT_ID`, no secret) and keeps the token in
  `/opt/omelet/github/token` (0600). It never reaches a user home itself: it writes a token-free
  `desired.json` with a rising `generation`, `omelet-github.path` runs
  `runtime/install/lib/github-apply.sh` as root, and that echoes the generation into
  `applied.json`. The UI says "ready" only when the two match; `applied.json` missing after 30 s
  means the runtime predates the feature. `POST /github/clone` passes the token to git only
  through the child's environment.
```

Add under "Things that will bite you":

```markdown
- `install.sh` step 4 runs `chmod -R g+rwX /opt/omelet` on every install, which widens
  `/opt/omelet/github/token`. The modes are reasserted right after the sweep; keep that order.
- Login accounts are never the API's uid 1000: WSL2 has only root, and Lima's user carries the
  macOS uid. The API writes into projects through the docker group, so anything it creates there
  needs `umask 002` (see `clone_argv`), and `/etc/gitconfig` trusts `safe.directory '*'`.
```

- [ ] **Step 2: Full verification**

Run: `TMPDIR=$PWD/.tmp python3 -m pytest -q && (cd runtime/web && npm test && npm run typecheck)`
Expected: all PASS. Report any failure verbatim; do not continue to the PR with red tests.

- [ ] **Step 3: Version**

Do not bump. `__version__` is `0.2.0` and the highest tag is `runtime-v0.0.5`, so 0.2.0 is unreleased and this ships in it. If the 0.2.0 images turn out to be already pushed to ghcr, bump `__init__.py`, the Dockerfile `SERVICE_VERSION` and both `stack.yml` tags to `0.3.0` together (`tests/test_constants_agree.py` holds them equal), and say so in the PR.

- [ ] **Step 4: Commit, push, PR**

```bash
git add CLAUDE.md docs/superpowers
git commit -m "Document Connect GitHub"
git push -u origin feature/12-connect-github
gh pr create --base main --title "Connect GitHub" --body-file <body>
```

The PR body must:
- link `https://github.com/omelet-app/omelet-resources/issues/12`;
- explain where each piece lives and why (spec §2), and that no host release is needed;
- list what was left untested: the systemd units and modal components;
- include the live acceptance checklist from spec §10;
- end with the Claude Code attribution line.

- [ ] **Step 5: Code review by a separate agent** (required by `~/.claude/CLAUDE.md`)

Dispatch a reviewer agent on the PR diff. Fix confirmed findings in follow-up commits.
