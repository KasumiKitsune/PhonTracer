import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import customtkinter as ctk
import parselmouth
import numpy as np
import sounddevice as sd
import os
import csv
import threading
import concurrent.futures
from PIL import Image
import windnd

# 导入拆分后的模块
from modules.ui_widgets import ToolTip, CTkReleaseButton
from modules.data_utils import parse_wordlist, fuzzy_match_word_to_path
from modules.audio_core import core_microscopic_vowel_nucleus, batch_process_worker, macroscopic_vad
from modules.visual_splitter import VisualSplitter
from modules.spectrogram_panel import SpectrogramPanel
from modules.project_tree import ProjectTreePanel

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
        
        self.debounce_timer = None
        self.recalc_id_counter = 0
        
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
        
        # 绑定拖拽事件
        try: windnd.hook_dropfiles(self.root, func=self.on_files_dropped)
        except Exception: pass
        
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
                    snd = parselmouth.Sound(path)
                    segments = macroscopic_vad(snd)
                    def update_ui():
                        self.stop_loading()
                        if len(segments) <= 1:
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
            self.lbl_status.configure(text="独立音频就绪", text_color="#10B981")
        
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
        CTkReleaseButton(tab_long, text=" 导入字表并切分", image=self.icons.get("cut"), compound="left", command=lambda: self.open_text_dialog('long'), **btn_kwargs_secondary).pack(fill=tk.X, padx=10, pady=(0, 5))
        CTkReleaseButton(tab_long, text=" 可视化手动切分", image=self.icons.get("eye"), compound="left", command=self.open_visual_splitter, **btn_kwargs_secondary).pack(fill=tk.X, padx=10, pady=(0, 15))

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
                item['pitch'] = item['snd'].to_pitch()
                self.set_status("就绪", "#10B981", "status_success")
            except Exception as e:
                self.set_status(f"加载失败: {str(e)}", "#EF4444", "status_error")
                return
        self.spectrogram_panel.load_item(item)

    def on_spectrogram_time_changed(self, item):
        self.tree_panel.update_preview()

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
                mic_s, mic_e = self._microscopic_vowel_nucleus(snd, pitch, mac_s, mac_e)
                def update_ui():
                    item['start'] = mic_s
                    item['end'] = mic_e
                    self.spectrogram_panel.var_t_start.set(f"{mic_s:.3f}")
                    self.spectrogram_panel.var_t_end.set(f"{mic_e:.3f}")
                    self.spectrogram_panel.update_lines(mic_s, mic_e)
                    self.tree_panel.update_preview()
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
                    self.tree_panel.update_preview()
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

        entry.bind("<Enter>", on_enter)
        entry.bind("<Leave>", on_leave)
        entry.bind("<FocusIn>", on_focus_in)
        entry.bind("<FocusOut>", on_focus_out)
        entry.bind("<Return>", lambda e: self.root.focus_set())

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
                
            if changed_algo: self.recalculate_all_audio_debounced()
            if new_pts != self.last_params['pts']:
                self.last_params['pts'] = new_pts
                self.tree_panel.update_preview()
        except ValueError: pass

    def on_trim_silence_toggle(self):
        self.recalculate_all_audio_debounced()

    def recalculate_all_audio_debounced(self):
        if self.debounce_timer:
            self.root.after_cancel(self.debounce_timer)
        self.debounce_timer = self.root.after(500, self.recalculate_all_audio)

    def recalculate_all_audio(self):
        if not self.items: return
        self.recalc_id_counter += 1
        current_id = self.recalc_id_counter
        
        items_snapshot = list(self.items.items())
        total = len(items_snapshot)
        results = []

        def process_item(args):
            iid, item = args
            # 如果已经中止，提前返回
            if current_id != self.recalc_id_counter: return None
            
            # Lazy load in thread
            if not item.get('snd') and item.get('path'):
                try:
                    item['snd'] = parselmouth.Sound(item['path'])
                    item['pitch'] = item['snd'].to_pitch()
                except: return None
            
            if item.get('snd'):
                mac_s, mac_e = item['macro_start'], item['macro_end']
                mic_s, mic_e = self._microscopic_vowel_nucleus(item['snd'], item['pitch'], mac_s, mac_e)
                return (iid, mic_s, mic_e)
            return None

        def run():
            self.root.after(0, lambda: self.start_loading("正在重新计算 (首次较慢)..."))
            
            # 使用 ThreadPoolExecutor 以便共享内存缓存 snd 和 pitch
            import concurrent.futures
            completed_count = 0
            with concurrent.futures.ThreadPoolExecutor(max_workers=min(os.cpu_count() or 4, 8)) as executor:
                futures = [executor.submit(process_item, (iid, item)) for iid, item in items_snapshot]
                for future in concurrent.futures.as_completed(futures):
                    if current_id != self.recalc_id_counter:
                        # 放弃剩下的
                        executor.shutdown(wait=False, cancel_futures=True)
                        return
                    res = future.result()
                    if res:
                        results.append(res)
                        
                    completed_count += 1
                    if completed_count % 5 == 0 or completed_count == total:
                        self.root.after(0, lambda v=completed_count / total: self.set_progress(v))

            def finalize():
                if current_id != self.recalc_id_counter:
                    return
                    
                for iid, mic_s, mic_e in results:
                    if iid in self.items:
                        self.items[iid]['start'] = mic_s
                        self.items[iid]['end'] = mic_e
                
                if self.spectrogram_panel.current_item:
                    self.spectrogram_panel.plot_item_spectrogram()
                    self.spectrogram_panel.update_ui_times()
                self.tree_panel.update_preview()
                self.stop_loading("全局参数已应用")

            self.root.after(0, finalize)
        threading.Thread(target=run, daemon=True).start()

    def _microscopic_vowel_nucleus(self, snd, global_pitch, t_min, t_max):
        return core_microscopic_vowel_nucleus(
            snd, global_pitch, t_min, t_max, 
            self.last_params['db'], self.last_params['dur'], 
            self.switch_trim_silence.get()
        )

    # --- 核心调度 ---
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
            for iid, item in self.items.items():
                if item.get('snd') is not None and 'macro_start' in item:
                    existing_items.append({
                        'id': iid,
                        'label': item['label'],
                        'start': item['macro_start'], 
                        'end': item['macro_end']
                    })
            existing_items.sort(key=lambda x: x['start'])
            
        VisualSplitter(self.root, self.pending_long_snd, self.icons, self.on_visual_split_confirm, existing_items)

    def on_visual_split_confirm(self, segments, is_update=False):
        if is_update:
            for seg in segments:
                if 'id' in seg and seg['id'] in self.items:
                    item = self.items[seg['id']]
                    item['macro_start'] = seg['start']
                    item['macro_end'] = seg['end']
                    mic_s, mic_e = self._microscopic_vowel_nucleus(
                        item['snd'], item['pitch'], item['macro_start'], item['macro_end']
                    )
                    item['start'], item['end'] = mic_s, mic_e
            
            self.tree_panel.update_preview()
            if self.spectrogram_panel.current_item:
                self.spectrogram_panel.plot_item_spectrogram() 
                self.spectrogram_panel.update_ui_times()
                
            messagebox.showinfo("提示", "手动微调已应用，时间边界已更新。")
        else:
            self.manual_segments = segments
            messagebox.showinfo("提示", f"全新手动切分完成，共 {len(segments)} 个片段。\n现在请点击“导入字表并切分”来匹配文本。")

    def process_long_with_wordlist(self, raw_text):
        groups, flat_words = parse_wordlist(raw_text)
        if not flat_words: return
        
        def run():
            self.root.after(0, lambda: self.start_loading("正在处理长音频..."))
            self.root.after(0, self.tree_panel.clear_all)
            
            snd = self.pending_long_snd
            global_pitch = snd.to_pitch()
            
            if hasattr(self, 'manual_segments') and self.manual_segments:
                macro_segments = self.manual_segments
            else:
                macro_segments = macroscopic_vad(snd)
            
            total = len(flat_words)
            results = []
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
                    gid = self.tree_panel.ensure_group(res['group'])
                    if not res['missing']:
                        iid = self.tree_panel.tree.insert(gid, tk.END, text=res['word'], tags=('item',))
                        self.items[iid] = {
                            'label': res['word'], 'group': res['group'], 'snd': snd, 'pitch': global_pitch,
                            'macro_start': res['ms'], 'macro_end': res['me'], 
                            'start': res['mis'], 'end': res['mie']
                        }
                    else:
                        iid = self.tree_panel.tree.insert(gid, tk.END, text=res['word'] + " (缺失)", tags=('item',))
                        self.items[iid] = {'label': res['word'], 'group': res['group'], 'snd': None, 'start': 0.0, 'end': 0.0}
                
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
        self.lbl_status.configure(text="独立音频就绪", text_color="#10B981")

    def process_batch_direct(self):
        if not self.pending_batch_paths:
            return messagebox.showwarning("提示", "请先选择多个音频文件")
            
        def run():
            self.root.after(0, lambda: self.start_loading("正在并行批量提取..."))
            self.root.after(0, self.tree_panel.clear_all)
            
            total = len(self.pending_batch_paths)
            params = {'db': self.last_params['db'], 'dur': self.last_params['dur']}
            trim = self.switch_trim_silence.get()
            
            results = []
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
                gid = self.tree_panel.ensure_group("独立文件")
                for _, res in results:
                    if res.get('success'):
                        res['group'] = "独立文件"
                        iid = f"batch_{res['label']}_{id(res)}"
                        self.items[iid] = res
                        self.tree_panel.tree.insert(gid, tk.END, iid=iid, text=res['label'], tags=('item',))
                
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
                    gid = self.tree_panel.ensure_group(res['group'])
                    if not res['missing'] and res.get('success'):
                        res['group'] = tasks[i]['group']
                        display = f"{res['label']} ← {os.path.basename(res['path'])}" if match_mode == 'fuzzy' else res['label']
                        iid = f"batch_wl_{res['label']}_{id(res)}"
                        self.tree_panel.tree.insert(gid, tk.END, iid=iid, text=display, tags=('item',))
                        self.items[iid] = res
                        matched_count += 1
                    else:
                        suffix = " (未匹配)" if match_mode == 'fuzzy' else " (缺失)"
                        iid = f"missing_{res['label']}_{id(res)}"
                        self.tree_panel.tree.insert(gid, tk.END, iid=iid, text=res['label'] + suffix, tags=('item',))
                        self.items[iid] = {'label': res['label'], 'group': res['group'], 'snd': None, 'start': 0.0, 'end': 0.0}
                
                self.stop_loading(f"并行处理完成: {matched_count}/{total}")
                self.tree_panel.select_first_item()
                
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
            
        CTkReleaseButton(dlg, text="开始匹配提取", command=process, corner_radius=20, height=40, font=self.font_main).pack(pady=15)
