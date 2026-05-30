; Inno Setup Script for PhonTracer Suite
; 命令行编译命令示例: iscc /DMyAppVersion="v1.4.1" installer.iss

#ifndef MyAppVersion
  #define MyAppVersion "v1.4.1"
#endif

#define MyAppName "PhonTracer"
#define MyAppPublisher "KasumiKitsune"
#define MyAppURL "https://github.com/KasumiKitsune/PhonTracer"
#define MyAppExeName "PhonTracer.exe"
#define ToolkitExeName "AudioToolkit.exe"
#define MyAppAssocName "PhonTracer Project"
#define MyAppAssocExt ".teproj"
#define MyAppAssocKey "PhonTracer.Document"

[Setup]
; AppId 作为应用的唯一标识。请勿在其他安装包中复用此 AppId
AppId={{9F8229F8-55F4-42D0-BA91-C8A5D6E14CBE}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={autopf}\{#MyAppName}
ChangesAssociations=yes
DisableProgramGroupPage=yes
OutputDir=dist
OutputBaseFilename=PhonTracer_Setup_Windows
SetupIconFile=assets\icon.ico
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64

[Languages]
Name: "chinesesimplified"; MessagesFile: "assets\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "dist\PhonTracer_Suite\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs

[Registry]
; 注册工程文件关联后缀 .teproj 并在双击时通过 PhonTracer 打开
Root: HKA; Subkey: "Software\Classes\{#MyAppAssocExt}"; ValueType: string; ValueName: ""; ValueData: "{#MyAppAssocKey}"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\{#MyAppAssocExt}"; ValueType: string; ValueName: "Content Type"; ValueData: "application/vnd.phontracer.project"; Flags: uninsdeletevalue
Root: HKA; Subkey: "Software\Classes\{#MyAppAssocExt}\OpenWithProgids"; ValueType: string; ValueName: "{#MyAppAssocKey}"; ValueData: ""; Flags: uninsdeletevalue
Root: HKA; Subkey: "Software\Classes\{#MyAppAssocKey}"; ValueType: string; ValueName: ""; ValueData: "{#MyAppAssocName}"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\{#MyAppAssocKey}\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: """{app}\_internal\assets\teproj.ico"",0"
Root: HKA; Subkey: "Software\Classes\{#MyAppAssocKey}\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExeName}"" ""%1"""
Root: HKA; Subkey: "Software\Classes\Applications\{#MyAppExeName}\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExeName}"" ""%1"""
Root: HKA; Subkey: "Software\Classes\Applications\{#MyAppExeName}\SupportedTypes"; ValueType: string; ValueName: "{#MyAppAssocExt}"; ValueData: ""

[Icons]
; 在开始菜单中创建快捷方式
Name: "{autoprograms}\{#MyAppName}\{#MyAppName} 声调提取分析器"; Filename: "{app}\{#MyAppExeName}"
Name: "{autoprograms}\{#MyAppName}\AudioToolkit 音频工具箱"; Filename: "{app}\{#ToolkitExeName}"
Name: "{autoprograms}\{#MyAppName}\PhonTracerCLI 命令行界面"; Filename: "{app}\PhonTracerCLI.exe"
; 在桌面上创建快捷方式
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent
