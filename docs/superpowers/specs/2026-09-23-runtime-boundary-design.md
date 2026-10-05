# Runtime boundary: dissolving `engine/`, naming the guest component

Date: 2026-09-23
Status: proposed

## 1. Problem

Three things are wrong with the current top-level layout, and all three get more
expensive the day the first host binary ships.

**The word `agent` means two things.** `agent/` is the FastAPI service in the VM.
"Agent" everywhere else — `engine/lib/install-agents.sh`, `/opt/eggie/agents`,
`-a claude-code codex` — means a coding agent: Claude Code, Codex, Cursor. The
collision is already in the tree, and the industry has taken the word. It is not
reclaimable.

**The word `engine` is about to mean a third thing.** It meant "everything in the
VM" (`docs/superpowers/specs/2026-09-14-host-shell-engine-split-design.md`,
decision 8: *"Both together are the engine"*). The skills are moving to their own
repository, and once they leave, what remains under `engine/` is the compose
stack, the API's own CLI client, and the installer — none of which is an engine in
any sense the name carried.

**`web/` has no stated relationship to the service it is the face of.** It sits at
the top level as a peer of `agent/`, but it cannot even run its own test suite
without reaching outside itself: `web/Dockerfile:14` needs
`--build-context fixtures=tests/fixtures` to get `slugify-cases.json`, which the
agent's tests also read. The two release as one unit under one version, held
there by three tests in `tests/test_constants_agree.py`, and nothing in the
layout says so.

Nothing has shipped. Guest paths, environment variables and tag names are still
free to change. After the first host binary is out, `EGGIE_ENGINE_URL`,
`/opt/eggie/engine.version` and their siblings are frozen for every host in the
field, and the vocabulary outlives the concepts permanently.

## 2. Decisions

| # | Decision |
|---|---|
| 1 | The guest component is named **`runtime`**. It says what it does, carries no AI association, and stays true when the target moves from a VM to a VPS. |
| 2 | `engine/` **dissolves**. It is not renamed — once the skills leave, every remaining file has a home inside `runtime/`. |
| 3 | The skills move to **`github.com/eggie-io/eggie-skills`** and are installed from there by pinned ref. They leave this repository in this change — see §6. |
| 4 | `web/` is a **sibling of the service inside `runtime/`**, never nested in the Python package. Two images behind Traefik are peers, not container and contained. |
| 5 | `instructions/eggie.md` goes with **`runtime`**, not the skills. It documents the `eggie` CLI and must move in lockstep with `cli/eggie.py`; alone in a skills repo it would describe something that may not be installed. `install-agents.sh` reads it and nothing else from the tree, so this costs nothing. |
| 6 | The two published images are **`eggie-api`** and **`eggie-web`**. Traefik already names the first router `eggie-api` (`engine/stack.yml:78`), so this adopts a name the stack half-uses already. |
| 7 | `/opt/eggie/agent.token` becomes **`/opt/eggie/api.token`** — see §7. |
| 8 | The rename is **complete, not staged**: the Python package, the host's client classes and the prose all move in this change, before the first installation. Staged inside the PR as ordered commits — see §8. |
| 9 | The skills install **unpinned** from `main` at first, through an `EGGIE_SKILLS_SOURCE` override that makes pinning a later config change rather than a code change — see §12. |

## 3. Target layout

### Repository

```
runtime/                     ← the future eggie-runtime repo, verbatim
  eggie_api/                ← the Python package, was agent/ (see §8)
    __init__.py              ← __version__, the pair's release version
    core/
    routes/                  ← was agent/api/; eggie_api.api would stutter
    Dockerfile
    Dockerfile.debug
    pyproject.toml
  web/                       ← unchanged npm workspace
  cli/eggie.py              ← the API's own client, was engine/cli/
  instructions/eggie.md     ← was engine/instructions/
  install/                   ← was engine/{get,install}.sh + engine/lib/
    get.sh
    install.sh
    lib/{install-agents.sh,login-users.sh}
  stack.yml
  stack.debug.yml

host/                        ← unchanged
packaging/                   ← unchanged paths, updated contexts
tests/
  host/                      unchanged
  runtime/                   was tests/agent/ + tests/engine/ minus test_skills.py
  fixtures/                  unchanged — shared until the repo split
  test_constants_agree.py    spans all components; stays at the root
  test_no_platform_leak.py   spans host and runtime; stays at the root
```

