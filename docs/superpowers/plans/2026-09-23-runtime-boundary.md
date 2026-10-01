# Runtime Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Regroup the guest side of this repository under `runtime/`, dissolve `engine/`, move the skills to their own public repository, and rename every "agent"/"engine" contract name before the first host binary freezes them.

**Architecture:** Seven tasks, ordered so that only Task 3 changes anything the VM can observe. Tasks 1, 2, 5 and 6 are proved entirely by `pytest` and `vitest`; Task 4 adds a public repository; Task 7 is the single live-VM run that judges Task 3. No behaviour changes anywhere — every task is a move or a rename, and the suite that passes before must pass after.

**Tech Stack:** Python 3.12 + pytest, bash + `bash -n`, npm workspace + Vitest, Docker buildx, git.

**Spec:** `docs/superpowers/specs/2026-09-23-runtime-boundary-design.md`

## Global Constraints

- **Baseline to preserve:** `738 passed` from `python3 -m pytest -q`. Every task ends with that number or higher, never lower.
- **`TMPDIR` must be set** on every pytest invocation in this WSL sandbox — `/tmp/pytest-of-$USER` is root-owned and breaks `tmp_path`. Use `TMPDIR=$(mktemp -d) python3 -m pytest -q`.
- **No test may spawn `wsl.exe`/`limactl`, touch a real VM, or reach the network.** Engine shell scripts are the standing exception: they run under `bash` against fakes on `PATH`.
- **Never run `wsl.exe` into `omelet-vm` from this shell.** Task 7's commands are handed to the user for PowerShell.
- **224 of the 812 lines containing "agent" mean a *coding* agent** — `install-agents.sh`, `-a claude-code codex`, `.codex/AGENTS.md`, every "the coding agent" comment. These keep the word. Task 5 is a review, never a blanket `sed`.
- **Host runtime dependencies stay exactly** `typer`, `pywebview`, `pythonnet` (win32), `pyobjc-core`/`pyobjc-framework-Cocoa`/`pyobjc-framework-WebKit` (darwin). `tests/host/test_host_dependencies.py` enforces it.
- **`host/` never imports the API package, and the API package never imports `host/`.** Both directions are AST-enforced and both anchors move in Task 2.
- **Commit after every task.** Branch: `feature/runtime-boundary`, cut from `main`.

---

### Task 0: Branch and baseline

**Files:** none

- [ ] **Step 1: Cut the branch from main**

```bash
git checkout main
git checkout -b feature/runtime-boundary
```

- [ ] **Step 2: Record the baseline**

Run: `TMPDIR=$(mktemp -d) python3 -m pytest -q 2>&1 | tail -2`
Expected: `738 passed`

- [ ] **Step 3: Record the web baseline**

Run: `cd web && npm install && npm test && npm run typecheck && cd ..`
Expected: all green. Note the Vitest test count; Task 1 must preserve it.

---

### Task 1: Move the non-Python tree into `runtime/`

Moves `web/` and everything under `engine/` except the skills. The Python package stays where it is — Task 2 handles it, so that a failure here cannot be an import problem.

**Files:**
- Move: `web/` → `runtime/web/`
- Move: `engine/cli/` → `runtime/cli/`
- Move: `engine/instructions/` → `runtime/instructions/`
- Move: `engine/get.sh`, `engine/install.sh`, `engine/lib/` → `runtime/install/`
- Move: `engine/stack.yml`, `engine/stack.debug.yml` → `runtime/`
- Move: `tests/engine/` → `tests/runtime/` (carries `test_skills.py` for now; Task 4 removes it)
- Modify: `runtime/web/Dockerfile` (WORKDIR), `runtime/web/apps/console/src/projects/slugify.test.ts:6`
- Modify: `packaging/images/build.sh`, `tests/runtime/cli/loader.py`, `tests/test_constants_agree.py`, `tests/host/test_no_guest_assets.py:17`, `.vscode/launch.json`

**Interfaces:**
- Produces: the path prefix `runtime/` for `web`, `cli`, `instructions`, `install`, `stack.yml`, `stack.debug.yml`; and the test package `tests.runtime` (was `tests.engine`).
- Consumes: nothing.

- [ ] **Step 1: Move the directories**

```bash
mkdir -p runtime/install
git mv web runtime/web
git mv engine/cli runtime/cli
git mv engine/instructions runtime/instructions
git mv engine/get.sh engine/install.sh runtime/install/
git mv engine/lib runtime/install/lib
git mv engine/stack.yml engine/stack.debug.yml runtime/
git mv tests/engine tests/runtime
```

`engine/` now holds only `skills/`. Leave it — Task 4 empties it.

- [ ] **Step 2: Run the suite to see exactly what broke**

Run: `TMPDIR=$(mktemp -d) python3 -m pytest -q 2>&1 | tail -20`
Expected: FAIL. Failures come from `tests/runtime/cli/loader.py` (path to `omelet.py`), `tests/test_constants_agree.py` (paths to `stack.yml` and `version.ts`) and the engine-script tests. This is the failing-test step: the suite is the test, and it must fail here before you fix anything.

- [ ] **Step 3: Re-anchor the guest CLI loader**

`tests/runtime/cli/loader.py` — the path is anchored to `__file__`, which is correct; only the tail changes:

```python
GUEST_CLI = (Path(__file__).resolve().parents[3]
             / "runtime" / "cli" / "omelet.py")
```

- [ ] **Step 4: Prove the loader is not passing vacuously**

The anchor above is `parents[3]`, and `tests/runtime/cli/loader.py` is three levels below the root — the same depth `tests/engine/cli/loader.py` was, so the number does not change. Confirm it resolves rather than assuming:

Run: `python3 -c "from pathlib import Path; p = Path('tests/runtime/cli/loader.py').resolve().parents[3] / 'runtime' / 'cli' / 'omelet.py'; print(p, p.exists())"`
Expected: an absolute path ending `runtime/cli/omelet.py` and `True`.

- [ ] **Step 5: Re-anchor the constants test**

