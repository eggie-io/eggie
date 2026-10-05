# Tray App and Launch at Login Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the Eggie desktop app into a tray / menu-bar app whose close button hides the window, whose Quit stops the VM, and which opens at login (on by default after the first setup).

**Architecture:** Pure decisions (`lifecycle.py`, `settings.py`) and a testable `Controller` sit in `host/desktop/`. Everything platform-specific is a provider method in `host/providers/` (tray, autostart, single instance, login-launch detection, Dock policy, graceful stop). `__main__.run()` wires them; `DesktopApi` gains settings and quit; the local UI gains three screens.

**Tech Stack:** Python 3.12, pywebview 6 (`events.closing` returning `False` cancels a close; `create_window(hidden=True)`; `window.show()/hide()`), pystray + Pillow (Windows tray), pyobjc AppKit/Foundation + `SMAppService` (macOS), `multiprocessing.connection` (single instance), Inno Setup, PyInstaller.

**Spec:** `docs/superpowers/specs/2026-10-02-tray-and-launch-at-login-design.md`

## Global Constraints

- Platform branching (`sys.platform`, `platform.system()`, `os.name`) only inside `host/providers/` — `tests/test_no_platform_leak.py`.
- No test spawns `wsl.exe`/`limactl`, touches the registry, a real tray, AppKit or the network. Fakes are injected; assert constructed argv / values.
- Tests reach repo files via `__file__`, never cwd-relative paths.
- `host/` never imports `eggie_api`.
- Every module under `host/` must be reachable by import from `host.cli` (`tests/host/test_no_dead_modules.py`; function-local imports count).
- macOS-only and Windows-only imports (`AppKit`, `Foundation`, `ServiceManagement`, `objc`, `winreg`, `pystray`, `PIL`) are function-local, so the suite imports every module on Linux.
- Run value (exact): `"<exe_path>" setup --background`, under `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`, value name `Eggie`.
- Copy (exact): tray items `Open Eggie`, `Settings`, `Quit Eggie`; checkbox `Open Eggie when I sign in`; first-close notice `Eggie is still running. Find it in the system tray.`; quitting screen `Stopping Eggie…`; background-start failure `Eggie could not start. Open Eggie to see why.`
- `settings.json` lives next to `install-state.json` (`default_install_dir().parent`), keys `autostart_set_once`, `tray_notice_shown`; missing/corrupt = all false.
- WSL graceful stop: `systemctl poweroff` (root) → poll `running()` up to 30 s → `wsl --terminate <distro>` always.
- Quit Eggie stops the VM only if `running()`; exits even if `stop()` raises. The app-update quit never stops the VM.
- Comments only for non-obvious edge cases/workarounds; no ticket references (user rule).
- Test command (the sandbox's `/tmp/pytest-of-$USER` is root-owned):
  `mkdir -p .superpowers/tmp && TMPDIR=$PWD/.superpowers/tmp .venv/bin/python -m pytest -q` — written below as `$PYTEST`.

## Review Focus

1. **Cmd+Q / app-update destroy hitting the close-to-tray handler** — `closing` fires for every close, including `window.destroy()` and macOS Cmd+Q; without an "exiting" flag the app can never quit and an update runs against a live app. Pinned in Task 6 (`test_exit_is_not_turned_into_a_hide`).
2. **Tray Settings/Quit while the projects console (a VM page) is showing** — `window.eggie.route` does not exist there and the bridge is guarded; the controller must load the local UI with a `#route` fragment instead. Pinned in Task 6 (`test_a_route_from_the_console_reloads_the_local_ui`).
3. **Quit while a job runs, then "Quit anyway"** — `JobRegistry` allows one job, so quit must not be a job; it runs on its own thread. Pinned in Task 7 (`test_quit_anyway_runs_while_a_job_is_still_running`).
4. **A user who turned autostart off gets it back after a repair/reinstall** — the flag must survive `InstallState.clear()` / `remove_vm_data`. Pinned in Task 7 (`test_a_second_successful_install_does_not_turn_autostart_back_on`).
5. **Install path with spaces in the Run value** — unquoted, Windows runs `C:\Users\Jane` and fails silently at login. Pinned in Task 2 (`test_run_value_quotes_a_path_with_spaces`).

---

## File Structure

| File | Status | Responsibility |
|------|--------|----------------|
| `host/desktop/settings.py` | create | `Settings`: boolean flags in `settings.json` |
| `host/desktop/lifecycle.py` | create | Pure: `launch_mode`, `turn_on_autostart_once` |
| `host/desktop/controller.py` | create | Window/tray choreography: hide on close, show, routes, exit, background start |
| `host/desktop/__main__.py` | modify | Wiring: flags, single instance, hidden window, tray, controller |
| `host/desktop/api.py` | modify | `get_settings`, `set_autostart`, `quit`; turn-on-once after install |
| `host/desktop/ui/index.html`, `app.js`, `app.css` | modify | `settings`, `quit-confirm`, `quitting` screens; Settings tile; `window.eggie.route` |
| `host/desktop/resources/icon.ico` | create (copy) | App + tray icon |
| `host/providers/instance.py` | create | Single-instance claim over `multiprocessing.connection` |
| `host/providers/tray_win.py` | create | pystray tray |
| `host/providers/tray_mac.py` | create | NSStatusItem tray, Cmd+Q and Dock reopen hooks |
| `host/providers/mac_login.py` | create | SMAppService + login-launch Apple event detection |
| `host/providers/wsl2.py` | modify | Graceful `stop()`, autostart, single instance, tray, no-op desktop hooks |
| `host/providers/lima.py` | modify | Autostart, login watch, Dock policy, tray, single instance (no-op) |
| `host/cli.py` | modify | `setup --background`; `uninstall` turns autostart off |
| `pyproject.toml` | modify | `pystray`, `Pillow` (win32); `Pillow` in `dev` |
| `packaging/windows/eggie.spec`, `installer.iss`, `packaging/macos/eggie.spec` | modify | Icon, bundled resources, hidden imports |
| `docs/release-testing.md`, `host/desktop/CLAUDE.md`, `host/CLAUDE.md` | modify | Manual gates, module roles, gotchas |

### Provider desktop surface (Tasks 2–5 build it; Task 4 pins it)

Both `Wsl2Provider` and `LimaProvider` provide:

```python
def autostart_enabled(self, exe_path: str) -> bool
def set_autostart(self, on: bool, exe_path: str) -> None
def single_instance(self, on_show: Callable[[], None], *, announce: bool) -> bool   # True = we are the first instance
def watch_login_launch(self, on_login: Callable[[], None]) -> None                  # macOS only does anything
def on_window_shown(self, visible: bool) -> None                                    # macOS Dock policy
def tray(self, *, icon: Path, on_open, on_settings, on_quit) -> "Tray"
```

`Tray` (duck-typed): `start() -> None`, `stop() -> None`, `notify(text: str) -> bool` (False = this platform cannot show a notification).

---

### Task 1: Settings store and launch decisions

**Files:**
- Create: `host/desktop/settings.py`, `host/desktop/lifecycle.py`
- Test: `tests/host/desktop/test_settings.py`, `tests/host/desktop/test_lifecycle.py`

**Interfaces:**
- Produces:
  - `Settings(path: Path)`, `.get(key: str) -> bool`, `.set(key: str, value: bool) -> None`
  - constants `AUTOSTART_SET_ONCE = "autostart_set_once"`, `TRAY_NOTICE_SHOWN = "tray_notice_shown"`
  - `WINDOW = "window"`, `TRAY_ONLY = "tray_only"`
  - `launch_mode(*, resume: bool, background: bool, vm_exists: Callable[[], bool]) -> str`
  - `turn_on_autostart_once(settings: Settings, *, available: bool, enable: Callable[[], None]) -> None`

`tests/host/test_no_dead_modules.py` requires every host module to be imported from `host.cli`. The real importers arrive in Task 7 (`api.py`) and Task 9 (`__main__.py`), so this task adds a temporary import line to `host/desktop/api.py` (Step 3), which Task 7 replaces.

- [ ] **Step 1: Write the failing tests**

`tests/host/desktop/test_settings.py`:
```python
from __future__ import annotations

from host.desktop.settings import AUTOSTART_SET_ONCE, TRAY_NOTICE_SHOWN, Settings


def test_a_missing_file_reads_as_all_false(tmp_path):
    settings = Settings(tmp_path / "settings.json")
    assert settings.get(AUTOSTART_SET_ONCE) is False
    assert settings.get(TRAY_NOTICE_SHOWN) is False


def test_a_corrupt_file_reads_as_all_false(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{not json")
    assert Settings(path).get(AUTOSTART_SET_ONCE) is False


def test_a_non_object_file_reads_as_all_false(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("[1, 2]")
    assert Settings(path).get(AUTOSTART_SET_ONCE) is False


def test_setting_one_key_keeps_the_other(tmp_path):
    path = tmp_path / "nested" / "settings.json"
    Settings(path).set(AUTOSTART_SET_ONCE, True)
    Settings(path).set(TRAY_NOTICE_SHOWN, True)
    reread = Settings(path)
    assert reread.get(AUTOSTART_SET_ONCE) is True
    assert reread.get(TRAY_NOTICE_SHOWN) is True
```

`tests/host/desktop/test_lifecycle.py`:
```python
from __future__ import annotations

import pytest

from host.desktop.lifecycle import TRAY_ONLY, WINDOW, launch_mode, turn_on_autostart_once
from host.desktop.settings import AUTOSTART_SET_ONCE, Settings


def _exists(value):
    def check():
        if isinstance(value, Exception):
            raise value
        return value
    return check


@pytest.mark.parametrize("resume, background, exists, expected", [
    (True, True, True, WINDOW),       # --resume wins: setup must continue on screen
    (True, False, True, WINDOW),
    (False, True, False, WINDOW),     # login launch before setup ever finished
    (False, True, True, TRAY_ONLY),
    (False, False, True, WINDOW),
    (False, False, False, WINDOW),
])
def test_launch_mode(resume, background, exists, expected):
    assert launch_mode(resume=resume, background=background,
                       vm_exists=_exists(exists)) == expected


def test_a_provider_that_cannot_answer_opens_the_window():
    # A hidden window over a broken provider is an app the user cannot find.
    assert launch_mode(resume=False, background=True,
                       vm_exists=_exists(RuntimeError("wsl.exe missing"))) == WINDOW


def test_a_normal_launch_never_asks_the_provider():
    def boom():
        raise AssertionError("exists() probed on a plain launch")
    assert launch_mode(resume=False, background=False, vm_exists=boom) == WINDOW


def test_the_first_successful_install_turns_autostart_on(tmp_path):
    settings, calls = Settings(tmp_path / "s.json"), []
    turn_on_autostart_once(settings, available=True, enable=lambda: calls.append(True))
    assert calls == [True]
    assert settings.get(AUTOSTART_SET_ONCE) is True


def test_autostart_is_turned_on_only_once(tmp_path):
    settings, calls = Settings(tmp_path / "s.json"), []
    turn_on_autostart_once(settings, available=True, enable=lambda: calls.append(1))
    turn_on_autostart_once(settings, available=True, enable=lambda: calls.append(2))
    assert calls == [1]


def test_a_failing_enable_still_sets_the_flag(tmp_path):
    settings = Settings(tmp_path / "s.json")

    def fail():
        raise OSError("registry locked")

    turn_on_autostart_once(settings, available=True, enable=fail)
    assert settings.get(AUTOSTART_SET_ONCE) is True


def test_an_unavailable_autostart_is_left_for_a_later_frozen_run(tmp_path):
    settings, calls = Settings(tmp_path / "s.json"), []
    turn_on_autostart_once(settings, available=False, enable=lambda: calls.append(1))
    assert calls == []
    assert settings.get(AUTOSTART_SET_ONCE) is False
```

- [ ] **Step 2: Run to verify they fail**

Run: `$PYTEST tests/host/desktop/test_settings.py tests/host/desktop/test_lifecycle.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'host.desktop.settings'`

- [ ] **Step 3: Implement**

`host/desktop/settings.py`:
```python
"""Desktop preferences that must outlive the VM.

Kept out of install-state.json on purpose: resetting or removing the VM deletes
that file, which would re-arm "turn autostart on once" for a user who had
turned it off.
"""
from __future__ import annotations

import json
from pathlib import Path

AUTOSTART_SET_ONCE = "autostart_set_once"
TRAY_NOTICE_SHOWN = "tray_notice_shown"


class Settings:
    def __init__(self, path):
        self._path = Path(path)

    def _read(self) -> dict:
        try:
            data = json.loads(self._path.read_text())
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def get(self, key: str) -> bool:
        return self._read().get(key) is True

    def set(self, key: str, value: bool) -> None:
        data = self._read()
        data[key] = bool(value)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(data, sort_keys=True))
```

`host/desktop/lifecycle.py`:
```python
"""What a launch does, and when autostart turns itself on. Pure."""
from __future__ import annotations

from typing import Callable

from .settings import AUTOSTART_SET_ONCE, Settings

WINDOW = "window"
TRAY_ONLY = "tray_only"


def launch_mode(*, resume: bool, background: bool, vm_exists: Callable[[], bool]) -> str:
    if resume or not background:
        return WINDOW
    try:
        return TRAY_ONLY if vm_exists() else WINDOW
    except Exception:
        return WINDOW


def turn_on_autostart_once(settings: Settings, *, available: bool,
                           enable: Callable[[], None]) -> None:
    if not available or settings.get(AUTOSTART_SET_ONCE):
        return
    try:
        enable()
    except Exception:
        # Still marked: a broken registry must not be retried on every repair.
        pass
    settings.set(AUTOSTART_SET_ONCE, True)
```

Add to the top of `host/desktop/api.py` (after `from .jobs import JobRegistry`):
```python
from . import lifecycle as _lifecycle, settings as _settings  # noqa: F401
```

- [ ] **Step 4: Run to verify they pass, then the full suite**

Run: `$PYTEST tests/host/desktop/test_settings.py tests/host/desktop/test_lifecycle.py` → PASS
Run: `$PYTEST` → all pass

- [ ] **Step 5: Commit**

```bash
git add host/desktop/settings.py host/desktop/lifecycle.py host/desktop/api.py tests/host/desktop/test_settings.py tests/host/desktop/test_lifecycle.py
git commit -m "feat: settings store and launch-mode decision for the tray app"
```

---

### Task 2: WSL provider — graceful stop and autostart

**Files:**
- Modify: `host/providers/wsl2.py`
- Test: `tests/host/test_wsl2.py` (append)

**Interfaces:**
- Produces on `Wsl2Provider`:
  - constructor kwargs `registry_reader=_default_registry_reader`, `registry_deleter=_default_registry_deleter`, `sleep=time.sleep`, `clock=time.monotonic`
  - `RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"`, `AUTOSTART_VALUE_NAME = "Eggie"` (module constants)
  - `run_value(exe_path: str) -> str` (module function)
  - `autostart_enabled(exe_path: str) -> bool`, `set_autostart(on: bool, exe_path: str) -> None`
  - `stop()` new behaviour

- [ ] **Step 1: Write the failing tests** (append to `tests/host/test_wsl2.py`)

```python
from host.providers.wsl2 import AUTOSTART_VALUE_NAME, RUN_KEY, Wsl2Provider, run_value


class ScriptedRunner:
    """Answers by argv prefix; records every call."""
    def __init__(self, answers):
        self.calls, self._answers = [], answers

    def __call__(self, argv):
        self.calls.append(argv)
        for prefix, (rc, out) in self._answers.items():
            if tuple(argv[:len(prefix)]) == prefix:
                break
        else:
            rc, out = 0, b""
        class R:
            returncode = rc
            stdout = out
            stderr = b""
        return R()


def _stopper(runner, clock_values):
    ticks = iter(clock_values)
    return Wsl2Provider(distro="eggie-vm", wsl="wsl.exe", runner=runner,
                        spawner=[].append, sleep=lambda s: None,
                        clock=lambda: next(ticks))


RUNNING = ("eggie-vm\r\n").encode("utf-16-le")


def test_stop_powers_off_through_systemd_before_terminating():
    runner = ScriptedRunner({("wsl.exe", "-l", "--running", "-q"): (1, b"")})
    _stopper(runner, [0, 1]).stop()
    assert runner.calls[0] == ["wsl.exe", "-d", "eggie-vm", "-u", "root", "--",
                               "systemctl", "poweroff"]
    assert runner.calls[-1] == ["wsl.exe", "--terminate", "eggie-vm"]


def test_stop_terminates_even_when_poweroff_fails():
    runner = ScriptedRunner({
        ("wsl.exe", "-d", "eggie-vm", "-u", "root", "--", "systemctl"): (1, b"boom"),
        ("wsl.exe", "-l", "--running", "-q"): (1, b""),
    })
    _stopper(runner, [0, 1]).stop()
    assert runner.calls[-1] == ["wsl.exe", "--terminate", "eggie-vm"]


def test_stop_gives_up_waiting_after_thirty_seconds_and_terminates():
    runner = ScriptedRunner({("wsl.exe", "-l", "--running", "-q"): (0, RUNNING)})
    _stopper(runner, [0, 10, 20, 31]).stop()
    polls = [c for c in runner.calls if c[:3] == ["wsl.exe", "-l", "--running"]]
    assert len(polls) == 3
    assert runner.calls[-1] == ["wsl.exe", "--terminate", "eggie-vm"]


def test_run_value_quotes_a_path_with_spaces():
    exe = r"C:\Users\Jane Doe\AppData\Local\Programs\Eggie\setup.exe"
    assert run_value(exe) == f'"{exe}" setup --background'


def _autostart(store):
    def reader(key, name):
        return store.get((key, name))

    def writer(key, name, value):
        store[(key, name)] = value

    def deleter(key, name):
        store.pop((key, name), None)

    return Wsl2Provider(distro="eggie-vm", wsl="wsl.exe", runner=lambda a: None,
                        spawner=[].append, registry_reader=reader,
                        registry_writer=writer, registry_deleter=deleter)


EXE = r"C:\Program Files\Eggie\setup.exe"


def test_set_autostart_on_writes_the_run_value():
    store = {}
    _autostart(store).set_autostart(True, EXE)
    assert store == {(RUN_KEY, AUTOSTART_VALUE_NAME): run_value(EXE)}


def test_set_autostart_off_deletes_the_run_value():
    store = {(RUN_KEY, AUTOSTART_VALUE_NAME): run_value(EXE)}
    _autostart(store).set_autostart(False, EXE)
    assert store == {}


def test_autostart_reads_enabled_only_for_this_exe():
    store = {(RUN_KEY, AUTOSTART_VALUE_NAME): run_value(r"C:\old\setup.exe")}
    provider = _autostart(store)
    assert provider.autostart_enabled(EXE) is False
    provider.set_autostart(True, EXE)
    assert provider.autostart_enabled(EXE) is True


def test_autostart_reads_disabled_when_the_value_is_missing():
    assert _autostart({}).autostart_enabled(EXE) is False
```

- [ ] **Step 2: Run to verify they fail**

Run: `$PYTEST tests/host/test_wsl2.py`
Expected: FAIL — `ImportError: cannot import name 'AUTOSTART_VALUE_NAME'`

- [ ] **Step 3: Implement** in `host/providers/wsl2.py`

Add `import time` to the imports. Next to `RUNONCE_KEY`:
```python
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
AUTOSTART_VALUE_NAME = "Eggie"
# Docker's own shutdown-timeout is 15 s; this leaves systemd room for the rest.
POWEROFF_WAIT = 30.0
```

Below `_default_registry_writer`:
```python
def _default_registry_reader(key: str, name: str) -> str | None:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as handle:
            value, _kind = winreg.QueryValueEx(handle, name)
    except FileNotFoundError:
        return None
    return value if isinstance(value, str) else None


def _default_registry_deleter(key: str, name: str) -> None:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_SET_VALUE) as handle:
            winreg.DeleteValue(handle, name)
    except FileNotFoundError:
        pass


def run_value(exe_path: str) -> str:
    return f'"{exe_path}" setup --background'
```

Constructor: add parameters `registry_reader=_default_registry_reader, registry_deleter=_default_registry_deleter, sleep=time.sleep, clock=time.monotonic` and store them as `self._read_registry`, `self._delete_registry`, `self._sleep`, `self._clock`.

Replace `stop()`:
```python
    def stop(self) -> None:
        # --terminate alone pulls the plug on Docker and every project
        # database. Powering off through systemd stops them first; whether
        # poweroff also ends the distro depends on the WSL version, so
        # --terminate follows either way.
        self.exec(["systemctl", "poweroff"], root=True)
        deadline = self._clock() + POWEROFF_WAIT
        while self.running() and self._clock() < deadline:
            self._sleep(1)
        self._require(self._meta(["--terminate", self.distro]),
                      f"the virtual machine '{self.distro}' could not be stopped")
```

Note: `exec` returns a `Completed` (never raises except `VmUnresponsive`). Wrap the poweroff in `try/except VmUnresponsive: pass` so a hung distro still reaches `--terminate`:
```python
        try:
            self.exec(["systemctl", "poweroff"], root=True)
        except VmUnresponsive:
            pass
```

Add after `register_resume`:
```python
    def autostart_enabled(self, exe_path: str) -> bool:
        return self._read_registry(RUN_KEY, AUTOSTART_VALUE_NAME) == run_value(exe_path)

    def set_autostart(self, on: bool, exe_path: str) -> None:
        if on:
            self._write_registry(RUN_KEY, AUTOSTART_VALUE_NAME, run_value(exe_path))
        else:
            self._delete_registry(RUN_KEY, AUTOSTART_VALUE_NAME)
```

- [ ] **Step 4: Run tests**

Run: `$PYTEST tests/host/test_wsl2.py` → PASS. Then `$PYTEST` → all pass. If an existing test asserted `stop()` issues only `--terminate`, update it to the new order (the new order is the spec, decision 5).

- [ ] **Step 5: Commit**

```bash
git add host/providers/wsl2.py tests/host/test_wsl2.py
git commit -m "feat: graceful WSL stop and Run-key autostart"
```

---

### Task 3: Single instance

**Files:**
- Create: `host/providers/instance.py`
- Modify: `host/providers/wsl2.py`, `host/providers/lima.py`
- Test: `tests/host/test_instance.py`

**Interfaces:**
- Produces:
  - `claim(address: str, family: str, on_show: Callable[[], None], *, announce: bool, authkey: bytes = AUTHKEY) -> bool` — True when this process is the first instance (and now listens); False when another instance answered (and was sent `"show"` if `announce`).
  - `Wsl2Provider.single_instance(on_show, *, announce) -> bool` using `\\.\pipe\eggie-<username>`, family `"AF_PIPE"`.
  - `LimaProvider.single_instance(on_show, *, announce) -> bool` → always `True` (LaunchServices already keeps one instance; the reopen event is handled by the tray, Task 5).

- [ ] **Step 1: Write the failing tests** — `tests/host/test_instance.py`. AF_UNIX stands in for AF_PIPE: same `multiprocessing.connection` code path, available on Linux.

```python
from __future__ import annotations

import threading

from host.providers.instance import claim


def _address(tmp_path):
    return str(tmp_path / "eggie.sock")


def test_the_first_instance_claims_and_a_second_is_turned_away(tmp_path):
    shown = threading.Event()
    assert claim(_address(tmp_path), "AF_UNIX", shown.set, announce=True) is True
    assert claim(_address(tmp_path), "AF_UNIX", lambda: None, announce=True) is False
    assert shown.wait(timeout=5), "the first instance was never asked to show"


def test_a_background_second_instance_does_not_show_the_first(tmp_path):
    shown = threading.Event()
    claim(_address(tmp_path), "AF_UNIX", shown.set, announce=True)
    assert claim(_address(tmp_path), "AF_UNIX", lambda: None, announce=False) is False
    assert not shown.wait(timeout=0.5)


def test_with_no_first_instance_startup_proceeds(tmp_path):
    assert claim(_address(tmp_path), "AF_UNIX", lambda: None, announce=True) is True


def test_the_lima_provider_leaves_single_instance_to_launchservices():
    from pathlib import Path
    from host.providers.lima import LimaProvider
    provider = LimaProvider(config=Path("/tmp/eggie.yaml"), runner=lambda a: None)
    assert provider.single_instance(lambda: None, announce=True) is True
```

- [ ] **Step 2: Run to verify they fail**

Run: `$PYTEST tests/host/test_instance.py` → FAIL (`No module named 'host.providers.instance'`)

- [ ] **Step 3: Implement**

`host/providers/instance.py`:
```python
"""One running Eggie per user.

A second launch (Start menu, the login entry, the post-update relaunch) hands
the first one a "show" and exits instead of opening a second tray icon.
"""
from __future__ import annotations

import threading
from multiprocessing.connection import Client, Listener
from typing import Callable

AUTHKEY = b"eggie-single-instance"
SHOW = "show"
HELLO = "hello"


def claim(address: str, family: str, on_show: Callable[[], None], *,
          announce: bool, authkey: bytes = AUTHKEY) -> bool:
    try:
        conn = Client(address, family=family, authkey=authkey)
    except Exception:
        # Nothing listening (FileNotFoundError / ConnectionRefusedError), or a
        # stranger on the address failing the handshake: either way, run.
        return _listen(address, family, on_show, authkey)
    with conn:
        conn.send(SHOW if announce else HELLO)
    return False


def _listen(address, family, on_show, authkey) -> bool:
    listener = Listener(address, family=family, authkey=authkey)

    def serve():
        while True:
            try:
                with listener.accept() as conn:
                    if conn.recv() == SHOW:
                        on_show()
            except Exception:
                continue

    threading.Thread(target=serve, daemon=True, name="eggie-instance").start()
    return True
```

`host/providers/wsl2.py` — add `import getpass` and:
```python
    def single_instance(self, on_show, *, announce: bool) -> bool:
        from .instance import claim
        # Pipe names are machine-wide: without the user name, another user's
        # Eggie would answer and show its window instead.
        address = rf"\\.\pipe\eggie-{getpass.getuser()}"
        return claim(address, "AF_PIPE", on_show, announce=announce)
```

`host/providers/lima.py`:
```python
    def single_instance(self, on_show, *, announce: bool) -> bool:
        """LaunchServices keeps one instance of a .app; a second open arrives
        as the reopen event, which the tray turns into Open Eggie."""
        return True
```

- [ ] **Step 4: Run tests** — `$PYTEST tests/host/test_instance.py` → PASS; `$PYTEST` → all pass.

- [ ] **Step 5: Commit**

```bash
git add host/providers/instance.py host/providers/wsl2.py host/providers/lima.py tests/host/test_instance.py
git commit -m "feat: single running instance per user"
```

---

### Task 4: macOS autostart, login-launch detection, Dock policy; surface pinned

**Files:**
- Create: `host/providers/mac_login.py`
- Modify: `host/providers/lima.py`, `host/providers/wsl2.py`
- Test: `tests/host/test_provider_surface.py` (extend), `tests/host/test_lima.py` (append)

**Interfaces:**
- Produces on `LimaProvider`: constructor kwarg `login_items=None` (an object with `enabled() -> bool`, `register()`, `unregister()`; default `mac_login.MainAppLoginItem()`), `autostart_enabled(exe_path) -> bool`, `set_autostart(on, exe_path)`, `watch_login_launch(on_login)`, `on_window_shown(visible)`.
- Produces on `Wsl2Provider`: `watch_login_launch(on_login)` (no-op; Windows passes `--background`), `on_window_shown(visible)` (no-op).
- `mac_login.MainAppLoginItem`, `mac_login.install_login_launch_handler(on_login: Callable[[], None]) -> None`, `mac_login.set_dock_visible(visible: bool) -> None`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/host/test_provider_surface.py`:
```python
# What the desktop app asks of a provider to live in the tray and open at
# login. Not lifecycle and not install, so it is pinned separately; a provider
# missing one fails only on that platform, at the user's first launch.
DESKTOP_SURFACE = {"autostart_enabled", "set_autostart", "single_instance",
                   "watch_login_launch", "on_window_shown"}


def test_every_provider_offers_the_desktop_surface():
    from host.providers.lima import LimaProvider
    from host.providers.wsl2 import Wsl2Provider

    for cls in (LimaProvider, Wsl2Provider):
        missing = [name for name in DESKTOP_SURFACE if not callable(getattr(cls, name, None))]
        assert not missing, f"{cls.__name__} does not implement {missing}"


def test_the_desktop_surface_stays_out_of_the_lifecycle_protocol():
    assert not (DESKTOP_SURFACE & _protocol_methods())
```

Append to `tests/host/test_lima.py`:
```python
class FakeLoginItems:
    def __init__(self, enabled=False, fail=None):
        self._enabled, self._fail, self.calls = enabled, fail, []

    def enabled(self):
        return self._enabled

    def register(self):
        self.calls.append("register")
        if self._fail:
            raise self._fail
        self._enabled = True

    def unregister(self):
        self.calls.append("unregister")
        self._enabled = False


def _with_login_items(items):
    return LimaProvider(name="eggie-vm", config=Path("/tmp/eggie.yaml"),
                        limactl="limactl", runner=FakeRunner(), mac_ver=_mac(),
                        login_items=items)


def test_set_autostart_registers_and_unregisters_the_main_app():
    items = FakeLoginItems()
    provider = _with_login_items(items)
    provider.set_autostart(True, "/ignored")
    assert provider.autostart_enabled("/ignored") is True
    provider.set_autostart(False, "/ignored")
    assert items.calls == ["register", "unregister"]
    assert provider.autostart_enabled("/ignored") is False


def test_a_refused_registration_reaches_the_caller():
    import pytest
    provider = _with_login_items(FakeLoginItems(fail=RuntimeError("not approved")))
    with pytest.raises(RuntimeError, match="not approved"):
        provider.set_autostart(True, "/ignored")
```

- [ ] **Step 2: Run to verify they fail**

Run: `$PYTEST tests/host/test_provider_surface.py tests/host/test_lima.py` → FAIL (missing methods / unexpected kwarg `login_items`).

In this task `DESKTOP_SURFACE` is written **without** `"tray"`; Task 5 adds it together with the trays.

- [ ] **Step 3: Implement**

`host/providers/mac_login.py`:
```python
"""Open at login on macOS, and telling a login launch from a user launch.

SMAppService (macOS 13+, our minimum) registers the app itself as a Login
Item. It launches the app with no arguments, so a login launch is recognised
from the open-application Apple event instead of a --background flag.
"""
from __future__ import annotations

from typing import Callable

# keyAEPropData / keyAELaunchedAsLogInItem, as four-char codes.
_PROP_DATA = int.from_bytes(b"prdt", "big")
_LAUNCHED_AS_LOGIN_ITEM = int.from_bytes(b"lgit", "big")
_CORE_EVENT_CLASS = int.from_bytes(b"aevt", "big")
_OPEN_APPLICATION = int.from_bytes(b"oapp", "big")


class MainAppLoginItem:
    def _service(self):
        from ServiceManagement import SMAppService
        return SMAppService.mainAppService()

    def enabled(self) -> bool:
        from ServiceManagement import SMAppServiceStatusEnabled
        return self._service().status() == SMAppServiceStatusEnabled

    def register(self) -> None:
        ok, error = self._service().registerAndReturnError_(None)
        if not ok:
            raise RuntimeError(f"macOS refused to add Eggie to Login Items: {error}")

    def unregister(self) -> None:
        ok, error = self._service().unregisterAndReturnError_(None)
        if not ok:
            raise RuntimeError(f"macOS refused to remove Eggie from Login Items: {error}")


def install_login_launch_handler(on_login: Callable[[], None]) -> None:
    """Replace the open-application handler for this launch.

    AppKit installs its own oapp handler in finishLaunching, so ours goes in
    from applicationWillFinishLaunching_, added to pywebview's app delegate,
    which is the documented point where an app may override it.
    """
    import objc
    from Foundation import NSAppleEventManager, NSObject
    from webview.platforms.cocoa import BrowserView

    class _OpenHandler(NSObject):
        def handleOpen_withReply_(self, event, reply):
            descriptor = event.paramDescriptorForKeyword_(_PROP_DATA)
            if descriptor is not None and descriptor.enumCodeValue() == _LAUNCHED_AS_LOGIN_ITEM:
                on_login()

    handler = _OpenHandler.alloc().init()

    def applicationWillFinishLaunching_(self, notification):
        NSAppleEventManager.sharedAppleEventManager() \
            .setEventHandler_andSelector_forEventClass_andEventID_(
                handler, b"handleOpen:withReply:", _CORE_EVENT_CLASS, _OPEN_APPLICATION)

    objc.classAddMethods(BrowserView.AppDelegate, [applicationWillFinishLaunching_])
    # Kept alive for the life of the app: the event manager does not retain it.
    install_login_launch_handler._handler = handler


def set_dock_visible(visible: bool) -> None:
    from AppKit import (NSApplication, NSApplicationActivationPolicyAccessory,
                        NSApplicationActivationPolicyRegular)
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyRegular if visible
                             else NSApplicationActivationPolicyAccessory)
    if visible:
        app.activateIgnoringOtherApps_(True)
