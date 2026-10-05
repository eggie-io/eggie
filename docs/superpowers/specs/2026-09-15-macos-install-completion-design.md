# Finishing the macOS install — design

**Date:** 2026-09-15
**Status:** approved, not implemented
**Touches:** `host/providers/`, `host/core/install.py`, `host/core/provider.py`,
`host/setup_app/`, `packaging/macos/`, the macOS docs.

## The problem

Three gaps, all of them on the path between a user double-clicking
`EggieSetup-<version>.pkg` and that user's coding agent working inside the VM.

1. **Lima is the user's problem.** `LimaProvider.preflight()` dead-ends with
   `install Lima (brew install lima) or bundle limactl`. A packaged installer
   that stops to tell the user to install a package manager and then a package
   has not finished installing anything.
2. **The setup window is a Windows window.** Every label in
   `host/setup_app/app.py` asks for `("Segoe UI", 10)`, which does not exist on
   macOS; the progress bar is indeterminate even during a 391 MB download; the
   log occupies a third of the window from the first frame; and two of the
   failure messages in `host/core/install.py` tell the user to press a **Copy
   diagnostics** button that has never been built.
3. **Nothing tells the user how to get a coding agent into the VM.** The engine
   installs Claude Code and Codex inside the guest, with skills, and the host
   says only `eggie up <folder>`. On macOS the way in is SSH, and the
   credentials are scattered across `~/.lima/eggie-vm/ssh.config` and
   `~/.lima/_config/user`.

## Decisions taken before the design

| Question | Decision |
|---|---|
| How does Lima reach the user's Mac? | Downloaded during setup, into a managed directory. Not bundled in the `.pkg`, not Homebrew. |
| What about a Mac that already has Lima? | Ours always wins. A brew install is left alone and ignored once ours exists. |
| What does the coding-agent screen hand over? | Read-only credentials with per-field Copy. The app never writes `~/.ssh/config`. |
| What happens when the app opens on a provisioned machine? | It opens on a status screen. Setup runs from a button — and automatically the first time, when nothing is provisioned. |
| How far does the UI rework go? | A custom-drawn wizard, not a ttk polish pass. |

Two structural choices follow from these and were approved separately: the new
work attaches through **two new install-surface members** rather than through
private provider state, and `host/setup_app/` becomes a package of screens.

## Architecture

Nothing here crosses a boundary the repo already defends. The host still never
imports `agent/`; no platform branch appears outside `host/providers/`; the
frozen bundle gains no `datas` entry, because Lima is fetched at setup time, not
shipped.

```
Eggie.app
   └── eggie-setup            host/setup_app/app.py  (router)
                                 ├── probe()          host/core/status.py
                                 ├── status screen    host/setup_app/status.py
                                 │     └── provider.access()
                                 └── wizard screen    host/setup_app/wizard.py
                                       └── run_install(default_steps(provider))
                                             ├── preflight
                                             ├── install_runtime  ← new, from provider.runtime()
                                             ├── create_vm … verify … finish
```

### 1. Lima is installed by setup, as its own step

**New module `host/providers/lima_install.py`.** It holds the pinned release and
the mechanics of putting it on disk, and nothing else:

```python
LIMA_VERSION = "2.2.0"
ARCHIVES: dict[str, Image] = {"arm64": Image(url, sha256),
                              "x86_64": Image(url, sha256)}

def archive_for(machine: str) -> Image
def managed_root(root: Path) -> Path          # root/lima
def managed_limactl(root: Path) -> Path       # root/lima/bin/limactl
def install(root: Path, *, fetch=fetch, on_progress=None) -> Path
```

The two digests are not invented here: they are pinned at implementation time
from the SHA256SUMS published with the Lima release, and a test asserts that
both architectures are present and that each digest is 64 hex characters.

`archive_for` normalizes `platform.machine()` (`aarch64` → `arm64`, `amd64` →
`x86_64`) and raises for anything else. `platform.machine()` is not one of the
three names `tests/test_no_platform_leak.py` greps for, but it is exactly the
kind of knowledge that test exists to contain, so it lives in `providers/`
regardless.

`install()` is idempotent, and does five things in order:

1. Return `managed_limactl(root)` immediately if `root/lima/.version` reads
   `LIMA_VERSION` and the binary is executable.
2. `fetch(archive, root/cache/<name>.tar.gz, on_progress=on_progress)` — the
   same digest-verified, resumable call `fetch_image` makes. A wrong digest
   raises `ChecksumMismatch` and deletes the partial file, for free.
3. Extract into a temp sibling directory with `tarfile`'s `filter="data"`
   **plus** an explicit absolute-path rejection. The filter silently normalizes
   an absolute member name instead of refusing it — the finding already written
   down for `agent/core/files.extract_archive`, and the same tar library here.
4. Swap the temp directory into place (move any existing one aside, move the new
   one in, delete the old), then write `.version`.
5. Run `limactl --version` and require the output to contain `LIMA_VERSION`.

