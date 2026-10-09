# Local Runtime PoC Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A stack-agnostic Python CLI that brings up a managed Linux VM, installs Docker in it, runs an arbitrary `docker-compose` project, and returns a working URL on the host — on Windows/WSL2 (verified) and macOS/Lima (parity, unverified).

**Architecture:** A 7-method `VmProvider` abstraction (`Wsl2Provider` shells to `wsl.exe`, `LimaProvider` to `limactl`) hides the OS. Everything above the guest — install Docker, run one Traefik entrypoint, generate a compose overlay that routes by hostname — is written once on top of `exec`. Platform branching exists only in `providers/`.

**Tech Stack:** Python 3.12, Typer (CLI), PyYAML, sqlite3 (stdlib), http.server (stdlib JSON-RPC), pytest. Guest: Ubuntu 24.04, docker-ce, Traefik.

**Spec:** `docs/superpowers/specs/2026-08-14-local-runtime-poc-design.md`

## Global Constraints

- Python 3.12+. Package root is `runtime/`; installed console script `runtime = "runtime.cli:app"`.
- `if sys.platform` / `platform.system()` may appear **only** under `runtime/providers/`. Enforced by a test (Task 18).
- The runtime **never edits the user's compose file** — routing is added via a separate generated `runtime/.runtime/overlay.yml` in the project dir (`.runtime/`).
- Guest services and Traefik **bind `0.0.0.0`**, never `127.0.0.1`.
- Exactly one host-visible port: **`39080`**. Traefik's entrypoint listens on `39080` inside the guest (identical on both OSes); WSL same-port `localhostForwarding` and Lima `portForwards` both surface it on host `39080`. No `netsh`/admin for the edge port.
- Default routing domain: `local.eggie.space` (resolves to 127.0.0.1 in every browser incl. Safari; needs internet). URL shape: `http://<id>.local.eggie.space:39080`.
- Distro/VM name: `runtime-vm`. Guest project root: `/opt/runtime/projects/<id>`. Bootstrap marker: `/opt/runtime/.bootstrapped` holding an integer version (current: `1`).
- No stack names (`wordpress`, `laravel`, `django`, …) anywhere in `runtime/core/` or `runtime/providers/`.

---

### Task 1: Project scaffold + packaging

**Files:**
- Create: `pyproject.toml`
- Create: `runtime/__init__.py`, `runtime/core/__init__.py`, `runtime/providers/__init__.py`, `runtime/api/__init__.py`
- Create: `runtime/cli.py`
- Create: `tests/__init__.py`, `tests/conftest.py`
- Test: `tests/test_cli_smoke.py`

**Interfaces:**
- Produces: Typer app object `runtime.cli:app`; console entry `runtime`.

- [ ] **Step 1: Write `pyproject.toml`**

```toml
[project]
name = "runtime"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = ["typer>=0.12", "pyyaml>=6"]

[project.scripts]
runtime = "runtime.cli:app"

[project.optional-dependencies]
dev = ["pytest>=8"]

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
include = ["runtime*"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

- [ ] **Step 2: Create empty `__init__.py` files** for `runtime`, `runtime/core`, `runtime/providers`, `runtime/api`, `tests`.

- [ ] **Step 3: Write minimal `runtime/cli.py`**

```python
import typer

app = typer.Typer(help="Local Runtime: VM + Docker + one exposed port.", no_args_is_help=True)


@app.command()
def version():
    """Print the runtime version."""
    typer.echo("runtime 0.1.0")


if __name__ == "__main__":
    app()
```

- [ ] **Step 4: Write the failing smoke test** `tests/test_cli_smoke.py`

```python
from typer.testing import CliRunner
from runtime.cli import app

runner = CliRunner()


def test_version_command_runs():
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "runtime 0.1.0" in result.stdout
```

- [ ] **Step 5: Install and run**

Run: `pip install -e ".[dev]" && pytest tests/test_cli_smoke.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml runtime tests
git commit -m "feat: project scaffold and Typer CLI entrypoint"
```

---

### Task 2: Core types — Completed, CheckResult, Diagnosis, VmProvider Protocol

**Files:**
- Create: `runtime/core/provider.py`
- Test: `tests/core/test_provider_types.py`, `tests/core/__init__.py`

**Interfaces:**
- Produces:
  - `Completed(returncode: int, stdout: str, stderr: str)` with `.ok -> bool`.
  - `CheckResult(label: str, ok: bool, fix: str | None = None)`.
  - `Diagnosis(checks: list[CheckResult])` with `.ok -> bool` (all checks ok) and `.blocking -> list[CheckResult]` (the not-ok ones).
  - `class VmProvider(Protocol)` with the 7 methods from the spec.

- [ ] **Step 1: Write the failing test** `tests/core/test_provider_types.py`

```python
from runtime.core.provider import Completed, CheckResult, Diagnosis


def test_completed_ok_reflects_returncode():
    assert Completed(0, "out", "").ok is True
    assert Completed(1, "", "err").ok is False


def test_diagnosis_ok_only_when_all_checks_pass():
    good = Diagnosis([CheckResult("a", True), CheckResult("b", True)])
    bad = Diagnosis([CheckResult("a", True), CheckResult("b", False, fix="do x")])
    assert good.ok is True
    assert bad.ok is False


def test_diagnosis_blocking_lists_only_failures():
    d = Diagnosis([CheckResult("a", True), CheckResult("b", False, fix="do x")])
    assert [c.label for c in d.blocking] == ["b"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/core/test_provider_types.py -v`
Expected: FAIL (module not found).

- [ ] **Step 3: Write `runtime/core/provider.py`**

```python
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class Completed:
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


@dataclass(frozen=True)
class CheckResult:
    label: str
    ok: bool
    fix: str | None = None


@dataclass(frozen=True)
class Diagnosis:
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks)

    @property
    def blocking(self) -> list[CheckResult]:
        return [c for c in self.checks if not c.ok]


@runtime_checkable
class VmProvider(Protocol):
    def is_supported(self) -> Diagnosis: ...
    def exists(self) -> bool: ...
    def create(self) -> None: ...
    def start(self) -> None: ...
    def stop(self) -> None: ...
    def destroy(self) -> None: ...
    def exec(self, argv: list[str], *, root: bool = False) -> Completed: ...
    def forward(self, guest_port: int, host_port: int) -> None: ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/core/test_provider_types.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add runtime/core/provider.py tests/core
git commit -m "feat: core provider types (Completed, Diagnosis, VmProvider)"
```

---

### Task 3: WSL output decoder (UTF-16LE meta vs UTF-8 passthrough)

**Files:**
- Create: `runtime/providers/wsl_encoding.py`
- Test: `tests/providers/test_wsl_encoding.py`, `tests/providers/__init__.py`

**Interfaces:**
- Produces: `decode_wsl(data: bytes) -> str` — decodes wsl.exe meta output (UTF-16LE with NULs) or UTF-8 passthrough, normalizes CRLF to `\n`, strips a trailing newline.

- [ ] **Step 1: Write the failing test** `tests/providers/test_wsl_encoding.py`

```python
from runtime.providers.wsl_encoding import decode_wsl


def test_decodes_utf16le_meta_output():
    raw = "Ubuntu\r\ndocker-desktop\r\n".encode("utf-16-le")
    assert decode_wsl(raw) == "Ubuntu\ndocker-desktop"


def test_decodes_plain_utf8_passthrough():
    assert decode_wsl(b"HELLO-world\n") == "HELLO-world"


def test_empty_bytes_give_empty_string():
    assert decode_wsl(b"") == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/providers/test_wsl_encoding.py -v`
Expected: FAIL (module not found).

- [ ] **Step 3: Write `runtime/providers/wsl_encoding.py`**

```python
def decode_wsl(data: bytes) -> str:
    """wsl.exe meta commands emit UTF-16LE (NUL-interleaved); command
    passthrough emits UTF-8. Detect the former by embedded NUL bytes."""
    if not data:
        return ""
    if b"\x00" in data:
        text = data.decode("utf-16-le", "replace")
    else:
        text = data.decode("utf-8", "replace")
    return text.replace("\r\n", "\n").strip("\n").strip("\x00")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/providers/test_wsl_encoding.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add runtime/providers/wsl_encoding.py tests/providers
git commit -m "feat: WSL output decoder handling UTF-16LE meta vs UTF-8"
```

---

### Task 4: Wsl2Provider command construction

**Files:**
- Create: `runtime/providers/wsl2.py`
- Test: `tests/providers/test_wsl2.py`

**Interfaces:**
- Consumes: `Completed` (Task 2), `decode_wsl` (Task 3).
- Produces: `Wsl2Provider(distro="runtime-vm", install_dir: Path, rootfs: Path, wsl="wsl.exe", runner=subprocess.run)`. Implements the 7 `VmProvider` methods. `runner` is injectable for tests (signature: `runner(argv: list[str]) -> CompletedProcess-like` with `.returncode`, `.stdout` (bytes), `.stderr` (bytes)).
- Constant: `EDGE_PORT = 39080`.

- [ ] **Step 1: Write the failing test** `tests/providers/test_wsl2.py`

```python
from pathlib import Path
from runtime.providers.wsl2 import Wsl2Provider


class FakeRunner:
    """Records argv, returns a scripted (returncode, stdout, stderr)."""
    def __init__(self, stdout=b"", stderr=b"", returncode=0):
        self.calls = []
        self._out, self._err, self._rc = stdout, stderr, returncode

    def __call__(self, argv):
        self.calls.append(argv)
        class R:
            returncode = self._rc
            stdout = self._out
            stderr = self._err
        return R()