`engine/skills/` and `tests/engine/test_skills.py` leave for the skills
repository. `runtime/` is a future repository root exactly as written, so the
eventual split is `git filter-repo --path runtime/` and nothing else.

### Guest

```
/opt/eggie/runtime/         install/, cli/, instructions/, stack.yml
/opt/eggie/runtime.version
/opt/eggie/api.token
```

There is no `/opt/eggie/skills/`. Skills are no longer unpacked from the
tarball; `skills add` fetches them from GitHub into each account's own skill
directories, which is where they already lived after install.

## 4. Scope

**In scope** — one PR, one live-VM verification run:

- Every directory move in §3.
- The contract renames in §5.
- Extracting `engine/skills/` to `eggie-skills` and repointing `install.sh`'s
  one `skills add` argument at it (§6).
- Path updates in the eight non-Python files that name these directories:
  `agent/Dockerfile`, `web/Dockerfile`, `engine/stack.yml`, `engine/get.sh`,
  `packaging/images/build.sh`, both PyInstaller `.spec` files, `.vscode/launch.json`.
- Re-anchoring the three boundary tests (§9).
- Renaming the Python package `agent` → `eggie_api`, and `agent/api/` →
  `eggie_api/routes/`. 66 import lines across 29 files.
- Renaming the host's client vocabulary: `AgentClient` → `ApiClient`,
  `AgentError` → `ApiError`, `AgentUnavailableError` → `ApiUnavailableError`,
  and the 588 comments, test names and prose lines that describe the service
  rather than a coding agent (§8).
- A `README.md` for `eggie-skills` (§13).

**Explicitly out of scope:**

- **Splitting `install.sh` in two.** It is two installers in one file — Docker,
  token, stack and the CLI shim on one side; Node, `gh`, the Codex block and
  `skills add` on the other. Moving the skills source (§6) does not require that
  split and does not perform it. Making the second half a standalone installer,
  so a laptop with no VM can run it, is the next spec.
- **The repository split itself**, including the replacement for
  `test_constants_agree.py` — today the only thing holding six version and API
  declarations equal, and something no single repo will be able to see all sides
  of.

## 5. Rename inventory

Every name below is a host-visible contract or a published artifact, and every
one is free today and frozen after the first shipped host.

| Old | New | Declared in |
|---|---|---|
| `EGGIE_ENGINE_REPO` | `EGGIE_RUNTIME_REPO` | `get.sh` |
| `EGGIE_ENGINE_REF` | `EGGIE_RUNTIME_REF` | `get.sh` |
| `EGGIE_ENGINE_REPAIR` | `EGGIE_RUNTIME_REPAIR` | `get.sh`, `host/core/bootstrap.py` |
| `ENGINE_URL` → `…/main/engine/get.sh` | `RUNTIME_URL` → `…/main/runtime/install/get.sh` | `host/core/constants.py:34` |
| `ENGINE_MARKER` = `/opt/eggie/engine.version` | `RUNTIME_MARKER` = `/opt/eggie/runtime.version` | `host/core/constants.py:36` |
| `GUEST_TOKEN` = `/opt/eggie/agent.token` | `/opt/eggie/api.token` | `host/core/constants.py:41`, `install.sh:93` |
| `/opt/eggie/engine/` | `/opt/eggie/runtime/` | `get.sh`, `install.sh` |
| `engine-v*` tags | `runtime-v*` | `get.sh` `resolve_ref`, release process |
| `eggie-agent` image | `eggie-api` | `stack.yml:40`, `build.sh` |
| `EGGIE_AGENT_IMAGE` | `EGGIE_API_IMAGE` | `stack.yml:40` |
| compose service `agent` | `api` | `stack.yml:39`, `install.sh:138` |
| `ARG AGENT_VERSION` | `ARG SERVICE_VERSION` | `Dockerfile`, `build.sh` |
| `ENV EGGIE_AGENT_VERSION` | `ENV EGGIE_SERVICE_VERSION` | `Dockerfile:20` |
| `$ENGINE_DIR/skills` | `$EGGIE_SKILLS_SOURCE` (§6) | `install.sh:223` |
| `Readiness.engine_version` | `Readiness.runtime_version` | `host/core/status.py:25` |
| `Readiness.agent_api` | `Readiness.api_version` | `host/core/status.py:26` |
| image account `agent` | `api` | `Dockerfile` `useradd`/`USER` |

