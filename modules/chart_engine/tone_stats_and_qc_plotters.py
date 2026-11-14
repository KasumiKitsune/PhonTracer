# -*- coding: utf-8 -*-
"""
modules.chart_engine.tone_stats_and_qc_plotters
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
音调分布统计、音节物理时长对比、时序效应分析、时域核密度估计、Spaghetti 质检叠加图与发音人质检面板。
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

from modules.core.acoustic_math import (
    _safe_nanmean,
    _safe_nanstd,
    evaluate_1d_kde,
    evaluate_2d_kde_grid,
    hz_to_t_value,
)
from modules.data_utils import get_item_syllable_bounds, split_into_syllables


class ToneStatsAndQCPlottersMixin:
    """音调统计、物理时长对比与质检可视化混入类。"""

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
        1) 右侧半小提琴核密度云 (Half-Violin KDE，严格裁剪至有效数据极值/内群区间，杜绝细长针状尾巴)
        2) 中央窄箱线图 (Narrow Boxplot)
        3) 左侧个体抖动散点 (Individual Jittered Scatter)
        """
        for idx, (vals, pos, color) in enumerate(zip(data_list, positions, colors)):
            arr = np.asarray(vals, dtype=float)
            arr = arr[np.isfinite(arr)]
            if arr.size == 0:
                continue

            # 1. Half-Violin KDE clipped strictly to data min/max & core distribution (no needle spikes to distant outliers)
            if arr.size >= 2 and np.std(arr) > 1e-5:
                try:
                    kde_vals = arr
                    if arr.size >= 4:
                        q1, q3 = np.percentile(arr, [25.0, 75.0])
                        iqr = float(q3 - q1)
                        if iqr > 1e-4:
                            inliers = arr[(arr >= q1 - 1.5 * iqr) & (arr <= q3 + 1.5 * iqr)]
                            if inliers.size >= 3 and np.std(inliers) > 1e-5:
                                kde_vals = inliers
                    v_lo = float(np.min(kde_vals))
                    v_hi = float(np.max(kde_vals))
                    if v_hi > v_lo + 1e-5:
                        y_grid = np.linspace(v_lo, v_hi, 72)
                        dens = evaluate_1d_kde(kde_vals, y_grid, bw_method=0.34)
                        max_d = float(np.max(dens)) if dens.size > 0 else 0.0
                        if max_d > 0:
                            # Trim near-zero density tails (< 4% peak) so thin edge strokes never form vertical needles
                            keep = dens >= 0.04 * max_d
                            if np.count_nonzero(keep) >= 3:
                                k_idx = np.where(keep)[0]
                                y_sub = y_grid[k_idx[0] : k_idx[-1] + 1]
                                d_sub = dens[k_idx[0] : k_idx[-1] + 1]
                            else:
                                y_sub, d_sub = y_grid, dens
                            dens_scaled = (d_sub / max_d) * width
                            ax.fill_betweenx(
                                y_sub,
                                pos + 0.02,
                                pos + 0.02 + dens_scaled,
                                facecolor=color,
                                edgecolor=color,
                                alpha=0.52,
                                linewidth=1.1,
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


    @staticmethod
    def _clip_outliers_for_violin(columns):
        """Clip extreme single-point outliers per column so matplotlib violinplot never draws thin full-canvas spikes."""
        clipped = []
        for col in columns:
            arr = np.asarray(col, dtype=float)
            arr = arr[np.isfinite(arr)]
            if arr.size >= 4:
                q1, q3 = np.percentile(arr, [25.0, 75.0])
                iqr = float(q3 - q1)
                if iqr > 1e-4:
                    inl = arr[(arr >= q1 - 1.5 * iqr) & (arr <= q3 + 1.5 * iqr)]
                    if inl.size >= 3 and np.std(inl) > 1e-5:
                        clipped.append(inl)
                        continue
            clipped.append(arr if arr.size > 0 else np.array([np.nan]))
        return clipped


    def _extract_entry_syllable_durations_ms(self, entry) -> List[float]:
        """Extract per-syllable physical durations (in ms) robustly across both f0 and formant modes."""
        syl_list = entry.get("syl_data")
        if syl_list:
            durs = [float(s_dur) * 1000.0 for s_dur, _pts in syl_list if s_dur is not None and float(s_dur) > 0]
            if durs:
                return durs
        syl_formants = entry.get("syl_formants")
        if syl_formants:
            durs = []
            for sf in syl_formants:
                b = sf.get("bounds")
                if b and len(b) == 2 and float(b[1]) > float(b[0]):
                    durs.append((float(b[1]) - float(b[0])) * 1000.0)
            if durs:
                return durs
        raw_item = entry.get("raw_item")
        if isinstance(raw_item, dict):
            bounds = get_item_syllable_bounds(raw_item)
            if bounds:
                durs = [(float(b[1]) - float(b[0])) * 1000.0 for b in bounds if float(b[1]) > float(b[0])]
                if durs:
                    return durs
            s_v, e_v = raw_item.get("start"), raw_item.get("end")
            if s_v is not None and e_v is not None and float(e_v) > float(s_v):
                return [(float(e_v) - float(s_v)) * 1000.0]
        tot = float(entry.get("total_dur") or 0.0)
        if tot > 0:
            return [tot * 1000.0]
        return []


    def _plot_syllable_duration_comparison(self, data_entries, group_key, scale="T 值"):
        """
        各组/各词项音节物理时长对比图 (`export duration`)：
        - 100% 兼容 `f0` 与 `formant` 分析模式；
        - 单音节语料：绘制组/词项物理时长均值柱状图 + ±1 SD 误差棒 + 个体抖动散点 + 顶部数值标注；
        - 双音节/多音节语料（如二字组连读变调）：绘制分音节堆叠物理时长柱状图（前字+后字）+ 全词总时长 ±1 SD 误差棒 + 顶部总时长标注。
        """
        facet_mode = self._resolve_facet_by(chart_type="duration", fallback="none")

        def _entry_facet_val(e):
            if facet_mode == "speaker":
                return e.get("speaker_name", "默认发音人")
            if facet_mode == "group":
                return self._entry_wordlist_group(e) if group_key != "group" else e.get("group", "未分组")
            if facet_mode == "label":
                return e.get("label", "未命名")
            return "Default"

        facet_keys = ["Default"] if facet_mode == "none" else self._ordered_unique(_entry_facet_val(e) for e in data_entries)
        n_facets = max(1, len(facet_keys))
        n_cols = min(2, n_facets)
        n_rows = math.ceil(n_facets / n_cols)

        all_durs_check = [self._extract_entry_syllable_durations_ms(e) for e in data_entries]
        max_syls = max((len(d) for d in all_durs_check), default=1)
        unique_cats_global = self._ordered_unique(e.get(group_key, e.get("group", "未分组")) for e in data_entries)

        base_panel_w = max(7.4, min(13.5, 1.05 * len(unique_cats_global) + 2.2)) if n_facets == 1 else max(5.6, 0.85 * len(unique_cats_global) + 1.8)
        fig, axes = plt.subplots(
            n_rows, n_cols,
            figsize=(base_panel_w * n_cols, 4.8 * n_rows + 0.4),
            squeeze=False, sharey=(n_facets > 1),
        )
        axes_flat = axes.flatten()
        duration_summary = {}

        syl_hatches = ["", "//", "..", "xx"]
        syl_alphas = [0.76, 0.48, 0.34, 0.26]

        for f_idx, f_key in enumerate(facet_keys):
            ax = axes_flat[f_idx]
            ax.set_facecolor("#FFFFFF")
            ax.grid(True, axis="y", linestyle="--", alpha=0.28)

            sub_entries = data_entries if facet_mode == "none" else [e for e in data_entries if _entry_facet_val(e) == f_key]
            grouped = {}
            for e in sub_entries:
                cat = e.get(group_key, e.get("group", "未分组"))
                d_list = self._extract_entry_syllable_durations_ms(e)
                if d_list:
                    grouped.setdefault(cat, []).append(d_list)

            if not grouped:
                ax.text(0.5, 0.5, "没有找到有效的音节物理时长数据", ha="center", va="center", color="#6B7280")
                continue

            cats = list(grouped.keys())
            x_pos = np.arange(len(cats), dtype=float)
            bar_w = min(0.54, max(0.32, 3.2 / max(4, len(cats))))
            max_top_y = 0.0

            if max_syls <= 1:
                for c_i, cat in enumerate(cats):
                    totals = np.asarray([ sum(d) for d in grouped[cat] ], dtype=float)
                    mean_v = float(np.mean(totals))
                    sd_v = float(np.std(totals, ddof=1)) if totals.size > 1 else 0.0
                    color = self.colors[c_i % len(self.colors)]

                    ax.bar(
                        x_pos[c_i], mean_v, width=bar_w,
                        color=color, alpha=0.74, edgecolor="#111827", linewidth=0.85, zorder=3,
                    )
                    if totals.size > 1 and sd_v > 0:
                        ax.errorbar(
                            x_pos[c_i], mean_v, yerr=sd_v,
                            fmt="none", ecolor="#111827", elinewidth=1.3, capsize=4.5, capthick=1.2, zorder=5,
                        )
                    # Subtle jittered individual token dots
                    rng = np.random.default_rng(2026 + c_i)
                    jitter = rng.uniform(-bar_w * 0.24, bar_w * 0.24, size=totals.size)
                    ax.scatter(
                        np.full(totals.size, x_pos[c_i]) + jitter, totals,
                        color="#FFFFFF", edgecolors=color, s=22, linewidth=1.0, alpha=0.82, zorder=4,
                    )
                    top_pt = max(mean_v + (sd_v if totals.size > 1 else 0.0), float(np.max(totals)))
                    max_top_y = max(max_top_y, top_pt)
                    duration_summary[f"{f_key}:{cat}" if n_facets > 1 else str(cat)] = {
                        "count": int(totals.size),
                        "mean_ms": round(mean_v, 2),
                        "sd_ms": round(sd_v, 2),
                    }
                    ax._pending_bar_labels = getattr(ax, "_pending_bar_labels", []) + [(x_pos[c_i], top_pt, f"{mean_v:.0f} ms")]

                if max_top_y > 0:
                    y_upper = max_top_y * 1.22
                    ax.set_ylim(0.0, y_upper)
                    for bx, btop, blbl in getattr(ax, "_pending_bar_labels", []):
                        ax.text(
                            bx, btop + 0.024 * y_upper, blbl,
                            ha="center", va="bottom", fontsize=8.5, fontweight="bold", color="#111827",
                            bbox=dict(boxstyle="round,pad=0.12", fc="#FFFFFF", ec="none", alpha=0.82),
                            zorder=6,
                        )
            else:
                # Multi-syllable stacked duration bars (e.g. Syllable 1 + Syllable 2) with total duration ±1 SD error bar
                syl_names = ["第1音节 (前字)", "第2音节 (后字)"] if max_syls == 2 else [f"第{k + 1}音节" for k in range(max_syls)]
                legend_added = [False] * max_syls

                for c_i, cat in enumerate(cats):
                    d_rows = grouped[cat]
                    totals = np.asarray([sum(r) for r in d_rows], dtype=float)
                    mean_tot = float(np.mean(totals))
                    sd_tot = float(np.std(totals, ddof=1)) if totals.size > 1 else 0.0
                    color = self.colors[c_i % len(self.colors)]

                    bottom_acc = 0.0
                    syl_means = []
                    for s_k in range(max_syls):
                        s_vals = [r[s_k] for r in d_rows if s_k < len(r)]
                        m_sk = float(np.mean(s_vals)) if s_vals else 0.0
                        syl_means.append(m_sk)
                        lbl_s = syl_names[s_k] if not legend_added[s_k] else None
                        bar_patch = ax.bar(
                            x_pos[c_i], m_sk, bottom=bottom_acc, width=bar_w,
                            color=color, alpha=syl_alphas[s_k % len(syl_alphas)],
                            edgecolor="#111827", linewidth=0.85,
                            hatch=syl_hatches[s_k % len(syl_hatches)],
                            label=lbl_s, zorder=3,
                        )
                        if lbl_s is not None and bar_patch:
                            # Make legend swatch neutral gray so hatch/alpha represents syllable position across all groups
                            legend_added[s_k] = True
                        has_err = bool(totals.size > 1 and sd_tot > 0)
                        txt_x = (x_pos[c_i] - bar_w * 0.16) if (has_err and s_k == max_syls - 1) else x_pos[c_i]
                        if m_sk >= 52.0:
                            ax.text(
                                txt_x, bottom_acc + 0.5 * m_sk,
                                f"{m_sk:.0f}",
                                ha="center", va="center", fontsize=7.5, fontweight="bold",
                                color="#FFFFFF" if s_k == 0 else "#111827",
                                bbox=dict(
                                    boxstyle="round,pad=0.12",
                                    fc="#111827" if s_k == 0 else "#FFFFFF",
                                    ec="none",
                                    alpha=0.62 if s_k == 0 else 0.88,
                                ),
                                zorder=5.2,
                            )
                        bottom_acc += m_sk

                    if totals.size > 1 and sd_tot > 0:
                        err_x = x_pos[c_i] + bar_w * 0.20
                        ax.errorbar(
                            err_x, bottom_acc, yerr=sd_tot,
                            fmt="none", ecolor="#111827", elinewidth=1.3, capsize=4.0, capthick=1.2, zorder=5.0,
                        )
                    top_pt = bottom_acc + (sd_tot if totals.size > 1 else 0.0)
                    max_top_y = max(max_top_y, top_pt)
                    duration_summary[f"{f_key}:{cat}" if n_facets > 1 else str(cat)] = {
                        "count": int(totals.size),
                        "mean_ms": round(mean_tot, 2),
                        "sd_ms": round(sd_tot, 2),
                        "syllable_means_ms": [round(v, 2) for v in syl_means],
                    }
                    ax._pending_bar_labels = getattr(ax, "_pending_bar_labels", []) + [(x_pos[c_i], top_pt, f"{bottom_acc:.0f} ms")]

                if max_top_y > 0:
                    y_upper = max_top_y * 1.22
                    ax.set_ylim(0.0, y_upper)
                    for bx, btop, blbl in getattr(ax, "_pending_bar_labels", []):
                        ax.text(
                            bx, btop + 0.022 * y_upper, blbl,
                            ha="center", va="bottom", fontsize=8.2, fontweight="bold", color="#111827", zorder=6,
                        )

                # Build neutral-gray legend proxies for Syllable 1 vs Syllable 2
                from matplotlib.patches import Patch
                if f_idx == min(n_cols - 1, n_facets - 1):
                    proxies = [
                        Patch(
                            facecolor="#4B5563", alpha=syl_alphas[k % len(syl_alphas)],
                            edgecolor="#111827", hatch=syl_hatches[k % len(syl_hatches)],
                            label=syl_names[k],
                        )
                        for k in range(max_syls)
                    ]
                    leg_kw = self._get_legend_kwargs(ax=ax, default_outside=True)
                    leg_kw["fontsize"] = 8.5
                    ax.legend(handles=proxies, **leg_kw)

            ax.set_xticks(x_pos)
            rot = 28 if (len(cats) > 6 or any(len(str(c)) > 5 for c in cats)) else 0
            ax.set_xticklabels(
                [str(c) for c in cats],
                rotation=rot,
                ha="right" if rot else "center",
                fontsize=9.0,
                fontweight="bold",
            )
            if f_idx % n_cols == 0:
                ax.set_ylabel("物理时长 Duration (ms)")
            if (f_idx // n_cols == n_rows - 1) or (f_idx + n_cols >= n_facets):
                if group_key == "label":
                    ax.set_xlabel("词项")
                elif group_key == "speaker_name":
                    ax.set_xlabel("发音人")
                else:
                    ax.set_xlabel("组别 / 调类")

            t_str = "各组音节物理时长对比 (Mean ± SD)" if f_key == "Default" else str(f_key)
            ax.set_title(t_str, fontsize=11.5, fontweight="bold")
            ax._original_title = t_str

        for idx in range(n_facets, len(axes_flat)):
            axes_flat[idx].set_visible(False)

        fig._duration_summary = duration_summary
        fig.tight_layout()
        return fig


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
                    parts = ax.violinplot(self._clip_outliers_for_violin(data_to_plot), positions=positions, showmeans=True)
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
                ax.set_xlabel("声调类别")
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
                    parts = ax.violinplot(self._clip_outliers_for_violin(data_to_plot), positions=positions, showmeans=True)
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
                ax.set_ylabel("T 值 (0-5 标度)" if "T 值" in scale else "频率 (Hz)")
                ax.set_title("各声调起-中-终三点基频离散比较", fontsize=13, fontweight="bold")
                ax._original_title = "各声调起-中-终三点基频离散比较"
                if "T 值" in scale:
                    ax.set_ylim(-0.2, 5.2)
                    ax.set_yticks([0, 1, 2, 3, 4, 5])

            fig._tone_distribution_metrics = all_group_metrics
            fig.tight_layout()
            return fig

        elif dt_low in ("range", "调域范围跨度图") or "调域范围" in dist_type_val:
            fig_h = max(5.0, 0.70 * n_groups + 1.2)
            fig, ax = plt.subplots(figsize=(8.6, fig_h))
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
                    avg_min = float(np.mean(min_vals))
                    avg_max = float(np.mean(max_vals))
                    span_w = avg_max - avg_min
                    ax.barh(
                        idx, span_w, left=avg_min, height=0.66,
                        color=color, alpha=0.72, edgecolor=color, linewidth=1.1, align='center',
                    )
                    ax.plot([avg_min, avg_max], [idx, idx], '|', color='#111827', markersize=12, markeredgewidth=2)
                    try:
                        import matplotlib.colors as _mcolors
                        rc, gc, bc = _mcolors.to_rgb(color)
                        r_eff = 0.72 * rc + 0.28 * 1.0
                        g_eff = 0.72 * gc + 0.28 * 1.0
                        b_eff = 0.72 * bc + 0.28 * 1.0
                        lum_eff = 0.299 * r_eff + 0.587 * g_eff + 0.114 * b_eff
                    except Exception:
                        lum_eff = 0.65
                    use_pill = bool(span_w < 1.05)
                    txt_color_bar = "#111827" if use_pill else ("#FFFFFF" if lum_eff < 0.50 else "#111827")
                    ax.text(
                        (avg_min + avg_max) / 2.0,
                        idx,
                        f"{avg_min:.2f} ~ {avg_max:.2f}\n(调域: {span_w:.2f})",
                        ha='center', va='center', color=txt_color_bar,
                        fontsize=8.2, fontweight='bold', linespacing=1.15,
                        bbox=dict(boxstyle="round,pad=0.14", fc="#FFFFFF", ec="none", alpha=0.86) if use_pill else None,
                    )

            ax.set_yticks(y_positions)
            ax.set_yticklabels(group_names, fontsize=10.5, fontweight="bold")
            ax.set_ylim(-0.6, max(0.6, n_groups - 0.4))
            ax.set_ylabel("调类 / 组合")
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

            max_val = max(var_values, default=0.0)
            y_top = max_val * 1.18 if max_val > 0 else 1.0
            ax.set_ylim(0.0, y_top)

            for idx, val in enumerate(var_values):
                lbl_str = f"{val:.2f}" if is_t_scale else f"{val:.1%}"
                offset = max_val * 0.025 if max_val > 0 else 0.01
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

        else:  # 测量点精细分布 (boxplot_violin point-wise view)
            n_cols = min(2, max(1, n_groups))
            n_rows = math.ceil(max(1, n_groups) / n_cols)
            fig, axes = plt.subplots(n_rows, n_cols, figsize=(5.2 * n_cols + 1.8, 4.2 * n_rows + 0.6), squeeze=False, sharex=True, sharey=True)
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
                    v_cols = self._clip_outliers_for_violin(cleaned_columns)
                    parts = ax.violinplot(v_cols, positions, showmeans=True, showmedians=False)
                    col_c = self.colors[idx % len(self.colors)]
                    for pc in parts['bodies']:
                        pc.set_facecolor(col_c)
                        pc.set_edgecolor(col_c)
                        pc.set_alpha(0.55)
                else:
                    ax.boxplot(cleaned_columns, positions=positions, patch_artist=True,
                               boxprops=dict(facecolor="#DBEAFE", color="#1E40AF"),
                               whiskerprops=dict(color="#1E40AF"),
                               capprops=dict(color="#1E40AF"),
                               medianprops=dict(color="#DC2626", linewidth=1.5))

                if max_syls > 1:
                    for k in range(1, max_syls):
                        ax.axvline(
                            k * num_points + 0.5,
                            color="#475569", linestyle="--", linewidth=1.15, alpha=0.70, zorder=2.5,
                        )

                ax.set_title(f"{g_name} 基频测量点分布", fontsize=11, fontweight="bold")
                ax._original_title = f"{g_name} 基频测量点分布"
                if "T 值" in scale:
                    ax.set_ylim(-0.2, 5.2)
                    ax.set_yticks([0, 1, 2, 3, 4, 5])
                ax.set_xticks(positions)
                ax.set_xticklabels([str((p - 1) % num_points + 1) for p in positions], fontsize=8)

                row_idx = idx // n_cols
                col_idx = idx % n_cols
                if col_idx == 0:
                    ax.set_ylabel("T 值 (0-5 标度)" if "T 值" in scale else "频率 (Hz)")
                else:
                    ax.set_ylabel("")
                if (row_idx == n_rows - 1) or (idx + n_cols >= n_groups):
                    ax.set_xlabel("音节测量点 (时序展开)")
                else:
                    ax.set_xlabel("")

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
        ax.set_xlim(-2.0, 202.0)
        ax.set_ylim(-0.035, 1.05)
        ax.set_xticks([5, 50, 100, 150, 195])
        ax.set_xticklabels(["前字5%", "前字50%", "音节界", "后字50%", "后字95%"])
        ax.set_xlabel("两字组归一化时间轴")
        ax.set_ylabel("调类组合均值方差占比 (η²)")
        ax.set_title("二字组调类效应时间进程", fontsize=13, fontweight="bold")
        ax._original_title = "二字组调类效应时间进程"
        ax.grid(True, linestyle="--", alpha=0.25, linewidth=0.8)
        legend_kwargs = self._get_legend_kwargs(ax=ax, default_outside=True)
        legend_kwargs["fontsize"] = 9
        ax.legend(**legend_kwargs)
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

        def _density_facet_key(e):
            if facet_mode == "speaker":
                return e.get("speaker_name", "默认发音人")
            if facet_mode == "label":
                return e.get("label", "未命名")
            if facet_mode == "group":
                return self._entry_wordlist_group(e) if group_key != "group" else e.get("group", "未分组")
            return e.get(group_key, e.get('group', '未分组'))

        facet_keys = ["Default"]
        if facet_mode != "none":
            facet_keys = self._ordered_unique(_density_facet_key(e) for e in data_entries)

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

            facet_entries = data_entries if facet_mode == "none" else [e for e in data_entries if _density_facet_key(e) == f_key]

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

            try:
                self._check_export_cancelled()
                grid_x = max(120, min(240, int(80 * max_syls))) if high_prec else max(60, min(120, int(40 * max_syls)))
                grid_y = 90 if high_prec else 50
                xi, yi = np.mgrid[xmin:xmax:complex(0, grid_x), ymin:ymax:complex(0, grid_y)]
                zi = evaluate_2d_kde_grid(x_arr, y_arr, xi, yi, bw_method=bw_method)

                vmax = float(np.nanmax(zi)) if zi.size > 0 else 0.0
                if vmax > 0:
                    levels = np.linspace(vmax * 0.05, vmax, 30)
                    ax.contourf(xi, yi, zi, levels=levels, cmap="YlOrRd", extend='neither')
            except Exception as e:
                ax.text(
                    0.5, 0.5,
                    f"KDE 计算失败:\n{str(e)[:120]}",
                    ha='center', va='center', color='red', fontsize=9.5,
                    transform=ax.transAxes, wrap=True,
                )

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
                ax.set_xlabel("归一化音节时序 (%)")
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
                g_val = self._entry_wordlist_group(entry) if group_key != "group" else entry.get('group', '未分组')
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
            stat_outlier_count = 0
            warn_outlier_count = 0
            outlier_count = 0
            pending_outlier_tags = []
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
                c_info["has_warning"] = bool(has_warn)
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
                    if has_warn:
                        warn_outlier_count += 1
                        lbl_plot = "声学/切分警告轨迹" if warn_outlier_count == 1 else None
                        ax.plot(
                            x_grid, y_arr,
                            color="#DC2626", linestyle="-", linewidth=2.2, alpha=0.90,
                            zorder=6, label=lbl_plot,
                        )
                        tag_fg, tag_ec, tag_arrow = "#B91C1C", "#FCA5A5", "#DC2626"
                    else:
                        stat_outlier_count += 1
                        lbl_plot = "组内统计离群参考 (>2σ, 无声学警告)" if stat_outlier_count == 1 else None
                        ax.plot(
                            x_grid, y_arr,
                            color="#D97706", linestyle="--", linewidth=1.7, alpha=0.85,
                            zorder=5, label=lbl_plot,
                        )
                        tag_fg, tag_ec, tag_arrow = "#B45309", "#FCD34D", "#D97706"
                    finite_idx = np.where(np.isfinite(y_arr))[0]
                    if finite_idx.size > 0:
                        if finite_z.size > 0:
                            peak_i = int(finite_idx[int(np.argmax(np.abs(y_arr[finite_idx] - med_y[finite_idx])))])
                        else:
                            peak_i = int(finite_idx[-1])
                        last_i = int(finite_idx[-1])
                        pending_outlier_tags.append({
                            "peak_x": float(x_grid[peak_i]),
                            "peak_y": float(y_arr[peak_i]),
                            "end_x": float(x_grid[last_i]),
                            "end_y": float(y_arr[last_i]),
                            "med_at_peak": float(med_y[peak_i]) if np.isfinite(med_y[peak_i]) else float(y_arr[peak_i]),
                            "text": tag_text,
                            "fg": tag_fg,
                            "ec": tag_ec,
                            "arrow": tag_arrow,
                        })
                else:
                    normal_count += 1
                    lbl_plot = "正常轨迹 (≤2σ)" if normal_count == 1 else None
                    ax.plot(
                        x_grid, y_arr,
                        color="#94A3B8", linewidth=1.0, alpha=0.45,
                        zorder=2, label=lbl_plot,
                    )

            if np.any(np.isfinite(med_y)):
                ax.plot(x_grid, med_y, color="#1E293B", linestyle="--", linewidth=1.5, alpha=0.85, zorder=4, label="组中位数包络")
                ax.fill_between(x_grid, med_y - 2.0 * sigma, med_y + 2.0 * sigma, color="#CBD5E1", alpha=0.18, zorder=1)

            if max_syls > 1:
                for k in range(1, max_syls):
                    ax.axvline(k * num_points + 0.5, color="gray", linestyle="--", alpha=0.5)

            # Set generous X/Y limits on ALL subplots (including right column!) so outlier labels never cross borders
            right_margin_x = max(4.2, total_pts * 0.36) if pending_outlier_tags else 0.8
            ax.set_xlim(0.35, float(total_pts) + right_margin_x)
            ax.set_xticks([p for p in range(1, total_pts + 1) if total_pts <= 12 or p in (1, num_points // 2 + 1, num_points, num_points + 1, total_pts)])

            if "T 值" in scale and not is_formant:
                ax.set_ylim(-0.38, 5.38)
                ax.set_yticks([0, 1, 2, 3, 4, 5])
            else:
                ylo_c, yhi_c = ax.get_ylim()
                ysp_c = max(10.0, float(yhi_c - ylo_c))
                ax.set_ylim(ylo_c - 0.10 * ysp_c, yhi_c + 0.14 * ysp_c)

            # Non-overlapping outlier tag layout inside subplot boundaries
            if pending_outlier_tags:
                ylo_f, yhi_f = ax.get_ylim()
                yspan_f = max(1e-3, float(yhi_f - ylo_f))
                xlo_f, xhi_f = ax.get_xlim()
                xspan_f = max(1e-3, float(xhi_f - xlo_f))
                ax_w_pts = max(210.0, 5.0 * 72.0 * 0.80)
                ax_h_pts = max(165.0, 4.1 * 72.0 * 0.76)

                tail_tags = [t for t in pending_outlier_tags if t["peak_x"] >= total_pts - 2.2]
                inner_tags = [t for t in pending_outlier_tags if t["peak_x"] < total_pts - 2.2]

                # 1) Right-tail tags: stack & repel cleanly below upper-right legend box
                if tail_tags:
                    tail_tags_sorted = sorted(tail_tags, key=lambda d: d["end_y"])
                    raw_tys = [d["end_y"] for d in tail_tags_sorted]
                    rep_min = ylo_f + 0.08 * yspan_f
                    rep_max = yhi_f - 0.24 * yspan_f
                    min_gap_y = 0.095 * yspan_f
                    pos_y = list(raw_tys)
                    n_t = len(pos_y)
                    eff_g = min(min_gap_y, max(1e-3, (rep_max - rep_min)) / max(1, n_t))
                    for _ in range(40):
                        for k in range(n_t - 1):
                            if pos_y[k + 1] - pos_y[k] < eff_g:
                                mid_v = 0.5 * (pos_y[k] + pos_y[k + 1])
                                pos_y[k] = mid_v - 0.5 * eff_g
                                pos_y[k + 1] = mid_v + 0.5 * eff_g
                        pos_y[0] = max(rep_min, pos_y[0])
                        for k in range(1, n_t):
                            pos_y[k] = max(pos_y[k], pos_y[k - 1] + eff_g)
                        pos_y[-1] = min(rep_max, pos_y[-1])
                        for k in range(n_t - 2, -1, -1):
                            pos_y[k] = min(pos_y[k], pos_y[k + 1] - eff_g)
                    pts_per_y = ax_h_pts / yspan_f
                    for t_obj, adj_y in zip(tail_tags_sorted, pos_y):
                        dy_pts = float((adj_y - t_obj["end_y"]) * pts_per_y)
                        ax.annotate(
                            t_obj["text"],
                            xy=(t_obj["end_x"], t_obj["end_y"]),
                            xytext=(6.0, dy_pts),
                            textcoords="offset points",
                            fontsize=7.3,
                            fontweight="bold",
                            color=t_obj["fg"],
                            ha="left",
                            va="center",
                            zorder=7,
                            bbox=dict(boxstyle="round,pad=0.14", fc="#FFFFFF", ec=t_obj["ec"], lw=0.6, alpha=0.90),
                            arrowprops=dict(arrowstyle="-", color=t_obj["arrow"], lw=0.65, alpha=0.65, shrinkA=0, shrinkB=2),
                        )

                # 2) Interior peak tags: repel in 2D point space away from curve, top/bottom ticks, and right spine
                if inner_tags:
                    boxes = []
                    anchors = []
                    for t_obj in inner_tags:
                        px = (t_obj["peak_x"] - xlo_f) / xspan_f * ax_w_pts
                        py = (t_obj["peak_y"] - ylo_f) / yspan_f * ax_h_pts
                        txt_s = t_obj["text"]
                        cw = sum(1.0 if ord(ch) > 127 else 0.55 for ch in txt_s)
                        hw = max(24.0, 0.5 * cw * 7.3 + 5.0)
                        hh = 7.8
                        prefer_up = (t_obj["peak_y"] >= t_obj["med_at_peak"]) and (py + 2 * hh + 14.0 < ax_h_pts)
                        by = (py + hh + 9.0) if prefer_up else (py - hh - 9.0)
                        by = float(np.clip(by, hh + 8.0, ax_h_pts - hh - 10.0))
                        bx = float(np.clip(px, hw + 6.0, ax_w_pts - hw - 8.0))
                        anchors.append((px, py))
                        boxes.append([bx, by, hw, hh])
                    for _it in range(40):
                        for i in range(len(boxes)):
                            for j in range(i + 1, len(boxes)):
                                ovx = (boxes[i][2] + boxes[j][2] + 4.0) - abs(boxes[j][0] - boxes[i][0])
                                ovy = (boxes[i][3] + boxes[j][3] + 3.5) - abs(boxes[j][1] - boxes[i][1])
                                if ovx > 0 and ovy > 0:
                                    sgn = 1.0 if boxes[j][1] >= boxes[i][1] else -1.0
                                    boxes[i][1] -= 0.52 * ovy * sgn
                                    boxes[j][1] += 0.52 * ovy * sgn
                        for i in range(len(boxes)):
                            boxes[i][0] = float(np.clip(boxes[i][0], boxes[i][2] + 6.0, ax_w_pts - boxes[i][2] - 8.0))
                            boxes[i][1] = float(np.clip(boxes[i][1], boxes[i][3] + 8.0, ax_h_pts - boxes[i][3] - 10.0))
                    for t_obj, (apx, apy), (bx, by, _hw, _hh) in zip(inner_tags, anchors, boxes):
                        ax.annotate(
                            t_obj["text"],
                            xy=(t_obj["peak_x"], t_obj["peak_y"]),
                            xytext=(float(bx - apx), float(by - apy)),
                            textcoords="offset points",
                            fontsize=7.3,
                            fontweight="bold",
                            color=t_obj["fg"],
                            ha="center",
                            va="center",
                            zorder=7,
                            bbox=dict(boxstyle="round,pad=0.14", fc="#FFFFFF", ec=t_obj["ec"], lw=0.6, alpha=0.90),
                            arrowprops=dict(arrowstyle="-", color=t_obj["arrow"], lw=0.65, alpha=0.65, shrinkA=1, shrinkB=2),
                        )

            if warn_outlier_count > 0:
                sub_t = f"{g_name} - 组内个体原始曲线叠加 (警告={warn_outlier_count}, 统计离群={stat_outlier_count})"
            else:
                sub_t = f"{g_name} - 组内个体原始曲线叠加 (警告=0, 统计离群={stat_outlier_count})"
            ax.set_title(sub_t, fontsize=10.5, fontweight="bold")
            ax._original_title = sub_t
            ax.set_xlabel("归一化测量点")
            if f_idx % n_cols == 0:
                if is_formant:
                    ax.set_ylabel("F1 频率 (Hz)")
                elif "T 值" in scale:
                    ax.set_ylabel("T 值 (0-5 标度)")
                else:
                    ax.set_ylabel("频率 (Hz)")

            handles, labels = ax.get_legend_handles_labels()
            if handles:
                # Enforce deterministic legend order across all subplots: normal -> median -> stat_ref -> warning
                order_rank = {
                    "正常轨迹 (≤2σ)": 0,
                    "组中位数包络": 1,
                    "组内统计离群参考 (>2σ, 无声学警告)": 2,
                    "声学/切分警告轨迹": 3,
                }
                paired = sorted(zip(handles, labels), key=lambda hl: order_rank.get(hl[1], 99))
                s_handles = [p[0] for p in paired]
                s_labels = [p[1] for p in paired]
                legend_kwargs = self._get_legend_kwargs(ax=ax, default_outside=False)
                legend_kwargs["fontsize"] = 7.4
                legend_kwargs["loc"] = "upper right"
                ax.legend(s_handles, s_labels, **legend_kwargs)

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
            legend_kwargs = self._get_legend_kwargs(ax=ax)
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
            legend_kwargs = self._get_legend_kwargs(ax=ax)
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
            fig, ax = plt.subplots(figsize=(9.6, 5.4))
            ax.set_facecolor("#FFFFFF")
            ax.grid(True, linestyle="--", alpha=0.3)

            max_syls = max((len(e.get('syl_data', [])) for e in data_entries), default=1)
            num_points = self.project_tree.app_state_params.get('pts', 11)
            total_pts = max_syls * num_points

            normal_lines = []
            stat_ref_lines = []
            warning_lines = []
            group_rows = {}
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
                grp_k = self._entry_wordlist_group(entry) if hasattr(self, "_entry_wordlist_group") else entry.get("group", "未分组")
                if len(y_flat) == total_pts:
                    group_rows.setdefault(grp_k, []).append(y_flat)
                entry_xy.append((idx_e, entry, grp_k, x_flat, y_flat))

            # 按各调类组内中位数计算残差，避免将不同调型（如 55 与 21）混算全局均值误判为异常
            group_medians = {}
            residual_rows = []
            for gk, rows_g in group_rows.items():
                mat_g = np.vstack(rows_g)
                med_g = np.nanmedian(mat_g, axis=0)
                group_medians[gk] = med_g
                if len(rows_g) >= 2:
                    residual_rows.append(mat_g - med_g)

            if residual_rows:
                res_mat = np.vstack(residual_rows)
                mad_res = np.nanmedian(np.abs(res_mat), axis=0)
                std_classic = _safe_nanstd(res_mat, axis=0, default=0.35 if is_t_value else 12.0)
                std_robust = np.where(np.isfinite(mad_res) & (mad_res > 1e-4), 1.4826 * mad_res, std_classic)
                min_sigma = 0.25 if is_t_value else 8.0
                res_sigma = np.maximum(np.maximum(std_classic, std_robust), min_sigma)
            else:
                res_sigma = None

            warn_tag_count = 0
            stat_tag_count = 0
            pending_qc_tags = []
            for idx_e, entry, grp_k, x_flat, y_flat in entry_xy:
                has_warning = any(
                    str(w).startswith(("[警告]", "[致命]", "[复核]", "[错误]"))
                    for w in (entry.get('warnings') or [])
                )
                exceeds_stat_ref = False
                med_g = group_medians.get(grp_k)
                if not has_warning and res_sigma is not None and med_g is not None and len(y_flat) == len(med_g) and len(group_rows.get(grp_k, [])) >= 3:
                    z_vals = np.abs(y_flat - med_g) / res_sigma
                    finite_z = z_vals[np.isfinite(z_vals)]
                    if finite_z.size > 0 and (float(np.max(finite_z)) > 2.8 or float(np.sqrt(np.mean(finite_z ** 2))) > 2.1):
                        exceeds_stat_ref = True

                seg = np.column_stack((x_flat, y_flat))
                finite_idx = np.where(np.isfinite(y_flat))[0]
                item_id = entry.get('item_id', f"item_{idx_e}")
                tag_lbl = f"{entry.get('speaker_name', '')}: {entry.get('label', '')} #{item_id}"

                if has_warning:
                    warning_lines.append(seg)
                    warn_tag_count += 1
                    if finite_idx.size > 0 and warn_tag_count <= 10:
                        ti = int(finite_idx[-1])
                        pending_qc_tags.append({
                            "end_x": float(x_flat[ti]),
                            "end_y": float(y_flat[ti]),
                            "text": tag_lbl,
                            "color": "#DC2626",
                            "ec": "#FCA5A5",
                            "bold": True,
                        })
                elif exceeds_stat_ref:
                    stat_ref_lines.append(seg)
                    stat_tag_count += 1
                    if finite_idx.size > 0 and stat_tag_count <= 6:
                        ti = int(finite_idx[-1])
                        pending_qc_tags.append({
                            "end_x": float(x_flat[ti]),
                            "end_y": float(y_flat[ti]),
                            "text": tag_lbl,
                            "color": "#B45309",
                            "ec": "#FCD34D",
                            "bold": False,
                        })
                else:
                    normal_lines.append(seg)

            if normal_lines:
                lc_norm = LineCollection(normal_lines, colors="#94A3B8", linewidths=1.0, alpha=0.42)
                ax.add_collection(lc_norm)
                ax.plot([], [], color="#94A3B8", linewidth=1.0, alpha=0.45, label=f"正常轨迹 (无声学警告, n={len(normal_lines)})")
            if stat_ref_lines:
                lc_stat = LineCollection(stat_ref_lines, colors="#D97706", linewidths=1.6, alpha=0.80, linestyles="--")
                ax.add_collection(lc_stat)
                ax.plot([], [], color="#D97706", linewidth=1.6, alpha=0.85, linestyle="--", label=f"组内统计偏离参考 (>2.8σ, 无声学警告, n={len(stat_ref_lines)})")
            if warning_lines:
                lc_out = LineCollection(warning_lines, colors="#DC2626", linewidths=2.2, alpha=0.92, linestyles="-")
                ax.add_collection(lc_out)
                ax.plot([], [], color="#DC2626", linewidth=2.2, alpha=0.92, linestyle="-", label=f"声学/切分警告轨迹 (需复核, n={len(warning_lines)})")
            else:
                ax.plot([], [], color="#10B981", linewidth=1.6, linestyle=":", label="声学/切分警告轨迹 (0 项，已全部通过)")

            if max_syls > 1:
                for k in range(1, max_syls):
                    ax.axvline(k * num_points + 0.5, color='gray', linestyle='--', alpha=0.5)

            right_pad = max(4.5, total_pts * 0.28) if pending_qc_tags else 0.8
            ax.set_xlim(0.4, float(total_pts) + right_pad)

            title_main = "数据质量分析：逐项基频曲线质量分布叠加"
            ax.set_title(title_main, fontsize=13, fontweight="bold", pad=16)
            ax._original_title = title_main
            ax.text(
                0.5, 1.015,
                "口径说明：红实线 = 声学提取/切分警告（与 status / fix_warnings 一致）；橙虚线 = 相对同组中位轨迹的个体统计离散参考（非提取报错）",
                transform=ax.transAxes, ha="center", va="bottom", fontsize=8.0, color="#475569",
            )
            ax.set_xlabel("测量点")

            if is_t_value:
                ax.set_ylim(-0.25, 5.35)
                ax.set_yticks([0, 1, 2, 3, 4, 5])
                ax.set_ylabel("T 值 (0-5 标度)")
            else:
                ax.autoscale_view()
                ylo_q, yhi_q = ax.get_ylim()
                ysp_q = max(10.0, float(yhi_q - ylo_q))
                ax.set_ylim(ylo_q - 0.08 * ysp_q, yhi_q + 0.16 * ysp_q)
                ax.set_ylabel("频率 (Hz)")

            if pending_qc_tags:
                ylo_f, yhi_f = ax.get_ylim()
                yspan_f = max(1e-3, float(yhi_f - ylo_f))
                ax_h_pts = 5.4 * 72.0 * 0.78
                tags_sorted = sorted(pending_qc_tags, key=lambda d: d["end_y"])
                pos_y = [d["end_y"] for d in tags_sorted]
                rep_min = ylo_f + 0.06 * yspan_f
                rep_max = yhi_f - 0.24 * yspan_f
                min_gap_y = 0.072 * yspan_f
                n_t = len(pos_y)
                eff_g = min(min_gap_y, max(1e-3, (rep_max - rep_min)) / max(1, n_t))
                for _ in range(40):
                    for k in range(n_t - 1):
                        if pos_y[k + 1] - pos_y[k] < eff_g:
                            mid_v = 0.5 * (pos_y[k] + pos_y[k + 1])
                            pos_y[k] = mid_v - 0.5 * eff_g
                            pos_y[k + 1] = mid_v + 0.5 * eff_g
                    pos_y[0] = max(rep_min, pos_y[0])
                    for k in range(1, n_t):
                        pos_y[k] = max(pos_y[k], pos_y[k - 1] + eff_g)
                    pos_y[-1] = min(rep_max, pos_y[-1])
                    for k in range(n_t - 2, -1, -1):
                        pos_y[k] = min(pos_y[k], pos_y[k + 1] - eff_g)
                pts_per_y = ax_h_pts / yspan_f
                for t_obj, adj_y in zip(tags_sorted, pos_y):
                    dy_pts = float((adj_y - t_obj["end_y"]) * pts_per_y)
                    ax.annotate(
                        t_obj["text"],
                        xy=(t_obj["end_x"], t_obj["end_y"]),
                        xytext=(6.0, dy_pts),
                        textcoords="offset points",
                        fontsize=7.3 if t_obj["bold"] else 7.1,
                        fontweight="bold" if t_obj["bold"] else "normal",
                        color=t_obj["color"],
                        ha="left",
                        va="center",
                        zorder=7,
                        bbox=dict(boxstyle="round,pad=0.14", fc="#FFFFFF", ec=t_obj["ec"], lw=0.55, alpha=0.90),
                        arrowprops=dict(arrowstyle="-", color=t_obj["color"], lw=0.6, alpha=0.6, shrinkA=0, shrinkB=2),
                    )

            legend_kwargs = self._get_legend_kwargs(ax=ax, default_outside=False)
            legend_kwargs["loc"] = "upper right"
            legend_kwargs["fontsize"] = 8.2
            ax.legend(**legend_kwargs)
            return fig

        elif view == "active_ratio":
            fig, ax = plt.subplots(figsize=(8, 5))
            self._draw_active_ratio_panel(ax, data_entries)
            return fig

        elif view == "speaker_means":
            fig, ax = plt.subplots(figsize=(9, 5))
            self._draw_speaker_range_panel(ax, data_entries)
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

        if any("row_index" in item for item in hover_meta):
            row_idx = int(round(y_val))
            col_idx = max(0, int(round(x_val)))
            for item in hover_meta:
                if item.get("row_index") == row_idx:
                    return item, col_idx
            return None, col_idx

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
            score = dist * (0.55 if item.get("is_outlier") or item.get("is_anomaly") else 1.0)
            if score < best_score:
                best_score = score
                best_item = item
        return best_item, max(0, col_idx)


    def locate_qc_item_in_project_tree(self, item_id, speaker_name=None, label=None):
        """
        阶段 A 质检交互闭环：在质检图表中发现异常词条后，直接定位并选中主程序工程树对应词条。
        """
        pt = getattr(self, "project_tree", None)
        if pt is None:
            return False

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
