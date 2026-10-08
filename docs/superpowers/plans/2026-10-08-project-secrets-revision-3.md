# Project secrets — Revision 3 rework Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cut the secrets feature on `feature/13-project-secrets` down to Revision 3: the project
keeps its own `.env`, Eggie stores only credentials, plus `eggie secret request`.

**Architecture:** Remove every `.env`/`.env.example` reader, defaults, missing detection and the
import route. Keep storage, validation, overlay names, env on every compose call, the write-only API,
the restart notice and the CLI. Add a `secret_requests` table, a `PUT /projects/{id}/secret-requests/{name}`
route, `eggie secret request`, and a Requested section on the Secrets page.

**Tech Stack:** Python 3.12 / FastAPI / sqlite (`runtime/eggie_api`), stdlib CLI (`runtime/cli/eggie.py`),
React + TanStack Query + MSW (`runtime/web/apps/console`).

**Spec:** `docs/superpowers/specs/2026-10-08-project-secrets-design.md` (Revision 3).

## Global Constraints

- Worktree `/home/ihor/projects/local-environment-for-non-tech/poc-13-project-secrets`, branch
  `feature/13-project-secrets`. Run `git branch --show-current` before every commit.
- Python tests: `TMPDIR=/tmp/claude-1000 .venv/bin/python -m pytest -q` from the worktree root.
- Web: in `runtime/web`, `npm test`, `npm run typecheck`, `npm run build`, `npm run check-offline`.
- `.env` belongs to the project: no code reads, parses, imports or writes `.env` or `.env.example`.
- No response, log, argv, overlay or job output ever contains a secret value.
- Reserved names (case-insensitive): prefixes `COMPOSE_`, `DOCKER_`, `LD_`; names `PATH`, `HOME`.
- Value: not empty, no NUL, no lone surrogate, ≤ 64 KiB; project total ≤ 512 KiB.
- Hint: 1–500 characters, no NUL, no lone surrogate; plain text.
- Error codes: `secret_name_invalid`, `secret_name_reserved`, `secret_invalid_value`,
  `secret_too_large`, `secrets_too_large`, `secret_hint_invalid`, `secret_not_found`.
- No backward compatibility: migration v6 is unreleased and is edited in place.
- Comments only for non-obvious things; no ticket references. Tests only where a wrong result is
  plausible; no component tests.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. A request made for a name that already has a value must not show under Requested (Task 1 test).
2. A hint with a NUL or lone surrogate must be a 400 `secret_hint_invalid`, not a 500 (Task 1 + Task 2 tests).
3. DELETE of a name that is only requested removes the request, returns 204 and does not make the
   project need a restart (Task 2 test).
4. A project with a `.env` and `.env.example` in its folder: a start passes exactly the stored values
   to compose, nothing from those files, and neither file is touched (Task 2 test).
5. Purge removes requests as well as values; a plain delete keeps both (Task 2 test).

---

### Task 1: Core — validation, storage, requests

**Files:**
- Modify: `runtime/eggie_api/core/secrets.py`
- Modify: `runtime/eggie_api/core/migrate.py` (`_v6_secrets`)
- Modify: `runtime/eggie_api/core/state.py`
- Modify: `runtime/eggie_api/core/lifecycle.py` (drop `empty_file_as_root`, restore the single
  `remove_tree_as_root` as on `main`)
- Test: `tests/runtime/api/test_secrets.py`, `tests/runtime/api/test_state.py`, `tests/runtime/api/test_migrate.py`, `tests/runtime/api/test_lifecycle.py`

**Interfaces:**
- Produces: `secrets.check_hint(hint: str) -> None`; `secrets.declared(compose) -> tuple[list[str], dict[str, set[str]]]` (unchanged);
  `State.secret_requests(pid) -> list[dict]` (`{name, hint}`, sorted by name, only names with no stored value);
  `State.request_secret(pid, name, hint) -> None`; `State.delete_request(pid, name) -> bool`;
  `State.set_secrets` also deletes requests of the names it sets; `State.drop_secrets` clears both tables.
