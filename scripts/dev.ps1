<#
.SYNOPSIS
  Dev loop on Windows: run this checkout's runtime in the Eggie VM and this
  checkout's host window against it. The Windows twin of scripts/dev.sh.
  Nothing is pushed; the images are built inside the VM from your working
  tree. Re-run it after every change. Run it from PowerShell, not from WSL.

.EXAMPLE
  .\scripts\dev.ps1                # fast: sync runtime/, rebuild api + web, restart them, open the window
  .\scripts\dev.ps1 -Full          # also run the whole install.sh (CLI, agents, skills, systemd units);
                                   # creates the VM first if there is none
  .\scripts\dev.ps1 -Only web      # rebuild one image (api|web)
  .\scripts\dev.ps1 -NoWindow      # update the VM only
  .\scripts\dev.ps1 -Release       # put the latest released runtime back

.NOTES
  The VM records the runtime as `local`. The host only installs when that
  marker is missing, and the boot updater skips anything not a release tag, so
  nothing replaces your dev runtime until you run -Release.
#>
[CmdletBinding()]
param(
    [switch]$Full,
    [ValidateSet('api', 'web')][string]$Only,
    [switch]$NoWindow,
    [switch]$Release
)

$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$py = Join-Path $root '.venv\Scripts\python.exe'
$eggie = Join-Path $root '.venv\Scripts\eggie.exe'
$tag = 'local'
$stage = '/opt/eggie/dev'    # mirror of the checkout parts build.sh needs

