# Agent Registry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every coding agent is one manifest folder under `runtime/agents/`; the VM writes each agent's instructions and skills from it, a root-side runner detects whether the agent has connected and runs its setup when the user picks it, and the console shows the result.

**Architecture:** `runtime/install/lib/agents.py` (stdlib Python, runs on the VM) is the only reader of the manifests' install-side fields; `install.sh`, `install-agents.sh` and the runner call it. The API never touches a user home: it bumps counter files in `/opt/eggie/agent-status/` and reads the runner's `status.json` (GitHub's desired/applied shape). The console reads the same manifests from nginx for the guides and polls the API for status.

**Tech Stack:** bash, Python 3.12 stdlib, systemd path units, FastAPI, React + TanStack Query + Vitest, nginx.

**Spec:** `docs/superpowers/specs/2026-10-05-agent-registry-design.md`

## Global Constraints

- Branch `feature/43-agent-registry`, PR to `main`. Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Python tests: `.venv/bin/python -m pytest` (3.12+). In the WSL sandbox prefix `TMPDIR=<writable dir>` if `tmp_path` fails.
- Web: run from `runtime/web`: `npm test`, `npm run typecheck`.
- Tests reach repo files via `__file__`, never cwd-relative paths; scans assert they found something.
- `host/` is untouched. No `sys.platform`/`platform.system()`/`os.name` in `runtime/eggie_api/`.
- The API never reads a user home. Status files carry only booleans/states, never file names or file contents.
- Status dir is `/opt/eggie/agent-status/` — never `/opt/eggie/agents` (install.sh deletes that as a leftover).
- New API routes are additive; `API_VERSION` stays 1.
- Every non-2xx API body is `{"error": {"code", "message"}}` via `ApiError`.
- Comments only for non-obvious edge cases; no ticket/doc references in comments.
- Setup timeout per account: `10m`. Runner: at most 5 passes. Console poll: 3 s.
- Manifest id: `^[a-z0-9][a-z0-9-]*$`.

## Review Focus

1. A setup interrupted mid-run (VM shut down) leaves `installing` in `status.json`; the API refuses to request again while `installing`, so without care it is stuck forever. Expected: the next pass turns it into `failed`, Retry works. (Task 4 test.)
2. Two setup requests before the runner gets to them (React StrictMode double effect, double click). Expected: setup runs once. (Task 4 test.)
3. A manifest whose `home`, `instructions` or `ignore` could escape the account's home (`..`, absolute, `/` inside a name). Expected: the manifest is skipped with its id on stderr, the others still apply. (Task 2 test.)
4. `status.json` missing, half-written or garbage. Expected: API answers `{"agents": {}}`, never 500; console shows "Waiting…". (Task 5 test.)
5. An agent already connected when the user opens its guide. Expected: setup is not run; it is marked `ready`. (Task 4 test.)

---

## File Structure

| Path | Responsibility |
|---|---|
| `runtime/agents/` (moved from `runtime/web/apps/console/agent-guides/`) | Manifests + guide assets, one folder per agent |
| `runtime/agents/CLAUDE.md` | Manifest fields, trust, how to add an agent |
| `runtime/install/lib/agents.py` | Load/validate manifests; `skills`, `instructions`, `run` subcommands |
| `runtime/install/lib/agents-run.sh` | Root wrapper: accounts → `agents.py run` |
| `runtime/install/systemd/eggie-agents.{path,service}` | Trigger on `check` change |
| `runtime/install/lib/install-agents.sh` | Per-account instruction blocks from `agents.py` |
| `runtime/install/install.sh` | System instructions, skills `-a` list, runner install |
| `runtime/eggie_api/core/agents.py` | `AgentStatus`: bump counters, read status |
| `runtime/eggie_api/routes/app.py` | `GET /agents/status`, `POST /agents/{id}/setup` |
| `runtime/web/apps/console/src/agents/status.ts` | Pure status → line mapping |
| `runtime/web/apps/console/src/screens/agents/AgentStatusLine.tsx` | Guide status line + Retry |

---

### Task 1: Move the agent manifests to `runtime/agents/`

Pure move; the console must work exactly as before.

**Files:**
- Move: `runtime/web/apps/console/agent-guides/` → `runtime/agents/`
- Modify: `runtime/web/Dockerfile`, `packaging/images/build.sh:68`, `runtime/web/apps/console/vite.config.ts`, `runtime/web/apps/console/src/agents/content.test.ts:7`
- Create: `runtime/agents/CLAUDE.md`
- Modify docs: `runtime/CLAUDE.md`, `runtime/web/CLAUDE.md`

**Interfaces:**
- Produces: manifests at `runtime/agents/index.json` and `runtime/agents/<id>/agent.json`; still served at `/agent-guides/` in production and dev.

- [ ] **Step 1: Move the folder**

```bash
git mv runtime/web/apps/console/agent-guides runtime/agents
rm -f runtime/agents/*/icon.webp:Zone.Identifier
```

- [ ] **Step 2: Point the content test at the new place**

In `runtime/web/apps/console/src/agents/content.test.ts` replace line 7:

```ts
// The manifests live outside the web workspace; the Dockerfile copies them to
// the same relative place (/runtime/agents) so this path holds in the image too.
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "../../../../../agents");
```

- [ ] **Step 3: Run the web tests**

Run (from `runtime/web`): `npx vitest run apps/console/src/agents/content.test.ts`
Expected: PASS (3 agents parsed).

- [ ] **Step 4: Dockerfile and build context**

In `runtime/web/Dockerfile`, after the `COPY --from=fixtures …` line add:

```dockerfile
# The agent manifests live in runtime/agents/, outside this build context;
# build with `--build-context agents=runtime/agents`.
COPY --from=agents . /runtime/agents/
```

and replace the last `COPY --from=build /runtime/web/apps/console/agent-guides …` with:

```dockerfile
# Outside Vite's build output: guides are data read at page load, not bundled.
COPY --from=build /runtime/agents /usr/share/nginx/html/agent-guides
```

In `packaging/images/build.sh` line 68:

```bash
[[ "$only" == api ]] || build eggie-web "$repo/runtime/web" --build-context "fixtures=$repo/tests/fixtures" --build-context "agents=$repo/runtime/agents"
```

`runtime/agents/CLAUDE.md` is served too, as `/agent-guides/CLAUDE.md`. That's harmless (the repo is public), so leave it.

- [ ] **Step 5: Serve the folder in `npm run dev`**

Vite used to serve it because it sat under the app root. Replace `runtime/web/apps/console/vite.config.ts` with:

```ts
import { createReadStream, statSync } from "node:fs";
import { extname, join, normalize, sep } from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";

const AGENTS = fileURLToPath(new URL("../../../agents", import.meta.url));
const TYPES: Record<string, string> = {
  ".json": "application/json", ".svg": "image/svg+xml", ".webp": "image/webp", ".png": "image/png",
};

// Dev only: production gets the same folder from the Dockerfile.
function agentGuides(): Plugin {
  return {
    name: "agent-guides",
    configureServer(server) {
      server.middlewares.use("/agent-guides", (req, res) => {
        const file = normalize(join(AGENTS, decodeURIComponent((req.url ?? "/").split("?")[0])));
        let isFile = false;
        try {
          isFile = file.startsWith(AGENTS + sep) && statSync(file).isFile();
        } catch {
          isFile = false;
        }
        if (!isFile) {
          res.statusCode = 404;
          res.end();
          return;
        }
        res.setHeader("Content-Type", TYPES[extname(file)] ?? "application/octet-stream");
        createReadStream(file).pipe(res);
      });
    },
  };
}

export default defineConfig(({ command }) => ({
  plugins: [react(), agentGuides()],
  // The MSW worker lives in dev-public/, so a production build never ships it.
  publicDir: command === "serve" ? "dev-public" : false,
  // The page's CSP refuses data: fonts, so every asset ships as a file.
  build: { assetsInlineLimit: 0 },
}));
```

Check by hand: `npm run dev`, open `/agents`, the three cards and icons show; `curl -s localhost:5173/agent-guides/index.json` returns the list; `curl -si localhost:5173/agent-guides/../package.json | head -1` is 404.

- [ ] **Step 6: Typecheck and test**

Run (from `runtime/web`): `npm run typecheck && npm test`
Expected: PASS.

- [ ] **Step 7: Docs**

Create `runtime/agents/CLAUDE.md`:

```markdown
# runtime/agents/ — one folder per coding agent

`index.json` orders the agents; `<id>/agent.json` describes one. Adding an agent is a folder plus
one line in `index.json` — no code changes. Two readers, each validating only what it uses:

- the console (`runtime/web/apps/console/src/agents/catalog.ts`) reads `name`, `icon`,
  `platforms` (the guide) and whether `setup` exists; served at `/agent-guides/`;
- `runtime/install/lib/agents.py` reads the install-side fields below, on the VM.

| Field | Meaning |
|---|---|
| `home` | Directory under each account's home, e.g. `.codex`. Detection reads it. |
| `instructions` | Files that get `instructions/eggie.md`: `/abs/path` written whole once, `~/path` as the `<!-- eggie:begin/end -->` block per account. |
| `skills` | The `npx skills add -a` agent name. |
| `detect.ignore` | Top-level names under `home` that Eggie's own install creates (instructions, skills). Anything else there means the agent connected. |
| `setup.run` | Shell command run once per account when the user opens the agent's guide. |

## Things that will bite you

- `setup.run` runs as root and as every login user. A manifest is as trusted as `install.sh`:
  review a new one like a script change.
- `detect.ignore` must list everything the install puts under `home`, or the agent reads as
  connected right after install. Check on a fresh VM with `ls -A ~/<home>`.
- This folder is copied into the web image (`--build-context agents=…`) and read from
  `/opt/eggie/runtime/agents` on the VM by both the runner and the API.
```

In `runtime/CLAUDE.md` "What lives here", add a bullet after `instructions/`:

```markdown
- `agents/` — one manifest folder per coding agent (guide, instructions targets, skills name,
  detection, setup). Own CLAUDE.md.
```

In `runtime/web/CLAUDE.md`, replace "All content is in `apps/console/agent-guides/` (served at `/agent-guides/`)" with "All content is in `runtime/agents/` (served at `/agent-guides/`; the Dockerfile copies it, a Vite plugin serves it in dev)", and in "Things that will bite you" replace the `agent-guides/ is outside Vite's build output` bullet with:

```markdown
- `runtime/agents/` is outside this workspace: the image needs `--build-context agents=runtime/agents`
  (use `packaging/images/build.sh`), and `content.test.ts` reaches it with five `../`, which
  matches `/runtime/agents` in the image only because `WORKDIR` is `/runtime/web`.
```

Also update the web CLAUDE.md "Commands" note: the image build needs both `fixtures` and `agents` build contexts.

- [ ] **Step 8: Commit**

```bash
git add -A runtime/agents runtime/web packaging/images/build.sh runtime/CLAUDE.md
git commit -m "refactor: move agent manifests to runtime/agents

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: `agents.py` — load manifests, `skills`, `instructions`

**Files:**
- Create: `runtime/install/lib/agents.py`
- Modify: `runtime/agents/{claude-code,codex,cursor}/agent.json`
- Test: `tests/runtime/test_agents_manifests.py`

**Interfaces:**
- Produces (CLI, all take `--agents-dir DIR`, default `<script>/../../agents`):
  - `agents.py skills` → stdout: skills names, space-separated, one line.
  - `agents.py instructions --system` → stdout: absolute targets, one per line.
  - `agents.py instructions --home HOME` → stdout: per-account targets with `~` replaced by `HOME`, one per line.
  - Invalid manifest → skipped, `skipping agent <id>: <reason>` on stderr, exit 0. Unreadable `index.json` → exit 1.
- Produces (Python, used by Task 4 in the same file): `Agent` dataclass (`id, home, instructions, skills, ignore, setup`), `load(agents_dir: Path) -> list[Agent]`.

- [ ] **Step 1: Write the failing tests**

`tests/runtime/test_agents_manifests.py`:

```python
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "runtime" / "install" / "lib" / "agents.py"
AGENTS = ROOT / "runtime" / "agents"


def agents_py(*args, agents_dir=AGENTS):
    return subprocess.run([sys.executable, str(SCRIPT), "--agents-dir", str(agents_dir), *args],
                          capture_output=True, text=True)


def write_agents(tmp_path, manifests):
    root = tmp_path / "agents"
    root.mkdir()
    (root / "index.json").write_text(json.dumps({"agents": list(manifests)}))
    for agent_id, manifest in manifests.items():
        (root / agent_id).mkdir()
        (root / agent_id / "agent.json").write_text(json.dumps(manifest))
    return root


def test_every_shipped_manifest_is_valid():
    ids = json.loads((AGENTS / "index.json").read_text())["agents"]
    assert ids, "scanned no agents"
    result = agents_py("skills")
    assert result.returncode == 0
    assert result.stderr == ""


def test_system_wide_and_per_account_instruction_files_are_kept_apart(tmp_path):
    root = write_agents(tmp_path, {
        "a": {"home": ".a", "instructions": ["/etc/a/RULES.md"]},
        "b": {"home": ".b", "instructions": ["~/.b/AGENTS.md"]},
    })
    assert agents_py("instructions", "--system", agents_dir=root).stdout.split() == ["/etc/a/RULES.md"]
    assert agents_py("instructions", "--home", "/home/ada", agents_dir=root).stdout.split() == \
        ["/home/ada/.b/AGENTS.md"]


@pytest.mark.parametrize("bad", [
    {"home": "../etc"},
    {"home": "/root"},
    {"home": ".x", "instructions": ["~/../../etc/passwd"]},
    {"home": ".x", "instructions": ["relative.md"]},
    {"home": ".x", "detect": {"ignore": ["a/b"]}},
    {"home": ".x", "setup": {"run": "  "}},
    {},
])
def test_a_manifest_that_could_escape_or_is_incomplete_is_skipped_by_name(tmp_path, bad):
    root = write_agents(tmp_path, {"good": {"home": ".g", "skills": "good"},
                                   "bad": {**bad, "skills": "bad"}})
    result = agents_py("skills", agents_dir=root)
    assert result.returncode == 0
    assert result.stdout.split() == ["good"]
    assert "bad" in result.stderr


def test_an_unreadable_index_fails(tmp_path):
    root = tmp_path / "agents"
    root.mkdir()
    (root / "index.json").write_text("{nope")
    assert agents_py("skills", agents_dir=root).returncode == 1
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/runtime/test_agents_manifests.py -q`
Expected: FAIL (script missing).

- [ ] **Step 3: Implement `agents.py` (loader + two subcommands)**

`runtime/install/lib/agents.py`:

```python
#!/usr/bin/env python3
"""Reads the coding-agent manifests (runtime/agents/<id>/agent.json) for the
install scripts and the root-side agent runner. Stdlib only: it runs on the
VM's own python3.
  agents.py [--agents-dir DIR] skills
  agents.py [--agents-dir DIR] instructions (--system | --home HOME)
  agents.py [--agents-dir DIR] run STATUS_DIR --path PATH   (accounts on stdin)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ID = re.compile(r"^[a-z0-9][a-z0-9-]*$")
SEGMENT = re.compile(r"^[A-Za-z0-9._-]+$")
DEFAULT_DIR = Path(__file__).resolve().parents[2] / "agents"


@dataclass(frozen=True)
class Agent:
    id: str
    home: str
    instructions: tuple[str, ...]
    skills: str | None
    ignore: frozenset[str]
    setup: str | None


def _inside(path: object) -> bool:
    if not isinstance(path, str) or path == "" or path.startswith("/"):
        return False
    return all(SEGMENT.match(part) and part not in {".", ".."} for part in path.split("/"))


def _target(path: object) -> bool:
    if not isinstance(path, str):
        return False
    if path.startswith("~/"):
        return _inside(path[2:])
    return path.startswith("/") and _inside(path[1:])


def parse(agent_id: str, raw: object) -> Agent:
    if not ID.match(agent_id):
        raise ValueError("the id is not a plain name")
    if not isinstance(raw, dict):
        raise ValueError("agent.json is not an object")
    if not _inside(raw.get("home")):
        raise ValueError("home must be a path inside the account's home")
    instructions = raw.get("instructions", [])
    if not isinstance(instructions, list) or not all(_target(t) for t in instructions):
        raise ValueError("instructions must be /absolute or ~/ paths")
    skills = raw.get("skills")
    if skills is not None and not (isinstance(skills, str) and ID.match(skills)):
        raise ValueError("skills must be a plain name")
    detect = raw.get("detect", {})
    ignore = detect.get("ignore", []) if isinstance(detect, dict) else None
    if not isinstance(ignore, list) or not all(isinstance(n, str) and SEGMENT.match(n) for n in ignore):
        raise ValueError("detect.ignore must be a list of names")
    setup = raw.get("setup")
    if setup is not None and not (isinstance(setup, dict) and isinstance(setup.get("run"), str)
                                  and setup["run"].strip()):
        raise ValueError("setup.run must be a command")
    return Agent(agent_id, raw["home"], tuple(instructions), skills, frozenset(ignore),
                 setup["run"] if setup else None)


def load(agents_dir: Path) -> list[Agent]:
    index = json.loads((agents_dir / "index.json").read_text())
    ids = index.get("agents") if isinstance(index, dict) else None
    if not isinstance(ids, list):
        raise ValueError("index.json has no agent list")
    agents = []
    for agent_id in ids:
        try:
            agents.append(parse(agent_id, json.loads((agents_dir / str(agent_id) / "agent.json").read_text())))
        except (OSError, ValueError) as e:
            print(f"skipping agent {agent_id}: {e}", file=sys.stderr)
    return agents


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--agents-dir", type=Path, default=DEFAULT_DIR)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("skills")
    instructions = sub.add_parser("instructions")
    where = instructions.add_mutually_exclusive_group(required=True)
    where.add_argument("--system", action="store_true")
    where.add_argument("--home")
    args = parser.parse_args(argv)

    try:
        agents = load(args.agents_dir)
    except (OSError, ValueError) as e:
        print(f"cannot read the agent list: {e}", file=sys.stderr)
        return 1

    if args.command == "skills":
        print(" ".join(a.skills for a in agents if a.skills))
    elif args.command == "instructions":
        for agent in agents:
            for target in agent.instructions:
                if args.system and target.startswith("/"):
                    print(target)
                elif args.home and target.startswith("~/"):
                    print(f"{args.home.rstrip('/')}/{target[2:]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
```

- [ ] **Step 4: Check which agent names the skills CLI accepts**

Run: `npx -y skills@1.5.26 add --help 2>&1 | grep -i -A40 agent | head -60` (the version must match `SKILLS_CLI` in `install.sh`).
If `cursor` is not an accepted `-a` value, leave `"skills"` out of Cursor's manifest in Step 5 and say so in the PR. If you have no network, leave it in and list it under "verify on a VM" in the PR.

- [ ] **Step 5: Add the install-side fields to the shipped manifests**

Add these keys to each `agent.json` (top level, after `"icon"`), leaving `platforms` as is:

`runtime/agents/claude-code/agent.json`:
```json
  "home": ".claude",
  "instructions": ["/etc/claude-code/CLAUDE.md"],
  "skills": "claude-code",
  "detect": {"ignore": ["skills"]},
```

`runtime/agents/codex/agent.json`:
```json
  "home": ".codex",
  "instructions": ["~/.codex/AGENTS.md"],
  "skills": "codex",
  "detect": {"ignore": ["AGENTS.md"]},
  "setup": {"run": "curl -fsSL https://chatgpt.com/codex/install.sh | sh"},
```

`runtime/agents/cursor/agent.json`:
```json
  "home": ".cursor",
  "instructions": ["~/.cursor/AGENTS.md"],
  "skills": "cursor",
  "detect": {"ignore": ["AGENTS.md", "skills"]},
```

- [ ] **Step 6: Run the tests**

Run: `.venv/bin/python -m pytest tests/runtime/test_agents_manifests.py -q` and (from `runtime/web`) `npx vitest run apps/console/src/agents`
Expected: PASS (the console ignores the new keys).

- [ ] **Step 7: Commit**

```bash
git add runtime/install/lib/agents.py runtime/agents tests/runtime/test_agents_manifests.py
git commit -m "feat: read coding-agent manifests on the VM

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Install instructions and skills from the manifests

**Files:**
- Modify: `runtime/install/lib/install-agents.sh`, `runtime/install/install.sh` (steps 9 and 11)
- Test: `tests/runtime/test_install_agents.py`
- Docs: `runtime/install/CLAUDE.md`

**Interfaces:**
- Consumes: `agents.py instructions --system|--home`, `agents.py skills` (Task 2).
- `install-agents.sh <runtime-dir> <home> <owner uid:gid>` — unchanged signature; reads `<runtime-dir>/agents` via `agents.py` from its own `lib/`.

- [ ] **Step 1: Update the tests**

In `tests/runtime/test_install_agents.py`, replace `test_the_codex_block_is_replaced_and_the_users_own_text_kept` with a test over a temp agents dir, and add one for several targets:

```python
def _source(tmp_path, manifests):
    source = tmp_path / "src"
    (source / "instructions").mkdir(parents=True)
    shutil.copy(SOURCE / "instructions" / "eggie.md", source / "instructions" / "eggie.md")
    agents = source / "agents"
    agents.mkdir()
    (agents / "index.json").write_text(json.dumps({"agents": list(manifests)}))
    for agent_id, manifest in manifests.items():
        (agents / agent_id).mkdir()
        (agents / agent_id / "agent.json").write_text(json.dumps(manifest))
    return source


def test_the_block_is_replaced_and_the_users_own_text_kept(tmp_path):
    home = tmp_path / "home"
    (home / ".codex").mkdir(parents=True)
    agents_md = home / ".codex" / "AGENTS.md"
    agents_md.write_text("# my notes\nkeep me")  # no trailing newline on purpose
    source = _source(tmp_path, {"codex": {"home": ".codex", "instructions": ["~/.codex/AGENTS.md"]}})

    _install(home, source)
    (source / "instructions" / "eggie.md").write_text("new instructions\n")
    _install(home, source)

    text = agents_md.read_text()
    assert text.startswith("# my notes\nkeep me\n")
    assert text.count("<!-- eggie:begin -->") == 1
    assert "new instructions" in text
    assert "You are working inside an Eggie VM" not in text


def test_every_per_account_target_gets_the_block_and_system_ones_are_left_to_install(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    source = _source(tmp_path, {
        "codex": {"home": ".codex", "instructions": ["~/.codex/AGENTS.md"]},
        "cursor": {"home": ".cursor", "instructions": ["~/.cursor/AGENTS.md"]},
        "claude-code": {"home": ".claude", "instructions": ["/etc/claude-code/CLAUDE.md"]},
    })
    _install(home, source)
    for target in (home / ".codex" / "AGENTS.md", home / ".cursor" / "AGENTS.md"):
        assert "<!-- eggie:begin -->" in target.read_text()
    assert not (home / ".claude").exists()
```

Add `import json` at the top. The other tests keep using `SOURCE` (the real runtime tree, now with `runtime/agents`).

- [ ] **Step 2: Run to verify the new test fails**

Run: `.venv/bin/python -m pytest tests/runtime/test_install_agents.py -q`
Expected: FAIL in `test_every_per_account_target…` (`~/.cursor/AGENTS.md` missing).

- [ ] **Step 3: Rewrite `install-agents.sh`**

```bash
#!/usr/bin/env bash
# Writes Eggie's instructions block into every per-account file the agent
# manifests name, and the ~/projects link, into one home directory. Skills are
# installed separately, with npx; system-wide instructions by install.sh.
# Run by install.sh as root, once per home:
#   install-agents.sh <runtime-dir> <home> <owner uid:gid>
set -euo pipefail

SRC=$1
HOME_DIR=$2
OWNER=$3
LIB="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BEGIN='<!-- eggie:begin -->'
END='<!-- eggie:end -->'
TARGET=/opt/eggie/projects

# Captured first: a failure inside a process substitution would go unnoticed.
targets="$(python3 "$LIB/agents.py" --agents-dir "$SRC/agents" instructions --home "$HOME_DIR")"

# The user may keep their own text in these files: only the block between the
# markers is ours to replace.
while IFS= read -r file; do
  [[ -n "$file" ]] || continue
  mkdir -p "$(dirname "$file")"
  touch "$file"
  sed -i "\|^$BEGIN\$|,\|^$END\$|d" "$file"
  if [[ -s "$file" && -n "$(tail -c1 "$file")" ]]; then
    echo >> "$file"
  fi
  { echo "$BEGIN"; cat "$SRC/instructions/eggie.md"; echo "$END"; } >> "$file"
  chown "$OWNER" "$file" "$(dirname "$file")"
done <<< "$targets"

if [[ ! -e "$HOME_DIR/projects" && ! -L "$HOME_DIR/projects" ]]; then
  ln -s "$TARGET" "$HOME_DIR/projects"
elif [[ -L "$HOME_DIR/projects" && "$(readlink "$HOME_DIR/projects")" == "$TARGET" ]]; then
  :
elif [[ -e "$HOME_DIR/projects" || -L "$HOME_DIR/projects" ]]; then
  echo "left $HOME_DIR/projects alone: it already exists and is not Eggie's link"
fi

if [[ -L "$HOME_DIR/projects" && "$(readlink "$HOME_DIR/projects")" == "$TARGET" ]]; then
  chown -h "$OWNER" "$HOME_DIR/projects"
fi
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/runtime/test_install_agents.py -q`
Expected: PASS.

- [ ] **Step 5: `install.sh` step 9 — system-wide instructions from the manifests**

Replace

```bash
install -d /etc/claude-code
install -m 644 "$RUNTIME_DIR/instructions/eggie.md" /etc/claude-code/CLAUDE.md
```

with

```bash
if ! system_instructions="$(python3 "$INSTALL_DIR/lib/agents.py" --agents-dir "$RUNTIME_DIR/agents" instructions --system)"; then
  echo "the coding-agent manifests in this runtime are damaged" >&2
  exit 1
fi
while IFS= read -r target; do
  [[ -n "$target" ]] && install -D -m 644 "$RUNTIME_DIR/instructions/eggie.md" "$target"
done <<< "$system_instructions"
```

- [ ] **Step 6: `install.sh` step 11 — skills for every manifest's agent**

Before the `while IFS=: read -r name uid gid home; do` loop add:

```bash
if ! skill_agents="$(python3 "$INSTALL_DIR/lib/agents.py" --agents-dir "$RUNTIME_DIR/agents" skills)"; then
  echo "the coding-agent manifests in this runtime are damaged" >&2
  exit 1
fi
read -ra skill_agents <<< "$skill_agents"
```

and in the `npx` line replace `-a claude-code codex -y` with `-a "${skill_agents[@]}" -y`. Update the step comment to `# 11. per account: docker group, agent instructions, ~/projects, skills.`

- [ ] **Step 7: Run the install tests**

Run: `.venv/bin/python -m pytest tests/runtime -q`
Expected: PASS. If a `test_install_shell.py` assertion names `/etc/claude-code/CLAUDE.md` or `claude-code codex`, it is asserting the old hard-coding — drop that assertion, don't re-add the string.

- [ ] **Step 8: Docs**

In `runtime/install/CLAUDE.md` "install.sh — numbered steps": replace "`/etc/claude-code/CLAUDE.md`" with "the system-wide instruction files the agent manifests name (`lib/agents.py instructions --system`)", replace "the Codex block and `~/projects` link (`lib/install-agents.sh`)" with "each manifest's per-account instruction block and the `~/projects` link (`lib/install-agents.sh`)", and replace "`-a claude-code codex`" with "`-a $(agents.py skills)`". Add `test_agents_manifests.py` to the Tests line.

- [ ] **Step 9: Commit**

```bash
git add runtime/install tests/runtime/test_install_agents.py tests/runtime/test_install_shell.py
git commit -m "feat: install agent instructions and skills from the manifests

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: The root-side runner — detection and setup

**Files:**
- Modify: `runtime/install/lib/agents.py` (add `run`)
- Create: `runtime/install/lib/agents-run.sh`, `runtime/install/systemd/eggie-agents.path`, `runtime/install/systemd/eggie-agents.service`
- Modify: `runtime/install/install.sh` (new step 12c)
- Test: `tests/runtime/test_agents_run.py`, one test in `tests/runtime/test_install_shell.py`
- Docs: `runtime/install/CLAUDE.md`

**Interfaces:**
- Consumes: `load()`, `Agent` (Task 2).
- Produces, in `STATUS_DIR` (`/opt/eggie/agent-status`):
  - reads `check` (int) and `setup/<id>` (int); missing/garbage = 0;
  - writes `status.json` (0644): `{"generation": int, "agents": {"<id>": {"connected": bool, "setup": null|"installing"|"ready"|"failed", "setup_generation": int}}}`;
  - writes `setup-<id>.log` (0600).
- `agents-run.sh [status-dir]`; env overrides for tests: `EGGIE_ROOT_HOME`, `EGGIE_SHELLS_FILE`, `EGGIE_APPLY_PATH`, `EGGIE_AGENTS_DIR`.

- [ ] **Step 1: Write the failing tests**

`tests/runtime/test_agents_run.py`:

```python
import json
import os
import stat
import subprocess
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "runtime" / "install" / "lib" / "agents-run.sh"

FAKE_RUNUSER = """#!/usr/bin/env bash
# runuser -u NAME -- CMD...: log who, then run CMD as us.
name=$2; shift 3
echo "$name" >> "$LOG/runuser"
exec "$@"
"""
FAKE_GETENT = """#!/usr/bin/env bash
echo "ada:x:1001:1001::$ADA_HOME:/bin/bash"
"""
SETUP = '[[ ! -e "$HOME/setup-fails" ]] && touch "$HOME/installed"'
MANIFESTS = {
    "codex": {"home": ".codex", "detect": {"ignore": ["AGENTS.md"]}, "setup": {"run": SETUP}},
    "claude-code": {"home": ".claude", "detect": {"ignore": ["skills"]}},
}


def make(tmp_path):
    bin_dir, log, status = tmp_path / "bin", tmp_path / "log", tmp_path / "agent-status"
    root, ada, agents = tmp_path / "root", tmp_path / "ada", tmp_path / "agents"
    for d in (bin_dir, log, status / "setup", root, ada, agents):
        d.mkdir(parents=True)
    for name, text in {"runuser": FAKE_RUNUSER, "getent": FAKE_GETENT}.items():
        (bin_dir / name).write_text(text)
        (bin_dir / name).chmod(0o755)
    (agents / "index.json").write_text(json.dumps({"agents": list(MANIFESTS)}))
    for agent_id, manifest in MANIFESTS.items():
        (agents / agent_id).mkdir()
        (agents / agent_id / "agent.json").write_text(json.dumps(manifest))
    shells = tmp_path / "shells"
    shells.write_text("/bin/bash\n")
    path = f"{bin_dir}:/usr/bin:/bin"
    env = {**os.environ, "PATH": path, "EGGIE_APPLY_PATH": path, "LOG": str(log),
           "EGGIE_ROOT_HOME": str(root), "EGGIE_SHELLS_FILE": str(shells),
           "ADA_HOME": str(ada), "EGGIE_AGENTS_DIR": str(agents)}
    return SimpleNamespace(env=env, status=status, root=root, ada=ada, log=log)


def run(t):
    result = subprocess.run(["bash", str(SCRIPT), str(t.status)], env=t.env,
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return json.loads((t.status / "status.json").read_text())["agents"]


def request(t, agent_id, number):
    (t.status / "setup" / agent_id).write_text(str(number))


def setups_run(t):
    log = t.log / "runuser"
    return log.read_text().split() if log.exists() else []


def test_a_dir_holding_only_eggies_own_files_is_not_connected(tmp_path):
    t = make(tmp_path)
    (t.root / ".codex").mkdir()
    (t.root / ".codex" / "AGENTS.md").write_text("ours")
    (t.root / ".claude" / "skills").mkdir(parents=True)
    agents = run(t)
    assert agents["codex"]["connected"] is False
    assert agents["claude-code"]["connected"] is False


def test_anything_else_in_any_accounts_dir_means_connected(tmp_path):
    t = make(tmp_path)
    (t.ada / ".claude" / "sessions").mkdir(parents=True)
    agents = run(t)
    assert agents["claude-code"]["connected"] is True
    assert agents["codex"]["connected"] is False


def test_a_requested_setup_runs_once_for_every_account(tmp_path):
    t = make(tmp_path)
    request(t, "codex", 1)
    assert run(t)["codex"]["setup"] == "ready"
    assert (t.root / "installed").exists() and (t.ada / "installed").exists()
    run(t)
    assert sorted(setups_run(t)) == ["ada", "root"]


def test_two_requests_before_a_pass_run_setup_once(tmp_path):
    t = make(tmp_path)
    request(t, "codex", 1)
    request(t, "codex", 2)
    run(t)
    assert sorted(setups_run(t)) == ["ada", "root"]


def test_a_failed_setup_says_so_and_a_new_request_retries(tmp_path):
    t = make(tmp_path)
    (t.ada / "setup-fails").touch()
    request(t, "codex", 1)
    assert run(t)["codex"]["setup"] == "failed"
    (t.ada / "setup-fails").unlink()
    request(t, "codex", 2)
    assert run(t)["codex"]["setup"] == "ready"


def test_an_agent_already_connected_is_ready_without_running_setup(tmp_path):
    t = make(tmp_path)
    (t.root / ".codex" / "sessions").mkdir(parents=True)
    request(t, "codex", 1)
    assert run(t)["codex"] == {"connected": True, "setup": "ready", "setup_generation": 1}
    assert setups_run(t) == []


def test_a_setup_left_installing_by_an_interrupted_pass_becomes_failed(tmp_path):
    t = make(tmp_path)
    request(t, "codex", 1)
    (t.status / "status.json").write_text(json.dumps({"generation": 0, "agents": {
        "codex": {"connected": False, "setup": "installing", "setup_generation": 1}}}))
    assert run(t)["codex"]["setup"] == "failed"
    assert setups_run(t) == []


def test_the_setup_log_is_readable_by_root_only(tmp_path):
    t = make(tmp_path)
    request(t, "codex", 1)
    run(t)
    mode = stat.S_IMODE((t.status / "setup-codex.log").stat().st_mode)
    assert mode == 0o600
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/runtime/test_agents_run.py -q`
Expected: FAIL (`agents-run.sh` missing).

- [ ] **Step 3: Add the runner to `agents.py`**

Add imports `import os`, `import subprocess`, `import tempfile` and these constants after `DEFAULT_DIR`:

```python
SETUP_TIMEOUT = "10m"
MAX_PASSES = 5
STATES = {"installing", "ready", "failed"}
```

Add before `main`:

```python
def connected(agent: Agent, homes: list[str]) -> bool:
    for home in homes:
        try:
            names = os.listdir(Path(home) / agent.home)
        except OSError:
            continue
        if any(name not in agent.ignore for name in names):
            return True
    return False


def _number(path: Path) -> int:
    try:
        return int(path.read_text().strip())
    except (OSError, ValueError):
        return 0


def _previous(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    agents = data.get("agents") if isinstance(data, dict) else None
    return agents if isinstance(agents, dict) else {}


def _write(path: Path, value: dict) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".status-")
    with os.fdopen(fd, "w") as f:
        json.dump(value, f)
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def _run_setup(agent: Agent, accounts: list[tuple[str, str]], status_dir: Path, path: str) -> bool:
    log = os.open(status_dir / f"setup-{agent.id}.log", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    ok = True
    with os.fdopen(log, "w") as out:
        for name, home in accounts:
            out.write(f"== {name}\n")
            out.flush()
            argv = ["timeout", SETUP_TIMEOUT, "runuser", "-u", name, "--", "env",
                    f"HOME={home}", f"PATH={path}", "bash", "-c", agent.setup]
            try:
                code = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=out,
                                      stderr=subprocess.STDOUT).returncode
            except OSError as e:
                out.write(f"{e}\n")
                code = 1
            ok = ok and code == 0
    return ok


def run_pass(agents: list[Agent], accounts: list[tuple[str, str]], status_dir: Path,
             path: str, generation: int) -> None:
    status_path = status_dir / "status.json"
    previous = _previous(status_path)
    homes = [home for _, home in accounts]
    out: dict[str, dict] = {}
    for agent in agents:
        before = previous.get(agent.id) if isinstance(previous.get(agent.id), dict) else {}
        setup = before.get("setup") if before.get("setup") in STATES else None
        # Passes are serialized, so an "installing" left behind was interrupted.
        if setup == "installing":
            setup = "failed"
        done = before.get("setup_generation")
        out[agent.id] = {"connected": connected(agent, homes), "setup": setup,
                         "setup_generation": done if isinstance(done, int) else 0}

    for agent in agents:
        entry = out[agent.id]
        wanted = _number(status_dir / "setup" / agent.id)
        if agent.setup is None or wanted <= entry["setup_generation"]:
            continue
        entry["setup_generation"] = wanted
        if entry["connected"]:
            entry["setup"] = "ready"
            continue
        entry["setup"] = "installing"
        _write(status_path, {"generation": generation, "agents": out})
        entry["setup"] = "ready" if _run_setup(agent, accounts, status_dir, path) else "failed"

    for agent in agents:
        out[agent.id]["connected"] = connected(agent, homes)
    _write(status_path, {"generation": generation, "agents": out})


def run(agents: list[Agent], accounts: list[tuple[str, str]], status_dir: Path, path: str) -> None:
    # Triggers that arrive while a pass runs are dropped by systemd, so look again.
    for _ in range(MAX_PASSES):
        start = _number(status_dir / "check")
        run_pass(agents, accounts, status_dir, path, start)
        if _number(status_dir / "check") == start:
            return
```

In `main`, register the subcommand and dispatch:

```python
    runner = sub.add_parser("run")
    runner.add_argument("status_dir", type=Path)
    runner.add_argument("--path", required=True)
```

```python
    elif args.command == "run":
        accounts = []
        for line in sys.stdin:
            parts = line.rstrip("\n").split(":")
            if len(parts) == 4:
                accounts.append((parts[0], parts[3]))
        run(agents, accounts, args.status_dir, args.path)
```

- [ ] **Step 4: Create `agents-run.sh`**

```bash
#!/usr/bin/env bash
# Detects which coding agents have connected and runs the setups the console
# asked for, over root and every login account. Run as root by
# eggie-agents.service and once by install.sh.
#   agents-run.sh [status-dir]
set -euo pipefail

DIR="${1:-/opt/eggie/agent-status}"
LIB="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENTS="${EGGIE_AGENTS_DIR:-$LIB/../../agents}"
ROOT_HOME="${EGGIE_ROOT_HOME:-/root}"
SHELLS="${EGGIE_SHELLS_FILE:-/etc/shells}"
SAFE_PATH="${EGGIE_APPLY_PATH:-/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin}"

accounts="root:0:0:$ROOT_HOME
$(getent passwd | bash "$LIB/login-users.sh" "$SHELLS")"
python3 "$LIB/agents.py" --agents-dir "$AGENTS" run "$DIR" --path "$SAFE_PATH" <<< "$accounts"
```

`chmod +x runtime/install/lib/agents-run.sh`.

- [ ] **Step 5: Run the tests**

Run: `.venv/bin/python -m pytest tests/runtime/test_agents_run.py tests/runtime/test_agents_manifests.py -q`
Expected: PASS.

- [ ] **Step 6: systemd units**

`runtime/install/systemd/eggie-agents.path`:

```ini
[Unit]
Description=Check Eggie's coding agents when the API asks

[Path]
PathChanged=/opt/eggie/agent-status/check

[Install]
WantedBy=multi-user.target
```

`runtime/install/systemd/eggie-agents.service`:

```ini
[Unit]
Description=Detect connected coding agents and run the setups the console asked for
StartLimitIntervalSec=0

[Service]
Type=oneshot
ExecStart=/bin/bash /opt/eggie/runtime/install/lib/agents-run.sh
```

- [ ] **Step 7: Install test, then the install step**

Add to `tests/runtime/test_install_shell.py`:

```python
def test_install_enables_the_agent_runner_and_runs_it_before_the_marker():
    text = INSTALL.read_text()
    assert "systemctl enable --now eggie-agents.path" in text
    assert "install -d -m 2770 -o root -g docker /opt/eggie/agent-status" in text
    assert text.index("systemctl start eggie-agents.service") < text.index(f"> {constants.RUNTIME_MARKER}")
```

Run it: FAIL. Then in `install.sh`, after step 12b, add:

```bash
# 12c. coding agents: the root-side runner the API pokes to detect connected
# agents and run their setup. The API writes counters here, never into a home.
install -d -m 2770 -o root -g docker /opt/eggie/agent-status /opt/eggie/agent-status/setup
chmod 2770 /opt/eggie/agent-status /opt/eggie/agent-status/setup
install -m 644 "$INSTALL_DIR/systemd/eggie-agents.path" \
  "$INSTALL_DIR/systemd/eggie-agents.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now eggie-agents.path
# Not fatal: the console only loses its "connected" line until the next poll.
systemctl start eggie-agents.service || echo "could not check this machine's coding agents" >&2
```

Run: `.venv/bin/python -m pytest tests/runtime -q` → PASS.

- [ ] **Step 8: Docs**

Append to `runtime/install/CLAUDE.md` after the `github-apply.sh` paragraph:

```markdown
`lib/agents-run.sh` runs as root from `systemd/eggie-agents.path` whenever the API writes
`/opt/eggie/agent-status/check`: `lib/agents.py run` marks each agent in the manifests
(`runtime/agents/`) connected or not and runs `setup.run` for every `setup/<id>` request number
above the one it last handled, into `status.json`. systemd drops triggers that arrive during a pass,
so the runner re-reads `check` and passes again (at most 5); a 10-minute setup holds detection up
meanwhile.
```

Add `test_agents_run.py` to the Tests line.

- [ ] **Step 9: Commit**

```bash
git add runtime/install tests/runtime/test_agents_run.py tests/runtime/test_install_shell.py
git commit -m "feat: detect connected agents and run their setup as root

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: API — `GET /agents/status`, `POST /agents/{id}/setup`

**Files:**
- Create: `runtime/eggie_api/core/agents.py`
- Modify: `runtime/eggie_api/core/config.py`, `runtime/eggie_api/core/constants.py`, `runtime/eggie_api/routes/app.py`, `tests/runtime/api/conftest.py`
- Test: `tests/runtime/api/test_api_agents.py`
- Docs: `runtime/eggie_api/CLAUDE.md`

**Interfaces:**
- Consumes: the files Task 4 defines.
- Produces:
  - `GET /agents/status` → `{"agents": {"<id>": {"connected": bool, "setup": null|"installing"|"ready"|"failed"}}}`
  - `POST /agents/{id}/setup` → `{"requested": bool}`; `404 agent_not_found`; `503 agent_setup_unavailable`.

- [ ] **Step 1: Config and the test fixture**

`core/constants.py`, after `GITHUB_DIR`:

```python
AGENT_STATUS_DIR = f"{GUEST_ROOT}/agent-status"
AGENTS_DIR = f"{GUEST_ROOT}/runtime/agents"
```

`core/config.py`: add fields after `github_dir`:

```python
    agent_status_dir: Path = Path(constants.AGENT_STATUS_DIR)
    agents_dir: Path = Path(constants.AGENTS_DIR)
```

and in `from_env`:

```python
            agent_status_dir=Path(env.get("EGGIE_AGENT_STATUS_DIR", constants.AGENT_STATUS_DIR)),
            agents_dir=Path(env.get("EGGIE_AGENTS_DIR", constants.AGENTS_DIR)),
```

`tests/runtime/api/conftest.py` `env` fixture's `ApiConfig(...)`: add

```python
        agent_status_dir=tmp_path / "agent-status",
        agents_dir=tmp_path / "agents",
```

- [ ] **Step 2: Write the failing tests**

`tests/runtime/api/test_api_agents.py`:

```python
import json


def manifest(env, agent_id, setup=True):
    folder = env.config.agents_dir / agent_id
    folder.mkdir(parents=True)
    body = {"home": f".{agent_id}", **({"setup": {"run": "true"}} if setup else {})}
    (folder / "agent.json").write_text(json.dumps(body))


def runner_status(env, agents):
    env.config.agent_status_dir.mkdir(exist_ok=True)
    (env.config.agent_status_dir / "status.json").write_text(json.dumps({"generation": 1, "agents": agents}))


def counter(env, *parts):
    path = env.config.agent_status_dir.joinpath(*parts)
    return int(path.read_text()) if path.exists() else 0


def test_status_gives_the_runners_view_and_asks_for_a_fresh_one(env):
    runner_status(env, {"codex": {"connected": True, "setup": "ready", "setup_generation": 3}})
    assert env.client.get("/agents/status").json() == {"agents": {"codex": {"connected": True, "setup": "ready"}}}
    env.client.get("/agents/status")
    assert counter(env, "check") == 2


def test_a_missing_or_garbled_status_reads_as_no_agents(env):
    assert env.client.get("/agents/status").json() == {"agents": {}}
    (env.config.agent_status_dir / "status.json").write_text('{"agents": {"codex": ')
    assert env.client.get("/agents/status").json() == {"agents": {}}
    runner_status(env, {"codex": "yes", "cursor": {"connected": "maybe", "setup": "exploded"}})
    assert env.client.get("/agents/status").json() == {"agents": {"cursor": {"connected": False, "setup": None}}}


def test_setup_needs_an_agent_that_has_one(env):
    manifest(env, "claude-code", setup=False)
    for agent_id in ("nope", "claude-code"):
        response = env.client.post(f"/agents/{agent_id}/setup")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "agent_not_found"


def test_setup_is_requested_only_when_nothing_is_ready_running_or_connected(env):
    manifest(env, "codex")
    for state, connected in (("ready", False), ("installing", False), (None, True)):
        runner_status(env, {"codex": {"connected": connected, "setup": state}})
        assert env.client.post("/agents/codex/setup").json() == {"requested": False}
    assert counter(env, "setup", "codex") == 0

    runner_status(env, {"codex": {"connected": False, "setup": "failed"}})
    assert env.client.post("/agents/codex/setup").json() == {"requested": True}
    assert env.client.post("/agents/codex/setup").json() == {"requested": True}
    assert counter(env, "setup", "codex") == 2
    assert counter(env, "check") >= 2
```

- [ ] **Step 3: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/runtime/api/test_api_agents.py -q`
Expected: FAIL (404 on the routes).

- [ ] **Step 4: Implement `core/agents.py`**

```python
from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import threading
from pathlib import Path

log = logging.getLogger("eggie.api")

_ID = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_STATES = {"installing", "ready", "failed"}
_SETTLED = {"installing", "ready"}


class UnknownAgent(Exception):
    pass


class AgentStatus:
    """The API's half of the root-side agent runner (install/lib/agents.py):
    it only bumps request counters and reads the runner's status.json, so it
    never needs to see a user's home."""

    def __init__(self, status_dir: Path, agents_dir: Path):
        self._dir = Path(status_dir)
        self._agents_dir = Path(agents_dir)
        self._lock = threading.Lock()

    def status(self) -> dict:
        try:
            self._bump(self._dir / "check")
        except OSError:
            log.exception("could not ask the agent runner for a fresh check")
        return {"agents": self._read()}

    def ensure_setup(self, agent_id: str) -> dict:
        if not self._has_setup(agent_id):
            raise UnknownAgent(agent_id)
        entry = self._read().get(agent_id, {})
        if entry.get("connected") or entry.get("setup") in _SETTLED:
            return {"requested": False}
        self._bump(self._dir / "setup" / agent_id)
        self._bump(self._dir / "check")
        return {"requested": True}

    def _has_setup(self, agent_id: str) -> bool:
        if not _ID.match(agent_id):
            return False
        try:
            manifest = json.loads((self._agents_dir / agent_id / "agent.json").read_text())
        except (OSError, ValueError):
            return False
        return isinstance(manifest, dict) and isinstance(manifest.get("setup"), dict)

    def _read(self) -> dict:
        try:
            data = json.loads((self._dir / "status.json").read_text())
        except (OSError, ValueError):
            return {}
        agents = data.get("agents") if isinstance(data, dict) else None
        if not isinstance(agents, dict):
            return {}
        return {agent_id: {"connected": entry.get("connected") is True,
                           "setup": entry.get("setup") if entry.get("setup") in _STATES else None}
                for agent_id, entry in agents.items()
                if isinstance(agent_id, str) and _ID.match(agent_id) and isinstance(entry, dict)}

    def _bump(self, path: Path) -> None:
        with self._lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                current = int(path.read_text().strip())
            except (OSError, ValueError):
                current = 0
            fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".count-")
            with os.fdopen(fd, "w") as f:
                f.write(str(current + 1))
            os.chmod(tmp, 0o660)
            os.replace(tmp, path)
