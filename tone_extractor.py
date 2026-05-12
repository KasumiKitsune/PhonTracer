import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import customtkinter as ctk
import parselmouth
import numpy as np
import sounddevice as sd
import matplotlib
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import os
import csv
import warnings
from PIL import Image

# 解决中文字体显示问题
matplotlib.rcParams['font.sans-serif'] =['SimHei', 'Microsoft YaHei', 'PingFang SC', 'Arial Unicode MS', 'sans-serif']
matplotlib.rcParams['axes.unicode_minus'] = False
warnings.filterwarnings("ignore", category=RuntimeWarning)

# 初始化 CustomTkinter 全局主题配置
ctk.set_appearance_mode("Light")
ctk.set_default_color_theme("blue")

# --- 现代化的悬停提示工具类 ---
class ToolTip:
    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self.tipwindow = None
        self.id = None
        self.widget.bind("<Enter>", self.enter)
        self.widget.bind("<Leave>", self.leave)

    def enter(self, event=None):
        self.schedule()

    def leave(self, event=None):
        self.unschedule()
        self.hidetip()

    def schedule(self):
        self.unschedule()
        self.id = self.widget.after(500, self.showtip) # 500毫秒延迟

    def unschedule(self):
        id_ = self.id
        self.id = None
        if id_:
            self.widget.after_cancel(id_)

    def showtip(self, event=None):
        x, y, cx, cy = self.widget.bbox("insert") or (0,0,0,0)
        x += self.widget.winfo_rootx() + 25
        y += self.widget.winfo_rooty() + 25
        self.tipwindow = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        label = tk.Label(tw, text=self.text, justify=tk.LEFT,
                         background="#374151", foreground="white", relief=tk.FLAT,
                         borderwidth=0, font=("Microsoft YaHei", 10), padx=8, pady=5)
        label.pack(ipadx=1)

    def hidetip(self):
        tw = self.tipwindow
        self.tipwindow = None
        if tw:
            tw.destroy()


