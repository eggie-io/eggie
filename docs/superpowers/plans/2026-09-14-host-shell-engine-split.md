# Host Shell / Engine Split Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the host a VM shell that runs one configurable bootstrap command, and move everything that runs inside the VM into a separately released `engine/`.

**Architecture:** The host's `bootstrap()` stops pushing seven guest files and instead runs a tiny base64 stub that downloads `OMELET_ENGINE_URL` (default: `engine/get.sh` on GitHub) and executes it. `get.sh` picks an `engine-v*` tag, unpacks that tag's `engine/` into `/opt/omelet/engine`, and runs `install.sh`, which installs Docker, the stack, Node 22, the in-VM CLI, agent instructions and skills (`npx skills add` from the local folder), then writes `/opt/omelet/engine.version`. The host's version check becomes an API-number check plus a token-refusal repair.

**Tech Stack:** Python 3.12 (host: stdlib + typer; agent: FastAPI), bash, pytest, `skills@1.5.26` (npx), NodeSource Node 22.

**Spec:** `docs/superpowers/specs/2026-09-14-host-shell-engine-split-design.md`

## Global Constraints

- `host/` never imports `agent/`; host runtime dependency stays `typer` only; no `sys.platform`/`platform.system()`/`os.name` outside `host/providers/`.
- Every test reaches repo files through `Path(__file__).resolve()`, never a cwd-relative path.
- Engine entrypoint default: `https://raw.githubusercontent.com/omelet-app/omelet/main/engine/get.sh`.
- Engine repository default: `https://github.com/omelet-app/omelet`.
- Installed marker: `/opt/omelet/engine.version` — the host checks presence only.
- Engine versions are git tags `engine-vX.Y.Z`.
- Skills CLI pinned to `skills@1.5.26`; it requires Node `>=22.20.0`.
- Host `SUPPORTED_API = frozenset({1})`; agent `API_VERSION = 1`; a `/health` without `api` counts as 1.
- `BOOTSTRAP_VERSION` and `/opt/omelet/.bootstrapped` are gone; nothing replaces them on the host.
- No self-update command (deferred — `docs/future/engine-self-update.md`).
- Comments: only for non-obvious edge cases/workarounds, short, never mention docs, tickets or issues.
- Do not touch `host/provision/Untitled` or the deleted `task.md` — they are the user's uncommitted changes. Never `git add -A`; add the paths each task names.
- Every commit message ends with:
  ```
  Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01Qiyv3LSCWfManiuhZCtzHp
  ```
- Test command: `python3 -m pytest -q`. If `tmp_path` fixtures error with a permission error on `/tmp/pytest-of-$USER`, prefix with `TMPDIR=<a writable dir>` (sandbox artifact).
- Branch: `feature/host-shell-engine-split` (already checked out). Baseline: 423 passed.

## File Map

| Path | Change | Responsibility |
|---|---|---|
| `host/core/constants.py` | modify | `ENGINE_URL`, `ENGINE_MARKER`, `SUPPORTED_API`; bootstrap/image constants removed |
| `host/core/bootstrap.py` | rewrite | Runs the fetch-and-run stub; `BootstrapError` |
| `host/core/install.py` | modify | `connect_step` replaces `agent_version_step`; repair = `bootstrap(repair=True)` |
| `host/cli.py` | modify | `vm create` wording; `selfcheck` checks only bundled host assets |
| `host/setup_app/app.py` | modify | Step label `connect` |
| `packaging/windows/omelet.spec` | modify | `datas` = nginx-hello + omelet.yaml |
| `agent/core/constants.py`, `agent/api/app.py` | modify | `API_VERSION`, `/health` `api` |
| `engine/get.sh` | create | Resolve ref, download tarball, run install.sh |
| `engine/install.sh` | move+modify | Was `host/provision/bootstrap.sh` |
| `engine/stack.yml`, `engine/stack.debug.yml` | move | Was `host/provision/` |
| `engine/cli/omelet.py` | move | Was `host/provision/guest/omelet.py` |
| `engine/instructions/omelet.md` | move | Was `host/provision/agents/omelet.md` |
| `engine/skills/omelet-setup/SKILL.md` | move | Was `host/provision/agents/skills/omelet-setup/` |
| `engine/lib/install-agents.sh`, `engine/lib/login-users.sh` | move+modify | Codex block + `~/projects` link; account selection |
| `tests/engine/…` | move/create | Engine script and guest CLI tests |
| `tests/host/test_bootstrap.py` | rewrite | Host bootstrap behaviour |
| `tests/host/test_connect_step.py` | create | Replaces `test_agent_version.py` |
| `tests/host/test_no_guest_assets.py` | create | `host/provision/` holds no guest assets |
| `CLAUDE.md`, `README.md` | modify | Architecture shape, release steps |

---

### Task 1: Host bootstrap runs the engine entrypoint

**Files:**
- Modify: `host/core/constants.py`
- Rewrite: `host/core/bootstrap.py`
- Modify: `host/core/install.py` (`_bootstrap`, `_reconnect`, the `agent` step)
- Modify: `host/cli.py` (`vm_create`, `selfcheck`)
- Modify: `packaging/windows/omelet.spec`
- Rewrite: `tests/host/test_bootstrap.py`
- Modify: `tests/host/test_bootstrap_shell.py`, `tests/host/test_frozen_bundle.py`, `tests/host/test_exec_callers.py`, `tests/test_vm_cli.py`, `tests/test_setup_cli.py`, `tests/test_constants_agree.py`

**Interfaces:**
- Produces: `host.core.bootstrap.bootstrap(provider, *, source: str | None = None, repair: bool = False) -> None`; `host.core.bootstrap.BootstrapError`; `host.core.constants.ENGINE_URL: str`, `ENGINE_MARKER: str`.
- Produces (guest command contract): one `provider.exec(["bash", "-lc", "<cmd>"], root=True)` where `<cmd>` is `echo <b64 stub> | base64 -d > /tmp/omelet-bootstrap.sh && [OMELET_ENGINE_REF=<ref>] [OMELET_ENGINE_REPAIR=1] bash /tmp/omelet-bootstrap.sh <url>`; marker check is `provider.exec(["test", "-s", ENGINE_MARKER], root=True)`.

- [ ] **Step 1: Write the failing tests**

Replace the whole of `tests/host/test_bootstrap.py` with:

