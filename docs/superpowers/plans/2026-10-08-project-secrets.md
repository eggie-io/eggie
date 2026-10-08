# Project secrets Implementation Plan

> **Superseded** by Revision 3 — see `docs/superpowers/specs/2026-10-08-project-secrets-design.md` and `2026-10-08-project-secrets-revision-3.md`. Do not execute.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Per-project secrets stored in the VM's `state.db`, handed to every compose service as environment variables on start, managed write-only from the console, the in-VM CLI and the API.

**Architecture:** Pure logic (validation, `.env.example`/compose reference scanning, `.env` parsing) in `runtime/eggie_api/core/secrets.py`; storage in `state.db` (migration v6); delivery by listing bare names in the generated `.eggie/overlay.yml` and passing values as the environment of `docker compose up`; routes in `routes/app.py`; CLI `eggie secret …`; console page `/p/:id/secrets`.

**Tech Stack:** Python 3.12 + FastAPI + sqlite (API), stdlib-only Python (CLI), React + TanStack Query + MSW + Vitest (console).

**Spec:** `docs/superpowers/specs/2026-10-08-project-secrets-design.md`

## Global Constraints

- Python 3.12+; run tests with `.venv/bin/python -m pytest` from the repo root. If `tmp_path` fails with a permission error, prefix `TMPDIR=$PWD/.tmp` (create it first).
- `from __future__ import annotations` at the top of every new Python module.
- No response of any secrets route, CLI output, job log or overlay ever contains a secret **value**.
- Name rule: `^[A-Za-z_][A-Za-z0-9_]*$`; reserved prefixes `COMPOSE_`, `DOCKER_` (checked case-insensitively).
- Size caps: value ≤ 65536 bytes UTF-8, no NUL; project total `sum(len(name)+len(value)+2)` in UTF-8 bytes ≤ 524288.
- Error body shape is always `{"error": {"code", "message"}}`. Codes: `secret_name_invalid`, `secret_name_reserved`, `secret_too_large`, `secrets_too_large`, `secret_invalid_value`, `dotenv_invalid`, `secret_not_found`, `dotenv_missing`, `dotenv_not_removed`.
- `runtime/cli/eggie.py` stays stdlib-only and imports neither `host/` nor `eggie_api`.
- `host/` is not touched. `API_VERSION` is not bumped.
- No comments that restate code; comments only for non-obvious reasons. No references to issues/tickets in comments.
- Tests follow the user's rules: only logic where a wrong result is plausible; no component tests; no mock-only tests.
- Commit after each task; messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. A secret set while a start is in flight (after values were read, before `mark_started`) must still show "restart needed" — pinned in Task 4 (`test_a_secret_set_during_a_start_still_needs_a_restart`).
2. A `.env` with an invalid line (no `=`, bad name) must store nothing and keep the file — pinned in Task 4 (`test_import_of_an_invalid_dotenv_stores_nothing_and_keeps_the_file`).
3. A compose file with services but no web service still gets the secret names on every service — pinned in Task 3 (`test_non_web_services_get_the_secret_names_too`).
4. A value containing newlines/quotes/`$` reaches compose byte-for-byte (env, not a file) — pinned in Task 3 (`test_values_reach_compose_up_through_env_only`).
5. CLI piped value with a trailing newline (`echo x | eggie secret set`) stores `x`, not `x\n` — pinned in Task 5 (`test_secret_set_reads_the_value_from_stdin_without_its_trailing_newline`).

---

### Task 1: Core secrets logic (`core/secrets.py`)

**Files:**
- Create: `runtime/eggie_api/core/secrets.py`
- Test: `tests/runtime/api/test_secrets.py`

**Interfaces:**
- Produces:
  - `class SecretError(ValueError)` with `.code: str`, `.message: str`
  - `check_name(name: str) -> None`
  - `check_value(value: str) -> None`
  - `check_total(values: dict[str, str]) -> None`
  - `expected(example_text: str | None, compose_text: str | None) -> set[str]`
  - `parse_dotenv(text: str) -> dict[str, str]` (validates every name and value; raises `SecretError`)

- [ ] **Step 1: Write the failing tests**

```python
# tests/runtime/api/test_secrets.py
import pytest

from eggie_api.core.secrets import (SecretError, check_name, check_total,
                                    check_value, expected, parse_dotenv)


@pytest.mark.parametrize("name", ["API_KEY", "_x", "a1"])
def test_ordinary_env_names_are_accepted(name):
    check_name(name)


@pytest.mark.parametrize("name", ["", "1KEY", "MY-KEY", "A B", "KÉY"])
def test_names_compose_cannot_pass_as_env_are_refused(name):
    with pytest.raises(SecretError) as e:
        check_name(name)
    assert e.value.code == "secret_name_invalid"


@pytest.mark.parametrize("name", ["COMPOSE_PROJECT_NAME", "DOCKER_HOST", "compose_file"])
def test_names_that_steer_compose_itself_are_reserved(name):
    with pytest.raises(SecretError) as e:
        check_name(name)
    assert e.value.code == "secret_name_reserved"


def test_a_value_over_64_kib_is_refused_but_64_kib_is_not():
    check_value("x" * 65536)
    with pytest.raises(SecretError) as e:
        check_value("x" * 65537)
    assert e.value.code == "secret_too_large"


def test_a_value_is_measured_in_utf8_bytes():
    with pytest.raises(SecretError):
        check_value("é" * 32769)


def test_a_nul_byte_cannot_travel_through_an_environment():
    with pytest.raises(SecretError) as e:
        check_value("a\x00b")
    assert e.value.code == "secret_invalid_value"


def test_the_project_total_is_capped():
    check_total({f"K{i}": "x" * 60000 for i in range(8)})
    with pytest.raises(SecretError) as e:
        check_total({f"K{i}": "x" * 60000 for i in range(9)})
    assert e.value.code == "secrets_too_large"


def test_expected_reads_env_example_keys_and_ignores_their_values():
    example = "# comment\n\nexport API_KEY=placeholder\nDB_URL = postgres://x\nnot a line\n"
    assert expected(example, None) == {"API_KEY", "DB_URL"}


def test_expected_counts_compose_refs_without_a_default():
    compose = (
        "services:\n"
        "  web:\n"
        "    environment:\n"
        "      A: ${A}\n"
        "      B: ${B:-fallback}\n"
        "      C: ${C-fallback}\n"
        "      D: ${D:?must be set}\n"
        "      E: $E\n"
        "      F: $${F}\n"
        "      G: ${G:+on}\n"
        "      H: $$$H\n"
        "    # I: ${I}\n"
    )
    assert expected(None, compose) == {"A", "D", "E", "H"}


def test_expected_with_nothing_to_read_is_empty():
    assert expected(None, None) == set()


def test_parse_dotenv_handles_quotes_export_and_inline_comments():
    text = (
        "# top\n"
        "export PLAIN=abc  # trailing\n"
        "SINGLE='a $b # not a comment'\n"
        'DOUBLE="line1\\nline2 \\"q\\" \\\\"\n'
        "EMPTY=\n"
        "HASH=a#b\n"
    )
    assert parse_dotenv(text) == {
        "PLAIN": "abc",
        "SINGLE": "a $b # not a comment",
        "DOUBLE": 'line1\nline2 "q" \\',
        "EMPTY": "",
        "HASH": "a#b",
    }


def test_parse_dotenv_reads_a_double_quoted_value_across_lines():
    text = 'KEY="-----BEGIN-----\nabc\n-----END-----"\nNEXT=1\n'
    assert parse_dotenv(text) == {"KEY": "-----BEGIN-----\nabc\n-----END-----",
                                  "NEXT": "1"}


def test_parse_dotenv_refuses_a_line_without_an_equals_sign():
    with pytest.raises(SecretError) as e:
        parse_dotenv("GOOD=1\njust words\n")
    assert e.value.code == "dotenv_invalid"
    assert "line 2" in e.value.message


def test_parse_dotenv_refuses_an_unclosed_quote():
    with pytest.raises(SecretError) as e:
        parse_dotenv('KEY="never closed\n')
    assert e.value.code == "dotenv_invalid"


def test_parse_dotenv_refuses_a_reserved_name():
    with pytest.raises(SecretError) as e:
        parse_dotenv("DOCKER_HOST=tcp://x\n")
    assert e.value.code == "secret_name_reserved"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/runtime/api/test_secrets.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'eggie_api.core.secrets'`

- [ ] **Step 3: Implement**

