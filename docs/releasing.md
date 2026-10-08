# Releasing

The desktop app (`host/`) and the runtime (everything inside the VM) release separately.

## How installed machines update

- **Runtime:** every VM checks at boot (`eggie-update.service`) and moves to the newest
  `runtime-vX.Y.Z` whose `runtime/release.json` `api` the host accepts (`/opt/eggie/host.json`).
  A host that finds an older API updates the runtime at once. A failed update keeps the old one.
- **Desktop app:** the app checks `app-vX.Y.Z` releases on launch and shows **Update**.

`runtime/install/get.sh` on `main` is what every host and every VM runs. Keep its env vars
(`EGGIE_RUNTIME_REPO`, `_REF`, `_REPAIR`, `_API`, `_UPDATE`), the marker
`/opt/eggie/runtime.version` and its exit codes backward compatible.

## Cut a runtime release

GitHub → **Actions** → **Release runtime** → **Run workflow** on `main`, version `X.Y.Z`.

It runs the Python and web tests, builds and pushes `eggie-api` and `eggie-web` for amd64 and
arm64 as `X.Y.Z`, then creates `runtime-vX.Y.Z`. The tag is the only place the version lives:
`install.sh` turns it into `EGGIE_VERSION` in `/opt/eggie/.env`, which `stack.yml` reads. Nothing
to bump in the repo. VMs pick it up at their next boot.

Every change under `runtime/` needs a release to reach VMs, the in-VM CLI and agent instructions
included. Skills live in `eggie-skills` and are re-installed by every runtime install.

### Changing the API number

Only when a route the host calls changes incompatibly:

1. Bump `API_VERSION` in `runtime/eggie_api/constants.py` and `runtime/release.json`.
2. Release a desktop app whose `SUPPORTED_API` includes the new number **before** tagging the runtime,
   or no VM will install it.

The release number (the tag) and `API_VERSION` (the wire protocol) are different numbers. Never
merge them.

## Cut a desktop app release

1. In a PR, bump `version` in `pyproject.toml` and `APP_VERSION` in `host/core/constants.py`
   to `X.Y.Z`, and merge it.
2. GitHub → **Actions** → **Release app** → **Run workflow** on `main`, version `X.Y.Z`.

It refuses a version that doesn't match both files, runs the Python tests, builds
`EggieSetup-X.Y.Z.exe`, `EggieSetup-X.Y.Z-arm64.pkg` and `EggieSetup-X.Y.Z-x86_64.pkg` on
Windows and macOS runners, then creates the `app-vX.Y.Z` release with them and `SHA256SUMS`.
Installed apps see the release as soon as it exists, so run the manual checks in
[release-testing.md](release-testing.md) on locally built installers before step 2. The
installers are unsigned.

A release marked prerelease or draft is never offered to installed apps.

## First-release checklist

- The repository is public, `runtime/install/get.sh` is on `main`.
- A `runtime-v*` tag with `runtime/release.json` exists.
- ghcr `eggie-api` and `eggie-web` are public, and each package grants this repository
  **Write** under *Manage Actions access* (the workflow pushes with `GITHUB_TOKEN`).
- `github.com/eggie-io/eggie-skills` is public.

## Pin or repair a VM by hand

```powershell
wsl -d eggie-vm -u root -- bash -lc "curl -fsSL https://raw.githubusercontent.com/eggie-io/eggie/main/runtime/install/get.sh | EGGIE_RUNTIME_REF=runtime-vX.Y.Z bash"
```

```bash
limactl shell eggie-vm -- sudo bash -lc "curl -fsSL https://raw.githubusercontent.com/eggie-io/eggie/main/runtime/install/get.sh | EGGIE_RUNTIME_REF=runtime-vX.Y.Z bash"
```

Use `EGGIE_RUNTIME_REPAIR=1` instead to reinstall the current release. A VM on a pinned branch
(not a `runtime-v*` tag) is never moved by the boot update.
