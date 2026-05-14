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
        self.dragging = None
        self.ax = None
        self.ax2 = None
        self.switch_trim_silence = None
        self.font_title = ctk.CTkFont(family="Microsoft YaHei", size=15, weight="bold")
        
        self.setup_ui()
        
    def setup_ui(self):
        center_frame = ctk.CTkFrame(self.parent, fg_color="white", corner_radius=10)
        center_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, pady=10)
        
        top_bar = ctk.CTkFrame(center_frame, fg_color="transparent")
        top_bar.pack(side=tk.TOP, fill=tk.X, padx=15, pady=(15, 5))
        
        frame_tune = ctk.CTkFrame(top_bar, fg_color="#F9FAFB", corner_radius=8)
        frame_tune.pack(side=tk.TOP, fill=tk.X, pady=(0, 10))
        ctk.CTkLabel(frame_tune, text="当前区间(s):", font=self.font_title, text_color="#111827").pack(side=tk.LEFT, padx=(10, 10), pady=10)
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
        self.fig.clf()
        self.canvas.draw()
        self.var_t_start.set("0.000")
        self.var_t_end.set("0.000")

    def load_item(self, item):
        self.current_item = item
        self.var_t_start.set(f"{item['start']:.3f}")
        self.var_t_end.set(f"{item['end']:.3f}")
        self.plot_item_spectrogram()

    def plot_item_spectrogram(self):
        item = self.current_item
        if not item: return
        if not item.get('snd') or item.get('start') is None: return
        
        self.fig.clf()
        self.ax = self.fig.add_subplot(111)
        self.ax2 = self.ax.twinx()
        
        snd = item['snd']
        t_s, t_e = item['start'], item['end']
        
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
        
        pitch = part.to_pitch()
        p_xs = pitch.xs() + view_s
        p_vals = pitch.selected_array['frequency']
        p_vals[p_vals == 0] = np.nan
        self.ax2.plot(p_xs, p_vals, 'o', markersize=4, color='#3B82F6', zorder=5)
        
        self.ax.set_ylim([0, 5000])
        self.ax.set_xlim([view_s, view_e])
        self.ax.set_ylabel("Frequency (Hz)")
        self.ax2.set_ylim([50, 500])
        self.ax2.set_ylabel("F0 (Hz)", color='#3B82F6')
        self.ax2.tick_params(axis='y', labelcolor='#3B82F6')
        self.ax.set_title(f"字: {item['label']}", pad=10)
        
        self.line_start = self.ax.axvline(t_s, color='#EF4444', linestyle='-', linewidth=2)
        self.line_end = self.ax.axvline(t_e, color='#EF4444', linestyle='-', linewidth=2)
        self.span_fill = self.ax.axvspan(t_s, t_e, color='#BFDBFE', alpha=0.35) 
        
        self.fig.tight_layout()
        self.canvas.draw()

    def on_press(self, event):
        if not self.ax or not self.ax2 or not self.current_item: return
        if event.inaxes not in [self.ax, self.ax2] or event.button != 1: return
        item = self.current_item
        start_px = self.ax.transData.transform((item['start'], 0))[0]
        end_px = self.ax.transData.transform((item['end'], 0))[0]
        
        if abs(event.x - start_px) < 15:
            self.dragging = 'start'
            self.line_start.set_color('#047857') 
            self.line_start.set_linewidth(4)
        elif abs(event.x - end_px) < 15:
            self.dragging = 'end'
            self.line_end.set_color('#047857') 
            self.line_end.set_linewidth(4)
            
        if self.dragging: self.canvas.draw_idle()

    def on_motion(self, event):
        if not self.ax or not self.current_item or event.xdata is None: return
        item = self.current_item
        
        if not self.dragging:
            start_px = self.ax.transData.transform((item['start'], 0))[0]
            end_px = self.ax.transData.transform((item['end'], 0))[0]
            is_hovering = False
            
            if abs(event.x - start_px) < 15:
                self.line_start.set_linewidth(4)
                self.line_start.set_color('#B91C1C') 
                self.canvas.get_tk_widget().config(cursor="sb_h_double_arrow")
                is_hovering = True
            else:
                self.line_start.set_linewidth(2)
                self.line_start.set_color('#EF4444')
                
            if abs(event.x - end_px) < 15:
                self.line_end.set_linewidth(4)
                self.line_end.set_color('#B91C1C')
                self.canvas.get_tk_widget().config(cursor="sb_h_double_arrow")
                is_hovering = True
            else:
                self.line_end.set_linewidth(2)
                self.line_end.set_color('#EF4444')
                
            if not is_hovering:
                self.canvas.get_tk_widget().config(cursor="arrow")
                
            self.canvas.draw_idle()
            return
            
        if self.dragging == 'start': item['start'] = event.xdata
        elif self.dragging == 'end': item['end'] = event.xdata
        self.update_lines(item['start'], item['end'])

    def on_release(self, event):
        if self.dragging:
            item = self.current_item
            if item['start'] > item['end']: item['start'], item['end'] = item['end'], item['start']
            self.dragging = None
            self.line_start.set_color('#EF4444')
            self.line_end.set_color('#EF4444')
            self.line_start.set_linewidth(2)
            self.line_end.set_linewidth(2)
            self.update_lines(item['start'], item['end'])
            self.update_ui_times()
            self.canvas.get_tk_widget().config(cursor="arrow")

    def update_lines(self, t_s, t_e):
        if not hasattr(self, 'line_start'): return
        self.line_start.set_xdata([t_s, t_s])
        self.line_end.set_xdata([t_e, t_e])
        try: self.span_fill.remove()
        except Exception: pass
        self.span_fill = self.ax.axvspan(t_s, t_e, color='#BFDBFE', alpha=0.35)
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
            item['start'] = min(t1, t2)
            item['end'] = max(t1, t2)
            self.update_lines(item['start'], item['end'])
            self.update_ui_times()
        except ValueError: messagebox.showerror("错误", "请输入有效的数字")

    def play_selected(self):
        item = self.current_item
        if not item: return
        if not item.get('snd') and item.get('path'):
            try: item['snd'] = parselmouth.Sound(item['path'])
            except Exception: return
        if not item.get('snd'): return
        try:
            part = item['snd'].extract_part(from_time=item['start'], to_time=item['end'])
            audio_data = np.ascontiguousarray(part.values.T, dtype=np.float32)
            sd.play(audio_data, samplerate=int(part.sampling_frequency))
        except Exception as e: messagebox.showerror("错误", f"播放失败: {str(e)}")

    def apply_auto_detect(self):
        if self.on_auto_detect_callback:
            self.on_auto_detect_callback()
