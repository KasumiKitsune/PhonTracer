import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import customtkinter as ctk
import csv
import parselmouth
import numpy as np
import math
import matplotlib.pyplot as plt
from .data_utils import get_export_text_for_item
from .ui_widgets import CTkReleaseButton

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
        
        self.text_preview.tag_config("zero", foreground="#EF4444")
        start_idx = "1.0"
        while True:
            pos = self.text_preview.search("0.000000", start_idx, stopindex=tk.END)
            if not pos: break
            end_pos = f"{pos}+8c"
            self.text_preview.tag_add("zero", pos, end_pos)
            start_idx = end_pos
            
        self.text_preview.configure(state='disabled')
    def update_item_icon(self, iid):
        item = self.items.get(iid)
        if not item or item.get('start') is None: return
        has_empty = False
        num_points = self.app_state_params['pts']
        if (not item.get('snd') or not item.get('pitch')) and item.get('path'):
            if item.get('preview_f0'):
                has_empty = any(f == 0 for f in item['preview_f0'])
        else:
            if item.get('snd') and item.get('pitch'):
                times = np.linspace(item['start'], item['end'], num_points)
                has_empty = any(np.isnan(item['pitch'].get_value_at_time(t)) or item['pitch'].get_value_at_time(t) == 0 for t in times)
        
        img = self.tk_icons.get('warning', '') if has_empty else ''
        try:
            self.tree.item(iid, image=img)
        except tk.TclError:
            pass

    def export_project(self):
        if not self.items: return messagebox.showwarning("提示", "没有可导出的数据。")
        
        empty_labels = []
        num_points = self.app_state_params['pts']
        for grp_name in self.project_groups:
            grp_node = self.group_nodes[grp_name]
            for child in self.tree.get_children(grp_node):
                if child in self.items:
                    item = self.items[child]
                    if item.get('start') is None: continue
                    has_empty = False
                    if (not item.get('snd') or not item.get('pitch')) and item.get('path'):
                        if item.get('preview_f0'):
                            has_empty = any(f == 0 for f in item['preview_f0'])
                    else:
                        if item.get('snd') and item.get('pitch'):
                            times = np.linspace(item['start'], item['end'], num_points)
                            has_empty = any(np.isnan(item['pitch'].get_value_at_time(t)) or item['pitch'].get_value_at_time(t) == 0 for t in times)
                    if has_empty:
                        empty_labels.append(f"[{grp_name}] {item['label']}")
        
        if empty_labels:
            msg = "以下项目的基频数据包含 0 值（可能无法提取有效声调）：\n\n"
            msg += "\n".join(empty_labels[:10])
            if len(empty_labels) > 10: msg += f"\n... 等共 {len(empty_labels)} 项"
            msg += "\n\n是否继续导出？"
            if not messagebox.askyesno("空数据警告", msg):
                return
                
        # 弹出导出选择菜单
        self._show_export_menu()

    def _show_export_menu(self):
        """弹出导出格式选择对话框"""
        dlg = ctk.CTkToplevel(self.parent)
        dlg.title("选择导出格式")
        dlg.geometry("320x320")
        dlg.attributes('-topmost', True)
        dlg.resizable(False, False)
        
        # 居中显示
        dlg.update_idletasks()
        main_win = self.parent.winfo_toplevel()
        x = main_win.winfo_rootx() + (main_win.winfo_width() - 320) // 2
        y = main_win.winfo_rooty() + (main_win.winfo_height() - 320) // 2
        dlg.geometry(f"+{x}+{y}")
        
        ctk.CTkLabel(dlg, text="请选择导出格式", font=self.font_title, text_color="#111827").pack(pady=(20, 15))
        
        btn_kwargs = {"corner_radius": 12, "height": 44, "font": self.font_main, "anchor": "w", "compound": "left"}
        
        def do_export(mode):
            dlg.destroy()
            if mode == 'txt':
                out_file = filedialog.asksaveasfilename(title="导出文本", defaultextension=".txt", initialfile="tone_export_data", filetypes=[("文本文件", "*.txt")])
                if not out_file: return
                try:
                    self._export_txt(out_file)
                    messagebox.showinfo("成功", f"数据已导出至:\n{out_file}")
                except Exception as e: messagebox.showerror("错误", str(e))
            elif mode == 'xlsx':
                out_file = filedialog.asksaveasfilename(title="导出Excel", defaultextension=".xlsx", initialfile="tone_export_data", filetypes=[("Excel 表格", "*.xlsx")])
                if not out_file: return
                try:
                    include_chart = messagebox.askyesno("导出设置", "是否在 Excel 中包含分析图表？\n(包含图表可能在部分旧版 Office 中打开较慢)", default=messagebox.NO)
                    self._export_xlsx(out_file, include_chart=include_chart)
                    messagebox.showinfo("成功", f"数据已导出至:\n{out_file}")
                except Exception as e: messagebox.showerror("错误", str(e))
            elif mode == 'line_chart':
                out_file = filedialog.asksaveasfilename(title="导出折线图", defaultextension=".png", initialfile="tone_line_chart", filetypes=[("PNG 图片", "*.png"), ("SVG 矢量图", "*.svg"), ("PDF 文档", "*.pdf")])
                if not out_file: return
                try:
                    self._export_line_chart(out_file)
                    messagebox.showinfo("成功", f"图表已导出至:\n{out_file}")
                except Exception as e: messagebox.showerror("错误", str(e))
            elif mode == 'kde':
                out_file = filedialog.asksaveasfilename(title="导出KDE热力图", defaultextension=".png", initialfile="tone_kde_heatmap", filetypes=[("PNG 图片", "*.png"), ("SVG 矢量图", "*.svg"), ("PDF 文档", "*.pdf")])
                if not out_file: return
                try:
                    self._export_kde_heatmap(out_file)
                    messagebox.showinfo("成功", f"热力图已导出至:\n{out_file}")
                except Exception as e: messagebox.showerror("错误", str(e))
        
        ctk.CTkButton(dlg, text="  📄  文本文件 (.txt)", command=lambda: do_export('txt'), fg_color="#F3F4F6", text_color="#374151", hover_color="#E5E7EB", **btn_kwargs).pack(fill=tk.X, padx=25, pady=4)
        ctk.CTkButton(dlg, text="  📊  Excel 表格 (.xlsx)", command=lambda: do_export('xlsx'), fg_color="#ECFDF5", text_color="#047857", hover_color="#D1FAE5", **btn_kwargs).pack(fill=tk.X, padx=25, pady=4)
        ctk.CTkButton(dlg, text="  📈  声调格局折线图", command=lambda: do_export('line_chart'), fg_color="#EFF6FF", text_color="#1E40AF", hover_color="#DBEAFE", **btn_kwargs).pack(fill=tk.X, padx=25, pady=4)
        ctk.CTkButton(dlg, text="  🔥  KDE 密度热力图", command=lambda: do_export('kde'), fg_color="#FFF7ED", text_color="#9A3412", hover_color="#FFEDD5", **btn_kwargs).pack(fill=tk.X, padx=25, pady=4)

    def _export_xlsx(self, out_file, include_chart=False):
        try:
            import xlsxwriter
        except ImportError:
            messagebox.showerror("错误", "缺少 xlsxwriter 库，请先安装：pip install xlsxwriter")
            return
            
        is_continuous = (self.num_rule_var.get() == "continuous")
        num_points = self.app_state_params['pts']
        
        workbook = xlsxwriter.Workbook(out_file)
        ws_data = workbook.add_worksheet("数据")
        ws_res = workbook.add_worksheet("分析结果")
        
        headers = ["组别", "编号", "字", "时长(s)"]
        for i in range(1, num_points + 1):
            headers.append(f"T{i}(Hz)")
            
        for col, header in enumerate(headers):
            ws_data.write(0, col, header)
            
        global_idx = 1
        row_idx = 1
        raw_data = [] 
        
        for grp_name in self.project_groups:
            if not is_continuous: global_idx = 1
            grp_node = self.group_nodes[grp_name]
            for child in self.tree.get_children(grp_node):
                if child not in self.items: continue
                item = self.items[child]
                
                if (not item.get('snd') or not item.get('pitch')) and item.get('path'):
                    try:
                        item['snd'] = parselmouth.Sound(item['path'])
                        item['pitch'] = item['snd'].to_pitch()
                    except Exception: continue
                    
                if item.get('start') is None or not item.get('snd'): continue
                
                t_s, t_e = item['start'], item['end']
                duration = t_e - t_s
                if duration <= 0: continue
                
                row = [grp_name, global_idx, item['label'], float(f"{duration:.6f}")]
                times = np.linspace(t_s, t_e, num_points)
                hz_vals = []
                for t in times:
                    f0 = item['pitch'].get_value_at_time(t)
                    hz_vals.append(f0)
                    row.append("" if np.isnan(f0) else float(f"{f0:.6f}"))
                    
                for col, val in enumerate(row):
                    ws_data.write(row_idx, col, val)
                
                raw_data.append([grp_name, duration] + hz_vals)
                row_idx += 1
                global_idx += 1
                
        all_hz = [hz for r in raw_data for hz in r[2:] if not np.isnan(hz) and hz > 0]
        if not all_hz:
            workbook.close()
            return
            
        res_headers = ["声调类型"]
        for i in range(1, num_points + 1): res_headers.append(f"T{i}")
        res_headers.append("平均时长(s)")
        for col, header in enumerate(res_headers): ws_res.write(0, col, header)
            
        dict_data = {}
        tone_counts = {}
        
        for r in raw_data:
            tone_name = r[0]
            if not tone_name: continue
            if tone_name not in dict_data:
                dict_data[tone_name] = [0.0] * (num_points + 1)
                tone_counts[tone_name] = 0
            dict_data[tone_name][0] += r[1]
            for i in range(num_points):
                val = r[i+2]
                if not np.isnan(val) and val > 0: dict_data[tone_name][i+1] += val
            tone_counts[tone_name] += 1
            
        # 第一步：计算各组的所有均值点
        avg_points = {}
        all_avg_hz = []
        for k, v in dict_data.items():
            count = tone_counts[k]
            avg_points[k] = []
            for j in range(1, num_points + 1):
                avg_hz = v[j] / count if count > 0 else 0
                avg_points[k].append(avg_hz)
                if avg_hz > 0: all_avg_hz.append(avg_hz)
                
        if not all_avg_hz:
            workbook.close()
            return
            
        # 根据实验语音学“声调格局图”规范，使用均值折线中的最大和最小值进行对数归一化
        min_hz = min(all_avg_hz)
        max_hz = max(all_avg_hz)
        
        res_row = 1
        for k, v in dict_data.items():
            ws_res.write(res_row, 0, k)
            count = tone_counts[k]
            for j in range(num_points):
                avg_hz = avg_points[k][j]
                if avg_hz > 0 and max_hz > min_hz and min_hz > 0:
                    t_val = 5 * (math.log10(avg_hz) - math.log10(min_hz)) / (math.log10(max_hz) - math.log10(min_hz))
                    ws_res.write(res_row, j + 1, round(t_val, 2))
                else: ws_res.write(res_row, j + 1, "")
            avg_dur = v[0] / count if count > 0 else 0
            ws_res.write(res_row, num_points + 1, round(avg_dur, 4))
            res_row += 1
            
        if include_chart:
            chart = workbook.add_chart({'type': 'scatter', 'subtype': 'straight'})
            for i in range(1, res_row):
                chart.add_series({
                    'name':       ['分析结果', i, 0],
                    'categories': ['分析结果', 0, 1, 0, num_points],
                    'values':     ['分析结果', i, 1, i, num_points],
                })
            chart.set_title({'name': '声调格局图 (0-5标度)'})
            chart.set_y_axis({'min': 0, 'max': 5, 'major_unit': 0.5})
            chart.set_legend({'position': 'right'})
            ws_res.insert_chart('O2', chart)
        
        workbook.close()

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
                        if item['start'] is not None:
                            txt_data = get_export_text_for_item(item, global_idx, self.app_state_params['pts'])
                            f.write(txt_data)
                            global_idx += 1

    def _collect_group_avg_data(self):
        """收集各组均值数据，返回 (group_name, t_values_list) 和 min/max_hz"""
        num_points = self.app_state_params['pts']
        raw_data = []
        for grp_name in self.project_groups:
            grp_node = self.group_nodes[grp_name]
            for child in self.tree.get_children(grp_node):
                if child not in self.items: continue
                item = self.items[child]
                if (not item.get('snd') or not item.get('pitch')) and item.get('path'):
                    try:
                        item['snd'] = parselmouth.Sound(item['path'])
                        item['pitch'] = item['snd'].to_pitch()
                    except Exception: continue
                if item.get('start') is None or not item.get('snd'): continue
                t_s, t_e = item['start'], item['end']
                if t_e - t_s <= 0: continue
                times = np.linspace(t_s, t_e, num_points)
                hz_vals = [item['pitch'].get_value_at_time(t) for t in times]
                raw_data.append([grp_name] + hz_vals)

        dict_data, tone_counts = {}, {}
        for r in raw_data:
            name = r[0]
            if name not in dict_data:
                dict_data[name] = [0.0] * num_points
                tone_counts[name] = 0
            for i in range(num_points):
                val = r[i+1]
                if not np.isnan(val) and val > 0: dict_data[name][i] += val
            tone_counts[name] += 1

        avg_points = {}
        all_avg_hz = []
        for k, v in dict_data.items():
            count = tone_counts[k]
            avg_points[k] = [v[j] / count if count > 0 else 0 for j in range(num_points)]
            all_avg_hz.extend([h for h in avg_points[k] if h > 0])

        if not all_avg_hz: return None
        min_hz, max_hz = min(all_avg_hz), max(all_avg_hz)

        result = {}
        for k, avgs in avg_points.items():
            t_vals = []
            for h in avgs:
                if h > 0 and max_hz > min_hz and min_hz > 0:
                    t_vals.append(5 * (math.log10(h) - math.log10(min_hz)) / (math.log10(max_hz) - math.log10(min_hz)))
                else:
                    t_vals.append(None)
            result[k] = t_vals
        return result

    def _export_line_chart(self, out_file):
        """导出声调格局折线图 (PNG/SVG/PDF)"""
        data = self._collect_group_avg_data()
        if not data: return messagebox.showwarning("提示", "没有有效数据可供绘图。")

        num_points = self.app_state_params['pts']
        plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'Arial Unicode MS']
        plt.rcParams['axes.unicode_minus'] = False

        fig, ax = plt.subplots(figsize=(10, 6))
        x_vals = list(range(1, num_points + 1))

        colors = ['#2563EB', '#DC2626', '#16A34A', '#9333EA', '#EA580C', '#0891B2', '#CA8A04', '#6366F1']
        for i, (name, t_vals) in enumerate(data.items()):
            valid_x = [x for x, v in zip(x_vals, t_vals) if v is not None]
            valid_y = [v for v in t_vals if v is not None]
            if valid_x:
                ax.plot(valid_x, valid_y, '-o', color=colors[i % len(colors)], linewidth=2, markersize=5, label=name)

        ax.set_ylim(0, 5)
        ax.set_xlim(0.5, num_points + 0.5)
        ax.set_xlabel('测量点', fontsize=12)
        ax.set_ylabel('T 值 (0-5 标度)', fontsize=12)
        ax.set_title('声调格局图', fontsize=16, fontweight='bold')
        ax.legend(loc='best', fontsize=10)
        ax.grid(True, alpha=0.3)
        ax.set_yticks([0, 0.5, 1, 1.5, 2, 2.5, 3, 3.5, 4, 4.5, 5])

        fig.tight_layout()
        fig.savefig(out_file, dpi=300, bbox_inches='tight')
        plt.close(fig)

    def _export_kde_heatmap(self, out_file):
        """导出 KDE 密度热力图：直接从音频提取高密度 F0，按组绘制 KDE"""
        import seaborn as sns
        from scipy.interpolate import interp1d
        from scipy.signal import savgol_filter

        N_DENSE = 100  # 高密度采样点
        group_contours = {}  # {grp_name: [normalized_contour_array, ...]}

        # 创建进度条弹窗
        prog_dlg = ctk.CTkToplevel(self.parent)
        prog_dlg.title("正在导出")
        prog_dlg.geometry("300x120")
        prog_dlg.attributes('-topmost', True)
        prog_dlg.resizable(False, False)
        
        prog_dlg.update_idletasks()
        main_win = self.parent.winfo_toplevel()
        px = main_win.winfo_rootx() + (main_win.winfo_width() - 300) // 2
        py = main_win.winfo_rooty() + (main_win.winfo_height() - 120) // 2
        prog_dlg.geometry(f"+{px}+{py}")
        
        lbl_status = ctk.CTkLabel(prog_dlg, text="正在处理数据，请稍候...", font=self.font_main)
        lbl_status.pack(pady=(20, 5))
        pbar = ctk.CTkProgressBar(prog_dlg, width=250)
        pbar.pack()
        pbar.set(0)
        prog_dlg.update()

        # 收集全局 min/max F0 用于归一化
        all_raw_f0 = []
        
        total_items = sum(len(self.tree.get_children(self.group_nodes[g])) for g in self.project_groups)
        processed = 0

        for grp_name in self.project_groups:
            grp_node = self.group_nodes[grp_name]
            group_contours[grp_name] = []
            for child in self.tree.get_children(grp_node):
                processed += 1
                # 提高颗粒度：每处理一个就更新进度，且数据处理占总进度的 70%
                pbar.set(0.7 * (processed / total_items))
                prog_dlg.update()
                    
                if child not in self.items: continue
                item = self.items[child]
                # 确保音频已加载
                if (not item.get('snd') or not item.get('pitch')) and item.get('path'):
                    try:
                        item['snd'] = parselmouth.Sound(item['path'])
                        item['pitch'] = item['snd'].to_pitch(
                            pitch_floor=self.app_state_params.get('pitch_floor', 75),
                            pitch_ceiling=self.app_state_params.get('pitch_ceiling', 600)
                        )
                    except Exception: continue
                if item.get('start') is None or not item.get('snd') or not item.get('pitch'): continue

                t_s, t_e = item['start'], item['end']
                if t_e - t_s <= 0: continue

                pitch = item['pitch']
                xs = pitch.xs()
                freqs = pitch.selected_array['frequency']
                mask = (xs >= t_s) & (xs <= t_e) & (freqs > 0)
                valid_freqs = freqs[mask]

                if len(valid_freqs) < 3: continue

                # Savitzky-Golay 平滑
                win = len(valid_freqs) // 3
                if win % 2 == 0: win += 1
                if win < 3: win = 3
                if len(valid_freqs) > win:
                    smoothed = savgol_filter(valid_freqs, win, 2)
                else:
                    smoothed = valid_freqs

                # 插值到 N_DENSE 点
                x_orig = np.linspace(0, 1, len(smoothed))
                f_interp = interp1d(x_orig, smoothed, kind='linear')
                contour = f_interp(np.linspace(0, 1, N_DENSE))

                group_contours[grp_name].append(contour)
                all_raw_f0.extend(contour.tolist())

        if not all_raw_f0:
            return messagebox.showwarning("提示", "没有有效的音频数据可供绘制热力图。")

        # 使用各组均值的 min/max 做归一化（与折线图一致）
        group_means = {}
        all_mean_vals = []
        for name, contours in group_contours.items():
            if contours:
                mean_contour = np.mean(contours, axis=0)
                group_means[name] = mean_contour
                all_mean_vals.extend(mean_contour.tolist())
        
        if not all_mean_vals:
            return messagebox.showwarning("提示", "没有有效数据可供绘制热力图。")
            
        min_f0 = min(all_mean_vals)
        max_f0 = max(all_mean_vals)

        lbl_status.configure(text="正在进行数据归一化...")
        pbar.set(0.75)
        prog_dlg.update()

        def hz_to_5_scale(hz):
            # 取消 clip 限制，让自然数据延伸出 0-5 边界，防止 KDE 在边界处发生密度截断堆积
            if max_f0 == min_f0: return 3.0
            return 5 * (np.log(hz) - np.log(min_f0)) / (np.log(max_f0) - np.log(min_f0))

        # 对所有 contour 做归一化
        norm_contours = {}
        for name, contours in group_contours.items():
            norm_contours[name] = [np.array([hz_to_5_scale(h) for h in c]) for c in contours]
            
        pbar.set(0.8)
        prog_dlg.update()

        # 绘图
        plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'Arial Unicode MS']
        plt.rcParams['axes.unicode_minus'] = False

        groups_with_data = [g for g in self.project_groups if norm_contours.get(g)]
        n_groups = len(groups_with_data)
        if n_groups == 0:
            prog_dlg.destroy()
            return messagebox.showwarning("提示", "没有有效数据可供绘制热力图。")

        n_cols = min(4, n_groups)
        n_rows = math.ceil(n_groups / n_cols)
        fig, axes = plt.subplots(n_rows, n_cols, figsize=(5 * n_cols, 5 * n_rows), squeeze=False, sharex=True, sharey=True)
        axes_flat = axes.flatten()

        x_vals = np.linspace(0, 100, N_DENSE)
        for idx, grp_name in enumerate(groups_with_data):
            lbl_status.configure(text=f"正在绘制 {grp_name} ({idx+1}/{n_groups})...")
            # 绘图过程占剩余的 20%
            pbar.set(0.8 + 0.2 * (idx / n_groups))
            prog_dlg.update()
            
            ax = axes_flat[idx]
            contours = norm_contours[grp_name]
            X = np.tile(x_vals, len(contours))
            Y = np.concatenate(contours)
            sns.kdeplot(x=X, y=Y, fill=True, cmap="YlOrRd", bw_adjust=0.5, ax=ax, thresh=0.05)
            for c in contours:
                ax.plot(x_vals, c, color="black", alpha=0.05, linewidth=0.5)
            ax.set_title(grp_name, fontsize=14)
            # 扩展 y 轴显示范围，容纳未 clip 的自然极值点
            ax.set_ylim(-1, 6)
            ax.set_xlim(0, 100)
            ax.set_yticks([-1, 0, 1, 2, 3, 4, 5, 6])
            ax.set_ylabel('T 值')
            ax.set_xlabel('归一化时间 (%)')

        # 隐藏多余的子图
        for idx in range(n_groups, len(axes_flat)):
            axes_flat[idx].set_visible(False)

        fig.suptitle('声调 KDE 密度热力图', fontsize=18, fontweight='bold', y=1.02)
        fig.tight_layout()
        fig.savefig(out_file, dpi=300, bbox_inches='tight')
        plt.close(fig)
        prog_dlg.destroy()
