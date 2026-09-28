# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A PoC CLI (`omelet`) that creates a managed Linux VM, installs Docker inside it, runs any
`docker-compose` project in the guest, and hands back a working URL on the host. Windows/WSL2 is
the primary platform. macOS/Lima is **confirmed once, still mostly unverified**: a VM from this
project's own config booted and finished the runtime install on Apple Silicon, but nobody has
recorded a run of `create_vm` through `verify`. `host/providers/lima.py` and `omelet.yaml` carry
banners saying what is and is not confirmed; `docs/lima-verification-report.md` has the detail.

Neither `docs/architecture.md` nor `docs/roadmap.md` exists in this tree — check with `ls docs/`
before trusting a pointer to either; nothing here should send a reader to a file that isn't there.
The phased plan as it stands: Phase 1 (MVP) fixes what exists (repo split, no host CLI, SSH-only
access, autostart, self-update, signing) and adds accounts, a web app, secrets and public URLs;
Phases 2 and 3 are VPS deploys, paid plans and an own cloud. Check it before adding a host CLI
command or a host-side guest asset.
`docs/superpowers/` holds the original blueprint plus every design spec and plan;
`.superpowers/sdd/` holds the per-task execution ledger.

## Architecture shape: the host is a VM shell, the runtime is everything inside

Two halves, shipped and versioned independently:

- **Host** (`host/`, a frozen desktop binary) — creates and runs the VM, runs one bootstrap
  command in it (fetch `OMELET_RUNTIME_URL` → `bash`), reads the token, forwards ports, and talks
  to the API over HTTP. It holds no guest files and no knowledge of what the runtime installs.
- **Runtime** (`runtime/`) — everything inside the VM: Docker, the Traefik+API stack, the in-VM
  `omelet` CLI, coding-agent instructions, and skills installed from the separate `omelet-skills`
  repository (via `npx skills add`). Released as `runtime-v*` tags with a matching `omelet-api`
  image. The same `get.sh` provisions a cloud VM.

The seam between them is a fixed contract — token path, API port + `/health` `api` number, edge
port, `/opt/omelet/runtime.version` — and nothing else. A change inside the VM must never need a
host release; if it does, the logic is on the wrong side.

### Releasing the runtime (manual until CI exists)

Before the first shipped host can install anything:

- The repository is public.
- `runtime/install/get.sh` exists on `main` — hosts fetch `RUNTIME_URL` from `main`, not from a tag.
- At least one `runtime-vX.Y.Z` tag exists.
- Both ghcr images named by that tag's `runtime/stack.yml` — `omelet-api` and `omelet-web` — are
  published and public: ghcr makes a newly pushed package private, so each needs its visibility
  flipped by hand. `omelet-web` must be pushed multi-arch (`linux/amd64` and `linux/arm64`) —
  `install.sh` fails the whole install on any image it can't pull.
- `omelet-skills` (github.com/ihorklymchukdev/omelet-skills) must be public and hold the five
  skill folders — `install.sh`'s `npx skills add` step fails the whole install otherwise.
- `runtime/omelet_api/__init__.py`, the Dockerfile's `SERVICE_VERSION` and `runtime/stack.yml` are
  bumped together before the first tag — the api gained `/health`'s `api` field after 0.1.0. The
  stack's `omelet-web` tag carries the same version.

`runtime/install/get.sh` on `main` is a live contract for every shipped host: keep its env vars
(`OMELET_RUNTIME_REPO`, `OMELET_RUNTIME_REF`, `OMELET_RUNTIME_REPAIR`), marker path and exit
semantics backward compatible.

1. Bump `runtime/omelet_api/__init__.py`'s `__version__`, the Dockerfile's `SERVICE_VERSION` and
   `runtime/stack.yml`'s api and web image tags together (`tests/test_constants_agree.py` holds
   them equal).
2. `packaging/images/build.sh --push` — builds and pushes both images for amd64 and arm64. The web
   build needs `--build-context fixtures=tests/fixtures`, so a bare `docker build runtime/web` fails.
3. `git tag runtime-vX.Y.Z && git push origin runtime-vX.Y.Z`