**`AGENT_VERSION` must not become `API_VERSION`.** `agent/core/constants.py`
already defines `API_VERSION` as the wire protocol number, which is a different
thing on a different release cadence; reusing the name would collapse two
independent versions into one word. `SERVICE_VERSION` keeps them apart. The
`ENV` companion to the Dockerfile's `ARG SERVICE_VERSION` follows the same
rule.

**The image's Linux account renames `agent` → `api` alongside everything
else.** `Dockerfile.debug` builds `FROM` a published release tag rather than
the working tree, so an unbumped default there pins debugging to a stale
image -- and because Docker never validates `USER` at build time, a debug
image built before this account rename would fail only at `docker run`, not
at build. `test_the_debug_image_is_built_on_the_current_release_not_a_stale_one`
(`tests/test_constants_agree.py`) exists to catch exactly that.

`EGGIE_WEB_IMAGE` and `eggie-web` are already correct and do not change.

## 6. The skills become remote

`install.sh:223` today reads:

```
npx -y "$SKILLS_CLI" add "$ENGINE_DIR/skills" -s '*' -g -a claude-code codex -y
```

The argument is a **local filesystem path**. The skills are *distributed* in the
engine tarball and merely *filed* by the skills CLI — which is the real reason
they feel entangled, and something no directory move would fix.

The CLI accepts remote sources: `owner/repo` shorthand, full GitHub URLs, other
git hosts, and a `/tree/<ref>/<path>` form that carries a ref. So the entire
change is the argument:

```
SKILLS_SOURCE="${EGGIE_SKILLS_SOURCE:-eggie-io/eggie-skills}"
npx -y "$SKILLS_CLI" add "$SKILLS_SOURCE" -s '*' -g -a claude-code codex -y
```

This *simplifies* the rest of the design rather than complicating it. `get.sh`
keeps extracting exactly one directory, as it does today; there is no
`/opt/eggie/skills/`; and `tests/engine/test_skills.py` moves to the skills repo
where it belongs, since it only ever checked frontmatter, hand-off names and
bundled files.

**What it costs.** The skills leave the reach of
`tests/test_constants_agree.py` — the first piece of this project to live outside
the one thing holding its declarations in agreement, and a preview of the problem
§13 describes. §12 says what that buys and what it defers.

**New release gate.** `eggie-skills` must be public and populated before any
install works, exactly like the ghcr images. The repository exists and is empty;
populating it — with the skills and a README — is part of this change.

## 7. Why `agent.token` gets renamed

This was left open, on the grounds that the token has no AI-association pressure
behind it — nobody reads `agent.token` and thinks of an LLM.

Rename it anyway. It is the one remaining host-contract path that a person
debugging a box will actually look at, and leaving it sitting next to
`runtime.version` and an `eggie-api` container makes it the single surviving
trace of the word this whole change exists to remove. The cost is two lines
(`host/core/constants.py:41`, `install.sh:93`) inside a change that already
requires a live-VM run to prove the guest paths. Done separately later, it would
need its own run — so it is strictly cheaper now, and "cheap now, frozen after
the first shipped host" applies to it exactly as it does to the others.

## 8. The rename is complete, and ordered

A half-rename leaves `runtime/agent/` sitting next to `runtime/web/` and an
`eggie-api` container indefinitely, and deferred cleanup of that shape does not
get done. Everything moves in this change, before the first installation.

The concern that argued for staging was never cost — it was **blast radius during
verification**. Only the guest-path renames need the live-VM run; if a package
rename is in flight at the same time, a failed run has two unrelated classes of
mistake in it. That is a commit-ordering problem, not a reason to defer, so:

1. **Moves.** `git mv` only, no content edits. `pytest` green.
2. **Package rename.** `agent` → `eggie_api`, `agent/api/` → `eggie_api/routes/`,
   66 imports, `pythonpath = ["runtime"]` in the root pytest config. Pure Python,
   touches no guest path, proved entirely by `pytest`. Green before step 3 starts.
3. **Contract renames.** §5. The only commit the live run has to judge.
4. **Skills extraction.** §6.
5. **Vocabulary sweep.** The host's client classes and the prose around them.

Steps 2 and 5 are reviewable on their own and cannot break the VM. Step 3 arrives
at the live run alone.

### Why the package is `eggie_api`, not `api`

At test time a package's name is its directory name — `package-dir` in
`agent/pyproject.toml` only affects builds. So `runtime/api/` would mean
`import api.core`, claiming a top-level name generic enough to collide with
anything on `sys.path`. `eggie_api` is unambiguous and matches the dist name the
image already publishes. `runtime/eggie_api/` reads slightly against its plain
siblings (`web/`, `cli/`, `install/`), which is the ordinary cost of Python
package naming and not worth fighting.

