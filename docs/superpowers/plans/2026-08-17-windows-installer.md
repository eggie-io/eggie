# Windows Installer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A non-technical Windows user downloads one file, double-clicks it, and ends up with a provisioned Linux VM running Docker and Traefik, proven by a real HTTP 200.

**Architecture:** A platform-free install engine (`runtime/core/install.py`) drives an ordered list of idempotent steps and delegates every platform-specific action to the existing `VmProvider` abstraction, which gains three methods. A tkinter setup app renders progress. PyInstaller freezes the CLI; Inno Setup wraps it into a per-user installer.

**Tech Stack:** Python 3.12, typer, pyyaml, tkinter (stdlib), PyInstaller (build-time), Inno Setup (build-time), pytest.

**Spec:** `docs/superpowers/specs/2026-08-17-windows-installer-design.md`

## Global Constraints

Every task's requirements implicitly include this section.

- **Python 3.12+** (`requires-python = ">=3.12"`).
- **Runtime dependencies stay exactly `typer` and `pyyaml`.** tkinter is stdlib. PyInstaller and Inno Setup are build-time only and must not enter `[project.dependencies]`.
- **Platform-boundary invariant:** no `sys.platform`, `platform.system()`, or `os.name` anywhere under `runtime/` outside `runtime/providers/`. Enforced by `tests/test_no_platform_leak.py` — it will fail the build if violated.
- **Rootfs images** (exact, verified 2026-08-17):
  - amd64 URL `https://releases.ubuntu.com/24.04.4/ubuntu-24.04.4-wsl-amd64.wsl`, sha256 `9b2f7730dc68227dd04a9f3e5eab86ad85caf556b8606ad94f1f29ff5c4fd3f5`
  - arm64 URL `https://cdimages.ubuntu.com/releases/24.04.4/release/ubuntu-24.04.4-wsl-arm64.wsl`, sha256 `6b244d89f412a68f51e58f396fab65bed3b5896a25c045a99bef9c78a07df507`
- **Install target:** per-user, `%LOCALAPPDATA%\Programs\LocalRuntime`. Inno `PrivilegesRequired=lowest`.
- **State and cache:** `%LOCALAPPDATA%\Runtime\install-state.json`, `%LOCALAPPDATA%\Runtime\cache`, `%LOCALAPPDATA%\Runtime\logs`.
- **Existing constants** in `runtime/core/constants.py` are authoritative: `EDGE_PORT = 39080`, `DEFAULT_DOMAIN = "local.eggie.space"`. Never re-declare them.
- **PyInstaller one-dir**, never one-file.
- **Running tests:** `python3 -m pytest -q`. In sandboxes where `/tmp/pytest-of-*` is root-owned, prefix with `TMPDIR=<writable dir>` or `tmp_path` fixtures fail. Baseline before this plan: **82 passed**.
- **No network in tests.** Every download, subprocess, registry write, and HTTP request is behind an injected callable.

---

## File Structure

**Create:**

| File | Responsibility |
|---|---|
| `runtime/core/images.py` | Rootfs URL + SHA256 table. Data only. |
| `runtime/core/download.py` | Resumable download, SHA256 verification, cache reuse. |
| `runtime/core/install.py` | Step type, install state file, engine loop, step factory. |
| `runtime/setup_app/__init__.py` | Package marker. |
| `runtime/setup_app/app.py` | tkinter progress window. |
| `packaging/windows/runtime.spec` | PyInstaller one-dir spec. |
| `packaging/windows/installer.iss` | Inno Setup script. |
| `packaging/windows/build.ps1` | Freeze + package; identical locally and in CI. |
| `tests/core/test_download.py` | Download/verify/cache logic. |
| `tests/core/test_install.py` | Engine ordering, resume, idempotency, classification. |
| `tests/core/test_verify_step.py` | HTTP 200 gate. |
| `tests/providers/test_wsl_preflight.py` | Preflight parsers against captured real output. |
| `tests/providers/test_wsl_remedy.py` | Elevation and RunOnce, with injected fakes. |
| `tests/test_setup_cli.py` | `runtime setup` / `runtime uninstall` wiring. |

**Modify:**

| File | Change |
|---|---|
| `runtime/core/provider.py` | `CheckResult.remedy`; three new Protocol methods. |
| `runtime/providers/wsl_checks.py` | Pure `preflight_checks(...)` + parsers. |
| `runtime/providers/wsl2.py` | `preflight`, `apply_remedy`, `reboot_required`, arch/image selection, elevation, RunOnce. |
| `runtime/providers/lima.py` | Same three methods so the Protocol holds on both. |
| `runtime/cli.py` | `setup` and `uninstall` commands. |
| `pyproject.toml` | `pyinstaller` in the `dev` extra only. |

---

## Task 1: Extend the provider contract

**Files:**
- Modify: `runtime/core/provider.py`
- Test: `tests/core/test_provider_types.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `CheckResult(label: str, ok: bool, fix: str | None = None, remedy: str | None = None)`; `Diagnosis.dead_ends -> list[CheckResult]`; `Diagnosis.fixable -> list[CheckResult]`; Protocol methods `preflight() -> Diagnosis`, `apply_remedy(remedy: str) -> None`, `reboot_required() -> bool`.

- [ ] **Step 1: Write the failing test**

Append to `tests/core/test_provider_types.py`:

```python
def test_diagnosis_splits_dead_ends_from_fixable():
    d = Diagnosis([
        CheckResult("ok thing", True),
        CheckResult("bios", False, fix="enable VT-x in BIOS"),
        CheckResult("wsl features", False, fix="we can do it", remedy="enable_wsl_features"),
    ])
    assert [c.label for c in d.dead_ends] == ["bios"]
    assert [c.label for c in d.fixable] == ["wsl features"]


def test_check_result_defaults_to_no_remedy():
    assert CheckResult("x", False, fix="do it yourself").remedy is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/core/test_provider_types.py -v`
Expected: FAIL — `AttributeError: 'Diagnosis' object has no attribute 'dead_ends'`

- [ ] **Step 3: Write minimal implementation**

In `runtime/core/provider.py`, add `remedy` to `CheckResult`:

```python
@dataclass(frozen=True)
class CheckResult:
    label: str
    ok: bool
    fix: str | None = None
    remedy: str | None = None
```

Add to `Diagnosis`:

```python
    @property
    def dead_ends(self) -> list[CheckResult]:
        return [c for c in self.blocking if c.remedy is None]

    @property
    def fixable(self) -> list[CheckResult]:
        return [c for c in self.blocking if c.remedy is not None]
```

Add to the `VmProvider` Protocol:

```python
    def preflight(self) -> Diagnosis: ...
    def apply_remedy(self, remedy: str) -> None: ...
    def reboot_required(self) -> bool: ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/core/test_provider_types.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add runtime/core/provider.py tests/core/test_provider_types.py
git commit -m "feat: classify diagnosis checks as dead-end or auto-fixable"
```

---

## Task 2: Image table and resumable download

**Files:**
- Create: `runtime/core/images.py`, `runtime/core/download.py`, `tests/core/test_download.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `Image(url: str, sha256: str)`; `WSL_IMAGES: dict[str, Image]` keyed `"amd64"` / `"arm64"`; `sha256_of(path: Path) -> str`; `fetch(image: Image, dest: Path, *, opener, on_progress=None) -> Path`.

