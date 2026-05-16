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
    upx=True,
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
    upx=True,
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
    upx=True,
    upx_exclude=[],
    name='PhonTracer_Suite',
)

if sys.platform == 'darwin':
    # 为主程序创建 .app
    app1 = BUNDLE(
        coll,
        name='PhonTracer.app',
        icon='assets/icon.icns',
        bundle_identifier='com.kasumikitsune.phonetracer',
    )
    # 为工具箱创建 .app (通常 macOS 上如果是套件，用户会更习惯在同一个目录下看到两个 app)
    # 注意：这里我们简单处理，Actions 最终会打包整个 Suite 文件夹
