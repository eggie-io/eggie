# runtime/eggie_api/ — the in-VM API

FastAPI service shipped as the `eggie-api` Docker image, built from this directory alone (own
`pyproject.toml`: fastapi, uvicorn, pyyaml). Runs as a non-root user in the docker group. Tests:
`tests/runtime/api/` (the root `pyproject.toml` puts `runtime/` on `pythonpath`).

**Never import `host/`** (`tests/runtime/api/test_no_host_import.py`), and no platform branching —
the guest is always Linux (`tests/test_no_platform_leak.py`).

## Shape

- `core/` — all logic, no FastAPI: compose parsing (`compose.py`), web-service detection
  (`detect.py`), Traefik overlay generation (`overlay.py`), project identity/slug (`project.py`),
  failure classification and guest lifecycle (`lifecycle.py`), sqlite state + migrations
  (`state.py`, `migrate.py`), sessions, disk, health.
- `core/exec.py` — `LocalRunner`, the in-VM twin of the host's `VmProvider.exec`: same `Completed`
  contract, never raises. Inject a fake runner in tests.
- `core/config.py` — `ApiConfig`, the **only** place the domain and edge port may come from.
- `core/constants.py` — values shared with the host and the guest CLI, held equal by
  `tests/test_constants_agree.py`. `API_VERSION` here is the wire protocol, not the release number
  (see `runtime/CLAUDE.md`).
- `routes/app.py` — `create_app(config, runner, state)` is a **factory on purpose**: no module-level
  `app`, so importing it opens no sqlite file and starts no thread. `routes/jobs.py` is the
  in-process job registry keeping slow compose work off the request. `routes/__main__.py` is the
  uvicorn entrypoint on `0.0.0.0:39099` and the only place background threads (sync) start.

## Routing and auth

Every route is on one `APIRouter` mounted **twice**:
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

- **Secrets** (`core/secrets.py`) — per-project values in `state.db` (`secrets` table, v6),
  outside every project folder and never synced. `compose_up` lists the bare names under
  `environment:` for every service in `.eggie/overlay.yml` and passes the values only as the
  environment of `docker compose up`; no route, log or file ever carries a value.
  `restart_needed` = running and `secrets_changed_at > last_started_at`; a start is stamped
  before it reads values. `COMPOSE_`/`DOCKER_` names are refused because compose reads them.
  Purge drops them; a plain delete keeps them with the folder.
- **Files** (`core/files.py`) — `POST/GET /projects/{id}/files`, `PUT/GET/DELETE
  /projects/{id}/files/{path}`; replaces the 32,767-char `wsl.exe` command-line ceiling.
  `extract_archive` **merges** an uploaded tar.gz into the project dir and rejects absolute paths,
  `..` escapes and escaping links via `tarfile`'s `filter="data"` plus an explicit absolute-path
  check (the filter silently normalizes absolute names instead of refusing).
- **Uploads** (`core/uploads.py`) — resumable chunked uploads staged in `/opt/eggie/uploads`,
  outside `projects_root`; the staged file's size is the offset.
- **Reconcile** (`core/reconcile.py`) — project folders with no state row (made by a coding agent),
  for the console's adopt flow.
- **Delete** — `DELETE /projects/{id}?purge=true` removes volumes and the folder; plain DELETE keeps
  them.
- **Connect** (`core/connect.py`) — `GET /connect` turns `/opt/eggie/connect.json` into
  `{vm, ssh}`; the SSH port is `host/providers/eggie.yaml`'s `ssh.localPort`, held equal by
  `tests/test_constants_agree.py`.
- **Account / cloud sync** (`core/cloud.py`, `account.py`, `sync.py`) — the Eggie service
  (`EGGIE_CLOUD_URL`, default `https://app.eggie.io/api`). Device-code sign-in, tokens in
  `state.db`; `sync.py` gives each local project a service record
  (`client_ref = <device_id>/<local_id>`) on a thread. Only the console is locked until sign-in —
  the CLI and agents never wait on it. Service gaps are **not** worked around here; see
  `docs/superpowers/specs/2026-09-23-account-sign-in-sync-design.md` §7.
- **GitHub** (`core/github.py`, `github_link.py`) — Device Flow (`EGGIE_GITHUB_CLIENT_ID`, no
  secret); token in `/opt/eggie/github/token` (0600). The API never touches a user home: it writes
  a token-free `desired.json` with a rising `generation`; a systemd path unit runs
  `install/lib/github-apply.sh` as root, which echoes the generation into `applied.json`. "Ready"
  only when the two match; `applied.json` missing after 30 s means the runtime predates the feature.
  `POST /github/clone` passes the token to git only through the child's environment.
- **Public URL** (`core/public.py`) — a project's temporary public URL. The Eggie service owns the
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
- **Agents** (`core/agents.py`) — `GET /agents/status` and `POST /agents/{id}/setup`. The API never
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
  get no restart policy, so after a VM reboot only `resume_projects()` (run from `__main__`)
  brings the `started_ok` ones back; without it Traefik answers 404 under a "running" badge.
- Anything the API creates inside a project must be group-writable: login users are never the
  API's uid. `install.sh`'s default ACL on `/opt/eggie/projects` handles that under the API's
  umask 022; `clone_argv`'s `umask 002` predates it and is harmless.
