; Lan.Assistant installer (Inno Setup 6+)
; Build with scripts\build_release.ps1.  Models and Ollama are intentionally
; not included; setup_manager.py downloads them on first launch.

#ifndef MyAppVersion
  #define MyAppVersion "1.0.0"
#endif
#ifndef MyAppDisplayVersion
  #define MyAppDisplayVersion "v1.0"
#endif
#ifndef MyVersionInfo
  #define MyVersionInfo "1.0.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\dist\Lan.Assistant"
#endif

#ifdef PerUser
  #define MySetupPrivileges "lowest"
  #define MyOutputSuffix "-User"
#else
  #define MySetupPrivileges "admin"
  #define MyOutputSuffix ""
#endif

#define MyAppName "Lan.Assistant"
#define MyAppPublisher "Lan.Assistant"
#define MyAppExeName "Lan.Assistant.exe"
#define MyAppId "{{D7A2C9E1-6F3B-4B7D-9E42-4C1A8F5B0A11}"

[Setup]
AppId={#MyAppId}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppDisplayVersion}
AppPublisher={#MyAppPublisher}
VersionInfoVersion={#MyVersionInfo}
VersionInfoProductVersion={#MyVersionInfo}
DefaultDirName={code:GetDefaultDir}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
DisableDirPage=no
UsePreviousAppDir=no
DirExistsWarning=no
AllowNoIcons=yes
OutputDir=..\dist\installer
OutputBaseFilename=Lan.Assistant-Setup-{#MyAppDisplayVersion}{#MyOutputSuffix}
SetupIconFile=..\static\favicon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired={#MySetupPrivileges}
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes
RestartApplications=no
ChangesAssociations=no
MinVersion=10.0
LanguageDetectionMethod=uilanguage

[Languages]
Name: "chs"; MessagesFile: "ChineseSimplified.isl"
Name: "en"; MessagesFile: "compiler:Default.isl"
Name: "ja"; MessagesFile: "compiler:Languages\Japanese.isl"
Name: "ko"; MessagesFile: "compiler:Languages\Korean.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"
Name: "{group}\卸载 {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "启动 {#MyAppName}"; Flags: nowait postinstall skipifsilent

[Code]
function GetDefaultDir(Param: String): String;
begin
#ifdef PerUser
  Result := ExpandConstant('{localappdata}\Programs\Lan_Assistant');
#else
  { Keep the app in its own child directory. }
  if DirExists('D:\') then
    Result := 'D:\Lan_Assistant'
  else
    Result := 'C:\Lan_Assistant';
#endif
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  Selected: String;
begin
  Result := True;
  if CurPageID = wpSelectDir then
  begin
    Selected := WizardForm.DirEdit.Text;
    while (Length(Selected) > 3) and (Copy(Selected, Length(Selected), 1) = '\') do
      Delete(Selected, Length(Selected), 1);
    if CompareText(ExtractFileName(Selected), 'Lan_Assistant') <> 0 then
    begin
      if (Length(Selected) = 0) then
        Selected := 'C:\';
      if Copy(Selected, Length(Selected), 1) <> '\' then
        Selected := Selected + '\';
      WizardForm.DirEdit.Text := Selected + 'Lan_Assistant';
    end
    else
      WizardForm.DirEdit.Text := Selected;
  end;
end;
