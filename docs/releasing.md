# Releasing the runtime

The host (installer) and the runtime (everything inside the VM) release separately. A host never
bundles the runtime. When it sets up a VM, it fetches `runtime/install/get.sh` from **`main`**,
and `get.sh` installs the highest `runtime-vX.Y.Z` tag. The VM then pulls the image tags that
tag's `runtime/stack.yml` names. So a new image reaches nobody until a new tag points at it.

**Keep `get.sh` on `main` backward compatible.** Every shipped host runs it. Its env vars
(`OMELET_RUNTIME_REPO`, `OMELET_RUNTIME_REF`, `OMELET_RUNTIME_REPAIR`), the
`/opt/omelet/runtime.version` marker and its exit codes are a contract.

## Cutting a release

1. **Bump the version in all five places at once.** `tests/test_constants_agree.py` fails if any
   of them differ:
   - `runtime/omelet_api/__init__.py` — `__version__`
   - `runtime/omelet_api/pyproject.toml` — `version`
   - `runtime/omelet_api/Dockerfile` — `SERVICE_VERSION`
   - `runtime/omelet_api/Dockerfile.debug` — the `SERVICE_IMAGE` default tag
   - `runtime/stack.yml` — both the `omelet-api` and `omelet-web` image tags
2. **Build and push both images for amd64 and arm64:**
   ```bash
   packaging/images/build.sh --push
   ```
3. **Tag.** `get.sh` ignores every tag that isn't `runtime-vN.N.N`:
   ```bash
   git tag runtime-vX.Y.Z && git push origin runtime-vX.Y.Z
   ```

### `SERVICE_VERSION` is not `API_VERSION`

The version above is the release number. `API_VERSION` in `runtime/omelet_api/core/constants.py`
is the wire-protocol number the host checks against its `SUPPORTED_API`. Bump it only when a
route the host calls changes incompatibly, and that change also needs a new host release. Keep
the two numbers separate.

## First-release checklist

These must all be true before any shipped host can install anything. `install.sh` fails the
whole install if any of them is false:

- The GitHub repository is public, and `runtime/install/get.sh` is on `main`.
- At least one `runtime-v*` tag exists.
- Both ghcr packages (`omelet-api`, `omelet-web`) are **public**. ghcr makes a newly pushed
  package private, so flip each one by hand in the package settings. Both are multi-arch.
- `github.com/ihorklymchukdev/omelet-skills` is public and holds the five skill folders.
  `install.sh` installs them with `npx skills add`; override the source with
  `OMELET_SKILLS_SOURCE`.

## Moving an installed VM to a new release

Re-running setup does nothing once the runtime is installed. **Repair** reinstalls the ref that
is *already* installed, so it doesn't upgrade. To move to the newest `runtime-v*` tag, run
`get.sh` in the VM without the repair flag. It pulls the new images and recreates the containers
whose image changed.

```powershell
# Windows
wsl -d omelet-vm -u root -- bash -lc "curl -fsSL https://raw.githubusercontent.com/ihorklymchukdev/local-environment/main/runtime/install/get.sh | bash"
```

```bash
# macOS
limactl shell omelet-vm -- sudo bash -lc "curl -fsSL https://raw.githubusercontent.com/ihorklymchukdev/local-environment/main/runtime/install/get.sh | bash"
```

To pin a release, put `OMELET_RUNTIME_REF=runtime-vX.Y.Z` before `bash`. To reinstall the current
ref in place, put `OMELET_RUNTIME_REPAIR=1` there instead.
