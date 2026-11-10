"""
Acoustic chart export dialog window.
Provides real-time preview, publication preset switching, multi-format export,
and interactive acoustic chart configuration.
"""
from __future__ import annotations

import csv
import ctypes
import io
import logging
import os
import queue
import threading

import numpy as np
import matplotlib.pyplot as plt

_GUI_IMPORT_ERROR = None

try:
    import tkinter as tk
    import customtkinter as ctk
    from tkinter import messagebox, filedialog
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
except ImportError as exc:
    _GUI_IMPORT_ERROR = exc

    import matplotlib
    matplotlib.use("Agg", force=True)

    class _MissingTk:
        LEFT = "left"
        RIGHT = "right"
        X = "x"
        BOTH = "both"

    class _MissingCtkToplevel:
        pass

    class _MissingCtk:
        CTkToplevel = _MissingCtkToplevel

    tk = _MissingTk()
    ctk = _MissingCtk()
    messagebox = None
    filedialog = None
    FigureCanvasTkAgg = None

try:
    from modules.chart_engine.presets import PUBLICATION_PRESETS, ExportCancelled
    from modules.chart_engine.exporter_core import AcousticChartExporter, _MATPLOTLIB_LOCK
except ImportError:
    from modules.acoustic_exporter import (
        PUBLICATION_PRESETS,
        ExportCancelled,
        AcousticChartExporter,
        _MATPLOTLIB_LOCK,
    )

from modules.gui.chart_dialogs.settings_builders import ChartFormantSettingsBuilderMixin
from modules.gui.chart_dialogs.group_rule_dialog import ChartGroupRuleDialogMixin


