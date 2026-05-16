# -*- mode: python ; coding: utf-8 -*-
import os
import sys
import customtkinter

# 1. 动态获取 customtkinter 的安装路径，确保主题和 json 文件被包含
ctk_path = os.path.dirname(customtkinter.__file__)

# 2. 隐式导入列表（PyInstaller 自动检测不到的模块）
hidden_imports = [
    'parselmouth', 
    'PIL._tkinter_finder',
    'xlsxwriter',
    'scipy.interpolate',
    'scipy.signal',
    'scipy.stats',
    'scipy.special._cdflib' # scipy 经常漏掉的底层库
]

if sys.platform == 'win32':
    hidden_imports.append('windnd')

# 3. 显式排除列表（优化体积，防止环境污染）
excluded_modules = [
    'seaborn', 'pandas', 'torch', 'torchvision', 'torchaudio', 
    'whisper', 'matplotlib.tests', 'IPython', 'jupyter', 
    'notebook', 'sqlite3', 'numpy.f2py', 'tkinter.test',
    'PIL.ImageQt', 'PIL.ImageTk' if sys.platform != 'win32' else '',
    # Aggressive exclusions
    'matplotlib.backends.backend_qt5agg', 'matplotlib.backends.backend_qt5',
    'matplotlib.backends.backend_qt', 'matplotlib.backends.backend_webagg',
    'matplotlib.backends.backend_webagg_core', 'matplotlib.backends.backend_wxagg',
    'matplotlib.backends.backend_wx', 'matplotlib.backends.backend_cairo',
    'matplotlib.backends.backend_gtk3agg', 'matplotlib.backends.backend_gtk3',
    'matplotlib.backends.backend_gtk4agg', 'matplotlib.backends.backend_gtk4',
    'matplotlib.backends.backend_macosx', 'matplotlib.backends.backend_nbagg',
    'matplotlib.backends.backend_pgf', 'matplotlib.backends.backend_ps',
    'matplotlib.backends.backend_svg', 'matplotlib.backends.backend_template',
    'matplotlib.backends.qt_compat',
    'PyQt5', 'PyQt6', 'PySide2', 'PySide6', 'wx',
    'pydoc', 'xmlrpc', 'http.server', 'urllib.request',
    'email', 'html', 'multiprocessing.dummy'
]

# 4. 图标逻辑
icon_file = 'assets/icon.ico'
if sys.platform == 'darwin':
    icon_file = 'assets/icon.icns' if os.path.exists('assets/icon.icns') else None

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
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=None,
    noarchive=False,
    optimize=0, # 关键：设置为 0 防止 numpy 等库的 docstring 被删导致 runtime error
)

pyz = PYZ(a.pure, a.zipped_data, cipher=None)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='ToneExtractor',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False, 
    upx=True,           # 强制开启 UPX
    upx_exclude=[],
    console=False,      # 如果需要调试，可改为 True 查看后台报错
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
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,           # 强制开启 UPX
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
