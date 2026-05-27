# -*- mode: python ; coding: utf-8 -*-
import os
import sys
import customtkinter
from PyInstaller.utils.hooks import collect_submodules, copy_metadata

# 1. 动态获取 customtkinter 的安装路径，确保主题和 json 文件被包含
ctk_path = os.path.dirname(customtkinter.__file__)
package_datas = copy_metadata('pyreaper') + copy_metadata('setuptools')

# 2. 隐式导入列表（PyInstaller 自动检测不到的模块）
hidden_imports = [
    'parselmouth', 
    'PIL._tkinter_finder',
    'xlsxwriter',
    'scipy.interpolate',
    'scipy.signal',
    'scipy.stats',
    'scipy.special._cdflib', # scipy 经常漏掉的底层库
    'textgrid',
    'pyreaper',
    'setuptools',
    'pkg_resources',
    'matplotlib.backends.backend_svg'
] + collect_submodules('pkg_resources')

if sys.platform == 'win32':
    hidden_imports.append('windnd')

# 3. 显式排除列表（优化体积，防止环境污染）
excluded_modules = [
    'seaborn', 'pandas', 'torch', 'torchvision', 'torchaudio', 
    'whisper', 'matplotlib.tests', 'IPython', 'jupyter', 
    'notebook', 'sqlite3', 'numpy.f2py', 'tkinter.test',
    'PIL.ImageQt', 'PIL.ImageTk' if sys.platform != 'win32' else ''
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
        ('assets', 'assets'),
        (ctk_path, 'customtkinter')
    ] + package_datas,
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
    upx=False,          # 强制关闭 UPX
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
    upx=False,          # 强制关闭 UPX
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
