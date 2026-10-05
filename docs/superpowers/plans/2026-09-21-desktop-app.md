# Eggie Desktop App Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the tkinter `host/setup_app/` with a pywebview desktop app that renders the Eggie Desktop design board on Windows and macOS.

**Architecture:** A native window hosting local HTML/CSS/JS over the system webview (WebView2 / WKWebView). `host/desktop/api.py` is the only object JavaScript can reach; slow work runs on worker threads registered in `host/desktop/jobs.py` and pushes events back with `evaluate_js`. All branching logic lives in `host/desktop/view.py`, which is pure and therefore the only part with tests. `host/core/**` is called as-is.

**Tech Stack:** Python 3.12+, pywebview (+ `pythonnet` on Windows, `pyobjc` on macOS), stdlib `threading`/`webbrowser`, vanilla JS, PyInstaller.

**Spec:** `docs/superpowers/specs/2026-09-21-desktop-app-design.md`

## Global Constraints

- **`host/` never imports `agent/`.** Enforced by `tests/host/test_no_agent_import.py`.
- **No `sys.platform`, `platform.system()` or `os.name` outside `host/providers/`.** Enforced by `tests/test_no_platform_leak.py`, which globs `host/**/*.py` and will cover `host/desktop/` automatically. Resolving frozen asset paths uses `getattr(sys, "_MEIPASS", None)` — an attribute check, not a platform check, and permitted.
- **The host declares no YAML parser and no web framework.** `tests/host/test_host_dependencies.py` forbids `fastapi`, `uvicorn`, `pyyaml`.
- **Python 3.12+, `from __future__ import annotations` at the top of every module, frozen dataclasses for value types.**
- **No copy in Python.** All user-facing words live in `host/desktop/ui/index.html`, taken verbatim from the design board. `view.py` returns state identifiers and facts only.
- **No remote URLs in `host/desktop/ui/`.** Fonts are bundled woff2. Enforced by a test added in Task 5.
- **Agent/edge ports** are `constants.AGENT_PORT = 39099` and `constants.EDGE_PORT = 39080`. Never hard-code either.
- **Run tests with a writable TMPDIR in this sandbox:** `TMPDIR=$(mktemp -d) python3 -m pytest -q`. `/tmp/pytest-of-$USER` is root-owned here and breaks `tmp_path`.
- **Commit after every task.** Branch is `feature/desktop-app`, cut from `main`.

---

## File Structure

| File | Responsibility |
|---|---|
| `host/desktop/view.py` | Pure mapping: `Readiness` → route, step list → row model, terminal exception → event, folder → summary. All branching, all tests. |
| `host/desktop/jobs.py` | One-at-a-time worker-thread registry; coalesces progress events. |
| `host/desktop/api.py` | `DesktopApi` — the fixed surface JS may call. Thin: delegates to `host/core` and `view.py`. |
| `host/desktop/__main__.py` | Entrypoint: provider, window, `webview.start()`, WebView2-missing fallback. |
| `host/desktop/ui/index.html` | Every screen as a `<template>`; all copy; CSP. |
| `host/desktop/ui/app.css` | The board's tokens verbatim + focus states + reduced-motion. |
| `host/desktop/ui/app.js` | Router and renderers. No framework. |
| `host/desktop/ui/fonts/` | Bricolage Grotesque, Hanken Grotesk, IBM Plex Mono woff2 + OFL licenses. |

Modified: `host/client.py` (upload progress), `host/core/provider.py` + `host/providers/wsl2.py` + `host/providers/lima.py` (`reboot()`), `host/cli.py` (entrypoint swap, `up` guard), `pyproject.toml`, both packaging specs, `CLAUDE.md`.

Deleted: `host/setup_app/**`, `tests/host/test_setup_app_logic.py`.

---

# Milestone 1 — Shell, bridge, Home

Deliverable: the app opens, probes the machine, and shows the correct one of the four Home states or the unreachable screen. No install yet.

---

### Task 1: Readiness → route

**Files:**
- Create: `host/desktop/__init__.py` (empty), `host/desktop/view.py`
- Test: `tests/host/desktop/__init__.py` (empty), `tests/host/desktop/test_view_route.py`

**Interfaces:**
- Consumes: `host.core.status.Readiness`, `host.core.constants.SUPPORTED_API`
- Produces: `route_for(readiness: Readiness) -> tuple[str, str]` returning `(route, state)` where route is `"home"` or `"unreachable"` and state is one of `"not_installed"`, `"stopped"`, `"wrong"`, `"running"`, or `""` for `unreachable`.

- [ ] **Step 1: Write the failing test**

```python
# tests/host/desktop/test_view_route.py
"""Which screen a probe result opens.

`probe()` returns early at every stage, so each Readiness shape below is
exactly one `return` in host/core/status.py. The rows are mutually exclusive;
getting one wrong shows the user a screen whose words are false.
"""
from __future__ import annotations

from host.core.status import Readiness
from host.desktop.view import route_for


def test_provider_that_could_not_be_asked_is_not_not_installed():
    # `provider.exists()` threw -- no limactl on PATH. vm_exists is False, the
    # same shape as "no VM yet", but telling this user to press "Set up the
    # kitchen" sends them at a setup that cannot run.
    assert route_for(Readiness(problem="limactl not found")) == ("home", "wrong")


def test_no_vm_is_not_installed():
    assert route_for(Readiness()) == ("home", "not_installed")


def test_vm_present_but_exec_threw_is_wrong_not_stopped():
    assert route_for(Readiness(vm_exists=True, problem="wsl.exe: access denied")) \
        == ("home", "wrong")


def test_vm_present_and_quiet_is_stopped():
    assert route_for(Readiness(vm_exists=True)) == ("home", "stopped")


def test_reachable_without_engine_is_wrong():
    assert route_for(Readiness(vm_exists=True, vm_reachable=True)) == ("home", "wrong")


def test_agent_refused_is_the_only_unreachable():
    # The one state where the board's copy is literally true: the VM answers
    # `true`, the engine marker is there, and only /health is silent.
    readiness = Readiness(vm_exists=True, vm_reachable=True,
                          engine_version="engine-v0.1.0",
                          problem="connection refused")
    assert route_for(readiness) == ("unreachable", "")


def test_unsupported_api_is_wrong_not_unreachable():
    readiness = Readiness(vm_exists=True, vm_reachable=True,
                          engine_version="engine-v9.0.0", agent_api=99)
    assert route_for(readiness) == ("home", "wrong")


def test_ready_is_running():
    readiness = Readiness(vm_exists=True, vm_reachable=True,
                          engine_version="engine-v0.1.0", agent_api=1)
    assert route_for(readiness) == ("home", "running")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `TMPDIR=$(mktemp -d) python3 -m pytest tests/host/desktop/test_view_route.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'host.desktop'`

- [ ] **Step 3: Write minimal implementation**

```python
# host/desktop/view.py
"""Pure mappings from host/core values to what a screen needs.

Everything here is a function of its arguments. No provider, no client, no
clock, no I/O -- which is what makes this the only module in host/desktop
worth testing, and why api.py stays thin enough to read in one sitting.

No user-facing copy lives here. These functions return state identifiers;
ui/index.html holds the words, so the design board stays the single source
for them.
"""
from __future__ import annotations

from host.core import constants
from host.core.status import Readiness


def route_for(readiness: Readiness) -> tuple[str, str]:
    """`(route, state)` for a probe result.

    Ordered to match `status.probe()`'s own early returns, with one
    deliberate exception: `problem` is checked before `vm_exists`. A provider
    that threw in `exists()` reports vm_exists=False, which is shape-identical
    to "no VM yet" -- and routing that to "not installed" would offer a Set up
    button on a machine where setup cannot run.
    """
    if readiness.problem:
        # The agent refusing /health is the only failure the unreachable
        # screen describes truthfully: its copy claims we can see the machine
        # humming, which is only established once `true` ran in it and the
        # engine marker was read.
        if readiness.vm_reachable and readiness.engine_version:
            return ("unreachable", "")
        return ("home", "wrong")
    if not readiness.vm_exists:
        return ("home", "not_installed")
    if not readiness.vm_reachable:
        return ("home", "stopped")
    if not readiness.engine_version:
        return ("home", "wrong")
    if readiness.agent_api not in constants.SUPPORTED_API:
        return ("home", "wrong")
    return ("home", "running")
```

Also create empty `host/desktop/__init__.py` and `tests/host/desktop/__init__.py`.

- [ ] **Step 4: Run test to verify it passes**

Run: `TMPDIR=$(mktemp -d) python3 -m pytest tests/host/desktop/test_view_route.py -q`
Expected: PASS — 8 passed

- [ ] **Step 5: Commit**

```bash
git add host/desktop/__init__.py host/desktop/view.py tests/host/desktop/
git commit -m "Add desktop route mapping from probe readiness"
```

---

### Task 2: Job registry

**Files:**
- Create: `host/desktop/jobs.py`
- Test: `tests/host/desktop/test_jobs.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `class JobBusy(RuntimeError)`
  - `class JobRegistry` with `__init__(self, push, *, clock=time.monotonic, min_interval=0.1)`, `start(self, kind: str, work) -> str`, `join(self, timeout=None) -> None`.
  - `work` is called as `work(emit)` where `emit(event: dict) -> None`. The registry adds `job` and `kind` keys to every event before pushing. Events with `{"type": "progress"}` are coalesced to at most one per `min_interval`; every other type passes through immediately.
  - `work` returning a dict pushes it as the terminal event; `work` raising pushes `{"type": "crashed", "message": str(exc)}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/host/desktop/test_jobs.py
"""The worker-thread registry behind every slow button.

Two things here can lose data rather than merely misbehave: a progress
coalescer that drops the *last* event (leaving a bar stuck at 96% forever),
and a second job starting while the first is mid-write of install-state.json.
"""
from __future__ import annotations

import pytest

from host.desktop.jobs import JobBusy, JobRegistry


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def test_progress_events_are_coalesced_but_terminal_events_are_not():
    clock, pushed = FakeClock(), []
    jobs = JobRegistry(pushed.append, clock=clock, min_interval=1.0)

    def work(emit):
        for percent in (10, 20, 30):
            emit({"type": "progress", "percent": percent})
        return {"type": "done"}

    jobs.start("install", work)
    jobs.join(timeout=5)

    kinds = [e["type"] for e in pushed]
    # Three progress events inside one interval collapse to the first;
    # the terminal event is never held back.
    assert kinds == ["progress", "done"]
    assert pushed[0]["percent"] == 10


def test_a_later_progress_event_passes_once_the_interval_elapses():
    clock, pushed = FakeClock(), []
    jobs = JobRegistry(pushed.append, clock=clock, min_interval=1.0)

    def work(emit):
        emit({"type": "progress", "percent": 10})
        clock.now += 2.0
        emit({"type": "progress", "percent": 90})
        return {"type": "done"}

    jobs.start("import", work)
    jobs.join(timeout=5)

    assert [e.get("percent") for e in pushed if e["type"] == "progress"] == [10, 90]


def test_every_event_carries_its_job_id_and_kind():
    pushed = []
    jobs = JobRegistry(pushed.append)
    job_id = jobs.start("doctor", lambda emit: {"type": "done"})
    jobs.join(timeout=5)

    assert all(e["job"] == job_id and e["kind"] == "doctor" for e in pushed)


def test_a_second_job_is_refused_while_one_runs():
    import threading
    release = threading.Event()
    jobs = JobRegistry(lambda event: None)
    jobs.start("install", lambda emit: release.wait(5) and {"type": "done"})
    try:
        with pytest.raises(JobBusy):
            jobs.start("import", lambda emit: {"type": "done"})
    finally:
        release.set()
        jobs.join(timeout=5)


def test_a_crashing_job_reports_instead_of_dying_silently():
    pushed = []
    jobs = JobRegistry(pushed.append)

    def work(emit):
        raise RuntimeError("netsh exploded")

    jobs.start("ports", work)
    jobs.join(timeout=5)

    assert pushed[-1]["type"] == "crashed"
    assert "netsh exploded" in pushed[-1]["message"]


def test_the_registry_frees_up_after_a_crash():
    jobs = JobRegistry(lambda event: None)
    jobs.start("ports", lambda emit: (_ for _ in ()).throw(RuntimeError("boom")))
    jobs.join(timeout=5)
    # A crashed job that never released the slot would wedge the app: every
    # button afterwards would raise JobBusy until relaunch.
    jobs.start("doctor", lambda emit: {"type": "done"})
    jobs.join(timeout=5)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `TMPDIR=$(mktemp -d) python3 -m pytest tests/host/desktop/test_jobs.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'host.desktop.jobs'`

- [ ] **Step 3: Write minimal implementation**

```python
# host/desktop/jobs.py
"""One slow thing at a time, off the thread that owns the window.

pywebview's `start()` blocks the main thread, so anything that takes longer
than a frame runs here and reports back through `push`. The shape is the
queue-and-thread one host/setup_app/wizard.py used, minus Tk's after() pump:
there, the UI drained a queue on a timer; here, the worker pushes.

Only one job runs at a time. Not a performance choice -- InstallState is a
JSON file, and two installs writing it would race.
"""
from __future__ import annotations

import threading
import time
import uuid
from typing import Callable


class JobBusy(RuntimeError):
    """Something slow is already running."""


class JobRegistry:
    def __init__(self, push: Callable[[dict], None], *,
                 clock: Callable[[], float] = time.monotonic,
                 min_interval: float = 0.1):
        self._push = push
        self._clock = clock
        self._min_interval = min_interval
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def start(self, kind: str, work: Callable[[Callable[[dict], None]], dict]) -> str:
        job_id = uuid.uuid4().hex
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                raise JobBusy(f"{kind} cannot start: another job is running")
            thread = threading.Thread(
                target=self._run, args=(job_id, kind, work), daemon=True)
            self._thread = thread
        thread.start()
        return job_id

    def join(self, timeout: float | None = None) -> None:
        """Wait for the running job. For tests and for shutdown."""
        thread = self._thread
        if thread is not None:
            thread.join(timeout)

    def _run(self, job_id: str, kind: str, work) -> None:
        last = [None]

        def send(event: dict) -> None:
            self._push({**event, "job": job_id, "kind": kind})

        def emit(event: dict) -> None:
            # Only progress is coalesced. A terminal or state-change event
            # held back for a tenth of a second is a screen that lies; a
            # dropped one is a screen that never recovers.
            if event.get("type") != "progress":
                return send(event)
            now = self._clock()
            if last[0] is not None and now - last[0] < self._min_interval:
                return
            last[0] = now
            send(event)

        try:
            result = work(emit)
        except Exception as e:
            # Never let a worker die into a daemon thread's silence: the
            # screen that started it would spin forever.
            send({"type": "crashed", "message": f"{e}"})
            return
        send(result if result is not None else {"type": "done"})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `TMPDIR=$(mktemp -d) python3 -m pytest tests/host/desktop/test_jobs.py -q`
