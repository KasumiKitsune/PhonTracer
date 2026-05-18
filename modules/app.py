import tkinter as tk
from tkinter import filedialog, messagebox
import customtkinter as ctk
# pyrefly: ignore [missing-import]
import parselmouth
import os
import threading
import concurrent.futures
import numpy as np
from PIL import Image
import queue

# 导入拆分后的模块
from .ui_widgets import ToolTip, CTkReleaseButton
from .data_utils import parse_wordlist, fuzzy_match_word_to_path
from .audio_core import core_microscopic_vowel_nucleus, batch_process_worker, macroscopic_vad, check_audio_segments, long_process_worker, recalculate_bounds_fast, auto_split_inner_word
from .visual_splitter import VisualSplitter
from .spectrogram_panel import SpectrogramPanel
from .project_tree import ProjectTreePanel

class PhoneticsApp:
    def __init__(self, root, initial_files=None):
        self.root = root
        self.root.title("PhonTracer - 声调提取与分析工具")
        self.root.geometry("1200x700")
        self.root.minsize(1100, 650)
        self.root.configure(fg_color="#F3F4F6") 
        
        # 设置窗口图标
        try:
            icon_file = os.path.join("assets", "icon.ico")
            if not os.path.exists(icon_file):
                icon_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets", "icon.ico")
            if os.path.exists(icon_file):
                self.root.iconbitmap(icon_file)
        except Exception:
            pass
        
        self.pending_long_snd = None 
        self.pending_batch_paths = []
        
        # === 初始化安全队列以处理拖拽事件，防止 windnd 的 C++ 回调直接操作 Tkinter 引引发 GIL 崩溃 ===
        self.drop_queue = queue.Queue()
        self.root.after(100, self._check_drop_queue)
        
        # 全局数据源 (Source of Truth)
        self.items = {}
        self.audio_cache = {}
        
        self.debounce_timer = None
        
        self.last_params = {
            'pts': 11,
            'db': 60.0,
            'skip_front': 0.00,
            'pitch_floor': 75,
            'pitch_ceiling': 600,
            'voicing_threshold': 0.25
        }

        # Shared ProcessPoolExecutor for performance optimization
        max_workers = min(os.cpu_count() or 4, 8)
        self.executor = concurrent.futures.ProcessPoolExecutor(max_workers=max_workers)

        def on_closing():
            self.executor.shutdown(wait=False)
            self.root.destroy()
        self.root.protocol("WM_DELETE_WINDOW", on_closing)
        
        self.font_title = ctk.CTkFont(family="Microsoft YaHei", size=15, weight="bold")
        self.font_main = ctk.CTkFont(family="Microsoft YaHei", size=13)
        self.font_code = ctk.CTkFont(family="Consolas", size=13)

        self.setup_icons()
        self.setup_ui()
        
        # 绑定拖拽事件
        try:
            import windnd
            windnd.hook_dropfiles(self.root, func=self.on_files_dropped)
        except Exception:
            pass
        
        # 处理初始传入的文件（例如“打开方式”或拖动到图标）
        if initial_files:
            self.root.after(1500, lambda: self.on_files_dropped(initial_files))

    def _check_drop_queue(self):
        try:
            # 安全地将拖入的文件拿到主线程标准事件流中
            while True:
                files = self.drop_queue.get_nowait()
                self._process_dropped_files(files)
        except queue.Empty:
            pass
        self.root.after(100, self._check_drop_queue)

    def on_files_dropped(self, files):
        # 仅将文件压入队列，彻底不涉及 Tkinter UI 的 Tcl 调用
        self.drop_queue.put(files)

    def _process_dropped_files(self, files):
        decoded_paths = []
        for f in files:
            if isinstance(f, bytes):
                try: decoded_paths.append(f.decode('gbk'))
                except UnicodeDecodeError: decoded_paths.append(f.decode('utf-8'))
            else:
                decoded_paths.append(str(f))
                
        audio_paths = [p for p in decoded_paths if p.lower().endswith(('.wav', '.mp3'))]
        if not audio_paths:
            messagebox.showwarning("提示", "拖入的文件中没有支持的音频文件 (.wav, .mp3)")
            return
            
        self.handle_input_files(audio_paths)

    def handle_input_files(self, paths):
        if len(paths) == 1:
            path = paths[0]
            self.start_loading("正在分析音频区段...")
            def check_audio():
                try:
                    # 使用全局线程池运行 parselmouth
                    future = self.executor.submit(check_audio_segments, path)
                    seg_count = future.result()
                    
                    # 避免在主线程加载 Sound，如果在后台加载可以防止卡顿和可能的 GIL 崩溃
                    snd = None
                    if seg_count > 1:
                        snd = parselmouth.Sound(path)
                        
                    def update_ui():
                        self.stop_loading()
                        if seg_count <= 1:
                            self.tabview.set("多条独立音频")
                            self.pending_batch_paths = [path]
                            self.lbl_batch_files.configure(text=f"已选 1 个文件 (从拖拽)", text_color="#2563EB")
                            self.lbl_status.configure(text="独立音频就绪", text_color="#10B981")
                        else:
                            self.tabview.set("单条长音频")
                            self.pending_long_snd = snd
                            self.lbl_long_file.configure(text=os.path.basename(path) + " (从拖拽)", text_color="#2563EB")
                            self.lbl_status.configure(text="长音频就绪", text_color="#10B981")
                    self.root.after(0, update_ui)
                except Exception as e:
                    self.root.after(0, self.stop_loading)
                    self.root.after(0, lambda: messagebox.showerror("错误", f"读取音频失败: {e}"))
            threading.Thread(target=check_audio, daemon=True).start()
        else:
            self.tabview.set("多条独立音频")
            self.pending_batch_paths = paths
            self.lbl_batch_files.configure(text=f"已选 {len(paths)} 个文件 (从拖拽)", text_color="#2563EB")
            self.lbl_status.configure(text="独立音频就绪，正在后台分析...", text_color="#10B981")
            self.start_background_batch_processing(paths)
        
    def setup_icons(self):
        # 预加载所有图标
        icon_path = "icons"
        if not os.path.exists(icon_path):
            icon_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "icons")
            
        self.icons = {}
        icon_files = {
            "audio": "audio_file.png", "cut": "cut.png", "batch": "batch.png",
            "eye": "eye.png", "list": "list.png", "plus": "plus.png",
            "play": "play.png", "save": "save.png", "check": "check.png",
            "bulb": "bulb.png", "points": "points.png", "energy": "energy.png",
            "duration": "duration.png", "trim": "trim.png", "tag": "tag.png",
            "tab_single": "tab_single.png", "tab_batch": "tab_batch.png",
            "status_success": "status_success.png", 
            "status_loading": "status_loading.png", 
            "status_error": "status_error.png",
            "warning": "warning.png",
            "import": "import_file.png", "ai_prompt": "ai_prompt.png", "copy": "copy_icon.png",
            "import_white": "import_white.png", "copy_white": "copy_white.png", "check_white": "check_white.png"
        }
        from PIL import ImageTk
        self.tk_icons = {}
        for key, filename in icon_files.items():
            path = os.path.join(icon_path, filename)
            if os.path.exists(path):
                img = Image.open(path)
                self.icons[key] = ctk.CTkImage(light_image=img, dark_image=img, size=(20, 20))
                # Resize for ttk.Treeview
                img_tk = img.resize((16, 16), Image.Resampling.LANCZOS)
                self.tk_icons[key] = ImageTk.PhotoImage(img_tk)
            else:
                self.icons[key] = None
                self.tk_icons[key] = None

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
        # --- Sidebar Container ---
        sidebar_frame = ctk.CTkFrame(self.root, width=320, fg_color="transparent")
        sidebar_frame.pack(side=tk.LEFT, fill=tk.Y, padx=10, pady=(10, 5))
        
        # --- Header (Fixed at top) ---
        header_frame = ctk.CTkFrame(sidebar_frame, fg_color="transparent")
        header_frame.pack(side=tk.TOP, fill=tk.X, pady=(5, 10))

        left_scrollable = ctk.CTkScrollableFrame(sidebar_frame, width=320, fg_color="transparent")
        left_scrollable.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        self._make_scrollable_auto(left_scrollable)

        btn_kwargs_primary = {"corner_radius": 20, "height": 38, "font": self.font_main}
        btn_kwargs_secondary = {"corner_radius": 20, "height": 38, "font": self.font_main, 
                                "fg_color": "#E5E7EB", "text_color": "#1F2937", "hover_color": "#D1D5DB"}
        
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

        self.tabview = ctk.CTkTabview(left_scrollable, height=250, corner_radius=12, fg_color="white", 
                                      segmented_button_selected_color="#60A5FA", segmented_button_fg_color="#F3F4F6")
        self.tabview.pack(fill=tk.X, pady=(0, 10))
        tab_long = self.tabview.add("单条长音频")
        tab_batch = self.tabview.add("多条独立音频")
        
        self.tabview._segmented_button._buttons_dict["单条长音频"].configure(image=self.icons.get("tab_single"), compound="left")
        self.tabview._segmented_button._buttons_dict["多条独立音频"].configure(image=self.icons.get("tab_batch"), compound="left")

        CTkReleaseButton(tab_long, text=" 导入长音频", image=self.icons.get("audio"), compound="left", command=self.load_long_audio, **btn_kwargs_primary).pack(fill=tk.X, padx=10, pady=(15, 2))
        self.lbl_long_file = ctk.CTkLabel(tab_long, text="未选择", font=self.font_main, text_color="#6B7280")
        self.lbl_long_file.pack(pady=(0, 10))
        row_mode1_btns = ctk.CTkFrame(tab_long, fg_color="transparent")
        row_mode1_btns.pack(fill=tk.X, padx=10, pady=(0, 15))
        CTkReleaseButton(row_mode1_btns, text="导入字表", image=self.icons.get("cut"), compound="left", command=lambda: self.open_text_dialog('long'), **btn_kwargs_secondary, width=110).pack(side=tk.LEFT, expand=True, padx=(0, 5))
        CTkReleaseButton(row_mode1_btns, text="段落编辑器", image=self.icons.get("eye"), compound="left", command=self.open_visual_splitter, **btn_kwargs_secondary, width=110).pack(side=tk.RIGHT, expand=True, padx=(5, 0))

        CTkReleaseButton(tab_batch, text=" 选择多个音频文件", image=self.icons.get("batch"), compound="left", command=self.load_batch_audio, **btn_kwargs_primary).pack(fill=tk.X, padx=10, pady=(15, 2))
        self.lbl_batch_files = ctk.CTkLabel(tab_batch, text="未选择", font=self.font_main, text_color="#6B7280")
        self.lbl_batch_files.pack(pady=(0, 10))
        row_mode2_btns = ctk.CTkFrame(tab_batch, fg_color="transparent")
        row_mode2_btns.pack(fill=tk.X, padx=10, pady=(0, 15))
        CTkReleaseButton(row_mode2_btns, text="文件名提取", image=self.icons.get("tag"), compound="left", command=self.process_batch_direct, **btn_kwargs_secondary, width=110).pack(side=tk.LEFT, expand=True, padx=(0, 5))
        CTkReleaseButton(row_mode2_btns, text="导入字表", image=self.icons.get("list"), compound="left", command=lambda: self.open_text_dialog('batch'), **btn_kwargs_secondary, width=110).pack(side=tk.RIGHT, expand=True, padx=(5, 0))

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
        lbl_dur = ctk.CTkLabel(row_dur, text=" 排除声母:", image=self.icons.get("duration"), compound="left", text_color="#374151", font=self.font_main)
        lbl_dur.pack(side=tk.LEFT)
        self.slider_dur = ctk.CTkSlider(row_dur, from_=0.00, to=0.15, number_of_steps=15, width=100, height=16,
                                        command=lambda v: self._on_slider_change(v, self.entry_min_dur, 'skip_front'))
        self.slider_dur.set(self.last_params['skip_front'])
        self.slider_dur.pack(side=tk.LEFT, padx=10)
        self.var_min_dur = ctk.StringVar(value=f"{self.last_params['skip_front']:.2f}")
        self.entry_min_dur = ctk.CTkEntry(row_dur, textvariable=self.var_min_dur, width=60, justify="center", corner_radius=20, height=26)
        self.entry_min_dur.pack(side=tk.RIGHT)
        self.setup_entry_behavior(self.entry_min_dur, 'skip_front')
        ToolTip(lbl_dur, "切除有效波形最前方的时长(秒)，用于排除声母(辅音)的干扰。")

        # Pitch 范围参数
        row_pitch = ctk.CTkFrame(card_params, fg_color="transparent")
        row_pitch.pack(fill=tk.X, padx=15, pady=5)
        ctk.CTkLabel(row_pitch, text=" F0 范围 (Hz):", image=self.icons.get("points"), compound="left", text_color="#374151", font=self.font_main).pack(side=tk.LEFT)
        self.entry_pitch_ceiling = ctk.CTkEntry(row_pitch, width=55, justify="center", corner_radius=20, height=26)
        self.entry_pitch_ceiling.insert(0, str(self.last_params['pitch_ceiling']))
        self.entry_pitch_ceiling.pack(side=tk.RIGHT)
        self.setup_entry_behavior(self.entry_pitch_ceiling, 'pitch_ceiling')
        ctk.CTkLabel(row_pitch, text="~", text_color="#6B7280").pack(side=tk.RIGHT, padx=2)
        self.entry_pitch_floor = ctk.CTkEntry(row_pitch, width=55, justify="center", corner_radius=20, height=26)
        self.entry_pitch_floor.insert(0, str(self.last_params['pitch_floor']))
        self.entry_pitch_floor.pack(side=tk.RIGHT)
        self.setup_entry_behavior(self.entry_pitch_floor, 'pitch_floor')
        ToolTip(row_pitch, "Praat pitch 分析的频率范围。\n男声建议 30~300 (嘎裂声设为30或40)，女声/儿童建议 100~500。")
        
        # 浊音阈值参数
        row_voicing = ctk.CTkFrame(card_params, fg_color="transparent")
        row_voicing.pack(fill=tk.X, padx=15, pady=5)
        ctk.CTkLabel(row_voicing, text=" 浊音阈值:", image=self.icons.get("points"), compound="left", text_color="#374151", font=self.font_main).pack(side=tk.LEFT)
        self.entry_voicing_threshold = ctk.CTkEntry(row_voicing, width=55, justify="center", corner_radius=20, height=26)
        self.entry_voicing_threshold.insert(0, f"{self.last_params['voicing_threshold']:.2f}")
        self.entry_voicing_threshold.pack(side=tk.RIGHT)
        self.setup_entry_behavior(self.entry_voicing_threshold, 'voicing_threshold')
        ToolTip(row_voicing, "浊音阈值 (Voicing Threshold)，默认 0.25。\n数值越低对嘎裂声 (气泡音) 等不规则波形越宽容。")
        
        row_trim = ctk.CTkFrame(card_params, fg_color="transparent")
        row_trim.pack(fill=tk.X, padx=15, pady=(10, 15))
        self.lbl_trim_icon = ctk.CTkLabel(row_trim, text="", image=self.icons.get("trim"))
        self.lbl_trim_icon.pack(side=tk.LEFT, padx=(0, 5))
        self.switch_trim_silence = ctk.CTkSwitch(row_trim, text="开启边缘静音裁切", font=self.font_main, 
                                                 progress_color="#10B981", text_color="#374151", command=self.on_trim_silence_toggle)
        self.switch_trim_silence.pack(side=tk.LEFT)
        self.switch_trim_silence.select() 
        ToolTip(self.switch_trim_silence, "开启后将在图表上自动忽略首尾低于 -50dB 的绝对静音区域，\n让有效波形占满屏幕。")

        # 全局应用按钮 (固定在底部)
        self.btn_apply_all = CTkReleaseButton(sidebar_frame, text="  全局应用", image=self.icons.get("check_white"), compound="left", 
                                              command=self.recalculate_all_audio, corner_radius=20, height=44, font=self.font_title,
                                              fg_color="#3B82F6", hover_color="#2563EB")
        self.btn_apply_all.pack(fill=tk.X, pady=(10, 5))

        # 实例化右侧树状面板 (先于中间面板初始化以确保正确的 pack 顺序)
        self.tree_panel = ProjectTreePanel(
            parent=self.root,
            icons=self.icons,
            tk_icons=self.tk_icons,
            items_dict=self.items,
            app_state_params=self.last_params,
            on_item_selected_callback=self.on_tree_item_selected,
            on_clear_canvas_callback=self.on_clear_canvas_callback
        )

        # 实例化中间画布面板 (最后初始化并 expand=True 以占据剩余空间)
        self.spectrogram_panel = SpectrogramPanel(
            parent=self.root, 
            icons=self.icons,
            on_time_changed_callback=self.on_spectrogram_time_changed,
            on_auto_detect_callback=self.on_spectrogram_auto_detect,
            on_export_callback=self.on_export_callback
        )
        self.spectrogram_panel.switch_trim_silence = self.switch_trim_silence

    # --- 交互回调 ---
    def on_tree_item_selected(self, iid):
        item = self.items[iid]
        if (not item.get('snd') or not item.get('pitch')) and item.get('path'):
            self.set_status(f"正在读取音频: {item['label']}...", "#3B82F6", "status_loading")
            self.root.update_idletasks()
            try:
                item['snd'] = parselmouth.Sound(item['path'])
                item['pitch'] = item['snd'].to_pitch_ac(time_step=None, pitch_floor=self.last_params['pitch_floor'], pitch_ceiling=self.last_params['pitch_ceiling'], voicing_threshold=self.last_params.get('voicing_threshold', 0.25), very_accurate=True, octave_jump_cost=0.9)
                item['pitch_floor'] = self.last_params['pitch_floor']
                item['pitch_ceiling'] = self.last_params['pitch_ceiling']
                item['voicing_threshold'] = self.last_params.get('voicing_threshold', 0.25)
                self.set_status("就绪", "#10B981", "status_success")
            except Exception as e:
                self.set_status(f"加载失败: {str(e)}", "#EF4444", "status_error")
                return
                
        # 同步侧边栏 UI 参数为当前选中项的独立参数（所见即所得）
        if 'pitch_floor' in item:
            self.entry_pitch_floor.delete(0, tk.END)
            self.entry_pitch_floor.insert(0, str(int(item['pitch_floor'])))
            self.entry_pitch_floor._last_val = str(int(item['pitch_floor']))
        if 'pitch_ceiling' in item:
            self.entry_pitch_ceiling.delete(0, tk.END)
            self.entry_pitch_ceiling.insert(0, str(int(item['pitch_ceiling'])))
            self.entry_pitch_ceiling._last_val = str(int(item['pitch_ceiling']))
        if 'voicing_threshold' in item:
            self.entry_voicing_threshold.delete(0, tk.END)
            self.entry_voicing_threshold.insert(0, str(float(item['voicing_threshold'])))
            self.entry_voicing_threshold._last_val = str(float(item['voicing_threshold']))
            
        self.spectrogram_panel.load_item(item)

    def on_spectrogram_time_changed(self, item):
        # Time has been manually changed. We need to clear the cached preview_f0
        # so that _check_item_has_empty_data in project_tree will properly recalculate
        # and not rely on stale preview_f0 values.
        if 'preview_f0' in item:
            item.pop('preview_f0')
        if 'has_empty_data' in item:
            item.pop('has_empty_data')

        self.tree_panel.update_preview()
        for iid, it in list(self.items.items()):
            if it is item:
                self.tree_panel.update_item_icon(iid)
                break

    def on_export_callback(self):
        self.tree_panel.export_project()

    def on_clear_canvas_callback(self):
        self.spectrogram_panel.clear_canvas()

    def on_spectrogram_auto_detect(self):
        item = self.spectrogram_panel.current_item
        if not item: return
        snd = item['snd']
        pitch = item['pitch']
        mac_s, mac_e = item['macro_start'], item['macro_end']
        
        def run():
            try:
                self.root.after(0, lambda: self.start_loading("正在智能识别..."))
                mic_s, mic_e, raw_s, raw_e = self._microscopic_vowel_nucleus(snd, pitch, mac_s, mac_e)
                
                label = item['label'].replace(" (缺失)", "")
                if len(label) > 1:
                    inner_splits = auto_split_inner_word(snd, mic_s, mic_e, len(label))
                else:
                    inner_splits = []
                    
                def update_ui():
                    item['start'] = mic_s
                    item['end'] = mic_e
                    item['raw_start'] = raw_s
                    item['raw_end'] = raw_e
                    item['inner_splits'] = inner_splits
                    
                    if len(label) > 1:
                        from modules.audio_core import auto_split_to_chars_bounds
                        item['chars_bounds'] = auto_split_to_chars_bounds(snd, mic_s, mic_e, inner_splits, len(label), self.last_params)
                    else:
                        item['chars_bounds'] = [[mic_s, mic_e]]

                    self.spectrogram_panel.var_t_start.set(f"{mic_s:.3f}")
                    self.spectrogram_panel.var_t_end.set(f"{mic_e:.3f}")
                    self.spectrogram_panel.plot_item_spectrogram()
                    self.spectrogram_panel.update_ui_times()
                    self.stop_loading("识别完成")
                self.root.after(0, update_ui)
            except Exception as e:
                self.root.after(0, lambda: self.set_status(f"识别失败: {str(e)}", "#EF4444", "status_error"))
                self.root.after(0, self.stop_loading)
        threading.Thread(target=run, daemon=True).start()

    # --- 辅助方法 ---
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

                    def update_icons_bg():
                        self.root.after(0, lambda: self.start_loading("正在重新检测图标..."))
                        keys = list(self.items.keys())

                        def update_batch(start_idx):
                            end_idx = min(start_idx + 10, len(keys))
                            for iid in keys[start_idx:end_idx]:
                                self.tree_panel.update_item_icon(iid)

                            self.set_progress(end_idx / max(1, len(keys)))

                            if end_idx < len(keys):
                                self.root.after(5, lambda: update_batch(end_idx))
                            else:
                                self.tree_panel.update_preview()
                                self.stop_loading()

                        self.root.after(10, lambda: update_batch(0))
                    update_icons_bg()
            elif key == 'db':
                val = float(self.entry_drop_db.get())
                if val != self.last_params['db']:
                    self.last_params['db'] = val
                    self.slider_db.set(val)
                    self.recalculate_current_item()
            elif key == 'skip_front':
                val = float(self.entry_min_dur.get())
                if val != self.last_params['skip_front']:
                    self.last_params['skip_front'] = val
                    self.slider_dur.set(val)
                    self.recalculate_current_item()
            elif key == 'pitch_floor':
                val = int(self.entry_pitch_floor.get())
                if val != self.last_params['pitch_floor']:
                    self.last_params['pitch_floor'] = val
                    self.recalculate_current_item(recompute_pitch=True)
            elif key == 'pitch_ceiling':
                val = int(self.entry_pitch_ceiling.get())
                if val != self.last_params['pitch_ceiling']:
                    self.last_params['pitch_ceiling'] = val
                    self.recalculate_current_item(recompute_pitch=True)
        except Exception: pass

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

    def _make_scrollable_auto(self, scrollable_frame):
        """
        使 CTkScrollableFrame 的滚动条仅在内容溢出时显示。
        """
        scrollbar = scrollable_frame._scrollbar
        orig_set = scrollbar.set
        
        def auto_set(low, high):
            # 使用更严谨的判断，并加入 update 以防止布局死循环
            if float(low) <= 0.0 and float(high) >= 1.0:
                if scrollbar.winfo_ismapped():
                    scrollbar.grid_remove()
            else:
                if not scrollbar.winfo_ismapped():
                    scrollbar.grid()
            orig_set(low, high)
            
        scrollbar.set = auto_set
        # 初始检查一次
        self.root.after(100, lambda: scrollbar.set(*scrollbar.get()))

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
            if param_key in ['pts', 'db', 'skip_front', 'pitch_floor', 'pitch_ceiling', 'voicing_threshold']: self.on_param_change()

        entry.bind("<Enter>", on_enter)
        entry.bind("<Leave>", on_leave)
        entry.bind("<FocusIn>", on_focus_in)
        entry.bind("<FocusOut>", on_focus_out)
        entry.bind("<Return>", lambda e: self.root.focus_set())

    def on_param_change(self, event=None):
        try:
            new_db = float(self.var_drop_db.get())
            new_skip = float(self.var_min_dur.get())
            new_pts = int(self.entry_points.get())
            changed_algo = False
            
            if new_db != self.last_params['db']:
                self.last_params['db'] = new_db
                changed_algo = True
            if new_skip != self.last_params['skip_front']:
                self.last_params['skip_front'] = new_skip
                changed_algo = True
                
            recompute_pitch = False
            new_floor = int(self.entry_pitch_floor.get())
            new_ceiling = int(self.entry_pitch_ceiling.get())
            new_voicing = float(self.entry_voicing_threshold.get())
            
            # 无论如何先更新全局 last_params
            if new_floor != self.last_params['pitch_floor']:
                self.last_params['pitch_floor'] = new_floor
                recompute_pitch = True
            if new_ceiling != self.last_params['pitch_ceiling']:
                self.last_params['pitch_ceiling'] = new_ceiling
                recompute_pitch = True
            if new_voicing != self.last_params['voicing_threshold']:
                self.last_params['voicing_threshold'] = new_voicing
                recompute_pitch = True
                
            # 即使全局 last_params 没变，只要当前输入框的值与“当前选中项的专属参数”不同，也要强制重算当前项
            curr_item = getattr(self, 'spectrogram_panel', None) and self.spectrogram_panel.current_item
            if curr_item:
                if new_floor != curr_item.get('pitch_floor', self.last_params['pitch_floor']): recompute_pitch = True
                if new_ceiling != curr_item.get('pitch_ceiling', self.last_params['pitch_ceiling']): recompute_pitch = True
                if new_voicing != curr_item.get('voicing_threshold', self.last_params.get('voicing_threshold', 0.25)): recompute_pitch = True

            if changed_algo or recompute_pitch: 
                self.recalculate_current_item(recompute_pitch=recompute_pitch)
            if new_pts != self.last_params['pts']:
                self.last_params['pts'] = new_pts
                for iid in list(self.items.keys()):
                    self.tree_panel.update_item_icon(iid)
                self.tree_panel.update_preview()
        except ValueError: pass

    def on_trim_silence_toggle(self):
        self.recalculate_current_item(only_trim_silence=True)

    def recalculate_all_audio(self, only_trim_silence=False, recompute_pitch=True):
        if not self.items: return
        
        # 1. 确保所有 UI 输入框的最新的参数值都已经同步到了 self.last_params 中（在主线程中执行）
        try:
            self.on_param_change()
        except Exception:
            pass
            
        items_snapshot = list(self.items.items())
        total = len(items_snapshot)

        def run():
            self.root.after(0, lambda: self.start_loading("正在重新计算..."))
            trim_silence = self.switch_trim_silence.get()
            
            if only_trim_silence:
                for i, (iid, item) in enumerate(items_snapshot):
                    if item.get('snd') and 'raw_start' in item and 'raw_end' in item:
                        mic_s, mic_e = recalculate_bounds_fast(
                            item['snd'], item['pitch'], item['raw_start'], item['raw_end'], trim_silence
                        )
                        # 等比例缩放内部蓝线和 chars_bounds (如果只因裁切而变化)
                        old_s, old_e = item.get('start', mic_s), item.get('end', mic_e)

                        # 如果用户手动修改了 start/end，我们可以检查 item.get('is_manual_edited', False)
                        # 但由于没有这个 flag，我们依然等比例缩放它们。
                        if 'inner_splits' in item and item['inner_splits'] and old_e > old_s:
                            ratio = (mic_e - mic_s) / (old_e - old_s)
                            item['inner_splits'] = [mic_s + (s - old_s) * ratio for s in item['inner_splits']]
                            if 'chars_bounds' in item and item['chars_bounds']:
                                item['chars_bounds'] = [[mic_s + (c[0] - old_s) * ratio, mic_s + (c[1] - old_s) * ratio] for c in item['chars_bounds']]

                        # 如果有 chars_bounds，则基于 chars_bounds 更新 start/end 以防错位
                        if 'chars_bounds' in item and item['chars_bounds']:
                            item['start'] = item['chars_bounds'][0][0]
                            item['end'] = item['chars_bounds'][-1][1]
                        else:
                            item['start'], item['end'] = mic_s, mic_e
                        
                    if i % 5 == 0 or i == total - 1:
                        self.root.after(0, lambda v=(i + 1) / total: self.set_progress(v))
            else:
                tasks = []
                params = {
                    'db': self.last_params['db'], 
                    'skip_front': self.last_params['skip_front'], 
                    'pitch_floor': self.last_params['pitch_floor'], 
                    'pitch_ceiling': self.last_params['pitch_ceiling'],
                    'voicing_threshold': self.last_params.get('voicing_threshold', 0.25)
                }
                
                valid_items = []
                recomputed_pitches = {}
                for iid, item in items_snapshot:
                    if item.get('snd'):
                        snd = item['snd']
                        snd_id = id(snd)
                        
                        # 如果修改了 Pitch Floor/Ceiling，必须先重新计算 Pitch 对象
                        # 性能优化：按 snd 实例缓存 Pitch，避免长音频模式下被重复计算上千次
                        if recompute_pitch:
                            try:
                                if snd_id not in recomputed_pitches:
                                    recomputed_pitches[snd_id] = snd.to_pitch_ac(time_step=None, voicing_threshold=self.last_params.get('voicing_threshold', 0.25), very_accurate=True, octave_jump_cost=0.9,
                                        pitch_floor=self.last_params['pitch_floor'],
                                        pitch_ceiling=self.last_params['pitch_ceiling']
                                    )
                                item['pitch'] = recomputed_pitches[snd_id]
                            except Exception: pass
                        
                        # 保存计算该项时所用的参数实现所见即所得导出
                        item['pitch_floor'] = params['pitch_floor']
                        item['pitch_ceiling'] = params['pitch_ceiling']
                        item['voicing_threshold'] = params['voicing_threshold']
                        
                        mac_s, mac_e = item['macro_start'], item['macro_end']
                        valid_ms = max(0, mac_s)
                        valid_me = min(snd.get_total_duration(), mac_e)
                        
                        if valid_me > valid_ms:
                            part = snd.extract_part(from_time=valid_ms, to_time=valid_me)
                            
                            # 性能优化：切片 Pitch 数组，大幅减少 IPC 数据量
                            p_xs = item['pitch'].xs()
                            p_freqs = item['pitch'].selected_array['frequency']
                            idx_start = np.searchsorted(p_xs, valid_ms)
                            idx_end = np.searchsorted(p_xs, valid_me)
                            
                            tasks.append({
                                'ms': mac_s, 'me': mac_e,
                                'snd_values': part.values, 'snd_sf': part.sampling_frequency,
                                'pitch_xs': p_xs[idx_start:idx_end], 'pitch_freqs': p_freqs[idx_start:idx_end],
                                'word_label': item['label'].replace(" (缺失)", "")
                            })
                            valid_items.append(item)
                    elif item.get('path'):
                        # 独立音频模式：直接记录路径，后续使用多进程批处理
                        tasks.append({
                            'path': item['path'],
                            'word_label': item['label'].replace(" (缺失)", ""),
                            'type': 'batch'
                        })
                        valid_items.append(item)
                
                if tasks:
                    # 使用 ProcessPoolExecutor 进行 CPU 密集型任务
                    with concurrent.futures.ProcessPoolExecutor(max_workers=min(os.cpu_count() or 4, 8)) as executor:
                        futures = {}
                        for idx, task in enumerate(tasks):
                            if task.get('type') == 'batch':
                                # 独立音频：重新运行 batch_process_worker
                                f = executor.submit(batch_process_worker, task['path'], params, trim_silence, task.get('word_label', ""))
                            else:
                                # 长音频：重新运行 long_process_worker
                                f = executor.submit(
                                    long_process_worker,
                                    task['snd_values'], task['snd_sf'], task['pitch_xs'], task['pitch_freqs'],
                                    task['ms'], task['me'], params, trim_silence, task['word_label']
                                )
                            futures[f] = idx
                        
                        completed = 0
                        for future in concurrent.futures.as_completed(futures):
                            idx = futures[future]
                            try:
                                res = future.result()
                                if res.get('success'):
                                    target_item = valid_items[idx]
                                    if tasks[idx].get('type') == 'batch':
                                        # 合并独立音频处理结果
                                        if not target_item.get('is_manual_edited'):
                                            target_item['start'] = res['start']
                                            target_item['end'] = res['end']
                                            target_item['raw_start'] = res['raw_start']
                                            target_item['raw_end'] = res['raw_end']
                                            target_item['inner_splits'] = res.get('inner_splits', [])
                                            target_item['chars_bounds'] = res.get('chars_bounds', [])
                                        target_item['has_empty_data'] = res.get('has_empty_data', False)
                                        target_item['preview_f0'] = res.get('preview_f0', [])
                                        # 如果是独立音频，还需要把 Cache 也更新了，防止下次加载又是旧的
                                        if target_item.get('path'):
                                            self.audio_cache[target_item['path']] = res
                                    else:
                                        # 合并长音频处理结果
                                        if not target_item.get('is_manual_edited'):
                                            target_item['start'] = res['mis']
                                            target_item['end'] = res['mie']
                                            target_item['raw_start'] = res['raw_s']
                                            target_item['raw_end'] = res['raw_e']
                                            target_item['inner_splits'] = res.get('inner_splits', [])
                                            target_item['chars_bounds'] = res.get('chars_bounds', [])
                                        target_item['has_empty_data'] = res.get('has_empty_data', False)
                                        target_item['preview_f0'] = res.get('preview_f0', [])
                            except Exception: pass
                            
                            completed += 1
                            if completed % max(1, len(futures)//10) == 0 or completed == len(futures):
                                self.root.after(0, lambda v=completed/len(futures): self.set_progress(v))

            def finalize():
                if self.spectrogram_panel.current_item:
                    self.spectrogram_panel.plot_item_spectrogram()
                    self.spectrogram_panel.update_ui_times()
                
                # 更新所有列表项的图标（刷新警告标志）
                for iid in list(self.items.keys()):
                    self.tree_panel.update_item_icon(iid)
                    
                self.tree_panel.update_preview()
                self.stop_loading("全局参数已应用")

            self.root.after(0, finalize)
        threading.Thread(target=run, daemon=True).start()

    def recalculate_current_item(self, only_trim_silence=False, recompute_pitch=False):
        """仅针对当前正在编辑的项重新计算参数（暂不影响全局）"""
        item = self.spectrogram_panel.current_item
        if not item: return
        
        def run():
            try:
                self.root.after(0, lambda: self.set_status("正在更新当前项...", "#3B82F6", "status_loading"))
                
                # 如果是独立音频模式且没有加载 Sound 对象
                if not item.get('snd') and item.get('path'):
                    item['snd'] = parselmouth.Sound(item['path'])
                    # 总是为单项重新生成 pitch 确保准确性
                    item['pitch'] = item['snd'].to_pitch_ac(
                        time_step=None, voicing_threshold=self.last_params.get('voicing_threshold', 0.25), very_accurate=True, octave_jump_cost=0.9,
                        pitch_floor=self.last_params['pitch_floor'],
                        pitch_ceiling=self.last_params['pitch_ceiling']
                    )
                    item['pitch_floor'] = self.last_params['pitch_floor']
                    item['pitch_ceiling'] = self.last_params['pitch_ceiling']
                    item['voicing_threshold'] = self.last_params.get('voicing_threshold', 0.25)
                    # 独立音频的宏观边界就是全文
                    item['macro_start'] = 0.0
                    item['macro_end'] = item['snd'].get_total_duration()

                # 如果修改了 Pitch Floor/Ceiling，重新计算该项的 Pitch
                if recompute_pitch and item.get('snd'):
                    item['pitch'] = item['snd'].to_pitch_ac(
                        time_step=None, voicing_threshold=self.last_params.get('voicing_threshold', 0.25), very_accurate=True, octave_jump_cost=0.9,
                        pitch_floor=self.last_params['pitch_floor'],
                        pitch_ceiling=self.last_params['pitch_ceiling']
                    )
                    item['pitch_floor'] = self.last_params['pitch_floor']
                    item['pitch_ceiling'] = self.last_params['pitch_ceiling']
                    item['voicing_threshold'] = self.last_params.get('voicing_threshold', 0.25)
                    
                if item.get('snd') and 'macro_start' in item and 'macro_end' in item:
                    if only_trim_silence:
                        mic_s, mic_e = recalculate_bounds_fast(
                            item['snd'], item['pitch'], item['raw_start'], item['raw_end'], self.switch_trim_silence.get()
                        )
                        # 等比例缩放内部蓝线
                        old_s, old_e = item.get('start', mic_s), item.get('end', mic_e)
                        if 'inner_splits' in item and item['inner_splits'] and old_e > old_s:
                            ratio = (mic_e - mic_s) / (old_e - old_s)
                            item['inner_splits'] = [mic_s + (s - old_s) * ratio for s in item['inner_splits']]
                            if 'chars_bounds' in item and item['chars_bounds']:
                                item['chars_bounds'] = [[mic_s + (c[0] - old_s) * ratio, mic_s + (c[1] - old_s) * ratio] for c in item['chars_bounds']]
                        item['start'], item['end'] = mic_s, mic_e
                    else:
                        mic_s, mic_e, raw_s, raw_e = self._microscopic_vowel_nucleus(
                            item['snd'], item['pitch'], item['macro_start'], item['macro_end']
                        )
                        item['start'], item['end'] = mic_s, mic_e
                        item['raw_start'], item['raw_end'] = raw_s, raw_e
                        
                        label = item['label'].replace(" (缺失)", "")
                        if len(label) > 1:
                            item['inner_splits'] = auto_split_inner_word(item['snd'], mic_s, mic_e, len(label))
                            from modules.audio_core import auto_split_to_chars_bounds
                            item['chars_bounds'] = auto_split_to_chars_bounds(item['snd'], mic_s, mic_e, item['inner_splits'], len(label), self.last_params)
                        else:
                            item['inner_splits'] = []
                            item['chars_bounds'] = [[mic_s, mic_e]]
                            
                # 重新生成 11 点预览数据用于警告图标状态更新
                if item.get('snd') and item.get('pitch'):
                    preview_times = np.linspace(item['start'], item['end'], 11)
                    preview_f0 = [item['pitch'].get_value_at_time(t) for t in preview_times]
                    item['preview_f0'] = [0.0 if (np.isnan(hz) or hz <= 0) else hz for hz in preview_f0]
                    item['has_empty_data'] = any(f == 0.0 for f in item['preview_f0'])
                            
                def finalize():
                    self.spectrogram_panel.plot_item_spectrogram()
                    self.spectrogram_panel.update_ui_times()
                    # 更新树图标（警告标志）
                    for iid, it in list(self.items.items()):
                        if it is item:
                            self.tree_panel.update_item_icon(iid)
                            break
                    self.tree_panel.update_preview()
                    self.set_status("当前项已更新", "#10B981", "status_success")
                    
                self.root.after(0, finalize)
            except Exception as e:
                self.root.after(0, lambda: self.set_status(f"更新失败: {str(e)}", "#EF4444", "status_error"))
                
        threading.Thread(target=run, daemon=True).start()

    def _microscopic_vowel_nucleus(self, snd, global_pitch, t_min, t_max):
        return core_microscopic_vowel_nucleus(
            snd, global_pitch, t_min, t_max, 
            self.last_params['db'], self.last_params['skip_front'], 
            self.switch_trim_silence.get()
        )

    # --- 核心调度 ---
    def load_long_audio(self):
        path = filedialog.askopenfilename(filetypes=[("Audio Files", "*.wav *.mp3")])
        if not path: return
        self.lbl_long_file.configure(text=os.path.basename(path), text_color="#9CA3AF")
        def run():
            self.root.after(0, lambda: self.start_loading(f"正在加载: {os.path.basename(path)}"))
            try:
                snd = parselmouth.Sound(path)
                def done():
                    self.pending_long_snd = snd
                    self.lbl_long_file.configure(text=os.path.basename(path), text_color="#2563EB")
                    self.stop_loading("长音频就绪")
                self.root.after(0, done)
            except Exception as e:
                self.root.after(0, lambda: self.stop_loading(f"加载失败: {e}"))
        threading.Thread(target=run, daemon=True).start()

    def open_visual_splitter(self):
        if not self.pending_long_snd:
            return messagebox.showwarning("提示", "请先导入一条长音频。")
            
        existing_items = []
        if self.items:
            for iid, item in self.items.items():
                if item.get('snd') is not None and 'macro_start' in item:
                    existing_items.append({
                        'id': iid,
                        'label': item['label'],
                        'start': item['macro_start'], 
                        'end': item['macro_end'],
                        'inner_splits': item.get('inner_splits', [])
                    })
            existing_items.sort(key=lambda x: x['start'])
            
            # 追加剩余的未分配音频段
            if hasattr(self, 'current_macro_segments') and self.current_macro_segments:
                num_assigned = len(existing_items)
                if num_assigned < len(self.current_macro_segments):
                    for i in range(num_assigned, len(self.current_macro_segments)):
                        ms, me = self.current_macro_segments[i]
                        existing_items.append({
                            'id': None,
                            'label': f"【未分配段】",
                            'start': ms,
                            'end': me,
                            'inner_splits': []
                        })
            
        if existing_items:
            # 已有字表匹配结果 → 直接进入 edit 模式微调
            VisualSplitter(self.root, self.pending_long_snd, self.icons, self.on_visual_split_confirm, existing_items=existing_items)
        else:
            # 未导入字表 → 先自动跑 VAD，然后进入 review 模式
            def run_vad():
                self.root.after(0, lambda: self.start_loading("正在自动检测音频区段..."))
                try:
                    vad_segs = macroscopic_vad(self.pending_long_snd)
                    def open_splitter():
                        self.stop_loading(f"检测到 {len(vad_segs)} 个区段")
                        VisualSplitter(self.root, self.pending_long_snd, self.icons, 
                                      self.on_visual_split_confirm, vad_segments=vad_segs)
                    self.root.after(0, open_splitter)
                except Exception as e:
                    self.root.after(0, lambda: self.stop_loading(f"检测失败: {e}"))
            threading.Thread(target=run_vad, daemon=True).start()

    def on_visual_split_confirm(self, segments, is_update=False, deleted_count=0):
        if is_update:
            # segments 包含了所有的有效段映射：{'id': new_iid, 'old_id': old_iid, 'start', 'end', 'inner_splits', 'is_modified'}
            mapped_segs = {seg['id']: seg for seg in segments}
            
            # 备份旧的 micro 边界，以便在未修改宏观边界时重用（避免覆盖手动微调且节省算力）
            old_micro_bounds = {}
            for iid, item in self.items.items():
                if item.get('start') is not None and item.get('end') is not None:
                    old_micro_bounds[iid] = (item['start'], item['end'], item.get('inner_splits', []), item.get('chars_bounds', []))
            
            # 1. 收集树中所有的 word items (保持顺序)
            all_iids = []
            for grp_name in self.tree_panel.project_groups:
                grp_node = self.tree_panel.group_nodes[grp_name]
                for child in self.tree_panel.tree.get_children(grp_node):
                    if child in self.items:
                        all_iids.append(child)
            
            # 提前准备好全局 Pitch，避免在循环中重复计算耗时巨大
            global_pitch_cache = None
            
            # 2. 应用映射
            for iid in all_iids:
                item = self.items[iid]
                if iid in mapped_segs:
                    # 有对应的音频段
                    seg = mapped_segs[iid]
                    item['macro_start'] = seg['start']
                    item['macro_end'] = seg['end']
                    
                    # 恢复 snd 和 pitch
                    if not item.get('snd'):
                        item['snd'] = self.pending_long_snd
                        if global_pitch_cache is None:
                            global_pitch_cache = self.pending_long_snd.to_pitch_ac(time_step=None, voicing_threshold=self.last_params.get('voicing_threshold', 0.25), very_accurate=True, octave_jump_cost=0.9,
                                pitch_floor=self.last_params['pitch_floor'], 
                                pitch_ceiling=self.last_params['pitch_ceiling']
                            )
                        item['pitch'] = global_pitch_cache
                    
                    # 核心优化：如果没有被拖拽修改边界，且原来就有微观边界，直接继承！
                    if not seg.get('is_modified') and seg.get('old_id') and seg['old_id'] in old_micro_bounds:
                        item['start'], item['end'], item['inner_splits'], item['chars_bounds'] = old_micro_bounds[seg['old_id']]
                        if 'raw_start' in self.items[seg['old_id']]:
                            item['raw_start'] = self.items[seg['old_id']]['raw_start']
                            item['raw_end'] = self.items[seg['old_id']]['raw_end']
                    else:
                        if seg.get('is_modified'):
                            # 词语模式：如果用户在界面上明确改了红线蓝线，那用户的操作就是绝对真理！不跑自动识别覆盖。
                            item['start'] = seg['start']
                            item['end'] = seg['end']
                            item['inner_splits'] = list(seg.get('inner_splits', []))
                            
                            # 重新计算独立的字符边界 chars_bounds，确保跟蓝线位置同步！
                            label = item['label'].replace(" (缺失)", "")
                            if len(label) > 1:
                                from modules.audio_core import auto_split_to_chars_bounds
                                item['chars_bounds'] = auto_split_to_chars_bounds(
                                    item['snd'], item['start'], item['end'],
                                    item['inner_splits'], len(label), self.last_params
                                )
                            else:
                                item['chars_bounds'] = [[item['start'], item['end']]]
                                
                            item['raw_start'] = seg['start']
                            item['raw_end'] = seg['end']
                        else:
                            # 纯新分配的自动识别段落，调用完整识别流
                            mic_s, mic_e, raw_s, raw_e = self._microscopic_vowel_nucleus(
                                item['snd'], item['pitch'], item['macro_start'], item['macro_end']
                            )
                            item['start'], item['end'] = mic_s, mic_e
                            item['raw_start'], item['raw_end'] = raw_s, raw_e
                            
                            label = item['label'].replace(" (缺失)", "")
                            if len(label) > 1:
                                item['inner_splits'] = auto_split_inner_word(item['snd'], mic_s, mic_e, len(label))
                                from modules.audio_core import auto_split_to_chars_bounds
                                item['chars_bounds'] = auto_split_to_chars_bounds(item['snd'], mic_s, mic_e, item['inner_splits'], len(label), self.last_params)
                            else:
                                item['inner_splits'] = []
                                item['chars_bounds'] = [[mic_s, mic_e]]
                    
                    # 移除可能的 "(缺失)" 后缀
                    if item['label'].endswith(" (缺失)"):
                        item['label'] = item['label'].replace(" (缺失)", "")
                    self.tree_panel.tree.item(iid, text=item['label'])
                    self.tree_panel.update_item_icon(iid)
                else:
                    # 音频段不够了，标记为缺失
                    item['snd'] = None
                    item['pitch'] = None
                    item['macro_start'] = None
                    item['macro_end'] = None
                    item['start'] = None
                    item['end'] = None
                    item['inner_splits'] = []
                    
                    if not item['label'].endswith(" (缺失)"):
                        self.tree_panel.tree.item(iid, text=item['label'] + " (缺失)")
                    self.tree_panel.tree.item(iid, image='')

            if self.spectrogram_panel.current_item:
                self.spectrogram_panel.clear_canvas()
            
            # 3. 更新全局的宏观区段记录，以便下次打开时仍能顺延
            self.current_macro_segments = [(seg['start'], seg['end']) for seg in segments]
            
            self.tree_panel.update_preview()
            
            deleted_msg = f"\n由于您删除了音频段，后续字表已自动向前顺延对齐。" if deleted_count else ""
            messagebox.showinfo("提示", f"手动微调已应用，时间边界已更新。{deleted_msg}")
        else:
            self.manual_segments = segments
            messagebox.showinfo("提示", f"全新手动切分完成，共 {len(segments)} 个片段。\n现在请点击“导入字表”来匹配文本。")

    def process_long_with_wordlist(self, raw_text):
        groups, flat_words = parse_wordlist(raw_text)
        if not flat_words: return
        
        def run():
            self.root.after(0, lambda: self.start_loading("正在处理长音频..."))
            self.root.after(0, self.tree_panel.clear_all)
            
            snd = self.pending_long_snd
            global_pitch = snd.to_pitch_ac(time_step=None, pitch_floor=self.last_params['pitch_floor'], pitch_ceiling=self.last_params['pitch_ceiling'], voicing_threshold=self.last_params.get('voicing_threshold', 0.25), very_accurate=True, octave_jump_cost=0.9)
            
            if hasattr(self, 'manual_segments') and self.manual_segments:
                macro_segments = self.manual_segments
            else:
                macro_segments = macroscopic_vad(snd)
            
            self.current_macro_segments = macro_segments.copy()
            total = len(flat_words)
            results = []
            
            # 准备参数
            params = {'db': self.last_params['db'], 'skip_front': self.last_params['skip_front'], 'pitch_floor': self.last_params['pitch_floor'], 'pitch_ceiling': self.last_params['pitch_ceiling'], 'voicing_threshold': self.last_params.get('voicing_threshold', 0.25)}
            trim = self.switch_trim_silence.get()
            pitch_xs = global_pitch.xs()
            pitch_freqs = global_pitch.selected_array['frequency']
            
            # 构建任务数据
            tasks = []
            word_idx = 0
            for grp in groups:
                for word in grp['items']:
                    if word_idx < len(macro_segments):
                        ms, me = macro_segments[word_idx]
                        
                        # 提前提取小段音频的数据和采样率
                        valid_ms = max(0, ms)
                        valid_me = min(snd.get_total_duration(), me)
                        if valid_me > valid_ms:
                            part = snd.extract_part(from_time=valid_ms, to_time=valid_me)
                            snd_values = part.values
                            snd_sf = part.sampling_frequency
                            
                            # 性能优化：切片 Pitch 数组
                            idx_start = np.searchsorted(pitch_xs, valid_ms)
                            idx_end = np.searchsorted(pitch_xs, valid_me)
                            sliced_xs = pitch_xs[idx_start:idx_end]
                            sliced_freqs = pitch_freqs[idx_start:idx_end]
                            
                            tasks.append({
                                'word': word, 'group': grp['group'], 'ms': ms, 'me': me,
                                'snd_values': snd_values, 'snd_sf': snd_sf, 
                                'pitch_xs': sliced_xs, 'pitch_freqs': sliced_freqs,
                                'missing': False
                            })
                        else:
                            tasks.append({'word': word, 'group': grp['group'], 'missing': True})
                        word_idx += 1
                    else:
                        tasks.append({'word': word, 'group': grp['group'], 'missing': True})
            
            # 多进程执行
            with concurrent.futures.ProcessPoolExecutor(max_workers=min(os.cpu_count() or 4, 8)) as executor:
                futures = {}
                for idx, task in enumerate(tasks):
                    if not task.get('missing'):
                        f = executor.submit(
                            long_process_worker,
                            task['snd_values'], task['snd_sf'], task['pitch_xs'], task['pitch_freqs'],
                            task['ms'], task['me'], params, trim, task['word']
                        )
                        futures[f] = idx
                
                # 等待完成
                completed_count = 0
                for future in concurrent.futures.as_completed(futures):
                    idx = futures[future]
                    res = future.result()
                    if res.get('success'):
                        tasks[idx]['mis'] = res['mis']
                        tasks[idx]['mie'] = res['mie']
                        tasks[idx]['raw_s'] = res['raw_s']
                        tasks[idx]['raw_e'] = res['raw_e']
                        tasks[idx]['inner_splits'] = res.get('inner_splits', [])
                        if 'chars_bounds' in res: tasks[idx]['chars_bounds'] = res['chars_bounds']
                        tasks[idx]['has_empty_data'] = res['has_empty_data']
                    else:
                        tasks[idx]['missing'] = True # fallback
                    
                    completed_count += 1
                    if completed_count % 10 == 0 or completed_count == len(futures):
                        self.root.after(0, lambda v=completed_count/len(futures) if len(futures) else 1: self.set_progress(v))
            
            results = tasks
            
            def finalize():
                for res in results:
                    gid = self.tree_panel.ensure_group(res['group'])
                    if not res.get('missing'):
                        has_empty = res.get('has_empty_data', False)
                        img = self.tk_icons.get('warning', '') if has_empty else ''
                        iid = self.tree_panel.tree.insert(gid, tk.END, text=res['word'], tags=('item',), image=img)
                        self.items[iid] = {
                            'label': res['word'], 'group': res['group'], 'snd': snd, 'pitch': global_pitch,
                            'macro_start': res['ms'], 'macro_end': res['me'], 
                            'start': res['mis'], 'end': res['mie'],
                            'inner_splits': res.get('inner_splits', []),
                            'chars_bounds': res.get('chars_bounds', []),
                            'raw_start': res.get('raw_s', res['mis']), 'raw_end': res.get('raw_e', res['mie']),
                            'pitch_floor': params['pitch_floor'],
                            'pitch_ceiling': params['pitch_ceiling'],
                            'voicing_threshold': params['voicing_threshold']
                        }
                        self.tree_panel.update_item_icon(iid)
                    else:
                        iid = self.tree_panel.tree.insert(gid, tk.END, text=res['word'] + " (缺失)", tags=('item',))
                        self.items[iid] = {'label': res['word'], 'group': res['group'], 'snd': None, 'start': None, 'end': None, 'inner_splits': []}
                
                self.stop_loading("长音频切分完成")
                self.tree_panel.select_first_item()
                if hasattr(self, 'manual_segments'): self.manual_segments = None
            
            self.root.after(0, finalize)
        threading.Thread(target=run, daemon=True).start()

    def load_batch_audio(self):
        paths = filedialog.askopenfilenames(filetypes=[("Audio Files", "*.wav *.mp3")])
        if not paths: return
        self.pending_batch_paths = paths
        self.lbl_batch_files.configure(text=f"已选 {len(paths)} 个文件", text_color="#2563EB")
        self.lbl_status.configure(text="独立音频就绪，正在后台分析...", text_color="#10B981")
        self.start_background_batch_processing(paths)

    def start_background_batch_processing(self, paths):
        def run():
            params = {'db': self.last_params['db'], 'skip_front': self.last_params['skip_front'], 'pitch_floor': self.last_params['pitch_floor'], 'pitch_ceiling': self.last_params['pitch_ceiling'], 'voicing_threshold': self.last_params.get('voicing_threshold', 0.25)}
            trim = self.switch_trim_silence.get()
            paths_to_process = [p for p in paths if p not in self.audio_cache]
            if not paths_to_process:
                self.root.after(0, lambda: self.lbl_status.configure(text="后台分析完成", text_color="#10B981"))
                return
            
            total = len(paths_to_process)
            self.root.after(0, lambda: self.start_loading(f"正在后台预分析 {total} 个音频..."))
            
            futures = {self.executor.submit(batch_process_worker, p, params, trim): p for p in paths_to_process}
            for i, future in enumerate(concurrent.futures.as_completed(futures)):
                p = futures[future]
                try:
                    self.audio_cache[p] = future.result()
                except Exception as e:
                    self.audio_cache[p] = {'success': False, 'error': str(e), 'path': p}
                
                self.root.after(0, lambda v=(i+1)/total: self.set_progress(v))
            
            self.root.after(0, lambda: self.stop_loading("后台分析完成"))
        threading.Thread(target=run, daemon=True).start()

    def process_batch_direct(self):
        if not self.pending_batch_paths:
            return messagebox.showwarning("提示", "请先选择多个音频文件")
            
        def run():
            self.root.after(0, lambda: self.start_loading("正在并行批量提取..."))
            self.root.after(0, self.tree_panel.clear_all)
            
            total = len(self.pending_batch_paths)
            params = {'db': self.last_params['db'], 'skip_front': self.last_params['skip_front'], 'pitch_floor': self.last_params['pitch_floor'], 'pitch_ceiling': self.last_params['pitch_ceiling'], 'voicing_threshold': self.last_params.get('voicing_threshold', 0.25)}
            trim = self.switch_trim_silence.get()
            
            results = []
            futures = {}
            executor = concurrent.futures.ProcessPoolExecutor(max_workers=min(os.cpu_count() or 4, 8))
            for i, p in enumerate(self.pending_batch_paths):
                if p in self.audio_cache:
                    results.append((i, self.audio_cache[p]))
                else:
                    futures[executor.submit(batch_process_worker, p, params, trim)] = i
                    
            if futures:
                for i, future in enumerate(concurrent.futures.as_completed(futures)):
                    orig_idx = futures[future]
                    try: 
                        res = future.result()
                        self.audio_cache[self.pending_batch_paths[orig_idx]] = res
                        results.append((orig_idx, res))
                    except Exception as e: print(f"Error: {e}")
                    
                    if i % 2 == 0 or i == len(futures) - 1:
                        self.root.after(0, lambda v=(len(results))/total: self.set_progress(v))
            else:
                self.root.after(0, lambda: self.set_progress(1.0))
            
            executor.shutdown(wait=False)

            def finalize():
                results.sort(key=lambda x: x[0])
                gid = self.tree_panel.ensure_group("独立文件")
                for _, res in results:
                    if res.get('success'):
                        res['group'] = "独立文件"
                        res['pitch_floor'] = params['pitch_floor']
                        res['pitch_ceiling'] = params['pitch_ceiling']
                        res['voicing_threshold'] = params['voicing_threshold']
                        iid = f"batch_{res['label']}_{id(res)}"
                        self.items[iid] = res
                        has_empty = res.get('has_empty_data', False)
                        img = self.tk_icons.get('warning', '') if has_empty else ''
                        self.tree_panel.tree.insert(gid, tk.END, iid=iid, text=res['label'], tags=('item',), image=img)
                        self.tree_panel.update_item_icon(iid)
                
                self.set_status(f"批量并行提取完成 ({len(results)}/{total})")
                self.stop_loading()
                self.tree_panel.select_first_item()
                
            self.root.after(0, finalize)
        threading.Thread(target=run, daemon=True).start()

    def process_batch_with_wordlist(self, raw_text, match_mode='order'):
        groups, flat_words = parse_wordlist(raw_text)
        if not flat_words: return
        
        def run():
            self.root.after(0, lambda: self.start_loading("正在并行匹配独立音频..."))
            self.root.after(0, self.tree_panel.clear_all)
            total = len(flat_words)
            
            tasks = []
            if match_mode == 'fuzzy':
                # 自然排序：确保 1, 2, 10 的顺序
                import re
                def natural_sort_key(s):
                    return [int(text) if text.isdigit() else text.lower()
                            for text in re.split('([0-9]+)', s)]
                
                sorted_paths = sorted(self.pending_batch_paths, key=natural_sort_key)
                used_indices = set()
                
                for grp in groups:
                    group_name = grp['group']
                    for word in grp['items']:
                        idx = fuzzy_match_word_to_path(word, sorted_paths, used_indices=list(used_indices))
                        if idx is not None:
                            path = sorted_paths[idx]
                            used_indices.add(idx)
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
            params = {'db': self.last_params['db'], 'skip_front': self.last_params['skip_front'], 'pitch_floor': self.last_params['pitch_floor'], 'pitch_ceiling': self.last_params['pitch_ceiling'], 'voicing_threshold': self.last_params.get('voicing_threshold', 0.25)}
            trim = self.switch_trim_silence.get()
            
            futures = {}
            executor = concurrent.futures.ProcessPoolExecutor(max_workers=min(os.cpu_count() or 4, 8))
            for i, t in enumerate(tasks):
                if t['missing']:
                    results[i] = {'label': t['word'], 'group': t['group'], 'success': False, 'missing': True}
                else:
                    path = t['path']
                    if path in self.audio_cache:
                        res = self.audio_cache[path]
                        results[i] = {**res, 'missing': False, 'group': t['group']}
                    else:
                        futures[executor.submit(batch_process_worker, path, params, trim, tasks[i]['word'])] = i
            
            total_futures = len(futures) if futures else 1
            done_count = 0
            if futures:
                for future in concurrent.futures.as_completed(futures):
                    orig_idx = futures[future]
                    try:
                        res = future.result()
                        self.audio_cache[tasks[orig_idx]['path']] = res
                        results[orig_idx] = {**res, 'missing': False, 'group': tasks[orig_idx]['group']}
                    except Exception as e:
                        results[orig_idx] = {'label': tasks[orig_idx]['word'], 'group': tasks[orig_idx]['group'], 'success': False, 'missing': True, 'error': str(e)}
                    
                    done_count += 1
                    self.root.after(0, lambda v=done_count/total_futures: self.set_progress(v))
            else:
                self.root.after(0, lambda: self.set_progress(1.0))
            
            executor.shutdown(wait=False)

            # 在后台线程补全字表不匹配时的蓝线修复（因为基于Cache抓取的可能是错误的）
            for i, res in enumerate(results):
                if res and not res.get('missing') and res.get('success'):
                    word = tasks[i]['word']
                    cached_label = res.get('label', '')
                    if len(word) > 1 and len(cached_label) != len(word):
                        try:
                            snd = parselmouth.Sound(res['path'])
                            res['inner_splits'] = auto_split_inner_word(snd, res['start'], res['end'], len(word))
                            from modules.audio_core import auto_split_to_chars_bounds
                            res['chars_bounds'] = auto_split_to_chars_bounds(snd, res['start'], res['end'], res['inner_splits'], len(word), self.last_params)
                        except Exception:
                            res['inner_splits'] = []
                            res['chars_bounds'] = [[res['start'], res['end']]]
                    elif len(word) <= 1:
                        res['inner_splits'] = []
                        res['chars_bounds'] = [[res['start'], res['end']]]

            def finalize():
                matched_count = 0
                for i, res in enumerate(results):
                    gid = self.tree_panel.ensure_group(res['group'])
                    if not res.get('missing') and res.get('success'):
                        res['group'] = tasks[i]['group']
                        res['label'] = tasks[i]['word']
                        if 'pitch_floor' not in res:
                            res['pitch_floor'] = params['pitch_floor']
                            res['pitch_ceiling'] = params['pitch_ceiling']
                            res['voicing_threshold'] = params['voicing_threshold']
                        display = f"{res['label']} ← {os.path.basename(res['path'])}" if match_mode == 'fuzzy' else res['label']
                        iid = f"batch_wl_{res['label']}_{id(res)}"
                        
                        has_empty = res.get('has_empty_data', False)
                        img = self.tk_icons.get('warning', '') if has_empty else ''
                        self.tree_panel.tree.insert(gid, tk.END, iid=iid, text=display, tags=('item',), image=img)
                        
                        self.items[iid] = res
                        self.tree_panel.update_item_icon(iid)
                        matched_count += 1
                    else:
                        suffix = " (未匹配)" if match_mode == 'fuzzy' else " (缺失)"
                        iid = f"missing_{res['label']}_{id(res)}"
                        self.tree_panel.tree.insert(gid, tk.END, iid=iid, text=res['label'] + suffix, tags=('item',))
                        self.items[iid] = {'label': res['label'], 'group': res['group'], 'snd': None, 'start': None, 'end': None, 'inner_splits': []}
                
                self.stop_loading(f"并行处理完成: {matched_count}/{total}")
                self.tree_panel.select_first_item()
                
            self.root.after(0, finalize)
        threading.Thread(target=run, daemon=True).start()

    def open_text_dialog(self, mode):
        if mode == 'long' and not self.pending_long_snd: 
            return messagebox.showwarning("提示", "请先导入一条长音频。")
        if mode == 'batch' and not self.pending_batch_paths: 
            return messagebox.showwarning("提示", "请先选择独立音频。")
            
        dlg = ctk.CTkToplevel(self.root)
        dlg.title("导入字表")
        w, h = 450, (600 if mode == 'batch' else 520)
        # 居中计算
        sw = dlg.winfo_screenwidth()
        sh = dlg.winfo_screenheight()
        x = (sw - w) // 2
        y = (sh - h) // 2
        dlg.geometry(f"{w}x{h}+{x}+{y}")
        dlg.configure(fg_color="#f3f4f6")
        
        dlg.transient(self.root) 
        dlg.focus_set()           
        
        # 1. 顶部工具栏 (导入文件 / 复制AI提示词)
        toolbar = ctk.CTkFrame(dlg, fg_color="transparent")
        toolbar.pack(fill=tk.X, padx=20, pady=(15, 0))
        
        def load_txt():
            path = filedialog.askopenfilename(filetypes=[("Text/CSV Files", "*.txt *.csv"), ("All Files", "*.*")])
            if not path: return
            try:
                with open(path, 'r', encoding='utf-8') as f: text = f.read()
            except UnicodeDecodeError:
                try:
                    with open(path, 'r', encoding='gbk') as f: text = f.read()
                except Exception as e:
                    return messagebox.showerror("错误", f"读取文件失败: {e}")
            text_box.delete("1.0", tk.END)
            text_box.insert("1.0", text)
            update_stats()

        def copy_prompt():
            prompt = "请帮我把下面这段字表转换成特定格式：\n1. 每个组别名称用【】包裹并独占一行\n2. 组别下的词/字跟在组别名称下面，可以一行一个，也可以用空格或逗号分隔\n3. 去除所有不相关的序号、拼音 and 多余的空行\n\n示例输出格式：\n【阴平】\n八 扒 吧\n【双音节】\n音频 视频\n\n以下是我的原始字表，请直接返回转换后的结果即可：\n\n[在此处粘贴你的字表]"
            self.root.clipboard_clear()
            self.root.clipboard_append(prompt)
            messagebox.showinfo("成功", "AI 整理提示词已复制！\n您可以前往 ChatGPT / 豆包 / DeepSeek 等平台粘贴使用。", parent=dlg)

        btn_import = ctk.CTkButton(toolbar, text=" 导入 .txt文件", image=self.icons.get("import_white"), compound="left", 
                                   width=110, height=28, corner_radius=14, fg_color="#3B82F6", text_color="white", 
                                   hover_color="#2563EB", command=load_txt)
        btn_import.pack(side=tk.LEFT)

        if mode == 'long':
            def load_textgrid():
                path = filedialog.askopenfilename(filetypes=[("TextGrid Files", "*.TextGrid"), ("All Files", "*.*")])
                if not path: return
                try:
                    with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                        tg_content = f.read()
                    import re
                    pattern = re.compile(r'intervals\s*\[\d+\]:\s*xmin\s*=\s*([\d\.]+)\s*xmax\s*=\s*([\d\.]+)\s*text\s*=\s*"([^"]*)"', re.MULTILINE)
                    segments = []
                    words = []
                    for match in pattern.finditer(tg_content):
                        xmin = float(match.group(1))
                        xmax = float(match.group(2))
                        text = match.group(3).strip()
                        if text:
                            segments.append({'start': xmin, 'end': xmax})
                            words.append(text)
                    if not segments:
                        return messagebox.showwarning("提示", "未能从 TextGrid 提取到有效的标注段。")
                    self.manual_segments = segments

                    # 生成假文本填入输入框
                    display_text = "【TextGrid 导入】\n" + " ".join(words)
                    text_box.delete("1.0", tk.END)
                    text_box.insert("1.0", display_text)
                    update_stats()
                    messagebox.showinfo("成功", f"成功提取了 {len(segments)} 个时间段及其文本，已自动赋值时间边界。\n您可以直接点击“开始匹配提取”。")
                except Exception as e:
                    messagebox.showerror("错误", f"解析 TextGrid 失败: {e}")

            btn_import_tg = ctk.CTkButton(toolbar, text=" 导入 TextGrid", image=self.icons.get("import_white"), compound="left",
                                       width=110, height=28, corner_radius=14, fg_color="#8B5CF6", text_color="white",
                                       hover_color="#7C3AED", command=load_textgrid)
            btn_import_tg.pack(side=tk.LEFT, padx=(10, 0))

        btn_prompt = ctk.CTkButton(toolbar, text=" 复制 AI 整理提示词", image=self.icons.get("copy_white"), compound="left", 
                                   width=150, height=28, corner_radius=14, fg_color="#F59E0B", text_color="white", 
                                   hover_color="#D97706", command=copy_prompt)
        btn_prompt.pack(side=tk.LEFT, padx=10)

        # 2. 文本输入区
        # 创建一个容器来包裹 Textbox 和 浮动占位符
        text_frame = ctk.CTkFrame(dlg, fg_color="transparent")
        text_frame.pack(padx=20, pady=(10, 5), fill=tk.BOTH, expand=True)

        text_box = ctk.CTkTextbox(text_frame, width=380, height=220, corner_radius=8, border_width=1, border_color="#D1D5DB")
        text_box.pack(fill=tk.BOTH, expand=True)
        
        placeholder_text = "请在此处粘贴字表文本，或点击下方按钮导入文件。\n\n格式规范：\n1. 组别名称：使用 【】、[] 或 # 开头（如：【一组】）。\n2. 字/词项：组别下方的行即为字词，支持空格、逗号、分号或 Tab 分隔。\n3. 匹配逻辑：程序将按此处的顺序依次匹配音频区段。\n\n示例格式：\n【一组】\n妈 麻 马 骂\n#双音节\n音频, 视频, 提取\n[三字项]\n录音笔；笔记本；打字机"

        # 创建浮动占位符标签
        placeholder_label = ctk.CTkLabel(text_box, text=placeholder_text, text_color="#9CA3AF", 
                                         justify=tk.LEFT, font=("Microsoft YaHei", 12), anchor="nw")
        placeholder_label.place(x=10, y=10)
        
        # 点击占位符时聚焦输入框
        placeholder_label.bind("<Button-1>", lambda e: text_box.focus_set())

        # 3. 实时统计栏
        lbl_stats = ctk.CTkLabel(dlg, text="实时统计：已识别 0 个组别 | 0 个项", text_color="#6B7280", font=("Microsoft YaHei", 12))
        lbl_stats.pack(pady=(0, 10), padx=20, anchor="w")

        def update_stats(event=None):
            raw_text = text_box.get("1.0", tk.END)
            groups, flat_words = parse_wordlist(raw_text)
            color = "#10B981" if flat_words else "#6B7280"
            lbl_stats.configure(text=f"实时统计：已识别 {len(groups)} 个组别 | {len(flat_words)} 个项", text_color=color)
            
            text_box.tag_remove("group_title", "1.0", tk.END)
            text_box.tag_remove("word_item", "1.0", tk.END)

            # 控制浮动占位符的显示/隐藏
            if not raw_text.strip():
                placeholder_label.place(x=10, y=10)
                lbl_stats.configure(text="实时统计：待输入...", text_color="#6B7280")
                return
            else:
                placeholder_label.place_forget()

            text_box.tag_config("group_title", foreground="#2563EB") 
            text_box.tag_config("word_item", foreground="#10B981")
            
            lines = raw_text.split('\n')
            current_line_idx = 1
            import re
            for line in lines:
                stripped = line.strip()
                if not stripped:
                    current_line_idx += 1
                    continue
                if stripped.startswith('【') or stripped.startswith('[') or stripped.startswith('［') or stripped.startswith('#'):
                    text_box.tag_add("group_title", f"{current_line_idx}.0", f"{current_line_idx}.end")
                else:
                    words = [w for w in re.split(r'[\s,，、]+', stripped) if w]
                    start_char = 0
                    for w in words:
                        idx = line.find(w, start_char)
                        if idx != -1:
                            text_box.tag_add("word_item", f"{current_line_idx}.{idx}", f"{current_line_idx}.{idx+len(w)}")
                            start_char = idx + len(w)
                current_line_idx += 1
            
        text_box.bind("<KeyRelease>", update_stats)

        
        # 4. 匹配参数区
        match_mode_var = ctk.StringVar(value="fuzzy")
        if mode == 'batch':
            frame_match = ctk.CTkFrame(dlg, fg_color="#F3F4F6", corner_radius=8)
            frame_match.pack(padx=20, pady=10, fill=tk.X)
            ctk.CTkLabel(frame_match, text="匹配方式", text_color="#4B5563").pack(anchor=tk.W, padx=10, pady=(5, 0))
            ctk.CTkRadioButton(frame_match, text="模糊匹配 (按文件名自动识别)", variable=match_mode_var, value="fuzzy").pack(anchor=tk.W, padx=15, pady=5)
            ctk.CTkRadioButton(frame_match, text="顺序匹配 (按字表顺序依次对应)", variable=match_mode_var, value="order").pack(anchor=tk.W, padx=15, pady=(0, 10))
        
        # 5. 执行处理与防错预检
        def process():
            raw_text = text_box.get("1.0", tk.END)
            groups, flat_words = parse_wordlist(raw_text)
            
            if not flat_words:
                return messagebox.showwarning("提示", "未识别到任何数据项，请检查文本格式。")
                
            # --- 防呆设计：数量与音频数匹配预检 ---
            if mode == 'batch':
                audio_count = len(self.pending_batch_paths)
                word_count = len(flat_words)
                if audio_count != word_count:
                    if not messagebox.askyesno("数量不匹配警告", f"检测到 {audio_count} 个独立音频文件，但字表内包含 {word_count} 个项。\n\n数量不一致可能导致映射错位或部分缺失，是否继续强制提取？"):
                        return
            elif mode == 'long':
                if hasattr(self, 'manual_segments') and self.manual_segments:
                    seg_count = len(self.manual_segments)
                    word_count = len(flat_words)
                    if seg_count != word_count:
                        if not messagebox.askyesno("数量不匹配警告", f"您刚才手动切分了 {seg_count} 个片段，但字表内包含 {word_count} 个项。\n\n数量不一致将导致音频与文本错位，是否继续强制提取？"):
                            return
                            
            dlg.destroy()
            if mode == 'long': self.process_long_with_wordlist(raw_text)
            else: self.process_batch_with_wordlist(raw_text, match_mode=match_mode_var.get())
            
        CTkReleaseButton(dlg, text="开始匹配提取", command=process, corner_radius=20, height=40, font=self.font_main).pack(pady=15, anchor="e", padx=20)
        
        # 初始触发一次统计
        update_stats()