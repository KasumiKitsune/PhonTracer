import tkinter as tk
from tkinter import messagebox
import customtkinter as ctk
import numpy as np
import parselmouth
import sounddevice as sd
import platform
from .ui_widgets import CTkReleaseButton

class VisualSplitter(ctk.CTkToplevel):
    def __init__(self, master, snd, icons, callback, existing_items=None, vad_segments=None):
        super().__init__(master)
        self.title("段落编辑器")
        
        w, h = 950, 550
        # 居中计算
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        x = (sw - w) // 2
        y = (sh - h) // 2
        self.geometry(f"{w}x{h}+{x}+{y}")
        
        self.attributes('-topmost', True)
        
        self.snd = snd
        self.icons = icons
        self.callback = callback
        
        # 三种模式：
        # 'edit'   - 已有字表匹配结果，微调边界
        # 'review' - VAD 自动检测结果预览，可删除/试听/微调边界
        # 'cut'    - 手动添加切分线（无 VAD 预检测时的兜底）
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
            # 将 VAD 元组转为字典格式，方便统一处理
            self.segments = [{'start': s, 'end': e, 'label': f'#{i+1}'} for i, (s, e) in enumerate(vad_segments)]
        else:
            self.mode = 'cut'
            self.segments = []
            
        self.cuts = [] 
        self.deleted_indices = set()  # 记录被标记为删除的区段索引
        
        self.px_per_sec = 100
        self.duration = self.snd.get_total_duration()
        self.dragging = None # {'seg_idx': int, 'bound': 'start'|'end'|'inner', 'inner_idx': int}
        self.play_rects = []
        self.delete_rects = []  # 删除按钮的点击区域
        
        self.setup_ui()
        self.init_data()
        
        if self.mode == 'edit':
            self.update_dynamic_labels()
            
        # 延迟一下等待窗口布局完成后计算自适应缩放
        self.after(100, self.auto_fit_scale)
        
    def auto_fit_scale(self):
        cw = self.canvas.winfo_width()
        if cw > 100:
            fit_scale = cw / self.duration
            
            # 用户需求：给每一段设置最小初始宽度 (防止过窄)
            min_px_per_sec = 20
            if self.segments:
                # 只计算有效段落的平均时长（排除段落之间的大段空白）
                total_seg_time = sum([s['end'] - s['start'] for s in self.segments])
                avg_seg_dur = total_seg_time / len(self.segments) if len(self.segments) > 0 else 0.5
                if avg_seg_dur < 0.1: avg_seg_dur = 0.1
                
                # 设定每段初始平均宽度至少 100 像素，这样看起来更舒展
                min_px_per_sec = 100 / avg_seg_dur
            
            # 取自适应宽度和段落最小宽度的较大者
            self.px_per_sec = max(min_px_per_sec, fit_scale)
            
            # 档位化：对齐到最近的 25px 倍数
            self.px_per_sec = round(self.px_per_sec / 25) * 25
            
            # 限制在 slider 的范围内
            self.px_per_sec = max(25, min(2000, self.px_per_sec))
            
            self.zoom_slider.set(self.px_per_sec)
            self.update_zoom_label()
        self.render_canvas()

    def update_zoom_label(self):
        if hasattr(self, 'lbl_zoom'):
            self.lbl_zoom.configure(text=f"缩放: {int(self.px_per_sec)}")

    def setup_ui(self):
        self.configure(fg_color="#F9FAFB")
        
        # 顶部说明栏
        info_frame = ctk.CTkFrame(self, height=45, fg_color="#F3F4F6", corner_radius=0)
        info_frame.pack(side=tk.TOP, fill=tk.X)
        
        if self.mode == 'cut':
            msg = "操作说明：【左键】添加切分线，【右键】删除最近线，【滚轮】滚动，【Ctrl+滚轮】缩放波形。"
        elif self.mode == 'review':
            msg = "VAD 自动检测完成。【右键】删除噪声段，点击标签【试听】，拖拽红线【微调边界】。"
        else:
            msg = "【右键】删除错误段。词语模式下可拖动【蓝线】微调单字边界。完成后点击右下角确认。"
            
        ctk.CTkLabel(info_frame, text=msg, font=("Microsoft YaHei", 13), text_color="#1F2937").pack(side=tk.LEFT, padx=20, pady=10)
        
        # 底部控制栏
        bottom_frame = ctk.CTkFrame(self, height=70, fg_color="white", corner_radius=0)
        bottom_frame.pack(side=tk.BOTTOM, fill=tk.X)
        
        if self.mode == 'cut':
            CTkReleaseButton(bottom_frame, text="清空所有点", image=self.icons.get("cut"), compound="left",
                         fg_color="#FEE2E2", hover_color="#FECACA", text_color="#DC2626", corner_radius=20, height=36,
                         command=self.clear_cuts).pack(side=tk.LEFT, padx=20, pady=15)
                         
            self.lbl_count = ctk.CTkLabel(bottom_frame, text="当前切分点：0", font=("Microsoft YaHei", 13, "bold"), text_color="#4B5563")
            self.lbl_count.pack(side=tk.RIGHT, padx=20)
        
        if self.mode in ('review', 'edit'):
            # 统计标签
            self.lbl_count = ctk.CTkLabel(bottom_frame, text="", font=("Microsoft YaHei", 13, "bold"), text_color="#4B5563")
            self.lbl_count.pack(side=tk.RIGHT, padx=20)
            self.update_review_count()
        
        CTkReleaseButton(bottom_frame, text="确认并应用",
                     fg_color="#10B981", hover_color="#059669", corner_radius=20, height=36,
                     command=self.confirm).pack(side=tk.RIGHT, padx=20, pady=15)
        
        # 缩放控制
        zoom_frame = ctk.CTkFrame(bottom_frame, fg_color="transparent")
        zoom_frame.pack(side=tk.LEFT, padx=30, pady=15)
        self.lbl_zoom = ctk.CTkLabel(zoom_frame, text=f"缩放: {int(self.px_per_sec)}", font=("Microsoft YaHei", 13), text_color="#4B5563")
        self.lbl_zoom.pack(side=tk.LEFT, padx=5)
        # 档位制：from 25 to 2000
        self.zoom_slider = ctk.CTkSlider(zoom_frame, from_=25, to=2000, number_of_steps=79, command=self.on_zoom_change, button_color="#3B82F6", progress_color="#93C5FD")
        self.zoom_slider.set(self.px_per_sec)
        self.zoom_slider.pack(side=tk.LEFT)

        # 中间滚动区域
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
        if self.mode != 'edit': return
        word_idx = 0
        for i, seg in enumerate(self.segments):
            if i in self.deleted_indices:
                seg['dyn_label'] = "已剔除"
                seg['dyn_id'] = None
            else:
                if word_idx < len(self.original_words):
                    dyn_lbl = self.original_words[word_idx]['label']
                    seg['dyn_label'] = dyn_lbl
                    seg['dyn_id'] = self.original_words[word_idx]['id']
                    
                    # 词语模式：初始化或补齐内部分割点
                    if len(dyn_lbl) > 1:
                        target_splits = len(dyn_lbl) - 1
                        splits = seg.get('inner_splits', [])
                        if len(splits) != target_splits:
                            dur = seg['end'] - seg['start']
                            seg['inner_splits'] = [seg['start'] + dur * j / len(dyn_lbl) for j in range(1, len(dyn_lbl))]
                    else:
                        seg['inner_splits'] = []
                        
                    word_idx += 1
                else:
                    seg['dyn_label'] = "【未分配段】"
                    seg['dyn_id'] = None
                    seg['inner_splits'] = []

    def render_canvas(self):
        self.canvas_width = int(self.duration * self.px_per_sec)
        self.canvas_height = self.canvas.winfo_height()
        if self.canvas_height < 100: self.canvas_height = 400
        
        self.canvas.delete("all")
        self.canvas.config(scrollregion=(0, 0, self.canvas_width, self.canvas_height))
        
        # 1. 绘制片段背景
        if self.mode in ('edit', 'review'):
            for i, seg in enumerate(self.segments):
                x1 = seg['start'] * self.px_per_sec
                x2 = seg['end'] * self.px_per_sec
                bg_color = "#FEE2E2" if i in self.deleted_indices else "#EFF6FF"
                self.canvas.create_rectangle(x1, 0, x2, self.canvas_height, fill=bg_color, outline="")

        # 2. 绘制波形中心线
        mid_y = self.canvas_height / 2 + 20
        self.canvas.create_line(0, mid_y, self.canvas_width, mid_y, fill="#E5E7EB")
        
        # 3. 绘制波形 (切分以优化性能)
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
        
        # 4. 绘制时间轴刻度
        step_sec = 1 if self.px_per_sec > 50 else 5
        if self.px_per_sec > 200: step_sec = 0.5
        for t in np.arange(0, self.duration, step_sec):
            x = t * self.px_per_sec
            self.canvas.create_line(x, self.canvas_height-15, x, self.canvas_height, fill="#D1D5DB")
            self.canvas.create_text(x+2, self.canvas_height-8, text=f"{t}s", anchor=tk.W, font=("Arial", 9), fill="#6B7280")

        # 5. 绘制边界线与标签
        if self.mode == 'cut':
            for cut in self.cuts: self.draw_cut_line(cut)
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
                
                # --- 词语模式：绘制蓝线 ---
                inner_splits = seg.get('inner_splits', [])
                if not is_deleted and inner_splits:
                    for s_t in inner_splits:
                        s_x = s_t * self.px_per_sec
                        self.canvas.create_line(s_x, 0, s_x, self.canvas_height, fill="#3B82F6", width=2, dash=(4, 4))
                
                tag_y = 25
                display_label = seg.get('dyn_label', seg['label'])
                
                def create_pill_smooth(canvas, x, y, text, bg_color, tags):
                    text_id = canvas.create_text(x, y, text=text, font=("Microsoft YaHei", 10, "bold"), fill="white", tags=tags)
                    b = canvas.bbox(text_id)
                    h = b[3] - b[1] + 8
                    canvas.create_line(b[0], y, b[2], y, width=h, fill=bg_color, capstyle='round', tags=tags)
                    canvas.tag_raise(text_id)
                    return canvas.bbox(tags)

                if is_deleted:
                    cx = (x1 + x2) / 2
                    bbox = create_pill_smooth(self.canvas, cx, tag_y, f"✕ {display_label}", "#9CA3AF", f"btn_{i}")
                    self.delete_rects.append({'idx': i, 'bbox': bbox})
                    center_y = self.canvas_height / 2
                    self.canvas.create_line(cx, center_y, cx, center_y, width=44, fill="#EF4444", capstyle='round')
                    self.canvas.create_text(cx, center_y, text="✕", font=("Arial", 18, "bold"), fill="white")
                else:
                    # 如果是词语，分离出多个标签块
                    if len(display_label) > 1 and len(inner_splits) == len(display_label) - 1:
                        splits = [seg['start']] + inner_splits + [seg['end']]
                        char_colors = ["#3B82F6", "#10B981", "#EF4444", "#F59E0B", "#8B5CF6", "#14B8A6", "#EC4899", "#6366F1"]
                        for char_idx, char in enumerate(display_label):
                            c_s, c_e = splits[char_idx], splits[char_idx+1]
                            cx = (c_s + c_e) / 2 * self.px_per_sec
                            color = char_colors[char_idx % len(char_colors)]
                            bbox = create_pill_smooth(self.canvas, cx, tag_y, f"▶ {char}", color, f"btn_{i}_{char_idx}")
                            self.play_rects.append({'idx': i, 'start': c_s, 'end': c_e, 'bbox': bbox})
                    else:
                        cx = (x1 + x2) / 2
                        bbox = create_pill_smooth(self.canvas, cx, tag_y, f"▶ {display_label}", "#3B82F6", f"btn_{i}")
                        self.play_rects.append({'idx': i, 'start': seg['start'], 'end': seg['end'], 'bbox': bbox})

    def draw_cut_line(self, time_sec):
        x = time_sec * self.px_per_sec
        self.canvas.create_line(x, 0, x, self.canvas_height, fill="#EF4444", width=2)
        self.canvas.create_text(x + 5, 20, text=f"{time_sec:.3f}s", anchor=tk.W, fill="#B91C1C", font=("Arial", 10, "bold"))

    def clear_cuts(self):
        self.cuts = []
        self.render_canvas()
        if hasattr(self, 'lbl_count'): self.lbl_count.configure(text=f"当前切分点：0")

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
            
            # 红线边界
            if abs(seg['start'] - time_sec) * self.px_per_sec < tolerance: return i, 'start', None
            if abs(seg['end'] - time_sec) * self.px_per_sec < tolerance: return i, 'end', None
            
            # 蓝线边界
            if 'inner_splits' in seg:
                for split_idx, split_t in enumerate(seg['inner_splits']):
                    if abs(split_t - time_sec) * self.px_per_sec < tolerance:
                        return i, 'inner', split_idx
        return None, None, None

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
            idx, bound, _ = self._get_element_at_x(canvas_x)
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
                    
            idx, bound, inner_idx = self._get_element_at_x(canvas_x)
            if idx is not None:
                self.dragging = {'seg_idx': idx, 'bound': bound, 'inner_idx': inner_idx}
                
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
            splits = seg.get('inner_splits', [])
            
            # 强化拖拽约束逻辑
            if bound == 'start':
                max_limit = splits[0] if splits else seg['end']
                seg['start'] = min(time_sec, max_limit - 0.01)
            elif bound == 'end':
                min_limit = splits[-1] if splits else seg['start']
                seg['end'] = max(time_sec, min_limit + 0.01)
            elif bound == 'inner':
                inner_idx = self.dragging['inner_idx']
                min_limit = seg['start'] if inner_idx == 0 else splits[inner_idx - 1]
                max_limit = seg['end'] if inner_idx == len(splits) - 1 else splits[inner_idx + 1]
                splits[inner_idx] = max(min_limit + 0.01, min(time_sec, max_limit - 0.01))
                
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
                    # 识别是否修改了边界或内部蓝线
                    is_mod = (seg['start'] != seg.get('orig_start', seg['start'])) or \
                             (seg['end'] != seg.get('orig_end', seg['end'])) or \
                             (seg.get('inner_splits', []) != seg.get('orig_inner_splits', []))
                    
                    kept_segments.append({
                        'id': seg['dyn_id'],
                        'old_id': seg.get('id'),
                        'start': seg['start'],
                        'end': seg['end'],
                        'inner_splits': seg.get('inner_splits', []),
                        'is_modified': is_mod
                    })
            self.destroy()
            self.callback(kept_segments, True, len(self.deleted_indices))