Expected: PASS — 6 passed

- [ ] **Step 5: Commit**

```bash
git add host/desktop/jobs.py tests/host/desktop/test_jobs.py
git commit -m "Add desktop job registry with coalesced progress"
```

---

### Task 3: The bridge's read-only surface

**Files:**
- Create: `host/desktop/api.py`
- Test: `tests/host/desktop/test_api_home.py`

**Interfaces:**
- Consumes: `route_for` (Task 1), `JobRegistry`/`JobBusy` (Task 2), `host.core.status.probe`, `host.core.install.InstallState`, `host.core.constants`.
- Produces: `class DesktopApi` with `__init__(self, provider, state, *, push, probe_fn=probe, browser_open=webbrowser.open)` and methods `home(self) -> dict`, `open_eggie(self) -> dict`.
  - `home()` returns `{"route", "state", "first_run", "app_version", "engine_version", "problem"}`.
  - `first_run` is True only when nothing has ever been recorded — `not readiness.vm_exists and not state.completed()`.

- [ ] **Step 1: Write the failing test**

```python
# tests/host/desktop/test_api_home.py
"""The bridge's answer to "what should I draw".

The first-run rule is carried over from host/setup_app/app.py and is the one
piece of routing that is not a function of Readiness alone.
"""
from __future__ import annotations

from host.core.install import InstallState
from host.core.status import Readiness
from host.desktop.api import DesktopApi

READY = Readiness(vm_exists=True, vm_reachable=True,
                  engine_version="engine-v0.1.0", agent_api=1)


class FakeProvider:
    pass


def _api(tmp_path, readiness, *, opened=None):
    state = InstallState(tmp_path / "install-state.json")
    return DesktopApi(FakeProvider(), state, push=lambda event: None,
                      probe_fn=lambda provider: readiness,
                      browser_open=(opened.append if opened is not None else lambda url: None))


def test_a_virgin_machine_is_first_run(tmp_path):
    assert _api(tmp_path, Readiness()).home()["first_run"] is True


def test_a_machine_that_got_partway_is_not_first_run(tmp_path):
    # The loop this guards: create_vm failing forever leaves vm_exists False
    # on every relaunch. Without the state check the app would re-enter the
    # wizard every time and the user could never reach Home's Doctor button.
    state = InstallState(tmp_path / "install-state.json")
    state.mark("preflight")
    api = DesktopApi(FakeProvider(), state, push=lambda event: None,
                     probe_fn=lambda provider: Readiness())
    home = api.home()
    assert home["first_run"] is False
    assert (home["route"], home["state"]) == ("home", "not_installed")


def test_a_ready_machine_is_never_first_run(tmp_path):
    assert _api(tmp_path, READY).home()["first_run"] is False


def test_home_carries_the_versions_the_odds_and_ends_row_shows(tmp_path):
    from host.core import constants
    home = _api(tmp_path, READY).home()
    assert home["app_version"] == constants.APP_VERSION
    assert home["engine_version"] == "engine-v0.1.0"


def test_home_passes_the_probe_problem_through_for_the_log(tmp_path):
    readiness = Readiness(vm_exists=True, vm_reachable=True,
                          engine_version="engine-v0.1.0",
                          problem="connection refused")
    home = _api(tmp_path, readiness).home()
    assert home["route"] == "unreachable"
    assert home["problem"] == "connection refused"


def test_open_eggie_opens_the_edge_port_not_the_agent_port(tmp_path):
    from host.core import constants
    opened = []
    _api(tmp_path, READY, opened=opened).open_eggie()
    assert opened == [f"http://localhost:{constants.EDGE_PORT}"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `TMPDIR=$(mktemp -d) python3 -m pytest tests/host/desktop/test_api_home.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'host.desktop.api'`

- [ ] **Step 3: Write minimal implementation**

```python
# host/desktop/api.py
"""The only object JavaScript can reach.

Everything here is a security boundary, so the surface is a fixed list of
method names taking scalars. No path, command or URL arriving from JS is
passed to a shell, and nothing is eval'd. Slow work goes through
JobRegistry rather than blocking the thread that owns the window.

Thin on purpose: the decisions live in view.py, which is testable without a
window.
"""
from __future__ import annotations

import webbrowser

from host.core import constants
from host.core.status import probe

from .jobs import JobRegistry
from .view import route_for


class DesktopApi:
    def __init__(self, provider, state, *, push,
                 probe_fn=probe, browser_open=webbrowser.open):
        self._provider = provider
        self._state = state
        self._probe = probe_fn
        self._open = browser_open
        self.jobs = JobRegistry(push)

    # --- what to draw -------------------------------------------------

    def home(self) -> dict:
        readiness = self._probe(self._provider)
        route, state = route_for(readiness)
        return {
            "route": route,
            "state": state,
            # Carried over from host/setup_app/app.py's _should_auto_start:
            # true only where nothing has ever been recorded. Once any step
            # has completed the answer flips, because "not vm_exists" stays
            # true across every failed create_vm relaunch too.
            "first_run": not readiness.vm_exists and not self._state.completed(),
            "app_version": constants.APP_VERSION,
            "engine_version": readiness.engine_version or "",
            "problem": readiness.problem,
        }

    # --- actions ------------------------------------------------------

    def open_eggie(self) -> dict:
        # The edge port, never the agent port: this is whatever Traefik is
        # routing, and becomes the web app for free when that ships.
        self._open(f"http://localhost:{constants.EDGE_PORT}")
        return {"ok": True}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `TMPDIR=$(mktemp -d) python3 -m pytest tests/host/desktop/test_api_home.py -q`
Expected: PASS — 6 passed

- [ ] **Step 5: Commit**

```bash
git add host/desktop/api.py tests/host/desktop/test_api_home.py
git commit -m "Add desktop bridge home and open-eggie"
```

---

### Task 4: Entrypoint and the WebView2 fallback

**Files:**
- Create: `host/desktop/__main__.py`
- Test: `tests/host/desktop/test_entry.py`

**Interfaces:**
- Consumes: `DesktopApi` (Task 3).
- Produces:
  - `ui_dir() -> Path` — resolves `ui/` next to this module, or under `sys._MEIPASS` when frozen.
  - `WEBVIEW_MISSING` — the exact sentence printed when the window cannot be created.
  - `run(provider, state, *, create=..., start=...) -> int` — builds the api, creates the window, starts the loop; returns 0, or 3 after printing `WEBVIEW_MISSING` if window creation raised.

- [ ] **Step 1: Write the failing test**

```python
# tests/host/desktop/test_entry.py
"""Opening the window, and what happens when the platform cannot.

The WebView2 runtime is evergreen but absent on un-updated Windows 10. A
traceback there is a dead end for a non-technical user; the fallback has to
name the command that still works.
"""
from __future__ import annotations

from pathlib import Path

from host.core.install import InstallState
from host.desktop.__main__ import WEBVIEW_MISSING, run, ui_dir


class FakeProvider:
    pass


def test_ui_dir_points_at_the_bundled_assets():
    assert (ui_dir() / "index.html").is_file()


def test_a_missing_webview_runtime_is_a_sentence_not_a_traceback(tmp_path, capsys):
    def create(**kwargs):
        raise RuntimeError("WebView2 runtime not found")

    code = run(FakeProvider(), InstallState(tmp_path / "s.json"),
               create=create, start=lambda **kwargs: None)

    assert code == 3
    assert WEBVIEW_MISSING in capsys.readouterr().err


def test_the_fallback_names_the_headless_command():
    # Without this the user is told the app is broken and nothing else.
    assert "eggie setup --headless" in WEBVIEW_MISSING


def test_a_working_window_starts_the_loop_and_returns_zero(tmp_path):
    started = []
    window = object()
    code = run(FakeProvider(), InstallState(tmp_path / "s.json"),
               create=lambda **kwargs: window,
               start=lambda **kwargs: started.append(kwargs))

    assert code == 0
    # debug must be off in a shipped build: it exposes devtools and a context
    # menu over the bridge.
    assert started[0]["debug"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `TMPDIR=$(mktemp -d) python3 -m pytest tests/host/desktop/test_entry.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'host.desktop.__main__'`

- [ ] **Step 3: Write minimal implementation**

Create `host/desktop/ui/index.html` as a one-line stub for now (Task 5 fills it):

```html
<!DOCTYPE html><html><head><meta charset="utf-8"><title>Eggie</title></head><body></body></html>
```

```python
# host/desktop/__main__.py
"""Build the provider, open the window, hand the loop to pywebview.

`webview.start()` owns the main thread for the life of the app, so this
module does nothing after calling it. Everything that happens later happens
on a worker thread from jobs.py.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

WINDOW_TITLE = "Eggie"
# The board's frames are 880x620. The OS draws the title bar, so that is the
# content size; min_size keeps the nine-row install panel from clipping.
WINDOW_SIZE = (880, 620)
MIN_SIZE = (800, 560)

WEBVIEW_MISSING = (
    "Eggie could not open its window because this computer is missing the "
    "Microsoft Edge WebView2 runtime.\n"
    "Install it from https://developer.microsoft.com/microsoft-edge/webview2/ "
    "and open Eggie again.\n"
    "In the meantime you can still set up Eggie by running: "
    "eggie setup --headless"
)


def ui_dir() -> Path:
    """Where the HTML lives, in a checkout and inside the frozen binary.

    `sys._MEIPASS` is PyInstaller's unpack directory. Reading it is an
    attribute check, not a platform check, so tests/test_no_platform_leak.py
    stays satisfied.
    """
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        return Path(bundle) / "host" / "desktop" / "ui"
    return Path(__file__).resolve().parent / "ui"


def _default_create(**kwargs):
    import webview
    return webview.create_window(**kwargs)


def _default_start(**kwargs):
    import webview
    webview.start(**kwargs)


def run(provider, state, *, create=_default_create, start=_default_start) -> int:
    from .api import DesktopApi

    holder: dict = {}

    def push(event: dict) -> None:
        # Events cross into JS as one JSON argument. json.dumps, never a
        # format string: a message carrying a quote would otherwise close the
        # call and inject whatever followed.
        import json
        window = holder.get("window")
        if window is not None:
            window.evaluate_js(f"window.eggie.on({json.dumps(event)})")

    api = DesktopApi(provider, state, push=push)

    try:
        window = create(title=WINDOW_TITLE, url=str(ui_dir() / "index.html"),
                        js_api=api, width=WINDOW_SIZE[0], height=WINDOW_SIZE[1],
                        min_size=MIN_SIZE)
    except Exception:
        print(WEBVIEW_MISSING, file=sys.stderr)
        return 3

    holder["window"] = window
    start(debug=False)
    return 0


def main(argv: list[str] | None = None) -> int:
    from host.core.install import InstallState
    from host.providers import default_install_dir, get_provider

    parser = argparse.ArgumentParser(prog="eggie-desktop")
    # Written by provider.register_resume() into Windows RunOnce. The flag
    # must keep working or a restarted machine never finishes setup.
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args(argv)

    root = default_install_dir().parent
    state = InstallState(root / "install-state.json")
    provider = get_provider()
    provider.resume = args.resume
    return run(provider, state)


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `TMPDIR=$(mktemp -d) python3 -m pytest tests/host/desktop/test_entry.py -q`
Expected: PASS — 4 passed

- [ ] **Step 5: Commit**

```bash
git add host/desktop/__main__.py host/desktop/ui/index.html tests/host/desktop/test_entry.py
git commit -m "Add desktop entrypoint with WebView2 fallback"
```

---

### Task 5: The UI shell — tokens, fonts, Home and first run

**Files:**
- Create: `host/desktop/ui/app.css`, `host/desktop/ui/app.js`, `host/desktop/ui/fonts/*.woff2`, `host/desktop/ui/fonts/OFL.txt`
- Modify: `host/desktop/ui/index.html`
- Test: `tests/host/desktop/test_ui_assets.py`

**Interfaces:**
- Consumes: `DesktopApi.home()`, `DesktopApi.open_eggie()` (Task 3).
- Produces: `window.eggie.on(event)` — the global the Python `push` calls; `data-screen` attributes on each `<template>` naming a route+state pair.

- [ ] **Step 1: Write the failing test**

```python
# tests/host/desktop/test_ui_assets.py
"""Invariants of the bundled UI, not its rendering.

