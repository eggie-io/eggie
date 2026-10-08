# runtime/eggie_api/ — the in-VM API

FastAPI service shipped as the `eggie-api` Docker image, built from this directory alone (own
`pyproject.toml`: fastapi, uvicorn, pyyaml). Runs as a non-root user in the docker group. Tests:
`tests/runtime/api/` (the root `pyproject.toml` puts `runtime/` on `pythonpath`).

**Never import `host/`** (`tests/runtime/api/test_no_host_import.py`), and no platform branching —
the guest is always Linux (`tests/test_no_platform_leak.py`).

## Shape

One dependency edge: `rest → services → domain | infra`. `tests/runtime/api/test_layering.py`
fails on anything else.

- `domain/` — pure logic, no I/O: compose port helpers (`compose.py`), web-service detection
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
- `rest/` — the only tree importing FastAPI (named `rest`, not `http`: see below). `app.py`'s
  `create_app(*, config=None, services=None, **overrides)` is a **factory on purpose**: no
  module-level `app`, so importing it opens no sqlite file and starts no thread; it calls
  `wiring.build()` when not handed services. `auth.py` is the middleware, `errors.py` the one
  class → status map, `schemas.py` the request models, `routers/<feature>.py` one `build(services)`
  per feature. A router parses the request, calls one service method and shapes the response:
  no `try/except`, no locks, no business branching.
- `wiring.py` — `build(config, **fakes) -> Services`, the composition root; `__main__.py` is the
  uvicorn entrypoint (`python -m eggie_api`) and the only place the long-running loops start (sync, project resume);
  services start their own short-lived pollers on demand.
- `errors.py` — `EggieError(code, message)` and its subclasses (`NotFound`, `Conflict`,
  `Invalid`, …). Anything below `rest/` that wants the caller to act raises one with the wire
  code; the status comes from the class. Transport exceptions (`CloudError`, `GitHubError`,
  `NotSignedIn`, `JobFailed`) never reach HTTP — the calling service translates.
- `config.py` — `ApiConfig`, the **only** place the domain and edge port may come from.
- `constants.py` — values shared with the host and the guest CLI, held equal by
  `tests/test_constants_agree.py`. `API_VERSION` here is the wire protocol, not the release
  number (see `runtime/CLAUDE.md`).

### Adding a feature area

1. A table → `infra/migrate.py` migration + `infra/repos/<name>.py`, added to `Repos`.
2. `services/<name>.py` taking the repos and services it needs; raise `errors.*` subclasses.
3. `rest/routers/<name>.py` with `build(services)`; add it to `FEATURES` in `rest/app.py`.
4. Construct it in `wiring.build()` and add the field to `Services`.

## Routing and auth

Every feature router (`FEATURES` in `rest/app.py`) is mounted **twice**:
- at `/` behind the bearer token (`/opt/eggie/api.token`) — the host and the in-VM CLI;
- at `/api` behind the `eggie_session` cookie plus a `Host`/`Origin` allowlist — the browser
  console, reached through Traefik on the edge port.

The desktop gets the browser a session via `POST /sessions/handoff`; a signed-in page gets the
system browser one via `POST /api/sessions/handoff`
(`docs/superpowers/specs/2026-09-21-web-ui-agent-prerequisites-design.md`).
Every non-2xx body is `{"error": {"code": ..., "message": ...}}`, produced by one exception
handler — the host's `ApiClient` and the console both depend on that shape.

A change to a route the **host** calls that isn't backward compatible needs an `API_VERSION` bump
and a host release. Prefer additive changes.

## Feature areas

- **Secrets** (`domain/secrets.py`, `services/secrets.py`) — per-project values in `state.db` (`secrets`, v6), outside
  every project folder and never synced; requests (`secret_requests`: name + hint) ask the owner
  for one and vanish once it has a value. The project's `.env` is never read or written. A service
  gets a name unless it sets that name to a non-empty literal in its own `environment:` (`declared`).
  `compose_up` lists bare names in `.eggie/overlay.yml`; values go only into the environment of
  compose commands that load the user's file (`up`, `ps`, `down`, `logs`, `container_id`) — compose
  interpolates the file for each, so `${KEY:?}` breaks any that lacks them. `restart_needed` =
  `started_ok` or `crash_looping` and `secrets_changed_at > last_started_at`; a start is stamped
  before it reads values. Reserved names (`COMPOSE_`/`DOCKER_`/`LD_`/`BUILDX_`/`BUILDKIT_`, `PATH`, `HOME`) are refused.
  Services pulled in through compose `include:` get no names. Purge drops values and requests.
- **Files** (`infra/files.py`, `services/files.py`) — `POST/GET /projects/{id}/files`, `PUT/GET/DELETE
  /projects/{id}/files/{path}`; replaces the 32,767-char `wsl.exe` command-line ceiling.
  `extract_archive` **merges** an uploaded tar.gz into the project dir and rejects absolute paths,
  `..` escapes and escaping links via `tarfile`'s `filter="data"` plus an explicit absolute-path
  check (the filter silently normalizes absolute names instead of refusing).