```

- [ ] **Step 5: Routes**

In `routes/app.py`: import `from ..core.agents import AgentStatus, UnknownAgent`; in `create_app` after `github_link = …` add `agent_status = AgentStatus(config.agent_status_dir, config.agents_dir)`; after the `/connect` route add:

```python
    @router.get("/agents/status")
    def agents_status() -> dict:
        return agent_status.status()

    @router.post("/agents/{agent_id}/setup")
    def agent_setup(agent_id: str) -> dict:
        try:
            return agent_status.ensure_setup(agent_id)
        except UnknownAgent:
            raise ApiError("agent_not_found", "Eggie has nothing to set up for that agent.", 404) from None
        except OSError:
            raise ApiError("agent_setup_unavailable", "This VM can't set up agents yet. "
                           "Restart Eggie and try again.", 503) from None
```

- [ ] **Step 6: Run the API tests**

Run: `.venv/bin/python -m pytest tests/runtime/api -q`
Expected: PASS, including the auth sweeps in `test_api_auth.py` / `test_api_browser_auth.py` (if a sweep needs a sample value for `{agent_id}`, follow how it fills other path params).

- [ ] **Step 7: Docs**

Add to `runtime/eggie_api/CLAUDE.md` "Feature areas":

```markdown
- **Agents** (`core/agents.py`) — `GET /agents/status` and `POST /agents/{id}/setup`. The API never
  reads a home: it bumps counters in `/opt/eggie/agent-status/` (`check`, `setup/<id>`) and returns
  the root runner's `status.json` (`runtime/install/lib/agents.py`), one poll behind. Setup is only
  requested when the agent isn't connected and its setup isn't `installing`/`ready`, so Retry after
  `failed` is the same call. Manifests are read from `/opt/eggie/runtime/agents`.