```

`host/providers/lima.py`: constructor gains `login_items=None`, stored as `self._login_items = login_items` (resolved lazily so Linux tests never import ServiceManagement):
```python
    def _items(self):
        if self._login_items is None:
            from .mac_login import MainAppLoginItem
            self._login_items = MainAppLoginItem()
        return self._login_items

    def autostart_enabled(self, exe_path: str) -> bool:
        return self._items().enabled()

    def set_autostart(self, on: bool, exe_path: str) -> None:
        """exe_path is unused: SMAppService registers this .app bundle itself."""
        if on:
            self._items().register()
        else:
            self._items().unregister()

    def watch_login_launch(self, on_login) -> None:
        from .mac_login import install_login_launch_handler
        install_login_launch_handler(on_login)

    def on_window_shown(self, visible: bool) -> None:
        from .mac_login import set_dock_visible
        set_dock_visible(visible)
```

`host/providers/wsl2.py`:
```python
    def watch_login_launch(self, on_login) -> None:
        """Nothing to watch: the Run value passes --background itself."""

    def on_window_shown(self, visible: bool) -> None:
        """The taskbar button follows the window on its own."""
```

Add `pyobjc-framework-ServiceManagement>=10.1; sys_platform == 'darwin'` to `pyproject.toml` dependencies (next to the other pyobjc packages).

- [ ] **Step 4: Run tests** — `$PYTEST tests/host/test_provider_surface.py tests/host/test_lima.py tests/host/test_host_dependencies.py` → PASS; `$PYTEST` → all pass.

- [ ] **Step 5: Commit**

```bash
git add host/providers/mac_login.py host/providers/lima.py host/providers/wsl2.py pyproject.toml tests/host/test_provider_surface.py tests/host/test_lima.py
git commit -m "feat: macOS login item, login-launch detection and Dock policy"
```

---

### Task 5: Trays

**Files:**
- Create: `host/providers/tray_win.py`, `host/providers/tray_mac.py`
- Modify: `host/providers/wsl2.py`, `host/providers/lima.py`, `pyproject.toml`, `tests/host/test_provider_surface.py` (add `"tray"` to `DESKTOP_SURFACE`), `tests/host/test_host_dependencies.py`

**Interfaces:**
- Consumes: Task 4's `DESKTOP_SURFACE`.
- Produces: `Wsl2Provider.tray(*, icon: Path, on_open, on_settings, on_quit) -> WinTray`; `LimaProvider.tray(...) -> MacTray`. Both: `start()`, `stop()`, `notify(text) -> bool`. Labels from Global Constraints.

pystray, AppKit and the menu bar are not unit-tested (manual gates, Task 10). Only the dependency declaration is.

- [ ] **Step 1: Write the failing test** (append to `tests/host/test_host_dependencies.py`)

```python
def test_the_windows_tray_dependencies_are_platform_scoped():
    # pystray's macOS backend needs the main thread pywebview already owns, so
    # the mac tray is native; pystray and Pillow are Windows-only at runtime.
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    deps = data["project"]["dependencies"]
    for name in ("pystray", "Pillow"):
        dep = next((d for d in deps if d.startswith(name)), None)
        assert dep, f"{name} is not declared"
        assert "sys_platform == 'win32'" in dep