Bump `runtime/omelet_api/core/constants.API_VERSION` (and the host's `SUPPORTED_API`) only when a
route the host calls changes incompatibly — that one needs a host release.
**`SERVICE_VERSION` must not become `API_VERSION`:** `SERVICE_VERSION` is the image/package
release number above; `API_VERSION` is the wire-protocol number the host checks against
`SUPPORTED_API`, a different value on a different cadence. Collapsing them into one word loses
the distinction a host release needs.

## Commands

```bash
pip install -e ".[dev]"

python3 -m pytest -q                                          # full suite (mid hundreds of tests, well under a minute)
python3 -m pytest tests/runtime/api/test_project.py -q        # one file
python3 -m pytest -k classify -q                              # one test by name
```

The browser UI lives in `runtime/web/` (npm workspace, Node ≥ 22.22):

```bash
cd runtime/web && npm install
npm run dev          # Vite + an in-browser mock API; ?scenario=empty|expired|handoff-spent|old-api|down|lost-mid-use|wrong-host|uploads|full|fills-up|busy|locked|public-on|public-expiring|public-active-elsewhere|public-unavailable|github-pending|github-denied|github-expired|github-applying|github-outdated|github-reconnect|github-clone-fails|windows
npm test             # Vitest
npm run typecheck
npm run build && npm run check-offline
```

In this WSL sandbox `/tmp/pytest-of-$USER` is root-owned, which breaks `tmp_path` fixtures.
Prefix with `TMPDIR=<writable dir>`.

There is no linter or formatter configured.

## Architecture

**The VM is abstracted, not Docker.** The guest is plain Ubuntu 24.04 running plain Docker on both
host platforms, so everything above the guest OS is platform-independent. The OS difference collapses
into the two provider classes.

```
host CLI (typer)  →  ApiClient (urllib)  →  127.0.0.1:39099 → the API (FastAPI in the VM)
                  →  VmProvider.exec()  →  guest: the runtime bootstrap, the token read
                                            /opt/omelet/projects/<id>/
host localhost:39080 ─────────────────────────→  traefik :39080 → routes by Host header
```

### Hard invariant: `host/` never imports `omelet_api`

The host ships as a PyInstaller-frozen binary and reaches the API over HTTP; the API ships as a
Docker image built from `runtime/omelet_api/` alone. `tests/host/test_no_api_import.py` and
`tests/runtime/api/test_no_host_import.py` enforce both directions by AST, anchored to `__file__`.
Every declared host dependency lands in that frozen binary, so the list stays short and the API's
FastAPI/uvicorn must never appear in it: `typer` for the CLI, `pywebview` for the desktop window,
and its native backends — `pythonnet` on Windows, the three `pyobjc-*` packages on macOS — marker-
scoped in `pyproject.toml` so neither platform's binding installs on the other.
`tests/host/test_host_dependencies.py` fails on a declared or imported dependency outside that
list. Names both packages need are declared twice and held equal by `tests/test_constants_agree.py`.

### Hard invariant: no platform branching outside `host/providers/`

`tests/test_no_platform_leak.py` greps every `host/**/*.py` and `runtime/omelet_api/**/*.py`
outside `providers/` for `sys.platform`, `platform.system()`, `os.name` and fails on any hit. Like
the two import-boundary tests it resolves the tree from `__file__` and asserts it scanned something
— a cwd-relative `Path("host")` passes vacuously from any other directory, which has now bitten
this repo three times. Reach for repo files that way in every test.
`host/providers/__init__.py` is the single place the host platform is resolved
(`get_provider()`, `default_install_dir()`).
Do not add an `if windows` anywhere else — push the difference into a provider method.

### Layers

- `host/core/provider.py` — the `VmProvider` Protocol plus `Completed` / `CheckResult` /
  `Diagnosis` value types. Providers are **duck-typed against the Protocol, not subclasses**.
- `host/providers/` — `Wsl2Provider` (shells `wsl.exe`), `LimaProvider` (shells `limactl`).
  Both take an injectable `runner` callable (defaults to `subprocess.run(..., capture_output=True)`),
  which is what makes them unit-testable.
- `host/client.py` — the only way the host reaches project logic: `ApiClient` over stdlib
  `urllib.request` (the host is PyInstaller-frozen, so it may never gain an HTTP dependency).
  It reads `/opt/omelet/api.token` through `provider.exec()`, turns every non-2xx body into
  `ApiError(code, message, status)`, a refused connection into `ApiUnavailableError`, and a
  failed job into `JobFailedError` carrying the guest's own stderr.
- `runtime/omelet_api/core/` — all platform-free logic: compose parsing, web detection, Traefik
  overlay generation, project identity, failure classification, guest lifecycle, sqlite state.
- `host/core/bootstrap.py` — the host's whole share of provisioning: unless
  `/opt/omelet/runtime.version` exists (or `repair=True`), it runs a base64 stub as one `bash -lc`
  argument that downloads `OMELET_RUNTIME_URL` in full and runs it, forwarding `OMELET_RUNTIME_REF`
  when set and `OMELET_RUNTIME_REPAIR=1` on repair. Values are checked against a shell-safe pattern
  because the argument is re-parsed by wsl.exe/ssh. `host/provision/` holds only the installer's
  `nginx-hello` smoke-test project (`tests/host/test_no_guest_assets.py`).
- `host/desktop/` — the GUI, a native window over the system webview (WebView2 on Windows,
  WKWebView on macOS) that replaced the old tkinter wizard. `view.py` is pure mappings from
  `host/core` values to what a screen needs — no provider, no client, nothing reaching the VM or
  the network (it does walk the local filesystem in `inspect_folder()`) — which is what makes
  it the only module here worth unit-testing. `api.py` is the only object JavaScript can reach —
  through `shell.py`'s `guarded()` functions, which refuse every call unless the local UI is the
  page showing — so it stays a thin, fixed list of methods taking scalars, with every real
  decision pushed into `view.py`. When the machine is running the same window shows the projects
  console (`localhost:<edge>`, entered with a handoff code); the console's own top bar links
  back to the local screens and opens itself in the system browser — there is no native menu.
  `jobs.py` runs one slow job at a time on a worker thread: `InstallState` is a JSON file, and
  two installs writing it at once would race. `ui/` holds the HTML, CSS, JS and bundled
  fonts and must work fully offline. `cli.setup` launches it; `--headless` still bypasses it
  entirely for a machine without a webview runtime.
- `runtime/install/get.sh` — the entrypoint: `resolve_ref` (explicit `OMELET_RUNTIME_REF` →
  installed ref on repair → highest `runtime-v*` tag by `sort -V`), downloads that ref's tarball,
  replaces `/opt/omelet/runtime/` with its `runtime/`, runs `install/install.sh <ref> [--repair]`.
- `runtime/install/install.sh` — Docker, `edge`, `/opt/omelet` permissions, token, the stack
  (recreating the api on a new token or repair), Node ≥ 22.20 from NodeSource, `/usr/local/bin/omelet`,
  `/etc/claude-code/CLAUDE.md`, then per account (root + `lib/login-users.sh`) the Codex block and
  `~/projects` link (`lib/install-agents.sh`) and
  `npx -y skills@1.5.26 add $SKILLS_SOURCE -s '*' -g -a claude-code codex -y </dev/null`, where
  `SKILLS_SOURCE` defaults to `ihorklymchukdev/omelet-skills`, unpinned on purpose (`OMELET_SKILLS_SOURCE`
  overrides it). Records the VM kind (`wsl`/`lima`/`other`) and first login user in
  `/opt/omelet/connect.json` for `GET /connect`. Writes `runtime.version` last.
- `runtime/cli/omelet.py` — the `omelet` command **inside** the VM, used by coding agents:
  `up`/`new`/`clone`/`status`/`logs`/`down` over the API with the guest token. One
  stdlib-only file, loaded by tests by path (`tests/runtime/cli/loader.py`); it shares constants
  with both sides, held equal by `tests/test_constants_agree.py`. `runtime/instructions/` holds
  what those agents read; the five skills themselves now live in the separate `omelet-skills`
  repository and are installed by `install.sh`'s `npx skills add`, not shipped in this tree. The
  runtime itself writes nothing into user repositories; the skills tell the coding agent to keep a
  project's own `docs/` and `AGENTS.md`. Design of the skills as they were before the extraction:
  `docs/superpowers/specs/2026-09-15-agent-skills-library-design.md`.
- `runtime/omelet_api/routes/` — the FastAPI app the host talks to. `app.py::create_app(config,
  runner, state)` is a factory on purpose (no module-level `app`, so importing it opens no sqlite
  file); `jobs.py` is the in-process job registry that keeps slow compose work off the request;
  `__main__.py` is the uvicorn entrypoint on `0.0.0.0:39099`. Every non-2xx body is
  `{"error": {"code": ..., "message": ...}}`, produced by one exception handler.
  Every route is on one `APIRouter` mounted twice: at `/` behind the bearer token
  (host, in-VM CLI) and at `/api` behind the `omelet_session` cookie plus a
  `Host`/`Origin` allowlist (the browser UI, reached through Traefik on the edge
  port). The desktop gets the browser a session through `POST /sessions/handoff`, and a
  signed-in page gets the system browser one through `POST /api/sessions/handoff`;
  see `docs/superpowers/specs/2026-09-21-web-ui-agent-prerequisites-design.md`.
  `DELETE /projects/{id}?purge=true` removes volumes and the folder; plain DELETE keeps them.
  `GET /connect` turns `connect.json` into `{vm, ssh}` for the console's agent guide; the SSH port
  is `omelet.yaml`'s declared `ssh.localPort`, held equal by `tests/test_constants_agree.py`.
- `runtime/omelet_api/core/exec.py` — `LocalRunner`, the in-VM twin of `VmProvider.exec`: same
  `Completed` contract, never raises. `runtime/omelet_api/core/config.py` — `ApiConfig`, the only
  place the domain and edge port may come from.
- `runtime/omelet_api/core/files.py` — project file transfer over HTTP (`POST/GET /projects/{id}/files`,
  `PUT/GET/DELETE /projects/{id}/files/{path}`), replacing the 32,767-character `wsl.exe`
  command-line ceiling with a streamed request body. `extract_archive` merges an uploaded
  tar.gz into the project directory rather than replacing it, and rejects any entry (absolute
  path, `..` escape, symlink/hardlink escaping the tree) via `tarfile`'s `filter="data"` plus an
  explicit absolute-path check, since that filter silently normalizes an absolute name instead
  of refusing it.
- `runtime/omelet_api/core/uploads.py` — resumable chunked uploads staged in `/opt/omelet/uploads`,
  outside `projects_root`; the staged file's size is the offset.
  `runtime/omelet_api/core/reconcile.py` lists project folders with no state row (made by a
  coding agent) for the UI's adopt flow.
- `runtime/omelet_api/core/cloud.py` / `account.py` / `sync.py` — the Omelet service
  (`OMELET_CLOUD_URL`, default `https://omelet.bridgie.chat/api`). `account.py` runs the
  device-code sign-in and keeps the tokens in `state.db`; `sync.py` gives each local project
  a service record (created with `client_ref = <device_id>/<local_id>`, deleted with it) on a
  thread `routes/__main__.py` starts — `create_app()` never starts one. Only the console is
  locked until sign-in (`GET /api/account`); the CLI and coding agents never wait on it.
  Service gaps are not worked around in our code; see
  `docs/superpowers/specs/2026-09-23-account-sign-in-sync-design.md` section 7.
- `runtime/omelet_api/core/github.py` / `github_link.py` — Connect GitHub. The API runs GitHub's
  Device Flow (`OMELET_GITHUB_CLIENT_ID`, no secret) and keeps the token in
  `/opt/omelet/github/token` (0600). It never reaches a user home itself: it writes a token-free
  `desired.json` with a rising `generation`, `omelet-github.path` runs
  `runtime/install/lib/github-apply.sh` as root, and that echoes the generation into
  `applied.json`. The UI says "ready" only when the two match; `applied.json` missing after 30 s
  means the runtime predates the feature. `POST /github/clone` passes the token to git only
  through the child's environment.
- `runtime/omelet_api/core/public.py` — a project's temporary public URL. The service owns the
  Cloudflare tunnel and its routing; the VM asks for a URL, keeps the token in
  `/opt/omelet/tunnel/token` (0640, present only while a URL is on; the directory is
  mounted read-only into the client) and starts the profile-gated `tunnel` service in `stack.yml` through compose. The service rewrites Host to the
  project's local hostname, so overlays are unchanged; apps that build absolute URLs from Host
  send public visitors to `*.127-0-0-1.sslip.io`. Only the console turns it on or off (POST
  and DELETE are mounted at `/api` alone); the CLI never shows it. Nothing about it is in
  `state.db`: the service is the record, the API keeps only a memory of it and re-reads
  the account's active URL (`GET /v1/tunnels/url`) on each sync pass. The
  `tunnel` network reaches the VM through its gateway, so every port published on 0.0.0.0
  (the api's, a project's `ports:`) is reachable from the tunnel client.
- `runtime/web/` — the browser UI at `localhost:<edge>`, shipped as the `omelet-web` nginx image
  and routed by Traefik below the API's `/api` router — a sibling of the API inside `runtime/`,
  never nested in the Python package. `packages/ui` is the kit (tokens, fonts,
  components; drawn from `docs/design/`, never from `host/desktop/ui`; its imports are held to
  React and its fonts by `packages/ui/test/boundary.test.ts`). `apps/console` is the app:
  `boot/boot.ts` decides signed-in / signed-out / needs-update / not-answering from `/api/health`
  and `/api/session`; `api/version.ts`'s `SUPPORTED_API` is held to the API's `API_VERSION` by
  `tests/test_constants_agree.py`. Assets always ship as files — the CSP refuses `data:` fonts
  (`build.assetsInlineLimit: 0` in `apps/console/vite.config.ts`). The page must work offline —
  `scripts/check-offline.mjs` fails the image build on any load from another host.
  `projects/view.ts` maps the API's `status`/`problem`/`job`/`empty` to one screen state for both the
  list and the project page; `projects/slugify.ts` mirrors the API's `_slug`, held equal by
  `tests/fixtures/slugify-cases.json` (read by Vitest and `tests/runtime/api/test_slug_cases.py`).
  `uploads/queue.ts` owns the chunked-upload protocol (resume at the API's offset, busy retry on
  the last chunk, hold on `disk_full`) with no React in it; one instance lives above the router in
  `uploads/QueueProvider.tsx`, so uploads carry on across screens but stop when the page closes.
  `desktop/desktop.ts` reads the `home=` address the desktop window adds to the handoff link
  (only `http://127.0.0.1:<port>`), which switches on the shell's Home button and "open in
  browser"; open external addresses with its `openExternal` (an anchor click), never
  `window.open` — WKWebView hands only link activations to the system browser.
  `/agents` and `/agents/:id` are the "Connect an agent" guide, all content in
  `apps/console/agent-guides/` (served at `/agent-guides/`) (`index.json` for order, `<id>/agent.json` with a `windows` and a `mac`
  block, `via_ssh`, steps and optional screenshots); `src/agents/catalog.ts` validates it and
  `content.test.ts` fails on a shipped file that doesn't parse or names a missing image.

### Things that will bite you

No `docs/architecture.md` exists to hold this list yet — gotchas live directly in this file until
that document is written. Add a new entry here when you hit one.

- `install.sh` step 4 runs `chmod -R g+rwX /opt/omelet` on every install, which widens
  `/opt/omelet/github/token`. The modes are reasserted right after the sweep; keep that order.
- Login accounts are never the API's uid 1000: WSL2 has only root, and Lima's user carries the
  macOS uid. The API writes into projects through the docker group, so anything it creates there
  needs `umask 002` (see `clone_argv`), and `/etc/gitconfig` trusts `safe.directory '*'`.
- `runtime/web/apps/console/src/projects/slugify.test.ts` reaches
  `tests/fixtures/slugify-cases.json` by counting `../` segments from its own location, and that
  count must agree with `runtime/web/Dockerfile`'s `WORKDIR` (and the `COPY --from=fixtures`
  destination inside it). Changing one without the other passes `npm test`, which runs against the
  checkout, and fails only inside the image build.