```python
import base64
import os
import re
import subprocess

import pytest

from host.core import constants
from host.core.bootstrap import BootstrapError, bootstrap
from host.core.provider import Completed


class FakeProvider:
    """Guest stand-in. A successful installer run writes the marker, as
    engine/install.sh does on its last line."""

    def __init__(self, *, installed=False, fail=False, stderr="", writes_marker=True):
        self.execs = []
        self.installed = installed
        self._fail = fail
        self._stderr = stderr
        self._writes_marker = writes_marker

    def exec(self, argv, *, root=False):
        self.execs.append((argv, root))
        if argv[:2] == ["test", "-s"]:
            return Completed(0 if self.installed else 1, "", "")
        if self._fail:
            return Completed(1, "", self._stderr)
        if self._writes_marker:
            self.installed = True
        return Completed(0, "", "")

    def installer_runs(self):
        return [(argv, root) for argv, root in self.execs if argv[:2] != ["test", "-s"]]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in ("OMELET_ENGINE_URL", "OMELET_ENGINE_REF"):
        monkeypatch.delenv(name, raising=False)


def _command(provider) -> str:
    (run,) = provider.installer_runs()
    argv, _root = run
    assert argv[:2] == ["bash", "-lc"]
    return argv[2]


def _stub(command: str) -> str:
    return base64.b64decode(re.search(r"echo (\S+) \| base64 -d", command)[1]).decode()


def test_an_installed_engine_is_left_alone():
    p = FakeProvider(installed=True)
    bootstrap(p)
    assert p.installer_runs() == []
    assert any(constants.ENGINE_MARKER in argv for argv, _ in p.execs)


def test_a_missing_engine_runs_the_default_entrypoint_as_root():
    p = FakeProvider()
    bootstrap(p)
    (run,) = p.installer_runs()
    assert run[1] is True, "the installer needs root"
    assert _command(p).rstrip().endswith(constants.ENGINE_URL)


def test_the_entrypoint_can_be_pointed_elsewhere_from_the_environment(monkeypatch):
    monkeypatch.setenv("OMELET_ENGINE_URL", "https://example.invalid/branch/get.sh")
    p = FakeProvider()
    bootstrap(p)
    assert _command(p).rstrip().endswith("https://example.invalid/branch/get.sh")
    assert constants.ENGINE_URL not in _command(p)


def test_a_ref_reaches_the_guest_only_when_one_is_set(monkeypatch):
    p = FakeProvider()
    bootstrap(p)
    assert "OMELET_ENGINE_REF" not in _command(p)

    monkeypatch.setenv("OMELET_ENGINE_REF", "feature/engine-work")
    p = FakeProvider()
    bootstrap(p)
    assert "OMELET_ENGINE_REF=feature/engine-work" in _command(p)


def test_repair_reinstalls_an_installed_engine_and_tells_the_installer_so():
    plain = FakeProvider()
    bootstrap(plain)
    assert "OMELET_ENGINE_REPAIR" not in _command(plain)

    p = FakeProvider(installed=True)
    bootstrap(p, repair=True)
    assert "OMELET_ENGINE_REPAIR=1" in _command(p)


def test_a_failing_installer_raises_with_the_guests_own_error():
    p = FakeProvider(fail=True, stderr="E: Unable to locate package docker-ce")
    with pytest.raises(BootstrapError) as excinfo:
        bootstrap(p)
    assert "docker-ce" in str(excinfo.value)


def test_an_installer_that_exits_zero_without_the_marker_is_a_failure():
    with pytest.raises(BootstrapError, match="engine.version"):
        bootstrap(FakeProvider(writes_marker=False))


def test_a_value_that_would_be_re_split_on_the_guest_command_line_is_refused(monkeypatch):
    # The command crosses wsl.exe or ssh as one argument; a space or quote in
    # it would be parsed as shell by the guest.
    monkeypatch.setenv("OMELET_ENGINE_REF", "main; rm -rf /")
    p = FakeProvider()
    with pytest.raises(BootstrapError, match="OMELET_ENGINE_REF"):
        bootstrap(p)
    assert p.installer_runs() == []


def _fake_curl(tmp_path, body: str):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    curl = bin_dir / "curl"
    curl.write_text("#!/bin/sh\n" + body)
    curl.chmod(0o755)
    return {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}


def _run_stub(tmp_path, env):
    p = FakeProvider()
    bootstrap(p)
    stub = tmp_path / "stub.sh"
    stub.write_text(_stub(_command(p)))
    return subprocess.run(["bash", str(stub), "https://example.invalid/get.sh"],
                          env=env, capture_output=True, text=True)


def test_the_stub_runs_the_script_it_downloaded(tmp_path):
    env = _fake_curl(tmp_path, "echo 'echo engine-ran'\n")
    result = _run_stub(tmp_path, env)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "engine-ran"


def test_a_broken_download_runs_nothing_and_says_so(tmp_path):
    # Piping curl straight into bash would execute whatever arrived before the
    # connection dropped.
    env = _fake_curl(tmp_path, "echo 'echo half-a-script'\nexit 22\n")
    result = _run_stub(tmp_path, env)
    assert result.returncode != 0
    assert "could not download" in result.stderr
    assert "half-a-script" not in result.stdout
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/host/test_bootstrap.py -q`
Expected: FAIL / ERROR — `ImportError` or `AttributeError: module 'host.core.constants' has no attribute 'ENGINE_MARKER'`.

- [ ] **Step 3: Update host constants**

In `host/core/constants.py`:

Replace the module docstring's last paragraph
```
Guest paths are derived from GUEST_ROOT rather than spelled out, and the shell
tests compare bootstrap.sh's literals against these names, so the host and the
script cannot end up pointing at different files.
```
with
```
Guest paths are derived from GUEST_ROOT rather than spelled out, and the engine
shell tests compare install.sh's literals against these names, so the host and
the engine cannot end up pointing at different files.
```

Replace the block from `# Bootstrap is purely host-side provisioning;` through `GUEST_STACK = f"{GUEST_ROOT}/stack.yml"` with:

```python
# The host knows only where the engine's entrypoint lives and which file says
# it finished; what gets installed, and which version, is decided in the VM.
ENGINE_URL = ("https://raw.githubusercontent.com/omelet-app/"
              "omelet/main/engine/get.sh")
ENGINE_MARKER = f"{GUEST_ROOT}/engine.version"
```

Replace the token comment
```
# Generated in the guest by bootstrap.sh, never pushed from the host. The host
```
with
```
# Generated in the guest by the engine installer, never pushed from the host. The host
```

Leave `AGENT_IMAGE` and `EXPECTED_AGENT_VERSION` in place (Task 2 removes them).

- [ ] **Step 4: Rewrite `host/core/bootstrap.py`**

```python
from __future__ import annotations

import base64
import os
import re

from host.core import constants


class BootstrapError(RuntimeError):
    """A command run inside the guest failed; carries the guest's own output."""


# Downloaded in full before it runs: `curl | bash` executes whatever arrived
# before a dropped connection. python3 covers a rootfs that ships without curl.
_STUB = """set -euo pipefail
if command -v curl >/dev/null 2>&1; then
  script="$(curl -fsSL "$1")" || { echo "could not download the Omelet installer from $1" >&2; exit 1; }
else
  script="$(python3 -c 'import sys, urllib.request; sys.stdout.write(urllib.request.urlopen(sys.argv[1], timeout=60).read().decode())' "$1")" \\
    || { echo "could not download the Omelet installer from $1" >&2; exit 1; }
fi
bash -c "$script"
"""
_STUB_PATH = "/tmp/omelet-bootstrap.sh"

# The command reaches the guest as one `bash -lc` argument through wsl.exe or
# ssh, where a space or quote would be parsed again.
_SHELL_SAFE = re.compile(r"[A-Za-z0-9._:/@+=-]+")


def _shell_safe(value: str, name: str) -> str:
    if not _SHELL_SAFE.fullmatch(value):
        raise BootstrapError(
            f"{name} contains characters the VM command line cannot carry: {value!r}")
    return value


def _run(provider, argv, *, step: str):
    result = provider.exec(argv, root=True)
    if not result.ok:
        detail = (result.stderr or result.stdout).strip()
        raise BootstrapError(
            f"{step} failed inside the VM (exit {result.returncode})"
            + (f":\n{detail}" if detail else "."))
    return result


def _installed(provider) -> bool:
    return provider.exec(["test", "-s", constants.ENGINE_MARKER], root=True).ok


def bootstrap(provider, *, source: str | None = None, repair: bool = False) -> None:
    """Install the engine in the VM unless it is already there.

    `repair` reinstalls regardless and tells the installer to keep the ref it
    has and recreate the agent container.
    """
    if not repair and _installed(provider):
        return
    url = _shell_safe(
        source or os.environ.get("OMELET_ENGINE_URL") or constants.ENGINE_URL,
        "OMELET_ENGINE_URL")
    assignments = []
    ref = os.environ.get("OMELET_ENGINE_REF")
    if ref:
        assignments.append(f"OMELET_ENGINE_REF={_shell_safe(ref, 'OMELET_ENGINE_REF')}")
    if repair:
        assignments.append("OMELET_ENGINE_REPAIR=1")
    encoded = base64.b64encode(_STUB.encode()).decode("ascii")
    command = " ".join([*assignments, "bash", _STUB_PATH, url])
    _run(provider,
         ["bash", "-lc", f"echo {encoded} | base64 -d > {_STUB_PATH} && {command}"],
         step="installing Omelet inside the VM")
    # The installer writes the marker last, so its absence means it stopped
    # early without a non-zero status reaching us.
    if not _installed(provider):
        raise BootstrapError(
            "the Omelet installer reported success but left no "
            f"{constants.ENGINE_MARKER}")
```

- [ ] **Step 5: Run the bootstrap tests**

Run: `python3 -m pytest tests/host/test_bootstrap.py -q`
Expected: PASS (10 tests).

- [ ] **Step 6: Point `install.py` at the new signature**

In `host/core/install.py`, in `default_steps`, replace
```python
        step("agent", lambda: agent_version_step(
            provider, repair=lambda: _bootstrap(provider, force=True),
            reconnect=lambda: _reconnect(provider)),
            always_run=True),
```
with
```python
        step("agent", lambda: agent_version_step(
            provider, repair=lambda: _bootstrap(provider, repair=True),
            reconnect=lambda: _reconnect(provider)),
            always_run=True),
```

Replace `_bootstrap` and `_reconnect` at the end of the file with:
```python
def _bootstrap(provider, *, repair: bool = False) -> None:
    from .bootstrap import bootstrap
    bootstrap(provider, repair=repair)


def _reconnect(provider):
    """Reinstall in repair mode, which recreates the agent container so it
    re-reads its token, then dial it with the token the VM holds now (the
    client caches the one it was built with)."""
    from host.client import AgentClient

    _bootstrap(provider, repair=True)
    return AgentClient.for_provider(provider)
```

- [ ] **Step 7: Update `host/cli.py`**

In `vm_create`, replace the docstring and the two messages:
```python
    """Create the VM and install Omelet inside it."""
```
```python
            typer.echo("VM already exists; checking the Omelet install.")
```
```python
        typer.echo("Installing Omelet in the VM (a few minutes)…")
```
(The `typer.echo("\nBootstrap failed.\n{e}")` line stays.)