```

In `tests/host/test_provider_surface.py` set `DESKTOP_SURFACE` to include `"tray"`.

- [ ] **Step 2: Run to verify it fails** — `$PYTEST tests/host/test_host_dependencies.py tests/host/test_provider_surface.py` → FAIL.

- [ ] **Step 3: Implement**

`pyproject.toml` dependencies:
```toml
    "pystray>=0.19; sys_platform == 'win32'",
    "Pillow>=10.0; sys_platform == 'win32'",
```
and the `dev` extra becomes `["pytest>=8", "pyinstaller>=6.10", "Pillow>=10.0"]` (PyInstaller converts `icon.ico` to `.icns` with it on macOS).

`host/providers/tray_win.py`:
```python
"""The Windows notification-area icon."""
from __future__ import annotations

import threading
from pathlib import Path


class WinTray:
    def __init__(self, *, icon: Path, on_open, on_settings, on_quit):
        self._paths = icon
        self._callbacks = (on_open, on_settings, on_quit)
        self._icon = None

    def start(self) -> None:
        import pystray
        from PIL import Image

        on_open, on_settings, on_quit = self._callbacks
        menu = pystray.Menu(
            # default=True is what a left-click on the icon runs.
            pystray.MenuItem("Open Eggie", lambda: on_open(), default=True),
            pystray.MenuItem("Settings", lambda: on_settings()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit Eggie", lambda: on_quit()),
        )
        self._icon = pystray.Icon("Eggie", Image.open(self._paths), "Eggie", menu)
        # The Win32 backend runs its own message loop, so it can live off the
        # main thread that webview.start() owns.
        threading.Thread(target=self._icon.run, daemon=True, name="eggie-tray").start()

    def stop(self) -> None:
        if self._icon is not None:
            self._icon.stop()

    def notify(self, text: str) -> bool:
        if self._icon is None:
            return False
        self._icon.notify(text, "Eggie")
        return True
```

`host/providers/tray_mac.py`:
```python
"""The macOS menu-bar item, plus the two app-level hooks a tray app needs.

pywebview's Cocoa loop owns the main thread and AppKit refuses status items
from any other, so everything here is scheduled onto that loop.
"""
from __future__ import annotations

from pathlib import Path


class MacTray:
    def __init__(self, *, icon: Path, on_open, on_settings, on_quit):
        self._icon_path = icon
        self._on_open, self._on_settings, self._on_quit = on_open, on_settings, on_quit
        self._item = None
        self._target = None

    def start(self) -> None:
        from PyObjCTools import AppHelper
        self._install_app_hooks()
        AppHelper.callAfter(self._build)

    def _build(self) -> None:
        from AppKit import NSImage, NSMenu, NSMenuItem, NSStatusBar, NSVariableStatusItemLength
        from Foundation import NSObject

        on_open, on_settings, on_quit = self._on_open, self._on_settings, self._on_quit

        class _Target(NSObject):
            def open_(self, sender): on_open()
            def settings_(self, sender): on_settings()
            def quit_(self, sender): on_quit()

        self._target = _Target.alloc().init()
        menu = NSMenu.alloc().init()
        for title, action in (("Open Eggie", b"open:"), ("Settings", b"settings:"),
                              (None, None), ("Quit Eggie", b"quit:")):
            if title is None:
                menu.addItem_(NSMenuItem.separatorItem())
                continue
            entry = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, action, "")
            entry.setTarget_(self._target)
            menu.addItem_(entry)

        self._item = NSStatusBar.systemStatusBar().statusItemWithLength_(NSVariableStatusItemLength)
        image = NSImage.alloc().initWithContentsOfFile_(str(self._icon_path))
        image.setSize_((18, 18))
        self._item.button().setImage_(image)
        self._item.setMenu_(menu)
        self._retarget_cmd_q()

    def _retarget_cmd_q(self) -> None:
        # pywebview's Quit item calls terminate:, which runs every window's
        # closing handler -- and ours turns a close into a hide. Cmd+Q must
        # mean Quit Eggie instead.
        from AppKit import NSApplication
        main_menu = NSApplication.sharedApplication().mainMenu()
        if main_menu is None:
            return
        for top in main_menu.itemArray():
            submenu = top.submenu()
            for entry in (submenu.itemArray() if submenu else []):
                if entry.keyEquivalent() == "q":
                    entry.setTarget_(self._target)
                    entry.setAction_(b"quit:")

    def _install_app_hooks(self) -> None:
        import objc
        from webview.platforms.cocoa import BrowserView

        on_open = self._on_open

        def applicationShouldHandleReopen_hasVisibleWindows_(self, app, visible):
            on_open()
            return True

        objc.classAddMethods(BrowserView.AppDelegate,
                             [applicationShouldHandleReopen_hasVisibleWindows_])

    def stop(self) -> None:
        if self._item is None:
            return
        from AppKit import NSStatusBar
        from PyObjCTools import AppHelper
        item, self._item = self._item, None
        AppHelper.callAfter(NSStatusBar.systemStatusBar().removeStatusItem_, item)

    def notify(self, text: str) -> bool:
        # UNUserNotificationCenter needs a signed app; until then the caller
        # falls back to showing the window where it matters.
        return False
