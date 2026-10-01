# Omelet (PoC)

Creates a Linux VM, installs Docker in it, runs any `docker-compose` project inside, and gives you a
working URL on the host. Windows/WSL2 is the main platform; macOS/Lima works but is mostly
unverified ([status](docs/macos-status.md)).

Details for everything below: [docs/](docs/README.md).

## Develop

```bash
python3.12 -m venv .venv && .venv/bin/python -m pip install -e ".[dev]"
.venv/bin/python -m pytest -q                  # Python tests
.venv/bin/python -m pytest -k classify -q      # one test by name

cd runtime/web && npm install
npm run dev                                    # console on a mock API (?scenario=empty, busy, …)
npm test && npm run typecheck
```

## Run

```bash
omelet setup                 # desktop window: builds the VM and installs the runtime
omelet setup --headless      # same, in the terminal
omelet doctor                # what this machine is missing

omelet up ./my-project       # → http://my-project.127-0-0-1.sslip.io:39080
omelet status | logs <id> | down <id> | destroy <id>
omelet vm stop | vm destroy
omelet uninstall --purge     # removes the VM and every project in it
```

On Windows, run from PowerShell, not inside WSL. `powershell -ExecutionPolicy Bypass -File .\setup.ps1`
does the whole dev install in one go.

## Build

```powershell
powershell -ExecutionPolicy Bypass -File .\packaging\windows\build.ps1   # → dist\OmeletSetup-<v>.exe
```

```bash
bash packaging/macos/build.sh          # → dist/OmeletSetup-<v>-<arch>.pkg (native arch, unsigned)
packaging/images/build.sh              # api + web images into local docker
```

## Release the runtime

```bash
# bump the version in the 5 places listed in docs/releasing.md, then:
packaging/images/build.sh --push
git tag runtime-vX.Y.Z && git push origin runtime-vX.Y.Z
```

## Inside the VM

```powershell
wsl -d omelet-vm -u root                       # Windows
```

```bash
limactl shell omelet-vm -- sudo -i             # macOS
```

Projects are in `/opt/omelet/projects/<id>/`. The runtime version is in `/opt/omelet/runtime.version`.
