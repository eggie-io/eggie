# Architecture

Eggie gives a non-technical user a Linux VM on their own computer that runs any
`docker-compose` project and hands back a URL for it. Users and their coding agents (Claude
Code, Codex, Cursor) work inside that VM. This page is the map; the `CLAUDE.md` in each folder
has the detail.

## The two halves

```
 host computer (Windows / macOS)
 ┌───────────────────────────────────────────────────────────────────────┐
 │ Eggie desktop app ──wsl.exe / limactl──→ VM: create, start, bootstrap │
 │                   ──127.0.0.1:39099────→ api   (token)                │
 │ browser / window  ──127.0.0.1:39080────→ traefik                      │
 │ coding agent      ──WSL shell / SSH────→ login account in the VM      │
 └───────────────────────────────────────────────────────────────────────┘
 eggie-vm (Ubuntu 24.04)
 ┌───────────────────────────────────────────────────────────────────────┐
 │ traefik :39080 ── /                         → web   (console)         │
 │                ── /api                      → api   (session cookie)  │
 │                ── <id>.127-0-0-1.sslip.io   → project containers      │
 │ api :39099 ── docker socket → project compose stacks                  │
 │            ── /opt/eggie: api.token, state.db, projects/              │
 │            ── https → Eggie service, GitHub                           │
 │ tunnel (cloudflared, only while a public URL is on) → Eggie service   │
 │ login account: shell, `eggie` CLI, coding-agent instructions, skills  │
 └───────────────────────────────────────────────────────────────────────┘
```

- **Host** (`host/`) is the desktop app: one PyInstaller-frozen binary per platform. It creates
  and starts the VM, runs one bootstrap command in it, reads the API token, and talks to the
  API over HTTP. It holds no guest files and knows nothing about what the runtime installs.
- **Runtime** (`runtime/`) is everything inside the VM: Docker, the Traefik + API + console
  stack, the in-VM `eggie` CLI, coding-agent instructions and agent manifests. It is released
  as `runtime-vX.Y.Z` tags. The same `get.sh` would provision a cloud VM.

The two halves ship and update on their own. **A change inside the VM must never need a host
release**; if it does, the logic is on the wrong side.

## The host ↔ runtime contract

This is everything the two halves agree on. It is small on purpose:

| Item | Value | Direction |
|---|---|---|
| Bootstrap | fetch `runtime/install/get.sh` from `main`, run it with `bash` | host → VM |
| Installed marker | `/opt/eggie/runtime.version` | VM → host |
| API token | `/opt/eggie/api.token` | VM → host |
| API | port `39099`, `GET /health` returns `api` (wire-protocol number) | VM → host |
| Accepted API numbers | `/opt/eggie/host.json` (`supported_api`) | host → VM |
| Release's API number | `runtime/release.json` `api` | runtime tag → VM updater |
| Edge port | `39080` (Traefik) | VM → host |
| SSH port | `39022` (Lima) | VM → host |

Shared constants are declared on each side and kept equal by `tests/test_constants_agree.py`.
`host/` never imports `eggie_api` and vice versa (they ship as a frozen binary and a Docker image).

## Host: the desktop app

| Part | Role |
|---|---|
| `host/core/provider.py` | `VmProvider` protocol: create, start, stop, exec, forward, diagnose |
| `host/providers/` | `Wsl2Provider` (shells `wsl.exe`) and `LimaProvider` (shells `limactl`, VM in `eggie.yaml`). The only place platform differences live |
| `host/core/install.py` | Setup steps: preflight → (Mac) install Lima → (Win) enable WSL, reboot gate → fetch image → create VM → (Mac) SSH alias → bootstrap → connect → verify → finish |
| `host/core/bootstrap.py` | The host's whole share of provisioning: run `get.sh` unless the marker exists |
| `host/client.py` | `ApiClient` (stdlib `urllib`), the only way the host reaches project logic |
| `host/desktop/` | pywebview window and tray. Shows local setup screens, then the VM's console in the same window |
| `host/cli.py` | `eggie setup / doctor / vm / up / …`: support and packaging tool, shrinking over time |

## Runtime: inside the VM

### Provisioning

`get.sh` resolves a ref (the newest `runtime-v*` tag whose `release.json` `api` the host accepts),
downloads it into `/opt/eggie/runtime/`, and runs `install.sh`. `install.sh` installs Docker,
starts the stack, installs git/gh/Node, the in-VM `eggie` CLI, agent instructions and skills
(from the separate `eggie-skills` repo), and writes `runtime.version` **last**, so a failed install
never looks installed. Everything runs under `flock /opt/eggie/update.lock`.

### The stack (`runtime/stack.yml`)

| Service | Image | Role |
|---|---|---|
| `traefik` | Traefik | Edge on `39080`: `/` → console, `/api` → API, `Host(<id>.127-0-0-1.sslip.io)` → project |
| `api` | `eggie-api` (`runtime/eggie_api/`) | FastAPI on `39099`. Owns projects, jobs, sessions, accounts, GitHub, public URLs, secrets. Talks to Docker through the socket |
| `web` | `eggie-web` (`runtime/web/`) | React console served by nginx. Works offline |
| `tunnel` | cloudflared | Only under the `tunnel` compose profile, while a public URL is on |