- WSL's service can hang (`Wsl/Service/CreateInstance/0x8007274c`, often after sleep) while
  `wsl -l --running` still lists `omelet-vm`, so "listed as running" never proves it answers.
  `Wsl2Provider` raises `VmUnresponsive` from `exec()` and `_meta()` on that code;
  `recover()` tries `--terminate` first. Only `--shutdown` is sure to clear it, and that stops
  every distro and Docker Desktop, so the desktop app asks before using it.
- pywebview injects `window.pywebview.api` into every page the window loads, including the
  console served from the VM. `host/desktop/shell.py` refuses bridge calls and pushed events
  unless the local UI is showing; a new `DesktopApi` method is covered automatically. Keep
  `create_window(js_api=None)` and register functions with `window.expose()`: pywebview
  resolves a dotted call name from `js_api` with plain `getattr`, so any object there lets a
  page walk `home.__func__.__globals__` past the guard. It checks the page showing, not the
  sender, and the local UI is plain `http://127.0.0.1:<port>`; the spec's "What the guard
  does not cover" names the residual race. Any page can also open a new window, which pywebview
  hands to `webbrowser.open` (`os.startfile` on Windows), so `_default_start` wraps it with
  `web_links_only` — http(s) only.
- The `tunnel` service is behind a compose profile. A plain `docker compose -f stack.yml up -d`
  or `pull` never touches it; the API's own compose calls pass `--profile tunnel`, and so must
  anything else that means to include it.

