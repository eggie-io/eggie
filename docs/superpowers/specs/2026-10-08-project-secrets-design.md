# Project secrets (local) — design

Issue: eggie-io/eggie-resources#13. Status: approved in brainstorm 2026-10-08.

## Goal

Every project gets a place for secrets (API keys, passwords, tokens) that keeps them out of
project files, git, exports, synced metadata and coding-agent chat. Secrets reach the app as
ordinary environment variables; the user's compose file is never modified.

Out of scope: encryption at rest, per-service scoping (every service gets every secret), cloud
deploy / transfer between environments, any host-side change, an `API_VERSION` bump (all routes are
additive and the host calls none of them).

Known limitation: a coding agent inside the VM can still read values from running containers
(`docker inspect`, `/proc/<pid>/environ`). This protects against leaks into git, files and chat,
not against the agent.

## Decisions taken

| # | Question | Decision |
|---|----------|----------|
| D1 | Delivery mechanism | Names in `.eggie/overlay.yml`, values via the environment of `docker compose up` |
| D2 | Imported `.env` | VM-side offer on the project, whatever brought the file in; no host change |
| D3 | Project delete | Secrets removed only on purge (they live as long as the folder) |
| D4 | Agent instructions | `runtime/instructions/eggie.md` here **and** a separate PR in `eggie-skills` |
| D5 | "Missing" rule | Keys of `.env.example` + `${VAR}` refs with no default, minus existing secrets |
| D6 | CLI value input | Hidden prompt on a TTY, stdin otherwise; never argv |

## 1. Storage

Migration v6 in `core/migrate.py`:

```sql
CREATE TABLE IF NOT EXISTS secrets (
  project_id TEXT NOT NULL,
  name       TEXT NOT NULL,
  value      TEXT NOT NULL,
  updated_at REAL NOT NULL,
  PRIMARY KEY (project_id, name)
);
```

plus `_add_column(projects, "secrets_changed_at", "REAL")`.

`state.db` already holds the cloud and GitHub tokens, sits outside every project folder, and is
never synced. `core/state.py` gains `secret_names`, `secret_values`, `set_secrets`,
`delete_secret`, `drop_secrets` (all under the existing RLock); `set_secrets`/`delete_secret` also
stamp `projects.secrets_changed_at`.

Lifetime: `DELETE /projects/{id}?purge=1` drops the project's secrets. A plain delete keeps them
(the folder stays too); re-adding the same id brings them back.

## 2. Validation (`core/secrets.py`, pure)

- Name: `^[A-Za-z_][A-Za-z0-9_]*$`; reject prefixes `COMPOSE_` and `DOCKER_` (compose and the
  docker CLI read those from their own environment — a secret would hijack the run).
- Value: no NUL byte; at most 64 KiB. Project total (sum of `len(name)+len(value)+2`) at most
  512 KiB — well below Linux's per-string (128 KiB) and total (~2 MiB) exec limits, so the failure
  is a clear `400` on set, not an exec error at start.
- Errors use the API's existing `{"error": {code, message}}` shape:
  `secret_name_invalid`, `secret_name_reserved`, `secret_too_large`, `secrets_too_large`.

## 3. Delivery

- `build_overlay(project_id, webs, domain, services, secret_names)`: when `secret_names` is
  non-empty, **every** compose service gets `environment: [NAME, …]` (bare names, no values).
  Web services keep their networks/labels; non-web services get only `environment`.
- Every compose command that loads the user's file gets `env={name: value}`: `up`, `ps`, `down`,
  `logs` (buffered and follow) and `container_id` (`ps -q`). Compose interpolates the whole file
  for each of them, so a `${KEY:?}` that only `up` could resolve would break the rest. Plain
  `docker` calls (`ps -a --filter`, `inspect`, `rm`, `exec`) get no env.
- Bare `- NAME` in compose resolves from the compose process environment, and so does `${NAME}`
  interpolation in the user's compose file. Secrets are merged over the API's inherited
  environment, so they win over it.
- The overlay is the later `-f` file, so a secret overrides a hard-coded `environment:` value of
  the same name in the user's compose.
- Restart (down + up) and `resume_projects()` go through `compose_up`, so both apply secrets.
- Values never appear in argv, the overlay, logs or job output.

## 4. Expected and missing variables (`core/secrets.py`)

- `.env.example` in the project root: one key per `KEY=…` line; blank lines and `#` comments
  skipped; leading `export ` allowed; the value is ignored (placeholders).
- `docker-compose.yml` raw text: `${VAR}`, `${VAR:?err}`, `${VAR?err}` and `$VAR` count;
  `${VAR:-x}`, `${VAR-x}`, `${VAR:+x}`, `${VAR+x}` do not; `$$` is an escape, not a reference.
- `missing = sorted(expected - secret_names)`; `COMPOSE_*`/`DOCKER_*` names never count as
  expected, since they can never be set.
- Only the main `docker-compose.yml` is read: services pulled in through compose `include:` get no
  secret names in the overlay and their refs are not listed.

