# Eggie desktop app — design

Date: 2026-09-21
Design board: `Eggie Desktop.dc.html` (claude.ai/design project `5a77e605`)

## 1. What this replaces and why

`host/setup_app/` is a tkinter wizard plus a status screen: it can run an install
and say whether the machine is set up, and nothing else. The design board covers
twelve screens — first run, a stepped install with restart and failure states,
four Home states, folder import, ports, and a connection failure — in a visual
language tkinter cannot render at all: variable-optical-size display type, soft
shadows, 18–22px radii, and four looping keyframe animations.

So the GUI is rebuilt rather than extended. `host/setup_app/` and
`tests/host/test_setup_app_logic.py` are deleted. `host/cli.py` stays as the
scripting surface and the headless fallback. `host/core/**` is the point of the
exercise: it already holds nearly everything the new screens need, and it is not
restructured.

## 2. Stack

**pywebview over the system webview** — WebView2 on Windows, WKWebView on macOS.
The app stays a Python program: `host/core` is called directly through a small
JS↔Python bridge, PyInstaller packaging survives, and the design's CSS renders
as authored instead of being approximated in a widget toolkit.

Rejected: Tauri/Electron (a third shippable half, two build toolchains, two
signing flows, and every progress event crossing a process boundary); PySide6
(≈100–150 MB frozen, and QSS cannot reproduce this design).

The window uses **native chrome**. The board draws macOS traffic lights and a
centred title inside each frame; that is read as "this is a window", not as
chrome to reimplement. The OS draws the title bar — traffic lights on macOS,
minimise/maximise/close on Windows — and the HTML renders only the content area.
The window title changes per route ("Eggie", "Setting up", "Ports"). Frameless
chrome was rejected: it would make drag regions, double-click-to-maximise and
Windows snap our problem, and "which platform am I on" is a branch the
no-platform-leak invariant forbids outside `host/providers/`.

Theme follows the OS through CSS `prefers-color-scheme` alone. Both webviews
report the system setting and update it live. There is no override control and
nothing is persisted — which also means no Python-side OS check.

## 3. Module layout

```
host/desktop/
  __init__.py
  __main__.py      entrypoint: build provider, create window, webview.start()
  api.py           DesktopApi — the only class JS can reach
  jobs.py          worker-thread registry, event coalescing, event push
  view.py          Readiness / step list / Diagnosis → view models
  ui/
    index.html     one document; every screen is a <template>
    app.css        the board's tokens verbatim, plus focus and reduced-motion
    app.js         router; no framework
    fonts/         Bricolage Grotesque, Hanken Grotesk, IBM Plex Mono (OFL 1.1)
```

`view.py` holds the branching and is therefore where the tests are. `api.py` is
the security boundary: a fixed set of method names, no `eval`, and no path or
command taken from JS and passed to a shell. `index.html` carries a CSP
forbidding every remote origin; the frozen build calls `webview.start(debug=False)`.

Bundled UI assets resolve through `sys._MEIPASS` when frozen. That is a `sys`
attribute check, not a platform check, so `tests/test_no_platform_leak.py`
stays green.

### The bridge

`DesktopApi` is passed to `webview.create_window(js_api=...)`. JS calls
`window.pywebview.api.<method>()` and receives a promise. Anything slow returns
a job id immediately and runs on a worker thread, which pushes events back with
`window.evaluate_js("eggie.on(...)")`.

This is the queue-and-thread shape `setup_app/wizard.py` already uses, without
Tk's `after()` pump. A local HTTP server with SSE was rejected: it opens a
listening socket on the user's machine, and its one benefit — serving the same
HTML from inside the VM later — does not apply, because the in-VM web app is a
different application with different screens.

`evaluate_js` marshals through the webview's main loop, so `jobs.py` coalesces
byte-progress events to ~10/sec. Install emits roughly 30 events over four
minutes and needs no help; the rootfs download's 391 events and the import
upload's byte stream do.

## 4. Routes and states

Twelve boards collapse into six routes, because four are one route in different
states.

```
boot ──probe()──┬─ not_installed ──┐
                ├─ running         │
                ├─ stopped         ├──► #home   (4 states, one template)
                ├─ wrong           │
                └─ unreachable ───────► #unreachable

#first-run ──"Warm up the kitchen"──► #install ─┬─ running
                                                ├─ reboot_required
                                                └─ failed
#home ──► #import ──► #import-progress ──► #home
#home ──► #ports
#home ──► #doctor    (modal over #home)
```

