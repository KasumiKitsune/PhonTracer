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
        
        # Eraser mode state
        self.eraser_mode = False
        self.erasing = False
        self.erase_radius = 15.0  # Default pixel radius
        self.eraser_circle = None # Matplotlib patch for displaying the eraser scope
        self.background = None

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
        
        # 导出按钮 (使用 ctk.CTkButton，实现即时按键响应)
        ctk.CTkButton(frame_actions, text=" 导出", image=self.icons.get("save"), compound="left", command=self.on_export_callback, font=ctk.CTkFont(family="Microsoft YaHei", size=13, weight="bold"), corner_radius=20, height=36, width=60, fg_color="#10B981", hover_color="#059669").pack(side=tk.RIGHT)
        
        # 播放/暂停按钮 (使用 ctk.CTkButton，实现即时按键响应)
        self.btn_play = ctk.CTkButton(frame_actions, text=" 播放", image=self.icons.get("play"), compound="left", command=self.play_selected, font=ctk.CTkFont(family="Microsoft YaHei", size=13, weight="bold"), corner_radius=20, height=36, width=60, fg_color="#E5E7EB", text_color="#1F2937", hover_color="#D1D5DB")
        self.btn_play.pack(side=tk.RIGHT, padx=(0, 20))

        self.fig = plt.Figure(figsize=(7, 5), facecolor='white') 
        self.ax = self.fig.add_subplot(111)
        self.ax2 = self.ax.twinx()
        self.canvas = FigureCanvasTkAgg(self.fig, master=center_frame)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
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
        self.switch_auto_save.pack(side=tk.RIGHT, padx=(5, 15), pady=5)

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
            self.is_playing = False
            try:
                import sounddevice as sd
                sd.stop()
            except Exception:
                pass
            self._update_play_button_state(playing=False)
        self.current_item = None
        self.ax.clear()
        self.ax2.clear()
        self.bound_lines.clear()
        self.span_fills.clear()
        self.char_texts.clear()
        self.canvas.draw()
        self.var_t_start.set("0.000")
        self.var_t_end.set("0.000")

    def load_item(self, item):
        if self.is_playing:
            self.is_playing = False
            try:
                import sounddevice as sd
                sd.stop()
            except Exception:
                pass
            self._update_play_button_state(playing=False)
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
        self.cursor_x = t
        self.cursor_char_index = char_index if char_index is not None else self._get_char_index_for_time(t)
        self.update_cursor_graphics()

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
        
        part = snd.extract_part(from_time=view_s, to_time=view_e)
        spectrogram = part.to_spectrogram(window_length=0.005, maximum_frequency=5000)
        X = spectrogram.x_grid() + view_s 
        Y = spectrogram.y_grid()
        vals = np.where(spectrogram.values > 0, spectrogram.values, 1e-10)
        sg_db = 10 * np.log10(vals)
        
        self.ax.pcolormesh(X, Y, sg_db, vmin=sg_db.max()-50, vmax=sg_db.max(), cmap='Greys')
        
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
        
        self.ax.set_ylim([0, 5000])
        self.ax.set_xlim([view_s, view_e])
        self.ax.set_ylabel("Frequency (Hz)")
        self.ax2.set_ylim([50, 500])
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
        self.cursor_line = self.ax.axvline(self.cursor_x, color='#1B5E20', linestyle='--', linewidth=1.5, zorder=10)
        self.cursor_text = self.ax.text(self.cursor_x, 5000, f"{self.cursor_x:.3f}", color='#1B5E20', fontsize=11, ha='center', va='bottom', fontweight='bold', zorder=10)

        self.fig.tight_layout()
        self.canvas.draw()

    def on_press(self, event):
        if not self.ax or not self.ax2 or not self.current_item: return
        if event.inaxes not in [self.ax, self.ax2] or event.button != 1: return

        if self.is_playing:
            try:
                import sounddevice as sd
                sd.stop()
            except Exception:
                pass
            self.is_playing = False
            self._update_play_button_state(playing=False)
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
                self.dragging = 'cursor'
                self.cursor_line.set_color('#064E3B')
                self.cursor_line.set_linewidth(2.5)

    def update_cursor_graphics(self):
        if not self.cursor_line or not self.cursor_text: return
        self.cursor_line.set_xdata([self.cursor_x, self.cursor_x])
        self.cursor_text.set_position((self.cursor_x, 5000))
        self.cursor_text.set_text(f"{self.cursor_x:.3f}")
        self.canvas.draw_idle()

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
            for i, (c_s, c_e) in enumerate(chars_bounds):
                s_px = self.ax.transData.transform((c_s, 0))[0]
                e_px = self.ax.transData.transform((c_e, 0))[0]
                
                if abs(event.x - s_px) < 15:
                    self.bound_lines[i][0].set_linewidth(4); self.bound_lines[i][0].set_color('#B91C1C')
                    is_hovering = True
                else:
                    self.bound_lines[i][0].set_linewidth(2); self.bound_lines[i][0].set_color('#EF4444')

                if abs(event.x - e_px) < 15:
                    self.bound_lines[i][1].set_linewidth(4); self.bound_lines[i][1].set_color('#B91C1C')
                    is_hovering = True
                else:
                    self.bound_lines[i][1].set_linewidth(2); self.bound_lines[i][1].set_color('#EF4444')
                
            if self.cursor_x is not None:
                c_px = self.ax.transData.transform((self.cursor_x, 0))[0]
                if abs(event.x - c_px) < 15:
                    self.cursor_line.set_linewidth(2.5); self.cursor_line.set_color('#065F46')
                    is_hovering = True
                else:
                    self.cursor_line.set_linewidth(1.5); self.cursor_line.set_color('#1B5E20')

            self.canvas.get_tk_widget().config(cursor="sb_h_double_arrow" if is_hovering else "arrow")
            self.canvas.draw_idle()
            return
            
        if event.xdata is None:
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
                item = self.current_item
                if item:
                    if 'preview_f0' in item:
                        item.pop('preview_f0')
                    if 'has_empty_data' in item:
                        item.pop('has_empty_data')
                self.update_ui_times()
                self.plot_item_spectrogram()
                # Re-draw the eraser circle since plot_item_spectrogram cleared the axes
                if event.x is not None and event.y is not None:
                    self.update_eraser_circle(event)
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

            if was_dragging == 'cursor':
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
        
        for fill in self.span_fills:
            try: fill.remove()
            except: pass
        self.span_fills.clear()
        
        for i, (c_s, c_e) in enumerate(chars_bounds):
            self.bound_lines[i][0].set_xdata([c_s, c_s])
            self.bound_lines[i][1].set_xdata([c_e, c_e])
            span = self.ax.axvspan(c_s, c_e, color='#BFDBFE', alpha=0.35)
            self.span_fills.append(span)

            if i < len(self.char_texts):
                cx = (c_s + c_e) / 2
                self.char_texts[i].set_position((cx, 4800))
                
        self.canvas.draw_idle()

    def update_ui_times(self):
        item = self.current_item
        if not item: return
        self.var_t_start.set(f"{item['start']:.3f}")
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
                if hasattr(self.app, 'project_manager'):
                    self.app.project_manager.trigger_auto_save()
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
                self.btn_play.configure(text=" 暂停", image=self.icons.get("pause"))
            else:
                self.btn_play.configure(text=" 播放", image=self.icons.get("play"))

    def play_selected(self):
        item = self.current_item
        if not item: return

        # 如果当前正在播放，则作为“暂停”功能：停止播放并恢复按钮状态
        if self.is_playing:
            self.is_playing = False
            try:
                sd.stop()
            except Exception:
                pass
            self._update_play_button_state(playing=False)
            return

        if not item.get('snd') and item.get('path'):
            try: item['snd'] = parselmouth.Sound(item['path'])
            except Exception: return
        if not item.get('snd'): return

        snd = item['snd']

        try:
            total_duration = snd.get_total_duration()
            if self.cursor_x is None:
                self.cursor_x = item['start']

            chars_bounds = item.get('chars_bounds', [])
            play_s = None
            play_e = None
            self.play_is_selection = False
            self.play_selection_start = 0.0

            char_idx = self.cursor_char_index
            if char_idx is None or char_idx >= len(chars_bounds):
                char_idx = self._get_char_index_for_time(self.cursor_x)

            if char_idx is not None and 0 <= char_idx < len(chars_bounds):
                c_s, c_e = chars_bounds[char_idx]
                if c_s <= self.cursor_x <= c_e:
                    play_s = self.cursor_x
                    play_e = c_e
                    self.play_is_selection = True
                    self.play_selection_start = c_s

            if play_s is None:
                # If cursor is within the current segment, play from the cursor to the end of the segment.
                # Otherwise, play the entire current segment from its start.
                if item['start'] <= self.cursor_x < item['end'] - 0.01:
                    play_s = self.cursor_x
                else:
                    play_s = item['start']
                    self.cursor_x = item['start']
                    self.update_cursor_graphics()
                play_e = item['end']
                self.play_is_selection = True
                self.play_selection_start = play_s

            if play_e <= play_s:
                return

            part = snd.extract_part(from_time=play_s, to_time=play_e)
            audio_data = np.ascontiguousarray(part.values.T, dtype=np.float32)

            sd.play(audio_data, samplerate=int(part.sampling_frequency))

            self.is_playing = True
            self.play_start_sys_time = time.time()
            self.play_start_audio_time = play_s
            self.play_end_audio_time = play_e

            self._update_play_button_state(playing=True)
            self._playback_update_loop()

        except Exception as e:
            self.is_playing = False
            self._update_play_button_state(playing=False)
            messagebox.showerror("错误", f"播放失败: {str(e)}")

    def _playback_update_loop(self):
        if not self.is_playing:
            self._update_play_button_state(playing=False)
            return
        elapsed = time.time() - self.play_start_sys_time
        current_audio_time = self.play_start_audio_time + elapsed

        if current_audio_time >= self.play_end_audio_time:
            self.is_playing = False
            if getattr(self, 'play_is_selection', False):
                self.cursor_x = getattr(self, 'play_selection_start', self.current_item['start'])
            else:
                self.cursor_x = self.play_end_audio_time
            self.update_cursor_graphics()
            self._update_play_button_state(playing=False)
            return

        self.cursor_x = current_audio_time
        self.update_cursor_graphics()
        self.canvas.get_tk_widget().after(16, self._playback_update_loop)

    def apply_auto_detect(self):
        if self.on_auto_detect_callback:
            self.on_auto_detect_callback()

    def toggle_eraser_mode(self):
        self.eraser_mode = not self.eraser_mode
        if self.eraser_mode:
            self.btn_eraser.configure(
                fg_color="#FEE2E2", 
                text_color="#DC2626", 
                hover_color="#FCA5A5"
            )
            self.canvas.get_tk_widget().config(cursor="crosshair")
        else:
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
        
        # Limit eraser interactions specifically to the pitch axis (ax2) to prevent accidental
        # erasure when the mouse cursor is moved or dragged in the spectrogram axis (ax) or outside
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
        
        # Transform data to screen coordinates
        pts_data = np.column_stack((xs, freqs))
        pts_pixels = self.ax2.transData.transform(pts_data)
        
        # Calculate Euclidean distances in pixels
        dists = np.hypot(pts_pixels[:, 0] - event.x, pts_pixels[:, 1] - event.y)
        
        erase_radius = self.erase_radius  # pixels
        mask_to_erase = (dists <= erase_radius) & (freqs > 0)
        
        if np.any(mask_to_erase):
            freqs[mask_to_erase] = 0.0
            item['is_manual_edited'] = True
            panel = None
            if self.app:
                if hasattr(self.app, 'tree_panel') and self.app.tree_panel:
                    panel = self.app.tree_panel
                elif hasattr(self.app, 'project_panel') and self.app.project_panel:
                    panel = self.app.project_panel
            if panel:
                panel.update_item_icon(self.current_item_iid)
            
            # Live visual update: update F0 line data in real time
            if self.ax2.lines:
                f0_line = self.ax2.lines[0]
                view_s, view_e = self.ax.get_xlim()
                mask = (xs >= view_s) & (xs <= view_e)
                p_vals = freqs[mask].copy()
                p_vals[p_vals == 0] = np.nan
                
                # Verify that masked shape matches plotted F0 line y-data length
                if len(p_vals) == len(f0_line.get_ydata()):
                    f0_line.set_ydata(p_vals)
                    self.canvas.draw_idle()
                else:
                    self.plot_item_spectrogram()

    def on_draw(self, event):
        self.background = self.canvas.copy_from_bbox(self.fig.bbox)

    def update_eraser_circle(self, event=None):
        if not self.eraser_mode or not self.ax2:
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

        if event is None or event.x is None or event.y is None or event.inaxes != self.ax2:
            if self.eraser_circle:
                self.eraser_circle.set_visible(False)
                if self.background is not None:
                    try:
                        self.canvas.restore_region(self.background)
                        self.canvas.blit(self.fig.bbox)
                    except Exception:
                        self.canvas.draw_idle()
            return

        # If circle doesn't exist or was cleared, create a new one
        if self.eraser_circle is None or self.eraser_circle not in self.ax2.patches:
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
            self.ax2.add_patch(self.eraser_circle)
        else:
            self.eraser_circle.set_center((event.x, event.y))
            self.eraser_circle.set_radius(self.erase_radius)
            self.eraser_circle.set_visible(True)

        if self.background is not None:
            try:
                self.canvas.restore_region(self.background)
                self.ax2.draw_artist(self.eraser_circle)
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
