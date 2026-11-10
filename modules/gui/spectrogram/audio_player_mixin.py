"""
Sounddevice playback, loop mode, WSOLA time stretch, and audio selection mixin.
"""
import time
import tkinter as tk
from tkinter import messagebox
import numpy as np
import sounddevice as sd
import matplotlib.patches as mpatches
from modules.audio_core import load_compatible_sound


class AudioPlayerMixin:

    def _get_playback_domain(self):
        if self.playback_domain_start is not None and self.playback_domain_end is not None:
            return (self.playback_domain_start, self.playback_domain_end)

        item = self.current_item
        if not item:
            return None
        snd = item.get('snd')
        if snd:
            try:
                return (0.0, snd.get_total_duration())
            except Exception:
                pass
        if item.get('start') is not None and item.get('end') is not None:
            return (item['start'], item['end'])
        return None

    def _has_playback_selection(self):
        return (
            self.playback_selection is not None
            and self.playback_selection[1] - self.playback_selection[0] > 0.005
        )

    def _set_playback_selection(self, start_t, end_t, redraw=True):
        start_t = self._clamp_time_to_item(start_t)
        end_t = self._clamp_time_to_item(end_t)
        if start_t is None or end_t is None:
            self._clear_playback_selection(redraw=redraw)
            return

        s, e = sorted((start_t, end_t))
        self.playback_selection = (s, e) if e - s > 0.005 else None
        self._update_selection_graphics(redraw=redraw)
        self._update_playback_status()

    def _clear_playback_selection(self, redraw=True):
        self.playback_selection = None
        self.selection_anchor_x = None
        self.selection_drag_started = False
        if self.selection_patch:
            try:
                self.selection_patch.remove()
            except Exception:
                pass
            self.selection_patch = None
        if redraw and self.canvas:
            self.canvas.draw_idle()
        self._update_playback_status()

    def _update_selection_graphics(self, redraw=True):
        if self.ax and self._has_playback_selection():
            s, e = self.playback_selection
            if self.selection_patch:
                if isinstance(self.selection_patch, mpatches.Rectangle):
                    self.selection_patch.set_x(s)
                    self.selection_patch.set_width(e - s)
                else:
                    try:
                        xy = self.selection_patch.get_xy()
                        xy[0, 0] = s
                        xy[1, 0] = s
                        xy[2, 0] = e
                        xy[3, 0] = e
                        xy[4, 0] = s
                        self.selection_patch.set_xy(xy)
                    except Exception:
                        pass
            else:
                self.selection_patch = self.ax.axvspan(s, e, color="#FDE68A", alpha=0.38, zorder=4)
        else:
            if self.selection_patch:
                try:
                    self.selection_patch.remove()
                except Exception:
                    pass
                self.selection_patch = None
        if redraw and self.canvas:
            self.canvas.draw_idle()

    def _update_playback_status(self):
        if not self.playback_status_var:
            return
        item = self.current_item
        if not item:
            self.playback_status_var.set("0.000 / 0.000 s")
            return
        domain = self._get_playback_domain()
        if domain:
            start, end = domain
        else:
            start = item.get('start') or 0.0
            end = item.get('end') or start
        cursor = self.cursor_x if self.cursor_x is not None else start
        cursor_rel = max(0.0, min(end - start, cursor - start))
        if self._has_playback_selection():
            s, e = self.playback_selection
            self.playback_status_var.set(f"{cursor_rel:.3f} / {end - start:.3f} s  ({e - s:.3f}s)")
        else:
            self.playback_status_var.set(f"{cursor_rel:.3f} / {end - start:.3f} s")

    def on_playback_range_changed(self, value):
        self.playback_range_mode = value or "自动"
        if self.playback_range_var:
            self.playback_range_var.set(self.playback_range_mode)
        self._update_playback_mode_buttons()
        # 点击即播 (Click-to-Play)：切换模式时若当前有有效音频则立即触发对应范围播放
        if self.current_item and (self.current_item.get('snd') or self.current_item.get('path')):
            if self.is_playing:
                self._stop_playback(reset_cursor=False)
            play_range = self._get_playback_range(self.playback_range_mode)
            if play_range and play_range[1] > play_range[0] + 1e-4:
                self._start_playback_range(*play_range)

    def on_loop_toggled(self):
        if self.playback_loop_var:
            self.playback_loop_enabled = bool(self.playback_loop_var.get())

    def _update_playback_mode_buttons(self):
        for mode, btn in getattr(self, 'playback_mode_buttons', {}).items():
            if mode == self.playback_range_mode:
                btn.configure(fg_color="#2563EB", hover_color="#1D4ED8", text_color="#FFFFFF")
            else:
                btn.configure(fg_color="#E5E7EB", hover_color="#D1D5DB", text_color="#1F2937")

    def _update_play_button_state(self, playing=False):
        if hasattr(self, 'btn_play') and self.btn_play:
            if playing:
                self.btn_play.configure(text=" 停止", image=self.icons.get("pause"), width=68)
            else:
                self.btn_play.configure(text=" 播放", image=self.icons.get("play"), width=68)

    def stop_playback(self):
        self._stop_playback(reset_cursor=False)

    def _stop_playback(self, reset_cursor=False):
        if self._playback_job is not None and self.canvas:
            try:
                self.canvas.get_tk_widget().after_cancel(self._playback_job)
            except Exception:
                pass
            self._playback_job = None
        if self.is_playing:
            try:
                sd.stop()
            except Exception:
                pass
        self.is_playing = False
        if reset_cursor and self.current_item:
            self.cursor_x = self.current_item.get('start')
            self.cursor_char_index = 0 if self.current_item.get('chars_bounds') else None
            self.update_cursor_graphics()
        self._update_play_button_state(playing=False)
        self._update_playback_status()

    def _get_current_char_bounds(self):
        item = self.current_item
        if not item:
            return None
        chars_bounds = item.get('chars_bounds', [])
        if not chars_bounds:
            return None
        char_idx = self.cursor_char_index
        if char_idx is None or char_idx >= len(chars_bounds):
            char_idx = self._get_char_index_for_time(self.cursor_x)
        if char_idx is None or not (0 <= char_idx < len(chars_bounds)):
            return None
        return tuple(chars_bounds[char_idx])

    def _get_playback_range(self, mode=None):
        item = self.current_item
        if not item:
            return None
        mode = mode or self.playback_range_mode or "自动"
        domain = self._get_playback_domain()

        if mode == "选区":
            return self.playback_selection if self._has_playback_selection() else None
        if mode == "当前字":
            bounds = self._get_current_char_bounds()
            if not bounds:
                return None
            return (bounds[0], bounds[1])
        if mode == "整段":
            return domain if domain else (item['start'], item['end'])

        if self._has_playback_selection():
            return self.playback_selection

        t_s, t_e = item.get('start'), item.get('end')
        if t_s is None or t_e is None or t_e <= t_s:
            return domain

        # 当游标停在首字起点 item['start'] 或词尾附近时，默认返回有效全词 (item['start'], item['end'])
        if self.cursor_x is None or abs(self.cursor_x - t_s) < 1e-4 or self.cursor_x >= t_e - 0.01:
            return (t_s, t_e)

        bounds = self._get_current_char_bounds()
        if bounds:
            c_s, c_e = bounds
            # 若游标停在任何字右边界末尾附近，绝不返回零长度 (c_e, c_e)，而是返回有效全词
            if abs(self.cursor_x - c_e) < 0.005:
                return (t_s, t_e)
            if c_s <= self.cursor_x < c_e - 0.005:
                return (self.cursor_x, c_e)

        if t_s < self.cursor_x < t_e - 0.01:
            return (self.cursor_x, t_e)

        return (t_s, t_e)

    def play_current_item(self):
        self._play_range_mode("整段")

    def play_current_char(self):
        self._play_range_mode("当前字")

    def _play_range_mode(self, mode):
        if self.is_playing:
            self._stop_playback()
            return
        play_range = self._get_playback_range(mode)
        if not play_range:
            return
        self._start_playback_range(*play_range)

    def play_selected(self):
        item = self.current_item
        if not item:
            return

        # 如果当前正在播放，则作为“暂停”功能：停止播放并恢复按钮状态
        if self.is_playing:
            self._stop_playback()
            return

        play_range = self._get_playback_range()
        if not play_range:
            return
        self._start_playback_range(*play_range)

    @staticmethod
    def _wsola_stretch_2x(audio_1d, sr):
        x = np.asarray(audio_1d, dtype=np.float32).reshape(-1)
        n = len(x)
        win_len = max(64, int(round(sr * 0.025)))
        if win_len % 2 != 0:
            win_len += 1
        if n <= win_len:
            return np.repeat(x, 2)
        syn_hop = win_len // 2
        ana_hop = max(1, syn_hop // 2)
        search_tol = max(4, int(round(sr * 0.005)))
        win = np.hanning(win_len).astype(np.float32)
        num_frames = max(1, (n - win_len) // ana_hop + 1)
        out_len = (num_frames - 1) * syn_hop + win_len
        out = np.zeros(out_len, dtype=np.float32)
        norm = np.zeros(out_len, dtype=np.float32)
        ana_pos = 0
        for i in range(num_frames):
            s_idx = i * syn_hop
            expected_ana = i * ana_hop
            if i == 0:
                best_offset = 0
            else:
                natural_next = ana_pos + syn_hop
                if natural_next + syn_hop <= n:
                    ref_tail = x[natural_next:natural_next + syn_hop]
                    s0 = max(0, expected_ana - search_tol)
                    s1 = min(n - win_len, expected_ana + search_tol)
                    if s1 > s0 and np.max(np.abs(ref_tail)) > 1e-5:
                        best_corr = -1e18
                        best_p = expected_ana
                        for p in range(s0, s1 + 1, 2):
                            c = float(np.dot(ref_tail, x[p:p + syn_hop]))
                            if c > best_corr:
                                best_corr = c
                                best_p = p
                        best_offset = best_p - expected_ana
                    else:
                        best_offset = 0
                else:
                    best_offset = 0
            ana_pos = max(0, min(n - win_len, expected_ana + best_offset))
            out[s_idx:s_idx + win_len] += x[ana_pos:ana_pos + win_len] * win
            norm[s_idx:s_idx + win_len] += win * win
        mask = norm > 1e-6
        out[mask] /= norm[mask]
        return out

    def _start_playback_range(self, play_s, play_e):
        item = self.current_item
        if not item:
            return

        if not item.get('snd') and item.get('path'):
            try:
                from modules.audio_core import load_compatible_sound
                item['snd'] = load_compatible_sound(item['path'])
            except Exception:
                return
        if not item.get('snd'):
            return

        snd = item['snd']

        try:
            total_duration = snd.get_total_duration()
            play_s = max(0.0, min(total_duration, play_s))
            play_e = max(0.0, min(total_duration, play_e))

            if play_e <= play_s:
                return

            self.cursor_x = play_s
            self.cursor_char_index = self._get_char_index_for_time(play_s)
            self._update_char_capsules_style()
            self.update_cursor_graphics()

            part = snd.extract_part(from_time=play_s, to_time=play_e)
            sr = int(part.sampling_frequency)
            rate = float(getattr(self, 'play_rate', 1.0) or 1.0)
            if abs(rate - 0.5) < 1e-3:
                try:
                    lengthened = parselmouth.praat.call(part, "Lengthen (overlap-add)", 75.0, 600.0, 2.0)
                    audio_data = np.ascontiguousarray(lengthened.values.T, dtype=np.float32)
                    sr = int(lengthened.sampling_frequency)
                except Exception:
                    stretched = self._wsola_stretch_2x(part.values[0], sr)
                    audio_data = np.ascontiguousarray(stretched[:, None], dtype=np.float32)
            else:
                audio_data = np.ascontiguousarray(part.values.T, dtype=np.float32)

            sd.play(audio_data, samplerate=sr, blocking=False, latency='low')

            self.is_playing = True
            self.play_start_sys_time = time.time()
            self.play_start_audio_time = play_s
            self.play_end_audio_time = play_e
            self.play_selection_start = play_s

            self._update_play_button_state(playing=True)
            self._playback_update_loop()

        except Exception as e:
            self.is_playing = False
            self._update_play_button_state(playing=False)
            messagebox.showerror("错误", f"播放失败: {str(e)}")

    def _playback_update_loop(self):
        if not self.is_playing:
            self._playback_job = None
            self._update_play_button_state(playing=False)
            return
        rate = float(getattr(self, 'play_rate', 1.0) or 1.0)
        elapsed = time.time() - self.play_start_sys_time
        current_audio_time = self.play_start_audio_time + elapsed * rate

        if current_audio_time >= self.play_end_audio_time:
            if self.playback_loop_enabled:
                self._start_playback_range(self.play_start_audio_time, self.play_end_audio_time)
                return
            self.is_playing = False
            self.cursor_x = getattr(self, 'play_selection_start', self.current_item['start'])
            self._playback_job = None
            self.update_cursor_graphics(prefer_blit=True)
            self._update_play_button_state(playing=False)
            return

        self.cursor_x = current_audio_time
        self.update_cursor_graphics(prefer_blit=True)
        self._playback_job = self.canvas.get_tk_widget().after(10, self._playback_update_loop)

    def apply_auto_detect(self):
        if self.on_auto_detect_callback:
            self.on_auto_detect_callback()

