; Inno Setup script: builds release\KerbApex-Setup-<version>.exe from dist\KerbApex. Run via packaging\build.ps1.
#ifndef AppVersion
  #define AppVersion "1.0.0-rc.2"
#endif
#ifndef DistDir
  #define DistDir "..\dist"
#endif
#ifndef OutDir
  #define OutDir "..\release"
#endif

[Setup]
AppId={{6B0F7C3E-5A52-4E5B-9C1D-57A1D7E0A420}
AppName=KerbApex
AppVersion={#AppVersion}
AppPublisher=KerbApex
DefaultDirName={autopf}\KerbApex
DefaultGroupName=KerbApex
LicenseFile=..\LICENSE.txt
SetupIconFile=..\assets\kerbapex.ico
OutputDir={#OutDir}
OutputBaseFilename=KerbApex-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
UninstallDisplayName=KerbApex
UninstallDisplayIcon={app}\KerbApex.exe

[Files]
Source: "{#DistDir}\KerbApex\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\KerbApex"; Filename: "{app}\KerbApex.exe"
Name: "{group}\User guide"; Filename: "{app}\_internal\docs\USER_GUIDE.md"
Name: "{group}\Uninstall KerbApex"; Filename: "{uninstallexe}"
Name: "{autodesktop}\KerbApex"; Filename: "{app}\KerbApex.exe"; Tasks: desktopicon
Name: "{userstartup}\KerbApex engineer"; Filename: "{app}\StintRadio.exe"; Parameters: "--start"; Tasks: startup

[Tasks]
Name: "desktopicon"; Description: "A desktop shortcut"; Flags: unchecked
Name: "startup"; Description: "Start the engineer with Windows (it waits for the game)"; Flags: unchecked

[Run]
Filename: "{app}\KerbApex.exe"; Description: "Open KerbApex"; Flags: postinstall nowait skipifsilent

; Settings, laps and recordings live in %APPDATA%\StintRadio and are kept on uninstall.
