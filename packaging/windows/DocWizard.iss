; Inno Setup script — builds DocWizard-Setup.exe from dist\DocWizard (PyInstaller onedir output).
; Per-user install (no admin rights): %LOCALAPPDATA%\Programs\DocWizard.
; Files installed by a setup program carry no "downloaded from internet" mark, so .NET (pywebview) loads them.
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{506A0AD5-437E-406E-A6BC-AA46F68A3703}
AppName=DocWizard
AppVersion={#AppVersion}
AppPublisher=Fremen
DefaultDirName={localappdata}\Programs\DocWizard
DefaultGroupName=DocWizard
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\..\dist
OutputBaseFilename=DocWizard-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\DocWizard.exe

[Languages]
Name: "dutch"; MessagesFile: "compiler:Languages\Dutch.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\..\dist\DocWizard\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\DocWizard"; Filename: "{app}\DocWizard.exe"
Name: "{group}\DocWizard verwijderen"; Filename: "{uninstallexe}"
Name: "{autodesktop}\DocWizard"; Filename: "{app}\DocWizard.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\DocWizard.exe"; Description: "{cm:LaunchProgram,DocWizard}"; Flags: nowait postinstall skipifsilent
