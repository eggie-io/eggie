# Release testing (manual)

CI doesn't cover any of this. Run these checks by hand on real or virtual machines before an
installer ships. For each run, record the date, the build and what you actually saw. Don't mark a
case passed without that record.

The previous matrices (`installer-test-matrix.md`, `macos-install-test-matrix.md`) were written
for the tkinter setup window that the webview desktop app has since replaced. Their old results
are in git history at commit `7204c3d`. Every case below is **UNRUN against the current app**.

⚠️ Cases marked **destructive** delete the VM and every project in it. Run them only where that's
disposable.

## Windows (`EggieSetup-<version>.exe`)

| # | Machine / action | Pass when | Result |
|---|---|---|---|
| W1 | WSL2 already enabled; run the installer | Setup completes; the test project answers 200; the window moves on to the projects console | |
| W2 | **Clean Windows, WSL2 never enabled** (needs nested virtualization in a VM). This is the release gate | One UAC prompt; after the restart, setup resumes on its own and completes | |
| W3 | Virtualization disabled in firmware | Setup stops at preflight with the BIOS instruction and no stack trace | |
| W4 | Run setup again on a provisioned machine | Every step reports skipped or finishes quickly; exits 0 | |
| W5 | Machine without WebView2 | The installer installs it; the window opens | |
| W6 | Watch the window during W1–W4 | Progress shows each step; failures show the guest error and what to do; closing mid-install exits non-zero; no console windows flash | |
| W7 | `eggie.exe selfcheck` from the *installed* copy | Prints OK for every asset, exits 0 | |
| W8 | **Destructive.** Uninstall from Apps & Features | A confirmation appears first (declining leaves everything in place); afterwards `wsl -l -v` has no `eggie-vm` and the cache is gone | |
| W9 | PATH before and after W8 (`reg query HKCU\Environment /v Path`) | No other entry changed. The app's own leftover segment is expected (see `installer.iss`) | |
| W10 | Update from the previous host release via the **Update** button | The window closes on its own after the download; the installer runs and reopens the app with the new version in the footer | |
| W11 | Boot a VM with a newer compatible runtime tag published | `runtime.version` moves; projects still run | |
| W12 | Boot with the network off | The VM starts on its old runtime; `journalctl -u eggie-update` explains why | |
| W13 | After W1, search the Start menu for "Eggie" and open Task Manager while the app runs | The Start menu entry is **Eggie**; Task Manager's Apps list shows **Eggie** (Details tab: `eggie-app.exe`). Nothing says "Setup" or "setup" | |
| W14 | **VM can't reach Windows**, right after W1 (no restart in between) and again on a VM set up by the previous release once W11 updated it and it has restarted (`wsl --terminate eggie-vm`, then open Eggie). In PowerShell: `wsl -d eggie-vm -- ls /mnt/c`; `wsl -d eggie-vm -- cmd.exe /c ver`; `wsl -d eggie-vm -- bash -lc 'echo $PATH'`; `wsl -d eggie-vm -- whoami`; `wsl -d eggie-vm -- sudo -n true; echo $LASTEXITCODE` | `ls` lists nothing or says no such directory; `cmd.exe` fails (not found, or "Exec format error") and prints no Windows version; `PATH` has no `/mnt/c/...` entry; `whoami` says `eggie`; record whether `sudo -n true` exits 0 (expected: yes, passwordless) | |
| W15 | After W14, on the same VM | Projects still start and answer; the console's **Import** with a Windows folder uploads it and the project appears; `\\wsl.localhost\eggie-vm\opt\eggie\projects` still opens in Explorer | |
| W16 | After W14, connect each agent by its Windows guide: Claude Code, Codex, Cursor. Then in a `wsl -d eggie-vm --cd ~` shell run `claude` once | Each agent signs in from its Windows app, opens the projects folder in the VM and runs a command there (Cursor's WSL connection is the one most likely to break without interop). The CLI starts without a crash; any login link it prints opens when copied into a Windows browser | |

## macOS (`EggieSetup-<version>-<arch>.pkg`)

The build is native-arch. Test a *downloaded* copy for M2: a locally built `.pkg` carries no
quarantine flag, so Gatekeeper never checks it. The Lima-specific unknowns are listed in
[macos-status.md](macos-status.md).

| # | Machine / action | Pass when | Result |
|---|---|---|---|
| M1 | `bash packaging/macos/build.sh` | Exits 0; `version` and `selfcheck` pass inside the app | |
| M2 | Install on a Mac that has never had Eggie | `/Applications/Eggie.app` exists; `eggie version` works in a new shell | |
| M3 | Open Eggie.app from Finder | The window opens with a Dock icon (no terminal) and runs the steps | |
| M4 | Mac without Lima | Setup downloads Lima into `~/.local/share/eggie/lima/` and carries on | |
| M5 | Full setup | Every step completes; the test project answers 200. **Record the wall-clock time** | |
| M6 | Run setup again on a provisioned Mac | Steps skip or re-run cleanly; exits 0 | |
| M7 | Open the app on a provisioned Mac | The status screen or console appears within seconds, without reinstalling; the SSH details work from Terminal | |
| M8 | Install a new `.pkg` over an existing install | The app is replaced; the VM is left alone and found | |
| M9 | No network during the Lima download | Fails with a plain sentence; re-running resumes the download | |
| M10 | Light and dark mode | Text is readable in both | |
| M11 | **Destructive.** `bash packaging/macos/uninstall.sh` | The VM, app, `/usr/local/bin/eggie`, pkg receipt and `~/.local/share/eggie` are all gone | |
| M12 | Intel Mac, built on Intel | Boots the `x86_64` image | |
| M13 | Update from the previous host release via the **Update** button | The window closes on its own after the download; Installer.app opens and asks for the admin password (as on a first install); reopening Eggie afterwards shows the new version in the footer | |
| M14 | Boot a VM with a newer compatible runtime tag published | `runtime.version` moves; projects still run | |
| M15 | Boot with the network off | The VM starts on its old runtime; `journalctl -u eggie-update` explains why | |

## Tray and open at login

| # | Action | Pass when | Result |
|---|---|---|---|
| T1 | Close the window with the title-bar button | The window hides; the tray / menu-bar icon stays; projects still answer. Windows: the first time only, a notification says Eggie is still in the tray | |
| T2 | Tray icon: left-click (Windows) / menu **Open Eggie** | The window comes back where it was | |
| T3 | Tray menu **Settings** while the projects console is showing | The window shows Eggie's Settings screen, not the console | |
| T4 | Finish a first setup, then open Settings | **Open Eggie when I sign in** is ticked. Windows: `reg query HKCU\Software\Microsoft\Windows\CurrentVersion\Run /v Eggie` shows `"<install dir>\eggie-app.exe" setup --background`. macOS: Eggie is listed in System Settings → General → Login Items | |
| T5 | Sign out and back in (and once: reboot) | Only the tray icon appears, no window (macOS: the window may flash briefly before it hides — the login launch is only recognised once the app is open); within a minute the VM is running and projects answer; opening Eggie then shows the up-to-date Home or console, not a "starting" screen | |
| T6 | Untick the checkbox; turn it back on in Task Manager / System Settings | Reopening Settings shows the OS state each time | |
| T7 | Untick, then run **Repair** and an app update | It stays unticked | |
| T8 | Open Eggie again from the Start menu / Finder while it runs | The existing window comes forward; still one tray icon | |
| T9 | Tray **Quit Eggie** with the VM running | "Stopping Eggie…" shows, then the app exits; `wsl -l --running` / `limactl list` shows the VM stopped. Record whether `systemctl poweroff` alone ended the WSL distro. If stopping the VM fails the app still exits | |
| T10 | **Quit Eggie** during an install or update | "Eggie is still working" asks first; Cancel returns; Quit anyway exits | |
| T11 | macOS: red button, then click the Dock icon; then Cmd+Q; then, with the VM running, Dock → Quit and a logout / restart | The Dock icon disappears while hidden and the window comes back on reopen; Cmd+Q stops the VM and quits; Dock → Quit, logout and restart end the app at once and do not stop the VM (and do not hang the logout) | |
| T12 | **Update now** while the VM runs | The app restarts on the new version; the VM was never stopped | |
| T13 | **Destructive.** Uninstall while the app runs (Windows: Settings → Apps; macOS: `uninstall.sh`) | Windows: after the confirmation the uninstaller force-ends `eggie-app.exe` (taskkill; Inno's uninstaller cannot close running apps itself), so no "file in use" prompt appears, the tray icon goes (a force-killed icon may linger until hovered), the VM is destroyed and the Run value is gone. macOS: the script quits Eggie first (the tray icon disappears, the VM is not stopped by the app), then the purge destroys the VM and the Login Item is gone. Either way: no Eggie process and no tray icon remain | |
| T14 | Windows: make the tray unable to start (e.g. rename `icon.ico` in a build) and launch, also once with `--background` | The app runs as a plain window, even with `--background`; closing it exits. `eggie-app.exe` is windowed, so the reason is in `eggie.log` next to `settings.json` (the Doctor modal names the path), not on screen. macOS: a missing icon is not a tray failure — the menu-bar item shows the text "Eggie" instead and the app behaves as in T1–T3 | |
| T15 | Windows: with the window hidden to the tray, sign out; then restart from the Start menu; then run setup's **Restart now** (after a WSL feature install); then install an app update over the running app | None of them is blocked by Eggie (no "This app is preventing you from signing out" / restart screen); the installer closes Eggie without asking | |

## Both platforms, once set up

| # | Action | Pass when | Result |
|---|---|---|---|
| B1 | Open the console from the desktop window, then "open in browser" | The handoff signs the browser in; links open in the system browser | |
| B2 | Add a project folder and start it | It gets a `*.local.eggie.space:39080` URL that answers | |
| B3 | Bring up the five compose files in `tests/fixtures/compose/` | Count how many work unchanged | |
| B4 | Inside the VM, `eggie new` / `eggie up` from a coding agent | The project shows up in the console | |
| B5 | In a running project: `eggie secret set API_KEY` (pipe a value), put `API_KEY=other` in the project's `.env`, then restart it | `docker exec <container> env` shows the secret's value, not the `.env` one; a compose `${KEY:?}` reference still lets `eggie logs` and stop/down work | |

## Site mode (a droplet from the snapshot; see `site-mode.md`)

| # | Action | Pass when | Result |
|---|---|---|---|
| S1 | Build the snapshot (install, `snapshot-prep.sh`, image) and provision a droplet from it with the service | `https://<site host>` answers within two minutes of boot with a valid certificate; `/opt/eggie/account.json` is gone | |
| S2 | "Open console" from the web app | The console opens signed in; the account menu has no sign-out; projects page has no public-address tile | |
| S3 | Add a project and start it | Its URL is `https://<id>.<site domain>` and answers with the same certificate | |
| S4 | SSH in as the login account; `eggie status` | The CLI works over loopback; the agents card in the console shows the site host, port 22, no key file | |
| S5 | Reboot the droplet | The stack comes back on 443; the boot update moves `runtime.version` if a newer release exists; projects resume | |
| S6 | Renew the certificate on the service side; wait a day or restart the api container | `/opt/eggie/tls/current` points at a new directory; Traefik serves the new certificate without a restart | |
| S7 | `curl http://<ip>:39099/health` and `:39080` from outside | Both refused by the firewall; `curl 127.0.0.1:39099/health` inside works | |
| S8 | Inside the droplet: `ss -ltn | grep 39080` | Listens on `127.0.0.1` only; `curl -H 'Host: <id>.<site domain>' http://127.0.0.1:39080/` answers the project | |
| S9 | Start a project, then `docker exec <its container> curl -m 3 http://169.254.169.254/metadata/v1/user-data` | Times out; the same curl from the droplet shell answers | |
| S10 | `curl -X POST -H 'Origin: https://<site host>' https://<site host>/api/account/sign-out -b <console cookie>` (or the bearer route on loopback) | `403` with code `site_account`; the console stays signed in | |
| S11 | Open the console from the web app a second time with the first link (already used) | "Open the console from the web app again"; the service records one redemption for the first open and refuses the second | |
| S12 | On the service, confirm the device pair seeded in user data was rotated at first boot | The seeded refresh token is spent; `curl 169.254.169.254/metadata/v1/user-data` from the droplet shell still shows it, and it no longer works | |
