# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A PoC (`omelet`) that creates a managed Linux VM (Ubuntu 24.04), installs Docker inside it, runs
any `docker-compose` project in the guest, and hands back a working URL on the host. Windows/WSL2
is the primary platform. macOS/Lima is **confirmed once, still mostly unverified** (a VM booted and
finished the runtime install on Apple Silicon; nobody has recorded a full `create_vm` → `verify`
run) — see `docs/macos-status.md`.

Each layer has its own CLAUDE.md with the detail; read the one for the tree you are touching:
`host/`, `host/desktop/`, `runtime/`, `runtime/install/`, `runtime/omelet_api/`, `runtime/web/`.

## Architecture shape: the host is a VM shell, the runtime is everything inside

Two halves, shipped and versioned independently:

- **Host** (`host/`, a PyInstaller-frozen desktop binary) — creates and runs the VM, runs one
  bootstrap command in it (fetch `OMELET_RUNTIME_URL` → `bash`), reads the token, forwards ports,
  and talks to the API over HTTP. It holds no guest files and no knowledge of what the runtime
  installs.
- **Runtime** (`runtime/`) — everything inside the VM: Docker, the Traefik + API stack, the browser
  console, the in-VM `omelet` CLI, coding-agent instructions and skills. Released as `runtime-v*`
  tags. The same `get.sh` provisions a cloud VM.

```
host CLI / desktop  →  ApiClient (urllib)  →  127.0.0.1:39099 → API (FastAPI in the VM)
                    →  VmProvider.exec()   →  guest: runtime bootstrap, token read
host localhost:39080 ──────────────────────→  Traefik :39080 → projects, console (/), API (/api)
public URL (optional) → Omelet service's Cloudflare tunnel → `tunnel` container → Traefik
```

**The seam is a fixed contract and nothing else:** token path `/opt/omelet/api.token`, API port +
`/health`'s `api` number, edge port, `/opt/omelet/runtime.version`, `/opt/omelet/host.json` (host →
VM, the API numbers it accepts), `runtime/release.json`'s `api`. A change inside the VM must
never need a host release; if it does, the logic is on the wrong side. Before adding a host CLI
command or a host-side guest asset, check the phased plan: Phase 1 (MVP) is moving toward no host
CLI and SSH-only access, not away from it.

## Cross-cutting invariants (enforced by tests)

- **`host/` never imports `omelet_api`, and vice versa.** They ship as separate artifacts (frozen
  binary vs. Docker image). `tests/host/test_no_api_import.py`,
  `tests/runtime/api/test_no_host_import.py`.
- **No platform branching outside `host/providers/`.** `tests/test_no_platform_leak.py` fails on
  `sys.platform` / `platform.system()` / `os.name` anywhere else in `host/` or `runtime/omelet_api/`.
  Push the difference into a provider method.
- **Shared constants are declared on each side and held equal** by `tests/test_constants_agree.py`
  (ports, `SUPPORTED_API` vs `API_VERSION`, SSH port). The runtime release number is not one of
them: it lives only in the `runtime-v*` tag (see `runtime/CLAUDE.md`). When you change one,
  run it.
- **Tests reach repo files via `__file__`, never a cwd-relative `Path("host")`** — that passes
  vacuously from another directory and has bitten this repo three times. Boundary tests also assert
  they scanned something.

## Commands

Needs Python 3.12+ — a bare `python3` may be older and fails at collection with an f-string
`SyntaxError`. Use the repo's `.venv` (`.venv/bin/python -m pytest`).

```bash
pip install -e ".[dev]"

python3 -m pytest -q                                   # full Python suite (well under a minute)
python3 -m pytest tests/runtime/api/test_project.py -q # one file
python3 -m pytest -k classify -q                       # by name
```

`pyproject.toml` puts `runtime/` on `pythonpath`, so tests import `omelet_api` directly. All Python
tests live under the top-level `tests/` (mirroring `host/` and `runtime/`); the web UI has its own
Vitest suite — see `runtime/web/CLAUDE.md`. There is no linter or formatter configured.

In the WSL sandbox `/tmp/pytest-of-$USER` is root-owned, which breaks `tmp_path`; prefix with
`TMPDIR=<writable dir>`.

## Testing conventions

- No test spawns `wsl.exe`/`limactl`, touches a real VM, or reaches the network. Fakes are injected
  (`FakeRunner` recording argv, `FakeProvider`, fake cloud/GitHub servers); assertions are about
  **constructed argv and decoded output**, not side effects.
- The exception: runtime shell scripts run under real `bash` against fakes on `PATH`
  (see `runtime/install/CLAUDE.md`). The live-VM acceptance run covers apt, NodeSource, npm, ghcr.

## Conventions

- Python 3.12+, `from __future__ import annotations`, frozen dataclasses for value types.
- Packaging lives in `packaging/<platform>/` (Inno Setup on Windows, `pkgbuild`/`productbuild` on
  macOS); images in `packaging/images/build.sh`. Manual release gates: `docs/release-testing.md`.
- Gotchas go in the CLAUDE.md of the layer they belong to, under "Things that will bite you". Add
  one when you hit it.

## Docs

- `docs/README.md` indexes the human how-tos: `development.md`, `building.md`, `releasing.md`,
  `vm.md`, `release-testing.md`, `macos-status.md`. Keep them in step when a command or path they
  name changes.
- `docs/superpowers/` — the original blueprint plus every design spec and plan (dated filenames).
  `.superpowers/sdd/` (gitignored) is the per-task execution ledger.
- `docs/architecture.md` and `docs/roadmap.md` **do not exist** — check `ls docs/` before trusting a
  pointer to either. Phase 1 (MVP) fixes what exists (repo split, no host CLI, SSH-only access,
  autostart, self-update, signing) and adds accounts, a web app, secrets and public URLs; Phases 2
  and 3 are VPS deploys, paid plans and an own cloud.
- Top-level `agent/`, `engine/`, `tests/agent/`, `tests/engine/` are untracked `__pycache__`
  leftovers from old layouts — ignore them.
