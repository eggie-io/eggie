# Update system: runtime and desktop app

Date: 2026-09-28
Status: implemented
Issue: #25

## 1. Problem

Nothing updates today. Once `/opt/omelet/runtime.version` exists the host never runs the
installer again, Repair reinstalls the ref already installed, and a new `runtime-v*` tag reaches
only fresh VMs. The host has no way to learn about or install a newer desktop app. The console's
"Omelet needs an update" screen points at a desktop action that does not exist.

Goal:

- The runtime updates itself, automatically, but only to a release the host can drive.
- A host that finds an incompatible runtime updates it at once.
- The desktop app updates on one click.

## 2. Decisions

| # | Decision | Why |
|---|----------|-----|
| 1 | The runtime updates at VM boot, never while the VM is up. | Nobody is mid-task, and the stack restart hides inside the boot. A VM up for weeks stays behind until it restarts — accepted. |
| 2 | The VM decides and applies the update, not the host. | The VM often runs without the desktop app open, and a cloud VM has no host at all. |
| 3 | Compatibility is the existing API number. Each runtime release declares it in `runtime/release.json`. | A tag is immutable, so the declaration cannot drift. A separate manifest on `main` can be forgotten; encoding it in the tag name collapses the release number into the API number. |
| 4 | Tags without `release.json` are skipped. | `runtime-v0.0.5`–`0.0.7` are pre-releases no shipped host installed; no compat for unreleased state. |
| 5 | The host tells the VM what it supports through `/opt/omelet/host.json`. | The boot updater must know the host's range while the host is not running. |
| 6 | No overlap rule. When a host finds an incompatible runtime, it updates it immediately. | Simpler to state; a newer host never waits on a reboot. |
| 7 | Every update, whoever starts it, runs `get.sh` from `main`. There is no `omelet self-update` CLI command. | One updater code path, and it works when the installed runtime is too old to hold any updater. |
| 8 | An update stages everything before touching the installed runtime, and rolls back if `install.sh` fails. | A boot with no network, or a bad release, must leave a working VM working. |
| 9 | The desktop app updates manually, on one click: download, verify, run the installer, quit. | The users are non-technical; a release page and a download folder lose them. |
| 10 | Host builds are GitHub releases tagged `host-vX.Y.Z` with a `SHA256SUMS` asset. | The repo already hosts the runtime; no new infrastructure. |

## 3. The contract between host and runtime

Added to the seam. Everything already in it is unchanged.

1. **`runtime/release.json`** in every runtime tag: `{"api": N}`. `tests/test_constants_agree.py`
   holds `N` equal to `runtime/omelet_api/core/constants.API_VERSION`.
2. **`/opt/omelet/host.json`**: `{"supported_api": [1]}`, written by the host (root) every time it
   reaches the VM. Only the host writes it; the runtime only reads it.
3. **`/opt/omelet/runtime.env`**: `OMELET_RUNTIME_URL=…` and `OMELET_RUNTIME_REPO=…`, written by
   `get.sh` on every install, so an update started inside the VM uses the source the VM was
   installed from (a fork during development) rather than a hard-coded default. The host's stub
   exports `OMELET_RUNTIME_URL` to `get.sh`; without it (`curl | bash` on a cloud VM) `get.sh`
   records `$REPO/raw/main/runtime/install/get.sh`.
4. **`get.sh` inputs**, both optional, so every existing caller behaves as before:
   - `OMELET_RUNTIME_API=1,2` — accepted API numbers. When set, `resolve_ref` takes the newest
     `runtime-vN.N.N` tag whose `release.json` has an accepted `api`.
   - `OMELET_RUNTIME_UPDATE=1` — update mode (section 4).

`resolve_ref` precedence becomes: explicit `OMELET_RUNTIME_REF` → installed ref on repair →
newest tag matching `OMELET_RUNTIME_API` (or newest tag when unset). An explicit ref is installed
as asked, without an API check: pinning is a deliberate override.

The host's `bootstrap()` always passes `OMELET_RUNTIME_API` from `SUPPORTED_API`, so a fresh
install also gets a compatible runtime.

## 4. The runtime updater (inside the VM)

### Trigger

