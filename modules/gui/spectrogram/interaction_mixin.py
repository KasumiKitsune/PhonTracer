"""
Interactive canvas mouse events, boundary snapping, octave correction, and drag mixin.
"""
import copy
import tkinter as tk
import numpy as np


class InteractionMixin:

    def _get_char_index_for_time(self, t, prefer_right=True):
        item = self.current_item
        if not item or t is None:
            return None
        chars_bounds = item.get('chars_bounds', [])
        if not chars_bounds:
            return None

        tol = 1e-6
        indices = range(len(chars_bounds) - 1, -1, -1) if prefer_right else range(len(chars_bounds))
        for idx in indices:
            c_s, c_e = chars_bounds[idx]
            if c_s - tol <= t <= c_e + tol:
                return idx

        nearest_idx = None
        nearest_dist = float('inf')
        for idx, (c_s, c_e) in enumerate(chars_bounds):
            dist = min(abs(t - c_s), abs(t - c_e))
            if dist < nearest_dist:
                nearest_idx = idx
                nearest_dist = dist
        return nearest_idx

    def _set_cursor_position(self, t, char_index=None):
        self.cursor_x = self._clamp_time_to_item(t)
        self.cursor_char_index = char_index if char_index is not None else self._get_char_index_for_time(t)
        self._update_char_capsules_style()
        self.update_cursor_graphics()

    def _clamp_time_to_item(self, t):
        if t is None:
            return None
        domain = self._get_playback_domain()
        if not domain:
            return t
        t_s, t_e = domain
        return max(t_s, min(t_e, t))

    def _format_cursor_label(self):
        if self.cursor_x is None:
            return "0.000"
        if self.dragging and isinstance(self.dragging, tuple) and self.current_item:
            chars_bounds = self.current_item.get('chars_bounds', [])
            idx = self.cursor_char_index
            if idx is not None and 0 <= idx < len(chars_bounds):
                c_s, c_e = chars_bounds[idx]
                dur_ms = int(round(max(0.0, c_e - c_s) * 1000))
                return f"{self.cursor_x:.3f}s ({dur_ms}ms)"
        return f"{self.cursor_x:.3f}"

    def _event_has_shift(self, event):
        key = str(getattr(event, 'key', '') or '').lower()
        if 'shift' in key:
            return True
        gui_ev = getattr(event, 'guiEvent', None)
        if gui_ev is not None and hasattr(gui_ev, 'state'):
            return bool(gui_ev.state & 0x0001)
        return False

    def _event_has_alt(self, event):
        key = str(getattr(event, 'key', '') or '').lower()
        if 'alt' in key or 'meta' in key:
            return True
        gui_ev = getattr(event, 'guiEvent', None)
        if gui_ev is not None and hasattr(gui_ev, 'state'):
            # 注意：Windows Tk 下 0x0008 是 Num Lock，绝不可作为 Alt 掩码判断
            return bool(gui_ev.state & 0x20000)
        return False

    def _snap_to_zero_crossing(self, t, window=0.0025):
        item = self.current_item
        if not item or not item.get('snd') or t is None:
            return t
        snd = item['snd']
        try:
            dur = snd.get_total_duration()
            s_t = max(0.0, t - window)
            e_t = min(dur, t + window)
            if e_t <= s_t:
                return t
            part = snd.extract_part(from_time=s_t, to_time=e_t)
            vals = part.values[0]
            xs = part.xs() + s_t
            if len(vals) < 2:
                return t
            crossings = np.where(vals[:-1] * vals[1:] <= 0)[0]
            if len(crossings) == 0:
                return t
            cand_times = xs[crossings]
            best_idx = int(np.argmin(np.abs(cand_times - t)))
            return float(cand_times[best_idx])
        except Exception:
            return t

    def _find_closest_boundary(self, event_x, threshold_px=15, ref_ax=None):
        trans_ax = ref_ax or self.ax
        if not trans_ax or not self.current_item or event_x is None:
            return None, None, float('inf')
        chars_bounds = self.current_item.get('chars_bounds', [])
        paired_bounds = list(zip(chars_bounds, self.bound_lines))
        closest = None
        min_dist = threshold_px
        closest_priority = 99
        for i, ((c_s, c_e), _line_pair) in enumerate(paired_bounds):
            s_px = trans_ax.transData.transform((c_s, 0))[0]
            s_dist = abs(event_x - s_px)
            if s_dist < min_dist or (s_dist == min_dist and closest_priority > 0):
                closest = ('start', i)
                min_dist = s_dist
                closest_priority = 0

            e_px = trans_ax.transData.transform((c_e, 0))[0]
            e_dist = abs(event_x - e_px)
            if e_dist < min_dist or (e_dist == min_dist and closest_priority > 1):
                closest = ('end', i)
                min_dist = e_dist
                closest_priority = 1
        if closest is None:
            return None, None, float('inf')
        b_type, idx = closest
        b_time = chars_bounds[idx][0] if b_type == 'start' else chars_bounds[idx][1]
        return closest, b_time, min_dist

    def _try_fix_octave_jump(self, event):
        item = self.current_item
        if not item or not self.ax2 or event.x is None or event.y is None:
            return False
        app_params = getattr(self.app, 'last_params', {}) if self.app else {}
        mode = item.get('analysis_mode', app_params.get('analysis_mode', 'f0'))
        if mode == 'formant':
            return False
        if not item.get('pitch_data') and item.get('pitch'):
            pitch = item['pitch']
            item['pitch_data'] = {
                'xs': pitch.xs(),
                'freqs': pitch.selected_array['frequency'].copy(),
            }
        if not item.get('pitch_data'):
            return False

        xs = np.asarray(item['pitch_data']['xs'])
        freqs = item['pitch_data']['freqs']
        valid_mask = np.isfinite(freqs) & (freqs > 0)
        if not np.any(valid_mask):
            return False

        valid_indices = np.where(valid_mask)[0]
        pts = np.column_stack((xs[valid_indices], freqs[valid_indices]))
        pix = self.ax2.transData.transform(pts)
        dists = np.hypot(pix[:, 0] - event.x, pix[:, 1] - event.y)
        min_pos = int(np.argmin(dists))
        if dists[min_pos] > 20.0:
            return False

        target_idx = int(valid_indices[min_pos])
        f_cur = float(freqs[target_idx])
        neighbor_vals = [
            float(freqs[j])
            for j in valid_indices
            if j != target_idx and abs(int(j) - target_idx) <= 8 and freqs[j] > 0
        ]
        f_med = float(np.median(neighbor_vals)) if neighbor_vals else 200.0

        self._push_undo_state()
        if f_cur > f_med * 1.35:
            freqs[target_idx] = f_cur * 0.5
        elif f_cur < f_med * 0.72:
            freqs[target_idx] = f_cur * 2.0
        else:
            freqs[target_idx] = f_cur * 0.5 if f_cur >= 240.0 else f_cur * 2.0

        item['is_manual_edited'] = True
        item['_dirty_pitch'] = True
        item.pop('preview_f0', None)
        self._refresh_pitch_main_line_ydata()
        self._refresh_anomaly_layer()
        self.canvas.draw_idle()
        if self.app:
            self.app.mark_modified()
            panel = getattr(self.app, 'tree_panel', None) or getattr(self.app, 'project_panel', None)
            if panel and hasattr(panel, 'update_preview'):
                panel.update_preview()
        return True

    def _pan_viewport_to_center(self, center_t):
        if not self.ax or center_t is None:
            return
        domain = self._get_playback_domain()
        if not domain:
            return
        dom_s, dom_e = domain
        cur_s, cur_e = self.ax.get_xlim()
        span = cur_e - cur_s
        if span <= 0 or span >= (dom_e - dom_s):
            self._set_cursor_position(center_t)
            return
        new_s = max(dom_s, min(dom_e - span, center_t - span / 2.0))
        new_e = new_s + span
        self.ax.set_xlim([new_s, new_e])
        self._update_nav_lens((new_s, new_e))
        self.canvas.draw_idle()

    def on_press(self, event):
        if not self.ax or not self.ax2 or not self.current_item:
            return

        # 顶部缩略导航条：若命中红色边界线则直接进入红线拖拽，否则点击平移视野/定位游标
        if self.ax_nav is not None and event.inaxes == self.ax_nav:
            if not self.eraser_mode and event.button == 1:
                closest_nav, _, _ = self._find_closest_boundary(
                    event.x, threshold_px=15, ref_ax=self.ax_nav
                )
                if closest_nav is None:
                    if event.xdata is not None:
                        self.dragging = 'nav_pan'
                        self._pan_viewport_to_center(event.xdata)
                        self._set_cursor_position(event.xdata)
                    return
            else:
                return

        if event.inaxes not in [self.ax, self.ax2, self.ax_nav]:
            return

        # 橡皮擦模式下：左键擦除 (Shift+左键恢复)，右键局部恢复画笔
        if self.eraser_mode:
            if event.button == 3 or (event.button == 1 and self._event_has_shift(event)):
                self._suppress_next_context_menu = (event.button == 3)
                self._push_undo_state()
                self.restoring_points = True
                if event.xdata is not None:
                    self.restore_points_near(event)
                return
            if event.button == 1:
                self._push_undo_state()
                self.erasing = True
                if event.xdata is not None:
                    self.erase_points_near(event)
                return
            return

        # 非橡皮擦模式下右键：准备右键拖拽平移视野（未移动则松开弹右键菜单）
        if event.button == 3:
            self._pan_start_x = event.x
            self._pan_start_xlim = self.ax.get_xlim()
            self._pan_moved = False
            return

        if event.button != 1:
            return

        if self.is_playing:
            self._stop_playback(reset_cursor=False)
            return

        item = self.current_item
        chars_bounds = item.get('chars_bounds', [])

        # 优先判定是否命中红色边界线（15px 阈值，兼容主语图与顶部波形条），确保两侧边界拖拽绝不被胶囊、游标线或快捷键误拦截
        ref_ax = self.ax_nav if (self.ax_nav is not None and event.inaxes == self.ax_nav) else self.ax
        closest, _, min_dist = self._find_closest_boundary(event.x, threshold_px=15, ref_ax=ref_ax)

        if closest is None:
            # Alt + 左键：修复 F0 八度跳变点
            if self._event_has_alt(event):
                if self._try_fix_octave_jump(event):
                    return

            # 检查是否点击了顶部音节胶囊或双击了某个字区间内部
            if event.xdata is not None and chars_bounds:
                clicked_capsule_idx = None
                for i, txt in enumerate(self.char_texts):
                    try:
                        contains, _ = txt.contains(event)
                        if contains:
                            clicked_capsule_idx = i
                            break
                    except Exception:
                        pass

                if clicked_capsule_idx is not None and 0 <= clicked_capsule_idx < len(chars_bounds):
                    c_s, c_e = chars_bounds[clicked_capsule_idx]
                    self._set_cursor_position(c_s, clicked_capsule_idx)
                    self._start_playback_range(c_s, c_e)
                    return

                if getattr(event, 'dblclick', False):
                    for i, (c_s, c_e) in enumerate(chars_bounds):
                        if c_s <= event.xdata <= c_e:
                            self._set_cursor_position(c_s, i)
                            self._start_playback_range(c_s, c_e)
                            return

        self._drag_pre_snapshot = None
        self.dragging = closest
        if isinstance(closest, tuple):
            self._drag_pre_snapshot = self._capture_item_snapshot(item)
            bound_type, idx = closest
            # 检查是否为相邻两字重合边界 (c_e[i] == c_s[i+1])
            shared_left_idx = None
            if bound_type == 'start' and idx > 0 and abs(chars_bounds[idx - 1][1] - chars_bounds[idx][0]) < 1e-4:
                shared_left_idx = idx - 1
            elif bound_type == 'end' and idx + 1 < len(chars_bounds) and abs(chars_bounds[idx][1] - chars_bounds[idx + 1][0]) < 1e-4:
                shared_left_idx = idx

            boundary_time = chars_bounds[idx][0] if bound_type == 'start' else chars_bounds[idx][1]
            cursor_idx = idx
            if bound_type == 'end' and idx + 1 < len(chars_bounds):
                next_start = chars_bounds[idx + 1][0]
                if abs(next_start - boundary_time) < 0.02:
                    cursor_idx = idx + 1
                    boundary_time = next_start
            self._set_cursor_position(boundary_time, cursor_idx)

            if shared_left_idx is not None:
                if self._event_has_shift(event):
                    self.dragging = ('shared', shared_left_idx)
                else:
                    self.dragging = ('overlap_pending', shared_left_idx, event.x)
                self.bound_lines[shared_left_idx][1].set_color('#047857')
                self.bound_lines[shared_left_idx][1].set_linewidth(4)
                self.bound_lines[shared_left_idx + 1][0].set_color('#047857')
                self.bound_lines[shared_left_idx + 1][0].set_linewidth(4)
                if shared_left_idx < len(self.nav_bound_lines):
                    self.nav_bound_lines[shared_left_idx][1].set_color('#047857')
                    self.nav_bound_lines[shared_left_idx][1].set_linewidth(2.5)
                if shared_left_idx + 1 < len(self.nav_bound_lines):
                    self.nav_bound_lines[shared_left_idx + 1][0].set_color('#047857')
                    self.nav_bound_lines[shared_left_idx + 1][0].set_linewidth(2.5)
            elif bound_type == 'start':
                self.bound_lines[idx][0].set_color('#047857')
                self.bound_lines[idx][0].set_linewidth(4)
                if idx < len(self.nav_bound_lines):
                    self.nav_bound_lines[idx][0].set_color('#047857')
                    self.nav_bound_lines[idx][0].set_linewidth(2.5)
            elif bound_type == 'end':
                self.bound_lines[idx][1].set_color('#047857')
                self.bound_lines[idx][1].set_linewidth(4)
                if idx < len(self.nav_bound_lines):
                    self.nav_bound_lines[idx][1].set_color('#047857')
                    self.nav_bound_lines[idx][1].set_linewidth(2.5)
            self.canvas.draw_idle()
        else:
            if event.xdata is not None:
                # 若已有黄色播放选区且点击在其左右边缘 10px 内，则以对侧边界为锚点微调选区边界；否则以当前点击点为起点拉出新选区
                anchor_x = None
                if self._has_playback_selection() and event.x is not None:
                    sel_s, sel_e = self.playback_selection
                    sel_s_px = self.ax.transData.transform((sel_s, 0))[0]
                    sel_e_px = self.ax.transData.transform((sel_e, 0))[0]
                    s_d = abs(event.x - sel_s_px)
                    e_d = abs(event.x - sel_e_px)
                    if min(s_d, e_d) <= 10:
                        anchor_x = sel_e if s_d <= e_d else sel_s

                self._set_cursor_position(event.xdata)
                self.selection_anchor_x = anchor_x if anchor_x is not None else self.cursor_x
                self._select_press_x = event.x
                self.selection_drag_started = False
                self.dragging = 'play_select'
                if self.cursor_line is not None:
                    self.cursor_line.set_color('#064E3B')
                    self.cursor_line.set_linewidth(2.5)

    def on_motion(self, event):
        if not self.ax or not self.current_item:
            return

        # 右键拖拽平移视野
        if self._pan_start_x is not None and self._pan_start_xlim is not None:
            if getattr(event, 'button', None) != 3 or event.x is None:
                self._pan_start_x = None
                self._pan_start_xlim = None
                return
            if abs(event.x - self._pan_start_x) > 4:
                self._pan_moved = True
                self._suppress_next_context_menu = True
                try:
                    x0 = self.ax.transData.inverted().transform((self._pan_start_x, 0))[0]
                    x1 = self.ax.transData.inverted().transform((event.x, 0))[0]
                    dt = x1 - x0
                    s0, e0 = self._pan_start_xlim
                    span = e0 - s0
                    dom_s, dom_e = self._get_playback_domain() or (s0, e0)
                    new_s = max(dom_s, min(dom_e - span, s0 - dt))
                    new_e = new_s + span
                    self.ax.set_xlim([new_s, new_e])
                    self._update_nav_lens((new_s, new_e))
                    self.canvas.draw_idle()
                except Exception:
                    pass
            return

        if (self.dragging or self.erasing or self.restoring_points) and getattr(event, 'button', None) not in (1, 3):
            self.on_release(event)
            return

        if self.eraser_mode:
            self.canvas.get_tk_widget().config(cursor="crosshair")
            if event.x is not None and event.y is not None:
                self.update_eraser_circle(event)
            else:
                self.update_eraser_circle(None)

            if self.erasing and event.xdata is not None:
                self.erase_points_near(event)
            elif self.restoring_points and event.xdata is not None:
                self.restore_points_near(event)
            return

        if self.dragging == 'nav_pan':
            if event.xdata is not None:
                self._pan_viewport_to_center(event.xdata)
            return

        item = self.current_item
        chars_bounds = item.get('chars_bounds', [])

        if not self.dragging:
            is_hovering = False
            is_hovering_boundary = False
            needs_redraw = False
            if event.x is None:
                return
            ref_ax = self.ax_nav if (self.ax_nav is not None and event.inaxes == self.ax_nav) else self.ax
            in_valid_axes = event.inaxes in (self.ax, self.ax2, self.ax_nav)
            for i, ((c_s, c_e), (line_s, line_e)) in enumerate(zip(chars_bounds, self.bound_lines)):
                s_px = ref_ax.transData.transform((c_s, 0))[0]
                e_px = ref_ax.transData.transform((c_e, 0))[0]
                nav_pair = self.nav_bound_lines[i] if i < len(self.nav_bound_lines) else None

                if in_valid_axes and abs(event.x - s_px) < 15:
                    if line_s.get_linewidth() != 4:
                        line_s.set_linewidth(4)
                        line_s.set_color('#B91C1C')
                        needs_redraw = True
                    if nav_pair is not None and nav_pair[0].get_linewidth() != 2.5:
                        nav_pair[0].set_linewidth(2.5)
                        nav_pair[0].set_color('#B91C1C')
                        needs_redraw = True
                    is_hovering = True
                    is_hovering_boundary = True
                else:
                    if line_s.get_linewidth() != 2:
                        line_s.set_linewidth(2)
                        line_s.set_color('#EF4444')
                        needs_redraw = True
                    if nav_pair is not None and nav_pair[0].get_linewidth() != 1.0:
                        nav_pair[0].set_linewidth(1.0)
                        nav_pair[0].set_color('#EF4444')
                        needs_redraw = True

                if in_valid_axes and abs(event.x - e_px) < 15:
                    if line_e.get_linewidth() != 4:
                        line_e.set_linewidth(4)
                        line_e.set_color('#B91C1C')
                        needs_redraw = True
                    if nav_pair is not None and nav_pair[1].get_linewidth() != 2.5:
                        nav_pair[1].set_linewidth(2.5)
                        nav_pair[1].set_color('#B91C1C')
                        needs_redraw = True
                    is_hovering = True
                    is_hovering_boundary = True
                else:
                    if line_e.get_linewidth() != 2:
                        line_e.set_linewidth(2)
                        line_e.set_color('#EF4444')
                        needs_redraw = True
                    if nav_pair is not None and nav_pair[1].get_linewidth() != 1.0:
                        nav_pair[1].set_linewidth(1.0)
                        nav_pair[1].set_color('#EF4444')
                        needs_redraw = True

            if event.inaxes in (self.ax, self.ax2) and self._has_playback_selection():
                sel_s, sel_e = self.playback_selection
                sel_s_px = self.ax.transData.transform((sel_s, 0))[0]
                sel_e_px = self.ax.transData.transform((sel_e, 0))[0]
                if abs(event.x - sel_s_px) <= 10 or abs(event.x - sel_e_px) <= 10:
                    is_hovering = True

            if self.cursor_x is not None and self.cursor_line is not None:
                c_px = self.ax.transData.transform((self.cursor_x, 0))[0]
                if not is_hovering_boundary and event.inaxes in (self.ax, self.ax2) and abs(event.x - c_px) < 15:
                    if self.cursor_line.get_linewidth() != 2.5:
                        self.cursor_line.set_linewidth(2.5)
                        self.cursor_line.set_color('#065F46')
                        needs_redraw = True
                else:
                    if self.cursor_line.get_linewidth() != 1.5:
                        self.cursor_line.set_linewidth(1.5)
                        self.cursor_line.set_color('#1B5E20')
                        needs_redraw = True

            cursor_name = "sb_h_double_arrow" if is_hovering else "arrow"
            if self.canvas.get_tk_widget().cget("cursor") != cursor_name:
                self.canvas.get_tk_widget().config(cursor=cursor_name)

            if needs_redraw:
                self.canvas.draw_idle()
            return

        xdata = event.xdata
        if xdata is None and event.x is not None:
            try:
                ref_ax = self.ax_nav if (self.ax_nav is not None and event.inaxes == self.ax_nav) else self.ax
                calc_x = ref_ax.transData.inverted().transform((event.x, 0))[0]
                xlim = ref_ax.get_xlim()
                xdata = float(np.clip(calc_x, min(xlim), max(xlim)))
            except Exception:
                xdata = None

        if xdata is None:
            return

        if self.dragging == 'play_select':
            current_t = self._clamp_time_to_item(xdata)
            if current_t is None:
                return
            self._set_cursor_position(current_t)
            anchor = self.selection_anchor_x if self.selection_anchor_x is not None else current_t
            press_x = getattr(self, '_select_press_x', None)
            moved_enough = (
                self.selection_drag_started
                or press_x is None
                or event.x is None
                or abs(event.x - press_x) >= 2.0
            )
            if moved_enough and abs(current_t - anchor) > 0.005:
                self.selection_drag_started = True
                self._set_playback_selection(anchor, current_t)
            return

        if isinstance(self.dragging, tuple):
            # 多音节重叠红线方向消歧：向左拖抓前字终点 ('end', i)，向右拖抓后字起点 ('start', i+1)，按住 Shift 同步移动共享边界
            if self.dragging[0] == 'overlap_pending':
                _, shared_idx, press_x = self.dragging
                if self._event_has_shift(event):
                    self.dragging = ('shared', shared_idx)
                else:
                    shared_t = chars_bounds[shared_idx][1]
                    dx_px = (event.x - press_x) if (event.x is not None and press_x is not None) else 0.0
                    if abs(dx_px) >= 1.5 or abs(xdata - shared_t) > 1e-4:
                        if xdata < shared_t or dx_px < 0:
                            self.dragging = ('end', shared_idx)
                            self.bound_lines[shared_idx + 1][0].set_color('#EF4444')
                            self.bound_lines[shared_idx + 1][0].set_linewidth(2)
                            if shared_idx + 1 < len(self.nav_bound_lines):
                                self.nav_bound_lines[shared_idx + 1][0].set_color('#EF4444')
                                self.nav_bound_lines[shared_idx + 1][0].set_linewidth(1.0)
                        else:
                            self.dragging = ('start', shared_idx + 1)
                            self.bound_lines[shared_idx][1].set_color('#EF4444')
                            self.bound_lines[shared_idx][1].set_linewidth(2)
                            if shared_idx < len(self.nav_bound_lines):
                                self.nav_bound_lines[shared_idx][1].set_color('#EF4444')
                                self.nav_bound_lines[shared_idx][1].set_linewidth(1.0)
                    else:
                        return

            bound_type, idx = self.dragging[0], self.dragging[1]
            snapped_x = self._snap_to_zero_crossing(xdata, window=0.0025)

            if bound_type in ('shared', 'inner'):
                if idx + 1 >= len(chars_bounds):
                    self.dragging = None
                    return
                min_val = chars_bounds[idx][0] + 0.01
                max_val = chars_bounds[idx + 1][1] - 0.01
                if min_val > max_val:
                    min_val = max_val = (min_val + max_val) / 2.0
                new_t = float(np.clip(snapped_x, min_val, max_val))
                chars_bounds[idx][1] = new_t
                chars_bounds[idx + 1][0] = new_t
                self.cursor_x = new_t
                self.cursor_char_index = idx
            elif bound_type == 'start':
                if idx >= len(chars_bounds):
                    self.dragging = None
                    return
                min_val = chars_bounds[idx - 1][1] if idx > 0 else 0.0
                max_val = chars_bounds[idx][1] - 0.01
                if min_val > max_val:
                    min_val = max_val
                chars_bounds[idx][0] = float(np.clip(snapped_x, min_val, max_val))
                self.cursor_x = chars_bounds[idx][0]
                self.cursor_char_index = idx
            elif bound_type == 'end':
                if idx >= len(chars_bounds):
                    self.dragging = None
                    return
                min_val = chars_bounds[idx][0] + 0.01
                domain = self._get_playback_domain()
                max_xlim = max(self.ax.get_xlim()) if self.ax else 999999.0
                if domain is not None:
                    max_xlim = max(max_xlim, domain[1])
                max_val = chars_bounds[idx + 1][0] if (idx + 1 < len(chars_bounds)) else max_xlim
                if min_val > max_val:
                    max_val = min_val
                chars_bounds[idx][1] = float(np.clip(snapped_x, min_val, max_val))
                self.cursor_x = chars_bounds[idx][1]
                self.cursor_char_index = idx

            if chars_bounds:
                item['start'] = chars_bounds[0][0]
                item['end'] = chars_bounds[-1][1]
                self._sync_inner_splits_from_chars_bounds(item)
                item['is_manual_edited'] = True
                if getattr(self, 'var_t_start', None) is not None:
                    s_str = f"{item['start']:.3f}"
                    self.var_t_start.set(s_str)
                    if hasattr(self, 'entry_t_start'):
                        self.entry_t_start._last_val = s_str
                        self.entry_t_start._last_applied_val = s_str
                if getattr(self, 'var_t_end', None) is not None:
                    e_str = f"{item['end']:.3f}"
                    self.var_t_end.set(e_str)
                    if hasattr(self, 'entry_t_end'):
                        self.entry_t_end._last_val = e_str
                        self.entry_t_end._last_applied_val = e_str

            # Update the cursor line and text coordinates in real-time
            if self.cursor_line and self.cursor_text:
                self.cursor_line.set_xdata([self.cursor_x, self.cursor_x])
                self.cursor_text.set_position((self.cursor_x, 0.98))
                self.cursor_text.set_text(self._format_cursor_label())
        elif self.dragging == 'cursor':
            self._set_cursor_position(xdata)
            return

        self.update_lines()

    def _sync_inner_splits_from_chars_bounds(self, item=None):
        item = item or self.current_item
        if not item:
            return
        cb = item.get('chars_bounds', [])
        if len(cb) > 1:
            item['inner_splits'] = [
                round((cb[i][1] + cb[i + 1][0]) / 2.0, 6)
                for i in range(len(cb) - 1)
            ]
        else:
            item['inner_splits'] = []

    def on_release(self, event):
        if self._pan_start_x is not None:
            self._pan_start_x = None
            self._pan_start_xlim = None
            self._pan_moved = False
            return

        if self.eraser_mode:
            if self.erasing or self.restoring_points:
                self.erasing = False
                self.restoring_points = False
                if event.x is not None and event.y is not None:
                    self.update_eraser_circle(event)
                self.light_apply_eraser_changes()
                # 更新背景快照以反映最新擦除/恢复后的蓝色主线
                if self.canvas:
                    self.canvas.draw_idle()
            return

        if self.dragging:
            was_dragging = self.dragging
            self.dragging = None
            for line_s, line_e in self.bound_lines:
                line_s.set_color('#EF4444')
                line_e.set_color('#EF4444')
                line_s.set_linewidth(2)
                line_e.set_linewidth(2)
            for n_line_s, n_line_e in self.nav_bound_lines:
                n_line_s.set_color('#EF4444')
                n_line_e.set_color('#EF4444')
                n_line_s.set_linewidth(1.0)
                n_line_e.set_linewidth(1.0)
            if self.cursor_line:
                self.cursor_line.set_color('#1B5E20')
                self.cursor_line.set_linewidth(1.5)

            if was_dragging in ('cursor', 'play_select', 'nav_pan'):
                if was_dragging == 'play_select' and not self.selection_drag_started:
                    self._clear_playback_selection(redraw=False)
                self.update_cursor_graphics()
            else:
                if self._drag_pre_snapshot is not None and self.current_item:
                    if self._drag_pre_snapshot.get('chars_bounds') != self.current_item.get('chars_bounds'):
                        self._push_undo_state(self._drag_pre_snapshot)
                self._drag_pre_snapshot = None
                self._sync_inner_splits_from_chars_bounds()
                # 局部更新线条、音节胶囊与异常点，避免调用 plot_item_spectrogram 全量重建引发松手闪烁
                self._refresh_anomaly_layer()
                self.update_lines()
                self.update_cursor_graphics()
                self.update_ui_times()
                if self.app:
                    self.app.mark_modified()
                    panel = getattr(self.app, 'tree_panel', None) or getattr(self.app, 'project_panel', None)
                    if panel:
                        panel.update_item_icon(self.current_item_iid)
                        if hasattr(panel, 'update_preview'):
                            panel.update_preview()
            self.canvas.get_tk_widget().config(cursor="arrow")

    def on_scroll(self, event):
        if event.step is None:
            return

        if self.eraser_mode:
            delta = 2.0 * event.step
            self.erase_radius = max(3.0, min(100.0, self.erase_radius + delta))
            if event.x is not None and event.y is not None and event.inaxes is not None:
                self.update_eraser_circle(event)
            return

        # 非橡皮擦模式下滚轮横向缩放时间轴
        if not self.ax or not self.current_item or event.inaxes not in (self.ax, self.ax2, self.ax_nav):
            return
        domain = self._get_playback_domain()
        if not domain:
            return
        dom_s, dom_e = domain
        max_span = dom_e - dom_s
        if max_span <= 0:
            return

        cur_s, cur_e = self.ax.get_xlim()
        cur_span = cur_e - cur_s
        if cur_span <= 0:
            cur_span = max_span

        scale = 0.80 if event.step > 0 else 1.25
        min_span = min(max_span, max(0.08, max_span * 0.05))
        new_span = float(np.clip(cur_span * scale, min_span, max_span))

        anchor = event.xdata if event.xdata is not None else (cur_s + cur_e) / 2.0
        rel = float(np.clip((anchor - cur_s) / cur_span, 0.0, 1.0))
        new_s = anchor - rel * new_span
        new_s = max(dom_s, min(dom_e - new_span, new_s))
        new_e = new_s + new_span

        self.ax.set_xlim([new_s, new_e])
        self._update_nav_lens((new_s, new_e))
        self.canvas.draw_idle()

    def on_leave_fig(self, event):
        if self.eraser_mode:
            self.update_eraser_circle(None)

