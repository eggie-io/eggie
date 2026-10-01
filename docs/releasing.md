# Releasing

The host (desktop app) and the runtime (everything inside the VM) release separately.

## How installed machines update

- **Runtime:** every VM checks at boot (`omelet-update.service`) and moves to the newest
  `runtime-vX.Y.Z` whose `runtime/release.json` `api` the host accepts (`/opt/omelet/host.json`).
  A host that finds an older API updates the runtime at once. A failed update keeps the old one.
- **Desktop app:** the app checks `host-v*` releases on launch and shows **Update**.

`runtime/install/get.sh` on `main` is what every host and every VM runs. Keep its env vars
(`OMELET_RUNTIME_REPO`, `_REF`, `_REPAIR`, `_API`, `_UPDATE`), the marker
`/opt/omelet/runtime.version` and its exit codes backward compatible.

## Cut a runtime release

GitHub → **Actions** → **Release runtime** → **Run workflow** on `main`, version `X.Y.Z`.

It runs the Python and web tests, builds and pushes `omelet-api` and `omelet-web` for amd64 and
arm64 as `X.Y.Z`, then creates `runtime-vX.Y.Z`. The tag is the only place the version lives:
`install.sh` turns it into `OMELET_VERSION` in `/opt/omelet/.env`, which `stack.yml` reads. Nothing
to bump in the repo. VMs pick it up at their next boot.

Every change under `runtime/` needs a release to reach VMs, the in-VM CLI and agent instructions
included. Skills live in `omelet-skills` and are re-installed by every runtime install.

### Changing the API number

Only when a route the host calls changes incompatibly:

1. Bump `API_VERSION` in `runtime/omelet_api/core/constants.py` and `runtime/release.json`.
2. Release a host whose `SUPPORTED_API` includes the new number **before** tagging the runtime,
   or no VM will install it.

The release number (the tag) and `API_VERSION` (the wire protocol) are different numbers. Never
merge them.

## Cut a host release

1. Bump `version` in `pyproject.toml` and `APP_VERSION` in `host/core/constants.py`.
2. Build on each platform (`docs/building.md`): `OmeletSetup-X.Y.Z.exe`,
   `OmeletSetup-X.Y.Z-arm64.pkg`, `OmeletSetup-X.Y.Z-x86_64.pkg`.
3. Put all three in one folder and run `sha256sum OmeletSetup-* > SHA256SUMS`.
4. `gh release create host-vX.Y.Z OmeletSetup-* SHA256SUMS --title "Omelet X.Y.Z"`
   (not `--prerelease`, or no app will offer it).

## First-release checklist

- The repository is public, `runtime/install/get.sh` is on `main`.
- A `runtime-v*` tag with `runtime/release.json` exists.
- ghcr `omelet-api` and `omelet-web` are public, and each package grants this repository
  **Write** under *Manage Actions access* (the workflow pushes with `GITHUB_TOKEN`).
- `github.com/omelet-app/omelet-skills` is public.

## Pin or repair a VM by hand

```powershell
wsl -d omelet-vm -u root -- bash -lc "curl -fsSL https://raw.githubusercontent.com/omelet-app/omelet/main/runtime/install/get.sh | OMELET_RUNTIME_REF=runtime-vX.Y.Z bash"
```

```bash
limactl shell omelet-vm -- sudo bash -lc "curl -fsSL https://raw.githubusercontent.com/omelet-app/omelet/main/runtime/install/get.sh | OMELET_RUNTIME_REF=runtime-vX.Y.Z bash"
```

Use `OMELET_RUNTIME_REPAIR=1` instead to reinstall the current release. A VM on a pinned branch
(not a `runtime-v*` tag) is never moved by the boot update.
