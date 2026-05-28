import math
import os
import threading
import queue
import numpy as np
import parselmouth
import matplotlib

matplotlib.use("Agg", force=True)

import warnings
warnings.filterwarnings("ignore", category=UserWarning, message=".*Tight layout not applied.*")

from scipy.stats import gaussian_kde
from .data_utils import split_into_syllables, get_export_text_for_item, get_item_syllable_bounds, sample_formant_points_by_bounds

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
    def __init__(self, project_tree, app=None, all_speakers=None):
        self.project_tree = project_tree
        self.app = app
        self.all_speakers = all_speakers or []
        self.params = {}  # CLI parameter overrides
        self.colors = ['#2563EB', '#DC2626', '#16A34A', '#9333EA', '#EA580C', '#0891B2', '#CA8A04', '#6366F1']
        self._speaker_data_cache = {}
        self._force_live_extract = False

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
            all_entries = self._extract_active_data(self.all_speakers if self.all_speakers else [self.active_speaker])
            group_counts = {}
            for entry in all_entries:
                g = entry['group']
                group_counts[g] = group_counts.get(g, 0) + 1
            self.available_groups = sorted(list(group_counts.keys()))

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
        fmt = self.get_param('format', 'png').lower()
        if 'svg' in fmt or 'pdf' in fmt:
            return 300

        pixel_mode = self.get_param('image_pixel_mode', '默认')
        custom_pixel = self.get_param('image_pixel_custom', 1080)

        if pixel_mode == '默认':
            return 300

        pixel_map = {
            "480 px": 480,
            "600 px": 600,
            "720 px": 720,
            "1080 px": 1080,
            "1440 px": 1440,
            "2160 px": 2160
        }
        return float(pixel_map.get(pixel_mode, custom_pixel))

    def _resolve_save_dpi(self, fig):
        target_min_pixels = self._get_save_dpi()
        if target_min_pixels == 300:
            return 300

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
            'chart_type': lambda: getattr(self, 'var_chart_type').get() if hasattr(self, 'var_chart_type') else None,
            'export_scope': lambda: getattr(self, 'var_export_scope').get() if hasattr(self, 'var_export_scope') else None,
            'groupby': lambda: getattr(self, 'combo_groupby').get() if hasattr(self, 'combo_groupby') else None,
            'scale': lambda: getattr(self, 'combo_scale').get() if hasattr(self, 'combo_scale') else None,
            'format': lambda: getattr(self, 'combo_format').get() if hasattr(self, 'combo_format') else None,
            'intention': lambda: getattr(self, 'combo_intention').get() if hasattr(self, 'combo_intention') else None,

            # contour specific
            'contour_x': lambda: getattr(self, 'combo_contour_x').get() if hasattr(self, 'combo_contour_x') else None,
            'contour_content': lambda: getattr(self, 'combo_contour_content').get() if hasattr(self, 'combo_contour_content') else None,
            'contour_facet': lambda: getattr(self, 'combo_contour_facet').get() if hasattr(self, 'combo_contour_facet') else None,

            # distribution specific
            'dist_type': lambda: getattr(self, 'combo_dist_type').get() if hasattr(self, 'combo_dist_type') else None,
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

            # overview specific
            'overview_metric': lambda: getattr(self, 'combo_overview_metric').get() if hasattr(self, 'combo_overview_metric') else None,
            'formant_overview_mode': lambda: getattr(self, 'combo_formant_overview_mode').get() if hasattr(self, 'combo_formant_overview_mode') else None,

            # formant space specific
            'formant_ellipse': lambda: getattr(self, 'combo_formant_ellipse').get() if hasattr(self, 'combo_formant_ellipse') else None,
            'formant_label_mode': lambda: getattr(self, 'combo_formant_label_mode').get() if hasattr(self, 'combo_formant_label_mode') else None,
            'formant_show_raw': lambda: getattr(self, 'var_formant_show_raw').get() if hasattr(self, 'var_formant_show_raw') else None,
            'formant_time_gradient': lambda: getattr(self, 'var_formant_time_gradient').get() if hasattr(self, 'var_formant_time_gradient') else None,
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

    def _get_legend_kwargs(self):
        loc_val = self.get_param('legend_loc', '右上')
        outside_val = self.get_param('legend_outside', False)
        
        loc_map = {
            "右上": "upper right",
            "右下": "lower right",
            "左上": "upper left",
            "左下": "lower left",
        }
        loc_str = loc_map.get(loc_val, "upper right")
        
        kwargs = {
            "markerscale": 0.55,
            "labelspacing": 0.65
        }
        
        if outside_val:
            if loc_val == "右上":
                kwargs.update({"loc": "upper left", "bbox_to_anchor": (1.02, 1)})
            elif loc_val == "右下":
                kwargs.update({"loc": "lower left", "bbox_to_anchor": (1.02, 0)})
            elif loc_val == "左上":
                kwargs.update({"loc": "upper right", "bbox_to_anchor": (-0.02, 1)})
            elif loc_val == "左下":
                kwargs.update({"loc": "lower right", "bbox_to_anchor": (-0.02, 0)})
        else:
            kwargs["loc"] = loc_str
            
        return kwargs

    # --- CORE DATA EXTRACTION ENGINE ---
    def _extract_active_data(self, speakers_list):
        analysis_mode = self.project_tree.app_state_params.get('analysis_mode', 'f0')
        if analysis_mode == 'formant':
            return self._extract_active_formant_data(speakers_list)
        num_points = self.project_tree.app_state_params.get('pts', 11)
        data_entries = []

        for speaker in speakers_list:
            if not getattr(self, '_force_live_extract', False) and hasattr(self, '_speaker_data_cache') and speaker in self._speaker_data_cache:
                data_entries.extend(self._speaker_data_cache[speaker])
                continue

            orig_items = self.project_tree.items
            self.project_tree.items = speaker.items

            s_struct = self.project_tree._get_items_by_group_for_dict(speaker.items)

            speaker_f0_pool = []
            speaker_items_temp = []

            for grp_name, children in s_struct:
                for child in children:
                    item = speaker.items[child]
                    self.project_tree._ensure_item_loaded(item)
                    if item.get('start') is None or not item.get('snd') or (not item.get('pitch') and not item.get('pitch_data')):
                        continue

                    total_dur, syl_data = self.project_tree._extract_syl_data(item, num_points)
                    if total_dur <= 0:
                        continue

                    p_xs, p_freqs = self.project_tree._get_pitch_arrays_for_item(item)
                    if p_xs is None or p_freqs is None:
                        continue

                    valid_f0_mask = p_freqs > 0
                    active_ratio = np.mean(valid_f0_mask) if len(p_freqs) > 0 else 0.0

                    warnings = item.get('warnings', [])

                    speaker_items_temp.append({
                        'speaker_name': speaker.name,
                        'group': grp_name,
                        'label': item.get('label', ''),
                        'total_dur': total_dur,
                        'syl_data': syl_data,
                        'raw_xs': p_xs,
                        'raw_freqs': p_freqs,
                        'active_ratio': active_ratio,
                        'warnings': warnings,
                        'raw_item': item
                    })

                    speaker_f0_pool.extend([f for f in p_freqs if f > 0])

            if speaker_f0_pool:
                s_min = np.percentile(speaker_f0_pool, 5.0)
                s_max = np.percentile(speaker_f0_pool, 95.0)
            else:
                s_min, s_max = 75.0, 600.0

            if s_max > s_min and s_min > 0:
                log_s_min = math.log10(s_min)
                log_s_max_min = math.log10(s_max) - log_s_min
            else:
                log_s_min = 0.0
                log_s_max_min = 1.0

            def _normalize_freqs(f_arr):
                f_arr = np.asarray(f_arr, dtype=float)
                norm_arr = np.full_like(f_arr, np.nan)
                valid = f_arr > 0
                if np.any(valid):
                    if s_max > s_min and s_min > 0:
                        norm_arr[valid] = np.clip(5 * (np.log10(f_arr[valid]) - log_s_min) / log_s_max_min, 0.0, 5.0)
                    else:
                        norm_arr[valid] = 3.0
                return norm_arr

            speaker_data_entries = []
            for entry in speaker_items_temp:
                normalized_syl_data = []
                for s_dur, freqs in entry['syl_data']:
                    norm_freqs = _normalize_freqs(freqs).tolist()
                    normalized_syl_data.append((s_dur, norm_freqs))

                entry['normalized_syl_data'] = normalized_syl_data
                entry['normalized_raw_freqs'] = _normalize_freqs(entry['raw_freqs'])
                speaker_data_entries.append(entry)

            self.project_tree.items = orig_items
            if hasattr(self, '_speaker_data_cache'):
                self._speaker_data_cache[speaker] = speaker_data_entries
            data_entries.extend(speaker_data_entries)

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

    def _get_group_key(self, groupby_val):
        if groupby_val in ("按词语", "label"):
            return 'label'
        if groupby_val in ("按发音人", "speaker"):
            return 'speaker_name'
        return 'group'

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

    def _build_overview_word_pages(self, data_entries, page_size=20):
        rows = self._build_overview_word_rows(data_entries)
        pages = []
        current_group = None
        current_page = []

        for row in rows:
            if row['group'] != current_group:
                current_group = row['group']
                current_page = []
            if not current_page:
                pages.append(current_page)
            current_page.append(row)
            if len(current_page) >= page_size:
                current_page = []

        return rows, pages

    def _get_group_pagination_state(self, data_entries, chart_type, groupby_val):
        group_key = self._get_group_key(groupby_val)
        unique_groups = self._ordered_unique(entry[group_key] for entry in data_entries)
        total_groups = len(unique_groups)
        is_paginated_heatmap = (
            self._is_overview_heatmap_chart(chart_type)
            and group_key == "label"
            and self.get_param('intention') == "附录图册 (完整数据)"
        )

        state = {
            'group_key': group_key,
            'unique_groups': unique_groups,
            'total_groups': total_groups,
            'is_paginated_heatmap': is_paginated_heatmap,
            'total_pages': 1,
            'pages': [],
        }

        if is_paginated_heatmap:
            _, pages = self._build_overview_word_pages(data_entries)
            state['pages'] = pages
            state['total_pages'] = len(pages) if pages else 1
        elif chart_type in ("formant_trajectory", "formant_density"):
            state['total_pages'] = 1
        else:
            state['total_pages'] = math.ceil(total_groups / 8) if total_groups > 0 else 1

        return state

    # --- ADVANCED SCIENTIFIC PLOTTING ENGINE ---
    def generate_plot(self, data_entries, is_preview=True):
        plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'Arial Unicode MS', 'sans-serif']
        plt.rcParams['axes.unicode_minus'] = False

        chart_type = self.get_param('chart_type', 'contour')
        groupby_val = self.get_param('groupby', 'group')
        scale_val = self.get_param('scale', 't_value')
        group_key = self._get_group_key(groupby_val)

        if not data_entries:
            fig, ax = plt.subplots(figsize=(6, 4))
            ax.text(0.5, 0.5, "没有找到有效的声调基频数据！\n请检查是否配置了发音人或导入了音频点。", ha='center', va='center', fontsize=12, color='red')
            ax.axis('off')
            return fig

        page_size = 8
        pagination_state = self._get_group_pagination_state(data_entries, chart_type, groupby_val)
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
            elif chart_type in ("formant_trajectory", "formant_density"):
                pass
            elif total_groups > page_size and not self._is_overview_heatmap_chart(chart_type):
                truncated = True
                start_idx = P * page_size
                end_idx = min(total_groups, start_idx + page_size)
                allowed_groups = set(unique_groups[start_idx:end_idx])
                data_entries = [e for e in data_entries if e[group_key] in allowed_groups]

        if scale_val and ("T" in str(scale_val) or "t_value" in str(scale_val).lower()):
            scale = "T 值"
        else:
            scale = "Hz"

        if chart_type == "contour":
            fig = self._plot_tone_contour(data_entries, group_key, scale)
        elif chart_type == "distribution":
            fig = self._plot_tone_distribution(data_entries, group_key, scale)
        elif chart_type == "density":
            fig = self._plot_temporal_density(data_entries, group_key, is_preview=is_preview)
        elif chart_type == "quality":
            fig = self._plot_quality_check(data_entries)
        elif chart_type == "overview_heatmap":
            fig = self._plot_tone_overview_heatmap(data_entries, group_key, scale)
        elif chart_type == "formant_overview_heatmap":
            fig = self._plot_formant_overview_heatmap(data_entries, group_key, scale)
        elif chart_type == "formant_space":
            fig = self._plot_formant_vowel_space(data_entries, group_key, scale, is_preview=is_preview)
        elif chart_type == "formant_trajectory":
            fig = self._plot_formant_trajectories(data_entries, group_key, scale)
        elif chart_type == "formant_density":
            fig = self._plot_formant_density_heatmap(data_entries, group_key, scale)
        else:
            fig, ax = plt.subplots()
            return fig

        # Apply aspect ratio resize
        ratio_mode = self.get_param('image_ratio_mode', '默认')
        custom_ratio = self.get_param('image_ratio_custom', 1.5)

        if ratio_mode != "默认":
            ratio_map = {
                "4:3": 4.0 / 3.0,
                "16:9": 16.0 / 9.0,
                "3:2": 3.0 / 2.0,
                "1:1": 1.0,
                "16:10": 16.0 / 10.0,
                "2:1": 2.0
            }
            R = ratio_map.get(ratio_mode, custom_ratio)
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

        if truncated:
            fig.subplots_adjust(top=0.88)
            if is_paginated_heatmap:
                current_page_rows = pagination_state['pages'][P] if P < len(pagination_state['pages']) else []
                tg_name = current_page_rows[0]['group'] if current_page_rows else ""
                fig.text(0.5, 0.96, f"[预览提示] 附录图册模式已自动分页。当前第 {P+1}/{total_pages} 页 (组别: {tg_name}，包含 {len(current_page_rows)} 个词语)。",
                         ha='center', va='center', fontsize=10, color='#991B1B', weight='bold',
                         bbox=dict(facecolor='#FEF2F2', edgecolor='#FCA5A5', boxstyle='round,pad=0.4'))
            else:
                fig.text(0.5, 0.96, f"[预览提示] 当前共 {total_groups} 组，分 {total_pages} 页显示。当前预览第 {P+1} 页（显示第 {start_idx+1}~{end_idx} 组）。导出时将自动分页/完整输出。",
                         ha='center', va='center', fontsize=10, color='#991B1B', weight='bold',
                         bbox=dict(facecolor='#FEF2F2', edgecolor='#FCA5A5', boxstyle='round,pad=0.4'))

        return fig

    def _plot_tone_contour(self, data_entries, group_key, scale):
        x_axis_val = self.get_param('contour_x', 'normalized')
        if x_axis_val in ("normalized", "归一化采样点"):
            x_axis = "归一化采样点"
        else:
            x_axis = "真实物理时长"

        content_val = self.get_param('contour_content', 'average')
        if content_val in ("average", "仅组别平均曲线"):
            content = "仅组别平均曲线"
        elif content_val in ("average_individual", "平均曲线 + 个体浅色细线"):
            content = "平均曲线 + 个体浅色细线"
        elif content_val in ("average_sd_ci", "平均曲线 + 置信区间阴影"):
            content = "平均曲线 + 置信区间阴影"
        else:
            content = "仅组别平均曲线"

        facet_val = self.get_param('contour_facet', 'none')
        if facet_val in ("group", "按声调类型分面"):
            facet = "按声调类型分面"
        elif facet_val in ("syllable_position", "按音节位置分面"):
            facet = "按音节位置分面"
        else:
            facet = "单图展示 (不分面)"

        grouped_data = {}
        for entry in data_entries:
            val = entry[group_key]
            if val not in grouped_data:
                grouped_data[val] = []
            grouped_data[val].append(entry)

        max_syls = max(len(e['syl_data']) for e in data_entries)
        num_points = self.project_tree.app_state_params.get('pts', 11)

        facet_keys = ["Default"]
        if facet == "按声调类型分面":
            facet_keys = sorted(list(set(e['group'] for e in data_entries)))
        elif facet == "按音节位置分面":
            facet_keys = [f"第 {k+1} 音节" for k in range(max_syls)]

        n_facets = len(facet_keys)
        n_cols = min(2, n_facets)
        n_rows = math.ceil(n_facets / n_cols)

        fig, axes = plt.subplots(n_rows, n_cols, figsize=(5 * n_cols + 2, 4.5 * n_rows + 0.5), squeeze=False, sharex=True, sharey=True)
        axes_flat = axes.flatten()

        for f_idx, f_key in enumerate(facet_keys):
            ax = axes_flat[f_idx]
            ax.grid(True, linestyle="--", alpha=0.3)

            facet_entries = data_entries
            if facet == "按声调类型分面":
                facet_entries = [e for e in data_entries if e['group'] == f_key]

            facet_grouped = {}
            for entry in facet_entries:
                val = entry[group_key]
                if val not in facet_grouped:
                    facet_grouped[val] = []
                facet_grouped[val].append(entry)

            for g_color_idx, (g_name, entries) in enumerate(facet_grouped.items()):
                color = self.colors[g_color_idx % len(self.colors)]

                syl_indices = [f_idx] if facet == "按音节位置分面" else list(range(max_syls))
                label_added = False

                for s_idx in syl_indices:
                    curves_x = []
                    curves_y = []

                    for entry in entries:
                        syl_list = entry['normalized_syl_data'] if "T 值" in scale else entry['syl_data']
                        if s_idx >= len(syl_list):
                            continue
                        
                        acc_dur = 0.0
                        for prev_idx in range(s_idx):
                            acc_dur += syl_list[prev_idx][0]
                            
                        s_dur, pts = syl_list[s_idx]

                        if x_axis == "归一化采样点":
                            x_pts = np.linspace(s_idx * num_points + 1, (s_idx + 1) * num_points, len(pts))
                        else:
                            x_pts = np.linspace(acc_dur, acc_dur + s_dur, len(pts))

                        curves_x.append(np.array(x_pts))
                        curves_y.append(np.array(pts))

                    if not curves_y:
                        continue

                    grid_x = np.linspace(np.min([np.min(cx) for cx in curves_x]), np.max([np.max(cx) for cx in curves_x]), num_points)

                    interpolated_ys = []
                    for cx, cy in zip(curves_x, curves_y):
                        valid = ~np.isnan(cy)
                        if np.sum(valid) >= 2:
                            iy = np.interp(grid_x, cx[valid], cy[valid])
                            interpolated_ys.append(iy)

                    if not interpolated_ys:
                        continue

                    mean_y = np.nanmean(interpolated_ys, axis=0)
                    std_y = np.nanstd(interpolated_ys, axis=0)

                    if "个体浅色" in content:
                        from matplotlib.collections import LineCollection
                        segments = [np.column_stack((grid_x, cy)) for cy in interpolated_ys]
                        lc = LineCollection(segments, colors=color, linewidths=0.6, alpha=0.18)
                        ax.add_collection(lc)
                    elif "置信区间" in content:
                        ax.fill_between(grid_x, mean_y - std_y, mean_y + std_y, color=color, alpha=0.15)

                    short_g_name = g_name
                    if len(g_name) > 12:
                        short_g_name = g_name[:10] + ".."
                    
                    lbl = short_g_name if not label_added else None
                    ax.plot(grid_x, mean_y, '-o', color=color, linewidth=2.5, markersize=5, label=lbl)
                    label_added = True

            title_text = "声调声学格局连贯图"
            if facet in ("按声调类型分面", "按音节位置分面"):
                title_text = f_key
            else:
                if len(set(e['speaker_name'] for e in data_entries)) == 1:
                    title_text = f"{data_entries[0]['speaker_name']} - 声学格局连贯图"
            if len(title_text) > 20:
                title_text = title_text[:17] + "..."
            ax.set_title(title_text, fontsize=12, fontweight="bold")

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

            is_bottom_row = (row_idx == n_rows - 1) or (f_idx + n_cols >= n_facets)
            if is_bottom_row:
                if x_axis == "归一化采样点":
                    ax.set_xlabel("音节测量点 (时序展开)")
                else:
                    ax.set_xlabel("时长 Duration (s)")
            else:
                ax.set_xlabel("")

            if max_syls > 1 and x_axis == "归一化采样点" and facet != "按音节位置分面":
                for k in range(1, max_syls):
                    ax.axvline(k * num_points + 0.5, color='gray', linestyle='--', alpha=0.5)

            if g_color_idx >= 0:
                legend_kwargs = self._get_legend_kwargs()
                legend_kwargs["fontsize"] = 8
                ax.legend(**legend_kwargs)

        for idx in range(n_facets, len(axes_flat)):
            axes_flat[idx].set_visible(False)

        fig.tight_layout()
        if self.get_param('legend_outside', False):
            loc_val = self.get_param('legend_loc', '右上')
            if "右" in loc_val:
                fig.subplots_adjust(right=0.82)
            elif "left" in loc_val or "左" in loc_val:
                fig.subplots_adjust(left=0.18)
        return fig

    def _plot_tone_overview_heatmap(self, data_entries, group_key, scale):
        metric_val = self.get_param('overview_metric', 'mean')
        if metric_val in ("sd", "标准差热图 (SD Map)"):
            metric = "标准差热图 (SD Map)"
        else:
            metric = "均值热图 (Mean Map)"

        max_syls = max(len(e['syl_data']) for e in data_entries)
        num_points = self.project_tree.app_state_params.get('pts', 11)
        total_points = max_syls * num_points

        grouped_data = {}
        for entry in data_entries:
            val = (entry['group'], entry['label']) if group_key == 'label' else entry[group_key]
            if val not in grouped_data:
                grouped_data[val] = []
            grouped_data[val].append(entry)

        if group_key == 'label':
            overview_rows = self._build_overview_word_rows(data_entries)
            groups_sorted = [row['row_id'] for row in overview_rows if row['row_id'] in grouped_data]
        else:
            if group_key == 'group':
                self._ensure_available_groups()
                group_order = self.available_groups
            else:
                group_order = self._ordered_unique(entry[group_key] for entry in data_entries)
            row_order = {value: idx for idx, value in enumerate(group_order)}
            groups_sorted = sorted(list(grouped_data.keys()), key=lambda value: row_order.get(value, 999999))

        if not groups_sorted:
            fig, ax = plt.subplots(figsize=(6, 4))
            ax.text(0.5, 0.5, "没有找到有效的声调数据用于生成概览图", ha='center', va='center')
            return fig

        matrix = []
        y_ticks = []
        y_labels = []
        
        last_tg = None
        current_row_idx = 0

        for g_name in groups_sorted:
            entries = grouped_data[g_name]

            if group_key == 'label':
                tg, label_name = g_name
                if last_tg is not None and tg != last_tg:
                    matrix.append(np.full(total_points, np.nan))
                    current_row_idx += 1
                last_tg = tg
            else:
                label_name = g_name

            vectors = []
            for entry in entries:
                syl_list = entry['normalized_syl_data'] if "T 值" in scale else entry['syl_data']
                y_flat = []
                for s_dur, pts in syl_list:
                    y_flat.extend(pts)
                if len(y_flat) < total_points:
                    y_flat.extend([np.nan] * (total_points - len(y_flat)))
                elif len(y_flat) > total_points:
                    y_flat = y_flat[:total_points]
                vectors.append(y_flat)

            if vectors:
                vectors = np.array(vectors)
                with np.errstate(all='ignore'):
                    if "均值" in metric:
                        row_vec = np.nanmean(vectors, axis=0)
                    else:
                        row_vec = np.nanstd(vectors, axis=0)
                if np.isnan(row_vec).all():
                    row_vec = np.zeros(total_points)
                matrix.append(row_vec)

                count = len(entries)
                y_ticks.append(current_row_idx)
                y_labels.append(f"{label_name} (N={count})")
                current_row_idx += 1

        matrix = np.array(matrix)

        fig_height = max(4, current_row_idx * 0.35 + 1.5)
        fig, ax = plt.subplots(figsize=(8, fig_height))

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
        current_cmap.set_bad(color='white', alpha=0.0)

        im = ax.imshow(matrix, cmap=current_cmap, aspect='auto', vmin=vmin, vmax=vmax)

        cbar = fig.colorbar(im, ax=ax, pad=0.02)
        if "均值" in metric:
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

        title_text = f"声调组别概览图 - {metric}"
        unique_entry_groups = set(e['group'] for e in data_entries)
        if len(unique_entry_groups) == 1:
            title_text += f" (组别: {list(unique_entry_groups)[0]})"
        if len(set(e['speaker_name'] for e in data_entries)) == 1:
            title_text = f"{data_entries[0]['speaker_name']} - {title_text}"
        ax.set_title(title_text, fontsize=12, fontweight="bold", pad=15)

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
        metric_val = self.get_param('overview_metric', 'mean')
        if metric_val in ("sd", "标准差热图 (SD Map)"):
            metric = "标准差热图 (SD Map)"
        else:
            metric = "均值热图 (Mean Map)"

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
            val = (entry['group'], entry['label']) if group_key == 'label' else entry[group_key]
            if val not in grouped_data:
                grouped_data[val] = []
            grouped_data[val].append(entry)

        if group_key == 'label':
            overview_rows = self._build_overview_word_rows(data_entries)
            groups_sorted = [row['row_id'] for row in overview_rows if row['row_id'] in grouped_data]
        else:
            if group_key == 'group':
                self._ensure_available_groups()
                group_order = self.available_groups
            else:
                group_order = self._ordered_unique(entry[group_key] for entry in data_entries)
            row_order = {value: idx for idx, value in enumerate(group_order)}
            groups_sorted = sorted(list(grouped_data.keys()), key=lambda value: row_order.get(value, 999999))

        if not groups_sorted:
            fig, ax = plt.subplots(figsize=(6, 4))
            ax.text(0.5, 0.5, "没有找到有效的共振峰数据用于生成概览图", ha='center', va='center')
            return fig

        normalization_mode = self.get_param('formant_normalization', '原始频率 (Hz)')
        transform_fn = self._build_formant_normalizer(data_entries, normalization_mode)

        matrix_f1 = []
        matrix_f2 = []
        matrix_ratio = []
        y_ticks = []
        y_labels = []
        last_tg = None
        current_row_idx = 0

        for g_name in groups_sorted:
            entries = grouped_data[g_name]
            if group_key == 'label':
                tg, label_name = g_name
                if last_tg is not None and tg != last_tg:
                    blank = np.full(total_points, np.nan)
                    matrix_f1.append(blank.copy())
                    matrix_f2.append(blank.copy())
                    matrix_ratio.append(blank.copy())
                    current_row_idx += 1
                last_tg = tg
            else:
                label_name = g_name

            vectors_f1 = []
            vectors_f2 = []
            vectors_ratio = []
            for entry in entries:
                f1_flat = []
                f2_flat = []
                ratio_flat = []

                syl_formants = entry.get('syl_formants', [])
                for syl_idx in range(max_syls):
                    if syl_idx < len(syl_formants):
                        syl = syl_formants[syl_idx]
                        f1_track = self._resample_formant_track(syl.get('f1', []), num_points)
                        f2_track = self._resample_formant_track(syl.get('f2', []), num_points)
                    else:
                        f1_track = np.full(num_points, np.nan)
                        f2_track = np.full(num_points, np.nan)

                    t_f1, t_f2 = transform_fn(f1_track, f2_track)
                    t_f1 = np.asarray(t_f1, dtype=float)
                    t_f2 = np.asarray(t_f2, dtype=float)

                    with np.errstate(divide='ignore', invalid='ignore'):
                        ratio_track = np.where((f1_track > 1e-6) & np.isfinite(f1_track) & np.isfinite(f2_track), f2_track / f1_track, np.nan)

                    f1_flat.extend(t_f1.tolist())
                    f2_flat.extend(t_f2.tolist())
                    ratio_flat.extend(ratio_track.tolist())

                vectors_f1.append(f1_flat[:total_points])
                vectors_f2.append(f2_flat[:total_points])
                vectors_ratio.append(ratio_flat[:total_points])

            with np.errstate(all='ignore'):
                vectors_f1 = np.asarray(vectors_f1, dtype=float)
                vectors_f2 = np.asarray(vectors_f2, dtype=float)
                vectors_ratio = np.asarray(vectors_ratio, dtype=float)
                if "均值" in metric:
                    row_f1 = np.nanmean(vectors_f1, axis=0)
                    row_f2 = np.nanmean(vectors_f2, axis=0)
                    row_ratio = np.nanmean(vectors_ratio, axis=0)
                else:
                    row_f1 = np.nanstd(vectors_f1, axis=0)
                    row_f2 = np.nanstd(vectors_f2, axis=0)
                    row_ratio = np.nanstd(vectors_ratio, axis=0)

            matrix_f1.append(row_f1)
            matrix_f2.append(row_f2)
            matrix_ratio.append(row_ratio)
            y_ticks.append(current_row_idx)
            y_labels.append(f"{label_name} (N={len(entries)})")
            current_row_idx += 1

        matrix_f1 = np.asarray(matrix_f1, dtype=float)
        matrix_f2 = np.asarray(matrix_f2, dtype=float)
        matrix_ratio = np.asarray(matrix_ratio, dtype=float)

        fig_height = max(4.0, current_row_idx * 0.35 + 1.5)

        if "Lobanov" in normalization_mode or "归一化" in normalization_mode or "L-归一化" in normalization_mode:
            unit_text = "Z-score"
        else:
            unit_text = "Hz"

        if "均值" in metric:
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
            cmap = _copy_cmap(ratio_cmap)
            cmap.set_bad(color='white', alpha=0.0)
            im = ax.imshow(matrix_ratio, cmap=cmap, aspect='auto', vmin=vmin_ratio, vmax=vmax_ratio)
            cbar = fig.colorbar(im, ax=ax, pad=0.02)
            if "均值" in metric:
                cbar.set_label("平均 F2/F1 比值")
            else:
                cbar.set_label("F2/F1 比值标准差 (SD)")
            axes_to_label = [ax]
        else:
            fig, axes = plt.subplots(2, 1, figsize=(8.6, max(5.6, fig_height + 1.2)), sharex=True)
            ax_f1, ax_f2 = axes

            cmap_f1 = _copy_cmap(cmap_main)
            cmap_f2 = _copy_cmap(cmap_main)
            cmap_f1.set_bad(color='white', alpha=0.0)
            cmap_f2.set_bad(color='white', alpha=0.0)

            im1 = ax_f1.imshow(matrix_f1, cmap=cmap_f1, aspect='auto', vmin=vmin_main, vmax=vmax_main)
            im2 = ax_f2.imshow(matrix_f2, cmap=cmap_f2, aspect='auto', vmin=vmin_main, vmax=vmax_main)
            cb1 = fig.colorbar(im1, ax=ax_f1, pad=0.02)
            cb2 = fig.colorbar(im2, ax=ax_f2, pad=0.02)
            if "均值" in metric:
                cb1.set_label(f"平均 F1 ({unit_text})")
                cb2.set_label(f"平均 F2 ({unit_text})")
            else:
                cb1.set_label(f"F1 标准差 ({unit_text})")
                cb2.set_label(f"F2 标准差 ({unit_text})")
            ax_f1.set_title(f"F1 轨迹热图 - {metric}", fontsize=11, fontweight="bold", pad=10)
            ax_f2.set_title(f"F2 轨迹热图 - {metric}", fontsize=11, fontweight="bold", pad=10)
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
        title_text = f"共振峰组别概览图 - {title_mode_text} - {metric}"
        unique_entry_groups = set(e['group'] for e in data_entries)
        if len(unique_entry_groups) == 1:
            title_text += f" (组别: {list(unique_entry_groups)[0]})"
        if len(set(e['speaker_name'] for e in data_entries)) == 1:
            title_text = f"{data_entries[0]['speaker_name']} - {title_text}"
        fig.suptitle(title_text, fontsize=12, fontweight="bold", y=0.995)

        fig.tight_layout(rect=[0, 0, 1, 0.98])
        return fig

    def _plot_tone_distribution(self, data_entries, group_key, scale):
        dist_type_val = self.get_param('dist_type', 'boxplot_violin')
        if dist_type_val in ("start_mid_end", "起-中-终三点比较"):
            dist_type = "起-中-终三点比较"
        elif dist_type_val in ("range", "调域范围跨度图"):
            dist_type = "调域范围跨度图"
        elif dist_type_val in ("variability", "变异程度(CV)比较"):
            dist_type = "变异程度(CV)比较"
        else:
            dist_type = "测量点精细分布"

        style_val = self.get_param('dist_style', 'boxplot')
        if style_val and ("violin" in str(style_val).lower() or "小提琴" in str(style_val)):
            style = "小提琴图 (Violin Plot)"
        else:
            style = "科学箱线图 (Box Plot)"

        grouped_data = {}
        for entry in data_entries:
            val = entry[group_key]
            if val not in grouped_data:
                grouped_data[val] = []
            grouped_data[val].append(entry)

        num_points = self.project_tree.app_state_params.get('pts', 11)
        max_syls = max(len(e['syl_data']) for e in data_entries)
        n_groups = len(grouped_data)

        if "测量点精细分布" in dist_type:
            n_cols = min(2, n_groups)
            n_rows = math.ceil(n_groups / n_cols)
            fig, axes = plt.subplots(n_rows, n_cols, figsize=(5 * n_cols + 2, 4.2 * n_rows + 0.5), squeeze=False, sharex=True, sharey=True)
            axes_flat = axes.flatten()

            for idx, (g_name, entries) in enumerate(grouped_data.items()):
                ax = axes_flat[idx]
                ax.grid(True, linestyle="--", alpha=0.3)

                pts_data = []
                for entry in entries:
                    y_series = []
                    syl_list = entry['normalized_syl_data'] if "T 值" in scale else entry['syl_data']
                    for s_dur, pts in syl_list:
                        y_series.extend(pts)
                    if len(y_series) == max_syls * num_points:
                        pts_data.append(y_series)

                if not pts_data:
                    ax.text(0.5, 0.5, "数据不完整，音节数不一致", ha='center', va='center', color='red')
                    continue

                pts_data = np.array(pts_data)
                positions = np.arange(1, pts_data.shape[1] + 1)

                if "小提琴图" in style:
                    cleaned_columns = []
                    for col_idx in range(pts_data.shape[1]):
                        col = pts_data[:, col_idx]
                        cleaned_columns.append(col[~np.isnan(col)])
                    ax.violinplot(cleaned_columns, positions, showmeans=True, showmedians=False)
                else:
                    cleaned_columns = []
                    for col_idx in range(pts_data.shape[1]):
                        col = pts_data[:, col_idx]
                        cleaned_columns.append(col[~np.isnan(col)])
                    ax.boxplot(cleaned_columns, positions=positions, patch_artist=True,
                               boxprops=dict(facecolor="#DBEAFE", color="#1E40AF"),
                               whiskerprops=dict(color="#1E40AF"),
                               capprops=dict(color="#1E40AF"),
                               medianprops=dict(color="#DC2626", linewidth=1.5))

                ax.set_title(f"{g_name} 基频测量点分布", fontsize=11, fontweight="bold")
                if "T 值" in scale:
                    ax.set_ylim(-0.2, 5.2)
                    ax.set_yticks([0, 1, 2, 3, 4, 5])
                ax.set_xticks(positions)
                ax.set_xticklabels([str((p-1)%num_points + 1) for p in positions], fontsize=8)

            for idx in range(n_groups, len(axes_flat)):
                axes_flat[idx].set_visible(False)

            fig.tight_layout()
            return fig

        elif "起-中-终" in dist_type:
            fig, ax = plt.subplots(figsize=(8, 5))
            ax.grid(True, linestyle="--", alpha=0.3)

            data_to_plot = []
            labels = []
            colors_to_use = []

            for g_color_idx, (g_name, entries) in enumerate(grouped_data.items()):
                start_pts, mid_pts, end_pts = [], [], []
                color = self.colors[g_color_idx % len(self.colors)]

                for entry in entries:
                    y_series = []
                    syl_list = entry['normalized_syl_data'] if "T 值" in scale else entry['syl_data']
                    for s_dur, pts in syl_list:
                        y_series.extend(pts)

                    if len(y_series) >= 3:
                        y_series = np.array(y_series)
                        valid_ys = y_series[~np.isnan(y_series)]
                        if len(valid_ys) >= 3:
                            start_pts.append(valid_ys[0])
                            mid_pts.append(valid_ys[len(valid_ys) // 2])
                            end_pts.append(valid_ys[-1])

                if start_pts:
                    data_to_plot.extend([start_pts, mid_pts, end_pts])
                    labels.extend([f"{g_name}\n起点", f"{g_name}\n中点", f"{g_name}\n终点"])
                    colors_to_use.extend([color, color, color])

            if data_to_plot:
                if "小提琴图" in style:
                    parts = ax.violinplot(data_to_plot, showmeans=True)
                    for pc_idx, pc in enumerate(parts['bodies']):
                        pc.set_facecolor(colors_to_use[pc_idx])
                        pc.set_alpha(0.6)
                else:
                    bp = ax.boxplot(data_to_plot, patch_artist=True)
                    for patch, color in zip(bp['boxes'], colors_to_use):
                        patch.set_facecolor(color)
                        patch.set_alpha(0.6)

                ax.set_xticklabels(labels, fontsize=9)
                ax.set_title("各声调起-中-终三点基频离散比较", fontsize=13, fontweight="bold")
                if "T 值" in scale:
                    ax.set_ylim(-0.2, 5.2)
                    ax.set_yticks([0, 1, 2, 3, 4, 5])

            return fig

        elif "调域范围" in dist_type:
            fig, ax = plt.subplots(figsize=(8, 5))
            ax.grid(True, linestyle="--", alpha=0.3)

            y_positions = np.arange(n_groups)
            group_names = []

            for idx, (g_name, entries) in enumerate(grouped_data.items()):
                group_names.append(g_name)
                color = self.colors[idx % len(self.colors)]

                min_vals = []
                max_vals = []
                for entry in entries:
                    y_series = []
                    syl_list = entry['normalized_syl_data'] if "T 值" in scale else entry['syl_data']
                    for s_dur, pts in syl_list:
                        y_series.extend(pts)
                    y_series = np.array(y_series)
                    valid_ys = y_series[~np.isnan(y_series)]
                    if len(valid_ys) > 0:
                        min_vals.append(np.min(valid_ys))
                        max_vals.append(np.max(valid_ys))

                if min_vals:
                    avg_min = np.mean(min_vals)
                    avg_max = np.mean(max_vals)
                    ax.barh(idx, avg_max - avg_min, left=avg_min, height=0.5, color=color, alpha=0.7, edgecolor=color, align='center')
                    ax.plot([avg_min, avg_max], [idx, idx], '|', color='black', markersize=10, markeredgewidth=2)
                    ax.text((avg_min + avg_max)/2, idx, f"{avg_min:.2f} ~ {avg_max:.2f}\n(调域: {avg_max-avg_min:.2f})",
                            ha='center', va='center', color='black', fontsize=9, fontweight='bold')

            ax.set_yticks(y_positions)
            ax.set_yticklabels(group_names, fontsize=11, fontweight="bold")
            ax.set_title("各声调调域范围图 (最高点 / 最低点 / 调域跨度)", fontsize=13, fontweight="bold")

            if "T 值" in scale:
                ax.set_xlim(-0.2, 5.2)
                ax.set_xticks([0, 1, 2, 3, 4, 5])
                ax.set_xlabel("T 值域区间")
            else:
                ax.set_xlabel("绝对频率范围 (Hz)")

            return fig

        elif "变异程度" in dist_type:
            fig, ax = plt.subplots(figsize=(8, 5))
            ax.grid(True, linestyle="--", alpha=0.3)

            group_names = []
            cv_values = []
            colors_to_use = []

            for idx, (g_name, entries) in enumerate(grouped_data.items()):
                group_names.append(g_name)
                color = self.colors[idx % len(self.colors)]
                colors_to_use.append(color)

                all_pts_flat = []
                for entry in entries:
                    y_series = []
                    syl_list = entry['normalized_syl_data'] if "T 值" in scale else entry['syl_data']
                    for s_dur, pts in syl_list:
                        y_series.extend(pts)
                    y_series = np.array(y_series)
                    valid_ys = y_series[~np.isnan(y_series)]
                    all_pts_flat.extend(valid_ys.tolist())

                if all_pts_flat:
                    mean_val = np.mean(all_pts_flat)
                    std_val = np.nanstd(all_pts_flat)
                    cv = (std_val / mean_val) if mean_val > 0 else 0.0
                    cv_values.append(cv)
                else:
                    cv_values.append(0.0)

            ax.bar(group_names, cv_values, color=colors_to_use, alpha=0.75, width=0.45)

            for idx, val in enumerate(cv_values):
                ax.text(idx, val + 0.005, f"{val:.1%}", ha='center', va='bottom', fontweight='bold')

            ax.set_title("各声调内部发音变异系数 (Coefficient of Variation) 比较", fontsize=12, fontweight="bold")
            ax.set_ylabel("变异系数 CV (SD / Mean)")
            ax.set_xlabel("声调类别")

            return fig

        fig, ax = plt.subplots()
        return fig

    def _plot_temporal_density(self, data_entries, group_key, is_preview=True):
        bw_method = float(self.get_param('density_bw', 0.15))
        f0_mode_val = self.get_param('density_f0_mode', 'percentile')
        facet_val = self.get_param('density_facet', 'group')
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

        if facet_val in ("none", "不分面 (混合叠加)"):
            facet = "不分面 (混合叠加)"
        elif facet_val in ("label", "按词语分面"):
            facet = "按词语分面"
        else:
            facet = "声调类型分面 (默认)"

        grouped_data = {}
        for entry in data_entries:
            val = entry[group_key]
            if val not in grouped_data:
                grouped_data[val] = []
            grouped_data[val].append(entry)

        n_groups = len(grouped_data)
        max_syls = max(len(e['syl_data']) for e in data_entries)
        N_DENSE = 100

        def empty_density_fig(message):
            fig, ax = plt.subplots()
            ax.text(0.5, 0.5, message, ha='center', va='center')
            return fig

        def extract_normalized_contour(entry, c_s, c_e):
            p_xs = np.asarray(entry.get('raw_xs', []), dtype=float)
            p_freqs = np.asarray(entry.get('normalized_raw_freqs', []), dtype=float)
            if len(p_xs) != len(p_freqs) or len(p_xs) == 0:
                return None

            valid = (p_xs >= c_s) & (p_xs <= c_e) & np.isfinite(p_freqs)
            if np.sum(valid) < 2:
                return None

            x_valid = p_xs[valid]
            y_valid = p_freqs[valid]
            order = np.argsort(x_valid)
            x_valid = x_valid[order]
            y_valid = y_valid[order]

            unique_x, unique_idx = np.unique(x_valid, return_index=True)
            if len(unique_x) < 2:
                return None
            y_valid = y_valid[unique_idx]

            dense_x = np.linspace(c_s, c_e, N_DENSE)
            dense_y = np.interp(dense_x, unique_x, y_valid)

            for idx, x_val in enumerate(dense_x):
                nearest = np.min(np.abs(unique_x - x_val))
                if nearest > 0.025:
                    dense_y[idx] = np.nan

            return dense_y

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
                hz_array = np.asarray(hz_array, dtype=float)
                if max_f0 == min_f0 or min_f0 <= 0 or max_f0 <= min_f0:
                    return np.full_like(hz_array, 3.0)
                hz_val = np.clip(hz_array, min_f0, max_f0)
                return 5 * (np.log(hz_val) - np.log(min_f0)) / (np.log(max_f0) - np.log(min_f0))
        elif not any(np.any(np.isfinite(entry.get('normalized_raw_freqs', []))) for entry in data_entries):
            return empty_density_fig("没有有效归一化基频点可进行 KDE 计算")

        facet_keys = ["Default"]
        if facet == "声调类型分面 (默认)":
            facet_keys = sorted(list(set(e['group'] for e in data_entries)))
        elif facet == "按词语分面":
            facet_keys = sorted(list(set(e['label'] for e in data_entries)))

        n_facets = len(facet_keys)
        n_cols = min(2, n_facets)
        n_rows = math.ceil(n_facets / n_cols)

        fig, axes = plt.subplots(n_rows, n_cols, figsize=(5 * max_syls * n_cols + 1, 4.5 * n_rows + 0.5), squeeze=False, sharex=True, sharey=True)
        axes_flat = axes.flatten()

        for f_idx, f_key in enumerate(facet_keys):
            self._check_export_cancelled()
            if n_facets > 0:
                self._report_export_progress(0.35 + 0.45 * (f_idx / n_facets), f"正在计算 KDE 分面 {f_idx + 1}/{n_facets}...")
            ax = axes_flat[f_idx]

            facet_entries = data_entries
            if facet == "声调类型分面 (默认)":
                facet_entries = [e for e in data_entries if e['group'] == f_key]
            elif facet == "按词语分面":
                facet_entries = [e for e in data_entries if e['label'] == f_key]

            X_all_list, Y_all_list = [], []
            for e_idx, entry in enumerate(facet_entries):
                if (e_idx % 8) == 0:
                    self._check_export_cancelled()
                syl_bounds = self.project_tree._get_syllables_and_bounds(entry['raw_item'])[1]
                for s_idx, (c_s, c_e) in enumerate(syl_bounds):
                    if normalization == "speaker":
                        y_t_dense = extract_normalized_contour(entry, c_s, c_e)
                    else:
                        y_dense = self.project_tree._extract_kde_contour(entry['raw_xs'], entry['raw_freqs'], c_s, c_e, N_DENSE)
                        y_t_dense = hz_to_t(y_dense) if y_dense is not None else None
                    if y_t_dense is not None:
                        x_dense = np.linspace(s_idx * 100, (s_idx + 1) * 100, N_DENSE)
                        valid = np.isfinite(y_t_dense)
                        X_all_list.append(x_dense[valid])
                        Y_all_list.append(y_t_dense[valid])

            if not X_all_list:
                ax.text(0.5, 0.5, "没有足够的有效数据点", ha='center', va='center')
                continue

            xmin, xmax = 0, max_syls * 100
            ymin, ymax = -0.5, 5.5

            x_arr = np.concatenate(X_all_list)
            y_arr = np.concatenate(Y_all_list)
            if len(x_arr) == 0:
                ax.text(0.5, 0.5, "没有足够的有效数据点", ha='center', va='center')
                continue
            high_prec = (not is_preview) or bool(self.get_param('high_precision', False))
            if high_prec:
                max_kde_points = int(self.get_param('density_max_points', 12000))
            else:
                max_kde_points = 3000

            if len(x_arr) > max_kde_points and max_kde_points > 0:
                sample_idx = np.linspace(0, len(x_arr) - 1, max_kde_points, dtype=int)
                x_arr = x_arr[sample_idx]
                y_arr = y_arr[sample_idx]

            positions = np.vstack([x_arr, y_arr])
            try:
                self._check_export_cancelled()
                kernel = gaussian_kde(positions, bw_method=bw_method)
                if high_prec:
                    grid_x = max(120, min(240, int(80 * max_syls)))
                    grid_y = 90
                else:
                    grid_x = max(60, min(120, int(40 * max_syls)))
                    grid_y = 50
                xi, yi = np.mgrid[xmin:xmax:complex(0, grid_x), ymin:ymax:complex(0, grid_y)]
                zi = kernel(np.vstack([xi.flatten(), yi.flatten()]))
                zi = zi.reshape(xi.shape)

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
                ax.set_ylabel("T 值 (0-5 标度)")
            else:
                ax.set_ylabel("")

            is_bottom_row = (row_idx == n_rows - 1) or (f_idx + n_cols >= n_facets)
            if is_bottom_row:
                ticks, labels = [], []
                for k in range(max_syls):
                    ticks.append(k * 100 + 50)
                    labels.append(f"第 {k+1} 字\n(0-100%)")
                ax.set_xticks(ticks)
                ax.set_xticklabels(labels, fontsize=9)
            else:
                ticks = []
                for k in range(max_syls):
                    ticks.append(k * 100 + 50)
                ax.set_xticks(ticks)
                ax.set_xticklabels([])

            ax.set_ylim(-0.5, 5.5)
            ax.set_yticks([0, 1, 2, 3, 4, 5])

            title_text = f_key if f_key != "Default" else "时序密度热力图"
            if len(title_text) > 20:
                title_text = title_text[:17] + "..."
            ax.set_title(title_text, fontsize=12, fontweight="bold")

        for idx in range(n_facets, len(axes_flat)):
            axes_flat[idx].set_visible(False)

        fig.tight_layout()
        return fig

    def _plot_quality_check(self, data_entries):
        view_val = self.get_param('qc_view', 'raw_overlay')
        if view_val:
            if "active_ratio" in str(view_val).lower() or "有效点" in str(view_val):
                view = "active_ratio"
            elif "speaker_means" in str(view_val).lower() or "发音人" in str(view_val):
                view = "speaker_means"
            else:
                view = "raw_overlay"
        else:
            view = "raw_overlay"

        scale_val = self.get_param('scale')
        is_t_value = scale_val and ("T" in str(scale_val) or "t_value" in str(scale_val).lower())

        if view == "raw_overlay":
            fig, ax = plt.subplots(figsize=(9, 5))
            ax.grid(True, linestyle="--", alpha=0.3)

            max_syls = max(len(e['syl_data']) for e in data_entries)
            num_points = self.project_tree.app_state_params.get('pts', 11)

            normal_lines = []
            outlier_lines = []

            for entry in data_entries:
                y_series = entry['normalized_syl_data'] if is_t_value else entry['syl_data']
                y_flat = []
                x_flat = []

                for s_idx, (s_dur, pts) in enumerate(y_series):
                    x_pts = np.linspace(s_idx * num_points + 1, (s_idx + 1) * num_points, len(pts))
                    x_flat.extend(x_pts)
                    y_flat.extend(pts)

                y_flat = np.array(y_flat)
                x_flat = np.array(x_flat)

                has_warning = any(w.startswith("[警告]") or w.startswith("[致命]") for w in entry.get('warnings', []))

                seg = np.column_stack((x_flat, y_flat))
                if has_warning:
                    outlier_lines.append(seg)
                else:
                    normal_lines.append(seg)

            from matplotlib.collections import LineCollection
            if normal_lines:
                lc_norm = LineCollection(normal_lines, colors="#3B82F6", linewidths=0.75, alpha=0.3)
                ax.add_collection(lc_norm)
                ax.plot([], [], color="#3B82F6", linewidth=0.75, alpha=0.3, label="质量良好的常规发音")
            if outlier_lines:
                lc_out = LineCollection(outlier_lines, colors="#EF4444", linewidths=1.2, alpha=0.75, linestyles="--")
                ax.add_collection(lc_out)
                ax.plot([], [], color="#EF4444", linewidth=1.2, alpha=0.75, linestyle="--", label="存在质量异常的发音")

            if max_syls > 1:
                for k in range(1, max_syls):
                    ax.axvline(k * num_points + 0.5, color='gray', linestyle='--', alpha=0.5)

            ax.set_title("数据质量分析：逐项基频曲线质量分布叠加", fontsize=13, fontweight="bold")
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
            ax.grid(True, linestyle="--", alpha=0.3)

            spk_data = {}
            for entry in data_entries:
                spk = entry['speaker_name']
                if spk not in spk_data:
                    spk_data[spk] = []
                spk_data[spk].append(entry['active_ratio'])

            labels = []
            data_to_plot = []
            for spk, ratios in spk_data.items():
                labels.append(spk)
                data_to_plot.append(ratios)

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
                ax.set_ylabel("有效基频采样点比例 (Active Ratio)", fontsize=11)
                ax.set_title("各发音人录音有效率与基频检出比例分布比较", fontsize=13, fontweight="bold")

                ax.axhline(0.60, color="#EF4444", linestyle=":", label="常规建议的极低阈值 (60%)")
                legend_kwargs = self._get_legend_kwargs()
                ax.legend(**legend_kwargs)
                ax.set_ylim(-0.05, 1.05)
                if self.get_param('legend_outside', False):
                    loc_val = self.get_param('legend_loc', '右上')
                    if "右" in loc_val:
                        fig.subplots_adjust(right=0.82)
                    elif "left" in loc_val or "左" in loc_val:
                        fig.subplots_adjust(left=0.18)

            return fig

        elif view == "speaker_means":
            fig, ax = plt.subplots(figsize=(9, 5))
            ax.grid(True, linestyle="--", alpha=0.3)

            for idx, entry in enumerate(data_entries):
                spk = entry['speaker_name']
                color = self.colors[hash(spk) % len(self.colors)]

                valid_ys = entry['raw_freqs'][entry['raw_freqs'] > 0]
                if len(valid_ys) > 0:
                    mean_f0 = np.mean(valid_ys)
                    ax.scatter(spk, mean_f0, color=color, alpha=0.4, edgecolors='none', s=40)

            spk_freqs_pool = {}
            for entry in data_entries:
                spk = entry['speaker_name']
                valid_ys = entry['raw_freqs'][entry['raw_freqs'] > 0]
                if len(valid_ys) > 0:
                    if spk not in spk_freqs_pool:
                        spk_freqs_pool[spk] = []
                    spk_freqs_pool[spk].append(np.mean(valid_ys))

            for idx, (spk, means) in enumerate(spk_freqs_pool.items()):
                med = np.median(means)
                q1 = np.percentile(means, 25)
                q3 = np.percentile(means, 75)
                color = self.colors[hash(spk) % len(self.colors)]
                ax.errorbar(spk, med, yerr=[[med - q1], [q3 - med]], fmt='D', color='black', ecolor=color, elinewidth=3, capsize=8, label=f"{spk} 中位数 ({med:.1f}Hz)" if idx < 5 else "")

            ax.set_title("各受试发音人基频均值及调域离散域 (用于快速排查八度音高跳变)", fontsize=12, fontweight="bold")
            ax.set_ylabel("发音基频均值 Mean F0 (Hz)")
            legend_kwargs = self._get_legend_kwargs()
            legend_kwargs["fontsize"] = 9
            ax.legend(**legend_kwargs)
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
            plt.close(fig)

    def _export_dataset(self, data, out_path, ext):
        self._check_export_cancelled()
        self._report_export_progress(0.05, "正在准备导出数据...")
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

        if len(unique_groups) > 8 and not self._is_overview_heatmap_chart(chart_type):
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

    def _extract_active_formant_data(self, speakers_list):
        num_points = self.project_tree.app_state_params.get('pts', 11)
        data_entries = []

        for speaker in speakers_list:
            if not getattr(self, '_force_live_extract', False) and hasattr(self, '_speaker_data_cache') and speaker in self._speaker_data_cache:
                data_entries.extend(self._speaker_data_cache[speaker])
                continue

            orig_items = self.project_tree.items
            self.project_tree.items = speaker.items

            s_struct = self.project_tree._get_items_by_group_for_dict(speaker.items)
            speaker_data_entries = []

            for grp_name, children in s_struct:
                for child in children:
                    item = speaker.items[child]
                    self.project_tree._ensure_item_loaded(item)
                    if item.get('start') is None or not item.get('snd') or not item.get('formant_data'):
                        continue

                    # Get bounds and split into syllables
                    bounds = get_item_syllable_bounds(item)
                    label = item.get('label', '')
                    syls = split_into_syllables(label)

                    # Get sample strategy
                    strategy = item.get('formant_sample_strategy')
                    if not strategy and hasattr(self.app, 'last_params'):
                        strategy = self.app.last_params.get('formant_sample_strategy', '整段11点')
                    if not strategy:
                        strategy = '整段11点'

                    times, f1_vals, f2_vals = sample_formant_points_by_bounds(item, bounds, num_points, strategy)

                    # Formant data is syllable-level structured
                    syl_formants = []
                    for idx_syl, (c_s, c_e) in enumerate(bounds):
                        char = syls[idx_syl] if idx_syl < len(syls) else f"字{idx_syl+1}"
                        s_idx = idx_syl * num_points
                        e_idx = s_idx + num_points
                        s_times = times[s_idx:e_idx]
                        s_f1 = f1_vals[s_idx:e_idx]
                        s_f2 = f2_vals[s_idx:e_idx]
                        syl_formants.append({
                            'syllable_index': idx_syl,
                            'char': char,
                            'bounds': [c_s, c_e],
                            'times': s_times,
                            'f1': s_f1,
                            'f2': s_f2
                        })

                    warnings = item.get('warnings', [])

                    speaker_data_entries.append({
                        'speaker_name': speaker.name,
                        'group': grp_name,
                        'label': label,
                        'syl_formants': syl_formants,
                        'raw_xs': item['formant_data'].get('xs', []),
                        'raw_f1': item['formant_data'].get('f1', []),
                        'raw_f2': item['formant_data'].get('f2', []),
                        'warnings': warnings,
                        'raw_item': item
                    })

            self.project_tree.items = orig_items
            if hasattr(self, '_speaker_data_cache'):
                self._speaker_data_cache[speaker] = speaker_data_entries
            data_entries.extend(speaker_data_entries)

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
        if groupby_val in ("按词语", "label"):
            return entry['label']
        if groupby_val in ("按单字/音节", "syl_char"):
            return syl['char']
        if groupby_val in ("按发音人", "speaker"):
            return entry['speaker_name']
        return entry['group']

    def _draw_confidence_ellipse(self, x, y, ax, n_std=1.0, facecolor='none', **kwargs):
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
            if len(x) >= 10:
                x_lo, x_hi = np.percentile(x, [5, 95])
                y_lo, y_hi = np.percentile(y, [5, 95])
                core = (x >= x_lo) & (x <= x_hi) & (y >= y_lo) & (y <= y_hi)
                if np.sum(core) >= 3:
                    x = x[core]
                    y = y[core]
            cov = np.cov(x, y)
            if np.any(np.isnan(cov)) or np.any(np.isinf(cov)):
                return None
            
            vals, vecs = np.linalg.eigh(cov)
            order = vals.argsort()[::-1]
            vals, vecs = vals[order], vecs[:, order]
            
            theta = np.degrees(np.arctan2(*vecs[:, 0][::-1]))
            width, height = 2 * n_std * np.sqrt(np.maximum(vals, 0))
            
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

    def _get_formant_density_points(self, data_entries):
        all_f1_list, all_f2_list, all_tau_list = [], [], []
        for entry in data_entries:
            xs = np.asarray(entry.get('raw_xs', []), dtype=float)
            f1_arr = np.asarray(entry.get('raw_f1', []), dtype=float)
            f2_arr = np.asarray(entry.get('raw_f2', []), dtype=float)
            if len(xs) == 0 or len(f1_arr) == 0 or len(f2_arr) == 0:
                continue
            for syl in entry.get('syl_formants', []):
                c_s, c_e = syl['bounds']
                if c_e <= c_s:
                    continue
                mask = (xs >= c_s) & (xs <= c_e) & np.isfinite(f1_arr) & np.isfinite(f2_arr) & (f2_arr > f1_arr)
                if not np.any(mask):
                    continue
                s_tau = np.clip((xs[mask] - c_s) / (c_e - c_s), 0.0, 1.0)
                all_f1_list.append(f1_arr[mask])
                all_f2_list.append(f2_arr[mask])
                all_tau_list.append(s_tau)

        if not all_f1_list:
            return np.array([]), np.array([]), np.array([])

        return np.concatenate(all_f1_list), np.concatenate(all_f2_list), np.concatenate(all_tau_list)

    def _draw_formant_density_layer(self, ax, data_entries, show_raw=False, show_contours=True, bw_method=None, alpha_max=0.58, zorder=1, transform_fn=None, is_preview=True):
        self._report_export_progress(0.24, "正在收集共振峰时空密度数据...")
        all_f1, all_f2, all_tau = self._get_formant_density_points(data_entries)
        if len(all_f1) < 4:
            return None, [], []

        finite_mask = np.isfinite(all_f1) & np.isfinite(all_f2) & np.isfinite(all_tau)
        all_f1, all_f2, all_tau = all_f1[finite_mask], all_f2[finite_mask], all_tau[finite_mask]
        if len(all_f1) < 4:
            return None, [], []

        if transform_fn is not None:
            all_f1, all_f2 = transform_fn(all_f1, all_f2)

        f1_p1, f1_p99 = np.percentile(all_f1, 1.0), np.percentile(all_f1, 99.0)
        f2_p1, f2_p99 = np.percentile(all_f2, 1.0), np.percentile(all_f2, 99.0)
        
        norm_mode = self.get_param('formant_normalization', '原始频率 (Hz)')
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

        D_max = float(np.nanmax(D)) if np.size(D) else 0.0
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
        ax.set_facecolor("#F8FAFC")
        ax.grid(True, linestyle="--", alpha=0.25, linewidth=0.8)

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
                
                if len(xs) == 0 or len(f1_arr) == 0 or len(f2_arr) == 0:
                    continue
                    
                mask = (xs >= c_s) & (xs <= c_e) & np.isfinite(f1_arr) & np.isfinite(f2_arr) & (f2_arr > f1_arr)
                s_f1 = f1_arr[mask]
                s_f2 = f2_arr[mask]
                
                if len(s_f1) == 0:
                    continue
                
                if transform_fn is not None:
                    s_f1, s_f2 = transform_fn(s_f1, s_f2)
                
                cat = self._get_syl_category(entry, syl, groupby_val)
                if cat not in category_data:
                    category_data[cat] = {
                        'f1': [], 'f2': [], 'entries_labels': [], 'syl_chars': [],
                        'trajs_f1': [], 'trajs_f2': []
                    }
                
                category_data[cat]['f1'].append(s_f1)
                category_data[cat]['f2'].append(s_f2)
                category_data[cat]['entries_labels'].append(entry['label'])
                category_data[cat]['syl_chars'].append(syl['char'])
                
                # Collect normalized trajectory data (typically 11 points)
                traj_f1 = np.asarray(syl.get('f1', []), dtype=float)
                traj_f2 = np.asarray(syl.get('f2', []), dtype=float)
                if len(traj_f1) > 0 and len(traj_f2) > 0:
                    if transform_fn is not None:
                        traj_f1, traj_f2 = transform_fn(traj_f1, traj_f2)
                    category_data[cat]['trajs_f1'].append(traj_f1)
                    category_data[cat]['trajs_f2'].append(traj_f2)

        if not category_data:
            ax.text(0.5, 0.5, "没有找到有效的共振峰分析数据！", ha='center', va='center', color='red', fontsize=12)
            return [], []

        categories = sorted(list(category_data.keys()))
        cmap = plt.get_cmap('tab10')
        cat_colors = {cat: cmap(i % 10) for i, cat in enumerate(categories)}

        all_f1_plotted_list = []
        all_f2_plotted_list = []

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
                ax.scatter(f2_list, f1_list, color=color, s=14, alpha=0.20 if density_overlay else 0.12, edgecolors='none', zorder=3)

        for cat in categories:
            color = cat_colors[cat]
            if 'f1_concat' not in category_data[cat]:
                continue
            f1_list = category_data[cat]['f1_concat']
            f2_list = category_data[cat]['f2_concat']
                
            trajs_f1 = np.array(category_data[cat]['trajs_f1'])
            trajs_f2 = np.array(category_data[cat]['trajs_f2'])
            
            lbl_x = np.nan
            lbl_y = np.nan
            
            if time_gradient and len(trajs_f1) > 0 and len(trajs_f2) > 0:
                with np.errstate(all='ignore'):
                    mean_traj_f1 = np.nanmean(trajs_f1, axis=0)
                    mean_traj_f2 = np.nanmean(trajs_f2, axis=0)
                
                valid_mask = np.isfinite(mean_traj_f1) & np.isfinite(mean_traj_f2)
                if np.any(valid_mask):
                    v_f1 = mean_traj_f1[valid_mask]
                    v_f2 = mean_traj_f2[valid_mask]
                    n_pts = len(v_f1)
                    
                    # Plot the trajectory line using the category color
                    ax.plot(v_f2, v_f1, color=color, linewidth=1.2, alpha=0.55, label=str(cat), zorder=5)
                    
                    cmap = self._get_formant_time_cmap()
                    marker_colors = cmap(np.linspace(0, 1, n_pts))
                    for i in range(n_pts):
                        ax.scatter(v_f2[i], v_f1[i], color=marker_colors[i], s=80, 
                                   edgecolors='black', linewidth=0.8, zorder=6)
                    
                    # Store midpoint for label placement
                    mid_idx = n_pts // 2
                    lbl_x = v_f2[mid_idx]
                    lbl_y = v_f1[mid_idx]
            
            if np.isnan(lbl_x) or np.isnan(lbl_y):
                mean_f1 = np.mean(f1_list)
                mean_f2 = np.mean(f2_list)
                ax.scatter(mean_f2, mean_f1, color=color, s=150, marker='o', edgecolors='black', linewidth=1.2, zorder=6, label=str(cat))
                lbl_x = mean_f2
                lbl_y = mean_f1
            
            if not np.isnan(lbl_x) and not np.isnan(lbl_y):
                if label_mode == "显示分组标签":
                    lbl_text = str(cat)
                    ax.text(lbl_x, lbl_y - 15, lbl_text, fontsize=11, fontweight='bold', color='#111827', ha='center', va='bottom', zorder=7,
                            bbox=dict(boxstyle="round,pad=0.2", fc="white", ec=color, alpha=0.8, lw=1))
                elif label_mode == "显示单字标签":
                    syl_chars = category_data[cat]['syl_chars']
                    lbl_text = max(set(syl_chars), key=syl_chars.count) if syl_chars else cat
                    ax.text(lbl_x, lbl_y - 15, lbl_text, fontsize=11, fontweight='bold', color='#111827', ha='center', va='bottom', zorder=7,
                            bbox=dict(boxstyle="round,pad=0.2", fc="white", ec=color, alpha=0.8, lw=1))
                elif label_mode == "显示词语标签":
                    entries_labels = category_data[cat]['entries_labels']
                    lbl_text = str(cat) if groupby_val == "按词语" else (max(set(entries_labels), key=entries_labels.count) if entries_labels else cat)
                    ax.text(lbl_x, lbl_y - 15, lbl_text, fontsize=11, fontweight='bold', color='#111827', ha='center', va='bottom', zorder=7,
                            bbox=dict(boxstyle="round,pad=0.2", fc="white", ec=color, alpha=0.8, lw=1))

            if "1-sigma" in ellipse_mode:
                self._draw_confidence_ellipse(f2_list, f1_list, ax, n_std=1.0, facecolor='none', edgecolor=color, linestyle='--', linewidth=1.5, zorder=4)
            elif "2-sigma" in ellipse_mode:
                self._draw_confidence_ellipse(f2_list, f1_list, ax, n_std=2.0, facecolor='none', edgecolor=color, linestyle='-.', linewidth=1.5, zorder=4)

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
            
            norm_mode = self.get_param('formant_normalization', '原始频率 (Hz)')
            is_hz = "Hz" in norm_mode
            
            f1_pad = (f1_p99 - f1_p1) * 0.15 if f1_p99 > f1_p1 else (100.0 if is_hz else 0.5)
            f2_pad = (f2_p99 - f2_p1) * 0.15 if f2_p99 > f2_p1 else (150.0 if is_hz else 0.8)
            
            ax.set_ylim(f1_p99 + f1_pad, max(50.0, f1_p1 - f1_pad) if is_hz else (f1_p1 - f1_pad))
            ax.set_xlim(f2_p99 + f2_pad, max(500.0, f2_p1 - f2_pad) if is_hz else (f2_p1 - f2_pad))

        norm_mode = self.get_param('formant_normalization', '原始频率 (Hz)')
        if "Bark" in norm_mode or "巴克" in norm_mode:
            ax.set_xlabel("F2 (Bark)", fontsize=12, fontweight='bold', labelpad=10)
            ax.set_ylabel("F1 (Bark)", fontsize=12, fontweight='bold', labelpad=10)
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

        cb = fig.colorbar(sm, cax=cax, orientation='vertical')
        cb.set_label("相对时间 0-100% (红=早, 蓝=晚)", fontsize=10, fontweight='bold')
        setattr(fig, "_phontracer_skip_tight_layout", True)
        return cb

    def _build_formant_normalizer(self, ref_entries, mode):
        if not ref_entries:
            return lambda f1, f2: (f1, f2)
        
        if "Bark" in mode or "巴克" in mode:
            def to_bark(f1, f2):
                f1 = np.asarray(f1, dtype=float)
                f2 = np.asarray(f2, dtype=float)
                b1 = 26.81 * f1 / (1960.0 + f1) - 0.53
                b2 = 26.81 * f2 / (1960.0 + f2) - 0.53
                return b1, b2
            return to_bark
            
        elif "Mel" in mode or "美尔" in mode:
            def to_mel(f1, f2):
                f1 = np.asarray(f1, dtype=float)
                f2 = np.asarray(f2, dtype=float)
                m1 = 2595.0 * np.log10(1.0 + f1 / 700.0)
                m2 = 2595.0 * np.log10(1.0 + f2 / 700.0)
                return m1, m2
            return to_mel
            
        elif "Lobanov" in mode or "归一化" in mode or "L-归一化" in mode:
            all_f1, all_f2 = [], []
            for entry in ref_entries:
                f1_arr = np.asarray(entry.get('raw_f1', []), dtype=float)
                f2_arr = np.asarray(entry.get('raw_f2', []), dtype=float)
                valid = np.isfinite(f1_arr) & np.isfinite(f2_arr) & (f2_arr > f1_arr)
                all_f1.extend(f1_arr[valid].tolist())
                all_f2.extend(f2_arr[valid].tolist())
                for syl in entry.get('syl_formants', []):
                    f1_pts = np.asarray(syl.get('f1', []), dtype=float)
                    f2_pts = np.asarray(syl.get('f2', []), dtype=float)
                    valid_syl = np.isfinite(f1_pts) & np.isfinite(f2_pts)
                    all_f1.extend(f1_pts[valid_syl].tolist())
                    all_f2.extend(f2_pts[valid_syl].tolist())
                        
            mean_f1 = np.mean(all_f1) if all_f1 else 500.0
            std_f1 = np.std(all_f1) if all_f1 else 100.0
            if std_f1 < 1e-3: std_f1 = 1.0
            
            mean_f2 = np.mean(all_f2) if all_f2 else 1500.0
            std_f2 = np.std(all_f2) if all_f2 else 250.0
            if std_f2 < 1e-3: std_f2 = 1.0
            
            def to_lobanov(f1, f2):
                f1 = np.asarray(f1, dtype=float)
                f2 = np.asarray(f2, dtype=float)
                return (f1 - mean_f1) / std_f1, (f2 - mean_f2) / std_f2
                
            return to_lobanov
            
        else:
            return lambda f1, f2: (f1, f2)

    def _plot_formant_vowel_space(self, data_entries, group_key, scale, is_preview=True):
        groupby_val = self.get_param('groupby', 'group')
        label_mode = self.get_param('formant_label_mode', '显示分组标签')
        ellipse_mode = self.get_param('formant_ellipse', '1-sigma 置信椭圆')
        show_raw = self.get_param('formant_show_raw', True)
        time_gradient = self.get_param('formant_time_gradient', False)
        density_overlay = bool(self.get_param('formant_density_overlay', False))
        facet_val = self.get_param('formant_density_facet', '单图展示 (不分面)')

        normalization_mode = self.get_param('formant_normalization', '原始频率 (Hz)')
        lock_axes = bool(self.get_param('formant_axis_lock', False))
        axis_ref_entries = self.get_param('formant_axis_ref_entries', data_entries)
        
        transform_fn = self._build_formant_normalizer(axis_ref_entries, normalization_mode)
        
        fixed_limits = None
        if lock_axes:
            ref_f1, ref_f2 = [], []
            for entry in axis_ref_entries:
                xs = np.asarray(entry.get('raw_xs', []), dtype=float)
                f1_arr = np.asarray(entry.get('raw_f1', []), dtype=float)
                f2_arr = np.asarray(entry.get('raw_f2', []), dtype=float)
                mask = np.isfinite(f1_arr) & np.isfinite(f2_arr) & (f2_arr > f1_arr)
                if np.any(mask):
                    ref_f1.extend(f1_arr[mask].tolist())
                    ref_f2.extend(f2_arr[mask].tolist())
                for syl in entry.get('syl_formants', []):
                    f1_pts = np.asarray(syl.get('f1', []), dtype=float)
                    f2_pts = np.asarray(syl.get('f2', []), dtype=float)
                    valid_syl = np.isfinite(f1_pts) & np.isfinite(f2_pts)
                    ref_f1.extend(f1_pts[valid_syl].tolist())
                    ref_f2.extend(f2_pts[valid_syl].tolist())
            
            if ref_f1 and ref_f2:
                ref_f1, ref_f2 = transform_fn(np.array(ref_f1), np.array(ref_f2))
                f1_p1, f1_p99 = np.percentile(ref_f1, 1.0), np.percentile(ref_f1, 99.0)
                f2_p1, f2_p99 = np.percentile(ref_f2, 1.0), np.percentile(ref_f2, 99.0)
                
                is_hz = "Hz" in normalization_mode
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
        if facet_val in ("按字表组分面", "group"):
            facet_specs = [(g, [e for e in data_entries if e['group'] == g]) for g in sorted(set(e['group'] for e in data_entries))]
        elif facet_val in ("按发音人分面", "speaker"):
            facet_specs = [(s, [e for e in data_entries if e['speaker_name'] == s]) for s in sorted(set(e['speaker_name'] for e in data_entries))]
        elif facet_val in ("按音节位置分面", "syllable_position"):
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
        for idx, (facet_name, facet_entries) in enumerate(facet_specs):
            ax = axes_flat[idx]
            self._draw_formant_space_panel(
                ax, facet_entries, groupby_val, label_mode, ellipse_mode,
                show_raw, time_gradient, density_overlay, density_sm_holder,
                transform_fn=transform_fn, fixed_limits=fixed_limits, is_preview=is_preview
            )
            title_text = "元音共振峰空间分布图" if facet_name == "Default" else str(facet_name)
            if len(title_text) > 28:
                title_text = title_text[:25] + "..."
            ax.set_title(title_text, fontsize=12 if n_facets > 1 else 13, fontweight='bold', pad=12)

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
        fig, ax = plt.subplots(figsize=(8.6, 7.2))
        ax.set_facecolor("#F8FAFC")
        ax.grid(True, linestyle="--", alpha=0.25, linewidth=0.8)

        show_raw = self.get_param('formant_density_show_raw', False)
        show_contours = self.get_param('formant_density_show_contours', True)
        
        normalization_mode = self.get_param('formant_normalization', '原始频率 (Hz)')
        transform_fn = self._build_formant_normalizer(data_entries, normalization_mode)
        
        sm, all_f1, all_f2 = self._draw_formant_density_layer(
            ax,
            data_entries,
            show_raw=show_raw,
            show_contours=show_contours,
            bw_method=self.get_param('formant_density_bw', 0.14),
            alpha_max=0.78,
            zorder=2,
            transform_fn=transform_fn
        )

        if sm is None:
            ax.text(0.5, 0.5, "没有找到有效的共振峰时空密度数据！", ha='center', va='center', color='red', fontsize=12)
            return fig

        ax.invert_xaxis()
        ax.invert_yaxis()

        if all_f1 is not None and all_f2 is not None and len(all_f1) > 0 and len(all_f2) > 0:
            f1_p1, f1_p99 = np.percentile(all_f1, 1.0), np.percentile(all_f1, 99.0)
            f2_p1, f2_p99 = np.percentile(all_f2, 1.0), np.percentile(all_f2, 99.0)
            
            is_hz = "Hz" in normalization_mode
            f1_pad = (f1_p99 - f1_p1) * 0.16 if f1_p99 > f1_p1 else (100.0 if is_hz else 0.5)
            f2_pad = (f2_p99 - f2_p1) * 0.16 if f2_p99 > f2_p1 else (150.0 if is_hz else 0.8)
            
            ax.set_ylim(f1_p99 + f1_pad, max(50.0, f1_p1 - f1_pad) if is_hz else (f1_p1 - f1_pad))
            ax.set_xlim(f2_p99 + f2_pad, max(500.0, f2_p1 - f2_pad) if is_hz else (f2_p1 - f2_pad))

        if "Bark" in normalization_mode or "巴克" in normalization_mode:
            ax.set_xlabel("F2 (Bark)", fontsize=12, fontweight='bold', labelpad=10)
            ax.set_ylabel("F1 (Bark)", fontsize=12, fontweight='bold', labelpad=10)
        elif "Mel" in normalization_mode or "美尔" in normalization_mode:
            ax.set_xlabel("F2 (Mel)", fontsize=12, fontweight='bold', labelpad=10)
            ax.set_ylabel("F1 (Mel)", fontsize=12, fontweight='bold', labelpad=10)
        elif "Lobanov" in normalization_mode or "归一化" in normalization_mode or "L-归一化" in normalization_mode:
            ax.set_xlabel("F2 (Z-score)", fontsize=12, fontweight='bold', labelpad=10)
            ax.set_ylabel("F1 (Z-score)", fontsize=12, fontweight='bold', labelpad=10)
        else:
            ax.set_xlabel("F2 (Hz)", fontsize=12, fontweight='bold', labelpad=10)
            ax.set_ylabel("F1 (Hz)", fontsize=12, fontweight='bold', labelpad=10)

        self._add_formant_density_colorbar(fig, [ax], sm)

        title_text = "共振峰时空密度图"
        if len(set(e['speaker_name'] for e in data_entries)) == 1:
            title_text = f"{data_entries[0]['speaker_name']} - {title_text}"
        ax.set_title(title_text, fontsize=13, fontweight='bold', pad=15)

        setattr(fig, "_phontracer_skip_tight_layout", True)
        return fig

    def _plot_formant_trajectories(self, data_entries, group_key, scale):
        fig, ax = plt.subplots(figsize=(9.2, 5.8))
        ax.set_facecolor("#F8FAFC")
        ax.grid(True, linestyle="--", alpha=0.25, linewidth=0.8)

        groupby_val = self.get_param('groupby', 'group')
        traj_style = self.get_param('formant_traj_style', '平均曲线 + 置信区间阴影')
        num_points = self.project_tree.app_state_params.get('pts', 11)

        normalization_mode = self.get_param('formant_normalization', '原始频率 (Hz)')
        transform_fn = self._build_formant_normalizer(data_entries, normalization_mode)

        category_trajs = {}

        for entry in data_entries:
            for syl in entry.get('syl_formants', []):
                cat = self._get_syl_category(entry, syl, groupby_val)
                if cat not in category_trajs:
                    category_trajs[cat] = {'f1': [], 'f2': []}
                
                f1_pts = np.array(syl['f1'], dtype=float)
                f2_pts = np.array(syl['f2'], dtype=float)
                
                if len(f1_pts) == num_points and len(f2_pts) == num_points:
                    if transform_fn is not None:
                        f1_pts, f2_pts = transform_fn(f1_pts, f2_pts)
                    category_trajs[cat]['f1'].append(f1_pts)
                    category_trajs[cat]['f2'].append(f2_pts)

        if not category_trajs:
            ax.text(0.5, 0.5, "没有找到有效的共振峰时序数据！", ha='center', va='center', color='red', fontsize=12)
            return fig

        categories = sorted(list(category_trajs.keys()))
        cmap = plt.get_cmap('tab10')
        cat_colors = {cat: cmap(i % 10) for i, cat in enumerate(categories)}

        x_pts = np.linspace(0, 100, num_points)

        for cat in categories:
            color = cat_colors[cat]
            f1_arr = np.array(category_trajs[cat]['f1'])
            f2_arr = np.array(category_trajs[cat]['f2'])
            
            if len(f1_arr) == 0 or len(f2_arr) == 0:
                continue

            with np.errstate(all='ignore'):
                mean_f1 = np.nanmean(f1_arr, axis=0)
                std_f1 = np.nanstd(f1_arr, axis=0)
                mean_f2 = np.nanmean(f2_arr, axis=0)
                std_f2 = np.nanstd(f2_arr, axis=0)

            if "个体浅色细线" in traj_style:
                from matplotlib.collections import LineCollection
                segments_f1 = [np.column_stack((x_pts, single_f1)) for single_f1 in f1_arr]
                segments_f2 = [np.column_stack((x_pts, single_f2)) for single_f2 in f2_arr]
                lc_f1 = LineCollection(segments_f1, colors=color, alpha=0.1, linewidths=0.8)
                lc_f2 = LineCollection(segments_f2, colors=color, alpha=0.1, linewidths=0.8)
                ax.add_collection(lc_f1)
                ax.add_collection(lc_f2)
            elif "置信区间阴影" in traj_style:
                ax.fill_between(x_pts, mean_f1 - std_f1, mean_f1 + std_f1, color=color, alpha=0.1)
                ax.fill_between(x_pts, mean_f2 - std_f2, mean_f2 + std_f2, color=color, alpha=0.1)

            ax.plot(x_pts, mean_f1, linestyle='--', marker='s', markersize=4.5, color=color, linewidth=2.1, label=f"{cat} F1")
            ax.plot(x_pts, mean_f2, linestyle='-', marker='o', markersize=4.5, color=color, linewidth=2.3, label=f"{cat} F2")

        ax.set_xlabel("音节物理时长百分比 (%)", fontsize=12, fontweight='bold', labelpad=10)
        
        if "Bark" in normalization_mode or "巴克" in normalization_mode:
            ax.set_ylabel("频率 (Bark)", fontsize=12, fontweight='bold', labelpad=10)
        elif "Mel" in normalization_mode or "美尔" in normalization_mode:
            ax.set_ylabel("频率 (Mel)", fontsize=12, fontweight='bold', labelpad=10)
        elif "Lobanov" in normalization_mode or "归一化" in normalization_mode or "L-归一化" in normalization_mode:
            ax.set_ylabel("频率 (Z-score)", fontsize=12, fontweight='bold', labelpad=10)
        else:
            ax.set_ylabel("频率 (Hz)", fontsize=12, fontweight='bold', labelpad=10)
        ax.margins(x=0.02)
        
        legend_kwargs = self._get_legend_kwargs()
        legend_kwargs["fontsize"] = 9
        ax.legend(**legend_kwargs)
        
        title_text = "共振峰 F1-F2 时序轨迹平均曲线图"
        if len(set(e['speaker_name'] for e in data_entries)) == 1:
            title_text = f"{data_entries[0]['speaker_name']} - {title_text}"
        ax.set_title(title_text, fontsize=13, fontweight='bold', pad=15)
        
        fig.tight_layout()
        return fig

    def _build_formant_space_settings(self):
        # Confidence Ellipse
        ctk.CTkLabel(self.dynamic_content_frame, text="置信椭圆 (Confidence Ellipse):", font=self.font_small).pack(anchor="w", pady=(5, 2))
        self.combo_formant_ellipse = ctk.CTkOptionMenu(
            self.dynamic_content_frame, 
            values=["1-sigma 置信椭圆", "2-sigma 置信椭圆", "无置信椭圆"], 
            command=lambda _: self.trigger_preview_update(), 
            **self.dropdown_kwargs
        )
        self.combo_formant_ellipse.set("1-sigma 置信椭圆")
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

        ctk.CTkLabel(self.dynamic_content_frame, text="共振峰归一化:", font=self.font_small).pack(anchor="w", pady=(6, 2))
        self.combo_formant_normalization = ctk.CTkOptionMenu(
            self.dynamic_content_frame,
            values=["原始频率 (Hz)", "Lobanov z-score (学术标准)"],
            command=lambda _: self.trigger_preview_update(),
            **self.dropdown_kwargs
        )
        self.combo_formant_normalization.set(self.var_formant_normalization.get())
        self.combo_formant_normalization.pack(fill=tk.X, pady=(2, 6))
        self._apply_custom_arrow(self.combo_formant_normalization)

        self.cb_formant_axis_lock = ctk.CTkCheckBox(
            self.dynamic_content_frame,
            text="固定横纵坐标范围 (跨组/跨页一致)",
            variable=self.var_formant_axis_lock,
            font=self.font_small,
            checkbox_width=18,
            checkbox_height=18,
            fg_color=("#3B82F6", "#2563EB"),
            hover_color=("#4B5563", "#9CA3AF"),
            border_color=("#9CA3AF", "#4B5563"),
            command=self.update_preview
        )
        self.cb_formant_axis_lock.pack(anchor="w", pady=(2, 8))

        # Show raw scatter points
        self.cb_formant_show_raw = ctk.CTkCheckBox(
            self.dynamic_content_frame, 
            text="显示个体测量帧 (散点背景)", 
            variable=self.var_formant_show_raw,
            font=self.font_small, 
            checkbox_width=18, 
            checkbox_height=18,
            fg_color=("#3B82F6", "#2563EB"), 
            hover_color=("#4B5563", "#9CA3AF"), 
            border_color=("#9CA3AF", "#4B5563"),
            command=self.update_preview
        )
        self.cb_formant_show_raw.pack(anchor="w", pady=(10, 5))

        # Show time gradient (red to blue)
        self.cb_formant_time_gradient = ctk.CTkCheckBox(
            self.dynamic_content_frame, 
            text="显示时序渐变轨迹线 (红→蓝)", 
            variable=self.var_formant_time_gradient,
            font=self.font_small, 
            checkbox_width=18, 
            checkbox_height=18,
            fg_color=("#3B82F6", "#2563EB"), 
            hover_color=("#4B5563", "#9CA3AF"), 
            border_color=("#9CA3AF", "#4B5563"),
            command=self.update_preview
        )
        self.cb_formant_time_gradient.pack(anchor="w", pady=(5, 5))

        self.cb_formant_density_overlay = ctk.CTkCheckBox(
            self.dynamic_content_frame,
            text="叠加时空密度热力层 (红→蓝)",
            variable=self.var_formant_density_overlay,
            font=self.font_small,
            checkbox_width=18,
            checkbox_height=18,
            fg_color=("#3B82F6", "#2563EB"),
            hover_color=("#4B5563", "#9CA3AF"),
            border_color=("#9CA3AF", "#4B5563"),
            command=self.update_preview
        )
        self.cb_formant_density_overlay.pack(anchor="w", pady=(10, 5))

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
        self.slider_formant_density_bw.pack(fill=tk.X, pady=(0, 8))

        self.cb_formant_density_show_contours = ctk.CTkCheckBox(
            self.dynamic_content_frame,
            text="叠加等密度轮廓线",
            variable=self.var_formant_density_show_contours,
            font=self.font_small,
            checkbox_width=18,
            checkbox_height=18,
            fg_color=("#3B82F6", "#2563EB"),
            hover_color=("#4B5563", "#9CA3AF"),
            border_color=("#9CA3AF", "#4B5563"),
            command=self.update_preview
        )
        self.cb_formant_density_show_contours.pack(anchor="w", pady=(2, 5))

        ctk.CTkLabel(self.dynamic_content_frame, text="分面子图排版 (Facet):", font=self.font_small).pack(anchor="w", pady=(8, 2))
        self.combo_formant_density_facet = ctk.CTkOptionMenu(
            self.dynamic_content_frame,
            values=["单图展示 (不分面)", "按字表组分面", "按音节位置分面", "按发音人分面"],
            command=lambda _: self.trigger_preview_update(),
            **self.dropdown_kwargs
        )
        self.combo_formant_density_facet.set(self.var_formant_density_facet.get())
        self.combo_formant_density_facet.pack(fill=tk.X, pady=(2, 10))
        self._apply_custom_arrow(self.combo_formant_density_facet)

    def _on_formant_density_bw_changed(self, val):
        bw = float(val)
        self.var_formant_density_bw.set(bw)
        if hasattr(self, 'lbl_formant_density_bw'):
            self.lbl_formant_density_bw.configure(text=f"{bw:.2f}")
        self.trigger_preview_update()

    def _build_formant_density_settings(self):
        # Checkbox for show raw points
        self.cb_formant_density_show_raw = ctk.CTkCheckBox(
            self.dynamic_content_frame, 
            text="显示个体测量帧 (极淡散点)", 
            variable=self.var_formant_density_show_raw,
            font=self.font_small, 
            checkbox_width=18, 
            checkbox_height=18,
            fg_color=("#3B82F6", "#2563EB"), 
            hover_color=("#4B5563", "#9CA3AF"), 
            border_color=("#9CA3AF", "#4B5563"),
            command=self.update_preview
        )
        self.cb_formant_density_show_raw.pack(anchor="w", pady=(10, 5))

        # Checkbox for show contour lines
        self.cb_formant_density_show_contours = ctk.CTkCheckBox(
            self.dynamic_content_frame, 
            text="叠加等密度轮廓线", 
            variable=self.var_formant_density_show_contours,
            font=self.font_small, 
            checkbox_width=18, 
            checkbox_height=18,
            fg_color=("#3B82F6", "#2563EB"), 
            hover_color=("#4B5563", "#9CA3AF"), 
            border_color=("#9CA3AF", "#4B5563"),
            command=self.update_preview
        )
        self.cb_formant_density_show_contours.pack(anchor="w", pady=(5, 5))

    def _build_formant_trajectory_settings(self):
        # Trajectory style
        ctk.CTkLabel(self.dynamic_content_frame, text="曲线展现形式:", font=self.font_small).pack(anchor="w", pady=(5, 2))
        self.combo_formant_traj_style = ctk.CTkOptionMenu(
            self.dynamic_content_frame, 
            values=["仅平均曲线", "平均曲线 + 个体浅色细线", "平均曲线 + 置信区间阴影"], 
            command=lambda _: self.trigger_preview_update(), 
            **self.dropdown_kwargs
        )
        self.combo_formant_traj_style.set("平均曲线 + 置信区间阴影")
        self.combo_formant_traj_style.pack(fill=tk.X, pady=(2, 10))
        self._apply_custom_arrow(self.combo_formant_traj_style)

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
            plt.close(fig)


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

        self.title("声学图表导出 - 声视化工具箱")
        self.geometry("980x640")
        self.resizable(True, True)
        # self.transient(parent)
        # self.grab_set()
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
        self.colors = ['#2563EB', '#DC2626', '#16A34A', '#9333EA', '#EA580C', '#0891B2', '#CA8A04', '#6366F1']

        # Load active speaker's data items as default fallback
        self.sm = getattr(self.app, 'speaker_manager', None)
        self.active_speaker = self.sm.get_active_speaker() if self.sm else None

        # Fonts
        self.font_title = ctk.CTkFont(family="Microsoft YaHei", size=15, weight="bold")
        self.font_main = ctk.CTkFont(family="Microsoft YaHei", size=12)
        self.font_small = ctk.CTkFont(family="Microsoft YaHei", size=11)

        # Dropdown OptionMenu styling to match speaker_dropdown
        self.dropdown_kwargs = {
            "font": self.font_main,
            "fg_color": ("#F3F4F6", "#374151"),
            "text_color": ("#1F2937", "#E5E7EB"),
            "button_color": ("#F3F4F6", "#374151"),
            "button_hover_color": ("#E5E7EB", "#4B5563"),
            "height": 32,
            "corner_radius": 16
        }

        # Variables Setup
        self._init_variables()
        self._init_group_filters()

        # GUI Structure
        self._build_gui()
        self._populate_groups_list()

        # Update pixel options state based on initial format
        self._update_pixel_options_state()

        # Render Initial Preview
        self.update_preview()

    def _apply_custom_arrow(self, dropdown):
        try:
            orig_draw_arrow = dropdown._draw_engine.draw_dropdown_arrow

            def custom_draw_arrow(*args, **kwargs):
                old_method = dropdown._draw_engine.preferred_drawing_method
                try:
                    dropdown._draw_engine.preferred_drawing_method = "polygon_shapes"
                    res = orig_draw_arrow(*args, **kwargs)
                    try:
                        dropdown._canvas.itemconfigure("dropdown_arrow", width=2)
                    except Exception:
                        pass
                    return res
                finally:
                    dropdown._draw_engine.preferred_drawing_method = old_method

            dropdown._draw_engine.draw_dropdown_arrow = custom_draw_arrow
            dropdown._canvas.delete("dropdown_arrow")
            dropdown._draw(no_color_updates=False)
        except Exception:
            pass

    def _init_variables(self):
        # 1. Chart Type
        analysis_mode = self.project_tree.app_state_params.get('analysis_mode', 'f0')
        if analysis_mode == 'formant':
            self.var_chart_type = ctk.StringVar(value="formant_space")
        else:
            self.var_chart_type = ctk.StringVar(value="contour")  # contour, distribution, density, quality, overview_heatmap, formant_overview_heatmap

        # 2. Export Scope
        scope_default = "active"
        if self.initial_mode == 'separate':
            scope_default = "separate"
        elif self.initial_mode == 'integrated':
            scope_default = "integrated"
        self.var_export_scope = ctk.StringVar(value=scope_default)  # active, separate, integrated

        # 3. Grouping Basis
        self.var_group_by = ctk.StringVar(value="group")  # group (声调类型), label (词语), speaker (发音人)

        # 4. Acoustic Scale
        self.var_scale = ctk.StringVar(value="t_value")  # t_value, hz

        # 5. Image Format
        self.var_format = ctk.StringVar(value="png")  # png, svg, pdf

        # Dynamic Options - Tone Contour
        self.var_contour_x = ctk.StringVar(value="normalized")  # normalized, duration
        self.var_contour_content = ctk.StringVar(value="average")  # average, average_individual, average_sd_ci
        self.var_contour_facet = ctk.StringVar(value="none")  # none, group, speaker, syllable_position

        # Dynamic Options - Tone Distribution
        self.var_dist_type = ctk.StringVar(value="boxplot_violin")  # boxplot_violin, start_mid_end, range, variability
        self.var_dist_style = ctk.StringVar(value="boxplot")  # boxplot, violinplot

        # Dynamic Options - Temporal Density (KDE Heatmap)
        self.var_density_bw = ctk.DoubleVar(value=0.15)
        self.var_density_f0_mode = ctk.StringVar(value="percentile")  # percentile, minmax, manual
        self.var_density_p_low = ctk.StringVar(value="5")
        self.var_density_p_high = ctk.StringVar(value="95")
        self.var_density_m_min = ctk.StringVar(value="75")
        self.var_density_m_max = ctk.StringVar(value="600")
        self.var_density_facet = ctk.StringVar(value="none")  # none, group, label

        # Dynamic Options - Formant Density (KDE Heatmap)
        self.var_formant_density_overlay = tk.BooleanVar(value=False)
        self.var_formant_density_bw = ctk.DoubleVar(value=0.14)
        self.var_formant_density_show_raw = tk.BooleanVar(value=False)
        self.var_formant_density_show_contours = tk.BooleanVar(value=True)
        self.var_formant_density_facet = ctk.StringVar(value="单图展示 (不分面)")

        # Dynamic Options - Quality Check
        self.var_qc_view = ctk.StringVar(value="raw_overlay")  # raw_overlay, active_ratio, speaker_means

        # Pagination state
        self.current_preview_page = 0
        self.current_group_page = 0
        self.sort_by_count = False

        # Live refresh switch
        self.var_live_refresh = ctk.BooleanVar(value=True)
        self.var_high_precision = ctk.BooleanVar(value=False)
        self._debounce_timer_id = None

        # Legend configuration
        self.var_legend_outside = ctk.BooleanVar(value=False)

        # Dynamic Options - Formant Space
        self.var_formant_ellipse = ctk.StringVar(value="1-sigma 置信椭圆")
        self.var_formant_label_mode = ctk.StringVar(value="显示分组标签")
        self.var_formant_show_raw = ctk.BooleanVar(value=True)
        self.var_formant_time_gradient = ctk.BooleanVar(value=False)
        self.var_formant_normalization = ctk.StringVar(value=self.project_tree.app_state_params.get('formant_normalization', "原始频率 (Hz)"))
        self.var_formant_axis_lock = ctk.BooleanVar(value=bool(self.project_tree.app_state_params.get('formant_axis_lock', False)))

        # Dynamic Options - Formant Trajectory
        self.var_formant_traj_style = ctk.StringVar(value="平均曲线 + 置信区间阴影")
        self.var_formant_overview_mode = ctk.StringVar(value="F1 & F2 双轨")

        # Image Size & Pixel configuration
        self.var_image_ratio_mode = ctk.StringVar(value="默认")
        self.var_image_ratio_custom = ctk.DoubleVar(value=1.5)
        self.var_image_pixel_mode = ctk.StringVar(value="默认")
        self.var_image_pixel_custom = ctk.IntVar(value=1080)

    def _init_group_filters(self):
        # Extract all unique group names across all speakers
        all_entries = self._extract_active_data(self.all_speakers if self.all_speakers else [self.active_speaker])
        self.group_counts = {}
        for entry in all_entries:
            g = entry['group']
            self.group_counts[g] = self.group_counts.get(g, 0) + 1

        self.available_groups = sorted(list(self.group_counts.keys()))
        self.group_checkbox_vars = {}
        for g in self.available_groups:
            self.group_checkbox_vars[g] = ctk.BooleanVar(value=True)

    def _build_gui(self):
        # Main Grid Layout: Left Column = Settings, Right Column = Preview
        self.grid_columnconfigure(0, weight=0, minsize=420)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # --- LEFT SIDE: Scrollable Configuration Frame ---
        self.left_scroll = ctk.CTkScrollableFrame(self, width=400, label_text="📊 图表设置", label_font=self.font_title, fg_color=("#F9FAFB", "#2D3748"))
        self.left_scroll.grid(row=0, column=0, sticky="nsew", padx=(15, 10), pady=15)

        self._build_settings_cards()

        # --- RIGHT SIDE: Real-time Live Preview & Action Buttons ---
        self.right_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.right_frame.grid(row=0, column=1, sticky="nsew", padx=(10, 15), pady=15)
        self.right_frame.grid_rowconfigure(0, weight=1)
        self.right_frame.grid_rowconfigure(1, weight=0)
        self.right_frame.grid_rowconfigure(2, weight=0)
        self.right_frame.grid_rowconfigure(3, weight=0)
        self.right_frame.grid_columnconfigure(0, weight=1)

        # Preview Canvas Container wrapped to maintain target aspect ratio
        self.preview_wrapper = ctk.CTkFrame(self.right_frame, fg_color="transparent")
        self.preview_wrapper.grid(row=0, column=0, sticky="nsew", pady=(0, 15))
        self.preview_wrapper.grid_rowconfigure(0, weight=1)
        self.preview_wrapper.grid_columnconfigure(0, weight=1)

        self.preview_container = ctk.CTkFrame(self.preview_wrapper, fg_color="#F3F4F6", border_width=1, border_color="#D1D5DB")
        self.preview_container.place(relx=0.5, rely=0.5, anchor="center")
        self.preview_container.grid_rowconfigure(0, weight=1)
        self.preview_container.grid_columnconfigure(0, weight=1)

        self.preview_lbl = ctk.CTkLabel(self.preview_container, text="正在加载图表预览...", font=self.font_title, text_color="#6B7280")
        self.preview_lbl.grid(row=0, column=0)

        self.preview_wrapper.bind("<Configure>", lambda e: self.on_preview_wrapper_configure())

        # Pagination Frame (Hidden by default, shown when scope is separate)
        self.pagination_frame = ctk.CTkFrame(self.right_frame, fg_color="transparent")
        self.pagination_frame.grid_columnconfigure(0, weight=1)
        self.pagination_frame.grid_columnconfigure(1, weight=0)
        self.pagination_frame.grid_columnconfigure(2, weight=1)

        self.btn_prev_page = ctk.CTkButton(
            self.pagination_frame, text="◀ 上一个发音人", width=120, height=30, corner_radius=15,
            fg_color=("#F3F4F6", "#374151"), text_color=("#374151", "#E5E7EB"), hover_color=("#E5E7EB", "#4B5563"),
            font=self.font_main, command=self._prev_page
        )
        self.btn_prev_page.grid(row=0, column=0, padx=10, sticky="e")

        self.lbl_page_info = ctk.CTkLabel(self.pagination_frame, text="发音人: - (0/0)", font=self.font_title)
        self.lbl_page_info.grid(row=0, column=1, padx=20)

        self.btn_next_page = ctk.CTkButton(
            self.pagination_frame, text="下一个发音人 ▶", width=120, height=30, corner_radius=15,
            fg_color=("#F3F4F6", "#374151"), text_color=("#374151", "#E5E7EB"), hover_color=("#E5E7EB", "#4B5563"),
            font=self.font_main, command=self._next_page
        )
        self.btn_next_page.grid(row=0, column=2, padx=10, sticky="w")

        # Group Pagination Frame (Hidden by default, shown when groups > 8)
        self.group_pagination_frame = ctk.CTkFrame(self.right_frame, fg_color="transparent")
        self.group_pagination_frame.grid_columnconfigure(0, weight=1)
        self.group_pagination_frame.grid_columnconfigure(1, weight=0)
        self.group_pagination_frame.grid_columnconfigure(2, weight=1)

        self.btn_prev_group_page = ctk.CTkButton(
            self.group_pagination_frame, text="◀", width=120, height=30, corner_radius=15,
            fg_color=("#F3F4F6", "#374151"), text_color=("#374151", "#E5E7EB"), hover_color=("#E5E7EB", "#4B5563"),
            font=self.font_main, command=self._prev_group_page
        )
        self.btn_prev_group_page.grid(row=0, column=0, padx=10, sticky="e")

        self.lbl_group_page_info = ctk.CTkLabel(self.group_pagination_frame, text="组别页码: - (0/0)", font=self.font_title)
        self.lbl_group_page_info.grid(row=0, column=1, padx=20)

        self.btn_next_group_page = ctk.CTkButton(
            self.group_pagination_frame, text="▶", width=120, height=30, corner_radius=15,
            fg_color=("#F3F4F6", "#374151"), text_color=("#374151", "#E5E7EB"), hover_color=("#E5E7EB", "#4B5563"),
            font=self.font_main, command=self._next_group_page
        )
        self.btn_next_group_page.grid(row=0, column=2, padx=10, sticky="w")

        # Bottom Buttons
        self.bottom_frame = ctk.CTkFrame(self.right_frame, fg_color="transparent")
        self.bottom_frame.grid(row=3, column=0, sticky="ew")

        ctk.CTkButton(self.bottom_frame, text="🔄 刷新预览", width=120, height=38, corner_radius=19, fg_color="#E5E7EB", text_color="#374151", hover_color="#D1D5DB", font=self.font_main, command=self.manual_refresh_preview).pack(side=tk.LEFT, padx=5)

        self.switch_live_refresh = ctk.CTkSwitch(
            self.bottom_frame, text="实时刷新", variable=self.var_live_refresh,
            font=self.font_main, command=self._on_live_refresh_toggle
        )
        self.switch_live_refresh.pack(side=tk.LEFT, padx=10)

        self.switch_high_precision = ctk.CTkSwitch(
            self.bottom_frame, text="高渲染精细度", variable=self.var_high_precision,
            font=self.font_main, command=self.update_preview
        )
        self.switch_high_precision.pack(side=tk.LEFT, padx=10)

        self.btn_export = ctk.CTkButton(self.bottom_frame, text="💾 导出", width=120, height=38, corner_radius=19, font=self.font_title, command=self.on_confirm)
        self.btn_export.pack(side=tk.RIGHT, padx=5)

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
        card_padding = {"padx": 10, "pady": 8}

        # --- OPTIONAL: Multi-group warning/suggestion card ---
        if len(self.available_groups) > 8:
            card_warn = ctk.CTkFrame(self.left_scroll, fg_color=("#FFFBEB", "#2A200B"), border_width=1, border_color=("#F59E0B", "#B45309"), corner_radius=12)
            card_warn.pack(fill=tk.X, **card_padding)

            ctk.CTkLabel(card_warn, text="⚠️ 多组别优化建议", font=self.font_title, text_color=("#B45309", "#FBBF24")).pack(anchor="w", padx=15, pady=(10, 5))

            lbl_warn_text = (
                f"当前数据包含 {len(self.available_groups)} 个组别。如果在一张图里\n"
                f"叠加所有曲线会导致图表极其杂乱、无法阅读。\n"
                f"建议选择以下三种专业优化模式之一："
            )
            ctk.CTkLabel(card_warn, text=lbl_warn_text, font=self.font_small, justify="left", text_color=("#78350F", "#FDE68A")).pack(anchor="w", padx=15, pady=(0, 10))

            btn_frame = ctk.CTkFrame(card_warn, fg_color="transparent")
            btn_frame.pack(fill=tk.X, padx=15, pady=(0, 15))

            btn_opt1 = ctk.CTkButton(
                btn_frame, text="精选对比", width=95, height=28, corner_radius=14,
                fg_color=("#F59E0B", "#D97706"), text_color="white", hover_color=("#D97706", "#B45309"),
                font=self.font_small, command=self._apply_featured_contrast_mode
            )
            btn_opt1.pack(side=tk.LEFT, padx=3)

            btn_opt2 = ctk.CTkButton(
                btn_frame, text="批量图册", width=95, height=28, corner_radius=14,
                fg_color=("#10B981", "#059669"), text_color="white", hover_color=("#059669", "#047857"),
                font=self.font_small, command=self._apply_batch_album_mode
            )
            btn_opt2.pack(side=tk.LEFT, padx=3)

            btn_opt3 = ctk.CTkButton(
                btn_frame, text="整体概览", width=95, height=28, corner_radius=14,
                fg_color=("#6366F1", "#4F46E5"), text_color="white", hover_color=("#4F46E5", "#4338CA"),
                font=self.font_small, command=self._apply_overall_overview_mode
            )
            btn_opt3.pack(side=tk.LEFT, padx=3)

        # --- CARD 1: Basic Type & Scope ---
        card1 = ctk.CTkFrame(self.left_scroll, fg_color=("#FFFFFF", "#1E293B"), border_width=1, border_color=("#E5E7EB", "#475569"), corner_radius=12)
        card1.pack(fill=tk.X, **card_padding)

        ctk.CTkLabel(card1, text="🔹 基础类型与范围", font=self.font_title).pack(anchor="w", padx=15, pady=(10, 5))

        # Chart Type OptionMenu
        if self._is_formant_mode():
            type_values = ["元音共振峰空间图", "共振峰时序轨迹图", "共振峰组别概览图"]
            default_type = "元音共振峰空间图"
            intention_values = ["论文主图 (清晰对比)", "附录图册 (完整数据)"]
        else:
            type_values = ["声调轮廓图", "声调分布图", "时序密度图", "数据质量检查", "声调组别概览图"]
            default_type = "声调轮廓图"
            intention_values = ["论文主图 (清晰对比)", "附录图册 (完整数据)", "数据诊断 (质量排查)"]

        ctk.CTkLabel(card1, text="图表类型:", font=self.font_small).pack(anchor="w", padx=15)
        self.combo_type = ctk.CTkOptionMenu(card1, values=type_values, command=self._on_type_changed, **self.dropdown_kwargs)
        self.combo_type.set(default_type)
        self.combo_type.pack(fill=tk.X, padx=15, pady=(0, 10))
        self._apply_custom_arrow(self.combo_type)

        # Export Intention OptionMenu
        ctk.CTkLabel(card1, text="导出意图 (自动匹配推荐设置):", font=self.font_small).pack(anchor="w", padx=15)
        self.combo_intention = ctk.CTkOptionMenu(card1, values=intention_values, command=self._on_intention_changed, **self.dropdown_kwargs)
        self.combo_intention.set("论文主图 (清晰对比)")
        self.combo_intention.pack(fill=tk.X, padx=15, pady=(0, 10))
        self._apply_custom_arrow(self.combo_intention)

        # Export Scope Options
        ctk.CTkLabel(card1, text="导出范围:", font=self.font_small).pack(anchor="w", padx=15)
        self.combo_scope = ctk.CTkOptionMenu(card1, values=["仅当前发音人", "所有发音人(分别导出)", "所有发音人(整合导出)"], command=self._on_scope_changed, **self.dropdown_kwargs)
        # Match original menu selected mode
        if self.initial_mode == 'separate':
            self.combo_scope.set("所有发音人(分别导出)")
        elif self.initial_mode == 'integrated':
            self.combo_scope.set("所有发音人(整合导出)")
        else:
            self.combo_scope.set("仅当前发音人")
        self.combo_scope.pack(fill=tk.X, padx=15, pady=(0, 10))
        self._apply_custom_arrow(self.combo_scope)

        # Speaker Management Restriction
        if not self.all_speakers or len(self.all_speakers) <= 1:
            self.combo_scope.configure(state="disabled")

        # --- CARD 2: Visual Parameters ---
        card2 = ctk.CTkFrame(self.left_scroll, fg_color=("#FFFFFF", "#1E293B"), border_width=1, border_color=("#E5E7EB", "#475569"), corner_radius=12)
        card2.pack(fill=tk.X, **card_padding)
        card2.grid_columnconfigure(0, weight=1)
        card2.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(card2, text="🔹 核心维度与尺度", font=self.font_title).grid(row=0, column=0, columnspan=2, sticky="w", padx=15, pady=(10, 5))

        # Row 1 Labels
        if self._is_formant_mode():
            groupby_values = ["按字表组", "按词语", "按单字/音节", "按发音人"]
            groupby_default = "按字表组"
            scale_values = ["Hz (共振峰频率)"]
            scale_default = "Hz (共振峰频率)"
            groupby_label_text = "分组依据 / 配色:"
            scale_label_text = "频率单位:"
        else:
            groupby_values = ["按声调类型", "按词语", "按发音人"]
            groupby_default = "按声调类型"
            scale_values = ["T 值 (五度标调)", "Hz (基频绝对频率)"]
            scale_default = "T 值 (五度标调)"
            groupby_label_text = "分组依据 / 曲线配色:"
            scale_label_text = "声学尺度 (纵轴单位):"

        ctk.CTkLabel(card2, text=groupby_label_text, font=self.font_small).grid(row=1, column=0, sticky="w", padx=(15, 5), pady=(5, 2))
        ctk.CTkLabel(card2, text=scale_label_text, font=self.font_small).grid(row=1, column=1, sticky="w", padx=(5, 15), pady=(5, 2))

        # Row 2 OptionMenus
        self.combo_groupby = ctk.CTkOptionMenu(card2, values=groupby_values, command=self._on_groupby_changed, **self.dropdown_kwargs)
        self.combo_groupby.set(groupby_default)
        self.combo_groupby.grid(row=2, column=0, sticky="ew", padx=(15, 5), pady=(0, 10))
        self._apply_custom_arrow(self.combo_groupby)

        self.combo_scale = ctk.CTkOptionMenu(card2, values=scale_values, command=lambda _: self.update_preview(), **self.dropdown_kwargs)
        self.combo_scale.set(scale_default)
        self.combo_scale.grid(row=2, column=1, sticky="ew", padx=(5, 15), pady=(0, 10))
        self._apply_custom_arrow(self.combo_scale)
        if self._is_formant_mode():
            self.combo_scale.configure(state="disabled")

        # Row 3 Labels
        ctk.CTkLabel(card2, text="图例位置:", font=self.font_small).grid(row=3, column=0, sticky="w", padx=(15, 5), pady=(5, 2))
        ctk.CTkLabel(card2, text="图像导出格式:", font=self.font_small).grid(row=3, column=1, sticky="w", padx=(5, 15), pady=(5, 2))

        # Row 4 OptionMenus
        self.combo_legend_loc = ctk.CTkOptionMenu(card2, values=["右上", "右下", "左上", "左下"], command=lambda _: self.update_preview(), **self.dropdown_kwargs)
        self.combo_legend_loc.set("右上")
        self.combo_legend_loc.grid(row=4, column=0, sticky="ew", padx=(15, 5), pady=(0, 10))
        self._apply_custom_arrow(self.combo_legend_loc)

        self.combo_format = ctk.CTkOptionMenu(card2, values=["PNG 图片 (.png)", "SVG 矢量图 (.svg)", "PDF 文档 (.pdf)"], command=self._on_format_changed, **self.dropdown_kwargs)
        self.combo_format.set("PNG 图片 (.png)")
        self.combo_format.grid(row=4, column=1, sticky="ew", padx=(5, 15), pady=(0, 10))
        self._apply_custom_arrow(self.combo_format)

        # Row 5 CheckBox
        self.cb_legend_outside = ctk.CTkCheckBox(
            card2, text="显示在图表主体的外侧", variable=self.var_legend_outside,
            font=self.font_small, checkbox_width=18, checkbox_height=18,
            fg_color=("#3B82F6", "#2563EB"), hover_color=("#4B5563", "#9CA3AF"), border_color=("#9CA3AF", "#4B5563"),
            command=self.update_preview
        )
        self.cb_legend_outside.grid(row=5, column=0, columnspan=2, sticky="w", padx=15, pady=(0, 15))

        # --- CARD 2.5: Image Size & Pixels Settings ---
        card_size = ctk.CTkFrame(self.left_scroll, fg_color=("#FFFFFF", "#1E293B"), border_width=1, border_color=("#E5E7EB", "#475569"), corner_radius=12)
        card_size.pack(fill=tk.X, **card_padding)
        card_size.grid_columnconfigure(0, weight=1)
        card_size.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(card_size, text="📐 图像尺寸与比例", font=self.font_title).grid(row=0, column=0, columnspan=2, sticky="w", padx=15, pady=(10, 5))

        # Ratio Mode Label & OptionMenu
        ctk.CTkLabel(card_size, text="图片比例 (宽/高):", font=self.font_small).grid(row=1, column=0, sticky="w", padx=(15, 5), pady=(5, 2))
        self.combo_ratio_mode = ctk.CTkOptionMenu(card_size, values=["默认", "4:3", "16:9", "3:2", "1:1", "16:10", "2:1", "自定义"], command=self._on_ratio_mode_changed, **self.dropdown_kwargs)
        self.combo_ratio_mode.set("默认")
        self.combo_ratio_mode.grid(row=2, column=0, sticky="ew", padx=(15, 5), pady=(0, 10))
        self._apply_custom_arrow(self.combo_ratio_mode)

        # Pixel Mode Label & OptionMenu
        ctk.CTkLabel(card_size, text="最小边像素 (对于PNG):", font=self.font_small).grid(row=1, column=1, sticky="w", padx=(5, 15), pady=(5, 2))
        self.combo_pixel_mode = ctk.CTkOptionMenu(card_size, values=["默认", "480 px", "600 px", "720 px", "1080 px", "1440 px", "2160 px", "自定义"], command=self._on_pixel_mode_changed, **self.dropdown_kwargs)
        self.combo_pixel_mode.set("默认")
        self.combo_pixel_mode.grid(row=2, column=1, sticky="ew", padx=(5, 15), pady=(0, 10))
        self._apply_custom_arrow(self.combo_pixel_mode)

        # Row 3: Custom Controls
        # Custom Ratio Slider Row (Left column)
        self.ratio_custom_frame = ctk.CTkFrame(card_size, fg_color="transparent")
        self.ratio_custom_frame.grid(row=3, column=0, sticky="nsew", padx=(15, 5), pady=(0, 10))
        self.ratio_custom_frame.grid_columnconfigure(0, weight=1)

        self.slider_ratio_custom = ctk.CTkSlider(self.ratio_custom_frame, from_=0.5, to=2.5, number_of_steps=40, variable=self.var_image_ratio_custom, command=self._on_ratio_slider_change)
        self.slider_ratio_custom.grid(row=0, column=0, sticky="ew")
        self.slider_ratio_custom.configure(state="disabled")

        self.lbl_ratio_val = ctk.CTkLabel(self.ratio_custom_frame, text="1.50", font=self.font_small)
        self.lbl_ratio_val.grid(row=0, column=1, padx=(5, 0))

        # Custom Pixel Entry Row (Right column)
        self.pixel_custom_frame = ctk.CTkFrame(card_size, fg_color="transparent")
        self.pixel_custom_frame.grid(row=3, column=1, sticky="nsew", padx=(5, 15), pady=(0, 10))
        self.pixel_custom_frame.grid_columnconfigure(0, weight=1)

        self.entry_pixel_custom = ctk.CTkEntry(self.pixel_custom_frame, placeholder_text="1080", font=self.font_small, height=28, border_width=1, border_color="#D1D5DB")
        self.entry_pixel_custom.insert(0, "1080")
        self.entry_pixel_custom.grid(row=0, column=0, sticky="ew")
        self.entry_pixel_custom.configure(state="disabled")
        self.entry_pixel_custom.bind("<KeyRelease>", lambda e: self.trigger_preview_update())

        # --- CARD 3: Dynamic Options Frame ---
        self.dynamic_card = ctk.CTkFrame(self.left_scroll, fg_color=("#FFFFFF", "#1E293B"), border_width=1, border_color=("#E5E7EB", "#475569"), corner_radius=12)
        self.dynamic_card.pack(fill=tk.X, **card_padding)

        self.dynamic_title = ctk.CTkLabel(self.dynamic_card, text="⚙️ 声调轮廓图专有选项", font=self.font_title)
        self.dynamic_title.pack(anchor="w", padx=15, pady=(10, 5))

        self.dynamic_content_frame = ctk.CTkFrame(self.dynamic_card, fg_color="transparent")
        self.dynamic_content_frame.pack(fill=tk.X, padx=15, pady=(0, 15))

        # Build initial dynamic UI for Tone Contour / Formant Space
        if self._is_formant_mode():
            self.dynamic_title.configure(text="⚙️ 元音空间图专有选项")
            self._build_formant_space_settings()
        else:
            self.dynamic_title.configure(text="⚙️ 声调轮廓图专有选项")
            self._build_contour_settings()

        # --- CARD 4: Group Filter ---
        self.card_filter = ctk.CTkFrame(self.left_scroll, fg_color=("#FFFFFF", "#1E293B"), border_width=1, border_color=("#E5E7EB", "#475569"), corner_radius=12)
        self.card_filter.pack(fill=tk.X, **card_padding)

        ctk.CTkLabel(self.card_filter, text="🎯 组别筛选器", font=self.font_title).pack(anchor="w", padx=15, pady=(10, 5))

        # Search Entry & Buttons Row
        search_btn_row = ctk.CTkFrame(self.card_filter, fg_color="transparent")
        search_btn_row.pack(fill=tk.X, padx=15, pady=(0, 5))

        self.search_group_var = ctk.StringVar()
        self.search_group_var.trace_add("write", lambda *args: self._filter_groups_list())

        self.entry_search_group = ctk.CTkEntry(
            search_btn_row, placeholder_text="搜索组别...", textvariable=self.search_group_var,
            font=self.font_small, height=32, fg_color="white", text_color="#1F2937",
            border_width=1, border_color="#E5E7EB", corner_radius=16
        )
        self.entry_search_group.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))

        # Select All & Invert buttons packed to the right of the search entry
        ctk.CTkButton(
            search_btn_row, text="全选", width=55, height=26, corner_radius=13,
            font=self.font_small, command=self._select_all_groups
        ).pack(side=tk.LEFT, padx=2)

        ctk.CTkButton(
            search_btn_row, text="反选", width=55, height=26, corner_radius=13,
            font=self.font_small, command=self._reverse_groups
        ).pack(side=tk.LEFT, padx=2)

        # Scrollable Frame for Group list
        self.filter_scroll = ctk.CTkScrollableFrame(self.card_filter, height=120, fg_color="transparent")
        self.filter_scroll.pack(fill=tk.X, padx=10, pady=5)
        try:
            self.filter_scroll._parent_canvas.configure(yscrollincrement=15)
        except Exception:
            pass

        # Bind enter/leave to self.filter_scroll to disable parent left_scroll scrolling
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
        util_btn_frame2.pack(fill=tk.X, padx=15, pady=(2, 10))

        self.btn_toggle_sort = ctk.CTkButton(
            util_btn_frame2, text="按样本量排序", width=95, height=24, corner_radius=12, font=self.font_small,
            fg_color=("#E5E7EB", "#374151"), text_color=("#374151", "#E5E7EB"), hover_color=("#D1D5DB", "#4B5563"),
            command=self._toggle_groups_sorting
        )
        self.btn_toggle_sort.pack(side=tk.LEFT, padx=2)

        ctk.CTkLabel(util_btn_frame2, text="样本量 >", font=self.font_small).pack(side=tk.LEFT, padx=(10, 2))
        self.entry_min_count = ctk.CTkEntry(util_btn_frame2, width=40, height=24, font=self.font_small, justify="center")
        self.entry_min_count.insert(0, "0")
        self.entry_min_count.pack(side=tk.LEFT, padx=2)
        self.entry_min_count.bind("<KeyRelease>", lambda e: self._populate_groups_list())
        self._bind_left_scroll_wheel_recursive(self.left_scroll)

    def _on_filter_scroll_wheel(self, event):
        canvas = getattr(self.filter_scroll, "_parent_canvas", None)
        if canvas is None:
            return "break"

        if getattr(event, "num", None) == 4:
            steps = -5  # 5 units = 75 pixels
        elif getattr(event, "num", None) == 5:
            steps = 5   # 5 units = 75 pixels
        else:
            delta = getattr(event, "delta", 0)
            if delta == 0:
                return "break"
            if abs(delta) >= 120:
                steps = -int(delta / 120) * 5
            else:
                steps = -delta * 5

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
            steps = -60  # scroll up
        elif getattr(event, "num", None) == 5:
            steps = 60   # scroll down
        else:
            delta = getattr(event, "delta", 0)
            if delta == 0:
                return "break"
            if abs(delta) >= 120:
                steps = -int(delta / 120) * 60
            else:
                steps = -delta * 60

        try:
            canvas.configure(yscrollincrement=1)
        except Exception:
            pass

        if canvas.yview() != (0.0, 1.0):
            canvas.yview_scroll(steps, "units")
        return "break"

    def _bind_left_scroll_wheel_recursive(self, widget):
        # Do not bind widgets inside filter_scroll to left_scroll's wheel event
        if widget == getattr(self, "filter_scroll", None):
            return

        if not getattr(widget, "_phontracer_left_wheel_bound", False):
            for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
                widget.bind(sequence, self._on_left_scroll_wheel, add="+")
            widget._phontracer_left_wheel_bound = True

        for child in widget.winfo_children():
            self._bind_left_scroll_wheel_recursive(child)

    def _on_type_changed(self, val):
        self.current_group_page = 0
        # Update dynamic options card UI based on chart type selection
        for widget in self.dynamic_content_frame.winfo_children():
            widget.destroy()

        if val == "声调轮廓图":
            self.var_chart_type.set("contour")
            self.dynamic_title.configure(text="⚙️ 声调轮廓图专有选项")
            self._build_contour_settings()
        elif val == "声调分布图":
            self.var_chart_type.set("distribution")
            self.dynamic_title.configure(text="⚙️ 声调分布图专有选项")
            self._build_distribution_settings()
        elif val == "时序密度图":
            self.var_chart_type.set("density")
            self.dynamic_title.configure(text="⚙️ 时序密度图专有选项")
            self._build_density_settings()
        elif val == "数据质量检查":
            self.var_chart_type.set("quality")
            self.dynamic_title.configure(text="⚙️ 数据质量检查专有选项")
            self._build_quality_settings()
        elif val == "声调组别概览图":
            self.var_chart_type.set("overview_heatmap")
            self.dynamic_title.configure(text="⚙️ 声调组别概览图专有选项")
            self._build_overview_heatmap_settings()
            self.combo_groupby.set("按词语")
        elif val == "元音共振峰空间图":
            self.var_chart_type.set("formant_space")
            self.dynamic_title.configure(text="⚙️ 元音空间图专有选项")
            self._build_formant_space_settings()
        elif val == "共振峰时序轨迹图":
            self.var_chart_type.set("formant_trajectory")
            self.dynamic_title.configure(text="⚙️ 共振峰轨迹图专有选项")
            self._build_formant_trajectory_settings()
        elif val == "共振峰时空密度图":
            self.var_chart_type.set("formant_density")
            self.dynamic_title.configure(text="⚙️ 共振峰时空密度图专有选项")
            self._build_formant_density_settings()
        elif val == "共振峰组别概览图":
            self.var_chart_type.set("formant_overview_heatmap")
            self.dynamic_title.configure(text="⚙️ 共振峰组别概览图专有选项")
            self._build_formant_overview_heatmap_settings()
            self.combo_groupby.set("按词语")

        self._bind_left_scroll_wheel_recursive(self.dynamic_content_frame)
        self.update_preview()

    def _on_scope_changed(self, val):
        self.current_preview_page = 0
        self.current_group_page = 0
        if "整合" in val:
            self.var_export_scope.set("integrated")
            # In F0 integrated mode we force T-value scale; formant mode stays in Hz.
            if self._is_formant_mode():
                self.combo_scale.set("Hz (共振峰频率)")
                self.combo_scale.configure(state="disabled")
            else:
                self.combo_scale.set("T 值 (五度标调)")
                self.combo_scale.configure(state="disabled")
        elif "分别" in val:
            self.var_export_scope.set("separate")
            if self._is_formant_mode():
                self.combo_scale.set("Hz (共振峰频率)")
                self.combo_scale.configure(state="disabled")
            else:
                self.combo_scale.configure(state="normal")
        else:
            self.var_export_scope.set("active")
            if self._is_formant_mode():
                self.combo_scale.set("Hz (共振峰频率)")
                self.combo_scale.configure(state="disabled")
            else:
                self.combo_scale.configure(state="normal")
        self.update_preview()

    def _on_groupby_changed(self, val):
        self.current_group_page = 0
        self.update_preview()

    def _on_intention_changed(self, val):
        if self._is_formant_mode():
            if "论文主图" in val:
                selected_count = sum(1 for v in self.group_checkbox_vars.values() if v.get())
                if selected_count > 8 and messagebox.askyesno("论文主图模式", f"当前选中了 {selected_count} 个组，论文主图建议展示 8 组以内以保证可读性。\n是否自动精选样本量最大的 8 组？"):
                    self._apply_featured_contrast_mode()
                if self.var_chart_type.get() == "formant_space":
                    self.combo_formant_label_mode.set("显示分组标签")
            elif "附录图册" in val:
                for var in self.group_checkbox_vars.values():
                    var.set(True)
                self._populate_groups_list()
                self.combo_format.set("PDF 文档 (.pdf)")
                self._on_group_filter_changed()
                messagebox.showinfo("附录图册模式", "已选中所有组别，并将导出格式设为 PDF。导出时将自动分页绘制完整图册。")
            return

        if "论文主图" in val:
            selected_count = sum(1 for v in self.group_checkbox_vars.values() if v.get())
            if selected_count > 8:
                if messagebox.askyesno("论文主图模式", f"当前选中了 {selected_count} 个组，论文主图建议展示 8 组以内以保证可读性。\n是否自动精选样本量最大的 8 组？"):
                    self._apply_featured_contrast_mode()

            if self.var_chart_type.get() == "quality" or self.var_chart_type.get() == "overview_heatmap":
                self.combo_type.set("声调轮廓图")
                self._on_type_changed("声调轮廓图")

            if self.var_chart_type.get() == "contour":
                self.combo_contour_content.set("平均曲线 + 置信区间阴影")

        elif "附录图册" in val:
            for var in self.group_checkbox_vars.values():
                var.set(True)
            self._populate_groups_list()
            self.combo_format.set("PDF 文档 (.pdf)")

            if self.var_chart_type.get() == "quality":
                self.combo_type.set("声调轮廓图")
                self._on_type_changed("声调轮廓图")

            self._on_group_filter_changed()
            messagebox.showinfo("附录图册模式", "已选中所有组别，并将导出格式设为 PDF。导出时将自动分页绘制完整图册。")

        elif "数据诊断" in val:
            self.combo_type.set("数据质量检查")
            self._on_type_changed("数据质量检查")
            self.var_qc_view.set("raw_overlay")
            self.combo_qc_view.set("个体 Raw F0 曲线叠加 (异常高亮)")

            for var in self.group_checkbox_vars.values():
                var.set(True)
            self._populate_groups_list()
            self._on_group_filter_changed()
            messagebox.showinfo("数据诊断模式", "已切换至“数据质量检查”视图，将重点排查 F0 缺失、异常与变异。")

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
            messagebox.showinfo("提示", "当前主界面的目录树中没有选中任何项。\n请先在主界面目录树中选择组别或词条。")
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
            messagebox.showinfo("提示", "未能在当前目录树选中项中识别到有效组别。")
            return

        for g, var in self.group_checkbox_vars.items():
            var.set(g in selected_groups)

        self._populate_groups_list()
        self._on_group_filter_changed()

    def _toggle_groups_sorting(self):
        self.sort_by_count = not getattr(self, 'sort_by_count', False)
        if self.sort_by_count:
            self.btn_toggle_sort.configure(fg_color=("#3B82F6", "#2563EB"), text_color="white")
        else:
            self.btn_toggle_sort.configure(fg_color=("#E5E7EB", "#374151"), text_color=("#374151", "#E5E7EB"))
        self._populate_groups_list()

    def _populate_groups_list(self):
        for w in self.filter_scroll.winfo_children():
            w.destroy()

        search_query = self.search_group_var.get().strip().lower()

        min_count = 0
        try:
            val_str = self.entry_min_count.get().strip()
            if val_str:
                min_count = int(val_str)
        except ValueError:
            pass

        if getattr(self, 'sort_by_count', False):
            groups_to_draw = sorted(self.available_groups, key=lambda g: self.group_counts.get(g, 0), reverse=True)
        else:
            groups_to_draw = self.available_groups

        for g in groups_to_draw:
            count = self.group_counts.get(g, 0)
            if count < min_count:
                continue
            if search_query and search_query not in g.lower():
                continue

            var = self.group_checkbox_vars[g]
            cb = ctk.CTkCheckBox(
                self.filter_scroll, text=f"{g} ({count}项)", variable=var,
                font=self.font_small, checkbox_width=18, checkbox_height=18,
                fg_color=("#3B82F6", "#2563EB"), hover_color=("#4B5563", "#9CA3AF"), border_color=("#9CA3AF", "#4B5563"),
                command=self._on_group_filter_changed
            )
            cb.pack(anchor="w", padx=10, pady=3)

        self._bind_filter_scroll_wheel_recursive(self.filter_scroll)

    def _on_group_filter_changed(self):
        self.current_preview_page = 0
        self.current_group_page = 0
        self.trigger_preview_update()

    def _select_all_groups(self):
        for var in self.group_checkbox_vars.values():
            var.set(True)
        self._on_group_filter_changed()

    def _reverse_groups(self):
        for var in self.group_checkbox_vars.values():
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

    def _build_overview_heatmap_settings(self):
        ctk.CTkLabel(self.dynamic_content_frame, text="热图展示维度:", font=self.font_small).pack(anchor="w", pady=(5, 2))
        self.combo_overview_metric = ctk.CTkOptionMenu(self.dynamic_content_frame, values=["均值热图 (Mean Map)", "标准差热图 (SD Map)"], command=lambda _: self.trigger_preview_update(), **self.dropdown_kwargs)
        self.combo_overview_metric.set("均值热图 (Mean Map)")
        self.combo_overview_metric.pack(fill=tk.X, pady=(2, 10))
        self._apply_custom_arrow(self.combo_overview_metric)

    def _build_formant_overview_heatmap_settings(self):
        ctk.CTkLabel(self.dynamic_content_frame, text="热图展示维度:", font=self.font_small).pack(anchor="w", pady=(5, 2))
        self.combo_overview_metric = ctk.CTkOptionMenu(
            self.dynamic_content_frame,
            values=["均值热图 (Mean Map)", "标准差热图 (SD Map)"],
            command=lambda _: self.trigger_preview_update(),
            **self.dropdown_kwargs
        )
        self.combo_overview_metric.set("均值热图 (Mean Map)")
        self.combo_overview_metric.pack(fill=tk.X, pady=(2, 8))
        self._apply_custom_arrow(self.combo_overview_metric)

        ctk.CTkLabel(self.dynamic_content_frame, text="共振峰轨道模式:", font=self.font_small).pack(anchor="w", pady=(6, 2))
        self.combo_formant_overview_mode = ctk.CTkOptionMenu(
            self.dynamic_content_frame,
            values=["F1 & F2 双轨", "F2 / F1 比值"],
            command=lambda _: self.trigger_preview_update(),
            **self.dropdown_kwargs
        )
        self.combo_formant_overview_mode.set(self.var_formant_overview_mode.get())
        self.combo_formant_overview_mode.pack(fill=tk.X, pady=(2, 8))
        self._apply_custom_arrow(self.combo_formant_overview_mode)

        ctk.CTkLabel(self.dynamic_content_frame, text="共振峰归一化:", font=self.font_small).pack(anchor="w", pady=(6, 2))
        self.combo_formant_normalization = ctk.CTkOptionMenu(
            self.dynamic_content_frame,
            values=["原始频率 (Hz)", "Lobanov z-score (学术标准)"],
            command=lambda _: self.trigger_preview_update(),
            **self.dropdown_kwargs
        )
        self.combo_formant_normalization.set(self.var_formant_normalization.get())
        self.combo_formant_normalization.pack(fill=tk.X, pady=(2, 10))
        self._apply_custom_arrow(self.combo_formant_normalization)

    # --- DYNAMIC CONFIGURATION UI BUILDERS ---
    def _build_contour_settings(self):
        # X-Axis scale
        ctk.CTkLabel(self.dynamic_content_frame, text="横轴展现形式:", font=self.font_small).pack(anchor="w", pady=(5, 2))
        self.combo_contour_x = ctk.CTkOptionMenu(self.dynamic_content_frame, values=["归一化采样点", "真实物理时长"], command=lambda _: self.trigger_preview_update(), **self.dropdown_kwargs)
        self.combo_contour_x.set("归一化采样点")
        self.combo_contour_x.pack(fill=tk.X, pady=2)
        self._apply_custom_arrow(self.combo_contour_x)

        # Curve Content
        ctk.CTkLabel(self.dynamic_content_frame, text="曲线展示要素:", font=self.font_small).pack(anchor="w", pady=(5, 2))
        self.combo_contour_content = ctk.CTkOptionMenu(self.dynamic_content_frame, values=["仅组别平均曲线", "平均曲线 + 个体浅色细线", "平均曲线 + 置信区间阴影"], command=lambda _: self.trigger_preview_update(), **self.dropdown_kwargs)
        self.combo_contour_content.set("仅组别平均曲线")
        self.combo_contour_content.pack(fill=tk.X, pady=2)
        self._apply_custom_arrow(self.combo_contour_content)

        # Facet By
        ctk.CTkLabel(self.dynamic_content_frame, text="分面子图排版 (Facet):", font=self.font_small).pack(anchor="w", pady=(5, 2))
        self.combo_contour_facet = ctk.CTkOptionMenu(self.dynamic_content_frame, values=["单图展示 (不分面)", "按声调类型分面", "按音节位置分面"], command=lambda _: self.trigger_preview_update(), **self.dropdown_kwargs)
        self.combo_contour_facet.set("单图展示 (不分面)")
        self.combo_contour_facet.pack(fill=tk.X, pady=(2, 10))
        self._apply_custom_arrow(self.combo_contour_facet)

    def _build_distribution_settings(self):
        # Distribution view type
        ctk.CTkLabel(self.dynamic_content_frame, text="分布展示类型:", font=self.font_small).pack(anchor="w", pady=(5, 2))
        self.combo_dist_type = ctk.CTkOptionMenu(self.dynamic_content_frame, values=["测量点精细分布", "起-中-终三点比较", "调域范围跨度图", "变异程度(CV)比较"], command=self._on_dist_type_changed, **self.dropdown_kwargs)
        self.combo_dist_type.set("测量点精细分布")
        self.combo_dist_type.pack(fill=tk.X, pady=2)
        self._apply_custom_arrow(self.combo_dist_type)

        # Plot style (Boxplot / Violin)
        self.lbl_dist_style = ctk.CTkLabel(self.dynamic_content_frame, text="统计图样式:", font=self.font_small)
        self.lbl_dist_style.pack(anchor="w", pady=(5, 2))
        self.combo_dist_style = ctk.CTkOptionMenu(self.dynamic_content_frame, values=["科学箱线图 (Box Plot)", "小提琴图 (Violin Plot)"], command=lambda _: self.trigger_preview_update(), **self.dropdown_kwargs)
        self.combo_dist_style.set("科学箱线图 (Box Plot)")
        self.combo_dist_style.pack(fill=tk.X, pady=(2, 10))
        self._apply_custom_arrow(self.combo_dist_style)

    def _on_dist_type_changed(self, val):
        if val in ["调域范围跨度图", "变异程度(CV)比较"]:
            self.combo_dist_style.configure(state="disabled")
        else:
            self.combo_dist_style.configure(state="normal")
        self.trigger_preview_update()

    def _build_density_settings(self):
        # KDE Bandwidth
        ctk.CTkLabel(self.dynamic_content_frame, text="核密度带宽 (Bandwidth):", font=self.font_small).pack(anchor="w", pady=(5, 2))
        bw_val_frame = ctk.CTkFrame(self.dynamic_content_frame, fg_color="transparent")
        bw_val_frame.pack(fill=tk.X)
        self.slider_density_bw = ctk.CTkSlider(bw_val_frame, from_=0.05, to=0.50, number_of_steps=45, command=self._on_bw_slider_changed)
        self.slider_density_bw.set(0.15)
        self.slider_density_bw.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.lbl_bw_val = ctk.CTkLabel(bw_val_frame, text="0.15", font=self.font_main, width=40)
        self.lbl_bw_val.pack(side=tk.RIGHT, padx=(5, 0))

        # F0 Truncation Mode
        ctk.CTkLabel(self.dynamic_content_frame, text="截断极值处理:", font=self.font_small).pack(anchor="w", pady=(5, 2))
        self.combo_density_f0 = ctk.CTkOptionMenu(self.dynamic_content_frame, values=["分位数自动截断 (5%-95%)", "极值自动范围 (Min-Max)", "手动指定频率范围 (Hz)"], command=self._on_density_f0_changed, **self.dropdown_kwargs)
        self.combo_density_f0.set("分位数自动截断 (5%-95%)")
        self.combo_density_f0.pack(fill=tk.X, pady=2)
        self._apply_custom_arrow(self.combo_density_f0)

        # Sub-entries for manual / percentile
        self.manual_f0_frame = ctk.CTkFrame(self.dynamic_content_frame, fg_color="transparent")
        self.pct_f0_frame = ctk.CTkFrame(self.dynamic_content_frame, fg_color="transparent")

        # Manual entries
        ctk.CTkLabel(self.manual_f0_frame, text="频率 (Hz):", font=self.font_small).pack(side=tk.LEFT)
        self.entry_min_hz = ctk.CTkEntry(self.manual_f0_frame, width=55, font=self.font_small)
        self.entry_min_hz.insert(0, "75")
        self.entry_min_hz.pack(side=tk.LEFT, padx=3)
        ctk.CTkLabel(self.manual_f0_frame, text="~", font=self.font_small).pack(side=tk.LEFT)
        self.entry_max_hz = ctk.CTkEntry(self.manual_f0_frame, width=55, font=self.font_small)
        self.entry_max_hz.insert(0, "600")
        self.entry_max_hz.pack(side=tk.LEFT, padx=3)

        # Percentile entries
        ctk.CTkLabel(self.pct_f0_frame, text="分位数 (%):", font=self.font_small).pack(side=tk.LEFT)
        self.entry_low_p = ctk.CTkEntry(self.pct_f0_frame, width=45, font=self.font_small)
        self.entry_low_p.insert(0, "5")
        self.entry_low_p.pack(side=tk.LEFT, padx=3)
        ctk.CTkLabel(self.pct_f0_frame, text="~", font=self.font_small).pack(side=tk.LEFT)
        self.entry_high_p = ctk.CTkEntry(self.pct_f0_frame, width=45, font=self.font_small)
        self.entry_high_p.insert(0, "95")
        self.entry_high_p.pack(side=tk.LEFT, padx=3)

        # Show default
        self.pct_f0_frame.pack(fill=tk.X, pady=2)

        # Facet Density
        ctk.CTkLabel(self.dynamic_content_frame, text="排版分面依据:", font=self.font_small).pack(anchor="w", pady=(5, 2))
        self.combo_density_facet = ctk.CTkOptionMenu(self.dynamic_content_frame, values=["声调类型分面 (默认)", "不分面 (混合叠加)", "按词语分面"], command=lambda _: self.trigger_preview_update(), **self.dropdown_kwargs)
        self.combo_density_facet.set("声调类型分面 (默认)")
        self.combo_density_facet.pack(fill=tk.X, pady=(2, 10))
        self._apply_custom_arrow(self.combo_density_facet)

    def _on_bw_slider_changed(self, val):
        self.var_density_bw.set(float(val))
        self.lbl_bw_val.configure(text=f"{float(val):.2f}")
        # Use simple debounce by only updating on actual release if desired,
        # but here we update directly (Matplotlib is fast enough)
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

    def _build_quality_settings(self):
        ctk.CTkLabel(self.dynamic_content_frame, text="数据质检视图类型:", font=self.font_small).pack(anchor="w", pady=(5, 2))
        self.combo_qc_view = ctk.CTkOptionMenu(self.dynamic_content_frame, values=["个体 Raw F0 曲线叠加 (异常高亮)", "有效点比例 (Active Ratio) 分布箱线图", "发音人基频均值与调域跨度散点图"], command=self._on_qc_view_changed, **self.dropdown_kwargs)
        self.combo_qc_view.set("个体 Raw F0 曲线叠加 (异常高亮)")
        self.combo_qc_view.pack(fill=tk.X, pady=(2, 10))
        self._apply_custom_arrow(self.combo_qc_view)
        self.var_qc_view.set("raw_overlay")

    def _on_qc_view_changed(self, val):
        if "个体" in val:
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

        self._debounce_timer_id = self.after(300, self._debounced_update_preview)

    def _debounced_update_preview(self):
        self._debounce_timer_id = None
        self.update_preview()

    def _snapshot_render_params(self):
        keys = [
            'chart_type', 'export_scope', 'groupby', 'scale', 'format',
            'contour_x', 'contour_content', 'contour_facet',
            'dist_type', 'dist_style',
            'density_bw', 'density_f0_mode', 'density_facet',
            'density_normalization', 'density_p_low', 'density_p_high',
            'density_m_min', 'density_m_max', 'density_max_points',
            'formant_density_overlay', 'formant_density_bw', 'formant_density_facet',
            'formant_density_show_raw', 'formant_density_show_contours',
            'formant_normalization', 'formant_axis_lock', 'formant_overview_mode',
            'qc_view', 'overview_metric',
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
                widget.destroy()
            canvas = FigureCanvasTkAgg(fig, master=self.preview_container)
            canvas.draw()
            canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
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

        # Clear existing preview canvas
        for widget in self.preview_container.winfo_children():
            widget.destroy()

        preview_status = ctk.CTkFrame(self.preview_container, fg_color="transparent")
        preview_status.grid(row=0, column=0)
        self.preview_lbl = ctk.CTkLabel(preview_status, text="正在实时渲染图表，请稍候...", font=self.font_title, text_color="#6B7280")
        self.preview_lbl.pack(pady=(0, 8))
        self.preview_progress = ctk.CTkProgressBar(preview_status, width=220, height=8)
        self.preview_progress.set(0.05)
        self.preview_progress.pack(pady=(0, 10))
        ctk.CTkButton(preview_status, text="取消预览", width=90, height=30, command=self._cancel_preview_render).pack()

        scope = self.var_export_scope.get()
        if scope == "separate" and len(self.all_speakers) > 1:
            self.pagination_frame.grid(row=1, column=0, pady=(0, 10))
            idx = getattr(self, 'current_preview_page', 0)
            if idx < 0 or idx >= len(self.all_speakers):
                idx = 0
                self.current_preview_page = 0
            spk_name = self.all_speakers[idx].name
            self.lbl_page_info.configure(text=f"发音人: {spk_name} ({idx+1}/{len(self.all_speakers)})")
        else:
            self.pagination_frame.grid_forget()

        self.update_idletasks()

        try:
            data = self._get_current_data_entries()
            if not data:
                analysis_mode = self.project_tree.app_state_params.get('analysis_mode', 'f0')
                if analysis_mode == 'formant':
                    msg = "❌ 没有检索到有效共振峰数据，请先完成共振峰分析。"
                else:
                    msg = "❌ 没有检索到有效基频曲线，请导入有发音数据的项目！"
                self.preview_lbl.configure(text=msg, text_color="#EF4444")
                self.group_pagination_frame.grid_forget()
                return

            groupby = self.combo_groupby.get()
            chart_type = self.var_chart_type.get()
            pagination_state = self._get_group_pagination_state(data, chart_type, groupby)
            total_groups = pagination_state['total_groups']
            total_pages = pagination_state['total_pages']
            is_paginated_heatmap = pagination_state['is_paginated_heatmap']
            show_group_pagination = (total_groups > 8 and not self._is_overview_heatmap_chart(chart_type)) or is_paginated_heatmap
            if show_group_pagination:
                self.group_pagination_frame.grid(row=2, column=0, pady=(0, 10))
                if self.current_group_page < 0:
                    self.current_group_page = 0
                elif self.current_group_page >= total_pages:
                    self.current_group_page = max(0, total_pages - 1)

                if is_paginated_heatmap:
                    current_chunk = pagination_state['pages'][self.current_group_page] if self.current_group_page < len(pagination_state['pages']) else []
                    tg_name = current_chunk[0]['group'] if current_chunk else ""
                    self.lbl_group_page_info.configure(
                        text=f"组别页码: {self.current_group_page+1}/{total_pages} (当前显示组别: {tg_name}，共 {len(current_chunk)} 个词语)"
                    )
                else:
                    self.lbl_group_page_info.configure(
                        text=f"组别页码: {self.current_group_page+1}/{total_pages} (当前显示第 {self.current_group_page*8+1}~{min(total_groups, (self.current_group_page+1)*8)} 组，共 {total_groups} 组)"
                    )
            else:
                self.group_pagination_frame.grid_forget()

            params_snapshot = self._snapshot_render_params()
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
                        progress_queue.put(("progress", {"progress": 0.10, "message": "正在准备图表数据..."}))
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
        all_entries = self._extract_active_data(self.all_speakers if self.all_speakers else [self.active_speaker])
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

    def _on_format_changed(self, val):
        self._update_pixel_options_state()
        self.update_preview()

    def _on_ratio_mode_changed(self, val):
        if val == "自定义":
            self.slider_ratio_custom.configure(state="normal")
        else:
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
        self.lbl_ratio_val.configure(text=f"{val:.2f}")
        self.on_preview_wrapper_configure()
        self.trigger_preview_update()

    def on_preview_wrapper_configure(self, event=None):
        if not hasattr(self, 'preview_wrapper') or not hasattr(self, 'preview_container'):
            return
        w_avail = self.preview_wrapper.winfo_width()
        h_avail = self.preview_wrapper.winfo_height()
        if w_avail < 50 or h_avail < 50:
            return

        ratio_mode = self.get_param('image_ratio_mode', '默认')
        custom_ratio = self.get_param('image_ratio_custom', 1.5)

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

    def _update_pixel_options_state(self):
        fmt = self.combo_format.get()
        is_vector = "svg" in fmt.lower() or "pdf" in fmt.lower()
        if is_vector:
            self.combo_pixel_mode.configure(state="disabled")
            self.entry_pixel_custom.configure(state="disabled")
        else:
            self.combo_pixel_mode.configure(state="normal")
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
        prog.title("导出进行中")
        prog.geometry("380x150")
        prog.resizable(False, False)
        prog.transient(self)
        prog.attributes('-topmost', True)
        prog.protocol("WM_DELETE_WINDOW", self._cancel_export_job)

        lbl = ctk.CTkLabel(prog, text="正在准备导出...", font=self.font_main)
        lbl.pack(pady=(20, 8))
        pbar = ctk.CTkProgressBar(prog, width=320)
        pbar.pack(pady=(0, 10))
        pbar.set(0)
        ctk.CTkButton(prog, text="取消导出", width=100, command=self._cancel_export_job).pack()

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

        # Sync active speaker before exporting
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
                if self._is_formant_mode():
                    name_suffix = "共振峰可视化图表"
                else:
                    name_suffix = "声调可视化图表"
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