def make(runner):
    return Wsl2Provider(
        distro="runtime-vm",
        install_dir=Path("/tmp/inst"),
        rootfs=Path("/tmp/ubuntu.tar.gz"),
        wsl="wsl.exe",
        runner=runner,
    )


def test_exec_builds_passthrough_argv_and_decodes_utf8():
    r = FakeRunner(stdout=b"Linux 6.6\n")
    result = make(r).exec(["uname", "-sr"])
    assert r.calls[-1] == ["wsl.exe", "-d", "runtime-vm", "--", "uname", "-sr"]
    assert result.stdout == "Linux 6.6"
    assert result.ok is True


def test_exec_root_inserts_user_root():
    r = FakeRunner()
    make(r).exec(["id", "-un"], root=True)
    assert r.calls[-1] == ["wsl.exe", "-d", "runtime-vm", "-u", "root", "--", "id", "-un"]


def test_exists_true_when_distro_in_list():
    listing = "runtime-vm\r\nUbuntu\r\n".encode("utf-16-le")
    r = FakeRunner(stdout=listing)
    assert make(r).exists() is True
    assert r.calls[-1] == ["wsl.exe", "-l", "-q"]


def test_exists_false_when_absent():
    r = FakeRunner(stdout="Ubuntu\r\n".encode("utf-16-le"))
    assert make(r).exists() is False


def test_create_imports_then_enables_systemd_then_terminates():
    r = FakeRunner()
    make(r).create()
    argvs = r.calls
    assert argvs[0][:2] == ["wsl.exe", "--import"]
    assert argvs[0][2] == "runtime-vm"
    assert "/tmp/inst" in argvs[0][3]
    assert "/tmp/ubuntu.tar.gz" in argvs[0][4]
    assert argvs[0][-2:] == ["--version", "2"]
    # systemd fixup runs as root, writes wsl.conf
    assert any("-u" in a and "root" in a and "wsl.conf" in " ".join(a) for a in argvs)
    # ends by terminating so systemd takes effect
    assert argvs[-1] == ["wsl.exe", "--terminate", "runtime-vm"]