- **Uploads** (`infra/uploads.py`, `services/uploads.py`) — resumable chunked uploads staged in `/opt/eggie/uploads`,
  outside `projects_root`; the staged file's size is the offset.
- **Reconcile** (`infra/reconcile.py`) — project folders with no state row (made by a coding agent),
  for the console's adopt flow.
- **Delete** — `DELETE /projects/{id}?purge=true` removes volumes and the folder; plain DELETE keeps
  them.
- **Connect** (`infra/connect.py`) — `GET /connect` turns `/opt/eggie/connect.json` into
  `{vm, ssh}`; the SSH port is `host/providers/eggie.yaml`'s `ssh.localPort`, held equal by
  `tests/test_constants_agree.py`.
- **Account / cloud sync** (`infra/cloud.py`, `services/account.py`, `services/sync.py`) — the Eggie service
  (`EGGIE_CLOUD_URL`, default `https://app.eggie.io/api`). Device-code sign-in, tokens in
  `state.db`; `sync.py` gives each local project a service record
  (`client_ref = <device_id>/<local_id>`) on a thread. Only the console is locked until sign-in —
  the CLI and agents never wait on it. Service gaps are **not** worked around here; see
  `docs/superpowers/specs/2026-09-23-account-sign-in-sync-design.md` §7.
- **GitHub** (`infra/github.py`, `services/github_link.py`) — Device Flow (`EGGIE_GITHUB_CLIENT_ID`, no
  secret); token in `/opt/eggie/github/token` (0600). The API never touches a user home: it writes
  a token-free `desired.json` with a rising `generation`; a systemd path unit runs
  `install/lib/github-apply.sh` as root, which echoes the generation into `applied.json`. "Ready"
  only when the two match; `applied.json` missing after 30 s means the runtime predates the feature.
  `POST /github/clone` passes the token to git only through the child's environment.
- **Public URL** (`services/public.py`, `infra/tunnel.py`) — a project's temporary public URL. The Eggie service owns the
  Cloudflare tunnel and its routing; the VM asks for a URL, keeps the token in
  `/opt/eggie/tunnel/token` (0640, present only while a URL is on; the directory is mounted
  read-only into the client) and starts the profile-gated `tunnel` service in `stack.yml` through
  compose. The service rewrites Host to the project's local hostname, so overlays are unchanged;
  apps that build absolute URLs from Host send public visitors to `*.127-0-0-1.sslip.io`. Only the
  console turns it on or off (POST and DELETE are mounted at `/api` alone); the CLI never shows it.
  Nothing about it is in `state.db`: the service is the record, the API keeps only a memory of it
  and re-reads the account's active URL (`GET /v1/tunnels/url`) on each sync pass. The `tunnel`
  network reaches the VM through its gateway, so every port published on 0.0.0.0 (the api's, a
  project's `ports:`) is reachable from the tunnel client. Design:
  `docs/superpowers/specs/2026-09-24-public-url-design.md`.
- **Agents** (`services/agents.py`) — `GET /agents/status` and `POST /agents/{id}/setup`. The API never
  reads a home: it bumps counters in `/opt/eggie/agent-status/` (`check`, `setup/<id>`) and returns
  the root runner's `status.json` (`runtime/install/lib/agents.py`), one poll behind. Setup is only
  requested when the agent isn't connected and its setup isn't `installing`/`ready`, so Retry after
  `failed` is the same call. Manifests are read from `/opt/eggie/runtime/agents`.

## Testing

- `tests/runtime/api/test_acceptance_detection.py` runs the real-world compose shapes in
  `tests/fixtures/compose/` through `detect_web` — add a fixture when changing detection rules.
- `_slug` is mirrored in the console; both sides read `tests/fixtures/slugify-cases.json`
  (`test_slug_cases.py`). Change the fixture, not just one implementation.
- `fake_cloud.py` / `fake_github.py` stand in for the network.

## Things that will bite you

- The `tunnel` service is behind a compose profile. A plain `docker compose -f stack.yml up -d` or
  `pull` never touches it; the API's own compose calls pass `--profile tunnel`, and so must anything
  else that means to include it.
- A project's `status` in `state.db` is the last start/stop outcome, not a live reading. Projects
  get no restart policy, so after a VM reboot only `LifecycleService.resume_all()` (run from `__main__`)
  brings the `started_ok` ones back; without it Traefik answers 404 under a "running" badge.
- Anything the API creates inside a project must be group-writable: login users are never the
  API's uid. `install.sh`'s default ACL on `/opt/eggie/projects` handles that under the API's
  umask 022; `clone_argv`'s `umask 002` predates it and is harmless.
- A top-level directory or module in `eggie_api/` must not share a name with a stdlib module (`http`,
  `json`, …): the image's working directory is first on `sys.path`, so it would shadow the stdlib.
  `test_layering.py` pins it.