There is no JS test runner in this repo and one is not being added to assert
that a template renders. What is worth pinning is the boundary: a desktop app
whose fonts come from a CDN is broken on a train, and one that can reach any
remote origin has a bridge worth attacking.
"""
from __future__ import annotations

import re
from pathlib import Path

UI = Path(__file__).resolve().parents[3] / "host" / "desktop" / "ui"

# Matches a remote origin in markup, CSS or JS. Deliberately not anchored to
# src=/href=: a fetch() or an @import is the same problem.
REMOTE = re.compile(r"https?://(?!localhost|127\.0\.0\.1)", re.IGNORECASE)


def _assets() -> list[Path]:
    files = [p for p in UI.rglob("*") if p.suffix in {".html", ".css", ".js"}]
    # A relative Path() would pass vacuously from another directory; this has
    # bitten the repo three times, so assert we scanned something.
    assert files, f"no UI assets found under {UI}"
    return files


def test_no_asset_reaches_a_remote_origin():
    offenders = []
    for path in _assets():
        for number, line in enumerate(path.read_text().splitlines(), 1):
            if REMOTE.search(line):
                offenders.append(f"{path.relative_to(UI)}:{number}: {line.strip()}")
    assert not offenders, "UI assets must work offline:\n" + "\n".join(offenders)


def test_the_three_typefaces_are_bundled():
    names = {p.name for p in (UI / "fonts").glob("*.woff2")}
    for family in ("bricolage-grotesque", "hanken-grotesk", "ibm-plex-mono"):
        assert any(n.startswith(family) for n in names), f"missing {family}"


def test_the_font_licenses_ship_with_them():
    # All three are SIL OFL 1.1, which requires the license to travel with
    # the binary.
    assert (UI / "fonts" / "OFL.txt").is_file()


def test_the_page_forbids_remote_origins_at_runtime():
    head = (UI / "index.html").read_text()
    assert "Content-Security-Policy" in head
    assert "default-src 'self'" in head


def test_every_home_state_has_a_template():
    markup = (UI / "index.html").read_text()
    for screen in ("home:not_installed", "home:stopped", "home:running",
                   "home:wrong", "unreachable", "first-run",
                   "updates-unavailable"):
        assert f'data-screen="{screen}"' in markup, f"no template for {screen}"


def test_the_update_tile_promises_nothing_it_cannot_do():
    # There is no update backend. The tile ships because the board has it,
    # but it must not claim to have checked anything.
    markup = (UI / "index.html").read_text()
    assert "nothing to check for yet" in markup.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `TMPDIR=$(mktemp -d) python3 -m pytest tests/host/desktop/test_ui_assets.py -q`
Expected: FAIL — `test_the_three_typefaces_are_bundled` and the template test fail; `fonts/` does not exist.

- [ ] **Step 3: Write minimal implementation**

Download the three families as woff2 and their license:

```bash
mkdir -p host/desktop/ui/fonts
cd host/desktop/ui/fonts
curl -L -o bricolage-grotesque-800.woff2 \
  "https://cdn.jsdelivr.net/fontsource/fonts/bricolage-grotesque@latest/latin-800-normal.woff2"
curl -L -o hanken-grotesk-400.woff2 \
  "https://cdn.jsdelivr.net/fontsource/fonts/hanken-grotesk@latest/latin-400-normal.woff2"
curl -L -o hanken-grotesk-600.woff2 \
  "https://cdn.jsdelivr.net/fontsource/fonts/hanken-grotesk@latest/latin-600-normal.woff2"
curl -L -o hanken-grotesk-700.woff2 \
  "https://cdn.jsdelivr.net/fontsource/fonts/hanken-grotesk@latest/latin-700-normal.woff2"
curl -L -o ibm-plex-mono-400.woff2 \
  "https://cdn.jsdelivr.net/fontsource/fonts/ibm-plex-mono@latest/latin-400-normal.woff2"
curl -L -o OFL.txt \
  "https://raw.githubusercontent.com/googlefonts/bricolage/main/OFL.txt"
cd -
```

`host/desktop/ui/app.css` — the board's `[data-theme]` block becomes `:root`, and the dark block becomes a `prefers-color-scheme` media query. Values copied verbatim from the design board:

```css
/* Tokens are the design board's, unchanged. The board scoped them to
   [data-theme] because it showed both themes side by side; the app has one
   theme at a time and follows the OS, so they live on :root. */
:root {
  --desk:#EFE3D2; --cream:#FFF8EE; --surface:#FFFFFF; --surface-2:#FBF1E4;
  --line:rgba(58,38,22,.10); --line-2:rgba(58,38,22,.20);
  --ink:#2B1E14; --ink-2:#5C4A3C; --ink-3:#8B7868;
  --yolk:#FFB020; --yolk-deep:#B96C00; --yolk-soft:#FFE9BF;
  --basil:#1F7F52; --basil-soft:#DCF2E6;
  --paprika:#C63A20; --paprika-soft:#FBE3DC;
  --cold:#8B959B; --cold-soft:#EAEDEF;
  --shadow:rgba(80,45,12,.40);
  --r-sm:10px; --r-md:14px; --r-lg:18px; --r-xl:22px;
  --ease:cubic-bezier(.2,.8,.2,1);
}
@media (prefers-color-scheme: dark) {
  :root {
    --desk:#14110E; --cream:#1C1714; --surface:#221C17; --surface-2:#2A231D;
    --line:rgba(255,238,214,.10); --line-2:rgba(255,238,214,.20);
    --ink:#F8EFE3; --ink-2:#CFBDAA; --ink-3:#9D8B7B;
    --yolk:#FFB93D; --yolk-deep:#FFCE70; --yolk-soft:#4A3517;
    --basil:#63CC93; --basil-soft:#1E3A2C;
    --paprika:#FF8768; --paprika-soft:#3E211A;
    --cold:#8E9AA1; --cold-soft:#2B3134;
    --shadow:rgba(0,0,0,.65);
  }
}

@font-face { font-family:'Bricolage Grotesque'; font-weight:800; font-display:swap;
  src:url('fonts/bricolage-grotesque-800.woff2') format('woff2'); }
@font-face { font-family:'Hanken Grotesk'; font-weight:400; font-display:swap;
  src:url('fonts/hanken-grotesk-400.woff2') format('woff2'); }
@font-face { font-family:'Hanken Grotesk'; font-weight:600; font-display:swap;
  src:url('fonts/hanken-grotesk-600.woff2') format('woff2'); }
@font-face { font-family:'Hanken Grotesk'; font-weight:700; font-display:swap;
  src:url('fonts/hanken-grotesk-700.woff2') format('woff2'); }
@font-face { font-family:'IBM Plex Mono'; font-weight:400; font-display:swap;
  src:url('fonts/ibm-plex-mono-400.woff2') format('woff2'); }

* { box-sizing:border-box; }
html, body { height:100%; }
body {
  margin:0; background:var(--cream); color:var(--ink);
  font-family:'Hanken Grotesk', system-ui, sans-serif;
  -webkit-font-smoothing:antialiased;
  /* The OS draws the title bar; the board's mock chrome is not reproduced. */
  overflow:hidden;
}
#screen { height:100%; display:flex; flex-direction:column; }

/* The board has no focus states -- it was never keyboard-driven. Derived
   from --yolk so it reads as the same system in both themes. */
:focus-visible {
  outline:3px solid var(--yolk-deep); outline-offset:2px; border-radius:4px;
}

@keyframes om-spin { to { transform:rotate(360deg); } }
@keyframes om-sizzle { 0%{transform:translateY(0) scale(.5);opacity:0}
  25%{opacity:.95} 100%{transform:translateY(-30px) scale(1.2);opacity:0} }
@keyframes om-glow { 0%,100%{transform:scale(1);opacity:.85}
  50%{transform:scale(1.05);opacity:1} }
@keyframes om-bob { 0%,100%{transform:translateY(0)} 50%{transform:translateY(-5px)} }
@keyframes om-zzz { 0%{opacity:0;transform:translate(0,0) scale(.6)}
  35%{opacity:1} 100%{opacity:0;transform:translate(16px,-26px) scale(1.15)} }
@keyframes om-bar { 0%{transform:translateX(-100%)} 100%{transform:translateX(320%)} }
@keyframes om-blink { 0%,100%{opacity:1} 50%{opacity:.35} }

/* The board's own motion note: loops are for waiting, and only one thing
   loops at a time. Under reduced-motion none of them do -- the pan is still
   a pan, it just stops sizzling. */
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration:0.001ms !important;
    animation-iteration-count:1 !important;
    transition-duration:0.001ms !important;
  }
}

.btn-primary {
  border:0; cursor:pointer; padding:18px 34px; border-radius:var(--r-lg);
  background:var(--yolk); color:#2B1E14;
  font:700 20px 'Bricolage Grotesque', sans-serif;
  box-shadow:0 12px 26px -12px rgba(255,176,32,.95);
  transition:transform 120ms var(--ease);
}
.btn-primary:hover { transform:translateY(-2px); }
.btn-primary:disabled { opacity:.6; cursor:not-allowed; transform:none; }
.btn-secondary {
  cursor:pointer; padding:15px 24px; border-radius:var(--r-md);
  background:var(--surface); border:1px solid var(--line-2); color:var(--ink-2);
  font:600 15.5px 'Hanken Grotesk', sans-serif;
  transition:background 120ms var(--ease), color 120ms var(--ease);
}
.btn-secondary:hover { background:var(--surface-2); color:var(--ink); }
```

`host/desktop/ui/app.js` — router:

```js
'use strict';

// The one global Python reaches, and the one place a pushed event lands.
// Handlers register by event kind; an unknown kind is ignored rather than
// thrown, so an older UI paired with a newer bridge degrades quietly.
window.eggie = {
  handlers: {},
  on(event) {
    const handler = this.handlers[event.kind];
    if (handler) handler(event);
  },
};

const api = () => window.pywebview.api;

function show(screen, data) {
  const template = document.querySelector(`template[data-screen="${screen}"]`);
  if (!template) throw new Error(`no template for ${screen}`);
  const root = document.getElementById('screen');
  root.replaceChildren(template.content.cloneNode(true));
  root.dataset.screen = screen;
  fill(root, data || {});
  wire(root);
}

// Every [data-field] is replaced by the matching key. Values are written with
// textContent, never innerHTML: an engine version or a provider's error text
// is data, and some of it comes from a subprocess.
function fill(root, data) {
  root.querySelectorAll('[data-field]').forEach((node) => {
    const value = data[node.dataset.field];
    if (value !== undefined && value !== null) node.textContent = String(value);
  });
}

function wire(root) {
  root.querySelectorAll('[data-action]').forEach((node) => {
    node.addEventListener('click', () => ACTIONS[node.dataset.action](node));
  });
}

const ACTIONS = {
  'open-eggie': () => api().open_eggie(),
  'go-home': () => refresh(),
};

async function refresh() {
  const home = await api().home();
  document.title = 'Eggie';
  if (home.first_run) return show('first-run', home);
  show(home.route === 'unreachable' ? 'unreachable' : `home:${home.state}`, home);
}

window.addEventListener('pywebviewready', refresh);
```

`host/desktop/ui/index.html` — the `<head>` plus one `<template>` per screen. Copy is taken verbatim from the design board.

```html
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<!-- No remote origin is reachable from this page. The bridge is the only way
     out, and it takes scalars. -->
<meta http-equiv="Content-Security-Policy"
      content="default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; font-src 'self';">
<title>Eggie</title>
<link rel="stylesheet" href="app.css">
</head>
<body>
<main id="screen"></main>

<template data-screen="first-run">
  <section class="pad">
    <h1 class="display">Welcome to Eggie</h1>
    <p class="lede">Eggie sets up a tiny private computer inside your computer,
      so you can build real, working apps just by chatting with it.</p>
    <ol class="steps">
      <li><b>We build you a kitchen</b><span>A small Linux machine in a sealed box.
        Your own files stay exactly where they are.</span></li>
      <li><b>We hire the cook</b><span>A coding agent moves in. It writes the code
        and runs it in there, never out here.</span></li>
      <li><b>You start ordering</b><span>Describe what you want. Watch it get made.
        Change your mind as often as you like.</span></li>
    </ol>
    <div class="row">
      <button class="btn-primary" data-action="start-install">Warm up the kitchen</button>
      <span class="hint">About 4 minutes. You can quit and come back.</span>
    </div>
  </section>
</template>

<template data-screen="home:running">
  <section class="pad">
    <span class="badge badge-running">Running</span>
    <h1 class="display">The kitchen is open</h1>
    <p class="lede">Your machine is up and the cook is at the counter.</p>
    <div class="row">
      <button class="btn-primary" data-action="open-eggie">Open Eggie</button>
      <button class="btn-secondary" data-action="stop-vm">Stop the kitchen</button>
    </div>
    <footer class="odds"><span data-field="app_version"></span></footer>
  </section>
</template>

<template data-screen="home:stopped">
  <section class="pad">
    <span class="badge badge-cold">Stopped</span>
    <h1 class="display">The pan is cold</h1>
    <p class="lede">Everything is saved and waiting. Starting up takes about 20 seconds.</p>
    <div class="row">
      <button class="btn-primary" data-action="start-vm">Start the kitchen</button>
      <button class="btn-secondary" disabled>Open Eggie</button>
      <span class="hint">Opens by itself once the pan is hot.</span>
    </div>
    <footer class="odds"><span data-field="app_version"></span></footer>
  </section>
</template>

<template data-screen="home:not_installed">
  <section class="pad">
    <span class="badge">Not installed</span>
    <h1 class="display">No kitchen yet</h1>
    <p class="lede">There's nothing cooking on this computer. Setting one up takes
      about four minutes and 6 GB of space.</p>
    <div class="row">
      <button class="btn-primary" data-action="start-install">Set up the kitchen</button>
    </div>
    <footer class="odds"><span data-field="app_version"></span></footer>
  </section>
</template>

<template data-screen="home:wrong">
  <section class="pad">
    <span class="badge badge-bad">Needs a look</span>
    <h1 class="display">Something's off in here</h1>
    <p class="lede">The kitchen started but one of its parts didn't. Your projects
      are safe — Doctor can usually sort this out on its own.</p>
    <div class="row">
      <button class="btn-primary" data-action="doctor">Run Doctor</button>
      <button class="btn-secondary" data-action="restart-vm">Restart the kitchen</button>
    </div>
    <details class="log"><summary>Show the log</summary>
      <pre data-field="problem"></pre></details>
    <footer class="odds"><span data-field="app_version"></span></footer>
  </section>
</template>

<template data-screen="unreachable">
  <section class="pad">
    <span class="badge badge-warm">Running, but quiet</span>
    <h1 class="display">Nobody's answering the door</h1>
    <p class="lede">Your machine is on — we can see it humming — but Eggie can't
      get a word in. Nine times out of ten a retry sorts it out. Nothing is lost
      either way.</p>
    <div class="row">
      <button class="btn-primary" data-action="go-home">Try again</button>
      <button class="btn-secondary" data-action="repair">Repair the connection</button>
      <span class="hint">Repair restarts the link. Your projects stay put.</span>
    </div>
    <details class="log"><summary>Show logs</summary>
      <pre data-field="problem"></pre></details>
  </section>
</template>

<script src="app.js"></script>
</body>
</html>
```

The `data-action` names `start-install`, `stop-vm`, `start-vm`, `restart-vm`, `doctor` and `repair` are wired in Milestone 2 and 3. Add them to `ACTIONS` as no-ops returning `undefined` for now so a click does not throw:

```js
// Wired in later milestones; present so a click is inert rather than fatal.
['start-install', 'stop-vm', 'start-vm', 'restart-vm', 'doctor', 'repair']
  .forEach((name) => { if (!ACTIONS[name]) ACTIONS[name] = () => {}; });
```

Each Home template's `<footer class="odds">` carries the board's "Odds and ends" grid — five tiles plus the version line. Copy verbatim, with the two tiles that need a VM disabled in the `not_installed` state exactly as the board shows them:

```html
<footer class="odds">
  <span class="overline">Odds and ends</span>
  <div class="tiles">
    <button data-action="import">Import folder</button>
    <button data-action="ports">Ports</button>
    <button data-action="doctor">Doctor</button>
    <button data-action="check-updates">Check for updates</button>
    <button class="tile-danger" data-action="uninstall">Uninstall</button>
    <span class="tile-note"><span data-field="app_version"></span> · <span data-field="engine_version"></span></span>
  </div>
</footer>
```

```html
<template data-screen="updates-unavailable">
  <section class="pad centered">
    <h2 class="title">Nothing to check yet</h2>
    <p class="lede">Eggie updates itself when a new version is released.
      There's nothing to check for yet.</p>
    <p class="hint"><span data-field="app_version"></span> ·
      <span data-field="engine_version"></span></p>
    <div class="row"><button class="btn-secondary" data-action="go-home">Done</button></div>
  </section>
</template>
```

```js
// Ships visible and honest rather than hidden: the board has the tile, and
// there is no update backend behind it yet.
ACTIONS['check-updates'] = async () => show('updates-unavailable', await api().home());
ACTIONS['uninstall'] = () => show('uninstall-confirm', {});
```

Layout classes (`.pad`, `.display`, `.lede`, `.steps`, `.row`, `.hint`, `.badge*`, `.odds`, `.tiles`, `.log`) are styled in `app.css` using the board's exact type sizes from the token sheet: Display 42/800 Bricolage, Title 31, Body 16.5, Label 14.5, Overline 12 at +.1em, Mono 13. Window padding 40/48, card padding 16–22, gaps 10–14 within a group and 22–28 between groups.

- [ ] **Step 4: Run test to verify it passes**

Run: `TMPDIR=$(mktemp -d) python3 -m pytest tests/host/desktop/ -q`
Expected: PASS — all of Milestone 1's tests

- [ ] **Step 5: Verify the window actually opens**

Run: `python3 -m host.desktop` on a machine with a webview. Expected: an 880×620 window showing one of the Home states, following the OS light/dark setting. This cannot be verified from the WSL dev shell — record it as done on Windows or macOS.

- [ ] **Step 6: Commit**

```bash
git add host/desktop/ui tests/host/desktop/test_ui_assets.py
git commit -m "Add desktop UI shell, tokens, bundled fonts and Home screens"
```

---

# Milestone 2 — The install flow

Deliverable: "Warm up the kitchen" runs a real install with live per-step rows, byte progress, and the restart and failure screens.

---

### Task 6: Step list → row model

**Files:**
- Modify: `host/desktop/view.py`
- Test: `tests/host/desktop/test_view_steps.py`

**Interfaces:**
- Consumes: `host.core.install.Step`.
- Produces:
  - `STEP_LABELS: dict[str, str]` — moved verbatim from `host/setup_app/wizard.py::LABELS`.
  - `rows_for(steps: list[Step]) -> list[dict]` — one `{"name", "label", "progress"}` per step, in order.
  - `step_label(step: Step) -> str` — `step.label` when set, else `STEP_LABELS`, else `step.name`.

- [ ] **Step 1: Write the failing test**

```python
# tests/host/desktop/test_view_steps.py
"""The install panel's rows.

