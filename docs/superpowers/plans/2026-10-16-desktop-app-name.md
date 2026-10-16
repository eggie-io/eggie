# Desktop App Name Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** After installation the desktop app presents itself as "Eggie" — Start menu, Windows search, Task Manager, Alt-Tab — never "Eggie Setup" or "setup" (eggie-io/eggie-resources#67).

**Architecture:** Packaging-only rename. The Windows GUI executable `setup.exe` becomes `eggie-app.exe` and gains a version resource whose FileDescription/ProductName is "Eggie" (Task Manager's Apps list reads FileDescription, falling back to the file name). The Inno Setup shortcut becomes `Eggie`. macOS renames its `eggie-setup` executable to `eggie-app` for consistency; the bundle is already "Eggie".

**Tech Stack:** PyInstaller spec files, Inno Setup 6, PowerShell, bash, pytest.

**Spec:** Bounded design approved in chat on 2026-10-16 (no spec file). Decisions: GUI exe name `eggie-app` on both platforms; version resource on both Windows exes; installer file name `EggieSetup-<v>.exe` stays; no upgrade cleanup of the old `setup.exe`/shortcut (no-compat rule).

## Global Constraints

- The CLI stays `eggie` / `eggie.exe` (it is what lands on PATH).
- GUI executable name: `eggie-app` (`eggie-app.exe` on Windows). Never `Eggie` — it collides with `eggie` on case-insensitive filesystems.
- User-visible app name: exactly `Eggie`.
- No `[InstallDelete]`, migration, or shim for the old `setup.exe` or "Eggie Setup" shortcut.
- PyInstaller and Inno cannot run in this environment. Verification is pytest plus a syntax-only parse of each spec: `python3 -c "import ast,sys; ast.parse(open(sys.argv[1]).read())" <spec>` (specs can't be executed — their globals come from PyInstaller).
- Tests: `TMPDIR=/home/ihor/tmp python3 -m pytest -q` (no .venv in this checkout).
- Comments: only for non-obvious edge cases; never mention issues/tickets.

## Review Focus

1. installer.iss references an exe the spec no longer builds (e.g. one `[Run]` line still says `setup.exe`) → install finishes but nothing launches. Pinned by Task 1's cross-file test.
2. `desktop_win._gui_exe` still looks for `setup.exe` → autostart registers the console `eggie.exe` and flashes a console at every sign-in. Pinned by Task 1's updated test.
3. Version resource built from a non-numeric version (e.g. `0.3.0rc1`) crashes the spec → build fails. Spec parses leading digits of each part.
4. `EGGIE_VERSION` unset when PyInstaller runs on Windows → resource says 0.0.0. build.ps1 sets it before PyInstaller.
5. macOS `CFBundleExecutable` and build.sh assertion drift from the spec's EXE name → double-click runs the CLI. All three change together in Task 2.

---

### Task 1: Windows — rename GUI exe, version resource, shortcut "Eggie"

**Files:**
- Modify: `packaging/windows/eggie.spec`
- Modify: `packaging/windows/installer.iss`
- Modify: `packaging/windows/build.ps1`
- Modify: `host/providers/desktop_win.py:34-41` (`_gui_exe`)
- Modify: `tests/host/test_desktop_win.py` (test `test_autostart_registers_the_gui_exe_when_given_the_console_exe`, plus the `setup.exe` example paths at lines 7, 29, 45 for readability)
- Create: `tests/host/test_windows_installer.py`
- Modify: `docs/building.md:24-31`, `docs/release-testing.md` rows T4, T13, T14

**Interfaces:**
- Produces: GUI exe name `eggie-app.exe`, sitting beside `eggie.exe` in `{app}`.

- [ ] **Step 1: Write the failing tests**

In `tests/host/test_desktop_win.py`, change the sibling test to the new name:

```python
def test_autostart_registers_the_gui_exe_when_given_the_console_exe(tmp_path):
    """`eggie setup` runs the window from eggie.exe; a login entry pointing
    there flashes a console at every sign-in."""
    (tmp_path / "eggie.exe").write_bytes(b"")
    (tmp_path / "eggie-app.exe").write_bytes(b"")
    console, gui = str(tmp_path / "EGGIE.EXE"), str(tmp_path / "eggie-app.exe")
    store = {}
    desktop = _autostart(store)
    desktop.set_autostart(True, console)
    assert store[(RUN_KEY, AUTOSTART_VALUE_NAME)] == run_value(gui)
    assert desktop.autostart_enabled(gui) is True
    assert desktop.autostart_enabled(console) is True
```

Replace `setup.exe` with `eggie-app.exe` in the example paths at lines 7, 29, 45 of the same file.

Create `tests/host/test_windows_installer.py` — the seam test between the spec, the installer and the provider, which is exactly what a rename can break silently:

```python
"""The installer and the provider name executables the spec must build.

A rename that misses one of them still builds and installs cleanly; the
failure is a shortcut or a login entry pointing at a file that isn't there.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = ROOT / "packaging" / "windows" / "eggie.spec"
ISS = ROOT / "packaging" / "windows" / "installer.iss"
DESKTOP_WIN = ROOT / "host" / "providers" / "desktop_win.py"


def _built() -> set[str]:
    names = re.findall(r'EXE\(.*?name="([^"]+)"', SPEC.read_text(), re.S)
    assert names, f"no EXE names found in {SPEC}"
    return {f"{n}.exe".lower() for n in names}


def test_every_exe_the_installer_runs_is_built():
    referenced = {n.lower() for n in re.findall(r"\{app\}\\([\w.-]+\.exe)", ISS.read_text())}
    assert referenced, "installer.iss references no {app} executables"
    assert referenced <= _built(), f"not built by the spec: {referenced - _built()}"


def test_the_gui_sibling_the_provider_looks_for_is_built():
    sibling = re.search(r'with_name\("([^"]+)"\)', DESKTOP_WIN.read_text())
    assert sibling, "desktop_win no longer names a GUI sibling"
    assert sibling.group(1).lower() in _built()


def test_start_menu_shortcuts_are_named_eggie():
    icons = re.findall(r'^Name:\s*"\{group\}\\([^"]+)"', ISS.read_text(), re.M)
    assert icons, "installer.iss creates no Start menu shortcut"
    assert icons == ["Eggie"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `TMPDIR=/home/ihor/tmp python3 -m pytest tests/host/test_desktop_win.py tests/host/test_windows_installer.py -q`
Expected: FAIL — the sibling test (provider still looks for `setup.exe`), `test_the_gui_sibling_the_provider_looks_for_is_built`, `test_start_menu_shortcuts_are_named_eggie` (shortcut is "Eggie Setup"). `test_every_exe_the_installer_runs_is_built` passes now (setup.exe is built today) — it guards the rename.

- [ ] **Step 3: Implement**

`host/providers/desktop_win.py` `_gui_exe`: `sibling = path.with_name("eggie-app.exe")`.

`packaging/windows/eggie.spec` — replace the two `EXE(...)` lines and the `COLLECT` line with:

```python
import os

from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo, StringFileInfo, StringStruct, StringTable, VarFileInfo,
    VarStruct, VSVersionInfo)

VERSION = os.environ.get("EGGIE_VERSION", "0.0.0")


def _version_tuple(version):
    # Leading digits only, so a "0.3.0rc1" build still stamps 0.3.0.0.
    parts = [int(re.match(r"\d*", p).group() or 0) for p in version.split(".")[:4]]
    return tuple(parts + [0] * (4 - len(parts)))


def _version_info(filename):
    # Task Manager and Alt-Tab show FileDescription; without it they fall back
    # to the file name.
    numbers = _version_tuple(VERSION)
    return VSVersionInfo(
        ffi=FixedFileInfo(filevers=numbers, prodvers=numbers),
        kids=[
            StringFileInfo([StringTable("040904B0", [
                StringStruct("CompanyName", "Eggie"),
                StringStruct("FileDescription", "Eggie"),
                StringStruct("FileVersion", VERSION),
                StringStruct("InternalName", filename),
                StringStruct("OriginalFilename", f"{filename}.exe"),
                StringStruct("ProductName", "Eggie"),
                StringStruct("ProductVersion", VERSION),
            ])]),
            VarFileInfo([VarStruct("Translation", [0x0409, 1200])]),
        ],
    )


cli_exe = EXE(pyz, a.scripts, exclude_binaries=True, name="eggie", console=True,
              icon="../../host/desktop/resources/icon.ico",
              version=_version_info("eggie"))
# Not "Eggie": Windows paths are case-insensitive, so it would be eggie.exe.
app_exe = EXE(pyz, a.scripts, exclude_binaries=True, name="eggie-app", console=False,
              icon="../../host/desktop/resources/icon.ico",
              version=_version_info("eggie-app"))

coll = COLLECT(cli_exe, app_exe, a.binaries, a.datas, name="Eggie")
```

Put `import os` / `import re` and the versioninfo import at the top of the spec (spec files are exec'd without the stdlib imported — see the macOS spec's note). Keep the existing comment above the EXE lines, changing "the setup window" wording to "the app window".

`packaging/windows/installer.iss`:
- `UninstallDisplayIcon={app}\eggie-app.exe`
- `[Icons]`: `Name: "{group}\Eggie"; Filename: "{app}\eggie-app.exe"; Parameters: "setup"`
- `[Run]` both entries: `Filename: "{app}\eggie-app.exe"`; the postinstall `Description: "Open Eggie"`
- `InitializeUninstall`: `Get-Process eggie-app` and `ExpandConstant('{app}\eggie-app.exe')`; drop the now-wrong "setup.exe is a common installer name" comment but keep full-path matching (a second Eggie install elsewhere would otherwise be killed).

`packaging/windows/build.ps1`:
- `$appExe = Join-Path $repo 'dist\Eggie\eggie-app.exe'` (replacing `$setupExe`), and the check/throw message `"eggie-app.exe was not built."`; comment "Only eggie.exe is smoke-tested: eggie-app.exe is the same code frozen for the GUI subsystem…".
- Move `$env:EGGIE_VERSION = $version` to right after `$version` is computed (before PyInstaller), and remove it from before ISCC.

Docs:
- `docs/building.md`: "`build.ps1` freezes `eggie.exe` and `eggie-app.exe`…"; "On its last page it offers an "Open Eggie" checkbox, which runs `eggie-app.exe setup`." Add one sentence: the Start menu entry is "Eggie".
- `docs/release-testing.md`: T4 `"<install dir>\eggie-app.exe" setup --background`; T13 force-ends `eggie-app.exe`; T14 `eggie-app.exe` is windowed. Add to T4's or an existing install row's expectation: Start menu/search lists "Eggie" and Task Manager shows "Eggie" (not "setup").

- [ ] **Step 4: Run tests and syntax-check the spec**

Run: `TMPDIR=/home/ihor/tmp python3 -m pytest tests/host -q`
Expected: PASS (includes `test_frozen_bundle.py`'s case-collision check on the new names).
Run: `python3 -c "import ast,sys; ast.parse(open(sys.argv[1]).read())" packaging/windows/eggie.spec`
Expected: no output.
Run: `grep -rn "setup\.exe\|Eggie Setup" packaging/windows host docs/building.md docs/release-testing.md tests/host | grep -v WebView2`
Expected: no output.

- [ ] **Step 5: Commit**

```bash
git add packaging/windows host/providers/desktop_win.py tests/host/test_desktop_win.py tests/host/test_windows_installer.py docs/building.md docs/release-testing.md
git commit -m "packaging(windows): name the desktop app Eggie, not Eggie Setup"
```

### Task 2: macOS — rename `eggie-setup` executable to `eggie-app`

**Files:**
- Modify: `packaging/macos/eggie.spec` (`name="eggie-setup"`, `"CFBundleExecutable": "eggie-setup"`, comments mentioning the setup window)
- Modify: `packaging/macos/build.sh` (`gui="$app/Contents/MacOS/eggie-setup"`, `[ "$main_exe" = "eggie-setup" ]` and its message)

**Interfaces:**
- Consumes: nothing from Task 1.

- [ ] **Step 1: Rename**

In `packaging/macos/eggie.spec`: `setup_exe = EXE(PYZ(gui.pure), gui.scripts, exclude_binaries=True, name="eggie-app", console=False)` (rename the variable to `app_exe` and update `COLLECT(app_exe, cli_exe, …)`), and `"CFBundleExecutable": "eggie-app"`. Reword comments that call it "the setup window" to "the app window" where they describe the executable; leave the existing case-collision explanation intact.

In `packaging/macos/build.sh`: `gui="$app/Contents/MacOS/eggie-app"`; `[ "$main_exe" = "eggie-app" ] || { echo "Eggie.app would launch '$main_exe', not the app window." >&2; exit 1; }`.

- [ ] **Step 2: Verify**

Run: `TMPDIR=/home/ihor/tmp python3 -m pytest tests/host/test_frozen_bundle.py -q` → PASS.
Run: `python3 -c "import ast,sys; ast.parse(open(sys.argv[1]).read())" packaging/macos/eggie.spec` → no output.
Run: `bash -n packaging/macos/build.sh` → no output.
Run: `grep -rn "eggie-setup" packaging/macos docs/*.md` → no output.

- [ ] **Step 3: Commit**

```bash
git add packaging/macos
git commit -m "packaging(macos): rename the app executable to eggie-app"
```

### Task 3: Full suite

- [ ] Run: `TMPDIR=/home/ihor/tmp python3 -m pytest -q` → all pass.
