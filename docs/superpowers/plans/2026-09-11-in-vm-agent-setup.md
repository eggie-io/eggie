# In-VM Agent Setup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A coding agent running inside the Eggie VM can turn a repo URL, an archive, a folder, or a plain-words app idea into a routed project with a working local URL, without asking the user a technical question.

**Architecture:** A stdlib-only `eggie` command inside the guest talks to the existing agent API (`127.0.0.1:39099`, bearer token) exactly the way the host client does. Two agent-neutral Markdown files (always-loaded instructions + an `eggie-setup` skill) are pushed with it, and provisioning copies them into each coding agent's discovery locations. Nothing is written into user repositories.

**Tech Stack:** Python 3.12 stdlib (guest CLI), bash (provisioning), pytest + FastAPI `TestClient` (seam tests against the real agent app).

**Spec:** `docs/superpowers/specs/2026-09-11-in-vm-agent-setup-design.md`

## Global Constraints

- The guest CLI is **one file**, `host/provision/guest/eggie.py`, importing **only the standard library** — never `host`, never `agent`.
- Every failure the guest CLI reports is **one plain sentence on stderr, exit code 1**. Agent error bodies are printed **verbatim**; no status code ever reaches the user.
- **Nothing under the user's project is modified** except `.eggie/` (permissions only, §5.3 of the spec).
- Shared values, declared again in the guest CLI: `AGENT_PORT = 39099`, `GUEST_ROOT = "/opt/eggie"`, `GUEST_PROJECTS = "/opt/eggie/projects"`, `GUEST_TOKEN = "/opt/eggie/agent.token"`, `COMPOSE_FILE = "docker-compose.yml"`; slug rule `re.sub(r"[^a-z0-9-]+", "-", name.strip().lower()).strip("-")`.
- **No `sys.platform` / `platform.system()` / `os.name`** anywhere outside `host/providers/` (enforced by `tests/test_no_platform_leak.py`, which also scans the guest CLI).
- **Bump `BOOTSTRAP_VERSION`** (5 → 6) because `bootstrap.sh` changes.
- Each pushed asset must fit one `wsl.exe` command line (Windows caps it at 32,767 characters, base64 included).
- Tests follow the repo's testing rules: behaviour through the public surface, one reason to fail per test, no test that only restates the implementation. Anchor every repo path to `__file__`, never the cwd.
- If `tmp_path` fixtures error with a permission problem, prefix pytest with `TMPDIR=<writable dir>` (a sandbox artifact, see `CLAUDE.md`).
- Comments: only for edge cases, workarounds, non-obvious reasons; two lines beat a paragraph; never mention tickets, specs or docs.
- Commit messages end with:
  ```
  Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01DBSpFPWc1TRWYn4pnjP8px
  ```

## File Structure

| Path | Responsibility |
|---|---|
| `host/provision/guest/eggie.py` (create) | The in-VM `eggie` command: path rules, `.eggie/` permissions, agent API client, commands, `main`. |
| `host/provision/agents/eggie.md` (create) | Always-loaded instructions for every coding agent. |
| `host/provision/agents/skills/eggie-setup/SKILL.md` (create) | The setup procedure, loaded on use. |
| `host/provision/install-agents.sh` (create) | Copies the skill and the Codex block into one home directory. |
| `host/provision/bootstrap.sh` (modify) | New step 8: git, the CLI, system-wide agent files, per-user installer loop. |
| `host/core/bootstrap.py` (modify) | `guest_assets()` gains the four new files. |
| `host/core/constants.py` (modify) | `BOOTSTRAP_VERSION = 6`. |
| `packaging/windows/eggie.spec` (modify) | Bundles the four new files. |
| `tests/guest/__init__.py`, `tests/guest/loader.py` (create) | Load the guest CLI by path, the way the VM runs it. |
| `tests/guest/conftest.py` (create) | `guest` fixture: guest CLI wired to the real agent app in-process. |
| `tests/guest/test_paths.py`, `test_boundaries.py`, `test_transport.py`, `test_seam.py`, `test_new_and_clone.py` (create) | Guest CLI tests. |
| `tests/host/test_install_agents.py` (create) | Runs `install-agents.sh` against a temp home. |
| `tests/test_constants_agree.py`, `tests/host/test_no_dead_modules.py`, `tests/host/test_bootstrap.py`, `tests/host/test_bootstrap_shell.py` (modify) | Boundaries, asset budget, bootstrap drift. |
| `CLAUDE.md` (modify) | Record the guest CLI and its rules for future sessions. |

---

### Task 1: Guest CLI foundation — path rules, `.eggie/` permissions, boundaries

**Files:**
- Create: `host/provision/guest/eggie.py`
- Create: `tests/guest/__init__.py`, `tests/guest/loader.py`, `tests/guest/test_paths.py`, `tests/guest/test_boundaries.py`
- Modify: `tests/test_constants_agree.py`, `tests/host/test_no_dead_modules.py`

**Interfaces:**
- Produces (in `eggie.py`): constants `AGENT_PORT`, `GUEST_ROOT`, `GUEST_PROJECTS`, `GUEST_TOKEN`, `COMPOSE_FILE`, `DOCKER_GROUP`; `class EggieError(Exception)`; `project_id_for(name: str) -> str`; `project_of(directory: Path, root: Path) -> Path | None`; `require_id(folder: Path) -> str`; `prepare_overlay_dir(project: Path, gid: int) -> None`; `docker_gid() -> int`.
- Produces (tests): `tests.guest.loader.load()` returning the loaded module, `tests.guest.loader.GUEST_CLI` (its `Path`).

- [ ] **Step 1: Create the test loader**

`tests/guest/__init__.py` — empty file.

`tests/guest/loader.py`:

```python
"""The guest CLI is a standalone script copied into the VM, never a module of
host/, so tests load it by path the way the VM runs it."""
import importlib.util
import sys
from pathlib import Path

GUEST_CLI = (Path(__file__).resolve().parents[2]
             / "host" / "provision" / "guest" / "eggie.py")
_NAME = "eggie_guest_cli"


def load():
    if _NAME not in sys.modules:
        spec = importlib.util.spec_from_file_location(_NAME, GUEST_CLI)
        module = importlib.util.module_from_spec(spec)
        # Registered before exec: dataclasses resolve their module through
        # sys.modules while the class body runs.
        sys.modules[_NAME] = module
        spec.loader.exec_module(module)
    return sys.modules[_NAME]
```

- [ ] **Step 2: Write the failing path and permission tests**

`tests/guest/test_paths.py`:

```python
import os
import stat

import pytest

from tests.guest.loader import load

cli = load()


@pytest.fixture
def root(tmp_path):
    projects = tmp_path / "opt" / "projects"
    projects.mkdir(parents=True)
    return projects


def test_a_folder_and_its_subfolders_belong_to_the_project(root):
    (root / "blog" / "src" / "pages").mkdir(parents=True)
    expected = (root / "blog").resolve()
    assert cli.project_of(root / "blog", root) == expected
    assert cli.project_of(root / "blog" / "src" / "pages", root) == expected


def test_the_home_symlink_reaches_the_same_project(root, tmp_path):
    (root / "blog").mkdir()
    link = tmp_path / "home" / "projects"
    link.parent.mkdir()
    link.symlink_to(root)
    assert cli.project_of(link / "blog", root) == (root / "blog").resolve()


def test_the_projects_root_itself_and_folders_outside_it_are_no_project(root, tmp_path):
    (tmp_path / "elsewhere").mkdir()
    assert cli.project_of(root, root) is None
    assert cli.project_of(tmp_path / "elsewhere", root) is None


def test_a_folder_whose_name_is_not_an_id_must_be_renamed_first(root):
    folder = root / "My Blog"
    folder.mkdir()
    with pytest.raises(cli.EggieError, match="'my-blog'"):
        cli.require_id(folder)


def test_a_folder_name_with_nothing_usable_is_refused(root):
    folder = root / "???"
    folder.mkdir()
    with pytest.raises(cli.EggieError, match="cannot be a project name"):
        cli.require_id(folder)


def test_the_agent_can_write_its_overlay_after_a_root_session_made_the_files(root):
    project = root / "blog"
    eggie_dir = project / ".eggie"
    eggie_dir.mkdir(parents=True)
    eggie_dir.chmod(0o755)
    overlay = eggie_dir / "overlay.yml"
    overlay.write_text("old")
    overlay.chmod(0o644)
    source = project / "app.py"
    source.write_text("")
    source.chmod(0o644)

    cli.prepare_overlay_dir(project, os.getgid())

    assert stat.S_IMODE(eggie_dir.stat().st_mode) == 0o2775
    assert stat.S_IMODE(overlay.stat().st_mode) == 0o664
    assert stat.S_IMODE(source.stat().st_mode) == 0o644


def test_a_missing_eggie_folder_is_created_writable(root):
    project = root / "blog"
    project.mkdir()
    cli.prepare_overlay_dir(project, os.getgid())
    assert stat.S_IMODE((project / ".eggie").stat().st_mode) == 0o2775
```

- [ ] **Step 3: Write the failing boundary tests**

`tests/guest/test_boundaries.py`:

```python
import ast
import sys

from tests.guest.loader import GUEST_CLI, load


def test_the_guest_cli_imports_only_the_standard_library():
    # It is copied into a VM on its own: an import of host/, agent/ or a
    # third-party package works in this checkout and fails only in the guest.
    imported = set()
    for node in ast.walk(ast.parse(GUEST_CLI.read_text())):
        if isinstance(node, ast.Import):
            imported |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, f"relative import at line {node.lineno}"
            imported.add((node.module or "").split(".")[0])
    assert imported, f"scanned no imports in {GUEST_CLI}"
    outside = sorted(imported - set(sys.stdlib_module_names))
    assert not outside, f"the guest CLI imports non-stdlib modules: {outside}"


def test_the_guest_cli_slugs_project_names_the_way_the_agent_does():
    # The agent derives the project folder from the slugged id; a guest that
    # slugs differently checks one folder and registers another.
    from agent.core.project import _slug
    for name in ("Blog", "my app", "My.Repo", "--x--", "Ünïcode 2", "a__b", ""):
        assert load().project_id_for(name) == _slug(name), name
```

Append to `tests/test_constants_agree.py`:

```python
def test_the_guest_cli_holds_the_same_values_as_the_host_and_the_agent():
    # The guest CLI is copied into the VM on its own and can import neither
    # side, so its copies of the shared names are held equal here.
    from tests.guest.loader import load

    guest = _public(load())
    for side, other in (("agent", _public(agent_constants)),
                        ("host", _public(host_constants))):
        shared = sorted(set(guest) & set(other))
        assert shared, f"the guest CLI shares no names with the {side}"
        diverged = {name: (guest[name], other[name])
                    for name in shared if guest[name] != other[name]}
        assert not diverged, f"guest/{side} constants diverged: {diverged}"
```

- [ ] **Step 4: Run the new tests to verify they fail**

Run: `python3 -m pytest tests/guest tests/test_constants_agree.py -q`
Expected: FAIL — `FileNotFoundError` / `ModuleNotFoundError` loading `host/provision/guest/eggie.py`.

- [ ] **Step 5: Create the guest CLI foundation**

`host/provision/guest/eggie.py`:

```python
#!/usr/bin/env python3
"""`eggie` inside the VM: turns a folder in ~/projects into a routed project.

Pushed into the guest by the host and installed as /usr/local/bin/eggie, then
run by whichever coding agent the user works in. Stdlib only: it can import
neither host/ nor agent/, so the names it shares with them are declared again
here and held equal by tests/test_constants_agree.py.
"""
from __future__ import annotations

import argparse
import grp
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, TextIO

AGENT_PORT = 39099
GUEST_ROOT = "/opt/eggie"
GUEST_PROJECTS = f"{GUEST_ROOT}/projects"
GUEST_TOKEN = f"{GUEST_ROOT}/agent.token"
COMPOSE_FILE = "docker-compose.yml"
# The agent container's only credential shared with this VM.
DOCKER_GROUP = "docker"


class EggieError(Exception):
    """One plain sentence for the user; `main` prints it and exits 1."""


def project_id_for(name: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", name.strip().lower()).strip("-")


def project_of(directory: Path, root: Path) -> Path | None:
    """The project folder holding `directory`, or None outside `root`.

    Resolved first, so ~/projects/x (a symlink) and /opt/eggie/projects/x are
    one folder; a subfolder counts, so `eggie up` from src/ runs the project.
    """
    root = root.resolve()
    path = directory.resolve()
    for candidate in (path, *path.parents):
        if candidate.parent == root:
            return candidate
    return None


def require_id(folder: Path) -> str:
    project_id = project_id_for(folder.name)
    if not project_id:
        raise EggieError(
            f"The folder name '{folder.name}' cannot be a project name. "
            "Rename it using letters, digits and dashes.")
    if project_id != folder.name:
        # The agent derives the folder from the slugged id, so any other name
        # would register a different, empty folder.
        raise EggieError(
            f"Rename the folder '{folder.name}' to '{project_id}' first, "
            "then run the command again.")
    return project_id


def docker_gid() -> int:
    try:
        return grp.getgrnam(DOCKER_GROUP).gr_gid
    except KeyError:
        raise EggieError(
            "This VM has no docker group, so Eggie is not set up here. "
            "Run `eggie setup` on your computer.") from None


def prepare_overlay_dir(project: Path, gid: int) -> None:
    """Let the agent write .eggie/overlay.yml, the one file it writes here.

    It runs as a non-root member of the docker group, while a coding agent
    working as root leaves directories 755 and files 644.
    """
    eggie_dir = project / ".eggie"
    overlay = eggie_dir / "overlay.yml"
    try:
        eggie_dir.mkdir(exist_ok=True)
        os.chown(eggie_dir, -1, gid)
        os.chmod(eggie_dir, 0o2775)
        if overlay.exists():
            os.chown(overlay, -1, gid)
            os.chmod(overlay, 0o664)
    except PermissionError as e:
        raise EggieError(
            f"Eggie could not make {eggie_dir} writable for itself "
            f"({e.strerror}). Run the command as the folder's owner.") from None
```

(The remaining imports are used by Tasks 2 and 3.)

- [ ] **Step 6: Exclude `host/provision/` from the dead-module scan**

In `tests/host/test_no_dead_modules.py`, add below `ROOTS = {"host.cli"}`:

```python
# Files here are pushed into the VM and run there; the host never imports them.
PROVISION = HOST / "provision"
```

and in `test_every_host_module_is_reachable_from_the_entry_point` replace

```python
    files = sorted(HOST.rglob("*.py"))
```

with

