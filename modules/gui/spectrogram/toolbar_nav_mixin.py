"""
Toolbar controls, playback modes, capsule styling, manual boundary input, and undo/redo mixin.
"""
import copy
import time
import tkinter as tk
from tkinter import messagebox
import customtkinter as ctk
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from modules.ui_widgets import CTkReleaseButton, make_context_menu, post_context_menu
from modules.data_utils import split_into_syllables


class ToolbarNavMixin:

    def setup_ui(self):
        center_frame = ctk.CTkFrame(self.parent, fg_color="white", corner_radius=10)
        center_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, pady=10)

        top_bar = ctk.CTkFrame(center_frame, fg_color="transparent")
        top_bar.pack(side=tk.TOP, fill=tk.X, padx=10, pady=(10, 4))

        # --- 顶部第 1 排 (frame_tune) ---
        frame_tune = ctk.CTkFrame(top_bar, fg_color="#F8FAFB", corner_radius=8)
        frame_tune.pack(side=tk.TOP, fill=tk.X, pady=(0, 6))

        # 右侧按钮先 pack(side=tk.RIGHT)，确保小窗口下绝不被左侧挤出
        CTkReleaseButton(
            frame_tune,
            text="应用",
            image=self.icons.get("check"),
            compound="left",
            command=self.apply_manual_time,
            corner_radius=14,
            height=28,
            width=56,
            fg_color="#E5E7EB",
            text_color="#1F2937",
            hover_color="#D1D5DB",
        ).pack(side=tk.RIGHT, padx=(2, 6), pady=6)

        self.btn_undo = CTkReleaseButton(
            frame_tune,
            text="撤销",
            command=self.undo_last_action,
            corner_radius=14,
            height=28,
            width=52,
            fg_color="#E5E7EB",
            text_color="#1F2937",
            hover_color="#D1D5DB",
        )
        self.btn_undo.pack(side=tk.RIGHT, padx=2, pady=6)

        self.btn_slow_rate = CTkReleaseButton(
            frame_tune,
            text="1.0x",
            command=self.toggle_slow_rate,
            corner_radius=14,
            height=28,
            width=48,
            fg_color="#E5E7EB",
            text_color="#1F2937",
            hover_color="#D1D5DB",
        )
        self.btn_slow_rate.pack(side=tk.RIGHT, padx=2, pady=6)

        # 左侧区间起止输入框
        ctk.CTkLabel(
            frame_tune, text="区间(s):", font=self.font_title, text_color="#111827"
        ).pack(side=tk.LEFT, padx=(8, 4), pady=6)
        ctk.CTkLabel(frame_tune, text="起:").pack(side=tk.LEFT, padx=(2, 2))
        self.var_t_start = ctk.StringVar(value="0.000")
        self.entry_t_start = ctk.CTkEntry(
            frame_tune, textvariable=self.var_t_start, width=62, corner_radius=14, height=28
        )
        self.entry_t_start.pack(side=tk.LEFT, padx=(2, 6), pady=6)
        self.setup_entry_behavior(self.entry_t_start, 'start_manual')

        ctk.CTkLabel(frame_tune, text="止:").pack(side=tk.LEFT, padx=(2, 2))
        self.var_t_end = ctk.StringVar(value="0.000")
        self.entry_t_end = ctk.CTkEntry(
            frame_tune, textvariable=self.var_t_end, width=62, corner_radius=14, height=28
        )
        self.entry_t_end.pack(side=tk.LEFT, padx=(2, 6), pady=6)
        self.setup_entry_behavior(self.entry_t_end, 'end_manual')

        # --- 顶部第 2 排 (frame_actions) ---
        frame_actions = ctk.CTkFrame(top_bar, fg_color="transparent")
        frame_actions.pack(side=tk.TOP, fill=tk.X, pady=(2, 0))

        CTkReleaseButton(
            frame_actions,
            text="自动识别",
            image=self.icons.get("bulb"),
            compound="left",
            command=self.apply_auto_detect,
            corner_radius=16,
            height=32,
            width=88,
            fg_color=("#EEF2FF", "#312E81"),
            text_color=("#4338CA", "#A5B4FC"),
            hover_color=("#E0E7FF", "#3730A3"),
        ).pack(side=tk.LEFT, padx=(0, 6))

        self.btn_eraser = CTkReleaseButton(
            frame_actions,
            text="橡皮擦",
            image=self.icons.get("eraser"),
            compound="left",
            command=self.toggle_eraser_mode,
            corner_radius=16,
            height=32,
            width=82,
            fg_color="#E5E7EB",
            text_color="#1F2937",
            hover_color="#D1D5DB",
            font=ctk.CTkFont(family="Microsoft YaHei", size=12, weight="bold"),
        )
        self.btn_eraser.pack(side=tk.LEFT, padx=(0, 6))

        self.playback_status_var = ctk.StringVar(value="0.000 / 0.000 s")

        # 右侧导出按钮先于 status_frame 执行 pack(side=tk.RIGHT)
        CTkReleaseButton(
            frame_actions,
            text=" 导出",
            image=self.icons.get("save"),
            compound="left",
            command=self.on_export_callback,
            font=ctk.CTkFont(family="Microsoft YaHei", size=12, weight="bold"),
            corner_radius=16,
            height=32,
            width=68,
            fg_color="#10B981",
            text_color="#FFFFFF",
            hover_color="#059669",
        ).pack(side=tk.RIGHT, padx=(6, 0))

        status_frame = ctk.CTkFrame(
            frame_actions, fg_color=("#F1F5F9", "#1E293B"), corner_radius=8, height=30
        )
        status_frame.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=4, pady=2)
        ctk.CTkLabel(
            status_frame,
            textvariable=self.playback_status_var,
            font=ctk.CTkFont(family="Consolas", size=11),
            text_color=("#334155", "#94A3B8"),
            fg_color="transparent",
            corner_radius=0,
        ).pack(padx=6, pady=3)

        # --- 双层语谱图画布 (顶部全局波形导航条 ax_nav + 主语谱图 ax / ax2) ---
        self.fig = plt.Figure(figsize=(7, 5), facecolor='white')
        gs = self.fig.add_gridspec(
            2, 1, height_ratios=[1, 11], hspace=0.05, left=0.11, right=0.89, top=0.92, bottom=0.10
        )
        self.ax_nav = self.fig.add_subplot(gs[0])
        self.ax_nav.set_xticks([])
        self.ax_nav.set_yticks([])
        self.ax = self.fig.add_subplot(gs[1])
        self.ax2 = self.ax.twinx()
        self.ax2.yaxis.set_label_position("right")
        self.ax2.yaxis.tick_right()

        self.canvas = FigureCanvasTkAgg(self.fig, master=center_frame)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True, padx=8, pady=6)
        self.canvas.get_tk_widget().bind("<Button-2>", self._show_context_menu, add="+")
        self.canvas.get_tk_widget().bind("<Button-3>", self._show_context_menu, add="+")
        self.canvas.mpl_connect('button_press_event', self.on_press)
        self.canvas.mpl_connect('motion_notify_event', self.on_motion)
        self.canvas.mpl_connect('button_release_event', self.on_release)
        self.canvas.mpl_connect('scroll_event', self.on_scroll)
        self.canvas.mpl_connect('figure_leave_event', self.on_leave_fig)
        self.canvas.mpl_connect('draw_event', self.on_draw)

        try:
            top_win = self.parent.winfo_toplevel()
            top_win.bind("<Control-z>", lambda e: self.undo_last_action(), add="+")
            top_win.bind("<Control-Z>", lambda e: self.undo_last_action(), add="+")
            top_win.bind("<Control-y>", lambda e: self.redo_last_action(), add="+")
            top_win.bind("<Control-Y>", lambda e: self.redo_last_action(), add="+")
        except Exception:
            pass

        # --- 底部第 2 排 (bottom_bar: 工程管理 + 自动保存) ---
        bottom_bar = ctk.CTkFrame(center_frame, fg_color="#F9FAFB", corner_radius=10, height=36)
        bottom_bar.pack(side=tk.BOTTOM, fill=tk.X, padx=10, pady=(2, 6))

        self.switch_auto_save = ctk.CTkSwitch(
            bottom_bar,
            text="自动保存",
            width=82,
            switch_width=32,
            switch_height=18,
            font=ctk.CTkFont(family="Microsoft YaHei", size=12),
            progress_color="#2563EB",
            command=self.on_auto_save_toggled,
        )
        if self.app and getattr(self.app, 'project_manager', None) and self.app.project_manager.auto_save_enabled:
            self.switch_auto_save.select()
        else:
            self.switch_auto_save.deselect()
        self.switch_auto_save.pack(side=tk.RIGHT, padx=(4, 10), pady=4)

        CTkReleaseButton(
            bottom_bar,
            text="导入工程",
            image=self.icons.get("import"),
            compound="left",
            command=self.on_import_project_clicked,
            corner_radius=14,
            width=80,
            height=28,
            fg_color="#E5E7EB",
            text_color="#1F2937",
            hover_color="#D1D5DB",
        ).pack(side=tk.LEFT, padx=(8, 4), pady=4)

        CTkReleaseButton(
            bottom_bar,
            text="导出工程",
            image=self.icons.get("save_black"),
            compound="left",
            command=self.on_export_project_clicked,
            corner_radius=14,
            width=80,
            height=28,
            fg_color="#E5E7EB",
            text_color="#1F2937",
            hover_color="#D1D5DB",
        ).pack(side=tk.LEFT, padx=4, pady=4)

        # --- 底部第 1 排 (playback_bar: 播放 + 模式 + 循环) ---
        self.setup_playback_controls(center_frame)

    def setup_playback_controls(self, center_frame):
        playback_bar = ctk.CTkFrame(center_frame, fg_color="#F8FAFC", corner_radius=10, height=42)
        playback_bar.pack(side=tk.BOTTOM, fill=tk.X, padx=10, pady=(2, 4))
        playback_bar.pack_propagate(False)

        self.playback_range_var = ctk.StringVar(value=self.playback_range_mode)
        self.playback_loop_var = ctk.BooleanVar(value=False)
        self.playback_mode_buttons = {}

        self.btn_play = ctk.CTkButton(
            playback_bar,
            text=" 播放",
            image=self.icons.get("play"),
            compound="left",
            command=self.play_selected,
            font=ctk.CTkFont(family="Microsoft YaHei", size=12, weight="bold"),
            corner_radius=15,
            height=30,
            width=68,
            fg_color="#E5E7EB",
            text_color="#1F2937",
            hover_color="#D1D5DB",
        )
        self.btn_play.pack(side=tk.LEFT, padx=(8, 4), pady=6)

        # 右侧循环开关先于 mode_frame 执行 pack(side=tk.RIGHT)，确保绝不被挤压截断
        self.switch_loop = ctk.CTkSwitch(
            playback_bar,
            text="循环",
            width=62,
            switch_width=32,
            switch_height=18,
            variable=self.playback_loop_var,
            command=self.on_loop_toggled,
            progress_color="#2563EB",
            font=ctk.CTkFont(family="Microsoft YaHei", size=12),
        )
        self.switch_loop.pack(side=tk.RIGHT, padx=(4, 8), pady=6)

        mode_frame = ctk.CTkFrame(playback_bar, fg_color="transparent")
        mode_frame.pack(side=tk.LEFT, padx=2, pady=6)
        for mode in ["自动", "选区", "当前字", "整段"]:
            btn = ctk.CTkButton(
                mode_frame,
                text=mode,
                command=lambda value=mode: self.on_playback_range_changed(value),
                corner_radius=14,
                height=28,
                width=52,
                fg_color="#E5E7EB",
                text_color="#1F2937",
                hover_color="#D1D5DB",
                font=ctk.CTkFont(family="Microsoft YaHei", size=12, weight="bold"),
            )
            btn.pack(side=tk.LEFT, padx=2)
            self.playback_mode_buttons[mode] = btn
        self._update_playback_mode_buttons()

    def setup_entry_behavior(self, entry, param_key):
        entry._last_val = entry.get()
        entry._last_applied_val = entry.get()

        def on_enter(e):
            entry.configure(border_color="#3B82F6", border_width=2)

        def on_leave(e):
            try:
                focused = entry.winfo_toplevel().focus_get()
            except Exception:
                focused = None
            inner = getattr(entry, '_entry', None)
            if focused not in (entry, inner):
                entry.configure(border_color=["#979DA2", "#565B5E"], border_width=1)

        def on_focus_in(e):
            entry.configure(border_color="#2563EB", border_width=2)
            entry._last_val = entry.get()

        def on_focus_out(e):
            entry.configure(border_color=["#979DA2", "#565B5E"], border_width=1)
            current_val = entry.get()
            if current_val == getattr(entry, '_last_val', None):
                return
            if current_val == getattr(entry, '_last_applied_val', None):
                return
            entry._last_val = current_val
            entry._last_applied_val = current_val
            if param_key in ['start_manual', 'end_manual']:
                self.apply_manual_time()

        def on_return(e):
            current_val = entry.get()
            entry._last_val = current_val
            entry._last_applied_val = current_val
            if param_key in ['start_manual', 'end_manual']:
                self.apply_manual_time()
            try:
                entry.winfo_toplevel().focus_set()
            except Exception:
                pass

        def on_wheel(e):
            if not self.current_item:
                return
            direction = 0
            if getattr(e, 'num', None) == 4 or getattr(e, 'delta', 0) > 0:
                direction = 1
            elif getattr(e, 'num', None) == 5 or getattr(e, 'delta', 0) < 0:
                direction = -1
            if direction == 0:
                return
            try:
                cur = float(entry.get())
            except Exception:
                return
            new_val = max(0.0, cur + direction * 0.005)
            if param_key == 'start_manual':
                self.var_t_start.set(f"{new_val:.3f}")
            elif param_key == 'end_manual':
                self.var_t_end.set(f"{new_val:.3f}")
            entry._last_val = entry.get()
            entry._last_applied_val = entry.get()
            self.apply_manual_time()
            return "break"

        targets = [entry]
        inner_entry = getattr(entry, '_entry', None)
        if inner_entry is not None and inner_entry is not entry:
            targets.append(inner_entry)

        for target in targets:
            try:
                target.bind("<Enter>", on_enter, add="+")
                target.bind("<Leave>", on_leave, add="+")
                target.bind("<FocusIn>", on_focus_in, add="+")
                target.bind("<FocusOut>", on_focus_out, add="+")
                target.bind("<Return>", on_return, add="+")
                target.bind("<MouseWheel>", on_wheel, add="+")
                target.bind("<Button-4>", on_wheel, add="+")
                target.bind("<Button-5>", on_wheel, add="+")
            except Exception:
                pass

    def toggle_slow_rate(self):
        self.play_rate = 0.5 if getattr(self, 'play_rate', 1.0) == 1.0 else 1.0
        if hasattr(self, 'btn_slow_rate') and self.btn_slow_rate:
            if self.play_rate == 0.5:
                self.btn_slow_rate.configure(
                    text="0.5x",
                    fg_color="#DBEAFE",
                    text_color="#1D4ED8",
                    hover_color="#BFDBFE",
                )
            else:
                self.btn_slow_rate.configure(
                    text="1.0x",
                    fg_color="#E5E7EB",
                    text_color="#1F2937",
                    hover_color="#D1D5DB",
                )
        if self.is_playing:
            s = getattr(self, 'play_start_audio_time', None)
            e = getattr(self, 'play_end_audio_time', None)
            cur = self.cursor_x if self.cursor_x is not None else s
            self._stop_playback(reset_cursor=False)
            if cur is not None and e is not None and e > cur + 0.005:
                self._start_playback_range(cur, e)

    def _capture_item_snapshot(self, item=None):
        item = item or self.current_item
        if not item:
            return None
        snap = {
            'start': item.get('start'),
            'end': item.get('end'),
            'inner_splits': copy.deepcopy(item.get('inner_splits', [])),
            'chars_bounds': copy.deepcopy(item.get('chars_bounds', [])),
            'is_manual_edited': item.get('is_manual_edited', False),
            'session_erased_pitch_indices': set(getattr(self, 'session_erased_pitch_indices', set())),
            'session_erased_pitch_points': copy.deepcopy(getattr(self, 'session_erased_pitch_points', {})),
            'session_erased_formant_indices': copy.deepcopy(
                getattr(self, 'session_erased_formant_indices', {"f1": set(), "f2": set(), "f3": set()})
            ),
            'session_erased_formant_points': copy.deepcopy(
                getattr(self, 'session_erased_formant_points', {"f1": {}, "f2": {}, "f3": {}})
            ),
        }
        if item.get('pitch_data') and 'freqs' in item['pitch_data']:
            snap['pitch_freqs'] = np.array(item['pitch_data']['freqs'], copy=True)
        if item.get('formant_data'):
            snap['formant_data'] = {}
            for k in ('f1', 'f2', 'f3'):
                if item['formant_data'].get(k) is not None:
                    snap['formant_data'][k] = np.array(item['formant_data'][k], copy=True)
        return snap

    def _push_undo_state(self, snap=None):
        if not self.current_item:
            return
        snap = snap if snap is not None else self._capture_item_snapshot(self.current_item)
        if snap is None:
            return
        self.undo_stack.append(snap)
        if len(self.undo_stack) > 50:
            self.undo_stack.pop(0)
        self.redo_stack.clear()

    def _restore_item_snapshot(self, snap):
        item = self.current_item
        if not item or not snap:
            return
        item['start'] = snap['start']
        item['end'] = snap['end']
        item['inner_splits'] = copy.deepcopy(snap['inner_splits'])
        item['chars_bounds'] = copy.deepcopy(snap['chars_bounds'])
        item['is_manual_edited'] = snap.get('is_manual_edited', True)

        if 'pitch_freqs' in snap and item.get('pitch_data'):
            item['pitch_data']['freqs'] = np.array(snap['pitch_freqs'], copy=True)
            item.pop('preview_f0', None)
        if 'formant_data' in snap and item.get('formant_data'):
            for k, arr in snap['formant_data'].items():
                item['formant_data'][k] = np.array(arr, copy=True)
            item.pop('preview_formants', None)

        self.session_erased_pitch_indices = set(snap.get('session_erased_pitch_indices', set()))
        self.session_erased_pitch_points = copy.deepcopy(snap.get('session_erased_pitch_points', {}))
        self.session_erased_formant_indices = copy.deepcopy(
            snap.get('session_erased_formant_indices', {"f1": set(), "f2": set(), "f3": set()})
        )
        self.session_erased_formant_points = copy.deepcopy(
            snap.get('session_erased_formant_points', {"f1": {}, "f2": {}, "f3": {}})
        )

        self.plot_item_spectrogram()
        self.update_ui_times()
        if self.app:
            self.app.mark_modified()
            panel = getattr(self.app, 'tree_panel', None) or getattr(self.app, 'project_panel', None)
            if panel:
                panel.update_item_icon(self.current_item_iid)
                if hasattr(panel, 'update_preview'):
                    panel.update_preview()

    def undo_last_action(self):
        if not self.current_item or not self.undo_stack:
            return
        cur_snap = self._capture_item_snapshot(self.current_item)
        prev_snap = self.undo_stack.pop()
        if cur_snap is not None:
            self.redo_stack.append(cur_snap)
        self._restore_item_snapshot(prev_snap)

    def redo_last_action(self):
        if not self.current_item or not self.redo_stack:
            return
        cur_snap = self._capture_item_snapshot(self.current_item)
        next_snap = self.redo_stack.pop()
        if cur_snap is not None:
            self.undo_stack.append(cur_snap)
        self._restore_item_snapshot(next_snap)

    def on_import_project_clicked(self):
        if self.app and hasattr(self.app, 'on_import_project'):
            self.app.on_import_project()

    def on_export_project_clicked(self):
        if self.app and hasattr(self.app, 'on_export_project'):
            self.app.on_export_project()

    def on_auto_save_toggled(self):
        if self.app and hasattr(self.app, 'on_auto_save_toggled'):
            self.app.on_auto_save_toggled(self.switch_auto_save.get())

    def _format_capsule_label(self, syl, c_s, c_e):
        dur_ms = int(round(max(0.0, c_e - c_s) * 1000))
        return f"{syl} · {dur_ms}ms"

    def _update_char_capsules_style(self):
        if not self.current_item or not self.char_texts:
            return
        from modules.data_utils import split_into_syllables
        syls = split_into_syllables(self.current_item.get('label', ''))
        chars_bounds = self.current_item.get('chars_bounds', [])
        for i, txt in enumerate(self.char_texts):
            if i < len(chars_bounds) and i < len(syls):
                c_s, c_e = chars_bounds[i]
                txt.set_text(self._format_capsule_label(syls[i], c_s, c_e))
            is_active = (i == self.cursor_char_index)
            txt.set_color('#1D4ED8' if is_active else '#111827')
            bbox = txt.get_bbox_patch()
            if bbox is not None:
                bbox.set_facecolor('#EFF6FF' if is_active else '#FFFFFF')
                bbox.set_edgecolor('#2563EB' if is_active else '#CBD5E1')
                bbox.set_linewidth(1.4 if is_active else 0.9)

    def _update_nav_lens(self, xlim=None):
        if not self.ax_nav or not self.nav_lens_patch or not self.ax:
            return
        if xlim is None:
            xlim = self.ax.get_xlim()
        s, e = min(xlim), max(xlim)
        if isinstance(self.nav_lens_patch, mpatches.Rectangle):
            self.nav_lens_patch.set_x(s)
            self.nav_lens_patch.set_width(e - s)
        else:
            try:
                xy = self.nav_lens_patch.get_xy()
                xy[0, 0] = s
                xy[1, 0] = s
                xy[2, 0] = e
                xy[3, 0] = e
                xy[4, 0] = s
                self.nav_lens_patch.set_xy(xy)
            except Exception:
                pass

    def update_ui_times(self):
        item = self.current_item
        if not item:
            return
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
        if self.on_time_changed:
            self.on_time_changed(item)

    def apply_manual_time(self):
        if not self.current_item:
            return
        try:
            item = self.current_item
            t1, t2 = float(self.var_t_start.get()), float(self.var_t_end.get())
            if not (np.isfinite(t1) and np.isfinite(t2)):
                raise ValueError("非有限数值")
            old_s, old_e = item['start'], item['end']
            new_s, new_e = min(t1, t2), max(t1, t2)
            if new_e - new_s < 0.01:
                new_e = new_s + 0.01

            self._push_undo_state()
            chars_bounds = item.get('chars_bounds', [])
            if chars_bounds:
                # 单侧修改不按比例缩放内部其他音节边界
                only_start_changed = (
                    len(chars_bounds) > 1
                    and abs(new_e - old_e) < 1e-4
                    and abs(new_s - old_s) >= 1e-4
                    and new_s < chars_bounds[0][1] - 0.01
                )
                only_end_changed = (
                    len(chars_bounds) > 1
                    and abs(new_s - old_s) < 1e-4
                    and abs(new_e - old_e) >= 1e-4
                    and new_e > chars_bounds[-1][0] + 0.01
                )
                if only_start_changed:
                    chars_bounds[0][0] = new_s
                elif only_end_changed:
                    chars_bounds[-1][1] = new_e
                else:
                    ratio = (new_e - new_s) / (old_e - old_s) if old_e > old_s else 1.0
                    for i in range(len(chars_bounds)):
                        c_s, c_e = chars_bounds[i]
                        chars_bounds[i] = [
                            new_s + (c_s - old_s) * ratio,
                            new_s + (c_e - old_s) * ratio,
                        ]
                item['start'] = chars_bounds[0][0]
                item['end'] = chars_bounds[-1][1]
                self._sync_inner_splits_from_chars_bounds(item)
            else:
                item['start'] = new_s
                item['end'] = new_e
                item['inner_splits'] = []

            item['is_manual_edited'] = True
            panel = None
            if self.app:
                self.app.mark_modified()
                if hasattr(self.app, 'tree_panel') and self.app.tree_panel:
                    panel = self.app.tree_panel
                elif hasattr(self.app, 'project_panel') and self.app.project_panel:
                    panel = self.app.project_panel
            if panel:
                panel.update_item_icon(self.current_item_iid)

            self.plot_item_spectrogram()
            self.update_ui_times()
        except ValueError:
            messagebox.showerror("错误", "请输入有效的数字")

    def _show_context_menu(self, event):
        if getattr(self, '_suppress_next_context_menu', False):
            self._suppress_next_context_menu = False
            return
        if self.dragging is not None or getattr(self, 'erasing', False) or getattr(self, 'restoring_points', False):
            return

        from modules.ui_widgets import make_context_menu, post_context_menu

        menu = make_context_menu(self.canvas.get_tk_widget(), font_size=15)

        if not self.current_item:
            menu.add_command(label="暂无可操作条目", state="disabled")
        else:
            play_label = "停止播放" if self.is_playing else "播放 / 停止播放"
            menu.add_command(label=play_label, command=self.play_selected)
            menu.add_command(label="播放当前字", command=self.play_current_char)
            menu.add_command(label="播放整段", command=self.play_current_item)

            clear_state = "normal" if self.playback_selection is not None else "disabled"
            menu.add_command(label="清除播放选区", command=self._clear_playback_selection, state=clear_state)

            menu.add_separator()

            undo_state = "normal" if self.undo_stack else "disabled"
            menu.add_command(label="撤销上一步 (Ctrl+Z)", command=self.undo_last_action, state=undo_state)

            eraser_label = "退出橡皮擦并应用" if self.eraser_mode else "开启橡皮擦"
            menu.add_command(label=eraser_label, command=self.toggle_eraser_mode)
            menu.add_command(label="应用当前时间", command=self.apply_manual_time)
            menu.add_command(label="自动识别", command=self.apply_auto_detect)

            menu.add_separator()

            menu.add_command(label="导出当前数据...", command=self.on_export_callback)
            menu.add_command(label="导入工程...", command=self.on_import_project_clicked)
            menu.add_command(label="导出工程...", command=self.on_export_project_clicked)

        post_context_menu(menu, event)