In `selfcheck`, replace everything from `from host.core import bootstrap as _bootstrap` through the closing `]` of `checks += [...]` with:
```python
    from host.core.install import VERIFY_TEMPLATE
    import host.providers as _providers

    # Resolved from the modules that read them, not from this file: cli.py is
    # the frozen entry script, whose __file__ sits at the bundle root.
    checks = [
        ("host/provision/nginx-hello/docker-compose.yml",
         VERIFY_TEMPLATE / "docker-compose.yml"),
        ("host/providers/omelet.yaml", Path(_providers.__file__).parent / "omelet.yaml"),
    ]
```

- [ ] **Step 8: Shrink the PyInstaller `datas`**

In `packaging/windows/omelet.spec`, replace the `datas=[...]` list with:
```python
    datas=[
        ("../../host/provision/nginx-hello/docker-compose.yml",
         "host/provision/nginx-hello"),
        ("../../host/providers/omelet.yaml", "host/providers"),
    ],
```
and replace the comment under it with:
```python
    # Nothing from agent/ or engine/ is bundled: the VM pulls the image and
    # fetches the engine itself, and tests/host/test_frozen_bundle.py fails if
    # an entry reappears. Every dest mirrors the repo path its reader resolves
    # from __file__, so the bundle and a source checkout look identical.
```

- [ ] **Step 9: Update the tests that depended on the old bootstrap**

`tests/host/test_frozen_bundle.py` — replace the two tests that mention agent and `guest_assets`:
```python
def test_the_frozen_binary_bundles_nothing_from_the_agent_or_the_engine():
    entries = _datas()
    assert entries, "the spec bundles no assets at all -- this test guards nothing"
    offenders = [src for src, dest in entries
                 if "agent/" in src or "engine/" in src
                 or dest.startswith(("agent", "engine"))]
    assert not offenders, (
        f"the frozen host binary bundles VM-side files: {offenders}. The VM "
        "pulls the agent image and fetches the engine itself; a copy in the "
        "host would need a desktop release to change.")


def test_every_bundled_source_exists_and_lands_where_its_reader_looks():
    from host.core.install import VERIFY_TEMPLATE

    entries = _datas()
    for source, _dest in entries:
        assert (SPEC.parent / source).resolve().is_file(), f"{source} does not exist"

    dests = {dest for _src, dest in entries}
    expected = {VERIFY_TEMPLATE.relative_to(ROOT).as_posix()}
    missing = expected - dests
    assert not missing, f"assets the host reads at runtime are not bundled: {missing}"
```

`tests/host/test_exec_callers.py` — replace `ALLOWED_CALLERS`:
```python
ALLOWED_CALLERS = {
    ("core/bootstrap.py", "_run"),
    ("core/bootstrap.py", "_installed"),
    ("client.py", "read_token"),
}
```

`tests/test_vm_cli.py` — in `FakeProvider.exec`, replace the marker branch:
```python
    def exec(self, argv, *, root=False):
        self.execs.append(argv)
        # the engine marker exists, so bootstrap is a no-op
        if argv[:2] == ["test", "-s"]:
            return Completed(0, "", "")
        return Completed(0, "", "")
```
and in `FailingProvider.exec`:
```python
        def exec(self, argv, *, root=False):
            self.execs.append(argv)
            if argv[:2] == ["test", "-s"]:
                return Completed(1, "", "")          # not installed yet
            return Completed(1, "", "E: Unable to locate package docker-ce")
```

`tests/test_setup_cli.py` — in `test_selfcheck_reports_ok_for_every_bundled_asset`, replace the names tuple:
```python
    for name in ("docker-compose.yml", "omelet.yaml"):
```

`tests/test_constants_agree.py` — replace `test_bootstrap_constants_live_only_on_the_host`:
```python
def test_the_engine_entrypoint_constants_live_only_on_the_host():
    # The agent has no use for either, and must not become a second source of
    # truth for where the engine comes from.
    for name in ("ENGINE_URL", "ENGINE_MARKER"):
        assert not hasattr(agent_constants, name), f"{name} must not live in agent/core/constants.py"
        assert hasattr(host_constants, name), f"{name} must live in host/core/constants.py"
```

`tests/host/test_bootstrap_shell.py` — the script still writes the old marker until Task 5, so give the test file its own literals. After `BOOTSTRAP = ROOT / "host" / "provision" / "bootstrap.sh"` add:
```python
MARKER = f"{constants.GUEST_ROOT}/.bootstrapped"
STACK = f"{constants.GUEST_ROOT}/stack.yml"
```
Then replace every `constants.BOOTSTRAP_MARKER` with `MARKER` and every `constants.GUEST_STACK` with `STACK` (4 occurrences), and delete the whole `test_bootstrap_installs_the_agent_files_where_the_host_pushes_them` test (its `guest_assets()` no longer exists).

- [ ] **Step 10: Run the full suite**

Run: `python3 -m pytest -q`
Expected: all pass. If anything still imports `read_marker`, `restart_agent`, `guest_assets`, `BOOTSTRAP_VERSION`, `BOOTSTRAP_MARKER` or `GUEST_STACK` from the host, `grep -rn` for the name under `host/` and `tests/` and update it the same way.

- [ ] **Step 11: Commit**

```bash
git add host/core/constants.py host/core/bootstrap.py host/core/install.py host/cli.py \
  packaging/windows/omelet.spec tests/host/test_bootstrap.py tests/host/test_bootstrap_shell.py \
  tests/host/test_frozen_bundle.py tests/host/test_exec_callers.py tests/test_vm_cli.py \
  tests/test_setup_cli.py tests/test_constants_agree.py
git commit -m "feat: host bootstrap runs the engine entrypoint instead of pushing guest files"
```

---

### Task 2: Connect step checks the API number and repairs a token refusal

**Files:**
- Modify: `agent/core/constants.py`, `agent/api/app.py`
- Modify: `host/core/constants.py`, `host/core/install.py`, `host/setup_app/app.py`
- Delete: `tests/host/test_agent_version.py`
- Create: `tests/host/test_connect_step.py`
- Modify: `tests/host/test_client_seam.py`, `tests/host/test_default_steps.py`, `tests/test_setup_cli.py`, `tests/test_constants_agree.py`

**Interfaces:**
- Consumes: `host.core.bootstrap.bootstrap(provider, *, repair=...)` (Task 1); `AgentClient.health() -> dict`, `AgentClient.version() -> str`.
- Produces: `host.core.install.connect_step(provider, *, client=None, reconnect=None, sleep=time.sleep) -> str | None`; `host.core.install.AgentIncompatible`; `host.core.install.AgentNotAccepted` (kept); step name `"connect"`; `host.core.constants.SUPPORTED_API: frozenset[int]`; `agent.core.constants.API_VERSION: int`; `/health` JSON gains `"api"`.

- [ ] **Step 1: Write the failing tests**

Delete `tests/host/test_agent_version.py`:
```bash
git rm tests/host/test_agent_version.py
```

Create `tests/host/test_connect_step.py`:
```python
"""The host's check that the agent in the VM speaks its API and accepts its
token -- turned into one sentence before the slow smoke test."""
from __future__ import annotations

import pytest

from host.client import AgentError, AgentUnavailableError
from host.core import constants
from host.core.install import AgentIncompatible, AgentNotAccepted, connect_step


class FakeClient:
    """`health` is the /health body. `versions` answers /version one call at a
    time, the last repeating; an exception entry is raised instead."""

    def __init__(self, *versions, health=None):
        self._health = {"status": "ok", "api": 1} if health is None else health
        self._versions = list(versions) or ["0.1.0"]

    def health(self) -> dict:
        return self._health

    def version(self) -> str:
        answer = (self._versions.pop(0) if len(self._versions) > 1
                  else self._versions[0])
        if isinstance(answer, BaseException):
            raise answer
        return answer


def _refused(code: str) -> AgentError:
    return AgentError(code, "missing or invalid bearer token", 401)


def test_an_agent_on_another_api_is_reported_and_never_repaired():
    unsupported = max(constants.SUPPORTED_API) + 1
    reconnects = []
    with pytest.raises(AgentIncompatible) as excinfo:
        connect_step(None, client=FakeClient(health={"status": "ok", "api": unsupported}),
                     reconnect=lambda: reconnects.append("reconnect"))
    assert str(unsupported) in str(excinfo.value), "support needs the number"
    assert reconnects == [], "reinstalling the same engine cannot change its API"


def test_an_agent_from_before_the_api_number_counts_as_api_1():
    assert connect_step(None, client=FakeClient(health={"status": "ok"})) is None


def test_a_matching_agent_that_accepts_this_host_is_left_alone():
    reconnects = []
    assert connect_step(None, client=FakeClient("0.1.0"),
                        reconnect=lambda: reconnects.append("reconnect")) is None
    assert reconnects == []


def test_an_agent_refusing_this_host_is_reconnected_once():
    # /health skips the token check, so restart:always never restarts an agent
    # that refuses every other route; only a repair recreates it.
    reconnects = []

    def reconnect():
        reconnects.append("reconnect")
        return FakeClient("0.1.0")

    message = connect_step(None, client=FakeClient(_refused("agent_unconfigured")),
                           reconnect=reconnect)
    assert reconnects == ["reconnect"]
    assert "reconnected" in message


def test_an_agent_still_refusing_after_the_reconnect_says_what_was_wrong():
    with pytest.raises(AgentNotAccepted) as excinfo:
        connect_step(None, client=FakeClient(_refused("unauthorized")),
                     reconnect=lambda: FakeClient(_refused("unauthorized")))
    assert "did not accept this computer" in str(excinfo.value)


def test_an_error_that_is_not_about_the_token_is_not_repaired():
    reconnects = []
    with pytest.raises(AgentError):
        connect_step(None, client=FakeClient(AgentError("internal", "boom", 500)),
                     reconnect=lambda: reconnects.append("reconnect"))
    assert reconnects == []


def test_the_agent_restarting_after_the_reconnect_is_waited_out():
    # The recreated container is not serving yet when the repair returns.
    slept = []
    restarted = FakeClient(AgentUnavailableError("connection refused"), "0.1.0")
    message = connect_step(None, client=FakeClient(_refused("unauthorized")),
                           reconnect=lambda: restarted, sleep=slept.append)
    assert slept, "the restart must be waited out, not reported as a failure"
    assert "reconnected" in message
```

