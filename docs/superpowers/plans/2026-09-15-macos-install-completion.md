# Finishing the macOS Install — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `EggieSetup-<version>.pkg` install Lima itself, land the user on a status screen that hands them the SSH credentials a coding agent needs, and replace the Windows-looking setup window with a drawn one.

**Architecture:** Two new install-surface members carry every platform difference — `provider.runtime()` inserts an `install_runtime` step that downloads and verifies a pinned Lima release, and `provider.access()` returns the connection facts the status screen renders without knowing what SSH is. `host/setup_app/` becomes a package of two screens (wizard, status) over a shared theme and widget layer, routed by a readiness probe.

**Tech Stack:** Python 3.12+, stdlib only on the host (`typer` is the sole runtime dependency), tkinter/ttk for the window, PyInstaller one-dir + `pkgbuild`/`productbuild` for the package.

**Spec:** `docs/superpowers/specs/2026-09-15-macos-install-completion-design.md`

## Global Constraints

- **`host/` never imports `agent/`.** `tests/host/test_no_agent_import.py` enforces it by AST.
- **No platform branching outside `host/providers/`.** `tests/test_no_platform_leak.py` greps `host/**/*.py` and `agent/**/*.py` for `sys.platform`, `platform.system()`, `os.name`. `platform.machine()` is not grepped but belongs in `providers/` all the same.
- **The host's only runtime dependency is `typer`.** `tests/host/test_host_dependencies.py` fails on a declared or imported second one. No `requests`, no `yaml`.
- **Nothing from `agent/` or `engine/` may be added to a PyInstaller `datas` list.** `tests/host/test_frozen_bundle.py` checks every spec under `packaging/`. Lima is fetched at setup time and must never appear there.
- **`provider.exec()` returns a `Completed` and never raises.** Every caller checks `.ok` itself. A dropped result is a silent success — that bug has shipped in this repo twice.
- **Names declared in both `host/core/constants.py` and `agent/core/constants.py` must hold the same value** (`tests/test_constants_agree.py`, intersection only).
- **Python style:** `from __future__ import annotations`, frozen dataclasses for value types, function-local imports inside CLI command bodies.
- **Pinned Lima version: `2.2.0`.** Archive URLs and digests, verbatim:
  - `arm64` — `https://github.com/lima-vm/lima/releases/download/v2.2.0/lima-2.2.0-Darwin-arm64.tar.gz`
    sha256 `bbdef91774885a0d05f7b048c4eb89ae2bcf3a0c252ae7ca7934e63df76d93c3`
  - `x86_64` — `https://github.com/lima-vm/lima/releases/download/v2.2.0/lima-2.2.0-Darwin-x86_64.tar.gz`
    sha256 `0d6f99c19f6e4bc3c92730c4c29d929e6927f0cb0a0ba1a84383367135a8ff31`
  These are the main release tarballs, which carry the **native-arch** guest agent. `lima-additional-guestagents-*` is for running a guest of a different architecture and is deliberately not fetched.
- **Tests never spawn `wsl.exe` or `limactl`, never touch a real VM, never reach the network, and never open a window.** Inject a fake runner, a fake `fetch`, or a `FakeProvider`.
- **Repo-file lookups in tests resolve from `__file__`,** never from the working directory, and assert they scanned something. A cwd-relative `Path("host")` has passed vacuously here three times.

**Run the suite with** `python3 -m pytest -q` (444 tests today, ~8s). If `tmp_path` fixtures error, prefix with `TMPDIR=<writable dir>` — a sandbox artifact, not a code bug.

---

### Task 1: Downloads report progress

`fetch()` already accepts `on_progress`; nothing passes one, so a 391 MB rootfs download sits behind an indeterminate bar on Windows today. The new Lima download needs the same channel, so build it once, first.

**Files:**
- Modify: `host/core/install.py` (the `Progress` and `Step` dataclasses, `run_install`, `default_steps`)
- Modify: `host/cli.py` (the `report` closure inside `setup`)
- Test: `tests/host/test_install_progress.py` (create)

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `Progress(step: str, status: str, message: str = "", fraction: float | None = None)`
  - `Step(name, run, always_run=False, action="", label="", progress=False)` — when `progress=True`, `run_install` calls `step.run(emit)` with `emit(done: int, total: int) -> None`; otherwise it calls `step.run()` as today.
  - `label` is the human-readable name for a step whose text comes from the provider; empty means "the UI decides".

- [ ] **Step 1: Write the failing test**

```python
# tests/host/test_install_progress.py
"""A download step must be able to say how far along it is.

fetch() has taken an on_progress callback since it was written and nothing
ever passed one, so the installer's only long step reported nothing at all.
"""
from host.core.install import InstallState, Progress, Step, run_install


def _events(steps, tmp_path):
    seen = []
    run_install(steps, InstallState(tmp_path / "state.json"), seen.append)
    return seen


def test_a_progress_step_is_handed_an_emitter(tmp_path):
    def run(emit):
        emit(50, 200)
        emit(200, 200)

    seen = _events([Step("fetch_image", run, progress=True)], tmp_path)
    fractions = [e.fraction for e in seen if e.fraction is not None]
    assert fractions == [0.25, 1.0]
    assert all(e.step == "fetch_image" and e.status == "running"
               for e in seen if e.fraction is not None)


def test_a_plain_step_is_still_called_with_no_arguments(tmp_path):
    # Every existing step takes no arguments, and every test that builds a toy
    # step does too. Adding progress must not change that contract.
    seen = _events([Step("finish", lambda: "done")], tmp_path)
    assert [(e.step, e.status) for e in seen] == [("finish", "running"), ("finish", "done")]


def test_a_zero_length_download_reports_no_fraction(tmp_path):
    # Content-Length can be absent, and 0/0 must not raise inside the installer.
    seen = _events([Step("fetch_image", lambda emit: emit(0, 0), progress=True)], tmp_path)
    assert [e for e in seen if e.fraction is not None] == []


def test_progress_defaults_to_no_fraction():
    assert Progress("verify", "running").fraction is None
```

- [ ] **Step 2: Run it and watch it fail**

Run: `python3 -m pytest tests/host/test_install_progress.py -q`
Expected: FAIL — `Step.__init__() got an unexpected keyword argument 'progress'`.

- [ ] **Step 3: Implement**

In `host/core/install.py`, extend the two dataclasses:

```python
@dataclass(frozen=True)
class Progress:
    step: str
    status: str          # running | done | skipped | failed | reboot
    message: str = ""
    # How far through a long step we are, 0.0-1.0, and only for a step that
    # declared `progress=True`. None on every other event, including the
    # `running` one that opens such a step.
    fraction: float | None = None
```

```python
@dataclass(frozen=True)
class Step:
    name: str
    run: Callable[..., str | None]
    always_run: bool = False
    action: str = ""
    # Set by a provider that names its own step -- "Installing Lima" is Lima's
    # word, not the installer's. Empty means the UI picks the text.
    label: str = ""
    # When true, `run` is called with an `emit(done, total)` callable instead
    # of with nothing. Only the two download steps set it; every other step,
    # and every toy step in the tests, keeps the no-argument signature.
    progress: bool = False
```

In `run_install`, replace the single `message = step.run()` call:

```python
        report(Progress(step.name, "running"))
        try:
            message = step.run(_emitter(step.name, report)) if step.progress else step.run()
```

and add the emitter beside it:

```python
def _emitter(name: str, report: Callable[[Progress], None]):
    """A fraction channel for one step, throttled to whole percents.

    `fetch` calls back once per megabyte, which is 391 events for the rootfs.
    The UI drains a queue on a timer and would cope, but a headless run would
    not, and neither would a log file.
    """
    last = [-1]

    def emit(done: int, total: int) -> None:
        if not total:
            return          # no Content-Length: nothing truthful to report
        percent = int(done * 100 / total)
        if percent == last[0]:
            return
        last[0] = percent
        report(Progress(name, "running", fraction=done / total))

    return emit
```

In `default_steps`, make `step()` carry the two new fields and mark `fetch_image`:

```python
    def step(name: str, run, *, always_run: bool = False, label: str = "",
             progress: bool = False) -> Step:
        return Step(name, run, always_run=always_run, action=_ACTIONS.get(name, ""),
                    label=label, progress=progress)
```

```python
    def fetch_image(emit):
        fetch(image, rootfs, on_progress=emit)
```

```python
        steps.append(step("fetch_image", fetch_image, always_run=True, progress=True))
```

In `host/cli.py`, the headless `report` closure must ignore fraction events — it prints one line per event, and 100 of them for a download is not a report:

```python
    def report(progress: Progress):
        if progress.fraction is not None:
            return          # the window draws a bar; a terminal would print 100 lines
        if progress.status not in ("running", "done", "failed"):
            return
        typer.echo(f"[{progress.status:>7}] {progress.step}")
        if progress.message:
            typer.echo(progress.message)
```

- [ ] **Step 4: Run the new tests, then the whole suite**

Run: `python3 -m pytest tests/host/test_install_progress.py -q && python3 -m pytest -q`
Expected: the new file passes; the full suite stays green (444 passing today — the count grows, nothing regresses).

- [ ] **Step 5: Commit**

```bash
git add tests/host/test_install_progress.py host/core/install.py host/cli.py
git commit -m "feat(install): steps can report download progress as a fraction"
```

---

### Task 2: `lima_install` — fetch, verify and unpack a pinned Lima

The module that knows about the Lima release and nothing else. No provider, no step, no UI yet: this task ends with a tested function that puts a working `limactl` on disk.

**Files:**
- Create: `host/providers/lima_install.py`
- Test: `tests/host/test_lima_install.py` (create)

**Interfaces:**
- Consumes: `host.core.images.Image(url, sha256)`, `host.core.download.fetch(image, dest, *, opener, on_progress)`.
- Produces:
  - `LIMA_VERSION: str` = `"2.2.0"`
  - `ARCHIVES: dict[str, Image]` keyed `"arm64"` / `"x86_64"`
  - `archive_for(machine: str) -> Image`
  - `managed_root(root: Path) -> Path` → `root / "lima"`
  - `managed_limactl(root: Path) -> Path` → `root / "lima" / "bin" / "limactl"`
  - `install(root: Path, *, fetch=fetch, runner=_default_runner, on_progress=None) -> Path`
  - `LimaInstallError(RuntimeError)`

- [ ] **Step 1: Write the failing test**

