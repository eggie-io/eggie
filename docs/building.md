# Building

Both installers use PyInstaller to freeze the host in one-dir mode. Before packaging, the build
runs the frozen binary (`omelet version`, then `omelet selfcheck`), so a missing bundled asset
fails the build instead of turning up during a user's setup. The version comes from
`pyproject.toml`. Before a build ships, run the manual checks in
[release-testing.md](release-testing.md).

## Windows installer

Build in PowerShell on Windows, from a Windows-side checkout (not `\\wsl$\...`). A Linux `.venv`
in a WSL checkout clashes with the Windows one.

```powershell
git clone <repo-url> C:\src\omelet; cd C:\src\omelet
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"      # PyInstaller, pywebview, pythonnet
winget install -e --id JRSoftware.InnoSetup

powershell -ExecutionPolicy Bypass -File .\packaging\windows\build.ps1
# → dist\OmeletSetup-<version>.exe
```

`build.ps1` freezes `omelet.exe` and `setup.exe` from `packaging\windows\omelet.spec`,
smoke-tests them, downloads Microsoft's WebView2 bootstrapper (so the build needs network access),
and packages everything with Inno Setup (`installer.iss`). If `ISCC.exe` isn't found, pass
`-InnoSetup "C:\Path\To\ISCC.exe"`. The installer is unsigned.

The installer also installs WebView2. On its last page it offers a "Set up Omelet now" checkbox,
which runs `setup.exe setup`. The uninstaller runs
`omelet.exe uninstall --purge`, which destroys the VM and every project in it.

## macOS package

```bash
python3.12 -m venv .venv && .venv/bin/python -m pip install -e ".[dev]"
bash packaging/macos/build.sh
# → dist/OmeletSetup-<version>.pkg
sudo installer -pkg dist/OmeletSetup-<version>.pkg -target /
```

- **Native architecture only.** An Apple Silicon build refuses to install on Intel; an Intel Mac
  needs its own build.
- **Unsigned unless** `OMELET_CODESIGN_ID` and `OMELET_INSTALLER_ID` are set. A *downloaded*
  unsigned copy needs right-click → Open; a locally built one installs normally. Notarization
  isn't set up.
- The package installs `Omelet.app` into `/Applications` and links `omelet` into your PATH, and
  does nothing else. Open the app, or run `omelet setup --headless`, to build the VM. The
  package's scripts run as root, but Lima keeps VMs per user, so the installer can't build the
  VM itself.
- You don't need to install Lima yourself. Setup downloads a checksum-verified copy into
  `~/.local/share/omelet/lima/`.
- To remove everything, including the VM and its projects, run
  `bash packaging/macos/uninstall.sh` as yourself, not with `sudo`.

## Container images

The VM runs two images of ours, `omelet-api` and `omelet-web`. They're built together and share
one version.

```bash
packaging/images/build.sh                              # native arch, loaded into local docker
packaging/images/build.sh --only web                   # one image
packaging/images/build.sh --push                       # amd64 + arm64 to ghcr (a release)
packaging/images/build.sh --push --tag dev --only web  # throwaway tag to try in a VM
```

Pushing needs `docker buildx` and `docker login ghcr.io`. The script refuses to build if the
version numbers disagree. The web image needs `--build-context fixtures=tests/fixtures`, so build
it with this script rather than a bare `docker build runtime/web`, which fails.