The design board is a macOS mock and shows seven. default_steps returns seven
on Lima and nine on WSL2, so a hard-coded seven puts "Step 4 of 7" on a
machine running nine steps and binds the progress bar to the wrong row.
"""
from __future__ import annotations

from host.core.install import Step
from host.desktop.view import rows_for, step_label


def _step(name, **kwargs):
    return Step(name, lambda: None, **kwargs)


def test_rows_follow_the_list_the_factory_returned():
    windows = [_step("preflight"), _step("remediate"), _step("reboot_gate"),
               _step("fetch_image", progress=True), _step("create_vm"),
               _step("bootstrap"), _step("connect"), _step("verify"),
               _step("finish")]
    rows = rows_for(windows)
    assert [r["name"] for r in rows] == [s.name for s in windows]
    assert len(rows) == 9


def test_a_mac_list_is_the_seven_the_board_draws():
    mac = [_step("preflight"),
           _step("install_runtime", label="Installing Lima 2.2.0", progress=True),
           _step("create_vm"), _step("bootstrap"), _step("connect"),
           _step("verify"), _step("finish")]
    assert len(rows_for(mac)) == 7


def test_only_the_download_row_carries_a_progress_bar():
    rows = rows_for([_step("preflight"), _step("fetch_image", progress=True)])
    assert [r["progress"] for r in rows] == [False, True]


def test_a_provider_that_names_its_own_step_wins():
    # "Installing Lima 2.2.0" is Lima's sentence -- the version in it is the
    # provider's fact. A label table copy would go stale on the next pin.
    assert step_label(_step("install_runtime", label="Installing Lima 2.2.0")) \
        == "Installing Lima 2.2.0"


def test_a_step_without_a_label_uses_the_table():
    assert step_label(_step("bootstrap")) == "Installing Eggie"


def test_an_unknown_step_falls_back_to_its_name():
    assert step_label(_step("brand_new_step")) == "brand_new_step"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `TMPDIR=$(mktemp -d) python3 -m pytest tests/host/desktop/test_view_steps.py -q`
Expected: FAIL — `ImportError: cannot import name 'rows_for'`

- [ ] **Step 3: Write minimal implementation**

Append to `host/desktop/view.py`:

```python
from host.core.install import Step

# Carried over from host/setup_app/wizard.py. install_runtime is absent on
# purpose: its text comes from provider.runtime().label, because the version
# in it is Lima's fact, not the installer's -- a second copy here would go
# stale the first time the pinned version changes.
STEP_LABELS = {
    "preflight": "Checking this computer",
    "remediate": "Turning on Windows features",
    "reboot_gate": "Restart needed",
    "fetch_image": "Downloading Linux image",
    "create_vm": "Preparing the virtual machine",
    "bootstrap": "Installing Eggie",
    "connect": "Connecting to the Eggie service",
    "verify": "Testing the setup",
    "finish": "Finishing up",
}


def step_label(step: Step) -> str:
    return step.label or STEP_LABELS.get(step.name, step.name)


def rows_for(steps: list[Step]) -> list[dict]:
    """One row per step the factory actually returned.

    Never a fixed seven: default_steps drops install_runtime on Windows (wsl
    ships with the OS) and drops remediate, reboot_gate and fetch_image on
    macOS (no features to enable, and limactl fetches its own image). The
    "Step N of M" counter is derived from this list for the same reason.
    """
    return [{"name": s.name, "label": step_label(s), "progress": s.progress}
            for s in steps]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `TMPDIR=$(mktemp -d) python3 -m pytest tests/host/desktop/test_view_steps.py -q`
Expected: PASS — 6 passed

- [ ] **Step 5: Commit**

```bash
git add host/desktop/view.py tests/host/desktop/test_view_steps.py
git commit -m "Add install row model derived from the real step list"
```

---

### Task 7: Progress and terminal events

**Files:**
- Modify: `host/desktop/view.py`, `host/desktop/api.py`
- Test: `tests/host/desktop/test_view_events.py`, `tests/host/desktop/test_api_install.py`

**Interfaces:**
- Consumes: `rows_for` (Task 6), `JobRegistry` (Task 2), `host.core.install.{run_install, RebootRequired, InstallError, DeadEnd, Progress}`.
- Produces:
  - `progress_event(p: Progress) -> dict` → `{"type": "step", "step", "status", "message", "fraction"}`.
  - `terminal_event(exc: BaseException | None) -> dict` → `{"type": "done"}` / `{"type": "reboot"}` / `{"type": "failed", "message", "action"}` / `{"type": "dead_end", "message"}`.
  - `DesktopApi.start_install(self) -> dict` returning `{"job": id, "rows": [...]}`, and `DesktopApi.__init__` gaining a `steps_factory` argument.

- [ ] **Step 1: Write the failing test**

```python
# tests/host/desktop/test_view_events.py
"""Turning run_install's reports and exceptions into screen events.

run_install already reported a `failed` Progress for the row before it
raises, so the terminal event does not need to carry a step name -- but it
does need to distinguish the three endings, because the board offers a
different pair of buttons for each.
"""
from __future__ import annotations

from host.core.install import DeadEnd, InstallError, Progress, RebootRequired
from host.desktop.view import progress_event, terminal_event


def test_a_running_report_names_its_row():
    event = progress_event(Progress("bootstrap", "running"))
    assert event["type"] == "step"
    assert (event["step"], event["status"]) == ("bootstrap", "running")


def test_a_fraction_survives_for_the_download_bar():
    event = progress_event(Progress("fetch_image", "running", fraction=0.58))
    assert event["fraction"] == 0.58


def test_a_step_without_a_fraction_reports_none_not_zero():
    # Zero would draw an empty bar on every ordinary step.
    assert progress_event(Progress("connect", "running"))["fraction"] is None


def test_a_clean_run_is_done():
    assert terminal_event(None) == {"type": "done"}


def test_reboot_required_keeps_its_own_ending():
    # Rendering this as a failure would lose the resume path entirely: the
    # user would be offered a retry instead of a restart.
    assert terminal_event(RebootRequired())["type"] == "reboot"


def test_an_install_error_carries_the_step_s_own_next_move():
    exc = InstallError("fetch_image", "connection reset",
                       "Run setup again and the download continues.")
    event = terminal_event(exc)
    assert event["type"] == "failed"
    assert event["message"] == "connection reset"
    assert event["action"] == "Run setup again and the download continues."


def test_a_dead_end_is_not_a_retry():
    # DeadEnd is "a blocking check no code can fix". Offering "Try this step
    # again" there sends the user round a loop that cannot terminate.
    event = terminal_event(DeadEnd("Virtualization is off in the BIOS"))
    assert event["type"] == "dead_end"
    assert "BIOS" in event["message"]


def test_an_unexpected_exception_still_reaches_the_screen():
    event = terminal_event(ValueError("rootfs is required"))
    assert event["type"] == "failed"
    assert "rootfs is required" in event["message"]
```

```python
# tests/host/desktop/test_api_install.py
"""The install job as the bridge runs it."""
from __future__ import annotations

import threading

from host.core.install import InstallError, InstallState, Step
from host.core.status import Readiness
from host.desktop.api import DesktopApi


class FakeProvider:
    pass


def _api(tmp_path, steps, pushed):
    return DesktopApi(FakeProvider(), InstallState(tmp_path / "s.json"),
                      push=pushed.append,
                      probe_fn=lambda provider: Readiness(),
                      steps_factory=lambda: steps)


def _drain(api):
    api.jobs.join(timeout=5)


def test_start_install_hands_back_the_rows_to_draw(tmp_path):
    steps = [Step("preflight", lambda: None), Step("finish", lambda: None)]
    started = _api(tmp_path, steps, []).start_install()
    assert [r["name"] for r in started["rows"]] == ["preflight", "finish"]
    assert "job" in started


def test_a_successful_install_ends_in_done(tmp_path):
    pushed = []
    api = _api(tmp_path, [Step("preflight", lambda: None)], pushed)
    api.start_install()
    _drain(api)
    assert pushed[-1]["type"] == "done"
    assert [e["type"] for e in pushed if e["type"] == "step"]


def test_a_failing_step_ends_in_failed_carrying_its_action(tmp_path):
    def boom():
        raise RuntimeError("connection reset")

    pushed = []
    api = _api(tmp_path, [Step("fetch_image", boom, action="Try again later.")], pushed)
    api.start_install()
    _drain(api)

    assert pushed[-1]["type"] == "failed"
    assert pushed[-1]["action"] == "Try again later."


def test_the_steps_factory_is_called_per_run_not_once(tmp_path):
    # The steps close over provider state -- provider.rootfs is assigned while
    # the list is built -- so a second run against the first run's list would
    # install against stale bindings.
    calls = []

    def factory():
        calls.append(1)
        return [Step("preflight", lambda: None)]

    api = DesktopApi(FakeProvider(), InstallState(tmp_path / "s.json"),
                     push=lambda e: None, probe_fn=lambda p: Readiness(),
                     steps_factory=factory)
    api.start_install()
    _drain(api)
    api.start_install()
    _drain(api)
    assert len(calls) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `TMPDIR=$(mktemp -d) python3 -m pytest tests/host/desktop/test_view_events.py tests/host/desktop/test_api_install.py -q`
Expected: FAIL — `ImportError: cannot import name 'progress_event'`

- [ ] **Step 3: Write minimal implementation**

Append to `host/desktop/view.py`:

```python
from host.core.install import DeadEnd, InstallError, Progress, RebootRequired


def progress_event(progress: Progress) -> dict:
    return {
        "type": "step",
        "step": progress.step,
        "status": progress.status,
        "message": progress.message,
        # None, never 0.0: only a step that declared progress=True has a
        # fraction, and a zero here would draw an empty bar on every other row.
        "fraction": progress.fraction,
    }


def terminal_event(exc: BaseException | None) -> dict:
    """How the run ended, in the three shapes the board draws differently.

    run_install has already reported a `failed` Progress naming the row, so
    nothing here needs the step name -- only the ending, because each offers
    a different pair of buttons.
    """
    if exc is None:
        return {"type": "done"}
    if isinstance(exc, RebootRequired):
        return {"type": "reboot"}
    if isinstance(exc, DeadEnd):
        # No code can fix this one, so the failed screen's "Try this step
        # again" is the wrong offer and the UI hides it for this type.
        return {"type": "dead_end", "message": f"{exc}"}
    if isinstance(exc, InstallError):
        return {"type": "failed", "message": exc.message, "action": exc.action}
    return {"type": "failed", "message": f"{exc}", "action": ""}
```

Modify `host/desktop/api.py` — add `steps_factory` to `__init__` and the `start_install` method:

```python
# in __init__'s signature, after `state`:
#     *, push, probe_fn=probe, browser_open=webbrowser.open, steps_factory=None
# and in the body:
        self._steps_factory = steps_factory

    def start_install(self) -> dict:
        from host.core.install import run_install

        # A fresh list per run: the steps close over provider state
        # (provider.rootfs is assigned while the list is built), so re-running
        # a list built for an earlier run installs against stale bindings.
        steps = self._steps_factory()

        def work(emit):
            def report(progress):
                emit(progress_event(progress))

            try:
                run_install(steps, self._state, report)
            except Exception as e:
                return terminal_event(e)
            return terminal_event(None)

        job_id = self.jobs.start("install", work)
        return {"job": job_id, "rows": rows_for(steps)}
```

Update the import line in `api.py` to `from .view import progress_event, route_for, rows_for, terminal_event`.

- [ ] **Step 4: Run test to verify it passes**

Run: `TMPDIR=$(mktemp -d) python3 -m pytest tests/host/desktop/ -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add host/desktop/view.py host/desktop/api.py tests/host/desktop/test_view_events.py tests/host/desktop/test_api_install.py
git commit -m "Wire the install job and its three endings into the bridge"
```

---

### Task 8: `provider.reboot()` and the restart order

**Files:**
- Modify: `host/core/provider.py`, `host/providers/wsl2.py`, `host/providers/lima.py`, `host/desktop/api.py`
- Test: `tests/host/test_provider_reboot.py`, add to `tests/host/desktop/test_api_install.py`

**Interfaces:**
- Consumes: `JobRegistry` (Task 2).
- Produces: `VmProvider.reboot() -> None` on the Protocol and both providers; `DesktopApi.reboot_now() -> dict`.

- [ ] **Step 1: Write the failing test**

```python
# tests/host/test_provider_reboot.py
"""Restarting the machine from the reboot screen.