Append to `tests/host/test_client_seam.py`:
```python
def test_the_connect_step_accepts_the_real_agent(seam):
    # Pins the contract: /health carries an api number this host speaks, and
    # /version accepts the host's token.
    from host.core import constants
    from host.core.install import connect_step

    client, _runner, _root = seam
    assert client.health()["api"] in constants.SUPPORTED_API
    assert connect_step(None, client=client) is None
```

In `tests/test_constants_agree.py`, delete `test_agent_image_matches_the_stack_files_default` and `test_the_agent_version_the_host_expects_is_the_one_the_image_reports`, and add:
```python
def test_the_stack_deploys_the_image_version_the_agent_reports():
    # Three files name this version and are bumped together on an engine
    # release: the stack's image tag (what the VM pulls), the Dockerfile's
    # AGENT_VERSION (GET /version's answer) and the package __version__.
    import re
    from pathlib import Path

    from agent import __version__ as package_version

    root = Path(__file__).resolve().parent.parent
    stack = re.search(r"\$\{OMELET_AGENT_IMAGE:-[^}]+:([^}:]+)\}",
                      (root / "host" / "provision" / "stack.yml").read_text())
    dockerfile = re.search(r"^ARG AGENT_VERSION=(\S+)",
                           (root / "agent" / "Dockerfile").read_text(), re.M)
    assert stack, "stack.yml must default OMELET_AGENT_IMAGE with a tag"
    assert dockerfile, "the Dockerfile must default AGENT_VERSION"
    assert stack[1] == dockerfile[1] == package_version


def test_the_host_speaks_the_api_the_agent_serves():
    assert agent_constants.API_VERSION in host_constants.SUPPORTED_API
```

In `tests/host/test_default_steps.py`:
- in `test_step_names_and_order_match_the_spec` replace `"agent"` with `"connect"` in the expected list;
- replace every patch key `"agent":` with `"connect":` and every `ran.append("agent")` with `ran.append("connect")`, and the two expected lists `["agent", "verify", "finish"]` with `["connect", "verify", "finish"]`.

In `tests/test_setup_cli.py`, replace
```python
    monkeypatch.setattr(install_mod, "agent_version_step", lambda *a, **k: None)
```
with
```python
    monkeypatch.setattr(install_mod, "connect_step", lambda *a, **k: None)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/host/test_connect_step.py tests/host/test_client_seam.py tests/test_constants_agree.py tests/host/test_default_steps.py -q`
Expected: FAIL — `ImportError: cannot import name 'AgentIncompatible'`, `AttributeError: ... 'API_VERSION'`, step list mismatch.

- [ ] **Step 3: Agent API number**

Append to `agent/core/constants.py`:
```python
# Bumped only when a route the host calls changes incompatibly; the host
# refuses an agent whose number it does not list in SUPPORTED_API.
API_VERSION = 1
```

In `agent/api/app.py`, in `health()`, add the field after `"version": config.version,`:
```python
            "api": constants.API_VERSION,
```

- [ ] **Step 4: Host constants**

In `host/core/constants.py`, replace the block from `# Must match stack.yml's OMELET_AGENT_IMAGE default;` through `EXPECTED_AGENT_VERSION = AGENT_IMAGE.rsplit(":", 1)[-1]` with:
```python
# The agent API numbers this host can drive. An engine release that keeps the
# routes compatible keeps the number, so it never needs a host release.
SUPPORTED_API = frozenset({1})
```

- [ ] **Step 5: Replace `agent_version_step` with `connect_step`**

In `host/core/install.py`:
- delete `import re`;
- delete `class AgentTooOld`, `_version_tuple`, `_is_current`, `_version_once_serving` and `agent_version_step`;
- keep `AgentNotAccepted`, `_TOKEN_CODES` and `AGENT_RESTART_TIMEOUT` (update its comment as below);
- add after `_TOKEN_CODES`:

```python
class AgentIncompatible(RuntimeError):
    """The agent serves an API number this host does not speak."""


# `docker compose up -d` returns before the agent container is serving, so the
# first call after an install or a repair legitimately answers "connection
# refused".
AGENT_RESTART_TIMEOUT = 30.0


def _once_serving(call, sleep, timeout: float):
    from host.client import AgentUnavailableError
    deadline = time.monotonic() + timeout
    while True:
        try:
            return call()
        except AgentUnavailableError:
            if time.monotonic() >= deadline:
                raise
        sleep(1.0)


def connect_step(provider, *, client=None, reconnect=None, sleep=time.sleep):
    """Check the agent speaks this host's API and accepts this host's token.

    `reconnect` reinstalls the engine in repair mode -- recreating the agent so
    it re-reads its token -- and returns a client holding the token the VM has
    now. Returning None keeps the current client.
    """
    from host.client import AgentClient, AgentError
    from host.core import constants

    client = client or AgentClient.for_provider(provider)
    # Agents from before the field serve exactly the api 1 routes.
    api = _once_serving(client.health, sleep, AGENT_RESTART_TIMEOUT).get("api", 1)
    if api not in constants.SUPPORTED_API:
        supported = ", ".join(str(n) for n in sorted(constants.SUPPORTED_API))
        raise AgentIncompatible(
            "This app and the Omelet service inside the virtual machine are "
            "versions that cannot work together.\n"
            f"service API {api}, app supports {supported}")
    # /health skips the token check; /version is the cheapest route that does not.
    try:
        client.version()
    except AgentError as e:
        if e.code not in _TOKEN_CODES or reconnect is None:
            raise
        client = reconnect() or client
        try:
            _once_serving(client.version, sleep, AGENT_RESTART_TIMEOUT)
        except AgentError as again:
            if again.code not in _TOKEN_CODES:
                raise
            raise AgentNotAccepted(
                "The Omelet service inside the virtual machine did not accept "
                "this computer, and setting the virtual machine up again did "
                f"not change that.\n{again.message}") from again
        return ("The Omelet service in the virtual machine was not accepting "
                "this computer, and has been reconnected.")
    return None
```

In `default_steps`, replace the `agent` step and its comment with:
```python
        # Before verify: a mismatched or refusing agent is one sentence here,
        # not a 404 or 401 minutes into the smoke test.
        step("connect", lambda: connect_step(
            provider, reconnect=lambda: _reconnect(provider)),
            always_run=True),
```

In `_ACTIONS`, replace the `"agent"` entry with:
```python
    "connect": "The Omelet service inside the virtual machine would not work "
               "with this app, or would not accept this computer — the "
               "message above says which. Run setup again; if it fails the "
               "same way twice, use Copy diagnostics and send us the text.",
```
and the `"bootstrap"` entry with:
```python
    "bootstrap": "Omelet could not be installed inside the virtual machine. The "
                 "detail above comes from inside the VM. Run setup again; if it "
                 "fails the same way twice, send us that text.",
```

- [ ] **Step 6: Setup window label**

In `host/setup_app/app.py`, replace the `"bootstrap"` and `"agent"` label entries (and the comment above `"agent"`) with:
```python
    "bootstrap": "Installing Omelet",
    "connect": "Connecting to the Omelet service",
```

- [ ] **Step 7: Run the full suite**

Run: `python3 -m pytest -q`
Expected: all pass. `grep -rn "agent_version_step\|AgentTooOld\|EXPECTED_AGENT_VERSION\|AGENT_IMAGE" host tests` returns nothing.

- [ ] **Step 8: Commit**

```bash
git add agent/core/constants.py agent/api/app.py host/core/constants.py host/core/install.py \
  host/setup_app/app.py tests/host/test_connect_step.py tests/host/test_client_seam.py \
  tests/host/test_default_steps.py tests/test_setup_cli.py tests/test_constants_agree.py
git commit -m "feat: connect step checks the agent API number instead of its version"
```

