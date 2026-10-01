# Host is a VM shell, the engine is everything inside — design

Date: 2026-09-14. Status: approved in brainstorming, pending spec review.

## Problem

PR #3 put every guest asset under `host/provision/` — `bootstrap.sh`, `stack.yml`, the guest
`omelet.py`, `install-agents.sh`, `login-users.sh`, `omelet.md`, the `omelet-setup` skill — and the
frozen host pushes all seven into the VM, gated by `host/core/constants.BOOTSTRAP_VERSION`. Changing
one line of a skill or of the guest CLI therefore needs a new desktop build, and none of it is
reusable on a cloud VM that has no desktop host.

## Goal

- The host is a VM shell only: create/run the VM, run one bootstrap command in it, read the
  token, forward ports, talk HTTP.
- Everything inside the VM is the **engine**, versioned and released independently of the host,
  installable by the same entrypoint on a cloud VM.
- Skills are installed with `npx skills add`.
- CLAUDE.md states this shape as the governing architecture rule.

## Decisions

| # | Decision |
|---|---|
| 1 | The host does not know or care which engine version runs. |
| 2 | The host carries a tiny bootstrap; the source it fetches is configurable (`OMELET_ENGINE_URL`). |
| 3 | The source is this repository on GitHub (made public). The agent image is always pulled from ghcr. |
| 4 | An engine version is a git tag `engine-vX.Y.Z`. |
| 5 | Re-running host setup with the engine already installed does nothing (except repair, below). |
| 6 | No automatic or self-update for now — see `docs/future/engine-self-update.md`. |
| 7 | The engine does not install Claude Code or Codex. |
| 8 | Layout: a new `engine/` folder in this repo; `agent/` stays where it is. Both together are the engine. |
| 9 | Omelet's own skills install with `npx skills add` from the unpacked engine folder, not from a GitHub URL. |

## 1. The contract between host and engine

The only things both sides agree on:

| Item | Value |
|---|---|
| Token file | `/opt/omelet/agent.token`, readable by root |
| Agent API | `127.0.0.1:39099`; `GET /health` returns `"api": <int>` |
| Edge | port `39080`, domain `127-0-0-1.sslip.io` |
| Installed marker | `/opt/omelet/engine.version` — the host checks presence only, never the value |
| Engine entrypoint | the script at `OMELET_ENGINE_URL`, run as root with `bash` |
| Entrypoint env | `OMELET_ENGINE_REF` (optional), `OMELET_ENGINE_REPAIR=1` (optional) |

Everything else — what is installed, in what order, which image tag — is engine-side.

## 2. Host side

### 2.1 `host/core/bootstrap.py` (shrinks to ~30 lines)

```python
def bootstrap(provider, *, source: str | None = None, repair: bool = False) -> None:
    if not repair and _installed(provider):          # test -s /opt/omelet/engine.version
        return
    url = source or os.environ.get("OMELET_ENGINE_URL", constants.ENGINE_URL)
    _run(provider, ["bash", "-c", _FETCH_AND_RUN, "omelet-bootstrap", url],
         step="installing Omelet inside the VM")
    if not _installed(provider):
        raise BootstrapError("the installer reported success but left no engine.version")
```

- `source` defaults to `os.environ.get("OMELET_ENGINE_URL", constants.ENGINE_URL)`, with
  `constants.ENGINE_URL = "https://raw.githubusercontent.com/omelet-app/omelet/main/engine/get.sh"`.
- The fetch-and-run command is one `bash -c` line passing the URL as `$1`: `curl -fsSL "$1"` piped
  to `bash`, falling back to `python3 -c 'urllib.request…'` when `curl` is absent. Nothing is
  pushed as a file.
- `OMELET_ENGINE_REF` is forwarded into the guest environment only when set on the host.
  `repair=True` forwards `OMELET_ENGINE_REPAIR=1`.
- Failures raise `BootstrapError` carrying the guest's stderr (`_run` is kept as is).
- `restart_agent`, `read_marker`, `_push_file`, `guest_assets()` are deleted.

### 2.2 Setup steps (`host/core/install.py`)

`preflight → remediate → reboot_gate → fetch_image → create_vm → bootstrap → connect → verify → finish`

