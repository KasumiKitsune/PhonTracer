import copy
import time
import tkinter as tk
from tkinter import messagebox
import customtkinter as ctk
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import numpy as np
import sounddevice as sd
try:
    import parselmouth
except ImportError:
    from modules import _parselmouth_fallback as parselmouth
    import sys as _sys
    _sys.modules.setdefault("parselmouth", parselmouth)
    _sys.modules.setdefault("parselmouth.praat", parselmouth.praat)
from .audio_core import SILENCE_AMPLITUDE_THRESHOLD
from .anomaly_detection import detect_pitch_anomaly_points
from .ui_widgets import CTkReleaseButton


class SpectrogramPanel:
    def __init__(self, parent, icons, on_time_changed_callback, on_auto_detect_callback, on_export_callback, app=None):
        self.parent = parent
        self.icons = icons
        self.on_time_changed = on_time_changed_callback
        self.on_auto_detect_callback = on_auto_detect_callback
        self.on_export_callback = on_export_callback
        self.app = app

        self.current_item = None
        self.current_item_iid = None
        self.dragging = None  # 取值: ('start', idx), ('end', idx), ('shared', idx), ('overlap_pending', idx, px), 'cursor', 'play_select', 'nav_pan'
        self.ax_nav = None
        self.ax = None
        self.ax2 = None
        self.nav_lens_patch = None
        self.nav_bound_lines = []
        self.nav_span_fills = []
        self.switch_trim_silence = None
        self.bound_lines = []
        self.span_fills = []
        self.char_texts = []
        self.pitch_main_line = None
        self.anomaly_line = None
        self._pitch_view_indices = None
        self._audition_flash_patches = []
        self._audition_flash_job = None

        try:
            self.font_title = ctk.CTkFont(family="Microsoft YaHei", size=13, weight="bold")
        except Exception:
            self.font_title = ("Microsoft YaHei", 13, "bold")

        # Cursor and playback state
        self.is_playing = False
        self.play_rate = 1.0
        self.play_start_sys_time = 0
        self.play_start_audio_time = 0
        self.play_end_audio_time = 0
        self.cursor_x = None
        self.cursor_char_index = None
        self.cursor_line = None
        self.cursor_text = None
        self._playback_job = None
        self.playback_selection = None
        self.selection_patch = None
        self.selection_anchor_x = None
        self.selection_drag_started = False
        self.playback_loop_enabled = False
        self.playback_range_mode = "自动"
        self.playback_status_var = None
        self.playback_range_var = None
        self.playback_loop_var = None
        self.playback_mode_buttons = {}
        self.playback_domain_start = None
        self.playback_domain_end = None

        # Undo / Redo state
        self.undo_stack = []
        self.redo_stack = []
        self._drag_pre_snapshot = None

        # Right-click pan & context menu disambiguation
        self._pan_start_x = None
        self._pan_start_xlim = None
        self._pan_moved = False
        self._suppress_next_context_menu = False

        # Eraser mode state
        self.eraser_mode = False
        self.erasing = False
        self.restoring_points = False
        self.erase_radius = 15.0  # Default pixel radius
        self.eraser_circle = None  # Matplotlib patch for displaying the eraser scope
        self.background = None
        self.session_erased_pitch_indices = set()
        self.session_erased_formant_indices = {"f1": set(), "f2": set(), "f3": set()}
        self.session_erased_pitch_points = {}
        self.session_erased_formant_points = {"f1": {}, "f2": {}, "f3": {}}
        self.erased_pitch_layer = None
        self.erased_f1_layer = None
        self.erased_f2_layer = None
        self.erased_f3_layer = None

        self.setup_ui()

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

    def clear_canvas(self):
        self.dragging = None
        self.erasing = False
        self.restoring_points = False
        if self.is_playing:
            self._stop_playback()
        if self.eraser_mode:
            self.apply_eraser_changes()
        self.current_item = None
        if self.ax_nav:
            self.ax_nav.clear()
            self.ax_nav.set_xticks([])
            self.ax_nav.set_yticks([])
        self.nav_lens_patch = None
        self.nav_bound_lines.clear()
        self.nav_span_fills.clear()
        self.ax.clear()
        self.ax2.clear()
        self.ax2.yaxis.set_label_position("right")
        self.ax2.yaxis.tick_right()
        self.background = None
        self.pitch_main_line = None
        self.anomaly_line = None
        self.playback_domain_start = None
        self.playback_domain_end = None
        self.bound_lines.clear()
        self.span_fills.clear()
        self.char_texts.clear()
        self.undo_stack.clear()
        self.redo_stack.clear()
        self._clear_playback_selection(redraw=False)
        self.canvas.draw()
        self.var_t_start.set("0.000")
        self.var_t_end.set("0.000")
        self._update_playback_status()

    def load_item(self, item):
        self.dragging = None
        self.erasing = False
        self.restoring_points = False
        if self.is_playing:
            self._stop_playback()
        if self.eraser_mode:
            self.apply_eraser_changes()
            self._reset_eraser_session()
        self.current_item = item
        self.current_item_iid = None
        self.undo_stack.clear()
        self.redo_stack.clear()
        if self.app and hasattr(self.app, 'items'):
            for k, v in self.app.items.items():
                if v is item:
                    self.current_item_iid = k
                    break
        t_start = item.get('start')
        t_end = item.get('end')

        # 备份原始 F0 / Formant 数据以支持 Shift/右键局部恢复画笔
        if item.get('pitch_data') and 'freqs' in item['pitch_data'] and '_orig_pitch_freqs' not in item:
            item['_orig_pitch_freqs'] = np.array(item['pitch_data']['freqs'], copy=True)
        elif item.get('pitch') and '_orig_pitch_freqs' not in item:
            try:
                item['_orig_pitch_freqs'] = np.array(item['pitch'].selected_array['frequency'], copy=True)
            except Exception:
                pass
        if item.get('formant_data') and '_orig_formant_data' not in item:
            item['_orig_formant_data'] = {
                k: np.array(v, copy=True)
                for k, v in item['formant_data'].items()
                if isinstance(v, np.ndarray)
            }

        # Init chars_bounds
        label = item.get('label', '')
        from .data_utils import split_into_syllables
        syls = split_into_syllables(label)
        if 'chars_bounds' not in item or not item.get('chars_bounds'):
            inner_splits = item.get('inner_splits', [])
            splits = [t_start] + [s for s in inner_splits if t_start < s < t_end] + [t_end]
            if len(syls) > 1 and len(splits) != len(syls) + 1:
                splits = np.linspace(t_start, t_end, len(syls) + 1).tolist()
            elif len(syls) <= 1:
                splits = [t_start, t_end]

            chars_bounds = []
            for i in range(len(splits) - 1):
                chars_bounds.append([splits[i], splits[i + 1]])
            item['chars_bounds'] = chars_bounds

        # Update overall bounds to reflect max/min of char bounds
        c_bounds = item['chars_bounds']
        if c_bounds:
            item['start'] = c_bounds[0][0]
            item['end'] = c_bounds[-1][1]
            t_start = item['start']
            t_end = item['end']

        s_str = f"{t_start:.3f}" if t_start is not None else "0.000"
        e_str = f"{t_end:.3f}" if t_end is not None else "0.000"
        self.var_t_start.set(s_str)
        self.var_t_end.set(e_str)
        if hasattr(self, 'entry_t_start'):
            self.entry_t_start._last_val = s_str
            self.entry_t_start._last_applied_val = s_str
        if hasattr(self, 'entry_t_end'):
            self.entry_t_end._last_val = e_str
            self.entry_t_end._last_applied_val = e_str

        self.cursor_x = t_start  # Reset cursor position when loading new item
        self.cursor_char_index = 0 if item.get('chars_bounds') else None
        self.playback_domain_start = None
        self.playback_domain_end = None
        self._clear_playback_selection(redraw=False)
        self.plot_item_spectrogram()

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

    def _format_capsule_label(self, syl, c_s, c_e):
        dur_ms = int(round(max(0.0, c_e - c_s) * 1000))
        return f"{syl} · {dur_ms}ms"

    def _update_char_capsules_style(self):
        if not self.current_item or not self.char_texts:
            return
        from .data_utils import split_into_syllables
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

    def plot_item_spectrogram(self):
        item = self.current_item
        if not item:
            return
        if not item.get('snd') or item.get('start') is None:
            return

        if self.ax_nav:
            self.ax_nav.clear()
        self.ax.clear()
        self.ax2.clear()
        # 显式将 ax2 的标签和刻度固定在右侧，防止 clear() 后回到左侧与 Frequency (Hz) 重叠
        self.ax2.yaxis.set_label_position("right")
        self.ax2.yaxis.tick_right()
        self.eraser_circle = None
        self.pitch_main_line = None
        self.anomaly_line = None
        self._pitch_view_indices = None

        self.bound_lines.clear()
        for fill in self.span_fills:
            try:
                fill.remove()
            except Exception:
                pass
        self.span_fills.clear()
        self.nav_bound_lines.clear()
        self.nav_span_fills.clear()
        self.nav_lens_patch = None

        self.char_texts.clear()
        self.cursor_line = None
        self.cursor_text = None
        self.selection_patch = None

        snd = item['snd']
        t_s, t_e = item['start'], item['end']
        chars_bounds = item.get('chars_bounds', [])
        label = item.get('label', '')
        total_dur = snd.get_total_duration()

        if self.switch_trim_silence and self.switch_trim_silence.get():
            mac_part = snd.extract_part(from_time=item['macro_start'], to_time=item['macro_end'])
            vals = mac_part.values[0]
            mac_xs = mac_part.xs()
            valid_idx = np.where(np.abs(vals) > SILENCE_AMPLITUDE_THRESHOLD)[0]
            if len(valid_idx) > 0:
                view_s = item['macro_start'] + mac_xs[valid_idx[0]]
                view_e = item['macro_start'] + mac_xs[valid_idx[-1]]
            else:
                view_s, view_e = item['macro_start'], item['macro_end']
            # 保留 ±0.08s 视野余量，防止首尾红线被 xlim 锁死无法向外微调
            view_s = max(0.0, min(view_s, t_s) - 0.08)
            view_e = min(total_dur, max(view_e, t_e) + 0.08)
        else:
            view_s = max(item['macro_start'] - 0.2, 0.0)
            view_e = min(item['macro_end'] + 0.2, total_dur)

        if view_e <= view_s:
            view_e = min(total_dur, view_s + 0.1)

        self.playback_domain_start = view_s
        self.playback_domain_end = view_e

        part = snd.extract_part(from_time=view_s, to_time=view_e)

        # --- 绘制顶部全局波形缩略导航条 (self.ax_nav) ---
        if self.ax_nav is not None:
            self.ax_nav.set_title(f"编辑区: {label}", pad=5, fontsize=11, fontweight='bold', color='#111827')
            self.ax_nav.set_xticks([])
            self.ax_nav.set_yticks([])
            self.ax_nav.set_xlim([view_s, view_e])
            try:
                w_xs = part.xs() + view_s
                w_ys = part.values[0]
                if len(w_xs) > 1200:
                    step = max(1, len(w_xs) // 1200)
                    w_xs = w_xs[::step]
                    w_ys = w_ys[::step]
                max_amp = float(np.max(np.abs(w_ys))) if len(w_ys) > 0 else 1.0
                if max_amp <= 0:
                    max_amp = 1.0
                self.ax_nav.plot(w_xs, w_ys, color='#64748B', linewidth=0.6, alpha=0.85)
                self.ax_nav.set_ylim([-max_amp * 1.15, max_amp * 1.15])
            except Exception:
                pass
            self.ax_nav.set_facecolor('#F8FAFC')
            for c_start, c_end in chars_bounds:
                n_span = self.ax_nav.axvspan(c_start, c_end, color='#93C5FD', alpha=0.35)
                n_ls = self.ax_nav.axvline(c_start, color='#EF4444', linewidth=1.0, alpha=0.8)
                n_le = self.ax_nav.axvline(c_end, color='#EF4444', linewidth=1.0, alpha=0.8)
                self.nav_span_fills.append(n_span)
                self.nav_bound_lines.append((n_ls, n_le))
            self.nav_lens_patch = self.ax_nav.axvspan(
                view_s, view_e, facecolor='#3B82F6', edgecolor='#1D4ED8', alpha=0.14, linewidth=1.0, zorder=6
            )

        app_params = getattr(self.app, 'last_params', {}) if self.app else {}
        analysis_mode = item.get('analysis_mode', app_params.get('analysis_mode', 'f0'))
        self.update_eraser_button_text()

        spec_max_freq = 5000.0
        if analysis_mode == 'formant':
            formant_max_hz = float(item.get('formant_max_hz', app_params.get('formant_max_hz', 5500.0)))
            spec_max_freq = max(5000.0, formant_max_hz)

        try:
            nyquist = part.sampling_frequency / 2.0
            spec_max_freq = min(spec_max_freq, nyquist)
        except Exception:
            pass

        # 轻量缓存 STFT 频谱矩阵，避免微调边界线或触发重绘时重复进行昂贵的科学计算
        cache_key = (id(snd), round(view_s, 6), round(view_e, 6), round(spec_max_freq, 2))
        cached_spec = getattr(self, '_spectrogram_cache', None)
        if cached_spec and cached_spec.get('snd') is snd and cached_spec.get('key') == cache_key:
            X = cached_spec['X']
            Y = cached_spec['Y']
            sg_db = cached_spec['sg_db']
        else:
            spectrogram = part.to_spectrogram(window_length=0.005, maximum_frequency=spec_max_freq)
            X = spectrogram.x_grid() + view_s
            Y = spectrogram.y_grid()
            vals = np.where(spectrogram.values > 0, spectrogram.values, 1e-10)
            sg_db = 10 * np.log10(vals)
            self._spectrogram_cache = {
                'snd': snd,
                'key': cache_key,
                'X': X,
                'Y': Y,
                'sg_db': sg_db,
            }

        sg_max = float(sg_db.max()) if sg_db.size > 0 else 0.0
        ext_x0 = float(X[0]) if len(X) > 0 else view_s
        ext_x1 = float(X[-1]) if len(X) > 0 else view_e
        ext_y0 = float(Y[0]) if len(Y) > 0 else 0.0
        ext_y1 = float(Y[-1]) if len(Y) > 0 else spec_max_freq
        self.ax.imshow(
            sg_db,
            extent=[ext_x0, ext_x1, ext_y0, ext_y1],
            origin='lower',
            aspect='auto',
            vmin=sg_max - 50.0,
            vmax=sg_max,
            cmap='Greys',
            interpolation='bilinear',
        )

        if analysis_mode == 'formant':
            self.ax2.set_visible(False)
            formant_max_hz = float(item.get('formant_max_hz', app_params.get('formant_max_hz', 5500.0)))
            try:
                nyquist = part.sampling_frequency / 2.0
                plot_max_hz = min(max(5000.0, formant_max_hz), nyquist)
            except Exception:
                plot_max_hz = max(5000.0, formant_max_hz)
            self.ax.set_ylim([0, plot_max_hz])
            self.ax.set_xlim([view_s, view_e])
            self.ax.set_ylabel("Frequency (Hz)")

            if item.get('formant_data'):
                f_xs = item['formant_data']['xs']
                f1_arr = item['formant_data']['f1']
                f2_arr = item['formant_data']['f2']
                mask = (f_xs >= view_s) & (f_xs <= view_e)
                f_xs_filtered = f_xs[mask]
                f1_plot = f1_arr[mask].copy()
                f2_plot = f2_arr[mask].copy()

                self.ax.scatter(
                    f_xs_filtered, f1_plot, s=18, color='#F97316', alpha=0.9, edgecolors='none', zorder=5, label="F1"
                )
                self.ax.scatter(
                    f_xs_filtered, f2_plot, s=18, color='#22C55E', alpha=0.9, edgecolors='none', zorder=5, label="F2"
                )

                show_f3 = bool(item.get("show_f3", app_params.get("show_f3", False)))
                f3_arr = item['formant_data'].get('f3') if show_f3 else None

                if f3_arr is not None:
                    f3_plot = f3_arr[mask].copy()
                    self.ax.scatter(
                        f_xs_filtered, f3_plot, s=18, color='#8B5CF6', alpha=0.9, edgecolors='none', zorder=5, label="F3"
                    )
                    self.erased_f3_layer = self.ax.scatter(
                        [], [], s=22, color='#DC2626', alpha=0.6, edgecolors='none', zorder=7, label="待剔除F3", animated=True
                    )
                else:
                    self.erased_f3_layer = None

                # 创建共振峰待剔除点图层 (animated=True 纳入 Blit 消除闪烁)
                self.erased_f1_layer = self.ax.scatter(
                    [], [], s=22, color='#DC2626', alpha=0.6, edgecolors='none', zorder=7, label="待剔除F1", animated=True
                )
                self.erased_f2_layer = self.ax.scatter(
                    [], [], s=22, color='#DC2626', alpha=0.6, edgecolors='none', zorder=7, label="待剔除F2", animated=True
                )

                if self.eraser_mode and hasattr(self, 'session_erased_formant_indices'):
                    self._remember_erased_formant_points(f_xs, f1_arr, f2_arr, f3_arr)
                    self._update_erased_formant_layers()
        else:
            self.ax2.set_visible(True)
            self.ax2.yaxis.set_label_position("right")
            self.ax2.yaxis.tick_right()
            self.ax.set_ylim([0, 5000])
            self.ax.set_xlim([view_s, view_e])
            self.ax.set_ylabel("Frequency (Hz)")

            if item.get('pitch_data'):
                p_xs_all = np.asarray(item['pitch_data']['xs'])
                p_freqs_all = np.asarray(item['pitch_data']['freqs'])
                mask = (p_xs_all >= view_s) & (p_xs_all <= view_e)
                self._pitch_view_indices = np.where(mask)[0]
                p_xs = p_xs_all[mask]
                p_vals = p_freqs_all[mask].astype(float, copy=True)
                p_vals[p_vals == 0] = np.nan
            elif item.get('pitch'):
                global_pitch = item['pitch']
                p_xs_all = np.asarray(global_pitch.xs())
                p_freqs_all = np.asarray(global_pitch.selected_array['frequency'])
                mask = (p_xs_all >= view_s) & (p_xs_all <= view_e)
                self._pitch_view_indices = np.where(mask)[0]
                p_xs = p_xs_all[mask]
                p_vals = p_freqs_all[mask].astype(float, copy=True)
                p_vals[p_vals == 0] = np.nan
            else:
                self._pitch_view_indices = np.array([], dtype=int)
                p_xs = np.array([])
                p_vals = np.array([])

            if self.eraser_mode and hasattr(self, 'session_erased_pitch_indices') and self.session_erased_pitch_indices:
                if self._pitch_view_indices is not None and len(self._pitch_view_indices) == len(p_vals):
                    for local_i, global_i in enumerate(self._pitch_view_indices):
                        if int(global_i) in self.session_erased_pitch_indices:
                            p_vals[local_i] = np.nan

            self.pitch_main_line = self.ax2.plot(
                p_xs, p_vals, '-o', markersize=4, linewidth=1.5, color='#3B82F6', zorder=5
            )[0]

            # 创建 F0 待擦除点图层 (animated=True 纳入 Blit 消除闪烁)
            self.erased_pitch_layer = self.ax2.plot(
                [], [], 'o', color='#DC2626', markersize=5, zorder=7, label="待擦除点", alpha=0.6, animated=True
            )[0]
            if (
                self.eraser_mode
                and item.get('pitch_data')
                and hasattr(self, 'session_erased_pitch_indices')
                and self.session_erased_pitch_indices
            ):
                xs = item['pitch_data']['xs']
                freqs = item['pitch_data']['freqs']
                self._remember_erased_pitch_points(xs, freqs)
                self._update_erased_pitch_layer()

            # Highlight anomalies (pitch jumps)
            p_xs_raw, p_freqs_raw = None, None
            if item.get('pitch_data'):
                p_xs_raw = np.asarray(item['pitch_data'].get('xs'))
                p_freqs_raw = np.asarray(item['pitch_data'].get('freqs'))
            elif item.get('pitch'):
                global_pitch = item['pitch']
                try:
                    p_xs_raw = np.asarray(global_pitch.xs())
                    p_freqs_raw = np.asarray(global_pitch.selected_array['frequency'])
                except Exception:
                    pass

            anomaly_points = []
            if p_xs_raw is not None and p_freqs_raw is not None and len(p_xs_raw) > 0:
                bounds = chars_bounds if chars_bounds else [[t_s, t_e]]
                anomaly_points = detect_pitch_anomaly_points(
                    p_xs_raw, p_freqs_raw, bounds=bounds, start=t_s, end=t_e
                )

                if anomaly_points and not self.eraser_mode:
                    jumps_x = [t for t, _ in anomaly_points]
                    jumps_y = [f for _, f in anomaly_points]
                    self.anomaly_line = self.ax2.plot(
                        jumps_x, jumps_y, 'o', color='#EF4444', markersize=6, zorder=6, label="异常点"
                    )[0]

            pitch_floor = float(item.get('pitch_floor', app_params.get('pitch_floor', 75.0)))
            pitch_ceiling = float(item.get('pitch_ceiling', app_params.get('pitch_ceiling', 600.0)))
            visible_vals = p_vals[np.isfinite(p_vals)] if len(p_vals) else np.array([])
            visible_high = float(np.max(visible_vals)) if len(visible_vals) else pitch_ceiling
            anomaly_high = max((f for _, f in anomaly_points), default=pitch_ceiling)
            y_min = max(0.0, min(50.0, pitch_floor - 25.0))
            y_max = max(500.0, pitch_ceiling, visible_high, anomaly_high) + 25.0
            self.ax2.set_ylim([y_min, y_max])
            self.ax2.yaxis.set_label_position("right")
            self.ax2.yaxis.tick_right()
            self.ax2.set_ylabel("F0 (Hz)", color='#3B82F6')
            self.ax2.tick_params(axis='y', labelcolor='#3B82F6', labelright=True, labelleft=False, right=True, left=False)

        from .data_utils import split_into_syllables
        syls = split_into_syllables(label)
        for i, (c_start, c_end) in enumerate(chars_bounds):
            line_s = self.ax.axvline(c_start, color='#EF4444', linestyle='-', linewidth=2)
            line_e = self.ax.axvline(c_end, color='#EF4444', linestyle='-', linewidth=2)
            self.bound_lines.append((line_s, line_e))
            span = self.ax.axvspan(c_start, c_end, color='#BFDBFE', alpha=0.35)
            self.span_fills.append(span)

            if i < len(syls):
                cx = (c_start + c_end) / 2
                is_active = (i == self.cursor_char_index)
                txt = self.ax.text(
                    cx,
                    0.90,
                    self._format_capsule_label(syls[i], c_start, c_end),
                    transform=self.ax.get_xaxis_transform(),
                    color='#1D4ED8' if is_active else '#111827',
                    fontsize=10.5,
                    ha='center',
                    va='top',
                    fontweight='bold',
                    bbox=dict(
                        boxstyle="round,pad=0.25",
                        fc="#EFF6FF" if is_active else "#FFFFFF",
                        ec="#2563EB" if is_active else "#CBD5E1",
                        lw=1.4 if is_active else 0.9,
                        alpha=0.92,
                    ),
                    zorder=8,
                )
                self.char_texts.append(txt)

        if self.cursor_x is None:
            self.cursor_x = t_s
        self.cursor_line = self.ax.axvline(
            self.cursor_x,
            color='#1B5E20',
            linestyle='--',
            linewidth=1.5,
            zorder=10,
            animated=True,
        )
        self.cursor_text = self.ax.text(
            self.cursor_x,
            0.98,
            self._format_cursor_label(),
            transform=self.ax.get_xaxis_transform(),
            color='#065F46',
            fontsize=10.5,
            ha='center',
            va='top',
            fontweight='bold',
            fontfamily="Microsoft YaHei",
            bbox=dict(boxstyle="round,pad=0.2", fc="#ECFDF5", ec="#10B981", lw=0.8, alpha=0.92),
            zorder=10,
            animated=True,
        )
        self._update_selection_graphics(redraw=False)
        self._update_playback_status()

        self.canvas.draw()
        self.update_cursor_graphics(prefer_blit=True)

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

    def _refresh_pitch_main_line_ydata(self):
        if not self.pitch_main_line or not self.current_item:
            return
        item = self.current_item
        if not item.get('pitch_data'):
            return
        freqs = np.asarray(item['pitch_data']['freqs'], dtype=float)
        if self._pitch_view_indices is not None and len(self._pitch_view_indices) > 0:
            p_vals = freqs[self._pitch_view_indices].copy()
        else:
            xs = np.asarray(item['pitch_data']['xs'])
            view_s = self.playback_domain_start if self.playback_domain_start is not None else 0.0
            view_e = self.playback_domain_end if self.playback_domain_end is not None else float('inf')
            mask = (xs >= view_s) & (xs <= view_e)
            self._pitch_view_indices = np.where(mask)[0]
            p_vals = freqs[mask].copy()
        p_vals[p_vals == 0] = np.nan
        if hasattr(self, 'session_erased_pitch_indices') and self.session_erased_pitch_indices:
            for local_i, global_i in enumerate(self._pitch_view_indices):
                if int(global_i) in self.session_erased_pitch_indices:
                    p_vals[local_i] = np.nan
        self.pitch_main_line.set_ydata(p_vals)

    def _refresh_anomaly_layer(self):
        item = self.current_item
        if not item or not self.ax2:
            return
        if self.anomaly_line is not None:
            try:
                self.anomaly_line.remove()
            except Exception:
                pass
            self.anomaly_line = None
        if self.eraser_mode:
            return
        app_params = getattr(self.app, 'last_params', {}) if self.app else {}
        analysis_mode = item.get('analysis_mode', app_params.get('analysis_mode', 'f0'))
        if analysis_mode == 'formant':
            return
        p_xs_raw, p_freqs_raw = None, None
        if item.get('pitch_data'):
            p_xs_raw = np.asarray(item['pitch_data'].get('xs'))
            p_freqs_raw = np.asarray(item['pitch_data'].get('freqs'))
        elif item.get('pitch'):
            try:
                p_xs_raw = np.asarray(item['pitch'].xs())
                p_freqs_raw = np.asarray(item['pitch'].selected_array['frequency'])
            except Exception:
                pass
        if p_xs_raw is not None and p_freqs_raw is not None and len(p_xs_raw) > 0:
            chars_bounds = item.get('chars_bounds', [])
            bounds = chars_bounds if chars_bounds else [[item['start'], item['end']]]
            anomaly_points = detect_pitch_anomaly_points(
                p_xs_raw, p_freqs_raw, bounds=bounds, start=item['start'], end=item['end']
            )
            if anomaly_points:
                jumps_x = [t for t, _ in anomaly_points]
                jumps_y = [f for _, f in anomaly_points]
                self.anomaly_line = self.ax2.plot(
                    jumps_x, jumps_y, 'o', color='#EF4444', markersize=6, zorder=6, label="异常点"
                )[0]

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

    def update_cursor_graphics(self, prefer_blit=False):
        if not self.cursor_line or not self.cursor_text:
            return
        self.cursor_line.set_xdata([self.cursor_x, self.cursor_x])
        self.cursor_text.set_position((self.cursor_x, 0.98))
        self.cursor_text.set_text(self._format_cursor_label())
        self._update_playback_status()
        if prefer_blit or self.is_playing:
            if self._blit_cursor():
                return
        self.canvas.draw_idle()

    def _draw_animated_eraser_layers(self):
        if self.ax2 and self.erased_pitch_layer is not None:
            try:
                self.ax2.draw_artist(self.erased_pitch_layer)
            except Exception:
                pass
        if self.ax:
            for layer_name in ("erased_f1_layer", "erased_f2_layer", "erased_f3_layer"):
                layer = getattr(self, layer_name, None)
                if layer is not None:
                    try:
                        self.ax.draw_artist(layer)
                    except Exception:
                        pass

    def _blit_cursor(self):
        if self.background is None or not self.canvas or not self.ax:
            return False
        try:
            self.canvas.restore_region(self.background)
            self._draw_animated_eraser_layers()
            self.ax.draw_artist(self.cursor_line)
            self.ax.draw_artist(self.cursor_text)
            if self.eraser_mode and self.eraser_circle is not None and self.eraser_circle.get_visible():
                app_params = getattr(self.app, 'last_params', {}) if self.app else {}
                mode = (
                    self.current_item.get('analysis_mode', app_params.get('analysis_mode', 'f0'))
                    if self.current_item
                    else app_params.get('analysis_mode', 'f0')
                )
                target_ax = self.ax if mode == 'formant' else self.ax2
                if target_ax:
                    target_ax.draw_artist(self.eraser_circle)
            self.canvas.blit(self.fig.bbox)
            self.canvas.flush_events()
            return True
        except Exception:
            return False

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

    def update_lines(self):
        if not self.current_item:
            return
        item = self.current_item
        chars_bounds = item.get('chars_bounds', [])
        if not chars_bounds or len(self.bound_lines) != len(chars_bounds):
            return

        from .data_utils import split_into_syllables
        syls = split_into_syllables(item.get('label', ''))

        for i, (c_s, c_e) in enumerate(chars_bounds):
            self.bound_lines[i][0].set_xdata([c_s, c_s])
            self.bound_lines[i][1].set_xdata([c_e, c_e])

            if i < len(self.span_fills):
                poly = self.span_fills[i]
                if isinstance(poly, mpatches.Rectangle):
                    poly.set_x(c_s)
                    poly.set_width(c_e - c_s)
                else:
                    try:
                        xy = poly.get_xy()
                        xy[0, 0] = c_s
                        xy[1, 0] = c_s
                        xy[2, 0] = c_e
                        xy[3, 0] = c_e
                        xy[4, 0] = c_s
                        poly.set_xy(xy)
                    except Exception:
                        pass

            if i < len(self.nav_bound_lines):
                self.nav_bound_lines[i][0].set_xdata([c_s, c_s])
                self.nav_bound_lines[i][1].set_xdata([c_e, c_e])
            if i < len(self.nav_span_fills):
                n_poly = self.nav_span_fills[i]
                if isinstance(n_poly, mpatches.Rectangle):
                    n_poly.set_x(c_s)
                    n_poly.set_width(c_e - c_s)
                else:
                    try:
                        xy = n_poly.get_xy()
                        xy[0, 0] = c_s
                        xy[1, 0] = c_s
                        xy[2, 0] = c_e
                        xy[3, 0] = c_e
                        xy[4, 0] = c_s
                        n_poly.set_xy(xy)
                    except Exception:
                        pass

            if i < len(self.char_texts):
                cx = (c_s + c_e) / 2
                self.char_texts[i].set_position((cx, 0.90))
                if i < len(syls):
                    self.char_texts[i].set_text(self._format_capsule_label(syls[i], c_s, c_e))

        self._update_char_capsules_style()
        self.canvas.draw_idle()

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
                from .audio_core import load_compatible_sound
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

    def _reset_eraser_session(self):
        self.session_erased_pitch_indices = set()
        self.session_erased_formant_indices = {"f1": set(), "f2": set(), "f3": set()}
        self.session_erased_pitch_points = {}
        self.session_erased_formant_points = {"f1": {}, "f2": {}, "f3": {}}

    def _remember_erased_pitch_points(self, xs, freqs):
        points = getattr(self, 'session_erased_pitch_points', {})
        orig_freqs = (
            self.current_item.get('_orig_pitch_freqs')
            if self.current_item and '_orig_pitch_freqs' in self.current_item
            else freqs
        )
        for idx in self.session_erased_pitch_indices:
            if idx not in points and 0 <= idx < len(xs):
                val = orig_freqs[idx] if (orig_freqs is not None and idx < len(orig_freqs) and orig_freqs[idx] > 0) else freqs[idx]
                if val > 0:
                    points[idx] = (xs[idx], val)
        self.session_erased_pitch_points = points

    def _remember_erased_formant_points(self, xs, f1, f2, f3=None):
        points = getattr(self, 'session_erased_formant_points', {"f1": {}, "f2": {}, "f3": {}})
        orig_fd = self.current_item.get('_orig_formant_data', {}) if self.current_item else {}
        curves = [("f1", f1), ("f2", f2)]
        if f3 is not None:
            curves.append(("f3", f3))
        for curve, values in curves:
            curve_points = points.setdefault(curve, {})
            orig_arr = orig_fd.get(curve, values)
            for idx in self.session_erased_formant_indices.get(curve, set()):
                if idx not in curve_points and 0 <= idx < len(xs) and 0 <= idx < len(values):
                    val = orig_arr[idx] if (orig_arr is not None and idx < len(orig_arr) and not np.isnan(orig_arr[idx])) else values[idx]
                    if not np.isnan(val):
                        curve_points[idx] = (xs[idx], val)
        self.session_erased_formant_points = points

    def _update_erased_pitch_layer(self):
        if not hasattr(self, 'erased_pitch_layer') or not self.erased_pitch_layer:
            return
        points = getattr(self, 'session_erased_pitch_points', {})
        ordered_points = [points[idx] for idx in sorted(points) if idx in self.session_erased_pitch_indices]
        self.erased_pitch_layer.set_data(
            [point[0] for point in ordered_points],
            [point[1] for point in ordered_points],
        )

    def _update_erased_formant_layers(self):
        points = getattr(self, 'session_erased_formant_points', {"f1": {}, "f2": {}, "f3": {}})
        for curve, layer_name in (("f1", "erased_f1_layer"), ("f2", "erased_f2_layer"), ("f3", "erased_f3_layer")):
            layer = getattr(self, layer_name, None)
            if not layer:
                continue
            active_set = self.session_erased_formant_indices.get(curve, set())
            ordered_points = [
                points.get(curve, {})[idx]
                for idx in sorted(points.get(curve, {}))
                if idx in active_set
            ]
            offsets = np.array(ordered_points) if ordered_points else np.empty((0, 2))
            layer.set_offsets(offsets)

    def light_apply_eraser_changes(self):
        if not self.current_item:
            return

        applied_pitch = False
        applied_formant = False
        item = self.current_item

        # 提交共振峰剔除，同时保留原始坐标供红色图层持续显示
        if hasattr(self, 'session_erased_formant_indices') and (
            self.session_erased_formant_indices.get("f1")
            or self.session_erased_formant_indices.get("f2")
            or self.session_erased_formant_indices.get("f3")
        ):
            if item.get('formant_data'):
                f1 = item['formant_data']['f1']
                f2 = item['formant_data']['f2']
                f3 = item['formant_data'].get('f3')
                self._remember_erased_formant_points(item['formant_data']['xs'], f1, f2, f3)

                for curve in ("f1", "f2", "f3"):
                    arr = item["formant_data"].get(curve)
                    if arr is None:
                        continue
                    for idx in self.session_erased_formant_indices.get(curve, set()):
                        if 0 <= idx < len(arr) and not np.isnan(arr[idx]):
                            arr[idx] = np.nan
                            applied_formant = True

        # 提交 F0 擦除，同时保留原始坐标供红色图层持续显示，并实时断开蓝色 F0 主线
        if hasattr(self, 'session_erased_pitch_indices') and self.session_erased_pitch_indices:
            if not item.get('pitch_data') and item.get('pitch'):
                pitch = item['pitch']
                item['pitch_data'] = {
                    'xs': pitch.xs(),
                    'freqs': pitch.selected_array['frequency'].copy(),
                }

            if item.get('pitch_data'):
                self._remember_erased_pitch_points(item['pitch_data']['xs'], item['pitch_data']['freqs'])
                freqs = item['pitch_data']['freqs']
                for idx in self.session_erased_pitch_indices:
                    if 0 <= idx < len(freqs) and freqs[idx] != 0.0:
                        freqs[idx] = 0.0
                        applied_pitch = True
                self._refresh_pitch_main_line_ydata()

        if applied_pitch or applied_formant:
            item['is_manual_edited'] = True
            if applied_pitch:
                item['_dirty_pitch'] = True
            if applied_formant:
                item['_dirty_formant'] = True
            if 'preview_f0' in item:
                item.pop('preview_f0')
            if 'preview_formants' in item:
                item.pop('preview_formants')
            if 'has_empty_data' in item:
                item.pop('has_empty_data')

            if self.app:
                self.app.mark_modified()

                app_params = getattr(self.app, 'last_params', {}) if self.app else {}
                if applied_formant:
                    pts = int(app_params.get('pts', 11))
                    strategy = app_params.get('formant_sample_strategy', '整段11点')
                    show_f3 = bool(item.get("show_f3", app_params.get("show_f3", False)))
                    try:
                        if show_f3:
                            res = self.app.sample_formant_points_with_f3(item, pts, strategy)
                            if isinstance(res, (tuple, list)) and len(res) == 4:
                                _, preview_f1, preview_f2, preview_f3 = res
                                item['preview_formants'] = {"f1": preview_f1, "f2": preview_f2, "f3": preview_f3}
                        else:
                            res = self.app.sample_formant_points(item, pts, strategy)
                            if isinstance(res, (tuple, list)) and len(res) == 3:
                                _, preview_f1, preview_f2 = res
                                item['preview_formants'] = {"f1": preview_f1, "f2": preview_f2}
                    except Exception:
                        pass

                panel = None
                if hasattr(self.app, 'tree_panel') and self.app.tree_panel:
                    panel = self.app.tree_panel
                elif hasattr(self.app, 'project_panel') and self.app.project_panel:
                    panel = self.app.project_panel
                if panel:
                    panel.update_item_icon(self.current_item_iid)
                    if hasattr(panel, 'update_preview'):
                        panel.update_preview()

    def apply_eraser_changes(self):
        self.light_apply_eraser_changes()
        self._reset_eraser_session()
        self.plot_item_spectrogram()

    def discard_eraser_changes(self):
        item = self.current_item
        restored_pitch = False
        restored_formant = False
        if item:
            pitch_points = getattr(self, 'session_erased_pitch_points', {}) or {}
            if pitch_points and item.get('pitch_data') and 'freqs' in item['pitch_data']:
                freqs = item['pitch_data']['freqs']
                for idx, (_t, orig_freq) in pitch_points.items():
                    if 0 <= idx < len(freqs):
                        freqs[idx] = orig_freq
                        restored_pitch = True

            formant_points = getattr(self, 'session_erased_formant_points', {}) or {}
            if formant_points and item.get('formant_data'):
                for curve, curve_points in formant_points.items():
                    arr = item['formant_data'].get(curve)
                    if arr is None or not curve_points:
                        continue
                    for idx, (_t, orig_val) in curve_points.items():
                        if 0 <= idx < len(arr):
                            arr[idx] = orig_val
                            restored_formant = True

            if restored_pitch or restored_formant:
                item.pop('preview_f0', None)
                item.pop('preview_formants', None)
                item.pop('has_empty_data', None)
                if self.app:
                    app_params = getattr(self.app, 'last_params', {}) if self.app else {}
                    if restored_formant:
                        pts = int(app_params.get('pts', 11))
                        strategy = app_params.get('formant_sample_strategy', '整段11点')
                        show_f3 = bool(item.get("show_f3", app_params.get("show_f3", False)))
                        try:
                            if show_f3:
                                res = self.app.sample_formant_points_with_f3(item, pts, strategy)
                                if isinstance(res, (tuple, list)) and len(res) == 4:
                                    _, preview_f1, preview_f2, preview_f3 = res
                                    item['preview_formants'] = {"f1": preview_f1, "f2": preview_f2, "f3": preview_f3}
                            else:
                                res = self.app.sample_formant_points(item, pts, strategy)
                                if isinstance(res, (tuple, list)) and len(res) == 3:
                                    _, preview_f1, preview_f2 = res
                                    item['preview_formants'] = {"f1": preview_f1, "f2": preview_f2}
                        except Exception:
                            pass
                    panel = getattr(self.app, 'tree_panel', None) or getattr(self.app, 'project_panel', None)
                    if panel:
                        panel.update_item_icon(self.current_item_iid)
                        if hasattr(panel, 'update_preview'):
                            panel.update_preview()

        self._reset_eraser_session()

        # 清空待擦除点图层
        if hasattr(self, 'erased_pitch_layer') and self.erased_pitch_layer:
            self.erased_pitch_layer.set_data([], [])
        if hasattr(self, 'erased_f1_layer') and self.erased_f1_layer:
            self.erased_f1_layer.set_offsets(np.empty((0, 2)))
        if hasattr(self, 'erased_f2_layer') and self.erased_f2_layer:
            self.erased_f2_layer.set_offsets(np.empty((0, 2)))
        if hasattr(self, 'erased_f3_layer') and self.erased_f3_layer:
            self.erased_f3_layer.set_offsets(np.empty((0, 2)))

        if restored_pitch or restored_formant:
            self.plot_item_spectrogram()
        else:
            self.canvas.draw_idle()

    def toggle_eraser_mode(self):
        self.eraser_mode = not self.eraser_mode
        if self.eraser_mode:
            self._reset_eraser_session()
            self.btn_eraser.configure(
                fg_color="#FEE2E2",
                text_color="#DC2626",
                hover_color="#FCA5A5",
            )
            self.canvas.get_tk_widget().config(cursor="crosshair")
            self.plot_item_spectrogram()
        else:
            self.apply_eraser_changes()
            self.btn_eraser.configure(
                fg_color="#E5E7EB",
                text_color="#1F2937",
                hover_color="#D1D5DB",
            )
            self.canvas.get_tk_widget().config(cursor="arrow")
            self.update_eraser_circle(None)

    def erase_points_near(self, event):
        item = self.current_item
        if not item:
            return

        app_params = getattr(self.app, 'last_params', {}) if self.app else {}
        mode = item.get('analysis_mode', app_params.get('analysis_mode', 'f0'))

        if mode == 'formant':
            if event.inaxes != self.ax:
                return
            if not item.get('formant_data'):
                return

            xs = item['formant_data']['xs']
            f1 = item['formant_data']['f1']
            f2 = item['formant_data']['f2']
            if len(xs) == 0:
                return

            show_f3 = bool(item.get("show_f3", app_params.get("show_f3", False)))
            f3 = item['formant_data'].get('f3')

            candidate_curves = [("f1", f1), ("f2", f2)]
            if show_f3 and f3 is not None:
                candidate_curves.append(("f3", f3))

            best_curve = None
            best_idx = -1
            best_dist = np.inf

            for curve, values in candidate_curves:
                temp = values.copy()
                for idx in self.session_erased_formant_indices.get(curve, set()):
                    if 0 <= idx < len(temp):
                        temp[idx] = np.nan

                clean = temp.copy()
                clean[np.isnan(clean)] = 0.0
                pixels = self.ax.transData.transform(np.column_stack((xs, clean)))
                dists = np.hypot(pixels[:, 0] - event.x, pixels[:, 1] - event.y)
                dists[np.isnan(temp)] = np.inf

                idx = int(np.argmin(dists)) if len(dists) > 0 else -1
                if idx >= 0 and dists[idx] < best_dist:
                    best_curve = curve
                    best_idx = idx
                    best_dist = dists[idx]

            if best_idx >= 0 and best_dist <= self.erase_radius:
                self.session_erased_formant_indices[best_curve].add(best_idx)
                self._remember_erased_formant_points(xs, f1, f2, f3 if show_f3 else None)
                self._update_erased_formant_layers()
                if not self._blit_cursor():
                    self.canvas.draw_idle()
        else:
            if event.inaxes != self.ax2:
                return

            if not item.get('pitch_data') and item.get('pitch'):
                pitch = item['pitch']
                item['pitch_data'] = {
                    'xs': pitch.xs(),
                    'freqs': pitch.selected_array['frequency'].copy(),
                }

            if not item.get('pitch_data'):
                return

            xs = item['pitch_data']['xs']
            freqs = item['pitch_data']['freqs']
            if len(xs) == 0:
                return

            pts_data = np.column_stack((xs, freqs))
            pts_pixels = self.ax2.transData.transform(pts_data)

            dists = np.hypot(pts_pixels[:, 0] - event.x, pts_pixels[:, 1] - event.y)

            erase_radius = self.erase_radius
            mask_to_erase = (dists <= erase_radius) & (freqs > 0)

            if np.any(mask_to_erase):
                erased_indices = np.where(mask_to_erase)[0]
                self.session_erased_pitch_indices.update(erased_indices)

                # 先保存原始点坐标，再实时断开蓝色 F0 主线并更新红色待擦除图层
                self._remember_erased_pitch_points(xs, freqs)
                self._refresh_pitch_main_line_ydata()
                if hasattr(self, 'erased_pitch_layer') and self.erased_pitch_layer:
                    self._update_erased_pitch_layer()
                if not self._blit_cursor():
                    self.canvas.draw_idle()

    def restore_points_near(self, event):
        item = self.current_item
        if not item or event.x is None or event.y is None:
            return

        app_params = getattr(self.app, 'last_params', {}) if self.app else {}
        mode = item.get('analysis_mode', app_params.get('analysis_mode', 'f0'))

        if mode == 'formant':
            if event.inaxes != self.ax or not item.get('formant_data'):
                return
            xs = item['formant_data']['xs']
            orig_fd = item.get('_orig_formant_data', {})
            restored_any = False
            for curve in ("f1", "f2", "f3"):
                arr = item['formant_data'].get(curve)
                if arr is None:
                    continue
                orig_arr = orig_fd.get(curve)
                remembered = self.session_erased_formant_points.get(curve, {})
                cand_indices = set(self.session_erased_formant_indices.get(curve, set())) | set(remembered.keys())
                if orig_arr is not None:
                    cand_indices |= set(np.where(np.isnan(arr) & ~np.isnan(orig_arr))[0].tolist())
                for idx in list(cand_indices):
                    if not (0 <= idx < len(xs)):
                        continue
                    orig_val = remembered.get(idx, (None, None))[1]
                    if orig_val is None and orig_arr is not None and idx < len(orig_arr):
                        orig_val = orig_arr[idx]
                    if orig_val is None or np.isnan(orig_val):
                        continue
                    px, py = self.ax.transData.transform((xs[idx], orig_val))
                    if np.hypot(px - event.x, py - event.y) <= self.erase_radius:
                        arr[idx] = orig_val
                        self.session_erased_formant_indices.get(curve, set()).discard(idx)
                        remembered.pop(idx, None)
                        restored_any = True
            if restored_any:
                self._update_erased_formant_layers()
                self.canvas.draw_idle()
        else:
            if event.inaxes != self.ax2 or not item.get('pitch_data'):
                return
            xs = item['pitch_data']['xs']
            freqs = item['pitch_data']['freqs']
            orig_freqs = item.get('_orig_pitch_freqs')
            remembered = getattr(self, 'session_erased_pitch_points', {})
            cand_indices = set(self.session_erased_pitch_indices) | set(remembered.keys())
            if orig_freqs is not None and len(orig_freqs) == len(freqs):
                cand_indices |= set(np.where((freqs == 0) & (orig_freqs > 0))[0].tolist())
            restored_any = False
            for idx in list(cand_indices):
                if not (0 <= idx < len(xs)):
                    continue
                orig_val = remembered.get(idx, (None, None))[1]
                if orig_val is None and orig_freqs is not None and idx < len(orig_freqs):
                    orig_val = orig_freqs[idx]
                if orig_val is None or orig_val <= 0:
                    continue
                px, py = self.ax2.transData.transform((xs[idx], orig_val))
                if np.hypot(px - event.x, py - event.y) <= self.erase_radius:
                    freqs[idx] = orig_val
                    self.session_erased_pitch_indices.discard(idx)
                    remembered.pop(idx, None)
                    restored_any = True
            if restored_any:
                self._refresh_pitch_main_line_ydata()
                self._update_erased_pitch_layer()
                if not self._blit_cursor():
                    self.canvas.draw_idle()

    def on_draw(self, event):
        self.background = self.canvas.copy_from_bbox(self.fig.bbox)
        if self.cursor_line and self.cursor_text:
            self._blit_cursor()

    def update_eraser_circle(self, event=None):
        app_params = getattr(self.app, 'last_params', {}) if self.app else {}
        mode = 'f0'
        if self.current_item is not None:
            mode = self.current_item.get('analysis_mode', app_params.get('analysis_mode', 'f0'))
        else:
            mode = app_params.get('analysis_mode', 'f0')
        target_ax = self.ax if mode == 'formant' else self.ax2

        if not self.eraser_mode or not target_ax:
            if self.eraser_circle:
                try:
                    self.eraser_circle.remove()
                except Exception:
                    pass
                self.eraser_circle = None
                if self.background is not None:
                    try:
                        self.canvas.restore_region(self.background)
                        self._draw_animated_eraser_layers()
                        if self.ax and self.cursor_line is not None and self.cursor_line.get_visible():
                            self.ax.draw_artist(self.cursor_line)
                        if self.ax and self.cursor_text is not None and self.cursor_text.get_visible():
                            self.ax.draw_artist(self.cursor_text)
                        self.canvas.blit(self.fig.bbox)
                    except Exception:
                        self.canvas.draw_idle()
            return

        if event is None or event.x is None or event.y is None or event.inaxes != target_ax:
            if self.eraser_circle:
                self.eraser_circle.set_visible(False)
                if self.background is not None:
                    try:
                        self.canvas.restore_region(self.background)
                        self._draw_animated_eraser_layers()
                        if self.ax and self.cursor_line is not None and self.cursor_line.get_visible():
                            self.ax.draw_artist(self.cursor_line)
                        if self.ax and self.cursor_text is not None and self.cursor_text.get_visible():
                            self.ax.draw_artist(self.cursor_text)
                        self.canvas.blit(self.fig.bbox)
                    except Exception:
                        self.canvas.draw_idle()
            return

        if self.eraser_circle is None or self.eraser_circle not in target_ax.patches:
            from matplotlib.patches import Circle
            self.eraser_circle = Circle(
                (event.x, event.y),
                radius=self.erase_radius,
                fill=True,
                facecolor='#FEE2E2',
                edgecolor='#EF4444',
                alpha=0.4,
                linewidth=1.5,
                transform=None,
                zorder=100,
                animated=True,
            )
            target_ax.add_patch(self.eraser_circle)
        else:
            self.eraser_circle.set_center((event.x, event.y))
            self.eraser_circle.set_radius(self.erase_radius)
            self.eraser_circle.set_visible(True)

        if self.background is not None:
            try:
                self.canvas.restore_region(self.background)
                self._draw_animated_eraser_layers()
                if self.ax and self.cursor_line is not None and self.cursor_line.get_visible():
                    self.ax.draw_artist(self.cursor_line)
                if self.ax and self.cursor_text is not None and self.cursor_text.get_visible():
                    self.ax.draw_artist(self.cursor_text)
                target_ax.draw_artist(self.eraser_circle)
                self.canvas.blit(self.fig.bbox)
            except Exception:
                self.canvas.draw_idle()
        else:
            self.canvas.draw_idle()

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

    def update_eraser_button_text(self):
        if not hasattr(self, 'btn_eraser') or not self.btn_eraser:
            return
        app_params = getattr(self.app, 'last_params', {}) if self.app else {}
        if self.current_item is not None:
            mode = self.current_item.get('analysis_mode', app_params.get('analysis_mode', 'f0'))
        else:
            mode = app_params.get('analysis_mode', 'f0')
        if mode == 'formant':
            self.btn_eraser.configure(text="剔除点")
        else:
            self.btn_eraser.configure(text="橡皮擦")

    def _show_context_menu(self, event):
        if getattr(self, '_suppress_next_context_menu', False):
            self._suppress_next_context_menu = False
            return
        if self.dragging is not None or getattr(self, 'erasing', False) or getattr(self, 'restoring_points', False):
            return

        from .ui_widgets import make_context_menu, post_context_menu

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
