import tkinter as tk
from tkinter import messagebox, ttk
import customtkinter as ctk
import numpy as np
import parselmouth
import sounddevice as sd
import platform
import time
from .ui_widgets import CTkReleaseButton, make_context_menu, post_context_menu
from .data_utils import split_into_syllables


class VisualSplitter(ctk.CTkToplevel):
    def __init__(self, master, snd, icons=None, callback=None, existing_items=None, vad_segments=None, word_items=None, **kwargs):
        super().__init__(master)
        self.title("PhonTracer语音学标注与声学分析系统 V1.0 - 段落编辑器")

        w, h = 950, 550
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        x = (sw - w) // 2
        y = (sh - h) // 2
        self.geometry(f"{w}x{h}+{x}+{y}")

        # VS-BUG-06: 移除 -topmost，使用 transient
        self.transient(master)

        self.snd = snd
        self.icons = icons or {}
        self.callback = callback
        self.wordlist = kwargs.get('wordlist', []) or []

        # 三种模式：
        # 'edit'   - 已有字表匹配结果，微调边界
        # 'review' - VAD 自动检测结果预览，可删除/试听/微调边界
        # 'cut'    - 手动添加切分线（无 VAD 预检测时的兜底）
        if existing_items:
            self.mode = 'edit'
            self.segments = []
            for source_seg in existing_items:
                seg = dict(source_seg)
                seg['inner_splits'] = list(source_seg.get('inner_splits', []))
                seg['_user_modified'] = False
                self.segments.append(seg)
            for i, seg in enumerate(self.segments):
                seg['orig_start'] = seg['start']
                seg['orig_end'] = seg['end']
                seg['orig_inner_splits'] = list(seg.get('inner_splits', []))
                self._cache_seg_splits_by_count(seg)
            source_words = word_items if word_items is not None else existing_items
            self.original_words = [
                {'id': s['id'], 'label': s['label']}
                for s in source_words
                if s.get('id') is not None
            ]
        elif vad_segments:
            self.mode = 'review'
            self.segments = [{'start': s, 'end': e, 'label': f'#{i+1}'} for i, (s, e) in enumerate(vad_segments)]
        else:
            self.mode = 'cut'
            self.segments = []

        self.cuts = []
        self.deleted_indices = set()
        self.selected_seg_idx = None

        self.px_per_sec = 100
        self.duration = self.snd.get_total_duration()
        self.dragging = None  # {'seg_idx': int, 'bound': 'start'|'end'|'inner', 'inner_idx': int}
        self.creating_seg = None
        self.play_rects = []
        self.delete_rects = []

        # 撤销/重做栈 (VS-UX-02)
        self.undo_stack = []
        self.redo_stack = []

        # 播放状态追踪 (VS-UX-05, VS-BUG-06)
        self.is_playing = False
        self._play_cursor_timer = None
        self._zoom_timer = None
        self._configure_timer = None
        self._last_canvas_size = None
        self._hovered_line_tag = None

        self.setup_ui()
        self.init_data()
        self.update_dynamic_labels()

        # 延迟一下等待窗口布局完成后计算自适应缩放与全局缩略图
        self.after(100, self.auto_fit_scale)

        # VS-BUG-06: 绑定关闭协议并激活窗口
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.lift()
        self.focus_force()

    def auto_fit_scale(self):
        cw = self.canvas.winfo_width()
        if cw > 100:
            fit_scale = cw / self.duration
            min_px_per_sec = 20
            if self.segments:
                total_seg_time = sum([s['end'] - s['start'] for s in self.segments])
                avg_seg_dur = total_seg_time / len(self.segments) if len(self.segments) > 0 else 0.5
                if avg_seg_dur < 0.1:
                    avg_seg_dur = 0.1
                min_px_per_sec = 100 / avg_seg_dur

            self.px_per_sec = max(min_px_per_sec, fit_scale)
            self.px_per_sec = round(self.px_per_sec / 25) * 25
            self.px_per_sec = max(25, min(2000, self.px_per_sec))

            self.zoom_slider.set(self.px_per_sec)
            self.update_zoom_label()

        self.render_canvas()
        self.render_minimap()

    def update_zoom_label(self):
        if hasattr(self, 'lbl_zoom'):
            self.lbl_zoom.configure(text=f"缩放: {int(self.px_per_sec)}")

    def setup_ui(self):
        self.configure(fg_color=("#FFFFFF", "#1A1D24"))

        # 1. 窗口最顶部品牌蓝装饰条 (VS-UX-08)
        top_accent = ctk.CTkFrame(self, height=4, fg_color="#2563EB", corner_radius=0)
        top_accent.pack(side=tk.TOP, fill=tk.X)

        # 2. 顶部说明与撤销重做栏
        info_frame = ctk.CTkFrame(self, height=45, fg_color=("#F9FAFB", "#161A22"), corner_radius=0)
        info_frame.pack(side=tk.TOP, fill=tk.X)

        if self.mode == 'cut':
            msg = "操作说明：【左键】添加切分线，【右键】删除最近线，【滚轮】滚动，【Ctrl+滚轮】缩放波形。"
        elif self.mode == 'review':
            msg = "VAD 自动检测完成。【右键】菜单操作，左键空白【拖拽拉框建段】，点击胶囊【试听】，拖拽红线【微调边界】。"
        else:
            msg = "【右键】菜单操作。可拖动【蓝线】微调单字边界，左键空白【拖拽拉框建段】。空格试听，方向键选段。"

        ctk.CTkLabel(info_frame, text=msg, font=("Microsoft YaHei", 12), text_color=("#1F2937", "#E5E7EB")).pack(side=tk.LEFT, padx=20, pady=10)

        # VS-UX-02: 撤销 / 重做工具栏按钮
        self.btn_redo = CTkReleaseButton(info_frame, text="重做",
                                         corner_radius=14, height=28, width=64,
                                         fg_color=("#EEF2FF", "#312E81"),
                                         hover_color=("#E0E7FF", "#3730A3"),
                                         text_color=("#4338CA", "#C7D2FE"),
                                         text_color_disabled=("#94A3B8", "#64748B"),
                                         font=("Microsoft YaHei", 12, "bold"),
                                         command=self.redo)
        self.btn_redo.pack(side=tk.RIGHT, padx=(4, 20), pady=8)

        self.btn_undo = CTkReleaseButton(info_frame, text="撤销",
                                         corner_radius=14, height=28, width=64,
                                         fg_color=("#EEF2FF", "#312E81"),
                                         hover_color=("#E0E7FF", "#3730A3"),
                                         text_color=("#4338CA", "#C7D2FE"),
                                         text_color_disabled=("#94A3B8", "#64748B"),
                                         font=("Microsoft YaHei", 12, "bold"),
                                         command=self.undo)
        self.btn_undo.pack(side=tk.RIGHT, padx=4, pady=8)
        self._update_undo_redo_buttons()

        # 3. 底部控制栏
        bottom_frame = ctk.CTkFrame(self, height=70, fg_color=("#FFFFFF", "#1A1D24"), corner_radius=0)
        bottom_frame.pack(side=tk.BOTTOM, fill=tk.X)

        if self.mode == 'cut':
            CTkReleaseButton(bottom_frame, text="清空所有点", image=self.icons.get("cut"), compound="left",
                             fg_color=("#FEE2E2", "#7F1D1D"), hover_color=("#FECACA", "#991B1B"), text_color=("#DC2626", "#FCA5A5"),
                             corner_radius=8, height=36,
                             command=self.clear_cuts).pack(side=tk.LEFT, padx=20, pady=15)

        # 缩放控制放在底部左侧
        zoom_frame = ctk.CTkFrame(bottom_frame, fg_color="transparent")
        zoom_frame.pack(side=tk.LEFT, padx=20, pady=15)
        self.lbl_zoom = ctk.CTkLabel(zoom_frame, text=f"缩放: {int(self.px_per_sec)}", font=("Microsoft YaHei", 13), text_color=("#4B5563", "#9CA3AF"))
        self.lbl_zoom.pack(side=tk.LEFT, padx=5)
        self.zoom_slider = ctk.CTkSlider(zoom_frame, from_=25, to=2000, number_of_steps=79, command=self.on_zoom_change, button_color=("#3B82F6", "#2563EB"), progress_color=("#93C5FD", "#1D4ED8"))
        self.zoom_slider.set(self.px_per_sec)
        self.zoom_slider.pack(side=tk.LEFT)

        # VS-UX-08: 先将“确认并应用”按钮 pack(side=tk.RIGHT, padx=20, pady=15)
        btn_confirm = CTkReleaseButton(bottom_frame, text="确认并应用",
                                       fg_color=("#10B981", "#059669"),
                                       hover_color=("#059669", "#047857"),
                                       text_color="#FFFFFF",
                                       corner_radius=18, height=36, width=116,
                                       font=("Microsoft YaHei", 13, "bold"),
                                       command=self.confirm)
        btn_confirm.pack(side=tk.RIGHT, padx=20, pady=15)

        # 然后再把 self.lbl_count pack(side=tk.RIGHT, padx=(0, 12)) 放在其左侧
        if self.mode == 'cut':
            self.lbl_count = ctk.CTkLabel(bottom_frame, text="当前切分点：0", font=("Microsoft YaHei", 13, "bold"), text_color=("#4B5563", "#9CA3AF"))
            self.lbl_count.pack(side=tk.RIGHT, padx=(0, 12))
        elif self.mode in ('review', 'edit'):
            self.lbl_count = ctk.CTkLabel(bottom_frame, text="", font=("Microsoft YaHei", 13, "bold"), text_color=("#4B5563", "#9CA3AF"))
            self.lbl_count.pack(side=tk.RIGHT, padx=(0, 12))
            self.update_review_count()

        # 4. 中间主绘图容器 (内含 Minimap + 分割线 + 主 Canvas + 滚动条)
        self.main_frame = ctk.CTkFrame(self, fg_color=("#FFFFFF", "#1A1D24"), corner_radius=12, border_width=1, border_color=("#E5E7EB", "#374151"))
        self.main_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=(15, 10))

        is_dark = (ctk.get_appearance_mode() == "Dark")

        # VS-UX-05: 顶部 36px 全局缩略图 Minimap Canvas
        self.minimap_canvas = tk.Canvas(self.main_frame, height=36, bg="#1E293B" if is_dark else "#F8FAFC", highlightthickness=0)
        self.minimap_canvas.pack(side=tk.TOP, fill=tk.X, padx=2, pady=(2, 0))
        self.minimap_sep = ctk.CTkFrame(self.main_frame, height=1, fg_color=("#E2E8F0", "#334155"))
        self.minimap_sep.pack(side=tk.TOP, fill=tk.X, padx=2)

        self.minimap_canvas.bind("<Button-1>", self.on_minimap_click)
        self.minimap_canvas.bind("<B1-Motion>", self.on_minimap_click)

        # 主波形画布与滚动条
        self.scrollbar = ctk.CTkScrollbar(self.main_frame, orientation="horizontal", command=self._on_scroll)
        self.canvas = tk.Canvas(self.main_frame, bg="#1A1D24" if is_dark else "#FFFFFF", highlightthickness=0)
        self.canvas.configure(xscrollcommand=self._on_canvas_xscroll)
        # VS-BUG-05: 高精度平滑滚动步长
        self.canvas.configure(xscrollincrement=20)

        self.scrollbar.pack(side=tk.BOTTOM, fill=tk.X, padx=2, pady=2)
        self.canvas.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=2, pady=2)

        # 鼠标与按键事件绑定
        self.canvas.bind("<Button-1>", self.on_click)
        self.canvas.bind("<B1-Motion>", self.on_motion)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self.canvas.bind("<Motion>", self.on_hover)
        self.canvas.bind("<Leave>", self.on_leave)

        if self.mode in ('cut', 'review', 'edit'):
            self.canvas.bind("<Button-3>", self.on_right_click)
            self.canvas.bind("<Button-2>", self.on_right_click)

        # VS-BUG-05: 跨平台与触摸板滚轮绑定
        self.canvas.bind("<MouseWheel>", self.on_mousewheel)
        self.canvas.bind("<Shift-MouseWheel>", self.on_mousewheel)
        self.canvas.bind("<Button-4>", self.on_mousewheel)
        self.canvas.bind("<Button-5>", self.on_mousewheel)
        self.canvas.bind("<Control-MouseWheel>", self.on_ctrl_mousewheel)
        if platform.system() == 'Darwin':
            self.canvas.bind("<Command-MouseWheel>", self.on_ctrl_mousewheel)

        # VS-BUG-07: Configure 防抖绑定
        self.canvas.bind("<Configure>", self.on_canvas_configure)

        # VS-UX-02 & VS-UX-05: 快捷键绑定
        self.bind("<Control-z>", self.undo)
        self.bind("<Control-Z>", self.undo)
        self.bind("<Control-y>", self.redo)
        self.bind("<Control-Y>", self.redo)
        self.bind("<space>", self.on_key_space)
        self.bind("<Left>", self.on_key_prev)
        self.bind("<Right>", self.on_key_next)

    def _on_scroll(self, *args):
        self.canvas.xview(*args)
        self.update_minimap_viewport()

    def _on_canvas_xscroll(self, low, high):
        self.scrollbar.set(low, high)
        self.update_minimap_viewport()

    def init_data(self):
        # VS-BUG-07: 保留全分辨率原始波形与采样率，用于极值包络分箱
        self.full_values = self.snd.values[0]
        self.sr = self.snd.sampling_frequency
        # 兼容保留 envelope_data 属性以防外部读取
        target_sr = 2000
        step = max(1, int(self.sr / target_sr))
        self.envelope_data = self.full_values[::step]

    def _compute_min_max_envelope(self, width):
        """矢量化计算 width 个像素列对应的 (min, max) 极值包络（零截断误差）"""
        if width <= 0 or not hasattr(self, 'full_values'):
            return np.array([]), np.array([])
        n_samples = len(self.full_values)
        if n_samples == 0:
            return np.zeros(width), np.zeros(width)
        if n_samples >= width:
            edges = np.linspace(0, n_samples, width + 1, dtype=np.int64)
            starts = np.minimum(edges[:-1], n_samples - 1)
            return np.minimum.reduceat(self.full_values, starts), np.maximum.reduceat(self.full_values, starts)
        else:
            indices = np.linspace(0, n_samples - 1, width).astype(np.int64)
            vals = self.full_values[indices]
            return vals, vals

    def on_canvas_configure(self, event):
        # VS-BUG-07: 仅在尺寸真正改变时防抖 30ms 触发重绘
        cur_size = (event.width, event.height)
        if cur_size != self._last_canvas_size:
            self._last_canvas_size = cur_size
            if self._configure_timer is not None:
                self.after_cancel(self._configure_timer)
            self._configure_timer = self.after(30, self._on_configure_timer_fire)

    def _on_configure_timer_fire(self):
        self._configure_timer = None
        self.render_canvas()
        self.render_minimap()

    def on_zoom_change(self, val):
        old_zoom = self.px_per_sec
        new_zoom = round(float(val) / 25) * 25
        if new_zoom == old_zoom:
            return

        # VS-BUG-01: 50ms 节流期视口防跳动：如果上一次缩放尚未完成渲染，直接保留已有 zoom_target！
        if getattr(self, '_zoom_timer', None) is not None and getattr(self, 'zoom_target', None) is not None:
            pass
        else:
            viewport_width = self.canvas.winfo_width()
            center_canvas_x = self.canvas.canvasx(0) + viewport_width / 2
            time_at_center = center_canvas_x / old_zoom
            self.zoom_target = {
                'time': time_at_center,
                'widget_x': viewport_width / 2
            }

        self.px_per_sec = new_zoom
        self.update_zoom_label()
        if self._zoom_timer is not None:
            self.after_cancel(self._zoom_timer)
        self._zoom_timer = self.after(50, self._on_zoom_timer_fire)

    def _on_zoom_timer_fire(self):
        self._zoom_timer = None
        self.render_canvas()

    def on_mousewheel(self, event):
        # VS-BUG-05: 兼容 Button-4 / Button-5 与各种鼠标/触摸板 delta
        if hasattr(event, 'num') and event.num in (4, 5):
            steps = -3 if event.num == 4 else 3
        else:
            delta = event.delta
            if platform.system() == 'Darwin':
                steps = int(-delta)
            elif abs(delta) >= 120:
                steps = int(-delta / 120) * 3
            elif 0 < abs(delta) < 120:
                steps = -1 if delta > 0 else 1
            else:
                steps = 0
        self.canvas.xview_scroll(steps, "units")
        self.update_minimap_viewport()

    def on_ctrl_mousewheel(self, event):
        delta = event.delta if platform.system() == 'Darwin' else event.delta / 120
        old_zoom = self.px_per_sec
        new_zoom = old_zoom * (1.2 if delta > 0 else 0.8)
        new_zoom = max(25, min(2000, round(new_zoom / 25) * 25))
        if new_zoom == old_zoom:
            return

        canvas_x = self.canvas.canvasx(event.x)
        time_at_mouse = canvas_x / old_zoom

        self.zoom_target = {
            'time': time_at_mouse,
            'widget_x': event.x
        }

        self.px_per_sec = new_zoom
        self.zoom_slider.set(new_zoom)
        self.update_zoom_label()
        self.render_canvas()

    # --- Phase 1 缓存与内部分割比例算法 (完整保留) ---
    def _cache_seg_splits_by_count(self, seg):
        splits = seg.get('inner_splits', [])
        if not splits:
            return
        start = seg.get('start', 0.0)
        end = seg.get('end', 0.0)
        dur = end - start
        if dur <= 0:
            return
        ratios = [(s - start) / dur for s in splits if start < s < end]
        if len(ratios) == len(splits):
            n_syls = len(splits) + 1
            seg.setdefault('_splits_by_count', {})[n_syls] = ratios

    def _apply_syl_splits_with_cache(self, seg, n_syls):
        self._cache_seg_splits_by_count(seg)
        if n_syls > 1:
            target_splits = n_syls - 1
            splits = seg.get('inner_splits', [])
            if len(splits) != target_splits:
                dur = seg['end'] - seg['start']
                cached_ratios = seg.get('_splits_by_count', {}).get(n_syls)
                if cached_ratios and len(cached_ratios) == target_splits and dur > 0:
                    seg['inner_splits'] = [seg['start'] + dur * r for r in cached_ratios]
                else:
                    seg['inner_splits'] = [seg['start'] + dur * j / n_syls for j in range(1, n_syls)]
                    self._cache_seg_splits_by_count(seg)
        else:
            seg['inner_splits'] = []

    def update_dynamic_labels(self):
        word_idx = 0
        for i, seg in enumerate(self.segments):
            if i in self.deleted_indices:
                self._cache_seg_splits_by_count(seg)
                seg['dyn_label'] = "已剔除"
                seg['dyn_id'] = None
            else:
                if self.wordlist and word_idx < len(self.wordlist):
                    dyn_lbl = self.wordlist[word_idx]
                    seg['dyn_label'] = dyn_lbl
                    seg['dyn_id'] = word_idx
                    syls = split_into_syllables(dyn_lbl)
                    self._apply_syl_splits_with_cache(seg, len(syls))
                    word_idx += 1
                elif hasattr(self, 'original_words') and word_idx < len(self.original_words):
                    dyn_lbl = self.original_words[word_idx]['label']
                    seg['dyn_label'] = dyn_lbl
                    seg['dyn_id'] = self.original_words[word_idx]['id']
                    syls = split_into_syllables(dyn_lbl)
                    self._apply_syl_splits_with_cache(seg, len(syls))
                    word_idx += 1
                else:
                    self._cache_seg_splits_by_count(seg)
                    seg['dyn_label'] = f"#{word_idx + 1}" if self.mode == 'review' else "【未分配段】"
                    seg['dyn_id'] = None
                    seg['inner_splits'] = []
                    word_idx += 1

    # --- 撤销 / 重做系统 (VS-UX-02) ---
    def _push_undo_snapshot(self):
        state = {
            'segments': [
                {
                    'start': s['start'],
                    'end': s['end'],
                    'label': s.get('label', ''),
                    'dyn_label': s.get('dyn_label', ''),
                    'dyn_id': s.get('dyn_id'),
                    'id': s.get('id'),
                    'orig_start': s.get('orig_start', s['start']),
                    'orig_end': s.get('orig_end', s['end']),
                    'orig_inner_splits': list(s.get('orig_inner_splits', [])),
                    'inner_splits': list(s.get('inner_splits', [])),
                    '_user_modified': s.get('_user_modified', False),
                    '_splits_by_count': {k: list(v) for k, v in s.get('_splits_by_count', {}).items()}
                }
                for s in self.segments
            ],
            'deleted_indices': set(self.deleted_indices),
            'cuts': list(self.cuts),
            'selected_seg_idx': self.selected_seg_idx
        }
        self.undo_stack.append(state)
        if len(self.undo_stack) > 50:
            self.undo_stack.pop(0)
        self.redo_stack.clear()
        self._update_undo_redo_buttons()

    def undo(self, event=None):
        if not self.undo_stack:
            return "break"
        curr_state = {
            'segments': [
                {
                    'start': s['start'],
                    'end': s['end'],
                    'label': s.get('label', ''),
                    'dyn_label': s.get('dyn_label', ''),
                    'dyn_id': s.get('dyn_id'),
                    'id': s.get('id'),
                    'orig_start': s.get('orig_start', s['start']),
                    'orig_end': s.get('orig_end', s['end']),
                    'orig_inner_splits': list(s.get('orig_inner_splits', [])),
                    'inner_splits': list(s.get('inner_splits', [])),
                    '_user_modified': s.get('_user_modified', False),
                    '_splits_by_count': {k: list(v) for k, v in s.get('_splits_by_count', {}).items()}
                }
                for s in self.segments
            ],
            'deleted_indices': set(self.deleted_indices),
            'cuts': list(self.cuts),
            'selected_seg_idx': self.selected_seg_idx
        }
        self.redo_stack.append(curr_state)
        if len(self.redo_stack) > 50:
            self.redo_stack.pop(0)

        state = self.undo_stack.pop()
        self._apply_history_state(state)
        return "break"

    def redo(self, event=None):
        if not self.redo_stack:
            return "break"
        curr_state = {
            'segments': [
                {
                    'start': s['start'],
                    'end': s['end'],
                    'label': s.get('label', ''),
                    'dyn_label': s.get('dyn_label', ''),
                    'dyn_id': s.get('dyn_id'),
                    'id': s.get('id'),
                    'orig_start': s.get('orig_start', s['start']),
                    'orig_end': s.get('orig_end', s['end']),
                    'orig_inner_splits': list(s.get('orig_inner_splits', [])),
                    'inner_splits': list(s.get('inner_splits', [])),
                    '_user_modified': s.get('_user_modified', False),
                    '_splits_by_count': {k: list(v) for k, v in s.get('_splits_by_count', {}).items()}
                }
                for s in self.segments
            ],
            'deleted_indices': set(self.deleted_indices),
            'cuts': list(self.cuts),
            'selected_seg_idx': self.selected_seg_idx
        }
        self.undo_stack.append(curr_state)
        if len(self.undo_stack) > 50:
            self.undo_stack.pop(0)

        state = self.redo_stack.pop()
        self._apply_history_state(state)
        return "break"

    def _apply_history_state(self, state):
        self.segments = [
            {
                'start': s['start'],
                'end': s['end'],
                'label': s.get('label', ''),
                'dyn_label': s.get('dyn_label', ''),
                'dyn_id': s.get('dyn_id'),
                'id': s.get('id'),
                'orig_start': s.get('orig_start', s['start']),
                'orig_end': s.get('orig_end', s['end']),
                'orig_inner_splits': list(s.get('orig_inner_splits', [])),
                'inner_splits': list(s.get('inner_splits', [])),
                '_user_modified': s.get('_user_modified', False),
                '_splits_by_count': {k: list(v) for k, v in s.get('_splits_by_count', {}).items()}
            }
            for s in state['segments']
        ]
        self.deleted_indices = set(state['deleted_indices'])
        self.cuts = list(state['cuts'])
        self.selected_seg_idx = state.get('selected_seg_idx')
        self.update_dynamic_labels()
        if self.mode in ('review', 'edit'):
            self.update_review_count()
        elif self.mode == 'cut' and hasattr(self, 'lbl_count'):
            self.lbl_count.configure(text=f"当前切分点：{len(self.cuts)}")
        self.render_canvas()
        self.render_minimap()
        self._update_undo_redo_buttons()

    def _update_undo_redo_buttons(self):
        if hasattr(self, 'btn_undo'):
            if len(self.undo_stack) > 0:
                self.btn_undo.configure(
                    state="normal",
                    fg_color=("#EEF2FF", "#312E81"),
                    hover_color=("#DCE4FF", "#3730A3"),
                    text_color=("#4338CA", "#C7D2FE"),
                )
            else:
                self.btn_undo.configure(
                    state="disabled",
                    fg_color=("#F1F5F9", "#1E293B"),
                    hover_color=("#F1F5F9", "#1E293B"),
                    text_color_disabled=("#94A3B8", "#64748B"),
                )
        if hasattr(self, 'btn_redo'):
            if len(self.redo_stack) > 0:
                self.btn_redo.configure(
                    state="normal",
                    fg_color=("#EEF2FF", "#312E81"),
                    hover_color=("#DCE4FF", "#3730A3"),
                    text_color=("#4338CA", "#C7D2FE"),
                )
            else:
                self.btn_redo.configure(
                    state="disabled",
                    fg_color=("#F1F5F9", "#1E293B"),
                    hover_color=("#F1F5F9", "#1E293B"),
                    text_color_disabled=("#94A3B8", "#64748B"),
                )

    # --- 辅助几何方法 (VS-BUG-02, VS-BUG-03, VS-UX-06) ---
    def _get_adjacent_valid_bounds(self, seg_idx):
        """VS-BUG-02: 查找 seg_idx 左右最近未删除有效区段的硬边界"""
        prev_end = 0.0
        for j in range(seg_idx - 1, -1, -1):
            if j not in self.deleted_indices:
                prev_end = self.segments[j]['end']
                break
        next_start = self.duration
        for j in range(seg_idx + 1, len(self.segments)):
            if j not in self.deleted_indices:
                next_start = self.segments[j]['start']
                break
        return prev_end, next_start

    def _find_blank_gap_bounds(self, time_sec):
        """VS-BUG-03: 判断 time_sec 是否在空白间隙，返回间隙 (gap_s, gap_e) 或 None"""
        for i, seg in enumerate(self.segments):
            if i not in self.deleted_indices:
                if seg['start'] <= time_sec <= seg['end']:
                    return None
        left_ends = [seg['end'] for i, seg in enumerate(self.segments) if i not in self.deleted_indices and seg['end'] <= time_sec]
        gap_s = max(left_ends) if left_ends else 0.0
        right_starts = [seg['start'] for i, seg in enumerate(self.segments) if i not in self.deleted_indices and seg['start'] >= time_sec]
        gap_e = min(right_starts) if right_starts else self.duration
        return gap_s, gap_e

    def _get_element_at_x(self, x, tolerance=8):
        """VS-UX-06: 最近距离匹配 (Closest-Match)，在 tolerance 范围内返回距离最近的红线/蓝线"""
        candidates = []
        for i, seg in enumerate(self.segments):
            if i in self.deleted_indices:
                continue
            dist_s = abs(seg['start'] * self.px_per_sec - x)
            if dist_s <= tolerance:
                candidates.append((dist_s, i, 'start', None))
            dist_e = abs(seg['end'] * self.px_per_sec - x)
            if dist_e <= tolerance:
                candidates.append((dist_e, i, 'end', None))
            for s_idx, s_t in enumerate(seg.get('inner_splits', [])):
                dist_in = abs(s_t * self.px_per_sec - x)
                if dist_in <= tolerance:
                    candidates.append((dist_in, i, 'inner', s_idx))
        if not candidates:
            return None, None, None
        candidates.sort(key=lambda c: c[0])
        _, seg_idx, bound_type, inner_idx = candidates[0]
        return seg_idx, bound_type, inner_idx

    def _get_segment_at_x(self, x, include_deleted=True):
        time_sec = x / self.px_per_sec
        for i, seg in enumerate(self.segments):
            if i not in self.deleted_indices and seg['start'] <= time_sec <= seg['end']:
                return i
        if include_deleted:
            for i, seg in enumerate(self.segments):
                if i in self.deleted_indices and seg['start'] <= time_sec <= seg['end']:
                    return i
        return None

    # --- 主画布与全局 Minimap 渲染 ---
    def render_canvas(self):
        self.canvas_width = int(self.duration * self.px_per_sec)
        self.canvas_height = self.canvas.winfo_height()
        if self.canvas_height < 100:
            self.canvas_height = 400

        # VS-UX-03: 独立底部 24px 时间标尺带
        ruler_h = 24
        waveform_h = max(80, self.canvas_height - ruler_h)

        self.canvas.delete("all")
        # VS-BUG-04: 最右侧余量 32px
        self.canvas.config(scrollregion=(0, 0, self.canvas_width + 32, self.canvas_height))

        is_dark = (ctk.get_appearance_mode() == "Dark")
        canvas_bg = "#1A1D24" if is_dark else "#FFFFFF"
        self.canvas.configure(bg=canvas_bg)

        # 1. 绘制片段背景矩形 (VS-BUG-02: 限制在 y=0..waveform_h，打上 seg_bg_{i} 标签)
        if self.mode in ('edit', 'review'):
            for i, seg in enumerate(self.segments):
                x1 = seg['start'] * self.px_per_sec
                x2 = seg['end'] * self.px_per_sec
                is_del = (i in self.deleted_indices)
                bg_color = ("#FEE2E2", "#450A0A")[is_dark] if is_del else ("#EFF6FF", "#172554")[is_dark]
                self.canvas.create_rectangle(x1, 0, x2, waveform_h, fill=bg_color, outline="", tags=f"seg_bg_{i}")

                # 选中区段高亮边框 (VS-UX-05)
                if i == self.selected_seg_idx and not is_del:
                    self.canvas.create_rectangle(x1, 0, x2, waveform_h, outline="#2563EB", width=2, tags=f"seg_sel_{i}")

        # 2. 绘制波形中心线 (限制在 waveform_h 范围)
        mid_y = waveform_h / 2
        self.canvas.create_line(0, mid_y, self.canvas_width + 32, mid_y, fill="#374151" if is_dark else "#E5E7EB")

        # 3. VS-BUG-07: 分箱极值包络 (Min-Max Binning) 绘制波形
        amp_scale = (waveform_h / 2 - 10) * 0.9
        min_vals, max_vals = self._compute_min_max_envelope(self.canvas_width)
        if len(min_vals) > 0:
            points = []
            for x in range(self.canvas_width):
                y_top = mid_y - (max_vals[x] * amp_scale)
                y_bot = mid_y - (min_vals[x] * amp_scale)
                points.extend([x, y_top, x, y_bot])
            chunk_size = 8000
            for i in range(0, len(points), chunk_size):
                chunk = points[i:i + chunk_size + 2]
                if len(chunk) >= 4:
                    self.canvas.create_line(chunk, fill="#4B5563" if is_dark else "#9CA3AF", width=1, tags="waveform")

        # 4. VS-UX-03: 独立底部 24px 时间标尺带 (彻底根除红线劈穿文字)
        ruler_bg = "#1E293B" if is_dark else "#F8FAFC"
        ruler_div = "#334155" if is_dark else "#E2E8F0"
        self.canvas.create_rectangle(0, waveform_h, self.canvas_width + 32, self.canvas_height, fill=ruler_bg, outline="")
        self.canvas.create_line(0, waveform_h, self.canvas_width + 32, waveform_h, fill=ruler_div, width=1)

        step_sec = 1 if self.px_per_sec > 50 else 5
        if self.px_per_sec > 200:
            step_sec = 0.5
        if self.px_per_sec > 500:
            step_sec = 0.2
        if self.px_per_sec > 1000:
            step_sec = 0.1

        for t in np.arange(0, self.duration + 0.0001, step_sec):
            x = t * self.px_per_sec
            self.canvas.create_line(x, waveform_h, x, waveform_h + 6, fill="#475569" if is_dark else "#CBD5E1")
            text_str = f"{t:.1f}s" if step_sec < 1 else f"{int(round(t))}s"
            self.canvas.create_text(x + 2, waveform_h + 14, text=text_str, anchor=tk.W, font=("Arial", 9), fill="#94A3B8" if is_dark else "#64748B")

        # 5. 绘制边界线与胶囊按钮
        if self.mode == 'cut':
            for cut in self.cuts:
                self.draw_cut_line(cut, waveform_h)
        elif self.mode in ('edit', 'review'):
            self.play_rects = []
            self.delete_rects = []
            for i, seg in enumerate(self.segments):
                is_deleted = (i in self.deleted_indices)
                x1 = seg['start'] * self.px_per_sec
                x2 = seg['end'] * self.px_per_sec

                line_color = "#D1D5DB" if is_deleted else "#EF4444"
                line_dash = (4, 4) if is_deleted else ()
                self.canvas.create_line(x1, 0, x1, waveform_h, fill=line_color, width=2, dash=line_dash, tags=f"seg_start_line_{i}")
                self.canvas.create_line(x2, 0, x2, waveform_h, fill=line_color, width=2, dash=line_dash, tags=f"seg_end_line_{i}")

                # 词语模式：绘制蓝线
                inner_splits = seg.get('inner_splits', [])
                if not is_deleted and inner_splits:
                    for split_idx, s_t in enumerate(inner_splits):
                        s_x = s_t * self.px_per_sec
                        self.canvas.create_line(s_x, 0, s_x, waveform_h, fill="#3B82F6", width=2, dash=(4, 4), tags=f"seg_inner_line_{i}_{split_idx}")

                display_label = seg.get('dyn_label', seg.get('label', ''))

                def create_pill_smooth(canvas, px, py, text, bg_color, tags):
                    text_id = canvas.create_text(px, py, text=text, font=("Microsoft YaHei", 10, "bold"), fill="white", tags=tags)
                    b = canvas.bbox(text_id)
                    if not b:
                        return (px - 20, py - 10, px + 20, py + 10)
                    h = b[3] - b[1] + 8
                    canvas.create_line(b[0], py, b[2], py, width=h, fill=bg_color, capstyle='round', tags=tags)
                    canvas.tag_raise(text_id)
                    return canvas.bbox(tags)

                if is_deleted:
                    cx = (x1 + x2) / 2
                    del_txt = f"✕ {display_label}" if (x2 - x1) >= 54 else "✕"
                    bbox = create_pill_smooth(self.canvas, cx, 24, del_txt, "#9CA3AF", f"btn_{i}")
                    self.delete_rects.append({'idx': i, 'bbox': bbox})
                    center_y = waveform_h / 2
                    self.canvas.create_line(cx, center_y, cx, center_y, width=44, fill="#EF4444", capstyle='round')
                    self.canvas.create_text(cx, center_y, text="✕", font=("Arial", 18, "bold"), fill="white")
                else:
                    # VS-UX-04: 三级自适应防重叠胶囊
                    syls = split_into_syllables(display_label)
                    if len(syls) > 1 and len(inner_splits) == len(syls) - 1:
                        splits = [seg['start']] + inner_splits + [seg['end']]
                        char_colors = ["#3B82F6", "#10B981", "#EF4444", "#F59E0B", "#8B5CF6", "#14B8A6", "#EC4899", "#6366F1"]
                        for syl_idx, syl in enumerate(syls):
                            c_s, c_e = splits[syl_idx], splits[syl_idx + 1]
                            cx = (c_s + c_e) / 2 * self.px_per_sec
                            color = char_colors[syl_idx % len(char_colors)]
                            syl_w = (c_e - c_s) * self.px_per_sec

                            if syl_w >= 54:
                                txt = f"▶ {syl}"
                                tag_y = 24
                            elif syl_w >= 28:
                                txt = syl
                                tag_y = 24
                            else:
                                txt = syl
                                tag_y = 18 if syl_idx % 2 == 0 else 42

                            bbox = create_pill_smooth(self.canvas, cx, tag_y, txt, color, f"btn_{i}_{syl_idx}")
                            self.play_rects.append({'idx': i, 'start': c_s, 'end': c_e, 'bbox': bbox})
                    else:
                        cx = (x1 + x2) / 2
                        seg_w = x2 - x1
                        txt = f"▶ {display_label}" if seg_w >= 54 else display_label
                        bbox = create_pill_smooth(self.canvas, cx, 24, txt, "#3B82F6", f"btn_{i}")
                        self.play_rects.append({'idx': i, 'start': seg['start'], 'end': seg['end'], 'bbox': bbox})

        # 6. VS-BUG-01: 精确视口中心保持滚动并在完成后置空 zoom_target 与 _zoom_timer
        if hasattr(self, 'zoom_target') and self.zoom_target:
            target_time = self.zoom_target['time']
            widget_x = self.zoom_target['widget_x']
            new_left_x = target_time * self.px_per_sec - widget_x
            fraction = max(0.0, min(1.0, new_left_x / max(1, self.canvas_width + 32)))
            self.canvas.xview_moveto(fraction)
            self.zoom_target = None
            self._zoom_timer = None

        self.update_minimap_viewport()

    def draw_cut_line(self, time_sec, waveform_h):
        x = time_sec * self.px_per_sec
        self.canvas.create_line(x, 0, x, waveform_h, fill="#EF4444", width=2)
        self.canvas.create_text(x + 5, 20, text=f"{time_sec:.3f}s", anchor=tk.W, fill="#B91C1C", font=("Arial", 10, "bold"))

    def render_minimap(self):
        """VS-UX-05: 渲染顶部 36px 全局波形与区段缩略图"""
        if not hasattr(self, 'minimap_canvas'):
            return
        w = self.minimap_canvas.winfo_width()
        if w < 50:
            w = max(100, self.winfo_width() - 44)
        h = 36
        is_dark = (ctk.get_appearance_mode() == "Dark")
        self.minimap_canvas.delete("all")
        self.minimap_canvas.configure(bg="#1E293B" if is_dark else "#F8FAFC")

        # 1. 优先绘制区段底色块 (VS-NEW-04)
        if self.duration > 0:
            for i, seg in enumerate(self.segments):
                x1 = (seg['start'] / self.duration) * w
                x2 = (seg['end'] / self.duration) * w
                if i in self.deleted_indices:
                    bg_col = "#7F1D1D" if is_dark else "#FECACA"
                else:
                    bg_col = "#1E40AF" if is_dark else "#BFDBFE"
                self.minimap_canvas.create_rectangle(x1, 2, x2, h - 2, fill=bg_col, outline="", tags="mini_seg")

        # 2. 随后在色块上层绘制缩略波形
        if hasattr(self, 'full_values') and len(self.full_values) > 0 and self.duration > 0:
            min_vals, max_vals = self._compute_min_max_envelope(w)
            mid_y = h / 2
            amp = (h / 2 - 4) * 0.9
            points = []
            for x in range(len(min_vals)):
                y_top = mid_y - (max_vals[x] * amp)
                y_bot = mid_y - (min_vals[x] * amp)
                points.extend([x, y_top, x, y_bot])
            if len(points) >= 4:
                self.minimap_canvas.create_line(points, fill="#94A3B8" if is_dark else "#64748B", width=1, tags="mini_wave")

        # 3. 绘制视口高亮框
        self.update_minimap_viewport()

    def update_minimap_viewport(self):
        """VS-UX-05: 刷新 Minimap 上的当前视口框"""
        if not hasattr(self, 'minimap_canvas'):
            return
        w = self.minimap_canvas.winfo_width()
        if w < 10:
            return
        h = 36
        try:
            low, high = self.canvas.xview()
        except Exception:
            low, high = 0.0, 1.0
        vx1 = max(0, low * w)
        vx2 = min(w, high * w)
        if vx2 - vx1 < 6:
            vx2 = min(w, vx1 + 6)

        if self.minimap_canvas.find_withtag("minimap_vp"):
            self.minimap_canvas.coords("minimap_vp", vx1, 1, vx2, h - 1)
            self.minimap_canvas.tag_raise("minimap_vp")
        else:
            self.minimap_canvas.create_rectangle(vx1, 1, vx2, h - 1, outline="#2563EB", width=2, fill="#3B82F6", stipple="gray25", tags="minimap_vp")

    def on_minimap_click(self, event):
        """VS-UX-05: Minimap 点击或拖拽定位视口中心"""
        w = self.minimap_canvas.winfo_width()
        if w < 10 or self.duration <= 0:
            return
        time_clicked = max(0.0, min(self.duration, (event.x / w) * self.duration))
        viewport_width = self.canvas.winfo_width()
        center_x = time_clicked * self.px_per_sec
        left_x = center_x - viewport_width / 2
        fraction = max(0.0, min(1.0, left_x / max(1, self.canvas_width + 32)))
        self.canvas.xview_moveto(fraction)
        self.update_minimap_viewport()

    # --- 交互操作 ---
    def clear_cuts(self):
        if self.cuts:
            self._push_undo_snapshot()
            self.cuts = []
            self.render_canvas()
            if hasattr(self, 'lbl_count'):
                self.lbl_count.configure(text=f"当前切分点：0")

    def update_review_count(self):
        if hasattr(self, 'lbl_count'):
            total = len(self.segments)
            deleted = len(self.deleted_indices)
            kept = total - deleted
            if deleted > 0:
                self.lbl_count.configure(text=f"共 {total} 段 | 保留 {kept} 段 | 已移除 {deleted} 段", text_color=("#DC2626", "#F87171"))
            else:
                self.lbl_count.configure(text=f"共 {total} 个检测区段", text_color=("#4B5563", "#9CA3AF"))

    def toggle_delete_segment(self, idx):
        self._push_undo_snapshot()
        if idx in self.deleted_indices:
            self.deleted_indices.discard(idx)
        else:
            self.deleted_indices.add(idx)
        self.update_dynamic_labels()
        self.render_canvas()
        self.render_minimap()
        if self.mode in ('review', 'edit'):
            self.update_review_count()

    def reset_inner_splits_with_undo(self, seg_idx):
        """重置多音节词内部分割蓝线为均分"""
        if not (0 <= seg_idx < len(self.segments)):
            return
        seg = self.segments[seg_idx]
        lbl = seg.get('dyn_label', seg.get('label', ''))
        syls = split_into_syllables(lbl)
        n_syls = len(syls)
        if n_syls > 1:
            self._push_undo_snapshot()
            dur = seg['end'] - seg['start']
            seg['inner_splits'] = [seg['start'] + dur * j / n_syls for j in range(1, n_syls)]
            seg['_user_modified'] = True
            self._cache_seg_splits_by_count(seg)
            self.render_canvas()

    def add_segment_at(self, start, end):
        new_seg = {
            'start': start,
            'end': end,
            'label': f'#{len(self.segments)+1}',
            '_user_modified': True
        }
        if self.mode == 'edit':
            new_seg['orig_start'] = start
            new_seg['orig_end'] = end
            new_seg['orig_inner_splits'] = []
            new_seg['inner_splits'] = []

        insert_idx = 0
        while insert_idx < len(self.segments) and self.segments[insert_idx]['start'] < start:
            insert_idx += 1

        self.segments.insert(insert_idx, new_seg)

        new_deleted_indices = set()
        for idx in self.deleted_indices:
            if idx >= insert_idx:
                new_deleted_indices.add(idx + 1)
            else:
                new_deleted_indices.add(idx)
        self.deleted_indices = new_deleted_indices
        self.selected_seg_idx = insert_idx

        self.update_dynamic_labels()
        if self.mode in ('review', 'edit'):
            self.update_review_count()
        self.render_canvas()
        self.render_minimap()

    def _clear_hover_line(self):
        if getattr(self, '_hovered_line_tag', None):
            if self.canvas.find_withtag(self._hovered_line_tag):
                self.canvas.itemconfigure(self._hovered_line_tag, width=2)
            self._hovered_line_tag = None
        self.canvas.delete("hover_time_tip")

    def on_leave(self, event):
        self._clear_hover_line()

    def on_hover(self, event):
        canvas_x = self.canvas.canvasx(event.x)
        canvas_y = self.canvas.canvasy(event.y)
        waveform_h = max(80, self.canvas_height - 24)

        if self.mode in ('edit', 'review') and not self.dragging:
            # VS-UX-04: 优先判断是否命中红线/蓝线 (tolerance 8px)，优先级高于胶囊！
            idx, bound, inner_idx = self._get_element_at_x(canvas_x, tolerance=8)
            if idx is not None:
                tag = f"seg_start_line_{idx}" if bound == 'start' else (f"seg_end_line_{idx}" if bound == 'end' else f"seg_inner_line_{idx}_{inner_idx}")
                if getattr(self, '_hovered_line_tag', None) != tag:
                    self._clear_hover_line()
                    self._hovered_line_tag = tag
                    if self.canvas.find_withtag(tag):
                        self.canvas.itemconfigure(tag, width=3.5)

                # VS-UX-06: 悬停高亮时间气泡
                seg = self.segments[idx]
                t_val = seg['start'] if bound == 'start' else (seg['end'] if bound == 'end' else seg['inner_splits'][inner_idx])
                tip_y = max(16, min(canvas_y - 12, waveform_h - 20))
                self.canvas.delete("hover_time_tip")
                is_dark = (ctk.get_appearance_mode() == "Dark")
                tip_col = "#E2E8F0" if is_dark else "#1E293B"
                self.canvas.create_text(canvas_x + 8, tip_y, text=f"{t_val:.3f}s", anchor=tk.W, fill=tip_col, font=("Arial", 9, "bold"), tags="hover_time_tip")
                return self.canvas.config(cursor="sb_h_double_arrow")
            else:
                self._clear_hover_line()

            for pr in reversed(self.play_rects):
                x1, y1, x2, y2 = pr['bbox']
                if x1 <= canvas_x <= x2 and y1 <= canvas_y <= y2:
                    return self.canvas.config(cursor="hand2")
            for dr in reversed(self.delete_rects):
                x1, y1, x2, y2 = dr['bbox']
                if x1 <= canvas_x <= x2 and y1 <= canvas_y <= y2:
                    return self.canvas.config(cursor="hand2")

        elif not self.dragging:
            self._clear_hover_line()

        self.canvas.config(cursor="arrow")

    def on_click(self, event):
        canvas_x = self.canvas.canvasx(event.x)
        canvas_y = self.canvas.canvasy(event.y)
        time_sec = canvas_x / self.px_per_sec

        if self.mode in ('edit', 'review'):
            # VS-UX-04: 优先判断线条拖拽 (tolerance 8px)，不被外溢胶囊拦截！
            idx, bound, inner_idx = self._get_element_at_x(canvas_x, tolerance=8)
            if idx is not None:
                self._clear_hover_line()
                self.canvas.delete("hover_time_tip")
                self._cache_seg_splits_by_count(self.segments[idx])
                self._push_undo_snapshot()
                self.selected_seg_idx = idx
                self.dragging = {'seg_idx': idx, 'bound': bound, 'inner_idx': inner_idx}
                return

            for pr in reversed(self.play_rects):
                x1, y1, x2, y2 = pr['bbox']
                if x1 <= canvas_x <= x2 and y1 <= canvas_y <= y2:
                    self.selected_seg_idx = pr['idx']
                    self.render_canvas()
                    return self.play_segment(pr['start'], pr['end'])

            for dr in reversed(self.delete_rects):
                x1, y1, x2, y2 = dr['bbox']
                if x1 <= canvas_x <= x2 and y1 <= canvas_y <= y2:
                    return self.toggle_delete_segment(dr['idx'])

            seg_idx = self._get_segment_at_x(canvas_x, include_deleted=False)
            if seg_idx is not None:
                self.selected_seg_idx = seg_idx
                self.render_canvas()
                return

            # VS-UX-01: 左键空白区拖拽拉框建段 (Rubber-band Selection)
            gap = self._find_blank_gap_bounds(time_sec)
            if gap is not None and (gap[1] - gap[0]) >= 0.05:
                gap_s, gap_e = gap
                self.creating_seg = {
                    'anchor_t': time_sec,
                    'cur_t': time_sec,
                    'gap_s': gap_s,
                    'gap_e': gap_e
                }
                self.selected_seg_idx = None
                self.render_canvas()

        elif self.mode == 'cut' and 0 <= time_sec <= self.duration:
            self._push_undo_snapshot()
            self.cuts.append(time_sec)
            self.cuts.sort()
            self.render_canvas()
            if hasattr(self, 'lbl_count'):
                self.lbl_count.configure(text=f"当前切分点：{len(self.cuts)}")

    def on_motion(self, event):
        canvas_x = self.canvas.canvasx(event.x)
        waveform_h = max(80, self.canvas_height - 24)

        if self.dragging:
            time_sec = max(0.0, min(self.duration, canvas_x / self.px_per_sec))
            idx = self.dragging['seg_idx']
            bound = self.dragging['bound']
            seg = self.segments[idx]
            splits = seg.get('inner_splits', [])

            # VS-BUG-02: 红线拖拽邻段硬约束 + 背景块实时跟随伸缩
            prev_end, next_start = self._get_adjacent_valid_bounds(idx)

            n_splits = len(splits)
            seg['_user_modified'] = True
            if bound == 'start':
                lower_limit = (prev_end + 0.01) if prev_end > 0 else 0.0
                upper_limit = seg['end'] - (0.02 * (n_splits + 1))
                seg['start'] = max(lower_limit, min(upper_limit, time_sec))
                cur_t = seg['start']
                tag = f"seg_start_line_{idx}"
                self.canvas.coords(f"seg_bg_{idx}", seg['start'] * self.px_per_sec, 0, seg['end'] * self.px_per_sec, waveform_h)
                if self.canvas.find_withtag(f"seg_sel_{idx}"):
                    self.canvas.coords(f"seg_sel_{idx}", seg['start'] * self.px_per_sec, 1, seg['end'] * self.px_per_sec, waveform_h - 1)
                # VS-NEW-03: 同步按比例缩放内部蓝线
                if n_splits > 0:
                    dur = seg['end'] - seg['start']
                    ratios = seg.get('_splits_by_count', {}).get(n_splits + 1)
                    if ratios and len(ratios) == n_splits:
                        seg['inner_splits'] = [seg['start'] + dur * r for r in ratios]
                    else:
                        seg['inner_splits'] = [seg['start'] + dur * (j + 1) / (n_splits + 1) for j in range(n_splits)]
                    for s_idx, s_t in enumerate(seg['inner_splits']):
                        sx = s_t * self.px_per_sec
                        self.canvas.coords(f"seg_inner_line_{idx}_{s_idx}", sx, 0, sx, waveform_h)
            elif bound == 'end':
                lower_limit = max((prev_end + 0.01) if prev_end > 0 else 0.0, seg['start'] + (0.02 * (n_splits + 1)))
                upper_limit = (next_start - 0.01) if next_start < self.duration else self.duration
                seg['end'] = max(lower_limit, min(upper_limit, time_sec))
                cur_t = seg['end']
                tag = f"seg_end_line_{idx}"
                self.canvas.coords(f"seg_bg_{idx}", seg['start'] * self.px_per_sec, 0, seg['end'] * self.px_per_sec, waveform_h)
                if self.canvas.find_withtag(f"seg_sel_{idx}"):
                    self.canvas.coords(f"seg_sel_{idx}", seg['start'] * self.px_per_sec, 1, seg['end'] * self.px_per_sec, waveform_h - 1)
                # VS-NEW-03: 同步按比例缩放内部蓝线
                if n_splits > 0:
                    dur = seg['end'] - seg['start']
                    ratios = seg.get('_splits_by_count', {}).get(n_splits + 1)
                    if ratios and len(ratios) == n_splits:
                        seg['inner_splits'] = [seg['start'] + dur * r for r in ratios]
                    else:
                        seg['inner_splits'] = [seg['start'] + dur * (j + 1) / (n_splits + 1) for j in range(n_splits)]
                    for s_idx, s_t in enumerate(seg['inner_splits']):
                        sx = s_t * self.px_per_sec
                        self.canvas.coords(f"seg_inner_line_{idx}_{s_idx}", sx, 0, sx, waveform_h)
            elif bound == 'inner':
                inner_idx = self.dragging['inner_idx']
                min_limit = seg['start'] if inner_idx == 0 else splits[inner_idx - 1]
                max_limit = seg['end'] if inner_idx == len(splits) - 1 else splits[inner_idx + 1]
                splits[inner_idx] = max(min_limit + 0.01, min(max_limit - 0.01, time_sec))
                self._cache_seg_splits_by_count(seg)
                cur_t = splits[inner_idx]
                tag = f"seg_inner_line_{idx}_{inner_idx}"
            else:
                cur_t = time_sec
                tag = None

            cur_x = cur_t * self.px_per_sec
            if tag and self.canvas.find_withtag(tag):
                self.canvas.coords(tag, cur_x, 0, cur_x, waveform_h)

            if not self.canvas.find_withtag("drag_indicator"):
                color = "#EF4444" if bound in ('start', 'end') else "#3B82F6"
                self.canvas.create_line(cur_x, 0, cur_x, waveform_h, fill=color, width=2, dash=(2, 2), tags="drag_indicator")
                self.canvas.create_text(cur_x + 5, 20, text=f"{cur_t:.3f}s", anchor=tk.W, fill=color, font=("Arial", 10, "bold"), tags="drag_indicator_text")
            else:
                self.canvas.coords("drag_indicator", cur_x, 0, cur_x, waveform_h)
                self.canvas.coords("drag_indicator_text", cur_x + 5, 20)
                self.canvas.itemconfigure("drag_indicator_text", text=f"{cur_t:.3f}s")
                self.canvas.tag_raise("drag_indicator")
                self.canvas.tag_raise("drag_indicator_text")

        elif getattr(self, 'creating_seg', None):
            # VS-UX-01: 拖拽拉框建段半透明预览框
            gap_s = self.creating_seg['gap_s']
            gap_e = self.creating_seg['gap_e']
            raw_t = canvas_x / self.px_per_sec
            cur_t = max(gap_s + 0.01, min(gap_e - 0.01, raw_t))
            self.creating_seg['cur_t'] = cur_t

            s_t = min(self.creating_seg['anchor_t'], cur_t)
            e_t = max(self.creating_seg['anchor_t'], cur_t)
            dur = e_t - s_t
            x1 = s_t * self.px_per_sec
            x2 = e_t * self.px_per_sec

            self.canvas.delete("rubber_band")
            self.canvas.create_rectangle(x1, 0, x2, waveform_h, fill="#DCFCE7", stipple="gray25", outline="#10B981", width=2, dash=(4, 4), tags="rubber_band")
            self.canvas.create_text((x1 + x2) / 2, 24, text=f"+ {dur:.2f}s", font=("Microsoft YaHei", 10, "bold"), fill="#065F46", tags="rubber_band")

    def on_release(self, event):
        if self.dragging:
            seg_idx = self.dragging.get('seg_idx')
            if seg_idx is not None and 0 <= seg_idx < len(self.segments):
                self._cache_seg_splits_by_count(self.segments[seg_idx])
            self.canvas.delete("drag_indicator")
            self.canvas.delete("drag_indicator_text")
            self.dragging = None
            self.update_dynamic_labels()
            self.render_canvas()
            self.render_minimap()

        elif getattr(self, 'creating_seg', None):
            anchor_t = self.creating_seg['anchor_t']
            cur_t = self.creating_seg['cur_t']
            self.canvas.delete("rubber_band")
            self.creating_seg = None
            if abs(cur_t - anchor_t) >= 0.04:
                s_t = min(anchor_t, cur_t)
                e_t = max(anchor_t, cur_t)
                self._push_undo_snapshot()
                self.add_segment_at(s_t, e_t)

    def on_right_click(self, event):
        canvas_x = self.canvas.canvasx(event.x)
        if self.mode == 'cut' and self.cuts:
            time_sec = canvas_x / self.px_per_sec
            closest_idx = np.argmin([abs(c - time_sec) for c in self.cuts])
            if abs(self.cuts[closest_idx] - time_sec) < (20 / self.px_per_sec):
                self._push_undo_snapshot()
                self.cuts.pop(closest_idx)
                self.render_canvas()
                if hasattr(self, 'lbl_count'):
                    self.lbl_count.configure(text=f"当前切分点：{len(self.cuts)}")
        elif self.mode in ('review', 'edit'):
            seg_idx = self._get_segment_at_x(canvas_x)
            time_sec = canvas_x / self.px_per_sec

            if seg_idx is not None:
                self.selected_seg_idx = seg_idx
                self.render_canvas()
                seg = self.segments[seg_idx]
                is_deleted = (seg_idx in self.deleted_indices)
                menu = make_context_menu(self)
                if not is_deleted:
                    menu.add_command(label="试听此区段", command=lambda s=seg: self.play_segment(s['start'], s['end']))
                    menu.add_separator()
                    menu.add_command(label="删除此区段", command=lambda idx=seg_idx: self.toggle_delete_segment(idx))
                    if self.mode == 'edit':
                        lbl = seg.get('dyn_label', seg.get('label', ''))
                        syls = split_into_syllables(lbl)
                        if len(syls) > 1:
                            menu.add_command(label="重置此段内部蓝线", command=lambda idx=seg_idx: self.reset_inner_splits_with_undo(idx))
                else:
                    menu.add_command(label="恢复此区段", command=lambda idx=seg_idx: self.toggle_delete_segment(idx))
                post_context_menu(menu, event)
            else:
                # VS-BUG-03: 空白间隙右键插入区段并校验间隙大小
                gap = self._find_blank_gap_bounds(time_sec)
                if gap is not None:
                    gap_s, gap_e = gap
                    menu = make_context_menu(self)

                    def do_insert():
                        if gap_e - gap_s < 0.05:
                            messagebox.showinfo("提示", "当前位置空白间隙过小，无法插入新区间。", parent=self)
                            return
                        s = max(gap_s + 0.01, time_sec - 0.25)
                        e = min(gap_e - 0.01, time_sec + 0.25)
                        if e <= s:
                            s = gap_s + 0.005
                            e = gap_e - 0.005
                        self._push_undo_snapshot()
                        self.add_segment_at(s, e)

                    menu.add_command(label="在此处插入新区间", command=do_insert)
                    post_context_menu(menu, event)

    # --- 试听与播放进度游标 (VS-UX-05, VS-BUG-06) ---
    def stop_playback(self):
        """VS-BUG-06: 彻底停止播放并清理游标定时器"""
        self.is_playing = False
        if self._play_cursor_timer is not None:
            try:
                self.after_cancel(self._play_cursor_timer)
            except Exception:
                pass
            self._play_cursor_timer = None
        try:
            sd.stop()
        except Exception:
            pass
        if hasattr(self, 'canvas'):
            self.canvas.delete("play_cursor")

    def on_close(self):
        """VS-BUG-06: 窗口销毁时清理幽灵播放"""
        self.stop_playback()
        self.destroy()

    def play_segment(self, start_t, end_t):
        """VS-UX-05: 试听区段并启动实时绿色播放游标"""
        try:
            self.stop_playback()
            dur = end_t - start_t
            if dur <= 0.005:
                return
            part = self.snd.extract_part(from_time=start_t, to_time=end_t)
            audio_data = np.ascontiguousarray(part.values.T, dtype=np.float32)
            sd.play(audio_data, samplerate=int(part.sampling_frequency))

            self.is_playing = True
            self.play_start_wall = time.perf_counter()
            self.play_start_t = start_t
            self.play_end_t = end_t
            self._tick_play_cursor()
        except Exception as e:
            messagebox.showerror("错误", f"播放失败: {str(e)}", parent=self)

    def _tick_play_cursor(self):
        if not self.is_playing:
            if hasattr(self, 'canvas'):
                self.canvas.delete("play_cursor")
            return

        cur_t = self.play_start_t + (time.perf_counter() - self.play_start_wall)
        if cur_t >= self.play_end_t:
            self.stop_playback()
            return

        waveform_h = max(80, self.canvas_height - 24)
        cur_x = cur_t * self.px_per_sec
        if self.canvas.find_withtag("play_cursor"):
            self.canvas.coords("play_cursor", cur_x, 0, cur_x, waveform_h)
            self.canvas.tag_raise("play_cursor")
        else:
            self.canvas.create_line(cur_x, 0, cur_x, waveform_h, fill="#10B981", width=2, tags="play_cursor")

        self._play_cursor_timer = self.after(25, self._tick_play_cursor)

    # --- 快捷键导航与播放 (VS-UX-05) ---
    def on_key_space(self, event=None):
        if self.is_playing:
            self.stop_playback()
            return "break"

        target_idx = self.selected_seg_idx
        if target_idx is not None and 0 <= target_idx < len(self.segments) and target_idx not in self.deleted_indices:
            seg = self.segments[target_idx]
            self.play_segment(seg['start'], seg['end'])
            return "break"

        viewport_w = self.canvas.winfo_width()
        center_t = (self.canvas.canvasx(0) + viewport_w / 2) / self.px_per_sec
        best_idx = None
        min_dist = float('inf')
        for i, seg in enumerate(self.segments):
            if i in self.deleted_indices:
                continue
            seg_mid = (seg['start'] + seg['end']) / 2
            dist = abs(seg_mid - center_t)
            if dist < min_dist:
                min_dist = dist
                best_idx = i

        if best_idx is not None:
            self.selected_seg_idx = best_idx
            self.render_canvas()
            seg = self.segments[best_idx]
            self.play_segment(seg['start'], seg['end'])
        return "break"

    def on_key_prev(self, event=None):
        valid_indices = [i for i in range(len(self.segments)) if i not in self.deleted_indices]
        if not valid_indices:
            return "break"
        curr = self.selected_seg_idx
        if curr in valid_indices:
            idx_in_valid = valid_indices.index(curr)
            new_idx = valid_indices[max(0, idx_in_valid - 1)]
        else:
            new_idx = valid_indices[0]
        self.selected_seg_idx = new_idx
        self._scroll_seg_into_view(new_idx)
        self.render_canvas()
        return "break"

    def on_key_next(self, event=None):
        valid_indices = [i for i in range(len(self.segments)) if i not in self.deleted_indices]
        if not valid_indices:
            return "break"
        curr = self.selected_seg_idx
        if curr in valid_indices:
            idx_in_valid = valid_indices.index(curr)
            new_idx = valid_indices[min(len(valid_indices) - 1, idx_in_valid + 1)]
        else:
            new_idx = valid_indices[0]
        self.selected_seg_idx = new_idx
        self._scroll_seg_into_view(new_idx)
        self.render_canvas()
        return "break"

    def _scroll_seg_into_view(self, seg_idx):
        if not (0 <= seg_idx < len(self.segments)):
            return
        seg = self.segments[seg_idx]
        seg_mid = (seg['start'] + seg['end']) / 2
        vp_w = self.canvas.winfo_width()
        center_x = seg_mid * self.px_per_sec
        left_x = center_x - vp_w / 2
        fraction = max(0.0, min(1.0, left_x / max(1, self.canvas_width + 32)))
        self.canvas.xview_moveto(fraction)
        self.update_minimap_viewport()

    # --- 确认应用 ---
    def confirm(self):
        self.stop_playback()
        if self.mode == 'cut':
            if not self.cuts:
                return messagebox.showwarning("提示", "请至少添加一个切分点。", parent=self)
            sorted_cuts = sorted(self.cuts)
            segments = []
            last_t = 0
            for c in sorted_cuts:
                if c > last_t:
                    segments.append((last_t, c))
                last_t = c
            if last_t < self.duration:
                segments.append((last_t, self.duration))
            self.destroy()
            self.callback(segments, False)
        elif self.mode == 'review':
            kept_segments = [(seg['start'], seg['end']) for i, seg in enumerate(self.segments) if i not in self.deleted_indices]
            if not kept_segments:
                return messagebox.showwarning("提示", "至少需要保留一个区段。", parent=self)
            self.destroy()
            self.callback(kept_segments, False)
        else:
            kept_segments = []
            for idx, seg in enumerate(self.segments):
                if idx in self.deleted_indices:
                    continue
                is_mod = bool(seg.get('_user_modified')) or \
                         (abs(seg['start'] - seg.get('orig_start', seg['start'])) > 1e-4) or \
                         (abs(seg['end'] - seg.get('orig_end', seg['end'])) > 1e-4) or \
                         (seg.get('orig_inner_splits') is not None and len(seg.get('inner_splits', [])) == len(seg['orig_inner_splits']) and any(abs(a - b) > 1e-4 for a, b in zip(seg.get('inner_splits', []), seg['orig_inner_splits'])))

                kept_segments.append({
                    'id': seg.get('dyn_id'),
                    'old_id': seg.get('id'),
                    'start': seg['start'],
                    'end': seg['end'],
                    'inner_splits': list(seg.get('inner_splits', [])),
                    'is_modified': is_mod
                })
            self.destroy()
            self.callback(kept_segments, True, len(self.deleted_indices))
