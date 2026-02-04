; ============================================================================
; PowerTrader AI - Windows Installer Script (Inno Setup)
; ============================================================================
;
; This script creates a professional Windows installer for PowerTrader AI.
; It packages all three executables (pt_hub, pt_thinker, pt_trader) and their
; dependencies into a single .exe installer with uninstall support.
;
; Requirements:
;   - Inno Setup 6.0 or later (https://jrsoftware.org/isinfo.php)
;   - All executables built in dist/ folder
;   - Custom icon available at assets/icon.ico
;
; Build:
;   Open this file in Inno Setup Compiler and click Compile (Ctrl+F9)
;   Or use command line: iscc.exe installer_windows.iss
;
; Output:
;   Output\PowerTrader_AI_Setup.exe - Ready-to-distribute installer
;
; Author: PowerTrader AI Contributors
; License: Apache 2.0
; ============================================================================

[Setup]
; ============================================================================
; Application Information
; ============================================================================
AppName=PowerTrader AI
AppVersion=1.0.0
AppVerName=PowerTrader AI 1.0.0
AppPublisher=HMAHD (Akash Hasendra)
AppPublisherURL=https://github.com/HMAHD/PowerTrader_AI
AppSupportURL=https://github.com/HMAHD/PowerTrader_AI/issues
AppUpdatesURL=https://github.com/HMAHD/PowerTrader_AI/releases
AppCopyright=Copyright (C) 2026 PowerTrader AI Contributors

; ============================================================================
; Installation Directories
; ============================================================================
DefaultDirName={autopf}\PowerTrader_AI
DefaultGroupName=PowerTrader AI
; Allow users to change install location
DisableDirPage=no
DisableProgramGroupPage=no

; ============================================================================
; Output Configuration
; ============================================================================
OutputDir=Output
OutputBaseFilename=PowerTrader_AI_Setup
; Single file installer with compression
Compression=lzma2
SolidCompression=yes
; LZMA2 provides best compression ratio

; ============================================================================
; Architecture and Requirements
; ============================================================================
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Require Windows 10 or later (version 10.0)
MinVersion=10.0

; ============================================================================
; Privileges and Security
; ============================================================================
; Run installer as administrator to install to Program Files
PrivilegesRequired=admin
; Don't require privileges for silent install (not recommended for this app)
PrivilegesRequiredOverridesAllowed=dialog

; ============================================================================
; User Interface
; ============================================================================
; Custom icon for installer and installed app
SetupIconFile=assets\icon.ico
; Uninstall icon
UninstallDisplayIcon={app}\PowerTrader_AI\PowerTrader_AI.exe
; Show language selection dialog
ShowLanguageDialog=yes
; Modern wizard style
WizardStyle=modern
; Disable "What's New" page
DisableWelcomePage=no

; ============================================================================
; Uninstall Configuration
; ============================================================================
UninstallDisplayName=PowerTrader AI
; Don't allow uninstall without admin rights
UninstallFilesDir={app}\uninstall
Uninstallable=yes

; ============================================================================
; License and Info Pages
; ============================================================================
; Use README as info file (shown before install)
InfoBeforeFile=README.md
; License file (if you have one)
; LicenseFile=LICENSE

; ============================================================================
; Version Information Resource
; ============================================================================
VersionInfoVersion=1.0.0.0
VersionInfoCompany=PowerTrader AI Contributors
VersionInfoDescription=PowerTrader AI Installer
VersionInfoTextVersion=1.0.0
VersionInfoCopyright=Copyright (C) 2026 PowerTrader AI Contributors
VersionInfoProductName=PowerTrader AI
VersionInfoProductVersion=1.0.0.0

; ============================================================================

[Languages]
; ============================================================================
; Supported Languages
; ============================================================================
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"
Name: "french"; MessagesFile: "compiler:Languages\French.isl"
Name: "german"; MessagesFile: "compiler:Languages\German.isl"
Name: "japanese"; MessagesFile: "compiler:Languages\Japanese.isl"
Name: "chinese"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

; ============================================================================

[Tasks]
; ============================================================================
; Installation Tasks (user-selectable options)
; ============================================================================
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "quicklaunchicon"; Description: "{cm:CreateQuickLaunchIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked; OnlyBelowVersion: 6.1; Check: not IsAdminInstallMode

; ============================================================================

[Files]
; ============================================================================
; Files to Install
; ============================================================================

; Main Application (pt_hub) - GUI
Source: "dist\PowerTrader_AI\*"; DestDir: "{app}\PowerTrader_AI"; Flags: ignoreversion recursesubdirs createallsubdirs
; Note: pt_hub builds to PowerTrader_AI folder (specified in spec file)

; AI Signal Processor (pt_thinker)
Source: "dist\pt_thinker\*"; DestDir: "{app}\pt_thinker"; Flags: ignoreversion recursesubdirs createallsubdirs

; Trading Engine (pt_trader)
Source: "dist\pt_trader\*"; DestDir: "{app}\pt_trader"; Flags: ignoreversion recursesubdirs createallsubdirs

; Documentation
Source: "README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "docs\*"; DestDir: "{app}\docs"; Flags: ignoreversion recursesubdirs createallsubdirs

; Assets (icons, logos)
Source: "assets\*"; DestDir: "{app}\assets"; Flags: ignoreversion recursesubdirs createallsubdirs

; ============================================================================

[Icons]
; ============================================================================
; Shortcuts and Icons
; ============================================================================

; Start Menu shortcuts
Name: "{group}\PowerTrader AI"; Filename: "{app}\PowerTrader_AI\PowerTrader_AI.exe"; Comment: "Launch PowerTrader AI"; IconFilename: "{app}\assets\icon.ico"
Name: "{group}\Documentation"; Filename: "{app}\docs"; Comment: "View Documentation"
Name: "{group}\{cm:UninstallProgram,PowerTrader AI}"; Filename: "{uninstallexe}"

; Desktop shortcut (optional, user-selectable)
Name: "{autodesktop}\PowerTrader AI"; Filename: "{app}\PowerTrader_AI\PowerTrader_AI.exe"; Tasks: desktopicon; IconFilename: "{app}\assets\icon.ico"

; Quick Launch shortcut (optional, for older Windows)
Name: "{userappdata}\Microsoft\Internet Explorer\Quick Launch\PowerTrader AI"; Filename: "{app}\PowerTrader_AI\PowerTrader_AI.exe"; Tasks: quicklaunchicon; IconFilename: "{app}\assets\icon.ico"

; ============================================================================

[Run]
; ============================================================================
; Post-Installation Actions
; ============================================================================

; Offer to launch the application after installation
Name: "{app}\PowerTrader_AI\PowerTrader_AI.exe"; Description: "{cm:LaunchProgram,PowerTrader AI}"; Flags: nowait postinstall skipifsilent

; ============================================================================

[UninstallDelete]
; ============================================================================
; Files to Delete on Uninstall
; ============================================================================

; Clean up user data (optional - be careful!)
; Type: filesandordirs; Name: "{userappdata}\PowerTrader_AI"

; Clean up logs
Type: files; Name: "{app}\*.log"

; ============================================================================

[Code]
; ============================================================================
; Custom Pascal Script Code
; ============================================================================

// Check if .NET Framework is installed (if needed)
// function IsDotNetDetected(version: string; service: cardinal): boolean;
// begin
//   // Add .NET detection code here if needed
//   Result := true;
// end;

// Custom initialization
procedure InitializeWizard();
var
  WelcomePage: TWizardPage;
begin
  // Display welcome message
  // You can add custom pages here if needed
end;

// Pre-installation checks
function InitializeSetup(): Boolean;
begin
  Result := True;

  // Check for minimum Windows version
  if not (GetWindowsVersion >= $0A000000) then
  begin
    MsgBox('PowerTrader AI requires Windows 10 or later.' + #13#10 +
           'Please upgrade your operating system.', mbError, MB_OK);
    Result := False;
    Exit;
  end;

  // Check available disk space (approximate)
  // PowerTrader AI requires ~250 MB
  // if GetSpaceOnDisk(ExpandConstant('{app}'), False, , ) < 250000000 then
  // begin
  //   MsgBox('Insufficient disk space. At least 250 MB required.', mbError, MB_OK);
  //   Result := False;
  // end;
end;

// Post-installation message
procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    // Show important warning after installation
    MsgBox('IMPORTANT: PowerTrader AI trades automatically with real money.' + #13#10 + #13#10 +
           '• Keep your API keys private' + #13#10 +
           '• Start with small amounts' + #13#10 +
           '• Monitor the bot regularly' + #13#10 +
           '• Read the documentation thoroughly' + #13#10 + #13#10 +
           'You are responsible for all trades made by this software.',
           mbInformation, MB_OK);
  end;
end;

; ============================================================================
