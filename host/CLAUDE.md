# host/ — the VM shell

The desktop binary that runs on Windows/macOS. It creates and runs the VM, bootstraps the runtime,
forwards ports and talks to the in-VM API over HTTP. Everything that happens *inside* the VM belongs
in `runtime/`, not here. Tests: `tests/host/`, plus the top-level `tests/test_*_cli.py`.

## Hard constraints

- **Frozen with PyInstaller.** Every declared dependency lands in the binary, so the list is fixed:
  `typer`, `pywebview`, and pywebview's native backends — `pythonnet` (Windows) and three `pyobjc-*`
  packages (macOS), marker-scoped in `pyproject.toml` so neither installs on the other platform.
  `tests/host/test_host_dependencies.py` fails on anything else declared *or imported*. No HTTP
  library: the client is stdlib `urllib.request`. FastAPI/uvicorn must never appear.
- **Never import `omelet_api`** (`tests/host/test_no_api_import.py`). Shared values are duplicated
  in `host/core/constants.py` and held equal by `tests/test_constants_agree.py`.
- **Platform is resolved only in `host/providers/__init__.py`** (`get_provider()`,
  `default_install_dir()`). No `sys.platform` / `os.name` / `platform.system()` anywhere else — add
  a provider method instead (`tests/test_no_platform_leak.py`).
- **No guest assets.** `host/provision/` holds only the installer's `nginx-hello` smoke-test project
  (`tests/host/test_no_guest_assets.py`). Anything the VM needs ships with the runtime.

## Layers

- `core/provider.py` — the `VmProvider` Protocol plus `Completed` / `CheckResult` / `Diagnosis`
  value types. Providers are **duck-typed against the Protocol, not subclasses**.
- `providers/` — `Wsl2Provider` (shells `wsl.exe`) and `LimaProvider` (shells `limactl`, VM defined
  by `omelet.yaml`). Both take an injectable `runner` callable (default
  `subprocess.run(..., capture_output=True)`) — that is what makes them unit-testable.
  `lima.py` and `omelet.yaml` carry banners saying exactly what has and hasn't been run on a real
  Mac; keep them accurate when you change either.
  Desktop surface (`autostart_*`, `single_instance`, `watch_login_launch`, `on_window_shown`,
  `tray`) is pinned by `tests/host/test_provider_surface.py::DESKTOP_SURFACE`. macOS-only and
  Windows-only imports stay function-local so the suite imports every provider on Linux.
- `core/bootstrap.py` — the host's entire share of provisioning. Unless
  `/opt/omelet/runtime.version` exists (or `repair=True`) it runs a base64 stub as one `bash -lc`
  argument that downloads `OMELET_RUNTIME_URL` in full and runs it, forwarding `OMELET_RUNTIME_REF`
  when set and `OMELET_RUNTIME_REPAIR=1` on repair. Values are checked against a shell-safe pattern
  because the argument is re-parsed by `wsl.exe`/`ssh`.
- `client.py` — `ApiClient`, the only way the host reaches project logic. Reads the token through
  `provider.exec()`; maps every non-2xx body to `ApiError(code, message, status)`, a refused
  connection to `ApiUnavailableError`, a failed job to `JobFailedError` carrying the guest's stderr.
  The host checks `/health`'s `api` against `constants.SUPPORTED_API`.
- `core/install.py`, `core/status.py`, `core/diagnose.py` — the setup steps, readiness probe and
  doctor, shared by the CLI and the desktop app.
- `cli.py` — the typer app (`omelet setup|doctor|vm|port|up|status|logs|down|destroy|uninstall|
  selfcheck`). `setup` launches the desktop GUI; `--headless` bypasses it for a machine with no
  webview runtime. `selfcheck` is what the packaging scripts run against the frozen binary.
- `desktop/` — the GUI; has its own CLAUDE.md (security-sensitive).

## Testing

Provider tests inject a `FakeRunner` that records argv and returns scripted bytes; CLI tests
monkeypatch `cli._provider_factory` with a `FakeProvider`. Assert on constructed argv and decoded
output. Nothing may spawn `wsl.exe`/`limactl`.

## Running for real

- The CLI must run on **Windows, not inside WSL** — it drives `wsl.exe`, and `get_provider()` raises
  `unsupported host platform: linux` from a WSL shell.
- `omelet vm create` on WSL needs `OMELET_ROOTFS` (Ubuntu 24.04 `.wsl` image; README has the URLs)
  as a **Windows** path — it goes straight to `wsl.exe --import`. Without it `ValueError`.
- Packaging (`packaging/windows/build.ps1`, `packaging/macos/build.sh`) freezes one-dir and
  smoke-tests the frozen binary (`version`, then `selfcheck`) before packaging. The mac build is
  native-arch only.

## Things that will bite you

- WSL's service can hang (`Wsl/Service/CreateInstance/0x8007274c`, often after sleep) while
  `wsl -l --running` still lists `omelet-vm` — "listed as running" never proves it answers.
  `Wsl2Provider` raises `VmUnresponsive` from `exec()` and `_meta()` on that code; `recover()`
  tries `--terminate` first. Only `--shutdown` is sure to clear it, and that stops every distro and
  Docker Desktop, so the desktop app asks before using it.
- The imported WSL distro runs as root: `create()` replaces `/etc/wsl.conf` with a
  `[boot] systemd=true` stanza, dropping the image's default user.
- Lima gives cloud-init a new `instance-id` on every `limactl start`, so the guest's SSH host keys
  change on every boot. Anything that pins them in `~/.ssh/known_hosts` breaks after a restart or
  reinstall. Setup's `ssh_alias` step (`providers/ssh_alias.py`) adds `Host omelet` to the user's
  `~/.ssh/config` with host-key checking off. That's safe only because the port is loopback-only.