The board's "Restart now" button is new: today reboot_gate_step only raises
and the user restarts by hand. Rebooting is the one thing in this feature
that genuinely differs per platform, so it belongs in a provider.
"""
from __future__ import annotations

from host.core.provider import VmProvider
from host.providers.lima import LimaProvider
from host.providers.wsl2 import Wsl2Provider


class FakeRunner:
    def __init__(self):
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
        class Result:
            returncode = 0
            stdout = b""
            stderr = b""
        return Result()


def test_the_protocol_declares_reboot():
    assert hasattr(VmProvider, "reboot")


def test_windows_reboots_through_shutdown():
    runner = FakeRunner()
    Wsl2Provider(runner=runner).reboot()
    argv = runner.calls[-1]
    assert argv[0] == "shutdown"
    assert "/r" in argv


def test_macos_reboots_through_osascript():
    # Plain `shutdown -r` needs root; the Apple Events route prompts the user
    # the same way choosing Restart from the Apple menu does.
    runner = FakeRunner()
    LimaProvider(runner=runner).reboot()
    argv = runner.calls[-1]
    assert argv[0] == "osascript"
    assert any("restart" in part for part in argv)
```

Append to `tests/host/desktop/test_api_install.py`:

```python
def test_resume_is_registered_before_the_machine_goes_down(tmp_path):
    """A reboot fired before RunOnce is written never comes back to setup."""
    order = []

    class RebootingProvider:
        def register_resume(self, exe_path):
            order.append("register")

        def reboot(self):
            order.append("reboot")

    api = DesktopApi(RebootingProvider(), InstallState(tmp_path / "s.json"),
                     push=lambda e: None, probe_fn=lambda p: Readiness(),
                     steps_factory=lambda: [])
    api.reboot_now()

    assert order == ["register", "reboot"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `TMPDIR=$(mktemp -d) python3 -m pytest tests/host/test_provider_reboot.py tests/host/desktop/test_api_install.py -q`
Expected: FAIL — `AttributeError: 'Wsl2Provider' object has no attribute 'reboot'`

- [ ] **Step 3: Write minimal implementation**

In `host/core/provider.py`, add to the `VmProvider` Protocol after `reboot_required`:

```python
    def reboot(self) -> None: ...
```

In `host/providers/wsl2.py`:

```python
    def reboot(self) -> None:
        """Restart Windows now.

        /t 0 rather than a delay: the user pressed a button that says
        "Restart now", and a countdown they cannot see is worse than none.
        Resume is already registered by the gate before this is reachable.
        """
        self._run(["shutdown", "/r", "/t", "0"])
```

In `host/providers/lima.py`:

```python
    def reboot(self) -> None:
        """Restart macOS now.

        Through Apple Events rather than `shutdown -r`, which needs root: this
        prompts the user exactly as choosing Restart from the Apple menu does.
        Unreachable in practice -- reboot_required() is always False here --
        but the Protocol is satisfied honestly rather than with a pass.
        """
        self._run(["osascript", "-e", 'tell application "System Events" to restart'])
```

In `host/desktop/api.py`:

```python
    def reboot_now(self) -> dict:
        import sys
        # Order matters and is pinned by a test: a machine that goes down
        # before RunOnce is written never comes back to setup.
        self._provider.register_resume(sys.executable)
        self._provider.reboot()
        return {"ok": True}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `TMPDIR=$(mktemp -d) python3 -m pytest tests/host/ -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add host/core/provider.py host/providers/wsl2.py host/providers/lima.py host/desktop/api.py tests/host/test_provider_reboot.py tests/host/desktop/test_api_install.py
git commit -m "Add provider.reboot() and order it after register_resume"
```

---

### Task 9: Install screens

**Files:**
- Modify: `host/desktop/ui/index.html`, `host/desktop/ui/app.js`, `host/desktop/ui/app.css`
- Test: extend `tests/host/desktop/test_ui_assets.py`

**Interfaces:**
- Consumes: `DesktopApi.start_install()`, `DesktopApi.reboot_now()`, the `step` / `done` / `reboot` / `failed` / `dead_end` events.

- [ ] **Step 1: Write the failing test**

Append to `tests/host/desktop/test_ui_assets.py`:

```python
def test_every_install_state_has_a_template():
    markup = (UI / "index.html").read_text()
    for screen in ("install:running", "install:reboot", "install:failed"):
        assert f'data-screen="{screen}"' in markup, f"no template for {screen}"


def test_the_install_panel_has_no_hard_coded_step_count():
    # "Step 4 of 7" is a macOS fact. Windows runs nine.
    markup = (UI / "index.html").read_text()
    assert "of 7" not in markup
```

- [ ] **Step 2: Run test to verify it fails**

Run: `TMPDIR=$(mktemp -d) python3 -m pytest tests/host/desktop/test_ui_assets.py -q`
Expected: FAIL — `no template for install:running`

- [ ] **Step 3: Write minimal implementation**

Add three templates to `index.html`. Each is the board's two-column layout: a 300px left column (the pan, headline, buttons) and a `<ol data-rows>` panel on the right that `app.js` fills from `rows`.

```html
<template data-screen="install:running">
  <section class="install">
    <div class="install-left">
      <div class="pan pan-hot" aria-hidden="true"></div>
      <h2 class="title">Heating the pan</h2>
      <p class="lede">Nothing here can hurt your computer — the kitchen lives in
        its own sealed box.</p>
      <p class="hint">Good moment for a coffee.</p>
      <div class="bar"><span data-field="fraction"></span></div>
      <span class="overline" data-field="counter"></span>
    </div>
    <ol class="rows" data-rows></ol>
  </section>
</template>

<template data-screen="install:reboot">
  <section class="install">
    <div class="install-left">
      <div class="pan pan-asleep" aria-hidden="true"></div>
      <h2 class="title">Time for a quick nap</h2>
      <p class="lede">Your computer needs one restart before the kitchen can open.
        We'll pick up exactly where we left off — nothing is lost.</p>
      <button class="btn-primary" data-action="reboot-now">Restart now</button>
      <button class="btn-secondary" data-action="go-home">I'll restart later</button>
    </div>
    <ol class="rows" data-rows></ol>
  </section>
</template>

<template data-screen="install:failed">
  <section class="install">
    <div class="install-left">
      <div class="pan pan-burnt" aria-hidden="true"></div>
      <h2 class="title">The burner wouldn't light</h2>
      <p class="lede" data-field="action"></p>
      <button class="btn-primary" data-retry data-action="start-install">Try this step again</button>
      <button class="btn-secondary" data-action="start-over">Start over from scratch</button>
    </div>
    <ol class="rows" data-rows></ol>
  </section>
</template>
```

In `app.js`, replace the placeholder `start-install` no-op and add the event handler:

```js
let rowsByName = {};

ACTIONS['start-install'] = async () => {
  const started = await api().start_install();
  rowsByName = {};
  show('install:running', { counter: '' });
  const list = document.querySelector('[data-rows]');
  started.rows.forEach((row) => {
    const li = document.createElement('li');
    li.className = 'row-step';
    li.dataset.status = 'waiting';
    li.innerHTML = '<span class="mark"></span><span class="label"></span><span class="state"></span>';
    li.querySelector('.label').textContent = row.label;
    li.querySelector('.state').textContent = 'Waiting';
    list.appendChild(li);
    rowsByName[row.name] = li;
  });
  // "Step N of M" comes from the list the factory returned, never a literal:
  // seven on Lima, nine on WSL2.
  window.eggie.total = started.rows.length;
  window.eggie.done = 0;
};

ACTIONS['reboot-now'] = () => api().reboot_now();

const STATE_WORDS = {
  running: 'Working…', done: 'Done', skipped: 'Done',
  failed: "Didn't work", reboot: 'Needs restart',
};

window.eggie.handlers.install = (event) => {
  if (event.type === 'step') {
    const li = rowsByName[event.step];
    if (li) {
      li.dataset.status = event.status;
      li.querySelector('.state').textContent = STATE_WORDS[event.status] || '';
    }
    if (event.status === 'done' || event.status === 'skipped') {
      window.eggie.done += 1;
      const counter = document.querySelector('[data-field="counter"]');
      if (counter) {
        counter.textContent = `Step ${window.eggie.done + 1} of ${window.eggie.total}`;
      }
    }
    if (event.fraction !== null && event.fraction !== undefined) {
      const bar = document.querySelector('[data-field="fraction"]');
      if (bar) bar.style.width = `${Math.round(event.fraction * 100)}%`;
    }
    return;
  }
  if (event.type === 'done') return refresh();
  if (event.type === 'reboot') return swap('install:reboot');
  if (event.type === 'failed' || event.type === 'dead_end') {
    swap('install:failed', { action: event.action || event.message });
    // DeadEnd means no code can fix it; a retry button there loops forever.
    if (event.type === 'dead_end') {
      document.querySelector('[data-retry]')?.remove();
    }
  }
};

// Swap the left column without rebuilding the row panel, so the rows keep the
// states the stream already put on them.
function swap(screen, data) {
  const rows = document.querySelector('[data-rows]');
  const keep = rows ? rows.cloneNode(true) : null;
  show(screen, data);
  if (keep) document.querySelector('[data-rows]').replaceWith(keep);
}

ACTIONS['start-over'] = async () => { await api().reset_install(); refresh(); };
```

Add `reset_install` to `host/desktop/api.py`:

```python
    def reset_install(self) -> dict:
        """Forget every recorded step so the next run starts from preflight."""
        self._state.clear()
        return {"ok": True}
```

Style `.install`, `.install-left`, `.pan*`, `.rows`, `.row-step`, `.mark`, `.bar` in `app.css` from the board: panel `--surface` with `1px solid var(--line)` at `--r-xl`; rows `13px 12px` at `--r-md`; the active row `background:var(--yolk-soft)` with a spinning `--yolk-deep` ring; done rows a `--basil` check on `--basil-soft`; waiting rows a `2px dashed var(--line-2)` circle; the failed row `--paprika-soft` with a `!` on `--paprika`.

- [ ] **Step 4: Run test to verify it passes**

Run: `TMPDIR=$(mktemp -d) python3 -m pytest tests/host/desktop/ -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add host/desktop/ui host/desktop/api.py tests/host/desktop/test_ui_assets.py
git commit -m "Add install running, restart and failed screens"
```

---

# Milestone 3 — Import, Ports, Doctor

Deliverable: the three utility screens work against a real VM.

---

### Task 10: Upload progress

**Files:**
- Modify: `host/client.py:261-285`
- Test: `tests/host/test_client_upload_progress.py`

**Interfaces:**
- Produces: `AgentClient.upload_directory(self, project_id, local_dir, *, on_progress=None) -> dict`. `on_progress(phase: str, done: int, total: int)` where phase is `"packing"` or `"sending"`. `total` is the file count while packing and the archive's byte size while sending.

- [ ] **Step 1: Write the failing test**

```python
# tests/host/test_client_upload_progress.py
"""Reporting the two phases an upload actually has.

upload_directory tars the tree to a temp file and then streams it. Both take
real time on a 412-file project, and today neither reports -- the board's bar
would sit at zero through the first and jump at the second.
"""
from __future__ import annotations

import io

from host.client import AgentClient


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def _client(opener):
    return AgentClient("token", opener=opener)


def test_both_phases_report(tmp_path):
    (tmp_path / "a.txt").write_text("a" * 1024)
    (tmp_path / "b.txt").write_text("b" * 1024)

    seen = []
    client = _client(lambda request, timeout=None: FakeResponse(b"{}"))
    client.upload_directory("proj", tmp_path,
                            on_progress=lambda phase, done, total: seen.append(phase))

    assert "packing" in seen
    assert "sending" in seen


def test_packing_counts_files_and_sending_counts_bytes(tmp_path):
    (tmp_path / "a.txt").write_text("a" * 4096)

    seen = []
    client = _client(lambda request, timeout=None: FakeResponse(b"{}"))
    client.upload_directory("proj", tmp_path,
                            on_progress=lambda *args: seen.append(args))

    packing = [s for s in seen if s[0] == "packing"]
    sending = [s for s in seen if s[0] == "sending"]
    assert packing and packing[-1][1] == packing[-1][2] == 1
    # The last sending event must reach the total, or the bar sticks short of
    # full and the screen never looks finished.
    assert sending and sending[-1][1] == sending[-1][2]


def test_upload_still_works_without_a_callback(tmp_path):
    (tmp_path / "a.txt").write_text("a")
    client = _client(lambda request, timeout=None: FakeResponse(b'{"ok": true}'))
    assert client.upload_directory("proj", tmp_path) == {"ok": True}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `TMPDIR=$(mktemp -d) python3 -m pytest tests/host/test_client_upload_progress.py -q`
Expected: FAIL — `TypeError: upload_directory() got an unexpected keyword argument 'on_progress'`

- [ ] **Step 3: Write minimal implementation**

Replace `upload_directory` in `host/client.py`:

```python
    def upload_directory(self, project_id: str, local_dir, *,
                         on_progress=None) -> dict:
        """Send the directory's contents as a raw tar.gz body (not multipart).

        Archived into a temp file rather than memory so a large project is
        never held twice, and streamed from there by urllib.

        `on_progress(phase, done, total)` is called for both phases the user
        waits through: "packing" counts top-level entries, "sending" counts
        bytes. A caller that passes nothing pays for nothing.
        """
        report = on_progress or (lambda phase, done, total: None)
        with tempfile.TemporaryFile() as archive:
            items = sorted(Path(local_dir).iterdir())
            with tarfile.open(fileobj=archive, mode="w:gz") as tar:
                for index, item in enumerate(items, 1):
                    tar.add(item, arcname=item.name, filter=_uploadable)
                    report("packing", index, len(items))
            size = archive.tell()

            def send():
                # Rewound per attempt: a retry after `project_busy` must send
                # the archive again, not the empty tail the last one left.
                archive.seek(0)
                body = _CountingReader(archive, size, report)
                with self._open("POST", f"/projects/{project_id}/files",
                                data=body,
                                headers={"Content-Type": "application/gzip",
                                         "Content-Length": str(size)},
                                timeout=UPLOAD_TIMEOUT) as response:
                    return response.read().decode("utf-8")

            body = self._while_busy(send)
        return json.loads(body) if body.strip() else {}


class _CountingReader:
    """Wraps the archive so urllib's read loop reports bytes as they leave.

    urllib calls read(blocksize) until it gets b"", so the count here is what
    was actually handed to the socket. The final call reports done == total
    explicitly: a bar that stops at 99.6% reads as a hang.
    """

    def __init__(self, stream, total: int, report):
        self._stream = stream
        self._total = total
        self._report = report
        self._done = 0

    def read(self, amount: int = -1) -> bytes:
        chunk = self._stream.read(amount)
        if chunk:
            self._done += len(chunk)
            self._report("sending", self._done, self._total)
        else:
            self._report("sending", self._total, self._total)
        return chunk
```

- [ ] **Step 4: Run test to verify it passes**

Run: `TMPDIR=$(mktemp -d) python3 -m pytest tests/host/test_client_upload_progress.py tests/ -q -k "client or upload"`
Expected: PASS, and no existing client test regresses.

- [ ] **Step 5: Commit**

```bash
git add host/client.py tests/host/test_client_upload_progress.py
git commit -m "Report packing and sending progress from upload_directory"
```

---

### Task 11: Folder inspection

**Files:**
- Modify: `host/desktop/view.py`, `host/desktop/api.py`
- Test: `tests/host/desktop/test_view_folder.py`

**Interfaces:**
- Produces:
  - `inspect_folder(path: Path) -> dict` → `{"name", "files", "bytes"}`, counting only what `client._uploadable` would send.
  - `DesktopApi.choose_folder() -> dict`, `DesktopApi.inspect_folder(path: str) -> dict` (the latter adds `"project_id"` and `"conflict"`).

- [ ] **Step 1: Write the failing test**

```python
# tests/host/desktop/test_view_folder.py
"""What the import screen says before anything is sent.

