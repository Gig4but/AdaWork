; AdaWork-Setup.exe (PLAN §3.1): installs dist\AdaWork for the current user, no admin.
; Built by packaging\build.ps1, which passes the version: ISCC /DAppVersion=0.1.0 installer.iss

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{6E0B7F1C-3D2A-4C55-9B8E-ADA0A0C0FFEE}
AppName=AdaWork
AppVersion={#AppVersion}
AppPublisher=AdaWork
VersionInfoVersion={#AppVersion}
DefaultDirName={localappdata}\Programs\AdaWork
DefaultGroupName=AdaWork
DisableProgramGroupPage=yes
DisableDirPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
OutputDir=..\dist
OutputBaseFilename=AdaWork-Setup
SetupIconFile=..\build\adawork.ico
UninstallDisplayIcon={app}\AdaWork.exe
UninstallDisplayName=AdaWork
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=force
RestartApplications=no

[Languages]
Name: "cs"; MessagesFile: "compiler:Languages\Czech.isl"
Name: "en"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
cs.Guide=Návod
en.Guide=User guide
cs.OpenGuide=Zobrazit návod
en.OpenGuide=Show the user guide
cs.DeleteData=Smazat i data aplikace (nabídky, stažené modely umělé inteligence a mapy)?%n%n%1%n%nKdyž je necháte, nová instalace je hned použije.
en.DeleteData=Also delete the app's data (jobs, downloaded AI models and maps)?%n%n%1%n%nIf you keep them, a new installation uses them right away.
cs.NoWebView2=AdaWork potřebuje pro své okno součást Windows „Microsoft Edge WebView2“, která v tomto počítači chybí.%n%nStáhnout ji teď? Stažený soubor potom spusťte.
en.NoWebView2=AdaWork needs the Windows component "Microsoft Edge WebView2" for its window, and this PC lacks it.%n%nDownload it now? Then run the downloaded file.

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\dist\AdaWork\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; files of an older version that the new one no longer has
Type: filesandordirs; Name: "{app}\_internal"
Type: filesandordirs; Name: "{app}\vendor"
Type: filesandordirs; Name: "{app}\licenses"

[Icons]
Name: "{autoprograms}\AdaWork"; Filename: "{app}\AdaWork.exe"
Name: "{autoprograms}\AdaWork – {cm:Guide}"; Filename: "{app}\navod.html"
Name: "{autodesktop}\AdaWork"; Filename: "{app}\AdaWork.exe"; Tasks: desktopicon

[Registry]
; The app adds "start with Windows" itself while automatic checks are on; removed on uninstall.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: none; ValueName: "AdaWork"; Flags: uninsdeletevalue dontcreatekey

[Run]
Filename: "{app}\navod.html"; Description: "{cm:OpenGuide}"; Flags: postinstall shellexec skipifsilent unchecked
Filename: "{app}\AdaWork.exe"; Description: "{cm:LaunchProgram,AdaWork}"; Flags: postinstall nowait skipifsilent
; An update from the app (adawork/desktop/update.py): /RELAUNCH=1 [/MINIMIZED=1].
Filename: "{app}\AdaWork.exe"; Parameters: "{code:RelaunchArgs}"; Flags: nowait; Check: Relaunch

[UninstallRun]
; The app and the programs it started (AI model, routing server) hold files in {app}.
Filename: "{sys}\taskkill.exe"; Parameters: "/F /T /IM AdaWork.exe"; Flags: runhidden; RunOnceId: "StopApp"

[Code]
const
  WebView2Key = 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';
  WebView2Download = 'https://go.microsoft.com/fwlink/p/?LinkId=2124703';

function DataDir(): String;
begin
  Result := ExpandConstant('{localappdata}\AdaWork');
end;

function WebView2Version(RootKey: Integer): String;
begin
  if not RegQueryStringValue(RootKey, WebView2Key, 'pv', Result) then
    Result := '';
end;

function HasWebView2(): Boolean;
var
  Version: String;
begin
  { Installed for all users (32-bit registry view) or for this user. }
  Version := WebView2Version(HKLM32);
  if (Version = '') or (Version = '0.0.0.0') then
    Version := WebView2Version(HKCU);
  Result := (Version <> '') and (Version <> '0.0.0.0');
end;

function Relaunch(): Boolean;
begin
  Result := ExpandConstant('{param:RELAUNCH|0}') = '1';
end;

function RelaunchArgs(Param: String): String;
begin
  Result := '';
  if ExpandConstant('{param:MINIMIZED|0}') = '1' then
    Result := '--minimized';
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ErrorCode: Integer;
begin
  { Windows 11 has it; an older Windows 10 may not, and the window would stay blank. }
  if (CurStep = ssPostInstall) and not WizardSilent() and not HasWebView2() then
    if MsgBox(CustomMessage('NoWebView2'), mbConfirmation, MB_YESNO) = IDYES then
      ShellExec('open', WebView2Download, '', '', SW_SHOWNORMAL, ewNoWait, ErrorCode);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  { Data is kept unless the user says otherwise: models and maps take hours to download. }
  if (CurUninstallStep = usPostUninstall) and DirExists(DataDir()) and not UninstallSilent() then
    if MsgBox(FmtMessage(CustomMessage('DeleteData'), [DataDir()]), mbConfirmation,
              MB_YESNO or MB_DEFBUTTON2) = IDYES then
      DelTree(DataDir(), True, True, True);
end;
