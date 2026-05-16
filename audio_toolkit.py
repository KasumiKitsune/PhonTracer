import os
import sys
import threading
import re
import subprocess
import platform
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import customtkinter as ctk
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
        if self._release_command:
            self.bind("<ButtonRelease-1>", self._on_release)
            
    def _on_release(self, event):
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
        self.title("音频段落编辑")
        
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
        self.scrollbar = ctk.CTkScrollbar(self.main_frame, orientation="horizontal", command=self.canvas.xview)
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
        self.canvas.bind("<Configure>", lambda e: self.render_canvas())

    def init_data(self):
        full_values = self.snd.values[0]
        target_sr = 2000
        step = max(1, int(self.snd.sampling_frequency / target_sr))
        self.envelope_data = full_values[::step]

    def on_zoom_change(self, val):
        self.px_per_sec = round(float(val) / 25) * 25
        self.update_zoom_label()
        if hasattr(self, '_zoom_timer'):
            self.after_cancel(self._zoom_timer)
        self._zoom_timer = self.after(50, self.render_canvas)

    def on_mousewheel(self, event):
        delta = event.delta if platform.system() == 'Darwin' else event.delta / 120
        self.canvas.xview_scroll(int(-1 * delta), "units")

    def on_ctrl_mousewheel(self, event):
        delta = event.delta if platform.system() == 'Darwin' else event.delta / 120
        new_zoom = self.px_per_sec * (1.2 if delta > 0 else 0.8)
        new_zoom = max(25, min(2000, round(new_zoom / 25) * 25))
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
        if self.mode == 'review': self.update_review_count()

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
            for pr in self.play_rects:
                x1, y1, x2, y2 = pr['bbox']
                if x1 <= canvas_x <= x2 and y1 <= canvas_y <= y2:
                    return self.canvas.config(cursor="hand2")
            for dr in self.delete_rects:
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
            for pr in self.play_rects:
                x1, y1, x2, y2 = pr['bbox']
                if x1 <= canvas_x <= x2 and y1 <= canvas_y <= y2:
                    return self.play_segment(pr['start'], pr['end'])
            for dr in self.delete_rects:
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
            if seg_idx is not None: self.toggle_delete_segment(seg_idx)

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
        super().__init__()
        self.title("PhonTracer - 独立音频处理套件")
        self.geometry("900x650")
        self.configure(fg_color="#F3F4F6")
        
        self.font_title = ctk.CTkFont(family="Microsoft YaHei", size=15, weight="bold")
        self.font_main = ctk.CTkFont(family="Microsoft YaHei", size=13)
        
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
                self.iconphoto(True, self.icon_photo) # True 会应用到子窗口
            except Exception:
                pass
        
        # 创建拖拽指示线 (在 setup_ui 之后，确保 self.tree_merge 已创建)
        self._drop_indicator = tk.Frame(self.tree_merge, bg="#3B82F6", height=2)
        self._drop_indicator.place_forget()
        
        try:
            import windnd
            windnd.hook_dropfiles(self, func=self.on_files_dropped)
        except ImportError:
            pass

    def setup_icons(self):
        icon_path = "icons"
        if not os.path.exists(icon_path):
            icon_path = os.path.join(os.path.dirname(__file__), "icons")
            
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

    def setup_ui(self):
        self.btn_kwargs = {"text_color": "white", "corner_radius": 20, "height": 38, "font": self.font_main}
        
        # 配置 Treeview 样式 (增大字体和行高)
        style = ttk.Style()
        style.theme_use("default") # 确保样式生效
        style.configure("Treeview", 
                        font=("Microsoft YaHei", 12), 
                        rowheight=35,
                        background="#FFFFFF",
                        fieldbackground="#FFFFFF",
                        foreground="#1F2937")
        style.configure("Treeview.Heading", 
                        font=("Microsoft YaHei", 12, "bold"),
                        background="#F3F4F6",
                        foreground="#374151")
        style.map("Treeview", background=[('selected', '#3B82F6')], foreground=[('selected', '#FFFFFF')])
        
        header_frame = ctk.CTkFrame(self, fg_color="white", corner_radius=0, height=60)
        header_frame.pack(fill=tk.X, side=tk.TOP)
        ctk.CTkLabel(header_frame, text="PhonTracer 配套音频工具", font=ctk.CTkFont(family="Microsoft YaHei", size=20, weight="bold"), text_color="#1F2937").pack(side=tk.LEFT, padx=20, pady=15)
        
        self.lbl_status = ctk.CTkLabel(header_frame, text="就绪", text_color="#10B981", font=self.font_main)
        self.lbl_status.pack(side=tk.RIGHT, padx=20)

        self.tabview = ctk.CTkTabview(self, corner_radius=12, fg_color="white", segmented_button_selected_color="#60A5FA")
        self.tabview.pack(fill=tk.BOTH, expand=True, padx=20, pady=20)
        
        self.tab_merge = self.tabview.add("多音频合并 (拼长音)")
        self.tab_split = self.tabview.add("长音频拆分 (按字表)")
        
        self.build_merge_tab()
        self.build_split_tab()
        
        self.progress = ctk.CTkProgressBar(self, height=6, progress_color="#3B82F6", fg_color="#E5E7EB")
        self.progress.set(0)
        
    def build_merge_tab(self):
        left_panel = ctk.CTkFrame(self.tab_merge, fg_color="transparent")
        left_panel.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(10, 5), pady=10)
        
        CTkReleaseButton(left_panel, text=" ＋ 添加音频文件", image=self.icons.get("plus"), compound="left", command=self.add_merge_files, **self.btn_kwargs).pack(fill=tk.X, pady=(0, 10))
        
        tree_container = ctk.CTkFrame(left_panel, fg_color="transparent")
        tree_container.pack(fill=tk.BOTH, expand=True)
        
        self.tree_merge = ttk.Treeview(tree_container, columns=("Path",), show="headings", height=15)
        self.tree_merge.heading("Path", text="待合并的音频文件路径 (可鼠标拖拽调整顺序)")
        
        self.merge_scroll = ctk.CTkScrollbar(tree_container, orientation="vertical", command=self.tree_merge.yview)
        self.tree_merge.configure(yscrollcommand=self.merge_scroll.set)
        
        self.merge_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree_merge.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.tree_merge.bind('<BackSpace>', self.remove_merge_file)
        self.tree_merge.bind('<Delete>', self.remove_merge_file)
        
        # 绑定拖拽事件
        self.tree_merge.bind("<Button-1>", self.on_tree_drag_start)
        self.tree_merge.bind("<B1-Motion>", self.on_tree_drag_motion)
        self.tree_merge.bind("<ButtonRelease-1>", self.on_tree_drag_drop)
        
        ctk.CTkLabel(left_panel, text="提示: 选中按 Delete 移除。直接拖拽条目可调整合并顺序。", font=ctk.CTkFont(family="Microsoft YaHei", size=11), text_color="#6B7280").pack(anchor="w", pady=5)

        right_panel = ctk.CTkFrame(self.tab_merge, fg_color="#F9FAFB", corner_radius=10, width=280)
        right_panel.pack(side=tk.RIGHT, fill=tk.Y, padx=(5, 10), pady=10)
        right_panel.pack_propagate(False)
        
        ctk.CTkLabel(right_panel, text="合并参数", font=self.font_title, text_color="#111827").pack(pady=15, padx=15, anchor="w")
        ctk.CTkLabel(right_panel, text="音频间隔 (插入静音秒数):", font=self.font_main).pack(padx=15, anchor="w")
        self.var_gap = ctk.StringVar(value="0.5")
        ctk.CTkEntry(right_panel, textvariable=self.var_gap, width=100).pack(padx=15, pady=5, anchor="w")
        
        CTkReleaseButton(right_panel, text=" 合并并导出音频", image=self.icons.get("save"), compound="left", fg_color="#10B981", hover_color="#059669", command=self.process_merge, **self.btn_kwargs).pack(side=tk.BOTTOM, fill=tk.X, padx=15, pady=15)
        CTkReleaseButton(right_panel, text=" 导入字表自动排序", image=self.icons.get("list"), compound="left", fg_color="#6366F1", hover_color="#4F46E5", command=self.import_wordlist_for_sort, **self.btn_kwargs).pack(side=tk.BOTTOM, fill=tk.X, padx=15, pady=(0, 5))
        CTkReleaseButton(right_panel, text=" 清空列表", fg_color="#EF4444", hover_color="#DC2626", command=self.clear_merge_list, **self.btn_kwargs).pack(side=tk.BOTTOM, fill=tk.X, padx=15, pady=(0, 5))

    def build_split_tab(self):
        top_panel = ctk.CTkFrame(self.tab_split, fg_color="transparent")
        top_panel.pack(side=tk.TOP, fill=tk.X, padx=10, pady=(10, 0))
        
        self.btn_sel_source = ctk.CTkButton(top_panel, text=" 选择长音频源", image=self.icons.get("audio"), compound="left", width=160, command=self.select_split_source, **self.btn_kwargs)
        self.btn_sel_source.pack(side=tk.LEFT, padx=(0, 10))
        
        self.btn_edit_segments = CTkReleaseButton(top_panel, text=" 音频段落编辑", image=self.icons.get("eye"), compound="left", width=160, fg_color="#F59E0B", hover_color="#D97706", command=self.open_visual_splitter, **self.btn_kwargs)
        self.btn_edit_segments.pack(side=tk.LEFT, padx=(0, 10))
        
        self.lbl_split_source = ctk.CTkLabel(top_panel, text="未选择", text_color="#6B7280", font=self.font_main)
        self.lbl_split_source.pack(side=tk.LEFT, fill=tk.X, expand=True)

        main_area = ctk.CTkFrame(self.tab_split, fg_color="transparent")
        main_area.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        left_panel = ctk.CTkFrame(main_area, fg_color="transparent")
        left_panel.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 5))
        
        lbl_frame = ctk.CTkFrame(left_panel, fg_color="transparent")
        lbl_frame.pack(fill=tk.X, pady=(0, 5))
        ctk.CTkLabel(lbl_frame, text="粘贴字表文本 或", font=self.font_title, text_color="#111827").pack(side=tk.LEFT)
        CTkReleaseButton(lbl_frame, text=" 导入字表文件", image=self.icons.get("import_white"), compound="left", fg_color="#6366F1", hover_color="#4F46E5", command=self.import_wordlist, **self.btn_kwargs).pack(side=tk.LEFT, padx=10)
        
        self.txt_wordlist = ctk.CTkTextbox(left_panel, corner_radius=8, border_width=1, border_color="#D1D5DB")
        self.txt_wordlist.pack(fill=tk.BOTH, expand=True)
        self.txt_wordlist.bind("<KeyRelease>", self.validate_wordlist)
        self.txt_wordlist.bind("<<Paste>>", lambda e: self.after(10, self.validate_wordlist))
        
        self.lbl_wordlist_status = ctk.CTkLabel(left_panel, text="字表为空", font=ctk.CTkFont(family="Microsoft YaHei", size=12), text_color="#6B7280")
        self.lbl_wordlist_status.pack(anchor="w", pady=5)
        
        right_panel = ctk.CTkFrame(main_area, fg_color="#F9FAFB", corner_radius=10, width=280)
        right_panel.pack(side=tk.RIGHT, fill=tk.Y, padx=(5, 0))
        right_panel.pack_propagate(False)
        
        ctk.CTkLabel(right_panel, text="拆分设置", font=self.font_title, text_color="#111827").pack(pady=15, padx=15, anchor="w")
        
        self.var_trim = ctk.BooleanVar(value=True)
        ctk.CTkSwitch(right_panel, text="智能剔除边缘空白杂音", variable=self.var_trim, font=self.font_main, progress_color="#10B981").pack(padx=15, pady=10, anchor="w")
        
        ctk.CTkLabel(right_panel, text="保存区段首尾缓冲 (秒):", font=self.font_main).pack(padx=15, pady=(10, 0), anchor="w")
        self.var_buffer = ctk.StringVar(value="0.1")
        ctk.CTkEntry(right_panel, textvariable=self.var_buffer, width=100).pack(padx=15, pady=5, anchor="w")

        CTkReleaseButton(right_panel, text=" 一键匹配 (字表与音频)", image=self.icons.get("check"), compound="left", fg_color="#F59E0B", hover_color="#D97706", command=self.match_segments_to_wordlist, **self.btn_kwargs).pack(side=tk.TOP, fill=tk.X, padx=15, pady=(10, 15))
        
        CTkReleaseButton(right_panel, text=" 拆分并发送到主程序", image=self.icons.get("tab_batch"), compound="left", fg_color="#3B82F6", hover_color="#2563EB", command=lambda: self.process_split(send_to_main=True), **self.btn_kwargs).pack(side=tk.BOTTOM, fill=tk.X, padx=15, pady=15)
        CTkReleaseButton(right_panel, text=" 仅拆分保存到目录", image=self.icons.get("save"), compound="left", fg_color="#10B981", hover_color="#059669", command=lambda: self.process_split(send_to_main=False), **self.btn_kwargs).pack(side=tk.BOTTOM, fill=tk.X, padx=15, pady=(0, 5))

    # ==========================
    # 交互回调与工具函数
    # ==========================
    
    def on_files_dropped(self, files):
        paths = [f.decode('gbk') if isinstance(f, bytes) else str(f) for f in files]
        audio_paths = [p for p in paths if p.lower().endswith(('.wav', '.mp3'))]
        if not audio_paths: return
        
        current_tab = self.tabview.get()
        if current_tab == "多音频合并 (拼长音)":
            for p in audio_paths:
                if p not in self.merge_files:
                    self.merge_files.append(p)
                    self.tree_merge.insert("", tk.END, values=(p,))
        else:
            self.split_source = audio_paths[0]
            self.lbl_split_source.configure(text=os.path.basename(audio_paths[0]))
            self.custom_segments = None  # 更换文件时重置编辑数据

    def add_merge_files(self):
        files = filedialog.askopenfilenames(filetypes=[("Audio Files", "*.wav *.mp3")])
        for f in files:
            if f not in self.merge_files:
                self.merge_files.append(f)
                self.tree_merge.insert("", tk.END, values=(f,))

    def remove_merge_file(self, event=None):
        selected = self.tree_merge.selection()
        for item in selected:
            val = self.tree_merge.item(item, 'values')[0]
            if val in self.merge_files: self.merge_files.remove(val)
            self.tree_merge.delete(item)

    def clear_merge_list(self):
        self.merge_files.clear()
        self.tree_merge.delete(*self.tree_merge.get_children())

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
        new_list = []
        for child in self.tree_merge.get_children():
            path = self.tree_merge.item(child, 'values')[0]
            new_list.append(path)
        self.merge_files = new_list

    def import_wordlist_for_sort(self):
        if not self.merge_files:
            return messagebox.showwarning("提示", "合并列表为空，请先添加音频文件")
            
        path = filedialog.askopenfilename(filetypes=[("Text Files", "*.txt"), ("All Files", "*.*")])
        if not path: return
        
        try:
            with open(path, 'r', encoding='utf-8') as f: text = f.read()
        except:
            try:
                with open(path, 'r', encoding='gbk') as f: text = f.read()
            except: return messagebox.showerror("错误", "读取文件失败")
            
        flat_words = parse_wordlist(text)
        if not flat_words: return messagebox.showwarning("提示", "字表解析结果为空")
        
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
            self.tree_merge.insert("", tk.END, values=(p,))
            
        messagebox.showinfo("排序完成", f"已根据字表重新排序 {len(used_indices)} 个文件。")

    def select_split_source(self):
        f = filedialog.askopenfilename(filetypes=[("Audio Files", "*.wav *.mp3")])
        if f:
            self.split_source = f
            self.lbl_split_source.configure(text=os.path.basename(f))
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
        
        threading.Thread(target=run, daemon=True).start()

    def set_loading(self, state, msg=""):
        if state:
            self.lbl_status.configure(text=msg, text_color="#3B82F6")
            self.progress.pack(fill=tk.X, side=tk.BOTTOM, padx=0, pady=0)
            self.progress.set(0)
            self.update_idletasks()
        else:
            self.lbl_status.configure(text="就绪" if not msg else msg, text_color="#10B981")
            self.progress.pack_forget()

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
                
        threading.Thread(target=run, daemon=True).start()

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
                
        threading.Thread(target=run, daemon=True).start()

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
                
        threading.Thread(target=run, daemon=True).start()

if __name__ == "__main__":
    app = AudioToolkitApp()
    app.mainloop()