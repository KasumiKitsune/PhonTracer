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
from scipy.stats import gaussian_kde
from scipy.ndimage import gaussian_filter
try:
    from scipy.spatial import ConvexHull
except ImportError:
    ConvexHull = None

from modules.core.acoustic_math import (
    _safe_nanmean,
    _safe_nanmax,
    _compute_2d_convex_hull_and_area,
    _compute_pillai_bartlett_trace,
)
from modules.data_utils import (
    split_into_syllables,
    get_item_syllable_bounds,
    sample_formant_points_by_bounds,
    sample_formant_points_by_bounds_extended,
)


class FormantPlottersMixin:
    """共振峰分析与可视化混入类。"""

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


    def _draw_confidence_ellipse(self, x, y, ax, n_std=1.5152, facecolor='none', center=None, **kwargs):
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
            if len(x) >= 4:
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
            cov = np.cov(x, y, ddof=1)
            if np.any(np.isnan(cov)) or np.any(np.isinf(cov)):
                return None

            vals, vecs = np.linalg.eigh(cov)
            order = vals.argsort()[::-1]
            vals, vecs = vals[order], vecs[:, order]

            theta = np.degrees(np.arctan2(*vecs[:, 0][::-1]))
            width, height = 2.0 * float(n_std) * np.sqrt(np.maximum(vals, 0))

            if center is not None and len(center) == 2 and np.isfinite(center[0]) and np.isfinite(center[1]):
                cx, cy = float(center[0]), float(center[1])
            else:
                cx, cy = float(np.mean(x)), float(np.mean(y))

            ellipse = Ellipse(xy=(cx, cy), width=width, height=height,
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
                    category_data[cat] = {
                        'f1': [], 'f2': [], 'steady_f1': [], 'steady_f2': [],
                        'token_f1': [], 'token_f2': [],
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
                    if len(s_f1) >= 3:
                        t_idx = np.linspace(-0.5, 0.5, len(s_f1))
                        slope1 = float(np.polyfit(t_idx, s_f1, 1)[0]) if np.std(s_f1) > 1e-6 else 0.0
                        slope2 = float(np.polyfit(t_idx, s_f2, 1)[0]) if np.std(s_f2) > 1e-6 else 0.0
                        det1 = (s_f1 - (np.mean(s_f1) + slope1 * t_idx)) + tok_f1
                        det2 = (s_f2 - (np.mean(s_f2) + slope2 * t_idx)) + tok_f2
                        category_data[cat]['steady_f1'].append(det1)
                        category_data[cat]['steady_f2'].append(det2)
                    else:
                        category_data[cat]['steady_f1'].append(s_f1)
                        category_data[cat]['steady_f2'].append(s_f2)

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
                self._draw_confidence_ellipse(ellipse_f2_pts, ellipse_f1_pts, ax, n_std=1.5152, facecolor='none', center=ellipse_center, edgecolor=color, linestyle='--', linewidth=1.5, zorder=4)
            elif "2-sigma" in str(ellipse_mode) or "95%" in str(ellipse_mode):
                ax._error_band_label = "Ellipse: 95% CI"
                n_std_val = 1.5152 if is_intra_token_ellipse else 2.4477
                self._draw_confidence_ellipse(ellipse_f2_pts, ellipse_f1_pts, ax, n_std=n_std_val, facecolor='none', center=ellipse_center, edgecolor=color, linestyle='-.', linewidth=1.5, zorder=4)

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
