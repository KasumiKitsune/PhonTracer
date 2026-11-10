"""
modules.toolkit_ui: PhonTracer 辅助工具箱模块包。
包含 11 个拆分后的 UI 子模块与 Mixin。
"""
from modules.toolkit_ui.tk_utils import (
    start_safe_thread,
    SafeCTkFont,
    _keep_alive_fonts,
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

__all__ = [
    "start_safe_thread",
    "SafeCTkFont",
    "_keep_alive_fonts",
    "CTkScriptList",
    "ExportReportDialog",
    "ScriptEditorDialog",
    "AIPromptDialog",
    "ToolkitShellMixin",
    "AudioBatchTabMixin",
    "TextGridWordlistTabMixin",
    "ProjectToolsTabMixin",
    "ScriptWorkbenchMixin",
    "ScriptResultViewersMixin",
]
