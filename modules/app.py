import tkinter as tk
from tkinter import filedialog, messagebox
import customtkinter as ctk
# pyrefly: ignore [missing-import]
import parselmouth
import copy
import os
import threading
import concurrent.futures
import numpy as np
from PIL import Image
import queue

# 导入拆分后的模块
from .ui_widgets import CTkReleaseButton, ToolTip
from .data_utils import parse_wordlist, fuzzy_match_word_to_path, split_into_syllables, has_cjk
from .audio_core import core_microscopic_vowel_nucleus, batch_process_worker, macroscopic_vad, check_audio_segments, long_process_worker, recalculate_bounds_fast, auto_split_inner_word, extract_f0, batch_process_worker_with_textgrid
from .visual_splitter import VisualSplitter
from .spectrogram_panel import SpectrogramPanel
from .project_tree import ProjectTreePanel
from .speaker_manager import SpeakerManager
from .project_manager import ProjectManager
from .version import APP_NAME, __version__
import sys

# Monkey patch CTkScrollableFrame._mouse_wheel_all to increase scroll speed on Windows by a factor of 3.0
orig_mouse_wheel_all = ctk.CTkScrollableFrame._mouse_wheel_all
def patched_mouse_wheel_all(self, event):
    if self.check_if_master_is_canvas(event.widget):
        if sys.platform.startswith("win"):
            if self._shift_pressed:
                if self._parent_canvas.xview() != (0.0, 1.0):
                    self._parent_canvas.xview("scroll", -int(event.delta / 2), "units")
            else:
                if self._parent_canvas.yview() != (0.0, 1.0):
                    self._parent_canvas.yview("scroll", -int(event.delta / 2), "units")
        else:
            orig_mouse_wheel_all(self, event)
    else:
        orig_mouse_wheel_all(self, event)
ctk.CTkScrollableFrame._mouse_wheel_all = patched_mouse_wheel_all


