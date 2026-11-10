"""
modules.toolkit_ui.tab_textgrid_wordlist_mixin: Tab 3 TextGrid 转换器与 Tab 4 高级字表编辑器 Mixin。
"""
import os
import sys
import copy
import traceback
import typing
import builtins
import modules.wordlist_v2 as _wlv2

for _t_name in ("Optional", "Dict", "List", "Any", "Union", "Tuple", "Callable"):
    if not hasattr(builtins, _t_name):
        setattr(builtins, _t_name, getattr(typing, _t_name, None))

for _w_name in (
    "normalize_wordlist_document",
    "DEFAULT_REVIEW_STATUS",
    "AI_REVIEW_STATUS",
    "REVIEWED_STATUS",
    "CORE_META_FIELDS",
    "build_document_from_csv_text",
    "build_document_from_structured_text",
    "build_document_from_v1_text",
    "document_to_csv_text",
    "document_to_v1_text",
    "flatten_wordlist_document",
    "load_wordlist_document",
    "mark_ai_fields_reviewed",
    "save_wordlist_document",
    "summarize_wordlist_document",
    "validate_wordlist_document",
    "looks_like_csv_wordlist_text",
    "looks_like_structured_wordlist_text",
):
    if not hasattr(builtins, _w_name) and hasattr(_wlv2, _w_name):
        setattr(builtins, _w_name, getattr(_wlv2, _w_name))

import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import customtkinter as ctk

from modules.ui_widgets import _apply_custom_arrow
from modules.data_utils import make_textgrid_export_stem
from modules.core.text_io import read_text_file_with_fallback
from modules.textgrid_converter import (
    DEFAULT_GROUP_NAME,
    NONE_TIER_LABEL,
    PAIR_MODE_ADJACENT,
    PAIR_MODE_NONE,
    TextGridMapping,
    convert_textgrid,
    inspect_textgrid,
    load_auxiliary_wordlist_records,
    preview_textgrid_conversion,
    recommend_tier_names,
)
from modules.wordlist_editor import RoundedGroupList, VisualWordlistEditor
from modules.wordlist_v2 import (
    ADVANCED_WORDLIST_AGENT_PROMPT,
    build_document_from_csv_text,
    build_document_from_v1_text,
    document_to_v1_text,
    flatten_wordlist_document,
    load_wordlist_document,
)
from modules.toolkit_ui.tk_utils import start_safe_thread