Routing is one-way and stateless: every transition re-renders from a fresh view
model, and no screen keeps its own copy of readiness. Returning from `#ports` to
`#home` re-probes rather than restoring what was on screen, so a VM that died
while Ports was open reads as stopped instead of stale.

`#first-run` versus `#home · not installed` is the one place the board overlaps
itself — both are "nothing installed, press the button". The existing
`_should_auto_start` rule in `setup_app/app.py` draws that line and is carried
over verbatim: `#first-run` only on a machine where `InstallState` has recorded
nothing ever, `#home · not installed` on every later launch. Without it, a
machine stuck failing at `create_vm` re-enters the wizard forever and the user
can never reach the status screen.

### Readiness → route

Derived in `view.py` from `host/core/status.Readiness`. `probe()` returns early
at every stage, so each row below is the exact shape one `return` produces and
the rows are mutually exclusive — evaluated top to bottom:

| `Readiness` | Route / state | What actually happened |
|---|---|---|
| `problem`, nothing else set | `#home · something's wrong` | `provider.exists()` threw — e.g. no `limactl` on PATH |
| all fields default | `#home · not installed` (or `#first-run`, per above) | no VM |
| `vm_exists`, `problem` | `#home · something's wrong` | `exec(["true"])` threw |
| `vm_exists` only | `#home · stopped` | the VM is there and not running |
| `vm_exists ∧ vm_reachable`, `problem` | `#home · something's wrong` | reading `engine.version` threw |
| `vm_exists ∧ vm_reachable`, no engine | `#home · something's wrong` | the engine never finished installing |
| `… ∧ engine_version`, `problem` | `#unreachable` | the VM answers `true` but the agent's `/health` refused or timed out |
| `… ∧ agent_api ∉ SUPPORTED_API` | `#home · something's wrong` | engine too new or too old for this host |
| `Readiness.ready` | `#home · running` | |

`#unreachable` is therefore exactly one case: **reachable, engine installed, and
the agent would not answer.** That is precisely what the board's copy claims —
"Your machine is on — we can see it humming — but Eggie can't get a word in" —
and it is the only state where those words are true. Every other `problem` lands
on "something's wrong", which is the only state carrying both a log and a Run
Doctor button.

One wart worth naming: a provider that cannot even be constructed (`exists()`
threw) reports `vm_exists=False`, which reads like "not installed" but is not.
The table takes `problem` first for that reason; a first-time user with a broken
Lima install must not be told to press "Set up the kitchen" on a machine where
setup cannot run.

## 5. The install flow

The board's seven rows are a **macOS** mock. `default_steps` builds a
platform-dependent list:

| macOS / Lima | Windows / WSL2 |
|---|---|
| `preflight` | `preflight` |
| `install_runtime` (byte progress) | `remediate` |
| | `reboot_gate` |
| | `fetch_image` (byte progress) |
| `create_vm` | `create_vm` |
| `bootstrap` | `bootstrap` |
| `connect` | `connect` |
| `verify` | `verify` |
| `finish` | `finish` |
| **7 rows — the board exactly** | **9 rows** |

The UI renders the list the factory actually returns and derives "Step N of M"
from it; the seven in the mock is not hard-coded. The board's own failed-state
variant already uses tighter row padding (10px against 11px), which is what nine
rows need. The `Downloading … 214 MB of 380 MB` footer binds to whichever step
carries `progress=True` — `install_runtime` on macOS, `fetch_image` on Windows.

