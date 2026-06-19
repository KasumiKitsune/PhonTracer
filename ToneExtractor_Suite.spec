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
    'textgrid', 'matplotlib.backends.backend_svg'
]
if sys.platform == 'win32':
    hidden_imports.append('windnd')

excluded_modules = [
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
    hiddenimports=hidden_imports,
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
    cli_hidden_imports = [
        item for item in hidden_imports
        if item != 'PIL._tkinter_finder'
    ] + ['matplotlib.backends.backend_agg']

    c = Analysis(
        ['cli.py'],
        pathex=['.'],
        binaries=[],
        datas=[],
        hiddenimports=cli_hidden_imports,
        excludes=excluded_modules + ['customtkinter', 'tkinter'], # CLI 排除 GUI 库，但保留 Pillow 供 Matplotlib 无窗口导出
        win_no_prefer_redirects=False,
        win_private_assemblies=False,
        cipher=None,
        noarchive=False,
        optimize=0,
    )

# --- 4. 分析 PhonRec 配套分析引擎 ---
engine_hidden_imports = [
    'scipy.io', 'scipy.signal', 'matplotlib.backends.backend_agg',
    'uvicorn.loops', 'uvicorn.loops.auto',
    'uvicorn.protocols', 'uvicorn.protocols.http', 'uvicorn.protocols.http.auto',
    'uvicorn.protocols.websockets', 'uvicorn.protocols.websockets.auto',
    'uvicorn.lifespan', 'uvicorn.lifespan.on', 'multipart'
]
d = Analysis(
    ['PhonRec/backend/main.py'],
    pathex=['.', 'PhonRec/backend'],
    binaries=[],
    datas=[],
    hiddenimports=engine_hidden_imports,
    excludes=excluded_modules + ['customtkinter', 'tkinter'],
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
pyz_d = PYZ(d.pure, d.zipped_data, cipher=None)

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

# --- 6. 定义 PhonRec 分析引擎 EXE ---
exe4 = EXE(
    pyz_d,
    d.scripts,
    [],
    exclude_binaries=True,
    name='PhonTracerAnalysisEngine',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
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
        exe4,
        d.binaries,
        d.zipfiles,
        d.datas,
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
        exe4,
        d.binaries,
        d.zipfiles,
        d.datas,
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
