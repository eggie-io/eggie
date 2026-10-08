# Project secrets (local) — design

Issue: eggie-io/eggie-resources#13. Status: Revision 3, agreed with the user 2026-10-08.

Revision 3 replaces the earlier `.env.example`-driven model (defaults, missing detection, `.env`
import). That model fought frameworks that require a real `.env` (Laravel, WordPress/Bedrock,
Symfony), guessed at template names (`.env.example`, `.env.sample`, `.env.dist`, …) and could not
stop a secret landing in a file anyway. Git history has the earlier text.

## Goal

A project keeps working exactly as it would on a developer's laptop: its own `.env`, edited by the
coding agent for ordinary settings. On top of that, Eggie offers one simple, optional place for
**third-party credentials** (API keys, payment keys, passwords to outside services). A value put
there never lands in the project folder, git or an export, reaches the app as an ordinary
environment variable, and wins over the same name in `.env`.

This is a safe default, not enforcement. Nothing stops a user or agent from writing a key into a
file or hard-coding it; Eggie's terms (outside this repo) say Eggie is not responsible for that.

Out of scope: encryption at rest, per-service scoping (every service gets every secret), cloud
deploy / transfer between environments, scanning the project for leaked keys (a later security
skill), any host-side change, an `API_VERSION` bump (all routes are additive and the host calls
none of them).

Known limitation: a coding agent inside the VM can still read values from running containers
(`docker exec … env`, `docker inspect`).

## Decisions

| # | Question | Decision |
|---|----------|----------|
| D1 | Delivery | Names in `.eggie/overlay.yml`, values only in the environment of compose calls |
| D2 | `.env` | Belongs to the project. Eggie never reads, parses, imports or empties it |
| D3 | What is a secret | Third-party credentials. Internal keys the app generates (Laravel `APP_KEY`, the local DB password) stay in `.env` as usual |
| D4 | Compose literal vs secret | A value a service hard-codes in its own `environment:` wins; the agent fixes that case |
| D5 | Telling the user what's needed | `eggie secret request NAME "where to get it"` puts an empty field with that hint on the Secrets page |
| D6 | Project delete | Secrets and requests removed only on purge |
| D7 | CLI value input | Hidden prompt on a TTY, stdin otherwise; never argv |
| D8 | Visibility | Value visible while typing (eye toggle), write-only once saved |
| D9 | Leak notice | The project page tells the user to keep keys in Secrets, not in project files |

## 1. Storage

Migration v6 in `core/migrate.py` (not yet released, so v6 is edited in place):

```sql
CREATE TABLE IF NOT EXISTS secrets (
  project_id TEXT NOT NULL,
  name       TEXT NOT NULL,
  value      TEXT NOT NULL,
  updated_at REAL NOT NULL,
  PRIMARY KEY (project_id, name)
);
CREATE TABLE IF NOT EXISTS secret_requests (
  project_id TEXT NOT NULL,
  name       TEXT NOT NULL,
  hint       TEXT NOT NULL,
  created_at REAL NOT NULL,
  PRIMARY KEY (project_id, name)
);
```

plus `_add_column(projects, "secrets_changed_at", "REAL")`.

`state.db` already holds the cloud and GitHub tokens, sits outside every project folder, and is
never synced. `core/state.py`: `secret_names`, `secret_values`, `set_secrets` (also deletes a
request of the same name), `delete_secret`, `secret_requests`, `request_secret`, `delete_request`,
`drop_secrets` (both tables). Value writes stamp `projects.secrets_changed_at` inside the lock;
requests never do.

## 2. Validation (`core/secrets.py`, pure)

- Name: `^[A-Za-z_][A-Za-z0-9_]*$`. Reserved (case-insensitive): prefixes `COMPOSE_`, `DOCKER_`,
  `LD_`, names `PATH`, `HOME` — compose and the docker CLI read these from their own environment.
- Value: not empty, no NUL, no lone surrogate, at most 64 KiB. Project total (sum of
  `len(name)+len(value)+2`) at most 512 KiB, below Linux's exec limits.
- Hint: plain text, 1–500 characters, no NUL; shown as text, never as HTML.
- Errors in the API's `{"error": {code, message}}` shape: `secret_name_invalid`,
  `secret_name_reserved`, `secret_invalid_value`, `secret_too_large`, `secrets_too_large`,
  `secret_hint_invalid`.

`core/secrets.py` keeps `declared(compose)`: per service, the names it sets to a literal (a value
with no `$`) in its own `environment:`. Every `.env`/`.env.example` parser and compose `${VAR}`
scanner from the earlier revisions is removed.

## 3. Delivery

- `build_overlay(..., services, secret_names, declared)`: every compose service gets
  `environment: [NAME, …]` (bare names), minus the names that service declares as a literal (D4).
- Every compose call that loads the user's file (`up`, `ps`, `down`, `logs`, `ps -q`/diagnose) gets
  `env={name: value}` merged over the API's environment. Plain `docker` calls get none.
