# runtime/ — everything inside the VM

Released independently of the host as `runtime-vX.Y.Z` tags. A change here must never need a host
release — if it does, the logic is on the wrong side of the seam (see the root CLAUDE.md).

## What lives here

- `install/` — `get.sh` (the entrypoint every shipped host fetches) and `install.sh`. Own CLAUDE.md.
- `stack.yml` — the compose stack installed at `/opt/omelet/stack.yml`: `traefik` on the edge port,
  `api` (`omelet-api` image) and `web` (`omelet-web` image) on the `edge` network. Image tags are
  overridable with `OMELET_API_IMAGE` / `OMELET_WEB_IMAGE`. A fourth service, `tunnel`
  (cloudflared, for public URLs), sits behind the `tunnel` compose **profile**: a plain `up -d` or
  `pull` never touches it, so pass `--profile tunnel` when you mean to include it. The api starts
  and stops it; `install.sh` pulls it up front. `stack.debug.yml` is an override that
  swaps in the debugpy api build; `stack.yml` itself must stay identical to what runs in production.
- `omelet_api/` — the FastAPI service. Own CLAUDE.md.
- `web/` — the browser console (npm workspace, `omelet-web` nginx image). Own CLAUDE.md.
- `cli/omelet.py` — the `omelet` command **inside** the VM (`/usr/local/bin/omelet`), used by coding
  agents: `up`/`new`/`clone`/`status`/`logs`/`down` over the API with the guest token. **One
  stdlib-only file**: it can import neither `host/` nor `omelet_api`, so shared names are
  re-declared and held equal by `tests/test_constants_agree.py`. Tests load it by path
  (`tests/runtime/cli/loader.py`).
- `instructions/` — what coding agents read (installed as `/etc/claude-code/CLAUDE.md` etc.). The
  five skills live in the separate `omelet-skills` repo (github.com/omelet-app/omelet-skills)
  and are installed by `install.sh` via `npx skills add`; they are not in this tree. The runtime
  writes nothing into user repositories.

## Two version numbers — never collapse them

- `SERVICE_VERSION` — the image/package release number. Lives in `omelet_api/__init__.py`
  (`__version__`), `omelet_api/pyproject.toml`, `omelet_api/Dockerfile` (`SERVICE_VERSION`),
  `omelet_api/Dockerfile.debug` (`SERVICE_IMAGE` default) and **both** image tags in `stack.yml`.
  Bumped together every release; `tests/test_constants_agree.py` fails on any drift.
- `API_VERSION` (`omelet_api/core/constants.py`) — the wire-protocol number the host checks against
  its `SUPPORTED_API`. Bump only when a route the host calls changes incompatibly; that needs a host
  release too. The console's `SUPPORTED_API` (`web/apps/console/src/api/version.ts`) follows it.

## Releasing (manual until CI exists)

1. Bump the `SERVICE_VERSION` set above together.
2. `packaging/images/build.sh --push` — builds and pushes `omelet-api` and `omelet-web` for
   `linux/amd64` and `linux/arm64` (needs `docker buildx` + `docker login ghcr.io`; refuses if the
   versions disagree). Variants: no `--push` (native arch, local docker), `--only web`,
   `--tag dev`.
3. `git tag runtime-vX.Y.Z && git push origin runtime-vX.Y.Z` — only `runtime-v*` tags count.

Preconditions for any shipped host to install anything: the repo is public; `install/get.sh` is on
`main` (hosts fetch it from `main`, not a tag); at least one `runtime-v*` tag exists; both ghcr
packages are **public** (ghcr makes a newly pushed package private — flip it by hand) and
multi-arch; `omelet-skills` is public with its five skill folders. `install.sh` fails the whole
install on any image it can't pull or a failed `npx skills add`.

## Updating an installed VM

The VM updates itself at boot; see `docs/releasing.md`. To try a `--tag dev` image,
`OMELET_WEB_IMAGE=… docker compose -f /opt/omelet/stack.yml up -d web` inside the VM; the next
update puts the released image back.
