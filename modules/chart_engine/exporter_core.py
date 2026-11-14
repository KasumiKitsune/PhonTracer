# -*- coding: utf-8 -*-
"""
modules.chart_engine.exporter_core
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
无头声学图表导出器核心主类。
通过多继承组合样式预设、数据分组、调值图、质检图与共振峰图等 Mixin。
完全无头，仅依赖 matplotlib Agg 后端，不依赖任何 GUI 库。
"""
from __future__ import annotations

import csv
import math
import os
import shutil as _shutil
import threading
import warnings
from typing import Any, Dict, List, Optional, Tuple
import numpy as np

import matplotlib
matplotlib.use("Agg", force=True)
import matplotlib.pyplot as plt
import matplotlib.text as mtext
from matplotlib.backends.backend_pdf import PdfPages
try:
    import pandas as pd
except ImportError:
    pd = None

from modules.data_utils import compute_speaker_f0_bounds
from .presets import PUBLICATION_PRESETS, ExportCancelled, ChartPresetsMixin
from .domain_port import ProjectDataPort, HeadlessProjectDataSource, GuiTreeDataSourceAdapter
from .grouping_and_data import ChartDataGroupingMixin
from .tone_contour_plotters import ToneContourPlottersMixin
from .tone_stats_and_qc_plotters import ToneStatsAndQCPlottersMixin
from .formant_plotters import FormantPlottersMixin

_MATPLOTLIB_LOCK = threading.RLock()


def _sanitize_fonttools_harfbuzz() -> None:
    """
    Prevent fontTools PDF Type-42 subsetting from crashing with
    `AttributeError: module 'uharfbuzz' has no attribute 'RepackerError'`
    when uharfbuzz is partially bundled or a leftover directory in frozen builds.
    """
    try:
        import fontTools.ttLib.tables.otBase as _ot_base
        _hb = getattr(_ot_base, "hb", None)
        if _hb is not None and not hasattr(_hb, "RepackerError"):
            setattr(_hb, "RepackerError", getattr(_hb, "SerializerError", getattr(_hb, "HarfBuzzError", Exception)))
        if _hb is None or not callable(getattr(_hb, "repack", None)) or not hasattr(_hb, "RepackerError"):
            _ot_base.have_uharfbuzz = False
    except Exception:
        pass


_sanitize_fonttools_harfbuzz()