- `host/desktop/ui/index.html`'s CSP must keep `script-src 'self' 'unsafe-eval'`. pywebview
  builds every bridge method with `new Function(...)` in the api.js it injects after load, so
  under a bare `default-src 'self'` WebKit refuses the call: `window.pywebview.api` stays `{}`,
  `pywebviewready` never fires, `refresh()` never runs, and the window opens showing only its
  background colour — with nothing on stderr, since the injection is fire-and-forget on the
  Python side. WebView2 runs host-injected script outside the page's CSP, so this is invisible
  on Windows and fatal on macOS. `tests/host/desktop/test_ui_assets.py` pins it.

- `runtime/web/apps/console/agent-guides/` is outside Vite's build output (`publicDir` is dev-only):
  the Dockerfile copies it into the nginx root, so `npm run build` + `preview` shows no guides.
  nginx's `/agent-guides/` location has no SPA fallback, so its path must never be a console
  route prefix: `/agents/` once turned every guide reload into a bare 404. `content.test.ts` checks.

## Testing conventions

- No test spawns `wsl.exe`/`limactl`, touches a real VM, or reaches the network. Provider tests
  inject a `FakeRunner` that records `argv` and returns scripted bytes; CLI tests monkeypatch
  `cli._provider_factory` with a `FakeProvider`. Assertions are about **constructed argv and decoded
  output**, not side effects. Runtime shell scripts are the exception that still runs a real
  process: they are exercised with `bash` against fakes on `PATH` (see the runtime-scripts bullet
  below).