The current stack skills write `${X_API_KEY:-}`; the eggie-skills PR makes them also list such
keys in `.env.example`, which is how they become "missing".

## 5. Routes

Mounted on both the console's `/api` and the token API (the in-VM CLI uses them). No response of
any secrets route contains a value.

| Method | Path | Result |
|--------|------|--------|
| GET | `/projects/{id}/secrets` | `{secrets: [{name, updated_at}], missing: [name], dotenv: {names: [name], error: str \| null} \| null, restart_needed: bool}` |
| PUT | `/projects/{id}/secrets/{name}` | body `{value}`; create or replace; `204` |
| DELETE | `/projects/{id}/secrets/{name}` | `204`; `404 secret_not_found` if absent |
| POST | `/projects/{id}/secrets/import-dotenv` | parse root `.env`, store every key (file values overwrite), empty the file; returns `{imported: [name]}` |

Unknown project → existing `404`. PUT/DELETE take no project lock (one sqlite statement each); `start_work` stamps the start time before reading values, so a change during a start still reports `restart_needed`. import-dotenv takes the project lock because it writes a file.

import-dotenv errors: a `.env` line with an invalid name fails with `dotenv_invalid` and a message naming only the line number (never the key text, which may be part of a secret); a reserved-prefix name fails with `secret_name_reserved`; a `.env` with no values is `404 dotenv_missing`; a `.env` that is a symlink is never read (`400 dotenv_invalid`, and GET reports it as `dotenv.error`); a file that cannot be emptied is `409 dotenv_not_removed` after the values are saved.

`restart_needed` = project status is `started_ok` or `crash_looping` and
`secrets_changed_at > last_started_at` (a service exiting for lack of a key is the usual crash
loop). A start that ends in either status stamps `last_started_at`.
It is also added to the project payload (`GET /projects/{id}` and the list) so the project page can
show the notice.

### `.env` parsing for import

`KEY=value` lines; `export ` prefix; `#` comments and blank lines skipped; single-quoted values
literal; double-quoted values support `\n`, `\"`, `\\` and may span lines; unquoted values end at
` #` and are trimmed, and one that starts with `#` (`KEY= # note`) is empty. Invalid names or
reserved prefixes fail the whole import (nothing stored, file kept): `dotenv_invalid` naming only
the line number, or `secret_name_reserved`.

The file is emptied, not deleted: a service with `env_file: .env` fails to start without it.
Emptying falls back to a root container (`truncate`, same image as the tree-removal fallback) when
the API cannot write the file.

The `.env` offer stays visible while the file has values (an emptied file is `dotenv: null`);
there is no dismissed state.

## 6. Console (`runtime/web/apps/console`)

- New tile **Secrets** in `screens/project/Tiles.tsx` → route `/p/:id/secrets` (like Files).
- Secrets page:
  - `.env` notice when `dotenv` is non-null: "This project has a .env file with N values. Move them
    into secrets?" → import-dotenv.
  - Missing rows: name + empty password field + Save.
  - Existing rows: `NAME  ••••••••` + Edit (new value, old never shown) + Delete.
  - Add form: name + password-type value.
  - "Secrets changed — restart to apply" notice with the existing Restart action when
    `restart_needed`.
- Project page shows the same restart notice when the project payload's `restart_needed` is true.
- `api/client.ts` gains `put`. Wording in `projects/copy.ts`. MSW handler + scenario for secrets.

## 7. In-VM CLI (`runtime/cli/eggie.py`)

- `eggie secret set NAME` — TTY: `getpass` prompt (no echo); otherwise read stdin to EOF, strip one
  trailing newline. Never accepts the value as an argument.
- `eggie secret list` — names, then a `missing:` section; prints a "restart to apply" hint when
  `restart_needed`.
- `eggie secret rm NAME`.
- Project resolved from the cwd like `up` (`_require_project`).

## 8. Coding-agent instructions

`runtime/instructions/eggie.md` gains: never write secret values into any file, compose file or
chat; never create `.env`; for a new key add `NAME=` to `.env.example`, read it from the
environment (or `${NAME}` in compose), and ask the user to fill it on the project's **Secrets**
page in Eggie (or `eggie secret set NAME` in a terminal).

Separate PR in `eggie-io/eggie-skills`: `omelet-stack/references/*` stop telling agents to put
keys in `.env`, list `${KEY:-}`-style keys in `.env.example`, and point at the Secrets page;
`omelet-rules` mentions the same rule.

## 9. Testing

Only where a wrong result is plausible:

- Validation: name pattern, reserved prefixes, value and project size caps, NUL.
- Missing detection: `.env.example` comments/`export`/quotes; `${VAR}` vs defaults; `$$`.
- `.env` import parsing: quotes, multi-line, `export`, inline comments; invalid key fails whole import.
- Overlay: every service gets the names; no values in the overlay text.
- Compose calls: values reach the exec `env` of every compose command, never argv; plain docker
  calls get none.