In `tests/test_constants_agree.py`, three literals change. `test_the_stack_deploys_the_image_version_the_agent_reports`:

```python
    stack = re.search(r"\$\{OMELET_AGENT_IMAGE:-[^}]+:([^}:]+)\}",
                      (root / "runtime" / "stack.yml").read_text())
```

`test_the_web_page_speaks_the_api_the_agent_serves`:

```python
    source = (Path(__file__).resolve().parent.parent / "runtime" / "web"
              / "apps" / "console" / "src" / "api" / "version.ts").read_text()
```

`test_the_web_image_ships_with_the_agent_it_was_built_against`:

```python
    stack = (Path(__file__).resolve().parent.parent / "runtime" / "stack.yml").read_text()
```

Leave `agent/Dockerfile` alone — Task 2 moves it.

- [ ] **Step 6: Fix the engine-script test paths**

Every path literal under `tests/runtime/` that names `engine/` becomes `runtime/install/` (for `get.sh`, `install.sh`, `lib/`) or `runtime/` (for `stack.yml`, `cli/`, `instructions/`). Find them:

Run: `grep -rn '"engine"\|engine/' tests/runtime/`

Change each to the new location. `tests/runtime/test_skills.py` still points at `engine/skills` — leave that one; Task 4 removes the file.

- [ ] **Step 7: Fix the web build's fixture depth**

This one is a trap. `slugify.test.ts` reaches the shared fixture by relative depth, and the depth must agree between the repo checkout and the Docker image.

`runtime/web/apps/console/src/projects/slugify.test.ts:6` — five `../` becomes six:

```ts
  readFileSync(new URL("../../../../../../tests/fixtures/slugify-cases.json", import.meta.url), "utf-8"),
```

`runtime/web/Dockerfile` — `WORKDIR /web` becomes `WORKDIR /runtime/web`, so that six levels up inside the image is still `/` and the `COPY --from=fixtures … /tests/fixtures/` target still resolves:

```dockerfile
WORKDIR /runtime/web
```

Change the final-stage `COPY --from=build /web/apps/console/dist` to `/runtime/web/apps/console/dist` to match.

- [ ] **Step 8: Fix the image build script**

`packaging/images/build.sh` — the web context and the stack path:

```bash
stack_agent="$(sed -n 's/.*omelet-agent:\([^}]*\)}.*/\1/p' "$repo/runtime/stack.yml")"
stack_web="$(sed -n 's/.*omelet-web:\([^}]*\)}.*/\1/p' "$repo/runtime/stack.yml")"
```

and

```bash
[[ "$only" == agent ]] || build omelet-web "$repo/runtime/web" --build-context "fixtures=$repo/tests/fixtures"
```

Leave `"$repo/agent"` and the two `agent/` sed paths — Task 2 moves them.

- [ ] **Step 9: Fix the remaining path references**

`tests/host/test_no_guest_assets.py:17` — the message says guest assets "belong in engine/":

```python
    assert not strays, f"guest assets under host/provision belong in runtime/: {strays}"
```

`.vscode/launch.json` — `"localRoot": "${workspaceFolder}/agent"` stays for now (Task 2), but any `engine/` path becomes `runtime/`.

- [ ] **Step 10: Run both suites**

Run: `TMPDIR=$(mktemp -d) python3 -m pytest -q 2>&1 | tail -3`
Expected: `738 passed`

Run: `cd runtime/web && npm test && npm run typecheck && cd ../..`
Expected: green, same test count as Task 0 Step 3.

- [ ] **Step 11: Prove the web image still builds**

Run: `packaging/images/build.sh --only web`
Expected: the build reaches and passes `npm test` inside the image — this is what proves Step 7's depth change is right in both places. A failure here reading `/tests/fixtures/slugify-cases.json` means the WORKDIR and the `../` count disagree.

- [ ] **Step 12: Commit**

