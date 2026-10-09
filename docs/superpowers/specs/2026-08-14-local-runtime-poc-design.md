# Local Runtime PoC — Design Spec

**Date:** 2026-08-14
**Status:** Approved for planning
**Source blueprint:** `task.md`

## 1. Goal

Bring up a managed Linux VM, install Docker inside it, run an arbitrary
`docker-compose` project, and hand back a working URL on the host. No UI, no AI,
no deploy. Two host platforms: Windows 11 + WSL2 (primary) and macOS 13+ Apple
Silicon (parity, unverified this session).

The abstraction is the **VM**, not Docker. Everything above the guest OS is
identical on both platforms; the OS difference collapses into two small
providers.

## 2. Scope for this session

- Build the full architecture (providers, core, CLI, local API, guest bootstrap).
- Prove the WSL2 vertical slice (M0–M4) by **unit tests + dry-run command
  construction**. The multi-GB live rootfs/image download and browser check are
  left for the human to run.
- Extend toward M5–M8 (state/restart survival, concurrency, auto-detection,
  lifecycle) as budget allows.
- `LimaProvider` is written for parity against the same contract, clearly marked
  **UNVERIFIED — no macOS available**.

Out of scope (per blueprint §10): GUI, agent integration, deploy/VPS/secrets,
snapshots/backups (but `state.py` is structured to allow them), Linux-as-host,
code signing.

## 3. Architecture

```
HOST (Windows / macOS)
  runtime-cli (Python / Typer)
    └── VmProvider (Protocol)
          ├── Wsl2Provider   → wsl.exe   (sibling distro "runtime-vm")
          └── LimaProvider   → limactl   (VM "runtime-vm")
    core/  — stack-agnostic lifecycle, overlay, detect, state, diagnose
    api/   — 127.0.0.1 JSON-RPC server (attachment point for GUI/MCP later)
  localhost:39080  ──┐
                     │
GUEST: Ubuntu 24.04 (identical both OSes)
  dockerd
  ├── traefik :80        ← single entry point, routes by Host label
  ├── <proj>_web ...     (labels via generated overlay)
  └── <proj>_db ...
  /opt/runtime/projects/<id>/   ← canonical project files live here
```

Exactly one port is forwarded: `guest:80 → host:39080`. Traefik routes by
hostname. The user never learns the word "port".

## 4. Provider contract (the only place `if sys.platform` may appear)

```python
class VmProvider(Protocol):
    def is_supported(self) -> Diagnosis: ...   # hypervisor, versions, privileges, conflicts
    def exists(self) -> bool: ...
    def create(self) -> None: ...              # fetch rootfs, create VM, systemd fixup
    def start(self) -> None: ...
    def stop(self) -> None: ...
    def destroy(self) -> None: ...
    def exec(self, argv: list[str], *, root=False) -> Completed: ...
    def forward(self, guest_port: int, host_port: int) -> None: ...
```

Everything else (install Docker, Traefik, `compose up`) is implemented **once**
on top of `exec`. A test greps the tree and fails if `if sys.platform` /
`sys.platform ==` appears outside `providers/`.

### 4.1 Wsl2Provider specifics

- Creates a **sibling** distro named `runtime-vm` via
  `wsl.exe --import runtime-vm <dir> <rootfs.tar.gz> --version 2`. Works
  identically whether the CLI runs on Windows or inside another WSL2 distro
  (interop).
- **Encoding:** `wsl.exe` *meta* commands (`--version`, `--list`,
  `--status`) emit **UTF-16LE** (NUL-interleaved bytes). `wsl -d <dist> -- <cmd>`
  passes the Linux command's **UTF-8** through. The provider decodes per command
  class; a small helper strips/decodes UTF-16 when needed.