```python
    files = sorted(py for py in HOST.rglob("*.py") if PROVISION not in py.parents)
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `python3 -m pytest tests/guest tests/test_constants_agree.py tests/host/test_no_dead_modules.py tests/test_no_platform_leak.py -q`
Expected: all PASS.

- [ ] **Step 8: Commit**

```bash
git add host/provision/guest/eggie.py tests/guest tests/test_constants_agree.py tests/host/test_no_dead_modules.py
git commit -m "feat: guest eggie CLI path rules and overlay permissions

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01DBSpFPWc1TRWYn4pnjP8px"
```

---

### Task 2: Guest CLI talks to the agent — `up`, `status`, `logs`, `down`

**Files:**
- Modify: `host/provision/guest/eggie.py` (append)
- Create: `tests/guest/conftest.py`, `tests/guest/test_transport.py`, `tests/guest/test_seam.py`

**Interfaces:**
- Consumes: everything Task 1 produced.
- Consumes (tests): `tests.host.test_client_seam.AppOpener`, `TOKEN`; `tests.agent.test_api_routes.COMPOSE_ONE_WEB`, `COMPOSE_MALFORMED`, `FakeRunner`, `FakeProbe`; `agent.api.app.create_app`; `agent.core.config.AgentConfig`; `agent.core.exec.Completed`.
- Produces (in `eggie.py`): `START_STACK: str`; `class AgentError(EggieError)` with `.code`; `class JobFailed(EggieError)` with `.result: dict`; `read_token(path: Path) -> str`; `class Agent(token, *, base_url=..., opener=None, sleep=time.sleep, monotonic=time.monotonic)` with `ensure_project(id) -> None`, `project(id) -> dict`, `projects() -> list[dict]`, `up(id) -> dict`, `down(id) -> dict`, `logs(id, service=None) -> str`; `@dataclass class Env(root, cwd, agent, gid, git, out, err)` where `git: Callable[[list[str], dict], subprocess.CompletedProcess]`; `_start(env, folder: Path, project_id: str) -> None`; `main(argv: list[str] | None = None, env: Env | None = None) -> int`.
- Produces (tests): fixture `guest` yielding a `Guest` with `.root: Path`, `.runner: FakeRunner`, `.git_calls: list`, `.git_result: tuple[int, str]`, `.clone_files: dict[str, str]`, and `.run(*argv, cwd: Path) -> tuple[int, str, str]` (exit code, stdout, stderr).

- [ ] **Step 1: Write the `guest` fixture**

`tests/guest/conftest.py`:

```python
import io
import os
import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent.api.app import create_app
from agent.core.config import AgentConfig
from tests.agent.test_api_routes import FakeProbe, FakeRunner
from tests.guest.loader import load
from tests.host.test_client_seam import TOKEN, AppOpener

cli = load()


class Guest:
    """The guest CLI wired to the real agent app, in-process, over the fake
    Docker runner -- the same seam tests/host/test_client_seam.py uses."""

    def __init__(self, root: Path, client: TestClient, runner: FakeRunner):
        self.root = root
        self.runner = runner
        self._client = client
        self.git_calls: list = []
        self.git_result: tuple[int, str] = (0, "")
        self.clone_files: dict[str, str] = {}

    def _git(self, argv, env):
        self.git_calls.append((argv, env))
        code, stderr = self.git_result
        if code == 0:
            target = Path(argv[-1])
            target.mkdir()
            for name, text in self.clone_files.items():
                (target / name).write_text(text)
        return subprocess.CompletedProcess(argv, code, "", stderr)

    def _agent(self):
        return cli.Agent(TOKEN, opener=AppOpener(self._client),
                         sleep=lambda _s: time.sleep(0.01))

    def run(self, *argv, cwd: Path) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        env = cli.Env(root=self.root, cwd=cwd, agent=self._agent,
                      gid=os.getgid, git=self._git, out=out, err=err)
        code = cli.main(list(argv), env)
        return code, out.getvalue(), err.getvalue()


@pytest.fixture
def guest(tmp_path):
    (tmp_path / "agent.token").write_text(TOKEN)
    root = tmp_path / "projects"
    root.mkdir()
    config = AgentConfig(domain="test.local", edge_port=41080,
                         projects_root=root, state_db=tmp_path / "state.db",
                         token_path=tmp_path / "agent.token", ready_timeout=0.0)
    runner = FakeRunner()
    app = create_app(config=config, runner=runner, http_probe=FakeProbe())
    with TestClient(app) as client:
        yield Guest(root, client, runner)
```

- [ ] **Step 2: Write the failing seam tests**

`tests/guest/test_seam.py`:

```python
import threading
import time

from agent.core.exec import Completed
from tests.agent.test_api_routes import COMPOSE_MALFORMED, COMPOSE_ONE_WEB


def _project(guest, name="blog", compose=COMPOSE_ONE_WEB):
    folder = guest.root / name
    folder.mkdir()
    (folder / "docker-compose.yml").write_text(compose)
    return folder


def _compose_ups(runner):
    return [argv for argv in runner.calls if argv[-2:] == ["up", "-d"]]


