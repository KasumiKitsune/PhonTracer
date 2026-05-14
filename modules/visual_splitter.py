import tkinter as tk
from tkinter import messagebox
import customtkinter as ctk
import numpy as np
import parselmouth
import sounddevice as sd
from .ui_widgets import CTkReleaseButton

class VisualSplitter(ctk.CTkToplevel):
    def __init__(self, master, snd, icons, callback, existing_items=None):
        super().__init__(master)
        self.title("可视化手动切分 / 微调")
        self.geometry("950x550")
        self.attributes('-topmost', True)
        
        self.snd = snd
        self.icons = icons
        self.callback = callback
        
        self.mode = 'edit' if existing_items else 'cut'
        self.segments = existing_items if existing_items else []
        self.cuts = [] 
        
        self.px_per_sec = 100
        self.duration = self.snd.get_total_duration()
        self.dragging = None # {'seg_idx': int, 'bound': 'start'|'end'}
        
        self.setup_ui()
        self.init_data()
        
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
        else:
            msg = "操作说明：拖拽红色竖线微调边界，点击上方标签栏可以【试听】该区段。完成微调后点击右下角确认。"
            
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
        if self.mode == 'cut':
            self.canvas.bind("<Button-3>", self.on_right_click)
            
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
        self.canvas.xview_scroll(int(-1*(event.delta/120)), "units")

    def on_ctrl_mousewheel(self, event):
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
        
        # 1. 绘制片段背景 (如果在 edit 模式)
        if self.mode == 'edit':
            for seg in self.segments:
                x1 = seg['start'] * self.px_per_sec
                x2 = seg['end'] * self.px_per_sec
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
        else:
            self.play_rects = [] # 记录播放按钮的区域
            for i, seg in enumerate(self.segments):
                x1 = seg['start'] * self.px_per_sec
                x2 = seg['end'] * self.px_per_sec
                
                # 绘制红色边界
                self.canvas.create_line(x1, 0, x1, self.canvas_height, fill="#EF4444", width=2, tags=f"line_start_{i}")
                self.canvas.create_line(x2, 0, x2, self.canvas_height, fill="#EF4444", width=2, tags=f"line_end_{i}")
                
                # 绘制顶部标签和播放按钮
                cx = (x1 + x2) / 2
                tag_y = 20
                
                # 标签背景框
                lbl_text = f"▶ {seg['label']}"
                text_item = self.canvas.create_text(cx, tag_y, text=lbl_text, font=("Microsoft YaHei", 10, "bold"), fill="white", tags=f"lbl_{i}")
                bbox = self.canvas.bbox(text_item)
                if bbox:
                    pad = 6
                    rect = self.canvas.create_rectangle(bbox[0]-pad, bbox[1]-pad, bbox[2]+pad, bbox[3]+pad, fill="#3B82F6", outline="", tags=f"btn_{i}")
                    self.canvas.tag_raise(text_item)
                    self.play_rects.append({'idx': i, 'bbox': (bbox[0]-pad, bbox[1]-pad, bbox[2]+pad, bbox[3]+pad)})

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

    def _get_element_at_x(self, x):
        """编辑模式下，判断鼠标X坐标是否在某个边界线附近"""
        tolerance = 10
        time_sec = x / self.px_per_sec
        for i, seg in enumerate(self.segments):
            if abs(seg['start'] - time_sec) * self.px_per_sec < tolerance: return i, 'start'
            if abs(seg['end'] - time_sec) * self.px_per_sec < tolerance: return i, 'end'
        return None, None

    def on_hover(self, event):
        canvas_x = self.canvas.canvasx(event.x)
        canvas_y = self.canvas.canvasy(event.y)
        
        # 1. 检查是否悬停在播放按钮上
        if self.mode == 'edit':
            for pr in self.play_rects:
                x1, y1, x2, y2 = pr['bbox']
                if x1 <= canvas_x <= x2 and y1 <= canvas_y <= y2:
                    self.canvas.config(cursor="hand2")
                    return
        
        # 2. 检查是否悬停在边界线上
        if self.mode == 'edit' and not self.dragging:
            idx, bound = self._get_element_at_x(canvas_x)
            if idx is not None:
                self.canvas.config(cursor="sb_h_double_arrow")
                return
                
        self.canvas.config(cursor="arrow")

    def on_click(self, event):
        canvas_x = self.canvas.canvasx(event.x)
        canvas_y = self.canvas.canvasy(event.y)
        time_sec = canvas_x / self.px_per_sec
        
        if self.mode == 'edit':
            # 检查播放按钮点击
            for pr in self.play_rects:
                x1, y1, x2, y2 = pr['bbox']
                if x1 <= canvas_x <= x2 and y1 <= canvas_y <= y2:
                    self.play_segment(pr['idx'])
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
        if self.mode == 'cut' and self.cuts:
            canvas_x = self.canvas.canvasx(event.x)
            time_sec = canvas_x / self.px_per_sec
            closest_idx = np.argmin([abs(c - time_sec) for c in self.cuts])
            if abs(self.cuts[closest_idx] - time_sec) < (20 / self.px_per_sec):
                self.cuts.pop(closest_idx)
                self.render_canvas()
                self.update_count()

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
        else:
            self.destroy()
            self.callback(self.segments, True)