- **systemd fixup is a `create()` state-machine step, not documentation:** write
  `/etc/wsl.conf` `[boot]\nsystemd=true`, `wsl --terminate runtime-vm`, then
  continue. Without it there is no working `docker.service` (Risk #1).
- `exec(root=True)` uses `wsl -d runtime-vm -u root -- ...`.
- `forward()` for the single edge port relies on WSL2 default
  `localhostForwarding=true`; guest binds `0.0.0.0`. Extra raw-TCP forwards
  (DBeaver use case) are additional guest→host forwards recorded in state.

### 4.2 LimaProvider specifics (UNVERIFIED)

- `limactl start --name=runtime-vm --tty=false runtime.yaml`; `exec` via
  `limactl shell runtime-vm ...`. `runtime.yaml` from blueprint §4 (`vmType: vz`,
  rosetta on, explicit `portForwards: 80→39080`, `mounts: []`).
- For release, `limactl` would be bundled (Apache-2.0); Homebrew is a dev-only
  convenience. Not required this session.

### 4.3 `is_supported()` → `Diagnosis`

`Diagnosis` is a structured, human-readable result: a list of checks each with
`ok: bool`, `label`, and a `fix` hint. WSL2 checks: Windows build, WSL feature +
VirtualMachinePlatform enabled, `wsl --version` present (else `wsl --update`),
virtualization in BIOS, Hyper-V/VirtualBox/AV conflicts (Risk #3, detected
separately with a specific message). `runtime doctor` renders it (M0).

## 5. Guest bootstrap (one idempotent script, both OSes)

`guest/bootstrap.sh`, safe to re-run:
1. `docker-ce` from Docker's official repo (not Ubuntu's `docker.io`).
2. `systemctl enable --now docker`.
3. `docker network create edge` (idempotent).
4. `mkdir -p /opt/runtime/projects`.
5. Traefik as a container: docker provider, entrypoint `:80`, `--restart=always`,
   on the `edge` network, config from `guest/traefik.yml`.
6. Write `/opt/runtime/.bootstrapped` with a **version number**. App update → new
   bootstrap → migration without recreating the VM. `bootstrap()` in core reads
   the marker and skips/upgrades accordingly (M2 idempotency).

## 6. Routing & project contract

### 6.1 Overlay generation (`core/overlay.py`)

The runtime **never edits the user's compose file**. It generates
`.runtime/overlay.yml` from `.runtime/project.yml` and runs:

```
docker compose -f docker-compose.yml -f .runtime/overlay.yml up -d
```

Overlay attaches the `edge` network and a Traefik router to each declared web
service:

```yaml
services:
  <web_service>:
    networks: [default, edge]
    labels:
      traefik.enable: "true"
      traefik.http.routers.<router>.rule: "Host(`<host>`)"
      traefik.http.services.<router>.loadbalancer.server.port: "<internal_port>"
networks:
  edge:
    external: true
```

### 6.2 `.runtime/project.yml`

```yaml
id: myproj
web:
  - service: app     # which service serves HTTP
    port: 8080       # internal container port
```

- `web` is a **list** → multiple HTTP services (`myproj.<domain>`,
  `api.myproj.<domain>`).
- Non-HTTP services (redis, workers): no labels, run on the project `default`
  network.
- `build:` instead of `image:` must work; first build is slow → stream progress.
- `.env` / `env_file`: located in project root, never touched.

### 6.3 Auto-detection (`core/detect.py`, M7)

Fill missing `web` fields from the compose file: single service → take it; else
require explicit `web`. Port from the service's `ports:` mapping, else `EXPOSE`
in a referenced Dockerfile, else a sane default. Must correctly handle the five
acceptance shapes (PHP-FPM+nginx+MySQL, Node+Postgres, Python+Redis, `build:`,
two HTTP services) with **zero stack-specific code**.

### 6.4 Domain

Default `‹proj›.local.eggie.space` (public DNS, resolves to `127.0.0.1`
everywhere including Safari; needs internet). Documented fallback: direct
`localhost:PORT` forward for a single active project. Configurable so a
`/etc/hosts` mode can be added later.

## 7. Project files live inside the VM

Canonical copy in the guest at `/opt/runtime/projects/<id>` — never a host mount
(9p/`/mnt/c` is several times slower; virtiofs on mac is not native). Host
access over a network share (`\\wsl$\runtime-vm\...` on Windows; reverse mount /
SSHFS on mac) — read-access convenience, not the execution path. Archive import:
copy the archive into the guest, unpack **there**, never on the host.

## 8. State (`core/state.py`, SQLite)