"412 files · 38 MB" has to describe what will actually travel, not what is on
disk: upload_directory filters through client._uploadable, and a count that
includes .git or node_modules would promise a transfer that never happens.
"""
from __future__ import annotations

from host.desktop.view import inspect_folder


def test_the_project_name_comes_from_the_folder(tmp_path):
    folder = tmp_path / "recipe-box"
    folder.mkdir()
    (folder / "a.txt").write_text("a")
    assert inspect_folder(folder)["name"] == "recipe-box"


def test_files_and_bytes_count_what_will_be_sent(tmp_path):
    folder = tmp_path / "p"
    folder.mkdir()
    (folder / "a.txt").write_text("x" * 100)
    (folder / "sub").mkdir()
    (folder / "sub" / "b.txt").write_text("y" * 50)

    summary = inspect_folder(folder)
    assert summary["files"] == 2
    assert summary["bytes"] == 150


def test_excluded_trees_are_not_counted(tmp_path):
    folder = tmp_path / "p"
    (folder / ".git").mkdir(parents=True)
    (folder / ".git" / "HEAD").write_text("ref: refs/heads/main")
    (folder / "node_modules" / "left-pad").mkdir(parents=True)
    (folder / "node_modules" / "left-pad" / "i.js").write_text("module.exports=1")
    (folder / "app.py").write_text("print(1)")

    summary = inspect_folder(folder)
    assert summary["files"] == 1
    assert summary["bytes"] == len("print(1)")


def test_an_empty_folder_is_reported_not_refused(tmp_path):
    # Import deliberately does not require a docker-compose.yml: the coding
    # agent writes one later. An empty folder is a real, allowed import.
    folder = tmp_path / "blank"
    folder.mkdir()
    assert inspect_folder(folder) == {"name": "blank", "files": 0, "bytes": 0}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `TMPDIR=$(mktemp -d) python3 -m pytest tests/host/desktop/test_view_folder.py -q`
Expected: FAIL — `ImportError: cannot import name 'inspect_folder'`

- [ ] **Step 3: Write minimal implementation**

`host/client.py` already exposes both exclusion sets module-level — `EXCLUDED_DIRS` (`.git`, `node_modules`, `.venv`, `__pycache__`, matched on any path component) and `EXCLUDED_FILES` (`.eggie/overlay.yml`). Import them rather than restating them; a second copy would drift from what `_uploadable` actually filters. Append to `host/desktop/view.py`:

```python
from pathlib import Path

from host.client import EXCLUDED_DIRS, EXCLUDED_FILES


def inspect_folder(path) -> dict:
    """Count what an import would actually send.

    Walks with the same two exclusion sets client._uploadable applies, so the
    "412 files · 38 MB" line describes the transfer rather than the disk.
    Symlinks are skipped rather than followed: following one could leave the
    tree or loop.
    """
    root = Path(path)
    files = 0
    total = 0
    stack = [root]
    while stack:
        current = stack.pop()
        for entry in current.iterdir():
            if entry.is_symlink():
                continue
            if entry.is_dir():
                if entry.name not in EXCLUDED_DIRS:
                    stack.append(entry)
                continue
            # EXCLUDED_FILES holds paths relative to the project root
            # (".eggie/overlay.yml"), so compare the same way the tar filter
            # sees them -- posix separators, relative to root.
            if entry.relative_to(root).as_posix() in EXCLUDED_FILES:
                continue
            files += 1
            total += entry.stat().st_size
    return {"name": root.name, "files": files, "bytes": total}
```

Add to `host/desktop/api.py`:

```python
    def choose_folder(self) -> dict:
        """Native folder picker. pywebview supplies it on both platforms."""
        import webview
        window = webview.windows[0]
        chosen = window.create_file_dialog(webview.FOLDER_DIALOG)
        if not chosen:
            return {"cancelled": True}
        return self.inspect_folder(chosen[0])

    def inspect_folder(self, path: str) -> dict:
        from host.client import AgentClient, project_id_for

        summary = inspect_folder(path)
        project_id = project_id_for(summary["name"])
        try:
            AgentClient.for_provider(self._provider).get_project(project_id)
            conflict = True
        except Exception:
            # Any refusal means "no project by that name to merge into". A
            # conflict banner shown because the agent was briefly unreachable
            # would offer Replace -- which deletes -- over nothing.
            conflict = False
        return {**summary, "path": path, "project_id": project_id,
                "conflict": conflict}
```

Add `from .view import inspect_folder, progress_event, route_for, rows_for, terminal_event` to the import line.

- [ ] **Step 4: Run test to verify it passes**

Run: `TMPDIR=$(mktemp -d) python3 -m pytest tests/host/desktop/test_view_folder.py tests/ -q -k "upload or uploadable or client"`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add host/desktop/view.py host/desktop/api.py host/client.py tests/host/desktop/test_view_folder.py
git commit -m "Add folder inspection for the import screen"
```

---

### Task 12: The import job

**Files:**
- Modify: `host/desktop/api.py`
- Test: `tests/host/desktop/test_api_import.py`

**Interfaces:**
- Produces: `DesktopApi.start_import(self, path: str, mode: str) -> dict` where mode is `"merge"` or `"replace"`.

- [ ] **Step 1: Write the failing test**

```python
# tests/host/desktop/test_api_import.py
"""Merge and replace, and the order replace does things in.

Replace is the only destructive action in the app. The board's warning says
"Anything in the existing project that isn't in your folder is gone for good".
That is only true if the delete happens first; the other order merges and then
wipes, which loses the import as well.
"""
from __future__ import annotations

from host.core.install import InstallState
from host.core.status import Readiness
from host.desktop.api import DesktopApi


class RecordingClient:
    def __init__(self):
        self.calls = []

    def delete_project(self, project_id):
        self.calls.append(("delete", project_id))

    def ensure_project(self, project_id, **kwargs):
        self.calls.append(("ensure", project_id))
        return {}

    def upload_directory(self, project_id, local_dir, *, on_progress=None):
        self.calls.append(("upload", project_id))
        if on_progress:
            on_progress("sending", 10, 10)
        return {}

    def get_project(self, project_id):
        return {}


def _api(tmp_path, client, pushed):
    return DesktopApi(object(), InstallState(tmp_path / "s.json"),
                      push=pushed.append, probe_fn=lambda p: Readiness(),
                      client_factory=lambda provider: client)


def test_merge_uploads_without_deleting(tmp_path):
    folder = tmp_path / "recipe-box"
    folder.mkdir()
    client, pushed = RecordingClient(), []
    api = _api(tmp_path, client, pushed)
    api.start_import(str(folder), "merge")
    api.jobs.join(timeout=5)

    assert [c[0] for c in client.calls] == ["ensure", "upload"]


def test_replace_deletes_before_it_uploads(tmp_path):
    folder = tmp_path / "recipe-box"
    folder.mkdir()
    client, pushed = RecordingClient(), []
    api = _api(tmp_path, client, pushed)
    api.start_import(str(folder), "replace")
    api.jobs.join(timeout=5)

    assert [c[0] for c in client.calls] == ["delete", "ensure", "upload"]


def test_an_unknown_mode_is_refused_rather_than_guessed(tmp_path):
    import pytest
    folder = tmp_path / "p"
    folder.mkdir()
    api = _api(tmp_path, RecordingClient(), [])
    # Guessing here could pick "replace" and delete a project.
    with pytest.raises(ValueError):
        api.start_import(str(folder), "obliterate")


def test_import_progress_reaches_the_screen(tmp_path):
    folder = tmp_path / "p"
    folder.mkdir()
    client, pushed = RecordingClient(), []
    api = _api(tmp_path, client, pushed)
    api.start_import(str(folder), "merge")
    api.jobs.join(timeout=5)

    assert any(e["type"] == "progress" for e in pushed)
    assert pushed[-1]["type"] == "done"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `TMPDIR=$(mktemp -d) python3 -m pytest tests/host/desktop/test_api_import.py -q`
Expected: FAIL — `TypeError: DesktopApi.__init__() got an unexpected keyword argument 'client_factory'`

- [ ] **Step 3: Write minimal implementation**

Add `client_factory=None` to `DesktopApi.__init__`, storing `self._client_factory = client_factory or (lambda provider: AgentClient.for_provider(provider))` (import `AgentClient` lazily inside the lambda to keep module import cheap), then:

```python
    IMPORT_MODES = ("merge", "replace")

    def start_import(self, path: str, mode: str) -> dict:
        from host.client import project_id_for
        from .view import inspect_folder

        if mode not in self.IMPORT_MODES:
            # Never guessed: one of the two modes deletes a project.
            raise ValueError(f"unknown import mode {mode!r}")

        summary = inspect_folder(path)
        project_id = project_id_for(summary["name"])
        client = self._client_factory(self._provider)

        def work(emit):
            if mode == "replace":
                # Delete first. The other order merges the folder in and then
                # wipes it, losing the import along with the old project.
                client.delete_project(project_id)
            client.ensure_project(project_id)

            def on_progress(phase, done, total):
                emit({"type": "progress", "phase": phase,
                      "done": done, "total": total})

            client.upload_directory(project_id, path, on_progress=on_progress)
            return {"type": "done", "project_id": project_id}

        return {"job": self.jobs.start("import", work),
                "project_id": project_id, **summary}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `TMPDIR=$(mktemp -d) python3 -m pytest tests/host/desktop/ -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add host/desktop/api.py tests/host/desktop/test_api_import.py
git commit -m "Add the import job with merge and replace"
```

---

### Task 13: Ports

**Files:**
- Modify: `host/desktop/view.py`, `host/desktop/api.py`
- Test: `tests/host/desktop/test_view_ports.py`

**Interfaces:**
- Produces:
  - `validate_port(guest: int, host_port: int, existing: list[tuple[int, int]]) -> str` — `""` when allowed, otherwise a reason identifier: `"range"`, `"duplicate"`, `"reserved"`.
  - `DesktopApi.list_ports() -> dict`, `.add_port(guest: int, host_port: int) -> dict`, `.remove_port(guest: int, host_port: int) -> dict`.

- [ ] **Step 1: Write the failing test**

```python
# tests/host/desktop/test_view_ports.py
"""What the ports table may accept.

