# Project secrets — Revision 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `.env.example` defaults (delivered to the app, editable), let a compose literal win over Eggie's variables, and fold in the review fixes — on the existing `feature/13-project-secrets` branch (PR eggie-io/eggie#55).

**Architecture:** `core/secrets.py` gains a lenient `.env.example` reader, reserved-name rules, per-service "declared" names, `defaults`/`missing`/`importable` helpers. The API computes one effective environment (`defaults` overlaid by stored values) for every compose call and lists names in the overlay per service, skipping names a service declares itself. The console shows three sections; the CLI prints a defaults count.

**Tech Stack:** Python 3.12 + FastAPI + sqlite; stdlib-only CLI; React + TanStack Query + MSW + Vitest.

**Spec:** `docs/superpowers/specs/2026-10-08-project-secrets-design.md` — **Revision 2** section (R1–R8, D7–D12) wins over sections 1–9.

## Global Constraints

- Run Python tests from the worktree root: `TMPDIR=$PWD/.tmp .venv/bin/python -m pytest <path> -q`. Web: from `runtime/web`, `npm test && npm run typecheck && npm run build && npm run check-offline`.
- No stored value ever appears in any response, error, log, argv, overlay or CLI output. Default values (from the repo's `.env.example`) are returned in clear.
- Reserved names — never listed, stored or delivered: prefixes `COMPOSE_`, `DOCKER_`, `LD_` (case-insensitive), exact names `PATH`, `HOME` (case-insensitive).
- Precedence: compose literal (name declared in a service's own `environment:`) > stored value > `.env.example` default.
- `PUT` refuses an empty value with `secret_invalid_value`.
- Import stores only non-empty values that differ from the default; reserved keys stay in `.env` (file rewritten with only them, or emptied).
- Value fields: visible while typing, eye toggle, `autocomplete="off"`; write-only once saved.
- `runtime/cli/eggie.py` stdlib-only; `host/` untouched; `API_VERSION` unchanged.
- Comments only for non-obvious reasons; tests only where a wrong result is plausible; no component tests.
- Commit messages end with a blank line and the Co-Authored-By trailer the harness specifies.

## Review Focus

1. A service whose compose `environment:` declares `DATABASE_URL: postgres://db/app` keeps that value even when a stored value or default exists — pinned in Task 2 (`test_a_service_that_declares_a_name_keeps_its_own_value`).
2. A `.env.example` with a broken line (no `=`, unclosed quote) still yields the other defaults and never fails a start — pinned in Task 1 (`test_example_parsing_skips_broken_lines`).
3. `cp .env.example .env` then "Move into secrets" stores nothing and offers nothing (all values equal defaults or empty) — pinned in Task 2 (`test_a_dotenv_copied_from_the_example_is_not_offered`).
4. A `.env` with `COMPOSE_PROJECT_NAME=x` and `API_KEY=k` imports `API_KEY` and leaves exactly the `COMPOSE_PROJECT_NAME` line in `.env` — pinned in Task 2 (`test_import_keeps_reserved_lines_in_dotenv`).
5. A `.env` saved with a UTF-8 BOM imports normally — pinned in Task 1 (`test_parse_dotenv_ignores_a_bom`).

---

### Task 1: Core rules (`core/secrets.py`) and lock-side timestamps (`core/state.py`)

**Files:**
- Modify: `runtime/eggie_api/core/secrets.py`
- Modify: `runtime/eggie_api/core/state.py` (`set_secrets`, `delete_secret`)
- Modify tests: `tests/runtime/api/test_secrets.py`, `tests/runtime/api/test_state.py`

**Interfaces (Produces):**
- `is_reserved(name: str) -> bool`
- `check_name(name)` — reserved check uses `is_reserved`; message: `"'{name}' is reserved: Eggie and Docker read it from their own environment"`.
- `check_value(value)` — additionally raises `secret_invalid_value` for an unpaired surrogate (catch `UnicodeEncodeError`).
- `parse_dotenv(text: str) -> dict[str, str]` — strips a leading `﻿`; **no longer rejects reserved names** (returns them; callers filter). Invalid name → `dotenv_invalid` (line number only) as now.
- `parse_example(text: str | None) -> dict[str, str]` — lenient: same syntax as `parse_dotenv`, but a line that fails is skipped; an unclosed quote ends parsing (keep what was read); reserved names and invalid values are skipped. `None` → `{}`.
- `declared(compose: dict) -> tuple[list[str], dict[str, set[str]]]` — `(service names, {service: names that service sets to a **literal** value in its own environment})`. Literal = a value that is not None and contains no `$` (so `X: ${X}`, `X: ${X:-d}` and a bare `X` are NOT declared — they resolve from Eggie's environment). Mapping form: keys with a literal value. List form: `NAME=value` entries whose value has no `$`; a bare `NAME` is not declared. Non-dict services / missing environment → empty set.
- `defaults(example: dict[str, str]) -> dict[str, str]` — non-empty values, reserved removed.
- `missing(example: dict[str, str], compose_text: str | None, *, have: set[str], declared_names: set[str]) -> list[str]` — sorted `({k with empty value} ∪ compose refs without default) − have − defaults − declared_names − reserved`.
- `importable(values: dict[str, str], defaults_: dict[str, str]) -> dict[str, str]` — drop empty, reserved, and values equal to `defaults_.get(k)`.
- Remove `expected()` and `_example_keys()` (replaced by the above); update their tests.
- `State.set_secrets(project_id, values, at=None)` and `State.delete_secret(project_id, name, at=None)` — when `at` is None, take `time.time()` **inside** the lock.

- [ ] **Step 1: Write the failing tests** — replace the `expected` tests and the reserved `parse_dotenv` test in `tests/runtime/api/test_secrets.py` with:

```python
from eggie_api.core.secrets import (SecretError, check_name, check_total,
                                    check_value, declared, defaults, importable,
                                    is_reserved, missing, parse_dotenv,
                                    parse_example)


@pytest.mark.parametrize("name", ["COMPOSE_FILE", "docker_host", "LD_PRELOAD",
                                  "PATH", "home"])
def test_names_the_runtime_reads_itself_are_reserved(name):
    assert is_reserved(name)
    with pytest.raises(SecretError) as e:
        check_name(name)
    assert e.value.code == "secret_name_reserved"


@pytest.mark.parametrize("name", ["PATHS", "MY_HOME", "LDAP_URL", "API_KEY"])
def test_lookalikes_are_not_reserved(name):
    assert not is_reserved(name)


def test_an_unpaired_surrogate_is_an_invalid_value_not_a_crash():
    with pytest.raises(SecretError) as e:
        check_value("a\ud800b")
    assert e.value.code == "secret_invalid_value"


def test_parse_dotenv_ignores_a_bom():
    assert parse_dotenv("﻿API_KEY=k\n") == {"API_KEY": "k"}


def test_parse_dotenv_returns_reserved_names_for_the_caller_to_handle():
    assert parse_dotenv("COMPOSE_PROJECT_NAME=x\nA=1\n") == {
        "COMPOSE_PROJECT_NAME": "x", "A": "1"}


def test_example_parsing_skips_broken_lines():
    text = ("APP_NAME=Laravel\nnot a pair\n1BAD=x\nCOMPOSE_FILE=x\n"
            "APP_KEY=\nQUOTED=\"a b\"\nLAST='never closed\nIGNORED=1\n")
    assert parse_example(text) == {"APP_NAME": "Laravel", "APP_KEY": "",
                                   "QUOTED": "a b"}


def test_example_of_none_is_empty():
    assert parse_example(None) == {}


def test_declared_reads_mapping_and_list_environments():
    compose = {"services": {
        "web": {"environment": {"DATABASE_URL": "postgres://db/app", "X": None,
                                "Y": "${Y}", "PORT": 8080}},
        "worker": {"environment": ["A=1", "B", "C=${C:-d}"]},
        "db": {"image": "postgres"},
    }}
    services, names = declared(compose)
    assert services == ["web", "worker", "db"]
    assert names == {"web": {"DATABASE_URL", "PORT"}, "worker": {"A"}, "db": set()}


def test_declared_tolerates_a_compose_without_services():
    assert declared({}) == ([], {})


def test_defaults_are_non_empty_and_never_reserved():
    assert defaults({"A": "1", "B": "", "PATH": "/x"}) == {"A": "1"}


def test_missing_combines_empty_example_keys_and_compose_refs():
    example = {"APP_KEY": "", "APP_NAME": "Laravel", "STRIPE_KEY": "",
               "DB_PASSWORD": "", "HOME": ""}
    compose = "services:\n  web:\n    environment:\n      A: ${OPENAI_KEY}\n      B: ${APP_NAME}\n      C: ${OPT:-x}\n"
    assert missing(example, compose, have={"STRIPE_KEY"},
                   declared_names={"DB_PASSWORD"}) == ["APP_KEY", "OPENAI_KEY"]


def test_importable_drops_empty_reserved_and_unchanged_values():
    values = {"APP_NAME": "Laravel", "APP_KEY": "base64:abc", "EMPTY": "",
              "COMPOSE_PROJECT_NAME": "x", "APP_ENV": "production"}
    assert importable(values, {"APP_NAME": "Laravel", "APP_ENV": "local"}) == {
        "APP_KEY": "base64:abc", "APP_ENV": "production"}
```

Append to `tests/runtime/api/test_state.py`:

```python
def test_without_an_explicit_time_the_change_is_stamped_at_write(tmp_path):
    import time
    s = State(tmp_path / "state.db")
    s.add_project("a", "/p/a", "d")
    before = time.time()
    s.set_secrets("a", {"KEY": "v"})
    stamped = s.get_project("a")["secrets_changed_at"]
    assert before <= stamped <= time.time()
    assert s.delete_secret("a", "KEY") is True
    assert s.get_project("a")["secrets_changed_at"] >= stamped
```

- [ ] **Step 2: Run to verify failure** — `TMPDIR=$PWD/.tmp .venv/bin/python -m pytest tests/runtime/api/test_secrets.py tests/runtime/api/test_state.py -q` → ImportError on the new names.

- [ ] **Step 3: Implement** in `core/secrets.py`:

```python
RESERVED_PREFIXES = ("COMPOSE_", "DOCKER_", "LD_")
RESERVED_NAMES = frozenset({"PATH", "HOME"})


def is_reserved(name: str) -> bool:
    upper = name.upper()
    return upper in RESERVED_NAMES or upper.startswith(RESERVED_PREFIXES)


def check_name(name: str) -> None:
    if not NAME.fullmatch(name):
        raise SecretError("secret_name_invalid", ...unchanged...)
    if is_reserved(name):
        raise SecretError("secret_name_reserved",
                          f"'{name}' is reserved: Eggie and Docker read it "
                          "from their own environment")


def check_value(value: str) -> None:
    if "\x00" in value:
        raise SecretError("secret_invalid_value",
                          "a secret's value can't contain a NUL character")
    try:
        size = len(value.encode("utf-8"))
    except UnicodeEncodeError:
        raise SecretError("secret_invalid_value",
                          "a secret's value can't contain unpaired surrogate "
                          "characters") from None
    if size > MAX_VALUE_BYTES:
        raise SecretError("secret_too_large", ...unchanged...)
```

Refactor `parse_dotenv` into a private generator `_entries(text)` that yields `(number, key, value)` or raises `SecretError` for the line it is on, and two callers:

```python
def _entries(text: str):
    lines = text.removeprefix("﻿").splitlines()
    i = 0
    while i < len(lines):
        number = i + 1
        line = lines[i].strip()
        i += 1
        if not line or line.startswith("#"):
            continue
        line = line.removeprefix("export ").lstrip()
        key, sep, rest = line.partition("=")
        key = key.strip()
        if not sep:
            yield number, SecretError("dotenv_invalid",
                                      f".env line {number} isn't NAME=value")
            continue
        # An invalid key is often a piece of an unquoted secret, so its
        # text must not reach the error message.
        if not NAME.fullmatch(key):
            yield number, SecretError("dotenv_invalid",
                                      f".env line {number} has an invalid name")
            continue
        rest = rest.lstrip()
        if rest[:1] in ("'", '"'):
            quote, body = rest[0], rest[1:]
            end = _closing(body, quote)
            while end == -1:
                if i >= len(lines):
                    yield number, SecretError(
                        "dotenv_invalid",
                        f".env line {number} opens a quote that is never closed")
                    return
                body += "\n" + lines[i]
                i += 1
                end = _closing(body, quote)
            value = _unescape(body[:end]) if quote == '"' else body[:end]
        elif rest.startswith("#"):
            value = ""
        else:
            value = re.split(r"\s+#", rest, maxsplit=1)[0].strip()
        yield number, (key, value)


def parse_dotenv(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for _number, entry in _entries(text):
        if isinstance(entry, SecretError):
            raise entry
        key, value = entry
        check_value(value)
        values[key] = value
    return values


def parse_example(text: str | None) -> dict[str, str]:
    """`.env.example` is the project's list of variables; a line that doesn't
    parse must never stop a start, so it is skipped."""
    values: dict[str, str] = {}
    for _number, entry in _entries(text or ""):
        if isinstance(entry, SecretError):
            continue
        key, value = entry
        try:
            check_value(value)
        except SecretError:
            continue
        if not is_reserved(key):
            values[key] = value
    return values


def declared(compose: dict) -> tuple[list[str], dict[str, set[str]]]:
    services = compose.get("services")
    if not isinstance(services, dict):
        return [], {}
    names: dict[str, set[str]] = {}
    for service, spec in services.items():
        env = spec.get("environment") if isinstance(spec, dict) else None
        if isinstance(env, dict):
            pairs = [(str(k), v) for k, v in env.items()]
        elif isinstance(env, list):
            pairs = [(k, v) for k, sep, v in (str(e).partition("=") for e in env) if sep]
        else:
            pairs = []
        # `X: ${X}` or a bare `X` takes its value from Eggie's environment;
        # only a literal is the project's own choice.
        names[service] = {k for k, v in pairs if v is not None and "$" not in str(v)}
    return list(services), names


def defaults(example: dict[str, str]) -> dict[str, str]:
    return {k: v for k, v in example.items() if v != "" and not is_reserved(k)}


def missing(example: dict[str, str], compose_text: str | None, *,
            have: set[str], declared_names: set[str]) -> list[str]:
    wanted = ({k for k, v in example.items() if v == ""}
              | (_compose_refs(compose_text) if compose_text else set()))
    taken = have | set(defaults(example)) | declared_names
    return sorted(n for n in wanted - taken if not is_reserved(n))


def importable(values: dict[str, str], defaults_: dict[str, str]) -> dict[str, str]:
    return {k: v for k, v in values.items()
            if v != "" and not is_reserved(k) and v != defaults_.get(k)}
```

Delete `_example_keys` and `expected`. In `state.py`, change the two signatures to `at: float | None = None` and inside the lock: `at = time.time() if at is None else at` (add `import time`).

Note: callers of `expected` in `routes/app.py` break until Task 2; that is expected — run only the Task 1 test files in Step 4, and do NOT run the full suite until Task 2 lands. Commit anyway.

- [ ] **Step 4: Run** the two test files → PASS.
- [ ] **Step 5: Commit** — `git commit -m "Read .env.example defaults and tighten reserved names"`.

---

### Task 2: API — effective environment, compose precedence, routes, import

**Files:**
- Modify: `runtime/eggie_api/core/overlay.py` (`build_overlay`), `runtime/eggie_api/core/project.py` (`overlay_yaml`), `runtime/eggie_api/core/lifecycle.py` (`_write_overlay`, `compose_up`)
- Modify: `runtime/eggie_api/routes/app.py`
- Modify tests: `tests/runtime/api/test_overlay.py`, `tests/runtime/api/test_lifecycle.py`, `tests/runtime/api/test_api_secrets.py`

**Interfaces:**
- Consumes Task 1: `parse_example`, `declared`, `defaults`, `missing`, `importable`, `is_reserved`, `parse_dotenv`, `State.set_secrets(pid, values)` / `delete_secret(pid, name)` without `at`.
- Produces:
  - `build_overlay(..., services=(), secret_names=(), declared: Mapping[str, set[str]] | None = None)` — a service gets `environment: [sorted names not in declared[service]]`; a service left with no names gets no `environment` key (and a non-web service with nothing gets no entry).
  - `overlay_yaml(..., declared=None)`, `_write_overlay(..., declared=None)`, `compose_up(..., services=(), secrets=None, declared=None)` pass it through.
  - `GET /projects/{id}/secrets` →
    `{"secrets": [{"name","updated_at","overrides_default": bool}], "missing": [str], "defaults": [{"name","value","overridden": bool}], "dotenv": null | {"names": [str], "error": str|null}, "restart_needed": bool}`
    (`defaults` sorted by name; `dotenv.names` = sorted keys of `importable(parse_dotenv(.env), defaults)`; offer is `null` when that is empty).
  - `PUT` with `""` → 400 `secret_invalid_value`.
  - Import per spec R4; returns `{"imported": [sorted stored names]}`; 404 `dotenv_missing` when nothing is importable.

- [ ] **Step 1: Write the failing tests.**

`tests/runtime/api/test_overlay.py`:

```python
def test_a_name_a_service_declares_is_not_listed_for_that_service():
    ov = build_overlay("p", [WebSpec("web", 80)], "d.io", services=["web", "worker"],
                       secret_names=["DATABASE_URL", "KEY"],
                       declared={"web": {"DATABASE_URL"}, "worker": set()})
    assert ov["services"]["web"]["environment"] == ["KEY"]
    assert ov["services"]["worker"]["environment"] == ["DATABASE_URL", "KEY"]


def test_a_service_that_declares_every_name_gets_no_environment_entry():
    ov = build_overlay("p", [], "d.io", services=["db"], secret_names=["A"],
                       declared={"db": {"A"}})
    assert "db" not in ov["services"]
```

`tests/runtime/api/test_api_secrets.py` (reuse the file's helpers `_project`, `SECRET`, `COMPOSE_TWO`; read the file first):

```python
COMPOSE_DECLARES = """
services:
  web:
    image: nginx
    ports: ["8080:80"]
    environment:
      DATABASE_URL: postgres://db/app
"""


def _overlay_names(env, folder):
    import base64, yaml
    write = [a for a in env.runner.calls if a[0] == "bash" and "overlay.yml" in a[-1]][-1]
    text = base64.b64decode(write[-1].split("echo ", 1)[1].split(" ", 1)[0]).decode()
    return yaml.safe_load(text)["services"]


def test_a_service_that_declares_a_name_keeps_its_own_value(env):
    folder = _project(env, compose=COMPOSE_DECLARES)
    (folder / ".env.example").write_text("DATABASE_URL=postgres://localhost/app\n")
    env.client.put("/projects/blog/secrets/DATABASE_URL", json={"value": "x"})
    _run_to_completion(env, env.client.post("/projects/blog/up"))
    assert "environment" not in _overlay_names(env, folder).get("web", {})


def test_defaults_reach_compose_and_stored_values_override_them(env):
    folder = _project(env)
    (folder / ".env.example").write_text("APP_NAME=Blog\nMODE=dev\nAPI_KEY=\n")
    env.client.put("/projects/blog/secrets/MODE", json={"value": "prod"})
    _run_to_completion(env, env.client.post("/projects/blog/up"))
    ups = [e for a, e in zip(env.runner.calls, env.runner.envs) if a[-2:] == ["up", "-d"]]
    assert ups == [{"APP_NAME": "Blog", "MODE": "prod"}]
    assert _overlay_names(env, folder)["worker"]["environment"] == ["APP_NAME", "MODE"]


def test_the_listing_shows_defaults_in_clear_and_values_never(env):
    folder = _project(env)
    (folder / ".env.example").write_text("APP_NAME=Blog\nMODE=dev\nAPI_KEY=\n")
    env.client.put("/projects/blog/secrets/MODE", json={"value": SECRET})
    body = env.client.get("/projects/blog/secrets").json()
    assert body["defaults"] == [{"name": "APP_NAME", "value": "Blog", "overridden": False},
                                {"name": "MODE", "value": "dev", "overridden": True}]
    assert body["secrets"] == [{"name": "MODE", "updated_at": body["secrets"][0]["updated_at"],
                                "overrides_default": True}]
    assert body["missing"] == ["API_KEY"]
    assert SECRET not in env.client.get("/projects/blog/secrets").text


def test_an_empty_value_is_refused(env):
    _project(env)
    resp = env.client.put("/projects/blog/secrets/API_KEY", json={"value": ""})
    assert (resp.status_code, resp.json()["error"]["code"]) == (400, "secret_invalid_value")


def test_a_dotenv_copied_from_the_example_is_not_offered(env):
    folder = _project(env)
    (folder / ".env.example").write_text("APP_NAME=Blog\nAPI_KEY=\n")
    (folder / ".env").write_text("APP_NAME=Blog\nAPI_KEY=\n")
    assert env.client.get("/projects/blog/secrets").json()["dotenv"] is None
    resp = env.client.post("/projects/blog/secrets/import-dotenv")
    assert resp.json()["error"]["code"] == "dotenv_missing"
    assert env.state.secret_values("blog") == {}


def test_import_stores_only_values_that_differ_from_defaults(env):
    folder = _project(env)
    (folder / ".env.example").write_text("APP_NAME=Blog\nAPP_ENV=local\nAPI_KEY=\n")
    (folder / ".env").write_text("APP_NAME=Blog\nAPP_ENV=production\nAPI_KEY=k\nEMPTY=\n")
    assert env.client.get("/projects/blog/secrets").json()["dotenv"]["names"] == ["API_KEY", "APP_ENV"]
    assert env.client.post("/projects/blog/secrets/import-dotenv").json() == {
        "imported": ["API_KEY", "APP_ENV"]}
    assert env.state.secret_values("blog") == {"API_KEY": "k", "APP_ENV": "production"}
    assert (folder / ".env").read_text() == ""


def test_import_keeps_reserved_lines_in_dotenv(env):
    folder = _project(env)
    (folder / ".env").write_text("COMPOSE_PROJECT_NAME=shop\nAPI_KEY=k\n")
    assert env.client.post("/projects/blog/secrets/import-dotenv").json() == {"imported": ["API_KEY"]}
    from eggie_api.core.secrets import parse_dotenv
    assert parse_dotenv((folder / ".env").read_text()) == {"COMPOSE_PROJECT_NAME": "shop"}
```

Update existing tests in that file that the new rules change (read each failure; e.g. the earlier import test that stored an empty `PEM` or relied on file values overwriting; tests asserting `missing` from `.env.example` keys that have values — those are now defaults). Do not weaken a test's guarantee; adapt its fixture to the new rules and say so in the report.

- [ ] **Step 2: Run** `tests/runtime/api/test_overlay.py tests/runtime/api/test_api_secrets.py` → FAIL.

- [ ] **Step 3: Implement.**

`overlay.py` — in `build_overlay` add `declared: Mapping[str, set[str]] | None = None` and replace the names block with:

```python
    names = sorted(secret_names)
    if names:
        declared = declared or {}
        for service in dict.fromkeys([*services, *overlay_services]):
            # A value the service sets itself is the project's explicit choice.
            own = [n for n in names if n not in declared.get(service, set())]
            if own:
                overlay_services.setdefault(service, {})["environment"] = own
```

Thread `declared` through `overlay_yaml`, `_write_overlay` and `compose_up` (keyword, default `None`).

`routes/app.py`:
- Replace `secret_env(project_id)` with:

```python
    def example_of(project_id: str) -> dict[str, str]:
        return secret_rules.parse_example(read_text(project_dir(project_id) / ".env.example"))

    def project_env(project_id: str) -> dict[str, str] | None:
        # Stored values win over the repo's defaults.
        return {**secret_rules.defaults(example_of(project_id)),
                **state.secret_values(project_id)} or None
```

  and use `project_env` at every former `secret_env` call site (restart's down, `/down`, logs buffered + stream, `diagnose`, and the start's values).
- `read_text`: `path.read_text(encoding="utf-8-sig")`.
- `start_work`: parse the compose file once: `compose = parse_yaml(project_dir(project_id) / constants.COMPOSE_FILE)`; `services, declared_by = secret_rules.declared(compose)`; `name = str(compose.get("name") or project_id)` (drop the second parse in `compose_name_of` for this path only if `compose_name_of` has no other caller; otherwise keep it). In `work`: `values = project_env(project_id) or {}` after `began`; `compose_up(..., services=services, secrets=values, declared=declared_by)`; `diagnose(..., env=values or None)`.
- `list_secrets`:

```python
        row = require_row(project_id)
        d = project_dir(project_id)
        example = example_of(project_id)
        base = secret_rules.defaults(example)
        stored = state.secret_names(project_id)
        have = {s["name"] for s in stored}
        compose_text = read_text(d / constants.COMPOSE_FILE)
        try:
            compose = yaml.safe_load(compose_text or "") or {}
        except yaml.YAMLError:
            compose = {}
        _services, declared_by = secret_rules.declared(compose if isinstance(compose, dict) else {})
        declared_names = set().union(*declared_by.values()) if declared_by else set()
        dotenv = None
        try:
            text = read_dotenv(d / ".env")
            if text is not None:
                offer = secret_rules.importable(secret_rules.parse_dotenv(text), base)
                if offer:
                    dotenv = {"names": sorted(offer), "error": None}
        except SecretError as e:
            dotenv = {"names": [], "error": e.message}
        return {"secrets": [{**s, "overrides_default": s["name"] in base} for s in stored],
                "missing": secret_rules.missing(example, compose_text, have=have,
                                                declared_names=declared_names),
                "defaults": [{"name": k, "value": v, "overridden": k in have}
                             for k, v in sorted(base.items())],
                "dotenv": dotenv,
                "restart_needed": restart_needed(row)}
```

- `put_secret`: before `check_name`, `if body.value == "": raise SecretError("secret_invalid_value", "a value can't be empty; delete the secret instead")`; call `state.set_secrets(project_id, {name: body.value})` (no `at`).
- `delete_secret`: `state.delete_secret(project_id, name)`.
- `import_dotenv` (under the lock): `values = parse_dotenv(text)` (text None → `{}`); `store = importable(values, defaults(example_of(project_id)))`; empty → 404 `dotenv_missing` ("this project's .env has nothing that isn't already a default"); `check_total({**state.secret_values(project_id), **store})`; `state.set_secrets(project_id, store)`; then rewrite the file with only reserved keys:

```python
            kept = {k: v for k, v in values.items() if secret_rules.is_reserved(k)}
            content = "".join(f'{k}="{_dq(v)}"\n' for k, v in kept.items())
```

  where `_dq(v)` escapes `\\` → `\\\\`, `"` → `\\"`, newline → `\\n` (module-level helper). Write with `os.open(path, os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW)` + `os.write`. On `PermissionError`: if `content` is empty use `lifecycle.empty_file_as_root`, else treat as not emptied. Not emptied → existing 409 `dotenv_not_removed`. Return `{"imported": sorted(store)}`.

- [ ] **Step 4: Run** `TMPDIR=$PWD/.tmp .venv/bin/python -m pytest tests/runtime/api -q` then the full suite → PASS.
- [ ] **Step 5: Commit** — `git commit -m "Deliver .env.example defaults and let compose literals win"`.

---

### Task 3: CLI — defaults in `eggie secret list`

**Files:** Modify `runtime/cli/eggie.py` (`cmd_secret_list`); test `tests/runtime/cli/test_secret.py`.

- [ ] **Step 1: Failing test** (append):

```python
def test_secret_list_counts_defaults_and_never_prints_their_values(guest):
    folder = _project(guest)
    (folder / ".env.example").write_text("APP_NAME=Blog\nMODE=dev\nAPI_KEY=\n")
    code, out, _ = guest.run("secret", "list", cwd=folder)
    assert code == 0
    assert "2 defaults from .env.example" in out
    assert "Blog" not in out
    assert "API_KEY" in out
```

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** — after the missing line in `cmd_secret_list`:

```python
    count = len(data.get("defaults") or [])
    if count:
        print(f"{count} defaults from .env.example; change them on the "
              "project's Secrets page in Eggie.", file=env.out)
```

  and change the "has no secrets" condition to `if not names and not missing and not count`. Rename the missing label to `"Needs a value: "`; update any existing test asserting `"missing"` (case-insensitive `"missing"` check in `test_secret_list_shows_names_and_missing_but_no_values` → assert `"Needs a value"`).
- [ ] **Step 4: Run** `tests/runtime/cli tests/test_constants_agree.py` → PASS.
- [ ] **Step 5: Commit** — `git commit -m "Show .env.example defaults in eggie secret list"`.

---

### Task 4: Console — three sections, visible-while-typing fields

**Files:**
- Modify: `runtime/web/packages/ui/src/components/TextField.tsx` (+ its CSS module)
- Modify: `runtime/web/apps/console/src/projects/types.ts`, `screens/secrets/SecretsPage.tsx`, `screens/secrets/SecretsPage.module.css`, `mocks/handlers.ts`

**Interfaces:** consumes Task 2's GET shape.

- [ ] **Step 1: `TextField`** — replace the `type` prop with `secret?: boolean`. When `secret`, render the input as `type={shown ? "text" : "password"}` with `shown` state defaulting to `true`, plus an eye button (`aria-label={shown ? "Hide value" : "Show value"}`, `type="button"`) inside the field wrapper; always `autoComplete="off"` (secret fields too), `spellCheck={false}`. Keep every other prop unchanged. Update existing `type="password"` callers to `secret`.

- [ ] **Step 2: Types** — in `projects/types.ts`:

```ts
export interface SecretsView {
  secrets: { name: string; updated_at: number; overrides_default: boolean }[];
  missing: string[];
  defaults: { name: string; value: string; overridden: boolean }[];
  dotenv: { names: string[]; error: string | null } | null;
  restart_needed: boolean;
}
```

- [ ] **Step 3: Page** — `SecretsPage.tsx` sections in this order, each with an `h2` (class `page.big`):
  1. **Needs a value** (only if `missing.length`): one row per name — name, `TextField secret` labelled "Value", Save (disabled when empty). Save → `useSetSecret`.
  2. **Your values** (only if `secrets.length`): name, `••••••••`, Edit (opens an empty `TextField secret` "New value" + Save/Cancel), and Delete — label **Reset to default** when `overrides_default`.
  3. **Defaults** inside `<Collapsible summary={`Defaults from .env.example (${n})`}>` (closed by default, only if `defaults.length`): name, the value in clear (monospace) — when `overridden`, show the default struck through with an "overridden" tag and no Edit; otherwise Edit opens a `TextField secret` **pre-filled with the default value** + Save/Cancel; Save → `useSetSecret`.
  4. **Add a variable** form (name + `TextField secret` value), unchanged logic; `nameProblem(name, taken)` where `taken` = stored names.
  Keep the restart notice, the `.env` notice (pluralised), error notices and lead copy; update the lead to: "Settings and keys {id} needs. Eggie hands them to the app as environment variables when it starts. Values you save can't be shown again — only replaced. Defaults come from the project's .env.example." After a successful Save, clear the field and close the editor.

- [ ] **Step 4: Mock** — `handlers.ts`: entry gains `defaults: Map<string, string>`; the `secrets` scenario for recipe-box: defaults `APP_NAME=Recipe Box`, `LOG_LEVEL=info`, `MAIL_FROM=hello@recipe.box`; stored `STRIPE_KEY`; missing `OPENAI_API_KEY`; dotenv `["SMTP_PASSWORD","SMTP_USER"]`. GET returns the R3 shape (`overrides_default`/`overridden` computed). PUT rejects `""` with `secret_invalid_value`.

- [ ] **Step 5: Verify** — from `runtime/web`: `npm test && npm run typecheck && npm run build && npm run check-offline`. Start `npm run dev`, confirm it boots, stop it.
- [ ] **Step 6: Commit** — `git commit -m "Show defaults and visible-while-typing fields on the Secrets page"`.

---

### Task 5: Agent instructions and docs

**Files:** `runtime/instructions/eggie.md`, `runtime/eggie_api/CLAUDE.md` (Secrets entry), `docs/architecture.md` (the two secrets sentences), `runtime/web/CLAUDE.md` (secrets line).

- [ ] **Step 1:** Replace the secrets bullet in `runtime/instructions/eggie.md` with:

```markdown
- Settings and secrets: `.env.example` at the project root lists every variable the project
  needs — a secret as `NAME=` (empty), a non-secret setting as `NAME=default`. Eggie hands all of
  them to every service as environment variables; the user fills secrets and can change defaults
  on the project's **Secrets** page. Never write a secret value into any file, the compose file
  or a commit, and never create `.env`; if a tool writes one, move its non-secret keys into
  `.env.example`, then delete it. A random internal key (app secret, JWT/cookie secret, APP_KEY):
  generate it straight into Eggie with `openssl rand -hex 32 | eggie secret set NAME`. A key
  from an outside service (Stripe, OpenAI, mail): add `NAME=` and ask the user to fill it on the
  Secrets page, then restart. `eggie secret list` shows what is set and what still needs a value.
  `docker compose run` started by you gets none of these variables — run one-off commands in the
  running service with `docker compose exec`.
```

- [ ] **Step 2:** `runtime/eggie_api/CLAUDE.md` Secrets entry: rewrite to state the three sources and precedence (compose literal > stored > `.env.example` default), that defaults are read fresh at every compose call with a lenient parser, reserved names (`COMPOSE_`, `DOCKER_`, `LD_`, `PATH`, `HOME`), import storing only values that differ from defaults and keeping reserved lines in `.env`. Keep the existing sentences that are still true; keep it under ~12 lines.
- [ ] **Step 3:** `docs/architecture.md`: the "How a project runs" sentence names defaults from `.env.example` and that a compose literal wins. `runtime/web/CLAUDE.md`: the secrets line mentions the three sections.
- [ ] **Step 4:** Full Python suite → PASS. Commit — `git commit -m "Describe .env.example defaults for coding agents and in the docs"`.