`agent/api/` becomes `eggie_api/routes/` rather than `eggie_api/api/`, which
would stutter on every import.

### The vocabulary sweep is a review, not a `sed`

Of the 812 lines containing "agent", **224 mean a *coding* agent** — Claude Code,
Codex, Cursor — and must keep the word: `install-agents.sh`, `-a claude-code
codex`, `.codex/AGENTS.md`, and every comment reading "the coding agent". A
blanket replace would corrupt exactly the usage that is correct.

So step 5 is read-and-decide per occurrence, not a mechanical pass. It is still
low-risk — nothing outside the repo depends on any of it, and `pytest` plus
`vitest` prove it — but it should be reviewed as its own commit rather than
buried in a move.

## 9. Risks

**A boundary test that passes while scanning nothing.** `tests/test_no_platform_leak.py`,
`tests/host/test_no_agent_import.py` and `tests/agent/test_no_host_import.py` each
resolve a tree from `__file__` and walk it. CLAUDE.md records that a
cwd-relative or stale glob passing vacuously has bitten this repo three times.
After the move all three can go green while enforcing nothing, silently retiring
the two hardest invariants in the codebase. Each must be updated *and* have its
"assert it scanned something" guard confirmed by hand — ideally by deliberately
introducing a violation and watching the test fail before trusting it.

**`install.sh` self-locates.** `ENGINE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"`
currently resolves to the directory holding `stack.yml`, `cli/` and `instructions/`.
Once the script lives in `runtime/install/`, that is one level too deep: it needs
an explicit `RUNTIME_DIR="$(dirname "$INSTALL_DIR")"`, and every `$ENGINE_DIR/…`
reference must be audited for which of the two it meant.