The reserved pair is the one that matters: the providers forward 39099 and
39080 for themselves, and a user who takes 39099 silently severs the host from
the agent -- every screen afterwards reads as "can't reach the kitchen" with
no clue why.
"""
from __future__ import annotations

import pytest

from host.core import constants
from host.desktop.view import validate_port


def test_an_ordinary_pair_is_allowed():
    assert validate_port(3000, 3000, []) == ""


@pytest.mark.parametrize("guest, host_port", [(0, 3000), (3000, 0),
                                              (70000, 3000), (3000, 70000)])
def test_ports_outside_the_range_are_refused(guest, host_port):
    assert validate_port(guest, host_port, []) == "range"


def test_a_host_port_already_in_the_table_is_refused():
    assert validate_port(4000, 3000, [(3000, 3000)]) == "duplicate"


def test_the_same_host_port_is_refused_even_for_a_different_guest_port():
    # netsh keys its table by the listening port; a second rule would silently
    # replace the first rather than coexist.
    assert validate_port(9999, 3000, [(3000, 3000)]) == "duplicate"


def test_the_agent_port_may_not_be_taken():
    assert validate_port(1234, constants.AGENT_PORT, []) == "reserved"


def test_the_edge_port_may_not_be_taken():
    assert validate_port(1234, constants.EDGE_PORT, []) == "reserved"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `TMPDIR=$(mktemp -d) python3 -m pytest tests/host/desktop/test_view_ports.py -q`
Expected: FAIL — `ImportError: cannot import name 'validate_port'`

- [ ] **Step 3: Write minimal implementation**

Append to `host/desktop/view.py`:

```python
# Forwarded by the providers for their own use. A user who takes AGENT_PORT
# severs the host from the agent, and every screen afterwards reads as "can't
# reach the kitchen" with nothing pointing at the cause.
RESERVED_HOST_PORTS = frozenset({constants.AGENT_PORT, constants.EDGE_PORT})


def validate_port(guest: int, host_port: int,
                  existing: list[tuple[int, int]]) -> str:
    """`""` when the pair may be added, else why not."""
    if not (1 <= guest <= 65535 and 1 <= host_port <= 65535):
        return "range"
    if host_port in RESERVED_HOST_PORTS:
        return "reserved"
    # Keyed on the host port alone: netsh keys its table by the listening
    # port, so a second rule replaces the first instead of coexisting.
    if any(host_port == existing_host for _, existing_host in existing):
        return "duplicate"
    return ""
```

Add to `host/desktop/api.py`:

```python
    def list_ports(self) -> dict:
        return {"ports": [{"guest": guest, "host": host_port}
                          for guest, host_port in self._provider.forwards()]}

    def add_port(self, guest: int, host_port: int) -> dict:
        guest, host_port = int(guest), int(host_port)
        reason = validate_port(guest, host_port, self._provider.forwards())
        if reason:
            return {"ok": False, "reason": reason}
        self._provider.forward(guest, host_port)
        return {"ok": True}

    def remove_port(self, guest: int, host_port: int) -> dict:
        try:
            self._provider.unforward(int(guest), int(host_port))
        except Exception as e:
            # netsh writes to HKLM and needs administrator, so removal can
            # fail or be declined at the UAC prompt. The row says so rather
            # than disappearing as though it worked.
            return {"ok": False, "reason": "refused", "message": f"{e}"}
        return {"ok": True}
```

Add `validate_port` to the `from .view import ...` line.

- [ ] **Step 4: Run test to verify it passes**

Run: `TMPDIR=$(mktemp -d) python3 -m pytest tests/host/desktop/test_view_ports.py -q`
Expected: PASS — 8 passed

- [ ] **Step 5: Commit**

```bash
git add host/desktop/view.py host/desktop/api.py tests/host/desktop/test_view_ports.py
git commit -m "Add ports listing, validation and removal to the bridge"
```

---

### Task 14: Doctor, repair, VM lifecycle, uninstall

**Files:**
- Modify: `host/desktop/api.py`
- Test: `tests/host/desktop/test_api_actions.py`

**Interfaces:**
- Produces: `DesktopApi.doctor() -> dict`, `.start_repair() -> dict`, `.start_vm() -> dict`, `.stop_vm() -> dict`, `.restart_vm() -> dict`, `.start_uninstall(purge: bool) -> dict`.

- [ ] **Step 1: Write the failing test**

```python
# tests/host/desktop/test_api_actions.py
"""The remaining buttons, and the one that deletes things."""
from __future__ import annotations

from host.core.install import InstallState
from host.core.provider import CheckResult, Diagnosis
from host.core.status import Readiness
from host.desktop.api import DesktopApi


class FakeProvider:
    def __init__(self):
        self.started = self.stopped = False
        self.destroyed_with = None

    def preflight(self):
        return Diagnosis([CheckResult("Virtualization enabled", True),
                          CheckResult("WSL2 installed", False,
                                      fix="Run: wsl --install")])

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True


def _api(tmp_path, provider, pushed=None):
    return DesktopApi(provider, InstallState(tmp_path / "s.json"),
                      push=(pushed.append if pushed is not None else lambda e: None),
                      probe_fn=lambda p: Readiness())


def test_doctor_renders_the_diagnosis_for_the_log_pane(tmp_path):
    report = _api(tmp_path, FakeProvider()).doctor()
    assert report["ok"] is False
    assert "wsl --install" in report["text"]


def test_restart_stops_before_it_starts(tmp_path):
    order = []

    class Recording(FakeProvider):
        def stop(self): order.append("stop")
        def start(self): order.append("start")

    provider = Recording()
    api = _api(tmp_path, provider)
    api.restart_vm()
    api.jobs.join(timeout=5)
    assert order == ["stop", "start"]


def test_stopping_the_kitchen_is_a_job_not_a_blocking_call(tmp_path):
    # provider.stop() shells wsl.exe/limactl and takes seconds; running it on
    # the thread that owns the window freezes the app mid-click.
    provider = FakeProvider()
    api = _api(tmp_path, provider)
    assert "job" in api.stop_vm()
    api.jobs.join(timeout=5)
    assert provider.stopped is True


def test_uninstall_does_not_purge_unless_asked(tmp_path):
    seen = {}

    class Uninstallable(FakeProvider):
        def destroy(self):
            seen["destroyed"] = True

    api = _api(tmp_path, Uninstallable())
    api.start_uninstall(False)
    api.jobs.join(timeout=5)
    assert seen.get("destroyed") is True
    assert seen.get("purged") is not True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `TMPDIR=$(mktemp -d) python3 -m pytest tests/host/desktop/test_api_actions.py -q`
Expected: FAIL — `AttributeError: 'DesktopApi' object has no attribute 'doctor'`

- [ ] **Step 3: Write minimal implementation**

Add to `host/desktop/api.py`:

```python
    def doctor(self) -> dict:
        from host.core.diagnose import render_diagnosis

        diagnosis = self._provider.preflight()
        # Rendered by host/core so the modal shows exactly what `eggie doctor`
        # prints -- one wording for the user to read out to whoever helps them.
        return {"ok": diagnosis.ok, "text": render_diagnosis(diagnosis)}

    def start_vm(self) -> dict:
        return {"job": self.jobs.start(
            "vm", lambda emit: (self._provider.start(), {"type": "done"})[1])}

    def stop_vm(self) -> dict:
        return {"job": self.jobs.start(
            "vm", lambda emit: (self._provider.stop(), {"type": "done"})[1])}

    def restart_vm(self) -> dict:
        def work(emit):
            self._provider.stop()
            self._provider.start()
            return {"type": "done"}

        return {"job": self.jobs.start("vm", work)}

    def start_repair(self) -> dict:
        from host.core.install import _bootstrap, connect_step

        def work(emit):
            emit({"type": "stage", "stage": "bootstrap"})
            _bootstrap(self._provider, repair=True)
            emit({"type": "stage", "stage": "connect"})
            connect_step(self._provider)
            return {"type": "done"}

        return {"job": self.jobs.start("repair", work)}

    def start_uninstall(self, purge: bool) -> dict:
        def work(emit):
            self._provider.destroy()
            if purge:
                self._purge()
            self._state.clear()
            return {"type": "done"}

        return {"job": self.jobs.start("uninstall", work)}
```

`_purge` removes the install directory, mirroring `cli.uninstall --purge`. Read `host/cli.py:336-373` and reuse its logic rather than reimplementing it; if the deletion is more than a couple of lines there, lift it into a `host/core/install.py` helper both call, so the CLI and the app cannot drift on what "purge" removes.

- [ ] **Step 4: Run test to verify it passes**

Run: `TMPDIR=$(mktemp -d) python3 -m pytest tests/host/desktop/ -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add host/desktop/api.py tests/host/desktop/test_api_actions.py
git commit -m "Add doctor, repair, VM lifecycle and uninstall to the bridge"
```

---

### Task 15: Import, Ports and Doctor screens

**Files:**
- Modify: `host/desktop/ui/index.html`, `host/desktop/ui/app.js`, `host/desktop/ui/app.css`
- Test: extend `tests/host/desktop/test_ui_assets.py`

- [ ] **Step 1: Write the failing test**

```python
def test_the_utility_screens_have_templates():
    markup = (UI / "index.html").read_text()
    for screen in ("import", "import:progress", "ports", "doctor",
                   "uninstall-confirm"):
        assert f'data-screen="{screen}"' in markup, f"no template for {screen}"


def test_purge_is_not_preselected():
    # Uninstall removes the VM; purge additionally deletes everything on disk.
    markup = (UI / "index.html").read_text()
    purge = markup.index('name="purge"')
    assert "checked" not in markup[purge:purge + 120]


def test_replace_is_not_the_preselected_import_mode():
    # Replace deletes files with no undo. The board preselects Merge; a
    # stray Enter on this screen must not wipe a project.
    markup = (UI / "index.html").read_text()
    merge = markup.index('value="merge"')
    replace = markup.index('value="replace"')
    assert "checked" in markup[merge:merge + 120]
    assert "checked" not in markup[replace:replace + 120]


def test_the_ports_table_has_no_project_column():
    # Providers store (guest, host) pairs only; nothing records an owner, and
    # a column of empty cells is worse than three true ones.
    markup = (UI / "index.html").read_text()
    assert "Project" not in markup
```

- [ ] **Step 2: Run test to verify it fails**

Run: `TMPDIR=$(mktemp -d) python3 -m pytest tests/host/desktop/test_ui_assets.py -q`
Expected: FAIL — `no template for import`

- [ ] **Step 3: Write minimal implementation**

Add four templates to `index.html`, with the board's copy verbatim. Key fragments:

```html
<template data-screen="import">
  <section class="pad">
    <h2 class="title">Bring in a folder</h2>
    <p class="lede">Copy a folder from your computer into the kitchen. The
      original stays exactly where it is.</p>
    <div class="field">
      <span class="overline">Folder</span>
      <div class="picker">
        <span class="mono" data-field="path"></span>
        <button class="btn-secondary" data-action="choose-folder">Choose…</button>
      </div>
    </div>
    <div class="field">
      <span class="overline">Project name</span>
      <div class="derived"><span data-field="name"></span>
        <span class="hint">from folder name</span></div>
    </div>
    <fieldset data-conflict hidden>
      <legend class="overline">A project with this name already exists in here</legend>
      <label class="choice"><input type="radio" name="mode" value="merge" checked>
        <b>Merge into it</b>
        <span>Adds the new files and updates ones with the same name.
          Everything else stays put.</span></label>
      <label class="choice"><input type="radio" name="mode" value="replace">
        <b>Replace it</b>
        <span>Empties the project first, then copies your folder in.</span></label>
      <p class="warn"><b>Replace deletes files.</b> Anything in the existing
        project that isn't in your folder is gone for good — including work the
        cook did in there. There's no undo.</p>
    </fieldset>
    <div class="row">
      <button class="btn-primary" data-action="do-import">Bring it in</button>
      <button class="btn-secondary" data-action="go-home">Cancel</button>
      <span class="hint" data-field="summary"></span>
    </div>
  </section>
</template>

<template data-screen="import:progress">
  <section class="pad centered">
    <div class="pan pan-carry" aria-hidden="true"></div>
    <h2 class="title">Carrying it in…</h2>
    <p class="lede">Copying <b data-field="name"></b> into the kitchen. Your
      original folder isn't being touched.</p>
    <div class="bar"><span data-field="fraction"></span></div>
    <span class="hint" data-field="counter"></span>
  </section>
</template>

<template data-screen="ports">
  <section class="pad">
    <h2 class="title">Serving hatches</h2>
    <p class="lede">Each one lets something inside the kitchen show up in your
      browser at a normal web address.</p>
    <button class="btn-primary" data-action="add-port-row">Add a port</button>
    <table class="hatches">
      <thead><tr><th>On this computer</th><th>Inside the kitchen</th><th></th></tr></thead>
      <tbody data-ports></tbody>
    </table>
    <p class="note">Only you can reach these. Nothing is published to the internet.</p>
    <div class="row"><button class="btn-secondary" data-action="go-home">Done</button></div>
  </section>
</template>

<template data-screen="doctor">
  <section class="pad">
    <h2 class="title">Doctor</h2>
    <pre class="log" data-field="text"></pre>
    <div class="row"><button class="btn-secondary" data-action="go-home">Done</button></div>
  </section>
</template>

<template data-screen="uninstall-confirm">
  <section class="pad">
    <h2 class="title">Close the kitchen for good?</h2>
    <p class="lede">This removes the Linux machine and everything inside it.
      Your own files, outside the kitchen, are untouched.</p>
    <label class="choice"><input type="checkbox" name="purge">
      <b>Also delete downloads and settings</b>
      <span>Frees the disk space Eggie used. You'd start from scratch next time.</span></label>
    <p class="warn"><b>There's no undo.</b> Any project the cook made in there
      goes with it.</p>
    <div class="row">
      <button class="btn-danger" data-action="do-uninstall">Remove Eggie</button>
      <button class="btn-secondary" data-action="go-home">Keep it</button>
    </div>
  </section>
</template>
```

```js
ACTIONS['do-uninstall'] = async () => {
  const purge = document.querySelector('input[name="purge"]').checked;
  await api().start_uninstall(purge);
};
```

In `app.js`, replace the remaining no-ops:

```js
let pending = null;

ACTIONS['import'] = () => show('import', { path: '', name: '', summary: '' });

ACTIONS['choose-folder'] = async () => {
  const chosen = await api().choose_folder();
  if (chosen.cancelled) return;
  pending = chosen;
  const megabytes = (chosen.bytes / 1e6).toFixed(1);
  fill(document.getElementById('screen'), {
    path: chosen.path, name: chosen.name,
    summary: `${chosen.files} files · ${megabytes} MB`,
  });
  document.querySelector('[data-conflict]').hidden = !chosen.conflict;
};

ACTIONS['do-import'] = async () => {
  if (!pending) return;
  // No conflict means there is nothing to merge into or replace, so the
  // radios are hidden and merge is the only meaning.
  const picked = document.querySelector('input[name="mode"]:checked');
  const mode = pending.conflict && picked ? picked.value : 'merge';
  const started = await api().start_import(pending.path, mode);
  show('import:progress', { name: started.name, counter: '' });
};

window.eggie.handlers.import = (event) => {
  if (event.type === 'progress') {
    const percent = event.total ? Math.round((event.done / event.total) * 100) : 0;
    const bar = document.querySelector('[data-field="fraction"]');
    if (bar) bar.style.width = `${percent}%`;
    const counter = document.querySelector('[data-field="counter"]');
    if (counter) {
      counter.textContent = event.phase === 'packing'
        ? `Packing ${event.done} of ${event.total} files`
        : `${percent}% copied`;
    }
    return;
  }
  if (event.type === 'done' || event.type === 'crashed') refresh();
};

ACTIONS['ports'] = async () => {
  const listed = await api().list_ports();
  show('ports', {});
  const body = document.querySelector('[data-ports]');
  listed.ports.forEach((port) => body.appendChild(portRow(port)));
};

function portRow(port) {
  const tr = document.createElement('tr');
  tr.innerHTML = '<td class="mono"></td><td class="mono"></td>'
    + '<td><button class="btn-remove">Remove</button></td>';
  tr.children[0].textContent = `localhost:${port.host}`;
  tr.children[1].textContent = `vm:${port.guest}`;
  tr.querySelector('button').addEventListener('click', async () => {
    const result = await api().remove_port(port.guest, port.host);
    if (result.ok) return tr.remove();
    // netsh needs administrator. Say so in the row rather than pretending.
    tr.dataset.error = 'refused';
    tr.children[2].textContent = 'Could not remove — needs administrator';
  });
  return tr;
}

const PORT_REFUSALS = {
  range: 'Ports must be between 1 and 65535.',
  duplicate: 'That port on this computer is already in use by another hatch.',
  reserved: 'Eggie needs that port for itself. Pick another.',
};

ACTIONS['doctor'] = async () => show('doctor', await api().doctor());
ACTIONS['repair'] = async () => { await api().start_repair(); };
ACTIONS['start-vm'] = async () => { await api().start_vm(); };
ACTIONS['stop-vm'] = async () => { await api().stop_vm(); };
ACTIONS['restart-vm'] = async () => { await api().restart_vm(); };

window.eggie.handlers.vm = (event) => { if (event.type !== 'progress') refresh(); };
window.eggie.handlers.repair = (event) => { if (event.type !== 'progress') refresh(); };
window.eggie.handlers.uninstall = () => refresh();
```

`add-port-row` appends an editable row whose Save calls `api().add_port(...)` and, on `{ok: false}`, renders `PORT_REFUSALS[result.reason]` under the row in `--paprika`.

- [ ] **Step 4: Run test to verify it passes**

Run: `TMPDIR=$(mktemp -d) python3 -m pytest tests/host/desktop/ -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add host/desktop/ui tests/host/desktop/test_ui_assets.py
git commit -m "Add import, ports and doctor screens"
```

---

### Task 16: Relax `eggie up`'s compose guard

**Files:**
- Modify: `host/cli.py:164-180`
- Test: `tests/test_up_cli.py` (find the existing test covering the guard first: `grep -rn "no docker-compose" tests/`)

**Interfaces:**
- Produces: `eggie up` no longer refuses a folder without `docker-compose.yml`; it uploads and reports the agent's refusal as "imported, nothing to start yet".

- [ ] **Step 1: Write the failing test**

```python
def test_up_without_a_compose_file_imports_and_says_so(tmp_path, capsys, monkeypatch):
    """The folder still lands in the VM; the coding agent writes the compose
    file later. Refusing here would refuse exactly the folders Import exists
    for."""
    from host.client import AgentError

    uploaded = []

    class FakeClient:
        def ensure_project(self, project_id, **kwargs):
            return {}

        def upload_directory(self, project_id, local_dir, **kwargs):
            uploaded.append(project_id)
            return {}

        def project_up(self, project_id):
            raise AgentError("no_compose", "no docker-compose.yml in project", 400)

    monkeypatch.setattr("host.cli._client", lambda: FakeClient())
    (tmp_path / "README.md").write_text("hello")

    from host.cli import up
    import typer
    try:
        up(str(tmp_path))
    except typer.Exit as exit_:
        code = exit_.exit_code
    else:
        code = 0

    out = capsys.readouterr()
    assert uploaded, "the folder should still have been imported"
    assert "nothing to start" in (out.out + out.err).lower()
    assert code == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `TMPDIR=$(mktemp -d) python3 -m pytest tests/test_up_cli.py -q -k compose`
Expected: FAIL — the guard exits 1 before uploading.

- [ ] **Step 3: Write minimal implementation**

In `host/cli.py::up`, delete the `if not (local / COMPOSE_FILE).is_file():` block and its `COMPOSE_FILE` import, then wrap `project_up` so the agent's refusal reads as an outcome rather than an error:

```python
        client.ensure_project(project_id)
        client.upload_directory(project_id, local)
        if not (local / "docker-compose.yml").is_file():
            # No longer a refusal: a folder without one is a real import, and
            # the coding agent writes the compose file later. The upload above
            # already happened, so say what did happen rather than erroring.
            typer.echo(f"{project_id} was imported. There is no "
                       f"docker-compose.yml yet, so there is nothing to start.")
            return
        typer.echo(f"Starting {project_id} in the VM…")
```

Delete or update whichever existing test asserted the old refusal.

- [ ] **Step 4: Run test to verify it passes**

Run: `TMPDIR=$(mktemp -d) python3 -m pytest tests/ -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add host/cli.py tests/test_up_cli.py
git commit -m "Let eggie up import a folder without a compose file"
```

---

# Milestone 4 — Retire tkinter, package, document

Deliverable: the frozen binary on both platforms opens the new app; `host/setup_app/` is gone.

---

### Task 17: Retire `host/setup_app/`

**Files:**
- Delete: `host/setup_app/`, `tests/host/test_setup_app_logic.py`
- Modify: `host/cli.py:263-290`
- Test: `tests/host/desktop/test_no_tkinter.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/host/desktop/test_no_tkinter.py
"""tkinter is gone from the host.