class AcousticChartExporter(
    ChartPresetsMixin,
    ChartDataGroupingMixin,
    ToneContourPlottersMixin,
    ToneStatsAndQCPlottersMixin,
    FormantPlottersMixin,
):
    """无头声学图表导出器。完全解耦于 GUI，提供纯批处理/CLI/服务化制图能力。"""
    PUBLICATION_PRESETS = PUBLICATION_PRESETS

    def __init__(self, project_tree, app=None, all_speakers=None):
        self.project_tree = project_tree
        self.app = app
        self.all_speakers = all_speakers or []
        self.params = {}  # CLI parameter overrides
        self.work_mode = "publication"
        self.preset_theme = "chinese_journal"
        self.colors = ['#2563EB', '#DC2626', '#16A34A', '#9333EA', '#EA580C', '#0891B2', '#CA8A04', '#4F46E5', '#BE185D', '#0D9488', '#7C2D12', '#334155']
        self.linestyles = list(PUBLICATION_PRESETS["chinese_journal"]["linestyles"])
        self.markers = list(PUBLICATION_PRESETS["chinese_journal"]["markers"])
        self._speaker_data_cache = {}
        self._force_live_extract = False
        self.chart_group_rule = self._default_chart_group_rule()

        self.sm = getattr(self.app, 'speaker_manager', None)
        self.active_speaker = self.sm.get_active_speaker() if self.sm else None
        if not self.active_speaker and self.all_speakers:
            self.active_speaker = self.all_speakers[0]

        self.current_preview_page = 0
        self.current_group_page = 0
        self._render_runtime = threading.local()


    def _is_formant_mode(self):
        return self.project_tree.app_state_params.get('analysis_mode', 'f0') == 'formant'


    def _normalize_work_mode(self, mode=None):
        raw = mode if mode is not None else self.get_param('work_mode', getattr(self, 'work_mode', 'publication'))
        text = str(raw or "publication").strip().lower()
        if any(k in text for k in ("qc", "quality", "triage", "质检", "排查", "诊断", "mode_b", "模式b", "模式 b")):
            return "qc"
        return "publication"


    def _set_export_runtime(self, progress_callback=None, cancel_event=None, params=None):
        self._render_runtime.progress_callback = progress_callback
        self._render_runtime.cancel_event = cancel_event
        self._render_runtime.params = params


    def _clear_export_runtime(self):
        self._render_runtime.progress_callback = None
        self._render_runtime.cancel_event = None
        self._render_runtime.params = None


    def _ensure_available_groups(self):
        if not hasattr(self, 'available_groups') or not self.available_groups:
            all_entries = self._extract_active_data(self.all_speakers if self.all_speakers else [self.active_speaker], filter_groups=False)
            group_counts = {}
            for entry in all_entries:
                g = entry['group']
                group_counts[g] = group_counts.get(g, 0) + 1
            self.available_groups = sorted(list(group_counts.keys()))


    def _get_f0_normalization_bounds(self, f0_values):
        return compute_speaker_f0_bounds(f0_values, method="p5_p95_iqr")


    def _compute_error_band_halfwidth(self, matrix, mode_text="95% CI"):
        a = np.asarray(matrix, dtype=float)
        if a.ndim != 2 or a.shape[0] == 0:
            return np.zeros(a.shape[1] if a.ndim == 2 else 0, dtype=float)
        out = np.full(a.shape[1], np.nan, dtype=float)
        mode_str = str(mode_text or "").lower()
        t95_table = {
            1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571,
            6: 2.447, 7: 2.365, 8: 2.306, 9: 2.262, 10: 2.228,
            15: 2.131, 20: 2.086, 30: 2.042,
        }
        for col_idx in range(a.shape[1]):
            col = a[:, col_idx]
            valid = col[np.isfinite(col)]
            n = valid.size
            if n == 0:
                continue
            if n < 2:
                out[col_idx] = 0.0
                continue
            sd = float(np.std(valid, ddof=1)) if n > 1 else float(np.std(valid))
            se = sd / math.sqrt(n)
            if "se" in mode_str or "标准误" in mode_str:
                out[col_idx] = se
            elif "sd" in mode_str or "标准差" in mode_str:
                out[col_idx] = sd
            else:
                df = n - 1
                if df in t95_table:
                    t_val = t95_table[df]
                elif df > 30:
                    t_val = 1.96 + 2.4 / float(df)
                else:
                    t_val = next((t95_table[k] for k in sorted(t95_table) if df <= k), 1.96)
                out[col_idx] = t_val * se
        return out


    def _check_export_cancelled(self):
        cancel_event = getattr(self._render_runtime, "cancel_event", None)
        if cancel_event is not None and cancel_event.is_set():
            raise ExportCancelled("导出已取消")


    def _report_export_progress(self, progress=None, message=None):
        cb = getattr(self._render_runtime, "progress_callback", None)
        if cb is None:
            return
        if progress is not None:
            progress = max(0.0, min(1.0, float(progress)))
        cb(progress, message)


    def _get_save_dpi(self):
        dpi_val = self.get_param('dpi', None)
        if dpi_val is not None:
            try:
                dpi_str = str(dpi_val).lower().replace("dpi", "").strip()
                if dpi_str:
                    return float(dpi_str)
            except Exception:
                pass

        fmt = str(self.get_param('format', 'png')).lower()
        if 'svg' in fmt or 'pdf' in fmt:
            return 300

        pixel_mode = self.get_param('image_pixel_mode', '默认')
        custom_pixel = self.get_param('image_pixel_custom', 1080)

        if pixel_mode == '默认':
            preset = self.get_publication_preset()
            return float(preset.get("default_dpi", 300))

        pixel_map = {
            "480 px": 480,
            "600 px": 600,
            "720 px": 720,
            "1080 px": 1080,
            "1440 px": 1440,
            "2160 px": 2160,
            "600 DPI": 600,
            "300 DPI": 300,
            "150 DPI": 150,
        }
        return float(pixel_map.get(pixel_mode, custom_pixel))


    def _resolve_save_dpi(self, fig):
        dpi_explicit = self.get_param('dpi', None)
        if dpi_explicit is not None:
            try:
                return float(str(dpi_explicit).lower().replace("dpi", "").strip())
            except Exception:
                pass

        pixel_mode = str(self.get_param('image_pixel_mode', '默认'))
        if "dpi" in pixel_mode.lower() or pixel_mode == '默认':
            return self._get_save_dpi()

        target_min_pixels = self._get_save_dpi()
        if target_min_pixels in (300, 600, 150):
            return target_min_pixels

        try:
            width_inches, height_inches = fig.get_size_inches()
            min_edge_inches = min(float(width_inches), float(height_inches))
        except Exception:
            min_edge_inches = 0.0

        if min_edge_inches <= 0:
            return 300
        return target_min_pixels / min_edge_inches


    def _save_figure(self, fig, out_path):
        _sanitize_fonttools_harfbuzz()
        self._sync_chao_axes_limits(fig)
        fig.savefig(out_path, dpi=self._resolve_save_dpi(fig), bbox_inches='tight')


    def _sync_chao_axes_limits(self, fig):
        """Force every secondary Chao 5-level axis to share the exact ylim of its parent primary axis and suppress parent right ticks."""
        if fig is None:
            return
        for ca in getattr(fig, "axes", []):
            if getattr(ca, "_is_chao_axis", False):
                parent_ax = getattr(ca, "_parent_ax", None)
                if parent_ax is not None:
                    try:
                        parent_ax.yaxis.set_ticks_position("left")
                        parent_ax.tick_params(axis="y", which="both", right=False, labelright=False)
                        ca.yaxis.set_ticks_position("right")
                        ca.autoscale(enable=False, axis="y")
                        ca.set_autoscale_on(False)
                        ca.set_ylim(parent_ax.get_ylim())
                    except Exception:
                        pass


    def get_param(self, name, default=None):
        runtime_params = getattr(getattr(self, "_render_runtime", None), "params", None)
        if runtime_params is not None and name in runtime_params:
            return runtime_params[name]

        if hasattr(self, 'params') and self.params is not None and name in self.params:
            return self.params[name]

        gui_mappings = {
            'work_mode': lambda: getattr(self, 'var_work_mode').get() if hasattr(self, 'var_work_mode') else getattr(self, 'work_mode', None),
            'preset_theme': lambda: getattr(self, 'var_preset_theme').get() if hasattr(self, 'var_preset_theme') else getattr(self, 'preset_theme', None),
            'chart_type': lambda: getattr(self, 'var_chart_type').get() if hasattr(self, 'var_chart_type') else None,
            'export_scope': lambda: getattr(self, 'var_export_scope').get() if hasattr(self, 'var_export_scope') else None,
            'color_by': lambda: getattr(self, 'combo_color_by').get() if hasattr(self, 'combo_color_by') else (getattr(self, 'combo_groupby').get() if hasattr(self, 'combo_groupby') else None),
            'groupby': lambda: getattr(self, 'combo_groupby').get() if hasattr(self, 'combo_groupby') else (getattr(self, 'combo_color_by').get() if hasattr(self, 'combo_color_by') else None),
            'facet_by': lambda: getattr(self, 'combo_facet_by').get() if hasattr(self, 'combo_facet_by') else None,
            'chart_group_rule': lambda: dict(getattr(self, 'chart_group_rule', self._default_chart_group_rule())),
            'scale': lambda: getattr(self, 'combo_scale').get() if hasattr(self, 'combo_scale') else None,
            'format': lambda: getattr(self, 'combo_format').get() if hasattr(self, 'combo_format') else None,
            'layout_size': lambda: getattr(self, 'combo_layout_size').get() if hasattr(self, 'combo_layout_size') else None,
            'dpi': lambda: getattr(self, 'combo_dpi').get() if hasattr(self, 'combo_dpi') else None,
            'show_title': lambda: getattr(self, 'var_show_title').get() if hasattr(self, 'var_show_title') else None,
            'intention': lambda: getattr(self, 'combo_intention').get() if hasattr(self, 'combo_intention') else None,

            # contour specific
            'contour_x': lambda: getattr(self, 'combo_contour_x').get() if hasattr(self, 'combo_contour_x') else None,
            'contour_content': lambda: getattr(self, 'combo_contour_content').get() if hasattr(self, 'combo_contour_content') else None,
            'contour_facet': lambda: getattr(self, 'combo_contour_facet').get() if hasattr(self, 'combo_contour_facet') else None,
            'show_chao_five_level_axis': lambda: getattr(self, 'var_show_chao_five_level').get() if hasattr(self, 'var_show_chao_five_level') else None,
            'show_spaghetti': lambda: getattr(self, 'var_show_spaghetti').get() if hasattr(self, 'var_show_spaghetti') else None,
            'show_kde_bg': lambda: getattr(self, 'var_show_kde_bg').get() if hasattr(self, 'var_show_kde_bg') else None,

            # sandhi specific
            'sandhi_subview': lambda: getattr(self, 'combo_sandhi_subview').get() if hasattr(self, 'combo_sandhi_subview') else None,
            'sandhi_facet_by': lambda: getattr(self, 'combo_sandhi_facet').get() if hasattr(self, 'combo_sandhi_facet') else None,

            # distribution specific
            'dist_type': lambda: getattr(self, 'combo_dist_type').get() if hasattr(self, 'combo_dist_type') else None,
            'dist_metric': lambda: getattr(self, 'combo_dist_metric').get() if hasattr(self, 'combo_dist_metric') else None,
            'dist_style': lambda: getattr(self, 'combo_dist_style').get() if hasattr(self, 'combo_dist_style') else None,

            # density specific
            'density_bw': lambda: getattr(self, 'var_density_bw').get() if hasattr(self, 'var_density_bw') else None,
            'density_f0_mode': lambda: getattr(self, 'var_density_f0_mode').get() if hasattr(self, 'var_density_f0_mode') else None,
            'density_facet': lambda: getattr(self, 'combo_density_facet').get() if hasattr(self, 'combo_density_facet') else None,
            'density_normalization': lambda: getattr(self, 'var_density_normalization').get() if hasattr(self, 'var_density_normalization') else None,
            'density_p_low': lambda: getattr(self, 'entry_low_p').get() if hasattr(self, 'entry_low_p') else None,
            'density_p_high': lambda: getattr(self, 'entry_high_p').get() if hasattr(self, 'entry_high_p') else None,
            'density_m_min': lambda: getattr(self, 'entry_min_hz').get() if hasattr(self, 'entry_min_hz') else None,
            'density_m_max': lambda: getattr(self, 'entry_max_hz').get() if hasattr(self, 'entry_max_hz') else None,

            # quality specific
            'qc_view': lambda: getattr(self, 'var_qc_view').get() if hasattr(self, 'var_qc_view') else None,

            # overview / QC heatmap specific
            'overview_metric': lambda: getattr(self, 'combo_overview_metric').get() if hasattr(self, 'combo_overview_metric') else None,
            'overview_show_deviation': lambda: getattr(self, 'var_overview_show_deviation').get() if hasattr(self, 'var_overview_show_deviation') else None,
            'sort_by_anomaly': lambda: getattr(self, 'var_sort_by_anomaly').get() if hasattr(self, 'var_sort_by_anomaly') else None,
            'formant_overview_mode': lambda: getattr(self, 'combo_formant_overview_mode').get() if hasattr(self, 'combo_formant_overview_mode') else None,

            # formant space specific
            'formant_ellipse': lambda: getattr(self, 'combo_formant_ellipse').get() if hasattr(self, 'combo_formant_ellipse') else None,
            'formant_label_mode': lambda: getattr(self, 'combo_formant_label_mode').get() if hasattr(self, 'combo_formant_label_mode') else None,
            'formant_show_raw': lambda: getattr(self, 'var_formant_show_raw').get() if hasattr(self, 'var_formant_show_raw') else None,
            'formant_time_gradient': lambda: getattr(self, 'var_formant_time_gradient').get() if hasattr(self, 'var_formant_time_gradient') else None,
            'show_convex_hull': lambda: getattr(self, 'var_show_convex_hull').get() if hasattr(self, 'var_show_convex_hull') else None,
            'show_diphthong_arrows': lambda: getattr(self, 'var_show_diphthong_arrows').get() if hasattr(self, 'var_show_diphthong_arrows') else None,
            'formant_normalization': lambda: getattr(self, 'combo_formant_normalization').get() if hasattr(self, 'combo_formant_normalization') else None,
            'formant_axis_lock': lambda: getattr(self, 'var_formant_axis_lock').get() if hasattr(self, 'var_formant_axis_lock') else None,

            # formant density specific
            'formant_density_overlay': lambda: getattr(self, 'var_formant_density_overlay').get() if hasattr(self, 'var_formant_density_overlay') else None,
            'formant_density_bw': lambda: getattr(self, 'var_formant_density_bw').get() if hasattr(self, 'var_formant_density_bw') else None,
            'formant_density_facet': lambda: getattr(self, 'combo_formant_density_facet').get() if hasattr(self, 'combo_formant_density_facet') else None,
            'formant_density_show_raw': lambda: getattr(self, 'var_formant_density_show_raw').get() if hasattr(self, 'var_formant_density_show_raw') else None,
            'formant_density_show_contours': lambda: getattr(self, 'var_formant_density_show_contours').get() if hasattr(self, 'var_formant_density_show_contours') else None,

            # formant trajectory specific
            'formant_traj_style': lambda: getattr(self, 'combo_formant_traj_style').get() if hasattr(self, 'combo_formant_traj_style') else None,
            'show_f3': lambda: getattr(self, 'var_show_f3').get() if hasattr(self, 'var_show_f3') else None,
            'formant_syllable_filter': lambda: getattr(self, 'combo_formant_syllable_filter').get() if hasattr(self, 'combo_formant_syllable_filter') else None,

            # formant pillai specific
            'pillai_subview': lambda: getattr(self, 'combo_pillai_subview').get() if hasattr(self, 'combo_pillai_subview') else None,

            # legend specific
            'legend_loc': lambda: getattr(self, 'combo_legend_loc').get() if hasattr(self, 'combo_legend_loc') else None,
            'legend_outside': lambda: getattr(self, 'var_legend_outside').get() if hasattr(self, 'var_legend_outside') else None,
            # Image Size & Pixels configuration
            'image_ratio_mode': lambda: getattr(self, 'combo_ratio_mode').get() if hasattr(self, 'combo_ratio_mode') else None,
            'image_ratio_custom': lambda: getattr(self, 'var_image_ratio_custom').get() if hasattr(self, 'var_image_ratio_custom') else None,
            'image_pixel_mode': lambda: getattr(self, 'combo_pixel_mode').get() if hasattr(self, 'combo_pixel_mode') else None,
            'image_pixel_custom': lambda: int(self.entry_pixel_custom.get().strip()) if (hasattr(self, 'entry_pixel_custom') and self.entry_pixel_custom.get().strip().isdigit()) else 1080,
            'high_precision': lambda: getattr(self, 'var_high_precision').get() if hasattr(self, 'var_high_precision') else None,
        }

        if name in gui_mappings:
            try:
                val = gui_mappings[name]()
                if val is not None:
                    return val
            except Exception:
                pass
        return default


    def _get_legend_kwargs(self, ax=None, default_outside=None):
        loc_val = str(self.get_param('legend_loc', '右上') or '右上')
        has_chao = bool(ax is not None and getattr(ax, "_has_chao_axis", False))
        if default_outside is None:
            default_outside = True if has_chao else False
        outside_raw = self.get_param('legend_outside', None)
        if outside_raw is None:
            outside_val = bool(default_outside)
        elif isinstance(outside_raw, str):
            outside_val = outside_raw.strip().lower() in ("true", "1", "yes", "on", "是")
        else:
            outside_val = bool(outside_raw)

        preset = self.get_publication_preset()

        loc_map = {
            "右上": "upper right",
            "右下": "lower right",
            "左上": "upper left",
            "左下": "lower left",
        }
        loc_str = loc_map.get(loc_val, "upper right")

        kwargs = {
            "markerscale": 0.65,
            "labelspacing": 0.50,
            "frameon": bool(preset.get("legend_frameon", True)),
        }

        if outside_val:
            right_anchor_x = 1.22 if has_chao else 1.02
            if loc_val == "右上":
                kwargs.update({"loc": "upper left", "bbox_to_anchor": (right_anchor_x, 1.0)})
            elif loc_val == "右下":
                kwargs.update({"loc": "lower left", "bbox_to_anchor": (right_anchor_x, 0.0)})
            elif loc_val == "左上":
                kwargs.update({"loc": "upper right", "bbox_to_anchor": (-0.02, 1.0)})
            elif loc_val == "左下":
                kwargs.update({"loc": "lower right", "bbox_to_anchor": (-0.02, 0.0)})
            else:
                kwargs.update({"loc": "upper left", "bbox_to_anchor": (right_anchor_x, 1.0)})
        else:
            kwargs["loc"] = loc_str

        return kwargs


    def generate_plot(self, data_entries, is_preview=True):
        preset = self.apply_publication_preset(self.get_param('preset_theme', getattr(self, 'preset_theme', 'chinese_journal')))

        raw_chart_type = self.get_param('chart_type', 'contour')
        chart_type = self._normalize_chart_type(raw_chart_type)
        color_by_val = self._resolve_color_by()
        scale_val = self.get_param('scale', 't_value')
        group_key = self._get_group_key(color_by_val)

        if not data_entries:
            fig, ax = plt.subplots(figsize=(6, 4))
            fig.patch.set_facecolor("#FFFFFF")
            ax.set_facecolor("#FFFFFF")
            ax.text(0.5, 0.5, "没有找到有效的声学分析数据！\n请检查是否配置了发音人或导入了音频点。", ha='center', va='center', fontsize=12, color='red')
            ax.axis('off')
            return fig

        data_entries = self._prepare_entries_for_color_by(data_entries, group_key)

        pagination_state = self._get_group_pagination_state(data_entries, chart_type, color_by_val)
        total_pages = pagination_state['total_pages']
        total_groups = pagination_state['total_groups']
        unique_groups = pagination_state['unique_groups']
        is_paginated_heatmap = pagination_state['is_paginated_heatmap']
        facet_pages = pagination_state.get('facet_pages', [])

        if self.current_group_page < 0:
            self.current_group_page = 0
        elif self.current_group_page >= total_pages:
            self.current_group_page = max(0, total_pages - 1)

        P = self.current_group_page

        if is_preview:
            if is_paginated_heatmap:
                current_page_rows = pagination_state['pages'][P] if P < len(pagination_state['pages']) else []
                allowed_pairs = {row['row_id'] for row in current_page_rows}
                data_entries = [e for e in data_entries if (self._entry_wordlist_group(e), e['label']) in allowed_pairs]
            elif self._is_overview_heatmap_chart(chart_type):
                if total_pages > 1 and total_groups > 24:
                    start_idx = P * 24
                    end_idx = min(total_groups, start_idx + 24)
                    allowed_groups = set(unique_groups[start_idx:end_idx])
                    data_entries = [e for e in data_entries if e.get(group_key, e.get('group')) in allowed_groups]
            elif len(facet_pages) > 1 and P < len(facet_pages):
                data_entries = self._filter_entries_by_page_spec(data_entries, group_key, facet_pages[P])

        if scale_val and ("T" in str(scale_val) or "t_value" in str(scale_val).lower()):
            scale = "T 值"
        else:
            scale = "Hz"

        if chart_type == "contour":
            fig = self._plot_tone_contour(data_entries, group_key, scale)
        elif chart_type == "tone_sandhi":
            fig = self._plot_tone_sandhi_grid(data_entries, group_key, scale)
        elif chart_type == "distribution":
            fig = self._plot_tone_distribution(data_entries, group_key, scale)
        elif chart_type == "duration":
            fig = self._plot_syllable_duration_comparison(data_entries, group_key, scale)
        elif chart_type == "density":
            fig = self._plot_temporal_density(data_entries, group_key, scale=scale, is_preview=is_preview)
        elif chart_type == "spaghetti_qc":
            fig = self._plot_spaghetti_qc_overlay(data_entries, group_key, scale)
        elif chart_type == "quality":
            fig = self._plot_quality_check(data_entries)
        elif chart_type == "overview_heatmap":
            fig = self._plot_tone_overview_heatmap(data_entries, group_key, scale)
        elif chart_type == "tone_effect_time":
            if self.get_param("sandhi_subview", None) is not None:
                fig = self._plot_tone_sandhi_grid(data_entries, group_key, scale)
            else:
                fig = self._plot_tone_effect_time(data_entries)
        elif chart_type == "formant_overview_heatmap":
            fig = self._plot_formant_overview_heatmap(data_entries, group_key, scale)
        elif chart_type == "formant_space":
            fig = self._plot_formant_vowel_space(data_entries, group_key, scale, is_preview=is_preview)
        elif chart_type == "formant_trajectory":
            fig = self._plot_formant_trajectories(data_entries, group_key, scale)
        elif chart_type == "formant_pillai":
            fig = self._plot_formant_pillai_matrix(data_entries, group_key, scale)
        elif chart_type == "formant_density":
            fig = self._plot_formant_density_heatmap(data_entries, group_key, scale)
        else:
            fig, ax = plt.subplots()
            return fig

        self._apply_preset_to_figure(fig, chart_type=chart_type, preset_override=preset)

        # Apply layout_size / aspect ratio resize
        layout_size = str(self.get_param('layout_size', '') or '')
        ratio_mode = self.get_param('image_ratio_mode', '默认')
        custom_ratio = self.get_param('image_ratio_custom', 1.5)

        if preset["name"] == "presentation" and ratio_mode == "默认" and not layout_size:
            ratio_mode = "16:9"

        ratio_map = {
            "4:3": 4.0 / 3.0,
            "16:9": 16.0 / 9.0,
            "3:2": 3.0 / 2.0,
            "1:1": 1.0,
            "16:10": 16.0 / 10.0,
            "2:1": 2.0
        }
        custom_ratio_val = self._parse_custom_ratio_value(custom_ratio, default=1.5)
        R = float(ratio_map.get(ratio_mode, custom_ratio_val if ratio_mode != "默认" else (4.0 / 3.0)))

        layout_mm_map = {
            "单栏 80mm": 80.0,
            "80mm": 80.0,
            "单栏 85mm": 85.0,
            "85mm": 85.0,
            "1.5栏 114mm": 114.0,
            "114mm": 114.0,
            "双栏通栏 150mm": 150.0,
            "150mm": 150.0,
            "双栏 170mm": 170.0,
            "170mm": 170.0,
        }

        if is_preview and hasattr(self, 'preview_wrapper'):
            try:
                w_avail = self.preview_wrapper.winfo_width()
                h_avail = self.preview_wrapper.winfo_height()
            except Exception:
                w_avail, h_avail = 0, 0
        else:
            w_avail, h_avail = 0, 0

        if not is_preview and layout_size in layout_mm_map:
            w_inches = layout_mm_map[layout_size] / 25.4
            h_inches = w_inches / R
            fig.set_size_inches(w_inches, h_inches, forward=True)
            if not getattr(fig, "_phontracer_skip_tight_layout", False):
                try:
                    fig.tight_layout()
                except Exception:
                    pass
        elif not is_preview and ("16:9" in layout_size or preset["name"] == "presentation") and ratio_mode in ("默认", "16:9"):
            fig.set_size_inches(10.67, 6.0, forward=True)
            if not getattr(fig, "_phontracer_skip_tight_layout", False):
                try:
                    fig.tight_layout()
                except Exception:
                    pass
        elif ratio_mode != "默认":
            if is_preview and w_avail > 50 and h_avail > 50:
                w_avail_pad = max(50, w_avail - 16)
                h_avail_pad = max(50, h_avail - 16)
                if w_avail_pad / h_avail_pad >= R:
                    h_inches = h_avail_pad / fig.dpi
                    w_inches = h_inches * R
                else:
                    w_inches = w_avail_pad / fig.dpi
                    h_inches = w_inches / R
            else:
                S_min = 6.0
                if R >= 1.0:
                    w_inches = R * S_min
                    h_inches = S_min
                else:
                    w_inches = S_min
                    h_inches = S_min / R

            fig.set_size_inches(w_inches, h_inches, forward=True)
            if not getattr(fig, "_phontracer_skip_tight_layout", False):
                try:
                    fig.tight_layout()
                except Exception:
                    pass
        else:
            if is_preview and w_avail > 50 and h_avail > 50:
                w_avail_pad = max(50, w_avail - 16)
                h_avail_pad = max(50, h_avail - 16)
                fig.set_size_inches(w_avail_pad / fig.dpi, h_avail_pad / fig.dpi, forward=True)
                if not getattr(fig, "_phontracer_skip_tight_layout", False):
                    try:
                        fig.tight_layout()
                    except Exception:
                        pass

        if getattr(self, 'is_minimized', False):
            for text in fig.findobj(mtext.Text):
                curr_size = text.get_size()
                if isinstance(curr_size, (int, float)):
                    text.set_size(max(7.0, curr_size * 0.72))
                elif isinstance(curr_size, str):
                    try:
                        text.set_size(max(7.0, float(curr_size) * 0.72))
                    except ValueError:
                        pass
            try:
                fig.tight_layout(pad=1.2)
            except Exception:
                pass

        self._adjust_outside_legend_and_chao_layout(fig)
        self._sync_chao_axes_limits(fig)
        return fig


    def _adjust_outside_legend_and_chao_layout(self, fig):
        """Ensure outside right legends never overlap right-side Chao 5-level axes, colorbars, or clip at figure edges."""
        try:
            legend_axes = [
                a for a in fig.axes
                if a.get_visible()
                and not getattr(a, "_is_chao_axis", False)
                and not getattr(a, "_is_colorbar_axis", False)
                and a.get_legend() is not None
            ]
            if not legend_axes:
                return

            chao_axes = [
                a for a in fig.axes
                if a.get_visible() and getattr(a, "_is_chao_axis", False)
            ]
            loc_val = str(self.get_param("legend_loc", "右上") or "右上")
            if "右" not in loc_val:
                return

            anchor_y = 0.0 if loc_val == "右下" else 1.0
            for _step in range(3):
                fig.canvas.draw()
                renderer = fig.canvas.get_renderer()
                fig_w = float(fig.bbox.width)
                fig_x1 = float(fig.bbox.x1)
                if fig_w <= 50:
                    return

                # Identify axes whose legend is actually positioned outside to the right
                outside_right_axes = []
                for ax in legend_axes:
                    leg = ax.get_legend()
                    if leg is None:
                        continue
                    ax_bbox = ax.get_window_extent(renderer)
                    leg_bbox = leg.get_window_extent(renderer)
                    if float(leg_bbox.x1) > float(ax_bbox.x1) + 4.0:
                        outside_right_axes.append(ax)

                if not outside_right_axes:
                    return

                chao_x1 = max(
                    (
                        float(ca.get_tightbbox(renderer).x1)
                        for ca in chao_axes
                        if ca.get_tightbbox(renderer) is not None
                    ),
                    default=0.0,
                )

                needs_redraw = False
                if chao_x1 > 0:
                    for ax in outside_right_axes:
                        if not getattr(ax, "_has_chao_axis", False):
                            continue
                        leg = ax.get_legend()
                        if leg is None:
                            continue
                        ax_bbox = ax.get_window_extent(renderer)
                        leg_bbox = leg.get_window_extent(renderer)
                        target_leg_x0 = chao_x1 + 10.0
                        if float(leg_bbox.x0) < target_leg_x0 - 1.0 and float(ax_bbox.width) > 10.0:
                            anchor_x = max(1.16, (target_leg_x0 - float(ax_bbox.x0)) / float(ax_bbox.width))
                            leg.set_bbox_to_anchor((anchor_x, anchor_y), transform=ax.transAxes)
                            needs_redraw = True

                if needs_redraw:
                    fig.canvas.draw()
                    renderer = fig.canvas.get_renderer()

                max_leg_x1 = max(
                    (float(ax.get_legend().get_window_extent(renderer).x1) for ax in outside_right_axes if ax.get_legend() is not None),
                    default=0.0,
                )
                overflow_px = max_leg_x1 - (fig_x1 - 8.0)
                if overflow_px <= 1.0:
                    break

                curr_right = float(fig.subplotpars.right)
                curr_left = float(fig.subplotpars.left)
                shrink_frac = (overflow_px + 8.0) / fig_w
                new_right = max(curr_left + 0.30, min(curr_right - shrink_frac, 0.84))
                if abs(new_right - curr_right) < 1e-3:
                    break
                fig.subplots_adjust(right=new_right)
        except Exception:
            pass


    def _export_paginated_pdf(self, out_file, data_entries, group_key, scale, facet_pages=None):
        _sanitize_fonttools_harfbuzz()
        prepared = self._prepare_entries_for_color_by(data_entries, group_key)
        if not facet_pages:
            chart_type = self._normalize_chart_type(self.get_param('chart_type', 'contour'))
            facet_pages = self._build_facet_aware_group_pages(prepared, chart_type, group_key, page_size=8)

        total_pages = max(1, len(facet_pages))
        pdf_pages = PdfPages(out_file)

        try:
            for page_idx, page_spec in enumerate(facet_pages):
                self._check_export_cancelled()
                self._report_export_progress(0.15 + 0.75 * (page_idx / total_pages), f"正在导出分页图 {page_idx + 1}/{total_pages}...")
                chunk_entries = self._filter_entries_by_page_spec(prepared, group_key, page_spec)

                fig = self.generate_plot(chunk_entries, is_preview=False)
                self._sync_chao_axes_limits(fig)

                if total_pages > 1:
                    fig.text(0.95, 0.02, f"第 {page_idx + 1} 页 / 共 {total_pages} 页",
                             ha='right', va='bottom', fontsize=9, color='gray')

                pdf_pages.savefig(fig, bbox_inches='tight')
                plt.close(fig)
        finally:
            pdf_pages.close()
        return [out_file]


    def _export_paginated_images(self, base_path, data_entries, group_key, scale, ext, facet_pages=None):
        prepared = self._prepare_entries_for_color_by(data_entries, group_key)
        if not facet_pages:
            chart_type = self._normalize_chart_type(self.get_param('chart_type', 'contour'))
            facet_pages = self._build_facet_aware_group_pages(prepared, chart_type, group_key, page_size=8)

        total_pages = max(1, len(facet_pages))
        dir_name, file_name = os.path.split(base_path)
        name_part, _ = os.path.splitext(file_name)
        written_files = []

        # 分页导出 (>1页) 时清理可能残留的无后缀同名旧图，杜绝与 _第1页 完全重复的图片误导漏看后续页
        if total_pages > 1 and os.path.exists(base_path):
            try:
                os.remove(base_path)
            except Exception:
                pass

        for page_idx, page_spec in enumerate(facet_pages):
            self._check_export_cancelled()
            self._report_export_progress(0.15 + 0.75 * (page_idx / total_pages), f"正在导出分页图 {page_idx + 1}/{total_pages}...")
            chunk_entries = self._filter_entries_by_page_spec(prepared, group_key, page_spec)

            fig = self.generate_plot(chunk_entries, is_preview=False)

            if total_pages > 1:
                fig.text(0.95, 0.02, f"第 {page_idx + 1} 页 / 共 {total_pages} 页",
                         ha='right', va='bottom', fontsize=9, color='gray')

            out_path = os.path.join(dir_name, f"{name_part}_第{page_idx + 1}页{ext}") if total_pages > 1 else base_path
            self._save_figure(fig, out_path)
            written_files.append(out_path)
            plt.close(fig)
        return written_files


    def _export_dataset(self, data, out_path, ext):
        self._check_export_cancelled()
        rule_summary = self._format_chart_group_rule_summary(self.get_param("chart_group_rule", None))
        self._report_export_progress(0.05, f"正在按{rule_summary}准备导出数据...")
        chart_type = self._normalize_chart_type(self.get_param('chart_type', 'contour'))
        groupby = self._resolve_color_by()
        group_key = self._get_group_key(groupby)

        scale_val = self.get_param('scale')
        if scale_val and ("T" in str(scale_val) or "t_value" in str(scale_val).lower()):
            scale = "T 值"
        else:
            scale = "Hz"

        written_files = []
        pagination_state = self._get_group_pagination_state(data, chart_type, groupby)
        if pagination_state['is_paginated_heatmap'] and pagination_state['pages']:
            with _MATPLOTLIB_LOCK:
                if ext == ".pdf":
                    written_files = self._export_overview_heatmap_paginated_pdf(out_path, data, scale, pagination_state['pages'])
                else:
                    written_files = self._export_overview_heatmap_paginated_images(out_path, data, scale, ext, pagination_state['pages'])
            self._report_export_progress(1.0, "当前任务完成")
            return written_files

        facet_pages = pagination_state.get('facet_pages', [])
        if len(facet_pages) > 1 and not self._is_overview_heatmap_chart(chart_type):
            with _MATPLOTLIB_LOCK:
                if ext == ".pdf":
                    written_files = self._export_paginated_pdf(out_path, data, group_key, scale, facet_pages=facet_pages)
                else:
                    written_files = self._export_paginated_images(out_path, data, group_key, scale, ext, facet_pages=facet_pages)
        else:
            self._report_export_progress(0.2, "正在生成图表...")
            self._check_export_cancelled()
            with _MATPLOTLIB_LOCK:
                fig = self.generate_plot(data, is_preview=False)
                self._check_export_cancelled()
                self._report_export_progress(0.9, "正在写入文件...")
                self._save_figure(fig, out_path)
                written_files = [out_path]
                plt.close(fig)
        self._report_export_progress(1.0, "当前任务完成")
        return written_files


    def _parse_custom_ratio_value(self, raw_val, default=1.5):
        if isinstance(raw_val, (int, float)):
            try:
                v = float(raw_val)
                if np.isfinite(v) and v > 0:
                    return max(0.3, min(4.0, v))
            except Exception:
                pass
            return default
        s = str(raw_val or "").strip().replace("：", ":").replace("/", ":")
        if not s:
            return default
        if ":" in s:
            parts = [p.strip() for p in s.split(":") if p.strip()]
            if len(parts) == 2:
                try:
                    w_v = float(parts[0])
                    h_v = float(parts[1])
                    if w_v > 0 and h_v > 0:
                        return max(0.3, min(4.0, w_v / h_v))
                except Exception:
                    pass
        try:
            v = float(s)
            if np.isfinite(v) and v > 0:
                return max(0.3, min(4.0, v))
        except Exception:
            pass
        return default


    def _export_overview_heatmap_paginated_pdf(self, out_file, data, scale, pages):
        pdf_pages = PdfPages(out_file)
        total_pages = len(pages)
        try:
            for page_idx, page_rows in enumerate(pages):
                self._check_export_cancelled()
                self._report_export_progress(0.15 + 0.75 * (page_idx / total_pages), f"正在导出分页概览图 {page_idx + 1}/{total_pages}...")

                allowed_pairs = {row['row_id'] for row in page_rows}
                chunk_entries = [e for e in data if (self._entry_wordlist_group(e), e['label']) in allowed_pairs]
                fig = self.generate_plot(chunk_entries, is_preview=False)

                fig.text(0.95, 0.02, f"第 {page_idx + 1} 页 / 共 {total_pages} 页",
                         ha='right', va='bottom', fontsize=9, color='gray')

                pdf_pages.savefig(fig, bbox_inches='tight')
                plt.close(fig)
        finally:
            pdf_pages.close()
        return [out_file]


    def _export_overview_heatmap_paginated_images(self, base_path, data, scale, ext, pages):
        total_pages = len(pages)
        dir_name, file_name = os.path.split(base_path)
        name_part, _ = os.path.splitext(file_name)
        written_files = []

        if total_pages > 1 and os.path.exists(base_path):
            try:
                os.remove(base_path)
            except Exception:
                pass

        for page_idx, page_rows in enumerate(pages):
            self._check_export_cancelled()
            self._report_export_progress(0.15 + 0.75 * (page_idx / max(1, total_pages)), f"正在导出分页概览图 {page_idx + 1}/{total_pages}...")

            allowed_pairs = {row['row_id'] for row in page_rows}
            chunk_entries = [e for e in data if (self._entry_wordlist_group(e), e['label']) in allowed_pairs]
            fig = self.generate_plot(chunk_entries, is_preview=False)

            if total_pages > 1:
                fig.text(0.95, 0.02, f"第 {page_idx + 1} 页 / 共 {total_pages} 页",
                         ha='right', va='bottom', fontsize=9, color='gray')

            out_path = os.path.join(dir_name, f"{name_part}_第{page_idx + 1}页{ext}") if total_pages > 1 else base_path
            self._save_figure(fig, out_path)
            written_files.append(out_path)
            plt.close(fig)
        return written_files