---

### Task 3: Move every guest asset into `engine/`

**Files:**
- Move: `host/provision/{bootstrap.sh → ../../engine/install.sh, stack.yml, stack.debug.yml, guest/omelet.py → cli/omelet.py, agents/omelet.md → instructions/omelet.md, agents/skills → skills, install-agents.sh → lib/, login-users.sh → lib/}`
- Modify: `engine/install.sh` (read files from its own directory), `engine/lib/install-agents.sh` (instructions path)
- Move: `tests/guest/ → tests/engine/cli/`; `tests/host/test_bootstrap_shell.py → tests/engine/test_install_shell.py`; `tests/host/test_install_agents.py`, `tests/host/test_login_users.py`, `tests/host/test_stack_yml.py → tests/engine/`
- Create: `tests/engine/__init__.py`, `tests/host/test_no_guest_assets.py`
- Modify: `tests/test_constants_agree.py`, `tests/host/test_no_dead_modules.py`, `tests/host/test_verify_step.py`, `.gitattributes`

**Interfaces:**
- Consumes: nothing new.
- Produces: `engine/install.sh` resolves siblings via `ENGINE_DIR`; `engine/lib/install-agents.sh <engine-dir> <home> <uid:gid>` reads `<engine-dir>/instructions/omelet.md` and `<engine-dir>/skills/omelet-setup`; test loader `tests.engine.cli.loader.load()` / `GUEST_CLI`.

- [ ] **Step 1: Write the failing boundary test**

Create `tests/host/test_no_guest_assets.py`:
```python
"""The host carries nothing that runs inside the VM: a guest script or skill
under host/ would need a desktop release to change."""
from pathlib import Path

PROVISION = Path(__file__).resolve().parents[2] / "host" / "provision"
# The installer's smoke-test project is the host's own check, sent over HTTP.
HOST_OWNED = PROVISION / "nginx-hello"
GUEST_SHAPED = {".sh", ".py", ".md", ".yml", ".yaml", ".json"}


def test_host_provision_holds_only_the_smoke_test_project():
    assert (HOST_OWNED / "docker-compose.yml").is_file(), f"scanned the wrong tree: {PROVISION}"
    strays = sorted(p.relative_to(PROVISION).as_posix()
                    for p in PROVISION.rglob("*")
                    if p.is_file() and p.suffix in GUEST_SHAPED
                    and HOST_OWNED not in p.parents)
    assert not strays, f"guest assets under host/provision belong in engine/: {strays}"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m pytest tests/host/test_no_guest_assets.py -q`
Expected: FAIL listing `bootstrap.sh`, `stack.yml`, `guest/omelet.py`, …

- [ ] **Step 3: Move the files**

```bash
mkdir -p engine/cli engine/instructions engine/lib
git mv host/provision/bootstrap.sh engine/install.sh
git mv host/provision/stack.yml engine/stack.yml
git mv host/provision/stack.debug.yml engine/stack.debug.yml
git mv host/provision/guest/omelet.py engine/cli/omelet.py
git mv host/provision/agents/omelet.md engine/instructions/omelet.md
git mv host/provision/agents/skills engine/skills
git mv host/provision/install-agents.sh engine/lib/install-agents.sh
git mv host/provision/login-users.sh engine/lib/login-users.sh
rm -rf host/provision/guest host/provision/agents
mkdir -p tests/engine
touch tests/engine/__init__.py
git mv tests/guest tests/engine/cli
git mv tests/host/test_bootstrap_shell.py tests/engine/test_install_shell.py
git mv tests/host/test_install_agents.py tests/engine/test_install_agents.py
git mv tests/host/test_login_users.py tests/engine/test_login_users.py
git mv tests/host/test_stack_yml.py tests/engine/test_stack_yml.py
```
(`rm -rf host/provision/guest` only removes a leftover `__pycache__`; `host/provision/Untitled` is not touched.)

- [ ] **Step 4: Make `install.sh` read from its own directory**

The host no longer pushes anything, so `engine/install.sh` must find its siblings next to itself. In `engine/install.sh`:

Replace the header comment (lines 2–3) with:
```bash
# Installs the engine into this VM. Run as root by get.sh from the unpacked
# engine directory; re-running it is safe.
```

After `set -euo pipefail` add:
```bash
ENGINE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
```

Replace step 7's
```bash
test -f /opt/omelet/stack.yml || { echo 'stack.yml was never pushed to the VM' >&2; exit 1; }
```
with
```bash
install -m 644 "$ENGINE_DIR/stack.yml" /opt/omelet/stack.yml
```

In step 8 replace the four asset paths:
```bash
install -m 755 "$ENGINE_DIR/cli/omelet.py" /usr/local/bin/omelet
```
```bash
install -m 644 "$ENGINE_DIR/instructions/omelet.md" /etc/claude-code/CLAUDE.md
rm -rf /etc/codex/skills/omelet-setup
cp -r "$ENGINE_DIR/skills/omelet-setup" /etc/codex/skills/
```
```bash
bash "$ENGINE_DIR/lib/install-agents.sh" "$ENGINE_DIR" /root 0:0
bash "$ENGINE_DIR/lib/install-agents.sh" "$ENGINE_DIR" /etc/skel 0:0
while IFS=: read -r name uid gid home; do
  usermod -aG docker "$name"
  bash "$ENGINE_DIR/lib/install-agents.sh" "$ENGINE_DIR" "$home" "$uid:$gid"
done < <(getent passwd | bash "$ENGINE_DIR/lib/login-users.sh" /etc/shells)
```

In `engine/lib/install-agents.sh`, update the usage comment to `install-agents.sh <engine-dir> <home> <owner uid:gid>` and the Codex block line:
```bash
{ echo "$BEGIN"; cat "$SRC/instructions/omelet.md"; echo "$END"; } >> "$AGENTS_MD"
```

- [ ] **Step 5: Repoint the moved tests**

- `tests/engine/cli/loader.py`: `GUEST_CLI = (Path(__file__).resolve().parents[3] / "engine" / "cli" / "omelet.py")` and the docstring's "copied into the VM" wording stays.
- Every `from tests.guest.loader import` → `from tests.engine.cli.loader import` (files: `tests/engine/cli/conftest.py`, `tests/engine/cli/test_boundaries.py`, any other `tests/engine/cli/test_*.py`, `tests/test_constants_agree.py`). Find them with `grep -rln "tests.guest" tests`.
- `tests/engine/test_install_shell.py`: `BOOTSTRAP = ROOT / "engine" / "install.sh"`; delete `test_smoke_test_template_publishes_no_host_port` from this file.
- Append that test, unchanged except its path, to `tests/host/test_verify_step.py`:
```python
def test_smoke_test_template_publishes_no_host_port():
    # The template reaches the browser through Traefik on the edge network, so a
    # published port buys nothing and collides: the first real Windows run died
    # with "failed to bind host port 0.0.0.0:8080/tcp: address already in use".
    import yaml
    from host.core.install import VERIFY_TEMPLATE

    compose = yaml.safe_load((VERIFY_TEMPLATE / "docker-compose.yml").read_text())
    for name, svc in compose["services"].items():
        assert not svc.get("ports"), f"{name} publishes a host port"
```
- `tests/engine/test_install_agents.py`: `SCRIPT = ROOT / "engine" / "lib" / "install-agents.sh"`, `SOURCE = ROOT / "engine"`, and in `test_the_codex_block_is_replaced_and_the_users_own_text_kept` write the new text to `source / "instructions" / "omelet.md"`.
- `tests/engine/test_login_users.py`: `SCRIPT = ROOT / "engine" / "lib" / "login-users.sh"`.
- `tests/engine/test_stack_yml.py`: `STACK = Path(__file__).resolve().parents[2] / "engine" / "stack.yml"`.
- `tests/test_constants_agree.py`: in `test_the_stack_deploys_the_image_version_the_agent_reports`, read `root / "engine" / "stack.yml"`.
- `tests/host/test_no_dead_modules.py`: delete the `PROVISION = HOST / "provision"` line and its comment, and change the file list to `files = sorted(HOST.rglob("*.py"))`.

`ROOT` in files now at `tests/engine/` is still `parents[2]` (tests/engine/x.py → repo root). Check each moved file's `ROOT`/`parents[...]` resolves to the repo root; `tests/engine/cli/` files need `parents[3]`.

- [ ] **Step 6: Line endings**

In `.gitattributes`, below `host/provision/** text eol=lf`, add:
```
engine/** text eol=lf
```
and change the comment's `bootstrap.sh` to `install.sh`.

- [ ] **Step 7: Run the full suite**

Run: `python3 -m pytest -q`
Expected: all pass, including `tests/host/test_no_guest_assets.py`.

- [ ] **Step 8: Commit**