```

`host/providers/wsl2.py`:
```python
    def tray(self, *, icon, on_open, on_settings, on_quit):
        from .tray_win import WinTray
        return WinTray(icon=icon, on_open=on_open, on_settings=on_settings, on_quit=on_quit)
```
`host/providers/lima.py`:
```python
    def tray(self, *, icon, on_open, on_settings, on_quit):
        from .tray_mac import MacTray
        return MacTray(icon=icon, on_open=on_open, on_settings=on_settings, on_quit=on_quit)
```

Install locally so later tasks can import on Linux if needed: nothing to install (the markers exclude Linux).

- [ ] **Step 4: Run tests** — `$PYTEST tests/host/test_host_dependencies.py tests/host/test_provider_surface.py` → PASS; `$PYTEST` → all pass.

- [ ] **Step 5: Commit**

```bash
git add host/providers/tray_win.py host/providers/tray_mac.py host/providers/wsl2.py host/providers/lima.py pyproject.toml tests/host/test_host_dependencies.py tests/host/test_provider_surface.py
git commit -m "feat: tray icon for Windows and menu-bar item for macOS"
```

---

### Task 6: Controller

**Files:**
- Create: `host/desktop/controller.py`
- Test: `tests/host/desktop/test_controller.py`

**Interfaces:**
- Consumes: `Settings`, `TRAY_NOTICE_SHOWN` (Task 1); provider `on_window_shown`, `start` (Tasks 4); `Shell.is_local()`, `Shell.local_url()` (existing `host/desktop/shell.py`).
- Produces:
  ```python
  class Controller:
      def __init__(self, provider, settings: Settings, shell: Shell)
      window: Any; tray: Any            # assigned by __main__ after creation
      def on_closing(self) -> bool      # pywebview closing handler; False cancels
      def show(self) -> None
      def open_route(self, route: str) -> None   # "settings" | "quit"
      def exit(self) -> None            # really closes: tray off, window destroyed
      def on_login_launch(self) -> None # macOS: a login launch detected after start
      def start_vm_in_background(self) -> threading.Thread
  ```
  Notice texts: `TRAY_NOTICE = "Eggie is still running. Find it in the system tray."`, `START_FAILED = "Eggie could not start. Open Eggie to see why."` (module constants).

- [ ] **Step 1: Write the failing tests** — `tests/host/desktop/test_controller.py`

```python
from __future__ import annotations