`opener(url: str, start_byte: int) -> tuple[IO[bytes], int]` returns a readable stream and the total content length. Injected so tests never touch the network.

- [ ] **Step 1: Write the failing test**

Create `tests/core/test_download.py`:

```python
import hashlib
import io
import pytest

from runtime.core.images import Image, WSL_IMAGES
from runtime.core.download import fetch, sha256_of, ChecksumMismatch

PAYLOAD = b"ubuntu-rootfs-bytes"
DIGEST = hashlib.sha256(PAYLOAD).hexdigest()


class FakeOpener:
    """Serves PAYLOAD, honouring a start offset like an HTTP Range request."""

    def __init__(self, payload=PAYLOAD):
        self.calls = []
        self._payload = payload

    def __call__(self, url, start_byte):
        self.calls.append((url, start_byte))
        return io.BytesIO(self._payload[start_byte:]), len(self._payload)


def test_fetch_downloads_and_verifies(tmp_path):
    opener = FakeOpener()
    dest = tmp_path / "img.wsl"
    out = fetch(Image("http://x/img.wsl", DIGEST), dest, opener=opener)
    assert out.read_bytes() == PAYLOAD
    assert opener.calls == [("http://x/img.wsl", 0)]


def test_fetch_reuses_a_cached_file_with_a_matching_hash(tmp_path):
    dest = tmp_path / "img.wsl"
    dest.write_bytes(PAYLOAD)
    opener = FakeOpener()
    fetch(Image("http://x/img.wsl", DIGEST), dest, opener=opener)
    assert opener.calls == [], "a valid cached file must not be re-downloaded"


def test_fetch_resumes_from_a_partial_file(tmp_path):
    dest = tmp_path / "img.wsl"
    dest.with_suffix(".wsl.part").write_bytes(PAYLOAD[:5])
    opener = FakeOpener()
    fetch(Image("http://x/img.wsl", DIGEST), dest, opener=opener)
    assert opener.calls == [("http://x/img.wsl", 5)]
    assert dest.read_bytes() == PAYLOAD


def test_fetch_deletes_the_file_on_checksum_mismatch(tmp_path):
    dest = tmp_path / "img.wsl"
    opener = FakeOpener(payload=b"corrupted")
    with pytest.raises(ChecksumMismatch):
        fetch(Image("http://x/img.wsl", DIGEST), dest, opener=opener)
    assert not dest.exists(), "a bad download must not be left on disk to be reused"


def test_wsl_image_table_has_both_architectures():
    assert set(WSL_IMAGES) == {"amd64", "arm64"}
    assert all(len(i.sha256) == 64 for i in WSL_IMAGES.values())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/core/test_download.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'runtime.core.images'`

- [ ] **Step 3: Write minimal implementation**

Create `runtime/core/images.py`:

```python
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Image:
    url: str
    sha256: str


# Canonical's official WSL images — the files Microsoft's WSL distribution
# manifest points at. cloud-images.ubuntu.com/wsl/ holds only manifests now.
WSL_IMAGES: dict[str, Image] = {
    "amd64": Image(
        "https://releases.ubuntu.com/24.04.4/ubuntu-24.04.4-wsl-amd64.wsl",
        "9b2f7730dc68227dd04a9f3e5eab86ad85caf556b8606ad94f1f29ff5c4fd3f5",
    ),
    "arm64": Image(
        "https://cdimages.ubuntu.com/releases/24.04.4/release/ubuntu-24.04.4-wsl-arm64.wsl",
        "6b244d89f412a68f51e58f396fab65bed3b5896a25c045a99bef9c78a07df507",
    ),
}
```

Create `runtime/core/download.py`:

```python
from __future__ import annotations

import hashlib
from pathlib import Path

from .images import Image

_CHUNK = 1024 * 1024


class ChecksumMismatch(RuntimeError):
    """The downloaded bytes do not match the expected digest."""


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _default_opener(url: str, start_byte: int):
    from urllib.request import Request, urlopen
    headers = {"Range": f"bytes={start_byte}-"} if start_byte else {}
    response = urlopen(Request(url, headers=headers))
    length = int(response.headers.get("Content-Length", 0)) + start_byte
    return response, length


def fetch(image: Image, dest: Path, *, opener=_default_opener,
          on_progress=None) -> Path:
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)

    if dest.exists() and sha256_of(dest) == image.sha256:
        return dest

    part = dest.with_suffix(dest.suffix + ".part")
    start = part.stat().st_size if part.exists() else 0
    stream, total = opener(image.url, start)
    with open(part, "ab") as out:
        while chunk := stream.read(_CHUNK):
            out.write(chunk)
            start += len(chunk)
            if on_progress:
                on_progress(start, total)

    if sha256_of(part) != image.sha256:
        part.unlink(missing_ok=True)
        dest.unlink(missing_ok=True)
        raise ChecksumMismatch(
            f"{dest.name}: expected {image.sha256}, got {sha256_of(part) if part.exists() else 'nothing'}")

    part.replace(dest)
    return dest
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/core/test_download.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add runtime/core/images.py runtime/core/download.py tests/core/test_download.py
git commit -m "feat: resumable checksummed rootfs download"
```

---

## Task 3: Install state file

**Files:**
- Create: `runtime/core/install.py`, `tests/core/test_install.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `InstallState(path)` with `.completed() -> set[str]`, `.mark(step: str) -> None`, `.clear() -> None`.

- [ ] **Step 1: Write the failing test**

Create `tests/core/test_install.py`:

```python
from runtime.core.install import InstallState


def test_state_round_trips_through_a_new_object(tmp_path):
    path = tmp_path / "install-state.json"
    InstallState(path).mark("preflight")
    assert InstallState(path).completed() == {"preflight"}


def test_state_starts_empty_when_the_file_is_absent(tmp_path):
    assert InstallState(tmp_path / "nope.json").completed() == set()


def test_state_survives_a_corrupt_file(tmp_path):
    # A half-written file after a power loss must not brick setup forever.
    path = tmp_path / "install-state.json"
    path.write_text("{not json")
    assert InstallState(path).completed() == set()


def test_clear_forgets_everything(tmp_path):
    path = tmp_path / "install-state.json"
    state = InstallState(path)
    state.mark("preflight")
    state.clear()
    assert InstallState(path).completed() == set()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/core/test_install.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'runtime.core.install'`

- [ ] **Step 3: Write minimal implementation**

Create `runtime/core/install.py`:

```python
from __future__ import annotations

import json
from pathlib import Path


