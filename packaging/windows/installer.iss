; Inno Setup script for the Fexor Kiosk desktop app. Built by packaging/build.py.
; Installs per user (no administrator rights needed).

#ifndef Version
  #define Version "0.1.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\dist\Fexor Kiosk"
#endif
#ifndef OutputDir
  #define OutputDir "..\dist"
#endif

[Setup]
AppId={{6F2C8A31-4B7E-4D19-9A55-0E8F1C2B7D40}
AppName=Fexor Kiosk
AppVersion={#Version}
AppPublisher=Fexor
DefaultDirName={localappdata}\Programs\Fexor Kiosk
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir={#OutputDir}
OutputBaseFilename=FexorKiosk-Setup-{#Version}-windows-x64
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes
UninstallDisplayIcon={app}\Fexor Kiosk.exe

[Tasks]
Name: "autostart"; Description: "Start the kiosk automatically when this user signs in"
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{autoprograms}\Fexor Kiosk"; Filename: "{app}\Fexor Kiosk.exe"
Name: "{autodesktop}\Fexor Kiosk"; Filename: "{app}\Fexor Kiosk.exe"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; \
  ValueName: "Fexor Kiosk"; ValueData: """{app}\Fexor Kiosk.exe"""; Flags: uninsdeletevalue; \
  Tasks: autostart

[Run]
Filename: "{app}\Fexor Kiosk.exe"; Description: "Start Fexor Kiosk now"; \
  Flags: nowait postinstall skipifsilent
