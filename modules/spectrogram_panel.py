import tkinter as tk
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
    def __init__(self, parent, icons, on_time_changed_callback, on_auto_detect_callback, on_export_callback):
        self.parent = parent
        self.icons = icons
        self.on_time_changed = on_time_changed_callback
        self.on_auto_detect_callback = on_auto_detect_callback
        self.on_export_callback = on_export_callback
        
        self.current_item = None
        self.dragging = None # 取值: 'start', 'end', 或 ('inner', idx)
        self.ax = None
        self.ax2 = None
        self.switch_trim_silence = None
        self.bound_lines = []
        self.span_fills = []
        self.char_texts = []
        self.font_title = ctk.CTkFont(family="Microsoft YaHei", size=15, weight="bold")
        
        # Cursor and playback state
        self.is_playing = False
        self.play_start_sys_time = 0
        self.play_start_audio_time = 0
        self.play_end_audio_time = 0
        self.cursor_x = None
        self.cursor_line = None
        self.cursor_text = None
        self._playback_job = None

        self.setup_ui()
        
    def setup_ui(self):
        center_frame = ctk.CTkFrame(self.parent, fg_color="white", corner_radius=10)
        center_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, pady=10)
        
        top_bar = ctk.CTkFrame(center_frame, fg_color="transparent")
        top_bar.pack(side=tk.TOP, fill=tk.X, padx=15, pady=(15, 5))
        
        frame_tune = ctk.CTkFrame(top_bar, fg_color="#F9FAFB", corner_radius=8)
        frame_tune.pack(side=tk.TOP, fill=tk.X, pady=(0, 10))
        ctk.CTkLabel(frame_tune, text="宏观区间(s):", font=self.font_title, text_color="#111827").pack(side=tk.LEFT, padx=(10, 10), pady=10)
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
        
        frame_actions = ctk.CTkFrame(top_bar, fg_color="transparent")
        frame_actions.pack(side=tk.TOP, fill=tk.X, pady=(5, 0))
        
        CTkReleaseButton(frame_actions, text="应用", image=self.icons.get("check"), compound="left", command=self.apply_manual_time, corner_radius=20, height=36, width=110, fg_color="#E5E7EB", text_color="#1F2937", hover_color="#D1D5DB").pack(side=tk.LEFT, padx=(0, 10))
        CTkReleaseButton(frame_actions, text="自动识别", image=self.icons.get("bulb"), compound="left", command=self.apply_auto_detect, corner_radius=20, height=36, width=110, fg_color="#FCE7F3", text_color="#BE185D", hover_color="#FBCFE8").pack(side=tk.LEFT, padx=(0, 20))
        
        CTkReleaseButton(frame_actions, text=" 试听", image=self.icons.get("play"), compound="left", command=self.play_selected, font=ctk.CTkFont(family="Microsoft YaHei", size=13, weight="bold"), corner_radius=20, height=36, width=60, fg_color="#E5E7EB", text_color="#1F2937", hover_color="#D1D5DB").pack(side=tk.LEFT, padx=(0, 10))
        CTkReleaseButton(frame_actions, text=" 导出", image=self.icons.get("save"), compound="left", command=self.on_export_callback, font=ctk.CTkFont(family="Microsoft YaHei", size=13, weight="bold"), corner_radius=20, height=36, width=60, fg_color="#10B981", hover_color="#059669").pack(side=tk.LEFT)

        self.fig = plt.Figure(figsize=(7, 5), facecolor='white') 
        self.ax = self.fig.add_subplot(111)
        self.ax2 = self.ax.twinx()
        self.canvas = FigureCanvasTkAgg(self.fig, master=center_frame)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        self.canvas.mpl_connect('button_press_event', self.on_press)
        self.canvas.mpl_connect('motion_notify_event', self.on_motion)
        self.canvas.mpl_connect('button_release_event', self.on_release)

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

    def clear_canvas(self):
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
        self.current_item = item
        t_start = item.get('start')
        t_end = item.get('end')
        
        # Init chars_bounds
        label = item.get('label', '')
        if 'chars_bounds' not in item or not item.get('chars_bounds'):
            inner_splits = item.get('inner_splits', [])
            splits = [t_start] + [s for s in inner_splits if t_start < s < t_end] + [t_end]
            if len(label) > 1 and len(splits) != len(label) + 1:
                import numpy as np
                splits = np.linspace(t_start, t_end, len(label) + 1).tolist()
            elif len(label) <= 1:
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
        self.plot_item_spectrogram()

    def plot_item_spectrogram(self):
        item = self.current_item
        if not item: return
        if not item.get('snd') or item.get('start') is None: return
        
        self.ax.clear()
        self.ax2.clear()

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
        
        global_pitch = item.get('pitch')
        if global_pitch:
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
        
        for i, (c_start, c_end) in enumerate(chars_bounds):
            line_s = self.ax.axvline(c_start, color='#EF4444', linestyle='-', linewidth=2)
            line_e = self.ax.axvline(c_end, color='#EF4444', linestyle='-', linewidth=2)
            self.bound_lines.append((line_s, line_e))
            span = self.ax.axvspan(c_start, c_end, color='#BFDBFE', alpha=0.35)
            self.span_fills.append(span)
            
            if i < len(label):
                cx = (c_start + c_end) / 2
                txt = self.ax.text(cx, 4800, label[i], color='#111827', fontsize=12, ha='center', va='top', fontweight='bold', bbox=dict(boxstyle="round,pad=0.2", fc="white", ec="none", alpha=0.7))
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
            return

        item = self.current_item
        chars_bounds = item.get('chars_bounds', [])
        
        closest = None
        min_dist = 15 # px threshold
        
        for i, (c_s, c_e) in enumerate(chars_bounds):
            s_px = self.ax.transData.transform((c_s, 0))[0]
            if abs(event.x - s_px) < min_dist:
                closest = ('start', i)
                min_dist = abs(event.x - s_px)

            e_px = self.ax.transData.transform((c_e, 0))[0]
            if abs(event.x - e_px) < min_dist:
                closest = ('end', i)
                min_dist = abs(event.x - e_px)
        
        if self.cursor_x is not None:
            c_px = self.ax.transData.transform((self.cursor_x, 0))[0]
            if abs(event.x - c_px) < min_dist:
                closest = 'cursor'
                min_dist = abs(event.x - c_px)

        self.dragging = closest
        if isinstance(closest, tuple):
            bound_type, idx = closest
            boundary_time = chars_bounds[idx][0] if bound_type == 'start' else chars_bounds[idx][1]
            self.cursor_x = boundary_time
            if bound_type == 'start':
                self.bound_lines[idx][0].set_color('#047857')
                self.bound_lines[idx][0].set_linewidth(4)
            elif bound_type == 'end':
                self.bound_lines[idx][1].set_color('#047857')
                self.bound_lines[idx][1].set_linewidth(4)
            self.update_cursor_graphics()
        elif closest == 'cursor':
            self.cursor_line.set_color('#064E3B')
            self.cursor_line.set_linewidth(2.5)
            
        if self.dragging:
            self.canvas.draw_idle()
        else:
            if event.xdata is not None:
                self.cursor_x = event.xdata
                self.dragging = 'cursor'
                self.cursor_line.set_color('#064E3B')
                self.cursor_line.set_linewidth(2.5)
                self.update_cursor_graphics()

    def update_cursor_graphics(self):
        if not self.cursor_line or not self.cursor_text: return
        self.cursor_line.set_xdata([self.cursor_x, self.cursor_x])
        self.cursor_text.set_position((self.cursor_x, 5000))
        self.cursor_text.set_text(f"{self.cursor_x:.3f}")
        self.canvas.draw_idle()

    def on_motion(self, event):
        if not self.ax or not self.current_item or event.xdata is None: return
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
            
        if isinstance(self.dragging, tuple):
            bound_type, idx = self.dragging
            if bound_type == 'start':
                chars_bounds[idx][0] = min(event.xdata, chars_bounds[idx][1] - 0.01)
                self.cursor_x = chars_bounds[idx][0]
            elif bound_type == 'end':
                chars_bounds[idx][1] = max(event.xdata, chars_bounds[idx][0] + 0.01)
                self.cursor_x = chars_bounds[idx][1]

            if chars_bounds:
                item['start'] = chars_bounds[0][0]
                item['end'] = chars_bounds[-1][1]

            # Update the cursor line and text coordinates in real-time
            if self.cursor_line and self.cursor_text:
                self.cursor_line.set_xdata([self.cursor_x, self.cursor_x])
                self.cursor_text.set_position((self.cursor_x, 5000))
                self.cursor_text.set_text(f"{self.cursor_x:.3f}")
        elif self.dragging == 'cursor':
            self.cursor_x = event.xdata
            self.update_cursor_graphics()
            return
            
        self.update_lines()

    def on_release(self, event):
        if self.dragging:
            self.dragging = None
            for line_s, line_e in self.bound_lines:
                line_s.set_color('#EF4444')
                line_e.set_color('#EF4444')
                line_s.set_linewidth(2)
                line_e.set_linewidth(2)
            if self.cursor_line:
                self.cursor_line.set_color('#1B5E20')
                self.cursor_line.set_linewidth(1.5)
                
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
                
            self.plot_item_spectrogram()
            self.update_ui_times()
        except ValueError: 
            messagebox.showerror("错误", "请输入有效的数字")

    def play_selected(self):
        item = self.current_item
        if not item: return
        if not item.get('snd') and item.get('path'):
            try: item['snd'] = parselmouth.Sound(item['path'])
            except Exception: return
        if not item.get('snd'): return

        snd = item['snd']

        if self.is_playing:
            try:
                import sounddevice as sd
                sd.stop()
            except Exception:
                pass

        try:
            total_duration = snd.get_total_duration()
            if self.cursor_x is None:
                self.cursor_x = item['start']

            chars_bounds = item.get('chars_bounds', [])
            play_s = None
            play_e = None
            self.play_is_selection = False
            self.play_selection_start = 0.0

            for c_s, c_e in chars_bounds:
                if c_s <= self.cursor_x <= c_e:
                    play_s = self.cursor_x
                    play_e = c_e
                    self.play_is_selection = True
                    self.play_selection_start = c_s
                    break

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

            import sounddevice as sd
            sd.play(audio_data, samplerate=int(part.sampling_frequency))

            import time
            self.is_playing = True
            self.play_start_sys_time = time.time()
            self.play_start_audio_time = play_s
            self.play_end_audio_time = play_e

            self._playback_update_loop()

        except Exception as e:
            messagebox.showerror("错误", f"播放失败: {str(e)}")

    def _playback_update_loop(self):
        if not self.is_playing: return
        import time
        elapsed = time.time() - self.play_start_sys_time
        current_audio_time = self.play_start_audio_time + elapsed

        if current_audio_time >= self.play_end_audio_time:
            self.is_playing = False
            if getattr(self, 'play_is_selection', False):
                self.cursor_x = getattr(self, 'play_selection_start', self.current_item['start'])
            else:
                self.cursor_x = self.play_end_audio_time
            self.update_cursor_graphics()
            return

        self.cursor_x = current_audio_time
        self.update_cursor_graphics()
        self.canvas.get_tk_widget().after(16, self._playback_update_loop)

    def apply_auto_detect(self):
        if self.on_auto_detect_callback:
            self.on_auto_detect_callback()