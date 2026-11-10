# -*- coding: utf-8 -*-
"""
modules.chart_engine.tone_contour_plotters
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
调类单字五度值、变调方阵网格、综合概览热图与共振峰综合热图绘制引擎。
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
from matplotlib.collections import LineCollection

from modules.core.acoustic_math import (
    _safe_nanmean,
    _safe_nanstd,
    _safe_nanmax,
    evaluate_2d_kde_grid,
    fit_chao_tone_code,
)
from modules.data_utils import split_into_syllables


def _safe_sample_nanstd_cols(mat, default=np.nan):
    """Compute column-wise sample standard deviation (ddof=1 when n>=2, else 0.0) ignoring NaNs."""
    a = np.asarray(mat, dtype=float)
    if a.ndim != 2 or a.shape[0] == 0:
        return np.full(a.shape[1] if a.ndim == 2 else 0, default, dtype=float)
    out = np.full(a.shape[1], default, dtype=float)
    for col_idx in range(a.shape[1]):
        col = a[:, col_idx]
        valid = col[np.isfinite(col)]
        if valid.size >= 2:
            out[col_idx] = float(np.std(valid, ddof=1))
        elif valid.size == 1:
            out[col_idx] = 0.0
    return out


class ToneContourPlottersMixin:
    """调类曲线与热图绘制混入类。"""

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
        """Attach right-side Chao 5-level partition dashed lines and secondary axis strictly locked to parent ylim."""
        for deg in (1, 2, 3, 4, 5):
            ax.axhline(float(deg), color="#9CA3AF", linestyle=":", linewidth=0.75, alpha=0.55, zorder=1.5)
        ax._has_chao_axis = True
        ax.yaxis.set_ticks_position("left")
        ax.tick_params(axis="y", which="both", right=False, labelright=False)

        ax_right = ax.twinx()
        ax_right._is_chao_axis = True
        ax_right._parent_ax = ax
        ax._chao_axis = ax_right
        ax_right.yaxis.set_ticks_position("right")
        ax_right.set_yticks([0.5, 1.5, 2.5, 3.5, 4.5])
        ax_right.set_yticklabels(["1度 [0-1]", "2度 [1-2]", "3度 [2-3]", "4度 [3-4]", "5度 [4-5]"], fontsize=8)
        ax_right.set_ylabel("赵元任五度标尺", fontsize=9)
        ax_right.tick_params(axis="y", which="both", direction="in", left=False, right=True, labelleft=False, labelright=True)
        ax_right.autoscale(enable=False, axis="y")
        ax_right.set_autoscale_on(False)
        ax_right.set_ylim(ax.get_ylim())
        ax_right.grid(False)

        def _on_parent_ylim_changed(parent_axis):
            try:
                p_ylim = parent_axis.get_ylim()
                if ax_right.get_ylim() != p_ylim:
                    ax_right.set_ylim(p_ylim)
            except Exception:
                pass

        try:
            ax._chao_ylim_cid = ax.callbacks.connect("ylim_changed", _on_parent_ylim_changed)
        except Exception:
            pass
        return ax_right


    @staticmethod
    def _repel_1d_positions(y_vals, y_min, y_max, min_gap):
        """
        Iterative 1D anti-collision solver for end-of-line labels:
        preserves vertical order and enforces `y_adj[i+1] - y_adj[i] >= min_gap` within `[y_min, y_max]`.
        """
        n = len(y_vals)
        if n <= 1:
            return [float(np.clip(v, y_min, y_max)) for v in y_vals]
        span = max(1e-6, float(y_max - y_min))
        eff_gap = min(float(min_gap), span / max(1.0, float(n)))
        order = sorted(range(n), key=lambda i: float(y_vals[i]))
        pos = [float(np.clip(y_vals[i], y_min, y_max)) for i in order]

        for _ in range(40):
            changed = False
            for k in range(n - 1):
                diff = pos[k + 1] - pos[k]
                if diff < eff_gap - 1e-6:
                    push = 0.5 * (eff_gap - diff)
                    pos[k] -= push
                    pos[k + 1] += push
                    changed = True
            # Clamp boundaries gently from bottom and top
            if pos[0] < y_min:
                pos[0] = y_min
                for k in range(1, n):
                    if pos[k] - pos[k - 1] < eff_gap:
                        pos[k] = pos[k - 1] + eff_gap
            if pos[-1] > y_max:
                pos[-1] = y_max
                for k in range(n - 2, -1, -1):
                    if pos[k + 1] - pos[k] < eff_gap:
                        pos[k] = pos[k + 1] - eff_gap
            if not changed:
                break

        out = [0.0] * n
        for rank, orig_idx in enumerate(order):
            out[orig_idx] = float(np.clip(pos[rank], y_min, y_max))
        return out


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

        def _entry_facet_group(e):
            if group_key != "group":
                return self._entry_wordlist_group(e)
            return e.get('group', '未分组')

        facet_keys = ["Default"]
        if facet_mode == "group":
            facet_keys = self._ordered_unique(_entry_facet_group(e) for e in data_entries)
        elif facet_mode == "syllable_position":
            facet_keys = [f"第 {k+1} 音节" for k in range(max_syls)]
        elif facet_mode == "speaker":
            facet_keys = self._ordered_unique(e.get('speaker_name', '默认发音人') for e in data_entries)
        elif facet_mode == "label":
            facet_keys = self._ordered_unique(e.get('label', '未命名') for e in data_entries)

        n_facets = max(1, len(facet_keys))
        n_cols = min(2, n_facets)
        n_rows = math.ceil(n_facets / n_cols)

        # Shared series across facets when faceting by speaker or syllable_position
        shared_series_across_facets = (n_facets > 1) and (facet_mode in ("speaker", "syllable_position"))
        global_series_order = self._ordered_unique(
            e.get(group_key, e.get('group', '未分组')) for e in data_entries
        )
        global_style_map = {
            s_name: self._get_series_style(idx) for idx, s_name in enumerate(global_series_order)
        }

        # Extra width when multi-facet has per-facet outside legends (e.g. groupby=label facet=group)
        distinct_per_facet_legends = (n_facets > 1) and (facet_mode == "group") and (group_key != "group")
        fig_w = (5.8 * n_cols + 2.4) if distinct_per_facet_legends else (5.3 * n_cols + 2.0)
        fig_h = 4.4 * n_rows + 0.6

        fig, axes = plt.subplots(n_rows, n_cols, figsize=(fig_w, fig_h), squeeze=False, sharex=True, sharey=True)
        axes_flat = axes.flatten()
        chao_codes_map = {}
        shared_legend_codes: Dict[str, List[str]] = {s_name: [] for s_name in global_series_order}
        shared_legend_handles: Dict[str, Any] = {}
        global_xmin_list: List[float] = []
        global_xmax_list: List[float] = []
        global_right_pad_frac: float = 0.06
        global_band_ymins: List[float] = []
        global_band_ymaxs: List[float] = []

        for f_idx, f_key in enumerate(facet_keys):
            ax = axes_flat[f_idx]
            ax.set_facecolor("#FFFFFF")
            ax.grid(True, linestyle="--", alpha=0.25)
            if err_label:
                ax._error_band_label = err_label

            facet_entries = data_entries
            if facet_mode == "group":
                facet_entries = [e for e in data_entries if _entry_facet_group(e) == f_key]
            elif facet_mode == "speaker":
                facet_entries = [e for e in data_entries if e.get('speaker_name') == f_key]
            elif facet_mode == "label":
                facet_entries = [e for e in data_entries if e.get('label') == f_key]

            facet_grouped = {}
            for entry in facet_entries:
                val = entry.get(group_key, entry.get('group', '未分组'))
                facet_grouped.setdefault(val, []).append(entry)
            if shared_series_across_facets:
                ordered_fg = {k: facet_grouped[k] for k in global_series_order if k in facet_grouped}
                for k, v in facet_grouped.items():
                    if k not in ordered_fg:
                        ordered_fg[k] = v
                facet_grouped = ordered_fg

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
                        xmin_k, xmax_k = float(np.min(kx)), float(np.max(kx))
                        ymin_k = -0.1 if "T 值" in scale else max(50.0, float(np.min(ky)) - 15.0)
                        ymax_k = 5.1 if "T 值" in scale else (float(np.max(ky)) + 15.0)
                        xi, yi = np.mgrid[xmin_k:xmax_k:60j, ymin_k:ymax_k:50j]
                        zi = evaluate_2d_kde_grid(kx, ky, xi, yi, bw_method=0.22)
                        if np.nanmax(zi) > 0:
                            ax.contourf(xi, yi, zi, levels=16, cmap="YlOrRd", alpha=0.22, zorder=1)
                    except Exception:
                        pass

            # Track physical syllable boundaries per syllable transition index & per curve
            phys_boundaries_by_syl: Dict[int, List[Tuple[float, float, str]]] = {}
            tail_annotations: List[Tuple[float, float, str, str]] = []
            all_plotted_xs: List[float] = []
            all_plotted_ys: List[float] = []
            single_curve_t_code = ""

            g_color_idx = -1
            for g_color_idx, (g_name, entries) in enumerate(facet_grouped.items()):
                style = (
                    global_style_map.get(g_name, self._get_series_style(g_color_idx))
                    if shared_series_across_facets
                    else self._get_series_style(g_color_idx)
                )
                color = style["color"]
                ls = style["linestyle"]
                marker = style["marker"]
                lw = style["linewidth"]
                ms = style["markersize"]

                syl_indices = [f_idx] if facet_mode == "syllable_position" else list(range(max_syls))
                group_acc_dur = 0.0
                syl_mean_contours_t = []
                plotted_segments = []
                curve_boundaries_local = []

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
                    mean_y = _safe_nanmean(aligned_ys, axis=0)
                    mean_t = _safe_nanmean(aligned_ts, axis=0) if aligned_ts else mean_y

                    if x_axis == "归一化采样点":
                        grid_x = np.linspace(s_idx * num_points + 1, (s_idx + 1) * num_points, num_points)
                    else:
                        if s_idx > 0 and facet_mode != "syllable_position" and group_acc_dur > 0:
                            # Record exact syllable boundary X and interpolated boundary Y for this curve
                            prev_seg = plotted_segments[-1] if plotted_segments else None
                            prev_fin = np.where(np.isfinite(prev_seg[1]))[0] if prev_seg is not None else []
                            cur_fin = np.where(np.isfinite(mean_y))[0]
                            if len(prev_fin) > 0 and len(cur_fin) > 0:
                                y_b = 0.5 * (float(prev_seg[1][prev_fin[-1]]) + float(mean_y[cur_fin[0]]))
                            elif len(cur_fin) > 0:
                                y_b = float(mean_y[cur_fin[0]])
                            elif len(prev_fin) > 0:
                                y_b = float(prev_seg[1][prev_fin[-1]])
                            else:
                                y_b = np.nan
                            curve_boundaries_local.append((s_idx, float(group_acc_dur), y_b, color))
                        grid_x = np.linspace(group_acc_dur, group_acc_dur + mean_s_dur, num_points)
                        group_acc_dur += mean_s_dur

                    syl_mean_contours_t.append(mean_t)
                    err_half = self._compute_error_band_halfwidth(aligned_ys, mode_text=content)

                    if show_spaghetti:
                        segments = [np.column_stack((grid_x, cy)) for cy in aligned_ys]
                        lc = LineCollection(segments, colors=color, linewidths=0.65, alpha=0.20, zorder=2)
                        ax.add_collection(lc)
                    if any(k in content for k in ("置信区间", "CI", "SE", "SD", "标准误", "标准差")):
                        lo_b = mean_y - err_half
                        hi_b = mean_y + err_half
                        ax.fill_between(grid_x, lo_b, hi_b, color=color, alpha=0.15, zorder=3)
                        fin_lo = lo_b[np.isfinite(lo_b)]
                        fin_hi = hi_b[np.isfinite(hi_b)]
                        if fin_lo.size > 0:
                            global_band_ymins.append(float(np.min(fin_lo)))
                        if fin_hi.size > 0:
                            global_band_ymaxs.append(float(np.max(fin_hi)))

                    plotted_segments.append((grid_x, mean_y))
                    all_plotted_xs.extend(grid_x.tolist())
                    fin_my = mean_y[np.isfinite(mean_y)]
                    if fin_my.size > 0:
                        all_plotted_ys.extend(fin_my.tolist())

                if not plotted_segments:
                    continue

                for s_idx_b, bx_val, yb_val, b_col in curve_boundaries_local:
                    phys_boundaries_by_syl.setdefault(s_idx_b, []).append((bx_val, yb_val, b_col))

                t_code = self._fit_contour_chao_code(syl_mean_contours_t) if "T 值" in scale else ""
                if t_code:
                    chao_codes_map[str(g_name)] = t_code
                    single_curve_t_code = t_code
                    if g_name in shared_legend_codes:
                        tag = f"{f_key}{t_code}" if facet_mode == "speaker" else t_code
                        shared_legend_codes[g_name].append((str(f_key), t_code))

                short_g_name = str(g_name)
                if len(short_g_name) > 14:
                    short_g_name = short_g_name[:12] + ".."
                if show_chao_axis and t_code:
                    legend_lbl = f"{short_g_name} [{t_code}]"
                else:
                    legend_lbl = short_g_name

                first_line_handle = None
                for seg_i, (grid_x, mean_y) in enumerate(plotted_segments):
                    lbl = legend_lbl if seg_i == 0 else None
                    lines_drawn = ax.plot(
                        grid_x, mean_y,
                        linestyle=ls, marker=marker, color=color,
                        linewidth=lw, markersize=ms, label=lbl, zorder=5,
                    )
                    if seg_i == 0 and lines_drawn:
                        first_line_handle = lines_drawn[0]

                if first_line_handle is not None and g_name not in shared_legend_handles:
                    shared_legend_handles[g_name] = first_line_handle

                if show_chao_axis and t_code and plotted_segments and len(facet_grouped) <= 6:
                    last_gx, last_my = plotted_segments[-1]
                    finite_idx = np.where(np.isfinite(last_my))[0]
                    if len(finite_idx) > 0:
                        tail_i = int(finite_idx[-1])
                        tail_annotations.append(
                            (float(last_gx[tail_i]), float(last_my[tail_i]), f"[{t_code}]", color)
                        )

            # Record X limits across all facets so sharex=True never truncates earlier longer facets (Exp 2 Chart 04)!
            if all_plotted_xs:
                xmin_d, xmax_d = float(min(all_plotted_xs)), float(max(all_plotted_xs))
                global_xmin_list.append(xmin_d)
                global_xmax_list.append(xmax_d)
                if tail_annotations:
                    max_lbl_len = max((len(str(t[2])) for t in tail_annotations), default=4)
                    right_pad_frac = 0.14 + 0.024 * max(0, max_lbl_len - 4) + (0.045 if n_cols >= 2 else 0.0)
                else:
                    right_pad_frac = 0.055
                global_right_pad_frac = max(global_right_pad_frac, right_pad_frac)
                xspan_d = max(1e-3, xmax_d - xmin_d)
                ax.set_xlim(xmin_d - 0.045 * xspan_d, xmax_d + right_pad_frac * xspan_d)

            if "T 值" in scale:
                t_ylo = min(-0.35, max(-0.75, min(global_band_ymins) - 0.10)) if global_band_ymins else -0.32
                t_yhi = max(5.35, min(5.75, max(global_band_ymaxs) + 0.10)) if global_band_ymaxs else 5.32
                ax.set_ylim(t_ylo, t_yhi)
                ax.set_yticks([0, 1, 2, 3, 4, 5])
                y_span_curr = float(t_yhi - t_ylo)
                y_rep_min, y_rep_max, min_y_gap = 0.05, 4.95, 0.32
            else:
                y_lo, y_hi = ax.get_ylim()
                y_span_curr = max(10.0, float(y_hi - y_lo))
                y_rep_min, y_rep_max, min_y_gap = y_lo + 0.04 * y_span_curr, y_hi - 0.04 * y_span_curr, 0.06 * y_span_curr

            # Render repelled end-of-line [xxx] labels in the right margin
            if tail_annotations:
                raw_ys = [t[1] for t in tail_annotations]
                adj_ys = self._repel_1d_positions(raw_ys, y_rep_min, y_rep_max, min_y_gap)
                approx_ax_h_pts = max(140.0, 4.4 * 72.0 * 0.76)
                pts_per_y = approx_ax_h_pts / max(1e-6, y_span_curr)
                for (tx, ty, t_txt, t_col), ty_adj in zip(tail_annotations, adj_ys):
                    dy_pts = float((ty_adj - ty) * pts_per_y)
                    use_leader = abs(dy_pts) > 4.5
                    ax.annotate(
                        t_txt,
                        xy=(tx, ty),
                        xytext=(7.0, dy_pts),
                        textcoords="offset points",
                        fontsize=7.8,
                        fontweight="bold",
                        color=t_col,
                        va="center",
                        ha="left",
                        zorder=7,
                        bbox=dict(boxstyle="round,pad=0.12", fc="#FFFFFF", ec="none", alpha=0.84),
                        arrowprops=(
                            dict(arrowstyle="-", color=t_col, lw=0.65, alpha=0.55, shrinkA=0, shrinkB=2)
                            if use_leader else None
                        ),
                    )

            is_single_redundant_curve = (
                n_facets > 1
                and len(facet_grouped) == 1
                and (facet_mode == group_key or str(next(iter(facet_grouped.keys()))) == str(f_key))
            )

            title_text = "声调声学格局连贯图"
            if facet_mode in ("group", "syllable_position", "speaker", "label"):
                title_text = str(f_key)
                if is_single_redundant_curve and show_chao_axis and single_curve_t_code:
                    title_text = f"{f_key} [{single_curve_t_code}]"
            else:
                if len(set(e['speaker_name'] for e in data_entries)) == 1:
                    title_text = f"{data_entries[0]['speaker_name']} - 声学格局连贯图"
            if len(title_text) > 24:
                title_text = title_text[:21] + "..."
            ax.set_title(title_text, fontsize=11, fontweight="bold")
            ax._original_title = title_text

            row_idx = f_idx // n_cols
            col_idx = f_idx % n_cols

            if col_idx == 0:
                ax.set_ylabel("T 值 (0-5 标度)" if "T 值" in scale else "频率 (Hz)")
            else:
                ax.set_ylabel("")

            is_rightmost_col = (col_idx == n_cols - 1) or (f_idx == n_facets - 1)
            if show_chao_axis and is_rightmost_col:
                self._attach_chao_five_level_axis(ax)

            is_bottom_row = (row_idx == n_rows - 1) or (f_idx + n_cols >= n_facets)
            if is_bottom_row:
                ax.set_xlabel("音节测量点 (时序展开)" if x_axis == "归一化采样点" else "时长 Duration (s)")
            else:
                ax.set_xlabel("")

            if max_syls > 1 and facet_mode != "syllable_position":
                if x_axis == "归一化采样点":
                    for k in range(1, max_syls):
                        ax.axvline(k * num_points + 0.5, color='gray', linestyle='--', alpha=0.55)
                elif phys_boundaries_by_syl:
                    for _s_i, b_items in sorted(phys_boundaries_by_syl.items()):
                        b_xs = [it[0] for it in b_items]
                        if len(b_items) <= 1:
                            ax.axvline(float(b_xs[0]), color='gray', linestyle='--', alpha=0.55)
                        else:
                            # Draw a single clean mean syllable boundary line + per-curve colored boundary ticks
                            mean_bx = float(np.mean(b_xs))
                            ax.axvline(mean_bx, color='#64748B', linestyle='--', linewidth=1.1, alpha=0.60, zorder=2.5)
                            for bx_v, yb_v, b_col in b_items:
                                if np.isfinite(yb_v):
                                    ax.plot(
                                        [bx_v], [yb_v],
                                        marker='|', color=b_col,
                                        markersize=9.5, markeredgewidth=1.8, zorder=6,
                                    )

            # Legend placement rules:
            # 1) Never draw redundant 1-item legend inside single-curve facets (Exp 2 Chart 02/04)
            # 2) Never split inner/outer legends across shared-series facets (handled once globally below)
            # 3) Default outside right when Chao axis is shown or >= 4 curves exist (Exp 1 Chart 04)
            if g_color_idx >= 0 and not is_single_redundant_curve and not shared_series_across_facets:
                prefer_outside = bool(show_chao_axis or len(facet_grouped) >= 4)
                legend_kwargs = self._get_legend_kwargs(ax=ax, default_outside=prefer_outside)
                legend_kwargs["fontsize"] = 8
                ax.legend(**legend_kwargs)

        # Attach ONE unified outside-right legend for multi-facet shared-series plots (e.g. facet=speaker)
        if shared_series_across_facets and shared_legend_handles:
            top_right_idx = min(n_cols - 1, n_facets - 1)
            anchor_ax = axes_flat[top_right_idx]
            u_handles = []
            u_labels = []
            for s_name in global_series_order:
                if s_name not in shared_legend_handles:
                    continue
                u_handles.append(shared_legend_handles[s_name])
                short_s = s_name if len(str(s_name)) <= 14 else (str(s_name)[:12] + "..")
                code_pairs = shared_legend_codes.get(s_name, [])
                if show_chao_axis and code_pairs:
                    uniq_codes = self._ordered_unique(c for _, c in code_pairs)
                    if len(uniq_codes) == 1:
                        u_labels.append(f"{short_s} [{uniq_codes[0]}]")
                    elif facet_mode == "speaker" and len(code_pairs) <= 3:
                        pair_str = "/".join(f"{spk}:{cd}" for spk, cd in code_pairs)
                        u_labels.append(f"{short_s} [{pair_str}]")
                    else:
                        u_labels.append(short_s)
                else:
                    u_labels.append(short_s)
            legend_kwargs = self._get_legend_kwargs(ax=anchor_ax, default_outside=True)
            legend_kwargs["fontsize"] = 8
            anchor_ax.legend(u_handles, u_labels, **legend_kwargs)

        # Apply unified global X/Y envelope across shared-axis facets so no subplot gets truncated!
        if global_xmin_list and global_xmax_list and n_facets >= 1:
            g_xmin = float(min(global_xmin_list))
            g_xmax = float(max(global_xmax_list))
            g_xspan = max(1e-3, g_xmax - g_xmin)
            axes_flat[0].set_xlim(g_xmin - 0.045 * g_xspan, g_xmax + global_right_pad_frac * g_xspan)
        if "T 值" in scale and n_facets >= 1:
            t_ylo_f = min(-0.35, max(-0.75, min(global_band_ymins) - 0.10)) if global_band_ymins else -0.32
            t_yhi_f = max(5.35, min(5.75, max(global_band_ymaxs) + 0.10)) if global_band_ymaxs else 5.32
            axes_flat[0].set_ylim(t_ylo_f, t_yhi_f)

        for idx in range(n_facets, len(axes_flat)):
            axes_flat[idx].set_visible(False)

        fig._chao_tone_codes = chao_codes_map
        fig.tight_layout()
        if distinct_per_facet_legends and n_cols > 1:
            fig.subplots_adjust(wspace=0.42)
            setattr(fig, "_phontracer_skip_tight_layout", True)
        elif self.get_param('legend_outside', bool(show_chao_axis or shared_series_across_facets)):
            loc_val = str(self.get_param('legend_loc', '右上') or '右上')
            if "右" in loc_val:
                fig.subplots_adjust(right=0.72 if show_chao_axis else 0.80)
            elif "left" in loc_val or "左" in loc_val:
                fig.subplots_adjust(left=0.18)
        return fig


    def _plot_tone_sandhi_grid(self, data_entries, group_key="group", scale="T 值"):
        """
        声调 2：连读变调与协同发音图
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
            figsize=(5.6 * n_cols + 1.8, 4.3 * n_rows + 0.6),
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
            tail_items = []
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
                    tail_items.append((float(grid_x[ti]), float(mean_y[ti]), f"[{t_code}]", color))

            ax.set_xlim(0.2, float(total_points) + (4.2 if tail_items else 1.0))
            if "T 值" in scale:
                ax.set_ylim(-0.32, 5.32)
                ax.set_yticks([0, 1, 2, 3, 4, 5])
                for deg in (1, 2, 3, 4, 5):
                    ax.axhline(float(deg), color="#CBD5E1", linestyle=":", linewidth=0.65, alpha=0.5)
                y_span_s = 5.4
                adj_ys = self._repel_1d_positions([it[1] for it in tail_items], 0.05, 4.95, 0.28)
            else:
                ylo_s, yhi_s = ax.get_ylim()
                y_span_s = max(10.0, float(yhi_s - ylo_s))
                adj_ys = self._repel_1d_positions(
                    [it[1] for it in tail_items],
                    ylo_s + 0.04 * y_span_s,
                    yhi_s - 0.04 * y_span_s,
                    0.06 * y_span_s,
                )

            pts_per_y = (4.2 * 72.0 * 0.76) / max(1e-6, y_span_s)
            for (tx, ty, t_txt, t_col), ty_adj in zip(tail_items, adj_ys):
                dy_pts = float((ty_adj - ty) * pts_per_y)
                ax.annotate(
                    t_txt,
                    xy=(tx, ty),
                    xytext=(6.0, dy_pts),
                    textcoords="offset points",
                    fontsize=7.5,
                    fontweight="bold",
                    color=t_col,
                    va="center",
                    ha="left",
                    bbox=dict(boxstyle="round,pad=0.1", fc="#FFFFFF", ec="none", alpha=0.82),
                )

            ax.axvline(num_points + 0.5, color="#475569", linestyle="--", linewidth=1.2, alpha=0.75, zorder=3)

            sub_title = f"{'后字固定' if by_back else '前字固定'}: {p_key}"
            ax.set_title(sub_title, fontsize=11, fontweight="bold")
            ax._original_title = sub_title

            if f_idx % n_cols == 0:
                ax.set_ylabel("T 值 (0-5 标度)" if "T 值" in scale else "频率 (Hz)")
            if (f_idx // n_cols == n_rows - 1) or (f_idx + n_cols >= n_facets):
                ax.set_xticks([1, (num_points + 1) // 2, num_points + 0.5, num_points + (num_points + 1) // 2, total_points])
                ax.set_xticklabels(["前字始", "前字中", "音节界", "后字中", "后字末"], fontsize=8.2)
                ax.set_xlabel("二字组连读归一化采样点 (虚线为音节分界)")

            handles, labels = ax.get_legend_handles_labels()
            if handles:
                legend_kwargs = self._get_legend_kwargs(ax=ax, default_outside=True)
                legend_kwargs["fontsize"] = 7.5
                ax.legend(**legend_kwargs)

        for idx in range(n_facets, len(axes_flat)):
            axes_flat[idx].set_visible(False)

        fig._sandhi_grid_summary = sandhi_summary
        fig.suptitle("二字组连读变调与协同发音分面网格图", fontsize=13, fontweight="bold")
        fig.tight_layout()
        if n_cols > 1:
            fig.subplots_adjust(wspace=0.38)
            setattr(fig, "_phontracer_skip_tight_layout", True)
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

        # In QC / deviation inspection mode, keep per-token rows so individual speaker anomalies can be clicked;
        # in publication Mean / SD overview heatmaps (`groupby=label`), aggregate across all speakers per `(group, label)`!
        per_token_qc = bool(show_deviation or sort_by_anomaly)
        grouped_data = {}
        for entry in data_entries:
            base_grp = self._entry_wordlist_group(entry)
            if group_key == 'label':
                if per_token_qc:
                    val = (base_grp, entry['label'], entry.get('speaker_name', ''), entry.get('item_id', ''))
                else:
                    val = (base_grp, entry['label'])
            else:
                val = entry.get(group_key, base_grp)
            grouped_data.setdefault(val, []).append(entry)

        if group_key == 'label':
            if per_token_qc:
                groups_sorted = list(grouped_data.keys())
            else:
                present_groups = self._ordered_unique(self._entry_wordlist_group(e) for e in data_entries)
                preferred_order = list(getattr(self, 'available_groups', []) or [])
                grp_order_list = [g for g in preferred_order if g in present_groups]
                for g in present_groups:
                    if g not in grp_order_list:
                        grp_order_list.append(g)
                grp_rank = {g: i for i, g in enumerate(grp_order_list)}
                groups_sorted = sorted(
                    list(grouped_data.keys()),
                    key=lambda k: (grp_rank.get(k[0], 999999), list(grouped_data.keys()).index(k)),
                )
        else:
            if group_key == 'group':
                self._ensure_available_groups()
                group_order = self.available_groups
            else:
                group_order = self._ordered_unique(entry.get(group_key, self._entry_wordlist_group(entry)) for entry in data_entries)
            row_order = {value: idx for idx, value in enumerate(group_order)}
            groups_sorted = sorted(list(grouped_data.keys()), key=lambda value: row_order.get(value, 999999))

        if not groups_sorted:
            fig, ax = plt.subplots(figsize=(6, 4))
            ax.text(0.5, 0.5, "没有找到有效的声调数据用于生成概览图", ha='center', va='center')
            return fig

        # Pre-calculate group-level mean and SD contours for deviation / Z-score calculation
        tone_vectors_by_group = {}
        for entry in data_entries:
            base_grp = self._entry_wordlist_group(entry)
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
            tone_vectors_by_group.setdefault(base_grp, []).append(arr)

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
                if per_token_qc:
                    tg, label_name, spk_name, item_id = g_key
                else:
                    tg, label_name = g_key
                    spk_name = ", ".join(self._ordered_unique(e.get('speaker_name', '') for e in entries if e.get('speaker_name')))
                    item_id = str(entries[0].get('item_id', ''))
            else:
                tg = self._entry_wordlist_group(entries[0])
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
                plot_row = _safe_sample_nanstd_cols(raw_mat, default=np.nan)

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
                max_sd = _safe_nanmax(matrix, default=0.0)
                if not np.isfinite(max_sd) or max_sd <= 1e-6:
                    vmax = 0.5 if "T 值" in scale else 10.0
                else:
                    vmax = float(max_sd) * 1.05

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

        if max_syls <= 1:
            tick_pos = [0, num_points // 2, max(0, num_points - 1)] if num_points >= 3 else list(range(num_points))
            tick_lbls = [
                f"点{p + 1} ({int(round(100.0 * p / max(1, num_points - 1)))}%)"
                for p in tick_pos
            ]
            ax.set_xticks(tick_pos)
            ax.set_xticklabels(tick_lbls, rotation=0, ha='center', fontsize=8.5)
        else:
            tick_pos = []
            tick_lbls = []
            for s_idx in range(max_syls):
                syl_tag = "前字" if (max_syls == 2 and s_idx == 0) else ("后字" if (max_syls == 2 and s_idx == 1) else f"音节{s_idx + 1}")
                cand_pts = [0, num_points // 2, num_points - 1] if s_idx == 0 else [num_points // 2, num_points - 1]
                for p_idx in cand_pts:
                    col_i = s_idx * num_points + p_idx
                    pos_name = "始" if p_idx == 0 else ("中" if p_idx == num_points // 2 else "末")
                    tick_pos.append(col_i)
                    tick_lbls.append(f"{syl_tag}{pos_name} (点{p_idx + 1})")
            ax.set_xticks(tick_pos)
            ax.set_xticklabels(tick_lbls, rotation=30, ha='right', fontsize=8.2)

        if max_syls > 1:
            for k in range(1, max_syls):
                ax.axvline(k * num_points - 0.5, color='#334155', linestyle='--', linewidth=1.4, alpha=0.88)

        title_text = f"声调组别概览图 - {'偏差质检热图' if show_deviation else metric}"
        unique_entry_groups = set(self._entry_wordlist_group(e) for e in data_entries)
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

        canonical_tone_order = [
            "阴平", "阳平", "上声", "去声", "入声",
            "阴上", "阳上", "阴去", "阳去", "阴入", "阳入",
            "1_阴", "2_阳", "3_上", "4_去",
        ]
        canon_rank = {t: i for i, t in enumerate(canonical_tone_order)}

        raw_front_tones = self._ordered_unique(row["front"] for row in rows)
        raw_back_tones = self._ordered_unique(row["back"] for row in rows)
        front_tones = sorted(raw_front_tones, key=lambda t: (canon_rank.get(t, 999), raw_front_tones.index(t)))
        back_tones = sorted(raw_back_tones, key=lambda t: (canon_rank.get(t, 999), raw_back_tones.index(t)))

        if not front_tones or not back_tones:
            return self._tone_effect_blank("没有可识别的前后调类。")

        grouped = {}
        for row in rows:
            key = (row["front"], row["back"], row["syl"])
            grouped.setdefault(key, []).append(row["value"])

        front_index = {tone: index for index, tone in enumerate(front_tones)}
        back_index = {tone: index for index, tone in enumerate(back_tones)}
        front_matrix = np.full((len(front_tones), len(back_tones)), np.nan)
        back_matrix = np.full((len(front_tones), len(back_tones)), np.nan)
        for key, values in grouped.items():
            front, back, syl = key
            if front not in front_index or back not in back_index:
                continue
            matrix = front_matrix if syl == 1 else back_matrix
            matrix[front_index[front], back_index[back]] = float(sum(values) / len(values))

        fig_h = max(3.8, 1.15 * len(front_tones) + 1.8)
        fig_w = max(11.5, 2.5 * len(back_tones) + 3.2)
        fig, axes = plt.subplots(1, 2, figsize=(fig_w, fig_h))
        try:
            cmap = plt.colormaps.get_cmap("RdYlBu_r").copy()
        except AttributeError:
            cmap = plt.cm.get_cmap("RdYlBu_r").copy()
        cmap.set_bad(color="#F1F5F9", alpha=1.0)

        for axis, matrix, title in [
            (axes[0], front_matrix, "前字平均五度值"),
            (axes[1], back_matrix, "后字平均五度值"),
        ]:
            axis._is_matrix_heatmap = True
            im = axis.imshow(matrix, vmin=0.0, vmax=5.0, cmap=cmap, aspect="equal")
            axis.set_xticks(range(len(back_tones)))
            axis.set_yticks(range(len(front_tones)))
            axis.set_xticklabels(back_tones)
            axis.set_yticklabels(front_tones)
            axis.set_xlabel("后字调类")
            axis.set_ylabel("前字调类")
            axis.set_title(title, fontsize=12, fontweight="bold")
            axis._original_title = title
            for row_idx in range(matrix.shape[0]):
                for col_idx in range(matrix.shape[1]):
                    value = matrix[row_idx, col_idx]
                    if np.isfinite(value):
                        txt_c = "white" if (value <= 0.75 or value >= 4.35) else "#111827"
                        axis.text(col_idx, row_idx, f"{value:.2f}", ha="center", va="center", fontsize=9.5, fontweight="bold", color=txt_c)
                    else:
                        axis.text(col_idx, row_idx, "未测试", ha="center", va="center", fontsize=8.5, color="#94A3B8")
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

        per_token_qc = bool(show_deviation or sort_by_anomaly)
        grouped_data = {}
        for entry in data_entries:
            base_grp = self._entry_wordlist_group(entry)
            if group_key == 'label':
                val = (base_grp, entry['label'], entry.get('speaker_name', ''), entry.get('item_id', '')) if per_token_qc else (base_grp, entry['label'])
            else:
                val = entry.get(group_key, base_grp)
            grouped_data.setdefault(val, []).append(entry)

        if group_key == 'label':
            if per_token_qc:
                groups_sorted = list(grouped_data.keys())
            else:
                present_groups = self._ordered_unique(self._entry_wordlist_group(e) for e in data_entries)
                preferred_order = list(getattr(self, 'available_groups', []) or [])
                grp_order_list = [g for g in preferred_order if g in present_groups]
                for g in present_groups:
                    if g not in grp_order_list:
                        grp_order_list.append(g)
                grp_rank = {g: i for i, g in enumerate(grp_order_list)}
                groups_sorted = sorted(
                    list(grouped_data.keys()),
                    key=lambda k: (grp_rank.get(k[0], 999999), list(grouped_data.keys()).index(k)),
                )
        else:
            if group_key == 'group':
                self._ensure_available_groups()
                group_order = self.available_groups
            else:
                group_order = self._ordered_unique(entry.get(group_key, self._entry_wordlist_group(entry)) for entry in data_entries)
            row_order = {value: idx for idx, value in enumerate(group_order)}
            groups_sorted = sorted(list(grouped_data.keys()), key=lambda value: row_order.get(value, 999999))

        if not groups_sorted:
            fig, ax = plt.subplots(figsize=(6, 4))
            ax.text(0.5, 0.5, "没有找到有效的共振峰数据用于生成概览图", ha='center', va='center')
            return fig

        normalization_mode = (
            self._resolve_formant_normalization()
            if hasattr(self, "_resolve_formant_normalization")
            else str(self.get_param('formant_normalization', '原始频率 (Hz)'))
        )
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

        grp_pool = {}
        for entry in data_entries:
            base_grp = self._entry_wordlist_group(entry)
            ef1, ef2, eratio = _extract_entry_tracks(entry)
            grp_pool.setdefault(base_grp, {"f1": [], "f2": [], "ratio": []})
            grp_pool[base_grp]["f1"].append(ef1)
            grp_pool[base_grp]["f2"].append(ef2)
            grp_pool[base_grp]["ratio"].append(eratio)

        grp_ref = {}
        is_z_norm = any(k in normalization_mode.lower() for k in ("lobanov", "归一化", "bark", "erb", "z-score"))
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
                if per_token_qc:
                    tg, label_name, spk_name, item_id = g_name
                else:
                    tg, label_name = g_name
                    spk_name = ", ".join(self._ordered_unique(e.get('speaker_name', '') for e in entries if e.get('speaker_name')))
                    item_id = str(entries[0].get('item_id', ''))
            else:
                tg = self._entry_wordlist_group(entries[0])
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
                row_f1 = _safe_sample_nanstd_cols(vectors_f1, default=np.nan)
                row_f2 = _safe_sample_nanstd_cols(vectors_f2, default=np.nan)
                row_ratio = _safe_sample_nanstd_cols(vectors_ratio, default=np.nan)

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

        norm_low = normalization_mode.lower()
        if "lobanov" in norm_low or "归一化" in normalization_mode or "z-score" in norm_low:
            unit_text = "Lobanov Z"
        elif "bark" in norm_low:
            unit_text = "Bark"
        elif "erb" in norm_low:
            unit_text = "ERB"
        elif "mel" in norm_low:
            unit_text = "Mel"
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
            max_f_sd = max(_safe_nanmax(matrix_f1, default=0.0), _safe_nanmax(matrix_f2, default=0.0))
            vmax_main = (0.5 if unit_text != "Hz" else 20.0) if (not np.isfinite(max_f_sd) or max_f_sd <= 1e-6) else None
            vmin_ratio = 0.0
            max_r_sd = _safe_nanmax(matrix_ratio, default=0.0)
            vmax_ratio = 0.2 if (not np.isfinite(max_r_sd) or max_r_sd <= 1e-6) else None

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
            metric_short = metric.replace("热图", "分布")
            t1 = f"F1 共振峰轨迹 - {'偏差 Z-score' if show_deviation else metric_short}"
            t2 = f"F2 共振峰轨迹 - {'偏差 Z-score' if show_deviation else metric_short}"
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
                    ax.axvline(k * num_points - 0.5, color='#334155', linestyle='--', linewidth=1.35, alpha=0.88)

        if max_syls <= 1:
            tick_pos = [0, num_points // 2, max(0, num_points - 1)] if num_points >= 3 else list(range(num_points))
            tick_lbls = [
                f"点{p + 1} ({int(round(100.0 * p / max(1, num_points - 1)))}%)"
                for p in tick_pos
            ]
            axes_to_label[-1].set_xticks(tick_pos)
            axes_to_label[-1].set_xticklabels(tick_lbls, rotation=0, ha='center', fontsize=8.5)
            axes_to_label[-1].set_xlabel("音节归一化时长采样点", fontsize=9)
        else:
            tick_pos = []
            tick_lbls = []
            for s_idx in range(max_syls):
                syl_tag = "前字" if (max_syls == 2 and s_idx == 0) else ("后字" if (max_syls == 2 and s_idx == 1) else f"音节{s_idx + 1}")
                cand_pts = [0, num_points // 2, num_points - 1] if s_idx == 0 else [num_points // 2, num_points - 1]
                for p_idx in cand_pts:
                    tick_pos.append(s_idx * num_points + p_idx)
                    pos_name = "始" if p_idx == 0 else ("中" if p_idx == num_points // 2 else "末")
                    tick_lbls.append(f"{syl_tag}{pos_name} (点{p_idx + 1})")
            axes_to_label[-1].set_xticks(tick_pos)
            axes_to_label[-1].set_xticklabels(tick_lbls, rotation=30, ha='right', fontsize=8.2)
        if len(axes_to_label) > 1:
            axes_to_label[0].tick_params(axis='x', labelbottom=False)

        title_mode_text = "F2/F1 比值单轨" if is_ratio_mode else "F1 & F2 双轨"
        title_text = f"共振峰组别概览图 - {title_mode_text} - {'偏差质检热图' if show_deviation else metric}"
        unique_entry_groups = set(self._entry_wordlist_group(e) for e in data_entries)
        if len(unique_entry_groups) == 1:
            title_text += f" (组别: {list(unique_entry_groups)[0]})"
        if len(set(e['speaker_name'] for e in data_entries)) == 1:
            title_text = f"{data_entries[0]['speaker_name']} - {title_text}"
        fig.suptitle(title_text, fontsize=12, fontweight="bold", y=0.995)

        fig._qc_hover_metadata = hover_metadata
        fig.tight_layout(rect=[0, 0, 1, 0.98])
        return fig