### The API

The API keeps the logic in `core/` (no FastAPI imports) and the routes in `routes/`. Every route
is mounted twice:

- at `/` behind the bearer token, for the host and the in-VM `eggie` CLI;
- at `/api` behind an `eggie_session` cookie plus a `Host`/`Origin` allowlist, for the browser
  console. The desktop app gets the browser a session with a one-time handoff code.

State lives in `/opt/eggie/state.db` (sqlite). Slow compose work runs as in-process jobs.

### How a project runs

1. A project is a folder under `/opt/eggie/projects/` with a compose file. It gets there by
   upload, `git clone`, `eggie new`, or a coding agent creating it (the console offers to adopt
   folders the API doesn't know).
2. The API parses the compose file and detects the web service and its port (`core/detect.py`).
3. It writes a Traefik overlay (`core/overlay.py`) that joins that service to the `edge` network
   with a `Host(<project-id>.127-0-0-1.sslip.io)` rule, then runs `docker compose up` with the
   project's file plus the overlay. The project's own files are never edited. Secrets
   reach containers as environment variables through the compose process environment and win over
   the project's own `.env`; a non-empty literal a service sets in the compose file wins over them; the
   overlay lists names only.
4. `*.127-0-0-1.sslip.io` resolves to `127.0.0.1`, so the host's browser reaches Traefik through
   the forwarded edge port. No hosts-file edits.
5. After a VM reboot, `resume_projects()` restarts the projects that were running.

### Root-side helpers

The API never writes into a user's home. When it needs root work done it writes a request file
under `/opt/eggie/`, and a systemd path unit runs a root script:

| Request | Unit | Script |
|---|---|---|
| `github/desired.json` | `eggie-github.path` | `install/lib/github-apply.sh`: configures git/gh for login accounts |
| `agent-status/check`, `setup/<id>` | `eggie-agents.path` | `install/lib/agents-run.sh`: detects connected agents, runs agent setup |

### Coding agents

Agents run as a login account in the VM. On Windows they open the VM's WSL distro; on macOS
they connect over SSH (`Host eggie` in `~/.ssh/config`). Each agent is a manifest folder in
`runtime/agents/` (where its instructions go, how to detect it, its setup command, and the guide
the console shows). Agents use the in-VM `eggie` CLI to start and inspect projects, and read
`runtime/instructions/eggie.md`.

## Outside services

| Service | Used for | From |
|---|---|---|
| GitHub (`eggie-io/eggie`) | `get.sh` on `main`, `runtime-v*` tags, `app-v*` releases | VM, host |
| ghcr.io | `eggie-api` and `eggie-web` images (amd64 + arm64) | VM |
| Eggie service (`app.eggie.io`) | Sign-in, project sync, public-URL tunnels | API |
| GitHub OAuth (Device Flow) | Connecting the user's GitHub account | API |

## Updates

- **Runtime.** At every VM boot, `eggie-update.service` runs `get.sh` in update mode. It moves to the
  newest tag whose `api` is in `host.json`, pulls images before touching anything, and rolls back
  if `install.sh` fails. A host that sees an API it can't speak triggers the update at once.
- **Desktop app.** On launch it checks `app-vX.Y.Z` GitHub releases and offers **Update**.
- **Two version numbers.** The release number lives only in the tag. `API_VERSION` is the
  wire protocol; bump it only for an incompatible change to a route the host calls, and ship a
  host that accepts it first. See [releasing.md](releasing.md).

## Platforms

| | Windows | macOS |
|---|---|---|
| VM | WSL2 distro `eggie-vm`, imported from an Ubuntu 24.04 rootfs | Lima VM `eggie-vm` (`host/providers/eggie.yaml`) |
| Port forwarding | WSL `localhostForwarding` | Lima `portForwards`, loopback |
| Agent access | WSL distro (Codex needs `eggie-vm` as the default distro) | SSH on `39022` |
| Status | Primary, tested | Mostly unverified, see [macos-status.md](macos-status.md) |

## Trust boundary

The VM is the boundary. Everything inside it (the Docker socket, every project container, every
login account, every agent) is root-equivalent. All host forwards are loopback-only. A public URL
is the one way in from outside the machine. The API token tells the host apart from a stray
container; it is not authentication. Secrets live in `state.db`, readable by anything root-equivalent in the VM,
coding agents included: they protect against leaks into files, git and chat, not against the
agent. Details: [security.md](security.md).

## Where things live

```
host/              desktop app: providers, setup, ApiClient, desktop window
packaging/         Windows installer, macOS pkg, image build script
runtime/install/   get.sh, install.sh, boot updater, root helpers, systemd units
runtime/stack.yml  the compose stack inside the VM
runtime/eggie_api/ the API
runtime/web/       the browser console
runtime/cli/       the in-VM `eggie` command
runtime/agents/    one manifest per coding agent
runtime/instructions/  what coding agents read
tests/             all Python tests, mirroring host/ and runtime/
docs/superpowers/  dated design specs and plans (history, not current state)
```