from host.desktop.controller import START_FAILED, TRAY_NOTICE, Controller
from host.desktop.settings import TRAY_NOTICE_SHOWN, Settings
from host.desktop.shell import Shell

LOCAL = "http://127.0.0.1:53817/index.html"


class FakeWindow:
    def __init__(self, url=LOCAL):
        self.url, self.calls = url, []

    def get_current_url(self): return self.url
    def show(self): self.calls.append("show")
    def hide(self): self.calls.append("hide")
    def destroy(self): self.calls.append("destroy")
    def load_url(self, url): self.calls.append(("load", url)); self.url = url
    def evaluate_js(self, script): self.calls.append(("js", script))


class FakeTray:
    def __init__(self, can_notify=True):
        self.can_notify, self.notes, self.stopped = can_notify, [], False

    def notify(self, text):
        if self.can_notify:
            self.notes.append(text)
        return self.can_notify

    def stop(self): self.stopped = True


class FakeProvider:
    def __init__(self, start_error=None, exists=True):
        self.shown, self.started = [], 0
        self._start_error, self._exists = start_error, exists

    def on_window_shown(self, visible): self.shown.append(visible)
    def exists(self): return self._exists

    def start(self):
        self.started += 1
        if self._start_error:
            raise self._start_error


def _controller(tmp_path, *, provider=None, tray=None, url=LOCAL):
    window = FakeWindow(url)
    shell = Shell(window)
    controller = Controller(provider or FakeProvider(), Settings(tmp_path / "s.json"), shell)
    controller.window, controller.tray = window, tray or FakeTray()
    return controller, window


def test_closing_hides_instead_of_closing(tmp_path):
    controller, window = _controller(tmp_path)
    assert controller.on_closing() is False
    assert window.calls == ["hide"]


def test_the_first_hide_tells_the_user_where_eggie_went_once(tmp_path):
    controller, _ = _controller(tmp_path)
    controller.on_closing()
    controller.on_closing()
    assert controller.tray.notes == [TRAY_NOTICE]
    assert controller.settings.get(TRAY_NOTICE_SHOWN) is True


def test_a_platform_without_notifications_tries_again_next_time(tmp_path):
    controller, _ = _controller(tmp_path, tray=FakeTray(can_notify=False))
    controller.on_closing()
    assert controller.settings.get(TRAY_NOTICE_SHOWN) is False


def test_exit_is_not_turned_into_a_hide(tmp_path):
    controller, window = _controller(tmp_path)
    controller.exit()
    assert controller.on_closing() is True
    assert "hide" not in window.calls
    assert window.calls[-1] == "destroy"
    assert controller.tray.stopped is True


def test_show_brings_the_window_and_dock_back(tmp_path):
    controller, window = _controller(tmp_path)
    controller.on_closing()
    controller.show()
    assert window.calls[-1] == "show"
    assert controller.provider.shown == [False, True]


def test_a_route_on_the_local_ui_is_handed_to_the_page(tmp_path):
    controller, window = _controller(tmp_path)
    controller.open_route("settings")
    assert ("js", 'window.eggie.route("settings")') in window.calls


def test_a_route_from_the_console_reloads_the_local_ui(tmp_path):
    controller, window = _controller(tmp_path)
    controller.shell.local_url()          # the local page was seen first
    window.url = "http://localhost:39080/"
    controller.open_route("quit")
    assert ("load", LOCAL + "#quit") in window.calls


def test_a_failed_background_start_is_announced(tmp_path):
    provider = FakeProvider(start_error=RuntimeError("wsl.exe failed"))
    controller, _ = _controller(tmp_path, provider=provider)
    controller.start_vm_in_background().join(timeout=5)
    assert controller.tray.notes == [START_FAILED]


def test_a_failed_background_start_shows_the_window_where_it_cannot_notify(tmp_path):
    provider = FakeProvider(start_error=RuntimeError("limactl failed"))
    controller, window = _controller(tmp_path, provider=provider, tray=FakeTray(can_notify=False))
    controller.start_vm_in_background().join(timeout=5)
    assert window.calls[-1] == "show"


def test_a_login_launch_hides_and_starts_the_vm(tmp_path):
    controller, window = _controller(tmp_path)
    controller.on_login_launch()
    controller._background.join(timeout=5)
    assert window.calls[0] == "hide"
    assert controller.provider.started == 1


def test_a_login_launch_before_setup_keeps_the_window(tmp_path):
    controller, window = _controller(tmp_path, provider=FakeProvider(exists=False))
    controller.on_login_launch()
    assert window.calls == []
```

- [ ] **Step 2: Run to verify they fail** — `$PYTEST tests/host/desktop/test_controller.py` → FAIL (no module).

- [ ] **Step 3: Implement** — `host/desktop/controller.py`

```python
"""The window and the tray, as one app that lives past its window."""
from __future__ import annotations

import json
import sys
import threading

from .settings import TRAY_NOTICE_SHOWN, Settings

TRAY_NOTICE = "Eggie is still running. Find it in the system tray."
START_FAILED = "Eggie could not start. Open Eggie to see why."


class Controller:
    def __init__(self, provider, settings: Settings, shell):
        self.provider = provider
        self.settings = settings
        self.shell = shell
        self.window = None
        self.tray = None
        self._exiting = False
        self._background = None

    def on_closing(self) -> bool:
        # destroy() and macOS terminate: both arrive here too; only a real
        # exit may get through.
        if self._exiting:
            return True
        self.hide()
        if not self.settings.get(TRAY_NOTICE_SHOWN) and self.tray is not None:
            if self.tray.notify(TRAY_NOTICE):
                self.settings.set(TRAY_NOTICE_SHOWN, True)
        return False

    def hide(self) -> None:
        if self.window is not None:
            self.window.hide()
        self.provider.on_window_shown(False)

    def show(self) -> None:
        if self.window is None:
            return
        self.provider.on_window_shown(True)
        self.window.show()

    def open_route(self, route: str) -> None:
        self.show()
        if self.shell.is_local():
            self.window.evaluate_js(f"window.eggie.route({json.dumps(route)})")
            return
        # The console is a page from the VM: no window.eggie there, and the
        # bridge refuses it. Back to our own page, which reads the fragment.
        local = self.shell.local_url()
        if local:
            self.window.load_url(f"{local}#{route}")

    def exit(self) -> None:
        self._exiting = True
        if self.tray is not None:
            self.tray.stop()
        if self.window is not None:
            self.window.destroy()

    def on_login_launch(self) -> None:
        try:
            installed = self.provider.exists()
        except Exception:
            installed = False
        if not installed:
            return
        self.hide()
        self.start_vm_in_background()

    def start_vm_in_background(self) -> threading.Thread:
        def run():
            try:
                self.provider.start()
            except Exception as e:
                print(f"Eggie could not start the virtual machine: {e!r}", file=sys.stderr)
                if self.tray is None or not self.tray.notify(START_FAILED):
                    self.show()

        self._background = threading.Thread(target=run, daemon=True, name="eggie-autostart")
        self._background.start()
        return self._background
```

`host.desktop.controller` is imported by `__main__` in Task 9. Until then add to the temporary import line in `host/desktop/api.py`: `from . import controller as _controller, lifecycle as _lifecycle, settings as _settings  # noqa: F401`.

- [ ] **Step 4: Run tests** — `$PYTEST tests/host/desktop/test_controller.py` → PASS; `$PYTEST` → all pass.

- [ ] **Step 5: Commit**

```bash
git add host/desktop/controller.py host/desktop/api.py tests/host/desktop/test_controller.py
git commit -m "feat: controller that hides to the tray and exits on request"
```

---

### Task 7: DesktopApi — settings, quit, turn on once

**Files:**
- Modify: `host/desktop/api.py`
- Test: `tests/host/desktop/test_api_settings.py` (create), `tests/host/desktop/test_api_app_update.py` (append)

**Interfaces:**
- Consumes: `Settings`, `turn_on_autostart_once` (Task 1); provider `autostart_enabled`, `set_autostart`, `running`, `stop`.
- Produces on `DesktopApi`:
  - constructor kwargs `settings: Settings | None = None` (default `Settings(self._install_dir_factory().parent / "settings.json")`), `autostart_exe: str | None | object = _FROZEN` (sentinel: resolves to `sys.executable` when `getattr(sys, "frozen", False)`, else `None`). `quit_app` stays — it is the real exit (`Controller.exit` in Task 9).
  - `get_settings() -> {"autostart": bool, "autostart_available": bool}`
  - `set_autostart(on: bool) -> {"ok": bool, "error": str}`
  - `quit(force: bool) -> {"confirm": True} | {"quitting": True}`
  - attribute `_quit_thread: threading.Thread | None` (tests join it)

- [ ] **Step 1: Write the failing tests** — `tests/host/desktop/test_api_settings.py`