```python
# runtime/eggie_api/core/secrets.py
"""Project secrets: what a name and value may be, which names a project
expects, and reading a `.env` file. No storage, no FastAPI."""
from __future__ import annotations

import re

NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
# compose and the docker CLI read these from their own environment, and
# secrets are that environment.
RESERVED_PREFIXES = ("COMPOSE_", "DOCKER_")
MAX_VALUE_BYTES = 64 * 1024
MAX_PROJECT_BYTES = 512 * 1024

_DEFAULTED = {"-", ":-", "+", ":+"}
_BRACED = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(:?[-+?])?[^}]*\}")
_BARE = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)")
_ESCAPES = {"n": "\n", '"': '"', "\\": "\\"}


class SecretError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def check_name(name: str) -> None:
    if not NAME.fullmatch(name):
        raise SecretError("secret_name_invalid",
                          f"'{name}' can't be a secret name: use letters, digits "
                          "and underscores, not starting with a digit")
    if name.upper().startswith(RESERVED_PREFIXES):
        raise SecretError("secret_name_reserved",
                          f"'{name}' is reserved: names starting with COMPOSE_ "
                          "or DOCKER_ would change how Eggie runs the project")


def check_value(value: str) -> None:
    if "\x00" in value:
        raise SecretError("secret_invalid_value",
                          "a secret's value can't contain a NUL character")
    if len(value.encode("utf-8")) > MAX_VALUE_BYTES:
        raise SecretError("secret_too_large",
                          f"a secret's value can be at most {MAX_VALUE_BYTES // 1024} KB")


def check_total(values: dict[str, str]) -> None:
    total = sum(len(n.encode("utf-8")) + len(v.encode("utf-8")) + 2
                for n, v in values.items())
    if total > MAX_PROJECT_BYTES:
        raise SecretError("secrets_too_large",
                          f"a project's secrets can add up to at most "
                          f"{MAX_PROJECT_BYTES // 1024} KB")


def _example_keys(text: str) -> set[str]:
    keys = set()
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        line = line.removeprefix("export ").lstrip()
        key, sep, _ = line.partition("=")
        if sep and NAME.fullmatch(key.strip()):
            keys.add(key.strip())
    return keys


def _compose_refs(text: str) -> set[str]:
    refs = set()
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        # `$$` is compose's escape for a literal dollar sign.
        line = line.replace("$$", "")
        for match in _BRACED.finditer(line):
            if match.group(2) not in _DEFAULTED:
                refs.add(match.group(1))
        for match in _BARE.finditer(_BRACED.sub("", line)):
            refs.add(match.group(1))
    return refs


def expected(example_text: str | None, compose_text: str | None) -> set[str]:
    return ((_example_keys(example_text) if example_text else set())
            | (_compose_refs(compose_text) if compose_text else set()))


def _closing(body: str, quote: str) -> int:
    i = 0
    while i < len(body):
        if quote == '"' and body[i] == "\\":
            i += 2
            continue
        if body[i] == quote:
            return i
        i += 1
    return -1


def _unescape(body: str) -> str:
    return re.sub(r"\\(.)", lambda m: _ESCAPES.get(m.group(1), m.group(0)), body,
                  flags=re.DOTALL)


def parse_dotenv(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    lines = text.splitlines()
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
            raise SecretError("dotenv_invalid",
                              f".env line {number} isn't NAME=value")
        check_name(key)
        rest = rest.lstrip()
        if rest[:1] in ("'", '"'):
            quote, body = rest[0], rest[1:]
            end = _closing(body, quote)
            while end == -1:
                if i >= len(lines):
                    raise SecretError("dotenv_invalid",
                                      f".env line {number} opens a quote "
                                      "that is never closed")
                body += "\n" + lines[i]
                i += 1
                end = _closing(body, quote)
            value = _unescape(body[:end]) if quote == '"' else body[:end]
        else:
            value = re.split(r"\s+#", rest, maxsplit=1)[0].strip()
        check_value(value)
        values[key] = value
    return values
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/runtime/api/test_secrets.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add runtime/eggie_api/core/secrets.py tests/runtime/api/test_secrets.py
git commit -m "Add validation and parsing for project secrets"
```

---

### Task 2: Storage (migration v6 + `State` methods)

**Files:**
- Modify: `runtime/eggie_api/core/migrate.py` (add `_v6_secrets`, append to `MIGRATIONS`)
- Modify: `runtime/eggie_api/core/state.py` (new methods)
- Test: `tests/runtime/api/test_state.py` (append)

**Interfaces:**
- Produces on `State`:
  - `secret_names(project_id: str) -> list[dict]` → `[{"name": str, "updated_at": float}]` sorted by name
  - `secret_values(project_id: str) -> dict[str, str]`
  - `set_secrets(project_id: str, values: dict[str, str], at: float) -> None` — upserts all in one transaction and sets `projects.secrets_changed_at = at`
  - `delete_secret(project_id: str, name: str, at: float) -> bool` — False when absent (and then does not stamp)
  - `drop_secrets(project_id: str) -> None`
- Project rows gain the column `secrets_changed_at` (REAL, nullable).

