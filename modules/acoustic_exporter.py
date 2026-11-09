import warnings
warnings.filterwarnings("ignore", category=UserWarning, message=".*Glyph.*missing from.*font.*")
import math
import os
import re
import threading
import queue
from collections import Counter
import numpy as np
try:
    import parselmouth
except ImportError:
    from modules import _parselmouth_fallback as parselmouth
    import sys as _sys
    _sys.modules.setdefault('parselmouth', parselmouth)
    _sys.modules.setdefault('parselmouth.praat', parselmouth.praat)
import matplotlib

matplotlib.use("Agg", force=True)

import warnings
warnings.filterwarnings("ignore", category=UserWarning, message=".*Tight layout not applied.*")
warnings.filterwarnings("ignore", category=RuntimeWarning, message=".*Mean of empty slice.*")
warnings.filterwarnings("ignore", category=RuntimeWarning, message=".*Degrees of freedom <= 0 for slice.*")
warnings.filterwarnings("ignore", category=RuntimeWarning, message=".*All-NaN slice encountered.*")


def _safe_nanmean(arr, axis=None, default=np.nan):
    a = np.asarray(arr, dtype=float)
    if a.size == 0:
        return default
    if axis is None:
        valid = a[np.isfinite(a)]
        return float(np.mean(valid)) if valid.size > 0 else default
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)
        if a.ndim == 2 and axis == 0:
            out = np.full(a.shape[1], default, dtype=float)
            for col_idx in range(a.shape[1]):
                col = a[:, col_idx]
                valid = col[np.isfinite(col)]
                if valid.size > 0:
                    out[col_idx] = float(np.mean(valid))
            return out
        return np.nanmean(a, axis=axis)


def _safe_nanstd(arr, axis=None, default=np.nan):
    a = np.asarray(arr, dtype=float)
    if a.size == 0:
        return default
    if axis is None:
        valid = a[np.isfinite(a)]
        return float(np.std(valid)) if valid.size > 0 else default
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)
        if a.ndim == 2 and axis == 0:
            out = np.full(a.shape[1], default, dtype=float)
            for col_idx in range(a.shape[1]):
                col = a[:, col_idx]
                valid = col[np.isfinite(col)]
                if valid.size > 0:
                    out[col_idx] = float(np.std(valid))
            return out
        return np.nanstd(a, axis=axis)


def _safe_nanmax(arr, default=np.nan):
    a = np.asarray(arr, dtype=float)
    valid = a[np.isfinite(a)]
    return float(np.max(valid)) if valid.size > 0 else default

from scipy.stats import gaussian_kde
try:
    from scipy.spatial import ConvexHull
except ImportError:
    ConvexHull = None
from .data_utils import (
    split_into_syllables,
    get_export_text_for_item,
    get_item_syllable_bounds,
    sample_formant_points_by_bounds,
    sample_formant_points_by_bounds_extended,
    compute_speaker_f0_bounds,
    hz_to_t_value,
)
try:
    from .report_generator import fit_chao_tone_code
except Exception:
    def fit_chao_tone_code(t_contour, t_start=None, t_mid=None, t_end=None):
        vals = [float(v) for v in (t_contour or []) if v is not None and np.isfinite(v)]
        if not vals:
            return "33"
        d0 = int(np.clip(math.floor(vals[0]) + 1, 1, 5))
        d1 = int(np.clip(math.floor(vals[-1]) + 1, 1, 5))
        return f"{d0}{d1}"


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
            "SimSun", "Songti SC", "Source Han Serif SC", "Noto Serif CJK SC",
            "Times New Roman", "Microsoft YaHei", "Charis SIL", "Doulos SIL",
            "Segoe UI", "DejaVu Serif", "DejaVu Sans",
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
            "Microsoft YaHei", "Arial", "Helvetica", "Times New Roman", "Charis SIL", "Doulos SIL",
            "Noto Sans CJK SC", "Segoe UI", "DejaVu Sans",
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
            "Microsoft YaHei", "Arial", "Noto Sans CJK SC", "Charis SIL",
            "Doulos SIL", "Segoe UI", "DejaVu Sans",
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


def _compute_2d_convex_hull_and_area(points_xy):
    """Compute 2D convex hull polygon vertices and enclosed area (VSA)."""
    pts = np.asarray(points_xy, dtype=float)
    if pts.ndim != 2 or pts.shape[1] < 2:
        return np.empty((0, 2)), 0.0
    valid = np.isfinite(pts[:, 0]) & np.isfinite(pts[:, 1])
    pts = pts[valid, :2]
    if len(pts) < 3:
        return pts, 0.0

    # Deduplicate near-identical points
    unique_pts = np.unique(np.round(pts, 6), axis=0)
    if len(unique_pts) < 3:
        return unique_pts, 0.0

    if ConvexHull is not None:
        try:
            hull = ConvexHull(unique_pts)
            verts = unique_pts[hull.vertices]
            # In 2D scipy.spatial.ConvexHull, hull.volume is the polygon area and hull.area is perimeter
            area = float(hull.volume)
            return verts, area
        except Exception:
            pass

    # Fallback: Andrew's monotone chain 2D convex hull + Shoelace area formula
    sorted_pts = unique_pts[np.lexsort((unique_pts[:, 1], unique_pts[:, 0]))]

    def _cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower = []
    for p in sorted_pts:
        while len(lower) >= 2 and _cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper = []
    for p in reversed(sorted_pts):
        while len(upper) >= 2 and _cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    hull_pts = np.asarray(lower[:-1] + upper[:-1], dtype=float)
    if len(hull_pts) < 3:
        return hull_pts, 0.0
    x = hull_pts[:, 0]
    y = hull_pts[:, 1]
    area = 0.5 * float(np.abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))))
    return hull_pts, area


def _compute_pillai_bartlett_trace(pts_a, pts_b):
    """
    Compute two-group MANOVA Pillai-Bartlett Trace (Pillai score in [0, 1])
    and centroid Euclidean distance in F1-F2 (or F1-F2-F3) acoustic space.
    Small Pillai score (~0) indicates high overlap / vowel merger;
    large Pillai score (~1) indicates distinct / separated vowel categories.
    """
    a = np.asarray(pts_a, dtype=float)
    b = np.asarray(pts_b, dtype=float)
    if a.ndim == 1:
        a = a.reshape(-1, 1)
    if b.ndim == 1:
        b = b.reshape(-1, 1)
    a = a[np.all(np.isfinite(a), axis=1)]
    b = b[np.all(np.isfinite(b), axis=1)]
    if len(a) == 0 or len(b) == 0:
        return np.nan, np.nan

    mean_a = np.mean(a, axis=0)
    mean_b = np.mean(b, axis=0)
    diff = mean_a - mean_b
    euclid_dist = float(np.linalg.norm(diff))
    if euclid_dist < 1e-10 and a.shape == b.shape and np.allclose(a, b, atol=1e-8):
        return 0.0, 0.0

    n_a = float(len(a))
    n_b = float(len(b))
    p = a.shape[1]
    B = (n_a * n_b / (n_a + n_b)) * np.outer(diff, diff)
    da = a - mean_a
    db = b - mean_b
    W = da.T @ da + db.T @ db
    T = B + W
    reg = 1e-6 * max(1.0, float(np.trace(T)) / float(p)) * np.eye(p)
    try:
        mat = np.linalg.solve(T + reg, B)
        pillai = float(np.trace(mat))
    except Exception:
        pillai = float(np.trace(B) / max(1e-9, np.trace(T)))
    pillai = float(np.clip(pillai, 0.0, 1.0))
    return pillai, euclid_dist

_GUI_IMPORT_ERROR = None

try:
    import tkinter as tk
    import customtkinter as ctk
    from tkinter import messagebox, filedialog
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
except ImportError as exc:
    _GUI_IMPORT_ERROR = exc

    import matplotlib
    matplotlib.use("Agg", force=True)

    class _MissingTk:
        LEFT = "left"
        RIGHT = "right"
        X = "x"
        BOTH = "both"

    class _MissingCtkToplevel:
        pass

    class _MissingCtk:
        CTkToplevel = _MissingCtkToplevel

    tk = _MissingTk()
    ctk = _MissingCtk()
    messagebox = None
    filedialog = None
    FigureCanvasTkAgg = None

import matplotlib.pyplot as plt


_MATPLOTLIB_LOCK = threading.RLock()


class ExportCancelled(Exception):
    """Raised when a user cancels an in-progress chart export."""


class AcousticChartExporter:
    PUBLICATION_PRESETS = PUBLICATION_PRESETS

    def __init__(self, project_tree, app=None, all_speakers=None):
        self.project_tree = project_tree
        self.app = app
        self.all_speakers = all_speakers or []
        self.params = {}  # CLI parameter overrides
        self.work_mode = "publication"
        self.preset_theme = "chinese_journal"
        self.colors = ['#2563EB', '#DC2626', '#16A34A', '#9333EA', '#EA580C', '#0891B2', '#CA8A04', '#6366F1']
        self.linestyles = list(PUBLICATION_PRESETS["chinese_journal"]["linestyles"])
        self.markers = list(PUBLICATION_PRESETS["chinese_journal"]["markers"])
        self._speaker_data_cache = {}
        self._force_live_extract = False
        self.chart_group_rule = self._default_chart_group_rule()

        # Load active speaker's data items as default fallback
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
        colors = preset["colors"]
        linestyles = preset["linestyles"]
        markers = preset["markers"]
        return {
            "color": colors[idx % len(colors)],
            "linestyle": linestyles[idx % len(linestyles)],
            "marker": markers[idx % len(markers)],
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

            # Subplot (a), (b) numbering & top title handling
            orig_title = getattr(ax, "_original_title", None)
            if orig_title is None:
                orig_title = ax.get_title() or ""
                ax._original_title = orig_title

            if len(primary_axes) > 1:
                letter = chr(97 + (idx % 26))
                clean_t = re.sub(r"^\([a-z]\)\s*", "", str(orig_title).strip())
                sub_lbl = f"({letter}) {clean_t}".strip() if clean_t else f"({letter})"
                ax.set_title(sub_lbl, loc="left", fontsize=title_fs, fontweight="bold")
            else:
                if show_title:
                    if orig_title:
                        ax.set_title(orig_title, fontsize=title_fs, fontweight="bold")
                else:
                    ax.set_title("")

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

        if not show_title and getattr(fig, "_suptitle", None) is not None:
            try:
                fig._original_suptitle = fig._suptitle.get_text()
                fig.suptitle("")
            except Exception:
                pass

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
        fig.savefig(out_path, dpi=self._resolve_save_dpi(fig), bbox_inches='tight')

    def get_param(self, name, default=None):
        runtime_params = getattr(getattr(self, "_render_runtime", None), "params", None)
        if runtime_params is not None and name in runtime_params:
            return runtime_params[name]

        # If explicitly set in params (CLI mode)
        if hasattr(self, 'params') and self.params is not None and name in self.params:
            return self.params[name]

        # Dynamic GUI property routing if subclassed by CTkToplevel
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

    def _get_legend_kwargs(self, ax=None):
        loc_val = self.get_param('legend_loc', '右上')
        has_chao = bool(ax is not None and getattr(ax, "_has_chao_axis", False))
        default_outside = True if has_chao else False
        outside_val = self.get_param('legend_outside', default_outside)
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
            "labelspacing": 0.55,
            "frameon": bool(preset.get("legend_frameon", True)),
        }

        if outside_val:
            right_anchor_x = 1.20 if has_chao else 1.02
            if loc_val == "右上":
                kwargs.update({"loc": "upper left", "bbox_to_anchor": (right_anchor_x, 1.0)})
            elif loc_val == "右下":
                kwargs.update({"loc": "lower left", "bbox_to_anchor": (right_anchor_x, 0.0)})
            elif loc_val == "左上":
                kwargs.update({"loc": "upper right", "bbox_to_anchor": (-0.02, 1.0)})
            elif loc_val == "左下":
                kwargs.update({"loc": "lower right", "bbox_to_anchor": (-0.02, 0.0)})
        else:
            kwargs["loc"] = loc_str

        return kwargs

    # --- CORE DATA EXTRACTION ENGINE ---
    def _extract_active_data(self, speakers_list, filter_groups=True, apply_chart_grouping=True):
        analysis_mode = self.project_tree.app_state_params.get('analysis_mode', 'f0')
        if analysis_mode == 'formant':
            return self._extract_active_formant_data(
                speakers_list,
                filter_groups=filter_groups,
                apply_chart_grouping=apply_chart_grouping,
            )
        num_points = self.project_tree.app_state_params.get('pts', 11)
        data_entries = []

        for speaker in speakers_list:
            if speaker is None:
                continue
            cache_key = (getattr(speaker, 'id', id(speaker)), 'f0', int(num_points))
            if not getattr(self, '_force_live_extract', False) and hasattr(self, '_speaker_data_cache') and cache_key in self._speaker_data_cache:
                data_entries.extend([self._attach_entry_group_defaults(e) for e in self._speaker_data_cache[cache_key]])
                continue

            orig_items = self.project_tree.items
            try:
                self.project_tree.items = speaker.items
                s_struct = self.project_tree._get_items_by_group_for_dict(speaker.items)

                speaker_f0_pool = []
                speaker_items_temp = []

                for grp_name, children in s_struct:
                    for child in children:
                        item = speaker.items[child]
                        self.project_tree._ensure_item_loaded(item)
                        if item.get('start') is None or (not item.get('pitch') and not item.get('pitch_data')):
                            continue

                        total_dur, syl_data = self.project_tree._extract_syl_data(item, num_points)
                        if total_dur <= 0:
                            continue

                        p_xs, p_freqs = self.project_tree._get_pitch_arrays_for_item(item)
                        if p_xs is None or p_freqs is None:
                            continue

                        valid_f0_mask = np.isfinite(p_freqs) & (p_freqs > 0)
                        active_ratio = np.mean(valid_f0_mask) if len(p_freqs) > 0 else 0.0

                        warnings = item.get('warnings', [])

                        speaker_items_temp.append({
                            'item_id': str(child),
                            'speaker_name': speaker.name,
                            'group': grp_name,
                            'wordlist_group': grp_name,
                            'chart_group': grp_name,
                            'label': item.get('label', ''),
                            'total_dur': total_dur,
                            'syl_data': syl_data,
                            'raw_xs': p_xs,
                            'raw_freqs': p_freqs,
                            'active_ratio': active_ratio,
                            'warnings': warnings,
                            'raw_item': item,
                            **self._item_wordlist_metadata(item),
                        })

                        speaker_f0_pool.extend([float(f) for f in p_freqs if np.isfinite(f) and f > 0])

                s_min, s_max = self._get_f0_normalization_bounds(speaker_f0_pool)

                def _normalize_freqs(f_arr):
                    return hz_to_t_value(f_arr, s_min, s_max, clip=True)

                speaker_data_entries = []
                for entry in speaker_items_temp:
                    cleaned_syl_data = []
                    normalized_syl_data = []
                    for s_dur, freqs in entry['syl_data']:
                        f_arr = np.asarray(freqs, dtype=float)
                        clean_freqs = np.where(np.isfinite(f_arr) & (f_arr > 0), f_arr, np.nan)
                        cleaned_syl_data.append((s_dur, clean_freqs.tolist()))
                        norm_freqs = _normalize_freqs(clean_freqs).tolist()
                        normalized_syl_data.append((s_dur, norm_freqs))

                    entry['syl_data'] = cleaned_syl_data
                    entry['normalized_syl_data'] = normalized_syl_data
                    entry['normalized_raw_freqs'] = _normalize_freqs(entry['raw_freqs'])
                    entry['speaker_f0_bounds'] = (s_min, s_max)
                    speaker_data_entries.append(self._attach_entry_group_defaults(entry))
            finally:
                self.project_tree.items = orig_items

            if hasattr(self, '_speaker_data_cache'):
                self._speaker_data_cache[cache_key] = speaker_data_entries
            data_entries.extend(speaker_data_entries)

        if apply_chart_grouping:
            data_entries = self._apply_chart_grouping(data_entries)
        else:
            data_entries = self._apply_chart_grouping(
                data_entries,
                rule=self._default_chart_group_rule(),
                preserve_original_group=True,
            )

        if filter_groups:
            selected_groups = self.get_param('selected_groups', None)
            if selected_groups is not None:
                if isinstance(selected_groups, str):
                    selected_groups = [g.strip() for g in selected_groups.split(',') if g.strip()]
                selected_groups = set(selected_groups)
                data_entries = [e for e in data_entries if e['group'] in selected_groups]
            elif hasattr(self, 'group_checkbox_vars') and self.group_checkbox_vars:
                selected_groups = {g for g, var in self.group_checkbox_vars.items() if var.get()}
                data_entries = [e for e in data_entries if e['group'] in selected_groups]

        return data_entries

    def _get_current_data_entries(self):
        scope = self.get_param('export_scope', 'active')
        if scope == "active" or not self.all_speakers:
            return self._extract_active_data([self.active_speaker])
        elif scope == "separate":
            idx = getattr(self, 'current_preview_page', 0)
            if idx < 0 or idx >= len(self.all_speakers):
                idx = 0
                self.current_preview_page = 0
            current_speaker = self.all_speakers[idx]
            return self._extract_active_data([current_speaker])
        else:
            return self._extract_active_data(self.all_speakers)

    def _resolve_color_by(self, fallback=None):
        raw = None
        runtime_params = getattr(getattr(self, "_render_runtime", None), "params", None)
        if runtime_params is not None:
            raw = runtime_params.get("color_by") or runtime_params.get("groupby")
        if raw is None and hasattr(self, "params") and isinstance(self.params, dict):
            raw = self.params.get("color_by") or self.params.get("groupby")
        if raw is None and fallback is not None:
            raw = fallback
        if raw is None:
            raw = self.get_param("color_by", None) or self.get_param("groupby", "group")
        return str(raw or "group")

    def _resolve_facet_by(self, chart_type=None, fallback=None):
        if fallback is None and chart_type in ("density", "formant_density"):
            fallback = "group"
        raw = None
        runtime_params = getattr(getattr(self, "_render_runtime", None), "params", None)
        if runtime_params is not None:
            raw = runtime_params.get("facet_by") or runtime_params.get("layout")
            if raw is None:
                if chart_type in ("contour", None):
                    raw = runtime_params.get("contour_facet")
                elif chart_type == "density":
                    raw = runtime_params.get("density_facet")
                elif chart_type in ("formant_space", "formant_density"):
                    raw = runtime_params.get("formant_density_facet")
        if raw is None and hasattr(self, "params") and isinstance(self.params, dict):
            raw = self.params.get("facet_by") or self.params.get("layout")
            if raw is None:
                if chart_type in ("contour", None):
                    raw = self.params.get("contour_facet")
                elif chart_type == "density":
                    raw = self.params.get("density_facet")
                elif chart_type in ("formant_space", "formant_density"):
                    raw = self.params.get("formant_density_facet")
        if raw is None:
            raw = self.get_param("facet_by", None)
            if raw is None:
                if chart_type in ("contour", None):
                    raw = self.get_param("contour_facet", None)
                elif chart_type == "density":
                    raw = self.get_param("density_facet", None)
                elif chart_type in ("formant_space", "formant_density"):
                    raw = self.get_param("formant_density_facet", None)
            if raw is None:
                raw = fallback or "none"

        text = str(raw or "none").strip()
        low = text.lower()
        if low in ("none", "single", "单图展示 (不分面)", "不分面 (单图叠加)", "不分面 (混合叠加)", "不分面"):
            return "none"
        if low in ("group", "按字表组别分面", "按声调类型分面", "按字表组分面", "声调类型分面 (默认)") or "组" in text or "声调" in text:
            return "group"
        if low in ("syllable_position", "syllable", "按音节位置分面") or "音节" in text:
            return "syllable_position"
        if low in ("speaker", "speaker_name", "按发音人分面") or "发音人" in text:
            return "speaker"
        if low in ("label", "word", "按词语分面") or "词" in text:
            return "label"
        return "none"

    def _get_group_key(self, groupby_val=None):
        val = str(groupby_val if groupby_val is not None else self._resolve_color_by())
        low = val.strip().lower()
        if low in ("按词语", "按词项", "label", "word") or "词语" in val or "词项" in val:
            return 'label'
        if low in ("按发音人", "speaker", "speaker_name") or "发音人" in val:
            return 'speaker_name'
        if low in ("按单字音节", "按单字/音节", "syl_char", "syllable", "char") or "单字" in val or "音节" in val:
            return 'syl_char'
        if low.startswith("custom") or low.startswith("item_meta") or "自定义字段" in val:
            return 'custom_field'
        return 'group'

    def _prepare_entries_for_color_by(self, data_entries, group_key):
        if not data_entries:
            return data_entries
        color_raw = self._resolve_color_by()
        custom_field_name = self.get_param("custom_field", None)
        if not custom_field_name and ":" in color_raw:
            custom_field_name = color_raw.split(":", 1)[1].strip()
        if not custom_field_name:
            rule = self._get_chart_group_rule()
            custom_field_name = rule.get("field_name", "")

        out = []
        for idx, entry in enumerate(data_entries):
            e = dict(entry)
            e.setdefault("item_id", str(entry.get("item_id", f"item_{idx}")))
            # Populate syl_char
            syls = split_into_syllables(e.get("label", ""))
            e.setdefault("syl_char", syls[0] if syls else (e.get("label") or e.get("group") or "未标注"))
            # Populate custom_field
            meta = e.get("item_meta") if isinstance(e.get("item_meta"), dict) else {}
            if custom_field_name and custom_field_name in meta:
                e["custom_field"] = str(meta[custom_field_name] or "未标注")
            elif meta:
                first_k = sorted(meta.keys())[0]
                e["custom_field"] = str(meta[first_k] or "未标注")
            else:
                e["custom_field"] = str(e.get("chart_group") or e.get("group") or "未标注")

            if group_key == "syl_char" and "syl_data" in e and len(e.get("syl_data", [])) > 1:
                syl_list = e.get("syl_data", [])
                norm_list = e.get("normalized_syl_data", [])
                for s_i, s_item in enumerate(syl_list):
                    sub_e = dict(e)
                    ch = syls[s_i] if s_i < len(syls) else f"{e.get('label', '')}_字{s_i+1}"
                    sub_e["syl_char"] = ch
                    sub_e["syl_data"] = [s_item]
                    if s_i < len(norm_list):
                        sub_e["normalized_syl_data"] = [norm_list[s_i]]
                    out.append(sub_e)
            else:
                out.append(e)
        return out

    def _is_overview_heatmap_chart(self, chart_type):
        return chart_type in ("overview_heatmap", "formant_overview_heatmap")

    def _ordered_unique(self, values):
        ordered = []
        seen = set()
        for value in values:
            if value in seen:
                continue
            seen.add(value)
            ordered.append(value)
        return ordered

    def _default_chart_group_rule(self):
        return {
            "source": "default",
            "tag_mode": "each",
            "selected_values": [],
            "field_name": "",
        }

    def _normalize_chart_group_rule(self, rule=None):
        base = self._default_chart_group_rule()
        if isinstance(rule, dict):
            base.update(rule)
        source = str(base.get("source") or "default")
        if source not in {
            "default", "item_tags", "group_tags", "item_meta",
            "metadata_source", "label", "speaker_name"
        }:
            source = "default"
        tag_mode = str(base.get("tag_mode") or "each")
        if tag_mode not in {"each", "contains", "filter_default"}:
            tag_mode = "each"
        selected_values = base.get("selected_values") or []
        if isinstance(selected_values, str):
            selected_values = [v.strip() for v in selected_values.split(",") if v.strip()]
        selected_values = [str(v).strip() for v in selected_values if str(v).strip()]
        return {
            "source": source,
            "tag_mode": tag_mode,
            "selected_values": self._ordered_unique(selected_values),
            "field_name": str(base.get("field_name") or "").strip(),
        }

    def _get_chart_group_rule(self):
        return self._normalize_chart_group_rule(self.get_param("chart_group_rule", None))

    def _entry_wordlist_group(self, entry):
        return entry.get("wordlist_group") or entry.get("group") or "未分组"

    def _item_wordlist_metadata(self, item):
        item_meta = item.get("item_meta") or {}
        if not isinstance(item_meta, dict):
            item_meta = {}
        return {
            "wordlist_version": item.get("wordlist_version", "v1"),
            "wordlist_title": item.get("wordlist_title", ""),
            "item_note": item.get("item_note", ""),
            "item_tags": list(item.get("item_tags", []) or []),
            "item_aliases": list(item.get("item_aliases", []) or []),
            "item_meta": dict(item_meta),
            "group_note": item.get("group_note", ""),
            "group_tags": list(item.get("group_tags", []) or []),
            "metadata_source": item.get("metadata_source", ""),
        }

    def _attach_entry_group_defaults(self, entry):
        copied = dict(entry)
        base_group = copied.get("wordlist_group") or copied.get("group") or "未分组"
        copied["wordlist_group"] = base_group
        copied.setdefault("chart_group", copied.get("group", base_group))
        copied.setdefault("item_tags", [])
        copied.setdefault("group_tags", [])
        copied.setdefault("item_meta", {})
        copied.setdefault("metadata_source", "")
        copied.setdefault("item_note", "")
        copied.setdefault("group_note", "")
        copied.setdefault("wordlist_version", "v1")
        copied.setdefault("wordlist_title", "")
        copied.setdefault("item_aliases", [])
        return copied

    def _entry_tag_values(self, entry, source):
        values = entry.get(source) or []
        if isinstance(values, str):
            values = [v.strip() for v in values.replace("；", ",").replace("、", ",").split(",")]
        return self._ordered_unique([str(v).strip() for v in values if str(v).strip()])

    def _resolve_chart_groups_for_entry(self, entry, rule=None):
        rule = self._normalize_chart_group_rule(rule)
        source = rule["source"]
        base_group = self._entry_wordlist_group(entry)
        missing = "未标注"

        if source == "default":
            return [base_group], True
        if source == "label":
            return [str(entry.get("label") or missing)], True
        if source == "speaker_name":
            return [str(entry.get("speaker_name") or missing)], True
        if source == "metadata_source":
            return [str(entry.get("metadata_source") or missing)], True
        if source == "item_meta":
            field_name = rule.get("field_name", "")
            if not field_name:
                return [base_group], True
            item_meta = entry.get("item_meta") or {}
            value = item_meta.get(field_name, "") if isinstance(item_meta, dict) else ""
            value = str(value).strip() if value is not None else ""
            return [value or missing], True

        if source in {"item_tags", "group_tags"}:
            tags = self._entry_tag_values(entry, source)
            selected = set(rule.get("selected_values") or [])
            tag_mode = rule.get("tag_mode", "each")
            if tag_mode == "contains":
                target = next(iter(rule.get("selected_values") or []), "")
                if not target:
                    return [base_group], True
                return ([f"包含：{target}"] if target in tags else [f"不包含：{target}"]), True
            if tag_mode == "filter_default":
                if selected and not selected.intersection(tags):
                    return [], False
                return [base_group], True

            values = [tag for tag in tags if not selected or tag in selected]
            if not values:
                if selected:
                    return [], False
                values = [missing]
            return values, True

        return [base_group], True

    def _chart_grouping_preserves_original_group(self):
        return str(self.get_param("chart_type", "")) in ("tone_effect_time", "tone_sandhi", "sandhi_grid")

    def _apply_chart_grouping(self, data_entries, rule=None, preserve_original_group=None):
        rule = self._get_chart_group_rule() if rule is None else self._normalize_chart_group_rule(rule)
        preserve_original_group = (
            self._chart_grouping_preserves_original_group()
            if preserve_original_group is None else bool(preserve_original_group)
        )
        grouped_entries = []
        for raw_entry in data_entries or []:
            entry = self._attach_entry_group_defaults(raw_entry)
            base_group = self._entry_wordlist_group(entry)
            group_values, include_entry = self._resolve_chart_groups_for_entry(entry, rule)
            if not include_entry:
                continue
            if preserve_original_group:
                group_values = [base_group]
            for group_value in group_values:
                copied = dict(entry)
                chart_group = str(group_value or "未标注")
                copied["wordlist_group"] = base_group
                copied["chart_group"] = chart_group
                copied["group"] = base_group if preserve_original_group else chart_group
                grouped_entries.append(copied)
        return grouped_entries

    def _collect_chart_grouping_options(self, data_entries=None):
        if data_entries is None:
            data_entries = getattr(self, "_all_base_group_filter_entries", None)
        if data_entries is None:
            try:
                data_entries = self._extract_active_data(
                    self.all_speakers if self.all_speakers else [self.active_speaker],
                    filter_groups=False,
                    apply_chart_grouping=False,
                )
            except Exception:
                data_entries = []

        item_tags = Counter()
        group_tags = Counter()
        meta_values = {}
        metadata_sources = Counter()
        for entry in data_entries or []:
            entry = self._attach_entry_group_defaults(entry)
            item_tags.update(self._entry_tag_values(entry, "item_tags"))
            group_tags.update(self._entry_tag_values(entry, "group_tags"))
            metadata_source = str(entry.get("metadata_source") or "").strip()
            if metadata_source:
                metadata_sources[metadata_source] += 1
            item_meta = entry.get("item_meta") or {}
            if isinstance(item_meta, dict):
                for key, value in item_meta.items():
                    key = str(key).strip()
                    value = str(value).strip() if value is not None else ""
                    if not key:
                        continue
                    meta_values.setdefault(key, Counter())[value or "未标注"] += 1
        return {
            "item_tags": item_tags,
            "group_tags": group_tags,
            "item_meta": meta_values,
            "metadata_source": metadata_sources,
        }

    def _chart_group_counts_for_rule(self, rule=None, data_entries=None, preserve_original_group=False):
        if data_entries is None:
            data_entries = getattr(self, "_all_base_group_filter_entries", [])
        grouped_entries = self._apply_chart_grouping(
            data_entries,
            rule=rule,
            preserve_original_group=preserve_original_group,
        )
        counts = Counter()
        for entry in grouped_entries:
            counts[entry.get("group") or "未标注"] += 1
        return counts

    def _format_chart_group_rule_summary(self, rule=None):
        rule = self._normalize_chart_group_rule(rule)
        source = rule["source"]
        selected = rule.get("selected_values") or []
        values_text = "、".join(selected[:3])
        if len(selected) > 3:
            values_text += f" 等 {len(selected)} 项"
        if source == "default":
            return "按默认组"
        if source == "item_tags":
            if rule.get("tag_mode") == "contains":
                return f"词项标签：包含/不包含 {values_text or '未选择'}"
            if rule.get("tag_mode") == "filter_default":
                return f"筛选词项标签：{values_text or '全部'}"
            return f"按词项标签：{values_text or '全部'}"
        if source == "group_tags":
            if rule.get("tag_mode") == "contains":
                return f"组标签：包含/不包含 {values_text or '未选择'}"
            if rule.get("tag_mode") == "filter_default":
                return f"筛选组标签：{values_text or '全部'}"
            return f"按组标签：{values_text or '全部'}"
        if source == "item_meta":
            return f"按自定义字段：{rule.get('field_name') or '未选择'}"
        if source == "metadata_source":
            return "按复核状态"
        if source == "label":
            return "按词项"
        if source == "speaker_name":
            return "按发音人"
        return "按默认组"

    def _split_tone_pair(self, group, entry=None):
        raw_text = str(group or "").strip()
        text = raw_text.replace("＋", "+").replace("/", "+").replace("、", "+").replace("－", "+").replace("-", "+")
        if "+" in text:
            parts = [part.strip() for part in text.split("+") if part.strip()]
            if len(parts) >= 2:
                return parts[0], parts[1]

        # Match patterns like "11_阴阴", "12_阴阳", "24_阳去"
        m = re.match(r"^(\d)(\d)[_\s]*(\S)(\S)$", raw_text)
        if m:
            return f"{m.group(1)}_{m.group(3)}", f"{m.group(2)}_{m.group(4)}"
        m2 = re.match(r"^(\d)(\d)$", raw_text)
        if m2:
            return m2.group(1), m2.group(2)
        m3 = re.match(r"^([阴平阳上入去甲乙丙丁ABCDT1-8]{1})([阴平阳上入去甲乙丙丁ABCDT1-8]{1})$", raw_text)
        if m3 and raw_text not in ("阴平", "阳平", "上声", "去声", "入声", "阴入", "阳入", "阴上", "阳上", "阴去", "阳去"):
            return m3.group(1), m3.group(2)

        if isinstance(entry, dict):
            meta = entry.get("item_meta")
            if isinstance(meta, dict):
                ft = meta.get("front_tone") or meta.get("前字调类") or meta.get("t1")
                bt = meta.get("back_tone") or meta.get("后字调类") or meta.get("t2")
                if ft and bt:
                    return str(ft).strip(), str(bt).strip()
        return None, None

    def _is_two_syllable_tone_entry(self, entry):
        if len(entry.get("syl_data", [])) != 2:
            return False
        front, back = self._split_tone_pair(self._entry_wordlist_group(entry), entry=entry)
        return bool(front and back)

    def _has_tone_effect_time_data(self, data_entries):
        fronts = set()
        backs = set()
        combos = set()
        for entry in data_entries or []:
            if not self._is_two_syllable_tone_entry(entry):
                continue
            front, back = self._split_tone_pair(self._entry_wordlist_group(entry), entry=entry)
            fronts.add(front)
            backs.add(back)
            combos.add((front, back))
        return len(fronts) >= 2 and len(backs) >= 2 and len(combos) >= 4

    def _get_tone_chart_type_values(self, data_entries=None, work_mode=None):
        mode = self._normalize_work_mode(work_mode)
        if mode == "qc":
            return [
                "1. 词条级逐行质检热图",
                "2. 组内个体原始曲线叠加图",
                "3. 检出率与发音人基频体检图",
            ]
        values = [
            "1. 声调格局与时序轮廓图",
            "2. 连读变调与协同发音图",
            "3. 声调声学特征分布雨云图",
            "声调轮廓图",
            "声调分布图",
            "时序密度图",
            "数据质量检查",
            "声调组别概览图",
        ]
        if data_entries is None:
            data_entries = getattr(self, "_all_group_filter_entries", [])
        if self._has_tone_effect_time_data(data_entries):
            values.append("二字组调类效应时间进程")
        return values

    def _get_formant_chart_type_values(self, work_mode=None):
        mode = self._normalize_work_mode(work_mode)
        if mode == "qc":
            return [
                "1. 词条级逐行质检热图",
                "2. 组内个体原始曲线叠加图",
                "3. 检出率与发音人共振峰体检图",
            ]
        return [
            "1. 元音声学空间图",
            "2. 共振峰时序轨迹图",
            "3. 元音对立重叠度与分布图",
            "元音共振峰空间图",
            "共振峰时序轨迹图",
            "共振峰时空密度图",
            "共振峰组别概览图",
        ]

    def _get_tone_overview_metric_values(self, is_integrated=False, data_entries=None):
        values = [
            "与组内均值的偏差 (Z-score / 残差热图)",
            "均值热图 (Mean Map)",
        ]
        if is_integrated:
            values.append("标准差热图 (SD Map)")
        if data_entries is None:
            data_entries = getattr(self, "_all_group_filter_entries", [])
        if self._has_tone_effect_time_data(data_entries):
            values.append("调类组合前后字均值热图")
        return values

    def _tone_effect_blank(self, message):
        fig, ax = plt.subplots(figsize=(7.0, 4.4))
        ax.text(0.5, 0.5, message, ha="center", va="center", fontsize=12, color="#374151")
        ax.axis("off")
        fig.tight_layout()
        return fig

    def _tone_effect_point_x(self, syl_index, point_index, point_count):
        if point_count <= 1:
            percent = 50.0
        else:
            percent = 5.0 + point_index * (90.0 / float(point_count - 1))
        return percent if syl_index == 0 else 100.0 + percent

    def _collect_tone_effect_time_rows(self, data_entries):
        rows = []
        for entry in data_entries:
            if not self._is_two_syllable_tone_entry(entry):
                continue
            front, back = self._split_tone_pair(self._entry_wordlist_group(entry), entry=entry)
            syl_list = entry.get("normalized_syl_data") or entry.get("syl_data", [])
            if len(syl_list) != 2:
                continue
            for syl_index in range(2):
                _dur, points = syl_list[syl_index]
                point_count = min(len(points), self.project_tree.app_state_params.get("pts", 11))
                for point_index in range(point_count):
                    try:
                        value = float(points[point_index])
                    except Exception:
                        continue
                    if not np.isfinite(value):
                        continue
                    rows.append({
                        "front": front,
                        "back": back,
                        "syl": syl_index + 1,
                        "x": self._tone_effect_point_x(syl_index, point_index, point_count),
                        "value": value,
                    })
        return rows

    def _residual_sse_for_tone_effect(self, y_values, columns):
        design_columns = [[1.0 for _ in y_values]]
        for values in columns:
            unique = []
            for value in values:
                if value not in unique:
                    unique.append(value)
            for value in unique[1:]:
                design_columns.append([1.0 if current == value else 0.0 for current in values])
        design = np.asarray(design_columns, dtype=float).T
        y = np.asarray(y_values, dtype=float)
        fitted = np.linalg.lstsq(design, y, rcond=None)[0]
        residual = y - design.dot(fitted)
        return float(np.sum(residual * residual))

    def _is_tone_combination_overview_metric(self):
        metric_val = str(self.get_param('overview_metric', 'mean'))
        return "调类组合" in metric_val or "combination" in metric_val.lower()

    def _build_overview_word_rows(self, data_entries):
        rows = []
        present_groups = self._ordered_unique(entry['group'] for entry in data_entries)
        preferred_order = list(getattr(self, 'available_groups', []) or [])
        group_order = [group_name for group_name in preferred_order if group_name in present_groups]
        for group_name in present_groups:
            if group_name not in group_order:
                group_order.append(group_name)

        for group_name in group_order:
            seen_labels = set()
            for entry in data_entries:
                if entry['group'] != group_name:
                    continue
                label = entry['label']
                if label in seen_labels:
                    continue
                seen_labels.add(label)
                rows.append({
                    'row_id': (group_name, label),
                    'group': group_name,
                    'label': label,
                })
        return rows

    def _build_overview_word_pages(self, data_entries, page_size=24):
        rows = self._build_overview_word_rows(data_entries)
        if not rows:
            return rows, []

        grouped_buckets = []
        current_group = None
        current_bucket = []
        for row in rows:
            if row['group'] != current_group:
                if current_bucket:
                    grouped_buckets.append(current_bucket)
                current_group = row['group']
                current_bucket = [row]
            else:
                current_bucket.append(row)
        if current_bucket:
            grouped_buckets.append(current_bucket)

        pages = []
        current_page = []
        for bucket in grouped_buckets:
            if len(bucket) > page_size:
                if current_page:
                    pages.append(current_page)
                    current_page = []
                for idx in range(0, len(bucket), page_size):
                    chunk = bucket[idx:idx + page_size]
                    if len(chunk) == page_size:
                        pages.append(chunk)
                    else:
                        current_page = list(chunk)
            else:
                if current_page and len(current_page) + len(bucket) > page_size:
                    pages.append(current_page)
                    current_page = list(bucket)
                else:
                    current_page.extend(bucket)
        if current_page:
            pages.append(current_page)

        return rows, pages

    def _get_group_pagination_state(self, data_entries, chart_type, groupby_val=None):
        chart_type = self._normalize_chart_type(chart_type)
        group_key = self._get_group_key(groupby_val)
        prepared = self._prepare_entries_for_color_by(data_entries, group_key)
        unique_groups = self._ordered_unique(entry.get(group_key, entry.get('group')) for entry in prepared)
        total_groups = len(unique_groups)
        is_overview_hm = (
            self._is_overview_heatmap_chart(chart_type)
            and not (chart_type == "overview_heatmap" and self._is_tone_combination_overview_metric())
        )
        rows, pages = ([], [])
        if is_overview_hm and group_key == "label":
            rows, pages = self._build_overview_word_pages(prepared, page_size=24)
        is_paginated_heatmap = bool(is_overview_hm and group_key == "label" and len(rows) > 24 and len(pages) > 1)

        state = {
            'group_key': group_key,
            'unique_groups': unique_groups,
            'total_groups': total_groups,
            'is_paginated_heatmap': is_paginated_heatmap,
            'total_pages': 1,
            'pages': pages if is_paginated_heatmap else [],
        }

        if is_paginated_heatmap:
            state['total_pages'] = len(pages) if pages else 1
        elif is_overview_hm and group_key != "label" and total_groups > 24:
            state['total_pages'] = math.ceil(total_groups / 24)
        elif self._is_overview_heatmap_chart(chart_type):
            state['total_pages'] = 1
        elif chart_type in ("formant_trajectory", "tone_effect_time", "tone_sandhi", "sandhi_grid", "formant_pillai", "pillai_matrix"):
            state['total_pages'] = 1
        else:
            state['total_pages'] = math.ceil(total_groups / 8) if total_groups > 0 else 1

        return state

    def _normalize_chart_type(self, raw_chart_type):
        text = str(raw_chart_type or "contour").strip()
        low = text.lower()
        mapping = {
            "contour": "contour",
            "1. 声调格局与时序轮廓图": "contour",
            "声调格局与时序轮廓图": "contour",
            "声调格局与轮廓图": "contour",
            "声调轮廓图": "contour",
            "tone_sandhi": "tone_sandhi",
            "sandhi_grid": "tone_sandhi",
            "2. 连读变调与协同发音图": "tone_sandhi",
            "连读变调与协同发音图": "tone_sandhi",
            "二字组连读变调网格图": "tone_sandhi",
            "distribution": "distribution",
            "3. 声调声学特征分布雨云图": "distribution",
            "声调声学特征分布雨云图": "distribution",
            "声调分布图": "distribution",
            "雨云图 (raincloud)": "distribution",
            "density": "density",
            "时序密度图": "density",
            "tone_effect_time": "tone_effect_time",
            "二字组调类效应时间进程": "tone_effect_time",
            "overview_heatmap": "overview_heatmap",
            "声调组别概览图": "overview_heatmap",
            "词条级逐行质检热图": "overview_heatmap",
            "formant_overview_heatmap": "formant_overview_heatmap",
            "共振峰组别概览图": "formant_overview_heatmap",
            "spaghetti_qc": "spaghetti_qc",
            "2. 组内个体原始曲线叠加图": "spaghetti_qc",
            "组内个体原始曲线叠加图": "spaghetti_qc",
            "quality": "quality",
            "数据质量检查": "quality",
            "3. 检出率与发音人基频体检图": "quality",
            "3. 检出率与发音人共振峰体检图": "quality",
            "检出率与发音人基频/共振峰体检图": "quality",
            "formant_space": "formant_space",
            "1. 元音声学空间图": "formant_space",
            "元音声学空间图": "formant_space",
            "元音共振峰空间图": "formant_space",
            "formant_trajectory": "formant_trajectory",
            "2. 共振峰时序轨迹图": "formant_trajectory",
            "共振峰时序轨迹图": "formant_trajectory",
            "formant_pillai": "formant_pillai",
            "pillai_matrix": "formant_pillai",
            "vowel_merger": "formant_pillai",
            "3. 元音对立重叠度与分布图": "formant_pillai",
            "元音对立重叠度与分布图": "formant_pillai",
            "元音对立重叠度矩阵 (pillai score)": "formant_pillai",
            "formant_density": "formant_density",
            "共振峰时空密度图": "formant_density",
        }
        if text in mapping:
            res = mapping[text]
        elif low in mapping:
            res = mapping[low]
        elif "词条级逐行质检热图" in text:
            res = "formant_overview_heatmap" if self._is_formant_mode() else "overview_heatmap"
        elif "原始曲线叠加" in text or "spaghetti" in low:
            res = "spaghetti_qc"
        elif "体检" in text:
            res = "quality"
        elif "变调" in text or "sandhi" in low:
            res = "tone_sandhi"
        elif "雨云" in text or "raincloud" in low:
            res = "distribution"
        elif "pillai" in low or "重叠度" in text:
            res = "formant_pillai"
        else:
            res = text
        if res == "overview_heatmap" and self._is_formant_mode() and text == "1. 词条级逐行质检热图":
            res = "formant_overview_heatmap"
        return res

    # --- ADVANCED SCIENTIFIC PLOTTING ENGINE ---
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
            ax.text(0.5, 0.5, "没有找到有效的声调基频数据！\n请检查是否配置了发音人或导入了音频点。", ha='center', va='center', fontsize=12, color='red')
            ax.axis('off')
            return fig

        data_entries = self._prepare_entries_for_color_by(data_entries, group_key)

        page_size = 8
        pagination_state = self._get_group_pagination_state(data_entries, chart_type, color_by_val)
        total_pages = pagination_state['total_pages']
        total_groups = pagination_state['total_groups']
        unique_groups = pagination_state['unique_groups']
        is_paginated_heatmap = pagination_state['is_paginated_heatmap']

        if self.current_group_page < 0:
            self.current_group_page = 0
        elif self.current_group_page >= total_pages:
            self.current_group_page = max(0, total_pages - 1)

        P = self.current_group_page

        truncated = False
        if is_preview:
            if is_paginated_heatmap:
                truncated = True
                current_page_rows = pagination_state['pages'][P] if P < len(pagination_state['pages']) else []
                allowed_pairs = {row['row_id'] for row in current_page_rows}
                data_entries = [e for e in data_entries if (e['group'], e['label']) in allowed_pairs]
            elif self._is_overview_heatmap_chart(chart_type):
                if total_pages > 1 and total_groups > 24:
                    truncated = True
                    start_idx = P * 24
                    end_idx = min(total_groups, start_idx + 24)
                    allowed_groups = set(unique_groups[start_idx:end_idx])
                    data_entries = [e for e in data_entries if e.get(group_key, e.get('group')) in allowed_groups]
            elif chart_type in ("formant_trajectory", "tone_effect_time", "tone_sandhi", "formant_pillai"):
                pass
            elif total_groups > page_size and not self._is_overview_heatmap_chart(chart_type):
                truncated = True
                start_idx = P * page_size
                end_idx = min(total_groups, start_idx + page_size)
                allowed_groups = set(unique_groups[start_idx:end_idx])
                data_entries = [e for e in data_entries if e.get(group_key, e.get('group')) in allowed_groups]

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
            import matplotlib.text as mtext
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
        return fig

    def _adjust_outside_legend_and_chao_layout(self, fig):
        """Ensure outside right legend never overlaps the right-side Chao 5-level axis or clips at figure edge."""
        try:
            primary_axes = [
                a for a in fig.axes
                if a.get_visible()
                and not getattr(a, "_is_chao_axis", False)
                and getattr(a, "_has_chao_axis", False)
                and a.get_legend() is not None
            ]
            if not primary_axes:
                return
            chao_axes = [
                a for a in fig.axes
                if a.get_visible() and getattr(a, "_is_chao_axis", False)
            ]
            if not chao_axes:
                return

            loc_val = str(self.get_param("legend_loc", "右上") or "右上")
            outside_val = bool(self.get_param("legend_outside", True))
            if not outside_val or "右" not in loc_val:
                return

            anchor_y = 0.0 if loc_val == "右下" else 1.0
            for _step in range(3):
                fig.canvas.draw()
                renderer = fig.canvas.get_renderer()
                fig_w = float(fig.bbox.width)
                fig_x1 = float(fig.bbox.x1)
                if fig_w <= 50:
                    return

                chao_x1 = max(
                    (
                        float(ca.get_tightbbox(renderer).x1)
                        for ca in chao_axes
                        if ca.get_tightbbox(renderer) is not None
                    ),
                    default=0.0,
                )
                if chao_x1 <= 0:
                    return

                needs_redraw = False
                max_leg_x1 = 0.0
                max_leg_w = 0.0
                for ax in primary_axes:
                    leg = ax.get_legend()
                    if leg is None:
                        continue
                    ax_bbox = ax.get_window_extent(renderer)
                    leg_bbox = leg.get_window_extent(renderer)
                    max_leg_w = max(max_leg_w, float(leg_bbox.width))
                    target_leg_x0 = chao_x1 + 10.0
                    if float(leg_bbox.x0) < target_leg_x0 - 1.0 and float(ax_bbox.width) > 10.0:
                        anchor_x = max(1.16, (target_leg_x0 - float(ax_bbox.x0)) / float(ax_bbox.width))
                        leg.set_bbox_to_anchor((anchor_x, anchor_y), transform=ax.transAxes)
                        needs_redraw = True

                if needs_redraw:
                    fig.canvas.draw()
                    renderer = fig.canvas.get_renderer()
                    chao_x1 = max(
                        (
                            float(ca.get_tightbbox(renderer).x1)
                            for ca in chao_axes
                            if ca.get_tightbbox(renderer) is not None
                        ),
                        default=chao_x1,
                    )

                for ax in primary_axes:
                    leg = ax.get_legend()
                    if leg is not None:
                        lb = leg.get_window_extent(renderer)
                        max_leg_x1 = max(max_leg_x1, float(lb.x1))
                        max_leg_w = max(max_leg_w, float(lb.width))

                overflow_px = max_leg_x1 - (fig_x1 - 8.0)
                if overflow_px <= 1.0:
                    break

                curr_right = float(fig.subplotpars.right)
                curr_left = float(fig.subplotpars.left)
                shrink_frac = (overflow_px + 8.0) / fig_w
                new_right = max(curr_left + 0.28, min(curr_right - shrink_frac, 0.78))
                if abs(new_right - curr_right) < 1e-3:
                    break
                fig.subplots_adjust(right=new_right)
        except Exception:
            pass

    def _fit_contour_chao_code(self, syl_contours):
        """Fit Chao's 5-level tone code for a list of syllable mean T-value contours."""
        codes = []
        for pts in (syl_contours or []):
            arr = np.asarray(pts, dtype=float)
            valid = arr[np.isfinite(arr)]
            if valid.size == 0:
                continue
            code = fit_chao_tone_code(
                valid.tolist(),
                t_start=float(valid[0]),
                t_mid=float(valid[len(valid) // 2]),
                t_end=float(valid[-1]),
            )
            if code:
                codes.append(str(code))
        return "-".join(codes) if codes else "33"

    def _attach_chao_five_level_axis(self, ax):
        """Attach right-side Chao 5-level partition dashed lines and secondary axis."""
        for deg in (1, 2, 3, 4, 5):
            ax.axhline(float(deg), color="#9CA3AF", linestyle=":", linewidth=0.75, alpha=0.55, zorder=1.5)
        ax._has_chao_axis = True
        ax_right = ax.twinx()
        ax_right._is_chao_axis = True
        ax_right.set_ylim(-0.2, 5.2)
        ax_right.set_yticks([0.5, 1.5, 2.5, 3.5, 4.5])
        ax_right.set_yticklabels(["1度 [0-1]", "2度 [1-2]", "3度 [2-3]", "4度 [3-4]", "5度 [4-5]"], fontsize=8)
        ax_right.set_ylabel("赵元任五度标尺", fontsize=9)
        ax_right.grid(False)
        return ax_right

    def _plot_tone_contour(self, data_entries, group_key, scale):
        x_axis_val = self.get_param('contour_x', 'normalized')
        if x_axis_val in ("normalized", "归一化采样点", "归一化等距采样点"):
            x_axis = "归一化采样点"
        else:
            x_axis = "真实物理时长"

        content_val = self.get_param('error_band', None)
        if content_val is None:
            content_val = self.get_param('contour_content', '仅组别平均曲线')
        content_str = str(content_val or "")
        if content_val in ("average", "none", "仅组别平均曲线", "无误差带"):
            content = "仅组别平均曲线"
            err_label = None
        elif content_val in ("average_individual", "平均曲线 + 个体浅色细线"):
            content = "平均曲线 + 个体浅色细线"
            err_label = None
        elif "se" in content_str.lower() or "标准误" in content_str:
            content = "平均曲线 + ±1 SE (均值标准误)"
            err_label = "Mean ± 1 SE"
        elif "sd" in content_str.lower() and "ci" not in content_str.lower() and "置信" not in content_str:
            content = "平均曲线 + ±1 SD (样本标准差)"
            err_label = "Mean ± 1 SD"
        else:
            content = "平均曲线 + 95% CI (均值置信区间)"
            err_label = "Mean ± 95% CI"

        show_spaghetti = bool(self.get_param('show_spaghetti', False)) or ("个体浅色" in content)
        show_kde_bg = bool(self.get_param('show_kde_bg', False)) or ("KDE" in content_str.upper())
        chao_param = self.get_param('show_chao_five_level_axis', None)
        if chao_param is None:
            show_chao_axis = ("T 值" in scale)
        else:
            show_chao_axis = bool(chao_param) and ("T 值" in scale)

        facet_mode = self._resolve_facet_by(chart_type="contour")

        max_syls = max((len(e.get('syl_data', [])) for e in data_entries), default=1)
        num_points = self.project_tree.app_state_params.get('pts', 11)

        facet_keys = ["Default"]
        if facet_mode == "group":
            facet_keys = self._ordered_unique(e.get('group', '未分组') for e in data_entries)
        elif facet_mode == "syllable_position":
            facet_keys = [f"第 {k+1} 音节" for k in range(max_syls)]
        elif facet_mode == "speaker":
            facet_keys = self._ordered_unique(e.get('speaker_name', '默认发音人') for e in data_entries)
        elif facet_mode == "label":
            facet_keys = self._ordered_unique(e.get('label', '未命名') for e in data_entries)

        n_facets = max(1, len(facet_keys))
        n_cols = min(2, n_facets)
        n_rows = math.ceil(n_facets / n_cols)

        fig, axes = plt.subplots(n_rows, n_cols, figsize=(5.2 * n_cols + 1.8, 4.4 * n_rows + 0.6), squeeze=False, sharex=True, sharey=True)
        axes_flat = axes.flatten()
        chao_codes_map = {}

        for f_idx, f_key in enumerate(facet_keys):
            ax = axes_flat[f_idx]
            ax.set_facecolor("#FFFFFF")
            ax.grid(True, linestyle="--", alpha=0.25)
            if err_label:
                ax._error_band_label = err_label

            facet_entries = data_entries
            if facet_mode == "group":
                facet_entries = [e for e in data_entries if e.get('group') == f_key]
            elif facet_mode == "speaker":
                facet_entries = [e for e in data_entries if e.get('speaker_name') == f_key]
            elif facet_mode == "label":
                facet_entries = [e for e in data_entries if e.get('label') == f_key]

            facet_grouped = {}
            for entry in facet_entries:
                val = entry.get(group_key, entry.get('group', '未分组'))
                facet_grouped.setdefault(val, []).append(entry)

            # Optional KDE background density layer
            if show_kde_bg and facet_entries:
                kde_xs, kde_ys = [], []
                syl_indices_kde = [f_idx] if facet_mode == "syllable_position" else list(range(max_syls))
                for entry in facet_entries:
                    syl_list = entry['normalized_syl_data'] if "T 值" in scale else entry['syl_data']
                    for s_i in syl_indices_kde:
                        if s_i >= len(syl_list):
                            continue
                        _sd, pts = syl_list[s_i]
                        cy = np.asarray(pts, dtype=float)
                        gx = np.linspace(s_i * num_points + 1, (s_i + 1) * num_points, len(cy))
                        v_m = np.isfinite(cy)
                        if np.any(v_m):
                            kde_xs.extend(gx[v_m].tolist())
                            kde_ys.extend(cy[v_m].tolist())
                if len(kde_xs) >= 6 and np.std(kde_ys) > 1e-4:
                    try:
                        kx = np.asarray(kde_xs, dtype=float)
                        ky = np.asarray(kde_ys, dtype=float)
                        kde_obj = gaussian_kde(np.vstack([kx, ky]), bw_method=0.22)
                        xmin_k, xmax_k = float(np.min(kx)), float(np.max(kx))
                        ymin_k = -0.1 if "T 值" in scale else max(50.0, float(np.min(ky)) - 15.0)
                        ymax_k = 5.1 if "T 值" in scale else (float(np.max(ky)) + 15.0)
                        xi, yi = np.mgrid[xmin_k:xmax_k:60j, ymin_k:ymax_k:50j]
                        zi = kde_obj(np.vstack([xi.ravel(), yi.ravel()])).reshape(xi.shape)
                        if np.nanmax(zi) > 0:
                            ax.contourf(xi, yi, zi, levels=16, cmap="YlOrRd", alpha=0.22, zorder=1)
                    except Exception:
                        pass

            phys_boundary_xs = []
            g_color_idx = -1
            for g_color_idx, (g_name, entries) in enumerate(facet_grouped.items()):
                style = self._get_series_style(g_color_idx)
                color = style["color"]
                ls = style["linestyle"]
                marker = style["marker"]
                lw = style["linewidth"]
                ms = style["markersize"]

                syl_indices = [f_idx] if facet_mode == "syllable_position" else list(range(max_syls))
                group_acc_dur = 0.0
                syl_mean_contours_t = []
                plotted_segments = []

                for s_idx in syl_indices:
                    aligned_ys = []
                    aligned_ts = []
                    syl_durs = []

                    for entry in entries:
                        syl_list = entry['normalized_syl_data'] if "T 值" in scale else entry['syl_data']
                        t_list = entry.get('normalized_syl_data') or entry.get('syl_data', [])
                        if s_idx >= len(syl_list):
                            continue

                        s_dur, pts = syl_list[s_idx]
                        cy = np.asarray(pts, dtype=float)
                        cy = np.where(np.isfinite(cy), cy, np.nan)
                        if "Hz" in scale:
                            cy = np.where(cy > 0, cy, np.nan)
                        if s_dur > 0:
                            syl_durs.append(float(s_dur))

                        if len(cy) == num_points:
                            if np.count_nonzero(np.isfinite(cy)) >= 1:
                                aligned_ys.append(cy)
                        else:
                            valid = np.isfinite(cy)
                            if np.count_nonzero(valid) >= 2:
                                norm_src = np.linspace(0.0, 1.0, len(cy))
                                norm_dst = np.linspace(0.0, 1.0, num_points)
                                aligned_ys.append(np.interp(norm_dst, norm_src[valid], cy[valid]))

                        if s_idx < len(t_list):
                            _, t_pts = t_list[s_idx]
                            ct = np.asarray(t_pts, dtype=float)
                            if len(ct) == num_points and np.count_nonzero(np.isfinite(ct)) >= 1:
                                aligned_ts.append(ct)

                    if not aligned_ys:
                        continue

                    mean_s_dur = float(np.mean(syl_durs)) if syl_durs else 0.25
                    if x_axis == "归一化采样点":
                        grid_x = np.linspace(s_idx * num_points + 1, (s_idx + 1) * num_points, num_points)
                    else:
                        if s_idx > 0 and facet_mode != "syllable_position" and group_acc_dur > 0:
                            phys_boundary_xs.append(group_acc_dur)
                        grid_x = np.linspace(group_acc_dur, group_acc_dur + mean_s_dur, num_points)
                        group_acc_dur += mean_s_dur

                    mean_y = _safe_nanmean(aligned_ys, axis=0)
                    mean_t = _safe_nanmean(aligned_ts, axis=0) if aligned_ts else mean_y
                    syl_mean_contours_t.append(mean_t)
                    err_half = self._compute_error_band_halfwidth(aligned_ys, mode_text=content)

                    if show_spaghetti:
                        from matplotlib.collections import LineCollection
                        segments = [np.column_stack((grid_x, cy)) for cy in aligned_ys]
                        lc = LineCollection(segments, colors=color, linewidths=0.65, alpha=0.20, zorder=2)
                        ax.add_collection(lc)
                    if any(k in content for k in ("置信区间", "CI", "SE", "SD", "标准误", "标准差")):
                        ax.fill_between(grid_x, mean_y - err_half, mean_y + err_half, color=color, alpha=0.15, zorder=3)

                    plotted_segments.append((grid_x, mean_y))

                if not plotted_segments:
                    continue

                t_code = self._fit_contour_chao_code(syl_mean_contours_t) if "T 值" in scale else ""
                if t_code:
                    chao_codes_map[str(g_name)] = t_code

                short_g_name = str(g_name)
                if len(short_g_name) > 14:
                    short_g_name = short_g_name[:12] + ".."
                if show_chao_axis and t_code:
                    legend_lbl = f"{short_g_name} [{t_code}]"
                else:
                    legend_lbl = short_g_name

                for seg_i, (grid_x, mean_y) in enumerate(plotted_segments):
                    lbl = legend_lbl if seg_i == 0 else None
                    ax.plot(
                        grid_x, mean_y,
                        linestyle=ls, marker=marker, color=color,
                        linewidth=lw, markersize=ms, label=lbl, zorder=5,
                    )

                if show_chao_axis and t_code and plotted_segments and len(facet_grouped) <= 4:
                    last_gx, last_my = plotted_segments[-1]
                    finite_idx = np.where(np.isfinite(last_my))[0]
                    if len(finite_idx) > 0:
                        tail_i = int(finite_idx[-1])
                        ax.annotate(
                            f"[{t_code}]",
                            xy=(float(last_gx[tail_i]), float(last_my[tail_i])),
                            xytext=(-4, 5),
                            textcoords="offset points",
                            fontsize=8.0,
                            fontweight="bold",
                            color=color,
                            va="bottom",
                            ha="right",
                            zorder=6,
                        )

            ax.margins(x=0.06)
            title_text = "声调声学格局连贯图"
            if facet_mode in ("group", "syllable_position", "speaker", "label"):
                title_text = str(f_key)
            else:
                if len(set(e['speaker_name'] for e in data_entries)) == 1:
                    title_text = f"{data_entries[0]['speaker_name']} - 声学格局连贯图"
            if len(title_text) > 22:
                title_text = title_text[:19] + "..."
            ax.set_title(title_text, fontsize=11, fontweight="bold")
            ax._original_title = title_text

            if "T 值" in scale:
                ax.set_ylim(-0.2, 5.2)
                ax.set_yticks([0, 1, 2, 3, 4, 5])

            row_idx = f_idx // n_cols
            col_idx = f_idx % n_cols

            if col_idx == 0:
                if "T 值" in scale:
                    ax.set_ylabel("T 值 (0-5 标度)")
                else:
                    ax.set_ylabel("频率 (Hz)")
            else:
                ax.set_ylabel("")

            is_rightmost_col = (col_idx == n_cols - 1) or (f_idx == n_facets - 1)
            if show_chao_axis and is_rightmost_col:
                self._attach_chao_five_level_axis(ax)

            is_bottom_row = (row_idx == n_rows - 1) or (f_idx + n_cols >= n_facets)
            if is_bottom_row:
                if x_axis == "归一化采样点":
                    ax.set_xlabel("音节测量点 (时序展开)")
                else:
                    ax.set_xlabel("时长 Duration (s)")
            else:
                ax.set_xlabel("")

            if max_syls > 1 and facet_mode != "syllable_position":
                if x_axis == "归一化采样点":
                    for k in range(1, max_syls):
                        ax.axvline(k * num_points + 0.5, color='gray', linestyle='--', alpha=0.55)
                elif phys_boundary_xs:
                    for bx in np.unique(np.round(phys_boundary_xs, 3)):
                        ax.axvline(float(bx), color='gray', linestyle='--', alpha=0.55)

            if g_color_idx >= 0:
                legend_kwargs = self._get_legend_kwargs(ax=ax)
                legend_kwargs["fontsize"] = 8
                ax.legend(**legend_kwargs)

        for idx in range(n_facets, len(axes_flat)):
            axes_flat[idx].set_visible(False)

        fig._chao_tone_codes = chao_codes_map
        fig.tight_layout()
        if self.get_param('legend_outside', bool(show_chao_axis)):
            loc_val = self.get_param('legend_loc', '右上')
            if "右" in loc_val:
                fig.subplots_adjust(right=0.72 if show_chao_axis else 0.80)
            elif "left" in loc_val or "左" in loc_val:
                fig.subplots_adjust(left=0.18)
        return fig

    def _plot_tone_sandhi_grid(self, data_entries, group_key="group", scale="T 值"):
        """
        声调 2：连读变调与协同发音图
        支持三个子视图切换：
        - 'grid' (默认): 二字组连读变调 N1 子图分面网格图（固定前字/后字调类，叠加不同搭配条件下的双字组连读曲线+误差带+音节分界虚线+五度值）
        - 'matrix': 前后字调类组合特征矩阵图
        - 'eta2': 二字组调类效应时间进程图 (η²)
        """
        subview_raw = str(self.get_param('sandhi_subview', 'grid') or 'grid').strip().lower()
        if any(k in subview_raw for k in ("matrix", "矩阵", "热图", "combination")):
            return self._plot_tone_combination_average_heatmap(data_entries)
        if any(k in subview_raw for k in ("eta", "effect", "效应", "时间进程", "方差")):
            return self._plot_tone_effect_time(data_entries)

        num_points = int(self.project_tree.app_state_params.get("pts", 11))
        total_points = 2 * num_points
        facet_dir = str(self.get_param("sandhi_facet_by", "front") or "front").lower()
        by_back = ("back" in facet_dir or "后字" in facet_dir)

        sandhi_records = []
        for entry in data_entries:
            syl_list = entry.get("normalized_syl_data") if "T 值" in scale else entry.get("syl_data", [])
            t_list = entry.get("normalized_syl_data") or entry.get("syl_data", [])
            if not syl_list or len(syl_list) < 2:
                continue
            front, back = self._split_tone_pair(self._entry_wordlist_group(entry), entry=entry)
            if not front or not back:
                syl_chars = split_into_syllables(entry.get("label", ""))
                front = str(entry.get("group") or (syl_chars[0] if syl_chars else "前字"))
                back = syl_chars[1] if len(syl_chars) >= 2 else "后字"

            pts_s1 = np.asarray(syl_list[0][1], dtype=float)
            pts_s2 = np.asarray(syl_list[1][1], dtype=float)
            pts_s1 = self._resample_formant_track(pts_s1, num_points)
            pts_s2 = self._resample_formant_track(pts_s2, num_points)
            y_2syl = np.concatenate([pts_s1, pts_s2])

            t_s1 = self._resample_formant_track(np.asarray(t_list[0][1], dtype=float), num_points)
            t_s2 = self._resample_formant_track(np.asarray(t_list[1][1], dtype=float), num_points)

            sandhi_records.append({
                "front": str(front),
                "back": str(back),
                "y": y_2syl,
                "t_s1": t_s1,
                "t_s2": t_s2,
                "entry": entry,
            })

        if not sandhi_records:
            return self._tone_effect_blank(
                "没有找到可用的二字组连读变调数据。\n请确保所选数据包含双音节词条（如“阴平+阳平”或“11_阴阴”）。"
            )

        primary_keys = self._ordered_unique(r["back" if by_back else "front"] for r in sandhi_records)
        secondary_keys = self._ordered_unique(r["front" if by_back else "back"] for r in sandhi_records)

        n_facets = len(primary_keys)
        n_cols = min(2, max(1, n_facets))
        n_rows = math.ceil(n_facets / n_cols)

        fig, axes = plt.subplots(
            n_rows, n_cols,
            figsize=(5.4 * n_cols + 1.4, 4.2 * n_rows + 0.6),
            squeeze=False, sharex=True, sharey=True,
        )
        axes_flat = axes.flatten()
        grid_x = np.arange(1, total_points + 1, dtype=float)
        error_mode = str(self.get_param("error_band", self.get_param("contour_content", "仅组别平均曲线")))
        sandhi_summary = {}

        for f_idx, p_key in enumerate(primary_keys):
            ax = axes_flat[f_idx]
            ax.set_facecolor("#FFFFFF")
            ax.grid(True, linestyle="--", alpha=0.25)
            ax._error_band_label = "Mean ± 95% CI" if "ci" in error_mode.lower() or "置信" in error_mode else "Mean ± 1 SE"

            sub_records = [r for r in sandhi_records if (r["back"] if by_back else r["front"]) == p_key]
            for s_idx, s_key in enumerate(secondary_keys):
                cond_records = [r for r in sub_records if (r["front"] if by_back else r["back"]) == s_key]
                if not cond_records:
                    continue
                style = self._get_series_style(s_idx)
                color = style["color"]
                ls = style["linestyle"]
                marker = style["marker"]
                lw = style["linewidth"]
                ms = style["markersize"]

                mat = np.vstack([r["y"] for r in cond_records])
                mean_y = _safe_nanmean(mat, axis=0)
                err_half = self._compute_error_band_halfwidth(mat, mode_text=error_mode)

                mean_t1 = _safe_nanmean(np.vstack([r["t_s1"] for r in cond_records]), axis=0)
                mean_t2 = _safe_nanmean(np.vstack([r["t_s2"] for r in cond_records]), axis=0)
                t_code = self._fit_contour_chao_code([mean_t1, mean_t2])
                combo_name = f"{s_key}+{p_key}" if by_back else f"{p_key}+{s_key}"
                sandhi_summary[combo_name] = {
                    "front": s_key if by_back else p_key,
                    "back": p_key if by_back else s_key,
                    "count": len(cond_records),
                    "t_code": t_code,
                    "mean_contour": mean_y.tolist(),
                }

                if not any(k in error_mode for k in ("无误差带", "仅组别平均曲线", "none")):
                    ax.fill_between(grid_x, mean_y - err_half, mean_y + err_half, color=color, alpha=0.14, zorder=2)

                cond_prefix = "前字" if by_back else "后字"
                lbl = f"{cond_prefix}:{s_key} [{t_code}]"
                ax.plot(
                    grid_x, mean_y,
                    color=color, linestyle=ls, marker=marker,
                    linewidth=lw, markersize=ms * 0.85,
                    label=lbl, zorder=4,
                )

                finite_idx = np.where(np.isfinite(mean_y))[0]
                if len(finite_idx) > 0:
                    ti = int(finite_idx[-1])
                    ax.annotate(
                        f"[{t_code}]",
                        xy=(float(grid_x[ti]), float(mean_y[ti])),
                        xytext=(3, 0),
                        textcoords="offset points",
                        fontsize=7.5,
                        color=color,
                        va="center",
                    )

            # Syllable boundary dashed line between Syllable 1 and Syllable 2
            ax.axvline(num_points + 0.5, color="#475569", linestyle="--", linewidth=1.2, alpha=0.75, zorder=3)

            sub_title = f"{'后字固定' if by_back else '前字固定'}: {p_key}"
            ax.set_title(sub_title, fontsize=11, fontweight="bold")
            ax._original_title = sub_title

            if "T 值" in scale:
                ax.set_ylim(-0.2, 5.2)
                ax.set_yticks([0, 1, 2, 3, 4, 5])
                for deg in (1, 2, 3, 4, 5):
                    ax.axhline(float(deg), color="#CBD5E1", linestyle=":", linewidth=0.65, alpha=0.5)

            if f_idx % n_cols == 0:
                ax.set_ylabel("T 值 (0-5 标度)" if "T 值" in scale else "频率 (Hz)")
            if (f_idx // n_cols == n_rows - 1) or (f_idx + n_cols >= n_facets):
                ax.set_xticks([1, (num_points + 1) // 2, num_points, num_points + 1, num_points + (num_points + 1) // 2, total_points])
                ax.set_xticklabels(["前字始", "前字中", "前字末", "后字始", "后字中", "后字末"], fontsize=8)
                ax.set_xlabel("二字组连读归一化采样点 (虚线为音节分界)")

            handles, labels = ax.get_legend_handles_labels()
            if handles:
                legend_kwargs = self._get_legend_kwargs()
                legend_kwargs["fontsize"] = 7.5
                ax.legend(**legend_kwargs)

        for idx in range(n_facets, len(axes_flat)):
            axes_flat[idx].set_visible(False)

        fig._sandhi_grid_summary = sandhi_summary
        fig.suptitle("二字组连读变调与协同发音分面网格图", fontsize=13, fontweight="bold")
        fig.tight_layout()
        return fig

    def _plot_tone_overview_heatmap(self, data_entries, group_key, scale):
        metric_val = str(self.get_param('overview_metric', '') or '')
        if "调类组合" in metric_val or "combination" in metric_val.lower():
            return self._plot_tone_combination_average_heatmap(data_entries)

        dev_param = self.get_param('overview_show_deviation', None)
        if dev_param is not None:
            show_deviation = bool(dev_param)
        elif any(k in metric_val.lower() for k in ("偏差", "z-score", "zscore", "残差", "deviation")):
            show_deviation = True
        elif metric_val in ("mean", "均值热图 (Mean Map)", "sd", "标准差热图 (SD Map)"):
            show_deviation = False
        else:
            show_deviation = (self._normalize_work_mode() == "qc")

        if metric_val in ("sd", "标准差热图 (SD Map)") and not show_deviation:
            metric = "标准差热图 (SD Map)"
        else:
            metric = "均值热图 (Mean Map)"

        sort_param = self.get_param('sort_by_anomaly', None)
        if sort_param is None:
            sort_by_anomaly = bool(show_deviation)
        else:
            sort_by_anomaly = bool(sort_param)

        max_syls = max((len(e.get('syl_data', [])) for e in data_entries), default=1)
        num_points = self.project_tree.app_state_params.get('pts', 11)
        total_points = max_syls * num_points

        # In QC mode, if group_key is default 'group' and multiple items exist per group, allow item-level inspection when sort_by_anomaly or label mode is used
        grouped_data = {}
        for entry in data_entries:
            if group_key == 'label':
                val = (entry['group'], entry['label'], entry.get('speaker_name', ''), entry.get('item_id', ''))
            else:
                val = entry.get(group_key, entry.get('group'))
            grouped_data.setdefault(val, []).append(entry)

        if group_key == 'label':
            groups_sorted = list(grouped_data.keys())
        else:
            if group_key == 'group':
                self._ensure_available_groups()
                group_order = self.available_groups
            else:
                group_order = self._ordered_unique(entry.get(group_key, entry.get('group')) for entry in data_entries)
            row_order = {value: idx for idx, value in enumerate(group_order)}
            groups_sorted = sorted(list(grouped_data.keys()), key=lambda value: row_order.get(value, 999999))

        if not groups_sorted:
            fig, ax = plt.subplots(figsize=(6, 4))
            ax.text(0.5, 0.5, "没有找到有效的声调数据用于生成概览图", ha='center', va='center')
            return fig

        # Pre-calculate group-level mean and SD contours for deviation / Z-score calculation
        tone_vectors_by_group = {}
        for entry in data_entries:
            syl_list = entry['normalized_syl_data'] if "T 值" in scale else entry['syl_data']
            y_flat = []
            for _s_dur, pts in syl_list:
                y_flat.extend(pts)
            if len(y_flat) < total_points:
                y_flat.extend([np.nan] * (total_points - len(y_flat)))
            elif len(y_flat) > total_points:
                y_flat = y_flat[:total_points]
            arr = np.asarray(y_flat, dtype=float)
            arr = np.where(np.isfinite(arr), arr, np.nan)
            tone_vectors_by_group.setdefault(entry['group'], []).append(arr)

        tone_averages = {}
        tone_stds = {}
        min_std_floor = 0.15 if "T 值" in scale else 5.0
        for tg, vecs in tone_vectors_by_group.items():
            mat_g = np.vstack(vecs)
            tone_averages[tg] = _safe_nanmean(mat_g, axis=0, default=np.nan)
            sd_g = _safe_nanstd(mat_g, axis=0, default=min_std_floor)
            tone_stds[tg] = np.where(np.isfinite(sd_g) & (sd_g > min_std_floor), sd_g, min_std_floor)

        row_records = []
        for g_key in groups_sorted:
            entries = grouped_data[g_key]
            if group_key == 'label':
                tg, label_name, spk_name, item_id = g_key
            else:
                tg = entries[0]['group']
                label_name = str(g_key)
                spk_name = ", ".join(self._ordered_unique(e.get('speaker_name', '') for e in entries if e.get('speaker_name')))
                item_id = str(entries[0].get('item_id', ''))

            raw_vecs = []
            dev_vecs = []
            z_vecs = []
            base_mean = tone_averages.get(tg, np.full(total_points, np.nan))
            base_std = tone_stds.get(tg, np.full(total_points, min_std_floor))

            for entry in entries:
                syl_list = entry['normalized_syl_data'] if "T 值" in scale else entry['syl_data']
                y_flat = []
                for _s_dur, pts in syl_list:
                    y_flat.extend(pts)
                if len(y_flat) < total_points:
                    y_flat.extend([np.nan] * (total_points - len(y_flat)))
                elif len(y_flat) > total_points:
                    y_flat = y_flat[:total_points]
                y_arr = np.asarray(y_flat, dtype=float)
                y_arr = np.where(np.isfinite(y_arr), y_arr, np.nan)
                dev_arr = y_arr - base_mean
                z_arr = dev_arr / base_std
                raw_vecs.append(y_arr)
                dev_vecs.append(dev_arr)
                z_vecs.append(z_arr)

            raw_mat = np.vstack(raw_vecs)
            dev_mat = np.vstack(dev_vecs)
            z_mat = np.vstack(z_vecs)

            raw_row = _safe_nanmean(raw_mat, axis=0, default=np.nan)
            dev_row = _safe_nanmean(dev_mat, axis=0, default=np.nan)
            z_row = _safe_nanmean(z_mat, axis=0, default=np.nan)

            if show_deviation:
                plot_row = dev_row
            elif "均值" in metric:
                plot_row = raw_row
            else:
                plot_row = _safe_nanstd(raw_mat, axis=0, default=np.nan)

            finite_dev = dev_row[np.isfinite(dev_row)]
            rmsd = float(np.sqrt(np.mean(finite_dev ** 2))) if finite_dev.size > 0 else 0.0
            max_abs_z = float(_safe_nanmax(np.abs(z_row), default=0.0))
            if not np.isfinite(max_abs_z):
                max_abs_z = 0.0
            has_warning = any(bool(e.get('warnings')) for e in entries)
            rmsd_thresh = 0.65 if "T 值" in scale else 20.0
            is_anomaly = bool(max_abs_z >= 1.96 or rmsd >= rmsd_thresh or has_warning)

            row_records.append({
                "group": tg,
                "label": label_name,
                "speaker_name": spk_name,
                "item_id": item_id,
                "count": len(entries),
                "plot_row": plot_row,
                "raw_row": raw_row,
                "dev_row": dev_row,
                "z_row": z_row,
                "rmsd": rmsd,
                "max_abs_z": max_abs_z,
                "is_anomaly": is_anomaly,
            })

        if sort_by_anomaly and row_records:
            row_records.sort(key=lambda r: (r["rmsd"], r["max_abs_z"]), reverse=True)
            if not any(r["is_anomaly"] for r in row_records) and row_records[0]["rmsd"] > 0:
                row_records[0]["is_anomaly"] = True

        matrix = []
        y_ticks = []
        y_labels = []
        hover_metadata = []
        last_tg = None
        current_row_idx = 0

        for rec in row_records:
            tg = rec["group"]
            if group_key == 'label' and not sort_by_anomaly:
                if last_tg is not None and tg != last_tg:
                    matrix.append(np.full(total_points, np.nan))
                    current_row_idx += 1
                last_tg = tg

            matrix.append(rec["plot_row"])
            y_ticks.append(current_row_idx)
            prefix = "[!] " if (rec["is_anomaly"] and (show_deviation or sort_by_anomaly)) else ""
            base_lbl = f"{rec['label']} (N={rec['count']})"
            y_lbl = f"{prefix}{base_lbl}"
            y_labels.append(y_lbl)

            hover_metadata.append({
                "row_index": current_row_idx,
                "label": rec["label"],
                "group": rec["group"],
                "speaker_name": rec["speaker_name"],
                "item_id": rec["item_id"],
                "values": rec["raw_row"].tolist(),
                "deviations": rec["dev_row"].tolist(),
                "z_scores": rec["z_row"].tolist(),
                "rmsd": rec["rmsd"],
                "max_abs_z": rec["max_abs_z"],
                "is_anomaly": rec["is_anomaly"],
                "y_label": y_lbl,
            })
            current_row_idx += 1

        matrix = np.asarray(matrix, dtype=float)

        fig_height = max(4.0, current_row_idx * 0.35 + 1.5)
        fig, ax = plt.subplots(figsize=(8.2, fig_height))
        ax._is_matrix_heatmap = True

        if show_deviation:
            cmap = 'RdBu_r'
            max_abs = _safe_nanmax(np.abs(matrix), default=1.0) if len(matrix) > 0 else 1.0
            if np.isnan(max_abs) or max_abs <= 0:
                max_abs = 1.0
            vmin = -max_abs
            vmax = max_abs
        else:
            if "均值" in metric:
                cmap = 'RdYlBu_r' if "T 值" in scale else 'viridis'
                vmin = 0.0 if "T 值" in scale else None
                vmax = 5.0 if "T 值" in scale else None
            else:
                cmap = 'Reds'
                vmin = 0.0
                vmax = None

        try:
            current_cmap = plt.colormaps.get_cmap(cmap).copy()
        except AttributeError:
            current_cmap = plt.cm.get_cmap(cmap).copy()
        current_cmap.set_bad(color='#F1F5F9', alpha=0.0)

        im = ax.imshow(matrix, cmap=current_cmap, aspect='auto', vmin=vmin, vmax=vmax)

        cbar = fig.colorbar(im, ax=ax, pad=0.02)
        if hasattr(cbar, "ax"):
            cbar.ax._is_colorbar_axis = True
        if show_deviation:
            cbar.set_label("与组均值的偏差 (T 值)" if "T 值" in scale else "与组均值的偏差 (Hz)")
        elif "均值" in metric:
            cbar.set_label("平均 T 值" if "T 值" in scale else "平均基频 (Hz)")
        else:
            cbar.set_label("标准差 (SD)" if "T 值" in scale else "标准差 (Hz)")

        ax.set_yticks(y_ticks)
        ax.set_yticklabels(y_labels, fontsize=9)

        ax.set_xticks(np.arange(total_points))
        x_labels = []
        for s_idx in range(max_syls):
            for p_idx in range(num_points):
                if p_idx == 0 or p_idx == num_points - 1 or p_idx == num_points // 2:
                    x_labels.append(f"音节{s_idx+1}_点{p_idx+1}")
                else:
                    x_labels.append("")
        ax.set_xticklabels(x_labels, rotation=45, ha='right', fontsize=8)

        if max_syls > 1:
            for k in range(1, max_syls):
                ax.axvline(k * num_points - 0.5, color='white', linestyle='--', linewidth=1.5, alpha=0.8)

        title_text = f"声调组别概览图 - {'偏差质检热图' if show_deviation else metric}"
        unique_entry_groups = set(e['group'] for e in data_entries)
        if len(unique_entry_groups) == 1:
            title_text += f" (组别: {list(unique_entry_groups)[0]})"
        if len(set(e['speaker_name'] for e in data_entries)) == 1:
            title_text = f"{data_entries[0]['speaker_name']} - {title_text}"
        ax.set_title(title_text, fontsize=12, fontweight="bold", pad=15)
        ax._original_title = title_text

        fig._qc_hover_metadata = hover_metadata
        fig.tight_layout()
        return fig

    def _plot_tone_combination_average_heatmap(self, data_entries):
        rows = self._collect_tone_effect_time_rows(data_entries)
        if not rows:
            return self._tone_effect_blank(
                "没有可用于调类组合热图的二字组数据。\n请确认当前选择的组别来自二字组，并且组名形如“前字调类+后字调类”。"
            )
        if not self._has_tone_effect_time_data(data_entries):
            return self._tone_effect_blank(
                "调类组合热图数据不足。\n至少需要 2 个前字调类、2 个后字调类，以及 4 个可用调类组合。"
            )

        tones = []
        for row in rows:
            for tone in (row["front"], row["back"]):
                if tone not in tones:
                    tones.append(tone)
        if not tones:
            return self._tone_effect_blank("没有可识别的前后调类。")

        grouped = {}
        for row in rows:
            key = (row["front"], row["back"], row["syl"])
            grouped.setdefault(key, []).append(row["value"])

        tone_index = {tone: index for index, tone in enumerate(tones)}
        front_matrix = np.full((len(tones), len(tones)), np.nan)
        back_matrix = np.full((len(tones), len(tones)), np.nan)
        for key, values in grouped.items():
            front, back, syl = key
            if front not in tone_index or back not in tone_index:
                continue
            matrix = front_matrix if syl == 1 else back_matrix
            matrix[tone_index[front], tone_index[back]] = float(sum(values) / len(values))

        fig, axes = plt.subplots(1, 2, figsize=(14.5, 6.0))
        try:
            cmap = plt.colormaps.get_cmap("RdYlBu_r").copy()
        except AttributeError:
            cmap = plt.cm.get_cmap("RdYlBu_r").copy()
        cmap.set_bad(color="white", alpha=0.0)

        for axis, matrix, title in [
            (axes[0], front_matrix, "前字平均五度值"),
            (axes[1], back_matrix, "后字平均五度值"),
        ]:
            axis._is_matrix_heatmap = True
            im = axis.imshow(matrix, vmin=0.0, vmax=5.0, cmap=cmap)
            axis.set_xticks(range(len(tones)))
            axis.set_yticks(range(len(tones)))
            axis.set_xticklabels(tones)
            axis.set_yticklabels(tones)
            axis.set_xlabel("后字调类")
            axis.set_ylabel("前字调类")
            axis.set_title(title, fontsize=12, fontweight="bold")
            axis._original_title = title
            for row_idx in range(matrix.shape[0]):
                for col_idx in range(matrix.shape[1]):
                    value = matrix[row_idx, col_idx]
                    if np.isfinite(value):
                        axis.text(col_idx, row_idx, f"{value:.2f}", ha="center", va="center", fontsize=9)
            cbar = fig.colorbar(im, ax=axis, fraction=0.046, pad=0.04)
            if hasattr(cbar, "ax"):
                cbar.ax._is_colorbar_axis = True
            cbar.set_label("平均五度值")

        fig.suptitle("调类组合的前后字平均五度值热图", fontsize=14, fontweight="bold")
        fig.tight_layout()
        return fig

    def _resample_formant_track(self, values, target_len):
        arr = np.asarray(values, dtype=float)
        if target_len <= 0:
            return np.array([], dtype=float)
        if arr.size == 0:
            return np.full(target_len, np.nan, dtype=float)
        if arr.size == target_len:
            return arr

        src_x = np.linspace(0.0, 1.0, arr.size)
        dst_x = np.linspace(0.0, 1.0, target_len)
        finite_mask = np.isfinite(arr)
        if np.count_nonzero(finite_mask) == 0:
            return np.full(target_len, np.nan, dtype=float)
        if np.count_nonzero(finite_mask) == 1:
            only_val = float(arr[finite_mask][0])
            return np.full(target_len, only_val, dtype=float)

        src_x_valid = src_x[finite_mask]
        arr_valid = arr[finite_mask]
        return np.interp(dst_x, src_x_valid, arr_valid)

    def _plot_formant_overview_heatmap(self, data_entries, group_key, scale):
        metric_val = str(self.get_param('overview_metric', '') or '')
        dev_param = self.get_param('overview_show_deviation', None)
        if dev_param is not None:
            show_deviation = bool(dev_param)
        elif any(k in metric_val.lower() for k in ("偏差", "z-score", "zscore", "残差", "deviation")):
            show_deviation = True
        elif metric_val in ("mean", "均值热图 (Mean Map)", "sd", "标准差热图 (SD Map)"):
            show_deviation = False
        else:
            show_deviation = (self._normalize_work_mode() == "qc")

        if metric_val in ("sd", "标准差热图 (SD Map)") and not show_deviation:
            metric = "标准差热图 (SD Map)"
        else:
            metric = "均值热图 (Mean Map)"

        sort_param = self.get_param('sort_by_anomaly', None)
        sort_by_anomaly = bool(show_deviation) if sort_param is None else bool(sort_param)

        overview_mode = str(self.get_param('formant_overview_mode', 'F1 & F2 双轨'))
        is_ratio_mode = ("比值" in overview_mode) or ("ratio" in overview_mode.lower())

        max_syls = max((len(e.get('syl_formants', [])) for e in data_entries), default=0)
        num_points = int(self.project_tree.app_state_params.get('pts', 11))
        total_points = max_syls * num_points
        if total_points <= 0:
            fig, ax = plt.subplots(figsize=(6, 4))
            ax.text(0.5, 0.5, "没有找到有效的共振峰采样点用于生成概览图", ha='center', va='center')
            return fig

        grouped_data = {}
        for entry in data_entries:
            val = (entry['group'], entry['label'], entry.get('speaker_name', ''), entry.get('item_id', '')) if group_key == 'label' else entry.get(group_key, entry.get('group'))
            grouped_data.setdefault(val, []).append(entry)

        if group_key == 'label':
            groups_sorted = list(grouped_data.keys())
        else:
            if group_key == 'group':
                self._ensure_available_groups()
                group_order = self.available_groups
            else:
                group_order = self._ordered_unique(entry.get(group_key, entry.get('group')) for entry in data_entries)
            row_order = {value: idx for idx, value in enumerate(group_order)}
            groups_sorted = sorted(list(grouped_data.keys()), key=lambda value: row_order.get(value, 999999))

        if not groups_sorted:
            fig, ax = plt.subplots(figsize=(6, 4))
            ax.text(0.5, 0.5, "没有找到有效的共振峰数据用于生成概览图", ha='center', va='center')
            return fig

        normalization_mode = self.get_param('formant_normalization', '原始频率 (Hz)')
        transform_fn = self._build_formant_normalizer(data_entries, normalization_mode)

        def _extract_entry_tracks(entry):
            f1_flat, f2_flat, ratio_flat = [], [], []
            syl_formants = entry.get('syl_formants', [])
            for syl_idx in range(max_syls):
                if syl_idx < len(syl_formants):
                    syl = syl_formants[syl_idx]
                    f1_track = self._resample_formant_track(syl.get('f1', []), num_points)
                    f2_track = self._resample_formant_track(syl.get('f2', []), num_points)
                else:
                    f1_track = np.full(num_points, np.nan)
                    f2_track = np.full(num_points, np.nan)

                t_f1, t_f2 = transform_fn(f1_track, f2_track, speaker_name=entry.get('speaker_name'))
                t_f1 = np.where(np.isfinite(t_f1), t_f1, np.nan)
                t_f2 = np.where(np.isfinite(t_f2), t_f2, np.nan)

                with np.errstate(divide='ignore', invalid='ignore'):
                    ratio_track = np.where((f1_track > 1e-6) & np.isfinite(f1_track) & np.isfinite(f2_track), f2_track / f1_track, np.nan)

                f1_flat.extend(t_f1.tolist())
                f2_flat.extend(t_f2.tolist())
                ratio_flat.extend(ratio_track.tolist())
            return (
                np.asarray(f1_flat[:total_points], dtype=float),
                np.asarray(f2_flat[:total_points], dtype=float),
                np.asarray(ratio_flat[:total_points], dtype=float),
            )

        # Precompute per-vowel-group reference median/mean and SD for Z-score deviation
        grp_pool = {}
        for entry in data_entries:
            ef1, ef2, eratio = _extract_entry_tracks(entry)
            grp_pool.setdefault(entry['group'], {"f1": [], "f2": [], "ratio": []})
            grp_pool[entry['group']]["f1"].append(ef1)
            grp_pool[entry['group']]["f2"].append(ef2)
            grp_pool[entry['group']]["ratio"].append(eratio)

        grp_ref = {}
        is_z_norm = any(k in normalization_mode for k in ("Lobanov", "归一化", "Bark", "ERB"))
        std_floor = 0.20 if is_z_norm else 25.0
        for tg, pools in grp_pool.items():
            m_f1 = _safe_nanmean(np.vstack(pools["f1"]), axis=0, default=np.nan)
            s_f1 = _safe_nanstd(np.vstack(pools["f1"]), axis=0, default=std_floor)
            s_f1 = np.where(np.isfinite(s_f1) & (s_f1 > std_floor), s_f1, std_floor)
            m_f2 = _safe_nanmean(np.vstack(pools["f2"]), axis=0, default=np.nan)
            s_f2 = _safe_nanstd(np.vstack(pools["f2"]), axis=0, default=std_floor)
            s_f2 = np.where(np.isfinite(s_f2) & (s_f2 > std_floor), s_f2, std_floor)
            m_rt = _safe_nanmean(np.vstack(pools["ratio"]), axis=0, default=np.nan)
            s_rt = _safe_nanstd(np.vstack(pools["ratio"]), axis=0, default=0.15)
            s_rt = np.where(np.isfinite(s_rt) & (s_rt > 0.15), s_rt, 0.15)
            grp_ref[tg] = (m_f1, s_f1, m_f2, s_f2, m_rt, s_rt)

        row_records = []
        for g_name in groups_sorted:
            entries = grouped_data[g_name]
            if group_key == 'label':
                tg, label_name, spk_name, item_id = g_name
            else:
                tg = entries[0]['group']
                label_name = str(g_name)
                spk_name = ", ".join(self._ordered_unique(e.get('speaker_name', '') for e in entries if e.get('speaker_name')))
                item_id = str(entries[0].get('item_id', ''))

            vectors_f1, vectors_f2, vectors_ratio = [], [], []
            for entry in entries:
                ef1, ef2, eratio = _extract_entry_tracks(entry)
                vectors_f1.append(ef1)
                vectors_f2.append(ef2)
                vectors_ratio.append(eratio)

            vectors_f1 = np.vstack(vectors_f1)
            vectors_f2 = np.vstack(vectors_f2)
            vectors_ratio = np.vstack(vectors_ratio)

            raw_f1 = _safe_nanmean(vectors_f1, axis=0)
            raw_f2 = _safe_nanmean(vectors_f2, axis=0)
            raw_ratio = _safe_nanmean(vectors_ratio, axis=0)

            m_f1, s_f1, m_f2, s_f2, m_rt, s_rt = grp_ref.get(
                tg,
                (np.full(total_points, np.nan), np.full(total_points, std_floor),
                 np.full(total_points, np.nan), np.full(total_points, std_floor),
                 np.full(total_points, np.nan), np.full(total_points, 0.15)),
            )
            z_f1 = (raw_f1 - m_f1) / s_f1
            z_f2 = (raw_f2 - m_f2) / s_f2
            z_rt = (raw_ratio - m_rt) / s_rt

            if show_deviation:
                row_f1, row_f2, row_ratio = z_f1, z_f2, z_rt
            elif "均值" in metric:
                row_f1, row_f2, row_ratio = raw_f1, raw_f2, raw_ratio
            else:
                row_f1 = _safe_nanstd(vectors_f1, axis=0)
                row_f2 = _safe_nanstd(vectors_f2, axis=0)
                row_ratio = _safe_nanstd(vectors_ratio, axis=0)

            comb_z = np.concatenate([z_f1[np.isfinite(z_f1)], z_f2[np.isfinite(z_f2)]])
            rmsd = float(np.sqrt(np.mean(comb_z ** 2))) if comb_z.size > 0 else 0.0
            max_abs_z = float(np.max(np.abs(comb_z))) if comb_z.size > 0 else 0.0
            is_anomaly = bool(max_abs_z >= 1.96 or rmsd >= 1.25 or any(bool(e.get('warnings')) for e in entries))

            row_records.append({
                "group": tg,
                "label": label_name,
                "speaker_name": spk_name,
                "item_id": item_id,
                "count": len(entries),
                "row_f1": row_f1,
                "row_f2": row_f2,
                "row_ratio": row_ratio,
                "raw_f1": raw_f1,
                "raw_f2": raw_f2,
                "z_f1": z_f1,
                "z_f2": z_f2,
                "rmsd": rmsd,
                "max_abs_z": max_abs_z,
                "is_anomaly": is_anomaly,
            })

        if sort_by_anomaly and row_records:
            row_records.sort(key=lambda r: (r["rmsd"], r["max_abs_z"]), reverse=True)
            if not any(r["is_anomaly"] for r in row_records) and row_records[0]["rmsd"] > 0:
                row_records[0]["is_anomaly"] = True

        matrix_f1, matrix_f2, matrix_ratio = [], [], []
        y_ticks, y_labels, hover_metadata = [], [], []
        last_tg = None
        current_row_idx = 0

        for rec in row_records:
            tg = rec["group"]
            if group_key == 'label' and not sort_by_anomaly:
                if last_tg is not None and tg != last_tg:
                    blank = np.full(total_points, np.nan)
                    matrix_f1.append(blank.copy())
                    matrix_f2.append(blank.copy())
                    matrix_ratio.append(blank.copy())
                    current_row_idx += 1
                last_tg = tg

            matrix_f1.append(rec["row_f1"])
            matrix_f2.append(rec["row_f2"])
            matrix_ratio.append(rec["row_ratio"])
            y_ticks.append(current_row_idx)
            prefix = "[!] " if (rec["is_anomaly"] and (show_deviation or sort_by_anomaly)) else ""
            y_lbl = f"{prefix}{rec['label']} (N={rec['count']})"
            y_labels.append(y_lbl)
            hover_metadata.append({
                "row_index": current_row_idx,
                "label": rec["label"],
                "group": rec["group"],
                "speaker_name": rec["speaker_name"],
                "item_id": rec["item_id"],
                "values": rec["raw_f1"].tolist(),
                "f1_values": rec["raw_f1"].tolist(),
                "f2_values": rec["raw_f2"].tolist(),
                "z_scores": rec["z_f1"].tolist(),
                "f1_z_scores": rec["z_f1"].tolist(),
                "f2_z_scores": rec["z_f2"].tolist(),
                "rmsd": rec["rmsd"],
                "max_abs_z": rec["max_abs_z"],
                "is_anomaly": rec["is_anomaly"],
                "y_label": y_lbl,
            })
            current_row_idx += 1

        matrix_f1 = np.asarray(matrix_f1, dtype=float)
        matrix_f2 = np.asarray(matrix_f2, dtype=float)
        matrix_ratio = np.asarray(matrix_ratio, dtype=float)

        fig_height = max(4.0, current_row_idx * 0.35 + 1.5)

        if "Lobanov" in normalization_mode or "归一化" in normalization_mode or "L-归一化" in normalization_mode:
            unit_text = "Z-score"
        elif "Bark" in normalization_mode:
            unit_text = "Bark"
        elif "ERB" in normalization_mode:
            unit_text = "ERB"
        else:
            unit_text = "Hz"

        if show_deviation:
            cmap_main = 'RdBu_r'
            ratio_cmap = 'RdBu_r'
            max_a1 = max(_safe_nanmax(np.abs(matrix_f1), default=1.0), _safe_nanmax(np.abs(matrix_f2), default=1.0))
            if not np.isfinite(max_a1) or max_a1 <= 0:
                max_a1 = 1.0
            vmin_main, vmax_main = -max_a1, max_a1
            max_ar = _safe_nanmax(np.abs(matrix_ratio), default=1.0)
            if not np.isfinite(max_ar) or max_ar <= 0:
                max_ar = 1.0
            vmin_ratio, vmax_ratio = -max_ar, max_ar
        elif "均值" in metric:
            cmap_main = 'viridis'
            ratio_cmap = 'viridis'
            vmin_main = None
            vmax_main = None
            vmin_ratio = None
            vmax_ratio = None
        else:
            cmap_main = 'Reds'
            ratio_cmap = 'Reds'
            vmin_main = 0.0
            vmax_main = None
            vmin_ratio = 0.0
            vmax_ratio = None

        def _copy_cmap(name):
            try:
                return plt.colormaps.get_cmap(name).copy()
            except AttributeError:
                return plt.cm.get_cmap(name).copy()

        if is_ratio_mode:
            fig, ax = plt.subplots(figsize=(8.6, fig_height))
            ax._is_matrix_heatmap = True
            cmap = _copy_cmap(ratio_cmap)
            cmap.set_bad(color='#F1F5F9', alpha=0.0)
            im = ax.imshow(matrix_ratio, cmap=cmap, aspect='auto', vmin=vmin_ratio, vmax=vmax_ratio)
            cbar = fig.colorbar(im, ax=ax, pad=0.02)
            if hasattr(cbar, "ax"):
                cbar.ax._is_colorbar_axis = True
            if show_deviation:
                cbar.set_label("F2/F1 与组均值偏差 (Z-score)")
            elif "均值" in metric:
                cbar.set_label("平均 F2/F1 比值")
            else:
                cbar.set_label("F2/F1 比值标准差 (SD)")
            axes_to_label = [ax]
        else:
            fig, axes = plt.subplots(2, 1, figsize=(8.6, max(5.6, fig_height + 1.2)), sharex=True)
            ax_f1, ax_f2 = axes
            ax_f1._is_matrix_heatmap = True
            ax_f2._is_matrix_heatmap = True

            cmap_f1 = _copy_cmap(cmap_main)
            cmap_f2 = _copy_cmap(cmap_main)
            cmap_f1.set_bad(color='#F1F5F9', alpha=0.0)
            cmap_f2.set_bad(color='#F1F5F9', alpha=0.0)

            im1 = ax_f1.imshow(matrix_f1, cmap=cmap_f1, aspect='auto', vmin=vmin_main, vmax=vmax_main)
            im2 = ax_f2.imshow(matrix_f2, cmap=cmap_f2, aspect='auto', vmin=vmin_main, vmax=vmax_main)
            cb1 = fig.colorbar(im1, ax=ax_f1, pad=0.02)
            cb2 = fig.colorbar(im2, ax=ax_f2, pad=0.02)
            if hasattr(cb1, "ax"):
                cb1.ax._is_colorbar_axis = True
            if hasattr(cb2, "ax"):
                cb2.ax._is_colorbar_axis = True
            if show_deviation:
                cb1.set_label("F1 偏差 (Z-score)")
                cb2.set_label("F2 偏差 (Z-score)")
            elif "均值" in metric:
                cb1.set_label(f"平均 F1 ({unit_text})")
                cb2.set_label(f"平均 F2 ({unit_text})")
            else:
                cb1.set_label(f"F1 标准差 ({unit_text})")
                cb2.set_label(f"F2 标准差 ({unit_text})")
            t1 = f"F1 轨迹热图 - {'偏差 Z-score' if show_deviation else metric}"
            t2 = f"F2 轨迹热图 - {'偏差 Z-score' if show_deviation else metric}"
            ax_f1.set_title(t1, fontsize=11, fontweight="bold", pad=10)
            ax_f2.set_title(t2, fontsize=11, fontweight="bold", pad=10)
            ax_f1._original_title = t1
            ax_f2._original_title = t2
            axes_to_label = [ax_f1, ax_f2]

        for ax in axes_to_label:
            ax.set_yticks(y_ticks)
            ax.set_yticklabels(y_labels, fontsize=9)
            if max_syls > 1:
                for k in range(1, max_syls):
                    ax.axvline(k * num_points - 0.5, color='white', linestyle='--', linewidth=1.3, alpha=0.8)

        x_ticks = np.arange(total_points)
        x_labels = []
        for s_idx in range(max_syls):
            for p_idx in range(num_points):
                if p_idx == 0 or p_idx == num_points // 2 or p_idx == num_points - 1:
                    x_labels.append(f"音节{s_idx + 1}_点{p_idx + 1}")
                else:
                    x_labels.append("")
        axes_to_label[-1].set_xticks(x_ticks)
        axes_to_label[-1].set_xticklabels(x_labels, rotation=45, ha='right', fontsize=8)
        if len(axes_to_label) > 1:
            axes_to_label[0].tick_params(axis='x', labelbottom=False)

        title_mode_text = "F2/F1 比值单轨" if is_ratio_mode else "F1 & F2 双轨"
        title_text = f"共振峰组别概览图 - {title_mode_text} - {'偏差质检热图' if show_deviation else metric}"
        unique_entry_groups = set(e['group'] for e in data_entries)
        if len(unique_entry_groups) == 1:
            title_text += f" (组别: {list(unique_entry_groups)[0]})"
        if len(set(e['speaker_name'] for e in data_entries)) == 1:
            title_text = f"{data_entries[0]['speaker_name']} - {title_text}"
        fig.suptitle(title_text, fontsize=12, fontweight="bold", y=0.995)

        fig._qc_hover_metadata = hover_metadata
        fig.tight_layout(rect=[0, 0, 1, 0.98])
        return fig

    def _extract_tone_entry_metrics(self, entry, scale="T 值"):
        """
        提取声调词条的 8 项核心语言学声学指标：
        mean (全调均值), onset (起点值), mid (中点值), offset (终点值),
        inflection (拐点极值), range (调域跨度), slope (线性斜率), duration_ms (音节时长 ms)
        """
        syl_list = entry.get('normalized_syl_data') if "T 值" in scale else entry.get('syl_data', [])
        y_flat = []
        dur_sum = 0.0
        for s_dur, pts in (syl_list or []):
            dur_sum += float(s_dur or 0.0)
            y_flat.extend(pts)
        if dur_sum <= 0:
            dur_sum = float(entry.get('total_dur', 0.0) or 0.0)

        arr = np.asarray(y_flat, dtype=float)
        valid_idx = np.where(np.isfinite(arr))[0]
        if valid_idx.size == 0:
            return None

        v = arr[valid_idx]
        onset = float(v[0])
        mid = float(v[len(v) // 2])
        offset = float(v[-1])
        mean_val = float(np.mean(v))
        min_val = float(np.min(v))
        max_val = float(np.max(v))
        range_val = float(max_val - min_val)

        # Inflection point extremum: interior point with maximum perpendicular/vertical deviation from onset->offset chord
        if len(v) >= 3:
            chord = np.linspace(onset, offset, len(v))
            dev = np.abs(v - chord)
            inner_dev = dev[1:-1]
            if inner_dev.size > 0 and np.max(inner_dev) > 1e-4:
                turn_idx = int(np.argmax(inner_dev)) + 1
                inflection_val = float(v[turn_idx])
            else:
                inflection_val = mid
        else:
            inflection_val = mid

        # Slope across normalized time [0, 1] (or per second if duration > 0)
        if len(v) >= 2:
            norm_t = (valid_idx - valid_idx[0]) / max(1.0, float(len(arr) - 1))
            if np.std(norm_t) > 1e-6:
                slope_val = float(np.polyfit(norm_t, v, 1)[0])
            else:
                slope_val = float(offset - onset)
        else:
            slope_val = 0.0

        return {
            "mean": mean_val,
            "onset": onset,
            "mid": mid,
            "offset": offset,
            "inflection": inflection_val,
            "range": range_val,
            "slope": slope_val,
            "duration_ms": float(dur_sum * 1000.0),
        }

    def _draw_raincloud_series(self, ax, data_list, positions, colors, width=0.36):
        """
        在指定坐标轴上绘制现代雨云图（Raincloud Plot）：
        1) 右侧半小提琴核密度云 (Half-Violin KDE)
        2) 中央窄箱线图 (Narrow Boxplot)
        3) 左侧个体抖动散点 (Individual Jittered Scatter)
        """
        for idx, (vals, pos, color) in enumerate(zip(data_list, positions, colors)):
            arr = np.asarray(vals, dtype=float)
            arr = arr[np.isfinite(arr)]
            if arr.size == 0:
                continue

            # 1. Half-Violin KDE on the right side [pos + 0.02, pos + width]
            if arr.size >= 2 and np.std(arr) > 1e-5:
                try:
                    kde = gaussian_kde(arr, bw_method=0.32)
                    pad = (np.max(arr) - np.min(arr)) * 0.12
                    y_grid = np.linspace(np.min(arr) - pad, np.max(arr) + pad, 80)
                    dens = kde(y_grid)
                    max_d = float(np.max(dens))
                    if max_d > 0:
                        dens_scaled = (dens / max_d) * width
                        ax.fill_betweenx(
                            y_grid,
                            pos + 0.02,
                            pos + 0.02 + dens_scaled,
                            facecolor=color,
                            edgecolor=color,
                            alpha=0.52,
                            linewidth=1.2,
                            zorder=3,
                        )
                except Exception:
                    pass

            # 2. Narrow Boxplot at center `pos`
            bp = ax.boxplot(
                [arr],
                positions=[pos],
                widths=0.12,
                patch_artist=True,
                showfliers=False,
                zorder=4,
            )
            for box in bp["boxes"]:
                box.set_facecolor("#FFFFFF")
                box.set_edgecolor(color)
                box.set_linewidth(1.3)
                box.set_alpha(0.92)
            for whisker in bp["whiskers"]:
                whisker.set_color(color)
                whisker.set_linewidth(1.1)
            for cap in bp["caps"]:
                cap.set_color(color)
                cap.set_linewidth(1.1)
            for median in bp["medians"]:
                median.set_color("#111827")
                median.set_linewidth(1.6)

            # 3. Individual jittered scatter points on the left side [pos - 0.22, pos - 0.06]
            rng = np.random.default_rng(1000 + idx)
            jitter = rng.uniform(-0.22, -0.06, size=arr.size)
            ax.scatter(
                pos + jitter,
                arr,
                color=color,
                alpha=0.68,
                s=22,
                edgecolors="white",
                linewidth=0.4,
                zorder=5,
            )

    def _plot_tone_distribution(self, data_entries, group_key, scale):
        dist_type_val = str(self.get_param('dist_metric', None) or self.get_param('dist_type', 'raincloud_metric') or '')
        style_val = str(self.get_param('dist_style', 'raincloud' if self._normalize_work_mode() == 'publication' else 'boxplot') or '')

        low_style = style_val.lower()
        if "raincloud" in low_style or "雨云" in style_val:
            style = "雨云图 (Raincloud)"
        elif "violin" in low_style or "小提琴" in style_val:
            style = "小提琴图 (Violin Plot)"
        else:
            style = "科学箱线图 (Box Plot)"

        grouped_data = {}
        for entry in data_entries:
            val = entry.get(group_key, entry.get('group', '未分组'))
            grouped_data.setdefault(val, []).append(entry)

        num_points = self.project_tree.app_state_params.get('pts', 11)
        max_syls = max((len(e.get('syl_data', [])) for e in data_entries), default=1)
        n_groups = len(grouped_data)

        # Always compute the 8 linguistic metrics per group and attach to fig._tone_distribution_metrics
        all_group_metrics = {}
        for g_name, entries in grouped_data.items():
            m_bucket = {k: [] for k in ("mean", "onset", "mid", "offset", "inflection", "range", "slope", "duration_ms")}
            for entry in entries:
                rec = self._extract_tone_entry_metrics(entry, scale=scale)
                if rec is not None:
                    for k, v in rec.items():
                        m_bucket[k].append(v)
            all_group_metrics[g_name] = m_bucket

        metric_alias_map = {
            "mean": ("mean", "全调均值", "T 值 (0-5 标度)" if "T 值" in scale else "基频均值 (Hz)"),
            "全调均值": ("mean", "全调均值", "T 值 (0-5 标度)" if "T 值" in scale else "基频均值 (Hz)"),
            "onset": ("onset", "起点值 (Onset)", "起点 T 值" if "T 值" in scale else "起点基频 (Hz)"),
            "起点值": ("onset", "起点值 (Onset)", "起点 T 值" if "T 值" in scale else "起点基频 (Hz)"),
            "起点值 (onset)": ("onset", "起点值 (Onset)", "起点 T 值" if "T 值" in scale else "起点基频 (Hz)"),
            "mid": ("mid", "中点值 (Mid)", "中点 T 值" if "T 值" in scale else "中点基频 (Hz)"),
            "中点值": ("mid", "中点值 (Mid)", "中点 T 值" if "T 值" in scale else "中点基频 (Hz)"),
            "中点值 (mid)": ("mid", "中点值 (Mid)", "中点 T 值" if "T 值" in scale else "中点基频 (Hz)"),
            "offset": ("offset", "终点值 (Offset)", "终点 T 值" if "T 值" in scale else "终点基频 (Hz)"),
            "终点值": ("offset", "终点值 (Offset)", "终点 T 值" if "T 值" in scale else "终点基频 (Hz)"),
            "终点值 (offset)": ("offset", "终点值 (Offset)", "终点 T 值" if "T 值" in scale else "终点基频 (Hz)"),
            "inflection": ("inflection", "拐点极值", "拐点极值 T 值" if "T 值" in scale else "拐点极值 (Hz)"),
            "拐点极值": ("inflection", "拐点极值", "拐点极值 T 值" if "T 值" in scale else "拐点极值 (Hz)"),
            "range_metric": ("range", "调域跨度 (Range)", "调域跨度 (T 值)" if "T 值" in scale else "调域跨度 (Hz)"),
            "调域跨度 (range)": ("range", "调域跨度 (Range)", "调域跨度 (T 值)" if "T 值" in scale else "调域跨度 (Hz)"),
            "调域跨度": ("range", "调域跨度 (Range)", "调域跨度 (T 值)" if "T 值" in scale else "调域跨度 (Hz)"),
            "slope": ("slope", "斜率 (Slope)", "声调斜率 (ΔT)" if "T 值" in scale else "声调斜率 (ΔHz)"),
            "斜率": ("slope", "斜率 (Slope)", "声调斜率 (ΔT)" if "T 值" in scale else "声调斜率 (ΔHz)"),
            "斜率 (slope)": ("slope", "斜率 (Slope)", "声调斜率 (ΔT)" if "T 值" in scale else "声调斜率 (ΔHz)"),
            "duration": ("duration_ms", "音节时长 (Duration ms)", "音节时长 Duration (ms)"),
            "duration_ms": ("duration_ms", "音节时长 (Duration ms)", "音节时长 Duration (ms)"),
            "音节时长": ("duration_ms", "音节时长 (Duration ms)", "音节时长 Duration (ms)"),
            "音节时长 (duration ms)": ("duration_ms", "音节时长 (Duration ms)", "音节时长 Duration (ms)"),
        }

        dt_low = dist_type_val.strip().lower()
        if dt_low in metric_alias_map or dist_type_val in metric_alias_map or dt_low == "raincloud_metric":
            m_key, m_title, y_lbl = metric_alias_map.get(dt_low, metric_alias_map.get(dist_type_val, metric_alias_map["mean"]))
            fig, ax = plt.subplots(figsize=(max(7.2, 1.35 * n_groups + 2.0), 5.0))
            ax.set_facecolor("#FFFFFF")
            ax.grid(True, linestyle="--", alpha=0.25)

            data_to_plot, labels, colors_to_use = [], [], []
            for idx, g_name in enumerate(grouped_data.keys()):
                vals = all_group_metrics[g_name].get(m_key, [])
                if vals:
                    data_to_plot.append(vals)
                    labels.append(str(g_name))
                    colors_to_use.append(self.colors[idx % len(self.colors)])

            if data_to_plot:
                positions = np.arange(1, len(data_to_plot) + 1, dtype=float)
                if "雨云图" in style:
                    self._draw_raincloud_series(ax, data_to_plot, positions, colors_to_use)
                elif "小提琴" in style:
                    parts = ax.violinplot(data_to_plot, positions=positions, showmeans=True)
                    for pc_idx, pc in enumerate(parts['bodies']):
                        pc.set_facecolor(colors_to_use[pc_idx])
                        pc.set_alpha(0.6)
                else:
                    bp = ax.boxplot(data_to_plot, positions=positions, patch_artist=True)
                    for patch, color in zip(bp['boxes'], colors_to_use):
                        patch.set_facecolor(color)
                        patch.set_alpha(0.6)
                ax.set_xticks(positions)
                ax.set_xticklabels(labels, fontsize=10, fontweight="bold")
                ax.set_ylabel(y_lbl)
                t_str = f"声调声学特征分布 ({m_title} - {style})"
                ax.set_title(t_str, fontsize=12, fontweight="bold")
                ax._original_title = t_str
                if "T 值" in scale and m_key in ("mean", "onset", "mid", "offset", "inflection"):
                    ax.set_ylim(-0.2, 5.2)
                    ax.set_yticks([0, 1, 2, 3, 4, 5])

            fig._tone_distribution_metrics = all_group_metrics
            fig.tight_layout()
            return fig

        if dt_low in ("start_mid_end", "起-中-终三点比较") or "起-中-终" in dist_type_val:
            fig, ax = plt.subplots(figsize=(max(8.0, 2.2 * n_groups + 1.5), 5.0))
            ax.set_facecolor("#FFFFFF")
            ax.grid(True, linestyle="--", alpha=0.25)

            data_to_plot, labels, colors_to_use = [], [], []
            for g_color_idx, (g_name, entries) in enumerate(grouped_data.items()):
                color = self.colors[g_color_idx % len(self.colors)]
                m_b = all_group_metrics[g_name]
                if m_b["onset"]:
                    data_to_plot.extend([m_b["onset"], m_b["mid"], m_b["offset"]])
                    labels.extend([f"{g_name}\n起点", f"{g_name}\n中点", f"{g_name}\n终点"])
                    colors_to_use.extend([color, color, color])

            if data_to_plot:
                positions = np.arange(1, len(data_to_plot) + 1, dtype=float)
                if "雨云图" in style:
                    self._draw_raincloud_series(ax, data_to_plot, positions, colors_to_use, width=0.28)
                elif "小提琴图" in style:
                    parts = ax.violinplot(data_to_plot, positions=positions, showmeans=True)
                    for pc_idx, pc in enumerate(parts['bodies']):
                        pc.set_facecolor(colors_to_use[pc_idx])
                        pc.set_alpha(0.6)
                else:
                    bp = ax.boxplot(data_to_plot, positions=positions, patch_artist=True)
                    for patch, color in zip(bp['boxes'], colors_to_use):
                        patch.set_facecolor(color)
                        patch.set_alpha(0.6)

                ax.set_xticks(positions)
                ax.set_xticklabels(labels, fontsize=9)
                ax.set_title("各声调起-中-终三点基频离散比较", fontsize=13, fontweight="bold")
                ax._original_title = "各声调起-中-终三点基频离散比较"
                if "T 值" in scale:
                    ax.set_ylim(-0.2, 5.2)
                    ax.set_yticks([0, 1, 2, 3, 4, 5])

            fig._tone_distribution_metrics = all_group_metrics
            fig.tight_layout()
            return fig

        elif dt_low in ("range", "调域范围跨度图") or "调域范围" in dist_type_val:
            fig, ax = plt.subplots(figsize=(8, 5))
            ax.set_facecolor("#FFFFFF")
            ax.grid(True, linestyle="--", alpha=0.3)

            y_positions = np.arange(n_groups)
            group_names = []

            for idx, (g_name, entries) in enumerate(grouped_data.items()):
                group_names.append(g_name)
                color = self.colors[idx % len(self.colors)]

                min_vals, max_vals = [], []
                for entry in entries:
                    y_series = []
                    syl_list = entry['normalized_syl_data'] if "T 值" in scale else entry['syl_data']
                    for _s_dur, pts in syl_list:
                        y_series.extend(pts)
                    y_series = np.asarray(y_series, dtype=float)
                    valid_ys = y_series[np.isfinite(y_series)]
                    if len(valid_ys) > 0:
                        min_vals.append(np.min(valid_ys))
                        max_vals.append(np.max(valid_ys))

                if min_vals:
                    avg_min = np.mean(min_vals)
                    avg_max = np.mean(max_vals)
                    ax.barh(idx, avg_max - avg_min, left=avg_min, height=0.5, color=color, alpha=0.7, edgecolor=color, align='center')
                    ax.plot([avg_min, avg_max], [idx, idx], '|', color='black', markersize=10, markeredgewidth=2)
                    ax.text((avg_min + avg_max) / 2, idx, f"{avg_min:.2f} ~ {avg_max:.2f}\n(调域: {avg_max - avg_min:.2f})",
                            ha='center', va='center', color='black', fontsize=9, fontweight='bold')

            ax.set_yticks(y_positions)
            ax.set_yticklabels(group_names, fontsize=11, fontweight="bold")
            ax.set_title("各声调调域范围图 (最高点 / 最低点 / 调域跨度)", fontsize=13, fontweight="bold")
            ax._original_title = "各声调调域范围图 (最高点 / 最低点 / 调域跨度)"

            if "T 值" in scale:
                ax.set_xlim(-0.2, 5.2)
                ax.set_xticks([0, 1, 2, 3, 4, 5])
                ax.set_xlabel("T 值域区间")
            else:
                ax.set_xlabel("绝对频率范围 (Hz)")

            fig._tone_distribution_metrics = all_group_metrics
            fig.tight_layout()
            return fig

        elif dt_low in ("variability", "变异程度(cv)比较") or "变异程度" in dist_type_val:
            fig, ax = plt.subplots(figsize=(8, 5))
            ax.set_facecolor("#FFFFFF")
            ax.grid(True, linestyle="--", alpha=0.3)

            group_names, var_values, colors_to_use = [], [], []
            is_t_scale = ("T 值" in scale)
            total_pts = max_syls * num_points

            for idx, (g_name, entries) in enumerate(grouped_data.items()):
                group_names.append(g_name)
                color = self.colors[idx % len(self.colors)]
                colors_to_use.append(color)

                rows_mat = []
                for entry in entries:
                    y_series = []
                    syl_list = entry['normalized_syl_data'] if is_t_scale else entry['syl_data']
                    for _s_dur, pts in syl_list:
                        y_series.extend(pts)
                    if len(y_series) < total_pts:
                        y_series.extend([np.nan] * (total_pts - len(y_series)))
                    elif len(y_series) > total_pts:
                        y_series = y_series[:total_pts]
                    rows_mat.append(np.asarray(y_series, dtype=float))

                if rows_mat:
                    mat = np.vstack(rows_mat)
                    pt_sds = _safe_nanstd(mat, axis=0, default=np.nan)
                    if is_t_scale:
                        val = _safe_nanmean(pt_sds, default=0.0)
                    else:
                        pt_means = _safe_nanmean(mat, axis=0, default=np.nan)
                        with np.errstate(divide='ignore', invalid='ignore'):
                            pt_cvs = np.where(np.isfinite(pt_means) & (pt_means > 0), pt_sds / pt_means, np.nan)
                        val = _safe_nanmean(pt_cvs, default=0.0)
                    var_values.append(float(val) if np.isfinite(val) else 0.0)
                else:
                    var_values.append(0.0)

            ax.bar(group_names, var_values, color=colors_to_use, alpha=0.75, width=0.45)

            for idx, val in enumerate(var_values):
                lbl_str = f"{val:.2f}" if is_t_scale else f"{val:.1%}"
                offset = max(var_values) * 0.02 if max(var_values, default=0.0) > 0 else 0.005
                ax.text(idx, val + offset, lbl_str, ha='center', va='bottom', fontweight='bold')

            if is_t_scale:
                ax.set_title("各声调跨样本发音离散度 (点均标准差 Mean Point-wise SD) 比较", fontsize=12, fontweight="bold")
                ax.set_ylabel("跨样本点均标准差 (T 值 SD)")
            else:
                ax.set_title("各声调跨样本点均变异系数 (Point-wise CV) 比较", fontsize=12, fontweight="bold")
                ax.set_ylabel("点均变异系数 CV (SD / Mean)")
            ax.set_xlabel("声调类别")

            fig._tone_distribution_metrics = all_group_metrics
            fig.tight_layout()
            return fig

        else:  # 测量点精细分布 (legacy / detailed point-wise view)
            n_cols = min(2, max(1, n_groups))
            n_rows = math.ceil(max(1, n_groups) / n_cols)
            fig, axes = plt.subplots(n_rows, n_cols, figsize=(5 * n_cols + 2, 4.2 * n_rows + 0.5), squeeze=False, sharex=True, sharey=True)
            axes_flat = axes.flatten()

            for idx, (g_name, entries) in enumerate(grouped_data.items()):
                ax = axes_flat[idx]
                ax.set_facecolor("#FFFFFF")
                ax.grid(True, linestyle="--", alpha=0.3)

                pts_data = []
                for entry in entries:
                    y_series = []
                    syl_list = entry['normalized_syl_data'] if "T 值" in scale else entry['syl_data']
                    for _s_dur, pts in syl_list:
                        y_series.extend(pts)
                    if len(y_series) == max_syls * num_points:
                        pts_data.append(y_series)

                if not pts_data:
                    ax.text(0.5, 0.5, "数据不完整，音节数不一致", ha='center', va='center', color='red')
                    continue

                pts_data = np.asarray(pts_data, dtype=float)
                positions = np.arange(1, pts_data.shape[1] + 1)
                cleaned_columns = [pts_data[:, c][~np.isnan(pts_data[:, c])] for c in range(pts_data.shape[1])]

                if "雨云图" in style:
                    col_colors = [self.colors[idx % len(self.colors)]] * len(positions)
                    self._draw_raincloud_series(ax, cleaned_columns, positions, col_colors, width=0.30)
                elif "小提琴图" in style:
                    ax.violinplot(cleaned_columns, positions, showmeans=True, showmedians=False)
                else:
                    ax.boxplot(cleaned_columns, positions=positions, patch_artist=True,
                               boxprops=dict(facecolor="#DBEAFE", color="#1E40AF"),
                               whiskerprops=dict(color="#1E40AF"),
                               capprops=dict(color="#1E40AF"),
                               medianprops=dict(color="#DC2626", linewidth=1.5))

                ax.set_title(f"{g_name} 基频测量点分布", fontsize=11, fontweight="bold")
                ax._original_title = f"{g_name} 基频测量点分布"
                if "T 值" in scale:
                    ax.set_ylim(-0.2, 5.2)
                    ax.set_yticks([0, 1, 2, 3, 4, 5])
                ax.set_xticks(positions)
                ax.set_xticklabels([str((p - 1) % num_points + 1) for p in positions], fontsize=8)

            for idx in range(n_groups, len(axes_flat)):
                axes_flat[idx].set_visible(False)

            fig._tone_distribution_metrics = all_group_metrics
            fig.tight_layout()
            return fig

    def _plot_tone_effect_time(self, data_entries):
        rows = self._collect_tone_effect_time_rows(data_entries)
        if not rows:
            return self._tone_effect_blank(
                "没有可用于二字组调类效应分析的数据。\n请确认当前选择的组别来自二字组，并且组名形如“前字调类+后字调类”。"
            )

        fronts = {row["front"] for row in rows}
        backs = {row["back"] for row in rows}
        combos = {(row["front"], row["back"]) for row in rows}
        if len(fronts) < 2 or len(backs) < 2 or len(combos) < 4:
            return self._tone_effect_blank(
                "二字组调类效应数据不足。\n至少需要 2 个前字调类、2 个后字调类，以及 4 个可用调类组合。"
            )

        combo_groups = {}
        for row in rows:
            key = (row["front"], row["back"], row["syl"], row["x"])
            combo_groups.setdefault(key, []).append(row["value"])

        combo_rows = []
        for key, values in combo_groups.items():
            front, back, syl, x = key
            combo_rows.append({
                "front": front,
                "back": back,
                "syl": syl,
                "x": x,
                "mean": float(sum(values) / len(values)),
            })

        time_groups = {}
        for row in combo_rows:
            key = (row["syl"], row["x"])
            time_groups.setdefault(key, []).append(row)

        effect_rows = []
        for key, group_rows in time_groups.items():
            fronts_at_time = [row["front"] for row in group_rows]
            backs_at_time = [row["back"] for row in group_rows]
            y_values = [row["mean"] for row in group_rows]
            if len(y_values) < 4 or len(set(fronts_at_time)) < 2 or len(set(backs_at_time)) < 2:
                continue

            arr = np.asarray(y_values, dtype=float)
            total = float(np.sum((arr - float(np.mean(arr))) ** 2))
            if total <= 0:
                continue

            front_sse = self._residual_sse_for_tone_effect(y_values, [fronts_at_time])
            back_sse = self._residual_sse_for_tone_effect(y_values, [backs_at_time])
            additive_sse = self._residual_sse_for_tone_effect(y_values, [fronts_at_time, backs_at_time])
            front_part = max(0.0, 0.5 * ((total - front_sse) + (back_sse - additive_sse)))
            back_part = max(0.0, 0.5 * ((total - back_sse) + (front_sse - additive_sse)))
            interaction = max(0.0, additive_sse)
            denom = front_part + back_part + interaction
            if denom <= 0:
                continue

            _syl, x = key
            effect_rows.append({
                "x": x,
                "front": front_part / denom,
                "back": back_part / denom,
                "interaction": interaction / denom,
            })

        if not effect_rows:
            return self._tone_effect_blank(
                "当前组别无法估算调类效应时间进程。\n请增加前后调类组合覆盖，或检查所选组别是否过少。"
            )

        effect_rows.sort(key=lambda row: row["x"])
        xs = [row["x"] for row in effect_rows]

        fig, ax = plt.subplots(figsize=(11.2, 5.8))
        ax.set_facecolor("#FFFFFF")
        s0 = self._get_series_style(0)
        s1 = self._get_series_style(1)
        s2 = self._get_series_style(2)
        ax.plot(xs, [row["front"] for row in effect_rows], label="前字调类", linewidth=2.4, color=s0["color"], linestyle=s0["linestyle"], marker=s0["marker"], markersize=4.5)
        ax.plot(xs, [row["back"] for row in effect_rows], label="后字调类", linewidth=2.4, color=s1["color"], linestyle=s1["linestyle"], marker=s1["marker"], markersize=4.5)
        ax.plot(xs, [row["interaction"] for row in effect_rows], label="交互项/残差", linewidth=2.4, color=s2["color"], linestyle=s2["linestyle"], marker=s2["marker"], markersize=4.5)
        ax.axvline(100.0, color="#888888", linewidth=1.0, linestyle="--")
        ax.set_xlim(0, 200)
        ax.set_ylim(0, 1)
        ax.set_xticks([5, 50, 95, 105, 150, 195])
        ax.set_xticklabels(["前字5%", "前字50%", "前字95%", "后字5%", "后字50%", "后字95%"])
        ax.set_xlabel("两字组归一化时间轴")
        ax.set_ylabel("调类组合均值方差占比 (η²)")
        ax.set_title("二字组调类效应时间进程", fontsize=13, fontweight="bold")
        ax._original_title = "二字组调类效应时间进程"
        ax.grid(True, linestyle="--", alpha=0.25, linewidth=0.8)
        legend_kwargs = self._get_legend_kwargs()
        legend_kwargs["fontsize"] = 9
        ax.legend(**legend_kwargs)
        if self.get_param('legend_outside', False):
            loc_val = self.get_param('legend_loc', '右上')
            if "右" in loc_val:
                fig.subplots_adjust(right=0.82)
            elif "left" in loc_val or "左" in loc_val:
                fig.subplots_adjust(left=0.18)
        fig.tight_layout()
        return fig

    def _plot_temporal_density(self, data_entries, group_key, scale="T 值", is_preview=True):
        bw_method = float(self.get_param('density_bw', 0.15))
        f0_mode_val = self.get_param('density_f0_mode', 'percentile')
        facet_mode = self._resolve_facet_by(chart_type="density", fallback="group")
        scope = str(self.get_param('export_scope', 'active')).lower()
        normalization_val = self.get_param('density_normalization', None)
        if normalization_val is None:
            normalization_val = self.get_param('normalization', None)

        if normalization_val is None:
            normalization = "speaker" if scope == "integrated" else "global"
        else:
            normalization_text = str(normalization_val).lower()
            if "speaker" in normalization_text or "发音人" in str(normalization_val) or "per" in normalization_text:
                normalization = "speaker"
            else:
                normalization = "global"

        if f0_mode_val:
            if "percentile" in str(f0_mode_val).lower() or "分位数" in str(f0_mode_val):
                f0_mode = "percentile"
            elif "manual" in str(f0_mode_val).lower() or "手动" in str(f0_mode_val):
                f0_mode = "manual"
            else:
                f0_mode = "minmax"
        else:
            f0_mode = "percentile"

        max_syls = max((len(e.get('syl_data', [])) for e in data_entries), default=1)
        N_DENSE = 100

        def empty_density_fig(message):
            fig, ax = plt.subplots()
            ax.text(0.5, 0.5, message, ha='center', va='center')
            return fig

        def extract_normalized_contour(entry, c_s, c_e):
            y_dense_hz = self.project_tree._extract_kde_contour(entry.get('raw_xs', []), entry.get('raw_freqs', []), c_s, c_e, N_DENSE)
            if y_dense_hz is None:
                return None
            bounds = entry.get('speaker_f0_bounds')
            if not bounds:
                raw_f = np.asarray(entry.get('raw_freqs', []), dtype=float)
                bounds = self._get_f0_normalization_bounds(raw_f[np.isfinite(raw_f) & (raw_f > 0)])
            s_min, s_max = bounds
            return hz_to_t_value(y_dense_hz, s_min, s_max, clip=False)

        if "T 值" in scale:
            if normalization == "global":
                all_raw_f0_list = []
                for entry in data_entries:
                    valid_f = entry['raw_freqs'][entry['raw_freqs'] > 0]
                    if valid_f.size > 0:
                        all_raw_f0_list.append(valid_f)

                if not all_raw_f0_list:
                    return empty_density_fig("没有有效基频点可进行 KDE 计算")
                all_raw_f0 = np.concatenate(all_raw_f0_list)

                p_low_val = self.get_param('density_p_low', 5.0)
                p_high_val = self.get_param('density_p_high', 95.0)
                min_hz_val = self.get_param('density_m_min', 75.0)
                max_hz_val = self.get_param('density_m_max', 600.0)

                try:
                    p_low = float(p_low_val)
                    p_high = float(p_high_val)
                except ValueError:
                    p_low, p_high = 5.0, 95.0

                try:
                    min_f0 = float(min_hz_val)
                    max_f0 = float(max_hz_val)
                except ValueError:
                    min_f0, max_f0 = 75.0, 600.0

                if f0_mode == 'percentile':
                    min_f0 = np.percentile(all_raw_f0, p_low)
                    max_f0 = np.percentile(all_raw_f0, p_high)
                elif f0_mode == 'minmax':
                    min_f0 = np.min(all_raw_f0)
                    max_f0 = np.max(all_raw_f0)

                def hz_to_t(hz_array):
                    return hz_to_t_value(hz_array, min_f0, max_f0, clip=False)
            elif not any(np.any(np.isfinite(entry.get('normalized_raw_freqs', []))) for entry in data_entries):
                return empty_density_fig("没有有效归一化基频点可进行 KDE 计算")

            ymin, ymax = -0.5, 5.5
        else:
            all_ys = []
            for entry in data_entries:
                syl_bounds = self.project_tree._get_syllables_and_bounds(entry['raw_item'])[1]
                for _s_idx, (c_s, c_e) in enumerate(syl_bounds):
                    y_dense = self.project_tree._extract_kde_contour(entry['raw_xs'], entry['raw_freqs'], c_s, c_e, N_DENSE)
                    if y_dense is not None:
                        all_ys.append(y_dense[np.isfinite(y_dense)])
            if not all_ys:
                return empty_density_fig("没有有效基频点可进行 KDE 计算")
            all_y = np.concatenate(all_ys)
            if len(all_y) > 0:
                ymin = max(0.0, np.min(all_y) - 15.0)
                ymax = np.max(all_y) + 15.0
            else:
                ymin, ymax = 50.0, 500.0

        facet_keys = ["Default"]
        if facet_mode != "none":
            f_attr = "speaker_name" if facet_mode == "speaker" else ("label" if facet_mode == "label" else group_key)
            facet_keys = sorted(list(set(e.get(f_attr, e.get('group')) for e in data_entries)))

        n_facets = max(1, len(facet_keys))
        n_cols = min(2, n_facets)
        n_rows = math.ceil(n_facets / n_cols)

        fig, axes = plt.subplots(n_rows, n_cols, figsize=(5 * max_syls * n_cols + 1, 4.5 * n_rows + 0.5), squeeze=False, sharex=True, sharey=True)
        axes_flat = axes.flatten()

        for f_idx, f_key in enumerate(facet_keys):
            self._check_export_cancelled()
            if n_facets > 0:
                self._report_export_progress(0.35 + 0.45 * (f_idx / n_facets), f"正在计算 KDE 分面 {f_idx + 1}/{n_facets}...")
            ax = axes_flat[f_idx]
            ax.set_facecolor("#FFFFFF")

            facet_entries = data_entries
            if facet_mode != "none":
                f_attr = "speaker_name" if facet_mode == "speaker" else ("label" if facet_mode == "label" else group_key)
                facet_entries = [e for e in data_entries if e.get(f_attr, e.get('group')) == f_key]

            X_all_list, Y_all_list = [], []
            for e_idx, entry in enumerate(facet_entries):
                if (e_idx % 8) == 0:
                    self._check_export_cancelled()
                syl_bounds = self.project_tree._get_syllables_and_bounds(entry['raw_item'])[1]
                for s_idx, (c_s, c_e) in enumerate(syl_bounds):
                    if "T 值" in scale:
                        if normalization == "speaker":
                            y_t_dense = extract_normalized_contour(entry, c_s, c_e)
                        else:
                            y_dense = self.project_tree._extract_kde_contour(entry['raw_xs'], entry['raw_freqs'], c_s, c_e, N_DENSE)
                            y_t_dense = hz_to_t(y_dense) if y_dense is not None else None
                    else:
                        y_t_dense = self.project_tree._extract_kde_contour(entry['raw_xs'], entry['raw_freqs'], c_s, c_e, N_DENSE)
                    if y_t_dense is not None:
                        x_dense = np.linspace(s_idx * 100, (s_idx + 1) * 100, N_DENSE)
                        valid = np.isfinite(y_t_dense)
                        X_all_list.append(x_dense[valid])
                        Y_all_list.append(y_t_dense[valid])

            if not X_all_list:
                ax.text(0.5, 0.5, "没有足够的有效数据点", ha='center', va='center')
                continue

            xmin, xmax = 0, max_syls * 100
            x_arr = np.concatenate(X_all_list)
            y_arr = np.concatenate(Y_all_list)
            if len(x_arr) == 0:
                ax.text(0.5, 0.5, "没有足够的有效数据点", ha='center', va='center')
                continue
            high_prec = (not is_preview) or bool(self.get_param('high_precision', False))
            max_kde_points = int(self.get_param('density_max_points', 12000)) if high_prec else 3000

            if len(x_arr) > max_kde_points and max_kde_points > 0:
                sample_idx = np.linspace(0, len(x_arr) - 1, max_kde_points, dtype=int)
                x_arr = x_arr[sample_idx]
                y_arr = y_arr[sample_idx]

            positions = np.vstack([x_arr, y_arr])
            try:
                self._check_export_cancelled()
                kernel = gaussian_kde(positions, bw_method=bw_method)
                grid_x = max(120, min(240, int(80 * max_syls))) if high_prec else max(60, min(120, int(40 * max_syls)))
                grid_y = 90 if high_prec else 50
                xi, yi = np.mgrid[xmin:xmax:complex(0, grid_x), ymin:ymax:complex(0, grid_y)]
                zi = kernel(np.vstack([xi.flatten(), yi.flatten()])).reshape(xi.shape)

                vmax = zi.max()
                if vmax > 0:
                    levels = np.linspace(vmax * 0.05, vmax, 30)
                    ax.contourf(xi, yi, zi, levels=levels, cmap="YlOrRd", extend='neither')
            except Exception as e:
                ax.text(0.5, 0.5, f"KDE 计算失败: {str(e)[:20]}", ha='center', va='center', color='red')

            for k in range(1, max_syls):
                ax.axvline(k * 100, color='gray', linestyle='--', alpha=0.8)

            row_idx = f_idx // n_cols
            col_idx = f_idx % n_cols

            if col_idx == 0:
                ax.set_ylabel("T 值 (0-5 标度)" if "T 值" in scale else "频率 (Hz)")
            else:
                ax.set_ylabel("")

            is_bottom_row = (row_idx == n_rows - 1) or (f_idx + n_cols >= n_facets)
            ticks = [k * 100 + 50 for k in range(max_syls)]
            ax.set_xticks(ticks)
            if is_bottom_row:
                ax.set_xticklabels([f"第 {k+1} 字\n(0-100%)" for k in range(max_syls)], fontsize=9)
            else:
                ax.set_xticklabels([])

            ax.set_ylim(ymin, ymax)
            if "T 值" in scale:
                ax.set_yticks([0, 1, 2, 3, 4, 5])

            title_text = str(f_key) if f_key != "Default" else "时序密度热力图"
            if len(title_text) > 20:
                title_text = title_text[:17] + "..."
            ax.set_title(title_text, fontsize=12, fontweight="bold")
            ax._original_title = title_text

        for idx in range(n_facets, len(axes_flat)):
            axes_flat[idx].set_visible(False)

        fig.tight_layout()
        return fig

    def _plot_spaghetti_qc_overlay(self, data_entries, group_key="group", scale=None):
        """
        模式 B - 图表 2：组内个体原始曲线叠加图 (Spaghetti Raw Curve Overlay)
        - 将同一声调或同一元音下的所有词条原始曲线画在同一张子图里；
        - 正常曲线（与组中位数包络距离在 2σ 以内）：浅灰色半透明细线 (#94A3B8, alpha=0.45, lw=1.0)；
        - 超出 2σ 的离群词条曲线：高亮红色粗线 (#DC2626, lw=2.2)，并在曲线峰值/尾端直接标注 `发音人: 词条 #ID`。
        """
        if scale is None:
            scale_val = self.get_param('scale', 't_value')
            scale = "T 值" if (scale_val and ("T" in str(scale_val) or "t_value" in str(scale_val).lower())) else "Hz"

        is_formant = self._is_formant_mode() or any('syl_formants' in e for e in data_entries)
        num_points = int(self.project_tree.app_state_params.get('pts', 11))
        facet_mode = self._resolve_facet_by(chart_type="spaghetti_qc", fallback="group")

        grouped = {}
        for idx, entry in enumerate(data_entries):
            if facet_mode == "none":
                g_val = entry.get(group_key, entry.get('group', '全部样本'))
            elif facet_mode == "speaker":
                g_val = entry.get('speaker_name', '默认发音人')
            else:
                g_val = entry.get('group', '未分组')
            grouped.setdefault(g_val, []).append((idx, entry))

        facet_keys = list(grouped.keys()) if grouped else ["Default"]
        n_facets = max(1, len(facet_keys))
        n_cols = min(2, n_facets)
        n_rows = math.ceil(n_facets / n_cols)

        fig, axes = plt.subplots(
            n_rows, n_cols,
            figsize=(5.5 * n_cols + 1.5, 4.3 * n_rows + 0.6),
            squeeze=False,
        )
        axes_flat = axes.flatten()
        all_hover_meta = []
        outlier_records = []

        for f_idx, g_name in enumerate(facet_keys):
            ax = axes_flat[f_idx]
            ax.set_facecolor("#FFFFFF")
            ax.grid(True, linestyle="--", alpha=0.25)

            items_in_group = grouped.get(g_name, [])
            if not items_in_group:
                ax.text(0.5, 0.5, "无数据", ha="center", va="center")
                continue

            if is_formant:
                max_syls = max((len(e.get('syl_formants', [])) for _, e in items_in_group), default=1)
            else:
                max_syls = max((len(e.get('syl_data', [])) for _, e in items_in_group), default=1)
            total_pts = max(1, max_syls * num_points)
            x_grid = np.arange(1, total_pts + 1, dtype=float)

            curves_info = []
            for idx_e, entry in items_in_group:
                spk = entry.get('speaker_name', '未知发音人')
                lbl = entry.get('label', '未命名')
                item_id = entry.get('item_id') or f"item_{idx_e}"
                if is_formant:
                    y_flat = []
                    for s_i in range(max_syls):
                        syls = entry.get('syl_formants', [])
                        if s_i < len(syls):
                            y_flat.extend(self._resample_formant_track(syls[s_i].get('f1', []), num_points).tolist())
                        else:
                            y_flat.extend([np.nan] * num_points)
                    y_arr = np.asarray(y_flat[:total_pts], dtype=float)
                else:
                    syl_list = entry.get('normalized_syl_data') if "T 值" in scale else entry.get('syl_data', [])
                    y_flat = []
                    for _sd, pts in (syl_list or []):
                        y_flat.extend(pts)
                    if len(y_flat) < total_pts:
                        y_flat.extend([np.nan] * (total_pts - len(y_flat)))
                    y_arr = np.asarray(y_flat[:total_pts], dtype=float)
                y_arr = np.where(np.isfinite(y_arr), y_arr, np.nan)
                curves_info.append({
                    "speaker_name": spk,
                    "label": lbl,
                    "item_id": str(item_id),
                    "group": entry.get('group', str(g_name)),
                    "y": y_arr,
                    "warnings": entry.get('warnings', []),
                    "ax": ax,
                })

            mat = np.vstack([c["y"] for c in curves_info])
            with warnings.catch_warnings(), np.errstate(all='ignore'):
                warnings.simplefilter("ignore", category=RuntimeWarning)
                med_y = np.nanmedian(mat, axis=0)
                std_classic = _safe_nanstd(mat, axis=0, default=np.nan)
                mad = np.nanmedian(np.abs(mat - med_y), axis=0)
                std_robust = np.where(np.isfinite(mad) & (mad > 1e-4), 1.4826 * mad, std_classic)
                min_sigma = 0.12 if ("T 值" in scale and not is_formant) else (15.0 if is_formant else 4.0)
                sigma = np.maximum(np.minimum(std_classic, std_robust), min_sigma)
                sigma = np.where(np.isfinite(sigma) & (sigma > 0), sigma, min_sigma)

            normal_count = 0
            outlier_count = 0
            for c_info in curves_info:
                y_arr = c_info["y"]
                z_arr = np.abs(y_arr - med_y) / sigma
                finite_z = z_arr[np.isfinite(z_arr)]
                max_z = float(np.max(finite_z)) if finite_z.size > 0 else 0.0
                rms_z = float(np.sqrt(np.mean(finite_z ** 2))) if finite_z.size > 0 else 0.0
                has_warn = any(
                    str(w).startswith(("[警告]", "[致命]", "[复核]", "[错误]"))
                    for w in (c_info["warnings"] or [])
                )
                is_outlier = bool(max_z > 2.0 or rms_z > 1.85 or has_warn)
                c_info["max_z"] = max_z
                c_info["rms_z"] = rms_z
                c_info["rmsd"] = rms_z
                c_info["is_outlier"] = is_outlier
                c_info["is_anomaly"] = is_outlier
                c_info["z_scores"] = np.where(np.isfinite(z_arr), z_arr, np.nan).tolist()
                c_info["values"] = y_arr.tolist()

                tag_text = f"{c_info['speaker_name']}: {c_info['label']} #{c_info['item_id']}"
                c_info["tag_text"] = tag_text
                all_hover_meta.append(c_info)

                if is_outlier:
                    outlier_count += 1
                    outlier_records.append(c_info)
                    lbl_plot = "离群异常轨迹 (>2σ)" if outlier_count == 1 else None
                    ax.plot(
                        x_grid, y_arr,
                        color="#DC2626", linewidth=2.2, alpha=0.92,
                        zorder=5, label=lbl_plot,
                    )
                    # Annotate at peak deviation or curve tail
                    finite_idx = np.where(np.isfinite(y_arr))[0]
                    if finite_idx.size > 0:
                        if finite_z.size > 0:
                            peak_i = int(finite_idx[int(np.argmax(np.abs(y_arr[finite_idx] - med_y[finite_idx])))])
                        else:
                            peak_i = int(finite_idx[-1])
                        ax.annotate(
                            tag_text,
                            xy=(float(x_grid[peak_i]), float(y_arr[peak_i])),
                            xytext=(4, 4),
                            textcoords="offset points",
                            fontsize=8.0,
                            fontweight="bold",
                            color="#DC2626",
                            zorder=6,
                        )
                else:
                    normal_count += 1
                    lbl_plot = "正常轨迹 (≤2σ)" if normal_count == 1 else None
                    ax.plot(
                        x_grid, y_arr,
                        color="#94A3B8", linewidth=1.0, alpha=0.45,
                        zorder=2, label=lbl_plot,
                    )

            # Plot median reference and ±2σ envelope
            if np.any(np.isfinite(med_y)):
                ax.plot(x_grid, med_y, color="#1E293B", linestyle="--", linewidth=1.5, alpha=0.85, zorder=4, label="组中位数包络")
                ax.fill_between(x_grid, med_y - 2.0 * sigma, med_y + 2.0 * sigma, color="#CBD5E1", alpha=0.18, zorder=1)

            if max_syls > 1:
                for k in range(1, max_syls):
                    ax.axvline(k * num_points + 0.5, color="gray", linestyle="--", alpha=0.5)

            sub_t = f"{g_name} - 组内个体原始曲线叠加 (离群={outlier_count})"
            ax.set_title(sub_t, fontsize=11, fontweight="bold")
            ax._original_title = sub_t
            ax.set_xlabel("归一化测量点")
            if f_idx % n_cols == 0:
                if is_formant:
                    ax.set_ylabel("F1 频率 (Hz)")
                elif "T 值" in scale:
                    ax.set_ylim(-0.2, 5.2)
                    ax.set_yticks([0, 1, 2, 3, 4, 5])
                    ax.set_ylabel("T 值 (0-5 标度)")
                else:
                    ax.set_ylabel("频率 (Hz)")

            handles, labels = ax.get_legend_handles_labels()
            if handles:
                legend_kwargs = self._get_legend_kwargs()
                legend_kwargs["fontsize"] = 8
                ax.legend(**legend_kwargs)

        for idx in range(n_facets, len(axes_flat)):
            axes_flat[idx].set_visible(False)

        fig._qc_hover_metadata = all_hover_meta
        fig._spaghetti_outliers = outlier_records
        fig.tight_layout()
        return fig

    def _draw_active_ratio_panel(self, ax, data_entries):
        ax.set_facecolor("#FFFFFF")
        ax.grid(True, linestyle="--", alpha=0.3)

        spk_data = {}
        for entry in data_entries:
            spk = entry.get('speaker_name', '默认发音人')
            ratio = entry.get('active_ratio')
            if ratio is None:
                # Fallback for formant entries
                raw_f1 = np.asarray(entry.get('raw_f1', []), dtype=float)
                ratio = float(np.mean(np.isfinite(raw_f1) & (raw_f1 > 0))) if raw_f1.size > 0 else 1.0
            spk_data.setdefault(spk, []).append(float(ratio))

        labels = list(spk_data.keys())
        data_to_plot = [spk_data[s] for s in labels]

        if data_to_plot:
            bp = ax.boxplot(data_to_plot, patch_artist=True)
            for patch in bp['boxes']:
                patch.set_facecolor("#ECFDF5")
                patch.set_color("#059669")
            for whisker in bp['whiskers']:
                whisker.set_color("#059669")
            for cap in bp['caps']:
                cap.set_color("#059669")
            for median in bp['medians']:
                median.set_color("#DC2626")
                median.set_linewidth(1.5)

            ax.set_xticklabels(labels, fontsize=10, fontweight="bold")
            ax.set_ylabel("有效采样点检出率 (Active Ratio)", fontsize=10.5)
            t_str = "各发音人录音有效率与检出比例分布"
            ax.set_title(t_str, fontsize=11.5, fontweight="bold")
            ax._original_title = t_str
            ax.axhline(0.60, color="#EF4444", linestyle=":", label="常规建议的极低阈值 (60%)")
            legend_kwargs = self._get_legend_kwargs()
            legend_kwargs["fontsize"] = 8.5
            ax.legend(**legend_kwargs)
            ax.set_ylim(-0.05, 1.05)

    def _draw_speaker_range_panel(self, ax, data_entries):
        ax.set_facecolor("#FFFFFF")
        ax.grid(True, linestyle="--", alpha=0.3)

        ordered_speakers = self._ordered_unique(e.get('speaker_name', '默认发音人') for e in data_entries)
        spk_color_map = {spk: self.colors[i % len(self.colors)] for i, spk in enumerate(ordered_speakers)}
        is_formant = self._is_formant_mode() or any('syl_formants' in e for e in data_entries)

        spk_freqs_pool = {}
        for entry in data_entries:
            spk = entry.get('speaker_name', '默认发音人')
            color = spk_color_map.get(spk, self.colors[0])
            if is_formant:
                raw_v = np.asarray(entry.get('raw_f1', []), dtype=float)
            else:
                raw_v = np.asarray(entry.get('raw_freqs', []), dtype=float)
            valid_ys = raw_v[np.isfinite(raw_v) & (raw_v > 0)]
            if len(valid_ys) > 0:
                mean_v = float(np.mean(valid_ys))
                ax.scatter(spk, mean_v, color=color, alpha=0.45, edgecolors='none', s=40)
                spk_freqs_pool.setdefault(spk, []).append(mean_v)

        for idx, (spk, means) in enumerate(spk_freqs_pool.items()):
            med = float(np.median(means))
            q1 = float(np.percentile(means, 25))
            q3 = float(np.percentile(means, 75))
            color = spk_color_map.get(spk, self.colors[idx % len(self.colors)])
            unit_lbl = "Hz"
            ax.errorbar(
                spk, med,
                yerr=[[med - q1], [q3 - med]],
                fmt='D', color='black', ecolor=color,
                elinewidth=3, capsize=8,
                label=f"{spk} 中位数 ({med:.1f}{unit_lbl})" if idx < 5 else "",
            )

        t_str = "各受试发音人共振峰均值与跨度分布" if is_formant else "各受试发音人基频均值及调域离散域 (排查八度跳变)"
        ax.set_title(t_str, fontsize=11.5, fontweight="bold")
        ax._original_title = t_str
        ax.set_ylabel("共振峰均值 Mean F1 (Hz)" if is_formant else "发音基频均值 Mean F0 (Hz)")
        handles, _ = ax.get_legend_handles_labels()
        if handles:
            legend_kwargs = self._get_legend_kwargs()
            legend_kwargs["fontsize"] = 8.5
            ax.legend(**legend_kwargs)

    def _plot_quality_check(self, data_entries):
        view_val = self.get_param('qc_view', None)
        if view_val is None:
            view = "dual_check" if self._normalize_work_mode() == "qc" else "raw_overlay"
        else:
            v_str = str(view_val).lower()
            if any(k in v_str for k in ("dual", "双联", "综合体检", "体检面板")):
                view = "dual_check"
            elif any(k in v_str for k in ("spaghetti", "组内个体")):
                return self._plot_spaghetti_qc_overlay(data_entries)
            elif "active_ratio" in v_str or "有效点" in v_str or "检出率" in v_str:
                view = "active_ratio"
            elif "speaker_means" in v_str or "发音人" in v_str or "调域" in v_str:
                view = "speaker_means"
            else:
                view = "raw_overlay"

        if view == "dual_check":
            fig, (ax_left, ax_right) = plt.subplots(1, 2, figsize=(12.4, 5.2))
            self._draw_active_ratio_panel(ax_left, data_entries)
            self._draw_speaker_range_panel(ax_right, data_entries)
            fig.suptitle("检出率与发音人基频/共振峰综合体检面板", fontsize=13, fontweight="bold")
            fig.tight_layout()
            return fig

        scale_val = self.get_param('scale')
        is_t_value = scale_val and ("T" in str(scale_val) or "t_value" in str(scale_val).lower())

        if view == "raw_overlay":
            fig, ax = plt.subplots(figsize=(9, 5))
            ax.set_facecolor("#FFFFFF")
            ax.grid(True, linestyle="--", alpha=0.3)

            max_syls = max((len(e.get('syl_data', [])) for e in data_entries), default=1)
            num_points = self.project_tree.app_state_params.get('pts', 11)
            total_pts = max_syls * num_points

            normal_lines = []
            outlier_lines = []
            all_y_rows = []
            entry_xy = []

            for idx_e, entry in enumerate(data_entries):
                y_series = entry['normalized_syl_data'] if is_t_value else entry['syl_data']
                y_flat, x_flat = [], []
                for s_idx, (_s_dur, pts) in enumerate(y_series):
                    x_pts = np.linspace(s_idx * num_points + 1, (s_idx + 1) * num_points, len(pts))
                    x_flat.extend(x_pts)
                    y_flat.extend(pts)

                y_flat = np.asarray(y_flat, dtype=float)
                x_flat = np.asarray(x_flat, dtype=float)
                if len(y_flat) == total_pts:
                    all_y_rows.append(y_flat)
                entry_xy.append((idx_e, entry, x_flat, y_flat))

            if all_y_rows:
                mat = np.vstack(all_y_rows)
                med_y = np.nanmedian(mat, axis=0)
                std_y = np.maximum(_safe_nanstd(mat, axis=0, default=0.2), 0.15 if is_t_value else 5.0)
            else:
                med_y = None
                std_y = None

            for idx_e, entry, x_flat, y_flat in entry_xy:
                has_warning = any(
                    w.startswith("[警告]") or w.startswith("[致命]") or w.startswith("[复核]") or w.startswith("[错误]")
                    for w in entry.get('warnings', [])
                )
                exceeds_2sigma = False
                if med_y is not None and len(y_flat) == len(med_y):
                    z_vals = np.abs(y_flat - med_y) / std_y
                    finite_z = z_vals[np.isfinite(z_vals)]
                    if finite_z.size > 0 and float(np.max(finite_z)) > 2.0:
                        exceeds_2sigma = True

                seg = np.column_stack((x_flat, y_flat))
                if has_warning or exceeds_2sigma:
                    outlier_lines.append(seg)
                    finite_idx = np.where(np.isfinite(y_flat))[0]
                    if finite_idx.size > 0:
                        ti = int(finite_idx[-1])
                        item_id = entry.get('item_id', f"item_{idx_e}")
                        tag_lbl = f"{entry.get('speaker_name', '')}: {entry.get('label', '')} #{item_id}"
                        ax.annotate(
                            tag_lbl,
                            xy=(float(x_flat[ti]), float(y_flat[ti])),
                            xytext=(4, 2),
                            textcoords="offset points",
                            fontsize=7.5,
                            color="#DC2626",
                            fontweight="bold",
                        )
                else:
                    normal_lines.append(seg)

            from matplotlib.collections import LineCollection
            if normal_lines:
                lc_norm = LineCollection(normal_lines, colors="#94A3B8", linewidths=1.0, alpha=0.45)
                ax.add_collection(lc_norm)
                ax.plot([], [], color="#94A3B8", linewidth=1.0, alpha=0.45, label="未触发复核阈值轨迹")
            if outlier_lines:
                lc_out = LineCollection(outlier_lines, colors="#DC2626", linewidths=2.2, alpha=0.9, linestyles="-")
                ax.add_collection(lc_out)
                ax.plot([], [], color="#DC2626", linewidth=2.2, alpha=0.9, linestyle="-", label="触发复核阈值轨迹")

            if max_syls > 1:
                for k in range(1, max_syls):
                    ax.axvline(k * num_points + 0.5, color='gray', linestyle='--', alpha=0.5)

            ax.set_title("数据质量分析：逐项基频曲线质量分布叠加", fontsize=13, fontweight="bold")
            ax._original_title = "数据质量分析：逐项基频曲线质量分布叠加"
            ax.set_xlabel("测量点")

            if is_t_value:
                ax.set_ylim(-0.2, 5.2)
                ax.set_yticks([0, 1, 2, 3, 4, 5])
                ax.set_ylabel("T 值 (0-5 标度)")
            else:
                ax.set_ylabel("频率 (Hz)")

            legend_kwargs = self._get_legend_kwargs()
            ax.legend(**legend_kwargs)
            if self.get_param('legend_outside', False):
                loc_val = self.get_param('legend_loc', '右上')
                if "右" in loc_val:
                    fig.subplots_adjust(right=0.82)
                elif "left" in loc_val or "左" in loc_val:
                    fig.subplots_adjust(left=0.18)
            return fig

        elif view == "active_ratio":
            fig, ax = plt.subplots(figsize=(8, 5))
            self._draw_active_ratio_panel(ax, data_entries)
            if self.get_param('legend_outside', False):
                loc_val = self.get_param('legend_loc', '右上')
                if "右" in loc_val:
                    fig.subplots_adjust(right=0.82)
                elif "left" in loc_val or "左" in loc_val:
                    fig.subplots_adjust(left=0.18)
            return fig

        elif view == "speaker_means":
            fig, ax = plt.subplots(figsize=(9, 5))
            self._draw_speaker_range_panel(ax, data_entries)
            if self.get_param('legend_outside', False):
                loc_val = self.get_param('legend_loc', '右上')
                if "右" in loc_val:
                    fig.subplots_adjust(right=0.82)
                elif "left" in loc_val or "左" in loc_val:
                    fig.subplots_adjust(left=0.18)
            return fig

        fig, ax = plt.subplots()
        return fig

    def _export_paginated_pdf(self, out_file, data_entries, group_key, scale):
        from matplotlib.backends.backend_pdf import PdfPages

        unique_groups = []
        for e in data_entries:
            val = e[group_key]
            if val not in unique_groups:
                unique_groups.append(val)

        chunk_size = 8
        pdf_pages = PdfPages(out_file)

        try:
            for page_idx in range(math.ceil(len(unique_groups) / chunk_size)):
                self._check_export_cancelled()
                total_pages = max(1, math.ceil(len(unique_groups) / chunk_size))
                self._report_export_progress(0.15 + 0.75 * (page_idx / total_pages), f"正在导出分页图 {page_idx + 1}/{total_pages}...")
                allowed_groups = set(unique_groups[page_idx * chunk_size : (page_idx + 1) * chunk_size])
                chunk_entries = [e for e in data_entries if e[group_key] in allowed_groups]

                fig = self.generate_plot(chunk_entries, is_preview=False)

                fig.text(0.95, 0.02, f"第 {page_idx + 1} 页 / 共 {math.ceil(len(unique_groups) / chunk_size)} 页",
                         ha='right', va='bottom', fontsize=9, color='gray')

                pdf_pages.savefig(fig, bbox_inches='tight')
                plt.close(fig)
        finally:
            pdf_pages.close()

    def _export_paginated_images(self, base_path, data_entries, group_key, scale, ext):
        unique_groups = []
        for e in data_entries:
            val = e[group_key]
            if val not in unique_groups:
                unique_groups.append(val)

        chunk_size = 8
        total_pages = math.ceil(len(unique_groups) / chunk_size)

        dir_name, file_name = os.path.split(base_path)
        name_part, _ = os.path.splitext(file_name)

        for page_idx in range(total_pages):
            self._check_export_cancelled()
            self._report_export_progress(0.15 + 0.75 * (page_idx / max(1, total_pages)), f"正在导出分页图 {page_idx + 1}/{total_pages}...")
            allowed_groups = set(unique_groups[page_idx * chunk_size : (page_idx + 1) * chunk_size])
            chunk_entries = [e for e in data_entries if e[group_key] in allowed_groups]

            fig = self.generate_plot(chunk_entries, is_preview=False)

            fig.text(0.95, 0.02, f"第 {page_idx + 1} 页 / 共 {total_pages} 页",
                     ha='right', va='bottom', fontsize=9, color='gray')

            out_path = os.path.join(dir_name, f"{name_part}_第{page_idx + 1}页{ext}")
            self._save_figure(fig, out_path)
            if page_idx == 0 and os.path.abspath(out_path) != os.path.abspath(base_path):
                import shutil as _shutil
                _shutil.copy2(out_path, base_path)
            plt.close(fig)

    def _export_dataset(self, data, out_path, ext):
        self._check_export_cancelled()
        rule_summary = self._format_chart_group_rule_summary(self.get_param("chart_group_rule", None))
        self._report_export_progress(0.05, f"正在按{rule_summary}准备导出数据...")
        chart_type = self.get_param('chart_type', 'contour')
        groupby = self.get_param('groupby', 'group')
        group_key = self._get_group_key(groupby)

        unique_groups = []
        for e in data:
            val = e[group_key]
            if val not in unique_groups:
                unique_groups.append(val)

        scale_val = self.get_param('scale')
        if scale_val and ("T" in str(scale_val) or "t_value" in str(scale_val).lower()):
            scale = "T 值"
        else:
            scale = "Hz"

        pagination_state = self._get_group_pagination_state(data, chart_type, groupby)
        if pagination_state['is_paginated_heatmap'] and pagination_state['pages']:
            with _MATPLOTLIB_LOCK:
                if ext == ".pdf":
                    self._export_overview_heatmap_paginated_pdf(out_path, data, scale, pagination_state['pages'])
                else:
                    self._export_overview_heatmap_paginated_images(out_path, data, scale, ext, pagination_state['pages'])
            self._report_export_progress(1.0, "当前任务完成")
            return

        if len(unique_groups) > 8 and not self._is_overview_heatmap_chart(chart_type) and chart_type != "tone_effect_time":
            with _MATPLOTLIB_LOCK:
                if ext == ".pdf":
                    self._export_paginated_pdf(out_path, data, group_key, scale)
                else:
                    self._export_paginated_images(out_path, data, group_key, scale, ext)
        else:
            self._report_export_progress(0.2, "正在生成图表...")
            self._check_export_cancelled()
            with _MATPLOTLIB_LOCK:
                fig = self.generate_plot(data, is_preview=False)
                self._check_export_cancelled()
                self._report_export_progress(0.9, "正在写入文件...")
                self._save_figure(fig, out_path)
                plt.close(fig)
        self._report_export_progress(1.0, "当前任务完成")

    def _extract_active_formant_data(self, speakers_list, filter_groups=True, apply_chart_grouping=True):
        num_points = self.project_tree.app_state_params.get('pts', 11)
        data_entries = []

        default_strategy = '整段11点'
        if hasattr(self.app, 'last_params') and isinstance(self.app.last_params, dict):
            default_strategy = self.app.last_params.get('formant_sample_strategy', '整段11点')
        elif isinstance(self.project_tree.app_state_params, dict):
            default_strategy = self.project_tree.app_state_params.get('formant_sample_strategy', '整段11点')

        for speaker in speakers_list:
            if speaker is None:
                continue
            cache_key = (getattr(speaker, 'id', id(speaker)), 'formant', int(num_points), str(default_strategy))
            if not getattr(self, '_force_live_extract', False) and hasattr(self, '_speaker_data_cache') and cache_key in self._speaker_data_cache:
                data_entries.extend([self._attach_entry_group_defaults(e) for e in self._speaker_data_cache[cache_key]])
                continue

            orig_items = self.project_tree.items
            try:
                self.project_tree.items = speaker.items
                s_struct = self.project_tree._get_items_by_group_for_dict(speaker.items)
                speaker_data_entries = []

                for grp_name, children in s_struct:
                    for child in children:
                        item = speaker.items[child]
                        self.project_tree._ensure_item_loaded(item)
                        if item.get('start') is None or not item.get('formant_data'):
                            continue

                        bounds = get_item_syllable_bounds(item)
                        label = item.get('label', '')
                        syls = split_into_syllables(label)

                        strategy = item.get('formant_sample_strategy') or default_strategy

                        sampled = sample_formant_points_by_bounds_extended(
                            item, bounds, num_points, strategy, include_f3=True
                        )
                        times = sampled["times"]
                        f1_vals = sampled["f1"]
                        f2_vals = sampled["f2"]
                        f3_vals = sampled["f3"]

                        syl_formants = []
                        for idx_syl, (c_s, c_e) in enumerate(bounds):
                            char = syls[idx_syl] if idx_syl < len(syls) else f"字{idx_syl+1}"
                            s_idx_p = idx_syl * num_points
                            e_idx_p = s_idx_p + num_points
                            s_times = times[s_idx_p:e_idx_p]
                            s_f1 = f1_vals[s_idx_p:e_idx_p]
                            s_f2 = f2_vals[s_idx_p:e_idx_p]
                            s_f3 = f3_vals[s_idx_p:e_idx_p]
                            syl_formants.append({
                                'syllable_index': idx_syl,
                                'char': char,
                                'bounds': [c_s, c_e],
                                'times': s_times,
                                'f1': s_f1,
                                'f2': s_f2,
                                'f3': s_f3,
                            })

                        raw_f1 = np.asarray(item['formant_data'].get('f1', []), dtype=float)
                        raw_f2 = np.asarray(item['formant_data'].get('f2', []), dtype=float)
                        valid_mask = np.isfinite(raw_f1) & np.isfinite(raw_f2) & (raw_f1 > 0) & (raw_f2 > raw_f1)
                        active_ratio = float(np.mean(valid_mask)) if raw_f1.size > 0 else 0.0

                        warnings = item.get('warnings', [])

                        speaker_data_entries.append({
                            'item_id': str(child),
                            'speaker_name': speaker.name,
                            'group': grp_name,
                            'wordlist_group': grp_name,
                            'chart_group': grp_name,
                            'label': label,
                            'syl_formants': syl_formants,
                            'raw_xs': item['formant_data'].get('xs', []),
                            'raw_f1': item['formant_data'].get('f1', []),
                            'raw_f2': item['formant_data'].get('f2', []),
                            'raw_f3': item['formant_data'].get('f3', []),
                            'active_ratio': active_ratio,
                            'warnings': warnings,
                            'raw_item': item,
                            **self._item_wordlist_metadata(item),
                        })
            finally:
                self.project_tree.items = orig_items

            if hasattr(self, '_speaker_data_cache'):
                self._speaker_data_cache[cache_key] = [self._attach_entry_group_defaults(e) for e in speaker_data_entries]
            data_entries.extend(speaker_data_entries)

        if apply_chart_grouping:
            data_entries = self._apply_chart_grouping(data_entries)
        else:
            data_entries = self._apply_chart_grouping(
                data_entries,
                rule=self._default_chart_group_rule(),
                preserve_original_group=True,
            )

        if filter_groups:
            selected_groups = self.get_param('selected_groups', None)
            if selected_groups is not None:
                if isinstance(selected_groups, str):
                    selected_groups = [g.strip() for g in selected_groups.split(',') if g.strip()]
                selected_groups = set(selected_groups)
                data_entries = [e for e in data_entries if e['group'] in selected_groups]
            elif hasattr(self, 'group_checkbox_vars') and self.group_checkbox_vars:
                selected_groups = {g for g, var in self.group_checkbox_vars.items() if var.get()}
                data_entries = [e for e in data_entries if e['group'] in selected_groups]

        return data_entries

    def _get_syl_category(self, entry, syl, groupby_val):
        val = str(groupby_val or "")
        low = val.strip().lower()
        if low in ("按词语", "按词项", "label", "word") or "词语" in val:
            return entry['label']
        if low in ("按单字/音节", "按单字音节", "syl_char", "char", "syllable") or "单字" in val or "音节" in val:
            return syl['char']
        if low in ("按发音人", "speaker", "speaker_name") or "发音人" in val:
            return entry['speaker_name']
        if low == "custom_field" or low.startswith("custom") or low.startswith("item_meta") or "自定义字段" in val:
            if "custom_field" in entry:
                return entry["custom_field"]
            meta = entry.get("item_meta") if isinstance(entry.get("item_meta"), dict) else {}
            if ":" in val:
                fn = val.split(":", 1)[1].strip()
                if fn in meta:
                    return str(meta[fn] or "未标注")
            if meta:
                return str(meta[sorted(meta.keys())[0]] or "未标注")
        return entry['group']

    def _draw_confidence_ellipse(self, x, y, ax, n_std=1.5152, facecolor='none', **kwargs):
        if len(x) < 3:
            return None
        from matplotlib.patches import Ellipse
        try:
            x = np.asarray(x, dtype=float)
            y = np.asarray(y, dtype=float)
            finite = np.isfinite(x) & np.isfinite(y)
            x = x[finite]
            y = y[finite]
            if len(x) < 3:
                return None
            # 移除硬性 [5%, 95%] 矩形分位数裁剪，避免人为低估元音分布椭圆面积 (BUG-M06)
            cov = np.cov(x, y, ddof=1)
            if np.any(np.isnan(cov)) or np.any(np.isinf(cov)):
                return None

            vals, vecs = np.linalg.eigh(cov)
            order = vals.argsort()[::-1]
            vals, vecs = vals[order], vecs[:, order]

            theta = np.degrees(np.arctan2(*vecs[:, 0][::-1]))
            width, height = 2.0 * float(n_std) * np.sqrt(np.maximum(vals, 0))

            ellipse = Ellipse(xy=(np.mean(x), np.mean(y)), width=width, height=height,
                              angle=theta, facecolor=facecolor, **kwargs)
            return ax.add_patch(ellipse)
        except Exception:
            return None

    def _get_formant_time_cmap(self):
        from matplotlib.colors import LinearSegmentedColormap
        return LinearSegmentedColormap.from_list(
            "formant_time_red_blue",
            ["#D73027", "#B64D7A", "#2166AC"],
            N=256
        )

    def _slice_formant_entries_for_syllable(self, data_entries, syllable_index):
        sliced = []
        for entry in data_entries:
            syls = [s for s in entry.get('syl_formants', []) if int(s.get('syllable_index', -1)) == syllable_index]
            if syls:
                copied = dict(entry)
                copied['syl_formants'] = syls
                sliced.append(copied)
        return sliced

    def _get_formant_density_points(self, data_entries, transform_fn=None):
        all_f1_list, all_f2_list, all_tau_list = [], [], []
        for entry in data_entries:
            xs = np.asarray(entry.get('raw_xs', []), dtype=float)
            f1_arr = np.asarray(entry.get('raw_f1', []), dtype=float)
            f2_arr = np.asarray(entry.get('raw_f2', []), dtype=float)
            if len(xs) == 0 or len(f1_arr) == 0 or len(f2_arr) == 0:
                continue
            spk_name = entry.get('speaker_name')
            for syl in entry.get('syl_formants', []):
                c_s, c_e = syl['bounds']
                if c_e <= c_s:
                    continue
                mask = (xs >= c_s) & (xs <= c_e) & np.isfinite(f1_arr) & np.isfinite(f2_arr) & (f2_arr > f1_arr)
                if not np.any(mask):
                    continue
                s_tau = np.clip((xs[mask] - c_s) / (c_e - c_s), 0.0, 1.0)
                s_f1 = f1_arr[mask]
                s_f2 = f2_arr[mask]
                if transform_fn is not None:
                    try:
                        s_f1, s_f2 = transform_fn(s_f1, s_f2, speaker_name=spk_name)
                    except TypeError:
                        s_f1, s_f2 = transform_fn(s_f1, s_f2)
                all_f1_list.append(s_f1)
                all_f2_list.append(s_f2)
                all_tau_list.append(s_tau)

        if not all_f1_list:
            return np.array([]), np.array([]), np.array([])

        return np.concatenate(all_f1_list), np.concatenate(all_f2_list), np.concatenate(all_tau_list)

    def _draw_formant_density_layer(self, ax, data_entries, show_raw=False, show_contours=True, bw_method=None, alpha_max=0.58, zorder=1, transform_fn=None, is_preview=True):
        self._report_export_progress(0.24, "正在收集共振峰时空密度数据...")
        all_f1, all_f2, all_tau = self._get_formant_density_points(data_entries, transform_fn=transform_fn)
        if len(all_f1) < 4:
            return None, [], []

        finite_mask = np.isfinite(all_f1) & np.isfinite(all_f2) & np.isfinite(all_tau)
        all_f1, all_f2, all_tau = all_f1[finite_mask], all_f2[finite_mask], all_tau[finite_mask]
        if len(all_f1) < 4:
            return None, [], []

        f1_p1, f1_p99 = np.percentile(all_f1, 1.0), np.percentile(all_f1, 99.0)
        f2_p1, f2_p99 = np.percentile(all_f2, 1.0), np.percentile(all_f2, 99.0)

        norm_mode = str(self.get_param('formant_normalization', '原始频率 (Hz)') or '原始频率 (Hz)')
        is_hz = "Hz" in norm_mode

        f1_pad = (f1_p99 - f1_p1) * 0.16 if f1_p99 > f1_p1 else (100.0 if is_hz else 0.5)
        f2_pad = (f2_p99 - f2_p1) * 0.16 if f2_p99 > f2_p1 else (150.0 if is_hz else 0.8)

        f1_grid_min, f1_grid_max = (max(50.0, f1_p1 - f1_pad) if is_hz else (f1_p1 - f1_pad)), f1_p99 + f1_pad
        f2_grid_min, f2_grid_max = (max(500.0, f2_p1 - f2_pad) if is_hz else (f2_p1 - f2_pad)), f2_p99 + f2_pad

        high_prec = (not is_preview) or bool(self.get_param('high_precision', False))
        if not high_prec and len(all_f1) > 4000:
            step = len(all_f1) // 4000
            all_f1 = all_f1[::step]
            all_f2 = all_f2[::step]
            all_tau = all_tau[::step]

        grid_n = 190 if high_prec else 70
        grid_f2, grid_f1 = np.meshgrid(
            np.linspace(f2_grid_min, f2_grid_max, grid_n),
            np.linspace(f1_grid_min, f1_grid_max, grid_n)
        )
        grid_points = np.vstack([grid_f2.ravel(), grid_f1.ravel()])

        try:
            bw = float(bw_method if bw_method is not None else self.get_param('formant_density_bw', 0.14))
        except (TypeError, ValueError):
            bw = 0.14
        bw = float(np.clip(bw, 0.06, 0.32))

        tau_layers = np.linspace(0.0, 1.0, 9 if high_prec else 5)
        kde_layers = []
        sigma_tau = 0.11
        for layer_idx, tau_k in enumerate(tau_layers):
            self._check_export_cancelled()
            self._report_export_progress(
                0.30 + 0.44 * (layer_idx / max(1, len(tau_layers))),
                f"正在计算时空密度层 {layer_idx + 1}/{len(tau_layers)}..."
            )
            weights = np.exp(-((all_tau - tau_k) ** 2) / (2 * sigma_tau ** 2))
            if np.sum(weights) < 1e-5:
                kde_layers.append(np.zeros(grid_f2.shape))
                continue
            try:
                kde = gaussian_kde(np.vstack([all_f2, all_f1]), weights=weights, bw_method=bw)
                kde_layers.append(kde(grid_points).reshape(grid_f2.shape) * np.sum(weights))
            except Exception:
                kde_layers.append(np.zeros(grid_f2.shape))

        kde_stack = np.stack(kde_layers, axis=0)
        self._report_export_progress(0.76, "正在合成时空密度热力图...")
        D = np.sum(kde_stack, axis=0)
        TD = np.sum(tau_layers[:, np.newaxis, np.newaxis] * kde_stack, axis=0)

        try:
            from scipy.ndimage import gaussian_filter
            D = gaussian_filter(D, sigma=0.65)
            TD = gaussian_filter(TD, sigma=0.65)
        except Exception:
            pass

        D_max = _safe_nanmax(D, default=0.0) if np.size(D) else 0.0
        if D_max <= 0:
            return None, all_f1.tolist(), all_f2.tolist()

        with np.errstate(divide='ignore', invalid='ignore'):
            T = np.nan_to_num(TD / D, nan=0.0)
        density_norm = D / D_max
        low = 0.025
        alpha = np.clip((density_norm - low) / (1.0 - low), 0.0, 1.0) ** 0.52
        alpha = np.clip(alpha * alpha_max, 0.0, alpha_max)

        time_cmap = self._get_formant_time_cmap()
        color_rgba = time_cmap(T)
        color_rgba[:, :, 3] = alpha
        ax.imshow(
            color_rgba,
            extent=[f2_grid_min, f2_grid_max, f1_grid_min, f1_grid_max],
            origin='lower',
            aspect='auto',
            interpolation='bicubic',
            zorder=zorder
        )

        if show_contours:
            self._report_export_progress(0.84, "正在绘制等密度轮廓线...")
            contour_levels = np.linspace(D_max * 0.12, D_max * 0.88, 6)
            ax.contour(grid_f2, grid_f1, D, levels=contour_levels, colors='#111827', alpha=0.34, linewidths=0.75, zorder=zorder + 0.2)

        if show_raw:
            ax.scatter(all_f2, all_f1, c=all_tau, cmap=time_cmap, s=9, alpha=0.15, edgecolors='none', zorder=zorder + 0.4)

        from matplotlib.colors import Normalize
        sm = plt.cm.ScalarMappable(cmap=time_cmap, norm=Normalize(vmin=0.0, vmax=100.0))
        sm.set_array([])
        return sm, all_f1.tolist(), all_f2.tolist()

    def _draw_formant_space_panel(self, ax, data_entries, groupby_val, label_mode, ellipse_mode, show_raw, time_gradient, density_overlay, density_sm_holder=None, transform_fn=None, fixed_limits=None, is_preview=True):
        ax.set_facecolor("#FFFFFF")
        ax._is_vowel_space_ax = True
        ax.grid(True, linestyle="--", alpha=0.25, linewidth=0.8)

        show_convex_hull = bool(self.get_param('show_convex_hull', False)) or bool(self.get_param('formant_show_convex_hull', False))
        show_diphthong_arrows = bool(self.get_param('show_diphthong_arrows', False)) or bool(self.get_param('formant_show_diphthong_arrows', False))

        if density_overlay:
            sm, density_f1, density_f2 = self._draw_formant_density_layer(
                ax,
                data_entries,
                show_raw=self.get_param('formant_density_show_raw', False),
                show_contours=self.get_param('formant_density_show_contours', True),
                bw_method=self.get_param('formant_density_bw', 0.14),
                alpha_max=0.52,
                zorder=1,
                transform_fn=transform_fn,
                is_preview=is_preview
            )
            if sm is not None and density_sm_holder is not None and density_sm_holder.get('sm') is None:
                density_sm_holder['sm'] = sm
        else:
            density_f1, density_f2 = [], []

        category_data = {}

        for entry in data_entries:
            for syl in entry.get('syl_formants', []):
                c_s, c_e = syl['bounds']
                xs = np.asarray(entry.get('raw_xs', []), dtype=float)
                f1_arr = np.asarray(entry.get('raw_f1', []), dtype=float)
                f2_arr = np.asarray(entry.get('raw_f2', []), dtype=float)

                if len(xs) > 0 and len(f1_arr) > 0 and len(f2_arr) > 0:
                    mask = (xs >= c_s) & (xs <= c_e) & np.isfinite(f1_arr) & np.isfinite(f2_arr) & (f2_arr > f1_arr)
                    s_f1 = f1_arr[mask]
                    s_f2 = f2_arr[mask]
                else:
                    s_f1 = np.asarray(syl.get('f1', []), dtype=float)
                    s_f2 = np.asarray(syl.get('f2', []), dtype=float)
                    mask_s = np.isfinite(s_f1) & np.isfinite(s_f2) & (s_f2 > s_f1)
                    s_f1, s_f2 = s_f1[mask_s], s_f2[mask_s]

                if len(s_f1) == 0:
                    continue

                spk_name = entry.get('speaker_name')
                if transform_fn is not None:
                    try:
                        s_f1, s_f2 = transform_fn(s_f1, s_f2, speaker_name=spk_name)
                    except TypeError:
                        s_f1, s_f2 = transform_fn(s_f1, s_f2)

                cat = self._get_syl_category(entry, syl, groupby_val)
                if cat not in category_data:
                    category_data[cat] = {
                        'f1': [], 'f2': [], 'token_f1': [], 'token_f2': [],
                        'entries_labels': [], 'syl_chars': [],
                        'trajs_f1': [], 'trajs_f2': []
                    }

                category_data[cat]['f1'].append(s_f1)
                category_data[cat]['f2'].append(s_f2)
                category_data[cat]['entries_labels'].append(entry['label'])
                category_data[cat]['syl_chars'].append(syl['char'])

                traj_f1 = np.asarray(syl.get('f1', []), dtype=float)
                traj_f2 = np.asarray(syl.get('f2', []), dtype=float)
                if len(traj_f1) > 0 and len(traj_f2) > 0:
                    if transform_fn is not None:
                        try:
                            traj_f1, traj_f2 = transform_fn(traj_f1, traj_f2, speaker_name=spk_name)
                        except TypeError:
                            traj_f1, traj_f2 = transform_fn(traj_f1, traj_f2)
                    category_data[cat]['trajs_f1'].append(traj_f1)
                    category_data[cat]['trajs_f2'].append(traj_f2)
                    n_t = len(traj_f1)
                    i0 = int(math.floor(0.2 * n_t)) if n_t >= 5 else 0
                    i1 = max(i0 + 1, int(math.ceil(0.8 * n_t))) if n_t >= 5 else n_t
                    sub1, sub2 = traj_f1[i0:i1], traj_f2[i0:i1]
                    v_sub = np.isfinite(sub1) & np.isfinite(sub2)
                    if np.any(v_sub):
                        tok_f1 = float(np.mean(sub1[v_sub]))
                        tok_f2 = float(np.mean(sub2[v_sub]))
                    else:
                        tok_f1 = _safe_nanmean(s_f1)
                        tok_f2 = _safe_nanmean(s_f2)
                else:
                    tok_f1 = _safe_nanmean(s_f1)
                    tok_f2 = _safe_nanmean(s_f2)

                if np.isfinite(tok_f1) and np.isfinite(tok_f2):
                    category_data[cat]['token_f1'].append(tok_f1)
                    category_data[cat]['token_f2'].append(tok_f2)

        if not category_data:
            ax.text(0.5, 0.5, "没有找到有效的共振峰分析数据！", ha='center', va='center', color='red', fontsize=12)
            return [], []

        categories = sorted(list(category_data.keys()))
        cat_styles = {cat: self._get_series_style(i) for i, cat in enumerate(categories)}
        cat_colors = {cat: cat_styles[cat]["color"] for cat in categories}

        all_f1_plotted_list = []
        all_f2_plotted_list = []
        centroids_f2_f1 = []
        all_tokens_f2_f1 = []
        trajectory_lengths = {}

        for cat in categories:
            color = cat_colors[cat]
            if not category_data[cat]['f1']:
                continue

            f1_list = np.concatenate(category_data[cat]['f1'])
            f2_list = np.concatenate(category_data[cat]['f2'])
            category_data[cat]['f1_concat'] = f1_list
            category_data[cat]['f2_concat'] = f2_list

            if len(f1_list) == 0:
                continue

            all_f1_plotted_list.append(f1_list)
            all_f2_plotted_list.append(f2_list)

            if show_raw:
                ax.scatter(f2_list, f1_list, color=color, s=14, alpha=0.20 if density_overlay else 0.14, edgecolors='none', zorder=3)

        for cat in categories:
            color = cat_colors[cat]
            marker = cat_styles[cat]["marker"]
            if 'f1_concat' not in category_data[cat]:
                continue
            f1_list = category_data[cat]['f1_concat']
            f2_list = category_data[cat]['f2_concat']

            trajs_f1 = np.array(category_data[cat]['trajs_f1'])
            trajs_f2 = np.array(category_data[cat]['trajs_f2'])

            tok_f1_arr = np.asarray(category_data[cat].get('token_f1', []), dtype=float)
            tok_f2_arr = np.asarray(category_data[cat].get('token_f2', []), dtype=float)
            if len(tok_f1_arr) > 0 and len(tok_f2_arr) > 0:
                for tf2, tf1 in zip(tok_f2_arr, tok_f1_arr):
                    if np.isfinite(tf2) and np.isfinite(tf1):
                        all_tokens_f2_f1.append((float(tf2), float(tf1)))

            mean_f1 = _safe_nanmean(tok_f1_arr) if len(tok_f1_arr) > 0 else _safe_nanmean(f1_list)
            mean_f2 = _safe_nanmean(tok_f2_arr) if len(tok_f2_arr) > 0 else _safe_nanmean(f2_list)
            if np.isfinite(mean_f2) and np.isfinite(mean_f1):
                centroids_f2_f1.append((float(mean_f2), float(mean_f1)))

            lbl_x = np.nan
            lbl_y = np.nan

            # Diphthong 20% -> 50% -> 80% directional vector arrows & Trajectory Length (TL)
            if (show_diphthong_arrows or time_gradient) and len(trajs_f1) > 0 and len(trajs_f2) > 0:
                with warnings.catch_warnings(), np.errstate(all='ignore'):
                    warnings.simplefilter("ignore", category=RuntimeWarning)
                    mean_traj_f1 = _safe_nanmean(trajs_f1, axis=0)
                    mean_traj_f2 = _safe_nanmean(trajs_f2, axis=0)

                valid_mask = np.isfinite(mean_traj_f1) & np.isfinite(mean_traj_f2)
                if np.count_nonzero(valid_mask) >= 2:
                    v_f1 = mean_traj_f1[valid_mask]
                    v_f2 = mean_traj_f2[valid_mask]
                    n_pts = len(v_f1)

                    # Extract 20% -> 50% -> 80% trajectory segment
                    tau_src = np.linspace(0.0, 1.0, n_pts)
                    tau_20_80 = np.linspace(0.20, 0.80, max(7, n_pts))
                    seg_f1 = np.interp(tau_20_80, tau_src, v_f1)
                    seg_f2 = np.interp(tau_20_80, tau_src, v_f2)
                    tl_val = float(np.sum(np.sqrt(np.diff(seg_f1) ** 2 + np.diff(seg_f2) ** 2)))
                    trajectory_lengths[str(cat)] = tl_val

                    if show_diphthong_arrows:
                        ax.plot(
                            seg_f2, seg_f1,
                            color=color, linewidth=2.0, alpha=0.88,
                            label=f"{cat} (TL={tl_val:.1f})", zorder=5.5,
                        )
                        # Onset (20%) and midpoint (50%) markers
                        f1_20, f2_20 = float(seg_f1[0]), float(seg_f2[0])
                        f1_50, f2_50 = float(seg_f1[len(seg_f1) // 2]), float(seg_f2[len(seg_f2) // 2])
                        f1_80, f2_80 = float(seg_f1[-1]), float(seg_f2[-1])
                        f1_prev, f2_prev = float(seg_f1[-2]), float(seg_f2[-2])
                        ax.scatter([f2_20], [f1_20], color="white", edgecolors=color, s=42, linewidth=1.4, zorder=6)
                        ax.scatter([f2_50], [f1_50], color=color, marker=marker, s=48, edgecolors="black", linewidth=0.7, zorder=6)
                        ax.annotate(
                            "",
                            xy=(f2_80, f1_80),
                            xytext=(f2_prev, f1_prev),
                            arrowprops=dict(arrowstyle="-|>,head_width=0.38,head_length=0.55", color=color, lw=2.0),
                            zorder=6.8,
                        )
                        lbl_x, lbl_y = f2_50, f1_50
                    elif time_gradient:
                        ax.plot(v_f2, v_f1, color=color, linewidth=1.2, alpha=0.55, label=str(cat), zorder=5)
                        cmap_t = self._get_formant_time_cmap()
                        marker_colors = cmap_t(np.linspace(0, 1, n_pts))
                        for i in range(n_pts):
                            ax.scatter(v_f2[i], v_f1[i], color=marker_colors[i], s=80,
                                       edgecolors='black', linewidth=0.8, zorder=6)
                        mid_idx = n_pts // 2
                        lbl_x = v_f2[mid_idx]
                        lbl_y = v_f1[mid_idx]

            ellipse_f1_pts = tok_f1_arr if len(tok_f1_arr) >= 3 else f1_list
            ellipse_f2_pts = tok_f2_arr if len(tok_f2_arr) >= 3 else f2_list

            if np.isnan(lbl_x) or np.isnan(lbl_y):
                ax.scatter(mean_f2, mean_f1, color=color, s=135, marker=marker, edgecolors='black', linewidth=1.1, zorder=6, label=str(cat))
                lbl_x = mean_f2
                lbl_y = mean_f1

            if not np.isnan(lbl_x) and not np.isnan(lbl_y):
                lbl_text = None
                if label_mode == "显示分组标签":
                    lbl_text = str(cat)
                elif label_mode == "显示单字标签":
                    syl_chars = category_data[cat]['syl_chars']
                    lbl_text = str(max(set(syl_chars), key=syl_chars.count) if syl_chars else cat)
                elif label_mode == "显示词语标签":
                    entries_labels = category_data[cat]['entries_labels']
                    lbl_text = str(cat) if groupby_val == "按词语" else str(max(set(entries_labels), key=entries_labels.count) if entries_labels else cat)
                if lbl_text:
                    ax.annotate(
                        lbl_text,
                        xy=(lbl_x, lbl_y),
                        xytext=(0, 8),
                        textcoords="offset points",
                        fontsize=10.5,
                        fontweight='bold',
                        color='#111827',
                        ha='center',
                        va='bottom',
                        zorder=7,
                        bbox=dict(boxstyle="round,pad=0.2", fc="white", ec=color, alpha=0.85, lw=1),
                    )

            if "1-sigma" in str(ellipse_mode) or "68%" in str(ellipse_mode):
                ax._error_band_label = "Ellipse: 68% (1σ)"
                self._draw_confidence_ellipse(ellipse_f2_pts, ellipse_f1_pts, ax, n_std=1.5152, facecolor='none', edgecolor=color, linestyle='--', linewidth=1.5, zorder=4)
            elif "2-sigma" in str(ellipse_mode) or "95%" in str(ellipse_mode):
                ax._error_band_label = "Ellipse: 95% CI"
                self._draw_confidence_ellipse(ellipse_f2_pts, ellipse_f1_pts, ax, n_std=2.4477, facecolor='none', edgecolor=color, linestyle='-.', linewidth=1.5, zorder=4)

        # Outer Convex Hull & Vowel Space Area (VSA)
        vsa_area = 0.0
        if show_convex_hull:
            hull_input = centroids_f2_f1 if len(centroids_f2_f1) >= 3 else all_tokens_f2_f1
            hull_verts, vsa_area = _compute_2d_convex_hull_and_area(hull_input)
            if len(hull_verts) >= 3:
                closed_verts = np.vstack([hull_verts, hull_verts[0]])
                norm_mode_str = str(self.get_param('formant_normalization', '原始频率 (Hz)') or '原始频率 (Hz)')
                if "Bark" in norm_mode_str:
                    unit_sq = "Bark²"
                elif "ERB" in norm_mode_str:
                    unit_sq = "ERB²"
                elif "Lobanov" in norm_mode_str or "归一化" in norm_mode_str:
                    unit_sq = "SD²"
                else:
                    unit_sq = "Hz²"
                ax.plot(
                    closed_verts[:, 0], closed_verts[:, 1],
                    linestyle="--", linewidth=1.6, color="#1F2937",
                    alpha=0.85, zorder=3.5, label=f"元音空间凸包 (VSA={vsa_area:.2f} {unit_sq})",
                )
                ax.fill(
                    closed_verts[:, 0], closed_verts[:, 1],
                    color="#94A3B8", alpha=0.08, zorder=2,
                )
                ax.text(
                    0.03, 0.04,
                    f"VSA = {vsa_area:.2f} {unit_sq}",
                    transform=ax.transAxes,
                    fontsize=9.5,
                    fontweight="bold",
                    color="#111827",
                    bbox=dict(boxstyle="round,pad=0.25", fc="#FFFFFF", ec="#9CA3AF", alpha=0.9),
                    zorder=8,
                )

        ax._vsa_area = float(vsa_area)
        ax._trajectory_lengths = trajectory_lengths

        ax.invert_xaxis()
        ax.invert_yaxis()

        if fixed_limits is not None:
            ymin, ymax, xmin, xmax = fixed_limits
            ax.set_ylim(ymin, ymax)
            ax.set_xlim(xmin, xmax)
        elif all_f1_plotted_list and all_f2_plotted_list:
            if density_f1 and isinstance(density_f1, list):
                all_f1_plotted_list.append(np.array(density_f1))
            elif density_f1 is not None and len(density_f1) > 0:
                all_f1_plotted_list.append(density_f1)

            if density_f2 and isinstance(density_f2, list):
                all_f2_plotted_list.append(np.array(density_f2))
            elif density_f2 is not None and len(density_f2) > 0:
                all_f2_plotted_list.append(density_f2)

            limit_f1 = np.concatenate(all_f1_plotted_list)
            limit_f2 = np.concatenate(all_f2_plotted_list)
            f1_p1, f1_p99 = np.percentile(limit_f1, 1.0), np.percentile(limit_f1, 99.0)
            f2_p1, f2_p99 = np.percentile(limit_f2, 1.0), np.percentile(limit_f2, 99.0)

            norm_mode = str(self.get_param('formant_normalization', '原始频率 (Hz)') or '原始频率 (Hz)')
            is_hz = "Hz" in norm_mode

            f1_pad = (f1_p99 - f1_p1) * 0.15 if f1_p99 > f1_p1 else (100.0 if is_hz else 0.5)
            f2_pad = (f2_p99 - f2_p1) * 0.15 if f2_p99 > f2_p1 else (150.0 if is_hz else 0.8)

            ax.set_ylim(f1_p99 + f1_pad, max(50.0, f1_p1 - f1_pad) if is_hz else (f1_p1 - f1_pad))
            ax.set_xlim(f2_p99 + f2_pad, max(500.0, f2_p1 - f2_pad) if is_hz else (f2_p1 - f2_pad))

        norm_mode = str(self.get_param('formant_normalization', '原始频率 (Hz)') or '原始频率 (Hz)')
        if "Bark" in norm_mode or "巴克" in norm_mode:
            ax.set_xlabel("F2 (Bark)", fontsize=12, fontweight='bold', labelpad=10)
            ax.set_ylabel("F1 (Bark)", fontsize=12, fontweight='bold', labelpad=10)
        elif "ERB" in norm_mode or "等效矩形" in norm_mode:
            ax.set_xlabel("F2 (ERB)", fontsize=12, fontweight='bold', labelpad=10)
            ax.set_ylabel("F1 (ERB)", fontsize=12, fontweight='bold', labelpad=10)
        elif "Mel" in norm_mode or "美尔" in norm_mode:
            ax.set_xlabel("F2 (Mel)", fontsize=12, fontweight='bold', labelpad=10)
            ax.set_ylabel("F1 (Mel)", fontsize=12, fontweight='bold', labelpad=10)
        elif "Lobanov" in norm_mode or "归一化" in norm_mode or "L-归一化" in norm_mode:
            ax.set_xlabel("F2 (Z-score)", fontsize=12, fontweight='bold', labelpad=10)
            ax.set_ylabel("F1 (Z-score)", fontsize=12, fontweight='bold', labelpad=10)
        else:
            ax.set_xlabel("F2 (Hz)", fontsize=12, fontweight='bold', labelpad=10)
            ax.set_ylabel("F1 (Hz)", fontsize=12, fontweight='bold', labelpad=10)

        all_f1_res = np.concatenate(all_f1_plotted_list) if all_f1_plotted_list else np.array([])
        all_f2_res = np.concatenate(all_f2_plotted_list) if all_f2_plotted_list else np.array([])
        return all_f1_res, all_f2_res

    def _add_formant_density_colorbar(self, fig, axes, sm):
        axes_list = [ax for ax in axes if getattr(ax, "get_visible", lambda: True)()]
        if not axes_list:
            return None

        legend_outside = bool(self.get_param('legend_outside', False))
        legend_loc = str(self.get_param('legend_loc', '右上'))
        right_legend = legend_outside and legend_loc.startswith("右")
        left_legend = legend_outside and legend_loc.startswith("左")

        if right_legend:
            fig.subplots_adjust(left=0.08, right=0.76, top=0.91, bottom=0.10, wspace=0.26, hspace=0.28)
            cax = fig.add_axes([0.79, 0.18, 0.018, 0.66])
        elif left_legend:
            fig.subplots_adjust(left=0.18, right=0.88, top=0.91, bottom=0.10, wspace=0.26, hspace=0.28)
            cax = fig.add_axes([0.91, 0.18, 0.018, 0.66])
        else:
            fig.subplots_adjust(left=0.08, right=0.88, top=0.91, bottom=0.10, wspace=0.24, hspace=0.28)
            cax = fig.add_axes([0.91, 0.18, 0.018, 0.66])

        cax._is_colorbar_axis = True
        cb = fig.colorbar(sm, cax=cax, orientation='vertical')
        cb.set_label("相对时间 0-100% (红=早, 蓝=晚)", fontsize=10, fontweight='bold')
        setattr(fig, "_phontracer_skip_tight_layout", True)
        return cb

    def _build_formant_normalizer(self, ref_entries, mode):
        mode_str = str(mode or "原始频率 (Hz)")
        if not ref_entries:
            def _identity(f1, f2, speaker_name=None, f3=None):
                if f3 is not None:
                    return np.asarray(f1, dtype=float), np.asarray(f2, dtype=float), np.asarray(f3, dtype=float)
                return np.asarray(f1, dtype=float), np.asarray(f2, dtype=float)
            return _identity

        if "Bark" in mode_str or "巴克" in mode_str or mode_str.lower() == "bark":
            def to_bark(f1, f2, speaker_name=None, f3=None):
                f1 = np.asarray(f1, dtype=float)
                f2 = np.asarray(f2, dtype=float)
                b1 = 26.81 * f1 / (1960.0 + f1) - 0.53
                b2 = 26.81 * f2 / (1960.0 + f2) - 0.53
                if f3 is not None:
                    f3 = np.asarray(f3, dtype=float)
                    b3 = 26.81 * f3 / (1960.0 + f3) - 0.53
                    return b1, b2, b3
                return b1, b2
            return to_bark

        elif "ERB" in mode_str or "等效矩形" in mode_str or mode_str.lower() == "erb":
            def to_erb(f1, f2, speaker_name=None, f3=None):
                f1 = np.asarray(f1, dtype=float)
                f2 = np.asarray(f2, dtype=float)
                e1 = 21.4 * np.log10(1.0 + 0.00437 * np.maximum(f1, 0.0))
                e2 = 21.4 * np.log10(1.0 + 0.00437 * np.maximum(f2, 0.0))
                if f3 is not None:
                    f3 = np.asarray(f3, dtype=float)
                    e3 = 21.4 * np.log10(1.0 + 0.00437 * np.maximum(f3, 0.0))
                    return e1, e2, e3
                return e1, e2
            return to_erb

        elif "Mel" in mode_str or "美尔" in mode_str or mode_str.lower() == "mel":
            def to_mel(f1, f2, speaker_name=None, f3=None):
                f1 = np.asarray(f1, dtype=float)
                f2 = np.asarray(f2, dtype=float)
                m1 = 2595.0 * np.log10(1.0 + np.maximum(f1, 0.0) / 700.0)
                m2 = 2595.0 * np.log10(1.0 + np.maximum(f2, 0.0) / 700.0)
                if f3 is not None:
                    f3 = np.asarray(f3, dtype=float)
                    m3 = 2595.0 * np.log10(1.0 + np.maximum(f3, 0.0) / 700.0)
                    return m1, m2, m3
                return m1, m2
            return to_mel

        elif "Lobanov" in mode_str or "归一化" in mode_str or "L-归一化" in mode_str or "z-score" in mode_str.lower():
            # 按每位发音人 (per-speaker) 独立计算其元音音节稳态中段均值的均值与标准差 (F1, F2, F3)
            spk_tokens = {}
            spk_f3_tokens = {}
            spk_raw_fallback = {}
            for entry in ref_entries:
                spk = entry.get('speaker_name') or '__default__'
                spk_tokens.setdefault(spk, [])
                spk_f3_tokens.setdefault(spk, [])
                spk_raw_fallback.setdefault(spk, ([], [], []))

                syl_formants = entry.get('syl_formants', [])
                if syl_formants:
                    for syl in syl_formants:
                        f1_pts = np.asarray(syl.get('f1', []), dtype=float)
                        f2_pts = np.asarray(syl.get('f2', []), dtype=float)
                        f3_pts = np.asarray(syl.get('f3', []), dtype=float)
                        n_p = len(f1_pts)
                        if n_p > 0 and len(f2_pts) == n_p:
                            i0 = int(math.floor(0.2 * n_p)) if n_p >= 5 else 0
                            i1 = max(i0 + 1, int(math.ceil(0.8 * n_p))) if n_p >= 5 else n_p
                            sub1, sub2 = f1_pts[i0:i1], f2_pts[i0:i1]
                            valid_sub = np.isfinite(sub1) & np.isfinite(sub2) & (sub1 > 0) & (sub2 > sub1)
                            if np.any(valid_sub):
                                spk_tokens[spk].append((float(np.mean(sub1[valid_sub])), float(np.mean(sub2[valid_sub]))))
                            else:
                                valid_all = np.isfinite(f1_pts) & np.isfinite(f2_pts) & (f1_pts > 0) & (f2_pts > f1_pts)
                                if np.any(valid_all):
                                    spk_tokens[spk].append((float(np.mean(f1_pts[valid_all])), float(np.mean(f2_pts[valid_all]))))
                        if len(f3_pts) > 0:
                            vf3 = f3_pts[np.isfinite(f3_pts) & (f3_pts > 0)]
                            if vf3.size > 0:
                                spk_f3_tokens[spk].append(float(np.mean(vf3)))
                else:
                    f1_arr = np.asarray(entry.get('raw_f1', []), dtype=float)
                    f2_arr = np.asarray(entry.get('raw_f2', []), dtype=float)
                    valid = np.isfinite(f1_arr) & np.isfinite(f2_arr) & (f1_arr > 0) & (f2_arr > f1_arr)
                    if np.any(valid):
                        spk_tokens[spk].append((float(np.mean(f1_arr[valid])), float(np.mean(f2_arr[valid]))))

                f1_arr = np.asarray(entry.get('raw_f1', []), dtype=float)
                f2_arr = np.asarray(entry.get('raw_f2', []), dtype=float)
                f3_arr = np.asarray(entry.get('raw_f3', []), dtype=float)
                valid_raw = np.isfinite(f1_arr) & np.isfinite(f2_arr) & (f1_arr > 0) & (f2_arr > f1_arr)
                if np.any(valid_raw):
                    spk_raw_fallback[spk][0].extend(f1_arr[valid_raw].tolist())
                    spk_raw_fallback[spk][1].extend(f2_arr[valid_raw].tolist())
                if f3_arr.size > 0:
                    vf3_r = f3_arr[np.isfinite(f3_arr) & (f3_arr > 0)]
                    if vf3_r.size > 0:
                        spk_raw_fallback[spk][2].extend(vf3_r.tolist())

            spk_stats = {}
            all_tok_f1, all_tok_f2, all_tok_f3 = [], [], []
            for spk, pairs in spk_tokens.items():
                if len(pairs) >= 3:
                    f1s = [p[0] for p in pairs]
                    f2s = [p[1] for p in pairs]
                elif spk_raw_fallback.get(spk) and len(spk_raw_fallback[spk][0]) >= 2:
                    f1s, f2s = spk_raw_fallback[spk][0], spk_raw_fallback[spk][1]
                elif pairs:
                    f1s = [p[0] for p in pairs]
                    f2s = [p[1] for p in pairs]
                else:
                    continue

                f3s = spk_f3_tokens.get(spk) or spk_raw_fallback.get(spk, ([], [], []))[2]
                all_tok_f1.extend(f1s)
                all_tok_f2.extend(f2s)
                all_tok_f3.extend(f3s)

                m1 = float(np.mean(f1s))
                s1 = float(np.std(f1s, ddof=1)) if len(f1s) > 1 else float(np.std(f1s))
                if s1 < 1e-3:
                    s1 = 100.0
                m2 = float(np.mean(f2s))
                s2 = float(np.std(f2s, ddof=1)) if len(f2s) > 1 else float(np.std(f2s))
                if s2 < 1e-3:
                    s2 = 250.0
                m3 = float(np.mean(f3s)) if f3s else 2600.0
                s3 = (float(np.std(f3s, ddof=1)) if len(f3s) > 1 else 300.0) if f3s else 300.0
                if s3 < 1e-3:
                    s3 = 300.0
                spk_stats[spk] = (m1, s1, m2, s2, m3, s3)

            fb_m1 = float(np.mean(all_tok_f1)) if all_tok_f1 else 500.0
            fb_s1 = float(np.std(all_tok_f1, ddof=1)) if len(all_tok_f1) > 1 else 100.0
            if fb_s1 < 1e-3:
                fb_s1 = 100.0
            fb_m2 = float(np.mean(all_tok_f2)) if all_tok_f2 else 1500.0
            fb_s2 = float(np.std(all_tok_f2, ddof=1)) if len(all_tok_f2) > 1 else 250.0
            if fb_s2 < 1e-3:
                fb_s2 = 250.0
            fb_m3 = float(np.mean(all_tok_f3)) if all_tok_f3 else 2600.0
            fb_s3 = float(np.std(all_tok_f3, ddof=1)) if len(all_tok_f3) > 1 else 300.0
            if fb_s3 < 1e-3:
                fb_s3 = 300.0
            fallback_stat = (fb_m1, fb_s1, fb_m2, fb_s2, fb_m3, fb_s3)

            def to_lobanov(f1, f2, speaker_name=None, f3=None):
                m1, s1, m2, s2, m3, s3 = spk_stats.get(speaker_name, fallback_stat) if speaker_name else fallback_stat
                f1 = np.asarray(f1, dtype=float)
                f2 = np.asarray(f2, dtype=float)
                z1 = (f1 - m1) / s1
                z2 = (f2 - m2) / s2
                if f3 is not None:
                    f3 = np.asarray(f3, dtype=float)
                    z3 = (f3 - m3) / s3
                    return z1, z2, z3
                return z1, z2

            return to_lobanov

        else:
            def _raw_hz(f1, f2, speaker_name=None, f3=None):
                if f3 is not None:
                    return np.asarray(f1, dtype=float), np.asarray(f2, dtype=float), np.asarray(f3, dtype=float)
                return np.asarray(f1, dtype=float), np.asarray(f2, dtype=float)
            return _raw_hz

    def _plot_formant_vowel_space(self, data_entries, group_key, scale, is_preview=True):
        groupby_val = self._resolve_color_by()
        label_mode = self.get_param('formant_label_mode', '显示分组标签')
        ellipse_mode = self.get_param('formant_ellipse', '95% 置信椭圆' if self._normalize_work_mode() == 'publication' else '1-sigma 置信椭圆')
        show_raw = self.get_param('formant_show_raw', True)
        time_gradient = self.get_param('formant_time_gradient', False)
        density_overlay = bool(self.get_param('formant_density_overlay', False))
        facet_mode = self._resolve_facet_by(chart_type="formant_space")

        normalization_mode = self.get_param('formant_normalization', None) or self.get_param('scale', '原始频率 (Hz)')
        if str(normalization_mode) in ("t_value", "T 值", "T 值 (五度标调)"):
            normalization_mode = "原始频率 (Hz)"
        lock_axes = bool(self.get_param('formant_axis_lock', False))
        axis_ref_entries = self.get_param('formant_axis_ref_entries', data_entries)

        transform_fn = self._build_formant_normalizer(axis_ref_entries, normalization_mode)

        fixed_limits = None
        if lock_axes:
            ref_f1, ref_f2 = [], []
            for entry in axis_ref_entries:
                spk_name = entry.get('speaker_name')
                f1_arr = np.asarray(entry.get('raw_f1', []), dtype=float)
                f2_arr = np.asarray(entry.get('raw_f2', []), dtype=float)
                mask = np.isfinite(f1_arr) & np.isfinite(f2_arr) & (f2_arr > f1_arr)
                if np.any(mask):
                    tf1, tf2 = transform_fn(f1_arr[mask], f2_arr[mask], speaker_name=spk_name)
                    ref_f1.extend(np.asarray(tf1, dtype=float).tolist())
                    ref_f2.extend(np.asarray(tf2, dtype=float).tolist())
                for syl in entry.get('syl_formants', []):
                    f1_pts = np.asarray(syl.get('f1', []), dtype=float)
                    f2_pts = np.asarray(syl.get('f2', []), dtype=float)
                    valid_syl = np.isfinite(f1_pts) & np.isfinite(f2_pts)
                    if np.any(valid_syl):
                        tf1, tf2 = transform_fn(f1_pts[valid_syl], f2_pts[valid_syl], speaker_name=spk_name)
                        ref_f1.extend(np.asarray(tf1, dtype=float).tolist())
                        ref_f2.extend(np.asarray(tf2, dtype=float).tolist())

            if ref_f1 and ref_f2:
                ref_f1, ref_f2 = np.asarray(ref_f1, dtype=float), np.asarray(ref_f2, dtype=float)
                f1_p1, f1_p99 = np.percentile(ref_f1, 1.0), np.percentile(ref_f1, 99.0)
                f2_p1, f2_p99 = np.percentile(ref_f2, 1.0), np.percentile(ref_f2, 99.0)

                is_hz = "Hz" in str(normalization_mode)
                f1_pad = (f1_p99 - f1_p1) * 0.15 if f1_p99 > f1_p1 else (100.0 if is_hz else 0.5)
                f2_pad = (f2_p99 - f2_p1) * 0.15 if f2_p99 > f2_p1 else (150.0 if is_hz else 0.8)

                if is_hz:
                    ymin, ymax = f1_p99 + f1_pad, max(50.0, f1_p1 - f1_pad)
                    xmin, xmax = f2_p99 + f2_pad, max(500.0, f2_p1 - f2_pad)
                else:
                    ymin, ymax = f1_p99 + f1_pad, f1_p1 - f1_pad
                    xmin, xmax = f2_p99 + f2_pad, f2_p1 - f2_pad

                fixed_limits = (ymin, ymax, xmin, xmax)

        facet_specs = [("Default", data_entries)]
        if facet_mode == "group":
            facet_specs = [(g, [e for e in data_entries if e['group'] == g]) for g in sorted(set(e['group'] for e in data_entries))]
        elif facet_mode == "speaker":
            facet_specs = [(s, [e for e in data_entries if e['speaker_name'] == s]) for s in sorted(set(e['speaker_name'] for e in data_entries))]
        elif facet_mode == "syllable_position":
            max_syls = max((len(e.get('syl_formants', [])) for e in data_entries), default=0)
            facet_specs = [(f"第 {i + 1} 音节", self._slice_formant_entries_for_syllable(data_entries, i)) for i in range(max_syls)]

        facet_specs = [(name, entries) for name, entries in facet_specs if entries]
        if not facet_specs:
            fig, ax = plt.subplots(figsize=(8.6, 7.2))
            ax.text(0.5, 0.5, "没有找到有效的共振峰分析数据！", ha='center', va='center', color='red', fontsize=12)
            return fig

        n_facets = len(facet_specs)
        if n_facets == 1:
            fig, ax = plt.subplots(figsize=(8.6, 7.2))
            axes_flat = [ax]
        else:
            n_cols = min(2, n_facets)
            n_rows = math.ceil(n_facets / n_cols)
            fig, axes = plt.subplots(n_rows, n_cols, figsize=(8.0 * n_cols, 6.5 * n_rows), squeeze=False)
            axes_flat = axes.flatten()

        density_sm_holder = {'sm': None}
        fig_vsa_areas = {}
        fig_traj_lengths = {}
        for idx, (facet_name, facet_entries) in enumerate(facet_specs):
            ax = axes_flat[idx]
            self._draw_formant_space_panel(
                ax, facet_entries, groupby_val, label_mode, ellipse_mode,
                show_raw, time_gradient, density_overlay, density_sm_holder,
                transform_fn=transform_fn, fixed_limits=fixed_limits, is_preview=is_preview
            )
            fig_vsa_areas[str(facet_name)] = getattr(ax, "_vsa_area", 0.0)
            fig_traj_lengths.update(getattr(ax, "_trajectory_lengths", {}))
            title_text = "元音共振峰空间分布图" if facet_name == "Default" else str(facet_name)
            if len(title_text) > 28:
                title_text = title_text[:25] + "..."
            ax.set_title(title_text, fontsize=12 if n_facets > 1 else 13, fontweight='bold', pad=12)
            ax._original_title = title_text

        fig._vsa_area = next(iter(fig_vsa_areas.values()), 0.0)
        fig._vsa_areas = fig_vsa_areas
        fig._trajectory_lengths = fig_traj_lengths

        for idx in range(n_facets, len(axes_flat)):
            axes_flat[idx].set_visible(False)

        if density_overlay and density_sm_holder.get('sm') is not None:
            self._add_formant_density_colorbar(fig, axes_flat[:n_facets], density_sm_holder['sm'])

        legend_kwargs = self._get_legend_kwargs()
        legend_kwargs["fontsize"] = 9
        if density_overlay and self.get_param('legend_outside', False):
            legend_loc = str(self.get_param('legend_loc', '右上'))
            if legend_loc.startswith("右"):
                y_anchor = 1 if "上" in legend_loc else 0
                legend_kwargs["bbox_to_anchor"] = (1.34, y_anchor)
            elif legend_loc.startswith("左"):
                y_anchor = 1 if "上" in legend_loc else 0
                legend_kwargs["bbox_to_anchor"] = (-0.14, y_anchor)
        for ax in axes_flat[:n_facets]:
            handles, labels = ax.get_legend_handles_labels()
            if handles:
                ax.legend(**legend_kwargs)

        if not density_overlay:
            fig.tight_layout()
        return fig

    def _plot_formant_density_heatmap(self, data_entries, group_key, scale):
        show_raw = self.get_param('formant_density_show_raw', False)
        show_contours = self.get_param('formant_density_show_contours', True)
        facet_mode = self._resolve_facet_by(chart_type="formant_density", fallback="group")

        normalization_mode = self.get_param('formant_normalization', '原始频率 (Hz)')
        transform_fn = self._build_formant_normalizer(data_entries, normalization_mode)

        facet_specs = [("Default", data_entries)]
        if facet_mode == "group":
            facet_specs = [(g, [e for e in data_entries if e['group'] == g]) for g in sorted(set(e['group'] for e in data_entries))]
        elif facet_mode == "speaker":
            facet_specs = [(s, [e for e in data_entries if e['speaker_name'] == s]) for s in sorted(set(e['speaker_name'] for e in data_entries))]
        elif facet_mode == "syllable_position":
            max_syls = max((len(e.get('syl_formants', [])) for e in data_entries), default=0)
            facet_specs = [(f"第 {i + 1} 音节", self._slice_formant_entries_for_syllable(data_entries, i)) for i in range(max_syls)]
        elif facet_mode == "label":
            facet_specs = [(w, [e for e in data_entries if e['label'] == w]) for w in sorted(set(e['label'] for e in data_entries))]

        facet_specs = [(name, entries) for name, entries in facet_specs if entries]
        if not facet_specs:
            fig, ax = plt.subplots(figsize=(8.6, 7.2))
            ax.text(0.5, 0.5, "没有找到有效的共振峰时空密度数据！", ha='center', va='center', color='red', fontsize=12)
            return fig

        n_facets = len(facet_specs)
        if n_facets == 1:
            fig, ax = plt.subplots(figsize=(8.6, 7.2))
            axes_flat = [ax]
        else:
            n_cols = min(2, n_facets)
            n_rows = math.ceil(n_facets / n_cols)
            fig, axes = plt.subplots(n_rows, n_cols, figsize=(8.0 * n_cols, 6.2 * n_rows), squeeze=False)
            axes_flat = axes.flatten()

        norm_str = str(normalization_mode)
        if "Bark" in norm_str or "巴克" in norm_str:
            xlabel_str, ylabel_str = "F2 (Bark)", "F1 (Bark)"
        elif "ERB" in norm_str:
            xlabel_str, ylabel_str = "F2 (ERB)", "F1 (ERB)"
        elif "Mel" in norm_str or "美尔" in norm_str:
            xlabel_str, ylabel_str = "F2 (Mel)", "F1 (Mel)"
        elif "Lobanov" in norm_str or "归一化" in norm_str or "L-归一化" in norm_str:
            xlabel_str, ylabel_str = "F2 (Z-score)", "F1 (Z-score)"
        else:
            xlabel_str, ylabel_str = "F2 (Hz)", "F1 (Hz)"

        last_sm = None
        global_f1, global_f2 = [], []
        for idx, (facet_name, facet_entries) in enumerate(facet_specs):
            ax = axes_flat[idx]
            ax.set_facecolor("#FFFFFF")
            ax._is_vowel_space_ax = True
            ax.grid(True, linestyle="--", alpha=0.25, linewidth=0.8)

            sm, all_f1, all_f2 = self._draw_formant_density_layer(
                ax,
                facet_entries,
                show_raw=show_raw,
                show_contours=show_contours,
                bw_method=self.get_param('formant_density_bw', 0.14),
                alpha_max=0.78,
                zorder=2,
                transform_fn=transform_fn
            )
            if sm is not None:
                last_sm = sm
                if all_f1 is not None and len(all_f1) > 0:
                    global_f1.extend(np.asarray(all_f1, dtype=float).tolist())
                if all_f2 is not None and len(all_f2) > 0:
                    global_f2.extend(np.asarray(all_f2, dtype=float).tolist())
            else:
                ax.text(0.5, 0.5, "有效测量点不足", ha='center', va='center', color='#6B7280', fontsize=10, transform=ax.transAxes)

            ax.set_xlabel(xlabel_str, fontsize=11 if n_facets > 1 else 12, fontweight='bold', labelpad=8)
            ax.set_ylabel(ylabel_str, fontsize=11 if n_facets > 1 else 12, fontweight='bold', labelpad=8)

            if facet_name == "Default":
                title_text = "共振峰时空密度图"
                if len(set(e['speaker_name'] for e in data_entries)) == 1:
                    title_text = f"{data_entries[0]['speaker_name']} - {title_text}"
            else:
                title_text = f"{facet_name} (n={len(facet_entries)})"
            ax.set_title(title_text, fontsize=12 if n_facets > 1 else 13, fontweight='bold', pad=12)
            ax._original_title = title_text

        for idx in range(n_facets, len(axes_flat)):
            axes_flat[idx].set_visible(False)

        if last_sm is None:
            if n_facets == 1:
                axes_flat[0].clear()
                axes_flat[0].text(0.5, 0.5, "没有找到有效的共振峰时空密度数据！", ha='center', va='center', color='red', fontsize=12)
            return fig

        is_hz = "Hz" in str(normalization_mode)
        if global_f1 and global_f2:
            arr_f1 = np.asarray(global_f1, dtype=float)
            arr_f2 = np.asarray(global_f2, dtype=float)
            f1_p1, f1_p99 = np.percentile(arr_f1, 1.0), np.percentile(arr_f1, 99.0)
            f2_p1, f2_p99 = np.percentile(arr_f2, 1.0), np.percentile(arr_f2, 99.0)
            f1_pad = (f1_p99 - f1_p1) * 0.16 if f1_p99 > f1_p1 else (100.0 if is_hz else 0.5)
            f2_pad = (f2_p99 - f2_p1) * 0.16 if f2_p99 > f2_p1 else (150.0 if is_hz else 0.8)
            ylim_pair = (f1_p99 + f1_pad, max(50.0, f1_p1 - f1_pad) if is_hz else (f1_p1 - f1_pad))
            xlim_pair = (f2_p99 + f2_pad, max(500.0, f2_p1 - f2_pad) if is_hz else (f2_p1 - f2_pad))
        else:
            ylim_pair = None
            xlim_pair = None

        for ax in axes_flat[:n_facets]:
            ax.invert_xaxis()
            ax.invert_yaxis()
            if ylim_pair is not None and xlim_pair is not None:
                ax.set_ylim(*ylim_pair)
                ax.set_xlim(*xlim_pair)

        self._add_formant_density_colorbar(fig, axes_flat[:n_facets], last_sm)

        setattr(fig, "_phontracer_skip_tight_layout", True)
        return fig

    def _plot_formant_trajectories(self, data_entries, group_key, scale):
        fig, ax = plt.subplots(figsize=(9.2, 5.8))
        ax.set_facecolor("#FFFFFF")
        ax.grid(True, linestyle="--", alpha=0.25, linewidth=0.8)

        groupby_val = self._resolve_color_by()
        traj_style = str(self.get_param('formant_traj_style', '仅平均曲线') or '仅平均曲线')
        num_points = int(self.project_tree.app_state_params.get('pts', 11))

        show_f3_param = self.get_param('show_f3', None)
        if show_f3_param is None:
            show_f3_param = self.get_param('formant_show_f3', None)
        if show_f3_param is None:
            show_f3 = bool(self.project_tree.app_state_params.get('show_f3', False))
        else:
            show_f3 = bool(show_f3_param)

        syl_filter_raw = str(self.get_param('formant_syllable_filter', self.get_param('syllable_pos', '全部音节')) or '全部音节').strip()
        target_syl_idx = None
        if syl_filter_raw in ("1", "0", "第1音节", "第 1 音节"):
            target_syl_idx = 0 if syl_filter_raw != "1" or "音节" in syl_filter_raw or syl_filter_raw == "1" else 0
        elif syl_filter_raw in ("2", "第2音节", "第 2 音节"):
            target_syl_idx = 1
        elif syl_filter_raw in ("3", "第3音节", "第 3 音节"):
            target_syl_idx = 2
        else:
            m_syl = re.search(r"第\s*(\d+)\s*音节", syl_filter_raw)
            if m_syl:
                target_syl_idx = max(0, int(m_syl.group(1)) - 1)

        normalization_mode = self.get_param('formant_normalization', None) or self.get_param('scale', '原始频率 (Hz)')
        if str(normalization_mode) in ("t_value", "T 值", "T 值 (五度标调)"):
            normalization_mode = "原始频率 (Hz)"
        transform_fn = self._build_formant_normalizer(data_entries, normalization_mode)

        category_trajs = {}

        for entry in data_entries:
            spk_name = entry.get('speaker_name')
            raw_xs = np.asarray(entry.get('raw_xs', []), dtype=float)
            raw_f3 = np.asarray(entry.get('raw_f3', []), dtype=float)

            for syl in entry.get('syl_formants', []):
                s_idx = int(syl.get('syllable_index', 0))
                if target_syl_idx is not None and s_idx != target_syl_idx:
                    continue

                cat = self._get_syl_category(entry, syl, groupby_val)
                if cat not in category_trajs:
                    category_trajs[cat] = {'f1': [], 'f2': [], 'f3': []}

                f1_pts = self._resample_formant_track(syl.get('f1', []), num_points)
                f2_pts = self._resample_formant_track(syl.get('f2', []), num_points)
                f3_raw_pts = syl.get('f3', None)
                if f3_raw_pts is not None and len(f3_raw_pts) > 0 and np.any(np.isfinite(np.asarray(f3_raw_pts, dtype=float))):
                    f3_pts = self._resample_formant_track(f3_raw_pts, num_points)
                elif raw_f3.size > 0 and raw_xs.size == raw_f3.size:
                    c_s, c_e = syl.get('bounds', [0.0, 0.0])
                    mask3 = (raw_xs >= c_s) & (raw_xs <= c_e) & np.isfinite(raw_f3) & (raw_f3 > 0)
                    f3_pts = self._resample_formant_track(raw_f3[mask3], num_points) if np.any(mask3) else np.full(num_points, np.nan)
                else:
                    f3_pts = np.full(num_points, np.nan)

                if len(f1_pts) == num_points and len(f2_pts) == num_points:
                    if transform_fn is not None:
                        try:
                            f1_pts, f2_pts, f3_pts = transform_fn(f1_pts, f2_pts, speaker_name=spk_name, f3=f3_pts)
                        except TypeError:
                            f1_pts, f2_pts = transform_fn(f1_pts, f2_pts, speaker_name=spk_name)
                    category_trajs[cat]['f1'].append(f1_pts)
                    category_trajs[cat]['f2'].append(f2_pts)
                    category_trajs[cat]['f3'].append(f3_pts)

        if not category_trajs:
            ax.text(0.5, 0.5, "没有找到有效的共振峰时序数据！", ha='center', va='center', color='red', fontsize=12)
            return fig

        categories = sorted(list(category_trajs.keys()))
        x_pts = np.linspace(0, 100, num_points)
        if any(k in str(traj_style) for k in ("置信区间", "CI", "SE", "SD", "标准误", "标准差")):
            ax._error_band_label = "Mean ± 95% CI"

        for i_cat, cat in enumerate(categories):
            style = self._get_series_style(i_cat)
            color = style["color"]
            f1_arr = np.asarray(category_trajs[cat]['f1'], dtype=float)
            f2_arr = np.asarray(category_trajs[cat]['f2'], dtype=float)
            f3_arr = np.asarray(category_trajs[cat]['f3'], dtype=float)

            if len(f1_arr) == 0 or len(f2_arr) == 0:
                continue

            mean_f1 = _safe_nanmean(f1_arr, axis=0)
            mean_f2 = _safe_nanmean(f2_arr, axis=0)
            mean_f3 = _safe_nanmean(f3_arr, axis=0) if len(f3_arr) > 0 else np.full(num_points, np.nan)

            if "个体浅色细线" in traj_style or bool(self.get_param('show_spaghetti', False)):
                from matplotlib.collections import LineCollection
                segments_f1 = [np.column_stack((x_pts, single_f1)) for single_f1 in f1_arr]
                segments_f2 = [np.column_stack((x_pts, single_f2)) for single_f2 in f2_arr]
                ax.add_collection(LineCollection(segments_f1, colors=color, alpha=0.14, linewidths=0.8))
                ax.add_collection(LineCollection(segments_f2, colors=color, alpha=0.14, linewidths=0.8))
                if show_f3 and np.any(np.isfinite(mean_f3)):
                    segments_f3 = [np.column_stack((x_pts, single_f3)) for single_f3 in f3_arr if np.any(np.isfinite(single_f3))]
                    if segments_f3:
                        ax.add_collection(LineCollection(segments_f3, colors=color, alpha=0.12, linewidths=0.75))
            if any(k in str(traj_style) for k in ("置信区间", "CI", "SE", "SD", "标准误", "标准差")):
                err_f1 = self._compute_error_band_halfwidth(f1_arr, mode_text=traj_style)
                err_f2 = self._compute_error_band_halfwidth(f2_arr, mode_text=traj_style)
                ax.fill_between(x_pts, mean_f1 - err_f1, mean_f1 + err_f1, color=color, alpha=0.12)
                ax.fill_between(x_pts, mean_f2 - err_f2, mean_f2 + err_f2, color=color, alpha=0.12)
                if show_f3 and np.any(np.isfinite(mean_f3)):
                    err_f3 = self._compute_error_band_halfwidth(f3_arr, mode_text=traj_style)
                    ax.fill_between(x_pts, mean_f3 - err_f3, mean_f3 + err_f3, color=color, alpha=0.10)

            ax.plot(x_pts, mean_f1, linestyle='--', marker='s', markersize=4.5, color=color, linewidth=2.0, label=f"{cat} F1")
            ax.plot(x_pts, mean_f2, linestyle='-', marker='o', markersize=4.5, color=color, linewidth=2.2, label=f"{cat} F2")
            if show_f3 and np.any(np.isfinite(mean_f3)):
                ax.plot(x_pts, mean_f3, linestyle='-.', marker='^', markersize=4.8, color=color, linewidth=1.9, alpha=0.9, label=f"{cat} F3")

        ax.set_xlabel("音节归一化时长百分比 (%)", fontsize=12, fontweight='bold', labelpad=10)

        norm_str = str(normalization_mode)
        if "Bark" in norm_str or "巴克" in norm_str:
            ax.set_ylabel("频率 (Bark)", fontsize=12, fontweight='bold', labelpad=10)
        elif "ERB" in norm_str or "等效矩形" in norm_str:
            ax.set_ylabel("频率 (ERB)", fontsize=12, fontweight='bold', labelpad=10)
        elif "Mel" in norm_str or "美尔" in norm_str:
            ax.set_ylabel("频率 (Mel)", fontsize=12, fontweight='bold', labelpad=10)
        elif "Lobanov" in norm_str or "归一化" in norm_str or "L-归一化" in norm_str:
            ax.set_ylabel("频率 (Z-score)", fontsize=12, fontweight='bold', labelpad=10)
        else:
            ax.set_ylabel("频率 (Hz)", fontsize=12, fontweight='bold', labelpad=10)
        ax.margins(x=0.02)

        legend_kwargs = self._get_legend_kwargs()
        legend_kwargs["fontsize"] = 8.5
        ax.legend(**legend_kwargs)

        title_f_str = "F1-F2-F3" if show_f3 else "F1-F2"
        title_text = f"共振峰 {title_f_str} 时序轨迹平均曲线图"
        if target_syl_idx is not None:
            title_text += f" (第 {target_syl_idx + 1} 音节)"
        if len(set(e['speaker_name'] for e in data_entries)) == 1:
            title_text = f"{data_entries[0]['speaker_name']} - {title_text}"
        ax.set_title(title_text, fontsize=13, fontweight='bold', pad=15)
        ax._original_title = title_text

        fig.tight_layout()
        return fig

    def _plot_formant_pillai_matrix(self, data_entries, group_key="group", scale="Hz"):
        """
        共振峰 3：元音对立重叠度与分布图 (Vowel Merger Pillai Score Matrix & Distribution)
        支持两个子视图：
        - 'matrix' (默认): 自动计算各元音类别两两之间的 Pillai-Bartlett Trace (Pillai Score, 0~1) 与声学欧氏距离矩阵热图
        - 'raincloud': 各元音类别的稳态 F1、F2、动程长度 (TL) 与时长分布雨云图
        """
        groupby_val = self._resolve_color_by()
        normalization_mode = self.get_param('formant_normalization', None) or self.get_param('scale', '原始频率 (Hz)')
        if str(normalization_mode) in ("t_value", "T 值", "T 值 (五度标调)"):
            normalization_mode = "原始频率 (Hz)"
        transform_fn = self._build_formant_normalizer(data_entries, normalization_mode)

        cat_tokens = {}
        cat_metrics = {}

        for entry in data_entries:
            spk_name = entry.get('speaker_name')
            for syl in entry.get('syl_formants', []):
                cat = self._get_syl_category(entry, syl, groupby_val)
                f1_pts = np.asarray(syl.get('f1', []), dtype=float)
                f2_pts = np.asarray(syl.get('f2', []), dtype=float)
                if len(f1_pts) == 0 or len(f2_pts) == 0:
                    continue
                if transform_fn is not None:
                    try:
                        f1_pts, f2_pts = transform_fn(f1_pts, f2_pts, speaker_name=spk_name)
                    except TypeError:
                        f1_pts, f2_pts = transform_fn(f1_pts, f2_pts)

                n_p = len(f1_pts)
                i0 = int(math.floor(0.2 * n_p)) if n_p >= 5 else 0
                i1 = max(i0 + 1, int(math.ceil(0.8 * n_p))) if n_p >= 5 else n_p
                sub1, sub2 = f1_pts[i0:i1], f2_pts[i0:i1]
                valid = np.isfinite(sub1) & np.isfinite(sub2)
                if not np.any(valid):
                    valid_all = np.isfinite(f1_pts) & np.isfinite(f2_pts)
                    if not np.any(valid_all):
                        continue
                    sub1, sub2 = f1_pts[valid_all], f2_pts[valid_all]
                else:
                    sub1, sub2 = sub1[valid], sub2[valid]

                tok_f1 = float(np.mean(sub1))
                tok_f2 = float(np.mean(sub2))
                tl_val = float(np.sum(np.sqrt(np.diff(sub1) ** 2 + np.diff(sub2) ** 2))) if len(sub1) >= 2 else 0.0
                c_s, c_e = syl.get('bounds', [0.0, 0.0])
                dur_ms = max(0.0, float(c_e - c_s) * 1000.0)

                cat_tokens.setdefault(str(cat), []).append([tok_f1, tok_f2])
                cat_metrics.setdefault(str(cat), {"f1": [], "f2": [], "tl": [], "duration_ms": []})
                cat_metrics[str(cat)]["f1"].append(tok_f1)
                cat_metrics[str(cat)]["f2"].append(tok_f2)
                cat_metrics[str(cat)]["tl"].append(tl_val)
                cat_metrics[str(cat)]["duration_ms"].append(dur_ms)

        categories = sorted(cat_tokens.keys())
        n_cats = len(categories)
        if n_cats == 0:
            fig, ax = plt.subplots(figsize=(7, 5))
            ax.text(0.5, 0.5, "没有找到可用于计算 Pillai Score 的元音共振峰数据！", ha="center", va="center", color="red")
            return fig

        pillai_mat = np.zeros((n_cats, n_cats), dtype=float)
        euclid_mat = np.zeros((n_cats, n_cats), dtype=float)
        for i in range(n_cats):
            for j in range(n_cats):
                if i == j:
                    pillai_mat[i, j] = 0.0
                    euclid_mat[i, j] = 0.0
                elif j > i:
                    p_score, e_dist = _compute_pillai_bartlett_trace(cat_tokens[categories[i]], cat_tokens[categories[j]])
                    pillai_mat[i, j] = pillai_mat[j, i] = p_score
                    euclid_mat[i, j] = euclid_mat[j, i] = e_dist

        subview = str(self.get_param('pillai_subview', 'matrix') or 'matrix').strip().lower()
        if any(k in subview for k in ("raincloud", "distribution", "雨云", "分布")):
            fig, axes = plt.subplots(1, 2, figsize=(12.2, 5.2))
            ax_tl, ax_dur = axes
            ax_tl.set_facecolor("#FFFFFF")
            ax_dur.set_facecolor("#FFFFFF")
            ax_tl.grid(True, linestyle="--", alpha=0.25)
            ax_dur.grid(True, linestyle="--", alpha=0.25)

            positions = np.arange(1, n_cats + 1, dtype=float)
            colors = [self.colors[i % len(self.colors)] for i in range(n_cats)]
            tl_series = [cat_metrics[c]["tl"] for c in categories]
            dur_series = [cat_metrics[c]["duration_ms"] for c in categories]

            self._draw_raincloud_series(ax_tl, tl_series, positions, colors)
            self._draw_raincloud_series(ax_dur, dur_series, positions, colors)

            ax_tl.set_xticks(positions)
            ax_tl.set_xticklabels(categories, fontweight="bold")
            ax_tl.set_ylabel("轨迹动程长度 Trajectory Length (TL)")
            ax_tl.set_title("元音轨迹动程长度 (TL) 雨云分布", fontsize=11.5, fontweight="bold")
            ax_tl._original_title = "元音轨迹动程长度 (TL) 雨云分布"

            ax_dur.set_xticks(positions)
            ax_dur.set_xticklabels(categories, fontweight="bold")
            ax_dur.set_ylabel("音节时长 Duration (ms)")
            ax_dur.set_title("元音音节时长 (ms) 雨云分布", fontsize=11.5, fontweight="bold")
            ax_dur._original_title = "元音音节时长 (ms) 雨云分布"

            fig._pillai_matrix = pillai_mat
            fig._euclidean_matrix = euclid_mat
            fig._pillai_categories = categories
            fig._vowel_metrics = cat_metrics
            fig.tight_layout()
            return fig

        fig, (ax_p, ax_d) = plt.subplots(1, 2, figsize=(13.2, 5.8))
        ax_p._is_matrix_heatmap = True
        ax_d._is_matrix_heatmap = True

        im_p = ax_p.imshow(pillai_mat, vmin=0.0, vmax=1.0, cmap="YlGnBu")
        ax_p.set_xticks(range(n_cats))
        ax_p.set_yticks(range(n_cats))
        ax_p.set_xticklabels(categories, fontweight="bold")
        ax_p.set_yticklabels(categories, fontweight="bold")
        t_p = "元音对立重叠度矩阵 (Pillai Score: 0=合并, 1=分离)"
        ax_p.set_title(t_p, fontsize=11.5, fontweight="bold")
        ax_p._original_title = t_p
        for r_i in range(n_cats):
            for c_i in range(n_cats):
                val = pillai_mat[r_i, c_i]
                if np.isfinite(val):
                    txt_c = "white" if val > 0.55 else "#111827"
                    ax_p.text(c_i, r_i, f"{val:.3f}", ha="center", va="center", color=txt_c, fontsize=9, fontweight="bold")
        cb_p = fig.colorbar(im_p, ax=ax_p, fraction=0.046, pad=0.04)
        if hasattr(cb_p, "ax"):
            cb_p.ax._is_colorbar_axis = True
        cb_p.set_label("Pillai-Bartlett Trace (0~1)")

        im_d = ax_d.imshow(euclid_mat, cmap="PuBu")
        ax_d.set_xticks(range(n_cats))
        ax_d.set_yticks(range(n_cats))
        ax_d.set_xticklabels(categories, fontweight="bold")
        ax_d.set_yticklabels(categories, fontweight="bold")
        t_d = "元音中心声学欧氏距离矩阵 (Euclidean Distance)"
        ax_d.set_title(t_d, fontsize=11.5, fontweight="bold")
        ax_d._original_title = t_d
        max_d = _safe_nanmax(euclid_mat, default=1.0)
        for r_i in range(n_cats):
            for c_i in range(n_cats):
                val = euclid_mat[r_i, c_i]
                if np.isfinite(val):
                    txt_c = "white" if (max_d > 0 and val > 0.58 * max_d) else "#111827"
                    fmt_s = f"{val:.1f}" if val >= 10 else f"{val:.2f}"
                    ax_d.text(c_i, r_i, fmt_s, ha="center", va="center", color=txt_c, fontsize=9)
        cb_d = fig.colorbar(im_d, ax=ax_d, fraction=0.046, pad=0.04)
        if hasattr(cb_d, "ax"):
            cb_d.ax._is_colorbar_axis = True
        cb_d.set_label("声学欧氏距离")

        fig._pillai_matrix = pillai_mat
        fig._euclidean_matrix = euclid_mat
        fig._pillai_categories = categories
        fig._vowel_metrics = cat_metrics
        fig.suptitle("元音对立重叠度 (Pillai Score) 与声学距离矩阵", fontsize=13, fontweight="bold")
        fig.tight_layout()
        return fig

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

    def _make_checkbox(self, parent, text, variable=None, command=None, **kwargs):
        try:
            from .ui_widgets import install_dynamic_checkbox_patch
            install_dynamic_checkbox_patch()
        except Exception:
            pass
        primary = getattr(self, "ui_primary", "#2563EB")
        primary_hover = getattr(self, "ui_primary_hover", "#1D4ED8")
        options = {
            "text": text,
            "font": getattr(self, "font_small", None),
            "checkbox_width": 18,
            "checkbox_height": 18,
            "corner_radius": 1000,
            "border_width": 2,
            "fg_color": (primary, primary_hover),
            "hover_color": ("#9CA3AF", "#4B5563"),
            "border_color": ("#4B5563", "#9CA3AF"),
            "checkmark_color": "#FFFFFF",
            "text_color": ("#1F2937", "#E5E7EB"),
        }
        if variable is not None:
            options["variable"] = variable
        if command is not None:
            options["command"] = command
        options.update(kwargs)
        return ctk.CTkCheckBox(parent, **options)

    def _build_formant_space_settings(self):
        # Confidence Ellipse
        ctk.CTkLabel(self.dynamic_content_frame, text="置信椭圆 (Confidence Ellipse):", font=self.font_small).pack(anchor="w", pady=(5, 2))
        self.combo_formant_ellipse = ctk.CTkOptionMenu(
            self.dynamic_content_frame,
            values=["95% 置信椭圆", "1-sigma 置信椭圆", "无置信椭圆"],
            command=lambda _: self.trigger_preview_update(),
            **self.dropdown_kwargs
        )
        self.combo_formant_ellipse.set(self.var_formant_ellipse.get())
        self.combo_formant_ellipse.pack(fill=tk.X, pady=2)
        self._apply_custom_arrow(self.combo_formant_ellipse)

        # Label Mode
        ctk.CTkLabel(self.dynamic_content_frame, text="标签显示模式:", font=self.font_small).pack(anchor="w", pady=(5, 2))
        self.combo_formant_label_mode = ctk.CTkOptionMenu(
            self.dynamic_content_frame,
            values=["显示分组标签", "显示单字标签", "显示词语标签", "不显示标签"],
            command=lambda _: self.trigger_preview_update(),
            **self.dropdown_kwargs
        )
        self.combo_formant_label_mode.set("显示分组标签")
        self.combo_formant_label_mode.pack(fill=tk.X, pady=2)
        self._apply_custom_arrow(self.combo_formant_label_mode)

        ctk.CTkLabel(self.dynamic_content_frame, text="共振峰声学单位/归一化:", font=self.font_small).pack(anchor="w", pady=(6, 2))
        self.combo_formant_normalization = ctk.CTkOptionMenu(
            self.dynamic_content_frame,
            values=["原始频率 (Hz)", "Bark (听觉巴克)", "ERB (等效矩形带宽)", "Lobanov z-score (学术标准)"],
            command=lambda _: self.trigger_preview_update(),
            **self.dropdown_kwargs
        )
        self.combo_formant_normalization.set(self.var_formant_normalization.get())
        self.combo_formant_normalization.pack(fill=tk.X, pady=(2, 6))
        self._apply_custom_arrow(self.combo_formant_normalization)

        self.cb_show_convex_hull = self._make_checkbox(
            self.dynamic_content_frame,
            text="显示元音空间外围凸包与面积 (VSA)",
            variable=self.var_show_convex_hull,
            command=self.update_preview
        )
        self.cb_show_convex_hull.pack(anchor="w", pady=(4, 4))

        self.cb_show_diphthong_arrows = self._make_checkbox(
            self.dynamic_content_frame,
            text="显示复合元音 20%→80% 方向箭头轨迹 (TL)",
            variable=self.var_show_diphthong_arrows,
            command=self.update_preview
        )
        self.cb_show_diphthong_arrows.pack(anchor="w", pady=(4, 4))

        self.cb_formant_axis_lock = self._make_checkbox(
            self.dynamic_content_frame,
            text="固定横纵坐标范围 (跨组/跨页一致)",
            variable=self.var_formant_axis_lock,
            command=self.update_preview
        )
        self.cb_formant_axis_lock.pack(anchor="w", pady=(2, 6))

        self.cb_formant_show_raw = self._make_checkbox(
            self.dynamic_content_frame,
            text="显示个体测量帧 (散点背景)",
            variable=self.var_formant_show_raw,
            command=self.update_preview
        )
        self.cb_formant_show_raw.pack(anchor="w", pady=(4, 4))

        self.cb_formant_time_gradient = self._make_checkbox(
            self.dynamic_content_frame,
            text="显示时序渐变轨迹线 (红→蓝)",
            variable=self.var_formant_time_gradient,
            command=self.update_preview
        )
        self.cb_formant_time_gradient.pack(anchor="w", pady=(4, 4))

        self.cb_formant_density_overlay = self._make_checkbox(
            self.dynamic_content_frame,
            text="叠加时空密度热力层 (红→蓝)",
            variable=self.var_formant_density_overlay,
            command=self.update_preview
        )
        self.cb_formant_density_overlay.pack(anchor="w", pady=(4, 4))

        bw_val_frame = ctk.CTkFrame(self.dynamic_content_frame, fg_color="transparent")
        bw_val_frame.pack(fill=tk.X, pady=(4, 2))
        ctk.CTkLabel(bw_val_frame, text="密度带宽 (越小越清晰):", font=self.font_small).pack(side=tk.LEFT)
        self.lbl_formant_density_bw = ctk.CTkLabel(bw_val_frame, text=f"{self.var_formant_density_bw.get():.2f}", font=self.font_small)
        self.lbl_formant_density_bw.pack(side=tk.RIGHT)
        self.slider_formant_density_bw = ctk.CTkSlider(
            self.dynamic_content_frame,
            from_=0.06,
            to=0.32,
            number_of_steps=26,
            command=self._on_formant_density_bw_changed
        )
        self.slider_formant_density_bw.set(float(self.var_formant_density_bw.get()))
        self.slider_formant_density_bw.pack(fill=tk.X, pady=(0, 6))

        self.cb_formant_density_show_contours = self._make_checkbox(
            self.dynamic_content_frame,
            text="叠加等密度轮廓线",
            variable=self.var_formant_density_show_contours,
            command=self.update_preview
        )
        self.cb_formant_density_show_contours.pack(anchor="w", pady=(2, 4))

        ctk.CTkLabel(self.dynamic_content_frame, text="分面子图排版 (Facet):", font=self.font_small).pack(anchor="w", pady=(6, 2))
        self.combo_formant_density_facet = ctk.CTkOptionMenu(
            self.dynamic_content_frame,
            values=["单图展示 (不分面)", "按字表组分面", "按音节位置分面", "按发音人分面"],
            command=lambda val: self._sync_facet_from_child(val),
            **self.dropdown_kwargs
        )
        self.combo_formant_density_facet.set(self.var_formant_density_facet.get())
        self.combo_formant_density_facet.pack(fill=tk.X, pady=(2, 8))
        self._apply_custom_arrow(self.combo_formant_density_facet)

    def _on_formant_density_bw_changed(self, val):
        bw = float(val)
        self.var_formant_density_bw.set(bw)
        if hasattr(self, 'lbl_formant_density_bw'):
            self.lbl_formant_density_bw.configure(text=f"{bw:.2f}")
        self.trigger_preview_update()

    def _build_formant_density_settings(self):
        self.cb_formant_density_show_raw = self._make_checkbox(
            self.dynamic_content_frame,
            text="显示个体测量帧 (极淡散点)",
            variable=self.var_formant_density_show_raw,
            command=self.update_preview
        )
        self.cb_formant_density_show_raw.pack(anchor="w", pady=(10, 5))

        self.cb_formant_density_show_contours = self._make_checkbox(
            self.dynamic_content_frame,
            text="叠加等密度轮廓线",
            variable=self.var_formant_density_show_contours,
            command=self.update_preview
        )
        self.cb_formant_density_show_contours.pack(anchor="w", pady=(5, 5))

        ctk.CTkLabel(self.dynamic_content_frame, text="分面子图排版 (Facet):", font=self.font_small).pack(anchor="w", pady=(6, 2))
        self.combo_formant_density_facet = ctk.CTkOptionMenu(
            self.dynamic_content_frame,
            values=["单图展示 (不分面)", "按字表组分面", "按音节位置分面", "按发音人分面"],
            command=lambda val: self._sync_facet_from_child(val),
            **self.dropdown_kwargs
        )
        self.combo_formant_density_facet.set(self.var_formant_density_facet.get())
        self.combo_formant_density_facet.pack(fill=tk.X, pady=(2, 8))
        self._apply_custom_arrow(self.combo_formant_density_facet)

    def _build_formant_trajectory_settings(self):
        ctk.CTkLabel(self.dynamic_content_frame, text="曲线展现形式:", font=self.font_small).pack(anchor="w", pady=(5, 2))
        self.combo_formant_traj_style = ctk.CTkOptionMenu(
            self.dynamic_content_frame,
            values=["平均曲线 + 置信区间阴影", "仅平均曲线", "平均曲线 + 个体浅色细线"],
            command=lambda _: self.trigger_preview_update(),
            **self.dropdown_kwargs
        )
        self.combo_formant_traj_style.set(self.var_formant_traj_style.get() or "仅平均曲线")
        self.combo_formant_traj_style.pack(fill=tk.X, pady=(2, 6))
        self._apply_custom_arrow(self.combo_formant_traj_style)

        ctk.CTkLabel(self.dynamic_content_frame, text="音节位置筛选:", font=self.font_small).pack(anchor="w", pady=(4, 2))
        self.combo_formant_syllable_filter = ctk.CTkOptionMenu(
            self.dynamic_content_frame,
            values=["全部音节", "第 1 音节", "第 2 音节"],
            command=lambda _: self.trigger_preview_update(),
            **self.dropdown_kwargs
        )
        self.combo_formant_syllable_filter.set("全部音节")
        self.combo_formant_syllable_filter.pack(fill=tk.X, pady=(2, 6))
        self._apply_custom_arrow(self.combo_formant_syllable_filter)

        ctk.CTkLabel(self.dynamic_content_frame, text="共振峰声学单位:", font=self.font_small).pack(anchor="w", pady=(4, 2))
        self.combo_formant_normalization = ctk.CTkOptionMenu(
            self.dynamic_content_frame,
            values=["原始频率 (Hz)", "Bark (听觉巴克)", "ERB (等效矩形带宽)", "Lobanov z-score (学术标准)"],
            command=lambda _: self.trigger_preview_update(),
            **self.dropdown_kwargs
        )
        self.combo_formant_normalization.set(self.var_formant_normalization.get())
        self.combo_formant_normalization.pack(fill=tk.X, pady=(2, 6))
        self._apply_custom_arrow(self.combo_formant_normalization)

        self.cb_show_f3 = self._make_checkbox(
            self.dynamic_content_frame,
            text="叠加 F3 第三共振峰轨迹 (点划线)",
            variable=self.var_show_f3,
            command=self.update_preview
        )
        self.cb_show_f3.pack(anchor="w", pady=(4, 8))

    def _build_formant_pillai_settings(self):
        ctk.CTkLabel(self.dynamic_content_frame, text="元音对立与分布子视图:", font=self.font_small).pack(anchor="w", pady=(5, 2))
        self.combo_pillai_subview = ctk.CTkOptionMenu(
            self.dynamic_content_frame,
            values=["元音对立重叠度矩阵 (Pillai Score)", "共振峰与时长分布雨云图"],
            command=lambda _: self.trigger_preview_update(),
            **self.dropdown_kwargs
        )
        self.combo_pillai_subview.set("元音对立重叠度矩阵 (Pillai Score)")
        self.combo_pillai_subview.pack(fill=tk.X, pady=(2, 8))
        self._apply_custom_arrow(self.combo_pillai_subview)

        ctk.CTkLabel(self.dynamic_content_frame, text="共振峰声学单位:", font=self.font_small).pack(anchor="w", pady=(4, 2))
        self.combo_formant_normalization = ctk.CTkOptionMenu(
            self.dynamic_content_frame,
            values=["原始频率 (Hz)", "Bark (听觉巴克)", "ERB (等效矩形带宽)", "Lobanov z-score (学术标准)"],
            command=lambda _: self.trigger_preview_update(),
            **self.dropdown_kwargs
        )
        self.combo_formant_normalization.set(self.var_formant_normalization.get())
        self.combo_formant_normalization.pack(fill=tk.X, pady=(2, 8))
        self._apply_custom_arrow(self.combo_formant_normalization)

    def _export_overview_heatmap_paginated_pdf(self, out_file, data, scale, pages):
        from matplotlib.backends.backend_pdf import PdfPages
        pdf_pages = PdfPages(out_file)
        total_pages = len(pages)
        try:
            for page_idx, page_rows in enumerate(pages):
                self._check_export_cancelled()
                self._report_export_progress(0.15 + 0.75 * (page_idx / total_pages), f"正在导出分页概览图 {page_idx + 1}/{total_pages}...")

                allowed_pairs = {row['row_id'] for row in page_rows}
                chunk_entries = [e for e in data if (e['group'], e['label']) in allowed_pairs]
                fig = self.generate_plot(chunk_entries, is_preview=False)

                fig.text(0.95, 0.02, f"第 {page_idx + 1} 页 / 共 {total_pages} 页",
                         ha='right', va='bottom', fontsize=9, color='gray')

                pdf_pages.savefig(fig, bbox_inches='tight')
                plt.close(fig)
        finally:
            pdf_pages.close()

    def _export_overview_heatmap_paginated_images(self, base_path, data, scale, ext, pages):
        total_pages = len(pages)
        dir_name, file_name = os.path.split(base_path)
        name_part, _ = os.path.splitext(file_name)

        for page_idx, page_rows in enumerate(pages):
            self._check_export_cancelled()
            self._report_export_progress(0.15 + 0.75 * (page_idx / max(1, total_pages)), f"正在导出分页概览图 {page_idx + 1}/{total_pages}...")

            allowed_pairs = {row['row_id'] for row in page_rows}
            chunk_entries = [e for e in data if (e['group'], e['label']) in allowed_pairs]
            fig = self.generate_plot(chunk_entries, is_preview=False)

            fig.text(0.95, 0.02, f"第 {page_idx + 1} 页 / 共 {total_pages} 页",
                     ha='right', va='bottom', fontsize=9, color='gray')

            out_path = os.path.join(dir_name, f"{name_part}_第{page_idx + 1}页{ext}")
            self._save_figure(fig, out_path)
            if page_idx == 0 and os.path.abspath(out_path) != os.path.abspath(base_path):
                import shutil as _shutil
                _shutil.copy2(out_path, base_path)
            plt.close(fig)

    def _resolve_qc_hover_item(self, fig, event):
        """根据鼠标坐标解析当前悬停或双击命中的质检词条元数据 (支持热图行与 Spaghetti 叠加轨迹)。"""
        hover_meta = getattr(fig, "_qc_hover_metadata", None)
        if not hover_meta or event is None or getattr(event, "inaxes", None) is None or getattr(event, "ydata", None) is None:
            return None, 0

        y_val = float(event.ydata)
        x_val = float(event.xdata) if getattr(event, "xdata", None) is not None else 0.0

        # 1) 热图模式 (含 row_index)
        if any("row_index" in item for item in hover_meta):
            row_idx = int(round(y_val))
            col_idx = max(0, int(round(x_val)))
            for item in hover_meta:
                if item.get("row_index") == row_idx:
                    return item, col_idx
            return None, col_idx

        # 2) Spaghetti 原始曲线叠加模式 (按所属子图与最近采样点 Y 距离匹配，优先命中离群红线)
        col_idx = max(0, int(round(x_val)) - 1)
        candidates = [it for it in hover_meta if it.get("ax") is None or it.get("ax") is event.inaxes]
        if not candidates:
            candidates = list(hover_meta)

        best_item = None
        best_score = float("inf")
        for item in candidates:
            vals = item.get("values") or []
            if not vals:
                continue
            c_idx = min(max(0, col_idx), len(vals) - 1)
            v = vals[c_idx]
            if v is None or not np.isfinite(v):
                continue
            dist = abs(float(v) - y_val)
            # 离群曲线给予优先权重，方便点击红色离群轨迹
            score = dist * (0.55 if item.get("is_outlier") or item.get("is_anomaly") else 1.0)
            if score < best_score:
                best_score = score
                best_item = item
        return best_item, max(0, col_idx)

    def locate_qc_item_in_project_tree(self, item_id, speaker_name=None, label=None):
        """
        阶段 A 质检交互闭环：在质检图表中发现异常词条后，直接定位并选中主程序工程树对应词条，
        支持跨发音人自动切换与分组展开。
        """
        pt = getattr(self, "project_tree", None)
        if pt is None:
            return False

        # 若指定了发音人且与当前发音人不同，尝试通过主程序切换发音人
        if speaker_name and getattr(self, "app", None) is not None:
            sm = getattr(self.app, "speaker_manager", None)
            speakers = list(getattr(self, "all_speakers", None) or [])
            if not speakers and sm is not None and hasattr(sm, "speakers"):
                speakers = list(sm.speakers.values())
            target_spk = next((s for s in speakers if getattr(s, "name", None) == speaker_name), None)
            if target_spk is not None and sm is not None:
                active_spk = sm.get_active_speaker() if hasattr(sm, "get_active_speaker") else None
                if active_spk is None or getattr(active_spk, "id", None) != getattr(target_spk, "id", None):
                    for switch_attr in ("switch_speaker", "select_speaker", "on_speaker_selected"):
                        fn = getattr(self.app, switch_attr, None) or getattr(sm, switch_attr, None)
                        if callable(fn):
                            try:
                                fn(target_spk.id)
                                break
                            except Exception:
                                pass

        items_dict = getattr(pt, "items", None) or {}
        real_iid = None
        if item_id and str(item_id) in items_dict:
            real_iid = str(item_id)
        elif label:
            clean_lbl = str(label).split(" (")[0].replace("[!] ", "").strip()
            for k, v in items_dict.items():
                if isinstance(v, dict) and v.get("label") in (label, clean_lbl):
                    real_iid = str(k)
                    break
        if real_iid is None and item_id:
            real_iid = str(item_id)

        if not real_iid:
            return False

        self._last_located_qc_item_id = real_iid
        try:
            pt.current_iid = real_iid
        except Exception:
            pass

        tree = getattr(pt, "tree", None)
        if tree is not None:
            try:
                target_node = None
                if hasattr(tree, "exists"):
                    if tree.exists(real_iid):
                        target_node = real_iid
                    elif tree.exists(f"warning_{real_iid}"):
                        target_node = f"warning_{real_iid}"
                if target_node is not None:
                    if hasattr(tree, "parent") and hasattr(tree, "item"):
                        parent_gid = tree.parent(target_node)
                        if parent_gid:
                            tree.item(parent_gid, open=True)
                    if hasattr(tree, "selection_set"):
                        tree.selection_set(target_node)
                    if hasattr(tree, "see"):
                        tree.see(target_node)
                    if hasattr(tree, "focus"):
                        tree.focus(target_node)
            except Exception:
                pass

        if callable(getattr(pt, "on_tree_select", None)):
            try:
                pt.on_tree_select(None)
            except Exception:
                pass
        elif callable(getattr(pt, "on_item_selected", None)):
            try:
                pt.on_item_selected(real_iid)
            except Exception:
                pass

        return real_iid in items_dict or bool(real_iid)


class AcousticChartExportDialog(ctk.CTkToplevel, AcousticChartExporter):
    def __init__(self, parent, app=None, project_tree=None, mode='single', all_speakers=None):
        if _GUI_IMPORT_ERROR is not None:
            raise RuntimeError("Acoustic chart dialog requires Tkinter/CustomTkinter GUI support.") from _GUI_IMPORT_ERROR

        ctk.CTkToplevel.__init__(self, parent)
        AcousticChartExporter.__init__(self, project_tree, app, all_speakers)

        self.parent = parent
        self.app = app
        self.project_tree = project_tree
        self.initial_mode = mode
        self.all_speakers = all_speakers or []

        self.title("PhonTracer语音学标注与声学分析系统 V1.0 - 声学可视化与图表导出器")
        self.geometry("1040x680")
        self.resizable(True, True)
        if self.app:
            self.app.active_chart_dialog = self
        self.protocol("WM_DELETE_WINDOW", self._on_close_request)
        self._export_worker = None
        self._export_cancel_event = None
        self._export_progress_queue = None
        self._export_poll_job = None
        self._export_progress_window = None
        self._preview_worker = None
        self._preview_cancel_event = None
        self._preview_queue = None
        self._preview_poll_job = None
        self._preview_generation = 0

        # Color Palette
        self.colors = list(PUBLICATION_PRESETS["chinese_journal"]["colors"])
        self.ui_primary = "#2563EB"
        self.ui_primary_hover = "#1D4ED8"
        self.ui_primary_soft = "#DBEAFE"
        self.ui_row_even = ("#FFFFFF", "#1E293B")
        self.ui_row_odd = ("#F8FAFC", "#263244")

        self.sm = getattr(self.app, 'speaker_manager', None)
        self.active_speaker = self.sm.get_active_speaker() if self.sm else None

        # Fonts
        self.font_title = ctk.CTkFont(family="Microsoft YaHei", size=14, weight="bold")
        self.font_main = ctk.CTkFont(family="Microsoft YaHei", size=12)
        self.font_small = ctk.CTkFont(family="Microsoft YaHei", size=11)

        self.dropdown_kwargs = {
            "font": self.font_main,
            "fg_color": ("#EEF2F6", "#334155"),
            "text_color": ("#0F172A", "#F8FAFC"),
            "button_color": ("#EEF2F6", "#334155"),
            "button_hover_color": ("#E2E8F0", "#475569"),
            "dropdown_fg_color": ("#FFFFFF", "#1E293B"),
            "dropdown_hover_color": ("#EFF6FF", "#334155"),
            "dropdown_text_color": ("#0F172A", "#F8FAFC"),
            "height": 30,
            "corner_radius": 8,
        }

        self._init_variables()
        self._init_group_filters()

        self._build_gui()
        self._populate_groups_list()
        self._update_pixel_options_state()

        self.bind("<Left>", lambda e: self._on_arrow_left())
        self.bind("<Right>", lambda e: self._on_arrow_right())
        self.bind("<Up>", lambda e: self._on_arrow_up())
        self.bind("<Down>", lambda e: self._on_arrow_down())

        self.update_preview()

    def _apply_custom_arrow(self, dropdown):
        try:
            from .ui_widgets import _apply_custom_arrow as _shared_apply_custom_arrow
            _shared_apply_custom_arrow(dropdown)
        except Exception:
            pass

    def _init_variables(self):
        # Top-level work mode & publication preset
        self.var_work_mode = ctk.StringVar(value="学术论文图表")
        self.var_preset_theme = ctk.StringVar(value="中文期刊规范")
        self.var_show_title = ctk.BooleanVar(value=False)

        # 1. Chart Type
        analysis_mode = self.project_tree.app_state_params.get('analysis_mode', 'f0')
        if analysis_mode == 'formant':
            self.var_chart_type = ctk.StringVar(value="formant_space")
        else:
            self.var_chart_type = ctk.StringVar(value="contour")

        # 2. Export Scope
        scope_default = "active"
        if self.initial_mode == 'separate':
            scope_default = "separate"
        elif self.initial_mode == 'integrated':
            scope_default = "integrated"
        self.var_export_scope = ctk.StringVar(value=scope_default)

        # 3. Orthogonal Color-by & Facet-by
        self.var_group_by = ctk.StringVar(value="group")
        self.var_color_by = ctk.StringVar(value="按字表组别")
        self.var_facet_by = ctk.StringVar(value="不分面 (单图叠加)")

        # 4. Acoustic Scale
        self.var_scale = ctk.StringVar(value="t_value")

        # 5. Image Format & Layout Size
        self.var_format = ctk.StringVar(value="pdf")
        self.var_layout_size = ctk.StringVar(value="双栏通栏 150mm")
        self.var_dpi = ctk.StringVar(value="600 DPI")

        # Dynamic Options - Tone Contour
        self.var_contour_x = ctk.StringVar(value="normalized")
        self.var_contour_content = ctk.StringVar(value="仅组别平均曲线")
        self.var_contour_facet = ctk.StringVar(value="none")
        self.var_show_chao_five_level = ctk.BooleanVar(value=True)
        self.var_show_spaghetti = ctk.BooleanVar(value=False)
        self.var_show_kde_bg = ctk.BooleanVar(value=False)

        # Dynamic Options - Tone Distribution
        self.var_dist_type = ctk.StringVar(value="全调均值")
        self.var_dist_style = ctk.StringVar(value="雨云图 (Raincloud)")

        # Dynamic Options - Temporal Density (KDE Heatmap)
        self.var_density_bw = ctk.DoubleVar(value=0.15)
        self.var_density_f0_mode = ctk.StringVar(value="percentile")
        self.var_density_p_low = ctk.StringVar(value="5")
        self.var_density_p_high = ctk.StringVar(value="95")
        self.var_density_m_min = ctk.StringVar(value="75")
        self.var_density_m_max = ctk.StringVar(value="600")
        self.var_density_facet = ctk.StringVar(value="按声调类型分面")

        # Dynamic Options - Formant Density (KDE Heatmap)
        self.var_formant_density_overlay = tk.BooleanVar(value=False)
        self.var_formant_density_bw = ctk.DoubleVar(value=0.14)
        self.var_formant_density_show_raw = tk.BooleanVar(value=False)
        self.var_formant_density_show_contours = tk.BooleanVar(value=True)
        self.var_formant_density_facet = ctk.StringVar(value="按字表组分面")

        # Dynamic Options - Quality Check
        self.var_qc_view = ctk.StringVar(value="dual_check")

        # Pagination state
        self.current_preview_page = 0
        self.current_group_page = 0
        self.sort_by_count = False

        # Live refresh switch
        self.var_live_refresh = ctk.BooleanVar(value=True)
        self.var_high_precision = ctk.BooleanVar(value=False)
        self._debounce_timer_id = None

        # Legend configuration
        self.var_legend_outside = ctk.BooleanVar(value=True)

        # Overview heatmap settings
        self.var_overview_show_deviation = ctk.BooleanVar(value=True)
        self.var_sort_by_anomaly = ctk.BooleanVar(value=True)

        # Dynamic Options - Formant Space
        self.var_formant_ellipse = ctk.StringVar(value="95% 置信椭圆")
        self.var_formant_label_mode = ctk.StringVar(value="显示分组标签")
        self.var_formant_show_raw = ctk.BooleanVar(value=True)
        self.var_formant_time_gradient = ctk.BooleanVar(value=False)
        self.var_show_convex_hull = ctk.BooleanVar(value=False)
        self.var_show_diphthong_arrows = ctk.BooleanVar(value=False)
        self.var_formant_normalization = ctk.StringVar(value=self.project_tree.app_state_params.get('formant_normalization', "原始频率 (Hz)"))
        self.var_formant_axis_lock = ctk.BooleanVar(value=bool(self.project_tree.app_state_params.get('formant_axis_lock', False)))

        # Dynamic Options - Formant Trajectory
        self.var_formant_traj_style = ctk.StringVar(value="仅平均曲线")
        self.var_show_f3 = ctk.BooleanVar(value=bool(self.project_tree.app_state_params.get('show_f3', False)))
        self.var_formant_overview_mode = ctk.StringVar(value="F1 & F2 双轨")

        # Image Size & Pixel configuration
        self.var_image_ratio_mode = ctk.StringVar(value="默认")
        self.var_image_ratio_custom = ctk.DoubleVar(value=1.5)
        self.var_image_pixel_mode = ctk.StringVar(value="默认")
        self.var_image_pixel_custom = ctk.IntVar(value=1080)
        self.is_minimized = False
        self._adv_drawer_open = False

    def _init_group_filters(self):
        self._all_base_group_filter_entries = self._extract_active_data(
            self.all_speakers if self.all_speakers else [self.active_speaker],
            filter_groups=False,
            apply_chart_grouping=False,
        )
        all_entries = self._extract_active_data(self.all_speakers if self.all_speakers else [self.active_speaker], filter_groups=False)
        self._all_group_filter_entries = all_entries
        self.group_counts = {}
        for entry in all_entries:
            g = entry['group']
            self.group_counts[g] = self.group_counts.get(g, 0) + 1

        self.available_groups = sorted(list(self.group_counts.keys()))
        self.group_checkbox_vars = {}
        for g in self.available_groups:
            self.group_checkbox_vars[g] = ctk.BooleanVar(value=True)

    def _style_segmented_pill(self, seg_btn, selected_color=None, selected_hover=None):
        sel_color = selected_color or self.ui_primary
        sel_hover = selected_hover or self.ui_primary_hover
        try:
            seg_btn.configure(
                fg_color=("#E2E8F0", "#334155"),
                selected_color=sel_color,
                selected_hover_color=sel_hover,
                unselected_color=("#E2E8F0", "#334155"),
                unselected_hover_color=("#CBD5E1", "#475569"),
                text_color=("#334155", "#E2E8F0"),
                corner_radius=16,
                border_width=4,
                height=32,
            )
            orig_sel = seg_btn._select_button_by_value
            orig_unsel = seg_btn._unselect_button_by_value
            seg_btn._select_button_by_value = lambda v: (
                orig_sel(v),
                seg_btn._buttons_dict[v].configure(text_color="#FFFFFF") if v in getattr(seg_btn, "_buttons_dict", {}) else None,
            )
            seg_btn._unselect_button_by_value = lambda v: (
                orig_unsel(v),
                seg_btn._buttons_dict[v].configure(text_color=("#334155", "#E2E8F0")) if v in getattr(seg_btn, "_buttons_dict", {}) else None,
            )
            orig_set = seg_btn.set

            def _patched_set(val, *args, **kwargs):
                res = orig_set(val, *args, **kwargs)
                for k, btn in getattr(seg_btn, "_buttons_dict", {}).items():
                    btn.configure(text_color="#FFFFFF" if k == val else ("#334155", "#E2E8F0"))
                return res

            seg_btn.set = _patched_set
            curr_val = seg_btn.get()
            for k, btn in getattr(seg_btn, "_buttons_dict", {}).items():
                btn.configure(text_color="#FFFFFF" if k == curr_val else ("#334155", "#E2E8F0"))
        except Exception:
            pass

    def _build_gui(self):
        # Top Header Bar: Dual Work Mode Switch + 3 Publication Preset Themes
        self.grid_columnconfigure(0, weight=0, minsize=400)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=0)
        self.grid_rowconfigure(1, weight=1)

        self.top_mode_bar = ctk.CTkFrame(self, fg_color=("#FFFFFF", "#1E293B"), border_width=1, border_color=("#E5E7EB", "#475569"), corner_radius=12)
        self.top_mode_bar.grid(row=0, column=0, columnspan=2, sticky="ew", padx=15, pady=(10, 4))

        mode_row = ctk.CTkFrame(self.top_mode_bar, fg_color="transparent")
        mode_row.pack(fill=tk.X, padx=14, pady=(6, 4))

        ctk.CTkLabel(mode_row, text="工作模式:", font=self.font_small).pack(side=tk.LEFT, padx=(4, 6))
        self.seg_work_mode = ctk.CTkSegmentedButton(
            mode_row,
            values=["学术论文图表", "数据质检与异常排查"],
            variable=self.var_work_mode,
            font=self.font_main,
            fg_color=("#E2E8F0", "#334155"),
            selected_color=self.ui_primary,
            selected_hover_color=self.ui_primary_hover,
            unselected_color=("#E2E8F0", "#334155"),
            unselected_hover_color=("#CBD5E1", "#475569"),
            text_color=("#334155", "#E2E8F0"),
            corner_radius=16,
            border_width=4,
            height=32,
            command=self._on_work_mode_changed,
        )
        self.seg_work_mode.pack(side=tk.LEFT, padx=(0, 10))
        self._style_segmented_pill(self.seg_work_mode, self.ui_primary, self.ui_primary_hover)

        ctk.CTkFrame(mode_row, width=1, height=20, fg_color="#CBD5E1").pack(side=tk.LEFT, padx=(2, 10))

        ctk.CTkLabel(mode_row, text="出版样式预设:", font=self.font_small).pack(side=tk.LEFT, padx=(4, 6))
        self.seg_preset_theme = ctk.CTkSegmentedButton(
            mode_row,
            values=["中文期刊规范", "国际期刊规范", "汇报演示"],
            variable=self.var_preset_theme,
            font=self.font_small,
            fg_color=("#E2E8F0", "#334155"),
            selected_color="#4F46E5",
            selected_hover_color="#4338CA",
            unselected_color=("#E2E8F0", "#334155"),
            unselected_hover_color=("#CBD5E1", "#475569"),
            text_color=("#334155", "#E2E8F0"),
            corner_radius=16,
            border_width=4,
            height=32,
            command=self._on_preset_theme_changed,
        )
        self.seg_preset_theme.pack(side=tk.LEFT, padx=(0, 6))
        self._style_segmented_pill(self.seg_preset_theme, "#4F46E5", "#4338CA")

        # --- LEFT SIDE: Three-Step Scrollable Configuration Frame ---
        self.left_scroll = ctk.CTkScrollableFrame(self, width=390, fg_color=("#F9FAFB", "#2D3748"))
        self.left_scroll.grid(row=1, column=0, sticky="nsew", padx=(15, 8), pady=(4, 12))

        self._build_settings_cards()

        # --- RIGHT SIDE: Real-time Live Preview & Bottom Export Control Bar ---
        self.right_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.right_frame.grid(row=1, column=1, sticky="nsew", padx=(8, 15), pady=(4, 12))
        self.right_frame.grid_rowconfigure(0, weight=0)
        self.right_frame.grid_rowconfigure(1, weight=1)
        self.right_frame.grid_rowconfigure(2, weight=0)
        self.right_frame.grid_rowconfigure(3, weight=0)
        self.right_frame.grid_rowconfigure(4, weight=0)
        self.right_frame.grid_rowconfigure(5, weight=0)
        self.right_frame.grid_columnconfigure(0, weight=1)

        # Top Bar for floating button and native truncation banner
        self.preview_top_bar = ctk.CTkFrame(self.right_frame, fg_color="transparent")
        self.preview_top_bar.grid(row=0, column=0, sticky="ew", pady=(0, 4))

        self.btn_toggle_minimize = ctk.CTkButton(
            self.preview_top_bar, text="📌 悬浮模式", width=90, height=26, corner_radius=13,
            font=self.font_small, fg_color=("#E5E7EB", "#374151"), text_color=("#374151", "#E5E7EB"),
            hover_color=("#D1D5DB", "#4B5563"), bg_color="transparent", command=self.toggle_minimize
        )
        self.btn_toggle_minimize.pack(side=tk.LEFT)

        self.truncation_banner_frame = ctk.CTkFrame(
            self.preview_top_bar,
            fg_color=("#FEF2F2", "#450A0A"),
            corner_radius=12,
            height=26
        )
        self.lbl_truncation_banner = ctk.CTkLabel(
            self.truncation_banner_frame,
            text="",
            font=ctk.CTkFont(family="Microsoft YaHei", size=11, weight="bold"),
            fg_color="transparent",
            text_color=("#991B1B", "#FCA5A5"),
            height=26
        )
        self.lbl_truncation_banner.pack(padx=10, fill=tk.BOTH, expand=True)

        # Preview Canvas Container
        self.preview_wrapper = ctk.CTkFrame(self.right_frame, fg_color="transparent")
        self.preview_wrapper.grid(row=1, column=0, sticky="nsew", pady=(0, 6))
        self.preview_wrapper.grid_rowconfigure(0, weight=1)
        self.preview_wrapper.grid_columnconfigure(0, weight=1)

        self.preview_container = ctk.CTkFrame(self.preview_wrapper, fg_color="#FFFFFF", border_width=1, border_color="#D1D5DB")
        self.preview_container.place(relx=0.5, rely=0.5, anchor="center")
        self.preview_container.grid_rowconfigure(0, weight=1)
        self.preview_container.grid_columnconfigure(0, weight=1)

        self.preview_wrapper.bind("<Button-2>", self._show_context_menu, add="+")
        self.preview_wrapper.bind("<Button-3>", self._show_context_menu, add="+")

        self.preview_lbl = ctk.CTkLabel(self.preview_container, text="正在加载图表预览...", font=self.font_title, text_color="#6B7280")
        self.preview_lbl.grid(row=0, column=0)

        self._bind_chart_context_menu(self.preview_container)
        self.preview_wrapper.bind("<Configure>", lambda e: self.on_preview_wrapper_configure())

        # Pagination Frame
        self.pagination_frame = ctk.CTkFrame(self.right_frame, fg_color="transparent")
        self.pagination_frame.grid_columnconfigure(0, weight=1)
        self.pagination_frame.grid_columnconfigure(1, weight=0)
        self.pagination_frame.grid_columnconfigure(2, weight=1)

        self.btn_prev_page = ctk.CTkButton(
            self.pagination_frame, text="◀ 上一个发音人", width=120, height=28, corner_radius=14,
            fg_color=("#F3F4F6", "#374151"), text_color=("#374151", "#E5E7EB"), hover_color=("#E5E7EB", "#4B5563"),
            font=self.font_main, command=self._prev_page
        )
        self.btn_prev_page.grid(row=0, column=0, padx=10, sticky="e")

        self.lbl_page_info = ctk.CTkLabel(self.pagination_frame, text="发音人: - (0/0)", font=self.font_title)
        self.lbl_page_info.grid(row=0, column=1, padx=20)

        self.btn_next_page = ctk.CTkButton(
            self.pagination_frame, text="下一个发音人 ▶", width=120, height=28, corner_radius=14,
            fg_color=("#F3F4F6", "#374151"), text_color=("#374151", "#E5E7EB"), hover_color=("#E5E7EB", "#4B5563"),
            font=self.font_main, command=self._next_page
        )
        self.btn_next_page.grid(row=0, column=2, padx=10, sticky="w")

        # Group Pagination Frame
        self.group_pagination_frame = ctk.CTkFrame(self.right_frame, fg_color="transparent")
        self.group_pagination_frame.grid_columnconfigure(0, weight=1)
        self.group_pagination_frame.grid_columnconfigure(1, weight=0)
        self.group_pagination_frame.grid_columnconfigure(2, weight=1)

        self.btn_prev_group_page = ctk.CTkButton(
            self.group_pagination_frame, text="◀ 上一页", width=88, height=28, corner_radius=14,
            fg_color=("#F3F4F6", "#374151"), text_color=("#374151", "#E5E7EB"), hover_color=("#E5E7EB", "#4B5563"),
            font=self.font_main, command=self._prev_group_page
        )
        self.btn_prev_group_page.grid(row=0, column=0, padx=10, sticky="e")

        self.lbl_group_page_info = ctk.CTkLabel(
            self.group_pagination_frame, text="组别页码: - (0/0)",
            font=ctk.CTkFont(family="Microsoft YaHei", size=12, weight="bold")
        )
        self.lbl_group_page_info.grid(row=0, column=1, padx=20)

        self.btn_next_group_page = ctk.CTkButton(
            self.group_pagination_frame, text="下一页 ▶", width=88, height=28, corner_radius=14,
            fg_color=("#F3F4F6", "#374151"), text_color=("#374151", "#E5E7EB"), hover_color=("#E5E7EB", "#4B5563"),
            font=self.font_main, command=self._next_group_page
        )
        self.btn_next_group_page.grid(row=0, column=2, padx=10, sticky="w")

        # QC Hover Status Bar (hidden by default; shown on demand in QC hover)
        self.lbl_qc_hover_status = ctk.CTkLabel(
            self.right_frame,
            text="",
            font=self.font_small,
            text_color=("#4B5563", "#CBD5E1"),
            anchor="w",
        )
        self.lbl_qc_hover_status.grid(row=4, column=0, sticky="ew", padx=4, pady=(0, 3))
        self.lbl_qc_hover_status.grid_remove()

        # --- BOTTOM INDEPENDENT EXPORT CONTROL BAR ---
        self.bottom_frame = ctk.CTkFrame(
            self.right_frame,
            fg_color=("#FFFFFF", "#1E293B"),
            border_width=1,
            border_color=("#E5E7EB", "#475569"),
            corner_radius=12,
        )
        self.bottom_frame.grid(row=5, column=0, sticky="ew")
        self.bottom_export_bar = self.bottom_frame

        row_specs = ctk.CTkFrame(self.bottom_frame, fg_color="transparent")
        row_specs.pack(fill=tk.X, padx=10, pady=(6, 3))

        ctk.CTkLabel(row_specs, text="版面:", font=self.font_small).pack(side=tk.LEFT, padx=(0, 3))
        self.combo_layout_size = ctk.CTkOptionMenu(
            row_specs,
            values=["双栏通栏 150mm", "单栏 80mm", "单栏 85mm", "1.5栏 114mm", "双栏 170mm", "汇报宽屏 16:9", "自适应默认"],
            variable=self.var_layout_size,
            width=124,
            command=lambda _: self.trigger_preview_update(),
            **self.dropdown_kwargs,
        )
        self.combo_layout_size.pack(side=tk.LEFT, padx=(0, 6))
        self._apply_custom_arrow(self.combo_layout_size)

        ctk.CTkLabel(row_specs, text="比例:", font=self.font_small).pack(side=tk.LEFT, padx=(2, 3))
        self.combo_ratio_mode = ctk.CTkOptionMenu(
            row_specs,
            values=["默认", "4:3", "16:9", "3:2", "1:1", "16:10", "2:1", "自定义"],
            width=72,
            command=self._on_ratio_mode_changed,
            **self.dropdown_kwargs,
        )
        self.combo_ratio_mode.set("默认")
        self.combo_ratio_mode.pack(side=tk.LEFT, padx=(0, 6))
        self._apply_custom_arrow(self.combo_ratio_mode)

        # Inline Custom Aspect Ratio Controls (shown next to combo_ratio_mode when "自定义" is selected)
        self.custom_ratio_inline_frame = ctk.CTkFrame(row_specs, fg_color="transparent")
        self.slider_ratio_custom = ctk.CTkSlider(
            self.custom_ratio_inline_frame,
            width=76,
            height=16,
            from_=0.5,
            to=2.5,
            number_of_steps=40,
            variable=self.var_image_ratio_custom,
            progress_color=self.ui_primary,
            button_color=self.ui_primary,
            button_hover_color=self.ui_primary_hover,
            command=self._on_ratio_slider_change,
        )
        self.slider_ratio_custom.pack(side=tk.LEFT, padx=(0, 4))
        self.entry_ratio_custom = ctk.CTkEntry(
            self.custom_ratio_inline_frame,
            width=48,
            height=28,
            corner_radius=8,
            font=self.font_small,
            justify="center",
            fg_color=("#EEF2F6", "#334155"),
            border_width=1,
            border_color=("#CBD5E1", "#475569"),
        )
        self.entry_ratio_custom.insert(0, "1.50")
        self.entry_ratio_custom.pack(side=tk.LEFT)
        self.entry_ratio_custom.bind("<Return>", self._on_custom_ratio_entry_commit)
        self.entry_ratio_custom.bind("<FocusOut>", self._on_custom_ratio_entry_commit)

        # Keep combo_dpi attribute instantiated (unpacked) for internal preset/API compatibility
        self.combo_dpi = ctk.CTkOptionMenu(
            row_specs,
            values=["600 DPI", "300 DPI", "150 DPI"],
            variable=self.var_dpi,
            width=84,
            command=lambda _: self.trigger_preview_update(),
            **self.dropdown_kwargs,
        )
        self._apply_custom_arrow(self.combo_dpi)

        ctk.CTkLabel(row_specs, text="格式:", font=self.font_small).pack(side=tk.LEFT, padx=(2, 3))
        self.combo_format = ctk.CTkOptionMenu(
            row_specs,
            values=["PDF 文档 (.pdf)", "SVG 矢量图 (.svg)", "PNG 图片 (.png)"],
            width=126,
            command=self._on_format_changed,
            **self.dropdown_kwargs,
        )
        self.combo_format.set("PDF 文档 (.pdf)")
        self.combo_format.pack(side=tk.LEFT, padx=(0, 4))
        self._apply_custom_arrow(self.combo_format)

        row_actions = ctk.CTkFrame(self.bottom_frame, fg_color="transparent")
        row_actions.pack(fill=tk.X, padx=10, pady=(2, 6))

        self.btn_manual_refresh = ctk.CTkButton(
            row_actions, text="🔄 刷新预览", width=96, height=32, corner_radius=16,
            fg_color="#E5E7EB", text_color="#374151", hover_color="#D1D5DB",
            font=self.font_small, command=self.manual_refresh_preview
        )
        self.btn_manual_refresh.pack(side=tk.LEFT, padx=(0, 6))

        self.switch_live_refresh = ctk.CTkSwitch(
            row_actions, text="实时刷新", variable=self.var_live_refresh,
            font=self.font_small, progress_color=self.ui_primary,
            button_hover_color=self.ui_primary_hover, command=self._on_live_refresh_toggle
        )
        self.switch_live_refresh.pack(side=tk.LEFT, padx=6)

        self.btn_export_data = ctk.CTkButton(
            row_actions, text="📊 导出数据", width=96, height=32, corner_radius=16,
            font=self.font_small, fg_color="#3B82F6", hover_color="#2563EB",
            command=self.export_chart_table_data
        )
        self.btn_export_data.pack(side=tk.RIGHT, padx=4)

        self.btn_export = ctk.CTkButton(
            row_actions, text="💾 导出图表", width=106, height=32, corner_radius=16,
            font=self.font_main, fg_color="#10B981", hover_color="#059669",
            command=self.on_confirm
        )
        self.btn_export.pack(side=tk.RIGHT, padx=4)

        self.btn_copy_image = ctk.CTkButton(
            row_actions, text="📋 复制图片", width=96, height=32, corner_radius=16,
            font=self.font_small, fg_color="#6366F1", hover_color="#4F46E5",
            command=self.copy_figure_to_clipboard
        )
        self.btn_copy_image.pack(side=tk.RIGHT, padx=4)

        self.btn_restore = ctk.CTkButton(
            row_actions, text="🗗 还原", width=80, height=28, corner_radius=14,
            fg_color=("#E5E7EB", "#374151"), text_color=("#374151", "#E5E7EB"),
            hover_color=("#D1D5DB", "#4B5563"),
            font=self.font_small, command=self.toggle_minimize
        )

    def _on_work_mode_changed(self, val):
        mode = self._normalize_work_mode(val)
        self.work_mode = mode
        if mode != "qc" and hasattr(self, "lbl_qc_hover_status"):
            try:
                self.lbl_qc_hover_status.configure(text="")
                self.lbl_qc_hover_status.grid_remove()
            except Exception:
                pass
        if self._is_formant_mode():
            type_values = self._get_formant_chart_type_values(work_mode=mode)
            default_type = type_values[0]
        else:
            type_values = self._get_tone_chart_type_values(work_mode=mode)
            default_type = type_values[0]
        if hasattr(self, "combo_type"):
            self.combo_type.configure(values=type_values)
            self.combo_type.set(default_type)
            self._on_type_changed(default_type)

    def _on_preset_theme_changed(self, val):
        preset = self.apply_publication_preset(val)
        if hasattr(self, "combo_layout_size") and preset.get("default_layout_size"):
            self.combo_layout_size.set(preset["default_layout_size"])
        if hasattr(self, "combo_dpi") and preset.get("default_dpi"):
            self.combo_dpi.set(f"{int(preset['default_dpi'])} DPI")
        if hasattr(self, "var_show_title"):
            self.var_show_title.set(bool(preset.get("show_title", False)))
        if preset["name"] == "presentation" and hasattr(self, "combo_format"):
            self.combo_format.set("PNG 图片 (.png)")
            self._update_pixel_options_state()
        self.update_preview()

    def _prev_page(self):
        if not self.all_speakers:
            return
        self.current_preview_page = (self.current_preview_page - 1) % len(self.all_speakers)
        self.update_preview()

    def _next_page(self):
        if not self.all_speakers:
            return
        self.current_preview_page = (self.current_preview_page + 1) % len(self.all_speakers)
        self.update_preview()

    def _prev_group_page(self):
        data = self._get_current_data_entries()
        if not data:
            return
        pagination_state = self._get_group_pagination_state(data, self.var_chart_type.get(), self.combo_groupby.get())
        total_pages = pagination_state['total_pages']
        self.current_group_page = (self.current_group_page - 1) % total_pages
        self.update_preview()

    def _next_group_page(self):
        data = self._get_current_data_entries()
        if not data:
            return
        pagination_state = self._get_group_pagination_state(data, self.var_chart_type.get(), self.combo_groupby.get())
        total_pages = pagination_state['total_pages']
        self.current_group_page = (self.current_group_page + 1) % total_pages
        self.update_preview()

    def _build_settings_cards(self):
        card_padding = {"padx": 8, "pady": 5}

        # ======================================================================
        # 【第一步：选择图表与声学单位】
        # ======================================================================
        self.step1_card = ctk.CTkFrame(self.left_scroll, fg_color=("#FFFFFF", "#1E293B"), border_width=1, border_color=("#E5E7EB", "#475569"), corner_radius=12)
        self.step1_card.pack(fill=tk.X, **card_padding)
        card1 = self.step1_card

        ctk.CTkLabel(card1, text="【第一步：选择图表与声学单位】", font=self.font_title).pack(anchor="w", padx=12, pady=(8, 4))

        if self._is_formant_mode():
            type_values = self._get_formant_chart_type_values()
            default_type = "1. 元音声学空间图"
            scale_values = ["原始频率 (Hz)", "Bark (听觉巴克)", "ERB (等效矩形带宽)", "Lobanov z-score (学术标准)"]
            scale_default = "原始频率 (Hz)"
        else:
            type_values = self._get_tone_chart_type_values()
            default_type = "1. 声调格局与时序轮廓图"
            scale_values = ["T 值 (五度标调)", "Hz (基频绝对频率)"]
            scale_default = "T 值 (五度标调)"

        row_s1 = ctk.CTkFrame(card1, fg_color="transparent")
        row_s1.pack(fill=tk.X, padx=12, pady=(2, 8))
        row_s1.grid_columnconfigure(0, weight=3)
        row_s1.grid_columnconfigure(1, weight=2)

        ctk.CTkLabel(row_s1, text="图表类型:", font=self.font_small).grid(row=0, column=0, sticky="w", padx=(0, 4))
        ctk.CTkLabel(row_s1, text="声学标度:", font=self.font_small).grid(row=0, column=1, sticky="w", padx=(4, 0))

        self.combo_type = ctk.CTkOptionMenu(row_s1, values=type_values, command=self._on_type_changed, **self.dropdown_kwargs)
        self.combo_type.set(default_type)
        self.combo_type.grid(row=1, column=0, sticky="ew", padx=(0, 4), pady=(2, 0))
        self._apply_custom_arrow(self.combo_type)

        self.combo_scale = ctk.CTkOptionMenu(row_s1, values=scale_values, command=self._on_scale_changed, **self.dropdown_kwargs)
        self.combo_scale.set(scale_default)
        self.combo_scale.grid(row=1, column=1, sticky="ew", padx=(4, 0), pady=(2, 0))
        self._apply_custom_arrow(self.combo_scale)

        # ======================================================================
        # 【第二步：对比维度与数据范围】
        # ======================================================================
        self.step2_card = ctk.CTkFrame(self.left_scroll, fg_color=("#FFFFFF", "#1E293B"), border_width=1, border_color=("#E5E7EB", "#475569"), corner_radius=12)
        self.step2_card.pack(fill=tk.X, **card_padding)
        card2 = self.step2_card

        ctk.CTkLabel(card2, text="【第二步：对比维度与数据范围】", font=self.font_title).pack(anchor="w", padx=12, pady=(8, 4))

        dim_grid = ctk.CTkFrame(card2, fg_color="transparent")
        dim_grid.pack(fill=tk.X, padx=12, pady=(0, 6))
        dim_grid.grid_columnconfigure(0, weight=1)
        dim_grid.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(dim_grid, text="曲线/颜色对比 (Color by):", font=self.font_small).grid(row=0, column=0, sticky="w", padx=(0, 4))
        ctk.CTkLabel(dim_grid, text="子图分面排版 (Facet by):", font=self.font_small).grid(row=0, column=1, sticky="w", padx=(4, 0))

        color_by_values = ["按字表组别", "按词语", "按单字音节", "按发音人", "按自定义字段..."]
        self.combo_color_by = ctk.CTkOptionMenu(dim_grid, values=color_by_values, command=self._on_groupby_changed, **self.dropdown_kwargs)
        self.combo_color_by.set("按字表组别")
        self.combo_color_by.grid(row=1, column=0, sticky="ew", padx=(0, 4), pady=(2, 6))
        self._apply_custom_arrow(self.combo_color_by)
        # Keep backward-compatible alias self.combo_groupby
        self.combo_groupby = self.combo_color_by

        facet_by_values = ["不分面 (单图叠加)", "按字表组别分面", "按音节位置分面", "按发音人分面"]
        self.combo_facet_by = ctk.CTkOptionMenu(dim_grid, values=facet_by_values, command=self._on_facet_by_changed, **self.dropdown_kwargs)
        self.combo_facet_by.set("不分面 (单图叠加)")
        self.combo_facet_by.grid(row=1, column=1, sticky="ew", padx=(4, 0), pady=(2, 6))
        self._apply_custom_arrow(self.combo_facet_by)

        ctk.CTkLabel(dim_grid, text="发音人范围:", font=self.font_small).grid(row=2, column=0, columnspan=2, sticky="w")
        self.combo_scope = ctk.CTkOptionMenu(
            dim_grid,
            values=["仅当前发音人", "所有发音人(分别导出)", "所有发音人(整合导出)"],
            command=self._on_scope_changed,
            **self.dropdown_kwargs,
        )
        if self.initial_mode == 'separate':
            self.combo_scope.set("所有发音人(分别导出)")
        elif self.initial_mode == 'integrated':
            self.combo_scope.set("所有发音人(整合导出)")
        else:
            self.combo_scope.set("仅当前发音人")
        self.combo_scope.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(2, 4))
        self._apply_custom_arrow(self.combo_scope)
        if not self.all_speakers or len(self.all_speakers) <= 1:
            self.combo_scope.configure(state="disabled")

        # Embedded Group Filter inside Step 2
        self.card_filter = ctk.CTkFrame(card2, fg_color=("#F8FAFC", "#263244"), corner_radius=10)
        self.card_filter.pack(fill=tk.X, padx=10, pady=(2, 8))

        search_btn_row = ctk.CTkFrame(self.card_filter, fg_color="transparent")
        search_btn_row.pack(fill=tk.X, padx=8, pady=(6, 3))

        self.lbl_group_filter_title = ctk.CTkLabel(search_btn_row, text="参与组别:", font=self.font_small)
        self.lbl_group_filter_title.pack(side=tk.LEFT, padx=(0, 4))

        self.search_group_var = ctk.StringVar()
        self.search_group_var.trace_add("write", lambda *args: self._filter_groups_list())

        self.entry_search_group = ctk.CTkEntry(
            search_btn_row, placeholder_text="搜索...", textvariable=self.search_group_var,
            font=self.font_small, height=26, fg_color="white", text_color="#1F2937",
            border_width=1, border_color="#E5E7EB", corner_radius=13
        )
        self.entry_search_group.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))

        ctk.CTkButton(
            search_btn_row, text="全选", width=46, height=24, corner_radius=12,
            fg_color=self.ui_primary, hover_color=self.ui_primary_hover,
            font=self.font_small, command=self._select_all_groups
        ).pack(side=tk.LEFT, padx=2)

        ctk.CTkButton(
            search_btn_row, text="反选", width=46, height=24, corner_radius=12,
            fg_color=self.ui_primary, hover_color=self.ui_primary_hover,
            font=self.font_small, command=self._reverse_groups
        ).pack(side=tk.LEFT, padx=2)

        self.filter_scroll = ctk.CTkScrollableFrame(self.card_filter, height=90, fg_color="transparent")
        self.filter_scroll.pack(fill=tk.X, padx=6, pady=2)
        try:
            self.filter_scroll._parent_canvas.configure(yscrollincrement=15)
        except Exception:
            pass

        def on_enter_filter_scroll(e):
            method = getattr(self.left_scroll, "_disable_all_bindings", None)
            if callable(method):
                try:
                    method()
                except Exception:
                    pass

        def on_leave_filter_scroll(e):
            x = self.filter_scroll.winfo_pointerx() - self.filter_scroll.winfo_rootx()
            y = self.filter_scroll.winfo_pointery() - self.filter_scroll.winfo_rooty()
            w = self.filter_scroll.winfo_width()
            h = self.filter_scroll.winfo_height()
            if not (0 <= x < w and 0 <= y < h):
                method = getattr(self.left_scroll, "_enable_all_bindings", None)
                if callable(method):
                    try:
                        method()
                    except Exception:
                        pass

        self.filter_scroll.bind("<Enter>", on_enter_filter_scroll, add="+")
        self.filter_scroll.bind("<Leave>", on_leave_filter_scroll, add="+")

        util_btn_frame2 = ctk.CTkFrame(self.card_filter, fg_color="transparent")
        util_btn_frame2.pack(fill=tk.X, padx=8, pady=(2, 6))

        self.btn_toggle_sort = ctk.CTkButton(
            util_btn_frame2, text="按样本量排序", width=88, height=22, corner_radius=11, font=self.font_small,
            fg_color=("#E5E7EB", "#374151"), text_color=("#374151", "#E5E7EB"), hover_color=("#D1D5DB", "#4B5563"),
            command=self._toggle_groups_sorting
        )
        self.btn_toggle_sort.pack(side=tk.LEFT, padx=2)

        ctk.CTkLabel(util_btn_frame2, text="样本量 >", font=self.font_small).pack(side=tk.LEFT, padx=(8, 2))
        self.entry_min_count = ctk.CTkEntry(util_btn_frame2, width=36, height=22, font=self.font_small, justify="center")
        self.entry_min_count.insert(0, "0")
        self.entry_min_count.pack(side=tk.LEFT, padx=2)
        self.entry_min_count.bind("<KeyRelease>", lambda e: self._populate_groups_list())

        # ======================================================================
        # 【第三步：图表要素】 + ▸ 高级图形参数 折叠抽屉
        # ======================================================================
        self.dynamic_card = ctk.CTkFrame(self.left_scroll, fg_color=("#FFFFFF", "#1E293B"), border_width=1, border_color=("#E5E7EB", "#475569"), corner_radius=12)
        self.dynamic_card.pack(fill=tk.X, **card_padding)
        self.step3_card = self.dynamic_card

        self.dynamic_title = ctk.CTkLabel(self.dynamic_card, text="【第三步：图表要素】", font=self.font_title)
        self.dynamic_title.pack(anchor="w", padx=12, pady=(8, 4))

        self.dynamic_content_frame = ctk.CTkFrame(self.dynamic_card, fg_color="transparent")
        self.dynamic_content_frame.pack(fill=tk.X, padx=12, pady=(0, 6))

        if self._is_formant_mode():
            self._build_formant_space_settings()
        else:
            self._build_contour_settings()

        # Collapsible Drawer: ▸ 高级图形参数 (字号/图例/极值过滤)
        self.btn_adv_drawer = ctk.CTkButton(
            self.dynamic_card,
            text="▸ 高级图形参数 (字号/图例/极值过滤)",
            height=28,
            corner_radius=8,
            anchor="w",
            font=self.font_small,
            fg_color=("#F3F4F6", "#334155"),
            text_color=("#374151", "#E5E7EB"),
            hover_color=("#E5E7EB", "#475569"),
            command=self._toggle_adv_drawer,
        )
        self.btn_adv_drawer.pack(fill=tk.X, padx=12, pady=(2, 8))

        self.adv_drawer_frame = ctk.CTkFrame(self.dynamic_card, fg_color=("#F8FAFC", "#263244"), corner_radius=10)
        self.adv_drawer_frame.grid_columnconfigure(0, weight=1)
        self.adv_drawer_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(self.adv_drawer_frame, text="图例位置:", font=self.font_small).grid(row=0, column=0, sticky="w", padx=10, pady=(6, 2))
        ctk.CTkLabel(self.adv_drawer_frame, text="导出意图预设:", font=self.font_small).grid(row=0, column=1, sticky="w", padx=10, pady=(6, 2))

        self.combo_legend_loc = ctk.CTkOptionMenu(self.adv_drawer_frame, values=["右上", "右下", "左上", "左下"], command=lambda _: self.update_preview(), **self.dropdown_kwargs)
        self.combo_legend_loc.set("右上")
        self.combo_legend_loc.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 6))
        self._apply_custom_arrow(self.combo_legend_loc)

        intention_values = ["论文主图 (清晰对比)", "附录图册 (完整数据)", "数据诊断 (质量排查)"]
        self.combo_intention = ctk.CTkOptionMenu(self.adv_drawer_frame, values=intention_values, command=self._on_intention_changed, **self.dropdown_kwargs)
        self.combo_intention.set("论文主图 (清晰对比)")
        self.combo_intention.grid(row=1, column=1, sticky="ew", padx=10, pady=(0, 6))
        self._apply_custom_arrow(self.combo_intention)

        self.cb_legend_outside = self._make_checkbox(
            self.adv_drawer_frame, text="图例置于图表主体外侧", variable=self.var_legend_outside,
            command=self.update_preview
        )
        self.cb_legend_outside.grid(row=2, column=0, sticky="w", padx=10, pady=(2, 6))

        self.cb_show_title = self._make_checkbox(
            self.adv_drawer_frame, text="显示图内顶部大标题", variable=self.var_show_title,
            command=self.update_preview
        )
        self.cb_show_title.grid(row=2, column=1, sticky="w", padx=10, pady=(2, 6))

        self.switch_high_precision = ctk.CTkSwitch(
            self.adv_drawer_frame, text="高渲染精细度", variable=self.var_high_precision,
            font=self.font_small, progress_color=self.ui_primary, command=self.update_preview
        )
        self.switch_high_precision.grid(row=3, column=0, sticky="w", padx=10, pady=(2, 8))

        group_rule_bar = ctk.CTkFrame(self.adv_drawer_frame, fg_color="transparent")
        group_rule_bar.grid(row=3, column=1, sticky="ew", padx=10, pady=(2, 8))
        self.lbl_chart_group_rule = ctk.CTkLabel(
            group_rule_bar,
            text=self._format_chart_group_rule_summary(self.chart_group_rule),
            font=self.font_small,
            anchor="w",
        )
        self.lbl_chart_group_rule.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ctk.CTkButton(
            group_rule_bar, text="自定义分组规则", width=96, height=24, corner_radius=12,
            font=self.font_small, fg_color=(self.ui_primary, self.ui_primary_hover),
            command=self._open_chart_group_rule_dialog,
        ).pack(side=tk.RIGHT)

        # Keep hidden/compatible controls for custom ratio & pixel sliders
        self.combo_pixel_mode = ctk.CTkOptionMenu(self.adv_drawer_frame, values=["默认", "480 px", "600 px", "720 px", "1080 px", "1440 px", "2160 px", "自定义"], command=self._on_pixel_mode_changed, **self.dropdown_kwargs)
        self.combo_pixel_mode.set("默认")
        self.lbl_ratio_val = ctk.CTkLabel(self.adv_drawer_frame, text="1.50", font=self.font_small)
        self.entry_pixel_custom = ctk.CTkEntry(self.adv_drawer_frame, placeholder_text="1080", font=self.font_small, height=26)
        self.entry_pixel_custom.insert(0, "1080")

        self._bind_left_scroll_wheel_recursive(self.left_scroll)

    def _toggle_adv_drawer(self):
        self._adv_drawer_open = not getattr(self, "_adv_drawer_open", False)
        if self._adv_drawer_open:
            self.btn_adv_drawer.configure(text="▾ 高级图形参数 (字号/图例/极值过滤)")
            self.adv_drawer_frame.pack(fill=tk.X, padx=12, pady=(0, 10))
        else:
            self.btn_adv_drawer.configure(text="▸ 高级图形参数 (字号/图例/极值过滤)")
            self.adv_drawer_frame.pack_forget()

    def _on_scale_changed(self, val):
        if self._is_formant_mode():
            self.var_formant_normalization.set(val)
            if hasattr(self, "combo_formant_normalization"):
                try:
                    self.combo_formant_normalization.set(val)
                except Exception:
                    pass
        self.update_preview()

    def _on_facet_by_changed(self, val):
        self.var_facet_by.set(val)
        for attr in ("combo_contour_facet", "combo_density_facet", "combo_formant_density_facet"):
            if hasattr(self, attr):
                try:
                    getattr(self, attr).set(val)
                except Exception:
                    pass
        self.update_preview()

    def _sync_facet_from_child(self, val):
        if hasattr(self, "combo_facet_by"):
            try:
                self.combo_facet_by.set(val)
            except Exception:
                pass
        self.trigger_preview_update()

    def _on_filter_scroll_wheel(self, event):
        canvas = getattr(self.filter_scroll, "_parent_canvas", None)
        if canvas is None:
            return "break"

        if getattr(event, "num", None) == 4:
            steps = -3
        elif getattr(event, "num", None) == 5:
            steps = 3
        else:
            delta = getattr(event, "delta", 0)
            if delta == 0:
                return "break"
            if abs(delta) >= 120:
                steps = -int(delta / 120) * 3
            else:
                steps = -1 if delta > 0 else 1

        if canvas.yview() != (0.0, 1.0):
            canvas.yview_scroll(steps, "units")
        return "break"

    def _bind_filter_scroll_wheel_recursive(self, widget):
        if not getattr(widget, "_phontracer_filter_wheel_bound", False):
            for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
                widget.bind(sequence, self._on_filter_scroll_wheel, add="+")
            widget._phontracer_filter_wheel_bound = True

        for child in widget.winfo_children():
            self._bind_filter_scroll_wheel_recursive(child)

    def _on_left_scroll_wheel(self, event):
        if hasattr(self, 'filter_scroll') and self.filter_scroll:
            try:
                if str(event.widget).startswith(str(self.filter_scroll)):
                    return "break"
            except Exception:
                pass

        canvas = getattr(self.left_scroll, "_parent_canvas", None)
        if canvas is None:
            return "break"

        if getattr(event, "num", None) == 4:
            steps = -60
        elif getattr(event, "num", None) == 5:
            steps = 60
        else:
            delta = getattr(event, "delta", 0)
            if delta == 0:
                return "break"
            if abs(delta) >= 120:
                steps = -int(delta / 120) * 60
            else:
                step_mag = max(2, int(abs(delta) * 0.5))
                steps = -step_mag if delta > 0 else step_mag

        try:
            canvas.configure(yscrollincrement=1)
        except Exception:
            pass

        if canvas.yview() != (0.0, 1.0):
            canvas.yview_scroll(steps, "units")
        return "break"

    def _bind_left_scroll_wheel_recursive(self, widget):
        if widget == getattr(self, "filter_scroll", None):
            return

        if not getattr(widget, "_phontracer_left_wheel_bound", False):
            for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
                widget.bind(sequence, self._on_left_scroll_wheel, add="+")
            widget._phontracer_left_wheel_bound = True

        for child in widget.winfo_children():
            self._bind_left_scroll_wheel_recursive(child)

    def _update_chart_group_rule_label(self):
        if hasattr(self, "lbl_chart_group_rule"):
            text = self._format_chart_group_rule_summary(self.chart_group_rule)
            groupby_text = self.combo_groupby.get() if hasattr(self, "combo_groupby") else ""
            rule_active = self.chart_group_rule.get("source") != "default"
            if self._chart_grouping_preserves_original_group() and rule_active:
                text += "（当前图表仅作为筛选条件）"
            elif groupby_text in {"按词语", "按单字音节", "按单字/音节", "按发音人"} and rule_active:
                text += "（当前绘图维度会保留专用分类）"
            self.lbl_chart_group_rule.configure(text=text)

    def _apply_chart_group_rule_from_ui(self, rule):
        self.chart_group_rule = self._normalize_chart_group_rule(rule)
        self.current_preview_page = 0
        self.current_group_page = 0
        self._update_chart_group_rule_label()
        self._update_group_filters()
        self._on_group_filter_changed()

    def _open_chart_group_rule_dialog(self):
        options = self._collect_chart_grouping_options()
        current_rule = self._normalize_chart_group_rule(self.chart_group_rule)

        source_to_label = {
            "default": "默认组",
            "item_tags": "词项标签",
            "group_tags": "组标签",
            "item_meta": "自定义字段",
            "metadata_source": "复核状态",
            "label": "词项",
            "speaker_name": "发音人",
        }
        label_to_source = {label: source for source, label in source_to_label.items()}
        mode_to_label = {
            "each": "每个选中标签一组",
            "contains": "包含/不包含指定标签",
            "filter_default": "筛选标签后仍按默认组",
        }
        label_to_mode = {label: mode for mode, label in mode_to_label.items()}

        dialog = ctk.CTkToplevel(self)
        dialog.title("PhonTracer语音学标注与声学分析系统 V1.0 - 图表分组规则")
        dialog.geometry("680x620")
        dialog.resizable(False, False)
        dialog.transient(self)
        dialog.grab_set()

        outer = ctk.CTkFrame(dialog, fg_color=("#F8FAFC", "#111827"))
        outer.pack(fill=tk.BOTH, expand=True)

        card = ctk.CTkFrame(
            outer,
            fg_color=("#FFFFFF", "#1E293B"),
            border_width=1,
            border_color=("#E5E7EB", "#475569"),
            corner_radius=18,
        )
        card.pack(fill=tk.BOTH, expand=True, padx=18, pady=18)
        card.grid_columnconfigure(0, weight=1)
        card.grid_columnconfigure(1, weight=1)
        card.grid_rowconfigure(5, weight=1)

        ctk.CTkLabel(card, text="图表分组规则", font=ctk.CTkFont(family="Microsoft YaHei", size=18, weight="bold")).grid(
            row=0, column=0, columnspan=2, sticky="w", padx=20, pady=(18, 4)
        )
        ctk.CTkLabel(
            card,
            text="选择图表如何生成组别。默认不会改动字表组；标签分组更适合探索，自定义字段更适合正式比较。",
            font=self.font_small,
            text_color=("#6B7280", "#CBD5E1"),
            wraplength=600,
            justify="left",
        ).grid(row=1, column=0, columnspan=2, sticky="w", padx=20, pady=(0, 14))

        source_var = ctk.StringVar(value=source_to_label.get(current_rule["source"], "默认组"))
        mode_var = ctk.StringVar(value=mode_to_label.get(current_rule["tag_mode"], mode_to_label["each"]))
        meta_fields = sorted(options.get("item_meta", {}).keys())
        initial_field = current_rule.get("field_name") if current_rule.get("field_name") in meta_fields else (meta_fields[0] if meta_fields else "")
        field_var = ctk.StringVar(value=initial_field or "无可用字段")

        ctk.CTkLabel(card, text="分组来源", font=self.font_small).grid(row=2, column=0, sticky="w", padx=(20, 8), pady=(0, 3))
        ctk.CTkLabel(card, text="标签模式 / 字段", font=self.font_small).grid(row=2, column=1, sticky="w", padx=(8, 20), pady=(0, 3))

        source_menu = ctk.CTkOptionMenu(
            card,
            values=list(source_to_label.values()),
            variable=source_var,
            **self.dropdown_kwargs,
        )
        source_menu.grid(row=3, column=0, sticky="ew", padx=(20, 8), pady=(0, 10))
        self._apply_custom_arrow(source_menu)

        mode_menu = ctk.CTkOptionMenu(card, values=list(mode_to_label.values()), variable=mode_var, **self.dropdown_kwargs)
        mode_menu.grid(row=3, column=1, sticky="ew", padx=(8, 20), pady=(0, 10))
        self._apply_custom_arrow(mode_menu)

        field_menu = ctk.CTkOptionMenu(card, values=meta_fields or ["无可用字段"], variable=field_var, **self.dropdown_kwargs)
        field_menu.grid(row=4, column=1, sticky="ew", padx=(8, 20), pady=(0, 10))
        self._apply_custom_arrow(field_menu)

        values_panel = ctk.CTkFrame(card, fg_color=("#F8FAFC", "#263244"), corner_radius=14)
        values_panel.grid(row=5, column=0, sticky="nsew", padx=(20, 8), pady=(0, 14))
        values_panel.grid_rowconfigure(1, weight=1)
        values_panel.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(values_panel, text="参与标签", font=self.font_title).grid(row=0, column=0, sticky="w", padx=14, pady=(12, 4))
        values_scroll = ctk.CTkScrollableFrame(values_panel, fg_color="transparent", height=230)
        values_scroll.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 8))

        preview_panel = ctk.CTkFrame(card, fg_color=("#F8FAFC", "#263244"), corner_radius=14)
        preview_panel.grid(row=5, column=1, sticky="nsew", padx=(8, 20), pady=(0, 14))
        preview_panel.grid_rowconfigure(2, weight=1)
        preview_panel.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(preview_panel, text="分组预览", font=self.font_title).grid(row=0, column=0, sticky="w", padx=14, pady=(12, 4))
        preview_hint = ctk.CTkLabel(preview_panel, text="", font=self.font_small, text_color=("#6B7280", "#CBD5E1"), anchor="w")
        preview_hint.grid(row=1, column=0, sticky="ew", padx=14, pady=(0, 4))
        preview_scroll = ctk.CTkScrollableFrame(preview_panel, fg_color="transparent", height=230)
        preview_scroll.grid(row=2, column=0, sticky="nsew", padx=8, pady=(0, 8))

        selected_vars = {}

        def current_source():
            return label_to_source.get(source_var.get(), "default")

        def current_mode():
            return label_to_mode.get(mode_var.get(), "each")

        def build_rule():
            selected = [name for name, var in selected_vars.items() if var.get()]
            if current_mode() == "contains" and selected:
                selected = selected[:1]
            return self._normalize_chart_group_rule({
                "source": current_source(),
                "tag_mode": current_mode(),
                "selected_values": selected,
                "field_name": field_var.get() if field_var.get() != "无可用字段" else "",
            })

        def candidate_tags():
            source = current_source()
            if source == "item_tags":
                return options.get("item_tags", Counter())
            if source == "group_tags":
                return options.get("group_tags", Counter())
            return Counter()

        def refresh_preview():
            for child in preview_scroll.winfo_children():
                child.destroy()
            rule = build_rule()
            counts = self._chart_group_counts_for_rule(rule)
            if not counts:
                ctk.CTkLabel(
                    preview_scroll,
                    text="当前规则没有匹配到可绘制条目。",
                    font=self.font_small,
                    text_color=("#6B7280", "#CBD5E1"),
                ).pack(anchor="w", padx=8, pady=8)
                preview_hint.configure(text="0 组")
                return
            ordered = sorted(counts.items(), key=lambda item: (-item[1], str(item[0])))
            hint = f"{len(ordered)} 组 | {sum(counts.values())} 项"
            if len(ordered) > 8:
                hint += " | 论文主图建议筛选到 8 组以内"
            preview_hint.configure(text=hint)
            for idx, (name, count) in enumerate(ordered[:24]):
                row = ctk.CTkFrame(
                    preview_scroll,
                    fg_color=("#FFFFFF", "#1E293B") if idx % 2 == 0 else ("#EEF2FF", "#334155"),
                    corner_radius=10,
                )
                row.pack(fill=tk.X, padx=4, pady=3)
                ctk.CTkLabel(row, text=str(name), font=self.font_small, anchor="w").pack(side=tk.LEFT, fill=tk.X, expand=True, padx=10, pady=6)
                ctk.CTkLabel(row, text=f"{count} 项", font=self.font_small, text_color=("#2563EB", "#93C5FD")).pack(side=tk.RIGHT, padx=10, pady=6)

        def refresh_values():
            for child in values_scroll.winfo_children():
                child.destroy()
            selected_vars.clear()
            source = current_source()
            if source not in {"item_tags", "group_tags"}:
                ctk.CTkLabel(
                    values_scroll,
                    text="该来源会按实际取值自动分组，不需要选择标签。",
                    font=self.font_small,
                    text_color=("#6B7280", "#CBD5E1"),
                    wraplength=250,
                    justify="left",
                ).pack(anchor="w", padx=10, pady=8)
                refresh_preview()
                return

            tags = candidate_tags()
            if not tags:
                ctk.CTkLabel(
                    values_scroll,
                    text="当前工程没有可用标签。",
                    font=self.font_small,
                    text_color=("#6B7280", "#CBD5E1"),
                ).pack(anchor="w", padx=10, pady=8)
                refresh_preview()
                return

            selected_now = set(current_rule.get("selected_values", [])) if source == current_rule.get("source") else set()
            for tag, count in sorted(tags.items(), key=lambda item: (-item[1], str(item[0]))):
                var = ctk.BooleanVar(value=(not selected_now or tag in selected_now))
                selected_vars[tag] = var
                row = self._make_checkbox(
                    values_scroll,
                    text=f"{tag} ({count}项)",
                    variable=var,
                    command=refresh_preview,
                )
                row.pack(anchor="w", padx=10, pady=4)
            refresh_preview()

        def refresh_mode_state(*_):
            source = current_source()
            if source in {"item_tags", "group_tags"}:
                mode_menu.configure(values=list(mode_to_label.values()), state="normal")
                if mode_var.get() not in mode_to_label.values():
                    mode_var.set(mode_to_label["each"])
            else:
                mode_menu.configure(values=["按字段值分组"], state="disabled")
                mode_var.set("按字段值分组")
            if source == "item_meta":
                field_menu.configure(state="normal")
                if field_var.get() == "无可用字段" and meta_fields:
                    field_var.set(meta_fields[0])
            else:
                field_menu.configure(state="disabled")
            refresh_values()

        source_menu.configure(command=lambda _=None: refresh_mode_state())
        mode_menu.configure(command=lambda _=None: refresh_preview())
        field_menu.configure(command=lambda _=None: refresh_preview())
        refresh_mode_state()

        button_row = ctk.CTkFrame(card, fg_color="transparent")
        button_row.grid(row=6, column=0, columnspan=2, sticky="ew", padx=20, pady=(0, 18))
        button_row.grid_columnconfigure(0, weight=1)
        ctk.CTkButton(
            button_row,
            text="恢复默认",
            width=110,
            height=32,
            corner_radius=16,
            font=self.font_small,
            fg_color=("#E5E7EB", "#374151"),
            text_color=("#374151", "#E5E7EB"),
            hover_color=("#D1D5DB", "#4B5563"),
            command=lambda: (self._apply_chart_group_rule_from_ui(self._default_chart_group_rule()), dialog.destroy()),
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkButton(
            button_row,
            text="取消",
            width=90,
            height=32,
            corner_radius=16,
            font=self.font_small,
            fg_color=("#E5E7EB", "#374151"),
            text_color=("#374151", "#E5E7EB"),
            hover_color=("#D1D5DB", "#4B5563"),
            command=dialog.destroy,
        ).grid(row=0, column=1, padx=(8, 8))
        ctk.CTkButton(
            button_row,
            text="应用规则",
            width=110,
            height=32,
            corner_radius=16,
            font=self.font_main,
            fg_color=(self.ui_primary, self.ui_primary_hover),
            hover_color=self.ui_primary_hover,
            command=lambda: (self._apply_chart_group_rule_from_ui(build_rule()), dialog.destroy()),
        ).grid(row=0, column=2)

    def _apply_chart_type_defaults(self, norm_type):
        defaults_map = {
            "contour": ("按字表组别", "不分面 (单图叠加)"),
            "tone_sandhi": ("按字表组别", "按字表组别分面"),
            "distribution": ("按字表组别", "不分面 (单图叠加)"),
            "density": ("按字表组别", "按字表组别分面"),
            "formant_space": ("按字表组别", "不分面 (单图叠加)"),
            "formant_trajectory": ("按字表组别", "不分面 (单图叠加)"),
            "formant_pillai": ("按字表组别", "不分面 (单图叠加)"),
            "formant_density": ("按字表组别", "按字表组别分面"),
            "overview_heatmap": ("按词语", "按字表组别分面"),
            "formant_overview_heatmap": ("按词语", "按字表组别分面"),
            "spaghetti_qc": ("按字表组别", "按字表组别分面"),
            "quality": ("按发音人", "不分面 (单图叠加)"),
        }
        color_by_val, facet_by_val = defaults_map.get(norm_type, ("按字表组别", "不分面 (单图叠加)"))
        if hasattr(self, "var_color_by"):
            self.var_color_by.set(color_by_val)
        if hasattr(self, "combo_color_by"):
            try:
                self.combo_color_by.set(color_by_val)
            except Exception:
                pass
        elif hasattr(self, "combo_groupby"):
            try:
                self.combo_groupby.set(color_by_val)
            except Exception:
                pass

        if hasattr(self, "var_facet_by"):
            self.var_facet_by.set(facet_by_val)
        if hasattr(self, "combo_facet_by"):
            try:
                self.combo_facet_by.set(facet_by_val)
            except Exception:
                pass

        if norm_type == "contour":
            if hasattr(self, "var_contour_content"):
                self.var_contour_content.set("仅组别平均曲线")
            if hasattr(self, "combo_contour_content"):
                try:
                    self.combo_contour_content.set("仅组别平均曲线")
                except Exception:
                    pass
        elif norm_type == "formant_trajectory":
            if hasattr(self, "var_formant_traj_style"):
                self.var_formant_traj_style.set("仅平均曲线")
            if hasattr(self, "combo_formant_traj_style"):
                try:
                    self.combo_formant_traj_style.set("仅平均曲线")
                except Exception:
                    pass
        elif norm_type == "density":
            if hasattr(self, "var_density_facet"):
                self.var_density_facet.set("按声调类型分面")
            if hasattr(self, "combo_density_facet"):
                try:
                    self.combo_density_facet.set("按声调类型分面")
                except Exception:
                    pass
        elif norm_type == "formant_density":
            if hasattr(self, "var_formant_density_facet"):
                self.var_formant_density_facet.set("按字表组分面")
            if hasattr(self, "combo_formant_density_facet"):
                try:
                    self.combo_formant_density_facet.set("按字表组分面")
                except Exception:
                    pass

    def _on_type_changed(self, val):
        self.current_group_page = 0
        for widget in self.dynamic_content_frame.winfo_children():
            widget.destroy()

        norm_type = self._normalize_chart_type(val)
        self.var_chart_type.set(norm_type)

        if norm_type == "contour":
            self.dynamic_title.configure(text="【第三步：声调格局与轮廓图要素】")
            self._build_contour_settings()
        elif norm_type == "tone_sandhi":
            self.dynamic_title.configure(text="【第三步：连读变调与协同发音要素】")
            self._build_tone_sandhi_settings()
        elif norm_type == "distribution":
            self.dynamic_title.configure(text="【第三步：声调特征分布雨云图要素】")
            self._build_distribution_settings()
        elif norm_type == "density":
            self.dynamic_title.configure(text="【第三步：时序密度图要素】")
            self._build_density_settings()
        elif norm_type == "spaghetti_qc":
            self.dynamic_title.configure(text="【第三步：组内个体原始曲线叠加质检】")
            self._build_quality_settings()
            self.var_qc_view.set("spaghetti_qc")
        elif norm_type == "quality":
            self.dynamic_title.configure(text="【第三步：检出率与发音人综合体检】")
            self._build_quality_settings()
        elif norm_type == "overview_heatmap":
            self.dynamic_title.configure(text="【第三步：词条级逐行质检热图要素】")
            self._build_overview_heatmap_settings()
        elif norm_type == "tone_effect_time":
            self.dynamic_title.configure(text="【第三步：二字组调类效应时间进程】")
            self._build_tone_effect_time_settings()
        elif norm_type == "formant_space":
            self.dynamic_title.configure(text="【第三步：元音声学空间图要素】")
            self._build_formant_space_settings()
        elif norm_type == "formant_trajectory":
            self.dynamic_title.configure(text="【第三步：共振峰时序轨迹图要素】")
            self._build_formant_trajectory_settings()
        elif norm_type == "formant_pillai":
            self.dynamic_title.configure(text="【第三步：元音对立重叠度与分布要素】")
            self._build_formant_pillai_settings()
        elif norm_type == "formant_density":
            self.dynamic_title.configure(text="【第三步：共振峰时空密度图要素】")
            self._build_formant_density_settings()
        elif norm_type == "formant_overview_heatmap":
            self.dynamic_title.configure(text="【第三步：共振峰词条级逐行质检热图】")
            self._build_formant_overview_heatmap_settings()

        self._apply_chart_type_defaults(norm_type)

        self._bind_left_scroll_wheel_recursive(self.dynamic_content_frame)
        self._bind_left_scroll_wheel_recursive(self.left_scroll)
        self._update_chart_group_rule_label()
        self._update_group_filters()
        self.update_preview()

    def _on_scope_changed(self, val):
        self.current_preview_page = 0
        self.current_group_page = 0
        if "整合" in val:
            self.var_export_scope.set("integrated")
            if not self._is_formant_mode():
                self.combo_scale.set("T 值 (五度标调)")
                self.combo_scale.configure(state="disabled")
        elif "分别" in val:
            self.var_export_scope.set("separate")
            self.combo_scale.configure(state="normal")
        else:
            self.var_export_scope.set("active")
            self.combo_scale.configure(state="normal")

        if hasattr(self, 'combo_overview_metric') and self.combo_overview_metric.winfo_exists():
            is_integrated = ("整合" in val)
            metric_values = self._get_tone_overview_metric_values(is_integrated=is_integrated)
            self.combo_overview_metric.configure(values=metric_values)
            if self.combo_overview_metric.get() not in metric_values:
                self.combo_overview_metric.set(metric_values[0])
            self._on_overview_metric_changed(self.combo_overview_metric.get())

        self.update_preview()

    def _on_groupby_changed(self, val):
        self.current_group_page = 0
        if "自定义字段" in str(val):
            self._open_chart_group_rule_dialog()
            return
        self._update_chart_group_rule_label()
        self.update_preview()

    def _on_intention_changed(self, val):
        if "数据诊断" in val:
            self.var_work_mode.set("数据质检与异常排查")
            self._on_work_mode_changed("数据质检与异常排查")
            return
        if self._is_formant_mode():
            if "论文主图" in val:
                selected_count = sum(1 for v in self.group_checkbox_vars.values() if v.get())
                if selected_count > 8 and messagebox and messagebox.askyesno("论文主图模式", f"当前选中了 {selected_count} 个组，论文主图建议展示 8 组以内以保证可读性。\n是否自动精选样本量最大的 8 组？", parent=self):
                    self._apply_featured_contrast_mode()
            elif "附录图册" in val:
                for var in self.group_checkbox_vars.values():
                    var.set(True)
                self._populate_groups_list()
                self.combo_format.set("PDF 文档 (.pdf)")
                self._on_group_filter_changed()
            return

        if "论文主图" in val:
            selected_count = sum(1 for v in self.group_checkbox_vars.values() if v.get())
            if selected_count > 8 and messagebox:
                if messagebox.askyesno("论文主图模式", f"当前选中了 {selected_count} 个组，论文主图建议展示 8 组以内以保证可读性。\n是否自动精选样本量最大的 8 组？", parent=self):
                    self._apply_featured_contrast_mode()
            if self.var_chart_type.get() == "contour" and hasattr(self, "combo_contour_content"):
                self.combo_contour_content.set("仅组别平均曲线")
            self.update_preview()
        elif "附录图册" in val:
            for var in self.group_checkbox_vars.values():
                var.set(True)
            self._populate_groups_list()
            self.combo_format.set("PDF 文档 (.pdf)")
            self._on_group_filter_changed()
            self.update_preview()

    def _apply_featured_contrast_mode(self):
        if not self.group_counts:
            return
        sorted_groups = sorted(self.group_counts.keys(), key=lambda g: self.group_counts[g], reverse=True)
        top_8 = set(sorted_groups[:8])
        for g, var in self.group_checkbox_vars.items():
            var.set(g in top_8)
        self._populate_groups_list()
        self._on_group_filter_changed()

    def _apply_batch_album_mode(self):
        for var in self.group_checkbox_vars.values():
            var.set(True)
        self.combo_format.set("PDF 文档 (.pdf)")
        self._populate_groups_list()
        self._on_group_filter_changed()

    def _apply_overall_overview_mode(self):
        if self._is_formant_mode():
            self.combo_type.set("共振峰组别概览图")
            self._on_type_changed("共振峰组别概览图")
            return
        self.combo_type.set("声调组别概览图")
        self._on_type_changed("声调组别概览图")

    def _select_tree_selected_groups(self):
        if not self.project_tree or not hasattr(self.project_tree, 'tree'):
            return
        selected_iids = self.project_tree.tree.selection()
        if not selected_iids:
            return
        selected_groups = set()
        for iid in selected_iids:
            if iid.startswith("group_node_"):
                g_name = iid[len("group_node_"):]
                if g_name != "__warning__":
                    selected_groups.add(g_name)
            else:
                item = self.project_tree.items.get(iid)
                if item and 'group' in item:
                    selected_groups.add(item['group'])
                elif item and 'tone' in item:
                    selected_groups.add(item['tone'])
        if not selected_groups:
            return
        for g, var in self.group_checkbox_vars.items():
            var.set(g in selected_groups)
        self._populate_groups_list()
        self._on_group_filter_changed()

    def _toggle_groups_sorting(self):
        self.sort_by_count = not getattr(self, 'sort_by_count', False)
        if self.sort_by_count:
            self.btn_toggle_sort.configure(fg_color=(self.ui_primary, self.ui_primary_hover), text_color="white")
        else:
            self.btn_toggle_sort.configure(fg_color=("#E5E7EB", "#374151"), text_color=("#374151", "#E5E7EB"))
        self._populate_groups_list()

    def _visible_group_names(self):
        search_query = self.search_group_var.get().strip().lower() if hasattr(self, "search_group_var") else ""
        min_count = 0
        try:
            val_str = self.entry_min_count.get().strip()
            if val_str:
                min_count = int(val_str)
        except (ValueError, AttributeError):
            pass

        if getattr(self, 'sort_by_count', False):
            groups_to_draw = sorted(self.available_groups, key=lambda g: self.group_counts.get(g, 0), reverse=True)
        else:
            groups_to_draw = list(self.available_groups)

        visible_groups = []
        for group_name in groups_to_draw:
            count = self.group_counts.get(group_name, 0)
            if count < min_count:
                continue
            if search_query and search_query not in group_name.lower():
                continue
            visible_groups.append(group_name)
        return visible_groups

    def _populate_groups_list(self):
        for w in self.filter_scroll.winfo_children():
            w.destroy()

        self.visible_group_names = self._visible_group_names()
        row_frame = None
        row_col = 0
        row_index = -1

        def new_row():
            nonlocal row_frame, row_col, row_index
            row_index += 1
            row_col = 0
            row_frame = ctk.CTkFrame(
                self.filter_scroll,
                fg_color=self.ui_row_even if row_index % 2 == 0 else self.ui_row_odd,
                corner_radius=8
            )
            row_frame.pack(fill=tk.X, padx=4, pady=1)
            row_frame.grid_columnconfigure(0, weight=1, uniform="group_filter")
            row_frame.grid_columnconfigure(1, weight=1, uniform="group_filter")
            return row_frame

        for g in self.visible_group_names:
            count = self.group_counts.get(g, 0)
            is_short_name = len(str(g)) <= 7
            if row_frame is None or row_col >= 2 or not is_short_name:
                new_row()

            var = self.group_checkbox_vars[g]
            cb = self._make_checkbox(
                row_frame,
                text=f"{g} ({count}项)",
                variable=var,
                command=self._on_group_filter_changed,
            )
            if is_short_name:
                cb.grid(row=0, column=row_col, sticky="w", padx=8, pady=3)
                row_col += 1
            else:
                cb.grid(row=0, column=0, columnspan=2, sticky="w", padx=8, pady=3)
                row_col = 2

        self._bind_filter_scroll_wheel_recursive(self.filter_scroll)

    def _on_group_filter_changed(self):
        self.current_preview_page = 0
        self.current_group_page = 0
        self.trigger_preview_update()

    def _select_all_groups(self):
        visible_groups = getattr(self, "visible_group_names", None) or self._visible_group_names()
        for group_name in visible_groups:
            var = self.group_checkbox_vars.get(group_name)
            if var is not None:
                var.set(True)
        self._on_group_filter_changed()

    def _reverse_groups(self):
        visible_groups = getattr(self, "visible_group_names", None) or self._visible_group_names()
        for group_name in visible_groups:
            var = self.group_checkbox_vars.get(group_name)
            if var is not None:
                var.set(not var.get())
        self._on_group_filter_changed()

    def _select_high_frequency_groups(self):
        if not self.group_counts:
            return
        counts = list(self.group_counts.values())
        median_count = np.median(counts) if counts else 1
        for g, var in self.group_checkbox_vars.items():
            var.set(self.group_counts[g] >= median_count)
        self._on_group_filter_changed()

    def _filter_groups_list(self):
        self._populate_groups_list()

    def _on_overview_metric_changed(self, val):
        if hasattr(self, 'cb_show_deviation') and self.cb_show_deviation.winfo_exists():
            if "标准差" in val or "调类组合" in val:
                self.var_overview_show_deviation.set(False)
                self.cb_show_deviation.configure(state="disabled")
            elif "偏差" in val or "Z-score" in val:
                self.var_overview_show_deviation.set(True)
                self.cb_show_deviation.configure(state="normal")
            else:
                self.var_overview_show_deviation.set(False)
                self.cb_show_deviation.configure(state="normal")
        self.trigger_preview_update()

    def _build_overview_heatmap_settings(self):
        ctk.CTkLabel(self.dynamic_content_frame, text="热图展示维度:", font=self.font_small).pack(anchor="w", pady=(4, 2))
        is_integrated = (self.var_export_scope.get() == "integrated")
        metric_values = self._get_tone_overview_metric_values(is_integrated=is_integrated)
        self.combo_overview_metric = ctk.CTkOptionMenu(
            self.dynamic_content_frame,
            values=metric_values,
            command=self._on_overview_metric_changed,
            **self.dropdown_kwargs
        )
        self.combo_overview_metric.set(metric_values[0])
        self.combo_overview_metric.pack(fill=tk.X, pady=(2, 6))
        self._apply_custom_arrow(self.combo_overview_metric)

        self.cb_show_deviation = self._make_checkbox(
            self.dynamic_content_frame,
            text="查看与组内均值的偏差 (RdBu_r 残差热图)",
            variable=self.var_overview_show_deviation,
            command=self.trigger_preview_update,
        )
        self.cb_show_deviation.pack(anchor="w", pady=(4, 4))

        self.cb_sort_by_anomaly = self._make_checkbox(
            self.dynamic_content_frame,
            text="按异常偏离度自动排序 (异常置顶标 [!])",
            variable=self.var_sort_by_anomaly,
            command=self.trigger_preview_update,
        )
        self.cb_sort_by_anomaly.pack(anchor="w", pady=(4, 8))

    def _build_formant_overview_heatmap_settings(self):
        ctk.CTkLabel(self.dynamic_content_frame, text="热图展示维度:", font=self.font_small).pack(anchor="w", pady=(4, 2))
        is_integrated = (self.var_export_scope.get() == "integrated")
        metric_values = ["与组内均值的偏差 (Z-score / 残差热图)", "均值热图 (Mean Map)"]
        if is_integrated:
            metric_values.append("标准差热图 (SD Map)")
        self.combo_overview_metric = ctk.CTkOptionMenu(
            self.dynamic_content_frame,
            values=metric_values,
            command=self._on_overview_metric_changed,
            **self.dropdown_kwargs
        )
        self.combo_overview_metric.set(metric_values[0])
        self.combo_overview_metric.pack(fill=tk.X, pady=(2, 6))
        self._apply_custom_arrow(self.combo_overview_metric)

        ctk.CTkLabel(self.dynamic_content_frame, text="共振峰轨道模式:", font=self.font_small).pack(anchor="w", pady=(4, 2))
        self.combo_formant_overview_mode = ctk.CTkOptionMenu(
            self.dynamic_content_frame,
            values=["F1 & F2 双轨", "F2 / F1 比值"],
            command=lambda _: self.trigger_preview_update(),
            **self.dropdown_kwargs
        )
        self.combo_formant_overview_mode.set(self.var_formant_overview_mode.get())
        self.combo_formant_overview_mode.pack(fill=tk.X, pady=(2, 6))
        self._apply_custom_arrow(self.combo_formant_overview_mode)

        self.cb_show_deviation = self._make_checkbox(
            self.dynamic_content_frame,
            text="查看与元音类别均值的偏差 (Z-score)",
            variable=self.var_overview_show_deviation,
            command=self.trigger_preview_update,
        )
        self.cb_show_deviation.pack(anchor="w", pady=(4, 4))

        self.cb_sort_by_anomaly = self._make_checkbox(
            self.dynamic_content_frame,
            text="按异常偏离度自动排序 (异常置顶标 [!])",
            variable=self.var_sort_by_anomaly,
            command=self.trigger_preview_update,
        )
        self.cb_sort_by_anomaly.pack(anchor="w", pady=(4, 8))

    def _build_contour_settings(self):
        ctk.CTkLabel(self.dynamic_content_frame, text="误差区域与曲线要素:", font=self.font_small).pack(anchor="w", pady=(4, 2))
        self.combo_contour_content = ctk.CTkOptionMenu(
            self.dynamic_content_frame,
            values=[
                "平均曲线 + 置信区间阴影",
                "平均曲线 + ±1 SE (均值标准误)",
                "平均曲线 + ±1 SD (样本标准差)",
                "仅组别平均曲线",
                "平均曲线 + 个体浅色细线",
            ],
            command=lambda _: self.trigger_preview_update(),
            **self.dropdown_kwargs,
        )
        self.combo_contour_content.set(self.var_contour_content.get())
        self.combo_contour_content.pack(fill=tk.X, pady=2)
        self._apply_custom_arrow(self.combo_contour_content)

        ctk.CTkLabel(self.dynamic_content_frame, text="横轴模式:", font=self.font_small).pack(anchor="w", pady=(4, 2))
        self.combo_contour_x = ctk.CTkOptionMenu(
            self.dynamic_content_frame,
            values=["归一化采样点", "真实物理时长"],
            command=lambda _: self.trigger_preview_update(),
            **self.dropdown_kwargs,
        )
        self.combo_contour_x.set("归一化采样点")
        self.combo_contour_x.pack(fill=tk.X, pady=2)
        self._apply_custom_arrow(self.combo_contour_x)

        self.cb_chao_five_level = self._make_checkbox(
            self.dynamic_content_frame,
            text="显示右侧五度标尺与自动五度值",
            variable=self.var_show_chao_five_level,
            command=self.update_preview,
        )
        self.cb_chao_five_level.pack(anchor="w", pady=(6, 3))

        self.cb_show_spaghetti = self._make_checkbox(
            self.dynamic_content_frame,
            text="叠加个体样本细线 (Spaghetti)",
            variable=self.var_show_spaghetti,
            command=self.update_preview,
        )
        self.cb_show_spaghetti.pack(anchor="w", pady=(3, 3))

        self.cb_show_kde_bg = self._make_checkbox(
            self.dynamic_content_frame,
            text="叠加核密度热力背景 (KDE)",
            variable=self.var_show_kde_bg,
            command=self.update_preview,
        )
        self.cb_show_kde_bg.pack(anchor="w", pady=(3, 6))

        # Keep legacy combo_contour_facet synced
        self.combo_contour_facet = ctk.CTkOptionMenu(
            self.dynamic_content_frame,
            values=["单图展示 (不分面)", "按声调类型分面", "按音节位置分面"],
            command=lambda val: self._sync_facet_from_child(val),
            **self.dropdown_kwargs,
        )
        self.combo_contour_facet.set("单图展示 (不分面)")

    def _build_tone_sandhi_settings(self):
        ctk.CTkLabel(self.dynamic_content_frame, text="连读变调分析子视图:", font=self.font_small).pack(anchor="w", pady=(4, 2))
        self.combo_sandhi_subview = ctk.CTkOptionMenu(
            self.dynamic_content_frame,
            values=[
                "二字组连读变调网格图",
                "前后字调类组合特征矩阵图",
                "二字组调类效应时间进程图 (η²)",
            ],
            command=lambda _: self.trigger_preview_update(),
            **self.dropdown_kwargs,
        )
        self.combo_sandhi_subview.set("二字组连读变调网格图")
        self.combo_sandhi_subview.pack(fill=tk.X, pady=(2, 6))
        self._apply_custom_arrow(self.combo_sandhi_subview)

        ctk.CTkLabel(self.dynamic_content_frame, text="网格分面固定维度:", font=self.font_small).pack(anchor="w", pady=(4, 2))
        self.combo_sandhi_facet = ctk.CTkOptionMenu(
            self.dynamic_content_frame,
            values=["按前字调类分面", "按后字调类分面"],
            command=lambda _: self.trigger_preview_update(),
            **self.dropdown_kwargs,
        )
        self.combo_sandhi_facet.set("按前字调类分面")
        self.combo_sandhi_facet.pack(fill=tk.X, pady=(2, 8))
        self._apply_custom_arrow(self.combo_sandhi_facet)

    def _build_distribution_settings(self):
        ctk.CTkLabel(self.dynamic_content_frame, text="提取声学特征指标:", font=self.font_small).pack(anchor="w", pady=(4, 2))
        dist_metrics = [
            "全调均值",
            "起点值 (Onset)",
            "中点值 (Mid)",
            "终点值 (Offset)",
            "拐点极值",
            "调域跨度 (Range)",
            "斜率 (Slope)",
            "音节时长 (Duration ms)",
            "起-中-终三点比较",
            "测量点精细分布",
            "调域范围跨度图",
            "变异程度(CV)比较",
        ]
        self.combo_dist_type = ctk.CTkOptionMenu(
            self.dynamic_content_frame,
            values=dist_metrics,
            command=self._on_dist_type_changed,
            **self.dropdown_kwargs,
        )
        self.combo_dist_type.set(self.var_dist_type.get())
        self.combo_dist_type.pack(fill=tk.X, pady=2)
        self._apply_custom_arrow(self.combo_dist_type)
        self.combo_dist_metric = self.combo_dist_type

        self.lbl_dist_style = ctk.CTkLabel(self.dynamic_content_frame, text="分布图形态:", font=self.font_small)
        self.lbl_dist_style.pack(anchor="w", pady=(5, 2))
        self.combo_dist_style = ctk.CTkOptionMenu(
            self.dynamic_content_frame,
            values=["雨云图 (Raincloud)", "科学箱线图 (Box Plot)", "小提琴图 (Violin Plot)"],
            command=lambda _: self.trigger_preview_update(),
            **self.dropdown_kwargs,
        )
        self.combo_dist_style.set(self.var_dist_style.get())
        self.combo_dist_style.pack(fill=tk.X, pady=(2, 8))
        self._apply_custom_arrow(self.combo_dist_style)

    def _on_dist_type_changed(self, val):
        self.var_dist_type.set(val)
        if val in ["调域范围跨度图", "变异程度(CV)比较"]:
            self.combo_dist_style.configure(state="disabled")
        else:
            self.combo_dist_style.configure(state="normal")
        self.trigger_preview_update()

    def _build_density_settings(self):
        ctk.CTkLabel(self.dynamic_content_frame, text="核密度带宽 (Bandwidth):", font=self.font_small).pack(anchor="w", pady=(5, 2))
        bw_val_frame = ctk.CTkFrame(self.dynamic_content_frame, fg_color="transparent")
        bw_val_frame.pack(fill=tk.X)
        self.slider_density_bw = ctk.CTkSlider(bw_val_frame, from_=0.05, to=0.50, number_of_steps=45, command=self._on_bw_slider_changed)
        self.slider_density_bw.set(0.15)
        self.slider_density_bw.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.lbl_bw_val = ctk.CTkLabel(bw_val_frame, text="0.15", font=self.font_main, width=40)
        self.lbl_bw_val.pack(side=tk.RIGHT, padx=(5, 0))

        ctk.CTkLabel(self.dynamic_content_frame, text="截断极值处理:", font=self.font_small).pack(anchor="w", pady=(5, 2))
        self.combo_density_f0 = ctk.CTkOptionMenu(self.dynamic_content_frame, values=["分位数自动截断 (5%-95%)", "极值自动范围 (Min-Max)", "手动指定频率范围 (Hz)"], command=self._on_density_f0_changed, **self.dropdown_kwargs)
        self.combo_density_f0.set("分位数自动截断 (5%-95%)")
        self.combo_density_f0.pack(fill=tk.X, pady=2)
        self._apply_custom_arrow(self.combo_density_f0)

        self.manual_f0_frame = ctk.CTkFrame(self.dynamic_content_frame, fg_color="transparent")
        self.pct_f0_frame = ctk.CTkFrame(self.dynamic_content_frame, fg_color="transparent")

        ctk.CTkLabel(self.manual_f0_frame, text="频率 (Hz):", font=self.font_small).pack(side=tk.LEFT)
        self.entry_min_hz = ctk.CTkEntry(self.manual_f0_frame, width=55, font=self.font_small)
        self.entry_min_hz.insert(0, "75")
        self.entry_min_hz.pack(side=tk.LEFT, padx=3)
        ctk.CTkLabel(self.manual_f0_frame, text="~", font=self.font_small).pack(side=tk.LEFT)
        self.entry_max_hz = ctk.CTkEntry(self.manual_f0_frame, width=55, font=self.font_small)
        self.entry_max_hz.insert(0, "600")
        self.entry_max_hz.pack(side=tk.LEFT, padx=3)

        ctk.CTkLabel(self.pct_f0_frame, text="分位数 (%):", font=self.font_small).pack(side=tk.LEFT)
        self.entry_low_p = ctk.CTkEntry(self.pct_f0_frame, width=45, font=self.font_small)
        self.entry_low_p.insert(0, "5")
        self.entry_low_p.pack(side=tk.LEFT, padx=3)
        ctk.CTkLabel(self.pct_f0_frame, text="~", font=self.font_small).pack(side=tk.LEFT)
        self.entry_high_p = ctk.CTkEntry(self.pct_f0_frame, width=45, font=self.font_small)
        self.entry_high_p.insert(0, "95")
        self.entry_high_p.pack(side=tk.LEFT, padx=3)

        for _entry in (self.entry_min_hz, self.entry_max_hz, self.entry_low_p, self.entry_high_p):
            _entry.bind("<Return>", lambda _e: self.trigger_preview_update())
            _entry.bind("<FocusOut>", lambda _e: self.trigger_preview_update())

        self.pct_f0_frame.pack(fill=tk.X, pady=2)

        ctk.CTkLabel(self.dynamic_content_frame, text="排版分面依据:", font=self.font_small).pack(anchor="w", pady=(5, 2))
        self.combo_density_facet = ctk.CTkOptionMenu(self.dynamic_content_frame, values=["按声调类型分面", "声调类型分面 (默认)", "不分面 (混合叠加)", "按词语分面"], command=lambda val: self._sync_facet_from_child(val), **self.dropdown_kwargs)
        self.combo_density_facet.set(self.var_density_facet.get() or "按声调类型分面")
        self.combo_density_facet.pack(fill=tk.X, pady=(2, 8))
        self._apply_custom_arrow(self.combo_density_facet)

    def _on_bw_slider_changed(self, val):
        self.var_density_bw.set(float(val))
        self.lbl_bw_val.configure(text=f"{float(val):.2f}")
        self.trigger_preview_update()

    def _on_density_f0_changed(self, val):
        self.pct_f0_frame.pack_forget()
        self.manual_f0_frame.pack_forget()

        if "分位数" in val:
            self.var_density_f0_mode.set("percentile")
            self.pct_f0_frame.pack(fill=tk.X, pady=2)
        elif "手动" in val:
            self.var_density_f0_mode.set("manual")
            self.manual_f0_frame.pack(fill=tk.X, pady=2)
        else:
            self.var_density_f0_mode.set("minmax")

        self.trigger_preview_update()

    def _build_tone_effect_time_settings(self):
        text = (
            "此图使用当前勾选的二字组声调组合，估算前字调类、后字调类与交互项/残差在时间轴上的占比。\n"
            "请保留至少 2 个前字调类、2 个后字调类和 4 个调类组合。"
        )
        ctk.CTkLabel(
            self.dynamic_content_frame,
            text=text,
            font=self.font_small,
            justify="left",
            wraplength=340,
            text_color=("#374151", "#D1D5DB")
        ).pack(anchor="w", pady=(5, 10))

    def _build_quality_settings(self):
        ctk.CTkLabel(self.dynamic_content_frame, text="数据质检视图类型:", font=self.font_small).pack(anchor="w", pady=(5, 2))
        self.combo_qc_view = ctk.CTkOptionMenu(
            self.dynamic_content_frame,
            values=[
                "双联综合体检面板 (检出率+调域)",
                "组内个体原始曲线叠加图",
                "个体 Raw F0 曲线叠加 (异常高亮)",
                "有效点比例 (Active Ratio) 分布箱线图",
                "发音人基频均值与调域跨度散点图",
            ],
            command=self._on_qc_view_changed,
            **self.dropdown_kwargs,
        )
        self.combo_qc_view.set("双联综合体检面板 (检出率+调域)")
        self.combo_qc_view.pack(fill=tk.X, pady=(2, 8))
        self._apply_custom_arrow(self.combo_qc_view)
        self.var_qc_view.set("dual_check")

    def _on_qc_view_changed(self, val):
        if "双联" in val or "综合" in val:
            self.var_qc_view.set("dual_check")
        elif "组内个体" in val:
            self.var_qc_view.set("spaghetti_qc")
        elif "个体" in val:
            self.var_qc_view.set("raw_overlay")
        elif "比例" in val:
            self.var_qc_view.set("active_ratio")
        else:
            self.var_qc_view.set("speaker_means")
        self.trigger_preview_update()

    # --- CONTROLLER: RE-RENDER LIVE PREVIEW ---
    def _on_live_refresh_toggle(self):
        if self.var_live_refresh.get():
            self.update_preview()

    def trigger_preview_update(self):
        if not getattr(self, 'var_live_refresh', None) or not self.var_live_refresh.get():
            return

        if hasattr(self, '_debounce_timer_id') and self._debounce_timer_id is not None:
            try:
                self.after_cancel(self._debounce_timer_id)
            except Exception:
                pass
            self._debounce_timer_id = None

        self._debounce_timer_id = self.after(260, self._debounced_update_preview)

    def _debounced_update_preview(self):
        self._debounce_timer_id = None
        self.update_preview()

    def _snapshot_render_params(self):
        keys = [
            'work_mode', 'preset_theme', 'chart_type', 'export_scope',
            'color_by', 'groupby', 'facet_by', 'chart_group_rule',
            'scale', 'format', 'layout_size', 'dpi', 'show_title',
            'contour_x', 'contour_content', 'contour_facet',
            'show_chao_five_level_axis', 'show_spaghetti', 'show_kde_bg',
            'sandhi_subview', 'sandhi_facet_by',
            'dist_type', 'dist_metric', 'dist_style',
            'density_bw', 'density_f0_mode', 'density_facet',
            'density_normalization', 'density_p_low', 'density_p_high',
            'density_m_min', 'density_m_max', 'density_max_points',
            'formant_density_overlay', 'formant_density_bw', 'formant_density_facet',
            'formant_density_show_raw', 'formant_density_show_contours',
            'formant_ellipse', 'formant_label_mode', 'formant_show_raw', 'formant_time_gradient',
            'show_convex_hull', 'show_diphthong_arrows',
            'formant_normalization', 'formant_axis_lock', 'formant_overview_mode',
            'formant_traj_style', 'show_f3', 'formant_syllable_filter',
            'pillai_subview',
            'qc_view', 'overview_metric', 'overview_show_deviation', 'sort_by_anomaly',
            'legend_loc', 'legend_outside', 'intention',
            'image_ratio_mode', 'image_ratio_custom', 'image_pixel_mode', 'image_pixel_custom',
            'high_precision',
        ]
        snapshot = {}
        for key in keys:
            val = self.get_param(key)
            if val is not None:
                snapshot[key] = val
        return snapshot

    def _cancel_preview_render(self):
        if self._preview_cancel_event is not None:
            self._preview_cancel_event.set()

    def _bind_canvas_hover_metadata(self, canvas, fig):
        hover_meta = getattr(fig, "_qc_hover_metadata", None)
        if not hover_meta or not hasattr(self, "lbl_qc_hover_status"):
            return

        def _on_motion(event):
            item, col_idx = self._resolve_qc_hover_item(fig, event)
            if item is None:
                return
            vals = item.get("values") or []
            zs = item.get("z_scores") or []
            c_idx = min(max(0, col_idx), max(0, len(vals) - 1))
            v_str = f"{vals[c_idx]:.2f}" if (0 <= c_idx < len(vals) and vals[c_idx] is not None and np.isfinite(vals[c_idx])) else "NaN"
            z_str = f"{zs[c_idx]:+.2f}σ" if (0 <= c_idx < len(zs) and zs[c_idx] is not None and np.isfinite(zs[c_idx])) else "--"
            is_anom = bool(item.get("is_anomaly") or item.get("is_outlier"))
            flag = "[!异常] " if is_anom else ""
            rmsd_val = float(item.get("rmsd", item.get("rms_z", 0.0)) or 0.0)
            loc_hint = " (双击定位到工程树词条)" if (item.get("item_id") or item.get("label")) else ""
            msg = (
                f"🔍 {flag}{item.get('speaker_name', '')} | 词条: {item.get('label', '')} ({item.get('group', '')}) "
                f"| 采样点#{c_idx+1}: {v_str} (偏差 {z_str}, RMSD={rmsd_val:.2f}){loc_hint}"
            )
            try:
                self.lbl_qc_hover_status.configure(text=msg, text_color=("#DC2626" if is_anom else "#1E40AF"))
                if hasattr(self.lbl_qc_hover_status, "grid"):
                    self.lbl_qc_hover_status.grid(row=4, column=0, sticky="ew", padx=4, pady=(0, 3))
            except Exception:
                pass

        def _on_button_press(event):
            if not getattr(event, "dblclick", False):
                return
            item, _col_idx = self._resolve_qc_hover_item(fig, event)
            if item is None:
                return
            ok = self.locate_qc_item_in_project_tree(
                item.get("item_id"),
                speaker_name=item.get("speaker_name"),
                label=item.get("label"),
            )
            if ok:
                try:
                    self.lbl_qc_hover_status.configure(
                        text=f"✅ 已在工程树中定位词条: {item.get('speaker_name', '')} | {item.get('label', '')} (#{item.get('item_id', '')})",
                        text_color="#059669",
                    )
                    if hasattr(self.lbl_qc_hover_status, "grid"):
                        self.lbl_qc_hover_status.grid(row=4, column=0, sticky="ew", padx=4, pady=(0, 3))
                except Exception:
                    pass

        try:
            canvas.mpl_connect("motion_notify_event", _on_motion)
            canvas.mpl_connect("button_press_event", _on_button_press)
        except Exception:
            pass

    def _poll_preview_render(self, generation):
        if self._preview_queue is None:
            return

        done_payload = None
        while True:
            try:
                msg_type, payload = self._preview_queue.get_nowait()
            except queue.Empty:
                break
            if msg_type == "progress":
                message = payload.get("message")
                if message and hasattr(self, "preview_lbl"):
                    self.preview_lbl.configure(text=message, text_color="#6B7280")
                progress = payload.get("progress")
                if progress is not None and hasattr(self, "preview_progress"):
                    self.preview_progress.set(max(0.0, min(1.0, float(progress))))
            elif msg_type == "done":
                done_payload = payload

        if done_payload is None:
            self._preview_poll_job = self.after(80, lambda: self._poll_preview_render(generation))
            return

        if generation != self._preview_generation:
            fig = done_payload.get("fig")
            if fig is not None:
                plt.close(fig)
            return

        self._preview_poll_job = None
        self._preview_worker = None
        self._preview_queue = None
        self._preview_cancel_event = None

        status = done_payload.get("status")
        if status == "ok":
            fig = done_payload["fig"]
            for widget in self.preview_container.winfo_children():
                if widget != getattr(self, 'btn_toggle_minimize', None):
                    widget.destroy()
            canvas = FigureCanvasTkAgg(fig, master=self.preview_container)
            canvas.draw()
            canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
            self._bind_chart_context_menu(canvas.get_tk_widget())
            self._bind_canvas_hover_metadata(canvas, fig)
            if getattr(self, 'btn_toggle_minimize', None):
                self.btn_toggle_minimize.lift()
            if getattr(self, "active_figure", None) is not None and self.active_figure is not fig:
                try:
                    plt.close(self.active_figure)
                except Exception:
                    pass
            self.active_figure = fig
        elif status == "cancelled":
            if hasattr(self, "preview_lbl"):
                self.preview_lbl.configure(text="已取消本次预览渲染", text_color="#6B7280")
        else:
            self.group_pagination_frame.grid_forget()
            if hasattr(self, "preview_lbl"):
                self.preview_lbl.configure(text=f"图表渲染发生错误: {done_payload.get('message', '')[:35]}", text_color="#EF4444")

    def update_preview(self):
        if getattr(self, 'sm', None):
            active_spk = self.sm.get_active_speaker()
            if active_spk:
                self.active_speaker = active_spk

        if hasattr(self, '_debounce_timer_id') and self._debounce_timer_id is not None:
            try:
                self.after_cancel(self._debounce_timer_id)
            except Exception:
                pass
            self._debounce_timer_id = None

        self._preview_generation += 1
        generation = self._preview_generation
        self._cancel_preview_render()
        if self._preview_poll_job is not None:
            try:
                self.after_cancel(self._preview_poll_job)
            except Exception:
                pass
            self._preview_poll_job = None

        for widget in self.preview_container.winfo_children():
            if widget != getattr(self, 'btn_toggle_minimize', None):
                widget.destroy()

        if getattr(self, 'btn_toggle_minimize', None):
            self.btn_toggle_minimize.lift()

        preview_status = ctk.CTkFrame(self.preview_container, fg_color="transparent")
        preview_status.grid(row=0, column=0)
        self.preview_lbl = ctk.CTkLabel(preview_status, text="正在实时渲染图表，请稍候...", font=self.font_title, text_color="#6B7280")
        self.preview_lbl.pack(pady=(0, 8))
        self.preview_progress = ctk.CTkProgressBar(
            preview_status,
            width=220,
            height=8,
            corner_radius=4,
            fg_color=("#E5E7EB", "#374151"),
            progress_color=self.ui_primary,
        )
        self.preview_progress.set(0.05)
        self.preview_progress.pack(pady=(0, 10))
        self.btn_cancel_preview = ctk.CTkButton(
            preview_status,
            text="取消预览",
            width=96,
            height=30,
            corner_radius=999,
            font=self.font_small,
            fg_color=("#F3F4F6", "#374151"),
            text_color=("#374151", "#E5E7EB"),
            hover_color=("#E5E7EB", "#4B5563"),
            border_width=1,
            border_color=("#D1D5DB", "#4B5563"),
            command=self._cancel_preview_render,
        )
        self.btn_cancel_preview.pack()

        self._bind_chart_context_menu(preview_status)

        scope = self.var_export_scope.get()
        if scope == "separate" and len(self.all_speakers) > 1:
            pady_val = (0, 4) if getattr(self, 'is_minimized', False) else (0, 6)
            self.pagination_frame.grid(row=2, column=0, pady=pady_val)
            idx = getattr(self, 'current_preview_page', 0)
            if idx < 0 or idx >= len(self.all_speakers):
                idx = 0
                self.current_preview_page = 0
            spk_name = self.all_speakers[idx].name
            if getattr(self, 'is_minimized', False):
                self.lbl_page_info.configure(text=f"{idx+1}/{len(self.all_speakers)}", font=self.font_small)
                self.btn_prev_page.configure(text="◀", width=40, height=26, font=self.font_small)
                self.btn_next_page.configure(text="▶", width=40, height=26, font=self.font_small)
            else:
                self.lbl_page_info.configure(text=f"发音人: {spk_name} ({idx+1}/{len(self.all_speakers)})", font=self.font_title)
                self.btn_prev_page.configure(text="◀ 上一个发音人", width=120, height=28, font=self.font_main)
                self.btn_next_page.configure(text="下一个发音人 ▶", width=120, height=28, font=self.font_main)
        else:
            self.pagination_frame.grid_forget()

        self.update_idletasks()

        try:
            data = self._get_current_data_entries()
            if not data:
                analysis_mode = self.project_tree.app_state_params.get('analysis_mode', 'f0')
                msg = "❌ 没有检索到有效共振峰数据，请先完成共振峰分析。" if analysis_mode == 'formant' else "❌ 没有检索到有效基频曲线，请导入有发音数据的项目！"
                self.preview_lbl.configure(text=msg, text_color="#EF4444")
                self.group_pagination_frame.grid_forget()
                self.truncation_banner_frame.pack_forget()
                return

            groupby = self.combo_groupby.get()
            chart_type = self.var_chart_type.get()
            pagination_state = self._get_group_pagination_state(data, chart_type, groupby)
            total_groups = pagination_state['total_groups']
            total_pages = pagination_state['total_pages']
            is_paginated_heatmap = pagination_state['is_paginated_heatmap']
            show_group_pagination = (
                (
                    total_groups > 8
                    and not self._is_overview_heatmap_chart(chart_type)
                    and chart_type not in ("tone_effect_time", "tone_sandhi", "formant_pillai")
                )
                or (is_paginated_heatmap and total_pages > 1)
            )
            if show_group_pagination:
                pady_val = (0, 4) if getattr(self, 'is_minimized', False) else (0, 6)
                self.group_pagination_frame.grid(row=3, column=0, pady=pady_val)
                if self.current_group_page < 0:
                    self.current_group_page = 0
                elif self.current_group_page >= total_pages:
                    self.current_group_page = max(0, total_pages - 1)

                if getattr(self, 'is_minimized', False):
                    self.lbl_group_page_info.configure(text=f"{self.current_group_page+1}/{total_pages}", font=self.font_small)
                    self.btn_prev_group_page.configure(text="◀", width=40, height=26, font=self.font_small)
                    self.btn_next_group_page.configure(text="▶", width=40, height=26, font=self.font_small)
                else:
                    if is_paginated_heatmap:
                        current_chunk = pagination_state['pages'][self.current_group_page] if self.current_group_page < len(pagination_state['pages']) else []
                        if current_chunk and isinstance(current_chunk[0], dict):
                            chunk_groups = self._ordered_unique([r['group'] for r in current_chunk])
                            first_g = chunk_groups[0] if chunk_groups else ""
                            last_g = chunk_groups[-1] if chunk_groups else ""
                            g_range = first_g if first_g == last_g else f"{first_g} ~ {last_g}"
                            self.lbl_group_page_info.configure(
                                text=f"热图页码: {self.current_group_page+1}/{total_pages} (当前显示: {g_range}，共 {len(current_chunk)} 个词条)",
                                font=ctk.CTkFont(family="Microsoft YaHei", size=12, weight="bold")
                            )
                        else:
                            first_g = str(current_chunk[0]) if current_chunk else ""
                            last_g = str(current_chunk[-1]) if current_chunk else ""
                            g_range = first_g if first_g == last_g else f"{first_g} ~ {last_g}"
                            self.lbl_group_page_info.configure(
                                text=f"热图页码: {self.current_group_page+1}/{total_pages} (当前显示: {g_range}，共 {len(current_chunk)} 个组别)",
                                font=ctk.CTkFont(family="Microsoft YaHei", size=12, weight="bold")
                            )
                    else:
                        self.lbl_group_page_info.configure(
                            text=f"组别页码: {self.current_group_page+1}/{total_pages} (当前显示第 {self.current_group_page*8+1}~{min(total_groups, (self.current_group_page+1)*8)} 组，共 {total_groups} 组)",
                            font=ctk.CTkFont(family="Microsoft YaHei", size=12, weight="bold")
                        )
                    self.btn_prev_group_page.configure(text="◀ 上一页", width=88, height=28, font=self.font_main)
                    self.btn_next_group_page.configure(text="下一页 ▶", width=88, height=28, font=self.font_main)

                if not getattr(self, 'is_minimized', False):
                    if is_paginated_heatmap:
                        current_chunk = pagination_state['pages'][self.current_group_page] if self.current_group_page < len(pagination_state['pages']) else []
                        if current_chunk and isinstance(current_chunk[0], dict):
                            chunk_groups = self._ordered_unique([r['group'] for r in current_chunk])
                            first_g = chunk_groups[0] if chunk_groups else ""
                            last_g = chunk_groups[-1] if chunk_groups else ""
                            g_range = first_g if first_g == last_g else f"{first_g} ~ {last_g}"
                            banner_msg = f" ℹ 热图自动分页: 第 {self.current_group_page+1}/{total_pages} 页 ({g_range}, {len(current_chunk)}词)。导出时完整输出。"
                        else:
                            first_g = str(current_chunk[0]) if current_chunk else ""
                            last_g = str(current_chunk[-1]) if current_chunk else ""
                            g_range = first_g if first_g == last_g else f"{first_g} ~ {last_g}"
                            banner_msg = f" ℹ 热图自动分页: 第 {self.current_group_page+1}/{total_pages} 页 ({g_range}, {len(current_chunk)}组)。导出时完整输出。"
                    else:
                        s_g = self.current_group_page * 8 + 1
                        e_g = min(total_groups, (self.current_group_page + 1) * 8)
                        banner_msg = f" ℹ 共 {total_groups} 组分 {total_pages} 页: 预览第 {self.current_group_page+1} 页 ({s_g}~{e_g}组)。导出时完整输出。"
                    self.lbl_truncation_banner.configure(text=banner_msg)
                    self.truncation_banner_frame.pack(side=tk.LEFT, padx=(12, 0), fill=tk.X, expand=True)
                else:
                    self.truncation_banner_frame.pack_forget()
            else:
                self.group_pagination_frame.grid_forget()
                self.truncation_banner_frame.pack_forget()

            params_snapshot = self._snapshot_render_params()
            rule_summary = self._format_chart_group_rule_summary(params_snapshot.get("chart_group_rule"))
            rule_label = rule_summary.lstrip("按").strip()
            if rule_label in ("", "默认组"):
                rule_label = "默认组别"
            cancel_event = threading.Event()
            progress_queue = queue.Queue()
            self._preview_cancel_event = cancel_event
            self._preview_queue = progress_queue

            def worker():
                try:
                    self._set_export_runtime(
                        progress_callback=lambda p=None, msg=None: progress_queue.put(("progress", {"progress": p, "message": msg})),
                        cancel_event=cancel_event,
                        params=params_snapshot,
                    )
                    with _MATPLOTLIB_LOCK:
                        self._check_export_cancelled()
                        progress_queue.put(("progress", {"progress": 0.10, "message": f"正在按{rule_label}准备图表数据..."}))
                        fig = self.generate_plot(data, is_preview=True)
                        self._check_export_cancelled()
                    progress_queue.put(("progress", {"progress": 0.95, "message": "正在生成预览画布..."}))
                    progress_queue.put(("done", {"status": "ok", "fig": fig}))
                except ExportCancelled:
                    progress_queue.put(("done", {"status": "cancelled"}))
                except Exception as e:
                    progress_queue.put(("done", {"status": "error", "message": str(e)}))
                    import logging
                    logging.getLogger(__name__).error(f"Render chart error: {e}", exc_info=True)
                finally:
                    self._clear_export_runtime()

            self._preview_worker = threading.Thread(target=worker, daemon=True)
            self._preview_worker.start()
            self._poll_preview_render(generation)
        except Exception as e:
            self.group_pagination_frame.grid_forget()
            self.preview_lbl.configure(text=f"图表渲染发生错误: {str(e)[:35]}", text_color="#EF4444")
            import logging
            logging.getLogger(__name__).error(f"Render chart error: {e}", exc_info=True)

    # --- CONFIRM & EXPORT CONTROLLER ---
    def copy_figure_to_clipboard(self):
        fig = getattr(self, "active_figure", None)
        if fig is None:
            if messagebox:
                messagebox.showinfo("提示", "当前暂无可复制的预览图表。", parent=self)
            return False
        import io
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=300, bbox_inches="tight")
        png_bytes = buf.getvalue()
        if hasattr(self, "lbl_qc_hover_status"):
            self.lbl_qc_hover_status.configure(text=f"✅ 已生成 300 DPI 高清图像至内存剪贴板缓冲区 ({len(png_bytes) // 1024} KB)")
        return True

    def export_chart_table_data(self):
        data = self._get_current_data_entries()
        if not data:
            if messagebox:
                messagebox.showwarning("提示", "当前没有可导出的有效声学数据。", parent=self)
            return
        if filedialog is None:
            return
        out_csv = filedialog.asksaveasfilename(
            title="导出当前图表统计数据表",
            defaultextension=".csv",
            initialfile="acoustic_chart_data.csv",
            filetypes=[("CSV 表格", "*.csv")],
        )
        if not out_csv:
            return
        import csv
        with open(out_csv, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["发音人", "组别", "词条", "时长(s)", "有效检出率"])
            for e in data:
                writer.writerow([
                    e.get("speaker_name", ""),
                    e.get("group", ""),
                    e.get("label", ""),
                    f"{float(e.get('total_dur', 0.0)):.4f}",
                    f"{float(e.get('active_ratio', 1.0)):.4f}",
                ])
        if messagebox:
            messagebox.showinfo("导出成功", f"图表关联数据已导出至:\n{out_csv}", parent=self)

    def _on_close_request(self):
        if self._export_worker is not None and self._export_worker.is_alive():
            if messagebox.askyesno("导出进行中", "导出尚未完成，是否取消导出并关闭窗口？", parent=self):
                self._cancel_export_job()
            return
        self._cancel_preview_render()
        if self._preview_poll_job is not None:
            try:
                self.after_cancel(self._preview_poll_job)
            except Exception:
                pass
            self._preview_poll_job = None
        self.destroy()

    def destroy(self):
        self._cancel_preview_render()
        if hasattr(self, '_debounce_timer_id') and self._debounce_timer_id is not None:
            try:
                self.after_cancel(self._debounce_timer_id)
            except Exception:
                pass
            self._debounce_timer_id = None
        if self._preview_poll_job is not None:
            try:
                self.after_cancel(self._preview_poll_job)
            except Exception:
                pass
            self._preview_poll_job = None
        if self.app and getattr(self.app, 'active_chart_dialog', None) == self:
            try:
                self.app.active_chart_dialog = None
            except Exception:
                pass
        ctk.CTkToplevel.destroy(self)

    def manual_refresh_preview(self):
        if getattr(self, 'sm', None):
            active_spk = self.sm.get_active_speaker()
            if active_spk:
                self.active_speaker = active_spk
        self._force_live_extract = True
        try:
            self._update_group_filters()
            self.update_preview()
        finally:
            self._force_live_extract = False

    def _update_group_filters(self):
        base_entries = self._extract_active_data(
            self.all_speakers if self.all_speakers else [self.active_speaker],
            filter_groups=False,
            apply_chart_grouping=False,
        )
        self._all_base_group_filter_entries = base_entries
        all_entries = self._extract_active_data(self.all_speakers if self.all_speakers else [self.active_speaker], filter_groups=False)
        self._all_group_filter_entries = all_entries
        new_group_counts = {}
        for entry in all_entries:
            g = entry['group']
            new_group_counts[g] = new_group_counts.get(g, 0) + 1

        new_available_groups = sorted(list(new_group_counts.keys()))
        new_group_checkbox_vars = {}
        for g in new_available_groups:
            if hasattr(self, 'group_checkbox_vars') and g in self.group_checkbox_vars:
                new_group_checkbox_vars[g] = self.group_checkbox_vars[g]
            else:
                new_group_checkbox_vars[g] = ctk.BooleanVar(value=True)

        self.group_counts = new_group_counts
        self.available_groups = new_available_groups
        self.group_checkbox_vars = new_group_checkbox_vars

        self._populate_groups_list()
        if hasattr(self, "combo_type") and not self._is_formant_mode():
            type_values = self._get_tone_chart_type_values(base_entries, work_mode=self.var_work_mode.get() if hasattr(self, "var_work_mode") else None)
            self.combo_type.configure(values=type_values)
        if hasattr(self, "combo_overview_metric") and not self._is_formant_mode():
            try:
                exists = self.combo_overview_metric.winfo_exists()
            except Exception:
                exists = True
            if exists:
                is_integrated = self.var_export_scope.get() == "integrated"
                metric_values = self._get_tone_overview_metric_values(is_integrated=is_integrated, data_entries=all_entries)
                self.combo_overview_metric.configure(values=metric_values)
                if self.combo_overview_metric.get() not in metric_values:
                    self.combo_overview_metric.set(metric_values[0])
                self._on_overview_metric_changed(self.combo_overview_metric.get())

    def _on_format_changed(self, val):
        self._update_pixel_options_state()
        self.update_preview()

    def _on_ratio_mode_changed(self, val):
        if hasattr(self, "var_image_ratio_mode"):
            self.var_image_ratio_mode.set(val)
        if val == "自定义":
            self.slider_ratio_custom.configure(state="normal")
            if hasattr(self, "entry_ratio_custom"):
                self.entry_ratio_custom.configure(state="normal")
            if hasattr(self, "custom_ratio_inline_frame"):
                try:
                    self.custom_ratio_inline_frame.pack(side=tk.LEFT, after=self.combo_ratio_mode, padx=(0, 6))
                except Exception:
                    self.custom_ratio_inline_frame.pack(side=tk.LEFT, padx=(0, 6))
        else:
            if hasattr(self, "custom_ratio_inline_frame"):
                self.custom_ratio_inline_frame.pack_forget()
            self.slider_ratio_custom.configure(state="disabled")
        self.on_preview_wrapper_configure()
        self.update_preview()

    def _on_pixel_mode_changed(self, val):
        if val == "自定义":
            self.entry_pixel_custom.configure(state="normal")
        else:
            self.entry_pixel_custom.configure(state="disabled")
        self.update_preview()

    def _on_ratio_slider_change(self, val):
        r = self._parse_custom_ratio_value(val, default=1.5)
        self.var_image_ratio_custom.set(r)
        if hasattr(self, "lbl_ratio_val"):
            self.lbl_ratio_val.configure(text=f"{r:.2f}")
        if hasattr(self, "entry_ratio_custom"):
            try:
                self.entry_ratio_custom.delete(0, tk.END)
                self.entry_ratio_custom.insert(0, f"{r:.2f}")
            except Exception:
                pass
        self.on_preview_wrapper_configure()
        self.trigger_preview_update()

    def _on_custom_ratio_entry_commit(self, event=None):
        if not hasattr(self, "entry_ratio_custom"):
            return
        raw = self.entry_ratio_custom.get()
        r = self._parse_custom_ratio_value(raw, default=float(self.var_image_ratio_custom.get() or 1.5))
        self.var_image_ratio_custom.set(r)
        if hasattr(self, "lbl_ratio_val"):
            self.lbl_ratio_val.configure(text=f"{r:.2f}")
        try:
            self.entry_ratio_custom.delete(0, tk.END)
            self.entry_ratio_custom.insert(0, f"{r:.2f}")
        except Exception:
            pass
        self.on_preview_wrapper_configure()
        self.update_preview()

    def on_preview_wrapper_configure(self, event=None):
        if not hasattr(self, 'preview_wrapper') or not hasattr(self, 'preview_container'):
            return
        w_avail = self.preview_wrapper.winfo_width()
        h_avail = self.preview_wrapper.winfo_height()
        if w_avail < 50 or h_avail < 50:
            return

        ratio_mode = self.get_param('image_ratio_mode', '默认')
        custom_ratio = self._parse_custom_ratio_value(self.get_param('image_ratio_custom', 1.5), default=1.5)
        try:
            custom_ratio_round = round(float(custom_ratio), 3)
        except (TypeError, ValueError):
            custom_ratio_round = 1.5
        last_cfg = getattr(self, '_last_preview_wrapper_cfg', None)
        if last_cfg is not None:
            last_w, last_h, last_mode, last_r = last_cfg
            if abs(w_avail - last_w) < 6 and abs(h_avail - last_h) < 6 and ratio_mode == last_mode and custom_ratio_round == last_r:
                return
        self._last_preview_wrapper_cfg = (w_avail, h_avail, ratio_mode, custom_ratio_round)

        if ratio_mode == "默认":
            self.preview_container.place_forget()
            self.preview_container.configure(width=0, height=0)
            self.preview_container.place(
                relx=0.0,
                rely=0.0,
                x=0,
                y=0,
                relwidth=1.0,
                relheight=1.0,
                anchor="nw",
            )
            self.trigger_preview_update()
            return

        ratio_map = {
            "4:3": 4.0 / 3.0,
            "16:9": 16.0 / 9.0,
            "3:2": 3.0 / 2.0,
            "1:1": 1.0,
            "16:10": 16.0 / 10.0,
            "2:1": 2.0
        }
        R = ratio_map.get(ratio_mode, custom_ratio)

        if w_avail / h_avail >= R:
            h_target = h_avail
            w_target = h_avail * R
        else:
            w_target = w_avail
            h_target = w_avail / R

        self.preview_container.place_forget()
        self.preview_container.configure(width=int(w_target), height=int(h_target))
        self.preview_container.place(
            relx=0.5, rely=0.5, anchor="center"
        )
        self.trigger_preview_update()

    def _update_pixel_options_state(self):
        fmt = self.combo_format.get()
        is_vector = "svg" in fmt.lower() or "pdf" in fmt.lower()
        if is_vector:
            self.combo_pixel_mode.configure(state="disabled")
            self.entry_pixel_custom.configure(state="disabled")
            if hasattr(self, "combo_dpi"):
                self.combo_dpi.configure(state="disabled")
        else:
            self.combo_pixel_mode.configure(state="normal")
            if hasattr(self, "combo_dpi"):
                self.combo_dpi.configure(state="normal")
            if self.combo_pixel_mode.get() == "自定义":
                self.entry_pixel_custom.configure(state="normal")
            else:
                self.entry_pixel_custom.configure(state="disabled")

    def _destroy_export_progress_window(self):
        if self._export_poll_job is not None:
            try:
                self.after_cancel(self._export_poll_job)
            except Exception:
                pass
            self._export_poll_job = None
        if self._export_progress_window is not None:
            try:
                self._export_progress_window.destroy()
            except Exception:
                pass
            self._export_progress_window = None

    def _cancel_export_job(self):
        if self._export_cancel_event is not None:
            self._export_cancel_event.set()

    def _poll_export_updates(self):
        if self._export_progress_queue is None:
            return

        done_payload = None
        while True:
            try:
                msg_type, payload = self._export_progress_queue.get_nowait()
            except queue.Empty:
                break

            if msg_type == "progress" and self._export_progress_window is not None:
                bar = getattr(self._export_progress_window, "_pbar", None)
                label = getattr(self._export_progress_window, "_status_lbl", None)
                if bar is not None and payload.get("progress") is not None:
                    bar.set(payload["progress"])
                if label is not None and payload.get("message"):
                    label.configure(text=payload["message"])
            elif msg_type == "done":
                done_payload = payload

        if done_payload is not None:
            self._destroy_export_progress_window()
            self.btn_export.configure(state="normal")
            self._export_worker = None
            self._export_progress_queue = None
            self._export_cancel_event = None
            status = done_payload.get("status")
            if status == "ok":
                messagebox.showinfo("成功", done_payload.get("message", "导出完成。"), parent=self)
            elif status == "cancelled":
                messagebox.showinfo("已取消", "导出已取消。", parent=self)
            else:
                messagebox.showerror("错误", done_payload.get("message", "导出失败。"), parent=self)
            return

        self._export_poll_job = self.after(100, self._poll_export_updates)

    def _start_async_export(self, jobs, success_message, params_snapshot=None):
        if not jobs:
            return

        self.btn_export.configure(state="disabled")
        self._export_cancel_event = threading.Event()
        self._export_progress_queue = queue.Queue()

        prog = ctk.CTkToplevel(self)
        prog.title("PhonTracer语音学标注与声学分析系统 V1.0 - 导出进行中")
        prog.geometry("380x150")
        prog.resizable(False, False)
        prog.transient(self)
        prog.attributes('-topmost', True)
        prog.protocol("WM_DELETE_WINDOW", self._cancel_export_job)

        lbl = ctk.CTkLabel(prog, text="正在准备导出...", font=self.font_main)
        lbl.pack(pady=(20, 8))
        pbar = ctk.CTkProgressBar(
            prog,
            width=320,
            height=8,
            corner_radius=4,
            fg_color=("#E5E7EB", "#374151"),
            progress_color=self.ui_primary,
        )
        pbar.pack(pady=(0, 10))
        pbar.set(0)
        self.btn_cancel_export = ctk.CTkButton(
            prog,
            text="取消导出",
            width=100,
            height=30,
            corner_radius=999,
            font=self.font_small,
            fg_color=("#F3F4F6", "#374151"),
            text_color=("#374151", "#E5E7EB"),
            hover_color=("#E5E7EB", "#4B5563"),
            border_width=1,
            border_color=("#D1D5DB", "#4B5563"),
            command=self._cancel_export_job,
        )
        self.btn_cancel_export.pack()

        prog._status_lbl = lbl
        prog._pbar = pbar
        self._export_progress_window = prog

        total_jobs = len(jobs)

        def worker():
            try:
                for idx, job in enumerate(jobs):
                    if self._export_cancel_event.is_set():
                        raise ExportCancelled("导出已取消")
                    speaker_name = job.get("speaker_name", "当前发音人")
                    out_path = job["out_path"]
                    ext = job["ext"]
                    data = job["data"]

                    def job_progress(local_progress, local_message=None, _idx=idx, _speaker=speaker_name):
                        lp = 0.0 if local_progress is None else max(0.0, min(1.0, float(local_progress)))
                        global_progress = (_idx + lp) / total_jobs
                        message = local_message or f"正在导出 {_speaker} ({_idx + 1}/{total_jobs})..."
                        self._export_progress_queue.put(("progress", {"progress": global_progress, "message": message}))

                    self._export_progress_queue.put(("progress", {"progress": idx / total_jobs, "message": f"正在导出 {speaker_name} ({idx + 1}/{total_jobs})..."}))
                    self._set_export_runtime(progress_callback=job_progress, cancel_event=self._export_cancel_event, params=params_snapshot)
                    self._export_dataset(data, out_path, ext)
                    self._clear_export_runtime()
                    self._export_progress_queue.put(("progress", {"progress": (idx + 1) / total_jobs, "message": f"{speaker_name} 导出完成 ({idx + 1}/{total_jobs})"}))

                self._export_progress_queue.put(("done", {"status": "ok", "message": success_message}))
            except ExportCancelled:
                self._export_progress_queue.put(("done", {"status": "cancelled"}))
            except Exception as e:
                self._export_progress_queue.put(("done", {"status": "error", "message": f"图表导出失败: {e}"}))
            finally:
                self._clear_export_runtime()

        self._export_worker = threading.Thread(target=worker, daemon=True)
        self._export_worker.start()
        self._poll_export_updates()

    def on_confirm(self):
        if self._export_worker is not None and self._export_worker.is_alive():
            return messagebox.showwarning("提示", "已有导出任务正在进行，请稍候或先取消。", parent=self)

        if getattr(self, 'sm', None):
            active_spk = self.sm.get_active_speaker()
            if active_spk:
                self.active_speaker = active_spk

        scope = self.var_export_scope.get()
        fmt = self.combo_format.get()
        ext = ".png"
        if "svg" in fmt.lower():
            ext = ".svg"
        elif "pdf" in fmt.lower():
            ext = ".pdf"
        params_snapshot = self._snapshot_render_params()

        if scope == "separate" and len(self.all_speakers) > 1:
            out_dir = filedialog.askdirectory(title="选择声学图表导出文件夹")
            if not out_dir:
                return

            jobs = []
            for speaker in self.all_speakers:
                data = self._extract_active_data([speaker])
                if not data:
                    continue
                name_suffix = "共振峰可视化图表" if self._is_formant_mode() else "声调可视化图表"
                out_path = os.path.join(out_dir, f"{speaker.name}_{name_suffix}{ext}")
                jobs.append({
                    "speaker_name": speaker.name,
                    "data": data,
                    "out_path": out_path,
                    "ext": ext
                })
            if not jobs:
                if self._is_formant_mode():
                    return messagebox.showwarning("提示", "没有可导出的有效共振峰数据。", parent=self)
                return messagebox.showwarning("提示", "没有可导出的有效基频数据。", parent=self)

            self._start_async_export(jobs, f"批量图表成功导出至:\n{out_dir}", params_snapshot=params_snapshot)
            return

        if self._is_formant_mode():
            default_name = "formant_integrated_charts" if scope == "integrated" else "formant_charts"
        else:
            default_name = "tone_integrated_acoustic_charts" if scope == "integrated" else "tone_acoustic_charts"
        out_file = filedialog.asksaveasfilename(
            title="导出图表",
            defaultextension=ext,
            initialfile=default_name,
            filetypes=[("图像文件", f"*{ext}")]
        )
        if not out_file:
            return

        data = self._get_current_data_entries()
        if not data:
            if self._is_formant_mode():
                return messagebox.showwarning("提示", "没有有效共振峰曲线，无法导出！", parent=self)
            return messagebox.showwarning("提示", "没有有效基频曲线，无法导出！", parent=self)

        jobs = [{
            "speaker_name": "当前任务",
            "data": data,
            "out_path": out_file,
            "ext": ext
        }]
        self._start_async_export(jobs, f"图表已成功保存至:\n{out_file}", params_snapshot=params_snapshot)

    def toggle_minimize(self):
        if not hasattr(self, 'is_minimized'):
            self.is_minimized = False

        if not self.is_minimized:
            self.saved_geometry = self.geometry()
            if hasattr(self, 'top_mode_bar'):
                self.top_mode_bar.grid_remove()
            self.left_scroll.grid_remove()
            self.grid_columnconfigure(0, minsize=0)
            if hasattr(self, 'preview_top_bar'):
                self.preview_top_bar.grid_remove()
            if hasattr(self, 'btn_restore'):
                self.btn_restore.pack(side=tk.LEFT, padx=3)
            self.attributes("-topmost", True)
            self.minsize(300, 200)
            self.geometry("450x380")
            self.is_minimized = True
            self.update_preview()
        else:
            self.attributes("-topmost", False)
            self.minsize(800, 500)
            if hasattr(self, 'top_mode_bar'):
                self.top_mode_bar.grid(row=0, column=0, columnspan=2, sticky="ew", padx=15, pady=(10, 4))
            self.left_scroll.grid(row=1, column=0, sticky="nsew", padx=(15, 8), pady=(4, 12))
            self.grid_columnconfigure(0, weight=0, minsize=400)
            if hasattr(self, 'btn_restore'):
                self.btn_restore.pack_forget()
            if hasattr(self, 'preview_top_bar'):
                self.preview_top_bar.grid(row=0, column=0, sticky="ew", pady=(0, 4))
            if hasattr(self, 'saved_geometry') and self.saved_geometry:
                self.geometry(self.saved_geometry)
            else:
                self.geometry("1040x680")
            self.is_minimized = False
            self.update_preview()

    def _on_arrow_left(self):
        if hasattr(self, 'group_pagination_frame') and self.group_pagination_frame.winfo_ismapped():
            self._prev_group_page()
        elif hasattr(self, 'pagination_frame') and self.pagination_frame.winfo_ismapped():
            self._prev_page()

    def _on_arrow_right(self):
        if hasattr(self, 'group_pagination_frame') and self.group_pagination_frame.winfo_ismapped():
            self._next_group_page()
        elif hasattr(self, 'pagination_frame') and self.pagination_frame.winfo_ismapped():
            self._next_page()

    def _on_arrow_up(self):
        if hasattr(self, 'pagination_frame') and self.pagination_frame.winfo_ismapped():
            self._prev_page()

    def _on_arrow_down(self):
        if hasattr(self, 'pagination_frame') and self.pagination_frame.winfo_ismapped():
            self._next_page()

    def invalidate_cache(self):
        if hasattr(self, '_speaker_data_cache'):
            self._speaker_data_cache.clear()
        try:
            self._update_group_filters()
        except Exception:
            pass

    def _show_context_menu(self, event):
        from .ui_widgets import make_context_menu, post_context_menu

        menu = make_context_menu(self, font_size=15)
        menu.add_command(label="刷新预览", command=self.manual_refresh_preview)

        has_preview_thread = (
            getattr(self, '_preview_worker', None) is not None and
            self._preview_worker.is_alive()
        )
        cancel_state = "normal" if has_preview_thread else "disabled"
        menu.add_command(label="取消本次预览", command=self._cancel_preview_render, state=cancel_state)

        minimize_label = "还原预览" if getattr(self, 'is_minimized', False) else "悬浮模式"
        menu.add_command(label=minimize_label, command=self.toggle_minimize)

        is_exporting = (
            getattr(self, '_export_worker', None) is not None and
            self._export_worker.is_alive()
        )
        export_state = "disabled" if is_exporting else "normal"
        menu.add_command(label="复制图片到剪贴板", command=self.copy_figure_to_clipboard)
        menu.add_command(label="导出图表...", command=self.on_confirm, state=export_state)
        menu.add_command(label="导出图表数据表...", command=self.export_chart_table_data)

        menu.add_separator()
        menu.add_checkbutton(label="实时刷新", variable=self.var_live_refresh, command=self._on_live_refresh_toggle)
        menu.add_checkbutton(label="高渲染精细度", variable=self.var_high_precision, command=self.update_preview)

        show_speaker = hasattr(self, 'pagination_frame') and self.pagination_frame.winfo_ismapped()
        show_group = hasattr(self, 'group_pagination_frame') and self.group_pagination_frame.winfo_ismapped()

        if show_speaker or show_group:
            menu.add_separator()
            if show_speaker:
                menu.add_command(label="上一个发音人", command=self._prev_page)
                menu.add_command(label="下一个发音人", command=self._next_page)
            if show_group:
                menu.add_command(label="上一组别页", command=self._prev_group_page)
                menu.add_command(label="下一组别页", command=self._next_group_page)

        post_context_menu(menu, event)
        return "break"

    def _bind_chart_context_menu(self, widget):
        if not getattr(widget, "_phontracer_ctx_bound", False):
            widget._phontracer_ctx_bound = True
            widget.bind("<Button-2>", self._show_context_menu, add="+")
            widget.bind("<Button-3>", self._show_context_menu, add="+")
        internal_children = {
            getattr(widget, attr, None)
            for attr in ("_canvas", "_text_label", "_image_label")
        }
        try:
            for child in widget.winfo_children():
                if child in internal_children:
                    continue
                if child != getattr(self, 'btn_toggle_minimize', None):
                    self._bind_chart_context_menu(child)
        except Exception:
            pass
