"""
modules.toolkit_ui.dialog_ai_prompt: 生成 AI 提示词引导式对话框。
"""
import tkinter as tk
from tkinter import messagebox
import customtkinter as ctk

from modules.ui_widgets import _apply_custom_arrow, bind_text_context_menu

class AIPromptDialog(ctk.CTkToplevel):
    """
    生成 AI 提示词的引导式对话框。
    """
    def __init__(self, parent, project_data, script_type="chart"):
        super().__init__(parent)
        self.parent = parent
        self.project_data = project_data
        self.script_type = script_type or "chart"
        self.colors = parent.colors
        self.font_family = getattr(parent, "font_family", "Microsoft YaHei")

        self.title("PhonTracer语音学标注与声学分析系统 V1.0 - 生成 AI 数据处理脚本提示词" if self.script_type == "data_process" else "PhonTracer语音学标注与声学分析系统 V1.0 - 生成 AI 脚本提示词")
        self.geometry("620x620")
        self.resizable(True, True)

        # 居中显示
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        x = (sw - 620) // 2
        y = (sh - 620) // 2
        self.geometry(f"620x620+{x}+{y}")
        self.transient(parent)
        self.grab_set()

        self.configure(fg_color=("#F9FAFB", "#1A1D24"))

        # 顶边条
        accent_strip = ctk.CTkFrame(self, height=4, fg_color="#6366F1", corner_radius=0)
        accent_strip.pack(fill="x", side="top")

        self.setup_ui()

    def _section_label(self, parent, text):
        lbl = ctk.CTkLabel(parent, text=text, font=ctk.CTkFont(family="Microsoft YaHei", size=13, weight="bold"), text_color="#374151")
        lbl.pack(anchor="w", padx=10, pady=(15, 2))

    def _make_option_menu(self, parent, values, variable, command=None):
        combo = ctk.CTkOptionMenu(
            parent,
            values=values,
            variable=variable,
            command=command,
            fg_color=("#F3F4F6", "#374151"),
            text_color=("#1F2937", "#E5E7EB"),
            button_color=("#F3F4F6", "#374151"),
            button_hover_color=("#E5E7EB", "#4B5563"),
            height=32,
            corner_radius=16
        )
        combo.pack(fill="x", pady=2)
        _apply_custom_arrow(combo)
        return combo

    def _make_textbox(self, parent, height=80, placeholder=None):
        if placeholder:
            ctk.CTkLabel(
                parent,
                text=placeholder,
                font=ctk.CTkFont(family="Microsoft YaHei", size=11),
                text_color="#64748B",
                wraplength=520,
                justify="left"
            ).pack(anchor="w", padx=10, pady=(0, 2))
        box = ctk.CTkTextbox(
            parent,
            height=height,
            border_width=1,
            border_color=("#D1D5DB", "#374151"),
            fg_color=("#FFFFFF", "#262930"),
            text_color=("#111827", "#F9FAFB")
        )
        box.pack(fill="x", padx=10, pady=5)
        bind_text_context_menu(box)
        return box

    def _textbox_value_without_placeholder(self, textbox, placeholder):
        value = textbox.get("1.0", tk.END).strip()
        if value == (placeholder or "").strip():
            return ""
        return value

    def _on_prompt_tab_changed(self):
        self.after(1, self._update_prompt_tab_text_colors)

    def _update_prompt_tab_text_colors(self):
        segmented = getattr(self.prompt_tabview, "_segmented_button", None)
        buttons = getattr(segmented, "_buttons_dict", {}) if segmented is not None else {}
        current = self.prompt_tabview.get() if hasattr(self, "prompt_tabview") else None
        unselected_text = "#F9FAFB" if ctk.get_appearance_mode().lower() == "dark" else "#111827"
        for name, button in buttons.items():
            try:
                button.configure(text_color="#FFFFFF" if name == current else unselected_text)
            except Exception:
                pass

    def _speed_up_scroll_recursive(self, scroll):
        def _wheel_steps(delta, multiplier):
            if delta == 0:
                return 0
            if abs(delta) >= 120:
                return -int(delta / 120) * multiplier
            return -1 * multiplier if delta > 0 else multiplier

        def speed_up_main_scroll(event):
            canvas = getattr(scroll, "_parent_canvas", None)
            if canvas is not None:
                delta = getattr(event, "delta", 0)
                if delta != 0:
                    try:
                        canvas.configure(yscrollincrement=8)
                    except Exception:
                        pass
                    steps = _wheel_steps(delta, 12)
                    canvas.yview_scroll(steps, "units")
                return "break"

        def safe_mousewheel_bind(widget, handler):
            try:
                widget.bind("<MouseWheel>", handler, add="+")
            except (NotImplementedError, tk.TclError, AttributeError):
                for attr in ("_canvas", "_text_label", "_image_label"):
                    child = getattr(widget, attr, None)
                    if child is not None:
                        try:
                            child.bind("<MouseWheel>", handler, add="+")
                        except (NotImplementedError, tk.TclError, AttributeError):
                            pass

        def bind_scroll_recursive(w):
            if isinstance(w, ctk.CTkTextbox):
                def speed_up_tb_scroll(event):
                    delta = getattr(event, "delta", 0)
                    if delta != 0:
                        steps = _wheel_steps(delta, 8)
                        w.yview_scroll(steps, "units")
                    return "break"
                safe_mousewheel_bind(w, speed_up_tb_scroll)
                if hasattr(w, "_textbox"):
                    safe_mousewheel_bind(w._textbox, speed_up_tb_scroll)
                return

            safe_mousewheel_bind(w, speed_up_main_scroll)
            for child in w.winfo_children():
                bind_scroll_recursive(child)

        bind_scroll_recursive(scroll)

    def _setup_data_process_ui(self):
        bottom_frame = ctk.CTkFrame(self, height=56, fg_color=("#F9FAFB", "#1A1D24"), corner_radius=0)
        bottom_frame.pack(side="bottom", fill="x", pady=0)

        scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        scroll.pack(fill="both", expand=True, padx=20, pady=(15, 6))

        self.prompt_tabview = ctk.CTkTabview(
            scroll,
            fg_color=("#FFFFFF", "#1F232B"),
            segmented_button_fg_color=("#E5E7EB", "#2F3541"),
            segmented_button_selected_color=("#6366F1", "#6366F1"),
            segmented_button_selected_hover_color=("#4F46E5", "#4F46E5"),
            segmented_button_unselected_color=("#E5E7EB", "#2F3541"),
            segmented_button_unselected_hover_color=("#D1D5DB", "#374151"),
            text_color=("#111827", "#F9FAFB"),
            corner_radius=14,
            command=self._on_prompt_tab_changed,
        )
        self.prompt_tabview.pack(fill="x", padx=0, pady=(0, 10))
        agent_tab = self.prompt_tabview.add("Agent协作")
        agent_tab.configure(fg_color="transparent")
        self._build_agent_tab(agent_tab)
        self._update_prompt_tab_text_colors()

        preview_header = ctk.CTkFrame(scroll, fg_color="transparent")
        preview_header.pack(fill="x", padx=10, pady=(15, 2))
        ctk.CTkLabel(preview_header, text="提示词生成预览", font=ctk.CTkFont(family="Microsoft YaHei", size=13, weight="bold"), text_color="#374151").pack(side="left", anchor="w")
        self.btn_copy_prompt_preview = ctk.CTkButton(
            preview_header,
            text="复制",
            width=54,
            height=24,
            font=ctk.CTkFont(family=self.font_family, size=11),
            corner_radius=12,
            fg_color=self.colors["primary_soft"],
            text_color=self.colors["primary"],
            hover_color=self.colors["border_strong"],
            command=self.copy_prompt_preview
        )
        self.btn_copy_prompt_preview.pack(side="right", padx=(5, 0))

        self.txt_preview = ctk.CTkTextbox(
            scroll, height=190, font=ctk.CTkFont(family="Consolas", size=11),
            border_width=1,
            border_color=("#D1D5DB", "#374151"),
            fg_color=("#FFFFFF", "#262930"),
            text_color=("#111827", "#F9FAFB")
        )
        self.txt_preview.pack(fill="x", padx=10, pady=5)
        bind_text_context_menu(self.txt_preview)

        btn_cancel = ctk.CTkButton(
            bottom_frame, text="取消", fg_color="#F3F4F6", text_color="#1F2937", hover_color="#E5E7EB",
            font=ctk.CTkFont(family="Microsoft YaHei", size=13, weight="bold"), height=36, corner_radius=18,
            command=self.destroy
        )
        btn_cancel.pack(side="left", padx=20, pady=10)

        self.btn_copy = ctk.CTkButton(
            bottom_frame, text="生成提示词", fg_color="#6366F1", text_color="white", hover_color="#4F46E5",
            font=ctk.CTkFont(family="Microsoft YaHei", size=13, weight="bold"), height=36, corner_radius=18,
            command=self.generate_and_copy
        )
        self.btn_copy.pack(side="right", padx=20, pady=10)

        self._speed_up_scroll_recursive(scroll)

    def setup_ui(self):
        if self.script_type == "data_process":
            self._setup_data_process_ui()
            return
        # 底部固定按钮区（设为透明以融合背景，去掉白色块）
        bottom_frame = ctk.CTkFrame(self, height=56, fg_color=("#F9FAFB", "#1A1D24"), corner_radius=0)
        bottom_frame.pack(side="bottom", fill="x", pady=0)

        # 滚动内容区
        scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        scroll.pack(fill="both", expand=True, padx=20, pady=(15, 6))

        self.prompt_tabview = ctk.CTkTabview(
            scroll,
            fg_color=("#FFFFFF", "#1F232B"),
            segmented_button_fg_color=("#E5E7EB", "#2F3541"),
            segmented_button_selected_color=("#6366F1", "#6366F1"),
            segmented_button_selected_hover_color=("#4F46E5", "#4F46E5"),
            segmented_button_unselected_color=("#E5E7EB", "#2F3541"),
            segmented_button_unselected_hover_color=("#D1D5DB", "#374151"),
            text_color=("#111827", "#F9FAFB"),
            corner_radius=14,
            command=self._on_prompt_tab_changed,
        )
        self.prompt_tabview.pack(fill="x", padx=0, pady=(0, 10))
        manual_tab = self.prompt_tabview.add("参数选项")
        target_tab = self.prompt_tabview.add("目标导向")
        agent_tab = self.prompt_tabview.add("Agent协作")
        for tab in (manual_tab, target_tab, agent_tab):
            tab.configure(fg_color="transparent")
        self._update_prompt_tab_text_colors()

        # 1-6. 下拉选单配置区（一行两个，共三行，节省空间）
        # Row 1: 1. 脚本用途 & 2. 数据范围
        row1 = ctk.CTkFrame(manual_tab, fg_color="transparent")
        row1.pack(fill="x", pady=6)
        row1.columnconfigure(0, weight=1, uniform="row1")
        row1.columnconfigure(1, weight=1, uniform="row1")

        # Col 1: 脚本用途
        col1_1 = ctk.CTkFrame(row1, fg_color="transparent")
        col1_1.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        ctk.CTkLabel(col1_1, text="1. 脚本用途", font=ctk.CTkFont(family="Microsoft YaHei", size=13, weight="bold"), text_color="#374151").pack(anchor="w", pady=(0, 2))
        self.var_purpose = ctk.StringVar(value="绘制 F0 曲线图")
        purposes = ["绘制 F0 曲线图", "绘制 F0 分布图", "绘制 F1/F2 元音空间图", "绘制共振峰轨迹图", "绘制异常/质量检查图", "自定义图表"]
        self._make_option_menu(col1_1, purposes, self.var_purpose)

        # Col 2: 数据范围
        col1_2 = ctk.CTkFrame(row1, fg_color="transparent")
        col1_2.grid(row=0, column=1, sticky="nsew", padx=(10, 0))
        ctk.CTkLabel(col1_2, text="2. 数据范围", font=ctk.CTkFont(family="Microsoft YaHei", size=13, weight="bold"), text_color="#374151").pack(anchor="w", pady=(0, 2))
        self.var_scope = ctk.StringVar(value="只使用纳入分析的条目")
        scopes = ["只使用纳入分析的条目", "包含已排除条目", "只使用当前发音人", "使用全部发音人", "只使用当前选中分组", "手动指定分组"]
        self._make_option_menu(col1_2, scopes, self.var_scope, command=self.on_scope_change)

        self.frame_custom_scope = ctk.CTkFrame(col1_2, fg_color="transparent")
        self.entry_custom_scope = ctk.CTkEntry(
            self.frame_custom_scope,
            placeholder_text="输入指定的分组名称，多个以逗号分隔...",
            height=36,
            corner_radius=18,
            font=ctk.CTkFont(family="Microsoft YaHei", size=13),
            fg_color=("#F9FAFB", "#262930"),
            border_color=("#D1D5DB", "#374151"),
            text_color=("#111827", "#F9FAFB")
        )
        self.entry_custom_scope.pack(fill="x", pady=2)

        # Row 2: 3. 分组方式 & 4. 图表形式
        row2 = ctk.CTkFrame(manual_tab, fg_color="transparent")
        row2.pack(fill="x", pady=6)
        row2.columnconfigure(0, weight=1, uniform="row2")
        row2.columnconfigure(1, weight=1, uniform="row2")

        # Col 1: 分组方式
        col2_1 = ctk.CTkFrame(row2, fg_color="transparent")
        col2_1.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        ctk.CTkLabel(col2_1, text="3. 分组方式", font=ctk.CTkFont(family="Microsoft YaHei", size=13, weight="bold"), text_color="#374151").pack(anchor="w", pady=(0, 2))
        self.var_grouping = ctk.StringVar(value="按声调/分组")
        groupings = ["按声调/分组", "按发音人", "按音节位置", "按分析模式", "不分组", "自定义分组字段"]
        self._make_option_menu(col2_1, groupings, self.var_grouping, command=self.on_grouping_change)

        self.frame_custom_grouping = ctk.CTkFrame(col2_1, fg_color="transparent")
        self.entry_custom_grouping = ctk.CTkEntry(
            self.frame_custom_grouping,
            placeholder_text="输入自定义的分组字典键名...",
            height=36,
            corner_radius=18,
            font=ctk.CTkFont(family="Microsoft YaHei", size=13),
            fg_color=("#F9FAFB", "#262930"),
            border_color=("#D1D5DB", "#374151"),
            text_color=("#111827", "#F9FAFB")
        )
        self.entry_custom_grouping.pack(fill="x", pady=2)

        # Col 2: 图表形式
        col2_2 = ctk.CTkFrame(row2, fg_color="transparent")
        col2_2.grid(row=0, column=1, sticky="nsew", padx=(10, 0))
        ctk.CTkLabel(col2_2, text="4. 图表形式", font=ctk.CTkFont(family="Microsoft YaHei", size=13, weight="bold"), text_color="#374151").pack(anchor="w", pady=(0, 2))
        self.var_chart_style = ctk.StringVar(value="折线图")
        styles = ["折线图", "散点图", "箱线图", "小提琴图", "热力图", "轨迹图", "多子图分面", "自定义"]
        self._make_option_menu(col2_2, styles, self.var_chart_style)

        # Row 3: 5. 横轴 & 6. 纵轴
        row3 = ctk.CTkFrame(manual_tab, fg_color="transparent")
        row3.pack(fill="x", pady=6)
        row3.columnconfigure(0, weight=1, uniform="row3")
        row3.columnconfigure(1, weight=1, uniform="row3")

        # Col 1: 横轴
        col3_1 = ctk.CTkFrame(row3, fg_color="transparent")
        col3_1.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        ctk.CTkLabel(col3_1, text="5. 横轴", font=ctk.CTkFont(family="Microsoft YaHei", size=13, weight="bold"), text_color="#374151").pack(anchor="w", pady=(0, 2))
        self.var_xaxis = ctk.StringVar(value="归一化时间 0-1")
        xaxes = ["归一化时间 0-1", "真实时间 秒", "采样点序号", "音节位置", "自定义"]
        self._make_option_menu(col3_1, xaxes, self.var_xaxis)

        # Col 2: 纵轴
        col3_2 = ctk.CTkFrame(row3, fg_color="transparent")
        col3_2.grid(row=0, column=1, sticky="nsew", padx=(10, 0))
        ctk.CTkLabel(col3_2, text="6. 纵轴", font=ctk.CTkFont(family="Microsoft YaHei", size=13, weight="bold"), text_color="#374151").pack(anchor="w", pady=(0, 2))
        self.var_yaxis = ctk.StringVar(value="F0 Hz")
        yaxes = ["F0 Hz", "F0 T 值", "F1 Hz", "F2 Hz", "时长", "自定义"]
        self._make_option_menu(col3_2, yaxes, self.var_yaxis)

        # 7. 统计处理
        self._section_label(manual_tab, "7. 统计处理")
        stats_frame = ctk.CTkFrame(manual_tab, fg_color="transparent")
        stats_frame.pack(fill="x", padx=10, pady=5)

        stats_options = ["绘制个体曲线", "绘制均值", "绘制中位数", "绘制标准差阴影", "绘制置信区间", "忽略 NaN", "过滤明显异常值"]
        self.var_stats = {}
        for idx, opt in enumerate(stats_options):
            self.var_stats[opt] = ctk.BooleanVar(value=True if opt in ["绘制均值", "绘制标准差阴影", "忽略 NaN"] else False)
            cb = ctk.CTkCheckBox(
                stats_frame, text=opt, variable=self.var_stats[opt], height=24,
                checkbox_width=18, checkbox_height=18,
                corner_radius=1000, border_width=2,
                fg_color=("#2563EB", "#1D4ED8"), hover_color=("#9CA3AF", "#4B5563"), border_color=("#4B5563", "#9CA3AF"),
                checkmark_color="#FFFFFF", text_color=("#1F2937", "#E5E7EB"),
                font=ctk.CTkFont(family="Microsoft YaHei", size=12)
            )
            cb.grid(row=idx//2, column=idx%2, sticky="w", padx=5, pady=4)

        # 8. 输出要求
        self._section_label(manual_tab, "8. 输出要求")
        out_frame = ctk.CTkFrame(manual_tab, fg_color="transparent")
        out_frame.pack(fill="x", padx=10, pady=5)

        # 标题
        ctk.CTkLabel(out_frame, text="图表标题:", font=ctk.CTkFont(family="Microsoft YaHei", size=12)).grid(row=0, column=0, sticky="w", pady=4)
        self.entry_title = ctk.CTkEntry(
            out_frame,
            placeholder_text="例如：F0 均值图",
            height=36,
            corner_radius=18,
            font=ctk.CTkFont(family="Microsoft YaHei", size=13),
            fg_color=("#F9FAFB", "#262930"),
            border_color=("#D1D5DB", "#374151"),
            text_color=("#111827", "#F9FAFB")
        )
        self.entry_title.insert(0, "F0 分组均值折线图")
        self.entry_title.grid(row=0, column=1, sticky="ew", padx=(10, 0), pady=4)

        # 文件名
        ctk.CTkLabel(out_frame, text="文件名:", font=ctk.CTkFont(family="Microsoft YaHei", size=12)).grid(row=1, column=0, sticky="w", pady=4)
        self.entry_filename = ctk.CTkEntry(
            out_frame,
            placeholder_text="例如：contour.png",
            height=36,
            corner_radius=18,
            font=ctk.CTkFont(family="Microsoft YaHei", size=13),
            fg_color=("#F9FAFB", "#262930"),
            border_color=("#D1D5DB", "#374151"),
            text_color=("#111827", "#F9FAFB")
        )
        self.entry_filename.insert(0, "contour.png")
        self.entry_filename.grid(row=1, column=1, sticky="ew", padx=(10, 0), pady=4)

        # 复选框样式修改为一致的圆形
        self.var_output_table = ctk.BooleanVar(value=False)
        self.var_show_legend = ctk.BooleanVar(value=True)
        self.var_use_chinese = ctk.BooleanVar(value=True)

        cb_table = ctk.CTkCheckBox(
            out_frame, text="同时输出数据表", variable=self.var_output_table, height=24,
            checkbox_width=18, checkbox_height=18,
            corner_radius=1000, border_width=2,
            fg_color=("#2563EB", "#1D4ED8"), hover_color=("#9CA3AF", "#4B5563"), border_color=("#4B5563", "#9CA3AF"),
            checkmark_color="#FFFFFF", text_color=("#1F2937", "#E5E7EB"),
            font=ctk.CTkFont(family="Microsoft YaHei", size=12)
        )
        cb_table.grid(row=2, column=0, columnspan=2, sticky="w", pady=4)

        cb_legend = ctk.CTkCheckBox(
            out_frame, text="显示图例", variable=self.var_show_legend, height=24,
            checkbox_width=18, checkbox_height=18,
            corner_radius=1000, border_width=2,
            fg_color=("#2563EB", "#1D4ED8"), hover_color=("#9CA3AF", "#4B5563"), border_color=("#4B5563", "#9CA3AF"),
            checkmark_color="#FFFFFF", text_color=("#1F2937", "#E5E7EB"),
            font=ctk.CTkFont(family="Microsoft YaHei", size=12)
        )
        cb_legend.grid(row=3, column=0, sticky="w", pady=4)

        cb_chinese = ctk.CTkCheckBox(
            out_frame, text="图中使用中文标签", variable=self.var_use_chinese, height=24,
            checkbox_width=18, checkbox_height=18,
            corner_radius=1000, border_width=2,
            fg_color=("#2563EB", "#1D4ED8"), hover_color=("#9CA3AF", "#4B5563"), border_color=("#4B5563", "#9CA3AF"),
            checkmark_color="#FFFFFF", text_color=("#1F2937", "#E5E7EB"),
            font=ctk.CTkFont(family="Microsoft YaHei", size=12)
        )
        cb_chinese.grid(row=3, column=1, sticky="w", pady=4)

        out_frame.grid_columnconfigure(1, weight=1)

        # 9. 用户自定义需求
        self._section_label(manual_tab, "9. 补充具体需求")
        self.txt_custom = ctk.CTkTextbox(
            manual_tab, height=80,
            border_width=1,
            border_color=("#D1D5DB", "#374151"),
            fg_color=("#FFFFFF", "#262930"),
            text_color=("#111827", "#F9FAFB")
        )
        self.txt_custom.pack(fill="x", padx=10, pady=5)
        bind_text_context_menu(self.txt_custom)

        self._build_goal_oriented_tab(target_tab)
        self._build_agent_tab(agent_tab)

        # 10. 提示词预览
        preview_header = ctk.CTkFrame(scroll, fg_color="transparent")
        preview_header.pack(fill="x", padx=10, pady=(15, 2))

        preview_title = ctk.CTkLabel(preview_header, text="10. 提示词生成预览", font=ctk.CTkFont(family="Microsoft YaHei", size=13, weight="bold"), text_color="#374151")
        preview_title.pack(side="left", anchor="w")

        # 复制按钮
        self.btn_copy_prompt_preview = ctk.CTkButton(
            preview_header,
            text="复制",
            width=54,
            height=24,
            font=ctk.CTkFont(family=self.font_family, size=11),
            corner_radius=12,
            fg_color=self.colors["primary_soft"],
            text_color=self.colors["primary"],
            hover_color=self.colors["border_strong"],
            command=self.copy_prompt_preview
        )
        self.btn_copy_prompt_preview.pack(side="right", padx=(5, 0))

        self.txt_preview = ctk.CTkTextbox(
            scroll, height=150, font=ctk.CTkFont(family="Consolas", size=11),
            border_width=1,
            border_color=("#D1D5DB", "#374151"),
            fg_color=("#FFFFFF", "#262930"),
            text_color=("#111827", "#F9FAFB")
        )
        self.txt_preview.pack(fill="x", padx=10, pady=5)
        bind_text_context_menu(self.txt_preview)

        self._speed_up_scroll_recursive(scroll)

        # 底部按钮 (padding 调小为 10，贴合底部并美化布局)
        btn_cancel = ctk.CTkButton(
            bottom_frame, text="取消", fg_color="#F3F4F6", text_color="#1F2937", hover_color="#E5E7EB",
            font=ctk.CTkFont(family="Microsoft YaHei", size=13, weight="bold"), height=36, corner_radius=18,
            command=self.destroy
        )
        btn_cancel.pack(side="left", padx=20, pady=10)

        self.btn_copy = ctk.CTkButton(
            bottom_frame, text="生成提示词", fg_color="#6366F1", text_color="white", hover_color="#4F46E5",
            font=ctk.CTkFont(family="Microsoft YaHei", size=13, weight="bold"), height=36, corner_radius=18,
            command=self.generate_and_copy
        )
        self.btn_copy.pack(side="right", padx=20, pady=10)

        # 初始打包隐藏
        self.on_scope_change(self.var_scope.get())
        self.on_grouping_change(self.var_grouping.get())

    def _build_goal_oriented_tab(self, target_tab):
        intro = ctk.CTkLabel(
            target_tab,
            text="这个页面按研究目标生成提示词。你只需要说明想解决什么问题，AI 会自己选择合适的图表形式和统计处理。",
            font=ctk.CTkFont(family="Microsoft YaHei", size=12),
            text_color="#64748B",
            wraplength=520,
            justify="left"
        )
        intro.pack(anchor="w", padx=10, pady=(8, 10))

        row1 = ctk.CTkFrame(target_tab, fg_color="transparent")
        row1.pack(fill="x", pady=6)
        row1.columnconfigure(0, weight=1, uniform="target1")
        row1.columnconfigure(1, weight=1, uniform="target1")

        col_goal = ctk.CTkFrame(row1, fg_color="transparent")
        col_goal.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        ctk.CTkLabel(col_goal, text="1. 想实现的目标", font=ctk.CTkFont(family="Microsoft YaHei", size=13, weight="bold"), text_color="#374151").pack(anchor="w", pady=(0, 2))
        self.var_target_goal = ctk.StringVar(value="比较不同分组的声学差异")
        target_goals = [
            "比较不同分组的声学差异",
            "比较不同发音人的差异",
            "展示单个发音人的整体趋势",
            "展示一个词或一组词的轨迹",
            "寻找异常或质量问题",
            "制作论文用汇总图",
            "探索数据中的模式",
            "自定义目标",
        ]
        self._make_option_menu(col_goal, target_goals, self.var_target_goal)

        col_data = ctk.CTkFrame(row1, fg_color="transparent")
        col_data.grid(row=0, column=1, sticky="nsew", padx=(10, 0))
        ctk.CTkLabel(col_data, text="2. 关注的数据", font=ctk.CTkFont(family="Microsoft YaHei", size=13, weight="bold"), text_color="#374151").pack(anchor="w", pady=(0, 2))
        self.var_target_data = ctk.StringVar(value="使用全部纳入分析条目")
        target_data = [
            "使用全部纳入分析条目",
            "只看某些分组",
            "只看某些发音人",
            "比较两个或多个条件",
            "包含已排除条目做质量检查",
            "自定义范围",
        ]
        self._make_option_menu(col_data, target_data, self.var_target_data)

        row2 = ctk.CTkFrame(target_tab, fg_color="transparent")
        row2.pack(fill="x", pady=6)
        row2.columnconfigure(0, weight=1, uniform="target2")
        row2.columnconfigure(1, weight=1, uniform="target2")

        col_measure = ctk.CTkFrame(row2, fg_color="transparent")
        col_measure.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        ctk.CTkLabel(col_measure, text="3. 主要观察指标", font=ctk.CTkFont(family="Microsoft YaHei", size=13, weight="bold"), text_color="#374151").pack(anchor="w", pady=(0, 2))
        self.var_target_measure = ctk.StringVar(value="由 AI 根据目标选择")
        measures = ["由 AI 根据目标选择", "F0 走势", "F0 分布", "F0 T 值", "F1/F2 空间", "共振峰轨迹", "时长", "多个指标组合"]
        self._make_option_menu(col_measure, measures, self.var_target_measure)

        col_output = ctk.CTkFrame(row2, fg_color="transparent")
        col_output.grid(row=0, column=1, sticky="nsew", padx=(10, 0))
        ctk.CTkLabel(col_output, text="4. 输出风格", font=ctk.CTkFont(family="Microsoft YaHei", size=13, weight="bold"), text_color="#374151").pack(anchor="w", pady=(0, 2))
        self.var_target_output = ctk.StringVar(value="优先生成一张清晰图表")
        target_outputs = ["优先生成一张清晰图表", "图表和统计表都要", "多张分面图", "论文风格高分辨率图", "教学展示风格", "自定义输出"]
        self._make_option_menu(col_output, target_outputs, self.var_target_output)

        self._section_label(target_tab, "5. 具体目标")
        self.placeholder_target_goal = "例如：我想比较阴平、阳平、上声、去声四组在归一化时间上的 F0 走势差异，最好能看出均值和组内离散程度。"
        self.txt_target_goal = self._make_textbox(
            target_tab,
            height=90,
            placeholder=self.placeholder_target_goal
        )

        self._section_label(target_tab, "6. 指定对象或筛选条件")
        self.placeholder_target_scope = "例如：只看张三和李四；只看“实验组A”；排除已经标记忽略的条目。没有特殊限制可以留空。"
        self.txt_target_scope = self._make_textbox(
            target_tab,
            height=70,
            placeholder=self.placeholder_target_scope
        )

        self._section_label(target_tab, "7. 输出偏好")
        self.placeholder_target_constraints = "例如：颜色要区分清楚；适合论文插图；图例放在右上；如果数据不足要在图里说明。"
        self.txt_target_constraints = self._make_textbox(
            target_tab,
            height=70,
            placeholder=self.placeholder_target_constraints
        )

    def _build_agent_tab(self, agent_tab):
        is_data_process = self.script_type == "data_process"
        intro = ctk.CTkLabel(
            agent_tab,
            text=(
                "这个页面会把 AI 设定成数据处理 Agent：先询问工程再加工目的，推荐重分析、重组、音频处理或元数据合并方案，等你确认后再输出 Toolkit 可运行代码。"
                if is_data_process else
                "这个页面会把 AI 设定成协作 Agent：先询问研究目的并推荐图表，等你确认后再输出 Toolkit 可运行代码。"
            ),
            font=ctk.CTkFont(family="Microsoft YaHei", size=12),
            text_color="#64748B",
            wraplength=520,
            justify="left"
        )
        intro.pack(anchor="w", padx=10, pady=(8, 10))

        row1 = ctk.CTkFrame(agent_tab, fg_color="transparent")
        row1.pack(fill="x", pady=6)
        row1.columnconfigure(0, weight=1, uniform="agent1")
        row1.columnconfigure(1, weight=1, uniform="agent1")

        col_detail = ctk.CTkFrame(row1, fg_color="transparent")
        col_detail.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        ctk.CTkLabel(col_detail, text="1. 提示词详略", font=ctk.CTkFont(family="Microsoft YaHei", size=13, weight="bold"), text_color="#374151").pack(anchor="w", pady=(0, 2))
        self.var_agent_detail = ctk.StringVar(value="详细" if is_data_process else "精简")
        self._make_option_menu(col_detail, ["精简", "详细"], self.var_agent_detail)

        col_count = ctk.CTkFrame(row1, fg_color="transparent")
        col_count.grid(row=0, column=1, sticky="nsew", padx=(10, 0))
        self.var_agent_chart_count = ctk.StringVar(value="5")
        if is_data_process:
            ctk.CTkLabel(col_count, text="2. 方案数量", font=ctk.CTkFont(family="Microsoft YaHei", size=13, weight="bold"), text_color="#374151").pack(anchor="w", pady=(0, 2))
            self.var_agent_plan_count = ctk.StringVar(value="4")
            self._make_option_menu(col_count, ["3", "4", "5"], self.var_agent_plan_count)
        else:
            ctk.CTkLabel(col_count, text="2. 推荐图表数量", font=ctk.CTkFont(family="Microsoft YaHei", size=13, weight="bold"), text_color="#374151").pack(anchor="w", pady=(0, 2))
            self._make_option_menu(col_count, ["3", "5", "6"], self.var_agent_chart_count)

        row2 = ctk.CTkFrame(agent_tab, fg_color="transparent")
        row2.pack(fill="x", pady=6)
        row2.columnconfigure(0, weight=1)

        col_summary = ctk.CTkFrame(row2, fg_color="transparent")
        col_summary.grid(row=0, column=0, sticky="nsew")
        ctk.CTkLabel(col_summary, text="3. 工程摘要", font=ctk.CTkFont(family="Microsoft YaHei", size=13, weight="bold"), text_color="#374151").pack(anchor="w", pady=(0, 2))
        self.var_agent_summary = ctk.StringVar(value="包含精简工程摘要")
        self._make_option_menu(col_summary, ["包含精简工程摘要", "包含详细工程摘要", "不附带工程摘要"], self.var_agent_summary)

        self._section_label(agent_tab, "4. 给 Agent 的额外提示")
        self.placeholder_agent_extra = (
            "可选。例如：优先帮我重算失败条目；按词项标签拆成干净工程；裁剪每个条目音频；把 CSV 里的实验条件合并进 item_meta。"
            if is_data_process else
            "可选。例如：优先考虑论文图；先帮我判断哪些图适合当前工程；不要推荐结构类图表，除非数据里真的有结构字段。"
        )
        self.txt_agent_extra = self._make_textbox(
            agent_tab,
            height=110,
            placeholder=self.placeholder_agent_extra
        )

    def on_scope_change(self, val):
        if val == "手动指定分组":
            self.frame_custom_scope.pack(fill="x")
        else:
            self.frame_custom_scope.pack_forget()

    def on_grouping_change(self, val):
        if val == "自定义分组字段":
            self.frame_custom_grouping.pack(fill="x")
        else:
            self.frame_custom_grouping.pack_forget()

    def generate_prompt_dict(self):
        active_tab = self.prompt_tabview.get() if hasattr(self, "prompt_tabview") else "参数选项"
        if active_tab == "Agent协作":
            extra = self._textbox_value_without_placeholder(self.txt_agent_extra, self.placeholder_agent_extra)
            if self.script_type == "data_process":
                return {
                    "script_type": "data_process",
                    "prompt_mode": "Agent协作",
                    "goal": "让 AI 先询问工程再加工目的、推荐数据处理方案、再写 Toolkit 数据处理脚本",
                    "custom_desc": extra,
                    "agent_detail_level": self.var_agent_detail.get(),
                    "agent_plan_count": self.var_agent_plan_count.get() if hasattr(self, "var_agent_plan_count") else "4",
                    "agent_project_summary_mode": self.var_agent_summary.get(),
                    "agent_include_project_summary": self.var_agent_summary.get() != "不附带工程摘要",
                    "agent_detailed_project_summary": self.var_agent_summary.get() == "包含详细工程摘要",
                }
            return {
                "script_type": "chart",
                "prompt_mode": "Agent协作",
                "goal": "让 AI 先询问目的、推荐图表、再写 Toolkit 脚本",
                "data_range": "由用户与 Agent 对话确认",
                "group_by": "由 Agent 根据目标推荐",
                "chart_style": "由 Agent 推荐多种候选",
                "x_axis": "由 Agent 根据图表类型决定",
                "y_axis": "由 Agent 根据图表类型决定",
                "stats": ["由 Agent 推荐并在用户确认后落实"],
                "title": "Agent 协作脚本",
                "filename": "agent_chart.png",
                "output_table": False,
                "show_legend": True,
                "use_chinese": True,
                "custom_desc": extra,
                "agent_detail_level": self.var_agent_detail.get(),
                "agent_chart_count": self.var_agent_chart_count.get(),
                "agent_project_summary_mode": self.var_agent_summary.get(),
                "agent_include_project_summary": self.var_agent_summary.get() != "不附带工程摘要",
                "agent_detailed_project_summary": self.var_agent_summary.get() == "包含详细工程摘要",
            }

        if active_tab == "目标导向":
            goal_detail = self._textbox_value_without_placeholder(self.txt_target_goal, self.placeholder_target_goal)
            scope_detail = self._textbox_value_without_placeholder(self.txt_target_scope, self.placeholder_target_scope)
            constraints = self._textbox_value_without_placeholder(self.txt_target_constraints, self.placeholder_target_constraints)
            target_goal = self.var_target_goal.get()
            target_measure = self.var_target_measure.get()
            target_output = self.var_target_output.get()
            custom_lines = [
                f"目标导向模式：{target_goal}",
                f"关注数据：{self.var_target_data.get()}",
                f"主要观察指标：{target_measure}",
                f"输出风格：{target_output}",
            ]
            if goal_detail:
                custom_lines.append(f"用户具体目标：{goal_detail}")
            if scope_detail:
                custom_lines.append(f"筛选条件或指定对象：{scope_detail}")
            if constraints:
                custom_lines.append(f"输出偏好：{constraints}")
            return {
                "script_type": "chart",
                "prompt_mode": "目标导向",
                "goal": target_goal,
                "data_range": self.var_target_data.get(),
                "group_by": "由 AI 根据目标自动选择",
                "chart_style": "由 AI 根据目标自动选择",
                "x_axis": "由 AI 根据目标自动选择",
                "y_axis": target_measure,
                "stats": ["由 AI 根据目标选择合适统计处理"],
                "title": target_goal,
                "filename": "goal_oriented_chart.png",
                "output_table": target_output == "图表和统计表都要",
                "show_legend": True,
                "use_chinese": True,
                "custom_desc": "\n".join(custom_lines),
                "target_goal_detail": goal_detail,
                "target_scope_detail": scope_detail,
                "target_constraints": constraints,
            }

        stats_selected = [k for k, v in self.var_stats.items() if v.get()]

        scope = self.var_scope.get()
        if scope == "手动指定分组":
            custom_grp = self.entry_custom_scope.get().strip()
            scope = f"手动指定分组 ({custom_grp or '未指定'})"

        grouping = self.var_grouping.get()
        if grouping == "自定义分组字段":
            custom_key = self.entry_custom_grouping.get().strip()
            grouping = f"自定义分组字段 ({custom_key or '未指定'})"

        selections = {
            "script_type": "chart",
            "prompt_mode": "参数选项",
            "goal": self.var_purpose.get(),
            "data_range": scope,
            "group_by": grouping,
            "chart_style": self.var_chart_style.get(),
            "x_axis": self.var_xaxis.get(),
            "y_axis": self.var_yaxis.get(),
            "stats": stats_selected,
            "title": self.entry_title.get().strip() or "自定义图表",
            "filename": self.entry_filename.get().strip() or "custom_chart.png",
            "output_table": self.var_output_table.get(),
            "show_legend": self.var_show_legend.get(),
            "use_chinese": self.var_use_chinese.get(),
            "custom_desc": self.txt_custom.get("1.0", tk.END).strip()
        }
        return selections

    def copy_prompt_preview(self):
        try:
            prompt_text = self.txt_preview.get("1.0", tk.END).strip()
            self.clipboard_clear()
            self.clipboard_append(prompt_text)
            self.update()

            if hasattr(self, "btn_copy_prompt_preview"):
                self.btn_copy_prompt_preview.configure(
                    text="✔已复制",
                    fg_color=self.colors.get("success_soft", "#D1FAE5"),
                    text_color=self.colors.get("success", "#10B981")
                )
                def reset():
                    try:
                        self.btn_copy_prompt_preview.configure(
                            text="复制",
                            fg_color=self.colors.get("primary_soft", "#DBEAFE"),
                            text_color=self.colors.get("primary", "#2563EB")
                        )
                    except Exception:
                        pass
                self.after(1000, reset)
        except Exception as e:
            messagebox.showerror("错误", f"复制失败：{e}")

    def generate_preview(self):
        from modules.script_prompt import generate_ai_prompt
        selections = self.generate_prompt_dict()
        prompt_text = generate_ai_prompt(self.project_data, selections)

        self.txt_preview.delete("1.0", tk.END)
        self.txt_preview.insert("1.0", prompt_text)

    def generate_and_copy(self):
        from modules.script_prompt import generate_ai_prompt
        selections = self.generate_prompt_dict()
        prompt_text = generate_ai_prompt(self.project_data, selections)

        self.txt_preview.delete("1.0", tk.END)
        self.txt_preview.insert("1.0", prompt_text)

        try:
            self.clipboard_clear()
            self.clipboard_append(prompt_text)
            self.update()

            if hasattr(self, "btn_copy"):
                self.btn_copy.configure(
                    text="✔已生成并复制",
                    fg_color=self.colors.get("success", "#10B981"),
                    hover_color=self.colors.get("success_hover", "#059669")
                )
                def reset():
                    try:
                        self.btn_copy.configure(
                            text="生成提示词",
                            fg_color="#6366F1",
                            hover_color="#4F46E5"
                        )
                    except Exception:
                        pass
                self.after(1000, reset)
        except Exception:
            pass