```bash
git add -A
git commit -m "Move web/ and engine/'s non-skill files under runtime/

Pure relocation: no behaviour changes, no renames. The Python package
stays put so that a failure here cannot be an import problem.

The one subtle change is runtime/web/Dockerfile's WORKDIR. slugify.test.ts
reaches the shared fixture by relative depth, and that depth has to agree
between the checkout and the image; moving web/ one level deeper means
both the ../ count and the image's WORKDIR move together.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: Rename the Python package to `omelet_api`

Pure Python. Touches no guest path, so a failure here cannot reach the VM.

**Files:**
- Move: `agent/` → `runtime/omelet_api/`, then `runtime/omelet_api/api/` → `runtime/omelet_api/routes/`
- Move: `tests/agent/` → `tests/runtime/api/`
- Modify: 66 import lines across 29 files
- Modify: `pyproject.toml` (pytest `pythonpath`), `runtime/omelet_api/pyproject.toml`, `runtime/omelet_api/Dockerfile`, `runtime/omelet_api/Dockerfile.debug`, `packaging/images/build.sh`, `.vscode/launch.json`
- Modify: `tests/test_no_platform_leak.py`, `tests/host/test_no_agent_import.py` → renamed, `tests/runtime/api/test_no_host_import.py`, `tests/host/test_frozen_bundle.py`

**Interfaces:**
- Consumes: `runtime/` from Task 1.
- Produces: the importable package `omelet_api`, with `omelet_api.core`, `omelet_api.routes`, and `omelet_api.__version__`. Tasks 3–6 import these names.

- [ ] **Step 1: Move the package**

```bash
git mv agent runtime/omelet_api
git mv runtime/omelet_api/api runtime/omelet_api/routes
mkdir -p tests/runtime/api
git mv tests/agent/* tests/runtime/api/
rmdir tests/agent
```

- [ ] **Step 2: Put `runtime/` on the test path**

In the root `pyproject.toml`, under `[tool.pytest.ini_options]`, add:

```toml
pythonpath = ["runtime"]
```

`tests/` is a package (`tests/__init__.py` exists), so pytest already puts the repository root on `sys.path` and `host` keeps resolving. `pythonpath` is additive, so this adds `omelet_api` without removing that.

- [ ] **Step 3: Rewrite the imports**

```bash
grep -rl 'agent\.core\|agent\.api\|from agent import\|import agent$' --include=*.py . \
  | grep -v node_modules \
  | xargs sed -i \
      -e 's/\bagent\.api\b/omelet_api.routes/g' \
      -e 's/\bagent\.core\b/omelet_api.core/g' \
      -e 's/\bfrom agent import\b/from omelet_api import/g' \
      -e 's/^import agent$/import omelet_api/'
```

Then read every changed line — the pattern `agent.api` also appears in prose. Verify:

Run: `git diff --stat && grep -rn 'omelet_api' --include=*.py . | grep -v node_modules | wc -l`
Expected: 29 files changed, ~66 lines carrying `omelet_api`.

- [ ] **Step 4: Update the package's own build config**

`runtime/omelet_api/pyproject.toml`:

```toml
[project]
name = "omelet-api"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = ["fastapi>=0.110", "uvicorn[standard]>=0.30", "pyyaml>=6"]

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools]
package-dir = {"omelet_api" = "."}
packages = ["omelet_api", "omelet_api.core", "omelet_api.routes"]
```

In `runtime/omelet_api/Dockerfile` and `Dockerfile.debug`, change every `agent` path and the uvicorn module target to `omelet_api` (`agent.api` → `omelet_api.routes`). The `COPY api /app/api` layer must become `COPY routes /app/routes` to match the directory rename — miss it and the image builds but the package installs incomplete, which only shows up at container start. Prove the image is whole by importing `omelet_api`, `omelet_api.core`, `omelet_api.routes` and `omelet_api.__version__` inside it, not by a successful build. In `packaging/images/build.sh`, change `"$repo/agent"` to `"$repo/runtime/omelet_api"` and the two `agent/__init__.py` / `agent/Dockerfile` sed paths likewise.

- [ ] **Step 5: Re-anchor the platform-leak test**

`tests/test_no_platform_leak.py` — `_hits` takes a path fragment relative to `ROOT`:

```python
def test_the_agent_never_asks_what_platform_it_is_on():
    scanned, offenders = _hits("runtime/omelet_api")
```

Rename the test to `test_the_api_never_asks_what_platform_it_is_on` and update its message to say "the API" rather than "the agent". `EXEMPT` stays `ROOT / "host" / "providers"`.

- [ ] **Step 6: Prove the platform-leak test is not vacuous**

This is the single most important verification in the plan. The test asserts `scanned` is non-empty, but confirm the assertion actually fires:

```bash
echo "import sys; print(sys.platform)" > runtime/omelet_api/core/_leak_probe.py
TMPDIR=$(mktemp -d) python3 -m pytest tests/test_no_platform_leak.py -q
```

Expected: FAIL, naming `runtime/omelet_api/core/_leak_probe.py`. If it PASSES, the path fragment is wrong and the invariant is silently retired.

```bash
rm runtime/omelet_api/core/_leak_probe.py
```

- [ ] **Step 7: Re-anchor both import-boundary tests**

`tests/host/test_no_agent_import.py` → `git mv` to `tests/host/test_no_api_import.py`, and change the module check:

```python
def test_host_never_imports_from_the_api():
    offenders = []
    scanned = sorted(HOST.rglob("*.py"))
    assert scanned, f"scanned nothing under {HOST}"
    for py in scanned:
        for lineno, module in _imported_modules(ast.parse(py.read_text())):
            if module == "omelet_api" or module.startswith("omelet_api."):
                offenders.append(f"{py}:{lineno} imports {module}")
    assert not offenders, f"host/ imported API code: {offenders}"
```

`HOST` stays `Path(__file__).resolve().parents[2] / "host"` — the file did not change depth.

`tests/runtime/api/test_no_host_import.py` — the anchor moves, and so does the name it is read by inside the test body (the module currently calls it `AGENT` in both places):

```python
API = Path(__file__).resolve().parents[3] / "runtime" / "omelet_api"


def test_the_api_never_imports_from_the_host():
    offenders = []
    scanned = sorted(API.rglob("*.py"))
    assert scanned, f"scanned nothing under {API}"
    ...
```

`tests/runtime/api/test_no_host_import.py` sits three directories below the root, so the root is `parents[3]` — `parents[0]` is its own directory, not its parent. Do not trust that arithmetic; run it:

Run: `python3 -c "from pathlib import Path; p = Path('tests/runtime/api/test_no_host_import.py').resolve().parents[3] / 'runtime' / 'omelet_api'; print(p, p.is_dir())"`
Expected: an absolute path ending `runtime/omelet_api` and `True`.

**The same off-by-one hides in every other anchored path under `tests/runtime/api/`**, which gained a directory level in this task's move. `tests/runtime/api/test_dockerfile.py` and `tests/runtime/api/test_slug_cases.py` both anchor this way and are not otherwise listed in this task. Re-derive each one with the command above rather than adjusting it by eye.

- [ ] **Step 8: Prove both import-boundary tests are not vacuous**

```bash
echo "import omelet_api.core" >> host/core/status.py
TMPDIR=$(mktemp -d) python3 -m pytest tests/host/test_no_api_import.py -q
```

Expected: FAIL naming `host/core/status.py`. Then `git checkout host/core/status.py`.

```bash
echo "import host.core.constants" >> runtime/omelet_api/core/config.py
TMPDIR=$(mktemp -d) python3 -m pytest tests/runtime/api/test_no_host_import.py -q
```

Expected: FAIL naming `config.py`. Then `git checkout runtime/omelet_api/core/config.py`.

- [ ] **Step 9: Fix the frozen-bundle test**

`tests/host/test_frozen_bundle.py:41-42` greps PyInstaller spec sources for `"agent/"` and `"engine/"`. Both directories are gone, so the test would pass while checking for nothing:

```python
    bundled = [(src, dest) for src, dest in spec_datas(spec)
               if "runtime/" in src or dest.startswith("runtime")]
```

Update the failure message to say the VM pulls the API image and fetches the runtime itself. Then prove it fires: add `("../../runtime/stack.yml", "runtime")` to the `datas` list in `packaging/windows/omelet.spec`, run the test, confirm FAIL, and revert.

- [ ] **Step 10: Update the PyInstaller spec comments**

Neither spec references `agent/` or `engine/` as a *path* — only `host/provision/nginx-hello/docker-compose.yml`, which does not move. Only the comments in `packaging/windows/omelet.spec:26-27` and `packaging/macos/omelet.spec:17-18` need updating, to say nothing from `runtime/` is bundled.

- [ ] **Step 11: Fix the debug launch config**

`.vscode/launch.json` — `"module": "agent.api"` becomes `"omelet_api.routes"`, and `"localRoot": "${workspaceFolder}/agent"` becomes `"${workspaceFolder}/runtime/omelet_api"`. The `remoteRoot` site-packages path becomes `.../site-packages/omelet_api`.

- [ ] **Step 12: Run the suite**

Run: `TMPDIR=$(mktemp -d) python3 -m pytest -q 2>&1 | tail -3`
Expected: `738 passed`

- [ ] **Step 13: Prove the API image still builds**

Run: `packaging/images/build.sh --only agent`
Expected: success. This proves Step 4's Dockerfile and build-context edits.

- [ ] **Step 14: Commit**

```bash
git add -A
git commit -m "Rename the agent package to omelet_api

At test time a package's name is its directory name, so runtime/api/
would claim a top-level 'api' on sys.path. omelet_api is unambiguous and
matches the dist name the image publishes. agent/api/ becomes
omelet_api/routes/ rather than omelet_api/api/, which would stutter.

All three boundary tests were re-anchored and each was proved to still
fire by introducing a deliberate violation: after a move they can pass
while scanning nothing, which would retire the invariants silently.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: Rename the host contract

The only task the live-VM run in Task 7 has to judge. Everything here is a name the host or the guest filesystem can observe.

**Files:**
- Modify: `host/core/constants.py:34-41`, `host/core/bootstrap.py:50-80`, `host/core/status.py`, `host/core/install.py`, `host/desktop/view.py`
- Modify: `runtime/install/get.sh`, `runtime/install/install.sh`, `runtime/stack.yml`, `runtime/stack.debug.yml`, `runtime/omelet_api/Dockerfile`, `packaging/images/build.sh`
- Modify: `tests/host/test_bootstrap.py`, `tests/host/test_status_probe.py`, `tests/runtime/test_get_sh.py`, `tests/runtime/test_install_shell.py`, `tests/runtime/test_stack_yml.py`, `tests/test_constants_agree.py`

**Interfaces:**
- Consumes: `omelet_api` from Task 2.
- Produces: `constants.RUNTIME_URL`, `constants.RUNTIME_MARKER`, `constants.GUEST_TOKEN` (value changed, name kept), and the guest paths `/opt/omelet/runtime/`, `/opt/omelet/runtime.version`, `/opt/omelet/api.token`.

- [ ] **Step 1: Write the failing test for the new constants**

In `tests/test_constants_agree.py`, the module-level import changes first — Task 2 renamed the package but left the local alias:

```python
from omelet_api.core import constants as api_constants
from host.core import constants as host_constants
```

Rename every use of `agent_constants` in the file to `api_constants`. Then replace `test_the_engine_entrypoint_constants_live_only_on_the_host` with:

```python
def test_the_runtime_entrypoint_constants_live_only_on_the_host():
    # The API has no use for either, and must not become a second source of
    # truth for where the runtime comes from.
    for name in ("RUNTIME_URL", "RUNTIME_MARKER"):
        assert not hasattr(api_constants, name), f"{name} must not live in omelet_api/core/constants.py"
        assert hasattr(host_constants, name), f"{name} must live in host/core/constants.py"


def test_no_host_contract_name_still_says_engine_or_agent():
    # The window for these renames closes with the first shipped host binary,
    # so the test exists to fail loudly if one is reintroduced.
    stale = [name for name in vars(host_constants)
             if name.isupper() and ("ENGINE" in name or "AGENT" in name)]
    assert not stale, f"host constants still carrying the old vocabulary: {stale}"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `TMPDIR=$(mktemp -d) python3 -m pytest tests/test_constants_agree.py -q`
Expected: FAIL — `RUNTIME_URL must live in host/core/constants.py`, and the stale-name test naming `ENGINE_URL`, `ENGINE_MARKER`.

- [ ] **Step 3: Rename the host constants**

`host/core/constants.py`:

```python
# The host knows only where the runtime's entrypoint lives and which file says
# it finished; what gets installed, and which version, is decided in the VM.
RUNTIME_URL = ("https://raw.githubusercontent.com/omelet-app/"
               "omelet/main/runtime/install/get.sh")
RUNTIME_MARKER = f"{GUEST_ROOT}/runtime.version"

# Generated in the guest by the runtime installer, never pushed from the host.
# The host reads it fresh per client via provider.exec(root=True) rather than
# caching a copy -- see host/client.py.
GUEST_TOKEN = f"{GUEST_ROOT}/api.token"
```

- [ ] **Step 4: Rename the bootstrap's environment variables**

`host/core/bootstrap.py` — `ENGINE_MARKER` → `RUNTIME_MARKER`, `ENGINE_URL` → `RUNTIME_URL`, and the three env var names:

```python
    source = _shell_safe(
        source or os.environ.get("OMELET_RUNTIME_URL") or constants.RUNTIME_URL,
        "OMELET_RUNTIME_URL")

    ref = os.environ.get("OMELET_RUNTIME_REF")
    if ref:
        assignments.append(f"OMELET_RUNTIME_REF={_shell_safe(ref, 'OMELET_RUNTIME_REF')}")
    if repair:
        assignments.append("OMELET_RUNTIME_REPAIR=1")
```

Then sweep the rest of `host/` for the old names:

Run: `grep -rn "ENGINE_\|OMELET_ENGINE" host/`
Expected: no output.

- [ ] **Step 5: Rename inside `get.sh`**

`runtime/install/get.sh` — the header comment, the three env vars, the two paths, the tag glob, and the tarball wildcard. The wildcard must now match the repository's `runtime/` directory while the script itself lives one level deeper inside it:

```bash
REPO="${OMELET_RUNTIME_REPO:-https://github.com/omelet-app/omelet}"
MARKER=/opt/omelet/runtime.version
RUNTIME_DIR=/opt/omelet/runtime
```

In `resolve_ref`, `OMELET_RUNTIME_REF`, `OMELET_RUNTIME_REPAIR`, and both tag patterns:

```bash
  if ! tags="$(git ls-remote --tags --refs "$repo" 'runtime-v*')"; then
    echo "could not reach $repo to find the latest Omelet runtime" >&2
    return 1
  fi
  latest="$(sed -n 's#.*refs/tags/##p' <<<"$tags" | grep -E '^runtime-v[0-9]+\.[0-9]+\.[0-9]+$' | sort -V | tail -n 1)" || true
```

In `main`, the extraction and the hand-off:

```bash
  if ! tar -xzf "$tmp/runtime.tar.gz" -C "$tmp" --strip-components=1 --wildcards '*/runtime/' 2>/dev/null; then
    echo "$ref of $REPO is not a readable archive or has no runtime/ directory" >&2
    exit 1
  fi
  if [[ ! -f "$tmp/runtime/install/install.sh" ]]; then
    echo "$ref of $REPO has no runtime/install/install.sh" >&2
    exit 1
  fi
  mkdir -p /opt/omelet
  rm -rf "$RUNTIME_DIR"
  mv "$tmp/runtime" "$RUNTIME_DIR"
  chmod 755 "$RUNTIME_DIR"

  local args=("$ref")
  if [[ "${OMELET_RUNTIME_REPAIR:-}" == 1 ]]; then
    args+=(--repair)
  fi
  bash "$RUNTIME_DIR/install/install.sh" "${args[@]}"