Row labels come from `Step.label` when the step sets one (a provider names its
own sentence — "Installing Lima 2.2.0" is Lima's fact) and otherwise from a
label table carried over from `setup_app/wizard.py::LABELS`.

```
main thread          webview.start()  — blocks, owns the window
  │
  └─ api.start_install()  → returns {"job": "..."} immediately
       │
       worker thread ─── run_install(steps, state, report)
                            report(Progress) → jobs.push()
                                                 │ coalesce
                                                 └─ evaluate_js("eggie.onProgress(…)")
```

Terminal events are the exceptions `run_install` already raises:

- `RebootRequired` → `#install · reboot`
- `InstallError` / `DeadEnd` → `#install · failed`, carrying `step`, `message`
  and the step's existing `action` text
- clean return → `#home`

`jobs.py` holds a lock allowing one job at a time, because `InstallState` is a
JSON file and two concurrent installs would race it.

Closing the window mid-install maps onto pywebview's `closing` event the way
`setup_app` maps it onto `WM_DELETE_WINDOW`: record state, return the exit code,
let the relaunch resume.

### Restart

Resume needs no new machinery. `provider.register_resume(exe_path)` already
writes the Windows RunOnce entry and is a documented no-op on Lima (where
`reboot_required()` is always False), and `default_steps`'s gate already calls
it. `host/desktop/__main__.py` takes the same `--resume` flag `cli.setup` does,
so the RunOnce line keeps working.

The board's "Restart now" button is new behaviour: today `reboot_gate_step` only
raises and the tkinter UI asks the user to restart by hand. Rebooting is
`shutdown /r /t 0` against an `osascript`/`shutdown -r`, a platform difference,
so **`reboot()` is added to `VmProvider` and to both providers** rather than to
the UI. It must fire only after the gate has registered resume; a test pins that
order.

## 6. Import, Ports, Doctor, Uninstall

### Import

Import is not `eggie up`. The board says "Copy a folder from your computer into
the kitchen. The original stays exactly where it is" — nothing is started. So
Import is `ensure_project` + `upload_directory` and deliberately does **not**
require a `docker-compose.yml`. A folder a user brings in is one the coding
agent is about to work on; the `eggie-stack` skill writes the compose file
later. Requiring one up front would refuse exactly the folders this screen is
for.

Three additions to existing code:

1. `api.choose_folder()` → `window.create_file_dialog(webview.FOLDER_DIALOG)`.
   Native on both platforms, no branch.
2. `api.inspect_folder(path)` → `{name, files, bytes, conflict}`. Count and size
   come from an `rglob` walk applying the same exclusions as
   `client._uploadable`, so "412 files · 38 MB" describes what will be sent, not
   what is on disk. `conflict` is `client.get_project(project_id_for(name))`
   returning 200.
3. `client.upload_directory` gains an optional `on_progress` callback. It tars
   to a temp file and then streams it — two phases the user waits through, and
   today neither reports. Both are reported as one bar. This is the only change
   to `host/client.py`.

**Merge** is `upload_directory` alone (the agent's `extract_archive` merges by
design). **Replace** is `delete_project` → `ensure_project` → `upload_directory`.
The board's paprika warning is accurate for that sequence and ships as written;
Merge stays the preselected radio, as the board has it.

The board's live per-file path (`src/components/CardList.tsx`) is dropped.
`upload_directory` tars the tree in one pass and streams it, so there is no
per-file callback to report from, and a fabricated filename ticker would be the
wrong kind of honest. The real file count, total size and byte progress remain.

### Consequence for `eggie up`

`cli.up`'s local compose check is removed too, by decision. Without it,
`ensure_project` will have run by the time the agent refuses a folder with no
compose file, leaving a registered project behind. Rather than surfacing a raw
agent error, `up` reports it as what it now is — imported, but nothing to start
yet — which is coherent because Import is now a first-class concept.

### Ports

Backed by `provider.forwards()`, `.forward()`, `.unforward()` on both providers.
The board's highlighted "New" row in `--yolk-soft` is the add affordance, so
"Add a port" appends an editable row rather than opening a dialog.

Validation refuses ports outside 1–65535, a host port already in the table, and
— with a specific message, not a generic refusal — `39099` and `39080`, which
the providers forward for themselves and which a user stealing would silently
break the agent.

The Project column is dropped: providers store `(guest_port, host_port)` pairs
and nothing records which project owns a forward. The table is three columns.

`unforward` on WSL2 shells `netsh interface portproxy`, which writes to HKLM and
needs administrator, so removal can fail or raise UAC. The row reports that
inline in `--paprika` instead of optimistically disappearing.

### Doctor, repair, uninstall

- **Doctor** renders `render_diagnosis(provider.preflight())` into a modal over
  `#home`, reusing the board's `<details>` + `<pre>` log treatment. The board
  has the button but no screen; a modal avoids inventing a thirteenth screen in
  a visual language that would be guesswork.
- **"Repair the connection"** on `#unreachable` runs `_bootstrap(provider,
  repair=True)` then `connect_step` as a job through the same `jobs.py`
  machinery as install — it is a minute of work, not a click.
- **Uninstall** routes to a confirm modal and then `cli.uninstall`'s logic;
  `--purge` is a checkbox in that modal, defaulted off.

## 7. Deliberately inert

Two affordances in the board have no backing code. Neither gets an invented
backend or fake data:

- **"Check for updates"** ships visible and honestly labelled: it shows the
  installed app and engine versions and says update checks are not available
  yet.
- **Ports' Project column** is removed rather than labelled, because a column of
  empty cells is worse than three columns that are all true.

**"Open Eggie"**, the primary CTA on `#home · running`, opens
`http://localhost:39080` in the default browser via `webbrowser.open`. That is
whatever Traefik is serving today, and it becomes correct for free once the
Phase-1 web app lands behind that port — no host release, no new contract.

## 8. Testing

Each test names a bug it would catch.

| Test | The bug |
|---|---|
| `view.py` readiness → route | a provider whose `exists()` threw routed to "not installed", telling a user with a broken Lima to press "Set up the kitchen" on a machine where setup cannot run |
| `view.py`: `#unreachable` is one case | a merely stopped VM shown as "Nobody's answering the door", or an unreachable agent shown as "The pan is cold" |
| step list → row model | "Step 4 of 7" on a 9-step Windows machine; the progress bar bound to the wrong step |
| `jobs.py` terminal mapping | `RebootRequired` rendering as a generic failure, losing the resume path |
| reboot ordering | `reboot()` firing before `register_resume()` — a restart the app never returns from |
| Replace sequence | `upload` before `delete`, merging then wiping instead of wiping then copying |
| port validation | forwarding `39099` and silently killing the agent |
| no remote URL under `host/desktop/ui/` | fonts regressing to a CDN, breaking the app offline |

The last is an architectural-invariant test. Three more come free:
`test_no_platform_leak`, `test_no_agent_import` and `test_host_dependencies`
already glob `host/**/*.py` and will cover `host/desktop/` the day it exists.

**Not written:** anything asserting CSS rendering, the router, or that a
template renders. There is no JS test runner in this repo and one is not being
added for that. `tests/host/test_setup_app_logic.py` is deleted with the module
it covers.

## 9. Packaging

- Entrypoint swaps to `host/desktop/__main__.py` in
  `packaging/windows/eggie.spec` and `packaging/macos/eggie.spec`.
- `host/desktop/ui/**` is bundled data.
- The Windows installer ships and runs Microsoft's evergreen WebView2
  bootstrapper.
- The macOS build **stops needing a tkinter-capable Python**. CLAUDE.md's line
  saying otherwise is corrected.
- Both build scripts keep smoke-testing the frozen binary with `version` then
  `selfcheck` before packaging; both are headless and need no webview.

Dependencies grow by more than one package: `pywebview`, plus `pythonnet` on
Windows (the WebView2 backend loads .NET assemblies) and `pyobjc` on macOS, both
behind environment markers. `tests/host/test_host_dependencies.py` still passes
— it forbids `fastapi`, `uvicorn` and `pyyaml` specifically — but this does
relax CLAUDE.md's "host runtime deps are `typer` alone", and that line is
updated rather than left lying.

Fonts are SIL OFL 1.1; the woff2 files and their licenses ship in the binary.

## 10. Risks

1. **WebView2 missing on un-updated Windows 10.** Window creation raises
   outright. Mitigated twice: the Inno installer runs the evergreen
   bootstrapper, and `__main__.py` catches the creation failure to print the
   `eggie setup --headless` instruction instead of a traceback. This is the
   risk that will reach real users.
2. **PyInstaller + pywebview on Windows is fiddly** — pythonnet assemblies and
   `WebView2Loader.dll` must be collected explicitly. Solvable, but expect a
   packaging iteration on a real Windows machine.
3. **macOS remains largely unverified.** The repo's own banners record that
   `create_vm` → `verify` has never been run end to end. This work does not
   change that; it means the install screens get their first honest exercise on
   Windows.
4. **Gatekeeper.** An unsigned `.app` embedding a webview draws more friction
   than an unsigned CLI. A known Phase-1 gap, not addressed here.
5. **Accessibility.** The board is div-soup with no focus states and permanently
   looping animation. The implementation uses real `<button>`, `<label>` and
   `<input>`, adds a focus ring derived from `--yolk`, and freezes every loop
   under `prefers-reduced-motion` — which also enforces the board's own rule
   that only one thing loops at a time.
