# Building

Both installers use PyInstaller to freeze the host in one-dir mode. Before packaging, the build
runs the frozen binary (`eggie version`, then `eggie selfcheck`), so a missing bundled asset
fails the build instead of turning up during a user's setup. The version comes from
`pyproject.toml`. Before a build ships, run the manual checks in
[release-testing.md](release-testing.md).

## Windows installer

Build in PowerShell on Windows, from a Windows-side checkout (not `\\wsl$\...`). A Linux `.venv`
in a WSL checkout clashes with the Windows one.

```powershell
git clone <repo-url> C:\src\eggie; cd C:\src\eggie
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"      # PyInstaller, pywebview, pythonnet
winget install -e --id JRSoftware.InnoSetup

powershell -ExecutionPolicy Bypass -File .\packaging\windows\build.ps1
# → dist\EggieSetup-<version>.exe
```

`build.ps1` freezes `eggie.exe` and `setup.exe` from `packaging\windows\eggie.spec`,
smoke-tests them, downloads Microsoft's WebView2 bootstrapper (so the build needs network access),
and packages everything with Inno Setup (`installer.iss`). If `ISCC.exe` isn't found, pass
`-InnoSetup "C:\Path\To\ISCC.exe"`. The installer is unsigned.

The installer also installs WebView2. On its last page it offers a "Set up Eggie now" checkbox,
which runs `setup.exe setup`. The uninstaller runs
`eggie.exe uninstall --purge`, which destroys the VM and every project in it.

## macOS package

```bash
python3.12 -m venv .venv && .venv/bin/python -m pip install -e ".[dev]"
bash packaging/macos/build.sh
# → dist/EggieSetup-<version>-<arch>.pkg
sudo installer -pkg dist/EggieSetup-<version>-<arch>.pkg -target /
```

- **Native architecture only.** An Apple Silicon build refuses to install on Intel; an Intel Mac
  needs its own build.
- **Unsigned unless** `EGGIE_CODESIGN_ID` and `EGGIE_INSTALLER_ID` are set. A *downloaded*
  unsigned copy needs right-click → Open; a locally built one installs normally. Notarization
  isn't set up.
- The package installs `Eggie.app` into `/Applications` and links `eggie` into your PATH, and
  does nothing else. Open the app, or run `eggie setup --headless`, to build the VM. The
  package's scripts run as root, but Lima keeps VMs per user, so the installer can't build the
  VM itself.
- You don't need to install Lima yourself. Setup downloads a checksum-verified copy into
  `~/.local/share/eggie/lima/`.
- To remove everything, including the VM and its projects, run
  `bash packaging/macos/uninstall.sh` as yourself, not with `sudo`.

## Container images

The VM runs two images of ours, `eggie-api` and `eggie-web`. They're built together and share
one version: the runtime tag's. Releases build them in CI (`docs/releasing.md`); locally:

```bash
packaging/images/build.sh                              # native arch, loaded into local docker
packaging/images/build.sh --only web                   # one image
packaging/images/build.sh --push --tag dev --only web  # throwaway tag to try in a VM
```

Pushing needs `docker buildx` and `docker login ghcr.io`, and either `--version X.Y.Z` (what the
release workflow passes) or `--tag`. The web image needs `--build-context fixtures=tests/fixtures` and
`--build-context agents=runtime/agents`, so build
it with this script rather than a bare `docker build runtime/web`, which fails.
