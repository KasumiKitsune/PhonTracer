import os
import sys
import threading
import re
import subprocess
import platform
import numpy as np
import parselmouth

try:
    import sounddevice as sd
except ImportError:
    sd = None

import flet as ft
from flet import canvas as cv

from modules.data_utils import fuzzy_match_word_to_path

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
# 可视化段落编辑器
# ==========================================

class VisualSplitter(ft.AlertDialog):
    def __init__(self, snd, callback, existing_items=None, vad_segments=None, wordlist=None):
        super().__init__()
        self.snd = snd
        self.callback = callback
        self.wordlist = wordlist or []
        
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

        # 获取 envelope data
        full_values = self.snd.values[0]
        target_sr = 2000
        step = max(1, int(self.snd.sampling_frequency / target_sr))
        self.envelope_data = full_values[::step]
        
        self.update_dynamic_labels()
        
        self.title = ft.Text("音频段落编辑")
        
        if self.mode == 'cut': msg = "操作说明：【左键】添加切分线，【右键】删除最近线，【滚轮】上下滚动可以左右平移波形。"
        elif self.mode == 'review': msg = "VAD 自动检测完成。【右键/点击灰色按钮】删除噪声段，【点击蓝色按钮】试听，拖拽红线【微调边界】。"
        else: msg = "【右键/点击灰色按钮】删除错误段。拖动【红线】微调边界。完成后点击右下角确认。"
        
        self.lbl_info = ft.Text(msg, size=13, weight=ft.FontWeight.BOLD, color=ft.colors.ON_SURFACE_VARIANT)
        
        self.lbl_count = ft.Text(weight=ft.FontWeight.BOLD)
        self.update_review_count()
        
        self.lbl_zoom = ft.Text(f"缩放: {self.px_per_sec}", size=13)
        self.slider_zoom = ft.Slider(min=25, max=2000, divisions=79, value=self.px_per_sec, on_change=self.on_zoom_change)
        
        self.cvs = cv.Canvas(
            expand=True,
            on_resize=self.on_canvas_resize
        )
        
        self.gd = ft.GestureDetector(
            mouse_cursor=ft.MouseCursor.CLICK,
            on_tap_down=self.on_tap_down,
            on_pan_update=self.on_pan_update,
            on_pan_end=self.on_pan_end,
            on_secondary_tap_down=self.on_secondary_tap_down,
            on_scroll=self.on_scroll,
            content=self.cvs,
        )
        
        # 波形区域放置在一个可滚动的 Row 中
        self.scroll_view = ft.Row(
            controls=[self.gd],
            scroll=ft.ScrollMode.ALWAYS,
            expand=True,
        )
        
        self.main_container = ft.Container(
            content=self.scroll_view,
            border=ft.border.all(1, ft.colors.OUTLINE_VARIANT),
            border_radius=8,
            expand=True,
            bgcolor=ft.colors.SURFACE,
        )
        
        # 底部操作栏
        bottom_actions = []
        if self.mode == 'cut':
            bottom_actions.append(
                ft.ElevatedButton("清空所有点", icon=ft.icons.WARNING, style=ft.ButtonStyle(color=ft.colors.ERROR), on_click=self.clear_cuts)
            )
            
        bottom_actions.extend([
            ft.Row([self.lbl_zoom, self.slider_zoom]),
            ft.Container(expand=True),
            self.lbl_count,
            ft.FilledButton("确认并应用", icon=ft.icons.CHECK, on_click=self.confirm)
        ])

        self.content = ft.Container(
            width=900,
            height=500,
            content=ft.Column([
                self.lbl_info,
                self.main_container,
                ft.Row(bottom_actions)
            ])
        )

    def on_canvas_resize(self, e):
        self.canvas_width = int(self.duration * self.px_per_sec)
        self.canvas_height = e.height
        if self.canvas_width < 100: self.canvas_width = 100
        # 更新 GestureDetector 大小以匹配画布所需宽度
        self.gd.width = self.canvas_width
        self.cvs.width = self.canvas_width
        self.render_canvas()

    def update_dynamic_labels(self):
        word_idx = 0
        for i, seg in enumerate(self.segments):
            if i in self.deleted_indices:
                seg['dyn_label'] = "已剔除"
                seg['dyn_id'] = None
            else:
                if self.wordlist and word_idx < len(self.wordlist):
                    dyn_lbl = self.wordlist[word_idx]
                    seg['dyn_label'] = dyn_lbl
                    seg['dyn_id'] = word_idx
                    word_idx += 1
                elif hasattr(self, 'original_words') and word_idx < len(self.original_words):
                    dyn_lbl = self.original_words[word_idx]['label']
                    seg['dyn_label'] = dyn_lbl
                    seg['dyn_id'] = self.original_words[word_idx]['id']
                    word_idx += 1
                else:
                    seg['dyn_label'] = f"#{word_idx + 1}" if self.mode == 'review' else "【未分配段】"
                    seg['dyn_id'] = None
                    word_idx += 1

    def update_review_count(self):
        if self.mode == 'cut':
            self.lbl_count.value = f"当前切分点：{len(self.cuts)}"
            self.lbl_count.color = ft.colors.ON_SURFACE
        else:
            total = len(self.segments)
            deleted = len(self.deleted_indices)
            kept = total - deleted
            if deleted > 0:
                self.lbl_count.value = f"共 {total} 段 | 保留 {kept} 段 | 已移除 {deleted} 段"
                self.lbl_count.color = ft.colors.ERROR
            else:
                self.lbl_count.value = f"共 {total} 个检测区段"
                self.lbl_count.color = ft.colors.ON_SURFACE
        if self.page:
            self.lbl_count.update()

    def on_zoom_change(self, e):
        val = e.control.value
        self.px_per_sec = round(float(val) / 25) * 25
        self.lbl_zoom.value = f"缩放: {int(self.px_per_sec)}"
        if self.page:
            self.lbl_zoom.update()

        self.canvas_width = int(self.duration * self.px_per_sec)
        self.gd.width = self.canvas_width
        self.cvs.width = self.canvas_width
        self.render_canvas()

    def on_scroll(self, e):
        # flet Row scroll 处理比较简单，暂时由原生滚动条处理水平滚动
        pass

    def render_canvas(self):
        if not hasattr(self, 'canvas_height'): return

        self.cvs.shapes.clear()
        
        mid_y = self.canvas_height / 2 + 20
        
        # 绘制片段背景
        if self.mode in ('edit', 'review'):
            for i, seg in enumerate(self.segments):
                x1 = seg['start'] * self.px_per_sec
                x2 = seg['end'] * self.px_per_sec
                bg_color = ft.colors.RED_50 if i in self.deleted_indices else ft.colors.BLUE_50
                self.cvs.shapes.append(
                    cv.Rect(x1, 0, x2 - x1, self.canvas_height, paint=ft.Paint(color=bg_color, style=ft.PaintingStyle.FILL))
                )

        # 中心线
        self.cvs.shapes.append(
            cv.Line(0, mid_y, self.canvas_width, mid_y, paint=ft.Paint(color=ft.colors.OUTLINE_VARIANT, stroke_width=1))
        )
        
        # 绘制波形 (使用 Path 提升性能)
        draw_step = max(1, len(self.envelope_data) // int(self.canvas_width))
        draw_values = self.envelope_data[::draw_step]
        n = len(draw_values)
        if n > 1:
            path_elements = []
            start_x = 0
            start_y = mid_y - (draw_values[0] * (self.canvas_height/2 - 30) * 0.9)
            path_elements.append(cv.Path.MoveTo(start_x, start_y))

            for i in range(1, n):
                val = draw_values[i]
                x = (i / n) * self.canvas_width
                y = mid_y - (val * (self.canvas_height/2 - 30) * 0.9)
                path_elements.append(cv.Path.LineTo(x, y))

            self.cvs.shapes.append(
                cv.Path(elements=path_elements, paint=ft.Paint(color=ft.colors.ON_SURFACE_VARIANT, stroke_width=1, style=ft.PaintingStyle.STROKE))
            )

        # 刻度
        step_sec = 1 if self.px_per_sec > 50 else 5
        if self.px_per_sec > 200: step_sec = 0.5
        for t in np.arange(0, self.duration, step_sec):
            x = t * self.px_per_sec
            self.cvs.shapes.append(cv.Line(x, self.canvas_height-15, x, self.canvas_height, paint=ft.Paint(color=ft.colors.OUTLINE_VARIANT, stroke_width=1)))
            self.cvs.shapes.append(cv.Text(x+2, self.canvas_height-20, f"{t}s", style=ft.TextStyle(size=10, color=ft.colors.OUTLINE)))

        # 边界与标签
        self.play_rects = []
        self.delete_rects = []

        if self.mode == 'cut':
            for cut in self.cuts:
                x = cut * self.px_per_sec
                self.cvs.shapes.append(cv.Line(x, 0, x, self.canvas_height, paint=ft.Paint(color=ft.colors.ERROR, stroke_width=2)))
        elif self.mode in ('edit', 'review'):
            for i, seg in enumerate(self.segments):
                is_deleted = i in self.deleted_indices
                x1 = seg['start'] * self.px_per_sec
                x2 = seg['end'] * self.px_per_sec
                
                line_color = ft.colors.OUTLINE if is_deleted else ft.colors.ERROR
                # flet 暂时没有 dash，我们可以简化一下，或者直接改变颜色

                self.cvs.shapes.append(cv.Line(x1, 0, x1, self.canvas_height, paint=ft.Paint(color=line_color, stroke_width=2)))
                self.cvs.shapes.append(cv.Line(x2, 0, x2, self.canvas_height, paint=ft.Paint(color=line_color, stroke_width=2)))
                
                tag_y = 25
                display_label = seg.get('dyn_label', seg['label'])
                
                cx = (x1 + x2) / 2
                bg_color = ft.colors.GREY_500 if is_deleted else ft.colors.BLUE_500
                text = f"✕ {display_label}" if is_deleted else f"▶ {display_label}"

                # 简单计算 pill 宽度 (粗略)
                w = len(text) * 10 + 20
                pill_rect = [cx - w/2, tag_y - 12, cx + w/2, tag_y + 12]

                self.cvs.shapes.append(cv.Rect(pill_rect[0], pill_rect[1], w, 24, border_radius=12, paint=ft.Paint(color=bg_color, style=ft.PaintingStyle.FILL)))
                self.cvs.shapes.append(cv.Text(cx - w/2 + 10, tag_y - 8, text, style=ft.TextStyle(size=12, color=ft.colors.WHITE, weight=ft.FontWeight.BOLD)))

                if is_deleted:
                    self.delete_rects.append({'idx': i, 'rect': pill_rect})
                else:
                    self.play_rects.append({'idx': i, 'start': seg['start'], 'end': seg['end'], 'rect': pill_rect})

        self.cvs.update()

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

    def on_tap_down(self, e: ft.TapEvent):
        x, y = e.local_x, e.local_y
        time_sec = x / self.px_per_sec
        
        if self.mode in ('edit', 'review'):
            for pr in self.play_rects:
                r = pr['rect']
                if r[0] <= x <= r[2] and r[1] <= y <= r[3]:
                    return self.play_segment(pr['start'], pr['end'])
            for dr in self.delete_rects:
                r = dr['rect']
                if r[0] <= x <= r[2] and r[1] <= y <= r[3]:
                    return self.toggle_delete_segment(dr['idx'])

            idx, bound = self._get_element_at_x(x)
            if idx is not None:
                self.dragging = {'seg_idx': idx, 'bound': bound}
                
        elif self.mode == 'cut' and 0 <= time_sec <= self.duration:
            self.cuts.append(time_sec)
            self.cuts.sort()
            self.render_canvas()
            self.update_review_count()

    def on_pan_update(self, e: ft.DragUpdateEvent):
        if self.dragging:
            x = e.local_x
            time_sec = max(0, min(self.duration, x / self.px_per_sec))
            idx = self.dragging['seg_idx']
            bound = self.dragging['bound']
            seg = self.segments[idx]
            
            if bound == 'start': seg['start'] = min(time_sec, seg['end'] - 0.01)
            elif bound == 'end': seg['end'] = max(time_sec, seg['start'] + 0.01)
            self.render_canvas()

    def on_pan_end(self, e: ft.DragEndEvent):
        self.dragging = None

    def on_secondary_tap_down(self, e: ft.TapEvent):
        x = e.local_x
        if self.mode == 'cut' and self.cuts:
            time_sec = x / self.px_per_sec
            closest_idx = np.argmin([abs(c - time_sec) for c in self.cuts])
            if abs(self.cuts[closest_idx] - time_sec) < (20 / self.px_per_sec):
                self.cuts.pop(closest_idx)
                self.render_canvas()
                self.update_review_count()
        elif self.mode in ('review', 'edit'):
            seg_idx = self._get_segment_at_x(x)
            if seg_idx is not None: self.toggle_delete_segment(seg_idx)

    def toggle_delete_segment(self, idx):
        if idx in self.deleted_indices: self.deleted_indices.discard(idx)
        else: self.deleted_indices.add(idx)
        self.update_dynamic_labels()
        self.update_review_count()
        self.render_canvas()

    def clear_cuts(self, e):
        self.cuts = []
        self.update_review_count()
        self.render_canvas()

    def play_segment(self, start_t, end_t):
        if not sd:
            self.page.open(ft.SnackBar(ft.Text("错误: 缺少 sounddevice 模块，无法试听。")))
            return
        try:
            part = self.snd.extract_part(from_time=start_t, to_time=end_t)
            audio_data = np.ascontiguousarray(part.values.T, dtype=np.float32)
            sd.play(audio_data, samplerate=int(part.sampling_frequency))
        except Exception as e:
            self.page.open(ft.SnackBar(ft.Text(f"播放失败: {str(e)}")))

    def confirm(self, e):
        if self.mode == 'cut':
            if not self.cuts:
                self.page.open(ft.SnackBar(ft.Text("提示: 请至少添加一个切分点。")))
                return
            sorted_cuts = sorted(self.cuts)
            segments = []
            last_t = 0
            for c in sorted_cuts:
                if c > last_t: segments.append((last_t, c))
                last_t = c
            if last_t < self.duration: segments.append((last_t, self.duration))
            self.page.close(self)
            self.callback(segments, False)
        elif self.mode == 'review':
            kept_segments = [(seg['start'], seg['end']) for i, seg in enumerate(self.segments) if i not in self.deleted_indices]
            if not kept_segments:
                self.page.open(ft.SnackBar(ft.Text("提示: 至少需要保留一个区段。")))
                return
            self.page.close(self)
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
            self.page.close(self)
            self.callback(kept_segments, True, len(self.deleted_indices))


# ==========================================
# 主程序 App
# ==========================================

def main(page: ft.Page):
    page.title = "PhonTracer - 独立音频处理套件"
    page.window_width = 900
    page.window_height = 650
    page.theme = ft.Theme(color_scheme_seed=ft.colors.ORANGE)
    page.theme_mode = ft.ThemeMode.LIGHT
    page.padding = 20

    # 状态变量
    state = {
        "merge_files": [],
        "split_source": None,
        "wordlist": [],
        "custom_segments": None
    }

    # UI 引用
    merge_list_view = ft.ListView(expand=True, spacing=5, padding=10)
    lbl_split_source = ft.Text("未选择", color=ft.colors.ON_SURFACE_VARIANT, expand=True)
    txt_wordlist = ft.TextField(multiline=True, expand=True, border_color=ft.colors.OUTLINE_VARIANT, on_change=lambda e: validate_wordlist())
    lbl_wordlist_status = ft.Text("字表为空", size=12, color=ft.colors.ON_SURFACE_VARIANT)

    var_gap = ft.TextField(value="0.5", width=100, dense=True)
    var_trim = ft.Switch(value=True, label="智能剔除边缘空白杂音")
    var_buffer = ft.TextField(value="0.1", width=100, dense=True)

    progress_bar = ft.ProgressBar(value=0, visible=False, color=ft.colors.PRIMARY)
    lbl_status = ft.Text("就绪", color=ft.colors.GREEN, weight=ft.FontWeight.BOLD)

    # ==========================
    # 辅助工具函数
    # ==========================

    def show_snack(msg, is_error=False):
        page.open(ft.SnackBar(ft.Text(msg), bgcolor=ft.colors.ERROR if is_error else ft.colors.INVERSE_SURFACE))
        
    def set_loading(is_loading, msg=""):
        progress_bar.visible = is_loading
        progress_bar.value = None if is_loading else 0
        lbl_status.value = msg if msg else "就绪"
        lbl_status.color = ft.colors.PRIMARY if is_loading else ft.colors.GREEN
        page.update()

    def update_progress(val):
        progress_bar.value = val
        page.update()

    # ==========================
    # Merge Tab 逻辑
    # ==========================

    def render_merge_list():
        merge_list_view.controls.clear()
        for i, path in enumerate(state["merge_files"]):

            def make_drag_accept(target_idx):
                def on_accept(e):
                    src_control = page.get_control(e.src_id)
                    src_idx = int(src_control.data)
                    item = state["merge_files"].pop(src_idx)
                    state["merge_files"].insert(target_idx, item)
                    render_merge_list()
                return on_accept

            item_container = ft.Container(
                content=ft.Row([
                    ft.Icon(ft.icons.DRAG_HANDLE, color=ft.colors.OUTLINE),
                    ft.Text(os.path.basename(path), expand=True, tooltip=path),
                    ft.IconButton(ft.icons.DELETE, icon_color=ft.colors.ERROR, on_click=lambda e, p=path: remove_merge_file(p))
                ]),
                padding=10,
                border=ft.border.all(1, ft.colors.OUTLINE_VARIANT),
                border_radius=8,
                bgcolor=ft.colors.SURFACE,
            )

            draggable = ft.Draggable(
                group="merge_list",
                content=item_container,
                data=str(i)
            )

            drag_target = ft.DragTarget(
                group="merge_list",
                content=draggable,
                on_accept=make_drag_accept(i)
            )
            # Flet Draggable content 必须具有 unique ID 或在 data 里传, 用 src_id

            merge_list_view.controls.append(drag_target)
        page.update()

    def add_merge_files(e):
        def on_result(e: ft.FilePickerResultEvent):
            if e.files:
                for f in e.files:
                    if f.path not in state["merge_files"]:
                        state["merge_files"].append(f.path)
                render_merge_list()
        file_picker.on_result = on_result
        file_picker.pick_files(allow_multiple=True, allowed_extensions=["wav", "mp3"])

    def remove_merge_file(path):
        if path in state["merge_files"]:
            state["merge_files"].remove(path)
            render_merge_list()

    def clear_merge_list(e):
        state["merge_files"].clear()
        render_merge_list()

    def import_wordlist_for_sort(e):
        if not state["merge_files"]:
            return show_snack("合并列表为空，请先添加音频文件", True)

        def on_result(e: ft.FilePickerResultEvent):
            if e.files:
                path = e.files[0].path
                try:
                    with open(path, 'r', encoding='utf-8') as f: text = f.read()
                except:
                    try:
                        with open(path, 'r', encoding='gbk') as f: text = f.read()
                    except: return show_snack("读取文件失败", True)

                flat_words = parse_wordlist(text)
                if not flat_words: return show_snack("字表解析结果为空", True)
                
                sorted_paths = []
                available_paths = list(state["merge_files"])
                used_indices = set()

                for word in flat_words:
                    idx = fuzzy_match_word_to_path(word, available_paths, used_indices=list(used_indices))
                    if idx is not None:
                        sorted_paths.append(available_paths[idx])
                        used_indices.add(idx)

                for i, p in enumerate(available_paths):
                    if i not in used_indices:
                        sorted_paths.append(p)

                state["merge_files"] = sorted_paths
                render_merge_list()
                show_snack(f"已根据字表重新排序 {len(used_indices)} 个文件。")

        file_picker.on_result = on_result
        file_picker.pick_files(allowed_extensions=["txt", "csv"])

    def process_merge(e):
        if not state["merge_files"]: return show_snack("请先添加音频文件", True)
        try: gap_sec = float(var_gap.value)
        except ValueError: return show_snack("间隔时间必须为数字", True)
        
        def on_result(e: ft.FilePickerResultEvent):
            if e.path:
                out_path = e.path if e.path.endswith('.wav') else e.path + '.wav'
                def run():
                    set_loading(True, "正在合并音频...")
                    try:
                        target_sr = 44100
                        all_vals = []
                        gap_samples = int(target_sr * gap_sec)
                        gap_array = np.zeros(gap_samples)

                        total = len(state["merge_files"])
                        for i, path in enumerate(state["merge_files"]):
                            snd = parselmouth.Sound(path)
                            if snd.sampling_frequency != target_sr:
                                snd = snd.resample(target_sr)
                            all_vals.append(snd.values[0])
                            all_vals.append(gap_array)
                            update_progress((i+1)/total)

                        if all_vals:
                            merged_vals = np.concatenate(all_vals[:-1])
                            merged_snd = parselmouth.Sound(np.array([merged_vals]), sampling_frequency=target_sr)
                            merged_snd.save(out_path, "WAV")

                        show_snack(f"合并完成！保存在: {out_path}")
                    except Exception as ex:
                        show_snack(f"合并失败: {str(ex)}", True)
                    finally:
                        set_loading(False)
                threading.Thread(target=run, daemon=True).start()
                
        file_picker.on_result = on_result
        file_picker.save_file(allowed_extensions=["wav"])


    # ==========================
    # Split Tab 逻辑
    # ==========================

    def select_split_source(e):
        def on_result(e: ft.FilePickerResultEvent):
            if e.files:
                path = e.files[0].path
                state["split_source"] = path
                lbl_split_source.value = os.path.basename(path)
                state["custom_segments"] = None
                page.update()
        file_picker.on_result = on_result
        file_picker.pick_files(allowed_extensions=["wav", "mp3"])

    def import_wordlist(e):
        def on_result(e: ft.FilePickerResultEvent):
            if e.files:
                path = e.files[0].path
                try:
                    with open(path, 'r', encoding='utf-8') as f: text = f.read()
                except UnicodeDecodeError:
                    try:
                        with open(path, 'r', encoding='gbk') as f: text = f.read()
                    except Exception as ex:
                        return show_snack(f"读取文件失败: {ex}", True)
                txt_wordlist.value = text
                validate_wordlist()
                page.update()
        file_picker.on_result = on_result
        file_picker.pick_files(allowed_extensions=["txt", "csv"])

    def validate_wordlist():
        text = txt_wordlist.value.strip() if txt_wordlist.value else ""
        state["wordlist"] = parse_wordlist(text)
        count = len(state["wordlist"])
        if count > 0:
            lbl_wordlist_status.value = f"已加载 {count} 个词汇"
            lbl_wordlist_status.color = ft.colors.GREEN
        else:
            lbl_wordlist_status.value = "字表为空"
            lbl_wordlist_status.color = ft.colors.ON_SURFACE_VARIANT
        page.update()

    def match_segments_to_wordlist(e):
        if not state["split_source"]: return show_snack("请先选择长音频源文件", True)
        validate_wordlist()
        if not state["wordlist"]: return show_snack("字表为空，请先输入或导入字表", True)

        def run():
            set_loading(True, "正在进行 VAD 检测与匹配...")
            try:
                snd = parselmouth.Sound(state["split_source"])
                vad_segs = macroscopic_vad(snd)
                state["custom_segments"] = vad_segs
                
                msg = f"匹配完成！\n检测到音频段落: {len(vad_segs)} 个\n字表词汇: {len(state['wordlist'])} 个"
                if len(vad_segs) != len(state["wordlist"]):
                    msg += "\n\n注意：数量不一致，可能需要手动调整。"
                
                # Flet 中可以在非主线程调用 update/弹窗, 但最好同步
                def show_dialog():
                    dlg = ft.AlertDialog(title=ft.Text("匹配结果"), content=ft.Text(msg))
                    page.open(dlg)
                show_dialog()
            except Exception as ex:
                show_snack(f"匹配失败: {ex}", True)
            finally:
                set_loading(False)
        threading.Thread(target=run, daemon=True).start()

    def open_visual_splitter(e):
        if not state["split_source"]: return show_snack("请先选择长音频源文件", True)

        def on_confirm(segments, is_update=False, deleted_count=0):
            parsed_segs = []
            for seg in segments:
                if isinstance(seg, tuple): parsed_segs.append(seg)
                else: parsed_segs.append((seg['start'], seg['end']))
            state["custom_segments"] = parsed_segs
            show_snack(f"已保存 {len(parsed_segs)} 个自定义切分段！")

        def run():
            set_loading(True, "正在加载并检测音频区段...")
            try:
                snd = parselmouth.Sound(state["split_source"])
                if state["custom_segments"]:
                    existing_items = [{'id': i, 'label': f'#{i+1}', 'start': s, 'end': e} for i, (s, e) in enumerate(state["custom_segments"])]
                    vs = VisualSplitter(snd, on_confirm, existing_items=existing_items, wordlist=state["wordlist"])
                else:
                    vad_segs = macroscopic_vad(snd)
                    vs = VisualSplitter(snd, on_confirm, vad_segments=vad_segs, wordlist=state["wordlist"])

                page.open(vs)
            except Exception as ex:
                show_snack(f"加载失败: {ex}", True)
            finally:
                set_loading(False)

        threading.Thread(target=run, daemon=True).start()

    def _send_files_to_main_app(files):
        target = "main.py"
        if not os.path.exists(target):
            parent_main = os.path.join("..", "main.py")
            if os.path.exists(parent_main): target = parent_main
            else: target = None
            
        if target:
            subprocess.Popen([sys.executable, target] + files)
        else:
            show_snack("拆分已完成，但未能找到主程序 main.py，请手动将其拖拽入 PhonTracer。")

    def process_split(send_to_main=False):
        if not state["split_source"]: return show_snack("请选择长音频源文件", True)
        validate_wordlist()
        if not state["wordlist"]: return show_snack("字表为空，请粘贴字表。", True)
        try: buffer_sec = float(var_buffer.value)
        except ValueError: return show_snack("缓冲时间必须为数字", True)
        
        def on_result(e: ft.FilePickerResultEvent):
            if e.path:
                out_dir = e.path
                do_trim = var_trim.value
                def run():
                    set_loading(True, "正在分析并拆分音频...")
                    try:
                        snd = parselmouth.Sound(state["split_source"])
                        if state["custom_segments"]: segs = state["custom_segments"]
                        else: segs = macroscopic_vad(snd)

                        if not segs:
                            return show_snack("未能在音频中检测到任何有效发音段！", True)

                        total = min(len(segs), len(state["wordlist"]))
                        saved_files = []

                        for i in range(total):
                            s, e_time = segs[i]
                            word = state["wordlist"][i]

                            if do_trim:
                                part = snd.extract_part(from_time=s, to_time=e_time)
                                vals = part.values[0]
                                xs = part.xs()
                                threshold = 10 ** (-50 / 20)
                                valid_idx = np.where(np.abs(vals) > threshold)[0]
                                if len(valid_idx) > 0:
                                    s = s + xs[valid_idx[0]]
                                    e_time = s + xs[valid_idx[-1]]

                            s = max(0, s - buffer_sec)
                            e_time = min(snd.get_total_duration(), e_time + buffer_sec)

                            if e_time > s:
                                extract = snd.extract_part(from_time=s, to_time=e_time)
                                safe_word = re.sub(r'[\\/*?:"<>|]', "", word)
                                out_file = os.path.join(out_dir, f"{str(i+1).zfill(3)}_{safe_word}.wav")
                                extract.save(out_file, "WAV")
                                saved_files.append(out_file)

                            update_progress((i+1)/total)

                        if send_to_main and saved_files:
                            _send_files_to_main_app(saved_files)
                        else:
                            show_snack(f"成功拆分 {total} 段音频并保存到: {out_dir}")
                    except Exception as ex:
                        show_snack(f"拆分失败: {ex}", True)
                    finally:
                        set_loading(False)
                threading.Thread(target=run, daemon=True).start()
                
        file_picker.on_result = on_result
        file_picker.get_directory_path(title="选择拆分后音频的保存文件夹")

    # ==========================
    # 组合 UI 元素
    # ==========================
    
    file_picker = ft.FilePicker()
    page.overlay.append(file_picker)

    # --- Top Bar ---
    top_bar = ft.Row([
        ft.Text("PhonTracer 配套音频工具", size=20, weight=ft.FontWeight.BOLD),
        ft.Container(expand=True),
        lbl_status
    ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN)

    # --- Merge Tab ---
    merge_left = ft.Column([
        ft.ElevatedButton("添加音频文件", icon=ft.icons.ADD, on_click=add_merge_files),
        ft.Text("待合并的音频文件路径 (可拖拽调整顺序，点击垃圾桶删除)", size=12, color=ft.colors.ON_SURFACE_VARIANT),
        ft.Container(content=merge_list_view, expand=True, border=ft.border.all(1, ft.colors.OUTLINE_VARIANT), border_radius=8)
    ], expand=True)

    merge_right = ft.Container(
        width=280,
        padding=15,
        bgcolor=ft.colors.SURFACE_VARIANT,
        border_radius=10,
        content=ft.Column([
            ft.Text("合并参数", size=16, weight=ft.FontWeight.BOLD),
            ft.Text("音频间隔 (插入静音秒数):"),
            var_gap,
            ft.Container(expand=True),
            ft.FilledButton("导入字表自动排序", icon=ft.icons.FORMAT_LIST_NUMBERED, on_click=import_wordlist_for_sort, width=250),
            ft.FilledButton("清空列表", icon=ft.icons.CLEAR_ALL, style=ft.ButtonStyle(color=ft.colors.ERROR), on_click=clear_merge_list, width=250),
            ft.FilledButton("合并并导出音频", icon=ft.icons.SAVE, style=ft.ButtonStyle(bgcolor=ft.colors.GREEN), on_click=process_merge, width=250)
        ])
    )

    # --- Split Tab ---
    split_top = ft.Row([
        ft.ElevatedButton("选择长音频源", icon=ft.icons.AUDIO_FILE, on_click=select_split_source),
        ft.FilledTonalButton("音频段落编辑", icon=ft.icons.REMOVE_RED_EYE, on_click=open_visual_splitter),
        lbl_split_source
    ])

    split_left = ft.Column([
        ft.Row([
            ft.Text("粘贴字表文本 或", size=16, weight=ft.FontWeight.BOLD),
            ft.TextButton("导入字表文件", icon=ft.icons.UPLOAD_FILE, on_click=import_wordlist)
        ]),
        txt_wordlist,
        lbl_wordlist_status
    ], expand=True)

    split_right = ft.Container(
        width=280,
        padding=15,
        bgcolor=ft.colors.SURFACE_VARIANT,
        border_radius=10,
        content=ft.Column([
            ft.Text("拆分设置", size=16, weight=ft.FontWeight.BOLD),
            var_trim,
            ft.Text("保存区段首尾缓冲 (秒):"),
            var_buffer,
            ft.FilledTonalButton("一键匹配 (字表与音频)", icon=ft.icons.CHECK, on_click=match_segments_to_wordlist, width=250),
            ft.Container(expand=True),
            ft.FilledButton("拆分并发送到主程序", icon=ft.icons.SEND, on_click=lambda e: process_split(send_to_main=True), width=250),
            ft.FilledButton("仅拆分保存到目录", icon=ft.icons.SAVE, style=ft.ButtonStyle(bgcolor=ft.colors.GREEN), on_click=lambda e: process_split(send_to_main=False), width=250)
        ])
    )

    # --- Tabs ---
    tabs = ft.Tabs(
        selected_index=0,
        animation_duration=300,
        expand=True,
        tabs=[
            ft.Tab(
                text="多音频合并 (拼长音)",
                content=ft.Row([merge_left, merge_right], expand=True, padding=ft.padding.only(top=10))
            ),
            ft.Tab(
                text="长音频拆分 (按字表)",
                content=ft.Column([
                    split_top,
                    ft.Row([split_left, split_right], expand=True, padding=ft.padding.only(top=10))
                ], expand=True, padding=ft.padding.only(top=10))
            )
        ]
    )
    
    page.add(
        top_bar,
        tabs,
        progress_bar
    )

if __name__ == '__main__':
    ft.app(target=main)

