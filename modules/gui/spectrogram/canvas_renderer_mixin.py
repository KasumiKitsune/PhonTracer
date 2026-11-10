"""
Spectrogram plotting, pitch/formant line rendering, cursor blitting mixin.
"""
import tkinter as tk
import customtkinter as ctk
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import numpy as np
from modules.audio_core import SILENCE_AMPLITUDE_THRESHOLD
from modules.anomaly_detection import detect_pitch_anomaly_points
from modules.data_utils import split_into_syllables


class CanvasRendererMixin:

    def clear_canvas(self):
        self.dragging = None
        self.erasing = False
        self.restoring_points = False
        if self.is_playing:
            self._stop_playback()
        if self.eraser_mode:
            self.apply_eraser_changes()
        self.current_item = None
        if self.ax_nav:
            self.ax_nav.clear()
            self.ax_nav.set_xticks([])
            self.ax_nav.set_yticks([])
        self.nav_lens_patch = None
        self.nav_bound_lines.clear()
        self.nav_span_fills.clear()
        self.ax.clear()
        self.ax2.clear()
        self.ax2.yaxis.set_label_position("right")
        self.ax2.yaxis.tick_right()
        self.background = None
        self.pitch_main_line = None
        self.anomaly_line = None
        self.playback_domain_start = None
        self.playback_domain_end = None
        self.bound_lines.clear()
        self.span_fills.clear()
        self.char_texts.clear()
        self.undo_stack.clear()
        self.redo_stack.clear()
        self._clear_playback_selection(redraw=False)
        self.canvas.draw()
        self.var_t_start.set("0.000")
        self.var_t_end.set("0.000")
        self._update_playback_status()

    def load_item(self, item):
        self.dragging = None
        self.erasing = False
        self.restoring_points = False
        if self.is_playing:
            self._stop_playback()
        if self.eraser_mode:
            self.apply_eraser_changes()
            self._reset_eraser_session()
        self.current_item = item
        self.current_item_iid = None
        self.undo_stack.clear()
        self.redo_stack.clear()
        if self.app and hasattr(self.app, 'items'):
            for k, v in self.app.items.items():
                if v is item:
                    self.current_item_iid = k
                    break
        t_start = item.get('start')
        t_end = item.get('end')

        # 备份原始 F0 / Formant 数据以支持 Shift/右键局部恢复画笔
        if item.get('pitch_data') and 'freqs' in item['pitch_data'] and '_orig_pitch_freqs' not in item:
            item['_orig_pitch_freqs'] = np.array(item['pitch_data']['freqs'], copy=True)
        elif item.get('pitch') and '_orig_pitch_freqs' not in item:
            try:
                item['_orig_pitch_freqs'] = np.array(item['pitch'].selected_array['frequency'], copy=True)
            except Exception:
                pass
        if item.get('formant_data') and '_orig_formant_data' not in item:
            item['_orig_formant_data'] = {
                k: np.array(v, copy=True)
                for k, v in item['formant_data'].items()
                if isinstance(v, np.ndarray)
            }

        # Init chars_bounds
        label = item.get('label', '')
        from modules.data_utils import split_into_syllables
        syls = split_into_syllables(label)
        if 'chars_bounds' not in item or not item.get('chars_bounds'):
            inner_splits = item.get('inner_splits', [])
            splits = [t_start] + [s for s in inner_splits if t_start < s < t_end] + [t_end]
            if len(syls) > 1 and len(splits) != len(syls) + 1:
                splits = np.linspace(t_start, t_end, len(syls) + 1).tolist()
            elif len(syls) <= 1:
                splits = [t_start, t_end]

            chars_bounds = []
            for i in range(len(splits) - 1):
                chars_bounds.append([splits[i], splits[i + 1]])
            item['chars_bounds'] = chars_bounds

        # Update overall bounds to reflect max/min of char bounds
        c_bounds = item['chars_bounds']
        if c_bounds:
            item['start'] = c_bounds[0][0]
            item['end'] = c_bounds[-1][1]
            t_start = item['start']
            t_end = item['end']

        s_str = f"{t_start:.3f}" if t_start is not None else "0.000"
        e_str = f"{t_end:.3f}" if t_end is not None else "0.000"
        self.var_t_start.set(s_str)
        self.var_t_end.set(e_str)
        if hasattr(self, 'entry_t_start'):
            self.entry_t_start._last_val = s_str
            self.entry_t_start._last_applied_val = s_str
        if hasattr(self, 'entry_t_end'):
            self.entry_t_end._last_val = e_str
            self.entry_t_end._last_applied_val = e_str

        self.cursor_x = t_start  # Reset cursor position when loading new item
        self.cursor_char_index = 0 if item.get('chars_bounds') else None
        self.playback_domain_start = None
        self.playback_domain_end = None
        self._clear_playback_selection(redraw=False)
        self.plot_item_spectrogram()

    def plot_item_spectrogram(self):
        item = self.current_item
        if not item:
            return
        if not item.get('snd') or item.get('start') is None:
            return

        if self.ax_nav:
            self.ax_nav.clear()
        self.ax.clear()
        self.ax2.clear()
        # 显式将 ax2 的标签和刻度固定在右侧，防止 clear() 后回到左侧与 Frequency (Hz) 重叠
        self.ax2.yaxis.set_label_position("right")
        self.ax2.yaxis.tick_right()
        self.eraser_circle = None
        self.pitch_main_line = None
        self.anomaly_line = None
        self._pitch_view_indices = None

        self.bound_lines.clear()
        for fill in self.span_fills:
            try:
                fill.remove()
            except Exception:
                pass
        self.span_fills.clear()
        self.nav_bound_lines.clear()
        self.nav_span_fills.clear()
        self.nav_lens_patch = None

        self.char_texts.clear()
        self.cursor_line = None
        self.cursor_text = None
        self.selection_patch = None

        snd = item['snd']
        t_s, t_e = item['start'], item['end']
        chars_bounds = item.get('chars_bounds', [])
        label = item.get('label', '')
        total_dur = snd.get_total_duration()

        if self.switch_trim_silence and self.switch_trim_silence.get():
            mac_part = snd.extract_part(from_time=item['macro_start'], to_time=item['macro_end'])
            vals = mac_part.values[0]
            mac_xs = mac_part.xs()
            valid_idx = np.where(np.abs(vals) > SILENCE_AMPLITUDE_THRESHOLD)[0]
            if len(valid_idx) > 0:
                view_s = item['macro_start'] + mac_xs[valid_idx[0]]
                view_e = item['macro_start'] + mac_xs[valid_idx[-1]]
            else:
                view_s, view_e = item['macro_start'], item['macro_end']
            # 保留 ±0.08s 视野余量，防止首尾红线被 xlim 锁死无法向外微调
            view_s = max(0.0, min(view_s, t_s) - 0.08)
            view_e = min(total_dur, max(view_e, t_e) + 0.08)
        else:
            view_s = max(item['macro_start'] - 0.2, 0.0)
            view_e = min(item['macro_end'] + 0.2, total_dur)

        if view_e <= view_s:
            view_e = min(total_dur, view_s + 0.1)

        self.playback_domain_start = view_s
        self.playback_domain_end = view_e

        part = snd.extract_part(from_time=view_s, to_time=view_e)

        # --- 绘制顶部全局波形缩略导航条 (self.ax_nav) ---
        if self.ax_nav is not None:
            self.ax_nav.set_title(f"编辑区: {label}", pad=5, fontsize=11, fontweight='bold', color='#111827')
            self.ax_nav.set_xticks([])
            self.ax_nav.set_yticks([])
            self.ax_nav.set_xlim([view_s, view_e])
            try:
                w_xs = part.xs() + view_s
                w_ys = part.values[0]
                if len(w_xs) > 1200:
                    step = max(1, len(w_xs) // 1200)
                    w_xs = w_xs[::step]
                    w_ys = w_ys[::step]
                max_amp = float(np.max(np.abs(w_ys))) if len(w_ys) > 0 else 1.0
                if max_amp <= 0:
                    max_amp = 1.0
                self.ax_nav.plot(w_xs, w_ys, color='#64748B', linewidth=0.6, alpha=0.85)
                self.ax_nav.set_ylim([-max_amp * 1.15, max_amp * 1.15])
            except Exception:
                pass
            self.ax_nav.set_facecolor('#F8FAFC')
            for c_start, c_end in chars_bounds:
                n_span = self.ax_nav.axvspan(c_start, c_end, color='#93C5FD', alpha=0.35)
                n_ls = self.ax_nav.axvline(c_start, color='#EF4444', linewidth=1.0, alpha=0.8)
                n_le = self.ax_nav.axvline(c_end, color='#EF4444', linewidth=1.0, alpha=0.8)
                self.nav_span_fills.append(n_span)
                self.nav_bound_lines.append((n_ls, n_le))
            self.nav_lens_patch = self.ax_nav.axvspan(
                view_s, view_e, facecolor='#3B82F6', edgecolor='#1D4ED8', alpha=0.14, linewidth=1.0, zorder=6
            )

        app_params = getattr(self.app, 'last_params', {}) if self.app else {}
        analysis_mode = item.get('analysis_mode', app_params.get('analysis_mode', 'f0'))
        self.update_eraser_button_text()

        spec_max_freq = 5000.0
        if analysis_mode == 'formant':
            formant_max_hz = float(item.get('formant_max_hz', app_params.get('formant_max_hz', 5500.0)))
            spec_max_freq = max(5000.0, formant_max_hz)

        try:
            nyquist = part.sampling_frequency / 2.0
            spec_max_freq = min(spec_max_freq, nyquist)
        except Exception:
            pass

        # 轻量缓存 STFT 频谱矩阵，避免微调边界线或触发重绘时重复进行昂贵的科学计算
        cache_key = (id(snd), round(view_s, 6), round(view_e, 6), round(spec_max_freq, 2))
        cached_spec = getattr(self, '_spectrogram_cache', None)
        if cached_spec and cached_spec.get('snd') is snd and cached_spec.get('key') == cache_key:
            X = cached_spec['X']
            Y = cached_spec['Y']
            sg_db = cached_spec['sg_db']
        else:
            spectrogram = part.to_spectrogram(window_length=0.005, maximum_frequency=spec_max_freq)
            X = spectrogram.x_grid() + view_s
            Y = spectrogram.y_grid()
            vals = np.where(spectrogram.values > 0, spectrogram.values, 1e-10)
            sg_db = 10 * np.log10(vals)
            self._spectrogram_cache = {
                'snd': snd,
                'key': cache_key,
                'X': X,
                'Y': Y,
                'sg_db': sg_db,
            }

        sg_max = float(sg_db.max()) if sg_db.size > 0 else 0.0
        ext_x0 = float(X[0]) if len(X) > 0 else view_s
        ext_x1 = float(X[-1]) if len(X) > 0 else view_e
        ext_y0 = float(Y[0]) if len(Y) > 0 else 0.0
        ext_y1 = float(Y[-1]) if len(Y) > 0 else spec_max_freq
        self.ax.imshow(
            sg_db,
            extent=[ext_x0, ext_x1, ext_y0, ext_y1],
            origin='lower',
            aspect='auto',
            vmin=sg_max - 50.0,
            vmax=sg_max,
            cmap='Greys',
            interpolation='bilinear',
        )

        if analysis_mode == 'formant':
            self.ax2.set_visible(False)
            formant_max_hz = float(item.get('formant_max_hz', app_params.get('formant_max_hz', 5500.0)))
            try:
                nyquist = part.sampling_frequency / 2.0
                plot_max_hz = min(max(5000.0, formant_max_hz), nyquist)
            except Exception:
                plot_max_hz = max(5000.0, formant_max_hz)
            self.ax.set_ylim([0, plot_max_hz])
            self.ax.set_xlim([view_s, view_e])
            self.ax.set_ylabel("Frequency (Hz)")

            if item.get('formant_data'):
                f_xs = item['formant_data']['xs']
                f1_arr = item['formant_data']['f1']
                f2_arr = item['formant_data']['f2']
                mask = (f_xs >= view_s) & (f_xs <= view_e)
                f_xs_filtered = f_xs[mask]
                f1_plot = f1_arr[mask].copy()
                f2_plot = f2_arr[mask].copy()

                self.ax.scatter(
                    f_xs_filtered, f1_plot, s=18, color='#F97316', alpha=0.9, edgecolors='none', zorder=5, label="F1"
                )
                self.ax.scatter(
                    f_xs_filtered, f2_plot, s=18, color='#22C55E', alpha=0.9, edgecolors='none', zorder=5, label="F2"
                )

                show_f3 = bool(item.get("show_f3", app_params.get("show_f3", False)))
                f3_arr = item['formant_data'].get('f3') if show_f3 else None

                if f3_arr is not None:
                    f3_plot = f3_arr[mask].copy()
                    self.ax.scatter(
                        f_xs_filtered, f3_plot, s=18, color='#8B5CF6', alpha=0.9, edgecolors='none', zorder=5, label="F3"
                    )
                    self.erased_f3_layer = self.ax.scatter(
                        [], [], s=22, color='#DC2626', alpha=0.6, edgecolors='none', zorder=7, label="待剔除F3", animated=True
                    )
                else:
                    self.erased_f3_layer = None

                # 创建共振峰待剔除点图层 (animated=True 纳入 Blit 消除闪烁)
                self.erased_f1_layer = self.ax.scatter(
                    [], [], s=22, color='#DC2626', alpha=0.6, edgecolors='none', zorder=7, label="待剔除F1", animated=True
                )
                self.erased_f2_layer = self.ax.scatter(
                    [], [], s=22, color='#DC2626', alpha=0.6, edgecolors='none', zorder=7, label="待剔除F2", animated=True
                )

                if self.eraser_mode and hasattr(self, 'session_erased_formant_indices'):
                    self._remember_erased_formant_points(f_xs, f1_arr, f2_arr, f3_arr)
                    self._update_erased_formant_layers()
        else:
            self.ax2.set_visible(True)
            self.ax2.yaxis.set_label_position("right")
            self.ax2.yaxis.tick_right()
            self.ax.set_ylim([0, 5000])
            self.ax.set_xlim([view_s, view_e])
            self.ax.set_ylabel("Frequency (Hz)")

            if item.get('pitch_data'):
                p_xs_all = np.asarray(item['pitch_data']['xs'])
                p_freqs_all = np.asarray(item['pitch_data']['freqs'])
                mask = (p_xs_all >= view_s) & (p_xs_all <= view_e)
                self._pitch_view_indices = np.where(mask)[0]
                p_xs = p_xs_all[mask]
                p_vals = p_freqs_all[mask].astype(float, copy=True)
                p_vals[p_vals == 0] = np.nan
            elif item.get('pitch'):
                global_pitch = item['pitch']
                p_xs_all = np.asarray(global_pitch.xs())
                p_freqs_all = np.asarray(global_pitch.selected_array['frequency'])
                mask = (p_xs_all >= view_s) & (p_xs_all <= view_e)
                self._pitch_view_indices = np.where(mask)[0]
                p_xs = p_xs_all[mask]
                p_vals = p_freqs_all[mask].astype(float, copy=True)
                p_vals[p_vals == 0] = np.nan
            else:
                self._pitch_view_indices = np.array([], dtype=int)
                p_xs = np.array([])
                p_vals = np.array([])

            if self.eraser_mode and hasattr(self, 'session_erased_pitch_indices') and self.session_erased_pitch_indices:
                if self._pitch_view_indices is not None and len(self._pitch_view_indices) == len(p_vals):
                    for local_i, global_i in enumerate(self._pitch_view_indices):
                        if int(global_i) in self.session_erased_pitch_indices:
                            p_vals[local_i] = np.nan

            self.pitch_main_line = self.ax2.plot(
                p_xs, p_vals, '-o', markersize=4, linewidth=1.5, color='#3B82F6', zorder=5
            )[0]

            # 创建 F0 待擦除点图层 (animated=True 纳入 Blit 消除闪烁)
            self.erased_pitch_layer = self.ax2.plot(
                [], [], 'o', color='#DC2626', markersize=5, zorder=7, label="待擦除点", alpha=0.6, animated=True
            )[0]
            if (
                self.eraser_mode
                and item.get('pitch_data')
                and hasattr(self, 'session_erased_pitch_indices')
                and self.session_erased_pitch_indices
            ):
                xs = item['pitch_data']['xs']
                freqs = item['pitch_data']['freqs']
                self._remember_erased_pitch_points(xs, freqs)
                self._update_erased_pitch_layer()

            # Highlight anomalies (pitch jumps)
            p_xs_raw, p_freqs_raw = None, None
            if item.get('pitch_data'):
                p_xs_raw = np.asarray(item['pitch_data'].get('xs'))
                p_freqs_raw = np.asarray(item['pitch_data'].get('freqs'))
            elif item.get('pitch'):
                global_pitch = item['pitch']
                try:
                    p_xs_raw = np.asarray(global_pitch.xs())
                    p_freqs_raw = np.asarray(global_pitch.selected_array['frequency'])
                except Exception:
                    pass

            anomaly_points = []
            if p_xs_raw is not None and p_freqs_raw is not None and len(p_xs_raw) > 0:
                bounds = chars_bounds if chars_bounds else [[t_s, t_e]]
                anomaly_points = detect_pitch_anomaly_points(
                    p_xs_raw, p_freqs_raw, bounds=bounds, start=t_s, end=t_e
                )

                if anomaly_points and not self.eraser_mode:
                    jumps_x = [t for t, _ in anomaly_points]
                    jumps_y = [f for _, f in anomaly_points]
                    self.anomaly_line = self.ax2.plot(
                        jumps_x, jumps_y, 'o', color='#EF4444', markersize=6, zorder=6, label="异常点"
                    )[0]

            pitch_floor = float(item.get('pitch_floor', app_params.get('pitch_floor', 75.0)))
            pitch_ceiling = float(item.get('pitch_ceiling', app_params.get('pitch_ceiling', 600.0)))
            visible_vals = p_vals[np.isfinite(p_vals)] if len(p_vals) else np.array([])
            visible_high = float(np.max(visible_vals)) if len(visible_vals) else pitch_ceiling
            anomaly_high = max((f for _, f in anomaly_points), default=pitch_ceiling)
            y_min = max(0.0, min(50.0, pitch_floor - 25.0))
            y_max = max(500.0, pitch_ceiling, visible_high, anomaly_high) + 25.0
            self.ax2.set_ylim([y_min, y_max])
            self.ax2.yaxis.set_label_position("right")
            self.ax2.yaxis.tick_right()
            self.ax2.set_ylabel("F0 (Hz)", color='#3B82F6')
            self.ax2.tick_params(axis='y', labelcolor='#3B82F6', labelright=True, labelleft=False, right=True, left=False)

        from modules.data_utils import split_into_syllables
        syls = split_into_syllables(label)
        for i, (c_start, c_end) in enumerate(chars_bounds):
            line_s = self.ax.axvline(c_start, color='#EF4444', linestyle='-', linewidth=2)
            line_e = self.ax.axvline(c_end, color='#EF4444', linestyle='-', linewidth=2)
            self.bound_lines.append((line_s, line_e))
            span = self.ax.axvspan(c_start, c_end, color='#BFDBFE', alpha=0.35)
            self.span_fills.append(span)

            if i < len(syls):
                cx = (c_start + c_end) / 2
                is_active = (i == self.cursor_char_index)
                txt = self.ax.text(
                    cx,
                    0.90,
                    self._format_capsule_label(syls[i], c_start, c_end),
                    transform=self.ax.get_xaxis_transform(),
                    color='#1D4ED8' if is_active else '#111827',
                    fontsize=10.5,
                    ha='center',
                    va='top',
                    fontweight='bold',
                    bbox=dict(
                        boxstyle="round,pad=0.25",
                        fc="#EFF6FF" if is_active else "#FFFFFF",
                        ec="#2563EB" if is_active else "#CBD5E1",
                        lw=1.4 if is_active else 0.9,
                        alpha=0.92,
                    ),
                    zorder=8,
                )
                self.char_texts.append(txt)

        if self.cursor_x is None:
            self.cursor_x = t_s
        self.cursor_line = self.ax.axvline(
            self.cursor_x,
            color='#1B5E20',
            linestyle='--',
            linewidth=1.5,
            zorder=10,
            animated=True,
        )
        self.cursor_text = self.ax.text(
            self.cursor_x,
            0.98,
            self._format_cursor_label(),
            transform=self.ax.get_xaxis_transform(),
            color='#065F46',
            fontsize=10.5,
            ha='center',
            va='top',
            fontweight='bold',
            fontfamily="Microsoft YaHei",
            bbox=dict(boxstyle="round,pad=0.2", fc="#ECFDF5", ec="#10B981", lw=0.8, alpha=0.92),
            zorder=10,
            animated=True,
        )
        self._update_selection_graphics(redraw=False)
        self._update_playback_status()

        self.canvas.draw()
        self.update_cursor_graphics(prefer_blit=True)

    def _refresh_pitch_main_line_ydata(self):
        if not self.pitch_main_line or not self.current_item:
            return
        item = self.current_item
        if not item.get('pitch_data'):
            return
        freqs = np.asarray(item['pitch_data']['freqs'], dtype=float)
        if self._pitch_view_indices is not None and len(self._pitch_view_indices) > 0:
            p_vals = freqs[self._pitch_view_indices].copy()
        else:
            xs = np.asarray(item['pitch_data']['xs'])
            view_s = self.playback_domain_start if self.playback_domain_start is not None else 0.0
            view_e = self.playback_domain_end if self.playback_domain_end is not None else float('inf')
            mask = (xs >= view_s) & (xs <= view_e)
            self._pitch_view_indices = np.where(mask)[0]
            p_vals = freqs[mask].copy()
        p_vals[p_vals == 0] = np.nan
        if hasattr(self, 'session_erased_pitch_indices') and self.session_erased_pitch_indices:
            for local_i, global_i in enumerate(self._pitch_view_indices):
                if int(global_i) in self.session_erased_pitch_indices:
                    p_vals[local_i] = np.nan
        self.pitch_main_line.set_ydata(p_vals)

    def _refresh_anomaly_layer(self):
        item = self.current_item
        if not item or not self.ax2:
            return
        if self.anomaly_line is not None:
            try:
                self.anomaly_line.remove()
            except Exception:
                pass
            self.anomaly_line = None
        if self.eraser_mode:
            return
        app_params = getattr(self.app, 'last_params', {}) if self.app else {}
        analysis_mode = item.get('analysis_mode', app_params.get('analysis_mode', 'f0'))
        if analysis_mode == 'formant':
            return
        p_xs_raw, p_freqs_raw = None, None
        if item.get('pitch_data'):
            p_xs_raw = np.asarray(item['pitch_data'].get('xs'))
            p_freqs_raw = np.asarray(item['pitch_data'].get('freqs'))
        elif item.get('pitch'):
            try:
                p_xs_raw = np.asarray(item['pitch'].xs())
                p_freqs_raw = np.asarray(item['pitch'].selected_array['frequency'])
            except Exception:
                pass
        if p_xs_raw is not None and p_freqs_raw is not None and len(p_xs_raw) > 0:
            chars_bounds = item.get('chars_bounds', [])
            bounds = chars_bounds if chars_bounds else [[item['start'], item['end']]]
            anomaly_points = detect_pitch_anomaly_points(
                p_xs_raw, p_freqs_raw, bounds=bounds, start=item['start'], end=item['end']
            )
            if anomaly_points:
                jumps_x = [t for t, _ in anomaly_points]
                jumps_y = [f for _, f in anomaly_points]
                self.anomaly_line = self.ax2.plot(
                    jumps_x, jumps_y, 'o', color='#EF4444', markersize=6, zorder=6, label="异常点"
                )[0]

    def update_cursor_graphics(self, prefer_blit=False):
        if not self.cursor_line or not self.cursor_text:
            return
        self.cursor_line.set_xdata([self.cursor_x, self.cursor_x])
        self.cursor_text.set_position((self.cursor_x, 0.98))
        self.cursor_text.set_text(self._format_cursor_label())
        self._update_playback_status()
        if prefer_blit or self.is_playing:
            if self._blit_cursor():
                return
        self.canvas.draw_idle()

    def _blit_cursor(self):
        if self.background is None or not self.canvas or not self.ax:
            return False
        try:
            self.canvas.restore_region(self.background)
            self._draw_animated_eraser_layers()
            self.ax.draw_artist(self.cursor_line)
            self.ax.draw_artist(self.cursor_text)
            if self.eraser_mode and self.eraser_circle is not None and self.eraser_circle.get_visible():
                app_params = getattr(self.app, 'last_params', {}) if self.app else {}
                mode = (
                    self.current_item.get('analysis_mode', app_params.get('analysis_mode', 'f0'))
                    if self.current_item
                    else app_params.get('analysis_mode', 'f0')
                )
                target_ax = self.ax if mode == 'formant' else self.ax2
                if target_ax:
                    target_ax.draw_artist(self.eraser_circle)
            self.canvas.blit(self.fig.bbox)
            self.canvas.flush_events()
            return True
        except Exception:
            return False

    def update_lines(self):
        if not self.current_item:
            return
        item = self.current_item
        chars_bounds = item.get('chars_bounds', [])
        if not chars_bounds or len(self.bound_lines) != len(chars_bounds):
            return

        from modules.data_utils import split_into_syllables
        syls = split_into_syllables(item.get('label', ''))

        for i, (c_s, c_e) in enumerate(chars_bounds):
            self.bound_lines[i][0].set_xdata([c_s, c_s])
            self.bound_lines[i][1].set_xdata([c_e, c_e])

            if i < len(self.span_fills):
                poly = self.span_fills[i]
                if isinstance(poly, mpatches.Rectangle):
                    poly.set_x(c_s)
                    poly.set_width(c_e - c_s)
                else:
                    try:
                        xy = poly.get_xy()
                        xy[0, 0] = c_s
                        xy[1, 0] = c_s
                        xy[2, 0] = c_e
                        xy[3, 0] = c_e
                        xy[4, 0] = c_s
                        poly.set_xy(xy)
                    except Exception:
                        pass

            if i < len(self.nav_bound_lines):
                self.nav_bound_lines[i][0].set_xdata([c_s, c_s])
                self.nav_bound_lines[i][1].set_xdata([c_e, c_e])
            if i < len(self.nav_span_fills):
                n_poly = self.nav_span_fills[i]
                if isinstance(n_poly, mpatches.Rectangle):
                    n_poly.set_x(c_s)
                    n_poly.set_width(c_e - c_s)
                else:
                    try:
                        xy = n_poly.get_xy()
                        xy[0, 0] = c_s
                        xy[1, 0] = c_s
                        xy[2, 0] = c_e
                        xy[3, 0] = c_e
                        xy[4, 0] = c_s
                        n_poly.set_xy(xy)
                    except Exception:
                        pass

            if i < len(self.char_texts):
                cx = (c_s + c_e) / 2
                self.char_texts[i].set_position((cx, 0.90))
                if i < len(syls):
                    self.char_texts[i].set_text(self._format_capsule_label(syls[i], c_s, c_e))

        self._update_char_capsules_style()
        self.canvas.draw_idle()

    def on_draw(self, event):
        self.background = self.canvas.copy_from_bbox(self.fig.bbox)
        if self.cursor_line and self.cursor_text:
            self._blit_cursor()