- [ ] **Step 1: Write the failing tests** (append to `tests/runtime/api/test_state.py`; read the file's existing imports first and reuse its `State` import)

```python
def test_secrets_are_kept_per_project_and_never_listed_with_values(tmp_path):
    s = State(tmp_path / "state.db")
    s.add_project("a", "/p/a", "d")
    s.add_project("b", "/p/b", "d")
    s.set_secrets("a", {"KEY": "one", "OTHER": "two"}, at=10.0)
    s.set_secrets("b", {"KEY": "three"}, at=11.0)
    assert [r["name"] for r in s.secret_names("a")] == ["KEY", "OTHER"]
    assert all(set(r) == {"name", "updated_at"} for r in s.secret_names("a"))
    assert s.secret_values("a") == {"KEY": "one", "OTHER": "two"}
    assert s.secret_values("b") == {"KEY": "three"}


def test_setting_a_secret_again_replaces_it_and_stamps_the_change(tmp_path):
    s = State(tmp_path / "state.db")
    s.add_project("a", "/p/a", "d")
    s.set_secrets("a", {"KEY": "one"}, at=10.0)
    s.set_secrets("a", {"KEY": "two"}, at=20.0)
    assert s.secret_values("a") == {"KEY": "two"}
    assert s.get_project("a")["secrets_changed_at"] == 20.0


def test_deleting_an_absent_secret_reports_it_and_stamps_nothing(tmp_path):
    s = State(tmp_path / "state.db")
    s.add_project("a", "/p/a", "d")
    s.set_secrets("a", {"KEY": "one"}, at=10.0)
    assert s.delete_secret("a", "NOPE", at=20.0) is False
    assert s.get_project("a")["secrets_changed_at"] == 10.0
    assert s.delete_secret("a", "KEY", at=30.0) is True
    assert s.secret_values("a") == {}
    assert s.get_project("a")["secrets_changed_at"] == 30.0


def test_secrets_outlive_the_project_row_until_dropped(tmp_path):
    s = State(tmp_path / "state.db")
    s.add_project("a", "/p/a", "d")
    s.set_secrets("a", {"KEY": "one"}, at=10.0)
    s.remove_project("a")
    assert s.secret_values("a") == {"KEY": "one"}
    s.drop_secrets("a")
    assert s.secret_values("a") == {}
```

Also append to `tests/runtime/api/test_migrate.py`:

```python
def test_v6_adds_the_secrets_table_and_change_stamp(tmp_path):
    conn = connect(tmp_path)
    migrate.migrate(conn)
    assert "secrets" in tables(conn)
    assert "secrets_changed_at" in columns(conn, "projects")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/runtime/api/test_state.py tests/runtime/api/test_migrate.py -q`
Expected: FAIL — `AttributeError: 'State' object has no attribute 'set_secrets'`, and the v6 test fails on the missing table.

- [ ] **Step 3: Implement**

In `migrate.py`, after `_v5_github`:

```python
def _v6_secrets(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS secrets (
            project_id TEXT NOT NULL,
            name TEXT NOT NULL,
            value TEXT NOT NULL,
            updated_at REAL NOT NULL,
            PRIMARY KEY (project_id, name)
        )""")
    _add_column(conn, "projects", "secrets_changed_at", "REAL")
```

and append `_v6_secrets,` to `MIGRATIONS`.

In `state.py`, inside `class State` before `close`:

```python
    def secret_names(self, project_id) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(
                "SELECT name, updated_at FROM secrets WHERE project_id=? "
                "ORDER BY name", (project_id,))]

    def secret_values(self, project_id) -> dict[str, str]:
        with self._lock:
            return {r["name"]: r["value"] for r in self._conn.execute(
                "SELECT name, value FROM secrets WHERE project_id=?",
                (project_id,))}

    def set_secrets(self, project_id, values: dict[str, str], at: float) -> None:
        with self._lock:
            self._conn.executemany(
                "INSERT OR REPLACE INTO secrets(project_id, name, value, updated_at) "
                "VALUES (?,?,?,?)",
                [(project_id, name, value, at) for name, value in values.items()])
            self._conn.execute("UPDATE projects SET secrets_changed_at=? WHERE id=?",
                               (at, project_id))
            self._conn.commit()

    def delete_secret(self, project_id, name, at: float) -> bool:
        with self._lock:
            gone = self._conn.execute(
                "DELETE FROM secrets WHERE project_id=? AND name=?",
                (project_id, name)).rowcount > 0
            if gone:
                self._conn.execute(
                    "UPDATE projects SET secrets_changed_at=? WHERE id=?",
                    (at, project_id))
            self._conn.commit()
            return gone

    def drop_secrets(self, project_id) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM secrets WHERE project_id=?",
                               (project_id,))
            self._conn.commit()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/runtime/api/test_state.py tests/runtime/api/test_migrate.py -q`
Expected: PASS. (If an existing migrate test hardcodes the schema version as 5, update it to `migrate.SCHEMA_VERSION`.)

- [ ] **Step 5: Commit**

```bash
git add runtime/eggie_api/core/migrate.py runtime/eggie_api/core/state.py tests/runtime/api/test_state.py tests/runtime/api/test_migrate.py
git commit -m "Store project secrets in the state database"
```

---

### Task 3: Delivery through the overlay and the compose environment

**Files:**
- Modify: `runtime/eggie_api/core/overlay.py` (`build_overlay`)
- Modify: `runtime/eggie_api/core/project.py:43-45` (`overlay_yaml`)
- Modify: `runtime/eggie_api/core/lifecycle.py:30-62` (`_write_overlay`, `compose_up`)
- Test: `tests/runtime/api/test_overlay.py`, `tests/runtime/api/test_lifecycle.py` (append; update `FakeProvider.exec` in `test_lifecycle.py` to accept `env=None` and record it)

**Every** provider class in `test_lifecycle.py` overrides `exec(self, argv, *, root=False)` (`FailingProvider`, `OverlayFails` and the ones near lines 99–150); `compose_up` now always passes `env=`, so change each of those signatures to `exec(self, argv, *, root=False, env=None)` as well.

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `build_overlay(project_id: str, webs: list[WebSpec], domain: str, *, services: Sequence[str] = (), secret_names: Iterable[str] = ()) -> dict`
  - `overlay_yaml(project: Project, domain: str, *, services: Sequence[str] = (), secret_names: Iterable[str] = ()) -> str`
  - `compose_up(provider, project, directory, domain, *, on_phase=None, services: Sequence[str] = (), secrets: dict[str, str] | None = None)` — writes the names into the overlay and passes `env=secrets or None` to the `up` exec only.

- [ ] **Step 1: Write the failing tests**

Append to `tests/runtime/api/test_overlay.py`:

```python
def test_every_service_gets_the_secret_names_and_web_routing_is_kept():
    ov = build_overlay("p", [WebSpec("web", 80)], "d.io",
                       services=["web", "worker"], secret_names={"B", "A"})
    assert ov["services"]["web"]["environment"] == ["A", "B"]
    assert "traefik.enable" in ov["services"]["web"]["labels"]
    assert ov["services"]["worker"] == {"environment": ["A", "B"]}


def test_non_web_services_get_the_secret_names_too():
    ov = build_overlay("p", [], "d.io", services=["db", "worker"],
                       secret_names=["KEY"])
    assert ov["services"] == {"db": {"environment": ["KEY"]},
                              "worker": {"environment": ["KEY"]}}


def test_no_secrets_leaves_the_overlay_as_before():
    ov = build_overlay("p", [WebSpec("web", 80)], "d.io", services=["web", "db"])
    assert "environment" not in ov["services"]["web"]
    assert "db" not in ov["services"]
```

Append to `tests/runtime/api/test_lifecycle.py` (and change the file's `FakeProvider.exec` signature to `def exec(self, argv, *, root=False, env=None)` storing `self.envs.append(env)` next to `self.execs.append(argv)`, with `self.envs = []` in `__init__`; read the class first):

```python
def test_values_reach_compose_up_through_env_only(tmp_path):
    (tmp_path / "docker-compose.yml").write_text("services: {}\n")
    proj = Project(id="p", webs=[WebSpec("web", 80)])
    p = FakeProvider()
    value = 'multi\nline "quoted" $dollar'
    compose_up(p, proj, tmp_path, "d.io", services=["web", "db"],
               secrets={"TOKEN": value})
    ups = [(a, e) for a, e in zip(p.execs, p.envs) if a[-2:] == ["up", "-d"]]
    assert len(ups) == 1
    argv, env = ups[0]
    assert env == {"TOKEN": value}
    for other_argv, other_env in zip(p.execs, p.envs):
        assert all(value not in word and "dollar" not in word for word in other_argv)
        if other_argv is not argv:
            assert other_env is None


def test_the_overlay_written_names_the_secrets_but_holds_no_value(tmp_path):
    import base64
    import yaml
    (tmp_path / "docker-compose.yml").write_text("services: {}\n")
    proj = Project(id="p", webs=[WebSpec("web", 80)])
    p = FakeProvider()
    compose_up(p, proj, tmp_path, "d.io", services=["web", "db"],
               secrets={"TOKEN": "sekrit"})
    write = next(a for a in p.execs if "overlay.yml" in " ".join(a) and a[0] == "bash")
    encoded = write[-1].split("echo ", 1)[1].split(" ", 1)[0]
    text = base64.b64decode(encoded).decode()
    assert "sekrit" not in text
    assert yaml.safe_load(text)["services"]["db"] == {"environment": ["TOKEN"]}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/runtime/api/test_overlay.py tests/runtime/api/test_lifecycle.py -q`
Expected: FAIL — `TypeError: build_overlay() got an unexpected keyword argument 'services'`

- [ ] **Step 3: Implement**

`overlay.py`:

```python
from __future__ import annotations

from collections.abc import Iterable, Sequence

from .detect import WebSpec
from . import constants


def host_for(project_id: str, web: WebSpec, domain: str) -> str:
    if web.subdomain:
        return f"{web.subdomain}.{project_id}.{domain}"
    return f"{project_id}.{domain}"


def build_overlay(project_id: str, webs: list[WebSpec], domain: str, *,
                  services: Sequence[str] = (),
                  secret_names: Iterable[str] = ()) -> dict:
    overlay_services: dict = {}
    for web in webs:
        router = f"{project_id}-{web.service}"
        host = host_for(project_id, web, domain)
        overlay_services[web.service] = {
            "networks": ["default", constants.EDGE_NETWORK],
            "labels": {
                "traefik.enable": "true",
                f"traefik.http.routers.{router}.rule": f"Host(`{host}`)",
                f"traefik.http.services.{router}.loadbalancer.server.port":
                    str(web.port),
            },
        }
    names = sorted(secret_names)
    if names:
        # Bare names: compose takes each value from its own environment,
        # so no value is ever written into the project folder.
        for service in [*services, *overlay_services]:
            overlay_services.setdefault(service, {})["environment"] = names
    return {
        "services": overlay_services,
        "networks": {constants.EDGE_NETWORK: {"external": True}},
    }
```

`project.py` `overlay_yaml`:

```python
def overlay_yaml(project: Project, domain: str, *, services=(),
                 secret_names=()) -> str:
    overlay = build_overlay(project.id, project.webs, domain,
                            services=services, secret_names=secret_names)
    return yaml.safe_dump(overlay, sort_keys=False)
```

`lifecycle.py`:

```python
def _write_overlay(provider, project: Project, directory, domain: str, *,
                   services=(), secret_names=()):
    text = overlay_yaml(project, domain, services=services,
                        secret_names=secret_names)
    ...unchanged below...
```

and in `compose_up` add the keyword parameters `services=(), secrets: dict[str, str] | None = None`, pass `services=services, secret_names=list(secrets or {})` to `_write_overlay`, and run `up` as:

```python
    up = provider.exec(_compose_argv(directory), root=True,
                       env=dict(secrets) if secrets else None)
```

Extend the docstring with one sentence: "`secrets` reach compose only through the environment of `up`; the overlay names them."

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/runtime/api/test_overlay.py tests/runtime/api/test_lifecycle.py tests/runtime/api/test_project.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add runtime/eggie_api/core/overlay.py runtime/eggie_api/core/project.py runtime/eggie_api/core/lifecycle.py tests/runtime/api/test_overlay.py tests/runtime/api/test_lifecycle.py
git commit -m "Hand project secrets to compose through its environment"
```

---

### Task 4: API routes, start wiring, restart notice, purge

**Files:**
- Modify: `runtime/eggie_api/routes/app.py`
- Create: `tests/runtime/api/test_api_secrets.py`

**Interfaces:**
- Consumes: Task 1 (`SecretError`, `check_name`, `check_value`, `check_total`, `expected`, `parse_dotenv`), Task 2 (`State.secret_names/secret_values/set_secrets/delete_secret/drop_secrets`, column `secrets_changed_at`), Task 3 (`compose_up(..., services=, secrets=)`).
- Produces (wire contract used by Tasks 5 and 6):
  - `GET /projects/{id}/secrets` → `{"secrets": [{"name", "updated_at"}], "missing": [str], "dotenv": null | {"names": [str], "error": str | null}, "restart_needed": bool}`
  - `PUT /projects/{id}/secrets/{name}` body `{"value": str}` → 204
  - `DELETE /projects/{id}/secrets/{name}` → 204, or 404 `secret_not_found`
  - `POST /projects/{id}/secrets/import-dotenv` → `{"imported": [str]}`; 404 `dotenv_missing`; 400 on a parse/validation error; 409 `dotenv_not_removed`
  - Project payload gains `"restart_needed": bool`.
  - All mounted on `router` (both `/` and `/api`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/runtime/api/test_api_secrets.py
import threading

from tests.runtime.api.conftest import _create, _run_to_completion, _write_compose

COMPOSE_TWO = """
services:
  web:
    image: nginx
    ports: ["8080:80"]
    environment:
      API_KEY: ${API_KEY}
      MODE: ${MODE:-dev}
  worker:
    image: busybox
"""

SECRET = "s3cr3t-value-never-echoed"


def _project(env, pid="blog", compose=COMPOSE_TWO):
    _create(env, pid)
    _write_compose(env, pid, compose)
    return env.config.projects_root / pid


def _every_response_text(env, pid):
    paths = [f"/projects/{pid}/secrets", f"/projects/{pid}", "/projects"]
    return "".join(env.client.get(p).text for p in paths)


def test_a_set_secret_is_listed_by_name_and_its_value_never_comes_back(env):
    _project(env)
    put = env.client.put("/projects/blog/secrets/API_KEY", json={"value": SECRET})
    assert put.status_code == 204
    assert put.text == ""
    body = env.client.get("/projects/blog/secrets").json()
    assert [s["name"] for s in body["secrets"]] == ["API_KEY"]
    assert SECRET not in _every_response_text(env, "blog")


def test_missing_lists_expected_names_that_have_no_secret(env):
    folder = _project(env)
    (folder / ".env.example").write_text("API_KEY=\nSMTP_PASSWORD=changeme\n")
    env.client.put("/projects/blog/secrets/SMTP_PASSWORD", json={"value": "x"})
    assert env.client.get("/projects/blog/secrets").json()["missing"] == ["API_KEY"]


def test_an_invalid_or_reserved_name_is_a_400_with_its_code(env):
    _project(env)
    bad = env.client.put("/projects/blog/secrets/1BAD", json={"value": "x"})
    assert (bad.status_code, bad.json()["error"]["code"]) == (400, "secret_name_invalid")
    reserved = env.client.put("/projects/blog/secrets/DOCKER_HOST", json={"value": "x"})
    assert reserved.json()["error"]["code"] == "secret_name_reserved"


def test_the_project_total_counts_secrets_already_stored(env):
    _project(env)
    for i in range(8):
        assert env.client.put(f"/projects/blog/secrets/K{i}",
                              json={"value": "x" * 60000}).status_code == 204
    over = env.client.put("/projects/blog/secrets/K8", json={"value": "x" * 60000})
    assert over.json()["error"]["code"] == "secrets_too_large"


def test_deleting_an_unknown_secret_is_a_404(env):
    _project(env)
    resp = env.client.delete("/projects/blog/secrets/NOPE")
    assert (resp.status_code, resp.json()["error"]["code"]) == (404, "secret_not_found")


def test_start_hands_every_secret_to_compose_up_and_writes_no_value(env):
    folder = _project(env)
    env.client.put("/projects/blog/secrets/API_KEY", json={"value": SECRET})
    _run_to_completion(env, env.client.post("/projects/blog/up"))
    ups = [e for a, e in zip(env.runner.calls, env.runner.envs) if a[-2:] == ["up", "-d"]]
    assert ups == [{"API_KEY": SECRET}]
    assert all(SECRET not in word for argv in env.runner.calls for word in argv)
    assert SECRET not in "".join(p.read_text(errors="replace")
                                 for p in folder.rglob("*") if p.is_file())


def test_restart_needed_follows_changes_after_the_last_start(env):
    _project(env)
    _run_to_completion(env, env.client.post("/projects/blog/up"))
    assert env.client.get("/projects/blog").json()["restart_needed"] is False
    env.client.put("/projects/blog/secrets/API_KEY", json={"value": "v"})
    assert env.client.get("/projects/blog").json()["restart_needed"] is True
    assert env.client.get("/projects/blog/secrets").json()["restart_needed"] is True
    _run_to_completion(env, env.client.post("/projects/blog/restart"))
    assert env.client.get("/projects/blog").json()["restart_needed"] is False


def test_a_stopped_project_never_needs_a_restart(env):
    _project(env)
    env.client.put("/projects/blog/secrets/API_KEY", json={"value": "v"})
    assert env.client.get("/projects/blog").json()["restart_needed"] is False


def test_a_secret_set_during_a_start_still_needs_a_restart(env):
    _project(env)
    env.runner.up_gate = threading.Event()
    started = env.client.post("/projects/blog/up")
    # compose up is now blocked: the values it got were read before this set.
    env.client.put("/projects/blog/secrets/API_KEY", json={"value": "late"})
    env.runner.up_gate.set()
    _run_to_completion(env, started)
    assert env.client.get("/projects/blog").json()["restart_needed"] is True


def test_import_moves_dotenv_values_into_secrets_and_removes_the_file(env):
    folder = _project(env)
    (folder / ".env").write_text('API_KEY=from-file\nexport PEM="a\\nb"\n')
    listed = env.client.get("/projects/blog/secrets").json()
    assert listed["dotenv"] == {"names": ["API_KEY", "PEM"], "error": None}
    env.client.put("/projects/blog/secrets/API_KEY", json={"value": "old"})
    resp = env.client.post("/projects/blog/secrets/import-dotenv")
    assert resp.json() == {"imported": ["API_KEY", "PEM"]}
    assert not (folder / ".env").exists()
    assert env.state.secret_values("blog") == {"API_KEY": "from-file", "PEM": "a\nb"}
    assert env.client.get("/projects/blog/secrets").json()["dotenv"] is None


def test_import_of_an_invalid_dotenv_stores_nothing_and_keeps_the_file(env):
    folder = _project(env)
    (folder / ".env").write_text("GOOD=1\nnot a pair\n")
    listed = env.client.get("/projects/blog/secrets").json()
    assert listed["dotenv"]["names"] == []
    assert "line 2" in listed["dotenv"]["error"]
    resp = env.client.post("/projects/blog/secrets/import-dotenv")
    assert (resp.status_code, resp.json()["error"]["code"]) == (400, "dotenv_invalid")
    assert (folder / ".env").exists()
    assert env.state.secret_values("blog") == {}


def test_import_without_a_dotenv_is_a_404(env):
    _project(env)
    resp = env.client.post("/projects/blog/secrets/import-dotenv")
    assert resp.json()["error"]["code"] == "dotenv_missing"


def test_purge_drops_secrets_and_plain_delete_keeps_them(env):
    _project(env, "kept")
    env.client.put("/projects/kept/secrets/A", json={"value": "1"})
    env.client.delete("/projects/kept")
    assert env.state.secret_values("kept") == {"A": "1"}

    _project(env, "gone")
    env.client.put("/projects/gone/secrets/A", json={"value": "1"})
    env.client.delete("/projects/gone?purge=true")
    assert env.state.secret_values("gone") == {}


def test_secrets_routes_are_served_to_both_the_cli_and_the_console(env):
    from tests.runtime.api.route_sweep import every_route
    paths = {getattr(r, "path", None) for r in every_route(env.app)}
    for path in ("/projects/{project_id}/secrets",
                 "/projects/{project_id}/secrets/{name}",
                 "/projects/{project_id}/secrets/import-dotenv"):
        assert path in paths and "/api" + path in paths, path
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/runtime/api/test_api_secrets.py -q`
Expected: FAIL — 404/405 on `/projects/blog/secrets`.

- [ ] **Step 3: Implement in `routes/app.py`**

1. Imports: `from ..core import secrets as secret_rules` (the module name `secrets` clashes with the stdlib `secrets` already imported at the top of `app.py`) and `from ..core.secrets import SecretError`.
2. Model next to the others:

```python
class SecretValue(BaseModel):
    value: str
```

3. Exception handler next to `_upload_error`:

```python
    @app.exception_handler(SecretError)
    async def _secret_error(_request, exc: SecretError):
        return _body(exc.code, exc.message, 400)
```

4. Helpers inside `create_app`, after `payload` is defined (place `restart_needed` *before* `payload` since `payload` calls it):

```python
    def restart_needed(row: dict) -> bool:
        changed = row.get("secrets_changed_at")
        started = row.get("last_started_at")
        return (row["status"] == STARTED_OK and changed is not None
                and (started is None or changed > started))

    def read_text(path: Path) -> str | None:
        try:
            return path.read_text()
        except (OSError, UnicodeDecodeError):
            return None
```

5. In `payload`'s returned dict add `"restart_needed": restart_needed(row),`.

6. Routes (put them after `delete_file`):

```python
    @router.get("/projects/{project_id}/secrets")
    def list_secrets(project_id: str) -> dict:
        row = require_row(project_id)
        d = project_dir(project_id)
        names = state.secret_names(project_id)
        missing = secret_rules.expected(read_text(d / ".env.example"),
                                        read_text(d / constants.COMPOSE_FILE))
        dotenv = None
        text = read_text(d / ".env")
        if text is not None:
            try:
                dotenv = {"names": sorted(secret_rules.parse_dotenv(text)),
                          "error": None}
            except SecretError as e:
                dotenv = {"names": [], "error": e.message}
        return {"secrets": names,
                "missing": sorted(missing - {s["name"] for s in names}),
                "dotenv": dotenv,
                "restart_needed": restart_needed(row)}

    # No project lock: one sqlite statement, and a start in flight is caught
    # by restart_needed because the start is stamped before it reads values.
    @router.put("/projects/{project_id}/secrets/{name}", status_code=204)
    def put_secret(project_id: str, name: str, body: SecretValue) -> Response:
        require_row(project_id)
        secret_rules.check_name(name)
        secret_rules.check_value(body.value)
        secret_rules.check_total({**state.secret_values(project_id),
                                  name: body.value})
        state.set_secrets(project_id, {name: body.value}, time.time())
        return Response(status_code=204)

    @router.delete("/projects/{project_id}/secrets/{name}", status_code=204)
    def delete_secret(project_id: str, name: str) -> Response:
        require_row(project_id)
        if not state.delete_secret(project_id, name, time.time()):
            raise ApiError("secret_not_found",
                           f"project '{project_id}' has no secret '{name}'", 404)
        return Response(status_code=204)

    @router.post("/projects/{project_id}/secrets/import-dotenv")
    def import_dotenv(project_id: str) -> dict:
        require_row(project_id)
        path = project_dir(project_id) / ".env"
        with locks.held(project_id):
            text = read_text(path)
            if text is None:
                raise ApiError("dotenv_missing",
                               f"project '{project_id}' has no .env file", 404)
            values = secret_rules.parse_dotenv(text)
            secret_rules.check_total({**state.secret_values(project_id), **values})
            if values:
                state.set_secrets(project_id, values, time.time())
            try:
                path.unlink()
            except PermissionError:
                removed = lifecycle.remove_tree_as_root(runner, path)
                if not removed.ok:
                    raise ApiError("dotenv_not_removed",
                                   "the values are saved as secrets, but Eggie "
                                   "couldn't remove the .env file; delete it "
                                   "from the Files page", 409) from None
        return {"imported": sorted(values)}
```

7. In `delete_project`, inside `if purge:` next to `uploads.drop_project(project_id)`, add `state.drop_secrets(project_id)`.

8. In `start_work`, compute services next to `name = compose_name_of(project_id)`:

```python
        services = parse_yaml(project_dir(project_id) / constants.COMPOSE_FILE).get("services")
        services = list(services) if isinstance(services, dict) else []
```

Inside `work`, replace the `compose_up` call and `mark_started` with:

```python
                # Stamped before the values are read, so a secret changed while
                # compose runs still shows as needing a restart.
                began = time.time()
                status, detail = lifecycle.compose_up(
                    runner, project, directory, domain, on_phase=write.phase,
                    services=services, secrets=state.secret_values(project_id))
                diagnosis = None
                if status == STARTED_OK:
                    state.mark_started(project_id, began)
```

- [ ] **Step 4: Run tests to verify they pass, then the whole API suite**

Run: `.venv/bin/python -m pytest tests/runtime/api -q`
Expected: PASS (auth sweeps in `test_api_auth.py` / `test_api_browser_auth.py` must also pass with the new routes; if a sweep keeps an explicit allowlist of routes, add the four new ones there).

- [ ] **Step 5: Commit**

```bash
git add runtime/eggie_api/routes/app.py tests/runtime/api/test_api_secrets.py
git commit -m "Serve project secrets write-only and apply them on start"
```

---

### Task 5: In-VM CLI `eggie secret set|list|rm`

**Files:**
- Modify: `runtime/cli/eggie.py`
- Modify: `tests/runtime/cli/conftest.py` (`Guest.run` gains `stdin`)
- Create: `tests/runtime/cli/test_secret.py`

**Interfaces:**
- Consumes: Task 4 wire contract.
- Produces: `ApiClient.secrets(project_id) -> dict`, `ApiClient.set_secret(project_id, name, value) -> None`, `ApiClient.delete_secret(project_id, name) -> None`; `Env.stdin: TextIO`, `Env.getpass: Callable[[str], str]`.

- [ ] **Step 1: Extend the test harness and write failing tests**

In `tests/runtime/cli/conftest.py`, change `Guest.run`:

```python
    def run(self, *argv, cwd: Path, stdin: str | io.StringIO = "",
            getpass=None) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        stream = stdin if isinstance(stdin, io.StringIO) else io.StringIO(stdin)
        env = cli.Env(root=self.root, cwd=cwd, client=self._api_client,
                      gid=os.getgid, git=self._git, out=out, err=err,
                      stdin=stream,
                      getpass=getpass or (lambda _prompt: pytest.fail("no TTY here")))
        code = cli.main(list(argv), env)
        return code, out.getvalue(), err.getvalue()
```

```python
# tests/runtime/cli/test_secret.py
import io

import pytest

from tests.runtime.api.conftest import COMPOSE_ONE_WEB
from tests.runtime.cli.loader import load

cli = load()


def _project(guest, name="blog"):
    folder = guest.root / name
    folder.mkdir()
    (folder / "docker-compose.yml").write_text(COMPOSE_ONE_WEB)
    guest.run("up", cwd=folder)
    return folder


def _values(guest, pid="blog"):
    return guest._client.app.state.state.secret_values(pid)


def test_secret_set_reads_the_value_from_stdin_without_its_trailing_newline(guest):
    folder = _project(guest)
    code, out, err = guest.run("secret", "set", "API_KEY", cwd=folder, stdin="abc\n")
    assert (code, err) == (0, "")
    assert _values(guest) == {"API_KEY": "abc"}
    assert "abc" not in out


def test_secret_set_keeps_inner_newlines_of_a_piped_value(guest):
    folder = _project(guest)
    guest.run("secret", "set", "PEM", cwd=folder, stdin="a\nb\n")
    assert _values(guest) == {"PEM": "a\nb"}


def test_secret_set_on_a_terminal_asks_without_echo(guest):
    folder = _project(guest)

    class Tty(io.StringIO):
        def isatty(self):
            return True

    prompts = []
    code, out, _ = guest.run("secret", "set", "API_KEY", cwd=folder, stdin=Tty(),
                             getpass=lambda p: prompts.append(p) or "typed")
    assert code == 0
    assert prompts and "API_KEY" in prompts[0]
    assert _values(guest) == {"API_KEY": "typed"}
    assert "typed" not in out


def test_secret_set_refuses_an_empty_value(guest):
    folder = _project(guest)
    code, _, err = guest.run("secret", "set", "API_KEY", cwd=folder, stdin="")
    assert code == 1
    assert "nothing was saved" in err
    assert _values(guest) == {}


def test_secret_set_never_takes_the_value_as_an_argument(guest):
    folder = _project(guest)
    with pytest.raises(SystemExit):
        guest.run("secret", "set", "API_KEY", "leaked", cwd=folder)


def test_secret_set_reports_the_apis_validation_message(guest):
    folder = _project(guest)
    code, _, err = guest.run("secret", "set", "DOCKER_HOST", cwd=folder, stdin="x")
    assert code == 1
    assert "reserved" in err


def test_secret_list_shows_names_and_missing_but_no_values(guest):
    folder = _project(guest)
    (folder / ".env.example").write_text("API_KEY=\nOTHER=\n")
    guest.run("secret", "set", "API_KEY", cwd=folder, stdin="hidden-value")
    code, out, _ = guest.run("secret", "list", cwd=folder)
    assert code == 0
    assert "API_KEY" in out
    assert "OTHER" in out and "missing" in out.lower()
    assert "hidden-value" not in out


def test_secret_set_on_a_running_project_says_how_to_apply_it(guest):
    folder = _project(guest)
    _, out, _ = guest.run("secret", "set", "API_KEY", cwd=folder, stdin="v")
    assert "eggie up" in out


def test_secret_rm_removes_it_and_an_unknown_name_fails(guest):
    folder = _project(guest)
    guest.run("secret", "set", "API_KEY", cwd=folder, stdin="v")
    assert guest.run("secret", "rm", "API_KEY", cwd=folder)[0] == 0
    assert _values(guest) == {}
    code, _, err = guest.run("secret", "rm", "API_KEY", cwd=folder)
    assert code == 1 and "API_KEY" in err
```

`guest._client` is the FastAPI `TestClient`; `guest._client.app.state.state` is the API's `State`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/runtime/cli/test_secret.py -q`
Expected: FAIL — `TypeError: Env.__init__() got an unexpected keyword argument 'stdin'`

- [ ] **Step 3: Implement in `runtime/cli/eggie.py`**

1. `import getpass` with the other imports.
2. `ApiClient` methods after `logs`:

```python
    def secrets(self, project_id: str) -> dict:
        return self._call("GET", f"/projects/{project_id}/secrets")

    def set_secret(self, project_id: str, name: str, value: str) -> None:
        self._call("PUT", f"/projects/{project_id}/secrets/"
                          f"{urllib.parse.quote(name, safe='')}", {"value": value})

    def delete_secret(self, project_id: str, name: str) -> None:
        self._call("DELETE", f"/projects/{project_id}/secrets/"
                             f"{urllib.parse.quote(name, safe='')}")
```

3. `Env` gains, after `err`:

```python
    stdin: TextIO = field(default_factory=lambda: sys.stdin)
    getpass: Callable[[str], str] = getpass.getpass
```

4. Commands after `cmd_down`:

```python
_APPLY_HINT = "Run `eggie up` to apply it."


def cmd_secret_set(env: Env, name: str) -> None:
    project_id = _require_project(env)
    # Never from argv: that ends up in shell history and `ps`.
    if env.stdin.isatty():
        value = env.getpass(f"Value for {name} (not shown): ")
    else:
        value = env.stdin.read().removesuffix("\n")
    if value == "":
        raise EggieError(f"No value given for {name}; nothing was saved.")
    client = env.client()
    client.set_secret(project_id, name, value)
    print(f"Saved {name} for {project_id}.", file=env.out)
    if client.secrets(project_id).get("restart_needed"):
        print(_APPLY_HINT, file=env.out)


def cmd_secret_list(env: Env) -> None:
    project_id = _require_project(env)
    data = env.client().secrets(project_id)
    names = [s["name"] for s in data.get("secrets", [])]
    for name in names:
        print(name, file=env.out)
    missing = data.get("missing") or []
    if missing:
        print("Missing (no value yet): " + ", ".join(missing), file=env.out)
    if not names and not missing:
        print(f"{project_id} has no secrets.", file=env.out)
    if data.get("dotenv"):
        print("This project has a .env file; move it into secrets on the "
              "project's Secrets page in Eggie.", file=env.out)
    if data.get("restart_needed"):
        print("Run `eggie up` to apply the changes.", file=env.out)


def cmd_secret_rm(env: Env, name: str) -> None:
    project_id = _require_project(env)
    env.client().delete_secret(project_id, name)
    print(f"Removed {name} from {project_id}.", file=env.out)
```

5. `_parser`, before `return parser`:

```python
    secret = sub.add_parser("secret", help="keys and passwords this project's "
                                           "containers get as environment variables")
    secret_sub = secret.add_subparsers(dest="secret_command", required=True,
                                       metavar="<action>")
    secret_set = secret_sub.add_parser(
        "set", help="save a secret; the value is read from the terminal or stdin")
    secret_set.add_argument("name")
    secret_sub.add_parser("list", help="show secret names and the ones still missing")
    secret_rm = secret_sub.add_parser("rm", help="remove a secret")
    secret_rm.add_argument("name")
```

6. `main`'s `commands` dict:

```python
        "secret": lambda: {
            "set": lambda: cmd_secret_set(env, args.name),
            "list": lambda: cmd_secret_list(env),
            "rm": lambda: cmd_secret_rm(env, args.name),
        }[args.secret_command](),
```

`ApiError` is already an `EggieError` subclass, so `main` prints the API's message and exits 1.

- [ ] **Step 4: Run tests to verify they pass, then the whole CLI suite and constants test**

Run: `.venv/bin/python -m pytest tests/runtime/cli tests/test_constants_agree.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add runtime/cli/eggie.py tests/runtime/cli/conftest.py tests/runtime/cli/test_secret.py
git commit -m "Add eggie secret set, list and rm to the in-VM CLI"
```

---

### Task 6: Console Secrets page

**Files:**
- Modify: `runtime/web/apps/console/src/api/client.ts` (`put`)
- Modify: `runtime/web/apps/console/src/projects/types.ts` (`restart_needed`, `SecretsView`)
- Modify: `runtime/web/apps/console/src/projects/queries.ts` (secrets hooks)
- Create: `runtime/web/apps/console/src/projects/secrets.ts` + `secrets.test.ts`
- Create: `runtime/web/apps/console/src/screens/secrets/SecretsPage.tsx` + `SecretsPage.module.css`
- Modify: `runtime/web/apps/console/src/App.tsx` (route), `screens/project/Tiles.tsx` (tile), `screens/icons.tsx` (`KEY`), `screens/project/ProjectPage.tsx` (notice)
- Modify: `runtime/web/packages/ui/src/components/TextField.tsx` (`type` prop)
- Modify: `runtime/web/apps/console/src/mocks/handlers.ts` (`restart_needed`, secrets handlers, `secrets` scenario)

**Interfaces:**
- Consumes: Task 4 wire contract.
- Produces: `nameProblem(name: string, taken: string[]): string | null`; hooks `useSecrets(id)`, `useSetSecret(id)`, `useDeleteSecret(id)`, `useImportDotenv(id)`.

- [ ] **Step 1: Write the failing Vitest test**

```ts
// runtime/web/apps/console/src/projects/secrets.test.ts
import { describe, expect, it } from "vitest";
import { nameProblem } from "./secrets";

describe("nameProblem", () => {
  it("accepts an ordinary env name", () => {
    expect(nameProblem("OPENAI_API_KEY", [])).toBeNull();
  });
  it("asks for a name when empty", () => {
    expect(nameProblem("  ", [])).toMatch(/name/i);
  });
  it.each(["1KEY", "MY-KEY", "A B"])("refuses %s like the API does", (name) => {
    expect(nameProblem(name, [])).toMatch(/letters, digits/);
  });
  it.each(["COMPOSE_FILE", "docker_host"])("refuses the reserved %s", (name) => {
    expect(nameProblem(name, [])).toMatch(/reserved/);
  });
  it("points at Edit for a name that already exists", () => {
    expect(nameProblem("API_KEY", ["API_KEY"])).toMatch(/Edit/);
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run (from `runtime/web`): `npx vitest run apps/console/src/projects/secrets.test.ts`
Expected: FAIL — cannot resolve `./secrets`.

- [ ] **Step 3: Implement the pure module**

```ts
// runtime/web/apps/console/src/projects/secrets.ts
// Mirrors the API's check_name so a bad name is caught before the round trip.
const NAME = /^[A-Za-z_][A-Za-z0-9_]*$/;
const RESERVED = /^(COMPOSE_|DOCKER_)/i;

export function nameProblem(raw: string, taken: string[]): string | null {
  const name = raw.trim();
  if (name === "") return "Give it a name, like OPENAI_API_KEY.";
  if (!NAME.test(name)) return "Use letters, digits and underscores, not starting with a digit.";
  if (RESERVED.test(name)) return "Names starting with COMPOSE_ or DOCKER_ are reserved.";
  if (taken.includes(name)) return `There's already a secret called ${name} — use Edit to change it.`;
  return null;
}
```

Run the test again — Expected: PASS.

- [ ] **Step 4: API client, types, queries**

`api/client.ts`: add to `Api`:

```ts
  put<T>(path: string, body: unknown): Promise<T>;
```

and to the returned object:

```ts
    put: (path, body) => request("PUT", path, { json: body }),
```

`projects/types.ts`: add `restart_needed: boolean;` to `Project`, and:

```ts
export interface SecretsView {
  secrets: { name: string; updated_at: number }[];
  missing: string[];
  dotenv: { names: string[]; error: string | null } | null;
  restart_needed: boolean;
}
```

`projects/queries.ts` (import `SecretsView`):

```ts
const SECRETS = (id: string) => ["projects", "secrets", id] as const;

function secretPath(id: string, name: string): string {
  return `${projectPath(id)}/secrets/${encodeURIComponent(name)}`;
}

export function useSecrets(id: string) {
  return useQuery({ queryKey: SECRETS(id), queryFn: () => api.get<SecretsView>(`${projectPath(id)}/secrets`) });
}

export function useSetSecret(id: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ name, value }: { name: string; value: string }) => api.put<void>(secretPath(id, name), { value }),
    onSettled: () => client.invalidateQueries({ queryKey: ALL }),
  });
}