```

- [ ] **Step 8: Commit**

```bash
git add runtime/eggie_api tests/runtime/api
git commit -m "feat: API reports coding-agent status and requests their setup

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Console — connected badge, guide status line, automatic setup

**Files:**
- Modify: `runtime/web/apps/console/src/agents/catalog.ts`, `catalog.test.ts`, `queries.ts`
- Create: `runtime/web/apps/console/src/agents/status.ts`, `status.test.ts`, `runtime/web/apps/console/src/screens/agents/AgentStatusLine.tsx`
- Modify: `screens/agents/AgentGuide.tsx`, `screens/agents/AgentPicker.tsx`, `screens/agents/Agents.module.css`, `mocks/handlers.ts`

**Interfaces:**
- Consumes: the API routes from Task 5.
- Produces: `Agent.hasSetup: boolean`; `statusLine(name, state) → Line`; `useAgentStatus(watch?: string)`; `useEnsureSetup()`.

- [ ] **Step 1: Failing tests**

Append to `catalog.test.ts` (inside the existing `describe` for `parseAgent`, reusing its `guide()` helper):

```ts
  it("knows whether an agent has a setup, without exposing the command", () => {
    const withSetup = parseAgent("codex", { name: "Codex", icon: "i.svg", setup: { run: "curl x | sh" }, platforms: { mac: guide() } }, "/a");
    const without = parseAgent("cursor", { name: "Cursor", icon: "i.svg", platforms: { mac: guide() } }, "/a");
    expect(withSetup?.hasSetup).toBe(true);
    expect(without?.hasSetup).toBe(false);
  });
```

