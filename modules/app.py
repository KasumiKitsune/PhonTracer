import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import customtkinter as ctk
import parselmouth
import numpy as np
import sounddevice as sd
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import os
import csv
import threading
import concurrent.futures
from PIL import Image

# 导入拆分后的模块
from modules.ui_widgets import ToolTip
from modules.data_utils import parse_wordlist, fuzzy_match_word_to_path, get_export_text_for_item
from modules.audio_core import core_microscopic_vowel_nucleus, batch_process_worker, macroscopic_vad
from modules.visual_splitter import VisualSplitter

class PhoneticsApp:
    def __init__(self, root):
        self.root = root
        self.root.title("PhonTracer - 声调提取与分析工具")
        self.root.geometry("1200x700")
        self.root.configure(fg_color="#F3F4F6") 
        
        # 设置窗口图标
        try:
            icon_file = os.path.join("assets", "icon.ico")
            if not os.path.exists(icon_file):
                # 如果是作为模块运行，图标在上一级目录
                icon_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets", "icon.ico")
            if os.path.exists(icon_file):
                self.root.iconbitmap(icon_file)
        except Exception:
            pass
        
        self.pending_long_snd = None 
        self.pending_batch_paths = []
        self.project_groups =[]     
        self.group_nodes = {}        
        self.items = {}              
        self.current_iid = None  
        self.dragging = None 
        self.tree_drag_item = None 
        self.debounce_timer = None
        self.ax = None
        self.ax2 = None
        
        self.last_params = {
            'pts': 11,
            'db': 60.0,
            'dur': 0.04
        }
        
        self.font_title = ctk.CTkFont(family="Microsoft YaHei", size=15, weight="bold")
        self.font_main = ctk.CTkFont(family="Microsoft YaHei", size=13)
        self.font_code = ctk.CTkFont(family="Consolas", size=13)

        self.setup_icons()
        self.setup_ui()
        
    def setup_icons(self):
        # 预加载所有图标
        icon_path = "icons"
        if not os.path.exists(icon_path):
            icon_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "icons")
            
        self.icons = {}
        icon_files = {
            "audio": "audio_file.png", "cut": "cut.png", "batch": "batch.png",
            "magic": "magic.png", "list": "list.png", "plus": "plus.png",
            "play": "play.png", "save": "save.png", "check": "check.png",
            "auto": "auto.png", "points": "points.png", "energy": "energy.png",
            "duration": "duration.png", "trim": "trim.png", "tag": "tag.png",
            "tab_single": "tab_single.png", "tab_batch": "tab_batch.png",
            "status_success": "status_success.png", 
            "status_loading": "status_loading.png", 
            "status_error": "status_error.png"
        }
        for key, filename in icon_files.items():
            path = os.path.join(icon_path, filename)
            if os.path.exists(path):
                img = Image.open(path)
                self.icons[key] = ctk.CTkImage(light_image=img, dark_image=img, size=(20, 20))
            else:
                self.icons[key] = None

        logo_path = os.path.join("assets", "icon.png")
        if not os.path.exists(logo_path):
            logo_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets", "icon.png")
        if os.path.exists(logo_path):
            img = Image.open(logo_path)
            self.icons["logo"] = ctk.CTkImage(light_image=img, dark_image=img, size=(45, 45))

        brand_logo_path = os.path.join("assets", "logo.png")
        if not os.path.exists(brand_logo_path):
            brand_logo_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets", "logo.png")
        if os.path.exists(brand_logo_path):
            img = Image.open(brand_logo_path)
            orig_w, orig_h = img.size
            target_h = 60
            target_w = int(orig_w * (target_h / orig_h))
            self.icons["brand_logo"] = ctk.CTkImage(light_image=img, dark_image=img, size=(target_w, target_h))

    def setup_ui(self):
        left_scrollable = ctk.CTkScrollableFrame(self.root, width=320, fg_color="transparent")
        left_scrollable.pack(side=tk.LEFT, fill=tk.Y, padx=10, pady=10)
        btn_kwargs_primary = {"corner_radius": 20, "height": 38, "font": self.font_main}
        btn_kwargs_secondary = {"corner_radius": 20, "height": 38, "font": self.font_main, 
                                "fg_color": "#E5E7EB", "text_color": "#1F2937", "hover_color": "#D1D5DB"}
        
        header_frame = ctk.CTkFrame(left_scrollable, fg_color="transparent")
        header_frame.pack(fill=tk.X, pady=(10, 20))
        
        if self.icons.get("brand_logo"):
            logo_lbl = ctk.CTkLabel(header_frame, text="", image=self.icons.get("brand_logo"))
            logo_lbl.pack(side=tk.LEFT, padx=(10, 15))
        else:
            logo_lbl = ctk.CTkLabel(header_frame, text="PhonTracer", font=ctk.CTkFont(family="Segoe UI", size=26, weight="bold"), text_color="#1F2937")
            logo_lbl.pack(side=tk.LEFT, padx=(10, 15))
            
        status_container = ctk.CTkFrame(header_frame, fg_color="transparent")
        status_container.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        self.lbl_status = ctk.CTkLabel(status_container, text=" 就绪", image=self.icons.get("status_success"), compound="left", text_color="#10B981", font=ctk.CTkFont(family="Microsoft YaHei", size=12), wraplength=120)
        self.lbl_status.pack(pady=(5, 5), expand=True)
        
        self.progress_bar = ctk.CTkProgressBar(status_container, height=6, corner_radius=10, 
                                               progress_color="#60A5FA", fg_color="#E5E7EB")
        self.progress_bar.set(0)
        # 初始时不 pack 进度条

        self.tabview = ctk.CTkTabview(left_scrollable, height=250, corner_radius=12, fg_color="white", 
                                      segmented_button_selected_color="#60A5FA", segmented_button_fg_color="#F3F4F6")
        self.tabview.pack(fill=tk.X, pady=(0, 10))
        tab_long = self.tabview.add("单条长音频")
        tab_batch = self.tabview.add("多条独立音频")
        
        self.tabview._segmented_button._buttons_dict["单条长音频"].configure(image=self.icons.get("tab_single"), compound="left")
        self.tabview._segmented_button._buttons_dict["多条独立音频"].configure(image=self.icons.get("tab_batch"), compound="left")

        ctk.CTkButton(tab_long, text=" 导入长音频", image=self.icons.get("audio"), compound="left", command=self.load_long_audio, **btn_kwargs_primary).pack(fill=tk.X, padx=10, pady=(15, 2))
        self.lbl_long_file = ctk.CTkLabel(tab_long, text="未选择", font=self.font_main, text_color="#6B7280")
        self.lbl_long_file.pack(pady=(0, 10))
        ctk.CTkButton(tab_long, text=" 导入字表并切分", image=self.icons.get("cut"), compound="left", command=lambda: self.open_text_dialog('long'), **btn_kwargs_secondary).pack(fill=tk.X, padx=10, pady=(0, 5))
        ctk.CTkButton(tab_long, text=" 可视化手动切分", image=self.icons.get("magic"), compound="left", command=self.open_visual_splitter, **btn_kwargs_secondary).pack(fill=tk.X, padx=10, pady=(0, 15))

        ctk.CTkButton(tab_batch, text=" 选择多个音频文件", image=self.icons.get("batch"), compound="left", command=self.load_batch_audio, **btn_kwargs_primary).pack(fill=tk.X, padx=10, pady=(15, 2))
        self.lbl_batch_files = ctk.CTkLabel(tab_batch, text="未选择", font=self.font_main, text_color="#6B7280")
        self.lbl_batch_files.pack(pady=(0, 10))
        row_mode2_btns = ctk.CTkFrame(tab_batch, fg_color="transparent")
        row_mode2_btns.pack(fill=tk.X, padx=10, pady=(0, 15))
        ctk.CTkButton(row_mode2_btns, text="文件名提取", image=self.icons.get("tag"), compound="left", command=self.process_batch_direct, **btn_kwargs_secondary, width=110).pack(side=tk.LEFT, expand=True, padx=(0, 5))
        ctk.CTkButton(row_mode2_btns, text="导入字表", image=self.icons.get("list"), compound="left", command=lambda: self.open_text_dialog('batch'), **btn_kwargs_secondary, width=110).pack(side=tk.RIGHT, expand=True, padx=(5, 0))

        card_params = ctk.CTkFrame(left_scrollable, fg_color="white", corner_radius=10)
        card_params.pack(fill=tk.X, pady=10)
        ctk.CTkLabel(card_params, text="全局算法与导出参数", font=self.font_title, text_color="#111827").pack(anchor=tk.W, padx=15, pady=(15, 5))
        
        row_pts = ctk.CTkFrame(card_params, fg_color="transparent")
        row_pts.pack(fill=tk.X, padx=15, pady=5)
        lbl_pts = ctk.CTkLabel(row_pts, text=" 等分点 (N):", image=self.icons.get("points"), compound="left", text_color="#374151", font=self.font_main)
        lbl_pts.pack(side=tk.LEFT)
        self.slider_pts = ctk.CTkSlider(row_pts, from_=5, to=20, number_of_steps=15, width=100, height=16, 
                                        command=lambda v: self._on_slider_change(v, self.entry_points, 'pts'))
        self.slider_pts.set(self.last_params['pts'])
        self.slider_pts.pack(side=tk.LEFT, padx=10)
        self.entry_points = ctk.CTkEntry(row_pts, width=60, justify="center", corner_radius=20, height=26)
        self.entry_points.insert(0, str(self.last_params['pts'])) 
        self.entry_points.pack(side=tk.RIGHT)
        self.setup_entry_behavior(self.entry_points, 'pts')

        row_db = ctk.CTkFrame(card_params, fg_color="transparent")
        row_db.pack(fill=tk.X, padx=15, pady=5)
        lbl_db = ctk.CTkLabel(row_db, text=" 能量落差:", image=self.icons.get("energy"), compound="left", text_color="#374151", font=self.font_main)
        lbl_db.pack(side=tk.LEFT)
        self.slider_db = ctk.CTkSlider(row_db, from_=10, to=100, number_of_steps=90, width=100, height=16,
                                       command=lambda v: self._on_slider_change(v, self.entry_drop_db, 'db'))
        self.slider_db.set(self.last_params['db'])
        self.slider_db.pack(side=tk.LEFT, padx=10)
        self.var_drop_db = ctk.StringVar(value=str(self.last_params['db']))
        self.entry_drop_db = ctk.CTkEntry(row_db, textvariable=self.var_drop_db, width=60, justify="center", corner_radius=20, height=26)
        self.entry_drop_db.pack(side=tk.RIGHT)
        self.setup_entry_behavior(self.entry_drop_db, 'db')

        row_dur = ctk.CTkFrame(card_params, fg_color="transparent")
        row_dur.pack(fill=tk.X, padx=15, pady=5)
        lbl_dur = ctk.CTkLabel(row_dur, text=" 最短时长:", image=self.icons.get("duration"), compound="left", text_color="#374151", font=self.font_main)
        lbl_dur.pack(side=tk.LEFT)
        self.slider_dur = ctk.CTkSlider(row_dur, from_=0.01, to=0.5, number_of_steps=49, width=100, height=16,
                                        command=lambda v: self._on_slider_change(v, self.entry_min_dur, 'dur'))
        self.slider_dur.set(self.last_params['dur'])
        self.slider_dur.pack(side=tk.LEFT, padx=10)
        self.var_min_dur = ctk.StringVar(value=str(self.last_params['dur']))
        self.entry_min_dur = ctk.CTkEntry(row_dur, textvariable=self.var_min_dur, width=60, justify="center", corner_radius=20, height=26)
        self.entry_min_dur.pack(side=tk.RIGHT)
        self.setup_entry_behavior(self.entry_min_dur, 'dur')
        
        row_trim = ctk.CTkFrame(card_params, fg_color="transparent")
        row_trim.pack(fill=tk.X, padx=15, pady=(10, 15))
        self.lbl_trim_icon = ctk.CTkLabel(row_trim, text="", image=self.icons.get("trim"))
        self.lbl_trim_icon.pack(side=tk.LEFT, padx=(0, 5))
        self.switch_trim_silence = ctk.CTkSwitch(row_trim, text="开启边缘静音裁切", font=self.font_main, 
                                                 progress_color="#10B981", text_color="#374151", command=self.on_trim_silence_toggle)
        self.switch_trim_silence.pack(side=tk.LEFT)
        self.switch_trim_silence.select() 
        ToolTip(self.switch_trim_silence, "开启后将在图表上自动忽略首尾低于 -50dB 的绝对静音区域，\n让有效波形占满屏幕。")


        right_sidebar = ctk.CTkFrame(self.root, width=300, fg_color="transparent")
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
        self.tree = ttk.Treeview(tree_container, show='tree')
        scroll_tree = ctk.CTkScrollbar(tree_container, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll_tree.set)
        scroll_tree.pack(side=tk.RIGHT, fill=tk.Y, padx=(5, 0))
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        self.drag_indicator = tk.Frame(self.tree, height=2, bg="#3B82F6") 
        self.tree.tag_configure('hover', background='#F3F4F6')
        self.tree.tag_configure('drag_target', background='#DBEAFE')
        
        btn_add_group = ctk.CTkButton(frame_list, text=" 新增组", image=self.icons.get("plus"), compound="left", width=120, height=30, corner_radius=8, command=self.add_new_group, fg_color="#F3F4F6", text_color="#374151", hover_color="#E5E7EB")
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

        center_frame = ctk.CTkFrame(self.root, fg_color="white", corner_radius=10)
        center_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, pady=10)
        
        top_bar = ctk.CTkFrame(center_frame, fg_color="transparent")
        top_bar.pack(side=tk.TOP, fill=tk.X, padx=15, pady=(15, 5))
        
        frame_tune = ctk.CTkFrame(top_bar, fg_color="#F9FAFB", corner_radius=8)
        frame_tune.pack(side=tk.TOP, fill=tk.X, pady=(0, 10))
        ctk.CTkLabel(frame_tune, text="当前区间(s):", font=self.font_title, text_color="#111827").pack(side=tk.LEFT, padx=(10, 10), pady=10)
        ctk.CTkLabel(frame_tune, text=" 起:", image=self.icons.get("play"), compound="left").pack(side=tk.LEFT)
        self.var_t_start = ctk.StringVar(value="0.000")
        self.entry_t_start = ctk.CTkEntry(frame_tune, textvariable=self.var_t_start, width=70, corner_radius=20, height=28)
        self.entry_t_start.pack(side=tk.LEFT, padx=(5, 10))
        self.setup_entry_behavior(self.entry_t_start, 'start_manual')
        ctk.CTkLabel(frame_tune, text="止:").pack(side=tk.LEFT)
        self.var_t_end = ctk.StringVar(value="0.000")
        self.entry_t_end = ctk.CTkEntry(frame_tune, textvariable=self.var_t_end, width=70, corner_radius=20, height=28)
        self.entry_t_end.pack(side=tk.LEFT, padx=(5, 15))
        self.setup_entry_behavior(self.entry_t_end, 'end_manual')
        
        frame_actions = ctk.CTkFrame(top_bar, fg_color="transparent")
        frame_actions.pack(side=tk.TOP, fill=tk.X, pady=(5, 0))
        
        ctk.CTkButton(frame_actions, text="应用", image=self.icons.get("check"), compound="left", command=self.apply_manual_time, corner_radius=20, height=36, width=110, fg_color="#E5E7EB", text_color="#1F2937", hover_color="#D1D5DB").pack(side=tk.LEFT, padx=(0, 10))
        ctk.CTkButton(frame_actions, text="自动识别", image=self.icons.get("auto"), compound="left", command=self.apply_auto_detect, corner_radius=20, height=36, width=110, fg_color="#FCE7F3", text_color="#BE185D", hover_color="#FBCFE8").pack(side=tk.LEFT, padx=(0, 20))
        
        ctk.CTkButton(frame_actions, text=" 试听", image=self.icons.get("play"), compound="left", command=self.play_selected, font=ctk.CTkFont(family="Microsoft YaHei", size=13, weight="bold"), corner_radius=20, height=36, width=60, fg_color="#E5E7EB", text_color="#1F2937", hover_color="#D1D5DB").pack(side=tk.LEFT, padx=(0, 10))
        ctk.CTkButton(frame_actions, text=" 导出", image=self.icons.get("save"), compound="left", command=self.export_project, font=ctk.CTkFont(family="Microsoft YaHei", size=13, weight="bold"), corner_radius=20, height=36, width=60, fg_color="#10B981", hover_color="#059669").pack(side=tk.LEFT)

        self.fig = plt.Figure(figsize=(7, 5), facecolor='white') 
        self.canvas = FigureCanvasTkAgg(self.fig, master=center_frame)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        self.canvas.mpl_connect('button_press_event', self.on_press)
        self.canvas.mpl_connect('motion_notify_event', self.on_motion)
        self.canvas.mpl_connect('button_release_event', self.on_release)

    def _on_slider_change(self, value, entry, key):
        if key == 'pts':
            ival = int(value)
            entry.delete(0, tk.END)
            entry.insert(0, str(ival))
        else:
            entry.delete(0, tk.END)
            entry.insert(0, f"{value:.2f}")
        self._debounce_apply_params(key)

    def _debounce_apply_params(self, key):
        if self.debounce_timer:
            self.root.after_cancel(self.debounce_timer)
        self.debounce_timer = self.root.after(400, lambda: self.apply_params_from_entry(key))

    def apply_params_from_entry(self, key):
        try:
            if key == 'pts':
                val = int(self.entry_points.get())
                if val != self.last_params['pts']:
                    self.last_params['pts'] = val
                    self.slider_pts.set(val)
                    self.update_preview()
            elif key == 'db':
                val = float(self.entry_drop_db.get())
                if val != self.last_params['db']:
                    self.last_params['db'] = val
                    self.slider_db.set(val)
                    self.recalculate_all_audio()
            elif key == 'dur':
                val = float(self.entry_min_dur.get())
                if val != self.last_params['dur']:
                    self.last_params['dur'] = val
                    self.slider_dur.set(val)
                    self.recalculate_all_audio()
        except: pass

    def set_status(self, text, color="#10B981", icon_key="status_success"):
        self.lbl_status.configure(text=f" {text}", text_color=color, image=self.icons.get(icon_key))

    def set_progress(self, val):
        self.progress_bar.set(val)

    def start_loading(self, text="正在处理..."):
        self.set_status(text, "#3B82F6", "status_loading")
        self.progress_bar.set(0)
        if self.progress_bar.winfo_manager() == "":
            self.progress_bar.pack(fill=tk.X, padx=(0, 15), pady=(0, 8))

    def stop_loading(self, text="完成"):
        self.set_progress(1.0)
        self.set_status(text, "#10B981", "status_success")
        self.root.after(1500, lambda: self.progress_bar.pack_forget())

    def setup_entry_behavior(self, entry, param_key):
        def on_enter(e): entry.configure(border_color="#3B82F6", border_width=2)
        def on_leave(e):
            if self.root.focus_get() != entry:
                entry.configure(border_color=["#979DA2", "#565B5E"], border_width=1)
        def on_focus_in(e):
            entry.configure(border_color="#2563EB", border_width=2)
            entry._last_val = entry.get()
        def on_focus_out(e):
            entry.configure(border_color=["#979DA2", "#565B5E"], border_width=1)
            current_val = entry.get()
            if hasattr(entry, '_last_val') and current_val == entry._last_val: return
            if param_key in ['pts', 'db', 'dur']: self.on_param_change()
            elif param_key in ['start_manual', 'end_manual']: self.apply_manual_time()

        entry.bind("<Enter>", on_enter)
        entry.bind("<Leave>", on_leave)
        entry.bind("<FocusIn>", on_focus_in)
        entry.bind("<FocusOut>", on_focus_out)
        entry.bind("<Return>", lambda e: self.root.focus_set())

    def _clear_project(self):
        self.tree.delete(*self.tree.get_children())
        self.project_groups.clear()
        self.group_nodes.clear()
        self.items.clear()
        self._clear_canvas()
        
    def _clear_canvas(self):
        self.current_iid = None
        self.fig.clf()
        self.canvas.draw()
        self.text_preview.configure(state='normal')
        self.text_preview.delete('1.0', tk.END)
        self.text_preview.configure(state='disabled')
        self.var_t_start.set("0.000")
        self.var_t_end.set("0.000")

    def _ensure_group(self, group_name):
        if group_name not in self.project_groups:
            self.project_groups.append(group_name)
            gid = self.tree.insert("", tk.END, text=group_name, open=True, tags=('group',))
            self.group_nodes[group_name] = gid
        return self.group_nodes[group_name]

    def on_param_change(self, event=None):
        try:
            new_db = float(self.var_drop_db.get())
            new_dur = float(self.var_min_dur.get())
            new_pts = int(self.entry_points.get())
            changed_algo = False
            
            if new_db != self.last_params['db']:
                self.last_params['db'] = new_db
                changed_algo = True
            if new_dur != self.last_params['dur']:
                self.last_params['dur'] = new_dur
                changed_algo = True
                
            if changed_algo: self.recalculate_all_audio()
            if new_pts != self.last_params['pts']:
                self.last_params['pts'] = new_pts
                self.update_preview()
        except ValueError: pass

    def recalculate_all_audio(self):
        if not self.items: return
        items_snapshot = list(self.items.items())
        total = len(items_snapshot)

        def run():
            self.root.after(0, lambda: self.start_loading("正在重新计算..."))
            for i, (iid, item) in enumerate(items_snapshot):
                if item.get('snd'):
                    mac_s, mac_e = item['macro_start'], item['macro_end']
                    mic_s, mic_e = self._microscopic_vowel_nucleus(item['snd'], item['pitch'], mac_s, mac_e)
                    item['start'], item['end'] = mic_s, mic_e
                if i % 5 == 0 or i == total - 1:
                    self.root.after(0, lambda v=(i + 1) / total: self.set_progress(v))

            def finalize():
                if self.current_iid and self.current_iid in self.items:
                    self.plot_item_spectrogram(self.items[self.current_iid])
                    self.update_ui_times()
                self.update_preview()
                self.stop_loading("全局参数已应用")

            self.root.after(0, finalize)
        threading.Thread(target=run, daemon=True).start()

    def _microscopic_vowel_nucleus(self, snd, global_pitch, t_min, t_max):
        return core_microscopic_vowel_nucleus(
            snd, global_pitch, t_min, t_max, 
            self.last_params['db'], self.last_params['dur'], 
            self.switch_trim_silence.get()
        )

    def load_long_audio(self):
        path = filedialog.askopenfilename(filetypes=[("Audio Files", "*.wav *.mp3")])
        if not path: return
        self.pending_long_snd = parselmouth.Sound(path)
        self.lbl_long_file.configure(text=os.path.basename(path), text_color="#2563EB")
        self.lbl_status.configure(text="长音频就绪", text_color="#10B981")

    def open_visual_splitter(self):
        if not self.pending_long_snd:
            return messagebox.showwarning("提示", "请先导入一条长音频。")
            
        existing_items = []
        if self.items:
            # 获取当前已经存在于项目中的有效片段
            for iid, item in self.items.items():
                # 检查是否是长音频切分出来的片段（包含 macro 边界）
                if item.get('snd') is not None and 'macro_start' in item:
                    existing_items.append({
                        'id': iid,
                        'label': item['label'],
                        'start': item['macro_start'], # 使用宏观边界进行手动调整
                        'end': item['macro_end']
                    })
            # 按起始时间排序
            existing_items.sort(key=lambda x: x['start'])
            
        VisualSplitter(self.root, self.pending_long_snd, self.icons, self.on_visual_split_confirm, existing_items)

    def on_visual_split_confirm(self, segments, is_update=False):
        if is_update:
            # 用户进行了微调
            for seg in segments:
                if 'id' in seg and seg['id'] in self.items:
                    item = self.items[seg['id']]
                    # 更新宏观边界
                    item['macro_start'] = seg['start']
                    item['macro_end'] = seg['end']
                    # 基于新的宏观边界，重新计算精确的元音核心
                    mic_s, mic_e = self._microscopic_vowel_nucleus(
                        item['snd'], item['pitch'], item['macro_start'], item['macro_end']
                    )
                    item['start'], item['end'] = mic_s, mic_e
            
            # 更新预览和当前图形
            self.update_preview()
            if self.current_iid and self.current_iid in self.items:
                target_item = self.items[self.current_iid]
                self.plot_item_spectrogram(target_item) # 重新绘图，显示新的边界
                self.update_ui_times()
                
            messagebox.showinfo("提示", "手动微调已应用，时间边界已更新。")
        else:
            # 用户进行了全新的切分
            self.manual_segments = segments
            messagebox.showinfo("提示", f"全新手动切分完成，共 {len(segments)} 个片段。\n现在请点击“导入字表并切分”来匹配文本。")

    def process_long_with_wordlist(self, raw_text):
        groups, flat_words = parse_wordlist(raw_text)
        if not flat_words: return
        
        def run():
            self.root.after(0, lambda: self.start_loading("正在处理长音频..."))
            self.root.after(0, self._clear_project)
            self.items.clear()
            self.project_groups.clear()
            self.group_nodes.clear()
            
            snd = self.pending_long_snd
            global_pitch = snd.to_pitch()
            
            # 优先使用手动切分的片段
            if hasattr(self, 'manual_segments') and self.manual_segments:
                macro_segments = self.manual_segments
                # 使用完后清除，或者保留？建议清除以防下次误用
                # self.manual_segments = None 
            else:
                macro_segments = macroscopic_vad(snd)
            
            total = len(flat_words)
            results =[]
            word_idx = 0
            for grp in groups:
                for word in grp['items']:
                    if word_idx < len(macro_segments):
                        ms, me = macro_segments[word_idx]
                        mic_s, mic_e = self._microscopic_vowel_nucleus(snd, global_pitch, ms, me)
                        results.append({
                            'word': word, 'group': grp['group'], 'ms': ms, 'me': me,
                            'mis': mic_s, 'mie': mic_e, 'missing': False
                        })
                        word_idx += 1
                    else:
                        results.append({'word': word, 'group': grp['group'], 'missing': True})
                    
                    if len(results) % 10 == 0 or len(results) == total:
                        self.root.after(0, lambda v=len(results)/total: self.set_progress(v))
            
            def finalize():
                for res in results:
                    gid = self._ensure_group(res['group'])
                    if not res['missing']:
                        iid = self.tree.insert(gid, tk.END, text=res['word'], tags=('item',))
                        self.items[iid] = {
                            'label': res['word'], 'group': res['group'], 'snd': snd, 'pitch': global_pitch,
                            'macro_start': res['ms'], 'macro_end': res['me'], 
                            'start': res['mis'], 'end': res['mie']
                        }
                    else:
                        iid = self.tree.insert(gid, tk.END, text=res['word'] + " (缺失)", tags=('item',))
                        self.items[iid] = {'label': res['word'], 'group': res['group'], 'snd': None, 'start': 0.0, 'end': 0.0}
                
                self.stop_loading("长音频切分完成")
                self._select_first_item()
                # 处理完后重置手动片段
                if hasattr(self, 'manual_segments'): self.manual_segments = None
            
            self.root.after(0, finalize)
        threading.Thread(target=run, daemon=True).start()

    def load_batch_audio(self):
        paths = filedialog.askopenfilenames(filetypes=[("Audio Files", "*.wav *.mp3")])
        if not paths: return
        self.pending_batch_paths = paths
        self.lbl_batch_files.configure(text=f"已选 {len(paths)} 个文件", text_color="#2563EB")
        self.lbl_status.configure(text="独立音频就绪", text_color="#10B981")

    def process_batch_direct(self):
        if not self.pending_batch_paths:
            return messagebox.showwarning("提示", "请先选择多个音频文件")
            
        def run():
            self.root.after(0, lambda: self.start_loading("正在并行批量提取..."))
            self.items.clear()
            self.project_groups.clear()
            self.group_nodes.clear()
            self.root.after(0, self._clear_project)
            
            total = len(self.pending_batch_paths)
            params = {'db': self.last_params['db'], 'dur': self.last_params['dur']}
            trim = self.switch_trim_silence.get()
            
            results =[]
            with concurrent.futures.ProcessPoolExecutor(max_workers=min(os.cpu_count() or 4, 8)) as executor:
                futures = {executor.submit(batch_process_worker, p, params, trim): i for i, p in enumerate(self.pending_batch_paths)}
                for i, future in enumerate(concurrent.futures.as_completed(futures)):
                    orig_idx = futures[future]
                    try: results.append((orig_idx, future.result()))
                    except Exception as e: print(f"Error: {e}")
                    
                    if i % 2 == 0 or i == total - 1:
                        self.root.after(0, lambda v=(i+1)/total: self.set_progress(v))

            def finalize():
                results.sort(key=lambda x: x[0])
                gid = self._ensure_group("独立文件")
                for _, res in results:
                    if res.get('success'):
                        res['group'] = "独立文件"
                        iid = f"batch_{res['label']}_{id(res)}"
                        self.items[iid] = res
                        self.tree.insert(gid, tk.END, iid=iid, text=res['label'], tags=('item',))
                
                self.set_status(f"批量并行提取完成 ({len(results)}/{total})")
                self.stop_loading()
                self._select_first_item()
                
            self.root.after(0, finalize)
        threading.Thread(target=run, daemon=True).start()

    def process_batch_with_wordlist(self, raw_text, match_mode='order'):
        groups, flat_words = parse_wordlist(raw_text)
        if not flat_words: return
        
        def run():
            self.root.after(0, lambda: self.start_loading("正在并行匹配独立音频..."))
            self.items.clear()
            self.project_groups.clear()
            self.group_nodes.clear()
            self.root.after(0, self._clear_project)
            total = len(flat_words)
            
            tasks =[]
            if match_mode == 'fuzzy':
                available = list(range(len(self.pending_batch_paths)))
                for grp in groups:
                    group_name = grp['group']
                    for word in grp['items']:
                        remaining_paths = [self.pending_batch_paths[i] for i in available]
                        local_idx = fuzzy_match_word_to_path(word, remaining_paths)
                        if local_idx is not None:
                            real_idx = available[local_idx]
                            path = self.pending_batch_paths[real_idx]
                            available.remove(real_idx)
                            tasks.append({'word': word, 'group': group_name, 'path': path, 'missing': False})
                        else:
                            tasks.append({'word': word, 'group': group_name, 'missing': True})
            else:
                path_idx = 0
                for grp in groups:
                    group_name = grp['group']
                    for word in grp['items']:
                        if path_idx < len(self.pending_batch_paths):
                            path = self.pending_batch_paths[path_idx]
                            tasks.append({'word': word, 'group': group_name, 'path': path, 'missing': False})
                            path_idx += 1
                        else:
                            tasks.append({'word': word, 'group': group_name, 'missing': True})

            results = [None] * len(tasks)
            params = {'db': self.last_params['db'], 'dur': self.last_params['dur']}
            trim = self.switch_trim_silence.get()
            
            with concurrent.futures.ProcessPoolExecutor(max_workers=min(os.cpu_count() or 4, 8)) as executor:
                futures = {executor.submit(batch_process_worker, t['path'], params, trim): i for i, t in enumerate(tasks) if not t['missing']}
                for i, t in enumerate(tasks):
                    if t['missing']:
                        results[i] = {'label': t['word'], 'group': t['group'], 'success': False, 'missing': True}
                
                total_futures = len(futures) if futures else 1
                done_count = 0
                for i, future in enumerate(concurrent.futures.as_completed(futures)):
                    orig_idx = futures[future]
                    try:
                        res = future.result()
                        results[orig_idx] = {**res, 'missing': False}
                    except Exception as e:
                        results[orig_idx] = {'label': tasks[orig_idx]['word'], 'group': tasks[orig_idx]['group'], 'success': False, 'missing': True, 'error': str(e)}
                    
                    done_count += 1
                    self.root.after(0, lambda v=done_count/total_futures: self.set_progress(v))

            def finalize():
                matched_count = 0
                for i, res in enumerate(results):
                    gid = self._ensure_group(res['group'])
                    if not res['missing'] and res.get('success'):
                        res['group'] = tasks[i]['group']
                        display = f"{res['label']} ← {os.path.basename(res['path'])}" if match_mode == 'fuzzy' else res['label']
                        iid = f"batch_wl_{res['label']}_{id(res)}"
                        self.tree.insert(gid, tk.END, iid=iid, text=display, tags=('item',))
                        self.items[iid] = res
                        matched_count += 1
                    else:
                        suffix = " (未匹配)" if match_mode == 'fuzzy' else " (缺失)"
                        iid = f"missing_{res['label']}_{id(res)}"
                        self.tree.insert(gid, tk.END, iid=iid, text=res['label'] + suffix, tags=('item',))
                        self.items[iid] = {'label': res['label'], 'group': res['group'], 'snd': None, 'start': 0.0, 'end': 0.0}
                
                self.stop_loading(f"并行处理完成: {matched_count}/{total}")
                self._select_first_item()
                
            self.root.after(0, finalize)
        threading.Thread(target=run, daemon=True).start()

    def open_text_dialog(self, mode):
        if mode == 'long' and not self.pending_long_snd: return messagebox.showwarning("提示", "请先导入一条长音频。")
        if mode == 'batch' and not self.pending_batch_paths: return messagebox.showwarning("提示", "请先选择独立音频。")
            
        dlg = ctk.CTkToplevel(self.root)
        dlg.title("导入字表")
        dlg.geometry("400x520" if mode == 'batch' else "360x450")
        dlg.attributes('-topmost', True)
        
        ctk.CTkLabel(dlg, text="请粘贴文本字表，组别前加【】或 #：\n\n【阴平】\n八\n【阳平】\n拔", justify=tk.LEFT, text_color="#374151").pack(pady=(15, 10))
        text_box = ctk.CTkTextbox(dlg, width=320, height=200, corner_radius=8, border_width=1, border_color="#D1D5DB")
        text_box.pack(padx=20, pady=5)
        
        match_mode_var = ctk.StringVar(value="fuzzy")
        if mode == 'batch':
            frame_match = ctk.CTkFrame(dlg, fg_color="#F3F4F6", corner_radius=8)
            frame_match.pack(padx=20, pady=10, fill=tk.X)
            ctk.CTkLabel(frame_match, text="匹配方式", text_color="#4B5563").pack(anchor=tk.W, padx=10, pady=(5, 0))
            ctk.CTkRadioButton(frame_match, text="模糊匹配 (按文件名自动识别)", variable=match_mode_var, value="fuzzy").pack(anchor=tk.W, padx=15, pady=5)
            ctk.CTkRadioButton(frame_match, text="顺序匹配 (按字表顺序依次对应)", variable=match_mode_var, value="order").pack(anchor=tk.W, padx=15, pady=(0, 10))
        
        def process():
            raw_text = text_box.get("1.0", tk.END)
            dlg.destroy()
            if mode == 'long': self.process_long_with_wordlist(raw_text)
            else: self.process_batch_with_wordlist(raw_text, match_mode=match_mode_var.get())
            
        ctk.CTkButton(dlg, text="开始匹配提取", command=process, corner_radius=20, height=40, font=self.font_main).pack(pady=15)

    def add_new_group(self):
        dialog = ctk.CTkInputDialog(text="输入新组别名称:", title="新增组")
        new_name = dialog.get_input()
        if new_name:
            if new_name in self.project_groups: return messagebox.showwarning("警告", "组名已存在")
            self._ensure_group(new_name)

    def on_tree_double_click(self, event):
        iid = self.tree.identify_row(event.y)
        if not iid: return
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
            if new_name and new_name != old_name:
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
                self.update_preview()
            edit_entry.destroy()

        edit_entry.bind("<Return>", save_edit)
        edit_entry.bind("<FocusOut>", save_edit)
        edit_entry.bind("<Escape>", lambda e: edit_entry.destroy())

    def on_tree_backspace(self, event):
        selection = self.tree.selection()
        if not selection: return
        iid = selection[0]
        if 'group' in self.tree.item(iid, 'tags'):
            group_name = self.tree.item(iid, 'text')
            if messagebox.askyesno("确认删除", f"确定要删除组别【{group_name}】吗？"):
                for child in self.tree.get_children(iid):
                    self.items.pop(child, None)
                    if self.current_iid == child: self._clear_canvas()
                self.tree.delete(iid)
                self.project_groups.remove(group_name)
                self.group_nodes.pop(group_name, None)
                self.update_preview()
        elif 'item' in self.tree.item(iid, 'tags'):
            self.items.pop(iid, None)
            self.tree.delete(iid)
            if self.current_iid == iid: self._clear_canvas()
            self.update_preview()

    def on_tree_drag_start(self, event): self.tree_drag_item = self.tree.identify_row(event.y)

    def on_tree_drag_motion(self, event):
        if not getattr(self, 'tree_drag_item', None): return
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
        if target and target != self.tree_drag_item:
            tags = list(self.tree.item(target, 'tags'))
            if 'drag_target' not in tags:
                tags.append('drag_target')
                self.tree.item(target, tags=tags)
            self.last_drag_target = target

    def on_tree_drag_release(self, event):
        self.drag_indicator.place_forget()
        if getattr(self, 'last_drag_target', None) and self.tree.exists(self.last_drag_target):
            tags = list(self.tree.item(self.last_drag_target, 'tags'))
            if 'drag_target' in tags:
                tags.remove('drag_target')
                self.tree.item(self.last_drag_target, tags=tags)
        if not getattr(self, 'tree_drag_item', None): return
            
        target = self.tree.identify_row(event.y)
        if target and target != self.tree_drag_item:
            if 'item' in self.tree.item(self.tree_drag_item, 'tags'):
                if 'group' in self.tree.item(target, 'tags'):
                    self.tree.move(self.tree_drag_item, target, 'end')
                    self.items[self.tree_drag_item]['group'] = self.tree.item(target, 'text')
                elif 'item' in self.tree.item(target, 'tags'):
                    parent_grp = self.tree.parent(target)
                    target_idx = self.tree.index(target)
                    self.tree.move(self.tree_drag_item, parent_grp, target_idx)
                    self.items[self.tree_drag_item]['group'] = self.tree.item(parent_grp, 'text')
                self.update_preview()
        self.tree_drag_item = None

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

    def _select_first_item(self):
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
        item = self.items[iid]
        self.var_t_start.set(f"{item['start']:.3f}")
        self.var_t_end.set(f"{item['end']:.3f}")
        
        self.plot_item_spectrogram(item)
        self.update_preview()

    def plot_item_spectrogram(self, item):
        if (not item.get('snd') or not item.get('pitch')) and item.get('path'):
                self.set_status(f"正在读取音频: {item['label']}...", "#3B82F6", "status_loading")
                self.root.update_idletasks()
                try:
                    item['snd'] = parselmouth.Sound(item['path'])
                    item['pitch'] = item['snd'].to_pitch()
                    self.set_status("就绪", "#10B981", "status_success")
                except Exception as e:
                    self.set_status(f"加载失败: {str(e)}", "#EF4444", "status_error")
                return

        if not item.get('snd') or item.get('start') == 0.0: return
        self.fig.clf()
        self.ax = self.fig.add_subplot(111)
        self.ax2 = self.ax.twinx()
        
        snd = item['snd']
        t_s, t_e = item['start'], item['end']
        
        if self.switch_trim_silence.get():
            mac_part = snd.extract_part(from_time=item['macro_start'], to_time=item['macro_end'])
            vals = mac_part.values[0]
            mac_xs = mac_part.xs()
            valid_idx = np.where(np.abs(vals) > 0.00316)[0]
            if len(valid_idx) > 0:
                view_s = item['macro_start'] + mac_xs[valid_idx[0]]
                view_e = item['macro_start'] + mac_xs[valid_idx[-1]]
            else:
                view_s, view_e = item['macro_start'], item['macro_end']
            view_s = min(view_s, t_s)
            view_e = max(view_e, t_e)
        else:
            view_s = max(item['macro_start'] - 0.2, 0)
            view_e = min(item['macro_end'] + 0.2, snd.get_total_duration())
        
        part = snd.extract_part(from_time=view_s, to_time=view_e)
        spectrogram = part.to_spectrogram(window_length=0.005, maximum_frequency=5000)
        X = spectrogram.x_grid() + view_s 
        Y = spectrogram.y_grid()
        vals = np.where(spectrogram.values > 0, spectrogram.values, 1e-10)
        sg_db = 10 * np.log10(vals)
        
        self.ax.pcolormesh(X, Y, sg_db, vmin=sg_db.max()-50, vmax=sg_db.max(), cmap='Greys')
        
        pitch = part.to_pitch()
        p_xs = pitch.xs() + view_s
        p_vals = pitch.selected_array['frequency']
        p_vals[p_vals == 0] = np.nan
        self.ax2.plot(p_xs, p_vals, 'o', markersize=4, color='#3B82F6', zorder=5)
        
        self.ax.set_ylim([0, 5000])
        self.ax.set_xlim([view_s, view_e])
        self.ax.set_ylabel("Frequency (Hz)")
        self.ax2.set_ylim([50, 500])
        self.ax2.set_ylabel("F0 (Hz)", color='#3B82F6')
        self.ax2.tick_params(axis='y', labelcolor='#3B82F6')
        self.ax.set_title(f"字: {item['label']}", pad=10)
        
        self.line_start = self.ax.axvline(t_s, color='#EF4444', linestyle='-', linewidth=2)
        self.line_end = self.ax.axvline(t_e, color='#EF4444', linestyle='-', linewidth=2)
        self.span_fill = self.ax.axvspan(t_s, t_e, color='#BFDBFE', alpha=0.35) 
        
        self.fig.tight_layout()
        self.canvas.draw()

    def on_press(self, event):
        if not self.ax or not self.ax2: return
        if event.inaxes not in [self.ax, self.ax2] or event.button != 1 or not self.current_iid: return
        item = self.items[self.current_iid]
        start_px = self.ax.transData.transform((item['start'], 0))[0]
        end_px = self.ax.transData.transform((item['end'], 0))[0]
        
        if abs(event.x - start_px) < 15:
            self.dragging = 'start'
            self.line_start.set_color('#047857') 
            self.line_start.set_linewidth(4)
        elif abs(event.x - end_px) < 15:
            self.dragging = 'end'
            self.line_end.set_color('#047857') 
            self.line_end.set_linewidth(4)
            
        if self.dragging: self.canvas.draw_idle()

    def on_motion(self, event):
        if not self.ax or not self.current_iid or event.xdata is None: return
        item = self.items[self.current_iid]
        
        if not self.dragging:
            start_px = self.ax.transData.transform((item['start'], 0))[0]
            end_px = self.ax.transData.transform((item['end'], 0))[0]
            is_hovering = False
            
            if abs(event.x - start_px) < 15:
                self.line_start.set_linewidth(4)
                self.line_start.set_color('#B91C1C') 
                self.canvas.get_tk_widget().config(cursor="sb_h_double_arrow")
                is_hovering = True
            else:
                self.line_start.set_linewidth(2)
                self.line_start.set_color('#EF4444')
                
            if abs(event.x - end_px) < 15:
                self.line_end.set_linewidth(4)
                self.line_end.set_color('#B91C1C')
                self.canvas.get_tk_widget().config(cursor="sb_h_double_arrow")
                is_hovering = True
            else:
                self.line_end.set_linewidth(2)
                self.line_end.set_color('#EF4444')
                
            if not is_hovering:
                self.canvas.get_tk_widget().config(cursor="arrow")
                
            self.canvas.draw_idle()
            return
            
        if self.dragging == 'start': item['start'] = event.xdata
        elif self.dragging == 'end': item['end'] = event.xdata
        self.update_lines(item['start'], item['end'])

    def on_release(self, event):
        if self.dragging:
            item = self.items[self.current_iid]
            if item['start'] > item['end']: item['start'], item['end'] = item['end'], item['start']
            self.dragging = None
            self.line_start.set_color('#EF4444')
            self.line_end.set_color('#EF4444')
            self.line_start.set_linewidth(2)
            self.line_end.set_linewidth(2)
            self.update_lines(item['start'], item['end'])
            self.update_ui_times()
            self.canvas.get_tk_widget().config(cursor="arrow")

    def update_lines(self, t_s, t_e):
        if not hasattr(self, 'line_start'): return
        self.line_start.set_xdata([t_s, t_s])
        self.line_end.set_xdata([t_e, t_e])
        try: self.span_fill.remove()
        except Exception: pass
        self.span_fill = self.ax.axvspan(t_s, t_e, color='#BFDBFE', alpha=0.35)
        self.canvas.draw_idle()

    def update_ui_times(self):
        item = self.items[self.current_iid]
        self.var_t_start.set(f"{item['start']:.3f}")
        self.var_t_end.set(f"{item['end']:.3f}")
        self.update_preview()

    def apply_manual_time(self):
        if not self.current_iid: return
        try:
            item = self.items[self.current_iid]
            t1, t2 = float(self.var_t_start.get()), float(self.var_t_end.get())
            item['start'] = min(t1, t2)
            item['end'] = max(t1, t2)
            self.update_lines(item['start'], item['end'])
            self.update_ui_times()
        except ValueError: messagebox.showerror("错误", "请输入有效的数字")

    def apply_auto_detect(self):
        if not self.current_iid or self.current_iid not in self.items: return
        item = self.items[self.current_iid]
        snd = item['snd']
        pitch = item['pitch']
        mac_s, mac_e = item['macro_start'], item['macro_end']
        
        def run():
            try:
                self.root.after(0, lambda: self.start_loading("正在智能识别..."))
                mic_s, mic_e = self._microscopic_vowel_nucleus(snd, pitch, mac_s, mac_e)
                def update_ui():
                    self.var_t_start.set(f"{mic_s:.3f}")
                    self.var_t_end.set(f"{mic_e:.3f}")
                    item['start'] = mic_s
                    item['end'] = mic_e
                    self.update_lines(mic_s, mic_e)
                    self.update_preview()
                    self.stop_loading("识别完成")
                self.root.after(0, update_ui)
            except Exception as e:
                self.root.after(0, lambda: self.set_status(f"识别失败: {str(e)}", "#EF4444", "status_error"))
                self.root.after(0, self.stop_loading)
        threading.Thread(target=run, daemon=True).start()

    def on_trim_silence_toggle(self):
        self.recalculate_all_audio()

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
        if not self.current_iid: return
        item = self.items[self.current_iid]
        real_idx = self._get_item_index(self.current_iid)
        text = get_export_text_for_item(item, real_idx, self.last_params['pts'])
        
        self.text_preview.configure(state='normal')
        self.text_preview.delete('1.0', tk.END)
        self.text_preview.insert(tk.END, text)
        self.text_preview.configure(state='disabled')

    def play_selected(self):
        if not self.current_iid: return
        item = self.items[self.current_iid]
        if not item.get('snd') and item.get('path'):
            try: item['snd'] = parselmouth.Sound(item['path'])
            except: return
        if not item.get('snd'): return
        try:
            part = item['snd'].extract_part(from_time=item['start'], to_time=item['end'])
            audio_data = np.ascontiguousarray(part.values.T, dtype=np.float32)
            sd.play(audio_data, samplerate=int(part.sampling_frequency))
        except Exception as e: messagebox.showerror("错误", f"播放失败: {str(e)}")

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
                            txt_data = get_export_text_for_item(item, global_idx, self.last_params['pts'])
                            f.write(txt_data)
                            global_idx += 1

    def _export_csv(self, out_file):
        is_continuous = (self.num_rule_var.get() == "continuous")
        headers = ["组别", "编号", "字", "时长(s)", "T1(Hz)"]
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
                    if (not item.get('snd') or not item.get('pitch')) and item.get('path'):
                        try:
                            item['snd'] = parselmouth.Sound(item['path'])
                            item['pitch'] = item['snd'].to_pitch()
                        except: continue
                    if item.get('start') <= 0 or not item.get('snd'): continue
                    t_s, t_e = item['start'], item['end']
                    duration = t_e - t_s
                    if duration <= 0: continue
                    f0 = item['pitch'].get_value_at_time(t_s)
                    f0_str = "" if np.isnan(f0) else f"{f0:.6f}"
                    row = [grp_name, global_idx, item['label'], f"{duration:.6f}", f0_str]
                    writer.writerow(row)
                    global_idx += 1