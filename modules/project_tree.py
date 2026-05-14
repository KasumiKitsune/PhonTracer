import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import customtkinter as ctk
import csv
import parselmouth
import numpy as np
from modules.data_utils import get_export_text_for_item
from modules.ui_widgets import CTkReleaseButton

class ProjectTreePanel:
    def __init__(self, parent, icons, items_dict, app_state_params, on_item_selected_callback, on_clear_canvas_callback):
        self.parent = parent
        self.icons = icons
        self.items = items_dict
        self.app_state_params = app_state_params
        self.on_item_selected = on_item_selected_callback
        self.on_clear_canvas = on_clear_canvas_callback
        
        self.project_groups = []
        self.group_nodes = {}
        self.current_iid = None
        self.tree_drag_item = None
        self.last_hover = None
        
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
        self.tree = ttk.Treeview(tree_container, show='tree', selectmode='extended')
        scroll_tree = ctk.CTkScrollbar(tree_container, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll_tree.set)
        scroll_tree.pack(side=tk.RIGHT, fill=tk.Y, padx=(5, 0))
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
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
        self.tree.bind('<<TreeviewOpen>>', lambda e: self.parent.after(10, self._apply_zebra_stripes))
        self.tree.bind('<<TreeviewClose>>', lambda e: self.parent.after(10, self._apply_zebra_stripes))

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
        # 优雅的行内添加方式：先插入一个占位符，然后直接触发编辑
        temp_name = "新组别"
        base_name = temp_name
        counter = 1
        while temp_name in self.project_groups:
            temp_name = f"{base_name} {counter}"
            counter += 1
            
        self.project_groups.append(temp_name)
        gid = self.tree.insert("", tk.END, text=temp_name, open=True, tags=('group',))
        self.group_nodes[temp_name] = gid
        
        # 滚动到新组并选中
        self.tree.see(gid)
        self.tree.selection_set(gid)
        
        # 稍微延迟一下确保 Treeview 完成渲染后定位坐标
        self.parent.after(50, lambda: self.start_inline_edit(gid))

    def start_inline_edit(self, iid):
        bbox = self.tree.bbox(iid, "#0")
        if not bbox: return
        x, y, w, h = bbox
        old_name = self.tree.item(iid, 'text')
        
        # 在对应位置创建一个浮动的 Entry
        edit_entry = tk.Entry(self.tree, font=("Microsoft YaHei", 12), borderwidth=1, relief="solid")
        edit_entry.insert(0, old_name)
        edit_entry.select_range(0, tk.END)
        edit_entry.focus_set()
        edit_entry.place(x=x, y=y, width=w, height=h)
        
        def save_edit(event=None):
            new_name = edit_entry.get().strip()
            if not edit_entry.winfo_exists(): return
            
            # 如果没改或者为空，直接销毁
            if not new_name or new_name == old_name:
                edit_entry.destroy()
                return

            if 'group' in self.tree.item(iid, 'tags'):
                if new_name in self.project_groups:
                    messagebox.showwarning("错误", "组名已存在")
                    edit_entry.destroy()
                    return
                # 更新内部状态
                idx = self.project_groups.index(old_name)
                self.project_groups[idx] = new_name
                self.group_nodes[new_name] = self.group_nodes.pop(old_name)
                self.tree.item(iid, text=new_name)
                # 同步更新子项的组属性
                for child in self.tree.get_children(iid):
                    if child in self.items: self.items[child]['group'] = new_name
            elif 'item' in self.tree.item(iid, 'tags'):
                self.tree.item(iid, text=new_name)
                self.items[iid]['label'] = new_name
            
            self.update_preview()
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
                        if self.current_iid == child:
                            self.current_iid = None
                            if self.on_clear_canvas: self.on_clear_canvas()
                    self.tree.delete(gid)
                    if group_name in self.project_groups: self.project_groups.remove(group_name)
                    self.group_nodes.pop(group_name, None)
                    
        for iid in items_to_del:
            if self.tree.exists(iid):
                self.items.pop(iid, None)
                self.tree.delete(iid)
                if self.current_iid == iid:
                    self.current_iid = None
                    if self.on_clear_canvas: self.on_clear_canvas()
                    
        self.update_preview()

    def on_tree_drag_start(self, event): 
        # 记录初始位置，不直接干扰选择逻辑
        self._drag_start_pos = (event.x, event.y)
        self.tree_drag_items = None

    def on_tree_drag_motion(self, event):
        # 只有当鼠标移动超过一定距离才认为是在“拖拽”
        if not hasattr(self, '_drag_start_pos'): return
        
        if self.tree_drag_items is None:
            dx = abs(event.x - self._drag_start_pos[0])
            dy = abs(event.y - self._drag_start_pos[1])
            if dx > 5 or dy > 5:
                iid = self.tree.identify_row(self._drag_start_pos[1])
                if not iid: return
                
                sel = self.tree.selection()
                # 如果拖动的这一行不在已选集合中，则仅拖动这一行
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
        self.tree_drag_items = None

    def on_tree_hover(self, event):
        if getattr(self, 'tree_drag_item', None): return
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

    def _get_item_index(self, target_iid):
        is_continuous = (self.num_rule_var.get() == "continuous")
        target_group = self.items[target_iid]['group']
        idx = 1
        if is_continuous:
            for grp_name in self.project_groups:
                grp_node = self.group_nodes[grp_name]
                for child in self.tree.get_children(grp_node):
                    if child == target_iid: return idx
                    if child in self.items: idx += 1
        else:
            grp_node = self.group_nodes[target_group]
            for child in self.tree.get_children(grp_node):
                if child == target_iid: return idx
                if child in self.items: idx += 1
        return idx

    def update_preview(self):
        self._apply_zebra_stripes()
        if not self.current_iid:
            self.text_preview.configure(state='normal')
            self.text_preview.delete('1.0', tk.END)
            self.text_preview.configure(state='disabled')
            return
            
        item = self.items[self.current_iid]
        real_idx = self._get_item_index(self.current_iid)
        text = get_export_text_for_item(item, real_idx, self.app_state_params['pts'])
        
        self.text_preview.configure(state='normal')
        self.text_preview.delete('1.0', tk.END)
        self.text_preview.insert(tk.END, text)
        self.text_preview.configure(state='disabled')

    def export_project(self):
        if not self.items: return messagebox.showwarning("提示", "没有可导出的数据。")
        out_file = filedialog.asksaveasfilename(
            title="导出全表数据", defaultextension=".txt", initialfile="tone_export_data",
            filetypes=[("CSV 表格", "*.csv"), ("文本文件", "*.txt"), ("所有文件", "*.*")]
        )
        if not out_file: return
        try:
            if out_file.lower().endswith(".csv"): self._export_csv(out_file)
            else: self._export_txt(out_file)
            messagebox.showinfo("成功", f"数据已导出至:\n{out_file}")
        except Exception as e: messagebox.showerror("错误", str(e))

    def _export_txt(self, out_file):
        is_continuous = (self.num_rule_var.get() == "continuous")
        with open(out_file, "w", encoding="utf-8") as f:
            global_idx = 1
            for grp_name in self.project_groups:
                if not is_continuous: global_idx = 1
                f.write(f"{grp_name}\n")
                grp_node = self.group_nodes[grp_name]
                for child in self.tree.get_children(grp_node):
                    if child in self.items:
                        item = self.items[child]
                        if item['start'] > 0:
                            txt_data = get_export_text_for_item(item, global_idx, self.app_state_params['pts'])
                            f.write(txt_data)
                            global_idx += 1

    def _export_csv(self, out_file):
        is_continuous = (self.num_rule_var.get() == "continuous")
        num_points = self.app_state_params['pts']
        headers = ["组别", "编号", "字", "时长(s)"]
        for i in range(1, num_points + 1):
            headers.append(f"T{i}(Hz)")
            
        with open(out_file, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(headers)
            global_idx = 1
            for grp_name in self.project_groups:
                if not is_continuous: global_idx = 1
                grp_node = self.group_nodes[grp_name]
                for child in self.tree.get_children(grp_node):
                    if child not in self.items: continue
                    item = self.items[child]
                    # 如果数据未加载，先尝试加载
                    if (not item.get('snd') or not item.get('pitch')) and item.get('path'):
                        try:
                            item['snd'] = parselmouth.Sound(item['path'])
                            item['pitch'] = item['snd'].to_pitch()
                        except: continue
                    
                    if item.get('start') <= 0 or not item.get('snd'): continue
                    
                    t_s, t_e = item['start'], item['end']
                    duration = t_e - t_s
                    if duration <= 0: continue
                    
                    row = [grp_name, global_idx, item['label'], f"{duration:.6f}"]
                    times = np.linspace(t_s, t_e, num_points)
                    for t in times:
                        f0 = item['pitch'].get_value_at_time(t)
                        f0_str = "" if np.isnan(f0) else f"{f0:.6f}"
                        row.append(f0_str)
                        
                    writer.writerow(row)
                    global_idx += 1
