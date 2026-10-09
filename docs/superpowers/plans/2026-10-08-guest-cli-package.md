# Guest CLI Package Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn `runtime/cli/eggie.py` into the stdlib-only package `runtime/cli/eggie_cli/` and have `install.sh` build the single `/usr/local/bin/eggie` from it with `zipapp`, with no change to what the command does.

**Architecture:** Nine small modules with one dependency direction (`cli → commands | secrets → env → api | project → errors | constants`), a barrel `__init__` that tests load through, and a `zipapp` build step in `install.sh` so the guest still receives one file and no new dependency.

**Tech Stack:** Python 3.12 stdlib (`argparse`, `urllib`, `zipapp`), bash (`install.sh`), pytest.

**Spec:** `docs/superpowers/specs/2026-10-08-guest-cli-package-design.md`

## Global Constraints

- Branch `feature/59-guest-cli-package` (exists, from `main` at `cb687e8`; spec committed as `b078ebd`). Never commit on `main`.
- Tests: `cd /home/ihor/projects/local-environment-for-non-tech/poc && TMPDIR=/home/ihor/tmp python3 -m pytest -q` (system `python3` 3.12.3 has the deps; there is no `.venv`; `/tmp/pytest-of-$USER` is root-owned in this WSL). Baseline: 1300 passed. The full suite is green at the end of every task.
- Behaviour frozen: every command, argument, message, exit code and output line is unchanged. `tests/runtime/cli/*` and `tests/test_constants_agree.py` change only in the loader, the boundary test and one renamed attribute pair (`_RESERVED_*` → `RESERVED_*`). Never rewrite an assertion to fit.
- Stdlib only; the package imports neither `host/` nor `eggie_api`; the shared names stay re-declared (`API_PORT`, `GUEST_ROOT`, `GUEST_PROJECTS`, `GUEST_TOKEN`, `GUEST_STACK`, `COMPOSE_FILE`, `API_UNCONFIGURED`, `VERIFY_PROJECT_ID`).
- Code moves verbatim with its comments and docstrings; no comments restating code; no ticket references in comments; `from __future__ import annotations` first in every module; continuation lines aligned with their opening paren.
- `runtime/cli/` holds nothing but `eggie_cli/` — it is the zipapp root.
- Never run `wsl.exe`, `limactl`, or docker; `install.sh` is never executed, only parsed by tests.

## Review Focus

1. **A command run from a subfolder of a project** (`eggie up` in `~/projects/blog/src`) must still find `blog` — `project_of` and `require_id` move together into `project.py` and are pinned by the existing `tests/runtime/cli/test_paths.py`; no new test.
2. **`eggie secret set NAME=value`** must still refuse before anything is sent — the `"=" in name` check lives in `secrets.py` and `tests/runtime/cli/test_secret.py` pins it; no new test.
3. **The zipapp root on `sys.path`**: a future top-level entry in `runtime/cli/` named like a stdlib module would shadow it inside the executable. Pinned by the new test in Task 1 Step 2.
4. **The built executable must actually start** — a relative import that works under pytest (package on `sys.path`) but not from inside the zip would only fail on the VM. Pinned by Task 2 Step 4's local build-and-run.
5. **`install.sh` must build before it installs, and install at mode 755** — pinned by Task 2 Step 1's text assertion on line order.

---

### Task 1: The package, the loader, the boundary tests

**Files:**
- Create: `runtime/cli/eggie_cli/__init__.py`, `constants.py`, `errors.py`, `api.py`, `project.py`, `env.py`, `commands.py`, `secrets.py`, `cli.py`
- Delete: `runtime/cli/eggie.py`
- Modify: `tests/runtime/cli/loader.py`, `tests/runtime/cli/test_boundaries.py`, `tests/runtime/cli/test_secret.py:158-160`
- Test: `tests/runtime/cli/` (all), `tests/test_constants_agree.py`

