#define AppName "Killryujin"
#define AppVersion "0.1.0-alpha"
#define AppPublisher "killryujin contributors"

[Setup]
AppId={{8F3A6C21-4B9E-4D17-A2C8-1E5F70B9D4A6}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppCopyright=Copyright (c) 2026 killryujin contributors
VersionInfoVersion=0.1.0.0
VersionInfoProductVersion=0.1.0.0
VersionInfoCompany={#AppPublisher}
VersionInfoDescription=ASUS ROG Ryujin III LCD
DefaultDirName={autopf}\killryujin
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
LicenseFile=..\LICENSE
OutputDir=..\dist
OutputBaseFilename=killryujin-setup
SetupIconFile=..\killryujin\icon.ico
UninstallDisplayIcon={app}\killryujin.exe
UninstallDisplayName={#AppName}
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
WizardStyle=modern
Compression=lzma2
SolidCompression=yes
CloseApplications=yes
RestartApplications=no
UsedUserAreasWarning=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: unchecked

[Files]
Source: "..\dist\killryujin.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\killryujin.exe"; Comment: "Ryujin III LCD without Armoury Crate"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\killryujin.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\killryujin.exe"; Description: "Launch Killryujin"; Flags: nowait postinstall skipifsilent
