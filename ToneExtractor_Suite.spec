# -*- mode: python ; coding: utf-8 -*-
import os
import sys
import customtkinter

# 获取 customtkinter 资源路径
ctk_path = os.path.dirname(customtkinter.__file__)

# 隐式导入与排除列表
hidden_imports = [
    'parselmouth', 'PIL._tkinter_finder', 'xlsxwriter',
    'scipy.interpolate', 'scipy.signal', 'scipy.stats', 'scipy.special._cdflib'
]
if sys.platform == 'win32':
    hidden_imports.append('windnd')

excluded_modules = [
    'seaborn', 'pandas', 'torch', 'torchvision', 'torchaudio', 
    'whisper', 'matplotlib.tests', 'IPython', 'jupyter', 
    'notebook', 'sqlite3', 'numpy.f2py'
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

pyz_a = PYZ(a.pure, a.zipped_data, cipher=None)
pyz_b = PYZ(b.pure, b.zipped_data, cipher=None)

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

# --- 5. 统一收集到同一个目录 ---
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