`src/agents/status.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { statusLine } from "./status";

describe("statusLine", () => {
  it("says connected even when an earlier setup failed", () => {
    expect(statusLine("Codex", { connected: true, setup: "failed" }).kind).toBe("connected");
  });

  it("shows setup progress and failure before waiting", () => {
    expect(statusLine("Codex", { connected: false, setup: "installing" }).kind).toBe("installing");
    expect(statusLine("Codex", { connected: false, setup: "failed" }).kind).toBe("failed");
  });

  it("waits when the runner has not reported this agent yet", () => {
    expect(statusLine("Codex", undefined)).toEqual({ kind: "waiting", text: "Waiting for Codex to connect…" });
    expect(statusLine("Codex", { connected: false, setup: "ready" }).kind).toBe("waiting");
  });
});
```

Run (from `runtime/web`): `npx vitest run apps/console/src/agents` → FAIL.

- [ ] **Step 2: `catalog.ts` and `status.ts`**

In `catalog.ts`: add `hasSetup: boolean` to `interface Agent`, and in `parseAgent`'s return:

```ts
  return { id, name: value.name, icon: `${folder}/${value.icon}`, platforms, hasSetup: isObject(value.setup) };
```

`src/agents/status.ts`:

```ts
export type SetupState = "installing" | "ready" | "failed" | null;
export interface AgentState { connected: boolean; setup: SetupState }
export interface AgentStatuses { agents: Record<string, AgentState> }

export type LineKind = "connected" | "installing" | "failed" | "waiting";
export interface Line { kind: LineKind; text: string }

export function statusLine(name: string, state: AgentState | undefined): Line {
  if (state?.connected) return { kind: "connected", text: `${name} is connected` };
  if (state?.setup === "installing") return { kind: "installing", text: `Getting ${name} ready in your kitchen…` };
  if (state?.setup === "failed") return { kind: "failed", text: `Couldn't get ${name} ready.` };
  return { kind: "waiting", text: `Waiting for ${name} to connect…` };
}
```

Run the tests → PASS.

- [ ] **Step 3: Queries**

Append to `src/agents/queries.ts` (extend the imports: `useMutation, useQuery, useQueryClient` from `@tanstack/react-query`, `type AgentStatuses` from `./status`):

```ts
const POLL_MS = 3000;

// Polls only while a guide is open for an agent that hasn't connected; an API
// without these routes errors once and the console just shows no status.
export function useAgentStatus(watch?: string) {
  return useQuery({
    queryKey: ["agent-status"],
    queryFn: () => api.get<AgentStatuses>("/api/agents/status"),
    retry: false,
    refetchInterval: (query) =>
      watch && !query.state.error && !query.state.data?.agents[watch]?.connected ? POLL_MS : false,
  });
}

export function useEnsureSetup() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.post<{ requested: boolean }>(`/api/agents/${id}/setup`),
    onSettled: () => client.invalidateQueries({ queryKey: ["agent-status"] }),
  });
}
```

- [ ] **Step 4: Status line component and wiring**

`src/screens/agents/AgentStatusLine.tsx`:

```tsx
import { useEffect } from "react";
import { Button, cx } from "@eggie/ui";
import type { Agent } from "../../agents/catalog";
import { useAgentStatus, useEnsureSetup } from "../../agents/queries";
import { statusLine, type LineKind } from "../../agents/status";
import s from "./Agents.module.css";

const KIND: Record<LineKind, string | undefined> = {
  connected: s.statusConnected, installing: undefined, failed: s.statusFailed, waiting: undefined,
};

export function AgentStatusLine({ agent }: { agent: Agent }) {
  const status = useAgentStatus(agent.id);
  const setup = useEnsureSetup();
  const { mutate } = setup;

  useEffect(() => {
    if (agent.hasSetup) mutate(agent.id);
  }, [agent.id, agent.hasSetup, mutate]);

  if (status.error || status.data === undefined) return null;
  const line = statusLine(agent.name, status.data.agents[agent.id]);
  return (
    <div className={cx(s.status, KIND[line.kind])} role="status">
      <span className={s.statusText}>{line.kind === "connected" ? "✓ " : ""}{line.text}</span>
      {line.kind === "failed" && (
        <Button variant="secondary" disabled={setup.isPending} onClick={() => mutate(agent.id)}>Retry</Button>
      )}
    </div>
  );
}
```

In `AgentGuide.tsx`'s `Steps`, right after the closing `</ol>`, add `<AgentStatusLine agent={agent} />` (and the import).

In `AgentPicker.tsx`: `const status = useAgentStatus();` at the top of `AgentPicker`, and inside `cardTop` before `{CHEVRON_RIGHT}`:

```tsx
{status.data?.agents[agent.id]?.connected && <span className={s.badge}>Connected</span>}
```

Append to `Agents.module.css`:

```css
.status {
  display: flex; align-items: center; gap: 10px; padding: 10px 12px; border-radius: 14px;
  background: var(--surface); border: 1px solid var(--line); font: 600 14px var(--font-body); color: var(--ink-2);
}
.statusText { flex: 1; min-width: 0; }
.statusConnected { border-color: var(--yolk); color: var(--ink); }
.statusFailed { color: var(--paprika); }
.badge {
  margin-left: auto; margin-right: 6px; padding: 2px 8px; border-radius: 999px;
  background: var(--yolk-soft); color: var(--ink); font: 700 12px var(--font-body);
}
```

- [ ] **Step 5: Mock API**

In `mocks/handlers.ts` add to `SCENARIOS` (before `"windows"`): `"agents-connected"`, `"agents-installing"`, `"agents-failed"`. Next to the GitHub handlers add:

```ts
    http.get("/api/agents/status", () => {
      if (scenario === "agents-connected") return HttpResponse.json({ agents: { "claude-code": { connected: true, setup: null }, codex: { connected: true, setup: "ready" } } });
      if (scenario === "agents-installing") return HttpResponse.json({ agents: { codex: { connected: false, setup: "installing" } } });
      if (scenario === "agents-failed") return HttpResponse.json({ agents: { codex: { connected: false, setup: "failed" } } });
      return HttpResponse.json({ agents: {} });
    }),
    http.post("/api/agents/:id/setup", () => HttpResponse.json({ requested: true })),
```

- [ ] **Step 6: Typecheck, test, look**

Run (from `runtime/web`): `npm run typecheck && npm test` → PASS.
Then `npm run dev` and check `/agents?scenario=agents-connected` (badges), `/agents/codex?scenario=agents-installing`, `…=agents-failed` (Retry), `/agents/codex` (Waiting…), in light and dark.

- [ ] **Step 7: Commit**

```bash
git add runtime/web/apps/console
git commit -m "feat: console shows whether a coding agent is connected and sets it up

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Full verification and PR

- [ ] **Step 1: Whole suites**

Run: `.venv/bin/python -m pytest -q` and (from `runtime/web`) `npm run typecheck && npm test && npm run build && npm run check-offline`
Expected: all PASS. Paste the summary lines in the PR.

- [ ] **Step 2: Image build (if Docker is available)**

Run: `packaging/images/build.sh --only web`
Expected: builds; the in-image `npm test` finds `/runtime/agents`. If Docker isn't available, say so in the PR.

- [ ] **Step 3: Push and open the PR**

```bash
git push -u origin feature/43-agent-registry
gh pr create --base main --title "Agent registry: detect connected coding agents and set them up" --body "<summary, Closes #43, test results, and the VM checklist below>

🤖 Generated with [Claude Code](https://claude.com/claude-code)"
```

The PR body must carry this live-VM checklist (not automatable here):
- fresh install: `ls -A ~/.claude ~/.codex ~/.cursor` shows only ignored entries; console says "Waiting…" for all three;
- Cursor CLI reads `~/.cursor/AGENTS.md`; `skills add -a cursor` succeeds and writes only what `detect.ignore` lists;
- connect Claude Code from the desktop app → "✓ Claude Code is connected" within a few seconds;
- open the Codex guide → "Getting Codex ready…" → `codex --version` works in the VM; `/opt/eggie/agent-status/setup-codex.log` is `0600`.

- [ ] **Step 4: Code review by a separate agent** (user's rule), then address findings.
