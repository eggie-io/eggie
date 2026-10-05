<#
.SYNOPSIS
  One-command Windows setup: venv, install, rootfs download, VM create.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\setup.ps1
#>
[CmdletBinding()]
param(
    # Use a rootfs already on disk instead of downloading one.
    [string]$Rootfs,
    # Install only; skip `eggie vm create`.
    [switch]$NoCreate
)

$ErrorActionPreference = 'Stop'
$repo = $PSScriptRoot

function Step($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
function Fail($msg) { Write-Host "!!! $msg" -ForegroundColor Red; exit 1 }

# --- 1. a Python 3.12+ interpreter -------------------------------------------
Step "Locating Python 3.12+"
$pyExe = $null
$pyArgs = @()
foreach ($cand in @(@('py', '-3.12'), @('py', '-3'), @('python', ''))) {
    $exe = $cand[0]
    $pre = @($cand[1] | Where-Object { $_ })
    if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }
    $ok = & $exe @pre '-c' 'import sys;print(sys.version_info>=(3,12))' 2>$null
    if ($ok -eq 'True') { $pyExe = $exe; $pyArgs = $pre; break }
}
if (-not $pyExe) {
    Fail "No Python 3.12+ found. Install it (winget install Python.Python.3.12) and re-run."
}
Write-Host "    using: $pyExe $($pyArgs -join ' ')"

# --- 2. venv + editable install ----------------------------------------------
$venvPython = Join-Path $repo '.venv\Scripts\python.exe'
if (-not (Test-Path $venvPython)) {
    Step "Creating virtualenv in .venv"
    & $pyExe @pyArgs -m venv (Join-Path $repo '.venv')
    if ($LASTEXITCODE -ne 0) { Fail "venv creation failed." }
}

Step "Installing the eggie package"
# Called through the venv's python so no activation (and no execution policy) is needed.
& $venvPython -m pip install --quiet --upgrade pip
Push-Location $repo
try {
    & $venvPython -m pip install --quiet -e ".[dev]"
} finally { Pop-Location }
if ($LASTEXITCODE -ne 0) { Fail "pip install failed." }

$eggieExe = Join-Path $repo '.venv\Scripts\eggie.exe'
if (-not (Test-Path $eggieExe)) { Fail "Install finished but $eggieExe is missing." }

# Shim so `.\eggie <cmd>` works from this directory without activating the venv.
@"
@echo off
"%~dp0.venv\Scripts\eggie.exe" %*
"@ | Set-Content -Path (Join-Path $repo 'eggie.cmd') -Encoding ASCII

# --- 3. host check ------------------------------------------------------------
Step "Checking this host"
& $eggieExe doctor
if ($LASTEXITCODE -ne 0) { Fail "Host check failed — fix the items above, then re-run." }

if ($NoCreate) { Step "Done (skipped VM create). Run: .\eggie vm create"; exit 0 }

# --- 4. rootfs ----------------------------------------------------------------
# Canonical's official WSL images, the same ones Microsoft's WSL distro
# manifest points at.
$images = @{
    'amd64' = @{
        Url  = 'https://releases.ubuntu.com/24.04.4/ubuntu-24.04.4-wsl-amd64.wsl'
        Hash = '9B2F7730DC68227DD04A9F3E5EAB86AD85CAF556B8606AD94F1F29FF5C4FD3F5'
    }
    'arm64' = @{
        Url  = 'https://cdimages.ubuntu.com/releases/24.04.4/release/ubuntu-24.04.4-wsl-arm64.wsl'
        Hash = '6B244D89F412A68F51E58F396FAB65BED3B5896A25C045A99BEF9C78A07DF507'
    }
}
$arch = if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64') { 'arm64' } else { 'amd64' }

if ($Rootfs) {
    if (-not (Test-Path $Rootfs)) { Fail "Rootfs not found: $Rootfs" }
    $rootfsPath = (Resolve-Path $Rootfs).Path
} else {
    $image = $images[$arch]
    $cache = Join-Path $env:LOCALAPPDATA 'Eggie\cache'
    New-Item -ItemType Directory -Force -Path $cache | Out-Null
    $rootfsPath = Join-Path $cache (Split-Path $image.Url -Leaf)

    $haveIt = (Test-Path $rootfsPath) -and
              ((Get-FileHash $rootfsPath -Algorithm SHA256).Hash -eq $image.Hash)
    if ($haveIt) {
        Step "Reusing cached rootfs ($arch)"
    } else {
        Step "Downloading Ubuntu 24.04 rootfs for $arch (~400 MB, one time)"
        # IWR's progress bar makes large downloads several times slower.
        $prev = $ProgressPreference
        $ProgressPreference = 'SilentlyContinue'
        try { Invoke-WebRequest -Uri $image.Url -OutFile $rootfsPath -UseBasicParsing }
        finally { $ProgressPreference = $prev }

        Step "Verifying checksum"
        $actual = (Get-FileHash $rootfsPath -Algorithm SHA256).Hash
        if ($actual -ne $image.Hash) {
            Remove-Item $rootfsPath -Force
            Fail "Checksum mismatch (got $actual). Download discarded."
        }
    }
}
Write-Host "    rootfs: $rootfsPath"

# --- 5. create + bootstrap the VM --------------------------------------------
Step "Creating the VM and installing Docker + Traefik (several minutes)"
$env:EGGIE_ROOTFS = $rootfsPath
& $eggieExe vm create
if ($LASTEXITCODE -ne 0) { Fail "VM setup failed — see the error above." }

Write-Host ""
Write-Host "Ready. Start a project with:" -ForegroundColor Green
Write-Host "    .\eggie up <path-to-project>"
