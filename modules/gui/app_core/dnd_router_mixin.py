"""Drag and Drop Router Mixin: 统一文件拖放路由与解析."""
import os
import threading
import tkinter as tk
from tkinter import messagebox

from modules.audio_core import check_audio_segments, load_compatible_sound
from modules.data_utils import split_into_syllables
from modules.wordlist_v2 import load_wordlist_document, flatten_wordlist_document, document_to_v1_text


class AppDragDropMixin:
    """提供文件拖拽解析、格式分发与后台处理队列路由."""

    @staticmethod
    def _decode_dropped_file_path(f):
        if isinstance(f, bytes):
            import locale
            candidates = []
            for enc in ('utf-8', locale.getpreferredencoding(False) or 'utf-8', 'gbk', 'gb18030'):
                try:
                    s = f.decode(enc).strip().strip('"\'')
                    if s and s not in candidates:
                        candidates.append(s)
                except (UnicodeDecodeError, LookupError):
                    continue
            for s in candidates:
                if os.path.exists(s):
                    return s
            if candidates:
                return candidates[0]
            return f.decode('utf-8', errors='replace').strip().strip('"\'')
        return str(f).strip().strip('"\'')


    def _process_dlg_dropped_files(self, files):
        if not getattr(self, 'active_import_dlg', None) or not self.active_import_dlg.winfo_exists():
            return

        decoded_paths = []
        for f in files:
            path_str = self._decode_dropped_file_path(f)
            if path_str:
                decoded_paths.append(path_str)

        txt_files = [p for p in decoded_paths if p.lower().endswith(('.txt', '.csv'))]
        tg_files = [p for p in decoded_paths if p.lower().endswith('.textgrid')]
        ptwl_files = [p for p in decoded_paths if p.lower().endswith('.ptwl')]
        active_doc = getattr(self, 'active_import_wordlist_document', None)

        def clear_active_doc():
            if isinstance(active_doc, dict):
                active_doc["doc"] = None

        if ptwl_files:
            path = ptwl_files[0]
            try:
                doc = load_wordlist_document(path)
                _groups, flat_words, _records = flatten_wordlist_document(doc)
                if not flat_words:
                    messagebox.showwarning("提示", "高级字表中没有可导入的词项。", parent=self.active_import_dlg)
                    return
                text = document_to_v1_text(doc)
                if isinstance(active_doc, dict):
                    active_doc["doc"] = doc
                self.active_import_textbox.delete("1.0", tk.END)
                self.active_import_textbox.insert("1.0", text)
                self.active_import_update_stats()
            except Exception as e:
                messagebox.showerror("错误", f"读取高级字表失败: {e}", parent=self.active_import_dlg)
            return

        if tg_files:
            clear_active_doc()
            if self.active_import_mode == 'batch':
                if len(tg_files) > 1 or len(self.pending_batch_paths) == 1:
                    dlg = self.active_import_dlg
                    dlg.destroy()
                    self.process_batch_with_textgrid(tg_files)
                    return
                else:
                    path = tg_files[0]
                    try:
                        from modules.data_utils import extract_wordlist_from_textgrid
                        text = extract_wordlist_from_textgrid(path)
                        self.active_import_textbox.delete("1.0", tk.END)
                        self.active_import_textbox.insert("1.0", text)
                        self.active_import_update_stats()
                    except Exception as e:
                        messagebox.showerror("错误", f"解析 TextGrid 失败: {e}", parent=self.active_import_dlg)
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
                    name_lower = t.name.strip().lower()
                    if name_lower in ["words", "word"] and words_tier is None: words_tier = t
                    elif name_lower in ["chars", "char"] and chars_tier is None: chars_tier = t
                    elif name_lower in ["groups", "group"] and groups_tier is None: groups_tier = t
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
            clear_active_doc()
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
            messagebox.showwarning("提示", "拖入的文件类型不支持，请拖入 .txt、.ptwl 或 .TextGrid 文件。", parent=self.active_import_dlg)


    def on_files_dropped(self, files):
        # 仅将文件压入队列，彻底不涉及 Tkinter UI 的 Tcl 调用
        self.drop_queue.put(files)


    def _process_dropped_files(self, files):
        if self._has_active_chart_dialog():
            messagebox.showwarning("提示", "图表编辑器已打开，修改图表期间禁止通过拖入文件进行导入/更改操作。")
            return
        decoded_paths = []
        for f in files:
            path_str = self._decode_dropped_file_path(f)
            if path_str:
                decoded_paths.append(path_str)

        # Check for project files (.teproj, .zip)
        teproj_files = [p for p in decoded_paths if p.lower().endswith(('.teproj', '.zip'))]
        if teproj_files:
            path = teproj_files[0]
            from modules.project_import_dialog import ProjectImportPreviewDialog
            ProjectImportPreviewDialog(self.root, self, path)
            return

        audio_paths = [p for p in decoded_paths if p.lower().endswith(('.wav', '.mp3'))]
        wordlist_files = [p for p in decoded_paths if p.lower().endswith(('.txt', '.csv', '.ptwl', '.textgrid'))]

        # 若同时拖入了多条音频与 TextGrid 文件，直接设置待分析音频列表并自动进行 TextGrid 匹配
        if audio_paths and wordlist_files:
            self.mark_modified()
            tg_only = [p for p in wordlist_files if p.lower().endswith('.textgrid')]
            if len(audio_paths) > 1 and tg_only:
                self.tabview.set("多条独立音频")
                self.pending_batch_paths = audio_paths
                self.lbl_batch_files.configure(text=f"已选 {len(audio_paths)} 个文件 (从拖拽)", text_color="#2563EB")
                self.lbl_status.configure(text="独立音频与 TextGrid 就绪，正在匹配分析...", text_color="#10B981")
                self.process_batch_with_textgrid(tg_only)
                return

        # 检查字表文件（.txt、.csv、.ptwl、.TextGrid）
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
            messagebox.showwarning("提示", "拖入的文件中没有支持的音频文件 (.wav, .mp3)、工程文件 (.teproj) 或字表文件 (.txt, .csv, .ptwl)")
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
                        snd = load_compatible_sound(path)

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
                            if self._has_unified_wordlist():
                                self._process_unified_wordlist('batch', match_mode='fuzzy')
                        else:
                            self.tabview.set("单条长音频")
                            self.pending_long_snd = snd
                            self.long_audio_path = path
                            self.lbl_long_file.configure(text=audio_name + " (从拖拽)", text_color="#2563EB")
                            self.lbl_status.configure(text="长音频就绪", text_color="#10B981")
                            if self._has_unified_wordlist():
                                self._process_unified_wordlist('long')
                    self.root.after(0, update_ui)
                except Exception as e:
                    error_message = f"读取音频失败: {e}"
                    self.root.after(0, self.stop_loading)
                    self.root.after(0, lambda msg=error_message: messagebox.showerror("错误", msg))
            threading.Thread(target=check_audio, daemon=True).start()
        else:
            self.tabview.set("多条独立音频")
            self.pending_batch_paths = paths
            self.lbl_batch_files.configure(text=f"已选 {len(paths)} 个文件 (从拖拽)", text_color="#2563EB")
            self.lbl_status.configure(text="独立音频就绪，正在后台分析...", text_color="#10B981")
            self.start_background_batch_processing(paths)


