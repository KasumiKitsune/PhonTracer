# -*- coding: utf-8 -*-
"""
modules.chart_engine
~~~~~~~~~~~~~~~~~~~~
纯无头声学图表渲染与导出引擎包。
完全解耦于 GUI 控件（GUI 界面组件），使用 Agg 后端进行离线渲染与批量导出。
"""
from __future__ import annotations

import warnings
warnings.filterwarnings("ignore", category=UserWarning, message=".*Tight layout not applied.*")
warnings.filterwarnings("ignore", category=RuntimeWarning, message=".*Mean of empty slice.*")
warnings.filterwarnings("ignore", category=RuntimeWarning, message=".*Degrees of freedom <= 0 for slice.*")
warnings.filterwarnings("ignore", category=RuntimeWarning, message=".*All-NaN slice encountered.*")

from .presets import PUBLICATION_PRESETS, ExportCancelled, ChartPresetsMixin
from .domain_port import ProjectDataPort, HeadlessProjectDataSource, GuiTreeDataSourceAdapter
from .grouping_and_data import ChartDataGroupingMixin
from .tone_contour_plotters import ToneContourPlottersMixin
from .tone_stats_and_qc_plotters import ToneStatsAndQCPlottersMixin
from .formant_plotters import FormantPlottersMixin
from .exporter_core import AcousticChartExporter, _MATPLOTLIB_LOCK

__all__ = [
    "PUBLICATION_PRESETS",
    "ExportCancelled",
    "ChartPresetsMixin",
    "ProjectDataPort",
    "HeadlessProjectDataSource",
    "GuiTreeDataSourceAdapter",
    "ChartDataGroupingMixin",
    "ToneContourPlottersMixin",
    "ToneStatsAndQCPlottersMixin",
    "FormantPlottersMixin",
    "AcousticChartExporter",
    "_MATPLOTLIB_LOCK",
]
