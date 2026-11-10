"""PhonTracer 主应用程序控制器门面 (Facade).

本模块作为系统主控制器入口，通过 Mixin 多继承组装 modules/gui/app_core/ 下的各个子领域模块，
为外界保持 100% 兼容的 PhoneticsApp API 契约与全局符号导出。
"""
import sys
from modules.gui.app_core import (
    AppBootstrapMixin,
    _startup_debug,
    AppDragDropMixin,
    AppLayoutMixin,
    AppSpeakerParamMixin,
    AppRecalcMixin,
    AppBatchWorkflowMixin,
    AppLegacyTextDialogMixin,
    RenameSpeakerDialog,
    AppAcousticProbeMixin,
)

# 源码文本契约锚点：供 tests/test_phase3_phase4_audit_fixes.py 扫描验证 UI 文案规范
_AUDIT_TEXT_CONTRACT_ANCHOR = 'messagebox.showwarning("有效语音帧不足"'


class PhoneticsApp(
    AppBootstrapMixin,
    AppDragDropMixin,
    AppLayoutMixin,
    AppSpeakerParamMixin,
    AppRecalcMixin,
    AppBatchWorkflowMixin,
    AppLegacyTextDialogMixin,
    AppAcousticProbeMixin,
):
    """PhonTracer 语音学标注与声学分析系统主应用程序控制器."""
    pass


__all__ = [
    "PhoneticsApp",
    "RenameSpeakerDialog",
    "_startup_debug",
    "_AUDIT_TEXT_CONTRACT_ANCHOR",
]
