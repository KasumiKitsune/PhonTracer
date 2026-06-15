import os
import copy
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Any, Dict, Optional

import customtkinter as ctk

from .wordlist_v2 import (
    AI_REVIEW_STATUS,
    CORE_META_FIELDS,
    DEFAULT_REVIEW_STATUS,
    REVIEWED_STATUS,
    build_document_from_csv_text,
    build_document_from_v1_text,
    document_to_csv_text,
    document_to_v1_text,
    flatten_wordlist_document,
    load_wordlist_document,
    mark_ai_fields_reviewed,
    normalize_wordlist_document,
    save_wordlist_document,
    summarize_wordlist_document,
    validate_wordlist_document,
)


def _split_tokens(text: str):
    import re
    return [t.strip() for t in re.split(r"[;；,，、\n\t]+", text or "") if t.strip()]


def _join_tokens(values):
    return "；".join([str(v).strip() for v in values or [] if str(v).strip()])


class RoundedGroupList(ctk.CTkScrollableFrame):
    """圆角按钮式组列表，避免原生 Listbox 的割裂感。"""

    def __init__(self, master, colors, font_family="Microsoft YaHei", **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self.colors = colors
        self.font_family = font_family
        self.buttons = []
        self._selected_index = None
        self._select_callback = None
        self._context_callback = None
        self._bind_fast_scroll()

    def _bind_fast_scroll(self):
        def on_wheel(event):
            step = -1 if getattr(event, "delta", 0) > 0 else 1
            try:
                self._parent_canvas.yview_scroll(step * 12, "units")
                return "break"
            except Exception:
                return None
        self.bind("<MouseWheel>", on_wheel, add="+")
        if hasattr(self, "_parent_canvas"):
            self._parent_canvas.bind("<MouseWheel>", on_wheel, add="+")

    def bind(self, event_name, callback, add=None):
        if event_name == "<<ListboxSelect>>":
            self._select_callback = callback
            return None
        if event_name in ("<Button-3>", "<Button-2>"):
            self._context_callback = callback
            for idx, btn in enumerate(self.buttons):
                btn.bind(event_name, lambda event, i=idx: self._post_context(event, i), add=add)
            return super().bind(event_name, lambda event: self._post_context(event, None), add)
        return super().bind(event_name, callback, add)

    def delete(self, start, end=None):
        for btn in self.buttons:
            btn.destroy()
        self.buttons = []
        self._selected_index = None

    def insert(self, index, text):
        idx = len(self.buttons)
        btn = ctk.CTkButton(
            self,
            text=str(text),
            anchor="w",
            height=36,
            corner_radius=10,
            fg_color="transparent",
            text_color=self.colors.get("text", "#17202A"),
            hover_color=("#F1F5F9", "#262930"),
            font=ctk.CTkFont(family=self.font_family, size=13),
            command=lambda i=idx: self.selection_set(i, notify=True),
        )
        btn.grid_columnconfigure(0, weight=0, minsize=10)
        btn.grid_columnconfigure(1, weight=0, minsize=0)
        btn.grid_columnconfigure(2, weight=0, minsize=0)
        btn.grid_columnconfigure(3, weight=0)
        btn.grid_columnconfigure(4, weight=1, minsize=10)
        if getattr(btn, "_text_label", None) is not None:
            btn._text_label.configure(anchor="w", justify="left")
        btn.bind("<Button-3>", lambda event, i=idx: self._post_context(event, i), add="+")
        btn.bind("<Button-2>", lambda event, i=idx: self._post_context(event, i), add="+")
        btn.pack(fill=tk.X, padx=2, pady=2)
        self.buttons.append(btn)

    def _post_context(self, event, index):
        if index is not None:
            self.selection_set(index, notify=True)
        if self._context_callback:
            self._context_callback(event)
        return "break"

    def selection_clear(self, start, end=None):
        if self._selected_index is not None and 0 <= self._selected_index < len(self.buttons):
            self.buttons[self._selected_index].configure(
                fg_color="transparent",
                text_color=self.colors.get("text", "#17202A"),
                hover_color=("#F1F5F9", "#262930"),
                font=ctk.CTkFont(family=self.font_family, size=13),
            )
        self._selected_index = None

    def selection_set(self, index, notify=True):
        try:
            idx = int(index)
        except Exception:
            return
        if idx < 0 or idx >= len(self.buttons):
            return
        self.selection_clear(0, tk.END)
        self._selected_index = idx
        self.buttons[idx].configure(
            fg_color=self.colors.get("primary", "#2563EB"),
            text_color="#FFFFFF",
            hover_color=self.colors.get("primary_hover", "#1D4ED8"),
            font=ctk.CTkFont(family=self.font_family, size=13, weight="bold"),
        )
        if notify and self._select_callback:
            class Event:
                def __init__(self, widget):
                    self.widget = widget
            self._select_callback(Event(self))

    def activate(self, index):
        return None

    def curselection(self):
        return () if self._selected_index is None else (self._selected_index,)


def _apply_custom_arrow(dropdown):
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


class VisualWordlistEditor(ctk.CTkFrame):
    """高级字表的可视化编辑组件。"""

    def __init__(self, master, colors: Optional[Dict[str, str]] = None, font_family: str = "Microsoft YaHei", on_change=None, extra_actions=None, bottom_actions=None, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self.colors = colors or {
            "surface": "#FFFFFF",
            "surface_soft": "#F8FAFC",
            "border": "#E2E8F0",
            "text": "#17202A",
            "text_soft": "#334155",
            "muted": "#64748B",
            "primary": "#2563EB",
            "primary_hover": "#1D4ED8",
            "success": "#10B981",
            "warning": "#F59E0B",
            "danger": "#EF4444",
        }
        self.font_family = font_family
        self.font_main = ctk.CTkFont(family=font_family, size=12)
        self.font_small = ctk.CTkFont(family=font_family, size=11)
        self.font_title = ctk.CTkFont(family=font_family, size=14, weight="bold")
        self.on_change = on_change
        self.extra_actions = extra_actions or []

        self.document = normalize_wordlist_document({})
        self.current_group_index = 0
        self.current_item_index = None
        self.current_path = None
        self._refreshing = False
        self._item_tree_hover = None
        self.bottom_actions = bottom_actions or []

        self.title_var = tk.StringVar()
        self.group_name_var = tk.StringVar()
        self.group_note_var = tk.StringVar()
        self.group_tags_var = tk.StringVar()
        self.item_label_var = tk.StringVar()
        self.item_note_var = tk.StringVar()
        self.item_tags_var = tk.StringVar()
        self.item_aliases_var = tk.StringVar()
        self.item_source_var = tk.StringVar(value=DEFAULT_REVIEW_STATUS)

        self._build_ui()
        self.set_document(self.document)

    def _button(self, parent, text, command, tone="primary", width=None):
        palette = {
            "primary": (self.colors["primary"], self.colors.get("primary_hover", "#1D4ED8"), "#FFFFFF"),
            "success": (self.colors.get("success", "#10B981"), "#059669", "#FFFFFF"),
            "warning": (self.colors.get("warning", "#F59E0B"), "#D97706", "#FFFFFF"),
            "danger": (self.colors.get("danger", "#EF4444"), "#DC2626", "#FFFFFF"),
            "purple": ("#6366F1", "#4F46E5", "#FFFFFF"),
            "secondary": ("#E2E8F0", "#CBD5E1", self.colors["text_soft"]),
        }
        fg, hover, tc = palette.get(tone, palette["primary"])
        options = {
            "text": text,
            "command": command,
            "height": 30,
            "corner_radius": 15,
            "fg_color": fg,
            "hover_color": hover,
            "text_color": tc,
            "font": self.font_small,
        }
        if width is not None:
            options["width"] = width
        return ctk.CTkButton(parent, **options)

    def _entry(self, parent, variable, placeholder="", height=30):
        return ctk.CTkEntry(
            parent,
            textvariable=variable,
            placeholder_text=placeholder,
            height=max(height, 34),
            corner_radius=999,
            border_color=self.colors["border"],
            fg_color=self.colors.get("surface_soft", "#F8FAFC"),
            text_color=self.colors.get("text", "#17202A"),
            font=self.font_main,
        )

    def _bind_fast_tree_scroll(self, widget):
        def on_wheel(event):
            step = -1 if getattr(event, "delta", 0) > 0 else 1
            widget.yview_scroll(step * 5, "units")
            return "break"
        widget.bind("<MouseWheel>", on_wheel, add="+")

    def _bind_fast_scroll_frame(self, frame):
        def on_wheel(event):
            step = -1 if getattr(event, "delta", 0) > 0 else 1
            try:
                frame._parent_canvas.yview_scroll(step * 56, "units")
                return "break"
            except Exception:
                return None
        def on_global_wheel(event):
            try:
                x1 = frame.winfo_rootx()
                y1 = frame.winfo_rooty()
                x2 = x1 + frame.winfo_width()
                y2 = y1 + frame.winfo_height()
                if x1 <= event.x_root <= x2 and y1 <= event.y_root <= y2:
                    return on_wheel(event)
            except Exception:
                return None
            return None
        frame._wordlist_fast_wheel = on_wheel
        frame.bind("<MouseWheel>", on_wheel, add="+")
        frame.bind_all("<MouseWheel>", on_global_wheel, add="+")
        if hasattr(frame, "_parent_canvas"):
            frame._parent_canvas.bind("<MouseWheel>", on_wheel, add="+")

    def _bind_child_to_scroll_frame(self, widget, frame):
        on_wheel = getattr(frame, "_wordlist_fast_wheel", None)
        if not on_wheel:
            return
        try:
            widget.bind("<MouseWheel>", on_wheel, add="+")
        except Exception:
            pass
        for child_name in ("_entry", "_textbox", "_canvas"):
            child = getattr(widget, child_name, None)
            if child is not None:
                try:
                    child.bind("<MouseWheel>", on_wheel, add="+")
                except Exception:
                    pass

    def _bind_scroll_descendants(self, parent, frame):
        for child in parent.winfo_children():
            self._bind_child_to_scroll_frame(child, frame)
            self._bind_scroll_descendants(child, frame)

    def _make_context_menu(self, parent):
        from .ui_widgets import make_context_menu
        return make_context_menu(parent, font_size=10)

    def _post_menu(self, menu, event):
        from .ui_widgets import post_context_menu
        post_context_menu(menu, event)

    def show_more_actions(self):
        popup = ctk.CTkToplevel(self.winfo_toplevel())
        popup.title("字表操作")
        popup.geometry("420x390")
        popup.resizable(False, False)
        popup.configure(fg_color=self.colors.get("surface", "#FFFFFF"))
        popup.transient(self.winfo_toplevel())
        popup.grab_set()
        popup.update_idletasks()
        x = self.winfo_toplevel().winfo_rootx() + 80
        y = self.winfo_toplevel().winfo_rooty() + 80
        popup.geometry(f"+{x}+{y}")

        ctk.CTkLabel(popup, text="更多操作", font=ctk.CTkFont(family=self.font_family, size=16, weight="bold"), text_color=self.colors["text"]).pack(anchor="w", padx=22, pady=(18, 4))
        ctk.CTkLabel(popup, text="低频的导入、导出和删除操作集中放在这里。", font=self.font_small, text_color=self.colors["muted"]).pack(anchor="w", padx=22, pady=(0, 14))

        grid = ctk.CTkFrame(popup, fg_color="transparent")
        grid.pack(fill=tk.BOTH, expand=True, padx=22, pady=(0, 14))
        grid.grid_columnconfigure((0, 1), weight=1)

        actions = [
            ("新建字表", lambda: self.set_document({}), "secondary"),
            ("另存为", lambda: self.save_ptwl_dialog(save_as=True), "success"),
            ("检查字表", self.check_document, "warning"),
            ("导入 v1 文本", self.import_v1_dialog, "secondary"),
            ("导入 CSV", self.import_csv_dialog, "secondary"),
            ("导出 v1 文本", self.export_v1_dialog, "secondary"),
            ("导出 CSV", self.export_csv_dialog, "secondary"),
            ("删除当前组", self.delete_group, "danger"),
            ("删除当前词项", self.delete_item, "danger"),
        ]
        actions.extend(self.extra_actions)

        for idx, (text, command, tone) in enumerate(actions):
            def run(cmd=command):
                popup.destroy()
                cmd()
            self._button(grid, text, run, tone=tone).grid(row=idx // 2, column=idx % 2, sticky="ew", padx=5, pady=5)

        self._button(popup, "关闭", popup.destroy, "secondary").pack(fill=tk.X, padx=22, pady=(0, 18))

    def _build_ui(self):
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(1, weight=1)

        header = ctk.CTkFrame(self, fg_color=self.colors["surface"], corner_radius=12, border_width=1, border_color=self.colors["border"])
        header.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 10))
        header.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(header, text="字表名称", font=self.font_small, text_color=self.colors["muted"]).grid(row=0, column=0, padx=(12, 8), pady=10, sticky="w")
        title_entry = self._entry(header, self.title_var, "例如：普通话声调实验字表")
        title_entry.grid(row=0, column=1, sticky="ew", pady=10)
        title_entry.bind("<FocusOut>", lambda _e: self._sync_from_fields())
        self.lbl_summary = ctk.CTkLabel(header, text="", font=self.font_small, text_color=self.colors["muted"])
        self.lbl_summary.grid(row=0, column=2, padx=(12, 8), pady=10, sticky="e")
        self._button(header, "添加组", self.add_group, "secondary", width=78).grid(row=0, column=3, padx=(0, 6), pady=10)
        self._button(header, "添加词项", self.add_item, "secondary", width=88).grid(row=0, column=4, padx=(0, 12), pady=10)

        left = ctk.CTkFrame(self, fg_color=self.colors["surface"], corner_radius=8, border_width=1, border_color=self.colors["border"], width=170)
        left.grid(row=1, column=0, sticky="ns", padx=(0, 10))
        left.grid_propagate(False)
        ctk.CTkLabel(left, text="组", font=self.font_title, text_color=self.colors["text"]).pack(anchor="w", padx=12, pady=(12, 6))
        self.group_list = RoundedGroupList(left, colors=self.colors, font_family=self.font_family)
        self.group_list.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 12))
        self.group_list.bind("<<ListboxSelect>>", self._on_group_select)
        self.group_list.bind("<Button-3>", self._show_group_context_menu)
        self.group_list.bind("<Button-2>", self._show_group_context_menu)

        center = ctk.CTkFrame(self, fg_color=self.colors["surface"], corner_radius=8, border_width=1, border_color=self.colors["border"])
        center.grid(row=1, column=1, sticky="nsew", padx=(0, 10))
        center.grid_columnconfigure(0, weight=1)
        center.grid_rowconfigure(1, weight=1)
        table_header = ctk.CTkFrame(center, fg_color="transparent")
        table_header.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 6))
        table_header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(table_header, text="词项表", font=self.font_title, text_color=self.colors["text"]).grid(row=0, column=0, sticky="w")

        table_wrap = ctk.CTkFrame(center, fg_color="transparent")
        table_wrap.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))
        table_wrap.grid_columnconfigure(0, weight=1)
        table_wrap.grid_rowconfigure(0, weight=1)
        self.item_columns = ("label", "note", "tags", "aliases", "source")
        self.item_column_headings = {
            "label": "词项",
            "note": "备注",
            "tags": "标签",
            "aliases": "别名",
            "source": "补全状态",
        }
        self.item_column_widths = {"label": 115, "note": 180, "tags": 140, "aliases": 110, "source": 118}
        style = ttk.Style()
        style.configure("Wordlist.Treeview", background="#FFFFFF", fieldbackground="#FFFFFF", foreground="#17202A", rowheight=36, borderwidth=0, font=(self.font_family, 11, "bold"))
        style.configure("Wordlist.Treeview.Heading", font=(self.font_family, 11, "bold"), background="#F8FAFC", foreground="#334155", borderwidth=0)
        style.map("Wordlist.Treeview", background=[("selected", "#2563EB")], foreground=[("selected", "#FFFFFF")])
        self.item_tree = ttk.Treeview(table_wrap, columns=self.item_columns, show="headings", height=10, style="Wordlist.Treeview")
        for col in self.item_columns:
            self.item_tree.heading(col, text=self.item_column_headings[col])
            self.item_tree.column(col, width=self.item_column_widths[col], minwidth=55, stretch=col in ("note", "tags", "source"))
        self.item_tree.tag_configure("item_normal", background="#FFFFFF", foreground="#17202A")
        self.item_tree.tag_configure("item_hover", background="#EFF6FF", foreground="#17202A")
        self.item_tree.tag_configure("item_active", background="#DBEAFE", foreground="#1E3A8A")
        yscroll = ctk.CTkScrollbar(table_wrap, orientation="vertical", command=self.item_tree.yview, width=12)
        xscroll = ctk.CTkScrollbar(table_wrap, orientation="horizontal", command=self.item_tree.xview, height=12)
        self.item_tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        self.item_tree.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        self.item_tree.bind("<<TreeviewSelect>>", self._on_item_select)
        self.item_tree.bind("<Double-1>", self._start_cell_edit)
        self.item_tree.bind("<Button-3>", self._show_item_context_menu)
        self.item_tree.bind("<Button-2>", self._show_item_context_menu)
        self.item_tree.bind("<Motion>", self._on_item_tree_motion)
        self.item_tree.bind("<Leave>", self._on_item_tree_leave)
        self._bind_fast_tree_scroll(self.item_tree)

        right = ctk.CTkScrollableFrame(self, fg_color=self.colors["surface"], corner_radius=8, border_width=1, border_color=self.colors["border"], width=250)
        right.grid(row=1, column=2, sticky="ns")
        self._bind_fast_scroll_frame(right)
        ctk.CTkLabel(right, text="属性", font=self.font_title, text_color=self.colors["text"]).pack(anchor="w", padx=12, pady=(12, 8))

        self._property_entry(right, "组名", self.group_name_var)
        self._property_entry(right, "组备注", self.group_note_var)
        self._tag_entry(right, "组标签", self.group_tags_var, ["主测试", "填充材料", "单字", "双字组", "三字组", "阴平", "阳平", "上声", "去声", "变调", "对照组"])

        ctk.CTkFrame(right, height=1, fg_color=self.colors["border"]).pack(fill=tk.X, padx=12, pady=10)
        self._property_entry(right, "词项", self.item_label_var)
        self._property_entry(right, "词项备注", self.item_note_var)
        self._tag_entry(right, "词项标签", self.item_tags_var, ["目标词", "填充词", "单字", "双字组", "三字组", "阴平", "阳平", "上声", "去声", "变调", "需复核"])
        self._property_entry(right, "别名", self.item_aliases_var)
        ctk.CTkLabel(right, text="自动补全状态", font=self.font_small, text_color=self.colors["muted"]).pack(anchor="w", padx=12)
        self.source_menu = ctk.CTkOptionMenu(
            right,
            values=[DEFAULT_REVIEW_STATUS, AI_REVIEW_STATUS, REVIEWED_STATUS],
            variable=self.item_source_var,
            command=lambda _v: self._sync_from_fields(),
            fg_color=("#F3F4F6", "#374151"),
            text_color=("#1F2937", "#E5E7EB"),
            button_color=("#F3F4F6", "#374151"),
            button_hover_color=("#E5E7EB", "#4B5563"),
            height=32,
            corner_radius=16,
            font=self.font_main,
        )
        self.source_menu.pack(fill=tk.X, padx=12, pady=(4, 8))
        _apply_custom_arrow(self.source_menu)
        self._bind_child_to_scroll_frame(self.source_menu, right)

        ctk.CTkLabel(right, text="自定义字段", font=self.font_small, text_color=self.colors["muted"]).pack(anchor="w", padx=12)
        self.custom_text = ctk.CTkTextbox(
            right,
            height=90,
            corner_radius=16,
            border_width=1,
            border_color=self.colors["border"],
            fg_color=self.colors.get("surface_soft", "#F8FAFC"),
            text_color=self.colors.get("text", "#17202A"),
            font=self.font_main,
        )
        self.custom_text.pack(fill=tk.X, padx=12, pady=(4, 8))
        self.custom_text.bind("<FocusOut>", lambda _e: self._sync_from_fields())
        self._bind_child_to_scroll_frame(self.custom_text, right)
        self._button(right, "添加自定义字段", self.add_custom_field, "secondary").pack(fill=tk.X, padx=12, pady=(0, 12))
        self._bind_scroll_descendants(right, right)

        footer = ctk.CTkFrame(self, fg_color=self.colors["surface"], corner_radius=12, border_width=1, border_color=self.colors["border"])
        footer.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(10, 0))
        footer.grid_columnconfigure(6, weight=1)
        footer_actions = [
            ("打开 .ptwl", self.load_ptwl_dialog, "primary", 96),
            ("保存", self.save_ptwl_dialog, "success", 76),
            ("全部标记为已复核", self.mark_ai_reviewed, "secondary", 135),
        ]
        footer_actions.extend([(text, command, tone, 196 if "提示词" in text else (135 if "同步" in text else 104)) for text, command, tone in self.bottom_actions])
        for idx, (text, command, tone, width) in enumerate(footer_actions):
            self._button(footer, text, command, tone, width=width).grid(row=0, column=idx, padx=(10 if idx == 0 else 0, 8), pady=10, sticky="w")
        self._button(footer, "更多", self.show_more_actions, "secondary", width=72).grid(row=0, column=7, padx=(0, 10), pady=10, sticky="e")

    def _property_entry(self, parent, label, variable):
        label_widget = ctk.CTkLabel(parent, text=label, font=self.font_small, text_color=self.colors["muted"])
        label_widget.pack(anchor="w", padx=12)
        self._bind_child_to_scroll_frame(label_widget, parent)
        entry = self._entry(parent, variable)
        entry.pack(fill=tk.X, padx=12, pady=(4, 8))
        entry.bind("<FocusOut>", lambda _e: self._sync_from_fields())
        entry.bind("<Return>", lambda _e: self._sync_from_fields())
        self._bind_child_to_scroll_frame(entry, parent)
        return entry

    def _tag_entry(self, parent, label, variable, presets):
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill=tk.X, padx=12)
        label_widget = ctk.CTkLabel(row, text=label, font=self.font_small, text_color=self.colors["muted"])
        label_widget.pack(side=tk.LEFT)
        self._button(row, "常用", lambda v=variable, p=presets: self._show_tag_menu(row, v, p), "secondary", width=58).pack(side=tk.RIGHT)
        self._bind_child_to_scroll_frame(row, parent)
        placeholder = "用分号分隔，例如 目标词；单字；阴平"
        entry = self._entry(parent, variable, placeholder=placeholder)
        entry.pack(fill=tk.X, padx=12, pady=(4, 8))
        entry.bind("<FocusOut>", lambda _e: self._sync_from_fields())
        entry.bind("<Return>", lambda _e: self._sync_from_fields())
        self._bind_child_to_scroll_frame(entry, parent)
        return entry

    def _show_tag_menu(self, anchor, variable, presets):
        menu = self._make_context_menu(anchor)
        for tag in presets:
            menu.add_command(label=tag, command=lambda t=tag: self._append_tag(variable, t))
        menu.add_separator()
        menu.add_command(label="声调类：阴平 / 阳平 / 上声 / 去声", command=lambda: self._append_tag(variable, "阴平"))
        menu.add_command(label="用途类：目标词 / 填充词 / 对照组", command=lambda: self._append_tag(variable, "目标词"))
        try:
            x = anchor.winfo_rootx()
            y = anchor.winfo_rooty() + anchor.winfo_height()
            menu.tk_popup(x, y)
        finally:
            menu.grab_release()

    def _append_tag(self, variable, tag):
        tags = _split_tokens(variable.get())
        if tag not in tags:
            tags.append(tag)
        variable.set(_join_tokens(tags))
        self._sync_from_fields()

    def _emit_change(self):
        if self.on_change and not self._refreshing:
            self.on_change(self.get_document())

    def _current_group(self):
        groups = self.document.setdefault("groups", [])
        if not groups:
            groups.append({"id": "", "name": "未分组", "note": "", "tags": [], "meta": {}, "items": []})
        self.current_group_index = max(0, min(self.current_group_index, len(groups) - 1))
        return groups[self.current_group_index]

    def _current_item(self):
        group = self._current_group()
        items = group.setdefault("items", [])
        if self.current_item_index is None or self.current_item_index < 0 or self.current_item_index >= len(items):
            return None
        return items[self.current_item_index]

    def set_document(self, doc: Dict[str, Any], path: Optional[str] = None):
        self._refreshing = True
        self.document = normalize_wordlist_document(doc)
        self.current_path = path
        self.current_group_index = 0
        self.current_item_index = None
        self.title_var.set(self.document.get("title", "未命名字表"))
        self._refresh_groups()
        self._load_group_fields()
        self._refresh_items()
        self._load_item_fields()
        self._refresh_summary()
        self._refreshing = False
        self._emit_change()

    def get_document(self) -> Dict[str, Any]:
        self._sync_from_fields(emit=False)
        return normalize_wordlist_document(self.document)

    def _sync_from_fields(self, emit=True, refresh_ui=True):
        if self._refreshing:
            return
        self.document["title"] = self.title_var.get().strip() or "未命名字表"
        group = self._current_group()
        group["name"] = self.group_name_var.get().strip() or "未分组"
        group["note"] = self.group_note_var.get().strip()
        group["tags"] = _split_tokens(self.group_tags_var.get())

        item = self._current_item()
        if item is not None:
            item["label"] = self.item_label_var.get().strip()
            item["note"] = self.item_note_var.get().strip()
            item["tags"] = _split_tokens(self.item_tags_var.get())
            item["aliases"] = _split_tokens(self.item_aliases_var.get())
            item["metadata_source"] = self.item_source_var.get().strip() or DEFAULT_REVIEW_STATUS
            meta = dict(item.get("meta", {}) or {})
            custom_meta = self._parse_custom_text()
            for key in list(meta.keys()):
                if key not in CORE_META_FIELDS and key not in custom_meta:
                    meta.pop(key, None)
            meta.update(custom_meta)
            item["meta"] = meta
        self.document = normalize_wordlist_document(self.document)
        if refresh_ui:
            self._refresh_groups(keep_selection=True)
            self._refresh_items(keep_selection=True)
            self._refresh_summary()
        if emit:
            self._emit_change()

    def _parse_custom_text(self):
        meta = {}
        raw = self.custom_text.get("1.0", tk.END) if hasattr(self, "custom_text") else ""
        for line in raw.splitlines():
            if not line.strip():
                continue
            if "=" in line:
                key, value = line.split("=", 1)
            elif "：" in line:
                key, value = line.split("：", 1)
            elif ":" in line:
                key, value = line.split(":", 1)
            else:
                continue
            key = key.strip()
            value = value.strip()
            if key and key not in CORE_META_FIELDS:
                meta[key] = value
        return meta

    def _load_group_fields(self):
        group = self._current_group()
        self.group_name_var.set(group.get("name", "未分组"))
        self.group_note_var.set(group.get("note", ""))
        self.group_tags_var.set(_join_tokens(group.get("tags", [])))

    def _load_item_fields(self):
        item = self._current_item()
        if item is None:
            for var in (self.item_label_var, self.item_note_var, self.item_tags_var, self.item_aliases_var):
                var.set("")
            self.item_source_var.set(DEFAULT_REVIEW_STATUS)
            self.custom_text.delete("1.0", tk.END)
            return
        meta = item.get("meta", {})
        self.item_label_var.set(item.get("label", ""))
        self.item_note_var.set(item.get("note", ""))
        self.item_tags_var.set(_join_tokens(item.get("tags", [])))
        self.item_aliases_var.set(_join_tokens(item.get("aliases", [])))
        self.item_source_var.set(item.get("metadata_source", DEFAULT_REVIEW_STATUS) or DEFAULT_REVIEW_STATUS)
        self.custom_text.delete("1.0", tk.END)
        custom_lines = [f"{key} = {value}" for key, value in meta.items() if key not in CORE_META_FIELDS]
        if custom_lines:
            self.custom_text.insert("1.0", "\n".join(custom_lines))

    def _refresh_groups(self, keep_selection=False):
        if not keep_selection:
            self.group_list.delete(0, tk.END)
            for group in self.document.get("groups", []):
                self.group_list.insert(tk.END, group.get("name", "未分组"))
            if self.document.get("groups"):
                self.group_list.selection_clear(0, tk.END)
                self.group_list.selection_set(self.current_group_index, notify=False)
                self.group_list.activate(self.current_group_index)
            return
        self.group_list.delete(0, tk.END)
        for group in self.document.get("groups", []):
            self.group_list.insert(tk.END, group.get("name", "未分组"))
        if self.document.get("groups"):
            self.group_list.selection_set(self.current_group_index, notify=False)

    def _refresh_items(self, keep_selection=False):
        selected = self.current_item_index
        self.item_tree.delete(*self.item_tree.get_children())
        group = self._current_group()
        items = group.get("items", [])
        self.item_tree.configure(displaycolumns=self._visible_item_columns(items))
        for idx, item in enumerate(items):
            self.item_tree.insert(
                "",
                tk.END,
                iid=str(idx),
                tags=("item_active" if idx == self.current_item_index else "item_normal",),
                values=(
                    item.get("label", ""),
                    item.get("note", ""),
                    _join_tokens(item.get("tags", [])),
                    _join_tokens(item.get("aliases", [])),
                    item.get("metadata_source", DEFAULT_REVIEW_STATUS),
                ),
            )
        if keep_selection and selected is not None and str(selected) in self.item_tree.get_children():
            self._select_item(selected, notify=False)
        self._refresh_item_row_styles()

    def _visible_item_columns(self, items):
        visible = ["label", "note", "tags"]
        optional_checks = {
            "aliases": lambda item, meta: bool(item.get("aliases")),
            "source": lambda item, meta: (item.get("metadata_source", DEFAULT_REVIEW_STATUS) or DEFAULT_REVIEW_STATUS) != DEFAULT_REVIEW_STATUS,
        }
        for col, predicate in optional_checks.items():
            if any(predicate(item, item.get("meta", {})) for item in items):
                visible.append(col)
        return tuple(visible)

    def _refresh_item_row_styles(self):
        if not hasattr(self, "item_tree"):
            return
        selected = set(self.item_tree.selection())
        for iid in self.item_tree.get_children():
            if iid in selected:
                self.item_tree.item(iid, tags=("item_normal",))
            elif self._item_tree_hover == iid and int(iid) != self.current_item_index:
                self.item_tree.item(iid, tags=("item_hover",))
            elif int(iid) == self.current_item_index:
                self.item_tree.item(iid, tags=("item_active",))
            else:
                self.item_tree.item(iid, tags=("item_normal",))

    def _on_item_tree_motion(self, event):
        iid = self.item_tree.identify_row(event.y)
        if iid == self._item_tree_hover:
            return
        self._item_tree_hover = iid or None
        self._refresh_item_row_styles()

    def _on_item_tree_leave(self, _event=None):
        if self._item_tree_hover is None:
            return
        self._item_tree_hover = None
        self._refresh_item_row_styles()

    def _display_columns(self):
        display_columns = self.item_tree["displaycolumns"]
        if display_columns == "#all":
            return list(self.item_columns)
        if isinstance(display_columns, str):
            return list(self.item_tree.tk.splitlist(display_columns))
        return list(display_columns)

    def _start_cell_edit(self, event):
        iid = self.item_tree.identify_row(event.y)
        column_id = self.item_tree.identify_column(event.x)
        if not iid or not column_id:
            return "break"
        try:
            column_index = int(column_id.replace("#", "")) - 1
        except ValueError:
            return "break"
        visible_columns = self._display_columns()
        if column_index < 0 or column_index >= len(visible_columns):
            return "break"
        column_key = visible_columns[column_index]
        if column_key not in self.item_columns:
            return "break"

        self._sync_from_fields(emit=False)
        self.current_item_index = int(iid)
        self._load_item_fields()
        self._refresh_item_row_styles()

        bbox = self.item_tree.bbox(iid, column_id)
        if not bbox:
            return "break"
        value = self.item_tree.set(iid, column_key)
        editor = tk.Entry(
            self.item_tree,
            font=(self.font_family, 11, "bold"),
            bg="#FFFFFF",
            fg="#17202A",
            relief="solid",
            bd=1,
            highlightthickness=1,
            highlightbackground="#2563EB",
            highlightcolor="#2563EB",
        )
        editor.insert(0, value)
        editor.select_range(0, tk.END)
        editor.place(x=bbox[0], y=bbox[1], width=bbox[2], height=bbox[3])
        editor.focus_set()

        committed = {"done": False}

        def commit(_event=None):
            if committed["done"]:
                return "break"
            committed["done"] = True
            new_value = editor.get()
            editor.destroy()
            self._set_item_field(int(iid), column_key, new_value)
            return "break"

        def cancel(_event=None):
            if not committed["done"]:
                committed["done"] = True
                editor.destroy()
            return "break"

        editor.bind("<Return>", commit)
        editor.bind("<FocusOut>", commit)
        editor.bind("<Escape>", cancel)
        return "break"

    def _set_item_field(self, index, column_key, value):
        group = self._current_group()
        items = group.setdefault("items", [])
        if index < 0 or index >= len(items):
            return
        item = items[index]
        value = value.strip()
        if column_key == "label":
            item["label"] = value
        elif column_key == "note":
            item["note"] = value
        elif column_key == "tags":
            item["tags"] = _split_tokens(value)
        elif column_key == "aliases":
            item["aliases"] = _split_tokens(value)
        elif column_key == "source":
            item["metadata_source"] = value or DEFAULT_REVIEW_STATUS
        self.current_item_index = index
        self._load_item_fields()
        self._refresh_items(keep_selection=True)
        self._refresh_summary()
        self._emit_change()

    def _refresh_summary(self):
        summary = summarize_wordlist_document(self.document)
        self.lbl_summary.configure(text=f"{summary['groups']} 组 | {summary['items']} 项 | {summary['tags']} 个标签 | {summary['ai_pending']} 项待复核")

    def _select_item(self, index, notify=False):
        if index is None:
            return
        iid = str(index)
        if iid not in self.item_tree.get_children():
            return
        if not notify:
            self.item_tree.focus(iid)
            self.item_tree.see(iid)
            self._refresh_item_row_styles()
            return
        previous_refreshing = self._refreshing
        try:
            self.item_tree.selection_set(iid)
            self.item_tree.focus(iid)
            self.item_tree.see(iid)
        finally:
            self._refreshing = previous_refreshing
        self._refresh_item_row_styles()

    def _show_group_context_menu(self, event):
        menu = self._make_context_menu(self.group_list)
        menu.add_command(label="添加组", command=self.add_group)
        menu.add_command(label="添加词项到当前组", command=self.add_item)
        menu.add_separator()
        menu.add_command(label="删除当前组...", command=self.delete_group)
        menu.add_separator()
        menu.add_command(label="检查字表", command=self.check_document)
        menu.add_command(label="更多操作...", command=self.show_more_actions)
        self._post_menu(menu, event)
        return "break"

    def _show_item_context_menu(self, event):
        iid = self.item_tree.identify_row(event.y)
        if iid:
            self._select_item(int(iid), notify=True)
        menu = self._make_context_menu(self.item_tree)
        menu.add_command(label="添加词项", command=self.add_item)
        if iid:
            menu.add_command(label="复制当前词项", command=self.duplicate_item)
            menu.add_command(label="删除当前词项...", command=self.delete_item)
            menu.add_separator()
            menu.add_command(label="添加自定义字段...", command=self.add_custom_field)
            menu.add_command(label="标记当前词项已复核", command=self.mark_current_item_reviewed)
            menu.add_separator()
        menu.add_command(label="检查字表", command=self.check_document)
        menu.add_command(label="更多操作...", command=self.show_more_actions)
        self._post_menu(menu, event)
        return "break"

    def _on_group_select(self, _event=None):
        if self._refreshing:
            return
        selection = self.group_list.curselection()
        if not selection:
            return
        target_group_index = selection[0]
        self._sync_from_fields(emit=False, refresh_ui=False)
        self.current_group_index = target_group_index
        self.current_item_index = None
        self._refreshing = True
        self._refresh_groups(keep_selection=True)
        self._load_group_fields()
        self._refresh_items()
        self._load_item_fields()
        self._refreshing = False
        self._refresh_summary()
        self._emit_change()

    def _on_item_select(self, _event=None):
        if self._refreshing:
            return
        selection = self.item_tree.selection()
        if not selection:
            return
        target_item_index = int(selection[0])
        self._sync_from_fields(emit=False, refresh_ui=False)
        self.current_item_index = target_item_index
        self._refreshing = True
        self._load_item_fields()
        self._refreshing = False
        self._refresh_item_row_styles()
        self._emit_change()

    def add_group(self):
        self._sync_from_fields()
        self.document.setdefault("groups", []).append({"id": "", "name": "新建组", "note": "", "tags": [], "meta": {}, "items": []})
        self.current_group_index = len(self.document["groups"]) - 1
        self.current_item_index = None
        self._refresh_groups()
        self._load_group_fields()
        self._refresh_items()
        self._load_item_fields()
        self._emit_change()

    def delete_group(self):
        if len(self.document.get("groups", [])) <= 1:
            messagebox.showwarning("提示", "至少需要保留一个组。")
            return
        group = self._current_group()
        if group.get("items") and not messagebox.askyesno("确认删除", f"组“{group.get('name', '未分组')}”中还有词项，确认删除吗？"):
            return
        del self.document["groups"][self.current_group_index]
        self.current_group_index = max(0, self.current_group_index - 1)
        self.current_item_index = None
        self._refresh_groups()
        self._load_group_fields()
        self._refresh_items()
        self._load_item_fields()
        self._emit_change()

    def add_item(self):
        self._sync_from_fields()
        group = self._current_group()
        group.setdefault("items", []).append({"id": "", "label": "新词项", "note": "", "tags": [], "aliases": [], "meta": {}, "metadata_source": DEFAULT_REVIEW_STATUS})
        self.current_item_index = len(group["items"]) - 1
        self._refresh_items()
        self._select_item(self.current_item_index, notify=False)
        self._load_item_fields()
        self._emit_change()

    def delete_item(self):
        item = self._current_item()
        if item is None:
            return
        if not messagebox.askyesno("确认删除", f"确认删除词项“{item.get('label', '')}”吗？"):
            return
        group = self._current_group()
        del group["items"][self.current_item_index]
        self.current_item_index = None
        self._refresh_items()
        self._load_item_fields()
        self._refresh_summary()
        self._emit_change()

    def duplicate_item(self):
        item = self._current_item()
        if item is None:
            return
        self._sync_from_fields()
        group = self._current_group()
        copied = copy.deepcopy(item)
        copied["id"] = ""
        copied["label"] = f"{copied.get('label', '词项')} 副本".strip()
        insert_at = self.current_item_index + 1
        group.setdefault("items", []).insert(insert_at, copied)
        self.current_item_index = insert_at
        self._refresh_items()
        self._select_item(self.current_item_index, notify=False)
        self._load_item_fields()
        self._refresh_summary()
        self._emit_change()

    def mark_current_item_reviewed(self):
        item = self._current_item()
        if item is None:
            return
        self._sync_from_fields()
        item = self._current_item()
        if item is None:
            return
        item["metadata_source"] = REVIEWED_STATUS
        self.item_source_var.set(REVIEWED_STATUS)
        self._refresh_items(keep_selection=True)
        self._refresh_summary()
        self._emit_change()

    def add_custom_field(self):
        item = self._current_item()
        if item is None:
            messagebox.showwarning("提示", "请先选择一个词项。")
            return
        name = simpledialog.askstring("添加自定义字段", "字段名称：", parent=self.winfo_toplevel())
        if not name:
            return
        value = simpledialog.askstring("添加自定义字段", "字段内容：", parent=self.winfo_toplevel()) or ""
        self._sync_from_fields()
        item = self._current_item()
        item.setdefault("meta", {})[name.strip()] = value.strip()
        self._load_item_fields()
        self._refresh_items(keep_selection=True)
        self._emit_change()

    def import_v1_text(self, text: str):
        self.set_document(build_document_from_v1_text(text or ""))

    def import_csv_text(self, text: str):
        self.set_document(build_document_from_csv_text(text or ""))

    def import_v1_dialog(self):
        path = filedialog.askopenfilename(filetypes=[("Text Files", "*.txt"), ("All Files", "*.*")])
        if not path:
            return None
        try:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    text = f.read()
            except UnicodeDecodeError:
                with open(path, "r", encoding="gbk") as f:
                    text = f.read()
            self.import_v1_text(text)
            return path
        except Exception as e:
            messagebox.showerror("错误", f"导入普通字表失败：{e}")
            return None

    def import_csv_dialog(self):
        path = filedialog.askopenfilename(filetypes=[("CSV Files", "*.csv"), ("All Files", "*.*")])
        if not path:
            return None
        try:
            try:
                with open(path, "r", encoding="utf-8-sig") as f:
                    text = f.read()
            except UnicodeDecodeError:
                with open(path, "r", encoding="gbk") as f:
                    text = f.read()
            self.import_csv_text(text)
            return path
        except Exception as e:
            messagebox.showerror("错误", f"导入 CSV 失败：{e}")
            return None

    def load_ptwl_dialog(self):
        path = filedialog.askopenfilename(filetypes=[("PhonTracer 高级字表", "*.ptwl"), ("All Files", "*.*")])
        if not path:
            return None
        try:
            self.set_document(load_wordlist_document(path), path=path)
            return path
        except Exception as e:
            messagebox.showerror("错误", f"打开高级字表失败：{e}")
            return None

    def save_ptwl_dialog(self, save_as=False):
        doc = self.get_document()
        path = None if save_as else self.current_path
        if not path:
            path = filedialog.asksaveasfilename(
                defaultextension=".ptwl",
                filetypes=[("PhonTracer 高级字表", "*.ptwl"), ("All Files", "*.*")],
                initialfile=f"{doc.get('title', '高级字表')}.ptwl",
            )
        if not path:
            return None
        try:
            save_wordlist_document(doc, path)
            self.current_path = path
            messagebox.showinfo("完成", f"高级字表已保存：\n{os.path.basename(path)}")
            return path
        except Exception as e:
            messagebox.showerror("错误", f"保存高级字表失败：{e}")
            return None

    def export_v1_dialog(self):
        path = filedialog.asksaveasfilename(defaultextension=".txt", filetypes=[("Text Files", "*.txt"), ("All Files", "*.*")])
        if not path:
            return None
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(document_to_v1_text(self.get_document()))
            messagebox.showinfo("完成", f"普通字表已导出：\n{os.path.basename(path)}")
            return path
        except Exception as e:
            messagebox.showerror("错误", f"导出普通字表失败：{e}")
            return None

    def export_csv_dialog(self):
        path = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV Files", "*.csv"), ("All Files", "*.*")])
        if not path:
            return None
        try:
            with open(path, "w", encoding="utf-8-sig", newline="") as f:
                f.write(document_to_csv_text(self.get_document()))
            messagebox.showinfo("完成", f"CSV 字表已导出：\n{os.path.basename(path)}")
            return path
        except Exception as e:
            messagebox.showerror("错误", f"导出 CSV 失败：{e}")
            return None

    def mark_ai_reviewed(self):
        self.set_document(mark_ai_fields_reviewed(self.get_document()), path=self.current_path)
        messagebox.showinfo("完成", "已把 AI 推断字段标记为已人工复核。")

    def check_document(self, expected_count: Optional[int] = None):
        warnings = validate_wordlist_document(self.get_document(), expected_count=expected_count)
        if not warnings:
            messagebox.showinfo("检查结果", "高级字表检查通过。")
        else:
            messagebox.showwarning("检查结果", "\n".join(warnings[:20]) + ("\n..." if len(warnings) > 20 else ""))
        return warnings

    def get_flat_words(self):
        _groups, flat_words, _records = flatten_wordlist_document(self.get_document())
        return flat_words

    def get_flattened(self):
        return flatten_wordlist_document(self.get_document())