```bash
git add -u host/provision tests .gitattributes
git add engine tests/engine tests/host/test_no_guest_assets.py tests/host/test_verify_step.py \
  tests/host/test_no_dead_modules.py tests/test_constants_agree.py
git status --short   # host/provision/Untitled must still be untracked, task.md still unstaged
git commit -m "refactor: move every guest asset from host/provision into engine/"
```

---

### Task 4: `engine/get.sh` resolves a ref, downloads it and runs its installer

**Files:**
- Create: `engine/get.sh`
- Create: `tests/engine/test_get_sh.py`

**Interfaces:**
- Consumes: `engine/install.sh <ref> [--repair]` (Task 5 gives it those arguments; until then extra arguments are ignored).
- Produces: `get.sh` environment contract — `OMELET_ENGINE_REPO` (default repository URL), `OMELET_ENGINE_REF`, `OMELET_ENGINE_REPAIR=1`; shell function `resolve_ref <repo> <marker-path>` printing one ref; unpacked engine at `/opt/omelet/engine/`.

- [ ] **Step 1: Write the failing tests**

Create `tests/engine/test_get_sh.py`:
```python
"""get.sh's choice of which engine to install, and that it actually runs when
fetched the ways it is fetched. The download and apt steps need a network and
are covered by the live-VM acceptance run."""
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
GET = ROOT / "engine" / "get.sh"
REPO = "https://example.invalid/omelet"


def _bin(tmp_path: Path, **scripts: str) -> dict:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    for name, body in scripts.items():
        path = bin_dir / name
        path.write_text("#!/bin/sh\n" + body)
        path.chmod(0o755)
    return {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}


def _git_listing(tmp_path: Path, tags, code: int = 0) -> str:
    listing = tmp_path / "tags.txt"
    listing.write_text("".join(f"{'0' * 40}\trefs/tags/{t}\n" for t in tags))
    return f"cat '{listing}'\nexit {code}\n"


def _resolve(tmp_path, *, tags=(), git_code=0, installed="", **env):
    marker = tmp_path / "engine.version"
    if installed:
        marker.write_text(installed + "\n")
    environ = _bin(tmp_path, git=_git_listing(tmp_path, tags, git_code))
    for name in ("OMELET_ENGINE_REF", "OMELET_ENGINE_REPAIR"):
        environ.pop(name, None)
    environ.update(env)
    return subprocess.run(
        ["bash", "-c", f'source "{GET}" && resolve_ref "{REPO}" "{marker}"'],
        env=environ, capture_output=True, text=True)


def test_get_sh_is_valid_bash():
    result = subprocess.run(["bash", "-n", str(GET)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_the_highest_engine_tag_wins_by_version_not_by_text(tmp_path):
    result = _resolve(tmp_path, tags=["engine-v0.9.0", "engine-v0.10.0", "engine-v0.2.1"])
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "engine-v0.10.0"


def test_an_explicit_ref_wins_over_every_tag(tmp_path):
    result = _resolve(tmp_path, tags=["engine-v0.3.0"], OMELET_ENGINE_REF="feature/x")
    assert result.stdout.strip() == "feature/x"


def test_a_repair_keeps_the_installed_ref_instead_of_upgrading(tmp_path):
    result = _resolve(tmp_path, tags=["engine-v0.3.0"], installed="engine-v0.2.0",
                      OMELET_ENGINE_REPAIR="1")
    assert result.stdout.strip() == "engine-v0.2.0"


def test_a_repair_with_nothing_installed_installs_the_latest(tmp_path):
    result = _resolve(tmp_path, tags=["engine-v0.3.0"], OMELET_ENGINE_REPAIR="1")
    assert result.stdout.strip() == "engine-v0.3.0"


def test_a_repository_without_engine_tags_is_a_plain_failure(tmp_path):
    result = _resolve(tmp_path, tags=[])
    assert result.returncode != 0
    assert "no engine-v" in result.stderr


def test_an_unreachable_repository_is_a_plain_failure(tmp_path):
    result = _resolve(tmp_path, tags=[], git_code=128)
    assert result.returncode != 0
    assert "could not reach" in result.stderr


@pytest.mark.parametrize("how", ["bash -c", "stdin"])
def test_fetching_the_script_runs_the_install_not_just_its_functions(tmp_path, how):
    # The host runs it with `bash -c "$script"`, a cloud VM with `curl | bash`;
    # a sourcing guard that misfires there would define functions and exit 0.
    environ = _bin(tmp_path, dpkg="exit 0\n", curl="exit 22\n",
                   git=_git_listing(tmp_path, ["engine-v0.1.0"]))
    for name in ("OMELET_ENGINE_REF", "OMELET_ENGINE_REPAIR"):
        environ.pop(name, None)
    script = GET.read_text()
    if how == "bash -c":
        result = subprocess.run(["bash", "-c", script], env=environ,
                                capture_output=True, text=True)
    else:
        result = subprocess.run(["bash"], input=script, env=environ,
                                capture_output=True, text=True)
    assert result.returncode != 0
    assert "could not download Omelet engine engine-v0.1.0" in result.stderr
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/engine/test_get_sh.py -q`
Expected: FAIL — `bash: .../engine/get.sh: No such file or directory`.

- [ ] **Step 3: Write `engine/get.sh`**

```bash
#!/usr/bin/env bash
# The engine's entrypoint: choose an engine ref, unpack that ref's engine/ into
# /opt/omelet/engine and run its install.sh. Fetched on its own (the host's
# bootstrap, or `curl -fsSL <url> | sudo bash` on a cloud VM), so it can rely
# on nothing beside it. Run as root.
set -euo pipefail

REPO="${OMELET_ENGINE_REPO:-https://github.com/omelet-app/omelet}"
MARKER=/opt/omelet/engine.version
ENGINE_DIR=/opt/omelet/engine

# resolve_ref <repo> <marker>: an explicit ref wins, a repair keeps what is
# installed, anything else takes the highest engine-v* tag.
resolve_ref() {
  local repo=$1 marker=$2 tags latest
  if [[ -n "${OMELET_ENGINE_REF:-}" ]]; then
    echo "$OMELET_ENGINE_REF"
    return
  fi
  if [[ "${OMELET_ENGINE_REPAIR:-}" == 1 && -s "$marker" ]]; then
    cat "$marker"
    return
  fi
  if ! tags="$(git ls-remote --tags --refs "$repo" 'engine-v*')"; then
    echo "could not reach $repo to find the latest Omelet engine" >&2
    return 1
  fi
  latest="$(sed -n 's#.*refs/tags/##p' <<<"$tags" | sort -V | tail -n 1)"
  if [[ -z "$latest" ]]; then
    echo "$repo has no engine-v* release to install" >&2
    return 1
  fi
  echo "$latest"
}

main() {
  export DEBIAN_FRONTEND=noninteractive
  local missing=() pkg
  for pkg in ca-certificates curl git; do
    dpkg -s "$pkg" >/dev/null 2>&1 || missing+=("$pkg")
  done
  if (( ${#missing[@]} )); then
    apt-get update
    apt-get install -y "${missing[@]}"
  fi

  local ref tmp
  ref="$(resolve_ref "$REPO" "$MARKER")"
  echo "installing Omelet engine $ref"

  tmp="$(mktemp -d)"
  trap 'rm -rf "$tmp"' EXIT
  if ! curl -fsSL "$REPO/archive/$ref.tar.gz" -o "$tmp/engine.tar.gz"; then
    echo "could not download Omelet engine $ref from $REPO" >&2
    exit 1
  fi
  tar -xzf "$tmp/engine.tar.gz" -C "$tmp" --strip-components=1 --wildcards '*/engine/'
  if [[ ! -f "$tmp/engine/install.sh" ]]; then
    echo "$ref of $REPO has no engine/install.sh" >&2
    exit 1
  fi
  # Replaced, not merged: a file dropped from the engine must not linger.
  mkdir -p /opt/omelet
  rm -rf "$ENGINE_DIR"
  mv "$tmp/engine" "$ENGINE_DIR"
  chmod 755 "$ENGINE_DIR"

  local args=("$ref")
  if [[ "${OMELET_ENGINE_REPAIR:-}" == 1 ]]; then
    args+=(--repair)
  fi
  bash "$ENGINE_DIR/install.sh" "${args[@]}"
}

# `return` only succeeds when sourced (the tests); under `bash -c` or a pipe it
# fails and the install runs.
(return 0 2>/dev/null) || main "$@"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/engine/test_get_sh.py -q`
Expected: PASS (9 tests).

- [ ] **Step 5: Commit**

```bash
git add engine/get.sh tests/engine/test_get_sh.py
git commit -m "feat: engine entrypoint resolves an engine-v tag and runs its installer"
```

---

### Task 5: `install.sh` installs the engine: ref marker, repair, Node 22, skills via `npx skills add`

**Files:**
- Modify: `engine/install.sh`
- Modify: `engine/lib/install-agents.sh`
- Modify: `tests/engine/test_install_shell.py`, `tests/engine/test_install_agents.py`

