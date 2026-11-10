"""
Tree UI hierarchy, event handling, drag-and-drop, and filter mixin for ProjectTreePanel.
"""
import time
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import customtkinter as ctk
import csv
import os
import numpy as np
import math
import logging
from PIL import Image, ImageDraw, ImageTk
from modules.data_utils import (
    get_export_text_for_item,
    get_formant_export_text_for_item,
    build_five_point_chart,
    split_into_syllables,
)
from modules.anomaly_detection import detect_pitch_anomaly_points
from modules.ui_widgets import CTkReleaseButton, AutoScrollbar, make_context_menu, post_context_menu
from .tree_dialogs import (
    AnomalyWarningDialog,
    ExclusionReasonDialog,
    ItemPropertiesDialog,
    CanvasButton,
    format_exclusion_reason,
    parse_exclusion_reason,
    apply_custom_dropdown_arrow,
    EXCLUSION_REASON_DETAIL_PLACEHOLDER,
    EXCLUSION_REASON_CATEGORY_PLACEHOLDER,
    EXCLUSION_REASON_CUSTOM_CATEGORY,
    EXCLUSION_REASON_CATEGORIES,
    TREE_INFO_MAX_CHARS,
)

logger = logging.getLogger(__name__)


class TreePanelUIMixin:

    def setup_ui(self):
        right_sidebar = ctk.CTkFrame(self.parent, width=300, fg_color="transparent")
        right_sidebar.pack(side=tk.RIGHT, fill=tk.Y, padx=10, pady=10)
        right_sidebar.pack_propagate(False)

        self._tree_style = ttk.Style()
        self._tree_style.theme_use("default")
        self._tree_style.configure("Treeview", background="white", foreground="#374151", rowheight=30, fieldbackground="white", borderwidth=0, font=("Microsoft YaHei", 12))
        self._tree_style.map('Treeview', background=[('selected', '#DBEAFE')], foreground=[('selected', '#1E3A8A')])

        frame_list = ctk.CTkFrame(right_sidebar, fg_color="white", corner_radius=10)
        frame_list.pack(side=tk.TOP, fill=tk.BOTH, expand=True, pady=(0, 5))
        ctk.CTkLabel(frame_list, text="项目目录", font=self.font_title, text_color="#111827", anchor="w").pack(fill=tk.X, padx=15, pady=(15, 5))

        # 1. 展开/折叠/新增组 药丸型按钮行
        ctrl_bar = ctk.CTkFrame(frame_list, fg_color="transparent")
        ctrl_bar.pack(fill=tk.X, padx=15, pady=(0, 5))

        btn_expand_all = ctk.CTkButton(
            ctrl_bar, text="展开全部", width=60, height=26, corner_radius=13,
            font=("Microsoft YaHei", 11), fg_color="#F3F4F6", text_color="#374151",
            hover_color="#E5E7EB", command=self.expand_all
        )
        btn_expand_all.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 4))

        btn_collapse_all = ctk.CTkButton(
            ctrl_bar, text="折叠全部", width=60, height=26, corner_radius=13,
            font=("Microsoft YaHei", 11), fg_color="#F3F4F6", text_color="#374151",
            hover_color="#E5E7EB", command=self.collapse_all
        )
        btn_collapse_all.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 4))

        btn_add_group = CTkReleaseButton(
            ctrl_bar, text="新增组", image=self.icons.get("plus"), compound="left",
            width=60, height=26, corner_radius=13, command=self.add_new_group,
            fg_color="#F3F4F6", text_color="#374151", hover_color="#E5E7EB"
        )
        btn_add_group.pack(side=tk.LEFT, expand=True, fill=tk.X)

        # 2. 搜索框与圆形筛选按钮行
        search_filter_frame = ctk.CTkFrame(frame_list, fg_color="transparent")
        search_filter_frame.pack(fill=tk.X, padx=15, pady=(5, 5))

        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *args: self.filter_tree())

        self.entry_search = ctk.CTkEntry(
            search_filter_frame, textvariable=self.search_var, placeholder_text="搜索...",
            font=("Microsoft YaHei", 12), height=32, fg_color="white", text_color="#1F2937",
            border_width=1, border_color="#E5E7EB", corner_radius=16, width=100
        )
        self.entry_search.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))

        self.filter_var = ctk.StringVar(value="全部")

        self.btn_filter_all = CanvasButton(
            search_filter_frame, size=36,
            image=self.tk_icons.get("filter_all_black"),
            command=lambda: self._on_filter_btn_click("全部")
        )
        self.btn_filter_all.pack(side=tk.LEFT, padx=(0, 4))

        self.btn_filter_warning = CanvasButton(
            search_filter_frame, size=36,
            image=self.tk_icons.get("filter_warning_black"),
            command=lambda: self._on_filter_btn_click("需检查")
        )
        self.btn_filter_warning.pack(side=tk.LEFT, padx=(0, 4))

        self.btn_filter_check = CanvasButton(
            search_filter_frame, size=36,
            image=self.tk_icons.get("filter_check_black"),
            command=lambda: self._on_filter_btn_click("已修改")
        )
        self.btn_filter_check.pack(side=tk.LEFT)

        # 3. 目录树容器
        tree_container = ctk.CTkFrame(frame_list, fg_color="transparent")
        tree_container.pack(fill=tk.BOTH, expand=True, padx=15, pady=(5, 10))
        tree_container.grid_columnconfigure(0, weight=1)
        tree_container.grid_rowconfigure(0, weight=1)

        self.tree = ttk.Treeview(tree_container, show='tree', selectmode='extended', columns=("info",))
        self.tree.column("#0", anchor="w", width=150, minwidth=80, stretch=True)
        self.tree.column("info", anchor="w", width=100, minwidth=60, stretch=False)
        scroll_tree = AutoScrollbar(tree_container, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll_tree.set)

        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll_tree.grid(row=0, column=1, sticky="ns", padx=(5, 0))

        self.drag_indicator = tk.Frame(self.tree, height=2, bg="#3B82F6")
        self.tree.tag_configure('hover', background='#F3F4F6')
        self.tree.tag_configure('drag_target', background='#DBEAFE')
        self.tree.tag_configure('group', background='#F3F4F6')
        self.tree.tag_configure('excluded', foreground='#9CA3AF')
        # Bind selection event to keep excluded items gray even when selected
        self.tree.bind('<<TreeviewSelect>>', self._on_tree_select_style, add='+')

        self.tree.bind('<Double-1>', self.on_tree_double_click)
        self.tree.bind('<BackSpace>', self.on_tree_backspace)
        self.tree.bind('<Delete>', self.on_tree_backspace)
        self.tree.bind('<Motion>', self.on_tree_hover)
        self.tree.bind('<Leave>', self.on_tree_leave)
        self.tree.bind('<<TreeviewSelect>>', self.on_tree_select, add='+')
        self.tree.bind('<ButtonPress-1>', self.on_tree_drag_start, add='+')
        self.tree.bind('<B1-Motion>', self.on_tree_drag_motion, add='+')
        self.tree.bind('<ButtonRelease-1>', self.on_tree_drag_release, add='+')
        self.tree.bind('<<TreeviewOpen>>', self._debounce_zebra_stripes)
        self.tree.bind('<<TreeviewClose>>', self._debounce_zebra_stripes)
        self.tree.bind('<Button-3>', self.on_right_click)
        self.tree.bind('<Button-2>', self.on_right_click)
        self.tree.bind('<Key-F2>', self.on_f2_press)
        self.tree.bind('<Return>', self.on_enter_press)

        self._update_filter_buttons()

        frame_preview = ctk.CTkFrame(right_sidebar, fg_color="white", corner_radius=10)
        frame_preview.pack(side=tk.BOTTOM, fill=tk.BOTH, expand=True, pady=(5, 0))
        ctk.CTkLabel(frame_preview, text="数据预览", font=self.font_title, text_color="#111827", anchor="w").pack(fill=tk.X, padx=15, pady=(15, 0))
        self.text_preview = ctk.CTkTextbox(frame_preview, font=self.font_code, corner_radius=8, fg_color="#F9FAFB", text_color="#1F2937", border_width=1, border_color="#E5E7EB")
        self.text_preview.pack(fill=tk.BOTH, expand=True, padx=15, pady=15)
        self.text_preview.configure(state='disabled')
        if hasattr(self.text_preview, "_textbox"):
            self.text_preview._textbox.configure(spacing1=0, spacing2=0, spacing3=0)

    def _debounce_zebra_stripes(self, event=None):
        if hasattr(self, '_zebra_timer') and self._zebra_timer:
            self.parent.after_cancel(self._zebra_timer)
        self._zebra_timer = self.parent.after(50, self._apply_zebra_stripes)

    def _apply_zebra_stripes(self):
        def get_visible_items(node=""):
            items = []
            for child in self.tree.get_children(node):
                items.append(child)
                if self.tree.item(child, 'open'):
                    items.extend(get_visible_items(child))
            return items

        visible = get_visible_items()
        self.tree.tag_configure('even', background='#F9FAFB')
        self.tree.tag_configure('odd', background='#FFFFFF')
        self.tree.tag_configure('group', background='#F3F4F6')

        leaf_count = 0
        for item in visible:
            tags = list(self.tree.item(item, 'tags'))
            if 'group' in tags:
                tags = [t for t in tags if t not in ('even', 'odd', 'hover', 'drag_target')]
                self.tree.item(item, tags=tags)
            else:
                tags = [t for t in tags if t not in ('even', 'odd', 'hover', 'drag_target')]
                tags.append('even' if leaf_count % 2 == 0 else 'odd')
                self.tree.item(item, tags=tags)
                leaf_count += 1

    def clear_ui_only(self):
        self.tree.delete(*self.tree.get_children())
        self.project_groups.clear()
        self.group_nodes.clear()
        self.current_iid = None
        self.warning_group_id = None
        self.warning_iids.clear()
        self.text_preview.configure(state='normal')
        self.text_preview.delete('1.0', tk.END)
        self.text_preview.configure(state='disabled')

    def clear_all(self):
        self.tree.delete(*self.tree.get_children())
        self.project_groups.clear()
        self.group_nodes.clear()
        self.items.clear()
        self.current_iid = None
        self.warning_group_id = None
        self.warning_iids.clear()
        self.text_preview.configure(state='normal')
        self.text_preview.delete('1.0', tk.END)
        self.text_preview.configure(state='disabled')

    def ensure_group(self, group_name):
        if group_name not in self.project_groups:
            self.project_groups.append(group_name)
        if group_name not in self.group_nodes:
            gid = f"group_node_{group_name}"
            if not self.tree.exists(gid):
                self.tree.insert("", tk.END, iid=gid, text=group_name, open=True, tags=('group',))
            self.group_nodes[group_name] = gid
        return self.group_nodes[group_name]

    def add_new_group(self):
        temp_name = "新组别"
        base_name = temp_name
        counter = 1
        while temp_name in self.project_groups:
            temp_name = f"{base_name} {counter}"
            counter += 1

        self.project_groups.append(temp_name)
        gid = self.tree.insert("", tk.END, iid=f"group_node_{temp_name}", text=temp_name, open=True, tags=('group',))
        self.group_nodes[temp_name] = gid

        if self.app:
            self.app.mark_modified()

        self.tree.see(gid)
        self.tree.selection_set(gid)
        self._debounce_zebra_stripes()
        self.parent.after(50, lambda: self.start_inline_edit(gid))

    def start_inline_edit(self, iid):
        if iid == self.warning_group_id: return
        bbox = self.tree.bbox(iid, "#0")
        if not bbox: return
        x, y, w, h = bbox
        is_group = 'group' in self.tree.item(iid, 'tags')
        if is_group:
            old_name = iid[11:]
        else:
            old_name = self.tree.item(iid, 'text')

        edit_entry = tk.Entry(self.tree, font=("Microsoft YaHei", 12), borderwidth=1, relief="solid")
        edit_entry.insert(0, old_name)
        edit_entry.select_range(0, tk.END)
        edit_entry.focus_set()
        edit_entry.place(x=x, y=y, width=w, height=h)

        saving_lock = [False]

        def save_edit(event=None):
            if saving_lock[0]:
                return
            saving_lock[0] = True
            try:
                if not edit_entry.winfo_exists(): return
                new_name = edit_entry.get().strip()

                if not new_name or new_name == old_name:
                    edit_entry.destroy()
                    return

                was_selected = (iid in self.tree.selection())

                if is_group:
                    if new_name in self.project_groups:
                        messagebox.showwarning("错误", "组名已存在")
                        if edit_entry.winfo_exists():
                            edit_entry.destroy()
                        return
                    idx = self.project_groups.index(old_name)
                    self.project_groups[idx] = new_name
                    self.group_nodes[new_name] = self.group_nodes.pop(old_name)
                    self.tree.item(iid, text=new_name)
                    for item in self.items.values():
                        if item.get('group') == old_name:
                            item['group'] = new_name
                else:
                    real_iid = iid[8:] if str(iid).startswith('warning_') else iid
                    w_iid = f"warning_{real_iid}"
                    if self.tree.exists(real_iid):
                        self.tree.item(real_iid, text=new_name)
                    if self.tree.exists(w_iid):
                        self.tree.item(w_iid, text=new_name)
                    self.items[real_iid]['label'] = new_name

                if self.app:
                    self.app.mark_modified()
                self.rebuild_tree()
                if was_selected:
                    new_gid = f"group_node_{new_name}" if is_group else iid
                    if self.tree.exists(new_gid):
                        self.tree.selection_set(new_gid)
                        self.tree.see(new_gid)
                self.update_preview()
                self._debounce_zebra_stripes()
                if edit_entry.winfo_exists():
                    edit_entry.destroy()
            finally:
                saving_lock[0] = False

        edit_entry.bind("<Return>", save_edit)
        edit_entry.bind("<FocusOut>", save_edit)
        edit_entry.bind("<Escape>", lambda e: edit_entry.destroy())

    def select_first_item(self):
        for raw_iid in list(self.items.keys()):
            for iid in (raw_iid, f"warning_{raw_iid}"):
                if self.tree.exists(iid):
                    try:
                        self.tree.selection_set(iid)
                        self.on_tree_select(None)
                        return
                    except Exception:
                        pass
        if self.on_clear_canvas:
            self.on_clear_canvas()

    def _on_tree_select_style(self, event=None):
        """Adjust selection foreground so excluded items remain gray when highlighted."""
        selection = self.tree.selection()
        has_excluded = any(
            'excluded' in self.tree.item(s, 'tags') for s in selection if self.tree.exists(s)
        )
        if has_excluded:
            # Mixed or all-excluded selection: use gray foreground for selected state
            self._tree_style.map(
                'Treeview',
                background=[('selected', '#E5E7EB')],
                foreground=[('selected', '#6B7280')]
            )
        else:
            # Normal selection: restore blue foreground
            self._tree_style.map(
                'Treeview',
                background=[('selected', '#DBEAFE')],
                foreground=[('selected', '#1E3A8A')]
            )

    def on_tree_select(self, event):
        selection = self.tree.selection()
        if not selection: return
        iid = selection[0]
        if 'item' not in self.tree.item(iid, 'tags'): return

        real_iid = iid[8:] if str(iid).startswith('warning_') else iid
        self.current_iid = real_iid
        if self.app and hasattr(self.app, 'active_speaker'):
            self.app.active_speaker.last_selected_iid = real_iid
        if self.on_item_selected:
            self.on_item_selected(real_iid)
        self.update_preview()

    def on_tree_double_click(self, event):
        iid = self.tree.identify_row(event.y)
        if not iid: return
        self.start_inline_edit(iid)

    def on_tree_backspace(self, event):
        selection = self.tree.selection()
        if not selection: return

        items_to_toggle = []
        for iid in selection:
            if 'item' in self.tree.item(iid, 'tags'):
                real_iid = iid[8:] if str(iid).startswith('warning_') else iid
                items_to_toggle.append(real_iid)

        if items_to_toggle:
            any_active = any(not self.items[iid].get('is_excluded', False) for iid in items_to_toggle)
            target_excluded = any_active
            import time
            now_str = time.strftime("%Y-%m-%d %H:%M:%S")

            for iid in items_to_toggle:
                item = self.items[iid]
                item['is_excluded'] = target_excluded
                if target_excluded:
                    item['excluded_at'] = now_str
                    if 'exclusion_reason' not in item:
                        item['exclusion_reason'] = ""
                else:
                    item.pop('excluded_at', None)
                    item.pop('exclusion_reason', None)

            if self.app:
                self.app.mark_modified()
                self.app.project_manager.trigger_auto_save()
            self.rebuild_tree()
            self.update_preview()

            if target_excluded:
                first_iid = items_to_toggle[0]
                current_reason = self.items[first_iid].get('exclusion_reason', "")
                self.prompt_exclusion_reason_dialog(items_to_toggle, current_reason)

    def prompt_exclusion_reason(self, real_iid):
        if real_iid not in self.items: return
        item = self.items[real_iid]
        current_reason = item.get('exclusion_reason', "")
        self.prompt_exclusion_reason_dialog([real_iid], current_reason)

    def prompt_exclusion_reason_dialog(self, items_to_toggle, current_reason):
        dialog = ExclusionReasonDialog(self.parent, current_reason, font_title=self.font_title, font_main=self.font_main)
        self.parent.wait_window(dialog)

        if dialog.result is not None:
            for iid in items_to_toggle:
                self.items[iid]['exclusion_reason'] = dialog.result
            if self.app:
                self.app.mark_modified()
                self.app.project_manager.trigger_auto_save()
            self.rebuild_tree()
            self.update_preview()

    def toggle_group_exclusion(self, group_name, exclude_state):
        import time
        now_str = time.strftime("%Y-%m-%d %H:%M:%S")
        modified = False
        for item in self.items.values():
            if item.get('group') == group_name:
                item['is_excluded'] = exclude_state
                if exclude_state:
                    item['excluded_at'] = now_str
                    if 'exclusion_reason' not in item:
                        item['exclusion_reason'] = ""
                else:
                    item.pop('excluded_at', None)
                    item.pop('exclusion_reason', None)
                modified = True
        if modified and self.app:
            self.app.mark_modified()
            self.app.project_manager.trigger_auto_save()
        self.rebuild_tree()
        self.update_preview()

    def permanently_delete_selected_items(self):
        selection = self.tree.selection()
        if not selection: return

        groups_to_del = [iid for iid in selection if 'group' in self.tree.item(iid, 'tags')]
        items_to_del = [iid for iid in selection if 'item' in self.tree.item(iid, 'tags')]

        if groups_to_del:
            if messagebox.askyesno("确认彻底删除组", f"确定要从工程中彻底删除选中的 {len(groups_to_del)} 个组别及其所有音频/数据吗？\n此操作不可逆！"):
                for gid in groups_to_del:
                    group_name = gid[11:]
                    items_in_group = [item_id for item_id, it in self.items.items() if it.get('group') == group_name]
                    for real_child in items_in_group:
                        self.items.pop(real_child, None)
                        w_iid = f"warning_{real_child}"
                        if self.tree.exists(w_iid):
                            self.tree.delete(w_iid)
                        if self.tree.exists(real_child):
                            self.tree.delete(real_child)
                        self.warning_iids.pop(real_child, None)

                        if self.current_iid == real_child:
                            self.current_iid = None
                            if self.on_clear_canvas: self.on_clear_canvas()

                    if gid != self.warning_group_id:
                        self.tree.delete(gid)
                        if group_name in self.project_groups: self.project_groups.remove(group_name)
                        self.group_nodes.pop(group_name, None)
                    else:
                        self.tree.delete(self.warning_group_id)
                        self.warning_group_id = None

        real_items_to_del = set()
        for iid in items_to_del:
            real_iid = iid[8:] if str(iid).startswith('warning_') else iid
            real_items_to_del.add(real_iid)

        if real_items_to_del:
            if messagebox.askyesno("确认彻底删除条目", f"确定要从工程中彻底删除选中的 {len(real_items_to_del)} 个条目及其所有音频/数据吗？\n此操作不可逆！"):
                for iid in real_items_to_del:
                    self.items.pop(iid, None)
                    w_iid = f"warning_{iid}"
                    if self.tree.exists(w_iid):
                        self.tree.delete(w_iid)
                    if self.tree.exists(iid):
                        self.tree.delete(iid)
                    self.warning_iids.pop(iid, None)

                    if self.current_iid == iid:
                        self.current_iid = None
                        if self.on_clear_canvas: self.on_clear_canvas()

        if self.warning_group_id and self.tree.exists(self.warning_group_id):
            if not self.tree.get_children(self.warning_group_id):
                self.tree.delete(self.warning_group_id)
                self.warning_group_id = None

        if self.app:
            self.app.mark_modified()
            self.app.project_manager.trigger_auto_save()
        self.rebuild_tree()
        self.update_preview()

    def on_tree_drag_start(self, event):
        self._drag_start_pos = (event.x, event.y)
        self.tree_drag_items = None

    def on_tree_drag_motion(self, event):
        if not hasattr(self, '_drag_start_pos'): return

        if self.tree_drag_items is None:
            dx = abs(event.x - self._drag_start_pos[0])
            dy = abs(event.y - self._drag_start_pos[1])
            if dx > 5 or dy > 5:
                iid = self.tree.identify_row(self._drag_start_pos[1])
                if not iid: return
                sel = self.tree.selection()
                if iid not in sel:
                    self.tree.selection_set(iid)
                    sel = (iid,)
                self.tree_drag_items = [item for item in sel if 'item' in self.tree.item(item, 'tags') and not str(item).startswith('warning_')]

        if not self.tree_drag_items: return

        # Auto scroll when mouse is within 20px of top/bottom edges
        try:
            tree_h = self.tree.winfo_height()
            if event.y < 20:
                self.tree.yview_scroll(-1, "units")
            elif event.y > tree_h - 20:
                self.tree.yview_scroll(1, "units")
        except Exception:
            pass

        target = self.tree.identify_row(event.y)
        if target == self.warning_group_id or (target and self.tree.parent(target) == self.warning_group_id):
            target = None

        if target:
            bbox = self.tree.bbox(target)
            if bbox:
                x, y, w, h = bbox
                if event.y < y + h/2: self.drag_indicator.place(x=x, y=y, width=w)
                else: self.drag_indicator.place(x=x, y=y+h, width=w)
        else: self.drag_indicator.place_forget()

        if getattr(self, 'last_drag_target', None) and self.tree.exists(self.last_drag_target) and self.last_drag_target != target:
            tags = list(self.tree.item(self.last_drag_target, 'tags'))
            if 'drag_target' in tags:
                tags.remove('drag_target')
                self.tree.item(self.last_drag_target, tags=tags)
        if target and target not in self.tree_drag_items:
            tags = list(self.tree.item(target, 'tags'))
            if 'drag_target' not in tags:
                tags.append('drag_target')
                self.tree.item(target, tags=tags)
            self.last_drag_target = target

    def on_tree_drag_release(self, event):
        self.drag_indicator.place_forget()
        if hasattr(self, '_drag_start_pos'): del self._drag_start_pos

        if getattr(self, 'last_drag_target', None) and self.tree.exists(self.last_drag_target):
            tags = list(self.tree.item(self.last_drag_target, 'tags'))
            if 'drag_target' in tags:
                tags.remove('drag_target')
                self.tree.item(self.last_drag_target, tags=tags)
        if not getattr(self, 'tree_drag_items', None): return

        target = self.tree.identify_row(event.y)
        if target == self.warning_group_id or (target and self.tree.parent(target) == self.warning_group_id):
            target = None

        if target and target not in self.tree_drag_items:
            after_target = False
            if 'group' in self.tree.item(target, 'tags'):
                parent_grp = target
                is_group_target = True
            elif 'item' in self.tree.item(target, 'tags'):
                parent_grp = self.tree.parent(target)
                is_group_target = False
                bbox = self.tree.bbox(target)
                if bbox and event.y >= bbox[1] + bbox[3] / 2:
                    after_target = True
            else:
                parent_grp = None
                is_group_target = False

            if parent_grp and parent_grp != self.warning_group_id:
                group_name = parent_grp[11:]
                if is_group_target:
                    for drag_item in self.tree_drag_items:
                        self.tree.move(drag_item, parent_grp, 'end')
                        self.items[drag_item]['group'] = group_name
                elif after_target:
                    for drag_item in reversed(self.tree_drag_items):
                        self.tree.move(drag_item, parent_grp, self.tree.index(target) + 1)
                        self.items[drag_item]['group'] = group_name
                else:
                    for drag_item in self.tree_drag_items:
                        self.tree.move(drag_item, parent_grp, self.tree.index(target))
                        self.items[drag_item]['group'] = group_name

                # 同步按组与树节点顺序全局重整 import_index，防止 rebuild_tree 抹除拖拽顺序或跨组索引碰撞
                # 当搜索或状态过滤激活时，禁止重写 import_index
                is_filtered = bool(
                    (getattr(self, 'search_var', None) and self.search_var.get().strip()) or
                    (getattr(self, 'filter_var', None) and self.filter_var.get() != "全部")
                )
                if not is_filtered:
                    try:
                        next_idx = 0
                        seen_cids = set()
                        for grp_key in getattr(self, 'project_groups', []):
                            grp_node = getattr(self, 'group_nodes', {}).get(grp_key)
                            if grp_node and self.tree.exists(grp_node):
                                for cid in self.tree.get_children(grp_node):
                                    if cid in self.items:
                                        self.items[cid]['import_index'] = next_idx
                                        seen_cids.add(cid)
                                        next_idx += 1
                        if self.tree.exists(parent_grp):
                            for cid in self.tree.get_children(parent_grp):
                                if cid in self.items and cid not in seen_cids:
                                    self.items[cid]['import_index'] = next_idx
                                    seen_cids.add(cid)
                                    next_idx += 1
                    except Exception:
                        pass
                if self.app:
                    self.app.mark_modified()
                self.rebuild_tree()
                self.update_preview()
                self._debounce_zebra_stripes()
        self.tree_drag_items = None

    def on_tree_hover(self, event):
        if getattr(self, 'tree_drag_items', None): return
        iid = self.tree.identify_row(event.y)
        if getattr(self, 'last_hover', None) and self.tree.exists(self.last_hover) and self.last_hover != iid:
            tags = list(self.tree.item(self.last_hover, 'tags'))
            if 'hover' in tags:
                tags.remove('hover')
                self.tree.item(self.last_hover, tags=tags)
        if iid and self.tree.exists(iid):
            tags = list(self.tree.item(iid, 'tags'))
            if 'hover' not in tags:
                tags.append('hover')
                self.tree.item(iid, tags=tags)
        self.last_hover = iid

    def on_tree_leave(self, event):
        if getattr(self, 'last_hover', None) and self.tree.exists(self.last_hover):
            tags = list(self.tree.item(self.last_hover, 'tags'))
            if 'hover' in tags:
                tags.remove('hover')
                self.tree.item(self.last_hover, tags=tags)
            self.last_hover = None

    def _get_all_items_by_group(self):
        structure = []
        for grp_name in self.project_groups:
            grp_node = self.group_nodes.get(grp_name)
            if grp_node:
                children = [c for c in self.tree.get_children(grp_node) if c in self.items and not self.items[c].get('is_excluded', False)]
                structure.append((grp_name, children))
        return structure

    def _get_item_index(self, target_iid):
        real_iid = target_iid[8:] if str(target_iid).startswith('warning_') else target_iid

        # 安全退保：如果节点在树中不存在（例如过滤状态下项被移除了），直接返回基于 items 字典顺序的索引
        if not self.tree.exists(real_iid):
            keys = list(self.items.keys())
            if real_iid in keys:
                return keys.index(real_iid) + 1
            return 1

        is_continuous = (self.num_rule_var.get() == "continuous")
        if not is_continuous:
            try:
                return self.tree.index(real_iid) + 1
            except tk.TclError:
                keys = list(self.items.keys())
                if real_iid in keys:
                    return keys.index(real_iid) + 1
                return 1

        target_group = self.items[real_iid].get('group', '导入内容')
        idx = 0
        for grp_name in self.project_groups:
            if grp_name == target_group: break
            grp_node = self.group_nodes.get(grp_name)
            if grp_node:
                try:
                    idx += len(self.tree.get_children(grp_node))
                except tk.TclError:
                    pass

        try:
            return idx + self.tree.index(real_iid) + 1
        except tk.TclError:
            keys = list(self.items.keys())
            if real_iid in keys:
                return keys.index(real_iid) + 1
            return 1

    def on_export_numbering_rule_changed(self):
        if self.app:
            self.app.export_numbering_rule_value = self.num_rule_var.get()
            self.app.mark_modified()
        self.update_preview()


    def update_preview(self):
        if self.current_iid not in self.items:
            self.current_iid = None

        if not self.current_iid:
            self.text_preview.configure(state='normal')
            self.text_preview.delete('1.0', tk.END)
            self.text_preview.configure(state='disabled')
            return

        item = self.items[self.current_iid]
        real_idx = self._get_item_index(self.current_iid)
        mode = getattr(self, 'app_state_params', {}).get('analysis_mode', 'f0')
        if mode == 'formant':
            from modules.data_utils import get_formant_export_text_for_item
            text = get_formant_export_text_for_item(item, real_idx, self.app_state_params['pts'])
        else:
            text = get_export_text_for_item(item, real_idx, self.app_state_params['pts'], pitch_floor=self.app_state_params.get('pitch_floor', 75.0), pitch_ceiling=self.app_state_params.get('pitch_ceiling', 600.0), voicing_threshold=self.app_state_params.get('voicing_threshold', 0.25))

        syls = split_into_syllables(item.get('label', ''))
        expected_sections = len(syls)
        shown_sections = 0
        if expected_sections > 1:
            lines = text.splitlines()
            subsection_prefix = f"{real_idx}_"
            single_prefix = f"{real_idx}."
            shown_sections = sum(1 for line in lines if line.startswith(subsection_prefix))
            if shown_sections == 0 and any(line.startswith(single_prefix) for line in lines):
                shown_sections = 1

        preview_mismatch = expected_sections > 1 and shown_sections == 1
        prev_mismatch = item.get('preview_segment_mismatch', False)
        item['preview_segment_mismatch'] = preview_mismatch
        if preview_mismatch:
            item['has_empty_data'] = True
            text = f"[致命] 检测到 {expected_sections} 个子段，但数据预览当前只显示 1 个。请检查该段边界或基频。\n\n{text}"
        if preview_mismatch != prev_mismatch:
            self._schedule_rebuild()

        warnings = item.get('warnings', [])
        if warnings:
            warnings_text = "\n".join(warnings)
            text = f"{warnings_text}\n\n{text}"

        # Process F0 data column format to be aligned for continuous vertical coloring
        lines = text.splitlines()
        f0_values = []
        if mode != 'formant':
            for line in lines:
                parts = line.split()
                if len(parts) == 2:
                    try:
                        t_val = float(parts[0])
                        f0_val = float(parts[1])
                        if f0_val > 0.0:
                            f0_values.append(f0_val)
                    except ValueError:
                        pass

        if f0_values:
            f0_min = min(f0_values)
            f0_max = max(f0_values)
            if f0_max == f0_min:
                f0_max = f0_min + 1.0
        else:
            f0_min = 0.0
            f0_max = 1.0

        formatted_lines = []
        for line in lines:
            parts = line.split()
            if mode != 'formant':
                if len(parts) == 2:
                    try:
                        t_val = float(parts[0])
                        f0_val = float(parts[1])
                        formatted_line = f"{parts[0]:<10}  {parts[1]:<12}"
                        formatted_lines.append(formatted_line)
                        continue
                    except ValueError:
                        pass
                elif len(parts) == 1:
                    try:
                        dur_val = float(parts[0])
                        formatted_lines.append(f"时长: {dur_val:.3f} s")
                        formatted_lines.append(f"{'时间 (s)':<10}  {'F0 (Hz)':<12}")
                        continue
                    except ValueError:
                        pass
            else:
                if len(parts) == 1:
                    try:
                        dur_val = float(parts[0])
                        formatted_lines.append(f"时长: {dur_val:.3f} s")
                        continue
                    except ValueError:
                        pass
            formatted_lines.append(line)
        text = "\n".join(formatted_lines)

        self.text_preview.configure(state='normal')
        self.text_preview.delete('1.0', tk.END)
        self.text_preview.insert(tk.END, text)

        if not getattr(self, "_preview_tags_initialized", False):
            self.text_preview.tag_config("zero", background="#FEE2E2", foreground="#DC2626")
            self.text_preview.tag_config("fatal_msg", foreground="#EF4444")
            self.text_preview.tag_config("warning_msg", foreground="#F59E0B")
            self.text_preview.tag_config("tip_msg", foreground="#3B82F6")
            self.text_preview.tag_config("table_header", foreground="#64748B")
            self.text_preview.tag_config("duration_line", foreground="#475569")
            if hasattr(self.text_preview, "_textbox"):
                try:
                    self.text_preview._textbox.tag_config("table_header", font=("Microsoft YaHei", 10, "bold"))
                    self.text_preview._textbox.tag_config("duration_line", font=("Microsoft YaHei", 10, "bold"))
                except Exception:
                    pass

            # Configure the 21-step Pastel Heatmap gradient palette
            # Low pitch: light blue #EFF6FF (239, 246, 255) -> Mid: light cyan #F0FDF4 (240, 253, 244) -> High: light amber #FEF3C7 (254, 243, 199)
            # Text foreground unified dark gray #1E293B
            blue_rgb = (239, 246, 255)
            cyan_rgb = (240, 253, 244)
            amber_rgb = (254, 243, 199)
            for i in range(21):
                r = i / 20.0
                if r < 0.5:
                    t = r * 2.0
                    bg_r = int(blue_rgb[0] + t * (cyan_rgb[0] - blue_rgb[0]))
                    bg_g = int(blue_rgb[1] + t * (cyan_rgb[1] - blue_rgb[1]))
                    bg_b = int(blue_rgb[2] + t * (cyan_rgb[2] - blue_rgb[2]))
                else:
                    t = (r - 0.5) * 2.0
                    bg_r = int(cyan_rgb[0] + t * (amber_rgb[0] - cyan_rgb[0]))
                    bg_g = int(cyan_rgb[1] + t * (amber_rgb[1] - cyan_rgb[1]))
                    bg_b = int(cyan_rgb[2] + t * (amber_rgb[2] - cyan_rgb[2]))
                bg_hex = f"#{bg_r:02X}{bg_g:02X}{bg_b:02X}"
                self.text_preview.tag_config(f"f0_grad_{i}", background=bg_hex, foreground="#1E293B")
            self._preview_tags_initialized = True

        lines = text.splitlines()
        for line_idx, line in enumerate(lines, start=1):
            if line.startswith("[致命]"):
                self.text_preview.tag_add("fatal_msg", f"{line_idx}.0", f"{line_idx}.end")
            elif line.startswith("[警告]"):
                self.text_preview.tag_add("warning_msg", f"{line_idx}.0", f"{line_idx}.end")
            elif line.startswith("[提示]"):
                self.text_preview.tag_add("tip_msg", f"{line_idx}.0", f"{line_idx}.end")
            elif line.startswith("时长:"):
                self.text_preview.tag_add("duration_line", f"{line_idx}.0", f"{line_idx}.end")
            elif line.startswith("时间 (s)") or line.startswith("时间(s)"):
                self.text_preview.tag_add("table_header", f"{line_idx}.0", f"{line_idx}.end")
            else:
                if mode == 'formant':
                    start_offset = 0
                    while True:
                        idx = line.find("--", start_offset)
                        if idx == -1:
                            break
                        pos_start = f"{line_idx}.{idx}"
                        pos_end = f"{line_idx}.{idx + 2}"
                        self.text_preview.tag_add("zero", pos_start, pos_end)
                        start_offset = idx + 2
                else:
                    parts = line.split()
                    if len(parts) == 2:
                        try:
                            t_val = float(parts[0])
                            f0_val = float(parts[1])
                            if f0_val > 0.0:
                                ratio = (f0_val - f0_min) / (f0_max - f0_min)
                                ratio = max(0.0, min(1.0, ratio))
                                grad_idx = int(ratio * 20.0 + 0.5)
                                self.text_preview.tag_add(f"f0_grad_{grad_idx}", f"{line_idx}.12", f"{line_idx}.end")
                            else:
                                self.text_preview.tag_add("zero", f"{line_idx}.12", f"{line_idx}.end")
                        except ValueError:
                            pass

        self.text_preview.configure(state='disabled')

    def _check_item_has_empty_data(self, item):
        """精准检测子音节区间的11点中是否含有0/NaN值（已应用智能边界收缩防误报）"""
        if not item or item.get('start') is None: return False
        if item.get('preview_segment_mismatch'):
            item['has_empty_data'] = True
            return True

        mode = getattr(self, 'app_state_params', {}).get('analysis_mode', 'f0')
        if mode == 'formant':
            f_data = item.get('formant_data')
            if not f_data:
                # "未分析" (Not Analyzed) — not counted as having empty/warning data
                item['has_empty_data'] = False
                return False
            f_xs = f_data.get('xs', np.array([]))
            f1 = f_data.get('f1', np.array([]))
            f2 = f_data.get('f2', np.array([]))
            if len(f_xs) == 0:
                item['has_empty_data'] = True
                return True

            t_s, t_e = item['start'], item['end']
            label = item.get('label', '')
            syls = split_into_syllables(label)
            chars_bounds = item.get('chars_bounds', [])
            if chars_bounds and len(chars_bounds) == len(syls):
                bounds = chars_bounds
            else:
                inner_splits = item.get('inner_splits', [])
                splits = [t_s] + [s for s in inner_splits if t_s < s < t_e] + [t_e]
                if len(syls) > 1 and len(splits) != len(syls) + 1:
                    splits = np.linspace(t_s, t_e, len(syls) + 1).tolist()
                elif len(syls) <= 1:
                    splits = [t_s, t_e]
                bounds = [[splits[i], splits[i+1]] for i in range(len(splits)-1)]

            has_empty = False
            for idx, (c_s, c_e) in enumerate(bounds):
                dur = c_e - c_s
                if dur <= 0:
                    has_empty = True
                    break
                margin = dur * 0.125
                core_s = c_s + margin
                core_e = c_e - margin

                mask = (f_xs >= core_s) & (f_xs <= core_e)
                seg_xs = f_xs[mask]
                seg_f1 = f1[mask]
                seg_f2 = f2[mask]
                if len(seg_xs) == 0:
                    has_empty = True
                    break
                valid_mask = ~np.isnan(seg_f1) & ~np.isnan(seg_f2) & (seg_f2 > seg_f1)
                ratio = np.sum(valid_mask) / len(seg_xs)
                if ratio < 0.40:
                    has_empty = True
                    break

            item['has_empty_data'] = has_empty
            return has_empty

        # 1. 如果 Pitch 数据已加载，优先执行最高精度的实时重新计算，并更新缓存
        # 注：此分支仅使用 pitch 数组进行检测，不需要 snd 对象
        if item.get('pitch') or item.get('pitch_data'):
            num_points = int(self.app_state_params.get('pts', 10))
            t_s, t_e = item['start'], item['end']
            label = item.get('label', '')
            inner_splits = item.get('inner_splits', [])

            syls = split_into_syllables(label)
            chars_bounds = item.get('chars_bounds', [])
            if chars_bounds and len(chars_bounds) == len(syls):
                bounds = chars_bounds
            else:
                splits = [t_s] + [s for s in inner_splits if t_s < s < t_e] + [t_e]
                if len(syls) > 1 and len(splits) != len(syls) + 1:
                    splits = np.linspace(t_s, t_e, len(syls) + 1).tolist()
                elif len(syls) <= 1:
                    splits = [t_s, t_e]
                bounds = [[splits[i], splits[i+1]] for i in range(len(splits)-1)]

            if item.get('pitch_data'):
                p_xs = item['pitch_data']['xs']
                p_freqs = item['pitch_data']['freqs']
            else:
                pitch = item['pitch']
                p_xs = pitch.xs()
                p_freqs = pitch.selected_array['frequency']

            has_empty = False
            for c_s, c_e in bounds:
                if c_e <= c_s: continue

                # 智能收缩围栏，过滤Gap
                valid_idx = np.where((p_xs >= c_s) & (p_xs <= c_e) & (p_freqs > 0))[0]
                if len(valid_idx) >= 2:
                    v_s, v_e = p_xs[valid_idx[0]], p_xs[valid_idx[-1]]
                    seg_xs = p_xs[valid_idx]
                    seg_ys = p_freqs[valid_idx]
                else:
                    has_empty = True
                    break

                if v_e <= v_s:
                    has_empty = True
                    break

                times = np.linspace(v_s, v_e, num_points)
                f0s = np.interp(times, seg_xs, seg_ys)
                for t, hz in zip(times, f0s):
                    if np.min(np.abs(seg_xs - t)) > 0.025 or np.isnan(hz) or hz <= 0:
                        has_empty = True
                        break
                if has_empty:
                    break

            item['has_empty_data'] = has_empty
            return has_empty

        # 2. 如果音频没有加载，则退回到已有缓存标记
        if 'has_empty_data' in item:
            return item['has_empty_data']

        if item.get('preview_f0'):
            return any(hz == 0 for hz in item['preview_f0'])

        return False

    def _schedule_rebuild(self, delay=10, reanalyze=True):
        if self._rebuild_timer:
            self.parent.after_cancel(self._rebuild_timer)
        self._rebuild_timer = self.parent.after(delay, lambda: self.rebuild_tree(reanalyze=reanalyze))

    def filter_tree(self, delay=250):
        self._schedule_rebuild(delay=delay, reanalyze=False)

    def _on_filter_btn_click(self, mode):
        current_mode = self.filter_var.get()
        if mode == "已修改":
            if current_mode == "已修改":
                new_mode = "未修改"
            elif current_mode == "未修改":
                new_mode = "已修改"
            else:
                new_mode = "已修改"
            self.filter_var.set(new_mode)
        elif mode == "全部":
            if current_mode == "全部":
                new_mode = "已忽略"
            elif current_mode == "已忽略":
                new_mode = "全部"
            else:
                new_mode = "全部"
            self.filter_var.set(new_mode)
        else:
            self.filter_var.set(mode)
        self._update_filter_buttons()
        self._schedule_rebuild(delay=10, reanalyze=False)

    def _update_filter_buttons(self):
        current_mode = self.filter_var.get()

        # 动态更新全部/已忽略按钮的背景颜色：全部为蓝色，已忽略为灰色
        if current_mode == "全部":
            self.btn_filter_all.update_active_colors("#3B82F6", "#2563EB")
        elif current_mode == "已忽略":
            self.btn_filter_all.update_active_colors("#9CA3AF", "#6B7280")

        self.btn_filter_all.configure_button(
            image=self.tk_icons.get("filter_all_white" if current_mode in ("全部", "已忽略") else "filter_all_black"),
            is_active=(current_mode in ("全部", "已忽略"))
        )
        self.btn_filter_warning.configure_button(
            image=self.tk_icons.get("filter_warning_white" if current_mode == "需检查" else "filter_warning_black"),
            is_active=(current_mode == "需检查")
        )

        # 动态更新已修改/未修改过滤按钮的背景颜色：已修改为蓝色，未修改为灰色
        if current_mode == "已修改":
            self.btn_filter_check.update_active_colors("#3B82F6", "#2563EB")
        elif current_mode == "未修改":
            self.btn_filter_check.update_active_colors("#9CA3AF", "#6B7280")

        self.btn_filter_check.configure_button(
            image=self.tk_icons.get("filter_check_white" if current_mode in ("已修改", "未修改") else "filter_check_black"),
            is_active=(current_mode in ("已修改", "未修改"))
        )

    def expand_all(self):
        for gid in self.group_nodes.values():
            try:
                if self.tree.exists(gid):
                    self.tree.item(gid, open=True)
            except tk.TclError:
                pass
        if self.warning_group_id:
            try:
                if self.tree.exists(self.warning_group_id):
                    self.tree.item(self.warning_group_id, open=True)
            except tk.TclError:
                pass
        self._debounce_zebra_stripes()

    def collapse_all(self):
        for gid in self.group_nodes.values():
            try:
                if self.tree.exists(gid):
                    self.tree.item(gid, open=False)
            except tk.TclError:
                pass
        if self.warning_group_id:
            try:
                if self.tree.exists(self.warning_group_id):
                    self.tree.item(self.warning_group_id, open=False)
            except tk.TclError:
                pass
        self._debounce_zebra_stripes()

    def on_f2_press(self, event=None):
        sel = self.tree.selection()
        if sel:
            self.start_inline_edit(sel[0])

    def on_enter_press(self, event=None):
        sel = self.tree.selection()
        if sel:
            iid = sel[0]
            tags = self.tree.item(iid, 'tags')
            if 'item' in tags:
                real_iid = iid[8:] if str(iid).startswith('warning_') else iid
                self.show_item_properties(real_iid)

    def show_item_properties(self, real_iid):
        item = self.items.get(real_iid)
        if not item:
            return
        ItemPropertiesDialog(
            self.parent,
            real_iid,
            item,
            self.app_state_params,
            font_title=self.font_title,
            font_main=self.font_main
        )

    def _get_item_analysis_mode(self, item):
        return item.get('analysis_mode', self.app_state_params.get('analysis_mode', 'f0'))

    def _get_item_f0_export_params(self, item):
        return {
            'pitch_floor': item.get('pitch_floor', self.app_state_params.get('pitch_floor', 75.0)),
            'pitch_ceiling': item.get('pitch_ceiling', self.app_state_params.get('pitch_ceiling', 600.0)),
            'voicing_threshold': item.get('voicing_threshold', self.app_state_params.get('voicing_threshold', 0.25))
        }

    def on_right_click(self, event):
        from modules.ui_widgets import make_context_menu, post_context_menu
        iid = self.tree.identify_row(event.y)

        if iid:
            sel = self.tree.selection()
            if iid not in sel:
                self.tree.selection_set(iid)

        menu = make_context_menu(self.tree)

        if iid and self.tree.exists(iid):
            tags = self.tree.item(iid, 'tags')
            if 'item' in tags:
                real_iid = iid[8:] if str(iid).startswith('warning_') else iid
                item = self.items.get(real_iid)
                if item:
                    is_excluded = item.get('is_excluded', False)
                    exclude_lbl = "恢复此项" if is_excluded else "忽略此项（不参与导出）"
                    menu.add_command(label=exclude_lbl, command=lambda: self.on_tree_backspace(None))

                    if is_excluded:
                        menu.add_command(label="填写忽略原因...", command=lambda: self.prompt_exclusion_reason(real_iid))

                    is_ignored = item.get('ignore_warnings', False)
                    label_text = "仅恢复异常提示" if is_ignored else "仅忽略异常提示"
                    menu.add_command(label=label_text, command=lambda: self.toggle_ignore_warnings(real_iid))

                    menu.add_command(label="重命名", command=lambda: self.start_inline_edit(real_iid))
                    menu.add_command(label="属性", command=lambda: self.show_item_properties(real_iid))

                    menu.add_separator()
                    adv_menu = make_context_menu(menu)
                    adv_menu.add_command(label="彻底删除此项...", command=lambda: self.permanently_delete_selected_items())
                    menu.add_cascade(label="高级操作", menu=adv_menu)
            elif 'group' in tags and iid != self.warning_group_id:
                group_name = iid[11:]
                menu.add_command(label="忽略整组 (不参与导出)", command=lambda: self.toggle_group_exclusion(group_name, True))
                menu.add_command(label="恢复整组 (参与导出)", command=lambda: self.toggle_group_exclusion(group_name, False))
                menu.add_separator()
                menu.add_command(label="重命名组", command=lambda: self.start_inline_edit(iid))
                menu.add_command(label="清空此组中所有项", command=lambda: self.clear_group_items(iid))
                menu.add_separator()
                menu.add_command(label="删除组及其所有项", command=lambda: self.delete_group_and_items(iid))
        else:
            menu.add_command(label="新建组别", command=self.add_new_group)

        post_context_menu(menu, event)

    def toggle_ignore_warnings(self, real_iid):
        if real_iid not in self.items: return
        item = self.items[real_iid]
        old_val = item.get('ignore_warnings', False)
        item['ignore_warnings'] = not old_val

        if self.app:
            self.app.mark_modified()
            self.app.project_manager.trigger_auto_save()

        self.rebuild_tree()

    def clear_group_items(self, gid):
        if not self.tree.exists(gid): return
        group_name = gid[11:]
        if messagebox.askyesno("清空确认", f"确定要清空组【{group_name}】中的所有音频和数据吗？"):
            iids_to_del = [iid for iid, item in list(self.items.items()) if item.get('group') == group_name]
            for iid in iids_to_del:
                self.items.pop(iid, None)
                if iid == self.current_iid:
                    self.current_iid = None
                    if self.on_clear_canvas: self.on_clear_canvas()

            if self.app:
                self.app.mark_modified()
                self.app.project_manager.trigger_auto_save()

            messagebox.showinfo("成功", f"组【{group_name}】中的 {len(iids_to_del)} 个项已清空。")
            self.rebuild_tree()
            self.update_preview()

    def delete_group_and_items(self, gid):
        if not self.tree.exists(gid): return
        group_name = gid[11:]
        if messagebox.askyesno("删除确认", f"确定要彻底删除组【{group_name}】及其中的所有音频和数据吗？"):
            iids_to_del = [iid for iid, item in list(self.items.items()) if item.get('group') == group_name]
            for iid in iids_to_del:
                self.items.pop(iid, None)
                if iid == self.current_iid:
                    self.current_iid = None
                    if self.on_clear_canvas: self.on_clear_canvas()

            if group_name in self.project_groups:
                self.project_groups.remove(group_name)
            self.group_nodes.pop(group_name, None)

            if self.app:
                self.app.mark_modified()
                self.app.project_manager.trigger_auto_save()

            self.rebuild_tree()
            self.update_preview()

    def rebuild_tree(self, force_reanalyze=None, reanalyze=True):
        if force_reanalyze is not None:
            reanalyze = bool(force_reanalyze)
        # 1. 保存当前选择和展开状态
        sel = self.tree.selection()
        mapped_sel = []
        for s in sel:
            if str(s).startswith('warning_'):
                mapped_sel.append(s[8:])
            else:
                mapped_sel.append(s)
        expanded_groups = set()
        for g_name, gid in self.group_nodes.items():
            try:
                if self.tree.exists(gid) and self.tree.item(gid, 'open'):
                    expanded_groups.add(g_name)
            except tk.TclError:
                pass
        if self.warning_group_id:
            try:
                if self.tree.exists(self.warning_group_id) and self.tree.item(self.warning_group_id, 'open'):
                    expanded_groups.add('__warning__')
            except tk.TclError:
                pass

        # 2. 清空 Tree
        for node in list(self.tree.get_children()):
            self.tree.delete(node)
        self.group_nodes.clear()
        self.warning_group_id = None
        self.warning_iids.clear()

        # 3. 读取搜索和过滤条件
        search_query = ""
        if hasattr(self, 'search_var') and self.search_var:
            search_query = self.search_var.get().strip().lower()

        status_filter = "全部"
        if hasattr(self, 'filter_var') and self.filter_var:
            status_filter = self.filter_var.get()

        # 4. 分组过滤数据
        groups_in_use = list(self.project_groups)
        for iid, item in self.items.items():
            g = item.get('group', '导入内容')
            if g not in groups_in_use:
                groups_in_use.append(g)

        group_items = {g: [] for g in groups_in_use}
        warning_items = []
        group_stats = None
        speaker_stats = None

        for iid, item in self.items.items():
            lbl = item.get('label', '')
            if search_query and search_query not in lbl.lower():
                continue

            # 搜索与状态过滤(reanalyze=False)优先复用已有缓存；数据变更重建(reanalyze=True)则重算
            if not reanalyze and item.get('warnings') is not None:
                warnings = item['warnings']
            else:
                if group_stats is None:
                    group_stats, speaker_stats = self._compute_group_and_speaker_stats()
                    self._cached_group_stats = group_stats
                    self._cached_speaker_stats = speaker_stats
                warnings = self.analyze_item_anomalies(item, group_stats, speaker_stats)
                item['warnings'] = warnings

            needs_check = any(w.startswith("[致命]") or w.startswith("[警告]") for w in item['warnings']) and not item.get('is_excluded', False)
            if status_filter == "已忽略" and not item.get('is_excluded', False):
                continue
            if status_filter != "全部" and status_filter != "已忽略" and item.get('is_excluded', False):
                continue
            if status_filter == "需检查" and not needs_check:
                continue
            if status_filter == "已修改" and not item.get('is_manual_edited', False):
                continue
            if status_filter == "未修改" and item.get('is_manual_edited', False):
                continue

            grp = item.get('group', '导入内容')
            group_items[grp].append((iid, item))
            if needs_check:
                warning_items.append((iid, item))

        keys_order = {k: idx for idx, k in enumerate(self.items.keys())}
        def get_sort_key(x):
            iid, item = x
            if 'import_index' in item:
                return item['import_index']
            if item.get('start') is not None:
                return item['start']
            return keys_order.get(iid, 999999)

        # 5. 插入“需要检查”组
        if warning_items:
            warning_items.sort(key=get_sort_key)
            w_count = len(warning_items)
            w_text = f"需要检查 ({w_count})"
            is_open = '__warning__' in expanded_groups or not expanded_groups
            self.warning_group_id = self.tree.insert("", 0, iid="group_node___warning__", text=w_text, open=is_open, tags=('group', 'warning_group'))
            for iid, item in warning_items:
                w_iid = f"warning_{iid}"
                img = self.tk_icons.get('warning', '') if self.tk_icons else ''
                info_text = self._get_item_info_text(item)
                self.tree.insert(self.warning_group_id, 'end', iid=w_iid, text=item.get('label', ''), image=img, tags=('item', 'warning_item'), values=(info_text,))
                self.warning_iids[iid] = w_iid

        # 6. 插入常规组
        for grp in groups_in_use:
            items_in_grp = group_items.get(grp, [])
            if not items_in_grp and (search_query or status_filter != "全部"):
                continue

            items_in_grp.sort(key=get_sort_key)

            total_count = len(items_in_grp)
            excluded_count = sum(1 for iid, item in items_in_grp if item.get('is_excluded', False))
            if excluded_count > 0:
                g_text = f"{grp} ({total_count - excluded_count}/{total_count}, 忽略 {excluded_count})"
            else:
                g_text = f"{grp} ({total_count})"
            is_open = grp in expanded_groups or not expanded_groups
            gid = self.tree.insert("", 'end', iid=f"group_node_{grp}", text=g_text, open=is_open, tags=('group',))
            self.group_nodes[grp] = gid

            for iid, item in items_in_grp:
                display = item.get('label', '')
                if item.get('is_excluded', False):
                    img = self.tk_icons.get('gray_dot', '') if self.tk_icons else ''
                else:
                    has_empty = any(w.startswith("[致命]") or w.startswith("[警告]") for w in item.get('warnings', []))
                    if has_empty:
                        img = self.tk_icons.get('warning', '') if self.tk_icons else ''
                    elif item.get('is_manual_edited'):
                        img = self.tk_icons.get('blue_dot', '') if self.tk_icons else ''
                    else:
                        img = ''  # Removed sound wave icon

                tags_list = ['item']
                if item.get('is_excluded', False):
                    tags_list.append('excluded')
                info_text = self._get_item_info_text(item)
                self.tree.insert(gid, 'end', iid=iid, text=display, tags=tuple(tags_list), image=img, values=(info_text,))

        # 7. 恢复选择和可见性
        if sel:
            valid_sel = [s for s in sel if self.tree.exists(s)]
            if not valid_sel:
                for s in mapped_sel:
                    w_s = f"warning_{s}"
                    if self.tree.exists(w_s):
                        valid_sel.append(w_s)
                    elif self.tree.exists(s):
                        valid_sel.append(s)
            if valid_sel:
                try:
                    self.tree.selection_set(valid_sel)
                    self.tree.see(valid_sel[0])
                except tk.TclError:
                    pass

        self._debounce_zebra_stripes()
        if self.current_iid:
            self.update_preview()

    def update_item_icon(self, iid):
        if str(iid).startswith('warning_'): return
        item = self.items.get(iid)
        if not item or item.get('start') is None: return

        item.pop('warnings', None)
        self._check_item_has_empty_data(item)

        search_query = ""
        if hasattr(self, 'search_var') and self.search_var:
            search_query = self.search_var.get().strip().lower()
        status_filter = "全部"
        if hasattr(self, 'filter_var') and self.filter_var:
            status_filter = self.filter_var.get()

        if self.tree.exists(iid) and status_filter == "全部" and not search_query:
            group_stats = getattr(self, '_cached_group_stats', None)
            speaker_stats = getattr(self, '_cached_speaker_stats', None)
            if group_stats is None or speaker_stats is None:
                group_stats, speaker_stats = self._compute_group_and_speaker_stats()
                self._cached_group_stats = group_stats
                self._cached_speaker_stats = speaker_stats
            warnings = self.analyze_item_anomalies(item, group_stats, speaker_stats)
            item['warnings'] = warnings

            needs_check = any(w.startswith("[致命]") or w.startswith("[警告]") for w in warnings) and not item.get('is_excluded', False)
            was_in_warning = iid in getattr(self, 'warning_iids', {})

            if needs_check == was_in_warning:
                if item.get('is_excluded', False):
                    img = self.tk_icons.get('gray_dot', '') if self.tk_icons else ''
                else:
                    if needs_check:
                        img = self.tk_icons.get('warning', '') if self.tk_icons else ''
                    elif item.get('is_manual_edited'):
                        img = self.tk_icons.get('blue_dot', '') if self.tk_icons else ''
                    else:
                        img = ''
                info_text = self._get_item_info_text(item)
                try:
                    self.tree.item(iid, image=img, values=(info_text,))
                    if was_in_warning:
                        w_iid = self.warning_iids.get(iid)
                        if w_iid and self.tree.exists(w_iid):
                            w_img = self.tk_icons.get('warning', '') if self.tk_icons else ''
                            self.tree.item(w_iid, image=w_img, values=(info_text,))
                except tk.TclError:
                    pass
                if self.current_iid == iid:
                    self.update_preview()
                return

        self._schedule_rebuild(delay=10, reanalyze=True)