class InstallState:
    """Which steps have finished, so a resume or re-run skips them."""

    def __init__(self, path):
        self._path = Path(path)

    def completed(self) -> set[str]:
        try:
            data = json.loads(self._path.read_text())
        except (OSError, json.JSONDecodeError):
            return set()
        steps = data.get("completed", [])
        return set(steps) if isinstance(steps, list) else set()

    def mark(self, step: str) -> None:
        done = self.completed()
        done.add(step)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps({"completed": sorted(done)}))

    def clear(self) -> None:
        self._path.unlink(missing_ok=True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/core/test_install.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add runtime/core/install.py tests/core/test_install.py
git commit -m "feat: install state file with corrupt-file tolerance"
```

---

## Task 4: The engine loop

**Files:**
- Modify: `runtime/core/install.py`
- Test: `tests/core/test_install.py`

**Interfaces:**
- Consumes: `InstallState` (Task 3).
- Produces: `Progress(step: str, status: str, message: str = "")` where status is one of `"running" | "done" | "skipped" | "failed" | "reboot"`; `Step(name: str, run: Callable[[], None])`; `RebootRequired`; `InstallError(step: str, message: str)`; `run_install(steps: list[Step], state: InstallState, report: Callable[[Progress], None]) -> None`.

- [ ] **Step 1: Write the failing test**

Append to `tests/core/test_install.py`:

```python
import pytest

from runtime.core.install import (
    InstallError, Progress, RebootRequired, Step, run_install,
)


def _recorder():
    events = []
    return events, events.append


def test_steps_run_in_order_and_are_all_marked(tmp_path):
    ran = []
    steps = [Step(n, (lambda n=n: ran.append(n))) for n in ("a", "b", "c")]
    state = InstallState(tmp_path / "s.json")
    events, report = _recorder()
    run_install(steps, state, report)
    assert ran == ["a", "b", "c"]
    assert state.completed() == {"a", "b", "c"}
    assert [(e.step, e.status) for e in events if e.status == "done"] == \
        [("a", "done"), ("b", "done"), ("c", "done")]


def test_completed_steps_are_skipped_not_rerun(tmp_path):
    ran = []
    state = InstallState(tmp_path / "s.json")
    state.mark("a")
    steps = [Step(n, (lambda n=n: ran.append(n))) for n in ("a", "b")]
    events, report = _recorder()
    run_install(steps, state, report)
    assert ran == ["b"], "an already-completed step must not run again"
    assert Progress("a", "skipped") in events


def test_a_failing_step_stops_the_run_and_is_not_marked(tmp_path):
    ran = []

    def boom():
        raise RuntimeError("apt exploded")

    steps = [Step("a", lambda: ran.append("a")),
             Step("b", boom),
             Step("c", lambda: ran.append("c"))]
    state = InstallState(tmp_path / "s.json")
    events, report = _recorder()
    with pytest.raises(InstallError) as excinfo:
        run_install(steps, state, report)
    assert excinfo.value.step == "b"
    assert "apt exploded" in str(excinfo.value)
    assert ran == ["a"], "steps after a failure must not run"
    assert state.completed() == {"a"}, "a failed step must stay un-marked so a re-run retries it"


def test_reboot_marks_the_step_so_resume_moves_past_it(tmp_path):
    steps = [Step("gate", lambda: (_ for _ in ()).throw(RebootRequired())),
             Step("after", lambda: None)]
    state = InstallState(tmp_path / "s.json")
    events, report = _recorder()
    with pytest.raises(RebootRequired):
        run_install(steps, state, report)
    assert state.completed() == {"gate"}, \
        "the gate is satisfied by the reboot itself; resume must not re-trigger it"
    assert any(e.status == "reboot" for e in events)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/core/test_install.py -v`
Expected: FAIL — `ImportError: cannot import name 'Progress'`

- [ ] **Step 3: Write minimal implementation**

Add to `runtime/core/install.py`:

```python
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class Progress:
    step: str
    status: str          # running | done | skipped | failed | reboot
    message: str = ""


@dataclass(frozen=True)
class Step:
    name: str
    run: Callable[[], None]


class RebootRequired(Exception):
    """The machine must restart before the remaining steps can run."""


class InstallError(RuntimeError):
    def __init__(self, step: str, message: str):
        super().__init__(f"{step}: {message}")
        self.step = step
        self.message = message


def run_install(steps: list[Step], state: InstallState,
                report: Callable[[Progress], None]) -> None:
    done = state.completed()
    for step in steps:
        if step.name in done:
            report(Progress(step.name, "skipped"))
            continue
        report(Progress(step.name, "running"))
        try:
            step.run()
        except RebootRequired:
            # The reboot itself satisfies the gate; resuming must step past it.
            state.mark(step.name)
            report(Progress(step.name, "reboot"))
            raise
        except Exception as e:
            report(Progress(step.name, "failed", str(e)))
            raise InstallError(step.name, str(e)) from e
        state.mark(step.name)
        report(Progress(step.name, "done"))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/core/test_install.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add runtime/core/install.py tests/core/test_install.py
git commit -m "feat: install engine with resume, skip, and reboot handling"
```

---

## Task 5: Preflight and remediate steps

**Files:**
- Modify: `runtime/core/install.py`
- Test: `tests/core/test_install.py`

**Interfaces:**
- Consumes: `Diagnosis.dead_ends` / `.fixable` (Task 1), `Step` (Task 4).
- Produces: `preflight_step(provider) -> None`; `remediate_step(provider) -> None`; `reboot_gate_step(provider) -> None`; `DeadEnd(RuntimeError)`.

- [ ] **Step 1: Write the failing test**

Append to `tests/core/test_install.py`:

```python
from runtime.core.install import DeadEnd, preflight_step, remediate_step, reboot_gate_step
from runtime.core.provider import CheckResult, Diagnosis


class FakeHost:
    def __init__(self, diagnosis, reboot=False):
        self._diagnosis = diagnosis
        self._reboot = reboot
        self.applied = []

    def preflight(self):
        return self._diagnosis

    def apply_remedy(self, remedy):
        self.applied.append(remedy)

    def reboot_required(self):
        return self._reboot


def test_preflight_raises_dead_end_with_the_users_instructions():
    host = FakeHost(Diagnosis([
        CheckResult("virtualization enabled", False,
                    fix="Restart into BIOS and enable Intel VT-x or AMD-V"),
    ]))
    with pytest.raises(DeadEnd, match="VT-x"):
        preflight_step(host)


def test_preflight_passes_when_only_fixable_checks_fail():
    host = FakeHost(Diagnosis([
        CheckResult("wsl features", False, fix="we enable it", remedy="enable_wsl_features"),
    ]))
    preflight_step(host)      # must not raise — step 2 handles this


def test_remediate_applies_only_fixable_remedies():
    host = FakeHost(Diagnosis([
        CheckResult("fine", True),
        CheckResult("wsl features", False, fix="x", remedy="enable_wsl_features"),
        CheckResult("wsl outdated", False, fix="y", remedy="update_wsl"),
    ]))
    remediate_step(host)
    assert host.applied == ["enable_wsl_features", "update_wsl"]


def test_remediate_does_nothing_when_everything_passes():
    host = FakeHost(Diagnosis([CheckResult("fine", True)]))
    remediate_step(host)
    assert host.applied == []


def test_reboot_gate_raises_only_when_the_provider_says_so():
    reboot_gate_step(FakeHost(Diagnosis([]), reboot=False))    # no raise
    with pytest.raises(RebootRequired):
        reboot_gate_step(FakeHost(Diagnosis([]), reboot=True))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/core/test_install.py -v`
Expected: FAIL — `ImportError: cannot import name 'DeadEnd'`

- [ ] **Step 3: Write minimal implementation**

Add to `runtime/core/install.py`:

```python
class DeadEnd(RuntimeError):
    """A blocking check no code can fix — the user must act."""


def preflight_step(provider) -> None:
    diagnosis = provider.preflight()
    dead = diagnosis.dead_ends
    if dead:
        raise DeadEnd("\n".join(
            f"{c.label}: {c.fix}" if c.fix else c.label for c in dead))


def remediate_step(provider) -> None:
    for check in provider.preflight().fixable:
        provider.apply_remedy(check.remedy)


def reboot_gate_step(provider) -> None:
    if provider.reboot_required():
        raise RebootRequired()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/core/test_install.py -v`
Expected: PASS (13 tests)

- [ ] **Step 5: Commit**

```bash
git add runtime/core/install.py tests/core/test_install.py
git commit -m "feat: preflight, remediate, and reboot-gate steps"
```

---

## Task 6: The HTTP 200 verify step

**Files:**
- Modify: `runtime/core/install.py`
- Create: `tests/core/test_verify_step.py`

**Interfaces:**
- Consumes: `push_project`, `compose_up`, `compose_down` from `runtime/core/lifecycle.py`; `load_project` and `STARTED_OK` from `runtime/core/project.py`.
- Produces: `verify_step(provider, template_dir: Path, domain: str, *, http_get) -> None`; `VerificationFailed(RuntimeError)`.

`http_get(url: str) -> int` returns an HTTP status code. Injected so tests never open a socket.

- [ ] **Step 1: Write the failing test**

Create `tests/core/test_verify_step.py`:

```python
import pytest
import yaml

from runtime.core.install import VerificationFailed, verify_step
from runtime.core.provider import Completed


class FakeProvider:
    """Records guest commands; reports a healthy compose stack."""

    def __init__(self):
        self.execs = []

    def exec(self, argv, *, root=False):
        self.execs.append(argv)
        if "ps" in argv:
            return Completed(0, '[{"Service":"web","State":"running"}]', "")
        return Completed(0, "", "")


@pytest.fixture
def template(tmp_path):
    d = tmp_path / "nginx-hello"
    d.mkdir()
    (d / "docker-compose.yml").write_text(yaml.safe_dump(
        {"services": {"web": {"image": "nginx:alpine", "ports": ["8080:80"]}}}))
    return d


def test_verify_passes_on_200_and_tears_the_project_down(template):
    provider = FakeProvider()
    seen = []

    def http_get(url):
        seen.append(url)
        return 200

    verify_step(provider, template, "local.eggie.space", http_get=http_get)
    assert seen == ["http://nginx-hello.local.eggie.space:39080"]
    assert any("down" in argv for argv in provider.execs), \
        "the smoke-test project must not be left running"


def test_verify_fails_on_a_non_200_status(template):
    with pytest.raises(VerificationFailed, match="502"):
        verify_step(FakeProvider(), template, "local.eggie.space",
                    http_get=lambda url: 502)


def test_verify_tears_down_even_when_the_request_fails(template):
    provider = FakeProvider()

    def http_get(url):
        raise OSError("connection refused")

    with pytest.raises(VerificationFailed):
        verify_step(provider, template, "local.eggie.space", http_get=http_get)
    assert any("down" in argv for argv in provider.execs)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/core/test_verify_step.py -v`
Expected: FAIL — `ImportError: cannot import name 'VerificationFailed'`

- [ ] **Step 3: Write minimal implementation**

Add to `runtime/core/install.py`:

```python
class VerificationFailed(RuntimeError):
    """The smoke-test project did not serve a successful response."""


def _default_http_get(url: str) -> int:
    from urllib.request import urlopen
    with urlopen(url, timeout=30) as response:
        return response.status


def verify_step(provider, template_dir, domain: str, *,
                http_get=_default_http_get) -> None:
    """Run the bundled template end to end and require HTTP 200."""
    import yaml

    from . import constants
    from .lifecycle import compose_down, compose_up, push_project
    from .project import STARTED_OK, load_project

    template_dir = Path(template_dir)
    compose = yaml.safe_load((template_dir / "docker-compose.yml").read_text()) or {}
    project = load_project(compose, None, template_dir.name)
    try:
        push_project(provider, project.id, template_dir)
        status, urls = compose_up(provider, project, template_dir, domain)
        if status != STARTED_OK:
            raise VerificationFailed(f"smoke-test project status: {status}")
        try:
            code = http_get(urls[0])
        except Exception as e:
            raise VerificationFailed(f"{urls[0]} did not respond: {e}") from e
        if code != 200:
            raise VerificationFailed(f"{urls[0]} returned HTTP {code}, expected 200")
    finally:
        compose_down(provider, project.id)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/core/test_verify_step.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add runtime/core/install.py tests/core/test_verify_step.py
git commit -m "feat: end-to-end verify step gated on HTTP 200"
```

---

## Task 7: WSL2 preflight checks

**Files:**
- Modify: `runtime/providers/wsl_checks.py`
- Create: `tests/providers/test_wsl_preflight.py`

**Interfaces:**
- Consumes: `CheckResult`, `Diagnosis` (Task 1).
- Produces: `parse_wsl_version(text: str) -> tuple[int, ...] | None`; `preflight_checks(*, wsl_version_text: str, build: int, hypervisor_present: bool, firmware_virtualization: bool, free_gb: float) -> Diagnosis`.

Keeping `preflight_checks` a pure function of gathered facts is what makes it testable — the provider does the impure gathering.

- [ ] **Step 1: Write the failing test**

Create `tests/providers/test_wsl_preflight.py`:

```python
from runtime.providers.wsl_checks import parse_wsl_version, preflight_checks

# Captured from a real `wsl --version` on Windows 11.
REAL_WSL_VERSION = """WSL version: 2.3.26.0
Kernel version: 5.15.167.4-1
WSLg version: 1.0.65
MSRDC version: 1.2.5620
Direct3D version: 1.611.1-81528511
DXCore version: 10.0.26100.1-240331-1435.ge-release
Windows version: 10.0.26100.2314"""

HEALTHY = dict(wsl_version_text=REAL_WSL_VERSION, build=26100,
               hypervisor_present=True, firmware_virtualization=True,
               free_gb=120.0)


def test_parses_a_real_wsl_version_banner():
    assert parse_wsl_version(REAL_WSL_VERSION) == (2, 3, 26, 0)


def test_returns_none_for_inbox_wsl_which_has_no_version_command():
    # Old in-box WSL prints usage text to stderr instead of a version.
    assert parse_wsl_version("Invalid command line option: --version") is None


def test_a_healthy_host_has_no_blocking_checks():
    assert preflight_checks(**HEALTHY).ok is True


def test_firmware_virtualization_off_is_a_dead_end():
    diag = preflight_checks(**{**HEALTHY, "hypervisor_present": False,
                               "firmware_virtualization": False})
    labels = [c.label for c in diag.dead_ends]
    assert any("virtualization" in l.lower() for l in labels)
    bios = next(c for c in diag.dead_ends if "virtualization" in c.label.lower())
    assert "BIOS" in bios.fix or "UEFI" in bios.fix


def test_hypervisor_running_counts_as_virtualization_available():
    # Once WSL2/Hyper-V is running, firmware flags can read False; the
    # hypervisor being present is proof enough.
    diag = preflight_checks(**{**HEALTHY, "firmware_virtualization": False,
                               "hypervisor_present": True})
    assert diag.ok is True


def test_missing_store_wsl_is_fixable_not_a_dead_end():
    diag = preflight_checks(**{**HEALTHY, "wsl_version_text": ""})
    assert [c.remedy for c in diag.fixable] == ["update_wsl"]
    assert diag.dead_ends == []


def test_old_windows_build_is_a_dead_end():
    diag = preflight_checks(**{**HEALTHY, "build": 18363})
    assert any("Windows" in c.label for c in diag.dead_ends)


def test_low_disk_space_is_a_dead_end():
    diag = preflight_checks(**{**HEALTHY, "free_gb": 3.2})
    assert any("disk" in c.label.lower() for c in diag.dead_ends)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/providers/test_wsl_preflight.py -v`
Expected: FAIL — `ImportError: cannot import name 'parse_wsl_version'`

- [ ] **Step 3: Write minimal implementation**

Add to `runtime/providers/wsl_checks.py`:

```python
import re

MIN_BUILD = 19045
MIN_FREE_GB = 10.0


def parse_wsl_version(text: str) -> tuple[int, ...] | None:
    match = re.search(r"WSL version:\s*([\d.]+)", text)
    if not match:
        return None
    return tuple(int(p) for p in match.group(1).split("."))


def preflight_checks(*, wsl_version_text: str, build: int,
                     hypervisor_present: bool, firmware_virtualization: bool,
                     free_gb: float) -> Diagnosis:
    checks: list[CheckResult] = []

    build_ok = build >= MIN_BUILD
    checks.append(CheckResult(
        f"Windows build {build} supports WSL2", build_ok,
        None if build_ok else
        f"Windows build {MIN_BUILD} or newer is required; run Windows Update"))

    # A running hypervisor proves virtualization works even when the firmware
    # flag reads False, which it does once Hyper-V has claimed the CPU.
    virt_ok = hypervisor_present or firmware_virtualization
    checks.append(CheckResult(
        "CPU virtualization available", virt_ok,
        None if virt_ok else
        "Restart into BIOS/UEFI setup and enable Intel VT-x or AMD-V "
        "(often called 'Virtualization Technology' or 'SVM Mode')"))

    wsl_ok = parse_wsl_version(wsl_version_text) is not None
    checks.append(CheckResult(
        "Store WSL installed", wsl_ok,
        None if wsl_ok else "we will install it for you",
        remedy=None if wsl_ok else "update_wsl"))

    disk_ok = free_gb >= MIN_FREE_GB
    checks.append(CheckResult(
        f"At least {MIN_FREE_GB:.0f} GB free disk space", disk_ok,
        None if disk_ok else
        f"only {free_gb:.1f} GB free; free up space and run setup again"))

    return Diagnosis(checks)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/providers/test_wsl_preflight.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add runtime/providers/wsl_checks.py tests/providers/test_wsl_preflight.py
git commit -m "feat: WSL2 preflight checks with dead-end classification"
```

---

## Task 8: WSL2 remedies, elevation, and reboot resume

**Files:**
- Modify: `runtime/providers/wsl2.py`, `runtime/providers/lima.py`
- Create: `tests/providers/test_wsl_remedy.py`

**Interfaces:**
- Consumes: `preflight_checks` (Task 7), `WSL_IMAGES` (Task 2).
- Produces: on `Wsl2Provider` — `preflight() -> Diagnosis`, `apply_remedy(remedy: str) -> None`, `reboot_required() -> bool`, `image() -> Image`, `register_resume(exe_path: str) -> None`. Constructor gains `elevator=<callable>`, `registry_writer=<callable>`, `facts=<callable>`.
- `elevator(exe: str, args: list[str]) -> int` returns an exit code.
- `registry_writer(key: str, name: str, value: str) -> None`.
- `facts() -> dict` returns the preflight inputs.

- [ ] **Step 1: Write the failing test**

Create `tests/providers/test_wsl_remedy.py`:

```python
import pytest

from runtime.providers.wsl2 import RUNONCE_KEY, Wsl2Provider

HEALTHY_FACTS = dict(
    wsl_version_text="WSL version: 2.3.26.0", build=26100,
    hypervisor_present=True, firmware_virtualization=True, free_gb=120.0)


def make(**kwargs):
    defaults = dict(runner=lambda argv: type("R", (), {
        "returncode": 0, "stdout": b"", "stderr": b""})(),
        facts=lambda: dict(HEALTHY_FACTS))
    return Wsl2Provider(**{**defaults, **kwargs})


def test_preflight_uses_gathered_facts():
    assert make().preflight().ok is True
    degraded = make(facts=lambda: {**HEALTHY_FACTS, "free_gb": 1.0})
    assert degraded.preflight().ok is False


def test_enable_features_remedy_runs_elevated_not_in_process():
    calls = []
    provider = make(elevator=lambda exe, args: calls.append((exe, args)) or 0)
    provider.apply_remedy("enable_wsl_features")
    assert calls == [("wsl.exe", ["--install", "--no-distribution"])]


def test_a_declined_uac_prompt_raises():
    # ShellExecuteW returns a non-zero exit code when the user says No.
    provider = make(elevator=lambda exe, args: 1223)
    with pytest.raises(RuntimeError, match="administrator"):
        provider.apply_remedy("enable_wsl_features")


def test_unknown_remedy_is_a_programming_error():
    with pytest.raises(ValueError, match="unknown remedy"):
        make().apply_remedy("make_coffee")


def test_reboot_is_required_only_after_features_were_enabled():
    provider = make(elevator=lambda exe, args: 0)
    assert provider.reboot_required() is False
    provider.apply_remedy("enable_wsl_features")
    assert provider.reboot_required() is True


def test_register_resume_writes_a_self_deleting_runonce_value():
    written = []
    provider = make(registry_writer=lambda key, name, value:
                    written.append((key, name, value)))
    provider.register_resume(r"C:\Apps\LocalRuntime\runtime.exe")
    assert written == [(RUNONCE_KEY, "LocalRuntimeSetup",
                        r'"C:\Apps\LocalRuntime\runtime.exe" setup --resume')]


def test_image_selection_follows_architecture():
    assert "amd64" in make(arch="amd64").image().url
    assert "arm64" in make(arch="arm64").image().url
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/providers/test_wsl_remedy.py -v`
Expected: FAIL — `ImportError: cannot import name 'RUNONCE_KEY'`

- [ ] **Step 3: Write minimal implementation**

In `runtime/providers/wsl2.py`, add imports and module constants:

```python
import shutil
import sys

from ..core.images import WSL_IMAGES
from .wsl_checks import diagnose_wsl2, preflight_checks

RUNONCE_KEY = r"Software\Microsoft\Windows\CurrentVersion\RunOnce"
_RESUME_VALUE_NAME = "LocalRuntimeSetup"


def _default_facts() -> dict:
    import subprocess
    from .wsl_encoding import decode_wsl

    def wmi(query: str) -> str:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", query],
                             capture_output=True)
        return out.stdout.decode("utf-8", "replace").strip()

    version = subprocess.run(["wsl.exe", "--version"], capture_output=True)
    return {
        "wsl_version_text": decode_wsl(version.stdout),
        "build": sys.getwindowsversion().build,
        "hypervisor_present":
            wmi("(Get-CimInstance Win32_ComputerSystem).HypervisorPresent") == "True",
        "firmware_virtualization":
            wmi("(Get-CimInstance Win32_Processor).VirtualizationFirmwareEnabled") == "True",
        "free_gb": shutil.disk_usage(sys.prefix).free / 1024 ** 3,
    }