`runtime/install/systemd/omelet-update.service`, installed and enabled by `install.sh` next to
`omelet-github.*`. `Type=exec`, so boot (and `limactl start` or the WSL launch) never waits for
image pulls; `After=network-online.target docker.service`, `Wants=docker.service` only —
`Wants=network-online.target` would pull in `systemd-networkd-wait-online`, which stalls under WSL,
so `boot-update.sh` retries its fetch instead; `WantedBy=multi-user.target`. The stack's containers
start at boot as they do now; an update replaces them moments later.

The unit runs a small script, `runtime/install/lib/boot-update.sh`:

1. Read `runtime.env` for the URL.
2. Accepted APIs: `host.json`'s `supported_api`; when that file is missing (a cloud VM, or a host
   that never connected), the `api` of the installed `/opt/omelet/runtime/release.json`. A VM with
   no host therefore gets fixes but never changes API.
3. Fetch `get.sh` from the URL (downloaded in full before it runs, as the host's stub does) and run
   it with `OMELET_RUNTIME_UPDATE=1 OMELET_RUNTIME_API=<accepted>`.

An installed ref that is not a `runtime-vN.N.N` tag (a pinned branch) is left alone.

### Update mode in `get.sh`

1. Resolve the ref. If it equals `runtime.version`, **exit 0 without changing anything**. This is
   the normal boot.
2. **Stage:** download the tarball to a temp dir, then
   `docker compose -f <new stack.yml> --profile tunnel pull`. Any failure (no network, GitHub or
   ghcr unreachable, a missing image) exits non-zero; the installed runtime and running stack are
   untouched.
3. **Swap:** move `/opt/omelet/runtime` to `/opt/omelet/runtime.prev`, move the new tree in, run
   `install.sh <new ref>`. On success delete `runtime.prev`.
4. **Roll back** when `install.sh` fails: restore `runtime.prev` and run its
   `install.sh <old ref>` (its images are still local), then exit non-zero.

### Locking

All of `get.sh` — install, repair and update — runs under `flock /opt/omelet/update.lock`, so a
host-started update and the boot unit cannot interleave.

### What an update never touches

Project containers and folders, `state.db`, `api.token`, `github/`, `tunnel/`, `host.json`.
`install.sh` already preserves these on a re-run; the tests pin it for update mode.

### Failures

Logged to the journal (`journalctl -u omelet-update`). The VM stays on its current version and
tries again next boot. Nothing is surfaced to the user: the one case the user must see — an
incompatible runtime — the host handles (section 5).

## 5. The host's share of runtime updates

### Declaring support

`host/core/runtime_update.py::declare_supported(provider)` writes `host.json` through
`provider.exec(root=True)`. Called from:

- `connect_step` (setup, repair, headless), before the `/health` check;
- the desktop's `home()`, right after `probe()` whenever `vm_reachable` is true.

`probe()` stays read-only.

### Immediate update on an incompatible runtime

`bootstrap(provider, update=True)` reuses the existing stub and adds `OMELET_RUNTIME_UPDATE=1`
next to the always-present `OMELET_RUNTIME_API`.

`connect_step`, instead of raising `ApiIncompatible` straight away:

1. runs `bootstrap(provider, update=True)`;
2. waits for `/health` through `_once_serving` (the existing 30 s window) and checks again;
3. raises `ApiIncompatible` only when still incompatible — carrying `get.sh`'s own error when it
   failed (for example "no runtime-v* release speaks api 2", or GitHub unreachable).

### Desktop

`route_for` gains `("update_runtime", "")` for "runtime installed, `api` not in `SUPPORTED_API`".
`("home", "wrong")` keeps a missing marker and unknown failures. On `update_runtime` the UI starts
the update job itself, no click, through `jobs.py` (so it cannot overlap a repair or a restart),
and shows "Updating Omelet inside the virtual machine" with the stage events the repair job
already emits. Success goes back to Home and into the console; failure shows the guest's stderr,
**Try again** and the existing Diagnose link.

### Console

`NeedsUpdate.tsx`'s copy becomes "Open the Omelet desktop app — it finishes the update."

## 6. Updating the desktop app

### Releases

GitHub releases on the repo, tag `host-vX.Y.Z`, not marked pre-release. Assets:

- `OmeletSetup-X.Y.Z.exe`
- `OmeletSetup-X.Y.Z-arm64.pkg`, `OmeletSetup-X.Y.Z-x86_64.pkg` (native-arch builds)
- `SHA256SUMS`

`packaging/macos/build.sh` puts the arch into the `.pkg` name.

### Checking

`host/core/app_update.py`, stdlib `urllib` only. Once per app launch on a background thread:
`GET https://api.github.com/repos/ihorklymchukdev/omelet/releases`, keep non-draft,
non-pre-release `host-vN.N.N` tags, compare as version tuples, take the highest above
`APP_VERSION` that has this machine's asset and `SHA256SUMS`. Any failure means "no update" and
shows nothing. Unauthenticated GitHub allows 60 requests an hour — one per launch is well inside.

### The button

Home shows "Omelet X.Y.Z is available" with **Update**, disabled while any job runs. Clicking it:

1. downloads the installer into the app's cache with `host/core/download.py` (resumable, SHA-256
   checked against `SHA256SUMS`), showing progress;
