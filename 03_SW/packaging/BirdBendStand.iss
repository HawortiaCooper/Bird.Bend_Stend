; Inno Setup 6 script — Bird Bend Stand Windows installer (optional; the zip is the primary deliverable).
; Owner: Implementer B (SW_design §24, packaging/README.md). Built by build_dist.ps1 -Installer when ISCC.exe exists:
;   ISCC /DAppVersion=0.1.0+ge600169 /DAppVersionTuple=0.1.0.0 /DDistDir=<03_SW\dist\BirdBendStand-...>
;        /DOutputDir=<03_SW\dist> /DOutputBase=BirdBendStand-...-setup BirdBendStand.iss
; Installs the PyInstaller one-folder build (no Python needed on the target). Per-user install by default (no admin
; rights; the user may choose "all users"). Data in %APPDATA%\BirdBendStand and recordings are never removed by
; the uninstaller. Implements: SW-PLT-001 (installable Windows 10 application)

#ifndef AppVersion
  #define AppVersion "0.0.0+dev"
#endif
#ifndef AppVersionTuple
  #define AppVersionTuple "0.0.0.0"
#endif
#ifndef DistDir
  #error "DistDir (the built 03_SW\dist\BirdBendStand-<ver> folder) must be given: ISCC /DDistDir=..."
#endif
#ifndef OutputDir
  #define OutputDir "."
#endif
#ifndef OutputBase
  #define OutputBase "BirdBendStand-setup"
#endif

[Setup]
AppId={{9557D355-5990-4F96-B6BE-691A183B1058}
AppName=Bird Bend Stand
AppVersion={#AppVersion}
AppVerName=Bird Bend Stand {#AppVersion}
AppPublisher=Bird Bend Stand project
VersionInfoVersion={#AppVersionTuple}
VersionInfoProductName=Bird Bend Stand
DefaultDirName={autopf}\BirdBendStand
DefaultGroupName=Bird Bend Stand
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#OutputDir}
OutputBaseFilename={#OutputBase}
SetupIconFile=assets\BirdBendStand.ico
UninstallDisplayIcon={app}\BirdBendStand.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; an older version is replaced in place (same AppId); running instances must be closed first
CloseApplications=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[InstallDelete]
; remove the previous version's runtime and docs so no stale module / screenshot survives an update
Type: filesandordirs; Name: "{app}\_internal"
Type: filesandordirs; Name: "{app}\docs"

[Files]
Source: "{#DistDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Bird Bend Stand"; Filename: "{app}\BirdBendStand.exe"
Name: "{group}\Bird Bend Stand (simulator)"; Filename: "{app}\BirdBendStand.exe"; Parameters: "--sim"
Name: "{group}\Operator manual"; Filename: "{app}\docs\USER_MANUAL.html"
Name: "{group}\Quick reference card"; Filename: "{app}\docs\QUICK_REFERENCE.html"
Name: "{group}\Build info"; Filename: "{app}\BUILD_INFO.txt"
Name: "{group}\{cm:UninstallProgram,Bird Bend Stand}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Bird Bend Stand"; Filename: "{app}\BirdBendStand.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\BirdBendStand.exe"; Parameters: "--sim"; Description: "Start Bird Bend Stand with the simulator"; Flags: nowait postinstall skipifsilent unchecked
