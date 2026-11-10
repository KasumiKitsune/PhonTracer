# -*- coding: utf-8 -*-
"""
modules.chart_engine.formant_plotters
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
共振峰元音舌位图、核密度热力图、动态轨迹图与 Pillai 巴特利特矩阵绘制引擎。
完全无头，仅依赖 matplotlib Agg 后端，不依赖任何 GUI 库。
"""
from __future__ import annotations

import math
import re
import warnings
from typing import Any, Dict, List, Optional, Tuple
import numpy as np

import matplotlib
matplotlib.use("Agg", force=True)
import matplotlib.pyplot as plt
from matplotlib.patches import Ellipse, Polygon
from matplotlib.collections import LineCollection
from matplotlib.colors import Normalize, LinearSegmentedColormap

from modules.core.acoustic_math import (
    _safe_nanmean,
    _safe_nanmax,
    _compute_2d_convex_hull_and_area,
    _compute_pillai_bartlett_trace,
    evaluate_2d_kde_grid,
    smooth_2d_array,
)
from modules.data_utils import (
    split_into_syllables,
    get_item_syllable_bounds,
    sample_formant_points_by_bounds,
    sample_formant_points_by_bounds_extended,
)


class FormantPlottersMixin:
    """共振峰分析与可视化混入类。"""

    def _resolve_formant_normalization(self, default: str = "原始频率 (Hz)") -> str:
        """
        统一兼容解析共振峰归一化参数：支持 `formant_normalization`、`normalization`、`norm`、`formant_norm` 与 `scale`，
        识别 `Lobanov` / `lobanov` / `z-score` / `Bark` / `ERB` / `Mel` / `Hz` 等大小写枚举。
        """
        raw = None
        for key in ("formant_normalization", "formant_norm", "normalization", "norm"):
            cand = self.get_param(key, None)
            if cand is not None and str(cand).strip():
                raw = str(cand).strip()
                break
        if raw is None:
            sc = self.get_param("scale", None)
            if sc is not None and str(sc).strip() and str(sc).strip() not in ("t_value", "T 值", "T 值 (五度标调)"):
                raw = str(sc).strip()
        if not raw:
            return default

        low = raw.lower()
        if any(k in low for k in ("lobanov", "z-score", "zscore", "l-归一化")) or "归一化" in raw or low in ("z", "speaker", "per_speaker", "发音人"):
            return "Lobanov (发音人 Z-score)"
        if "bark" in low or "巴克" in raw:
            return "Bark (听觉巴克)"
        if "erb" in low or "等效矩形" in raw:
            return "ERB (等效矩形带宽)"
        if "mel" in low or "美尔" in raw:
            return "Mel (美尔)"
        if low in ("hz", "raw", "原始频率", "原始频率 (hz)") or "hz" in low:
            return "原始频率 (Hz)"
        return raw


    @staticmethod
    def _is_hz_formant_mode(norm_mode: str) -> bool:
        low = str(norm_mode or "").lower()
        return not any(k in low for k in ("lobanov", "z-score", "zscore", "bark", "erb", "mel", "归一化", "巴克", "美尔"))


    @staticmethod
    def _formant_axis_labels(norm_mode: str) -> Tuple[str, str, str]:
        """Return `(xlabel_f2, ylabel_f1, traj_ylabel)` according to `norm_mode`."""
        low = str(norm_mode or "").lower()
        if "bark" in low or "巴克" in str(norm_mode):
            return "F2 (Bark)", "F1 (Bark)", "频率 (Bark)"
        if "erb" in low or "等效矩形" in str(norm_mode):
            return "F2 (ERB)", "F1 (ERB)", "频率 (ERB)"
        if "mel" in low or "美尔" in str(norm_mode):
            return "F2 (Mel)", "F1 (Mel)", "频率 (Mel)"
        if "lobanov" in low or "z-score" in low or "zscore" in low or "归一化" in str(norm_mode):
            return "F2 (Lobanov Z)", "F1 (Lobanov Z)", "频率 (Lobanov Z)"
        return "F2 (Hz)", "F1 (Hz)", "频率 (Hz)"


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
        if low in ("按词语", "按词项", "label", "word") or "词语" in val or "词项" in val:
            return entry['label']
        if low in ("按单字/音节", "按单字音节", "syl_char", "char", "syllable") or "单字" in val or "音节" in val:
            return syl['char']
        if low in ("按发音人", "speaker", "speaker_name") or "发音人" in val:
            return entry['speaker_name']
        if low == "custom_field" or low.startswith("custom") or low.startswith("item_meta") or low.startswith("meta:") or "自定义字段" in val:
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


    def _draw_confidence_ellipse(self, x, y, ax, n_std=1.5152, facecolor='none', center=None, canvas_span=None, **kwargs):
        """
        绘制元音稳态置信椭圆（带小样本特征值正则化与几何包络记录）：
        - 当样本量较少（如 N=3~4 发音人）或三点近似共线导致协方差矩阵病态时，自动对短轴特征值施加各向同性下限正则化
          （杜绝坍缩成一维细针线段）并对长轴施加稳健上限约束（杜绝混杂巨型十字架误差棒）；
        - 同时将椭圆的轴对齐外接矩形记录到 `ax._ellipse_envelopes`，供坐标轴自适应扩展余量，彻底消除椭圆贴边裁切。
        """
        x = np.asarray(x, dtype=float)
        y = np.asarray(y, dtype=float)
        finite = np.isfinite(x) & np.isfinite(y)
        x = x[finite]
        y = y[finite]
        if len(x) < 2:
            return None

        if center is not None and len(center) == 2 and np.isfinite(center[0]) and np.isfinite(center[1]):
            cx, cy = float(center[0]), float(center[1])
        else:
            cx, cy = float(np.mean(x)), float(np.mean(y))

        span_x, span_y = (float(canvas_span[0]), float(canvas_span[1])) if (canvas_span and len(canvas_span) == 2) else (2200.0, 900.0)
        span_x = max(span_x, 1e-3)
        span_y = max(span_y, 1e-3)
        edge_col = kwargs.get("edgecolor", "#374151")
        lw_val = float(kwargs.get("linewidth", 1.4))
        zord = kwargs.get("zorder", 4)
        env_list = getattr(ax, "_ellipse_envelopes", None)
        if env_list is None:
            env_list = []
            ax._ellipse_envelopes = env_list

        def _fallback_crossbars(arr_x, arr_y):
            sx = float(np.std(arr_x, ddof=1)) if len(arr_x) > 1 else 0.0
            sy = float(np.std(arr_y, ddof=1)) if len(arr_y) > 1 else 0.0
            sx_cap = min(sx, 0.16 * span_x)
            sy_cap = min(sy, 0.16 * span_y)
            if sx_cap <= 1e-6 and sy_cap <= 1e-6:
                return None
            env_list.append((cx - sx_cap, cx + sx_cap, cy - sy_cap, cy + sy_cap))
            return ax.errorbar(
                [cx], [cy],
                xerr=[sx_cap] if sx_cap > 1e-6 else None,
                yerr=[sy_cap] if sy_cap > 1e-6 else None,
                fmt="none",
                ecolor=edge_col,
                elinewidth=max(1.05, lw_val * 0.85),
                capsize=3.2,
                alpha=0.70,
                zorder=zord,
            )

        if len(x) < 3:
            return _fallback_crossbars(x, y)

        try:
            if len(x) >= 5:
                med_x = float(np.median(x))
                med_y = float(np.median(y))
                mad_x = float(np.median(np.abs(x - med_x))) * 1.4826
                mad_y = float(np.median(np.abs(y - med_y))) * 1.4826
                sx = max(mad_x, float(np.std(x)) * 0.25, 1e-6)
                sy = max(mad_y, float(np.std(y)) * 0.25, 1e-6)
                inlier = (np.abs(x - med_x) <= 3.0 * sx) & (np.abs(y - med_y) <= 3.0 * sy)
                if np.count_nonzero(inlier) >= 3:
                    x = x[inlier]
                    y = y[inlier]

            is_small_sample = len(x) < 5
            eff_n_std = min(float(n_std), 1.08) if is_small_sample else float(n_std)

            # Normalize into unit canvas coordinates so eigenvalue regularization is isotropic across F1 & F2 scales!
            ux = (x - cx) / span_x
            uy = (y - cy) / span_y
            cov_u = np.cov(ux, uy, ddof=1)
            if np.any(np.isnan(cov_u)) or np.any(np.isinf(cov_u)):
                return _fallback_crossbars(x, y)

            vals_u, vecs_u = np.linalg.eigh(cov_u)
            order = vals_u.argsort()[::-1]
            vals_u, vecs_u = vals_u[order], vecs_u[:, order]

            lam1 = max(float(vals_u[0]), 0.0)
            lam2 = max(float(vals_u[1]), 0.0)

            # Cap extreme single-speaker variance in tiny N<5 samples so ellipses stay readable (~<=20% of canvas radius)
            if is_small_sample:
                max_lam_u = (0.185 / max(eff_n_std, 0.8)) ** 2
                lam1 = min(lam1, max_lam_u)
                lam2 = min(lam2, lam1)
            else:
                max_lam_u = (0.42 / max(eff_n_std, 0.8)) ** 2
                if lam1 > max_lam_u:
                    lam1 = max_lam_u
                    lam2 = min(lam2, lam1)

            # Regularize minor eigenvalue in normalized space so collinear N=3 never collapses into a 1D needle (aspect <= 3.2)
            min_lam_floor = max(0.032 ** 2, 0.10 * lam1)
            lam2 = max(lam2, min_lam_floor)
            lam1 = max(lam1, lam2)

            # Map regularized unit covariance back to data coordinates
            reg_cov_u = vecs_u @ np.diag([lam1, lam2]) @ vecs_u.T
            scale_mat = np.diag([span_x, span_y])
            reg_cov = scale_mat @ reg_cov_u @ scale_mat

            vals_d, vecs_d = np.linalg.eigh(reg_cov)
            ord_d = vals_d.argsort()[::-1]
            vals_d, vecs_d = vals_d[ord_d], vecs_d[:, ord_d]

            theta = float(np.degrees(np.arctan2(*vecs_d[:, 0][::-1])))
            width, height = 2.0 * eff_n_std * np.sqrt(np.maximum(vals_d, 0.0))
            width = float(width)
            height = float(height)

            rad_t = math.radians(theta)
            half_a, half_b = 0.5 * width, 0.5 * height
            bbox_w = 2.0 * math.sqrt((half_a * math.cos(rad_t)) ** 2 + (half_b * math.sin(rad_t)) ** 2)
            bbox_h = 2.0 * math.sqrt((half_a * math.sin(rad_t)) ** 2 + (half_b * math.cos(rad_t)) ** 2)
            env_list.append((cx - 0.5 * bbox_w, cx + 0.5 * bbox_w, cy - 0.5 * bbox_h, cy + 0.5 * bbox_h))

            ellipse = Ellipse(
                xy=(cx, cy), width=width, height=height,
                angle=theta, facecolor=facecolor, **kwargs
            )
            return ax.add_patch(ellipse)
        except Exception:
            return _fallback_crossbars(x, y)


    @staticmethod
    def _place_non_overlapping_vowel_labels(ax, pending_labels, marker_points=None):
        """
        对元音空间图中的词项/组别汉字标签框进行二维排斥防重叠布局，
        并将最终偏移量转换为 `textcoords="offset points"`（既消除文字框互相遮挡，又严格保持 `ann.anncoords == 'offset points'`）。

        pending_labels: list of dicts `{x, y, text, color, fontsize}`
        marker_points: list of `(x, y)` data coordinates of plotted centroids
        """
        if not pending_labels:
            return
        xlim = ax.get_xlim()
        ylim = ax.get_ylim()
        dx_axis = float(xlim[1] - xlim[0])
        dy_axis = float(ylim[1] - ylim[0])
        if abs(dx_axis) < 1e-9 or abs(dy_axis) < 1e-9:
            dx_axis = 1.0
            dy_axis = 1.0

        # Estimate subplot physical width & height in points (1 inch = 72 pt)
        try:
            fig = ax.figure
            fw_in, fh_in = fig.get_size_inches()
            pos = ax.get_position()
            eff_w_frac = min(float(pos.width), 0.68) if float(pos.width) > 0.72 else float(pos.width)
            ax_w_pts = max(180.0, float(fw_in) * eff_w_frac * 72.0)
            ax_h_pts = max(150.0, float(fh_in) * float(pos.height) * 72.0)
        except Exception:
            ax_w_pts, ax_h_pts = 390.0, 340.0

        def _data_to_pt(x_d, y_d):
            u = (float(x_d) - float(xlim[0])) / dx_axis
            v = (float(y_d) - float(ylim[0])) / dy_axis
            return u * ax_w_pts, v * ax_h_pts

        anchors_pt = []
        boxes = []
        for item in pending_labels:
            ax_p, ay_p = _data_to_pt(item["x"], item["y"])
            txt_s = str(item["text"])
            fs = float(item.get("fontsize", 10.0))
            cjk_w = sum(1.06 if ord(ch) > 127 else 0.60 for ch in txt_s)
            hw = max(11.5, 0.54 * cjk_w * fs + 6.5)
            hh = max(9.0, 0.62 * fs + 4.5)
            anchors_pt.append((ax_p, ay_p))
            init_y = ay_p + hh + 7.5
            if init_y > ax_h_pts - hh - 6.0:
                init_y = ay_p - hh - 8.0
            init_x = float(np.clip(ax_p, hw + 6.0, max(hw + 7.0, ax_w_pts - hw - 6.0)))
            boxes.append([init_x, init_y, hw, hh])

        markers_pt = [_data_to_pt(mx, my) for (mx, my) in (marker_points or []) if np.isfinite(mx) and np.isfinite(my)]

        n = len(boxes)
        if n >= 1:
            for _it in range(64):
                moved = False
                # 1. Gentle spring pull ONLY in early iterations so final pass settles strictly collision-free!
                if _it < 42:
                    for i in range(n):
                        ax_p, ay_p = anchors_pt[i]
                        xi, yi, hwi, hhi = boxes[i]
                        target_y = ay_p + hhi + 7.5 if ay_p + 2 * hhi + 10.0 <= ax_h_pts else (ay_p - hhi - 8.0)
                        boxes[i][0] = 0.94 * xi + 0.06 * ax_p
                        boxes[i][1] = 0.94 * yi + 0.06 * target_y

                # 2. Pairwise label-box repulsion
                for i in range(n):
                    for j in range(i + 1, n):
                        xi, yi, hwi, hhi = boxes[i]
                        xj, yj, hwj, hhj = boxes[j]
                        min_dx = hwi + hwj + 4.5
                        min_dy = hhi + hhj + 4.0
                        ddx = xj - xi
                        ddy = yj - yi
                        ov_x = min_dx - abs(ddx)
                        ov_y = min_dy - abs(ddy)
                        if ov_x > 0 and ov_y > 0:
                            moved = True
                            if ov_x < ov_y * 1.25:
                                sgn = 1.0 if (ddx > 0 or (ddx == 0 and i < j)) else -1.0
                                push = 0.55 * ov_x * sgn
                                boxes[i][0] -= push
                                boxes[j][0] += push
                            else:
                                sgn = 1.0 if (ddy > 0 or (ddy == 0 and i < j)) else -1.0
                                push = 0.55 * ov_y * sgn
                                boxes[i][1] -= push
                                boxes[j][1] += push

                # 3. Strong centroid-marker repulsion (runs AFTER spring pull so boxes never touch any marker dot!)
                for i in range(n):
                    xi, yi, hwi, hhi = boxes[i]
                    for mx, my in markers_pt:
                        ov_x = (hwi + 9.5) - abs(xi - mx)
                        ov_y = (hhi + 9.0) - abs(yi - my)
                        if ov_x > 0 and ov_y > 0:
                            moved = True
                            if ov_y <= ov_x * 1.15:
                                sgn = 1.0 if yi >= my else -1.0
                                boxes[i][1] += 0.80 * ov_y * sgn
                            else:
                                sgn = 1.0 if xi >= mx else -1.0
                                boxes[i][0] += 0.80 * ov_x * sgn

                # 4. Strict canvas boundary clamp
                for i in range(n):
                    _xi, _yi, hwi, hhi = boxes[i]
                    boxes[i][0] = float(np.clip(boxes[i][0], hwi + 6.0, max(hwi + 7.0, ax_w_pts - hwi - 6.0)))
                    boxes[i][1] = float(np.clip(boxes[i][1], hhi + 6.0, max(hhi + 7.0, ax_h_pts - hhi - 6.0)))

                if not moved and _it >= 42:
                    break

        for item, (ax_p, ay_p), (bx_p, by_p, _hw, _hh) in zip(pending_labels, anchors_pt, boxes):
            dx_pts = float(bx_p - ax_p)
            dy_pts = float(by_p - ay_p)
            dist_pts = math.hypot(dx_pts, dy_pts)
            col = item["color"]
            fs = float(item.get("fontsize", 10.0))
            ax.annotate(
                str(item["text"]),
                xy=(float(item["x"]), float(item["y"])),
                xytext=(dx_pts, dy_pts),
                textcoords="offset points",
                fontsize=fs,
                fontweight='bold',
                color='#111827',
                ha='center',
                va='center',
                zorder=7,
                bbox=dict(boxstyle="round,pad=0.2", fc="white", ec=col, alpha=0.88, lw=1.0),
                arrowprops=(
                    dict(arrowstyle="-", color=col, lw=0.75, alpha=0.60, shrinkA=2, shrinkB=4)
                    if dist_pts > 20.0 else None
                ),
            )


    def _get_formant_time_cmap(self):
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

        norm_mode = self._resolve_formant_normalization()
        is_hz = self._is_hz_formant_mode(norm_mode)

        f1_pad = (f1_p99 - f1_p1) * 0.16 if f1_p99 > f1_p1 else (100.0 if is_hz else 0.5)
        f2_pad = (f2_p99 - f2_p1) * 0.20 if f2_p99 > f2_p1 else (180.0 if is_hz else 0.8)

        f1_grid_min, f1_grid_max = (max(35.0, f1_p1 - f1_pad) if is_hz else (f1_p1 - f1_pad)), f1_p99 + f1_pad
        f2_grid_min, f2_grid_max = (max(220.0, f2_p1 - f2_pad) if is_hz else (f2_p1 - f2_pad)), f2_p99 + f2_pad

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
            layer_z = evaluate_2d_kde_grid(all_f2, all_f1, grid_f2, grid_f1, bw_method=bw, weights=weights)
            kde_layers.append(layer_z * float(np.sum(weights)))

        kde_stack = np.stack(kde_layers, axis=0)
        self._report_export_progress(0.76, "正在合成时空密度热力图...")
        D = smooth_2d_array(np.sum(kde_stack, axis=0), sigma=0.65)
        TD = smooth_2d_array(np.sum(tau_layers[:, np.newaxis, np.newaxis] * kde_stack, axis=0), sigma=0.65)

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

        sm = plt.cm.ScalarMappable(cmap=time_cmap, norm=Normalize(vmin=0.0, vmax=100.0))
        sm.set_array([])
        return sm, all_f1.tolist(), all_f2.tolist()


    def _draw_formant_space_panel(self, ax, data_entries, groupby_val, label_mode, ellipse_mode, show_raw, time_gradient, density_overlay, density_sm_holder=None, transform_fn=None, fixed_limits=None, is_preview=True):
        ax.set_facecolor("#FFFFFF")
        ax._is_vowel_space_ax = True
        ax.grid(True, linestyle="--", alpha=0.25, linewidth=0.8)

        show_convex_hull = bool(self.get_param('show_convex_hull', False)) or bool(self.get_param('formant_show_convex_hull', False))
        show_diphthong_arrows = bool(self.get_param('show_diphthong_arrows', False)) or bool(self.get_param('formant_show_diphthong_arrows', False))
        norm_mode = self._resolve_formant_normalization()
        is_hz = self._is_hz_formant_mode(norm_mode)
        group_key = self._get_group_key(groupby_val)

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

        def _make_bucket():
            return {
                'f1': [], 'f2': [], 'steady_f1': [], 'steady_f2': [],
                'token_f1': [], 'token_f2': [],
                'entries_labels': [], 'syl_chars': [],
                'trajs_f1': [], 'trajs_f2': [],
                'sub_words': {},
            }

        for entry in data_entries:
            for syl in entry.get('syl_formants', []):
                c_s, c_e = syl['bounds']
                xs = np.asarray(entry.get('raw_xs', []), dtype=float)
                f1_arr = np.asarray(entry.get('raw_f1', []), dtype=float)
                f2_arr = np.asarray(entry.get('raw_f2', []), dtype=float)

                if len(xs) > 0 and len(f1_arr) > 0 and len(f2_arr) > 0:
                    base_valid = np.isfinite(f1_arr) & np.isfinite(f2_arr) & (f1_arr > 0) & (f2_arr > f1_arr)
                    dur = max(0.0, float(c_e - c_s))
                    core_s = c_s + 0.20 * dur
                    core_e = c_e - 0.20 * dur
                    core_mask = (xs >= core_s) & (xs <= core_e) & base_valid
                    if np.count_nonzero(core_mask) >= 3:
                        mask = core_mask
                    else:
                        mask = (xs >= c_s) & (xs <= c_e) & base_valid
                    s_f1 = f1_arr[mask]
                    s_f2 = f2_arr[mask]
                else:
                    s_f1 = np.asarray(syl.get('f1', []), dtype=float)
                    s_f2 = np.asarray(syl.get('f2', []), dtype=float)
                    n_s = len(s_f1)
                    if n_s >= 5 and len(s_f2) == n_s:
                        si0 = int(math.floor(0.2 * n_s))
                        si1 = max(si0 + 1, int(math.ceil(0.8 * n_s)))
                        sub_s1, sub_s2 = s_f1[si0:si1], s_f2[si0:si1]
                        mask_sub = np.isfinite(sub_s1) & np.isfinite(sub_s2) & (sub_s1 > 0) & (sub_s2 > sub_s1)
                        if np.count_nonzero(mask_sub) >= 3:
                            s_f1, s_f2 = sub_s1[mask_sub], sub_s2[mask_sub]
                        else:
                            mask_s = np.isfinite(s_f1) & np.isfinite(s_f2) & (s_f2 > s_f1)
                            s_f1, s_f2 = s_f1[mask_s], s_f2[mask_s]
                    else:
                        mask_s = np.isfinite(s_f1) & np.isfinite(s_f2) & (s_f2 > s_f1)
                        s_f1, s_f2 = s_f1[mask_s], s_f2[mask_s]

                if len(s_f1) == 0:
                    continue

                if len(s_f1) >= 4:
                    med1 = float(np.median(s_f1))
                    med2 = float(np.median(s_f2))
                    mad1 = max(float(np.median(np.abs(s_f1 - med1))) * 1.4826, 25.0 if med1 > 50.0 else 0.18)
                    mad2 = max(float(np.median(np.abs(s_f2 - med2))) * 1.4826, 45.0 if med2 > 50.0 else 0.25)
                    inlier_s = (np.abs(s_f1 - med1) <= 2.5 * mad1) & (np.abs(s_f2 - med2) <= 2.5 * mad2)
                    if np.count_nonzero(inlier_s) >= 3:
                        s_f1 = s_f1[inlier_s]
                        s_f2 = s_f2[inlier_s]

                spk_name = entry.get('speaker_name')
                if transform_fn is not None:
                    try:
                        s_f1, s_f2 = transform_fn(s_f1, s_f2, speaker_name=spk_name)
                    except TypeError:
                        s_f1, s_f2 = transform_fn(s_f1, s_f2)

                cat = self._get_syl_category(entry, syl, groupby_val)
                if cat not in category_data:
                    category_data[cat] = _make_bucket()

                word_lbl = str(syl.get('char') or entry.get('label') or cat)
                if word_lbl not in category_data[cat]['sub_words']:
                    category_data[cat]['sub_words'][word_lbl] = {
                        'f1': [], 'f2': [], 'steady_f1': [], 'steady_f2': [],
                        'token_f1': [], 'token_f2': [], 'trajs_f1': [], 'trajs_f2': [],
                    }
                sw_b = category_data[cat]['sub_words'][word_lbl]

                category_data[cat]['f1'].append(s_f1)
                category_data[cat]['f2'].append(s_f2)
                category_data[cat]['entries_labels'].append(entry['label'])
                category_data[cat]['syl_chars'].append(syl['char'])
                sw_b['f1'].append(s_f1)
                sw_b['f2'].append(s_f2)

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
                    sw_b['trajs_f1'].append(traj_f1)
                    sw_b['trajs_f2'].append(traj_f2)
                    n_t = len(traj_f1)
                    i0 = int(math.floor(0.2 * n_t)) if n_t >= 5 else 0
                    i1 = max(i0 + 1, int(math.ceil(0.8 * n_t))) if n_t >= 5 else n_t
                    sub1, sub2 = traj_f1[i0:i1], traj_f2[i0:i1]
                    v_sub = np.isfinite(sub1) & np.isfinite(sub2)
                    if np.any(v_sub):
                        v1, v2 = sub1[v_sub], sub2[v_sub]
                        if len(v1) >= 4:
                            m1_sub, m2_sub = float(np.median(v1)), float(np.median(v2))
                            s1_sub = max(float(np.median(np.abs(v1 - m1_sub))) * 1.4826, float(np.std(v1)) * 0.25, 1e-6)
                            s2_sub = max(float(np.median(np.abs(v2 - m2_sub))) * 1.4826, float(np.std(v2)) * 0.25, 1e-6)
                            keep = (np.abs(v1 - m1_sub) <= 2.5 * s1_sub) & (np.abs(v2 - m2_sub) <= 2.5 * s2_sub)
                            if np.any(keep):
                                v1, v2 = v1[keep], v2[keep]
                        tok_f1 = float(np.mean(v1))
                        tok_f2 = float(np.mean(v2))
                    else:
                        tok_f1 = _safe_nanmean(s_f1)
                        tok_f2 = _safe_nanmean(s_f2)
                else:
                    tok_f1 = _safe_nanmean(s_f1)
                    tok_f2 = _safe_nanmean(s_f2)

                if np.isfinite(tok_f1) and np.isfinite(tok_f2):
                    category_data[cat]['token_f1'].append(tok_f1)
                    category_data[cat]['token_f2'].append(tok_f2)
                    sw_b['token_f1'].append(tok_f1)
                    sw_b['token_f2'].append(tok_f2)
                    if len(s_f1) >= 3:
                        t_idx = np.linspace(-0.5, 0.5, len(s_f1))
                        slope1 = float(np.polyfit(t_idx, s_f1, 1)[0]) if np.std(s_f1) > 1e-6 else 0.0
                        slope2 = float(np.polyfit(t_idx, s_f2, 1)[0]) if np.std(s_f2) > 1e-6 else 0.0
                        det1 = (s_f1 - (np.mean(s_f1) + slope1 * t_idx)) + tok_f1
                        det2 = (s_f2 - (np.mean(s_f2) + slope2 * t_idx)) + tok_f2
                        category_data[cat]['steady_f1'].append(det1)
                        category_data[cat]['steady_f2'].append(det2)
                        sw_b['steady_f1'].append(det1)
                        sw_b['steady_f2'].append(det2)
                    else:
                        category_data[cat]['steady_f1'].append(s_f1)
                        category_data[cat]['steady_f2'].append(s_f2)
                        sw_b['steady_f1'].append(s_f1)
                        sw_b['steady_f2'].append(s_f2)

        if not category_data:
            ax.text(0.5, 0.5, "没有找到有效的共振峰分析数据！", ha='center', va='center', color='red', fontsize=12)
            return [], []

        categories = self._ordered_unique(
            self._get_syl_category(e, s, groupby_val)
            for e in data_entries
            for s in e.get('syl_formants', [])
            if self._get_syl_category(e, s, groupby_val) in category_data
        )
        for k in category_data.keys():
            if k not in categories:
                categories.append(k)

        cat_styles = {cat: self._get_series_style(i) for i, cat in enumerate(categories)}
        cat_colors = {cat: cat_styles[cat]["color"] for cat in categories}

        all_f1_plotted_list = []
        all_f2_plotted_list = []
        for cat in categories:
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
            is_f1f2_scatter = bool(self.get_param("_f1_f2_scatter_mode", False))
            if show_raw:
                ax.scatter(
                    f2_list, f1_list,
                    color=cat_colors[cat], s=18 if is_f1f2_scatter else 14,
                    alpha=0.24 if is_f1f2_scatter else (0.20 if density_overlay else 0.14),
                    edgecolors='none', zorder=3,
                )
            if is_f1f2_scatter:
                tk1 = np.asarray(category_data[cat].get('token_f1', []), dtype=float)
                tk2 = np.asarray(category_data[cat].get('token_f2', []), dtype=float)
                vm_tk = np.isfinite(tk1) & np.isfinite(tk2)
                if np.any(vm_tk):
                    ax.scatter(
                        tk2[vm_tk], tk1[vm_tk],
                        color=cat_colors[cat], marker=cat_styles[cat]["marker"],
                        s=56, alpha=0.76, edgecolors="white", linewidth=0.65, zorder=4.8,
                    )

        # Precompute canvas coordinate spans so _draw_confidence_ellipse can reject degenerate giant ellipses
        if fixed_limits is not None:
            ymin_l, ymax_l, xmin_l, xmax_l = fixed_limits
            f1_span_est = max(1e-3, abs(float(ymin_l - ymax_l)))
            f2_span_est = max(1e-3, abs(float(xmin_l - xmax_l)))
            final_ylim = (ymin_l, ymax_l)
            final_xlim = (xmin_l, xmax_l)
        elif all_f1_plotted_list and all_f2_plotted_list:
            lim_f1_all = list(all_f1_plotted_list)
            lim_f2_all = list(all_f2_plotted_list)
            if density_f1 is not None and len(density_f1) > 0:
                lim_f1_all.append(np.asarray(density_f1, dtype=float))
            if density_f2 is not None and len(density_f2) > 0:
                lim_f2_all.append(np.asarray(density_f2, dtype=float))
            limit_f1 = np.concatenate(lim_f1_all)
            limit_f2 = np.concatenate(lim_f2_all)
            f1_p1, f1_p99 = np.percentile(limit_f1, 1.0), np.percentile(limit_f1, 99.0)
            f2_p1, f2_p99 = np.percentile(limit_f2, 1.0), np.percentile(limit_f2, 99.0)
            f1_pad = (f1_p99 - f1_p1) * 0.16 if f1_p99 > f1_p1 else (100.0 if is_hz else 0.5)
            f2_pad = (f2_p99 - f2_p1) * 0.16 if f2_p99 > f2_p1 else (150.0 if is_hz else 0.8)
            final_ylim = (f1_p99 + f1_pad, max(35.0, f1_p1 - f1_pad) if is_hz else (f1_p1 - f1_pad))
            final_xlim = (f2_p99 + f2_pad, max(240.0, f2_p1 - f2_pad) if is_hz else (f2_p1 - f2_pad))
            f1_span_est = max(1e-3, abs(float(final_ylim[0] - final_ylim[1])))
            f2_span_est = max(1e-3, abs(float(final_xlim[0] - final_xlim[1])))
        else:
            final_ylim, final_xlim = None, None
            f1_span_est, f2_span_est = (900.0, 2200.0) if is_hz else (4.0, 4.0)

        canvas_span = (f2_span_est, f1_span_est)

        # Detect whether `groupby=group` or `groupby=speaker` groups multiple distinct vowels into macro buckets
        def _is_macro_multi_vowel_cat(cat_obj):
            sub_w = cat_obj.get('sub_words', {})
            if len(sub_w) < 2:
                return False
            # Exclude synthetic repetition names like 衣1..衣4 that share a common leading character
            first_chars = {str(w)[0] for w in sub_w.keys() if str(w)}
            if len(first_chars) <= 1:
                return False
            sub_m1, sub_m2 = [], []
            for wb in sub_w.values():
                if wb['token_f1'] and wb['token_f2']:
                    sub_m1.append(float(np.mean(wb['token_f1'])))
                    sub_m2.append(float(np.mean(wb['token_f2'])))
            if len(sub_m1) < 2:
                return False
            sp1 = float(max(sub_m1) - min(sub_m1))
            sp2 = float(max(sub_m2) - min(sub_m2))
            thresh1 = 110.0 if is_hz else 0.45
            thresh2 = 240.0 if is_hz else 0.55
            return (sp1 >= thresh1) or (sp2 >= thresh2)

        is_macro_grouping = (
            group_key in ("group", "speaker_name")
            and not show_diphthong_arrows
            and not time_gradient
            and any(_is_macro_multi_vowel_cat(category_data[c]) for c in categories)
        )

        centroids_f2_f1 = []
        all_tokens_f2_f1 = []
        trajectory_lengths = {}
        pending_labels = []
        plotted_markers_xy = []

        for cat in categories:
            color = cat_colors[cat]
            marker = cat_styles[cat]["marker"]
            ls = cat_styles[cat]["linestyle"]
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

            # Macro multi-vowel rendering (`groupby=group` or `groupby=speaker` in Exp 3 Charts 01, 02, 03)
            if is_macro_grouping and _is_macro_multi_vowel_cat(category_data[cat]):
                sub_words = category_data[cat]['sub_words']
                cat_sub_centroids = []
                cat_tls = []
                legend_added = False

                for w_lbl, wb in sub_words.items():
                    wt1 = np.asarray(wb['token_f1'], dtype=float)
                    wt2 = np.asarray(wb['token_f2'], dtype=float)
                    wf1 = np.concatenate(wb['f1']) if wb['f1'] else np.array([])
                    wf2 = np.concatenate(wb['f2']) if wb['f2'] else np.array([])
                    if len(wt1) == 0 and len(wf1) == 0:
                        continue
                    w_mf1 = _safe_nanmean(wt1) if len(wt1) > 0 else _safe_nanmean(wf1)
                    w_mf2 = _safe_nanmean(wt2) if len(wt2) > 0 else _safe_nanmean(wf2)
                    if not (np.isfinite(w_mf1) and np.isfinite(w_mf2)):
                        continue

                    centroids_f2_f1.append((float(w_mf2), float(w_mf1)))
                    cat_sub_centroids.append((float(w_mf2), float(w_mf1), str(w_lbl)))
                    plotted_markers_xy.append((float(w_mf2), float(w_mf1)))

                    if wb['trajs_f1'] and wb['trajs_f2']:
                        mt1 = _safe_nanmean(np.array(wb['trajs_f1']), axis=0)
                        mt2 = _safe_nanmean(np.array(wb['trajs_f2']), axis=0)
                        vm = np.isfinite(mt1) & np.isfinite(mt2)
                        if np.count_nonzero(vm) >= 2:
                            cat_tls.append(float(np.sum(np.sqrt(np.diff(mt1[vm]) ** 2 + np.diff(mt2[vm]) ** 2))))

                    lbl_leg = str(cat) if not legend_added else None
                    legend_added = True
                    pt_size = 76 if group_key == "speaker_name" else 98
                    ax.scatter(
                        w_mf2, w_mf1,
                        color=color, s=pt_size, marker=marker,
                        edgecolors='black', linewidth=0.95, alpha=0.90,
                        zorder=6, label=lbl_leg,
                    )

                    if group_key != "speaker_name":
                        # Draw per-vowel dispersion ellipse (using steady-state frames or small-sample 1σ scaling when dense)
                        is_small_n = len(wt1) <= 4
                        ef1 = (np.concatenate(wb['steady_f1']) if wb['steady_f1'] else wf1) if len(wt1) < 3 else wt1
                        ef2 = (np.concatenate(wb['steady_f2']) if wb['steady_f2'] else wf2) if len(wt1) < 3 else wt2
                        ecenter = (w_mf2, w_mf1)
                        n_std_1 = 1.0 if is_small_n else 1.5152
                        n_std_2 = 1.15 if is_small_n else 2.0
                        ell_alpha = 0.52 if len(sub_words) >= 4 else 0.72
                        if "1-sigma" in str(ellipse_mode) or "68%" in str(ellipse_mode):
                            ax._error_band_label = "Ellipse: 68% (1σ)"
                            self._draw_confidence_ellipse(
                                ef2, ef1, ax, n_std=n_std_1, facecolor='none', center=ecenter,
                                canvas_span=canvas_span, edgecolor=color, linestyle='--', linewidth=1.1, alpha=ell_alpha, zorder=4,
                            )
                        elif "2-sigma" in str(ellipse_mode) or "95%" in str(ellipse_mode):
                            ax._error_band_label = "Ellipse: ±1 SD" if is_small_n else "Ellipse: 95% CI"
                            self._draw_confidence_ellipse(
                                ef2, ef1, ax, n_std=n_std_2, facecolor='none', center=ecenter,
                                canvas_span=canvas_span, edgecolor=color, linestyle='-.', linewidth=1.1, alpha=ell_alpha, zorder=4,
                            )
                        if label_mode != "隐藏标签":
                            pending_labels.append({
                                "x": w_mf2, "y": w_mf1, "text": str(w_lbl), "color": color, "fontsize": 9.2,
                            })

                if cat_tls:
                    trajectory_lengths[str(cat)] = float(np.mean(cat_tls))

                # For speaker comparison (`groupby=speaker`), outline each speaker's vowel space polygon & label 3 corner vowels
                if group_key == "speaker_name" and len(cat_sub_centroids) >= 3:
                    pts_spk = np.asarray([(p[0], p[1]) for p in cat_sub_centroids], dtype=float)
                    hull_v, _spk_area = _compute_2d_convex_hull_and_area(pts_spk)
                    if len(hull_v) >= 3:
                        closed = np.vstack([hull_v, hull_v[0]])
                        ax.plot(closed[:, 0], closed[:, 1], linestyle=ls, linewidth=1.8, color=color, alpha=0.85, zorder=4.5)
                        ax.fill(closed[:, 0], closed[:, 1], color=color, alpha=0.06, zorder=2.2)
                    if label_mode != "隐藏标签":
                        # Pick up to 3 cardinal corner vowels (highest F2 [i], highest F1 [a], lowest F2 [u])
                        i_hi_f2 = max(cat_sub_centroids, key=lambda p: p[0])
                        i_hi_f1 = max(cat_sub_centroids, key=lambda p: p[1])
                        i_lo_f2 = min(cat_sub_centroids, key=lambda p: p[0])
                        seen_c = set()
                        for cx_v, cy_v, cw_lbl in (i_hi_f2, i_hi_f1, i_lo_f2):
                            if cw_lbl not in seen_c:
                                seen_c.add(cw_lbl)
                                pending_labels.append({
                                    "x": cx_v, "y": cy_v, "text": f"{cat}:{cw_lbl}", "color": color, "fontsize": 8.6,
                                })
                continue

            # Standard single-category rendering (`groupby=label` or single-vowel groups)
            mean_f1 = _safe_nanmean(tok_f1_arr) if len(tok_f1_arr) > 0 else _safe_nanmean(f1_list)
            mean_f2 = _safe_nanmean(tok_f2_arr) if len(tok_f2_arr) > 0 else _safe_nanmean(f2_list)
            if np.isfinite(mean_f2) and np.isfinite(mean_f1):
                centroids_f2_f1.append((float(mean_f2), float(mean_f1)))

            lbl_x = np.nan
            lbl_y = np.nan

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
                        plotted_markers_xy.extend([(f2_20, f1_20), (f2_50, f1_50), (f2_80, f1_80)])
                    elif time_gradient:
                        ax.plot(v_f2, v_f1, color=color, linewidth=1.2, alpha=0.55, label=str(cat), zorder=5)
                        cmap_t = self._get_formant_time_cmap()
                        marker_colors = cmap_t(np.linspace(0, 1, n_pts))
                        for i in range(n_pts):
                            ax.scatter(v_f2[i], v_f1[i], color=marker_colors[i], s=80,
                                       edgecolors='black', linewidth=0.8, zorder=6)
                        mid_idx = n_pts // 2
                        lbl_x = float(v_f2[mid_idx])
                        lbl_y = float(v_f1[mid_idx])
                        plotted_markers_xy.append((lbl_x, lbl_y))

            is_intra_token_ellipse = (len(tok_f1_arr) < 3)
            if not is_intra_token_ellipse:
                ellipse_f1_pts = tok_f1_arr
                ellipse_f2_pts = tok_f2_arr
                ellipse_center = None
            else:
                steady_f1_list = category_data[cat].get('steady_f1', [])
                steady_f2_list = category_data[cat].get('steady_f2', [])
                ellipse_f1_pts = np.concatenate(steady_f1_list) if steady_f1_list else f1_list
                ellipse_f2_pts = np.concatenate(steady_f2_list) if steady_f2_list else f2_list
                ellipse_center = (mean_f2, mean_f1)

            if np.isnan(lbl_x) or np.isnan(lbl_y):
                ax.scatter(mean_f2, mean_f1, color=color, s=125, marker=marker, edgecolors='black', linewidth=1.05, zorder=6, label=str(cat))
                lbl_x = float(mean_f2)
                lbl_y = float(mean_f1)
                plotted_markers_xy.append((lbl_x, lbl_y))

            if not np.isnan(lbl_x) and not np.isnan(lbl_y):
                lbl_text = None
                if label_mode == "显示分组标签":
                    lbl_text = str(cat)
                elif label_mode == "显示单字标签":
                    syl_chars = category_data[cat]['syl_chars']
                    lbl_text = str(max(set(syl_chars), key=syl_chars.count) if syl_chars else cat)
                elif label_mode == "显示词语标签":
                    entries_labels = category_data[cat]['entries_labels']
                    lbl_text = str(cat) if group_key == "label" else str(max(set(entries_labels), key=entries_labels.count) if entries_labels else cat)
                if lbl_text:
                    pending_labels.append({
                        "x": lbl_x, "y": lbl_y, "text": lbl_text, "color": color, "fontsize": 10.0,
                    })

            if "1-sigma" in str(ellipse_mode) or "68%" in str(ellipse_mode):
                ax._error_band_label = "Ellipse: 68% (1σ)"
                self._draw_confidence_ellipse(
                    ellipse_f2_pts, ellipse_f1_pts, ax, n_std=1.5152, facecolor='none',
                    center=ellipse_center, canvas_span=canvas_span,
                    edgecolor=color, linestyle='--', linewidth=1.5, zorder=4,
                )
            elif "2-sigma" in str(ellipse_mode) or "95%" in str(ellipse_mode):
                ax._error_band_label = "Ellipse: 95% CI"
                n_std_val = 1.5152 if is_intra_token_ellipse else 2.4477
                self._draw_confidence_ellipse(
                    ellipse_f2_pts, ellipse_f1_pts, ax, n_std=n_std_val, facecolor='none',
                    center=ellipse_center, canvas_span=canvas_span,
                    edgecolor=color, linestyle='-.', linewidth=1.5, zorder=4,
                )

        # Outer Convex Hull & Vowel Space Area (VSA)
        vsa_area = 0.0
        if show_convex_hull:
            hull_input = centroids_f2_f1 if len(centroids_f2_f1) >= 3 else all_tokens_f2_f1
            hull_verts, vsa_area = _compute_2d_convex_hull_and_area(hull_input)
            if len(hull_verts) >= 3:
                closed_verts = np.vstack([hull_verts, hull_verts[0]])
                norm_low = norm_mode.lower()
                if "bark" in norm_low:
                    unit_sq = "Bark²"
                elif "erb" in norm_low:
                    unit_sq = "ERB²"
                elif "lobanov" in norm_low or "归一化" in norm_mode or "z-score" in norm_low:
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

        # Expand final_xlim & final_ylim to enclose 100% of all plotted ellipses, errorbars, and markers!
        if fixed_limits is None and final_ylim is not None and final_xlim is not None:
            f1_hi, f1_lo = float(final_ylim[0]), float(final_ylim[1])
            f2_hi, f2_lo = float(final_xlim[0]), float(final_xlim[1])
            env_boxes = getattr(ax, "_ellipse_envelopes", []) or []
            env_f2_mins = [b[0] for b in env_boxes] + [p[0] for p in plotted_markers_xy]
            env_f2_maxs = [b[1] for b in env_boxes] + [p[0] for p in plotted_markers_xy]
            env_f1_mins = [b[2] for b in env_boxes] + [p[1] for p in plotted_markers_xy]
            env_f1_maxs = [b[3] for b in env_boxes] + [p[1] for p in plotted_markers_xy]
            if env_f2_mins and env_f1_mins:
                e_f2_min, e_f2_max = float(min(env_f2_mins)), float(max(env_f2_maxs))
                e_f1_min, e_f1_max = float(min(env_f1_mins)), float(max(env_f1_maxs))
                pad_x_e = max(1e-3, abs(f2_hi - f2_lo)) * 0.075
                pad_y_e = max(1e-3, abs(f1_hi - f1_lo)) * 0.085
                f2_hi = max(f2_hi, e_f2_max + pad_x_e)
                f2_lo = min(f2_lo, (max(200.0, e_f2_min - pad_x_e) if is_hz else (e_f2_min - pad_x_e)))
                f1_hi = max(f1_hi, e_f1_max + pad_y_e)
                f1_lo = min(f1_lo, (max(25.0, e_f1_min - pad_y_e) if is_hz else (e_f1_min - pad_y_e)))
                final_xlim = (f2_hi, f2_lo)
                final_ylim = (f1_hi, f1_lo)

        ax.invert_xaxis()
        ax.invert_yaxis()

        if final_ylim is not None and final_xlim is not None:
            ax.set_ylim(*final_ylim)
            ax.set_xlim(*final_xlim)

        # Now place all Chinese vowel labels with pairwise box + marker repulsion in `offset points`!
        self._place_non_overlapping_vowel_labels(ax, pending_labels, marker_points=plotted_markers_xy)

        xlabel_str, ylabel_str, _ = self._formant_axis_labels(norm_mode)
        ax.set_xlabel(xlabel_str, fontsize=12, fontweight='bold', labelpad=10)
        ax.set_ylabel(ylabel_str, fontsize=12, fontweight='bold', labelpad=10)

        all_f1_res = np.concatenate(all_f1_plotted_list) if all_f1_plotted_list else np.array([])
        all_f2_res = np.concatenate(all_f2_plotted_list) if all_f2_plotted_list else np.array([])
        return all_f1_res, all_f2_res


    def _add_formant_density_colorbar(self, fig, axes, sm):
        axes_list = [ax for ax in axes if getattr(ax, "get_visible", lambda: True)()]
        if not axes_list:
            return None

        legend_outside = bool(self.get_param('legend_outside', True))
        legend_loc = str(self.get_param('legend_loc', '右上'))
        right_legend = legend_outside and legend_loc.startswith("右")
        left_legend = legend_outside and legend_loc.startswith("左")

        if right_legend:
            fig.subplots_adjust(left=0.08, right=0.74, top=0.91, bottom=0.10, wspace=0.26, hspace=0.28)
            cax = fig.add_axes([0.77, 0.18, 0.018, 0.66])
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
        mode_low = mode_str.lower()
        if not ref_entries:
            def _identity(f1, f2, speaker_name=None, f3=None):
                if f3 is not None:
                    return np.asarray(f1, dtype=float), np.asarray(f2, dtype=float), np.asarray(f3, dtype=float)
                return np.asarray(f1, dtype=float), np.asarray(f2, dtype=float)
            return _identity

        if "bark" in mode_low or "巴克" in mode_str:
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

        elif "erb" in mode_low or "等效矩形" in mode_str:
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

        elif "mel" in mode_low or "美尔" in mode_str:
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

        elif "lobanov" in mode_low or "归一化" in mode_str or "z-score" in mode_low or "zscore" in mode_low or mode_low == "z":
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

        normalization_mode = self._resolve_formant_normalization()
        lock_axes = bool(self.get_param('formant_axis_lock', False))
        axis_ref_entries = self.get_param('formant_axis_ref_entries', data_entries)
        # Always compute Lobanov per-speaker normalization over the full speaker vowel inventory when available
        all_ref = self.all_speakers
        if "lobanov" in normalization_mode.lower() and all_ref and len(axis_ref_entries) < len(data_entries):
            norm_ref_entries = data_entries
        elif "lobanov" in normalization_mode.lower() and all_ref:
            try:
                full_entries = self._extract_active_formant_data(all_ref, filter_groups=False, apply_chart_grouping=False)
                norm_ref_entries = full_entries if full_entries else axis_ref_entries
            except Exception:
                norm_ref_entries = axis_ref_entries
        else:
            norm_ref_entries = axis_ref_entries

        transform_fn = self._build_formant_normalizer(norm_ref_entries, normalization_mode)

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

                is_hz = self._is_hz_formant_mode(normalization_mode)
                f1_pad = (f1_p99 - f1_p1) * 0.15 if f1_p99 > f1_p1 else (100.0 if is_hz else 0.5)
                f2_pad = (f2_p99 - f2_p1) * 0.15 if f2_p99 > f2_p1 else (150.0 if is_hz else 0.8)

                if is_hz:
                    ymin, ymax = f1_p99 + f1_pad, max(35.0, f1_p1 - f1_pad)
                    xmin, xmax = f2_p99 + f2_pad, max(240.0, f2_p1 - f2_pad)
                else:
                    ymin, ymax = f1_p99 + f1_pad, f1_p1 - f1_pad
                    xmin, xmax = f2_p99 + f2_pad, f2_p1 - f2_pad

                fixed_limits = (ymin, ymax, xmin, xmax)

        facet_specs = [("Default", data_entries)]
        if facet_mode == "group":
            f_grps = self._ordered_unique(self._entry_wordlist_group(e) for e in data_entries)
            facet_specs = [(g, [e for e in data_entries if self._entry_wordlist_group(e) == g]) for g in f_grps]
        elif facet_mode == "speaker":
            f_spks = self._ordered_unique(e['speaker_name'] for e in data_entries)
            facet_specs = [(s, [e for e in data_entries if e['speaker_name'] == s]) for s in f_spks]
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
            fig, ax = plt.subplots(figsize=(9.6, 7.0))
            axes_flat = [ax]
        else:
            n_cols = min(2, n_facets)
            n_rows = math.ceil(n_facets / n_cols)
            fig, axes = plt.subplots(n_rows, n_cols, figsize=(8.4 * n_cols, 6.5 * n_rows), squeeze=False)
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

        # Default legend outside to the right so high-back vowels ([u]/[o]/都) in upper-right are never occluded!
        shared_speaker_facet = (n_facets > 1) and (facet_mode == "speaker") and (group_key != "speaker_name")
        for idx_ax, ax in enumerate(axes_flat[:n_facets]):
            if shared_speaker_facet and idx_ax != min(1, n_facets - 1):
                continue
            handles, labels = ax.get_legend_handles_labels()
            if handles:
                legend_kwargs = self._get_legend_kwargs(ax=ax, default_outside=True)
                legend_kwargs["fontsize"] = 8.8
                if density_overlay and self.get_param('legend_outside', True):
                    legend_loc = str(self.get_param('legend_loc', '右上'))
                    if legend_loc.startswith("右"):
                        y_anchor = 1.0 if "上" in legend_loc else 0.0
                        legend_kwargs["bbox_to_anchor"] = (1.16, y_anchor)
                ax.legend(**legend_kwargs)

        if not density_overlay:
            fig.tight_layout()
            if n_facets > 1 and not shared_speaker_facet:
                fig.subplots_adjust(wspace=0.36)
                setattr(fig, "_phontracer_skip_tight_layout", True)
        return fig


    def _plot_formant_density_heatmap(self, data_entries, group_key, scale):
        show_raw = self.get_param('formant_density_show_raw', False)
        show_contours = self.get_param('formant_density_show_contours', True)
        facet_mode = self._resolve_facet_by(chart_type="formant_density", fallback="group")

        normalization_mode = self._resolve_formant_normalization()
        transform_fn = self._build_formant_normalizer(data_entries, normalization_mode)

        facet_specs = [("Default", data_entries)]
        if facet_mode == "group":
            f_grps = self._ordered_unique(self._entry_wordlist_group(e) for e in data_entries)
            facet_specs = [(g, [e for e in data_entries if self._entry_wordlist_group(e) == g]) for g in f_grps]
        elif facet_mode == "speaker":
            f_spks = self._ordered_unique(e['speaker_name'] for e in data_entries)
            facet_specs = [(s, [e for e in data_entries if e['speaker_name'] == s]) for s in f_spks]
        elif facet_mode == "syllable_position":
            max_syls = max((len(e.get('syl_formants', [])) for e in data_entries), default=0)
            facet_specs = [(f"第 {i + 1} 音节", self._slice_formant_entries_for_syllable(data_entries, i)) for i in range(max_syls)]
        elif facet_mode == "label":
            f_lbls = self._ordered_unique(e['label'] for e in data_entries)
            facet_specs = [(w, [e for e in data_entries if e['label'] == w]) for w in f_lbls]

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

        xlabel_str, ylabel_str, _ = self._formant_axis_labels(normalization_mode)

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

        is_hz = self._is_hz_formant_mode(normalization_mode)
        if global_f1 and global_f2:
            arr_f1 = np.asarray(global_f1, dtype=float)
            arr_f2 = np.asarray(global_f2, dtype=float)
            f1_p1, f1_p99 = np.percentile(arr_f1, 1.0), np.percentile(arr_f1, 99.0)
            f2_p1, f2_p99 = np.percentile(arr_f2, 1.0), np.percentile(arr_f2, 99.0)
            f1_pad = (f1_p99 - f1_p1) * 0.16 if f1_p99 > f1_p1 else (100.0 if is_hz else 0.5)
            f2_pad = (f2_p99 - f2_p1) * 0.22 if f2_p99 > f2_p1 else (180.0 if is_hz else 0.8)
            ylim_pair = (f1_p99 + f1_pad, max(35.0, f1_p1 - f1_pad) if is_hz else (f1_p1 - f1_pad))
            xlim_pair = (f2_p99 + f2_pad, max(220.0, f2_p1 - f2_pad) if is_hz else (f2_p1 - f2_pad))
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
        fig, ax = plt.subplots(figsize=(10.2, 5.8))
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
            target_syl_idx = 0
        elif syl_filter_raw in ("2", "第2音节", "第 2 音节"):
            target_syl_idx = 1
        elif syl_filter_raw in ("3", "第3音节", "第 3 音节"):
            target_syl_idx = 2
        else:
            m_syl = re.search(r"第\s*(\d+)\s*音节", syl_filter_raw)
            if m_syl:
                target_syl_idx = max(0, int(m_syl.group(1)) - 1)

        normalization_mode = self._resolve_formant_normalization()
        all_ref = self.all_speakers
        if "lobanov" in normalization_mode.lower() and all_ref:
            try:
                full_entries = self._extract_active_formant_data(all_ref, filter_groups=False, apply_chart_grouping=False)
                norm_ref = full_entries if full_entries else data_entries
            except Exception:
                norm_ref = data_entries
        else:
            norm_ref = data_entries
        transform_fn = self._build_formant_normalizer(norm_ref, normalization_mode)

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

        categories = list(category_trajs.keys())
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
        _, _, traj_ylabel = self._formant_axis_labels(normalization_mode)
        ax.set_ylabel(traj_ylabel, fontsize=12, fontweight='bold', labelpad=10)
        ax.margins(x=0.03)

        handles, labels = ax.get_legend_handles_labels()
        if handles:
            legend_kwargs = self._get_legend_kwargs(ax=ax, default_outside=True)
            legend_kwargs["fontsize"] = 8.0 if len(handles) > 10 else 8.5
            legend_kwargs["labelspacing"] = 0.40 if len(handles) > 10 else 0.50
            if len(handles) > 16:
                legend_kwargs["ncol"] = 2
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
        """
        groupby_val = self._resolve_color_by()
        normalization_mode = self._resolve_formant_normalization()
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
        ax_p.set_xticklabels(categories, fontweight="bold", rotation=35 if n_cats > 8 else 0, ha="right" if n_cats > 8 else "center")
        ax_p.set_yticklabels(categories, fontweight="bold")
        t_p = "元音对立重叠度矩阵 (Pillai Score: 0=合并, 1=分离)"
        ax_p.set_title(t_p, fontsize=11.5, fontweight="bold")
        ax_p._original_title = t_p
        if n_cats <= 14:
            for r_i in range(n_cats):
                for c_i in range(n_cats):
                    val = pillai_mat[r_i, c_i]
                    if np.isfinite(val):
                        txt_c = "white" if val > 0.55 else "#111827"
                        ax_p.text(c_i, r_i, f"{val:.2f}" if n_cats > 8 else f"{val:.3f}", ha="center", va="center", color=txt_c, fontsize=8 if n_cats > 8 else 9, fontweight="bold")
        cb_p = fig.colorbar(im_p, ax=ax_p, fraction=0.046, pad=0.04)
        if hasattr(cb_p, "ax"):
            cb_p.ax._is_colorbar_axis = True
        cb_p.set_label("Pillai-Bartlett Trace (0~1)")

        im_d = ax_d.imshow(euclid_mat, cmap="PuBu")
        ax_d.set_xticks(range(n_cats))
        ax_d.set_yticks(range(n_cats))
        ax_d.set_xticklabels(categories, fontweight="bold", rotation=35 if n_cats > 8 else 0, ha="right" if n_cats > 8 else "center")
        ax_d.set_yticklabels(categories, fontweight="bold")
        t_d = "元音中心声学欧氏距离矩阵 (Euclidean Distance)"
        ax_d.set_title(t_d, fontsize=11.5, fontweight="bold")
        ax_d._original_title = t_d
        max_d = _safe_nanmax(euclid_mat, default=1.0)
        if n_cats <= 14:
            for r_i in range(n_cats):
                for c_i in range(n_cats):
                    val = euclid_mat[r_i, c_i]
                    if np.isfinite(val):
                        txt_c = "white" if (max_d > 0 and val > 0.58 * max_d) else "#111827"
                        fmt_s = f"{val:.0f}" if (val >= 100 and n_cats > 8) else (f"{val:.1f}" if val >= 10 else f"{val:.2f}")
                        ax_d.text(c_i, r_i, fmt_s, ha="center", va="center", color=txt_c, fontsize=8 if n_cats > 8 else 9)
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