```

- [ ] **Step 6: Fix `install.sh`'s self-location**

`runtime/install/install.sh:6` currently resolves to the directory holding `stack.yml`, `cli/` and `instructions/`. It is now one level too deep:

```bash
INSTALL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNTIME_DIR="$(dirname "$INSTALL_DIR")"
```

Then audit every `$ENGINE_DIR/…` reference and decide which of the two it meant:

- `$ENGINE_DIR/stack.yml` → `$RUNTIME_DIR/stack.yml`
- `$ENGINE_DIR/cli/omelet.py` → `$RUNTIME_DIR/cli/omelet.py`
- `$ENGINE_DIR/instructions/omelet.md` → `$RUNTIME_DIR/instructions/omelet.md`
- `$ENGINE_DIR/lib/login-users.sh` → `$INSTALL_DIR/lib/login-users.sh`
- `$ENGINE_DIR/lib/install-agents.sh` → `$INSTALL_DIR/lib/install-agents.sh` (the argument it passes stays the *runtime* dir, because the script reads `$SRC/instructions/omelet.md`)
- `$ENGINE_DIR/skills` → left alone; Task 4 replaces it

Also `rm -f /opt/omelet/engine.version` → `runtime.version`, the two `/opt/omelet/agent.token` writes and the `chgrp`/`chmod` on it → `api.token`, `echo "$REF" > /opt/omelet/engine.version` → `runtime.version`, and the `--force-recreate agent` → `--force-recreate api`.

- [ ] **Step 7: Rename inside the stack**

`runtime/stack.yml` — the service key, the image variable and the image name:

```yaml
  api:
    image: ${OMELET_API_IMAGE:-ghcr.io/omelet-app/omelet-api:0.2.0}