class AcousticChartExportDialog(
    ctk.CTkToplevel,
    ChartFormantSettingsBuilderMixin,
    ChartGroupRuleDialogMixin,
    AcousticChartExporter,
):
    """
    GUI dialog for configuring and exporting acoustic charts.
    Inherits from CTkToplevel, mixins for formant settings and group rules, and AcousticChartExporter.
    """

    def __init__(self, parent, app=None, project_tree=None, mode='single', all_speakers=None):
        if _GUI_IMPORT_ERROR is not None:
            raise RuntimeError("Acoustic chart dialog requires Tkinter/CustomTkinter GUI support.") from _GUI_IMPORT_ERROR

        ctk.CTkToplevel.__init__(self, parent)
        AcousticChartExporter.__init__(self, project_tree, app, all_speakers)

        self.parent = parent
        self.app = app
        self.project_tree = project_tree
        self.initial_mode = mode
        self.all_speakers = all_speakers or []

        self.title("PhonTracer语音学标注与声学分析系统 V1.0 - 声学可视化与图表导出器")
        self.geometry("1040x680")
        self.resizable(True, True)
        if self.app:
            self.app.active_chart_dialog = self
        self.protocol("WM_DELETE_WINDOW", self._on_close_request)
        self._export_worker = None
        self._export_cancel_event = None
        self._export_progress_queue = None
        self._export_poll_job = None
        self._export_progress_window = None
        self._preview_worker = None
        self._preview_cancel_event = None
        self._preview_queue = None
        self._preview_poll_job = None
        self._preview_generation = 0

        # Color Palette
        self.colors = list(PUBLICATION_PRESETS["chinese_journal"]["colors"])
        self.ui_primary = "#2563EB"
        self.ui_primary_hover = "#1D4ED8"
        self.ui_primary_soft = "#DBEAFE"
        self.ui_row_even = ("#FFFFFF", "#1E293B")
        self.ui_row_odd = ("#F8FAFC", "#263244")

        self.sm = getattr(self.app, 'speaker_manager', None)
        self.active_speaker = self.sm.get_active_speaker() if self.sm else None

        # Fonts
        self.font_title = ctk.CTkFont(family="Microsoft YaHei", size=14, weight="bold")
        self.font_main = ctk.CTkFont(family="Microsoft YaHei", size=12)
        self.font_small = ctk.CTkFont(family="Microsoft YaHei", size=11)

        self.dropdown_kwargs = {
            "font": self.font_main,
            "fg_color": ("#EEF2F6", "#334155"),
            "text_color": ("#0F172A", "#F8FAFC"),
            "button_color": ("#EEF2F6", "#334155"),
            "button_hover_color": ("#E2E8F0", "#475569"),
            "dropdown_fg_color": ("#FFFFFF", "#1E293B"),
            "dropdown_hover_color": ("#EFF6FF", "#334155"),
            "dropdown_text_color": ("#0F172A", "#F8FAFC"),
            "height": 30,
            "corner_radius": 8,
        }

        self._init_variables()
        self._init_group_filters()

        self._build_gui()
        self._populate_groups_list()
        self._update_pixel_options_state()

        self.bind("<Left>", lambda e: self._on_arrow_left())
        self.bind("<Right>", lambda e: self._on_arrow_right())
        self.bind("<Up>", lambda e: self._on_arrow_up())
        self.bind("<Down>", lambda e: self._on_arrow_down())

        self.update_preview()


    def _apply_custom_arrow(self, dropdown):
        try:
            from modules.ui_widgets import _apply_custom_arrow as _shared_apply_custom_arrow
            _shared_apply_custom_arrow(dropdown)
        except Exception:
            pass


    def _init_variables(self):
        # Top-level work mode & publication preset
        self.var_work_mode = ctk.StringVar(value="学术论文图表")
        self.var_preset_theme = ctk.StringVar(value="中文期刊规范")
        self.var_show_title = ctk.BooleanVar(value=False)

        # 1. Chart Type
        analysis_mode = self.project_tree.app_state_params.get('analysis_mode', 'f0')
        if analysis_mode == 'formant':
            self.var_chart_type = ctk.StringVar(value="formant_space")
        else:
            self.var_chart_type = ctk.StringVar(value="contour")

        # 2. Export Scope
        scope_default = "active"
        if self.initial_mode == 'separate':
            scope_default = "separate"
        elif self.initial_mode == 'integrated':
            scope_default = "integrated"
        self.var_export_scope = ctk.StringVar(value=scope_default)

        # 3. Orthogonal Color-by & Facet-by
        self.var_group_by = ctk.StringVar(value="group")
        self.var_color_by = ctk.StringVar(value="按字表组别")
        self.var_facet_by = ctk.StringVar(value="不分面 (单图叠加)")

        # 4. Acoustic Scale
        self.var_scale = ctk.StringVar(value="t_value")

        # 5. Image Format & Layout Size
        self.var_format = ctk.StringVar(value="pdf")
        self.var_layout_size = ctk.StringVar(value="双栏通栏 150mm")
        self.var_dpi = ctk.StringVar(value="600 DPI")

        # Dynamic Options - Tone Contour
        self.var_contour_x = ctk.StringVar(value="normalized")
        self.var_contour_content = ctk.StringVar(value="仅组别平均曲线")
        self.var_contour_facet = ctk.StringVar(value="none")
        self.var_show_chao_five_level = ctk.BooleanVar(value=True)
        self.var_show_spaghetti = ctk.BooleanVar(value=False)
        self.var_show_kde_bg = ctk.BooleanVar(value=False)

        # Dynamic Options - Tone Distribution
        self.var_dist_type = ctk.StringVar(value="全调均值")
        self.var_dist_style = ctk.StringVar(value="雨云图 (Raincloud)")

        # Dynamic Options - Temporal Density (KDE Heatmap)
        self.var_density_bw = ctk.DoubleVar(value=0.15)
        self.var_density_f0_mode = ctk.StringVar(value="percentile")
        self.var_density_p_low = ctk.StringVar(value="5")
        self.var_density_p_high = ctk.StringVar(value="95")
        self.var_density_m_min = ctk.StringVar(value="75")
        self.var_density_m_max = ctk.StringVar(value="600")
        self.var_density_facet = ctk.StringVar(value="按声调类型分面")

        # Dynamic Options - Formant Density (KDE Heatmap)
        self.var_formant_density_overlay = tk.BooleanVar(value=False)
        self.var_formant_density_bw = ctk.DoubleVar(value=0.14)
        self.var_formant_density_show_raw = tk.BooleanVar(value=False)
        self.var_formant_density_show_contours = tk.BooleanVar(value=True)
        self.var_formant_density_facet = ctk.StringVar(value="按字表组分面")

        # Dynamic Options - Quality Check
        self.var_qc_view = ctk.StringVar(value="dual_check")

        # Pagination state
        self.current_preview_page = 0
        self.current_group_page = 0
        self.sort_by_count = False

        # Live refresh switch
        self.var_live_refresh = ctk.BooleanVar(value=True)
        self.var_high_precision = ctk.BooleanVar(value=False)
        self._debounce_timer_id = None

        # Legend configuration
        self.var_legend_outside = ctk.BooleanVar(value=True)

        # Overview heatmap settings
        self.var_overview_show_deviation = ctk.BooleanVar(value=True)
        self.var_sort_by_anomaly = ctk.BooleanVar(value=True)

        # Dynamic Options - Formant Space
        self.var_formant_ellipse = ctk.StringVar(value="95% 置信椭圆")
        self.var_formant_label_mode = ctk.StringVar(value="显示分组标签")
        self.var_formant_show_raw = ctk.BooleanVar(value=True)
        self.var_formant_time_gradient = ctk.BooleanVar(value=False)
        self.var_show_convex_hull = ctk.BooleanVar(value=False)
        self.var_show_diphthong_arrows = ctk.BooleanVar(value=False)
        self.var_formant_normalization = ctk.StringVar(value=self.project_tree.app_state_params.get('formant_normalization', "原始频率 (Hz)"))
        self.var_formant_axis_lock = ctk.BooleanVar(value=bool(self.project_tree.app_state_params.get('formant_axis_lock', False)))

        # Dynamic Options - Formant Trajectory
        self.var_formant_traj_style = ctk.StringVar(value="仅平均曲线")
        self.var_show_f3 = ctk.BooleanVar(value=bool(self.project_tree.app_state_params.get('show_f3', False)))
        self.var_formant_overview_mode = ctk.StringVar(value="F1 & F2 双轨")

        # Image Size & Pixel configuration
        self.var_image_ratio_mode = ctk.StringVar(value="默认")
        self.var_image_ratio_custom = ctk.DoubleVar(value=1.5)
        self.var_image_pixel_mode = ctk.StringVar(value="默认")
        self.var_image_pixel_custom = ctk.IntVar(value=1080)
        self.is_minimized = False
        self._adv_drawer_open = False


    def _init_group_filters(self):
        self._all_base_group_filter_entries = self._extract_active_data(
            self.all_speakers if self.all_speakers else [self.active_speaker],
            filter_groups=False,
            apply_chart_grouping=False,
        )
        all_entries = self._extract_active_data(self.all_speakers if self.all_speakers else [self.active_speaker], filter_groups=False)
        self._all_group_filter_entries = all_entries
        self.group_counts = {}
        for entry in all_entries:
            g = entry['group']
            self.group_counts[g] = self.group_counts.get(g, 0) + 1

        self.available_groups = sorted(list(self.group_counts.keys()))
        self.group_checkbox_vars = {}
        for g in self.available_groups:
            self.group_checkbox_vars[g] = ctk.BooleanVar(value=True)


    def _style_segmented_pill(self, seg_btn, selected_color=None, selected_hover=None):
        sel_color = selected_color or self.ui_primary
        sel_hover = selected_hover or self.ui_primary_hover
        try:
            seg_btn.configure(
                fg_color=("#E2E8F0", "#334155"),
                selected_color=sel_color,
                selected_hover_color=sel_hover,
                unselected_color=("#E2E8F0", "#334155"),
                unselected_hover_color=("#CBD5E1", "#475569"),
                text_color=("#334155", "#E2E8F0"),
                corner_radius=16,
                border_width=4,
                height=32,
            )
            orig_sel = seg_btn._select_button_by_value
            orig_unsel = seg_btn._unselect_button_by_value
            seg_btn._select_button_by_value = lambda v: (
                orig_sel(v),
                seg_btn._buttons_dict[v].configure(text_color="#FFFFFF") if v in getattr(seg_btn, "_buttons_dict", {}) else None,
            )
            seg_btn._unselect_button_by_value = lambda v: (
                orig_unsel(v),
                seg_btn._buttons_dict[v].configure(text_color=("#334155", "#E2E8F0")) if v in getattr(seg_btn, "_buttons_dict", {}) else None,
            )
            orig_set = seg_btn.set

            def _patched_set(val, *args, **kwargs):
                res = orig_set(val, *args, **kwargs)
                for k, btn in getattr(seg_btn, "_buttons_dict", {}).items():
                    btn.configure(text_color="#FFFFFF" if k == val else ("#334155", "#E2E8F0"))
                return res

            seg_btn.set = _patched_set
            curr_val = seg_btn.get()
            for k, btn in getattr(seg_btn, "_buttons_dict", {}).items():
                btn.configure(text_color="#FFFFFF" if k == curr_val else ("#334155", "#E2E8F0"))
        except Exception:
            pass


    def _build_gui(self):
        # Top Header Bar: Dual Work Mode Switch + 3 Publication Preset Themes
        self.grid_columnconfigure(0, weight=0, minsize=400)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=0)
        self.grid_rowconfigure(1, weight=1)

        self.top_mode_bar = ctk.CTkFrame(self, fg_color=("#FFFFFF", "#1E293B"), border_width=1, border_color=("#E5E7EB", "#475569"), corner_radius=12)
        self.top_mode_bar.grid(row=0, column=0, columnspan=2, sticky="ew", padx=15, pady=(10, 4))

        mode_row = ctk.CTkFrame(self.top_mode_bar, fg_color="transparent")
        mode_row.pack(fill=tk.X, padx=14, pady=(6, 4))

        ctk.CTkLabel(mode_row, text="工作模式:", font=self.font_small).pack(side=tk.LEFT, padx=(4, 6))
        self.seg_work_mode = ctk.CTkSegmentedButton(
            mode_row,
            values=["学术论文图表", "数据质检与异常排查"],
            variable=self.var_work_mode,
            font=self.font_main,
            fg_color=("#E2E8F0", "#334155"),
            selected_color=self.ui_primary,
            selected_hover_color=self.ui_primary_hover,
            unselected_color=("#E2E8F0", "#334155"),
            unselected_hover_color=("#CBD5E1", "#475569"),
            text_color=("#334155", "#E2E8F0"),
            corner_radius=16,
            border_width=4,
            height=32,
            command=self._on_work_mode_changed,
        )
        self.seg_work_mode.pack(side=tk.LEFT, padx=(0, 10))
        self._style_segmented_pill(self.seg_work_mode, self.ui_primary, self.ui_primary_hover)

        ctk.CTkFrame(mode_row, width=1, height=20, fg_color="#CBD5E1").pack(side=tk.LEFT, padx=(2, 10))

        ctk.CTkLabel(mode_row, text="出版样式预设:", font=self.font_small).pack(side=tk.LEFT, padx=(4, 6))
        self.seg_preset_theme = ctk.CTkSegmentedButton(
            mode_row,
            values=["中文期刊规范", "国际期刊规范", "汇报演示"],
            variable=self.var_preset_theme,
            font=self.font_small,
            fg_color=("#E2E8F0", "#334155"),
            selected_color="#4F46E5",
            selected_hover_color="#4338CA",
            unselected_color=("#E2E8F0", "#334155"),
            unselected_hover_color=("#CBD5E1", "#475569"),
            text_color=("#334155", "#E2E8F0"),
            corner_radius=16,
            border_width=4,
            height=32,
            command=self._on_preset_theme_changed,
        )
        self.seg_preset_theme.pack(side=tk.LEFT, padx=(0, 6))
        self._style_segmented_pill(self.seg_preset_theme, "#4F46E5", "#4338CA")

        # --- LEFT SIDE: Three-Step Scrollable Configuration Frame ---
        self.left_scroll = ctk.CTkScrollableFrame(self, width=390, fg_color=("#F9FAFB", "#2D3748"))
        self.left_scroll.grid(row=1, column=0, sticky="nsew", padx=(15, 8), pady=(4, 12))

        self._build_settings_cards()

        # --- RIGHT SIDE: Real-time Live Preview & Bottom Export Control Bar ---
        self.right_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.right_frame.grid(row=1, column=1, sticky="nsew", padx=(8, 15), pady=(4, 12))
        self.right_frame.grid_rowconfigure(0, weight=0)
        self.right_frame.grid_rowconfigure(1, weight=1)
        self.right_frame.grid_rowconfigure(2, weight=0)
        self.right_frame.grid_rowconfigure(3, weight=0)
        self.right_frame.grid_rowconfigure(4, weight=0)
        self.right_frame.grid_rowconfigure(5, weight=0)
        self.right_frame.grid_columnconfigure(0, weight=1)

        # Top Bar for floating button and native truncation banner
        self.preview_top_bar = ctk.CTkFrame(self.right_frame, fg_color="transparent")
        self.preview_top_bar.grid(row=0, column=0, sticky="ew", pady=(0, 4))

        self.btn_toggle_minimize = ctk.CTkButton(
            self.preview_top_bar, text="📌 悬浮模式", width=90, height=26, corner_radius=13,
            font=self.font_small, fg_color=("#E5E7EB", "#374151"), text_color=("#374151", "#E5E7EB"),
            hover_color=("#D1D5DB", "#4B5563"), bg_color="transparent", command=self.toggle_minimize
        )
        self.btn_toggle_minimize.pack(side=tk.LEFT)

        self.truncation_banner_frame = ctk.CTkFrame(
            self.preview_top_bar,
            fg_color=("#FEF2F2", "#450A0A"),
            corner_radius=12,
            height=26
        )
        self.lbl_truncation_banner = ctk.CTkLabel(
            self.truncation_banner_frame,
            text="",
            font=ctk.CTkFont(family="Microsoft YaHei", size=11, weight="bold"),
            fg_color="transparent",
            text_color=("#991B1B", "#FCA5A5"),
            height=26
        )
        self.lbl_truncation_banner.pack(padx=10, fill=tk.BOTH, expand=True)

        # Preview Canvas Container
        self.preview_wrapper = ctk.CTkFrame(self.right_frame, fg_color="transparent")
        self.preview_wrapper.grid(row=1, column=0, sticky="nsew", pady=(0, 6))
        self.preview_wrapper.grid_rowconfigure(0, weight=1)
        self.preview_wrapper.grid_columnconfigure(0, weight=1)

        self.preview_container = ctk.CTkFrame(self.preview_wrapper, fg_color="#FFFFFF", border_width=1, border_color="#D1D5DB")
        self.preview_container.place(relx=0.5, rely=0.5, anchor="center")
        self.preview_container.grid_rowconfigure(0, weight=1)
        self.preview_container.grid_columnconfigure(0, weight=1)

        self.preview_wrapper.bind("<Button-2>", self._show_context_menu, add="+")
        self.preview_wrapper.bind("<Button-3>", self._show_context_menu, add="+")

        self.preview_lbl = ctk.CTkLabel(self.preview_container, text="正在加载图表预览...", font=self.font_title, text_color="#6B7280")
        self.preview_lbl.grid(row=0, column=0)

        self._bind_chart_context_menu(self.preview_container)
        self.preview_wrapper.bind("<Configure>", lambda e: self.on_preview_wrapper_configure())

        # Pagination Frame
        self.pagination_frame = ctk.CTkFrame(self.right_frame, fg_color="transparent")
        self.pagination_frame.grid_columnconfigure(0, weight=1)
        self.pagination_frame.grid_columnconfigure(1, weight=0)
        self.pagination_frame.grid_columnconfigure(2, weight=1)

        self.btn_prev_page = ctk.CTkButton(
            self.pagination_frame, text="◀ 上一个发音人", width=120, height=28, corner_radius=14,
            fg_color=("#F3F4F6", "#374151"), text_color=("#374151", "#E5E7EB"), hover_color=("#E5E7EB", "#4B5563"),
            font=self.font_main, command=self._prev_page
        )
        self.btn_prev_page.grid(row=0, column=0, padx=10, sticky="e")

        self.lbl_page_info = ctk.CTkLabel(self.pagination_frame, text="发音人: - (0/0)", font=self.font_title)
        self.lbl_page_info.grid(row=0, column=1, padx=20)

        self.btn_next_page = ctk.CTkButton(
            self.pagination_frame, text="下一个发音人 ▶", width=120, height=28, corner_radius=14,
            fg_color=("#F3F4F6", "#374151"), text_color=("#374151", "#E5E7EB"), hover_color=("#E5E7EB", "#4B5563"),
            font=self.font_main, command=self._next_page
        )
        self.btn_next_page.grid(row=0, column=2, padx=10, sticky="w")

        # Group Pagination Frame
        self.group_pagination_frame = ctk.CTkFrame(self.right_frame, fg_color="transparent")
        self.group_pagination_frame.grid_columnconfigure(0, weight=1)
        self.group_pagination_frame.grid_columnconfigure(1, weight=0)
        self.group_pagination_frame.grid_columnconfigure(2, weight=1)

        self.btn_prev_group_page = ctk.CTkButton(
            self.group_pagination_frame, text="◀ 上一页", width=88, height=28, corner_radius=14,
            fg_color=("#F3F4F6", "#374151"), text_color=("#374151", "#E5E7EB"), hover_color=("#E5E7EB", "#4B5563"),
            font=self.font_main, command=self._prev_group_page
        )
        self.btn_prev_group_page.grid(row=0, column=0, padx=10, sticky="e")

        self.lbl_group_page_info = ctk.CTkLabel(
            self.group_pagination_frame, text="组别页码: - (0/0)",
            font=ctk.CTkFont(family="Microsoft YaHei", size=12, weight="bold")
        )
        self.lbl_group_page_info.grid(row=0, column=1, padx=20)

        self.btn_next_group_page = ctk.CTkButton(
            self.group_pagination_frame, text="下一页 ▶", width=88, height=28, corner_radius=14,
            fg_color=("#F3F4F6", "#374151"), text_color=("#374151", "#E5E7EB"), hover_color=("#E5E7EB", "#4B5563"),
            font=self.font_main, command=self._next_group_page
        )
        self.btn_next_group_page.grid(row=0, column=2, padx=10, sticky="w")

        # QC Hover Status Bar (hidden by default; shown on demand in QC hover)
        self.lbl_qc_hover_status = ctk.CTkLabel(
            self.right_frame,
            text="",
            font=self.font_small,
            text_color=("#4B5563", "#CBD5E1"),
            anchor="w",
        )
        self.lbl_qc_hover_status.grid(row=4, column=0, sticky="ew", padx=4, pady=(0, 3))
        self.lbl_qc_hover_status.grid_remove()

        # --- BOTTOM INDEPENDENT EXPORT CONTROL BAR ---
        self.bottom_frame = ctk.CTkFrame(
            self.right_frame,
            fg_color=("#FFFFFF", "#1E293B"),
            border_width=1,
            border_color=("#E5E7EB", "#475569"),
            corner_radius=12,
        )
        self.bottom_frame.grid(row=5, column=0, sticky="ew")
        self.bottom_export_bar = self.bottom_frame

        row_specs = ctk.CTkFrame(self.bottom_frame, fg_color="transparent")
        row_specs.pack(fill=tk.X, padx=10, pady=(6, 3))

        ctk.CTkLabel(row_specs, text="版面:", font=self.font_small).pack(side=tk.LEFT, padx=(0, 3))
        self.combo_layout_size = ctk.CTkOptionMenu(
            row_specs,
            values=["双栏通栏 150mm", "单栏 80mm", "单栏 85mm", "1.5栏 114mm", "双栏 170mm", "汇报宽屏 16:9", "自适应默认"],
            variable=self.var_layout_size,
            width=124,
            command=lambda _: self.trigger_preview_update(),
            **self.dropdown_kwargs,
        )
        self.combo_layout_size.pack(side=tk.LEFT, padx=(0, 6))
        self._apply_custom_arrow(self.combo_layout_size)

        ctk.CTkLabel(row_specs, text="比例:", font=self.font_small).pack(side=tk.LEFT, padx=(2, 3))
        self.combo_ratio_mode = ctk.CTkOptionMenu(
            row_specs,
            values=["默认", "4:3", "16:9", "3:2", "1:1", "16:10", "2:1", "自定义"],
            width=72,
            command=self._on_ratio_mode_changed,
            **self.dropdown_kwargs,
        )
        self.combo_ratio_mode.set("默认")
        self.combo_ratio_mode.pack(side=tk.LEFT, padx=(0, 6))
        self._apply_custom_arrow(self.combo_ratio_mode)

        # Inline Custom Aspect Ratio Controls (shown next to combo_ratio_mode when "自定义" is selected)
        self.custom_ratio_inline_frame = ctk.CTkFrame(row_specs, fg_color="transparent")
        self.slider_ratio_custom = ctk.CTkSlider(
            self.custom_ratio_inline_frame,
            width=76,
            height=16,
            from_=0.5,
            to=2.5,
            number_of_steps=40,
            variable=self.var_image_ratio_custom,
            progress_color=self.ui_primary,
            button_color=self.ui_primary,
            button_hover_color=self.ui_primary_hover,
            command=self._on_ratio_slider_change,
        )
        self.slider_ratio_custom.pack(side=tk.LEFT, padx=(0, 4))
        self.entry_ratio_custom = ctk.CTkEntry(
            self.custom_ratio_inline_frame,
            width=48,
            height=28,
            corner_radius=8,
            font=self.font_small,
            justify="center",
            fg_color=("#EEF2F6", "#334155"),
            border_width=1,
            border_color=("#CBD5E1", "#475569"),
        )
        self.entry_ratio_custom.insert(0, "1.50")
        self.entry_ratio_custom.pack(side=tk.LEFT)
        self.entry_ratio_custom.bind("<Return>", self._on_custom_ratio_entry_commit)
        self.entry_ratio_custom.bind("<FocusOut>", self._on_custom_ratio_entry_commit)

        # Keep combo_dpi attribute instantiated (unpacked) for internal preset/API compatibility
        self.combo_dpi = ctk.CTkOptionMenu(
            row_specs,
            values=["600 DPI", "300 DPI", "150 DPI"],
            variable=self.var_dpi,
            width=84,
            command=lambda _: self.trigger_preview_update(),
            **self.dropdown_kwargs,
        )
        self._apply_custom_arrow(self.combo_dpi)

        ctk.CTkLabel(row_specs, text="格式:", font=self.font_small).pack(side=tk.LEFT, padx=(2, 3))
        self.combo_format = ctk.CTkOptionMenu(
            row_specs,
            values=["PDF 文档 (.pdf)", "SVG 矢量图 (.svg)", "PNG 图片 (.png)"],
            width=126,
            command=self._on_format_changed,
            **self.dropdown_kwargs,
        )
        self.combo_format.set("PDF 文档 (.pdf)")
        self.combo_format.pack(side=tk.LEFT, padx=(0, 4))
        self._apply_custom_arrow(self.combo_format)

        row_actions = ctk.CTkFrame(self.bottom_frame, fg_color="transparent")
        row_actions.pack(fill=tk.X, padx=10, pady=(2, 6))

        self.btn_manual_refresh = ctk.CTkButton(
            row_actions, text="🔄 刷新预览", width=96, height=32, corner_radius=16,
            fg_color="#E5E7EB", text_color="#374151", hover_color="#D1D5DB",
            font=self.font_small, command=self.manual_refresh_preview
        )
        self.btn_manual_refresh.pack(side=tk.LEFT, padx=(0, 6))

        self.switch_live_refresh = ctk.CTkSwitch(
            row_actions, text="实时刷新", variable=self.var_live_refresh,
            font=self.font_small, progress_color=self.ui_primary,
            button_hover_color=self.ui_primary_hover, command=self._on_live_refresh_toggle
        )
        self.switch_live_refresh.pack(side=tk.LEFT, padx=6)

        self.btn_export_data = ctk.CTkButton(
            row_actions, text="📊 导出数据", width=96, height=32, corner_radius=16,
            font=self.font_small, fg_color="#3B82F6", hover_color="#2563EB",
            command=self.export_chart_table_data
        )
        self.btn_export_data.pack(side=tk.RIGHT, padx=4)

        self.btn_export = ctk.CTkButton(
            row_actions, text="💾 导出图表", width=106, height=32, corner_radius=16,
            font=self.font_main, fg_color="#10B981", hover_color="#059669",
            command=self.on_confirm
        )
        self.btn_export.pack(side=tk.RIGHT, padx=4)

        self.btn_copy_image = ctk.CTkButton(
            row_actions, text="📋 复制图片", width=96, height=32, corner_radius=16,
            font=self.font_small, fg_color="#6366F1", hover_color="#4F46E5",
            command=self.copy_figure_to_clipboard
        )
        self.btn_copy_image.pack(side=tk.RIGHT, padx=4)

        self.btn_restore = ctk.CTkButton(
            row_actions, text="🗗 还原", width=80, height=28, corner_radius=14,
            fg_color=("#E5E7EB", "#374151"), text_color=("#374151", "#E5E7EB"),
            hover_color=("#D1D5DB", "#4B5563"),
            font=self.font_small, command=self.toggle_minimize
        )


    def _on_work_mode_changed(self, val):
        mode = self._normalize_work_mode(val)
        self.work_mode = mode
        if mode != "qc" and hasattr(self, "lbl_qc_hover_status"):
            try:
                self.lbl_qc_hover_status.configure(text="")
                self.lbl_qc_hover_status.grid_remove()
            except Exception:
                pass
        if self._is_formant_mode():
            type_values = self._get_formant_chart_type_values(work_mode=mode)
            default_type = type_values[0]
        else:
            type_values = self._get_tone_chart_type_values(work_mode=mode)
            default_type = type_values[0]
        if hasattr(self, "combo_type"):
            self.combo_type.configure(values=type_values)
            self.combo_type.set(default_type)
            self._on_type_changed(default_type)


    def _on_preset_theme_changed(self, val):
        preset = self.apply_publication_preset(val)
        if hasattr(self, "combo_layout_size") and preset.get("default_layout_size"):
            self.combo_layout_size.set(preset["default_layout_size"])
        if hasattr(self, "combo_dpi") and preset.get("default_dpi"):
            self.combo_dpi.set(f"{int(preset['default_dpi'])} DPI")
        if hasattr(self, "var_show_title"):
            self.var_show_title.set(bool(preset.get("show_title", False)))
        if preset["name"] == "presentation" and hasattr(self, "combo_format"):
            self.combo_format.set("PNG 图片 (.png)")
            self._update_pixel_options_state()
        self.update_preview()


    def _prev_page(self):
        if not self.all_speakers:
            return
        self.current_preview_page = (self.current_preview_page - 1) % len(self.all_speakers)
        self.update_preview()


    def _next_page(self):
        if not self.all_speakers:
            return
        self.current_preview_page = (self.current_preview_page + 1) % len(self.all_speakers)
        self.update_preview()


    def _prev_group_page(self):
        data = self._get_current_data_entries()
        if not data:
            return
        pagination_state = self._get_group_pagination_state(data, self.var_chart_type.get(), self.combo_groupby.get())
        total_pages = pagination_state['total_pages']
        self.current_group_page = (self.current_group_page - 1) % total_pages
        self.update_preview()


    def _next_group_page(self):
        data = self._get_current_data_entries()
        if not data:
            return
        pagination_state = self._get_group_pagination_state(data, self.var_chart_type.get(), self.combo_groupby.get())
        total_pages = pagination_state['total_pages']
        self.current_group_page = (self.current_group_page + 1) % total_pages
        self.update_preview()


    def _build_settings_cards(self):
        card_padding = {"padx": 8, "pady": 5}

        # ======================================================================
        # 【第一步：选择图表与声学单位】
        # ======================================================================
        self.step1_card = ctk.CTkFrame(self.left_scroll, fg_color=("#FFFFFF", "#1E293B"), border_width=1, border_color=("#E5E7EB", "#475569"), corner_radius=12)
        self.step1_card.pack(fill=tk.X, **card_padding)
        card1 = self.step1_card

        ctk.CTkLabel(card1, text="【第一步：选择图表与声学单位】", font=self.font_title).pack(anchor="w", padx=12, pady=(8, 4))

        if self._is_formant_mode():
            type_values = self._get_formant_chart_type_values()
            default_type = "1. 元音声学空间图"
            scale_values = ["原始频率 (Hz)", "Bark (听觉巴克)", "ERB (等效矩形带宽)", "Lobanov z-score (学术标准)"]
            scale_default = "原始频率 (Hz)"
        else:
            type_values = self._get_tone_chart_type_values()
            default_type = "1. 声调格局与时序轮廓图"
            scale_values = ["T 值 (五度标调)", "Hz (基频绝对频率)"]
            scale_default = "T 值 (五度标调)"

        row_s1 = ctk.CTkFrame(card1, fg_color="transparent")
        row_s1.pack(fill=tk.X, padx=12, pady=(2, 8))
        row_s1.grid_columnconfigure(0, weight=3)
        row_s1.grid_columnconfigure(1, weight=2)

        ctk.CTkLabel(row_s1, text="图表类型:", font=self.font_small).grid(row=0, column=0, sticky="w", padx=(0, 4))
        ctk.CTkLabel(row_s1, text="声学标度:", font=self.font_small).grid(row=0, column=1, sticky="w", padx=(4, 0))

        self.combo_type = ctk.CTkOptionMenu(row_s1, values=type_values, command=self._on_type_changed, **self.dropdown_kwargs)
        self.combo_type.set(default_type)
        self.combo_type.grid(row=1, column=0, sticky="ew", padx=(0, 4), pady=(2, 0))
        self._apply_custom_arrow(self.combo_type)

        self.combo_scale = ctk.CTkOptionMenu(row_s1, values=scale_values, command=self._on_scale_changed, **self.dropdown_kwargs)
        self.combo_scale.set(scale_default)
        self.combo_scale.grid(row=1, column=1, sticky="ew", padx=(4, 0), pady=(2, 0))
        self._apply_custom_arrow(self.combo_scale)

        # ======================================================================
        # 【第二步：对比维度与数据范围】
        # ======================================================================
        self.step2_card = ctk.CTkFrame(self.left_scroll, fg_color=("#FFFFFF", "#1E293B"), border_width=1, border_color=("#E5E7EB", "#475569"), corner_radius=12)
        self.step2_card.pack(fill=tk.X, **card_padding)
        card2 = self.step2_card

        ctk.CTkLabel(card2, text="【第二步：对比维度与数据范围】", font=self.font_title).pack(anchor="w", padx=12, pady=(8, 4))

        dim_grid = ctk.CTkFrame(card2, fg_color="transparent")
        dim_grid.pack(fill=tk.X, padx=12, pady=(0, 6))
        dim_grid.grid_columnconfigure(0, weight=1)
        dim_grid.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(dim_grid, text="曲线/颜色对比 (Color by):", font=self.font_small).grid(row=0, column=0, sticky="w", padx=(0, 4))
        ctk.CTkLabel(dim_grid, text="子图分面排版 (Facet by):", font=self.font_small).grid(row=0, column=1, sticky="w", padx=(4, 0))

        color_by_values = ["按字表组别", "按词语", "按单字音节", "按发音人", "按自定义字段..."]
        self.combo_color_by = ctk.CTkOptionMenu(dim_grid, values=color_by_values, command=self._on_groupby_changed, **self.dropdown_kwargs)
        self.combo_color_by.set("按字表组别")
        self.combo_color_by.grid(row=1, column=0, sticky="ew", padx=(0, 4), pady=(2, 6))
        self._apply_custom_arrow(self.combo_color_by)
        # Keep backward-compatible alias self.combo_groupby
        self.combo_groupby = self.combo_color_by

        facet_by_values = ["不分面 (单图叠加)", "按字表组别分面", "按音节位置分面", "按发音人分面"]
        self.combo_facet_by = ctk.CTkOptionMenu(dim_grid, values=facet_by_values, command=self._on_facet_by_changed, **self.dropdown_kwargs)
        self.combo_facet_by.set("不分面 (单图叠加)")
        self.combo_facet_by.grid(row=1, column=1, sticky="ew", padx=(4, 0), pady=(2, 6))
        self._apply_custom_arrow(self.combo_facet_by)

        ctk.CTkLabel(dim_grid, text="发音人范围:", font=self.font_small).grid(row=2, column=0, columnspan=2, sticky="w")
        self.combo_scope = ctk.CTkOptionMenu(
            dim_grid,
            values=["仅当前发音人", "所有发音人(分别导出)", "所有发音人(整合导出)"],
            command=self._on_scope_changed,
            **self.dropdown_kwargs,
        )
        if self.initial_mode == 'separate':
            self.combo_scope.set("所有发音人(分别导出)")
        elif self.initial_mode == 'integrated':
            self.combo_scope.set("所有发音人(整合导出)")
        else:
            self.combo_scope.set("仅当前发音人")
        self.combo_scope.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(2, 4))
        self._apply_custom_arrow(self.combo_scope)
        if not self.all_speakers or len(self.all_speakers) <= 1:
            self.combo_scope.configure(state="disabled")

        # Embedded Group Filter inside Step 2
        self.card_filter = ctk.CTkFrame(card2, fg_color=("#F8FAFC", "#263244"), corner_radius=10)
        self.card_filter.pack(fill=tk.X, padx=10, pady=(2, 8))

        search_btn_row = ctk.CTkFrame(self.card_filter, fg_color="transparent")
        search_btn_row.pack(fill=tk.X, padx=8, pady=(6, 3))

        self.lbl_group_filter_title = ctk.CTkLabel(search_btn_row, text="参与组别:", font=self.font_small)
        self.lbl_group_filter_title.pack(side=tk.LEFT, padx=(0, 4))

        self.search_group_var = ctk.StringVar()
        self.search_group_var.trace_add("write", lambda *args: self._filter_groups_list())

        self.entry_search_group = ctk.CTkEntry(
            search_btn_row, placeholder_text="搜索...", textvariable=self.search_group_var,
            font=self.font_small, height=26, fg_color="white", text_color="#1F2937",
            border_width=1, border_color="#E5E7EB", corner_radius=13
        )
        self.entry_search_group.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))

        ctk.CTkButton(
            search_btn_row, text="全选", width=46, height=24, corner_radius=12,
            fg_color=self.ui_primary, hover_color=self.ui_primary_hover,
            font=self.font_small, command=self._select_all_groups
        ).pack(side=tk.LEFT, padx=2)

        ctk.CTkButton(
            search_btn_row, text="反选", width=46, height=24, corner_radius=12,
            fg_color=self.ui_primary, hover_color=self.ui_primary_hover,
            font=self.font_small, command=self._reverse_groups
        ).pack(side=tk.LEFT, padx=2)

        self.filter_scroll = ctk.CTkScrollableFrame(self.card_filter, height=90, fg_color="transparent")
        self.filter_scroll.pack(fill=tk.X, padx=6, pady=2)
        try:
            self.filter_scroll._parent_canvas.configure(yscrollincrement=15)
        except Exception:
            pass

        def on_enter_filter_scroll(e):
            method = getattr(self.left_scroll, "_disable_all_bindings", None)
            if callable(method):
                try:
                    method()
                except Exception:
                    pass

        def on_leave_filter_scroll(e):
            x = self.filter_scroll.winfo_pointerx() - self.filter_scroll.winfo_rootx()
            y = self.filter_scroll.winfo_pointery() - self.filter_scroll.winfo_rooty()
            w = self.filter_scroll.winfo_width()
            h = self.filter_scroll.winfo_height()
            if not (0 <= x < w and 0 <= y < h):
                method = getattr(self.left_scroll, "_enable_all_bindings", None)
                if callable(method):
                    try:
                        method()
                    except Exception:
                        pass

        self.filter_scroll.bind("<Enter>", on_enter_filter_scroll, add="+")
        self.filter_scroll.bind("<Leave>", on_leave_filter_scroll, add="+")

        util_btn_frame2 = ctk.CTkFrame(self.card_filter, fg_color="transparent")
        util_btn_frame2.pack(fill=tk.X, padx=8, pady=(2, 6))

        self.btn_toggle_sort = ctk.CTkButton(
            util_btn_frame2, text="按样本量排序", width=88, height=22, corner_radius=11, font=self.font_small,
            fg_color=("#E5E7EB", "#374151"), text_color=("#374151", "#E5E7EB"), hover_color=("#D1D5DB", "#4B5563"),
            command=self._toggle_groups_sorting
        )
        self.btn_toggle_sort.pack(side=tk.LEFT, padx=2)

        ctk.CTkLabel(util_btn_frame2, text="样本量 >", font=self.font_small).pack(side=tk.LEFT, padx=(8, 2))
        self.entry_min_count = ctk.CTkEntry(util_btn_frame2, width=36, height=22, font=self.font_small, justify="center")
        self.entry_min_count.insert(0, "0")
        self.entry_min_count.pack(side=tk.LEFT, padx=2)
        self.entry_min_count.bind("<KeyRelease>", lambda e: self._populate_groups_list())

        # ======================================================================
        # 【第三步：图表要素】 + ▸ 高级图形参数 折叠抽屉
        # ======================================================================
        self.dynamic_card = ctk.CTkFrame(self.left_scroll, fg_color=("#FFFFFF", "#1E293B"), border_width=1, border_color=("#E5E7EB", "#475569"), corner_radius=12)
        self.dynamic_card.pack(fill=tk.X, **card_padding)
        self.step3_card = self.dynamic_card

        self.dynamic_title = ctk.CTkLabel(self.dynamic_card, text="【第三步：图表要素】", font=self.font_title)
        self.dynamic_title.pack(anchor="w", padx=12, pady=(8, 4))

        self.dynamic_content_frame = ctk.CTkFrame(self.dynamic_card, fg_color="transparent")
        self.dynamic_content_frame.pack(fill=tk.X, padx=12, pady=(0, 6))

        if self._is_formant_mode():
            self._build_formant_space_settings()
        else:
            self._build_contour_settings()

        # Collapsible Drawer: ▸ 高级图形参数 (字号/图例/极值过滤)
        self.btn_adv_drawer = ctk.CTkButton(
            self.dynamic_card,
            text="▸ 高级图形参数 (字号/图例/极值过滤)",
            height=28,
            corner_radius=8,
            anchor="w",
            font=self.font_small,
            fg_color=("#F3F4F6", "#334155"),
            text_color=("#374151", "#E5E7EB"),
            hover_color=("#E5E7EB", "#475569"),
            command=self._toggle_adv_drawer,
        )
        self.btn_adv_drawer.pack(fill=tk.X, padx=12, pady=(2, 8))

        self.adv_drawer_frame = ctk.CTkFrame(self.dynamic_card, fg_color=("#F8FAFC", "#263244"), corner_radius=10)
        self.adv_drawer_frame.grid_columnconfigure(0, weight=1)
        self.adv_drawer_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(self.adv_drawer_frame, text="图例位置:", font=self.font_small).grid(row=0, column=0, sticky="w", padx=10, pady=(6, 2))
        ctk.CTkLabel(self.adv_drawer_frame, text="导出意图预设:", font=self.font_small).grid(row=0, column=1, sticky="w", padx=10, pady=(6, 2))

        self.combo_legend_loc = ctk.CTkOptionMenu(self.adv_drawer_frame, values=["右上", "右下", "左上", "左下"], command=lambda _: self.update_preview(), **self.dropdown_kwargs)
        self.combo_legend_loc.set("右上")
        self.combo_legend_loc.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 6))
        self._apply_custom_arrow(self.combo_legend_loc)

        intention_values = ["论文主图 (清晰对比)", "附录图册 (完整数据)", "数据诊断 (质量排查)"]
        self.combo_intention = ctk.CTkOptionMenu(self.adv_drawer_frame, values=intention_values, command=self._on_intention_changed, **self.dropdown_kwargs)
        self.combo_intention.set("论文主图 (清晰对比)")
        self.combo_intention.grid(row=1, column=1, sticky="ew", padx=10, pady=(0, 6))
        self._apply_custom_arrow(self.combo_intention)

        self.cb_legend_outside = self._make_checkbox(
            self.adv_drawer_frame, text="图例置于图表主体外侧", variable=self.var_legend_outside,
            command=self.update_preview
        )
        self.cb_legend_outside.grid(row=2, column=0, sticky="w", padx=10, pady=(2, 6))

        self.cb_show_title = self._make_checkbox(
            self.adv_drawer_frame, text="显示图内顶部大标题", variable=self.var_show_title,
            command=self.update_preview
        )
        self.cb_show_title.grid(row=2, column=1, sticky="w", padx=10, pady=(2, 6))

        self.switch_high_precision = ctk.CTkSwitch(
            self.adv_drawer_frame, text="高渲染精细度", variable=self.var_high_precision,
            font=self.font_small, progress_color=self.ui_primary, command=self.update_preview
        )
        self.switch_high_precision.grid(row=3, column=0, sticky="w", padx=10, pady=(2, 8))

        group_rule_bar = ctk.CTkFrame(self.adv_drawer_frame, fg_color="transparent")
        group_rule_bar.grid(row=3, column=1, sticky="ew", padx=10, pady=(2, 8))
        self.lbl_chart_group_rule = ctk.CTkLabel(
            group_rule_bar,
            text=self._format_chart_group_rule_summary(self.chart_group_rule),
            font=self.font_small,
            anchor="w",
        )
        self.lbl_chart_group_rule.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ctk.CTkButton(
            group_rule_bar, text="自定义分组规则", width=96, height=24, corner_radius=12,
            font=self.font_small, fg_color=(self.ui_primary, self.ui_primary_hover),
            command=self._open_chart_group_rule_dialog,
        ).pack(side=tk.RIGHT)

        # Keep hidden/compatible controls for custom ratio & pixel sliders
        self.combo_pixel_mode = ctk.CTkOptionMenu(self.adv_drawer_frame, values=["默认", "480 px", "600 px", "720 px", "1080 px", "1440 px", "2160 px", "自定义"], command=self._on_pixel_mode_changed, **self.dropdown_kwargs)
        self.combo_pixel_mode.set("默认")
        self.lbl_ratio_val = ctk.CTkLabel(self.adv_drawer_frame, text="1.50", font=self.font_small)
        self.entry_pixel_custom = ctk.CTkEntry(self.adv_drawer_frame, placeholder_text="1080", font=self.font_small, height=26)
        self.entry_pixel_custom.insert(0, "1080")

        self._bind_left_scroll_wheel_recursive(self.left_scroll)


    def _toggle_adv_drawer(self):
        self._adv_drawer_open = not getattr(self, "_adv_drawer_open", False)
        if self._adv_drawer_open:
            self.btn_adv_drawer.configure(text="▾ 高级图形参数 (字号/图例/极值过滤)")
            self.adv_drawer_frame.pack(fill=tk.X, padx=12, pady=(0, 10))
        else:
            self.btn_adv_drawer.configure(text="▸ 高级图形参数 (字号/图例/极值过滤)")
            self.adv_drawer_frame.pack_forget()


    def _on_scale_changed(self, val):
        if self._is_formant_mode():
            self.var_formant_normalization.set(val)
            if hasattr(self, "combo_formant_normalization"):
                try:
                    self.combo_formant_normalization.set(val)
                except Exception:
                    pass
        self.update_preview()


    def _on_facet_by_changed(self, val):
        self.var_facet_by.set(val)
        for attr in ("combo_contour_facet", "combo_density_facet", "combo_formant_density_facet"):
            if hasattr(self, attr):
                try:
                    getattr(self, attr).set(val)
                except Exception:
                    pass
        self.update_preview()


    def _sync_facet_from_child(self, val):
        if hasattr(self, "combo_facet_by"):
            try:
                self.combo_facet_by.set(val)
            except Exception:
                pass
        self.trigger_preview_update()


    def _on_filter_scroll_wheel(self, event):
        try:
            from modules.ui_widgets import _close_active_dropdown_popover
            _close_active_dropdown_popover(mark_closed_owner=False)
        except Exception:
            pass
        canvas = getattr(self.filter_scroll, "_parent_canvas", None)
        if canvas is None:
            return "break"

        if getattr(event, "num", None) == 4:
            steps = -3
        elif getattr(event, "num", None) == 5:
            steps = 3
        else:
            delta = getattr(event, "delta", 0)
            if delta == 0:
                return "break"
            if abs(delta) >= 120:
                steps = -int(delta / 120) * 3
            else:
                steps = -1 if delta > 0 else 1

        if canvas.yview() != (0.0, 1.0):
            canvas.yview_scroll(steps, "units")
        return "break"


    def _bind_filter_scroll_wheel_recursive(self, widget):
        if not getattr(widget, "_phontracer_filter_wheel_bound", False):
            for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
                widget.bind(sequence, self._on_filter_scroll_wheel, add="+")
            widget._phontracer_filter_wheel_bound = True

        for child in widget.winfo_children():
            self._bind_filter_scroll_wheel_recursive(child)


    def _on_left_scroll_wheel(self, event):
        try:
            from modules.ui_widgets import _close_active_dropdown_popover
            _close_active_dropdown_popover(mark_closed_owner=False)
        except Exception:
            pass
        if hasattr(self, 'filter_scroll') and self.filter_scroll:
            try:
                if str(event.widget).startswith(str(self.filter_scroll)):
                    return "break"
            except Exception:
                pass

        canvas = getattr(self.left_scroll, "_parent_canvas", None)
        if canvas is None:
            return "break"

        if getattr(event, "num", None) == 4:
            steps = -60
        elif getattr(event, "num", None) == 5:
            steps = 60
        else:
            delta = getattr(event, "delta", 0)
            if delta == 0:
                return "break"
            if abs(delta) >= 120:
                steps = -int(delta / 120) * 60
            else:
                step_mag = max(2, int(abs(delta) * 0.5))
                steps = -step_mag if delta > 0 else step_mag

        try:
            canvas.configure(yscrollincrement=1)
        except Exception:
            pass

        if canvas.yview() != (0.0, 1.0):
            canvas.yview_scroll(steps, "units")
        return "break"


    def _bind_left_scroll_wheel_recursive(self, widget):
        if widget == getattr(self, "filter_scroll", None):
            return

        if not getattr(widget, "_phontracer_left_wheel_bound", False):
            for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
                widget.bind(sequence, self._on_left_scroll_wheel, add="+")
            widget._phontracer_left_wheel_bound = True

        for child in widget.winfo_children():
            self._bind_left_scroll_wheel_recursive(child)


    def _apply_chart_type_defaults(self, norm_type):
        defaults_map = {
            "contour": ("按字表组别", "不分面 (单图叠加)"),
            "tone_sandhi": ("按字表组别", "按字表组别分面"),
            "distribution": ("按字表组别", "不分面 (单图叠加)"),
            "density": ("按字表组别", "按字表组别分面"),
            "formant_space": ("按字表组别", "不分面 (单图叠加)"),
            "formant_trajectory": ("按字表组别", "不分面 (单图叠加)"),
            "formant_pillai": ("按字表组别", "不分面 (单图叠加)"),
            "formant_density": ("按字表组别", "按字表组别分面"),
            "overview_heatmap": ("按词语", "按字表组别分面"),
            "formant_overview_heatmap": ("按词语", "按字表组别分面"),
            "spaghetti_qc": ("按字表组别", "按字表组别分面"),
            "quality": ("按发音人", "不分面 (单图叠加)"),
        }
        color_by_val, facet_by_val = defaults_map.get(norm_type, ("按字表组别", "不分面 (单图叠加)"))
        if hasattr(self, "var_color_by"):
            self.var_color_by.set(color_by_val)
        if hasattr(self, "combo_color_by"):
            try:
                self.combo_color_by.set(color_by_val)
            except Exception:
                pass
        elif hasattr(self, "combo_groupby"):
            try:
                self.combo_groupby.set(color_by_val)
            except Exception:
                pass

        if hasattr(self, "var_facet_by"):
            self.var_facet_by.set(facet_by_val)
        if hasattr(self, "combo_facet_by"):
            try:
                self.combo_facet_by.set(facet_by_val)
            except Exception:
                pass

        if norm_type == "contour":
            if hasattr(self, "var_contour_content"):
                self.var_contour_content.set("仅组别平均曲线")
            if hasattr(self, "combo_contour_content"):
                try:
                    self.combo_contour_content.set("仅组别平均曲线")
                except Exception:
                    pass
        elif norm_type == "formant_trajectory":
            if hasattr(self, "var_formant_traj_style"):
                self.var_formant_traj_style.set("仅平均曲线")
            if hasattr(self, "combo_formant_traj_style"):
                try:
                    self.combo_formant_traj_style.set("仅平均曲线")
                except Exception:
                    pass
        elif norm_type == "density":
            if hasattr(self, "var_density_facet"):
                self.var_density_facet.set("按声调类型分面")
            if hasattr(self, "combo_density_facet"):
                try:
                    self.combo_density_facet.set("按声调类型分面")
                except Exception:
                    pass
        elif norm_type == "formant_density":
            if hasattr(self, "var_formant_density_facet"):
                self.var_formant_density_facet.set("按字表组分面")
            if hasattr(self, "combo_formant_density_facet"):
                try:
                    self.combo_formant_density_facet.set("按字表组分面")
                except Exception:
                    pass


    def _on_type_changed(self, val):
        self.current_group_page = 0
        for widget in self.dynamic_content_frame.winfo_children():
            widget.destroy()

        norm_type = self._normalize_chart_type(val)
        self.var_chart_type.set(norm_type)

        if norm_type == "contour":
            self.dynamic_title.configure(text="【第三步：声调格局与轮廓图要素】")
            self._build_contour_settings()
        elif norm_type == "tone_sandhi":
            self.dynamic_title.configure(text="【第三步：连读变调与协同发音要素】")
            self._build_tone_sandhi_settings()
        elif norm_type == "distribution":
            self.dynamic_title.configure(text="【第三步：声调特征分布雨云图要素】")
            self._build_distribution_settings()
        elif norm_type == "density":
            self.dynamic_title.configure(text="【第三步：时序密度图要素】")
            self._build_density_settings()
        elif norm_type == "spaghetti_qc":
            self.dynamic_title.configure(text="【第三步：组内个体原始曲线叠加质检】")
            self._build_quality_settings()
            self.var_qc_view.set("spaghetti_qc")
        elif norm_type == "quality":
            self.dynamic_title.configure(text="【第三步：检出率与发音人综合体检】")
            self._build_quality_settings()
        elif norm_type == "overview_heatmap":
            self.dynamic_title.configure(text="【第三步：词条级逐行质检热图要素】")
            self._build_overview_heatmap_settings()
        elif norm_type == "tone_effect_time":
            self.dynamic_title.configure(text="【第三步：二字组调类效应时间进程】")
            self._build_tone_effect_time_settings()
        elif norm_type == "formant_space":
            self.dynamic_title.configure(text="【第三步：元音声学空间图要素】")
            self._build_formant_space_settings()
        elif norm_type == "formant_trajectory":
            self.dynamic_title.configure(text="【第三步：共振峰时序轨迹图要素】")
            self._build_formant_trajectory_settings()
        elif norm_type == "formant_pillai":
            self.dynamic_title.configure(text="【第三步：元音对立重叠度与分布要素】")
            self._build_formant_pillai_settings()
        elif norm_type == "formant_density":
            self.dynamic_title.configure(text="【第三步：共振峰时空密度图要素】")
            self._build_formant_density_settings()
        elif norm_type == "formant_overview_heatmap":
            self.dynamic_title.configure(text="【第三步：共振峰词条级逐行质检热图】")
            self._build_formant_overview_heatmap_settings()

        self._apply_chart_type_defaults(norm_type)

        self._bind_left_scroll_wheel_recursive(self.dynamic_content_frame)
        self._bind_left_scroll_wheel_recursive(self.left_scroll)
        self._update_chart_group_rule_label()
        self._update_group_filters()
        self.update_preview()


    def _on_scope_changed(self, val):
        self.current_preview_page = 0
        self.current_group_page = 0
        if "整合" in val:
            self.var_export_scope.set("integrated")
            if not self._is_formant_mode():
                self.combo_scale.set("T 值 (五度标调)")
                self.combo_scale.configure(state="disabled")
        elif "分别" in val:
            self.var_export_scope.set("separate")
            self.combo_scale.configure(state="normal")
        else:
            self.var_export_scope.set("active")
            self.combo_scale.configure(state="normal")

        if hasattr(self, 'combo_overview_metric') and self.combo_overview_metric.winfo_exists():
            is_integrated = ("整合" in val)
            metric_values = self._get_tone_overview_metric_values(is_integrated=is_integrated)
            self.combo_overview_metric.configure(values=metric_values)
            if self.combo_overview_metric.get() not in metric_values:
                self.combo_overview_metric.set(metric_values[0])
            self._on_overview_metric_changed(self.combo_overview_metric.get())

        self.update_preview()


    def _on_groupby_changed(self, val):
        self.current_group_page = 0
        if "自定义字段" in str(val):
            self._open_chart_group_rule_dialog()
            return
        self._update_chart_group_rule_label()
        self.update_preview()


    def _on_intention_changed(self, val):
        if "数据诊断" in val:
            self.var_work_mode.set("数据质检与异常排查")
            self._on_work_mode_changed("数据质检与异常排查")
            return
        if self._is_formant_mode():
            if "论文主图" in val:
                selected_count = sum(1 for v in self.group_checkbox_vars.values() if v.get())
                if selected_count > 8 and messagebox and messagebox.askyesno("论文主图模式", f"当前选中了 {selected_count} 个组，论文主图建议展示 8 组以内以保证可读性。\n是否自动精选样本量最大的 8 组？", parent=self):
                    self._apply_featured_contrast_mode()
            elif "附录图册" in val:
                for var in self.group_checkbox_vars.values():
                    var.set(True)
                self._populate_groups_list()
                self.combo_format.set("PDF 文档 (.pdf)")
                self._on_group_filter_changed()
            return

        if "论文主图" in val:
            selected_count = sum(1 for v in self.group_checkbox_vars.values() if v.get())
            if selected_count > 8 and messagebox:
                if messagebox.askyesno("论文主图模式", f"当前选中了 {selected_count} 个组，论文主图建议展示 8 组以内以保证可读性。\n是否自动精选样本量最大的 8 组？", parent=self):
                    self._apply_featured_contrast_mode()
            if self.var_chart_type.get() == "contour" and hasattr(self, "combo_contour_content"):
                self.combo_contour_content.set("仅组别平均曲线")
            self.update_preview()
        elif "附录图册" in val:
            for var in self.group_checkbox_vars.values():
                var.set(True)
            self._populate_groups_list()
            self.combo_format.set("PDF 文档 (.pdf)")
            self._on_group_filter_changed()
            self.update_preview()


    def _apply_featured_contrast_mode(self):
        if not self.group_counts:
            return
        sorted_groups = sorted(self.group_counts.keys(), key=lambda g: self.group_counts[g], reverse=True)
        top_8 = set(sorted_groups[:8])
        for g, var in self.group_checkbox_vars.items():
            var.set(g in top_8)
        self._populate_groups_list()
        self._on_group_filter_changed()


    def _apply_batch_album_mode(self):
        for var in self.group_checkbox_vars.values():
            var.set(True)
        self.combo_format.set("PDF 文档 (.pdf)")
        self._populate_groups_list()
        self._on_group_filter_changed()


    def _apply_overall_overview_mode(self):
        if self._is_formant_mode():
            self.combo_type.set("共振峰组别概览图")
            self._on_type_changed("共振峰组别概览图")
            return
        self.combo_type.set("声调组别概览图")
        self._on_type_changed("声调组别概览图")


    def _on_overview_metric_changed(self, val):
        if hasattr(self, 'cb_show_deviation') and self.cb_show_deviation.winfo_exists():
            if "标准差" in val or "调类组合" in val:
                self.var_overview_show_deviation.set(False)
                self.cb_show_deviation.configure(state="disabled")
            elif "偏差" in val or "Z-score" in val:
                self.var_overview_show_deviation.set(True)
                self.cb_show_deviation.configure(state="normal")
            else:
                self.var_overview_show_deviation.set(False)
                self.cb_show_deviation.configure(state="normal")
        self.trigger_preview_update()


    def _build_overview_heatmap_settings(self):
        ctk.CTkLabel(self.dynamic_content_frame, text="热图展示维度:", font=self.font_small).pack(anchor="w", pady=(4, 2))
        is_integrated = (self.var_export_scope.get() == "integrated")
        metric_values = self._get_tone_overview_metric_values(is_integrated=is_integrated)
        self.combo_overview_metric = ctk.CTkOptionMenu(
            self.dynamic_content_frame,
            values=metric_values,
            command=self._on_overview_metric_changed,
            **self.dropdown_kwargs
        )
        self.combo_overview_metric.set(metric_values[0])
        self.combo_overview_metric.pack(fill=tk.X, pady=(2, 6))
        self._apply_custom_arrow(self.combo_overview_metric)

        self.cb_show_deviation = self._make_checkbox(
            self.dynamic_content_frame,
            text="查看与组内均值的偏差 (RdBu_r 残差热图)",
            variable=self.var_overview_show_deviation,
            command=self.trigger_preview_update,
        )
        self.cb_show_deviation.pack(anchor="w", pady=(4, 4))

        self.cb_sort_by_anomaly = self._make_checkbox(
            self.dynamic_content_frame,
            text="按异常偏离度自动排序 (异常置顶标 [!])",
            variable=self.var_sort_by_anomaly,
            command=self.trigger_preview_update,
        )
        self.cb_sort_by_anomaly.pack(anchor="w", pady=(4, 8))


    def _build_formant_overview_heatmap_settings(self):
        ctk.CTkLabel(self.dynamic_content_frame, text="热图展示维度:", font=self.font_small).pack(anchor="w", pady=(4, 2))
        is_integrated = (self.var_export_scope.get() == "integrated")
        metric_values = ["与组内均值的偏差 (Z-score / 残差热图)", "均值热图 (Mean Map)"]
        if is_integrated:
            metric_values.append("标准差热图 (SD Map)")
        self.combo_overview_metric = ctk.CTkOptionMenu(
            self.dynamic_content_frame,
            values=metric_values,
            command=self._on_overview_metric_changed,
            **self.dropdown_kwargs
        )
        self.combo_overview_metric.set(metric_values[0])
        self.combo_overview_metric.pack(fill=tk.X, pady=(2, 6))
        self._apply_custom_arrow(self.combo_overview_metric)

        ctk.CTkLabel(self.dynamic_content_frame, text="共振峰轨道模式:", font=self.font_small).pack(anchor="w", pady=(4, 2))
        self.combo_formant_overview_mode = ctk.CTkOptionMenu(
            self.dynamic_content_frame,
            values=["F1 & F2 双轨", "F2 / F1 比值"],
            command=lambda _: self.trigger_preview_update(),
            **self.dropdown_kwargs
        )
        self.combo_formant_overview_mode.set(self.var_formant_overview_mode.get())
        self.combo_formant_overview_mode.pack(fill=tk.X, pady=(2, 6))
        self._apply_custom_arrow(self.combo_formant_overview_mode)

        self.cb_show_deviation = self._make_checkbox(
            self.dynamic_content_frame,
            text="查看与元音类别均值的偏差 (Z-score)",
            variable=self.var_overview_show_deviation,
            command=self.trigger_preview_update,
        )
        self.cb_show_deviation.pack(anchor="w", pady=(4, 4))

        self.cb_sort_by_anomaly = self._make_checkbox(
            self.dynamic_content_frame,
            text="按异常偏离度自动排序 (异常置顶标 [!])",
            variable=self.var_sort_by_anomaly,
            command=self.trigger_preview_update,
        )
        self.cb_sort_by_anomaly.pack(anchor="w", pady=(4, 8))


    def _build_contour_settings(self):
        ctk.CTkLabel(self.dynamic_content_frame, text="误差区域与曲线要素:", font=self.font_small).pack(anchor="w", pady=(4, 2))
        self.combo_contour_content = ctk.CTkOptionMenu(
            self.dynamic_content_frame,
            values=[
                "平均曲线 + 置信区间阴影",
                "平均曲线 + ±1 SE (均值标准误)",
                "平均曲线 + ±1 SD (样本标准差)",
                "仅组别平均曲线",
                "平均曲线 + 个体浅色细线",
            ],
            command=lambda _: self.trigger_preview_update(),
            **self.dropdown_kwargs,
        )
        self.combo_contour_content.set(self.var_contour_content.get())
        self.combo_contour_content.pack(fill=tk.X, pady=2)
        self._apply_custom_arrow(self.combo_contour_content)

        ctk.CTkLabel(self.dynamic_content_frame, text="横轴模式:", font=self.font_small).pack(anchor="w", pady=(4, 2))
        self.combo_contour_x = ctk.CTkOptionMenu(
            self.dynamic_content_frame,
            values=["归一化采样点", "真实物理时长"],
            command=lambda _: self.trigger_preview_update(),
            **self.dropdown_kwargs,
        )
        self.combo_contour_x.set("归一化采样点")
        self.combo_contour_x.pack(fill=tk.X, pady=2)
        self._apply_custom_arrow(self.combo_contour_x)

        self.cb_chao_five_level = self._make_checkbox(
            self.dynamic_content_frame,
            text="显示右侧五度标尺与自动五度值",
            variable=self.var_show_chao_five_level,
            command=self.update_preview,
        )
        self.cb_chao_five_level.pack(anchor="w", pady=(6, 3))

        self.cb_show_spaghetti = self._make_checkbox(
            self.dynamic_content_frame,
            text="叠加个体样本细线 (Spaghetti)",
            variable=self.var_show_spaghetti,
            command=self.update_preview,
        )
        self.cb_show_spaghetti.pack(anchor="w", pady=(3, 3))

        self.cb_show_kde_bg = self._make_checkbox(
            self.dynamic_content_frame,
            text="叠加核密度热力背景 (KDE)",
            variable=self.var_show_kde_bg,
            command=self.update_preview,
        )
        self.cb_show_kde_bg.pack(anchor="w", pady=(3, 6))

        # Keep legacy combo_contour_facet synced
        self.combo_contour_facet = ctk.CTkOptionMenu(
            self.dynamic_content_frame,
            values=["单图展示 (不分面)", "按声调类型分面", "按音节位置分面"],
            command=lambda val: self._sync_facet_from_child(val),
            **self.dropdown_kwargs,
        )
        self.combo_contour_facet.set("单图展示 (不分面)")


    def _build_tone_sandhi_settings(self):
        ctk.CTkLabel(self.dynamic_content_frame, text="连读变调分析子视图:", font=self.font_small).pack(anchor="w", pady=(4, 2))
        self.combo_sandhi_subview = ctk.CTkOptionMenu(
            self.dynamic_content_frame,
            values=[
                "二字组连读变调网格图",
                "前后字调类组合特征矩阵图",
                "二字组调类效应时间进程图 (η²)",
            ],
            command=lambda _: self.trigger_preview_update(),
            **self.dropdown_kwargs,
        )
        self.combo_sandhi_subview.set("二字组连读变调网格图")
        self.combo_sandhi_subview.pack(fill=tk.X, pady=(2, 6))
        self._apply_custom_arrow(self.combo_sandhi_subview)

        ctk.CTkLabel(self.dynamic_content_frame, text="网格分面固定维度:", font=self.font_small).pack(anchor="w", pady=(4, 2))
        self.combo_sandhi_facet = ctk.CTkOptionMenu(
            self.dynamic_content_frame,
            values=["按前字调类分面", "按后字调类分面"],
            command=lambda _: self.trigger_preview_update(),
            **self.dropdown_kwargs,
        )
        self.combo_sandhi_facet.set("按前字调类分面")
        self.combo_sandhi_facet.pack(fill=tk.X, pady=(2, 8))
        self._apply_custom_arrow(self.combo_sandhi_facet)


    def _build_distribution_settings(self):
        ctk.CTkLabel(self.dynamic_content_frame, text="提取声学特征指标:", font=self.font_small).pack(anchor="w", pady=(4, 2))
        dist_metrics = [
            "全调均值",
            "起点值 (Onset)",
            "中点值 (Mid)",
            "终点值 (Offset)",
            "拐点极值",
            "调域跨度 (Range)",
            "斜率 (Slope)",
            "音节时长 (Duration ms)",
            "起-中-终三点比较",
            "测量点精细分布",
            "调域范围跨度图",
            "变异程度(CV)比较",
        ]
        self.combo_dist_type = ctk.CTkOptionMenu(
            self.dynamic_content_frame,
            values=dist_metrics,
            command=self._on_dist_type_changed,
            **self.dropdown_kwargs,
        )
        self.combo_dist_type.set(self.var_dist_type.get())
        self.combo_dist_type.pack(fill=tk.X, pady=2)
        self._apply_custom_arrow(self.combo_dist_type)
        self.combo_dist_metric = self.combo_dist_type

        self.lbl_dist_style = ctk.CTkLabel(self.dynamic_content_frame, text="分布图形态:", font=self.font_small)
        self.lbl_dist_style.pack(anchor="w", pady=(5, 2))
        self.combo_dist_style = ctk.CTkOptionMenu(
            self.dynamic_content_frame,
            values=["雨云图 (Raincloud)", "科学箱线图 (Box Plot)", "小提琴图 (Violin Plot)"],
            command=lambda _: self.trigger_preview_update(),
            **self.dropdown_kwargs,
        )
        self.combo_dist_style.set(self.var_dist_style.get())
        self.combo_dist_style.pack(fill=tk.X, pady=(2, 8))
        self._apply_custom_arrow(self.combo_dist_style)


    def _on_dist_type_changed(self, val):
        self.var_dist_type.set(val)
        if val in ["调域范围跨度图", "变异程度(CV)比较"]:
            self.combo_dist_style.configure(state="disabled")
        else:
            self.combo_dist_style.configure(state="normal")
        self.trigger_preview_update()


    def _build_density_settings(self):
        ctk.CTkLabel(self.dynamic_content_frame, text="核密度带宽 (Bandwidth):", font=self.font_small).pack(anchor="w", pady=(5, 2))
        bw_val_frame = ctk.CTkFrame(self.dynamic_content_frame, fg_color="transparent")
        bw_val_frame.pack(fill=tk.X)
        self.slider_density_bw = ctk.CTkSlider(bw_val_frame, from_=0.05, to=0.50, number_of_steps=45, command=self._on_bw_slider_changed)
        self.slider_density_bw.set(0.15)
        self.slider_density_bw.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.lbl_bw_val = ctk.CTkLabel(bw_val_frame, text="0.15", font=self.font_main, width=40)
        self.lbl_bw_val.pack(side=tk.RIGHT, padx=(5, 0))

        ctk.CTkLabel(self.dynamic_content_frame, text="截断极值处理:", font=self.font_small).pack(anchor="w", pady=(5, 2))
        self.combo_density_f0 = ctk.CTkOptionMenu(self.dynamic_content_frame, values=["分位数自动截断 (5%-95%)", "极值自动范围 (Min-Max)", "手动指定频率范围 (Hz)"], command=self._on_density_f0_changed, **self.dropdown_kwargs)
        self.combo_density_f0.set("分位数自动截断 (5%-95%)")
        self.combo_density_f0.pack(fill=tk.X, pady=2)
        self._apply_custom_arrow(self.combo_density_f0)

        self.manual_f0_frame = ctk.CTkFrame(self.dynamic_content_frame, fg_color="transparent")
        self.pct_f0_frame = ctk.CTkFrame(self.dynamic_content_frame, fg_color="transparent")

        ctk.CTkLabel(self.manual_f0_frame, text="频率 (Hz):", font=self.font_small).pack(side=tk.LEFT)
        self.entry_min_hz = ctk.CTkEntry(self.manual_f0_frame, width=55, font=self.font_small)
        self.entry_min_hz.insert(0, "75")
        self.entry_min_hz.pack(side=tk.LEFT, padx=3)
        ctk.CTkLabel(self.manual_f0_frame, text="~", font=self.font_small).pack(side=tk.LEFT)
        self.entry_max_hz = ctk.CTkEntry(self.manual_f0_frame, width=55, font=self.font_small)
        self.entry_max_hz.insert(0, "600")
        self.entry_max_hz.pack(side=tk.LEFT, padx=3)

        ctk.CTkLabel(self.pct_f0_frame, text="分位数 (%):", font=self.font_small).pack(side=tk.LEFT)
        self.entry_low_p = ctk.CTkEntry(self.pct_f0_frame, width=45, font=self.font_small)
        self.entry_low_p.insert(0, "5")
        self.entry_low_p.pack(side=tk.LEFT, padx=3)
        ctk.CTkLabel(self.pct_f0_frame, text="~", font=self.font_small).pack(side=tk.LEFT)
        self.entry_high_p = ctk.CTkEntry(self.pct_f0_frame, width=45, font=self.font_small)
        self.entry_high_p.insert(0, "95")
        self.entry_high_p.pack(side=tk.LEFT, padx=3)

        for _entry in (self.entry_min_hz, self.entry_max_hz, self.entry_low_p, self.entry_high_p):
            _entry.bind("<Return>", lambda _e: self.trigger_preview_update())
            _entry.bind("<FocusOut>", lambda _e: self.trigger_preview_update())

        self.pct_f0_frame.pack(fill=tk.X, pady=2)

        ctk.CTkLabel(self.dynamic_content_frame, text="排版分面依据:", font=self.font_small).pack(anchor="w", pady=(5, 2))
        self.combo_density_facet = ctk.CTkOptionMenu(self.dynamic_content_frame, values=["按声调类型分面", "声调类型分面 (默认)", "不分面 (混合叠加)", "按词语分面"], command=lambda val: self._sync_facet_from_child(val), **self.dropdown_kwargs)
        self.combo_density_facet.set(self.var_density_facet.get() or "按声调类型分面")
        self.combo_density_facet.pack(fill=tk.X, pady=(2, 8))
        self._apply_custom_arrow(self.combo_density_facet)


    def _on_bw_slider_changed(self, val):
        self.var_density_bw.set(float(val))
        self.lbl_bw_val.configure(text=f"{float(val):.2f}")
        self.trigger_preview_update()


    def _on_density_f0_changed(self, val):
        self.pct_f0_frame.pack_forget()
        self.manual_f0_frame.pack_forget()

        if "分位数" in val:
            self.var_density_f0_mode.set("percentile")
            self.pct_f0_frame.pack(fill=tk.X, pady=2)
        elif "手动" in val:
            self.var_density_f0_mode.set("manual")
            self.manual_f0_frame.pack(fill=tk.X, pady=2)
        else:
            self.var_density_f0_mode.set("minmax")

        self.trigger_preview_update()


    def _build_tone_effect_time_settings(self):
        text = (
            "此图使用当前勾选的二字组声调组合，估算前字调类、后字调类与交互项/残差在时间轴上的占比。\n"
            "请保留至少 2 个前字调类、2 个后字调类和 4 个调类组合。"
        )
        ctk.CTkLabel(
            self.dynamic_content_frame,
            text=text,
            font=self.font_small,
            justify="left",
            wraplength=340,
            text_color=("#374151", "#D1D5DB")
        ).pack(anchor="w", pady=(5, 10))


    def _build_quality_settings(self):
        ctk.CTkLabel(self.dynamic_content_frame, text="数据质检视图类型:", font=self.font_small).pack(anchor="w", pady=(5, 2))
        self.combo_qc_view = ctk.CTkOptionMenu(
            self.dynamic_content_frame,
            values=[
                "双联综合体检面板 (检出率+调域)",
                "组内个体原始曲线叠加图",
                "个体 Raw F0 曲线叠加 (异常高亮)",
                "有效点比例 (Active Ratio) 分布箱线图",
                "发音人基频均值与调域跨度散点图",
            ],
            command=self._on_qc_view_changed,
            **self.dropdown_kwargs,
        )
        self.combo_qc_view.set("双联综合体检面板 (检出率+调域)")
        self.combo_qc_view.pack(fill=tk.X, pady=(2, 8))
        self._apply_custom_arrow(self.combo_qc_view)
        self.var_qc_view.set("dual_check")


    def _on_qc_view_changed(self, val):
        if "双联" in val or "综合" in val:
            self.var_qc_view.set("dual_check")
        elif "组内个体" in val:
            self.var_qc_view.set("spaghetti_qc")
        elif "个体" in val:
            self.var_qc_view.set("raw_overlay")
        elif "比例" in val:
            self.var_qc_view.set("active_ratio")
        else:
            self.var_qc_view.set("speaker_means")
        self.trigger_preview_update()


    def _on_live_refresh_toggle(self):
        if self.var_live_refresh.get():
            self.update_preview()


    def trigger_preview_update(self):
        if not getattr(self, 'var_live_refresh', None) or not self.var_live_refresh.get():
            return

        if hasattr(self, '_debounce_timer_id') and self._debounce_timer_id is not None:
            try:
                self.after_cancel(self._debounce_timer_id)
            except Exception:
                pass
            self._debounce_timer_id = None

        self._debounce_timer_id = self.after(260, self._debounced_update_preview)


    def _debounced_update_preview(self):
        self._debounce_timer_id = None
        self.update_preview()


    def _snapshot_render_params(self):
        keys = [
            'work_mode', 'preset_theme', 'chart_type', 'export_scope',
            'color_by', 'groupby', 'facet_by', 'chart_group_rule',
            'scale', 'format', 'layout_size', 'dpi', 'show_title',
            'contour_x', 'contour_content', 'contour_facet',
            'show_chao_five_level_axis', 'show_spaghetti', 'show_kde_bg',
            'sandhi_subview', 'sandhi_facet_by',
            'dist_type', 'dist_metric', 'dist_style',
            'density_bw', 'density_f0_mode', 'density_facet',
            'density_normalization', 'density_p_low', 'density_p_high',
            'density_m_min', 'density_m_max', 'density_max_points',
            'formant_density_overlay', 'formant_density_bw', 'formant_density_facet',
            'formant_density_show_raw', 'formant_density_show_contours',
            'formant_ellipse', 'formant_label_mode', 'formant_show_raw', 'formant_time_gradient',
            'show_convex_hull', 'show_diphthong_arrows',
            'formant_normalization', 'formant_axis_lock', 'formant_overview_mode',
            'formant_traj_style', 'show_f3', 'formant_syllable_filter',
            'pillai_subview',
            'qc_view', 'overview_metric', 'overview_show_deviation', 'sort_by_anomaly',
            'legend_loc', 'legend_outside', 'intention',
            'image_ratio_mode', 'image_ratio_custom', 'image_pixel_mode', 'image_pixel_custom',
            'high_precision',
        ]
        snapshot = {}
        for key in keys:
            val = self.get_param(key)
            if val is not None:
                snapshot[key] = val
        return snapshot


    def _cancel_preview_render(self):
        if self._preview_cancel_event is not None:
            self._preview_cancel_event.set()


    def _bind_canvas_hover_metadata(self, canvas, fig):
        hover_meta = getattr(fig, "_qc_hover_metadata", None)
        if not hover_meta or not hasattr(self, "lbl_qc_hover_status"):
            return

        def _on_motion(event):
            item, col_idx = self._resolve_qc_hover_item(fig, event)
            if item is None:
                return
            vals = item.get("values") or []
            zs = item.get("z_scores") or []
            c_idx = min(max(0, col_idx), max(0, len(vals) - 1))
            v_str = f"{vals[c_idx]:.2f}" if (0 <= c_idx < len(vals) and vals[c_idx] is not None and np.isfinite(vals[c_idx])) else "NaN"
            z_str = f"{zs[c_idx]:+.2f}σ" if (0 <= c_idx < len(zs) and zs[c_idx] is not None and np.isfinite(zs[c_idx])) else "--"
            is_anom = bool(item.get("is_anomaly") or item.get("is_outlier"))
            flag = "[!异常] " if is_anom else ""
            rmsd_val = float(item.get("rmsd", item.get("rms_z", 0.0)) or 0.0)
            loc_hint = " (双击定位到工程树词条)" if (item.get("item_id") or item.get("label")) else ""
            msg = (
                f"🔍 {flag}{item.get('speaker_name', '')} | 词条: {item.get('label', '')} ({item.get('group', '')}) "
                f"| 采样点#{c_idx+1}: {v_str} (偏差 {z_str}, RMSD={rmsd_val:.2f}){loc_hint}"
            )
            try:
                self.lbl_qc_hover_status.configure(text=msg, text_color=("#DC2626" if is_anom else "#1E40AF"))
                if hasattr(self.lbl_qc_hover_status, "grid"):
                    self.lbl_qc_hover_status.grid(row=4, column=0, sticky="ew", padx=4, pady=(0, 3))
            except Exception:
                pass

        def _on_button_press(event):
            if not getattr(event, "dblclick", False):
                return
            item, _col_idx = self._resolve_qc_hover_item(fig, event)
            if item is None:
                return
            ok = self.locate_qc_item_in_project_tree(
                item.get("item_id"),
                speaker_name=item.get("speaker_name"),
                label=item.get("label"),
            )
            if ok:
                try:
                    self.lbl_qc_hover_status.configure(
                        text=f"✅ 已在工程树中定位词条: {item.get('speaker_name', '')} | {item.get('label', '')} (#{item.get('item_id', '')})",
                        text_color="#059669",
                    )
                    if hasattr(self.lbl_qc_hover_status, "grid"):
                        self.lbl_qc_hover_status.grid(row=4, column=0, sticky="ew", padx=4, pady=(0, 3))
                except Exception:
                    pass

        try:
            canvas.mpl_connect("motion_notify_event", _on_motion)
            canvas.mpl_connect("button_press_event", _on_button_press)
        except Exception:
            pass


    def _poll_preview_render(self, generation):
        if self._preview_queue is None:
            return

        done_payload = None
        while True:
            try:
                msg_type, payload = self._preview_queue.get_nowait()
            except queue.Empty:
                break
            if msg_type == "progress":
                message = payload.get("message")
                if message and hasattr(self, "preview_lbl"):
                    self.preview_lbl.configure(text=message, text_color="#6B7280")
                progress = payload.get("progress")
                if progress is not None and hasattr(self, "preview_progress"):
                    self.preview_progress.set(max(0.0, min(1.0, float(progress))))
            elif msg_type == "done":
                done_payload = payload

        if done_payload is None:
            self._preview_poll_job = self.after(80, lambda: self._poll_preview_render(generation))
            return

        if generation != self._preview_generation:
            fig = done_payload.get("fig")
            if fig is not None:
                plt.close(fig)
            return

        self._preview_poll_job = None
        self._preview_worker = None
        self._preview_queue = None
        self._preview_cancel_event = None

        status = done_payload.get("status")
        if status == "ok":
            fig = done_payload["fig"]
            for widget in self.preview_container.winfo_children():
                if widget != getattr(self, 'btn_toggle_minimize', None):
                    widget.destroy()
            canvas = FigureCanvasTkAgg(fig, master=self.preview_container)
            canvas.draw()
            canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
            self._bind_chart_context_menu(canvas.get_tk_widget())
            self._bind_canvas_hover_metadata(canvas, fig)
            if getattr(self, 'btn_toggle_minimize', None):
                self.btn_toggle_minimize.lift()
            if getattr(self, "active_figure", None) is not None and self.active_figure is not fig:
                try:
                    plt.close(self.active_figure)
                except Exception:
                    pass
            self.active_figure = fig
        elif status == "cancelled":
            if hasattr(self, "preview_lbl"):
                self.preview_lbl.configure(text="已取消本次预览渲染", text_color="#6B7280")
        else:
            self.group_pagination_frame.grid_forget()
            if hasattr(self, "preview_lbl"):
                self.preview_lbl.configure(text=f"图表渲染发生错误: {done_payload.get('message', '')[:35]}", text_color="#EF4444")


    def update_preview(self):
        if getattr(self, 'sm', None):
            active_spk = self.sm.get_active_speaker()
            if active_spk:
                self.active_speaker = active_spk

        if hasattr(self, '_debounce_timer_id') and self._debounce_timer_id is not None:
            try:
                self.after_cancel(self._debounce_timer_id)
            except Exception:
                pass
            self._debounce_timer_id = None

        self._preview_generation += 1
        generation = self._preview_generation
        self._cancel_preview_render()
        if self._preview_poll_job is not None:
            try:
                self.after_cancel(self._preview_poll_job)
            except Exception:
                pass
            self._preview_poll_job = None

        for widget in self.preview_container.winfo_children():
            if widget != getattr(self, 'btn_toggle_minimize', None):
                widget.destroy()

        if getattr(self, 'btn_toggle_minimize', None):
            self.btn_toggle_minimize.lift()

        preview_status = ctk.CTkFrame(self.preview_container, fg_color="transparent")
        preview_status.grid(row=0, column=0)
        self.preview_lbl = ctk.CTkLabel(preview_status, text="正在实时渲染图表，请稍候...", font=self.font_title, text_color="#6B7280")
        self.preview_lbl.pack(pady=(0, 8))
        self.preview_progress = ctk.CTkProgressBar(
            preview_status,
            width=220,
            height=8,
            corner_radius=4,
            fg_color=("#E5E7EB", "#374151"),
            progress_color=self.ui_primary,
        )
        self.preview_progress.set(0.05)
        self.preview_progress.pack(pady=(0, 10))
        self.btn_cancel_preview = ctk.CTkButton(
            preview_status,
            text="取消预览",
            width=96,
            height=30,
            corner_radius=999,
            font=self.font_small,
            fg_color=("#F3F4F6", "#374151"),
            text_color=("#374151", "#E5E7EB"),
            hover_color=("#E5E7EB", "#4B5563"),
            border_width=1,
            border_color=("#D1D5DB", "#4B5563"),
            command=self._cancel_preview_render,
        )
        self.btn_cancel_preview.pack()

        self._bind_chart_context_menu(preview_status)

        scope = self.var_export_scope.get()
        if scope == "separate" and len(self.all_speakers) > 1:
            pady_val = (0, 4) if getattr(self, 'is_minimized', False) else (0, 6)
            self.pagination_frame.grid(row=2, column=0, pady=pady_val)
            idx = getattr(self, 'current_preview_page', 0)
            if idx < 0 or idx >= len(self.all_speakers):
                idx = 0
                self.current_preview_page = 0
            spk_name = self.all_speakers[idx].name
            if getattr(self, 'is_minimized', False):
                self.lbl_page_info.configure(text=f"{idx+1}/{len(self.all_speakers)}", font=self.font_small)
                self.btn_prev_page.configure(text="◀", width=40, height=26, font=self.font_small)
                self.btn_next_page.configure(text="▶", width=40, height=26, font=self.font_small)
            else:
                self.lbl_page_info.configure(text=f"发音人: {spk_name} ({idx+1}/{len(self.all_speakers)})", font=self.font_title)
                self.btn_prev_page.configure(text="◀ 上一个发音人", width=120, height=28, font=self.font_main)
                self.btn_next_page.configure(text="下一个发音人 ▶", width=120, height=28, font=self.font_main)
        else:
            self.pagination_frame.grid_forget()

        self.update_idletasks()

        try:
            data = self._get_current_data_entries()
            if not data:
                analysis_mode = self.project_tree.app_state_params.get('analysis_mode', 'f0')
                msg = "❌ 没有检索到有效共振峰数据，请先完成共振峰分析。" if analysis_mode == 'formant' else "❌ 没有检索到有效基频曲线，请导入有发音数据的项目！"
                self.preview_lbl.configure(text=msg, text_color="#EF4444")
                self.group_pagination_frame.grid_forget()
                self.truncation_banner_frame.pack_forget()
                return

            groupby = self.combo_groupby.get()
            chart_type = self.var_chart_type.get()
            pagination_state = self._get_group_pagination_state(data, chart_type, groupby)
            total_groups = pagination_state['total_groups']
            total_pages = pagination_state['total_pages']
            is_paginated_heatmap = pagination_state['is_paginated_heatmap']
            show_group_pagination = (
                (
                    total_groups > 8
                    and not self._is_overview_heatmap_chart(chart_type)
                    and chart_type not in ("tone_effect_time", "tone_sandhi", "formant_pillai")
                )
                or (is_paginated_heatmap and total_pages > 1)
            )
            if show_group_pagination:
                pady_val = (0, 4) if getattr(self, 'is_minimized', False) else (0, 6)
                self.group_pagination_frame.grid(row=3, column=0, pady=pady_val)
                if self.current_group_page < 0:
                    self.current_group_page = 0
                elif self.current_group_page >= total_pages:
                    self.current_group_page = max(0, total_pages - 1)

                if getattr(self, 'is_minimized', False):
                    self.lbl_group_page_info.configure(text=f"{self.current_group_page+1}/{total_pages}", font=self.font_small)
                    self.btn_prev_group_page.configure(text="◀", width=40, height=26, font=self.font_small)
                    self.btn_next_group_page.configure(text="▶", width=40, height=26, font=self.font_small)
                else:
                    if is_paginated_heatmap:
                        current_chunk = pagination_state['pages'][self.current_group_page] if self.current_group_page < len(pagination_state['pages']) else []
                        if current_chunk and isinstance(current_chunk[0], dict):
                            chunk_groups = self._ordered_unique([r['group'] for r in current_chunk])
                            first_g = chunk_groups[0] if chunk_groups else ""
                            last_g = chunk_groups[-1] if chunk_groups else ""
                            g_range = first_g if first_g == last_g else f"{first_g} ~ {last_g}"
                            self.lbl_group_page_info.configure(
                                text=f"热图页码: {self.current_group_page+1}/{total_pages} (当前显示: {g_range}，共 {len(current_chunk)} 个词条)",
                                font=ctk.CTkFont(family="Microsoft YaHei", size=12, weight="bold")
                            )
                        else:
                            first_g = str(current_chunk[0]) if current_chunk else ""
                            last_g = str(current_chunk[-1]) if current_chunk else ""
                            g_range = first_g if first_g == last_g else f"{first_g} ~ {last_g}"
                            self.lbl_group_page_info.configure(
                                text=f"热图页码: {self.current_group_page+1}/{total_pages} (当前显示: {g_range}，共 {len(current_chunk)} 个组别)",
                                font=ctk.CTkFont(family="Microsoft YaHei", size=12, weight="bold")
                            )
                    else:
                        self.lbl_group_page_info.configure(
                            text=f"组别页码: {self.current_group_page+1}/{total_pages} (当前显示第 {self.current_group_page*8+1}~{min(total_groups, (self.current_group_page+1)*8)} 组，共 {total_groups} 组)",
                            font=ctk.CTkFont(family="Microsoft YaHei", size=12, weight="bold")
                        )
                    self.btn_prev_group_page.configure(text="◀ 上一页", width=88, height=28, font=self.font_main)
                    self.btn_next_group_page.configure(text="下一页 ▶", width=88, height=28, font=self.font_main)

                if not getattr(self, 'is_minimized', False):
                    if is_paginated_heatmap:
                        current_chunk = pagination_state['pages'][self.current_group_page] if self.current_group_page < len(pagination_state['pages']) else []
                        if current_chunk and isinstance(current_chunk[0], dict):
                            chunk_groups = self._ordered_unique([r['group'] for r in current_chunk])
                            first_g = chunk_groups[0] if chunk_groups else ""
                            last_g = chunk_groups[-1] if chunk_groups else ""
                            g_range = first_g if first_g == last_g else f"{first_g} ~ {last_g}"
                            banner_msg = f" ℹ 热图自动分页: 第 {self.current_group_page+1}/{total_pages} 页 ({g_range}, {len(current_chunk)}词)。导出时完整输出。"
                        else:
                            first_g = str(current_chunk[0]) if current_chunk else ""
                            last_g = str(current_chunk[-1]) if current_chunk else ""
                            g_range = first_g if first_g == last_g else f"{first_g} ~ {last_g}"
                            banner_msg = f" ℹ 热图自动分页: 第 {self.current_group_page+1}/{total_pages} 页 ({g_range}, {len(current_chunk)}组)。导出时完整输出。"
                    else:
                        s_g = self.current_group_page * 8 + 1
                        e_g = min(total_groups, (self.current_group_page + 1) * 8)
                        banner_msg = f" ℹ 共 {total_groups} 组分 {total_pages} 页: 预览第 {self.current_group_page+1} 页 ({s_g}~{e_g}组)。导出时完整输出。"
                    self.lbl_truncation_banner.configure(text=banner_msg)
                    self.truncation_banner_frame.pack(side=tk.LEFT, padx=(12, 0), fill=tk.X, expand=True)
                else:
                    self.truncation_banner_frame.pack_forget()
            else:
                self.group_pagination_frame.grid_forget()
                self.truncation_banner_frame.pack_forget()

            params_snapshot = self._snapshot_render_params()
            rule_summary = self._format_chart_group_rule_summary(params_snapshot.get("chart_group_rule"))
            rule_label = rule_summary.lstrip("按").strip()
            if rule_label in ("", "默认组"):
                rule_label = "默认组别"
            cancel_event = threading.Event()
            progress_queue = queue.Queue()
            self._preview_cancel_event = cancel_event
            self._preview_queue = progress_queue

            def worker():
                try:
                    self._set_export_runtime(
                        progress_callback=lambda p=None, msg=None: progress_queue.put(("progress", {"progress": p, "message": msg})),
                        cancel_event=cancel_event,
                        params=params_snapshot,
                    )
                    with _MATPLOTLIB_LOCK:
                        self._check_export_cancelled()
                        progress_queue.put(("progress", {"progress": 0.10, "message": f"正在按{rule_label}准备图表数据..."}))
                        fig = self.generate_plot(data, is_preview=True)
                        self._check_export_cancelled()
                    progress_queue.put(("progress", {"progress": 0.95, "message": "正在生成预览画布..."}))
                    progress_queue.put(("done", {"status": "ok", "fig": fig}))
                except ExportCancelled:
                    progress_queue.put(("done", {"status": "cancelled"}))
                except Exception as e:
                    progress_queue.put(("done", {"status": "error", "message": str(e)}))
                    import logging
                    logging.getLogger(__name__).error(f"Render chart error: {e}", exc_info=True)
                finally:
                    self._clear_export_runtime()

            self._preview_worker = threading.Thread(target=worker, daemon=True)
            self._preview_worker.start()
            self._poll_preview_render(generation)
        except Exception as e:
            self.group_pagination_frame.grid_forget()
            self.preview_lbl.configure(text=f"图表渲染发生错误: {str(e)[:35]}", text_color="#EF4444")
            import logging
            logging.getLogger(__name__).error(f"Render chart error: {e}", exc_info=True)


    def _copy_png_bytes_to_system_clipboard(self, png_bytes):
        try:
            import io
            import ctypes
            from PIL import Image

            img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
            bmp_buf = io.BytesIO()
            img.save(bmp_buf, format="BMP")
            dib_data = bmp_buf.getvalue()[14:]

            GMEM_MOVEABLE = 0x0002
            CF_DIB = 8
            kernel32 = ctypes.windll.kernel32
            user32 = ctypes.windll.user32

            kernel32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
            kernel32.GlobalAlloc.restype = ctypes.c_void_p
            kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
            kernel32.GlobalLock.restype = ctypes.c_void_p
            kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
            user32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
            user32.SetClipboardData.restype = ctypes.c_void_p

            if not user32.OpenClipboard(0):
                return False
            try:
                user32.EmptyClipboard()
                h_mem = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(dib_data))
                if not h_mem:
                    return False
                p_mem = kernel32.GlobalLock(h_mem)
                if not p_mem:
                    return False
                ctypes.memmove(p_mem, dib_data, len(dib_data))
                kernel32.GlobalUnlock(h_mem)
                if not user32.SetClipboardData(CF_DIB, h_mem):
                    return False
                return True
            finally:
                user32.CloseClipboard()
        except Exception:
            return False


    def copy_figure_to_clipboard(self):
        fig = getattr(self, "active_figure", None)
        if fig is None:
            if messagebox:
                messagebox.showinfo("提示", "当前暂无可复制的预览图表。", parent=self)
            return False
        import io
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=300, bbox_inches="tight")
        png_bytes = buf.getvalue()
        self._clipboard_png_bytes = png_bytes
        self._copy_png_bytes_to_system_clipboard(png_bytes)

        if hasattr(self, "btn_copy_image"):
            try:
                if getattr(self, "_copy_btn_restore_job", None) is not None:
                    self.after_cancel(self._copy_btn_restore_job)
            except Exception:
                pass
            try:
                self.btn_copy_image.configure(text="✅ 已复制!", fg_color="#10B981", hover_color="#059669")
                def _restore_copy_btn():
                    self._copy_btn_restore_job = None
                    try:
                        if self.btn_copy_image.winfo_exists():
                            self.btn_copy_image.configure(text="📋 复制图片", fg_color="#6366F1", hover_color="#4F46E5")
                    except Exception:
                        pass
                self._copy_btn_restore_job = self.after(1800, _restore_copy_btn)
            except Exception:
                pass

        if hasattr(self, "lbl_qc_hover_status"):
            msg = f"✅ 已复制 300 DPI 高清图表至系统剪贴板 ({len(png_bytes) // 1024} KB)，可直接 Ctrl+V 粘贴"
            self.lbl_qc_hover_status.configure(text=msg, text_color="#059669")
            try:
                if hasattr(self.lbl_qc_hover_status, "grid"):
                    self.lbl_qc_hover_status.grid(row=4, column=0, sticky="ew", padx=4, pady=(0, 3))
                if getattr(self, "_copy_status_hide_job", None) is not None:
                    self.after_cancel(self._copy_status_hide_job)
                def _hide_copy_status():
                    self._copy_status_hide_job = None
                    try:
                        if getattr(self, "work_mode", "publication") != "qc" and self.lbl_qc_hover_status.winfo_exists():
                            self.lbl_qc_hover_status.grid_remove()
                    except Exception:
                        pass
                if hasattr(self, "after"):
                    self._copy_status_hide_job = self.after(2800, _hide_copy_status)
            except Exception:
                pass
        return True


    def export_chart_table_data(self):
        data = self._get_current_data_entries()
        if not data:
            if messagebox:
                messagebox.showwarning("提示", "当前没有可导出的有效声学数据。", parent=self)
            return
        if filedialog is None:
            return
        out_csv = filedialog.asksaveasfilename(
            title="导出当前图表统计数据表",
            defaultextension=".csv",
            initialfile="acoustic_chart_data.csv",
            filetypes=[("CSV 表格", "*.csv")],
        )
        if not out_csv:
            return
        import csv
        with open(out_csv, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["发音人", "组别", "词条", "时长(s)", "有效检出率"])
            for e in data:
                writer.writerow([
                    e.get("speaker_name", ""),
                    e.get("group", ""),
                    e.get("label", ""),
                    f"{float(e.get('total_dur', 0.0)):.4f}",
                    f"{float(e.get('active_ratio', 1.0)):.4f}",
                ])
        if messagebox:
            messagebox.showinfo("导出成功", f"图表关联数据已导出至:\n{out_csv}", parent=self)


    def _on_close_request(self):
        if self._export_worker is not None and self._export_worker.is_alive():
            if messagebox.askyesno("导出进行中", "导出尚未完成，是否取消导出并关闭窗口？", parent=self):
                self._cancel_export_job()
            return
        self._cancel_preview_render()
        if self._preview_poll_job is not None:
            try:
                self.after_cancel(self._preview_poll_job)
            except Exception:
                pass
            self._preview_poll_job = None
        self.destroy()


    def destroy(self):
        self._cancel_preview_render()
        if hasattr(self, '_debounce_timer_id') and self._debounce_timer_id is not None:
            try:
                self.after_cancel(self._debounce_timer_id)
            except Exception:
                pass
            self._debounce_timer_id = None
        if self._preview_poll_job is not None:
            try:
                self.after_cancel(self._preview_poll_job)
            except Exception:
                pass
            self._preview_poll_job = None
        if self.app and getattr(self.app, 'active_chart_dialog', None) == self:
            try:
                self.app.active_chart_dialog = None
            except Exception:
                pass
        ctk.CTkToplevel.destroy(self)


    def manual_refresh_preview(self):
        if getattr(self, 'sm', None):
            active_spk = self.sm.get_active_speaker()
            if active_spk:
                self.active_speaker = active_spk
        self._force_live_extract = True
        try:
            self._update_group_filters()
            self.update_preview()
        finally:
            self._force_live_extract = False


    def _update_group_filters(self):
        base_entries = self._extract_active_data(
            self.all_speakers if self.all_speakers else [self.active_speaker],
            filter_groups=False,
            apply_chart_grouping=False,
        )
        self._all_base_group_filter_entries = base_entries
        all_entries = self._extract_active_data(self.all_speakers if self.all_speakers else [self.active_speaker], filter_groups=False)
        self._all_group_filter_entries = all_entries
        new_group_counts = {}
        for entry in all_entries:
            g = entry['group']
            new_group_counts[g] = new_group_counts.get(g, 0) + 1

        new_available_groups = sorted(list(new_group_counts.keys()))
        new_group_checkbox_vars = {}
        for g in new_available_groups:
            if hasattr(self, 'group_checkbox_vars') and g in self.group_checkbox_vars:
                new_group_checkbox_vars[g] = self.group_checkbox_vars[g]
            else:
                new_group_checkbox_vars[g] = ctk.BooleanVar(value=True)

        self.group_counts = new_group_counts
        self.available_groups = new_available_groups
        self.group_checkbox_vars = new_group_checkbox_vars

        self._populate_groups_list()
        if hasattr(self, "combo_type") and not self._is_formant_mode():
            type_values = self._get_tone_chart_type_values(base_entries, work_mode=self.var_work_mode.get() if hasattr(self, "var_work_mode") else None)
            self.combo_type.configure(values=type_values)
        if hasattr(self, "combo_overview_metric") and not self._is_formant_mode():
            try:
                exists = self.combo_overview_metric.winfo_exists()
            except Exception:
                exists = True
            if exists:
                is_integrated = self.var_export_scope.get() == "integrated"
                metric_values = self._get_tone_overview_metric_values(is_integrated=is_integrated, data_entries=all_entries)
                self.combo_overview_metric.configure(values=metric_values)
                if self.combo_overview_metric.get() not in metric_values:
                    self.combo_overview_metric.set(metric_values[0])
                self._on_overview_metric_changed(self.combo_overview_metric.get())


    def _on_format_changed(self, val):
        self._update_pixel_options_state()
        self.update_preview()


    def _on_ratio_mode_changed(self, val):
        if hasattr(self, "var_image_ratio_mode"):
            self.var_image_ratio_mode.set(val)
        if val == "自定义":
            self.slider_ratio_custom.configure(state="normal")
            if hasattr(self, "entry_ratio_custom"):
                self.entry_ratio_custom.configure(state="normal")
            if hasattr(self, "custom_ratio_inline_frame"):
                try:
                    self.custom_ratio_inline_frame.pack(side=tk.LEFT, after=self.combo_ratio_mode, padx=(0, 6))
                except Exception:
                    self.custom_ratio_inline_frame.pack(side=tk.LEFT, padx=(0, 6))
        else:
            if hasattr(self, "custom_ratio_inline_frame"):
                self.custom_ratio_inline_frame.pack_forget()
            self.slider_ratio_custom.configure(state="disabled")
        self.on_preview_wrapper_configure()
        self.update_preview()


    def _on_pixel_mode_changed(self, val):
        if val == "自定义":
            self.entry_pixel_custom.configure(state="normal")
        else:
            self.entry_pixel_custom.configure(state="disabled")
        self.update_preview()


    def _on_ratio_slider_change(self, val):
        r = self._parse_custom_ratio_value(val, default=1.5)
        self.var_image_ratio_custom.set(r)
        if hasattr(self, "lbl_ratio_val"):
            self.lbl_ratio_val.configure(text=f"{r:.2f}")
        if hasattr(self, "entry_ratio_custom"):
            try:
                self.entry_ratio_custom.delete(0, tk.END)
                self.entry_ratio_custom.insert(0, f"{r:.2f}")
            except Exception:
                pass
        self.on_preview_wrapper_configure()
        self.trigger_preview_update()


    def _on_custom_ratio_entry_commit(self, event=None):
        if not hasattr(self, "entry_ratio_custom"):
            return
        raw = self.entry_ratio_custom.get()
        r = self._parse_custom_ratio_value(raw, default=float(self.var_image_ratio_custom.get() or 1.5))
        self.var_image_ratio_custom.set(r)
        if hasattr(self, "lbl_ratio_val"):
            self.lbl_ratio_val.configure(text=f"{r:.2f}")
        try:
            self.entry_ratio_custom.delete(0, tk.END)
            self.entry_ratio_custom.insert(0, f"{r:.2f}")
        except Exception:
            pass
        self.on_preview_wrapper_configure()
        self.update_preview()


    def on_preview_wrapper_configure(self, event=None):
        if not hasattr(self, 'preview_wrapper') or not hasattr(self, 'preview_container'):
            return
        w_avail = self.preview_wrapper.winfo_width()
        h_avail = self.preview_wrapper.winfo_height()
        if w_avail < 50 or h_avail < 50:
            return

        ratio_mode = self.get_param('image_ratio_mode', '默认')
        custom_ratio = self._parse_custom_ratio_value(self.get_param('image_ratio_custom', 1.5), default=1.5)
        try:
            custom_ratio_round = round(float(custom_ratio), 3)
        except (TypeError, ValueError):
            custom_ratio_round = 1.5
        last_cfg = getattr(self, '_last_preview_wrapper_cfg', None)
        if last_cfg is not None:
            last_w, last_h, last_mode, last_r = last_cfg
            if abs(w_avail - last_w) < 6 and abs(h_avail - last_h) < 6 and ratio_mode == last_mode and custom_ratio_round == last_r:
                return
        self._last_preview_wrapper_cfg = (w_avail, h_avail, ratio_mode, custom_ratio_round)

        if ratio_mode == "默认":
            self.preview_container.place_forget()
            self.preview_container.configure(width=0, height=0)
            self.preview_container.place(
                relx=0.0,
                rely=0.0,
                x=0,
                y=0,
                relwidth=1.0,
                relheight=1.0,
                anchor="nw",
            )
            self.trigger_preview_update()
            return

        ratio_map = {
            "4:3": 4.0 / 3.0,
            "16:9": 16.0 / 9.0,
            "3:2": 3.0 / 2.0,
            "1:1": 1.0,
            "16:10": 16.0 / 10.0,
            "2:1": 2.0
        }
        R = ratio_map.get(ratio_mode, custom_ratio)

        if w_avail / h_avail >= R:
            h_target = h_avail
            w_target = h_avail * R
        else:
            w_target = w_avail
            h_target = w_avail / R

        self.preview_container.place_forget()
        self.preview_container.configure(width=int(w_target), height=int(h_target))
        self.preview_container.place(
            relx=0.5, rely=0.5, anchor="center"
        )
        self.trigger_preview_update()


    def _update_pixel_options_state(self):
        fmt = self.combo_format.get()
        is_vector = "svg" in fmt.lower() or "pdf" in fmt.lower()
        if is_vector:
            self.combo_pixel_mode.configure(state="disabled")
            self.entry_pixel_custom.configure(state="disabled")
            if hasattr(self, "combo_dpi"):
                self.combo_dpi.configure(state="disabled")
        else:
            self.combo_pixel_mode.configure(state="normal")
            if hasattr(self, "combo_dpi"):
                self.combo_dpi.configure(state="normal")
            if self.combo_pixel_mode.get() == "自定义":
                self.entry_pixel_custom.configure(state="normal")
            else:
                self.entry_pixel_custom.configure(state="disabled")


    def _destroy_export_progress_window(self):
        if self._export_poll_job is not None:
            try:
                self.after_cancel(self._export_poll_job)
            except Exception:
                pass
            self._export_poll_job = None
        if self._export_progress_window is not None:
            try:
                self._export_progress_window.destroy()
            except Exception:
                pass
            self._export_progress_window = None


    def _cancel_export_job(self):
        if self._export_cancel_event is not None:
            self._export_cancel_event.set()


    def _poll_export_updates(self):
        if self._export_progress_queue is None:
            return

        done_payload = None
        while True:
            try:
                msg_type, payload = self._export_progress_queue.get_nowait()
            except queue.Empty:
                break

            if msg_type == "progress" and self._export_progress_window is not None:
                bar = getattr(self._export_progress_window, "_pbar", None)
                label = getattr(self._export_progress_window, "_status_lbl", None)
                if bar is not None and payload.get("progress") is not None:
                    bar.set(payload["progress"])
                if label is not None and payload.get("message"):
                    label.configure(text=payload["message"])
            elif msg_type == "done":
                done_payload = payload

        if done_payload is not None:
            self._destroy_export_progress_window()
            self.btn_export.configure(state="normal")
            self._export_worker = None
            self._export_progress_queue = None
            self._export_cancel_event = None
            status = done_payload.get("status")
            if status == "ok":
                messagebox.showinfo("成功", done_payload.get("message", "导出完成。"), parent=self)
            elif status == "cancelled":
                messagebox.showinfo("已取消", "导出已取消。", parent=self)
            else:
                messagebox.showerror("错误", done_payload.get("message", "导出失败。"), parent=self)
            return

        self._export_poll_job = self.after(100, self._poll_export_updates)


    def _start_async_export(self, jobs, success_message, params_snapshot=None):
        if not jobs:
            return

        self.btn_export.configure(state="disabled")
        self._export_cancel_event = threading.Event()
        self._export_progress_queue = queue.Queue()

        prog = ctk.CTkToplevel(self)
        prog.title("PhonTracer语音学标注与声学分析系统 V1.0 - 导出进行中")
        prog.geometry("380x150")
        prog.resizable(False, False)
        prog.transient(self)
        prog.attributes('-topmost', True)
        prog.protocol("WM_DELETE_WINDOW", self._cancel_export_job)

        lbl = ctk.CTkLabel(prog, text="正在准备导出...", font=self.font_main)
        lbl.pack(pady=(20, 8))
        pbar = ctk.CTkProgressBar(
            prog,
            width=320,
            height=8,
            corner_radius=4,
            fg_color=("#E5E7EB", "#374151"),
            progress_color=self.ui_primary,
        )
        pbar.pack(pady=(0, 10))
        pbar.set(0)
        self.btn_cancel_export = ctk.CTkButton(
            prog,
            text="取消导出",
            width=100,
            height=30,
            corner_radius=999,
            font=self.font_small,
            fg_color=("#F3F4F6", "#374151"),
            text_color=("#374151", "#E5E7EB"),
            hover_color=("#E5E7EB", "#4B5563"),
            border_width=1,
            border_color=("#D1D5DB", "#4B5563"),
            command=self._cancel_export_job,
        )
        self.btn_cancel_export.pack()

        prog._status_lbl = lbl
        prog._pbar = pbar
        self._export_progress_window = prog

        total_jobs = len(jobs)

        def worker():
            try:
                for idx, job in enumerate(jobs):
                    if self._export_cancel_event.is_set():
                        raise ExportCancelled("导出已取消")
                    speaker_name = job.get("speaker_name", "当前发音人")
                    out_path = job["out_path"]
                    ext = job["ext"]
                    data = job["data"]

                    def job_progress(local_progress, local_message=None, _idx=idx, _speaker=speaker_name):
                        lp = 0.0 if local_progress is None else max(0.0, min(1.0, float(local_progress)))
                        global_progress = (_idx + lp) / total_jobs
                        message = local_message or f"正在导出 {_speaker} ({_idx + 1}/{total_jobs})..."
                        self._export_progress_queue.put(("progress", {"progress": global_progress, "message": message}))

                    self._export_progress_queue.put(("progress", {"progress": idx / total_jobs, "message": f"正在导出 {speaker_name} ({idx + 1}/{total_jobs})..."}))
                    self._set_export_runtime(progress_callback=job_progress, cancel_event=self._export_cancel_event, params=params_snapshot)
                    self._export_dataset(data, out_path, ext)
                    self._clear_export_runtime()
                    self._export_progress_queue.put(("progress", {"progress": (idx + 1) / total_jobs, "message": f"{speaker_name} 导出完成 ({idx + 1}/{total_jobs})"}))

                self._export_progress_queue.put(("done", {"status": "ok", "message": success_message}))
            except ExportCancelled:
                self._export_progress_queue.put(("done", {"status": "cancelled"}))
            except Exception as e:
                self._export_progress_queue.put(("done", {"status": "error", "message": f"图表导出失败: {e}"}))
            finally:
                self._clear_export_runtime()

        self._export_worker = threading.Thread(target=worker, daemon=True)
        self._export_worker.start()
        self._poll_export_updates()


    def on_confirm(self):
        if self._export_worker is not None and self._export_worker.is_alive():
            return messagebox.showwarning("提示", "已有导出任务正在进行，请稍候或先取消。", parent=self)

        if getattr(self, 'sm', None):
            active_spk = self.sm.get_active_speaker()
            if active_spk:
                self.active_speaker = active_spk

        scope = self.var_export_scope.get()
        fmt = self.combo_format.get()
        ext = ".png"
        if "svg" in fmt.lower():
            ext = ".svg"
        elif "pdf" in fmt.lower():
            ext = ".pdf"
        params_snapshot = self._snapshot_render_params()

        if scope == "separate" and len(self.all_speakers) > 1:
            out_dir = filedialog.askdirectory(title="选择声学图表导出文件夹")
            if not out_dir:
                return

            jobs = []
            for speaker in self.all_speakers:
                data = self._extract_active_data([speaker])
                if not data:
                    continue
                name_suffix = "共振峰可视化图表" if self._is_formant_mode() else "声调可视化图表"
                out_path = os.path.join(out_dir, f"{speaker.name}_{name_suffix}{ext}")
                jobs.append({
                    "speaker_name": speaker.name,
                    "data": data,
                    "out_path": out_path,
                    "ext": ext
                })
            if not jobs:
                if self._is_formant_mode():
                    return messagebox.showwarning("提示", "没有可导出的有效共振峰数据。", parent=self)
                return messagebox.showwarning("提示", "没有可导出的有效基频数据。", parent=self)

            self._start_async_export(jobs, f"批量图表成功导出至:\n{out_dir}", params_snapshot=params_snapshot)
            return

        if self._is_formant_mode():
            default_name = "formant_integrated_charts" if scope == "integrated" else "formant_charts"
        else:
            default_name = "tone_integrated_acoustic_charts" if scope == "integrated" else "tone_acoustic_charts"
        out_file = filedialog.asksaveasfilename(
            title="导出图表",
            defaultextension=ext,
            initialfile=default_name,
            filetypes=[("图像文件", f"*{ext}")]
        )
        if not out_file:
            return

        data = self._get_current_data_entries()
        if not data:
            if self._is_formant_mode():
                return messagebox.showwarning("提示", "没有有效共振峰曲线，无法导出！", parent=self)
            return messagebox.showwarning("提示", "没有有效基频曲线，无法导出！", parent=self)

        jobs = [{
            "speaker_name": "当前任务",
            "data": data,
            "out_path": out_file,
            "ext": ext
        }]
        self._start_async_export(jobs, f"图表已成功保存至:\n{out_file}", params_snapshot=params_snapshot)


    def toggle_minimize(self):
        if not hasattr(self, 'is_minimized'):
            self.is_minimized = False

        if not self.is_minimized:
            self.saved_geometry = self.geometry()
            if hasattr(self, 'top_mode_bar'):
                self.top_mode_bar.grid_remove()
            self.left_scroll.grid_remove()
            self.grid_columnconfigure(0, minsize=0)
            if hasattr(self, 'preview_top_bar'):
                self.preview_top_bar.grid_remove()
            if hasattr(self, 'btn_restore'):
                self.btn_restore.pack(side=tk.LEFT, padx=3)
            self.attributes("-topmost", True)
            self.minsize(300, 200)
            self.geometry("450x380")
            self.is_minimized = True
            self.update_preview()
        else:
            self.attributes("-topmost", False)
            self.minsize(800, 500)
            if hasattr(self, 'top_mode_bar'):
                self.top_mode_bar.grid(row=0, column=0, columnspan=2, sticky="ew", padx=15, pady=(10, 4))
            self.left_scroll.grid(row=1, column=0, sticky="nsew", padx=(15, 8), pady=(4, 12))
            self.grid_columnconfigure(0, weight=0, minsize=400)
            if hasattr(self, 'btn_restore'):
                self.btn_restore.pack_forget()
            if hasattr(self, 'preview_top_bar'):
                self.preview_top_bar.grid(row=0, column=0, sticky="ew", pady=(0, 4))
            if hasattr(self, 'saved_geometry') and self.saved_geometry:
                self.geometry(self.saved_geometry)
            else:
                self.geometry("1040x680")
            self.is_minimized = False
            self.update_preview()


    def _on_arrow_left(self):
        if hasattr(self, 'group_pagination_frame') and self.group_pagination_frame.winfo_ismapped():
            self._prev_group_page()
        elif hasattr(self, 'pagination_frame') and self.pagination_frame.winfo_ismapped():
            self._prev_page()


    def _on_arrow_right(self):
        if hasattr(self, 'group_pagination_frame') and self.group_pagination_frame.winfo_ismapped():
            self._next_group_page()
        elif hasattr(self, 'pagination_frame') and self.pagination_frame.winfo_ismapped():
            self._next_page()


    def _on_arrow_up(self):
        if hasattr(self, 'pagination_frame') and self.pagination_frame.winfo_ismapped():
            self._prev_page()


    def _on_arrow_down(self):
        if hasattr(self, 'pagination_frame') and self.pagination_frame.winfo_ismapped():
            self._next_page()


    def invalidate_cache(self):
        if hasattr(self, '_speaker_data_cache'):
            self._speaker_data_cache.clear()
        try:
            self._update_group_filters()
        except Exception:
            pass


    def _show_context_menu(self, event):
        from modules.ui_widgets import make_context_menu, post_context_menu

        menu = make_context_menu(self, font_size=15)
        menu.add_command(label="刷新预览", command=self.manual_refresh_preview)

        has_preview_thread = (
            getattr(self, '_preview_worker', None) is not None and
            self._preview_worker.is_alive()
        )
        cancel_state = "normal" if has_preview_thread else "disabled"
        menu.add_command(label="取消本次预览", command=self._cancel_preview_render, state=cancel_state)

        minimize_label = "还原预览" if getattr(self, 'is_minimized', False) else "悬浮模式"
        menu.add_command(label=minimize_label, command=self.toggle_minimize)

        is_exporting = (
            getattr(self, '_export_worker', None) is not None and
            self._export_worker.is_alive()
        )
        export_state = "disabled" if is_exporting else "normal"
        menu.add_command(label="复制图片到剪贴板", command=self.copy_figure_to_clipboard)
        menu.add_command(label="导出图表...", command=self.on_confirm, state=export_state)
        menu.add_command(label="导出图表数据表...", command=self.export_chart_table_data)

        menu.add_separator()
        menu.add_checkbutton(label="实时刷新", variable=self.var_live_refresh, command=self._on_live_refresh_toggle)
        menu.add_checkbutton(label="高渲染精细度", variable=self.var_high_precision, command=self.update_preview)

        show_speaker = hasattr(self, 'pagination_frame') and self.pagination_frame.winfo_ismapped()
        show_group = hasattr(self, 'group_pagination_frame') and self.group_pagination_frame.winfo_ismapped()

        if show_speaker or show_group:
            menu.add_separator()
            if show_speaker:
                menu.add_command(label="上一个发音人", command=self._prev_page)
                menu.add_command(label="下一个发音人", command=self._next_page)
            if show_group:
                menu.add_command(label="上一组别页", command=self._prev_group_page)
                menu.add_command(label="下一组别页", command=self._next_group_page)

        post_context_menu(menu, event)
        return "break"


    def _bind_chart_context_menu(self, widget):
        if not getattr(widget, "_phontracer_ctx_bound", False):
            widget._phontracer_ctx_bound = True
            widget.bind("<Button-2>", self._show_context_menu, add="+")
            widget.bind("<Button-3>", self._show_context_menu, add="+")
        internal_children = {
            getattr(widget, attr, None)
            for attr in ("_canvas", "_text_label", "_image_label")
        }
        try:
            for child in widget.winfo_children():
                if child in internal_children:
                    continue
                if child != getattr(self, 'btn_toggle_minimize', None):
                    self._bind_chart_context_menu(child)
        except Exception:
            pass


    # Compatibility alias
    export_charts = _start_async_export