**Interfaces:**
- Consumes: `get.sh` calls `bash install.sh <ref> [--repair]` (Task 4); `host.core.constants.ENGINE_MARKER` (Task 1).
- Produces: `/opt/omelet/engine.version` containing `<ref>`, written last; skills at `~/.agents/skills/omelet-setup` (+ `~/.claude/skills/omelet-setup` symlink) for root and every login account.

- [ ] **Step 1: Write the failing tests**

In `tests/engine/test_install_shell.py`:

Delete the `MARKER = ...` line added in Task 1, and replace `test_bootstrap_writes_the_marker_path_the_host_reads_back` and `test_bootstrap_writes_the_marker_last` with:
```python
def test_install_writes_the_marker_path_the_host_checks():
    # The host treats a missing marker after a zero exit as a failed install;
    # two spellings would fail every install that actually worked.
    assert f"> {constants.ENGINE_MARKER}" in BOOTSTRAP.read_text()


def test_install_writes_the_marker_last():
    commands = _commands()
    marker = _index_of(f"> {constants.ENGINE_MARKER}")
    assert marker > _index_of('"$SKILLS_CLI" add')
    assert marker >= len(commands) - 2, "nothing that can fail may run after the marker"


def test_install_has_no_early_exit_of_its_own():
    # get.sh decides whether to install; the old marker's early `exit 0` left
    # here would turn every repair into a silent no-op.
    assert "exit 0" not in _commands()


def test_skills_come_from_the_unpacked_engine_through_a_pinned_cli():
    assert re.search(r"^SKILLS_CLI=skills@\d+\.\d+\.\d+$", BOOTSTRAP.read_text(), re.M), \
        "an unpinned skills CLI changes the install without a release"
    (add,) = [l for l in _commands() if '"$SKILLS_CLI" add' in l]
    assert '"$ENGINE_DIR/skills"' in add
    assert "github.com" not in add, "skills install from the same ref get.sh unpacked"


def test_npx_in_the_account_loop_cannot_swallow_the_account_list():
    # The loop reads accounts from stdin; a command inside it that reads stdin
    # consumes the remaining accounts, and only the first user gets skills.
    runuser = [l for l in _commands() if "runuser" in l]
    assert runuser, "skills are installed per account"
    assert all("</dev/null" in l for l in runuser)


def test_a_repair_or_a_new_token_recreates_the_agent():
    # The agent reads its token once at startup; `up -d` leaves it running.
    commands = _commands()
    recreate = _index_of("--force-recreate agent")
    assert recreate > _index_of(" up -d")
    condition = commands[recreate - 1]
    assert "TOKEN_CREATED" in condition and "REPAIR" in condition, condition
```

In `tests/engine/test_install_agents.py`, delete `test_every_agent_finds_the_skill_in_its_own_home_location` and add:
```python
def test_skills_are_left_to_npx(tmp_path):
    _install(tmp_path)
    assert not (tmp_path / ".claude" / "skills").exists()
    assert not (tmp_path / ".agents" / "skills").exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/engine/test_install_shell.py tests/engine/test_install_agents.py -q`
Expected: FAIL — marker path, `skills add`, `runuser`, `--force-recreate` not found; install-agents still creates skill dirs.

- [ ] **Step 3: Rewrite the top of `engine/install.sh`**

Replace everything from `MARKER=/opt/omelet/.bootstrapped` through the closing `fi` of the marker early-exit with:
```bash
REF="${1:?usage: install.sh <engine ref> [--repair]}"
REPAIR=0
if [[ "${2:-}" == --repair ]]; then
  REPAIR=1
fi
SKILLS_CLI=skills@1.5.26
# skills@1.5.26 declares node >=22.20.0; Ubuntu 24.04's own nodejs is 18.
NODE_MIN=22.20.0
TOKEN_CREATED=0
```

In step 6, inside `if [[ ! -s /opt/omelet/agent.token ]]; then`, add as the first line:
```bash
  TOKEN_CREATED=1
```

In step 7, after `/usr/bin/docker compose -f /opt/omelet/stack.yml up -d`, add:
```bash
# The agent reads its token once, at startup, and `up -d` leaves an unchanged
# container running.
if (( TOKEN_CREATED || REPAIR )); then
  /usr/bin/docker compose -f /opt/omelet/stack.yml up -d --force-recreate agent
fi
```

- [ ] **Step 4: Replace steps 8 and 9 of `engine/install.sh`**

Replace everything from `# 8. coding agents:` to the end of the file with:
```bash
# 8. git for `omelet clone`, Node for `npx skills`.
if ! dpkg -s git >/dev/null 2>&1; then
  apt-get update
  apt-get install -y git
fi
node_ok() {
  command -v node >/dev/null 2>&1 || return 1
  local have
  have="$(node -p 'process.versions.node')" || return 1
  [[ "$(printf '%s\n' "$NODE_MIN" "$have" | sort -V | head -n 1)" == "$NODE_MIN" ]]
}
if ! node_ok; then
  command -v gpg >/dev/null 2>&1 || { apt-get update; apt-get install -y gnupg; }
  install -m 0755 -d /etc/apt/keyrings
  if ! curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key \
      | gpg --dearmor --yes -o /etc/apt/keyrings/nodesource.gpg; then
    echo "could not reach NodeSource to install Node.js 22" >&2
    exit 1
  fi
  echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_22.x nodistro main" \
    > /etc/apt/sources.list.d/nodesource.list
  apt-get update
  apt-get install -y nodejs
  node_ok || { echo "Node.js $NODE_MIN or newer did not install" >&2; exit 1; }
fi

# 9. the in-VM omelet command and the instructions every session loads.
install -m 755 "$ENGINE_DIR/cli/omelet.py" /usr/local/bin/omelet
install -d /etc/claude-code
install -m 644 "$ENGINE_DIR/instructions/omelet.md" /etc/claude-code/CLAUDE.md

# 10. copies earlier provisioning made, which npx now owns or nothing reads.
rm -rf /etc/codex/skills/omelet-setup /opt/omelet/bin /opt/omelet/agents \
  /opt/omelet/.bootstrapped \
  /etc/skel/.claude/skills/omelet-setup /etc/skel/.agents/skills/omelet-setup

# 11. per account: docker group, Codex block, ~/projects, skills.
accounts() {
  echo "root:0:0:/root"
  getent passwd | bash "$ENGINE_DIR/lib/login-users.sh" /etc/shells
}
while IFS=: read -r name uid gid home; do
  if [[ "$name" != root ]]; then
    usermod -aG docker "$name"
  fi
  bash "$ENGINE_DIR/lib/install-agents.sh" "$ENGINE_DIR" "$home" "$uid:$gid"
  # npx symlinks ~/.claude/skills/<name>; a real directory left there by the
  # old copy-based install would block it.
  for old in "$home/.claude/skills/omelet-setup" "$home/.agents/skills/omelet-setup"; do
    if [[ -d "$old" && ! -L "$old" ]]; then
      rm -rf "$old"
    fi
  done
  # stdin is the account list this loop is reading.
  if ! runuser -u "$name" -- env HOME="$home" DISABLE_TELEMETRY=1 npx -y "$SKILLS_CLI" add "$ENGINE_DIR/skills" -s '*' -g -a claude-code codex -y </dev/null; then
    echo "could not install Omelet's skills for $name: the npm registry may be unreachable" >&2
    exit 1
  fi
done < <(accounts)

# 12. marker, last: a failure above must leave no marker behind.
echo "$REF" > /opt/omelet/engine.version
echo "Omelet engine $REF installed"
```

- [ ] **Step 5: Drop skill copying from `engine/lib/install-agents.sh`**

Replace the file with:
```bash
#!/usr/bin/env bash
# Writes Omelet's Codex instructions block and the ~/projects link into one
# home directory. Skills are installed separately, with npx.
# Run by install.sh as root, once per home:
#   install-agents.sh <engine-dir> <home> <owner uid:gid>
set -euo pipefail

SRC=$1
HOME_DIR=$2
OWNER=$3
BEGIN='<!-- omelet:begin -->'
END='<!-- omelet:end -->'
TARGET=/opt/omelet/projects

# Codex has no system-wide AGENTS.md, and the user may keep their own text in
# this one: only the block between the markers is ours to replace.
mkdir -p "$HOME_DIR/.codex"
AGENTS_MD="$HOME_DIR/.codex/AGENTS.md"
touch "$AGENTS_MD"
sed -i "\|^$BEGIN\$|,\|^$END\$|d" "$AGENTS_MD"
if [[ -s "$AGENTS_MD" && -n "$(tail -c1 "$AGENTS_MD")" ]]; then
  echo >> "$AGENTS_MD"
fi
{ echo "$BEGIN"; cat "$SRC/instructions/omelet.md"; echo "$END"; } >> "$AGENTS_MD"

if [[ ! -e "$HOME_DIR/projects" && ! -L "$HOME_DIR/projects" ]]; then
  ln -s "$TARGET" "$HOME_DIR/projects"
elif [[ -L "$HOME_DIR/projects" && "$(readlink "$HOME_DIR/projects")" == "$TARGET" ]]; then
  :
elif [[ -e "$HOME_DIR/projects" || -L "$HOME_DIR/projects" ]]; then
  echo "left $HOME_DIR/projects alone: it already exists and is not Omelet's link"
fi

chown "$OWNER" "$AGENTS_MD" "$HOME_DIR/.codex"
if [[ -L "$HOME_DIR/projects" && "$(readlink "$HOME_DIR/projects")" == "$TARGET" ]]; then
  chown -h "$OWNER" "$HOME_DIR/projects"
fi
```

- [ ] **Step 6: Run the engine tests, then fix what the new layout invalidated**

Run: `python3 -m pytest tests/engine -q`
Expected: PASS. The older text tests in `test_install_shell.py` (Docker repo, `chgrp`/`g+rwX`/`g+s` before `up -d`, token creation order, docker GID, absolute docker path, `compose -f {STACK} pull` before `up -d`) still hold unchanged; `_index_of(" up -d")` returns the first match, which is the plain `up -d`, not the `--force-recreate` line.

Then run: `bash -n engine/install.sh && python3 -m pytest -q`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add engine/install.sh engine/lib/install-agents.sh tests/engine/test_install_shell.py \
  tests/engine/test_install_agents.py
git commit -m "feat: engine installer records its ref, repairs the agent, installs skills with npx"
```

---

### Task 6: Document the architecture shape and the release steps

**Files:**
- Modify: `CLAUDE.md`
- Modify: `README.md`

**Interfaces:** none.

- [ ] **Step 1: Add the architecture shape to `CLAUDE.md`**

Insert after the "What this is" section (before `## Commands`):
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

### Releasing the engine (manual until CI exists)

1. Bump `agent/__init__.py`'s `__version__`, the Dockerfile's `AGENT_VERSION` and
   `engine/stack.yml`'s image tag together (`tests/test_constants_agree.py` holds them equal).
2. `docker build -t ghcr.io/omelet-app/omelet-agent:X.Y.Z agent/ && docker push ghcr.io/omelet-app/omelet-agent:X.Y.Z`
3. `git tag engine-vX.Y.Z && git push origin engine-vX.Y.Z`

Bump `agent/core/constants.API_VERSION` (and the host's `SUPPORTED_API`) only when a route the host
calls changes incompatibly — that one needs a host release.
```

- [ ] **Step 2: Bring the rest of `CLAUDE.md` in line**

- Commands: replace `(422 tests, ~6s)` with the count from `python3 -m pytest -q` now.
- Architecture diagram: replace `guest: dockerd, the token read, bootstrap` with `guest: the engine bootstrap, the token read`.
- Layers: replace the `host/provision/` bullet and the `host/provision/guest/omelet.py` bullet with:
```markdown
- `host/core/bootstrap.py` — the host's whole share of provisioning: unless
  `/opt/omelet/engine.version` exists (or `repair=True`), it runs a base64 stub as one `bash -lc`
  argument that downloads `OMELET_ENGINE_URL` in full and runs it, forwarding `OMELET_ENGINE_REF`
  when set and `OMELET_ENGINE_REPAIR=1` on repair. Values are checked against a shell-safe pattern
  because the argument is re-parsed by wsl.exe/ssh. `host/provision/` holds only the installer's
  `nginx-hello` smoke-test project (`tests/host/test_no_guest_assets.py`).
- `engine/get.sh` — the entrypoint: `resolve_ref` (explicit `OMELET_ENGINE_REF` → installed ref on
  repair → highest `engine-v*` tag by `sort -V`), downloads that ref's tarball, replaces
  `/opt/omelet/engine/` with its `engine/`, runs `install.sh <ref> [--repair]`.
- `engine/install.sh` — Docker, `edge`, `/opt/omelet` permissions, token, the stack (recreating the
  agent on a new token or repair), Node ≥ 22.20 from NodeSource, `/usr/local/bin/omelet`,
  `/etc/claude-code/CLAUDE.md`, then per account (root + `lib/login-users.sh`) the Codex block and
  `~/projects` link (`lib/install-agents.sh`) and `npx -y skills@1.5.26 add /opt/omelet/engine/skills
  -g -a claude-code codex`. Writes `engine.version` last.
- `engine/cli/omelet.py` — the `omelet` command **inside** the VM, used by coding agents:
  `up`/`new`/`clone`/`status`/`logs`/`down` over the agent API with the guest token. One
  stdlib-only file, loaded by tests by path (`tests/engine/cli/loader.py`); it shares constants
  with both sides, held equal by `tests/test_constants_agree.py`. `engine/instructions/` and
  `engine/skills/` hold what those agents read. Nothing is written into user repositories.
```
- Things that will bite you:
  - In the "Project files travel over HTTP" bullet, delete the last two sentences about `bootstrap._push_file`.
  - Delete both `BOOTSTRAP_VERSION` bullets and replace them with:
```markdown
- **Existing VMs update only when the engine is installed again.** Setup skips an installed engine;
  the connect step's token repair and a fresh VM are the only reinstall paths today (self-update is
  deferred — `docs/future/engine-self-update.md`). Accounts created after install get no skills
  until then.
- **`npx` inside `install.sh`'s account loop must read `</dev/null`**: the loop reads accounts from
  stdin, and anything else reading it eats the remaining accounts.
```
  - Replace the "Nothing under `agent/` is bundled" bullet with:
```markdown
- **Nothing under `agent/` or `engine/` is bundled into the frozen host binary**, and
  `tests/host/test_frozen_bundle.py` fails if a `datas` entry reappears. The VM pulls the image
  and fetches the engine itself; only the `nginx-hello` smoke test and `omelet.yaml` ship with the host.
```
  - In the "agent container runs as a non-root user" bullet, replace `bootstrap.sh` with `engine/install.sh`.
  - In "Guest failures must stay loud", replace "re-reads the marker afterwards" with "re-checks `engine.version` afterwards".
  - In the `install.verify_step` bullet, replace everything from `` `agent_version_step` runs just before it`` to the end of the bullet with:
```markdown
`connect_step` runs just before it: an agent whose `/health` `api` is not in
`constants.SUPPORTED_API` is reported in one sentence and never repaired, and an agent answering
`agent_unconfigured` or `unauthorized` gets one `bootstrap(repair=True)` — `/health` is exempt from
the token check, so `restart: always` never restarts a container refusing every other route, and
the agent reads its token **once, at startup**, which is why repair recreates the container — then
the host re-reads the token and dials again.
```
- Testing conventions: replace the `tests/host/test_bootstrap_shell.py` bullet with:
```markdown
- Engine scripts are tested under `tests/engine/`: `bash -n` plus text assertions over
  `install.sh`, `resolve_ref` sourced from `get.sh` with a fake `git` on `PATH`, and
  `install-agents.sh`/`login-users.sh` run against temporary homes. Nothing there touches the
  network; the live-VM acceptance run covers apt, NodeSource, npm and ghcr.
```

- [ ] **Step 3: Update `README.md`**

- In the `vm create` paragraph, replace `bootstraps Docker + Traefik inside it` with `installs the Omelet engine inside it (fetched from \`OMELET_ENGINE_URL\`, default \`engine/get.sh\` on GitHub; set \`OMELET_ENGINE_REF\` to a branch or tag to install something other than the latest \`engine-v*\` release)`.
- In "Looking inside the VM", replace the two `.bootstrapped` / `bootstrap.sh` lines with:
```powershell
wsl -d omelet-vm -u root -- cat /opt/omelet/engine.version            # installed engine ref
wsl -d omelet-vm -u root -- bash /opt/omelet/engine/install.sh engine-vX.Y.Z --repair   # re-run, live output
```

- [ ] **Step 4: Verify nothing stale is left**

Run: `grep -rn "BOOTSTRAP_VERSION\|\.bootstrapped\|host/provision/guest\|host/provision/agents\|agent_version_step\|guest_assets" CLAUDE.md README.md host tests engine agent packaging`
Expected: no output except `rm -rf ... /opt/omelet/.bootstrapped` in `engine/install.sh` and the test asserting no early exit on it.

Run: `python3 -m pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md README.md
git commit -m "docs: state the host-shell / engine architecture and the engine release steps"
```

---

## After the last task

- Push the branch and open a PR to `main` with `gh pr create`; the description lists what was deliberately left untested (network/apt/NodeSource/npm/ghcr steps, the stub's python3 fallback, `npx skills add` inside a real VM) and that the repository must be public and an `engine-v0.1.0` tag pushed before a fresh VM can install.
- Run a code review with a separate agent after the PR is created.
- Manual acceptance (user, PowerShell): fresh `omelet setup`; `wsl -d omelet-vm -u root -- cat /opt/omelet/engine.version`; `ls -la ~/.claude/skills ~/.agents/skills` as the login user; an existing PR #3 VM re-running setup migrates without duplicate `omelet-setup` skills.