```python
from __future__ import annotations

import threading

from host.core.install import InstallState, Step
from host.core.status import Readiness
from host.desktop.api import DesktopApi
from host.desktop.settings import Settings

EXE = r"C:\Eggie\setup.exe"


class FakeProvider:
    def __init__(self, running=True, stop_error=None):
        self.autostart, self.stops = {}, 0
        self._running, self._stop_error = running, stop_error

    def autostart_enabled(self, exe):
        return self.autostart.get(exe, False)

    def set_autostart(self, on, exe):
        self.autostart[exe] = on

    def running(self):
        return self._running

    def stop(self):
        self.stops += 1
        if self._stop_error:
            raise self._stop_error


def _api(tmp_path, provider=None, *, exe=EXE, steps=None, quits=None):
    return DesktopApi(provider or FakeProvider(), InstallState(tmp_path / "state.json"),
                      push=lambda e: None, probe_fn=lambda p: Readiness(),
                      steps_factory=lambda: steps or [Step("preflight", lambda: None)],
                      settings=Settings(tmp_path / "settings.json"), autostart_exe=exe,
                      quit_app=(lambda: quits.append(True)) if quits is not None else lambda: None)


def test_settings_read_the_real_state(tmp_path):
    provider = FakeProvider()
    api = _api(tmp_path, provider)
    assert api.get_settings() == {"autostart": False, "autostart_available": True}
    provider.autostart[EXE] = True     # turned on outside Eggie
    assert api.get_settings()["autostart"] is True


def test_a_source_checkout_cannot_register_itself(tmp_path):
    api = _api(tmp_path, exe=None)
    assert api.get_settings() == {"autostart": False, "autostart_available": False}
    assert api.set_autostart(True)["ok"] is False


def test_set_autostart_reports_a_refusal_as_text(tmp_path):
    class Refusing(FakeProvider):
        def set_autostart(self, on, exe):
            raise RuntimeError("macOS refused to add Eggie to Login Items")
    result = _api(tmp_path, Refusing()).set_autostart(True)
    assert result == {"ok": False, "error": "macOS refused to add Eggie to Login Items"}


def test_the_first_successful_install_turns_autostart_on(tmp_path):
    provider = FakeProvider()
    api = _api(tmp_path, provider)
    api.start_install()
    api.jobs.join(timeout=5)
    assert provider.autostart == {EXE: True}


def test_a_second_successful_install_does_not_turn_autostart_back_on(tmp_path):
    provider = FakeProvider()
    api = _api(tmp_path, provider)
    api.start_install(); api.jobs.join(timeout=5)
    api.set_autostart(False)
    InstallState(tmp_path / "state.json").clear()      # reset / repair path
    api.start_install(); api.jobs.join(timeout=5)
    assert provider.autostart == {EXE: False}


def test_a_failed_install_does_not_turn_autostart_on(tmp_path):
    def boom():
        raise RuntimeError("download failed")
    provider = FakeProvider()
    api = _api(tmp_path, provider, steps=[Step("fetch_image", boom)])
    api.start_install(); api.jobs.join(timeout=5)
    assert provider.autostart == {}


def test_quit_stops_a_running_vm_then_exits(tmp_path):
    provider, quits = FakeProvider(), []
    api = _api(tmp_path, provider, quits=quits)
    assert api.quit(False) == {"quitting": True}
    api._quit_thread.join(timeout=5)
    assert provider.stops == 1 and quits == [True]


def test_quit_leaves_a_stopped_vm_alone(tmp_path):
    provider, quits = FakeProvider(running=False), []
    api = _api(tmp_path, provider, quits=quits)
    api.quit(False); api._quit_thread.join(timeout=5)
    assert provider.stops == 0 and quits == [True]


def test_quit_exits_even_when_the_vm_will_not_stop(tmp_path):
    provider, quits = FakeProvider(stop_error=RuntimeError("wsl hung")), []
    api = _api(tmp_path, provider, quits=quits)
    api.quit(False); api._quit_thread.join(timeout=5)
    assert quits == [True]


def test_quit_during_a_job_asks_first(tmp_path):
    release = threading.Event()
    provider, quits = FakeProvider(), []
    api = _api(tmp_path, provider, quits=quits,
               steps=[Step("create_vm", lambda: release.wait(5))])
    api.start_install()
    try:
        assert api.quit(False) == {"confirm": True}
        assert provider.stops == 0 and quits == []
    finally:
        release.set()
        api.jobs.join(timeout=5)


def test_quit_anyway_runs_while_a_job_is_still_running(tmp_path):
    release = threading.Event()
    provider, quits = FakeProvider(), []
    api = _api(tmp_path, provider, quits=quits,
               steps=[Step("create_vm", lambda: release.wait(5))])
    api.start_install()
    try:
        assert api.quit(True) == {"quitting": True}
        api._quit_thread.join(timeout=5)
        assert quits == [True]
    finally:
        release.set()
        api.jobs.join(timeout=5)
```