```

The Traefik router labels already read `omelet-api` and do not change. Update any `depends_on: [agent]` to `[api]`, and the token mount path to `api.token`. Apply the same changes to `runtime/stack.debug.yml`.

- [ ] **Step 8: Rename the image build arg**

`runtime/omelet_api/Dockerfile` — `ARG AGENT_VERSION=` becomes `ARG SERVICE_VERSION=`. **It must not become `API_VERSION`**: `omelet_api/core/constants.py` already uses that name for the wire protocol number, which is a different thing on a different release cadence.

`packaging/images/build.sh` — the variable, the sed pattern, the build arg and the image name:

```bash
dockerfile_version="$(sed -n 's/^ARG SERVICE_VERSION=//p' "$repo/runtime/omelet_api/Dockerfile")"
stack_api="$(sed -n 's/.*omelet-api:\([^}]*\)}.*/\1/p' "$repo/runtime/stack.yml")"
```

```bash
[[ "$only" == web ]] || build omelet-api "$repo/runtime/omelet_api" --build-arg "SERVICE_VERSION=$version"
```

Change `--only agent|api` in the usage string and the two `[[ "$only" == … ]]` guards to `api`.

- [ ] **Step 8b: Rename the remaining contract-tier "agent" names**

Found during the pre-flight scan; these are the same tier as the rows above and freeze on the same deadline.

`AGENT_PORT` → `API_PORT`, declared three times and held equal by `tests/test_constants_agree.py`: `host/core/constants.py:19`, `runtime/omelet_api/core/constants.py:2`, `runtime/cli/omelet.py:26`. Its users: `host/client.py:28`, `host/desktop/view.py:150,153`, `runtime/omelet_api/core/config.py:26,53`, `tests/host/test_lima.py:87`, `tests/host/desktop/test_view_ports.py:37`, `tests/host/desktop/test_api_ports.py:73`, `runtime/cli/omelet.py:177`.

`OMELET_AGENT_PORT` → `OMELET_API_PORT` in `runtime/stack.yml:55,57,81`, `runtime/omelet_api/core/config.py:53`, `tests/agent/test_config.py:15`, `tests/runtime/test_stack_yml.py:28,37,40-42`.

`OMELET_AGENT_TOKEN` → `OMELET_API_TOKEN` in `runtime/omelet_api/core/config.py:62` and `.vscode/launch.json:55` (whose `.debug/agent.token` value also becomes `.debug/api.token`).

The FastAPI service identity, which `grep` in Step 12 will otherwise catch: `runtime/omelet_api/routes/app.py:159` (`title="omelet-agent"` → `"omelet-api"`), `runtime/omelet_api/routes/__main__.py:17` (the `omelet-agent will not start` message), and the fixture string at `tests/runtime/api/test_files.py:424` (`ghcr.io/x/omelet-agent:9` → `omelet-api:9`).

Leave `AGENT_URL` in `host/client.py:28` — it is a module-local name, not a contract, and Task 5 owns it.

- [ ] **Step 9: Update the tests that assert on these strings**

`tests/test_constants_agree.py` — `OMELET_AGENT_IMAGE` → `OMELET_API_IMAGE`, `omelet-agent` → `omelet-api`, `ARG AGENT_VERSION` → `ARG SERVICE_VERSION`, and `from agent import __version__` → `from omelet_api import __version__`.

`tests/runtime/test_get_sh.py` (33 references) — every `engine` literal becomes `runtime`, including the tag fixtures the fake `git` returns: `engine-v0.1.0` → `runtime-v0.1.0`.

`tests/runtime/test_install_shell.py`, `tests/runtime/test_stack_yml.py`, `tests/host/test_bootstrap.py`, `tests/host/test_status_probe.py` — same sweep for the env vars, the marker path, the token path and the compose service name.

- [ ] **Step 10: Run the suite**

Run: `TMPDIR=$(mktemp -d) python3 -m pytest -q 2>&1 | tail -3`
Expected: `738 passed`, including the two new tests from Step 1.

- [ ] **Step 11: Syntax-check the shell**

Run: `bash -n runtime/install/get.sh && bash -n runtime/install/install.sh && bash -n runtime/install/lib/*.sh && echo OK`
Expected: `OK`

- [ ] **Step 12: Confirm no old vocabulary survives in the contract surface**

Run: `grep -rn "OMELET_ENGINE\|engine.version\|agent\.token\|omelet-agent\|OMELET_AGENT_IMAGE\|AGENT_VERSION" host/ runtime/ packaging/ tests/`
Expected: no output.

- [ ] **Step 13: Commit**

```bash
git add -A
git commit -m "Rename the host contract: engine -> runtime, agent -> api

Every name here is one the host or the guest filesystem can observe, and
every one freezes with the first shipped host binary. Nothing has
shipped, so this is a rename rather than a commitment to support two
vocabularies forever.

install.sh now lives one level below the files it installs, so it
resolves RUNTIME_DIR from INSTALL_DIR explicitly rather than assuming
its own directory holds stack.yml.

ARG AGENT_VERSION becomes SERVICE_VERSION, not API_VERSION: the latter
is already the wire protocol number, on its own release cadence.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: Extract the skills to their own repository

**Files:**
- Create: `README.md` in the `omelet-skills` working copy
- Delete: `engine/` (its last contents), `tests/runtime/test_skills.py`
- Modify: `runtime/install/install.sh:223`, `tests/runtime/test_install_shell.py`

**Interfaces:**
- Consumes: `runtime/install/install.sh` from Task 3.
- Produces: `SKILLS_SOURCE`, defaulting to `omelet-app/omelet-skills`, overridable by `OMELET_SKILLS_SOURCE`.

- [ ] **Step 1: Populate the skills repository**

```bash
POC=/home/ihor/projects/local-environment-for-non-tech/poc
cd /tmp && git clone https://github.com/omelet-app/omelet-skills && cd omelet-skills
cp -r "$POC"/engine/skills/* .
mkdir -p tests && cp "$POC"/tests/runtime/test_skills.py tests/
```

The five skill folders go at the repository root, because `skills add <owner/repo>` scans the repository for `SKILL.md` files.

`test_skills.py` anchors its scan with `Path(__file__).resolve().parents[N]` pointing at `engine/skills`. In the new repository the skills are one level up from `tests/`, so the anchor becomes:

```python
SKILLS = Path(__file__).resolve().parents[1]
```

Verify before committing: `cd /tmp/omelet-skills && python3 -m pytest tests/ -q` must pass and must report the same number of tests it did here.

- [ ] **Step 2: Write the README**

`README.md` in `omelet-skills`. Keep it to what a stranger needs: what these are, the one install line, and one sentence per skill.

```markdown
# Omelet Skills

Skills for coding agents — Claude Code, Codex, Cursor — that turn a plain-words
idea into a running project: an interview, a stack choice, a plan, and the rules
the agent follows while building.

They are written for an [Omelet](https://github.com/omelet-app/omelet)
box, where the `omelet` command runs projects for you, but nothing here needs one
to install.

## Install

    npx skills add omelet-app/omelet-skills -s '*' -g -a claude-code codex

## The skills

- **omelet-setup** — the entry point; runs the others in order.
- **omelet-brainstorm** — a plain-words interview, written up as `docs/brief.md`.
- **omelet-stack** — chooses adopt, assemble or build, and records `docs/stack.md`.
- **omelet-rules** — writes the project's `AGENTS.md`.
- **omelet-plan** — writes and works through `docs/plans/<date>-<slug>.md`.
```

- [ ] **Step 3: Push, and confirm the repository is public**

```bash
git add -A && git commit -m "Omelet's skills for coding agents" && git push
gh repo view omelet-app/omelet-skills --json visibility
```

Expected: `{"visibility":"PUBLIC"}`. A private repository fails every install, exactly like a private ghcr package.

- [ ] **Step 4: Write the failing test for the remote source**

Back in this repository, in `tests/runtime/test_install_shell.py`:

```python
def test_the_skills_install_from_their_own_repository():
    # Shipping them in the tarball is what kept the skills entangled with the
    # runtime: `add` was doing the filing while the tarball did the
    # distributing. The argument must be a remote source, not a local path.
    script = (ROOT / "runtime" / "install" / "install.sh").read_text()
    assert "omelet-app/omelet-skills" in script
    assert "$RUNTIME_DIR/skills" not in script
    assert "$INSTALL_DIR/skills" not in script


def test_the_skills_source_is_overridable():
    # Pinning a ref must be a value change, not a code change.
    script = (ROOT / "runtime" / "install" / "install.sh").read_text()
    assert "${OMELET_SKILLS_SOURCE:-" in script
```

- [ ] **Step 5: Run it to verify it fails**

Run: `TMPDIR=$(mktemp -d) python3 -m pytest tests/runtime/test_install_shell.py -q`
Expected: FAIL — the script still names `$ENGINE_DIR/skills`.

- [ ] **Step 6: Repoint the installer**

`runtime/install/install.sh` — beside `SKILLS_CLI` near the top:

```bash
SKILLS_CLI=skills@1.5.26
# Unpinned on purpose: pinning a tag here is a value change, not a code change.
# Until it is pinned, a box's skills are not identifiable from runtime.version.
SKILLS_SOURCE="${OMELET_SKILLS_SOURCE:-omelet-app/omelet-skills}"
```

At line 223:

```bash
  if ! runuser -u "$name" -- env HOME="$home" DISABLE_TELEMETRY=1 npx -y "$SKILLS_CLI" add "$SKILLS_SOURCE" -s '*' -g -a claude-code codex -y </dev/null; then
```

And the failure message on the next line now has a second possible cause:

```bash
    echo "could not install Omelet's skills for $name: the npm registry or GitHub may be unreachable" >&2
```

- [ ] **Step 7: Remove the skills from this repository**

```bash
git rm -r engine/skills
git rm tests/runtime/test_skills.py
rmdir engine 2>/dev/null || true
```

- [ ] **Step 8: Run the suite**

Run: `TMPDIR=$(mktemp -d) python3 -m pytest -q 2>&1 | tail -3`
Expected: passing, with a lower total than 738 — `test_skills.py`'s tests now live in the skills repository. Record the new number.

- [ ] **Step 9: Confirm `engine/` is gone**

Run: `ls engine 2>&1; grep -rn "engine/" --include=*.py --include=*.sh --include=*.yml . | grep -v node_modules | grep -v docs/`
Expected: `ls: cannot access 'engine': No such file or directory`, and no code references.

- [ ] **Step 10: Commit**

```bash
git add -A
git commit -m "Install the skills from omelet-skills instead of the tarball

install.sh passed a local path to \`skills add\`, so the skills were
distributed by the tarball and merely filed by the CLI -- which is what
kept them entangled with the runtime, and what no directory move would
have fixed. The CLI takes owner/repo, so the whole change is the
argument.

Unpinned for now, behind OMELET_SKILLS_SOURCE, so pinning later is a
value change. The cost is that a box's skills are not identifiable from
its runtime.version until then.

engine/ is now empty and gone.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: Sweep the internal vocabulary

**Read every occurrence.** 224 of the 812 lines containing "agent" mean a *coding* agent and must keep the word.

**Files:**
- Modify: `host/client.py` (32 references), `host/core/install.py` (29), `host/cli.py` (12), `host/desktop/`, `runtime/omelet_api/`, `runtime/cli/omelet.py` (41), `runtime/web/apps/console/src/` (~102), and the tests that name them

**Interfaces:**
- Consumes: everything from Tasks 1–4.
- Produces: `ApiClient`, `ApiError`, `ApiUnavailableError` in `host/client.py`, replacing `AgentClient`, `AgentError`, `AgentUnavailableError`. `JobFailedError` keeps its name.

- [ ] **Step 1: Rename the client classes**

```bash
grep -rl 'AgentClient\|AgentError\|AgentUnavailableError' --include=*.py . \
  | grep -v node_modules \
  | xargs sed -i \
      -e 's/\bAgentUnavailableError\b/ApiUnavailableError/g' \
      -e 's/\bAgentClient\b/ApiClient/g' \
      -e 's/\bAgentError\b/ApiError/g'
```

- [ ] **Step 2: Run the suite**

Run: `TMPDIR=$(mktemp -d) python3 -m pytest -q 2>&1 | tail -3`
Expected: the Task 4 number, passing. A class rename that compiles and passes is done.

- [ ] **Step 3: List what remains, and triage it**

Run: `grep -rni "agent" --include=*.py --include=*.sh --include=*.ts --include=*.tsx --include=*.yml . | grep -v node_modules > /tmp/agent-refs.txt; wc -l /tmp/agent-refs.txt`

Split the list in two. **Keep the word** wherever it means a coding agent:

- `install-agents.sh` and every reference to it
- `-a claude-code codex`, `.codex/AGENTS.md`, `/etc/claude-code/CLAUDE.md`
- every comment or docstring reading "the coding agent", "a coding agent's shell", "coding agents"

**Change the word** wherever it means this project's service: comments saying "the agent" where "the API" is meant, test names like `test_the_stack_deploys_the_image_version_the_agent_reports`, variable names like `agent_timeout`, the `agent` key in fixtures describing the service.

- [ ] **Step 4: Apply the triage, one file at a time**

Work through `/tmp/agent-refs.txt` by file. Do not batch-replace: the two meanings appear in the same files and sometimes in adjacent lines (`runtime/omelet_api/core/config.py:41` says "the coding agent" and keeps it, while the module's other references to "the agent" change).

- [ ] **Step 5: Run both suites**

Run: `TMPDIR=$(mktemp -d) python3 -m pytest -q 2>&1 | tail -3`
Expected: the Task 4 number, passing.

Run: `cd runtime/web && npm test && npm run typecheck && cd ../..`
Expected: green.

- [ ] **Step 6: Confirm every surviving "agent" is deliberate**

Run: `grep -rni "agent" --include=*.py --include=*.sh --include=*.ts --include=*.yml . | grep -v node_modules | grep -viE "claude-code|codex|cursor|coding agent|agents\.md|install-agents|AGENTS\.md"`

Read every remaining line. Each one must be a considered keep, not a miss.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "Say API, not agent, where this project's service is meant

AgentClient/AgentError/AgentUnavailableError become Api*, and the
comments, test names and fixtures that described the service follow.

Reviewed rather than sed'd: 224 of the 812 lines containing 'agent'
mean a coding agent -- Claude Code, Codex, Cursor -- and keep the word.
A blanket replace would have corrupted exactly the usage that was
already correct.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: Update the documentation

**Files:**
- Modify: `CLAUDE.md` (35 "engine" references plus the layout sections), `README.md` (17)

**Interfaces:**
- Consumes: the final tree from Tasks 1–5.

- [ ] **Step 1: Rewrite CLAUDE.md's architecture sections**

The whole "Architecture shape" section describes a host/engine split that no longer exists. Replace "Engine (`engine/` + `agent/`)" with "Runtime (`runtime/`)", and rewrite the release section: `engine-v*` → `runtime-v*`, `omelet-agent` → `omelet-api`, `agent/__init__.py` → `runtime/omelet_api/__init__.py`, `AGENT_VERSION` → `SERVICE_VERSION`.

Update the "Hard invariant" headings: "`host/` never imports `agent/`" becomes "`host/` never imports `omelet_api`", naming `tests/host/test_no_api_import.py` and `tests/runtime/api/test_no_host_import.py`.

Rewrite every path in the "Layers" list. Add to the release gates that `omelet-skills` must be public and populated.

- [ ] **Step 2: Add the new gotcha, and fix a dangling pointer**

CLAUDE.md sends the reader to `docs/architecture.md` section 9 for gotchas, and to `docs/roadmap.md` for the phased plan. **Neither file exists in the tree** — check with `ls docs/`. Do not create them as part of this task; instead record the new gotcha directly in CLAUDE.md and note the two dangling references in the PR body so they can be resolved separately.

The gotcha to record: `slugify.test.ts` reaches `tests/fixtures/slugify-cases.json` by relative depth, and that depth must agree between the checkout and `runtime/web/Dockerfile`'s WORKDIR. Changing one without the other passes `npm test` and fails only inside the image build.

- [ ] **Step 3: Update README.md**

Same rename sweep. The `OMELET_ROOTFS` instructions and the WSL2 run steps are unaffected except for path names.

- [ ] **Step 4: Verify no stale paths remain in the docs**

Run: `grep -n "engine/\|agent/\|omelet-agent\|engine-v" CLAUDE.md README.md`
Expected: only references inside historical spec filenames, and the deliberate "coding agent" prose.

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md README.md
git commit -m "Update the docs for the runtime boundary

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 7: Live-VM verification

The only proof for Task 3. Nothing here runs in the dev shell — **never `wsl.exe` into `omelet-vm` from here.** Hand these commands to the user for PowerShell.

**Files:** none

- [ ] **Step 1: Build and push both images under the new name**

The multi-arch build needs qemu registered, and that registration is lost on every WSL restart:

```bash
docker run --privileged --rm tonistiigi/binfmt --install arm64
packaging/images/build.sh --push
```

- [ ] **Step 2: Make `omelet-api` public**

A newly pushed ghcr package is private, and `install.sh` fails the whole install on any image it cannot pull. Flip `omelet-api`'s visibility by hand at `github.com/orgs/omelet-app/packages/container/omelet-api/settings`.

Confirm from an unauthenticated client before going further:

```bash
docker logout ghcr.io && docker pull ghcr.io/omelet-app/omelet-api:0.2.0
```

- [ ] **Step 3: Push the branch and tag it**

```bash
git push -u origin feature/runtime-boundary
git tag runtime-v0.2.0 && git push origin runtime-v0.2.0
```

- [ ] **Step 4: Hand the user the PowerShell run**

The host fetches `get.sh` from `main` by default, which does not have this branch's changes yet, so the run must override the source:

```powershell
$env:OMELET_RUNTIME_URL = "https://raw.githubusercontent.com/omelet-app/omelet/feature/runtime-boundary/runtime/install/get.sh"
$env:OMELET_RUNTIME_REF = "runtime-v0.2.0"
omelet vm create
omelet verify
```

- [ ] **Step 5: Check the guest layout**

```powershell
wsl.exe -d omelet-vm -u root -- ls -la /opt/omelet/
```

Expected: `runtime/`, `runtime.version`, `api.token`, `projects/`, `stack.yml`, `state.db`. **No** `engine/`, `engine.version` or `agent.token`.

- [ ] **Step 6: Check the skills installed from GitHub**

```powershell
wsl.exe -d omelet-vm -- ls ~/.claude/skills/
```

Expected: the five `omelet-*` folders. An empty result means `skills add` could not reach the repository — check Step 3 of Task 4.

- [ ] **Step 7: Check the containers**

```powershell
wsl.exe -d omelet-vm -u root -- docker compose -f /opt/omelet/stack.yml ps
```

Expected: `traefik`, `api` and `web` running. The service is `api`, not `agent`.

- [ ] **Step 8: Check the repair path**

Repair is the branch of `resolve_ref` that reads the marker, so it exercises the renamed marker specifically:

```powershell
$env:OMELET_RUNTIME_REPAIR = "1"
omelet vm repair
```

Expected: it re-reads `runtime-v0.2.0` from `/opt/omelet/runtime.version` and reinstalls without asking GitHub for tags.

- [ ] **Step 9: Record the result**

Write the outcome into `docs/lima-verification-report.md`'s sibling or a short note in the PR body: which steps passed, and the exact output of Step 5.

---

## Definition of Done

- [ ] `TMPDIR=$(mktemp -d) python3 -m pytest -q` passes at the Task 4 count.
- [ ] `cd runtime/web && npm test && npm run typecheck && npm run build && npm run check-offline` green.
- [ ] `packaging/images/build.sh` (no `--push`) builds both images.
- [ ] `grep -rn "OMELET_ENGINE\|engine.version\|agent\.token\|omelet-agent" host/ runtime/ packaging/ tests/` returns nothing.
- [ ] All three boundary tests were each proved to fail on a deliberate violation.
- [ ] `omelet-skills` is public, populated, and has a README.
- [ ] Task 7's live run passed, with Step 5's output recorded.
- [ ] PR opened against `main`, followed by a code review from a separate agent.
