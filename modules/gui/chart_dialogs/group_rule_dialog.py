"""
Chart group rule dialog and group filtering mixin.
Handles grouping rules, multi-tag categorization dialogs, and group selection lists.
"""
from __future__ import annotations

from collections import Counter
import numpy as np

try:
    import tkinter as tk
    import customtkinter as ctk
except ImportError:
    tk = None
    ctk = None


class ChartGroupRuleDialogMixin:
    """Mixin managing chart group rules and group selection list in export dialog."""

    def _update_chart_group_rule_label(self):
        if hasattr(self, "lbl_chart_group_rule"):
            text = self._format_chart_group_rule_summary(self.chart_group_rule)
            groupby_text = self.combo_groupby.get() if hasattr(self, "combo_groupby") else ""
            rule_active = self.chart_group_rule.get("source") != "default"
            if self._chart_grouping_preserves_original_group() and rule_active:
                text += "（当前图表仅作为筛选条件）"
            elif groupby_text in {"按词语", "按单字音节", "按单字/音节", "按发音人"} and rule_active:
                text += "（当前绘图维度会保留专用分类）"
            self.lbl_chart_group_rule.configure(text=text)


    def _apply_chart_group_rule_from_ui(self, rule):
        self.chart_group_rule = self._normalize_chart_group_rule(rule)
        self.current_preview_page = 0
        self.current_group_page = 0
        self._update_chart_group_rule_label()
        self._update_group_filters()
        self._on_group_filter_changed()


    def _open_chart_group_rule_dialog(self):
        options = self._collect_chart_grouping_options()
        current_rule = self._normalize_chart_group_rule(self.chart_group_rule)

        source_to_label = {
            "default": "默认组",
            "item_tags": "词项标签",
            "group_tags": "组标签",
            "item_meta": "自定义字段",
            "metadata_source": "复核状态",
            "label": "词项",
            "speaker_name": "发音人",
        }
        label_to_source = {label: source for source, label in source_to_label.items()}
        mode_to_label = {
            "each": "每个选中标签一组",
            "contains": "包含/不包含指定标签",
            "filter_default": "筛选标签后仍按默认组",
        }
        label_to_mode = {label: mode for mode, label in mode_to_label.items()}

        dialog = ctk.CTkToplevel(self)
        dialog.title("PhonTracer语音学标注与声学分析系统 V1.0 - 图表分组规则")
        dialog.geometry("680x620")
        dialog.resizable(False, False)
        dialog.transient(self)
        dialog.grab_set()

        outer = ctk.CTkFrame(dialog, fg_color=("#F8FAFC", "#111827"))
        outer.pack(fill=tk.BOTH, expand=True)

        card = ctk.CTkFrame(
            outer,
            fg_color=("#FFFFFF", "#1E293B"),
            border_width=1,
            border_color=("#E5E7EB", "#475569"),
            corner_radius=18,
        )
        card.pack(fill=tk.BOTH, expand=True, padx=18, pady=18)
        card.grid_columnconfigure(0, weight=1)
        card.grid_columnconfigure(1, weight=1)
        card.grid_rowconfigure(5, weight=1)

        ctk.CTkLabel(card, text="图表分组规则", font=ctk.CTkFont(family="Microsoft YaHei", size=18, weight="bold")).grid(
            row=0, column=0, columnspan=2, sticky="w", padx=20, pady=(18, 4)
        )
        ctk.CTkLabel(
            card,
            text="选择图表如何生成组别。默认不会改动字表组；标签分组更适合探索，自定义字段更适合正式比较。",
            font=self.font_small,
            text_color=("#6B7280", "#CBD5E1"),
            wraplength=600,
            justify="left",
        ).grid(row=1, column=0, columnspan=2, sticky="w", padx=20, pady=(0, 14))

        source_var = ctk.StringVar(value=source_to_label.get(current_rule["source"], "默认组"))
        mode_var = ctk.StringVar(value=mode_to_label.get(current_rule["tag_mode"], mode_to_label["each"]))
        meta_fields = sorted(options.get("item_meta", {}).keys())
        initial_field = current_rule.get("field_name") if current_rule.get("field_name") in meta_fields else (meta_fields[0] if meta_fields else "")
        field_var = ctk.StringVar(value=initial_field or "无可用字段")

        ctk.CTkLabel(card, text="分组来源", font=self.font_small).grid(row=2, column=0, sticky="w", padx=(20, 8), pady=(0, 3))
        ctk.CTkLabel(card, text="标签模式 / 字段", font=self.font_small).grid(row=2, column=1, sticky="w", padx=(8, 20), pady=(0, 3))

        source_menu = ctk.CTkOptionMenu(
            card,
            values=list(source_to_label.values()),
            variable=source_var,
            **self.dropdown_kwargs,
        )
        source_menu.grid(row=3, column=0, sticky="ew", padx=(20, 8), pady=(0, 10))
        self._apply_custom_arrow(source_menu)

        mode_menu = ctk.CTkOptionMenu(card, values=list(mode_to_label.values()), variable=mode_var, **self.dropdown_kwargs)
        mode_menu.grid(row=3, column=1, sticky="ew", padx=(8, 20), pady=(0, 10))
        self._apply_custom_arrow(mode_menu)

        field_menu = ctk.CTkOptionMenu(card, values=meta_fields or ["无可用字段"], variable=field_var, **self.dropdown_kwargs)
        field_menu.grid(row=4, column=1, sticky="ew", padx=(8, 20), pady=(0, 10))
        self._apply_custom_arrow(field_menu)

        values_panel = ctk.CTkFrame(card, fg_color=("#F8FAFC", "#263244"), corner_radius=14)
        values_panel.grid(row=5, column=0, sticky="nsew", padx=(20, 8), pady=(0, 14))
        values_panel.grid_rowconfigure(1, weight=1)
        values_panel.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(values_panel, text="参与标签", font=self.font_title).grid(row=0, column=0, sticky="w", padx=14, pady=(12, 4))
        values_scroll = ctk.CTkScrollableFrame(values_panel, fg_color="transparent", height=230)
        values_scroll.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 8))

        preview_panel = ctk.CTkFrame(card, fg_color=("#F8FAFC", "#263244"), corner_radius=14)
        preview_panel.grid(row=5, column=1, sticky="nsew", padx=(8, 20), pady=(0, 14))
        preview_panel.grid_rowconfigure(2, weight=1)
        preview_panel.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(preview_panel, text="分组预览", font=self.font_title).grid(row=0, column=0, sticky="w", padx=14, pady=(12, 4))
        preview_hint = ctk.CTkLabel(preview_panel, text="", font=self.font_small, text_color=("#6B7280", "#CBD5E1"), anchor="w")
        preview_hint.grid(row=1, column=0, sticky="ew", padx=14, pady=(0, 4))
        preview_scroll = ctk.CTkScrollableFrame(preview_panel, fg_color="transparent", height=230)
        preview_scroll.grid(row=2, column=0, sticky="nsew", padx=8, pady=(0, 8))

        selected_vars = {}

        def current_source():
            return label_to_source.get(source_var.get(), "default")

        def current_mode():
            return label_to_mode.get(mode_var.get(), "each")

        def build_rule():
            selected = [name for name, var in selected_vars.items() if var.get()]
            if current_mode() == "contains" and selected:
                selected = selected[:1]
            return self._normalize_chart_group_rule({
                "source": current_source(),
                "tag_mode": current_mode(),
                "selected_values": selected,
                "field_name": field_var.get() if field_var.get() != "无可用字段" else "",
            })

        def candidate_tags():
            source = current_source()
            if source == "item_tags":
                return options.get("item_tags", Counter())
            if source == "group_tags":
                return options.get("group_tags", Counter())
            return Counter()

        def refresh_preview():
            for child in preview_scroll.winfo_children():
                child.destroy()
            rule = build_rule()
            counts = self._chart_group_counts_for_rule(rule)
            if not counts:
                ctk.CTkLabel(
                    preview_scroll,
                    text="当前规则没有匹配到可绘制条目。",
                    font=self.font_small,
                    text_color=("#6B7280", "#CBD5E1"),
                ).pack(anchor="w", padx=8, pady=8)
                preview_hint.configure(text="0 组")
                return
            ordered = sorted(counts.items(), key=lambda item: (-item[1], str(item[0])))
            hint = f"{len(ordered)} 组 | {sum(counts.values())} 项"
            if len(ordered) > 8:
                hint += " | 论文主图建议筛选到 8 组以内"
            preview_hint.configure(text=hint)
            for idx, (name, count) in enumerate(ordered[:24]):
                row = ctk.CTkFrame(
                    preview_scroll,
                    fg_color=("#FFFFFF", "#1E293B") if idx % 2 == 0 else ("#EEF2FF", "#334155"),
                    corner_radius=10,
                )
                row.pack(fill=tk.X, padx=4, pady=3)
                ctk.CTkLabel(row, text=str(name), font=self.font_small, anchor="w").pack(side=tk.LEFT, fill=tk.X, expand=True, padx=10, pady=6)
                ctk.CTkLabel(row, text=f"{count} 项", font=self.font_small, text_color=("#2563EB", "#93C5FD")).pack(side=tk.RIGHT, padx=10, pady=6)

        def refresh_values():
            for child in values_scroll.winfo_children():
                child.destroy()
            selected_vars.clear()
            source = current_source()
            if source not in {"item_tags", "group_tags"}:
                ctk.CTkLabel(
                    values_scroll,
                    text="该来源会按实际取值自动分组，不需要选择标签。",
                    font=self.font_small,
                    text_color=("#6B7280", "#CBD5E1"),
                    wraplength=250,
                    justify="left",
                ).pack(anchor="w", padx=10, pady=8)
                refresh_preview()
                return

            tags = candidate_tags()
            if not tags:
                ctk.CTkLabel(
                    values_scroll,
                    text="当前工程没有可用标签。",
                    font=self.font_small,
                    text_color=("#6B7280", "#CBD5E1"),
                ).pack(anchor="w", padx=10, pady=8)
                refresh_preview()
                return

            selected_now = set(current_rule.get("selected_values", [])) if source == current_rule.get("source") else set()
            for tag, count in sorted(tags.items(), key=lambda item: (-item[1], str(item[0]))):
                var = ctk.BooleanVar(value=(not selected_now or tag in selected_now))
                selected_vars[tag] = var
                row = self._make_checkbox(
                    values_scroll,
                    text=f"{tag} ({count}项)",
                    variable=var,
                    command=refresh_preview,
                )
                row.pack(anchor="w", padx=10, pady=4)
            refresh_preview()

        def refresh_mode_state(*_):
            source = current_source()
            if source in {"item_tags", "group_tags"}:
                mode_menu.configure(values=list(mode_to_label.values()), state="normal")
                if mode_var.get() not in mode_to_label.values():
                    mode_var.set(mode_to_label["each"])
            else:
                mode_menu.configure(values=["按字段值分组"], state="disabled")
                mode_var.set("按字段值分组")
            if source == "item_meta":
                field_menu.configure(state="normal")
                if field_var.get() == "无可用字段" and meta_fields:
                    field_var.set(meta_fields[0])
            else:
                field_menu.configure(state="disabled")
            refresh_values()

        source_menu.configure(command=lambda _=None: refresh_mode_state())
        mode_menu.configure(command=lambda _=None: refresh_preview())
        field_menu.configure(command=lambda _=None: refresh_preview())
        refresh_mode_state()

        button_row = ctk.CTkFrame(card, fg_color="transparent")
        button_row.grid(row=6, column=0, columnspan=2, sticky="ew", padx=20, pady=(0, 18))
        button_row.grid_columnconfigure(0, weight=1)
        ctk.CTkButton(
            button_row,
            text="恢复默认",
            width=110,
            height=32,
            corner_radius=16,
            font=self.font_small,
            fg_color=("#E5E7EB", "#374151"),
            text_color=("#374151", "#E5E7EB"),
            hover_color=("#D1D5DB", "#4B5563"),
            command=lambda: (self._apply_chart_group_rule_from_ui(self._default_chart_group_rule()), dialog.destroy()),
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkButton(
            button_row,
            text="取消",
            width=90,
            height=32,
            corner_radius=16,
            font=self.font_small,
            fg_color=("#E5E7EB", "#374151"),
            text_color=("#374151", "#E5E7EB"),
            hover_color=("#D1D5DB", "#4B5563"),
            command=dialog.destroy,
        ).grid(row=0, column=1, padx=(8, 8))
        ctk.CTkButton(
            button_row,
            text="应用规则",
            width=110,
            height=32,
            corner_radius=16,
            font=self.font_main,
            fg_color=(self.ui_primary, self.ui_primary_hover),
            hover_color=self.ui_primary_hover,
            command=lambda: (self._apply_chart_group_rule_from_ui(build_rule()), dialog.destroy()),
        ).grid(row=0, column=2)


    def _select_tree_selected_groups(self):
        if not self.project_tree or not hasattr(self.project_tree, 'tree'):
            return
        selected_iids = self.project_tree.tree.selection()
        if not selected_iids:
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
            return
        for g, var in self.group_checkbox_vars.items():
            var.set(g in selected_groups)
        self._populate_groups_list()
        self._on_group_filter_changed()


    def _toggle_groups_sorting(self):
        self.sort_by_count = not getattr(self, 'sort_by_count', False)
        if self.sort_by_count:
            self.btn_toggle_sort.configure(fg_color=(self.ui_primary, self.ui_primary_hover), text_color="white")
        else:
            self.btn_toggle_sort.configure(fg_color=("#E5E7EB", "#374151"), text_color=("#374151", "#E5E7EB"))
        self._populate_groups_list()


    def _visible_group_names(self):
        search_query = self.search_group_var.get().strip().lower() if hasattr(self, "search_group_var") else ""
        min_count = 0
        try:
            val_str = self.entry_min_count.get().strip()
            if val_str:
                min_count = int(val_str)
        except (ValueError, AttributeError):
            pass

        if getattr(self, 'sort_by_count', False):
            groups_to_draw = sorted(self.available_groups, key=lambda g: self.group_counts.get(g, 0), reverse=True)
        else:
            groups_to_draw = list(self.available_groups)

        visible_groups = []
        for group_name in groups_to_draw:
            count = self.group_counts.get(group_name, 0)
            if count < min_count:
                continue
            if search_query and search_query not in group_name.lower():
                continue
            visible_groups.append(group_name)
        return visible_groups


    def _populate_groups_list(self):
        for w in self.filter_scroll.winfo_children():
            w.destroy()

        self.visible_group_names = self._visible_group_names()
        row_frame = None
        row_col = 0
        row_index = -1

        def new_row():
            nonlocal row_frame, row_col, row_index
            row_index += 1
            row_col = 0
            row_frame = ctk.CTkFrame(
                self.filter_scroll,
                fg_color=self.ui_row_even if row_index % 2 == 0 else self.ui_row_odd,
                corner_radius=8
            )
            row_frame.pack(fill=tk.X, padx=4, pady=1)
            row_frame.grid_columnconfigure(0, weight=1, uniform="group_filter")
            row_frame.grid_columnconfigure(1, weight=1, uniform="group_filter")
            return row_frame

        for g in self.visible_group_names:
            count = self.group_counts.get(g, 0)
            is_short_name = len(str(g)) <= 7
            if row_frame is None or row_col >= 2 or not is_short_name:
                new_row()

            var = self.group_checkbox_vars[g]
            cb = self._make_checkbox(
                row_frame,
                text=f"{g} ({count}项)",
                variable=var,
                command=self._on_group_filter_changed,
            )
            if is_short_name:
                cb.grid(row=0, column=row_col, sticky="w", padx=8, pady=3)
                row_col += 1
            else:
                cb.grid(row=0, column=0, columnspan=2, sticky="w", padx=8, pady=3)
                row_col = 2

        self._bind_filter_scroll_wheel_recursive(self.filter_scroll)


    def _on_group_filter_changed(self):
        self.current_preview_page = 0
        self.current_group_page = 0
        self.trigger_preview_update()


    def _select_all_groups(self):
        visible_groups = getattr(self, "visible_group_names", None) or self._visible_group_names()
        for group_name in visible_groups:
            var = self.group_checkbox_vars.get(group_name)
            if var is not None:
                var.set(True)
        self._on_group_filter_changed()


    def _reverse_groups(self):
        visible_groups = getattr(self, "visible_group_names", None) or self._visible_group_names()
        for group_name in visible_groups:
            var = self.group_checkbox_vars.get(group_name)
            if var is not None:
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


    # Convenience and compatibility aliases
    select_all_groups = _select_all_groups
    invert_group_selection = _reverse_groups
    toggle_group_sort = _toggle_groups_sorting
    select_top_groups = _select_high_frequency_groups

    def _deselect_all_groups(self):
        visible_groups = getattr(self, "visible_group_names", None) or self._visible_group_names()
        for group_name in visible_groups:
            var = self.group_checkbox_vars.get(group_name)
            if var is not None:
                var.set(False)
        self._on_group_filter_changed()

    deselect_all_groups = _deselect_all_groups
