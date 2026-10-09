# Eggie (PoC)

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
eggie setup                 # desktop window: builds the VM and installs the runtime
eggie setup --headless      # same, in the terminal
eggie doctor                # what this machine is missing

eggie up ./my-project       # → http://my-project.local.eggie.space:39080
eggie status | logs <id> | down <id> | destroy <id>
eggie vm stop | vm destroy
eggie uninstall --purge     # removes the VM and every project in it
```

On Windows, run from PowerShell, not inside WSL. `powershell -ExecutionPolicy Bypass -File .\setup.ps1`
does the whole dev install in one go.

## Build

```powershell
powershell -ExecutionPolicy Bypass -File .\packaging\windows\build.ps1   # → dist\EggieSetup-<v>.exe
```

```bash
bash packaging/macos/build.sh          # → dist/EggieSetup-<v>-<arch>.pkg (native arch, unsigned)
packaging/images/build.sh              # api + web images into local docker
```

## Release

GitHub → Actions → **Release runtime** or **Release app** → Run workflow on `main`. Details in
[docs/releasing.md](docs/releasing.md).

## Inside the VM

```powershell
wsl -d eggie-vm -u root --cd ~                # Windows
```

```bash
limactl shell eggie-vm -- sudo -i             # macOS
```

Projects are in `/opt/eggie/projects/<id>/`. The runtime version is in `/opt/eggie/runtime.version`.
