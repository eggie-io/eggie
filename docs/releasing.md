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

1. Bump the version in all five places (`tests/test_constants_agree.py` checks):
   `runtime/omelet_api/__init__.py`, `runtime/omelet_api/pyproject.toml`,
   `runtime/omelet_api/Dockerfile` (`SERVICE_VERSION`), `runtime/omelet_api/Dockerfile.debug`
   (`SERVICE_IMAGE`), `runtime/stack.yml` (api and web tags).
2. `packaging/images/build.sh --push` (amd64 + arm64; register qemu first).
3. `git tag runtime-vX.Y.Z && git push origin runtime-vX.Y.Z`

VMs pick it up at their next boot.

### Changing the API number

Only when a route the host calls changes incompatibly:

1. Bump `API_VERSION` in `runtime/omelet_api/core/constants.py` and `runtime/release.json`.
2. Release a host whose `SUPPORTED_API` includes the new number **before** tagging the runtime,
   or no VM will install it.

`SERVICE_VERSION` (the release number) and `API_VERSION` (the wire protocol) are different
numbers. Never merge them.

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
- ghcr `omelet-api` and `omelet-web` are public and multi-arch.
- `github.com/ihorklymchukdev/omelet-skills` is public.

## Pin or repair a VM by hand

```powershell
wsl -d omelet-vm -u root -- bash -lc "curl -fsSL https://raw.githubusercontent.com/ihorklymchukdev/local-environment/main/runtime/install/get.sh | OMELET_RUNTIME_REF=runtime-vX.Y.Z bash"
```

```bash
limactl shell omelet-vm -- sudo bash -lc "curl -fsSL https://raw.githubusercontent.com/ihorklymchukdev/local-environment/main/runtime/install/get.sh | OMELET_RUNTIME_REF=runtime-vX.Y.Z bash"
```

Use `OMELET_RUNTIME_REPAIR=1` instead to reinstall the current release. A VM on a pinned branch
(not a `runtime-v*` tag) is never moved by the boot update.
