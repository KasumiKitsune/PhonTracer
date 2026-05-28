import os
import sys
import threading
import re
import subprocess
import platform
import json
import zipfile
import shutil
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import customtkinter as ctk
import encodings.cp437
import gc
import queue

# Keep-alive list to prevent temporary CTkFont objects from being garbage collected on background threads
_keep_alive_fonts = []
_original_ctkfont = ctk.CTkFont

class SafeCTkFont(_original_ctkfont):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _keep_alive_fonts.append(self)

ctk.CTkFont = SafeCTkFont

def start_safe_thread(target):
    gc.collect()
    gc.disable()
    def wrapped():
        try:
            target()
        finally:
            gc.enable()
    threading.Thread(target=wrapped, daemon=True).start()

import numpy as np

import parselmouth
from PIL import Image, ImageTk
from modules.data_utils import fuzzy_match_word_to_path

try:
    import sounddevice as sd
except ImportError:
    sd = None

# --- 核心修复：开启高 DPI 感知，防止图标和界面模糊 ---
if sys.platform == "win32":
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

# ==========================================
# UI 自定义组件
# ==========================================

class CTkReleaseButton(ctk.CTkButton):
    """
    自定义按钮类，将 command 触发时机从“按下”改为“松开且在按钮范围内”。
    避免误触并提升交互手感。
    """
    def __init__(self, master=None, **kwargs):
        self._release_command = kwargs.pop("command", None)
        super().__init__(master, **kwargs)
        self._is_pressed = False
        if self._release_command:
            widgets = [self]
            for attr in ("_canvas", "_text_label", "_image_label"):
                if hasattr(self, attr):
                    w = getattr(self, attr)
                    if w:
                        widgets.append(w)
            for w in widgets:
                w.bind("<ButtonPress-1>", self._on_press, add="+")
                w.bind("<ButtonRelease-1>", self._on_release, add="+")
            
    def _on_press(self, event):
        if self.cget("state") == "disabled":
            return
        self._is_pressed = True

    def _on_release(self, event):
        if not getattr(self, "_is_pressed", False):
            return
        self._is_pressed = False

        if self.cget("state") == "disabled":
            return
        x, y = self.winfo_pointerxy()
        btn_x = self.winfo_rootx()
        btn_y = self.winfo_rooty()
        btn_w = self.winfo_width()
        btn_h = self.winfo_height()
        
        if btn_x <= x <= btn_x + btn_w and btn_y <= y <= btn_y + btn_h:
            if self._release_command:
                self._release_command()

# ==========================================
# 核心音频处理算法
# ==========================================

def macroscopic_vad(snd: parselmouth.Sound, min_dur=0.1, merge_thresh=0.25):
    """VAD 静音检测：用于拆分音频时自动识别有效发音段"""
    intensity = snd.to_intensity(time_step=0.01)
    vals = intensity.values[0]
    xs = intensity.xs()
    sorted_vals = np.sort(vals[~np.isnan(vals)])

    if len(sorted_vals) > 20:
        max_int = np.mean(sorted_vals[-int(len(sorted_vals)*0.05):])
        noise_floor = np.mean(sorted_vals[:int(len(sorted_vals)*0.1)])
        thresh = max(noise_floor + 15, max_int - 25)
    else:
        thresh = 50.0

    is_sp = vals > thresh
    starts_idx = np.where(np.diff(is_sp.astype(int), prepend=0) == 1)[0]
    ends_idx = np.where(np.diff(is_sp.astype(int), append=0) == -1)[0]
    
    segs = []
    for s_idx, e_idx in zip(starts_idx, ends_idx):
        if s_idx < len(xs) and e_idx < len(xs):
            segs.append([xs[s_idx], xs[e_idx]])
    
    merged = []
    for s in segs:
        if not merged: merged.append(s)
        else:
            if s[0] - merged[-1][1] < merge_thresh: merged[-1][1] = s[1]
            else: merged.append(s)
            
    return [s for s in merged if s[1]-s[0] > min_dur]

def parse_wordlist(raw_text: str):
    """解析字表，兼容主程序的【组别】格式"""
    flat_words = []
    for line in raw_text.split('\n'):
        line = line.strip()
        if not line: continue
        if line.startswith('【') or line.startswith('[') or line.startswith('［') or line.startswith('#'):
            continue
        words = [w.strip() for w in re.split(r'[,\s\t，、]+', line) if w.strip()]
        flat_words.extend(words)
    return flat_words

# ==========================================
# 可视化段落编辑器 (直接移植自主程序)
# ==========================================

