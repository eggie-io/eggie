# Development

## Python (host + API)

Needs Python **3.12+**. On macOS, `python3` may be an older version; use the venv.

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"    # host deps + pytest + PyInstaller
```

The API's dependencies aren't part of `.[dev]`, which covers only the host. They're declared in
`runtime/omelet_api/pyproject.toml`. The API tests also need `httpx`, because FastAPI's
`TestClient` uses it:

```bash
.venv/bin/python -m pip install "fastapi>=0.110" "uvicorn[standard]>=0.30" pyyaml httpx
```

```bash
.venv/bin/python -m pytest -q                                   # everything, under a minute
.venv/bin/python -m pytest tests/runtime/api/test_project.py -q # one file
.venv/bin/python -m pytest -k classify -q                       # by name
```

Tests never start a VM or use the network. Providers get a fake runner, and the runtime shell
scripts run under `bash` with fake tools on `PATH`.

### Known failures on a macOS dev machine

The suite targets Linux/WSL and Python 3.12. On macOS with Python 3.13, 11 tests fail, and none
of them point at a product bug:

| Tests | Why |
|---|---|
| `tests/runtime/test_install_agents.py`, `test_get_sh.py` | The guest scripts are written for GNU `sed`/`tar`; macOS ships BSD versions |
| `tests/runtime/test_github_apply.py` | `${1,,}` needs bash 4+; macOS `/bin/bash` is 3.2 |
| `tests/runtime/api/test_health.py` (one case) | Python 3.13 formats IPv4-mapped addresses differently from 3.12 |
| `test_api_auth.py`, `test_api_browser_auth.py` (route sweeps) | They find fewer routes under the installed FastAPI version |

Run the suite on Linux/WSL before trusting a red result from a Mac.

In the WSL sandbox, `/tmp/pytest-of-$USER` can be owned by root, which breaks `tmp_path`. Run with
`TMPDIR=<a writable dir>`.

### Running the host from a checkout

```bash
omelet doctor
omelet setup              # opens the desktop window
omelet setup --headless   # terminal only
python -m host.desktop    # the window on its own
```

On Windows, run these from Windows (PowerShell), not from a WSL shell. The host drives `wsl.exe`,
and from inside WSL it fails with `unsupported host platform: linux`. `setup.ps1` does a full dev
install: it finds a Python, builds `.venv`, downloads the Ubuntu rootfs into
`%LOCALAPPDATA%\Omelet\cache`, creates the VM and drops an `omelet.cmd` shim in the repo. Flags:
`-Rootfs <path>` reuses a rootfs you already have; `-NoCreate` skips creating the VM.

To install a runtime other than the latest release, set `OMELET_RUNTIME_REF=<branch|tag>`. To
fetch the installer from somewhere else, set `OMELET_RUNTIME_URL`.

## Web console (`runtime/web/`)

Needs Node **22.22+**.

```bash
cd runtime/web
npm install
npm run dev              # Vite + an in-browser mock API
npm test                 # Vitest
npm run typecheck
npm run build && npm run check-offline
```

The dev server's mock takes `?scenario=<name>` in the URL to show a given state (empty list,
expired session, uploads, public URL, GitHub connect, …). The full list is `SCENARIOS` in
`apps/console/src/mocks/handlers.ts`.

`npm run build` + `preview` shows no "Connect an agent" guides, because the Dockerfile copies them
in. Build the image to see them (`packaging/images/build.sh --only web`).

## Debugging the API inside a VM

`runtime/stack.debug.yml` replaces the api service with a debugpy build and publishes port 5678.
Inside the VM, as root:

```bash
. /opt/omelet/.env && docker build -t omelet-api:debug \
  --build-arg SERVICE_IMAGE=ghcr.io/omelet-app/omelet-api:$OMELET_VERSION \
  - < /opt/omelet/runtime/omelet_api/Dockerfile.debug
docker compose -f /opt/omelet/stack.yml -f /opt/omelet/runtime/stack.debug.yml up -d
```

Then attach to `127.0.0.1:5678` from the host. On WSL2, localhost forwarding makes that port
reachable.

To try a web image you pushed with `--tag dev` without cutting a release:

```bash
OMELET_WEB_IMAGE=ghcr.io/omelet-app/omelet-web:dev \
  docker compose -f /opt/omelet/stack.yml up -d web
```

The next `get.sh` run puts the released image back.