**Interfaces:**
- Produces: the package `eggie_cli` whose `__init__` re-exports `API_PORT, GUEST_ROOT, GUEST_PROJECTS, GUEST_TOKEN, GUEST_STACK, COMPOSE_FILE, API_UNCONFIGURED, VERIFY_PROJECT_ID, DOCKER_GROUP, ALT_COMPOSE_FILES, START_STACK, RESTART_API, RESERVED_NAMES, RESERVED_PREFIXES, EggieError, ApiError, JobFailed, ApiClient, read_token, Env, project_id_for, project_of, require_id, prepare_overlay_dir, docker_gid, repo_name, main`; `eggie_cli.cli:run(argv=None, env=None) -> int` (Task 2's zipapp entry).
- `tests.runtime.cli.loader.load() -> module` (the package) and `GUEST_CLI: Path` (the package directory).

- [ ] **Step 1: Rewrite the loader and watch the suite fail**

`tests/runtime/cli/loader.py`:
```python
"""The guest CLI is a stdlib-only package built into one executable for the
VM, never a module of host/; tests put its source root on sys.path the way
the zipapp puts the archive root there."""
import importlib
import sys
from pathlib import Path

CLI_ROOT = Path(__file__).resolve().parents[3] / "runtime" / "cli"
GUEST_CLI = CLI_ROOT / "eggie_cli"


def load():
    if str(CLI_ROOT) not in sys.path:
        sys.path.insert(0, str(CLI_ROOT))
    return importlib.import_module("eggie_cli")
```

Run: `TMPDIR=/home/ihor/tmp python3 -m pytest tests/runtime/cli -q -x`
Expected: FAIL at collection, `ModuleNotFoundError: No module named 'eggie_cli'`.

- [ ] **Step 2: Rewrite `test_boundaries.py`'s stdlib test and add the zipapp-root test**

Replace `test_the_guest_cli_imports_only_the_standard_library` with:
```python
def test_the_guest_cli_imports_only_the_standard_library():
    # It is built into a VM on its own: an import of host/, eggie_api/ or a
    # third-party package works in this checkout and fails only in the guest.
    files = sorted(GUEST_CLI.rglob("*.py"))
    assert len(files) > 1, f"scanned {len(files)} files under {GUEST_CLI}"
    imported = set()
    for py in files:
        for node in ast.walk(ast.parse(py.read_text())):
            if isinstance(node, ast.Import):
                imported |= {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                imported.add((node.module or "").split(".")[0])
    assert imported, f"scanned no imports under {GUEST_CLI}"
    outside = sorted(imported - set(sys.stdlib_module_names) - {"eggie_cli"})
    assert not outside, f"the guest CLI imports non-stdlib modules: {outside}"
```
Add after it:
```python
def test_the_cli_source_root_holds_only_the_package():
    # runtime/cli is the zipapp root, first on sys.path inside the executable:
    # a stray file there ships, and a name like json/ would shadow the stdlib.
    entries = sorted(p.name for p in CLI_ROOT.iterdir() if p.name != "__pycache__")
    assert entries == ["eggie_cli"], entries
    assert "eggie_cli" not in sys.stdlib_module_names
```
and change the import line to `from tests.runtime.cli.loader import CLI_ROOT, GUEST_CLI, load`.

In `tests/runtime/cli/test_secret.py:159-160` change `cli._RESERVED_NAMES` → `cli.RESERVED_NAMES` and `cli._RESERVED_PREFIXES` → `cli.RESERVED_PREFIXES`.

- [ ] **Step 3: Create the package**

Every function, class, constant, comment and docstring below is moved **verbatim** from `runtime/cli/eggie.py` (line numbers refer to it at `cb687e8`); only the import headers are new. The module docstring from lines 2–8 goes on `__init__.py`.

`eggie_cli/constants.py` — lines 27–43 (`API_PORT` … `VERIFY_PROJECT_ID`, with their comments). Rename `_ALT_COMPOSE_FILES` to `ALT_COMPOSE_FILES` (it crosses a module now).
```python
from __future__ import annotations
```

`eggie_cli/errors.py` — lines 46–47 (`EggieError`), 136–145 (`ApiError`, `JobFailed`).

`eggie_cli/api.py` — lines 119–133 (timeouts, `START_STACK`, `RESTART_API`, `_GUIDANCE`), 148–281 (`read_token`, `_api_error`, `ApiClient`, `_default_client` renamed `default_client`).
```python
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .constants import API_PORT, API_UNCONFIGURED, GUEST_STACK, GUEST_TOKEN
from .errors import ApiError, EggieError, JobFailed
```

`eggie_cli/project.py` — lines 50–116 (`project_id_for`, `project_of`, `require_id`, `docker_gid`, `prepare_overlay_dir`), 316–320 (`_alt_compose_file` renamed `alt_compose_file`), 498–500 (`repo_name`).
```python
from __future__ import annotations

import grp
import os
import re
from pathlib import Path

from .constants import ALT_COMPOSE_FILES, DOCKER_GROUP
from .errors import EggieError
```

`eggie_cli/env.py` — lines 284–313 (`_run_git` renamed `run_git`, `Env`, `_project_here` renamed `project_here`, `_require_project` renamed `require_project`).
```python
from __future__ import annotations

import getpass as _getpass
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, TextIO

from .api import ApiClient, default_client
from .constants import GUEST_PROJECTS
from .errors import EggieError
from .project import docker_gid, project_of, require_id
```

`eggie_cli/commands.py` — lines 323–409 (`_start` renamed `start`, `_print_project` renamed `print_project`, `cmd_up`, `cmd_status`, `cmd_logs`, `cmd_down`), 503–545 (`_new_folder` renamed `new_folder`, `cmd_new`, `cmd_clone`).
```python
from __future__ import annotations

import os
from pathlib import Path

from .constants import COMPOSE_FILE, GUEST_PROJECTS, VERIFY_PROJECT_ID
from .env import Env, project_here, require_project
from .errors import ApiError, EggieError, JobFailed
from .project import alt_compose_file, prepare_overlay_dir, project_id_for, repo_name
```

`eggie_cli/secrets.py` — lines 411–495 (`_APPLY_HINT`, `_SECRET_NAME`, `_NO_ARGV_VALUE`, `_BAD_NAME`, `_RESERVED_PREFIXES` renamed `RESERVED_PREFIXES`, `_RESERVED_NAMES` renamed `RESERVED_NAMES` — keep the "Mirrors eggie_api's reserved names" comment —, `_secret_project`, `cmd_secret_set`, `cmd_secret_request`, `cmd_secret_list`, `cmd_secret_rm`).
```python
from __future__ import annotations

import re

from .api import ApiClient
from .env import Env, require_project
from .errors import EggieError
```

`eggie_cli/cli.py` — lines 547–628 (`_secret_usage_error`, `_parser`, `main`, and the `if __name__ == "__main__": sys.exit(main())` guard).
```python
from __future__ import annotations

import argparse
import sys
from typing import NoReturn

from .commands import cmd_clone, cmd_down, cmd_logs, cmd_new, cmd_status, cmd_up
from .env import Env
from .errors import EggieError
from .secrets import cmd_secret_list, cmd_secret_request, cmd_secret_rm, cmd_secret_set
```

`eggie_cli/__init__.py` — the old module docstring (lines 2–8, with "Pushed into the guest by the host and installed as /usr/local/bin/eggie" reworded to "Built into /usr/local/bin/eggie by install.sh") and the barrel:
```python
from __future__ import annotations

from .api import START_STACK, RESTART_API, ApiClient, default_client, read_token
from .constants import (ALT_COMPOSE_FILES, API_PORT, API_UNCONFIGURED, COMPOSE_FILE,
                        DOCKER_GROUP, GUEST_PROJECTS, GUEST_ROOT, GUEST_STACK,
                        GUEST_TOKEN, VERIFY_PROJECT_ID)
from .env import Env
from .errors import ApiError, EggieError, JobFailed
from .main import main
from .project import (docker_gid, prepare_overlay_dir, project_id_for, project_of,
                      repo_name, require_id)
from .secrets import RESERVED_NAMES, RESERVED_PREFIXES

__all__ = [name for name in dir() if not name.startswith("_")]
```

Then `git rm runtime/cli/eggie.py`. Fix every reference the renames touched inside the package (`_alt_compose_file` → `alt_compose_file`, `_start` → `start`, `_print_project` → `print_project`, `_new_folder` → `new_folder`, `_project_here` → `project_here`, `_require_project` → `require_project`, `_default_client` → `default_client`, `_run_git` → `run_git`, `_RESERVED_*` → `RESERVED_*`, `_ALT_COMPOSE_FILES` → `ALT_COMPOSE_FILES`); `grep -rn "_alt_compose\|_start(\|_print_project\|_new_folder\|_project_here\|_require_project\|_default_client\|_run_git\|_RESERVED\|_ALT_COMPOSE" runtime/cli` must print nothing.

- [ ] **Step 4: Run the CLI tests, then the whole suite**

Run: `TMPDIR=/home/ihor/tmp python3 -m pytest tests/runtime/cli tests/test_constants_agree.py -q`
Expected: all pass. A `NameError`/`ImportError` means a header above misses a name the moved body uses — add it to that module's imports, nothing else.
Run: `TMPDIR=/home/ihor/tmp python3 -m pytest -q` → 1301 passed (1300 + the zipapp-root test).

- [ ] **Step 5: Commit**

```bash
git add -A runtime/cli tests
git commit -m "$(cat <<'EOF'
Split the guest CLI into the eggie_cli package

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: `install.sh` builds the executable with zipapp

**Files:**
- Modify: `runtime/install/install.sh:244`
- Test: `tests/runtime/test_install_shell.py` (one new test)

**Interfaces:**
- Consumes: `eggie_cli.cli:run` from Task 1.

- [ ] **Step 1: Write the failing test**

Append to `tests/runtime/test_install_shell.py`:
```python
def test_install_builds_the_guest_cli_from_its_package_before_installing_it():
    # The CLI is a package; the VM gets one stdlib-only executable built from
    # it. The build must precede the install, and the install must set 755 so
    # every login account can run it.
    commands = _commands()
    build = _index_of("python3 -m zipapp")
    assert '"$RUNTIME_DIR/cli"' in commands[build]
    assert "-m \"eggie_cli.cli:run\"" in commands[build]
    assert "-p \"/usr/bin/env python3\"" in commands[build]
    installed = _index_of("/usr/local/bin/eggie")
    assert build < installed
    assert commands[installed].startswith("install -m 755 ")
    assert not any("cli/eggie.py" in l for l in commands)
```

Run: `TMPDIR=/home/ihor/tmp python3 -m pytest tests/runtime/test_install_shell.py -q -k zipapp`
Expected: FAIL, `install.sh has no line containing 'python3 -m zipapp'`.

- [ ] **Step 2: Change step 9 of `install.sh`**

Replace line 244 (`install -m 755 "$RUNTIME_DIR/cli/eggie.py" /usr/local/bin/eggie`) with:
```bash
# One stdlib-only executable built from the package: the stock python3 runs
# the zip directly, so the guest gains no dependency.
cli_build="$(mktemp)"
python3 -m zipapp "$RUNTIME_DIR/cli" -m "eggie_cli.cli:run" -p "/usr/bin/env python3" -o "$cli_build"
install -m 755 "$cli_build" /usr/local/bin/eggie
rm -f "$cli_build"
```

- [ ] **Step 3: Run the install tests**

Run: `TMPDIR=/home/ihor/tmp python3 -m pytest tests/runtime/test_install_shell.py -q`
Expected: all pass (`test_install_is_valid_bash` covers the syntax).

- [ ] **Step 4: Build and run the executable locally (the only place a zip-only import error shows)**

```bash
cd /home/ihor/projects/local-environment-for-non-tech/poc
out=/tmp/claude-1000/-home-ihor-projects-local-environment-for-non-tech-poc/f64b519b-89dd-4fc0-9c32-24abb9266975/scratchpad/eggie
python3 -m zipapp runtime/cli -m "eggie_cli.cli:run" -p "/usr/bin/env python3" -o "$out"
"$out" --help | head -3
"$out" status; echo "exit=$?"
```
Expected: `--help` prints the `usage: eggie <command> ...` block; `status` prints `Eggie is not set up in this VM yet. Run \`eggie setup\` on your computer.` and `exit=1` (no `/opt/eggie/api.token` here). Any `ImportError` means a module imports something the zip cannot resolve — fix the import in the package, re-run Task 1 Step 4, and repeat this step. Record both outputs in the commit message body or the report.

- [ ] **Step 5: Run the whole suite and commit**

Run: `TMPDIR=/home/ihor/tmp python3 -m pytest -q` → 1302 passed.
```bash
git add runtime/install/install.sh tests/runtime/test_install_shell.py
git commit -m "$(cat <<'EOF'
Build /usr/local/bin/eggie from the eggie_cli package with zipapp

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Docs

**Files:**
- Modify: `runtime/CLAUDE.md:18-22`, `runtime/install/CLAUDE.md:52`, any other `cli/eggie.py` mention (`grep -rn "cli/eggie\.py\|eggie\.py" docs/*.md CLAUDE.md runtime host tests --include=*.md --include=*.py --include=*.sh | grep -v docs/superpowers`)

- [ ] **Step 1: `runtime/CLAUDE.md`**

Replace the `cli/eggie.py` bullet with:
```markdown
- `cli/eggie_cli/` — the `eggie` command **inside** the VM, used by coding agents:
  `up`/`new`/`clone`/`status`/`logs`/`down`/`secret` over the API with the guest token.
  `install.sh` builds it into the one file `/usr/local/bin/eggie` with `python3 -m zipapp`, so
  `runtime/cli/` must hold nothing but the package (it is the zip's root, first on `sys.path`).
  **Stdlib only**: it can import neither `host/` nor `eggie_api`, so shared names are
  re-declared and held equal by `tests/test_constants_agree.py`. Modules depend one way:
  `cli → commands | secrets → env → api | project → errors | constants`; tests load the barrel
  through `tests/runtime/cli/loader.py`.
```

- [ ] **Step 2: `runtime/install/CLAUDE.md`**

Where the step list names `/usr/local/bin/eggie`, say it is built with `zipapp` from `cli/eggie_cli/`. Run the grep in Files; every remaining hit outside `docs/superpowers/` is updated.

- [ ] **Step 3: Run the suite and commit**

Run: `TMPDIR=/home/ihor/tmp python3 -m pytest -q` → 1302 passed.
```bash
git add -A
git commit -m "$(cat <<'EOF'
Describe the guest CLI package and its zipapp build

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
EOF
)"
```

Then (controller): push, open the PR to `main` with the Task 2 Step 4 outputs in the body, and run the separate review agent.