Not tidiness: the macOS build required a tkinter-capable Python only because
of setup_app, and packaging/macos/build.sh still checks for one. An import
left behind would keep that requirement alive invisibly.
"""
from __future__ import annotations

import ast
from pathlib import Path

HOST = Path(__file__).resolve().parents[3] / "host"


def test_no_host_module_imports_tkinter():
    files = sorted(HOST.rglob("*.py"))
    # A cwd-relative Path("host") passes vacuously from another directory;
    # this has bitten the repo three times.
    assert files, f"no host modules found under {HOST}"

    offenders = []
    for py in files:
        for node in ast.walk(ast.parse(py.read_text())):
            names = ([a.name for a in node.names] if isinstance(node, ast.Import)
                     else [node.module or ""] if isinstance(node, ast.ImportFrom)
                     and node.level == 0 else [])
            if any(n == "tkinter" or n.startswith("tkinter.") for n in names):
                offenders.append(f"{py}:{node.lineno}")
    assert not offenders, f"host/ imported tkinter: {offenders}"


def test_setup_app_is_gone():
    assert not (HOST / "setup_app").exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `TMPDIR=$(mktemp -d) python3 -m pytest tests/host/desktop/test_no_tkinter.py -q`
Expected: FAIL — `host/ imported tkinter: [...setup_app/app.py:12, ...]`

- [ ] **Step 3: Write minimal implementation**

```bash
git rm -r host/setup_app tests/host/test_setup_app_logic.py
```

In `host/cli.py::setup`, replace the tkinter branch:

```python
    if not headless:
        from host.desktop.__main__ import run
        provider.resume = resume
        raise typer.Exit(code=run(provider, state))
```

`run` builds its own `DesktopApi`, so `build_steps` must reach it. Pass it through: give `run` a `steps_factory=None` parameter forwarded to `DesktopApi(..., steps_factory=steps_factory)`, and have `cli.setup` call `run(provider, state, steps_factory=build_steps)`. Update `__main__.main()` to build the same factory (it already has `provider`, `root` and the constants `cli.setup` uses) so launching the app directly and launching it through `eggie setup` produce identical step lists.

- [ ] **Step 4: Run test to verify it passes**

Run: `TMPDIR=$(mktemp -d) python3 -m pytest tests/ -q`
Expected: PASS. The suite should now be ~530 tests minus the deleted setup_app ones, plus the new desktop ones, with **no** tkinter-related failures in this sandbox.

- [ ] **Step 5: Commit**

```bash
git add -A host tests
git commit -m "Retire the tkinter setup app"
```

---

### Task 18: Dependencies and packaging

**Files:**
- Modify: `pyproject.toml`, `packaging/windows/eggie.spec`, `packaging/windows/installer.iss`, `packaging/windows/build.ps1`, `packaging/macos/eggie.spec`, `packaging/macos/setup_main.py`, `packaging/macos/build.sh`
- Test: extend `tests/host/test_host_dependencies.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/host/test_host_dependencies.py`:

```python
def test_the_webview_backends_are_platform_scoped():
    """pythonnet is Windows-only and pyobjc is macOS-only.

    Declared unconditionally, each would be installed and frozen into the
    other platform's binary, where it cannot even import.
    """
    import tomllib
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    deps = tomllib.loads((root / "pyproject.toml").read_text())["project"]["dependencies"]
    by_name = {d.split(";")[0].split(">")[0].split("=")[0].strip().lower(): d
               for d in deps}

    assert "pywebview" in by_name
    assert "sys_platform" in by_name["pythonnet"]
    assert "sys_platform" in by_name["pyobjc-core"]


def test_the_ui_assets_are_bundled_by_both_specs():
    """A spec that freezes the code but not host/desktop/ui produces a binary
    that opens a window onto a missing file."""
    from pathlib import Path

    packaging = Path(__file__).resolve().parents[2] / "packaging"
    for spec in (packaging / "windows" / "eggie.spec",
                 packaging / "macos" / "eggie.spec"):
        assert "host/desktop/ui" in spec.read_text().replace("\\", "/"), spec
```

- [ ] **Step 2: Run test to verify it fails**

Run: `TMPDIR=$(mktemp -d) python3 -m pytest tests/host/test_host_dependencies.py -q`
Expected: FAIL — `KeyError: 'pythonnet'`

- [ ] **Step 3: Write minimal implementation**

`pyproject.toml`:

```toml
# The host still parses no YAML and ships no web framework. pywebview is the
# window; its backend is the platform's own webview, and the two bindings
# below are how Python reaches each one. Scoped by marker so neither is
# frozen into the other platform's binary.
dependencies = [
    "typer>=0.12",
    "pywebview>=5.1",
    "pythonnet>=3.0; sys_platform == 'win32'",
    "pyobjc-core>=10.1; sys_platform == 'darwin'",
    "pyobjc-framework-Cocoa>=10.1; sys_platform == 'darwin'",
    "pyobjc-framework-WebKit>=10.1; sys_platform == 'darwin'",
]
```

Both `eggie.spec` files: add the UI tree to `datas` and the backend to `hiddenimports`:

```python
datas = [
    ("../../host/provision", "host/provision"),
    ("../../host/providers/eggie.yaml", "host/providers"),
    ("../../host/desktop/ui", "host/desktop/ui"),
]
hiddenimports = ["webview", "webview.platforms.edgechromium"]  # winforms on Windows
```

On macOS use `"webview.platforms.cocoa"`. Confirm the exact module names with `python3 -c "import webview.platforms; print(dir(webview.platforms))"` on each platform before committing; PyInstaller will not find them by static analysis.

`packaging/windows/installer.iss`: add the WebView2 bootstrapper to `[Files]` and `[Run]`:

```
[Files]
Source: "MicrosoftEdgeWebview2Setup.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall

[Run]
; Evergreen, and a no-op when the runtime is already present -- which it is on
; Windows 11 and on updated Windows 10. Silent so the user sees one installer.
Filename: "{tmp}\MicrosoftEdgeWebview2Setup.exe"; Parameters: "/silent /install"; \
  StatusMsg: "Checking the Edge WebView2 runtime..."; Flags: waituntilterminated
```

`packaging/windows/build.ps1`: download the bootstrapper from
`https://go.microsoft.com/fwlink/p/?LinkId=2124703` into `packaging/windows/` before running ISCC.

`packaging/macos/build.sh`: delete the tkinter capability check. `packaging/macos/setup_main.py`: change the entrypoint import from `host.setup_app` to `host.desktop.__main__`.

- [ ] **Step 4: Run test to verify it passes**

Run: `TMPDIR=$(mktemp -d) python3 -m pytest tests/ -q`
Expected: PASS

- [ ] **Step 5: Verify a frozen build**

On Windows: `powershell packaging/windows/build.ps1`, then run the produced installer on a clean VM and confirm the app opens. On macOS: `bash packaging/macos/build.sh`, install the pkg, open Eggie. Both are out of reach from the WSL dev shell — record the result.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml packaging tests/host/test_host_dependencies.py
git commit -m "Package the desktop app on both platforms"
```

---

### Task 19: Correct the docs

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Find every sentence the work falsified**

Run:

```bash
grep -n "typer\|tkinter\|setup_app\|Host runtime deps" CLAUDE.md
```

- [ ] **Step 2: Make the edits**

Three claims are now false and must change:

1. "The host's only runtime dependency is `typer`" → name `pywebview` and the two platform-scoped backends, and keep the reason the rule exists (the host is PyInstaller-frozen, so every declared dependency ships).
2. "The mac build needs a Python 3.12+ with tkinter" → the tkinter requirement is gone; the mac build is still native-arch only.
3. The sandbox-failures note listing `tests/host/test_setup_app_logic.py` → that file no longer exists; only the two `selfcheck` tests in `tests/test_setup_cli.py` still fail here.

Add one line to the architecture section recording that `host/desktop/` is the GUI and that `host/desktop/view.py` is where its logic lives, so the next reader does not go looking in `api.py`.

- [ ] **Step 3: Verify nothing else went stale**

Run: `TMPDIR=$(mktemp -d) python3 -m pytest -q`
Expected: full suite passes apart from the two known `selfcheck` sandbox failures.

- [ ] **Step 4: Commit and open the PR**

```bash
git add CLAUDE.md
git commit -m "Update CLAUDE.md for the desktop app"
git push -u origin feature/desktop-app
gh pr create --base main --title "Desktop app" --body "..."
```

Per the repo's git rules, run a code review by a separate agent after the PR is created.

---

## Self-Review

**Spec coverage** — every section maps to at least one task:

| Spec section | Tasks |
|---|---|
| 1. What this replaces | 17 |
| 2. Stack (pywebview, native chrome, OS theme) | 4, 5 |
| 3. Module layout, bridge, CSP, `_MEIPASS` | 1–5 |
| 4. Routes and readiness table | 1, 3, 5 |
| 5. Install flow, platform step counts, restart | 6, 7, 8, 9 |
| 6. Import / Ports / Doctor / repair / uninstall | 10–15 |
| `eggie up` consequence | 16 |
| 7. Deliberately inert (updates, project column) | 5 (`app_version` only), 15 (no Project column) |
| 8. Testing table | 1, 2, 5, 6, 7, 8, 12, 13 |
| 9. Packaging | 18 |
| 10. Risks (WebView2 fallback, accessibility, reduced motion) | 4, 5 |

**Gap found and closed inline:** the spec's "Check for updates" tile and the board's whole "Odds and ends" grid had no task — Home's templates rendered a version string and nothing else, so Import, Ports, Doctor, Uninstall and the update tile had no way in. Task 5 now carries the grid, the `updates-unavailable` template and its test; Task 15 now carries the `uninstall-confirm` template and a test that purge is not preselected.

**Placeholder scan:** no "TBD", "add error handling", or "similar to Task N". Two steps intentionally say *reuse the existing code* rather than reproducing it — Task 14 (`cli.uninstall`'s purge) and Task 16 (the existing guard test) — because copying those literals into the plan is exactly how the CLI and the app drift apart.

**Type consistency:** `route_for` → `(route, state)` used identically in Tasks 1, 3, 5. `rows_for` → `{"name", "label", "progress"}` produced in Task 6, consumed in Tasks 7 and 9. `emit(event: dict)` is the same callable in Tasks 2, 7, 12, 14. `on_progress(phase, done, total)` is the same signature in Tasks 10, 12, 15. `validate_port` → `""`/`"range"`/`"duplicate"`/`"reserved"` produced in Task 13, consumed by `PORT_REFUSALS` in Task 15 — all four keys present.