**Skills now need the network at install time.** `skills add` reaches GitHub
where it previously read a local directory. `install.sh` already needs apt,
NodeSource, the npm registry and ghcr, so this adds no new class of dependency —
but it does add a new *host*, and the existing failure message ("the npm registry
may be unreachable") becomes wrong and should name GitHub too.

**Build contexts.** `packaging/images/build.sh` passes `$repo/agent` and
`$repo/web` as Docker contexts and `--build-context fixtures=$repo/tests/fixtures`;
both PyInstaller specs and `.vscode/launch.json` also name these paths. Eight
files total, but a wrong context fails at image-build time, not at test time.

**Images must be re-pushed under the new name.** `eggie-api` is a new ghcr
package, and CLAUDE.md's standing gotcha applies: a newly pushed package is
private, and `install.sh` fails the whole install on any image it cannot pull.
Its visibility needs flipping by hand before the first install against the new
name.

## 10. Verification

- `python3 -m pytest -q` — 738 tests green on `main` as of 2026-09-23; they cover
  every move and rename in §3–§5 except the guest paths.
- `cd runtime/web && npm test && npm run typecheck && npm run build && npm run check-offline`.
- `packaging/images/build.sh` without `--push` — proves both contexts resolve.
- A **live WSL2 run**: `create_vm` → bootstrap → `verify`, from a host whose
  `RUNTIME_URL` points at the branch. This is the only proof for the guest-path
  renames and for `skills add` against the real `eggie-skills` repo, and it is
  the real cost of the change. Per project convention it is driven from
  PowerShell on the Windows side, not from the WSL dev shell.

## 11. Sequencing

All of it lands in one PR, as the five commits in §8. One PR rather than two
because every alternative rebases a whole-tree move, and `git mv` conflicts are
the worst kind to resolve twice.

Only one part of it has a deadline, and that is worth keeping visible while
reviewing:

| Part | Deadline |
|---|---|
| Moves (§3), package rename (§8), vocabulary sweep (§8) | None. Same price forever. |
| Published names — `eggie-api`, `runtime-v*` (§5) | None, but gated on flipping ghcr visibility by hand. |
| Skills extraction (§6) | None, but gated on populating `eggie-skills`. |
| **Host contract — guest paths, env vars, `api.token` (§5)** | **The first shipped host binary.** After that it is not a rename but a commitment to support both vocabularies forever. |

If the PR has to be cut down under review, that last row is what must survive.
Everything above it can be redone at leisure.

## 12. The skills ship unpinned, for now

`eggie-skills` gets no tags yet. `install.sh` uses the `owner/repo` shorthand,
which the CLI documents plainly, and skips the question of whether a bare
`…/tree/<tag>` URL resolves a whole repository — the documented `/tree/` examples
all point at a single skill directory, so that form would need verifying before it
could be relied on.

```
SKILLS_SOURCE="${EGGIE_SKILLS_SOURCE:-eggie-io/eggie-skills}"
```

The override is the point. Every box installs whatever `main` holds at that
moment, which is the right trade for a PoC with one author and no released host —
and when pinning is wanted, it becomes a value change, not a code change. Tags
can arrive with the install split (§4, out of scope) without touching
`install.sh` again.

What this defers rather than solves: a box's skills are not identifiable from its
`runtime.version`. Until pinning lands, "which skills does this VM have" is
answerable only as "whatever `main` held when it was installed".

## 13. Repositories, and when to split

Three repositories, created in this order:

| Repository | Exists | Populated by |
|---|---|---|
| `eggie-skills` | yes, empty | **this change** (§6) |
| `eggie` (this one) | yes | stays the host + runtime home for now |
| `eggie-runtime` | not yet | a later change — reserve the name now, split later |

**Create `eggie-runtime` now if you like — but do not split into it yet.** The
name is worth reserving and an empty repository costs nothing. The split itself
is blocked on a problem this spec deliberately does not solve:
`tests/test_constants_agree.py` holds six version and API declarations equal
across the host, the API, the web page and the in-VM CLI. It works because one
repository can import all of them. After the split it can import at most half,
and the host↔API agreement — `API_VERSION` against `SUPPORTED_API`, and every
guest path in §5 — loses its only enforcement.

Replacing it is real design work: the API would publish its contract as a small
machine-readable file, and the host repository would vendor or fetch it and check
against it in CI. That is the next spec after the install split, and §6's
unpinned skills source is a first taste of the same problem — a cross-repo
agreement with nothing watching it.

Doing the boundary work first is what makes that spec writable: after this
change, the split is `git filter-repo --path runtime/` and the contract surface
is exactly the table in §5.

### README and bundling

`eggie-skills` gets a short `README.md` in this change — one paragraph on what
the skills are, the `npx skills add eggie-io/eggie-skills` line, and a
sentence each on the five skills. It is the public face of the extraction, the
repository is public, and an empty repository that `install.sh` points at is
worse than no repository.

A bundling or scaffolding script is **not** in this change. What it should build
is not yet clear — a one-line installer for the skills alone, a scaffolder for new
repositories of this shape, or something else — and the answer depends on how
`install.sh` splits (§4, out of scope). Writing it before that is guessing.

## 14. As-built

Execution (Tasks 1-6) matched this spec with seven additions, none of which
change the decisions above:

- **`agent_unconfigured` → `api_unconfigured` joined the rename inventory (§5).**
  The table missed this wire error code; it is renamed everywhere the API returns
  it and everywhere the host and the guest CLI match on it.
- **`AGENT_PORT`, `EGGIE_AGENT_PORT`, `EGGIE_AGENT_TOKEN` and `EGGIE_AGENT_HOST`
  → their `API_*` / `EGGIE_API_*` equivalents.** Also missing from §5 — found
  during the vocabulary sweep (§8 step 5), not planned for ahead of time.
- **The package rename shipped in this change, not staged separately**, as §8
  already concludes — recorded here because staging it was raised again during
  execution and the user chose, again, to keep it in scope rather than open a
  second PR.
- **`test_stack_recipes.py` left with the skills**, not just `test_skills.py`
  (§6). It tested the compose recipes bundled in `eggie-stack/references/`, so
  it belongs with the skill content it exercises, not with this repository.
- **`Readiness.engine_version` → `runtime_version` and `Readiness.agent_api` →
  `api_version`** (§5). Also missing from the table; these are the status
  probe's own field names, read by both the desktop UI markup and
  `DesktopApi.home()`.
- **`EGGIE_AGENT_VERSION` → `EGGIE_SERVICE_VERSION`** (§5), the `ENV`
  companion to `ARG SERVICE_VERSION` that the table listed on its own.
- **The image's Linux account `agent` → `api`** (§5), which is why
  `Dockerfile.debug`'s `USER api` and the version guard in
  `test_the_debug_image_is_built_on_the_current_release_not_a_stale_one` had
  to exist: a stale debug image built before this rename would fail only at
  `docker run`, since Docker never validates `USER` at build time.
