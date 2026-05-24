import os
import math
import numpy as np
import tkinter as tk
import customtkinter as ctk
from tkinter import messagebox, filedialog
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import parselmouth
from scipy.stats import gaussian_kde
from .data_utils import split_into_syllables, get_export_text_for_item

class AcousticChartExportDialog(ctk.CTkToplevel):
    def __init__(self, parent, app=None, project_tree=None, mode='single', all_speakers=None):
        super().__init__(parent)
        self.parent = parent
        self.app = app
        self.project_tree = project_tree
        self.initial_mode = mode
        self.all_speakers = all_speakers or []
        
        self.title("声学图表导出 - 声视化工具箱")
        self.geometry("980x640")
        self.resizable(True, True)
        self.transient(parent)
        self.grab_set()
        
        # Color Palette
        self.colors = ['#2563EB', '#DC2626', '#16A34A', '#9333EA', '#EA580C', '#0891B2', '#CA8A04', '#6366F1']
        
        # Load active speaker's data items as default fallback
        self.sm = getattr(self.app, 'speaker_manager', None)
        self.active_speaker = self.sm.get_active_speaker() if self.sm else None
        
        # Fonts
        self.font_title = ctk.CTkFont(family="Microsoft YaHei", size=15, weight="bold")
        self.font_main = ctk.CTkFont(family="Microsoft YaHei", size=12)
        self.font_small = ctk.CTkFont(family="Microsoft YaHei", size=11)
        
        # Dropdown OptionMenu styling to match speaker_dropdown
        self.dropdown_kwargs = {
            "font": self.font_main,
            "fg_color": ("#F3F4F6", "#374151"),
            "text_color": ("#1F2937", "#E5E7EB"),
            "button_color": ("#F3F4F6", "#374151"),
            "button_hover_color": ("#E5E7EB", "#4B5563"),
            "height": 32,
            "corner_radius": 16
        }
        
        # Variables Setup
        self._init_variables()
        self._init_group_filters()
        
        # GUI Structure
        self._build_gui()
        self._populate_groups_list()
        
        # Render Initial Preview
        self.update_preview()
        
    def _apply_custom_arrow(self, dropdown):
        try:
            orig_draw_arrow = dropdown._draw_engine.draw_dropdown_arrow

            def custom_draw_arrow(*args, **kwargs):
                old_method = dropdown._draw_engine.preferred_drawing_method
                try:
                    dropdown._draw_engine.preferred_drawing_method = "polygon_shapes"
                    res = orig_draw_arrow(*args, **kwargs)
                    try:
                        dropdown._canvas.itemconfigure("dropdown_arrow", width=2)
                    except Exception:
                        pass
                    return res
                finally:
                    dropdown._draw_engine.preferred_drawing_method = old_method

            dropdown._draw_engine.draw_dropdown_arrow = custom_draw_arrow
            dropdown._canvas.delete("dropdown_arrow")
            dropdown._draw(no_color_updates=False)
        except Exception:
            pass

    def _init_variables(self):
        # 1. Chart Type
        self.var_chart_type = ctk.StringVar(value="contour")  # contour, distribution, density, quality, overview_heatmap
        
        # 2. Export Scope
        scope_default = "active"
        if self.initial_mode == 'separate':
            scope_default = "separate"
        elif self.initial_mode == 'integrated':
            scope_default = "integrated"
        self.var_export_scope = ctk.StringVar(value=scope_default)  # active, separate, integrated
        
        # 3. Grouping Basis
        self.var_group_by = ctk.StringVar(value="group")  # group (声调类型), label (词语), speaker (发音人)
        
        # 4. Acoustic Scale
        self.var_scale = ctk.StringVar(value="t_value")  # t_value, hz
        
        # 5. Image Format
        self.var_format = ctk.StringVar(value="png")  # png, svg, pdf
        
        # Dynamic Options - Tone Contour
        self.var_contour_x = ctk.StringVar(value="normalized")  # normalized, duration
        self.var_contour_content = ctk.StringVar(value="average")  # average, average_individual, average_sd_ci
        self.var_contour_facet = ctk.StringVar(value="none")  # none, group, speaker, syllable_position
        
        # Dynamic Options - Tone Distribution
        self.var_dist_type = ctk.StringVar(value="boxplot_violin")  # boxplot_violin, start_mid_end, range, variability
        self.var_dist_style = ctk.StringVar(value="boxplot")  # boxplot, violinplot
        
        # Dynamic Options - Temporal Density (KDE Heatmap)
        self.var_density_bw = ctk.DoubleVar(value=0.15)
        self.var_density_f0_mode = ctk.StringVar(value="percentile")  # percentile, minmax, manual
        self.var_density_p_low = ctk.StringVar(value="5")
        self.var_density_p_high = ctk.StringVar(value="95")
        self.var_density_m_min = ctk.StringVar(value="75")
        self.var_density_m_max = ctk.StringVar(value="600")
        self.var_density_facet = ctk.StringVar(value="none")  # none, group, label
        
        # Dynamic Options - Quality Check
        self.var_qc_view = ctk.StringVar(value="raw_overlay")  # raw_overlay, active_ratio, speaker_means
        
        # Pagination state
        self.current_preview_page = 0
        self.sort_by_count = False

    def _init_group_filters(self):
        # Extract all unique group names across all speakers
        all_entries = self._extract_active_data(self.all_speakers if self.all_speakers else [self.active_speaker])
        self.group_counts = {}
        for entry in all_entries:
            g = entry['group']
            self.group_counts[g] = self.group_counts.get(g, 0) + 1
            
        self.available_groups = sorted(list(self.group_counts.keys()))
        self.group_checkbox_vars = {}
        for g in self.available_groups:
            self.group_checkbox_vars[g] = ctk.BooleanVar(value=True)

    def _build_gui(self):
        # Main Grid Layout: Left Column = Settings, Right Column = Preview
        self.grid_columnconfigure(0, weight=0, minsize=420)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        
        # --- LEFT SIDE: Scrollable Configuration Frame ---
        self.left_scroll = ctk.CTkScrollableFrame(self, width=400, label_text="📊 图表可视化高级设置", label_font=self.font_title, fg_color=("#F9FAFB", "#2D3748"))
        self.left_scroll.grid(row=0, column=0, sticky="nsew", padx=(15, 10), pady=15)
        
        self._build_settings_cards()
        
        # --- RIGHT SIDE: Real-time Live Preview & Action Buttons ---
        self.right_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.right_frame.grid(row=0, column=1, sticky="nsew", padx=(10, 15), pady=15)
        self.right_frame.grid_rowconfigure(0, weight=1)
        self.right_frame.grid_rowconfigure(1, weight=0)
        self.right_frame.grid_rowconfigure(2, weight=0)
        self.right_frame.grid_columnconfigure(0, weight=1)
        
        # Preview Canvas Container
        self.preview_container = ctk.CTkFrame(self.right_frame, fg_color="#F3F4F6", border_width=1, border_color="#D1D5DB")
        self.preview_container.grid(row=0, column=0, sticky="nsew", pady=(0, 15))
        self.preview_container.grid_rowconfigure(0, weight=1)
        self.preview_container.grid_columnconfigure(0, weight=1)
        
        self.preview_lbl = ctk.CTkLabel(self.preview_container, text="正在加载图表预览...", font=self.font_title, text_color="#6B7280")
        self.preview_lbl.grid(row=0, column=0)
        
        # Pagination Frame (Hidden by default, shown when scope is separate)
        self.pagination_frame = ctk.CTkFrame(self.right_frame, fg_color="transparent")
        self.pagination_frame.grid_columnconfigure(0, weight=1)
        self.pagination_frame.grid_columnconfigure(1, weight=0)
        self.pagination_frame.grid_columnconfigure(2, weight=1)
        
        self.btn_prev_page = ctk.CTkButton(
            self.pagination_frame, text="◀ 上一个发音人", width=120, height=30, corner_radius=15,
            fg_color=("#F3F4F6", "#374151"), text_color=("#374151", "#E5E7EB"), hover_color=("#E5E7EB", "#4B5563"),
            font=self.font_main, command=self._prev_page
        )
        self.btn_prev_page.grid(row=0, column=0, padx=10, sticky="e")
        
        self.lbl_page_info = ctk.CTkLabel(self.pagination_frame, text="发音人: - (0/0)", font=self.font_title)
        self.lbl_page_info.grid(row=0, column=1, padx=20)
        
        self.btn_next_page = ctk.CTkButton(
            self.pagination_frame, text="下一个发音人 ▶", width=120, height=30, corner_radius=15,
            fg_color=("#F3F4F6", "#374151"), text_color=("#374151", "#E5E7EB"), hover_color=("#E5E7EB", "#4B5563"),
            font=self.font_main, command=self._next_page
        )
        self.btn_next_page.grid(row=0, column=2, padx=10, sticky="w")
        
        # Bottom Buttons
        self.bottom_frame = ctk.CTkFrame(self.right_frame, fg_color="transparent")
        self.bottom_frame.grid(row=2, column=0, sticky="ew")
        
        ctk.CTkButton(self.bottom_frame, text="🔄 刷新预览", width=120, height=38, corner_radius=19, fg_color="#E5E7EB", text_color="#374151", hover_color="#D1D5DB", font=self.font_main, command=self.update_preview).pack(side=tk.LEFT, padx=5)
        
        self.btn_export = ctk.CTkButton(self.bottom_frame, text="💾 导出所选图表", width=180, height=38, corner_radius=19, font=self.font_title, command=self.on_confirm)
        self.btn_export.pack(side=tk.RIGHT, padx=5)
        
        ctk.CTkButton(self.bottom_frame, text="取消", width=100, height=38, corner_radius=19, fg_color="#F3F4F6", text_color="#4B5563", hover_color="#E5E7EB", font=self.font_main, command=self.destroy).pack(side=tk.RIGHT, padx=5)

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

    def _build_settings_cards(self):
        card_padding = {"padx": 10, "pady": 8}
        
        # --- OPTIONAL: Multi-group warning/suggestion card ---
        if len(self.available_groups) > 8:
            card_warn = ctk.CTkFrame(self.left_scroll, fg_color=("#FFFBEB", "#2A200B"), border_width=1, border_color=("#F59E0B", "#B45309"), corner_radius=12)
            card_warn.pack(fill=tk.X, **card_padding)
            
            ctk.CTkLabel(card_warn, text="⚠️ 多组别优化建议", font=self.font_title, text_color=("#B45309", "#FBBF24")).pack(anchor="w", padx=15, pady=(10, 5))
            
            lbl_warn_text = (
                f"当前数据包含 {len(self.available_groups)} 个组别。如果在一张图里\n"
                f"叠加所有曲线会导致图表极其杂乱、无法阅读。\n"
                f"建议选择以下三种专业优化模式之一："
            )
            ctk.CTkLabel(card_warn, text=lbl_warn_text, font=self.font_small, justify="left", text_color=("#78350F", "#FDE68A")).pack(anchor="w", padx=15, pady=(0, 10))
            
            btn_frame = ctk.CTkFrame(card_warn, fg_color="transparent")
            btn_frame.pack(fill=tk.X, padx=15, pady=(0, 15))
            
            btn_opt1 = ctk.CTkButton(
                btn_frame, text="精选对比", width=95, height=28, corner_radius=14,
                fg_color=("#F59E0B", "#D97706"), text_color="white", hover_color=("#D97706", "#B45309"),
                font=self.font_small, command=self._apply_featured_contrast_mode
            )
            btn_opt1.pack(side=tk.LEFT, padx=3)
            
            btn_opt2 = ctk.CTkButton(
                btn_frame, text="批量图册", width=95, height=28, corner_radius=14,
                fg_color=("#10B981", "#059669"), text_color="white", hover_color=("#059669", "#047857"),
                font=self.font_small, command=self._apply_batch_album_mode
            )
            btn_opt2.pack(side=tk.LEFT, padx=3)
            
            btn_opt3 = ctk.CTkButton(
                btn_frame, text="整体概览", width=95, height=28, corner_radius=14,
                fg_color=("#6366F1", "#4F46E5"), text_color="white", hover_color=("#4F46E5", "#4338CA"),
                font=self.font_small, command=self._apply_overall_overview_mode
            )
            btn_opt3.pack(side=tk.LEFT, padx=3)

        # --- CARD 1: Basic Type & Scope ---
        card1 = ctk.CTkFrame(self.left_scroll, fg_color=("#FFFFFF", "#1E293B"), border_width=1, border_color=("#E5E7EB", "#475569"), corner_radius=12)
        card1.pack(fill=tk.X, **card_padding)
        
        ctk.CTkLabel(card1, text="🔹 基础类型与范围", font=self.font_title).pack(anchor="w", padx=15, pady=(10, 5))
        
        # Chart Type OptionMenu
        ctk.CTkLabel(card1, text="图表类型:", font=self.font_small).pack(anchor="w", padx=15)
        self.combo_type = ctk.CTkOptionMenu(card1, values=["声调轮廓图", "声调分布图", "时序密度图", "数据质量检查", "声调组别概览图"], command=self._on_type_changed, **self.dropdown_kwargs)
        self.combo_type.set("声调轮廓图")
        self.combo_type.pack(fill=tk.X, padx=15, pady=(0, 10))
        self._apply_custom_arrow(self.combo_type)
        
        # Export Intention OptionMenu
        ctk.CTkLabel(card1, text="导出意图 (自动匹配推荐设置):", font=self.font_small).pack(anchor="w", padx=15)
        self.combo_intention = ctk.CTkOptionMenu(
            card1, values=["论文主图 (清晰对比)", "附录图册 (完整数据)", "数据诊断 (质量排查)"],
            command=self._on_intention_changed, **self.dropdown_kwargs
        )
        self.combo_intention.set("论文主图 (清晰对比)")
        self.combo_intention.pack(fill=tk.X, padx=15, pady=(0, 10))
        self._apply_custom_arrow(self.combo_intention)
        
        # Export Scope Options
        ctk.CTkLabel(card1, text="导出范围:", font=self.font_small).pack(anchor="w", padx=15)
        self.combo_scope = ctk.CTkOptionMenu(card1, values=["仅当前发音人", "所有发音人(分别导出)", "所有发音人(整合导出)"], command=self._on_scope_changed, **self.dropdown_kwargs)
        # Match original menu selected mode
        if self.initial_mode == 'separate':
            self.combo_scope.set("所有发音人(分别导出)")
        elif self.initial_mode == 'integrated':
            self.combo_scope.set("所有发音人(整合导出)")
        else:
            self.combo_scope.set("仅当前发音人")
        self.combo_scope.pack(fill=tk.X, padx=15, pady=(0, 10))
        self._apply_custom_arrow(self.combo_scope)
        
        # Speaker Management Restriction
        if not self.all_speakers or len(self.all_speakers) <= 1:
            self.combo_scope.configure(state="disabled")

        # --- CARD 2: Visual Parameters ---
        card2 = ctk.CTkFrame(self.left_scroll, fg_color=("#FFFFFF", "#1E293B"), border_width=1, border_color=("#E5E7EB", "#475569"), corner_radius=12)
        card2.pack(fill=tk.X, **card_padding)
        
        ctk.CTkLabel(card2, text="🔹 核心维度与尺度", font=self.font_title).pack(anchor="w", padx=15, pady=(10, 5))
        
        # Group By
        ctk.CTkLabel(card2, text="分组依据 / 曲线配色:", font=self.font_small).pack(anchor="w", padx=15)
        self.combo_groupby = ctk.CTkOptionMenu(card2, values=["按声调类型", "按词语", "按发音人"], command=lambda _: self.update_preview(), **self.dropdown_kwargs)
        self.combo_groupby.set("按声调类型")
        self.combo_groupby.pack(fill=tk.X, padx=15, pady=(0, 10))
        self._apply_custom_arrow(self.combo_groupby)
        
        # Acoustic Scale
        ctk.CTkLabel(card2, text="声学尺度 (纵轴单位):", font=self.font_small).pack(anchor="w", padx=15)
        self.combo_scale = ctk.CTkOptionMenu(card2, values=["T 值 (五度标调)", "Hz (基频绝对频率)"], command=lambda _: self.update_preview(), **self.dropdown_kwargs)
        self.combo_scale.set("T 值 (五度标调)")
        self.combo_scale.pack(fill=tk.X, padx=15, pady=(0, 10))
        self._apply_custom_arrow(self.combo_scale)
        
        # Image Format
        ctk.CTkLabel(card2, text="图像导出格式:", font=self.font_small).pack(anchor="w", padx=15)
        self.combo_format = ctk.CTkOptionMenu(card2, values=["PNG 图片 (.png)", "SVG 矢量图 (.svg)", "PDF 文档 (.pdf)"], **self.dropdown_kwargs)
        self.combo_format.set("PNG 图片 (.png)")
        self.combo_format.pack(fill=tk.X, padx=15, pady=(0, 15))
        self._apply_custom_arrow(self.combo_format)

        # --- CARD 3: Dynamic Options Frame ---
        self.dynamic_card = ctk.CTkFrame(self.left_scroll, fg_color=("#FFFFFF", "#1E293B"), border_width=1, border_color=("#E5E7EB", "#475569"), corner_radius=12)
        self.dynamic_card.pack(fill=tk.X, **card_padding)
        
        self.dynamic_title = ctk.CTkLabel(self.dynamic_card, text="⚙️ 声调轮廓图专有选项", font=self.font_title)
        self.dynamic_title.pack(anchor="w", padx=15, pady=(10, 5))
        
        self.dynamic_content_frame = ctk.CTkFrame(self.dynamic_card, fg_color="transparent")
        self.dynamic_content_frame.pack(fill=tk.X, padx=15, pady=(0, 15))
        
        # Build initial dynamic UI for Tone Contour
        self._build_contour_settings()

        # --- CARD 4: Group Filter ---
        self.card_filter = ctk.CTkFrame(self.left_scroll, fg_color=("#FFFFFF", "#1E293B"), border_width=1, border_color=("#E5E7EB", "#475569"), corner_radius=12)
        self.card_filter.pack(fill=tk.X, **card_padding)
        
        ctk.CTkLabel(self.card_filter, text="🎯 组别筛选器", font=self.font_title).pack(anchor="w", padx=15, pady=(10, 5))
        
        # Search Entry
        self.search_group_var = ctk.StringVar()
        self.search_group_var.trace_add("write", lambda *args: self._filter_groups_list())
        self.entry_search_group = ctk.CTkEntry(self.card_filter, placeholder_text="搜索组别...", textvariable=self.search_group_var, height=28, font=self.font_small)
        self.entry_search_group.pack(fill=tk.X, padx=15, pady=(0, 5))
        
        # Scrollable Frame for Group list
        self.filter_scroll = ctk.CTkScrollableFrame(self.card_filter, height=120, fg_color="transparent")
        self.filter_scroll.pack(fill=tk.X, padx=10, pady=5)
        
        # Utility buttons
        util_btn_frame1 = ctk.CTkFrame(self.card_filter, fg_color="transparent")
        util_btn_frame1.pack(fill=tk.X, padx=15, pady=(5, 2))
        
        ctk.CTkButton(util_btn_frame1, text="全选", width=70, height=24, corner_radius=12, font=self.font_small, command=self._select_all_groups).pack(side=tk.LEFT, padx=2)
        ctk.CTkButton(util_btn_frame1, text="反选", width=70, height=24, corner_radius=12, font=self.font_small, command=self._reverse_groups).pack(side=tk.LEFT, padx=2)
        ctk.CTkButton(util_btn_frame1, text="只选树中", width=70, height=24, corner_radius=12, font=self.font_small, command=self._select_tree_selected_groups).pack(side=tk.LEFT, padx=2)
        ctk.CTkButton(util_btn_frame1, text="仅高频", width=70, height=24, corner_radius=12, font=self.font_small, command=self._select_high_frequency_groups).pack(side=tk.LEFT, padx=2)
        
        util_btn_frame2 = ctk.CTkFrame(self.card_filter, fg_color="transparent")
        util_btn_frame2.pack(fill=tk.X, padx=15, pady=(2, 10))
        
        self.btn_toggle_sort = ctk.CTkButton(
            util_btn_frame2, text="按样本量排序", width=95, height=24, corner_radius=12, font=self.font_small,
            fg_color=("#E5E7EB", "#374151"), text_color=("#374151", "#E5E7EB"), hover_color=("#D1D5DB", "#4B5563"),
            command=self._toggle_groups_sorting
        )
        self.btn_toggle_sort.pack(side=tk.LEFT, padx=2)
        
        ctk.CTkLabel(util_btn_frame2, text="样本量 >", font=self.font_small).pack(side=tk.LEFT, padx=(10, 2))
        self.entry_min_count = ctk.CTkEntry(util_btn_frame2, width=40, height=24, font=self.font_small, justify="center")
        self.entry_min_count.insert(0, "0")
        self.entry_min_count.pack(side=tk.LEFT, padx=2)
        self.entry_min_count.bind("<KeyRelease>", lambda e: self._populate_groups_list())

    def _on_type_changed(self, val):
        # Update dynamic options card UI based on chart type selection
        for widget in self.dynamic_content_frame.winfo_children():
            widget.destroy()
            
        if val == "声调轮廓图":
            self.var_chart_type.set("contour")
            self.dynamic_title.configure(text="⚙️ 声调轮廓图专有选项")
            self._build_contour_settings()
        elif val == "声调分布图":
            self.var_chart_type.set("distribution")
            self.dynamic_title.configure(text="⚙️ 声调分布图专有选项")
            self._build_distribution_settings()
        elif val == "时序密度图":
            self.var_chart_type.set("density")
            self.dynamic_title.configure(text="⚙️ 时序密度图专有选项")
            self._build_density_settings()
        elif val == "数据质量检查":
            self.var_chart_type.set("quality")
            self.dynamic_title.configure(text="⚙️ 数据质量检查专有选项")
            self._build_quality_settings()
        elif val == "声调组别概览图":
            self.var_chart_type.set("overview_heatmap")
            self.dynamic_title.configure(text="⚙️ 声调组别概览图专有选项")
            self._build_overview_heatmap_settings()
            
        self.update_preview()

    def _on_scope_changed(self, val):
        self.current_preview_page = 0
        if "整合" in val:
            self.var_export_scope.set("integrated")
            # Force scale to T-value for integrated speaker plots
            self.combo_scale.set("T 值 (五度标调)")
            self.combo_scale.configure(state="disabled")
        elif "分别" in val:
            self.var_export_scope.set("separate")
            self.combo_scale.configure(state="normal")
        else:
            self.var_export_scope.set("active")
            self.combo_scale.configure(state="normal")
        self.update_preview()

    def _on_intention_changed(self, val):
        if "论文主图" in val:
            selected_count = sum(1 for v in self.group_checkbox_vars.values() if v.get())
            if selected_count > 8:
                if messagebox.askyesno("论文主图模式", f"当前选中了 {selected_count} 个组，论文主图建议展示 8 组以内以保证可读性。\n是否自动精选样本量最大的 8 组？"):
                    self._apply_featured_contrast_mode()
            
            if self.var_chart_type.get() == "quality" or self.var_chart_type.get() == "overview_heatmap":
                self.combo_type.set("声调轮廓图")
                self._on_type_changed("声调轮廓图")
            
            if self.var_chart_type.get() == "contour":
                self.combo_contour_content.set("平均曲线 + 置信区间阴影")
                
        elif "附录图册" in val:
            for var in self.group_checkbox_vars.values():
                var.set(True)
            self._populate_groups_list()
            self.combo_format.set("PDF 文档 (.pdf)")
            
            if self.var_chart_type.get() == "quality":
                self.combo_type.set("声调轮廓图")
                self._on_type_changed("声调轮廓图")
                
            self._on_group_filter_changed()
            messagebox.showinfo("附录图册模式", "已选中所有组别，并将导出格式设为 PDF。导出时将自动分页绘制完整图册。")
            
        elif "数据诊断" in val:
            self.combo_type.set("数据质量检查")
            self._on_type_changed("数据质量检查")
            self.var_qc_view.set("raw_overlay")
            self.combo_qc_view.set("个体 Raw F0 曲线叠加 (异常高亮)")
            
            for var in self.group_checkbox_vars.values():
                var.set(True)
            self._populate_groups_list()
            self._on_group_filter_changed()
            messagebox.showinfo("数据诊断模式", "已切换至“数据质量检查”视图，将重点排查 F0 缺失、异常与变异。")

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
        self.combo_type.set("声调组别概览图")
        self._on_type_changed("声调组别概览图")

    def _select_tree_selected_groups(self):
        if not self.project_tree or not hasattr(self.project_tree, 'tree'):
            return
            
        selected_iids = self.project_tree.tree.selection()
        if not selected_iids:
            messagebox.showinfo("提示", "当前主界面的目录树中没有选中任何项。\n请先在主界面目录树中选择声调组或词条。")
            return
            
        selected_groups = set()
        for iid in selected_iids:
            if iid.startswith("group_node_"):
                g_name = iid[len("group_node_"):]
                if g_name != "__warning__":
                    selected_groups.add(g_name)
            else:
                item = self.project_tree.items.get(iid)
                if item and 'group' in item:
                    selected_groups.add(item['group'])
                elif item and 'tone' in item:
                    selected_groups.add(item['tone'])
                    
        if not selected_groups:
            messagebox.showinfo("提示", "未能在当前目录树选中项中识别到有效的声调组别。")
            return
            
        for g, var in self.group_checkbox_vars.items():
            var.set(g in selected_groups)
            
        self._populate_groups_list()
        self._on_group_filter_changed()

    def _toggle_groups_sorting(self):
        self.sort_by_count = not getattr(self, 'sort_by_count', False)
        if self.sort_by_count:
            self.btn_toggle_sort.configure(fg_color=("#3B82F6", "#2563EB"), text_color="white")
        else:
            self.btn_toggle_sort.configure(fg_color=("#E5E7EB", "#374151"), text_color=("#374151", "#E5E7EB"))
        self._populate_groups_list()

    def _populate_groups_list(self):
        for w in self.filter_scroll.winfo_children():
            w.destroy()
            
        search_query = self.search_group_var.get().strip().lower()
        
        min_count = 0
        try:
            val_str = self.entry_min_count.get().strip()
            if val_str:
                min_count = int(val_str)
        except ValueError:
            pass
            
        if getattr(self, 'sort_by_count', False):
            groups_to_draw = sorted(self.available_groups, key=lambda g: self.group_counts.get(g, 0), reverse=True)
        else:
            groups_to_draw = self.available_groups
            
        for g in groups_to_draw:
            count = self.group_counts.get(g, 0)
            if count < min_count:
                continue
            if search_query and search_query not in g.lower():
                continue
                
            var = self.group_checkbox_vars[g]
            cb = ctk.CTkCheckBox(
                self.filter_scroll, text=f"{g} ({count}项)", variable=var,
                font=self.font_small, checkbox_width=18, checkbox_height=18,
                fg_color=("#3B82F6", "#2563EB"), hover_color=("#4B5563", "#9CA3AF"), border_color=("#9CA3AF", "#4B5563"),
                command=self._on_group_filter_changed
            )
            cb.pack(anchor="w", padx=10, pady=3)

    def _on_group_filter_changed(self):
        self.current_preview_page = 0
        self.update_preview()

    def _select_all_groups(self):
        for var in self.group_checkbox_vars.values():
            var.set(True)
        self._on_group_filter_changed()

    def _reverse_groups(self):
        for var in self.group_checkbox_vars.values():
            var.set(not var.get())
        self._on_group_filter_changed()

    def _select_high_frequency_groups(self):
        if not self.group_counts:
            return
        counts = list(self.group_counts.values())
        median_count = np.median(counts) if counts else 1
        for g, var in self.group_checkbox_vars.items():
            var.set(self.group_counts[g] >= median_count)
        self._on_group_filter_changed()

    def _filter_groups_list(self):
        self._populate_groups_list()

    def _build_overview_heatmap_settings(self):
        ctk.CTkLabel(self.dynamic_content_frame, text="热图展示维度:", font=self.font_small).pack(anchor="w", pady=(5, 2))
        self.combo_overview_metric = ctk.CTkOptionMenu(self.dynamic_content_frame, values=["均值热图 (Mean Map)", "标准差热图 (SD Map)"], command=lambda _: self.update_preview(), **self.dropdown_kwargs)
        self.combo_overview_metric.set("均值热图 (Mean Map)")
        self.combo_overview_metric.pack(fill=tk.X, pady=(2, 10))
        self._apply_custom_arrow(self.combo_overview_metric)

    # --- DYNAMIC CONFIGURATION UI BUILDERS ---
    def _build_contour_settings(self):
        # X-Axis scale
        ctk.CTkLabel(self.dynamic_content_frame, text="横轴展现形式:", font=self.font_small).pack(anchor="w", pady=(5, 2))
        self.combo_contour_x = ctk.CTkOptionMenu(self.dynamic_content_frame, values=["归一化采样点", "真实物理时长"], command=lambda _: self.update_preview(), **self.dropdown_kwargs)
        self.combo_contour_x.set("归一化采样点")
        self.combo_contour_x.pack(fill=tk.X, pady=2)
        self._apply_custom_arrow(self.combo_contour_x)
        
        # Curve Content
        ctk.CTkLabel(self.dynamic_content_frame, text="曲线展示要素:", font=self.font_small).pack(anchor="w", pady=(5, 2))
        self.combo_contour_content = ctk.CTkOptionMenu(self.dynamic_content_frame, values=["仅组别平均曲线", "平均曲线 + 个体浅色细线", "平均曲线 + 置信区间阴影"], command=lambda _: self.update_preview(), **self.dropdown_kwargs)
        self.combo_contour_content.set("仅组别平均曲线")
        self.combo_contour_content.pack(fill=tk.X, pady=2)
        self._apply_custom_arrow(self.combo_contour_content)
        
        # Facet By
        ctk.CTkLabel(self.dynamic_content_frame, text="分面子图排版 (Facet):", font=self.font_small).pack(anchor="w", pady=(5, 2))
        self.combo_contour_facet = ctk.CTkOptionMenu(self.dynamic_content_frame, values=["单图展示 (不分面)", "按声调类型分面", "按音节位置分面"], command=lambda _: self.update_preview(), **self.dropdown_kwargs)
        self.combo_contour_facet.set("单图展示 (不分面)")
        self.combo_contour_facet.pack(fill=tk.X, pady=(2, 10))
        self._apply_custom_arrow(self.combo_contour_facet)

    def _build_distribution_settings(self):
        # Distribution view type
        ctk.CTkLabel(self.dynamic_content_frame, text="分布展示类型:", font=self.font_small).pack(anchor="w", pady=(5, 2))
        self.combo_dist_type = ctk.CTkOptionMenu(self.dynamic_content_frame, values=["测量点精细分布", "起-中-终三点比较", "调域范围跨度图", "变异程度(CV)比较"], command=self._on_dist_type_changed, **self.dropdown_kwargs)
        self.combo_dist_type.set("测量点精细分布")
        self.combo_dist_type.pack(fill=tk.X, pady=2)
        self._apply_custom_arrow(self.combo_dist_type)
        
        # Plot style (Boxplot / Violin)
        self.lbl_dist_style = ctk.CTkLabel(self.dynamic_content_frame, text="统计图样式:", font=self.font_small)
        self.lbl_dist_style.pack(anchor="w", pady=(5, 2))
        self.combo_dist_style = ctk.CTkOptionMenu(self.dynamic_content_frame, values=["科学箱线图 (Box Plot)", "小提琴图 (Violin Plot)"], command=lambda _: self.update_preview(), **self.dropdown_kwargs)
        self.combo_dist_style.set("科学箱线图 (Box Plot)")
        self.combo_dist_style.pack(fill=tk.X, pady=(2, 10))
        self._apply_custom_arrow(self.combo_dist_style)

    def _on_dist_type_changed(self, val):
        if val in ["调域范围跨度图", "变异程度(CV)比较"]:
            self.combo_dist_style.configure(state="disabled")
        else:
            self.combo_dist_style.configure(state="normal")
        self.update_preview()

    def _build_density_settings(self):
        # KDE Bandwidth
        ctk.CTkLabel(self.dynamic_content_frame, text="核密度带宽 (Bandwidth):", font=self.font_small).pack(anchor="w", pady=(5, 2))
        bw_val_frame = ctk.CTkFrame(self.dynamic_content_frame, fg_color="transparent")
        bw_val_frame.pack(fill=tk.X)
        self.slider_density_bw = ctk.CTkSlider(bw_val_frame, from_=0.05, to=0.50, number_of_steps=45, command=self._on_bw_slider_changed)
        self.slider_density_bw.set(0.15)
        self.slider_density_bw.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.lbl_bw_val = ctk.CTkLabel(bw_val_frame, text="0.15", font=self.font_main, width=40)
        self.lbl_bw_val.pack(side=tk.RIGHT, padx=(5, 0))
        
        # F0 Truncation Mode
        ctk.CTkLabel(self.dynamic_content_frame, text="截断极值处理:", font=self.font_small).pack(anchor="w", pady=(5, 2))
        self.combo_density_f0 = ctk.CTkOptionMenu(self.dynamic_content_frame, values=["分位数自动截断 (5%-95%)", "极值自动范围 (Min-Max)", "手动指定频率范围 (Hz)"], command=self._on_density_f0_changed, **self.dropdown_kwargs)
        self.combo_density_f0.set("分位数自动截断 (5%-95%)")
        self.combo_density_f0.pack(fill=tk.X, pady=2)
        self._apply_custom_arrow(self.combo_density_f0)
        
        # Sub-entries for manual / percentile
        self.manual_f0_frame = ctk.CTkFrame(self.dynamic_content_frame, fg_color="transparent")
        self.pct_f0_frame = ctk.CTkFrame(self.dynamic_content_frame, fg_color="transparent")
        
        # Manual entries
        ctk.CTkLabel(self.manual_f0_frame, text="频率 (Hz):", font=self.font_small).pack(side=tk.LEFT)
        self.entry_min_hz = ctk.CTkEntry(self.manual_f0_frame, width=55, font=self.font_small)
        self.entry_min_hz.insert(0, "75")
        self.entry_min_hz.pack(side=tk.LEFT, padx=3)
        ctk.CTkLabel(self.manual_f0_frame, text="~", font=self.font_small).pack(side=tk.LEFT)
        self.entry_max_hz = ctk.CTkEntry(self.manual_f0_frame, width=55, font=self.font_small)
        self.entry_max_hz.insert(0, "600")
        self.entry_max_hz.pack(side=tk.LEFT, padx=3)
        
        # Percentile entries
        ctk.CTkLabel(self.pct_f0_frame, text="分位数 (%):", font=self.font_small).pack(side=tk.LEFT)
        self.entry_low_p = ctk.CTkEntry(self.pct_f0_frame, width=45, font=self.font_small)
        self.entry_low_p.insert(0, "5")
        self.entry_low_p.pack(side=tk.LEFT, padx=3)
        ctk.CTkLabel(self.pct_f0_frame, text="~", font=self.font_small).pack(side=tk.LEFT)
        self.entry_high_p = ctk.CTkEntry(self.pct_f0_frame, width=45, font=self.font_small)
        self.entry_high_p.insert(0, "95")
        self.entry_high_p.pack(side=tk.LEFT, padx=3)
        
        # Show default
        self.pct_f0_frame.pack(fill=tk.X, pady=2)
        
        # Facet Density
        ctk.CTkLabel(self.dynamic_content_frame, text="排版分面依据:", font=self.font_small).pack(anchor="w", pady=(5, 2))
        self.combo_density_facet = ctk.CTkOptionMenu(self.dynamic_content_frame, values=["声调类型分面 (默认)", "不分面 (混合叠加)", "按词语分面"], command=lambda _: self.update_preview(), **self.dropdown_kwargs)
        self.combo_density_facet.set("声调类型分面 (默认)")
        self.combo_density_facet.pack(fill=tk.X, pady=(2, 10))
        self._apply_custom_arrow(self.combo_density_facet)

    def _on_bw_slider_changed(self, val):
        self.var_density_bw.set(float(val))
        self.lbl_bw_val.configure(text=f"{float(val):.2f}")
        # Use simple debounce by only updating on actual release if desired, 
        # but here we update directly (Matplotlib is fast enough)
        self.update_preview()

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
            
        self.update_preview()

    def _build_quality_settings(self):
        ctk.CTkLabel(self.dynamic_content_frame, text="数据质检视图类型:", font=self.font_small).pack(anchor="w", pady=(5, 2))
        self.combo_qc_view = ctk.CTkOptionMenu(self.dynamic_content_frame, values=["个体 Raw F0 曲线叠加 (异常高亮)", "有效点比例 (Active Ratio) 分布箱线图", "发音人基频均值与调域跨度散点图"], command=self._on_qc_view_changed, **self.dropdown_kwargs)
        self.combo_qc_view.set("个体 Raw F0 曲线叠加 (异常高亮)")
        self.combo_qc_view.pack(fill=tk.X, pady=(2, 10))
        self._apply_custom_arrow(self.combo_qc_view)
        self.var_qc_view.set("raw_overlay")

    def _on_qc_view_changed(self, val):
        if "个体" in val:
            self.var_qc_view.set("raw_overlay")
        elif "比例" in val:
            self.var_qc_view.set("active_ratio")
        else:
            self.var_qc_view.set("speaker_means")
        self.update_preview()

    # --- CORE DATA EXTRACTION ENGINE ---
    def _extract_active_data(self, speakers_list):
        """Extracts complete structures, raw F0 arrays, active ratios, labels, groups, etc. for all speakers in scope."""
        num_points = self.project_tree.app_state_params.get('pts', 11)
        data_entries = []
        
        for speaker in speakers_list:
            # We need to temporarily set the tree items to this speaker if not the active one
            orig_items = self.project_tree.items
            self.project_tree.items = speaker.items
            
            s_struct = self.project_tree._get_items_by_group_for_dict(speaker.items)
            
            # Extract Speaker's absolute F0 stats to do speaker-wise T-value calculation
            speaker_f0_pool = []
            speaker_items_temp = []
            
            for grp_name, children in s_struct:
                for child in children:
                    item = speaker.items[child]
                    self.project_tree._ensure_item_loaded(item)
                    if item.get('start') is None or not item.get('snd') or (not item.get('pitch') and not item.get('pitch_data')):
                        continue
                    
                    total_dur, syl_data = self.project_tree._extract_syl_data(item, num_points)
                    if total_dur <= 0:
                        continue
                        
                    # Extract raw pitch arrays
                    p_xs, p_freqs = self.project_tree._get_pitch_arrays_for_item(item)
                    if p_xs is None or p_freqs is None:
                        continue
                        
                    # Calculate active ratios
                    valid_f0_mask = p_freqs > 0
                    active_ratio = np.mean(valid_f0_mask) if len(p_freqs) > 0 else 0.0
                    
                    # Warnings checks
                    warnings = item.get('warnings', [])
                    
                    speaker_items_temp.append({
                        'speaker_name': speaker.name,
                        'group': grp_name,
                        'label': item.get('label', ''),
                        'total_dur': total_dur,
                        'syl_data': syl_data,
                        'raw_xs': p_xs,
                        'raw_freqs': p_freqs,
                        'active_ratio': active_ratio,
                        'warnings': warnings,
                        'raw_item': item
                    })
                    
                    speaker_f0_pool.extend([f for f in p_freqs if f > 0])
            
            # Calculate speaker-wise min/max using robust percentile to avoid extreme outliers distorting
            if speaker_f0_pool:
                s_min = np.percentile(speaker_f0_pool, 5.0)
                s_max = np.percentile(speaker_f0_pool, 95.0)
            else:
                s_min, s_max = 75.0, 600.0
                
            # Perform T-value mapping speaker-wise
            for entry in speaker_items_temp:
                # Add normalized T-value to entry's syl_data
                normalized_syl_data = []
                for s_dur, freqs in entry['syl_data']:
                    norm_freqs = []
                    for f in freqs:
                        if f > 0:
                            if s_max > s_min:
                                norm_t = 5 * (math.log10(f) - math.log10(s_min)) / (math.log10(s_max) - math.log10(s_min))
                                norm_t = np.clip(norm_t, 0.0, 5.0)
                            else:
                                norm_t = 3.0
                            norm_freqs.append(norm_t)
                        else:
                            norm_freqs.append(np.nan)
                    normalized_syl_data.append((s_dur, norm_freqs))
                
                # Normalize raw F0 to T values too
                norm_raw_freqs = []
                for f in entry['raw_freqs']:
                    if f > 0:
                        if s_max > s_min:
                            norm_t = 5 * (math.log10(f) - math.log10(s_min)) / (math.log10(s_max) - math.log10(s_min))
                            norm_t = np.clip(norm_t, 0.0, 5.0)
                        else:
                            norm_t = 3.0
                        norm_raw_freqs.append(norm_t)
                    else:
                        norm_raw_freqs.append(np.nan)
                
                entry['normalized_syl_data'] = normalized_syl_data
                entry['normalized_raw_freqs'] = np.array(norm_raw_freqs)
                data_entries.append(entry)
                
            self.project_tree.items = orig_items
            
        if hasattr(self, 'group_checkbox_vars') and self.group_checkbox_vars:
            selected_groups = {g for g, var in self.group_checkbox_vars.items() if var.get()}
            data_entries = [e for e in data_entries if e['group'] in selected_groups]
            
        return data_entries

    def _get_current_data_entries(self):
        scope = self.var_export_scope.get()
        if scope == "active" or not self.all_speakers:
            # Active speaker only
            return self._extract_active_data([self.active_speaker])
        elif scope == "separate":
            # For separate export preview, show the speaker at self.current_preview_page
            idx = getattr(self, 'current_preview_page', 0)
            if idx < 0 or idx >= len(self.all_speakers):
                idx = 0
                self.current_preview_page = 0
            current_speaker = self.all_speakers[idx]
            return self._extract_active_data([current_speaker])
        else:
            # All speakers (integrated)
            return self._extract_active_data(self.all_speakers)

    # --- ADVANCED SCIENTIFIC PLOTTING ENGINE ---
    def generate_plot(self, data_entries, is_preview=True):
        plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'Arial Unicode MS', 'sans-serif']
        plt.rcParams['axes.unicode_minus'] = False
        
        chart_type = self.var_chart_type.get()
        groupby = self.combo_groupby.get()
        scale = self.combo_scale.get()
        
        # Decide grouping label extraction
        group_key = 'group'
        if groupby == "按词语":
            group_key = 'label'
        elif groupby == "按发音人":
            group_key = 'speaker_name'
            
        if not data_entries:
            fig, ax = plt.subplots(figsize=(6, 4))
            ax.text(0.5, 0.5, "没有找到有效的声调基频数据！\n请检查是否配置了发音人或导入了音频点。", ha='center', va='center', fontsize=12, color='red')
            ax.axis('off')
            return fig

        # Find unique groups to display
        unique_groups = []
        for e in data_entries:
            val = e[group_key]
            if val not in unique_groups:
                unique_groups.append(val)

        truncated = False
        if is_preview and len(unique_groups) > 8 and chart_type != "overview_heatmap":
            truncated = True
            hidden_count = len(unique_groups) - 8
            allowed_groups = set(unique_groups[:8])
            data_entries = [e for e in data_entries if e[group_key] in allowed_groups]
            
        if chart_type == "contour":
            fig = self._plot_tone_contour(data_entries, group_key, scale)
        elif chart_type == "distribution":
            fig = self._plot_tone_distribution(data_entries, group_key, scale)
        elif chart_type == "density":
            fig = self._plot_temporal_density(data_entries, group_key)
        elif chart_type == "quality":
            fig = self._plot_quality_check(data_entries)
        elif chart_type == "overview_heatmap":
            fig = self._plot_tone_overview_heatmap(data_entries, group_key, scale)
        else:
            fig, ax = plt.subplots()
            return fig

        if truncated:
            # Adjust subplots top margin to avoid overlaps with the banner
            fig.subplots_adjust(top=0.88)
            fig.text(0.5, 0.96, f"[预览提示] 当前共 {len(unique_groups)} 组，预览仅显示前 8 组（其余 {hidden_count} 组已隐藏）。导出时将自动分页/完整输出。",
                     ha='center', va='center', fontsize=10, color='#991B1B', weight='bold',
                     bbox=dict(facecolor='#FEF2F2', edgecolor='#FCA5A5', boxstyle='round,pad=0.4'))
            
        return fig

    def _plot_tone_contour(self, data_entries, group_key, scale):
        x_axis = self.combo_contour_x.get()
        content = self.combo_contour_content.get()
        facet = self.combo_contour_facet.get()
        
        # Group items
        grouped_data = {}
        for entry in data_entries:
            val = entry[group_key]
            if val not in grouped_data:
                grouped_data[val] = []
            grouped_data[val].append(entry)
            
        max_syls = max(len(e['syl_data']) for e in data_entries)
        num_points = self.project_tree.app_state_params.get('pts', 11)
        
        # Faceting setup
        facet_keys = ["Default"]
        if facet == "按声调类型分面":
            facet_keys = sorted(list(set(e['group'] for e in data_entries)))
        elif facet == "按音节位置分面":
            facet_keys = [f"第 {k+1} 音节" for k in range(max_syls)]
            
        n_facets = len(facet_keys)
        n_cols = min(2, n_facets)
        n_rows = math.ceil(n_facets / n_cols)
        
        fig, axes = plt.subplots(n_rows, n_cols, figsize=(5 * n_cols + 2, 4.5 * n_rows + 0.5), squeeze=False, sharex=True, sharey=True)
        axes_flat = axes.flatten()
        
        for f_idx, f_key in enumerate(facet_keys):
            ax = axes_flat[f_idx]
            ax.grid(True, linestyle="--", alpha=0.3)
            
            # Sub-slice data for this facet
            facet_entries = data_entries
            if facet == "按声调类型分面":
                facet_entries = [e for e in data_entries if e['group'] == f_key]
                
            # Group slice
            facet_grouped = {}
            for entry in facet_entries:
                val = entry[group_key]
                if val not in facet_grouped:
                    facet_grouped[val] = []
                facet_grouped[val].append(entry)
                
            # Plot each group
            for g_color_idx, (g_name, entries) in enumerate(facet_grouped.items()):
                color = self.colors[g_color_idx % len(self.colors)]
                
                # Collect curves
                curves_x = []
                curves_y = []
                
                for entry in entries:
                    y_series = []
                    x_series = []
                    
                    syl_list = entry['normalized_syl_data'] if "T 值" in scale else entry['syl_data']
                    
                    acc_dur = 0.0
                    for s_idx, (s_dur, pts) in enumerate(syl_list):
                        # Filter this syllable position if faceting by syllable position
                        if facet == "按音节位置分面" and f_idx != s_idx:
                            continue
                            
                        # Build X points
                        if x_axis == "归一化采样点":
                            # sequential points: e.g. 1 to 11 for syl 1, 12 to 22 for syl 2
                            x_pts = np.linspace(s_idx * num_points + 1, (s_idx + 1) * num_points, len(pts))
                        else:
                            # physically stretched real duration
                            x_pts = np.linspace(acc_dur, acc_dur + s_dur, len(pts))
                            acc_dur += s_dur
                            
                        x_series.extend(x_pts)
                        y_series.extend(pts)
                        
                    if len(y_series) > 0:
                        curves_x.append(np.array(x_series))
                        curves_y.append(np.array(y_series))
                
                if not curves_y:
                    continue
                    
                # Compute average curve
                # To handle potential differences in lengths gracefully, we interpolate to a standard grid
                total_len = len(curves_x[0]) if curves_x else 10
                grid_x = np.linspace(np.min([np.min(cx) for cx in curves_x]), np.max([np.max(cx) for cx in curves_x]), total_len)
                
                interpolated_ys = []
                for cx, cy in zip(curves_x, curves_y):
                    valid = ~np.isnan(cy)
                    if np.sum(valid) >= 2:
                        iy = np.interp(grid_x, cx[valid], cy[valid])
                        interpolated_ys.append(iy)
                        
                if not interpolated_ys:
                    continue
                    
                mean_y = np.nanmean(interpolated_ys, axis=0)
                std_y = np.nanstd(interpolated_ys, axis=0)
                
                # --- Drawing Content Options ---
                if "个体浅色" in content:
                    # Draw individual lines
                    for cy in interpolated_ys:
                        ax.plot(grid_x, cy, color=color, linewidth=0.6, alpha=0.18)
                elif "置信区间" in content:
                    # Draw Mean ± SD shadow
                    ax.fill_between(grid_x, mean_y - std_y, mean_y + std_y, color=color, alpha=0.15)
                    
                short_g_name = g_name
                if len(g_name) > 12:
                    short_g_name = g_name[:10] + ".."
                # Always draw the thick bold average curve
                ax.plot(grid_x, mean_y, '-o', color=color, linewidth=2.5, markersize=5, label=short_g_name)
                
            title_text = "声调声学格局连贯图"
            if facet in ("按声调类型分面", "按音节位置分面"):
                title_text = f_key
            else:
                if len(set(e['speaker_name'] for e in data_entries)) == 1:
                    title_text = f"{data_entries[0]['speaker_name']} - 声学格局连贯图"
            if len(title_text) > 20:
                title_text = title_text[:17] + "..."
            ax.set_title(title_text, fontsize=12, fontweight="bold")
            
            # Setup Y bounds
            if "T 值" in scale:
                ax.set_ylim(-0.2, 5.2)
                ax.set_yticks([0, 1, 2, 3, 4, 5])
            
            row_idx = f_idx // n_cols
            col_idx = f_idx % n_cols
            
            if col_idx == 0:
                if "T 值" in scale:
                    ax.set_ylabel("T 值 (0-5 标度)")
                else:
                    ax.set_ylabel("频率 (Hz)")
            else:
                ax.set_ylabel("")
                
            is_bottom_row = (row_idx == n_rows - 1) or (f_idx + n_cols >= n_facets)
            if is_bottom_row:
                if x_axis == "归一化采样点":
                    ax.set_xlabel("音节测量点 (时序展开)")
                else:
                    ax.set_xlabel("时长 Duration (s)")
            else:
                ax.set_xlabel("")
                
            # Add syllable divider lines if multiple syllables and normalized X
            if max_syls > 1 and x_axis == "归一化采样点" and facet != "按音节位置分面":
                for k in range(1, max_syls):
                    ax.axvline(k * num_points + 0.5, color='gray', linestyle='--', alpha=0.5)
                    
            if g_color_idx >= 0:
                ax.legend(loc="upper right", fontsize=8)
                
        # Hide unused subplots
        for idx in range(n_facets, len(axes_flat)):
            axes_flat[idx].set_visible(False)
            
        fig.tight_layout()
        return fig

    def _plot_tone_overview_heatmap(self, data_entries, group_key, scale):
        metric = self.combo_overview_metric.get()
        
        # Determine total points
        max_syls = max(len(e['syl_data']) for e in data_entries)
        num_points = self.project_tree.app_state_params.get('pts', 11)
        total_points = max_syls * num_points
        
        # Group entries
        grouped_data = {}
        for entry in data_entries:
            val = entry[group_key]
            if val not in grouped_data:
                grouped_data[val] = []
            grouped_data[val].append(entry)
            
        # Compute row values for each group
        groups_sorted = sorted(list(grouped_data.keys()))
        if not groups_sorted:
            fig, ax = plt.subplots(figsize=(6, 4))
            ax.text(0.5, 0.5, "没有找到有效的声调数据用于生成概览图", ha='center', va='center')
            return fig
            
        matrix = []
        row_labels = []
        
        for g_name in groups_sorted:
            entries = grouped_data[g_name]
            vectors = []
            for entry in entries:
                syl_list = entry['normalized_syl_data'] if "T 值" in scale else entry['syl_data']
                y_flat = []
                for s_dur, pts in syl_list:
                    y_flat.extend(pts)
                if len(y_flat) < total_points:
                    y_flat.extend([np.nan] * (total_points - len(y_flat)))
                elif len(y_flat) > total_points:
                    y_flat = y_flat[:total_points]
                vectors.append(y_flat)
                
            if vectors:
                vectors = np.array(vectors)
                with np.errstate(all='ignore'):
                    if "均值" in metric:
                        row_vec = np.nanmean(vectors, axis=0)
                    else:
                        row_vec = np.nanstd(vectors, axis=0)
                # Fallback check if row_vec is all NaNs
                if np.isnan(row_vec).all():
                    row_vec = np.zeros(total_points)
                matrix.append(row_vec)
                
                # Check sample size
                count = len(entries)
                row_labels.append(f"{g_name} (N={count})")
                
        matrix = np.array(matrix)
        
        # Height of the plot dynamically scales with the number of groups
        fig_height = max(4, len(row_labels) * 0.35 + 1.5)
        fig, ax = plt.subplots(figsize=(8, fig_height))
        
        if "均值" in metric:
            cmap = 'RdYlBu_r' if "T 值" in scale else 'viridis'
            vmin = 0.0 if "T 值" in scale else None
            vmax = 5.0 if "T 值" in scale else None
        else:
            cmap = 'Reds'
            vmin = 0.0
            vmax = None
            
        im = ax.imshow(matrix, cmap=cmap, aspect='auto', vmin=vmin, vmax=vmax)
        
        # Add colorbar
        cbar = fig.colorbar(im, ax=ax, pad=0.02)
        if "均值" in metric:
            cbar.set_label("平均 T 值" if "T 值" in scale else "平均基频 (Hz)")
        else:
            cbar.set_label("标准差 (SD)" if "T 值" in scale else "标准差 (Hz)")
            
        # Labels and ticks
        ax.set_yticks(np.arange(len(row_labels)))
        ax.set_yticklabels(row_labels, fontsize=9)
        
        ax.set_xticks(np.arange(total_points))
        x_labels = []
        for s_idx in range(max_syls):
            for p_idx in range(num_points):
                if p_idx == 0 or p_idx == num_points - 1 or p_idx == num_points // 2:
                    x_labels.append(f"音节{s_idx+1}_点{p_idx+1}")
                else:
                    x_labels.append("")
        ax.set_xticklabels(x_labels, rotation=45, ha='right', fontsize=8)
        
        # Draw grid lines separating syllables if multiple syllables
        if max_syls > 1:
            for k in range(1, max_syls):
                ax.axvline(k * num_points - 0.5, color='white', linestyle='--', linewidth=1.5, alpha=0.8)
                
        # Title and tight layout
        title_text = f"声调组别概览图 - {metric}"
        if len(set(e['speaker_name'] for e in data_entries)) == 1:
            title_text = f"{data_entries[0]['speaker_name']} - {title_text}"
        ax.set_title(title_text, fontsize=12, fontweight="bold", pad=15)
        
        fig.tight_layout()
        return fig

    def _plot_tone_distribution(self, data_entries, group_key, scale):
        dist_type = self.combo_dist_type.get()
        style = self.combo_dist_style.get()
        
        # Group items
        grouped_data = {}
        for entry in data_entries:
            val = entry[group_key]
            if val not in grouped_data:
                grouped_data[val] = []
            grouped_data[val].append(entry)
            
        n_groups = len(grouped_data)
        num_points = self.project_tree.app_state_params.get('pts', 11)
        max_syls = max(len(e['syl_data']) for e in data_entries)
        
        if "测量点精细分布" in dist_type:
            # 1. Boxplot/Violin per measurement point (Facetted by group)
            n_cols = min(2, n_groups)
            n_rows = math.ceil(n_groups / n_cols)
            fig, axes = plt.subplots(n_rows, n_cols, figsize=(5 * n_cols + 2, 4.2 * n_rows + 0.5), squeeze=False, sharex=True, sharey=True)
            axes_flat = axes.flatten()
            
            for idx, (g_name, entries) in enumerate(grouped_data.items()):
                ax = axes_flat[idx]
                ax.grid(True, linestyle="--", alpha=0.3)
                
                # Assemble points matrix (N_entries x Total_points)
                pts_data = []
                for entry in entries:
                    y_series = []
                    syl_list = entry['normalized_syl_data'] if "T 值" in scale else entry['syl_data']
                    for s_dur, pts in syl_list:
                        y_series.extend(pts)
                    if len(y_series) == max_syls * num_points:
                        pts_data.append(y_series)
                        
                if not pts_data:
                    ax.text(0.5, 0.5, "数据不完整，音节数不一致", ha='center', va='center', color='red')
                    continue
                    
                pts_data = np.array(pts_data)
                positions = np.arange(1, pts_data.shape[1] + 1)
                
                if "小提琴图" in style:
                    # Clean NaNs for violinplot
                    cleaned_columns = []
                    for col_idx in range(pts_data.shape[1]):
                        col = pts_data[:, col_idx]
                        cleaned_columns.append(col[~np.isnan(col)])
                    ax.violinplot(cleaned_columns, positions, showmeans=True, showmedians=False)
                else:
                    # Boxplot handles NaNs nicely in newer matplotlib, but robust cleaning is safer
                    cleaned_columns = []
                    for col_idx in range(pts_data.shape[1]):
                        col = pts_data[:, col_idx]
                        cleaned_columns.append(col[~np.isnan(col)])
                    ax.boxplot(cleaned_columns, positions=positions, patch_artist=True,
                               boxprops=dict(facecolor="#DBEAFE", color="#1E40AF"),
                               whiskerprops=dict(color="#1E40AF"),
                               capprops=dict(color="#1E40AF"),
                               medianprops=dict(color="#DC2626", linewidth=1.5))
                               
                ax.set_title(f"{g_name} 基频测量点分布", fontsize=11, fontweight="bold")
                if "T 值" in scale:
                    ax.set_ylim(-0.2, 5.2)
                    ax.set_yticks([0, 1, 2, 3, 4, 5])
                ax.set_xticks(positions)
                ax.set_xticklabels([str((p-1)%num_points + 1) for p in positions], fontsize=8)
                
            for idx in range(n_groups, len(axes_flat)):
                axes_flat[idx].set_visible(False)
                
            fig.tight_layout()
            return fig
            
        elif "起-中-终" in dist_type:
            # 2. Boxplot/Violin for Start, Mid, End comparison
            fig, ax = plt.subplots(figsize=(8, 5))
            ax.grid(True, linestyle="--", alpha=0.3)
            
            data_to_plot = []
            labels = []
            colors_to_use = []
            
            for g_color_idx, (g_name, entries) in enumerate(grouped_data.items()):
                start_pts, mid_pts, end_pts = [], [], []
                color = self.colors[g_color_idx % len(self.colors)]
                
                for entry in entries:
                    y_series = []
                    syl_list = entry['normalized_syl_data'] if "T 值" in scale else entry['syl_data']
                    for s_dur, pts in syl_list:
                        y_series.extend(pts)
                        
                    if len(y_series) >= 3:
                        y_series = np.array(y_series)
                        valid_ys = y_series[~np.isnan(y_series)]
                        if len(valid_ys) >= 3:
                            start_pts.append(valid_ys[0])
                            mid_pts.append(valid_ys[len(valid_ys) // 2])
                            end_pts.append(valid_ys[-1])
                            
                if start_pts:
                    data_to_plot.extend([start_pts, mid_pts, end_pts])
                    labels.extend([f"{g_name}\n起点", f"{g_name}\n中点", f"{g_name}\n终点"])
                    colors_to_use.extend([color, color, color])
                    
            if data_to_plot:
                if "小提琴图" in style:
                    parts = ax.violinplot(data_to_plot, showmeans=True)
                    for pc_idx, pc in enumerate(parts['bodies']):
                        pc.set_facecolor(colors_to_use[pc_idx])
                        pc.set_alpha(0.6)
                else:
                    bp = ax.boxplot(data_to_plot, patch_artist=True)
                    for patch, color in zip(bp['boxes'], colors_to_use):
                        patch.set_facecolor(color)
                        patch.set_alpha(0.6)
                        
                ax.set_xticklabels(labels, fontsize=9)
                ax.set_title("各声调起-中-终三点基频离散比较", fontsize=13, fontweight="bold")
                if "T 值" in scale:
                    ax.set_ylim(-0.2, 5.2)
                    ax.set_yticks([0, 1, 2, 3, 4, 5])
                    
            return fig
            
        elif "调域范围" in dist_type:
            # 3. Tone Range Plot (Highest vs Lowest domain mapping)
            fig, ax = plt.subplots(figsize=(8, 5))
            ax.grid(True, linestyle="--", alpha=0.3)
            
            y_positions = np.arange(n_groups)
            group_names = []
            
            for idx, (g_name, entries) in enumerate(grouped_data.items()):
                group_names.append(g_name)
                color = self.colors[idx % len(self.colors)]
                
                min_vals = []
                max_vals = []
                for entry in entries:
                    y_series = []
                    syl_list = entry['normalized_syl_data'] if "T 值" in scale else entry['syl_data']
                    for s_dur, pts in syl_list:
                        y_series.extend(pts)
                    y_series = np.array(y_series)
                    valid_ys = y_series[~np.isnan(y_series)]
                    if len(valid_ys) > 0:
                        min_vals.append(np.min(valid_ys))
                        max_vals.append(np.max(valid_ys))
                        
                if min_vals:
                    avg_min = np.mean(min_vals)
                    avg_max = np.mean(max_vals)
                    # Draw a floating range bar representing the pitch domain
                    ax.barh(idx, avg_max - avg_min, left=avg_min, height=0.5, color=color, alpha=0.7, edgecolor=color, align='center')
                    # Draw ticks for min and max
                    ax.plot([avg_min, avg_max], [idx, idx], '|', color='black', markersize=10, markeredgewidth=2)
                    ax.text((avg_min + avg_max)/2, idx, f"{avg_min:.2f} ~ {avg_max:.2f}\n(调域: {avg_max-avg_min:.2f})", 
                            ha='center', va='center', color='black', fontsize=9, fontweight='bold')
                            
            ax.set_yticks(y_positions)
            ax.set_yticklabels(group_names, fontsize=11, fontweight="bold")
            ax.set_title("各声调调域范围图 (最高点 / 最低点 / 调域跨度)", fontsize=13, fontweight="bold")
            
            if "T 值" in scale:
                ax.set_xlim(-0.2, 5.2)
                ax.set_xticks([0, 1, 2, 3, 4, 5])
                ax.set_xlabel("T 值域区间")
            else:
                ax.set_xlabel("绝对频率范围 (Hz)")
                
            return fig
            
        elif "变异程度" in dist_type:
            # 4. Variation coefficient (CV) bar chart
            fig, ax = plt.subplots(figsize=(8, 5))
            ax.grid(True, linestyle="--", alpha=0.3)
            
            group_names = []
            cv_values = []
            colors_to_use = []
            
            for idx, (g_name, entries) in enumerate(grouped_data.items()):
                group_names.append(g_name)
                color = self.colors[idx % len(self.colors)]
                colors_to_use.append(color)
                
                all_pts_flat = []
                for entry in entries:
                    y_series = []
                    syl_list = entry['normalized_syl_data'] if "T 值" in scale else entry['syl_data']
                    for s_dur, pts in syl_list:
                        y_series.extend(pts)
                    y_series = np.array(y_series)
                    valid_ys = y_series[~np.isnan(y_series)]
                    all_pts_flat.extend(valid_ys.tolist())
                    
                if all_pts_flat:
                    mean_val = np.mean(all_pts_flat)
                    std_val = np.nanstd(all_pts_flat)
                    cv = (std_val / mean_val) if mean_val > 0 else 0.0
                    cv_values.append(cv)
                else:
                    cv_values.append(0.0)
                    
            ax.bar(group_names, cv_values, color=colors_to_use, alpha=0.75, width=0.45)
            
            for idx, val in enumerate(cv_values):
                ax.text(idx, val + 0.005, f"{val:.1%}", ha='center', va='bottom', fontweight='bold')
                
            ax.set_title("各声调内部发音变异系数 (Coefficient of Variation) 比较", fontsize=12, fontweight="bold")
            ax.set_ylabel("变异系数 CV (SD / Mean)")
            ax.set_xlabel("声调类别")
            
            return fig
            
        # Fallback empty figure
        fig, ax = plt.subplots()
        return fig

    def _plot_temporal_density(self, data_entries, group_key):
        bw_method = self.var_density_bw.get()
        f0_mode = self.var_density_f0_mode.get()
        facet = self.combo_density_facet.get()
        
        # Group items
        grouped_data = {}
        for entry in data_entries:
            val = entry[group_key]
            if val not in grouped_data:
                grouped_data[val] = []
            grouped_data[val].append(entry)
            
        n_groups = len(grouped_data)
        max_syls = max(len(e['syl_data']) for e in data_entries)
        N_DENSE = 100
        
        # Collect F0 pool to compute robust global limits
        all_raw_f0 = []
        for entry in data_entries:
            all_raw_f0.extend([f for f in entry['raw_freqs'] if f > 0])
            
        if not all_raw_f0:
            fig, ax = plt.subplots()
            ax.text(0.5, 0.5, "没有有效基频点可进行 KDE 计算", ha='center', va='center')
            return fig
            
        # Determine Min/Max bounds based on selected F0 limits mode
        if f0_mode == 'percentile':
            try:
                p_low = float(self.entry_low_p.get())
                p_high = float(self.entry_high_p.get())
            except ValueError:
                p_low, p_high = 5.0, 95.0
            min_f0 = np.percentile(all_raw_f0, p_low)
            max_f0 = np.percentile(all_raw_f0, p_high)
        elif f0_mode == 'manual':
            try:
                min_f0 = float(self.entry_min_hz.get())
                max_f0 = float(self.entry_max_hz.get())
            except ValueError:
                min_f0, max_f0 = 75.0, 600.0
        else:
            min_f0 = min(all_raw_f0)
            max_f0 = max(all_raw_f0)
            
        # Helper to map Hz to T-scale robustly
        def hz_to_t(hz):
            if max_f0 == min_f0: return 3.0
            hz_val = np.clip(hz, min_f0, max_f0)
            if min_f0 <= 0 or max_f0 <= min_f0: return 3.0
            return 5 * (np.log(hz_val) - np.log(min_f0)) / (np.log(max_f0) - np.log(min_f0))
            
        # Faceting setup
        facet_keys = ["Default"]
        if facet == "声调类型分面 (默认)":
            facet_keys = sorted(list(set(e['group'] for e in data_entries)))
        elif facet == "按词语分面":
            facet_keys = sorted(list(set(e['label'] for e in data_entries)))
            
        n_facets = len(facet_keys)
        n_cols = min(2, n_facets)
        n_rows = math.ceil(n_facets / n_cols)
        
        fig, axes = plt.subplots(n_rows, n_cols, figsize=(5 * max_syls * n_cols + 1, 4.5 * n_rows + 0.5), squeeze=False, sharex=True, sharey=True)
        axes_flat = axes.flatten()
        
        for f_idx, f_key in enumerate(facet_keys):
            ax = axes_flat[f_idx]
            
            # Sift data for this facet panel
            facet_entries = data_entries
            if facet == "声调类型分面 (默认)":
                facet_entries = [e for e in data_entries if e['group'] == f_key]
            elif facet == "按词语分面":
                facet_entries = [e for e in data_entries if e['label'] == f_key]
                
            # Assemble density points
            X_all, Y_all = [], []
            for entry in facet_entries:
                syl_bounds = self.project_tree._get_syllables_and_bounds(entry['raw_item'])[1]
                for s_idx, (c_s, c_e) in enumerate(syl_bounds):
                    y_dense = self.project_tree._extract_kde_contour(entry['raw_xs'], entry['raw_freqs'], c_s, c_e, N_DENSE)
                    if y_dense is not None:
                        x_dense = np.linspace(s_idx * 100, (s_idx + 1) * 100, N_DENSE)
                        y_t_dense = np.array([hz_to_t(h) for h in y_dense])
                        valid = np.isfinite(y_t_dense)
                        X_all.extend(x_dense[valid].tolist())
                        Y_all.extend(y_t_dense[valid].tolist())
                        
            if not X_all:
                ax.text(0.5, 0.5, "没有足够的有效数据点", ha='center', va='center')
                continue
                
            xmin, xmax = 0, max_syls * 100
            ymin, ymax = -0.5, 5.5
            
            positions = np.vstack([X_all, Y_all])
            try:
                kernel = gaussian_kde(positions, bw_method=bw_method)
                xi, yi = np.mgrid[xmin:xmax:200j, ymin:ymax:100j]
                zi = kernel(np.vstack([xi.flatten(), yi.flatten()]))
                zi = zi.reshape(xi.shape)
                
                vmax = zi.max()
                if vmax > 0:
                    levels = np.linspace(vmax * 0.05, vmax, 30)
                    ax.contourf(xi, yi, zi, levels=levels, cmap="YlOrRd", extend='neither')
            except Exception as e:
                ax.text(0.5, 0.5, f"KDE 计算失败: {str(e)[:20]}", ha='center', va='center', color='red')
                
            # Layout grids & dividers
            for k in range(1, max_syls):
                ax.axvline(k * 100, color='gray', linestyle='--', alpha=0.8)
                
            row_idx = f_idx // n_cols
            col_idx = f_idx % n_cols
            
            if col_idx == 0:
                ax.set_ylabel("T 值 (0-5 标度)")
            else:
                ax.set_ylabel("")
                
            is_bottom_row = (row_idx == n_rows - 1) or (f_idx + n_cols >= n_facets)
            if is_bottom_row:
                ticks, labels = [], []
                for k in range(max_syls):
                    ticks.append(k * 100 + 50)
                    labels.append(f"第 {k+1} 字\n(0-100%)")
                ax.set_xticks(ticks)
                ax.set_xticklabels(labels, fontsize=9)
            else:
                # Still set ticks but clear labels to keep shared axis alignment neat
                ticks = []
                for k in range(max_syls):
                    ticks.append(k * 100 + 50)
                ax.set_xticks(ticks)
                ax.set_xticklabels([])
                
            ax.set_ylim(-0.5, 5.5)
            ax.set_yticks([0, 1, 2, 3, 4, 5])
            
            title_text = f_key if f_key != "Default" else "时序密度热力图"
            if len(title_text) > 20:
                title_text = title_text[:17] + "..."
            ax.set_title(title_text, fontsize=12, fontweight="bold")
            
        for idx in range(n_facets, len(axes_flat)):
            axes_flat[idx].set_visible(False)
            
        fig.tight_layout()
        return fig

    def _plot_quality_check(self, data_entries):
        view = self.var_qc_view.get()
        
        if view == "raw_overlay":
            # 1. Overlay raw individual curves and highlight anomalies in Red
            fig, ax = plt.subplots(figsize=(9, 5))
            ax.grid(True, linestyle="--", alpha=0.3)
            
            max_syls = max(len(e['syl_data']) for e in data_entries)
            num_points = self.project_tree.app_state_params.get('pts', 11)
            
            normal_drawn = False
            outlier_drawn = False
            
            for entry in data_entries:
                y_series = entry['normalized_syl_data'] if self.combo_scale.get().startswith("T") else entry['syl_data']
                y_flat = []
                x_flat = []
                
                for s_idx, (s_dur, pts) in enumerate(y_series):
                    x_pts = np.linspace(s_idx * num_points + 1, (s_idx + 1) * num_points, len(pts))
                    x_flat.extend(x_pts)
                    y_flat.extend(pts)
                    
                y_flat = np.array(y_flat)
                x_flat = np.array(x_flat)
                
                # Check if this item has warnings
                has_warning = any(w.startswith("[警告]") or w.startswith("[致命]") for w in entry.get('warnings', []))
                
                if has_warning:
                    ax.plot(x_flat, y_flat, color="#EF4444", linewidth=1.2, alpha=0.75, linestyle="--", 
                            label="存在质量异常的发音" if not outlier_drawn else "")
                    outlier_drawn = True
                else:
                    ax.plot(x_flat, y_flat, color="#3B82F6", linewidth=0.75, alpha=0.3,
                            label="质量良好的常规发音" if not normal_drawn else "")
                    normal_drawn = True
                    
            if max_syls > 1:
                for k in range(1, max_syls):
                    ax.axvline(k * num_points + 0.5, color='gray', linestyle='--', alpha=0.5)
                    
            ax.set_title("数据质量分析：逐项基频曲线质量分布叠加", fontsize=13, fontweight="bold")
            ax.set_xlabel("测量点")
            
            if self.combo_scale.get().startswith("T"):
                ax.set_ylim(-0.2, 5.2)
                ax.set_yticks([0, 1, 2, 3, 4, 5])
                ax.set_ylabel("T 值 (0-5 标度)")
            else:
                ax.set_ylabel("频率 (Hz)")
                
            ax.legend(loc="upper right")
            return fig
            
        elif view == "active_ratio":
            # 2. Active Ratio (F0 tracking success rate) boxplot
            fig, ax = plt.subplots(figsize=(8, 5))
            ax.grid(True, linestyle="--", alpha=0.3)
            
            # Group by speaker
            spk_data = {}
            for entry in data_entries:
                spk = entry['speaker_name']
                if spk not in spk_data:
                    spk_data[spk] = []
                spk_data[spk].append(entry['active_ratio'])
                
            labels = []
            data_to_plot = []
            for spk, ratios in spk_data.items():
                labels.append(spk)
                data_to_plot.append(ratios)
                
            if data_to_plot:
                bp = ax.boxplot(data_to_plot, patch_artist=True)
                for patch in bp['boxes']:
                    patch.set_facecolor("#ECFDF5")
                    patch.set_color("#059669")
                for whisker in bp['whiskers']:
                    whisker.set_color("#059669")
                for cap in bp['caps']:
                    cap.set_color("#059669")
                for median in bp['medians']:
                    median.set_color("#DC2626")
                    median.set_linewidth(1.5)
                    
                ax.set_xticklabels(labels, fontsize=10, fontweight="bold")
                ax.set_ylabel("有效基频采样点比例 (Active Ratio)", fontsize=11)
                ax.set_title("各发音人录音有效率与基频检出比例分布比较", fontsize=13, fontweight="bold")
                
                # Horizontal line for 60% standard recommended floor
                ax.axhline(0.60, color="#EF4444", linestyle=":", label="常规建议的极低阈值 (60%)")
                ax.legend(loc="lower left")
                ax.set_ylim(-0.05, 1.05)
                
            return fig
            
        elif view == "speaker_means":
            # 3. Speaker means scatter plot showing robust domains
            fig, ax = plt.subplots(figsize=(9, 5))
            ax.grid(True, linestyle="--", alpha=0.3)
            
            for idx, entry in enumerate(data_entries):
                spk = entry['speaker_name']
                color = self.colors[hash(spk) % len(self.colors)]
                
                valid_ys = entry['raw_freqs'][entry['raw_freqs'] > 0]
                if len(valid_ys) > 0:
                    mean_f0 = np.mean(valid_ys)
                    ax.scatter(spk, mean_f0, color=color, alpha=0.4, edgecolors='none', s=40)
                    
            # Compute medians and errors per speaker
            spk_freqs_pool = {}
            for entry in data_entries:
                spk = entry['speaker_name']
                valid_ys = entry['raw_freqs'][entry['raw_freqs'] > 0]
                if len(valid_ys) > 0:
                    if spk not in spk_freqs_pool:
                        spk_freqs_pool[spk] = []
                    spk_freqs_pool[spk].append(np.mean(valid_ys))
                    
            for idx, (spk, means) in enumerate(spk_freqs_pool.items()):
                med = np.median(means)
                q1 = np.percentile(means, 25)
                q3 = np.percentile(means, 75)
                color = self.colors[hash(spk) % len(self.colors)]
                # Plot robust median & IQR
                ax.errorbar(spk, med, yerr=[[med - q1], [q3 - med]], fmt='D', color='black', ecolor=color, elinewidth=3, capsize=8, label=f"{spk} 中位数 ({med:.1f}Hz)" if idx < 5 else "")
                
            ax.set_title("各受试发音人基频均值及调域离散域 (用于快速排查八度音高跳变)", fontsize=12, fontweight="bold")
            ax.set_ylabel("发音基频均值 Mean F0 (Hz)")
            ax.legend(loc="upper right", fontsize=9)
            
            return fig
            
        fig, ax = plt.subplots()
        return fig

    # --- CONTROLLER: RE-RENDER LIVE PREVIEW ---
    def update_preview(self):
        # Clear existing preview canvas
        for widget in self.preview_container.winfo_children():
            widget.destroy()
            
        self.preview_lbl = ctk.CTkLabel(self.preview_container, text="⏳ 正在实时渲染声图，请稍候...", font=self.font_title)
        self.preview_lbl.grid(row=0, column=0)
        
        scope = self.var_export_scope.get()
        if scope == "separate" and len(self.all_speakers) > 1:
            self.pagination_frame.grid(row=1, column=0, pady=(0, 10))
            idx = getattr(self, 'current_preview_page', 0)
            if idx < 0 or idx >= len(self.all_speakers):
                idx = 0
                self.current_preview_page = 0
            spk_name = self.all_speakers[idx].name
            self.lbl_page_info.configure(text=f"发音人: {spk_name} ({idx+1}/{len(self.all_speakers)})")
        else:
            self.pagination_frame.grid_forget()
            
        self.update_idletasks()
        
        try:
            data = self._get_current_data_entries()
            if not data:
                self.preview_lbl.configure(text="❌ 没有检索到有效基频曲线，请导入有发音数据的项目！", text_color="#EF4444")
                return
                
            # Create Plot Figure
            fig = self.generate_plot(data, is_preview=True)
            
            # Embed matplotlib inside TK
            for widget in self.preview_container.winfo_children():
                widget.destroy()
                
            canvas = FigureCanvasTkAgg(fig, master=self.preview_container)
            canvas.draw()
            canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
            
            # Save a reference to the active figure so GC doesn't delete it
            self.active_figure = fig
        except Exception as e:
            self.preview_lbl.configure(text=f"❌ 图表渲染发生错误: {str(e)[:35]}", text_color="#EF4444")
            import logging
            logging.getLogger(__name__).error(f"Render chart error: {e}", exc_info=True)

    # --- CONFIRM & EXPORT CONTROLLER ---
    def on_confirm(self):
        scope = self.var_export_scope.get()
        fmt = self.combo_format.get()
        ext = ".png"
        if "svg" in fmt.lower():
            ext = ".svg"
        elif "pdf" in fmt.lower():
            ext = ".pdf"
            
        if scope == "separate" and len(self.all_speakers) > 1:
            # Batch separate exports to a directory
            out_dir = filedialog.askdirectory(title="选择声学图表导出文件夹")
            if not out_dir:
                return
                
            # Progress window
            prog = ctk.CTkToplevel(self)
            prog.title("导出进行中")
            prog.geometry("300x120")
            prog.resizable(False, False)
            prog.update_idletasks()
            
            lbl = ctk.CTkLabel(prog, text="正在批量绘制各发音人图表...", font=self.font_main)
            lbl.pack(pady=(20, 5))
            pbar = ctk.CTkProgressBar(prog, width=240)
            pbar.pack()
            pbar.set(0)
            
            try:
                for idx, speaker in enumerate(self.all_speakers):
                    lbl.configure(text=f"正在绘制 {speaker.name} ({idx+1}/{len(self.all_speakers)})...")
                    pbar.set(idx / len(self.all_speakers))
                    prog.update()
                    
                    # Extract single speaker's entries
                    data = self._extract_active_data([speaker])
                    if data:
                        out_path = os.path.join(out_dir, f"{speaker.name}_声调可视化图表{ext}")
                        self._export_dataset(data, out_path, ext)
                        
                prog.destroy()
                messagebox.showinfo("成功", f"批量图表成功导出至:\n{out_dir}")
                self.destroy()
            except Exception as e:
                prog.destroy()
                messagebox.showerror("错误", f"批量图表导出失败: {e}")
        else:
            # Single/Integrated export to a file
            default_name = "tone_integrated_acoustic_charts" if scope == "integrated" else "tone_acoustic_charts"
            out_file = filedialog.asksaveasfilename(
                title="导出声图", 
                defaultextension=ext, 
                initialfile=default_name, 
                filetypes=[("图像文件", f"*{ext}")]
            )
            if not out_file:
                return
                
            try:
                data = self._get_current_data_entries()
                if not data:
                    return messagebox.showwarning("提示", "没有有效基频曲线，无法导出！")
                    
                self._export_dataset(data, out_file, ext)
                
                messagebox.showinfo("成功", f"图表已成功保存至:\n{out_file}")
                self.destroy()
            except Exception as e:
                messagebox.showerror("错误", f"图表导出失败: {e}")

    def _export_paginated_pdf(self, out_file, data_entries, group_key, scale):
        from matplotlib.backends.backend_pdf import PdfPages
        
        unique_groups = []
        for e in data_entries:
            val = e[group_key]
            if val not in unique_groups:
                unique_groups.append(val)
                
        chunk_size = 8
        pdf_pages = PdfPages(out_file)
        
        try:
            for page_idx in range(math.ceil(len(unique_groups) / chunk_size)):
                allowed_groups = set(unique_groups[page_idx * chunk_size : (page_idx + 1) * chunk_size])
                chunk_entries = [e for e in data_entries if e[group_key] in allowed_groups]
                
                fig = self.generate_plot(chunk_entries, is_preview=False)
                
                # Add page number to figure
                fig.text(0.95, 0.02, f"第 {page_idx + 1} 页 / 共 {math.ceil(len(unique_groups) / chunk_size)} 页",
                         ha='right', va='bottom', fontsize=9, color='gray')
                         
                pdf_pages.savefig(fig, bbox_inches='tight')
                plt.close(fig)
        finally:
            pdf_pages.close()

    def _export_paginated_images(self, base_path, data_entries, group_key, scale, ext):
        unique_groups = []
        for e in data_entries:
            val = e[group_key]
            if val not in unique_groups:
                unique_groups.append(val)
                
        chunk_size = 8
        total_pages = math.ceil(len(unique_groups) / chunk_size)
        
        dir_name, file_name = os.path.split(base_path)
        name_part, _ = os.path.splitext(file_name)
        
        for page_idx in range(total_pages):
            allowed_groups = set(unique_groups[page_idx * chunk_size : (page_idx + 1) * chunk_size])
            chunk_entries = [e for e in data_entries if e[group_key] in allowed_groups]
            
            fig = self.generate_plot(chunk_entries, is_preview=False)
            
            fig.text(0.95, 0.02, f"第 {page_idx + 1} 页 / 共 {total_pages} 页",
                     ha='right', va='bottom', fontsize=9, color='gray')
            
            out_path = os.path.join(dir_name, f"{name_part}_第{page_idx + 1}页{ext}")
            fig.savefig(out_path, dpi=300, bbox_inches='tight')
            plt.close(fig)

    def _export_dataset(self, data, out_path, ext):
        chart_type = self.var_chart_type.get()
        groupby = self.combo_groupby.get()
        
        group_key = 'group'
        if groupby == "按词语":
            group_key = 'label'
        elif groupby == "按发音人":
            group_key = 'speaker_name'
            
        unique_groups = []
        for e in data:
            val = e[group_key]
            if val not in unique_groups:
                unique_groups.append(val)
                
        if len(unique_groups) > 8 and chart_type != "overview_heatmap":
            if ext == ".pdf":
                self._export_paginated_pdf(out_path, data, group_key, self.combo_scale.get())
            else:
                self._export_paginated_images(out_path, data, group_key, self.combo_scale.get(), ext)
        else:
            fig = self.generate_plot(data, is_preview=False)
            fig.savefig(out_path, dpi=300, bbox_inches='tight')
            plt.close(fig)