Step 5 is the point of the step. "The tarball extracted" is not evidence that a
binary runs, and this repository has already shipped one provisioning step that
reported success for work that never happened (`bootstrap.py`'s dropped `exec`
result, and the WSL2 provider's dropped `limactl` twin).

The tarball's own layout is preserved deliberately: `limactl` locates
`share/lima/` relative to its own executable, so `bin/` and `share/` must remain
siblings under `root/lima/`.

**Why fetching sidesteps a real hazard.** `packaging/macos/build.sh` already
records that `codesign --deep` re-signs every Mach-O in the bundle, and would
therefore strip the `com.apple.security.virtualization` entitlement that Lima
ad-hoc signs `limactl` with — breaking `vz` on signed builds only, on the user's
machine. Bytes extracted from the release tarball carry Lima's own signature
untouched, and `urllib` attaches no `com.apple.quarantine` xattr, so neither
Gatekeeper nor our signing is in the path at all. This reasoning goes in the
module's docstring; it is the reason the decision is not arbitrary.

**New install-surface member `runtime()`.** It returns `None` on WSL2 — `wsl.exe`
is part of Windows and there is nothing to install — and on Lima a frozen value
naming the archive, the target directory and the label. `default_steps` inserts
one `install_runtime` step immediately after `preflight` when it is not `None`,
mirroring exactly how a non-`None` `image()` inserts `fetch_image`. `remediable`
stays `False` on Lima, so `reboot_gate` does not reappear.

The step is `always_run=True`, like every step below the reboot gate. What it
produces is a directory a user can delete, so recording it as done would let a
later run skip straight to `create_vm` with no `limactl` to run; re-deriving it
costs one file read when the version already matches, and needs no network. It
gets an `_ACTIONS` entry of its own, naming the download as the thing that
failed and a re-run as the fix, because `fetch` resumes from where it stopped.

**`Progress` gains `fraction: float | None`,** and `on_progress` is plumbed
through both `install_runtime` and `fetch_image`. This is not only for the new
step: the Windows path downloads 391 MB behind an indeterminate bar today.

**`find_limactl` resolution order becomes** managed copy → `PATH` → Homebrew
prefixes → bare name. Ours always wins once setup has run. The Homebrew fallback
survives for one case only: a source checkout on a developer machine that has
never run setup. A user's brew Lima is never removed, never upgraded and never
used once `root/lima/bin/limactl` exists.

**`is_supported()` and `preflight()` diverge on Lima,** the way they already do
on WSL2, where `preflight()` is the richer of the two. Here it is the other way
round:

- `preflight()` gates on the macOS version alone. Dead-ending on a missing Lima
  would now be setup refusing to do its own job.
- `is_supported()`, which is what `eggie doctor` prints, reports the macOS
  version *and* Lima, and tells a user who has neither to run Eggie setup —
  not to run `brew`.

**`host/providers/eggie.yaml` gains an `x86_64` image entry.** It declares only
`aarch64` today, so on an Intel Mac — which `packaging/macos/distribution.xml`
explicitly supports building for — `limactl start` has no image it can use.

### 2. `access()`, so the status screen never asks what SSH is

Two new frozen value types in `host/core/provider.py`, beside `Completed` and
`Diagnosis`:

```python
@dataclass(frozen=True)
class AccessField:
    label: str
    value: str

@dataclass(frozen=True)
class Access:
    headline: str
    summary: str
    command: str
    fields: tuple[AccessField, ...] = ()
    note: str = ""
```

`LimaProvider.access()` parses `~/.lima/eggie-vm/ssh.config` — `Hostname`,
`Port`, `User`, `IdentityFile`, matched case-insensitively — and falls back to
the values declared in `eggie.yaml` (`127.0.0.1`, port 39022,
`~/.lima/_config/user`) with a `note` saying the VM has not been started, so the
screen shows something true even before the first boot.

Reading that file rather than shelling `limactl show-ssh` is deliberate:
`LimaProvider.forward()` already passes the same path to `ssh -F`, and
`docs/lima-verification-report.md` lists its existence, name and layout as an
assumption no run has confirmed. A second reader turns a wrong assumption into a
visible defect on the screen a user looks at first, instead of a silent one
inside a port forward nobody exercises.

`Wsl2Provider.access()` answers honestly rather than by analogy: a WSL distro
runs no SSH server, so the command is `wsl -d eggie` and the fields are the
distro name and the `\\wsl$\eggie\opt\eggie\projects` path. The screen exists
on both platforms; only its contents differ, and they differ inside
`providers/`.

The screen is read-only. Per-field Copy buttons, and nothing is ever appended to
the user's `~/.ssh/config`.

`INSTALL_SURFACE` in `tests/host/test_provider_surface.py` becomes
`{image, register_resume, location, terminal, remediable, runtime, access}`, and
the test that keeps the install surface out of the lifecycle Protocol keeps
both new names out of it too.

### 3. `host/setup_app/` becomes a package of screens

`app.py` is 129 lines holding one function that builds a window, runs an
install, and renders progress. It splits five ways:

- **`theme.py`** — palette, fonts, metrics. Fonts are tkinter's named system
  fonts (`TkDefaultFont`, `TkHeadingFont`, `TkFixedFont`), copied and resized;
  no family name is ever written down. Light and dark palettes are selected by
  the **luminance of the ttk theme background** — `style.lookup("TFrame",
  "background")` → `winfo_rgb()` → luma — which gets dark mode right on both
  platforms without any code asking which OS it is running on. `palette_for()`
  takes an RGB triple and returns a palette, so it is a pure function.
- **`widgets.py`** — the drawn pieces. A header band; a step row carrying a 22 px
  glyph (empty ring pending, rotating arc running, check done, × failed, muted
  check skipped), a label, and a right-aligned elapsed time; a determinate
  progress bar; accent and secondary buttons.
- **`wizard.py`** — today's `run_window`, redrawn. Progress is real: completed
  steps over total, plus the within-step `fraction` while a download runs. The
  log moves behind a **Details** disclosure. The failure panel leads with the
  plain-language action sentence from `Step.action`, with the raw error under
  Details.
- **`status.py`** — the new home screen: VM state, engine version, agent API
  number, the `Access` panel with per-field Copy, and the buttons **Set up /
  Re-run setup**, **Copy diagnostics** and **Close**.
- **`app.py`** — the router.

**Copy diagnostics gets built.** `_ACTIONS` in `host/core/install.py` names the
button in two failure messages and no such button exists.
`status.diagnostics_text(readiness, access, version, log_lines)` is a pure
function producing the same text for both screens; the widget layer only calls
`clipboard_clear()`/`clipboard_append()`.

**Routing.** The app always opens on the status screen. It runs the readiness
probe on a worker thread first, showing a spinner rather than a frozen window,
and starts the wizard by itself when nothing is provisioned — so a first-time
user still double-clicks once and watches it install. On a provisioned machine
the status screen is the landing surface and setup runs only from its button.
The wizard's success path returns to the status screen instead of printing a
closing sentence, so "you are set up, and here is how to connect" is one surface
with one owner.

`finish_step` keeps returning its sentence: `eggie setup --headless` is still a
supported front door and still has to say something at the end.

### 4. `host/core/status.py` — the readiness probe

```python
@dataclass(frozen=True)
class Readiness:
    vm_exists: bool
    vm_reachable: bool
    engine_version: str | None
    agent_api: int | None
    problem: str = ""

    @property
    def ready(self) -> bool: ...

def probe(provider, *, client_factory=None) -> Readiness
```

`probe()` never raises; every fact is a guarded call, and the first failure is
recorded in `problem` rather than propagated. `vm_reachable` is
`provider.exec(["true"]).ok` rather than a new provider member for "is it
running": reaching the guest is the fact that matters, and both platforms answer
it identically with what the Protocol already offers. Then
`cat /opt/eggie/engine.version`, then `AgentClient.health()` for the API number.

## Testing

TDD, as the repo does it. No test opens a window; everything worth testing is
pure or injectable.

| Test | What it holds |
|---|---|
| `tests/host/test_lima_install.py` | arch selection and its rejection; the early return on a matching `.version`; a checksum mismatch; extraction rejecting an absolute member and a `..` escape; the version assertion failing when `limactl --version` disagrees |
| `tests/host/test_lima.py` (extended) | `access()` with and without an `ssh.config`; `find_limactl` preferring the managed copy over a brew prefix; `preflight()` no longer dead-ending on Lima |
| `tests/host/test_provider_surface.py` | the two new members on both providers, and out of the Protocol |
| `tests/host/test_default_steps.py` | `install_runtime` present for a provider with a `runtime()`, absent without one, and positioned after `preflight` |
| `tests/host/test_status_probe.py` | `probe()` against a `FakeProvider`: ready, VM missing, VM present but agent silent — and that it never raises |
| `tests/host/test_setup_app_logic.py` | `palette_for()` on a light and a dark background, elapsed-time and step-label formatting, `diagnostics_text()` |
| `tests/host/test_frozen_bundle.py` | unchanged, and must stay passing: Lima is fetched, never bundled |

The engine shell tests, the import-boundary tests and the platform-leak test all
stay untouched and passing.

## Documentation

- `CLAUDE.md` — the two new surface members, the `setup_app` package, and
  `Progress.fraction`.
- `docs/macos-install-test-matrix.md` — case 4 no longer means what it says,
  since Lima stops being a dead end; new cases for the download step and for the
  status screen.
- `docs/lima-verification-report.md` — `ssh.config` is now load-bearing in two
  places, and `install_runtime` is a macOS path that can be verified before a VM
  ever boots.

## What this explicitly does not do

It does not make `LimaProvider` verified. No VM has ever been created on macOS in
this repository. `install_runtime` ends at a binary that prints its own version;
`create_vm` remains the first unproven step, and every assumption in
`docs/lima-verification-report.md` about `vz`, Rosetta, port forwarding and the
ssh control master stays unproven. **The UNVERIFIED banners in
`host/providers/lima.py` and `host/providers/eggie.yaml` stay**, and both docs
will say plainly which of these paths ran and which did not.

It also does not bundle Lima, write to `~/.ssh/config`, upgrade or remove a
user's Homebrew Lima, sign anything, or wire up notarization.