2. starts the installer detached and closes the app.

The check usually finishes after Home has drawn, so a found release is pushed to the window, which
redraws Home (only Home) to show the offer. Known limitation: when the app opens straight into the
console on a running machine, the offer is seen only on Home — the "Check for updates" tile, or
coming back Home from the console.

### Platform difference, in the providers

- `installer_asset(version) -> str` — the asset name for this machine.
- `launch_installer(path) -> None`:
  - Windows: `OmeletSetup.exe /SILENT /SUPPRESSMSGBOXES`. Per-user install, so no UAC.
    `installer.iss` gains: waiting for the running app to exit before copying files, and a `[Run]`
    entry that relaunches the app after a silent install (the existing "Set up Omelet now" entry is
    `skipifsilent`).
  - macOS: `open <pkg>`. Installer.app asks for the admin password as a first install does; the
    user reopens Omelet.

### After the update

The new app starts; if its `SUPPORTED_API` no longer covers the VM's runtime, section 5's
immediate update runs.

## 7. Out of scope

- Code signing and notarization (a separate Phase 1 item). The SHA-256 check guards against a
  broken download, not a compromised release.
- Automatic host updates, "skip this version", release notes in the app.
- A periodic in-VM check or a "restart to update" action.
- Surfacing failed boot updates in the console.
- An `omelet self-update` CLI command.
- While a boot update recreates the stack, an open desktop app can briefly show the unreachable
  screen; its Restart button would interrupt the update (the next boot redoes it).

## 8. Testing

Automated, where a wrong result is plausible:

- `resolve_ref` (sourced from `get.sh`, fake `git` and `curl` on `PATH`): newest compatible tag
  wins over a newer incompatible one; tags without `release.json` are skipped; nothing compatible
  is a clear error; explicit ref and repair still win; unset `OMELET_RUNTIME_API` behaves as today.
- `get.sh` update mode against fakes: same ref exits 0 and changes nothing; a failed pull leaves
  `/opt/omelet/runtime` untouched; a failed `install.sh` restores `runtime.prev` and re-runs the
  old ref.
- `boot-update.sh`: accepted APIs come from `host.json`, else from the installed `release.json`.
- `connect_step`: incompatible → exactly one `bootstrap(update=True)`; still incompatible →
  `ApiIncompatible`; compatible → no update.
- `route_for`: installed-but-incompatible → `update_runtime`; missing marker → still `wrong`.
- `app_update` against a realistic GitHub response: ignores `runtime-v*`, drafts, pre-releases
  and older versions; `0.10.0 > 0.9.0`; no asset for this platform → no update; failed or empty
  response → no update.
- `bootstrap` argv: `OMELET_RUNTIME_API` always; `update=True` adds `OMELET_RUNTIME_UPDATE=1`.
- `test_constants_agree.py`: `release.json`'s `api` equals `API_VERSION`.

Left to manual cases in `docs/release-testing.md` (thin OS wrappers): the systemd unit,
`launch_installer`, the installer relaunch. New cases: update from an older host on Windows and on
macOS; boot with no network; boot onto a newer compatible runtime.

## 9. Docs

- `docs/releasing.md` rewritten short and direct: cutting a runtime release (with
  `release.json`), when to bump `API_VERSION`, cutting a host release (build, `SHA256SUMS`,
  `host-v*` tag, assets), how installed VMs move (automatically at boot; the manual `get.sh`
  commands stay for pinning).
- `runtime/install/CLAUDE.md` and the root `CLAUDE.md` seam list: `host.json`, `runtime.env`,
  `release.json`, the new `get.sh` inputs, the update lock.
- `docs/future/engine-self-update.md` deleted, its row removed from `docs/README.md`.
