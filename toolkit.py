"""
PhonTracer 语音学标注与声学分析系统 - 音频与数据处理工具箱根入口。
通过 modules.toolkit_ui 的 6 个 Mixin 多继承组装 ToolkitApp。
"""
import copy
import os
import sys
import threading
import traceback
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
import customtkinter as ctk

# 兼容 parselmouth 导入与 Tcl/Tk 路径
try:
    import parselmouth
except ImportError:
    from modules import _parselmouth_fallback as parselmouth
    import sys as _sys
    _sys.modules.setdefault("parselmouth", parselmouth)
    _sys.modules.setdefault("parselmouth.praat", parselmouth.praat)

_tcl_dir = os.path.join(getattr(sys, "base_prefix", sys.prefix), "tcl", "tcl8.6")
_tk_dir = os.path.join(getattr(sys, "base_prefix", sys.prefix), "tcl", "tk8.6")
if os.path.isdir(_tcl_dir):
    os.environ.setdefault("TCL_LIBRARY", _tcl_dir)
if os.path.isdir(_tk_dir):
    os.environ.setdefault("TK_LIBRARY", _tk_dir)

# 高 DPI 感知支持
if sys.platform == "win32":
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

# 核心音频与组件重导出（兼容旧版测试与依赖）
from modules.audio_core import (
    SILENCE_AMPLITUDE_THRESHOLD,
    macroscopic_vad,
    trim_bounds_by_amplitude,
)
from modules.ui_widgets import (
    CTkReleaseButton,
    _apply_custom_arrow,
    bind_text_context_menu,
    install_dynamic_checkbox_patch,
)
from modules.visual_splitter import VisualSplitter
from modules.core.text_io import parse_flat_wordlist, read_text_file_with_fallback
from modules.scripting.meta_parser import (
    SCRIPT_EDITOR_DEFAULT_DESC,
    SCRIPT_EDITOR_DEFAULT_NAME,
    SCRIPT_META_DESC_KEYS,
    SCRIPT_META_NAME_KEYS,
    _parse_script_meta_comment_line,
    extract_error_line_number,
    parse_script_metadata_comments,
    strip_markdown_code_fence,
)

from modules.toolkit_ui.tk_utils import (
    SafeCTkFont,
    _keep_alive_fonts,
    start_safe_thread,
)
from modules.toolkit_ui.script_list_widget import CTkScriptList
from modules.toolkit_ui.dialogs_report import ExportReportDialog
from modules.toolkit_ui.dialog_script_editor import ScriptEditorDialog
from modules.toolkit_ui.dialog_ai_prompt import AIPromptDialog

from modules.toolkit_ui.app_shell_mixin import ToolkitShellMixin
from modules.toolkit_ui.tab_audio_batch_mixin import AudioBatchTabMixin
from modules.toolkit_ui.tab_textgrid_wordlist_mixin import TextGridWordlistTabMixin
from modules.toolkit_ui.tab_project_tools_mixin import ProjectToolsTabMixin
from modules.toolkit_ui.tab_script_workbench_mixin import ScriptWorkbenchMixin
from modules.toolkit_ui.tab_script_viewers_mixin import ScriptResultViewersMixin


def parse_wordlist(raw_text: str):
    """解析字表，兼容主程序的【组别】格式"""
    return parse_flat_wordlist(raw_text)


_read_text_file_with_fallback = read_text_file_with_fallback


class ToolkitApp(
    ToolkitShellMixin,
    AudioBatchTabMixin,
    TextGridWordlistTabMixin,
    ProjectToolsTabMixin,
    ScriptWorkbenchMixin,
    ScriptResultViewersMixin,
    ctk.CTk,
):
    """
    PhonTracer 辅助工具箱主程序（由 6 个 Mixin 组合而成）。
    """
    pass


__all__ = [
    "ToolkitApp",
    "CTkScriptList",
    "ExportReportDialog",
    "ScriptEditorDialog",
    "AIPromptDialog",
    "start_safe_thread",
    "SafeCTkFont",
    "_keep_alive_fonts",
    "parse_wordlist",
    "_read_text_file_with_fallback",
    "CTkReleaseButton",
    "VisualSplitter",
    "macroscopic_vad",
    "SILENCE_AMPLITUDE_THRESHOLD",
    "SCRIPT_EDITOR_DEFAULT_NAME",
    "SCRIPT_EDITOR_DEFAULT_DESC",
    "SCRIPT_META_NAME_KEYS",
    "SCRIPT_META_DESC_KEYS",
    "_parse_script_meta_comment_line",
    "strip_markdown_code_fence",
    "parse_script_metadata_comments",
    "extract_error_line_number",
    "copy",
    "traceback",
    "tk",
    "ctk",
    "messagebox",
    "filedialog",
    "simpledialog",
]

if __name__ == "__main__":
    app = ToolkitApp()
    app.mainloop()
