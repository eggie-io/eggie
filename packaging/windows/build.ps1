#Requires -Version 5.1
[CmdletBinding()]
param([string]$InnoSetup)

$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path "$PSScriptRoot\..\..").Path

# PowerShell's call operator rejects bare relative paths: `& .venv\...` is
# parsed as a module name, not a program. Every invocation below is absolute.
$venvPython = Join-Path $repo '.venv\Scripts\python.exe'
$cliExe     = Join-Path $repo 'dist\Eggie\eggie.exe'
$setupExe   = Join-Path $repo 'dist\Eggie\setup.exe'
$specFile   = Join-Path $repo 'packaging\windows\eggie.spec'
$issFile    = Join-Path $repo 'packaging\windows\installer.iss'
$webview2   = Join-Path $repo 'packaging\windows\MicrosoftEdgeWebView2Setup.exe'
$webview2Url = 'https://go.microsoft.com/fwlink/p/?LinkId=2124703'

function Resolve-Iscc {
    # Inno's install location moves between versions and install modes: 6.3+
    # ships a 64-bit build under Program Files, and winget may install per-user.
    # The registry key it writes is the only stable answer; paths are fallbacks.
    foreach ($hive in 'HKLM:', 'HKCU:') {
        foreach ($view in 'SOFTWARE', 'SOFTWARE\WOW6432Node') {
            $key = "$hive\$view\Microsoft\Windows\CurrentVersion\Uninstall\Inno Setup 6_is1"
            $loc = (Get-ItemProperty -Path $key -Name InstallLocation -ErrorAction SilentlyContinue).InstallLocation
            if ($loc) {
                $candidate = Join-Path $loc 'ISCC.exe'
                if (Test-Path $candidate) { return $candidate }
            }
        }
    }
    $paths = @(
        (Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe'),
        (Join-Path $env:ProgramFiles 'Inno Setup 6\ISCC.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe')
    )
    foreach ($p in $paths) { if ($p -and (Test-Path $p)) { return $p } }
    $cmd = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    return $null
}

Push-Location $repo
try {
    if (-not (Test-Path $venvPython)) {
        throw "No virtualenv at $venvPython. Run: py -3.12 -m venv .venv"
    }
    & $venvPython -c "import PyInstaller" 2>$null
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller missing. Run: .\.venv\Scripts\python.exe -m pip install -e `".[dev]`""
    }

    # eggie.spec names this as a hidden import by string. If the name is
    # wrong, PyInstaller silently omits it -- the build still succeeds, and
    # the only symptom is a user's double-click reporting "install the Edge
    # WebView2 runtime", which is the wrong diagnosis for a packaging bug.
    # Failing here, on the machine that actually has pywebview installed,
    # turns that into a build-time error instead.
    & $venvPython -c "import webview.platforms.edgechromium"
    if ($LASTEXITCODE -ne 0) {
        throw "pywebview's EdgeChromium backend did not import; the hidden import in eggie.spec is wrong."
    }

    $version = (Select-String -Path (Join-Path $repo 'pyproject.toml') `
        -Pattern '^version = "(.+)"').Matches[0].Groups[1].Value
    Write-Host "==> Building Eggie $version"

    & $venvPython -m PyInstaller --noconfirm --clean $specFile
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed." }

    # Only eggie.exe is smoke-tested: setup.exe is the same code frozen for the
    # GUI subsystem, which PowerShell neither waits on nor reads output from.
    if (-not (Test-Path $setupExe)) { throw "setup.exe was not built." }

    & $cliExe version
    if ($LASTEXITCODE -ne 0) { throw "Smoke test failed: eggie.exe version." }
    # version never touches disk, so it can pass on a bundle that's missing a
    # datas entry or a hiddenimport; selfcheck resolves each bundled asset the
    # way the real code does, and also imports the four desktop modules the
    # spec's hiddenimports name, and catches either class of failure before
    # it reaches a user.
    & $cliExe selfcheck
    if ($LASTEXITCODE -ne 0) { throw "Smoke test failed: eggie.exe selfcheck reported a missing bundled asset or desktop-window module." }

    # Evergreen bootstrapper (~1.5MB), not the runtime itself: it detects
    # what's already on the machine and fetches only what's missing when the
    # installer runs it. Downloaded fresh each build rather than vendored so
    # the installer always ships Microsoft's current bootstrapper.
    Write-Host "==> Downloading Microsoft Edge WebView2 bootstrapper"
    Invoke-WebRequest -Uri $webview2Url -OutFile $webview2
    if (-not (Test-Path $webview2)) { throw "WebView2 bootstrapper download failed." }

    if (-not $InnoSetup) { $InnoSetup = Resolve-Iscc }
    if (-not $InnoSetup -or -not (Test-Path $InnoSetup)) {
        throw ("ISCC.exe not found. Install Inno Setup 6 " +
               "(winget install -e --id JRSoftware.InnoSetup), or pass its path " +
               "with -InnoSetup. Searched the Inno Setup 6_is1 registry key under " +
               "HKLM and HKCU, Program Files, Program Files (x86), " +
               "%LOCALAPPDATA%\Programs, and PATH.")
    }
    Write-Host "==> ISCC: $InnoSetup"
    $env:EGGIE_VERSION = $version
    & $InnoSetup $issFile
    if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed." }

    Write-Host "==> dist\EggieSetup-$version.exe" -ForegroundColor Green
} finally { Pop-Location }
