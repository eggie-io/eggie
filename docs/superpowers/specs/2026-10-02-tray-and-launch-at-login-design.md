# Tray app and launch at login

Date: 2026-10-02
Status: approved design, not implemented
Issue: omelet-app/omelet-resources#44

## 1. Problem

Closing the Omelet window quits the app, which users don't expect. Omelet also
never starts on its own, so after a reboot nothing is running until the user
opens it. This spec turns the desktop app into a tray (Windows) / menu-bar
(macOS) app that can open at login, and ties the VM's lifetime to the app's:
app running = VM running.

Signing comes later. We follow platform best practice now and add no
workarounds for unsigned builds.

## 2. Decisions

| # | Decision | Why |
|---|----------|-----|
| 1 | A tray / menu-bar icon is always present while the app runs. Menu: **Open Omelet**, **Settings**, **Quit Omelet**. | Standard for apps that live past their window. |
| 2 | The window's close button hides the window; it never quits. | The issue. The tray is how the user gets back. |
| 3 | **Quit Omelet** stops the VM, then exits. | No hidden VM using RAM/CPU after the user quit. Same as Docker Desktop. |
| 4 | The quit before an app update exits **without** stopping the VM. | The installer relaunches the app at once; a stop would only add a stop + boot. |
| 5 | Stopping the VM is graceful on both platforms and the host never names Docker. WSL `stop()` asks the guest's systemd to power off, and uses `--terminate` only as the fallback. | Host/runtime seam: the host knows nothing about what runs in the VM. Docker's own shutdown (default 15 s) stops containers when systemd stops it. Fixes the existing **Stop the kitchen** button too, which today pulls the plug. |
| 6 | Quitting while a job runs (install, import, runtime update, VM start/stop) asks for confirmation first. | A half-finished install or import is worse than a slower quit. |
| 7 | Open at login is **on by default**. It is turned on once, when the first setup finishes successfully, and never turned on again. | With Quit stopping the VM, this keeps project URLs working after a reboot. A user who turns it off keeps it off. |
| 8 | The checkbox reads the real state from the OS each time it is shown. The only stored value is "already turned on once". | Turning it off in Task Manager / System Settings must show in Omelet. |
| 9 | Windows: `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`. macOS: `SMAppService.mainApp`. | Per-user, no admin. `SMAppService` is the current Apple API (macOS 13+, our minimum). No LaunchAgent workaround for unsigned builds. |
| 10 | One running instance. A second launch shows the existing window and exits. | Start menu, Finder, the login entry and the post-update relaunch can all start a second copy. |
| 11 | `icon.ico` is the tray, window, `.exe`, installer and `.app` icon. | The user's choice; the user will replace the art later. |
| 12 | Settings is reachable from the tray menu and from a button on the home screens. | Tray-only would hide it from users who never look at the tray. |

## 3. Launch modes

`setup.exe setup` (Windows) / `Omelet.app` (macOS) gain a `--background` flag,
which only the login entry passes.

| Launch | VM exists? | Result |
|--------|------------|--------|
| `--resume` (RunOnce after a reboot during setup) | any | Window, as today. `--resume` wins over `--background`. |
| `--background` | no | Window, as a normal launch. |
| `--background` | yes | Tray only, window hidden. VM start runs on the worker thread. |
| normal | any | Window, as today. |

Here, "VM exists" means `provider.exists()`: the VM exists. It is the test that
separates `home:not_installed` from the other home states in `view.route_for`,
and it is cheap enough to run before the window is created. A VM that exists but
is broken still goes tray-only; its failed start raises the notification below.

If the background VM start fails, the app shows a notification; clicking it (or
**Open Omelet**) shows the window, whose home screen already explains the state.

The decision is a pure function in `host/desktop/lifecycle.py` and is unit-tested.

## 4. Window, tray and quit

### Close button
`window.events.closing` cancels the close and hides the window. On Windows, the
first time this happens on a machine, the tray shows a notification: *"Omelet is
still running. Find it in the system tray."* Whether it was shown is stored in
`settings.json` (section 6).