def _wait_for(condition, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "condition never became true"
        time.sleep(0.01)


def test_up_turns_a_folder_in_the_projects_root_into_a_routed_project(guest):
    folder = _project(guest)
    code, out, err = guest.run("up", cwd=folder)
    assert (code, err) == (0, "")
    assert "http://blog.test.local:41080" in out
    # Started with the generated overlay: without it there is no Traefik route.
    assert guest.runner.argv_containing(f"{folder}/.eggie/overlay.yml")


def test_up_from_a_subfolder_starts_the_enclosing_project(guest):
    folder = _project(guest)
    (folder / "src").mkdir()
    code, out, _err = guest.run("up", cwd=folder / "src")
    assert code == 0
    assert "http://blog.test.local:41080" in out


def test_a_second_up_restarts_an_already_registered_project(guest):
    folder = _project(guest)
    assert guest.run("up", cwd=folder)[0] == 0
    code, _out, err = guest.run("up", cwd=folder)
    assert (code, err) == (0, "")
    assert len(_compose_ups(guest.runner)) == 2


def test_a_broken_compose_file_is_reported_in_the_agents_own_words(guest):
    folder = _project(guest, compose=COMPOSE_MALFORMED)
    code, _out, err = guest.run("up", cwd=folder)
    assert code == 1
    assert "docker-compose.yml is not valid YAML" in err


def test_a_start_that_fails_reports_the_guest_output_and_points_at_logs(guest):
    guest.runner.up = Completed(1, "", "port is already allocated")
    folder = _project(guest)
    code, _out, err = guest.run("up", cwd=folder)
    assert code == 1
    assert "port is already allocated" in err
    assert "eggie logs" in err


def test_a_busy_project_is_waited_for_rather_than_reported(guest):
    folder = _project(guest)
    guest.runner.up_gate = threading.Event()
    first = threading.Thread(target=guest.run, args=("up",), kwargs={"cwd": folder})
    first.start()
    # Once compose up is running, the project's lock is held.
    _wait_for(lambda: _compose_ups(guest.runner))
    threading.Timer(0.2, guest.runner.up_gate.set).start()

    code, out, err = guest.run("down", cwd=folder)
    first.join(5)

    assert (code, err) == (0, "")
    assert "blog stopped." in out


def test_status_names_folders_that_are_not_set_up_yet(guest):
    blog = _project(guest)
    assert guest.run("up", cwd=blog)[0] == 0
    (guest.root / "shop").mkdir()
    code, out, _err = guest.run("status", cwd=guest.root)
    assert code == 0
    assert out.splitlines()[0].startswith("blog")
    assert "Not set up yet (run `eggie up` in each): shop" in out


def test_status_inside_an_unregistered_folder_says_how_to_set_it_up(guest):
    (guest.root / "shop").mkdir()
    code, out, _err = guest.run("status", cwd=guest.root / "shop")
    assert code == 0
    assert "shop is not set up yet" in out


def test_logs_and_down_outside_a_project_say_where_to_run_them(guest):
    for command in ("logs", "down"):
        code, _out, err = guest.run(command, cwd=guest.root)
        assert code == 1
        assert "inside a project folder" in err
```

- [ ] **Step 3: Write the failing transport tests**

`tests/guest/test_transport.py`:

```python
import io
import os
import urllib.error

import pytest

from tests.guest.loader import load

cli = load()


class _Refused:
    def open(self, request, timeout=None):
        raise urllib.error.URLError(ConnectionRefusedError(111, "Connection refused"))


def test_a_stopped_service_is_named_with_the_command_that_starts_it(tmp_path):
    root = tmp_path / "projects"
    (root / "blog").mkdir(parents=True)
    out, err = io.StringIO(), io.StringIO()
    env = cli.Env(root=root, cwd=root / "blog",
                  agent=lambda: cli.Agent("t", opener=_Refused()),
                  out=out, err=err)
    assert cli.main(["status"], env) == 1
    assert "not answering" in err.getvalue()
    assert cli.START_STACK in err.getvalue()


def test_a_vm_without_a_token_says_eggie_is_not_set_up(tmp_path):
    with pytest.raises(cli.EggieError, match="not set up"):
        cli.read_token(tmp_path / "agent.token")


@pytest.mark.skipif(os.geteuid() == 0, reason="root can read any file")
def test_an_unreadable_token_points_at_the_docker_group(tmp_path):
    token = tmp_path / "agent.token"
    token.write_text("secret")
    token.chmod(0)
    with pytest.raises(cli.EggieError, match="docker group"):
        cli.read_token(token)
```

- [ ] **Step 4: Run the tests to verify they fail**

Run: `python3 -m pytest tests/guest/test_seam.py tests/guest/test_transport.py -q`
Expected: FAIL — `AttributeError: module 'eggie_guest_cli' has no attribute 'Agent'` (or `Env`).

- [ ] **Step 5: Implement the agent client, the commands and `main`**

Append to `host/provision/guest/eggie.py`:

```python
REQUEST_TIMEOUT = 30.0
LOGS_TIMEOUT = 120.0
# `up` pulls or builds images, which is minutes, not seconds.
JOB_TIMEOUT = 1800.0
JOB_POLL_INTERVAL = 1.0
BUSY_RETRY_TIMEOUT = 60.0
BUSY_RETRY_INTERVAL = 1.0

START_STACK = "sudo /usr/bin/docker compose -f /opt/eggie/stack.yml up -d"
# The agent reads its token once, at startup, so only a recreate picks up a new one.
RESTART_AGENT = f"{START_STACK} --force-recreate agent"
_GUIDANCE = {
    "unauthorized": f"The Eggie service needs a restart. Run: {RESTART_AGENT}",
    "agent_unconfigured": f"The Eggie service needs a restart. Run: {RESTART_AGENT}",
}


class AgentError(EggieError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class JobFailed(EggieError):
    def __init__(self, message: str, result: dict | None):
        super().__init__(message)
        self.result = result or {}


def read_token(path: Path) -> str:
    try:
        token = path.read_text().strip()
    except PermissionError:
        raise EggieError(
            "This user can't reach Eggie: it must be in the docker group. "
            "Run `sudo usermod -aG docker $USER` and start a new session.") from None
    except OSError:
        token = ""
    if not token:
        raise EggieError("Eggie is not set up in this VM yet. "
                          "Run `eggie setup` on your computer.")
    return token


def _agent_error(exc: urllib.error.HTTPError) -> AgentError:
    """Every non-2xx body from the agent is {"error": {"code", "message"}};
    anything else on this port must still read as a sentence."""
    try:
        error = json.loads(exc.read().decode("utf-8", "replace"))["error"]
        code, message = str(error["code"]), str(error["message"])
    except (OSError, ValueError, KeyError, TypeError):
        return AgentError("http_error", f"The Eggie service answered HTTP "
                                        f"{exc.code} ({exc.reason}).")
    guidance = _GUIDANCE.get(code)
    return AgentError(code, f"{guidance} (the service said: {message})"
                      if guidance else message)


class Agent:
    """The agent API over the same HTTP contract host/client.py speaks."""

    def __init__(self, token: str, *, base_url: str = f"http://127.0.0.1:{AGENT_PORT}",
                 opener=None, sleep=time.sleep, monotonic=time.monotonic):
        self._token = token
        self._base = base_url
        self._opener = opener or urllib.request.build_opener()
        self._sleep = sleep
        self._monotonic = monotonic

    def _open(self, method: str, path: str, *, payload: dict | None = None,
              timeout: float = REQUEST_TIMEOUT) -> str:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(self._base + path, data=data, method=method)
        request.add_header("Authorization", f"Bearer {self._token}")
        if data is not None:
            request.add_header("Content-Type", "application/json")
        try:
            with self._opener.open(request, timeout=timeout) as response:
                return response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            raise _agent_error(e) from None
        except (urllib.error.URLError, OSError):
            raise EggieError("The Eggie service in this VM is not answering. "
                              f"Start it with: {START_STACK}") from None

    def _call(self, method: str, path: str, payload: dict | None = None) -> dict:
        body = self._open(method, path, payload=payload)
        return json.loads(body) if body.strip() else {}

    def _while_busy(self, call):
        """`project_busy`: another operation holds the project's lock."""
        deadline = self._monotonic() + BUSY_RETRY_TIMEOUT
        while True:
            try:
                return call()
            except AgentError as e:
                if e.code != "project_busy" or self._monotonic() >= deadline:
                    raise
            self._sleep(BUSY_RETRY_INTERVAL)

    def _wait(self, job_id: str) -> dict:
        deadline = self._monotonic() + JOB_TIMEOUT
        while True:
            job = self._call("GET", f"/jobs/{job_id}")
            state = job.get("state")
            if state == "done":
                return job
            if state == "failed":
                raise JobFailed(job.get("detail") or "the operation failed inside the VM",
                                job.get("result"))
            if self._monotonic() >= deadline:
                raise EggieError(f"Eggie was still working after "
                                  f"{JOB_TIMEOUT / 60:.0f} minutes. Run "
                                  "`eggie status` to see where it got to.")
            self._sleep(JOB_POLL_INTERVAL)

    def ensure_project(self, project_id: str) -> None:
        try:
            self._call("POST", "/projects", {"id": project_id})
        except AgentError as e:
            if e.code != "project_exists":
                raise

    def project(self, project_id: str) -> dict:
        return self._call("GET", f"/projects/{project_id}")

    def projects(self) -> list[dict]:
        return self._call("GET", "/projects").get("projects", [])

    def up(self, project_id: str) -> dict:
        started = self._while_busy(
            lambda: self._call("POST", f"/projects/{project_id}/up"))
        return self._wait(started["job_id"])

    def down(self, project_id: str) -> dict:
        started = self._while_busy(
            lambda: self._call("POST", f"/projects/{project_id}/down"))
        return self._wait(started["job_id"])

    def logs(self, project_id: str, service: str | None = None) -> str:
        path = f"/projects/{project_id}/logs"
        if service:
            path += f"?service={urllib.parse.quote(service)}"
        return self._open("GET", path, timeout=LOGS_TIMEOUT)


def _default_agent() -> Agent:
    return Agent(read_token(Path(GUEST_TOKEN)))


def _run_git(argv: list[str], env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(argv, env=env, capture_output=True, text=True)


@dataclass
class Env:
    """Everything a command touches besides its arguments, so tests can point
    it at a temporary projects root and an in-process agent."""
    root: Path = Path(GUEST_PROJECTS)
    cwd: Path = field(default_factory=Path.cwd)
    agent: Callable[[], Agent] = _default_agent
    gid: Callable[[], int] = docker_gid
    git: Callable[[list[str], dict], subprocess.CompletedProcess] = _run_git
    out: TextIO = field(default_factory=lambda: sys.stdout)
    err: TextIO = field(default_factory=lambda: sys.stderr)


def _project_here(env: Env, directory: str | None) -> tuple[Path, str] | None:
    folder = project_of(env.cwd / directory if directory else env.cwd, env.root)
    return (folder, require_id(folder)) if folder else None


def _require_project(env: Env) -> str:
    found = _project_here(env, None)
    if found is None:
        raise EggieError("Run this inside a project folder in ~/projects.")
    return found[1]


def _start(env: Env, folder: Path, project_id: str) -> None:
    if not (folder / COMPOSE_FILE).is_file():
        raise EggieError(f"There is no {COMPOSE_FILE} in {folder}.")
    prepare_overlay_dir(folder, env.gid())
    agent = env.agent()
    agent.ensure_project(project_id)
    print(f"Starting {project_id}…", file=env.out)
    try:
        job = agent.up(project_id)
    except JobFailed as e:
        status = e.result.get("status", "failed")
        raise EggieError(f"{project_id} did not start (status: {status}).\n{e}\n"
                          "To see what it printed, run: eggie logs") from None
    result = job.get("result") or {}
    urls = result.get("urls") or []
    for url in urls:
        print(f"  {url}", file=env.out)
    if not urls:
        print(f"{project_id} started. No service is exposed over HTTP.", file=env.out)
    problem = result.get("problem")
    if problem:
        # The containers are up, so this is no failure -- but the URL above
        # will not answer until this is fixed.
        print(problem["message"], file=env.out)


def _print_project(env: Env, project: dict) -> None:
    urls = project.get("urls") or []
    print(f"{project['id']:<20} {project['status']:<16} "
          f"{urls[0] if urls else ''}".rstrip(), file=env.out)
    for url in urls[1:]:
        print(f"    {url}", file=env.out)
    if project.get("problem"):
        print(f"    problem: {project['problem']['message']}", file=env.out)


def cmd_up(env: Env, directory: str | None) -> None:
    found = _project_here(env, directory)
    if found is None:
        raise EggieError("Projects live in ~/projects. Move this folder there, "
                          "or start a new one with `eggie new <name>`.")
    _start(env, *found)


def cmd_status(env: Env, directory: str | None) -> None:
    agent = env.agent()
    found = _project_here(env, directory)
    if found is not None:
        project_id = found[1]
        try:
            _print_project(env, agent.project(project_id))
        except AgentError as e:
            if e.code != "project_not_found":
                raise
            print(f"{project_id} is not set up yet. Run `eggie up` in it.",
                  file=env.out)
        return
    projects = agent.projects()
    for project in projects:
        _print_project(env, project)
    known = {project["id"] for project in projects}
    waiting = sorted(d.name for d in env.root.iterdir()
                     if d.is_dir() and not d.name.startswith(".")
                     and d.name not in known) if env.root.is_dir() else []
    if waiting:
        print("Not set up yet (run `eggie up` in each): " + ", ".join(waiting),
              file=env.out)
    elif not projects:
        print("No projects yet.", file=env.out)


def cmd_logs(env: Env, service: str | None) -> None:
    print(env.agent().logs(_require_project(env), service), end="", file=env.out)


def cmd_down(env: Env) -> None:
    project_id = _require_project(env)
    env.agent().down(project_id)
    print(f"{project_id} stopped.", file=env.out)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="eggie",
        description="Run projects in this VM behind Eggie's router. Projects "
                    "live in ~/projects, one folder each. Start them only with "
                    "`eggie up`, never `docker compose up`.")
    sub = parser.add_subparsers(dest="command", required=True, metavar="<command>")
    up = sub.add_parser("up", help="start or restart the project in this folder "
                                   "and print its URL")
    up.add_argument("directory", nargs="?")
    status = sub.add_parser("status", help="show this project, or every project")
    status.add_argument("directory", nargs="?")
    logs = sub.add_parser("logs", help="show what this project's containers printed")
    logs.add_argument("service", nargs="?")
    sub.add_parser("down", help="stop the project in this folder")
    return parser


def main(argv: list[str] | None = None, env: Env | None = None) -> int:
    args = _parser().parse_args(argv)
    env = env or Env()
    commands = {
        "up": lambda: cmd_up(env, args.directory),
        "status": lambda: cmd_status(env, args.directory),
        "logs": lambda: cmd_logs(env, args.service),
        "down": lambda: cmd_down(env),
    }
    try:
        commands[args.command]()
    except EggieError as e:
        print(str(e), file=env.err)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python3 -m pytest tests/guest -q`
Expected: all PASS.

- [ ] **Step 7: Smoke-test the script as the VM runs it**

Run: `python3 host/provision/guest/eggie.py --help`
Expected: usage listing `up`, `status`, `logs`, `down`, exit 0.

- [ ] **Step 8: Commit**

```bash
git add host/provision/guest/eggie.py tests/guest
git commit -m "feat: guest eggie up, status, logs and down over the agent API

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01DBSpFPWc1TRWYn4pnjP8px"
```

---

### Task 3: Guest CLI `new` and `clone`

**Files:**
- Modify: `host/provision/guest/eggie.py`
- Create: `tests/guest/test_new_and_clone.py`

**Interfaces:**
- Consumes: `Env`, `_start`, `project_id_for`, `EggieError`, `COMPOSE_FILE`, `main`, `_parser` from Task 2; the `guest` fixture.
- Produces: `repo_name(url: str) -> str`; `cmd_new(env, name) -> None`; `cmd_clone(env, url, name) -> None`; subcommands `new <name>` and `clone <url> [name]`.

- [ ] **Step 1: Write the failing tests**

`tests/guest/test_new_and_clone.py`:

```python
import pytest

from tests.agent.test_api_routes import COMPOSE_ONE_WEB


def test_new_creates_a_folder_named_by_the_project_id(guest):
    code, out, _err = guest.run("new", "Routine Tracker", cwd=guest.root)
    assert code == 0
    assert (guest.root / "routine-tracker").is_dir()
    assert str(guest.root / "routine-tracker") in out


@pytest.mark.parametrize("url,folder", [
    ("https://github.com/org/My.Repo.git", "my-repo"),
    ("https://github.com/org/blog/", "blog"),
    ("git@github.com:org/Shop.git", "shop"),
])
def test_clone_names_the_folder_after_the_repository(guest, url, folder):
    code, _out, err = guest.run("clone", url, cwd=guest.root)
    assert (code, err) == (0, "")
    assert (guest.root / folder).is_dir()


def test_clone_never_lets_git_wait_for_a_password(guest):
    # A coding agent's shell has no terminal: a credential prompt hangs forever.
    guest.git_result = (128, "fatal: could not read Username for "
                             "'https://github.com': terminal prompts disabled")
    code, _out, err = guest.run("clone", "https://github.com/org/private.git",
                                cwd=guest.root)
    _argv, env = guest.git_calls[0]
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert code == 1
    assert "Could not download https://github.com/org/private.git" in err
    assert "terminal prompts disabled" in err


def test_a_cloned_repo_with_a_compose_file_is_started_straight_away(guest):
    guest.clone_files = {"docker-compose.yml": COMPOSE_ONE_WEB}
    code, out, err = guest.run("clone", "https://github.com/org/blog.git",
                               cwd=guest.root)
    assert (code, err) == (0, "")
    assert "http://blog.test.local:41080" in out


def test_a_cloned_repo_without_a_compose_file_says_one_is_needed(guest):
    code, out, _err = guest.run("clone", "https://github.com/org/notes.git",
                                cwd=guest.root)
    assert code == 0
    assert "no docker-compose.yml yet" in out


def test_new_and_clone_never_reuse_an_existing_folder(guest):
    (guest.root / "blog").mkdir()
    assert guest.run("new", "Blog", cwd=guest.root)[0] == 1
    assert guest.run("clone", "https://github.com/org/blog.git", cwd=guest.root)[0] == 1
    assert guest.git_calls == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/guest/test_new_and_clone.py -q`
Expected: FAIL — argparse exits with `invalid choice: 'new'` / `'clone'`.

- [ ] **Step 3: Implement `new` and `clone`**

In `host/provision/guest/eggie.py`, add after `cmd_down`:

```python
def repo_name(url: str) -> str:
    """The last path segment without `.git`, for https and scp-style URLs alike."""
    return re.split(r"[/:]", url.rstrip("/"))[-1].removesuffix(".git")


def _new_folder(env: Env, name: str) -> Path:
    project_id = project_id_for(name)
    if not project_id:
        raise EggieError(f"'{name}' cannot be a project name. "
                          "Use letters, digits and dashes.")
    folder = env.root / project_id
    if folder.exists():
        raise EggieError(f"{folder} already exists. Pick another name, "
                          "or run `eggie up` in it.")
    return folder


def cmd_new(env: Env, name: str) -> None:
    folder = _new_folder(env, name)
    folder.mkdir()
    print(f"Created {folder}. Put the project's files there, "
          "then run `eggie up` in it.", file=env.out)


def cmd_clone(env: Env, url: str, name: str | None) -> None:
    folder = _new_folder(env, name or repo_name(url))
    # A coding agent's shell has no terminal to answer a credential prompt,
    # so a private repository must fail instead of hanging.
    result = env.git(["git", "clone", "--", url, str(folder)],
                     {**os.environ, "GIT_TERMINAL_PROMPT": "0"})
    if result.returncode != 0:
        raise EggieError(f"Could not download {url}:\n{(result.stderr or '').strip()}")
    if (folder / COMPOSE_FILE).is_file():
        _start(env, folder, folder.name)
    else:
        print(f"Downloaded to {folder}. It has no {COMPOSE_FILE} yet; one must "
              "be written before `eggie up`.", file=env.out)
```

In `_parser()`, add before `return parser`:

```python
    new = sub.add_parser("new", help="create an empty project folder in ~/projects")
    new.add_argument("name")
    clone = sub.add_parser("clone", help="download a git repository into "
                                         "~/projects and start it")
    clone.add_argument("url")
    clone.add_argument("name", nargs="?")
```

In `main()`, add to the `commands` dict:

```python
        "new": lambda: cmd_new(env, args.name),
        "clone": lambda: cmd_clone(env, args.url, args.name),
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/guest -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add host/provision/guest/eggie.py tests/guest/test_new_and_clone.py
git commit -m "feat: guest eggie new and clone

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01DBSpFPWc1TRWYn4pnjP8px"
```

---

### Task 4: What coding agents read — `eggie.md` and the `eggie-setup` skill

**Files:**
- Create: `host/provision/agents/eggie.md`
- Create: `host/provision/agents/skills/eggie-setup/SKILL.md`

**Interfaces:**
- Consumes: the guest CLI's commands (`up`, `status`, `logs`, `down`, `new`, `clone`) from Tasks 2–3.
- Produces: the two files Tasks 5–6 install. The skill directory name `eggie-setup` equals its frontmatter `name`.

No automated test: these files hold no logic. Task 7's acceptance run is their test.

- [ ] **Step 1: Write `host/provision/agents/eggie.md`**

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

- [ ] **Step 2: Write `host/provision/agents/skills/eggie-setup/SKILL.md`**

````markdown
---
name: eggie-setup
description: Set up, create, import, clone or run a project in this Eggie VM — a repository URL, an archive, a folder, or a new app described in plain words. Use whenever the user wants something running with a link to open.
---

# Setting up a project with Eggie

Projects live in `~/projects/<name>/`, one folder each. Eggie runs them in
Docker and gives each one a URL. The user is not technical: choose everything
yourself and never ask them about technology.

## 1. Get the project into ~/projects

| The user gives you | Do |
|---|---|
| A repository URL | `eggie clone <url>` — it also starts the project if it can |
| An archive (zip, tar) | unpack it so its `docker-compose.yml` sits directly in `~/projects/<name>/` |
| A folder already in `~/projects` | nothing — go to step 3 |
| A folder elsewhere in the VM | move it into `~/projects/` |
| A description of an app | `eggie new <name>`, then build it there following step 2 |

If `eggie up` asks you to rename the folder, rename it to the name it gives
and run `eggie up` again.

If `eggie clone` says it could not download the repository, it is private.
Tell the user in plain words that the repository needs access; do not ask for
tokens or keys unprompted.

## 2. Writing a compose file (new apps, or projects without one)

- Pick the simplest mainstream stack for the job yourself.
- Everything runs in `docker-compose.yml`. Run language tools inside containers
  (`docker compose run --rm <service> <command>`); never install them on the VM.
- The app must listen on `0.0.0.0`, not `127.0.0.1`, or its URL never answers.
- Publish no host ports. Tell Eggie which service serves the web page in
  `.eggie/project.yml`, with only a `web:` key:

  ```yaml
  web:
    - service: app
      port: 3000
  ```
- Mount the source into the container and run a development server that
  reloads on change, so edits show when the user refreshes the page.
- Keep data in SQLite, or in a database service with a named volume.

## 3. Start it and prove it works

1. Run `eggie up` inside the project folder. It prints the URL.
2. Check the URL answers before telling the user it works:
   `curl -s -o /dev/null -w '%{http_code}\n' <url>`.
3. On a failure or no answer: read `eggie logs`, fix the cause, run
   `eggie up` again.
4. Give the user the URL in one plain sentence.
````

- [ ] **Step 3: Commit**

```bash
git add host/provision/agents
git commit -m "feat: agent-neutral Eggie instructions and setup skill

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01DBSpFPWc1TRWYn4pnjP8px"
```

---

### Task 5: Per-home installer — `install-agents.sh`

**Files:**
- Create: `host/provision/install-agents.sh`
- Create: `tests/host/test_install_agents.py`

**Interfaces:**
- Consumes: `host/provision/agents/` from Task 4.
- Produces: `install-agents.sh <agents-source-dir> <home> <owner uid:gid>` — copies the skill to `<home>/.claude/skills/eggie-setup/` and `<home>/.agents/skills/eggie-setup/`, replaces the `<!-- eggie:begin -->`…`<!-- eggie:end -->` block in `<home>/.codex/AGENTS.md`, links `<home>/projects` → `/opt/eggie/projects` unless something else is there.

- [ ] **Step 1: Write the failing tests**

`tests/host/test_install_agents.py`:

```python
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "host" / "provision" / "install-agents.sh"
SOURCE = ROOT / "host" / "provision" / "agents"


def _install(home: Path, source: Path = SOURCE) -> subprocess.CompletedProcess:
    result = subprocess.run(
        ["bash", str(SCRIPT), str(source), str(home), f"{os.getuid()}:{os.getgid()}"],
        capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return result


def test_every_agent_finds_the_skill_in_its_own_home_location(tmp_path):
    _install(tmp_path)
    expected = (SOURCE / "skills" / "eggie-setup" / "SKILL.md").read_text()
    for where in (".claude/skills", ".agents/skills"):
        assert (tmp_path / where / "eggie-setup" / "SKILL.md").read_text() == expected


def test_the_codex_block_is_replaced_and_the_users_own_text_kept(tmp_path):
    home = tmp_path / "home"
    (home / ".codex").mkdir(parents=True)
    agents_md = home / ".codex" / "AGENTS.md"
    agents_md.write_text("# my notes\nkeep me")  # no trailing newline on purpose
    source = tmp_path / "src"
    shutil.copytree(SOURCE, source)

    _install(home, source)
    (source / "eggie.md").write_text("new instructions\n")
    _install(home, source)

    text = agents_md.read_text()
    assert text.startswith("# my notes\nkeep me\n")
    assert text.count("<!-- eggie:begin -->") == 1
    assert "new instructions" in text
    assert "You are working inside an Eggie VM" not in text


def test_the_projects_link_is_made_once_and_a_real_folder_is_left_alone(tmp_path):
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    _install(fresh)
    _install(fresh)
    assert os.readlink(fresh / "projects") == "/opt/eggie/projects"

    taken = tmp_path / "taken"
    (taken / "projects").mkdir(parents=True)
    result = _install(taken)
    assert (taken / "projects").is_dir() and not (taken / "projects").is_symlink()
    assert "left" in result.stdout
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/host/test_install_agents.py -q`
Expected: FAIL — bash cannot open `install-agents.sh`.

- [ ] **Step 3: Write `host/provision/install-agents.sh`**

```bash
#!/usr/bin/env bash
# Copies Eggie's skill and Codex instructions into one home directory.
# Run by bootstrap.sh as root, once per home:
#   install-agents.sh <agents-source-dir> <home> <owner uid:gid>
set -euo pipefail

SRC=$1
HOME_DIR=$2
OWNER=$3
SKILL=eggie-setup
BEGIN='<!-- eggie:begin -->'
END='<!-- eggie:end -->'

# Claude Code reads ~/.claude/skills; Codex, Gemini CLI, Cursor and Copilot
# read ~/.agents/skills. Copies, so a moved source never leaves a dead link.
for skills in "$HOME_DIR/.claude/skills" "$HOME_DIR/.agents/skills"; do
  mkdir -p "$skills"
  rm -rf "${skills:?}/$SKILL"
  cp -r "$SRC/skills/$SKILL" "$skills/"
done

# Codex has no system-wide AGENTS.md, and the user may keep their own text in
# this one: only the block between the markers is ours to replace.
mkdir -p "$HOME_DIR/.codex"
AGENTS_MD="$HOME_DIR/.codex/AGENTS.md"
touch "$AGENTS_MD"
sed -i "\|^$BEGIN\$|,\|^$END\$|d" "$AGENTS_MD"
if [[ -s "$AGENTS_MD" && -n "$(tail -c1 "$AGENTS_MD")" ]]; then
  echo >> "$AGENTS_MD"
fi
{ echo "$BEGIN"; cat "$SRC/eggie.md"; echo "$END"; } >> "$AGENTS_MD"

if [[ ! -e "$HOME_DIR/projects" && ! -L "$HOME_DIR/projects" ]]; then
  ln -s /opt/eggie/projects "$HOME_DIR/projects"
elif [[ ! -L "$HOME_DIR/projects" ]]; then
  echo "left $HOME_DIR/projects alone: it already exists and is not Eggie's link"
fi

chown -R "$OWNER" "$HOME_DIR/.claude/skills/$SKILL" "$HOME_DIR/.agents/skills/$SKILL" "$AGENTS_MD"
chown "$OWNER" "$HOME_DIR/.claude" "$HOME_DIR/.claude/skills" \
  "$HOME_DIR/.agents" "$HOME_DIR/.agents/skills" "$HOME_DIR/.codex"
if [[ -L "$HOME_DIR/projects" ]]; then
  chown -h "$OWNER" "$HOME_DIR/projects"
fi
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/host/test_install_agents.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add host/provision/install-agents.sh tests/host/test_install_agents.py
git commit -m "feat: install Eggie's skill and Codex block into a home directory

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01DBSpFPWc1TRWYn4pnjP8px"
```

---

### Task 6: Provisioning — push, bundle and install everything

**Files:**
- Modify: `host/core/bootstrap.py` (`guest_assets`)
- Modify: `host/core/constants.py` (`BOOTSTRAP_VERSION`)
- Modify: `host/provision/bootstrap.sh` (new step 8, marker becomes step 9)
- Modify: `packaging/windows/eggie.spec` (`datas`)
- Modify: `tests/host/test_bootstrap.py`, `tests/host/test_bootstrap_shell.py`
- Modify: `CLAUDE.md`

**Interfaces:**
- Consumes: `host/provision/guest/eggie.py`, `host/provision/install-agents.sh`, `host/provision/agents/eggie.md`, `host/provision/agents/skills/eggie-setup/SKILL.md`.
- Produces: guest paths `/opt/eggie/bin/eggie`, `/opt/eggie/bin/install-agents.sh`, `/opt/eggie/agents/eggie.md`, `/opt/eggie/agents/skills/eggie-setup/SKILL.md`; installed `/usr/local/bin/eggie`, `/etc/claude-code/CLAUDE.md`, `/etc/codex/skills/eggie-setup/`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/host/test_bootstrap.py`:

```python
def test_every_pushed_asset_fits_one_guest_command_line():
    # _push_file sends each asset base64-encoded inside one `wsl.exe -- bash -lc`
    # argument, and Windows caps the whole command line at 32,767 characters.
    import base64
    from host.core.bootstrap import guest_assets
    for local, remote in guest_assets():
        encoded = base64.b64encode(local.read_bytes().replace(b"\r\n", b"\n"))
        assert len(encoded) + 2 * len(remote) + 200 < 32_767, (
            f"{local.name} is too big to push into the VM in one command")
```

Append to `tests/host/test_bootstrap_shell.py`:

```python
def test_bootstrap_installs_the_agent_files_where_the_host_pushes_them():
    # The host pushes to guest_assets()'s paths and the script reads them by
    # literal path; two independent spellings install nothing, silently.
    import posixpath
    from host.core.bootstrap import guest_assets

    remotes = {local.name: remote for local, remote in guest_assets()}
    text = "\n".join(_commands())
    assert remotes["eggie.py"] in text
    assert remotes["install-agents.sh"] in text
    assert posixpath.dirname(remotes["eggie.md"]) in text
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/host/test_bootstrap.py tests/host/test_bootstrap_shell.py tests/host/test_frozen_bundle.py -q`
Expected: FAIL — `KeyError: 'eggie.py'` in the shell test.

- [ ] **Step 3: Push the new assets**

In `host/core/bootstrap.py`, below `_GUEST_SCRIPT = f"{_GUEST_DIR}/bootstrap.sh"` add:

```python
_GUEST_AGENTS = f"{constants.GUEST_ROOT}/agents"
_SKILL = "skills/eggie-setup/SKILL.md"
```

and replace the `return` in `guest_assets()` with:

```python
    return (
        (_ASSETS / "bootstrap.sh", _GUEST_SCRIPT),
        (_ASSETS / "stack.yml", constants.GUEST_STACK),
        (_ASSETS / "guest" / "eggie.py", f"{_GUEST_DIR}/eggie"),
        (_ASSETS / "install-agents.sh", f"{_GUEST_DIR}/install-agents.sh"),
        (_ASSETS / "agents" / "eggie.md", f"{_GUEST_AGENTS}/eggie.md"),
        (_ASSETS / "agents" / _SKILL, f"{_GUEST_AGENTS}/{_SKILL}"),
    )
```

- [ ] **Step 4: Bundle them into the frozen host**

In `packaging/windows/eggie.spec`, add to the `datas=[` list, after the `stack.yml` entry:

```python
        ("../../host/provision/guest/eggie.py", "host/provision/guest"),
        ("../../host/provision/install-agents.sh", "host/provision"),
        ("../../host/provision/agents/eggie.md", "host/provision/agents"),
        ("../../host/provision/agents/skills/eggie-setup/SKILL.md",
         "host/provision/agents/skills/eggie-setup"),
```

- [ ] **Step 5: Install everything from `bootstrap.sh`**

In `host/provision/bootstrap.sh`, replace

```bash
# 8. marker, last: a failure above must leave no marker behind.
```

with

```bash
# 8. coding agents: the in-VM `eggie` command, and what each agent reads.
if ! dpkg -s git >/dev/null 2>&1; then
  apt-get update
  apt-get install -y git
fi
command -v python3 >/dev/null || { echo 'python3 is missing; the eggie command needs it' >&2; exit 1; }
install -m 755 /opt/eggie/bin/eggie /usr/local/bin/eggie

# System-wide where the agent has such a place, so which user runs the
# session does not matter.
install -d /etc/claude-code /etc/codex/skills
install -m 644 /opt/eggie/agents/eggie.md /etc/claude-code/CLAUDE.md
rm -rf /etc/codex/skills/eggie-setup
cp -r /opt/eggie/agents/skills/eggie-setup /etc/codex/skills/

# Per home where it is not: root (WSL sessions), every login account (Lima's
# user) and /etc/skel for accounts made later. The docker group is the only
# way a non-root user can read the agent token.
bash /opt/eggie/bin/install-agents.sh /opt/eggie/agents /root 0:0
bash /opt/eggie/bin/install-agents.sh /opt/eggie/agents /etc/skel 0:0
while IFS=: read -r name _ uid gid _ home _; do
  if (( uid >= 1000 && uid < 60000 )) && [[ -d "$home" ]]; then
    usermod -aG docker "$name"
    bash /opt/eggie/bin/install-agents.sh /opt/eggie/agents "$home" "$uid:$gid"
  fi
done < <(getent passwd)

# 9. marker, last: a failure above must leave no marker behind.
```

- [ ] **Step 6: Bump the bootstrap version**

In `host/core/constants.py` change `BOOTSTRAP_VERSION = 5` to `BOOTSTRAP_VERSION = 6`.

- [ ] **Step 7: Run the tests to verify they pass**

Run: `python3 -m pytest tests/host tests/test_constants_agree.py -q`
Expected: all PASS, including `test_frozen_bundle.py` (it now requires the new `datas` entries) and `test_bootstrap_is_valid_bash`.

- [ ] **Step 8: Record the guest CLI in `CLAUDE.md`**

In `CLAUDE.md`, under `### Layers`, add after the `host/provision/` bullet:

```markdown
- `host/provision/guest/eggie.py` — the `eggie` command **inside** the VM, used by coding
  agents (Claude Code, Codex, …) working there: `up`/`new`/`clone`/`status`/`logs`/`down`
  over the agent API with the guest token. One stdlib-only file, loaded by tests by path
  (`tests/guest/loader.py`); it shares constants with both sides, held equal by
  `tests/test_constants_agree.py`. `host/provision/agents/` holds what those agents read
  (`eggie.md`, the `eggie-setup` skill); `bootstrap.sh` step 8 and `install-agents.sh`
  copy them into each agent's discovery paths (`/etc/claude-code/CLAUDE.md`,
  `/etc/codex/skills`, `~/.claude/skills`, `~/.agents/skills`, a marked block in
  `~/.codex/AGENTS.md`). Nothing is written into user repositories.
```

and under `### Things that will bite you`, add:

```markdown
- **A change to the guest CLI or to `host/provision/agents/` needs a `BOOTSTRAP_VERSION` bump**
  just like `bootstrap.sh` does: the marker is the only thing that makes an existing VM
  re-provision. Each pushed asset must also fit one `wsl.exe` command line
  (`test_every_pushed_asset_fits_one_guest_command_line`).
```

Also update the test count in the `## Commands` block from `365 tests` to the new total printed by the full run in Task 7.

- [ ] **Step 9: Commit**

```bash
git add host/core/bootstrap.py host/core/constants.py host/provision/bootstrap.sh packaging/windows/eggie.spec tests/host/test_bootstrap.py tests/host/test_bootstrap_shell.py CLAUDE.md
git commit -m "feat: provision the guest eggie command and agent instructions

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01DBSpFPWc1TRWYn4pnjP8px"
```

---

### Task 7: Verify, open the PR, review, hand over the acceptance run

**Files:** none new.

- [ ] **Step 1: Full suite**

Run: `python3 -m pytest -q`
Expected: all PASS (365 before this plan, plus the new tests). If `CLAUDE.md`'s `## Commands` block does not show that exact total, correct it and commit as `docs: update the test count`.

- [ ] **Step 2: Push and open the PR to `main`**

```bash
git push -u origin feature/2-in-vm-agent-setup
gh pr create --base main --title "Coding agents set up projects inside the VM" --body "$(cat <<'EOF'
Closes #2.

## What
- `eggie` inside the VM (`up`, `new`, `clone`, `status`, `logs`, `down`) — stdlib-only, talks to the agent API with the guest token, so every project it starts gets the overlay and a Traefik URL.
- Agent-neutral `eggie.md` + `eggie-setup` skill, copied by provisioning into Claude Code (`/etc/claude-code/CLAUDE.md`, `~/.claude/skills`), Codex (`/etc/codex/skills`, marked block in `~/.codex/AGENTS.md`) and `~/.agents/skills` for Gemini CLI / Cursor / Copilot.
- `BOOTSTRAP_VERSION` 5 → 6 so existing VMs pick it up.

## Tested
- Guest CLI against the real agent app in-process (register + start, restart, agent errors verbatim, failed start, busy retry, status of unregistered folders, clone/new).
- `install-agents.sh` run against a temp home (skill placement, Codex block replaced with user text kept, `~/projects` link).
- Constants/slug agreement, stdlib-only boundary, asset size budget, bootstrap drift.

## Not tested automatically
- The Markdown itself and whether agents follow it — the manual acceptance run in the design doc (§10) covers that.
- Whether a Desktop WSL session reads `/etc/claude-code/CLAUDE.md` inside the distro is not documented; the acceptance run settles it.

Design: `docs/superpowers/specs/2026-09-11-in-vm-agent-setup-design.md`

🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_01DBSpFPWc1TRWYn4pnjP8px
EOF
)"
```

- [ ] **Step 3: Code review by a separate agent**

Dispatch a fresh reviewer agent on the PR diff (`git diff main...HEAD`), with the spec and this plan as context. Address confirmed findings in follow-up commits on the same branch.

- [ ] **Step 4: Hand the user the acceptance run (Windows, PowerShell)**

Never boot or exec into the VM from the development WSL shell; give the user these commands instead.

1. Re-provision the existing VM from this branch: run `eggie setup` the way the host CLI is normally run on their machine (the version bump makes bootstrap re-run).
2. Check what landed:
   ```powershell
   wsl -d eggie-vm -u root -- eggie --help
   wsl -d eggie-vm -u root -- cat /etc/claude-code/CLAUDE.md
   wsl -d eggie-vm -u root -- ls /root/.claude/skills /root/.agents/skills /etc/codex/skills
   wsl -d eggie-vm -u root -- eggie status
   ```
3. In Claude Code Desktop → environment `eggie-vm` → folder `/root/projects`:
   1. paste a public repo URL that has a `docker-compose.yml` and say "set up the project";
   2. say "I want a web app for tracking routines";
   3. copy a folder in through `\\wsl.localhost\eggie-vm\opt\eggie\projects` and say "set this up".
4. In Codex (WSL mode), repeat 3.1 or 3.2.

Each passes when the agent asks no technical question, replies with a `…local.eggie.space:39080` URL, the URL opens in a Windows browser, and a page edit shows after a reload.
