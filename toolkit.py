import os
import sys
import threading
import re
import subprocess
import platform
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
import hashlib
import datetime
import uuid
import zipfile
import json
from modules.data_utils import fuzzy_match_word_to_path
from modules.project_manager import read_project_metadata_from_archive
from modules.report_generator import get_pitch_floor, get_pitch_ceiling

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

def _fit_vad_segments_to_expected_count(segs, expected_count):
    """按停顿长度保留最可信的边界，使切分段数量尽量贴合字表。"""
    expected_count = max(1, int(expected_count))
    fitted = [list(seg) for seg in segs]
    while len(fitted) > expected_count:
        merge_idx = min(
            range(len(fitted) - 1),
            key=lambda idx: fitted[idx + 1][0] - fitted[idx][1]
        )
        fitted[merge_idx][1] = fitted[merge_idx + 1][1]
        fitted.pop(merge_idx + 1)
    return fitted


def macroscopic_vad(snd: parselmouth.Sound, min_dur=0.1, merge_thresh=0.12, expected_count=None):
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

    usable_segs = [s for s in segs if s[1] - s[0] > 0.02]
    if expected_count:
        merged = _fit_vad_segments_to_expected_count(usable_segs, expected_count)
    else:
        merged = []
        for s in usable_segs:
            if not merged or s[0] - merged[-1][1] >= merge_thresh:
                merged.append(s)
            else:
                merged[-1][1] = s[1]

    return [s for s in merged if s[1]-s[0] > min_dur]

def parse_wordlist(raw_text: str):
    """解析字表，兼容主程序的【组别】格式"""
    flat_words = []
    raw_text = raw_text.lstrip('\ufeff')
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
            self.segments = []
            for source_seg in existing_items:
                seg = dict(source_seg)
                seg['inner_splits'] = list(source_seg.get('inner_splits', []))
                self.segments.append(seg)
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
            for idx, seg in enumerate(self.segments):
                if idx in self.deleted_indices:
                    continue
                kept_segments.append({
                    'id': seg.get('dyn_id'),
                    'start': seg['start'],
                    'end': seg['end']
                })
            self.destroy()
            self.callback(kept_segments, True, len(self.deleted_indices))


class ExportReportDialog(ctk.CTkToplevel):
    def __init__(self, parent, callback):
        super().__init__(parent)
        self.parent = parent
        self.callback = callback

        self.title("导出研究方法报告与数据档案")
        self.resizable(False, False)

        # Center the dialog
        width, height = 450, 320
        screen_width = self.winfo_screenwidth()
        screen_height = self.winfo_screenheight()
        x = (screen_width - width) // 2
        y = (screen_height - height) // 2
        self.geometry(f"{width}x{height}+{x}+{y}")

        self.configure(fg_color=("#FFFFFF", "#1A1D24"))

        # Modal configuration
        self.transient(parent)
        self.grab_set()
        self.focus_set()

        # Accent strip
        accent_strip = ctk.CTkFrame(self, height=4, fg_color="#10B981", corner_radius=0)
        accent_strip.pack(fill="x", side="top")

        self.setup_ui()

    def setup_ui(self):
        # Card container
        card = ctk.CTkFrame(self, fg_color=("#FFFFFF", "#262930"), corner_radius=12, border_width=1, border_color=("#E5E7EB", "#374151"))
        card.pack(fill="both", expand=True, padx=20, pady=(15, 20))

        # Title label
        lbl_title = ctk.CTkLabel(
            card,
            text="📑 导出配置",
            font=ctk.CTkFont(family="Microsoft YaHei", size=16, weight="bold"),
            text_color=("#111827", "#F9FAFB")
        )
        lbl_title.pack(anchor="w", padx=20, pady=(15, 10))

        # Radio variable for export format selection
        self.export_format_var = ctk.StringVar(value="both")

        # Options frame
        options_frame = ctk.CTkFrame(card, fg_color="transparent")
        options_frame.pack(fill="x", padx=20, pady=5)

        # Radio buttons
        r_both = ctk.CTkRadioButton(
            options_frame,
            text="同时导出 Markdown 与 Excel (推荐)",
            variable=self.export_format_var,
            value="both",
            font=ctk.CTkFont(family="Microsoft YaHei", size=12)
        )
        r_both.pack(anchor="w", pady=6)

        r_md = ctk.CTkRadioButton(
            options_frame,
            text="仅导出 Markdown 报告 (*.md)",
            variable=self.export_format_var,
            value="md",
            font=ctk.CTkFont(family="Microsoft YaHei", size=12)
        )
        r_md.pack(anchor="w", pady=6)

        r_excel = ctk.CTkRadioButton(
            options_frame,
            text="仅导出 Excel 数据归档 (*.xlsx)",
            variable=self.export_format_var,
            value="excel",
            font=ctk.CTkFont(family="Microsoft YaHei", size=12)
        )
        r_excel.pack(anchor="w", pady=6)

        # Divider line
        divider = ctk.CTkFrame(card, height=1, fg_color=("#E5E7EB", "#374151"))
        divider.pack(fill="x", padx=20, pady=10)

        # Checkbox for cache details
        self.include_cache_var = ctk.BooleanVar(value=False)
        self.cb_cache = ctk.CTkCheckBox(
            card,
            text="Excel 中附带完整 F0 / 共振峰缓存明细",
            variable=self.include_cache_var,
            font=ctk.CTkFont(family="Microsoft YaHei", size=12),
            fg_color="#10B981",
            hover_color="#059669"
        )
        self.cb_cache.pack(anchor="w", padx=20, pady=5)

        # If user selects only markdown, disable the cache details checkbox
        def on_format_change(*args):
            if self.export_format_var.get() == "md":
                self.cb_cache.configure(state="disabled")
                self.include_cache_var.set(False)
            else:
                self.cb_cache.configure(state="normal")

        self.export_format_var.trace_add("write", on_format_change)

        # Buttons frame
        buttons_frame = ctk.CTkFrame(card, fg_color="transparent")
        buttons_frame.pack(fill="x", side="bottom", padx=20, pady=15)

        btn_cancel = ctk.CTkButton(
            buttons_frame,
            text="取消",
            width=100,
            height=32,
            corner_radius=16,
            fg_color=("#F3F4F6", "#374151"),
            text_color=("#1F2937", "#E5E7EB"),
            hover_color=("#E5E7EB", "#4B5563"),
            font=ctk.CTkFont(family="Microsoft YaHei", size=12),
            command=self.destroy
        )
        btn_cancel.pack(side="left")

        btn_ok = ctk.CTkButton(
            buttons_frame,
            text="确定",
            width=100,
            height=32,
            corner_radius=16,
            fg_color=("#10B981", "#059669"),
            hover_color=("#059669", "#047857"),
            text_color="white",
            font=ctk.CTkFont(family="Microsoft YaHei", size=12, weight="bold"),
            command=self.on_confirm
        )
        btn_ok.pack(side="right")

    def on_confirm(self):
        fmt = self.export_format_var.get()
        include_cache = self.include_cache_var.get()
        self.destroy()
        self.callback(fmt, include_cache)


# ==========================================
# 主程序 App
# ==========================================

class ToolkitApp(ctk.CTk):
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

        self.title("PhonTracer Toolkit")
        self.geometry("1100x760")
        self.minsize(1000, 700)

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
        ico_path = os.path.join(os.path.dirname(__file__), "assets", "toolkit.ico")
        png_path = os.path.join(os.path.dirname(__file__), "assets", "toolkit.png")

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
            "import_white": "import_white.png", "tab_batch": "tab_batch.png",
            "pause": "pause.png", "copy": "copy_icon.png"
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
                if key in ["plus", "save", "check", "warning", "audio", "eye", "import_white", "tab_batch", "list", "pause", "copy"]:
                    img = make_white(img)
                self.icons[key] = ctk.CTkImage(light_image=img, dark_image=img, size=(20, 20))
            else:
                self.icons[key] = None

        # 加载软件 Logo
        logo_path = os.path.join(os.path.dirname(__file__), "assets", "toolkit.png")
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
        self.grid_rowconfigure(0, weight=1)

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

        self.main_shell = ctk.CTkFrame(self, fg_color="transparent")
        self.main_shell.grid(row=0, column=0, sticky="nsew", padx=24, pady=20)
        self.main_shell.grid_columnconfigure(0, weight=1)
        self.main_shell.grid_rowconfigure(0, weight=1)

        self.tab_merge_name = "音频合并（独立音频→长音频）"
        self.tab_split_name = "音频拆分（长音频→独立音频）"
        self.tab_project_name = "工程预览"
        self.tab_script_name = "自定义脚本"

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
        self.tab_script = self.tabview.add(self.tab_script_name)

        for tab in (self.tab_merge, self.tab_split, self.tab_project, self.tab_script):
            tab.configure(fg_color=self.colors["surface"])

        # Patch segmented button: left-align, pill-shaped, wider, fix text colors
        sb = self.tabview._segmented_button
        # Configure overall size, pill shape (corner_radius), and doubled inner border (border_width=6)
        sb.configure(width=640, height=40, corner_radius=20, border_width=6)
        # Cleanly left-align the segmented button in the grid without stretching
        sb.grid_configure(sticky="w", padx=20, pady=(12, 6))

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
        self.build_script_tab()

        self.progress = ctk.CTkProgressBar(self, height=5, progress_color=self.colors["primary"], fg_color="#D8E0EA")
        self.progress.set(0)
        self.progress.grid(row=1, column=0, sticky="ew")
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

        button_row = ctk.CTkFrame(source_card, fg_color="transparent")
        button_row.grid(row=0, column=2, padx=20, pady=16, sticky="e")
        self.btn_sel_source = self._make_button(button_row, "选择长音频", self.select_split_source, image=self.icons.get("audio"), width=142)
        self.btn_sel_source.pack(side=tk.LEFT, padx=(0, 10))
        self.btn_edit_segments = self._make_button(button_row, "段落编辑器", self.open_visual_splitter, tone="warning", image=self.icons.get("eye"), width=142)
        self.btn_edit_segments.pack(side=tk.LEFT)

        path_pill = ctk.CTkFrame(source_card, fg_color=self.colors["surface"], corner_radius=999, border_width=1, border_color=self.colors["border"])
        path_pill.grid(row=1, column=0, columnspan=3, sticky="ew", padx=20, pady=(0, 16))
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
        self._make_button(word_header, "导入字表", self.import_wordlist, tone="primary", image=self.icons.get("import_white"), width=126).grid(row=0, column=1, sticky="e")

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

    def set_loading(self, state, msg="", indeterminate=False):
        if getattr(self, "_ui_thread_id", None) is not None and threading.get_ident() != self._ui_thread_id:
            self.after(0, lambda: self.set_loading(state, msg, indeterminate))
            return

        if state:
            if hasattr(self, 'lbl_status'):
                self.lbl_status.configure(text=msg or "处理中...", text_color="#1D4ED8")
            self.progress.grid()
            self.progress.stop()
            self.progress.configure(mode="indeterminate" if indeterminate else "determinate")
            if indeterminate:
                self.progress.start()
            else:
                self.progress.set(0.06)
            self.update_idletasks()
        else:
            if hasattr(self, 'lbl_status'):
                self.lbl_status.configure(text="就绪" if not msg else msg, text_color="#047857")
            self.progress.stop()
            self.progress.configure(mode="determinate")
            self.progress.set(0)
            self.progress.grid_remove()

    def update_progress(self, val, msg=None):
        def apply_update():
            if msg and hasattr(self, 'lbl_status'):
                self.lbl_status.configure(text=msg, text_color="#1D4ED8")
            self.progress.stop()
            self.progress.configure(mode="determinate")
            self.progress.set(val)

        self.after(0, apply_update)

    def update_report_progress(self, _val, msg):
        def apply_update():
            if hasattr(self, 'lbl_status'):
                self.lbl_status.configure(text=msg, text_color="#1D4ED8")

        self.after(0, apply_update)

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
                    vad_segs = macroscopic_vad(snd, expected_count=len(self.wordlist) or None)
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
                    segs = macroscopic_vad(snd, expected_count=len(wordlist))

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
        self.btn_convert_zip.pack(fill=tk.X, pady=(0, 10))
        self.btn_convert_zip.configure(state="disabled")  # Disabled until a project is loaded

        self.btn_export_report = self._make_button(
            actions,
            "导出研究方法报告",
            self.show_export_report_dialog,
            tone="success",
            image=self.icons.get("tab_batch"),
        )
        self.btn_export_report.pack(fill=tk.X)
        self.btn_export_report.configure(state="disabled")

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