- Routes: no secrets-route response contains a stored value; `restart_needed` transitions; purge
  drops secrets, plain delete keeps them; import-dotenv empties the file; a `.env` symlink is
  never read.
- CLI: `set` reads stdin, refuses an extra positional value; `list` shows missing.
- Vitest only for pure console logic if any appears; no component tests.

Left untested: the console page rendering (no component-test setup exists); real compose
behaviour with bare `environment` names (covered by the live-VM acceptance run).

---

## Revision 2 — defaults, precedence and review fixes (2026-10-08)

Decided with the user after the independent reviews of eggie#55 and eggie-skills#1. Where this
section disagrees with sections 1–9 above, this section wins.

| # | Question | Decision |
|---|----------|----------|
| D7 | Compose literal vs secret | A value a service sets in its own `environment:` wins; Eggie never adds that name to that service |
| D8 | Generated internal keys | Agents may generate random internal keys straight into Eggie: `openssl rand -hex 32 \| eggie secret set NAME` |
| D9 | `.env.example` | Every key counts. Empty value → **needs a value**. Non-empty value → **default**, delivered to the app and editable |
| D10 | Non-secret settings | Live as defaults in `.env.example`; the owner edits them on the same page. No per-framework workaround — no `.env` anywhere |
| D11 | Visibility | Visible while typing (eye toggle), write-only once saved; repo defaults always shown in clear |
| D12 | `.env` import | Store only values that differ from the default; drop empty values |

### R1. Three sources, one environment

For each start, the effective variables are, highest precedence first:

1. **Compose literal** — any name a service sets to a literal value in its own `environment:`
   (a value with no `$`). Eggie does not list that name in the overlay for that service. A service
   entry `NAME: ${NAME}` or a bare `NAME` is not a literal: it resolves from Eggie's environment.
2. **Your values** — stored in `state.db` (`secrets` table), write-only, entered by the owner or set
   by an agent with `eggie secret set`.
3. **Defaults** — keys of the project-root `.env.example` with a non-empty value, read fresh from the
   file at every compose call. Parsed with the `.env` rules (§5); a line that doesn't parse is skipped,
   never fatal.

Values of 2 and 3 (yours win) are passed as the environment of every compose call that loads the
user's file; their names go into the overlay for every service except where rule 1 applies. No `.env`
file is needed by any framework: phpdotenv (Laravel), Next.js, Django, Rails and Node all prefer real
environment variables over a `.env`.

Reserved names are never listed, stored or delivered: prefixes `COMPOSE_`, `DOCKER_`, `LD_`, and the
exact names `PATH`, `HOME` (compose and the docker CLI read these from their own environment).

### R2. Needs a value

`missing` = (`.env.example` keys with an empty value ∪ compose `${VAR}` refs with no default)
− your values − defaults − names a compose service declares itself − reserved names.

### R3. Routes (changes)

`GET /projects/{id}/secrets` →

```json
{"secrets":  [{"name": "STRIPE_KEY", "updated_at": 1.0, "overrides_default": false}],
 "missing":  ["OPENAI_API_KEY"],
 "defaults": [{"name": "APP_NAME", "value": "Laravel", "overridden": false}],
 "dotenv":   null,
 "restart_needed": false}
```

Default values come from the repo file and are returned in clear; stored values never are.
"Reset to default" is `DELETE /projects/{id}/secrets/{name}`. `PUT` refuses an empty value
(`secret_invalid_value`), like the CLI and console already do.

### R4. Import

`POST …/secrets/import-dotenv`: parse `.env` (UTF-8 with or without BOM); skip empty values; skip
keys whose value equals the `.env.example` default; skip reserved keys and **keep those lines** in
`.env` (the file is rewritten with only them, or emptied when there are none); store the rest.
The offer (`dotenv`) is shown only when at least one key would be stored.

### R5. Console

The Secrets page has three sections: **Needs a value**, **Your values** (masked, Edit, Delete — on
an overridden default the action reads *Reset to default*), and **Defaults** (collapsed, value in
clear, Edit pre-filled with the default). Every value field is a text field with an eye toggle,
visible by default, `autocomplete="off"`; after Save the field clears and the row shows `••••••••`.

### R6. CLI

`eggie secret list` prints your values, then *Needs a value*, then the number of defaults from
`.env.example`. Unchanged otherwise.

### R7. Coding-agent instructions

`.env.example` at the project root is the project's list of variables: a secret is `NAME=` (empty),
a non-secret setting is `NAME=default`. Never create `.env`; if a scaffolding tool writes one, move
its non-secret keys into `.env.example` as defaults, generate random internal keys with
`openssl rand -hex 32 | eggie secret set NAME`, ask the owner for outside credentials on the
Secrets page, then delete the `.env`. `docker compose run` started by the agent gets none of these
variables; run one-off commands inside the running service with `docker compose exec`.

### R8. Review fixes folded in

A lone UTF-16 surrogate in a value is `secret_invalid_value`, not a 500; the change stamp is taken
inside the state lock; value fields carry `autocomplete="off"`.
