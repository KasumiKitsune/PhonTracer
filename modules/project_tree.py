import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import customtkinter as ctk
import csv
import parselmouth
import numpy as np
import math
import matplotlib.pyplot as plt
import logging
from .data_utils import get_export_text_for_item
from .ui_widgets import ToolTip, CTkReleaseButton, AutoScrollbar

logger = logging.getLogger(__name__)

class ProjectTreePanel:
    def __init__(self, parent, icons, items_dict, app_state_params, on_item_selected_callback, on_clear_canvas_callback, tk_icons=None):
        self.parent = parent
        self.icons = icons
        self.tk_icons = tk_icons or {}
        self.items = items_dict
        self.app_state_params = app_state_params
        self.on_item_selected = on_item_selected_callback
        self.on_clear_canvas = on_clear_canvas_callback
        
        self.project_groups = []
        self.group_nodes = {}
        self.current_iid = None
        self.tree_drag_items = None
        self.last_hover = None
        
        self.warning_group_id = None
        self.warning_iids = {}
        
        self.font_title = ctk.CTkFont(family="Microsoft YaHei", size=15, weight="bold")
        self.font_main = ctk.CTkFont(family="Microsoft YaHei", size=13)
        self.font_code = ctk.CTkFont(family="Consolas", size=13)
        
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
        
        btn_add_group = CTkReleaseButton(frame_list, text=" 新增组", image=self.icons.get("plus"), compound="left", width=120, height=30, corner_radius=8, command=self.add_new_group, fg_color="#F3F4F6", text_color="#374151", hover_color="#E5E7EB")
        btn_add_group.pack(pady=(0, 15))

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

        frame_rule = ctk.CTkFrame(right_sidebar, fg_color="white", corner_radius=10)
        frame_rule.pack(fill=tk.X, pady=5)
        ctk.CTkLabel(frame_rule, text="导出标号规则", font=self.font_title, text_color="#111827").pack(anchor=tk.W, padx=15, pady=(10, 0))
        self.num_rule_var = ctk.StringVar(value="continuous")
        rule_opts = ctk.CTkFrame(frame_rule, fg_color="transparent")
        rule_opts.pack(fill=tk.X, padx=15, pady=(5, 10))
        ctk.CTkRadioButton(rule_opts, text="全部连续 (1, 2...)", variable=self.num_rule_var, value="continuous", command=self.update_preview, font=self.font_main).pack(side=tk.LEFT, padx=(0, 10))
        ctk.CTkRadioButton(rule_opts, text="每组重新标号", variable=self.num_rule_var, value="per_group", command=self.update_preview, font=self.font_main).pack(side=tk.LEFT)

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
        
        for i, item in enumerate(visible):
            tags = list(self.tree.item(item, 'tags'))
            tags = [t for t in tags if t not in ('even', 'odd', 'hover', 'drag_target')]
            tags.append('even' if i % 2 == 0 else 'odd')
            self.tree.item(item, tags=tags)

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
            gid = self.tree.insert("", tk.END, text=group_name, open=True, tags=('group',))
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
        gid = self.tree.insert("", tk.END, text=temp_name, open=True, tags=('group',))
        self.group_nodes[temp_name] = gid
        
        self.tree.see(gid)
        self.tree.selection_set(gid)
        self._debounce_zebra_stripes()
        self.parent.after(50, lambda: self.start_inline_edit(gid))

    def start_inline_edit(self, iid):
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
                self.tree.item(iid, text=new_name)
                self.items[iid]['label'] = new_name
                if iid.startswith('warning_'):
                    orig_iid = iid[8:]
                    if self.tree.exists(orig_iid): self.tree.item(orig_iid, text=new_name)
                else:
                    if iid in self.warning_iids:
                        w_iid = self.warning_iids[iid]
                        if self.tree.exists(w_iid): self.tree.item(w_iid, text=new_name)
            
            self.update_preview()
            self._debounce_zebra_stripes()
            edit_entry.destroy()

        edit_entry.bind("<Return>", save_edit)
        edit_entry.bind("<FocusOut>", save_edit)
        edit_entry.bind("<Escape>", lambda e: edit_entry.destroy())

    def select_first_item(self):
        if self.items:
            first_iid = list(self.items.keys())[0]
            self.tree.selection_set(first_iid)
            self.on_tree_select(None)

    def on_tree_select(self, event):
        selection = self.tree.selection()
        if not selection: return
        iid = selection[0]
        if 'item' not in self.tree.item(iid, 'tags'): return
        
        self.current_iid = iid
        if self.on_item_selected:
            self.on_item_selected(iid)
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
                        self.items.pop(child, None)
                        if child in self.warning_iids:
                            w_iid = self.warning_iids.pop(child)
                            if self.tree.exists(w_iid): self.tree.delete(w_iid)
                        if self.current_iid == child:
                            self.current_iid = None
                            if self.on_clear_canvas: self.on_clear_canvas()
                    self.tree.delete(gid)
                    if group_name in self.project_groups: self.project_groups.remove(group_name)
                    self.group_nodes.pop(group_name, None)
                    
        real_items_to_del = set()
        for iid in items_to_del:
            if iid.startswith('warning_'):
                real_items_to_del.add(iid[8:])
            else:
                real_items_to_del.add(iid)
                
        for iid in real_items_to_del:
            if self.tree.exists(iid):
                self.items.pop(iid, None)
                self.tree.delete(iid)
                if iid in self.warning_iids:
                    w_iid = self.warning_iids.pop(iid)
                    if self.tree.exists(w_iid): self.tree.delete(w_iid)
                if self.current_iid == iid or self.current_iid == f"warning_{iid}":
                    self.current_iid = None
                    if self.on_clear_canvas: self.on_clear_canvas()
                    
        if self.warning_group_id and self.tree.exists(self.warning_group_id):
            if not self.tree.get_children(self.warning_group_id):
                self.tree.delete(self.warning_group_id)
                self.warning_group_id = None
                
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
                self.tree_drag_items = [item for item in sel if 'item' in self.tree.item(item, 'tags')]
        
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

            if parent_grp:
                group_name = self.tree.item(parent_grp, 'text')
                for drag_item in reversed(self.tree_drag_items):
                    self.tree.move(drag_item, parent_grp, target_idx)
                    self.items[drag_item]['group'] = group_name
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
        is_continuous = (self.num_rule_var.get() == "continuous")
        if not is_continuous:
            return self.tree.index(target_iid) + 1

        target_group = self.items[target_iid]['group']
        idx = 0
        for grp_name in self.project_groups:
            if grp_name == target_group: break
            grp_node = self.group_nodes.get(grp_name)
            if grp_node: idx += len(self.tree.get_children(grp_node))

        return idx + self.tree.index(target_iid) + 1

    def update_preview(self):
        if self.current_iid not in self.items:
            if str(self.current_iid).startswith('warning_'):
                orig_iid = self.current_iid[8:]
                if orig_iid in self.items:
                    self.current_iid = orig_iid
                    try: 
                        if self.tree.exists(orig_iid):
                            self.tree.selection_set(orig_iid)
                            self.tree.see(orig_iid)
                    except: pass
                else: self.current_iid = None
            else: self.current_iid = None
                
        if not self.current_iid:
            self.text_preview.configure(state='normal')
            self.text_preview.delete('1.0', tk.END)
            self.text_preview.configure(state='disabled')
            return
            
        item = self.items[self.current_iid]
        real_idx = self._get_item_index(self.current_iid)
        text = get_export_text_for_item(item, real_idx, self.app_state_params['pts'], pitch_floor=self.app_state_params.get('pitch_floor', 75.0), pitch_ceiling=self.app_state_params.get('pitch_ceiling', 600.0), voicing_threshold=self.app_state_params.get('voicing_threshold', 0.25))
        
        self.text_preview.configure(state='normal')
        self.text_preview.delete('1.0', tk.END)
        self.text_preview.insert(tk.END, text)
        
        self.text_preview.tag_config("zero", foreground="#EF4444")
        start_idx = "1.0"
        while True:
            pos = self.text_preview.search("0.000000", start_idx, stopindex=tk.END)
            if not pos: break
            end_pos = f"{pos}+8c"
            self.text_preview.tag_add("zero", pos, end_pos)
            start_idx = end_pos
            
        self.text_preview.configure(state='disabled')

    def _check_item_has_empty_data(self, item):
        """精准检测子音节区间的11点中是否含有0/NaN值（已应用智能边界收缩防误报）"""
        if not item or item.get('start') is None: return False
        
        # 1. 如果音频和 Pitch 对象已加载，优先执行最高精度的实时重新计算，并更新缓存
        if item.get('snd') and item.get('pitch'):
            num_points = int(self.app_state_params.get('pts', 10))
            t_s, t_e = item['start'], item['end']
            label = item.get('label', '')
            inner_splits = item.get('inner_splits', [])
            
            chars_bounds = item.get('chars_bounds', [])
            if chars_bounds and len(chars_bounds) == len(label):
                bounds = chars_bounds
            else:
                splits = [t_s] + [s for s in inner_splits if t_s < s < t_e] + [t_e]
                if len(label) > 1 and len(splits) != len(label) + 1:
                    splits = np.linspace(t_s, t_e, len(label) + 1).tolist()
                elif len(label) <= 1:
                    splits = [t_s, t_e]
                bounds = [[splits[i], splits[i+1]] for i in range(len(splits)-1)]
                
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

    def update_item_icon(self, iid):
        if str(iid).startswith('warning_'): return
        item = self.items.get(iid)
        if not item or item.get('start') is None: return
        
        has_empty = self._check_item_has_empty_data(item)
        img = self.tk_icons.get('warning', '') if has_empty else ''
        try:
            self.tree.item(iid, image=img)
        except tk.TclError:
            pass

        if has_empty:
            if not self.warning_group_id or not self.tree.exists(self.warning_group_id):
                self.warning_group_id = self.tree.insert("", 0, text="⚠️ 需要检查", open=True, tags=('group', 'warning_group'))
            
            if iid not in self.warning_iids:
                w_iid = f"warning_{iid}"
                self.tree.insert(self.warning_group_id, tk.END, iid=w_iid, text=item['label'], tags=('item',), image=img)
                self.items[w_iid] = item
                self.warning_iids[iid] = w_iid
            else:
                w_iid = self.warning_iids[iid]
                if self.tree.exists(w_iid):
                    self.tree.item(w_iid, text=item['label'], image=img)
        else:
            if iid in self.warning_iids:
                w_iid = self.warning_iids.pop(iid)
                if self.tree.exists(w_iid):
                    self.tree.delete(w_iid)
                self.items.pop(w_iid, None)
                
            if self.warning_group_id and self.tree.exists(self.warning_group_id):
                if not self.tree.get_children(self.warning_group_id):
                    self.tree.delete(self.warning_group_id)
                    self.warning_group_id = None
        self._debounce_zebra_stripes()

    def export_project(self):
        if not self.items: return messagebox.showwarning("提示", "没有可导出的数据。")
        
        tree_structure = self._get_all_items_by_group()
        empty_labels = []
        for grp_name, children in tree_structure:
            for child in children:
                item = self.items[child]
                if self._check_item_has_empty_data(item):
                    empty_labels.append(f"[{grp_name}] {item['label']}")
        
        if empty_labels:
            msg = "以下项目的基频数据包含 0 值（可能无法提取有效声调）：\n\n"
            msg += "\n".join(empty_labels[:10])
            if len(empty_labels) > 10: msg += f"\n... 等共 {len(empty_labels)} 项"
            msg += "\n\n是否继续导出？"
            if not messagebox.askyesno("空数据警告", msg):
                return
                
        self._show_export_menu(tree_structure)

    def _show_export_menu(self, tree_structure=None):
        dlg = ctk.CTkToplevel(self.parent)
        dlg.title("选择导出格式")
        dlg.geometry("320x320")
        dlg.attributes('-topmost', True)
        dlg.resizable(False, False)
        
        dlg.update_idletasks()
        main_win = self.parent.winfo_toplevel()
        x = main_win.winfo_rootx() + (main_win.winfo_width() - 320) // 2
        y = main_win.winfo_rooty() + (main_win.winfo_height() - 320) // 2
        dlg.geometry(f"+{x}+{y}")
        
        ctk.CTkLabel(dlg, text="请选择导出格式", font=self.font_title, text_color="#111827").pack(pady=(20, 15))
        
        btn_kwargs = {"corner_radius": 12, "height": 44, "font": self.font_main, "anchor": "w", "compound": "left"}
        
        def do_export(mode):
            dlg.destroy()
            if mode == 'textgrid':
                out_file = filedialog.asksaveasfilename(title="导出TextGrid", defaultextension=".TextGrid", initialfile="tone_export_data", filetypes=[("TextGrid文件", "*.TextGrid"), ("所有文件", "*.*")])
                if not out_file: return
                try:
                    self._export_textgrid(out_file, tree_structure=tree_structure)
                    messagebox.showinfo("成功", f"数据已导出至:\n{out_file}\n（独立音频已保存到该目录下的 TextGrid_export 文件夹）")
                except Exception as e: messagebox.showerror("错误", str(e))
            elif mode == 'txt':
                out_file = filedialog.asksaveasfilename(title="导出文本", defaultextension=".txt", initialfile="tone_export_data", filetypes=[("文本文件", "*.txt")])
                if not out_file: return
                try:
                    self._export_txt(out_file, tree_structure=tree_structure)
                    messagebox.showinfo("成功", f"数据已导出至:\n{out_file}")
                except Exception as e: messagebox.showerror("错误", str(e))
            elif mode == 'xlsx':
                out_file = filedialog.asksaveasfilename(title="导出Excel", defaultextension=".xlsx", initialfile="tone_export_data", filetypes=[("Excel 表格", "*.xlsx")])
                if not out_file: return
                try:
                    include_chart = messagebox.askyesno("导出设置", "是否在 Excel 中包含分析图表？\n(包含图表可能在部分旧版 Office 中打开较慢)", default=messagebox.NO)
                    self._export_xlsx(out_file, include_chart=include_chart, tree_structure=tree_structure)
                    messagebox.showinfo("成功", f"数据已导出至:\n{out_file}")
                except Exception as e: messagebox.showerror("错误", str(e))
            elif mode == 'line_chart':
                out_file = filedialog.asksaveasfilename(title="导出折线图", defaultextension=".png", initialfile="tone_line_chart", filetypes=[("PNG 图片", "*.png"), ("SVG 矢量图", "*.svg"), ("PDF 文档", "*.pdf")])
                if not out_file: return
                try:
                    self._export_line_chart(out_file, tree_structure=tree_structure)
                    messagebox.showinfo("成功", f"图表已导出至:\n{out_file}")
                except Exception as e: messagebox.showerror("错误", str(e))
            elif mode == 'kde':
                out_file = filedialog.asksaveasfilename(title="导出KDE热力图", defaultextension=".png", initialfile="tone_kde_heatmap", filetypes=[("PNG 图片", "*.png"), ("SVG 矢量图", "*.svg"), ("PDF 文档", "*.pdf")])
                if not out_file: return
                try:
                    self._export_kde_heatmap(out_file, tree_structure=tree_structure)
                    messagebox.showinfo("成功", f"热力图已导出至:\n{out_file}")
                except Exception as e: messagebox.showerror("错误", str(e))
        
        ctk.CTkButton(dlg, text="  📄  文本文件 (.txt)", command=lambda: do_export('txt'), fg_color="#F3F4F6", text_color="#374151", hover_color="#E5E7EB", **btn_kwargs).pack(fill=tk.X, padx=25, pady=4)
        ctk.CTkButton(dlg, text="  📝  TextGrid (.TextGrid)", command=lambda: do_export('textgrid'), fg_color="#FDF4FF", text_color="#86198F", hover_color="#FAE8FF", **btn_kwargs).pack(fill=tk.X, padx=25, pady=4)
        ctk.CTkButton(dlg, text="  📊  Excel 表格 (.xlsx)", command=lambda: do_export('xlsx'), fg_color="#ECFDF5", text_color="#047857", hover_color="#D1FAE5", **btn_kwargs).pack(fill=tk.X, padx=25, pady=4)
        ctk.CTkButton(dlg, text="  📈  声调格局连贯折线图", command=lambda: do_export('line_chart'), fg_color="#EFF6FF", text_color="#1E40AF", hover_color="#DBEAFE", **btn_kwargs).pack(fill=tk.X, padx=25, pady=4)
        ctk.CTkButton(dlg, text="  🔥  词语时序密度热力图", command=lambda: do_export('kde'), fg_color="#FFF7ED", text_color="#9A3412", hover_color="#FFEDD5", **btn_kwargs).pack(fill=tk.X, padx=25, pady=4)

    def _extract_syl_data(self, item, num_points):
        """提取项目中每个字的真实发音段(收缩后)的 11 点 F0 数据和时长。返回 (总时长, [(字时长, [F0数组]), ...])"""
        if item.get('start') is None or not item.get('snd') or not item.get('pitch'): return 0, []
        t_s, t_e = item['start'], item['end']
        if t_e <= t_s: return 0, []
        
        label = item.get('label', '')
        inner_splits = item.get('inner_splits', [])
        pitch = item['pitch']
        p_xs = pitch.xs()
        p_freqs = pitch.selected_array['frequency']
        
        chars_bounds = item.get('chars_bounds', [])
        if chars_bounds and len(chars_bounds) == len(label):
            bounds = chars_bounds
        else:
            splits = [t_s] + [s for s in inner_splits if t_s < s < t_e] + [t_e]
            if len(label) > 1 and len(splits) != len(label) + 1:
                splits = np.linspace(t_s, t_e, len(label) + 1).tolist()
            elif len(label) <= 1:
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
                if (not item.get('snd') or not item.get('pitch')) and item.get('path'):
                    try:
                        item['snd'] = parselmouth.Sound(item['path'])
                        pf = item.get('pitch_floor', self.app_state_params.get('pitch_floor', 75))
                        pc = item.get('pitch_ceiling', self.app_state_params.get('pitch_ceiling', 600))
                        vt = item.get('voicing_threshold', self.app_state_params.get('voicing_threshold', 0.25))
                        item['pitch'] = item['snd'].to_pitch_ac(time_step=None, pitch_floor=pf, pitch_ceiling=pc, voicing_threshold=vt, very_accurate=True, octave_jump_cost=0.9)
                    except Exception as e:
                        logger.error(f"Error loading sound or pitch for {item['path']}: {e}", exc_info=True)
                        continue
                    
                total_dur, syl_data = self._extract_syl_data(item, num_points)
                if total_dur <= 0: continue
                
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

        res_headers = ["声调类型"]
        for k in range(1, max_syls + 1):
            res_headers.append(f"字{k}_平均时长")
            for i in range(1, num_points + 1): res_headers.append(f"字{k}_T{i}")
        for col, header in enumerate(res_headers): ws_res.write(0, col, header)

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
        
        res_row = 1
        for grp, st in dict_data.items():
            ws_res.write(res_row, 0, grp)
            col = 1
            for k in range(max_syls):
                cnt = st['syl_counts'][k]
                avg_dur = st['syl_dur_sums'][k] / cnt if cnt > 0 else 0
                ws_res.write(res_row, col, round(avg_dur, 4))
                col += 1
                
                for avg_hz in avg_points_map[grp][k]:
                    if avg_hz > 0 and max_hz > min_hz and min_hz > 0:
                        t_val = 5 * (math.log10(avg_hz) - math.log10(min_hz)) / (math.log10(max_hz) - math.log10(min_hz))
                        ws_res.write(res_row, col, round(t_val, 2))
                    else:
                        ws_res.write(res_row, col, "")
                    col += 1
            res_row += 1
            
        if include_chart:
            try:
                ws_chart_data = workbook.add_worksheet("图表数据")
                ws_chart_data.hide()
                ws_chart_data.write(0, 0, "声调类型")
                for p in range(1, max_syls * num_points + 1):
                    ws_chart_data.write(0, p, p)
                    
                chart_row = 1
                for grp, st in dict_data.items():
                    ws_chart_data.write(chart_row, 0, grp)
                    col_idx = 1
                    for k in range(max_syls):
                        for avg_hz in avg_points_map[grp][k]:
                            if avg_hz > 0 and max_hz > min_hz and min_hz > 0:
                                t_val = 5 * (math.log10(avg_hz) - math.log10(min_hz)) / (math.log10(max_hz) - math.log10(min_hz))
                                ws_chart_data.write(chart_row, col_idx, round(t_val, 2))
                            else:
                                ws_chart_data.write(chart_row, col_idx, "")
                            col_idx += 1
                    chart_row += 1
                    
                chart = workbook.add_chart({'type': 'line'})
                for r in range(1, len(dict_data) + 1):
                    chart.add_series({
                        'name':       ['图表数据', r, 0],
                        'categories': ['图表数据', 0, 1, 0, max_syls * num_points],
                        'values':     ['图表数据', r, 1, r, max_syls * num_points],
                        'line':       {'width': 2.0},
                    })
                    
                chart.set_title({
                    'name': '连读变调声调格局图',
                    'name_font': {'name': 'Microsoft YaHei', 'size': 14, 'bold': True}
                })
                chart.set_x_axis({
                    'name': '测量点 (时序展开)',
                    'name_font': {'name': 'Microsoft YaHei', 'size': 10},
                    'num_font': {'name': 'Arial', 'size': 9}
                })
                chart.set_y_axis({
                    'name': 'T值 (0-5 标度)',
                    'name_font': {'name': 'Microsoft YaHei', 'size': 10},
                    'num_font': {'name': 'Arial', 'size': 9},
                    'min': 0,
                    'max': 5
                })
                chart.set_legend({
                    'position': 'bottom',
                    'font': {'name': 'Microsoft YaHei', 'size': 9}
                })
                chart.set_size({'width': 720, 'height': 400})
                
                ws_res.insert_chart(f'A{res_row + 3}', chart)
            except Exception as chart_err:
                logger.error(f"Error generating Excel chart: {chart_err}", exc_info=True)
        
        workbook.close()

    def _export_textgrid(self, out_file, tree_structure=None):
        import os
        if tree_structure is None: tree_structure = self._get_all_items_by_group()

        is_long = getattr(self.parent, 'pending_long_snd', None) is not None

        def write_tg(path, duration, intervals):
            lines = [
                'File type = "ooTextFile"',
                'Object class = "TextGrid"',
                '',
                'xmin = 0',
                f'xmax = {duration}',
                'tiers? <exists>',
                'size = 1',
                'item []:',
                '    item [1]:',
                '        class = "IntervalTier"',
                '        name = "words"',
                '        xmin = 0',
                f'        xmax = {duration}',
                f'        intervals: size = {len(intervals)}' # placeholder
            ]

            # Fill gaps for Praat compatibility
            full_intervals = []
            curr_time = 0.0
            for (st, en, lab) in intervals:
                if st > curr_time:
                    full_intervals.append((curr_time, st, ""))
                full_intervals.append((st, en, lab))
                curr_time = en
            if curr_time < duration:
                full_intervals.append((curr_time, duration, ""))

            lines[-1] = f'        intervals: size = {len(full_intervals)}'

            for i, (st, en, lab) in enumerate(full_intervals):
                lines.extend([
                    f'        intervals [{i+1}]:',
                    f'            xmin = {st}',
                    f'            xmax = {en}',
                    f'            text = "{lab}"'
                ])
            with open(path, 'w', encoding='utf-8') as f:
                f.write('\n'.join(lines))

        if is_long:
            # Single TextGrid
            duration = self.parent.pending_long_snd.get_total_duration()
            intervals = []
            for grp_name, children in tree_structure:
                for child in children:
                    item = self.items[child]
                    if item.get('start') is not None and item.get('end') is not None:
                        intervals.append((item['start'], item['end'], item['label']))
            intervals.sort(key=lambda x: x[0])
            write_tg(out_file, duration, intervals)
        else:
            # Batch mode: multiple textgrids
            base_dir = os.path.dirname(os.path.abspath(out_file))
            tg_dir = os.path.join(base_dir, "TextGrid_export")
            os.makedirs(tg_dir, exist_ok=True)
            for grp_name, children in tree_structure:
                for child in children:
                    item = self.items[child]
                    if item.get('snd') is not None and item.get('start') is not None:
                        duration = item['snd'].get_total_duration()
                        # Output a textgrid for this file
                        # Base filename
                        if hasattr(item['snd'], 'filepath') and item['snd'].filepath:
                            fname = os.path.splitext(os.path.basename(item['snd'].filepath))[0]
                        else:
                            fname = f"{grp_name}_{item['label']}"

                        tg_path = os.path.join(tg_dir, f"{fname}.TextGrid")
                        intervals = [(item['start'], item['end'], item['label'])]
                        write_tg(tg_path, duration, intervals)


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
                if (not item.get('snd') or not item.get('pitch')) and item.get('path'):
                    try:
                        item['snd'] = parselmouth.Sound(item['path'])
                        pf = item.get('pitch_floor', self.app_state_params.get('pitch_floor', 75))
                        pc = item.get('pitch_ceiling', self.app_state_params.get('pitch_ceiling', 600))
                        vt = item.get('voicing_threshold', self.app_state_params.get('voicing_threshold', 0.25))
                        item['pitch'] = item['snd'].to_pitch_ac(time_step=None, pitch_floor=pf, pitch_ceiling=pc, voicing_threshold=vt, very_accurate=True, octave_jump_cost=0.9)
                    except Exception: continue
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

    def _export_kde_heatmap(self, out_file, tree_structure=None):
        from scipy.interpolate import interp1d
        from scipy.signal import savgol_filter
        from scipy.stats import gaussian_kde

        N_DENSE = 100  
        group_syl_contours = {} 
        
        prog_dlg = ctk.CTkToplevel(self.parent)
        prog_dlg.title("正在导出")
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

        if tree_structure is None: tree_structure = self._get_all_items_by_group()
        
        max_syls = 1
        for grp_name, children in tree_structure:
            group_syl_contours[grp_name] = {}
            for child in children:
                lbl = self.items[child].get('label', '')
                if len(lbl) > max_syls: max_syls = len(lbl)

        total_items = sum(len(c) for _, c in tree_structure)
        processed = 0

        for grp_name, children in tree_structure:
            for child in children:
                processed += 1
                pbar.set(0.7 * (processed / max(1, total_items)))
                prog_dlg.update()
                    
                if child not in self.items: continue
                item = self.items[child]
                if (not item.get('snd') or not item.get('pitch')) and item.get('path'):
                    try:
                        item['snd'] = parselmouth.Sound(item['path'])
                        pf = item.get('pitch_floor', self.app_state_params.get('pitch_floor', 75))
                        pc = item.get('pitch_ceiling', self.app_state_params.get('pitch_ceiling', 600))
                        vt = item.get('voicing_threshold', self.app_state_params.get('voicing_threshold', 0.25))
                        item['pitch'] = item['snd'].to_pitch_ac(time_step=None, pitch_floor=pf, pitch_ceiling=pc, voicing_threshold=vt, very_accurate=True, octave_jump_cost=0.9)
                    except Exception: continue
                if item.get('start') is None or not item.get('snd') or not item.get('pitch'): continue

                t_s, t_e = item['start'], item['end']
                label = item.get('label', '')
                inner_splits = item.get('inner_splits', [])
                
                splits = [t_s] + [s for s in inner_splits if t_s < s < t_e] + [t_e]
                if len(splits) != len(label) + 1: splits = np.linspace(t_s, t_e, len(label) + 1).tolist()
                if len(label) <= 1: splits = [t_s, t_e]
                
                pitch = item['pitch']
                p_xs, p_freqs = pitch.xs(), pitch.selected_array['frequency']
                
                for k in range(len(splits) - 1):
                    c_s, c_e = splits[k], splits[k+1]
                    # 智能边界收缩：只画有真实发音的高密度区！
                    valid_idx = np.where((p_xs >= c_s) & (p_xs <= c_e) & (p_freqs > 0))[0]
                    if len(valid_idx) >= 2:
                        v_s, v_e = p_xs[valid_idx[0]], p_xs[valid_idx[-1]]
                        mask = (p_xs >= v_s) & (p_xs <= v_e) & (p_freqs > 0)
                        valid_freqs = p_freqs[mask]
                        if len(valid_freqs) < 3: continue
    
                        win = len(valid_freqs) // 3
                        if win % 2 == 0: win += 1
                        win = max(win, 3)
                        smoothed = savgol_filter(valid_freqs, win, 2) if len(valid_freqs) > win else valid_freqs
    
                        x_orig = np.linspace(0, 1, len(smoothed))
                        f_interp = interp1d(x_orig, smoothed, kind='linear')
                        y_dense = f_interp(np.linspace(0, 1, N_DENSE))
                        
                        if k not in group_syl_contours[grp_name]: group_syl_contours[grp_name][k] = []
                        group_syl_contours[grp_name][k].append(y_dense)

        all_mean_vals = []
        for name, syls_dict in group_syl_contours.items():
            for k, y_arrays in syls_dict.items():
                if y_arrays:
                    mean_contour = np.mean(y_arrays, axis=0)
                    all_mean_vals.extend(mean_contour.tolist())
                    
        if not all_mean_vals:
            prog_dlg.destroy()
            return messagebox.showwarning("提示", "没有有效数据可供绘制热力图。")
            
        min_f0, max_f0 = min(all_mean_vals), max(all_mean_vals)

        lbl_status.configure(text="正在进行数据归一化...")
        pbar.set(0.75)
        prog_dlg.update()

        def hz_to_5_scale(hz):
            if max_f0 == min_f0: return 3.0
            return 5 * (np.log(hz) - np.log(min_f0)) / (np.log(max_f0) - np.log(min_f0))

        group_norm_points = {} 
        for name, syls_dict in group_syl_contours.items():
            X_all, Y_all = [], []
            for k, y_arrays in syls_dict.items():
                x_dense = np.linspace(k * 100, (k + 1) * 100, N_DENSE)
                for y_arr in y_arrays:
                    X_all.extend(x_dense.tolist())
                    Y_all.extend([hz_to_5_scale(h) for h in y_arr])
            group_norm_points[name] = (np.array(X_all), np.array(Y_all))
            
        pbar.set(0.8)
        prog_dlg.update()

        plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'Arial Unicode MS']
        plt.rcParams['axes.unicode_minus'] = False

        groups_with_data = [g for g in self.project_groups if group_norm_points.get(g) and len(group_norm_points[g][0]) > 0]
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
                kernel = gaussian_kde(positions, bw_method=0.15) 
                xi, yi = np.mgrid[xmin:xmax:200j, ymin:ymax:100j]
                zi = kernel(np.vstack([xi.flatten(), yi.flatten()]))
                zi = zi.reshape(xi.shape)
                
                vmax = zi.max()
                if vmax > 0:
                    levels = np.linspace(vmax * 0.05, vmax, 30)
                    ax.contourf(xi, yi, zi, levels=levels, cmap="YlOrRd", extend='neither')
            except Exception as e:
                logger.error(f"KDE drawing failed for {grp_name}: {e}")

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