class PhoneticsApp:
    def __init__(self, root, initial_files=None):
        self.root = root
        self.root.title(f"{APP_NAME} v{__version__} - 声调提取与分析工具")
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

        self.drop_queue = queue.Queue()
        self._drop_queue_idle_delay = 500
        self._drop_queue_active_delay = 50
        self._drop_queue_job = None
        self._window_guard_job = None
        self._schedule_drop_queue_check(self._drop_queue_idle_delay)
        self.debounce_timer = None
        self.speaker_manager = SpeakerManager()
        self.project_manager = ProjectManager(self)
        self.export_numbering_rule_var = ctk.StringVar(value="continuous")
        self.has_changes = False
        self.current_project_path = None
        self.active_chart_dialog = None

        # Shared ProcessPoolExecutor for performance optimization
        max_workers = min(os.cpu_count() or 4, 8)
        self.executor = concurrent.futures.ProcessPoolExecutor(max_workers=max_workers)

        def on_closing():
            if getattr(self, 'has_changes', False):
                ans = messagebox.askyesnocancel("保存项目", "项目已被修改，是否在关闭前保存？")
                if ans is True: # Yes
                    if getattr(self, 'current_project_path', None):
                        success = self.project_manager.export_project(self.current_project_path)
                        if not success:
                            return
                    else:
                        import datetime
                        spk_name = self.active_speaker.name if getattr(self, 'active_speaker', None) else "发音人1"
                        date_str = datetime.datetime.now().strftime("%m%d-%H%M")
                        default_filename = f"{spk_name}_{date_str}"
                        path = filedialog.asksaveasfilename(
                            initialfile=default_filename,
                            defaultextension=".teproj",
                            filetypes=[("PhonTracer Project", "*.teproj")]
                        )
                        if not path:
                            return
                        success = self.project_manager.export_project(path)
                        if not success:
                            return
                        self.current_project_path = path
                elif ans is None: # Cancel
                    return

            if self._drop_queue_job is not None:
                try:
                    self.root.after_cancel(self._drop_queue_job)
                except Exception:
                    pass
                self._drop_queue_job = None
            if self._window_guard_job is not None:
                try:
                    self.root.after_cancel(self._window_guard_job)
                except Exception:
                    pass
                self._window_guard_job = None

            # 主动关闭并销毁 ProcessPoolExecutor 的子进程，避免程序关闭后滞留在后台
            import multiprocessing
            try:
                for child in multiprocessing.active_children():
                    child.terminate()
            except Exception:
                pass

            self.executor.shutdown(wait=False)
            self.root.destroy()
        self.root.protocol("WM_DELETE_WINDOW", on_closing)

        try:
            self.font_title = ctk.CTkFont(family="Microsoft YaHei", size=15, weight="bold")
            self.font_main = ctk.CTkFont(family="Microsoft YaHei", size=13)
            self.font_code = ctk.CTkFont(family="Consolas", size=13)
        except Exception:
            self.font_title = ("Microsoft YaHei", 15, "bold")
            self.font_main = ("Microsoft YaHei", 13)
            self.font_code = ("Consolas", 13)

        self.setup_icons()
        self.setup_ui()
        self.root.bind("<Map>", lambda _e: self._recover_window_from_stuck_grab())
        self._schedule_window_guard()

        # 绑定拖拽事件
        try:
            import windnd
            windnd.hook_dropfiles(self.root, func=self.on_files_dropped)
        except Exception:
            pass

        # 处理初始传入的文件（例如“打开方式”或拖动到图标）
        if initial_files:
            self.root.after(1500, lambda: self.on_files_dropped(initial_files))

        # 启动时后台静默检查更新 (已取消自动获取最新版本机制，改为手动检测更新)
        # self.root.after(3000, lambda: self.check_update(is_manual=False))

    def mark_modified(self):
        self.has_changes = True
        if hasattr(self, 'project_manager'):
            self.project_manager.trigger_auto_save()

    @property
    def active_speaker(self): return self.speaker_manager.get_active_speaker()
    @property
    def items(self): return self.active_speaker.items
    @items.setter
    def items(self, v): self.active_speaker.items = v
    @property
    def audio_cache(self): return self.active_speaker.audio_cache
    @audio_cache.setter
    def audio_cache(self, v): self.active_speaker.audio_cache = v
    @property
    def last_params(self): return self.active_speaker.last_params
    @last_params.setter
    def last_params(self, v): self.active_speaker.last_params = v
    @property
    def pending_long_snd(self): return self.active_speaker.pending_long_snd
    @pending_long_snd.setter
    def pending_long_snd(self, v): self.active_speaker.pending_long_snd = v
    @property
    def long_audio_path(self): return getattr(self.active_speaker, 'long_audio_path', None)
    @long_audio_path.setter
    def long_audio_path(self, v): self.active_speaker.long_audio_path = v
    @property
    def pending_batch_paths(self): return self.active_speaker.pending_batch_paths
    @pending_batch_paths.setter
    def pending_batch_paths(self, v): self.active_speaker.pending_batch_paths = v
    @property
    def current_macro_segments(self): return self.active_speaker.current_macro_segments
    @current_macro_segments.setter
    def current_macro_segments(self, v): self.active_speaker.current_macro_segments = v
    @property
    def manual_segments(self): return getattr(self.active_speaker, 'manual_segments', None)
    @manual_segments.setter
    def manual_segments(self, v): self.active_speaker.manual_segments = v

    def _schedule_drop_queue_check(self, delay, replace=False):
        if replace and self._drop_queue_job is not None:
            try:
                self.root.after_cancel(self._drop_queue_job)
            except Exception:
                pass
            self._drop_queue_job = None
        if self._drop_queue_job is None:
            self._drop_queue_job = self.root.after(delay, self._check_drop_queue)

    def _check_drop_queue(self):
        self._drop_queue_job = None
        processed = 0
        try:
            # 安全地将拖入的文件拿到主线程标准事件流中
            while True:
                item = self.drop_queue.get_nowait()
                processed += 1
                if isinstance(item, tuple) and len(item) == 2 and item[0] == 'dlg':
                    self._process_dlg_dropped_files(item[1])
                else:
                    self._process_dropped_files(item)
        except queue.Empty:
            pass
        delay = self._drop_queue_active_delay if processed else self._drop_queue_idle_delay
        self._schedule_drop_queue_check(delay)

    def _schedule_window_guard(self):
        if self._window_guard_job is None:
            self._window_guard_job = self.root.after(1200, self._window_guard_tick)

    def _window_guard_tick(self):
        self._window_guard_job = None
        self._recover_window_from_stuck_grab()
        self._schedule_window_guard()

    def _recover_window_from_stuck_grab(self):
        try:
            grabbed = self.root.grab_current()
        except Exception:
            grabbed = None
        if grabbed is None:
            return

        try:
            grabbed_ok = grabbed.winfo_exists() and grabbed.winfo_viewable()
        except Exception:
            grabbed_ok = False

        if grabbed_ok:
            return

        try:
            self.root.grab_release()
        except Exception:
            pass
        try:
            self.root.deiconify()
            self.root.lift()
            self.root.focus_force()
        except Exception:
            pass

    def _has_active_chart_dialog(self):
        dlg = getattr(self, 'active_chart_dialog', None)
        if dlg is None:
            return False
        try:
            if dlg.winfo_exists():
                return True
        except Exception:
            pass
        self.active_chart_dialog = None
        return False

    def _process_dlg_dropped_files(self, files):
        if not getattr(self, 'active_import_dlg', None) or not self.active_import_dlg.winfo_exists():
            return

        decoded_paths = []
        for f in files:
            if isinstance(f, bytes):
                try: decoded_paths.append(f.decode('gbk'))
                except UnicodeDecodeError: decoded_paths.append(f.decode('utf-8'))
            else:
                decoded_paths.append(str(f))

        txt_files = [p for p in decoded_paths if p.lower().endswith(('.txt', '.csv'))]
        tg_files = [p for p in decoded_paths if p.lower().endswith('.textgrid')]

        if tg_files:
            if self.active_import_mode == 'batch':
                dlg = self.active_import_dlg
                dlg.destroy()
                self.process_batch_with_textgrid(tg_files)
                return
            path = tg_files[0]
            if self.active_import_mode != 'long':
                messagebox.showwarning("提示", "目前仅在“单条长音频”模式下支持导入 TextGrid 词表。", parent=self.active_import_dlg)
                return
            try:
                import textgrid
                tg = textgrid.TextGrid.fromFile(path)
                words_tier = None
                groups_tier = None
                chars_tier = None
                for t in tg.tiers:
                    if t.name == "words" and words_tier is None: words_tier = t
                    elif t.name == "chars" and chars_tier is None: chars_tier = t
                    elif t.name in ["groups", "group"] and groups_tier is None: groups_tier = t
                if not words_tier:
                    for t in tg.tiers:
                        if isinstance(t, textgrid.IntervalTier):
                            words_tier = t
                            break
                if not words_tier:
                    messagebox.showerror("错误", "TextGrid 中没有找到 IntervalTier", parent=self.active_import_dlg)
                    return

                tg_intervals = []
                import numpy as np
                for interval in words_tier:
                    lbl = interval.mark.strip()
                    if lbl:
                        grp_name = "导入内容"
                        if groups_tier:
                            center = (interval.minTime + interval.maxTime) / 2.0
                            for g_interval in groups_tier:
                                if g_interval.minTime <= center <= g_interval.maxTime:
                                    g_lbl = g_interval.mark.strip()
                                    if g_lbl:
                                        grp_name = g_lbl
                                        break
                        chars_bounds = []
                        inner_splits = []
                        if chars_tier:
                            overlapping_chars = []
                            for c_interval in chars_tier:
                                c_lbl = c_interval.mark.strip()
                                if c_lbl:
                                    center = (c_interval.minTime + c_interval.maxTime) / 2.0
                                    if interval.minTime <= center <= interval.maxTime:
                                        overlapping_chars.append(c_interval)
                            overlapping_chars.sort(key=lambda c: c.minTime)
                            if overlapping_chars:
                                for c in overlapping_chars:
                                    chars_bounds.append([c.minTime, c.maxTime])
                                for j in range(len(overlapping_chars) - 1):
                                    inner_splits.append(overlapping_chars[j].maxTime)
                        if not chars_bounds:
                            syls = split_into_syllables(lbl)
                            w_len = len(syls)
                            if w_len > 1:
                                splits = np.linspace(interval.minTime, interval.maxTime, w_len + 1).tolist()
                                chars_bounds = [[splits[j], splits[j+1]] for j in range(w_len)]
                                inner_splits = splits[1:-1]
                            else:
                                chars_bounds = [[interval.minTime, interval.maxTime]]
                                inner_splits = []
                        tg_intervals.append({
                            'start': interval.minTime,
                            'end': interval.maxTime,
                            'label': lbl,
                            'group': grp_name,
                            'inner_splits': inner_splits,
                            'chars_bounds': chars_bounds
                        })
                if not tg_intervals:
                    messagebox.showerror("错误", "TextGrid 中没有非空标签的区间", parent=self.active_import_dlg)
                    return

                dlg = self.active_import_dlg
                dlg.destroy()
                self.process_long_with_textgrid(tg_intervals)
            except Exception as e:
                messagebox.showerror("错误", f"解析 TextGrid 失败: {e}", parent=self.active_import_dlg)

        elif txt_files:
            path = txt_files[0]
            try:
                try:
                    with open(path, 'r', encoding='utf-8') as f: text = f.read()
                except UnicodeDecodeError:
                    with open(path, 'r', encoding='gbk') as f: text = f.read()

                self.active_import_textbox.delete("1.0", tk.END)
                self.active_import_textbox.insert("1.0", text)
                self.active_import_update_stats()
            except Exception as e:
                messagebox.showerror("错误", f"读取文件失败: {e}", parent=self.active_import_dlg)
        else:
            messagebox.showwarning("提示", "拖入的文件类型不支持，请拖入 .txt 或 .TextGrid 文件。", parent=self.active_import_dlg)

    def on_files_dropped(self, files):
        # 仅将文件压入队列，彻底不涉及 Tkinter UI 的 Tcl 调用
        self.drop_queue.put(files)

    def _process_dropped_files(self, files):
        if self._has_active_chart_dialog():
            messagebox.showwarning("提示", "图表编辑器已打开，修改图表期间禁止通过拖入文件进行导入/更改操作。")
            return
        decoded_paths = []
        for f in files:
            if isinstance(f, bytes):
                try: decoded_paths.append(f.decode('gbk'))
                except UnicodeDecodeError: decoded_paths.append(f.decode('utf-8'))
            else:
                decoded_paths.append(str(f))

        # Check for project files (.teproj, .zip)
        teproj_files = [p for p in decoded_paths if p.lower().endswith(('.teproj', '.zip'))]
        if teproj_files:
            path = teproj_files[0]
            
            overlay = False
            if not self.is_project_empty():
                ans = messagebox.askyesnocancel(
                    "导入项目",
                    "当前已打开一个项目，是否以【叠加】方式导入新项目？\n\n"
                    "- 点击【是】：叠加导入，将新项目的数据合并到当前项目中\n"
                    "- 点击【否】：覆盖导入，清除当前项目并完全载入新项目\n"
                    "- 点击【取消】：取消本次导入"
                )
                if ans is None:
                    return
                overlay = ans

            self._last_imported_path = path
            self._last_import_was_overlay = overlay
            self.start_loading("正在导入工程...")
            def run():
                success = self.project_manager.load_project(path, overlay=overlay)
                self.root.after(0, self.stop_loading)
                if success:
                    self.root.after(0, self._sync_ui_after_project_load)
            import threading
            threading.Thread(target=run, daemon=True).start()
            return

        # Check for wordlist files (.txt, .csv, .textgrid)
        wordlist_files = [p for p in decoded_paths if p.lower().endswith(('.txt', '.csv', '.textgrid'))]
        if wordlist_files:
            self.mark_modified()
            current_tab = self.tabview.get()
            if current_tab == "单条长音频":
                mode = 'long'
                has_audio = self.pending_long_snd is not None
                err_msg = "请先导入长音频后，再拖入字表文件！"
            else:
                mode = 'batch'
                has_audio = bool(self.pending_batch_paths)
                err_msg = "请先选择独立音频后，再拖入字表文件！"

            if not has_audio:
                messagebox.showwarning("提示", err_msg)
                return

            if getattr(self, 'active_import_dlg', None) and self.active_import_dlg.winfo_exists():
                self._process_dlg_dropped_files(files)
            else:
                self.open_text_dialog(mode)
                self._process_dlg_dropped_files(files)
            return

        audio_paths = [p for p in decoded_paths if p.lower().endswith(('.wav', '.mp3'))]
        if not audio_paths:
            messagebox.showwarning("提示", "拖入的文件中没有支持的音频文件 (.wav, .mp3)、工程文件 (.teproj) 或字表文件 (.txt, .csv)")
            return

        self.mark_modified()
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
                        audio_name = os.path.splitext(os.path.basename(path))[0]
                        if seg_count > 1 and self.active_speaker.name.startswith("发音人"):
                            self.speaker_manager.rename_speaker(self.speaker_manager.active_speaker_id, audio_name)
                            self._update_speaker_dropdown()
                            self.speaker_option_var.set(audio_name)

                        if seg_count <= 1:
                            self.tabview.set("多条独立音频")
                            self.pending_batch_paths = [path]
                            self.lbl_batch_files.configure(text=f"已选 1 个文件 (从拖拽)", text_color="#2563EB")
                            self.lbl_status.configure(text="独立音频就绪", text_color="#10B981")
                            if getattr(self, 'switch_unified_wordlist', None) and self.switch_unified_wordlist.get() and getattr(self, 'global_wordlist_text', None):
                                self.process_batch_with_wordlist(self.global_wordlist_text, match_mode=getattr(self, 'global_wordlist_match_mode', 'fuzzy'))
                        else:
                            self.tabview.set("单条长音频")
                            self.pending_long_snd = snd
                            self.long_audio_path = path
                            self.lbl_long_file.configure(text=audio_name + " (从拖拽)", text_color="#2563EB")
                            self.lbl_status.configure(text="长音频就绪", text_color="#10B981")
                            if getattr(self, 'switch_unified_wordlist', None) and self.switch_unified_wordlist.get() and getattr(self, 'global_wordlist_text', None):
                                self.process_long_with_wordlist(self.global_wordlist_text)
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
            "play": "play.png", "save": "save.png", "save_black": "save.png", "check": "check.png",
            "bulb": "bulb.png", "points": "points.png", "energy": "energy.png",
            "duration": "duration.png", "trim": "trim.png", "tag": "tag.png",
            "tab_single": "tab_single.png", "tab_batch": "tab_batch.png",
            "status_success": "status_success.png",
            "status_loading": "status_loading.png",
            "status_error": "status_error.png",
            "warning": "warning.png",
            "import": "import_file.png", "ai_prompt": "ai_prompt.png", "copy": "copy_icon.png",
            "import_white": "import_white.png", "copy_white": "copy_white.png", "check_white": "check_white.png",
            "pause": "pause.png", "eraser": "eraser.png",
            "blue_dot": "blue_dot.png",
            "folder_close": "folder_close.png",
            "folder_open": "folder_open.png",
            "audio_wave": "audio_wave.png",
            "filter_all_black": "filter_all_black.png",
            "filter_all_white": "filter_all_white.png",
            "filter_warning_black": "filter_warning_black.png",
            "filter_warning_white": "filter_warning_white.png",
            "filter_check_black": "filter_check_black.png",
            "filter_check_white": "filter_check_white.png"
        }
        from PIL import ImageTk
        self.tk_icons = {}
        for key, filename in icon_files.items():
            path = os.path.join(icon_path, filename)
            if os.path.exists(path):
                img = Image.open(path)

                # 将 自动识别 (bulb) 的黑色图标染色为对应红色（#DC2626），与删除按钮风格高度统一
                if key in ["bulb", "save_black"]:
                    try:
                        img_rgba = img.convert("RGBA")
                        data = np.array(img_rgba)
                        if key == "bulb":
                            data[:,:,0] = 220 # R
                            data[:,:,1] = 38  # G
                            data[:,:,2] = 38  # B
                        elif key == "save_black":
                            mask = data[:,:,3] > 0
                            data[mask, 0] = 31
                            data[mask, 1] = 41
                            data[mask, 2] = 55
                        img = Image.fromarray(data)
                    except Exception:
                        pass

                if key == "blue_dot":
                    try:
                        # Resize the blue dot to 8x8
                        img_small = img.resize((8, 8), Image.Resampling.LANCZOS)
                        # Create a 16x16 transparent image
                        new_img = Image.new("RGBA", (16, 16), (0, 0, 0, 0))
                        # Paste the small blue dot in the center (4, 4)
                        new_img.paste(img_small, (4, 4))
                        img_tk = new_img
                    except Exception:
                        img_tk = img.resize((16, 16), Image.Resampling.LANCZOS)
                else:
                    img_tk = img.resize((16, 16), Image.Resampling.LANCZOS)

                if "filter_" in key:
                    self.icons[key] = ctk.CTkImage(light_image=img, dark_image=img, size=(16, 16))
                else:
                    self.icons[key] = ctk.CTkImage(light_image=img, dark_image=img, size=(20, 20))
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

        btn_kwargs_primary = {"corner_radius": 20, "height": 38, "font": self.font_main,
                              "fg_color": "#3B82F6", "hover_color": "#2563EB", "text_color": "white"}
        btn_kwargs_secondary = {"corner_radius": 20, "height": 38, "font": self.font_main,
                                "fg_color": "#E5E7EB", "text_color": "#1F2937", "hover_color": "#D1D5DB"}

        if self.icons.get("brand_logo"):
            self.logo_lbl = ctk.CTkLabel(header_frame, text="", image=self.icons.get("brand_logo"), cursor="hand2")
            self.logo_lbl.pack(side=tk.LEFT, padx=(10, 15))
        else:
            self.logo_lbl = ctk.CTkLabel(header_frame, text="PhonTracer", font=ctk.CTkFont(family="Segoe UI", size=26, weight="bold"), text_color="#1F2937", cursor="hand2")
            self.logo_lbl.pack(side=tk.LEFT, padx=(10, 15))

        # 绑定点击事件与悬停提示
        self.logo_lbl.bind("<Button-1>", lambda e: self.open_about_dialog())
        ToolTip(self.logo_lbl, "关于 PhonTracer")

        status_container = ctk.CTkFrame(header_frame, fg_color="transparent")
        status_container.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.lbl_status = ctk.CTkLabel(status_container, text=" 就绪", image=self.icons.get("status_success"), compound="left", text_color="#10B981", font=ctk.CTkFont(family="Microsoft YaHei", size=12), wraplength=120)
        self.lbl_status.pack(pady=(5, 5), expand=True)

        self.progress_bar = ctk.CTkProgressBar(status_container, height=6, corner_radius=10,
                                               progress_color="#3B82F6", fg_color="#E5E7EB")
        self.progress_bar.set(0)


        self.speaker_frame = ctk.CTkFrame(left_scrollable, fg_color="white", corner_radius=10)
        self.speaker_frame.pack(fill=tk.X, pady=(0, 10))
        speaker_header = ctk.CTkFrame(self.speaker_frame, fg_color="transparent")
        speaker_header.pack(fill=tk.X, padx=15, pady=(10, 5))
        ctk.CTkLabel(speaker_header, text="发音人列表", font=self.font_title, text_color="#111827").pack(side=tk.LEFT)
        CTkReleaseButton(speaker_header, text="", image=self.icons.get("plus"), width=24, height=24, command=self.on_add_speaker, fg_color="transparent", hover_color="#E5E7EB").pack(side=tk.RIGHT)
        self.speaker_option_var = ctk.StringVar(value=self.active_speaker.name)
        self.speaker_dropdown = ctk.CTkOptionMenu(
            self.speaker_frame,
            variable=self.speaker_option_var,
            values=[s.name for s in self.speaker_manager.get_all_speakers()],
            command=self.on_speaker_changed,
            font=self.font_main,
            fg_color="#F3F4F6",
            text_color="#1F2937",
            button_color="#F3F4F6",             # 统一背景色，消除右侧色块，使其浑然一体
            button_hover_color="#E5E7EB",
            height=32,                          # 增加高度以优化 Canvas 箭头高 DPI 渲染
            corner_radius=16                    # 药丸型圆角
        )
        self.speaker_dropdown.pack(fill=tk.X, padx=15, pady=(0, 10))
        # 修复下拉菜单箭头在 Windows 高分屏上渲染出白色噪点，同时避免重绘背景时出现白色缝隙
        try:
            orig_draw_arrow = self.speaker_dropdown._draw_engine.draw_dropdown_arrow
            def custom_draw_arrow(*args, **kwargs):
                old_method = self.speaker_dropdown._draw_engine.preferred_drawing_method
                self.speaker_dropdown._draw_engine.preferred_drawing_method = "polygon_shapes"
                res = orig_draw_arrow(*args, **kwargs)
                self.speaker_dropdown._draw_engine.preferred_drawing_method = old_method
                try:
                    self.speaker_dropdown._canvas.itemconfigure("dropdown_arrow", width=2)
                except Exception:
                    pass
                return res
            self.speaker_dropdown._draw_engine.draw_dropdown_arrow = custom_draw_arrow
            self.speaker_dropdown._canvas.delete("dropdown_arrow")
            # 依靠 pack 后的 <Configure> 事件自动触发正常的 _draw，无需手动重绘
        except Exception:
            pass
        speaker_actions = ctk.CTkFrame(self.speaker_frame, fg_color="transparent")
        speaker_actions.pack(fill=tk.X, padx=15, pady=(0, 10))
        CTkReleaseButton(
            speaker_actions,
            text="重命名",
            command=self.on_rename_speaker,
            height=32,
            corner_radius=16,
            font=self.font_main,
            fg_color="#F3F4F6",
            text_color="#4B5563",
            hover_color="#E5E7EB"
        ).pack(side=tk.LEFT, expand=True, padx=(0, 5), fill=tk.X)
        CTkReleaseButton(
            speaker_actions,
            text="删除",
            command=self.on_delete_speaker,
            height=32,
            corner_radius=16,
            font=self.font_main,
            fg_color="#FEE2E2",
            text_color="#DC2626",
            hover_color="#FCA5A5"
        ).pack(side=tk.LEFT, expand=True, padx=(5, 0), fill=tk.X)

        switch_row = ctk.CTkFrame(self.speaker_frame, fg_color="transparent")
        switch_row.pack(fill=tk.X, padx=15, pady=(0, 10))
        self.switch_unified_wordlist = ctk.CTkSwitch(switch_row, text="统一字表", font=self.font_main, width=60)
        self.switch_unified_wordlist.pack(side=tk.LEFT)
        self.switch_unified_wordlist.select()


        self.tabview = ctk.CTkTabview(left_scrollable, height=170, corner_radius=12, fg_color="white",
                                      segmented_button_selected_color="#3B82F6", segmented_button_fg_color="#F3F4F6")
        self.tabview.pack(fill=tk.X, pady=(0, 10))
        self.tabview._segmented_button.configure(corner_radius=20)

        # 分析模式 (药丸型按钮)
        self.mode_frame = ctk.CTkFrame(left_scrollable, fg_color="white", corner_radius=10)
        self.mode_frame.pack(fill=tk.X, pady=(0, 10))

        lbl_mode_title = ctk.CTkLabel(self.mode_frame, text="分析模式", font=self.font_title, text_color="#111827")
        lbl_mode_title.pack(side=tk.LEFT, padx=15, pady=10)

        self.mode_button = ctk.CTkSegmentedButton(
            self.mode_frame,
            values=["声调/F0", "共振峰/F1-F2"],
            command=self.on_analysis_mode_change,
            selected_color="#3B82F6",
            selected_hover_color="#2563EB",
            fg_color="#F3F4F6",
            unselected_color="#F3F4F6",
            unselected_hover_color="#E5E7EB",
            text_color="#1F2937",
            corner_radius=20,  # Pill shaped!
            height=32
        )
        self.mode_button.pack(side=tk.RIGHT, padx=15, pady=10)
        initial_mode = "声调/F0" if self.last_params.get('analysis_mode', 'f0') == 'f0' else "共振峰/F1-F2"
        self.mode_button.set(initial_mode)
        self._update_mode_button_text_colors()
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
        card_params.pack(fill=tk.X, pady=(0, 10))

        # 头部折叠栏（支持手势点击与手型悬停）
        header_frame = ctk.CTkFrame(card_params, fg_color="transparent", cursor="hand2")
        header_frame.pack(fill=tk.X, padx=15, pady=(12, 12))

        self.lbl_card_title = ctk.CTkLabel(header_frame, text="全局算法与导出参数", font=self.font_title, text_color="#111827", cursor="hand2")
        self.lbl_card_title.pack(side=tk.LEFT)

        self.lbl_card_toggle = ctk.CTkLabel(header_frame, text="▶", font=self.font_title, text_color="#6B7280", cursor="hand2")
        self.lbl_card_toggle.pack(side=tk.RIGHT)

        # 折叠容器
        self.params_content_frame = ctk.CTkFrame(card_params, fg_color="transparent")
        # 默认折叠，不进行pack

        self.params_expanded = False
        def toggle_params(event=None):
            if self.params_expanded:
                self.params_content_frame.pack_forget()
                self.lbl_card_toggle.configure(text="▶")
                header_frame.pack(fill=tk.X, padx=15, pady=(12, 12))
                self.params_expanded = False
            else:
                self.params_content_frame.pack(fill=tk.X)
                self.lbl_card_toggle.configure(text="▼")
                header_frame.pack(fill=tk.X, padx=15, pady=(12, 10))
                self.params_expanded = True

        header_frame.bind("<Button-1>", toggle_params)
        self.lbl_card_title.bind("<Button-1>", toggle_params)
        self.lbl_card_toggle.bind("<Button-1>", toggle_params)

        row_pts = ctk.CTkFrame(self.params_content_frame, fg_color="transparent")
        row_pts.pack(fill=tk.X, padx=15, pady=5)
        lbl_pts = ctk.CTkLabel(row_pts, text=" 等分点 (N):", image=self.icons.get("points"), compound="left", text_color="#374151", font=self.font_main)
        lbl_pts.pack(side=tk.LEFT)
        self.slider_pts = ctk.CTkSlider(row_pts, from_=5, to=20, number_of_steps=15, width=100, height=16,
                                        button_color="#3B82F6", button_hover_color="#2563EB", progress_color="#3B82F6",
                                        command=lambda v: self._on_slider_change(v, self.entry_points, 'pts'))
        self.slider_pts.set(self.last_params['pts'])
        self.slider_pts.pack(side=tk.LEFT, padx=10)
        self.entry_points = ctk.CTkEntry(row_pts, width=60, justify="center", corner_radius=20, height=26)
        self.entry_points.insert(0, str(self.last_params['pts']))
        self.entry_points.pack(side=tk.RIGHT)
        self.setup_entry_behavior(self.entry_points, 'pts')

        row_db = ctk.CTkFrame(self.params_content_frame, fg_color="transparent")
        row_db.pack(fill=tk.X, padx=15, pady=5)
        lbl_db = ctk.CTkLabel(row_db, text=" 能量落差:", image=self.icons.get("energy"), compound="left", text_color="#374151", font=self.font_main)
        lbl_db.pack(side=tk.LEFT)
        self.slider_db = ctk.CTkSlider(row_db, from_=10, to=100, number_of_steps=90, width=100, height=16,
                                       button_color="#3B82F6", button_hover_color="#2563EB", progress_color="#3B82F6",
                                       command=lambda v: self._on_slider_change(v, self.entry_drop_db, 'db'))
        self.slider_db.set(self.last_params['db'])
        self.slider_db.pack(side=tk.LEFT, padx=10)
        self.var_drop_db = ctk.StringVar(value=str(self.last_params['db']))
        self.entry_drop_db = ctk.CTkEntry(row_db, textvariable=self.var_drop_db, width=60, justify="center", corner_radius=20, height=26)
        self.entry_drop_db.pack(side=tk.RIGHT)
        self.setup_entry_behavior(self.entry_drop_db, 'db')

        row_dur = ctk.CTkFrame(self.params_content_frame, fg_color="transparent")
        row_dur.pack(fill=tk.X, padx=15, pady=5)
        lbl_dur = ctk.CTkLabel(row_dur, text=" 排除声母:", image=self.icons.get("duration"), compound="left", text_color="#374151", font=self.font_main)
        lbl_dur.pack(side=tk.LEFT)
        self.slider_dur = ctk.CTkSlider(row_dur, from_=0.00, to=0.15, number_of_steps=15, width=100, height=16,
                                        button_color="#3B82F6", button_hover_color="#2563EB", progress_color="#3B82F6",
                                        command=lambda v: self._on_slider_change(v, self.entry_min_dur, 'skip_front'))
        self.slider_dur.set(self.last_params['skip_front'])
        self.slider_dur.pack(side=tk.LEFT, padx=10)
        self.var_min_dur = ctk.StringVar(value=f"{self.last_params['skip_front']:.2f}")
        self.entry_min_dur = ctk.CTkEntry(row_dur, textvariable=self.var_min_dur, width=60, justify="center", corner_radius=20, height=26)
        self.entry_min_dur.pack(side=tk.RIGHT)
        self.setup_entry_behavior(self.entry_min_dur, 'skip_front')

        # F0 专属参数容器
        self.f0_params_container = ctk.CTkFrame(self.params_content_frame, fg_color="transparent")
        self.f0_params_container.pack(fill=tk.X)

        # Pitch 范围参数
        row_pitch = ctk.CTkFrame(self.f0_params_container, fg_color="transparent")
        row_pitch.pack(fill=tk.X, padx=15, pady=5)
        ctk.CTkLabel(row_pitch, text=" F0 范围 (Hz):", image=self.icons.get("points"), compound="left", text_color="#374151", font=self.font_main).pack(side=tk.LEFT)
        self.btn_detect_f0 = CTkReleaseButton(
            row_pitch,
            text="检测",
            command=self.on_detect_f0_clicked,
            width=50,
            height=22,
            font=self.font_main,
            corner_radius=11,
            fg_color="#F3F4F6",
            text_color="#2563EB",
            hover_color="#E5E7EB"
        )
        self.btn_detect_f0.pack(side=tk.LEFT, padx=(6, 8))
        self.entry_pitch_ceiling = ctk.CTkEntry(row_pitch, width=50, justify="center", corner_radius=20, height=26)
        self.entry_pitch_ceiling.insert(0, str(self.last_params['pitch_ceiling']))
        self.entry_pitch_ceiling.pack(side=tk.RIGHT)
        self.setup_entry_behavior(self.entry_pitch_ceiling, 'pitch_ceiling')
        ctk.CTkLabel(row_pitch, text="~", text_color="#6B7280").pack(side=tk.RIGHT, padx=2)
        self.entry_pitch_floor = ctk.CTkEntry(row_pitch, width=50, justify="center", corner_radius=20, height=26)
        self.entry_pitch_floor.insert(0, str(self.last_params['pitch_floor']))
        self.entry_pitch_floor.pack(side=tk.RIGHT)
        self.setup_entry_behavior(self.entry_pitch_floor, 'pitch_floor')

        # 浊音阈值参数
        row_voicing = ctk.CTkFrame(self.f0_params_container, fg_color="transparent")
        row_voicing.pack(fill=tk.X, padx=15, pady=5)
        ctk.CTkLabel(row_voicing, text=" 浊音阈值:", image=self.icons.get("points"), compound="left", text_color="#374151", font=self.font_main).pack(side=tk.LEFT)
        self.entry_voicing_threshold = ctk.CTkEntry(row_voicing, width=55, justify="center", corner_radius=20, height=26)
        self.entry_voicing_threshold.insert(0, f"{self.last_params['voicing_threshold']:.2f}")
        self.entry_voicing_threshold.pack(side=tk.RIGHT)
        self.setup_entry_behavior(self.entry_voicing_threshold, 'voicing_threshold')

        # 共振峰专属参数容器
        self.formant_params_container = ctk.CTkFrame(self.params_content_frame, fg_color="transparent")

        # 共振峰参数 row 1: Formant Count (5)
        row_formant_count = ctk.CTkFrame(self.formant_params_container, fg_color="transparent")
        row_formant_count.pack(fill=tk.X, padx=15, pady=4)
        ctk.CTkLabel(row_formant_count, text=" 数量 (N):", image=self.icons.get("points"), compound="left", text_color="#374151", font=self.font_main).pack(side=tk.LEFT)
        self.entry_formant_count = ctk.CTkEntry(row_formant_count, width=65, justify="center", corner_radius=20, height=26)
        self.entry_formant_count.insert(0, str(self.last_params.get('formant_count', 5)))
        self.entry_formant_count.pack(side=tk.RIGHT)
        self.setup_entry_behavior(self.entry_formant_count, 'formant_count')

        # 共振峰参数 row 2: Max Formant Hz (5500)
        row_formant_max = ctk.CTkFrame(self.formant_params_container, fg_color="transparent")
        row_formant_max.pack(fill=tk.X, padx=15, pady=4)
        ctk.CTkLabel(row_formant_max, text=" 最大频率 (Hz):", image=self.icons.get("points"), compound="left", text_color="#374151", font=self.font_main).pack(side=tk.LEFT)
        self.btn_detect_formant = CTkReleaseButton(
            row_formant_max,
            text="检测",
            command=self.on_detect_formant_clicked,
            width=50,
            height=22,
            font=self.font_main,
            corner_radius=11,
            fg_color="#F3F4F6",
            text_color="#2563EB",
            hover_color="#E5E7EB"
        )
        self.btn_detect_formant.pack(side=tk.LEFT, padx=(6, 8))
        self.entry_formant_max_hz = ctk.CTkEntry(row_formant_max, width=65, justify="center", corner_radius=20, height=26)
        self.entry_formant_max_hz.insert(0, str(self.last_params.get('formant_max_hz', 5500.0)))
        self.entry_formant_max_hz.pack(side=tk.RIGHT)
        self.setup_entry_behavior(self.entry_formant_max_hz, 'formant_max_hz')

        # 共振峰参数 row 3: Window Length (0.025)
        row_formant_win = ctk.CTkFrame(self.formant_params_container, fg_color="transparent")
        row_formant_win.pack(fill=tk.X, padx=15, pady=4)
        ctk.CTkLabel(row_formant_win, text=" 窗长 (s):", image=self.icons.get("duration"), compound="left", text_color="#374151", font=self.font_main).pack(side=tk.LEFT)
        self.entry_formant_window_length = ctk.CTkEntry(row_formant_win, width=65, justify="center", corner_radius=20, height=26)
        self.entry_formant_window_length.insert(0, str(self.last_params.get('formant_window_length', 0.025)))
        self.entry_formant_window_length.pack(side=tk.RIGHT)
        self.setup_entry_behavior(self.entry_formant_window_length, 'formant_window_length')

        # 共振峰参数 row 4: Pre-emphasis (50)
        row_formant_pre = ctk.CTkFrame(self.formant_params_container, fg_color="transparent")
        row_formant_pre.pack(fill=tk.X, padx=15, pady=4)
        ctk.CTkLabel(row_formant_pre, text=" 预加重 (Hz):", image=self.icons.get("duration"), compound="left", text_color="#374151", font=self.font_main).pack(side=tk.LEFT)
        self.entry_formant_pre_emphasis = ctk.CTkEntry(row_formant_pre, width=65, justify="center", corner_radius=20, height=26)
        self.entry_formant_pre_emphasis.insert(0, str(self.last_params.get('formant_pre_emphasis', 50.0)))
        self.entry_formant_pre_emphasis.pack(side=tk.RIGHT)
        self.setup_entry_behavior(self.entry_formant_pre_emphasis, 'formant_pre_emphasis')

        # 共振峰参数 row 5: Sample Strategy ("整段11点" / "中段均值")
        row_formant_strategy = ctk.CTkFrame(self.formant_params_container, fg_color="transparent")
        row_formant_strategy.pack(fill=tk.X, padx=15, pady=4)
        ctk.CTkLabel(row_formant_strategy, text=" 采样策略:", image=self.icons.get("tag"), compound="left", text_color="#374151", font=self.font_main).pack(side=tk.LEFT)
        
        self.option_formant_sample_strategy = ctk.CTkOptionMenu(
            row_formant_strategy,
            values=["整段11点", "中段均值"],
            command=self.on_formant_strategy_change,
            width=100,
            height=26,
            corner_radius=20,
            fg_color="#F3F4F6",
            text_color="#1F2937",
            button_color="#E5E7EB",
            button_hover_color="#D1D5DB"
        )
        self.option_formant_sample_strategy.set(self.last_params.get('formant_sample_strategy', '整段11点'))
        self.option_formant_sample_strategy.pack(side=tk.RIGHT)

        self.row_trim = ctk.CTkFrame(self.params_content_frame, fg_color="transparent")
        self.row_trim.pack(fill=tk.X, padx=15, pady=(10, 15))
        self.lbl_trim_icon = ctk.CTkLabel(self.row_trim, text="", image=self.icons.get("trim"))
        self.lbl_trim_icon.pack(side=tk.LEFT, padx=(0, 5))
        self.switch_trim_silence = ctk.CTkSwitch(self.row_trim, text="开启边缘静音裁切", font=self.font_main,
                                                 progress_color="#10B981", text_color="#374151", command=self.on_trim_silence_toggle)
        self.switch_trim_silence.pack(side=tk.LEFT)
        self.switch_trim_silence.select()

        self.row_export_rule = ctk.CTkFrame(self.params_content_frame, fg_color="transparent")
        self.row_export_rule.pack(fill=tk.X, padx=15, pady=(0, 15))
        ctk.CTkLabel(self.row_export_rule, text=" 导出编号:", image=self.icons.get("tag"), compound="left", text_color="#374151", font=self.font_main).pack(side=tk.LEFT)
        export_rule_opts = ctk.CTkFrame(self.row_export_rule, fg_color="transparent")
        export_rule_opts.pack(side=tk.LEFT, padx=(10, 0))

        def on_export_rule_change():
            if hasattr(self, 'tree_panel') and self.tree_panel:
                self.tree_panel.on_export_numbering_rule_changed()

        ctk.CTkRadioButton(
            export_rule_opts, text="全部连续", variable=self.export_numbering_rule_var,
            value="continuous", command=on_export_rule_change, font=self.font_main,
            radiobutton_width=18, radiobutton_height=18, width=0
        ).pack(side=tk.LEFT, padx=(0, 12))
        ctk.CTkRadioButton(
            export_rule_opts, text="每组重新标号", variable=self.export_numbering_rule_var,
            value="per_group", command=on_export_rule_change, font=self.font_main,
            radiobutton_width=18, radiobutton_height=18, width=0
        ).pack(side=tk.LEFT)

        # 全局应用按钮 (固定在底部)
        self.btn_apply_all = CTkReleaseButton(sidebar_frame, text="  全局应用", image=self.icons.get("check_white"), compound="left",
                                              command=self.recalculate_all_audio, corner_radius=20, height=44, font=self.font_title,
                                              fg_color="#3B82F6", hover_color="#2563EB")
        self.btn_apply_all.pack(fill=tk.X, pady=(10, 15))

        # 实例化右侧树状面板 (先于中间面板初始化以确保正确的 pack 顺序)
        self.tree_panel = ProjectTreePanel(
            parent=self.root,
            icons=self.icons,
            tk_icons=self.tk_icons,
            items_dict=self.items,
            app_state_params=self.last_params,
            on_item_selected_callback=self.on_tree_item_selected,
            on_clear_canvas_callback=self.on_clear_canvas_callback,
            app=self
        )

        # 实例化中间画布面板 (最后初始化并 expand=True 以占据剩余空间)
        self.spectrogram_panel = SpectrogramPanel(
            parent=self.root,
            icons=self.icons,
            on_time_changed_callback=self.on_spectrogram_time_changed,
            on_auto_detect_callback=self.on_spectrogram_auto_detect,
            on_export_callback=self.on_export_callback,
            app=self
        )
        self.spectrogram_panel.switch_trim_silence = self.switch_trim_silence

    # --- 交互回调 ---
    def on_tree_item_selected(self, iid):
        item = self.items[iid]

        if item.get('snd') and (item.get('pitch_data') or item.get('pitch')):
            self._sync_ui_and_plot(item)
            return

        if not item.get('path'):
            return

        def run():
            try:
                self.root.after(0, lambda: self.set_status(f"正在读取音频: {item['label']}...", "#3B82F6", "status_loading"))

                snd = parselmouth.Sound(item['path'])
                pitch_data = extract_f0(snd, self.last_params)

                def done():
                    item['snd'] = snd
                    item['pitch_data'] = pitch_data
                    if 'pitch' in item:
                        del item['pitch']
                    item['pitch_floor'] = self.last_params['pitch_floor']
                    item['pitch_ceiling'] = self.last_params['pitch_ceiling']
                    item['voicing_threshold'] = self.last_params.get('voicing_threshold', 0.25)
                    item['f0_engine'] = self.last_params.get('f0_engine', 'praat')

                    self.set_status("就绪", "#10B981", "status_success")
                    self._sync_ui_and_plot(item)

                self.root.after(0, done)
            except Exception as e:
                self.root.after(0, lambda: self.set_status(f"加载失败: {str(e)}", "#EF4444", "status_error"))

        threading.Thread(target=run, daemon=True).start()

    def _sync_ui_and_plot(self, item):
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
        if 'preview_formants' in item:
            item.pop('preview_formants')
        if 'has_empty_data' in item:
            item.pop('has_empty_data')

        self.mark_modified()
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

        # 重新提取 F0 / Formant，以还原橡皮擦抹去的数据点
        from .audio_core import extract_f0, extract_formants
        try:
            item['pitch_data'] = extract_f0(snd, self.last_params)
            if 'pitch' in item:
                del item['pitch']
        except Exception:
            pass
        try:
            item['formant_data'] = extract_formants(snd, self.last_params)
        except Exception:
            pass
        item.pop('preview_f0', None)
        item.pop('preview_formants', None)
        item.pop('has_empty_data', None)

        pitch = item.get('pitch_data', item.get('pitch'))
        mac_s, mac_e = item['macro_start'], item['macro_end']

        def run():
            try:
                self.root.after(0, lambda: self.start_loading("正在智能识别..."))
                mic_s, mic_e, raw_s, raw_e = self._microscopic_vowel_nucleus(snd, pitch, mac_s, mac_e)

                label = item['label'].replace(" (缺失)", "")
                syls = split_into_syllables(label)
                split_warnings = []
                split_confidence = 1.0
                if len(syls) > 1:
                    meta = {}
                    inner_splits = auto_split_inner_word(snd, raw_s, raw_e, len(syls), pitch_data=pitch, output_meta=meta)
                    split_warnings = meta.get('split_warnings', [])
                    split_confidence = meta.get('split_confidence', 1.0)
                    from modules.audio_core import auto_split_to_chars_bounds
                    chars_bounds = auto_split_to_chars_bounds(snd, raw_s, raw_e, inner_splits, len(syls), self.last_params)
                    if chars_bounds:
                        mic_s = chars_bounds[0][0]
                        mic_e = chars_bounds[-1][1]
                else:
                    inner_splits = []
                    chars_bounds = [[mic_s, mic_e]]

                def update_ui():
                    item['start'] = mic_s
                    item['end'] = mic_e
                    item['raw_start'] = raw_s
                    item['raw_end'] = raw_e
                    item['inner_splits'] = inner_splits
                    item['chars_bounds'] = chars_bounds
                    item['split_warnings'] = split_warnings
                    item['split_confidence'] = split_confidence
                    item['has_empty_data'] = item.get('has_empty_data', False) or len(split_warnings) > 0
                    item['analysis_mode'] = self.last_params.get('analysis_mode', 'f0')

                    if item.get('snd') and item.get('formant_data'):
                        pts = int(self.last_params.get('pts', 11))
                        strategy = self.last_params.get('formant_sample_strategy', '整段11点')
                        _, preview_f1, preview_f2 = self.sample_formant_points(item, pts, strategy)
                        item['preview_formants'] = {"f1": preview_f1, "f2": preview_f2}

                    self.spectrogram_panel.var_t_start.set(f"{mic_s:.3f}")
                    self.spectrogram_panel.var_t_end.set(f"{mic_e:.3f}")
                    self.spectrogram_panel.plot_item_spectrogram()
                    self.spectrogram_panel.update_ui_times()
                    self.mark_modified()
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


    def on_add_speaker(self):
        idx = len(self.speaker_manager.get_all_speakers()) + 1
        while True:
            name = f"发音人 {idx}"
            if not any(s.name == name for s in self.speaker_manager.get_all_speakers()):
                break
            idx += 1
        # 新增发音人时自动继承当前活动发音人的基频提取引擎设置，避免非预期的重置
        current_engine = self.last_params.get('f0_engine', 'praat')
        new_speaker = self.speaker_manager.add_speaker(name, default_engine=current_engine)
        new_speaker.tab_mode = self.tabview.get()
        self._update_speaker_dropdown()
        self.speaker_option_var.set(new_speaker.name)
        self.on_speaker_changed(new_speaker.name)
        self.mark_modified()

    def on_rename_speaker(self):
        dialog = ctk.CTkInputDialog(text="请输入新的名称:", title="重命名发音人")
        new_name = dialog.get_input()
        if new_name and new_name.strip():
            self.speaker_manager.rename_speaker(self.speaker_manager.active_speaker_id, new_name.strip())
            self._update_speaker_dropdown()
            self.speaker_option_var.set(new_name.strip())
            self.mark_modified()

    def on_delete_speaker(self):
        if len(self.speaker_manager.speakers) <= 1:
            messagebox.showwarning("提示", "必须至少保留一个发音人。")
            return
        if messagebox.askyesno("确认", f"确定要删除发音人 '{self.active_speaker.name}' 吗？其所有数据将丢失。"):
            self.speaker_manager.remove_speaker(self.speaker_manager.active_speaker_id)
            self._update_speaker_dropdown()
            self.speaker_option_var.set(self.active_speaker.name)
            self.on_speaker_changed(self.active_speaker.name)
            self.mark_modified()

    def _update_speaker_dropdown(self):
        self.speaker_dropdown.configure(values=[s.name for s in self.speaker_manager.get_all_speakers()])

    def on_speaker_changed(self, selected_name):
        for s in self.speaker_manager.get_all_speakers():
            if s.name == selected_name:
                self.active_speaker.tab_mode = self.tabview.get()
                self.speaker_manager.set_active_speaker(s.id)
                self._refresh_ui_for_speaker()
                break

    def _refresh_ui_for_speaker(self):
        if hasattr(self.active_speaker, 'tab_mode'):
            try: self.tabview.set(self.active_speaker.tab_mode)
            except ValueError: pass
        self.slider_pts.set(self.last_params['pts'])
        self.entry_points.delete(0, tk.END)
        self.entry_points.insert(0, str(self.last_params['pts']))
        self.slider_db.set(self.last_params['db'])
        self.var_drop_db.set(str(self.last_params['db']))
        self.slider_dur.set(self.last_params['skip_front'])
        self.var_min_dur.set(f"{self.last_params['skip_front']:.2f}")
        self.entry_pitch_ceiling.delete(0, tk.END)
        self.entry_pitch_ceiling.insert(0, str(self.last_params['pitch_ceiling']))
        self.entry_pitch_floor.delete(0, tk.END)
        self.entry_pitch_floor.insert(0, str(self.last_params['pitch_floor']))
        self.entry_voicing_threshold.delete(0, tk.END)
        self.entry_voicing_threshold.insert(0, f"{self.last_params['voicing_threshold']:.2f}")
        if hasattr(self, 'mode_button') and self.mode_button:
            initial_mode = "声调/F0" if self.last_params.get('analysis_mode', 'f0') == 'f0' else "共振峰/F1-F2"
            self.mode_button.set(initial_mode)
            self._update_mode_button_text_colors()
            self.update_param_containers_visibility()

        if hasattr(self, 'entry_formant_max_hz') and self.entry_formant_max_hz:
            self.entry_formant_max_hz.delete(0, tk.END)
            self.entry_formant_max_hz.insert(0, str(self.last_params.get('formant_max_hz', 5500.0)))
            self.entry_formant_count.delete(0, tk.END)
            self.entry_formant_count.insert(0, str(self.last_params.get('formant_count', 5)))
            self.entry_formant_window_length.delete(0, tk.END)
            self.entry_formant_window_length.insert(0, str(self.last_params.get('formant_window_length', 0.025)))
            self.entry_formant_pre_emphasis.delete(0, tk.END)
            self.entry_formant_pre_emphasis.insert(0, str(self.last_params.get('formant_pre_emphasis', 50.0)))
            self.option_formant_sample_strategy.set(self.last_params.get('formant_sample_strategy', '整段11点'))

        if self.pending_long_snd: self.lbl_long_file.configure(text="已加载音频", text_color="#2563EB")
        else: self.lbl_long_file.configure(text="未选择", text_color="#6B7280")
        if self.pending_batch_paths: self.lbl_batch_files.configure(text=f"已选 {len(self.pending_batch_paths)} 个文件", text_color="#2563EB")
        else: self.lbl_batch_files.configure(text="未选择", text_color="#6B7280")

        if hasattr(self, 'tree_panel'):
            self.tree_panel.items = self.items
            self.tree_panel.app_state_params = self.last_params
            self.tree_panel.clear_ui_only()
            unique_groups = set()
            for item in self.items.values(): unique_groups.add(item.get('group', '导入内容'))
            for g in unique_groups: self.tree_panel.ensure_group(g)
            # 使用 rebuild_tree 重新构建，确保所有项的警告状态被精准重新评估
            self.tree_panel.rebuild_tree()

            if hasattr(self.active_speaker, 'last_selected_iid') and self.active_speaker.last_selected_iid in self.items:
                try:
                    iid_to_select = self.active_speaker.last_selected_iid
                    if not self.tree_panel.tree.exists(iid_to_select):
                        warning_iid = f"warning_{iid_to_select}"
                        if self.tree_panel.tree.exists(warning_iid):
                            iid_to_select = warning_iid
                    self.tree_panel.tree.selection_set(iid_to_select)
                    self.tree_panel.on_tree_select(None)
                except tk.TclError:
                    self.tree_panel.select_first_item()
            else:
                self.tree_panel.select_first_item()

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
            if param_key in ['pts', 'db', 'skip_front', 'pitch_floor', 'pitch_ceiling', 'voicing_threshold', 'formant_max_hz', 'formant_count', 'formant_window_length', 'formant_pre_emphasis']: self.on_param_change()

        entry.bind("<Enter>", on_enter)
        entry.bind("<Leave>", on_leave)
        entry.bind("<FocusIn>", on_focus_in)
        entry.bind("<FocusOut>", on_focus_out)
        entry.bind("<Return>", lambda e: self.root.focus_set())

    def _build_worker_params(self):
        return {
            'db': self.last_params['db'],
            'skip_front': self.last_params['skip_front'],
            'pitch_floor': self.last_params['pitch_floor'],
            'pitch_ceiling': self.last_params['pitch_ceiling'],
            'voicing_threshold': self.last_params.get('voicing_threshold', 0.25),
            'f0_engine': self.last_params.get('f0_engine', 'praat'),
            'analysis_mode': self.last_params.get('analysis_mode', 'f0'),
            'formant_max_hz': self.last_params.get('formant_max_hz', 5500.0),
            'formant_count': self.last_params.get('formant_count', 5),
            'formant_window_length': self.last_params.get('formant_window_length', 0.025),
            'formant_pre_emphasis': self.last_params.get('formant_pre_emphasis', 50.0),
            'formant_sample_strategy': self.last_params.get('formant_sample_strategy', '整段11点'),
            'pts': self.last_params.get('pts', 11),
        }

    def _stamp_formant_params_on_item(self, item, params=None):
        params = params or self._build_worker_params()
        item['analysis_mode'] = params.get('analysis_mode', self.last_params.get('analysis_mode', 'f0'))
        item['formant_max_hz'] = params.get('formant_max_hz', self.last_params.get('formant_max_hz', 5500.0))
        item['formant_count'] = params.get('formant_count', self.last_params.get('formant_count', 5))
        item['formant_window_length'] = params.get('formant_window_length', self.last_params.get('formant_window_length', 0.025))
        item['formant_pre_emphasis'] = params.get('formant_pre_emphasis', self.last_params.get('formant_pre_emphasis', 50.0))
        item['formant_sample_strategy'] = params.get('formant_sample_strategy', self.last_params.get('formant_sample_strategy', '整段11点'))

    def _maybe_refresh_formants_after_import(self):
        if self.last_params.get('analysis_mode', 'f0') != 'formant':
            return
        for item in self.items.values():
            if item.get('snd') and item.get('start') is not None and item.get('end') is not None and not item.get('formant_data'):
                self.root.after(50, self.recalculate_all_formants)
                return

    def on_param_change(self, event=None, recalculate_current=True):
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
            
            # F0 specific params check
            if hasattr(self, 'entry_pitch_floor') and self.entry_pitch_floor:
                try:
                    new_floor = int(self.entry_pitch_floor.get())
                    new_ceiling = int(self.entry_pitch_ceiling.get())
                    new_voicing = float(self.entry_voicing_threshold.get())
                    
                    if new_floor != self.last_params.get('pitch_floor', 75):
                        self.last_params['pitch_floor'] = new_floor
                        recompute_pitch = True
                    if new_ceiling != self.last_params.get('pitch_ceiling', 600):
                        self.last_params['pitch_ceiling'] = new_ceiling
                        recompute_pitch = True
                    if new_voicing != self.last_params.get('voicing_threshold', 0.25):
                        self.last_params['voicing_threshold'] = new_voicing
                        recompute_pitch = True
                except ValueError: pass

            # Formant specific params check
            recompute_formant = False
            if hasattr(self, 'entry_formant_max_hz') and self.entry_formant_max_hz:
                try:
                    new_formant_max = float(self.entry_formant_max_hz.get())
                    if new_formant_max != self.last_params.get('formant_max_hz', 5500.0):
                        self.last_params['formant_max_hz'] = new_formant_max
                        recompute_formant = True
                except ValueError: pass
                
                try:
                    new_formant_count = int(self.entry_formant_count.get())
                    if new_formant_count != self.last_params.get('formant_count', 5):
                        self.last_params['formant_count'] = new_formant_count
                        recompute_formant = True
                except ValueError: pass
                
                try:
                    new_formant_window = float(self.entry_formant_window_length.get())
                    if new_formant_window != self.last_params.get('formant_window_length', 0.025):
                        self.last_params['formant_window_length'] = new_formant_window
                        recompute_formant = True
                except ValueError: pass
                
                try:
                    new_formant_pre = float(self.entry_formant_pre_emphasis.get())
                    if new_formant_pre != self.last_params.get('formant_pre_emphasis', 50.0):
                        self.last_params['formant_pre_emphasis'] = new_formant_pre
                        recompute_formant = True
                except ValueError: pass

            # 即使全局 last_params 没变，只要当前输入框的值与“当前选中项的专属参数”不同，也要强制重算当前项
            curr_item = getattr(self, 'spectrogram_panel', None) and self.spectrogram_panel.current_item
            if curr_item:
                if hasattr(self, 'entry_pitch_floor') and self.entry_pitch_floor:
                    try:
                        if int(self.entry_pitch_floor.get()) != curr_item.get('pitch_floor', self.last_params.get('pitch_floor', 75)): recompute_pitch = True
                        if int(self.entry_pitch_ceiling.get()) != curr_item.get('pitch_ceiling', self.last_params.get('pitch_ceiling', 600)): recompute_pitch = True
                        if float(self.entry_voicing_threshold.get()) != curr_item.get('voicing_threshold', self.last_params.get('voicing_threshold', 0.25)): recompute_pitch = True
                    except ValueError: pass
                    
                if hasattr(self, 'entry_formant_max_hz') and self.entry_formant_max_hz:
                    try:
                        if float(self.entry_formant_max_hz.get()) != curr_item.get('formant_max_hz', self.last_params.get('formant_max_hz', 5500.0)): recompute_formant = True
                        if int(self.entry_formant_count.get()) != curr_item.get('formant_count', self.last_params.get('formant_count', 5)): recompute_formant = True
                        if float(self.entry_formant_window_length.get()) != curr_item.get('formant_window_length', self.last_params.get('formant_window_length', 0.025)): recompute_formant = True
                        if float(self.entry_formant_pre_emphasis.get()) != curr_item.get('formant_pre_emphasis', self.last_params.get('formant_pre_emphasis', 50.0)): recompute_formant = True
                    except ValueError: pass

            need_recompute = (changed_algo or recompute_pitch or recompute_formant)
            if recalculate_current and need_recompute:
                if recompute_formant and not recompute_pitch and not changed_algo:
                    self.recalculate_current_item(recompute_formant_only=True)
                else:
                    self.recalculate_current_item(recompute_pitch=True)
                
            if new_pts != self.last_params['pts']:
                self.last_params['pts'] = new_pts
                for iid in list(self.items.keys()):
                    self.tree_panel.update_item_icon(iid)
                self.tree_panel.update_preview()
        except ValueError: pass

    def on_trim_silence_toggle(self):
        self.recalculate_current_item(only_trim_silence=True)

    def on_engine_change(self, value):
        self.last_params['f0_engine'] = value
        self._update_engine_button_text_colors()
        if hasattr(self, 'audio_cache'):
            self.audio_cache.clear()
        self.recalculate_current_item(recompute_pitch=True)

    def _update_engine_button_text_colors(self):
        pass

    def on_formant_strategy_change(self, value):
        self.last_params['formant_sample_strategy'] = value
        self.on_param_change()

    def _mode_state_key(self, mode):
        return f"_mode_state_{mode}"

    def _save_item_mode_state(self, item, mode):
        fields = ('start', 'end', 'raw_start', 'raw_end', 'inner_splits', 'chars_bounds', 'split_warnings', 'split_confidence')
        state = {}
        for field in fields:
            if field in item:
                state[field] = copy.deepcopy(item[field])
        if state:
            item[self._mode_state_key(mode)] = state

    def _load_item_mode_state(self, item, mode):
        fields = ('start', 'end', 'raw_start', 'raw_end', 'inner_splits', 'chars_bounds', 'split_warnings', 'split_confidence')
        state = item.get(self._mode_state_key(mode))
        if state:
            for field in fields:
                if field in state:
                    item[field] = copy.deepcopy(state[field])
        else:
            base_start = float(item.get('macro_start', item.get('start', 0.0)))
            base_end = float(item.get('macro_end', item.get('end', base_start + 0.01)))
            if base_end <= base_start:
                base_start = float(item.get('start', base_start))
                base_end = float(item.get('end', base_start + 0.01))
            item['start'] = base_start
            item['end'] = max(base_end, base_start + 0.01)
            item['raw_start'] = item['start']
            item['raw_end'] = item['end']
            item['inner_splits'] = []
            item['chars_bounds'] = [[item['start'], item['end']]]
            item['split_warnings'] = []
            item['split_confidence'] = 1.0

        item.pop('preview_f0', None)
        item.pop('preview_formants', None)
        item.pop('has_empty_data', None)
        item.pop('warnings', None)
        item['analysis_mode'] = mode

    def _reset_item_mode_boundaries(self, item, mode):
        base_start = float(item.get('macro_start', item.get('start', 0.0)))
        base_end = float(item.get('macro_end', item.get('end', base_start + 0.01)))
        if base_end <= base_start:
            base_start = float(item.get('start', base_start))
            base_end = float(item.get('end', base_start + 0.01))
        if base_end <= base_start:
            base_end = base_start + 0.01

        label = item.get('label', '').replace(" (缺失)", "")
        syls = split_into_syllables(label)
        syl_count = max(1, len(syls))
        splits = np.linspace(base_start, base_end, syl_count + 1).tolist()
        chars_bounds = [[splits[i], splits[i + 1]] for i in range(syl_count)]
        inner_splits = splits[1:-1] if syl_count > 1 else []

        item['start'] = chars_bounds[0][0]
        item['end'] = chars_bounds[-1][1]
        item['raw_start'] = item['start']
        item['raw_end'] = item['end']
        item['chars_bounds'] = chars_bounds
        item['inner_splits'] = inner_splits
        item['split_warnings'] = []
        item['split_confidence'] = 1.0
        item['is_manual_edited'] = False
        item['analysis_mode'] = mode
        item.pop('preview_f0', None)
        item.pop('preview_formants', None)
        item.pop('has_empty_data', None)
        item.pop('warnings', None)

    def on_analysis_mode_change(self, value):
        old_mode = self.last_params.get('analysis_mode', 'f0')
        mode = 'f0' if value == "声调/F0" else 'formant'
        if mode == old_mode:
            return
        self.last_params['analysis_mode'] = mode
        for item in self.items.values():
            self._save_item_mode_state(item, old_mode)
            # Switching mode should start from a clean boundary state in that mode.
            self._reset_item_mode_boundaries(item, mode)
        self._update_mode_button_text_colors()
        self.update_param_containers_visibility()
        # Refresh current spectrogram panel erasure button text
        if hasattr(self, 'spectrogram_panel') and self.spectrogram_panel:
            self.spectrogram_panel.update_eraser_button_text()
        if mode == 'formant':
            self.recalculate_all_formants()
        else:
            if self.spectrogram_panel and self.spectrogram_panel.current_item:
                self.spectrogram_panel.plot_item_spectrogram()
                self.spectrogram_panel.update_ui_times()
            self.tree_panel.update_preview()
        self.mark_modified()

    def _update_mode_button_text_colors(self):
        current_val = self.mode_button.get()
        if hasattr(self.mode_button, "_buttons_dict"):
            for val, btn in self.mode_button._buttons_dict.items():
                if val == current_val:
                    btn.configure(text_color="white")
                else:
                    btn.configure(text_color="#1F2937")

    def update_param_containers_visibility(self):
        mode = self.last_params.get('analysis_mode', 'f0')
        if mode == 'formant':
            self.f0_params_container.pack_forget()
            self.row_trim.pack_forget()
            self.row_export_rule.pack_forget()
            
            self.formant_params_container.pack(fill=tk.X)
            self.row_trim.pack(fill=tk.X, padx=15, pady=(10, 15))
            self.row_export_rule.pack(fill=tk.X, padx=15, pady=(0, 15))
        else:
            self.formant_params_container.pack_forget()
            self.row_trim.pack_forget()
            self.row_export_rule.pack_forget()
            
            self.f0_params_container.pack(fill=tk.X)
            self.row_trim.pack(fill=tk.X, padx=15, pady=(10, 15))
            self.row_export_rule.pack(fill=tk.X, padx=15, pady=(0, 15))

    def sample_formant_points(self, item, pts=11, strategy='整段11点'):
        start = item['start']
        end = item['end']
        preview_times = np.linspace(start, end, pts)
        
        f_data = item.get('formant_data')
        if not f_data or 'xs' not in f_data or 'f1' not in f_data or 'f2' not in f_data:
            nan_list = [np.nan] * pts
            return preview_times, nan_list, nan_list
            
        xs = f_data['xs']
        f1_arr = f_data['f1']
        f2_arr = f_data['f2']
        
        if strategy == '中段均值':
            duration = end - start
            m_start = start + duration / 3.0
            m_end = start + 2.0 * duration / 3.0
            
            mask = (xs >= m_start) & (xs <= m_end)
            f1_slice = f1_arr[mask]
            f2_slice = f2_arr[mask]
            
            f1_vals = f1_slice[~np.isnan(f1_slice)]
            f2_vals = f2_slice[~np.isnan(f2_slice)]
            
            mean_f1 = np.nanmean(f1_vals) if len(f1_vals) > 0 else np.nan
            mean_f2 = np.nanmean(f2_vals) if len(f2_vals) > 0 else np.nan
            
            preview_f1 = [mean_f1] * pts
            preview_f2 = [mean_f2] * pts
        else:
            preview_f1 = []
            preview_f2 = []
            
            f1_valid_idx = np.where(~np.isnan(f1_arr))[0]
            f2_valid_idx = np.where(~np.isnan(f2_arr))[0]
            
            for t in preview_times:
                # F1
                if len(f1_valid_idx) == 0 or t < xs[0] or t > xs[-1]:
                    preview_f1.append(np.nan)
                else:
                    nearest_idx = np.argmin(np.abs(xs[f1_valid_idx] - t))
                    if np.abs(xs[f1_valid_idx][nearest_idx] - t) > 0.04:
                        preview_f1.append(np.nan)
                    else:
                        preview_f1.append(float(np.interp(t, xs[f1_valid_idx], f1_arr[f1_valid_idx])))
                # F2
                if len(f2_valid_idx) == 0 or t < xs[0] or t > xs[-1]:
                    preview_f2.append(np.nan)
                else:
                    nearest_idx = np.argmin(np.abs(xs[f2_valid_idx] - t))
                    if np.abs(xs[f2_valid_idx][nearest_idx] - t) > 0.04:
                        preview_f2.append(np.nan)
                    else:
                        preview_f2.append(float(np.interp(t, xs[f2_valid_idx], f2_arr[f2_valid_idx])))
                        
        return preview_times.tolist(), preview_f1, preview_f2

    def recalculate_all_audio(self, only_trim_silence=False, recompute_pitch=True, only_pitch_changed=False):
        if not self.items: return

        # Capture parameter values before sync
        old_params = dict(self.last_params) if hasattr(self, 'last_params') and self.last_params else {}

        # 1. 确保所有 UI 输入框的最新的参数值都已经同步到了 self.last_params 中（在主线程中执行）
        try:
            self.on_param_change(recalculate_current=False)
        except Exception:
            pass

        # Check if ONLY acoustic parameters (pitch or formant) changed
        if not only_pitch_changed and old_params and hasattr(self, 'last_params') and self.last_params:
            new_params = self.last_params
            pitch_changed = (
                old_params.get('pitch_floor') != new_params.get('pitch_floor') or
                old_params.get('pitch_ceiling') != new_params.get('pitch_ceiling') or
                old_params.get('voicing_threshold') != new_params.get('voicing_threshold')
            )
            formant_changed = (
                old_params.get('formant_max_hz') != new_params.get('formant_max_hz') or
                old_params.get('formant_count') != new_params.get('formant_count') or
                old_params.get('formant_window_length') != new_params.get('formant_window_length') or
                old_params.get('formant_pre_emphasis') != new_params.get('formant_pre_emphasis') or
                old_params.get('formant_sample_strategy') != new_params.get('formant_sample_strategy') or
                old_params.get('analysis_mode') != new_params.get('analysis_mode')
            )
            boundary_params_changed = (
                old_params.get('db') != new_params.get('db') or
                old_params.get('skip_front') != new_params.get('skip_front')
            )
            if (pitch_changed or formant_changed) and not boundary_params_changed:
                only_pitch_changed = True

        items_snapshot = list(self.items.items())
        total = len(items_snapshot)

        def run():
            self.root.after(0, lambda: self.start_loading("正在重新计算..."))
            trim_silence = self.switch_trim_silence.get()

            if only_trim_silence:
                for i, (iid, item) in enumerate(items_snapshot):
                    if item.get('snd') and 'raw_start' in item and 'raw_end' in item:
                        mic_s, mic_e = recalculate_bounds_fast(
                            item['snd'], item.get('pitch_data', item.get('pitch')), item['raw_start'], item['raw_end'], trim_silence
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
                    'voicing_threshold': self.last_params.get('voicing_threshold', 0.25),
                    'f0_engine': self.last_params.get('f0_engine', 'praat'),
                    'analysis_mode': self.last_params.get('analysis_mode', 'f0'),
                    'formant_max_hz': self.last_params.get('formant_max_hz', 5500.0),
                    'formant_count': self.last_params.get('formant_count', 5),
                    'formant_window_length': self.last_params.get('formant_window_length', 0.025),
                    'formant_pre_emphasis': self.last_params.get('formant_pre_emphasis', 50.0),
                    'formant_sample_strategy': self.last_params.get('formant_sample_strategy', '整段11点'),
                    'pts': self.last_params.get('pts', 11),
                }

                valid_items = []
                recomputed_pitches = {}
                for iid, item in items_snapshot:
                    if item.get('snd'):
                        snd = item['snd']
                        snd_id = id(snd)

                        # 性能优化：按 snd 实例缓存 Pitch，避免长音频模式下被重复计算上千次
                        if recompute_pitch:
                            try:
                                if snd_id not in recomputed_pitches:
                                    recomputed_pitches[snd_id] = extract_f0(snd, self.last_params)
                                item['pitch_data'] = recomputed_pitches[snd_id]
                                if 'pitch' in item:
                                    del item['pitch']
                            except Exception: pass

                        # 保存计算该项时所用的参数实现所见即所得导出
                        item['pitch_floor'] = params['pitch_floor']
                        item['pitch_ceiling'] = params['pitch_ceiling']
                        item['voicing_threshold'] = params['voicing_threshold']
                        item['f0_engine'] = self.last_params.get('f0_engine', 'praat')

                        mac_s, mac_e = item['macro_start'], item['macro_end']
                        valid_ms = max(0, mac_s)
                        valid_me = min(snd.get_total_duration(), mac_e)

                        if valid_me > valid_ms:
                            part = snd.extract_part(from_time=valid_ms, to_time=valid_me)

                            # 性能优化：切片 Pitch 数组，大幅减少 IPC 数据量
                            p_xs = item['pitch_data']['xs']
                            p_freqs = item['pitch_data']['freqs']
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
                    engine = self.last_params.get('f0_engine', 'praat')
                    max_workers = 2 if engine == 'reaper' else min(os.cpu_count() or 4, 8)
                    with concurrent.futures.ProcessPoolExecutor(max_workers=max_workers) as executor:
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
                                        if not target_item.get('is_manual_edited') and not only_pitch_changed:
                                            target_item['start'] = res['start']
                                            target_item['end'] = res['end']
                                            target_item['raw_start'] = res['raw_start']
                                            target_item['raw_end'] = res['raw_end']
                                            target_item['inner_splits'] = res.get('inner_splits', [])
                                            target_item['chars_bounds'] = res.get('chars_bounds', [])
                                            target_item['split_warnings'] = res.get('split_warnings', [])
                                            target_item['split_confidence'] = res.get('split_confidence', 1.0)
                                        target_item['pitch_data'] = res['pitch_data']
                                        if 'pitch' in target_item:
                                            del target_item['pitch']
                                        target_item.pop('has_empty_data', None)
                                        target_item['preview_f0'] = res.get('preview_f0', [])
                                        if 'formant_data' in res:
                                            target_item['formant_data'] = res['formant_data']
                                        if 'preview_formants' in res:
                                            target_item['preview_formants'] = res['preview_formants']
                                        # 如果是独立音频，还需要把 Cache 也更新了，防止下次加载又是旧的
                                        if target_item.get('path'):
                                            self.audio_cache[target_item['path']] = res
                                    else:
                                        # 合并长音频处理结果
                                        if not target_item.get('is_manual_edited') and not only_pitch_changed:
                                            target_item['start'] = res['mis']
                                            target_item['end'] = res['mie']
                                            target_item['raw_start'] = res['raw_s']
                                            target_item['raw_end'] = res['raw_e']
                                            target_item['inner_splits'] = res.get('inner_splits', [])
                                            target_item['chars_bounds'] = res.get('chars_bounds', [])
                                            target_item['split_warnings'] = res.get('split_warnings', [])
                                            target_item['split_confidence'] = res.get('split_confidence', 1.0)
                                        target_item.pop('has_empty_data', None)
                                        target_item['preview_f0'] = res.get('preview_f0', [])
                                        if 'formant_data' in res:
                                            target_item['formant_data'] = res['formant_data']
                                        if 'preview_formants' in res:
                                            target_item['preview_formants'] = res['preview_formants']
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
                self.mark_modified()
                self.stop_loading("全局参数已应用")

            self.root.after(0, finalize)
        threading.Thread(target=run, daemon=True).start()

    def recalculate_current_item(self, only_trim_silence=False, recompute_pitch=False, recompute_formant_only=False):
        """仅针对当前正在编辑的项重新计算参数（暂不影响全局）"""
        item = self.spectrogram_panel.current_item
        if not item: return

        def run():
            try:
                self.root.after(0, lambda: self.set_status("正在更新当前项...", "#3B82F6", "status_loading"))

                def get_segmented_f0(snd, last_params):
                    total_dur = snd.get_total_duration()
                    if 'macro_start' in item and 'macro_end' in item and total_dur > 15.0:
                        padding = 1.0
                        seg_start = max(0.0, item['macro_start'] - padding)
                        seg_end = min(total_dur, item['macro_end'] + padding)
                        part_snd = snd.extract_part(from_time=seg_start, to_time=seg_end)
                        part_pitch_data = extract_f0(part_snd, last_params)
                        part_pitch_data['xs'] = part_pitch_data['xs'] + seg_start
                        return part_pitch_data
                    else:
                        return extract_f0(snd, last_params)

                def get_segmented_formant(snd, last_params):
                    from modules.audio_core import extract_formants
                    total_dur = snd.get_total_duration()
                    if 'macro_start' in item and 'macro_end' in item and total_dur > 15.0:
                        padding = 1.0
                        seg_start = max(0.0, item['macro_start'] - padding)
                        seg_end = min(total_dur, item['macro_end'] + padding)
                        part_snd = snd.extract_part(from_time=seg_start, to_time=seg_end)
                        part_formant_data = extract_formants(part_snd, last_params)
                        part_formant_data['xs'] = part_formant_data['xs'] + seg_start
                        return part_formant_data
                    else:
                        return extract_formants(snd, last_params)

                # 如果是独立音频模式且没有加载 Sound 对象
                if not item.get('snd') and item.get('path'):
                    item['snd'] = parselmouth.Sound(item['path'])
                    # 总是为单项重新生成 pitch 确保准确性
                    if not recompute_formant_only:
                        item['pitch_data'] = get_segmented_f0(item['snd'], self.last_params)
                        if 'pitch' in item:
                            del item['pitch']
                        item['pitch_floor'] = self.last_params['pitch_floor']
                        item['pitch_ceiling'] = self.last_params['pitch_ceiling']
                        item['voicing_threshold'] = self.last_params.get('voicing_threshold', 0.25)
                        item['f0_engine'] = self.last_params.get('f0_engine', 'praat')
                    
                    # 总是为单项重新生成 formant
                    item['formant_data'] = get_segmented_formant(item['snd'], self.last_params)
                    
                    # 独立音频的宏观边界就是全文
                    item['macro_start'] = 0.0
                    item['macro_end'] = item['snd'].get_total_duration()

                # 如果是仅重新计算共振峰且 Sound 对象已存在
                if recompute_formant_only and item.get('snd'):
                    item['formant_data'] = get_segmented_formant(item['snd'], self.last_params)
                # 如果修改了 Pitch Floor/Ceiling 或 Formant 参数，且非仅重算共振峰，重新计算该项
                elif recompute_pitch and item.get('snd'):
                    item['pitch_data'] = get_segmented_f0(item['snd'], self.last_params)
                    if 'pitch' in item:
                        del item['pitch']
                    item['pitch_floor'] = self.last_params['pitch_floor']
                    item['pitch_ceiling'] = self.last_params['pitch_ceiling']
                    item['voicing_threshold'] = self.last_params.get('voicing_threshold', 0.25)
                    item['f0_engine'] = self.last_params.get('f0_engine', 'praat')
                    
                    # 重新生成 formant
                    item['formant_data'] = get_segmented_formant(item['snd'], self.last_params)

                # 仅在非 recompute_formant_only 时才重新计算/定位边界，从而完美保护手动边界修改
                if not recompute_formant_only and item.get('snd') and 'macro_start' in item and 'macro_end' in item:
                    current_pitch = item.get('pitch_data', item.get('pitch'))
                    if only_trim_silence:
                        mic_s, mic_e = recalculate_bounds_fast(
                            item['snd'], current_pitch, item['raw_start'], item['raw_end'], self.switch_trim_silence.get()
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
                            item['snd'], current_pitch, item['macro_start'], item['macro_end']
                        )
                        item['start'], item['end'] = mic_s, mic_e
                        item['raw_start'], item['raw_end'] = raw_s, raw_e

                        label = item['label'].replace(" (缺失)", "")
                        syls = split_into_syllables(label)
                        split_warnings = []
                        split_confidence = 1.0
                        if len(syls) > 1:
                            meta = {}
                            item['inner_splits'] = auto_split_inner_word(item['snd'], raw_s, raw_e, len(syls), pitch_data=current_pitch, output_meta=meta)
                            split_warnings = meta.get('split_warnings', [])
                            split_confidence = meta.get('split_confidence', 1.0)
                            from modules.audio_core import auto_split_to_chars_bounds
                            item['chars_bounds'] = auto_split_to_chars_bounds(item['snd'], raw_s, raw_e, item['inner_splits'], len(syls), self.last_params)
                            if item['chars_bounds']:
                                item['start'] = item['chars_bounds'][0][0]
                                item['end'] = item['chars_bounds'][-1][1]
                        else:
                            item['inner_splits'] = []
                            item['chars_bounds'] = [[item['start'], item['end']]]
                        item['split_warnings'] = split_warnings
                        item['split_confidence'] = split_confidence
                        item['has_empty_data'] = item.get('has_empty_data', False) or len(split_warnings) > 0

                # 重新生成 11 点预览数据用于警告图标状态更新
                if item.get('snd') and (item.get('pitch_data') or item.get('pitch')):
                    preview_times = np.linspace(item['start'], item['end'], 11)
                    if item.get('pitch_data'):
                        p_xs = item['pitch_data']['xs']
                        p_freqs = item['pitch_data']['freqs']
                        preview_f0 = np.interp(preview_times, p_xs, p_freqs).tolist()
                        for j, t in enumerate(preview_times):
                            valid_indices = np.where(p_freqs > 0)[0]
                            if len(valid_indices) == 0:
                                preview_f0[j] = 0.0
                                continue
                            valid_xs = p_xs[valid_indices]
                            if np.min(np.abs(valid_xs - t)) > 0.025:
                                preview_f0[j] = 0.0
                    else:
                        preview_f0 = [item['pitch'].get_value_at_time(t) for t in preview_times]
                        preview_f0 = [0.0 if (np.isnan(hz) or hz <= 0) else hz for hz in preview_f0]
                    item['preview_f0'] = preview_f0
                    # 清除缓存标记，让后续 update_item_icon 通过 _check_item_has_empty_data 精准重算
                    item.pop('has_empty_data', None)

                # 同时重新生成 preview_formants
                if item.get('snd') and item.get('formant_data'):
                    pts = int(self.last_params.get('pts', 11))
                    strategy = self.last_params.get('formant_sample_strategy', '整段11点')
                    _, preview_f1, preview_f2 = self.sample_formant_points(item, pts, strategy)
                    item['preview_formants'] = {"f1": preview_f1, "f2": preview_f2}

                def finalize():
                    self.spectrogram_panel.plot_item_spectrogram()
                    self.spectrogram_panel.update_ui_times()
                    # 更新树图标（警告标志）
                    for iid, it in list(self.items.items()):
                        if it is item:
                            self.tree_panel.update_item_icon(iid)
                            break
                    self.tree_panel.update_preview()
                    self.mark_modified()
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
        if self._has_active_chart_dialog():
            messagebox.showwarning("提示", "图表编辑器已打开，修改图表期间禁止导入音频文件。")
            return
        path = filedialog.askopenfilename(filetypes=[("Audio Files", "*.wav *.mp3")])
        if not path: return
        self.lbl_long_file.configure(text=os.path.basename(path), text_color="#9CA3AF")
        def run():
            self.root.after(0, lambda: self.start_loading(f"正在加载: {os.path.basename(path)}"))
            try:
                snd = parselmouth.Sound(path)
                def done():
                    self.pending_long_snd = snd
                    self.long_audio_path = path
                    audio_name = os.path.splitext(os.path.basename(path))[0]
                    self.lbl_long_file.configure(text=os.path.basename(path), text_color="#2563EB")
                    self.mark_modified()
                    self.stop_loading("长音频就绪")
                    if self.active_speaker.name.startswith("发音人"):
                        self.speaker_manager.rename_speaker(self.speaker_manager.active_speaker_id, audio_name)
                        self._update_speaker_dropdown()
                        self.speaker_option_var.set(audio_name)
                    if getattr(self, 'switch_unified_wordlist', None) and self.switch_unified_wordlist.get() and getattr(self, 'global_wordlist_text', None):
                        self.process_long_with_wordlist(self.global_wordlist_text)
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
                            global_pitch_cache = extract_f0(self.pending_long_snd, self.last_params)
                        item['pitch_data'] = global_pitch_cache
                        if 'pitch' in item:
                            del item['pitch']

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
                            syls = split_into_syllables(label)
                            if len(syls) > 1:
                                from modules.audio_core import auto_split_to_chars_bounds
                                item['chars_bounds'] = auto_split_to_chars_bounds(
                                    item['snd'], item['start'], item['end'],
                                    item['inner_splits'], len(syls), self.last_params
                                )
                            else:
                                item['chars_bounds'] = [[item['start'], item['end']]]

                            item['raw_start'] = seg['start']
                            item['raw_end'] = seg['end']
                        else:
                            # 纯新分配的自动识别段落，调用完整识别流
                            mic_s, mic_e, raw_s, raw_e = self._microscopic_vowel_nucleus(
                                item['snd'], item.get('pitch_data', item.get('pitch')), item['macro_start'], item['macro_end']
                            )
                            item['start'], item['end'] = mic_s, mic_e
                            item['raw_start'], item['raw_end'] = raw_s, raw_e

                            label = item['label'].replace(" (缺失)", "")
                            syls = split_into_syllables(label)
                            split_warnings = []
                            split_confidence = 1.0
                            if len(syls) > 1:
                                meta = {}
                                p_data = item.get('pitch_data', item.get('pitch'))
                                item['inner_splits'] = auto_split_inner_word(item['snd'], raw_s, raw_e, len(syls), pitch_data=p_data, output_meta=meta)
                                split_warnings = meta.get('split_warnings', [])
                                split_confidence = meta.get('split_confidence', 1.0)
                                from modules.audio_core import auto_split_to_chars_bounds
                                item['chars_bounds'] = auto_split_to_chars_bounds(item['snd'], raw_s, raw_e, item['inner_splits'], len(syls), self.last_params)
                                if item['chars_bounds']:
                                    item['start'] = item['chars_bounds'][0][0]
                                    item['end'] = item['chars_bounds'][-1][1]
                            else:
                                item['inner_splits'] = []
                                item['chars_bounds'] = [[item['start'], item['end']]]
                            item['split_warnings'] = split_warnings
                            item['split_confidence'] = split_confidence
                            item['has_empty_data'] = item.get('has_empty_data', False) or len(split_warnings) > 0

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
            global_pitch_data = extract_f0(snd, self.last_params)

            if hasattr(self, 'manual_segments') and self.manual_segments:
                macro_segments = self.manual_segments
            else:
                macro_segments = macroscopic_vad(snd)

            self.current_macro_segments = macro_segments.copy()
            total = len(flat_words)
            results = []

            # 准备参数
            params = self._build_worker_params()
            trim = self.switch_trim_silence.get()
            pitch_xs = global_pitch_data['xs']
            pitch_freqs = global_pitch_data['freqs']

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
                        tasks[idx]['split_warnings'] = res.get('split_warnings', [])
                        tasks[idx]['split_confidence'] = res.get('split_confidence', 1.0)
                        tasks[idx]['formant_data'] = res.get('formant_data')
                        tasks[idx]['preview_formants'] = res.get('preview_formants')
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
                            'label': res['word'], 'group': res['group'], 'snd': snd, 'pitch_data': global_pitch_data,
                            'macro_start': res['ms'], 'macro_end': res['me'],
                            'start': res['mis'], 'end': res['mie'],
                            'inner_splits': res.get('inner_splits', []),
                            'chars_bounds': res.get('chars_bounds', []),
                            'raw_start': res.get('raw_s', res['mis']), 'raw_end': res.get('raw_e', res['mie']),
                            'pitch_floor': params['pitch_floor'],
                            'pitch_ceiling': params['pitch_ceiling'],
                            'voicing_threshold': params['voicing_threshold'],
                            'analysis_mode': params.get('analysis_mode', 'f0'),
                            'formant_data': res.get('formant_data'),
                            'preview_formants': res.get('preview_formants'),
                            'formant_max_hz': params.get('formant_max_hz'),
                            'formant_count': params.get('formant_count'),
                            'formant_window_length': params.get('formant_window_length'),
                            'formant_pre_emphasis': params.get('formant_pre_emphasis'),
                            'formant_sample_strategy': params.get('formant_sample_strategy'),
                            'is_user_specified_structure': '/' in res['word'],
                            'split_warnings': res.get('split_warnings', []),
                            'split_confidence': res.get('split_confidence', 1.0),
                            'preview_f0': res.get('preview_f0', []),
                            'has_empty_data': res.get('has_empty_data', False)
                        }
                        self.tree_panel.update_item_icon(iid)
                    else:
                        iid = self.tree_panel.tree.insert(gid, tk.END, text=res['word'] + " (缺失)", tags=('item',))
                        self.items[iid] = {'label': res['word'], 'group': res['group'], 'snd': None, 'start': None, 'end': None, 'inner_splits': []}

                self.stop_loading("长音频切分完成")
                self.tree_panel.select_first_item()
                if hasattr(self, 'manual_segments'): self.manual_segments = None
                self._maybe_refresh_formants_after_import()

            self.root.after(0, finalize)
        threading.Thread(target=run, daemon=True).start()

    def load_batch_audio(self):
        if self._has_active_chart_dialog():
            messagebox.showwarning("提示", "图表编辑器已打开，修改图表期间禁止导入音频文件。")
            return
        paths = filedialog.askopenfilenames(filetypes=[("Audio Files", "*.wav *.mp3")])
        if not paths: return
        self.pending_batch_paths = list(paths)
        self.lbl_batch_files.configure(text=f"已选 {len(paths)} 个文件", text_color="#2563EB")
        self.lbl_status.configure(text="独立音频就绪，正在后台分析...", text_color="#10B981")
        self.mark_modified()
        self.start_background_batch_processing(paths)
        if getattr(self, 'switch_unified_wordlist', None) and self.switch_unified_wordlist.get() and getattr(self, 'global_wordlist_text', None):
            self.root.after(100, lambda: self.process_batch_with_wordlist(self.global_wordlist_text, match_mode=getattr(self, 'global_wordlist_match_mode', 'fuzzy')))

    def start_background_batch_processing(self, paths):
        def run():
            params = self._build_worker_params()
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
            params = self._build_worker_params()
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
                        self._stamp_formant_params_on_item(res, params)
                        iid = f"batch_{res['label']}_{id(res)}"
                        self.items[iid] = res
                        has_empty = res.get('has_empty_data', False)
                        img = self.tk_icons.get('warning', '') if has_empty else ''
                        self.tree_panel.tree.insert(gid, tk.END, iid=iid, text=res['label'], tags=('item',), image=img)
                        self.tree_panel.update_item_icon(iid)

                self.set_status(f"批量并行提取完成 ({len(results)}/{total})")
                self.stop_loading()
                self.tree_panel.select_first_item()
                self._maybe_refresh_formants_after_import()

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
            params = self._build_worker_params()
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
                    syls = split_into_syllables(word)
                    cached_label = res.get('label', '')
                    cached_syls = split_into_syllables(cached_label)
                    if len(syls) > 1 and (len(cached_syls) != len(syls) or ('/' in word and cached_label != word)):
                        try:
                            snd = parselmouth.Sound(res['path'])
                            meta = {}
                            p_data = res.get('pitch_data')
                            r_s = res.get('raw_start', res['start'])
                            r_e = res.get('raw_end', res['end'])
                            res['inner_splits'] = auto_split_inner_word(snd, r_s, r_e, len(syls), pitch_data=p_data, output_meta=meta)
                            from modules.audio_core import auto_split_to_chars_bounds
                            res['chars_bounds'] = auto_split_to_chars_bounds(snd, r_s, r_e, res['inner_splits'], len(syls), self.last_params)
                            if res['chars_bounds']:
                                res['start'] = res['chars_bounds'][0][0]
                                res['end'] = res['chars_bounds'][-1][1]
                            res['split_warnings'] = meta.get('split_warnings', [])
                            res['split_confidence'] = meta.get('split_confidence', 1.0)
                            res['has_empty_data'] = res.get('has_empty_data', False)
                        except Exception:
                            res['inner_splits'] = []
                            res['chars_bounds'] = [[res['start'], res['end']]]
                    elif len(syls) <= 1:
                        res['inner_splits'] = []
                        res['chars_bounds'] = [[res['start'], res['end']]]
                        res['split_warnings'] = []
                        res['split_confidence'] = 1.0

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
                        self._stamp_formant_params_on_item(res, params)
                        display = f"{res['label']} ← {os.path.basename(res['path'])}" if match_mode == 'fuzzy' else res['label']
                        iid = f"batch_wl_{res['label']}_{id(res)}"

                        has_empty = res.get('has_empty_data', False)
                        img = self.tk_icons.get('warning', '') if has_empty else ''
                        self.tree_panel.tree.insert(gid, tk.END, iid=iid, text=display, tags=('item',), image=img)

                        self.items[iid] = res
                        res['is_user_specified_structure'] = '/' in res['label']
                        self.tree_panel.update_item_icon(iid)
                        matched_count += 1
                    else:
                        suffix = " (未匹配)" if match_mode == 'fuzzy' else " (缺失)"
                        iid = f"missing_{res['label']}_{id(res)}"
                        self.tree_panel.tree.insert(gid, tk.END, iid=iid, text=res['label'] + suffix, tags=('item',))
                        self.items[iid] = {'label': res['label'], 'group': res['group'], 'snd': None, 'start': None, 'end': None, 'inner_splits': []}

                self.stop_loading(f"并行处理完成: {matched_count}/{total}")
                self.tree_panel.select_first_item()
                self._maybe_refresh_formants_after_import()

            self.root.after(0, finalize)
        threading.Thread(target=run, daemon=True).start()

    def open_text_dialog(self, mode):
        if self._has_active_chart_dialog():
            messagebox.showwarning("提示", "图表编辑器已打开，修改图表期间禁止更改/导入字表。")
            return
        if mode == 'long' and not self.pending_long_snd:
            return messagebox.showwarning("提示", "请先导入一条长音频。")
        if mode == 'batch' and not self.pending_batch_paths:
            return messagebox.showwarning("提示", "请先选择独立音频。")

        dlg = ctk.CTkToplevel(self.root)
        dlg.title("导入字表")
        w, h = 480, (700 if mode == 'batch' else 620)
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

        def load_textgrid():
            if mode == 'batch':
                paths = filedialog.askopenfilenames(filetypes=[("TextGrid Files", "*.TextGrid"), ("All Files", "*.*")])
                if not paths: return
                dlg.destroy()
                self.process_batch_with_textgrid(list(paths))
                return
            path = filedialog.askopenfilename(filetypes=[("TextGrid Files", "*.TextGrid"), ("All Files", "*.*")])
            if not path: return
            try:
                import textgrid
                tg = textgrid.TextGrid.fromFile(path)

                words_tier = None
                chars_tier = None
                groups_tier = None
                for t in tg.tiers:
                    if t.name == "words" and words_tier is None:
                        words_tier = t
                    elif t.name == "chars" and chars_tier is None:
                        chars_tier = t
                    elif t.name in ["groups", "group"] and groups_tier is None:
                        groups_tier = t

                if not words_tier:
                    for t in tg.tiers:
                        if isinstance(t, textgrid.IntervalTier):
                            words_tier = t
                            break

                if not words_tier:
                    return messagebox.showerror("错误", "TextGrid 中没有找到 IntervalTier")

                tg_intervals = []
                for interval in words_tier:
                    lbl = interval.mark.strip()
                    if lbl:
                        grp_name = "导入内容"
                        if groups_tier:
                            center = (interval.minTime + interval.maxTime) / 2.0
                            for g_interval in groups_tier:
                                if g_interval.minTime <= center <= g_interval.maxTime:
                                    g_lbl = g_interval.mark.strip()
                                    if g_lbl:
                                        grp_name = g_lbl
                                        break

                        chars_bounds = []
                        inner_splits = []
                        if chars_tier:
                            overlapping_chars = []
                            for c_interval in chars_tier:
                                c_lbl = c_interval.mark.strip()
                                if c_lbl:
                                    # Use interval center to check overlap and robust tolerance
                                    center = (c_interval.minTime + c_interval.maxTime) / 2.0
                                    if interval.minTime <= center <= interval.maxTime:
                                        overlapping_chars.append(c_interval)

                            overlapping_chars.sort(key=lambda c: c.minTime)
                            if overlapping_chars:
                                for c in overlapping_chars:
                                    chars_bounds.append([c.minTime, c.maxTime])
                                for j in range(len(overlapping_chars) - 1):
                                    inner_splits.append(overlapping_chars[j].maxTime)

                        # Fallback to even splits if chars tier data is missing or empty
                        if not chars_bounds:
                            syls = split_into_syllables(lbl)
                            w_len = len(syls)
                            if w_len > 1:
                                splits = np.linspace(interval.minTime, interval.maxTime, w_len + 1).tolist()
                                chars_bounds = [[splits[j], splits[j+1]] for j in range(w_len)]
                                inner_splits = splits[1:-1]
                            else:
                                chars_bounds = [[interval.minTime, interval.maxTime]]
                                inner_splits = []

                        tg_intervals.append({
                            'start': interval.minTime,
                            'end': interval.maxTime,
                            'label': lbl,
                            'group': grp_name,
                            'inner_splits': inner_splits,
                            'chars_bounds': chars_bounds
                        })

                if not tg_intervals:
                    return messagebox.showerror("错误", "TextGrid 中没有非空标签的区间")

                dlg.destroy()
                self.process_long_with_textgrid(tg_intervals)
            except Exception as e:
                return messagebox.showerror("错误", f"解析 TextGrid 失败: {e}")



        btn_prompt = ctk.CTkButton(toolbar, text=" 复制 AI 整理提示词", image=self.icons.get("copy_white"), compound="left",
                                   width=150, height=28, corner_radius=14, fg_color="#F59E0B", text_color="white",
                                   hover_color="#D97706", command=copy_prompt)
        btn_prompt.pack(side=tk.LEFT, padx=10)

        # 2. 文本输入区
        # 创建一个容器来包裹 Textbox 和 浮动占位符
        text_frame = ctk.CTkFrame(dlg, fg_color="transparent")
        text_frame.pack(padx=20, pady=(10, 5), fill=tk.BOTH, expand=True)

        text_box = ctk.CTkTextbox(text_frame, width=380, height=180, corner_radius=8, border_width=1, border_color="#D1D5DB")
        text_box.pack(fill=tk.BOTH, expand=True)

        placeholder_text = "请在此处粘贴字表文本，或点击下方按钮导入文件。\n\n格式规范：\n1. 组别名称：使用 【】、[] 或 # 开头（如：【一组】）。\n2. 字/词项：组别下方的行即为字词，支持空格、逗号、分号或 Tab 分隔。\n3. 音节切分：可使用斜杠“/”手动分割音节，如 bro/ther 或 北/京。\n\n示例格式：\n【一组】\n妈 麻 马 骂\n#双音节\n北/京  bro/ther\n[三字项]\n录音笔；笔记本"

        # 创建浮动占位符标签
        placeholder_label = ctk.CTkLabel(text_box, text=placeholder_text, text_color="#9CA3AF",
                                         justify=tk.LEFT, font=("Microsoft YaHei", 12), anchor="nw")
        placeholder_label.place(x=10, y=10)

        # 点击占位符时聚焦输入框
        placeholder_label.bind("<Button-1>", lambda e: text_box.focus_set())

        # 3. 实时统计栏
        lbl_stats = ctk.CTkLabel(dlg, text="实时统计：已识别 0 个组别 | 0 个项", text_color="#6B7280", font=("Microsoft YaHei", 12))
        lbl_stats.pack(pady=(0, 5), padx=20, anchor="w")

        # 3.5 规则说明 (折叠面板)
        rule_frame = ctk.CTkFrame(dlg, fg_color="#EFF6FF", corner_radius=8, border_width=1, border_color="#BFDBFE", cursor="hand2")
        rule_frame.pack(padx=20, pady=(5, 5), fill=tk.X)
        rule_title = ctk.CTkLabel(rule_frame, text="▶ 💡 匹配与音节拆分规则说明", text_color="#1E40AF", font=("Microsoft YaHei", 12, "bold"), cursor="hand2")
        rule_title.pack(anchor=tk.W, padx=10, pady=5)
        rule_text = (
            "1. CJK(汉字)匹配：模糊匹配时无视数字、英文与特殊字符，仅比对汉字字符。\n"
            "2. 拉丁字母/非CJK：无视数字与特殊字符，保留英文字母进行比对。\n"
            "3. 多音节拆分：默认汉字逐字拆分，非汉字整体为一个音节。若字词中包含斜杠“/”\n"
            "   (如 bro/ther 或 北/京)，则强制按斜杠分割音节，不再按字拆分。"
        )
        rule_lbl = ctk.CTkLabel(rule_frame, text=rule_text, text_color="#1E40AF", font=("Microsoft YaHei", 11), justify=tk.LEFT)
        
        is_expanded = [False]
        def toggle_rules(event=None):
            if is_expanded[0]:
                rule_lbl.pack_forget()
                rule_title.configure(text="▶ 💡 匹配与音节拆分规则说明")
                rule_title.pack(anchor=tk.W, padx=10, pady=5)
                is_expanded[0] = False
            else:
                rule_title.configure(text="▼ 💡 匹配与音节拆分规则说明")
                rule_title.pack(anchor=tk.W, padx=10, pady=(5, 2))
                rule_lbl.pack(anchor=tk.W, padx=10, pady=(0, 5))
                is_expanded[0] = True

        rule_title.bind("<Button-1>", toggle_rules)
        rule_frame.bind("<Button-1>", toggle_rules)

        def update_stats(event=None):
            raw_text = text_box.get("1.0", tk.END)
            groups, flat_words = parse_wordlist(raw_text)
            color = "#10B981" if flat_words else "#6B7280"
            lbl_stats.configure(text=f"实时统计：已识别 {len(groups)} 个组别 | {len(flat_words)} 个项", text_color=color)

            text_box.tag_remove("group_title", "1.0", tk.END)
            text_box.tag_remove("word_item", "1.0", tk.END)
            text_box.tag_remove("separator", "1.0", tk.END)
            text_box.tag_remove("excluded", "1.0", tk.END)

            # 控制浮动占位符的显示/隐藏
            if not raw_text.strip():
                placeholder_label.place(x=10, y=10)
                lbl_stats.configure(text="实时统计：待输入...", text_color="#6B7280")
                return
            else:
                placeholder_label.place_forget()

            text_box.tag_config("group_title", foreground="#2563EB")
            text_box.tag_config("word_item", foreground="#10B981")
            text_box.tag_config("separator", foreground="#3B82F6")
            text_box.tag_config("excluded", foreground="#9CA3AF")
            text_box.tag_raise("separator")
            text_box.tag_raise("excluded")

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
                            w_start_idx = f"{current_line_idx}.{idx}"
                            w_end_idx = f"{current_line_idx}.{idx+len(w)}"
                            text_box.tag_add("word_item", w_start_idx, w_end_idx)

                            # 细化词内部的字符高亮
                            is_cjk = has_cjk(w)
                            for char_offset, char in enumerate(w):
                                char_pos = f"{current_line_idx}.{idx + char_offset}"
                                char_end_pos = f"{current_line_idx}.{idx + char_offset + 1}"
                                if char == '/':
                                    text_box.tag_add("separator", char_pos, char_end_pos)
                                else:
                                    is_excluded = False
                                    if is_cjk:
                                        # CJK 模式下，非 CJK 字符被排除
                                        if not re.match(r'[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff]', char):
                                            is_excluded = True
                                    else:
                                        # 非 CJK 模式下，非字母被排除
                                        if not re.match(r'[^\W\d_]', char):
                                            is_excluded = True
                                    if is_excluded:
                                        text_box.tag_add("excluded", char_pos, char_end_pos)

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

            self.global_wordlist_text = raw_text
            if mode == 'batch': self.global_wordlist_match_mode = match_mode_var.get()
            dlg.destroy()
            if mode == 'long': self.process_long_with_wordlist(raw_text)
            else: self.process_batch_with_wordlist(raw_text, match_mode=match_mode_var.get())

        # 底部按钮栏
        btn_row = ctk.CTkFrame(dlg, fg_color="transparent")
        btn_row.pack(fill=tk.X, padx=20, pady=(5, 15))

        btn_import_tg = ctk.CTkButton(btn_row, text="导入 .TextGrid",
                                      width=120, height=40, corner_radius=20, fg_color="#8B5CF6", text_color="white",
                                      hover_color="#7C3AED", command=load_textgrid, font=self.font_main)
        btn_import_tg.pack(side=tk.LEFT)

        CTkReleaseButton(btn_row, text="开始匹配提取", command=process, corner_radius=20, height=40, font=self.font_main).pack(side=tk.RIGHT)

        # 初始触发一次统计
        update_stats()

        self.active_import_dlg = dlg
        self.active_import_textbox = text_box
        self.active_import_update_stats = update_stats
        self.active_import_mode = mode

        try:
            import windnd
            windnd.hook_dropfiles(dlg, func=lambda files: self.drop_queue.put(('dlg', files)))
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"Failed to hook dropfiles on import dialog: {e}")

    def process_batch_with_textgrid(self, tg_paths):
        if not self.pending_batch_paths:
            return messagebox.showwarning("提示", "请先选择多个音频文件。")
        if not tg_paths: return

        def run():
            self.root.after(0, lambda: self.start_loading("正在并行匹配并分析 TextGrid..."))
            self.root.after(0, self.tree_panel.clear_all)

            audio_paths = self.pending_batch_paths

            import re
            def natural_sort_key(s):
                return [int(text) if text.isdigit() else text.lower()
                        for text in re.split('([0-9]+)', s)]

            sorted_audios = sorted(audio_paths, key=natural_sort_key)
            sorted_tgs = sorted(tg_paths, key=natural_sort_key)

            matched_audio_to_tg = {}
            matched_tg_to_audio = {}

            # 1. Exact match
            tg_base_map = {os.path.splitext(os.path.basename(tp))[0].lower(): tp for tp in sorted_tgs}
            for ap in sorted_audios:
                abase = os.path.splitext(os.path.basename(ap))[0].lower()
                if abase in tg_base_map:
                    tp = tg_base_map[abase]
                    matched_audio_to_tg[ap] = tp
                    matched_tg_to_audio[tp] = ap

            # 2. Fuzzy match
            for ap in sorted_audios:
                if ap in matched_audio_to_tg: continue
                abase = os.path.splitext(os.path.basename(ap))[0].lower()
                for tp in sorted_tgs:
                    if tp in matched_tg_to_audio: continue
                    tbase = os.path.splitext(os.path.basename(tp))[0].lower()
                    if abase in tbase or tbase in abase:
                        matched_audio_to_tg[ap] = tp
                        matched_tg_to_audio[tp] = ap
                        break

            # 3. Order match
            remaining_audios = [ap for ap in sorted_audios if ap not in matched_audio_to_tg]
            remaining_tgs = [tp for tp in sorted_tgs if tp not in matched_tg_to_audio]
            for ap, tp in zip(remaining_audios, remaining_tgs):
                matched_audio_to_tg[ap] = tp
                matched_tg_to_audio[tp] = ap

            total = len(sorted_audios)
            results = [None] * total

            params = self._build_worker_params()
            trim = self.switch_trim_silence.get()

            futures = {}
            for i, ap in enumerate(sorted_audios):
                tp = matched_audio_to_tg.get(ap)
                if tp:
                    futures[self.executor.submit(batch_process_worker_with_textgrid, ap, tp, params, trim)] = i
                else:
                    word_lbl = os.path.splitext(os.path.basename(ap))[0]
                    futures[self.executor.submit(batch_process_worker, ap, params, trim, word_lbl)] = i

            total_futures = len(futures) if futures else 1
            done_count = 0
            if futures:
                for future in concurrent.futures.as_completed(futures):
                    orig_idx = futures[future]
                    try:
                        res = future.result()
                        results[orig_idx] = res
                    except Exception as e:
                        ap = sorted_audios[orig_idx]
                        lbl = os.path.splitext(os.path.basename(ap))[0]
                        results[orig_idx] = {'label': lbl, 'group': '导入内容', 'success': False, 'error': str(e), 'path': ap}
                    done_count += 1
                    self.root.after(0, lambda v=done_count/total_futures: self.set_progress(v))
            else:
                self.root.after(0, lambda: self.set_progress(1.0))

            def finalize():
                self.tree_panel.clear_all()
                self.items.clear()
                matched_count = 0

                unique_groups = []
                for res in results:
                    if res:
                        g = res.get('group', '导入内容')
                        if g not in unique_groups:
                            unique_groups.append(g)

                for g in unique_groups:
                    if g not in self.tree_panel.project_groups:
                        self.tree_panel.project_groups.append(g)
                    iid_grp = f"group_{g}"
                    self.tree_panel.tree.insert("", tk.END, iid=iid_grp, text=g, tags=('group',), open=True)
                    self.tree_panel.group_nodes[g] = iid_grp

                for res in results:
                    if not res: continue
                    grp_name = res.get('group', '导入内容')
                    gid = self.tree_panel.group_nodes.get(grp_name)
                    if res.get('success'):
                        res['group'] = grp_name
                        if 'pitch_floor' not in res:
                            res['pitch_floor'] = params['pitch_floor']
                            res['pitch_ceiling'] = params['pitch_ceiling']
                            res['voicing_threshold'] = params['voicing_threshold']
                        self._stamp_formant_params_on_item(res, params)

                        tp = matched_audio_to_tg.get(res['path'])
                        if tp:
                            display = f"{res['label']} ← {os.path.basename(tp)}"
                        else:
                            display = res['label']

                        iid = f"batch_tg_{res['label']}_{id(res)}"
                        has_empty = res.get('has_empty_data', False)
                        img = self.tk_icons.get('warning', '') if has_empty else ''
                        self.tree_panel.tree.insert(gid, tk.END, iid=iid, text=display, tags=('item',), image=img)
                        self.items[iid] = res
                        self.tree_panel.update_item_icon(iid)
                        matched_count += 1
                    else:
                        iid = f"missing_{res['label']}_{id(res)}"
                        self.tree_panel.tree.insert(gid, tk.END, iid=iid, text=res['label'] + " (失败)", tags=('item',))
                        self.items[iid] = {'label': res['label'], 'group': grp_name, 'snd': None, 'start': None, 'end': None, 'inner_splits': []}

                self.stop_loading(f"TextGrid 导入并匹配完成: {matched_count}/{total}")
                self.tree_panel.select_first_item()
                self._maybe_refresh_formants_after_import()

            self.root.after(0, finalize)

        threading.Thread(target=run, daemon=True).start()

    def process_long_with_textgrid(self, tg_intervals):
        if not tg_intervals: return

        def run():
            self.root.after(0, lambda: self.start_loading("正在并行分析 TextGrid 音段..."))
            self.root.after(0, self.tree_panel.clear_all)

            snd = self.pending_long_snd
            # Compute global pitch once
            global_pitch = snd.to_pitch_ac(time_step=None, pitch_floor=self.last_params['pitch_floor'], pitch_ceiling=self.last_params['pitch_ceiling'], voicing_threshold=self.last_params.get('voicing_threshold', 0.25), very_accurate=True, octave_jump_cost=0.9)

            total = len(tg_intervals)
            results = []

            params = self._build_worker_params()
            trim = self.switch_trim_silence.get()
            pitch_xs = global_pitch.xs()
            pitch_freqs = global_pitch.selected_array['frequency']

            tasks = []
            for item in tg_intervals:
                ms = item['start']
                me = item['end']
                word = item['label']
                grp_name = item.get('group', '导入内容')
                ref_splits = item.get('inner_splits', [])

                valid_ms = max(0, ms)
                valid_me = min(snd.get_total_duration(), me)

                if valid_me > valid_ms:
                    part = snd.extract_part(from_time=valid_ms, to_time=valid_me)
                    snd_values = part.values
                    snd_sf = part.sampling_frequency

                    import numpy as np
                    idx_start = np.searchsorted(pitch_xs, valid_ms)
                    idx_end = np.searchsorted(pitch_xs, valid_me)
                    sliced_xs = pitch_xs[idx_start:idx_end]
                    sliced_freqs = pitch_freqs[idx_start:idx_end]

                    tasks.append({
                        'word': word, 'group': grp_name, 'ms': ms, 'me': me,
                        'snd_values': snd_values, 'snd_sf': snd_sf,
                        'sliced_xs': sliced_xs, 'sliced_freqs': sliced_freqs,
                        'ref_splits': ref_splits,
                        'missing': False
                    })
                else:
                    tasks.append({'word': word, 'group': grp_name, 'missing': True})

            import concurrent.futures
            from modules.audio_core import process_single_long_word

            with concurrent.futures.ThreadPoolExecutor(max_workers=min(os.cpu_count() or 4, 8)) as executor:
                futures = {}
                for i, t in enumerate(tasks):
                    if t.get('missing'):
                        results.append({'label': t['word'], 'group': t['group'], 'success': False, 'missing': True})
                        continue

                    future = executor.submit(
                        process_single_long_word,
                        t['snd_values'], t['snd_sf'], t['word'], t['ms'], t['me'],
                        params, trim, t['sliced_xs'], t['sliced_freqs'], t['ref_splits']
                    )
                    futures[future] = (i, t['group'], t['word'])

                temp_results = [None] * len(tasks)
                for future in concurrent.futures.as_completed(futures):
                    idx, grp, w = futures[future]
                    try:
                        res = future.result()
                        res['group'] = grp
                        res['missing'] = False
                        temp_results[idx] = res
                    except Exception as e:
                        import logging
                        logging.getLogger(__name__).error(f"处理 '{w}' 时出错: {e}", exc_info=True)
                        temp_results[idx] = {'label': w, 'group': grp, 'success': False, 'missing': True}

            # Override macro_segments for next workflow
            self.current_macro_segments = [(t['ms'], t['me']) for t in tasks if not t.get('missing')]

            def finalize():
                self.tree_panel.clear_all()
                self.items.clear()
                matched_count = 0

                # Pre-register the groups from the tasks
                unique_groups = []
                for t in tasks:
                    g = t.get('group', '导入内容')
                    if g not in unique_groups:
                        unique_groups.append(g)

                for g in unique_groups:
                    if g not in self.tree_panel.project_groups:
                        self.tree_panel.project_groups.append(g)
                    iid_grp = f"group_{g}"
                    self.tree_panel.tree.insert("", tk.END, iid=iid_grp, text=g, tags=('group',), open=True)
                    self.tree_panel.group_nodes[g] = iid_grp

                for idx, res in enumerate(temp_results):
                    gid = self.tree_panel.group_nodes.get(res['group'])
                    if res and res.get('success'):
                        iid = f"item_{res['label']}_{id(res)}"
                        res['snd'] = self.pending_long_snd
                        res['pitch'] = global_pitch
                        res['pitch_floor'] = params['pitch_floor']
                        res['pitch_ceiling'] = params['pitch_ceiling']
                        res['voicing_threshold'] = params['voicing_threshold']
                        self._stamp_formant_params_on_item(res, params)

                        self.tree_panel.tree.insert(gid, tk.END, iid=iid, text=res['label'], tags=('item',))
                        self.items[iid] = res
                        self.tree_panel.update_item_icon(iid)
                        matched_count += 1
                    else:
                        iid = f"missing_{res['label']}_{id(res)}"
                        self.tree_panel.tree.insert(gid, tk.END, iid=iid, text=res['label'] + " (失败)", tags=('item',))
                        self.items[iid] = {'label': res['label'], 'group': res['group'], 'snd': None, 'start': None, 'end': None, 'inner_splits': []}

                self.stop_loading(f"TextGrid 导入完成: {matched_count}/{total}")
                self.tree_panel.select_first_item()
                self._maybe_refresh_formants_after_import()

            self.root.after(0, finalize)

        import threading
        threading.Thread(target=run, daemon=True).start()

    # --- 工程管理回调 ---
    def is_project_empty(self):
        speakers = self.speaker_manager.get_all_speakers()
        if len(speakers) > 1:
            return False
        if len(speakers) == 1:
            spk = speakers[0]
            if spk.name not in ("发音人 1", "发音人1"):
                return False
            if spk.items:
                return False
            if getattr(spk, 'long_audio_path', None) or getattr(spk, 'pending_batch_paths', None):
                return False
        return True

    def on_import_project(self):
        if self._has_active_chart_dialog():
            messagebox.showwarning("提示", "图表编辑器已打开，修改图表期间禁止导入新工程。")
            return
        path = filedialog.askopenfilename(filetypes=[("PhonTracer Project", "*.teproj *.zip")])
        if not path: return
        
        overlay = False
        if not self.is_project_empty():
            ans = messagebox.askyesnocancel(
                "导入项目",
                "当前已打开一个项目，是否以【叠加】方式导入新项目？\n\n"
                "- 点击【是】：叠加导入，将新项目的数据合并到当前项目中\n"
                "- 点击【否】：覆盖导入，清除当前项目并完全载入新项目\n"
                "- 点击【取消】：取消本次导入"
            )
            if ans is None:
                return
            overlay = ans

        self._last_imported_path = path
        self._last_import_was_overlay = overlay
        self.start_loading("正在导入工程...")

        def run():
            success = self.project_manager.load_project(path, overlay=overlay)
            self.root.after(0, self.stop_loading)
            if success:
                self.root.after(0, self._sync_ui_after_project_load)
        import threading
        threading.Thread(target=run, daemon=True).start()

    def _sync_ui_after_project_load(self):
        self._update_speaker_dropdown()
        spk = self.active_speaker
        self.speaker_option_var.set(spk.name)
        self.tabview.set(spk.tab_mode)

        if spk.tab_mode == "多条独立音频":
            cnt = len(spk.pending_batch_paths)
            if cnt > 0:
                self.lbl_batch_files.configure(text=f"已选 {cnt} 个文件", text_color="#2563EB")
            else:
                self.lbl_batch_files.configure(text="未选择", text_color="#6B7280")
        else:
            if getattr(spk, 'long_audio_path', None):
                self.lbl_long_file.configure(text=os.path.basename(spk.long_audio_path), text_color="#2563EB")
            else:
                self.lbl_long_file.configure(text="未选择", text_color="#6B7280")

        self.spectrogram_panel.clear_canvas()
        self._refresh_ui_for_speaker()
        
        is_overlay = getattr(self, '_last_import_was_overlay', False)
        if is_overlay:
            self.current_project_path = None
            self.has_changes = True
        else:
            self.current_project_path = getattr(self, '_last_imported_path', None)
            self.has_changes = False
            
        messagebox.showinfo("成功", "工程导入成功！")

    def on_export_project(self):
        import datetime
        spk_name = self.active_speaker.name if getattr(self, 'active_speaker', None) else "发音人1"
        date_str = datetime.datetime.now().strftime("%m%d-%H%M")
        default_filename = f"{spk_name}_{date_str}"
        path = filedialog.asksaveasfilename(
            initialfile=default_filename,
            defaultextension=".teproj",
            filetypes=[("PhonTracer Project", "*.teproj")]
        )
        if not path: return
        self._last_exported_path = path
        self.start_loading("正在导出工程...")

        def run():
            success = self.project_manager.export_project(path)
            self.root.after(0, self.stop_loading)
            if success:
                def on_success():
                    self.has_changes = False
                    self.current_project_path = getattr(self, '_last_exported_path', None)
                    messagebox.showinfo("成功", "工程已成功导出！")
                self.root.after(0, on_success)
        import threading
        threading.Thread(target=run, daemon=True).start()

    def on_auto_save_toggled(self, is_enabled):
        self.project_manager.auto_save_enabled = bool(is_enabled)
        if is_enabled:
            self.project_manager.trigger_auto_save()
        else:
            self.project_manager.cancel_auto_save()

    def check_update(self, is_manual=False):
        """检查程序更新"""
        from modules.updater import check_for_updates_async
        from modules.update_dialog import UpdateDialog

        if is_manual:
            self.start_loading("正在检查更新...")

        def on_update_found(info):
            if is_manual:
                self.stop_loading()
            UpdateDialog(self.root, info, is_manual=is_manual)

        def on_no_update():
            if is_manual:
                self.stop_loading()
                messagebox.showinfo("提示", "当前已是最新版本！", parent=self.root)

        def on_error(msg):
            if is_manual:
                self.stop_loading()
                messagebox.showerror("检查更新失败", msg, parent=self.root)

        check_for_updates_async(
            self.root,
            on_update_found=on_update_found,
            on_no_update=on_no_update,
            on_error=on_error,
            is_manual=is_manual
        )

    def open_about_dialog(self):
        """打开关于页面弹窗"""
        from modules.about_dialog import AboutDialog
        AboutDialog(self.root, self.check_update)

    def on_detect_f0_clicked(self):
        if not self.items:
            messagebox.showwarning("提示", "请先导入音频文件以进行检测。")
            return

        self.start_loading("正在估算发音人 F0 分布...")

        # 在主线程中捕获 UI 和 Tkinter 状态，保证线程安全
        tab_mode = self.tabview.get()
        pending_long_snd = self.pending_long_snd
        long_audio_path = self.long_audio_path
        
        # 快照常规属性，避免工作线程直接读取或操作主线程的 self.items 数据字典
        items_snapshot = []
        for item in self.items.values():
            items_snapshot.append({
                'macro_start': item.get('macro_start'),
                'macro_end': item.get('macro_end'),
                'snd': item.get('snd'),
                'path': item.get('path'),
                'label': item.get('label')
            })

        params_temp = {
            'f0_engine': self.last_params.get('f0_engine', 'praat'),
            'pitch_floor': 50,
            'pitch_ceiling': 700,
            'voicing_threshold': self.last_params.get('voicing_threshold', 0.25)
        }

        def run_detection():
            try:
                import parselmouth
                from modules.audio_core import extract_f0

                all_stable_f0 = []

                if tab_mode == "单条长音频":
                    snd = pending_long_snd
                    if snd is None and long_audio_path and os.path.exists(long_audio_path):
                        snd = parselmouth.Sound(long_audio_path)
                    if snd is None:
                        self.root.after(0, self.stop_loading)
                        self.root.after(0, lambda: messagebox.showwarning("提示", "未找到已加载的长音频。"))
                        return

                    # 提取整段长音频的 F0
                    pitch_data = extract_f0(snd, params_temp)
                    times = pitch_data['xs']
                    freqs = pitch_data['freqs']

                    for item in items_snapshot:
                        macro_start = item.get('macro_start')
                        macro_end = item.get('macro_end')
                        # [P2 优化]：如果没有真实的 macro_start 和 macro_end，说明是占位项/缺失项，在长音频模式下应当跳过
                        if macro_start is None or macro_end is None:
                            continue

                        mask = (times >= macro_start) & (times <= macro_end)
                        item_times = times[mask]
                        item_freqs = freqs[mask]
                        stable_f0 = self.extract_stable_f0_values(item_times, item_freqs)
                        all_stable_f0.extend(stable_f0)

                elif tab_mode == "多条独立音频":
                    # [P2 优化]：在快照中提取有效的独立音频项
                    valid_items = [it for it in items_snapshot if it.get('snd') or (it.get('path') and os.path.exists(it['path']))]
                    if not valid_items:
                        self.root.after(0, self.stop_loading)
                        self.root.after(0, lambda: messagebox.showwarning("提示", "没有有效的独立音频文件。"))
                        return

                    total_items = len(valid_items)
                    for i, item in enumerate(valid_items):
                        self.root.after(0, lambda v=((i + 1) / total_items): self.set_progress(v))
                        # [P3 优化]：精确捕获 idx = i + 1 并在 lambda 默认参数中暂存，以防延迟回调展示错误索引
                        self.root.after(0, lambda name=item['label'], idx=i+1: self.set_status(f"正在分析 F0 ({idx}/{total_items}): {name}...", "#3B82F6", "status_loading"))

                        item_snd = item.get('snd')
                        if item_snd is None:
                            try:
                                item_snd = parselmouth.Sound(item['path'])
                            except Exception:
                                continue

                        try:
                            pitch_data = extract_f0(item_snd, params_temp)
                            stable_f0 = self.extract_stable_f0_values(pitch_data['xs'], pitch_data['freqs'])
                            all_stable_f0.extend(stable_f0)
                        except Exception:
                            continue

                # 判断有效有声帧是否足够（例如，每帧 10ms，至少需 50 帧，即 0.5 秒）
                if len(all_stable_f0) < 50:
                    self.root.after(0, self.stop_loading)
                    self.root.after(0, lambda: messagebox.showwarning("无法可靠建议", "有效有声数据太少，无法进行可靠建议。请先导入更多音频，或确保当前音频包含足够稳定发音段。"))
                    return

                # 计算百分位数
                p5 = float(np.percentile(all_stable_f0, 5))
                p10 = float(np.percentile(all_stable_f0, 10))
                p50 = float(np.percentile(all_stable_f0, 50))
                p90 = float(np.percentile(all_stable_f0, 90))
                p95 = float(np.percentile(all_stable_f0, 95))

                # 基于 P50 (中位数) 决定插值权重
                med = p50
                w = (med - 120.0) / 120.0
                w = max(0.0, min(1.0, w))

                # 插值系数
                mult_cons_floor = 0.66 * (1.0 - w) + 0.58 * w
                mult_cons_ceil = 1.94 * (1.0 - w) + 1.61 * w

                mult_reco_floor = 0.78 * (1.0 - w) + 0.76 * w
                mult_reco_ceil = 1.67 * (1.0 - w) + 1.45 * w

                mult_fine_floor = 0.89 * (1.0 - w) + 0.88 * w
                mult_fine_ceil = 1.44 * (1.0 - w) + 1.35 * w

                # 计算建议范围
                cons_floor = p5 * mult_cons_floor
                cons_ceil = p95 * mult_cons_ceil

                reco_floor = p5 * mult_reco_floor
                reco_ceil = p95 * mult_reco_ceil

                fine_floor = p5 * mult_fine_floor
                fine_ceil = p95 * mult_fine_ceil

                # 四舍五入到 5 Hz 和 10 Hz
                def round_to_nearest(val, base):
                    return int(round(val / base) * base)

                cons_floor = max(40, round_to_nearest(cons_floor, 5))
                cons_ceil = min(1000, round_to_nearest(cons_ceil, 10))

                reco_floor = max(40, round_to_nearest(reco_floor, 5))
                reco_ceil = min(1000, round_to_nearest(reco_ceil, 10))

                fine_floor = max(40, round_to_nearest(fine_floor, 5))
                fine_ceil = min(1000, round_to_nearest(fine_ceil, 10))

                # 估算稳定的有声时长 (dt 估计为 10ms = 0.01s)
                voiced_duration = len(all_stable_f0) * 0.01

                def show_result_dialog():
                    self.stop_loading()
                    from modules.f0_detection_dialog import F0DetectionDialog
                    F0DetectionDialog(
                        parent=self.root,
                        app=self,
                        p5=p5,
                        p10=p10,
                        p50=p50,
                        p90=p90,
                        p95=p95,
                        stable_count=len(all_stable_f0),
                        stable_duration=voiced_duration,
                        cons_range=(cons_floor, cons_ceil),
                        reco_range=(reco_floor, reco_ceil),
                        fine_range=(fine_floor, fine_ceil)
                    )

                self.root.after(0, show_result_dialog)

            except Exception as e:
                self.root.after(0, self.stop_loading)
                self.root.after(0, lambda: messagebox.showerror("错误", f"检测过程中发生错误: {e}"))

        import threading
        threading.Thread(target=run_detection, daemon=True).start()

    def extract_stable_f0_values(self, xs, freqs):
        if len(xs) < 2:
            return []

        dt = xs[1] - xs[0]
        if dt <= 0:
            dt = 0.010

        # 1. 查找连续的有声帧 (freq > 0)
        voiced_runs = []
        current_run = []
        for i in range(len(freqs)):
            if freqs[i] > 0:
                current_run.append((xs[i], freqs[i]))
            else:
                if current_run:
                    voiced_runs.append(current_run)
                    current_run = []
        if current_run:
            voiced_runs.append(current_run)

        stable_values = []
        for run in voiced_runs:
            if len(run) < 2:
                continue

            # 2. 对每个有声片段，如果相邻帧 of F0 突变过大（相对跳变 > 20%），在跳变处切断
            sub_runs = []
            current_sub = [run[0]]
            for i in range(1, len(run)):
                f_prev = run[i-1][1]
                f_curr = run[i][1]
                if abs(f_curr - f_prev) / f_prev > 0.20:
                    if current_sub:
                        sub_runs.append(current_sub)
                    current_sub = [run[i]]
                else:
                    current_sub.append(run[i])
            if current_sub:
                sub_runs.append(current_sub)

            # 3. 对子片段进行时间筛选和边界裁剪
            for sub in sub_runs:
                sub_duration = len(sub) * dt
                if sub_duration < 0.10:
                    continue

                # 边界剔除：从首尾各剔除 30ms 的数据，以消除发音边界过渡带来的基频不稳/追踪错误
                trim_frames = int(round(0.030 / dt))
                if trim_frames < 1:
                    trim_frames = 1

                if len(sub) > 2 * trim_frames:
                    trimmed_sub = sub[trim_frames:-trim_frames]
                    for item in trimmed_sub:
                        stable_values.append(item[1])
        return stable_values

    def apply_f0_bounds(self, floor, ceiling):
        self.entry_pitch_floor.delete(0, tk.END)
        self.entry_pitch_floor.insert(0, str(int(floor)))
        if hasattr(self.entry_pitch_floor, '_last_val'):
            self.entry_pitch_floor._last_val = str(int(floor))

        self.entry_pitch_ceiling.delete(0, tk.END)
        self.entry_pitch_ceiling.insert(0, str(int(ceiling)))
        if hasattr(self.entry_pitch_ceiling, '_last_val'):
            self.entry_pitch_ceiling._last_val = str(int(ceiling))

        # [P1 优化]：触发参数变化逻辑，并传入 only_pitch_changed=True 以保护所有非手动切分边界不被重写
        self.on_param_change(recalculate_current=False)
        self.recalculate_all_audio(recompute_pitch=True, only_pitch_changed=True)

    def on_detect_formant_clicked(self):
        if not self.items:
            messagebox.showwarning("提示", "请先导入音频文件以进行检测。")
            return

        self.start_loading("正在分析共振峰最佳参数...")

        tab_mode = self.tabview.get()
        pending_long_snd = self.pending_long_snd
        long_audio_path = self.long_audio_path

        items_snapshot = []
        for item in self.items.values():
            items_snapshot.append({
                'macro_start': item.get('macro_start'),
                'macro_end': item.get('macro_end'),
                'snd': item.get('snd'),
                'path': item.get('path'),
                'label': item.get('label'),
                'start': item.get('start'),
                'end': item.get('end')
            })

        def run_detection():
            try:
                import parselmouth
                import os
                import numpy as np

                # 1. Collect snippets
                snippets = []
                if tab_mode == "单条长音频":
                    snd = pending_long_snd
                    if snd is None and long_audio_path and os.path.exists(long_audio_path):
                        snd = parselmouth.Sound(long_audio_path)
                    if snd:
                        for item in items_snapshot:
                            m_s = item.get('macro_start')
                            m_e = item.get('macro_end')
                            if m_s is not None and m_e is not None and m_e > m_s + 0.06:
                                try:
                                    part = snd.extract_part(from_time=m_s + 0.025, to_time=m_e - 0.025)
                                    snippets.append(part)
                                except Exception:
                                    pass
                else: # "多条独立音频"
                    for item in items_snapshot:
                        item_snd = item.get('snd')
                        if item_snd is None and item.get('path') and os.path.exists(item['path']):
                            try:
                                item_snd = parselmouth.Sound(item['path'])
                            except Exception:
                                pass
                        if item_snd:
                            t_s = item.get('start', 0.0)
                            t_e = item.get('end', item_snd.get_total_duration())
                            if t_e > t_s + 0.06:
                                try:
                                    part = item_snd.extract_part(from_time=t_s + 0.025, to_time=t_e - 0.025)
                                    snippets.append(part)
                                except Exception:
                                    pass
                            else:
                                try:
                                    snippets.append(item_snd)
                                except Exception:
                                    pass

                if not snippets:
                    self.root.after(0, self.stop_loading)
                    self.root.after(0, lambda: messagebox.showwarning("提示", "没有有效发音片段可进行分析。"))
                    return

                # Sample at most 20 snippets to guarantee performance
                if len(snippets) > 20:
                    indices = np.linspace(0, len(snippets) - 1, 20, dtype=int)
                    snippets = [snippets[idx] for idx in indices]

                # 2. Scoring Helper
                def score_config(formant_max_hz, window_length, pre_emphasis):
                    total_frames = 0
                    valid_frames = 0

                    continuity_scores = []
                    gap_scores = []
                    range_scores = []
                    fragmentation_scores = []
                    edge_scores = []

                    all_valid_f1 = []
                    all_valid_f2 = []

                    def clipped_score(value, low, high):
                        if high <= low:
                            return 0.0
                        return float(np.clip((value - low) / (high - low), 0.0, 1.0))

                    def adjacent_jump_rate(xs_arr, vals, threshold):
                        vals = np.asarray(vals, dtype=float)
                        xs_arr = np.asarray(xs_arr, dtype=float)
                        valid = np.isfinite(vals)
                        if np.sum(valid) < 3:
                            return 1.0
                        idx = np.where(valid)[0]
                        jump_count = 0
                        pair_count = 0
                        for left, right in zip(idx[:-1], idx[1:]):
                            if xs_arr[right] - xs_arr[left] > 0.055:
                                continue
                            pair_count += 1
                            if abs(vals[right] - vals[left]) > threshold:
                                jump_count += 1
                        if pair_count == 0:
                            return 1.0
                        return jump_count / pair_count

                    def fragmentation_penalty(valid_mask):
                        if len(valid_mask) == 0:
                            return 1.0
                        runs = []
                        run_len = 0
                        for flag in valid_mask:
                            if flag:
                                run_len += 1
                            elif run_len:
                                runs.append(run_len)
                                run_len = 0
                        if run_len:
                            runs.append(run_len)
                        if not runs:
                            return 1.0
                        short = sum(r for r in runs if r < 4)
                        return short / max(1, sum(runs))

                    for snd_part in snippets:
                        try:
                            formant = snd_part.to_formant_burg(
                                time_step=None,
                                max_number_of_formants=5,
                                maximum_formant=formant_max_hz,
                                window_length=window_length,
                                pre_emphasis_from=pre_emphasis
                            )
                        except Exception:
                            continue

                        xs = formant.xs()
                        if len(xs) == 0:
                            continue

                        f1_vals = []
                        f2_vals = []
                        f3_vals = []
                        for t in xs:
                            f1_vals.append(formant.get_value_at_time(1, t))
                            f2_vals.append(formant.get_value_at_time(2, t))
                            f3_vals.append(formant.get_value_at_time(3, t))

                        f1 = np.array(f1_vals)
                        f2 = np.array(f2_vals)
                        f3 = np.array(f3_vals)

                        gap = f2 - f1
                        valid_mask = (
                            np.isfinite(f1) & np.isfinite(f2) &
                            (f1 >= 90.0) & (f1 <= 1300.0) &
                            (f2 >= 350.0) & (f2 <= min(float(formant_max_hz) * 0.96, 4200.0)) &
                            (gap >= 120.0)
                        )
                        total_frames += len(xs)
                        valid_frames += np.sum(valid_mask)

                        if np.sum(valid_mask) < 4:
                            continue

                        vf1 = f1[valid_mask]
                        vf2 = f2[valid_mask]
                        vf3 = f3[valid_mask]

                        all_valid_f1.extend(vf1.tolist())
                        all_valid_f2.extend(vf2.tolist())

                        valid_f1_series = np.where(valid_mask, f1, np.nan)
                        valid_f2_series = np.where(valid_mask, f2, np.nan)
                        f1_jump_threshold = max(150.0, 0.28 * np.nanmedian(vf1))
                        f2_jump_threshold = max(260.0, 0.20 * np.nanmedian(vf2))
                        f1_jump_rate = adjacent_jump_rate(xs, valid_f1_series, f1_jump_threshold)
                        f2_jump_rate = adjacent_jump_rate(xs, valid_f2_series, f2_jump_threshold)
                        continuity_scores.append(1.0 - min(1.0, 3.0 * (0.45 * f1_jump_rate + 0.55 * f2_jump_rate)))

                        diff = vf2 - vf1
                        diff_p10 = np.percentile(diff, 10)
                        diff_cv = np.std(diff) / np.mean(diff) if np.mean(diff) > 0 else 1.0
                        gap_score = 0.65 * clipped_score(diff_p10, 140.0, 450.0) + 0.35 * (1.0 - min(1.0, diff_cv / 0.85))
                        gap_scores.append(gap_score)

                        f1_p95 = np.percentile(vf1, 95)
                        f2_p99 = np.percentile(vf2, 99)
                        f1_range_penalty = clipped_score(f1_p95, 1050.0, 1450.0)
                        f2_range_penalty = clipped_score(f2_p99, 3300.0, 4300.0)
                        range_scores.append(1.0 - min(1.0, 0.55 * f1_range_penalty + 0.45 * f2_range_penalty))

                        fragmentation_scores.append(1.0 - fragmentation_penalty(valid_mask))

                        near_edge_f2 = np.sum(vf2 > 0.93 * formant_max_hz) / len(vf2)
                        vf3_clean = vf3[np.isfinite(vf3)]
                        near_edge_f3 = np.sum(vf3_clean > 0.93 * formant_max_hz) / len(vf3_clean) if len(vf3_clean) > 0 else 0.0
                        edge_scores.append(1.0 - min(1.0, 0.7 * near_edge_f2 + 0.3 * near_edge_f3))

                    if total_frames == 0:
                        return 0.0, 0.0, np.nan, np.nan

                    valid_rate = valid_frames / total_frames
                    coverage_score = min(1.0, valid_rate / 0.82)
                    continuity_score = np.mean(continuity_scores) if continuity_scores else 0.0
                    gap_score = np.mean(gap_scores) if gap_scores else 0.0
                    range_score = np.mean(range_scores) if range_scores else 0.0
                    fragmentation_score = np.mean(fragmentation_scores) if fragmentation_scores else 0.0
                    edge_score = np.mean(edge_scores) if edge_scores else 0.0

                    quality_score = (
                        0.22 * coverage_score +
                        0.30 * continuity_score +
                        0.22 * gap_score +
                        0.12 * range_score +
                        0.08 * fragmentation_score +
                        0.06 * edge_score
                    )

                    if valid_rate < 0.25:
                        quality_score *= 0.55

                    # Prefer a lower ceiling when the observed F1/F2 quality is effectively tied.
                    quality_score *= (1.0 - max(0.0, formant_max_hz - 5000.0) / 30000.0)

                    median_f1 = np.median(all_valid_f1) if all_valid_f1 else np.nan
                    median_f2 = np.median(all_valid_f2) if all_valid_f2 else np.nan

                    return quality_score, valid_rate, median_f1, median_f2

                # 3. Coarse search
                coarse_candidates = [3500, 4000, 4500, 5000, 5500, 6000, 6500]
                coarse_results = {}
                for h in coarse_candidates:
                    score, vr, m_f1, m_f2 = score_config(h, 0.025, 50)
                    coarse_results[h] = {
                        'score': score,
                        'valid_rate': vr,
                        'med_f1': m_f1,
                        'med_f2': m_f2
                    }

                # Apply consistency drift penalty
                final_coarse_scores = {}
                for i, h in enumerate(coarse_candidates):
                    res = coarse_results[h]
                    score = res['score']
                    med_f1 = res['med_f1']
                    med_f2 = res['med_f2']
                    drift_penalty = 1.0

                    if i > 0:
                        left_res = coarse_results[coarse_candidates[i-1]]
                        if np.isfinite(med_f1) and np.isfinite(left_res['med_f1']) and abs(med_f1 - left_res['med_f1']) > 100:
                            drift_penalty *= 0.85
                        if np.isfinite(med_f2) and np.isfinite(left_res['med_f2']) and abs(med_f2 - left_res['med_f2']) > 200:
                            drift_penalty *= 0.85

                    if i < len(coarse_candidates) - 1:
                        right_res = coarse_results[coarse_candidates[i+1]]
                        if np.isfinite(med_f1) and np.isfinite(right_res['med_f1']) and abs(med_f1 - right_res['med_f1']) > 100:
                            drift_penalty *= 0.85
                        if np.isfinite(med_f2) and np.isfinite(right_res['med_f2']) and abs(med_f2 - right_res['med_f2']) > 200:
                            drift_penalty *= 0.85

                    final_coarse_scores[h] = score * drift_penalty

                best_coarse_h = max(coarse_candidates, key=lambda h: final_coarse_scores[h])

                # 4. Fine search
                fine_h_candidates = [
                    best_coarse_h - 500,
                    best_coarse_h - 250,
                    best_coarse_h,
                    best_coarse_h + 250,
                    best_coarse_h + 500
                ]
                fine_h_candidates = [h for h in fine_h_candidates if 3000 <= h <= 7000]

                win_candidates = [0.020, 0.025, 0.030, 0.035]
                pre_candidates = [50, 80, 100]

                best_score = -1.0
                best_config = None
                all_fine_results = []

                total_steps = len(fine_h_candidates) * len(win_candidates) * len(pre_candidates)
                step_idx = 0

                for h in fine_h_candidates:
                    for win in win_candidates:
                        for pre in pre_candidates:
                            score, vr, _, _ = score_config(h, win, pre)
                            all_fine_results.append((h, win, pre, score))
                            if score > best_score:
                                best_score = score
                                best_config = (h, 5, win, pre, score)
                            step_idx += 1
                            if step_idx % 5 == 0 or step_idx == total_steps:
                                self.root.after(0, lambda v=step_idx/total_steps: self.set_progress(v))

                if best_config is None:
                    self.root.after(0, self.stop_loading)
                    self.root.after(0, lambda: messagebox.showwarning("提示", "未找到有效的最佳配置。"))
                    return

                # Determine insufficient data
                voiced_duration = 0.0
                for s in snippets:
                    try:
                        voiced_duration += float(s.get_total_duration())
                    except Exception:
                        pass
                insufficient_data = voiced_duration < 0.5

                # Generate gears
                # 1. Recommended
                reco_params = best_config

                # 2. Anti-misalignment (lower max_hz, longer window)
                anti_candidates = [
                    cfg for cfg in all_fine_results 
                    if cfg[1] >= 0.030 and cfg[0] <= reco_params[0] - 250 and cfg[3] >= best_score - 0.18
                ]
                if not anti_candidates:
                    anti_candidates = [
                        cfg for cfg in all_fine_results
                        if cfg[1] >= 0.025 and cfg[0] <= reco_params[0] and cfg[3] >= best_score - 0.25
                    ]
                if anti_candidates:
                    best_anti = max(anti_candidates, key=lambda c: (c[3], -c[0], c[1]))
                    anti_params = (best_anti[0], 5, best_anti[1], best_anti[2], best_anti[3])
                else:
                    anti_params = (reco_params[0], 5, max(0.025, reco_params[2]), reco_params[3], reco_params[4] * 0.9)

                # 3. High-resolution (shorter window, score within 0.05 of best)
                fine_candidates = [
                    cfg for cfg in all_fine_results
                    if cfg[1] <= 0.025 and cfg[3] >= best_score - 0.08
                ]
                if fine_candidates:
                    best_fine = sorted(fine_candidates, key=lambda c: (c[1], -c[3]))[0]
                    fine_params = (best_fine[0], 5, best_fine[1], best_fine[2], best_fine[3])
                else:
                    fine_params = (reco_params[0], 5, min(0.025, reco_params[2]), reco_params[3], reco_params[4] * 0.9)

                def show_result_dialog():
                    self.stop_loading()
                    from modules.formant_detection_dialog import FormantDetectionDialog
                    FormantDetectionDialog(
                        parent=self.root,
                        app=self,
                        voiced_duration=voiced_duration,
                        insufficient_data=insufficient_data,
                        reco_params=reco_params,
                        anti_params=anti_params,
                        fine_params=fine_params
                    )

                self.root.after(0, show_result_dialog)

            except Exception as e:
                self.root.after(0, self.stop_loading)
                self.root.after(0, lambda: messagebox.showerror("错误", f"检测过程中发生错误: {e}"))

        import threading
        threading.Thread(target=run_detection, daemon=True).start()

    def apply_formant_params(self, max_hz, count, window_length, pre_emphasis):
        # Update entry fields UI
        if hasattr(self, 'entry_formant_max_hz') and self.entry_formant_max_hz:
            self.entry_formant_max_hz.delete(0, tk.END)
            self.entry_formant_max_hz.insert(0, str(float(max_hz)))
            if hasattr(self.entry_formant_max_hz, '_last_val'):
                self.entry_formant_max_hz._last_val = str(float(max_hz))
                
        if hasattr(self, 'entry_formant_count') and self.entry_formant_count:
            self.entry_formant_count.delete(0, tk.END)
            self.entry_formant_count.insert(0, str(int(count)))
            if hasattr(self.entry_formant_count, '_last_val'):
                self.entry_formant_count._last_val = str(int(count))

        if hasattr(self, 'entry_formant_window_length') and self.entry_formant_window_length:
            self.entry_formant_window_length.delete(0, tk.END)
            self.entry_formant_window_length.insert(0, f"{float(window_length):.3f}")
            if hasattr(self.entry_formant_window_length, '_last_val'):
                self.entry_formant_window_length._last_val = f"{float(window_length):.3f}"

        if hasattr(self, 'entry_formant_pre_emphasis') and self.entry_formant_pre_emphasis:
            self.entry_formant_pre_emphasis.delete(0, tk.END)
            self.entry_formant_pre_emphasis.insert(0, str(float(pre_emphasis)))
            if hasattr(self.entry_formant_pre_emphasis, '_last_val'):
                self.entry_formant_pre_emphasis._last_val = str(float(pre_emphasis))

        # Sync to self.last_params
        self.last_params['formant_max_hz'] = float(max_hz)
        self.last_params['formant_count'] = int(count)
        self.last_params['formant_window_length'] = float(window_length)
        self.last_params['formant_pre_emphasis'] = float(pre_emphasis)

        self.recalculate_all_formants()

    def recalculate_all_formants(self):
        if not self.items: return

        # Ensure last params is synced
        try:
            self.on_param_change(recalculate_current=False)
        except Exception:
            pass

        items_snapshot = list(self.items.items())
        total = len(items_snapshot)

        def run():
            self.root.after(0, lambda: self.start_loading("正在重新计算共振峰..."))
            
            import parselmouth
            import os
            from modules.audio_core import _sample_formants_helper
            
            params = {
                'formant_max_hz': self.last_params.get('formant_max_hz', 5500.0),
                'formant_count': self.last_params.get('formant_count', 5),
                'formant_window_length': self.last_params.get('formant_window_length', 0.025),
                'formant_pre_emphasis': self.last_params.get('formant_pre_emphasis', 50.0),
                'formant_sample_strategy': self.last_params.get('formant_sample_strategy', '整段11点'),
                'pts': self.last_params.get('pts', 11),
                'analysis_mode': 'formant'
            }

            for i, (iid, item) in enumerate(items_snapshot):
                snd = item.get('snd')
                if not snd and item.get('path') and os.path.exists(item['path']):
                    try:
                        snd = parselmouth.Sound(item['path'])
                        item['snd'] = snd
                    except Exception:
                        pass
                
                if snd and item.get('start') is not None and item.get('end') is not None:
                    try:
                        mic_s = item['start']
                        mic_e = item['end']
                        
                        is_long = ('macro_start' in item and 'macro_end' in item and snd.get_total_duration() > 15.0)
                        
                        if is_long:
                            ms = item['macro_start']
                            me = item['macro_end']
                            valid_ms = max(0, ms)
                            valid_me = min(snd.get_total_duration(), me)
                            
                            padding = 1.0
                            seg_start = max(0.0, valid_ms - padding)
                            seg_end = min(snd.get_total_duration(), valid_me + padding)
                            part_snd = snd.extract_part(from_time=seg_start, to_time=seg_end)
                            
                            rel_s = mic_s - seg_start
                            rel_e = mic_e - seg_start
                            
                            formant_data, preview_formants = _sample_formants_helper(part_snd, rel_s, rel_e, params)
                            formant_data['xs'] = formant_data['xs'] + seg_start
                        else:
                            formant_data, preview_formants = _sample_formants_helper(snd, mic_s, mic_e, params)
                            
                        item['formant_data'] = formant_data
                        item['preview_formants'] = preview_formants
                        
                        item['formant_max_hz'] = params['formant_max_hz']
                        item['formant_count'] = params['formant_count']
                        item['formant_window_length'] = params['formant_window_length']
                        item['formant_pre_emphasis'] = params['formant_pre_emphasis']
                        item['formant_sample_strategy'] = params['formant_sample_strategy']
                        
                        if item.get('path'):
                            if item['path'] in self.audio_cache:
                                cache_item = self.audio_cache[item['path']]
                                cache_item['formant_data'] = formant_data
                                cache_item['preview_formants'] = preview_formants
                                cache_item['formant_max_hz'] = params['formant_max_hz']
                                cache_item['formant_count'] = params['formant_count']
                                cache_item['formant_window_length'] = params['formant_window_length']
                                cache_item['formant_pre_emphasis'] = params['formant_pre_emphasis']
                                cache_item['formant_sample_strategy'] = params['formant_sample_strategy']
                    except Exception as e:
                        import logging
                        logging.getLogger(__name__).error(f"Error recalculating formant: {e}")
                
                if i % 5 == 0 or i == total - 1:
                    self.root.after(0, lambda v=(i + 1) / total: self.set_progress(v))

            def finalize():
                if self.spectrogram_panel.current_item:
                    self.spectrogram_panel.plot_item_spectrogram()
                    self.spectrogram_panel.update_ui_times()

                for iid in list(self.items.keys()):
                    self.tree_panel.update_item_icon(iid)

                self.tree_panel.update_preview()
                self.mark_modified()
                self.stop_loading("共振峰参数已应用，重算完成")

            self.root.after(0, finalize)

        import threading
        threading.Thread(target=run, daemon=True).start()

