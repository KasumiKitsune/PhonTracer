"""Bootstrap Mixin: PhonTracer 启动生命周期、属性代理与工程生命周期管理."""
import os
import sys
import time
import threading
import queue
import re
import copy
import concurrent.futures
import multiprocessing
import shutil
import datetime
import tkinter as tk
from tkinter import messagebox, filedialog
import customtkinter as ctk

from modules.version import APP_NAME, __version__
from modules.speaker_manager import SpeakerManager
from modules.project_manager import ProjectManager
from modules.core.paths import get_asset_path


def _startup_debug(message):
    if os.environ.get("PHONTRACER_STARTUP_DEBUG") != "1":
        return
    try:
        import time
        log_dir = os.path.join(os.path.expanduser("~"), ".phon_tracer")
        os.makedirs(log_dir, exist_ok=True)
        with open(os.path.join(log_dir, "startup_debug.log"), "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} [app:{os.getpid()}] {message}\n")
    except Exception:
        pass



class AppBootstrapMixin:
    """提供应用程序启动初始化、属性代理、窗口死锁恢复、崩溃恢复与工程生命周期调度."""

    def __init__(self, root, initial_files=None, defer_startup_check=False):
        self.root = root
        self.root.title(f"{APP_NAME}语音学标注与声学分析系统 V{__version__}")
        self.root.geometry("1200x700")
        self.root.minsize(1100, 650)
        self.root.configure(fg_color="#F3F4F6")

        # 设置窗口图标
        try:
            icon_file = get_asset_path("icon.ico")
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
        self._progress_hide_timer = None
        self.speaker_manager = SpeakerManager()
        self.project_manager = ProjectManager(self)
        self.export_numbering_rule_value = "continuous"
        self.export_numbering_rule_var = ctk.StringVar(value=self.export_numbering_rule_value)
        self.has_changes = False
        self.current_project_path = None
        self.active_chart_dialog = None
        self.custom_script_runs = []

        # Shared ProcessPoolExecutor for performance optimization
        max_workers = min(os.cpu_count() or 4, 8)
        self.executor = concurrent.futures.ProcessPoolExecutor(max_workers=max_workers)

        def on_closing():
            if hasattr(self, "spectrogram_panel") and self.spectrogram_panel and hasattr(self.spectrogram_panel, "stop_playback"):
                self.spectrogram_panel.stop_playback()
            self.flush_eraser_changes()
            if getattr(self, 'has_changes', False):
                ans = messagebox.askyesnocancel("保存项目", "项目已被修改，是否在关闭前保存？")
                if ans is True: # Yes
                    self.project_manager.capture_ui_state()
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
            if getattr(self, '_progress_hide_timer', None) is not None:
                try:
                    self.root.after_cancel(self._progress_hide_timer)
                except Exception:
                    pass
                self._progress_hide_timer = None

            # Clean up workspace on normal exit
            try:
                if hasattr(self, 'project_manager'):
                    self.project_manager.cancel_auto_save()
                    ws_dir = getattr(self.project_manager, 'workspace_dir', None)
                    if ws_dir and isinstance(ws_dir, str):
                        with getattr(self.project_manager, '_save_lock'):
                            if os.path.exists(ws_dir):
                                import shutil
                                shutil.rmtree(ws_dir)
            except Exception as e:
                print(f"Failed to clean up workspace on exit: {e}")

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
            try:
                windnd.hook_dropfiles(self.root, func=self.on_files_dropped, force_unicode=True)
            except TypeError:
                windnd.hook_dropfiles(self.root, func=self.on_files_dropped)
        except Exception:
            pass

        self._startup_target_speaker = None
        self._startup_target_item = None
        self._initial_files_list = self._normalize_startup_files(initial_files)
        _startup_debug(
            f"PhoneticsApp 初始化 initial_files={initial_files!r} "
            f"normalized={self._initial_files_list!r} defer={defer_startup_check}"
        )

        # Check for autosave recovery after window is initialized. 打包版带启动界面时，
        # 这一步由 main.py 在主窗口显示后显式触发，避免启动画面回调链路吞掉工程导入。
        if not defer_startup_check:
            self.root.after(200, self.run_startup_check)


    def _normalize_startup_files(self_or_files, files=None):
        target_self = None
        if not isinstance(self_or_files, type) and hasattr(self_or_files, '_sync_ui_after_project_load'):
            target_self = self_or_files
            raw_files = files
        elif isinstance(self_or_files, type):
            target_self = None
            raw_files = files if files is not None else []
        else:
            target_self = None
            raw_files = self_or_files

        if not raw_files:
            return None

        if isinstance(raw_files, (str, bytes)):
            raw_list = [raw_files]
        else:
            raw_list = list(raw_files)

        cleaned = []
        target_speaker = None
        target_item = None

        i = 0
        while i < len(raw_list):
            item = raw_list[i]
            if item is None:
                i += 1
                continue
            s = str(item).strip().strip('"\'')
            if not s:
                i += 1
                continue

            if s == "--speaker":
                if i + 1 < len(raw_list):
                    target_speaker = str(raw_list[i + 1]).strip().strip('"\'')
                    i += 2
                    continue
                else:
                    i += 1
                    continue
            elif s.startswith("--speaker="):
                target_speaker = s.split("=", 1)[1].strip().strip('"\'')
                i += 1
                continue

            if s == "--item":
                if i + 1 < len(raw_list):
                    target_item = str(raw_list[i + 1]).strip().strip('"\'')
                    i += 2
                    continue
                else:
                    i += 1
                    continue
            elif s.startswith("--item="):
                target_item = s.split("=", 1)[1].strip().strip('"\'')
                i += 1
                continue

            if s.lower().startswith("file://"):
                try:
                    from urllib.parse import unquote, urlparse
                    parsed = urlparse(s)
                    s = unquote(parsed.path or "")
                    # Support windows drive letter formatting on linux for tests
                    if re.match(r"^/[A-Za-z]:", s):
                        s = s[1:]
                except Exception:
                    pass
            if s:
                cleaned.append(os.path.normpath(s))
            i += 1

        if target_self is not None:
            if target_speaker:
                target_self._startup_target_speaker = target_speaker
            if target_item:
                target_self._startup_target_item = target_item

        return cleaned or None


    @classmethod
    def _find_startup_project_file(cls, files):
        for path in cls._normalize_startup_files(files) or []:
            abs_path = os.path.abspath(path)
            if str(abs_path).lower().endswith(('.teproj', '.zip')):
                return abs_path
        return None


    def run_startup_check(self):
        _startup_debug("run_startup_check 开始")
        self._check_autosave_recovery()


    def flush_eraser_changes(self):
        if hasattr(self, 'spectrogram_panel') and self.spectrogram_panel:
            if getattr(self.spectrogram_panel, 'eraser_mode', False):
                self.spectrogram_panel.apply_eraser_changes()


    def mark_modified(self):
        self.has_changes = True
        if hasattr(self, 'project_manager'):
            self.project_manager.capture_ui_state()
            self.project_manager.trigger_auto_save()
        # If chart editor is active and live refresh is enabled, update it
        if getattr(self, 'active_chart_dialog', None):
            try:
                if self.active_chart_dialog.winfo_exists() and self.active_chart_dialog.var_live_refresh.get():
                    self.active_chart_dialog.invalidate_cache()
                    self.active_chart_dialog.trigger_preview_update()
            except Exception:
                pass


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


    def is_project_empty(self):
        speakers = self.speaker_manager.get_all_speakers()
        if len(speakers) > 1:
            return False
        if len(speakers) == 1:
            spk = speakers[0]
            if spk.items:
                return False
            if getattr(spk, 'long_audio_path', None) or getattr(spk, 'pending_batch_paths', None):
                return False
        return True


    def _check_autosave_recovery(self):
        # Skip checking autosave recovery when running unit tests to prevent GUI dialog block/crash
        import sys
        import json
        if not getattr(sys, 'frozen', False):
            if any(k in sys.modules for k in ('unittest', 'pytest')):
                return

        project_json = os.path.join(self.project_manager.workspace_dir, "project.json")
        has_autosave = False
        if os.path.exists(project_json) and os.path.getsize(project_json) > 0:
            try:
                with open(project_json, "r", encoding="utf-8") as f:
                    state = json.load(f)

                # Check if the project actually has any speakers with data
                is_empty = True
                speakers = state.get("speakers", {})
                if len(speakers) > 1:
                    is_empty = False
                elif len(speakers) == 1:
                    spk_id = next(iter(speakers))
                    spk_data = speakers[spk_id]
                    if spk_data.get("items") or spk_data.get("long_audio_path") or spk_data.get("pending_batch_paths"):
                        is_empty = False

                if not is_empty:
                    has_autosave = True
                else:
                    # Clean up the empty workspace silently
                    try:
                        import shutil
                        shutil.rmtree(self.project_manager.workspace_dir)
                        os.makedirs(self.project_manager.workspace_dir)
                    except Exception as e:
                        print(f"Failed to clean up empty autosave workspace: {e}")
            except Exception as e:
                print(f"Failed to parse project.json for autosave check: {e}")
                # Clean up the corrupted workspace silently
                try:
                    import shutil
                    shutil.rmtree(self.project_manager.workspace_dir)
                    os.makedirs(self.project_manager.workspace_dir)
                except Exception as e2:
                    print(f"Failed to clean up corrupted autosave workspace: {e2}")

        initial_files = self._normalize_startup_files(getattr(self, '_initial_files_list', None))
        self._initial_files_list = initial_files
        teproj_path = self._find_startup_project_file(initial_files)
        _startup_debug(
            f"_check_autosave_recovery initial_files={initial_files!r} "
            f"teproj_path={teproj_path!r} has_autosave={has_autosave}"
        )

        if teproj_path:
            # 启动参数来自双击文件关联或拖到 exe。导入逻辑会在完整验证后事务式替换工作区；
            # 如果新工程损坏，保留原自动保存数据供下次恢复。
            self.execute_project_import(teproj_path, overlay=False)
            return

        if has_autosave and not self.project_manager.auto_save_enabled:
            # Discard any leftover workspace files to prevent contamination, since auto-save is disabled
            try:
                import shutil
                shutil.rmtree(self.project_manager.workspace_dir)
                os.makedirs(self.project_manager.workspace_dir)
            except Exception as e:
                print(f"Failed to clean up workspace on startup: {e}")
            has_autosave = False

        if has_autosave:
            # Ask user
            ans = messagebox.askyesno("恢复工程", "检测到上次未正常关闭的自动保存数据，是否恢复？", parent=self.root)
            if ans:
                self.start_loading("正在恢复自动保存数据...")
                def run():
                    success = self.project_manager.load_from_workspace()
                    self.root.after(0, self.stop_loading)
                    if success:
                        def finalize_recovery():
                            self._sync_ui_after_project_load(is_recovery=True)

                            # Ask if they want to import the initial files too
                            if initial_files:
                                import_ans = messagebox.askyesno(
                                    "导入启动文件",
                                    "工程已成功恢复。是否继续导入启动时选择的音频文件？",
                                    parent=self.root
                                )
                                if import_ans:
                                    self.on_files_dropped(initial_files)
                        self.root.after(0, finalize_recovery)
                import threading
                threading.Thread(target=run, daemon=True).start()
            else:
                # User chose not to restore, clean up the workspace
                try:
                    import shutil
                    shutil.rmtree(self.project_manager.workspace_dir)
                    os.makedirs(self.project_manager.workspace_dir)
                except Exception as e:
                    print(f"Failed to clean up autosave workspace: {e}")

                # Now load initial files in a clean workspace
                if initial_files:
                    self.on_files_dropped(initial_files)
        else:
            # No autosave exists, import initial files normally
            if initial_files:
                self.on_files_dropped(initial_files)


    def on_import_project(self):
        if self._has_active_chart_dialog():
            messagebox.showwarning("提示", "图表编辑器已打开，修改图表期间禁止导入新工程。")
            return
        path = filedialog.askopenfilename(filetypes=[("PhonTracer Project", "*.teproj *.zip")])
        if not path: return

        from modules.project_import_dialog import ProjectImportPreviewDialog
        ProjectImportPreviewDialog(self.root, self, path)


    def execute_project_import(self, path, overlay):
        _startup_debug(f"execute_project_import path={path!r} overlay={overlay}")
        self._last_imported_path = path
        self._last_import_was_overlay = overlay
        self.start_loading("正在导入工程...")

        def run():
            success = self.project_manager.load_project(path, overlay=overlay)
            _startup_debug(f"load_project 返回 success={success} path={path!r}")
            self.root.after(0, self.stop_loading)
            if success:
                self.root.after(0, self._sync_ui_after_project_load)
        import threading
        threading.Thread(target=run, daemon=True).start()


    def _sync_ui_after_project_load(self, is_recovery=False):
        target_spk = getattr(self, '_startup_target_speaker', None)
        if target_spk:
            target_str = str(target_spk).strip()
            all_speakers = self.speaker_manager.get_all_speakers()
            for s in all_speakers:
                if s.id == target_str or s.name == target_str:
                    self.speaker_manager.set_active_speaker(s.id)
                    break
            self._startup_target_speaker = None

        self._update_speaker_dropdown()
        spk = self.active_speaker
        self.speaker_option_var.set(spk.name)
        self.tabview.set(spk.tab_mode)
        self.export_numbering_rule_var.set(getattr(self, "export_numbering_rule_value", "continuous"))

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
        self._maybe_refresh_formants_after_import()

        target_item = getattr(self, '_startup_target_item', None)
        if target_item:
            target_str = str(target_item).strip().lower()
            matched_iid = None
            for iid, item in getattr(self, 'items', {}).items():
                candidates = [
                    str(iid).lower(),
                    str(item.get('id', '')).lower(),
                    str(item.get('word_id', '')).lower(),
                    str(item.get('label', '')).lower(),
                    str(item.get('word', '')).lower(),
                    os.path.splitext(str(item.get('label', '')))[0].lower(),
                    os.path.splitext(str(item.get('word', '')))[0].lower(),
                ]
                if target_str in candidates:
                    matched_iid = iid
                    break
            if matched_iid and hasattr(self, 'tree_panel') and self.tree_panel and hasattr(self.tree_panel, 'tree'):
                tree = self.tree_panel.tree
                sel_iid = matched_iid if tree.exists(matched_iid) else f"warning_{matched_iid}"
                if tree.exists(sel_iid):
                    try:
                        tree.selection_set(sel_iid)
                        tree.focus(sel_iid)
                        tree.see(sel_iid)
                        self.tree_panel.on_tree_select(None)
                    except Exception:
                        pass
            self._startup_target_item = None

        if is_recovery:
            self.has_changes = True
        else:
            is_overlay = getattr(self, '_last_import_was_overlay', False)
            if is_overlay:
                self.current_project_path = None
                self.has_changes = True
            else:
                self.current_project_path = getattr(self, '_last_imported_path', None)
                self.has_changes = False


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
        self.flush_eraser_changes()
        self.project_manager.capture_ui_state()
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
        self.project_manager.save_config()
        if is_enabled:
            self.project_manager.capture_ui_state()
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