```python
# tests/host/test_lima_install.py
"""Putting a pinned Lima on disk.

Downloading is the easy half. The half that matters is refusing to believe the
work happened: this repo has already shipped two provisioning steps that
reported success for work that never ran, so `install()` ends by running the
binary and reading its version back.
"""
import io
import tarfile
from pathlib import Path

import pytest

from host.core.images import Image
from host.providers import lima_install


class FakeRunner:
    """Same shape as the providers' runners: returns an object with
    returncode/stdout/stderr, and never raises."""

    def __init__(self, stdout=b"limactl version 2.2.0\n", returncode=0):
        self.calls = []
        self._out, self._rc = stdout, returncode

    def __call__(self, argv):
        self.calls.append(argv)
        class R:
            returncode = self._rc
            stdout = self._out
            stderr = b""
        return R()


def _tarball(dest: Path, members: dict[str, bytes], *, mode=0o755) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(dest, "w:gz") as tar:
        for name, payload in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            info.mode = mode
            tar.addfile(info, io.BytesIO(payload))
    return dest


def _good_tarball(dest: Path) -> Path:
    return _tarball(dest, {"bin/limactl": b"#!/bin/sh\n",
                           "share/lima/lima-guestagent.Linux-aarch64": b"x"})


def _fetcher(payload_for):
    """Stands in for download.fetch: writes bytes where it was told to, and
    records that it was asked. The real one is digest-checked and resumable;
    neither behaviour belongs in these tests."""
    calls = []

    def fetch(image, dest, *, on_progress=None):
        calls.append((image, Path(dest)))
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        payload_for(Path(dest))
        if on_progress:
            on_progress(10, 10)
        return Path(dest)

    fetch.calls = calls
    return fetch


# --- which archive ---

@pytest.mark.parametrize("machine,key", [
    ("arm64", "arm64"), ("aarch64", "arm64"), ("ARM64", "arm64"),
    ("x86_64", "x86_64"), ("amd64", "x86_64"), ("AMD64", "x86_64"),
])
def test_archive_for_normalizes_the_machine_name(machine, key):
    assert lima_install.archive_for(machine) is lima_install.ARCHIVES[key]


def test_archive_for_refuses_an_architecture_we_have_no_build_for():
    with pytest.raises(lima_install.LimaInstallError) as caught:
        lima_install.archive_for("ppc64le")
    assert "ppc64le" in str(caught.value)


def test_both_architectures_are_pinned_with_a_real_digest():
    # The digests come from the release's SHA256SUMS. A truncated or
    # placeholder value here disables `fetch`'s only integrity check.
    assert set(lima_install.ARCHIVES) == {"arm64", "x86_64"}
    for key, image in lima_install.ARCHIVES.items():
        assert lima_install.LIMA_VERSION in image.url
        assert key in image.url
        assert len(image.sha256) == 64
        assert set(image.sha256) <= set("0123456789abcdef")


# --- installing ---

def test_install_unpacks_the_archive_and_returns_the_binary(tmp_path):
    fetch = _fetcher(_good_tarball)
    runner = FakeRunner()

    path = lima_install.install(tmp_path, fetch=fetch, runner=runner)

    assert path == tmp_path / "lima" / "bin" / "limactl"
    assert path.is_file()
    assert (tmp_path / "lima" / "share" / "lima").is_dir()
    # The share directory has to stay a sibling of bin/: limactl finds its own
    # templates and guest agents relative to the executable.
    assert path.parent.parent == tmp_path / "lima"


def test_install_verifies_the_binary_by_running_it(tmp_path):
    runner = FakeRunner()
    lima_install.install(tmp_path, fetch=_fetcher(_good_tarball), runner=runner)
    assert runner.calls == [[str(tmp_path / "lima" / "bin" / "limactl"), "--version"]]


def test_install_fails_when_the_unpacked_binary_reports_another_version(tmp_path):
    runner = FakeRunner(stdout=b"limactl version 1.0.0\n")
    with pytest.raises(lima_install.LimaInstallError) as caught:
        lima_install.install(tmp_path, fetch=_fetcher(_good_tarball), runner=runner)
    assert "1.0.0" in str(caught.value)
    assert lima_install.LIMA_VERSION in str(caught.value)


def test_install_fails_when_the_binary_will_not_run(tmp_path):
    runner = FakeRunner(stdout=b"", returncode=126)
    with pytest.raises(lima_install.LimaInstallError):
        lima_install.install(tmp_path, fetch=_fetcher(_good_tarball), runner=runner)


def test_install_fails_when_the_archive_has_no_limactl(tmp_path):
    fetch = _fetcher(lambda dest: _tarball(dest, {"share/lima/templates": b"x"}))
    with pytest.raises(lima_install.LimaInstallError) as caught:
        lima_install.install(tmp_path, fetch=fetch, runner=FakeRunner())
    assert "bin/limactl" in str(caught.value)


def test_install_is_skipped_when_the_pinned_version_is_already_there(tmp_path):
    fetch = _fetcher(_good_tarball)
    first = lima_install.install(tmp_path, fetch=fetch, runner=FakeRunner())
    second = lima_install.install(tmp_path, fetch=fetch, runner=FakeRunner())
    assert first == second
    assert len(fetch.calls) == 1, "a re-run must not download Lima again"


def test_install_replaces_a_different_version_that_is_already_there(tmp_path):
    lima_install.install(tmp_path, fetch=_fetcher(_good_tarball), runner=FakeRunner())
    (tmp_path / "lima" / ".version").write_text("1.0.0")
    fetch = _fetcher(_good_tarball)
    lima_install.install(tmp_path, fetch=fetch, runner=FakeRunner())
    assert len(fetch.calls) == 1
    assert (tmp_path / "lima" / ".version").read_text().strip() == lima_install.LIMA_VERSION


def test_install_reports_download_progress(tmp_path):
    seen = []
    lima_install.install(tmp_path, fetch=_fetcher(_good_tarball), runner=FakeRunner(),
                         on_progress=lambda done, total: seen.append((done, total)))
    assert seen == [(10, 10)]


# --- extraction safety ---

def test_extraction_refuses_an_absolute_member(tmp_path):
    # tarfile's data filter normalizes an absolute name instead of refusing it,
    # which is why this check is explicit -- the same finding as
    # agent/core/files.extract_archive.
    fetch = _fetcher(lambda dest: _tarball(dest, {"/etc/passwd": b"pwned",
                                                  "bin/limactl": b"#!/bin/sh\n"}))
    with pytest.raises(lima_install.LimaInstallError):
        lima_install.install(tmp_path, fetch=fetch, runner=FakeRunner())
    assert not (tmp_path / "lima").exists()


def test_extraction_refuses_a_parent_escape(tmp_path):
    fetch = _fetcher(lambda dest: _tarball(dest, {"../escaped": b"pwned",
                                                  "bin/limactl": b"#!/bin/sh\n"}))
    with pytest.raises(Exception):
        lima_install.install(tmp_path, fetch=fetch, runner=FakeRunner())
    assert not (tmp_path.parent / "escaped").exists()


def test_a_failed_install_leaves_the_previous_one_in_place(tmp_path):
    lima_install.install(tmp_path, fetch=_fetcher(_good_tarball), runner=FakeRunner())
    (tmp_path / "lima" / ".version").write_text("1.0.0")
    broken = _fetcher(lambda dest: _tarball(dest, {"share/lima/x": b"x"}))
    with pytest.raises(lima_install.LimaInstallError):
        lima_install.install(tmp_path, fetch=broken, runner=FakeRunner())
    assert (tmp_path / "lima" / "bin" / "limactl").is_file()
```

- [ ] **Step 2: Run it and watch it fail**

Run: `python3 -m pytest tests/host/test_lima_install.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'host.providers.lima_install'`.

- [ ] **Step 3: Implement**

```python
# host/providers/lima_install.py
from __future__ import annotations

# macOS only, and here rather than in host/core/ for the reason every other
# platform fact is: this module knows an architecture name, a release URL and
# a tarball layout, and none of that may leak into the installer.

import os
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path

from ..core.download import fetch as _fetch
from ..core.images import Image

LIMA_VERSION = "2.2.0"

_RELEASE = ("https://github.com/lima-vm/lima/releases/download/"
            f"v{LIMA_VERSION}/lima-{LIMA_VERSION}-Darwin-%s.tar.gz")

# Digests from the release's SHA256SUMS. `fetch` refuses anything else, so a
# wrong value here is a failed install, never a silently substituted binary.
#
# These are the main tarballs, which carry the guest agent for the host's own
# architecture. lima-additional-guestagents-* exists for running a guest of a
# different architecture and is deliberately not fetched: eggie.yaml asks for
# a native-arch Ubuntu, and the extra download is 38 MB nobody would use.
ARCHIVES: dict[str, Image] = {
    "arm64": Image(_RELEASE % "arm64",
                   "bbdef91774885a0d05f7b048c4eb89ae2bcf3a0c252ae7ca7934e63df76d93c3"),
    "x86_64": Image(_RELEASE % "x86_64",
                    "0d6f99c19f6e4bc3c92730c4c29d929e6927f0cb0a0ba1a84383367135a8ff31"),
}

_MACHINES = {"arm64": "arm64", "aarch64": "arm64",
             "x86_64": "x86_64", "amd64": "x86_64"}


class LimaInstallError(RuntimeError):
    """Lima could not be put on disk, or what landed there does not run."""


def _default_runner(argv):
    return subprocess.run(argv, capture_output=True)


def archive_for(machine: str) -> Image:
    key = _MACHINES.get(machine.lower())
    if key is None:
        raise LimaInstallError(
            f"there is no Lima build for this Mac's processor ({machine})")
    return ARCHIVES[key]


def managed_root(root: Path) -> Path:
    return Path(root) / "lima"


def managed_limactl(root: Path) -> Path:
    # bin/ and share/ must stay siblings: limactl resolves share/lima
    # relative to its own executable, so a flattened copy finds no templates
    # and no guest agent.
    return managed_root(root) / "bin" / "limactl"


def _machine() -> str:
    import platform
    return platform.machine()


def install(root: Path, *, fetch=_fetch, runner=_default_runner,
            on_progress=None, machine: str | None = None) -> Path:
    """Put the pinned Lima under `root/lima` and return the path to limactl.

    Idempotent: a matching `.version` and an executable binary is the whole
    check, so a re-run costs one file read and no network.

    Downloading rather than bundling is not only a size decision. build.sh
    signs the app with `codesign --deep`, which re-signs every Mach-O in the
    bundle and would strip the com.apple.security.virtualization entitlement
    Lima ad-hoc signs limactl with -- breaking vz on signed builds only, on
    the user's machine. Bytes unpacked from the release tarball keep Lima's
    own signature, and urllib attaches no com.apple.quarantine xattr, so
    neither Gatekeeper nor our signing is in this path at all.
    """
    root = Path(root)
    target = managed_root(root)
    binary = managed_limactl(root)
    version_file = target / ".version"

    if binary.is_file() and os.access(binary, os.X_OK):
        try:
            if version_file.read_text().strip() == LIMA_VERSION:
                return binary
        except OSError:
            pass

    archive = fetch(archive_for(machine or _machine()),
                    root / "cache" / f"lima-{LIMA_VERSION}.tar.gz",
                    on_progress=on_progress)

    staging = Path(tempfile.mkdtemp(prefix="lima-", dir=str(root)))
    try:
        _extract(Path(archive), staging)
        staged_binary = staging / "bin" / "limactl"
        if not staged_binary.is_file():
            raise LimaInstallError(
                "the Lima download did not contain bin/limactl")
        staged_binary.chmod(0o755)
        (staging / ".version").write_text(LIMA_VERSION + "\n")
        _require_version(staged_binary, runner)
        _swap(staging, target)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return binary


def _extract(archive: Path, into: Path) -> None:
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar.getmembers():
            # `filter="data"` below rejects a `..` escape and a link out of the
            # tree, but silently *normalizes* an absolute name rather than
            # refusing it -- the same finding as agent/core/files.py. Refusing
            # it here is the check that is actually missing.
            if member.name.startswith("/") or Path(member.name).is_absolute():
                raise LimaInstallError(
                    f"the Lima download contains an absolute path: {member.name}")
        tar.extractall(into, filter="data")


def _require_version(binary: Path, runner) -> None:
    """Run it. An unpacked tarball is not a working binary, and the difference
    is invisible until create_vm fails minutes later with something unrelated."""
    result = runner([str(binary), "--version"])
    output = ((result.stdout or b"") + (result.stderr or b"")).decode("utf-8", "replace")
    if result.returncode != 0:
        raise LimaInstallError(
            f"the downloaded limactl did not run (exit {result.returncode})"
            + (f": {output.strip()}" if output.strip() else "."))
    if LIMA_VERSION not in output:
        raise LimaInstallError(
            f"the downloaded limactl reports {output.strip()!r}, "
            f"not version {LIMA_VERSION}")


def _swap(staging: Path, target: Path) -> None:
    """Move the verified tree into place, keeping the old one until the last
    moment: a half-installed Lima is worse than the previous version."""
    previous = target.with_name(target.name + ".previous")
    shutil.rmtree(previous, ignore_errors=True)
    if target.exists():
        os.replace(target, previous)
    try:
        os.replace(staging, target)
    except OSError:
        if previous.exists():
            os.replace(previous, target)
        raise
    shutil.rmtree(previous, ignore_errors=True)
```

Note for the implementer: `install()` moves the staging directory into place, so the `shutil.rmtree(staging)` in the `finally` is a no-op on success and a cleanup on failure. That is intended.

- [ ] **Step 4: Run the tests**

Run: `python3 -m pytest tests/host/test_lima_install.py -q && python3 -m pytest -q`
Expected: the new file passes; the suite stays green. In particular `tests/host/test_host_dependencies.py` must still pass — this module imports only stdlib plus `host.core`.

- [ ] **Step 5: Commit**

```bash
git add host/providers/lima_install.py tests/host/test_lima_install.py
git commit -m "feat(lima): fetch, verify and unpack a pinned Lima release"
```

---

### Task 3: Setup installs Lima — `runtime()` and the `install_runtime` step

Wire Task 2 into the install list, stop dead-ending on a missing Lima, and make the managed copy the one the provider uses.

**Files:**
- Modify: `host/core/provider.py` (add `Runtime`)
- Modify: `host/core/install.py` (`default_steps`, `_ACTIONS`)
- Modify: `host/providers/lima.py` (`default_data_root`, `find_limactl`, `__init__`, `is_supported`, `preflight`, `runtime`)
- Modify: `host/providers/wsl2.py` (`runtime` returning `None`)
- Modify: `host/providers/__init__.py` (`default_install_dir`, `get_provider`)
- Modify: `host/providers/eggie.yaml` (the x86_64 image)
- Test: `tests/host/test_default_steps.py`, `tests/host/test_lima.py`, `tests/host/test_factory.py`

