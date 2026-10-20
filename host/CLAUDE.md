# host/ — the VM shell

The desktop binary that runs on Windows/macOS. It creates and runs the VM, bootstraps the runtime,
forwards ports and talks to the in-VM API over HTTP. Everything that happens *inside* the VM belongs
in `runtime/`, not here. Tests: `tests/host/`, plus the top-level `tests/test_*_cli.py`.

## Hard constraints

- **Frozen with PyInstaller.** Every declared dependency lands in the binary, so the list is fixed:
  `typer`, `certifi`, `pywebview`, and pywebview's native backends — `pythonnet` plus the tray's
  `pystray`/`Pillow` (Windows) and four `pyobjc-*` packages (macOS), marker-scoped in
  `pyproject.toml` so neither installs on the other platform.
  `tests/host/test_host_dependencies.py` fails on anything else declared *or imported*. No HTTP
  library: the client is stdlib `urllib.request`. FastAPI/uvicorn must never appear.
- **Never import `eggie_api`** (`tests/host/test_no_api_import.py`). Shared values are duplicated
  in `host/core/constants.py` and held equal by `tests/test_constants_agree.py`.
- **Platform is resolved only in `host/providers/__init__.py`** (`get_provider()`, `get_desktop()`,
  `default_install_dir()`). No `sys.platform` / `os.name` / `platform.system()` anywhere else — add
  a provider method instead (`tests/test_no_platform_leak.py`).
- **No guest assets.** `host/provision/` holds only the installer's `nginx-hello` smoke-test project
  (`tests/host/test_no_guest_assets.py`). Anything the VM needs ships with the runtime.

## Layers

- `core/provider.py` — the `VmProvider` Protocol plus `Completed` / `CheckResult` / `Diagnosis`
  value types, and the `DesktopPlatform` Protocol. Both are **duck-typed, not subclassed**.
- `providers/` — `Wsl2Provider` (shells `wsl.exe`) and `LimaProvider` (shells `limactl`, VM defined
  by `eggie.yaml`; its CPUs and memory
  are not in the file but passed by `create()`, sized from the Mac by `vm_size()`). Both take an injectable `runner` callable (default
  `subprocess.run(..., capture_output=True)`) — that is what makes them unit-testable.
  `lima.py` and `eggie.yaml` carry banners saying exactly what has and hasn't been run on a real
  Mac; keep them accurate when you change either.
  `desktop_win.py` / `desktop_mac.py` — `WindowsDesktop` / `MacDesktop`, what the window asks of
  the OS (`tray`, `single_instance`, `autostart_*`, `watch_login_launch`, `on_window_shown`,
  `let_session_end_close`, `style_title_bar`);
  `tests/host/test_provider_surface.py::DESKTOP_SURFACE` pins it there and asserts the VM
  providers carry none of it. macOS-only and Windows-only imports stay
  function-local so the suite imports every provider on Linux.
- `core/bootstrap.py` — the host's entire share of provisioning. Unless
  `/opt/eggie/runtime.version` exists (or `repair=True`) it runs a base64 stub as one `bash -lc`
  argument that downloads `EGGIE_RUNTIME_URL` in full and runs it, forwarding `EGGIE_RUNTIME_REF`
  when set and `EGGIE_RUNTIME_REPAIR=1` on repair. Values are checked against a shell-safe pattern
  because the argument is re-parsed by `wsl.exe`/`ssh`.
- `client.py` — `ApiClient`, the only way the host reaches project logic. Reads the token through
  `provider.exec()`; maps every non-2xx body to `ApiError(code, message, status)`, a refused
  connection to `ApiUnavailableError`, a failed job to `JobFailedError` carrying the guest's stderr.
  The host checks `/health`'s `api` against `constants.SUPPORTED_API`.
- `core/install.py`, `core/status.py`, `core/diagnose.py` — the setup steps, readiness probe and
  doctor, shared by the CLI and the desktop app.
- `cli.py` — the typer app (`eggie setup|doctor|vm|port|up|status|logs|down|destroy|uninstall|
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
- `eggie vm create` on WSL needs `EGGIE_ROOTFS` (Ubuntu 24.04 `.wsl` image; README has the URLs)
  as a **Windows** path — it goes straight to `wsl.exe --import`. Without it `ValueError`.
- Packaging (`packaging/windows/build.ps1`, `packaging/macos/build.sh`) freezes one-dir and
  smoke-tests the frozen binary (`version`, then `selfcheck`) before packaging. The mac build is
  native-arch only.

## Things that will bite you

- WSL's service can hang (`Wsl/Service/CreateInstance/0x8007274c`, often after sleep) while
  `wsl -l --running` still lists `eggie-vm` — "listed as running" never proves it answers.
  `Wsl2Provider` raises `VmUnresponsive` from `exec()` and `_meta()` on that code; `recover()`
  tries `--terminate` first. Only `--shutdown` is sure to clear it, and that stops every distro and
  Docker Desktop, so the desktop app asks before using it.
- The imported WSL distro runs as root: `create()` replaces `/etc/wsl.conf` with a
  `[boot] systemd=true` stanza, dropping the image's default user. It may truncate only because
  it runs right after a successful `--import`. The runtime owns everything else in the file:
  `[user]` (`lib/wsl-user.sh`) and `[automount]`/`[interop]` (`lib/wsl-conf.py`). Never write
  those sections from the host; a change there must not need a host release.
- wsl.conf applies only when the distro boots, so setup restarts a VM it just created, once,
  after the first bootstrap (`restart_after_first_bootstrap`, `restart_new_vm`). The debt is a
  `vm-restart-owed` file in the cache dir, so a setup that fails before the restart pays it on the
  re-run. Never restart a VM for it otherwise: projects and agents may be running.
- Every `wsl -d` the provider runs passes `--cd ~`. Without it WSL maps the host process's Windows
  folder into the VM, which has no drive mounts, so it prints a warning and falls back to home.
  Older hosts rely on that fallback against updated VMs.
- Lima gives cloud-init a new `instance-id` on every `limactl start`, and cloud-init regenerates the
  SSH host keys on a new instance, so a stale `~/.ssh/known_hosts` entry blocks SSH after a restart
  or reinstall. Turning off host-key checking does not help: Cursor's Remote-SSH reads known_hosts
  itself. The runtime keeps the keys (`ssh_deletekeys: false`, Lima only). Setup's `ssh_alias` step
  and every `start()` replace the known_hosts entries with the guest's real keys
  (`providers/ssh_alias.py`). `Host eggie` is written into `~/.ssh/config` itself, because
  Cursor's host list ignores `Include`.
