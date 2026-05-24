import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import customtkinter as ctk
import csv
import parselmouth
import numpy as np
import math
import matplotlib.pyplot as plt
import logging
from .data_utils import get_export_text_for_item, build_five_point_chart, write_analysis_sheet_with_formulas, split_into_syllables
from .anomaly_detection import detect_pitch_anomaly_points
from .ui_widgets import CTkReleaseButton, AutoScrollbar
from PIL import Image, ImageDraw, ImageTk

logger = logging.getLogger(__name__)


class CanvasButton(tk.Canvas):
    def __init__(self, parent, size=32, image=None, bg_color="white", active_bg="#3B82F6", hover_bg="#F3F4F6", active_hover="#2563EB", border_color="#E5E7EB", is_active=False, command=None):
        super().__init__(parent, width=size, height=size, bg=bg_color, highlightthickness=0, cursor="hand2")
        self.size = size
        self.image = image
        self.bg_color = bg_color
        self.active_bg = active_bg
        self.hover_bg = hover_bg
        self.active_hover = active_hover
        self.border_color = border_color
        self.is_active = is_active
        self.command = command

        self.hovered = False
        self._bg_images = {}  # Cache pre-rendered anti-aliased backgrounds

        # Pre-cache backgrounds for states to avoid rendering on the fly
        self.precache_backgrounds()

        self.draw()

        self.bind("<Enter>", self.on_enter)
        self.bind("<Leave>", self.on_leave)
        self.bind("<ButtonPress-1>", self.on_press)
        self.bind("<ButtonRelease-1>", self.on_release)

    def precache_backgrounds(self):
        # We render circular backgrounds using PIL draw supersampling for perfect anti-aliasing
        scale = 4
        canvas_size = self.size * scale

        states = [
            ("normal", "white", self.border_color),
            ("hover", self.hover_bg, self.border_color),
            ("active", self.active_bg, None),
            ("active_hover", self.active_hover, None)
        ]

        for name, fill_color, border_color in states:
            img = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)

            x0, y0 = 2, 2
            x1, y1 = canvas_size - 2, canvas_size - 2

            if border_color:
                b_width = 1 * scale
                draw.ellipse([x0, y0, x1, y1], fill=fill_color)
                draw.ellipse([x0, y0, x1, y1], outline=border_color, width=b_width)
            else:
                draw.ellipse([x0, y0, x1, y1], fill=fill_color)

            resized_img = img.resize((self.size, self.size), Image.Resampling.LANCZOS)
            self._bg_images[name] = ImageTk.PhotoImage(resized_img)

    def draw(self):
        self.delete("all")

        # Determine which pre-cached background image to use
        if self.is_active:
            bg_name = "active_hover" if self.hovered else "active"
        else:
            bg_name = "hover" if self.hovered else "normal"

        bg_img = self._bg_images.get(bg_name)
        if bg_img:
            self.create_image(self.size//2, self.size//2, image=bg_img)

        # Draw the icon in the center
        if self.image:
            self.create_image(self.size//2, self.size//2, image=self.image)

    def configure_button(self, image=None, is_active=None):
        if image is not None:
            self.image = image
        if is_active is not None:
            self.is_active = is_active
        self.draw()

    def on_enter(self, event):
        self.hovered = True
        self.draw()

    def on_leave(self, event):
        self.hovered = False
        self.draw()

    def on_press(self, event):
        pass

    def on_release(self, event):
        if self.hovered and self.command:
            self.command()


class ProjectTreePanel:
    def __init__(self, parent, icons, items_dict, app_state_params, on_item_selected_callback, on_clear_canvas_callback, tk_icons=None, app=None):
        self.parent = parent
        self.app = app
        self.icons = icons
        self.tk_icons = tk_icons or {}
        self.items = items_dict
        self.app_state_params = app_state_params
        self.on_item_selected = on_item_selected_callback
        self.on_clear_canvas = on_clear_canvas_callback
        if app and hasattr(app, 'export_numbering_rule_var'):
            self.num_rule_var = app.export_numbering_rule_var
        else:
            self.num_rule_var = ctk.StringVar(value="continuous")

        self.project_groups = []
        self.group_nodes = {}
        self.current_iid = None
        self.tree_drag_items = None
        self.last_hover = None

        self.warning_group_id = None
        self.warning_iids = {}
        self._rebuild_timer = None
        self._rebuilding = False

        try:
            self.font_title = ctk.CTkFont(family="Microsoft YaHei", size=15, weight="bold")
            self.font_main = ctk.CTkFont(family="Microsoft YaHei", size=13)
            self.font_code = ctk.CTkFont(family="Consolas", size=13)
        except Exception:
            self.font_title = ("Microsoft YaHei", 15, "bold")
            self.font_main = ("Microsoft YaHei", 13)
            self.font_code = ("Consolas", 13)

        self.setup_ui()

    def setup_ui(self):
        right_sidebar = ctk.CTkFrame(self.parent, width=300, fg_color="transparent")
        right_sidebar.pack(side=tk.RIGHT, fill=tk.Y, padx=10, pady=10)
        right_sidebar.pack_propagate(False)

        style = ttk.Style()
        style.theme_use("default")
        style.configure("Treeview", background="white", foreground="#374151", rowheight=34, fieldbackground="white", borderwidth=0, font=("Microsoft YaHei", 14))
        style.map('Treeview', background=[('selected', '#DBEAFE')], foreground=[('selected', '#1E3A8A')])

        frame_list = ctk.CTkFrame(right_sidebar, fg_color="white", corner_radius=10)
        frame_list.pack(side=tk.TOP, fill=tk.BOTH, expand=True, pady=(0, 5))
        ctk.CTkLabel(frame_list, text="项目目录", font=self.font_title, text_color="#111827").pack(pady=(15, 5))

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
            search_filter_frame, size=32,
            image=self.tk_icons.get("filter_all_black"),
            command=lambda: self._on_filter_btn_click("全部")
        )
        self.btn_filter_all.pack(side=tk.LEFT, padx=(0, 4))

        self.btn_filter_warning = CanvasButton(
            search_filter_frame, size=32,
            image=self.tk_icons.get("filter_warning_black"),
            command=lambda: self._on_filter_btn_click("需检查")
        )
        self.btn_filter_warning.pack(side=tk.LEFT, padx=(0, 4))

        self.btn_filter_check = CanvasButton(
            search_filter_frame, size=32,
            image=self.tk_icons.get("filter_check_black"),
            command=lambda: self._on_filter_btn_click("已修改")
        )
        self.btn_filter_check.pack(side=tk.LEFT)

        # 3. 目录树容器
        tree_container = ctk.CTkFrame(frame_list, fg_color="transparent")
        tree_container.pack(fill=tk.BOTH, expand=True, padx=15, pady=(5, 10))
        tree_container.grid_columnconfigure(0, weight=1)
        tree_container.grid_rowconfigure(0, weight=1)

        self.tree = ttk.Treeview(tree_container, show='tree', selectmode='extended')
        scroll_tree = AutoScrollbar(tree_container, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll_tree.set)

        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll_tree.grid(row=0, column=1, sticky="ns", padx=(5, 0))

        self.drag_indicator = tk.Frame(self.tree, height=2, bg="#3B82F6")
        self.tree.tag_configure('hover', background='#F3F4F6')
        self.tree.tag_configure('drag_target', background='#DBEAFE')
        self.tree.tag_configure('group', background='#F3F4F6')

        self.tree.bind('<Double-1>', self.on_tree_double_click)
        self.tree.bind('<BackSpace>', self.on_tree_backspace)
        self.tree.bind('<Delete>', self.on_tree_backspace)
        self.tree.bind('<Motion>', self.on_tree_hover)
        self.tree.bind('<Leave>', self.on_tree_leave)
        self.tree.bind('<<TreeviewSelect>>', self.on_tree_select)
        self.tree.bind('<ButtonPress-1>', self.on_tree_drag_start, add='+')
        self.tree.bind('<B1-Motion>', self.on_tree_drag_motion, add='+')
        self.tree.bind('<ButtonRelease-1>', self.on_tree_drag_release, add='+')
        self.tree.bind('<<TreeviewOpen>>', self._debounce_zebra_stripes)
        self.tree.bind('<<TreeviewClose>>', self._debounce_zebra_stripes)
        self.tree.bind('<Button-3>', self.on_right_click)
        self.tree.bind('<Button-2>', self.on_right_click)
        self.tree.bind('<Key-F2>', self.on_f2_press)

        self._update_filter_buttons()

        frame_preview = ctk.CTkFrame(right_sidebar, fg_color="white", corner_radius=10)
        frame_preview.pack(side=tk.BOTTOM, fill=tk.BOTH, expand=True, pady=(5, 0))
        ctk.CTkLabel(frame_preview, text="数据预览", font=self.font_title, text_color="#111827").pack(pady=(15, 0))
        self.text_preview = ctk.CTkTextbox(frame_preview, font=self.font_code, corner_radius=8, fg_color="#F9FAFB", text_color="#1F2937", border_width=1, border_color="#E5E7EB")
        self.text_preview.pack(fill=tk.BOTH, expand=True, padx=15, pady=15)
        self.text_preview.configure(state='disabled')

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
            gid = self.tree.insert("", tk.END, iid=f"group_node_{group_name}", text=group_name, open=True, tags=('group',))
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
        old_name = self.tree.item(iid, 'text')

        edit_entry = tk.Entry(self.tree, font=("Microsoft YaHei", 12), borderwidth=1, relief="solid")
        edit_entry.insert(0, old_name)
        edit_entry.select_range(0, tk.END)
        edit_entry.focus_set()
        edit_entry.place(x=x, y=y, width=w, height=h)

        def save_edit(event=None):
            new_name = edit_entry.get().strip()
            if not edit_entry.winfo_exists(): return

            if not new_name or new_name == old_name:
                edit_entry.destroy()
                return

            if 'group' in self.tree.item(iid, 'tags'):
                if new_name in self.project_groups:
                    messagebox.showwarning("错误", "组名已存在")
                    edit_entry.destroy()
                    return
                idx = self.project_groups.index(old_name)
                self.project_groups[idx] = new_name
                self.group_nodes[new_name] = self.group_nodes.pop(old_name)
                self.tree.item(iid, text=new_name)
                for child in self.tree.get_children(iid):
                    if child in self.items: self.items[child]['group'] = new_name
            elif 'item' in self.tree.item(iid, 'tags'):
                real_iid = iid[8:] if str(iid).startswith('warning_') else iid
                w_iid = f"warning_{real_iid}"
                if self.tree.exists(real_iid):
                    self.tree.item(real_iid, text=new_name)
                if self.tree.exists(w_iid):
                    self.tree.item(w_iid, text=new_name)
                self.items[real_iid]['label'] = new_name

            if self.app:
                self.app.mark_modified()
            self.update_preview()
            self._debounce_zebra_stripes()
            edit_entry.destroy()

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

        groups_to_del = [iid for iid in selection if 'group' in self.tree.item(iid, 'tags')]
        items_to_del = [iid for iid in selection if 'item' in self.tree.item(iid, 'tags')]

        if groups_to_del:
            if messagebox.askyesno("确认删除", f"确定要删除选中的 {len(groups_to_del)} 个组别吗？"):
                for gid in groups_to_del:
                    group_name = self.tree.item(gid, 'text')
                    for child in self.tree.get_children(gid):
                        real_child = child[8:] if str(child).startswith('warning_') else child
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
        self.update_preview()
        self._debounce_zebra_stripes()

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

        target = self.tree.identify_row(event.y)
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
        if target and target not in self.tree_drag_items:
            if 'group' in self.tree.item(target, 'tags'):
                parent_grp = target
                target_idx = 'end'
            elif 'item' in self.tree.item(target, 'tags'):
                parent_grp = self.tree.parent(target)
                target_idx = self.tree.index(target)
            else:
                parent_grp = None

            if parent_grp and parent_grp != self.warning_group_id:
                group_name = self.tree.item(parent_grp, 'text')
                for drag_item in reversed(self.tree_drag_items):
                    self.tree.move(drag_item, parent_grp, target_idx)
                    self.items[drag_item]['group'] = group_name
                if self.app:
                    self.app.mark_modified()
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
                children = [c for c in self.tree.get_children(grp_node) if c in self.items]
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

        self.text_preview.configure(state='normal')
        self.text_preview.delete('1.0', tk.END)
        self.text_preview.insert(tk.END, text)

        self.text_preview.tag_config("zero", foreground="#EF4444")
        self.text_preview.tag_config("fatal_msg", foreground="#EF4444")
        self.text_preview.tag_config("warning_msg", foreground="#F59E0B")
        self.text_preview.tag_config("tip_msg", foreground="#3B82F6")

        lines = text.splitlines()
        for line_idx, line in enumerate(lines, start=1):
            if line.startswith("[致命]"):
                self.text_preview.tag_add("fatal_msg", f"{line_idx}.0", f"{line_idx}.end")
            elif line.startswith("[警告]"):
                self.text_preview.tag_add("warning_msg", f"{line_idx}.0", f"{line_idx}.end")
            elif line.startswith("[提示]"):
                self.text_preview.tag_add("tip_msg", f"{line_idx}.0", f"{line_idx}.end")
            else:
                parts = line.split()
                if len(parts) == 2 and parts[1] == "0.000000":
                    first_len = len(parts[0])
                    sub_str = line[first_len:]
                    f0_start_offset = sub_str.find("0.000000")
                    if f0_start_offset != -1:
                        start_char = first_len + f0_start_offset
                        pos_start = f"{line_idx}.{start_char}"
                        pos_end = f"{line_idx}.{start_char + 8}"
                        self.text_preview.tag_add("zero", pos_start, pos_end)

        self.text_preview.configure(state='disabled')

    def _check_item_has_empty_data(self, item):
        """精准检测子音节区间的11点中是否含有0/NaN值（已应用智能边界收缩防误报）"""
        if not item or item.get('start') is None: return False
        if item.get('preview_segment_mismatch'):
            item['has_empty_data'] = True
            return True

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

    def _schedule_rebuild(self):
        if self._rebuild_timer:
            self.parent.after_cancel(self._rebuild_timer)
        self._rebuild_timer = self.parent.after(10, self.rebuild_tree)

    def filter_tree(self):
        self._schedule_rebuild()

    def _on_filter_btn_click(self, mode):
        self.filter_var.set(mode)
        self._update_filter_buttons()
        self.filter_tree()

    def _update_filter_buttons(self):
        current_mode = self.filter_var.get()

        self.btn_filter_all.configure_button(
            image=self.tk_icons.get("filter_all_white" if current_mode == "全部" else "filter_all_black"),
            is_active=(current_mode == "全部")
        )
        self.btn_filter_warning.configure_button(
            image=self.tk_icons.get("filter_warning_white" if current_mode == "需检查" else "filter_warning_black"),
            is_active=(current_mode == "需检查")
        )
        self.btn_filter_check.configure_button(
            image=self.tk_icons.get("filter_check_white" if current_mode == "已修改" else "filter_check_black"),
            is_active=(current_mode == "已修改")
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

    def on_right_click(self, event):
        iid = self.tree.identify_row(event.y)

        if iid:
            sel = self.tree.selection()
            if iid not in sel:
                self.tree.selection_set(iid)

        menu = tk.Menu(self.tree, tearoff=0, font=("Microsoft YaHei", 11))

        if iid and self.tree.exists(iid):
            tags = self.tree.item(iid, 'tags')
            if 'item' in tags:
                menu.add_command(label="重命名 (F2)", command=lambda: self.start_inline_edit(iid))
                menu.add_command(label="删除选中项 (Delete)", command=lambda: self.on_tree_backspace(None))
            elif 'group' in tags and iid != self.warning_group_id:
                menu.add_command(label="重命名组", command=lambda: self.start_inline_edit(iid))
                menu.add_command(label="清空此组中所有项", command=lambda: self.clear_group_items(iid))
                menu.add_separator()
                menu.add_command(label="删除组及其所有项", command=lambda: self.delete_group_and_items(iid))
        else:
            menu.add_command(label="新建组别", command=self.add_new_group)

        menu.post(event.x_root, event.y_root)

    def clear_group_items(self, gid):
        if not self.tree.exists(gid): return
        group_name = self.tree.item(gid, 'text').split(' (')[0]
        if messagebox.askyesno("清空确认", f"确定要清空组【{group_name}】中的所有音频和数据吗？"):
            iids_to_del = [iid for iid, item in list(self.items.items()) if item.get('group') == group_name]
            for iid in iids_to_del:
                self.items.pop(iid, None)
                if iid == self.current_iid:
                    self.current_iid = None
                    if self.on_clear_canvas: self.on_clear_canvas()

            messagebox.showinfo("成功", f"组【{group_name}】中的 {len(iids_to_del)} 个项已清空。")
            self.rebuild_tree()
            self.update_preview()

    def delete_group_and_items(self, gid):
        if not self.tree.exists(gid): return
        group_name = self.tree.item(gid, 'text').split(' (')[0]
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

            self.rebuild_tree()
            self.update_preview()

    def _extract_item_features(self, item):
        t_s, t_e = item.get('start'), item.get('end')
        if t_s is None or t_e is None or t_e <= t_s:
            return None

        duration = t_e - t_s
        p_xs, p_freqs = self._get_pitch_arrays_for_item(item)
        if p_xs is None or p_freqs is None or len(p_xs) == 0:
            if item.get('preview_f0'):
                p_freqs = np.array(item['preview_f0'])
                active_freqs = p_freqs[~np.isnan(p_freqs) & (p_freqs > 0)]
                mean_f0 = float(np.mean(active_freqs)) if len(active_freqs) > 0 else 0.0
                f0_range = float(np.max(active_freqs) - np.min(active_freqs)) if len(active_freqs) > 0 else 0.0
                active_ratio = float(len(active_freqs) / len(p_freqs)) if len(p_freqs) > 0 else 0.0
                return {
                    'duration': duration,
                    'mean_f0': mean_f0,
                    'f0_range': f0_range,
                    'active_ratio': active_ratio
                }
            return None

        mask = (p_xs >= t_s) & (p_xs <= t_e)
        p_xs_slice = p_xs[mask]
        p_freqs_slice = p_freqs[mask]

        if len(p_xs_slice) == 0:
            return {
                'duration': duration,
                'mean_f0': 0.0,
                'f0_range': 0.0,
                'active_ratio': 0.0
            }

        active_freqs = p_freqs_slice[~np.isnan(p_freqs_slice) & (p_freqs_slice > 0)]
        if len(active_freqs) == 0:
            return {
                'duration': duration,
                'mean_f0': 0.0,
                'f0_range': 0.0,
                'active_ratio': 0.0
            }

        mean_f0 = float(np.mean(active_freqs))
        f0_range = float(np.max(active_freqs) - np.min(active_freqs))
        active_ratio = float(len(active_freqs) / len(p_xs_slice))

        return {
            'duration': duration,
            'mean_f0': mean_f0,
            'f0_range': f0_range,
            'active_ratio': active_ratio
        }

    def analyze_item_anomalies(self, item, group_stats=None, speaker_stats=None):
        warnings = []
        if not item or item.get('start') is None:
            warnings.append("[致命] 时间边界无效或缺失")
            return warnings

        if item.get('preview_segment_mismatch'):
            warnings.append("[致命] 子段数量与预览不匹配")

        if self._check_item_has_empty_data(item):
            warnings.append("[致命] 基频数据含有0值 (F0 缺失)")

        feats = self._extract_item_features(item)
        if feats is not None:
            p_xs, p_freqs = self._get_pitch_arrays_for_item(item)
            if p_xs is not None and p_freqs is not None and len(p_xs) > 0:
                t_s, t_e = item.get('start'), item.get('end')
                mask = (p_xs >= t_s) & (p_xs <= t_e)
                p_xs_slice = p_xs[mask]
                p_freqs_slice = p_freqs[mask]

                _, bounds = self._get_syllables_and_bounds(item)
                if not bounds:
                    bounds = [[t_s, t_e]]

                anomaly_points = detect_pitch_anomaly_points(
                    p_xs_slice, p_freqs_slice, bounds=bounds, start=t_s, end=t_e
                )
                if len(anomaly_points) > 0:
                    jump_times = ", ".join([f"{t:.2f}s" for t, _ in anomaly_points[:5]])
                    suffix = "..." if len(anomaly_points) > 5 else ""
                    warnings.append(f"[警告] 疑似倍频/半频/噪声点 (发生在: {jump_times}{suffix})")

            split_warnings = item.get('split_warnings', [])
            for sw in split_warnings:
                if sw == 'tiny_segment':
                    warnings.append("[致命] 边界过短 (某个子段短于 80ms)")
                elif sw == 'imbalanced_duration':
                    warnings.append("[警告] 时长严重失衡 (子段时长比例不均)")
                elif sw == 'no_clear_valley':
                    warnings.append("[警告] 未能识别到能量谷 (子音节切分谷底不明显)")
                elif sw == 'fallback_equal_split':
                    warnings.append("[提示] 采用等分兜底切割")
                elif sw == 'low_f0_coverage':
                    warnings.append("[致命] F0 覆盖率低 (某子段有效基频点比例低于 30%)")

            g = (item.get('group', '导入内容'), len(item.get('chars_bounds', [[0, 1]])))
            if group_stats and (g in group_stats or item.get('group', '导入内容') in group_stats):
                g_feats = group_stats.get(g) or group_stats.get(item.get('group', '导入内容'))
                for key, (med, mad) in g_feats.items():
                    val = feats[key]
                    if mad > 0:
                        deviation = abs(val - med) / mad
                        if deviation > 4.0:
                            if key == 'duration':
                                warnings.append(f"[提示] 时长明显偏离同类项目 (当前 {val:.3f}s, 同类中位数 {med:.3f}s)")
                            elif key == 'mean_f0':
                                warnings.append(f"[提示] 基频均值明显偏离同类项目 (当前 {val:.1f}Hz, 同类中位数 {med:.1f}Hz)")
                            elif key == 'f0_range':
                                warnings.append(f"[提示] F0 波动范围明显偏离同类项目 (当前 {val:.1f}Hz, 同类中位数 {med:.1f}Hz)")
                            elif key == 'active_ratio' and val < med:
                                warnings.append(f"[提示] 有效点比例偏低 (当前 {val:.1%}, 同类中位数 {med:.1%})")

            if speaker_stats:
                spk_id = self.app.speaker_manager.active_speaker_id if self.app and hasattr(self.app, 'speaker_manager') else None
                if spk_id and spk_id in speaker_stats:
                    spk_info = speaker_stats[spk_id]
                    if spk_info.get('mean_f0_outlier'):
                        warnings.append(f"[提示] 建议检查 Pitch Floor/Ceiling 或录音质量 (发音人整体基频 {spk_info['mean_f0']:.1f}Hz 偏离其他发音人中位数)")
                    g_feats = group_stats.get(g) if group_stats else None
                    if g_feats and 'active_ratio' in g_feats:
                        g_active_med = g_feats['active_ratio'][0]
                        if g_active_med < 0.60:
                            warnings.append(f"[提示] 建议检查 Pitch Floor/Ceiling 或录音质量 (当前组平均有效基频点比例仅 {g_active_med:.1%})")

        return warnings

    def rebuild_tree(self):
        # 1. 保存当前选择和展开状态
        sel = self.tree.selection()
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

        # 计算项目统计信息以用于离群值检测
        group_stats = {}
        groups_items_features = {}
        for iid, item in self.items.items():
            syl_count = len(item.get('chars_bounds', [[0, 1]]))
            g = (item.get('group', '导入内容'), syl_count)
            if g not in groups_items_features:
                groups_items_features[g] = []
            feats = self._extract_item_features(item)
            if feats is not None:
                groups_items_features[g].append(feats)

        # 计算每组的 median and robust spread。普通统计离群只作为轻提示，
        # 因此要求更多样本并使用较宽的下限，避免正常语速/声调差异刷屏。
        for g, feats_list in groups_items_features.items():
            if len(feats_list) >= 8:
                group_stats[g] = {}
                for key in ['duration', 'mean_f0', 'f0_range', 'active_ratio']:
                    if key in ('mean_f0', 'f0_range'):
                        vals = np.array([
                            f[key] for f in feats_list
                            if f.get('active_ratio', 0.0) >= 0.60 and f.get(key, 0.0) > 0
                        ])
                    else:
                        vals = np.array([f[key] for f in feats_list])
                    if len(vals) < 8:
                        continue
                    med = np.median(vals)
                    abs_dev = np.abs(vals - med)
                    mad = 1.4826 * np.median(abs_dev)
                    if key == 'duration':
                        min_spread = max(0.15, abs(med) * 0.25)
                    elif key == 'mean_f0':
                        min_spread = 35.0
                    elif key == 'f0_range':
                        min_spread = 40.0
                    else:
                        min_spread = 0.20
                    mad = max(float(mad), min_spread)
                    group_stats[g][key] = (med, mad)

        # 计算发音人统计信息以用于组级检测
        speaker_stats = {}
        sm = getattr(self.app, 'speaker_manager', None)
        if sm:
            all_speakers = sm.get_all_speakers()
            speaker_means = []
            for spk in all_speakers:
                spk_freqs = []
                spk_ratios = []
                for iid, item in spk.items.items():
                    feats = self._extract_item_features(item)
                    if feats is not None:
                        if feats['mean_f0'] > 0:
                            spk_freqs.append(feats['mean_f0'])
                        spk_ratios.append(feats['active_ratio'])
                mean_f0 = np.mean(spk_freqs) if spk_freqs else 0.0
                mean_ratio = np.mean(spk_ratios) if spk_ratios else 0.0
                speaker_stats[spk.id] = {
                    'mean_f0': mean_f0,
                    'mean_ratio': mean_ratio,
                    'mean_f0_outlier': False
                }
                if mean_f0 > 0:
                    speaker_means.append((spk.id, mean_f0))

            if len(speaker_means) >= 3:
                means = np.array([item[1] for item in speaker_means])
                med_spk = np.median(means)
                mad_spk = max(1.4826 * np.median(np.abs(means - med_spk)), 45.0)
                for spk_id, mean_f0 in speaker_means:
                    if abs(mean_f0 - med_spk) / mad_spk > 4.0:
                        speaker_stats[spk_id]['mean_f0_outlier'] = True

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

        for iid, item in self.items.items():
            lbl = item.get('label', '')
            if search_query and search_query not in lbl.lower():
                continue

            item['warnings'] = self.analyze_item_anomalies(item, group_stats, speaker_stats)
            needs_check = any(w.startswith("[致命]") or w.startswith("[警告]") for w in item['warnings'])
            if status_filter == "需检查" and not needs_check:
                continue
            if status_filter == "已修改" and not item.get('is_manual_edited', False):
                continue

            grp = item.get('group', '导入内容')
            group_items[grp].append((iid, item))
            if needs_check:
                warning_items.append((iid, item))

        # 5. 插入“需要检查”组
        if warning_items:
            w_count = len(warning_items)
            w_text = f"需要检查 ({w_count})"
            is_open = '__warning__' in expanded_groups or not expanded_groups
            self.warning_group_id = self.tree.insert("", 0, iid="group_node___warning__", text=w_text, open=is_open, tags=('group', 'warning_group'))
            for iid, item in warning_items:
                w_iid = f"warning_{iid}"
                img = self.tk_icons.get('warning', '') if self.tk_icons else ''
                self.tree.insert(self.warning_group_id, 'end', iid=w_iid, text=item.get('label', ''), image=img, tags=('item', 'warning_item'))
                self.warning_iids[iid] = w_iid

        # 6. 插入常规组
        for grp in groups_in_use:
            items_in_grp = group_items.get(grp, [])
            if not items_in_grp and (search_query or status_filter != "全部"):
                continue

            g_text = f"{grp} ({len(items_in_grp)})"
            is_open = grp in expanded_groups or not expanded_groups
            gid = self.tree.insert("", 'end', iid=f"group_node_{grp}", text=g_text, open=is_open, tags=('group',))
            self.group_nodes[grp] = gid

            for iid, item in items_in_grp:
                display = item.get('label', '')
                has_empty = any(w.startswith("[致命]") or w.startswith("[警告]") for w in item.get('warnings', []))
                if has_empty:
                    img = self.tk_icons.get('warning', '') if self.tk_icons else ''
                elif item.get('is_manual_edited'):
                    img = self.tk_icons.get('blue_dot', '') if self.tk_icons else ''
                else:
                    img = ''  # Removed sound wave icon

                self.tree.insert(gid, 'end', iid=iid, text=display, tags=('item',), image=img)

        # 7. 恢复选择和可见性
        if sel:
            valid_sel = [s for s in sel if self.tree.exists(s)]
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

        self._check_item_has_empty_data(item)
        self._schedule_rebuild()

    def export_project(self):
        sm = getattr(self.app, 'speaker_manager', None)
        if not self.items and (not sm or len(sm.get_all_speakers()) <= 1):
            return messagebox.showwarning("提示", "没有可导出的数据。")
        if sm and len(sm.get_all_speakers()) > 1:
            self._show_multi_speaker_export_dialog(sm)
        else:
            self._do_export_preparation(None)

    def _show_multi_speaker_export_dialog(self, sm):
        dlg = ctk.CTkToplevel(self.parent)
        dlg.title("导出范围选择")
        dlg.geometry("400x440")
        dlg.resizable(False, False)
        dlg.transient(self.parent)
        dlg.grab_set()
        dlg.update_idletasks()
        main_win = self.parent.winfo_toplevel()
        x = main_win.winfo_rootx() + (main_win.winfo_width() - 400) // 2
        y = main_win.winfo_rooty() + (main_win.winfo_height() - 440) // 2
        dlg.geometry(f"+{x}+{y}")
        
        font_small = ctk.CTkFont(family="Microsoft YaHei", size=11)
        ctk.CTkLabel(dlg, text="请选择需要导出的发音人：", font=self.font_title, text_color=("#111827", "#F9FAFB")).pack(pady=(15, 5))
        
        scroll_frame = ctk.CTkScrollableFrame(dlg, height=180, border_width=1, border_color=("#E5E7EB", "#475569"), fg_color=("#FFFFFF", "#1E293B"))
        scroll_frame.pack(fill=tk.BOTH, padx=30, pady=5)
        
        all_speakers = sm.get_all_speakers()
        active_speaker = sm.get_active_speaker()
        
        checkboxes = {}
        for spk in all_speakers:
            is_active = (spk.id == active_speaker.id)
            val = ctk.BooleanVar(value=is_active)
            cb = ctk.CTkCheckBox(scroll_frame, text=f"{spk.name} ({len(spk.items)}项)", variable=val, font=self.font_main, 
                                 fg_color=("#3B82F6", "#2563EB"), hover_color=("#60A5FA", "#3B82F6"), border_color=("#9CA3AF", "#4B5563"))
            cb.pack(anchor="w", padx=15, pady=6)
            checkboxes[spk] = val
            
        util_frame = ctk.CTkFrame(dlg, fg_color="transparent")
        util_frame.pack(fill=tk.X, padx=30, pady=5)
        
        def select_all():
            for val in checkboxes.values():
                val.set(True)
        def select_none():
            for val in checkboxes.values():
                val.set(False)
                
        ctk.CTkButton(util_frame, text="全选", width=75, height=26, corner_radius=6, font=font_small, 
                      fg_color=("#F3F4F6", "#374151"), text_color=("#2563EB", "#60A5FA"), hover_color=("#E5E7EB", "#4B5563"), 
                      border_width=1, border_color=("#D1D5DB", "#475569"), command=select_all).pack(side=tk.LEFT, padx=5)
        ctk.CTkButton(util_frame, text="全不选", width=75, height=26, corner_radius=6, font=font_small, 
                      fg_color=("#F3F4F6", "#374151"), text_color=("#4B5563", "#D1D5DB"), hover_color=("#E5E7EB", "#4B5563"), 
                      border_width=1, border_color=("#D1D5DB", "#475569"), command=select_none).pack(side=tk.LEFT, padx=5)
        
        ctk.CTkFrame(dlg, height=1, fg_color=("#E5E7EB", "#475569")).pack(fill=tk.X, padx=30, pady=8)
        
        integrate_var = ctk.BooleanVar(value=False)
        cb_integrate = ctk.CTkCheckBox(dlg, text="整合选中发音人的结果 (采用 T值归一化)", variable=integrate_var, font=self.font_main,
                                       fg_color=("#3B82F6", "#2563EB"), hover_color=("#60A5FA", "#3B82F6"), border_color=("#9CA3AF", "#4B5563"))
        cb_integrate.pack(anchor="w", padx=40, pady=5)
        
        def on_confirm():
            selected_speakers = [spk for spk, var in checkboxes.items() if var.get()]
            if not selected_speakers:
                return messagebox.showwarning("提示", "请至少勾选一个发音人。", parent=dlg)
            
            do_integrate = integrate_var.get()
            dlg.destroy()
            self._do_custom_export_preparation(selected_speakers, do_integrate)
            
        btn_frame = ctk.CTkFrame(dlg, fg_color="transparent")
        btn_frame.pack(pady=(15, 10))
        
        ctk.CTkButton(btn_frame, text="取消", width=90, height=36, corner_radius=10, 
                      fg_color=("#F3F4F6", "#374151"), text_color=("#4B5563", "#D1D5DB"), hover_color=("#E5E7EB", "#4B5563"), 
                      border_width=1, border_color=("#D1D5DB", "#475569"), font=self.font_main, command=dlg.destroy).pack(side=tk.LEFT, padx=10)
        ctk.CTkButton(btn_frame, text="下一步", width=90, height=36, corner_radius=10, 
                      fg_color=("#3B82F6", "#2563EB"), text_color="#FFFFFF", hover_color=("#2563EB", "#1D4ED8"), font=self.font_main, command=on_confirm).pack(side=tk.LEFT, padx=10)

    def _do_custom_export_preparation(self, selected_speakers, do_integrate):
        empty_labels = []
        for s in selected_speakers:
            for grp_name, children in self._get_items_by_group_for_dict(s.items):
                for child in children:
                    item = s.items[child]
                    if self._check_item_has_empty_data(item):
                        empty_labels.append(f"[{s.name}] {item['label']}")
        if empty_labels:
            msg = "部分项目的基频数据包含 0 值：\n\n" + "\n".join(empty_labels[:10])
            if len(empty_labels) > 10: msg += f"\n... 等共 {len(empty_labels)} 项"
            msg += "\n\n是否继续导出？"
            if not messagebox.askyesno("空数据警告", msg): return
            
        if len(selected_speakers) == 1 and not do_integrate:
            s = selected_speakers[0]
            orig_items = self.items
            self.items = s.items
            tree_structure = self._get_all_items_by_group()
            self.items = orig_items
            self._check_empty_and_show_menu(tree_structure, mode='single', all_speakers=selected_speakers)
        else:
            if do_integrate:
                self._show_export_menu(mode='integrated', all_speakers=selected_speakers)
            else:
                self._show_export_menu(mode='separate', all_speakers=selected_speakers)

    def _do_export_preparation(self, multi_speaker_mode):
        self._do_custom_export_preparation([self.app.speaker_manager.get_active_speaker()], False)

    def _get_items_by_group_for_dict(self, items_dict):
        groups = {}
        for k, v in items_dict.items():
            g = v.get('group', '导入内容')
            if g not in groups: groups[g] = []
            groups[g].append(k)
        return [(g, groups[g]) for g in groups]

    def _check_empty_and_show_menu(self, tree_structure, mode='single'):
        empty_labels = []
        for grp_name, children in tree_structure:
            for child in children:
                item = self.items[child]
                if self._check_item_has_empty_data(item): empty_labels.append(f"[{grp_name}] {item['label']}")
        if empty_labels:
            msg = "以下项目的基频数据包含 0 值（可能无法提取有效声调）：\n\n" + "\n".join(empty_labels[:10])
            if len(empty_labels) > 10: msg += f"\n... 等共 {len(empty_labels)} 项"
            msg += "\n\n是否继续导出？"
            if not messagebox.askyesno("空数据警告", msg): return
        self._show_export_menu(tree_structure=tree_structure, mode=mode)

    def _show_export_menu(self, tree_structure=None, mode='single', all_speakers=None):
        dlg = ctk.CTkToplevel(self.parent)
        dlg.title("选择导出格式")
        dlg.geometry("320x330")
        dlg.resizable(False, False)
        dlg.transient(self.parent)
        dlg.grab_set()
        dlg.update_idletasks()
        main_win = self.parent.winfo_toplevel()
        x = main_win.winfo_rootx() + (main_win.winfo_width() - 320) // 2
        y = main_win.winfo_rooty() + (main_win.winfo_height() - 330) // 2
        dlg.geometry(f"+{x}+{y}")
        ctk.CTkLabel(dlg, text="请选择导出格式", font=self.font_title, text_color=("#111827", "#F9FAFB")).pack(pady=(20, 15))
        btn_kwargs = {"corner_radius": 10, "height": 44, "font": self.font_main, "anchor": "w", "compound": "left", "border_width": 1.5}

        def do_export(format_mode):
            if format_mode == 'line_chart':
                dlg.destroy()
                from .acoustic_exporter import AcousticChartExportDialog
                AcousticChartExportDialog(self.parent, app=self.app, project_tree=self, mode=mode, all_speakers=all_speakers)
                return
            if format_mode == 'kde':
                dlg.destroy()
                self._show_kde_params_dialog(mode=mode, tree_structure=tree_structure, all_speakers=all_speakers)
                return
            dlg.destroy()
            def execute_export(out_path, inc_chart=False):
                try:
                    if mode == 'single':
                        orig_items = self.items
                        if all_speakers and len(all_speakers) == 1:
                            self.items = all_speakers[0].items
                        try:
                            if format_mode == 'txt': self._export_txt(out_path, tree_structure=tree_structure)
                            elif format_mode == 'xlsx': self._export_xlsx(out_path, include_chart=inc_chart, tree_structure=tree_structure)
                            elif format_mode == 'textgrid':
                                is_batch = False
                                if self.app and hasattr(self.app, 'tabview'): is_batch = (self.app.tabview.get() == "多条独立音频")
                                elif hasattr(self.parent, 'tabview'): is_batch = (self.parent.tabview.get() == "多条独立音频")
                                if is_batch: self._export_textgrid_batch(out_path, tree_structure=tree_structure)
                                else: self._export_textgrid_long(out_path, tree_structure=tree_structure)
                            elif format_mode == 'line_chart': self._export_line_chart(out_path, tree_structure=tree_structure)
                            elif format_mode == 'kde': self._export_kde_heatmap(out_path, tree_structure=tree_structure)
                        finally:
                            self.items = orig_items
                    elif mode == 'separate':
                        import os
                        for s in all_speakers:
                            s_struct = self._get_items_by_group_for_dict(s.items)
                            orig_items = self.items
                            self.items = s.items
                            if format_mode == 'textgrid':
                                s_out = os.path.join(out_path, s.name)
                                os.makedirs(s_out, exist_ok=True)
                                is_batch = False
                                if getattr(s, 'tab_mode', None) == "多条独立音频": is_batch = True
                                if is_batch: self._export_textgrid_batch(s_out, tree_structure=s_struct)
                                else: self._export_textgrid_long(os.path.join(s_out, f"{s.name}.TextGrid"), tree_structure=s_struct)
                            else:
                                if os.path.isdir(out_path): s_out = os.path.join(out_path, f"{s.name}.{'txt' if format_mode=='txt' else 'xlsx' if format_mode=='xlsx' else 'png'}")
                                else:
                                    base, ext = os.path.splitext(out_path)
                                    s_out = f"{base}_{s.name}{ext}"
                                if format_mode == 'txt': self._export_txt(s_out, tree_structure=s_struct)
                                elif format_mode == 'xlsx': self._export_xlsx(s_out, include_chart=inc_chart, tree_structure=s_struct)
                                elif format_mode == 'line_chart': self._export_line_chart(s_out, tree_structure=s_struct)
                                elif format_mode == 'kde': self._export_kde_heatmap(s_out, tree_structure=s_struct)
                            self.items = orig_items
                    elif mode == 'integrated':
                        if format_mode in ('txt', 'xlsx'): self._export_integrated(out_path, format_mode, inc_chart, all_speakers)
                        elif format_mode == 'line_chart': self._export_line_chart_integrated(out_path, all_speakers)
                        elif format_mode == 'kde': self._export_kde_heatmap_integrated(out_path, all_speakers)
                        else:
                            messagebox.showwarning("提示", "未知的整合导出格式。")
                            return False
                    return True
                except Exception as e:
                    messagebox.showerror("错误", str(e))
                    import logging
                    logging.getLogger(__name__).error(f"Export error: {e}", exc_info=True)
                    return False
            if format_mode == 'txt':
                out = filedialog.askdirectory(title="选择导出文件夹") if mode == 'separate' else filedialog.asksaveasfilename(title="导出文本", defaultextension=".txt", initialfile="tone_export_data", filetypes=[("文本文件", "*.txt")])
                if out and execute_export(out): messagebox.showinfo("成功", f"数据已导出至:\n{out}")
            elif format_mode == 'textgrid':
                if mode == 'integrated': return messagebox.showwarning("提示", "不支持整合导出 TextGrid。")
                out = filedialog.askdirectory(title="选择TextGrid导出文件夹") if mode == 'separate' else None
                if mode != 'separate':
                    is_batch = False
                    if self.app and hasattr(self.app, 'tabview'): is_batch = (self.app.tabview.get() == "多条独立音频")
                    elif hasattr(self.parent, 'tabview'): is_batch = (self.parent.tabview.get() == "多条独立音频")
                    out = filedialog.askdirectory(title="选择TextGrid导出文件夹") if is_batch else filedialog.asksaveasfilename(title="导出 TextGrid", defaultextension=".TextGrid", initialfile="tone_export_data", filetypes=[("TextGrid 文件", "*.TextGrid")])
                if out and execute_export(out): messagebox.showinfo("成功", f"TextGrid 已导出至:\n{out}")
            elif format_mode == 'xlsx':
                out = filedialog.askdirectory(title="选择导出文件夹") if mode == 'separate' else filedialog.asksaveasfilename(title="导出Excel", defaultextension=".xlsx", initialfile="tone_export_data", filetypes=[("Excel 表格", "*.xlsx")])
                if out:
                    inc_chart = False if mode == 'integrated' else messagebox.askyesno("导出设置", "是否在 Excel 中包含分析图表？", default=messagebox.NO)
                    if execute_export(out, inc_chart): messagebox.showinfo("成功", f"数据已导出至:\n{out}")
            elif format_mode == 'line_chart':
                out = filedialog.askdirectory(title="选择图表导出文件夹") if mode == 'separate' else filedialog.asksaveasfilename(title="导出折线图", defaultextension=".png", initialfile="tone_line_chart", filetypes=[("PNG 图片", "*.png"), ("SVG 矢量图", "*.svg"), ("PDF 文档", "*.pdf")])
                if out and execute_export(out): messagebox.showinfo("成功", f"图表已导出至:\n{out}")
            elif format_mode == 'kde':
                out = filedialog.askdirectory(title="选择热力图导出文件夹") if mode == 'separate' else filedialog.asksaveasfilename(title="导出热力图", defaultextension=".png", initialfile="tone_heatmap", filetypes=[("PNG 图片", "*.png")])
                if out and execute_export(out): messagebox.showinfo("成功", f"热力图已导出至:\n{out}")

        ctk.CTkButton(dlg, text="  📄  文本文件 (.txt)", command=lambda: do_export('txt'), 
                      fg_color=("#F3F4F6", "#374151"), text_color=("#374151", "#E5E7EB"), hover_color=("#E5E7EB", "#4B5563"), border_color=("#D1D5DB", "#475569"), **btn_kwargs).pack(fill=tk.X, padx=25, pady=4)
        ctk.CTkButton(dlg, text="  🏷  TextGrid 标注文件 (.TextGrid)", command=lambda: do_export('textgrid'), 
                      fg_color=("#F3E8FF", "#3B0764"), text_color=("#6B21A8", "#E9D5FF"), hover_color=("#E9D5FF", "#5B21B6"), border_color=("#C084FC", "#A855F7"), **btn_kwargs).pack(fill=tk.X, padx=25, pady=4)
        ctk.CTkButton(dlg, text="  📊  Excel 表格 (.xlsx)", command=lambda: do_export('xlsx'), 
                      fg_color=("#ECFDF5", "#022C22"), text_color=("#047857", "#D1FAE5"), hover_color=("#D1FAE5", "#065F46"), border_color=("#34D399", "#10B981"), **btn_kwargs).pack(fill=tk.X, padx=25, pady=4)
        ctk.CTkButton(dlg, text="  📈  声学图表可视化导出", command=lambda: do_export('line_chart'), 
                      fg_color=("#EFF6FF", "#172554"), text_color=("#1E40AF", "#DBEAFE"), hover_color=("#DBEAFE", "#1E40AF"), border_color=("#60A5FA", "#3B82F6"), **btn_kwargs).pack(fill=tk.X, padx=25, pady=4)
        # 时序密度热力图已整合至声学图表导出中

    def _ensure_item_loaded(self, item):
        """确保 item.snd 和 item.pitch / item.pitch_data 已正确加载或计算"""
        if not item or not item.get('path'): return

        has_snd = item.get('snd') is not None
        has_pitch = (item.get('pitch') is not None) or (item.get('pitch_data') is not None)

        if not has_snd or not has_pitch:
            try:
                if not has_snd:
                    item['snd'] = parselmouth.Sound(item['path'])
                if not has_pitch:
                    from .audio_core import extract_f0
                    pf = item.get('pitch_floor', self.app_state_params.get('pitch_floor', 75))
                    pc = item.get('pitch_ceiling', self.app_state_params.get('pitch_ceiling', 600))
                    vt = item.get('voicing_threshold', self.app_state_params.get('voicing_threshold', 0.25))
                    engine = item.get('f0_engine', self.app_state_params.get('f0_engine', 'praat'))

                    if engine == 'praat':
                        item['pitch'] = item['snd'].to_pitch_ac(time_step=None, pitch_floor=pf, pitch_ceiling=pc, voicing_threshold=vt, very_accurate=True, octave_jump_cost=0.9)
                    else:
                        item['pitch_data'] = extract_f0(item['snd'], {'f0_engine': engine, 'pitch_floor': pf, 'pitch_ceiling': pc, 'voicing_threshold': vt})
            except Exception as e:
                logger.error(f"Error lazy loading sound/pitch for {item.get('path')}: {e}", exc_info=True)

    def _extract_syl_data(self, item, num_points):
        """提取项目中每个字的真实发音段(收缩后)的 11 点 F0 数据和时长。返回 (总时长, [(字时长, [F0数组]), ...])"""
        if item.get('start') is None or not item.get('snd') or (not item.get('pitch') and not item.get('pitch_data')): return 0, []
        t_s, t_e = item['start'], item['end']
        if t_e <= t_s: return 0, []

        label = item.get('label', '')
        inner_splits = item.get('inner_splits', [])
        if item.get('pitch_data'):
            p_xs = item['pitch_data']['xs']
            p_freqs = item['pitch_data']['freqs']
        else:
            pitch = item['pitch']
            p_xs = pitch.xs()
            p_freqs = pitch.selected_array['frequency']

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

        syl_data = []
        for c_s, c_e in bounds:
            if c_e <= c_s:
                syl_data.append((0.0, [0.0]*num_points))
                continue

            # 智能收缩！找到真正发声的核（有效基频段）
            valid_idx = np.where((p_xs >= c_s) & (p_xs <= c_e) & (p_freqs > 0))[0]
            if len(valid_idx) >= 2:
                v_s, v_e = p_xs[valid_idx[0]], p_xs[valid_idx[-1]]
                seg_xs = p_xs[valid_idx]     # 取出该字的真实时间轴
                seg_ys = p_freqs[valid_idx]  # 取出该字的真实F0值
            else:
                syl_data.append((0.0, [0.0]*num_points))
                continue

            dur = v_e - v_s
            if dur <= 0:
                syl_data.append((0.0, [0.0]*num_points))
                continue

            times = np.linspace(v_s, v_e, num_points)
            # 修复点：改用 numpy 局部插值，杜绝抓取界外的清辅音假象
            if len(seg_xs) >= 2:
                f0s = np.interp(times, seg_xs, seg_ys).tolist()
                # 修正：跨越静音区（>25ms）时强制归零，避免产生假数据桥接
                for j, t in enumerate(times):
                    if np.min(np.abs(seg_xs - t)) > 0.025:
                        f0s[j] = 0.0
                syl_data.append((dur, f0s))
            else:
                syl_data.append((dur, [0.0]*num_points))

        return t_e - t_s, syl_data

    def _get_pitch_arrays_for_item(self, item):
        if item.get('pitch_data'):
            p_xs = item['pitch_data'].get('xs')
            p_freqs = item['pitch_data'].get('freqs')
            if p_xs is None or p_freqs is None:
                return None, None
            return np.asarray(p_xs), np.asarray(p_freqs)
        if item.get('pitch'):
            pitch = item['pitch']
            try:
                p_xs = np.asarray(pitch.xs())
                p_freqs = np.asarray(pitch.selected_array['frequency'])
            except (TypeError, KeyError, AttributeError):
                return None, None
            if p_xs.ndim != 1 or p_freqs.ndim != 1 or len(p_xs) != len(p_freqs):
                return None, None
            return p_xs, p_freqs
        return None, None

    def _write_raw_pitch_sheet(self, workbook, rows, include_speaker=False):
        """写入逐点 Hz 原始基频数据。这里保留 pitch_data 当前状态，包括橡皮擦置零后的点。"""
        ws_raw = workbook.add_worksheet("原始基频数据")
        headers = []
        if include_speaker:
            headers.append("发音人")
        headers.extend([
            "组别", "编号", "词语", "字序", "字",
            "绝对时间(s)", "字内相对时间(s)", "基频Hz", "状态"
        ])
        for col, header in enumerate(headers):
            ws_raw.write(0, col, header)

        row_idx = 1
        for entry in rows:
            item = entry.get('item') or entry.get('raw_item')
            if not item:
                continue
            self._ensure_item_loaded(item)
            if item.get('start') is None or not item.get('snd') or (not item.get('pitch') and not item.get('pitch_data')):
                continue

            p_xs, p_freqs = self._get_pitch_arrays_for_item(item)
            if p_xs is None or p_freqs is None:
                continue

            syls, bounds = self._get_syllables_and_bounds(item)
            if not bounds:
                continue

            for syl_idx, (c_s, c_e) in enumerate(bounds, start=1):
                if c_e <= c_s:
                    continue
                char = syls[syl_idx - 1] if syl_idx - 1 < len(syls) else ""
                mask = (p_xs >= c_s) & (p_xs <= c_e)
                indices = np.where(mask)[0]
                for p_idx in indices:
                    t = float(p_xs[p_idx])
                    hz = float(p_freqs[p_idx]) if np.isfinite(p_freqs[p_idx]) else 0.0
                    status = "有效" if hz > 0 else "无声/已擦除"

                    values = []
                    if include_speaker:
                        values.append(entry.get('speaker', ''))
                    values.extend([
                        entry.get('group', ''),
                        entry.get('index', ''),
                        item.get('label', ''),
                        syl_idx,
                        char,
                        round(t, 6),
                        round(t - c_s, 6),
                        round(hz, 6) if hz > 0 else 0.0,
                        status
                    ])
                    for col, val in enumerate(values):
                        ws_raw.write(row_idx, col, val)
                    row_idx += 1

        ws_raw.freeze_panes(1, 0)
        ws_raw.autofilter(0, 0, max(row_idx - 1, 1), len(headers) - 1)
        return ws_raw

    def _get_syllables_and_bounds(self, item):
        """返回与当前编辑边界一致的子段列表，优先使用 chars_bounds。"""
        t_s, t_e = item.get('start'), item.get('end')
        if t_s is None or t_e is None or t_e <= t_s:
            return [], []

        label = item.get('label', '')
        syls = split_into_syllables(label)
        if not syls and label:
            syls = [label]

        chars_bounds = item.get('chars_bounds', [])
        if chars_bounds and len(chars_bounds) == len(syls):
            return syls, [[float(s), float(e)] for s, e in chars_bounds]

        inner_splits = item.get('inner_splits', [])
        splits = [t_s] + [s for s in inner_splits if t_s < s < t_e] + [t_e]
        if len(syls) > 1 and len(splits) != len(syls) + 1:
            splits = np.linspace(t_s, t_e, len(syls) + 1).tolist()
        elif len(syls) <= 1:
            splits = [t_s, t_e]
            if not syls:
                syls = [label]

        return syls, [[splits[i], splits[i + 1]] for i in range(len(splits) - 1)]

    def _extract_kde_contour(self, p_xs, p_freqs, c_s, c_e, n_dense):
        """提取 KDE 用的连续 F0 轮廓；橡皮擦/无声缺口保留为 NaN，后续绘制会跳过。"""
        if c_e <= c_s:
            return None

        valid_idx = np.where((p_xs >= c_s) & (p_xs <= c_e) & np.isfinite(p_freqs) & (p_freqs > 0))[0]
        if len(valid_idx) < 3:
            return None

        seg_xs = np.asarray(p_xs[valid_idx], dtype=float)
        seg_ys = np.asarray(p_freqs[valid_idx], dtype=float)
        order = np.argsort(seg_xs)
        seg_xs = seg_xs[order]
        seg_ys = seg_ys[order]

        v_s, v_e = seg_xs[0], seg_xs[-1]
        if v_e <= v_s:
            return None

        gap_threshold = 0.025
        smoothed = seg_ys.copy()
        try:
            from scipy.signal import savgol_filter
            breaks = np.where(np.diff(seg_xs) > gap_threshold)[0] + 1
            run_ranges = np.split(np.arange(len(seg_xs)), breaks)
            for run in run_ranges:
                run_len = len(run)
                if run_len < 5:
                    continue
                win = min(9, run_len if run_len % 2 == 1 else run_len - 1)
                if win >= 5:
                    smoothed[run] = savgol_filter(seg_ys[run], win, 2)
        except Exception:
            pass

        dense_times = np.linspace(v_s, v_e, n_dense)
        y_dense = np.interp(dense_times, seg_xs, smoothed)

        nearest_right = np.searchsorted(seg_xs, dense_times, side='left')
        nearest_left = np.clip(nearest_right - 1, 0, len(seg_xs) - 1)
        nearest_right = np.clip(nearest_right, 0, len(seg_xs) - 1)
        nearest_dist = np.minimum(np.abs(dense_times - seg_xs[nearest_left]), np.abs(dense_times - seg_xs[nearest_right]))
        y_dense[nearest_dist > gap_threshold] = np.nan
        return y_dense

    def _export_xlsx(self, out_file, include_chart=False, tree_structure=None):
        try:
            import xlsxwriter
        except ImportError:
            messagebox.showerror("错误", "缺少 xlsxwriter 库，请先安装：pip install xlsxwriter")
            return

        is_continuous = (self.num_rule_var.get() == "continuous")
        num_points = self.app_state_params['pts']
        if tree_structure is None: tree_structure = self._get_all_items_by_group()

        max_syls = 1
        for grp_name, children in tree_structure:
            for child in children:
                lbl = self.items[child].get('label', '')
                if len(lbl) > max_syls: max_syls = len(lbl)

        workbook = xlsxwriter.Workbook(out_file)
        ws_data = workbook.add_worksheet("数据")
        ws_res = workbook.add_worksheet("分析结果")
        raw_pitch_rows = []

        headers = ["组别", "编号", "词语", "总时长(s)"]
        for k in range(1, max_syls + 1):
            headers.append(f"字{k}_时长(s)")
            for i in range(1, num_points + 1):
                headers.append(f"字{k}_T{i}(Hz)")
        for col, header in enumerate(headers): ws_data.write(0, col, header)

        global_idx = 1
        row_idx = 1

        dict_data = {}

        for grp_name, children in tree_structure:
            if not is_continuous: global_idx = 1
            for child in children:
                item = self.items[child]
                self._ensure_item_loaded(item)
                if not item.get('snd') or (not item.get('pitch') and not item.get('pitch_data')):
                    continue

                total_dur, syl_data = self._extract_syl_data(item, num_points)
                if total_dur <= 0: continue
                raw_pitch_rows.append({
                    'group': grp_name,
                    'index': global_idx,
                    'item': item
                })

                row = [grp_name, global_idx, item['label'], float(f"{total_dur:.6f}")]

                if grp_name not in dict_data:
                    dict_data[grp_name] = {
                        'syl_dur_sums': [0.0]*max_syls, 'syl_counts': [0]*max_syls,
                        'f0_sums': [[0.0]*num_points for _ in range(max_syls)],
                        'f0_counts': [[0]*num_points for _ in range(max_syls)]
                    }

                for k in range(max_syls):
                    if k < len(syl_data):
                        dur, f0s = syl_data[k]
                        row.append(float(f"{dur:.6f}"))
                        dict_data[grp_name]['syl_dur_sums'][k] += dur
                        dict_data[grp_name]['syl_counts'][k] += 1
                        for i, f0 in enumerate(f0s):
                            if not np.isnan(f0) and f0 > 0:
                                row.append(float(f"{f0:.6f}"))
                                dict_data[grp_name]['f0_sums'][k][i] += f0
                                dict_data[grp_name]['f0_counts'][k][i] += 1
                            else:
                                row.append("")
                    else:
                        row.append("")
                        for _ in range(num_points): row.append("")

                for col, val in enumerate(row):
                    ws_data.write(row_idx, col, val)

                row_idx += 1
                global_idx += 1

        self._write_raw_pitch_sheet(workbook, raw_pitch_rows, include_speaker=False)

        all_avg_hz = []
        avg_points_map = {}

        for grp, st in dict_data.items():
            avg_points_map[grp] = []
            for k in range(max_syls):
                syl_avgs = []
                for i in range(num_points):
                    cnt = st['f0_counts'][k][i]
                    avg_hz = st['f0_sums'][k][i] / cnt if cnt > 0 else 0
                    syl_avgs.append(avg_hz)
                    if avg_hz > 0: all_avg_hz.append(avg_hz)
                avg_points_map[grp].append(syl_avgs)

        if not all_avg_hz:
            workbook.close()
            return

        min_hz, max_hz = min(all_avg_hz), max(all_avg_hz)

        # 写入分析结果 Sheet（全部使用 Excel 公式引用数据表）
        group_list = list(dict_data.keys())
        last_data_row = row_idx - 1  # 0-indexed
        res_row, _, _ = write_analysis_sheet_with_formulas(
            workbook, ws_res, group_list, num_points, max_syls, last_data_row
        )

        if include_chart:
            try:
                build_five_point_chart(
                    workbook, ws_res, dict_data, avg_points_map,
                    num_points, max_syls, min_hz, max_hz,
                    insert_cell=f'A{res_row + 3}',
                    chart_title='各声调平均基频五度标调图（保留真实时长）'
                )
            except Exception as chart_err:
                logger.error(f"Error generating Excel chart: {chart_err}", exc_info=True)

        workbook.close()

    def _export_integrated(self, out_file, format_mode, include_chart, all_speakers):
        try: import xlsxwriter
        except ImportError:
            if format_mode == 'xlsx': return messagebox.showerror("错误", "缺少 xlsxwriter 库，请先安装：pip install xlsxwriter")
        is_continuous = (self.num_rule_var.get() == "continuous")
        num_points = self.app_state_params['pts']
        speaker_stats = {}
        speaker_rows = {}
        max_syls = 1
        for speaker in all_speakers:
            s_struct = self._get_items_by_group_for_dict(speaker.items)
            rows = []
            f0_values = []
            orig_items = self.items
            self.items = speaker.items
            for grp_name, children in s_struct:
                for child in children:
                    item = self.items[child]
                    lbl = item.get('label', '')
                    if len(lbl) > max_syls: max_syls = len(lbl)
                    self._ensure_item_loaded(item)
                    if not item.get('snd') or (not item.get('pitch') and not item.get('pitch_data')):
                        continue
                    total_dur, syl_data = self._extract_syl_data(item, num_points)
                    if total_dur <= 0: continue
                    rows.append({'group': grp_name, 'label': lbl, 'total_dur': total_dur, 'syl_data': syl_data, 'raw_item': item})
                    for _, freqs in syl_data:
                        for f in freqs:
                            if f > 0: f0_values.append(f)
            self.items = orig_items
            if f0_values:
                import numpy as np
                speaker_stats[speaker.id] = (np.min(f0_values), np.max(f0_values))
            else: speaker_stats[speaker.id] = (0, 0)
            speaker_rows[speaker.id] = rows

        if format_mode == 'xlsx':
            workbook = xlsxwriter.Workbook(out_file)
            ws_data = workbook.add_worksheet("整合数据(T值)")
            ws_res = workbook.add_worksheet("分析结果")
            headers = ["发音人", "组别", "编号", "词语", "总时长(s)"]
            for k in range(1, max_syls + 1):
                headers.append(f"字{k}_时长(s)")
                for i in range(1, num_points + 1): headers.append(f"字{k}_T{i}")
            for col, header in enumerate(headers): ws_data.write(0, col, header)

            group_stats = {}
            raw_pitch_rows = []
            row_idx = 1
            for speaker in all_speakers:
                rows = speaker_rows.get(speaker.id, [])
                s_min, s_max = speaker_stats.get(speaker.id, (0, 0))
                diff = s_max - s_min if s_max > s_min else 1.0
                global_idx = 1
                for r in rows:
                    raw_pitch_rows.append({
                        'speaker': speaker.name,
                        'group': r['group'],
                        'index': global_idx,
                        'item': r['raw_item']
                    })
                    ws_data.write(row_idx, 0, speaker.name)
                    ws_data.write(row_idx, 1, r['group'])
                    ws_data.write(row_idx, 2, global_idx)
                    ws_data.write(row_idx, 3, r['label'])
                    ws_data.write(row_idx, 4, round(r['total_dur'], 4))
                    col_idx = 5

                    grp_name = r['group']
                    if grp_name not in group_stats:
                        group_stats[grp_name] = {
                            'syl_dur_sums': [0.0] * max_syls,
                            'syl_counts': [0] * max_syls,
                            't_sums': [[0.0] * num_points for _ in range(max_syls)],
                            't_counts': [[0] * num_points for _ in range(max_syls)]
                        }
                    stats = group_stats[grp_name]

                    for k, (s_dur, freqs) in enumerate(r['syl_data']):
                        ws_data.write(row_idx, col_idx, round(s_dur, 4))
                        col_idx += 1
                        if k < max_syls:
                            stats['syl_dur_sums'][k] += s_dur
                            stats['syl_counts'][k] += 1
                        for i, f in enumerate(freqs):
                            t_val = round(((f - s_min) / diff) * 5 if f > 0 else 0.0, 2)
                            ws_data.write(row_idx, col_idx, t_val)
                            col_idx += 1
                            if k < max_syls and f > 0:
                                stats['t_sums'][k][i] += t_val
                                stats['t_counts'][k][i] += 1

                    fill_count = max_syls - len(r['syl_data'])
                    for _ in range(fill_count):
                        ws_data.write(row_idx, col_idx, 0.0)
                        col_idx += 1
                        for _ in range(num_points):
                            ws_data.write(row_idx, col_idx, 0.0)
                            col_idx += 1
                    global_idx += 1
                    row_idx += 1

            res_headers = ["声调类型"]
            for k in range(1, max_syls + 1):
                res_headers.append(f"字{k}_平均时长")
                for i in range(1, num_points + 1): res_headers.append(f"字{k}_T{i}")
            for col, header in enumerate(res_headers): ws_res.write(0, col, header)

            res_row = 1
            for grp, st in group_stats.items():
                ws_res.write(res_row, 0, grp)
                col = 1
                for k in range(max_syls):
                    cnt = st['syl_counts'][k]
                    avg_dur = st['syl_dur_sums'][k] / cnt if cnt > 0 else 0
                    ws_res.write(res_row, col, round(avg_dur, 4))
                    col += 1
                    for i in range(num_points):
                        t_cnt = st['t_counts'][k][i]
                        if t_cnt > 0:
                            ws_res.write(res_row, col, round(st['t_sums'][k][i] / t_cnt, 2))
                        else:
                            ws_res.write(res_row, col, "")
                        col += 1
                res_row += 1

            self._write_raw_pitch_sheet(workbook, raw_pitch_rows, include_speaker=True)

            if include_chart and group_stats:
                try:
                    # 将 group_stats 的 t_sums/t_counts 转换为 avg_points_map 格式（Hz 平均值）
                    # 注意：整合模式下 t_sums/t_counts 里存的已经是 T 值而非 Hz，
                    # 但 build_five_point_chart 需要 Hz 形式的 avg_points_map 和 min_hz/max_hz。
                    # 这里直接复用 speaker_stats 中各发音人的原始 Hz 汇总来构建。
                    # 由于整合模式比较特殊（跨发音人归一化），
                    # 此处使用独立的折线图保持兼容。
                    ws_chart_data = workbook.add_worksheet("图表数据")
                    ws_chart_data.hide()
                    ws_chart_data.write(0, 0, "声调类型")
                    for p in range(1, max_syls * num_points + 1):
                        ws_chart_data.write(0, p, p)
                    chart_row = 1
                    for grp, st in group_stats.items():
                        ws_chart_data.write(chart_row, 0, grp)
                        col_idx = 1
                        for k in range(max_syls):
                            for i in range(num_points):
                                t_cnt = st['t_counts'][k][i]
                                if t_cnt > 0:
                                    ws_chart_data.write(chart_row, col_idx, round(st['t_sums'][k][i] / t_cnt, 2))
                                else:
                                    ws_chart_data.write(chart_row, col_idx, "")
                                col_idx += 1
                        chart_row += 1
                    chart = workbook.add_chart({'type': 'scatter', 'subtype': 'straight_with_markers'})
                    for r in range(1, len(group_stats) + 1):
                        chart.add_series({
                            'name':       ['图表数据', r, 0],
                            'categories': ['图表数据', 0, 1, 0, max_syls * num_points],
                            'values':     ['图表数据', r, 1, r, max_syls * num_points],
                            'line':       {'width': 2.5},
                            'marker':     {'type': 'circle', 'size': 6},
                        })
                    chart.set_title({
                        'name': '多发音人整合声调格局图',
                        'name_font': {'name': 'Microsoft YaHei', 'size': 14, 'bold': True}
                    })
                    chart.set_x_axis({
                        'name': '测量点 (时序展开)',
                        'name_font': {'name': 'Microsoft YaHei', 'size': 10},
                        'num_font': {'name': 'Arial', 'size': 9}
                    })
                    chart.set_y_axis({
                        'name': '赵元任五度标调法',
                        'name_font': {'name': 'Microsoft YaHei', 'size': 10},
                        'num_font': {'name': 'Arial', 'size': 1, 'color': 'white'},
                        'min': 0, 'max': 5,
                        'major_unit': 1,
                        'major_gridlines': {'visible': True, 'line': {'color': '#D0D0D0', 'width': 0.5}},
                        'major_tick_mark': 'none',
                    })
                    chart.set_legend({
                        'position': 'right',
                        'font': {'name': 'Microsoft YaHei', 'size': 9}
                    })
                    chart.set_size({'width': 650, 'height': 450})
                    ws_res.insert_chart(f'A{res_row + 3}', chart)
                except Exception as chart_err:
                    logger.error(f"Error generating integrated Excel chart: {chart_err}", exc_info=True)
            workbook.close()
        elif format_mode == 'txt':
            with open(out_file, 'w', encoding='utf-8') as f_out:
                headers = ["发音人", "组别", "编号", "词语", "总时长(s)"]
                for k in range(1, max_syls + 1):
                    headers.append(f"字{k}_时长(s)")
                    for i in range(1, num_points + 1): headers.append(f"字{k}_T{i}")
                f_out.write("\\t".join(headers) + "\\n")
                for speaker in all_speakers:
                    rows = speaker_rows.get(speaker.id, [])
                    s_min, s_max = speaker_stats.get(speaker.id, (0, 0))
                    diff = s_max - s_min if s_max > s_min else 1.0
                    global_idx = 1
                    for r in rows:
                        line_parts = [speaker.name, r['group'], str(global_idx), r['label'], f"{r['total_dur']:.4f}"]
                        for s_dur, freqs in r['syl_data']:
                            line_parts.append(f"{s_dur:.4f}")
                            for f in freqs: line_parts.append(f"{((f - s_min) / diff) * 5 if f > 0 else 0.0:.2f}")
                        fill_count = max_syls - len(r['syl_data'])
                        for _ in range(fill_count):
                            line_parts.append("0.0000")
                            for _ in range(num_points): line_parts.append("0.00")
                        f_out.write("\\t".join(line_parts) + "\\n")
                        global_idx += 1

    def _export_txt(self, out_file, tree_structure=None):
        is_continuous = (self.num_rule_var.get() == "continuous")
        if tree_structure is None: tree_structure = self._get_all_items_by_group()

        with open(out_file, "w", encoding="utf-8") as f:
            global_idx = 1
            for grp_name, children in tree_structure:
                if not is_continuous: global_idx = 1
                f.write(f"{grp_name}\n")
                for child in children:
                    item = self.items[child]
                    if item['start'] is not None:
                        txt_data = get_export_text_for_item(item, global_idx, self.app_state_params['pts'], pitch_floor=self.app_state_params.get('pitch_floor', 75.0), pitch_ceiling=self.app_state_params.get('pitch_ceiling', 600.0), voicing_threshold=self.app_state_params.get('voicing_threshold', 0.25))
                        f.write(txt_data)
                        global_idx += 1

    def _collect_group_avg_data(self, tree_structure=None):
        num_points = self.app_state_params['pts']
        if tree_structure is None: tree_structure = self._get_all_items_by_group()

        max_syls = 1
        dict_data = {}
        for grp_name, children in tree_structure:
            for child in children:
                lbl = self.items[child].get('label', '')
                if len(lbl) > max_syls: max_syls = len(lbl)
                item = self.items[child]
                self._ensure_item_loaded(item)
                if not item.get('snd') or (not item.get('pitch') and not item.get('pitch_data')):
                    continue
                total_dur, syl_data = self._extract_syl_data(item, num_points)
                if total_dur <= 0: continue

                if grp_name not in dict_data:
                    dict_data[grp_name] = { 'f0_sums': [[0.0]*num_points for _ in range(20)], 'f0_counts': [[0]*num_points for _ in range(20)] }
                for k, (dur, f0s) in enumerate(syl_data):
                    for i, f0 in enumerate(f0s):
                        if not np.isnan(f0) and f0 > 0:
                            dict_data[grp_name]['f0_sums'][k][i] += f0
                            dict_data[grp_name]['f0_counts'][k][i] += 1

        all_avg_hz = []
        avg_points_map = {}
        for grp, st in dict_data.items():
            avg_points_map[grp] = []
            for k in range(max_syls):
                syl_avgs = []
                for i in range(num_points):
                    cnt = st['f0_counts'][k][i]
                    hz = st['f0_sums'][k][i] / cnt if cnt > 0 else 0
                    syl_avgs.append(hz)
                    if hz > 0: all_avg_hz.append(hz)
                avg_points_map[grp].append(syl_avgs)

        if not all_avg_hz: return None, 1
        min_hz, max_hz = min(all_avg_hz), max(all_avg_hz)

        result = {}
        for grp, syl_avgs_list in avg_points_map.items():
            flat_t_vals = []
            for syl_avgs in syl_avgs_list:
                for h in syl_avgs:
                    if h > 0 and max_hz > min_hz and min_hz > 0:
                        flat_t_vals.append(5 * (math.log10(h) - math.log10(min_hz)) / (math.log10(max_hz) - math.log10(min_hz)))
                    else: flat_t_vals.append(None)
            result[grp] = flat_t_vals

        return result, max_syls

    def _export_line_chart(self, out_file, tree_structure=None):
        data, max_syls = self._collect_group_avg_data(tree_structure=tree_structure)
        if not data: return messagebox.showwarning("提示", "没有有效数据可供绘图。")
        self._draw_line_chart(data, max_syls, out_file)

    def _draw_line_chart(self, data, max_syls, out_file):
        num_points = self.app_state_params['pts']
        plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'Arial Unicode MS']
        plt.rcParams['axes.unicode_minus'] = False

        fig, ax = plt.subplots(figsize=(6 + 4 * max_syls, 6))
        total_points = max_syls * num_points
        x_vals = list(range(1, total_points + 1))

        colors = ['#2563EB', '#DC2626', '#16A34A', '#9333EA', '#EA580C', '#0891B2', '#CA8A04', '#6366F1']

        for i, (name, t_vals) in enumerate(data.items()):
            valid_x = [x for x, v in zip(x_vals, t_vals) if v is not None]
            valid_y = [v for v in t_vals if v is not None]
            if valid_x:
                ax.plot(valid_x, valid_y, '-o', color=colors[i % len(colors)], linewidth=2, markersize=5, label=name)

        ax.set_ylim(0, 5)
        ax.set_xlim(0.5, total_points + 0.5)
        ax.set_yticks([0, 0.5, 1, 1.5, 2, 2.5, 3, 3.5, 4, 4.5, 5])

        ax.set_xticks(range(1, total_points + 1))
        ax.set_xticklabels([(idx % num_points) + 1 for idx in range(total_points)])

        for k in range(1, max_syls):
            div_x = k * num_points + 0.5
            ax.axvline(div_x, color='gray', linestyle='--', alpha=0.5)
            ax.text(div_x - num_points/2, 5.1, f"第 {k} 字", ha='center', va='bottom', fontsize=12, fontweight='bold', color='#4B5563')
            if k == max_syls - 1:
                ax.text(div_x + num_points/2, 5.1, f"第 {k+1} 字", ha='center', va='bottom', fontsize=12, fontweight='bold', color='#4B5563')

        ax.set_xlabel('测量点 (沿时序展开)', fontsize=12)
        ax.set_ylabel('T 值 (0-5 标度)', fontsize=12)
        ax.set_title('连读变调声调格局图', fontsize=16, fontweight='bold', pad=25)
        ax.legend(loc='best', fontsize=10)
        ax.grid(True, alpha=0.3)

        fig.tight_layout()
        fig.savefig(out_file, dpi=300, bbox_inches='tight')
        plt.close(fig)

    def _export_line_chart_integrated(self, out_file, all_speakers):
        num_points = self.app_state_params['pts']
        max_syls = 1
        aggregated_t_sums = {}
        aggregated_t_counts = {}

        for speaker in all_speakers:
            s_struct = self._get_items_by_group_for_dict(speaker.items)
            orig_items = self.items
            self.items = speaker.items

            dict_data = {}
            for grp_name, children in s_struct:
                for child in children:
                    item = self.items[child]
                    lbl = item.get('label', '')
                    if len(lbl) > max_syls: max_syls = len(lbl)
                    self._ensure_item_loaded(item)
                    if not item.get('snd') or (not item.get('pitch') and not item.get('pitch_data')):
                        continue
                    total_dur, syl_data = self._extract_syl_data(item, num_points)
                    if total_dur <= 0: continue

                    if grp_name not in dict_data:
                        dict_data[grp_name] = { 'f0_sums': [[0.0]*num_points for _ in range(20)], 'f0_counts': [[0]*num_points for _ in range(20)] }
                    for k, (dur, f0s) in enumerate(syl_data):
                        for i, f0 in enumerate(f0s):
                            if not np.isnan(f0) and f0 > 0:
                                dict_data[grp_name]['f0_sums'][k][i] += f0
                                dict_data[grp_name]['f0_counts'][k][i] += 1

            all_hz = []
            avg_points_map = {}
            for grp, st in dict_data.items():
                avg_points_map[grp] = []
                for k in range(max_syls):
                    syl_avgs = []
                    for i in range(num_points):
                        cnt = st['f0_counts'][k][i]
                        hz = st['f0_sums'][k][i] / cnt if cnt > 0 else 0
                        syl_avgs.append(hz)
                        if hz > 0: all_hz.append(hz)
                    avg_points_map[grp].append(syl_avgs)

            if all_hz:
                min_hz, max_hz = min(all_hz), max(all_hz)
                for grp, syl_avgs_list in avg_points_map.items():
                    if grp not in aggregated_t_sums:
                        aggregated_t_sums[grp] = [[0.0]*num_points for _ in range(max_syls)]
                        aggregated_t_counts[grp] = [[0]*num_points for _ in range(max_syls)]
                    for k, syl_avgs in enumerate(syl_avgs_list):
                        for i, h in enumerate(syl_avgs):
                            if h > 0 and max_hz > min_hz and min_hz > 0:
                                t_val = 5 * (math.log10(h) - math.log10(min_hz)) / (math.log10(max_hz) - math.log10(min_hz))
                                aggregated_t_sums[grp][k][i] += t_val
                                aggregated_t_counts[grp][k][i] += 1

            self.items = orig_items

        data = {}
        for grp in aggregated_t_sums:
            flat_t_vals = []
            for k in range(max_syls):
                for i in range(num_points):
                    cnt = aggregated_t_counts[grp][k][i]
                    if cnt > 0:
                        flat_t_vals.append(aggregated_t_sums[grp][k][i] / cnt)
                    else:
                        flat_t_vals.append(None)
            data[grp] = flat_t_vals

        if not data: return messagebox.showwarning("提示", "没有有效数据可供绘图。")
        self._draw_line_chart(data, max_syls, out_file)

    def _show_kde_params_dialog(self, mode='single', tree_structure=None, all_speakers=None):
        param_dlg = ctk.CTkToplevel(self.parent)
        param_dlg.title("时序密度图参数设置")
        param_dlg.geometry("450x380")
        param_dlg.resizable(False, False)
        param_dlg.transient(self.parent)
        param_dlg.grab_set()

        param_dlg.update_idletasks()
        main_win = self.parent.winfo_toplevel()
        x = main_win.winfo_rootx() + (main_win.winfo_width() - 450) // 2
        y = main_win.winfo_rooty() + (main_win.winfo_height() - 380) // 2
        param_dlg.geometry(f"+{x}+{y}")

        ctk.CTkLabel(param_dlg, text="词语时序密度热力图参数设置", font=self.font_title).pack(pady=(15, 10))

        bw_frame = ctk.CTkFrame(param_dlg, fg_color="transparent")
        bw_frame.pack(fill=tk.X, padx=30, pady=5)

        ctk.CTkLabel(bw_frame, text="核密度带宽 (Bandwidth):", font=self.font_main).pack(side=tk.LEFT)

        bw_val_lbl = ctk.CTkLabel(bw_frame, text="0.15", font=self.font_main, width=40)

        def on_slider_change(val):
            bw_val_lbl.configure(text=f"{float(val):.2f}")

        bw_slider = ctk.CTkSlider(bw_frame, from_=0.05, to=0.50, number_of_steps=45, command=on_slider_change)
        bw_slider.set(0.15)
        bw_slider.pack(side=tk.RIGHT, padx=(10, 0))
        bw_val_lbl.pack(side=tk.RIGHT)

        f0_frame = ctk.CTkFrame(param_dlg, fg_color="transparent")
        f0_frame.pack(fill=tk.X, padx=30, pady=10)

        ctk.CTkLabel(f0_frame, text="基频 T值归一化范围:", font=self.font_main).pack(anchor="w", pady=(0, 5))

        f0_mode_var = ctk.StringVar(value="percentile")

        pct_frame = ctk.CTkFrame(param_dlg, fg_color="transparent")
        manual_frame = ctk.CTkFrame(param_dlg, fg_color="transparent")

        def update_f0_mode_ui():
            val = f0_mode_var.get()
            if val == "percentile":
                pct_frame.pack(fill=tk.X, padx=50, pady=2)
                manual_frame.pack_forget()
            elif val == "manual":
                manual_frame.pack(fill=tk.X, padx=50, pady=2)
                pct_frame.pack_forget()
            else:
                pct_frame.pack_forget()
                manual_frame.pack_forget()

        r_pct = ctk.CTkRadioButton(f0_frame, text="分位数自动截断 (推荐, 消除极端值压缩)", variable=f0_mode_var, value="percentile", font=self.font_main, command=update_f0_mode_ui)
        r_pct.pack(anchor="w", pady=2)

        r_minmax = ctk.CTkRadioButton(f0_frame, text="极值自动范围 (Min ~ Max)", variable=f0_mode_var, value="minmax", font=self.font_main, command=update_f0_mode_ui)
        r_minmax.pack(anchor="w", pady=2)

        r_manual = ctk.CTkRadioButton(f0_frame, text="手动指定范围 (Hz)", variable=f0_mode_var, value="manual", font=self.font_main, command=update_f0_mode_ui)
        r_manual.pack(anchor="w", pady=2)

        ctk.CTkLabel(pct_frame, text="分位区间 (Low % ~ High %):", font=self.font_main).pack(side=tk.LEFT)
        pct_low_ent = ctk.CTkEntry(pct_frame, width=50, font=self.font_main)
        pct_low_ent.insert(0, "5")
        pct_low_ent.pack(side=tk.LEFT, padx=5)
        ctk.CTkLabel(pct_frame, text="~", font=self.font_main).pack(side=tk.LEFT)
        pct_high_ent = ctk.CTkEntry(pct_frame, width=50, font=self.font_main)
        pct_high_ent.insert(0, "95")
        pct_high_ent.pack(side=tk.LEFT, padx=5)

        ctk.CTkLabel(manual_frame, text="基频范围 (Min Hz ~ Max Hz):", font=self.font_main).pack(side=tk.LEFT)
        min_hz_ent = ctk.CTkEntry(manual_frame, width=60, font=self.font_main)
        min_hz_ent.insert(0, "75")
        min_hz_ent.pack(side=tk.LEFT, padx=5)
        ctk.CTkLabel(manual_frame, text="~", font=self.font_main).pack(side=tk.LEFT)
        max_hz_ent = ctk.CTkEntry(manual_frame, width=60, font=self.font_main)
        max_hz_ent.insert(0, "600")
        max_hz_ent.pack(side=tk.LEFT, padx=5)

        update_f0_mode_ui()

        def on_confirm():
            bw = float(bw_slider.get())
            f0_mode = f0_mode_var.get()

            p_low = 5.0
            p_high = 95.0
            m_min = 75.0
            m_max = 600.0

            if f0_mode == 'percentile':
                try:
                    p_low = float(pct_low_ent.get())
                    p_high = float(pct_high_ent.get())
                    if not (0 <= p_low < p_high <= 100): raise ValueError
                except ValueError:
                    return messagebox.showerror("错误", "请输入有效的百分比 (0 到 100) 且低分位数必须小于高分位数。")
            elif f0_mode == 'manual':
                try:
                    m_min = float(min_hz_ent.get())
                    m_max = float(max_hz_ent.get())
                    if not (0 < m_min < m_max): raise ValueError
                except ValueError:
                    return messagebox.showerror("错误", "请输入有效的基频范围 (Hz)。")

            param_dlg.destroy()

            out = filedialog.askdirectory(title="选择热力图导出文件夹") if mode == 'separate' else filedialog.asksaveasfilename(title="导出热力图", defaultextension=".png", initialfile="tone_heatmap", filetypes=[("PNG 图片", "*.png")])
            if not out: return

            params = {
                'bw_method': bw,
                'f0_mode': f0_mode,
                'percentile_low': p_low,
                'percentile_high': p_high,
                'manual_min': m_min,
                'manual_max': m_max
            }

            try:
                if mode == 'single':
                    success = self._export_kde_heatmap(out, tree_structure=tree_structure, params=params)
                elif mode == 'separate':
                    import os
                    success = True
                    for s in all_speakers:
                        s_struct = self._get_items_by_group_for_dict(s.items)
                        orig_items = self.items
                        self.items = s.items
                        if os.path.isdir(out):
                            s_out = os.path.join(out, f"{s.name}.png")
                        else:
                            base, ext = os.path.splitext(out)
                            s_out = f"{base}_{s.name}{ext}"

                        ret = self._export_kde_heatmap(s_out, tree_structure=s_struct, params=params)
                        self.items = orig_items
                        if not ret: success = False
                else:
                    success = self._export_kde_heatmap_integrated(out, all_speakers, params=params)

                if success:
                    messagebox.showinfo("成功", f"热力图已导出至:\n{out}")
            except Exception as e:
                messagebox.showerror("错误", f"导出热力图失败: {e}")
                import logging
                logging.getLogger(__name__).error(f"KDE Heatmap Export error: {e}", exc_info=True)

        btn_frame = ctk.CTkFrame(param_dlg, fg_color="transparent")
        btn_frame.pack(side=tk.BOTTOM, pady=15)

        ctk.CTkButton(btn_frame, text="取消", width=90, fg_color="#E5E7EB", text_color="#374151", hover_color="#D1D5DB", command=param_dlg.destroy).pack(side=tk.LEFT, padx=10)
        ctk.CTkButton(btn_frame, text="确定并选择路径", width=120, command=on_confirm).pack(side=tk.LEFT, padx=10)

    def _draw_kde_heatmap(self, group_norm_points, max_syls, out_file, prog_dlg, pbar, lbl_status, bw_method=0.15):
        import math
        from scipy.stats import gaussian_kde
        plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'Arial Unicode MS']
        plt.rcParams['axes.unicode_minus'] = False

        groups_with_data = [g for g in group_norm_points.keys() if len(group_norm_points[g][0]) > 0]
        n_groups = len(groups_with_data)
        if n_groups == 0:
            prog_dlg.destroy()
            return messagebox.showwarning("提示", "没有有效数据可供绘制热力图。")

        n_cols = min(2, n_groups)
        n_rows = math.ceil(n_groups / n_cols)
        fig, axes = plt.subplots(n_rows, n_cols, figsize=(5 * max_syls * n_cols, 5 * n_rows), squeeze=False, sharex=True, sharey=True)
        axes_flat = axes.flatten()

        for idx, grp_name in enumerate(groups_with_data):
            lbl_status.configure(text=f"正在绘制 {grp_name} ({idx+1}/{n_groups})...")
            pbar.set(0.8 + 0.2 * (idx / n_groups))
            prog_dlg.update()

            ax = axes_flat[idx]
            X_all, Y_all = group_norm_points[grp_name]

            xmin, xmax = 0, max_syls * 100
            ymin, ymax = -1, 6

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
                import logging
                logging.getLogger(__name__).error(f"KDE drawing failed for {grp_name}: {e}")

            for k in range(1, max_syls):
                ax.axvline(k * 100, color='gray', linestyle='--', alpha=0.8)

            ax.set_title(grp_name, fontsize=16)
            ax.set_ylim(-1, 6)
            ax.set_xlim(0, max_syls * 100)
            ax.set_yticks([-1, 0, 1, 2, 3, 4, 5, 6])

            ticks, labels = [], []
            for k in range(max_syls):
                ticks.append(k * 100 + 50)
                labels.append(f"第 {k+1} 字\n(0-100%)")
            ax.set_xticks(ticks)
            ax.set_xticklabels(labels)

            if idx % n_cols == 0: ax.set_ylabel('T 值', fontsize=12)

        for idx in range(n_groups, len(axes_flat)): axes_flat[idx].set_visible(False)

        fig.suptitle('词语时序密度热力图 (连读变调)', fontsize=20, fontweight='bold', y=1.05)
        fig.tight_layout()
        fig.savefig(out_file, dpi=300, bbox_inches='tight')
        plt.close(fig)
        prog_dlg.destroy()
        return True

    def _export_kde_heatmap(self, out_file, tree_structure=None, params=None):
        import os

        if tree_structure is None:
            tree_structure = self._get_all_items_by_group()

        N_DENSE = 100
        max_syls = 1
        aggregated_syl_contours = {}

        prog_dlg = ctk.CTkToplevel(self.parent)
        prog_dlg.title("正在导出热力图")
        prog_dlg.geometry("300x120")
        prog_dlg.attributes('-topmost', True)
        prog_dlg.resizable(False, False)
        prog_dlg.update_idletasks()
        main_win = self.parent.winfo_toplevel()
        prog_dlg.geometry(f"+{main_win.winfo_rootx() + (main_win.winfo_width() - 300) // 2}+{main_win.winfo_rooty() + (main_win.winfo_height() - 120) // 2}")

        lbl_status = ctk.CTkLabel(prog_dlg, text="正在处理数据，请稍候...", font=self.font_main)
        lbl_status.pack(pady=(20, 5))
        pbar = ctk.CTkProgressBar(prog_dlg, width=250)
        pbar.pack()
        pbar.set(0)
        prog_dlg.update()

        pbar.set(0.2)
        prog_dlg.update()

        speaker_contours = {}
        for grp_name, children in tree_structure:
            speaker_contours[grp_name] = {}
            for child in children:
                item = self.items[child]
                syls, bounds = self._get_syllables_and_bounds(item)
                if syls:
                    max_syls = max(max_syls, len(syls))
                self._ensure_item_loaded(item)
                if item.get('start') is None or not item.get('snd') or (not item.get('pitch') and not item.get('pitch_data')):
                    continue

                if item.get('pitch_data'):
                    p_xs = item['pitch_data']['xs']
                    p_freqs = item['pitch_data']['freqs']
                else:
                    pitch = item['pitch']
                    p_xs = pitch.xs()
                    p_freqs = pitch.selected_array['frequency']

                for k, (c_s, c_e) in enumerate(bounds):
                    y_dense = self._extract_kde_contour(p_xs, p_freqs, c_s, c_e, N_DENSE)
                    if y_dense is None:
                        continue
                    if k not in speaker_contours[grp_name]: speaker_contours[grp_name][k] = []
                    speaker_contours[grp_name][k].append(y_dense)

        all_mean_vals = []
        for name, syls_dict in speaker_contours.items():
            for k, y_arrays in syls_dict.items():
                if y_arrays:
                    finite_vals = np.asarray(y_arrays, dtype=float).ravel()
                    finite_vals = finite_vals[np.isfinite(finite_vals)]
                    all_mean_vals.extend(finite_vals.tolist())
        if all_mean_vals:
            if params:
                f0_mode = params.get('f0_mode', 'minmax')
                p_low = params.get('percentile_low', 5.0)
                p_high = params.get('percentile_high', 95.0)
                m_min = params.get('manual_min', 75.0)
                m_max = params.get('manual_max', 600.0)
            else:
                f0_mode = 'minmax'
                p_low, p_high, m_min, m_max = 5.0, 95.0, 75.0, 600.0

            if f0_mode == 'percentile':
                min_f0 = np.percentile(all_mean_vals, p_low)
                max_f0 = np.percentile(all_mean_vals, p_high)
            elif f0_mode == 'manual':
                min_f0 = m_min
                max_f0 = m_max
            else:
                min_f0 = min(all_mean_vals)
                max_f0 = max(all_mean_vals)

            def hz_to_5_scale_s(hz):
                if max_f0 == min_f0: return 3.0
                hz_val = np.clip(hz, min_f0, max_f0) if min_f0 > 0 else hz
                if min_f0 <= 0 or max_f0 <= min_f0: return 3.0
                return 5 * (np.log(hz_val) - np.log(min_f0)) / (np.log(max_f0) - np.log(min_f0))

            for name, syls_dict in speaker_contours.items():
                if name not in aggregated_syl_contours: aggregated_syl_contours[name] = {}
                for k, y_arrays in syls_dict.items():
                    if k not in aggregated_syl_contours[name]: aggregated_syl_contours[name][k] = []
                    for y_arr in y_arrays:
                        t_arr = [hz_to_5_scale_s(h) for h in y_arr]
                        aggregated_syl_contours[name][k].append(t_arr)

        pbar.set(0.7)
        lbl_status.configure(text="正在汇总数据...")
        prog_dlg.update()

        group_norm_points = {}
        for name, syls_dict in aggregated_syl_contours.items():
            X_all, Y_all = [], []
            for k, y_arrays in syls_dict.items():
                x_dense = np.linspace(k * 100, (k + 1) * 100, N_DENSE)
                for y_arr in y_arrays:
                    y_arr = np.asarray(y_arr, dtype=float)
                    valid = np.isfinite(y_arr)
                    X_all.extend(x_dense[valid].tolist())
                    Y_all.extend(y_arr[valid].tolist())
            group_norm_points[name] = (np.array(X_all), np.array(Y_all))

        pbar.set(0.8)
        prog_dlg.update()

        bw_method = params.get('bw_method', 0.15) if params else 0.15
        self._draw_kde_heatmap(group_norm_points, max_syls, out_file, prog_dlg, pbar, lbl_status, bw_method=bw_method)
        return True

    def _export_kde_heatmap_integrated(self, out_file, all_speakers, params=None):
        N_DENSE = 100
        max_syls = 1
        aggregated_syl_contours = {}

        prog_dlg = ctk.CTkToplevel(self.parent)
        prog_dlg.title("正在导出整合热力图")
        prog_dlg.geometry("300x120")
        prog_dlg.attributes('-topmost', True)
        prog_dlg.resizable(False, False)
        prog_dlg.update_idletasks()
        main_win = self.parent.winfo_toplevel()
        prog_dlg.geometry(f"+{main_win.winfo_rootx() + (main_win.winfo_width() - 300) // 2}+{main_win.winfo_rooty() + (main_win.winfo_height() - 120) // 2}")

        lbl_status = ctk.CTkLabel(prog_dlg, text="正在处理数据，请稍候...", font=self.font_main)
        lbl_status.pack(pady=(20, 5))
        pbar = ctk.CTkProgressBar(prog_dlg, width=250)
        pbar.pack()
        pbar.set(0)
        prog_dlg.update()

        total_speakers = len(all_speakers)
        for s_idx, speaker in enumerate(all_speakers):
            lbl_status.configure(text=f"正在处理 {speaker.name} ({s_idx+1}/{total_speakers})...")
            pbar.set(0.6 * (s_idx / total_speakers))
            prog_dlg.update()

            s_struct = self._get_items_by_group_for_dict(speaker.items)
            orig_items = self.items
            self.items = speaker.items

            speaker_contours = {}
            for grp_name, children in s_struct:
                speaker_contours[grp_name] = {}
                for child in children:
                    item = self.items[child]
                    syls, bounds = self._get_syllables_and_bounds(item)
                    if syls:
                        max_syls = max(max_syls, len(syls))
                    self._ensure_item_loaded(item)
                    if item.get('start') is None or not item.get('snd') or (not item.get('pitch') and not item.get('pitch_data')):
                        continue

                    if item.get('pitch_data'):
                        p_xs = item['pitch_data']['xs']
                        p_freqs = item['pitch_data']['freqs']
                    else:
                        pitch = item['pitch']
                        p_xs = pitch.xs()
                        p_freqs = pitch.selected_array['frequency']

                    for k, (c_s, c_e) in enumerate(bounds):
                        y_dense = self._extract_kde_contour(p_xs, p_freqs, c_s, c_e, N_DENSE)
                        if y_dense is None:
                            continue
                        if k not in speaker_contours[grp_name]: speaker_contours[grp_name][k] = []
                        speaker_contours[grp_name][k].append(y_dense)

            for name, syls_dict in speaker_contours.items():
                if name not in aggregated_syl_contours: aggregated_syl_contours[name] = {}
                for k, y_arrays in syls_dict.items():
                    if k not in aggregated_syl_contours[name]: aggregated_syl_contours[name][k] = []
                    aggregated_syl_contours[name][k].extend(y_arrays)
            self.items = orig_items

        pbar.set(0.7)
        lbl_status.configure(text="正在汇总数据...")
        prog_dlg.update()

        all_hz_vals = []
        for syls_dict in aggregated_syl_contours.values():
            for y_arrays in syls_dict.values():
                finite_vals = np.asarray(y_arrays, dtype=float).ravel()
                finite_vals = finite_vals[np.isfinite(finite_vals)]
                all_hz_vals.extend(finite_vals.tolist())

        if all_hz_vals:
            if params:
                f0_mode = params.get('f0_mode', 'minmax')
                p_low = params.get('percentile_low', 5.0)
                p_high = params.get('percentile_high', 95.0)
                m_min = params.get('manual_min', 75.0)
                m_max = params.get('manual_max', 600.0)
            else:
                f0_mode = 'minmax'
                p_low, p_high, m_min, m_max = 5.0, 95.0, 75.0, 600.0

            if f0_mode == 'percentile':
                min_f0 = np.percentile(all_hz_vals, p_low)
                max_f0 = np.percentile(all_hz_vals, p_high)
            elif f0_mode == 'manual':
                min_f0 = m_min
                max_f0 = m_max
            else:
                min_f0 = min(all_hz_vals)
                max_f0 = max(all_hz_vals)

            for name, syls_dict in aggregated_syl_contours.items():
                for k, y_arrays in syls_dict.items():
                    normalized_arrays = []
                    for y_arr in y_arrays:
                        y_arr = np.asarray(y_arr, dtype=float)
                        t_arr = np.full_like(y_arr, np.nan, dtype=float)
                        valid = np.isfinite(y_arr)
                        if min_f0 > 0 and max_f0 > min_f0 and np.any(valid):
                            hz_vals = np.clip(y_arr[valid], min_f0, max_f0)
                            t_arr[valid] = 5 * (np.log(hz_vals) - np.log(min_f0)) / (np.log(max_f0) - np.log(min_f0))
                        elif np.any(valid):
                            t_arr[valid] = 3.0
                        normalized_arrays.append(t_arr)
                    syls_dict[k] = normalized_arrays

        group_norm_points = {}
        for name, syls_dict in aggregated_syl_contours.items():
            X_all, Y_all = [], []
            for k, y_arrays in syls_dict.items():
                x_dense = np.linspace(k * 100, (k + 1) * 100, N_DENSE)
                for y_arr in y_arrays:
                    y_arr = np.asarray(y_arr, dtype=float)
                    valid = np.isfinite(y_arr)
                    X_all.extend(x_dense[valid].tolist())
                    Y_all.extend(y_arr[valid].tolist())
            group_norm_points[name] = (np.array(X_all), np.array(Y_all))

        pbar.set(0.8)
        prog_dlg.update()

        bw_method = params.get('bw_method', 0.15) if params else 0.15
        self._draw_kde_heatmap(group_norm_points, max_syls, out_file, prog_dlg, pbar, lbl_status, bw_method=bw_method)
        return True


    def _export_textgrid_long(self, out_file, tree_structure=None):
        import textgrid
        if tree_structure is None: tree_structure = self._get_all_items_by_group()

        max_time = 0
        for grp_name, children in tree_structure:
            for child in children:
                item = self.items[child]
                if item.get('end') is not None and item['end'] > max_time:
                    max_time = item['end']

        if max_time == 0:
            max_time = 1.0 # default if empty

        tg = textgrid.TextGrid(maxTime=max_time)
        word_tier = textgrid.IntervalTier(name="words", minTime=0.0, maxTime=max_time)
        char_tier = textgrid.IntervalTier(name="chars", minTime=0.0, maxTime=max_time)
        group_tier = textgrid.IntervalTier(name="groups", minTime=0.0, maxTime=max_time)

        has_chars = False

        last_word_end = 0.0
        last_char_end = 0.0
        last_group_end = 0.0

        flat_items = []
        item_to_group = {}
        for grp_name, children in tree_structure:
            for child in children:
                item = self.items[child]
                if item.get('start') is not None and item.get('end') is not None:
                    flat_items.append(item)
                    item_to_group[id(item)] = grp_name

        flat_items.sort(key=lambda x: x['start'])

        for item in flat_items:
            t_s, t_e = item['start'], item['end']
            label = item.get('label', '')
            inner_splits = item.get('inner_splits', [])
            grp_name = item_to_group.get(id(item), "导入内容")

            if t_s > last_word_end:
                word_tier.add(last_word_end, t_s, "")
            word_tier.add(t_s, t_e, label)
            last_word_end = t_e

            if t_s > last_group_end:
                group_tier.add(last_group_end, t_s, "")
            group_tier.add(t_s, t_e, grp_name)
            last_group_end = t_e

            syls = split_into_syllables(label)
            if len(syls) > 1:
                has_chars = True

                if t_s > last_char_end:
                    char_tier.add(last_char_end, t_s, "")

                chars_bounds = item.get('chars_bounds', [])
                if not chars_bounds:
                    import numpy as np
                    splits = [t_s] + [s for s in inner_splits if t_s < s < t_e] + [t_e]
                    if len(splits) != len(syls) + 1:
                        splits = np.linspace(t_s, t_e, len(syls) + 1).tolist()
                    chars_bounds = [(splits[j], splits[j+1]) for j in range(len(splits)-1)]

                local_last = t_s
                for i in range(len(syls)):
                    if i < len(chars_bounds):
                        c_s, c_e = chars_bounds[i]
                        if c_s > local_last:
                            char_tier.add(local_last, c_s, "")
                        char_tier.add(c_s, c_e, syls[i])
                        local_last = c_e

                if local_last < t_e:
                    char_tier.add(local_last, t_e, "")
                last_char_end = t_e
            else:
                if t_s > last_char_end:
                    char_tier.add(last_char_end, t_s, "")
                char_tier.add(t_s, t_e, label)
                last_char_end = t_e

        if max_time > last_word_end:
            word_tier.add(last_word_end, max_time, "")
        if max_time > last_char_end:
            char_tier.add(last_char_end, max_time, "")
        if max_time > last_group_end:
            group_tier.add(last_group_end, max_time, "")

        tg.append(word_tier)
        tg.append(group_tier)
        if has_chars:
            tg.append(char_tier)

        tg.write(out_file)

    def _export_textgrid_batch(self, out_dir, tree_structure=None):
        import textgrid
        import os
        if tree_structure is None: tree_structure = self._get_all_items_by_group()

        # Group items by source file path
        path_to_items = {}
        item_to_group = {}
        for grp_name, children in tree_structure:
            for child in children:
                item = self.items[child]
                item_to_group[id(item)] = grp_name
                if item.get('path'):
                    path = item['path']
                    if path not in path_to_items:
                        path_to_items[path] = []
                    path_to_items[path].append(item)

        out_subdir = os.path.join(out_dir, "Textgrid_export")
        os.makedirs(out_subdir, exist_ok=True)

        for path, items in path_to_items.items():
            base_name = os.path.splitext(os.path.basename(path))[0]
            tg_path = os.path.join(out_subdir, f"{base_name}.TextGrid")

            # Since it's batch mode, usually each item corresponds to the full file.
            # but if multiple items share the same file, we'll combine them based on their time ranges
            max_time = 0
            for item in items:
                # If batch mode, the audio length might be available via snd object
                if item.get('snd'):
                    dur = item['snd'].get_total_duration()
                    if dur > max_time: max_time = dur
                elif item.get('end') is not None and item['end'] > max_time:
                    max_time = item['end']

            if max_time == 0: max_time = 1.0

            tg = textgrid.TextGrid(maxTime=max_time)
            word_tier = textgrid.IntervalTier(name="words", minTime=0.0, maxTime=max_time)
            char_tier = textgrid.IntervalTier(name="chars", minTime=0.0, maxTime=max_time)
            group_tier = textgrid.IntervalTier(name="groups", minTime=0.0, maxTime=max_time)

            items.sort(key=lambda x: x.get('start', 0))

            last_word_end = 0.0
            last_char_end = 0.0
            last_group_end = 0.0
            has_chars = False

            for item in items:
                if item.get('start') is None or item.get('end') is None: continue
                t_s, t_e = item['start'], item['end']
                label = item.get('label', '')
                inner_splits = item.get('inner_splits', [])
                grp_name = item_to_group.get(id(item), "导入内容")

                if t_s > last_word_end:
                    word_tier.add(last_word_end, t_s, "")
                word_tier.add(t_s, t_e, label)
                last_word_end = t_e

                if t_s > last_group_end:
                    group_tier.add(last_group_end, t_s, "")
                group_tier.add(t_s, t_e, grp_name)
                last_group_end = t_e

                syls = split_into_syllables(label)
                if len(syls) > 1:
                    has_chars = True
                    if t_s > last_char_end:
                        char_tier.add(last_char_end, t_s, "")

                    chars_bounds = item.get('chars_bounds', [])
                    if not chars_bounds:
                        import numpy as np
                        splits = [t_s] + [s for s in inner_splits if t_s < s < t_e] + [t_e]
                        if len(splits) != len(syls) + 1:
                            splits = np.linspace(t_s, t_e, len(syls) + 1).tolist()
                        chars_bounds = [(splits[j], splits[j+1]) for j in range(len(splits)-1)]

                    local_last = t_s
                    for i in range(len(syls)):
                        if i < len(chars_bounds):
                            c_s, c_e = chars_bounds[i]
                            if c_s > local_last:
                                char_tier.add(local_last, c_s, "")
                            char_tier.add(c_s, c_e, syls[i])
                            local_last = c_e
                    if local_last < t_e:
                        char_tier.add(local_last, t_e, "")
                    last_char_end = t_e
                else:
                    if t_s > last_char_end:
                        char_tier.add(last_char_end, t_s, "")
                    char_tier.add(t_s, t_e, label)
                    last_char_end = t_e

            if max_time > last_word_end:
                word_tier.add(last_word_end, max_time, "")
            if max_time > last_char_end:
                char_tier.add(last_char_end, max_time, "")
            if max_time > last_group_end:
                group_tier.add(last_group_end, max_time, "")

            tg.append(word_tier)
            tg.append(group_tier)
            if has_chars:
                tg.append(char_tier)

            tg.write(tg_path)
            tg.append(group_tier)
            if has_chars:
                tg.append(char_tier)

            tg.write(tg_path)