**Interfaces:**
- Consumes: `lima_install.install`, `lima_install.managed_limactl`, `Progress`/`Step` from Task 1.
- Produces:
  - `host.core.provider.Runtime(label: str, run: Callable[[Callable[[int, int], None] | None], None])`
  - `provider.runtime() -> Runtime | None` on both providers
  - `host.providers.lima.default_data_root() -> Path`
  - `LimaProvider(..., data_root: Path | None = None)`
  - `find_limactl(name="limactl", *, which=shutil.which, prefixes=BREW_PREFIXES, managed: Path | None = None)`

- [ ] **Step 1: Write the failing tests**

```python
# tests/host/test_default_steps.py  — append
def test_a_provider_with_a_runtime_gets_an_install_step_after_preflight(tmp_path):
    provider = SelfImagingProvider()
    calls = []
    provider.runtime_value = Runtime("Installing Lima", lambda emit: calls.append(emit))
    names = [s.name for s in build(provider, tmp_path)]
    assert names[:2] == ["preflight", "install_runtime"]
    assert "fetch_image" not in names


def test_the_install_step_carries_the_providers_own_label(tmp_path):
    provider = SelfImagingProvider()
    provider.runtime_value = Runtime("Installing Lima", lambda emit: None)
    step = next(s for s in build(provider, tmp_path) if s.name == "install_runtime")
    assert step.label == "Installing Lima"
    assert step.progress is True
    # Never recorded as done: what it produces is a directory a user can
    # delete, and re-deriving it costs one file read.
    assert step.always_run is True
    assert step.action, "a failed download needs a sentence telling the user what to do"


def test_a_provider_with_no_runtime_gets_no_install_step(tmp_path):
    assert "install_runtime" not in [s.name for s in build(FakeProvider(), tmp_path)]
```

Add to the top of that file: `from host.core.provider import Runtime`, and give the two fake providers the member:

```python
class FakeProvider:
    ...
    runtime_value = None

    def runtime(self):
        return self.runtime_value
```

```python
# tests/host/test_lima.py  — append
import host.providers.lima_install as lima_install
from host.core.provider import Runtime


def test_find_limactl_prefers_the_managed_copy_over_homebrew(tmp_path):
    managed = tmp_path / "lima" / "bin" / "limactl"
    managed.parent.mkdir(parents=True)
    managed.write_text("#!/bin/sh\n")
    managed.chmod(0o755)
    found = find_limactl(which=lambda name: "/opt/homebrew/bin/limactl",
                         managed=managed)
    assert found == str(managed)


def test_find_limactl_falls_back_to_the_path_before_setup_has_run(tmp_path):
    # A source checkout that has never run setup has no managed copy; a
    # developer's brew install is what makes `eggie doctor` answerable there.
    missing = tmp_path / "lima" / "bin" / "limactl"
    found = find_limactl(which=lambda name: "/opt/homebrew/bin/limactl",
                         managed=missing)
    assert found == "/opt/homebrew/bin/limactl"


def test_preflight_no_longer_dead_ends_on_a_missing_lima():
    # Installing Lima is setup's job now. A preflight that stops for it is
    # setup refusing to do its own work.
    diagnosis = make(FakeRunner()).preflight()
    assert diagnosis.dead_ends == []


def test_doctor_still_reports_a_missing_lima_and_names_setup_as_the_fix():
    provider = LimaProvider(name="eggie-vm", limactl="/nowhere/limactl",
                            runner=FakeRunner(), data_root=Path("/nowhere"))
    diagnosis = provider.is_supported()
    lima_check = next(c for c in diagnosis.checks if "Lima" in c.label)
    assert not lima_check.ok
    assert "setup" in lima_check.fix.lower()
    assert "brew" not in lima_check.fix.lower()


def test_runtime_installs_into_the_providers_data_root(tmp_path, monkeypatch):
    seen = {}

    def fake_install(root, *, on_progress=None):
        seen["root"] = root
        seen["on_progress"] = on_progress
        return root / "lima" / "bin" / "limactl"

    monkeypatch.setattr(lima_install, "install", fake_install)
    provider = LimaProvider(name="eggie-vm", runner=FakeRunner(), data_root=tmp_path)
    runtime = provider.runtime()
    assert isinstance(runtime, Runtime)
    assert "Lima" in runtime.label
    emit = lambda done, total: None
    runtime.run(emit)
    assert seen == {"root": tmp_path, "on_progress": emit}


def test_the_wsl2_provider_has_nothing_to_install():
    from host.providers.wsl2 import Wsl2Provider
    assert Wsl2Provider(arch="amd64").runtime() is None
```

```python
# tests/host/test_factory.py  — append
def test_the_lima_data_root_is_the_parent_of_the_install_dir(monkeypatch):
    # One literal, in lima.py. Two would drift, and the symptom would be a
    # limactl setup installed somewhere the provider never looks.
    from host.providers import default_install_dir
    from host.providers.lima import default_data_root
    monkeypatch.setattr(sys, "platform", "darwin")
    assert default_install_dir().parent == default_data_root()
```

```python
# tests/host/test_provider_surface.py — change one line
INSTALL_SURFACE = {"image", "register_resume", "location", "terminal",
                   "remediable", "runtime", "access"}
```

(`access` lands in Task 4; this line is written once, and Task 3 leaves `test_every_provider_answers_what_the_install_list_asks_of_it` failing until then. If you would rather keep the suite green between tasks, add `"runtime"` here now and `"access"` in Task 4.)

- [ ] **Step 2: Run them and watch them fail**

Run: `python3 -m pytest tests/host/test_default_steps.py tests/host/test_lima.py tests/host/test_factory.py -q`
Expected: FAIL — `AttributeError: 'LimaProvider' object has no attribute 'runtime'` and `TypeError: find_limactl() got an unexpected keyword argument 'managed'`.

- [ ] **Step 3: Implement**

In `host/core/provider.py`, beside the other value types:

```python
@dataclass(frozen=True)
class Runtime:
    """A program the VM platform needs that setup installs for the user.

    `None` from `provider.runtime()` means the platform ships it -- wsl.exe is
    part of Windows. A value means one step, named by `label`, calling `run`
    with the installer's fraction emitter.
    """
    label: str
    run: Callable[[Callable[[int, int], None] | None], None]
```

with `from typing import Callable, Protocol, runtime_checkable` at the top.

In `host/core/install.py`, add the action sentence:

```python
    "install_runtime": "Lima could not be downloaded — the internet connection "
                       "was unavailable, or the download did not match its "
                       "checksum. Run setup again; the download continues from "
                       "where it stopped.",
```

and insert the step in `default_steps`, immediately after `preflight`:

```python
    steps = [step("preflight", lambda: preflight_step(provider))]
    # What the VM platform itself needs, which on macOS is Lima. None on
    # Windows, where wsl.exe is part of the OS -- the same shape as image():
    # the step is absent rather than present and skipped.
    runtime = provider.runtime()
    if runtime is not None:
        steps.append(step("install_runtime", runtime.run, always_run=True,
                          label=runtime.label, progress=True))
```

In `host/providers/lima.py`:

```python
def default_data_root() -> Path:
    """Everything the host keeps for itself on this platform: the VM directory,
    the download cache and the managed Lima. The single literal -- the provider
    factory's default_install_dir() is derived from it, so the two cannot
    disagree about where setup put limactl."""
    return Path.home() / ".local" / "share" / "eggie"
```

```python
def find_limactl(name: str = "limactl", *, which=shutil.which,
                 prefixes=BREW_PREFIXES, managed: Path | None = None) -> str:
    """Absolute path to limactl, or `name` unchanged if it was not found.

    The managed copy wins whenever it exists: setup installs a pinned version,
    and every assumption LimaProvider makes about Lima's on-disk layout is an
    assumption about that version. A user's Homebrew Lima is never removed,
    never upgraded and never used once ours is there -- the fallback below
    exists for one case, a source checkout that has never run setup.
    """
    if os.sep in name:
        return name             # an explicit path: a test, or a bundled copy
    if managed is not None and os.access(managed, os.X_OK):
        return str(managed)
    found = which(name)
    if found:
        return found
    for prefix in prefixes:
        candidate = os.path.join(prefix, name)
        if os.access(candidate, os.X_OK):
            return candidate
    return name
```

Add `data_root` to `__init__` (after `lima_home`):

```python
                 lima_home: Path | None = None, data_root: Path | None = None):
        ...
        self.data_root = Path(data_root) if data_root else default_data_root()
```

Replace `is_supported` and `preflight`:

```python
    def is_supported(self) -> Diagnosis:
        """What `eggie doctor` prints: the whole truth about this machine.

        Wider than preflight() on purpose, and the reverse of the WSL2
        provider, where preflight is the richer of the two. Setup installs Lima
        itself now, so preflight must not stop for it -- but a user running
        doctor still deserves to be told it is missing, and told that setup is
        what fixes it.
        """
        checks = list(self._os_checks())
        from shutil import which
        present = os.access(self.limactl, os.X_OK) or which(self.limactl) is not None
        checks.append(CheckResult(
            f"Lima {lima_install.LIMA_VERSION}", present,
            None if present else "run Eggie setup, which installs Lima for you"))
        return Diagnosis(checks)

    def _os_checks(self):
        import platform
        release = platform.mac_ver()[0]
        major = int(release.split(".")[0]) if release.split(".")[0].isdigit() else 0
        # vz, which eggie.yaml asks for, is macOS 13+. The .pkg refuses to
        # install below that; a source checkout has nothing stopping it.
        yield CheckResult(
            f"macOS 13 or newer (found {release or 'unknown'})", major >= 13,
            None if major >= 13 else
            "Eggie needs macOS 13 or newer; this Mac cannot run it")

    def preflight(self) -> Diagnosis:
        """What setup gates on before it installs anything. Only facts about
        this computer that no step can change."""
        return Diagnosis(list(self._os_checks()))

    def runtime(self) -> Runtime | None:
        return Runtime(f"Installing Lima {lima_install.LIMA_VERSION}",
                       lambda emit: lima_install.install(self.data_root,
                                                         on_progress=emit))
```

with `from . import lima_install` and `from ..core.provider import Completed, Diagnosis, CheckResult, Runtime` at the top. Import `lima_install` as a module, not its names — the test monkeypatches `lima_install.install`.

In `host/providers/wsl2.py`, beside `image()`:

```python
    def runtime(self):
        """Nothing to install: wsl.exe ships with Windows, and what it needs
        turned on is `remediable` above, not a download."""
        return None
```

In `host/providers/__init__.py`:

```python
from .lima import LimaProvider, default_data_root, find_limactl
from .lima_install import managed_limactl


def default_install_dir() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA", str(Path.home()))
        return Path(base) / "Eggie" / "vm"
    return default_data_root() / "vm"
```

```python
    if sys.platform == "darwin":
        root = default_data_root()
        return LimaProvider(config=Path(__file__).parent / "eggie.yaml",
                            limactl=find_limactl(managed=managed_limactl(root)),
                            data_root=root)
```

In `host/providers/eggie.yaml`, add the second image beside the first:

```yaml
images:
  - location: "https://cloud-images.ubuntu.com/releases/24.04/release/ubuntu-24.04-server-cloudimg-arm64.img"
    arch: "aarch64"
  # distribution.xml stamps the building machine's arch into hostArchitectures,
  # so an Intel build is a supported artifact and needs an image it can boot.
  - location: "https://cloud-images.ubuntu.com/releases/24.04/release/ubuntu-24.04-server-cloudimg-amd64.img"
    arch: "x86_64"
```

- [ ] **Step 4: Run the tests**

Run: `python3 -m pytest tests/host -q && python3 -m pytest -q`
Expected: everything passes except `test_every_provider_answers_what_the_install_list_asks_of_it`, which stays red until Task 4 adds `access()`. Note the failure and move on — or stage `INSTALL_SURFACE` in two halves as described above.

- [ ] **Step 5: Commit**

```bash
git add host/core/provider.py host/core/install.py host/providers tests/host
git commit -m "feat(macos): setup installs Lima instead of telling the user to"
```

---

### Task 4: `access()` — the facts a coding agent needs