function Say($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Fail($msg) { Write-Host "dev.ps1: $msg" -ForegroundColor Red; exit 1 }

if (-not (Test-Path $py)) { Fail "no .venv here; run .\setup.ps1 -NoCreate first" }

# Runs argv in the VM as root with $Stdin (a stream) as its stdin. Not a
# PowerShell pipe: Windows PowerShell re-encodes piped bytes as text, and
# CRLF in a script, or one byte changed in a tarball, breaks it in the VM.
function Invoke-Vm([string[]]$Argv, [IO.Stream]$Stdin) {
    # Every argument here is shell-safe: `wsl --` hands the rest of the
    # command line to the guest shell, which would re-parse anything quoted.
    $psi = New-Object System.Diagnostics.ProcessStartInfo $wsl
    $psi.Arguments = (@('-d', $vm, '-u', 'root', '--cd', '~', '--') + $Argv) -join ' '
    $psi.UseShellExecute = $false
    $psi.RedirectStandardInput = $null -ne $Stdin
    $p = [System.Diagnostics.Process]::Start($psi)
    if ($Stdin) {
        # A guest that exits early closes the pipe; its exit code says why.
        try { $Stdin.CopyTo($p.StandardInput.BaseStream) } catch [IO.IOException] { }
        try { $p.StandardInput.Close() } catch [IO.IOException] { }
    }
    $p.WaitForExit()
    $p.ExitCode
}

# Runs a bash script in the VM as root, fed through stdin so nothing in it is
# quoted on the way.
function Invoke-VmScript([string]$Script, [string]$What) {
    # The braces make bash read the whole script before running any of it;
    # otherwise a command that reads stdin (apt, npm) eats the rest of it.
    $text = "set -euo pipefail`nTAG=$tag`nSTAGE=$stage`n{`nexec </dev/null`n$Script`n}`n" -replace "`r", ''
    $bytes = (New-Object System.Text.UTF8Encoding $false).GetBytes($text)
    $code = Invoke-Vm @('bash', '-s') (New-Object IO.MemoryStream (, $bytes))
    if ($code -ne 0) { Fail "$What failed in the VM (exit $code)" }
}

Push-Location $root
try {
    # The same wsl.exe and distro name the app uses.
    $resolved = & $py -c 'from host.providers import get_provider; p = get_provider(); print(p.wsl, p.distro)'
    if ($LASTEXITCODE -ne 0 -or -not $resolved) { Fail "could not resolve the VM provider (Windows only)" }
    $wsl, $vm = "$resolved".Trim() -split ' ', 2

    if ($Release) {
        Say "Installing the latest released runtime"
        & $py -c 'from host.providers import get_provider; from host.core.install import update_runtime; update_runtime(get_provider())'
        if ($LASTEXITCODE -ne 0) { Fail "the runtime update failed" }
        Say "Done. The VM runs:"
        Invoke-Vm @('cat', '/opt/eggie/runtime.version') | Out-Null
        exit 0
    }

    # Both windows claim the same single-instance pipe: the dev one would only
    # bring the installed one forward. Its Quit also stops this same VM.
    if (Get-Process eggie-app -ErrorAction SilentlyContinue) {
        Fail "the installed Eggie app is running. Quit it from its tray icon first."
    }

    # --- the VM --------------------------------------------------------------
    $known = (& $py -c 'from host.providers import get_provider; print(get_provider().exists())') -eq 'True'
    if (-not $known) {
        if (-not $Full) { Fail "no VM '$vm' yet; run: .\scripts\dev.ps1 -Full" }
        Say "No VM yet: creating it with the released runtime first (several minutes)"
        & $eggie setup --headless
        if ($LASTEXITCODE -ne 0) { Fail "setup failed" }
    }
    # Always, not only when stopped: a distro that nothing holds open ends seconds
    # after the last wsl.exe exits, and the commands below would boot it unheld.
    & $eggie vm start
    if ($LASTEXITCODE -ne 0) { Fail "the VM did not start" }

    if ((Invoke-Vm @('test', '-x', '/usr/bin/docker')) -ne 0) {
        Fail "the VM has no Docker yet; run: $eggie setup --headless, then this again"
    }

    # --- sync the checkout into the VM ---------------------------------------
    Say "Copying runtime/ into the VM"
    $tgz = Join-Path ([IO.Path]::GetTempPath()) "eggie-dev-$PID.tgz"
    try {
        # Windows' own bsdtar. Zone.Identifier files show up when the checkout
        # lives in a WSL distro and something was downloaded into it from Windows.
        & "$env:SystemRoot\System32\tar.exe" -C $root -czf $tgz `
            --exclude node_modules --exclude dist --exclude __pycache__ `
            --exclude .vitest --exclude '*Zone.Identifier' `
            runtime tests/fixtures packaging/images/build.sh
        if ($LASTEXITCODE -ne 0) { Fail "tar could not pack the checkout" }
        $file = [IO.File]::OpenRead($tgz)
        try { $code = Invoke-Vm @('dd', 'of=/opt/eggie/dev.tgz', 'bs=1M', 'status=none') $file }
        finally { $file.Dispose() }
        if ($code -ne 0) { Fail "could not copy the checkout into the VM (exit $code)" }
    } finally {
        Remove-Item $tgz -ErrorAction SilentlyContinue
    }
    Invoke-VmScript @'
rm -rf $STAGE && mkdir -p $STAGE
# Windows' tar records every file 0666 and every folder 0777.
tar -xzf /opt/eggie/dev.tgz --no-same-owner --no-same-permissions -C $STAGE
rm -f /opt/eggie/dev.tgz
rm -rf /opt/eggie/runtime
cp -a $STAGE/runtime /opt/eggie/runtime
chmod 755 /opt/eggie/runtime
'@ 'unpacking the checkout'

    # --- images --------------------------------------------------------------
    $which = if ($Only) { $Only } else { 'api + web' }
    Say "Building $which images in the VM (tag $tag)"
    $onlyArg = if ($Only) { "--only $Only" } else { '' }
    Invoke-VmScript "bash `$STAGE/packaging/images/build.sh --tag `$TAG $onlyArg" 'the image build'

    # --- install / restart ---------------------------------------------------
    if ($Full) {
        Say "Running install.sh from your checkout"
        Invoke-VmScript @'
flock /opt/eggie/update.lock env EGGIE_IMAGE_VERSION=$TAG EGGIE_SKIP_PULL=1 \
  bash /opt/eggie/runtime/install/install.sh $TAG
'@ 'install.sh'
    } else {
        Say "Restarting the stack on the local images"
        # What install.sh's steps 5, 7 and the CLI step do, minus everything slow.
        Invoke-VmScript @'
sed -i "s/^EGGIE_VERSION=.*/EGGIE_VERSION=$TAG/" /opt/eggie/.env
install -m 644 /opt/eggie/runtime/stack.yml /opt/eggie/stack.yml
/usr/bin/docker compose -f /opt/eggie/stack.yml up -d
cli=$(mktemp)
python3 -m zipapp /opt/eggie/runtime/cli -m eggie_cli.cli:run -p '/usr/bin/env python3' -o $cli
install -m 755 $cli /usr/local/bin/eggie && rm -f $cli
echo $TAG > /opt/eggie/runtime.version
'@ 'restarting the stack'
    }

    # --- window --------------------------------------------------------------
    if (-not $NoWindow) {
        Say "Opening the desktop window from this checkout (close it, or Ctrl-C, to stop)"
        & $py -m host.desktop
        exit $LASTEXITCODE
    }
    Say "VM updated. Open the window with: .venv\Scripts\python.exe -m host.desktop"
} finally {
    Pop-Location
}