export function useDeleteSecret(id: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => api.del<void>(secretPath(id, name)),
    onSettled: () => client.invalidateQueries({ queryKey: ALL }),
  });
}

export function useImportDotenv(id: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<{ imported: string[] }>(`${projectPath(id)}/secrets/import-dotenv`),
    onSettled: () => client.invalidateQueries({ queryKey: ALL }),
  });
}
```

`packages/ui/src/components/TextField.tsx`: add prop `type = "text"` typed `type?: "text" | "password"` and pass `type={type}` to `<input>`.

- [ ] **Step 5: The page**

`screens/icons.tsx`:

```ts
export const KEY = (
  <svg {...svg}><circle cx="6" cy="9" r="3" {...line} /><path d="M9 9h6.5M13 9v2.5M15.5 9v2" {...line} /></svg>
);
```

`screens/secrets/SecretsPage.module.css`:

```css
.page { display: flex; flex-direction: column; gap: 16px; max-width: 720px; }
.rows { display: flex; flex-direction: column; gap: 8px; }
.row { display: flex; align-items: flex-end; gap: 8px; flex-wrap: wrap; }
.name { font-family: var(--font-mono, monospace); min-width: 200px; }
.masked { color: var(--ink-3, #888); letter-spacing: 2px; }
.missing { color: var(--warn, #b45309); font-size: 13px; }
.grow { flex: 1; min-width: 200px; }
```

(Read `packages/ui/src/tokens` or `ProjectPage.module.css` first and replace the fallback variables with the real token names used there.)

`screens/secrets/SecretsPage.tsx`:

```tsx
import { useState } from "react";
import { Link, useParams } from "react-router";
import { Button, Notice, TextField } from "@eggie/ui";
import { actionError } from "../../projects/copy";
import { useDeleteSecret, useImportDotenv, useLifecycle, useSecrets, useSetSecret } from "../../projects/queries";
import { nameProblem } from "../../projects/secrets";
import page from "../project/ProjectPage.module.css";
import s from "./SecretsPage.module.css";

function ValueRow({ id, name, missing }: { id: string; name: string; missing: boolean }) {
  const save = useSetSecret(id);
  const remove = useDeleteSecret(id);
  const [editing, setEditing] = useState(missing);
  const [value, setValue] = useState("");
  const submit = () =>
    save.mutate({ name, value }, { onSuccess: () => { setValue(""); setEditing(missing); } });
  return (
    <div className={s.row}>
      <span className={s.name}>{name}</span>
      {editing ? (
        <>
          <div className={s.grow}>
            <TextField label={missing ? "Needs a value" : "New value"} type="password" value={value} onChange={setValue} />
          </div>
          <Button variant="primary" disabled={value === "" || save.isPending} onClick={submit}>Save</Button>
          {!missing && <Button onClick={() => { setValue(""); setEditing(false); }}>Cancel</Button>}
        </>
      ) : (
        <>
          <span className={`${s.masked} ${s.grow}`}>••••••••</span>
          <Button onClick={() => setEditing(true)}>Edit</Button>
          <Button variant="danger" disabled={remove.isPending} onClick={() => remove.mutate(name)}>Delete</Button>
        </>
      )}
      {missing && <span className={s.missing}>missing</span>}
      {(save.error || remove.error) && <Notice>{actionError(save.error ?? remove.error)}</Notice>}
    </div>
  );
}

export function SecretsPage() {
  const { id = "" } = useParams();
  const query = useSecrets(id);
  const add = useSetSecret(id);
  const move = useImportDotenv(id);
  const lifecycle = useLifecycle(id);
  const [name, setName] = useState("");
  const [value, setValue] = useState("");
  const [tried, setTried] = useState(false);

  const back = <Link to={`/p/${encodeURIComponent(id)}`} className={page.back}>‹ {id}</Link>;
  if (!query.data) {
    return (
      <section className={s.page}>
        {back}
        {query.isError ? <Notice>{actionError(query.error)}</Notice> : <p className={page.muted}>Opening the spice drawer…</p>}
      </section>
    );
  }
  const data = query.data;
  const taken = data.secrets.map((x) => x.name);
  const problem = nameProblem(name, taken);

  return (
    <section className={s.page}>
      {back}
      <h1 className={page.name}>Secrets</h1>
      <p className={page.lead}>
        Keys and passwords {id} needs. Eggie hands them to the app as environment variables when it starts; they never
        go into the project's files. A saved value can't be shown again — only replaced.
      </p>
      {data.restart_needed && (
        <Notice>
          Secrets changed — restart {id} to apply them.{" "}
          <Button disabled={lifecycle.isPending} onClick={() => lifecycle.mutate("restart")}>Restart</Button>
        </Notice>
      )}
      {data.dotenv && (data.dotenv.error ? (
        <Notice>This project has a .env file Eggie can't read: {data.dotenv.error}</Notice>
      ) : (
        <Notice icon="folder">
          This project has a .env file with {data.dotenv.names.length} values. Move them into secrets so they stay out
          of the project's files?{" "}
          <Button variant="primary" disabled={move.isPending} onClick={() => move.mutate()}>Move into secrets</Button>
        </Notice>
      ))}
      {move.error && <Notice>{actionError(move.error)}</Notice>}
      <div className={s.rows}>
        {data.missing.map((n) => <ValueRow key={`m-${n}`} id={id} name={n} missing />)}
        {data.secrets.map((x) => <ValueRow key={`s-${x.name}`} id={id} name={x.name} missing={false} />)}
      </div>
      <h2 className={page.big}>Add a secret</h2>
      <div className={s.row}>
        <div className={s.grow}>
          <TextField label="Name" value={name} onChange={setName} placeholder="OPENAI_API_KEY" error={tried && problem ? problem : undefined} />
        </div>
        <div className={s.grow}>
          <TextField label="Value" type="password" value={value} onChange={setValue} />
        </div>
        <Button
          variant="primary"
          disabled={add.isPending || value === ""}
          onClick={() => {
            setTried(true);
            if (problem) return;
            add.mutate({ name: name.trim(), value }, { onSuccess: () => { setName(""); setValue(""); setTried(false); } });
          }}
        >
          Add
        </Button>
      </div>
      {add.error && <Notice>{actionError(add.error)}</Notice>}
    </section>
  );
}
```

Check `Notice` accepts element children (it takes `ReactNode` — yes) and that `page.muted`, `page.big`, `page.lead`, `page.name`, `page.back` exist in `ProjectPage.module.css` (they are used by `ProjectPage.tsx`).

`App.tsx`: import `SecretsPage` and add `<Route path="/p/:id/secrets" element={<SecretsPage />} />` after the files route.

`Tiles.tsx`: import `KEY`; add after the Files link:

```tsx
      <Link to={`/p/${encodeURIComponent(id)}/secrets`} className={s.tile}>{KEY}Secrets</Link>
```

`ProjectPage.tsx`: in the `running` case, after `{failed}`:

```tsx
          {project.restart_needed && <Notice>Secrets changed — press Restart to apply them.</Notice>}
```

- [ ] **Step 6: Mocks**

In `mocks/handlers.ts`:
- `project()` default gets `restart_needed: false`.
- Add `"secrets"` to `SCENARIOS`.
- Inside `handlersFor`, a store and handlers:

```ts
  const secrets = new Map<string, { names: Map<string, number>; missing: string[]; dotenv: string[] | null }>();
  const secretsOf = (id: string) => {
    let entry = secrets.get(id);
    if (!entry) {
      entry = scenario === "secrets" && id === "recipe-box"
        ? { names: new Map([["STRIPE_KEY", nowSec()]]), missing: ["OPENAI_API_KEY"], dotenv: ["SMTP_PASSWORD", "SMTP_USER"] }
        : { names: new Map(), missing: [], dotenv: null };
      secrets.set(id, entry);
    }
    return entry;
  };
  const touched = (target: Project) => {
    if (target.status === "started_ok") target.restart_needed = true;
  };
```

and in the returned handler list:

```ts
    http.get("/api/projects/:id/secrets", ({ params }) => {
      const target = projects.get(String(params.id));
      if (!target) return notFound(String(params.id));
      const entry = secretsOf(target.id);
      return HttpResponse.json({
        secrets: [...entry.names].sort().map(([name, updated_at]) => ({ name, updated_at })),
        missing: entry.missing.filter((n) => !entry.names.has(n)),
        dotenv: entry.dotenv && { names: entry.dotenv, error: null },
        restart_needed: target.restart_needed,
      });
    }),
    http.put("/api/projects/:id/secrets/:name", ({ params }) => {
      const target = projects.get(String(params.id));
      if (!target) return notFound(String(params.id));
      const name = String(params.name);
      if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(name)) return refuse("secret_name_invalid", `'${name}' can't be a secret name`, 400);
      secretsOf(target.id).names.set(name, nowSec());
      touched(target);
      return new HttpResponse(null, { status: 204 });
    }),
    http.delete("/api/projects/:id/secrets/:name", ({ params }) => {
      const target = projects.get(String(params.id));
      if (!target) return notFound(String(params.id));
      if (!secretsOf(target.id).names.delete(String(params.name))) return refuse("secret_not_found", "no such secret", 404);
      touched(target);
      return new HttpResponse(null, { status: 204 });
    }),
    http.post("/api/projects/:id/secrets/import-dotenv", ({ params }) => {
      const target = projects.get(String(params.id));
      if (!target) return notFound(String(params.id));
      const entry = secretsOf(target.id);
      if (!entry.dotenv) return refuse("dotenv_missing", "no .env file", 404);
      const imported = entry.dotenv;
      for (const name of imported) entry.names.set(name, nowSec());
      entry.dotenv = null;
      touched(target);
      return HttpResponse.json({ imported });
    }),
```

Also, where the mock finishes an `up`/`restart` job (`target.status = "started_ok"` in `startJob`'s `tick`), add `target.restart_needed = false;`.

- [ ] **Step 7: Verify**

Run (from `runtime/web`): `npm test && npm run typecheck && npm run build`
Expected: all pass. Then `npm run dev` and open `/?scenario=secrets`, go to recipe-box → Secrets: see STRIPE_KEY masked, OPENAI_API_KEY as missing, the .env notice; add a secret and see the restart notice on the secrets page and the project page. Stop the dev server afterwards.

- [ ] **Step 8: Commit**

```bash
git add runtime/web
git commit -m "Add a Secrets page to the console"
```

---

### Task 7: Coding-agent instructions and docs

**Files:**
- Modify: `runtime/instructions/eggie.md`
- Modify: `runtime/eggie_api/CLAUDE.md` (Feature areas), `runtime/CLAUDE.md` (CLI command list), `runtime/web/CLAUDE.md` (Structure), `docs/architecture.md` ("How a project runs" + "Trust boundary")
- Modify: `docs/superpowers/specs/2026-10-08-project-secrets-design.md` (record the deviations below)

- [ ] **Step 1: `runtime/instructions/eggie.md`** — add after the "Never edit `.eggie/overlay.yml`" line:

```markdown
- Secrets (API keys, passwords, tokens): never write a value into any file, the compose
  file or a commit, and never create `.env`. Add the name to `.env.example` as `NAME=`,
  read it from the environment in the code (or `${NAME}` in docker-compose.yml), and ask the
  user to fill it in on the project's **Secrets** page in Eggie, then restart. Eggie gives
  every secret to every service as an environment variable. If the user pastes a value into
  the chat anyway, save it with `eggie secret set NAME` (value on stdin), never into a file.
  `eggie secret list` shows which names are set and which are still missing.
```

- [ ] **Step 2: Layer docs**

`runtime/eggie_api/CLAUDE.md`, Feature areas, add:

```markdown
- **Secrets** (`core/secrets.py`) — per-project values in `state.db` (`secrets` table, v6),
  outside every project folder and never synced. `compose_up` lists the bare names under
  `environment:` for every service in `.eggie/overlay.yml` and passes the values only as the
  environment of `docker compose up`; no route, log or file ever carries a value.
  `restart_needed` = running and `secrets_changed_at > last_started_at`; a start is stamped
  before it reads values. `COMPOSE_`/`DOCKER_` names are refused because compose reads them.
  Purge drops them; a plain delete keeps them with the folder.
```

`runtime/CLAUDE.md`: in the `cli/eggie.py` bullet, change `` `up`/`new`/`clone`/`status`/`logs`/`down` `` to `` `up`/`new`/`clone`/`status`/`logs`/`down`/`secret` ``.

`runtime/web/CLAUDE.md` Structure, add under `apps/console`:

```markdown
  - `/p/:id/secrets` — the Secrets page; `projects/secrets.ts`'s `nameProblem` mirrors the API's
    name rule. Values are write-only: the page never has one to show. Mock: `?scenario=secrets`.
```

`docs/architecture.md`: read "How a project runs" and "Trust boundary"; add one sentence to each — the first: secrets reach containers as environment variables through the compose process environment, names only in the overlay; the second: secrets live in `state.db`, readable by anything root-equivalent in the VM including coding agents (protects against leaks into files, git and chat, not against the agent).

- [ ] **Step 3: Spec deviations** — in the spec, section 5, replace the sentence about the project lock with: "PUT/DELETE take no project lock (one sqlite statement each); `start_work` stamps the start time before reading values, so a change during a start still reports `restart_needed`. import-dotenv takes the project lock because it deletes a file." Change `updated_at TEXT` to `REAL` in section 1 and add `"error": str | null` to the `dotenv` shape.

- [ ] **Step 4: Run the whole Python suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add runtime/instructions/eggie.md runtime/eggie_api/CLAUDE.md runtime/CLAUDE.md runtime/web/CLAUDE.md docs/architecture.md docs/superpowers/specs/2026-10-08-project-secrets-design.md
git commit -m "Tell coding agents to keep secret values out of files"
```

---

### Task 8: eggie-skills (separate repo, separate PR)

**Repo:** `eggie-io/eggie-skills` (clone to the session scratchpad; branch `feature/13-project-secrets` from `main`).

**Files:** `omelet-stack/references/{payload,medusa,streamlit,fastapi-nextjs,nextjs,django,laravel}.md`, `omelet-rules/assets/AGENTS.md`, `omelet-setup/SKILL.md` — whichever of these mention `.env` for keys (find them with `grep -rn -i "\.env\|secret\|api key\|token" --include=*.md .`). Run the repo's own tests (`ls tests/`, read how they run) before and after.

- [ ] **Step 1:** In every reference that tells the agent to put keys in `.env` or "entered in `.env`": replace with "list the name in `.env.example` (`NAME=`) and ask the user to fill it on the project's **Secrets** page in Eggie; never write the value into a file".
- [ ] **Step 2:** Where a compose snippet uses `${X_API_KEY:-}` (streamlit, fastapi-nextjs), keep it and add `X_API_KEY=` to the `.env.example` the reference creates, so Eggie shows the key as missing.
- [ ] **Step 3:** Placeholder secrets that the app generates for itself (`PAYLOAD_SECRET: change-me-in-env`, `JWT_SECRET: change-me`, `COOKIE_SECRET: change-me`): tell the agent to list them in `.env.example` and ask the user to set them on the Secrets page; keep `.env` in `.gitignore` lines unchanged.
- [ ] **Step 4:** Laravel's "copy `.env.example` to `.env`": Laravel needs a `.env` for `APP_KEY` etc. Change to: non-secret settings may live in `.env`; keys and passwords go on the Secrets page (they override `.env` because they arrive as real environment variables).
- [ ] **Step 5:** `omelet-rules/assets/AGENTS.md`: next to "Never commit `.env`", add "never write a secret value into any file; secrets live on the project's Secrets page in Eggie".
- [ ] **Step 6:** Run the repo's tests, commit ("Point coding agents at Eggie's Secrets page for keys"), push, open a PR to `main` referencing eggie-io/eggie-resources#13.
