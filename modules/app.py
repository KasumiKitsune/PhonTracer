import tkinter as tk
from tkinter import filedialog, messagebox
import customtkinter as ctk
# pyrefly: ignore [missing-import]
import parselmouth
import os
import threading
import concurrent.futures
from PIL import Image

# 导入拆分后的模块
from .ui_widgets import ToolTip, CTkReleaseButton
from .data_utils import parse_wordlist, fuzzy_match_word_to_path
from .audio_core import core_microscopic_vowel_nucleus, batch_process_worker, macroscopic_vad, check_audio_segments, long_process_worker, recalculate_bounds_fast
from .visual_splitter import VisualSplitter
from .spectrogram_panel import SpectrogramPanel
from .project_tree import ProjectTreePanel

class PhoneticsApp:
    def __init__(self, root, initial_files=None):
        self.root = root
        self.root.title("PhonTracer - 声调提取与分析工具")
        self.root.geometry("1200x700")
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
        
        # 全局数据源 (Source of Truth)
        self.items = {}
        self.audio_cache = {}
        
        self.debounce_timer = None
        
        self.last_params = {
            'pts': 11,
            'db': 60.0,
            'skip_front': 0.00,
            'pitch_floor': 75,
            'pitch_ceiling': 600
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
            self.root.after(100, lambda: self.on_files_dropped(initial_files))

    def on_files_dropped(self, files):
        decoded_paths = []
        for f in files:
            if isinstance(f, bytes):
                try: decoded_paths.append(f.decode('gbk'))
                except UnicodeDecodeError: decoded_paths.append(f.decode('utf-8'))
            else:
                decoded_paths.append(str(f))
                
        audio_paths = [p for p in decoded_paths if p.lower().endswith(('.wav', '.mp3'))]
        if not audio_paths:
            return messagebox.showwarning("提示", "拖入的文件中没有支持的音频文件 (.wav, .mp3)")
            
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
                    def update_ui():
                        self.stop_loading()
                        if seg_count <= 1:
                            self.tabview.set("多条独立音频")
                            self.pending_batch_paths = [path]
                            self.lbl_batch_files.configure(text=f"已选 1 个文件 (从拖拽)", text_color="#2563EB")
                            self.lbl_status.configure(text="独立音频就绪", text_color="#10B981")
                        else:
                            self.tabview.set("单条长音频")
                            self.pending_long_snd = parselmouth.Sound(path)
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
            "import_white": "import_white.png", "copy_white": "copy_white.png"
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
        CTkReleaseButton(tab_long, text=" 导入字表", image=self.icons.get("cut"), compound="left", command=lambda: self.open_text_dialog('long'), **btn_kwargs_secondary).pack(fill=tk.X, padx=10, pady=(0, 5))
        CTkReleaseButton(tab_long, text=" 音频段落编辑", image=self.icons.get("eye"), compound="left", command=self.open_visual_splitter, **btn_kwargs_secondary).pack(fill=tk.X, padx=10, pady=(0, 15))

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
        ToolTip(row_pitch, "Praat pitch 分析的频率范围。\n男声建议 75~300，女声/儿童建议 100~500。")
        
        row_trim = ctk.CTkFrame(card_params, fg_color="transparent")
        row_trim.pack(fill=tk.X, padx=15, pady=(10, 15))
        self.lbl_trim_icon = ctk.CTkLabel(row_trim, text="", image=self.icons.get("trim"))
        self.lbl_trim_icon.pack(side=tk.LEFT, padx=(0, 5))
        self.switch_trim_silence = ctk.CTkSwitch(row_trim, text="开启边缘静音裁切", font=self.font_main, 
                                                 progress_color="#10B981", text_color="#374151", command=self.on_trim_silence_toggle)
        self.switch_trim_silence.pack(side=tk.LEFT)
        self.switch_trim_silence.select() 
        ToolTip(self.switch_trim_silence, "开启后将在图表上自动忽略首尾低于 -50dB 的绝对静音区域，\n让有效波形占满屏幕。")

        # 实例化中间画布面板
        self.spectrogram_panel = SpectrogramPanel(
            parent=self.root, 
            icons=self.icons,
            on_time_changed_callback=self.on_spectrogram_time_changed,
            on_auto_detect_callback=self.on_spectrogram_auto_detect,
            on_export_callback=self.on_export_callback
        )
        self.spectrogram_panel.switch_trim_silence = self.switch_trim_silence
        
        # 实例化右侧树状面板
        self.tree_panel = ProjectTreePanel(
            parent=self.root,
            icons=self.icons,
            tk_icons=self.tk_icons,
            items_dict=self.items,
            app_state_params=self.last_params,
            on_item_selected_callback=self.on_tree_item_selected,
            on_clear_canvas_callback=self.on_clear_canvas_callback
        )

    # --- 交互回调 ---
    def on_tree_item_selected(self, iid):
        item = self.items[iid]
        if (not item.get('snd') or not item.get('pitch')) and item.get('path'):
            self.set_status(f"正在读取音频: {item['label']}...", "#3B82F6", "status_loading")
            self.root.update_idletasks()
            try:
                item['snd'] = parselmouth.Sound(item['path'])
                item['pitch'] = item['snd'].to_pitch(pitch_floor=self.last_params['pitch_floor'], pitch_ceiling=self.last_params['pitch_ceiling'])
                self.set_status("就绪", "#10B981", "status_success")
            except Exception as e:
                self.set_status(f"加载失败: {str(e)}", "#EF4444", "status_error")
                return
        self.spectrogram_panel.load_item(item)

    def on_spectrogram_time_changed(self, item):
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
                def update_ui():
                    item['start'] = mic_s
                    item['end'] = mic_e
                    item['raw_start'] = raw_s
                    item['raw_end'] = raw_e
                    self.spectrogram_panel.var_t_start.set(f"{mic_s:.3f}")
                    self.spectrogram_panel.var_t_end.set(f"{mic_e:.3f}")
                    self.spectrogram_panel.update_lines(mic_s, mic_e)
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
                    for iid in list(self.items.keys()):
                        self.tree_panel.update_item_icon(iid)
                    self.tree_panel.update_preview()
            elif key == 'db':
                val = float(self.entry_drop_db.get())
                if val != self.last_params['db']:
                    self.last_params['db'] = val
                    self.slider_db.set(val)
                    self.recalculate_all_audio()
            elif key == 'skip_front':
                val = float(self.entry_min_dur.get())
                if val != self.last_params['skip_front']:
                    self.last_params['skip_front'] = val
                    self.slider_dur.set(val)
                    self.recalculate_all_audio()
            elif key == 'pitch_floor':
                val = int(self.entry_pitch_floor.get())
                if val != self.last_params['pitch_floor']:
                    self.last_params['pitch_floor'] = val
            elif key == 'pitch_ceiling':
                val = int(self.entry_pitch_ceiling.get())
                if val != self.last_params['pitch_ceiling']:
                    self.last_params['pitch_ceiling'] = val
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
            if param_key in ['pts', 'db', 'skip_front']: self.on_param_change()

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
                
            if changed_algo: self.recalculate_all_audio()
            if new_pts != self.last_params['pts']:
                self.last_params['pts'] = new_pts
                for iid in list(self.items.keys()):
                    self.tree_panel.update_item_icon(iid)
                self.tree_panel.update_preview()
        except ValueError: pass

    def on_trim_silence_toggle(self):
        self.recalculate_all_audio(only_trim_silence=True)

    def recalculate_all_audio(self, only_trim_silence=False):
        if not self.items: return
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
                        item['start'], item['end'] = mic_s, mic_e
                    if i % 5 == 0 or i == total - 1:
                        self.root.after(0, lambda v=(i + 1) / total: self.set_progress(v))
            else:
                tasks = []
                params = {
                    'db': self.last_params['db'], 
                    'skip_front': self.last_params['skip_front'], 
                    'pitch_floor': self.last_params['pitch_floor'], 
                    'pitch_ceiling': self.last_params['pitch_ceiling']
                }
                
                valid_items = []
                for iid, item in items_snapshot:
                    if item.get('snd'):
                        snd = item['snd']
                        mac_s, mac_e = item['macro_start'], item['macro_end']
                        valid_ms = max(0, mac_s)
                        valid_me = min(snd.get_total_duration(), mac_e)
                        
                        if valid_me > valid_ms:
                            part = snd.extract_part(from_time=valid_ms, to_time=valid_me)
                            tasks.append({
                                'ms': mac_s, 'me': mac_e,
                                'snd_values': part.values, 'snd_sf': part.sampling_frequency,
                                'pitch_xs': item['pitch'].xs(), 'pitch_freqs': item['pitch'].selected_array['frequency']
                            })
                            valid_items.append(item)
                
                if tasks:
                    with concurrent.futures.ProcessPoolExecutor(max_workers=min(os.cpu_count() or 4, 8)) as executor:
                        futures = {}
                        for idx, task in enumerate(tasks):
                            f = executor.submit(
                                long_process_worker,
                                task['snd_values'], task['snd_sf'], task['pitch_xs'], task['pitch_freqs'],
                                task['ms'], task['me'], params, trim_silence
                            )
                            futures[f] = idx
                        
                        completed = 0
                        for future in concurrent.futures.as_completed(futures):
                            idx = futures[future]
                            res = future.result()
                            if res.get('success'):
                                valid_items[idx]['start'] = res['mis']
                                valid_items[idx]['end'] = res['mie']
                                valid_items[idx]['raw_start'] = res['raw_s']
                                valid_items[idx]['raw_end'] = res['raw_e']
                            
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
                        'end': item['macro_end']
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
                            'end': me
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
            # segments 包含了所有的有效段映射：{'id': new_iid, 'old_id': old_iid, 'start', 'end', 'is_modified'}
            mapped_segs = {seg['id']: seg for seg in segments}
            
            # 备份旧的 micro 边界，以便在未修改宏观边界时重用（避免覆盖手动微调且节省算力）
            old_micro_bounds = {}
            for iid, item in self.items.items():
                if item.get('start') is not None and item.get('end') is not None:
                    old_micro_bounds[iid] = (item['start'], item['end'])
            
            # 1. 收集树中所有的 word items (保持顺序)
            all_iids = []
            for grp_name in self.tree_panel.project_groups:
                grp_node = self.tree_panel.group_nodes[grp_name]
                for child in self.tree_panel.tree.get_children(grp_node):
                    if child in self.items:
                        all_iids.append(child)
            
            # 2. 应用映射
            for iid in all_iids:
                item = self.items[iid]
                if iid in mapped_segs:
                    # 有对应的音频段
                    seg = mapped_segs[iid]
                    item['macro_start'] = seg['start']
                    item['macro_end'] = seg['end']
                    
                    # 恢复 snd 和 pitch（如果之前是缺失状态，需要从 pending_long_snd 获取）
                    if not item.get('snd'):
                        item['snd'] = self.pending_long_snd
                        item['pitch'] = item['snd'].to_pitch(
                            pitch_floor=self.last_params['pitch_floor'], 
                            pitch_ceiling=self.last_params['pitch_ceiling']
                        )
                    
                    # 核心优化：如果该音频段没有被拖拽修改边界，且原来就有微观边界，直接继承！
                    if not seg.get('is_modified') and seg.get('old_id') and seg['old_id'] in old_micro_bounds:
                        item['start'], item['end'] = old_micro_bounds[seg['old_id']]
                        if 'raw_start' in self.items[seg['old_id']]:
                            item['raw_start'] = self.items[seg['old_id']]['raw_start']
                            item['raw_end'] = self.items[seg['old_id']]['raw_end']
                    else:
                        mic_s, mic_e, raw_s, raw_e = self._microscopic_vowel_nucleus(
                            item['snd'], item['pitch'], item['macro_start'], item['macro_end']
                        )
                        item['start'], item['end'] = mic_s, mic_e
                        item['raw_start'], item['raw_end'] = raw_s, raw_e
                    
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
            global_pitch = snd.to_pitch(pitch_floor=self.last_params['pitch_floor'], pitch_ceiling=self.last_params['pitch_ceiling'])
            
            if hasattr(self, 'manual_segments') and self.manual_segments:
                macro_segments = self.manual_segments
            else:
                macro_segments = macroscopic_vad(snd)
            
            self.current_macro_segments = macro_segments.copy()
            total = len(flat_words)
            results = []
            
            # 准备参数
            params = {'db': self.last_params['db'], 'skip_front': self.last_params['skip_front'], 'pitch_floor': self.last_params['pitch_floor'], 'pitch_ceiling': self.last_params['pitch_ceiling']}
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
                        # 为了避免边界问题，确保时间合理
                        valid_ms = max(0, ms)
                        valid_me = min(snd.get_total_duration(), me)
                        if valid_me > valid_ms:
                            part = snd.extract_part(from_time=valid_ms, to_time=valid_me)
                            snd_values = part.values
                            snd_sf = part.sampling_frequency
                            
                            tasks.append({
                                'word': word, 'group': grp['group'], 'ms': ms, 'me': me,
                                'snd_values': snd_values, 'snd_sf': snd_sf, 'missing': False
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
                            task['snd_values'], task['snd_sf'], pitch_xs, pitch_freqs,
                            task['ms'], task['me'], params, trim
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
                            'raw_start': res.get('raw_s', res['mis']), 'raw_end': res.get('raw_e', res['mie'])
                        }
                        self.tree_panel.update_item_icon(iid)
                    else:
                        iid = self.tree_panel.tree.insert(gid, tk.END, text=res['word'] + " (缺失)", tags=('item',))
                        self.items[iid] = {'label': res['word'], 'group': res['group'], 'snd': None, 'start': None, 'end': None}
                
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
            params = {'db': self.last_params['db'], 'skip_front': self.last_params['skip_front'], 'pitch_floor': self.last_params['pitch_floor'], 'pitch_ceiling': self.last_params['pitch_ceiling']}
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
            params = {'db': self.last_params['db'], 'skip_front': self.last_params['skip_front'], 'pitch_floor': self.last_params['pitch_floor'], 'pitch_ceiling': self.last_params['pitch_ceiling']}
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
            params = {'db': self.last_params['db'], 'skip_front': self.last_params['skip_front'], 'pitch_floor': self.last_params['pitch_floor'], 'pitch_ceiling': self.last_params['pitch_ceiling']}
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
                        futures[executor.submit(batch_process_worker, path, params, trim)] = i
            
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

            def finalize():
                matched_count = 0
                for i, res in enumerate(results):
                    gid = self.tree_panel.ensure_group(res['group'])
                    if not res.get('missing') and res.get('success'):
                        res['group'] = tasks[i]['group']
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
                        self.items[iid] = {'label': res['label'], 'group': res['group'], 'snd': None, 'start': None, 'end': None}
                
                self.stop_loading(f"并行处理完成: {matched_count}/{total}")
                self.tree_panel.select_first_item()
                
            self.root.after(0, finalize)
        threading.Thread(target=run, daemon=True).start()

    def open_text_dialog(self, mode):
        """完全重构后的文本导入对话框"""
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
        
        dlg.transient(self.root)  # 关键：设置为父窗口的临时窗口，使其保持在父窗口上方
        dlg.focus_set()           # 自动获取焦点
        
        # 2. 文本输入区
        ctk.CTkLabel(dlg, text="请粘贴文本或导入文件，组别前加【】或 #：\n示例格式：\n【阴平】\n八 扒 吧 (支持空格/逗号拆分多个字)", justify=tk.LEFT, text_color="#374151").pack(pady=(5, 5), anchor="w", padx=20)
        
        text_box = ctk.CTkTextbox(dlg, width=380, height=220, corner_radius=8, border_width=1, border_color="#D1D5DB")
        text_box.pack(padx=20, pady=5, fill=tk.BOTH, expand=True)
        
        # 移除 font 参数以兼容 scaling
        text_box.tag_config("group_title", foreground="#2563EB") 
        text_box.tag_config("word_item", foreground="#10B981")

        # 3. 实时统计栏
        lbl_stats = ctk.CTkLabel(dlg, text="实时统计：已识别 0 个组别 | 0 个单字", text_color="#6B7280", font=("Microsoft YaHei", 12))
        lbl_stats.pack(pady=(0, 10), padx=20, anchor="w")

        def update_stats(event=None):
            raw_text = text_box.get("1.0", tk.END)
            groups, flat_words = parse_wordlist(raw_text)
            color = "#10B981" if flat_words else "#6B7280"
            lbl_stats.configure(text=f"实时统计：已识别 {len(groups)} 个组别 | {len(flat_words)} 个单字", text_color=color)
            
            text_box.tag_remove("group_title", "1.0", tk.END)
            text_box.tag_remove("word_item", "1.0", tk.END)
            
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

        # 1. 顶部工具栏 (导入文件 / 复制AI提示词)
        toolbar = ctk.CTkFrame(dlg, fg_color="transparent")
        toolbar.pack(fill=tk.X, padx=20, pady=(15, 5))
        
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
            prompt = "请帮我把下面这段字表转换成特定格式：\n1. 每个组别名称用【】包裹并独占一行\n2. 组别下的字跟在组别名称下面，可以一行一个字，也可以用空格或逗号分隔\n3. 去除所有不相关的序号、拼音 and 多余的空行\n\n示例输出格式：\n【阴平】\n八 扒 吧\n【阳平】\n拔 跋\n\n以下是我的原始字表，请直接返回转换后的结果即可：\n\n[在此处粘贴你的字表]"
            self.root.clipboard_clear()
            self.root.clipboard_append(prompt)
            messagebox.showinfo("成功", "AI 整理提示词已复制！\n您可以前往 ChatGPT / 豆包 / DeepSeek 等平台粘贴使用。", parent=dlg)

        btn_import = ctk.CTkButton(toolbar, text=" 导入 .txt文件", image=self.icons.get("import_white"), compound="left", 
                                   width=110, height=28, corner_radius=14, fg_color="#3B82F6", text_color="white", 
                                   hover_color="#2563EB", command=load_txt)
        btn_import.pack(side=tk.LEFT)
        btn_prompt = ctk.CTkButton(toolbar, text=" 复制 AI 整理提示词", image=self.icons.get("copy_white"), compound="left", 
                                   width=150, height=28, corner_radius=14, fg_color="#F59E0B", text_color="white", 
                                   hover_color="#D97706", command=copy_prompt)
        btn_prompt.pack(side=tk.LEFT, padx=10)
        
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
                return messagebox.showwarning("提示", "未识别到任何单字，请检查文本格式。")
                
            # --- 防呆设计：字数与音频数匹配预检 ---
            if mode == 'batch':
                audio_count = len(self.pending_batch_paths)
                word_count = len(flat_words)
                if audio_count != word_count:
                    if not messagebox.askyesno("数量不匹配警告", f"检测到 {audio_count} 个独立音频文件，但字表内包含 {word_count} 个单字。\n\n数量不一致可能导致映射错位或部分缺失，是否继续强制提取？"):
                        return
            elif mode == 'long':
                if hasattr(self, 'manual_segments') and self.manual_segments:
                    seg_count = len(self.manual_segments)
                    word_count = len(flat_words)
                    if seg_count != word_count:
                        if not messagebox.askyesno("数量不匹配警告", f"您刚才手动切分了 {seg_count} 个片段，但字表内包含 {word_count} 个单字。\n\n数量不一致将导致音频与文本错位，是否继续强制提取？"):
                            return
                            
            dlg.destroy()
            if mode == 'long': self.process_long_with_wordlist(raw_text)
            else: self.process_batch_with_wordlist(raw_text, match_mode=match_mode_var.get())
            
        CTkReleaseButton(dlg, text="开始匹配提取", command=process, corner_radius=20, height=40, font=self.font_main).pack(pady=15)
        
        # 初始触发一次统计
        update_stats()