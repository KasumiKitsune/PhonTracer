# -*- coding: utf-8 -*-
"""
modules.chart_engine.presets
~~~~~~~~~~~~~~~~~~~~~~~~~~~~
出版级预设配置、样式规范与混入类。
完全无头，仅依赖 matplotlib Agg 后端，不依赖任何 GUI 库。
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple
import numpy as np

import matplotlib
matplotlib.use("Agg", force=True)
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm

PUBLICATION_PRESETS = {
    "chinese_journal": {
        "name": "chinese_journal",
        "label": "中文期刊规范",
        "description": "《中国语文》《方言》《民族语文》等中文核心期刊规范：纯白背景、黑白灰/深沉学术色 + 线型 + 点标记三重冗余编码、四框内向刻度、去图内顶部大标题、宋体+Times+Charis SIL矢量内嵌",
        "fig_facecolor": "#FFFFFF",
        "ax_facecolor": "#FFFFFF",
        "colors": [
            "#000000", "#374151", "#1E3A8A", "#7F1D1D",
            "#065F46", "#581C87", "#78350F", "#4B5563",
        ],
        "linestyles": [
            "-", "--", "-.", ":",
            (0, (5, 2)), (0, (3, 1, 1, 1, 1, 1)), (0, (5, 1)), (0, (1, 1)),
        ],
        "markers": ["o", "s", "^", "v", "D", "*", "P", "X"],
        "triple_redundant_encoding": True,
        "tick_direction": "in",
        "despine": False,
        "show_title": False,
        "subplot_label_prefix": True,
        "font_sans": [
            "Microsoft YaHei", "SimSun", "SimHei", "Songti SC", "PingFang SC", "Heiti SC",
            "Source Han Serif SC", "Source Han Sans SC", "Noto Serif CJK SC", "Noto Sans CJK SC",
            "Noto Serif CJK JP", "Noto Sans CJK JP", "WenQuanYi Micro Hei", "Droid Sans Fallback",
            "Arial Unicode MS", "Times New Roman", "Charis SIL", "Doulos SIL", "Segoe UI",
            "DejaVu Serif", "DejaVu Sans",
        ],
        "pdf_fonttype": 42,
        "ps_fonttype": 42,
        "base_fontsize": 9.0,
        "title_fontsize": 10.0,
        "linewidth": 1.8,
        "markersize": 5.2,
        "single_col_mm": 80.0,
        "double_col_mm": 150.0,
        "default_layout_size": "双栏通栏 150mm",
        "default_dpi": 600,
        "legend_frameon": True,
        "legend_framealpha": 0.9,
        "annotate_error_in_legend": False,
    },
    "intl_journal": {
        "name": "intl_journal",
        "label": "国际期刊规范",
        "description": "JASA / Journal of Phonetics / LabPhon 国际期刊规范：纯白背景、Okabe-Ito色盲安全调色板 + 线型与点标记区分、默认Despine去顶/右框线、去图内大标题、图例无框并注明误差带定义",
        "fig_facecolor": "#FFFFFF",
        "ax_facecolor": "#FFFFFF",
        "colors": [
            "#0072B2", "#D55E00", "#009E73", "#CC79A7",
            "#F0E442", "#56B4E9", "#E69F00", "#000000",
        ],
        "linestyles": [
            "-", "--", "-.", ":",
            (0, (5, 2)), (0, (3, 1, 1, 1)), (0, (5, 1)), (0, (2, 2)),
        ],
        "markers": ["o", "s", "^", "D", "v", "P", "X", "*"],
        "triple_redundant_encoding": True,
        "tick_direction": "out",
        "despine": True,
        "show_title": False,
        "subplot_label_prefix": True,
        "font_sans": [
            "Microsoft YaHei", "SimHei", "PingFang SC", "Heiti SC", "Songti SC", "SimSun",
            "Source Han Sans SC", "Source Han Serif SC", "Noto Sans CJK SC", "Noto Serif CJK SC",
            "Noto Sans CJK JP", "Noto Serif CJK JP", "WenQuanYi Micro Hei", "Droid Sans Fallback",
            "Arial Unicode MS", "Arial", "Helvetica", "Times New Roman", "Charis SIL", "Doulos SIL",
            "Segoe UI", "DejaVu Sans",
        ],
        "pdf_fonttype": 42,
        "ps_fonttype": 42,
        "base_fontsize": 9.0,
        "title_fontsize": 10.0,
        "linewidth": 1.8,
        "markersize": 5.0,
        "single_col_mm": 85.0,
        "one_half_col_mm": 114.0,
        "double_col_mm": 170.0,
        "default_layout_size": "双栏 170mm",
        "default_dpi": 600,
        "legend_frameon": False,
        "legend_framealpha": 0.85,
        "annotate_error_in_legend": True,
    },
    "presentation": {
        "name": "presentation",
        "label": "汇报演示",
        "description": "学术汇报与海报演示 (PPT / Poster)：16:9宽屏比例、高饱和现代科研配色、线条加粗至2.5pt、基准字号放大(13-14pt)、默认保留顶部大标题与副标题",
        "fig_facecolor": "#FFFFFF",
        "ax_facecolor": "#FFFFFF",
        "colors": [
            "#2563EB", "#DC2626", "#16A34A", "#9333EA",
            "#EA580C", "#0891B2", "#CA8A04", "#4F46E5",
        ],
        "linestyles": ["-", "--", "-.", ":", "-", "--", "-.", ":"],
        "markers": ["o", "s", "^", "D", "v", "P", "*", "X"],
        "triple_redundant_encoding": False,
        "tick_direction": "out",
        "despine": False,
        "show_title": True,
        "subplot_label_prefix": True,
        "font_sans": [
            "Microsoft YaHei", "SimHei", "PingFang SC", "Heiti SC", "Songti SC", "SimSun",
            "Source Han Sans SC", "Noto Sans CJK SC", "Noto Serif CJK SC", "WenQuanYi Micro Hei",
            "Droid Sans Fallback", "Arial Unicode MS", "Arial", "Charis SIL", "Doulos SIL",
            "Segoe UI", "DejaVu Sans",
        ],
        "pdf_fonttype": 42,
        "ps_fonttype": 42,
        "base_fontsize": 13.5,
        "title_fontsize": 15.0,
        "linewidth": 2.5,
        "markersize": 7.0,
        "aspect_ratio": 16.0 / 9.0,
        "default_figsize": (10.67, 6.0),
        "default_layout_size": "汇报宽屏 16:9",
        "default_dpi": 300,
        "legend_frameon": True,
        "legend_framealpha": 0.92,
        "annotate_error_in_legend": False,
    },
}


class ExportCancelled(Exception):
    """Raised when a user cancels an in-progress chart export."""
    pass


class ChartPresetsMixin:
    """出版预设配置与样式应用 Mixin。"""

    def _normalize_preset_theme(self, preset_name=None):
        raw = preset_name if preset_name is not None else self.get_param('preset_theme', getattr(self, 'preset_theme', 'chinese_journal'))
        text = str(raw or "chinese_journal").strip().lower()
        if any(k in text for k in ("intl", "international", "jasa", "jop", "labphon", "国际", "外文", "okabe")):
            return "intl_journal"
        if any(k in text for k in ("present", "ppt", "poster", "slide", "汇报", "演示", "海报")):
            return "presentation"
        return "chinese_journal"


    def get_publication_preset(self, preset_name=None):
        key = self._normalize_preset_theme(preset_name)
        preset = dict(PUBLICATION_PRESETS.get(key, PUBLICATION_PRESETS["chinese_journal"]))
        preset["colors"] = list(preset["colors"])
        preset["linestyles"] = list(preset["linestyles"])
        preset["markers"] = list(preset["markers"])
        preset["font_sans"] = list(preset["font_sans"])
        return preset


    def apply_publication_preset(self, preset_name=None, fig=None, axes=None):
        key = self._normalize_preset_theme(preset_name)
        preset = self.get_publication_preset(key)
        self.preset_theme = key
        if hasattr(self, 'params') and isinstance(self.params, dict) and preset_name is not None:
            self.params['preset_theme'] = key
        if hasattr(self, 'var_preset_theme'):
            try:
                self.var_preset_theme.set(preset["label"])
            except Exception:
                pass
        if hasattr(self, 'seg_preset_theme'):
            try:
                self.seg_preset_theme.set(preset["label"])
            except Exception:
                pass

        self.colors = list(preset["colors"])
        self.linestyles = list(preset["linestyles"])
        self.markers = list(preset["markers"])

        plt.rcParams['font.sans-serif'] = list(preset["font_sans"])
        plt.rcParams['axes.unicode_minus'] = False
        plt.rcParams['pdf.fonttype'] = 42
        plt.rcParams['ps.fonttype'] = 42
        plt.rcParams['font.size'] = float(preset.get("base_fontsize", 9.0))
        plt.rcParams['figure.facecolor'] = "#FFFFFF"
        plt.rcParams['axes.facecolor'] = "#FFFFFF"
        plt.rcParams['savefig.facecolor'] = "#FFFFFF"

        if fig is not None:
            self._apply_preset_to_figure(fig, preset_override=preset, axes_override=axes)
        return preset


    def _get_series_style(self, idx):
        preset = self.get_publication_preset()
        preset_name = preset.get("name", "chinese_journal")
        base_colors = list(preset["colors"])
        extra_colors_map = {
            "chinese_journal": ["#0F766E", "#9D174D", "#4338CA", "#B45309", "#15803D", "#6D28D9"],
            "intl_journal": ["#332288", "#882255", "#44AA99", "#999933", "#AA4499", "#117733"],
            "presentation": ["#BE185D", "#0D9488", "#7C2D12", "#334155", "#047857", "#6D28D9"],
        }
        ext_colors = base_colors + [c for c in extra_colors_map.get(preset_name, extra_colors_map["chinese_journal"]) if c not in base_colors]
        ext_markers = list(preset["markers"]) + [m for m in ["p", "h", "<", ">", "d", "H"] if m not in preset["markers"]]
        ext_linestyles = list(preset["linestyles"]) + [
            (0, (4, 1, 1, 1)), (0, (6, 2)), (0, (2, 1)), (0, (3, 2, 1, 2)),
        ]
        return {
            "color": ext_colors[idx % len(ext_colors)],
            "linestyle": ext_linestyles[idx % len(ext_linestyles)],
            "marker": ext_markers[idx % len(ext_markers)],
            "linewidth": float(preset.get("linewidth", 2.0)),
            "markersize": float(preset.get("markersize", 5.0)),
        }


    def _apply_preset_to_figure(self, fig, chart_type=None, preset_override=None, axes_override=None):
        if fig is None:
            return
        preset = preset_override or self.get_publication_preset()
        preset_key = preset["name"]
        work_mode = self._normalize_work_mode()
        base_fs = float(self.get_param('base_fontsize', preset.get("base_fontsize", 9.0)))
        title_fs = float(preset.get("title_fontsize", base_fs + 1.5))

        try:
            fig.patch.set_facecolor("#FFFFFF")
        except Exception:
            pass

        all_axes = list(axes_override) if axes_override is not None else list(getattr(fig, "axes", []))
        primary_axes = [
            ax for ax in all_axes
            if getattr(ax, "get_visible", lambda: True)()
            and not getattr(ax, "_is_chao_axis", False)
            and not getattr(ax, "_is_colorbar_axis", False)
            and ax.get_label() != "<colorbar>"
        ]
        chao_axes = [
            ax for ax in all_axes
            if getattr(ax, "get_visible", lambda: True)()
            and getattr(ax, "_is_chao_axis", False)
        ]

        show_title_param = self.get_param("show_title", None)
        if show_title_param is None:
            if work_mode == "qc":
                show_title = True
            else:
                show_title = bool(preset.get("show_title", False))
        else:
            if isinstance(show_title_param, str):
                show_title = show_title_param.strip().lower() in ("true", "1", "yes", "on", "是")
            else:
                show_title = bool(show_title_param)

        is_heatmap_chart = str(chart_type or "") in (
            "overview_heatmap", "formant_overview_heatmap", "formant_pillai",
            "pillai_matrix", "vowel_merger",
        ) or any(len(getattr(ax, "images", [])) > 0 and getattr(ax, "_is_matrix_heatmap", False) for ax in primary_axes)

        for idx, ax in enumerate(primary_axes):
            try:
                ax.set_facecolor("#FFFFFF")
            except Exception:
                pass

            is_chao_parent = bool(getattr(ax, "_has_chao_axis", False))
            is_vowel_space = bool(getattr(ax, "_is_vowel_space_ax", False)) or str(chart_type or "") == "formant_space"
            praat_axes = bool(self.get_param("praat_axes", False))

            if preset_key == "chinese_journal":
                for sp_name in ("top", "right", "bottom", "left"):
                    if sp_name in ax.spines:
                        ax.spines[sp_name].set_visible(True)
                        ax.spines[sp_name].set_color("#111827")
                        ax.spines[sp_name].set_linewidth(0.9)
                ax.tick_params(
                    axis="both",
                    which="both",
                    direction="in",
                    top=(not is_heatmap_chart),
                    right=(not is_chao_parent and not is_heatmap_chart),
                    labelsize=max(7.5, base_fs - 0.5),
                )
                if is_chao_parent:
                    ax.yaxis.set_ticks_position("left")
                    ax.tick_params(axis="y", which="both", right=False, labelright=False)
            elif preset_key == "intl_journal":
                if is_vowel_space and praat_axes:
                    ax.spines["top"].set_visible(True)
                    ax.spines["right"].set_visible(True)
                    ax.spines["bottom"].set_visible(False)
                    ax.spines["left"].set_visible(False)
                    ax.xaxis.tick_top()
                    ax.xaxis.set_label_position("top")
                    ax.yaxis.tick_right()
                    ax.yaxis.set_label_position("right")
                else:
                    if "top" in ax.spines:
                        ax.spines["top"].set_visible(False)
                    if "right" in ax.spines and not is_chao_parent:
                        ax.spines["right"].set_visible(False)
                    for sp_name in ("bottom", "left"):
                        if sp_name in ax.spines:
                            ax.spines[sp_name].set_visible(True)
                            ax.spines[sp_name].set_color("#111827")
                            ax.spines[sp_name].set_linewidth(0.9)
                ax.tick_params(
                    axis="both",
                    which="both",
                    direction="out",
                    top=(is_vowel_space and praat_axes),
                    right=(is_vowel_space and praat_axes),
                    labelsize=max(7.5, base_fs - 0.5),
                )
            else:  # presentation
                for sp_name in ("bottom", "left"):
                    if sp_name in ax.spines:
                        ax.spines[sp_name].set_visible(True)
                        ax.spines[sp_name].set_linewidth(1.2)
                ax.tick_params(axis="both", which="both", direction="out", labelsize=max(11.0, base_fs - 1.0))
                for line in ax.get_lines():
                    if line.get_linewidth() >= 1.5 and line.get_linestyle() != ":":
                        line.set_linewidth(max(2.5, float(line.get_linewidth())))

            try:
                ax.xaxis.label.set_size(base_fs)
                ax.yaxis.label.set_size(base_fs)
            except Exception:
                pass

            # Subplot (a), (b) numbering & top title handling (clear center/right title first to prevent collision!)
            orig_title = getattr(ax, "_original_title", None)
            if orig_title is None:
                orig_title = ax.get_title(loc="center") or ax.get_title(loc="left") or ""
                ax._original_title = orig_title

            if len(primary_axes) > 1:
                letter = chr(97 + (idx % 26))
                clean_t = re.sub(r"^\([a-z]\)\s*", "", str(orig_title).strip())
                sub_lbl = f"({letter}) {clean_t}".strip() if clean_t else f"({letter})"
                ax.set_title("", loc="center")
                ax.set_title("", loc="right")
                ax.set_title(sub_lbl, loc="left", fontsize=title_fs, fontweight="bold")
            else:
                ax.set_title("", loc="left")
                ax.set_title("", loc="right")
                if show_title and orig_title:
                    ax.set_title(orig_title, loc="center", fontsize=title_fs, fontweight="bold")
                else:
                    ax.set_title("", loc="center")

            # Legend refinement
            leg = ax.get_legend()
            if leg is not None:
                if preset_key == "intl_journal":
                    leg.set_frame_on(False)
                    err_lbl = getattr(ax, "_error_band_label", None)
                    if err_lbl:
                        curr_leg_title = leg.get_title().get_text() if leg.get_title() else ""
                        if err_lbl not in curr_leg_title:
                            leg.set_title(err_lbl, prop={"size": max(7.5, base_fs - 1.0)})
                elif preset_key == "chinese_journal":
                    leg.set_frame_on(True)
                    frame = leg.get_frame()
                    frame.set_facecolor("#FFFFFF")
                    frame.set_edgecolor("#D1D5DB")
                    frame.set_alpha(0.92)
                elif preset_key == "presentation":
                    for txt in leg.get_texts():
                        txt.set_fontsize(max(11.0, base_fs - 1.5))

        # Sync all secondary Chao 5-level axes strictly to their parent Y limits
        tick_dir = str(preset.get("tick_direction", "in"))
        for ca in chao_axes:
            try:
                if "right" in ca.spines:
                    ca.spines["right"].set_visible(True)
                    ca.spines["right"].set_color("#111827")
                    ca.spines["right"].set_linewidth(0.9)
                for sp_hide in ("top", "left", "bottom"):
                    if sp_hide in ca.spines:
                        ca.spines[sp_hide].set_visible(False)
            except Exception:
                pass
            try:
                ca.yaxis.set_ticks_position("right")
                ca.tick_params(
                    axis="y",
                    which="both",
                    direction=tick_dir,
                    right=True,
                    left=False,
                    labelright=True,
                    labelleft=False,
                    labelsize=max(7.5, base_fs - 1.0),
                )
            except Exception:
                pass
            parent_ax = getattr(ca, "_parent_ax", None)
            if parent_ax is not None:
                try:
                    parent_ax.yaxis.set_ticks_position("left")
                    parent_ax.tick_params(axis="y", which="both", right=False, labelright=False)
                except Exception:
                    pass
                try:
                    ca.set_yticks([0.5, 1.5, 2.5, 3.5, 4.5])
                    ca.set_yticklabels(["1度 [0-1]", "2度 [1-2]", "3度 [2-3]", "4度 [3-4]", "5度 [4-5]"], fontsize=max(7.5, base_fs - 1.0))
                    ca.autoscale(enable=False, axis="y")
                    ca.set_autoscale_on(False)
                    ca.set_ylim(parent_ax.get_ylim())
                except Exception:
                    pass

        if not show_title and getattr(fig, "_suptitle", None) is not None:
            try:
                fig._original_suptitle = fig._suptitle.get_text()
                fig.suptitle("")
            except Exception:
                pass
