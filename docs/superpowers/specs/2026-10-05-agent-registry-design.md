# Agent registry, connection detection and setup

Date: 2026-10-05

## Goal

The console's "Connect an agent" guide tells the user when their coding agent has actually connected
to the VM, and installs what an agent needs in the VM as soon as the user picks it. Every coding
agent is described by one manifest folder, so supporting a new agent is a content change: add a
folder, list it in `index.json`, no code edits (open/closed).

Out of scope: hooks that report agent sessions, a generic extension framework (recipes may copy the
same shape later), rewriting guide content (the onboarding flow changes in a later iteration).

## What the user said

- "Connected" means the agent's directory in the VM user's home is not empty — not a credentials
  file, which a desktop-app connection may never write.
- Files Eggie's install puts into those directories (instructions, skills) don't count.
- Cursor is supported like Codex, with an `AGENTS.md`.
- Codex needs its CLI in the VM (`curl -fsSL https://chatgpt.com/codex/install.sh | sh`). It is
  installed automatically when the user picks Codex, and the command lives in Codex's manifest, not
  in code.
- Codex on Windows doesn't work yet; its guide and detection stay as they are anyway.

## 1. The registry: `runtime/agents/`

```
runtime/agents/
  CLAUDE.md
  index.json                 {"agents": ["claude-code", "codex", "cursor"]}
  <id>/
    agent.json
    icon.webp, screenshots…
```

The folder moves from `runtime/web/apps/console/agent-guides/` and keeps its guide content and
assets. `agent.json` gains four install-side fields next to the existing guide fields:

```jsonc
{
  "name": "Codex",
  "icon": "icon.webp",
  "home": ".codex",                              // the agent's directory in each account's home
  "instructions": ["~/.codex/AGENTS.md"],
  "skills": "codex",                             // the `npx skills add -a` name
  "detect": { "ignore": ["AGENTS.md"] },
  "setup": { "run": "curl -fsSL https://chatgpt.com/codex/install.sh | sh" },
  "platforms": { "windows": { … }, "mac": { … } }
}
```

| Field | Required | Meaning |
|---|---|---|
| `home` | yes | Directory (or list of directories) under each account's home that detection reads. Relative paths, no `..`. A remote-SSH Cursor session writes `~/.cursor-server`, so Cursor lists both. |
| `instructions` | no | Files that receive `instructions/eggie.md`. An absolute path is written **whole**, once (a system-wide file Eggie owns, e.g. `/etc/claude-code/CLAUDE.md`). A `~/` path is written **per account** as the `<!-- eggie:begin/end -->` block, leaving the user's own text alone. |
| `skills` | no | Agent name passed to `npx skills add -a`. All manifests' names go in the one existing call. |
| `detect.ignore` | no | Top-level entry names under `home` that Eggie's own install creates. |
| `setup.run` | no | Shell command run once per account when the user picks the agent. |

The three manifests:

| id | `home` | `instructions` | `skills` | `detect.ignore` | `setup` |
|---|---|---|---|---|---|
| claude-code | `.claude` | `/etc/claude-code/CLAUDE.md` | `claude-code` | `skills` | — |
| codex | `.codex` | `~/.codex/AGENTS.md` | `codex` | `AGENTS.md`, `packages` | `curl -fsSL https://chatgpt.com/codex/install.sh \| CODEX_NON_INTERACTIVE=1 sh` |
| cursor | `.cursor`, `.cursor-server` | `~/.cursor/AGENTS.md` | `cursor` | `AGENTS.md`, `skills` | — |

Codex ignores `packages` and runs its installer with `CODEX_NON_INTERACTIVE=1` because the installer writes under `~/.codex/packages` and reads prompts from stdin without a tty.

**To verify on a VM before merge:** that Cursor's CLI reads a global `~/.cursor/AGENTS.md`, that
`skills` accepts `-a cursor` and where it writes, and the exact entries the skills CLI creates under
each `home`. A manifest drops a field that turns out unsupported; `ignore` lists what is actually
created.

**Trust.** `setup.run` runs as root (and as each login user). Manifests ship in the release
tarball, so they are as trusted as `install.sh`; a new agent from an outside contributor is reviewed
like a script change. `runtime/agents/CLAUDE.md` says so.

## 2. One reader: `runtime/install/lib/agents.py`

A stdlib-only Python 3 script (the VM already has `python3`; the install scripts use it). It is the
only code that interprets the install-side fields. It validates every manifest on load — an invalid
one is skipped with a message on stderr, never half-applied — and has subcommands:

- `skills` — prints the `-a` names, space-separated.
- `instructions --system` — prints absolute targets; `instructions --home <dir>` — prints the
  per-account targets with `~` expanded.
- `run <agent-status-dir> <accounts…>` — the runner pass of §3.

The console keeps validating the guide fields in `catalog.ts`; each side validates what it reads.

## 3. Detection and setup run as root

The API runs as a non-root user and never touches a user home (`/root/.claude` is `0700`), so the
work happens in a root-side runner, the same desired/applied shape as GitHub.

**Files** in `/opt/eggie/agent-status/` (created by `install.sh`, `2770 root:docker`; not
`/opt/eggie/agents`, which `install.sh` deletes as a leftover of an old layout):

