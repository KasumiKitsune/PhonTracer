# -*- mode: python ; coding: utf-8 -*-
import os
import sys
import customtkinter

# 获取 customtkinter 资源路径
ctk_path = os.path.dirname(customtkinter.__file__)

# 隐式导入与排除列表
hidden_imports = [
    'parselmouth', 'PIL._tkinter_finder', 'xlsxwriter',
    'scipy.interpolate', 'scipy.signal', 'scipy.stats', 'scipy.special._cdflib',
    'textgrid'
]
if sys.platform == 'win32':
    hidden_imports.append('windnd')

excluded_modules = [
    'seaborn', 'pandas', 'torch', 'torchvision', 'torchaudio', 
    'whisper', 'matplotlib.tests', 'IPython', 'jupyter', 
    'notebook', 'sqlite3'
]

# --- 1. 分析主程序 ---
a = Analysis(
    ['main.py'],
    pathex=['.'],
    binaries=[],
    datas=[
        ('icons', 'icons'), 
        ('assets', 'assets'),
        (ctk_path, 'customtkinter')
    ],
    hiddenimports=hidden_imports,
    excludes=excluded_modules,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=None,
    noarchive=False,
    optimize=0,
)

# --- 2. 分析音频工具箱 ---
b = Analysis(
    ['audio_toolkit.py'],
    pathex=['.'],
    binaries=[],
    datas=[
        ('icons', 'icons'),
        ('assets', 'assets'),
        (ctk_path, 'customtkinter')
    ],
    hiddenimports=hidden_imports,
    excludes=excluded_modules,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=None,
    noarchive=False,
    optimize=0,
)

is_win = (sys.platform == 'win32')

# --- 3. 分析命令行工具 (仅在 Windows 上执行，macOS 无需打包 CLI) ---
if is_win:
    c = Analysis(
        ['cli.py'],
        pathex=['.'],
        binaries=[],
        datas=[],  # CLI 不需要 GUI 资源
        hiddenimports=hidden_imports,
        excludes=excluded_modules + ['customtkinter', 'PIL', 'tkinter'], # CLI 完全排除 GUI 库，超强瘦身！
        win_no_prefer_redirects=False,
        win_private_assemblies=False,
        cipher=None,
        noarchive=False,
        optimize=0,
    )

pyz_a = PYZ(a.pure, a.zipped_data, cipher=None)
pyz_b = PYZ(b.pure, b.zipped_data, cipher=None)
if is_win:
    pyz_c = PYZ(c.pure, c.zipped_data, cipher=None)

# --- 3. 定义主程序 EXE ---
exe1 = EXE(
    pyz_a,
    a.scripts,
    [],
    exclude_binaries=True,
    name='PhonTracer',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='assets/icon.ico',
)

# --- 4. 定义音频工具箱 EXE ---
exe2 = EXE(
    pyz_b,
    b.scripts,
    [],
    exclude_binaries=True,
    name='AudioToolkit',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='assets/tool_icon.ico',
)

# --- 5. 定义命令行工具 EXE ---
if is_win:
    exe3 = EXE(
        pyz_c,
        c.scripts,
        [],
        exclude_binaries=True,
        name='PhonTracerCLI',
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=True,  # 命令行工具必须开启终端显示
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
        icon='assets/cli.ico',
    )

# --- 6. 统一收集到同一个目录 ---
if is_win:
    coll = COLLECT(
        exe1,
        a.binaries,
        a.zipfiles,
        a.datas,
        exe2,
        b.binaries,
        b.zipfiles,
        b.datas,
        exe3,
        c.binaries,
        c.zipfiles,
        c.datas,
        strip=False,
        upx=False,
        upx_exclude=[],
        name='PhonTracer_Suite',
    )
else:
    coll = COLLECT(
        exe1,
        a.binaries,
        a.zipfiles,
        a.datas,
        exe2,
        b.binaries,
        b.zipfiles,
        b.datas,
        strip=False,
        upx=False,
        upx_exclude=[],
        name='PhonTracer_Suite',
    )

if sys.platform == 'darwin':
    # 为主程序创建 .app
    app1 = BUNDLE(
        coll,
        name='PhonTracer_Suite/PhonTracer.app',
        icon='assets/icon.icns',
        bundle_identifier='com.kasumikitsune.phonetracer',
    )
    # 为工具箱创建 .app (通常 macOS 上如果是套件，用户会更习惯在同一个目录下看到两个 app)
    app2 = BUNDLE(
        coll,
        name='PhonTracer_Suite/AudioToolkit.app',
        icon='assets/tool_icon.icns',
        bundle_identifier='com.kasumikitsune.audiotoolkit',
    )
