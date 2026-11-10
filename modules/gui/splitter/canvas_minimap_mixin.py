"""
Canvas and minimap rendering and envelope calculation mixin for VisualSplitter.
"""
import numpy as np
import tkinter as tk
import customtkinter as ctk
from modules.data_utils import split_into_syllables


class CanvasMinimapMixin:

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