- `tests/runtime/api/test_acceptance_detection.py` runs the five real-world compose shapes in
  `tests/fixtures/compose/` through `detect_web` — add a fixture there when changing detection rules.
- Runtime shell scripts are tested under `tests/runtime/`: `bash -n` plus text assertions over
  `install.sh`, `resolve_ref` sourced from `get.sh` with a fake `git` on `PATH`, and
  `install-agents.sh`/`login-users.sh` run against temporary homes. Nothing there touches the
  network; the live-VM acceptance run covers apt, NodeSource, npm and ghcr.

## Conventions

- Python 3.12+, `from __future__ import annotations`, frozen dataclasses for value types.
- Host runtime deps are `typer`, `pywebview`, and `pywebview`'s marker-scoped native backends
  (`pythonnet` on Windows, `pyobjc-*` on macOS); the API's are declared in
  `runtime/omelet_api/pyproject.toml`. Keep the host list that short unless there's a reason.
- Packaging lives in `packaging/<platform>/`: Inno Setup on Windows (`build.ps1`), `pkgbuild`/
  `productbuild` on macOS (`build.sh` → `dist/OmeletSetup-<version>.pkg`). Both freeze with
  PyInstaller one-dir and smoke-test the frozen binary (`version`, then `selfcheck`) *before*
  packaging it. The mac build is native-arch only. Manual release gates:
  `docs/installer-test-matrix.md`, `docs/macos-install-test-matrix.md`.
- Live WSL2 run needs an Ubuntu 24.04 rootfs tarball path in `OMELET_ROOTFS` (README has the
  current download URL); the provider factory reads it, and `omelet vm create` without it raises
  `ValueError`. The value must be a Windows path — it goes straight to `wsl.exe --import`.
