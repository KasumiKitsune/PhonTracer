import tkinter as tk
import time
from tkinter import messagebox
import customtkinter as ctk
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import numpy as np
import sounddevice as sd
import parselmouth
from .audio_core import SILENCE_AMPLITUDE_THRESHOLD
from .anomaly_detection import detect_pitch_anomaly_points
from .ui_widgets import CTkReleaseButton

class SpectrogramPanel:
    def __init__(self, parent, icons, on_time_changed_callback, on_auto_detect_callback, on_export_callback, app=None):
        self.parent = parent
        self.icons = icons
        self.on_time_changed = on_time_changed_callback
        self.on_auto_detect_callback = on_auto_detect_callback
        self.on_export_callback = on_export_callback
        self.app = app

        self.current_item = None
        self.current_item_iid = None
        self.dragging = None # 取值: 'start', 'end', 或 ('inner', idx)
        self.ax = None
        self.ax2 = None
        self.switch_trim_silence = None
        self.bound_lines = []
        self.span_fills = []
        self.char_texts = []
        try:
            self.font_title = ctk.CTkFont(family="Microsoft YaHei", size=15, weight="bold")
        except Exception:
            self.font_title = ("Microsoft YaHei", 15, "bold")

        # Cursor and playback state
        self.is_playing = False
        self.play_start_sys_time = 0
        self.play_start_audio_time = 0
        self.play_end_audio_time = 0
        self.cursor_x = None
        self.cursor_char_index = None
        self.cursor_line = None
        self.cursor_text = None
        self._playback_job = None
        self.playback_selection = None
        self.selection_patch = None
        self.selection_anchor_x = None
        self.selection_drag_started = False
        self.playback_loop_enabled = False
        self.playback_range_mode = "自动"
        self.playback_status_var = None
        self.playback_range_var = None
        self.playback_loop_var = None
        self.playback_mode_buttons = {}
        self.playback_domain_start = None
        self.playback_domain_end = None

        # Eraser mode state
        self.eraser_mode = False
        self.erasing = False
        self.erase_radius = 15.0  # Default pixel radius
        self.eraser_circle = None # Matplotlib patch for displaying the eraser scope
        self.background = None
        self.session_erased_pitch_indices = set()
        self.session_erased_formant_indices = {"f1": set(), "f2": set(), "f3": set()}
        self.session_erased_pitch_points = {}
        self.session_erased_formant_points = {"f1": {}, "f2": {}, "f3": {}}

        self.setup_ui()

    def setup_ui(self):
        center_frame = ctk.CTkFrame(self.parent, fg_color="white", corner_radius=10)
        center_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, pady=10)

        top_bar = ctk.CTkFrame(center_frame, fg_color="transparent")
        top_bar.pack(side=tk.TOP, fill=tk.X, padx=15, pady=(15, 5))

        frame_tune = ctk.CTkFrame(top_bar, fg_color="#F9FAFB", corner_radius=8)
        frame_tune.pack(side=tk.TOP, fill=tk.X, pady=(0, 10))
        ctk.CTkLabel(frame_tune, text="区间(s):", font=self.font_title, text_color="#111827").pack(side=tk.LEFT, padx=(10, 10), pady=10)
        ctk.CTkLabel(frame_tune, text=" 起:", image=self.icons.get("play"), compound="left").pack(side=tk.LEFT)
        self.var_t_start = ctk.StringVar(value="0.000")
        self.entry_t_start = ctk.CTkEntry(frame_tune, textvariable=self.var_t_start, width=70, corner_radius=20, height=28)
        self.entry_t_start.pack(side=tk.LEFT, padx=(5, 10))
        self.setup_entry_behavior(self.entry_t_start, 'start_manual')

        ctk.CTkLabel(frame_tune, text="止:").pack(side=tk.LEFT)
        self.var_t_end = ctk.StringVar(value="0.000")
        self.entry_t_end = ctk.CTkEntry(frame_tune, textvariable=self.var_t_end, width=70, corner_radius=20, height=28)
        self.entry_t_end.pack(side=tk.LEFT, padx=(5, 15))
        self.setup_entry_behavior(self.entry_t_end, 'end_manual')

        # 将 “应用” 按钮放置在右上角，与区间在同一行
        CTkReleaseButton(
            frame_tune,
            text="应用",
            image=self.icons.get("check"),
            compound="left",
            command=self.apply_manual_time,
            corner_radius=14,
            height=28,
            width=70,
            fg_color="#E5E7EB",
            text_color="#1F2937",
            hover_color="#D1D5DB"
        ).pack(side=tk.RIGHT, padx=(0, 10), pady=10)

        frame_actions = ctk.CTkFrame(top_bar, fg_color="transparent")
        frame_actions.pack(side=tk.TOP, fill=tk.X, pady=(5, 0))

        CTkReleaseButton(frame_actions, text="自动识别", image=self.icons.get("bulb"), compound="left", command=self.apply_auto_detect, corner_radius=20, height=36, width=110, fg_color="#FEE2E2", text_color="#DC2626", hover_color="#FCA5A5").pack(side=tk.LEFT, padx=(0, 20))

        # 橡皮擦模式按钮
        self.btn_eraser = CTkReleaseButton(
            frame_actions,
            text="橡皮擦",
            image=self.icons.get("eraser"),
            compound="left",
            command=self.toggle_eraser_mode,
            corner_radius=20,
            height=36,
            width=100,
            fg_color="#E5E7EB",
            text_color="#1F2937",
            hover_color="#D1D5DB",
            font=ctk.CTkFont(family="Microsoft YaHei", size=13, weight="bold")
        )
        self.btn_eraser.pack(side=tk.LEFT, padx=(0, 20))

        self.playback_status_var = ctk.StringVar(value="0.000 / 0.000 s")

        # 导出按钮 (使用 ctk.CTkButton，实现即时按键响应)
        ctk.CTkButton(frame_actions, text=" 导出", image=self.icons.get("save"), compound="left", command=self.on_export_callback, font=ctk.CTkFont(family="Microsoft YaHei", size=13, weight="bold"), corner_radius=20, height=36, width=60, fg_color="#10B981", hover_color="#059669").pack(side=tk.RIGHT)

        ctk.CTkLabel(
            frame_actions,
            textvariable=self.playback_status_var,
            font=ctk.CTkFont(family="Consolas", size=12),
            text_color="#334155",
            fg_color="#F8FAFC",
            corner_radius=14,
            height=30,
            width=210
        ).pack(side=tk.RIGHT, padx=(0, 24), pady=3)

        self.fig = plt.Figure(figsize=(7, 5), facecolor='white')
        self.ax = self.fig.add_subplot(111)
        self.ax2 = self.ax.twinx()
        self.canvas = FigureCanvasTkAgg(self.fig, master=center_frame)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        self.canvas.get_tk_widget().bind("<Button-2>", self._show_context_menu, add="+")
        self.canvas.get_tk_widget().bind("<Button-3>", self._show_context_menu, add="+")
        self.canvas.mpl_connect('button_press_event', self.on_press)
        self.canvas.mpl_connect('motion_notify_event', self.on_motion)
        self.canvas.mpl_connect('button_release_event', self.on_release)
        self.canvas.mpl_connect('scroll_event', self.on_scroll)
        self.canvas.mpl_connect('figure_leave_event', self.on_leave_fig)
        self.canvas.mpl_connect('draw_event', self.on_draw)

        # --- Bottom bar for Project Management ---
        bottom_bar = ctk.CTkFrame(center_frame, fg_color="#F9FAFB", corner_radius=8, height=36)
        bottom_bar.pack(side=tk.BOTTOM, fill=tk.X, padx=15, pady=(5, 5))

        CTkReleaseButton(bottom_bar, text=" 导入工程", image=self.icons.get("import"), compound="left", command=self.on_import_project_clicked, corner_radius=15, height=30, fg_color="#E5E7EB", text_color="#1F2937", hover_color="#D1D5DB").pack(side=tk.LEFT, padx=(10, 5), pady=5)
        CTkReleaseButton(bottom_bar, text=" 导出工程", image=self.icons.get("save_black"), compound="left", command=self.on_export_project_clicked, corner_radius=15, height=30, fg_color="#E5E7EB", text_color="#1F2937", hover_color="#D1D5DB").pack(side=tk.LEFT, padx=5, pady=5)

        self.switch_auto_save = ctk.CTkSwitch(bottom_bar, text="自动保存", font=ctk.CTkFont(family="Microsoft YaHei", size=12), progress_color="#10B981", command=self.on_auto_save_toggled)
        if self.app and getattr(self.app, 'project_manager', None) and self.app.project_manager.auto_save_enabled:
            self.switch_auto_save.select()
        else:
            self.switch_auto_save.deselect()
        self.switch_auto_save.pack(side=tk.RIGHT, padx=(5, 15), pady=5)

        self.setup_playback_controls(center_frame)

    def setup_playback_controls(self, center_frame):
        playback_bar = ctk.CTkFrame(center_frame, fg_color="#F8FAFC", corner_radius=18, height=44)
        playback_bar.pack(side=tk.BOTTOM, fill=tk.X, padx=15, pady=(2, 5))
        playback_bar.pack_propagate(False)

        self.playback_range_var = ctk.StringVar(value=self.playback_range_mode)
        self.playback_loop_var = ctk.BooleanVar(value=False)
        self.playback_mode_buttons = {}

        self.btn_play = ctk.CTkButton(
            playback_bar,
            text=" 播放",
            image=self.icons.get("play"),
            compound="left",
            command=self.play_selected,
            font=ctk.CTkFont(family="Microsoft YaHei", size=13, weight="bold"),
            corner_radius=16,
            height=30,
            width=76,
            fg_color="#E5E7EB",
            text_color="#1F2937",
            hover_color="#D1D5DB"
        )
        self.btn_play.pack(side=tk.LEFT, padx=(10, 4), pady=7)

        mode_frame = ctk.CTkFrame(playback_bar, fg_color="transparent")
        mode_frame.pack(side=tk.LEFT, padx=(10, 8), pady=7)
        for mode in ["自动", "选区", "当前字", "整段"]:
            btn = ctk.CTkButton(
                mode_frame,
                text=mode,
                command=lambda value=mode: self.on_playback_range_changed(value),
                corner_radius=15,
                height=30,
                width=58,
                fg_color="#E5E7EB",
                text_color="#1F2937",
                hover_color="#D1D5DB",
                font=ctk.CTkFont(family="Microsoft YaHei", size=13, weight="bold")
            )
            btn.pack(side=tk.LEFT, padx=3)
            self.playback_mode_buttons[mode] = btn
        self._update_playback_mode_buttons()

        self.switch_loop = ctk.CTkSwitch(
            playback_bar,
            text="循环",
            variable=self.playback_loop_var,
            command=self.on_loop_toggled,
            progress_color="#2563EB",
            font=ctk.CTkFont(family="Microsoft YaHei", size=12)
        )
        self.switch_loop.pack(side=tk.RIGHT, padx=(6, 10), pady=8)

    def setup_entry_behavior(self, entry, param_key):
        def on_enter(e): entry.configure(border_color="#3B82F6", border_width=2)
        def on_leave(e):
            if entry.winfo_toplevel().focus_get() != entry:
                entry.configure(border_color=["#979DA2", "#565B5E"], border_width=1)
        def on_focus_in(e):
            entry.configure(border_color="#2563EB", border_width=2)
            entry._last_val = entry.get()
        def on_focus_out(e):
            entry.configure(border_color=["#979DA2", "#565B5E"], border_width=1)
            current_val = entry.get()
            if hasattr(entry, '_last_val') and current_val == entry._last_val: return
            if param_key in ['start_manual', 'end_manual']: self.apply_manual_time()

        entry.bind("<Enter>", on_enter)
        entry.bind("<Leave>", on_leave)
        entry.bind("<FocusIn>", on_focus_in)
        entry.bind("<FocusOut>", on_focus_out)
        entry.bind("<Return>", lambda e: entry.winfo_toplevel().focus_set())

    def on_import_project_clicked(self):
        if self.app and hasattr(self.app, 'on_import_project'):
            self.app.on_import_project()

    def on_export_project_clicked(self):
        if self.app and hasattr(self.app, 'on_export_project'):
            self.app.on_export_project()

    def on_auto_save_toggled(self):
        if self.app and hasattr(self.app, 'on_auto_save_toggled'):
            self.app.on_auto_save_toggled(self.switch_auto_save.get())

    def clear_canvas(self):
        if self.is_playing:
            self._stop_playback()
        if self.eraser_mode:
            self.apply_eraser_changes()
        self.current_item = None
        self.ax.clear()
        self.ax2.clear()
        self.background = None
        self.playback_domain_start = None
        self.playback_domain_end = None
        self.bound_lines.clear()
        self.span_fills.clear()
        self.char_texts.clear()
        self._clear_playback_selection(redraw=False)
        self.canvas.draw()
        self.var_t_start.set("0.000")
        self.var_t_end.set("0.000")
        self._update_playback_status()

    def load_item(self, item):
        if self.is_playing:
            self._stop_playback()
        if self.eraser_mode:
            self.apply_eraser_changes()
            self._reset_eraser_session()
        self.current_item = item
        self.current_item_iid = None
        if self.app and hasattr(self.app, 'items'):
            for k, v in self.app.items.items():
                if v is item:
                    self.current_item_iid = k
                    break
        t_start = item.get('start')
        t_end = item.get('end')

        # Init chars_bounds
        label = item.get('label', '')
        from .data_utils import split_into_syllables
        syls = split_into_syllables(label)
        if 'chars_bounds' not in item or not item.get('chars_bounds'):
            inner_splits = item.get('inner_splits', [])
            splits = [t_start] + [s for s in inner_splits if t_start < s < t_end] + [t_end]
            if len(syls) > 1 and len(splits) != len(syls) + 1:
                import numpy as np
                splits = np.linspace(t_start, t_end, len(syls) + 1).tolist()
            elif len(syls) <= 1:
                splits = [t_start, t_end]

            chars_bounds = []
            for i in range(len(splits) - 1):
                chars_bounds.append([splits[i], splits[i+1]])
            item['chars_bounds'] = chars_bounds

        # Update overall bounds to reflect max/min of char bounds
        c_bounds = item['chars_bounds']
        if c_bounds:
            item['start'] = c_bounds[0][0]
            item['end'] = c_bounds[-1][1]
            t_start = item['start']
            t_end = item['end']

        self.var_t_start.set(f"{t_start:.3f}" if t_start is not None else "0.000")
        self.var_t_end.set(f"{t_end:.3f}" if t_end is not None else "0.000")

        self.cursor_x = t_start # Reset cursor position when loading new item
        self.cursor_char_index = 0 if item.get('chars_bounds') else None
        self.playback_domain_start = None
        self.playback_domain_end = None
        self._clear_playback_selection(redraw=False)
        self.plot_item_spectrogram()

    def _get_char_index_for_time(self, t, prefer_right=True):
        item = self.current_item
        if not item or t is None:
            return None
        chars_bounds = item.get('chars_bounds', [])
        if not chars_bounds:
            return None

        tol = 1e-6
        indices = range(len(chars_bounds) - 1, -1, -1) if prefer_right else range(len(chars_bounds))
        for idx in indices:
            c_s, c_e = chars_bounds[idx]
            if c_s - tol <= t <= c_e + tol:
                return idx

        nearest_idx = None
        nearest_dist = float('inf')
        for idx, (c_s, c_e) in enumerate(chars_bounds):
            dist = min(abs(t - c_s), abs(t - c_e))
            if dist < nearest_dist:
                nearest_idx = idx
                nearest_dist = dist
        return nearest_idx

    def _set_cursor_position(self, t, char_index=None):
        self.cursor_x = self._clamp_time_to_item(t)
        self.cursor_char_index = char_index if char_index is not None else self._get_char_index_for_time(t)
        self.update_cursor_graphics()

    def _clamp_time_to_item(self, t):
        if t is None:
            return None
        domain = self._get_playback_domain()
        if not domain:
            return t
        t_s, t_e = domain
        return max(t_s, min(t_e, t))

    def _get_playback_domain(self):
        if self.playback_domain_start is not None and self.playback_domain_end is not None:
            return (self.playback_domain_start, self.playback_domain_end)

        item = self.current_item
        if not item:
            return None
        snd = item.get('snd')
        if snd:
            try:
                return (0.0, snd.get_total_duration())
            except Exception:
                pass
        if item.get('start') is not None and item.get('end') is not None:
            return (item['start'], item['end'])
        return None

    def _has_playback_selection(self):
        return (
            self.playback_selection is not None
            and self.playback_selection[1] - self.playback_selection[0] > 0.005
        )

    def _set_playback_selection(self, start_t, end_t, redraw=True):
        start_t = self._clamp_time_to_item(start_t)
        end_t = self._clamp_time_to_item(end_t)
        if start_t is None or end_t is None:
            self._clear_playback_selection(redraw=redraw)
            return

        s, e = sorted((start_t, end_t))
        self.playback_selection = (s, e) if e - s > 0.005 else None
        self._update_selection_graphics(redraw=redraw)
        self._update_playback_status()

    def _clear_playback_selection(self, redraw=True):
        self.playback_selection = None
        self.selection_anchor_x = None
        self.selection_drag_started = False
        if self.selection_patch:
            try:
                self.selection_patch.remove()
            except Exception:
                pass
            self.selection_patch = None
        if redraw and self.canvas:
            self.canvas.draw_idle()
        self._update_playback_status()

    def _update_selection_graphics(self, redraw=True):
        if self.ax and self._has_playback_selection():
            s, e = self.playback_selection
            if self.selection_patch:
                import matplotlib.patches
                if isinstance(self.selection_patch, matplotlib.patches.Rectangle):
                    self.selection_patch.set_x(s)
                    self.selection_patch.set_width(e - s)
                else:
                    try:
                        xy = self.selection_patch.get_xy()
                        xy[0, 0] = s
                        xy[1, 0] = s
                        xy[2, 0] = e
                        xy[3, 0] = e
                        xy[4, 0] = s
                        self.selection_patch.set_xy(xy)
                    except Exception:
                        pass
            else:
                self.selection_patch = self.ax.axvspan(s, e, color="#FDE68A", alpha=0.38, zorder=4)
        else:
            if self.selection_patch:
                try:
                    self.selection_patch.remove()
                except Exception:
                    pass
                self.selection_patch = None
        if redraw and self.canvas:
            self.canvas.draw_idle()

    def _update_playback_status(self):
        if not self.playback_status_var:
            return
        item = self.current_item
        if not item:
            self.playback_status_var.set("0.000 / 0.000 s")
            return
        domain = self._get_playback_domain()
        if domain:
            start, end = domain
        else:
            start = item.get('start') or 0.0
            end = item.get('end') or start
        cursor = self.cursor_x if self.cursor_x is not None else start
        cursor_rel = max(0.0, min(end - start, cursor - start))
        if self._has_playback_selection():
            s, e = self.playback_selection
            self.playback_status_var.set(f"{cursor_rel:.3f} / {end - start:.3f} s  选区 {e - s:.3f} s")
        else:
            self.playback_status_var.set(f"{cursor_rel:.3f} / {end - start:.3f} s")

    def on_playback_range_changed(self, value):
        self.playback_range_mode = value or "自动"
        if self.playback_range_var:
            self.playback_range_var.set(self.playback_range_mode)
        self._update_playback_mode_buttons()

    def on_loop_toggled(self):
        if self.playback_loop_var:
            self.playback_loop_enabled = bool(self.playback_loop_var.get())

    def _update_playback_mode_buttons(self):
        for mode, btn in getattr(self, 'playback_mode_buttons', {}).items():
            if mode == self.playback_range_mode:
                btn.configure(fg_color="#2563EB", hover_color="#1D4ED8", text_color="#FFFFFF")
            else:
                btn.configure(fg_color="#E5E7EB", hover_color="#D1D5DB", text_color="#1F2937")

    def plot_item_spectrogram(self):
        item = self.current_item
        if not item: return
        if not item.get('snd') or item.get('start') is None: return

        self.ax.clear()
        self.ax2.clear()
        self.eraser_circle = None

        if hasattr(self, 'bound_lines'):
            self.bound_lines.clear()
        else:
            self.bound_lines = []

        if hasattr(self, 'span_fills'):
            for fill in self.span_fills:
                try: fill.remove()
                except: pass
            self.span_fills.clear()
        else:
            self.span_fills = []

        self.char_texts.clear()
        self.cursor_line = None
        self.cursor_text = None
        self.selection_patch = None

        snd = item['snd']
        t_s, t_e = item['start'], item['end']
        chars_bounds = item.get('chars_bounds', [])
        label = item.get('label', '')

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
            view_s = min(view_s, t_s)
            view_e = max(view_e, t_e)
        else:
            view_s = max(item['macro_start'] - 0.2, 0)
            view_e = min(item['macro_end'] + 0.2, snd.get_total_duration())
        self.playback_domain_start = view_s
        self.playback_domain_end = view_e

        part = snd.extract_part(from_time=view_s, to_time=view_e)

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

        spectrogram = part.to_spectrogram(window_length=0.005, maximum_frequency=spec_max_freq)
        X = spectrogram.x_grid() + view_s
        Y = spectrogram.y_grid()
        vals = np.where(spectrogram.values > 0, spectrogram.values, 1e-10)
        sg_db = 10 * np.log10(vals)

        self.ax.pcolormesh(X, Y, sg_db, vmin=sg_db.max()-50, vmax=sg_db.max(), cmap='Greys')

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

                self.ax.scatter(f_xs_filtered, f1_plot, s=18, color='#F97316', alpha=0.9, edgecolors='none', zorder=5, label="F1")
                self.ax.scatter(f_xs_filtered, f2_plot, s=18, color='#22C55E', alpha=0.9, edgecolors='none', zorder=5, label="F2")

                show_f3 = bool(item.get("show_f3", app_params.get("show_f3", False)))
                f3_arr = item['formant_data'].get('f3') if show_f3 else None

                if f3_arr is not None:
                    f3_plot = f3_arr[mask].copy()
                    self.ax.scatter(f_xs_filtered, f3_plot, s=18, color='#8B5CF6', alpha=0.9, edgecolors='none', zorder=5, label="F3")
                    self.erased_f3_layer = self.ax.scatter([], [], s=22, color='#DC2626', alpha=0.6, edgecolors='none', zorder=7, label="待剔除F3")
                else:
                    self.erased_f3_layer = None

                # 创建共振峰待剔除点图层
                self.erased_f1_layer = self.ax.scatter([], [], s=22, color='#DC2626', alpha=0.6, edgecolors='none', zorder=7, label="待剔除F1")
                self.erased_f2_layer = self.ax.scatter([], [], s=22, color='#DC2626', alpha=0.6, edgecolors='none', zorder=7, label="待剔除F2")

                if self.eraser_mode and hasattr(self, 'session_erased_formant_indices'):
                    self._remember_erased_formant_points(f_xs, f1_arr, f2_arr, f3_arr)
                    self._update_erased_formant_layers()
        else:
            self.ax2.set_visible(True)
            self.ax.set_ylim([0, 5000])
            self.ax.set_xlim([view_s, view_e])
            self.ax.set_ylabel("Frequency (Hz)")

            if item.get('pitch_data'):
                p_xs = item['pitch_data']['xs']
                p_freqs = item['pitch_data']['freqs']
                mask = (p_xs >= view_s) & (p_xs <= view_e)
                p_xs = p_xs[mask]
                p_vals = p_freqs[mask].copy()
                p_vals[p_vals == 0] = np.nan
            elif item.get('pitch'):
                global_pitch = item['pitch']
                p_xs = global_pitch.xs()
                p_freqs = global_pitch.selected_array['frequency']
                mask = (p_xs >= view_s) & (p_xs <= view_e)
                p_xs = p_xs[mask]
                p_vals = p_freqs[mask].copy()
                p_vals[p_vals == 0] = np.nan
            else:
                p_xs = np.array([])
                p_vals = np.array([])
            self.ax2.plot(p_xs, p_vals, '-o', markersize=4, linewidth=1.5, color='#3B82F6', zorder=5)

            # 创建 F0 待擦除点图层
            self.erased_pitch_layer = self.ax2.plot([], [], 'o', color='#DC2626', markersize=5, zorder=7, label="待擦除点", alpha=0.6)[0]
            if self.eraser_mode and item.get('pitch_data') and hasattr(self, 'session_erased_pitch_indices') and self.session_erased_pitch_indices:
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
                    self.ax2.plot(jumps_x, jumps_y, 'o', color='#EF4444', markersize=6, zorder=6, label="异常点")

            pitch_floor = float(item.get('pitch_floor', app_params.get('pitch_floor', 75.0)))
            pitch_ceiling = float(item.get('pitch_ceiling', app_params.get('pitch_ceiling', 600.0)))
            visible_vals = p_vals[np.isfinite(p_vals)] if len(p_vals) else np.array([])
            visible_high = float(np.max(visible_vals)) if len(visible_vals) else pitch_ceiling
            anomaly_high = max((f for _, f in anomaly_points), default=pitch_ceiling)
            y_min = max(0.0, min(50.0, pitch_floor - 25.0))
            y_max = max(500.0, pitch_ceiling, visible_high, anomaly_high) + 25.0
            self.ax2.set_ylim([y_min, y_max])
            self.ax2.set_ylabel("F0 (Hz)", color='#3B82F6')
            self.ax2.tick_params(axis='y', labelcolor='#3B82F6')
        self.ax.set_title(f"编辑区: {label}", pad=10)

        from .data_utils import split_into_syllables
        syls = split_into_syllables(label)
        for i, (c_start, c_end) in enumerate(chars_bounds):
            line_s = self.ax.axvline(c_start, color='#EF4444', linestyle='-', linewidth=2)
            line_e = self.ax.axvline(c_end, color='#EF4444', linestyle='-', linewidth=2)
            self.bound_lines.append((line_s, line_e))
            span = self.ax.axvspan(c_start, c_end, color='#BFDBFE', alpha=0.35)
            self.span_fills.append(span)

            if i < len(syls):
                cx = (c_start + c_end) / 2
                txt = self.ax.text(cx, 4800, syls[i], color='#111827', fontsize=12, ha='center', va='top', fontweight='bold', bbox=dict(boxstyle="round,pad=0.2", fc="white", ec="none", alpha=0.7))
                self.char_texts.append(txt)

        if self.cursor_x is None:
            self.cursor_x = t_s
        self.cursor_line = self.ax.axvline(
            self.cursor_x,
            color='#1B5E20',
            linestyle='--',
            linewidth=1.5,
            zorder=10,
            animated=True
        )
        self.cursor_text = self.ax.text(
            self.cursor_x,
            5000,
            f"{self.cursor_x:.3f}",
            color='#1B5E20',
            fontsize=11,
            ha='center',
            va='bottom',
            fontweight='bold',
            zorder=10,
            animated=True
        )
        self._update_selection_graphics(redraw=False)
        self._update_playback_status()

        self.fig.tight_layout()
        self.canvas.draw()
        self.update_cursor_graphics(prefer_blit=True)

    def on_press(self, event):
        if not self.ax or not self.ax2 or not self.current_item: return
        if event.inaxes not in [self.ax, self.ax2] or event.button != 1: return

        if self.is_playing:
            self._stop_playback(reset_cursor=False)
            return

        if self.eraser_mode:
            self.erasing = True
            if event.xdata is not None:
                self.erase_points_near(event)
            return

        item = self.current_item
        chars_bounds = item.get('chars_bounds', [])

        closest = None
        min_dist = 15 # px threshold
        closest_priority = 99

        for i, (c_s, c_e) in enumerate(chars_bounds):
            s_px = self.ax.transData.transform((c_s, 0))[0]
            s_dist = abs(event.x - s_px)
            # If two character boundaries overlap, prefer the start of the following
            # character so playback begins in the character the user clicked into.
            if s_dist < min_dist or (s_dist == min_dist and closest_priority > 0):
                closest = ('start', i)
                min_dist = s_dist
                closest_priority = 0

            e_px = self.ax.transData.transform((c_e, 0))[0]
            e_dist = abs(event.x - e_px)
            if e_dist < min_dist or (e_dist == min_dist and closest_priority > 1):
                closest = ('end', i)
                min_dist = e_dist
                closest_priority = 1

        if self.cursor_x is not None:
            c_px = self.ax.transData.transform((self.cursor_x, 0))[0]
            c_dist = abs(event.x - c_px)
            if c_dist < min_dist:
                closest = 'cursor'
                min_dist = c_dist
                closest_priority = 2

        self.dragging = closest
        if isinstance(closest, tuple):
            bound_type, idx = closest
            boundary_time = chars_bounds[idx][0] if bound_type == 'start' else chars_bounds[idx][1]
            cursor_idx = idx
            if bound_type == 'end' and idx + 1 < len(chars_bounds):
                next_start = chars_bounds[idx + 1][0]
                if abs(next_start - boundary_time) < 0.02:
                    cursor_idx = idx + 1
                    boundary_time = next_start
            self._set_cursor_position(boundary_time, cursor_idx)
            if bound_type == 'start':
                self.bound_lines[idx][0].set_color('#047857')
                self.bound_lines[idx][0].set_linewidth(4)
            elif bound_type == 'end':
                self.bound_lines[idx][1].set_color('#047857')
                self.bound_lines[idx][1].set_linewidth(4)
        elif closest == 'cursor':
            self.cursor_line.set_color('#064E3B')
            self.cursor_line.set_linewidth(2.5)

        if self.dragging:
            self.canvas.draw_idle()
        else:
            if event.xdata is not None:
                self._set_cursor_position(event.xdata)
                self.selection_anchor_x = self.cursor_x
                self.selection_drag_started = False
                self.dragging = 'play_select'
                self.cursor_line.set_color('#064E3B')
                self.cursor_line.set_linewidth(2.5)

    def update_cursor_graphics(self, prefer_blit=False):
        if not self.cursor_line or not self.cursor_text: return
        self.cursor_line.set_xdata([self.cursor_x, self.cursor_x])
        self.cursor_text.set_position((self.cursor_x, 5000))
        self.cursor_text.set_text(f"{self.cursor_x:.3f}")
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
            self.ax.draw_artist(self.cursor_line)
            self.ax.draw_artist(self.cursor_text)
            self.canvas.blit(self.fig.bbox)
            self.canvas.flush_events()
            return True
        except Exception:
            return False

    def on_motion(self, event):
        if not self.ax or not self.current_item: return

        if self.eraser_mode:
            self.canvas.get_tk_widget().config(cursor="crosshair")
            if event.x is not None and event.y is not None:
                self.update_eraser_circle(event)
            else:
                self.update_eraser_circle(None)

            if self.erasing and event.xdata is not None:
                self.erase_points_near(event)
            return

        item = self.current_item
        chars_bounds = item.get('chars_bounds', [])

        if not self.dragging:
            is_hovering = False
            needs_redraw = False
            for i, (c_s, c_e) in enumerate(chars_bounds):
                s_px = self.ax.transData.transform((c_s, 0))[0]
                e_px = self.ax.transData.transform((c_e, 0))[0]

                if abs(event.x - s_px) < 15:
                    if self.bound_lines[i][0].get_linewidth() != 4:
                        self.bound_lines[i][0].set_linewidth(4)
                        self.bound_lines[i][0].set_color('#B91C1C')
                        needs_redraw = True
                    is_hovering = True
                else:
                    if self.bound_lines[i][0].get_linewidth() != 2:
                        self.bound_lines[i][0].set_linewidth(2)
                        self.bound_lines[i][0].set_color('#EF4444')
                        needs_redraw = True

                if abs(event.x - e_px) < 15:
                    if self.bound_lines[i][1].get_linewidth() != 4:
                        self.bound_lines[i][1].set_linewidth(4)
                        self.bound_lines[i][1].set_color('#B91C1C')
                        needs_redraw = True
                    is_hovering = True
                else:
                    if self.bound_lines[i][1].get_linewidth() != 2:
                        self.bound_lines[i][1].set_linewidth(2)
                        self.bound_lines[i][1].set_color('#EF4444')
                        needs_redraw = True

            if self.cursor_x is not None:
                c_px = self.ax.transData.transform((self.cursor_x, 0))[0]
                if abs(event.x - c_px) < 15:
                    if self.cursor_line.get_linewidth() != 2.5:
                        self.cursor_line.set_linewidth(2.5)
                        self.cursor_line.set_color('#065F46')
                        needs_redraw = True
                    is_hovering = True
                else:
                    if self.cursor_line.get_linewidth() != 1.5:
                        self.cursor_line.set_linewidth(1.5)
                        self.cursor_line.set_color('#1B5E20')
                        needs_redraw = True

            cursor_name = "sb_h_double_arrow" if is_hovering else "arrow"
            if self.canvas.get_tk_widget().cget("cursor") != cursor_name:
                self.canvas.get_tk_widget().config(cursor=cursor_name)

            if needs_redraw:
                self.canvas.draw_idle()
            return

        if event.xdata is None:
            return

        if self.dragging == 'play_select':
            current_t = self._clamp_time_to_item(event.xdata)
            if current_t is None:
                return
            self._set_cursor_position(current_t)
            anchor = self.selection_anchor_x if self.selection_anchor_x is not None else current_t
            if abs(current_t - anchor) > 0.005:
                self.selection_drag_started = True
                self._set_playback_selection(anchor, current_t)
            return

        if isinstance(self.dragging, tuple):
            bound_type, idx = self.dragging
            if bound_type == 'start':
                chars_bounds[idx][0] = min(event.xdata, chars_bounds[idx][1] - 0.01)
                self.cursor_x = chars_bounds[idx][0]
                self.cursor_char_index = idx
            elif bound_type == 'end':
                chars_bounds[idx][1] = max(event.xdata, chars_bounds[idx][0] + 0.01)
                self.cursor_x = chars_bounds[idx][1]
                self.cursor_char_index = idx

            if chars_bounds:
                item['start'] = chars_bounds[0][0]
                item['end'] = chars_bounds[-1][1]
                item['is_manual_edited'] = True

            # Update the cursor line and text coordinates in real-time
            if self.cursor_line and self.cursor_text:
                self.cursor_line.set_xdata([self.cursor_x, self.cursor_x])
                self.cursor_text.set_position((self.cursor_x, 5000))
                self.cursor_text.set_text(f"{self.cursor_x:.3f}")
        elif self.dragging == 'cursor':
            self._set_cursor_position(event.xdata)
            return

        self.update_lines()

    def on_release(self, event):
        if self.eraser_mode:
            if self.erasing:
                self.erasing = False
                # 更新橡皮擦圆圈并轻量提交底层数组
                if event.x is not None and event.y is not None:
                    self.update_eraser_circle(event)
                self.light_apply_eraser_changes()
            return

        if self.dragging:
            was_dragging = self.dragging
            self.dragging = None
            for line_s, line_e in self.bound_lines:
                line_s.set_color('#EF4444')
                line_e.set_color('#EF4444')
                line_s.set_linewidth(2)
                line_e.set_linewidth(2)
            if self.cursor_line:
                self.cursor_line.set_color('#1B5E20')
                self.cursor_line.set_linewidth(1.5)

            if was_dragging in ('cursor', 'play_select'):
                if was_dragging == 'play_select' and not self.selection_drag_started:
                    self._clear_playback_selection(redraw=False)
                self.update_cursor_graphics()
            else:
                self.plot_item_spectrogram()
                self.update_ui_times()
            self.canvas.get_tk_widget().config(cursor="arrow")

    def update_lines(self):
        if not self.current_item: return
        item = self.current_item
        chars_bounds = item.get('chars_bounds', [])
        if not chars_bounds or len(self.bound_lines) != len(chars_bounds): return

        for i, (c_s, c_e) in enumerate(chars_bounds):
            self.bound_lines[i][0].set_xdata([c_s, c_s])
            self.bound_lines[i][1].set_xdata([c_e, c_e])

            if i < len(self.span_fills):
                poly = self.span_fills[i]
                import matplotlib.patches
                if isinstance(poly, matplotlib.patches.Rectangle):
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

            if i < len(self.char_texts):
                cx = (c_s + c_e) / 2
                self.char_texts[i].set_position((cx, 4800))

        self.canvas.draw_idle()

    def update_ui_times(self):
        item = self.current_item
        if not item: return
        if getattr(self, 'var_t_start', None) is not None:
            self.var_t_start.set(f"{item['start']:.3f}")
        if getattr(self, 'var_t_end', None) is not None:
            self.var_t_end.set(f"{item['end']:.3f}")
        if self.on_time_changed:
            self.on_time_changed(item)

    def apply_manual_time(self):
        if not self.current_item: return
        try:
            item = self.current_item
            t1, t2 = float(self.var_t_start.get()), float(self.var_t_end.get())
            old_s, old_e = item['start'], item['end']
            new_s, new_e = min(t1, t2), max(t1, t2)

            chars_bounds = item.get('chars_bounds', [])
            if chars_bounds:
                ratio = (new_e - new_s) / (old_e - old_s) if old_e > old_s else 1
                for i in range(len(chars_bounds)):
                    c_s, c_e = chars_bounds[i]
                    chars_bounds[i] = [new_s + (c_s - old_s) * ratio, new_s + (c_e - old_s) * ratio]
                item['start'] = chars_bounds[0][0]
                item['end'] = chars_bounds[-1][1]
            else:
                item['start'] = new_s
                item['end'] = new_e

            item['is_manual_edited'] = True
            panel = None
            if self.app:
                self.app.mark_modified()
                if hasattr(self.app, 'tree_panel') and self.app.tree_panel:
                    panel = self.app.tree_panel
                elif hasattr(self.app, 'project_panel') and self.app.project_panel:
                    panel = self.app.project_panel
            if panel:
                panel.update_item_icon(self.current_item_iid)

            self.plot_item_spectrogram()
            self.update_ui_times()
        except ValueError:
            messagebox.showerror("错误", "请输入有效的数字")

    def _update_play_button_state(self, playing=False):
        if hasattr(self, 'btn_play') and self.btn_play:
            if playing:
                self.btn_play.configure(text=" 停止", image=self.icons.get("pause"), width=76)
            else:
                self.btn_play.configure(text=" 播放", image=self.icons.get("play"), width=76)

    def stop_playback(self):
        self._stop_playback(reset_cursor=False)

    def _stop_playback(self, reset_cursor=False):
        if self._playback_job is not None and self.canvas:
            try:
                self.canvas.get_tk_widget().after_cancel(self._playback_job)
            except Exception:
                pass
            self._playback_job = None
        if self.is_playing:
            try:
                sd.stop()
            except Exception:
                pass
        self.is_playing = False
        if reset_cursor and self.current_item:
            self.cursor_x = self.current_item.get('start')
            self.cursor_char_index = 0 if self.current_item.get('chars_bounds') else None
            self.update_cursor_graphics()
        self._update_play_button_state(playing=False)
        self._update_playback_status()

    def _get_current_char_bounds(self):
        item = self.current_item
        if not item:
            return None
        chars_bounds = item.get('chars_bounds', [])
        if not chars_bounds:
            return None
        char_idx = self.cursor_char_index
        if char_idx is None or char_idx >= len(chars_bounds):
            char_idx = self._get_char_index_for_time(self.cursor_x)
        if char_idx is None or not (0 <= char_idx < len(chars_bounds)):
            return None
        return tuple(chars_bounds[char_idx])

    def _get_playback_range(self, mode=None):
        item = self.current_item
        if not item:
            return None
        mode = mode or self.playback_range_mode or "自动"
        domain = self._get_playback_domain()

        if mode == "选区":
            return self.playback_selection if self._has_playback_selection() else None
        if mode == "当前字":
            bounds = self._get_current_char_bounds()
            if not bounds:
                return None
            return (bounds[0], bounds[1])
        if mode == "整段":
            return domain if domain else (item['start'], item['end'])

        if self._has_playback_selection():
            return self.playback_selection

        bounds = self._get_current_char_bounds()
        if bounds and self.cursor_x is not None and bounds[0] <= self.cursor_x <= bounds[1]:
            return (self.cursor_x, bounds[1])

        if self.cursor_x is not None and item['start'] <= self.cursor_x < item['end'] - 0.01:
            return (self.cursor_x, item['end'])

        if domain:
            return domain
        return (item['start'], item['end'])

    def play_current_item(self):
        self._play_range_mode("整段")

    def play_current_char(self):
        self._play_range_mode("当前字")

    def _play_range_mode(self, mode):
        if self.is_playing:
            self._stop_playback()
            return
        play_range = self._get_playback_range(mode)
        if not play_range:
            return
        self._start_playback_range(*play_range)

    def play_selected(self):
        item = self.current_item
        if not item: return

        # 如果当前正在播放，则作为“暂停”功能：停止播放并恢复按钮状态
        if self.is_playing:
            self._stop_playback()
            return

        play_range = self._get_playback_range()
        if not play_range:
            return
        self._start_playback_range(*play_range)

    def _start_playback_range(self, play_s, play_e):
        item = self.current_item
        if not item: return

        if not item.get('snd') and item.get('path'):
            try: item['snd'] = parselmouth.Sound(item['path'])
            except Exception: return
        if not item.get('snd'): return

        snd = item['snd']

        try:
            total_duration = snd.get_total_duration()
            play_s = max(0.0, min(total_duration, play_s))
            play_e = max(0.0, min(total_duration, play_e))

            if play_e <= play_s:
                return

            self.cursor_x = play_s
            self.cursor_char_index = self._get_char_index_for_time(play_s)
            self.update_cursor_graphics()

            part = snd.extract_part(from_time=play_s, to_time=play_e)
            audio_data = np.ascontiguousarray(part.values.T, dtype=np.float32)

            sd.play(audio_data, samplerate=int(part.sampling_frequency), blocking=False, latency='low')

            self.is_playing = True
            self.play_start_sys_time = time.time()
            self.play_start_audio_time = play_s
            self.play_end_audio_time = play_e
            self.play_selection_start = play_s

            self._update_play_button_state(playing=True)
            self._playback_update_loop()

        except Exception as e:
            self.is_playing = False
            self._update_play_button_state(playing=False)
            messagebox.showerror("错误", f"播放失败: {str(e)}")

    def _playback_update_loop(self):
        if not self.is_playing:
            self._playback_job = None
            self._update_play_button_state(playing=False)
            return
        elapsed = time.time() - self.play_start_sys_time
        current_audio_time = self.play_start_audio_time + elapsed

        if current_audio_time >= self.play_end_audio_time:
            if self.playback_loop_enabled:
                self._start_playback_range(self.play_start_audio_time, self.play_end_audio_time)
                return
            self.is_playing = False
            self.cursor_x = getattr(self, 'play_selection_start', self.current_item['start'])
            self._playback_job = None
            self.update_cursor_graphics(prefer_blit=True)
            self._update_play_button_state(playing=False)
            return

        self.cursor_x = current_audio_time
        self.update_cursor_graphics(prefer_blit=True)
        self._playback_job = self.canvas.get_tk_widget().after(10, self._playback_update_loop)

    def apply_auto_detect(self):
        if self.on_auto_detect_callback:
            self.on_auto_detect_callback()

    def _reset_eraser_session(self):
        self.session_erased_pitch_indices = set()
        self.session_erased_formant_indices = {"f1": set(), "f2": set(), "f3": set()}
        self.session_erased_pitch_points = {}
        self.session_erased_formant_points = {"f1": {}, "f2": {}, "f3": {}}

    def _remember_erased_pitch_points(self, xs, freqs):
        points = getattr(self, 'session_erased_pitch_points', {})
        for idx in self.session_erased_pitch_indices:
            if idx not in points and 0 <= idx < len(xs) and 0 <= idx < len(freqs):
                points[idx] = (xs[idx], freqs[idx])
        self.session_erased_pitch_points = points

    def _remember_erased_formant_points(self, xs, f1, f2, f3=None):
        points = getattr(self, 'session_erased_formant_points', {"f1": {}, "f2": {}, "f3": {}})
        curves = [("f1", f1), ("f2", f2)]
        if f3 is not None:
            curves.append(("f3", f3))
        for curve, values in curves:
            curve_points = points.setdefault(curve, {})
            for idx in self.session_erased_formant_indices.get(curve, set()):
                if idx not in curve_points and 0 <= idx < len(xs) and 0 <= idx < len(values):
                    curve_points[idx] = (xs[idx], values[idx])
        self.session_erased_formant_points = points

    def _update_erased_pitch_layer(self):
        if not hasattr(self, 'erased_pitch_layer') or not self.erased_pitch_layer:
            return
        points = getattr(self, 'session_erased_pitch_points', {})
        ordered_points = [points[idx] for idx in sorted(points)]
        self.erased_pitch_layer.set_data(
            [point[0] for point in ordered_points],
            [point[1] for point in ordered_points]
        )

    def _update_erased_formant_layers(self):
        points = getattr(self, 'session_erased_formant_points', {"f1": {}, "f2": {}, "f3": {}})
        for curve, layer_name in (("f1", "erased_f1_layer"), ("f2", "erased_f2_layer"), ("f3", "erased_f3_layer")):
            layer = getattr(self, layer_name, None)
            if not layer:
                continue
            ordered_points = [points.get(curve, {})[idx] for idx in sorted(points.get(curve, {}))]
            offsets = np.array(ordered_points) if ordered_points else np.empty((0, 2))
            layer.set_offsets(offsets)

    def light_apply_eraser_changes(self):
        if not self.current_item:
            return

        applied_pitch = False
        applied_formant = False
        item = self.current_item

        # 提交共振峰剔除，同时保留原始坐标供红色图层持续显示
        if hasattr(self, 'session_erased_formant_indices') and (
            self.session_erased_formant_indices.get("f1") or self.session_erased_formant_indices.get("f2") or self.session_erased_formant_indices.get("f3")
        ):
            if item.get('formant_data'):
                f1 = item['formant_data']['f1']
                f2 = item['formant_data']['f2']
                f3 = item['formant_data'].get('f3')
                self._remember_erased_formant_points(item['formant_data']['xs'], f1, f2, f3)

                for curve in ("f1", "f2", "f3"):
                    arr = item["formant_data"].get(curve)
                    if arr is None:
                        continue
                    for idx in self.session_erased_formant_indices.get(curve, set()):
                        if 0 <= idx < len(arr) and not np.isnan(arr[idx]):
                            arr[idx] = np.nan
                            applied_formant = True

        # 提交 F0 擦除，同时保留原始坐标供红色图层持续显示
        if hasattr(self, 'session_erased_pitch_indices') and self.session_erased_pitch_indices:
            if not item.get('pitch_data') and item.get('pitch'):
                pitch = item['pitch']
                item['pitch_data'] = {
                    'xs': pitch.xs(),
                    'freqs': pitch.selected_array['frequency'].copy()
                }
                if 'pitch' in item:
                    del item['pitch']

            if item.get('pitch_data'):
                self._remember_erased_pitch_points(item['pitch_data']['xs'], item['pitch_data']['freqs'])
                freqs = item['pitch_data']['freqs']
                for idx in self.session_erased_pitch_indices:
                    if 0 <= idx < len(freqs) and freqs[idx] != 0.0:
                        freqs[idx] = 0.0
                        applied_pitch = True

        if applied_pitch or applied_formant:
            item['is_manual_edited'] = True
            # 清理预览和空值标记
            if 'preview_f0' in item:
                item.pop('preview_f0')
            if 'preview_formants' in item:
                item.pop('preview_formants')
            if 'has_empty_data' in item:
                item.pop('has_empty_data')

            if self.app:
                self.app.mark_modified()

                # 必要时重新生成共振峰预览
                app_params = getattr(self.app, 'last_params', {}) if self.app else {}
                if applied_formant:
                    pts = int(app_params.get('pts', 11))
                    strategy = app_params.get('formant_sample_strategy', '整段11点')
                    show_f3 = bool(item.get("show_f3", app_params.get("show_f3", False)))
                    try:
                        if show_f3:
                            res = self.app.sample_formant_points_with_f3(item, pts, strategy)
                            if isinstance(res, (tuple, list)) and len(res) == 4:
                                _, preview_f1, preview_f2, preview_f3 = res
                                item['preview_formants'] = {"f1": preview_f1, "f2": preview_f2, "f3": preview_f3}
                        else:
                            res = self.app.sample_formant_points(item, pts, strategy)
                            if isinstance(res, (tuple, list)) and len(res) == 3:
                                _, preview_f1, preview_f2 = res
                                item['preview_formants'] = {"f1": preview_f1, "f2": preview_f2}
                    except Exception:
                        pass

                panel = None
                if hasattr(self.app, 'tree_panel') and self.app.tree_panel:
                    panel = self.app.tree_panel
                elif hasattr(self.app, 'project_panel') and self.app.project_panel:
                    panel = self.app.project_panel
                if panel:
                    panel.update_item_icon(self.current_item_iid)
                    if hasattr(panel, 'update_preview'):
                        panel.update_preview()

    def apply_eraser_changes(self):
        self.light_apply_eraser_changes()
        self._reset_eraser_session()
        self.plot_item_spectrogram()

    def discard_eraser_changes(self):
        self._reset_eraser_session()

        # 清空待擦除点图层
        if hasattr(self, 'erased_pitch_layer') and self.erased_pitch_layer:
            self.erased_pitch_layer.set_data([], [])
        if hasattr(self, 'erased_f1_layer') and self.erased_f1_layer:
            self.erased_f1_layer.set_offsets(np.empty((0, 2)))
        if hasattr(self, 'erased_f2_layer') and self.erased_f2_layer:
            self.erased_f2_layer.set_offsets(np.empty((0, 2)))
        if hasattr(self, 'erased_f3_layer') and self.erased_f3_layer:
            self.erased_f3_layer.set_offsets(np.empty((0, 2)))

        self.canvas.draw_idle()

    def toggle_eraser_mode(self):
        self.eraser_mode = not self.eraser_mode
        if self.eraser_mode:
            # 初始化会话状态
            self._reset_eraser_session()
            self.btn_eraser.configure(
                fg_color="#FEE2E2",
                text_color="#DC2626",
                hover_color="#FCA5A5"
            )
            self.canvas.get_tk_widget().config(cursor="crosshair")
            # 重绘一次，隐藏异常点并初始化干净状态
            self.plot_item_spectrogram()
        else:
            # 退出橡皮擦模式时完成提交和重绘
            self.apply_eraser_changes()
            self.btn_eraser.configure(
                fg_color="#E5E7EB",
                text_color="#1F2937",
                hover_color="#D1D5DB"
            )
            self.canvas.get_tk_widget().config(cursor="arrow")
            # 清理橡皮擦圆圈
            self.update_eraser_circle(None)

    def erase_points_near(self, event):
        item = self.current_item
        if not item: return

        app_params = getattr(self.app, 'last_params', {}) if self.app else {}
        mode = item.get('analysis_mode', app_params.get('analysis_mode', 'f0'))

        if mode == 'formant':
            if event.inaxes != self.ax: return
            if not item.get('formant_data'): return

            xs = item['formant_data']['xs']
            f1 = item['formant_data']['f1']
            f2 = item['formant_data']['f2']
            if len(xs) == 0: return

            show_f3 = bool(item.get("show_f3", app_params.get("show_f3", False)))
            f3 = item['formant_data'].get('f3')

            candidate_curves = [("f1", f1), ("f2", f2)]
            if show_f3 and f3 is not None:
                candidate_curves.append(("f3", f3))

            best_curve = None
            best_idx = -1
            best_dist = np.inf

            for curve, values in candidate_curves:
                temp = values.copy()
                for idx in self.session_erased_formant_indices.get(curve, set()):
                    if 0 <= idx < len(temp):
                        temp[idx] = np.nan

                clean = temp.copy()
                clean[np.isnan(clean)] = 0.0
                pixels = self.ax.transData.transform(np.column_stack((xs, clean)))
                dists = np.hypot(pixels[:, 0] - event.x, pixels[:, 1] - event.y)
                dists[np.isnan(temp)] = np.inf

                idx = int(np.argmin(dists)) if len(dists) > 0 else -1
                if idx >= 0 and dists[idx] < best_dist:
                    best_curve = curve
                    best_idx = idx
                    best_dist = dists[idx]

            if best_idx >= 0 and best_dist <= self.erase_radius:
                self.session_erased_formant_indices[best_curve].add(best_idx)
                self._remember_erased_formant_points(xs, f1, f2, f3 if show_f3 else None)
                self._update_erased_formant_layers()
                self.canvas.draw_idle()
        else:
            if event.inaxes != self.ax2: return

            if not item.get('pitch_data') and item.get('pitch'):
                pitch = item['pitch']
                item['pitch_data'] = {
                    'xs': pitch.xs(),
                    'freqs': pitch.selected_array['frequency'].copy()
                }
                if 'pitch' in item:
                    del item['pitch']

            if not item.get('pitch_data'): return

            xs = item['pitch_data']['xs']
            freqs = item['pitch_data']['freqs']
            if len(xs) == 0: return

            pts_data = np.column_stack((xs, freqs))
            pts_pixels = self.ax2.transData.transform(pts_data)

            dists = np.hypot(pts_pixels[:, 0] - event.x, pts_pixels[:, 1] - event.y)

            erase_radius = self.erase_radius
            mask_to_erase = (dists <= erase_radius) & (freqs > 0)

            if np.any(mask_to_erase):
                erased_indices = np.where(mask_to_erase)[0]
                self.session_erased_pitch_indices.update(erased_indices)

                # 实时更新待擦除点图层
                self._remember_erased_pitch_points(xs, freqs)
                if hasattr(self, 'erased_pitch_layer') and self.erased_pitch_layer:
                    self._update_erased_pitch_layer()
                    self.canvas.draw_idle()

    def on_draw(self, event):
        self.background = self.canvas.copy_from_bbox(self.fig.bbox)
        if self.cursor_line and self.cursor_text:
            self._blit_cursor()

    def update_eraser_circle(self, event=None):
        app_params = getattr(self.app, 'last_params', {}) if self.app else {}
        mode = 'f0'
        if self.current_item is not None:
            mode = self.current_item.get('analysis_mode', app_params.get('analysis_mode', 'f0'))
        else:
            mode = app_params.get('analysis_mode', 'f0')
        target_ax = self.ax if mode == 'formant' else self.ax2

        if not self.eraser_mode or not target_ax:
            if self.eraser_circle:
                try:
                    self.eraser_circle.remove()
                except Exception:
                    pass
                self.eraser_circle = None
                if self.background is not None:
                    try:
                        self.canvas.restore_region(self.background)
                        self.canvas.blit(self.fig.bbox)
                    except Exception:
                        self.canvas.draw_idle()
            return

        if event is None or event.x is None or event.y is None or event.inaxes != target_ax:
            if self.eraser_circle:
                self.eraser_circle.set_visible(False)
                if self.background is not None:
                    try:
                        self.canvas.restore_region(self.background)
                        self.canvas.blit(self.fig.bbox)
                    except Exception:
                        self.canvas.draw_idle()
            return

        if self.eraser_circle is None or self.eraser_circle not in target_ax.patches:
            from matplotlib.patches import Circle
            self.eraser_circle = Circle(
                (event.x, event.y),
                radius=self.erase_radius,
                fill=True,
                facecolor='#FEE2E2',
                edgecolor='#EF4444',
                alpha=0.4,
                linewidth=1.5,
                transform=None,
                zorder=100,
                animated=True
            )
            target_ax.add_patch(self.eraser_circle)
        else:
            self.eraser_circle.set_center((event.x, event.y))
            self.eraser_circle.set_radius(self.erase_radius)
            self.eraser_circle.set_visible(True)

        if self.background is not None:
            try:
                self.canvas.restore_region(self.background)
                target_ax.draw_artist(self.eraser_circle)
                self.canvas.blit(self.fig.bbox)
            except Exception:
                self.canvas.draw_idle()
        else:
            self.canvas.draw_idle()

    def on_scroll(self, event):
        if not self.eraser_mode: return
        if event.step is None: return

        delta = 2.0 * event.step
        self.erase_radius = max(3.0, min(100.0, self.erase_radius + delta))

        if event.x is not None and event.y is not None and event.inaxes is not None:
            self.update_eraser_circle(event)

    def on_leave_fig(self, event):
        if self.eraser_mode:
            self.update_eraser_circle(None)

    def update_eraser_button_text(self):
        if not hasattr(self, 'btn_eraser') or not self.btn_eraser:
            return
        app_params = getattr(self.app, 'last_params', {}) if self.app else {}
        if self.current_item is not None:
            mode = self.current_item.get('analysis_mode', app_params.get('analysis_mode', 'f0'))
        else:
            mode = app_params.get('analysis_mode', 'f0')
        if mode == 'formant':
            self.btn_eraser.configure(text=" 剔除点")
        else:
            self.btn_eraser.configure(text=" 橡皮擦")

    def _show_context_menu(self, event):
        if self.dragging is not None or getattr(self, 'erasing', False):
            return

        from .ui_widgets import make_context_menu, post_context_menu

        # 这里的 event 是 tkinter Event 实例，可以使用 event.x_root, event.y_root
        menu = make_context_menu(self.canvas.get_tk_widget(), font_size=15)

        if not self.current_item:
            menu.add_command(label="暂无可操作条目", state="disabled")
        else:
            play_label = "停止播放" if self.is_playing else "播放 / 停止播放"
            menu.add_command(label=play_label, command=self.play_selected)
            menu.add_command(label="播放当前字", command=self.play_current_char)
            menu.add_command(label="播放整段", command=self.play_current_item)

            clear_state = "normal" if self.playback_selection is not None else "disabled"
            menu.add_command(label="清除播放选区", command=self._clear_playback_selection, state=clear_state)

            menu.add_separator()

            eraser_label = "退出橡皮擦并应用" if self.eraser_mode else "开启橡皮擦"
            menu.add_command(label=eraser_label, command=self.toggle_eraser_mode)
            menu.add_command(label="应用当前时间", command=self.apply_manual_time)
            menu.add_command(label="自动识别", command=self.apply_auto_detect)

            menu.add_separator()

            # 导出当前数据
            menu.add_command(label="导出当前数据...", command=self.on_export_callback)
            menu.add_command(label="导入工程...", command=self.on_import_project_clicked)
            menu.add_command(label="导出工程...", command=self.on_export_project_clicked)

        post_context_menu(menu, event)
