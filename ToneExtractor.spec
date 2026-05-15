# -*- mode: python ; coding: utf-8 -*-
import os
import sys
import customtkinter

# 动态获取 customtkinter 的安装路径，确保主题文件被包含
ctk_path = os.path.dirname(customtkinter.__file__)

# 根据平台设置隐藏导入
hidden_imports = [
    'parselmouth', 
    'PIL._tkinter_finder',
    'xlsxwriter',
    'scipy.interpolate',
    'scipy.signal',
    'scipy.stats'
]
if sys.platform == 'win32':
    hidden_imports.append('windnd')

# 显式排除不需要的大型库，防止环境污染导致包体积过大
excluded_modules = [
    'seaborn',
    'pandas',
    'torch',
    'torchvision',
    'torchaudio',
    'whisper',
    'matplotlib.tests',
    'IPython',
    'jupyter',
    'notebook',
    'sqlite3',
    'numpy.f2py',
    'tkinter.test',
    'PIL.ImageQt',
    'PIL.ImageTk' if sys.platform != 'win32' else '', # Windows 下有时需要，保留
    'matplotlib.backends.backend_qt5agg',
    'matplotlib.backends.backend_qt4agg',
    'matplotlib.backends.backend_qtagg',
    'matplotlib.backends.backend_wxagg',
    'matplotlib.backends.backend_gtk3agg',
    'matplotlib.backends.backend_gtk4agg',
]

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
    excludes=[m for m in excluded_modules if m],
    noarchive=False,
    optimize=0, # 降低优化等级，防止 numpy 因为文档字符串被删除而报错
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
    upx=False, 
    upx_exclude=[],
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
    upx=False, 
    upx_exclude=[],
    name='ToneExtractor',
)

if sys.platform == 'darwin':
    app = BUNDLE(
        coll,
        name='ToneExtractor.app',
        icon=icon_file,
        bundle_identifier='com.kasumikitsune.toneextractor',
    )
