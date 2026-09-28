# Release testing (manual)

CI doesn't cover any of this. Run these checks by hand on real or virtual machines before an
installer ships. For each run, record the date, the build and what you actually saw. Don't mark a
case passed without that record.

The previous matrices (`installer-test-matrix.md`, `macos-install-test-matrix.md`) were written
for the tkinter setup window that the webview desktop app has since replaced. Their old results
are in git history at commit `7204c3d`. Every case below is **UNRUN against the current app**.

⚠️ Cases marked **destructive** delete the VM and every project in it. Run them only where that's
disposable.

## Windows (`OmeletSetup-<version>.exe`)

| # | Machine / action | Pass when | Result |
|---|---|---|---|
| W1 | WSL2 already enabled; run the installer | Setup completes; the test project answers 200; the window moves on to the projects console | |
| W2 | **Clean Windows, WSL2 never enabled** (needs nested virtualization in a VM). This is the release gate | One UAC prompt; after the restart, setup resumes on its own and completes | |
| W3 | Virtualization disabled in firmware | Setup stops at preflight with the BIOS instruction and no stack trace | |
| W4 | Run setup again on a provisioned machine | Every step reports skipped or finishes quickly; exits 0 | |
| W5 | Machine without WebView2 | The installer installs it; the window opens | |
| W6 | Watch the window during W1–W4 | Progress shows each step; failures show the guest error and what to do; closing mid-install exits non-zero; no console windows flash | |
| W7 | `omelet.exe selfcheck` from the *installed* copy | Prints OK for every asset, exits 0 | |
| W8 | **Destructive.** Uninstall from Apps & Features | A confirmation appears first (declining leaves everything in place); afterwards `wsl -l -v` has no `omelet-vm` and the cache is gone | |
| W9 | PATH before and after W8 (`reg query HKCU\Environment /v Path`) | No other entry changed. The app's own leftover segment is expected (see `installer.iss`) | |

## macOS (`OmeletSetup-<version>.pkg`)

The build is native-arch. Test a *downloaded* copy for M2: a locally built `.pkg` carries no
quarantine flag, so Gatekeeper never checks it. The Lima-specific unknowns are listed in
[macos-status.md](macos-status.md).

| # | Machine / action | Pass when | Result |
|---|---|---|---|
| M1 | `bash packaging/macos/build.sh` | Exits 0; `version` and `selfcheck` pass inside the app | |
| M2 | Install on a Mac that has never had Omelet | `/Applications/Omelet.app` exists; `omelet version` works in a new shell | |
| M3 | Open Omelet.app from Finder | The window opens with a Dock icon (no terminal) and runs the steps | |
| M4 | Mac without Lima | Setup downloads Lima into `~/.local/share/omelet/lima/` and carries on | |
| M5 | Full setup | Every step completes; the test project answers 200. **Record the wall-clock time** | |
| M6 | Run setup again on a provisioned Mac | Steps skip or re-run cleanly; exits 0 | |
| M7 | Open the app on a provisioned Mac | The status screen or console appears within seconds, without reinstalling; the SSH details work from Terminal | |
| M8 | Install a new `.pkg` over an existing install | The app is replaced; the VM is left alone and found | |
| M9 | No network during the Lima download | Fails with a plain sentence; re-running resumes the download | |
| M10 | Light and dark mode | Text is readable in both | |
| M11 | **Destructive.** `bash packaging/macos/uninstall.sh` | The VM, app, `/usr/local/bin/omelet`, pkg receipt and `~/.local/share/omelet` are all gone | |
| M12 | Intel Mac, built on Intel | Boots the `x86_64` image | |

## Both platforms, once set up

| # | Action | Pass when | Result |
|---|---|---|---|
| B1 | Open the console from the desktop window, then "open in browser" | The handoff signs the browser in; links open in the system browser | |
| B2 | Add a project folder and start it | It gets a `*.127-0-0-1.sslip.io:39080` URL that answers | |
| B3 | Bring up the five compose files in `tests/fixtures/compose/` | Count how many work unchanged | |
| B4 | Inside the VM, `omelet new` / `omelet up` from a coding agent | The project shows up in the console | |