- Why the secret wins over `.env`, for every way an app can read it:
  - compose `${NAME}` interpolation takes the process environment before the project's `.env`;
  - an overlay `environment:` entry beats `env_file: .env`;
  - framework loaders (phpdotenv, Symfony Dotenv, Node `dotenv`/`--env-file`, Next.js, Vite,
    python-dotenv, django-environ, Rails dotenv, godotenv `Load`) keep a variable that already
    exists in the environment.
  Exceptions the agent instructions name: loaders in override mode, config cached into an image,
  build-time values (`build.args`, `NEXT_PUBLIC_*`).
- Restart and `resume_projects()` go through `compose_up`, so both apply secrets. Values never
  appear in argv, the overlay, logs or job output.

## 4. Routes

Mounted on both `/api` (console) and the token API (in-VM CLI). No response contains a value.

| Method | Path | Result |
|--------|------|--------|
| GET | `/projects/{id}/secrets` | `{secrets: [{name, updated_at}], requested: [{name, hint}], restart_needed}` |
| PUT | `/projects/{id}/secrets/{name}` | body `{value}`; create or replace; clears a request of that name; `204` |
| DELETE | `/projects/{id}/secrets/{name}` | removes the value and any request of that name; `204`; `404 secret_not_found` if neither existed |
| PUT | `/projects/{id}/secret-requests/{name}` | body `{hint}`; create or replace the request; `204` |

`requested` lists only names with no stored value. Unknown project → existing `404`. PUT/DELETE take
no project lock; `start_work` stamps the start before reading values, so a change during a start
still reports `restart_needed`.

`restart_needed` = status `started_ok` or `crash_looping` and `secrets_changed_at >
last_started_at`; a start ending in either status stamps `last_started_at`. Also in the project
payload (`GET /projects/{id}` and the list), with `secrets_requested: <count>` for the tile.

## 5. Console (`runtime/web/apps/console`)

- **Secrets** tile on the project page → `/p/:id/secrets`. Its line reads "Keep API keys and
  passwords here, not in project files." When requests are open it shows "N requested".
- Secrets page:
  - Short intro: values saved here reach the app when it starts, override the same name in `.env`,
    and never land in the project folder or git.
  - **Requested** — name, hint, value field, Save; Dismiss (DELETE).
  - **Your secrets** — `NAME ••••••••`, Replace (new value, old never shown), Delete.
  - Add form: name + value.
  - Value fields: text with an eye toggle, visible by default, `autocomplete="off"`; cleared after
    Save.
  - "Secrets changed — restart to apply" with a Restart action that goes to the project page, which
    follows the job.
- Project page shows the same restart notice when `restart_needed`.
- MSW handlers and the `?scenario=secrets` mock follow the new shape.

## 6. In-VM CLI (`runtime/cli/eggie.py`)

- `eggie secret set NAME` — TTY: `getpass`; otherwise stdin to EOF, one trailing newline stripped.
  Never takes the value as an argument.
- `eggie secret request NAME "hint"` — adds or replaces a request.
- `eggie secret list` — names, then `Requested:` lines with hints, then the restart hint when
  `restart_needed`.
- `eggie secret rm NAME` — removes a value or a request.
- Project resolved from the cwd like `up`.

## 7. Coding-agent instructions

`runtime/instructions/eggie.md`, and a matching rework of eggie-skills PR #1:

- Use the project's `.env` as on any laptop: create it from whatever template the project has,
  edit it for settings, let framework commands write their own keys (`php artisan key:generate`).
- For a third-party credential: explain to the owner where to get it, run
  `eggie secret request NAME "where to get it"`, and tell them to fill it on the project's
  **Secrets** page. Leave the name empty or absent in `.env`; Eggie's value wins.
- Never write a secret value into any file, compose file or commit. If the owner insists on putting
  it in `.env`, do it, but say once that it then lives in the project folder and can reach git.
- A service that hard-codes a name in its own `environment:` beats Eggie: replace the literal with
  `${NAME}` when the owner moves that value to Secrets.
- After a secret changes, the app needs a restart. One-off commands go through
  `docker exec <container> …`; a `docker compose run` the agent starts gets no Eggie values.
- Override-mode `.env` loaders, cached config and build-time variables don't see Eggie's values.

## 8. Testing

Only where a wrong result is plausible:

- Validation: name pattern, reserved names, empty/NUL/surrogate values, size caps, hint limits.
- `declared`: literal vs `${…}` vs bare name, list and map forms.
- Overlay: every service gets the names minus its literals; no values in the overlay text.
- Compose calls: values reach the env of every compose call, never argv; plain docker calls get none.
- State/routes: no response contains a value; PUT clears the request; DELETE removes either;
  `requested` hides set names; `restart_needed` transitions; purge drops both tables.
- CLI: `set` reads stdin and refuses a value argument; `request` and `list` output.

Left untested: page rendering (no component-test setup); real compose and framework precedence
(live-VM acceptance run).