Tables: `projects` (id, path-in-guest, status, domain), `web_routes`
(project_id, service, internal_port, host), `forwards` (project_id, service,
guest_port, host_port), and room for `snapshots` later. Stored under the OS
app-data dir (`%LOCALAPPDATA%\Runtime` / `~/Library/Application Support/Runtime`).
Port allocation: a range from 39080; chosen port recorded so it survives
restarts and avoids collisions (Risk #5). Data must survive a VM restart (M5).

## 9. Repository structure

```
runtime/
├── cli.py                # Typer: doctor, up, down, status, logs, destroy, vm ...
├── core/
│   ├── provider.py       # Protocol + platform factory + Diagnosis/Completed types
│   ├── project.py        # compose lifecycle (stack-agnostic)
│   ├── overlay.py        # overlay from project.yml
│   ├── detect.py         # auto-detect web service + port
│   ├── state.py          # SQLite
│   └── diagnose.py       # preconditions + human-readable messages
├── providers/
│   ├── wsl2.py
│   └── lima.py
├── guest/
│   ├── bootstrap.sh
│   └── traefik.yml
├── templates/            # NOT part of the engine — starter compose files only
└── api/
    └── server.py         # 127.0.0.1 JSON-RPC
```

Language: Python for the whole core. Local API server built in from the start so
the CLI is not rewritten for a later GUI. `providers/` is the **only** place
platform branching may appear.

## 10. Error handling & failure classification (Risk #6)

The runtime does not fix broken projects, but must distinguish clearly:
- **"failed to start"** (compose error, missing `.env`, image pull failure) vs
- **"started, but the service is crashing"** (restart loop, non-zero exit).
Expose logs, exit codes, and restart-loop causes through the CLI (and the same
data through the API — the interface an agent consumes later).

Specific recognized conditions with clear messages (not raw stack traces):
- `exec format error` / missing arm64 image on Apple Silicon (Risk #4) —
  recognize and explain, don't die.
- Antivirus silently breaking VM creation on Windows (Risk #3).
- Port 39080 already in use → pick next in range, record it (Risk #5).

## 11. Milestones (build order)

| # | Deliverable | Done when |
|---|---|---|
| M0 | `runtime doctor` | honestly reports what's missing + how to fix, both OSes |
| M1 | `runtime vm create` | VM exists, `exec("uname -a")` works |
| M2 | bootstrap | `docker info` works in guest, idempotent on re-run |
| M3 | traefik + port | `curl localhost:39080` → Traefik 404 (success) |
| M4 | `runtime up <dir>` | single-service nginx compose resolves in browser |
| M5 | state + deps | multi-service compose w/ DB; data survives VM restart |
| M6 | concurrency | 3+ projects, different stacks, simultaneous, no collisions |
| M7 | auto-detection | `detect.py` guesses web service + port across all test files |
| M8 | lifecycle | stop/start VM, destroy a project, full reset |

This session: M0–M4 code + unit/dry-run verified; M5–M8 as budget allows. Human
runs the live multi-GB path and the 5-compose acceptance test.

## 12. Testing strategy (per repo testing rules)

Test logic that can produce a wrong result; do not chase coverage or re-assert
the framework.

- `detect.py`: auto-detection across the five acceptance compose shapes + the
  "ambiguous, must ask" case.
- `overlay.py`: label/network/router generation; multi-web; non-HTTP service
  gets no labels; user compose is never mutated.
- `state.py`: port allocation from range, collision avoidance, restart survival
  (reload from disk).
- Providers: **command construction** with mocked `subprocess` (exact argv for
  `create`/`exec`/`forward`); the UTF-16/UTF-8 decode helper.
- `diagnose.py`: parsing check results into `Diagnosis` with fix hints.
- Boundary test: no `if sys.platform` outside `providers/`.
- Failure classification: mapping compose/inspect output to
  "failed to start" vs "crash-looping".

The live rootfs/image run, browser check, and cross-OS runs are the human's.

## 13. Risks (budget time)

1. **systemd in WSL** — built into `create()` state machine (see §4.1).
2. **First run** downloads gigabytes — measure real elapsed time; 15+ min is a
   product-kill signal. (Human runs this; PoC surfaces progress, never goes
   silent.)
3. **Antivirus on Windows** silently breaks VM creation — catch specific error.
4. **Apple Silicon vs x86 images** — recognize `exec format error`, explain.
5. **Port 39080 in use** — range + recorded choice.
6. **Compose files that don't self-start** — classify failure vs crash, expose
   logs/exit codes.

## 14. Acceptance test (the most important thing)

Five arbitrary GitHub `docker-compose.yml` files not written by us — PHP-FPM +
nginx + MySQL, Node + Postgres, Python + Redis, one using `build:`, one with two
HTTP services — each comes up with a single command and returns a working URL,
on Windows and Mac, with **zero** stack-specific code in the runtime. If any
required adding something to the core, the abstraction is wrong. How many pass
on the first attempt is the real stack-agnosticism metric.
