#define AppName "Omelet"
#define AppVersion GetEnv("OMELET_VERSION")

[Setup]
AppName={#AppName}
AppVersion={#AppVersion}
DefaultDirName={localappdata}\Programs\Omelet
DefaultGroupName={#AppName}
OutputDir=..\..\dist
OutputBaseFilename=OmeletSetup-{#AppVersion}
SetupIconFile=..\..\host\desktop\resources\icon.ico
UninstallDisplayIcon={app}\setup.exe
; Per-user install: no admin for the install itself. The only UAC prompt in
; the whole experience is the scoped one for enabling WSL2.
PrivilegesRequired=lowest
ArchitecturesInstallIn64BitMode=x64compatible
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=force

[Files]
Source: "..\..\dist\Omelet\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion
; Microsoft's evergreen bootstrapper, fetched by build.ps1. A no-op on
; Windows 11 and any updated Windows 10 -- it detects an existing runtime and
; exits without reinstalling -- so shipping and running it unconditionally is
; cheaper than detecting the runtime ourselves.
Source: "MicrosoftEdgeWebView2Setup.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall

[Icons]
Name: "{group}\Omelet Setup"; Filename: "{app}\setup.exe"; Parameters: "setup"

[Run]
Filename: "{tmp}\MicrosoftEdgeWebView2Setup.exe"; Parameters: "/silent /install"; \
  StatusMsg: "Installing Microsoft Edge WebView2 runtime..."; Flags: waituntilterminated
Filename: "{app}\setup.exe"; Parameters: "setup"; \
  Description: "Set up Omelet now"; Flags: postinstall nowait skipifsilent
; A silent run is the app's own Update button: reopen it when done. The entry
; above is skipifsilent, so it never covers this.
Filename: "{app}\setup.exe"; Parameters: "setup"; Flags: nowait runasoriginaluser; Check: WizardSilent

[UninstallRun]
; Destroys the VM and every project inside it before files are removed.
; Console exe on purpose: the uninstaller must wait for it and read its exit code.
Filename: "{app}\omelet.exe"; Parameters: "uninstall --purge"; \
  Flags: runhidden; RunOnceId: "PurgeVm"

[Registry]
; {app} stays in Path after uninstall on purpose: surgically removing it
; risks corrupting PATH, and Windows ignores PATH entries that don't exist.
Root: HKCU; Subkey: "Environment"; ValueType: expandsz; ValueName: "Path"; \
  ValueData: "{olddata};{app}"; Check: NeedsAddPath('{app}')

[Code]
function NeedsAddPath(Param: string): boolean;
var OrigPath: string;
begin
  if not RegQueryStringValue(HKCU, 'Environment', 'Path', OrigPath) then
  begin Result := True; exit; end;
  Result := Pos(';' + ExpandConstant(Param) + ';', ';' + OrigPath + ';') = 0;
end;

function InitializeUninstall(): Boolean;
var ResultCode: Integer;
begin
  Result := MsgBox(
    'Uninstalling Omelet permanently deletes the VM and every ' +
    'project inside it. Project files live inside the VM, not on this ' +
    'PC, so nothing is recoverable afterward.' + #13#10#13#10 +
    'Continue with uninstall?',
    mbConfirmation, MB_YESNO + MB_DEFBUTTON2) = IDYES;
  if Result then
    // CloseApplications is Setup-only, so a running tray app would stay
    // locked and outlive the VM. The purge destroys the VM right after, so a
    // hard stop is fine. Done here, not in [UninstallRun], because Inno runs
    // those entries in reverse order and the kill must come first.
    // Matched by full path: setup.exe is a common installer name.
    Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
         '-NoProfile -Command "Get-Process setup -ErrorAction SilentlyContinue | ' +
         'Where-Object { $_.Path -eq ''' + ExpandConstant('{app}\setup.exe') + ''' } | ' +
         'Stop-Process -Force"',
         '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;