def _default_elevator(exe: str, args: list[str]) -> int:
    import ctypes
    # SW_SHOWNORMAL=1. ShellExecuteW returns >32 on success; the UAC prompt
    # being declined returns 5 (ACCESS_DENIED).
    result = ctypes.windll.shell32.ShellExecuteW(
        None, "runas", exe, " ".join(args), None, 1)
    return 0 if result > 32 else int(result)


def _default_registry_writer(key: str, name: str, value: str) -> None:
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key) as handle:
        winreg.SetValueEx(handle, name, 0, winreg.REG_SZ, value)


def _default_arch() -> str:
    import platform
    return "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "amd64"
```

Extend `Wsl2Provider.__init__` with the injected collaborators and a flag:

```python
    def __init__(self, distro="runtime-vm", install_dir: Path | None = None,
                 rootfs: Path | None = None, wsl="wsl.exe", runner=_default_runner,
                 facts=_default_facts, elevator=_default_elevator,
                 registry_writer=_default_registry_writer, arch=None):
        self.distro = distro
        self.install_dir = Path(install_dir) if install_dir else None
        self.rootfs = Path(rootfs) if rootfs else None
        self.wsl = wsl
        self._run = runner
        self._facts = facts
        self._elevate = elevator
        self._write_registry = registry_writer
        self._arch = arch or _default_arch()
        self._features_enabled = False