**Files:**
- Modify: `host/core/provider.py` (`Access`, `AccessField`)
- Modify: `host/providers/lima.py` (`access`)
- Modify: `host/providers/wsl2.py` (`access`)
- Modify: `tests/host/test_provider_surface.py`
- Test: `tests/host/test_access.py` (create)

**Interfaces:**
- Produces:
  - `AccessField(label: str, value: str)`
  - `Access(headline: str, summary: str, command: str, fields: tuple[AccessField, ...] = (), note: str = "")`
  - `provider.access() -> Access` on both providers

- [ ] **Step 1: Write the failing test**

```python
# tests/host/test_access.py
"""What the status screen tells a user who wants their coding agent in the VM.

The screen renders an Access value and knows nothing about SSH. That is the
point: WSL has no SSH server, and a screen that assumed one would be a
platform branch in the UI layer.
"""
from pathlib import Path

from host.providers.lima import LimaProvider
from host.providers.wsl2 import Wsl2Provider


class FakeRunner:
    def __init__(self, stdout=b"", returncode=0):
        self.calls = []
        self._out, self._rc = stdout, returncode

    def __call__(self, argv):
        self.calls.append(argv)
        class R:
            returncode = self._rc
            stdout = self._out
            stderr = b""
        return R()


SSH_CONFIG = """\
# This SSH config file is generated by lima
Host lima-eggie-vm
  IdentityFile "/Users/you/.lima/_config/user"
  User you
  Hostname 127.0.0.1
  Port 39022
  StrictHostKeyChecking no
"""


def _provider(tmp_path, *, written=True):
    home = tmp_path / ".lima"
    if written:
        (home / "eggie-vm").mkdir(parents=True)
        (home / "eggie-vm" / "ssh.config").write_text(SSH_CONFIG)
    return LimaProvider(name="eggie-vm", runner=FakeRunner(), lima_home=home,
                        data_root=tmp_path)


def _fields(access) -> dict:
    return {f.label: f.value for f in access.fields}


def test_lima_reads_the_credentials_lima_wrote(tmp_path):
    access = _provider(tmp_path).access()
    assert _fields(access) == {
        "Host": "127.0.0.1",
        "Port": "39022",
        "User": "you",
        "Identity file": "/Users/you/.lima/_config/user",
    }
    assert not access.note


def test_lima_hands_over_a_command_that_needs_no_ssh_config_of_the_users(tmp_path):
    access = _provider(tmp_path).access()
    assert access.command == (
        f"ssh -F {tmp_path / '.lima' / 'eggie-vm' / 'ssh.config'} lima-eggie-vm")


def test_lima_falls_back_to_the_declared_port_before_the_first_boot(tmp_path):
    # ssh.config is written when the VM is created. Showing nothing until then
    # would make the screen useless exactly when a user is most lost.
    access = _provider(tmp_path, written=False).access()
    assert _fields(access)["Port"] == "39022"
    assert _fields(access)["Host"] == "127.0.0.1"
    assert access.note, "the screen must say these are defaults, not live values"


def test_lima_parsing_is_case_insensitive_and_unquotes(tmp_path):
    home = tmp_path / ".lima" / "eggie-vm"
    home.mkdir(parents=True)
    (home / "ssh.config").write_text(
        'HOST lima-eggie-vm\n  hostname 127.0.0.1\n  PORT 40022\n'
        '  user someone\n  identityfile "/tmp/key"\n')
    access = LimaProvider(name="eggie-vm", runner=FakeRunner(),
                          lima_home=tmp_path / ".lima",
                          data_root=tmp_path).access()
    assert _fields(access)["Port"] == "40022"
    assert _fields(access)["Identity file"] == "/tmp/key"


def test_wsl2_does_not_pretend_to_have_an_ssh_server():
    access = Wsl2Provider(distro="eggie-vm", arch="amd64").access()
    assert access.command == "wsl -d eggie-vm"
    assert "ssh" not in access.command.lower()
    assert _fields(access)["Virtual machine"] == "eggie-vm"
    assert r"\\wsl$\eggie-vm\opt\eggie\projects" in _fields(access).values()


def test_both_providers_answer_with_something_a_screen_can_render():
    for access in (Wsl2Provider(distro="eggie-vm", arch="amd64").access(),):
        assert access.headline and access.summary and access.command
        assert all(f.label and f.value for f in access.fields)
```

- [ ] **Step 2: Run it and watch it fail**

Run: `python3 -m pytest tests/host/test_access.py -q`
Expected: FAIL — `AttributeError: 'LimaProvider' object has no attribute 'access'`.

- [ ] **Step 3: Implement**

In `host/core/provider.py`:

```python
@dataclass(frozen=True)
class AccessField:
    label: str
    value: str


@dataclass(frozen=True)
class Access:
    """How a person -- or their coding agent -- gets a shell inside the VM.

    Rendered by the status screen, which must not know what SSH is: a WSL
    distro runs no SSH server, and a screen that assumed one would be a
    platform branch in the UI layer.
    """
    headline: str
    summary: str
    command: str
    fields: tuple[AccessField, ...] = ()
    note: str = ""
```

In `host/providers/lima.py`:

```python
# Declared in eggie.yaml's `ssh.localPort`, and what the screen shows before
# the VM has ever been started and written its own ssh.config.
DECLARED_SSH_PORT = "39022"

_SSH_KEYS = {"hostname": "Host", "port": "Port", "user": "User",
             "identityfile": "Identity file"}


def parse_ssh_config(text: str) -> dict[str, str]:
    """The four fields an editor's Remote-SSH dialog asks for.

    Lima writes this file when it creates the VM, and LimaProvider.forward()
    already hands the same path to `ssh -F`. Parsing it here gives that
    assumption a second reader: if Lima ever moves or renames it, the status
    screen says so in plain sight instead of a port forward failing quietly.
    """
    found: dict[str, str] = {}
    for line in text.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2:
            continue
        label = _SSH_KEYS.get(parts[0].lower())
        if label and label not in found:
            found[label] = parts[1].strip().strip('"')
    return found
```

and on the class:

```python
    def access(self) -> Access:
        config = self._ssh_config()
        note = ""
        try:
            found = parse_ssh_config(config.read_text())
        except OSError:
            found = {}
        if not found:
            note = ("The virtual machine has not been started yet, so these are "
                    "the values Eggie asks Lima for rather than the ones Lima "
                    "has written down. Run setup, then open this window again.")
        import getpass
        fields = (
            AccessField("Host", found.get("Host", LOOPBACK)),
            AccessField("Port", found.get("Port", DECLARED_SSH_PORT)),
            AccessField("User", found.get("User", getpass.getuser())),
            AccessField("Identity file", found.get(
                "Identity file", str(self.lima_home / "_config" / "user"))),
        )
        return Access(
            headline="Connect a coding agent",
            summary=("Your coding agent runs inside the virtual machine, where "
                     "Docker and the eggie command already are. Open a shell "
                     "there with the command below, or point an editor's "
                     "Remote-SSH at these values."),
            command=f"ssh -F {config} lima-{self.name}",
            fields=fields,
            note=note)
```

In `host/providers/wsl2.py`:

```python
    def access(self) -> Access:
        """No SSH, and no pretending otherwise. A WSL distro runs no sshd; the
        way in is wsl.exe, and the way to the files is the UNC path Explorer
        and every Windows editor already understand."""
        from ..core import constants
        return Access(
            headline="Connect a coding agent",
            summary=("Your coding agent runs inside the virtual machine, where "
                     "Docker and the eggie command already are. Open a shell "
                     "there with the command below."),
            command=f"wsl -d {self.distro}",
            fields=(
                AccessField("Virtual machine", self.distro),
                AccessField("Projects folder",
                            rf"\\wsl$\{self.distro}"
                            + constants.GUEST_PROJECTS.replace("/", "\\")),
            ))
```

with `Access, AccessField` added to each file's import from `..core.provider`.

- [ ] **Step 4: Run the tests**

Run: `python3 -m pytest tests/host -q && python3 -m pytest -q`
Expected: green, including `test_every_provider_answers_what_the_install_list_asks_of_it` and `test_the_install_surface_stays_out_of_the_lifecycle_protocol`.

- [ ] **Step 5: Commit**

```bash
git add host/core/provider.py host/providers tests/host
git commit -m "feat(providers): access() answers how to get a shell in the VM"
```

---

### Task 5: The readiness probe

**Files:**
- Create: `host/core/status.py`
- Modify: `host/core/constants.py` (add `APP_VERSION`)
- Modify: `host/cli.py` (`version` reads the constant)
- Test: `tests/host/test_status_probe.py` (create)

**Interfaces:**
- Produces:
  - `Readiness(vm_exists: bool, vm_reachable: bool, engine_version: str | None, agent_api: int | None, problem: str = "")` with a `ready` property
  - `probe(provider, *, client_factory=None) -> Readiness`
  - `host.core.constants.APP_VERSION = "0.1.0"`

- [ ] **Step 1: Write the failing test**

```python
# tests/host/test_status_probe.py
"""Is this machine set up? Asked cheaply, and answered without raising.

The window calls this before it draws anything, so a provider that throws, a
VM that is gone and an agent that is silent all have to come back as facts.
"""
from host.core.provider import Completed
from host.core.status import Readiness, probe


class FakeProvider:
    def __init__(self, *, exists=True, reachable=True, engine="0.1.0"):
        self._exists, self._reachable, self._engine = exists, reachable, engine
        self.calls = []

    def exists(self):
        return self._exists

    def exec(self, argv, *, root=False):
        self.calls.append(argv)
        if not self._reachable:
            return Completed(1, "", "the VM is not running")
        if argv[0] == "cat":
            return (Completed(0, self._engine, "") if self._engine
                    else Completed(1, "", "No such file or directory"))
        return Completed(0, "", "")


def _client(health=None, error=None):
    class Client:
        def health(self):
            if error:
                raise error
            return health or {"api": 1}
    return lambda provider: Client()


def test_a_provisioned_machine_is_ready():
    result = probe(FakeProvider(), client_factory=_client())
    assert result == Readiness(vm_exists=True, vm_reachable=True,
                               engine_version="0.1.0", agent_api=1)
    assert result.ready


def test_a_missing_vm_stops_before_touching_the_guest():
    provider = FakeProvider(exists=False)
    result = probe(provider, client_factory=_client())
    assert not result.ready
    assert not result.vm_exists
    assert provider.calls == [], "nothing may be executed in a VM that is not there"


def test_a_vm_that_is_not_running_is_not_ready():
    result = probe(FakeProvider(reachable=False), client_factory=_client())
    assert result.vm_exists and not result.vm_reachable
    assert result.engine_version is None
    assert not result.ready


def test_a_vm_without_the_engine_is_not_ready():
    result = probe(FakeProvider(engine=""), client_factory=_client())
    assert result.vm_reachable and result.engine_version is None
    assert not result.ready


def test_a_silent_agent_is_not_ready_and_the_reason_is_kept():
    result = probe(FakeProvider(), client_factory=_client(error=OSError("refused")))
    assert result.engine_version == "0.1.0"
    assert result.agent_api is None
    assert "refused" in result.problem
    assert not result.ready


def test_probe_never_raises():
    class Exploding:
        def exists(self):
            raise RuntimeError("limactl is not installed")

    result = probe(Exploding(), client_factory=_client())
    assert not result.ready
    assert "limactl is not installed" in result.problem


def test_an_unsupported_agent_api_is_not_ready():
    from host.core import constants
    unsupported = max(constants.SUPPORTED_API) + 1
    result = probe(FakeProvider(), client_factory=_client(health={"api": unsupported}))
    assert result.agent_api == unsupported
    assert not result.ready
```

- [ ] **Step 2: Run it and watch it fail**

Run: `python3 -m pytest tests/host/test_status_probe.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'host.core.status'`.

- [ ] **Step 3: Implement**