- `check` — written by the API: a rising integer. Its change triggers the runner.
- `setup/<id>` — written by the API: a rising integer, the agent's setup request.
- `status.json` — written by the runner, `0644`:
  ```json
  {"generation": 12,
   "agents": {"codex": {"connected": false, "setup": "installing", "setup_generation": 3}}}
  ```
  `setup` is `null` (never requested), `installing`, `ready` or `failed`. Only booleans and states —
  never file names or anything read from inside an agent's directory.
- `setup-<id>.log` — the last setup's output, `0600`, for whoever debugs the VM.

**Units:** `eggie-agents.path` (`PathChanged=/opt/eggie/agent-status/check`) starts
`eggie-agents.service` (oneshot, `StartLimitIntervalSec=0`), which runs `agents.py run` over root
plus `login-users.sh`'s accounts. `install.sh` installs and enables both and starts the service
once.

**A runner pass:**

1. For each agent with a `setup/<id>` whose number is above `setup_generation`: if the agent is
   already connected, mark `ready` without running anything. Otherwise write `installing` to
   `status.json`, run `setup.run` as each account (`runuser`, `HOME` set, `timeout 10m`), record
   `ready` if every account succeeded, else `failed`, and store the request number.
2. Detection: an agent is connected when any account's `~/<home>` exists and holds a top-level
   entry not in `detect.ignore`.
3. Write `status.json` with the `check` number the pass started from. If `check` changed meanwhile,
   pass again (at most 5 passes, as `github-apply.sh`).

## 4. API

On the shared router (bearer token and console cookie), additive, no `API_VERSION` bump — the host
calls neither:

- `GET /agents/status` — writes `check` + 1, returns `status.json`'s `agents` as it is now (one
  poll behind is fine). Missing or unreadable `status.json` returns `{"agents": {}}`.
- `POST /agents/{id}/setup` — ensures setup. `404 agent_not_found` when
  `/opt/eggie/runtime/agents/<id>/agent.json` is missing or has no `setup`. A no-op when the
  agent is connected or its setup is `installing` or `ready`; otherwise writes `setup/<id>` + 1 and
  `check` + 1. So a retry after `failed` is the same call.

Counter writes are serialized by a lock and written via temp file + rename. Logic lives in
`core/agents.py`; the routes stay thin.

## 5. Console

- The web image copies `runtime/agents/` to `/agent-guides/` (a new build context, as `fixtures`);
  the URL and nginx location don't change. Vite dev serves the same folder.
- `catalog.ts` additionally reads whether an agent has `setup` (a boolean; the command isn't
  shown).
- **Picker:** a "Connected" badge on each connected agent's card, from one `GET /api/agents/status`.
- **Guide:** opening an agent with `setup` calls `POST /api/agents/{id}/setup`. A status line under
  the steps shows, in order of precedence:
  - connected → "✓ Codex is connected";
  - `installing` → "Getting Codex ready in your kitchen…";
  - `failed` → "Couldn't get Codex ready." with a Retry button (the same POST);
  - otherwise → "Waiting for Codex to connect…".
- Polls `/api/agents/status` every 3 s while a guide is open and the agent isn't connected; stops
  once it is. The mapping from status to line is a pure function next to `card.ts`.

## 6. Install changes

- Step 9: `/etc/claude-code/CLAUDE.md` is no longer hard-coded; every `instructions --system`
  target is written whole from `instructions/eggie.md`.
- Step 11: `install-agents.sh` takes the per-account targets from `agents.py` instead of its
  hard-coded `~/.codex/AGENTS.md` (the marker-block writer is unchanged), and `npx skills add`
  takes `-a $(agents.py skills)`.
- New step: `/opt/eggie/agent-status/` and the two units.

## 7. Error handling

- A broken manifest is skipped by both readers; the others keep working.
- A failed setup is visible (`failed` + Retry) and logged; it never fails `install.sh`, because it
  isn't part of install.
- A runner that never ran leaves no `status.json`: the console shows "Waiting…", which is true.
- An account whose home is unreadable counts as not connected for that account.

## 8. Tests

- `agents.py` against temporary homes and manifests: connected; only ignored entries; missing
  `home`; a second login user connected; a setup that succeeds, fails, and is skipped because the
  agent is already connected; a repeated request number not re-running setup; an invalid manifest
  skipped.
- Every shipped manifest loads in `agents.py` (Python test over `runtime/agents/`, found via
  `__file__`, asserting it scanned at least one); `content.test.ts` keeps checking guides.
- `install-agents.sh` writes blocks into the targets it is given (existing test, adjusted).
- API: status with the file present, missing and corrupt; setup for an unknown agent, an agent with
  no `setup`, a `ready` one (no write), a `failed` one (writes).
- Console: the status-to-line mapping.

Untested on purpose: the systemd units and the real Codex installer (live-VM acceptance only).

## 9. Docs

New `runtime/agents/CLAUDE.md` (manifest fields, trust, how to add an agent). Update
`runtime/CLAUDE.md` (what lives here), `runtime/install/CLAUDE.md` (steps, runner),
`runtime/eggie_api/CLAUDE.md` (feature area) and `runtime/web/CLAUDE.md` (guides' new source).
