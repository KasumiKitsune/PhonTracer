# -*- coding: utf-8 -*-
"""
modules.chart_engine.tone_stats_and_qc_plotters
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
音调分布统计、时序效应分析、时域核密度估计、Spaghetti 质检叠加图与发音人质检面板。
完全无头，仅依赖 matplotlib Agg 后端，不依赖任何 GUI 库。
"""
from __future__ import annotations

import math
import warnings
from typing import Any, Dict, List, Optional, Tuple
import numpy as np

import matplotlib
matplotlib.use("Agg", force=True)
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
import matplotlib.cm as cm
from matplotlib.collections import LineCollection
from scipy.stats import gaussian_kde

from modules.core.acoustic_math import (
    _safe_nanmean,
    _safe_nanstd,
    hz_to_t_value,
)
from modules.data_utils import split_into_syllables


class ToneStatsAndQCPlottersMixin:
    """音调统计与质检可视化混入类。"""

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