# ==========================================
# ToolkitApp 自定义脚本相关方法
# ==========================================

    def build_script_tab(self):
        # 初始化私有变量
        self.project_data = None
        self.project_namelist = []
        self.selected_script_id = None
        self.is_script_running = False
        self.run_cancel_event = None
        self.local_scripts = []
        self.script_output_dir = None
        self.script_figure_results = []
        self.current_script_figure_index = 0

        # 生成白色的 play 和 pause 图标
        for icon_key, filename in [("play_white", "play.png"), ("pause_white", "pause.png")]:
            try:
                icon_path = os.path.join("assets", "icons", filename)
                if not os.path.exists(icon_path):
                    icon_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets", "icons", filename)
                if os.path.exists(icon_path):
                    from PIL import Image
                    img = Image.open(icon_path).convert("RGBA")
                    data = np.array(img)
                    data[:, :, 0] = 255
                    data[:, :, 1] = 255
                    data[:, :, 2] = 255
                    img_white = Image.fromarray(data)
                    self.icons[icon_key] = ctk.CTkImage(light_image=img_white, dark_image=img_white, size=(20, 20))
            except Exception:
                pass

        content = ctk.CTkFrame(self.tab_script, fg_color="transparent")
        content.pack(fill=tk.BOTH, expand=True, padx=20, pady=18)
        content.grid_columnconfigure(0, weight=0)
        content.grid_columnconfigure(1, weight=1)
        content.grid_rowconfigure(0, weight=1)

        # ----------------------------------------------------
        # 左栏：脚本列表与管理
        # ----------------------------------------------------
        left_panel = self._make_card(content, width=280)
        left_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 16))
        left_panel.pack_propagate(False)

        self._section_header(left_panel, "脚本库", subtitle=None, icon_text="04")

        # 搜索框
        search_frame = ctk.CTkFrame(left_panel, fg_color="transparent")
        search_frame.pack(fill=tk.X, padx=20, pady=(0, 5))
        self.entry_script_search = ctk.CTkEntry(
            search_frame,
            placeholder_text="搜索脚本...",
            height=36,
            corner_radius=18,
            font=ctk.CTkFont(family="Microsoft YaHei", size=13),
            fg_color=("#F9FAFB", "#262930"),
            border_color=("#D1D5DB", "#374151"),
            text_color=("#111827", "#F9FAFB")
        )
        self.entry_script_search.pack(fill=tk.X)
        self.entry_script_search.bind("<KeyRelease>", lambda e: self.load_scripts_to_tree())

        # 类型过滤
        self.combo_script_type = ctk.CTkOptionMenu(
            left_panel,
            values=["全部类型", "图表脚本", "数据处理脚本（暂未开放）"],
            command=self.on_script_filter_changed,
            fg_color=("#F3F4F6", "#374151"),
            text_color=("#1F2937", "#E5E7EB"),
            button_color=("#F3F4F6", "#374151"),
            button_hover_color=("#E5E7EB", "#4B5563"),
            height=32,
            corner_radius=16
        )
        self.combo_script_type.pack(fill=tk.X, padx=20, pady=5)
        _apply_custom_arrow(self.combo_script_type)

        # 列表框
        tree_frame = ctk.CTkFrame(left_panel, fg_color="transparent")
        tree_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=5)

        style = ttk.Style()
        style.configure("Script.Treeview",
                        font=("Microsoft YaHei", 11),
                        rowheight=36,
                        background="#FFFFFF",
                        fieldbackground="#FFFFFF",
                        foreground="#1F2937",
                        borderwidth=0,
                        relief="flat")
        style.map("Script.Treeview", background=[('selected', '#3B82F6')], foreground=[('selected', '#FFFFFF')])

        self.script_tree = ttk.Treeview(tree_frame, show="tree", selectmode="browse", style="Script.Treeview")
        self.script_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # 自定义滚动条
        script_scroll = ctk.CTkScrollbar(tree_frame, orientation="vertical", command=self.script_tree.yview, width=12)
        script_scroll.pack(side=tk.RIGHT, fill=tk.Y, padx=1, pady=1)
        self.script_tree.configure(yscrollcommand=script_scroll.set)
        self.script_tree.bind("<<TreeviewSelect>>", self.on_script_selected)

        # 常用操作按钮
        act_frame1 = ctk.CTkFrame(left_panel, fg_color="transparent")
        act_frame1.pack(fill=tk.X, padx=20, pady=5)
        self._make_button(act_frame1, "新建", self.on_new_script, tone="primary", width=65, height=32).pack(side=tk.LEFT, padx=(0, 5))
        self._make_button(act_frame1, "编辑", self.on_edit_script, tone="purple", width=65, height=32).pack(side=tk.LEFT, padx=(0, 5))
        self._make_button(act_frame1, "删除", self.on_delete_script, tone="danger", width=65, height=32).pack(side=tk.LEFT)

        act_frame2 = ctk.CTkFrame(left_panel, fg_color="transparent")
        act_frame2.pack(fill=tk.X, padx=20, pady=(5, 20))
        self._make_button(act_frame2, "导入脚本", self.on_import_script, tone="secondary", height=32).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 5))
        self._make_button(act_frame2, "导出脚本", self.on_export_script, tone="secondary", height=32).pack(side=tk.LEFT, expand=True, fill=tk.X)

        # ----------------------------------------------------
        # 右栏：运行控制与预览
        # ----------------------------------------------------
        right_panel = self._make_card(content)
        right_panel.grid(row=0, column=1, sticky="nsew", padx=(16, 0))

        self._section_header(right_panel, "运行与输出", subtitle=None)

        # 1. 脚本信息栏
        info_frame = ctk.CTkFrame(right_panel, fg_color=self.colors["surface_soft"], corner_radius=10, border_width=1, border_color=self.colors["border"])
        info_frame.pack(fill=tk.X, padx=20, pady=5)
        info_frame.grid_columnconfigure(0, weight=1)

        self.lbl_selected_script_name = ctk.CTkLabel(
            info_frame,
            text="当前脚本：未选择",
            font=ctk.CTkFont(family=self.font_family, size=14, weight="bold"),
            text_color=self.colors["text"],
            anchor="w",
            justify="left"
        )
        self.lbl_selected_script_name.grid(row=0, column=0, sticky="w", padx=15, pady=(10, 2))

        self.lbl_selected_script_desc = ctk.CTkLabel(
            info_frame,
            text="描述：请从左侧列表中选择一个脚本，或者点击“新建”创建新脚本。",
            font=self.font_small,
            text_color=self.colors["muted"],
            anchor="w",
            justify="left",
            wraplength=480
        )
        self.lbl_selected_script_desc.grid(row=1, column=0, sticky="w", padx=15, pady=(0, 10))

        # 源码预览区
        ctk.CTkLabel(info_frame, text="源码预览:", font=ctk.CTkFont(family=self.font_family, size=11, weight="bold"), text_color=self.colors["muted"]).grid(row=2, column=0, sticky="w", padx=15, pady=(0, 2))
        self.txt_script_code_preview = ctk.CTkTextbox(
            info_frame,
            height=120,
            font=ctk.CTkFont(family="Consolas", size=11),
            wrap="none",
            border_width=1,
            border_color=("#D1D5DB", "#374151"),
            fg_color=("#FFFFFF", "#262930"),
            text_color=("#111827", "#F9FAFB")
        )
        self.txt_script_code_preview.grid(row=3, column=0, sticky="ew", padx=15, pady=(0, 15))
        self.txt_script_code_preview.configure(state="disabled")

        # 加速源码预览滚轮
        def speed_up_preview_scroll(event):
            scroll_units = int(-1 * (event.delta / 120) * 4)
            self.txt_script_code_preview.yview_scroll(scroll_units, "units")
            return "break"
        self.txt_script_code_preview.bind("<MouseWheel>", speed_up_preview_scroll)
        if hasattr(self.txt_script_code_preview, "_textbox"):
            self.txt_script_code_preview._textbox.bind("<MouseWheel>", speed_up_preview_scroll)

        # 2. 控制按钮 & 数据摘要
        control_frame = ctk.CTkFrame(right_panel, fg_color="transparent")
        control_frame.pack(fill=tk.X, padx=20, pady=5)

        self.btn_run_script = self._make_button(control_frame, " 运行", self.run_current_script, tone="primary", image=self.icons.get("play_white"), height=36)
        self.btn_run_script.pack(side=tk.LEFT, padx=(0, 10))

        self.btn_stop_script = self._make_button(control_frame, " 停止", self.stop_current_script, tone="danger", image=self.icons.get("pause_white"), height=36)
        self.btn_stop_script.pack(side=tk.LEFT, padx=(0, 10))
        self.btn_stop_script.configure(state="disabled")

        self.btn_copy_prompt = self._make_button(control_frame, " 复制 AI 脚本提示词", self.show_prompt_dialog, tone="purple", image=self.icons.get("copy"), height=36)
        self.btn_copy_prompt.pack(side=tk.LEFT, padx=(0, 10))

        self.btn_open_script_output = self._make_button(control_frame, "打开结果目录", self.open_script_output_dir, tone="secondary", height=36)
        self.btn_open_script_output.pack(side=tk.LEFT, padx=(0, 10))
        self.btn_open_script_output.configure(state="disabled")

        self.lbl_script_proj_summary = ctk.CTkLabel(control_frame, text="数据状态: 未加载工程", font=self.font_small, text_color=self.colors["muted"], anchor="e", justify="right")
        self.lbl_script_proj_summary.pack(side=tk.RIGHT, fill="x", expand=True)

        # 3. 输出预览 & 日志区
        output_frame = ctk.CTkFrame(right_panel, fg_color="transparent")
        output_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=(5, 20))
        output_frame.grid_columnconfigure(0, weight=2)
        output_frame.grid_columnconfigure(1, weight=1)
        output_frame.grid_rowconfigure(0, weight=1)

        # 左侧大图表预览
        preview_box = ctk.CTkFrame(output_frame, fg_color=self.colors["surface_soft"], corner_radius=10, border_width=1, border_color=self.colors["border"])
        preview_box.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        preview_box.grid_rowconfigure(1, weight=1)
        preview_box.grid_columnconfigure(0, weight=1)
        self.script_preview_nav = ctk.CTkFrame(preview_box, fg_color="transparent")
        self.script_preview_nav.grid(row=0, column=0, sticky="ew", padx=10, pady=(8, 0))
        self.script_preview_nav.grid_columnconfigure(1, weight=1)
        self.btn_script_prev_fig = ctk.CTkButton(
            self.script_preview_nav, text="上一张", width=82, height=28,
            corner_radius=14, fg_color="#E5E7EB", text_color="#1F2937",
            hover_color="#D1D5DB", command=self.show_prev_script_figure
        )
        self.btn_script_prev_fig.grid(row=0, column=0, sticky="w")
        self.lbl_script_fig_page = ctk.CTkLabel(
            self.script_preview_nav, text="图表: 0/0", font=self.font_small,
            text_color=self.colors["muted"], anchor="center"
        )
        self.lbl_script_fig_page.grid(row=0, column=1, sticky="ew")
        self.btn_script_next_fig = ctk.CTkButton(
            self.script_preview_nav, text="下一张", width=82, height=28,
            corner_radius=14, fg_color="#E5E7EB", text_color="#1F2937",
            hover_color="#D1D5DB", command=self.show_next_script_figure
        )
        self.btn_script_next_fig.grid(row=0, column=2, sticky="e")
        self.script_preview_nav.grid_remove()
        self.lbl_chart_preview = ctk.CTkLabel(preview_box, text="暂无图表预览")
        self.lbl_chart_preview.grid(row=1, column=0, sticky="nsew", padx=10, pady=10)

        # 右侧日志输出
        log_box = ctk.CTkFrame(output_frame, fg_color=self.colors["surface_soft"], corner_radius=10, border_width=1, border_color=self.colors["border"])
        log_box.grid(row=0, column=1, sticky="nsew")
        log_box.grid_rowconfigure(1, weight=1)
        log_box.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(log_box, text="运行日志:", font=ctk.CTkFont(family=self.font_family, size=11, weight="bold"), text_color=self.colors["muted"]).grid(row=0, column=0, sticky="w", padx=10, pady=(5, 2))
        self.txt_script_log = ctk.CTkTextbox(log_box, font=ctk.CTkFont(family="Consolas", size=11), fg_color="transparent")
        self.txt_script_log.grid(row=1, column=0, sticky="nsew", padx=5, pady=(0, 5))
        self.txt_script_log.configure(state="disabled")

        # 加速运行日志滚轮
        def speed_up_log_scroll(event):
            scroll_units = int(-1 * (event.delta / 120) * 4)
            self.txt_script_log.yview_scroll(scroll_units, "units")
            return "break"
        self.txt_script_log.bind("<MouseWheel>", speed_up_log_scroll)
        if hasattr(self.txt_script_log, "_textbox"):
            self.txt_script_log._textbox.bind("<MouseWheel>", speed_up_log_scroll)

        # 刷新列表并选择第一个
        self.load_scripts_to_tree()
        children = self.script_tree.get_children()
        if children:
            self.script_tree.selection_set(children[0])

    def load_scripts_to_tree(self):
        from modules.script_manager import load_all_scripts
        self.local_scripts = load_all_scripts()

        # 清空树
        for item in self.script_tree.get_children():
            self.script_tree.delete(item)

        search_term = self.entry_script_search.get().strip().lower()
        filter_type = self.combo_script_type.get()

        for s in self.local_scripts:
            name = s.get("name", "未命名")
            desc = s.get("description", "")
            type_str = s.get("type", "chart")

            # 过滤搜索
            if search_term and search_term not in name.lower() and search_term not in desc.lower():
                continue

            # 过滤类型
            if filter_type == "图表脚本" and type_str != "chart":
                continue
            elif filter_type == "数据处理脚本（暂未开放）" and type_str != "data_process":
                continue

            self.script_tree.insert("", tk.END, iid=s["id"], text=name)

    def on_script_selected(self, event):
        selected = self.script_tree.selection()
        if not selected:
            return
        s_id = selected[0]
        self.selected_script_id = s_id

        script = next((s for s in self.local_scripts if s["id"] == s_id), None)
        if script:
            self.lbl_selected_script_name.configure(text=f"当前脚本：{script.get('name', '')}")
            self.lbl_selected_script_desc.configure(text=f"描述：{script.get('description', '')}")

            self.txt_script_code_preview.configure(state="normal")
            self.txt_script_code_preview.delete("1.0", tk.END)
            self.txt_script_code_preview.insert("1.0", script.get("code", ""))
            self.txt_script_code_preview.configure(state="disabled")

    def on_script_filter_changed(self, val):
        self.load_scripts_to_tree()

    def on_new_script(self):
        default_template = '''def run(ctx):
    # 1. 获取纳入分析的条目
    items = ctx.dataset.included_items()
    if not items:
        # 生成提示空数据图表
        fig, ax = ctx.plt.subplots(figsize=(6, 4))
        ax.text(0.5, 0.5, "暂无分析数据", ha="center", va="center", fontsize=12, color="red")
        ax.axis("off")
        return ctx.figure(fig, filename="empty.png", title="无数据")

    # 2. 在下方编写你的绘图逻辑
    fig, ax = ctx.plt.subplots(figsize=(7, 5))
    ax.grid(True, linestyle="--", alpha=0.5)

    # 示例：遍历条目绘制 F0 曲线
    for item in items[:10]: # 仅绘制前 10 条示例
        pitch = ctx.dataset.pitch_points(item)
        freqs = pitch.get("freqs", [])
        valid_f = [f for f in freqs if f > 0]
        if valid_f:
            ax.plot(valid_f, label=item.get("label"))

    ax.set_title("自定义 F0 曲线图", fontsize=14, fontweight="bold")
    ax.set_xlabel("样本点", fontsize=12)
    ax.set_ylabel("基频 F0 (Hz)", fontsize=12)

    return ctx.figure(fig, filename="my_chart.png", title="我的自定义图表")
'''
        ScriptEditorDialog(self, script_id=None, script_name="新建自定义脚本", script_desc="自定义分析说明", script_code=default_template)

    def on_edit_script(self):
        selected = self.script_tree.selection()
        if not selected:
            return messagebox.showwarning("提示", "请先在左侧列表中选择要编辑的脚本。")
        s_id = selected[0]
        script = next((s for s in self.local_scripts if s["id"] == s_id), None)
        if script:
            ScriptEditorDialog(
                self,
                script_id=script["id"],
                script_name=script.get("name", ""),
                script_desc=script.get("description", ""),
                script_code=script.get("code", ""),
                script_type=script.get("type", "chart")
            )

    def on_delete_script(self):
        selected = self.script_tree.selection()
        if not selected:
            return messagebox.showwarning("提示", "请先在列表中选择一个要删除的脚本。")
        s_id = selected[0]

        script = next((s for s in self.local_scripts if s["id"] == s_id), None)
        if not script:
            return

        ans = messagebox.askyesno("确认删除", f"确认要删除脚本“{script['name']}”吗？")
        if ans:
            from modules.script_manager import delete_script
            delete_script(s_id)
            self.selected_script_id = None
            self.lbl_selected_script_name.configure(text="当前脚本：未选择")
            self.lbl_selected_script_desc.configure(text="描述：请从左侧列表中选择一个脚本，或者点击“新建”创建新脚本。")
            self.txt_script_code_preview.configure(state="normal")
            self.txt_script_code_preview.delete("1.0", tk.END)
            self.txt_script_code_preview.configure(state="disabled")
            self.load_scripts_to_tree()

    def on_import_script(self):
        file_path = filedialog.askopenfilename(filetypes=[("JSON Files", "*.json")])
        if file_path:
            from modules.script_manager import import_script
            try:
                new_s = import_script(file_path)
                messagebox.showinfo("成功", f"成功导入脚本：{new_s['name']}")
                self.load_scripts_to_tree()
                self.script_tree.selection_set(new_s["id"])
            except Exception as e:
                messagebox.showerror("错误", f"导入失败：{e}")

    def on_export_script(self):
        selected = self.script_tree.selection()
        if not selected:
            return messagebox.showwarning("提示", "请先在列表中选择一个要导出的脚本。")
        s_id = selected[0]

        script = next((s for s in self.local_scripts if s["id"] == s_id), None)
        if not script:
            return

        dest_path = filedialog.asksaveasfilename(
            defaultextension=".json",
            initialfile=f"{script['name']}.json",
            filetypes=[("JSON Files", "*.json")]
        )
        if dest_path:
            from modules.script_manager import export_script
            try:
                export_script(s_id, dest_path)
                messagebox.showinfo("成功", f"脚本已成功导出至：\n{dest_path}")
            except Exception as e:
                messagebox.showerror("错误", f"导出失败：{e}")

    def run_current_script(self):
        code = self.txt_script_code_preview.get("1.0", tk.END)

        # 1. 构造快照数据
        if hasattr(self, 'loaded_teproj_path') and self.loaded_teproj_path:
            from modules.script_api import build_dataset_snapshot
            dataset_items = build_dataset_snapshot(self.loaded_teproj_path)
        else:
            dataset_items = []

        self.is_script_running = True
        self.btn_run_script.configure(state="disabled")
        self.btn_stop_script.configure(state="normal")
        self.script_output_dir = None
        self.script_figure_results = []
        self.current_script_figure_index = 0
        if hasattr(self, "btn_open_script_output"):
            self.btn_open_script_output.configure(state="disabled")
        if hasattr(self, "script_preview_nav"):
            self.script_preview_nav.grid_remove()
        self.lbl_chart_preview.configure(text="正在运行脚本，等待图表输出...", image="")
        self.lbl_chart_preview.image = None

        self.txt_script_log.configure(state="normal")
        self.txt_script_log.delete("1.0", tk.END)
        self.txt_script_log.insert("1.0", "正在运行自定义脚本...\n")
        self.txt_script_log.configure(state="disabled")

        self.run_cancel_event = threading.Event()

        def execute():
            from modules.script_runner import run_custom_script
            import matplotlib.pyplot as plt
            plt.close('all') # 运行前清理图表

            res, logs, err = run_custom_script(code, dataset_items, timeout=30, cancel_event=self.run_cancel_event)
            self.after(0, lambda: self.on_script_finished(res, logs, err))

        start_safe_thread(execute)

    def stop_current_script(self):
        if getattr(self, "run_cancel_event", None):
            self.run_cancel_event.set()
            self.txt_script_log.configure(state="normal")
            self.txt_script_log.insert(tk.END, "\n已请求中止脚本运行，脚本会在下一次检查取消状态时结束。\n")
            self.txt_script_log.configure(state="disabled")

    def on_script_finished(self, res, logs, err):
        self.is_script_running = False
        self.btn_run_script.configure(state="normal")
        self.btn_stop_script.configure(state="disabled")

        self.txt_script_log.configure(state="normal")
        self.txt_script_log.delete("1.0", tk.END)
        if logs:
            self.txt_script_log.insert("1.0", "=== 运行日志 ===\n" + "\n".join(logs) + "\n\n")

        if err:
            self.txt_script_log.insert(tk.END, f"运行失败：\n{err}\n")
            self.script_output_dir = None
            self.script_figure_results = []
            self.current_script_figure_index = 0
            if hasattr(self, "btn_open_script_output"):
                self.btn_open_script_output.configure(state="disabled")
            if hasattr(self, "script_preview_nav"):
                self.script_preview_nav.grid_remove()
            self.lbl_chart_preview.configure(text="运行失败", image="")
            self.lbl_chart_preview.image = None
            self.txt_script_log.configure(state="disabled")
            return

        self.txt_script_log.insert(tk.END, "运行成功结束。\n")
        self.txt_script_log.configure(state="disabled")

        self.display_script_result(res)

    def append_script_log(self, text):
        if not hasattr(self, "txt_script_log"):
            return
        self.txt_script_log.configure(state="normal")
        self.txt_script_log.insert(tk.END, str(text))
        self.txt_script_log.see(tk.END)
        self.txt_script_log.configure(state="disabled")

    def _safe_script_output_name(self, name, fallback="output"):
        raw = str(name or fallback).strip()
        raw = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", raw)
        raw = re.sub(r"\s+", "_", raw)
        raw = raw.strip("._ ")
        return raw[:80] or fallback

    def _unique_path(self, folder, filename):
        base, ext = os.path.splitext(filename)
        path = os.path.join(folder, filename)
        index = 2
        while os.path.exists(path):
            path = os.path.join(folder, f"{base}_{index}{ext}")
            index += 1
        return path

    def get_script_output_dir(self, script_name):
        base_dir = os.path.join(os.path.expanduser("~"), ".phon_tracer", "script_outputs")
        safe_name = self._safe_script_output_name(script_name, "script")
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        folder = os.path.join(base_dir, f"{stamp}_{safe_name}")
        suffix = 2
        unique_folder = folder
        while os.path.exists(unique_folder):
            unique_folder = f"{folder}_{suffix}"
            suffix += 1
        os.makedirs(unique_folder, exist_ok=True)
        return unique_folder

    def open_script_output_dir(self):
        folder = getattr(self, "script_output_dir", None)
        if not folder or not os.path.isdir(folder):
            messagebox.showinfo("结果目录", "当前还没有可打开的脚本输出目录。请先运行脚本并生成结果。")
            return
        try:
            if sys.platform == "win32":
                os.startfile(folder)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", folder])
            else:
                subprocess.Popen(["xdg-open", folder])
        except Exception as e:
            messagebox.showerror("打开失败", f"无法打开结果目录：\n{e}")

    def show_prev_script_figure(self):
        self.show_script_figure_at(getattr(self, "current_script_figure_index", 0) - 1)

    def show_next_script_figure(self):
        self.show_script_figure_at(getattr(self, "current_script_figure_index", 0) + 1)

    def show_script_figure_at(self, index):
        figures = getattr(self, "script_figure_results", []) or []
        if not figures:
            if hasattr(self, "script_preview_nav"):
                self.script_preview_nav.grid_remove()
            self.lbl_chart_preview.configure(text="暂无图表预览", image="")
            self.lbl_chart_preview.image = None
            return

        index = max(0, min(index, len(figures) - 1))
        self.current_script_figure_index = index
        item = figures[index]

        try:
            with Image.open(item["preview_path"]) as src:
                img = src.convert("RGBA")

            w_avail = self.lbl_chart_preview.master.winfo_width() - 28
            h_avail = self.lbl_chart_preview.master.winfo_height() - 64
            if w_avail < 80:
                w_avail = 480
            if h_avail < 80:
                h_avail = 320
            img.thumbnail((w_avail, h_avail), Image.Resampling.LANCZOS)

            ctk_image = ctk.CTkImage(light_image=img, dark_image=img, size=img.size)
            self.lbl_chart_preview.configure(text="", image=ctk_image)
            self.lbl_chart_preview.image = ctk_image
        except Exception as e:
            self.lbl_chart_preview.configure(text=f"无法加载图像预览：{e}", image="")
            self.lbl_chart_preview.image = None

        total = len(figures)
        title = item.get("title") or item.get("filename") or "自定义图表"
        if len(title) > 36:
            title = title[:33] + "..."
        self.lbl_script_fig_page.configure(text=f"图表: {index + 1}/{total}  {title}")
        self.btn_script_prev_fig.configure(state="normal" if index > 0 else "disabled")
        self.btn_script_next_fig.configure(state="normal" if index < total - 1 else "disabled")
        if total > 1:
            self.script_preview_nav.grid()
        else:
            self.script_preview_nav.grid_remove()

    def display_script_result(self, res):
        from modules.script_api import FigureResult, TableResult, configure_matplotlib_chinese_font

        results = res if isinstance(res, list) else [res]
        figure_results = [r for r in results if isinstance(r, FigureResult)]
        table_results = [r for r in results if isinstance(r, TableResult)]

        script_meta = next((s for s in self.local_scripts if s.get("id") == self.selected_script_id), {}) or {}
        script_name = script_meta.get("name") or "未命名脚本"
        script_desc = script_meta.get("description") or "绘图分析"
        script_type = script_meta.get("type") or "chart"

        output_dir = None
        output_records = []
        self.script_figure_results = []
        self.current_script_figure_index = 0

        if figure_results or table_results:
            output_dir = self.get_script_output_dir(script_name)
            self.script_output_dir = output_dir
            if hasattr(self, "btn_open_script_output"):
                self.btn_open_script_output.configure(state="normal")
            self.append_script_log(f"\n输出目录：{output_dir}\n")
        else:
            self.script_output_dir = None
            if hasattr(self, "btn_open_script_output"):
                self.btn_open_script_output.configure(state="disabled")

        configure_matplotlib_chinese_font()

        for idx, fig_res in enumerate(figure_results, start=1):
            filename = self._safe_script_output_name(fig_res.filename or f"custom_chart_{idx}.png", f"custom_chart_{idx}.png")
            root, ext = os.path.splitext(filename)
            if not ext:
                filename = f"{filename}.png"
            elif ext.lower() not in {".png", ".jpg", ".jpeg", ".svg", ".pdf"}:
                filename = f"{root}.png"

            output_path = self._unique_path(output_dir, filename)
            preview_path = self._unique_path(output_dir, f"_preview_{idx}_{os.path.splitext(os.path.basename(output_path))[0]}.png")
            try:
                fig_res.fig.savefig(output_path, dpi=300, bbox_inches="tight")
                fig_res.fig.savefig(preview_path, dpi=150, bbox_inches="tight")
                item = {
                    "result": fig_res,
                    "output_path": output_path,
                    "preview_path": preview_path,
                    "title": fig_res.title,
                    "filename": os.path.basename(output_path),
                }
                self.script_figure_results.append(item)
                output_records.append({
                    "type": "figure",
                    "title": fig_res.title,
                    "filename": os.path.basename(output_path),
                    "saved_path": output_path,
                })
                self.append_script_log(f"图表 {idx}：{output_path}\n")
            except Exception as e:
                self.append_script_log(f"图表 {idx} 保存失败：{e}\n")

        for idx, tbl_res in enumerate(table_results, start=1):
            import csv
            base_name = self._safe_script_output_name(tbl_res.title or f"custom_table_{idx}", f"custom_table_{idx}")
            table_path = self._unique_path(output_dir, f"{base_name}.csv")
            try:
                with open(table_path, "w", encoding="utf-8-sig", newline="") as f:
                    writer = csv.writer(f)
                    writer.writerow(list(tbl_res.columns or []))
                    writer.writerows(tbl_res.rows or [])
                output_records.append({
                    "type": "table",
                    "title": tbl_res.title,
                    "filename": os.path.basename(table_path),
                    "saved_path": table_path,
                })
                self.append_script_log(f"表格 {idx}：{table_path}\n")
            except Exception as e:
                self.append_script_log(f"表格 {idx} 保存失败：{e}\n")

        if self.script_figure_results:
            self.show_script_figure_at(0)
        elif table_results:
            self.lbl_chart_preview.configure(text="未生成图表，表格结果已保存到输出目录。", image="")
            self.lbl_chart_preview.image = None
            if hasattr(self, "script_preview_nav"):
                self.script_preview_nav.grid_remove()
        else:
            self.lbl_chart_preview.configure(text="暂无图表预览", image="")
            self.lbl_chart_preview.image = None
            if hasattr(self, "script_preview_nav"):
                self.script_preview_nav.grid_remove()

        # 2. 归档运行历史到工程文件 project.json
        if hasattr(self, 'loaded_teproj_path') and self.loaded_teproj_path and hasattr(self, 'project_data') and self.project_data:
            import hashlib
            from modules.version import __version__

            code = self.txt_script_code_preview.get("1.0", tk.END).strip()
            code_sha256 = hashlib.sha256(code.encode('utf-8')).hexdigest()

            run_record = {
                "script_id": self.selected_script_id or str(uuid.uuid4()),
                "script_name": script_name,
                "script_type": script_type,
                "api_version": "1",
                "software_version": __version__,
                "code_sha256": code_sha256,
                "code": code,
                "used_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "user_goal": script_desc,
                "status": "成功",
                "outputs": output_records
            }

            if "custom_script_runs" not in self.project_data:
                self.project_data["custom_script_runs"] = []

            # 避免同一 SHA256 频繁追加入历史，做唯一性/最后一次覆盖，或直接追加
            self.project_data["custom_script_runs"].append(run_record)

            # 自动写回 zip 压缩包
            self.save_project_data_back_to_teproj()

    def save_project_data_back_to_teproj(self):
        if not hasattr(self, 'loaded_teproj_path') or not self.loaded_teproj_path:
            return
        import tempfile
        import zipfile
        import json

        zip_path = self.loaded_teproj_path
        zip_dir = os.path.dirname(os.path.abspath(zip_path)) or "."
        temp_fd, temp_path = tempfile.mkstemp(prefix=".script_update_", suffix=".teproj", dir=zip_dir)
        os.close(temp_fd)
        try:
            with zipfile.ZipFile(zip_path, 'r') as yin:
                with zipfile.ZipFile(temp_path, 'w', zipfile.ZIP_DEFLATED) as yout:
                    for item in yin.infolist():
                        if item.filename == 'project.json':
                            continue
                        yout.writestr(item, yin.read(item.filename))
                    # 写入更新后的 project.json
                    json_bytes = json.dumps(self.project_data, ensure_ascii=False, indent=2).encode('utf-8')
                    yout.writestr('project.json', json_bytes)
            read_project_metadata_from_archive(temp_path)
            os.replace(temp_path, zip_path)
        except Exception as e:
            messagebox.showerror("错误", f"更新工程文件归档失败：{e}")
        finally:
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except Exception:
                    pass

    def update_script_tab_project_summary(self):
        if not hasattr(self, 'project_data') or not self.project_data:
            self.lbl_script_proj_summary.configure(text="工程数据状态: 未加载工程", text_color=self.colors["muted"])
            return

        spk_count = len(self.project_data.get("speakers", {}))
        item_count = 0
        groups = set()
        for spk in self.project_data.get("speakers", {}).values():
            items = spk.get("items", {})
            item_count += len(items)
            for item in items.values():
                g = item.get("group")
                if g:
                    groups.add(g)

        summary_text = f"工程: {os.path.basename(self.loaded_teproj_path)}\n发音人数: {spk_count} | 条目总数: {item_count}\n声调分组数: {len(groups)} ({', '.join(sorted(list(groups))) if groups else '无'})"
        self.lbl_script_proj_summary.configure(text=summary_text, text_color=self.colors["success"])

    def show_prompt_dialog(self):
        AIPromptDialog(self, self.project_data if hasattr(self, 'project_data') else None)

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
                self.create_detail_row(param_frame, 1, "基频范围 (F0 Range):", f"{get_pitch_floor(last_params):.0f} Hz ~ {get_pitch_ceiling(last_params):.0f} Hz")
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
        def run():
            try:
                project_data, namelist = read_project_metadata_from_archive(path)
                self.project_data = project_data
                self.project_namelist = namelist
                self.after(0, lambda: self._finish_project_preview(project_data, namelist))
            except Exception as e:
                self.after(0, lambda message=str(e): self._show_project_preview_error(message))

        start_safe_thread(run)

    def _finish_project_preview(self, project_data, namelist):
        self.display_project_preview(project_data, namelist)
        self.btn_convert_zip.configure(state="normal")
        self.btn_export_report.configure(state="normal")
        self.update_script_tab_project_summary()

    def _show_project_preview_error(self, message):
        err_msg = f"❌ 无法解析工程文件: {message}"
        self.show_error_placeholder(err_msg)
        self.btn_convert_zip.configure(state="disabled")
        self.btn_export_report.configure(state="disabled")
        self.lbl_proj_file.configure(text="解析失败", text_color=self.colors["danger"])
        messagebox.showerror("错误", f"解析 .teproj 文件失败:\n{message}")

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
                lines.append(f"      - 基频范围 (F0 Range): {get_pitch_floor(last_params):.0f} Hz ~ {get_pitch_ceiling(last_params):.0f} Hz")
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

    def show_export_report_dialog(self):
        if not hasattr(self, 'loaded_teproj_path') or not self.loaded_teproj_path:
            return messagebox.showwarning("提示", "请先选择并加载 .teproj 文件")

        ExportReportDialog(self, self.execute_export_report)

    def execute_export_report(self, export_format, include_cache):
        output_dir = filedialog.askdirectory(title="选择报告导出保存目录")
        if not output_dir:
            return

        from modules.report_generator import export_reports_from_teproj

        def run():
            self.set_loading(True, "正在生成研究报告与数据档案...", indeterminate=True)
            try:
                export_markdown = (export_format in ("both", "md"))
                export_excel = (export_format in ("both", "excel"))

                exported_files, base_name = export_reports_from_teproj(
                    self.loaded_teproj_path,
                    output_dir,
                    export_markdown=export_markdown,
                    export_excel=export_excel,
                    include_cache_details=include_cache,
                    progress_callback=self.update_report_progress,
                )

                filenames = [os.path.basename(f) for f in exported_files]
                msg = f"报告已成功导出至以下文件：\n" + "\n".join([f"• {name}" for name in filenames])

                self.after(0, lambda: messagebox.showinfo("导出成功", msg))
            except Exception as e:
                import traceback
                traceback.print_exc()
                self.after(0, lambda err_msg=str(e): messagebox.showerror("错误", f"导出失败：\n{err_msg}"))
            finally:
                self.after(0, lambda: self.set_loading(False))

        start_safe_thread(run)