def test_stop_terminates_and_destroy_unregisters():
    r = FakeRunner()
    p = make(r)
    p.stop()
    assert r.calls[-1] == ["wsl.exe", "--terminate", "runtime-vm"]
    p.destroy()
    assert r.calls[-1] == ["wsl.exe", "--unregister", "runtime-vm"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/providers/test_wsl2.py -v`
Expected: FAIL (module not found).

- [ ] **Step 3: Write `runtime/providers/wsl2.py`**

```python
from __future__ import annotations

import subprocess
from pathlib import Path

from ..core.provider import Completed, Diagnosis
from .wsl_encoding import decode_wsl
from .wsl_checks import diagnose_wsl2

EDGE_PORT = 39080


def _default_runner(argv):
    return subprocess.run(argv, capture_output=True)


class Wsl2Provider:
    def __init__(self, distro="runtime-vm", install_dir: Path | None = None,
                 rootfs: Path | None = None, wsl="wsl.exe", runner=_default_runner):
        self.distro = distro
        self.install_dir = Path(install_dir) if install_dir else None
        self.rootfs = Path(rootfs) if rootfs else None
        self.wsl = wsl
        self._run = runner

    # --- wsl.exe's own output is UTF-16LE ---
    def _meta(self, args: list[str]) -> Completed:
        p = self._run([self.wsl, *args])
        return Completed(p.returncode, decode_wsl(p.stdout), decode_wsl(p.stderr))

    def is_supported(self) -> Diagnosis:
        return diagnose_wsl2(self._run, self.wsl)

    def exists(self) -> bool:
        out = self._meta(["-l", "-q"]).stdout
        return self.distro in [line.strip() for line in out.splitlines()]

    def create(self) -> None:
        if self.install_dir is None or self.rootfs is None:
            raise ValueError("install_dir and rootfs are required to create the VM")
        self.install_dir.mkdir(parents=True, exist_ok=True)
        self._meta(["--import", self.distro, str(self.install_dir),
                    str(self.rootfs), "--version", "2"])
        # systemd is off by default in WSL; docker.service needs it.
        self.exec(["bash", "-lc", "printf '[boot]\\nsystemd=true\\n' > /etc/wsl.conf"],
                  root=True)
        self.stop()  # --terminate so the wsl.conf change takes effect on next boot

    def start(self) -> None:
        # Running any command boots the distro.
        self.exec(["true"])

    def stop(self) -> None:
        self._meta(["--terminate", self.distro])

    def destroy(self) -> None:
        self._meta(["--unregister", self.distro])

    def exec(self, argv: list[str], *, root: bool = False) -> Completed:
        base = [self.wsl, "-d", self.distro]
        if root:
            base += ["-u", "root"]
        base += ["--", *argv]
        p = self._run(base)
        # command passthrough is UTF-8
        return Completed(p.returncode,
                         p.stdout.decode("utf-8", "replace").strip("\n"),
                         p.stderr.decode("utf-8", "replace").strip("\n"))

    def forward(self, guest_port: int, host_port: int) -> None:
        # WSL2 localhostForwarding surfaces guest 0.0.0.0:<port> on host
        # localhost:<same port>. The edge port is chosen equal on both sides,
        # so no action is required here. Distinct raw-TCP forwards
        # (guest_port != host_port) are added in a later milestone via netsh.
        if guest_port != host_port:
            raise NotImplementedError(
                "distinct-port forwarding on WSL2 is not part of the PoC slice")
```

- [ ] **Step 4: Create a placeholder `runtime/providers/wsl_checks.py`** so the import resolves (fully implemented in Task 6):

```python
from ..core.provider import Diagnosis


def diagnose_wsl2(runner, wsl="wsl.exe") -> Diagnosis:
    return Diagnosis([])  # replaced in Task 6
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/providers/test_wsl2.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add runtime/providers/wsl2.py runtime/providers/wsl_checks.py tests/providers/test_wsl2.py
git commit -m "feat: Wsl2Provider command construction with injectable runner"
```

---

### Task 5: LimaProvider command construction (UNVERIFIED) + runtime.yaml

**Files:**
- Create: `runtime/providers/lima.py`
- Create: `runtime/providers/runtime.yaml`
- Test: `tests/providers/test_lima.py`

**Interfaces:**
- Consumes: `Completed` (Task 2).
- Produces: `LimaProvider(name="runtime-vm", config: Path, limactl="limactl", runner=subprocess.run)` implementing the 7 methods. Marked UNVERIFIED (no macOS).

- [ ] **Step 1: Write the failing test** `tests/providers/test_lima.py`

```python
from pathlib import Path
from runtime.providers.lima import LimaProvider


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


def make(runner):
    return LimaProvider(name="runtime-vm", config=Path("/tmp/runtime.yaml"),
                        limactl="limactl", runner=runner)


def test_exec_uses_limactl_shell():
    r = FakeRunner(stdout=b"ok\n")
    make(r).exec(["uname", "-sr"])
    assert r.calls[-1] == ["limactl", "shell", "runtime-vm", "uname", "-sr"]


def test_exec_root_uses_sudo():
    r = FakeRunner()
    make(r).exec(["id", "-un"], root=True)
    assert r.calls[-1] == ["limactl", "shell", "runtime-vm", "sudo", "id", "-un"]


def test_create_calls_start_with_config():
    r = FakeRunner()
    make(r).create()
    assert r.calls[-1] == ["limactl", "start", "--name=runtime-vm",
                           "--tty=false", "/tmp/runtime.yaml"]


def test_stop_and_destroy():
    r = FakeRunner()
    p = make(r)
    p.stop()
    assert r.calls[-1] == ["limactl", "stop", "runtime-vm"]
    p.destroy()
    assert r.calls[-1] == ["limactl", "delete", "runtime-vm"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/providers/test_lima.py -v`
Expected: FAIL.

- [ ] **Step 3: Write `runtime/providers/lima.py`**

```python
from __future__ import annotations

# UNVERIFIED: written for parity against the VmProvider contract; not run on
# macOS in this session. Command construction is unit-tested; live behavior
# (vz, rosetta, port forwarding) must be confirmed on an Apple Silicon host.

import subprocess
from pathlib import Path

from ..core.provider import Completed, Diagnosis, CheckResult

EDGE_PORT = 39080


def _default_runner(argv):
    return subprocess.run(argv, capture_output=True)


class LimaProvider:
    def __init__(self, name="runtime-vm", config: Path | None = None,
                 limactl="limactl", runner=_default_runner):
        self.name = name
        self.config = Path(config) if config else None
        self.limactl = limactl
        self._run = runner

    def _cmd(self, args: list[str]) -> Completed:
        p = self._run([self.limactl, *args])
        return Completed(p.returncode,
                         p.stdout.decode("utf-8", "replace").strip("\n"),
                         (p.stderr or b"").decode("utf-8", "replace").strip("\n"))

    def is_supported(self) -> Diagnosis:
        from shutil import which
        present = which(self.limactl) is not None
        return Diagnosis([CheckResult(
            "limactl installed", present,
            None if present else "install Lima (brew install lima) or bundle limactl")])

    def exists(self) -> bool:
        out = self._cmd(["list", "--quiet"]).stdout
        return self.name in [line.strip() for line in out.splitlines()]

    def create(self) -> None:
        if self.config is None:
            raise ValueError("config path is required to create the VM")
        self._cmd(["start", f"--name={self.name}", "--tty=false", str(self.config)])

    def start(self) -> None:
        self._cmd(["start", self.name])

    def stop(self) -> None:
        self._cmd(["stop", self.name])

    def destroy(self) -> None:
        self._cmd(["delete", self.name])

    def exec(self, argv: list[str], *, root: bool = False) -> Completed:
        prefix = ["sudo", *argv] if root else list(argv)
        return self._cmd(["shell", self.name, *prefix])

    def forward(self, guest_port: int, host_port: int) -> None:
        # Declared in runtime.yaml portForwards; nothing to do at runtime for
        # the edge port. Dynamic forwards are a later milestone.
        if guest_port != host_port:
            raise NotImplementedError(
                "distinct-port forwarding on Lima is not part of the PoC slice")
```

- [ ] **Step 4: Write `runtime/providers/runtime.yaml`**

```yaml
# UNVERIFIED — macOS/Lima config for parity (see lima.py).
vmType: vz
rosetta:
  enabled: true
images:
  - location: "https://cloud-images.ubuntu.com/releases/24.04/release/ubuntu-24.04-server-cloudimg-arm64.img"
    arch: "aarch64"
cpus: 4
memory: "4GiB"
disk: "60GiB"
mounts: []
portForwards:
  - guestPort: 39080
    hostPort: 39080
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/providers/test_lima.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add runtime/providers/lima.py runtime/providers/runtime.yaml tests/providers/test_lima.py
git commit -m "feat: LimaProvider command construction (unverified) + runtime.yaml"
```

---

### Task 6: WSL2 preconditions (`diagnose_wsl2`) + platform factory

**Files:**
- Modify: `runtime/providers/wsl_checks.py`
- Modify: `runtime/providers/__init__.py`
- Test: `tests/providers/test_wsl_checks.py`, `tests/providers/test_factory.py`

**Interfaces:**
- Consumes: `Diagnosis`, `CheckResult` (Task 2), `decode_wsl` (Task 3).
- Produces:
  - `diagnose_wsl2(runner, wsl="wsl.exe") -> Diagnosis` with checks: `wsl --version present`, `runtime-vm-capable` (version parse), Hyper-V/VirtualBox conflict note.
  - `get_provider() -> VmProvider` in `providers/__init__.py` (the **only** platform branch in the codebase).

- [ ] **Step 1: Write the failing test** `tests/providers/test_wsl_checks.py`

```python
from runtime.providers.wsl_checks import diagnose_wsl2


class R:
    def __init__(self, out=b"", rc=0):
        self.returncode, self.stdout, self.stderr = rc, out, b""


def test_reports_wsl_version_present():
    def runner(argv):
        return R(out="WSL version: 2.6.1.0\r\n".encode("utf-16-le"))
    diag = diagnose_wsl2(runner)
    labels = {c.label: c.ok for c in diag.checks}
    assert labels["wsl --version available"] is True


def test_reports_wsl_version_missing_with_fix():
    def runner(argv):
        return R(out=b"", rc=1)
    diag = diagnose_wsl2(runner)
    missing = [c for c in diag.checks if c.label == "wsl --version available"][0]
    assert missing.ok is False
    assert "wsl --update" in (missing.fix or "")
    assert diag.ok is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/providers/test_wsl_checks.py -v`
Expected: FAIL (returns empty Diagnosis from the Task 4 placeholder).

- [ ] **Step 3: Replace `runtime/providers/wsl_checks.py`**

```python
from ..core.provider import Diagnosis, CheckResult
from .wsl_encoding import decode_wsl


def diagnose_wsl2(runner, wsl="wsl.exe") -> Diagnosis:
    checks: list[CheckResult] = []

    ver = runner([wsl, "--version"])
    ver_text = decode_wsl(ver.stdout)
    has_version = ver.returncode == 0 and "WSL version" in ver_text
    checks.append(CheckResult(
        "wsl --version available", has_version,
        None if has_version else "run `wsl --update` (in-box WSL is too old)"))

    # Conflicts (Hyper-V / VirtualBox / AV) silently break VM creation.
    # We cannot always detect them; surface guidance as a non-blocking note.
    checks.append(CheckResult(
        "no known hypervisor conflict", True,
        "if create fails: check VirtualBox/antivirus and that "
        "VirtualMachinePlatform is enabled"))

    return Diagnosis(checks)
```

- [ ] **Step 4: Run wsl_checks test**

Run: `pytest tests/providers/test_wsl_checks.py -v`
Expected: PASS.

- [ ] **Step 5: Write the failing factory test** `tests/providers/test_factory.py`

```python
import runtime.providers as providers
from runtime.providers.wsl2 import Wsl2Provider
from runtime.providers.lima import LimaProvider


def test_factory_returns_wsl2_on_windows(monkeypatch):
    monkeypatch.setattr(providers.sys, "platform", "win32")
    assert isinstance(providers.get_provider(), Wsl2Provider)


def test_factory_returns_lima_on_macos(monkeypatch):
    monkeypatch.setattr(providers.sys, "platform", "darwin")
    assert isinstance(providers.get_provider(), LimaProvider)


def test_factory_rejects_unsupported(monkeypatch):
    monkeypatch.setattr(providers.sys, "platform", "linux")
    try:
        providers.get_provider()
        assert False, "expected RuntimeError"
    except RuntimeError as e:
        assert "linux" in str(e).lower()
```

- [ ] **Step 6: Write `runtime/providers/__init__.py`** (the only platform branch)

```python
import sys
from pathlib import Path

from .wsl2 import Wsl2Provider
from .lima import LimaProvider


def default_install_dir() -> Path:
    if sys.platform == "win32":
        import os
        base = os.environ.get("LOCALAPPDATA", str(Path.home()))
        return Path(base) / "Runtime" / "vm"
    return Path.home() / ".local" / "share" / "runtime" / "vm"


def get_provider():
    if sys.platform == "win32":
        return Wsl2Provider(install_dir=default_install_dir())
    if sys.platform == "darwin":
        return LimaProvider(config=Path(__file__).parent / "runtime.yaml")
    raise RuntimeError(f"unsupported host platform: {sys.platform} "
                       "(only Windows/WSL2 and macOS/Lima are supported)")
```

- [ ] **Step 7: Run factory test**

Run: `pytest tests/providers/test_factory.py -v`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add runtime/providers/wsl_checks.py runtime/providers/__init__.py tests/providers/test_wsl_checks.py tests/providers/test_factory.py
git commit -m "feat: WSL2 preconditions and platform provider factory"
```

---

### Task 7: `runtime doctor` (M0)

**Files:**
- Create: `runtime/core/diagnose.py`
- Modify: `runtime/cli.py`
- Test: `tests/test_doctor_cli.py`

**Interfaces:**
- Consumes: `Diagnosis` (Task 2), `get_provider` (Task 6).
- Produces: `render_diagnosis(diag: Diagnosis) -> str` (multi-line, one line per check with ✓/✗ and fix hints); `doctor` CLI command.

- [ ] **Step 1: Write the failing test** `tests/test_doctor_cli.py`

```python
from runtime.core.diagnose import render_diagnosis
from runtime.core.provider import Diagnosis, CheckResult


def test_render_marks_pass_and_fail_with_fix():
    text = render_diagnosis(Diagnosis([
        CheckResult("wsl --version available", True),
        CheckResult("virtualization enabled", False, fix="enable VT-x in BIOS"),
    ]))
    assert "wsl --version available" in text
    assert "enable VT-x in BIOS" in text
    # a failed check is visually distinct from a passed one
    pass_line = [l for l in text.splitlines() if "available" in l][0]
    fail_line = [l for l in text.splitlines() if "virtualization" in l][0]
    assert pass_line[:1] != fail_line[:1]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_doctor_cli.py -v`
Expected: FAIL (module not found).

- [ ] **Step 3: Write `runtime/core/diagnose.py`**

```python
from .provider import Diagnosis


def render_diagnosis(diag: Diagnosis) -> str:
    lines = []
    for c in diag.checks:
        mark = "✓" if c.ok else "✗"
        line = f"{mark} {c.label}"
        if c.fix:
            line += f"\n    → {c.fix}"
        lines.append(line)
    verdict = "All required checks passed." if diag.ok else \
        "Some checks failed — see the fixes above."
    lines.append("")
    lines.append(verdict)
    return "\n".join(lines)
```

- [ ] **Step 4: Add the `doctor` command to `runtime/cli.py`** (append before `if __name__`)

```python
from runtime.core.diagnose import render_diagnosis
from runtime.providers import get_provider


@app.command()
def doctor():
    """Report whether this host can run the VM, and how to fix what's missing."""
    provider = get_provider()
    diag = provider.is_supported()
    typer.echo(render_diagnosis(diag))
    raise typer.Exit(code=0 if diag.ok else 1)
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_doctor_cli.py -v`
Expected: PASS.

- [ ] **Step 6: Manual smoke (this host is WSL2 with wsl.exe interop)**

Run: `runtime doctor`
Expected: prints checks; `wsl --version available` shows ✓ on this machine.

- [ ] **Step 7: Commit**

```bash
git add runtime/core/diagnose.py runtime/cli.py tests/test_doctor_cli.py
git commit -m "feat(M0): runtime doctor renders host diagnosis"
```

---

### Task 8: Guest bootstrap assets (bootstrap.sh, traefik.yml) + version constant

**Files:**
- Create: `runtime/guest/bootstrap.sh`
- Create: `runtime/guest/traefik.yml`
- Create: `runtime/core/constants.py`
- Test: `tests/guest/test_bootstrap_shell.py`, `tests/guest/__init__.py`

**Interfaces:**
- Produces: `runtime/core/constants.py` with `EDGE_PORT = 39080`, `BOOTSTRAP_VERSION = 1`, `GUEST_PROJECTS = "/opt/runtime/projects"`, `BOOTSTRAP_MARKER = "/opt/runtime/.bootstrapped"`, `EDGE_NETWORK = "edge"`, `DEFAULT_DOMAIN = "local.eggie.space"`.
- `bootstrap.sh` is idempotent, writes the marker with `BOOTSTRAP_VERSION`.

- [ ] **Step 1: Write `runtime/core/constants.py`**

```python
EDGE_PORT = 39080
BOOTSTRAP_VERSION = 1
GUEST_ROOT = "/opt/runtime"
GUEST_PROJECTS = "/opt/runtime/projects"
BOOTSTRAP_MARKER = "/opt/runtime/.bootstrapped"
EDGE_NETWORK = "edge"
DEFAULT_DOMAIN = "local.eggie.space"
```

- [ ] **Step 2: Write `runtime/guest/traefik.yml`**

```yaml
entryPoints:
  web:
    address: ":39080"
providers:
  docker:
    exposedByDefault: false
    network: edge
```

- [ ] **Step 3: Write `runtime/guest/bootstrap.sh`**

```bash
#!/usr/bin/env bash
set -euo pipefail

MARKER=/opt/runtime/.bootstrapped
WANT_VERSION="${1:-1}"

if [[ -f "$MARKER" ]] && [[ "$(cat "$MARKER")" == "$WANT_VERSION" ]]; then
  echo "already bootstrapped at version $WANT_VERSION"
  exit 0
fi

export DEBIAN_FRONTEND=noninteractive

# 1. docker-ce from the official repo (not Ubuntu's docker.io)
if ! command -v docker >/dev/null 2>&1; then
  apt-get update
  apt-get install -y ca-certificates curl
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
    -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  . /etc/os-release
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
fi

# 2. enable docker (systemd must be on — set via wsl.conf during create())
systemctl enable --now docker

# 3. edge network (idempotent)
docker network inspect edge >/dev/null 2>&1 || docker network create edge

# 4. project root
mkdir -p /opt/runtime/projects

# 5. traefik as a container, single entrypoint on :39080
mkdir -p /opt/runtime/traefik
cp "$(dirname "$0")/traefik.yml" /opt/runtime/traefik/traefik.yml 2>/dev/null || true
docker rm -f traefik >/dev/null 2>&1 || true
docker run -d --name traefik --restart=always --network edge \
  -p 39080:39080 \
  -v /var/run/docker.sock:/var/run/docker.sock:ro \
  -v /opt/runtime/traefik/traefik.yml:/etc/traefik/traefik.yml:ro \
  traefik:v3.1

# 6. marker
echo "$WANT_VERSION" > /opt/runtime/.bootstrapped
echo "bootstrap complete at version $WANT_VERSION"
```

- [ ] **Step 4: Write the failing test** `tests/guest/test_bootstrap_shell.py`

```python
import shutil
import subprocess
from pathlib import Path

BOOTSTRAP = Path("runtime/guest/bootstrap.sh")


def test_bootstrap_is_valid_bash():
    # `bash -n` parses without executing; catches syntax errors.
    result = subprocess.run(["bash", "-n", str(BOOTSTRAP)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_bootstrap_pins_docker_official_repo_not_docker_io():
    text = BOOTSTRAP.read_text()
    assert "download.docker.com" in text
    assert "docker.io" not in text


def test_bootstrap_writes_version_marker():
    text = BOOTSTRAP.read_text()
    assert "/opt/runtime/.bootstrapped" in text
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/guest/test_bootstrap_shell.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add runtime/core/constants.py runtime/guest tests/guest
git commit -m "feat: guest bootstrap script, traefik config, and constants"
```

---

### Task 9: Bootstrap orchestration (idempotent, version-aware) — M2

**Files:**
- Create: `runtime/core/bootstrap.py`
- Test: `tests/core/test_bootstrap.py`

**Interfaces:**
- Consumes: `VmProvider.exec`, `Completed` (Task 2), constants (Task 8).
- Produces: `bootstrap(provider, *, force=False) -> None` — pushes `bootstrap.sh`+`traefik.yml` into the guest and runs it as root; skips when marker already equals `BOOTSTRAP_VERSION` (unless `force`). `read_marker(provider) -> int | None`.

- [ ] **Step 1: Write the failing test** `tests/core/test_bootstrap.py`

```python
from runtime.core.bootstrap import bootstrap, read_marker
from runtime.core.provider import Completed
from runtime.core import constants


class FakeProvider:
    def __init__(self, marker_value=""):
        self.execs = []
        self._marker = marker_value

    def exec(self, argv, *, root=False):
        self.execs.append((argv, root))
        joined = " ".join(argv)
        if "cat" in argv and constants.BOOTSTRAP_MARKER in joined:
            return Completed(0 if self._marker else 1, self._marker, "")
        return Completed(0, "", "")


def test_read_marker_returns_int_when_present():
    assert read_marker(FakeProvider(marker_value="1")) == 1


def test_read_marker_none_when_absent():
    assert read_marker(FakeProvider(marker_value="")) is None


def test_bootstrap_skips_when_marker_current():
    p = FakeProvider(marker_value=str(constants.BOOTSTRAP_VERSION))
    bootstrap(p)
    # only the marker read happened; the script was never run as root
    assert all("bootstrap.sh" not in " ".join(a) for a, _ in p.execs)


def test_bootstrap_runs_script_as_root_when_absent():
    p = FakeProvider(marker_value="")
    bootstrap(p)
    ran = [a for a, root in p.execs if root and "bootstrap.sh" in " ".join(a)]
    assert ran, "expected bootstrap.sh to run as root"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/core/test_bootstrap.py -v`
Expected: FAIL (module not found).

- [ ] **Step 3: Write `runtime/core/bootstrap.py`**

```python
from __future__ import annotations

import base64
from pathlib import Path

from . import constants

_GUEST_DIR = "/opt/runtime/bin"
_GUEST_SCRIPT = f"{_GUEST_DIR}/bootstrap.sh"
_GUEST_TRAEFIK = f"{_GUEST_DIR}/traefik.yml"
_ASSETS = Path(__file__).resolve().parent.parent / "guest"


def read_marker(provider) -> int | None:
    r = provider.exec(["cat", constants.BOOTSTRAP_MARKER])
    if not r.ok or not r.stdout.strip():
        return None
    try:
        return int(r.stdout.strip())
    except ValueError:
        return None


def _push_file(provider, local: Path, remote: str) -> None:
    # base64 avoids quoting/newline issues over `wsl -- bash -lc`.
    encoded = base64.b64encode(local.read_bytes()).decode("ascii")
    provider.exec(
        ["bash", "-lc", f"mkdir -p {_GUEST_DIR} && "
                        f"echo {encoded} | base64 -d > {remote}"],
        root=True,
    )


def bootstrap(provider, *, force: bool = False) -> None:
    if not force and read_marker(provider) == constants.BOOTSTRAP_VERSION:
        return
    _push_file(provider, _ASSETS / "bootstrap.sh", _GUEST_SCRIPT)
    _push_file(provider, _ASSETS / "traefik.yml", _GUEST_TRAEFIK)
    provider.exec(["bash", _GUEST_SCRIPT, str(constants.BOOTSTRAP_VERSION)], root=True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/core/test_bootstrap.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add runtime/core/bootstrap.py tests/core/test_bootstrap.py
git commit -m "feat(M2): idempotent, version-aware guest bootstrap orchestration"
```

---

### Task 10: `runtime vm` commands (M1) — create/start/stop/destroy + bootstrap wiring

**Files:**
- Modify: `runtime/cli.py`
- Test: `tests/test_vm_cli.py`

**Interfaces:**
- Consumes: `get_provider` (Task 6), `bootstrap` (Task 9).
- Produces: a Typer sub-app `vm` with `create`, `start`, `stop`, `destroy`. `create` calls `provider.create()` then `bootstrap(provider)`. CLI resolves the provider via a module-level `_provider_factory` seam so tests inject a fake.

- [ ] **Step 1: Add a provider seam + `vm` sub-app to `runtime/cli.py`**

```python
# near the top, after `app = typer.Typer(...)`
from runtime.providers import get_provider as _get_provider

_provider_factory = _get_provider  # tests override this


def _provider():
    return _provider_factory()


vm = typer.Typer(help="Manage the runtime VM.", no_args_is_help=True)
app.add_typer(vm, name="vm")


@vm.command("create")
def vm_create():
    """Create the VM and bootstrap Docker + Traefik inside it."""
    from runtime.core.bootstrap import bootstrap
    p = _provider()
    if p.exists():
        typer.echo("VM already exists; bootstrapping (idempotent).")
    else:
        typer.echo("Creating VM…")
        p.create()
    bootstrap(p)
    typer.echo("VM ready.")


@vm.command("start")
def vm_start():
    _provider().start()
    typer.echo("VM started.")


@vm.command("stop")
def vm_stop():
    _provider().stop()
    typer.echo("VM stopped.")


@vm.command("destroy")
def vm_destroy():
    _provider().destroy()
    typer.echo("VM destroyed.")
```

- [ ] **Step 2: Write the failing test** `tests/test_vm_cli.py`

```python
from typer.testing import CliRunner
import runtime.cli as cli
from runtime.core.provider import Completed

runner = CliRunner()


class FakeProvider:
    def __init__(self, exists=False):
        self._exists = exists
        self.created = False
        self.execs = []

    def exists(self): return self._exists
    def create(self): self.created = True
    def start(self): pass
    def stop(self): pass
    def destroy(self): pass
    def exec(self, argv, *, root=False):
        self.execs.append(argv)
        # make the marker read report "already current" so bootstrap is a no-op
        from runtime.core import constants
        if "cat" in argv:
            return Completed(0, str(constants.BOOTSTRAP_VERSION), "")
        return Completed(0, "", "")
    def forward(self, g, h): pass
    def is_supported(self): ...


def test_vm_create_creates_when_absent(monkeypatch):
    fake = FakeProvider(exists=False)
    monkeypatch.setattr(cli, "_provider_factory", lambda: fake)
    result = runner.invoke(cli.app, ["vm", "create"])
    assert result.exit_code == 0
    assert fake.created is True
    assert "VM ready." in result.stdout


def test_vm_create_skips_create_when_present(monkeypatch):
    fake = FakeProvider(exists=True)
    monkeypatch.setattr(cli, "_provider_factory", lambda: fake)
    result = runner.invoke(cli.app, ["vm", "create"])
    assert result.exit_code == 0
    assert fake.created is False
```

- [ ] **Step 3: Run tests**

Run: `pytest tests/test_vm_cli.py -v`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add runtime/cli.py tests/test_vm_cli.py
git commit -m "feat(M1): runtime vm create/start/stop/destroy with bootstrap wiring"
```

---

### Task 11: Compose parsing helpers + `detect.py` (M7 logic)

**Files:**
- Create: `runtime/core/compose.py`
- Create: `runtime/core/detect.py`
- Test: `tests/core/test_compose.py`, `tests/core/test_detect.py`

**Interfaces:**
- Produces:
  - `runtime/core/compose.py`: `load_compose(path) -> dict`; `container_port(entry) -> int` (parses a `ports:` entry to its container-side port); `exposed_ports(service: dict) -> list[int]`.
  - `runtime/core/detect.py`: `WebSpec(service: str, port: int, subdomain: str | None = None)`; `AmbiguousError(Exception)`; `detect_web(compose: dict) -> list[WebSpec]`.

- [ ] **Step 1: Write the failing test** `tests/core/test_compose.py`

```python
from runtime.core.compose import container_port, exposed_ports


def test_container_port_from_short_mapping():
    assert container_port("8080:80") == 80


def test_container_port_with_host_ip_and_proto():
    assert container_port("127.0.0.1:8080:80/tcp") == 80


def test_container_port_single_value():
    assert container_port("80") == 80


def test_container_port_long_syntax_dict():
    assert container_port({"published": 8080, "target": 80}) == 80


def test_exposed_ports_prefers_ports_then_expose():
    assert exposed_ports({"ports": ["8080:80"]}) == [80]
    assert exposed_ports({"expose": [9000]}) == [9000]
    assert exposed_ports({}) == []
```

- [ ] **Step 2: Write `runtime/core/compose.py`**

```python
from __future__ import annotations

from pathlib import Path
import yaml


def load_compose(path) -> dict:
    return yaml.safe_load(Path(path).read_text()) or {}


def container_port(entry) -> int:
    if isinstance(entry, dict):  # long syntax
        return int(entry["target"])
    text = str(entry)
    text = text.split("/", 1)[0]          # drop /tcp|/udp
    parts = text.split(":")
    return int(parts[-1])                  # container port is last


def exposed_ports(service: dict) -> list[int]:
    if service.get("ports"):
        return [container_port(p) for p in service["ports"]]
    if service.get("expose"):
        return [int(p) for p in service["expose"]]
    return []
```

- [ ] **Step 3: Run compose test**

Run: `pytest tests/core/test_compose.py -v`
Expected: PASS.

- [ ] **Step 4: Write the failing test** `tests/core/test_detect.py`

```python
import pytest
from runtime.core.detect import detect_web, WebSpec, AmbiguousError


def test_single_service_with_published_port():
    compose = {"services": {"web": {"image": "nginx", "ports": ["8080:80"]}}}
    assert detect_web(compose) == [WebSpec(service="web", port=80)]


def test_single_service_uses_expose_when_no_ports():
    compose = {"services": {"app": {"build": ".", "expose": [3000]}}}
    assert detect_web(compose) == [WebSpec(service="app", port=3000)]


def test_single_service_defaults_to_80_when_nothing_declared():
    compose = {"services": {"app": {"build": "."}}}
    assert detect_web(compose) == [WebSpec(service="app", port=80)]


def test_non_http_services_are_ignored():
    compose = {"services": {
        "web": {"image": "nginx", "ports": ["8080:80"]},
        "redis": {"image": "redis"},
        "db": {"image": "postgres", "expose": [5432]},
    }}
    result = detect_web(compose)
    # only services that publish a host port are treated as web
    assert result == [WebSpec(service="web", port=80)]


def test_two_http_services_both_detected():
    compose = {"services": {
        "frontend": {"image": "node", "ports": ["3000:3000"]},
        "api": {"image": "node", "ports": ["4000:4000"]},
    }}
    result = sorted(detect_web(compose), key=lambda w: w.service)
    assert result == [WebSpec(service="api", port=4000),
                      WebSpec(service="frontend", port=3000)]


def test_multi_service_none_published_is_ambiguous():
    compose = {"services": {
        "a": {"image": "x", "expose": [1000]},
        "b": {"image": "y", "expose": [2000]},
    }}
    with pytest.raises(AmbiguousError):
        detect_web(compose)
```

- [ ] **Step 5: Write `runtime/core/detect.py`**

```python
from __future__ import annotations

from dataclasses import dataclass

from .compose import exposed_ports


@dataclass(frozen=True)
class WebSpec:
    service: str
    port: int
    subdomain: str | None = None


class AmbiguousError(Exception):
    """Auto-detection cannot decide which service serves HTTP."""


def _published(service: dict) -> bool:
    return bool(service.get("ports"))


def detect_web(compose: dict) -> list[WebSpec]:
    services: dict = compose.get("services", {}) or {}
    if not services:
        raise AmbiguousError("compose file declares no services")

    # A service that publishes a host port is a web candidate.
    published = {name: svc for name, svc in services.items() if _published(svc)}
    if published:
        return [WebSpec(service=name, port=exposed_ports(svc)[0])
                for name, svc in published.items()]

    # No published ports. A single service is unambiguous.
    if len(services) == 1:
        name, svc = next(iter(services.items()))
        ports = exposed_ports(svc)
        return [WebSpec(service=name, port=ports[0] if ports else 80)]

    raise AmbiguousError(
        "multiple services and none publish a port; "
        "declare `web:` explicitly in .runtime/project.yml")
```

- [ ] **Step 6: Run detect test**

Run: `pytest tests/core/test_detect.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add runtime/core/compose.py runtime/core/detect.py tests/core/test_compose.py tests/core/test_detect.py
git commit -m "feat(M7): compose port parsing and stack-agnostic web detection"
```

---

### Task 12: `overlay.py` — generate routing overlay, never mutate user compose

**Files:**
- Create: `runtime/core/overlay.py`
- Test: `tests/core/test_overlay.py`

**Interfaces:**
- Consumes: `WebSpec` (Task 11), constants (Task 8).
- Produces: `build_overlay(project_id: str, webs: list[WebSpec], domain: str) -> dict`; `host_for(project_id, web, domain) -> str`.

- [ ] **Step 1: Write the failing test** `tests/core/test_overlay.py`

```python
from runtime.core.overlay import build_overlay, host_for
from runtime.core.detect import WebSpec


def test_host_primary_is_bare_id_additional_prefixed():
    assert host_for("myproj", WebSpec("app", 80), "d.io") == "myproj.d.io"
    assert host_for("myproj", WebSpec("api", 4000, subdomain="api"), "d.io") == "api.myproj.d.io"


def test_overlay_attaches_edge_network_and_router_labels():
    ov = build_overlay("myproj", [WebSpec("app", 8080)], "d.io")
    svc = ov["services"]["app"]
    assert svc["networks"] == ["default", "edge"]
    labels = svc["labels"]
    assert labels["traefik.enable"] == "true"
    assert labels["traefik.http.routers.myproj-app.rule"] == "Host(`myproj.d.io`)"
    assert labels["traefik.http.services.myproj-app.loadbalancer.server.port"] == "8080"
    assert ov["networks"]["edge"] == {"external": True}


def test_overlay_only_contains_web_services():
    ov = build_overlay("p", [WebSpec("web", 80)], "d.io")
    # redis/db never appear — the overlay is additive and touches only web svcs
    assert list(ov["services"].keys()) == ["web"]


def test_two_web_services_get_distinct_hosts_and_routers():
    ov = build_overlay("shop", [
        WebSpec("frontend", 3000),
        WebSpec("api", 4000, subdomain="api"),
    ], "d.io")
    fe = ov["services"]["frontend"]["labels"]
    api = ov["services"]["api"]["labels"]
    assert fe["traefik.http.routers.shop-frontend.rule"] == "Host(`shop.d.io`)"
    assert api["traefik.http.routers.shop-api.rule"] == "Host(`api.shop.d.io`)"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/core/test_overlay.py -v`
Expected: FAIL (module not found).

- [ ] **Step 3: Write `runtime/core/overlay.py`**

```python
from __future__ import annotations

from .detect import WebSpec
from . import constants


def host_for(project_id: str, web: WebSpec, domain: str) -> str:
    if web.subdomain:
        return f"{web.subdomain}.{project_id}.{domain}"
    return f"{project_id}.{domain}"


def build_overlay(project_id: str, webs: list[WebSpec], domain: str) -> dict:
    services: dict = {}
    for web in webs:
        router = f"{project_id}-{web.service}"
        host = host_for(project_id, web, domain)
        services[web.service] = {
            "networks": ["default", constants.EDGE_NETWORK],
            "labels": {
                "traefik.enable": "true",
                f"traefik.http.routers.{router}.rule": f"Host(`{host}`)",
                f"traefik.http.services.{router}.loadbalancer.server.port":
                    str(web.port),
            },
        }
    return {
        "services": services,
        "networks": {constants.EDGE_NETWORK: {"external": True}},
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/core/test_overlay.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add runtime/core/overlay.py tests/core/test_overlay.py
git commit -m "feat: routing overlay generation (additive, never mutates compose)"
```

---

### Task 13: `state.py` — SQLite projects/ports, allocation, restart survival (M5)

**Files:**
- Create: `runtime/core/state.py`
- Test: `tests/core/test_state.py`

**Interfaces:**
- Produces: `State(db_path)` with `add_project(id, guest_path, domain, status="stopped")`, `get_project(id) -> dict | None`, `list_projects() -> list[dict]`, `set_status(id, status)`, `remove_project(id)`, `add_forward(project_id, service, guest_port, host_port)`, `allocate_host_port(start=39100, end=39200) -> int` (lowest free not already in `forwards`). `close()`.

- [ ] **Step 1: Write the failing test** `tests/core/test_state.py`

```python
from runtime.core.state import State


def test_add_and_get_project(tmp_path):
    s = State(tmp_path / "s.db")
    s.add_project("myproj", "/opt/runtime/projects/myproj", "myproj.d.io")
    row = s.get_project("myproj")
    assert row["guest_path"] == "/opt/runtime/projects/myproj"
    assert row["status"] == "stopped"


def test_projects_survive_reopen(tmp_path):
    db = tmp_path / "s.db"
    s = State(db)
    s.add_project("p1", "/g/p1", "p1.d.io", status="running")
    s.close()
    reopened = State(db)
    assert reopened.get_project("p1")["status"] == "running"


def test_allocate_host_port_skips_used(tmp_path):
    s = State(tmp_path / "s.db")
    s.add_project("p", "/g/p", "p.d.io")
    first = s.allocate_host_port(start=39100, end=39110)
    s.add_forward("p", "db", 5432, first)
    second = s.allocate_host_port(start=39100, end=39110)
    assert first == 39100
    assert second == 39101


def test_allocate_raises_when_range_exhausted(tmp_path):
    s = State(tmp_path / "s.db")
    s.add_project("p", "/g/p", "p.d.io")
    s.add_forward("p", "a", 1, 39100)
    try:
        s.allocate_host_port(start=39100, end=39100)
        assert False, "expected RuntimeError"
    except RuntimeError:
        pass
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/core/test_state.py -v`
Expected: FAIL (module not found).

- [ ] **Step 3: Write `runtime/core/state.py`**

```python
from __future__ import annotations

import sqlite3
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    guest_path TEXT NOT NULL,
    domain TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'stopped'
);
CREATE TABLE IF NOT EXISTS forwards (
    project_id TEXT NOT NULL,
    service TEXT NOT NULL,
    guest_port INTEGER NOT NULL,
    host_port INTEGER NOT NULL UNIQUE
);
"""


class State:
    def __init__(self, db_path):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def add_project(self, id, guest_path, domain, status="stopped"):
        self._conn.execute(
            "INSERT OR REPLACE INTO projects(id, guest_path, domain, status) "
            "VALUES (?,?,?,?)", (id, guest_path, domain, status))
        self._conn.commit()

    def get_project(self, id):
        row = self._conn.execute(
            "SELECT * FROM projects WHERE id=?", (id,)).fetchone()
        return dict(row) if row else None

    def list_projects(self):
        return [dict(r) for r in
                self._conn.execute("SELECT * FROM projects ORDER BY id")]

    def set_status(self, id, status):
        self._conn.execute("UPDATE projects SET status=? WHERE id=?", (status, id))
        self._conn.commit()

    def remove_project(self, id):
        self._conn.execute("DELETE FROM forwards WHERE project_id=?", (id,))
        self._conn.execute("DELETE FROM projects WHERE id=?", (id,))
        self._conn.commit()

    def add_forward(self, project_id, service, guest_port, host_port):
        self._conn.execute(
            "INSERT INTO forwards(project_id, service, guest_port, host_port) "
            "VALUES (?,?,?,?)", (project_id, service, guest_port, host_port))
        self._conn.commit()

    def allocate_host_port(self, start=39100, end=39200) -> int:
        used = {r["host_port"] for r in
                self._conn.execute("SELECT host_port FROM forwards")}
        for port in range(start, end + 1):
            if port not in used:
                return port
        raise RuntimeError(f"no free host port in range {start}-{end}")

    def close(self):
        self._conn.close()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/core/test_state.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add runtime/core/state.py tests/core/test_state.py
git commit -m "feat(M5): SQLite state with port allocation and restart survival"
```

---

### Task 14: `project.py` — load project, write overlay, compose up/down, failure classification

**Files:**
- Create: `runtime/core/project.py`
- Test: `tests/core/test_project.py`

**Interfaces:**
- Consumes: `detect_web`/`WebSpec` (Task 11), `build_overlay` (Task 12), constants (Task 8), `Completed`/provider (Task 2).
- Produces:
  - `Status` enum-like constants: `STARTED_OK`, `FAILED_TO_START`, `CRASH_LOOPING`.
  - `classify(up_result: Completed, ps_json: str) -> str` — maps compose up result + `docker compose ps --format json` to a Status.
  - `load_project(compose_dict: dict, project_yml: dict | None, dir_name: str) -> Project` where `Project` has `id: str`, `webs: list[WebSpec]`.
  - `overlay_yaml(project: Project, domain: str) -> str` — YAML text of the overlay.

- [ ] **Step 1: Write the failing test** `tests/core/test_project.py`

```python
from runtime.core.project import (
    load_project, classify, overlay_yaml,
    STARTED_OK, FAILED_TO_START, CRASH_LOOPING,
)
from runtime.core.provider import Completed
import yaml


def test_load_project_uses_explicit_web_over_detection():
    compose = {"services": {"a": {"image": "x", "ports": ["1:2"]},
                            "b": {"image": "y", "ports": ["3:4"]}}}
    proj = load_project(compose, {"id": "custom", "web": [{"service": "b", "port": 4}]}, "dir")
    assert proj.id == "custom"
    assert [w.service for w in proj.webs] == ["b"]


def test_load_project_falls_back_to_detection_and_dirname():
    compose = {"services": {"only": {"image": "nginx", "ports": ["8080:80"]}}}
    proj = load_project(compose, None, "My Dir")
    assert proj.id == "my-dir"          # sanitized directory name
    assert proj.webs[0].service == "only"


def test_classify_failed_to_start_on_nonzero_up():
    assert classify(Completed(1, "", "boom"), "[]") == FAILED_TO_START


def test_classify_crash_looping_on_restarting_container():
    ps = '[{"Service":"web","State":"restarting","ExitCode":1}]'
    assert classify(Completed(0, "", ""), ps) == CRASH_LOOPING


def test_classify_started_ok_when_running():
    ps = '[{"Service":"web","State":"running","ExitCode":0}]'
    assert classify(Completed(0, "", ""), ps) == STARTED_OK


def test_overlay_yaml_roundtrips_to_expected_structure():
    compose = {"services": {"web": {"image": "nginx", "ports": ["8080:80"]}}}
    proj = load_project(compose, None, "p")
    parsed = yaml.safe_load(overlay_yaml(proj, "d.io"))
    assert parsed["services"]["web"]["labels"]["traefik.enable"] == "true"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/core/test_project.py -v`
Expected: FAIL (module not found).

- [ ] **Step 3: Write `runtime/core/project.py`**

```python
from __future__ import annotations

import json
import re
from dataclasses import dataclass

import yaml

from .detect import detect_web, WebSpec
from .overlay import build_overlay

STARTED_OK = "started_ok"
FAILED_TO_START = "failed_to_start"
CRASH_LOOPING = "crash_looping"


@dataclass(frozen=True)
class Project:
    id: str
    webs: list[WebSpec]


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", name.strip().lower()).strip("-")


def load_project(compose_dict: dict, project_yml: dict | None, dir_name: str) -> Project:
    project_yml = project_yml or {}
    pid = project_yml.get("id") or _slug(dir_name)
    if project_yml.get("web"):
        webs = [WebSpec(service=w["service"], port=int(w["port"]),
                        subdomain=w.get("subdomain"))
                for w in project_yml["web"]]
    else:
        webs = detect_web(compose_dict)
    return Project(id=pid, webs=webs)


def overlay_yaml(project: Project, domain: str) -> str:
    overlay = build_overlay(project.id, project.webs, domain)
    return yaml.safe_dump(overlay, sort_keys=False)


def classify(up_result, ps_json: str) -> str:
    if not up_result.ok:
        return FAILED_TO_START
    try:
        rows = json.loads(ps_json) if ps_json.strip() else []
    except json.JSONDecodeError:
        rows = []
    for row in rows:
        state = str(row.get("State", "")).lower()
        if state == "restarting" or (state == "exited" and row.get("ExitCode", 0) != 0):
            return CRASH_LOOPING
    return STARTED_OK
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/core/test_project.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add runtime/core/project.py tests/core/test_project.py
git commit -m "feat: project model, overlay YAML, and start/crash failure classification"
```

---

### Task 15: `runtime up/down/status/logs/destroy` (M4/M6/M8) + guest orchestration

**Files:**
- Create: `runtime/core/lifecycle.py`
- Modify: `runtime/cli.py`
- Test: `tests/core/test_lifecycle.py`

**Interfaces:**
- Consumes: provider (`exec`), `Project`/`load_project`/`overlay_yaml`/`classify` (Task 14), constants (Task 8).
- Produces:
  - `push_project(provider, project_id, local_dir)` — tars the local dir, copies into the guest, unpacks at `/opt/runtime/projects/<id>` (never on host).
  - `compose_up(provider, project, local_dir, domain) -> tuple[str, list[str]]` — writes overlay into the guest project dir, runs `docker compose -f docker-compose.yml -f .runtime/overlay.yml up -d`, returns `(status, urls)`.
  - `compose_down(provider, project_id)`, `project_logs(provider, project_id, service=None) -> str`.

- [ ] **Step 1: Write the failing test** `tests/core/test_lifecycle.py`

```python
from runtime.core.lifecycle import compose_up, _compose_argv
from runtime.core.project import Project, STARTED_OK
from runtime.core.detect import WebSpec
from runtime.core.provider import Completed
from runtime.core import constants


class FakeProvider:
    def __init__(self):
        self.execs = []

    def exec(self, argv, *, root=False):
        self.execs.append(argv)
        joined = " ".join(argv)
        if "ps" in argv and "--format" in joined:
            return Completed(0, '[{"Service":"web","State":"running","ExitCode":0}]', "")
        return Completed(0, "", "")


def test_compose_argv_uses_both_files_in_order():
    argv = _compose_argv("myproj")
    d = f"{constants.GUEST_PROJECTS}/myproj"
    assert argv[:2] == ["docker", "compose"]
    assert argv.count("-f") == 2
    assert f"{d}/docker-compose.yml" in argv
    assert f"{d}/.runtime/overlay.yml" in argv
    assert argv[-2:] == ["up", "-d"]


def test_compose_up_returns_url_and_status(tmp_path):
    (tmp_path / "docker-compose.yml").write_text("services: {}\n")
    proj = Project(id="myproj", webs=[WebSpec("web", 80)])
    p = FakeProvider()
    status, urls = compose_up(p, proj, tmp_path, "d.io")
    assert status == STARTED_OK
    assert f"http://myproj.d.io:{constants.EDGE_PORT}" in urls
    # overlay was written into the guest, compose up ran with both -f files
    assert any("overlay.yml" in " ".join(a) for a in p.execs)
    assert any(a[-2:] == ["up", "-d"] for a in p.execs)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/core/test_lifecycle.py -v`
Expected: FAIL (module not found).

- [ ] **Step 3: Write `runtime/core/lifecycle.py`**

```python
from __future__ import annotations

import base64
import io
import tarfile
from pathlib import Path

from . import constants
from .project import Project, classify, overlay_yaml, STARTED_OK


def _guest_dir(project_id: str) -> str:
    return f"{constants.GUEST_PROJECTS}/{project_id}"


def _compose_argv(project_id: str) -> list[str]:
    d = _guest_dir(project_id)
    return ["docker", "compose",
            "-f", f"{d}/docker-compose.yml",
            "-f", f"{d}/.runtime/overlay.yml",
            "up", "-d"]


def push_project(provider, project_id: str, local_dir) -> None:
    """Tar the local project and unpack it inside the guest (never on host)."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for item in Path(local_dir).iterdir():
            tar.add(item, arcname=item.name)
    encoded = base64.b64encode(buf.getvalue()).decode("ascii")
    d = _guest_dir(project_id)
    provider.exec(["bash", "-lc",
                   f"mkdir -p {d} && echo {encoded} | base64 -d | "
                   f"tar -xzf - -C {d}"], root=True)


def _write_overlay(provider, project: Project, domain: str) -> None:
    d = _guest_dir(project.id)
    text = overlay_yaml(project, domain)
    encoded = base64.b64encode(text.encode("utf-8")).decode("ascii")
    provider.exec(["bash", "-lc",
                   f"mkdir -p {d}/.runtime && echo {encoded} | base64 -d "
                   f"> {d}/.runtime/overlay.yml"], root=True)


def _urls(project: Project, domain: str) -> list[str]:
    from .overlay import host_for
    return [f"http://{host_for(project.id, w, domain)}:{constants.EDGE_PORT}"
            for w in project.webs]


def compose_up(provider, project: Project, local_dir, domain: str):
    _write_overlay(provider, project, domain)
    up = provider.exec(_compose_argv(project.id), root=True)
    ps = provider.exec(["docker", "compose", "-f",
                        f"{_guest_dir(project.id)}/docker-compose.yml",
                        "ps", "--format", "json"], root=True)
    status = classify(up, ps.stdout)
    return status, _urls(project, domain)


def compose_down(provider, project_id: str):
    d = _guest_dir(project_id)
    return provider.exec(["docker", "compose", "-f", f"{d}/docker-compose.yml",
                          "-f", f"{d}/.runtime/overlay.yml", "down"], root=True)


def project_logs(provider, project_id: str, service: str | None = None) -> str:
    d = _guest_dir(project_id)
    argv = ["docker", "compose", "-f", f"{d}/docker-compose.yml", "logs", "--no-color"]
    if service:
        argv.append(service)
    return provider.exec(argv, root=True).stdout
```

- [ ] **Step 4: Run lifecycle test**

Run: `pytest tests/core/test_lifecycle.py -v`
Expected: PASS.

- [ ] **Step 5: Add CLI commands to `runtime/cli.py`** (append before `if __name__`)

```python
from pathlib import Path as _Path


@app.command()
def up(directory: str = typer.Argument(".", help="Project directory with a docker-compose.yml")):
    """Bring a compose project up and print its URL(s)."""
    import yaml
    from runtime.core import constants
    from runtime.core.project import load_project, STARTED_OK, CRASH_LOOPING
    from runtime.core.lifecycle import push_project, compose_up
    from runtime.core.state import State
    from runtime.providers import default_install_dir

    local = _Path(directory).resolve()
    compose_dict = yaml.safe_load((local / "docker-compose.yml").read_text()) or {}
    pyml_path = local / ".runtime" / "project.yml"
    pyml = yaml.safe_load(pyml_path.read_text()) if pyml_path.exists() else None
    project = load_project(compose_dict, pyml, local.name)

    p = _provider()
    push_project(p, project.id, local)
    status, urls = compose_up(p, project, local, constants.DEFAULT_DOMAIN)

    state = State(default_install_dir().parent / "state.db")
    state.add_project(project.id, f"{constants.GUEST_PROJECTS}/{project.id}",
                      constants.DEFAULT_DOMAIN,
                      status="running" if status == STARTED_OK else "error")

    if status == STARTED_OK:
        for u in urls:
            typer.echo(f"  {u}")
    else:
        typer.echo(f"Project status: {status}. Run `runtime logs {project.id}`.")
        raise typer.Exit(code=1)


@app.command()
def down(project_id: str):
    """Stop a project's containers."""
    from runtime.core.lifecycle import compose_down
    compose_down(_provider(), project_id)
    typer.echo(f"{project_id} stopped.")


@app.command()
def status():
    """List known projects and their status."""
    from runtime.core.state import State
    from runtime.core import constants
    from runtime.providers import default_install_dir
    state = State(default_install_dir().parent / "state.db")
    rows = state.list_projects()
    if not rows:
        typer.echo("No projects.")
        return
    for r in rows:
        typer.echo(f"{r['id']:<20} {r['status']:<10} "
                   f"http://{r['id']}.{r['domain']}:{constants.EDGE_PORT}")


@app.command()
def logs(project_id: str, service: str = typer.Option(None)):
    """Show a project's container logs."""
    from runtime.core.lifecycle import project_logs
    typer.echo(project_logs(_provider(), project_id, service))


@app.command()
def destroy(project_id: str):
    """Stop and forget a project."""
    from runtime.core.lifecycle import compose_down
    from runtime.core.state import State
    from runtime.providers import default_install_dir
    compose_down(_provider(), project_id)
    State(default_install_dir().parent / "state.db").remove_project(project_id)
    typer.echo(f"{project_id} destroyed.")
```

- [ ] **Step 6: Run the full suite**

Run: `pytest -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add runtime/core/lifecycle.py runtime/cli.py tests/core/test_lifecycle.py
git commit -m "feat(M4/M8): up/down/status/logs/destroy with guest-side compose overlay"
```

---

### Task 16: Local JSON-RPC API server

**Files:**
- Create: `runtime/api/server.py`
- Test: `tests/api/test_server.py`, `tests/api/__init__.py`

**Interfaces:**
- Consumes: nothing platform-specific; takes a `handlers` dict for testability.
- Produces: `dispatch(request: dict, handlers: dict) -> dict` — JSON-RPC 2.0 dispatch (id echo, method lookup, `-32601` on unknown method, `-32602`/error wrapping); `serve(host="127.0.0.1", port=39099)` — binds `http.server` (not unit-tested).

- [ ] **Step 1: Write the failing test** `tests/api/test_server.py`

```python
from runtime.api.server import dispatch


def test_dispatch_calls_handler_and_echoes_id():
    handlers = {"status": lambda params: {"projects": []}}
    resp = dispatch({"jsonrpc": "2.0", "id": 7, "method": "status", "params": {}}, handlers)
    assert resp == {"jsonrpc": "2.0", "id": 7, "result": {"projects": []}}


def test_dispatch_unknown_method_returns_method_not_found():
    resp = dispatch({"jsonrpc": "2.0", "id": 1, "method": "nope", "params": {}}, {})
    assert resp["error"]["code"] == -32601


def test_dispatch_handler_error_is_wrapped():
    def boom(params): raise ValueError("bad")
    resp = dispatch({"jsonrpc": "2.0", "id": 2, "method": "up", "params": {}},
                    {"up": boom})
    assert resp["error"]["code"] == -32000
    assert "bad" in resp["error"]["message"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/api/test_server.py -v`
Expected: FAIL (module not found).

- [ ] **Step 3: Write `runtime/api/server.py`**

```python
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, HTTPServer


def dispatch(request: dict, handlers: dict) -> dict:
    rid = request.get("id")
    method = request.get("method")
    params = request.get("params") or {}
    handler = handlers.get(method)
    if handler is None:
        return {"jsonrpc": "2.0", "id": rid,
                "error": {"code": -32601, "message": f"method not found: {method}"}}
    try:
        return {"jsonrpc": "2.0", "id": rid, "result": handler(params)}
    except Exception as e:  # surface as a JSON-RPC error, never crash the server
        return {"jsonrpc": "2.0", "id": rid,
                "error": {"code": -32000, "message": str(e)}}


def _make_handler(handlers: dict):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
            resp = dispatch(body, handlers)
            payload = json.dumps(resp).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass
    return Handler


def serve(handlers: dict, host="127.0.0.1", port=39099):
    HTTPServer((host, port), _make_handler(handlers)).serve_forever()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/api/test_server.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add runtime/api/server.py tests/api
git commit -m "feat: local JSON-RPC dispatch and 127.0.0.1 server"
```

---

### Task 17: Templates (starter compose files, NOT engine) + acceptance fixtures

**Files:**
- Create: `runtime/templates/nginx-hello/docker-compose.yml`
- Create: `tests/fixtures/compose/*.yml` (five acceptance shapes)
- Test: `tests/core/test_acceptance_detection.py`

**Interfaces:**
- Consumes: `detect_web` (Task 11), `load_compose` (Task 11).
- Produces: five real-world-shaped compose fixtures the detection must handle with zero stack-specific code.

- [ ] **Step 1: Write `runtime/templates/nginx-hello/docker-compose.yml`**

```yaml
services:
  web:
    image: nginx:alpine
    ports:
      - "8080:80"
```

- [ ] **Step 2: Write five fixtures under `tests/fixtures/compose/`**

`php_nginx_mysql.yml`:
```yaml
services:
  nginx:
    image: nginx:alpine
    ports: ["8080:80"]
  php:
    image: php:8.3-fpm
  mysql:
    image: mysql:8
    expose: ["3306"]
```

`node_postgres.yml`:
```yaml
services:
  app:
    image: node:20
    ports: ["3000:3000"]
  db:
    image: postgres:16
    expose: ["5432"]
```

`python_redis.yml`:
```yaml
services:
  web:
    build: .
    ports: ["5000:5000"]
  redis:
    image: redis:7
```

`build_only.yml`:
```yaml
services:
  app:
    build: .
    expose: ["8000"]
```

`two_http.yml`:
```yaml
services:
  frontend:
    image: node:20
    ports: ["3000:3000"]
  api:
    image: node:20
    ports: ["4000:4000"]
```

- [ ] **Step 3: Write the acceptance test** `tests/core/test_acceptance_detection.py`

```python
from pathlib import Path
from runtime.core.compose import load_compose
from runtime.core.detect import detect_web

FIX = Path("tests/fixtures/compose")


def test_php_nginx_mysql_picks_nginx_only():
    webs = detect_web(load_compose(FIX / "php_nginx_mysql.yml"))
    assert [(w.service, w.port) for w in webs] == [("nginx", 80)]


def test_node_postgres_picks_app():
    webs = detect_web(load_compose(FIX / "node_postgres.yml"))
    assert [(w.service, w.port) for w in webs] == [("app", 3000)]


def test_python_redis_picks_web_build_service():
    webs = detect_web(load_compose(FIX / "python_redis.yml"))
    assert [(w.service, w.port) for w in webs] == [("web", 5000)]


def test_build_only_single_service():
    webs = detect_web(load_compose(FIX / "build_only.yml"))
    assert [(w.service, w.port) for w in webs] == [("app", 8000)]


def test_two_http_services_both():
    webs = sorted(detect_web(load_compose(FIX / "two_http.yml")), key=lambda w: w.service)
    assert [(w.service, w.port) for w in webs] == [("api", 4000), ("frontend", 3000)]
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/core/test_acceptance_detection.py -v`
Expected: PASS (all five shapes, zero stack-specific code).

- [ ] **Step 5: Commit**

```bash
git add runtime/templates tests/fixtures tests/core/test_acceptance_detection.py
git commit -m "test: acceptance detection across five arbitrary compose shapes"
```

---

### Task 18: Architectural boundary test — no platform branching outside providers/

**Files:**
- Test: `tests/test_no_platform_leak.py`

**Interfaces:**
- Consumes: the source tree.
- Produces: a test that fails if `sys.platform`, `platform.system()`, or `os.name` appear anywhere under `runtime/` except `runtime/providers/`.

- [ ] **Step 1: Write the test** `tests/test_no_platform_leak.py`

```python
import re
from pathlib import Path

FORBIDDEN = re.compile(r"sys\.platform|platform\.system\(\)|os\.name")


def test_platform_branching_only_in_providers():
    offenders = []
    for py in Path("runtime").rglob("*.py"):
        if "providers" in py.parts:
            continue
        if FORBIDDEN.search(py.read_text()):
            offenders.append(str(py))
    assert not offenders, f"platform branching leaked outside providers/: {offenders}"
```

- [ ] **Step 2: Run the test**

Run: `pytest tests/test_no_platform_leak.py -v`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/test_no_platform_leak.py
git commit -m "test: enforce platform branching lives only in providers/"
```

---

### Task 19: Full-suite green + README quickstart + live-run runbook

**Files:**
- Create: `README.md`
- Test: (run the whole suite)

**Interfaces:**
- Produces: `README.md` covering install, `runtime doctor`, and the human's live-run steps (rootfs source, `vm create`, `up`, the URL), plus what is verified vs left for the live run.

- [ ] **Step 1: Run the entire suite**

Run: `pytest -v`
Expected: ALL PASS.

- [ ] **Step 2: Write `README.md`**

````markdown
# Local Runtime (PoC)

Bring up a Linux VM, install Docker in it, run any `docker-compose` project, and
get a working URL on the host. Windows/WSL2 (verified by unit tests) and
macOS/Lima (parity, unverified).

## Install

```bash
pip install -e ".[dev]"
```

## Check your host

```bash
runtime doctor
```

## Live run (Windows/WSL2)

The CLI shells out to `wsl.exe`. Provide an Ubuntu 24.04 rootfs tarball (from
`https://cloud-images.ubuntu.com`) — set it on the provider (`ROOTFS`), then:

```bash
runtime vm create        # imports the runtime-vm distro, enables systemd, bootstraps Docker + Traefik
runtime up ./my-project  # prints http://my-project.local.eggie.space:39080
```

`runtime status`, `runtime logs <id>`, `runtime down <id>`, `runtime destroy <id>`.

## Verified vs live

- Verified here: all core logic (detection, overlay, state, failure
  classification), provider command construction, the WSL encoding decoder, and
  the platform-boundary invariant.
- Left for the live run: the multi-GB rootfs/image download, the browser check,
  and the five-compose acceptance test on real Windows and macOS hosts.
````

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: README quickstart and live-run runbook"
```

---

## Self-Review

**Spec coverage:**
- §3 architecture → Tasks 2,4,5,8,15. §4 provider contract → Tasks 2,4,5. §4.1 WSL specifics (encoding, systemd, sibling distro) → Tasks 3,4. §4.2 Lima → Task 5. §4.3 Diagnosis → Tasks 2,6,7. §5 bootstrap → Tasks 8,9. §6 routing/overlay → Task 12. §6.2 project.yml → Task 14. §6.3 detection → Tasks 11,17. §6.4 domain → Task 8 (constant), 12/15 (usage). §7 files-in-VM → Task 15 (`push_project` tars+unpacks in guest). §8 state → Task 13. §9 repo structure → all. §10 failure classification → Task 14. §11 milestones M0–M8 → M0 T7, M1 T10, M2 T9, M3 T8 (traefik on :39080), M4 T15, M5 T13, M6 T15 (multiple projects via state+overlay), M7 T11/T17, M8 T15. §12 testing → every task is TDD. §13 risks: #1 systemd T4, #4 exec-format is deferred (noted below), #5 port range T13. §14 acceptance → Task 17.

**Known deferrals (not gaps — explicitly out of the PoC vertical slice, noted for the executor):**
- Raw-TCP distinct-port forwarding (`forward()` with `guest!=host`) raises `NotImplementedError` (Tasks 4,5) — the DB-client use case is post-slice.
- `exec format error` / arm64 recognition (Risk #4) is a Lima-live concern; left as a message-mapping TODO on the LimaProvider, unverifiable without macOS.
- Host network-share access to guest files (§7) is a convenience, not the execution path; not implemented.

**Placeholder scan:** none — every code/test step carries real content.

**Type consistency:** `Completed`, `Diagnosis`, `CheckResult`, `WebSpec(service,port,subdomain)`, `Project(id,webs)`, status constants `STARTED_OK/FAILED_TO_START/CRASH_LOOPING`, and `_compose_argv`/`compose_up` signatures are used identically across Tasks 2→15. `EDGE_PORT=39080` and `DEFAULT_DOMAIN` come from `constants` (Task 8) everywhere.
