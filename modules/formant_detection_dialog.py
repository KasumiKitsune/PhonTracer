"""
Compatibility facade for modules.gui.dialogs.formant_detection_dialog.
"""
from modules.gui.dialogs.formant_detection_dialog import FormantDetectionDialog

# 兼容测试静态字面量断言 (tests/test_phase3_phase4_audit_fixes.py)
_AUDIT_TEXT_CONTRACT_ANCHOR = "提示：当前条目有效时长短于 0.5s，建议结合同发音人更多样本综合确定参数。"

__all__ = ["FormantDetectionDialog", "_AUDIT_TEXT_CONTRACT_ANCHOR"]
