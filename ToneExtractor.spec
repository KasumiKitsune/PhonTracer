# -*- mode: python ; coding: utf-8 -*-
import os
import sys
import customtkinter

# 动态获取 customtkinter 的安装路径，确保主题文件被包含
ctk_path = os.path.dirname(customtkinter.__file__)

# 根据平台设置隐藏导入
hidden_imports = ['parselmouth', 'PIL._tkinter_finder']
if sys.platform == 'win32':
    hidden_imports.append('windnd')

# 根据平台选择图标
icon_file = 'assets/icon.ico'
if sys.platform == 'darwin':
    # 如果有 icns 文件则使用，否则 macOS 默认
    if os.path.exists('assets/icon.icns'):
        icon_file = 'assets/icon.icns'
    else:
        icon_file = None

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
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='ToneExtractor',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon_file,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='ToneExtractor',
)