```python
# host/core/status.py
from __future__ import annotations

from dataclasses import dataclass

from . import constants


@dataclass(frozen=True)
class Readiness:
    """What the window knows before it draws anything.

    Every field is a fact it managed to establish; `problem` holds the first
    one it could not, in the words whoever refused gave. Nothing here raises --
    a probe that threw would leave the window with nothing to show at all.
    """
    vm_exists: bool = False
    vm_reachable: bool = False
    engine_version: str | None = None
    agent_api: int | None = None
    problem: str = ""

    @property
    def ready(self) -> bool:
        return (self.vm_exists and self.vm_reachable
                and bool(self.engine_version)
                and self.agent_api in constants.SUPPORTED_API)


def _default_client_factory(provider):
    from host.client import AgentClient
    return AgentClient.for_provider(provider)


def probe(provider, *, client_factory=None) -> Readiness:
    """Ask the three questions that decide which screen opens.

    `vm_reachable` is `exec(["true"]).ok` rather than a new provider member for
    "is it running": reaching the guest is the fact that matters, both
    platforms answer it identically, and the Protocol already offers it.
    """
    client_factory = client_factory or _default_client_factory
    try:
        if not provider.exists():
            return Readiness()
        if not provider.exec(["true"]).ok:
            return Readiness(vm_exists=True)
        marker = provider.exec(["cat", constants.ENGINE_MARKER])
        engine = marker.stdout.strip() if marker.ok else ""
        if not engine:
            return Readiness(vm_exists=True, vm_reachable=True)
        try:
            api = client_factory(provider).health().get("api", 1)
        except Exception as e:
            return Readiness(vm_exists=True, vm_reachable=True,
                             engine_version=engine, problem=f"{e}")
        return Readiness(vm_exists=True, vm_reachable=True,
                         engine_version=engine, agent_api=api)
    except Exception as e:
        return Readiness(problem=f"{e}")
```

In `host/core/constants.py`, add near the top:

```python
# The desktop app's own version, shown on the status screen and in the
# diagnostics text. Bumped with pyproject.toml's.
APP_VERSION = "0.1.0"
```

and in `host/cli.py`:

```python
@app.command()
def version():
    """Print the Eggie version."""
    from host.core import constants
    typer.echo(f"eggie {constants.APP_VERSION}")
```

- [ ] **Step 4: Run the tests**

Run: `python3 -m pytest tests/host/test_status_probe.py -q && python3 -m pytest -q`
Expected: green. Watch `tests/test_setup_cli.py` — if it asserts the literal `eggie 0.1.0`, it still passes, since `APP_VERSION` is that string.

- [ ] **Step 5: Commit**

```bash
git add host/core/status.py host/core/constants.py host/cli.py tests/host/test_status_probe.py
git commit -m "feat(host): a readiness probe that answers without raising"
```

---

### Task 6: The theme and the drawn widgets

**Files:**
- Create: `host/setup_app/theme.py`
- Create: `host/setup_app/widgets.py`
- Test: `tests/host/test_setup_app_logic.py` (create)

**Interfaces:**
- Produces:
  - `theme.Palette(bg, surface, text, muted, accent, accent_text, ok, error, border)`, `theme.LIGHT`, `theme.DARK`
  - `theme.luminance(rgb16: tuple[int, int, int]) -> float`
  - `theme.palette_for(rgb16: tuple[int, int, int]) -> Palette`
  - `theme.load_fonts(root) -> dict[str, tkinter.font.Font]` with keys `title`, `heading`, `body`, `small`, `mono`
  - `widgets.format_elapsed(seconds: float) -> str`
  - `widgets.GLYPHS: dict[str, str]` keyed by step status
  - `widgets.Header`, `widgets.StepList`, `widgets.ProgressBar`, `widgets.Button`, `widgets.FieldRow`

- [ ] **Step 1: Write the failing test**

```python
# tests/host/test_setup_app_logic.py
"""The parts of the window that are not the window.

No test opens a Tk root: everything worth asserting here is a pure function,
which is the reason the theme and the widgets are separate modules at all.
"""
import pytest

from host.setup_app import theme, widgets


def test_a_light_background_selects_the_light_palette():
    # winfo_rgb returns 16-bit channels: 65535 is full white.
    assert theme.palette_for((65535, 65535, 65535)) is theme.LIGHT
    assert theme.palette_for((60000, 60000, 60000)) is theme.LIGHT


def test_a_dark_background_selects_the_dark_palette():
    assert theme.palette_for((0, 0, 0)) is theme.DARK
    assert theme.palette_for((7710, 7710, 7967)) is theme.DARK


def test_luminance_weights_green_most():
    # Not an average: a mid green reads brighter to the eye than a mid blue,
    # and picking the palette by a flat mean gets dark mode wrong on macOS.
    green = theme.luminance((0, 40000, 0))
    blue = theme.luminance((0, 0, 40000))
    assert green > blue


def test_every_palette_defines_every_colour():
    for palette in (theme.LIGHT, theme.DARK):
        for name, value in vars(palette).items():
            assert isinstance(value, str) and value.startswith("#"), name
            assert len(value) == 7, f"{name}={value}"


@pytest.mark.parametrize("seconds,text", [
    (0, ""), (0.4, ""), (1, "1s"), (9.6, "9s"), (59, "59s"),
    (60, "1m 00s"), (145, "2m 25s"), (3600, "60m 00s"),
])
def test_elapsed_time_is_only_shown_once_there_is_some(seconds, text):
    # A step that took 400ms showing "0s" is noise on every row of the list.
    assert widgets.format_elapsed(seconds) == text


def test_there_is_a_glyph_for_every_status_run_install_can_report():
    from host.core.install import Progress
    # The five statuses run_install emits, and the sixth the UI starts rows in.
    for status in ("pending", "running", "done", "skipped", "failed", "reboot"):
        assert status in widgets.GLYPHS
    assert Progress("x", "running").status in widgets.GLYPHS
```

- [ ] **Step 2: Run it and watch it fail**

Run: `python3 -m pytest tests/host/test_setup_app_logic.py -q`
Expected: FAIL — `ImportError: cannot import name 'theme' from 'host.setup_app'`.

- [ ] **Step 3: Implement**

```python
# host/setup_app/theme.py
from __future__ import annotations

# Colours, fonts and metrics for both screens.
#
# Nothing here asks which operating system it is on, and it must not start:
# light and dark are chosen from the ttk theme's own background, which the OS
# has already set correctly. That keeps dark mode working on both platforms and
# keeps tests/test_no_platform_leak.py passing.

from dataclasses import dataclass
from tkinter import font as tkfont


@dataclass(frozen=True)
class Palette:
    bg: str
    surface: str
    text: str
    muted: str
    accent: str
    accent_text: str
    ok: str
    error: str
    border: str


LIGHT = Palette(bg="#f6f6f7", surface="#ffffff", text="#16161a",
                muted="#6b6b76", accent="#c2571a", accent_text="#ffffff",
                ok="#2e7d4f", error="#b3261e", border="#dcdce1")

DARK = Palette(bg="#1c1c1f", surface="#26262b", text="#f2f2f4",
               muted="#a0a0ab", accent="#e2802f", accent_text="#1c1c1f",
               ok="#5cc98a", error="#ff8a80", border="#3a3a41")


def luminance(rgb16: tuple[int, int, int]) -> float:
    """Perceived brightness, 0.0-1.0, from tkinter's 16-bit channels.

    Weighted, not averaged: a flat mean calls macOS's graphite window chrome
    light and hands a light palette to a dark window.
    """
    red, green, blue = (channel / 65535 for channel in rgb16)
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def palette_for(rgb16: tuple[int, int, int]) -> Palette:
    return DARK if luminance(rgb16) < 0.5 else LIGHT


def palette_for_root(root) -> Palette:
    """The palette matching the window the OS just gave us."""
    from tkinter import ttk
    background = ttk.Style(root).lookup("TFrame", "background") or "#ffffff"
    try:
        return palette_for(root.winfo_rgb(background))
    except Exception:
        return LIGHT


def load_fonts(root) -> dict[str, tkfont.Font]:
    """The system UI font at five sizes.

    Copies of the named fonts tkinter resolves per platform, never a family
    name of our own: every label in this app used to ask for "Segoe UI", which
    does not exist on macOS, so the whole window fell back to a default the
    layout was not measured against.
    """
    base = tkfont.nametofont("TkDefaultFont")
    size = base.cget("size") or 13
    def derived(delta: int, weight: str = "normal") -> tkfont.Font:
        made = base.copy()
        made.configure(size=size + delta, weight=weight)
        return made
    mono = tkfont.nametofont("TkFixedFont").copy()
    mono.configure(size=size - 1)
    return {"title": derived(7, "bold"), "heading": derived(2, "bold"),
            "body": derived(0), "small": derived(-1), "mono": mono}


PAD = 20            # window margin
ROW_HEIGHT = 30     # one step row
GLYPH = 22          # the circle at the left of a step row
RADIUS = 8          # card and button corner radius
```

```python
# host/setup_app/widgets.py
from __future__ import annotations

# Everything the two screens draw. Canvas-based rather than ttk, because the
# step list needs states ttk has no widget for and the window is the whole
# product on macOS -- it is the only thing a user sees before their first
# project runs.

import tkinter as tk

from . import theme

# The five statuses run_install reports, plus the one a row starts in.
GLYPHS = {"pending": "", "running": "", "done": "✓", "skipped": "✓",
          "failed": "✗", "reboot": "!"}


def format_elapsed(seconds: float) -> str:
    """Whole seconds, and nothing at all below one.

    A list of rows each claiming "0s" says less than a list of rows saying
    nothing, and the fast steps here really are instant.
    """
    whole = int(seconds)
    if whole < 1:
        return ""
    if whole < 60:
        return f"{whole}s"
    return f"{whole // 60}m {whole % 60:02d}s"


def rounded(canvas: tk.Canvas, x1, y1, x2, y2, radius, **kwargs) -> int:
    """A rounded rectangle. Canvas has no such primitive, and a square-cornered
    card next to macOS's own chrome reads as a rendering failure."""
    points = [x1 + radius, y1, x2 - radius, y1, x2, y1, x2, y1 + radius,
              x2, y2 - radius, x2, y2, x2 - radius, y2, x1 + radius, y2,
              x1, y2, x1, y2 - radius, x1, y1 + radius, x1, y1]
    return canvas.create_polygon(points, smooth=True, **kwargs)


class Header(tk.Canvas):
    """The accent band across the top: the product's name, and one line saying
    what this window is doing."""

    HEIGHT = 84

    def __init__(self, parent, palette: theme.Palette, fonts, title: str, subtitle: str):
        super().__init__(parent, height=self.HEIGHT, highlightthickness=0,
                         bg=palette.accent)
        self._palette, self._fonts = palette, fonts
        self.create_text(theme.PAD, 26, text=title, anchor="w",
                         fill=palette.accent_text, font=fonts["title"])
        self._subtitle = self.create_text(
            theme.PAD, 58, text=subtitle, anchor="w",
            fill=palette.accent_text, font=fonts["small"])

    def say(self, subtitle: str) -> None:
        self.itemconfigure(self._subtitle, text=subtitle)


class StepList(tk.Canvas):
    """One row per install step: a glyph, a label, and how long it took.

    Redrawn whole on every change. The list is a dozen rows at most, and a
    partial redraw is how a step row ends up showing two states at once.
    """

    def __init__(self, parent, palette: theme.Palette, fonts, labels: list[tuple[str, str]]):
        super().__init__(parent, highlightthickness=0, bg=palette.bg,
                         height=theme.ROW_HEIGHT * len(labels) + 8)
        self._palette, self._fonts = palette, fonts
        self._labels = labels                       # [(step name, text)]
        self._state = {name: "pending" for name, _ in labels}
        self._elapsed = {name: 0.0 for name, _ in labels}
        self._spin = 0
        self.bind("<Configure>", lambda event: self.redraw())

    def set_state(self, name: str, state: str, elapsed: float = 0.0) -> None:
        if name in self._state:
            self._state[name] = state
            self._elapsed[name] = elapsed
            self.redraw()

    def tick(self) -> None:
        """Advance the running row's arc. Called on the window's timer."""
        self._spin = (self._spin + 30) % 360
        if "running" in self._state.values():
            self.redraw()

    def redraw(self) -> None:
        self.delete("all")
        width = self.winfo_width() or 520
        for index, (name, text) in enumerate(self._labels):
            y = 12 + index * theme.ROW_HEIGHT
            state = self._state[name]
            self._glyph(theme.PAD + theme.GLYPH // 2, y + 8, state)
            colour = (self._palette.muted if state in ("pending", "skipped")
                      else self._palette.error if state == "failed"
                      else self._palette.text)
            self.create_text(theme.PAD + theme.GLYPH + 12, y + 8, text=text,
                             anchor="w", fill=colour, font=self._fonts["body"])
            elapsed = format_elapsed(self._elapsed[name])
            if elapsed:
                self.create_text(width - theme.PAD, y + 8, text=elapsed, anchor="e",
                                 fill=self._palette.muted, font=self._fonts["small"])

    def _glyph(self, cx: int, cy: int, state: str) -> None:
        radius = theme.GLYPH // 2
        box = (cx - radius, cy - radius, cx + radius, cy + radius)
        if state == "running":
            self.create_oval(*box, outline=self._palette.border, width=2)
            self.create_arc(*box, start=self._spin, extent=100, style="arc",
                            outline=self._palette.accent, width=2)
            return
        if state == "pending":
            self.create_oval(*box, outline=self._palette.border, width=2)
            return
        fill = {"done": self._palette.ok, "skipped": self._palette.muted,
                "failed": self._palette.error, "reboot": self._palette.accent}[state]
        self.create_oval(*box, fill=fill, outline=fill)
        self.create_text(cx, cy, text=GLYPHS[state], fill=self._palette.surface,
                         font=self._fonts["small"])


class ProgressBar(tk.Canvas):
    """Determinate, because every long step here can say how far along it is.

    The bar this replaces was indeterminate through a 391 MB download, which
    is the one moment a user most wants to know whether to wait."""

    HEIGHT = 6

    def __init__(self, parent, palette: theme.Palette):
        super().__init__(parent, height=self.HEIGHT, highlightthickness=0, bg=palette.bg)
        self._palette = palette
        self._value = 0.0
        self.bind("<Configure>", lambda event: self._draw())

    def set(self, value: float) -> None:
        self._value = max(0.0, min(1.0, value))
        self._draw()

    def _draw(self) -> None:
        self.delete("all")
        width = self.winfo_width() or 520
        self.create_rectangle(0, 0, width, self.HEIGHT,
                              fill=self._palette.border, outline="")
        if self._value > 0:
            self.create_rectangle(0, 0, width * self._value, self.HEIGHT,
                                  fill=self._palette.accent, outline="")


class Button(tk.Canvas):
    """A drawn button, in two weights. ttk's would not take the accent colour
    on macOS's aqua theme, which is the one place this app's look matters."""

    HEIGHT = 34

    def __init__(self, parent, palette: theme.Palette, fonts, text: str,
                 command, *, primary: bool = False, width: int = 150):
        super().__init__(parent, height=self.HEIGHT, width=width,
                         highlightthickness=0, bg=palette.bg)
        self._palette, self._fonts = palette, fonts
        self._text, self._command, self._primary = text, command, primary
        self._enabled = True
        self._draw(hover=False)
        self.bind("<Enter>", lambda e: self._enabled and self._draw(hover=True))
        self.bind("<Leave>", lambda e: self._enabled and self._draw(hover=False))
        self.bind("<Button-1>", lambda e: self._enabled and self._command())

    def enable(self, enabled: bool) -> None:
        self._enabled = enabled
        self._draw(hover=False)

    def _draw(self, *, hover: bool) -> None:
        self.delete("all")
        width = int(self["width"])
        if not self._enabled:
            fill, text_colour = self._palette.border, self._palette.muted
        elif self._primary:
            fill, text_colour = self._palette.accent, self._palette.accent_text
        else:
            fill, text_colour = self._palette.surface, self._palette.text
        rounded(self, 1, 1, width - 1, self.HEIGHT - 1, theme.RADIUS,
                fill=fill, outline=self._palette.border)
        if hover and self._enabled:
            rounded(self, 1, 1, width - 1, self.HEIGHT - 1, theme.RADIUS,
                    fill="", outline=self._palette.accent, width=2)
        self.create_text(width // 2, self.HEIGHT // 2, text=self._text,
                         fill=text_colour, font=self._fonts["body"])
        self.configure(cursor="hand2" if self._enabled else "")


class FieldRow(tk.Frame):
    """One label, one monospaced value, one Copy button.

    Read-only on purpose: the app never writes to the user's ~/.ssh/config.
    """

    def __init__(self, parent, palette: theme.Palette, fonts, label: str, value: str):
        super().__init__(parent, bg=palette.bg)
        self._value = value
        tk.Label(self, text=label, width=14, anchor="w", bg=palette.bg,
                 fg=palette.muted, font=fonts["small"]).pack(side="left")
        entry = tk.Entry(self, bg=palette.surface, fg=palette.text,
                         readonlybackground=palette.surface,
                         font=fonts["mono"], relief="flat",
                         highlightthickness=1, highlightbackground=palette.border)
        entry.insert(0, value)
        # Read-only rather than disabled: the text must stay selectable, so a
        # user can drag out one field without the Copy button at all.
        entry.configure(state="readonly")
        entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self._copy = Button(self, palette, fonts, "Copy", self._on_copy, width=70)
        self._copy.pack(side="left")

    def _on_copy(self) -> None:
        self.clipboard_clear()
        self.clipboard_append(self._value)
        self._copy._text = "Copied"
        self._copy._draw(hover=False)
        self.after(1200, self._restore)

    def _restore(self) -> None:
        self._copy._text = "Copy"
        self._copy._draw(hover=False)
```

- [ ] **Step 4: Run the tests**

Run: `python3 -m pytest tests/host/test_setup_app_logic.py -q && python3 -m pytest -q`
Expected: green, including `tests/test_no_platform_leak.py` — neither new module may contain `sys.platform`, `platform.system()` or `os.name`.

- [ ] **Step 5: Commit**

```bash
git add host/setup_app/theme.py host/setup_app/widgets.py tests/host/test_setup_app_logic.py
git commit -m "feat(setup-app): a theme and drawn widgets, with no hardcoded font"
```

---

### Task 7: The wizard screen

**Files:**
- Create: `host/setup_app/wizard.py`
- Test: `tests/host/test_setup_app_logic.py` (extend)

**Interfaces:**
- Consumes: `theme`, `widgets`, `host.core.install.{Progress, Step, run_install, RebootRequired, DeadEnd, InstallError, RESUME_NOTICE}`.
- Produces:
  - `wizard.step_label(step) -> str`
  - `wizard.LABELS: dict[str, str]`
  - `wizard.Outcome(code: int, message: str, log: tuple[str, ...])`
  - `wizard.WizardScreen(parent, palette, fonts, steps, state, on_finished, *, resumed=False)` — a `tk.Frame` that starts its worker on `start()` and calls `on_finished(Outcome)` on the main thread.

- [ ] **Step 1: Write the failing test**

```python
# tests/host/test_setup_app_logic.py — append
from host.core.install import Step
from host.setup_app import wizard


def test_a_step_label_prefers_the_providers_own_words():
    # "Installing Lima 2.2.0" is Lima's sentence; the installer has no business
    # keeping a second copy of it in a dict keyed by step name.
    step = Step("install_runtime", lambda emit: None, label="Installing Lima 2.2.0")
    assert wizard.step_label(step) == "Installing Lima 2.2.0"


def test_a_step_without_a_label_falls_back_to_the_installers_wording():
    assert wizard.step_label(Step("create_vm", lambda: None)) == \
        "Creating the virtual machine"


def test_an_unknown_step_shows_its_own_name_rather_than_nothing():
    assert wizard.step_label(Step("something_new", lambda: None)) == "something_new"


def test_the_label_table_has_no_entry_for_a_step_the_provider_names():
    # install_runtime's text comes from provider.runtime().label. A second copy
    # here would go stale the first time the pinned version changes.
    assert "install_runtime" not in wizard.LABELS


def test_the_windows_only_steps_are_still_named_for_windows():
    assert wizard.LABELS["remediate"] == "Turning on Windows features"
```

- [ ] **Step 2: Run it and watch it fail**

Run: `python3 -m pytest tests/host/test_setup_app_logic.py -q`
Expected: FAIL — `ImportError: cannot import name 'wizard'`.

- [ ] **Step 3: Implement**

```python
# host/setup_app/wizard.py
from __future__ import annotations

# The screen that runs the install. One row per step, a real progress bar, and
# the log behind a disclosure -- a first-time user watching this should see a
# short list of plain sentences, not a terminal.

import queue
import threading
import time
import tkinter as tk
from dataclasses import dataclass, field

from host.core.install import (
    RESUME_NOTICE, DeadEnd, InstallError, Progress, RebootRequired, Step, run_install,
)

from . import theme, widgets

# Steps the installer names itself. install_runtime is absent on purpose: its
# text comes from provider.runtime().label, because the version in it is Lima's
# fact, not the installer's.
LABELS = {
    "preflight": "Checking this computer",
    "remediate": "Turning on Windows features",
    "reboot_gate": "Restart needed",
    "fetch_image": "Downloading Linux image",
    "create_vm": "Creating the virtual machine",
    "bootstrap": "Installing Eggie",
    "connect": "Connecting to the Eggie service",
    "verify": "Testing the setup",
    "finish": "Finishing up",
}


def step_label(step: Step) -> str:
    return step.label or LABELS.get(step.name, step.name)


@dataclass(frozen=True)
class Outcome:
    code: int
    message: str = ""
    log: tuple[str, ...] = field(default_factory=tuple)


class WizardScreen(tk.Frame):
    def __init__(self, parent, palette: theme.Palette, fonts, steps, state,
                 on_finished, *, resumed: bool = False):
        super().__init__(parent, bg=palette.bg)
        self._palette, self._fonts = palette, fonts
        self._steps, self._state = steps, state
        self._on_finished = on_finished
        self._events: queue.Queue = queue.Queue()
        self._log: list[str] = []
        self._outcome = Outcome(0)
        self._finished = False
        self._started_at = {}
        self._done = 0

        self._header = widgets.Header(
            self, palette, fonts, "Eggie",
            RESUME_NOTICE if resumed else "Setting up your local environment")
        self._header.pack(fill="x")

        body = tk.Frame(self, bg=palette.bg)
        body.pack(fill="both", expand=True, padx=theme.PAD, pady=theme.PAD)

        self._list = widgets.StepList(
            body, palette, fonts, [(s.name, step_label(s)) for s in steps])
        self._list.pack(fill="x")

        self._bar = widgets.ProgressBar(body, palette)
        self._bar.pack(fill="x", pady=(12, 4))

        self._detail = tk.Label(body, text="", bg=palette.bg, fg=palette.muted,
                                font=fonts["small"], anchor="w", justify="left",
                                wraplength=520)
        self._detail.pack(fill="x")

        self._disclosure = widgets.Button(body, palette, fonts, "Show details ▾",
                                          self._toggle_log, width=140)
        self._disclosure.pack(anchor="w", pady=(12, 0))
        self._log_box = tk.Text(body, height=9, wrap="word", relief="flat",
                                bg=palette.surface, fg=palette.text,
                                font=fonts["mono"], state="disabled",
                                highlightthickness=1,
                                highlightbackground=palette.border)

        self._buttons = tk.Frame(body, bg=palette.bg)
        self._buttons.pack(side="bottom", anchor="e", pady=(12, 0))

    # --- running ---

    def start(self) -> None:
        threading.Thread(target=self._worker, daemon=True).start()
        self.after(80, self._pump)

    def _worker(self) -> None:
        try:
            try:
                run_install(self._steps, self._state, self._events.put)
            except RebootRequired:
                self._outcome = Outcome(2, (
                    "Restart your computer.\n"
                    "Setup will continue on its own when you log back in."))
            except DeadEnd as e:
                self._outcome = Outcome(1, "This computer needs a change before "
                                           f"setup can continue:\n\n{e}")
            except InstallError as e:
                message = f"Setup failed during {step_label(self._step(e.step))}."
                if e.action:
                    message += f"\n\n{e.action}"
                self._outcome = Outcome(1, message)
            except Exception as e:                  # noqa: BLE001 - last resort
                self._outcome = Outcome(1, f"Unexpected error: {e}")
        finally:
            self._events.put(None)

    def _step(self, name: str) -> Step:
        for step in self._steps:
            if step.name == name:
                return step
        return Step(name, lambda: None)

    def _pump(self) -> None:
        while True:
            try:
                event = self._events.get_nowait()
            except queue.Empty:
                break
            if event is None:
                return self._finish()
            self._render(event)
        self._list.tick()
        self.after(80, self._pump)

    def _render(self, event: Progress) -> None:
        # The finish step's product is words, and they belong on the next
        # screen rather than scrolled away in a log nobody opened.
        if event.step == "finish" and event.status == "done":
            self._outcome = Outcome(0, event.message, tuple(self._log))
            return
        label = step_label(self._step(event.step))
        if event.fraction is not None:
            self._bar.set((self._done + event.fraction) / len(self._steps))
            self._detail.configure(text=f"{label} — {int(event.fraction * 100)}%")
            return
        if event.status == "running":
            self._started_at[event.step] = time.monotonic()
            self._detail.configure(text=label)
        else:
            self._done += 1
            self._bar.set(self._done / len(self._steps))
        elapsed = time.monotonic() - self._started_at.get(event.step, time.monotonic())
        self._list.set_state(event.step, event.status,
                             elapsed if event.status != "running" else 0.0)
        if event.message:
            self._append(f"{label}: {event.message}")

    def _append(self, text: str) -> None:
        self._log.append(text)
        self._log_box.configure(state="normal")
        self._log_box.insert("end", text + "\n")
        self._log_box.see("end")
        self._log_box.configure(state="disabled")

    def _toggle_log(self) -> None:
        if self._log_box.winfo_ismapped():
            self._log_box.pack_forget()
            self._disclosure._text = "Show details ▾"
        else:
            self._log_box.pack(fill="both", expand=True, pady=(8, 0))
            self._disclosure._text = "Hide details ▴"
        self._disclosure._draw(hover=False)

    def _finish(self) -> None:
        self._finished = True
        self._bar.set(1.0 if self._outcome.code == 0 else self._bar._value)
        self._header.say("Finished" if self._outcome.code == 0 else "Setup stopped")
        self._detail.configure(
            text=self._outcome.message,
            fg=self._palette.text if self._outcome.code == 0 else self._palette.error)
        if self._outcome.code != 0 and self._log and not self._log_box.winfo_ismapped():
            self._toggle_log()
        self._on_finished(Outcome(self._outcome.code, self._outcome.message,
                                  tuple(self._log)))

    def cancelled(self) -> Outcome:
        """The window was closed mid-install. Report failure rather than
        defaulting to success -- nothing about a half-built VM is a success."""
        if self._finished:
            return self._outcome
        return Outcome(1, "Setup was cancelled before completing.", tuple(self._log))
```

