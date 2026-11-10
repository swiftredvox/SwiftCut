; Inno Setup script for SwiftCut (per-user install, no admin rights needed).
; Built by packaging\release.ps1, which passes /DAppVersion=x.y.z.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{8F5FE0D6-61C9-476B-A9D1-F505CA809B60}
AppName=SwiftCut
AppVersion={#AppVersion}
AppVerName=SwiftCut {#AppVersion}
AppPublisher=SwiftRedVox
AppPublisherURL=https://github.com/swiftredvox/SwiftCut
AppSupportURL=https://github.com/swiftredvox/SwiftCut/issues
AppUpdatesURL=https://github.com/swiftredvox/SwiftCut/releases
DefaultDirName={localappdata}\Programs\SwiftCut
DefaultGroupName=SwiftCut
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist\release
OutputBaseFilename=SwiftCut-{#AppVersion}-Setup
SetupIconFile=..\swiftcut\resources\swiftcut.ico
UninstallDisplayIcon={app}\SwiftCut.exe
UninstallDisplayName=SwiftCut
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
VersionInfoVersion={#AppVersion}
VersionInfoProductName=SwiftCut
VersionInfoDescription=SwiftCut video trimmer setup

[Tasks]
Name: "explorermenu"; Description: "Add ""Trim with SwiftCut"" to the right-click menu for video files"
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[Files]
Source: "..\dist\SwiftCut\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{userprograms}\SwiftCut"; Filename: "{app}\SwiftCut.exe"; Comment: "Trim videos quickly"
Name: "{userdesktop}\SwiftCut"; Filename: "{app}\SwiftCut.exe"; Tasks: desktopicon

[Registry]
; Settings live here; remove them when SwiftCut is uninstalled.
Root: HKCU; Subkey: "Software\SwiftCut"; Flags: uninsdeletekey

[Run]
Filename: "{app}\SwiftCut.exe"; Parameters: "--register-shell"; Flags: runhidden; Tasks: explorermenu
Filename: "{app}\SwiftCut.exe"; Parameters: "--unregister-shell"; Flags: runhidden; Tasks: not explorermenu
Filename: "{app}\SwiftCut.exe"; Description: "Launch SwiftCut"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{app}\SwiftCut.exe"; Parameters: "--unregister-shell"; Flags: runhidden; RunOnceId: "RemoveExplorerMenu"

[UninstallDelete]
; Logs (and nothing else) in the user's AppData.
Type: filesandordirs; Name: "{localappdata}\SwiftCut"
