# Design: coding agents set up projects inside the VM

**Status:** approved design, pending implementation plan
**Scope:** the guest-side `eggie` command, agent-neutral instructions for coding
agents running inside the VM, and the provisioning that installs both. Validated
on Windows first, on macOS in the next session — nothing here may depend on the
host platform.

---

## 1. Purpose

A non-technical user opens the Eggie VM in their coding agent (Claude Code
Desktop's WSL environment on Windows, an SSH environment into Lima on macOS) and
says "set up this repo", "I want a web app for tracking routines", or "set up
this folder". The agent does it without asking a single technical question and
hands back a working `http://<id>.127-0-0-1.sslip.io:39080` URL.

Today that fails. Inside the VM there is no `eggie` command, and nothing tells
the agent that projects must be started through the Eggie agent API. Left to
itself it runs `docker compose up`, which skips the generated overlay: no
Traefik labels, no `edge` network, no URL. Only a project first started from the
host (`eggie up <dir>`) comes out routed.

Established on 2026-09-11, Windows: a Claude Code Desktop WSL session works on
the `wsl --import`ed `eggie-vm`; projects survive a full Windows restart; page
edits show after a browser reload; routing through Traefik works.

## 2. Non-goals

- **Agent instructions on the host.** A session opened on a Windows/Mac folder
  gets nothing from this design. The host's `eggie up <dir>` keeps working as
  the import path, and may be removed later.
- **Hooks.** None in this iteration. "Start with `eggie status`" in the
  instructions does the job for every agent; Codex user-level hooks would also
  require manual trust, which defeats the point.
- **An MCP server or per-agent plugins.** Every agent can run a shell command;
  the CLI reaches all of them with one implementation. Claude Code Desktop WSL
  sessions do not load plugins yet.
- **A template catalogue.** The coding agent chooses the stack itself within the
  rules in §6.2. Templates may come from the service later.
- **`destroy` inside the VM.** Destructive, and the host CLI already has it.
- **Machine-readable output (`--json`).** Agents read plain sentences fine.

## 3. Constraints and established facts

Verified against official docs on 2026-09-11.

| | Claude Code | Codex | Gemini CLI / Cursor / Copilot |
|---|---|---|---|
| Always-loaded instructions | `CLAUDE.md` only, never `AGENTS.md`. System-wide `/etc/claude-code/CLAUDE.md`, plus `~/.claude/CLAUDE.md` and every ancestor of the cwd | `AGENTS.md` in the Codex home (`~/.codex`, or `$CODEX_HOME`); project files from the git root down to the cwd, nothing above the git root; 32 KiB total | `AGENTS.md` (Gemini only with `context.fileName`); no shared global location |
| Skills (`SKILL.md`, the Agent Skills format) | `~/.claude/skills`, `.claude/skills`; no `.agents/skills` | `$CWD/.agents/skills` up to the repo root, `~/.agents/skills`, `/etc/codex/skills` (admin); follows symlinks | `~/.agents/skills`, `.agents/skills` |
| Skill cost before use | name + description only | name + description only | name + description only |

Sources: code.claude.com/docs/en/memory, /skills, /managed-settings,
/desktop-wsl; learn.chatgpt.com/docs/agent-configuration/agents-md,
/build-skills, /hooks; agentskills.io; agents.md.

Constraints from this repo:

- **The guest is identical on both platforms** (Ubuntu 24.04, docker-ce,
  `python3` present). Everything in this design lives in the guest, so the host
  platform never leaks into it.
- **`host/` never imports `agent/`**, and the new guest CLI imports neither —
  it is one stdlib-only file copied into a VM.
- **The agent container runs as a non-root user in the `docker` group** and must
  write `<project>/.eggie/overlay.yml`. Files a root agent session creates are
  root-owned `644`/`755`.
- **The agent token** is `/opt/eggie/agent.token`, root:docker `640`.
- **Assets are pushed base64 through `bash -lc`** (`bootstrap._push_file`), so
  each asset must stay under the ~24 KB command-line budget.
- **Bump `BOOTSTRAP_VERSION` whenever `bootstrap.sh` changes**, or existing VMs
  skip the new provisioning.

## 4. Architecture

Three layers. The command is the contract; everything above it is thin and
replaceable.

```
coding agent (Claude Code, Codex, …)
   │ reads                         │ runs
   ▼                               ▼
per-agent locations  ──copies──  /opt/eggie/agents/     eggie (guest CLI)
(§6.3 adapter table)             eggie.md, SKILL.md          │ HTTP + token
                                                              ▼
                                                  agent API 127.0.0.1:39099
                                                              │
                                     overlay + compose up → Traefik :39080
```

1. **Guest CLI** (`/usr/local/bin/eggie`): the only thing that knows how to
   turn a folder into a routed project. Agent-neutral, self-describing
   (`eggie --help`).
2. **Content** (`eggie.md`, `skills/eggie-setup/SKILL.md`): written once, in
   plain Markdown, naming only `eggie` commands.
3. **Adapters**: a table in `bootstrap.sh` that copies the content into each
   agent's discovery locations. Supporting a new agent is one more row.

Nothing is ever written into the user's project repository, extending the
existing rule that `docker-compose.yml` is never modified.

## 5. The guest CLI

One file, `host/provision/guest/eggie.py`, Python 3 stdlib only, installed as
`/usr/local/bin/eggie` (mode 755, `#!/usr/bin/env python3`).

### 5.1 Project location and identity

- Projects live in `/opt/eggie/projects/<id>/` (`GUEST_PROJECTS`), the path the
  agent container mounts at the same location. Provisioning adds a `~/projects`
  symlink to it for each target user (§6.4).
- A folder is a project folder iff `Path(dir).resolve().parent ==
  Path(GUEST_PROJECTS).resolve()`. Resolving first makes `~/projects/x` and
  `/opt/eggie/projects/x` the same folder.
- The project id **is** the folder name, and must already equal the slug rule
  (`[^a-z0-9-]+` → `-`, lowercased, trimmed of `-`). `new` and `clone` slug the
  name they are given; `up` refuses a folder whose name is not a valid id and
  names the id to rename it to. (The agent's `create_project` slugs the id and
  derives the folder from the slug, so a non-slug folder name would register a
  different, empty folder.)

### 5.2 Commands

| Command | Behaviour |
|---|---|
| `eggie up [dir]` | `dir` defaults to the cwd. Validates §5.1 and that `docker-compose.yml` exists. Prepares `.eggie/` (§5.3). Creates the project (`POST /projects`, a 409 `project_exists` is the ordinary case), starts it (`POST /projects/{id}/up`), polls the job to completion, prints every URL, then the agent's `problem` message if one came back. |
| `eggie new <name>` | Creates `GUEST_PROJECTS/<slug(name)>/` and prints its path. Registers and starts nothing — the folder is empty. Fails if the folder exists. |
| `eggie clone <url> [name]` | `name` defaults to the URL's last path segment minus `.git`. Runs `git clone <url> GUEST_PROJECTS/<slug(name)>` as the calling user with `GIT_TERMINAL_PROMPT=0`: a coding agent's shell has no terminal to answer a credential prompt, so a private repo must fail fast with git's own message rather than hang. Fails if the target exists. If the clone has a `docker-compose.yml`, runs `up` on it; otherwise says the project needs one. |
| `eggie status [dir]` | Inside a project folder: that project (`GET /projects/{id}`) — status, URLs, problem. Anywhere else: every project (`GET /projects`). A folder not yet registered reports "not set up yet — run `eggie up`". |
| `eggie logs [service]` | Logs of the project in the cwd (`GET /projects/{id}/logs`). |
| `eggie down` | Stops the project in the cwd (`POST /projects/{id}/down`), waited on. |
| `eggie --help` | One line per command; the fallback for agents without skill support. |

### 5.3 Letting the agent write the overlay

The agent writes exactly one path inside a project: `.eggie/overlay.yml`
(`agent/core/lifecycle.py`). Before registering, `up` ensures `.eggie/` exists
with group `docker`, mode `2775`, and makes an existing `overlay.yml`
group-writable. Nothing else in the project is touched — no recursive `chmod`
over `node_modules`.

### 5.4 Talking to the agent

- `urllib.request` against `http://127.0.0.1:39099`, `Authorization: Bearer
  <token>` read from `GUEST_TOKEN` on every invocation.
- An injectable opener (the same shape as `host/client.py`'s) so the seam test
  can drive the real agent app in-process.
- Semantics copied from `host/client.py`, not imported: every non-2xx body
  `{"error": {"code", "message"}}` becomes that message; `project_busy` (409) is
  retried for up to 60 s; a `failed` job surfaces its `detail`; job polling
  mirrors `wait_for_job`.

### 5.5 Failures

Every failure is one plain sentence on stderr and exit code 1. The coding agent
relays it; the user is never shown a status code.

| Situation | Message (gist) |
|---|---|
| Connection refused | "The Eggie service in this VM is not answering." + `sudo docker compose -f /opt/eggie/stack.yml up -d` |
| Token unreadable | "This user can't reach Eggie — it must be in the docker group." |
| Folder outside the projects root | "Projects live in ~/projects. Move this folder there, or use `eggie new`." |
| Folder name not a valid id | "Rename this folder to `<slug>` first." |
| No `docker-compose.yml` | "There is no docker-compose.yml in `<dir>`." |
| `logs`/`down` outside a project folder | "Run this inside a project folder in ~/projects." |
| `new`/`clone` target exists | "`~/projects/<id>` already exists." |
| `git clone` failed | "Could not download `<url>`:" + git's own stderr (a private repo lands here). |
| Agent error body | The agent's own message, verbatim. |
| Job failed / crash-looping | Status, the job's detail, then "run `eggie logs`". |
| Started, URL not answering | The agent's `problem.message` (e.g. listening on 127.0.0.1). |

## 6. What the coding agents read

### 6.1 `host/provision/agents/eggie.md` — always loaded

```markdown
# You are working inside an Eggie VM
This is an isolated Linux VM. Projects live in ~/projects, one folder each,
and run in Docker behind Eggie's router.

The user is not technical. Never ask them technical questions (stack, ports,
databases, frameworks) — decide yourself. Report results in plain words and
always give them the project's URL.

- Start with `eggie status` to see what exists and what is running.
- Run projects only with `eggie up` — never `docker compose up` directly,
  or the project gets no URL.
- Never edit `.eggie/overlay.yml`; it is generated.
- Something broken? `eggie logs`.
- New project, repo URL, or an archive to set up: use the `eggie-setup` skill
  (no skills? run `eggie --help`).
```

### 6.2 `host/provision/agents/skills/eggie-setup/SKILL.md` — loaded on use

Frontmatter `name: eggie-setup`, and a description that triggers on setting up,
creating, importing, cloning, or running a project. Body:

- **Repo URL** → `eggie clone <url>`; if it reports no compose file, write one
  (rules below), then `eggie up`.
- **Archive** (zip/tar in the VM) → unpack into `~/projects/<name>`, then
  `eggie up` in it.
- **Folder already in `~/projects`** → `eggie up` in it.
- **Folder elsewhere in the VM** → move it into `~/projects`, then `eggie up`.
- **`eggie up` asks for a rename** → rename the folder to the id it names,
  then re-run.
- **Private repo** (`clone` reports it could not download) → tell the user in
  plain words that the repository is private and needs access; do not ask for
  tokens or keys unprompted.
- **New app from a description** → `eggie new <name>`, build it following the
  rules, then `eggie up`.
- **Rules for a compose file you write** (each matches what the agent enforces):
  - Choose the simplest mainstream stack yourself; do not ask.
  - Everything runs in `docker-compose.yml`. Run language toolchains in
    containers (`docker compose run`); never install them on the VM.
  - The app listens on `0.0.0.0`, never `127.0.0.1`.
  - Declare the web service in `.eggie/project.yml`
    (`web: [{service: <name>, port: <container port>}]`) and publish no host
    ports — detection needs it once there are two services, and fixed host
    ports collide across projects.
  - Bind-mount the source and run a dev server with reload, so edits appear on
    a browser refresh.
  - Data in SQLite, or in a database service with a named volume.
- **After `eggie up`**: open the URL yourself (e.g. `curl`) before telling the
  user it works; on failure read `eggie logs`, fix, re-run `eggie up`.

### 6.3 Adapter table

`bootstrap.sh` copies (never symlinks — a copy has no dangling-link failure mode
and is rewritten on every bootstrap) from `/opt/eggie/agents/`:

| Agent | Instructions | Skill |
|---|---|---|
| Claude Code | `/etc/claude-code/CLAUDE.md` ← `eggie.md` (system-wide; the VM owns this file) | `~/.claude/skills/eggie-setup/` per target user |
| Codex | `~/.codex/AGENTS.md` per target user: a block between `<!-- eggie:begin -->` and `<!-- eggie:end -->`, replaced in place, anything else in the file kept | `/etc/codex/skills/eggie-setup/` (system-wide) |
| Gemini CLI, Cursor, Copilot | — (no global standard) | `~/.agents/skills/eggie-setup/` per target user |

### 6.4 Target users

Per-user entries go to `root`, to every account with `1000 <= uid < 60000` and
an existing home directory, and to `/etc/skel` for accounts created later. That
covers WSL sessions (root, the imported distro's only user) and Lima (the
`lima` user) without asking the provider. For each non-root target user,
provisioning also runs `usermod -aG docker` (token access) and creates
`~/projects` → `/opt/eggie/projects` unless `~/projects` already exists as
something else, in which case it leaves it alone and logs one line.

## 7. Provisioning changes

- `bootstrap.guest_assets()` gains four entries:
  `guest/eggie.py` → `/opt/eggie/bin/eggie`,
  `install-agents.sh` → `/opt/eggie/bin/install-agents.sh`,
  `agents/eggie.md` → `/opt/eggie/agents/eggie.md`,
  `agents/skills/eggie-setup/SKILL.md` →
  `/opt/eggie/agents/skills/eggie-setup/SKILL.md`.
  `cli.selfcheck` already verifies every entry of that list.
- `packaging/windows/eggie.spec` bundles the same four files under
  `host/provision/`.
- `install-agents.sh <agents-dir> <home> <uid:gid>` does the per-home half of
  §6.3/§6.4 (both skill copies, the Codex marker block, the `~/projects` link),
  kept out of `bootstrap.sh` so it can be run against a temporary home in tests.
- `bootstrap.sh` gains one step after Docker is installed: `apt-get install -y
  git` if missing; fail loudly if `python3` is missing; `install -m 755
  /opt/eggie/bin/eggie /usr/local/bin/eggie`; the system-wide rows of the
  adapter table (§6.3); `install-agents.sh` for root, `/etc/skel` and each
  target user, plus `usermod -aG docker` (§6.4).
- `BOOTSTRAP_VERSION` 5 → 6. Any later change to the CLI or the Markdown bumps
  it again — the marker is the only thing that makes an existing VM re-run.

## 8. Constants and boundaries

- The guest CLI declares `AGENT_PORT`, `GUEST_PROJECTS`, `GUEST_TOKEN`,
  `COMPOSE_FILE` and its own slug function. `tests/test_constants_agree.py`
  extends to hold every name it shares with `agent/core/constants.py` equal, and
  the slug rule is held equal to the agent's `_slug` the way
  `test_project_id_matches_the_rule_the_agent_slugs_with` already does for the
  host.
- A new AST test: `host/provision/guest/eggie.py` imports only the standard
  library (no `host`, no `agent`, no third-party).
- `tests/host/test_no_dead_modules.py` skips `host/provision/`: files there are
  pushed into the VM, never imported by the host.
- The platform-leak test already scans the file and needs no change.

## 9. Testing

Only tests that can catch a real regression:

- **Seam: the guest CLI against the real agent app**, in-process, reusing
  `tests/host/test_client_seam.py`'s `AppOpener` over the fake Docker runner:
  `up` on a new project creates then starts it and prints its URL; `up` on an
  existing project skips straight to start; an agent error body surfaces as its
  message; `project_busy` is retried; a failed job reports its detail.
- **Path resolution**: inside vs outside the projects root, reached through a
  symlink, a non-slug folder name, the root itself.
- **`.eggie/` preparation**: an existing root-owned `overlay.yml` ends up
  group-writable; nothing outside `.eggie/` changes.
- **Constant agreement, stdlib-only boundary, asset size** — each asset in
  `guest_assets()` fits the push budget.
- **`install-agents.sh`** run against a temporary home: the skill lands in both
  per-user locations; a second run replaces the Codex block while keeping the
  user's own text (including a last line with no newline); `~/projects` is
  linked once and an existing real folder is left alone.
- **`bootstrap.sh`**: `bash -n` passes (existing test), and the paths it installs
  from are the ones `guest_assets()` pushes to, so the two cannot drift.

Not tested automatically: the Markdown (no logic), and whether agents follow it.
That is the acceptance run below.

## 10. Acceptance (manual, Windows now, macOS next session)

After `eggie setup` on a fresh VM (and once on an existing VM, proving the
version bump re-provisions), open `~/projects` in the agent and:

1. Claude Code: paste a public repo URL with a `docker-compose.yml`, say "set up
   the project".
2. Claude Code: "I want a web app for tracking routines."
3. Claude Code: copy a folder in through `\\wsl.localhost\eggie-vm\opt\eggie\projects`,
   say "set this up".
4. Codex: repeat (1) or (2).

Each passes when the agent asks no technical question, the reply contains a
`…127-0-0-1.sslip.io:39080` URL, that URL answers in a Windows browser, and
editing a page shows on reload.

## 11. Risks and open items

- **Desktop WSL/SSH sessions and managed files.** The docs do not say whether a
  Desktop WSL session reads `/etc/claude-code/CLAUDE.md` inside the distro.
  The acceptance run proves it; if it does not, the adapter row falls back to
  `~/.claude/CLAUDE.md` per target user — a one-line change to the table.
- **Codex `~/.agents/skills` discovery** has a community report of it being
  missed, which is why the Codex row uses `/etc/codex/skills`.
- **Root sessions.** On WSL the agent session runs as root, which the design
  supports. A non-root default user is a separate decision, and §6.4 already
  covers one if it is added.
- **Host re-import overwrites.** Running the host's `eggie up <dir>` again on a
  project since edited in the VM overwrites those edits (upload merges, never
  deletes). Acceptable while host sessions are a non-goal; noted for whoever
  revisits them.