def _apply_custom_arrow(dropdown):
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


class ScriptEditorDialog(ctk.CTkToplevel):
    """
    新建/编辑自定义脚本的弹出式对话框。
    """
    def __init__(self, parent, script_id=None, script_name="", script_desc="", script_code="", script_type="chart"):
        super().__init__(parent)
        self.parent = parent
        self.script_id = script_id
        self.script_type = script_type

        self.title("新建自定义脚本" if not script_id else "编辑自定义脚本")
        self.geometry("720x640")

        # 居中显示
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        x = (sw - 720) // 2
        y = (sh - 640) // 2
        self.geometry(f"720x640+{x}+{y}")
        self.transient(parent)
        self.grab_set()

        self.configure(fg_color=("#F9FAFB", "#1A1D24"))

        # 顶边条
        accent_strip = ctk.CTkFrame(self, height=4, fg_color="#6366F1", corner_radius=0)
        accent_strip.pack(fill="x", side="top")

        content = ctk.CTkFrame(self, fg_color="transparent")
        content.pack(fill="both", expand=True, padx=24, pady=18)

        # 脚本名称
        row_name = ctk.CTkFrame(content, fg_color="transparent")
        row_name.pack(fill="x", pady=6)
        ctk.CTkLabel(row_name, text="脚本名称:", font=parent.font_main, text_color=parent.colors["text"], width=70, anchor="w").pack(side="left")
        self.entry_name = ctk.CTkEntry(
            row_name,
            height=36,
            corner_radius=18,
            font=ctk.CTkFont(family="Microsoft YaHei", size=13),
            fg_color=("#F9FAFB", "#262930"),
            border_color=("#D1D5DB", "#374151"),
            text_color=("#111827", "#F9FAFB")
        )
        self.entry_name.insert(0, script_name)
        self.entry_name.pack(side="left", fill="x", expand=True, padx=(5, 0))

        # 功能说明
        row_desc = ctk.CTkFrame(content, fg_color="transparent")
        row_desc.pack(fill="x", pady=6)
        ctk.CTkLabel(row_desc, text="功能说明:", font=parent.font_main, text_color=parent.colors["text"], width=70, anchor="w").pack(side="left")
        self.entry_desc = ctk.CTkEntry(
            row_desc,
            height=36,
            corner_radius=18,
            font=ctk.CTkFont(family="Microsoft YaHei", size=13),
            fg_color=("#F9FAFB", "#262930"),
            border_color=("#D1D5DB", "#374151"),
            text_color=("#111827", "#F9FAFB")
        )
        self.entry_desc.insert(0, script_desc)
        self.entry_desc.pack(side="left", fill="x", expand=True, padx=(5, 0))

        # 代码编辑区
        ctk.CTkLabel(content, text="代码编辑区 (Python 3):", font=parent.font_main, text_color=parent.colors["text"]).pack(anchor="w", pady=(10, 4))
        self.txt_code = ctk.CTkTextbox(
            content,
            font=ctk.CTkFont(family="Consolas", size=13),
            wrap="none",
            border_width=1,
            border_color=("#D1D5DB", "#374151"),
            fg_color=("#FFFFFF", "#262930"),
            text_color=("#111827", "#F9FAFB")
        )
        self.txt_code.insert("1.0", script_code)
        self.txt_code.pack(fill="both", expand=True, pady=(0, 10))

        # 加速滚动
        def speed_up_edit_scroll(event):
            scroll_units = int(-1 * (event.delta / 120) * 4)
            self.txt_code.yview_scroll(scroll_units, "units")
            return "break"
        self.txt_code.bind("<MouseWheel>", speed_up_edit_scroll)
        if hasattr(self.txt_code, "_textbox"):
            self.txt_code._textbox.bind("<MouseWheel>", speed_up_edit_scroll)

        # 底部按钮区
        btn_frame = ctk.CTkFrame(self, height=60, fg_color="transparent")
        btn_frame.pack(fill="x", side="bottom", padx=24, pady=10)

        btn_cancel = ctk.CTkButton(
            btn_frame, text="取消", fg_color="#F3F4F6", text_color="#1F2937", hover_color="#E5E7EB",
            font=ctk.CTkFont(family="Microsoft YaHei", size=13, weight="bold"), height=36, corner_radius=18,
            command=self.destroy
        )
        btn_cancel.pack(side="left")

        btn_save = ctk.CTkButton(
            btn_frame, text="保存脚本", fg_color="#10B981", text_color="white", hover_color="#059669",
            font=ctk.CTkFont(family="Microsoft YaHei", size=13, weight="bold"), height=36, corner_radius=18,
            command=self.save_script
        )
        btn_save.pack(side="right")

    def save_script(self):
        name = self.entry_name.get().strip()
        desc = self.entry_desc.get().strip()
        code = self.txt_code.get("1.0", tk.END)

        if not name:
            return messagebox.showwarning("提示", "请输入脚本名称")

        from modules.script_manager import save_script, load_all_scripts
        new_id = save_script(self.script_id, name, desc, self.script_type, code)

        self.parent.local_scripts = load_all_scripts()
        self.parent.load_scripts_to_tree()
        self.parent.script_tree.selection_set(new_id)

        messagebox.showinfo("成功", "脚本保存成功！")
        self.destroy()