Append to `tests/host/desktop/test_api_app_update.py`:
```python
def test_the_update_quit_leaves_the_vm_running(tmp_path, monkeypatch):
    # The installer relaunches the app at once; a stop here only adds a
    # stop and a boot to every update.
    from host.core import download

    class Provider(FakeProvider):
        stops = 0
        def stop(self):
            Provider.stops += 1

    monkeypatch.setattr(download, "fetch", lambda *a, **k: tmp_path / "setup.exe")
    quits = []
    api = _api(tmp_path, AppRelease("0.2.0", "https://dl.invalid/x.exe", "0" * 64),
               quits=quits, provider=Provider())
    api.check_app_update()
    api.start_app_update()
    api.jobs.join(timeout=5)
    assert quits == [True]
    assert Provider.stops == 0
```
(If `download.fetch` is already stubbed differently in this file, reuse that file's existing approach.)

- [ ] **Step 2: Run to verify they fail** — `$PYTEST tests/host/desktop/test_api_settings.py` → FAIL (unexpected kwarg `settings`).

- [ ] **Step 3: Implement** in `host/desktop/api.py`

Replace the temporary import line from Tasks 1/6 with:
```python
from . import controller as _controller  # noqa: F401  (removed in Task 9)
from .lifecycle import turn_on_autostart_once
from .settings import Settings
```
and add `import sys`, `import threading` at the top. Add a module sentinel:
```python
_FROZEN = object()
```

Constructor: add `settings=None, autostart_exe=_FROZEN` and:
```python
        self._settings = settings or Settings(self._install_dir_factory().parent / "settings.json")
        if autostart_exe is _FROZEN:
            # A source checkout has no stable executable to register at login.
            autostart_exe = sys.executable if getattr(sys, "frozen", False) else None
        self._autostart_exe = autostart_exe
        self._quitting = False
        self._quit_thread = None
```
(`self._install_dir_factory` must be assigned before this line.)

In `start_install.work`, after `run_install(...)` succeeds and before `return terminal_event(None)`:
```python
            turn_on_autostart_once(
                self._settings, available=self._autostart_exe is not None,
                enable=lambda: self._provider.set_autostart(True, self._autostart_exe))
```
(Note `run_install` may also end in a reboot without raising? Check `run_install`: if it raises `RebootRequired` the `except` branch returns first — correct, setup is not finished.)

New methods (place under a `# --- settings and quit ---` section):
```python
    def get_settings(self) -> dict:
        available = self._autostart_exe is not None
        return {"autostart": available and self._provider.autostart_enabled(self._autostart_exe),
                "autostart_available": available}

    def set_autostart(self, on: bool) -> dict:
        if self._autostart_exe is None:
            return {"ok": False, "error": "Only an installed Eggie can open when you sign in."}
        try:
            self._provider.set_autostart(bool(on), self._autostart_exe)
        except Exception as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True, "error": ""}

    def quit(self, force: bool) -> dict:
        if self.jobs.running() and not force and not self._quitting:
            return {"confirm": True}
        if not self._quitting:
            self._quitting = True
            # Not a job: JobRegistry runs one at a time, and Quit anyway must
            # work while an install still holds it.
            self._quit_thread = threading.Thread(target=self._stop_then_exit,
                                                 daemon=True, name="eggie-quit")
            self._quit_thread.start()
        return {"quitting": True}

    def _stop_then_exit(self) -> None:
        try:
            if self._provider.running():
                self._provider.stop()
        except Exception as e:
            # A VM that will not stop must not leave an app that cannot close.
            print(f"Eggie could not stop the virtual machine: {e!r}", file=sys.stderr)
        finally:
            self._quit_app()
```

- [ ] **Step 4: Run tests** — `$PYTEST tests/host/desktop/` → PASS; `$PYTEST` → all pass. `tests/host/desktop/test_ui_assets.py::test_every_bridge_call_in_the_ui_names_a_real_method` is unaffected (no UI calls yet).

- [ ] **Step 5: Commit**

```bash
git add host/desktop/api.py tests/host/desktop/test_api_settings.py tests/host/desktop/test_api_app_update.py
git commit -m "feat: settings, quit and turn-on-once in the desktop bridge"
```

---

### Task 8: UI — Settings, Quit confirm, Quitting

**Files:**
- Modify: `host/desktop/ui/index.html`, `host/desktop/ui/app.js`, `host/desktop/ui/app.css`
- Test: `tests/host/desktop/test_ui_assets.py` (append)

**Interfaces:**
- Consumes: `get_settings`, `set_autostart`, `quit` (Task 7); `Controller.open_route` calls `window.eggie.route(name)` or loads `#name` (Task 6).
- Produces: templates `settings`, `quit-confirm`, `quitting`; actions `settings`, `toggle-autostart`, `quit-anyway`, `quit-cancel`; `window.eggie.route(name)` for `"settings"` and `"quit"`; a `Settings` tile on every home screen (`home:running`, `home:stopped`, `home:not_installed`, `home:wrong`).

- [ ] **Step 1: Write the failing tests** (append to `tests/host/desktop/test_ui_assets.py`)

```python
def test_the_tray_screens_have_templates():
    markup = (UI / "index.html").read_text()
    for screen in ("settings", "quit-confirm", "quitting"):
        assert f'data-screen="{screen}"' in markup, f"no template for {screen}"


def test_every_home_state_can_open_settings():
    markup = (UI / "index.html").read_text()
    for screen in ("home:not_installed", "home:stopped", "home:running", "home:wrong"):
        start = markup.index(f'data-screen="{screen}"')
        end = markup.index("</template>", start)
        assert 'data-action="settings"' in markup[start:end], screen


def test_the_settings_copy_is_exact():
    markup = (UI / "index.html").read_text()
    assert "Open Eggie when I sign in" in markup
    assert "Stopping Eggie…" in markup


def test_the_page_answers_the_routes_the_tray_sends():
    # controller.open_route() sends exactly these names, by call or by #fragment.
    script = (UI / "app.js").read_text()
    assert "eggie.route" in script or "route(" in script
    for route in ("settings", "quit"):
        assert f"'{route}'" in script, f"app.js does not handle the {route} route"
    assert "location.hash" in script
```

- [ ] **Step 2: Run to verify they fail** — `$PYTEST tests/host/desktop/test_ui_assets.py` → FAIL.

- [ ] **Step 3: Implement**

`index.html` — in each of the four home templates, inside `<div class="tiles">`, add before the Uninstall/`tile-note` entries:
```html
        <button data-action="settings">Settings</button>
```
(Read each template first: `home:not_installed` and `home:wrong` have their own tile lists; keep their existing disabled/omitted tiles as they are.)

Add three templates before `<template data-screen="install:running">`:
```html
<template data-screen="settings">
  <section class="pad">
    <h2 class="title">Settings</h2>
    <label class="setting">
      <input type="checkbox" data-setting="autostart" data-action="toggle-autostart">
      <span>Open Eggie when I sign in</span>
    </label>
    <p class="hint" data-when="unavailable">Only an installed Eggie can open when you sign in.</p>
    <p class="hint" data-field="error"></p>
    <div class="row"><button class="btn-secondary" data-action="go-home">Back</button></div>
  </section>
</template>

<template data-screen="quit-confirm">
  <section class="pad centered">
    <h2 class="title">Eggie is still working</h2>
    <p class="lede">Quitting now stops what it is doing and turns the kitchen off.</p>
    <div class="row">
      <button class="btn-primary" data-action="quit-anyway">Quit anyway</button>
      <button class="btn-secondary" data-action="quit-cancel">Cancel</button>
    </div>
  </section>
</template>

<template data-screen="quitting">
  <section class="pad centered">
    <h2 class="title">Stopping Eggie…</h2>
    <p class="lede">Turning the kitchen off. The window closes by itself.</p>
  </section>
</template>
```

`app.css` — append:
```css
.setting {
  display:flex; align-items:center; gap:12px; margin:18px 0 6px;
  font:500 15px 'Hanken Grotesk', sans-serif; color:var(--ink); cursor:pointer;
}
.setting input { width:18px; height:18px; accent-color:var(--yolk); }
```

`app.js` — add after the `ACTIONS['start-over']` line:
```js
// --- Settings and quit (also reached from the tray) -------------------

ACTIONS['settings'] = async () => {
  const settings = await api().get_settings();
  show('settings', { unavailable: settings.autostart_available ? '' : 'yes', error: '' });
  const box = document.querySelector('[data-setting="autostart"]');
  box.checked = settings.autostart;
  box.disabled = !settings.autostart_available;
};

ACTIONS['toggle-autostart'] = async (node) => {
  const result = await api().set_autostart(node.checked);
  // Re-read rather than trust the click: the OS has the final say.
  const settings = await api().get_settings();
  node.checked = settings.autostart;
  fill(document.getElementById('screen'), { error: result.ok ? '' : result.error });
};

async function startQuit(force) {
  const result = await api().quit(force);
  if (result.confirm) return show('quit-confirm', {});
  show('quitting', {});
}

ACTIONS['quit-anyway'] = () => startQuit(true);
ACTIONS['quit-cancel'] = () => refresh();

const ROUTES = { settings: () => ACTIONS.settings(), quit: () => startQuit(false) };

window.eggie.route = (name) => {
  const go = ROUTES[name];
  if (go) go();
};
```

The checkbox fires `click` through `wire()`, which passes the checkbox as `node`. An unavailable checkbox is `disabled`, so it never fires.

Initial route: find where the page first calls `refresh()` on `pywebviewready` (bottom of `app.js`). Replace that first call so a `#route` fragment wins:
```js
function start() {
  const route = window.location.hash.slice(1);
  // The tray reloads this page with #settings or #quit when the console was
  // showing; cleared so a later reload lands on Home.
  if (route && ROUTES[route]) {
    history.replaceState(null, '', window.location.pathname);
    return ROUTES[route]();
  }
  return refresh();
}
```
and call `start()` where the initial `refresh()` was called (read the bottom of `app.js` for the exact listener; keep its structure).

- [ ] **Step 4: Run tests** — `$PYTEST tests/host/desktop/test_ui_assets.py` → PASS (including the existing `test_every_bridge_call_in_the_ui_names_a_real_method`, which now sees `get_settings`, `set_autostart`, `quit`). `$PYTEST` → all pass.

- [ ] **Step 5: Commit**

```bash
git add host/desktop/ui/index.html host/desktop/ui/app.js host/desktop/ui/app.css tests/host/desktop/test_ui_assets.py
git commit -m "feat: settings, quit-confirm and quitting screens"
```

---

### Task 9: Wiring — window, tray, flags, CLI

**Files:**
- Modify: `host/desktop/__main__.py`, `host/desktop/api.py` (drop the temporary controller import), `host/cli.py`, `packaging/macos/setup_main.py` (comment only if needed)
- Create: `host/desktop/resources/icon.ico` (copy of `/home/ihor/projects/local-environment-for-non-tech/poc/icon.ico`)
- Test: `tests/host/desktop/test_entry.py` (update + append), `tests/test_setup_cli.py` (update + append)

**Interfaces:**
- Consumes: everything above.
- Produces:
  - `resource_dir() -> Path` in `__main__` (same `_MEIPASS` rule as `ui_dir()`), `icon_path() -> Path`
  - `run(provider, state, *, create=..., start=..., resumed=False, background=False, steps_factory=None, app_update_fn=None, settings=None) -> int`
  - `main()` parses `--background`
  - `eggie setup --background`
  - `eggie uninstall --purge` calls `provider.set_autostart(False, sys.executable)` first, ignoring errors

- [ ] **Step 1: Copy the icon**

```bash
mkdir -p host/desktop/resources
cp /home/ihor/projects/local-environment-for-non-tech/poc/icon.ico host/desktop/resources/icon.ico
```

- [ ] **Step 2: Write the failing tests**

In `tests/host/desktop/test_entry.py` replace `FakeProvider` and `FakeWindow` with:
```python
class FakeTray:
    def __init__(self):
        self.started = self.stopped = False
    def start(self): self.started = True
    def stop(self): self.stopped = True
    def notify(self, text): return True


class FakeProvider:
    def __init__(self, exists=True, primary=True):
        self._exists, self._primary = exists, primary
        self.tray_made, self.started, self.login_watch = None, 0, None
        self.announce = None

    def exists(self): return self._exists
    def start(self): self.started += 1
    def single_instance(self, on_show, *, announce):
        self.announce = announce
        return self._primary
    def watch_login_launch(self, on_login): self.login_watch = on_login
    def on_window_shown(self, visible): pass
    def tray(self, **kwargs):
        self.tray_made = (kwargs, FakeTray())
        return self.tray_made[1]


class FakeEvent:
    def __init__(self): self.handlers = []
    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self


class FakeEvents:
    def __init__(self): self.closing = FakeEvent()


class FakeWindow:
    def __init__(self, url=LOCAL):
        self.url = url
        self.loaded, self.evaluated, self.exposed = [], [], {}
        self.events = FakeEvents()
        self.calls = []

    def get_current_url(self): return self.url
    def load_url(self, url): self.loaded.append(url); self.url = url
    def evaluate_js(self, script): self.evaluated.append(script)
    def expose(self, *functions): self.exposed.update({f.__name__: f for f in functions})
    def show(self): self.calls.append("show")
    def hide(self): self.calls.append("hide")
    def destroy(self): self.calls.append("destroy")
```
Every existing `run(...)` call gains `settings=Settings(tmp_path / "settings.json")` (import `from host.desktop.settings import Settings`).

Append:
```python
def _run(tmp_path, provider, **kwargs):
    captured = {}
    window = FakeWindow()

    def create(**kw):
        captured.update(kw)
        return window

    code = run(provider, InstallState(tmp_path / "s.json"), create=create,
               start=lambda **kw: None, app_update_fn=_no_update_check,
               settings=Settings(tmp_path / "settings.json"), **kwargs)
    return code, captured, window


def test_a_login_launch_with_a_vm_stays_in_the_tray(tmp_path):
    provider = FakeProvider(exists=True)
    code, captured, _ = _run(tmp_path, provider, background=True)
    assert code == 0
    assert captured["hidden"] is True
    assert provider.tray_made[1].started is True


def test_a_login_launch_before_setup_opens_the_window(tmp_path):
    _, captured, _ = _run(tmp_path, FakeProvider(exists=False), background=True)
    assert captured["hidden"] is False


def test_closing_the_window_is_wired_to_hide(tmp_path):
    _, _, window = _run(tmp_path, FakeProvider())
    assert [h() for h in window.events.closing.handlers] == [False]
    assert window.calls == ["hide"]


def test_a_second_instance_exits_without_a_window(tmp_path):
    provider = FakeProvider(primary=False)
    code, captured, _ = _run(tmp_path, provider)
    assert code == 0 and captured == {}
    assert provider.announce is True


def test_a_second_background_instance_does_not_ask_for_the_window(tmp_path):
    provider = FakeProvider(primary=False)
    _run(tmp_path, provider, background=True)
    assert provider.announce is False


def test_the_tray_menu_carries_open_settings_and_quit(tmp_path):
    provider = FakeProvider()
    _, _, window = _run(tmp_path, provider)
    kwargs, _tray = provider.tray_made
    assert set(kwargs) == {"icon", "on_open", "on_settings", "on_quit"}
    assert kwargs["icon"].is_file()
    kwargs["on_settings"]()
    assert window.evaluated[-1] == 'window.eggie.route("settings")'
    kwargs["on_quit"]()
    assert window.evaluated[-1] == 'window.eggie.route("quit")'


def test_the_tray_stops_when_the_loop_ends(tmp_path):
    provider = FakeProvider()
    _run(tmp_path, provider)
    assert provider.tray_made[1].stopped is True
```

In `tests/test_setup_cli.py`:
- `StubProvider` gains `def set_autostart(self, on, exe): self.autostart_off = (on is False)` and `autostart_off = False` class attribute.
- Both `fake_run` signatures gain `background=False` and record it: `captured["background"] = background`.
Append:
```python
def test_setup_background_reaches_the_window(monkeypatch, tmp_path):
    captured = {}

    def fake_run(provider, state, *, steps_factory=None, resumed=False, background=False):
        captured["background"] = background
        return 0

    monkeypatch.setattr(cli, "_provider_factory", lambda: StubProvider())
    monkeypatch.setattr("host.desktop.__main__.run", fake_run)
    result = runner.invoke(cli.app, ["setup", "--background"])
    assert result.exit_code == 0
    assert captured["background"] is True


def test_uninstall_turns_open_at_login_off(monkeypatch):
    provider = StubProvider()
    monkeypatch.setattr(cli, "_provider_factory", lambda: provider)
    result = runner.invoke(cli.app, ["uninstall", "--purge"])
    assert result.exit_code == 0
    assert provider.autostart_off is True


def test_uninstall_goes_on_when_autostart_cannot_be_removed(monkeypatch):
    class Stuck(StubProvider):
        def set_autostart(self, on, exe):
            raise OSError("registry locked")
    monkeypatch.setattr(cli, "_provider_factory", lambda: Stuck())
    assert runner.invoke(cli.app, ["uninstall", "--purge"]).exit_code == 0
```
(Read the existing `setup` tests first: if they monkeypatch differently than `_provider_factory`, follow the file's pattern.)

- [ ] **Step 3: Run to verify they fail** — `$PYTEST tests/host/desktop/test_entry.py tests/test_setup_cli.py` → FAIL.

- [ ] **Step 4: Implement**

`host/desktop/__main__.py` — add below `ui_dir()`:
```python
def resource_dir() -> Path:
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        return Path(bundle) / "host" / "desktop" / "resources"
    return Path(__file__).resolve().parent / "resources"


def icon_path() -> Path:
    return resource_dir() / "icon.ico"
```

Rewrite `run()`:
```python
def run(provider, state, *, create=_default_create, start=_default_start,
        resumed: bool = False, background: bool = False, steps_factory=None,
        app_update_fn=None, settings=None) -> int:
    from .api import DesktopApi
    from .controller import Controller
    from .lifecycle import TRAY_ONLY, launch_mode
    from .settings import Settings
    from .shell import Shell, guarded

    if settings is None:
        from host.providers import default_install_dir
        settings = Settings(default_install_dir().parent / "settings.json")

    shell = Shell()
    controller = Controller(provider, settings, shell)
    if not provider.single_instance(controller.show, announce=not background):
        return 0

    mode = launch_mode(resume=resumed, background=background, vm_exists=provider.exists)
    api = DesktopApi(provider, state, push=shell.push, steps_factory=steps_factory,
                     local_url=shell.local_url, app_update_fn=app_update_fn,
                     quit_app=controller.exit, settings=settings)
    api.resumed = resumed
    api.start_app_update_check()

    try:
        window = create(title=WINDOW_TITLE, url=str(ui_dir() / "index.html"),
                        js_api=None, width=WINDOW_SIZE[0],
                        height=WINDOW_SIZE[1], min_size=MIN_SIZE,
                        hidden=mode == TRAY_ONLY)
    except Exception as e:
        # (keep the existing comment block verbatim)
        print(WEBVIEW_MISSING, file=sys.stderr)
        print(f"(technical detail: {e!r})", file=sys.stderr)
        return 3

    shell.window = window
    controller.window = window
    window.expose(*guarded(api, shell))
    window.events.closing += controller.on_closing

    tray = provider.tray(icon=icon_path(), on_open=controller.show,
                         on_settings=lambda: controller.open_route("settings"),
                         on_quit=lambda: controller.open_route("quit"))
    controller.tray = tray
    tray.start()
    if mode == TRAY_ONLY:
        provider.on_window_shown(False)
        controller.start_vm_in_background()
    elif not resumed:
        provider.watch_login_launch(controller.on_login_launch)

    try:
        start(debug=False)
    finally:
        tray.stop()
    return 0
```

Keep the existing `# Surfaced by a later task...` comment above `api.resumed`. Note `single_instance` is called before `api` exists, so a second instance never starts the update check or touches `install-state.json`.

`main()`: add `parser.add_argument("--background", action="store_true")` and pass `background=args.background` to `run`.

`host/desktop/api.py`: delete the line `from . import controller as _controller  # noqa: F401  (removed in Task 9)`.

`host/cli.py` `setup`: add parameter `background: bool = typer.Option(False, "--background")`, pass `background=background` into `run(...)` in the non-headless branch. `uninstall`: right after the `--purge` guard and imports:
```python
    import sys as _sys
    provider = _provider()
    try:
        provider.set_autostart(False, _sys.executable)
    except Exception:
        # Never block removing the VM on a login entry that is already gone.
        pass
```
and use that same `provider` for `.destroy()`.

- [ ] **Step 5: Run tests** — `$PYTEST tests/host/desktop/test_entry.py tests/test_setup_cli.py` → PASS; `$PYTEST` → all pass (including `test_no_dead_modules`, now that `__main__` imports controller/lifecycle/settings).

- [ ] **Step 6: Commit**

```bash
git add host/desktop/__main__.py host/desktop/api.py host/desktop/resources/icon.ico host/cli.py tests/host/desktop/test_entry.py tests/test_setup_cli.py
git commit -m "feat: run the desktop app from the tray, with --background for login"
```

---

### Task 10: Packaging and docs

**Files:**
- Modify: `packaging/windows/eggie.spec`, `packaging/windows/installer.iss`, `packaging/macos/eggie.spec`, `docs/release-testing.md`, `host/desktop/CLAUDE.md`, `host/CLAUDE.md`
- Test: `tests/host/test_host_dependencies.py` (append)

**Interfaces:**
- Consumes: `host/desktop/resources/icon.ico` (Task 9), new modules (Tasks 1–6).

- [ ] **Step 1: Write the failing test** (append to `tests/host/test_host_dependencies.py`)

```python
def test_packaging_bundles_the_tray_icon_and_uses_it_as_the_app_icon():
    # The tray loads icon.ico at runtime; a spec without it starts a tray
    # with no image and pystray raises on the first draw.
    for spec_path in ("packaging/windows/eggie.spec", "packaging/macos/eggie.spec"):
        spec = (REPO_ROOT / spec_path).read_text()
        assert '"../../host/desktop/resources"' in spec, f"{spec_path} does not bundle resources"
        assert "icon=" in spec, f"{spec_path} does not set the app icon"
    iss = (REPO_ROOT / "packaging/windows/installer.iss").read_text()
    assert "SetupIconFile=" in iss
```

- [ ] **Step 2: Run to verify it fails** — `$PYTEST tests/host/test_host_dependencies.py` → FAIL.

- [ ] **Step 3: Implement**

`packaging/windows/eggie.spec`:
- `datas` gains `("../../host/desktop/resources", "host/desktop/resources"),`
- `HIDDEN` gains `"host.desktop.controller", "host.desktop.lifecycle", "host.desktop.settings", "host.providers.tray_win", "host.providers.instance", "pystray._win32", "PIL.Image"`
- both `EXE(...)` calls gain `icon="../../host/desktop/resources/icon.ico"`

`packaging/macos/eggie.spec`:
- `DATAS` gains the same resources tuple
- `HIDDEN` gains `"host.desktop.controller", "host.desktop.lifecycle", "host.desktop.settings", "host.providers.tray_mac", "host.providers.mac_login", "ServiceManagement"`
- `BUNDLE(...)` gains `icon="../../host/desktop/resources/icon.ico"` (PyInstaller converts to `.icns` via Pillow, from the `dev` extra)

`packaging/windows/installer.iss` `[Setup]`: add
```
SetupIconFile=..\..\host\desktop\resources\icon.ico
UninstallDisplayIcon={app}\setup.exe
```

`docs/release-testing.md` — add a section before "## Both platforms, once set up":
```markdown
## Tray and open at login

| # | Action | Pass when | Result |
|---|---|---|---|
| T1 | Close the window with the title-bar button | The window hides; the tray / menu-bar icon stays; projects still answer. Windows: the first time only, a notification says Eggie is still in the tray | |
| T2 | Tray icon: left-click (Windows) / menu **Open Eggie** | The window comes back where it was | |
| T3 | Tray menu **Settings** while the projects console is showing | The window shows Eggie's Settings screen, not the console | |
| T4 | Finish a first setup, then open Settings | **Open Eggie when I sign in** is ticked. Windows: `reg query HKCU\Software\Microsoft\Windows\CurrentVersion\Run /v Eggie` shows `"<install dir>\setup.exe" setup --background`. macOS: Eggie is listed in System Settings → General → Login Items | |
| T5 | Sign out and back in (and once: reboot) | Only the tray icon appears, no window; within a minute the VM is running and projects answer | |
| T6 | Untick the checkbox; turn it back on in Task Manager / System Settings | Reopening Settings shows the OS state each time | |
| T7 | Untick, then run **Repair** and an app update | It stays unticked | |
| T8 | Open Eggie again from the Start menu / Finder while it runs | The existing window comes forward; still one tray icon | |
| T9 | Tray **Quit Eggie** with the VM running | "Stopping Eggie…" shows, then the app exits; `wsl -l --running` / `limactl list` shows the VM stopped. Record whether `systemctl poweroff` alone ended the WSL distro | |
| T10 | **Quit Eggie** during an import | "Eggie is still working" asks first; Cancel returns; Quit anyway exits | |
| T11 | macOS: red button, then click the Dock icon; then Cmd+Q | The Dock icon disappears while hidden and the window comes back on reopen; Cmd+Q stops the VM and quits | |
| T12 | **Update now** while the VM runs | The app restarts on the new version; the VM was never stopped | |
| T13 | **Destructive.** Uninstall while the app runs | The app closes; the Run value / Login Item is gone | |
```

`host/desktop/CLAUDE.md` — under "Module roles" add:
```markdown
- `controller.py` — the window and tray as one app: close hides, `exit()` really closes, tray
  routes reach the page via `window.eggie.route()` or a `#route` reload when the console shows.
- `lifecycle.py` / `settings.py` — pure launch-mode decision and the `settings.json` flags that
  must outlive the VM (`install-state.json` is deleted on reset).
```
and under "Things that will bite you":
```markdown
- **`events.closing` fires for every close** — the title-bar button, `window.destroy()` and macOS
  Cmd+Q (via `applicationShouldTerminate_`). `Controller` lets one through only after `exit()`;
  the mac tray re-points the Cmd+Q menu item at Quit Eggie. A new close path must go through
  `Controller.exit()` or it becomes a hide.
- **Quit is not a job.** `JobRegistry` runs one job; Quit anyway must work while an install holds it.
```

`host/CLAUDE.md` — add a line under the providers section (read the file first for placement):
```markdown
- Desktop surface (`autostart_*`, `single_instance`, `watch_login_launch`, `on_window_shown`, `tray`)
  is pinned by `tests/host/test_provider_surface.py::DESKTOP_SURFACE`. macOS-only and
  Windows-only imports stay function-local so the suite imports every provider on Linux.
```

- [ ] **Step 4: Run tests** — `$PYTEST` → all pass.

- [ ] **Step 5: Commit**

```bash
git add packaging docs/release-testing.md host/desktop/CLAUDE.md host/CLAUDE.md tests/host/test_host_dependencies.py
git commit -m "build: ship the icon and tray modules; manual gates for the tray app"
```