class VisualSplitter(ctk.CTkToplevel):
    def __init__(self, master, snd, icons, callback, existing_items=None, vad_segments=None, **kwargs):
        super().__init__(master)
        self.title("段落编辑器")
        
        w, h = 950, 550
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        x = (sw - w) // 2
        y = (sh - h) // 2
        self.geometry(f"{w}x{h}+{x}+{y}")
        self.attributes('-topmost', True)
        
        self.snd = snd
        self.icons = icons
        self.callback = callback
        
        # 确保文字为白色
        self.btn_kwargs = {"text_color": "white", "corner_radius": 20, "height": 36, "font": ("Microsoft YaHei", 13, "bold")}
        
        if existing_items:
            self.mode = 'edit'
            self.segments = existing_items
            for i, seg in enumerate(self.segments):
                seg['orig_start'] = seg['start']
                seg['orig_end'] = seg['end']
                seg['orig_inner_splits'] = list(seg.get('inner_splits', []))
            self.original_words = [{'id': s['id'], 'label': s['label']} for s in existing_items if s.get('id') is not None]
        elif vad_segments:
            self.mode = 'review'
            self.segments = [{'start': s, 'end': e, 'label': f'#{i+1}'} for i, (s, e) in enumerate(vad_segments)]
        else:
            self.mode = 'cut'
            self.segments = []
            
        self.cuts = [] 
        self.deleted_indices = set()
        self.px_per_sec = 100
        self.duration = self.snd.get_total_duration()
        self.dragging = None
        self.play_rects = []
        self.delete_rects = []
        self.wordlist = kwargs.get('wordlist', [])
        
        self.setup_ui()
        self.init_data()
        self.update_dynamic_labels()
        self.after(100, self.auto_fit_scale)
        
    def auto_fit_scale(self):
        cw = self.canvas.winfo_width()
        if cw > 100:
            fit_scale = cw / self.duration
            min_px_per_sec = 20
            if self.segments:
                total_seg_time = sum([s['end'] - s['start'] for s in self.segments])
                avg_seg_dur = total_seg_time / len(self.segments) if len(self.segments) > 0 else 0.5
                if avg_seg_dur < 0.1: avg_seg_dur = 0.1
                min_px_per_sec = 100 / avg_seg_dur
            self.px_per_sec = max(min_px_per_sec, fit_scale)
            self.px_per_sec = round(self.px_per_sec / 25) * 25
            self.px_per_sec = max(25, min(2000, self.px_per_sec))
            self.zoom_slider.set(self.px_per_sec)
            self.update_zoom_label()
        self.render_canvas()

    def update_zoom_label(self):
        if hasattr(self, 'lbl_zoom'):
            self.lbl_zoom.configure(text=f"缩放: {int(self.px_per_sec)}")

    def setup_ui(self):
        self.configure(fg_color="#F9FAFB")
        info_frame = ctk.CTkFrame(self, height=45, fg_color="#F3F4F6", corner_radius=0)
        info_frame.pack(side=tk.TOP, fill=tk.X)
        
        if self.mode == 'cut': msg = "操作说明：【左键】添加切分线，【右键】删除最近线，【滚轮】滚动，【Ctrl+滚轮】缩放波形。"
        elif self.mode == 'review': msg = "VAD 自动检测完成。【右键】删除噪声段，点击标签【试听】，拖拽红线【微调边界】。"
        else: msg = "【右键】删除错误段。拖动【红线】微调边界。完成后点击右下角确认。"
            
        ctk.CTkLabel(info_frame, text=msg, font=("Microsoft YaHei", 13), text_color="#1F2937").pack(side=tk.LEFT, padx=20, pady=10)
        
        bottom_frame = ctk.CTkFrame(self, height=70, fg_color="white", corner_radius=0)
        bottom_frame.pack(side=tk.BOTTOM, fill=tk.X)
        
        if self.mode == 'cut':
            CTkReleaseButton(bottom_frame, text=" 清空所有点", image=self.icons.get("warning"), compound="left", fg_color="#EF4444", hover_color="#DC2626", text_color="white", corner_radius=20, height=36, command=self.clear_cuts).pack(side=tk.LEFT, padx=20, pady=15)
            self.lbl_count = ctk.CTkLabel(bottom_frame, text="当前切分点：0", font=("Microsoft YaHei", 13, "bold"), text_color="#4B5563")
            self.lbl_count.pack(side=tk.RIGHT, padx=20)
        
        if self.mode in ('review', 'edit'):
            self.lbl_count = ctk.CTkLabel(bottom_frame, text="", font=("Microsoft YaHei", 13, "bold"), text_color="#4B5563")
            self.lbl_count.pack(side=tk.RIGHT, padx=20)
            self.update_review_count()
        
        CTkReleaseButton(bottom_frame, text=" 确认并应用", image=self.icons.get("check"), compound="left", text_color="white", fg_color="#10B981", hover_color="#059669", corner_radius=20, height=40, width=120, command=self.confirm).pack(side=tk.RIGHT, padx=20, pady=15)
        
        zoom_frame = ctk.CTkFrame(bottom_frame, fg_color="transparent")
        zoom_frame.pack(side=tk.LEFT, padx=30, pady=15)
        self.lbl_zoom = ctk.CTkLabel(zoom_frame, text=f"缩放: {int(self.px_per_sec)}", font=("Microsoft YaHei", 13), text_color="#4B5563")
        self.lbl_zoom.pack(side=tk.LEFT, padx=5)
        self.zoom_slider = ctk.CTkSlider(zoom_frame, from_=25, to=2000, number_of_steps=79, command=self.on_zoom_change, button_color="#3B82F6", progress_color="#93C5FD")
        self.zoom_slider.set(self.px_per_sec)
        self.zoom_slider.pack(side=tk.LEFT)

        self.main_frame = ctk.CTkFrame(self, fg_color="white", corner_radius=12, border_width=1, border_color="#E5E7EB")
        self.main_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=(20, 10))
        
        self.canvas = tk.Canvas(self.main_frame, bg="white", highlightthickness=0)
        self.scrollbar = ttk.Scrollbar(self.main_frame, orient="horizontal", command=self.canvas.xview)
        self.canvas.configure(xscrollcommand=self.scrollbar.set)
        
        self.scrollbar.pack(side=tk.BOTTOM, fill=tk.X, padx=2, pady=2)
        self.canvas.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=2, pady=2)
        
        self.canvas.bind("<Button-1>", self.on_click)
        self.canvas.bind("<B1-Motion>", self.on_motion)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self.canvas.bind("<Motion>", self.on_hover)
        if self.mode in ('cut', 'review', 'edit'):
            self.canvas.bind("<Button-3>", self.on_right_click)
            self.canvas.bind("<Button-2>", self.on_right_click)
            
        self.canvas.bind("<MouseWheel>", self.on_mousewheel)
        self.canvas.bind("<Control-MouseWheel>", self.on_ctrl_mousewheel)
        if platform.system() == 'Darwin':
            self.canvas.bind("<Command-MouseWheel>", self.on_ctrl_mousewheel)
        self.canvas.bind("<Configure>", lambda e: self.render_canvas())

    def init_data(self):
        full_values = self.snd.values[0]
        target_sr = 2000
        step = max(1, int(self.snd.sampling_frequency / target_sr))
        self.envelope_data = full_values[::step]

    def on_zoom_change(self, val):
        old_zoom = self.px_per_sec
        new_zoom = round(float(val) / 25) * 25
        if new_zoom == old_zoom:
            return
            
        # 1. 计算当前视口中心的时间点
        viewport_width = self.canvas.winfo_width()
        center_canvas_x = self.canvas.canvasx(0) + viewport_width / 2
        time_at_center = center_canvas_x / old_zoom
        
        # 2. 存储缩放中心目标：保持 time_at_center 在屏幕中心位置不变
        self.zoom_target = {
            'time': time_at_center,
            'widget_x': viewport_width / 2
        }
        
        self.px_per_sec = new_zoom
        self.update_zoom_label()
        if hasattr(self, '_zoom_timer'):
            self.after_cancel(self._zoom_timer)
        self._zoom_timer = self.after(50, self.render_canvas)

    def on_mousewheel(self, event):
        delta = event.delta if platform.system() == 'Darwin' else event.delta / 120
        self.canvas.xview_scroll(int(-1 * delta), "units")

    def on_ctrl_mousewheel(self, event):
        delta = event.delta if platform.system() == 'Darwin' else event.delta / 120
        old_zoom = self.px_per_sec
        new_zoom = old_zoom * (1.2 if delta > 0 else 0.8)
        new_zoom = max(25, min(2000, round(new_zoom / 25) * 25))
        if new_zoom == old_zoom:
            return
            
        # 1. 计算当前鼠标指针所在的时间点
        canvas_x = self.canvas.canvasx(event.x)
        time_at_mouse = canvas_x / old_zoom
        
        # 2. 存储缩放中心目标：保持 time_at_mouse 在 event.x 位置不变
        self.zoom_target = {
            'time': time_at_mouse,
            'widget_x': event.x
        }
        
        self.px_per_sec = new_zoom
        self.zoom_slider.set(new_zoom)
        self.update_zoom_label()
        self.render_canvas()

    def update_dynamic_labels(self):
        # 统一处理动态标签显示
        word_idx = 0
        for i, seg in enumerate(self.segments):
            if i in self.deleted_indices:
                seg['dyn_label'] = "已剔除"
                seg['dyn_id'] = None
            else:
                # 优先匹配传入的字表 (wordlist)
                if self.wordlist and word_idx < len(self.wordlist):
                    dyn_lbl = self.wordlist[word_idx]
                    seg['dyn_label'] = dyn_lbl
                    seg['dyn_id'] = word_idx
                    word_idx += 1
                # 如果是编辑模式且有原始词汇信息
                elif hasattr(self, 'original_words') and word_idx < len(self.original_words):
                    dyn_lbl = self.original_words[word_idx]['label']
                    seg['dyn_label'] = dyn_lbl
                    seg['dyn_id'] = self.original_words[word_idx]['id']
                    word_idx += 1
                else:
                    seg['dyn_label'] = f"#{word_idx + 1}" if self.mode == 'review' else "【未分配段】"
                    seg['dyn_id'] = None
                    word_idx += 1

    def render_canvas(self):
        self.canvas_width = int(self.duration * self.px_per_sec)
        self.canvas_height = self.canvas.winfo_height()
        if self.canvas_height < 100: self.canvas_height = 400
        
        self.canvas.delete("all")
        self.canvas.config(scrollregion=(0, 0, self.canvas_width, self.canvas_height))
        
        # 绘制片段背景
        if self.mode in ('edit', 'review'):
            for i, seg in enumerate(self.segments):
                x1 = seg['start'] * self.px_per_sec
                x2 = seg['end'] * self.px_per_sec
                bg_color = "#FEE2E2" if i in self.deleted_indices else "#EFF6FF"
                self.canvas.create_rectangle(x1, 0, x2, self.canvas_height, fill=bg_color, outline="")

        # 波形中心线
        mid_y = self.canvas_height / 2 + 20
        self.canvas.create_line(0, mid_y, self.canvas_width, mid_y, fill="#E5E7EB")
        
        # 绘制波形
        draw_step = max(1, len(self.envelope_data) // self.canvas_width)
        draw_values = self.envelope_data[::draw_step]
        points = []
        n = len(draw_values)
        if n > 1:
            for i, val in enumerate(draw_values):
                x = (i / n) * self.canvas_width
                y = mid_y - (val * (self.canvas_height/2 - 30) * 0.9)
                points.extend([x, y])
            chunk_size = 8000
            for i in range(0, len(points), chunk_size):
                chunk = points[i:i+chunk_size+2]
                if len(chunk) >= 4:
                    self.canvas.create_line(chunk, fill="#9CA3AF", width=1, tags="waveform")
        
        # 刻度
        step_sec = 1 if self.px_per_sec > 50 else 5
        if self.px_per_sec > 200: step_sec = 0.5
        for t in np.arange(0, self.duration, step_sec):
            x = t * self.px_per_sec
            self.canvas.create_line(x, self.canvas_height-15, x, self.canvas_height, fill="#D1D5DB")
            self.canvas.create_text(x+2, self.canvas_height-8, text=f"{t}s", anchor=tk.W, font=("Arial", 9), fill="#6B7280")

        # 边界与标签
        if self.mode == 'cut':
            for cut in self.cuts:
                x = cut * self.px_per_sec
                self.canvas.create_line(x, 0, x, self.canvas_height, fill="#EF4444", width=2)
        elif self.mode in ('edit', 'review'):
            self.play_rects = []
            self.delete_rects = []
            for i, seg in enumerate(self.segments):
                is_deleted = i in self.deleted_indices
                x1 = seg['start'] * self.px_per_sec
                x2 = seg['end'] * self.px_per_sec
                
                line_color = "#D1D5DB" if is_deleted else "#EF4444"
                line_dash = (4, 4) if is_deleted else ()
                self.canvas.create_line(x1, 0, x1, self.canvas_height, fill=line_color, width=2, dash=line_dash)
                self.canvas.create_line(x2, 0, x2, self.canvas_height, fill=line_color, width=2, dash=line_dash)
                
                tag_y = 25
                display_label = seg.get('dyn_label', seg['label'])
                
                def create_pill(canvas, x, y, text, bg_color, tags):
                    text_id = canvas.create_text(x, y, text=text, font=("Microsoft YaHei", 10, "bold"), fill="white", tags=tags)
                    b = canvas.bbox(text_id)
                    h = b[3] - b[1] + 8
                    canvas.create_line(b[0], y, b[2], y, width=h, fill=bg_color, capstyle='round', tags=tags)
                    canvas.tag_raise(text_id)
                    return canvas.bbox(tags)

                cx = (x1 + x2) / 2
                if is_deleted:
                    bbox = create_pill(self.canvas, cx, tag_y, f"✕ {display_label}", "#9CA3AF", f"btn_{i}")
                    self.delete_rects.append({'idx': i, 'bbox': bbox})
                else:
                    bbox = create_pill(self.canvas, cx, tag_y, f"▶ {display_label}", "#3B82F6", f"btn_{i}")
                    self.play_rects.append({'idx': i, 'start': seg['start'], 'end': seg['end'], 'bbox': bbox})

        # 在绘制完毕后，如果存在 zoom_target，则执行视口精确滚动
        if hasattr(self, 'zoom_target') and self.zoom_target:
            target_time = self.zoom_target['time']
            widget_x = self.zoom_target['widget_x']
            new_left_x = target_time * self.px_per_sec - widget_x
            fraction = max(0.0, min(1.0, new_left_x / self.canvas_width))
            self.canvas.xview_moveto(fraction)
            self.zoom_target = None

    def clear_cuts(self):
        self.cuts = []
        self.render_canvas()
        if hasattr(self, 'lbl_count'): self.lbl_count.configure(text="当前切分点：0")

    def update_review_count(self):
        if hasattr(self, 'lbl_count'):
            total = len(self.segments)
            deleted = len(self.deleted_indices)
            kept = total - deleted
            if deleted > 0: self.lbl_count.configure(text=f"共 {total} 段 | 保留 {kept} 段 | 已移除 {deleted} 段", text_color="#DC2626")
            else: self.lbl_count.configure(text=f"共 {total} 个检测区段", text_color="#4B5563")

    def toggle_delete_segment(self, idx):
        if idx in self.deleted_indices: self.deleted_indices.discard(idx)
        else: self.deleted_indices.add(idx)
        self.update_dynamic_labels()
        self.render_canvas()
        if self.mode in ('review', 'edit'): self.update_review_count()

    def add_segment_at(self, start, end):
        new_seg = {
            'start': start,
            'end': end,
            'label': f'#{len(self.segments)+1}'
        }
        if self.mode == 'edit':
            new_seg['orig_start'] = start
            new_seg['orig_end'] = end
            new_seg['orig_inner_splits'] = []
            new_seg['inner_splits'] = []
            
        # Find sorted insertion index
        insert_idx = 0
        while insert_idx < len(self.segments) and self.segments[insert_idx]['start'] < start:
            insert_idx += 1
            
        self.segments.insert(insert_idx, new_seg)
        
        # Adjust deleted_indices because we shifted segments after insert_idx
        new_deleted_indices = set()
        for idx in self.deleted_indices:
            if idx >= insert_idx:
                new_deleted_indices.add(idx + 1)
            else:
                new_deleted_indices.add(idx)
        self.deleted_indices = new_deleted_indices
        
        self.update_dynamic_labels()
        if self.mode in ('review', 'edit'):
            self.update_review_count()
        self.render_canvas()

    def _get_element_at_x(self, x):
        tolerance = 10
        time_sec = x / self.px_per_sec
        for i, seg in enumerate(self.segments):
            if i in self.deleted_indices: continue
            if abs(seg['start'] - time_sec) * self.px_per_sec < tolerance: return i, 'start'
            if abs(seg['end'] - time_sec) * self.px_per_sec < tolerance: return i, 'end'
        return None, None

    def _get_segment_at_x(self, x):
        time_sec = x / self.px_per_sec
        for i, seg in enumerate(self.segments):
            if seg['start'] <= time_sec <= seg['end']: return i
        return None

    def on_hover(self, event):
        canvas_x = self.canvas.canvasx(event.x)
        canvas_y = self.canvas.canvasy(event.y)
        
        if self.mode in ('edit', 'review'):
            for pr in reversed(self.play_rects):
                x1, y1, x2, y2 = pr['bbox']
                if x1 <= canvas_x <= x2 and y1 <= canvas_y <= y2:
                    return self.canvas.config(cursor="hand2")
            for dr in reversed(self.delete_rects):
                x1, y1, x2, y2 = dr['bbox']
                if x1 <= canvas_x <= x2 and y1 <= canvas_y <= y2:
                    return self.canvas.config(cursor="hand2")
        
        if self.mode in ('edit', 'review') and not self.dragging:
            idx, bound = self._get_element_at_x(canvas_x)
            if idx is not None:
                return self.canvas.config(cursor="sb_h_double_arrow")
                
        self.canvas.config(cursor="arrow")

    def on_click(self, event):
        canvas_x = self.canvas.canvasx(event.x)
        canvas_y = self.canvas.canvasy(event.y)
        time_sec = canvas_x / self.px_per_sec
        
        if self.mode in ('edit', 'review'):
            for pr in reversed(self.play_rects):
                x1, y1, x2, y2 = pr['bbox']
                if x1 <= canvas_x <= x2 and y1 <= canvas_y <= y2:
                    return self.play_segment(pr['start'], pr['end'])
            for dr in reversed(self.delete_rects):
                x1, y1, x2, y2 = dr['bbox']
                if x1 <= canvas_x <= x2 and y1 <= canvas_y <= y2:
                    return self.toggle_delete_segment(dr['idx'])
                    
            idx, bound = self._get_element_at_x(canvas_x)
            if idx is not None:
                self.dragging = {'seg_idx': idx, 'bound': bound}
                
        elif self.mode == 'cut' and 0 <= time_sec <= self.duration:
            self.cuts.append(time_sec)
            self.cuts.sort()
            self.render_canvas()
            if hasattr(self, 'lbl_count'): self.lbl_count.configure(text=f"当前切分点：{len(self.cuts)}")

    def on_motion(self, event):
        if self.dragging:
            canvas_x = self.canvas.canvasx(event.x)
            time_sec = max(0, min(self.duration, canvas_x / self.px_per_sec))
            idx = self.dragging['seg_idx']
            bound = self.dragging['bound']
            seg = self.segments[idx]
            
            if bound == 'start': seg['start'] = min(time_sec, seg['end'] - 0.01)
            elif bound == 'end': seg['end'] = max(time_sec, seg['start'] + 0.01)
            self.render_canvas()

    def on_release(self, event):
        self.dragging = None

    def on_right_click(self, event):
        canvas_x = self.canvas.canvasx(event.x)
        if self.mode == 'cut' and self.cuts:
            time_sec = canvas_x / self.px_per_sec
            closest_idx = np.argmin([abs(c - time_sec) for c in self.cuts])
            if abs(self.cuts[closest_idx] - time_sec) < (20 / self.px_per_sec):
                self.cuts.pop(closest_idx)
                self.render_canvas()
                if hasattr(self, 'lbl_count'): self.lbl_count.configure(text=f"当前切分点：{len(self.cuts)}")
        elif self.mode in ('review', 'edit'):
            seg_idx = self._get_segment_at_x(canvas_x)
            if seg_idx is not None:
                self.toggle_delete_segment(seg_idx)
            else:
                time_sec = canvas_x / self.px_per_sec
                start = max(0, time_sec - 0.25)
                end = min(self.duration, start + 0.5)
                if end - start < 0.5 and self.duration >= 0.5:
                    start = max(0, end - 0.5)
                self.add_segment_at(start, end)

    def play_segment(self, start_t, end_t):
        if not sd:
            return messagebox.showerror("错误", "缺少 sounddevice 模块，无法试听。")
        try:
            part = self.snd.extract_part(from_time=start_t, to_time=end_t)
            audio_data = np.ascontiguousarray(part.values.T, dtype=np.float32)
            sd.play(audio_data, samplerate=int(part.sampling_frequency))
        except Exception as e:
            messagebox.showerror("错误", f"播放失败: {str(e)}")

    def confirm(self):
        if self.mode == 'cut':
            if not self.cuts: return messagebox.showwarning("提示", "请至少添加一个切分点。")
            sorted_cuts = sorted(self.cuts)
            segments = []
            last_t = 0
            for c in sorted_cuts:
                if c > last_t: segments.append((last_t, c))
                last_t = c
            if last_t < self.duration: segments.append((last_t, self.duration))
            self.destroy()
            self.callback(segments, False)
        elif self.mode == 'review':
            kept_segments = [(seg['start'], seg['end']) for i, seg in enumerate(self.segments) if i not in self.deleted_indices]
            if not kept_segments: return messagebox.showwarning("提示", "至少需要保留一个区段。")
            self.destroy()
            self.callback(kept_segments, False)
        else:
            kept_segments = []
            for seg in self.segments:
                if seg.get('dyn_id') is not None:
                    kept_segments.append({
                        'id': seg['dyn_id'],
                        'start': seg['start'],
                        'end': seg['end']
                    })
            self.destroy()
            self.callback(kept_segments, True, len(self.deleted_indices))


# ==========================================
# 主程序 App
# ==========================================

class AudioToolkitApp(ctk.CTk):
    def __init__(self):
        self._ui_thread_id = threading.get_ident()
        self.gui_queue = queue.Queue()
        super().__init__()
        self.process_gui_queue()
        ctk.set_appearance_mode("Light")
        try:
            ctk.set_default_color_theme("blue")
        except Exception:
            pass

        self.title("PhonTracer - 独立音频处理套件")
        self.geometry("1000x660")
        self.minsize(900, 600)

        self.colors = {
            "bg": "#EEF2F6",
            "surface": "#FFFFFF",
            "surface_soft": "#F8FAFC",
            "surface_warm": "#FAFAF8",
            "border": "#E2E8F0",
            "border_strong": "#CBD5E1",
            "text": "#17202A",
            "text_soft": "#334155",
            "muted": "#64748B",
            "muted_soft": "#94A3B8",
            "primary": "#2563EB",
            "primary_hover": "#1D4ED8",
            "primary_soft": "#DBEAFE",
            "success": "#10B981",
            "success_hover": "#059669",
            "success_soft": "#D1FAE5",
            "warning": "#F59E0B",
            "warning_hover": "#D97706",
            "warning_soft": "#FEF3C7",
            "danger": "#EF4444",
            "danger_hover": "#DC2626",
            "danger_soft": "#FEE2E2",
            "purple": "#6366F1",
            "purple_hover": "#4F46E5",
        }
        self.configure(fg_color=self.colors["bg"])

        self.font_family = "Microsoft YaHei"
        self.font_title = ctk.CTkFont(family=self.font_family, size=16, weight="bold")
        self.font_main = ctk.CTkFont(family=self.font_family, size=13)
        self.font_small = ctk.CTkFont(family=self.font_family, size=12)
        self.font_caption = ctk.CTkFont(family=self.font_family, size=11)
        self.font_heading = ctk.CTkFont(family=self.font_family, size=24, weight="bold")

        self.merge_files = []
        self.split_source = None
        self.wordlist = []
        self.custom_segments = None  # 存储用户微调后的分段数据

        self.setup_icons()
        self.setup_ui()

        # 设置窗口图标
        ico_path = os.path.join(os.path.dirname(__file__), "assets", "tool_icon.ico")
        png_path = os.path.join(os.path.dirname(__file__), "assets", "tool_icon.png")

        if os.path.exists(ico_path) and sys.platform == "win32":
            try:
                self.iconbitmap(ico_path)
            except Exception:
                pass
        elif os.path.exists(png_path):
            try:
                img = Image.open(png_path)
                self.icon_photo = ImageTk.PhotoImage(img)
                self.iconphoto(True, self.icon_photo)  # True 会应用到子窗口
            except Exception:
                pass

        # 创建拖拽指示线 (在 setup_ui 之后，确保 self.tree_merge 已创建)
        self._drop_indicator = tk.Frame(self.tree_merge, bg=self.colors["primary"], height=2)
        self._drop_indicator.place_forget()

        # 关闭窗口级文件拖拽导入（windnd），规避 Windows + Tk 下偶发的 GIL 崩溃。
        self.window_drop_enabled = False

    def after(self, ms, func=None, *args):
        if func is not None and getattr(self, "_ui_thread_id", None) is not None and threading.get_ident() != self._ui_thread_id:
            self.gui_queue.put(lambda: func(*args))
            return None
        return super().after(ms, func, *args)

    def process_gui_queue(self):
        try:
            while True:
                task = self.gui_queue.get_nowait()
                try:
                    task()
                except Exception as e:
                    print(f"Error in GUI thread execution: {e}")
                finally:
                    self.gui_queue.task_done()
        except queue.Empty:
            pass
        super().after(10, self.process_gui_queue)

    def setup_icons(self):
        icon_path = os.path.join("assets", "icons")
        if not os.path.exists(icon_path):
            icon_path = os.path.join(os.path.dirname(__file__), "assets", "icons")
            
        self.icons = {}
        icon_files = {
            "audio": "audio_file.png", "cut": "cut.png", "batch": "batch.png",
            "eye": "eye.png", "list": "list.png", "plus": "plus.png",
            "play": "play.png", "save": "save.png", "check": "check.png",
            "warning": "warning.png", "import": "import_file.png", 
            "import_white": "import_white.png", "tab_batch": "tab_batch.png"
        }
        
        def make_white(img):
            img = img.convert("RGBA")
            data = np.array(img)
            # 将所有非透明像素变为白色
            alpha = data[:, :, 3]
            data[alpha > 0, 0:3] = 255
            return Image.fromarray(data)

        for key, filename in icon_files.items():
            path = os.path.join(icon_path, filename)
            if os.path.exists(path):
                img = Image.open(path)
                # 对于按钮上使用的图标，我们统一生成白色版本以确保协调
                if key in ["plus", "save", "check", "warning", "audio", "eye", "import_white", "tab_batch", "list"]:
                    img = make_white(img)
                self.icons[key] = ctk.CTkImage(light_image=img, dark_image=img, size=(20, 20))
            else:
                self.icons[key] = None
        
        # 加载软件 Logo
        logo_path = os.path.join(os.path.dirname(__file__), "assets", "tool_icon.png")
        if os.path.exists(logo_path):
            img = Image.open(logo_path)
            self.icons["logo"] = ctk.CTkImage(light_image=img, dark_image=img, size=(32, 32))

    # ==========================
    # 主界面视觉系统
    # ==========================

    def _button_colors(self, tone="primary"):
        palette = {
            "primary": (self.colors["primary"], self.colors["primary_hover"], "white"),
            "success": (self.colors["success"], self.colors["success_hover"], "white"),
            "warning": (self.colors["warning"], self.colors["warning_hover"], "white"),
            "danger": (self.colors["danger"], self.colors["danger_hover"], "white"),
            "purple": (self.colors["purple"], self.colors["purple_hover"], "white"),
            "secondary": ("#E2E8F0", "#CBD5E1", self.colors["text_soft"]),
            "ghost": (self.colors["surface"], "#F1F5F9", self.colors["text_soft"]),
        }
        return palette.get(tone, palette["primary"])

    def _make_button(self, parent, text, command, tone="primary", image=None, width=None, height=42, **kwargs):
        fg, hover, text_color = self._button_colors(tone)
        options = {
            "text": text,
            "command": command,
            "fg_color": fg,
            "hover_color": hover,
            "text_color": text_color,
            "corner_radius": 999,
            "height": height,
            "font": ctk.CTkFont(family=self.font_family, size=13, weight="bold"),
        }
        if width is not None:
            options["width"] = width
        if image is not None:
            options["image"] = image
            options["compound"] = "left"
        options.update(kwargs)
        return CTkReleaseButton(parent, **options)

    def _make_card(self, parent, fg_color=None, width=None, height=None, corner_radius=18):
        kwargs = {
            "fg_color": fg_color or self.colors["surface"],
            "corner_radius": corner_radius,
            "border_width": 1,
            "border_color": self.colors["border"],
        }
        if width is not None:
            kwargs["width"] = width
        if height is not None:
            kwargs["height"] = height
        return ctk.CTkFrame(parent, **kwargs)

    def _section_header(self, parent, title, subtitle=None, icon_text=None):
        frame = ctk.CTkFrame(parent, fg_color="transparent")
        frame.pack(fill=tk.X, padx=20, pady=(18, 12))

        title_row = ctk.CTkFrame(frame, fg_color="transparent")
        title_row.pack(fill=tk.X)
        if icon_text:
            ctk.CTkLabel(
                title_row,
                text=icon_text,
                font=ctk.CTkFont(family=self.font_family, size=18, weight="bold"),
                text_color=self.colors["primary"],
            ).pack(side=tk.LEFT, padx=(0, 8))
        ctk.CTkLabel(title_row, text=title, font=self.font_title, text_color=self.colors["text"]).pack(side=tk.LEFT)

        if subtitle:
            ctk.CTkLabel(
                frame,
                text=subtitle,
                font=self.font_small,
                text_color=self.colors["muted"],
                justify="left",
                wraplength=620,
            ).pack(fill=tk.X, anchor="w", pady=(4, 0))
        return frame

    def _make_labeled_entry(self, parent, label, variable, width=120, suffix=None):
        wrap = ctk.CTkFrame(parent, fg_color="transparent")
        wrap.pack(fill=tk.X, pady=(4, 14))
        ctk.CTkLabel(wrap, text=label, font=self.font_small, text_color=self.colors["text_soft"]).pack(anchor="w")
        row = ctk.CTkFrame(wrap, fg_color="transparent")
        row.pack(fill=tk.X, pady=(6, 0))
        entry = ctk.CTkEntry(
            row,
            textvariable=variable,
            width=width,
            height=38,
            corner_radius=19,
            border_color=self.colors["border_strong"],
            fg_color=self.colors["surface"],
            text_color=self.colors["text"],
            font=self.font_main,
        )
        entry.pack(side=tk.LEFT)
        if suffix:
            ctk.CTkLabel(row, text=suffix, font=self.font_small, text_color=self.colors["muted"]).pack(side=tk.LEFT, padx=8)
        return entry

    def _short_path(self, path, max_parts=2):
        if not path:
            return "未选择"
        normalized = path.replace("\\", "/")
        parts = [p for p in normalized.split("/") if p]
        if len(parts) <= max_parts:
            return normalized
        return "/".join(parts[-max_parts:])

    def _bind_adaptive_wrap(self, label, container, reserved_width=0, min_wrap=220):
        def update_wrap(_event=None):
            width = container.winfo_width()
            if width <= 1:
                return
            wrap = max(min_wrap, width - reserved_width)
            label.configure(wraplength=wrap)

        container.bind("<Configure>", update_wrap, add="+")
        self.after(0, update_wrap)

    @staticmethod
    def _decode_drop_path(raw_path):
        if isinstance(raw_path, bytes):
            for enc in ("gbk", "utf-8"):
                try:
                    return raw_path.decode(enc)
                except UnicodeDecodeError:
                    continue
            return raw_path.decode(errors="ignore")
        return str(raw_path)

    def _insert_merge_file(self, path):
        display_p = self._short_path(path, max_parts=2)
        self.tree_merge.insert("", tk.END, values=(path, display_p))
        self._refresh_merge_ui()

    def _refresh_merge_ui(self):
        count = len(self.merge_files)
        if hasattr(self, "lbl_merge_count"):
            self.lbl_merge_count.configure(text=f"{count} 个文件")
        if hasattr(self, "merge_empty_state"):
            if count == 0:
                self.merge_empty_state.place(relx=0.5, rely=0.5, anchor="center")
            else:
                self.merge_empty_state.place_forget()

    def setup_ui(self):
        self.btn_kwargs = {
            "text_color": "white",
            "corner_radius": 999,
            "height": 42,
            "font": ctk.CTkFont(family=self.font_family, size=13, weight="bold"),
        }

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        style = ttk.Style()
        style.theme_use("default")
        try:
            style.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
        except Exception:
            pass
        style.configure(
            "Treeview",
            font=(self.font_family, 12),
            rowheight=38,
            background=self.colors["surface"],
            fieldbackground=self.colors["surface"],
            foreground=self.colors["text"],
            borderwidth=0,
            relief="flat",
        )
        style.configure(
            "Treeview.Heading",
            font=(self.font_family, 12, "bold"),
            background=self.colors["surface_soft"],
            foreground=self.colors["text_soft"],
            padding=(12, 10),
            borderwidth=0,
            relief="flat",
        )
        style.map(
            "Treeview",
            background=[("selected", self.colors["primary"])],
            foreground=[("selected", "#FFFFFF")],
        )

        header_frame = ctk.CTkFrame(self, fg_color=self.colors["surface"], corner_radius=0, height=88)
        header_frame.grid(row=0, column=0, sticky="ew")
        header_frame.grid_propagate(False)
        header_frame.grid_columnconfigure(1, weight=1)

        brand = ctk.CTkFrame(header_frame, fg_color="transparent")
        brand.grid(row=0, column=0, sticky="w", padx=28, pady=16)
        if self.icons.get("logo"):
            ctk.CTkLabel(brand, text="", image=self.icons.get("logo")).pack(side=tk.LEFT, padx=(0, 14))
        title_block = ctk.CTkFrame(brand, fg_color="transparent")
        title_block.pack(side=tk.LEFT)
        ctk.CTkLabel(
            title_block,
            text="PhonTracer 音频处理套件",
            font=self.font_heading,
            text_color=self.colors["text"],
        ).pack(anchor="w")
        ctk.CTkLabel(
            title_block,
            text="合并长音频、按字表拆分、预览工程文件",
            font=self.font_small,
            text_color=self.colors["muted"],
        ).pack(anchor="w", pady=(2, 0))

        status_frame = ctk.CTkFrame(header_frame, fg_color=self.colors["success_soft"], corner_radius=999)
        status_frame.grid(row=0, column=2, sticky="e", padx=28)
        ctk.CTkLabel(status_frame, text="●", font=self.font_small, text_color=self.colors["success"]).pack(side=tk.LEFT, padx=(8, 4), pady=3)
        self.lbl_status = ctk.CTkLabel(status_frame, text="就绪", text_color="#047857", font=self.font_small)
        self.lbl_status.pack(side=tk.LEFT, padx=(0, 8), pady=3)

        self.main_shell = ctk.CTkFrame(self, fg_color="transparent")
        self.main_shell.grid(row=1, column=0, sticky="nsew", padx=24, pady=20)
        self.main_shell.grid_columnconfigure(0, weight=1)
        self.main_shell.grid_rowconfigure(0, weight=1)

        self.tab_merge_name = "合并长音频"
        self.tab_split_name = "按字表拆分"
        self.tab_project_name = "工程预览 / 压缩"

        self.tabview = ctk.CTkTabview(
            self.main_shell,
            corner_radius=20,
            fg_color=self.colors["surface"],
            border_width=1,
            border_color=self.colors["border"],
            segmented_button_fg_color="#E2E8F0",
            segmented_button_selected_color=self.colors["primary"],
            segmented_button_selected_hover_color=self.colors["primary_hover"],
            segmented_button_unselected_color="#E2E8F0",
            segmented_button_unselected_hover_color="#CBD5E1",
            text_color="#000000",
            text_color_disabled=self.colors["muted_soft"],
        )
        self.tabview.grid(row=0, column=0, sticky="nsew")

        self.tab_merge = self.tabview.add(self.tab_merge_name)
        self.tab_split = self.tabview.add(self.tab_split_name)
        self.tab_project = self.tabview.add(self.tab_project_name)

        for tab in (self.tab_merge, self.tab_split, self.tab_project):
            tab.configure(fg_color=self.colors["surface"])

        # Patch segmented button to allow different text colors for selected (white) vs unselected (black) tabs
        sb = self.tabview._segmented_button
        orig_sel = sb._select_button_by_value
        orig_unsel = sb._unselect_button_by_value
        sb._select_button_by_value = lambda v: (orig_sel(v), sb._buttons_dict[v].configure(text_color="#FFFFFF") if v in sb._buttons_dict else None)
        sb._unselect_button_by_value = lambda v: (orig_unsel(v), sb._buttons_dict[v].configure(text_color="#000000") if v in sb._buttons_dict else None)
        
        # Ensure initial tab text is white
        curr_val = sb.get()
        if curr_val in sb._buttons_dict:
            sb._buttons_dict[curr_val].configure(text_color="#FFFFFF")

        self.build_merge_tab()
        self.build_split_tab()
        self.build_project_tab()

        self.progress = ctk.CTkProgressBar(self, height=5, progress_color=self.colors["primary"], fg_color="#D8E0EA")
        self.progress.set(0)
        self.progress.grid(row=2, column=0, sticky="ew")
        self.progress.grid_remove()

    def build_merge_tab(self):
        content = ctk.CTkFrame(self.tab_merge, fg_color="transparent")
        content.pack(fill=tk.BOTH, expand=True, padx=20, pady=18)
        content.grid_columnconfigure(0, weight=1)
        content.grid_columnconfigure(1, weight=0)
        content.grid_rowconfigure(0, weight=1)

        left_panel = self._make_card(content)
        left_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 16))
        left_panel.grid_rowconfigure(1, weight=1)
        left_panel.grid_columnconfigure(0, weight=1)

        header = ctk.CTkFrame(left_panel, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=20, pady=(18, 12))
        header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(header, text="待合并文件", font=self.font_title, text_color=self.colors["text"]).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(
            header,
            text="拖入 wav/mp3，或手动添加；列表内拖拽可调整拼接顺序。",
            font=self.font_small,
            text_color=self.colors["muted"],
        ).grid(row=1, column=0, sticky="w", pady=(4, 0))
        self.lbl_merge_count = ctk.CTkLabel(header, text="0 个文件", font=self.font_small, text_color=self.colors["muted"])
        self.lbl_merge_count.grid(row=0, column=1, sticky="e", padx=(8, 0))
        self._make_button(header, "添加音频", self.add_merge_files, image=self.icons.get("plus"), width=132).grid(row=1, column=1, sticky="e", pady=(4, 0))

        tree_container = ctk.CTkFrame(left_panel, fg_color=self.colors["surface"], corner_radius=14, border_width=1, border_color=self.colors["border"])
        tree_container.grid(row=1, column=0, sticky="nsew", padx=20, pady=(0, 12))
        tree_container.grid_columnconfigure(0, weight=1)
        tree_container.grid_rowconfigure(0, weight=1)

        self.tree_merge = ttk.Treeview(tree_container, columns=("FullPath", "DisplayPath"), show="headings", height=15)
        self.tree_merge.heading("DisplayPath", text="文件路径")
        self.tree_merge.column("FullPath", width=0, stretch=tk.NO)
        self.tree_merge.column("DisplayPath", width=520, anchor="w")
        self.tree_merge.configure(displaycolumns=("DisplayPath",), style="Treeview", takefocus=False)

        self.merge_scroll = ctk.CTkScrollbar(tree_container, orientation="vertical", command=self.tree_merge.yview, width=12)
        self.tree_merge.configure(yscrollcommand=self.merge_scroll.set)
        self.tree_merge.grid(row=0, column=0, sticky="nsew", padx=(1, 0), pady=1)
        self.merge_scroll.grid(row=0, column=1, sticky="ns", padx=(0, 1), pady=1)
        self.tree_merge.bind('<BackSpace>', self.remove_merge_file)
        self.tree_merge.bind('<Delete>', self.remove_merge_file)
        self.tree_merge.bind("<Button-1>", self.on_tree_drag_start)
        self.tree_merge.bind("<B1-Motion>", self.on_tree_drag_motion)
        self.tree_merge.bind("<ButtonRelease-1>", self.on_tree_drag_drop)

        self.merge_empty_state = ctk.CTkFrame(tree_container, fg_color="transparent")
        ctk.CTkLabel(self.merge_empty_state, text="将音频文件拖到这里", font=ctk.CTkFont(family=self.font_family, size=18, weight="bold"), text_color=self.colors["text_soft"]).pack(pady=(0, 6))
        ctk.CTkLabel(self.merge_empty_state, text="支持 .wav / .mp3；按 Delete 可移除选中项", font=self.font_small, text_color=self.colors["muted"]).pack()
        self.merge_empty_state.place(relx=0.5, rely=0.5, anchor="center")

        ctk.CTkLabel(
            left_panel,
            text="提示：导入字表自动排序会用字表条目去模糊匹配文件名，未匹配到的文件会保留在末尾。",
            font=self.font_caption,
            text_color=self.colors["muted"],
        ).grid(row=2, column=0, sticky="w", padx=22, pady=(0, 16))

        right_panel = self._make_card(content, fg_color=self.colors["surface_soft"], width=310)
        right_panel.grid(row=0, column=1, sticky="ns")
        right_panel.grid_propagate(False)
        self._section_header(right_panel, "合并参数", "设置静音间隔后导出一个连续 wav 文件。", icon_text="01")

        settings = ctk.CTkFrame(right_panel, fg_color="transparent")
        settings.pack(fill=tk.X, padx=20, pady=(4, 10))
        self.var_gap = ctk.StringVar(value="0.5")
        self._make_labeled_entry(settings, "音频间隔", self.var_gap, width=110, suffix="秒静音")

        actions = ctk.CTkFrame(right_panel, fg_color="transparent")
        actions.pack(side=tk.BOTTOM, fill=tk.X, padx=20, pady=20)
        self._make_button(actions, "合并并导出音频", self.process_merge, tone="success", image=self.icons.get("save")).pack(fill=tk.X, pady=(0, 10))
        self._make_button(actions, "导入字表自动排序", self.import_wordlist_for_sort, tone="primary", image=self.icons.get("list")).pack(fill=tk.X, pady=(0, 10))
        self._make_button(actions, "清空列表", self.clear_merge_list, tone="danger").pack(fill=tk.X)

        self._refresh_merge_ui()

    def build_split_tab(self):
        content = ctk.CTkFrame(self.tab_split, fg_color="transparent")
        content.pack(fill=tk.BOTH, expand=True, padx=20, pady=18)
        content.grid_columnconfigure(0, weight=1)
        content.grid_columnconfigure(1, weight=0)
        content.grid_rowconfigure(1, weight=1)

        source_card = self._make_card(content, fg_color=self.colors["surface_soft"])
        source_card.grid(row=0, column=0, sticky="ew", padx=(0, 16), pady=(0, 16))
        source_card.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(source_card, text="音频源", font=self.font_title, text_color=self.colors["text"]).grid(row=0, column=0, padx=20, pady=(16, 6), sticky="w")
        source_hint = ctk.CTkLabel(source_card, text="先选择长音频，再输入字表并匹配切分。", font=self.font_small, text_color=self.colors["muted"], justify="left")
        source_hint.grid(row=1, column=0, columnspan=2, padx=20, pady=(0, 14), sticky="ew")
        self._bind_adaptive_wrap(source_hint, source_card, reserved_width=380, min_wrap=220)

        button_row = ctk.CTkFrame(source_card, fg_color="transparent")
        button_row.grid(row=0, column=2, rowspan=2, padx=20, pady=16, sticky="e")
        self.btn_sel_source = self._make_button(button_row, "选择长音频", self.select_split_source, image=self.icons.get("audio"), width=142)
        self.btn_sel_source.pack(side=tk.LEFT, padx=(0, 10))
        self.btn_edit_segments = self._make_button(button_row, "段落编辑器", self.open_visual_splitter, tone="warning", image=self.icons.get("eye"), width=142)
        self.btn_edit_segments.pack(side=tk.LEFT)

        path_pill = ctk.CTkFrame(source_card, fg_color=self.colors["surface"], corner_radius=999, border_width=1, border_color=self.colors["border"])
        path_pill.grid(row=2, column=0, columnspan=3, sticky="ew", padx=20, pady=(0, 16))
        self.lbl_split_source = ctk.CTkLabel(path_pill, text="未选择音频文件", text_color=self.colors["muted"], font=self.font_small, anchor="w")
        self.lbl_split_source.pack(fill=tk.X, padx=14, pady=8)

        word_card = self._make_card(content)
        word_card.grid(row=1, column=0, sticky="nsew", padx=(0, 16))
        word_card.grid_rowconfigure(1, weight=1)
        word_card.grid_columnconfigure(0, weight=1)

        word_header = ctk.CTkFrame(word_card, fg_color="transparent")
        word_header.grid(row=0, column=0, sticky="ew", padx=20, pady=(18, 12))
        word_header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(word_header, text="字表文本", font=self.font_title, text_color=self.colors["text"]).grid(row=0, column=0, sticky="w")
        word_hint = ctk.CTkLabel(word_header, text="支持空格、逗号、顿号、换行分隔；【组别】和 # 开头行会被跳过。", font=self.font_small, text_color=self.colors["muted"], justify="left")
        word_hint.grid(row=1, column=0, sticky="ew", pady=(4, 0))
        self._bind_adaptive_wrap(word_hint, word_header, reserved_width=180, min_wrap=220)
        self._make_button(word_header, "导入字表", self.import_wordlist, tone="primary", image=self.icons.get("import_white"), width=126).grid(row=0, column=1, rowspan=2, sticky="e")

        self.txt_wordlist = ctk.CTkTextbox(
            word_card,
            corner_radius=14,
            border_width=1,
            border_color=self.colors["border"],
            fg_color=self.colors["surface"],
            text_color=self.colors["text"],
            font=ctk.CTkFont(family=self.font_family, size=14),
        )
        self.txt_wordlist.grid(row=1, column=0, sticky="nsew", padx=20, pady=(0, 10))
        self.txt_wordlist.bind("<KeyRelease>", self.validate_wordlist)
        self.txt_wordlist.bind("<<Paste>>", lambda e: self.after(10, self.validate_wordlist))

        self.lbl_wordlist_status = ctk.CTkLabel(word_card, text="字表为空", font=self.font_small, text_color=self.colors["muted"])
        self.lbl_wordlist_status.grid(row=2, column=0, sticky="w", padx=22, pady=(0, 16))

        right_panel = self._make_card(content, fg_color=self.colors["surface_soft"], width=310)
        right_panel.grid(row=0, column=1, rowspan=2, sticky="ns")
        right_panel.grid_propagate(False)
        self._section_header(right_panel, "拆分设置", "自动检测有效发音段，也可以在段落编辑器里人工微调。", icon_text="02")

        settings = ctk.CTkFrame(right_panel, fg_color="transparent")
        settings.pack(fill=tk.X, padx=20, pady=(4, 8))

        switch_box = ctk.CTkFrame(settings, fg_color=self.colors["surface"], corner_radius=14, border_width=1, border_color=self.colors["border"])
        switch_box.pack(fill=tk.X, pady=(0, 14))
        self.var_trim = ctk.BooleanVar(value=True)
        switch_trim = ctk.CTkSwitch(
            switch_box,
            text="智能剔除边缘空白杂音",
            variable=self.var_trim,
            font=self.font_main,
            progress_color=self.colors["success"],
            button_color="#475569",
            button_hover_color="#334155",
        )
        switch_trim.pack(anchor="w", padx=14, pady=12)

        self.var_buffer = ctk.StringVar(value="0.1")
        self._make_labeled_entry(settings, "保存区段首尾缓冲", self.var_buffer, width=110, suffix="秒")



        actions = ctk.CTkFrame(right_panel, fg_color="transparent")
        actions.pack(side=tk.BOTTOM, fill=tk.X, padx=20, pady=20)
        self._make_button(actions, "匹配字表", self.match_segments_to_wordlist, tone="warning", image=self.icons.get("check")).pack(fill=tk.X, pady=(0, 10))
        self._make_button(actions, "保存到目录", lambda: self.process_split(send_to_main=False), tone="success", image=self.icons.get("save")).pack(fill=tk.X)

    # ==========================
    # 交互回调与工具函数
    # ==========================
    
    def on_files_dropped(self, files):
        paths = [self._decode_drop_path(f) for f in files]
        self.after(0, lambda p=paths: self._handle_dropped_files(p))

    def _handle_dropped_files(self, paths):
        if not paths:
            return

        # Check if there is any .teproj file dropped
        teproj_files = [p for p in paths if p.lower().endswith(('.teproj', '.zip'))]
        if teproj_files:
            self.tabview.set(self.tab_project_name)
            self.load_project_file(teproj_files[0])
            return

        audio_paths = [p for p in paths if p.lower().endswith(('.wav', '.mp3'))]
        if not audio_paths:
            return

        current_tab = self.tabview.get()
        if current_tab == self.tab_merge_name:
            added = False
            for p in audio_paths:
                if p not in self.merge_files:
                    self.merge_files.append(p)
                    self._insert_merge_file(p)
                    added = True
            if added:
                self.sync_merge_files_from_tree()
        else:
            self.split_source = audio_paths[0]
            self.lbl_split_source.configure(text=self._short_path(audio_paths[0], max_parts=3), text_color=self.colors["text_soft"])
            self.custom_segments = None  # 更换文件时重置编辑数据

    def add_merge_files(self):
        files = filedialog.askopenfilenames(filetypes=[("Audio Files", "*.wav *.mp3")])
        for f in files:
            if f not in self.merge_files:
                self.merge_files.append(f)
                self._insert_merge_file(f)
        self.sync_merge_files_from_tree()

    def remove_merge_file(self, event=None):
        selected = self.tree_merge.selection()
        for item in selected:
            values = self.tree_merge.item(item, 'values')
            if values:
                full_path = values[0]
                if full_path in self.merge_files:
                    self.merge_files.remove(full_path)
            self.tree_merge.delete(item)
        self.sync_merge_files_from_tree()
        self._refresh_merge_ui()

    def clear_merge_list(self):
        self.merge_files.clear()
        self.tree_merge.delete(*self.tree_merge.get_children())
        self._refresh_merge_ui()

    def on_tree_drag_start(self, event):
        item = self.tree_merge.identify_row(event.y)
        if item:
            self._dragged_item = item
            self.tree_merge.selection_set(item)

    def on_tree_drag_motion(self, event):
        item = self.tree_merge.identify_row(event.y)
        if item:
            bbox = self.tree_merge.bbox(item)
            if bbox:
                mid_y = bbox[1] + bbox[3] // 2
                # x 从 0 开始，宽度为 Treeview 宽度
                x = 0
                w = self.tree_merge.winfo_width()
                if event.y < mid_y:
                    y = bbox[1]
                else:
                    y = bbox[1] + bbox[3]
                self._drop_indicator.place(x=x, y=y, width=w)
        else:
            self._drop_indicator.place_forget()

    def on_tree_drag_drop(self, event):
        self._drop_indicator.place_forget()
        if hasattr(self, '_dragged_item'):
            target = self.tree_merge.identify_row(event.y)
            if target and target != self._dragged_item:
                bbox = self.tree_merge.bbox(target)
                if bbox:
                    mid_y = bbox[1] + bbox[3] // 2
                    if event.y < mid_y:
                        self.tree_merge.move(self._dragged_item, '', self.tree_merge.index(target))
                    else:
                        self.tree_merge.move(self._dragged_item, '', self.tree_merge.index(target) + 1)
                
                self.sync_merge_files_from_tree()
            del self._dragged_item

    def sync_merge_files_from_tree(self):
        """当用户通过拖拽改变顺序后，同步后台的 merge_files 列表"""
        new_list = []
        for child in self.tree_merge.get_children():
            path = self.tree_merge.item(child, 'values')[0]
            new_list.append(path)
        self.merge_files = new_list

    def import_wordlist_for_sort(self):
        if not self.merge_files:
            return messagebox.showwarning("提示", "合并列表为空，请先添加音频文件")

        path = filedialog.askopenfilename(filetypes=[("Text Files", "*.txt"), ("All Files", "*.*")])
        if not path:
            return

        try:
            with open(path, 'r', encoding='utf-8') as f:
                text = f.read()
        except UnicodeDecodeError:
            try:
                with open(path, 'r', encoding='gbk') as f:
                    text = f.read()
            except Exception:
                return messagebox.showerror("错误", "读取文件失败")
        except Exception:
            return messagebox.showerror("错误", "读取文件失败")

        flat_words = parse_wordlist(text)
        if not flat_words:
            return messagebox.showwarning("提示", "字表解析结果为空")

        # 模糊匹配排序
        sorted_paths = []
        available_paths = list(self.merge_files)
        used_indices = set()

        for word in flat_words:
            idx = fuzzy_match_word_to_path(word, available_paths, used_indices=list(used_indices))
            if idx is not None:
                sorted_paths.append(available_paths[idx])
                used_indices.add(idx)

        # 将未匹配到的文件追加到末尾
        for i, p in enumerate(available_paths):
            if i not in used_indices:
                sorted_paths.append(p)

        # 更新 UI
        self.merge_files = sorted_paths
        self.tree_merge.delete(*self.tree_merge.get_children())
        for p in self.merge_files:
            self._insert_merge_file(p)

        messagebox.showinfo("排序完成", f"已根据字表重新排序 {len(used_indices)} 个文件。")

    def select_split_source(self):
        f = filedialog.askopenfilename(filetypes=[("Audio Files", "*.wav *.mp3")])
        if f:
            self.split_source = f
            self.lbl_split_source.configure(text=self._short_path(f, max_parts=3), text_color=self.colors["text_soft"])
            self.custom_segments = None

    def import_wordlist(self):
        path = filedialog.askopenfilename(filetypes=[("Text/CSV Files", "*.txt *.csv"), ("All Files", "*.*")])
        if not path: return
        try:
            with open(path, 'r', encoding='utf-8') as f:
                text = f.read()
        except UnicodeDecodeError:
            try:
                with open(path, 'r', encoding='gbk') as f:
                    text = f.read()
            except Exception as e:
                return messagebox.showerror("错误", f"读取文件失败: {e}")
        self.txt_wordlist.delete("1.0", tk.END)
        self.txt_wordlist.insert("1.0", text)
        self.validate_wordlist()

    def validate_wordlist(self, event=None):
        text = self.txt_wordlist.get("1.0", tk.END).strip()
        self.wordlist = parse_wordlist(text)
        count = len(self.wordlist)
        if count > 0:
            self.lbl_wordlist_status.configure(text=f"已加载 {count} 个词汇", text_color="#10B981")
        else:
            self.lbl_wordlist_status.configure(text="字表为空", text_color="#6B7280")

    def match_segments_to_wordlist(self):
        if not self.split_source:
            return messagebox.showwarning("提示", "请先选择长音频源文件")
        
        self.validate_wordlist()
        if not self.wordlist:
            return messagebox.showwarning("提示", "字表为空，请先输入或导入字表")

        def run():
            self.set_loading(True, "正在进行 VAD 检测与匹配...")
            try:
                snd = parselmouth.Sound(self.split_source)
                vad_segs = macroscopic_vad(snd)
                self.custom_segments = vad_segs
                
                msg = f"匹配完成！\n检测到音频段落: {len(vad_segs)} 个\n字表词汇: {len(self.wordlist)} 个"
                if len(vad_segs) != len(self.wordlist):
                    msg += "\n\n注意：数量不一致，可能需要手动调整。"
                
                self.after(0, lambda: messagebox.showinfo("匹配结果", msg))
            except Exception as e:
                self.after(0, lambda: messagebox.showerror("错误", f"匹配失败: {e}"))
            finally:
                self.after(0, lambda: self.set_loading(False))
        
        start_safe_thread(run)

    def set_loading(self, state, msg=""):
        if getattr(self, "_ui_thread_id", None) is not None and threading.get_ident() != self._ui_thread_id:
            self.after(0, lambda: self.set_loading(state, msg))
            return

        if state:
            self.lbl_status.configure(text=msg or "处理中...", text_color="#1D4ED8")
            self.progress.grid()
            self.progress.set(0.06)
            self.update_idletasks()
        else:
            self.lbl_status.configure(text="就绪" if not msg else msg, text_color="#047857")
            self.progress.set(0)
            self.progress.grid_remove()

    def update_progress(self, val):
        self.after(0, lambda: self.progress.set(val))

    def _send_files_to_main_app(self, files):
        target = "main.py"
        if not os.path.exists(target):
            parent_main = os.path.join("..", "main.py")
            if os.path.exists(parent_main): target = parent_main
            else: target = None
            
        if target:
            subprocess.Popen([sys.executable, target] + files)
        else:
            messagebox.showinfo("提示", "拆分已完成，但未能在同目录下找到主程序 main.py，请手动将其拖拽入 PhonTracer。")

    # ==========================
    # 可视化编辑器调用
    # ==========================
    
    def open_visual_splitter(self):
        if not self.split_source:
            return messagebox.showwarning("提示", "请先选择长音频源文件")
        
        def run():
            self.set_loading(True, "正在加载并检测音频区段...")
            try:
                snd = parselmouth.Sound(self.split_source)
                
                if self.custom_segments:
                    existing_items = [{'id': i, 'label': f'#{i+1}', 'start': s, 'end': e} for i, (s, e) in enumerate(self.custom_segments)]
                    self.after(0, lambda: VisualSplitter(self, snd, {}, self.on_visual_split_confirm, existing_items=existing_items, wordlist=self.wordlist))
                else:
                    vad_segs = macroscopic_vad(snd)
                    self.after(0, lambda: VisualSplitter(self, snd, {}, self.on_visual_split_confirm, vad_segments=vad_segs, wordlist=self.wordlist))
            except Exception as e:
                self.after(0, lambda: messagebox.showerror("错误", f"加载失败: {e}"))
            finally:
                self.after(0, lambda: self.set_loading(False))
                
        start_safe_thread(run)

    def on_visual_split_confirm(self, segments, is_update=False, deleted_count=0):
        parsed_segs = []
        for seg in segments:
            if isinstance(seg, tuple):
                parsed_segs.append(seg)
            else:
                parsed_segs.append((seg['start'], seg['end']))
        self.custom_segments = parsed_segs
        messagebox.showinfo("提示", f"已保存 {len(parsed_segs)} 个自定义切分段！\n请确保左侧字表数量与切分段数量一致。")

    # ==========================
    # 核心任务：合并
    # ==========================
    
    def process_merge(self):
        if not self.merge_files: return messagebox.showwarning("提示", "请先添加音频文件")
        try: gap_sec = float(self.var_gap.get())
        except ValueError: return messagebox.showwarning("提示", "间隔时间必须为数字")
        
        out_path = filedialog.asksaveasfilename(defaultextension=".wav", filetypes=[("WAV Audio", "*.wav")], title="保存合并后的音频")
        if not out_path: return
        
        def run():
            self.set_loading(True, "正在合并音频...")
            try:
                target_sr = 44100
                all_vals = []
                gap_samples = int(target_sr * gap_sec)
                gap_array = np.zeros(gap_samples)
                
                total = len(self.merge_files)
                for i, path in enumerate(self.merge_files):
                    snd = parselmouth.Sound(path)
                    if snd.sampling_frequency != target_sr:
                        snd = snd.resample(target_sr)
                    all_vals.append(snd.values[0])
                    all_vals.append(gap_array)
                    self.update_progress((i+1)/total)
                    
                if all_vals:
                    merged_vals = np.concatenate(all_vals[:-1])
                    merged_snd = parselmouth.Sound(np.array([merged_vals]), sampling_frequency=target_sr)
                    merged_snd.save(out_path, "WAV")
                    
                self.after(0, lambda: messagebox.showinfo("成功", f"合并完成！\n保存在: {out_path}"))
            except Exception as e:
                self.after(0, lambda: messagebox.showerror("错误", f"合并失败:\n{str(e)}"))
            finally:
                self.after(0, lambda: self.set_loading(False))
                
        start_safe_thread(run)

    # ==========================
    # 核心任务：拆分
    # ==========================
    
    def process_split(self, send_to_main=False):
        if not self.split_source: return messagebox.showwarning("提示", "请选择长音频源文件")
        
        raw_text = self.txt_wordlist.get("1.0", tk.END)
        wordlist = parse_wordlist(raw_text)
        if not wordlist: return messagebox.showwarning("提示", "字表为空，请粘贴字表。")
        
        try: buffer_sec = float(self.var_buffer.get())
        except ValueError: return messagebox.showwarning("提示", "缓冲时间必须为数字")
        
        out_dir = filedialog.askdirectory(title="选择拆分后音频的保存文件夹")
        if not out_dir: return
        
        do_trim = self.var_trim.get()
        
        def run():
            self.set_loading(True, "正在分析并拆分音频...")
            try:
                snd = parselmouth.Sound(self.split_source)
                
                # 优先使用可视化编辑器微调后的边界，否则跑自动 VAD
                if self.custom_segments:
                    segs = self.custom_segments
                else:
                    segs = macroscopic_vad(snd)
                
                if not segs:
                    self.after(0, lambda: messagebox.showwarning("警告", "未能在音频中检测到任何有效发音段！"))
                    return
                
                if len(segs) != len(wordlist):
                    pass # 容错机制：数量不匹配时，只提取匹配的最小部分
                
                total = min(len(segs), len(wordlist))
                saved_files = []
                
                for i in range(total):
                    s, e = segs[i]
                    word = wordlist[i]
                    
                    if do_trim:
                        part = snd.extract_part(from_time=s, to_time=e)
                        vals = part.values[0]
                        xs = part.xs()
                        threshold = 10 ** (-50 / 20)
                        valid_idx = np.where(np.abs(vals) > threshold)[0]
                        if len(valid_idx) > 0:
                            s = s + xs[valid_idx[0]]
                            e = s + xs[valid_idx[-1]]
                            
                    s = max(0, s - buffer_sec)
                    e = min(snd.get_total_duration(), e + buffer_sec)
                    
                    if e > s:
                        extract = snd.extract_part(from_time=s, to_time=e)
                        safe_word = re.sub(r'[\\/*?:"<>|]', "", word)
                        out_file = os.path.join(out_dir, f"{str(i+1).zfill(3)}_{safe_word}.wav")
                        extract.save(out_file, "WAV")
                        saved_files.append(out_file)
                        
                    self.update_progress((i+1)/total)
                
                if send_to_main and saved_files:
                    self.after(0, lambda: self._send_files_to_main_app(saved_files))
                else:
                    self.after(0, lambda: messagebox.showinfo("成功", f"成功拆分 {total} 段音频并保存到:\n{out_dir}"))
                    
            except Exception as e:
                self.after(0, lambda: messagebox.showerror("错误", f"拆分失败:\n{str(e)}"))
            finally:
                self.after(0, lambda: self.set_loading(False))
                
        start_safe_thread(run)

    def build_project_tab(self):
        content = ctk.CTkFrame(self.tab_project, fg_color="transparent")
        content.pack(fill=tk.BOTH, expand=True, padx=20, pady=18)
        content.grid_columnconfigure(0, weight=0)
        content.grid_columnconfigure(1, weight=1)
        content.grid_rowconfigure(0, weight=1)

        left_panel = self._make_card(content, fg_color=self.colors["surface_soft"], width=310)
        left_panel.grid(row=0, column=0, sticky="ns", padx=(0, 16))
        left_panel.grid_propagate(False)
        self._section_header(left_panel, "工程操作", "打开 .teproj 后可预览结构，也可另存为 zip。", icon_text="03")

        actions = ctk.CTkFrame(left_panel, fg_color="transparent")
        actions.pack(fill=tk.X, padx=20, pady=(4, 18))
        self.btn_open_project = self._make_button(
            actions,
            "选择工程文件",
            self.select_project_file,
            tone="primary",
            image=self.icons.get("import_white"),
        )
        self.btn_open_project.pack(fill=tk.X, pady=(0, 10))

        self.btn_convert_zip = self._make_button(
            actions,
            "转换为 ZIP 压缩包",
            self.convert_project_to_zip,
            tone="purple",
            image=self.icons.get("tab_batch"),
        )
        self.btn_convert_zip.pack(fill=tk.X)
        self.btn_convert_zip.configure(state="disabled")  # Disabled until a project is loaded

        info_box = ctk.CTkFrame(left_panel, fg_color=self.colors["surface"], corner_radius=14, border_width=1, border_color=self.colors["border"])
        info_box.pack(fill=tk.X, padx=20, pady=(0, 18))
        ctk.CTkLabel(info_box, text="文件信息", font=ctk.CTkFont(family=self.font_family, size=13, weight="bold"), text_color=self.colors["text"]).pack(anchor="w", padx=14, pady=(12, 4))
        self.lbl_proj_file = ctk.CTkLabel(
            info_box,
            text="未加载任何工程文件",
            font=self.font_small,
            text_color=self.colors["muted"],
            wraplength=248,
            justify="left",
        )
        self.lbl_proj_file.pack(fill=tk.X, padx=14, pady=(0, 14), anchor="w")



        right_panel = self._make_card(content)
        right_panel.grid(row=0, column=1, sticky="nsew")
        right_panel.grid_rowconfigure(1, weight=1)
        right_panel.grid_columnconfigure(0, weight=1)

        header = ctk.CTkFrame(right_panel, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=20, pady=(18, 12))
        ctk.CTkLabel(header, text="工程内容预览", font=self.font_title, text_color=self.colors["text"]).pack(anchor="w")
        ctk.CTkLabel(header, text="读取 project.json、发音人、条目、音频资源和缓存数据。", font=self.font_small, text_color=self.colors["muted"]).pack(anchor="w", pady=(4, 0))

        self.preview_container = ctk.CTkFrame(right_panel, fg_color="transparent")
        self.preview_container.grid(row=1, column=0, sticky="nsew", padx=20, pady=(0, 20))
        self.show_placeholder()

    def select_project_file(self):
        path = filedialog.askopenfilename(filetypes=[("PhonTracer Project", "*.teproj")])
        if path:
            self.load_project_file(path)

    def clear_preview_container(self):
        for widget in self.preview_container.winfo_children():
            widget.destroy()

    def show_placeholder(self):
        self.clear_preview_container()
        card = ctk.CTkFrame(self.preview_container, fg_color=self.colors["surface_soft"], corner_radius=16, border_width=1, border_color=self.colors["border"])
        card.pack(fill=tk.BOTH, expand=True)

        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.place(relx=0.5, rely=0.5, anchor="center")

        ctk.CTkLabel(inner, text="▣", font=ctk.CTkFont(family=self.font_family, size=54, weight="bold"), text_color=self.colors["muted_soft"]).pack(pady=(0, 12))
        ctk.CTkLabel(inner, text="暂无工程文件数据", font=ctk.CTkFont(family=self.font_family, size=20, weight="bold"), text_color=self.colors["text"]).pack(pady=4)
        ctk.CTkLabel(inner, text="请在左侧点击“选择工程文件”并打开 .teproj。", font=self.font_small, text_color=self.colors["muted"], justify="center").pack(pady=4)

    def show_loading_placeholder(self):
        self.clear_preview_container()
        card = ctk.CTkFrame(self.preview_container, fg_color=self.colors["surface_soft"], corner_radius=16, border_width=1, border_color=self.colors["border"])
        card.pack(fill=tk.BOTH, expand=True)

        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.place(relx=0.5, rely=0.5, anchor="center")

        ctk.CTkLabel(inner, text="正在读取工程数据...", font=ctk.CTkFont(family=self.font_family, size=18, weight="bold"), text_color=self.colors["text_soft"]).pack(pady=(0, 8))
        ctk.CTkLabel(inner, text="如果工程包含大量音频资源，预览生成可能需要几秒。", font=self.font_small, text_color=self.colors["muted"]).pack()

    def show_error_placeholder(self, error_msg):
        self.clear_preview_container()
        card = ctk.CTkFrame(self.preview_container, fg_color="#FFF7F7", corner_radius=16, border_width=1, border_color="#FCA5A5")
        card.pack(fill=tk.BOTH, expand=True)

        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.place(relx=0.5, rely=0.5, anchor="center")

        ctk.CTkLabel(inner, text="无法解析工程文件", font=ctk.CTkFont(family=self.font_family, size=20, weight="bold"), text_color=self.colors["danger"]).pack(pady=(0, 8))
        ctk.CTkLabel(inner, text="请确认它是有效的 PhonTracer .teproj 文件。", font=self.font_small, text_color="#991B1B").pack(pady=(0, 10))

        err_box = ctk.CTkTextbox(inner, width=460, height=120, corner_radius=12, border_width=1, border_color="#FCA5A5", fg_color="#FEF2F2", text_color="#991B1B")
        err_box.pack(pady=8)
        err_box.insert("1.0", error_msg)
        err_box.configure(state="disabled")

    def create_detail_row(self, parent, row, label_text, val_text):
        lbl = ctk.CTkLabel(parent, text=label_text, font=ctk.CTkFont(family=self.font_family, size=12, weight="bold"), text_color=self.colors["muted"])
        lbl.grid(row=row, column=0, sticky="w", padx=10, pady=3)
        val = ctk.CTkLabel(parent, text=val_text, font=ctk.CTkFont(family="Consolas", size=12), text_color=self.colors["text"])
        val.grid(row=row, column=1, sticky="w", padx=5, pady=3)

    def display_project_preview(self, project_data, namelist):
        self.clear_preview_container()
        
        # Configure a custom smaller style for the project preview treeview
        style = ttk.Style()
        style.configure("Proj.Treeview", 
                        font=("Microsoft YaHei", 10), 
                        rowheight=26,
                        background="#FFFFFF",
                        fieldbackground="#FFFFFF",
                        foreground="#1F2937",
                        borderwidth=0,
                        relief="flat")
        style.configure("Proj.Treeview.Heading", 
                        font=("Microsoft YaHei", 10, "bold"),
                        background="#F3F4F6",
                        foreground="#374151")
        style.map("Proj.Treeview", background=[('selected', '#3B82F6')], foreground=[('selected', '#FFFFFF')])

        # Main scrollable content frame inside self.preview_container
        scroll_content = ctk.CTkScrollableFrame(self.preview_container, fg_color="transparent")
        scroll_content.pack(fill=tk.BOTH, expand=True)
        try:
            scroll_content._parent_canvas.configure(yscrollincrement=8)
        except Exception:
            pass
        
        # 1. Summary Card
        summary_frame = ctk.CTkFrame(scroll_content, fg_color="#FFFFFF", corner_radius=12, border_width=1, border_color="#E5E7EB")
        summary_frame.pack(fill=tk.X, padx=5, pady=5)
        
        ctk.CTkLabel(summary_frame, text="📊 工程概览与资源统计", font=ctk.CTkFont(family="Microsoft YaHei", size=14, weight="bold"), text_color="#1E3A8A").pack(anchor="w", padx=15, pady=(10, 5))
        
        sub_grid = ctk.CTkFrame(summary_frame, fg_color="transparent")
        sub_grid.pack(fill=tk.X, padx=5, pady=(0, 10))
        sub_grid.columnconfigure(0, weight=1)
        sub_grid.columnconfigure(1, weight=1)
        
        # Left summary col (Meta)
        meta_frame = ctk.CTkFrame(sub_grid, fg_color="transparent")
        meta_frame.grid(row=0, column=0, sticky="nsew", padx=5, pady=5)
        ctk.CTkLabel(meta_frame, text="【基本信息】", font=ctk.CTkFont(family="Microsoft YaHei", size=12, weight="bold"), text_color="#374151").grid(row=0, column=0, columnspan=2, sticky="w", padx=10, pady=5)
        
        version = project_data.get("version", "未知")
        speakers = project_data.get("speakers", {})
        active_speaker_id = project_data.get("active_speaker_id", "无")
        trunc_active_id = (active_speaker_id[:12] + "...") if len(active_speaker_id) > 15 else active_speaker_id
        
        self.create_detail_row(meta_frame, 1, "工程格式版本:", version)
        self.create_detail_row(meta_frame, 2, "发音人数量:", str(len(speakers)))
        self.create_detail_row(meta_frame, 3, "默认选中发音人 ID:", trunc_active_id)
        
        # Right summary col (Files)
        files_frame = ctk.CTkFrame(sub_grid, fg_color="transparent")
        files_frame.grid(row=0, column=1, sticky="nsew", padx=5, pady=5)
        ctk.CTkLabel(files_frame, text="【物理文件统计】", font=ctk.CTkFont(family="Microsoft YaHei", size=12, weight="bold"), text_color="#374151").grid(row=0, column=0, columnspan=2, sticky="w", padx=10, pady=5)
        
        audio_files = [f for f in namelist if f.startswith("audio/")]
        data_files = [f for f in namelist if f.startswith("data/")]
        
        self.create_detail_row(files_frame, 1, "压缩包内文件总数:", str(len(namelist)))
        self.create_detail_row(files_frame, 2, "音频文件数 (audio/):", str(len(audio_files)))
        self.create_detail_row(files_frame, 3, "缓存数据数 (data/):", str(len(data_files)))
        
        # 2. Speakers Detail Section
        ctk.CTkLabel(scroll_content, text="👥 发音人及数据分析明细", font=ctk.CTkFont(family="Microsoft YaHei", size=14, weight="bold"), text_color="#1F2937").pack(anchor="w", pady=(15, 5), padx=5)
        
        if not speakers:
            no_spk_card = ctk.CTkFrame(scroll_content, fg_color="#FFFFFF", corner_radius=12, border_width=1, border_color="#E5E7EB")
            no_spk_card.pack(fill=tk.X, padx=5, pady=5)
            ctk.CTkLabel(no_spk_card, text="暂无发音人数据", font=self.font_main, text_color="#6B7280").pack(pady=20)
            return

        spk_tabview = ctk.CTkTabview(scroll_content, corner_radius=12, border_width=1, border_color="#E5E7EB", fg_color="#FFFFFF")
        spk_tabview.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        for s_id, spk in speakers.items():
            name = spk.get("name", "未命名")
            tab_title = name
            if s_id == active_speaker_id:
                tab_title += " (当前选中)"
            
            spk_tabview.add(tab_title)
            tab_frame = spk_tabview.tab(tab_title)
            
            # Sub layout within each speaker tab
            top_row = ctk.CTkFrame(tab_frame, fg_color="transparent")
            top_row.pack(fill=tk.X, anchor="n", pady=5)
            
            info_frame = ctk.CTkFrame(top_row, fg_color="#F3F4F6", corner_radius=8)
            info_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 5), pady=5)
            
            ctk.CTkLabel(info_frame, text="发音人与音频", font=ctk.CTkFont(family="Microsoft YaHei", size=12, weight="bold"), text_color="#111827").grid(row=0, column=0, columnspan=2, sticky="w", padx=10, pady=5)
            tab_mode = spk.get("tab_mode", "未知模式")
            self.create_detail_row(info_frame, 1, "唯一标识符 (ID):", s_id)
            self.create_detail_row(info_frame, 2, "音频管理模式:", tab_mode)
            
            if tab_mode == "单条长音频":
                long_audio_path = spk.get("long_audio_path", "")
                mac_segs = spk.get("current_macro_segments", [])
                man_segs = spk.get("manual_segments", [])
                self.create_detail_row(info_frame, 3, "长音频文件:", os.path.basename(long_audio_path) if long_audio_path else "无")
                self.create_detail_row(info_frame, 4, "自动分段 / 手动微调:", f"{len(mac_segs)} 段 / {len(man_segs) if man_segs is not None else '未进行微调'}")
            else:
                pending_batch_paths = spk.get("pending_batch_paths", [])
                self.create_detail_row(info_frame, 3, "待导入音频数量:", str(len(pending_batch_paths)))
            
            # Engine params frame
            param_frame = ctk.CTkFrame(top_row, fg_color="#F3F4F6", corner_radius=8)
            param_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=(5, 0), pady=5)
            
            ctk.CTkLabel(param_frame, text="分析引擎参数", font=ctk.CTkFont(family="Microsoft YaHei", size=12, weight="bold"), text_color="#111827").grid(row=0, column=0, columnspan=2, sticky="w", padx=10, pady=5)
            last_params = spk.get("last_params", {})
            if last_params:
                self.create_detail_row(param_frame, 1, "基频范围 (F0 Range):", f"{last_params.get('f0_min', 75)} Hz ~ {last_params.get('f0_max', 600)} Hz")
                self.create_detail_row(param_frame, 2, "时序分析点数 (Pts):", str(last_params.get('pts', 11)))
                self.create_detail_row(param_frame, 3, "分析算法:", last_params.get('method', 'ac'))
            else:
                ctk.CTkLabel(param_frame, text="无已存引擎参数", font=self.font_main, text_color="#6B7280").grid(row=1, column=0, columnspan=2, sticky="w", padx=10, pady=5)
            
            # Bottom row: Word items Treeview
            items = spk.get("items", {})
            
            table_frame = ctk.CTkFrame(tab_frame, fg_color="transparent")
            table_frame.pack(fill=tk.BOTH, expand=True, pady=(10, 5))
            
            ctk.CTkLabel(table_frame, text=f"📋 解析出的字词条目 (共 {len(items)} 条)", font=ctk.CTkFont(family="Microsoft YaHei", size=13, weight="bold"), text_color="#374151").pack(anchor="w", pady=(0, 5))
            
            if not items:
                no_items_card = ctk.CTkFrame(table_frame, fg_color="#F3F4F6", corner_radius=8)
                no_items_card.pack(fill=tk.X, pady=5)
                ctk.CTkLabel(no_items_card, text="暂无提取的字词数据", font=self.font_main, text_color="#6B7280").pack(pady=15)
            else:
                tree_container = ctk.CTkFrame(table_frame, fg_color="transparent")
                tree_container.pack(fill=tk.BOTH, expand=True)
                
                cols = ("idx", "word", "time", "cache")
                tree = ttk.Treeview(tree_container, columns=cols, show="headings", height=8)
                
                tree.heading("idx", text="序号")
                tree.heading("word", text="音节/字词")
                tree.heading("time", text="时间区间 (s)")
                tree.heading("cache", text="F0 缓存状态")
                
                tree.column("idx", width=60, anchor="center")
                tree.column("word", width=120, anchor="center")
                tree.column("time", width=220, anchor="center")
                tree.column("cache", width=120, anchor="center")
                
                tree.configure(style="Proj.Treeview", takefocus=False)
                
                scrollbar = ctk.CTkScrollbar(tree_container, orientation="vertical", command=tree.yview)
                tree.configure(yscrollcommand=scrollbar.set)
                
                tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
                scrollbar.pack(side=tk.RIGHT, fill=tk.Y, padx=1, pady=1)
                
                # 滚动隔离：当鼠标悬浮在列表容器（包括列表和滚动条）上时，禁用父 scroll_content 的滚动绑定，允许 Treeview 完全原生滚动；离开时重新启用。
                def on_enter_tree(e):
                    method = getattr(scroll_content, "_disable_all_bindings", None)
                    if callable(method):
                        try:
                            method()
                        except Exception:
                            pass
                
                def on_leave_tree(e):
                    x = tree_container.winfo_pointerx() - tree_container.winfo_rootx()
                    y = tree_container.winfo_pointery() - tree_container.winfo_rooty()
                    w = tree_container.winfo_width()
                    h = tree_container.winfo_height()
                    if not (0 <= x < w and 0 <= y < h):
                        method = getattr(scroll_content, "_enable_all_bindings", None)
                        if callable(method):
                            try:
                                method()
                            except Exception:
                                pass
                
                tree_container.bind("<Enter>", on_enter_tree, add="+")
                tree_container.bind("<Leave>", on_leave_tree, add="+")
                tree.bind("<Enter>", on_enter_tree, add="+")
                scrollbar.bind("<Enter>", on_enter_tree, add="+")
                
                for idx, (item_id, item) in enumerate(items.items(), 1):
                    label = item.get("label", "无")
                    start = item.get("start", 0.0)
                    end = item.get("end", 0.0)
                    time_str = f"{start:.3f}s ~ {end:.3f}s"
                    
                    pitch_file = item.get("pitch_data_file", "")
                    has_pitch = "无"
                    if pitch_file and pitch_file in namelist:
                        has_pitch = "已缓存"
                    elif item.get("pitch_data") is not None:
                        has_pitch = "已缓存"
                        
                    tree.insert("", tk.END, values=(idx, label, time_str, has_pitch))

    def load_project_file(self, path):
        if not os.path.exists(path):
            messagebox.showerror("错误", "工程文件不存在。")
            return
            
        self.loaded_teproj_path = path
        self.lbl_proj_file.configure(
            text=f"已加载工程:\n{os.path.basename(path)}\n\n大小: {os.path.getsize(path) / (1024 * 1024):.2f} MB",
            text_color=self.colors["success"]
        )
        
        self.show_loading_placeholder()
        try:
            if not zipfile.is_zipfile(path):
                raise ValueError("所选文件不是有效的 ZIP 格式压缩包")

            with zipfile.ZipFile(path, 'r') as zf:
                namelist = zf.namelist()
                if "project.json" not in namelist:
                    raise ValueError("未在压缩包中找到 project.json，这可能不是一个合法的 PhonTracer 工程文件")

                with zf.open("project.json") as f:
                    project_data = json.loads(f.read().decode('utf-8'))

            self.display_project_preview(project_data, namelist)
            self.btn_convert_zip.configure(state="normal")

        except Exception as e:
            import traceback
            traceback.print_exc()
            err_msg = f"❌ 无法解析工程文件: {str(e)}"
            self.show_error_placeholder(err_msg)
            self.btn_convert_zip.configure(state="disabled")
            self.lbl_proj_file.configure(text="解析失败", text_color=self.colors["danger"])
            messagebox.showerror("错误", f"解析 .teproj 文件失败:\n{str(e)}")

    def format_project_preview(self, project_data, namelist):
        def pad_chinese(text, width):
            text_len = len(text)
            chinese_len = sum(1 for char in text if ord(char) > 127)
            padding = width - text_len - chinese_len
            if padding <= 0:
                return text
            return text + " " * padding

        lines = []
        lines.append("=" * 60)
        lines.append("               PHONTRACER 工程文件 (.teproj) 数据预览")
        lines.append("=" * 60)
        lines.append("")
        
        # 1. Basic Info
        version = project_data.get("version", "未知")
        active_speaker_id = project_data.get("active_speaker_id", "无")
        speakers = project_data.get("speakers", {})
        
        lines.append("【基本信息】")
        lines.append(f"  • 工程格式版本: {version}")
        lines.append(f"  • 发音人数量: {len(speakers)}")
        lines.append("")
        
        # 2. Speakers & Detailed structure
        lines.append("【发音人及音频明细】")
        for s_id, spk in speakers.items():
            name = spk.get("name", "未命名")
            tab_mode = spk.get("tab_mode", "未知模式")
            last_params = spk.get("last_params", {})
            items = spk.get("items", {})
            pending_batch_paths = spk.get("pending_batch_paths", [])
            long_audio_path = spk.get("long_audio_path", "")
            
            is_active = " (当前选中)" if s_id == active_speaker_id else ""
            lines.append(f"  ■ 发音人: {name}{is_active}")
            lines.append(f"    • 唯一标识符: {s_id}")
            lines.append(f"    • 音频管理模式: {tab_mode}")
            
            if tab_mode == "单条长音频":
                lines.append(f"    • 长音频文件: {long_audio_path or '无'}")
                mac_segs = spk.get("current_macro_segments", [])
                man_segs = spk.get("manual_segments", [])
                lines.append(f"    • 自动分段数量: {len(mac_segs)}")
                lines.append(f"    • 手动微调段数: {len(man_segs) if man_segs is not None else '未进行微调'}")
            else:
                lines.append(f"    • 待导入音频数: {len(pending_batch_paths)}")
                
            # Engine parameters
            if last_params:
                lines.append("    • 分析引擎参数配置:")
                lines.append(f"      - 基频范围 (F0 Range): {last_params.get('f0_min', 75)} Hz ~ {last_params.get('f0_max', 600)} Hz")
                lines.append(f"      - 时序分析点数 (Points): {last_params.get('pts', 11)}")
                lines.append(f"      - 分析算法: {last_params.get('method', 'ac')}")
            
            lines.append(f"    • 解析出的字词条目 (共 {len(items)} 条):")
            if not items:
                lines.append("      (暂无提取 of 字词条目数据)")
            else:
                item_header = f"      {'序号':<4} | {pad_chinese('音节/字词', 12)} | {'时间区间 (s)':<22} | {pad_chinese('F0缓存状态', 10)}"
                lines.append(item_header)
                lines.append("      " + "-" * 56)
                
                for idx, (item_id, item) in enumerate(items.items(), 1):
                    label = item.get("label", "无")
                    start = item.get("start", 0.0)
                    end = item.get("end", 0.0)
                    
                    pitch_file = item.get("pitch_data_file", "")
                    has_pitch = "无"
                    if pitch_file and pitch_file in namelist:
                        has_pitch = "已缓存"
                    elif item.get("pitch_data") is not None:
                        has_pitch = "已缓存"
                        
                    idx_str = f"{idx:<4}"
                    label_col = pad_chinese(label, 12)
                    time_col = f"{start:7.3f}s ~ {end:7.3f}s"
                    has_pitch_col = pad_chinese(has_pitch, 10)
                    
                    lines.append(f"      {idx_str} | {label_col} | {time_col} | {has_pitch_col}")
            lines.append("")
            
        # 3. Archive file list overview
        lines.append("【工程压缩包物理文件清单】")
        lines.append(f"  • 压缩包内文件总数: {len(namelist)}")
        
        audio_files = [f for f in namelist if f.startswith("audio/")]
        data_files = [f for f in namelist if f.startswith("data/")]
        
        lines.append(f"  • 音频资源文件数 (audio/): {len(audio_files)}")
        lines.append(f"  • 基频数据缓存数 (data/): {len(data_files)}")
        lines.append("")
        lines.append("=" * 60)
        
        return "\n".join(lines)

    def convert_project_to_zip(self):
        if not hasattr(self, 'loaded_teproj_path') or not self.loaded_teproj_path:
            return messagebox.showwarning("提示", "请先选择并加载 .teproj 文件")
            
        default_name = os.path.splitext(os.path.basename(self.loaded_teproj_path))[0] + ".zip"
        zip_path = filedialog.asksaveasfilename(
            defaultextension=".zip",
            initialfile=default_name,
            filetypes=[("ZIP Archive", "*.zip")],
            title="另存为 ZIP 压缩包"
        )
        if not zip_path:
            return
            
        try:
            shutil.copy2(self.loaded_teproj_path, zip_path)
            messagebox.showinfo("转换成功", f"工程已成功另存为 ZIP 压缩包：\n{zip_path}")
        except Exception as e:
            messagebox.showerror("错误", f"另存为 ZIP 失败：\n{str(e)}")

if __name__ == "__main__":
    app = AudioToolkitApp()
    app.mainloop()