```

Add the new methods:

```python
    def preflight(self) -> Diagnosis:
        return preflight_checks(**self._facts())

    def apply_remedy(self, remedy: str) -> None:
        if remedy not in ("enable_wsl_features", "update_wsl"):
            raise ValueError(f"unknown remedy: {remedy}")
        args = (["--install", "--no-distribution"] if remedy == "enable_wsl_features"
                else ["--update"])
        code = self._elevate(self.wsl, args)
        if code != 0:
            raise RuntimeError(
                f"`wsl {' '.join(args)}` needs administrator approval "
                f"(exit {code}). Re-run setup and choose Yes when Windows asks.")
        if remedy == "enable_wsl_features":
            self._features_enabled = True

    def reboot_required(self) -> bool:
        return self._features_enabled

    def register_resume(self, exe_path: str) -> None:
        self._write_registry(RUNONCE_KEY, _RESUME_VALUE_NAME,
                             f'"{exe_path}" setup --resume')

    def image(self):
        return WSL_IMAGES[self._arch]
```

In `runtime/providers/lima.py`, satisfy the same Protocol:

```python
    def preflight(self) -> Diagnosis:
        return self.is_supported()

    def apply_remedy(self, remedy: str) -> None:
        raise ValueError(f"unknown remedy: {remedy}")

    def reboot_required(self) -> bool:
        return False   # no OS features to enable; Lima needs no restart
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/providers/ -v`
Expected: PASS — 7 new tests, existing provider tests unchanged

- [ ] **Step 5: Commit**

```bash
git add runtime/providers/wsl2.py runtime/providers/lima.py tests/providers/test_wsl_remedy.py
git commit -m "feat: WSL2 remedies with scoped elevation and reboot resume"
```

---

## Task 9: The `setup` and `uninstall` commands

**Files:**
- Modify: `runtime/cli.py`
- Create: `tests/test_setup_cli.py`

**Interfaces:**
- Consumes: everything from Tasks 2–8.
- Produces: `runtime setup [--resume] [--headless]`; `runtime uninstall [--purge]`; `runtime.core.install.default_steps(provider, *, cache_dir, template_dir, domain) -> list[Step]`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_setup_cli.py`:

```python
from typer.testing import CliRunner

import runtime.cli as cli
from runtime.core.install import DeadEnd, RebootRequired
from runtime.core.provider import CheckResult, Completed, Diagnosis

runner = CliRunner()


class StubProvider:
    def __init__(self, diagnosis=None, reboot=False):
        self._diagnosis = diagnosis or Diagnosis([CheckResult("all good", True)])
        self._reboot = reboot
        self.resumed_with = None

    def preflight(self): return self._diagnosis
    def apply_remedy(self, remedy): pass
    def reboot_required(self): return self._reboot
    def register_resume(self, exe): self.resumed_with = exe
    def exists(self): return True
    def create(self): pass
    def exec(self, argv, *, root=False): return Completed(0, "", "")


def test_setup_reports_a_dead_end_in_plain_language(monkeypatch):
    blocked = Diagnosis([CheckResult(
        "CPU virtualization available", False,
        fix="Restart into BIOS/UEFI setup and enable Intel VT-x or AMD-V")])
    monkeypatch.setattr(cli, "_provider_factory", lambda: StubProvider(blocked))
    result = runner.invoke(cli.app, ["setup", "--headless"])
    assert result.exit_code == 1
    assert "BIOS" in result.stdout
    assert "Traceback" not in result.stdout, "non-technical users must not see a stack trace"


def test_setup_registers_resume_and_asks_for_a_restart(monkeypatch):
    provider = StubProvider(reboot=True)
    monkeypatch.setattr(cli, "_provider_factory", lambda: provider)
    result = runner.invoke(cli.app, ["setup", "--headless"])
    assert result.exit_code == 2, "a pending reboot is not a failure"
    assert "Restart your computer" in result.stdout
    assert provider.resumed_with is not None


def test_uninstall_requires_purge_to_destroy_the_vm(monkeypatch):
    monkeypatch.setattr(cli, "_provider_factory", lambda: StubProvider())
    result = runner.invoke(cli.app, ["uninstall"])
    assert result.exit_code == 1
    assert "--purge" in result.stdout
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_setup_cli.py -v`
Expected: FAIL — `No such command 'setup'`

- [ ] **Step 3: Write minimal implementation**

Add to `runtime/core/install.py`:

```python
def default_steps(provider, *, cache_dir, template_dir, domain,
                  exe_path: str) -> list[Step]:
    from .download import fetch

    def fetch_image():
        image = provider.image()
        dest = Path(cache_dir) / image.url.rsplit("/", 1)[-1]
        provider.rootfs = fetch(image, dest)

    def gate():
        if provider.reboot_required():
            provider.register_resume(exe_path)
        reboot_gate_step(provider)

    return [
        Step("preflight", lambda: preflight_step(provider)),
        Step("remediate", lambda: remediate_step(provider)),
        Step("reboot_gate", gate),
        Step("fetch_image", fetch_image),
        Step("create_vm", lambda: None if provider.exists() else provider.create()),
        Step("bootstrap", lambda: _bootstrap(provider)),
        Step("verify", lambda: verify_step(provider, template_dir, domain)),
    ]


def _bootstrap(provider) -> None:
    from .bootstrap import bootstrap
    bootstrap(provider)
```

Add to `runtime/cli.py`:

```python
@app.command()
def setup(resume: bool = typer.Option(False, "--resume"),
          headless: bool = typer.Option(False, "--headless")):
    """Set up everything: check the host, create the VM, install Docker."""
    import sys as _sys
    from pathlib import Path
    from runtime.core import constants
    from runtime.core.install import (
        DeadEnd, InstallError, InstallState, Progress, RebootRequired,
        default_steps, run_install,
    )
    from runtime.providers import default_install_dir

    root = default_install_dir().parent
    provider = _provider()
    state = InstallState(root / "install-state.json")
    steps = default_steps(
        provider,
        cache_dir=root / "cache",
        template_dir=Path(__file__).resolve().parent / "templates" / "nginx-hello",
        domain=constants.DEFAULT_DOMAIN,
        exe_path=_sys.executable,
    )

    def report(progress: Progress):
        if progress.status in ("running", "done", "failed"):
            typer.echo(f"[{progress.status:>7}] {progress.step} {progress.message}".rstrip())

    if not headless:
        from runtime.setup_app.app import run_window
        raise typer.Exit(code=run_window(steps, state))

    try:
        run_install(steps, state, report)
    except RebootRequired:
        typer.echo("\nRestart your computer. Setup will continue on its own "
                   "when you log back in.")
        raise typer.Exit(code=2)
    except DeadEnd as e:
        typer.echo(f"\nThis computer needs a change before setup can continue:\n\n{e}")
        raise typer.Exit(code=1)
    except InstallError as e:
        typer.echo(f"\nSetup failed during {e.step}:\n\n{e.message}")
        raise typer.Exit(code=1)
    typer.echo("\nSetup complete.")


@app.command()
def uninstall(purge: bool = typer.Option(False, "--purge")):
    """Remove the VM and all cached data. Destroys every project inside it."""
    if not purge:
        typer.echo("This destroys the VM and every project inside it. "
                   "Re-run with --purge to confirm.")
        raise typer.Exit(code=1)
    import shutil
    from runtime.core.install import InstallState
    from runtime.providers import default_install_dir
    _provider().destroy()
    root = default_install_dir().parent
    InstallState(root / "install-state.json").clear()
    shutil.rmtree(root / "cache", ignore_errors=True)
    typer.echo("Removed.")
```

Note: `default_steps` must raise `DeadEnd` out of `run_install` unwrapped. Adjust `run_install`'s `except Exception` clause to re-raise `DeadEnd` unchanged:

```python
        except (RebootRequired, DeadEnd):
            raise
```

placing that clause before the generic handler, and marking the step only for `RebootRequired` as already implemented.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_setup_cli.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Run the whole suite**

Run: `python3 -m pytest -q`
Expected: PASS, no regressions in the 82 baseline tests

- [ ] **Step 6: Commit**

```bash
git add runtime/cli.py runtime/core/install.py tests/test_setup_cli.py
git commit -m "feat: runtime setup and uninstall commands"
```

---

## Task 10: The tkinter setup window

**Files:**
- Create: `runtime/setup_app/__init__.py`, `runtime/setup_app/app.py`

**Interfaces:**
- Consumes: `Step`, `InstallState`, `run_install`, `Progress`, `DeadEnd`, `RebootRequired`, `InstallError` from `runtime/core/install.py`.
- Produces: `run_window(steps, state) -> int` returning the same exit codes as `--headless` (0 success, 1 failure, 2 reboot pending).

No unit tests: this module is tkinter wiring, and a test asserting that a Label displays text only re-asserts the framework. All logic under test lives in `core/install.py`. Verification is manual (Task 11 matrix).

- [ ] **Step 1: Create the package marker**

```bash
touch runtime/setup_app/__init__.py
```

- [ ] **Step 2: Write the window**

Create `runtime/setup_app/app.py`:

```python
from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import ttk

from runtime.core.install import (
    DeadEnd, InstallError, Progress, RebootRequired, run_install,
)

_LABELS = {
    "preflight": "Checking this computer",
    "remediate": "Turning on Windows features",
    "reboot_gate": "Restart needed",
    "fetch_image": "Downloading Linux image",
    "create_vm": "Creating the virtual machine",
    "bootstrap": "Installing Docker",
    "verify": "Testing the setup",
}
_MARKS = {"running": "…", "done": "✓", "failed": "✗", "skipped": "✓", "reboot": "!"}


def run_window(steps, state) -> int:
    events: queue.Queue = queue.Queue()
    outcome = {"code": 0, "message": ""}

    def worker():
        try:
            run_install(steps, state, events.put)
        except RebootRequired:
            outcome.update(code=2, message=(
                "Restart your computer.\n"
                "Setup will continue on its own when you log back in."))
        except DeadEnd as e:
            outcome.update(code=1, message=
                f"This computer needs a change before setup can continue:\n\n{e}")
        except InstallError as e:
            outcome.update(code=1, message=f"Setup failed during {e.step}:\n\n{e.message}")
        events.put(None)

    root = tk.Tk()
    root.title("Local Runtime Setup")
    root.geometry("560x420")

    rows: dict[str, tk.StringVar] = {}
    frame = ttk.Frame(root, padding=16)
    frame.pack(fill="both", expand=True)
    for step in steps:
        var = tk.StringVar(value=f"   {_LABELS.get(step.name, step.name)}")
        ttk.Label(frame, textvariable=var, font=("Segoe UI", 10)).pack(anchor="w", pady=2)
        rows[step.name] = var

    bar = ttk.Progressbar(frame, mode="indeterminate")
    bar.pack(fill="x", pady=12)
    bar.start(12)

    log = tk.Text(frame, height=8, wrap="word", state="disabled")
    log.pack(fill="both", expand=True)

    def append(text: str):
        log.configure(state="normal")
        log.insert("end", text + "\n")
        log.see("end")
        log.configure(state="disabled")

    def pump():
        while True:
            try:
                event = events.get_nowait()
            except queue.Empty:
                break
            if event is None:
                bar.stop()
                if outcome["message"]:
                    append("\n" + outcome["message"])
                ttk.Button(frame, text="Close", command=root.destroy).pack(pady=8)
                return
            _render(event, rows, append)
        root.after(100, pump)

    threading.Thread(target=worker, daemon=True).start()
    root.after(100, pump)
    root.mainloop()
    return outcome["code"]


def _render(event: Progress, rows, append) -> None:
    label = _LABELS.get(event.step, event.step)
    if event.step in rows:
        rows[event.step].set(f" {_MARKS.get(event.status, ' ')} {label}")
    if event.message:
        append(f"{label}: {event.message}")
```

- [ ] **Step 3: Verify it imports and the suite still passes**

Run: `python3 -c "import runtime.setup_app.app"` then `python3 -m pytest -q`
Expected: no import error; suite passes

- [ ] **Step 4: Commit**

```bash
git add runtime/setup_app/
git commit -m "feat: tkinter setup window"
```

---

## Task 11: Packaging

**Files:**
- Create: `packaging/windows/runtime.spec`, `packaging/windows/installer.iss`, `packaging/windows/build.ps1`
- Modify: `pyproject.toml`

**Interfaces:**
- Consumes: the `runtime` console script from `pyproject.toml`.
- Produces: `dist/LocalRuntimeSetup-<version>.exe`.

- [ ] **Step 1: Add the build-time dependency**

In `pyproject.toml`, change the dev extra — PyInstaller must not enter `[project.dependencies]`:

```toml
dev = ["pytest>=8", "pyinstaller>=6.10"]
```

- [ ] **Step 2: Write the PyInstaller spec**

Create `packaging/windows/runtime.spec`:

```python
# PyInstaller one-dir. One-file unpacks to a temp dir on every launch and is
# the mode antivirus heuristics dislike most; the installer wraps this anyway.
from PyInstaller.utils.hooks import collect_data_files

a = Analysis(
    ["../../runtime/cli.py"],
    pathex=["../.."],
    datas=[
        ("../../runtime/guest/bootstrap.sh", "runtime/guest"),
        ("../../runtime/guest/traefik.yml", "runtime/guest"),
        ("../../runtime/templates/nginx-hello/docker-compose.yml",
         "runtime/templates/nginx-hello"),
        ("../../runtime/providers/runtime.yaml", "runtime/providers"),
    ],
    hiddenimports=["runtime.setup_app.app"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, name="runtime", console=False)
coll = COLLECT(exe, a.binaries, a.datas, name="LocalRuntime")
```