class PhoneticsApp:
    def __init__(self, root):
        self.root = root
        self.root.title("声调提取")
        self.root.geometry("1200x700")
        self.root.configure(fg_color="#F3F4F6") 
        
        # 核心数据结构
        self.pending_long_snd = None 
        self.pending_batch_paths =[]
        self.project_groups =[]     
        self.group_nodes = {}        
        self.items = {}              
        self.current_iid = None  
        self.dragging = None 
        self.tree_drag_item = None 
        
        # 记录上一次的参数状态，避免仅仅聚焦就触发全部重算
        self.last_params = {
            'pts': 11,
            'db': 60.0,
            'dur': 0.04
        }
        
        # 字体统一定义
        self.font_title = ctk.CTkFont(family="Microsoft YaHei", size=15, weight="bold")
        self.font_main = ctk.CTkFont(family="Microsoft YaHei", size=13)
        self.font_code = ctk.CTkFont(family="Consolas", size=13)

        self.setup_icons()
        self.setup_ui()
        
    def setup_icons(self):
        # 预加载所有图标
        icon_path = os.path.join(os.path.dirname(__file__), "icons")
        self.icons = {}
        
        icon_files = {
            "audio": "audio_file.png",
            "cut": "cut.png",
            "batch": "batch.png",
            "magic": "magic.png",
            "list": "list.png",
            "plus": "plus.png",
            "play": "play.png",
            "save": "save.png",
            "check": "check.png",
            "auto": "auto.png",
            "points": "points.png",
            "energy": "energy.png",
            "duration": "duration.png",
            "trim": "trim.png",
            "tag": "tag.png"
        }
        
        for key, filename in icon_files.items():
            path = os.path.join(icon_path, filename)
            if os.path.exists(path):
                img = Image.open(path)
                self.icons[key] = ctk.CTkImage(light_image=img, dark_image=img, size=(20, 20))
            else:
                self.icons[key] = None

    def setup_ui(self):
        # ---------------- 1. 左侧控制面板 ----------------
        left_scrollable = ctk.CTkScrollableFrame(self.root, width=320, fg_color="transparent")
        left_scrollable.pack(side=tk.LEFT, fill=tk.Y, padx=10, pady=10)
        
        btn_kwargs_primary = {"corner_radius": 20, "height": 38, "font": self.font_main}
        btn_kwargs_secondary = {"corner_radius": 20, "height": 38, "font": self.font_main, 
                                "fg_color": "#E5E7EB", "text_color": "#1F2937", "hover_color": "#D1D5DB"}
        
        # --- 卡片 1：单条长音频 ---
        card_mode1 = ctk.CTkFrame(left_scrollable, fg_color="white", corner_radius=10)
        card_mode1.pack(fill=tk.X, pady=(0, 10))
        ctk.CTkLabel(card_mode1, text="模式一：单条长音频", font=self.font_title, text_color="#111827").pack(anchor=tk.W, padx=15, pady=(15, 5))
        ctk.CTkButton(card_mode1, text=" 导入长音频", image=self.icons.get("audio"), compound="left", command=self.load_long_audio, **btn_kwargs_primary).pack(fill=tk.X, padx=15, pady=(5, 2))
        self.lbl_long_file = ctk.CTkLabel(card_mode1, text="未选择", font=self.font_main, text_color="#6B7280")
        self.lbl_long_file.pack(pady=(0, 5))
        ctk.CTkButton(card_mode1, text=" 导入字表并切分", image=self.icons.get("cut"), compound="left", command=lambda: self.open_text_dialog('long'), **btn_kwargs_secondary).pack(fill=tk.X, padx=15, pady=(0, 15))

        # --- 卡片 2：多条独立音频 ---
        card_mode2 = ctk.CTkFrame(left_scrollable, fg_color="white", corner_radius=10)
        card_mode2.pack(fill=tk.X, pady=5)
        ctk.CTkLabel(card_mode2, text="模式二：多条独立音频", font=self.font_title, text_color="#111827").pack(anchor=tk.W, padx=15, pady=(15, 5))
        ctk.CTkButton(card_mode2, text=" 选择多个音频文件", image=self.icons.get("batch"), compound="left", command=self.load_batch_audio, **btn_kwargs_primary).pack(fill=tk.X, padx=15, pady=(5, 2))
        self.lbl_batch_files = ctk.CTkLabel(card_mode2, text="未选择", font=self.font_main, text_color="#6B7280")
        self.lbl_batch_files.pack(pady=(0, 5))
        row_mode2_btns = ctk.CTkFrame(card_mode2, fg_color="transparent")
        row_mode2_btns.pack(fill=tk.X, padx=15, pady=(0, 15))
        ctk.CTkButton(row_mode2_btns, text="文件名提取", image=self.icons.get("tag"), compound="left", command=self.process_batch_direct, **btn_kwargs_secondary, width=120).pack(side=tk.LEFT, expand=True, padx=(0, 5))
        ctk.CTkButton(row_mode2_btns, text="导入字表", image=self.icons.get("list"), compound="left", command=lambda: self.open_text_dialog('batch'), **btn_kwargs_secondary, width=120).pack(side=tk.RIGHT, expand=True, padx=(5, 0))

        # --- 卡片 3：全局算法与参数设置 ---
        card_params = ctk.CTkFrame(left_scrollable, fg_color="white", corner_radius=10)
        card_params.pack(fill=tk.X, pady=10)
        ctk.CTkLabel(card_params, text="全局算法与导出参数", font=self.font_title, text_color="#111827").pack(anchor=tk.W, padx=15, pady=(15, 5))
        
        # N 等分数据点
        row_pts = ctk.CTkFrame(card_params, fg_color="transparent")
        row_pts.pack(fill=tk.X, padx=15, pady=5)
        lbl_pts = ctk.CTkLabel(row_pts, text=" 等分数据点 (N):", image=self.icons.get("points"), compound="left", text_color="#374151", font=self.font_main)
        lbl_pts.pack(side=tk.LEFT)
        self.entry_points = ctk.CTkEntry(row_pts, width=60, justify="center", corner_radius=20, height=28)
        self.entry_points.insert(0, str(self.last_params['pts'])) 
        self.entry_points.pack(side=tk.RIGHT)
        ToolTip(lbl_pts, "导出数据时，对这段录音提取多少个 F0 频率点\n(默认11点，即 0%, 10% ... 100%)")
        self.setup_entry_behavior(self.entry_points, 'pts')
        
        # 算法: 元音能量落差
        row_db = ctk.CTkFrame(card_params, fg_color="transparent")
        row_db.pack(fill=tk.X, padx=15, pady=5)
        lbl_db = ctk.CTkLabel(row_db, text=" 元音能量落差 (dB):", image=self.icons.get("energy"), compound="left", text_color="#374151", font=self.font_main)
        lbl_db.pack(side=tk.LEFT)
        self.var_drop_db = ctk.StringVar(value=str(self.last_params['db']))
        self.entry_drop_db = ctk.CTkEntry(row_db, textvariable=self.var_drop_db, width=60, justify="center", corner_radius=20, height=28)
        self.entry_drop_db.pack(side=tk.RIGHT)
        ToolTip(lbl_db, "用于定位元音核心区。\n落差值越大 (如 60dB)，保留的头尾边缘越多；\n值越小 (如 15dB)，越向最高能量的元音核心靠拢。")
        self.setup_entry_behavior(self.entry_drop_db, 'db')
        
        # 算法: 最短持续时间
        row_dur = ctk.CTkFrame(card_params, fg_color="transparent")
        row_dur.pack(fill=tk.X, padx=15, pady=5)
        lbl_dur = ctk.CTkLabel(row_dur, text=" 最短持续时间 (s):", image=self.icons.get("duration"), compound="left", text_color="#374151", font=self.font_main)
        lbl_dur.pack(side=tk.LEFT)
        self.var_min_dur = ctk.StringVar(value=str(self.last_params['dur']))
        self.entry_min_dur = ctk.CTkEntry(row_dur, textvariable=self.var_min_dur, width=60, justify="center", corner_radius=20, height=28)
        self.entry_min_dur.pack(side=tk.RIGHT)
        ToolTip(lbl_dur, "算法会自动丢弃所有持续时间短于此值的切片。\n调大可以过滤掉短促的杂音。")
        self.setup_entry_behavior(self.entry_min_dur, 'dur')
        
        # 算法: 边缘静音裁切开关
        row_trim = ctk.CTkFrame(card_params, fg_color="transparent")
        row_trim.pack(fill=tk.X, padx=15, pady=(10, 15))
        self.lbl_trim_icon = ctk.CTkLabel(row_trim, text="", image=self.icons.get("trim"))
        self.lbl_trim_icon.pack(side=tk.LEFT, padx=(0, 5))
        self.switch_trim_silence = ctk.CTkSwitch(row_trim, text="开启边缘静音裁切 (<-50dB)", font=self.font_main, 
                                                 progress_color="#10B981", text_color="#374151", command=self.on_trim_silence_toggle)
        self.switch_trim_silence.pack(side=tk.LEFT)
        self.switch_trim_silence.select() # 默认开启
        ToolTip(self.switch_trim_silence, "开启后将在图表上自动忽略首尾低于 -50dB 的绝对静音区域，\n让有效波形占满屏幕。")

        self.lbl_status = ctk.CTkLabel(left_scrollable, text="就绪", text_color="#10B981", font=self.font_main, wraplength=280)
        self.lbl_status.pack(pady=20)

        # ---------------- 2. 右侧面板 (列表 + 标号规则 + 预览) ----------------
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
        
        self.drag_indicator = tk.Frame(self.tree, height=2, bg="#3B82F6") # 拖拽指示线
        
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

        # ---------------- 3. 中间语谱图面板 ----------------
        center_frame = ctk.CTkFrame(self.root, fg_color="white", corner_radius=10)
        center_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, pady=10)
        
        # --- 顶部控制栏 (区间微调、试听、导出 横排) ---
        top_bar = ctk.CTkFrame(center_frame, fg_color="transparent")
        top_bar.pack(side=tk.TOP, fill=tk.X, padx=15, pady=(15, 5))
        
        # 第一行：区间微调
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
        self.entry_t_end.pack(side=tk.LEFT, padx=(5, 15))
        self.setup_entry_behavior(self.entry_t_end, 'end_manual')
        
        # 第二行：主要动作按钮（手动应用 + 自动识别 + 试听 + 导出）
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

    # =============== 通用工具与清理 ===============
    def setup_entry_behavior(self, entry, param_key):
        """为输入框增加悬停、按下效果，以及失去焦点自动应用功能"""
        def on_enter(e):
            entry.configure(border_color="#3B82F6", border_width=2)
        def on_leave(e):
            if self.root.focus_get() != entry:
                entry.configure(border_color=["#979DA2", "#565B5E"], border_width=1)
        def on_focus_in(e):
            entry.configure(border_color="#2563EB", border_width=2)
            # 记录进入时的值，用于对比是否发生变化
            entry._last_val = entry.get()
            
        def on_focus_out(e):
            entry.configure(border_color=["#979DA2", "#565B5E"], border_width=1)
            current_val = entry.get()
            if hasattr(entry, '_last_val') and current_val == entry._last_val:
                return # 值没变，不触发应用
            
            # 自动应用逻辑
            if param_key in ['pts', 'db', 'dur']:
                self.on_param_change()
            elif param_key in ['start_manual', 'end_manual']:
                self.apply_manual_time()

        entry.bind("<Enter>", on_enter)
        entry.bind("<Leave>", on_leave)
        entry.bind("<FocusIn>", on_focus_in)
        entry.bind("<FocusOut>", on_focus_out)
        entry.bind("<Return>", lambda e: self.root.focus_set()) # 回车即失去焦点

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

    def _parse_wordlist(self, raw_text):
        groups = []
        flat_words =[]
        curr_group = "未分组"
        curr_items =[]
        for line in raw_text.split('\n'):
            line = line.strip()
            if not line: continue
            if line.startswith('【') or line.startswith('[') or line.startswith('#'):
                if curr_items:
                    groups.append({"group": curr_group, "items": curr_items})
                    curr_items = []
                curr_group = line.replace('【', '').replace('】', '').replace('[', '').replace(']', '').replace('#', '').strip()
            else:
                curr_items.append(line)
                flat_words.append(line)
        if curr_items: groups.append({"group": curr_group, "items": curr_items})
        return groups, flat_words

    # =============== 状态拦截与重计算机制 ===============
    def on_param_change(self, event=None):
        try:
            new_db = float(self.var_drop_db.get())
            new_dur = float(self.var_min_dur.get())
            new_pts = int(self.entry_points.get())
            
            changed_algo = False
            
            # 对比数值是否真正发生了变化
            if new_db != self.last_params['db']:
                self.last_params['db'] = new_db
                changed_algo = True
            
            if new_dur != self.last_params['dur']:
                self.last_params['dur'] = new_dur
                changed_algo = True
                
            # 如果算法核心参数变了，才重新计算音频
            if changed_algo:
                self.recalculate_all_audio()
                
            # 如果仅仅是等分点改变了，只更新右侧预览区
            if new_pts != self.last_params['pts']:
                self.last_params['pts'] = new_pts
                self.update_preview()
                
        except ValueError:
            pass # 用户正在输入非数字，静默跳过

    def recalculate_all_audio(self):
        if not self.items: return
        self.lbl_status.configure(text="正在根据新参数重新计算全部音频...", text_color="#F59E0B")
        self.root.update()
        
        for iid, item in self.items.items():
            if item['snd']:
                mac_s, mac_e = item['macro_start'], item['macro_end']
                mic_s, mic_e = self._microscopic_vowel_nucleus(item['snd'], item['pitch'], mac_s, mac_e)
                item['start'], item['end'] = mic_s, mic_e
                
        if self.current_iid and self.current_iid in self.items:
            self.plot_item_spectrogram(self.items[self.current_iid])
            self.update_ui_times()
            
        self.update_preview()
        self.lbl_status.configure(text="全局参数已应用至全部音频", text_color="#10B981")

    # =============== 核心识别算法 ===============
    def _macroscopic_vad(self, snd):
        intensity = snd.to_intensity(time_step=0.01)
        vals = intensity.values[0]
        xs = intensity.xs()
        sorted_vals = np.sort(vals[~np.isnan(vals)])
        max_int = np.mean(sorted_vals[-int(len(sorted_vals)*0.05):]) if len(sorted_vals) > 20 else 70
        thresh = max_int - 25 
        is_sp = vals > thresh
        segs, start =[], None
        for i, s in enumerate(is_sp):
            if s and start is None: start = xs[i]
            elif not s and start is not None:
                segs.append([start, xs[i]])
                start = None
        if start is not None: segs.append([start, xs[-1]])
        
        merged =[]
        for s in segs:
            if not merged: merged.append(s)
            else:
                if s[0] - merged[-1][1] < 0.25: merged[-1][1] = s[1]
                else: merged.append(s)
        return [s for s in merged if s[1]-s[0] > 0.1]

    def _microscopic_vowel_nucleus(self, snd, global_pitch, t_min, t_max):
        part = snd.extract_part(from_time=t_min, to_time=t_max)
        intensity = part.to_intensity()
        xs = global_pitch.xs()
        freqs = global_pitch.selected_array['frequency']
        try: max_int = np.nanmax(intensity.values)
        except Exception: return t_min, t_max
        
        drop_db = self.last_params['db']
        min_dur = self.last_params['dur']
            
        best_s, best_e = 0.0, part.get_total_duration()
        thresh = max_int - drop_db
        valid =[]
        for t in part.xs():
            idx = np.argmin(np.abs(xs - (t_min + t)))
            if freqs[idx] > 0:
                val = intensity.get_value(t)
                if val and not np.isnan(val) and val > thresh: valid.append(t)
                
        if len(valid) > 2 and (valid[-1] - valid[0]) > min_dur:
            best_s, best_e = valid[0], valid[-1]
            
        temp_s, temp_e = t_min + best_s, t_min + best_e
        
        if self.switch_trim_silence.get():
            trim_part = snd.extract_part(from_time=temp_s, to_time=temp_e)
            vals = trim_part.values[0]
            trim_xs = trim_part.xs()
            valid_idx = np.where(np.abs(vals) > 0.00316)[0]
            if len(valid_idx) > 0:
                return temp_s + trim_xs[valid_idx[0]], temp_s + trim_xs[valid_idx[-1]]
                
        return temp_s, temp_e

    # =============== 模式一：长音频 ===============
    def load_long_audio(self):
        path = filedialog.askopenfilename(filetypes=[("Audio Files", "*.wav *.mp3")])
        if not path: return
        self.pending_long_snd = parselmouth.Sound(path)
        self.lbl_long_file.configure(text=os.path.basename(path), text_color="#2563EB")
        self.lbl_status.configure(text="长音频就绪", text_color="#10B981")

    def process_long_with_wordlist(self, raw_text):
        groups, flat_words = self._parse_wordlist(raw_text)
        if not flat_words: return
        self.lbl_status.configure(text="正在按照全局参数切分...", text_color="#F59E0B")
        self.root.update()
        self._clear_project()

        snd = self.pending_long_snd
        global_pitch = snd.to_pitch()
        macro_segments = self._macroscopic_vad(snd)
        
        word_idx = 0
        for grp in groups:
            gid = self._ensure_group(grp['group'])
            for word in grp['items']:
                if word_idx < len(macro_segments):
                    mac_s, mac_e = macro_segments[word_idx]
                    mic_s, mic_e = self._microscopic_vowel_nucleus(snd, global_pitch, mac_s, mac_e)
                    
                    iid = self.tree.insert(gid, tk.END, text=word, tags=('item',))
                    self.items[iid] = {
                        'label': word, 'group': grp['group'], 'snd': snd, 'pitch': global_pitch,
                        'macro_start': mac_s, 'macro_end': mac_e, 'start': mic_s, 'end': mic_e
                    }
                    word_idx += 1
                else:
                    iid = self.tree.insert(gid, tk.END, text=word + " (缺失)", tags=('item',))
                    self.items[iid] = {'label': word, 'group': grp['group'], 'snd': None, 'start': 0.0, 'end': 0.0}
        
        self.lbl_status.configure(text="长音频切分完成", text_color="#10B981")
        self._select_first_item()

    # =============== 模式二：独立音频 ===============
    def load_batch_audio(self):
        paths = filedialog.askopenfilenames(filetypes=[("Audio Files", "*.wav *.mp3")])
        if not paths: return
        self.pending_batch_paths = paths
        self.lbl_batch_files.configure(text=f"已选 {len(paths)} 个文件", text_color="#2563EB")
        self.lbl_status.configure(text="独立音频就绪", text_color="#10B981")

    def process_batch_direct(self):
        if not self.pending_batch_paths: return messagebox.showwarning("提示", "请先选择独立音频。")
        self.lbl_status.configure(text="正在处理...", text_color="#F59E0B")
        self.root.update()
        self._clear_project()
        
        gid = self._ensure_group("独立文件")
        for p in self.pending_batch_paths:
            snd = parselmouth.Sound(p)
            pitch = snd.to_pitch()
            mac_s, mac_e = 0.0, snd.get_total_duration()
            mic_s, mic_e = self._microscopic_vowel_nucleus(snd, pitch, mac_s, mac_e)
            
            word = os.path.splitext(os.path.basename(p))[0]
            iid = self.tree.insert(gid, tk.END, text=word, tags=('item',))
            self.items[iid] = {
                'label': word, 'group': "独立文件", 'snd': snd, 'pitch': pitch,
                'macro_start': mac_s, 'macro_end': mac_e, 'start': mic_s, 'end': mic_e
            }
        self.lbl_status.configure(text="独立文件提取完成", text_color="#10B981")
        self._select_first_item()

    def _fuzzy_match_word_to_path(self, word, available_paths):
        word_lower = word.lower()
        exact_matches, contains_matches = [],[]
        for i, p in enumerate(available_paths):
            fname = os.path.splitext(os.path.basename(p))[0].lower()
            if fname == word_lower: exact_matches.append(i)
            elif word_lower in fname or fname in word_lower: contains_matches.append(i)
        
        if exact_matches: return exact_matches[0]
        if contains_matches:
            contains_matches.sort(key=lambda i: len(os.path.basename(available_paths[i])))
            return contains_matches[0]
        return None

    def process_batch_with_wordlist(self, raw_text, match_mode='order'):
        groups, flat_words = self._parse_wordlist(raw_text)
        if not flat_words: return
        self.lbl_status.configure(text="正在分配与提取...", text_color="#F59E0B")
        self.root.update()
        self._clear_project()
        
        if match_mode == 'fuzzy':
            available = list(range(len(self.pending_batch_paths)))
            matched_count = 0
            unmatched_words =[]
            
            for grp in groups:
                gid = self._ensure_group(grp['group'])
                for word in grp['items']:
                    remaining_paths =[self.pending_batch_paths[i] for i in available]
                    local_idx = self._fuzzy_match_word_to_path(word, remaining_paths)
                    
                    if local_idx is not None:
                        real_idx = available[local_idx]
                        p = self.pending_batch_paths[real_idx]
                        available.remove(real_idx)
                        
                        snd = parselmouth.Sound(p)
                        pitch = snd.to_pitch()
                        mac_s, mac_e = 0.0, snd.get_total_duration()
                        mic_s, mic_e = self._microscopic_vowel_nucleus(snd, pitch, mac_s, mac_e)
                        
                        display = f"{word} ← {os.path.basename(p)}"
                        iid = self.tree.insert(gid, tk.END, text=display, tags=('item',))
                        self.items[iid] = {
                            'label': word, 'group': grp['group'], 'snd': snd, 'pitch': pitch,
                            'macro_start': mac_s, 'macro_end': mac_e, 'start': mic_s, 'end': mic_e
                        }
                        matched_count += 1
                    else:
                        unmatched_words.append(word)
                        iid = self.tree.insert(gid, tk.END, text=word + " (未匹配)", tags=('item',))
                        self.items[iid] = {'label': word, 'group': grp['group'], 'snd': None, 'start': 0.0, 'end': 0.0}
            
            status = f"模糊匹配完成: {matched_count}/{len(flat_words)} 匹配成功"
            if unmatched_words: status += f"，{len(unmatched_words)} 个未匹配"
            self.lbl_status.configure(text=status, text_color="#10B981" if not unmatched_words else "#F59E0B")
        else:
            if len(flat_words) != len(self.pending_batch_paths):
                if not messagebox.askyesno("数量不匹配", f"字表有 {len(flat_words)} 个字，导入了 {len(self.pending_batch_paths)} 个音频。\n是否继续？"): return
            
            path_idx = 0
            for grp in groups:
                gid = self._ensure_group(grp['group'])
                for word in grp['items']:
                    if path_idx < len(self.pending_batch_paths):
                        p = self.pending_batch_paths[path_idx]
                        snd = parselmouth.Sound(p)
                        pitch = snd.to_pitch()
                        mac_s, mac_e = 0.0, snd.get_total_duration()
                        mic_s, mic_e = self._microscopic_vowel_nucleus(snd, pitch, mac_s, mac_e)
                        
                        iid = self.tree.insert(gid, tk.END, text=word, tags=('item',))
                        self.items[iid] = {
                            'label': word, 'group': grp['group'], 'snd': snd, 'pitch': pitch,
                            'macro_start': mac_s, 'macro_end': mac_e, 'start': mic_s, 'end': mic_e
                        }
                        path_idx += 1
                    else:
                        iid = self.tree.insert(gid, tk.END, text=word + " (缺失)", tags=('item',))
                        self.items[iid] = {'label': word, 'group': grp['group'], 'snd': None, 'start': 0.0, 'end': 0.0}
            self.lbl_status.configure(text="独立文件贴字表提取完成", text_color="#10B981")
        self._select_first_item()

    # =============== 文本导入对话框 ===============
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

    # =============== Treeview 拖拽与重命名管理 ===============
    def add_new_group(self):
        dialog = ctk.CTkInputDialog(text="输入新组别名称:", title="新增组")
        new_name = dialog.get_input()
        if new_name:
            if new_name in self.project_groups:
                messagebox.showwarning("警告", "组名已存在")
                return
            self._ensure_group(new_name)

    def on_tree_double_click(self, event):
        iid = self.tree.identify_row(event.y)
        if not iid: return
        
        # 获取该项在 Treeview 中的位置
        bbox = self.tree.bbox(iid, "#0")
        if not bbox: return
        x, y, w, h = bbox
        
        old_name = self.tree.item(iid, 'text')
        
        # 创建临时的输入框覆盖在上面
        edit_entry = tk.Entry(self.tree, font=("Microsoft YaHei", 12), borderwidth=1, relief="solid")
        edit_entry.insert(0, old_name)
        edit_entry.select_range(0, tk.END)
        edit_entry.focus_set()
        
        # 放置输入框
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
                        if child in self.items:
                            self.items[child]['group'] = new_name
                elif 'item' in self.tree.item(iid, 'tags'):
                    self.tree.item(iid, text=new_name)
                    self.items[iid]['label'] = new_name
                self.update_preview()
            
            edit_entry.destroy()

        # 绑定保存与取消事件
        edit_entry.bind("<Return>", save_edit)
        edit_entry.bind("<FocusOut>", save_edit)
        edit_entry.bind("<Escape>", lambda e: edit_entry.destroy())
            
        self.update_preview()

    def on_tree_backspace(self, event):
        selection = self.tree.selection()
        if not selection: return
        iid = selection[0]
        
        if 'group' in self.tree.item(iid, 'tags'):
            group_name = self.tree.item(iid, 'text')
            if messagebox.askyesno("确认删除", f"确定要删除组别【{group_name}】及其包含的所有条目吗？"):
                for child in self.tree.get_children(iid):
                    self.items.pop(child, None)
                    if self.current_iid == child:
                        self._clear_canvas()
                self.tree.delete(iid)
                self.project_groups.remove(group_name)
                self.group_nodes.pop(group_name, None)
                self.update_preview()
        elif 'item' in self.tree.item(iid, 'tags'):
            self.items.pop(iid, None)
            self.tree.delete(iid)
            if self.current_iid == iid:
                self._clear_canvas()
            self.update_preview()

    def on_tree_drag_start(self, event):
        self.tree_drag_item = self.tree.identify_row(event.y)

    def on_tree_drag_motion(self, event):
        if not hasattr(self, 'tree_drag_item') or not self.tree_drag_item: 
            return
        target = self.tree.identify_row(event.y)
        
        # 绘制指示线
        if target:
            bbox = self.tree.bbox(target)
            if bbox:
                x, y, w, h = bbox
                if event.y < y + h/2:
                    self.drag_indicator.place(x=x, y=y, width=w)
                else:
                    self.drag_indicator.place(x=x, y=y+h, width=w)
        else:
            self.drag_indicator.place_forget()

        if hasattr(self, 'last_drag_target') and self.last_drag_target and self.tree.exists(self.last_drag_target):
            if self.last_drag_target != target:
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
        if hasattr(self, 'last_drag_target') and self.last_drag_target and self.tree.exists(self.last_drag_target):
            tags = list(self.tree.item(self.last_drag_target, 'tags'))
            if 'drag_target' in tags:
                tags.remove('drag_target')
                self.tree.item(self.last_drag_target, tags=tags)
                
        if not hasattr(self, 'tree_drag_item') or not self.tree_drag_item: 
            return
            
        target = self.tree.identify_row(event.y)
        
        if target and target != self.tree_drag_item:
            # 拖拽到新位置：执行移动逻辑
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
        if hasattr(self, 'tree_drag_item') and self.tree_drag_item: return
        iid = self.tree.identify_row(event.y)
        if hasattr(self, 'last_hover') and self.last_hover and self.tree.exists(self.last_hover):
            if self.last_hover != iid:
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
        if hasattr(self, 'last_hover') and self.last_hover and self.tree.exists(self.last_hover):
            tags = list(self.tree.item(self.last_hover, 'tags'))
            if 'hover' in tags: 
                tags.remove('hover')
                self.tree.item(self.last_hover, tags=tags)
            self.last_hover = None

    # =============== 交互与绘图 ===============
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
        if not item['snd'] or item['start'] == 0.0: return
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
        if not self.current_iid or event.xdata is None: return
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

    # =============== 手动应用与导出 ===============
    def apply_manual_time(self):
        if not self.current_iid: return
        try:
            item = self.items[self.current_iid]
            t1, t2 = float(self.var_t_start.get()), float(self.var_t_end.get())
            item['start'] = min(t1, t2)
            item['end'] = max(t1, t2)
            self.update_lines(item['start'], item['end'])
            self.update_ui_times()
        except ValueError:
            messagebox.showerror("错误", "请输入有效的数字")

    def apply_auto_detect(self):
        if not self.current_iid: return
        item = self.items[self.current_iid]
        if not item['snd']: return
        mac_s, mac_e = item['macro_start'], item['macro_end']
        mic_s, mic_e = self._microscopic_vowel_nucleus(item['snd'], item['pitch'], mac_s, mac_e)
        item['start'], item['end'] = mic_s, mic_e
        self.update_lines(mic_s, mic_e)
        self.update_ui_times()

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

    def get_export_text_for_item(self, item, real_index):
        num_points = self.last_params['pts']
        t_s, t_e = item['start'], item['end']
        duration = t_e - t_s
        if duration <= 0 or not item['snd']: return ""
        
        times = np.linspace(t_s, t_e, num_points)
        output = f"{real_index}.{item['label']}\n{duration:.3f}\n"
        for t in times:
            f0 = item['pitch'].get_value_at_time(t)
            f0_str = "0.000000" if np.isnan(f0) else f"{f0:.6f}"
            output += f"{t:.6f}   {f0_str}\n"
        return output

    def update_preview(self):
        if not self.current_iid: return
        item = self.items[self.current_iid]
        real_idx = self._get_item_index(self.current_iid)
        text = self.get_export_text_for_item(item, real_idx)
        
        self.text_preview.configure(state='normal')
        self.text_preview.delete('1.0', tk.END)
        self.text_preview.insert(tk.END, text)
        self.text_preview.configure(state='disabled')

    def play_selected(self):
        if not self.current_iid: return
        item = self.items[self.current_iid]
        if not item['snd']: return
        try:
            part = item['snd'].extract_part(from_time=item['start'], to_time=item['end'])
            # 转换为内存连续且符合 float32 格式的音频数据，修复播放不出声的 Bug
            audio_data = np.ascontiguousarray(part.values.T, dtype=np.float32)
            sd.play(audio_data, samplerate=int(part.sampling_frequency))
        except Exception as e: 
            messagebox.showerror("错误", f"播放失败: {str(e)}")

    def export_project(self):
        if not self.items: return messagebox.showwarning("提示", "没有可导出的数据。")
        out_file = filedialog.asksaveasfilename(
            title="导出全表数据",
            defaultextension=".txt",
            initialfile="tone_export_data",
            filetypes=[("CSV 表格", "*.csv"), ("文本文件", "*.txt"), ("所有文件", "*.*")]
        )
        if not out_file: return  # 用户取消了对话框
        try:
            if out_file.lower().endswith(".csv"):
                self._export_csv(out_file)
            else:
                self._export_txt(out_file)
            messagebox.showinfo("成功", f"数据已导出至:\n{out_file}")
        except Exception as e:
            messagebox.showerror("错误", str(e))

    def _export_txt(self, out_file):
        """原有的纵向文本格式导出"""
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
                            txt_data = self.get_export_text_for_item(item, global_idx)
                            f.write(txt_data)
                            global_idx += 1

    def _export_csv(self, out_file):
        """宽表 CSV 格式：每行一个字，列为 组别/编号/字/时长/T1"""
        is_continuous = (self.num_rule_var.get() == "continuous")

        headers = ["组别", "编号", "字", "时长(s)", "T1(Hz)"]

        with open(out_file, "w", encoding="utf-8-sig", newline="") as f:
            # utf-8-sig 写入 BOM，让 Excel 正确识别中文
            writer = csv.writer(f)
            writer.writerow(headers)

            global_idx = 1
            for grp_name in self.project_groups:
                if not is_continuous: global_idx = 1
                grp_node = self.group_nodes[grp_name]
                for child in self.tree.get_children(grp_node):
                    if child not in self.items: continue
                    item = self.items[child]
                    if item['start'] <= 0 or not item['snd']: continue

                    t_s, t_e = item['start'], item['end']
                    duration = t_e - t_s
                    if duration <= 0: continue

                    f0 = item['pitch'].get_value_at_time(t_s)
                    f0_str = "" if np.isnan(f0) else f"{f0:.6f}"

                    row = [grp_name, global_idx, item['label'], f"{duration:.6f}", f0_str]
                    writer.writerow(row)
                    global_idx += 1


if __name__ == "__main__":
    root = ctk.CTk()
    app = PhoneticsApp(root)
    root.mainloop()