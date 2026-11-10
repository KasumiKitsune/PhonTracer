"""
modules.toolkit_ui.tab_script_workbench_mixin: Tab 6 自定义脚本工作台上半部（布局、参数面板、脚本树、沙箱运行、工程补丁回写等） Mixin。
"""
import os
import sys
import json
import re
import copy
import shutil
import tempfile
import datetime
import zipfile
import subprocess
import platform
import threading
import traceback
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import customtkinter as ctk
import numpy as np
from PIL import Image, ImageTk

from modules.core.paths import get_asset_path
from modules.ui_widgets import bind_text_context_menu, _apply_custom_arrow
from modules.project_manager import read_project_metadata_from_archive
from modules.scripting.meta_parser import (
    SCRIPT_EDITOR_DEFAULT_DESC,
    SCRIPT_EDITOR_DEFAULT_NAME,
    extract_error_line_number,
    parse_script_metadata_comments,
    strip_markdown_code_fence,
)
from modules.toolkit_ui.tk_utils import start_safe_thread
from modules.toolkit_ui.script_list_widget import CTkScriptList
from modules.toolkit_ui.dialog_script_editor import ScriptEditorDialog
from modules.toolkit_ui.dialog_ai_prompt import AIPromptDialog


class ScriptWorkbenchMixin:
    def build_script_tab(self):
        # 初始化私有变量与交互式工作台状态
        self.project_data = None
        self.project_namelist = []
        self.selected_script_id = None
        self.is_script_running = False
        self.run_cancel_event = None
        self.local_scripts = []
        self.script_output_dir = None
        self.script_figure_results = []
        self.previous_run_figure_results = []
        self.current_script_figure_index = 0
        self.script_table_results = []
        self.current_script_table_index = 0
        self.pending_project_patch = None
        self.last_script_run_record = None
        self.last_error_line_no = None
        self.active_script_editor_dialog = None
        self.current_script_params_schema = []
        self.script_param_widgets = {}
        self.code_drawer_expanded = False
        self.log_pane_collapsed = False
        self.active_artifact_mode = "figure"
        self.active_artifact_key = ("figure", 0)
        self.canvas_zoom_scale = 1.0
        self.canvas_fit_mode = True
        self.canvas_pan_offset_x = 0.0
        self.canvas_pan_offset_y = 0.0
        self.canvas_split_compare_mode = False
        self.current_canvas_pil_image = None
        self.compare_canvas_pil_image = None
        self._canvas_tk_img = None
        self._canvas_compare_tk_img = None
        self._canvas_resize_after_id = None
        self._canvas_drag_start_pos = None
        self._table_sort_state = {}
        self._clipboard_image_buffer = None
        self.var_auto_archive_script_run = tk.BooleanVar(value=False)
        self.var_auto_apply_patch = tk.BooleanVar(value=False)

        # 生成白色的 play 和 pause 图标
        for icon_key, filename in [("play_white", "play.png"), ("pause_white", "pause.png")]:
            try:
                icon_path = get_asset_path("icons", filename)
                if os.path.exists(icon_path):
                    from PIL import Image
                    img = Image.open(icon_path).convert("RGBA")
                    data = np.array(img)
                    data[:, :, 0] = 255
                    data[:, :, 1] = 255
                    data[:, :, 2] = 255
                    img_white = Image.fromarray(data)
                    self.icons[icon_key] = ctk.CTkImage(light_image=img_white, dark_image=img_white, size=(20, 20))
            except Exception:
                pass

        content = ctk.CTkFrame(self.tab_script, fg_color="transparent")
        content.pack(fill=tk.BOTH, expand=True, padx=16, pady=12)
        content.grid_columnconfigure(0, weight=0)
        content.grid_columnconfigure(1, weight=1)
        content.grid_rowconfigure(0, weight=1)

        # ----------------------------------------------------
        # 左栏：脚本列表与管理
        # ----------------------------------------------------
        left_panel = self._make_card(content, width=285)
        left_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        left_panel.pack_propagate(False)

        self._section_header(left_panel, "脚本库", subtitle=None, icon_text="05")

        # 搜索框
        search_frame = ctk.CTkFrame(left_panel, fg_color="transparent")
        search_frame.pack(fill=tk.X, padx=12, pady=(0, 4))
        self.entry_script_search = ctk.CTkEntry(
            search_frame,
            placeholder_text="搜索脚本...",
            height=34,
            corner_radius=17,
            font=ctk.CTkFont(family="Microsoft YaHei", size=12),
            fg_color=("#F9FAFB", "#262930"),
            border_color=("#D1D5DB", "#374151"),
            text_color=("#111827", "#F9FAFB")
        )
        self.entry_script_search.pack(fill=tk.X)
        self.entry_script_search.bind("<KeyRelease>", lambda e: self.load_scripts_to_tree())

        # 类型过滤
        self.combo_script_type = ctk.CTkOptionMenu(
            left_panel,
            values=["全部类型", "图表脚本", "数据处理脚本"],
            command=self.on_script_filter_changed,
            fg_color=("#F3F4F6", "#374151"),
            text_color=("#1F2937", "#E5E7EB"),
            button_color=("#F3F4F6", "#374151"),
            button_hover_color=("#E5E7EB", "#4B5563"),
            height=30,
            corner_radius=15
        )
        self.combo_script_type.pack(fill=tk.X, padx=12, pady=4)
        _apply_custom_arrow(self.combo_script_type)

        # 列表框
        tree_frame = ctk.CTkFrame(left_panel, fg_color="transparent")
        tree_frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=4)

        self.script_tree = CTkScriptList(tree_frame, font_family=self.font_family, colors=self.colors)
        self.script_tree.pack(fill=tk.BOTH, expand=True)
        self.script_tree.bind("<<TreeviewSelect>>", self.on_script_selected)
        self.script_tree.set_context_callback(self.show_script_context_menu)

        # 常用操作按钮
        act_frame1 = ctk.CTkFrame(left_panel, fg_color="transparent")
        act_frame1.pack(fill=tk.X, padx=12, pady=4)
        self._make_button(act_frame1, "新建", self.on_new_script, tone="primary", height=30).pack(
            side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 4)
        )
        self._make_button(act_frame1, "编辑", self.on_edit_script, tone="secondary", height=30).pack(
            side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 4)
        )
        self._make_button(
            act_frame1,
            "删除",
            self.on_delete_script,
            tone="danger",
            height=30,
            fg_color=self.colors["danger_soft"],
            hover_color="#FECACA",
            text_color=self.colors["danger_hover"],
        ).pack(side=tk.LEFT, expand=True, fill=tk.X)

        act_frame2 = ctk.CTkFrame(left_panel, fg_color="transparent")
        act_frame2.pack(fill=tk.X, padx=12, pady=(4, 14))
        self._make_button(act_frame2, "导入脚本", self.on_import_script, tone="secondary", height=30).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 4))
        self._make_button(act_frame2, "导出脚本", self.on_export_script, tone="secondary", height=30).pack(side=tk.LEFT, expand=True, fill=tk.X)

        # ----------------------------------------------------
        # 右栏：紧凑控制条 + 动态参数 + 交互式预览工作台 (占 >75% 空间)
        # ----------------------------------------------------
        right_panel = self._make_card(content)
        right_panel.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        self.script_right_panel = right_panel

        # 1. 顶部紧凑工具条（整合脚本名称、类型标签、独立按钮行、描述、工程状态与切换）
        top_bar = ctk.CTkFrame(
            right_panel,
            fg_color=self.colors["surface_soft"],
            corner_radius=10,
            border_width=1,
            border_color=self.colors["border"],
        )
        top_bar.pack(fill=tk.X, padx=14, pady=(10, 4))
        top_bar.grid_columnconfigure(0, weight=3)
        top_bar.grid_columnconfigure(1, weight=2, minsize=270)
        self.script_top_bar = top_bar

        script_meta_box = ctk.CTkFrame(top_bar, fg_color="transparent")
        script_meta_box.grid(row=0, column=0, sticky="nsew", padx=(12, 8), pady=6)

        # 第一行：脚本标题 + 类型徽章
        title_line = ctk.CTkFrame(script_meta_box, fg_color="transparent")
        title_line.pack(fill=tk.X)

        self.lbl_selected_script_name = ctk.CTkLabel(
            title_line,
            text="当前脚本：未选择",
            font=ctk.CTkFont(family=self.font_family, size=13, weight="bold"),
            text_color=self.colors["text"],
            anchor="w",
        )
        self.lbl_selected_script_name.pack(side=tk.LEFT, padx=(0, 6))

        self.lbl_selected_script_type_tag = ctk.CTkLabel(
            title_line,
            text="图表脚本",
            font=ctk.CTkFont(family=self.font_family, size=11, weight="bold"),
            fg_color=self.colors["primary_soft"],
            text_color=self.colors["primary"],
            corner_radius=8,
            padx=8,
            pady=1,
        )
        self.lbl_selected_script_type_tag.pack(side=tk.LEFT, padx=(0, 6))

        # 第二行：独立操作按钮行（▸ 源码预览 + ✨ AI提示词 另起一行，不与标题同行）
        meta_action_line = ctk.CTkFrame(script_meta_box, fg_color="transparent")
        meta_action_line.pack(fill=tk.X, pady=(4, 2))
        self.script_meta_action_line = meta_action_line

        self.btn_toggle_code_drawer = ctk.CTkButton(
            meta_action_line,
            text="▸ 源码预览",
            width=88,
            height=24,
            corner_radius=8,
            font=ctk.CTkFont(family=self.font_family, size=11),
            fg_color="#FFFFFF",
            border_width=1,
            border_color=self.colors["border"],
            text_color=self.colors["text_soft"],
            hover_color="#F1F5F9",
            command=self.toggle_code_preview_drawer,
        )
        self.btn_toggle_code_drawer.pack(side=tk.LEFT, padx=(0, 6))

        # 保留实例属性兼容外部访问，不在顶栏重复 pack（左栏底部已有 [编辑]）
        self.btn_quick_edit_script = ctk.CTkButton(
            meta_action_line,
            text="编辑代码",
            width=72,
            height=24,
            corner_radius=8,
            font=ctk.CTkFont(family=self.font_family, size=11),
            fg_color=self.colors["purple_soft"],
            text_color=self.colors["purple"],
            hover_color=self.colors["border_strong"],
            command=self.on_edit_script,
        )

        self.btn_copy_prompt = ctk.CTkButton(
            meta_action_line,
            text="✨ AI提示词",
            width=88,
            height=24,
            corner_radius=8,
            font=ctk.CTkFont(family=self.font_family, size=11),
            fg_color=self.colors["primary_soft"],
            text_color=self.colors["primary"],
            hover_color=self.colors["border_strong"],
            command=self.show_prompt_dialog,
        )
        self.btn_copy_prompt.pack(side=tk.LEFT)

        # 第三行：脚本功能描述（DPI 自适应换行，绝不截断）
        self.lbl_selected_script_desc = ctk.CTkLabel(
            script_meta_box,
            text="描述：请从左侧列表中选择一个脚本，或者点击“新建”创建新脚本。",
            font=self.font_caption,
            text_color=self.colors["muted"],
            anchor="w",
            justify="left",
            wraplength=360,
        )
        self.lbl_selected_script_desc.pack(fill=tk.X, pady=(2, 0))
        self._bind_adaptive_wrap(
            self.lbl_selected_script_desc,
            script_meta_box,
            reserved_width=16,
            min_wrap=180,
            extra_containers=(top_bar,),
        )

        # 右侧紧凑工程卡片（左文字状态自适应换行 + 右固定宽度切换按钮，杜绝挤压）
        project_card = ctk.CTkFrame(top_bar, fg_color="transparent")
        project_card.grid(row=0, column=1, sticky="nsew", padx=(6, 12), pady=6)
        project_card.grid_columnconfigure(0, weight=1)
        project_card.grid_columnconfigure(1, weight=0)

        proj_info_box = ctk.CTkFrame(project_card, fg_color="transparent")
        proj_info_box.grid(row=0, column=0, sticky="ew", padx=(0, 8))

        self.lbl_script_project_status = ctk.CTkLabel(
            proj_info_box,
            text="未加载工程",
            font=ctk.CTkFont(family=self.font_family, size=12, weight="bold"),
            text_color=self.colors["muted"],
            anchor="w",
            justify="left",
            wraplength=220,
        )
        self.lbl_script_project_status.pack(fill=tk.X, anchor="w")
        self._bind_adaptive_wrap(
            self.lbl_script_project_status,
            proj_info_box,
            reserved_width=10,
            min_wrap=150,
            extra_containers=(top_bar,),
        )

        self.lbl_script_project_detail = ctk.CTkLabel(
            proj_info_box,
            text="可在此处或“工程预览”页选择同一个 .teproj 工程。",
            font=self.font_caption,
            text_color=self.colors["muted"],
            anchor="w",
            justify="left",
            wraplength=220,
        )
        self.lbl_script_project_detail.pack(fill=tk.X, pady=(1, 0), anchor="w")
        self._bind_adaptive_wrap(
            self.lbl_script_project_detail,
            proj_info_box,
            reserved_width=10,
            min_wrap=150,
            extra_containers=(top_bar,),
        )

        # 保留实例属性兼容外部访问，不在顶栏重复 pack（顶部 Tab 栏已有 [工程预览]）
        self.btn_script_show_project = self._make_button(
            project_card,
            "查看工程预览",
            lambda: self.tabview.set(self.tab_project_name),
            tone="secondary",
            height=26,
            width=92,
        )

        self.btn_script_load_project = self._make_button(
            project_card,
            "选择工程",
            self.select_project_file,
            tone="primary",
            height=30,
            width=96,
        )
        self.btn_script_load_project.grid(row=0, column=1, sticky="e")

        # 1b. 默认收起的可折叠源码预览抽屉（保留 self.txt_script_code_preview 引用）
        self.code_preview_drawer = ctk.CTkFrame(
            right_panel,
            fg_color=self.colors["surface_soft"],
            corner_radius=8,
            border_width=1,
            border_color=self.colors["border"],
        )
        self.txt_script_code_preview = ctk.CTkTextbox(
            self.code_preview_drawer,
            height=110,
            font=ctk.CTkFont(family="Consolas", size=11),
            wrap="none",
            border_width=1,
            border_color=("#D1D5DB", "#374151"),
            fg_color=("#FFFFFF", "#262930"),
            text_color=("#111827", "#F9FAFB")
        )
        self.txt_script_code_preview.pack(fill=tk.X, padx=10, pady=6)
        self.txt_script_code_preview.configure(state="disabled")
        bind_text_context_menu(
            self.txt_script_code_preview,
            readonly=True,
            extra_items=[("编辑脚本代码...", self.on_edit_script)],
        )
        # 默认收起抽屉
        self.code_preview_drawer.pack_forget()

        def speed_up_preview_scroll(event):
            scroll_units = int(-1 * (event.delta / 120) * 4)
            self.txt_script_code_preview.yview_scroll(scroll_units, "units")
            return "break"
        self.txt_script_code_preview.bind("<MouseWheel>", speed_up_preview_scroll)
        if hasattr(self.txt_script_code_preview, "_textbox"):
            self.txt_script_code_preview._textbox.bind("<MouseWheel>", speed_up_preview_scroll)

        # 2. 运行控制栏 & 动态 PARAMS 参数控件栏 & 工程补丁按钮
        self.script_control_bar = ctk.CTkFrame(right_panel, fg_color="transparent")
        self.script_control_bar.pack(fill=tk.X, padx=14, pady=(2, 4))

        self.btn_run_script = self._make_button(
            self.script_control_bar, " 运行脚本", self.run_current_script,
            tone="primary", image=self.icons.get("play_white"), height=32, width=96
        )
        self.btn_run_script.pack(side=tk.LEFT, padx=(0, 6))

        self.btn_stop_script = self._make_button(
            self.script_control_bar, " 停止", self.stop_current_script,
            tone="danger", image=self.icons.get("pause_white"), height=32, width=72
        )
        self.btn_stop_script.pack(side=tk.LEFT, padx=(0, 6))
        self.btn_stop_script.configure(state="disabled")

        self.btn_open_script_output = self._make_button(
            self.script_control_bar, " 打开目录", self.open_script_output_dir,
            tone="secondary", image=self.icons.get("folder_dark"), height=32, width=88
        )
        self.btn_open_script_output.pack(side=tk.LEFT, padx=(0, 8))
        self.btn_open_script_output.configure(state="disabled")

        # 工程补丁操作条（仅在选中数据处理脚本或返回 ProjectPatchResult 时显示）
        self.project_patch_bar = ctk.CTkFrame(self.script_control_bar, fg_color="transparent")

        self.btn_apply_project_patch = ctk.CTkButton(
            self.project_patch_bar,
            text="应用补丁并另存工程 (.teproj)",
            height=30,
            corner_radius=15,
            font=ctk.CTkFont(family=self.font_family, size=12, weight="bold"),
            fg_color="#059669",
            hover_color="#047857",
            text_color="#FFFFFF",
            state="disabled",
            command=self.apply_pending_project_patch,
        )
        self.btn_apply_project_patch.pack(side=tk.LEFT, padx=(0, 8))

        self.chk_auto_apply_patch = self._make_checkbox(
            self.project_patch_bar,
            text="自动应用补丁",
            variable=self.var_auto_apply_patch,
            width=96,
        )
        self.chk_auto_apply_patch.pack(side=tk.LEFT)
        self.project_patch_bar.pack_forget()

        # 动态 PARAMS 参数行（选中含 PARAMS 的脚本时自动生成）
        self.script_params_frame = ctk.CTkFrame(
            right_panel,
            fg_color=self.colors["surface_soft"],
            corner_radius=8,
            border_width=1,
            border_color=self.colors["border"],
        )

        # 3. 输出预览工作台 & 运行日志区（使用 tk.PanedWindow 将 75%+ 空间交给预览工作台）
        output_frame = ctk.CTkFrame(right_panel, fg_color="transparent")
        output_frame.pack(fill=tk.BOTH, expand=True, padx=14, pady=(2, 12))
        self.script_output_frame = output_frame

        paned_bg = "#E5E7EB" if ctk.get_appearance_mode().lower() != "dark" else "#374151"
        self.script_workspace_paned = tk.PanedWindow(
            output_frame,
            orient=tk.HORIZONTAL,
            sashwidth=6,
            sashrelief=tk.FLAT,
            bg=paned_bg,
            bd=0,
            opaqueresize=True,
        )
        self.script_workspace_paned.pack(fill=tk.BOTH, expand=True)

        # 左侧主预览工作台（占 75% 以上空间）
        preview_box = ctk.CTkFrame(
            self.script_workspace_paned,
            fg_color=self.colors["surface_soft"],
            corner_radius=10,
            border_width=1,
            border_color=self.colors["border"],
        )
        self.script_preview_box = preview_box
        preview_box.grid_rowconfigure(2, weight=1)
        preview_box.grid_columnconfigure(0, weight=1)

        # Row 0: 多产物胶囊标签栏 + 翻页导航 + 收起/展开日志按钮
        top_artifact_bar = ctk.CTkFrame(preview_box, fg_color="transparent", height=28)
        top_artifact_bar.grid(row=0, column=0, sticky="ew", padx=8, pady=(6, 2))
        top_artifact_bar.grid_columnconfigure(0, weight=1)

        self.artifact_tabs_frame = ctk.CTkFrame(top_artifact_bar, fg_color="transparent", height=28)
        self.artifact_tabs_frame.grid(row=0, column=0, sticky="w")
        self.artifact_tabs_frame.grid_remove()
        self.artifact_tab_buttons = []

        # 兼容保留 script_preview_nav / btn_script_prev_fig / lbl_script_fig_page / btn_script_next_fig
        self.script_preview_nav = ctk.CTkFrame(top_artifact_bar, fg_color="transparent", height=28)
        self.script_preview_nav.grid(row=0, column=1, sticky="e", padx=(6, 6))
        self.btn_script_prev_fig = ctk.CTkButton(
            self.script_preview_nav, text="上一张", width=62, height=24,
            corner_radius=8, fg_color="#FFFFFF", border_width=1,
            border_color=self.colors["border"], text_color=self.colors["text_soft"],
            hover_color="#F1F5F9", command=self.show_prev_script_figure
        )
        self.btn_script_prev_fig.grid(row=0, column=0, padx=(0, 4))
        self.lbl_script_fig_page = ctk.CTkLabel(
            self.script_preview_nav, text="图表: 0/0", font=self.font_caption,
            text_color=self.colors["muted"], anchor="center"
        )
        self.lbl_script_fig_page.grid(row=0, column=1, padx=4)
        self.btn_script_next_fig = ctk.CTkButton(
            self.script_preview_nav, text="下一张", width=62, height=24,
            corner_radius=8, fg_color="#FFFFFF", border_width=1,
            border_color=self.colors["border"], text_color=self.colors["text_soft"],
            hover_color="#F1F5F9", command=self.show_next_script_figure
        )
        self.btn_script_next_fig.grid(row=0, column=2, padx=(4, 0))
        self.script_preview_nav.grid_remove()

        self.btn_toggle_log_pane = ctk.CTkButton(
            top_artifact_bar,
            text="收起日志 ▸",
            width=82,
            height=26,
            corner_radius=8,
            font=ctk.CTkFont(family=self.font_family, size=11),
            fg_color="#FFFFFF",
            border_width=1,
            border_color=self.colors["border"],
            text_color=self.colors["text_soft"],
            hover_color="#F1F5F9",
            command=self.toggle_script_log_pane,
        )
        self.btn_toggle_log_pane.grid(row=0, column=2, sticky="e")

        # Row 1: 画布交互工具栏（仅在 figure 模式下显示，空状态/占位态自动隐藏）
        self.canvas_toolbar = ctk.CTkFrame(preview_box, fg_color="transparent", height=30)
        self.canvas_toolbar.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 4))

        def _make_tb_btn(text, cmd, width=68):
            b = ctk.CTkButton(
                self.canvas_toolbar,
                text=text,
                width=width,
                height=26,
                corner_radius=8,
                font=ctk.CTkFont(family=self.font_family, size=11),
                fg_color="#FFFFFF",
                border_width=1,
                border_color=self.colors["border"],
                text_color=self.colors["text_soft"],
                hover_color="#F1F5F9",
                command=cmd,
            )
            b.pack(side=tk.LEFT, padx=(0, 4))
            return b

        self.btn_canvas_fit = _make_tb_btn("适应窗口", self.canvas_zoom_fit, width=68)
        self.btn_canvas_original = _make_tb_btn("1:1 原始", self.canvas_zoom_original, width=68)
        self.btn_canvas_zoom_in = _make_tb_btn("放大+", self.canvas_zoom_in, width=54)
        self.btn_canvas_zoom_out = _make_tb_btn("缩小-", self.canvas_zoom_out, width=54)
        self.lbl_canvas_zoom = ctk.CTkLabel(
            self.canvas_toolbar,
            text="100%",
            font=ctk.CTkFont(family="Consolas", size=11, weight="bold"),
            text_color=self.colors["muted"],
            width=52,
        )
        self.lbl_canvas_zoom.pack(side=tk.LEFT, padx=(2, 6))

        self.btn_canvas_split_compare = _make_tb_btn("双图对比", self.toggle_canvas_split_compare, width=72)
        self.btn_canvas_copy_img = _make_tb_btn("复制图片", self.copy_current_chart_to_clipboard, width=72)
        self.btn_canvas_save_as = _make_tb_btn("另存为...", self.save_current_chart_as, width=72)

        # Row 2: 预览视口容器（容纳交互式画布、内置表格预览器、文件列表与占位文本）
        self.preview_viewport = ctk.CTkFrame(preview_box, fg_color="transparent")
        self.preview_viewport.grid(row=2, column=0, sticky="nsew", padx=8, pady=(0, 8))
        self.preview_viewport.grid_rowconfigure(0, weight=1)
        self.preview_viewport.grid_columnconfigure(0, weight=1)

        # 交互式高清预览画布 (tk.Canvas)
        self.canvas_chart_preview = tk.Canvas(
            self.preview_viewport,
            bg="#F8FAFC",
            highlightthickness=1,
            highlightbackground="#E2E8F0",
            cursor="fleur",
        )
        self.canvas_chart_preview.grid(row=0, column=0, sticky="nsew")
        self.canvas_chart_preview.bind("<Configure>", self._on_canvas_configure)
        self.canvas_chart_preview.bind("<Enter>", lambda _e: self.canvas_chart_preview.focus_set())
        self.canvas_chart_preview.bind("<MouseWheel>", self.on_canvas_mousewheel)
        self.canvas_chart_preview.bind("<Button-4>", self.on_canvas_mousewheel)
        self.canvas_chart_preview.bind("<Button-5>", self.on_canvas_mousewheel)
        self.canvas_chart_preview.bind("<ButtonPress-1>", self.on_canvas_drag_start)
        self.canvas_chart_preview.bind("<B1-Motion>", self.on_canvas_drag_move)
        self.canvas_chart_preview.bind("<Double-Button-1>", self.on_canvas_double_click)
        self.canvas_chart_preview.bind("<Button-3>", self.show_canvas_context_menu)
        self.canvas_chart_preview.bind("<Button-2>", self.show_canvas_context_menu)
        self.canvas_chart_preview.bind("<Control-c>", lambda _e: self.copy_current_chart_to_clipboard())
        self.canvas_chart_preview.bind("<Control-C>", lambda _e: self.copy_current_chart_to_clipboard())

        # 保留 self.lbl_chart_preview 兼容无头测试与纯文本占位（不再绑定单击弹出系统外部看图器）
        self.lbl_chart_preview = ctk.CTkLabel(
            self.preview_viewport,
            text="暂无图表预览\n点击上方 [运行脚本] 生成图表或数据表预览",
            font=ctk.CTkFont(family=self.font_family, size=13),
            text_color=self.colors["muted"],
            justify="center",
        )
        self.lbl_chart_preview.grid(row=0, column=0, sticky="nsew")

        # 内置表格预览器（ttk.Treeview）
        self.table_preview_container = ctk.CTkFrame(self.preview_viewport, fg_color="transparent")
        self.table_preview_container.grid(row=0, column=0, sticky="nsew")
        self.table_preview_container.grid_rowconfigure(1, weight=1)
        self.table_preview_container.grid_columnconfigure(0, weight=1)

        table_bar = ctk.CTkFrame(self.table_preview_container, fg_color="transparent")
        table_bar.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 4))

        self.lbl_table_summary = ctk.CTkLabel(
            table_bar,
            text="数据表预览 (点击表头可按列排序)",
            font=ctk.CTkFont(family=self.font_family, size=11, weight="bold"),
            text_color=self.colors["muted"],
        )
        self.lbl_table_summary.pack(side=tk.LEFT)

        self.btn_save_table_as = ctk.CTkButton(
            table_bar,
            text="另存为 CSV/XLSX",
            width=116,
            height=25,
            corner_radius=12,
            font=ctk.CTkFont(family=self.font_family, size=11),
            fg_color=self.colors["primary_soft"],
            text_color=self.colors["primary"],
            hover_color=self.colors["border_strong"],
            command=self.save_current_table_as,
        )
        self.btn_save_table_as.pack(side=tk.RIGHT, padx=(4, 0))

        self.btn_copy_table_tsv = ctk.CTkButton(
            table_bar,
            text="复制全表 (TSV)",
            width=108,
            height=25,
            corner_radius=12,
            font=ctk.CTkFont(family=self.font_family, size=11),
            fg_color=self.colors["success_soft"],
            text_color=self.colors["success"],
            hover_color=self.colors["border_strong"],
            command=self.copy_current_table_tsv_to_clipboard,
        )
        self.btn_copy_table_tsv.pack(side=tk.RIGHT)

        self.script_table_tree = ttk.Treeview(self.table_preview_container, show="headings", selectmode="extended")
        self.script_table_tree.grid(row=1, column=0, sticky="nsew")
        tbl_vsb = ttk.Scrollbar(self.table_preview_container, orient="vertical", command=self.script_table_tree.yview)
        tbl_vsb.grid(row=1, column=1, sticky="ns")
        tbl_hsb = ttk.Scrollbar(self.table_preview_container, orient="horizontal", command=self.script_table_tree.xview)
        tbl_hsb.grid(row=2, column=0, sticky="ew")
        self.script_table_tree.configure(yscrollcommand=tbl_vsb.set, xscrollcommand=tbl_hsb.set)
        self.table_preview_container.grid_remove()

        # 生成文件列表的滚动区域（默认隐藏）
        self.script_file_list_frame = ctk.CTkScrollableFrame(
            self.preview_viewport,
            fg_color="transparent",
            label_text="已生成的文件列表 (点击打开文件夹)",
            label_font=ctk.CTkFont(family=self.font_family, size=11, weight="bold"),
            label_text_color=self.colors["muted"]
        )
        self.script_file_list_frame.grid(row=0, column=0, sticky="nsew")
        self.script_file_list_frame.grid_remove()

        if hasattr(self.script_file_list_frame, "_label") and self.script_file_list_frame._label:
            self.script_file_list_frame._label.configure(cursor="hand2")
            self.script_file_list_frame._label.bind("<Button-1>", lambda e: self.open_script_output_dir())
            if hasattr(self.script_file_list_frame._label, "_label") and self.script_file_list_frame._label._label:
                self.script_file_list_frame._label._label.configure(cursor="hand2")
                self.script_file_list_frame._label._label.bind("<Button-1>", lambda e: self.open_script_output_dir())

        # 右侧运行日志与归档分栏
        log_box = ctk.CTkFrame(
            self.script_workspace_paned,
            fg_color=self.colors["surface_soft"],
            corner_radius=10,
            border_width=1,
            border_color=self.colors["border"],
        )
        self.script_log_box = log_box
        log_box.grid_rowconfigure(1, weight=1)
        log_box.grid_columnconfigure(0, weight=1)

        log_header_frame = ctk.CTkFrame(log_box, fg_color="transparent")
        log_header_frame.grid(row=0, column=0, sticky="ew", padx=8, pady=(6, 2))

        log_title_lbl = ctk.CTkLabel(
            log_header_frame, text="运行日志:",
            font=ctk.CTkFont(family=self.font_family, size=11, weight="bold"),
            text_color=self.colors["muted"]
        )
        log_title_lbl.pack(side=tk.LEFT, anchor="w")

        self.btn_copy_log = ctk.CTkButton(
            log_header_frame,
            text="复制",
            width=48,
            height=22,
            font=ctk.CTkFont(family=self.font_family, size=11),
            corner_radius=11,
            fg_color=self.colors["primary_soft"],
            text_color=self.colors["primary"],
            hover_color=self.colors["border_strong"],
            command=self.copy_log_to_clipboard
        )
        self.btn_copy_log.pack(side=tk.RIGHT, padx=(4, 0))

        self.btn_goto_error_line = ctk.CTkButton(
            log_header_frame,
            text="跳转到报错行",
            width=96,
            height=22,
            font=ctk.CTkFont(family=self.font_family, size=11, weight="bold"),
            corner_radius=11,
            fg_color="#FEE2E2",
            text_color="#DC2626",
            hover_color="#FECACA",
            command=lambda: self.jump_to_script_error_line(self.last_error_line_no),
        )
        self.btn_goto_error_line.pack(side=tk.RIGHT, padx=(4, 0))
        self.btn_goto_error_line.pack_forget()

        self.txt_script_log = ctk.CTkTextbox(log_box, font=ctk.CTkFont(family="Consolas", size=11), fg_color="transparent")
        self.txt_script_log.grid(row=1, column=0, sticky="nsew", padx=4, pady=(0, 4))
        self.txt_script_log.insert(tk.END, "数据状态: 未加载工程\n")
        self.txt_script_log.configure(state="disabled")
        bind_text_context_menu(self.txt_script_log, readonly=True)

        # 绑定日志点击报错行号跳转
        log_tb = getattr(self.txt_script_log, "_textbox", self.txt_script_log)
        try:
            log_tb.tag_config("error_line_link", foreground="#DC2626", underline=True)
            log_tb.tag_bind("error_line_link", "<Button-1>", self._on_error_log_clicked)
        except Exception:
            pass

        def speed_up_log_scroll(event):
            scroll_units = int(-1 * (event.delta / 120) * 4)
            self.txt_script_log.yview_scroll(scroll_units, "units")
            return "break"
        self.txt_script_log.bind("<MouseWheel>", speed_up_log_scroll)
        if hasattr(self.txt_script_log, "_textbox"):
            self.txt_script_log._textbox.bind("<MouseWheel>", speed_up_log_scroll)

        # 日志底部：无弹窗工程归档控制区（紧凑单行水平排布）
        archive_footer = ctk.CTkFrame(log_box, fg_color="transparent", height=28)
        archive_footer.grid(row=2, column=0, sticky="ew", padx=8, pady=(2, 6))

        self.chk_auto_archive_script_run = self._make_checkbox(
            archive_footer,
            text="自动归档",
            variable=self.var_auto_archive_script_run,
            width=84,
        )
        self.chk_auto_archive_script_run.pack(side=tk.LEFT)

        self.btn_archive_script_run = ctk.CTkButton(
            archive_footer,
            text="写入工程",
            width=76,
            height=24,
            corner_radius=8,
            font=ctk.CTkFont(family=self.font_family, size=11),
            fg_color=self.colors["primary_soft"],
            text_color=self.colors["primary"],
            hover_color=self.colors["border_strong"],
            state="disabled",
            command=self.archive_last_script_run_to_project,
        )
        self.btn_archive_script_run.pack(side=tk.RIGHT)

        # 加入 PanedWindow（默认左 4 : 右 1 比例，确保预览区占 >75% 空间）
        self.script_workspace_paned.add(preview_box, minsize=380, stretch="always")
        self.script_workspace_paned.add(log_box, minsize=185, width=230, stretch="never")

        # 初始切换为空白占位模式（隐藏画布交互按钮栏，居中展示空状态提示）
        self._switch_preview_viewport_mode("placeholder")

        # 刷新列表并选择第一个
        self.load_scripts_to_tree()
        children = self.script_tree.get_children()
        if children:
            self.script_tree.selection_set(children[0])


    def toggle_code_preview_drawer(self, expand=None):
        current = bool(getattr(self, "code_drawer_expanded", False))
        target = (not current) if expand is None else bool(expand)
        self.code_drawer_expanded = target
        drawer = getattr(self, "code_preview_drawer", None)
        btn = getattr(self, "btn_toggle_code_drawer", None)
        if drawer is not None:
            if target:
                ctrl_bar = getattr(self, "script_control_bar", None)
                if ctrl_bar is not None:
                    drawer.pack(fill=tk.X, padx=14, pady=(0, 4), before=ctrl_bar)
                else:
                    drawer.pack(fill=tk.X, padx=14, pady=(0, 4))
            else:
                drawer.pack_forget()
        if btn is not None:
            btn.configure(text="▾ 收起源码" if target else "▸ 源码预览")
        return self.code_drawer_expanded


    toggle_script_code_drawer = toggle_code_preview_drawer


    def toggle_script_log_pane(self, collapse=None):
        current = bool(getattr(self, "log_pane_collapsed", False))
        target = (not current) if collapse is None else bool(collapse)
        self.log_pane_collapsed = target
        paned = getattr(self, "script_workspace_paned", None)
        log_box = getattr(self, "script_log_box", None)
        btn = getattr(self, "btn_toggle_log_pane", None)
        if paned is not None and log_box is not None:
            try:
                panes = list(paned.panes())
                if target and str(log_box) in [str(p) for p in panes]:
                    paned.forget(log_box)
                elif (not target) and str(log_box) not in [str(p) for p in panes]:
                    paned.add(log_box, minsize=185, width=230, stretch="never")
            except Exception:
                pass
        if btn is not None:
            btn.configure(text="◂ 展开日志" if target else "收起日志 ▸")
        return self.log_pane_collapsed


    def _update_project_patch_bar_visibility(self, script_type=None):
        bar = getattr(self, "project_patch_bar", None)
        if bar is None:
            return
        if script_type is None:
            script = next(
                (s for s in getattr(self, "local_scripts", []) if s.get("id") == getattr(self, "selected_script_id", None)),
                None,
            )
            script_type = script.get("type", "chart") if script else "chart"
        has_patch = getattr(self, "pending_project_patch", None) is not None
        if script_type == "data_process" or has_patch:
            try:
                bar.pack(side=tk.RIGHT, padx=(6, 0))
            except Exception:
                pass
        else:
            try:
                bar.pack_forget()
            except Exception:
                pass


    def rebuild_script_params_ui(self, code):
        from modules.script_runner import extract_script_params_schema
        schema = extract_script_params_schema(code or "")
        self.current_script_params_schema = list(schema)
        self.script_param_widgets = {}

        frame = getattr(self, "script_params_frame", None)
        if frame is None:
            return schema

        for child in frame.winfo_children():
            child.destroy()

        if not schema:
            frame.pack_forget()
            return schema

        out_frame = getattr(self, "script_output_frame", None)
        if out_frame is not None:
            frame.pack(fill=tk.X, padx=14, pady=(0, 4), before=out_frame)
        else:
            frame.pack(fill=tk.X, padx=14, pady=(0, 4))

        inner = ctk.CTkFrame(frame, fg_color="transparent")
        inner.pack(fill=tk.X, padx=10, pady=5)

        ctk.CTkLabel(
            inner,
            text="脚本参数:",
            font=ctk.CTkFont(family=self.font_family, size=11, weight="bold"),
            text_color=self.colors["muted"],
        ).pack(side=tk.LEFT, padx=(0, 8))

        for item in schema:
            key = item["key"]
            label = item.get("label") or key
            p_type = item.get("type", "str")
            default_v = item.get("default")
            choices = item.get("choices") or []

            cell = ctk.CTkFrame(inner, fg_color="transparent")
            cell.pack(side=tk.LEFT, padx=(0, 10))

            if p_type == "bool":
                var = tk.BooleanVar(value=bool(default_v))
                widget = self._make_checkbox(
                    cell,
                    text=label,
                    variable=var,
                )
                widget.pack(side=tk.LEFT)
                self.script_param_widgets[key] = {
                    "schema": item,
                    "type": p_type,
                    "var": var,
                    "widget": widget,
                }
            elif p_type == "choice":
                ctk.CTkLabel(
                    cell,
                    text=f"{label}:",
                    font=ctk.CTkFont(family=self.font_family, size=11),
                    text_color=self.colors["text"],
                ).pack(side=tk.LEFT, padx=(0, 4))
                str_choices = [str(c) for c in choices] if choices else [str(default_v)]
                var = tk.StringVar(value=str(default_v))
                widget = ctk.CTkOptionMenu(
                    cell,
                    values=str_choices,
                    variable=var,
                    height=26,
                    width=max(90, min(160, max(len(s) for s in str_choices) * 11 + 36)),
                    corner_radius=8,
                    font=ctk.CTkFont(family=self.font_family, size=11),
                    fg_color="#FFFFFF",
                    text_color=self.colors["text"],
                    button_color="#F8FAFC",
                    button_hover_color="#E2E8F0",
                    dropdown_fg_color="#FFFFFF",
                    dropdown_text_color=self.colors["text"],
                    dropdown_hover_color="#EFF6FF",
                )
                widget.pack(side=tk.LEFT)
                _apply_custom_arrow(widget)
                self.script_param_widgets[key] = {
                    "schema": item,
                    "type": p_type,
                    "var": var,
                    "widget": widget,
                }
            else:
                ctk.CTkLabel(
                    cell,
                    text=f"{label}:",
                    font=ctk.CTkFont(family=self.font_family, size=11),
                    text_color=self.colors["text"],
                ).pack(side=tk.LEFT, padx=(0, 4))
                var = tk.StringVar(value="" if default_v is None else str(default_v))
                width_px = 68 if p_type in ("int", "float") else 110
                widget = ctk.CTkEntry(
                    cell,
                    textvariable=var,
                    width=width_px,
                    height=26,
                    corner_radius=8,
                    border_width=1,
                    font=ctk.CTkFont(family="Consolas", size=11),
                    fg_color="#FFFFFF",
                    border_color=self.colors.get("border_strong", "#CBD5E1"),
                    text_color=self.colors["text"],
                )
                widget.pack(side=tk.LEFT)
                self.script_param_widgets[key] = {
                    "schema": item,
                    "type": p_type,
                    "var": var,
                    "widget": widget,
                }

        self.btn_reset_script_params = ctk.CTkButton(
            inner,
            text="重置默认",
            width=68,
            height=24,
            corner_radius=8,
            font=ctk.CTkFont(family=self.font_family, size=11),
            fg_color="#FFFFFF",
            border_width=1,
            border_color=self.colors.get("border_strong", "#CBD5E1"),
            text_color=self.colors.get("text_soft", "#334155"),
            hover_color="#F1F5F9",
            command=self.reset_script_params_to_default,
        )
        self.btn_reset_script_params.pack(side=tk.RIGHT, padx=(6, 0))
        return schema


    def set_script_param_ui_value(self, key, value):
        info = (getattr(self, "script_param_widgets", None) or {}).get(key)
        if not info:
            return False
        p_type = info.get("type")
        var = info.get("var")
        widget = info.get("widget")
        if p_type == "bool":
            b_val = bool(value)
            if var is not None:
                var.set(b_val)
            elif widget is not None:
                if b_val:
                    widget.select()
                else:
                    widget.deselect()
        elif p_type == "choice":
            s_val = str(value)
            if var is not None:
                var.set(s_val)
            if widget is not None and hasattr(widget, "set"):
                widget.set(s_val)
        else:
            s_val = "" if value is None else str(value)
            if var is not None:
                var.set(s_val)
            elif widget is not None:
                widget.delete(0, tk.END)
                widget.insert(0, s_val)
        return True


    def reset_script_params_to_default(self):
        schema = getattr(self, "current_script_params_schema", None) or []
        for item in schema:
            self.set_script_param_ui_value(item["key"], item.get("default"))
        return self.collect_script_params_from_ui()


    def collect_script_params_from_ui(self):
        from modules.script_runner import coerce_script_params
        schema = getattr(self, "current_script_params_schema", None) or []
        widgets = getattr(self, "script_param_widgets", None) or {}
        raw_values = {}
        for item in schema:
            key = item["key"]
            info = widgets.get(key)
            if not info:
                raw_values[key] = item.get("default")
                continue
            var = info.get("var")
            widget = info.get("widget")
            if var is not None:
                raw_values[key] = var.get()
            elif widget is not None and hasattr(widget, "get"):
                raw_values[key] = widget.get()
            else:
                raw_values[key] = item.get("default")
        return coerce_script_params(schema, raw_values)


    def copy_log_to_clipboard(self):
        try:
            log_text = self.txt_script_log.get("1.0", tk.END).strip()
            self.clipboard_clear()
            self.clipboard_append(log_text)
            self.update()

            self.btn_copy_log.configure(
                text="✔已复制",
                fg_color=self.colors["success_soft"],
                text_color=self.colors["success"]
            )
            def reset():
                try:
                    self.btn_copy_log.configure(
                        text="复制",
                        fg_color=self.colors["primary_soft"],
                        text_color=self.colors["primary"]
                    )
                except Exception:
                    pass
            self.after(1000, reset)
        except Exception as e:
            messagebox.showerror("错误", f"复制失败：{e}")


    def load_scripts_to_tree(self):
        from modules.script_manager import load_all_scripts
        self.local_scripts = load_all_scripts()

        for item in self.script_tree.get_children():
            self.script_tree.delete(item)

        search_term = self.entry_script_search.get().strip().lower()
        filter_type = self.combo_script_type.get()

        for s in self.local_scripts:
            name = s.get("name", "未命名")
            desc = s.get("description", "")
            type_str = s.get("type", "chart")

            if search_term and search_term not in name.lower() and search_term not in desc.lower():
                continue

            if filter_type == "图表脚本" and type_str != "chart":
                continue
            elif filter_type == "数据处理脚本" and type_str != "data_process":
                continue

            self.script_tree.insert("", tk.END, iid=s["id"], text=name)

        if self.selected_script_id and self.script_tree.exists(self.selected_script_id):
            self.script_tree.selection_set(self.selected_script_id)
        else:
            self.script_tree.selection_clear()
            self.on_script_selected()


    def show_script_context_menu(self, event, iid):
        import os
        from modules.ui_widgets import make_context_menu, post_context_menu

        menu = make_context_menu(self)

        if iid is not None:
            if getattr(self, "is_script_running", False):
                menu.add_command(label="停止运行", command=lambda: self.after(10, self.stop_current_script))
            else:
                menu.add_command(label="运行脚本", command=lambda: self.after(10, self.run_current_script))

            menu.add_command(label="编辑脚本", command=lambda: self.after(10, self.on_edit_script))
            menu.add_command(label="导出脚本...", command=lambda: self.after(10, self.on_export_script))
            menu.add_command(label="复制 AI 提示词...", command=lambda: self.after(10, self.show_prompt_dialog))

            menu.add_separator()

            folder = getattr(self, "script_output_dir", None)
            has_output = folder and os.path.isdir(folder)
            state = "normal" if has_output else "disabled"
            menu.add_command(label="打开结果目录", command=lambda: self.after(10, self.open_script_output_dir), state=state)

            menu.add_separator()
            menu.add_command(label="删除脚本...", command=lambda: self.after(10, self.on_delete_script))
        else:
            menu.add_command(label="新建脚本", command=lambda: self.after(10, self.on_new_script))
            menu.add_command(label="导入脚本...", command=lambda: self.after(10, self.on_import_script))
            menu.add_command(label="复制 AI 提示词...", command=lambda: self.after(10, self.show_prompt_dialog))

        post_context_menu(menu, event)


    def on_script_selected(self, event=None):
        selected = self.script_tree.selection() if hasattr(self, "script_tree") else ()
        if not selected:
            self.selected_script_id = None
            if hasattr(self, "lbl_selected_script_name"):
                self.lbl_selected_script_name.configure(text="当前脚本：未选择")
            if hasattr(self, "lbl_selected_script_desc"):
                self.lbl_selected_script_desc.configure(text="描述：请从左侧列表中选择一个脚本，或者点击“新建”创建新脚本。")
            if hasattr(self, "txt_script_code_preview"):
                self.txt_script_code_preview.configure(state="normal")
                self.txt_script_code_preview.delete("1.0", tk.END)
                self.txt_script_code_preview.configure(state="disabled")
            self._update_project_patch_bar_visibility("chart")
            self.rebuild_script_params_ui("")
            return
        s_id = selected[0]
        self.selected_script_id = s_id

        script = next((s for s in self.local_scripts if s["id"] == s_id), None)
        if script:
            s_type = script.get("type", "chart")
            if hasattr(self, "lbl_selected_script_name"):
                self.lbl_selected_script_name.configure(text=f"当前脚本：{script.get('name', '')}")
            if hasattr(self, "lbl_selected_script_type_tag"):
                if s_type == "data_process":
                    self.lbl_selected_script_type_tag.configure(
                        text="数据处理",
                        fg_color=self.colors["warning_soft"],
                        text_color=self.colors["warning"],
                    )
                else:
                    self.lbl_selected_script_type_tag.configure(
                        text="图表脚本",
                        fg_color=self.colors["primary_soft"],
                        text_color=self.colors["primary"],
                    )
            if hasattr(self, "lbl_selected_script_desc"):
                self.lbl_selected_script_desc.configure(text=f"描述：{script.get('description', '')}")
                wrap_fn = getattr(self.lbl_selected_script_desc, "_update_adaptive_wrap", None)
                if callable(wrap_fn):
                    wrap_fn()
                    self.after(20, wrap_fn)

            code = script.get("code", "")
            if hasattr(self, "txt_script_code_preview"):
                self.txt_script_code_preview.configure(state="normal")
                self.txt_script_code_preview.delete("1.0", tk.END)
                self.txt_script_code_preview.insert("1.0", code)
                self.txt_script_code_preview.configure(state="disabled")
            self._update_project_patch_bar_visibility(s_type)
            self.rebuild_script_params_ui(code)


    def on_script_filter_changed(self, val):
        self.load_scripts_to_tree()


    def on_new_script(self):
        default_type = "data_process" if self.combo_script_type.get() == "数据处理脚本" else "chart"
        dlg = ScriptEditorDialog(
            self,
            script_id=None,
            script_name=SCRIPT_EDITOR_DEFAULT_NAME,
            script_desc=SCRIPT_EDITOR_DEFAULT_DESC,
            script_code="",
            script_type=default_type,
        )
        self.active_script_editor_dialog = dlg
        return dlg


    def on_edit_script(self, initial_line=None):
        selected = self.script_tree.selection() if hasattr(self, "script_tree") else ()
        if not selected and not getattr(self, "selected_script_id", None):
            return messagebox.showwarning("提示", "请先在左侧列表中选择要编辑的脚本。")
        s_id = selected[0] if selected else self.selected_script_id
        script = next((s for s in self.local_scripts if s["id"] == s_id), None)
        if script:
            dlg = ScriptEditorDialog(
                self,
                script_id=script["id"],
                script_name=script.get("name", ""),
                script_desc=script.get("description", ""),
                script_code=script.get("code", ""),
                script_type=script.get("type", "chart"),
                initial_line=initial_line,
            )
            self.active_script_editor_dialog = dlg
            return dlg
        return None


    def jump_to_script_error_line(self, line_no=None):
        target_line = line_no if line_no is not None else getattr(self, "last_error_line_no", None)
        if not target_line:
            return None
        try:
            target_line = int(target_line)
        except (TypeError, ValueError):
            return None
        dlg = getattr(self, "active_script_editor_dialog", None)
        try:
            if dlg is not None and dlg.winfo_exists():
                dlg.lift()
                dlg.goto_line(target_line)
                return dlg
        except Exception:
            pass
        return self.on_edit_script(initial_line=target_line)


    def _on_error_log_clicked(self, _event=None):
        if getattr(self, "last_error_line_no", None):
            self.jump_to_script_error_line(self.last_error_line_no)


    def on_delete_script(self):
        selected = self.script_tree.selection()
        if not selected:
            return messagebox.showwarning("提示", "请先在列表中选择一个要删除的脚本。")
        s_id = selected[0]

        script = next((s for s in self.local_scripts if s["id"] == s_id), None)
        if not script:
            return

        ans = messagebox.askyesno("确认删除", f"确认要删除脚本“{script['name']}”吗？")
        if ans:
            from modules.script_manager import delete_script
            delete_script(s_id)
            self.selected_script_id = None
            self.lbl_selected_script_name.configure(text="当前脚本：未选择")
            self.lbl_selected_script_desc.configure(text="描述：请从左侧列表中选择一个脚本，或者点击“新建”创建新脚本。")
            self.txt_script_code_preview.configure(state="normal")
            self.txt_script_code_preview.delete("1.0", tk.END)
            self.txt_script_code_preview.configure(state="disabled")
            self.rebuild_script_params_ui("")
            self.load_scripts_to_tree()


    def on_import_script(self):
        file_path = filedialog.askopenfilename(filetypes=[("JSON Files", "*.json")])
        if file_path:
            from modules.script_manager import import_script
            try:
                new_s = import_script(file_path)
                messagebox.showinfo("成功", f"成功导入脚本：{new_s['name']}")
                self.load_scripts_to_tree()
                self.script_tree.selection_set(new_s["id"])
            except Exception as e:
                messagebox.showerror("错误", f"导入失败：{e}")


    def on_export_script(self):
        selected = self.script_tree.selection()
        if not selected:
            return messagebox.showwarning("提示", "请先在列表中选择一个要导出的脚本。")
        s_id = selected[0]

        script = next((s for s in self.local_scripts if s["id"] == s_id), None)
        if not script:
            return

        dest_path = filedialog.asksaveasfilename(
            defaultextension=".json",
            initialfile=f"{script['name']}.json",
            filetypes=[("JSON Files", "*.json")]
        )
        if dest_path:
            from modules.script_manager import export_script
            try:
                export_script(s_id, dest_path)
                messagebox.showinfo("成功", f"脚本已成功导出至：\n{dest_path}")
            except Exception as e:
                messagebox.showerror("错误", f"导出失败：{e}")


    def run_current_script(self):
        code = strip_markdown_code_fence(self.txt_script_code_preview.get("1.0", tk.END))
        teproj_path = getattr(self, "loaded_teproj_path", None)
        ui_params = self.collect_script_params_from_ui()
        self.last_run_params = dict(ui_params)

        if getattr(self, "script_figure_results", None):
            self.previous_run_figure_results = list(self.script_figure_results)

        self.is_script_running = True
        self.last_error_line_no = None
        if hasattr(self, "btn_goto_error_line") and self.btn_goto_error_line:
            self.btn_goto_error_line.pack_forget()
        self.btn_run_script.configure(state="disabled")
        self.btn_stop_script.configure(state="normal")
        self._update_chart_preview("正在运行脚本，等待输出...", None)
        self.script_output_dir = None
        self.script_figure_results = []
        self.current_script_figure_index = 0
        self.script_table_results = []
        self.current_script_table_index = 0
        self.pending_project_patch = None
        if hasattr(self, "btn_apply_project_patch") and self.btn_apply_project_patch:
            self.btn_apply_project_patch.configure(state="disabled", text="应用补丁并另存工程 (.teproj)")
        self._update_project_patch_bar_visibility()
        if hasattr(self, "btn_open_script_output") and self.btn_open_script_output:
            self.btn_open_script_output.configure(state="disabled")
        if hasattr(self, "script_preview_nav") and self.script_preview_nav:
            self.script_preview_nav.grid_remove()
        self._rebuild_artifact_capsule_tabs()

        self.txt_script_log.configure(state="normal")
        self.txt_script_log.delete("1.0", tk.END)
        self.txt_script_log.insert("1.0", "正在构建工程数据快照并运行自定义脚本...\n")
        if ui_params:
            self.txt_script_log.insert(tk.END, f"运行参数: {ui_params}\n")
        self.txt_script_log.configure(state="disabled")

        self.run_cancel_event = threading.Event()

        def execute():
            try:
                if teproj_path:
                    from modules.script_api import build_dataset_snapshot
                    dataset_items = build_dataset_snapshot(teproj_path)
                else:
                    dataset_items = []

                from modules.script_runner import run_custom_script
                import matplotlib.pyplot as plt
                plt.close('all')

                res, logs, err = run_custom_script(
                    code,
                    dataset_items,
                    timeout=30,
                    cancel_event=self.run_cancel_event,
                    teproj_path=teproj_path,
                    params=ui_params,
                )
                self.after(0, lambda res=res, logs=logs, err=err: self.on_script_finished(res, logs, err))
            except Exception as e:
                import traceback
                tb = traceback.format_exc()
                err_msg = f"发生意外错误：\n{e}\n\n堆栈信息：\n{tb}"
                self.after(0, lambda err_msg=err_msg: self.on_script_finished(None, [], err_msg))

        start_safe_thread(execute)


    def stop_current_script(self):
        if getattr(self, "run_cancel_event", None):
            self.run_cancel_event.set()
            self.txt_script_log.configure(state="normal")
            self.txt_script_log.insert(tk.END, "\n已请求中止脚本运行，脚本会在下一次检查取消状态时结束。\n")
            self.txt_script_log.configure(state="disabled")


    def on_script_finished(self, res, logs, err):
        self.is_script_running = False
        if hasattr(self, "btn_run_script") and self.btn_run_script:
            self.btn_run_script.configure(state="normal")
        if hasattr(self, "btn_stop_script") and self.btn_stop_script:
            self.btn_stop_script.configure(state="disabled")

        if hasattr(self, "txt_script_log") and self.txt_script_log:
            self.txt_script_log.configure(state="normal")
            self.txt_script_log.delete("1.0", tk.END)
            if logs:
                self.txt_script_log.insert("1.0", "=== 运行日志 ===\n" + "\n".join(logs) + "\n\n")
            self.txt_script_log.configure(state="disabled")

        if err:
            self.append_script_log(f"运行失败：\n{err}\n")
            self._update_chart_preview("运行失败", None)
            self.script_output_dir = None
            self.script_figure_results = []
            self.current_script_figure_index = 0
            self.script_table_results = []
            self.current_script_table_index = 0
            if hasattr(self, "btn_open_script_output") and self.btn_open_script_output:
                self.btn_open_script_output.configure(state="disabled")
            if hasattr(self, "script_preview_nav") and self.script_preview_nav:
                self.script_preview_nav.grid_remove()
            self._rebuild_artifact_capsule_tabs()
            return

        self.append_script_log("运行成功结束。\n")
        self.display_script_result(res)


    def append_script_log(self, text):
        msg = str(text or "")
        line_no = extract_error_line_number(msg)
        if line_no:
            self.last_error_line_no = line_no
            btn_err = getattr(self, "btn_goto_error_line", None)
            if btn_err is not None:
                try:
                    btn_err.configure(text=f"跳转到报错行 (Line {line_no})")
                    btn_err.pack(side=tk.RIGHT, padx=(4, 0))
                except Exception:
                    pass

        if not hasattr(self, "txt_script_log") or not self.txt_script_log:
            return
        self.txt_script_log.configure(state="normal")
        start_idx = self.txt_script_log.index("end-1c")
        self.txt_script_log.insert(tk.END, msg)
        end_idx = self.txt_script_log.index("end-1c")
        if line_no:
            log_tb = getattr(self.txt_script_log, "_textbox", self.txt_script_log)
            try:
                log_tb.tag_config("error_line_link", foreground="#DC2626", underline=True)
                log_tb.tag_add("error_line_link", start_idx, end_idx)
            except Exception:
                pass
        self.txt_script_log.see(tk.END)
        self.txt_script_log.configure(state="disabled")


    def _safe_script_output_name(self, name, fallback="output"):
        raw = str(name or fallback).strip()
        raw = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", raw)
        raw = re.sub(r"\s+", "_", raw)
        raw = raw.strip("._ ")
        return raw[:80] or fallback


    def _unique_path(self, folder, filename):
        base, ext = os.path.splitext(filename)
        path = os.path.join(folder, filename)
        index = 2
        while os.path.exists(path):
            path = os.path.join(folder, f"{base}_{index}{ext}")
            index += 1
        return path


    def get_script_output_dir(self, script_name):
        base_dir = os.path.join(os.path.expanduser("~"), ".phon_tracer", "script_outputs")
        safe_name = self._safe_script_output_name(script_name, "script")
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        folder = os.path.join(base_dir, f"{stamp}_{safe_name}")
        suffix = 2
        unique_folder = folder
        while os.path.exists(unique_folder):
            unique_folder = f"{folder}_{suffix}"
            suffix += 1
        os.makedirs(unique_folder, exist_ok=True)
        return unique_folder


    def get_script_preview_dir(self, output_dir):
        base_dir = os.path.join(os.path.expanduser("~"), ".phon_tracer", "temp", "script_previews")
        run_name = os.path.basename(os.path.normpath(output_dir)) if output_dir else "script_run"
        preview_dir = os.path.join(base_dir, run_name)
        os.makedirs(preview_dir, exist_ok=True)
        return preview_dir


    def open_script_output_dir(self):
        folder = getattr(self, "script_output_dir", None)
        if not folder or not os.path.isdir(folder):
            messagebox.showinfo("结果目录", "当前还没有可打开的脚本输出目录。请先运行脚本并生成结果。")
            return
        try:
            if sys.platform == "win32":
                os.startfile(folder)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", folder])
            else:
                subprocess.Popen(["xdg-open", folder])
        except Exception as e:
            messagebox.showerror("打开失败", f"无法打开结果目录：\n{e}")


    def _open_file_safely(self, path):
        if not path or not os.path.exists(path):
            messagebox.showerror("错误", f"找不到文件：\n{path}")
            return
        try:
            if sys.platform == "win32":
                os.startfile(path)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception as e:
            messagebox.showerror("打开失败", f"无法打开文件：\n{e}")


    def archive_last_script_run_to_project(self):
        if not getattr(self, "loaded_teproj_path", None) or not getattr(self, "project_data", None):
            self.append_script_log("当前未加载工程，无法归档脚本运行记录。\n")
            return False
        run_record = getattr(self, "last_script_run_record", None)
        if not run_record:
            self.append_script_log("当前没有可归档的脚本运行记录。\n")
            return False

        if "custom_script_runs" not in self.project_data:
            self.project_data["custom_script_runs"] = []

        script_runs = self.project_data["custom_script_runs"]
        prev_runs = copy.deepcopy(script_runs)
        from modules.project_patch import _append_or_update_script_run
        _append_or_update_script_run(script_runs, copy.deepcopy(run_record))

        if self.save_project_data_back_to_teproj():
            self.append_script_log("本次脚本运行已成功写入当前工程记录。\n")
            return True
        else:
            self.project_data["custom_script_runs"] = prev_runs
            self.append_script_log("本次脚本运行写入工程记录失败。\n")
            return False


    def apply_pending_project_patch(self, output_path=None, run_synchronously=False):
        from modules.project_patch import apply_project_patch_to_teproj

        patch_res = getattr(self, "pending_project_patch", None)
        if patch_res is None:
            self.append_script_log("当前没有待应用的工程补丁。\n")
            return None

        if not getattr(self, "loaded_teproj_path", None):
            self.append_script_log("未加载任何工程，无法应用数据处理结果。\n")
            if not (getattr(self, "script_figure_results", None) or getattr(self, "script_table_results", None)):
                self._update_chart_preview("未加载工程，操作未应用。", None)
            messagebox.showwarning("警告", "当前未加载工程，无法应用数据处理操作。")
            return None

        if not patch_res.operations:
            self.append_script_log("脚本没有返回任何工程修改操作。\n")
            if not (getattr(self, "script_figure_results", None) or getattr(self, "script_table_results", None)):
                self._update_chart_preview("脚本没有返回任何工程修改操作。", None)
            return None

        if not output_path:
            orig_dir, orig_name = os.path.split(self.loaded_teproj_path)
            name_part, ext_part = os.path.splitext(orig_name)
            default_output_name = f"patched_{name_part}{ext_part}"
            output_path = filedialog.asksaveasfilename(
                title="保存修改后的工程",
                initialdir=orig_dir,
                initialfile=default_output_name,
                defaultextension=".teproj",
                filetypes=[("PhonTracer Project", "*.teproj")]
            )
        if not output_path:
            self.append_script_log("用户取消了保存，工程补丁未应用。\n")
            return None

        if os.path.normcase(os.path.abspath(output_path)) == os.path.normcase(os.path.abspath(self.loaded_teproj_path)):
            self.append_script_log("输出路径与原工程相同，操作未应用。请另存为新的 .teproj 文件。\n")
            messagebox.showwarning("警告", "数据处理脚本不会覆盖原工程，请另存为新的 .teproj 文件。")
            return None

        run_record = copy.deepcopy(getattr(self, "last_script_run_record", None) or {})
        run_record.setdefault("script_name", patch_res.title or "数据处理脚本")
        run_record.setdefault("script_type", "data_process")
        outputs_list = list(run_record.get("outputs") or [])
        outputs_list.append({
            "type": "project",
            "title": patch_res.title or "数据处理后的工程",
            "filename": os.path.basename(output_path),
            "saved_path": output_path,
        })
        run_record["outputs"] = outputs_list

        self.append_script_log("\n正在后台解压工程、应用修改并重新打包，请稍候...\n")
        if hasattr(self, "set_loading") and hasattr(self, "progress"):
            try:
                self.set_loading(True, "正在应用数据处理并重新生成工程归档...", indeterminate=True)
            except Exception:
                pass
        if hasattr(self, "btn_run_script") and self.btn_run_script:
            self.btn_run_script.configure(state="disabled")

        if run_synchronously:
            try:
                summary = apply_project_patch_to_teproj(
                    self.loaded_teproj_path,
                    patch_res,
                    output_path,
                    run_record=run_record,
                )
                self._on_project_patch_complete(summary, output_path, notify_modal=False)
                return summary
            except Exception as e:
                tb = traceback.format_exc()
                self._on_project_patch_failed(e, tb, notify_modal=False)
                raise
            finally:
                self._on_project_patch_cleanup()

        def do_apply_patch():
            try:
                summary = apply_project_patch_to_teproj(
                    self.loaded_teproj_path,
                    patch_res,
                    output_path,
                    run_record=run_record
                )
                self.after(0, lambda: self._on_project_patch_complete(summary, output_path))
            except Exception as e:
                tb = traceback.format_exc()
                self.after(0, lambda err=e, trace=tb: self._on_project_patch_failed(err, trace))
            finally:
                self.after(0, lambda: self._on_project_patch_cleanup())

        start_safe_thread(do_apply_patch)
        return output_path


    def _on_project_patch_complete(self, summary, output_path, notify_modal=True):
        self.append_script_log("\n=== 数据处理应用结果 ===\n")
        self.append_script_log(f"操作名称: {summary.get('title') or '未命名'}\n")
        if summary.get('description'):
            self.append_script_log(f"操作说明: {summary.get('description')}\n")

        self.append_script_log(f"包含操作总数: {summary.get('operation_count', 0)}\n")

        applied_ops = summary.get("applied_operations", [])
        self.append_script_log(f"实际执行细节 (共 {len(applied_ops)} 项):\n")
        for r in applied_ops:
            self.append_script_log(f"  - {r.get('message') or r.get('op')}\n")

        output_paths = summary.get("output_paths", [])
        if output_paths:
            self.append_script_log("\n已成功拆分为以下子工程文件：\n")
            for path in output_paths:
                self.append_script_log(f"  - {path}\n")
        else:
            self.append_script_log(f"\n已生成工程文件: {summary.get('output_path')}\n")

        if not getattr(self, "script_output_dir", None):
            self.script_output_dir = os.path.dirname(output_path)
        if hasattr(self, "btn_open_script_output") and self.btn_open_script_output:
            self.btn_open_script_output.configure(state="normal")

        has_visuals = bool(getattr(self, "script_figure_results", None) or getattr(self, "script_table_results", None))
        if not has_visuals:
            self._update_chart_preview("数据处理脚本运行成功，无图表输出。", None)
            if hasattr(self, "script_preview_nav") and self.script_preview_nav:
                self.script_preview_nav.grid_remove()

            if not output_paths:
                single_path = summary.get("output_path")
                if single_path:
                    output_paths = [single_path]

            if output_paths and hasattr(self, "script_file_list_frame") and self.script_file_list_frame:
                self._switch_preview_viewport_mode("files")
                for widget in self.script_file_list_frame.winfo_children():
                    widget.destroy()

                for path in output_paths:
                    file_name = os.path.basename(path)
                    row_frame = ctk.CTkFrame(self.script_file_list_frame, fg_color="transparent")
                    row_frame.pack(fill=tk.X, padx=5, pady=2)

                    btn = ctk.CTkButton(
                        row_frame,
                        text=f"工程文件：{file_name}",
                        anchor="w",
                        fg_color="transparent",
                        text_color=self.colors["primary"],
                        hover_color=self.colors["surface_soft"],
                        font=ctk.CTkFont(family=self.font_family, size=11),
                        height=28,
                        command=lambda p=path: self._open_file_safely(p)
                    )
                    btn.pack(side=tk.LEFT, fill=tk.X, expand=True)

        if notify_modal:
            messagebox.showinfo("成功", summary.get("message") or "操作成功应用！")


    def _on_project_patch_failed(self, e, tb, notify_modal=True):
        self.append_script_log(f"应用数据处理操作失败：\n{e}\n\n{tb}\n")
        if not (getattr(self, "script_figure_results", None) or getattr(self, "script_table_results", None)):
            self._update_chart_preview("数据处理应用失败。", None)
            if hasattr(self, "script_preview_nav") and self.script_preview_nav:
                self.script_preview_nav.grid_remove()
        if notify_modal:
            messagebox.showerror("错误", f"应用数据处理操作失败：{e}")


    def _on_project_patch_cleanup(self):
        if hasattr(self, 'set_loading') and hasattr(self, 'progress'):
            try:
                self.set_loading(False)
            except Exception:
                pass
        if hasattr(self, 'btn_run_script') and self.btn_run_script:
            self.btn_run_script.configure(state="normal")


    def save_project_data_back_to_teproj(self):
        if not hasattr(self, 'loaded_teproj_path') or not self.loaded_teproj_path:
            return False
        import tempfile
        import zipfile
        import json

        zip_path = self.loaded_teproj_path
        zip_dir = os.path.dirname(os.path.abspath(zip_path)) or "."
        temp_fd, temp_path = tempfile.mkstemp(prefix=".script_update_", suffix=".teproj", dir=zip_dir)
        os.close(temp_fd)
        try:
            import shutil
            with zipfile.ZipFile(zip_path, 'r') as yin:
                with zipfile.ZipFile(temp_path, 'w', zipfile.ZIP_DEFLATED) as yout:
                    for item in yin.infolist():
                        if item.filename == 'project.json':
                            continue
                        if item.is_dir():
                            yout.writestr(item, b'')
                            continue
                        with yin.open(item, 'r') as src, yout.open(item, 'w') as dst:
                            shutil.copyfileobj(src, dst, length=1024 * 1024)
                    json_bytes = json.dumps(self.project_data, ensure_ascii=False, indent=2).encode('utf-8')
                    yout.writestr('project.json', json_bytes)
            read_project_metadata_from_archive(temp_path)
            os.replace(temp_path, zip_path)
            return True
        except Exception as e:
            messagebox.showerror("错误", f"更新工程文件归档失败：{e}")
            return False
        finally:
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except Exception:
                    pass


    def update_script_tab_project_summary(self):
        self.txt_script_log.configure(state="normal")
        self.txt_script_log.delete("1.0", tk.END)
        if not hasattr(self, 'project_data') or not self.project_data:
            self.txt_script_log.insert(tk.END, "工程数据状态: 未加载工程\n")
            self.txt_script_log.configure(state="disabled")
            if hasattr(self, "btn_archive_script_run") and self.btn_archive_script_run:
                self.btn_archive_script_run.configure(state="disabled")
            return

        spk_count = len(self.project_data.get("speakers", {}))
        item_count = 0
        groups = set()
        for spk in self.project_data.get("speakers", {}).values():
            items = spk.get("items", {})
            item_count += len(items)
            for item in items.values():
                g = item.get("group")
                if g:
                    groups.add(g)

        summary_text = f"工程: {os.path.basename(self.loaded_teproj_path)}\n发音人数: {spk_count} | 条目总数: {item_count}\n声调分组数: {len(groups)} ({', '.join(sorted(list(groups))) if groups else '无'})"
        self.txt_script_log.insert(tk.END, "=== 工程数据状态 ===\n" + summary_text + "\n")
        self.txt_script_log.configure(state="disabled")
        if hasattr(self, "btn_archive_script_run") and self.btn_archive_script_run and getattr(self, "last_script_run_record", None):
            self.btn_archive_script_run.configure(state="normal")


    def _refresh_top_bar_adaptive_wraps(self):
        for attr in ("lbl_selected_script_desc", "lbl_script_project_status", "lbl_script_project_detail"):
            lbl = getattr(self, attr, None)
            wrap_fn = getattr(lbl, "_update_adaptive_wrap", None) if lbl is not None else None
            if callable(wrap_fn):
                try:
                    wrap_fn()
                    self.after(20, wrap_fn)
                except Exception:
                    pass


    def _set_script_project_card_loading(self, path):
        if not hasattr(self, "lbl_script_project_status"):
            return
        file_name = os.path.basename(path) if path else "未选择"
        self.lbl_script_project_status.configure(text=f"正在加载：{file_name}", text_color=self.colors["primary"])
        self.lbl_script_project_detail.configure(text="正在读取工程元数据，并同步刷新工程预览页。", text_color=self.colors["muted"])
        self._refresh_top_bar_adaptive_wraps()


    def _set_script_project_card_error(self, message):
        if not hasattr(self, "lbl_script_project_status"):
            return
        self.lbl_script_project_status.configure(text="工程解析失败", text_color=self.colors["danger"])
        self.lbl_script_project_detail.configure(text=message, text_color=self.colors["danger"])
        self._refresh_top_bar_adaptive_wraps()


    def _set_script_project_card_summary(self, project_data=None):
        if not hasattr(self, "lbl_script_project_status"):
            return
        project_data = project_data or getattr(self, "project_data", None)
        path = getattr(self, "loaded_teproj_path", None)
        if not path or not project_data:
            self.lbl_script_project_status.configure(text="未加载工程", text_color=self.colors["muted"])
            self.lbl_script_project_detail.configure(
                text="可在此处或“工程预览”页选择同一个 .teproj 工程。",
                text_color=self.colors["muted"],
            )
            if hasattr(self, "btn_script_load_project") and self.btn_script_load_project:
                self.btn_script_load_project.configure(text="选择工程")
            self._refresh_top_bar_adaptive_wraps()
            return

        speakers = project_data.get("speakers", {})
        item_count = sum(len(spk.get("items", {})) for spk in speakers.values())
        self.lbl_script_project_status.configure(
            text=f"已加载：{os.path.basename(path)}",
            text_color=self.colors["success"],
        )
        self.lbl_script_project_detail.configure(
            text=f"发音人数：{len(speakers)}；条目总数：{item_count}。工程预览页已同步。",
            text_color=self.colors["muted"],
        )
        if hasattr(self, "btn_script_load_project") and self.btn_script_load_project:
            self.btn_script_load_project.configure(text="切换工程")
        self._refresh_top_bar_adaptive_wraps()


    def show_prompt_dialog(self):
        script_type = "chart"
        script = next((s for s in self.local_scripts if s.get("id") == self.selected_script_id), None)
        if script:
            script_type = script.get("type", "chart")
        elif hasattr(self, "combo_script_type") and self.combo_script_type.get() == "数据处理脚本":
            script_type = "data_process"
        AIPromptDialog(self, self.project_data if hasattr(self, 'project_data') else None, script_type=script_type)


    def select_project_file(self):
        path = filedialog.askopenfilename(filetypes=[("PhonTracer Project", "*.teproj")])
        if path:
            self.load_project_file(path)


    def clear_project_script_runs(self):
        if not hasattr(self, "loaded_teproj_path") or not self.loaded_teproj_path:
            messagebox.showinfo("提示", "请先打开一个工程文件。")
            return
        if not hasattr(self, "project_data") or not self.project_data:
            messagebox.showinfo("提示", "当前没有可清理的工程数据。")
            return

        count = len(self.project_data.get("custom_script_runs", []) or [])
        if count <= 0:
            messagebox.showinfo("提示", "当前工程没有脚本运行记录。")
            return

        ok = messagebox.askyesno(
            "确认清理",
            f"确认清理当前工程中的 {count} 条脚本运行记录吗？\n\n"
            "这只会移除工程里的脚本运行历史和源码归档，不会删除本地脚本库、音频、条目、缓存数据或已导出的图表文件。",
        )
        if not ok:
            return

        self.project_data.pop("custom_script_runs", None)
        if not self.save_project_data_back_to_teproj():
            return
        self.display_project_preview(self.project_data, getattr(self, "project_namelist", []))
        self.update_script_tab_project_summary()
        messagebox.showinfo("完成", "已清理当前工程中的脚本运行记录。")