class AIPromptDialog(ctk.CTkToplevel):
    """
    生成 AI 提示词的引导式对话框。
    """
    def __init__(self, parent, project_data):
        super().__init__(parent)
        self.parent = parent
        self.project_data = project_data

        self.title("生成 AI 脚本提示词")
        self.geometry("620x620")
        self.resizable(True, True)

        # 居中显示
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        x = (sw - 620) // 2
        y = (sh - 620) // 2
        self.geometry(f"620x620+{x}+{y}")
        self.transient(parent)
        self.grab_set()

        self.configure(fg_color=("#F9FAFB", "#1A1D24"))

        # 顶边条
        accent_strip = ctk.CTkFrame(self, height=4, fg_color="#6366F1", corner_radius=0)
        accent_strip.pack(fill="x", side="top")

        self.setup_ui()

    def _section_label(self, parent, text):
        lbl = ctk.CTkLabel(parent, text=text, font=ctk.CTkFont(family="Microsoft YaHei", size=13, weight="bold"), text_color="#374151")
        lbl.pack(anchor="w", padx=10, pady=(15, 2))

    def _make_option_menu(self, parent, values, variable, command=None):
        combo = ctk.CTkOptionMenu(
            parent,
            values=values,
            variable=variable,
            command=command,
            fg_color=("#F3F4F6", "#374151"),
            text_color=("#1F2937", "#E5E7EB"),
            button_color=("#F3F4F6", "#374151"),
            button_hover_color=("#E5E7EB", "#4B5563"),
            height=32,
            corner_radius=16
        )
        combo.pack(fill="x", pady=2)
        _apply_custom_arrow(combo)
        return combo

    def _make_textbox(self, parent, height=80, placeholder=None):
        if placeholder:
            ctk.CTkLabel(
                parent,
                text=placeholder,
                font=ctk.CTkFont(family="Microsoft YaHei", size=11),
                text_color="#64748B",
                wraplength=520,
                justify="left"
            ).pack(anchor="w", padx=10, pady=(0, 2))
        box = ctk.CTkTextbox(
            parent,
            height=height,
            border_width=1,
            border_color=("#D1D5DB", "#374151"),
            fg_color=("#FFFFFF", "#262930"),
            text_color=("#111827", "#F9FAFB")
        )
        box.pack(fill="x", padx=10, pady=5)
        return box

    def _textbox_value_without_placeholder(self, textbox, placeholder):
        value = textbox.get("1.0", tk.END).strip()
        if value == (placeholder or "").strip():
            return ""
        return value

    def _on_prompt_tab_changed(self):
        self.after(1, self._update_prompt_tab_text_colors)

    def _update_prompt_tab_text_colors(self):
        segmented = getattr(self.prompt_tabview, "_segmented_button", None)
        buttons = getattr(segmented, "_buttons_dict", {}) if segmented is not None else {}
        current = self.prompt_tabview.get() if hasattr(self, "prompt_tabview") else None
        unselected_text = "#F9FAFB" if ctk.get_appearance_mode().lower() == "dark" else "#111827"
        for name, button in buttons.items():
            try:
                button.configure(text_color="#FFFFFF" if name == current else unselected_text)
            except Exception:
                pass

    def setup_ui(self):
        # 底部固定按钮区（设为透明以融合背景，去掉白色块）
        bottom_frame = ctk.CTkFrame(self, height=56, fg_color=("#F9FAFB", "#1A1D24"), corner_radius=0)
        bottom_frame.pack(side="bottom", fill="x", pady=0)

        # 滚动内容区
        scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        scroll.pack(fill="both", expand=True, padx=20, pady=(15, 6))

        self.prompt_tabview = ctk.CTkTabview(
            scroll,
            fg_color=("#FFFFFF", "#1F232B"),
            segmented_button_fg_color=("#E5E7EB", "#2F3541"),
            segmented_button_selected_color=("#6366F1", "#6366F1"),
            segmented_button_selected_hover_color=("#4F46E5", "#4F46E5"),
            segmented_button_unselected_color=("#E5E7EB", "#2F3541"),
            segmented_button_unselected_hover_color=("#D1D5DB", "#374151"),
            text_color=("#111827", "#F9FAFB"),
            corner_radius=14,
            command=self._on_prompt_tab_changed,
        )
        self.prompt_tabview.pack(fill="x", padx=0, pady=(0, 10))
        manual_tab = self.prompt_tabview.add("参数选项")
        target_tab = self.prompt_tabview.add("目标导向")
        for tab in (manual_tab, target_tab):
            tab.configure(fg_color="transparent")
        self._update_prompt_tab_text_colors()

        # 1-6. 下拉选单配置区（一行两个，共三行，节省空间）
        # Row 1: 1. 脚本用途 & 2. 数据范围
        row1 = ctk.CTkFrame(manual_tab, fg_color="transparent")
        row1.pack(fill="x", pady=6)
        row1.columnconfigure(0, weight=1, uniform="row1")
        row1.columnconfigure(1, weight=1, uniform="row1")

        # Col 1: 脚本用途
        col1_1 = ctk.CTkFrame(row1, fg_color="transparent")
        col1_1.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        ctk.CTkLabel(col1_1, text="1. 脚本用途", font=ctk.CTkFont(family="Microsoft YaHei", size=13, weight="bold"), text_color="#374151").pack(anchor="w", pady=(0, 2))
        self.var_purpose = ctk.StringVar(value="绘制 F0 曲线图")
        purposes = ["绘制 F0 曲线图", "绘制 F0 分布图", "绘制 F1/F2 元音空间图", "绘制共振峰轨迹图", "绘制异常/质量检查图", "自定义图表"]
        self._make_option_menu(col1_1, purposes, self.var_purpose)

        # Col 2: 数据范围
        col1_2 = ctk.CTkFrame(row1, fg_color="transparent")
        col1_2.grid(row=0, column=1, sticky="nsew", padx=(10, 0))
        ctk.CTkLabel(col1_2, text="2. 数据范围", font=ctk.CTkFont(family="Microsoft YaHei", size=13, weight="bold"), text_color="#374151").pack(anchor="w", pady=(0, 2))
        self.var_scope = ctk.StringVar(value="只使用纳入分析的条目")
        scopes = ["只使用纳入分析的条目", "包含已排除条目", "只使用当前发音人", "使用全部发音人", "只使用当前选中分组", "手动指定分组"]
        self._make_option_menu(col1_2, scopes, self.var_scope, command=self.on_scope_change)

        self.frame_custom_scope = ctk.CTkFrame(col1_2, fg_color="transparent")
        self.entry_custom_scope = ctk.CTkEntry(
            self.frame_custom_scope,
            placeholder_text="输入指定的分组名称，多个以逗号分隔...",
            height=36,
            corner_radius=18,
            font=ctk.CTkFont(family="Microsoft YaHei", size=13),
            fg_color=("#F9FAFB", "#262930"),
            border_color=("#D1D5DB", "#374151"),
            text_color=("#111827", "#F9FAFB")
        )
        self.entry_custom_scope.pack(fill="x", pady=2)

        # Row 2: 3. 分组方式 & 4. 图表形式
        row2 = ctk.CTkFrame(manual_tab, fg_color="transparent")
        row2.pack(fill="x", pady=6)
        row2.columnconfigure(0, weight=1, uniform="row2")
        row2.columnconfigure(1, weight=1, uniform="row2")

        # Col 1: 分组方式
        col2_1 = ctk.CTkFrame(row2, fg_color="transparent")
        col2_1.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        ctk.CTkLabel(col2_1, text="3. 分组方式", font=ctk.CTkFont(family="Microsoft YaHei", size=13, weight="bold"), text_color="#374151").pack(anchor="w", pady=(0, 2))
        self.var_grouping = ctk.StringVar(value="按声调/分组")
        groupings = ["按声调/分组", "按发音人", "按音节位置", "按分析模式", "不分组", "自定义分组字段"]
        self._make_option_menu(col2_1, groupings, self.var_grouping, command=self.on_grouping_change)

        self.frame_custom_grouping = ctk.CTkFrame(col2_1, fg_color="transparent")
        self.entry_custom_grouping = ctk.CTkEntry(
            self.frame_custom_grouping,
            placeholder_text="输入自定义的分组字典键名...",
            height=36,
            corner_radius=18,
            font=ctk.CTkFont(family="Microsoft YaHei", size=13),
            fg_color=("#F9FAFB", "#262930"),
            border_color=("#D1D5DB", "#374151"),
            text_color=("#111827", "#F9FAFB")
        )
        self.entry_custom_grouping.pack(fill="x", pady=2)

        # Col 2: 图表形式
        col2_2 = ctk.CTkFrame(row2, fg_color="transparent")
        col2_2.grid(row=0, column=1, sticky="nsew", padx=(10, 0))
        ctk.CTkLabel(col2_2, text="4. 图表形式", font=ctk.CTkFont(family="Microsoft YaHei", size=13, weight="bold"), text_color="#374151").pack(anchor="w", pady=(0, 2))
        self.var_chart_style = ctk.StringVar(value="折线图")
        styles = ["折线图", "散点图", "箱线图", "小提琴图", "热力图", "轨迹图", "多子图分面", "自定义"]
        self._make_option_menu(col2_2, styles, self.var_chart_style)

        # Row 3: 5. 横轴 & 6. 纵轴
        row3 = ctk.CTkFrame(manual_tab, fg_color="transparent")
        row3.pack(fill="x", pady=6)
        row3.columnconfigure(0, weight=1, uniform="row3")
        row3.columnconfigure(1, weight=1, uniform="row3")

        # Col 1: 横轴
        col3_1 = ctk.CTkFrame(row3, fg_color="transparent")
        col3_1.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        ctk.CTkLabel(col3_1, text="5. 横轴", font=ctk.CTkFont(family="Microsoft YaHei", size=13, weight="bold"), text_color="#374151").pack(anchor="w", pady=(0, 2))
        self.var_xaxis = ctk.StringVar(value="归一化时间 0-1")
        xaxes = ["归一化时间 0-1", "真实时间 秒", "采样点序号", "音节位置", "自定义"]
        self._make_option_menu(col3_1, xaxes, self.var_xaxis)

        # Col 2: 纵轴
        col3_2 = ctk.CTkFrame(row3, fg_color="transparent")
        col3_2.grid(row=0, column=1, sticky="nsew", padx=(10, 0))
        ctk.CTkLabel(col3_2, text="6. 纵轴", font=ctk.CTkFont(family="Microsoft YaHei", size=13, weight="bold"), text_color="#374151").pack(anchor="w", pady=(0, 2))
        self.var_yaxis = ctk.StringVar(value="F0 Hz")
        yaxes = ["F0 Hz", "F0 T 值", "F1 Hz", "F2 Hz", "时长", "自定义"]
        self._make_option_menu(col3_2, yaxes, self.var_yaxis)

        # 7. 统计处理
        self._section_label(manual_tab, "7. 统计处理")
        stats_frame = ctk.CTkFrame(manual_tab, fg_color="transparent")
        stats_frame.pack(fill="x", padx=10, pady=5)

        stats_options = ["绘制个体曲线", "绘制均值", "绘制中位数", "绘制标准差阴影", "绘制置信区间", "忽略 NaN", "过滤明显异常值"]
        self.var_stats = {}
        for idx, opt in enumerate(stats_options):
            self.var_stats[opt] = ctk.BooleanVar(value=True if opt in ["绘制均值", "绘制标准差阴影", "忽略 NaN"] else False)
            cb = ctk.CTkCheckBox(
                stats_frame, text=opt, variable=self.var_stats[opt], height=24,
                checkbox_width=18, checkbox_height=18,
                corner_radius=1000,
                fg_color=("#3B82F6", "#2563EB"), hover_color=("#9CA3AF", "#4B5563"), border_color=("#4B5563", "#9CA3AF"),
                font=ctk.CTkFont(family="Microsoft YaHei", size=12)
            )
            cb.grid(row=idx//2, column=idx%2, sticky="w", padx=5, pady=4)

        # 8. 输出要求
        self._section_label(manual_tab, "8. 输出要求")
        out_frame = ctk.CTkFrame(manual_tab, fg_color="transparent")
        out_frame.pack(fill="x", padx=10, pady=5)

        # 标题
        ctk.CTkLabel(out_frame, text="图表标题:", font=ctk.CTkFont(family="Microsoft YaHei", size=12)).grid(row=0, column=0, sticky="w", pady=4)
        self.entry_title = ctk.CTkEntry(
            out_frame,
            placeholder_text="例如：F0 均值图",
            height=36,
            corner_radius=18,
            font=ctk.CTkFont(family="Microsoft YaHei", size=13),
            fg_color=("#F9FAFB", "#262930"),
            border_color=("#D1D5DB", "#374151"),
            text_color=("#111827", "#F9FAFB")
        )
        self.entry_title.insert(0, "F0 分组均值折线图")
        self.entry_title.grid(row=0, column=1, sticky="ew", padx=(10, 0), pady=4)

        # 文件名
        ctk.CTkLabel(out_frame, text="文件名:", font=ctk.CTkFont(family="Microsoft YaHei", size=12)).grid(row=1, column=0, sticky="w", pady=4)
        self.entry_filename = ctk.CTkEntry(
            out_frame,
            placeholder_text="例如：contour.png",
            height=36,
            corner_radius=18,
            font=ctk.CTkFont(family="Microsoft YaHei", size=13),
            fg_color=("#F9FAFB", "#262930"),
            border_color=("#D1D5DB", "#374151"),
            text_color=("#111827", "#F9FAFB")
        )
        self.entry_filename.insert(0, "contour.png")
        self.entry_filename.grid(row=1, column=1, sticky="ew", padx=(10, 0), pady=4)

        # 复选框样式修改为一致的圆形
        self.var_output_table = ctk.BooleanVar(value=False)
        self.var_show_legend = ctk.BooleanVar(value=True)
        self.var_use_chinese = ctk.BooleanVar(value=True)

        cb_table = ctk.CTkCheckBox(
            out_frame, text="同时输出数据表", variable=self.var_output_table, height=24,
            checkbox_width=18, checkbox_height=18,
            corner_radius=1000,
            fg_color=("#3B82F6", "#2563EB"), hover_color=("#9CA3AF", "#4B5563"), border_color=("#4B5563", "#9CA3AF"),
            font=ctk.CTkFont(family="Microsoft YaHei", size=12)
        )
        cb_table.grid(row=2, column=0, columnspan=2, sticky="w", pady=4)

        cb_legend = ctk.CTkCheckBox(
            out_frame, text="显示图例", variable=self.var_show_legend, height=24,
            checkbox_width=18, checkbox_height=18,
            corner_radius=1000,
            fg_color=("#3B82F6", "#2563EB"), hover_color=("#9CA3AF", "#4B5563"), border_color=("#4B5563", "#9CA3AF"),
            font=ctk.CTkFont(family="Microsoft YaHei", size=12)
        )
        cb_legend.grid(row=3, column=0, sticky="w", pady=4)

        cb_chinese = ctk.CTkCheckBox(
            out_frame, text="图中使用中文标签", variable=self.var_use_chinese, height=24,
            checkbox_width=18, checkbox_height=18,
            corner_radius=1000,
            fg_color=("#3B82F6", "#2563EB"), hover_color=("#9CA3AF", "#4B5563"), border_color=("#4B5563", "#9CA3AF"),
            font=ctk.CTkFont(family="Microsoft YaHei", size=12)
        )
        cb_chinese.grid(row=3, column=1, sticky="w", pady=4)

        out_frame.grid_columnconfigure(1, weight=1)

        # 9. 用户自定义需求
        self._section_label(manual_tab, "9. 补充具体需求")
        self.txt_custom = ctk.CTkTextbox(
            manual_tab, height=80,
            border_width=1,
            border_color=("#D1D5DB", "#374151"),
            fg_color=("#FFFFFF", "#262930"),
            text_color=("#111827", "#F9FAFB")
        )
        self.txt_custom.pack(fill="x", padx=10, pady=5)

        self._build_goal_oriented_tab(target_tab)

        # 10. 提示词预览
        self._section_label(scroll, "10. 提示词生成预览")
        self.txt_preview = ctk.CTkTextbox(
            scroll, height=150, font=ctk.CTkFont(family="Consolas", size=11),
            border_width=1,
            border_color=("#D1D5DB", "#374151"),
            fg_color=("#FFFFFF", "#262930"),
            text_color=("#111827", "#F9FAFB")
        )
        self.txt_preview.pack(fill="x", padx=10, pady=5)

        # 加速 main dialog 滚动与内部文本框滚动
        def _wheel_steps(delta, multiplier):
            if delta == 0:
                return 0
            if abs(delta) >= 120:
                return -int(delta / 120) * multiplier
            return -1 * multiplier if delta > 0 else multiplier

        def speed_up_main_scroll(event):
            canvas = getattr(scroll, "_parent_canvas", None)
            if canvas is not None:
                delta = getattr(event, "delta", 0)
                if delta != 0:
                    try:
                        canvas.configure(yscrollincrement=8)
                    except Exception:
                        pass
                    steps = _wheel_steps(delta, 12)
                    canvas.yview_scroll(steps, "units")
                return "break"

        def safe_mousewheel_bind(widget, handler):
            try:
                widget.bind("<MouseWheel>", handler, add="+")
            except (NotImplementedError, tk.TclError, AttributeError):
                for attr in ("_canvas", "_text_label", "_image_label"):
                    child = getattr(widget, attr, None)
                    if child is not None:
                        try:
                            child.bind("<MouseWheel>", handler, add="+")
                        except (NotImplementedError, tk.TclError, AttributeError):
                            pass

        def bind_scroll_recursive(w):
            if isinstance(w, ctk.CTkTextbox):
                def speed_up_tb_scroll(event):
                    delta = getattr(event, "delta", 0)
                    if delta != 0:
                        steps = _wheel_steps(delta, 8)
                        w.yview_scroll(steps, "units")
                    return "break"
                safe_mousewheel_bind(w, speed_up_tb_scroll)
                if hasattr(w, "_textbox"):
                    safe_mousewheel_bind(w._textbox, speed_up_tb_scroll)
                return

            safe_mousewheel_bind(w, speed_up_main_scroll)
            for child in w.winfo_children():
                bind_scroll_recursive(child)

        bind_scroll_recursive(scroll)

        # 底部按钮 (padding 调小为 10，贴合底部并美化布局)
        btn_cancel = ctk.CTkButton(
            bottom_frame, text="取消", fg_color="#F3F4F6", text_color="#1F2937", hover_color="#E5E7EB",
            font=ctk.CTkFont(family="Microsoft YaHei", size=13, weight="bold"), height=36, corner_radius=18,
            command=self.destroy
        )
        btn_cancel.pack(side="left", padx=20, pady=10)

        btn_preview = ctk.CTkButton(
            bottom_frame, text="仅生成预览", fg_color="#E2E8F0", text_color="#1F2937", hover_color="#CBD5E1",
            font=ctk.CTkFont(family="Microsoft YaHei", size=13, weight="bold"), height=36, corner_radius=18,
            command=self.generate_preview
        )
        btn_preview.pack(side="left", padx=10, pady=10)

        btn_copy = ctk.CTkButton(
            bottom_frame, text="生成并复制提示词", fg_color="#6366F1", text_color="white", hover_color="#4F46E5",
            font=ctk.CTkFont(family="Microsoft YaHei", size=13, weight="bold"), height=36, corner_radius=18,
            command=self.generate_and_copy
        )
        btn_copy.pack(side="right", padx=20, pady=10)

        # 初始打包隐藏
        self.on_scope_change(self.var_scope.get())
        self.on_grouping_change(self.var_grouping.get())

    def _build_goal_oriented_tab(self, target_tab):
        intro = ctk.CTkLabel(
            target_tab,
            text="这个页面按研究目标生成提示词。你只需要说明想解决什么问题，AI 会自己选择合适的图表形式和统计处理。",
            font=ctk.CTkFont(family="Microsoft YaHei", size=12),
            text_color="#64748B",
            wraplength=520,
            justify="left"
        )
        intro.pack(anchor="w", padx=10, pady=(8, 10))

        row1 = ctk.CTkFrame(target_tab, fg_color="transparent")
        row1.pack(fill="x", pady=6)
        row1.columnconfigure(0, weight=1, uniform="target1")
        row1.columnconfigure(1, weight=1, uniform="target1")

        col_goal = ctk.CTkFrame(row1, fg_color="transparent")
        col_goal.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        ctk.CTkLabel(col_goal, text="1. 想实现的目标", font=ctk.CTkFont(family="Microsoft YaHei", size=13, weight="bold"), text_color="#374151").pack(anchor="w", pady=(0, 2))
        self.var_target_goal = ctk.StringVar(value="比较不同分组的声学差异")
        target_goals = [
            "比较不同分组的声学差异",
            "比较不同发音人的差异",
            "展示单个发音人的整体趋势",
            "展示一个词或一组词的轨迹",
            "寻找异常或质量问题",
            "制作论文用汇总图",
            "探索数据中的模式",
            "自定义目标",
        ]
        self._make_option_menu(col_goal, target_goals, self.var_target_goal)

        col_data = ctk.CTkFrame(row1, fg_color="transparent")
        col_data.grid(row=0, column=1, sticky="nsew", padx=(10, 0))
        ctk.CTkLabel(col_data, text="2. 关注的数据", font=ctk.CTkFont(family="Microsoft YaHei", size=13, weight="bold"), text_color="#374151").pack(anchor="w", pady=(0, 2))
        self.var_target_data = ctk.StringVar(value="使用全部纳入分析条目")
        target_data = [
            "使用全部纳入分析条目",
            "只看某些分组",
            "只看某些发音人",
            "比较两个或多个条件",
            "包含已排除条目做质量检查",
            "自定义范围",
        ]
        self._make_option_menu(col_data, target_data, self.var_target_data)

        row2 = ctk.CTkFrame(target_tab, fg_color="transparent")
        row2.pack(fill="x", pady=6)
        row2.columnconfigure(0, weight=1, uniform="target2")
        row2.columnconfigure(1, weight=1, uniform="target2")

        col_measure = ctk.CTkFrame(row2, fg_color="transparent")
        col_measure.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        ctk.CTkLabel(col_measure, text="3. 主要观察指标", font=ctk.CTkFont(family="Microsoft YaHei", size=13, weight="bold"), text_color="#374151").pack(anchor="w", pady=(0, 2))
        self.var_target_measure = ctk.StringVar(value="由 AI 根据目标选择")
        measures = ["由 AI 根据目标选择", "F0 走势", "F0 分布", "F0 T 值", "F1/F2 空间", "共振峰轨迹", "时长", "多个指标组合"]
        self._make_option_menu(col_measure, measures, self.var_target_measure)

        col_output = ctk.CTkFrame(row2, fg_color="transparent")
        col_output.grid(row=0, column=1, sticky="nsew", padx=(10, 0))
        ctk.CTkLabel(col_output, text="4. 输出风格", font=ctk.CTkFont(family="Microsoft YaHei", size=13, weight="bold"), text_color="#374151").pack(anchor="w", pady=(0, 2))
        self.var_target_output = ctk.StringVar(value="优先生成一张清晰图表")
        target_outputs = ["优先生成一张清晰图表", "图表和统计表都要", "多张分面图", "论文风格高分辨率图", "教学展示风格", "自定义输出"]
        self._make_option_menu(col_output, target_outputs, self.var_target_output)

        self._section_label(target_tab, "5. 具体目标")
        self.placeholder_target_goal = "例如：我想比较阴平、阳平、上声、去声四组在归一化时间上的 F0 走势差异，最好能看出均值和组内离散程度。"
        self.txt_target_goal = self._make_textbox(
            target_tab,
            height=90,
            placeholder=self.placeholder_target_goal
        )

        self._section_label(target_tab, "6. 指定对象或筛选条件")
        self.placeholder_target_scope = "例如：只看张三和李四；只看“实验组A”；排除已经标记忽略的条目。没有特殊限制可以留空。"
        self.txt_target_scope = self._make_textbox(
            target_tab,
            height=70,
            placeholder=self.placeholder_target_scope
        )

        self._section_label(target_tab, "7. 输出偏好")
        self.placeholder_target_constraints = "例如：颜色要区分清楚；适合论文插图；图例放在右上；如果数据不足要在图里说明。"
        self.txt_target_constraints = self._make_textbox(
            target_tab,
            height=70,
            placeholder=self.placeholder_target_constraints
        )

    def on_scope_change(self, val):
        if val == "手动指定分组":
            self.frame_custom_scope.pack(fill="x")
        else:
            self.frame_custom_scope.pack_forget()

    def on_grouping_change(self, val):
        if val == "自定义分组字段":
            self.frame_custom_grouping.pack(fill="x")
        else:
            self.frame_custom_grouping.pack_forget()

    def generate_prompt_dict(self):
        active_tab = self.prompt_tabview.get() if hasattr(self, "prompt_tabview") else "参数选项"
        if active_tab == "目标导向":
            goal_detail = self._textbox_value_without_placeholder(self.txt_target_goal, self.placeholder_target_goal)
            scope_detail = self._textbox_value_without_placeholder(self.txt_target_scope, self.placeholder_target_scope)
            constraints = self._textbox_value_without_placeholder(self.txt_target_constraints, self.placeholder_target_constraints)
            target_goal = self.var_target_goal.get()
            target_measure = self.var_target_measure.get()
            target_output = self.var_target_output.get()
            custom_lines = [
                f"目标导向模式：{target_goal}",
                f"关注数据：{self.var_target_data.get()}",
                f"主要观察指标：{target_measure}",
                f"输出风格：{target_output}",
            ]
            if goal_detail:
                custom_lines.append(f"用户具体目标：{goal_detail}")
            if scope_detail:
                custom_lines.append(f"筛选条件或指定对象：{scope_detail}")
            if constraints:
                custom_lines.append(f"输出偏好：{constraints}")
            return {
                "prompt_mode": "目标导向",
                "goal": target_goal,
                "data_range": self.var_target_data.get(),
                "group_by": "由 AI 根据目标自动选择",
                "chart_style": "由 AI 根据目标自动选择",
                "x_axis": "由 AI 根据目标自动选择",
                "y_axis": target_measure,
                "stats": ["由 AI 根据目标选择合适统计处理"],
                "title": target_goal,
                "filename": "goal_oriented_chart.png",
                "output_table": target_output in ("图表和统计表都要", "论文风格高分辨率图"),
                "show_legend": True,
                "use_chinese": True,
                "custom_desc": "\n".join(custom_lines),
                "target_goal_detail": goal_detail,
                "target_scope_detail": scope_detail,
                "target_constraints": constraints,
            }

        stats_selected = [k for k, v in self.var_stats.items() if v.get()]

        scope = self.var_scope.get()
        if scope == "手动指定分组":
            custom_grp = self.entry_custom_scope.get().strip()
            scope = f"手动指定分组 ({custom_grp or '未指定'})"

        grouping = self.var_grouping.get()
        if grouping == "自定义分组字段":
            custom_key = self.entry_custom_grouping.get().strip()
            grouping = f"自定义分组字段 ({custom_key or '未指定'})"

        selections = {
            "prompt_mode": "参数选项",
            "goal": self.var_purpose.get(),
            "data_range": scope,
            "group_by": grouping,
            "chart_style": self.var_chart_style.get(),
            "x_axis": self.var_xaxis.get(),
            "y_axis": self.var_yaxis.get(),
            "stats": stats_selected,
            "title": self.entry_title.get().strip() or "自定义图表",
            "filename": self.entry_filename.get().strip() or "custom_chart.png",
            "output_table": self.var_output_table.get(),
            "show_legend": self.var_show_legend.get(),
            "use_chinese": self.var_use_chinese.get(),
            "custom_desc": self.txt_custom.get("1.0", tk.END).strip()
        }
        return selections

    def generate_preview(self):
        from modules.script_prompt import generate_ai_prompt
        selections = self.generate_prompt_dict()
        prompt_text = generate_ai_prompt(self.project_data, selections)

        self.txt_preview.delete("1.0", tk.END)
        self.txt_preview.insert("1.0", prompt_text)

    def generate_and_copy(self):
        from modules.script_prompt import generate_ai_prompt
        selections = self.generate_prompt_dict()
        prompt_text = generate_ai_prompt(self.project_data, selections)

        self.clipboard_clear()
        self.clipboard_append(prompt_text)

        messagebox.showinfo("成功", "AI 脚本提示词已成功生成并复制到剪贴板！")
        self.destroy()


if __name__ == "__main__":
    app = ToolkitApp()
    app.mainloop()
