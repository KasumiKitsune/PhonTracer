# -*- coding: utf-8 -*-
"""
PhonTracer 统一路径与资源寻址核心模块 (Layer 0 - 零第三方与零 GUI 依赖)
支持源码任意深度子包运行，以及 PyInstaller 5/6 onedir 与 onefile 冻结运行模式。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import List, Optional

# 锚定仓库根目录：本文件固定位于 <repo_root>/modules/core/paths.py，向上 3 级恒为 <repo_root>
_SOURCE_REPO_ROOT = Path(__file__).resolve().parents[2]


def is_frozen() -> bool:
    """判断当前进程是否运行在 PyInstaller 打包后的冻结环境中。"""
    return bool(getattr(sys, "frozen", False))


def get_resource_root() -> Path:
    """
    获取静态资源（如 assets/、docs/）所在的根目录：
    - PyInstaller 打包模式：优先返回 Path(sys._MEIPASS)（即 _internal 目录或临时解压目录）
    - 源码开发模式：始终返回仓库根目录，无论调用方位于多少层子包内
    """
    if is_frozen() and hasattr(sys, "_MEIPASS"):
        return Path(getattr(sys, "_MEIPASS")).resolve()
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return _SOURCE_REPO_ROOT


def get_exe_dir() -> Path:
    """
    获取可执行文件（PhonTracer.exe / Toolkit.exe / PhonTracerCLI.exe）所在的目录：
    - PyInstaller onedir 模式：返回 Path(sys.executable).parent（即 dist/PhonTracer_Suite/）
    - 源码开发模式：返回仓库根目录
    """
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return _SOURCE_REPO_ROOT


def get_asset_path(*parts: str) -> str:
    """
    获取 assets/ 目录下资源的绝对路径字符串。
    示例：get_asset_path("icons", "play.png") 或 get_asset_path("toolkit.ico")
    """
    primary = get_resource_root().joinpath("assets", *parts)
    if primary.exists():
        return str(primary)
    # 兼容回退：若外部安装包将 assets 放置于 exe 同级目录
    fallback = get_exe_dir().joinpath("assets", *parts)
    if fallback.exists():
        return str(fallback)
    return str(primary)


def resolve_sibling_command(target_stem: str) -> Optional[List[str]]:
    """
    解析同套件配套程序（Toolkit 或 PhonTracerCLI 或 PhonTracer）的跨进程启动命令列表。
    兼容：
      1. Windows/macOS PyInstaller 6 onedir 模式（外层 exe_dir）
      2. 旧版 PyInstaller 布局（_MEIPASS 目录）
      3. 源码开发模式（使用当前解释器启动根目录下的 toolkit.py / cli.py / main.py）
    """
    script_map = {
        "Toolkit": "toolkit.py",
        "PhonTracerCLI": "cli.py",
        "PhonTracer": "main.py",
    }
    if is_frozen():
        exe_name = f"{target_stem}.exe" if sys.platform == "win32" else target_stem
        # 1. 优先在主程序可执行文件同级目录查找 (dist/PhonTracer_Suite/Toolkit.exe)
        candidate_exe_dir = get_exe_dir() / exe_name
        if candidate_exe_dir.is_file():
            return [str(candidate_exe_dir)]
        # 2. 次选在 _MEIPASS (_internal) 目录查找
        candidate_meipass = get_resource_root() / exe_name
        if candidate_meipass.is_file():
            return [str(candidate_meipass)]
        return None

    # 源码模式
    script_rel = script_map.get(target_stem, f"{target_stem.lower()}.py")
    script_path = _SOURCE_REPO_ROOT / script_rel
    if not script_path.is_file():
        return None

    python_exe = sys.executable
    if sys.platform == "win32" and target_stem == "PhonTracerCLI":
        # 启动交互式 CLI 时，若当前是 pythonw.exe，需切换为带控制台的 python.exe
        if python_exe.lower().endswith("pythonw.exe"):
            alt_py = python_exe[:-11] + "python.exe"
            if os.path.exists(alt_py):
                python_exe = alt_py
    return [python_exe, str(script_path)]


# 别名兼容（蓝图架构图中提到 resolve_suite_executable）
resolve_suite_executable = resolve_sibling_command