- [ ] **Step 4: Run the tests**

Run: `python3 -m pytest tests/host/test_setup_app_logic.py -q && python3 -m pytest -q`
Expected: green.

- [ ] **Step 5: Commit**

```bash
git add host/setup_app/wizard.py tests/host/test_setup_app_logic.py
git commit -m "feat(setup-app): redraw the install screen with real progress"
```

---

### Task 8: The status screen and Copy diagnostics

**Files:**
- Create: `host/setup_app/status.py`
- Test: `tests/host/test_setup_app_logic.py` (extend)

**Interfaces:**
- Consumes: `host.core.status.Readiness`, `host.core.provider.Access`, `theme`, `widgets`.
- Produces:
  - `status.diagnostics_text(readiness, access, *, version: str, log: tuple[str, ...] = ()) -> str`
  - `status.summarize(readiness) -> tuple[str, str]` — (headline, one-line detail)
  - `status.StatusScreen(parent, palette, fonts, readiness, access, *, on_setup, on_close, version, log=())`

- [ ] **Step 1: Write the failing test**

```python
# tests/host/test_setup_app_logic.py — append
from host.core.provider import Access, AccessField
from host.core.status import Readiness
from host.setup_app import status

READY = Readiness(vm_exists=True, vm_reachable=True, engine_version="0.1.0", agent_api=1)
ACCESS = Access(headline="Connect a coding agent", summary="…",
                command="ssh -F /Users/you/.lima/eggie-vm/ssh.config lima-eggie-vm",
                fields=(AccessField("Host", "127.0.0.1"),
                        AccessField("Port", "39022")))


def test_a_ready_machine_says_so_in_two_lines():
    headline, detail = status.summarize(READY)
    assert headline == "Ready"
    assert "0.1.0" in detail


def test_a_machine_with_no_vm_says_what_is_missing_not_what_failed():
    headline, detail = status.summarize(Readiness())
    assert headline == "Not set up yet"
    assert "Set up" in detail or "set up" in detail


def test_a_stopped_vm_is_distinguished_from_a_missing_one():
    headline, _ = status.summarize(Readiness(vm_exists=True))
    assert headline != "Not set up yet"
    assert "not running" in headline.lower() or "stopped" in headline.lower()


def test_an_incompatible_agent_is_named_as_a_version_problem():
    from host.core import constants
    bad = Readiness(vm_exists=True, vm_reachable=True, engine_version="9.9.9",
                    agent_api=max(constants.SUPPORTED_API) + 1)
    headline, detail = status.summarize(bad)
    assert "version" in (headline + detail).lower()


def test_diagnostics_carry_everything_someone_would_ask_for():
    text = status.diagnostics_text(READY, ACCESS, version="0.1.0",
                                   log=("bootstrap: done",))
    for expected in ("0.1.0", "vm_exists", "engine_version", "agent_api",
                     "bootstrap: done"):
        assert expected in text


def test_diagnostics_never_carry_the_identity_file_contents_or_a_token():
    # Paths are fine; secrets are not. This text is written to be pasted into
    # a bug report by someone who will not read it first.
    access = Access(headline="x", summary="y", command="ssh …",
                    fields=(AccessField("Identity file", "/Users/you/.lima/_config/user"),))
    text = status.diagnostics_text(READY, access, version="0.1.0")
    assert "/Users/you/.lima/_config/user" in text
    assert "PRIVATE KEY" not in text


def test_diagnostics_work_before_anything_is_provisioned():
    # The button exists on a machine where the probe found nothing, and that is
    # exactly the machine whose user needs to send us something.
    text = status.diagnostics_text(Readiness(problem="limactl is not installed"),
                                   None, version="0.1.0")
    assert "limactl is not installed" in text
```

- [ ] **Step 2: Run it and watch it fail**

Run: `python3 -m pytest tests/host/test_setup_app_logic.py -q`
Expected: FAIL — `ImportError: cannot import name 'status' from 'host.setup_app'`.

- [ ] **Step 3: Implement**

```python
# host/setup_app/status.py
from __future__ import annotations

# The screen the app opens on. It answers two questions -- is this machine set
# up, and how does my coding agent get inside the VM -- and it renders an
# Access value without knowing what SSH is.

import tkinter as tk

from host.core import constants
from host.core.provider import Access
from host.core.status import Readiness

from . import theme, widgets


def summarize(readiness: Readiness) -> tuple[str, str]:
    """A headline and one line under it. Never a status code, never a
    traceback: this is the first thing a user reads."""
    if readiness.ready:
        return ("Ready",
                f"Eggie {readiness.engine_version} is running in the virtual machine.")
    if not readiness.vm_exists:
        return ("Not set up yet",
                "Set up Eggie to create the virtual machine and install it."
                + (f"\n{readiness.problem}" if readiness.problem else ""))
    if not readiness.vm_reachable:
        return ("The virtual machine is not running",
                "Run setup again to start it."
                + (f"\n{readiness.problem}" if readiness.problem else ""))
    if not readiness.engine_version:
        return ("Eggie is not installed in the virtual machine",
                "The virtual machine is there, but nothing is installed inside "
                "it yet. Run setup again.")
    if readiness.agent_api not in constants.SUPPORTED_API:
        if readiness.agent_api is None:
            return ("The Eggie service is not answering",
                    "The virtual machine is running, but the service inside it "
                    "did not respond. Run setup again."
                    + (f"\n{readiness.problem}" if readiness.problem else ""))
        return ("Versions do not match",
                "This app and the Eggie service inside the virtual machine are "
                f"versions that cannot work together (service API "
                f"{readiness.agent_api}, this app speaks "
                f"{', '.join(str(n) for n in sorted(constants.SUPPORTED_API))}).")
    return ("Not ready", readiness.problem or "Run setup again.")


def diagnostics_text(readiness: Readiness, access: Access | None, *,
                     version: str, log: tuple[str, ...] = ()) -> str:
    """The text behind Copy diagnostics.

    install.py's failure messages have told users to press this button since
    the installer was written, and no such button existed. It carries facts and
    paths -- never a token, and never the contents of a key file.
    """
    lines = [f"eggie {version}", ""]
    for name in ("vm_exists", "vm_reachable", "engine_version", "agent_api", "problem"):
        lines.append(f"{name}: {getattr(readiness, name)!r}")
    if access is not None:
        lines += ["", f"command: {access.command}"]
        lines += [f"{field.label}: {field.value}" for field in access.fields]
        if access.note:
            lines.append(f"note: {access.note}")
    if log:
        lines += ["", "--- setup log ---", *log]
    return "\n".join(lines) + "\n"


class StatusScreen(tk.Frame):
    def __init__(self, parent, palette: theme.Palette, fonts,
                 readiness: Readiness, access: Access | None, *,
                 on_setup, on_close, version: str, log: tuple[str, ...] = ()):
        super().__init__(parent, bg=palette.bg)
        self._palette, self._fonts = palette, fonts
        self._readiness, self._access = readiness, access
        self._version, self._log = version, log

        headline, detail = summarize(readiness)
        widgets.Header(self, palette, fonts, "Eggie", headline).pack(fill="x")

        body = tk.Frame(self, bg=palette.bg)
        body.pack(fill="both", expand=True, padx=theme.PAD, pady=theme.PAD)

        tk.Label(body, text=detail, bg=palette.bg, fg=palette.text,
                 font=fonts["body"], anchor="w", justify="left",
                 wraplength=520).pack(fill="x")

        if access is not None and readiness.ready:
            self._access_panel(body, access)

        buttons = tk.Frame(body, bg=palette.bg)
        buttons.pack(side="bottom", anchor="e", pady=(16, 0))
        widgets.Button(buttons, palette, fonts, "Copy diagnostics",
                       self._copy_diagnostics, width=160).pack(side="left", padx=(0, 8))
        widgets.Button(buttons, palette, fonts,
                       "Re-run setup" if readiness.ready else "Set up Eggie",
                       on_setup, primary=True, width=150).pack(side="left", padx=(0, 8))
        widgets.Button(buttons, palette, fonts, "Close", on_close,
                       width=100).pack(side="left")

    def _access_panel(self, parent, access: Access) -> None:
        panel = tk.Frame(parent, bg=self._palette.bg)
        panel.pack(fill="x", pady=(18, 0))
        tk.Label(panel, text=access.headline, bg=self._palette.bg,
                 fg=self._palette.text, font=self._fonts["heading"],
                 anchor="w").pack(fill="x")
        tk.Label(panel, text=access.summary, bg=self._palette.bg,
                 fg=self._palette.muted, font=self._fonts["small"], anchor="w",
                 justify="left", wraplength=520).pack(fill="x", pady=(2, 10))
        widgets.FieldRow(panel, self._palette, self._fonts,
                         "Command", access.command).pack(fill="x", pady=2)
        for field in access.fields:
            widgets.FieldRow(panel, self._palette, self._fonts,
                             field.label, field.value).pack(fill="x", pady=2)
        if access.note:
            tk.Label(panel, text=access.note, bg=self._palette.bg,
                     fg=self._palette.muted, font=self._fonts["small"],
                     anchor="w", justify="left", wraplength=520).pack(
                         fill="x", pady=(8, 0))

    def _copy_diagnostics(self) -> None:
        self.clipboard_clear()
        self.clipboard_append(diagnostics_text(self._readiness, self._access,
                                               version=self._version, log=self._log))
```

- [ ] **Step 4: Run the tests**

Run: `python3 -m pytest tests/host/test_setup_app_logic.py -q && python3 -m pytest -q`
Expected: green.

- [ ] **Step 5: Commit**

```bash
git add host/setup_app/status.py tests/host/test_setup_app_logic.py
git commit -m "feat(setup-app): a status screen that hands over the SSH details"
```

---

### Task 9: The router, and the CLI that opens it

**Files:**
- Modify: `host/setup_app/app.py` (replace `run_window` entirely)
- Modify: `host/cli.py` (`setup` builds a steps factory)
- Modify: `packaging/macos/eggie.spec`, `packaging/windows/eggie.spec` (hidden imports)
- Test: `tests/test_setup_cli.py` (extend)

**Interfaces:**
- Consumes: everything from Tasks 5–8.
- Produces:
  - `app.run_window(provider, steps_factory, state, *, resumed: bool = False) -> int`
  - `steps_factory: Callable[[], list[Step]]`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_setup_cli.py — append
