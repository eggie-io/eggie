# runtime/ — everything inside the VM

Released independently of the host as `runtime-vX.Y.Z` tags. A change here must never need a host
release — if it does, the logic is on the wrong side of the seam (see the root CLAUDE.md).

## What lives here

- `install/` — `get.sh` (the entrypoint every shipped host fetches) and `install.sh`. Own CLAUDE.md.
- `stack.yml` — the compose stack installed at `/opt/eggie/stack.yml`: `traefik` on the edge port,
  `api` (`eggie-api` image) and `web` (`eggie-web` image) on the `edge` network. Image tags are
  overridable with `EGGIE_API_IMAGE` / `EGGIE_WEB_IMAGE`. A fourth service, `tunnel`
  (cloudflared, for public URLs), sits behind the `tunnel` compose **profile**: a plain `up -d` or
  `pull` never touches it, so pass `--profile tunnel` when you mean to include it. The api starts
  and stops it; `install.sh` pulls it up front. `stack.debug.yml` is an override that
  swaps in the debugpy api build; `stack.yml` itself must stay identical to what runs in production.
- `eggie_api/` — the FastAPI service. Own CLAUDE.md.
- `web/` — the browser console (npm workspace, `eggie-web` nginx image). Own CLAUDE.md.
- `cli/eggie_cli/` — the `eggie` command **inside** the VM, used by coding agents:
  `up`/`new`/`clone`/`status`/`logs`/`down`/`secret` over the API with the guest token.
  `install.sh` builds it into the one file `/usr/local/bin/eggie` with `python3 -m zipapp`, so
  `runtime/cli/` must hold nothing but the package (it is the zip's root, first on `sys.path`).
  **Stdlib only**: it can import neither `host/` nor `eggie_api`, so shared names are
  re-declared and held equal by `tests/test_constants_agree.py`. Modules depend one way:
  `cli → commands | secrets → env → api | project → errors | constants`; tests load the barrel
  through `tests/runtime/cli/loader.py`, and `test_boundaries.py` builds and runs the zip.
- `instructions/` — what coding agents read (installed as `/etc/claude-code/CLAUDE.md` etc.). The
  five skills live in the separate `eggie-skills` repo (github.com/eggie-io/eggie-skills)
  and are installed by `install.sh` via `npx skills add`; they are not in this tree. The runtime
  writes nothing into user repositories.
- `agents/` — one manifest folder per coding agent (guide, instructions targets, skills name,
  detection, setup). Own CLAUDE.md.

## Two version numbers — never collapse them

- The release number — lives **only** in the `runtime-vX.Y.Z` tag. The release workflow builds
  both images as `X.Y.Z` (baked into the api as `EGGIE_SERVICE_VERSION`), `install.sh` writes
  `EGGIE_VERSION=X.Y.Z` into `/opt/eggie/.env` (`install/lib/image-version.sh`), and `stack.yml`'s
  image tags read it. The `0.0.0` in `eggie_api/__init__.py`, `pyproject.toml` and the
  Dockerfile is a checkout placeholder — never bump it.
- `API_VERSION` (`eggie_api/constants.py`) — the wire-protocol number the host checks against
  its `SUPPORTED_API`. Bump only when a route the host calls changes incompatibly; that needs a host
  release too. The console's `SUPPORTED_API` (`web/apps/console/src/api/version.ts`) follows it.

## Releasing

Run the **Release runtime** workflow (`.github/workflows/release-runtime.yml`) on `main` with the
version: tests, `packaging/images/build.sh --push --version X.Y.Z` (amd64 + arm64), then the
`runtime-vX.Y.Z` tag — **tag last**, because VMs install a tag the moment they see it
(`tests/test_release_workflow.py`). Locally, `build.sh` without `--push` builds native-arch
images; `--push --tag dev --only web` pushes a throwaway image to try in a VM.

Preconditions for any shipped host to install anything: the repo is public; `install/get.sh` is on
`main` (hosts fetch it from `main`, not a tag); at least one `runtime-v*` tag exists; both ghcr
packages are **public** (ghcr makes a newly pushed package private — flip it by hand) and
multi-arch; `eggie-skills` is public with its five skill folders. `install.sh` fails the whole
install on any image it can't pull or a failed `npx skills add`.

## Updating an installed VM

The VM updates itself at boot; see `docs/releasing.md`. To try a `--tag dev` image,
`EGGIE_WEB_IMAGE=… docker compose -f /opt/eggie/stack.yml up -d web` inside the VM; the next
update puts the released image back.