### macOS conventions
- The red button hides the window; clicking the Dock icon shows it again.
- Cmd+Q is **Quit Omelet**.
- The Dock icon is visible only while the window is visible: the app switches
  between the *regular* and *accessory* activation policies on show/hide.

### Tray menu
- **Open Omelet**: show and focus the window. Windows: a left-click on the icon
  does the same; a right-click shows the menu.
- **Settings**: show the window on the Settings screen. If the window is showing
  the projects console (a page from the VM), it first loads the local UI.
- **Quit Omelet**: section 4, *Quit*.

### Quit
1. If a job is running, show the window on a `quit-confirm` screen
   (*Quit anyway* / *Cancel*). Cancel returns to the previous screen.
2. Show the window on a `quitting` screen: *"Stopping Omelet…"*.
3. If the VM is running, `provider.stop()` on the worker thread.
4. Remove the tray icon, destroy the window, exit.

If `stop()` fails, the app still exits; the failure is printed to stderr.
Blocking quit on a VM that will not stop would leave the user with an app they
cannot close.

The app-update path calls a separate `exit_for_update()` that does step 4 only.
Neither quit path goes through the `closing` handler's hide.

Logoff and shutdown end the app and the VM at the OS level; nothing extra.

## 5. Graceful VM stop (WSL)

`Wsl2Provider.stop()` today is `wsl --terminate <distro>`. New order:

1. `exec(["systemctl", "poweroff"], root=True)`: systemd stops every unit, which
   includes Docker and, through it, the containers (Docker's default
   `shutdown-timeout` is 15 s).
2. Poll `running()` until the distro is gone, for up to 30 s.
3. `wsl --terminate <distro>` regardless. It is a no-op on a stopped distro and
   the fallback for a hung one.

The host names systemd (part of the guest OS the host already configures with
`systemd=true` in `wsl.conf`), never Docker. `LimaProvider.stop()` is unchanged:
`limactl stop` already shuts the guest down cleanly.

Whether `systemctl poweroff` ends a WSL distro, or leaves it for step 3, depends
on the WSL version. Either way the containers have been stopped first. The
manual gate records which happened.

## 6. Settings and open at login

### Settings screen
New `settings` template in the local UI with one row:
**[ ] Open Omelet when I sign in**. A Back button returns to home.

`DesktopApi` gains:
- `get_settings() -> {"autostart": bool, "autostart_available": bool}`
- `set_autostart(on: bool) -> {"ok": bool, "error": str}`

`autostart_available` is false when the app is not a frozen build (a source
checkout has no stable executable to register); the checkbox is then disabled
with a short note.

### Provider methods
Both providers implement:
- `autostart_enabled() -> bool`
- `set_autostart(on: bool, exe_path: str) -> None`

| | On | Off | State |
|---|----|-----|-------|
| Windows | Write the `Omelet` value under `HKCU\...\Run`: `"<exe_path>" setup --background` | Delete the value | The value exists and points at this exe |
| macOS | `SMAppService.mainApp.registerAndReturnError_` | `unregisterAndReturnError_` | `status == enabled` |

On macOS the login item launches the app with no arguments; the app tells a
login launch from a user launch by checking whether it was opened as a login
item (`NSAppleEventManager` current event's `keyAELaunchedAsLogInItem`), and
treats that as `--background`. This check lives in the provider.

### Stored settings
`settings.json` next to `install-state.json`:

```json
{"autostart_set_once": true, "tray_notice_shown": true}
```

Separate from `install-state.json` on purpose: resetting or removing the VM
deletes that file, which would re-arm the "turn on once" rule. Missing or
unreadable file = all false.

### Turn on once
When the install job finishes successfully: if `autostart_set_once` is false and
autostart is available, call `set_autostart(True)` and set the flag. A failure
is ignored (the user can still turn it on in Settings) but the flag is still set,
so a broken registry is not retried on every repair.

## 7. Single instance

| | Mechanism |
|---|---|
| Windows | The first instance listens on a per-user named pipe, `\\.\pipe\omelet-<username>`, through the standard library's `multiprocessing.connection` (`AF_PIPE`, with an authkey). A second instance connects, sends `show`, and exits 0. Pipe names are machine-wide, hence the user name: another user's Omelet must not answer. If the connect fails, there is no first instance and this one runs normally. |
| macOS | LaunchServices already keeps one instance of a `.app`. A second open fires the reopen event, handled as **Open Omelet**. |

`--background` on a second instance does nothing (no `show`).

## 8. Packaging

- `icon.ico` → `host/desktop/resources/icon.ico`, bundled as data (both specs) so
  the tray can load it at runtime.
- Windows spec: `icon=` on both executables. Installer: `SetupIconFile`.
- macOS spec: `icon=` on `BUNDLE`. PyInstaller converts `.ico` to `.icns` with
  Pillow at build time.
- New dependencies: `pystray` and `Pillow`, `sys_platform == 'win32'`. Pillow is
  added to the `dev` extra for the macOS build's icon conversion.
- `omelet uninstall --purge` (run by the Inno uninstaller and by
  `packaging/macos/uninstall.sh`) turns autostart off before destroying the VM.

## 9. Where the code goes

Platform code stays in `host/providers/` (`tests/test_no_platform_leak.py`).

| Unit | Role |
|------|------|
| `host/desktop/lifecycle.py` | Pure: launch mode from args + install state; "turn on once" decision. |
| `host/desktop/settings.py` | `settings.json` read/write (same pattern as `InstallState`). |
| `host/providers/tray_win.py`, `tray_mac.py` | The tray per platform, behind `provider.tray(on_open, on_settings, on_quit)` returning an object with `start()`, `stop()`, `notify(text)`. |
| `host/providers/wsl2.py`, `lima.py` | `autostart_enabled`, `set_autostart`, `single_instance(on_show)`, `launched_at_login()`, window-visibility hook (`on_window_shown(bool)`, macOS Dock policy), graceful `stop()` (WSL). |
| `host/desktop/__main__.py` | Wiring: flags, single instance, hidden window, `closing` handler, tray, both quit paths. |
| `host/desktop/api.py` | `get_settings`, `set_autostart`, `quit`, `cancel_quit`; turn-on-once after install; update path calls `exit_for_update`. |
| `host/desktop/ui/` | `settings`, `quitting`, `quit-confirm` screens; Settings button on the home screens; `#settings` route for the tray. |

**Threading.** `webview.start()` owns the main thread. On Windows `pystray` runs
on its own thread. On macOS AppKit requires the main thread, so the
`NSStatusItem` is created and updated on pywebview's Cocoa loop
(`AppHelper.callAfter`); `pystray` is not used there.

## 10. Testing

Unit tests (no real tray, registry, or VM; fakes as elsewhere):
- Launch mode: every row of the section 3 table.
- `Run` value: exact quoted string, including a path with spaces.
- Turn on once: the first successful install turns it on; a later install,
  repair or update does not after the user turned it off; a failing
  `set_autostart` still sets the flag.
- `settings.json`: missing / corrupt file reads as all false; writing one key
  keeps the other.
- Quit paths: **Quit Omelet** calls `provider.stop()` when the VM runs and exits
  even when `stop()` raises; `exit_for_update` never calls `stop()`.
- WSL `stop()`: argv order is poweroff → poll → `--terminate`; `--terminate` is
  issued even when poweroff fails.
- Single instance: a second instance sends `show` and exits; with no first
  instance, startup proceeds; `--background` on a second instance sends nothing.

Not unit-tested: pystray, AppKit, `SMAppService`, the registry itself. These are
manual gates added to `docs/release-testing.md` for both OSes: close hides to
tray, tray menu items, first-close notification, login launch after sign-out /
reboot, open at login on by default after the first setup and staying off once
turned off, second launch focuses the first, Quit stops the VM, update while
running, uninstall while running.

## 11. Known limits

- Tray, login launch and `SMAppService` are verified only by the manual gates;
  this repo's environment cannot run either GUI.
- `SMAppService` may not register reliably until the app is signed.
- `icon.ico` holds one 32 × 32 image: soft at large sizes and as the macOS
  `.app` icon; on a light macOS menu bar the cream egg has low contrast. The
  art will be replaced manually.

## 12. Out of scope

- Code signing and notarization.
- Settings beyond open at login.
