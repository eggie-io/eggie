# Projects console inside the desktop app — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The desktop window shows the in-VM projects console itself instead of sending the user to a browser, with host-only controls behind a native "Eggie" menu.

**Architecture:** One pywebview window. It loads the local desktop UI first and navigates to `http://localhost:39080/#handoff=<code>` with `window.load_url` when the machine is running. A new `host/desktop/shell.py` owns the window: it remembers which URL is the local UI, and every bridge call and pushed event is refused unless that page is showing. JS gets a guarded facade over `DesktopApi`, never `DesktopApi` itself.

**Tech Stack:** Python 3.12, pywebview ≥ 5.1 (`webview.menu.Menu`/`MenuAction`, `window.load_url`, `window.get_current_url`), plain JS in `host/desktop/ui/app.js`, pytest.

**Spec:** `docs/superpowers/specs/2026-09-23-desktop-embedded-console-design.md`

## Global Constraints

- Host-only change: nothing under `runtime/` changes; no runtime release.
- `host/` never imports `eggie_api`; no `sys.platform`/`platform.system()`/`os.name` outside `host/providers/`.
- No new host dependency (`tests/host/test_host_dependencies.py`). `webview` is imported lazily inside functions only; the dev environment does not have it installed, so no test may import it.
- The console URL is `http://localhost:{constants.EDGE_PORT}` — the edge port, never the API port.
- The console is never loaded inside the app without a handoff code.
- `private_mode` stays at pywebview's default (on); `start(debug=False)` stays.
- Comments: only for non-obvious edge cases; no mention of issues, tickets or docs.
- Tests only where a wrong result is plausible (see the user's testing rules); no test of pywebview itself.
- Run tests with `TMPDIR=<writable dir>` prefix in this WSL sandbox, e.g. `TMPDIR=$PWD/.tmp python3 -m pytest ...` (create `.tmp` first; it is git-ignored or delete it after).

## Review Focus

1. A console page calls a bridge method **before** the local UI ever called one (the first `get_current_url()` the shell sees is already the console) — must be refused, not recorded as "ours". Pinned in Task 1 (`test_a_first_call_from_the_console_is_refused`).
2. *Machine* chosen from the menu after the console loaded must return to the **original** local URL, not reload the console. Pinned in Task 1.
3. *Machine* reloads the local UI, which re-probes and sees "running" — must **not** bounce back into the console. Pinned in Task 2 (flag is one-shot).
4. A handoff that fails (stopped VM, old API) must leave the window where it is, not load a bare console. Pinned in Task 2.
5. A JS call to a misspelled bridge method (`api().enter_consol()`) rejects only at runtime inside a window nobody tests. Pinned in Task 4 (every `api().X(` in `app.js` is a public `DesktopApi` method).

---

### Task 1: `Shell` — which page is ours, the guarded bridge, the guarded push

**Files:**
- Create: `host/desktop/shell.py`
- Modify: `host/desktop/jobs.py` (add `JobRegistry.running()`)
- Test: `tests/host/desktop/test_shell.py`

**Interfaces:**
- Produces:
  - `class NotLocalPage(PermissionError)`
  - `class Shell` — `Shell(window=None)`; attribute `window`; `is_local() -> bool`; `load(url: str) -> None`; `load_local() -> None`; `push(event: dict) -> None`
  - `public_methods(cls: type) -> list[str]`
  - `guarded(api: object, shell: Shell) -> object` — an object exposing exactly `public_methods(type(api))`, each refusing with `NotLocalPage` unless `shell.is_local()`
  - `JobRegistry.running() -> bool`

- [ ] **Step 1: Write the failing tests**

`tests/host/desktop/test_shell.py`:

```python
"""The bridge belongs to the local UI, not to whatever page is showing.

pywebview injects window.pywebview.api into every page the window loads,
including the console served from inside the VM, where coding agents run as
root. These pin that such a page can see the bridge and use none of it.
"""
from __future__ import annotations

import json

import pytest

from host.core.install import InstallState
from host.desktop.api import DesktopApi
from host.desktop.shell import NotLocalPage, Shell, guarded, public_methods

LOCAL = "file:///opt/eggie/host/desktop/ui/index.html"
CONSOLE = "http://localhost:39080/#handoff=abc"


class FakeWindow:
    def __init__(self, url):
        self.url = url
        self.loaded = []
        self.evaluated = []

    def get_current_url(self):
        return self.url

    def load_url(self, url):
        self.loaded.append(url)
        self.url = url

    def evaluate_js(self, script):
        self.evaluated.append(script)


class FakeProvider:
    pass


def _api(tmp_path):
    return DesktopApi(FakeProvider(), InstallState(tmp_path / "s.json"),
                      push=lambda event: None)


def test_the_page_the_window_opened_with_is_local():
    assert Shell(FakeWindow(LOCAL)).is_local() is True


def test_a_first_call_from_the_console_is_refused():
    # Nothing asked is_local() while the local UI showed; the first question
    # arrives from the console. load() must have remembered the local URL on
    # the way out, or the console would be recorded as ours.
    shell = Shell(FakeWindow(LOCAL))
    shell.load(CONSOLE)
    assert shell.is_local() is False


def test_the_fragment_does_not_make_the_local_page_foreign():
    window = FakeWindow(LOCAL)
    shell = Shell(window)
    assert shell.is_local()
    window.url = LOCAL + "#install"
    assert shell.is_local() is True


def test_load_local_returns_to_the_original_page_not_the_console():
    window = FakeWindow(LOCAL)
    shell = Shell(window)
    shell.load(CONSOLE)
    shell.load_local()
    assert window.url == LOCAL
    assert shell.is_local() is True


def test_events_reach_the_local_page_as_one_json_argument():
    window = FakeWindow(LOCAL)
    Shell(window).push({"kind": "vm", "message": 'a "quote"'})
    assert window.evaluated == [
        f"window.eggie.on({json.dumps({'kind': 'vm', 'message': 'a \"quote\"'})})"]


def test_events_never_reach_the_console():
    # A console page can define its own window.eggie.on and read job events.
    window = FakeWindow(LOCAL)
    shell = Shell(window)
    shell.load(CONSOLE)
    shell.push({"kind": "import", "type": "progress"})
    assert window.evaluated == []


def test_every_public_method_is_refused_from_the_console(tmp_path):
    shell = Shell(FakeWindow(LOCAL))
    bridge = guarded(_api(tmp_path), shell)
    shell.load(CONSOLE)
    names = public_methods(DesktopApi)
    assert "reboot_now" in names and "start_import" in names
    for name in names:
        with pytest.raises(NotLocalPage):
            getattr(bridge, name)()


def test_the_bridge_exposes_only_the_public_methods(tmp_path):
    # JobRegistry sits on DesktopApi as .jobs; pywebview exposes nested
    # objects, so the facade must not carry it (or anything else) along.
    bridge = guarded(_api(tmp_path), Shell(FakeWindow(LOCAL)))
    exposed = {n for n in dir(bridge) if not n.startswith("_")}
    assert exposed == set(public_methods(DesktopApi))


def test_the_local_page_reaches_the_real_method(tmp_path):
    bridge = guarded(_api(tmp_path), Shell(FakeWindow(LOCAL)))
    assert bridge.reset_install() == {"ok": True}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `TMPDIR=$PWD/.tmp python3 -m pytest tests/host/desktop/test_shell.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'host.desktop.shell'`

- [ ] **Step 3: Write `host/desktop/shell.py`**

```python
"""The one window, and which page in it is ours.

pywebview injects `window.pywebview.api` into every page the window loads.
Once the projects console (served from inside the VM) is showing, anything
running in the VM can see the bridge. `guarded()` is what JS receives, and it
refuses every call unless the local UI is the page on screen.
"""
from __future__ import annotations

import functools
import json
from typing import Any
from urllib.parse import urldefrag


class NotLocalPage(PermissionError):
    """A bridge call arrived while a page other than the local UI showed."""


class Shell:
    def __init__(self, window: Any = None):
        self.window = window
        self._local: str | None = None

    def _current(self) -> str | None:
        if self.window is None:
            return None
        url = self.window.get_current_url()
        return urldefrag(url)[0] if url else None

    def _remember(self) -> None:
        # Until the first load() the window has only ever shown the local UI,
        # so the first URL seen is ours. load() calls this before leaving.
        if self._local is None:
            self._local = self._current()

    def is_local(self) -> bool:
        self._remember()
        current = self._current()
        return current is not None and current == self._local

    def load(self, url: str) -> None:
        self._remember()
        self.window.load_url(url)

    def load_local(self) -> None:
        self._remember()
        if self._local is not None:
            self.window.load_url(self._local)

    def push(self, event: dict) -> None:
        # json.dumps, never a format string: a message carrying a quote would
        # otherwise close the call and inject whatever followed.
        if self.window is not None and self.is_local():
            self.window.evaluate_js(f"window.eggie.on({json.dumps(event)})")


def public_methods(cls: type) -> list[str]:
    return sorted(name for name, value in vars(cls).items()
                  if callable(value) and not name.startswith("_"))


def guarded(api: object, shell: Shell) -> object:
    bridge = type("DesktopBridge", (), {})()
    for name in public_methods(type(api)):
        setattr(bridge, name, _guard(getattr(api, name), shell))
    return bridge


def _guard(method, shell: Shell):
    @functools.wraps(method)
    def call(*args, **kwargs):
        if not shell.is_local():
            raise NotLocalPage(f"{method.__name__} is only available to Eggie's own screens")
        return method(*args, **kwargs)
    return call
```

- [ ] **Step 4: Add `running()` to `JobRegistry`** in `host/desktop/jobs.py`, after `start()`:

```python
    def running(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `TMPDIR=$PWD/.tmp python3 -m pytest tests/host/desktop -q`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add host/desktop/shell.py host/desktop/jobs.py tests/host/desktop/test_shell.py
git commit -m "Desktop: guard the bridge to the local UI page"
```

---

### Task 2: `DesktopApi.enter_console()` and the one-shot launch flag

**Files:**
- Modify: `host/desktop/api.py` (constructor, `home()`, new `enter_console()`)
- Test: `tests/host/desktop/test_api_console.py`

**Interfaces:**
- Consumes: `JobRegistry.running()` (Task 1)
- Produces:
  - `DesktopApi(..., navigate: Callable[[str], None] | None = None)` — called with the console URL
  - `DesktopApi.enter_console() -> dict` — `{"ok": True}` or `{"ok": False, "message": str}`
  - `home()` gains key `"enter_console": bool`

- [ ] **Step 1: Write the failing tests**

`tests/host/desktop/test_api_console.py`:

```python
"""Entering the projects console inside the window."""
from __future__ import annotations

import threading

from host.core import constants
from host.core.install import InstallState
from host.core.status import Readiness
from host.desktop.api import DesktopApi

READY = Readiness(vm_exists=True, vm_reachable=True,
                  runtime_version="runtime-v0.1.0", api_version=1)
STOPPED = Readiness(vm_exists=True)


class FakeProvider:
    pass


class HandoffClient:
    def __init__(self, code=None, error=None):
        self._code, self._error = code, error

    def handoff_code(self):
        if self._error:
            raise self._error
        return self._code


def _api(tmp_path, *, readiness=READY, client=None, loaded=None):
    return DesktopApi(FakeProvider(), InstallState(tmp_path / "s.json"),
                      push=lambda event: None,
                      probe_fn=lambda provider: readiness,
                      client_factory=lambda provider: client or HandoffClient(code="abc"),
                      navigate=(loaded.append if loaded is not None else None))


def test_entering_loads_the_edge_port_with_the_handoff_code(tmp_path):
    loaded = []
    assert _api(tmp_path, loaded=loaded).enter_console() == {"ok": True}
    assert loaded == [f"http://localhost:{constants.EDGE_PORT}/#handoff=abc"]


def test_a_failed_handoff_loads_nothing(tmp_path):
    # Inside the app the console's signed-out screen says "open this from the
    # desktop app" -- a dead end for someone already in it.
    loaded = []
    client = HandoffClient(error=ConnectionRefusedError("refused"))
    result = _api(tmp_path, client=client, loaded=loaded).enter_console()
    assert result["ok"] is False
    assert "refused" in result["message"]
    assert loaded == []


def test_entering_is_refused_while_a_job_runs(tmp_path):
    loaded = []
    api = _api(tmp_path, loaded=loaded)
    release = threading.Event()
    api.jobs.start("import", lambda emit: release.wait(5) and {"type": "done"})
    try:
        assert api.enter_console()["ok"] is False
    finally:
        release.set()
        api.jobs.join(5)
    assert loaded == []


def test_a_running_machine_enters_the_console_at_launch(tmp_path):
    assert _api(tmp_path).home()["enter_console"] is True


def test_the_launch_flag_is_spent_by_the_first_home_call(tmp_path):
    # The Machine menu item reloads the local UI, which calls home() again;
    # a second True would bounce the user straight back into the console.
    api = _api(tmp_path)
    api.home()
    assert api.home()["enter_console"] is False


def test_a_stopped_machine_does_not_enter_and_still_spends_the_flag(tmp_path):
    api = _api(tmp_path, readiness=STOPPED)
    assert api.home()["enter_console"] is False
    api._probe = lambda provider: READY
    assert api.home()["enter_console"] is False


def test_a_resumed_install_never_enters_the_console(tmp_path):
    api = _api(tmp_path)
    api.resumed = True
    assert api.home()["enter_console"] is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `TMPDIR=$PWD/.tmp python3 -m pytest tests/host/desktop/test_api_console.py -q`
Expected: FAIL — `TypeError: ... unexpected keyword argument 'navigate'`

- [ ] **Step 3: Implement in `host/desktop/api.py`**

Constructor — add the `navigate` keyword and two attributes:

```python
    def __init__(self, provider, state, *, push,
                 probe_fn=probe, browser_open=webbrowser.open, steps_factory=None,
                 client_factory=None, install_dir_factory=None, navigate=None):
        ...
        self._navigate = navigate or (lambda url: None)
        self._home_seen = False
```

`home()` — compute and spend the flag; replace the body from `readiness = ...` to the `return` with:

```python
        readiness = self._probe(self._provider)
        route, state = route_for(readiness)
        resumed = getattr(self, "resumed", False)
        self.resumed = False
        first_run = not readiness.vm_exists and not self._state.completed()
        # One-shot like `resumed`: the Machine menu item reloads this page,
        # and a second True would bounce the user back into the console.
        enter_console = (not self._home_seen and (route, state) == ("home", "running")
                         and not first_run and not resumed)
        self._home_seen = True
        return {
            "route": route,
            "state": state,
            # (keep the existing first_run comment here)
            "first_run": first_run,
            "app_version": constants.APP_VERSION,
            "runtime_version": readiness.runtime_version or "",
            "problem": readiness.problem,
            # (keep the existing resumed comment here)
            "resumed": resumed,
            "enter_console": enter_console,
        }
```

New method, directly after `open_eggie`:

```python
    def enter_console(self) -> dict:
        if self.jobs.running():
            # Leaving the local UI now would strand the job's events.
            return {"ok": False, "message": "Wait for the current job to finish first."}
        try:
            code = self._client_factory(self._provider).handoff_code()
        except Exception as e:
            # Never load the console without a code: its signed-out screen
            # sends the user to the desktop app they are already in.
            return {"ok": False, "message": f"{e}"}
        self._navigate(f"http://localhost:{constants.EDGE_PORT}/#handoff={code}")
        return {"ok": True}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `TMPDIR=$PWD/.tmp python3 -m pytest tests/host/desktop -q`
Expected: all PASS (including the existing `test_api_home.py`).

- [ ] **Step 5: Commit**

```bash
git add host/desktop/api.py tests/host/desktop/test_api_console.py
git commit -m "Desktop: enter the projects console in the window"
```

---

### Task 3: Wire the shell, the guarded bridge and the Eggie menu into `run()`

**Files:**
- Modify: `host/desktop/__main__.py` (`run()`, new `menu_items()`, new `_default_menu()`)
- Test: `tests/host/desktop/test_entry.py`

**Interfaces:**
- Consumes: `Shell`, `guarded` (Task 1); `DesktopApi(navigate=...)`, `enter_console()` (Task 2)
- Produces: `menu_items(api, shell) -> list[tuple[str, Callable[[], object]]]`; `run(..., menu=_default_menu)` where `menu(items)` returns what `start(menu=...)` receives

- [ ] **Step 1: Update and add tests in `tests/host/desktop/test_entry.py`**

Add a fake window at the top (after `FakeProvider`):

```python
LOCAL = "file:///ui/index.html"


class FakeWindow:
    def __init__(self, url=LOCAL):
        self.url = url
        self.loaded = []

    def get_current_url(self):
        return self.url

    def load_url(self, url):
        self.loaded.append(url)
        self.url = url

    def evaluate_js(self, script):
        pass
```

Every existing `run(...)` call that succeeds passes `menu=lambda items: items`, and every `create=lambda **kwargs: object()` becomes `create=lambda **kwargs: FakeWindow()`. The facade has no `resumed` attribute, so replace `test_a_resumed_launch_is_recorded_for_the_install_screen` with:

```python
def test_a_resumed_launch_is_recorded_for_the_install_screen(tmp_path):
    """RunOnce relaunches with --resume after a restart; the screen has to
    be able to say why it opened by itself."""
    captured = {}

    def create(**kwargs):
        captured["api"] = kwargs["js_api"]
        return FakeWindow()

    run(FakeProvider(), InstallState(tmp_path / "s.json"),
        create=create, start=lambda **kwargs: None, resumed=True,
        menu=lambda items: items)

    assert captured["api"].home()["resumed"] is True
```

New tests:

```python
def test_javascript_gets_the_guarded_bridge_not_the_api(tmp_path):
    from host.desktop.shell import NotLocalPage
    captured = {}
    window = FakeWindow()

    def create(**kwargs):
        captured["api"] = kwargs["js_api"]
        return window

    run(FakeProvider(), InstallState(tmp_path / "s.json"),
        create=create, start=lambda **kwargs: None, menu=lambda items: items)
    captured["api"].reset_install()
    window.url = "http://localhost:39080/"
    with pytest.raises(NotLocalPage):
        captured["api"].reset_install()


def test_the_menu_offers_projects_machine_and_the_browser(tmp_path):
    started = []
    run(FakeProvider(), InstallState(tmp_path / "s.json"),
        create=lambda **kwargs: FakeWindow(),
        start=lambda **kwargs: started.append(kwargs), menu=lambda items: items)
    assert [title for title, _ in started[0]["menu"]] == \
        ["Projects", "Machine", "Open in browser"]


def test_machine_returns_the_window_to_the_local_ui(tmp_path):
    started = []
    window = FakeWindow()
    run(FakeProvider(), InstallState(tmp_path / "s.json"),
        create=lambda **kwargs: window,
        start=lambda **kwargs: started.append(kwargs), menu=lambda items: items)
    machine = dict(started[0]["menu"])["Machine"]
    # The first machine() records the page the window opened with as local.
    window.url = LOCAL
    machine()
    window.url = "http://localhost:39080/"
    machine()
    assert window.url == LOCAL
```

Add `import pytest` at the top.

- [ ] **Step 2: Run tests to verify they fail**

Run: `TMPDIR=$PWD/.tmp python3 -m pytest tests/host/desktop/test_entry.py -q`
Expected: FAIL — `TypeError: run() got an unexpected keyword argument 'menu'`

- [ ] **Step 3: Implement in `host/desktop/__main__.py`**

Add after `_default_start`:

```python
def menu_items(api, shell) -> list:
    return [
        ("Projects", api.enter_console),
        ("Machine", shell.load_local),
        ("Open in browser", api.open_eggie),
    ]


def _default_menu(items):
    import threading

    from webview.menu import Menu, MenuAction

    def detached(fn):
        # Menu callbacks run on the UI thread; a handoff reads the token
        # through the provider, which would freeze the window meanwhile.
        return lambda: threading.Thread(target=fn, daemon=True).start()

    return [Menu("Eggie", [MenuAction(title, detached(fn)) for title, fn in items])]
```

Replace `run()`'s body from `from .api import DesktopApi` to the end with:

```python
    from .api import DesktopApi
    from .shell import Shell, guarded

    shell = Shell()
    api = DesktopApi(provider, state, push=shell.push, steps_factory=steps_factory,
                     navigate=shell.load)
    # Surfaced by a later task: the install screen reads this to show
    # host.core.install.RESUME_NOTICE when RunOnce reopened the window.
    api.resumed = resumed

    try:
        window = create(title=WINDOW_TITLE, url=str(ui_dir() / "index.html"),
                        js_api=guarded(api, shell), width=WINDOW_SIZE[0],
                        height=WINDOW_SIZE[1], min_size=MIN_SIZE)
    except Exception as e:
        # (keep the existing comment and body of this handler unchanged)
        print(WEBVIEW_MISSING, file=sys.stderr)
        print(f"(technical detail: {e!r})", file=sys.stderr)
        return 3

    shell.window = window
    start(debug=False, menu=menu(menu_items(api, shell)))
    return 0
```

and the signature becomes:

```python
def run(provider, state, *, create=_default_create, start=_default_start,
        menu=_default_menu, resumed: bool = False, steps_factory=None) -> int:
```

Remove the old `holder`/`push` closure — `Shell.push` replaces it (its json comment moved there in Task 1).

- [ ] **Step 4: Run tests to verify they pass**

Run: `TMPDIR=$PWD/.tmp python3 -m pytest tests/host -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add host/desktop/__main__.py tests/host/desktop/test_entry.py
git commit -m "Desktop: guarded bridge and the Eggie menu in the window"
```

---

### Task 4: The UI enters the console

**Files:**
- Modify: `host/desktop/ui/app.js` (`refresh()`, the `open-eggie` action)
- Modify: `host/desktop/ui/index.html` (`home:running`'s primary button action name)
- Test: `tests/host/desktop/test_ui_assets.py`

**Interfaces:**
- Consumes: `home()["enter_console"]`, `enter_console() -> {ok, message?}` (Task 2)

- [ ] **Step 1: Write the failing test** — append to `tests/host/desktop/test_ui_assets.py`:

```python
def test_every_bridge_call_in_the_ui_names_a_real_method():
    """A misspelled api().method() only rejects at runtime, inside a window
    no test opens."""
    from host.desktop.api import DesktopApi
    from host.desktop.shell import public_methods

    script = (UI / "app.js").read_text()
    called = set(re.findall(r"api\(\)\.([a-zA-Z_]+)\(", script))
    assert "enter_console" in called
    assert called <= set(public_methods(DesktopApi)), called - set(public_methods(DesktopApi))
```

- [ ] **Step 2: Run it to verify it fails**

Run: `TMPDIR=$PWD/.tmp python3 -m pytest tests/host/desktop/test_ui_assets.py -q`
Expected: FAIL on `assert "enter_console" in called`.

- [ ] **Step 3: Implement**

In `index.html`, `home:running`'s primary button:

```html
      <button class="btn-primary" data-action="enter-console">Open Eggie</button>
```

In `app.js`, replace the `'open-eggie'` entry in `ACTIONS` with:

```js
  // Success navigates the window away; there is nothing to render after it.
  'enter-console': async () => {
    const result = await api().enter_console();
    if (!result.ok) showNotice(result.message);
  },
```

In `refresh()`, after the `home.first_run` line and before the final `show(...)`:

```js
  if (home.enter_console) {
    const result = await api().enter_console();
    if (result.ok) return;
    showNotice(result.message);
  }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `TMPDIR=$PWD/.tmp python3 -m pytest tests/host -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add host/desktop/ui/app.js host/desktop/ui/index.html tests/host/desktop/test_ui_assets.py
git commit -m "Desktop UI: open the projects console in the window"
```

---

### Task 5: Docs and full verification

**Files:**
- Modify: `CLAUDE.md` (`host/desktop/` bullet; "Things that will bite you")

- [ ] **Step 1: Update `CLAUDE.md`**

In the `host/desktop/` layer bullet, replace the sentence starting "`api.py` is the only object JavaScript can reach" with:

```
`api.py` is the only object JavaScript can reach — through `shell.py`'s `guarded()`
facade, which refuses every call unless the local UI is the page showing. When the
machine is running the same window shows the projects console (`localhost:<edge>`,
entered with a handoff code); a native "Eggie" menu switches between it and the
local screens and offers "Open in browser". It stays a thin, fixed list of methods
taking scalars, with every real decision pushed into `view.py`.
```

Append to "Things that will bite you":

```
- pywebview injects `window.pywebview.api` into every page the window loads, including
  the console served from the VM. `host/desktop/shell.py` refuses bridge calls and
  pushed events unless the local UI is showing; a new `DesktopApi` method is covered
  automatically, but anything handed to `create_window(js_api=...)` other than
  `guarded(...)` is not.
```

- [ ] **Step 2: Full suite**

Run: `TMPDIR=$PWD/.tmp python3 -m pytest -q`
Expected: all PASS.

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "Docs: the console inside the desktop window"
```

- [ ] **Step 4: Manual acceptance (for the PR description, run by a human on Windows and macOS)**

- Launch with the machine running → the window lands on the projects list.
- *Eggie → Machine* → Home "The kitchen is open"; stays there (no bounce).
- *Eggie → Projects* → back in the console.
- *Eggie → Open in browser* → system browser signed in.
- A project's address link and its "Open" button (`window.open`) open in the system browser, not the window.
- Stop the machine from *Machine*, choose *Projects* → notice explaining the handoff failed; window stays on Home.
- Debug build: from the console's devtools, `await window.pywebview.api.doctor()` rejects.
- Every desktop button still works (the bridge now goes through the facade).
```
