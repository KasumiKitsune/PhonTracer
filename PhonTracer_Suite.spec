# -*- mode: python ; coding: utf-8 -*-
import os
import sys
import customtkinter
from PyInstaller.utils.hooks import collect_submodules

# 获取 customtkinter 资源路径
ctk_path = os.path.dirname(customtkinter.__file__)

# 1. 自动收集 modules 下所有子模块，但按【无头层】与【GUI层】精准分流
GUI_ONLY_PREFIXES = (
    'modules.gui',
    'modules.toolkit_ui',
    'modules.ui_dialogs',
    'modules.app',
    'modules.project_tree',
    'modules.spectrogram_panel',
    'modules.visual_splitter',
    'modules.wordlist_editor',
    'modules.ui_widgets',
    'modules.about_dialog',
    'modules.update_dialog',
    'modules.project_import_dialog',
    'modules.f0_detection_dialog',
    'modules.formant_detection_dialog',
)

all_modules_subpackages = collect_submodules('modules')
headless_submodules = [
    m for m in all_modules_subpackages
    if not m.startswith(GUI_ONLY_PREFIXES)
]
gui_submodules = [
    m for m in all_modules_subpackages
    if m.startswith(GUI_ONLY_PREFIXES)
]

# 预加载运行时实际使用的 numpy / scipy 子包，捕获 Cython 扩展与动态 __getattr__ 注入的全部子模块
import numpy
import scipy.stats
import scipy.special
import scipy.signal
import scipy.interpolate
import scipy.ndimage
import scipy.spatial
import scipy.io.wavfile
import scipy.fft
import scipy.linalg

def _safe_collect_submodules(pkg_name):
    try:
        return collect_submodules(pkg_name)
    except Exception:
        return []

runtime_scientific_imports = sorted(
    m for m in sys.modules
    if m.startswith(('numpy', 'scipy')) and not m.startswith(('numpy.f2py', 'numpy.distutils', 'numpy.testing', 'scipy.conftest'))
)

base_hidden_imports = [
    'parselmouth', 'xlsxwriter',
    'numpy.core', 'numpy.core.multiarray', 'numpy.core._multiarray_umath',
    'numpy.core.umath', 'numpy.core.numeric', 'numpy.core.fromnumeric',
    'numpy._core', 'numpy._core.multiarray', 'numpy._core._multiarray_umath',
    'numpy._core.umath', 'numpy._core.numeric', 'numpy._core.fromnumeric',
    'scipy._cyutility', 'scipy._lib._ccallback_c',
    'scipy.interpolate', 'scipy.signal',
    'scipy.stats', 'scipy.stats._stats_py', 'scipy.stats._kde', 'scipy.stats.kde',
    'scipy.special', 'scipy.special._ufuncs', 'scipy.special._ufuncs_cxx',
    'scipy.special.cython_special', 'scipy.special._cdflib',
    'scipy.linalg', 'scipy.linalg.cython_blas', 'scipy.linalg.cython_lapack',
    'scipy.ndimage', 'scipy.spatial', 'scipy.io.wavfile', 'scipy.fft',
    'textgrid', 'matplotlib.backends.backend_svg',
    *_safe_collect_submodules('numpy._core'),
    *_safe_collect_submodules('numpy.core'),
    *_safe_collect_submodules('scipy.stats'),
    *_safe_collect_submodules('scipy.special'),
    *runtime_scientific_imports,
    *headless_submodules,
]

gui_hidden_imports = [
    *base_hidden_imports,
    *gui_submodules,
    'PIL._tkinter_finder',
]
if sys.platform == 'win32':
    gui_hidden_imports.append('windnd')

excluded_modules = [
    'uharfbuzz',
    'seaborn', 'pandas', 'torch', 'torchvision', 'torchaudio',
    'whisper', 'matplotlib.tests', 'IPython', 'jupyter',
    'notebook', 'sqlite3', 'numba', 'llvmlite', 'gradio', 'altair',
    'PyQt5', 'PyQt6', 'PySide2', 'PySide6', 'qtpy'
]

# --- 1. 分析主程序 ---
a = Analysis(
    ['main.py'],
    pathex=['.'],
    binaries=[],
    datas=[
        ('assets', 'assets'),
        (ctk_path, 'customtkinter')
    ],
    hiddenimports=gui_hidden_imports,
    excludes=excluded_modules,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=None,
    noarchive=False,
    optimize=0,
)

# --- 2. 分析工具箱 ---
b = Analysis(
    ['toolkit.py'],
    pathex=['.'],
    binaries=[],
    datas=[
        ('assets', 'assets'),
        (ctk_path, 'customtkinter')
    ],
    hiddenimports=gui_hidden_imports,
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
    cli_hidden_imports = [
        *base_hidden_imports,
        'matplotlib.backends.backend_agg',
        *collect_submodules('modules.cli_app'),
    ]

    c = Analysis(
        ['cli.py'],
        pathex=['.'],
        binaries=[],
        datas=[],
        hiddenimports=cli_hidden_imports,
        excludes=excluded_modules + ['customtkinter', 'tkinter', *gui_submodules],  # CLI 排除 GUI 库与 GUI 子包
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

# --- 4. 定义工具箱 EXE ---
exe2 = EXE(
    pyz_b,
    b.scripts,
    [],
    exclude_binaries=True,
    name='Toolkit',
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
    icon='assets/toolkit.ico',
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


# --- 7. 统一收集到同一个目录 ---
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
    # 为 PhonTracer 准备独立的 COLLECT 和 BUNDLE
    coll1 = COLLECT(
        exe1,
        a.binaries,
        a.zipfiles,
        a.datas,
        strip=False,
        upx=False,
        upx_exclude=[],
        name='PhonTracer_App',
    )
    app1 = BUNDLE(
        coll1,
        name='PhonTracer.app',
        icon='assets/icon.icns',
        bundle_identifier='com.kasumikitsune.phonetracer',
    )

    # 为 Toolkit 准备独立的 COLLECT 和 BUNDLE
    coll2 = COLLECT(
        exe2,
        b.binaries,
        b.zipfiles,
        b.datas,
        strip=False,
        upx=False,
        upx_exclude=[],
        name='Toolkit_App',
    )
    app2 = BUNDLE(
        coll2,
        name='Toolkit.app',
        icon='assets/toolkit.icns',
        bundle_identifier='com.kasumikitsune.toolkit',
    )