The `datas` entries matter: `bootstrap.sh`, `traefik.yml`, and the nginx-hello template are read from disk at run time via `Path(__file__).parent`, so a freeze without them produces a binary that fails at the bootstrap and verify steps.

- [ ] **Step 3: Write the Inno Setup script**

Create `packaging/windows/installer.iss`:

```ini
#define AppName "Local Runtime"
#define AppVersion GetEnv("RUNTIME_VERSION")

[Setup]
AppName={#AppName}
AppVersion={#AppVersion}
DefaultDirName={localappdata}\Programs\LocalRuntime
DefaultGroupName={#AppName}
OutputDir=..\..\dist
OutputBaseFilename=LocalRuntimeSetup-{#AppVersion}
; Per-user install: no admin for the install itself. The only UAC prompt in
; the whole experience is the scoped one for enabling WSL2.
PrivilegesRequired=lowest
ArchitecturesInstallIn64BitMode=x64compatible
Compression=lzma2
SolidCompression=yes
WizardStyle=modern

[Files]
Source: "..\..\dist\LocalRuntime\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\Local Runtime Setup"; Filename: "{app}\runtime.exe"; Parameters: "setup"

[Run]
Filename: "{app}\runtime.exe"; Parameters: "setup"; \
  Description: "Set up Local Runtime now"; Flags: postinstall nowait skipifsilent

[UninstallRun]
; Destroys the VM and every project inside it before files are removed.
Filename: "{app}\runtime.exe"; Parameters: "uninstall --purge"; \
  Flags: runhidden; RunOnceId: "PurgeVm"

[Registry]
Root: HKCU; Subkey: "Environment"; ValueType: expandsz; ValueName: "Path"; \
  ValueData: "{olddata};{app}"; Check: NeedsAddPath('{app}')

[Code]
function NeedsAddPath(Param: string): boolean;
var OrigPath: string;
begin
  if not RegQueryStringValue(HKCU, 'Environment', 'Path', OrigPath) then
  begin Result := True; exit; end;
  Result := Pos(';' + ExpandConstant(Param) + ';', ';' + OrigPath + ';') = 0;
end;
```

- [ ] **Step 4: Write the build script**

Create `packaging/windows/build.ps1`:

```powershell
#Requires -Version 5.1
[CmdletBinding()]
param([string]$InnoSetup = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe")

$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path "$PSScriptRoot\..\..").Path
Push-Location $repo
try {
    $version = (Select-String -Path pyproject.toml -Pattern '^version = "(.+)"').Matches[0].Groups[1].Value
    Write-Host "==> Building Local Runtime $version"

    & .venv\Scripts\python.exe -m PyInstaller --noconfirm --clean `
        packaging\windows\runtime.spec
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed." }

    if (-not (Test-Path $InnoSetup)) {
        throw "Inno Setup not found at $InnoSetup. Install it or pass -InnoSetup."
    }
    $env:RUNTIME_VERSION = $version
    & $InnoSetup packaging\windows\installer.iss
    if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed." }

    Write-Host "==> dist\LocalRuntimeSetup-$version.exe" -ForegroundColor Green
} finally { Pop-Location }
```

- [ ] **Step 5: Build and smoke-test the artifact**

On the Windows machine:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
powershell -ExecutionPolicy Bypass -File .\packaging\windows\build.ps1
.\dist\LocalRuntime\runtime.exe version
```

Expected: `runtime 0.1.0`, and `dist\LocalRuntimeSetup-0.1.0.exe` exists.

- [ ] **Step 6: Commit**

```bash
git add packaging/ pyproject.toml
git commit -m "build: PyInstaller and Inno Setup packaging for Windows"
```

---

## Task 12: Manual verification matrix

**Files:**
- Create: `docs/installer-test-matrix.md`

No automated tests. This task records outcomes; it is the release gate.

- [ ] **Step 1: Write the matrix document**

Create `docs/installer-test-matrix.md` with a row per case, each recording date, build tested, and verbatim outcome:

| # | Machine state | What it proves | Pass condition |
|---|---|---|---|
| 1 | Windows with WSL2 already installed | The upgrade path | Setup completes, verify step returns 200 |
| 2 | **Clean Windows VM, WSL2 never enabled** | The real first-run path *including the reboot* | UAC prompt appears once; after restart, setup resumes unattended and completes |
| 3 | Windows VM with virtualization disabled in firmware | The dead-end message | Setup stops at preflight with the BIOS instruction and no stack trace |
| 4 | Re-run setup on an already-provisioned machine | Idempotency | All steps report skipped; exits 0 quickly |
| 5 | Uninstall | Cleanup | `wsl -l -v` no longer lists `runtime-vm`; cache directory gone |

- [ ] **Step 2: Run cases 1, 4, and 5 on the development machine**

Record verbatim output in the document.

- [ ] **Step 3: Run cases 2 and 3 in a VM**

Case 2 is the release gate — it is the only case that exercises `RunOnce` resume. Nested virtualization under Hyper-V is required. If nested virtualization is unavailable, record that fact explicitly rather than marking the case passed.

- [ ] **Step 4: Commit**

```bash
git add docs/installer-test-matrix.md
git commit -m "docs: installer manual test matrix with recorded outcomes"
```

---

## Self-Review

**Spec coverage.** §4 architecture → Tasks 1, 4, 9. §5 state machine, all eight steps → Tasks 3–6, 9 (`finish` is the successful return from `run_install`, not a separate Step; the CLI prints the completion message). §5 preflight table → Task 7. §5 scoped elevation → Task 8. §5 reboot resume → Task 8 + Task 9's `gate`. §5 fetch → Task 2. §5 verify → Task 6. §6 progress and error reporting → Tasks 9, 10. §7 packaging → Task 11. §8 uninstall → Task 9 + Task 11's `[UninstallRun]`. §11 testing → tests throughout + Task 12. §9 CI is explicitly optional and deliberately not a task.

**Deliberately deferred, matching the spec:** the "Copy diagnostics" clipboard button and the timestamped log file (§6) are not in a task — the window shows the failure and the log pane holds the detail. Add them when pilot feedback shows they are needed rather than building support tooling before there are users to support.

**Type consistency.** `CheckResult.remedy` (Task 1) is read by `Diagnosis.fixable` (Task 1), produced by `preflight_checks` (Task 7), consumed by `remediate_step` (Task 5) and `apply_remedy` (Task 8) — same string identifiers `enable_wsl_features` and `update_wsl` throughout. `Step`/`Progress`/`InstallState` signatures are defined in Tasks 3–4 and used unchanged in Tasks 5, 6, 9, 10. `Image` (Task 2) is returned by `provider.image()` (Task 8) and consumed by `fetch` (Task 2) in `default_steps` (Task 9). Exit codes 0/1/2 are identical between `--headless` (Task 9) and `run_window` (Task 10).

**One ordering note for the executor:** Task 9 amends `run_install`'s exception handling so `DeadEnd` propagates unwrapped. Task 4's tests must still pass afterwards — run the full suite at Task 9 Step 5, not just the new file.