- Removed (later tasks must not use): `parse_dotenv`, `parse_example`, `defaults`, `missing`,
  `importable`, `compose_defaulted`, `_compose_refs`, `_scan_refs`, `_entries`, `_closing`,
  `_unescape`, `_DEFAULTED`, `_BRACED`, `_BARE`, `_ESCAPES`, `lifecycle.empty_file_as_root`.

- [ ] **Step 1: Write the failing tests**

In `tests/runtime/api/test_secrets.py` delete every test of a removed function (missing, compose
refs, `parse_dotenv*`, `parse_example`/example, `defaults`), keep the validation and `declared` tests, and add:

```python
@pytest.mark.parametrize("hint", ["", "x" * 501, "a\x00b", "bad \ud800"])
def test_a_bad_hint_is_refused_with_its_code(hint):
    with pytest.raises(SecretError) as e:
        check_hint(hint)
    assert e.value.code == "secret_hint_invalid"


def test_a_hint_of_500_characters_with_a_url_is_accepted():
    check_hint("Stripe → Developers → API keys: https://dashboard.stripe.com/apikeys".ljust(500, "."))
```

In `tests/runtime/api/test_state.py` add (reuse the file's existing state/project fixture helpers):

```python
def test_a_request_shows_until_its_name_gets_a_value(state_with_project):
    st, pid = state_with_project
    st.request_secret(pid, "STRIPE_KEY", "Stripe dashboard")
    assert st.secret_requests(pid) == [{"name": "STRIPE_KEY", "hint": "Stripe dashboard"}]
    st.set_secrets(pid, {"STRIPE_KEY": "sk"})
    assert st.secret_requests(pid) == []


def test_a_request_for_a_name_that_already_has_a_value_is_not_listed(state_with_project):
    st, pid = state_with_project
    st.set_secrets(pid, {"STRIPE_KEY": "sk"})
    st.request_secret(pid, "STRIPE_KEY", "again")
    assert st.secret_requests(pid) == []


def test_a_request_never_marks_secrets_changed(state_with_project):
    st, pid = state_with_project
    st.request_secret(pid, "STRIPE_KEY", "hint")
    assert st.get(pid)["secrets_changed_at"] is None


def test_drop_secrets_clears_values_and_requests(state_with_project):
    st, pid = state_with_project
    st.set_secrets(pid, {"A": "1"})
    st.request_secret(pid, "B", "hint")
    st.drop_secrets(pid)
    assert st.secret_values(pid) == {} and st.secret_requests(pid) == []
```

Adapt the fixture name and the row getter to what `test_state.py` already uses. In
`test_migrate.py` extend the v6 assertion to the `secret_requests` table. In `test_lifecycle.py`
delete the `empty_file_as_root` test(s).

- [ ] **Step 2: Run them to verify they fail**

Run: `TMPDIR=/tmp/claude-1000 .venv/bin/python -m pytest -q tests/runtime/api/test_secrets.py tests/runtime/api/test_state.py tests/runtime/api/test_migrate.py`
Expected: FAIL (`check_hint`, `request_secret` undefined; no `secret_requests` table).

- [ ] **Step 3: Implement**

`core/secrets.py`: module docstring becomes `"""Project secrets: what a name, value and request hint may be, and which names a compose service sets itself. No storage, no FastAPI."""`. Delete the removed functions and constants. Add:

```python
MAX_HINT_CHARS = 500


def check_hint(hint: str) -> None:
    ok = 0 < len(hint) <= MAX_HINT_CHARS and "\x00" not in hint
    try:
        hint.encode("utf-8")
    except UnicodeEncodeError:
        ok = False
    if not ok:
        raise SecretError("secret_hint_invalid",
                          f"a hint is 1 to {MAX_HINT_CHARS} characters of plain text")
```

`core/migrate.py` `_v6_secrets` also creates:

```python
    conn.execute("""
        CREATE TABLE IF NOT EXISTS secret_requests (
            project_id TEXT NOT NULL,
            name TEXT NOT NULL,
            hint TEXT NOT NULL,
            created_at REAL NOT NULL,
            PRIMARY KEY (project_id, name)
        )""")
```

`core/state.py`:

```python
    def secret_requests(self, project_id) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(
                "SELECT name, hint FROM secret_requests r WHERE project_id=? "
                "AND NOT EXISTS (SELECT 1 FROM secrets s WHERE s.project_id=r.project_id "
                "AND s.name=r.name) ORDER BY name", (project_id,))]

    def request_secret(self, project_id, name, hint) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO secret_requests(project_id, name, hint, created_at) "
                "VALUES (?,?,?,?)", (project_id, name, hint, time.time()))
            self._conn.commit()

    def delete_request(self, project_id, name) -> bool:
        with self._lock:
            gone = self._conn.execute(
                "DELETE FROM secret_requests WHERE project_id=? AND name=?",
                (project_id, name)).rowcount > 0
            self._conn.commit()
            return gone
```

In `set_secrets`, before the commit:

```python
            self._conn.executemany(
                "DELETE FROM secret_requests WHERE project_id=? AND name=?",
                [(project_id, name) for name in values])
```

In `drop_secrets` also `DELETE FROM secret_requests WHERE project_id=?`.

`core/lifecycle.py`: delete `empty_file_as_root` and fold `_as_root` back into `remove_tree_as_root`
exactly as it is on `main` (`git show main:runtime/eggie_api/core/lifecycle.py`).

- [ ] **Step 4: Run tests to verify they pass**

Run the Step 2 command plus `tests/runtime/api/test_lifecycle.py`. Expected: PASS. Routes still
import the removed names, so `test_api_secrets.py` fails until Task 2 — expected; say so in the commit.

- [ ] **Step 5: Commit**

```bash
git add -A runtime/eggie_api/core tests/runtime/api
git commit -m "Secrets core: drop .env readers and defaults, add secret requests

Routes are reworked in the next commit.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Routes

**Files:**
- Modify: `runtime/eggie_api/routes/app.py`
- Test: `tests/runtime/api/test_api_secrets.py`

**Interfaces:**
- Consumes: Task 1's `check_hint`, `secret_requests`, `request_secret`, `delete_request`, `declared`.
- Produces (wire): `GET /projects/{id}/secrets` → `{"secrets": [{"name", "updated_at"}], "requested": [{"name", "hint"}], "restart_needed": bool}`;
  `PUT /projects/{id}/secret-requests/{name}` body `{"hint": str}` → 204;
  `DELETE /projects/{id}/secrets/{name}` removes value and/or request → 204, 404 `secret_not_found` if neither;
  project payload gains `"secrets_requested": int` next to `restart_needed`.

- [ ] **Step 1: Rewrite the tests**

In `test_api_secrets.py` delete every test about missing, defaults, `.env`/`.env.example`, import,
symlinks, `compose_default`, `shadowed`, `overrides_default`. Keep the tests for: value never returned,
invalid/reserved names, project total, unknown delete 404, every compose call gets env and plain docker
calls none, no-secrets project gets no env, `restart_needed` transitions (incl. crash-looping and
set-during-start), purge vs plain delete, routes on both mounts, a service's literal wins. Fix their
expected bodies to the new shape. Add:

```python
def test_a_request_is_listed_with_its_hint_until_a_value_is_saved(env):
    _project(env)
    r = env.client.put("/projects/blog/secret-requests/STRIPE_KEY",
                       json={"hint": "Stripe → Developers → API keys"})
    assert r.status_code == 204
    body = env.client.get("/projects/blog/secrets").json()
    assert body["requested"] == [{"name": "STRIPE_KEY", "hint": "Stripe → Developers → API keys"}]
    assert env.client.get("/projects/blog").json()["secrets_requested"] == 1
    env.client.put("/projects/blog/secrets/STRIPE_KEY", json={"value": SECRET})
    assert env.client.get("/projects/blog/secrets").json()["requested"] == []


def test_a_request_with_a_bad_hint_or_name_is_a_400(env):
    _project(env)
    assert env.client.put("/projects/blog/secret-requests/STRIPE_KEY",
                          json={"hint": "a\x00b"}).json()["error"]["code"] == "secret_hint_invalid"
    assert env.client.put("/projects/blog/secret-requests/DOCKER_HOST",
                          json={"hint": "x"}).json()["error"]["code"] == "secret_name_reserved"


def test_dismissing_a_request_does_not_need_a_restart(env):
    _project(env)
    _run_to_completion(env, env.client.post("/projects/blog/start"))
    env.client.put("/projects/blog/secret-requests/STRIPE_KEY", json={"hint": "x"})
    assert env.client.delete("/projects/blog/secrets/STRIPE_KEY").status_code == 204
    body = env.client.get("/projects/blog/secrets").json()
    assert body["requested"] == [] and body["restart_needed"] is False


def test_the_projects_env_files_are_neither_read_nor_touched(env):
    folder = _project(env)
    (folder / ".env").write_text("API_KEY=from-dotenv\nMODE=prod\n")
    (folder / ".env.example").write_text("API_KEY=\nEXTRA=1\n")
    env.client.put("/projects/blog/secrets/API_KEY", json={"value": SECRET})
    _run_to_completion(env, env.client.post("/projects/blog/start"))
    envs = [c.env for c in env.runner.calls if c.env]   # adapt to the FakeRunner's recorded-call shape
    assert envs and all(e == {"API_KEY": SECRET} for e in envs)
    assert (folder / ".env").read_text() == "API_KEY=from-dotenv\nMODE=prod\n"


def test_purge_drops_requests_and_plain_delete_keeps_them(env):
    _project(env, "kept")
    env.client.put("/projects/kept/secret-requests/B", json={"hint": "x"})
    env.client.delete("/projects/kept")
    assert env.state.secret_requests("kept") == [{"name": "B", "hint": "x"}]

    _project(env, "gone")
    env.client.put("/projects/gone/secret-requests/B", json={"hint": "x"})
    env.client.delete("/projects/gone?purge=true")
    assert env.state.secret_requests("gone") == []
```

Use the existing helpers and the FakeRunner's real recorded-call attribute names (see
`test_every_compose_call_gets_the_secrets_and_no_other_call_does`).

- [ ] **Step 2: Run to verify they fail**

Run: `TMPDIR=/tmp/claude-1000 .venv/bin/python -m pytest -q tests/runtime/api/test_api_secrets.py`
Expected: FAIL / import errors.

- [ ] **Step 3: Implement in `routes/app.py`**

- Delete `_dq`, `read_dotenv`, `example_of`, `dotenv_names`, the import-dotenv route and
  `read_text` if nothing else uses it.
- `project_env(project_id)` becomes `return state.secret_values(project_id) or None`.
- `class SecretHint(BaseModel): hint: str`.
- `list_secrets`:

```python
    @router.get("/projects/{project_id}/secrets")
    def list_secrets(project_id: str) -> dict:
        row = require_row(project_id)
        return {"secrets": state.secret_names(project_id),
                "requested": state.secret_requests(project_id),
                "restart_needed": restart_needed(row)}
```

- `put_secret`: unchanged (`set_secrets` now clears the request).
- New route:

```python
    @router.put("/projects/{project_id}/secret-requests/{name}", status_code=204)
    def request_secret(project_id: str, name: str, body: SecretHint) -> Response:
        require_row(project_id)
        secret_rules.check_name(name)
        secret_rules.check_hint(body.hint)
        state.request_secret(project_id, name, body.hint)
        return Response(status_code=204)
```

- `delete_secret`:

```python
        removed = state.delete_secret(project_id, name)
        dismissed = state.delete_request(project_id, name)
        if not (removed or dismissed):
            raise ApiError("secret_not_found",
                           f"project '{project_id}' has no secret '{name}'", 404)
```

- `payload`: add `"secrets_requested": len(state.secret_requests(row["id"]))` beside
  `restart_needed` (use the row's id key as `payload` already does).
- `start_work` keeps `declared` and the env plumbing as is.

- [ ] **Step 4: Run the full Python suite**

Run: `TMPDIR=/tmp/claude-1000 .venv/bin/python -m pytest -q`
Expected: all pass except `tests/runtime/cli/test_secret.py` cases about defaults/missing (Task 3).

- [ ] **Step 5: Commit** — `Secrets routes: requests, no .env handling` with the trailer.

---

### Task 3: CLI

**Files:**
- Modify: `runtime/cli/eggie.py`
- Test: `tests/runtime/cli/test_secret.py`

**Interfaces:**
- Consumes: Task 2 wire shapes.
- Produces: `eggie secret request NAME HINT`; `ApiClient.request_secret(project_id, name, hint)`.

- [ ] **Step 1: Tests** — delete `test_secret_list_counts_defaults_*`, `test_secret_list_says_one_default_*`;
  rewrite `test_secret_list_shows_names_and_missing_but_no_values` to requests; add:

```python
def test_secret_request_lists_the_name_and_hint_until_it_is_set(guest):
    folder = _project(guest)
    code, out, err = guest.run("secret", "request", "STRIPE_KEY",
                               "Stripe → Developers → API keys", cwd=folder)
    assert (code, err) == (0, "")
    _, out, _ = guest.run("secret", "list", cwd=folder)
    assert "Requested:" in out and "STRIPE_KEY — Stripe → Developers → API keys" in out
    guest.run("secret", "set", "STRIPE_KEY", cwd=folder, stdin="sk\n")
    _, out, _ = guest.run("secret", "list", cwd=folder)
    assert "Requested:" not in out and "sk" not in out.split()


def test_secret_rm_dismisses_a_request(guest):
    folder = _project(guest)
    guest.run("secret", "request", "STRIPE_KEY", "hint", cwd=folder)
    code, _, _ = guest.run("secret", "rm", "STRIPE_KEY", cwd=folder)
    assert code == 0
    _, out, _ = guest.run("secret", "list", cwd=folder)
    assert "STRIPE_KEY" not in out
```

- [ ] **Step 2: Run, expect FAIL** — `TMPDIR=/tmp/claude-1000 .venv/bin/python -m pytest -q tests/runtime/cli/test_secret.py`

- [ ] **Step 3: Implement**

```python
    def request_secret(self, project_id: str, name: str, hint: str) -> None:
        self._call("PUT", f"/projects/{project_id}/secret-requests/"
                          f"{urllib.parse.quote(name, safe='')}", {"hint": hint})


def cmd_secret_request(env: Env, name: str, hint: str) -> None:
    project_id = _require_project(env)
    env.client().request_secret(project_id, name, hint)
    print(f"Requested {name}. Ask the owner to fill it on {project_id}'s Secrets "
          "page in Eggie.", file=env.out)


def cmd_secret_list(env: Env) -> None:
    project_id = _require_project(env)
    data = env.client().secrets(project_id)
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
```

Parser: `secret_request = secret_sub.add_parser("request", help="ask the owner for a secret; shows on the project's Secrets page")`,
args `name`, `hint`; `list` help → `"show secret names and open requests"`; `rm` help →
`"remove a secret or a request"`; dispatch `"request": lambda: cmd_secret_request(env, args.name, args.hint)`.

- [ ] **Step 4: Full Python suite passes.**
- [ ] **Step 5: Commit** — `eggie secret request; list shows requests`.

---

### Task 4: Console

**Files:**
- Modify: `runtime/web/apps/console/src/projects/{types.ts,queries.ts,secrets.ts,secrets.test.ts}`
- Modify: `runtime/web/apps/console/src/screens/secrets/SecretsPage.tsx` (+ `.module.css` if a class goes unused)
- Modify: `runtime/web/apps/console/src/screens/project/{Tiles.tsx,ProjectPage.tsx}`
- Modify: `runtime/web/apps/console/src/mocks/handlers.ts`, `src/projects/view.test.ts` (fixture field)

**Interfaces:**
- Consumes: Task 2 wire shapes.
- Produces: `SecretsView = { secrets: {name: string; updated_at: number}[]; requested: {name: string; hint: string}[]; restart_needed: boolean }`;
  `Project.secrets_requested: number`; remove `useImportDotenv`.

- [ ] **Step 1: Test** — `secrets.test.ts`: add cases that `nameProblem("ld_preload", [])`, `nameProblem("PATH", [])`,
  `nameProblem("home", [])` return the reserved message, and `nameProblem("PATHS", [])` returns null.
- [ ] **Step 2: Run `npm test` in `runtime/web`, expect FAIL.**
- [ ] **Step 3: Implement**

`secrets.ts`:

```ts
const RESERVED = /^(COMPOSE_|DOCKER_|LD_)|^(PATH|HOME)$/i;
...
  if (RESERVED.test(name)) return "That name is reserved: Eggie and Docker read it themselves.";
  if (taken.includes(name)) return `There's already a secret called ${name} — use Replace to change it.`;
```

`Tiles.tsx` — take a `secretsLine: string` prop and render
`{KEY}Secrets<small>{secretsLine}</small>`. `ProjectPage.tsx` passes
`project.secrets_requested > 0 ? \`${project.secrets_requested} requested\` : "Keep API keys and passwords here, not in project files."`.

`SecretsPage.tsx`: delete `MissingRow`, `DefaultRow`, the `.env` notice, `useImportDotenv`,
`Collapsible`. `StoredRow` loses `resets` (button reads Delete; Edit reads Replace). Add:

```tsx
function RequestedRow({ id, name, hint }: { id: string; name: string; hint: string }) {
  const save = useSetSecret(id);
  const dismiss = useDeleteSecret(id);
  const [value, setValue] = useState("");
  return (
    <div className={s.row}>
      <span className={s.name}>{name}</span>
      <div className={s.grow}>
        <p className={page.muted}>{hint}</p>
        <TextField label="Value" secret value={value} onChange={setValue} />
      </div>
      <Button variant="primary" disabled={value === "" || save.isPending} onClick={() => save.mutate({ name, value }, { onSuccess: () => setValue("") })}>Save</Button>
      <Button disabled={dismiss.isPending} onClick={() => dismiss.mutate(name)}>Dismiss</Button>
      {(save.error || dismiss.error) && <Notice>{actionError(save.error ?? dismiss.error)}</Notice>}
    </div>
  );
}
```

Lead text: `Keys and passwords for outside services, like Stripe, OpenAI or your mail provider. Eggie hands them to {id} as environment variables when it starts; they override the same name in .env and never land in the project's files or git. Saved values can't be shown again, only replaced.`
Sections: `Requested` (when non-empty), `Your secrets` (when non-empty), `Add a secret` form
(unchanged logic; `taken` = stored names). Restart notice unchanged.

`types.ts`/`queries.ts`: new `SecretsView`, `secrets_requested` on `Project`, drop `useImportDotenv`.

`handlers.ts`: per-project mock `{ names: Map<string, number>; requested: Map<string, string> }`;
`?scenario=secrets` seeds recipe-box with `STRIPE_KEY` stored and `OPENAI_API_KEY` requested
(hint `"OpenAI → API keys → Create new secret key"`). GET returns the new shape; PUT on a secret
deletes its request; DELETE removes either (404 if neither) and calls `touched` only when a value
went; drop the import handler; projects carry `secrets_requested`.

- [ ] **Step 4: In `runtime/web`: `npm test && npm run typecheck && npm run build && npm run check-offline`, all green.**
- [ ] **Step 5: Commit** — `Secrets page: requests, no defaults or .env import`.

---

### Task 5: Agent instructions and docs

**Files:**
- Modify: `runtime/instructions/eggie.md`, `runtime/eggie_api/CLAUDE.md`, `runtime/CLAUDE.md` (if its
  line mentions defaults), `runtime/web/CLAUDE.md`, `docs/architecture.md`
- Test: `grep -rn "env.example\|import-dotenv\|Needs a value\|Defaults" runtime docs/architecture.md`
  returns nothing about this feature.

- [ ] **Step 1: Replace the "Settings and secrets" bullet in `runtime/instructions/eggie.md` with:**

```markdown
- Settings and secrets: use the project's `.env` as on any laptop — create it from the project's
  template, edit it for settings, and let framework commands write their own keys (Laravel's
  `php artisan key:generate`). A key or password from an outside service (Stripe, OpenAI, mail)
  is a secret: tell the user where to get it, run `eggie secret request NAME "where to get it"`,
  and ask them to fill it on the project's **Secrets** page in Eggie. Leave the name empty or out
  of `.env`; Eggie hands the value to every service as an environment variable, and it wins over
  `.env`. Never write a secret value into a file, the compose file or a commit. If the user insists
  on putting it in `.env`, do it, but tell them once that it then lives in the project folder. A
  service that sets the name to a literal in its own `environment:` beats Eggie: change it to
  `${NAME}`. A secret change needs a restart. `.env` loaders in override mode, config cached into an
  image and build-time variables don't see Eggie's values. `eggie secret list` shows what is set and
  requested. Run one-off commands with `docker exec <container> …` (find it with `docker ps`); a
  `docker compose run` you start gets none of Eggie's values.
```

- [ ] **Step 2: `runtime/eggie_api/CLAUDE.md` Secrets bullet becomes:**

```markdown
- **Secrets** (`core/secrets.py`) — per-project values in `state.db` (`secrets`, v6), outside
  every project folder and never synced; requests (`secret_requests`: name + hint) ask the owner
  for one and vanish once it has a value. The project's `.env` is never read or written. A service
  gets a name unless it sets that name to a literal in its own `environment:` (`declared`).
  `compose_up` lists bare names in `.eggie/overlay.yml`; values go only into the environment of
  compose commands that load the user's file (`up`, `ps`, `down`, `logs`, `container_id`) — compose
  interpolates the file for each, so `${KEY:?}` breaks any that lacks them. `restart_needed` =
  `started_ok` or `crash_looping` and `secrets_changed_at > last_started_at`; a start is stamped
  before it reads values. Reserved names (`COMPOSE_`/`DOCKER_`/`LD_`, `PATH`, `HOME`) are refused.
  Services pulled in through compose `include:` get no names. Purge drops values and requests.
```

`runtime/web/CLAUDE.md` secrets line: `Sections: Requested (hint + value), Your secrets
(write-only, typed values visible), Add. Mock: ?scenario=secrets.` `docs/architecture.md`: replace
the precedence sentence with `Secrets reach containers as environment variables through the compose
process environment and win over the project's own .env; a literal a service sets in the compose
file wins over them; the overlay lists names only.`

- [ ] **Step 3: Full Python suite + web checks green.**
- [ ] **Step 4: Commit** — `Agent instructions and docs for Revision 3 secrets`.

---

### Task 6: eggie-skills PR #1 rework (separate repo)

**Repo:** `/tmp/claude-1000/-home-ihor-projects-local-environment-for-non-tech-poc/090baa0f-1c5a-4ef4-b016-f32a743cacbe/scratchpad/eggie-skills-13`,
branch `feature/13-project-secrets`, PR eggie-io/eggie-skills#1. Tests: `python -m pytest -q`.

- [ ] **Step 1:** Restore every `omelet-stack/references/*.md` to `origin/main`
  (`git checkout origin/main -- omelet-stack/references`), so recipes use `.env` as before.
- [ ] **Step 2:** In each recipe that needs an outside credential (search for `API_KEY`, `SECRET`,
  `STRIPE`, `SMTP`, `MAIL_PASSWORD`), add one line under its env/setup notes:
  `Outside credentials (<names>): run \`eggie secret request NAME "where to get it"\` and ask the
  owner to fill them on the Secrets page; leave them empty in .env.` Recipes with none get nothing.
- [ ] **Step 3:** `omelet-rules/assets/AGENTS.md` and `omelet-setup/SKILL.md`: replace the
  Revision 2 text with the same rule as `runtime/instructions/eggie.md` (Task 5 Step 1), shortened to
  3–4 lines.
- [ ] **Step 4:** `tests/test_stack_recipes.py`: keep the skeleton checks; keep the hard-coded-secret
  guard only if it passes against the restored recipes, otherwise narrow it to outside-credential
  names (`*_API_KEY`, `STRIPE_*`, `*_SECRET_KEY`) never being compose literals.
- [ ] **Step 5:** `python -m pytest -q` green; commit `Secrets: .env stays, credentials via eggie secret request`
  with the trailer; push and update the PR body.

## Self-review notes

- Spec §1–§3 → Tasks 1–2; §4 → Task 2; §5 → Task 4; §6 → Task 3; §7 → Tasks 5–6; §8 → tests in each.
- D9 notice → Task 4 tile line and page lead.
