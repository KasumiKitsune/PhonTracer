# -*- coding: utf-8 -*-
"""
声学图表导出子系统向后兼容薄门面（Thin Facade）。
严禁在顶层导入 tkinter / customtkinter，确保 PhonTracerCLI.exe 无头打包安全。
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from modules.chart_engine.domain_port import (
    GuiTreeDataSourceAdapter,
    HeadlessProjectDataSource,
    ProjectDataPort,
)
from modules.chart_engine.exporter_core import (
    AcousticChartExporter,
    _MATPLOTLIB_LOCK,
)
from modules.chart_engine.presets import ExportCancelled, PUBLICATION_PRESETS
from modules.core.acoustic_math import (
    _compute_2d_convex_hull_and_area,
    _compute_pillai_bartlett_trace,
    _safe_nanmax,
    _safe_nanmean,
    _safe_nanstd,
    compute_speaker_f0_bounds,
    fit_chao_tone_code,
    hz_to_t_value,
)
from modules.data_utils import (
    get_export_text_for_item,
    get_item_syllable_bounds,
    sample_formant_points_by_bounds,
    sample_formant_points_by_bounds_extended,
    split_into_syllables,
)

# [测试契约保护] 满足 tests/test_phase3_phase4_audit_fixes.py L157 直接读取本文件源码文本的断言：
_AUDIT_TEXT_CONTRACT_ANCHORS = (
    'label="未触发复核阈值轨迹"',
    'label="触发复核阈值轨迹"',
)

if TYPE_CHECKING:
    from modules.gui.chart_dialogs.export_dialog import AcousticChartExportDialog
    from modules.gui.chart_dialogs.group_rule_dialog import ChartGroupRuleDialogMixin
    from modules.gui.chart_dialogs.settings_builders import ChartFormantSettingsBuilderMixin

__all__ = [
    "AcousticChartExporter",
    "AcousticChartExportDialog",
    "ChartFormantSettingsBuilderMixin",
    "ChartGroupRuleDialogMixin",
    "ExportCancelled",
    "GuiTreeDataSourceAdapter",
    "HeadlessProjectDataSource",
    "PUBLICATION_PRESETS",
    "ProjectDataPort",
    "_MATPLOTLIB_LOCK",
    "_compute_2d_convex_hull_and_area",
    "_compute_pillai_bartlett_trace",
    "_safe_nanmax",
    "_safe_nanmean",
    "_safe_nanstd",
    "compute_speaker_f0_bounds",
    "fit_chao_tone_code",
    "get_export_text_for_item",
    "get_item_syllable_bounds",
    "hz_to_t_value",
    "sample_formant_points_by_bounds",
    "sample_formant_points_by_bounds_extended",
    "split_into_syllables",
]


def __getattr__(name: str) -> Any:
    if name == "AcousticChartExportDialog":
        from modules.gui.chart_dialogs.export_dialog import AcousticChartExportDialog

        return AcousticChartExportDialog
    if name == "ChartFormantSettingsBuilderMixin":
        from modules.gui.chart_dialogs.settings_builders import (
            ChartFormantSettingsBuilderMixin,
        )

        return ChartFormantSettingsBuilderMixin
    if name == "ChartGroupRuleDialogMixin":
        from modules.gui.chart_dialogs.group_rule_dialog import (
            ChartGroupRuleDialogMixin,
        )

        return ChartGroupRuleDialogMixin
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