def test_setup_hands_the_window_a_factory_it_can_call_twice(monkeypatch, tmp_path):
    """Re-run setup must build a fresh step list.

    The steps close over provider state (`provider.rootfs` is assigned while
    the list is built), so handing the window one list and running it twice
    would re-run the second install against the first one's bindings.
    """
    import host.cli as cli

    captured = {}

    def fake_run_window(provider, steps_factory, state, *, resumed=False):
        captured["provider"] = provider
        steps_factory()
        steps_factory()
        captured["resumed"] = resumed
        return 0

    monkeypatch.setattr("host.setup_app.app.run_window", fake_run_window)
    monkeypatch.setattr(cli, "_provider_factory", lambda: FakeProvider())
    monkeypatch.setattr("host.providers.default_install_dir",
                        lambda: tmp_path / "vm")

    result = runner.invoke(app, ["setup"])
    assert result.exit_code == 0
    assert captured["resumed"] is False
```

(Use the `FakeProvider` and `runner`/`app` already defined in that file; give the fake the `runtime()` and `access()` members added in Tasks 3 and 4, returning `None` and a minimal `Access` respectively.)

- [ ] **Step 2: Run it and watch it fail**

Run: `python3 -m pytest tests/test_setup_cli.py -q`
Expected: FAIL — `run_window()` takes the old three-argument signature.

- [ ] **Step 3: Implement**

```python
# host/setup_app/app.py
from __future__ import annotations

# The router. Two screens live behind it: the status screen a user lands on,
# and the wizard that runs the install.
#
# It opens on status even when nothing is provisioned -- and starts the wizard
# itself in that case, so a first-time user still double-clicks once. On a
# machine that is already set up, the status screen is where the SSH details
# are, and nothing re-runs a multi-minute install just to show them.

import queue
import threading
import tkinter as tk

from host.core.status import Readiness, probe

from . import status as status_screen
from . import theme, wizard


def run_window(provider, steps_factory, state, *, resumed: bool = False) -> int:
    root = tk.Tk()
    root.title("Eggie Setup")
    root.geometry("620x620")
    root.minsize(560, 560)

    palette = theme.palette_for_root(root)
    fonts = theme.load_fonts(root)
    root.configure(bg=palette.bg)

    app_state = {"code": 0, "screen": None, "log": ()}

    def close() -> None:
        screen = app_state["screen"]
        if isinstance(screen, wizard.WizardScreen):
            outcome = screen.cancelled()
            app_state["code"] = outcome.code
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", close)

    def show(frame) -> None:
        previous = app_state["screen"]
        if previous is not None:
            previous.destroy()
        app_state["screen"] = frame
        frame.pack(fill="both", expand=True)

    def show_status(readiness: Readiness) -> None:
        from host.core import constants
        access = None
        try:
            access = provider.access()
        except Exception:
            # A provider that cannot describe how to get in is not a reason to
            # show nothing; the rest of the screen still answers "am I set up".
            access = None
        show(status_screen.StatusScreen(
            root, palette, fonts, readiness, access,
            on_setup=start_wizard, on_close=close,
            version=constants.APP_VERSION, log=app_state["log"]))

    def start_wizard() -> None:
        screen = wizard.WizardScreen(root, palette, fonts, steps_factory(),
                                     state, on_finished=wizard_finished,
                                     resumed=resumed)
        show(screen)
        screen.start()

    def wizard_finished(outcome: wizard.Outcome) -> None:
        app_state["code"] = outcome.code
        app_state["log"] = outcome.log
        if outcome.code == 0:
            # Straight to the screen that says how to get in: the install's
            # closing sentence is the beginning of the next thing the user does.
            begin_probe(then=show_status)

    # --- the probe, off the main thread so the window draws immediately ---

    results: queue.Queue = queue.Queue()

    def begin_probe(*, then, auto_setup: bool = False) -> None:
        show(_Spinner(root, palette, fonts))
        threading.Thread(target=lambda: results.put(probe(provider)),
                         daemon=True).start()

        def wait() -> None:
            try:
                readiness = results.get_nowait()
            except queue.Empty:
                return root.after(100, wait)
            if auto_setup and not readiness.vm_exists:
                return start_wizard()
            then(readiness)

        root.after(100, wait)

    if resumed:
        # A window that opened by itself after a restart has one job.
        start_wizard()
    else:
        begin_probe(then=show_status, auto_setup=True)

    root.mainloop()
    return app_state["code"]


class _Spinner(tk.Frame):
    """Shown for the second or two the probe takes. A window that draws nothing
    while a subprocess runs reads as a hang."""

    def __init__(self, parent, palette: theme.Palette, fonts):
        super().__init__(parent, bg=palette.bg)
        from . import widgets
        widgets.Header(self, palette, fonts, "Eggie", "Checking this computer…").pack(fill="x")
        self._list = widgets.StepList(self, palette, fonts, [("probe", "Looking for the virtual machine")])
        self._list.set_state("probe", "running")
        self._list.pack(fill="x", padx=theme.PAD, pady=theme.PAD)
        self._tick()

    def _tick(self) -> None:
        if self.winfo_exists():
            self._list.tick()
            self.after(80, self._tick)
```

In `host/cli.py`'s `setup`, build a factory instead of a list:

```python
    def build_steps():
        return default_steps(
            provider,
            cache_dir=root / "cache",
            template_dir=VERIFY_TEMPLATE,
            domain=constants.DEFAULT_DOMAIN,
            exe_path=_sys.executable,
        )

    if not headless:
        from host.setup_app.app import run_window
        raise typer.Exit(code=run_window(provider, build_steps, state, resumed=resume))

    steps = build_steps()
```

and use `steps` for the headless path below, unchanged.

In both `packaging/macos/eggie.spec` and `packaging/windows/eggie.spec`, extend the hidden imports — `setup_app` is reached only by a function-local import, so PyInstaller does not see the new modules:

```python
HIDDEN = ["host.setup_app.app", "host.setup_app.wizard", "host.setup_app.status",
          "host.setup_app.theme", "host.setup_app.widgets"]
```

and pass `hiddenimports=HIDDEN` to each `Analysis`.

- [ ] **Step 4: Run the tests**

Run: `python3 -m pytest -q`
Expected: green, all of it.

- [ ] **Step 5: Manual smoke, on the Mac**

```bash
python3 -m host.cli setup --headless   # expect it to stop at install_runtime or later
python3 -m host.cli doctor             # expect macOS + Lima checks, not a brew instruction
```

Then open the window (`python3 -c "from host.cli import app; app(['setup'])"`), confirm it lands on the status screen, and confirm the buttons work. Do **not** let the install run to `create_vm` unless you intend a real VM — see the verification report.

- [ ] **Step 6: Commit**

```bash
git add host/setup_app/app.py host/cli.py packaging tests/test_setup_cli.py
git commit -m "feat(setup-app): open on a status screen, run setup from a button"
```

---

### Task 10: The build, the docs, and the honest verdict

**Files:**
- Modify: `packaging/macos/build.sh` (assert the new hidden imports made it in)
- Modify: `CLAUDE.md`
- Modify: `docs/macos-install-test-matrix.md`
- Modify: `docs/lima-verification-report.md`
- Modify: `host/cli.py` (`selfcheck`)

- [ ] **Step 1: Extend `selfcheck` so a missing screen fails the build, not the user**

`selfcheck` exists because `version` passes on a bundle missing an asset. The same hazard now applies to the five `setup_app` modules, which are reached only through a function-local import. Add to `host/cli.py`'s `selfcheck`:

```python
    # The setup window is five modules PyInstaller can only find through the
    # spec's hiddenimports. A bundle missing one launches, shows a Dock icon
    # and dies on the first draw -- which is exactly what this command exists
    # to catch before a user does.
    import importlib
    for name in ("theme", "widgets", "wizard", "status", "app"):
        importlib.import_module(f"host.setup_app.{name}")
    typer.echo("✓ setup window modules import")
```

- [ ] **Step 2: Build and verify**

```bash
bash packaging/macos/build.sh
```

Expected: exits 0; `eggie version`, `eggie selfcheck` both pass against the frozen binary; the two executable names and the two plist assertions still hold.

- [ ] **Step 3: Update `CLAUDE.md`**

Three edits, in the sections that are now wrong:

1. In the "install step list is built from the provider" bullet, change "five members" to seven and add:
   `runtime()` returning None means the VM platform ships with the host OS and `install_runtime` disappears; a value names the step and installs what the platform needs. `access()` is how the status screen shows a user the way into the VM without knowing what SSH is.
2. Replace the "A Mac app gets no shell PATH" bullet's conclusion — Lima is now installed by setup into `~/.local/share/eggie/lima`, and `find_limactl` prefers that copy over any Homebrew one. The PATH finding still holds for anything else the host shells out to by name.
3. Add to "Things that will bite you":
   **The setup window is two screens, and the app opens on the status one.** `host/setup_app/app.py` routes on `host/core/status.py::probe` — VM exists, guest reachable, `engine.version` present, agent API supported — and starts the wizard by itself only when nothing is provisioned. `theme.py` picks light or dark from the luminance of the ttk background rather than by asking which OS this is, which is what keeps the no-platform-branching invariant true in the UI layer. Fonts are tkinter's named system fonts; a hardcoded family name is how every label came to ask macOS for "Segoe UI".

- [ ] **Step 4: Update `docs/macos-install-test-matrix.md`**

- **Case 4 changes meaning.** It currently passes by printing `install Lima (brew install lima)`. Rewrite it: *Setup on a Mac without Lima* now proves setup **installs** Lima — pass condition is that `install_runtime` completes and `~/.local/share/eggie/lima/bin/limactl --version` prints 2.2.0. Mark the old result superseded; do not delete it.
- **New case 9:** *Setup with no network at `install_runtime`.* Pass condition: the step fails with the sentence from `_ACTIONS["install_runtime"]`, no stack trace, and a re-run with the network back resumes the partial download rather than restarting it.
- **New case 10:** *Open Eggie.app on a provisioned Mac.* Pass condition: the status screen appears within a few seconds without running an install; the SSH command and the four fields are populated from `~/.lima/eggie-vm/ssh.config`; Copy puts them on the clipboard; `ssh -F … lima-eggie-vm` from Terminal gets a shell.
- **New case 11:** *The window in dark mode and in light mode.* Pass condition: text is legible in both, with no white-on-white card and no black-on-black label, switched live via System Settings with the app reopened.
- **Update the Notes section:** the `--deep` signing hazard for a bundled `limactl` no longer applies, because Lima is fetched rather than bundled. Keep the paragraph and say why it is now moot — it is the reason the decision went the way it did.

- [ ] **Step 5: Update `docs/lima-verification-report.md`**

Add a section for this work saying exactly this much and no more:

- `install_runtime` is the first macOS path that can be verified without booting a VM: it ends by running the downloaded `limactl` and reading its version back. Record the result of that run.
- `~/.lima/<name>/ssh.config` now has two readers — `forward()` and `access()` — so the layout assumption is visible on the status screen instead of only inside a port forward.
- **Everything else is unchanged.** No VM has been created. `create_vm` is still the first unproven step, and `vz`, Rosetta, the port forwards and the ssh control master are all still assumptions. **The UNVERIFIED banners in `host/providers/lima.py` and `host/providers/eggie.yaml` stay.**
- `forwards()` still returns an empty list on Lima; that open question is not touched by this work.

- [ ] **Step 6: Run everything and commit**

```bash
python3 -m pytest -q
git add CLAUDE.md docs packaging host/cli.py
git commit -m "docs: record what the macOS install work proves, and what it does not"
```

---

## Self-review

**Spec coverage.** Every section of the design maps to a task: Lima acquisition → Tasks 2 and 3; `runtime()` and the step → Task 3; `Progress.fraction` → Task 1; `find_limactl` order, the `preflight`/`is_supported` divergence and the x86_64 image → Task 3; `access()` → Task 4; the readiness probe → Task 5; the `setup_app` package → Tasks 6–9; Copy diagnostics → Task 8; docs and the honest verdict → Task 10.

**Known deviation from the spec:** the spec listed `tests/host/test_setup_app_logic.py` covering `palette_for`, elapsed time and `diagnostics_text`; this plan spreads those across Tasks 6, 7 and 8 in one file, which is the same coverage in three commits.

**Type consistency.** `Runtime.run` takes the emitter and returns None everywhere it appears (Tasks 1, 3). `Access`/`AccessField` field names are identical in Tasks 4, 8 and 9. `Readiness` field names match between Task 5's definition and Task 8's `summarize`/`diagnostics_text`. `run_window`'s signature matches between Task 9's `app.py` and `cli.py`. `install(root, *, fetch, runner, on_progress, machine)` in Task 2 matches the two-keyword call in Task 3.

**One thing left for the implementer, not a placeholder:** Task 3's note about staging `INSTALL_SURFACE` in one edit or two. Either is correct; one leaves a single test red between two commits.
