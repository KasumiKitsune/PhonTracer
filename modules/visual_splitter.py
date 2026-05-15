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
        self.title("音频段落编辑")
        
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
        self.dragging = None # {'seg_idx': int, 'bound': 'start'|'end'}
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
            # 如果音频很短，fit_scale 会很大，我们限制一下最大值
            # 同样如果很长，限制一下最小值
            self.px_per_sec = max(20, min(2000, fit_scale))
            self.zoom_slider.set(self.px_per_sec)
        self.render_canvas()

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
            msg = "【右键】删除错误段，点击标签【试听】，拖拽红线【微调边界】。完成后点击右下角确认。"
            
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
        ctk.CTkLabel(zoom_frame, text="缩放:", font=("Microsoft YaHei", 13), text_color="#4B5563").pack(side=tk.LEFT, padx=5)
        self.zoom_slider = ctk.CTkSlider(zoom_frame, from_=20, to=2000, command=self.on_zoom_change, button_color="#3B82F6", progress_color="#93C5FD")
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
        self.px_per_sec = float(val)
        self.render_canvas()

    def on_mousewheel(self, event):
        if platform.system() == 'Darwin':
            delta = event.delta
        else:
            delta = event.delta / 120
        self.canvas.xview_scroll(int(-1 * delta), "units")

    def on_ctrl_mousewheel(self, event):
        if platform.system() == 'Darwin':
            delta = event.delta
        else:
            delta = event.delta / 120
        new_zoom = self.px_per_sec * (1.2 if delta > 0 else 0.8)
        new_zoom = max(20, min(3000, new_zoom))
        self.px_per_sec = new_zoom
        self.zoom_slider.set(new_zoom)
        self.render_canvas()

    def render_canvas(self):
        self.canvas_width = int(self.duration * self.px_per_sec)
        self.canvas_height = self.canvas.winfo_height()
        if self.canvas_height < 100: self.canvas_height = 400
        
        self.canvas.delete("all")
        self.canvas.config(scrollregion=(0, 0, self.canvas_width, self.canvas_height))
        
        # 1. 绘制片段背景 (edit 和 review 模式)
        if self.mode in ('edit', 'review'):
            for i, seg in enumerate(self.segments):
                x1 = seg['start'] * self.px_per_sec
                x2 = seg['end'] * self.px_per_sec
                if i in self.deleted_indices:
                    self.canvas.create_rectangle(x1, 0, x2, self.canvas_height, fill="#FEE2E2", outline="")
                else:
                    self.canvas.create_rectangle(x1, 0, x2, self.canvas_height, fill="#EFF6FF", outline="")

        # 2. 绘制波形中心线
        mid_y = self.canvas_height / 2 + 20 # 下移一点留出顶部空间给标签
        self.canvas.create_line(0, mid_y, self.canvas_width, mid_y, fill="#E5E7EB")
        
        # 3. 绘制波形
        draw_step = max(1, len(self.envelope_data) // self.canvas_width)
        draw_values = self.envelope_data[::draw_step]
        
        points = []
        n = len(draw_values)
        if n > 1:
            for i, val in enumerate(draw_values):
                x = (i / n) * self.canvas_width
                y = mid_y - (val * (self.canvas_height/2 - 30) * 0.9)
                points.extend([x, y])
            self.canvas.create_line(points, fill="#9CA3AF", width=1, tags="waveform")
        
        # 4. 绘制时间轴刻度
        step_sec = 1 if self.px_per_sec > 50 else 5
        if self.px_per_sec > 200: step_sec = 0.5
        
        for t in np.arange(0, self.duration, step_sec):
            x = t * self.px_per_sec
            self.canvas.create_line(x, self.canvas_height-15, x, self.canvas_height, fill="#D1D5DB")
            self.canvas.create_text(x+2, self.canvas_height-8, text=f"{t}s", anchor=tk.W, font=("Arial", 9), fill="#6B7280")

        # 5. 绘制切分线或片段边界
        if self.mode == 'cut':
            for cut in self.cuts:
                self.draw_cut_line(cut)
        elif self.mode in ('edit', 'review'):
            self.play_rects = []
            self.delete_rects = []
            for i, seg in enumerate(self.segments):
                is_deleted = i in self.deleted_indices
                x1 = seg['start'] * self.px_per_sec
                x2 = seg['end'] * self.px_per_sec
                
                # 绘制边界线
                line_color = "#D1D5DB" if is_deleted else "#EF4444"
                line_dash = (4, 4) if is_deleted else ()
                self.canvas.create_line(x1, 0, x1, self.canvas_height, fill=line_color, width=2, dash=line_dash, tags=f"line_start_{i}")
                self.canvas.create_line(x2, 0, x2, self.canvas_height, fill=line_color, width=2, dash=line_dash, tags=f"line_end_{i}")
                
                # 绘制顶部标签
                cx = (x1 + x2) / 2
                tag_y = 25
                
                # 获取动态计算的标签 (如果存在)
                display_label = seg.get('dyn_label', seg['label'])
                
                # 使用 create_line + capstyle='round' 模拟抗锯齿的药丸型/圆形背景
                def create_pill_smooth(canvas, x, y, text, bg_color, tags):
                    text_id = canvas.create_text(x, y, text=text, font=("Microsoft YaHei", 10, "bold"), fill="white", tags=tags)
                    b = canvas.bbox(text_id)
                    h = b[3] - b[1] + 8
                    # 用粗线条模拟药丸背景，线条的端点圆点(round)就是天然的抗锯齿圆角
                    canvas.create_line(b[0], y, b[2], y, width=h, fill=bg_color, capstyle='round', tags=tags)
                    canvas.tag_raise(text_id)
                    return canvas.bbox(tags)

                if is_deleted:
                    # 被删除段：灰色标签
                    lbl_text = f"✕ {display_label}"
                    bbox = create_pill_smooth(self.canvas, cx, tag_y, lbl_text, "#9CA3AF", f"btn_{i}")
                    # 可以点击取消删除
                    self.delete_rects.append({'idx': i, 'bbox': bbox})
                    
                    # 在区段中央绘制更精致的 ✕ 标识：利用粗线圆点模拟抗锯齿圆形
                    center_y = self.canvas_height / 2
                    # 线条长度为 0，端点为 round，即为一个完美的抗锯齿圆
                    self.canvas.create_line(cx, center_y, cx, center_y, width=44, fill="#EF4444", capstyle='round')
                    self.canvas.create_text(cx, center_y, text="✕", font=("Arial", 18, "bold"), fill="white")
                else:
                    # 正常段：蓝色标签 + 试听
                    lbl_text = f"▶ {display_label}"
                    bbox = create_pill_smooth(self.canvas, cx, tag_y, lbl_text, "#3B82F6", f"btn_{i}")
                    self.play_rects.append({'idx': i, 'bbox': bbox})

    def draw_cut_line(self, time_sec):
        x = time_sec * self.px_per_sec
        self.canvas.create_line(x, 0, x, self.canvas_height, fill="#EF4444", width=2, tags="cut_line")
        self.canvas.create_text(x + 5, 20, text=f"{time_sec:.3f}s", anchor=tk.W, fill="#B91C1C", font=("Arial", 10, "bold"))

    def clear_cuts(self):
        self.cuts = []
        self.render_canvas()
        self.update_count()

    def update_count(self):
        if hasattr(self, 'lbl_count'):
            self.lbl_count.configure(text=f"当前切分点：{len(self.cuts)}")

    def update_review_count(self):
        """更新 review 模式的统计信息"""
        if hasattr(self, 'lbl_count'):
            total = len(self.segments)
            deleted = len(self.deleted_indices)
            kept = total - deleted
            if deleted > 0:
                self.lbl_count.configure(
                    text=f"共 {total} 段 | 保留 {kept} 段 | 已移除 {deleted} 段",
                    text_color="#DC2626"
                )
            else:
                self.lbl_count.configure(
                    text=f"共 {total} 个检测区段",
                    text_color="#4B5563"
                )

    def update_dynamic_labels(self):
        """动态计算顺延后的标签，并在界面上实时显示"""
        if self.mode != 'edit': return
        
        word_idx = 0
        for i, seg in enumerate(self.segments):
            if i in self.deleted_indices:
                seg['dyn_label'] = "已剔除"
                seg['dyn_id'] = None
            else:
                if word_idx < len(self.original_words):
                    seg['dyn_label'] = self.original_words[word_idx]['label']
                    seg['dyn_id'] = self.original_words[word_idx]['id']
                    word_idx += 1
                else:
                    seg['dyn_label'] = "【未分配段】"
                    seg['dyn_id'] = None

    def toggle_delete_segment(self, idx):
        """切换某个区段的删除状态"""
        if idx in self.deleted_indices:
            self.deleted_indices.discard(idx)
        else:
            self.deleted_indices.add(idx)
            
        self.update_dynamic_labels()
        self.render_canvas()
        if self.mode == 'review':
            self.update_review_count()

    def _get_element_at_x(self, x):
        """编辑模式下，判断鼠标X坐标是否在某个边界线附近"""
        tolerance = 10
        time_sec = x / self.px_per_sec
        for i, seg in enumerate(self.segments):
            if i in self.deleted_indices:
                continue  # 跳过已删除的段，不允许拖拽
            if abs(seg['start'] - time_sec) * self.px_per_sec < tolerance: return i, 'start'
            if abs(seg['end'] - time_sec) * self.px_per_sec < tolerance: return i, 'end'
        return None, None

    def _get_segment_at_x(self, x):
        """判断鼠标X坐标落在哪个区段内"""
        time_sec = x / self.px_per_sec
        for i, seg in enumerate(self.segments):
            if seg['start'] <= time_sec <= seg['end']:
                return i
        return None

    def on_hover(self, event):
        canvas_x = self.canvas.canvasx(event.x)
        canvas_y = self.canvas.canvasy(event.y)
        
        # 1. 检查是否悬停在播放按钮上
        if self.mode in ('edit', 'review'):
            for pr in self.play_rects:
                x1, y1, x2, y2 = pr['bbox']
                if x1 <= canvas_x <= x2 and y1 <= canvas_y <= y2:
                    self.canvas.config(cursor="hand2")
                    return
            # 检查是否悬停在已删除段的标签上（可恢复）
            for dr in self.delete_rects:
                x1, y1, x2, y2 = dr['bbox']
                if x1 <= canvas_x <= x2 and y1 <= canvas_y <= y2:
                    self.canvas.config(cursor="hand2")
                    return
        
        # 2. 检查是否悬停在边界线上
        if self.mode in ('edit', 'review') and not self.dragging:
            idx, bound = self._get_element_at_x(canvas_x)
            if idx is not None:
                self.canvas.config(cursor="sb_h_double_arrow")
                return
                
        self.canvas.config(cursor="arrow")

    def on_click(self, event):
        canvas_x = self.canvas.canvasx(event.x)
        canvas_y = self.canvas.canvasy(event.y)
        time_sec = canvas_x / self.px_per_sec
        
        if self.mode in ('edit', 'review'):
            # 检查播放按钮点击
            for pr in self.play_rects:
                x1, y1, x2, y2 = pr['bbox']
                if x1 <= canvas_x <= x2 and y1 <= canvas_y <= y2:
                    self.play_segment(pr['idx'])
                    return
            
            # 检查已删除段标签点击（恢复）
            for dr in self.delete_rects:
                x1, y1, x2, y2 = dr['bbox']
                if x1 <= canvas_x <= x2 and y1 <= canvas_y <= y2:
                    self.toggle_delete_segment(dr['idx'])
                    return
                    
            # 检查边界拖拽
            idx, bound = self._get_element_at_x(canvas_x)
            if idx is not None:
                self.dragging = {'seg_idx': idx, 'bound': bound}
                
        elif self.mode == 'cut':
            if 0 <= time_sec <= self.duration:
                self.cuts.append(time_sec)
                self.cuts.sort()
                self.render_canvas() # 重绘以便看到所有点
                self.update_count()

    def on_motion(self, event):
        if self.dragging:
            canvas_x = self.canvas.canvasx(event.x)
            time_sec = max(0, min(self.duration, canvas_x / self.px_per_sec))
            idx = self.dragging['seg_idx']
            bound = self.dragging['bound']
            
            # 更新数据（这里可以加限制，不让 start 大于 end）
            if bound == 'start':
                self.segments[idx]['start'] = min(time_sec, self.segments[idx]['end'] - 0.01)
            else:
                self.segments[idx]['end'] = max(time_sec, self.segments[idx]['start'] + 0.01)
                
            # 性能优化：可以只重绘线，但为了简单和带背景，直接重绘全图
            self.render_canvas()

    def on_release(self, event):
        if self.dragging:
            self.dragging = None

    def on_right_click(self, event):
        canvas_x = self.canvas.canvasx(event.x)
        
        if self.mode == 'cut' and self.cuts:
            time_sec = canvas_x / self.px_per_sec
            closest_idx = np.argmin([abs(c - time_sec) for c in self.cuts])
            if abs(self.cuts[closest_idx] - time_sec) < (20 / self.px_per_sec):
                self.cuts.pop(closest_idx)
                self.render_canvas()
                self.update_count()
        
        elif self.mode in ('review', 'edit'):
            # review/edit 模式：右键点击区段内部可删除/恢复
            seg_idx = self._get_segment_at_x(canvas_x)
            if seg_idx is not None:
                self.toggle_delete_segment(seg_idx)

    def play_segment(self, idx):
        seg = self.segments[idx]
        try:
            part = self.snd.extract_part(from_time=seg['start'], to_time=seg['end'])
            audio_data = np.ascontiguousarray(part.values.T, dtype=np.float32)
            sd.play(audio_data, samplerate=int(part.sampling_frequency))
        except Exception as e:
            messagebox.showerror("错误", f"播放失败: {str(e)}")

    def confirm(self):
        if self.mode == 'cut':
            if not self.cuts:
                return messagebox.showwarning("提示", "请至少添加一个切分点。")
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
            # 过滤掉被删除的区段，只保留有效段
            kept_segments = []
            for i, seg in enumerate(self.segments):
                if i not in self.deleted_indices:
                    kept_segments.append((seg['start'], seg['end']))
            if not kept_segments:
                return messagebox.showwarning("提示", "至少需要保留一个区段。")
            self.destroy()
            self.callback(kept_segments, False)
        else:
            # edit 模式：利用动态 id 直接传回重新匹配好的有效段
            kept_segments = []
            for seg in self.segments:
                if seg.get('dyn_id') is not None:
                    # 传回重新匹配好的有效段，以及用户是否拖拽修改了边界
                    kept_segments.append({
                        'id': seg['dyn_id'],
                        'old_id': seg.get('id'),
                        'start': seg['start'],
                        'end': seg['end'],
                        'is_modified': seg['start'] != seg.get('orig_start', seg['start']) or seg['end'] != seg.get('orig_end', seg['end'])
                    })
            
            deleted_count = len(self.deleted_indices)
            self.destroy()
            self.callback(kept_segments, True, deleted_count)