- `bootstrap` calls `bootstrap(provider)`.
- `connect` replaces `agent_version_step`:
  - Reads `/health`. `api` missing counts as `1` (today's 0.1.0 agent predates the field). An `api`
    not in `constants.SUPPORTED_API = frozenset({1})` raises one plain sentence saying the app and
    the Omelet service in the VM do not match; no repair is attempted.
  - A call answering `unauthorized` / `agent_unconfigured` runs `bootstrap(provider, repair=True)`
    exactly once, rebuilds `AgentClient.for_provider(provider)` (fresh token), and dials again; a
    second refusal raises `AgentNotAccepted` as today.
- `_ACTIONS["bootstrap"]` becomes "Omelet could not be installed inside the virtual machine…".
- `omelet vm create` calls the same `bootstrap(provider)`.

### 2.3 Removed from the host

- Files: `host/provision/bootstrap.sh`, `stack.yml`, `stack.debug.yml`, `guest/`, `agents/`,
  `install-agents.sh`, `login-users.sh`.
- Constants: `BOOTSTRAP_VERSION`, `BOOTSTRAP_MARKER`, `GUEST_STACK`, `AGENT_IMAGE`,
  `EXPECTED_AGENT_VERSION`, and the version-tuple helpers in `install.py`.
- Added constants: `ENGINE_URL`, `ENGINE_MARKER`, `SUPPORTED_API`.
- `cli.selfcheck` checks only `host/provision/nginx-hello/docker-compose.yml` and
  `host/providers/omelet.yaml`.
- PyInstaller `datas` lose every removed asset.

`host/provision/nginx-hello/` stays: it is the host's own end-to-end check of the contract.

## 3. Engine side

### 3.1 Layout

```
engine/
  get.sh                   # remote entrypoint
  install.sh               # was host/provision/bootstrap.sh, extended
  stack.yml                # was host/provision/stack.yml
  stack.debug.yml          # was host/provision/stack.debug.yml
  cli/omelet.py            # was host/provision/guest/omelet.py
  instructions/omelet.md   # was host/provision/agents/omelet.md
  skills/omelet-setup/     # was host/provision/agents/skills/omelet-setup
  lib/login-users.sh       # unchanged
  lib/install-agents.sh    # skill copying removed; Codex block + ~/projects link kept
agent/                     # unchanged: the image source
```

`.gitattributes` gains `engine/** text eol=lf`; the `host/provision/**` line narrows to what is left.

### 3.2 `engine/get.sh`

Runs as root, `set -euo pipefail`. Kept minimal so version-specific logic lives in the tarball.

1. `apt-get install -y ca-certificates curl git` when any is missing.
2. `REPO=${OMELET_ENGINE_REPO:-https://github.com/omelet-app/omelet}`.
3. Choose the ref, first match wins:
   1. `OMELET_ENGINE_REF` when set (a branch works — this is how unreleased work is tested);
   2. `OMELET_ENGINE_REPAIR=1` and `/opt/omelet/engine.version` exists → that installed ref;
   3. the highest `engine-v*` from `git ls-remote --tags --refs "$REPO" 'engine-v*'`, by `sort -V`.
   No ref found → one sentence to stderr, exit 1.
4. Download `$REPO/archive/<ref>.tar.gz`, extract its `engine/` into a fresh
   `/opt/omelet/engine/` (replace, not merge, so deleted files do not linger).
5. `exec bash /opt/omelet/engine/install.sh <ref> [--repair]`.

### 3.3 `engine/install.sh <ref> [--repair]`

Idempotent, `set -euo pipefail`, run as root. Steps:

1. **OS and Docker** — today's bootstrap steps 1–6 unchanged: docker-ce from the official repo,
   `systemctl enable --now docker`, `edge` network, `/opt/omelet` group permissions + setgid, the
   docker GID into `/opt/omelet/.env`, the token when absent.
2. **Stack** — copy `stack.yml` to `/opt/omelet/stack.yml`; `docker compose pull && up -d`. Add
   `--force-recreate agent` when this run created the token or `--repair` was given (the agent
   reads its token once, at startup).
3. **Node 22** — NodeSource `node_22.x` apt repo when `node` is missing or older than 22.20
   (`skills@1.5.26` declares `node >=22.20.0`; Ubuntu 24.04's apt ships 18).
4. **CLI** — `install -m 755 cli/omelet.py /usr/local/bin/omelet`; `git` is already present.
5. **Instructions** — `/etc/claude-code/CLAUDE.md`; per account (root + every line from
   `lib/login-users.sh`): `usermod -aG docker`, then `lib/install-agents.sh` for the Codex
   `AGENTS.md` block and the `~/projects` link.
6. **Migration cleanup** — remove what PR #3 installed and npx would collide with:
   `/etc/codex/skills/omelet-setup`, per account `~/.claude/skills/omelet-setup` and
   `~/.agents/skills/omelet-setup` when they are real directories (npx creates a symlink at the
   first), `/opt/omelet/bin`, `/opt/omelet/agents`, `/opt/omelet/.bootstrapped`. Nothing is copied
   into `/etc/skel` any more.
7. **Skills** — per account:
   `runuser -u <user> -- env HOME=<home> DISABLE_TELEMETRY=1 npx -y skills@1.5.26 add /opt/omelet/engine/skills -s '*' -g -a claude-code codex -y`
   (root runs it directly with `HOME=/root`). The skills CLI version is pinned.
8. **Marker, last** — `echo <ref> > /opt/omelet/engine.version`. A failure above leaves no marker.

Every network step (GitHub, NodeSource, npm, ghcr) prints one plain sentence naming what was
unreachable before exiting non-zero.

### 3.4 Image version

`stack.yml`'s `OMELET_AGENT_IMAGE` default tag is the single source of which image an engine ref
runs. The test that held `host.constants.AGENT_IMAGE` equal to `stack.yml` is replaced by one
holding `engine/stack.yml`'s tag equal to `agent/`'s `__version__` and the Dockerfile's
`AGENT_VERSION`.

### 3.5 Agent API number

`agent/core/constants.py` gains `API_VERSION = 1`; `/health` returns it as `"api"`. It changes only
when a route the host calls changes incompatibly, never on an ordinary engine release.

## 4. Verified behaviour of `npx skills` (skills@1.5.26)

Probed against a throwaway `$HOME`:

- `add <local folder of skill folders> -s '*' -g -a claude-code codex -y` installs non-interactively,
  writing the real copy to `~/.agents/skills/<name>` (read by Codex) and a symlink at
  `~/.claude/skills/<name>`.
- `-g` means the invoking `$HOME`, so it must run once per account.
- The full `https://github.com/<o>/<r>/tree/<ref>/<folder>` form works with branches and tags; the
  shorthand `o/r/tree/...` ignores the subpath and finds nothing. Not used here (decision 9), but
  it is the form for future third-party skills.

## 5. Testing

Only tests that can fail on a real bug.

- **Host bootstrap** — skipped when the marker exists; runs the fetch with the configured URL
  (env override honoured); forwards `OMELET_ENGINE_REF` only when set and `OMELET_ENGINE_REPAIR=1`
  only on repair; raises with the guest's stderr on failure; raises when exit 0 leaves no marker.
- **Connect step** — unsupported `api` → one-sentence error and no bootstrap call; missing `api`
  treated as 1; a token refusal triggers exactly one repair bootstrap, then dials with the new token.
- **Boundaries** — `host/provision/` contains only `nginx-hello/`; the frozen bundle includes
  nothing from `engine/`; contract constants (token path, agent port, edge port, `GUEST_ROOT`) are
  held equal across host constants, agent constants and the guest CLI (extends
  `tests/test_constants_agree.py`).
- **Engine scripts** — `bash -n` over `get.sh` and `install.sh`; `get.sh`'s ref choice with a fake
  `git` on `PATH` (explicit ref wins; repair reuses the installed ref; otherwise highest tag by
  version sort, so `engine-v0.10.0` beats `engine-v0.9.0`); text checks that `install.sh` pins
  `skills@`, adds from the local skills folder, and writes the marker last.
- **Moved unchanged** — `tests/guest/` → `tests/engine/cli/`; `test_login_users.py`,
  `test_install_agents.py` (minus skill-copy assertions), and `test_bootstrap_shell.py` → `tests/engine/`.
- **Deliberately untested** — real network/apt steps and `npx skills add` inside a VM; covered by
  the manual acceptance run on a live VM.

## 6. Release (manual until CI exists)

1. Bump `agent/` version, the Dockerfile `AGENT_VERSION`, and `engine/stack.yml`'s image tag.
2. `docker build -t ghcr.io/omelet-app/omelet-agent:X.Y.Z agent/ && docker push …`.
3. `git tag engine-vX.Y.Z && git push origin engine-vX.Y.Z`.

An engine release never touches `host/`.

## 7. CLAUDE.md

Add near the top:

```markdown
## Architecture shape: the host is a VM shell, the engine is everything inside

Two halves, shipped and versioned independently:

- **Host** (`host/`, a frozen desktop binary) — creates and runs the VM, runs one bootstrap
  command in it (fetch `OMELET_ENGINE_URL` → `bash`), reads the token, forwards ports, and talks
  to the agent over HTTP. It holds no guest files and no knowledge of what the engine installs.
- **Engine** (`engine/` + `agent/`) — everything inside the VM: Docker, the Traefik+agent stack,
  the in-VM `omelet` CLI, agent instructions and skills (via `npx skills add`). Released as
  `engine-v*` tags with a matching `omelet-agent` image. The same `get.sh` provisions a cloud VM.

The seam between them is a fixed contract — token path, agent port + `/health` `api` number, edge
port, `/opt/omelet/engine.version` — and nothing else. A change inside the VM must never need a
host release; if it does, the logic is on the wrong side.
```

Then bring the rest in line: the Layers list (`host/provision/` → `engine/`), remove both
`BOOTSTRAP_VERSION` bullets and the "nothing under `agent/` is bundled" rationale that referred to
`stack.yml`, describe the release steps, and update the test count.

## 8. Known gaps and out of scope

- Accounts created after install get no skills or Codex block until the engine is installed again
  (host repair, or a future `omelet self-update`).
- No self-update or automatic update — `docs/future/engine-self-update.md`.
- Installing Claude Code / Codex; the Codex Windows-app gap; Lima verification (still UNVERIFIED).
- CI for image builds and tags.