class TextGridWordlistTabMixin:
    def _make_converter_option(self, parent, values, command=None, width=190):
        combo = ctk.CTkOptionMenu(
            parent,
            values=values or [NONE_TIER_LABEL],
            command=command,
            width=width,
            height=36,
            corner_radius=18,
            fg_color=self.colors["surface"],
            text_color=self.colors["text"],
            button_color=self.colors["surface"],
            button_hover_color="#E5E7EB",
            dropdown_fg_color=self.colors["surface"],
            dropdown_hover_color="#EFF6FF",
            dropdown_text_color=self.colors["text"],
            font=self.font_main,
            dropdown_font=self.font_main,
        )
        _apply_custom_arrow(combo)
        return combo


    def build_textgrid_tab(self):
        self.var_textgrid_group_tier = ctk.StringVar(value=NONE_TIER_LABEL)
        self.var_textgrid_item_tier = ctk.StringVar(value=NONE_TIER_LABEL)
        self.var_textgrid_core_tier = ctk.StringVar(value=NONE_TIER_LABEL)
        self.var_textgrid_pair_mode = ctk.BooleanVar(value=False)

        content = ctk.CTkFrame(self.tab_textgrid, fg_color="transparent")
        content.pack(fill=tk.BOTH, expand=True, padx=20, pady=18)
        content.grid_columnconfigure(0, weight=1)
        content.grid_columnconfigure(1, weight=0)
        content.grid_rowconfigure(0, weight=1)

        # 左栏：源文件摘要 + 转换预览
        left_panel = ctk.CTkFrame(content, fg_color="transparent")
        left_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 16))

        source_card = self._make_card(left_panel, fg_color=self.colors["surface_soft"])
        source_card.pack(fill=tk.X, side=tk.TOP, pady=(0, 12))
        source_card.grid_columnconfigure(0, weight=1)

        source_header = ctk.CTkFrame(source_card, fg_color="transparent")
        source_header.grid(row=0, column=0, sticky="ew", padx=20, pady=(8, 4))
        source_header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(source_header, text="源文件与层摘要", font=self.font_title, text_color=self.colors["text"]).grid(row=0, column=0, sticky="w")
        self._make_button(
            source_header,
            "导入 TextGrid",
            self.select_textgrid_source,
            tone="primary",
            image=self.icons.get("import_white"),
            width=148,
            height=36,
        ).grid(row=0, column=1, sticky="e")

        path_pill = ctk.CTkFrame(source_card, fg_color=self.colors["surface"], corner_radius=999, border_width=1, border_color=self.colors["border"])
        path_pill.grid(row=1, column=0, sticky="ew", padx=20, pady=(0, 4))
        self.lbl_textgrid_source = ctk.CTkLabel(path_pill, text="未选择 TextGrid 文件", text_color=self.colors["muted"], font=self.font_small, anchor="w")
        self.lbl_textgrid_source.pack(fill=tk.X, padx=14, pady=5)

        source_body = ctk.CTkFrame(source_card, fg_color="transparent")
        source_body.grid(row=2, column=0, sticky="ew", padx=20, pady=(0, 10))
        source_body.grid_columnconfigure(0, weight=0, minsize=245)
        source_body.grid_columnconfigure(1, weight=1)
        source_body.grid_rowconfigure(0, weight=1)

        file_container = ctk.CTkFrame(source_body, fg_color=self.colors["surface"], corner_radius=8, border_width=1, border_color=self.colors["border"], width=245, height=150)
        file_container.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        file_container.grid_propagate(False)
        file_container.grid_columnconfigure(0, weight=1)
        file_container.grid_rowconfigure(1, weight=1)
        ctk.CTkLabel(file_container, text="待转换文件", font=self.font_title, text_color=self.colors["text"]).grid(row=0, column=0, sticky="w", padx=12, pady=(10, 4))
        self.textgrid_file_list = RoundedGroupList(file_container, colors=self.colors, font_family=self.font_family)
        self.textgrid_file_list.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 10))
        self.textgrid_file_list.bind("<<ListboxSelect>>", self.on_textgrid_file_selected)
        self.textgrid_files_empty_state = ctk.CTkFrame(file_container, fg_color="transparent")
        ctk.CTkLabel(self.textgrid_files_empty_state, text="可一次选择多个 TextGrid", font=self.font_caption, text_color=self.colors["muted"]).pack()
        self.textgrid_files_empty_state.place(relx=0.5, rely=0.5, anchor="center")

        tier_container = ctk.CTkFrame(source_body, fg_color=self.colors["surface"], corner_radius=14, border_width=1, border_color=self.colors["border"], height=150)
        tier_container.grid(row=0, column=1, sticky="nsew")
        tier_container.grid_propagate(False)
        tier_container.grid_columnconfigure(0, weight=1)
        tier_container.grid_rowconfigure(0, weight=1)
        self.tree_textgrid_tiers = ttk.Treeview(
            tier_container,
            columns=("name", "type", "count", "samples"),
            show="headings",
            height=3,
            selectmode="browse",
        )
        self.tree_textgrid_tiers.heading("name", text="层名")
        self.tree_textgrid_tiers.heading("type", text="类型")
        self.tree_textgrid_tiers.heading("count", text="非空/总数")
        self.tree_textgrid_tiers.heading("samples", text="示例标签")
        self.tree_textgrid_tiers.column("name", width=130, anchor="w")
        self.tree_textgrid_tiers.column("type", width=110, anchor="center")
        self.tree_textgrid_tiers.column("count", width=90, anchor="center")
        self.tree_textgrid_tiers.column("samples", width=420, anchor="w")
        self.tree_textgrid_tiers.configure(style="Converter.Treeview", takefocus=False)
        self.textgrid_tier_scroll = ctk.CTkScrollbar(tier_container, orientation="vertical", command=self.tree_textgrid_tiers.yview, width=12, height=120)
        self.tree_textgrid_tiers.configure(yscrollcommand=self.textgrid_tier_scroll.set)
        self.tree_textgrid_tiers.grid(row=0, column=0, sticky="nsew", padx=(1, 0), pady=1)
        self.textgrid_tier_scroll.grid(row=0, column=1, sticky="ns", padx=(0, 1), pady=1)
        self.textgrid_tier_empty_state = ctk.CTkFrame(tier_container, fg_color="transparent")
        ctk.CTkLabel(self.textgrid_tier_empty_state, text="选择文件后显示 IntervalTier 摘要", font=self.font_main, text_color=self.colors["muted"]).pack()
        self.textgrid_tier_empty_state.place(relx=0.5, rely=0.5, anchor="center")

        preview_card = self._make_card(left_panel)
        preview_card.pack(fill=tk.BOTH, expand=True, side=tk.TOP)
        preview_card.grid_columnconfigure(0, weight=1)
        preview_card.grid_rowconfigure(1, weight=1)

        preview_header = ctk.CTkFrame(preview_card, fg_color="transparent")
        preview_header.grid(row=0, column=0, sticky="ew", padx=20, pady=(18, 12))
        preview_header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(preview_header, text="转换预览", font=self.font_title, text_color=self.colors["text"]).grid(row=0, column=0, sticky="w")
        self.lbl_textgrid_preview_count = ctk.CTkLabel(preview_header, text="0 个条目", font=self.font_small, text_color=self.colors["muted"])
        self.lbl_textgrid_preview_count.grid(row=0, column=1, sticky="e")
        ctk.CTkLabel(
            preview_header,
            text="可多选条目并移动到右侧输入的临时组；导出时写入 groups / words / chars 三层。",
            font=self.font_small,
            text_color=self.colors["muted"],
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 0))

        preview_container = ctk.CTkFrame(preview_card, fg_color=self.colors["surface"], corner_radius=14, border_width=1, border_color=self.colors["border"])
        preview_container.grid(row=1, column=0, sticky="nsew", padx=20, pady=(0, 12))
        preview_container.grid_columnconfigure(0, weight=1)
        preview_container.grid_rowconfigure(0, weight=1)
        self.tree_textgrid_preview = ttk.Treeview(
            preview_container,
            columns=("label", "group", "core", "chars", "status"),
            show="headings",
            height=12,
            selectmode="extended",
        )
        self.tree_textgrid_preview.heading("label", text="条目")
        self.tree_textgrid_preview.heading("group", text="组名")
        self.tree_textgrid_preview.heading("core", text="核心边界")
        self.tree_textgrid_preview.heading("chars", text="字区间")
        self.tree_textgrid_preview.heading("status", text="状态")
        self.tree_textgrid_preview.column("label", width=120, anchor="w")
        self.tree_textgrid_preview.column("group", width=150, anchor="w")
        self.tree_textgrid_preview.column("core", width=130, anchor="center")
        self.tree_textgrid_preview.column("chars", width=70, anchor="center")
        self.tree_textgrid_preview.column("status", width=330, anchor="w")
        self.tree_textgrid_preview.configure(style="Converter.Treeview", takefocus=False)
        self.tree_textgrid_preview.tag_configure("warning", background="#FEF3C7")
        self.textgrid_preview_scroll = ctk.CTkScrollbar(preview_container, orientation="vertical", command=self.tree_textgrid_preview.yview, width=12)
        self.tree_textgrid_preview.configure(yscrollcommand=self.textgrid_preview_scroll.set)
        self.tree_textgrid_preview.grid(row=0, column=0, sticky="nsew", padx=(1, 0), pady=1)
        self.textgrid_preview_scroll.grid(row=0, column=1, sticky="ns", padx=(0, 1), pady=1)
        self.textgrid_preview_empty_state = ctk.CTkFrame(preview_container, fg_color="transparent")
        ctk.CTkLabel(self.textgrid_preview_empty_state, text="选择 TextGrid 并确认层映射后显示预览", font=self.font_main, text_color=self.colors["muted"]).pack()
        self.textgrid_preview_empty_state.place(relx=0.5, rely=0.5, anchor="center")

        preview_footer = ctk.CTkFrame(preview_card, fg_color="transparent")
        preview_footer.grid(row=2, column=0, sticky="ew", padx=20, pady=(0, 16))

        self.lbl_textgrid_status = ctk.CTkLabel(preview_footer, text="等待选择源文件。", font=self.font_small, text_color=self.colors["muted"], anchor="w")
        self.lbl_textgrid_status.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 10))

        self.btn_export_textgrid = self._make_button(
            preview_footer,
            "导出标准 TextGrid",
            self.export_converted_textgrid,
            tone="success",
            image=self.icons.get("save"),
            height=36,
        )
        self.btn_export_textgrid.pack(side=tk.RIGHT)
        self.btn_batch_export_textgrid = self._make_button(
            preview_footer,
            "批量导出",
            self.export_converted_textgrids_batch,
            tone="purple",
            height=36,
            width=96,
        )
        self.btn_batch_export_textgrid.pack(side=tk.RIGHT, padx=(0, 8))

        # 右栏：层映射 + 导出设置
        right_panel = ctk.CTkFrame(content, fg_color="transparent", width=340)
        right_panel.grid(row=0, column=1, sticky="nsew")
        right_panel.grid_propagate(False)
        right_panel.pack_propagate(False)

        mapping_card = self._make_card(right_panel, fg_color=self.colors["surface_soft"], width=340)
        mapping_card.pack(fill=tk.X, side=tk.TOP, pady=(0, 16))

        mapping_body = ctk.CTkFrame(mapping_card, fg_color="transparent")
        self.mapping_body = mapping_body

        self.mapping_is_collapsed = False
        self.export_is_collapsed = True

        def toggle_mapping():
            if self.mapping_is_collapsed:
                self.mapping_body.pack(fill=tk.X, padx=20, pady=(0, 10))
                self.header_mapping.toggle_btn.configure(text="折叠")
                self.mapping_is_collapsed = False

                if not self.export_is_collapsed:
                    self.export_body.pack_forget()
                    self.export_card.pack(fill=tk.X, side=tk.TOP, pady=(0, 16))
                    self.header_export.toggle_btn.configure(text="展开")
                    self.export_is_collapsed = True
            else:
                self.mapping_body.pack_forget()
                self.header_mapping.toggle_btn.configure(text="展开")
                self.mapping_is_collapsed = True

        def toggle_export():
            if self.export_is_collapsed:
                self.export_card.pack(fill=tk.BOTH, expand=True, side=tk.TOP)
                self.export_body.pack(fill=tk.BOTH, expand=True, padx=20, pady=(0, 12))
                self.header_export.toggle_btn.configure(text="折叠")
                self.export_is_collapsed = False

                if not self.mapping_is_collapsed:
                    self.mapping_body.pack_forget()
                    self.header_mapping.toggle_btn.configure(text="展开")
                    self.mapping_is_collapsed = True
            else:
                self.export_body.pack_forget()
                self.export_card.pack(fill=tk.X, side=tk.TOP, pady=(0, 16))
                self.header_export.toggle_btn.configure(text="展开")
                self.export_is_collapsed = True

        self.header_mapping = self._section_header(
            mapping_card,
            "层映射",
            "条目层给词项标签，核心层给实际分析边界。",
            icon_text="03",
            show_toggle=True,
            toggle_cmd=toggle_mapping,
        )

        self.mapping_body.pack(fill=tk.X, padx=20, pady=(0, 10))

        # 采用 Grid 布局横向排列下拉菜单，合并组层和条目层在同一行
        dropdowns_frame = ctk.CTkFrame(mapping_body, fg_color="transparent")
        dropdowns_frame.pack(fill=tk.X, pady=(0, 8))
        dropdowns_frame.grid_columnconfigure(0, weight=0)
        dropdowns_frame.grid_columnconfigure(1, weight=1)
        dropdowns_frame.grid_columnconfigure(2, weight=0)
        dropdowns_frame.grid_columnconfigure(3, weight=1)

        # 组层
        lbl_group = ctk.CTkLabel(dropdowns_frame, text="组层", font=self.font_small, text_color=self.colors["text_soft"], anchor="w")
        lbl_group.grid(row=0, column=0, sticky="w", padx=(0, 6), pady=4)
        self.combo_textgrid_group = self._make_converter_option(
            dropdowns_frame,
            [NONE_TIER_LABEL],
            command=lambda _value: self.refresh_textgrid_preview(),
            width=90,
        )
        self.combo_textgrid_group.configure(variable=self.var_textgrid_group_tier)
        self.combo_textgrid_group.grid(row=0, column=1, sticky="ew", padx=(0, 12), pady=4)

        # 条目层
        lbl_item = ctk.CTkLabel(dropdowns_frame, text="条目层", font=self.font_small, text_color=self.colors["text_soft"], anchor="w")
        lbl_item.grid(row=0, column=2, sticky="w", padx=(0, 6), pady=4)
        self.combo_textgrid_item = self._make_converter_option(
            dropdowns_frame,
            [NONE_TIER_LABEL],
            command=lambda _value: self.refresh_textgrid_preview(),
            width=90,
        )
        self.combo_textgrid_item.configure(variable=self.var_textgrid_item_tier)
        self.combo_textgrid_item.grid(row=0, column=3, sticky="ew", pady=4)

        # 核心层
        lbl_core = ctk.CTkLabel(dropdowns_frame, text="核心层", font=self.font_small, text_color=self.colors["text_soft"], anchor="w")
        lbl_core.grid(row=1, column=0, sticky="w", padx=(0, 6), pady=4)
        self.combo_textgrid_core = self._make_converter_option(
            dropdowns_frame,
            [NONE_TIER_LABEL],
            command=lambda _value: self.refresh_textgrid_preview(),
            width=190,
        )
        self.combo_textgrid_core.configure(variable=self.var_textgrid_core_tier)
        self.combo_textgrid_core.grid(row=1, column=1, columnspan=3, sticky="ew", pady=4)

        # 辅助字表栏：按钮并排，布局更紧凑
        wordlist_box = ctk.CTkFrame(mapping_body, fg_color=self.colors["surface"], corner_radius=12, border_width=1, border_color=self.colors["border"])
        wordlist_box.pack(fill=tk.X, pady=(0, 8))

        wl_header = ctk.CTkFrame(wordlist_box, fg_color="transparent")
        wl_header.pack(fill=tk.X, padx=12, pady=(8, 4))

        ctk.CTkLabel(
            wl_header,
            text="辅助字表",
            font=ctk.CTkFont(family=self.font_family, size=13, weight="bold"),
            text_color=self.colors["text"],
        ).pack(side=tk.LEFT)

        self.btn_wordlist_clear = self._make_button(
            wl_header,
            "清除",
            self.clear_textgrid_aux_wordlist,
            tone="ghost",
            height=26,
            width=50,
        )
        self.btn_wordlist_clear.pack(side=tk.RIGHT, padx=(4, 0))

        self.btn_wordlist_import = self._make_button(
            wl_header,
            "导入",
            self.import_textgrid_aux_wordlist,
            tone="secondary",
            height=26,
            width=50,
        )
        self.btn_wordlist_import.pack(side=tk.RIGHT)

        self.lbl_textgrid_wordlist = ctk.CTkLabel(
            wordlist_box,
            text="未导入；二字组将按时间顺序判断。",
            font=self.font_caption,
            text_color=self.colors["muted"],
            wraplength=260,
            justify="left",
            anchor="w",
        )
        self.lbl_textgrid_wordlist.pack(fill=tk.X, padx=12, pady=(0, 8))

        # 智能合并开关：缩短说明文本，取消不必要的高度占用
        switch_box = ctk.CTkFrame(mapping_body, fg_color=self.colors["surface"], corner_radius=12, border_width=1, border_color=self.colors["border"])
        switch_box.pack(fill=tk.X, pady=(2, 0))

        self.switch_textgrid_pair = ctk.CTkSwitch(
            switch_box,
            text="启用二字组智能合并",
            variable=self.var_textgrid_pair_mode,
            command=self.refresh_textgrid_preview,
            font=self.font_main,
            progress_color=self.colors["purple"],
            button_color="#475569",
            button_hover_color="#334155",
        )
        self.switch_textgrid_pair.pack(anchor="w", padx=12, pady=(8, 4))

        ctk.CTkLabel(
            switch_box,
            text="根据字表或时间顺序合并二字组",
            font=self.font_caption,
            text_color=self.colors["muted"],
            anchor="w",
        ).pack(fill=tk.X, padx=12, pady=(0, 8))

        self.export_card = self._make_card(right_panel, fg_color=self.colors["surface_soft"], width=340)
        self.export_card.pack(fill=tk.X, side=tk.TOP, pady=(0, 16))
        self.export_card.grid_propagate(False)

        self.header_export = self._section_header(
            self.export_card,
            "临时分组与导出",
            "无组层时可在这里把条目分入临时组。",
            icon_text="04",
            show_toggle=True,
            toggle_cmd=toggle_export,
        )
        self.header_export.toggle_btn.configure(text="展开")

        self.export_body = ctk.CTkFrame(self.export_card, fg_color="transparent")

        group_body = ctk.CTkFrame(self.export_body, fg_color="transparent")
        group_body.pack(fill=tk.X, padx=20, pady=(0, 12))
        ctk.CTkLabel(group_body, text="目标组名", font=self.font_small, text_color=self.colors["text_soft"]).pack(anchor="w")
        self.entry_textgrid_group = ctk.CTkEntry(
            group_body,
            placeholder_text=DEFAULT_GROUP_NAME,
            height=38,
            corner_radius=19,
            border_color=self.colors["border_strong"],
            fg_color=self.colors["surface"],
            text_color=self.colors["text"],
            font=self.font_main,
        )
        self.entry_textgrid_group.pack(fill=tk.X, pady=(6, 10))

        assign_row = ctk.CTkFrame(group_body, fg_color="transparent")
        assign_row.pack(fill=tk.X, pady=(0, 10))
        self._make_button(assign_row, "应用到选中", self.add_textgrid_group, tone="purple", height=34).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 8))
        self._make_button(assign_row, "清除选中", self.clear_textgrid_group_override, tone="secondary", height=34).pack(side=tk.LEFT, expand=True, fill=tk.X)

        diagnosis_box = ctk.CTkFrame(self.export_body, fg_color=self.colors["surface"], corner_radius=14, border_width=1, border_color=self.colors["border"])
        diagnosis_box.pack(fill=tk.BOTH, expand=True, padx=20, pady=(0, 12))
        ctk.CTkLabel(
            diagnosis_box,
            text="二字组体检",
            font=ctk.CTkFont(family=self.font_family, size=13, weight="bold"),
            text_color=self.colors["text"],
        ).pack(anchor="w", padx=14, pady=(12, 4))
        self.lbl_textgrid_diagnosis = ctk.CTkLabel(
            diagnosis_box,
            text="尚无可诊断内容。",
            font=self.font_small,
            text_color=self.colors["muted"],
            wraplength=278,
            justify="left",
            anchor="w",
        )
        self.lbl_textgrid_diagnosis.pack(fill=tk.BOTH, expand=True, padx=14, pady=(0, 12), anchor="nw")

        actions = ctk.CTkFrame(self.export_body, fg_color="transparent")
        actions.pack(side=tk.BOTTOM, fill=tk.X, padx=20, pady=20)
        self._make_button(actions, " 刷新预览", self.refresh_textgrid_preview, tone="secondary", image=self.icons.get("refresh_dark"), height=36).pack(fill=tk.X)


    def select_textgrid_source(self):
        paths = filedialog.askopenfilenames(
            filetypes=[
                ("Praat TextGrid", "*.TextGrid *.textgrid"),
                ("All Files", "*.*"),
            ]
        )
        if paths:
            self.load_textgrid_sources(list(paths))


    def load_textgrid_sources(self, paths):
        cleaned = []
        seen = set()
        for path in paths or []:
            normalized = os.path.normpath(str(path))
            seen_key = os.path.normcase(os.path.abspath(normalized)) if normalized else ""
            if normalized and normalized.lower().endswith(".textgrid") and seen_key not in seen:
                cleaned.append(normalized)
                seen.add(seen_key)
        if not cleaned:
            return
        self.textgrid_source_paths = cleaned
        self._fill_textgrid_files_tree(cleaned)
        self.load_textgrid_source(cleaned[0], update_sources=False)


    def load_textgrid_source(self, path, update_sources=True):
        if not path:
            return
        try:
            summary = inspect_textgrid(path)
        except Exception as e:
            messagebox.showerror("读取失败", f"无法读取 TextGrid：\n{e}")
            return

        if update_sources:
            normalized = os.path.normpath(str(path))
            self.textgrid_source_paths = [normalized]
            self._fill_textgrid_files_tree(self.textgrid_source_paths)
            path = normalized
        self.textgrid_source_path = path
        self.textgrid_summary = summary
        self.textgrid_group_overrides = {}
        self.textgrid_preview_items = []
        if hasattr(self, "lbl_textgrid_source"):
            total = len(getattr(self, "textgrid_source_paths", []) or [])
            if total > 1:
                current = (self.textgrid_source_paths.index(path) + 1) if path in self.textgrid_source_paths else 1
                text = f"当前 {current}/{total}：{self._short_path(path, max_parts=3)}"
            else:
                text = self._short_path(path, max_parts=4)
            self.lbl_textgrid_source.configure(text=text, text_color=self.colors["text_soft"])
        self._select_textgrid_file_in_tree(path)
        self._fill_textgrid_tiers_tree(summary.get("tiers", []))
        self.refresh_textgrid_tier_options()
        self.tabview.set(self.tab_textgrid_name)
        self._textgrid_status(f"已读取 {len(getattr(self, 'textgrid_source_paths', []) or [path])} 个 TextGrid，请确认层映射。", "muted")


    def _fill_textgrid_files_tree(self, paths):
        if not hasattr(self, "textgrid_file_list"):
            return
        self.textgrid_file_list_paths = list(paths or [])
        self.textgrid_file_list.delete(0, tk.END)
        basename_counts = {}
        for path in self.textgrid_file_list_paths:
            name = os.path.basename(path)
            basename_counts[name] = basename_counts.get(name, 0) + 1
        for idx, path in enumerate(paths or []):
            basename = os.path.basename(path)
            label = self._short_path(path, max_parts=2) if basename_counts.get(basename, 0) > 1 else basename
            self.textgrid_file_list.insert(tk.END, f"{idx + 1:02d}  {label}")
        if paths:
            self.textgrid_files_empty_state.place_forget()
        else:
            self.textgrid_files_empty_state.place(relx=0.5, rely=0.5, anchor="center")


    def _select_textgrid_file_in_tree(self, path):
        if not hasattr(self, "textgrid_file_list"):
            return
        target = os.path.normcase(os.path.abspath(path))
        for index, item_path in enumerate(getattr(self, "textgrid_file_list_paths", []) or []):
            if os.path.normcase(os.path.abspath(item_path)) == target:
                self.textgrid_file_list.selection_set(index, notify=False)
                self.textgrid_file_list.activate(index)
                break


    def on_textgrid_file_selected(self, _event=None):
        if not hasattr(self, "textgrid_file_list"):
            return
        selected = self.textgrid_file_list.curselection()
        if not selected:
            return
        index = selected[0]
        paths = getattr(self, "textgrid_file_list_paths", []) or []
        if index < 0 or index >= len(paths):
            return
        path = paths[index]
        if path and os.path.normpath(path) != os.path.normpath(getattr(self, "textgrid_source_path", "") or ""):
            self.load_textgrid_source(path, update_sources=False)


    def import_textgrid_aux_wordlist(self):
        path = filedialog.askopenfilename(
            filetypes=[
                ("支持的字表", "*.txt *.csv *.ptwl"),
                ("普通字表", "*.txt *.csv"),
                ("PhonTracer 高级字表", "*.ptwl"),
                ("All Files", "*.*"),
            ]
        )
        if not path:
            return
        try:
            records, groups_count = self._load_textgrid_aux_wordlist_records(path)
        except Exception as e:
            messagebox.showerror("读取失败", f"无法读取辅助字表：\n{e}")
            return
        if not records:
            messagebox.showwarning("提示", "辅助字表中没有可用词项。")
            return

        self.textgrid_wordlist_records = records
        self.textgrid_wordlist_path = path
        if hasattr(self, "lbl_textgrid_wordlist"):
            self.lbl_textgrid_wordlist.configure(
                text=f"已导入 {self._short_path(path, max_parts=2)}；{groups_count} 组，{len(records)} 项。",
                text_color=self.colors["success"],
            )
        self.refresh_textgrid_preview()


    def clear_textgrid_aux_wordlist(self):
        self.textgrid_wordlist_records = []
        self.textgrid_wordlist_path = None
        if hasattr(self, "lbl_textgrid_wordlist"):
            self.lbl_textgrid_wordlist.configure(
                text="未导入；二字组将按时间顺序判断。",
                text_color=self.colors["muted"],
            )
        self.refresh_textgrid_preview()


    def _load_textgrid_aux_wordlist_records(self, path):
        return load_auxiliary_wordlist_records(path)


    def refresh_textgrid_tier_options(self):
        summary = self.textgrid_summary or {}
        tiers = [tier for tier in summary.get("tiers", []) if tier.get("supported")]
        names = [str(tier.get("name", "")).strip() for tier in tiers if str(tier.get("name", "")).strip()]
        if not names:
            names = [NONE_TIER_LABEL]

        group_values = [NONE_TIER_LABEL] + [name for name in names if name != NONE_TIER_LABEL]
        recommendations = recommend_tier_names(summary) if summary else {}
        defaults = {
            "group": recommendations.get("group_tier") or NONE_TIER_LABEL,
            "item": recommendations.get("item_tier") or names[0],
            "core": recommendations.get("core_tier") or (names[1] if len(names) > 1 else names[0]),
        }

        for combo, values in (
            (getattr(self, "combo_textgrid_group", None), group_values),
            (getattr(self, "combo_textgrid_item", None), names),
            (getattr(self, "combo_textgrid_core", None), names),
        ):
            if combo is not None:
                combo.configure(values=values)

        self.var_textgrid_group_tier.set(defaults["group"] if defaults["group"] in group_values else NONE_TIER_LABEL)
        self.var_textgrid_item_tier.set(defaults["item"] if defaults["item"] in names else names[0])
        self.var_textgrid_core_tier.set(defaults["core"] if defaults["core"] in names else names[0])
        self.refresh_textgrid_preview()


    def _selected_textgrid_mapping(self):
        item_tier = (self.var_textgrid_item_tier.get() or "").strip()
        core_tier = (self.var_textgrid_core_tier.get() or "").strip()
        group_tier = (self.var_textgrid_group_tier.get() or "").strip()
        if item_tier in ("", NONE_TIER_LABEL) or core_tier in ("", NONE_TIER_LABEL):
            raise ValueError("请选择条目层和核心层。")
        if group_tier in ("", NONE_TIER_LABEL):
            group_tier = None
        return TextGridMapping(
            item_tier=item_tier,
            core_tier=core_tier,
            group_tier=group_tier,
            pair_mode=PAIR_MODE_ADJACENT if self.var_textgrid_pair_mode.get() else PAIR_MODE_NONE,
        )


    def refresh_textgrid_preview(self, *_args):
        if not getattr(self, "textgrid_source_path", None):
            self._fill_textgrid_preview_tree([])
            self._textgrid_status("等待选择源文件。", "muted")
            return
        try:
            mapping = self._selected_textgrid_mapping()
            preview = preview_textgrid_conversion(
                self.textgrid_source_path,
                mapping,
                group_overrides=self.textgrid_group_overrides,
                wordlist_records=self.textgrid_wordlist_records,
            )
        except ValueError as e:
            self.textgrid_preview_items = []
            self._fill_textgrid_preview_tree([])
            self.lbl_textgrid_diagnosis.configure(text="尚无可诊断内容。")
            self._textgrid_status(str(e), "warning")
            return
        except Exception as e:
            self.textgrid_preview_items = []
            self._fill_textgrid_preview_tree([])
            self.lbl_textgrid_diagnosis.configure(text=f"预览失败：{e}")
            self._textgrid_status(f"预览失败：{e}", "danger")
            return

        self.textgrid_preview_items = preview.items
        self._fill_textgrid_preview_tree(preview.items)
        self.lbl_textgrid_diagnosis.configure(text=self._format_textgrid_diagnosis(preview))
        warn_count = sum(len(item.warnings) for item in preview.items)
        if warn_count:
            self._textgrid_status(f"预览完成，发现 {warn_count} 条需要确认的提示。", "warning")
        else:
            self._textgrid_status("预览完成，可以导出。", "success")


    def _fill_textgrid_tiers_tree(self, tiers):
        if not hasattr(self, "tree_textgrid_tiers"):
            return
        self.tree_textgrid_tiers.delete(*self.tree_textgrid_tiers.get_children())
        for idx, tier in enumerate(tiers or []):
            samples = "，".join(tier.get("sample_labels", [])[:6]) or "空"
            count_text = f"{tier.get('non_empty_count', 0)}/{tier.get('count', 0)}"
            self.tree_textgrid_tiers.insert(
                "",
                tk.END,
                iid=f"tier_{idx}",
                values=(tier.get("name", ""), tier.get("type", ""), count_text, samples),
            )
        if tiers:
            self.textgrid_tier_empty_state.place_forget()
        else:
            self.textgrid_tier_empty_state.place(relx=0.5, rely=0.5, anchor="center")


    def _fill_textgrid_preview_tree(self, items):
        if not hasattr(self, "tree_textgrid_preview"):
            return
        previous_selection = set(self.tree_textgrid_preview.selection())
        self.tree_textgrid_preview.delete(*self.tree_textgrid_preview.get_children())
        for item in items or []:
            core_text = f"{item.core_start:.3f} - {item.core_end:.3f}"
            char_text = str(len(item.char_bounds))
            notes = []
            if item.match_note:
                notes.append(item.match_note)
            notes.extend(item.warnings[:2])
            status = "；".join(notes) if notes else "正常"
            tags = ("warning",) if item.warnings else ()
            self.tree_textgrid_preview.insert(
                "",
                tk.END,
                iid=item.id,
                values=(item.label, item.group, core_text, char_text, status),
                tags=tags,
            )
        if hasattr(self, "lbl_textgrid_preview_count"):
            self.lbl_textgrid_preview_count.configure(text=f"{len(items or [])} 个条目")
        if items:
            self.textgrid_preview_empty_state.place_forget()
        else:
            self.textgrid_preview_empty_state.place(relx=0.5, rely=0.5, anchor="center")
        for item_id in previous_selection:
            if self.tree_textgrid_preview.exists(item_id):
                self.tree_textgrid_preview.selection_add(item_id)


    def _textgrid_selected_item_ids(self):
        if not hasattr(self, "tree_textgrid_preview"):
            return []
        return list(self.tree_textgrid_preview.selection())


    def add_textgrid_group(self):
        self.assign_textgrid_group()


    def assign_textgrid_group(self):
        selected_ids = self._textgrid_selected_item_ids()
        if not selected_ids:
            messagebox.showwarning("提示", "请先在转换预览中选择一个或多个条目。")
            return
        group_name = self.entry_textgrid_group.get().strip() or DEFAULT_GROUP_NAME
        item_map = {item.id: item for item in self.textgrid_preview_items}
        for item_id in selected_ids:
            self.textgrid_group_overrides[item_id] = group_name
            item = item_map.get(item_id)
            if item:
                for source_id in item.source_ids:
                    self.textgrid_group_overrides[source_id] = group_name
        self.refresh_textgrid_preview()


    def clear_textgrid_group_override(self):
        selected_ids = self._textgrid_selected_item_ids()
        if not selected_ids:
            messagebox.showwarning("提示", "请先在转换预览中选择一个或多个条目。")
            return
        item_map = {item.id: item for item in self.textgrid_preview_items}
        for item_id in selected_ids:
            self.textgrid_group_overrides.pop(item_id, None)
            item = item_map.get(item_id)
            if item:
                for source_id in item.source_ids:
                    self.textgrid_group_overrides.pop(source_id, None)
        self.refresh_textgrid_preview()


    def export_converted_textgrid(self):
        if not getattr(self, "textgrid_source_path", None):
            messagebox.showwarning("提示", "请先选择一个 TextGrid 文件。")
            return
        try:
            mapping = self._selected_textgrid_mapping()
        except ValueError as e:
            messagebox.showwarning("提示", str(e))
            return

        source_dir = os.path.dirname(self.textgrid_source_path)
        source_name = os.path.splitext(os.path.basename(self.textgrid_source_path))[0]
        out_path = filedialog.asksaveasfilename(
            defaultextension=".TextGrid",
            initialdir=source_dir,
            initialfile=f"{source_name}_converted.TextGrid",
            filetypes=[
                ("Praat TextGrid", "*.TextGrid"),
                ("All Files", "*.*"),
            ],
        )
        if not out_path:
            return
        try:
            preview = convert_textgrid(
                self.textgrid_source_path,
                out_path,
                mapping,
                group_overrides=self.textgrid_group_overrides,
                wordlist_records=self.textgrid_wordlist_records,
            )
            self.textgrid_preview_items = preview.items
            self._fill_textgrid_preview_tree(preview.items)
            self.lbl_textgrid_diagnosis.configure(text=self._format_textgrid_diagnosis(preview))
            self._textgrid_status(f"已导出：{self._short_path(out_path, max_parts=3)}", "success")
            messagebox.showinfo("导出完成", f"已导出标准 TextGrid：\n{out_path}")
        except Exception as e:
            self._textgrid_status(f"导出失败：{e}", "danger")
            messagebox.showerror("导出失败", f"无法导出 TextGrid：\n{e}")


    def export_converted_textgrids_batch(self):
        paths = list(getattr(self, "textgrid_source_paths", []) or [])
        if not paths and getattr(self, "textgrid_source_path", None):
            paths = [self.textgrid_source_path]
        if not paths:
            messagebox.showwarning("提示", "请先导入一个或多个 TextGrid 文件。")
            return
        try:
            mapping = self._selected_textgrid_mapping()
        except ValueError as e:
            messagebox.showwarning("提示", str(e))
            return

        initialdir = os.path.dirname(paths[0]) if paths else os.getcwd()
        out_dir = filedialog.askdirectory(title="选择批量导出目录", initialdir=initialdir)
        if not out_dir:
            return

        batch_overrides = self._textgrid_group_overrides_for_batch(len(paths))
        exported = []
        failed = []
        current_preview = None
        current_source = os.path.normcase(os.path.abspath(getattr(self, "textgrid_source_path", "") or ""))
        for path in paths:
            try:
                source_name = os.path.splitext(os.path.basename(path))[0]
                out_path = self._unique_textgrid_output_path(out_dir, f"{source_name}_converted.TextGrid")
                preview = convert_textgrid(
                    path,
                    out_path,
                    mapping,
                    group_overrides=batch_overrides,
                    wordlist_records=self.textgrid_wordlist_records,
                )
                exported.append((out_path, preview))
                if os.path.normcase(os.path.abspath(path)) == current_source:
                    current_preview = preview
            except Exception as e:
                failed.append((path, str(e)))

        if exported:
            shown_preview = current_preview or exported[-1][1]
            self.textgrid_preview_items = shown_preview.items
            self._fill_textgrid_preview_tree(shown_preview.items)
            self.lbl_textgrid_diagnosis.configure(text=self._format_textgrid_diagnosis(shown_preview))
            self._textgrid_status(
                f"批量导出完成：成功 {len(exported)} 个，失败 {len(failed)} 个。",
                "warning" if failed else "success",
            )
        else:
            self._textgrid_status(f"批量导出失败：{len(failed)} 个文件未能转换。", "danger")

        lines = [f"成功：{len(exported)} 个", f"失败：{len(failed)} 个"]
        if len(paths) > 1 and self.textgrid_group_overrides and not batch_overrides:
            lines.append("提示：当前预览中的临时分组只适用于单文件导出，批量导出已按层映射和辅助字表处理。")
        if exported:
            lines.append(f"输出目录：{out_dir}")
        if failed:
            lines.append("")
            lines.append("失败文件：")
            for path, error in failed[:5]:
                lines.append(f"- {os.path.basename(path)}：{error}")
            if len(failed) > 5:
                lines.append(f"- 还有 {len(failed) - 5} 个失败文件未列出")

        if exported:
            messagebox.showinfo("批量导出完成", "\n".join(lines))
        else:
            messagebox.showerror("批量导出失败", "\n".join(lines))


    def _textgrid_group_overrides_for_batch(self, source_count):
        if source_count <= 1:
            return dict(self.textgrid_group_overrides)
        current_item_keys = set()
        for item in self.textgrid_preview_items:
            current_item_keys.add(item.id)
            current_item_keys.update(item.source_ids)
        return {
            key: value
            for key, value in self.textgrid_group_overrides.items()
            if key not in current_item_keys
        }


    def _unique_textgrid_output_path(self, out_dir, filename):
        base, ext = os.path.splitext(filename)
        ext = ext or ".TextGrid"
        candidate = os.path.join(out_dir, f"{base}{ext}")
        counter = 2
        while os.path.exists(candidate):
            candidate = os.path.join(out_dir, f"{base}_{counter}{ext}")
            counter += 1
        return candidate


    def _format_textgrid_diagnosis(self, preview):
        report = preview.tone_pair_report or {}
        lines = [f"转换预览：{len(preview.items)} 个条目。"]
        wordlist_matched = sum(1 for item in preview.items if getattr(item, "match_note", ""))
        if self.textgrid_wordlist_records:
            lines.append(f"辅助字表：{len(self.textgrid_wordlist_records)} 项；当前命中 {wordlist_matched} 个条目/二字组。")
        lines.append(
            f"二字组：可识别 {report.get('eligible_count', 0)} 个；"
            f"前调类 {report.get('front_count', 0)} 个，后调类 {report.get('back_count', 0)} 个，组合 {report.get('combo_count', 0)} 个。"
        )
        for message in report.get("messages", [])[:3]:
            lines.append(message)
        unique_warnings = []
        for warning in preview.warnings:
            if warning not in unique_warnings:
                unique_warnings.append(warning)
            if len(unique_warnings) >= 3:
                break
        if unique_warnings:
            lines.append("转换提示：" + "；".join(unique_warnings))
        return "\n".join(lines)


    def _textgrid_status(self, text, tone="muted"):
        if not hasattr(self, "lbl_textgrid_status"):
            return
        colors = {
            "muted": self.colors["muted"],
            "success": self.colors["success"],
            "warning": self.colors["warning_hover"],
            "danger": self.colors["danger"],
        }
        self.lbl_textgrid_status.configure(text=text, text_color=colors.get(tone, self.colors["muted"]))


    def build_wordlist_tab(self):
        content = ctk.CTkFrame(self.tab_wordlist, fg_color="transparent")
        content.pack(fill=tk.BOTH, expand=True, padx=20, pady=18)
        content.grid_columnconfigure(0, weight=1)
        content.grid_rowconfigure(0, weight=1)

        editor_card = self._make_card(content)
        editor_card.grid(row=0, column=0, sticky="nsew")
        editor_card.grid_rowconfigure(0, weight=1)
        editor_card.grid_columnconfigure(0, weight=1)
        self.wordlist_editor = VisualWordlistEditor(
            editor_card,
            colors=self.colors,
            font_family=self.font_family,
            bottom_actions=[
                ("导入文本并转换为高级字表", self.import_csv_into_wordlist_editor, "secondary"),
                ("复制Agent提示词", self.copy_wordlist_agent_prompt, "warning"),
            ],
            extra_actions=[
                ("同步到音频拆分", self.sync_wordlist_from_editor, "purple"),
            ],
        )
        self.wordlist_editor.grid(row=0, column=0, sticky="nsew", padx=14, pady=14)


    def copy_wordlist_agent_prompt(self):
        prompt = ADVANCED_WORDLIST_AGENT_PROMPT
        try:
            self.clipboard_clear()
            self.clipboard_append(prompt)
            messagebox.showinfo("成功", "高级字表 Agent 提示词已复制。")
        except Exception as e:
            messagebox.showerror("错误", f"复制失败：{e}")


    def open_wordlist_editor_ptwl(self):
        path = self.wordlist_editor.load_ptwl_dialog()
        if path:
            self.tabview.set(self.tab_wordlist_name)


    def import_v1_into_wordlist_editor(self):
        path = filedialog.askopenfilename(filetypes=[("Text Files", "*.txt"), ("All Files", "*.*")])
        if not path:
            return
        try:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    text = f.read()
            except UnicodeDecodeError:
                with open(path, "r", encoding="gbk") as f:
                    text = f.read()
            self.wordlist_editor.import_v1_text(text)
            self.tabview.set(self.tab_wordlist_name)
        except Exception as e:
            messagebox.showerror("错误", f"导入普通字表失败：{e}")


    def import_csv_into_wordlist_editor(self):
        try:
            self.wordlist_editor.import_csv_dialog()
            self.tabview.set(self.tab_wordlist_name)
        except Exception as e:
            messagebox.showerror("错误", f"导入文本失败：{e}")


    def export_wordlist_editor_v1(self):
        self.wordlist_editor.export_v1_dialog()


    def sync_wordlist_from_editor(self):
        if not hasattr(self, "wordlist_editor"):
            return messagebox.showwarning("提示", "字表编辑器尚未初始化。")
        doc = self.wordlist_editor.get_document()
        _groups, flat_words, _records = flatten_wordlist_document(doc)
        if not flat_words:
            return messagebox.showwarning("提示", "字表编辑器中没有可同步的词项。")
        text = document_to_v1_text(doc)
        self.txt_wordlist.delete("1.0", tk.END)
        self.txt_wordlist.insert("1.0", text)
        self._set_split_wordlist_document(doc, text)
        self.validate_wordlist()
        self.tabview.set(self.tab_split_name)
        messagebox.showinfo("完成", f"已同步 {len(flat_words)} 个词项到拆分页。")

