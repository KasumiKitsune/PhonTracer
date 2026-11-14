"""
modules.toolkit_ui.tab_audio_batch_mixin: Tab 1 音频合并与 Tab 2 音频切分 Mixin。
"""
import os
import sys
import re
import traceback
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import customtkinter as ctk
import numpy as np

try:
    import parselmouth
except ImportError:
    from modules import _parselmouth_fallback as parselmouth
    import sys as _sys
    _sys.modules.setdefault("parselmouth", parselmouth)
    _sys.modules.setdefault("parselmouth.praat", parselmouth.praat)

from modules.audio_core import load_compatible_sound, trim_bounds_by_amplitude, macroscopic_vad, SILENCE_AMPLITUDE_THRESHOLD
from modules.visual_splitter import VisualSplitter
from modules.data_utils import fuzzy_match_word_to_path, parse_wordlist as parse_grouped_wordlist
from modules.core.text_io import parse_flat_wordlist, read_text_file_with_fallback
from modules.wordlist_v2 import (
    build_document_from_csv_text,
    build_document_from_messy_text,
    build_document_from_structured_text,
    build_document_from_v1_text,
    document_to_v1_text,
    flatten_wordlist_document,
    load_wordlist_document,
    looks_like_csv_wordlist_text,
    looks_like_structured_wordlist_text,
)
from modules.ui_widgets import bind_text_context_menu
from modules.toolkit_ui.tk_utils import start_safe_thread

def parse_wordlist(raw_text: str):
    """解析字表，兼容主程序的【组别】格式及杂乱草稿格式"""
    return parse_flat_wordlist(raw_text)


class AudioBatchTabMixin:
    def _insert_merge_file(self, path):
        display_p = self._short_path(path, max_parts=2)
        self.tree_merge.insert("", tk.END, values=(path, display_p))
        self._refresh_merge_ui()


    def _refresh_merge_ui(self):
        count = len(self.merge_files)
        if hasattr(self, "lbl_merge_count"):
            self.lbl_merge_count.configure(text=f"{count} 个文件")
        if hasattr(self, "merge_empty_state"):
            if count == 0:
                self.merge_empty_state.place(relx=0.5, rely=0.5, anchor="center")
            else:
                self.merge_empty_state.place_forget()


    def build_merge_tab(self):
        content = ctk.CTkFrame(self.tab_merge, fg_color="transparent")
        content.pack(fill=tk.BOTH, expand=True, padx=20, pady=18)
        content.grid_columnconfigure(0, weight=1)
        content.grid_columnconfigure(1, weight=0)
        content.grid_rowconfigure(0, weight=1)

        left_panel = self._make_card(content)
        left_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 16))
        left_panel.grid_rowconfigure(1, weight=1)
        left_panel.grid_columnconfigure(0, weight=1)

        header = ctk.CTkFrame(left_panel, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=20, pady=(18, 12))
        header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(header, text="待合并文件", font=self.font_title, text_color=self.colors["text"]).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(
            header,
            text="拖入 wav/mp3，或手动添加；列表内拖拽可调整拼接顺序。",
            font=self.font_small,
            text_color=self.colors["muted"],
        ).grid(row=1, column=0, sticky="w", pady=(4, 0))
        self.lbl_merge_count = ctk.CTkLabel(header, text="0 个文件", font=self.font_small, text_color=self.colors["muted"])
        self.lbl_merge_count.grid(row=0, column=1, sticky="e", padx=(8, 0))
        self._make_button(header, "添加音频", self.add_merge_files, image=self.icons.get("plus"), width=132).grid(row=1, column=1, sticky="e", pady=(4, 0))

        tree_container = ctk.CTkFrame(left_panel, fg_color=self.colors["surface"], corner_radius=14, border_width=1, border_color=self.colors["border"])
        tree_container.grid(row=1, column=0, sticky="nsew", padx=20, pady=(0, 12))
        tree_container.grid_columnconfigure(0, weight=1)
        tree_container.grid_rowconfigure(0, weight=1)

        self.tree_merge = ttk.Treeview(tree_container, columns=("FullPath", "DisplayPath"), show="headings", height=15)
        self.tree_merge.heading("DisplayPath", text="文件路径")
        self.tree_merge.column("FullPath", width=0, stretch=tk.NO)
        self.tree_merge.column("DisplayPath", width=520, anchor="w")
        self.tree_merge.configure(displaycolumns=("DisplayPath",), style="Treeview", takefocus=False)

        self.merge_scroll = ctk.CTkScrollbar(tree_container, orientation="vertical", command=self.tree_merge.yview, width=12)
        self.tree_merge.configure(yscrollcommand=self.merge_scroll.set)
        self.tree_merge.grid(row=0, column=0, sticky="nsew", padx=(1, 0), pady=1)
        self.merge_scroll.grid(row=0, column=1, sticky="ns", padx=(0, 1), pady=1)
        self.tree_merge.bind('<BackSpace>', self.remove_merge_file)
        self.tree_merge.bind('<Delete>', self.remove_merge_file)
        self.tree_merge.bind("<Button-1>", self.on_tree_drag_start)
        self.tree_merge.bind("<B1-Motion>", self.on_tree_drag_motion)
        self.tree_merge.bind("<ButtonRelease-1>", self.on_tree_drag_drop)

        self.merge_empty_state = ctk.CTkFrame(tree_container, fg_color="transparent")
        ctk.CTkLabel(self.merge_empty_state, text="将音频文件拖到这里", font=ctk.CTkFont(family=self.font_family, size=18, weight="bold"), text_color=self.colors["text_soft"]).pack(pady=(0, 6))
        ctk.CTkLabel(self.merge_empty_state, text="支持 .wav / .mp3；按 Delete 可移除选中项", font=self.font_small, text_color=self.colors["muted"]).pack()
        self.merge_empty_state.place(relx=0.5, rely=0.5, anchor="center")

        ctk.CTkLabel(
            left_panel,
            text="提示：导入字表自动排序会用字表条目去模糊匹配文件名，未匹配到的文件会保留在末尾。",
            font=self.font_caption,
            text_color=self.colors["muted"],
        ).grid(row=2, column=0, sticky="w", padx=22, pady=(0, 16))

        right_panel = self._make_card(content, fg_color=self.colors["surface_soft"], width=310)
        right_panel.grid(row=0, column=1, sticky="ns")
        right_panel.grid_propagate(False)
        self._section_header(right_panel, "合并参数", "设置静音间隔后导出一个连续 wav 文件。", icon_text="01")

        settings = ctk.CTkFrame(right_panel, fg_color="transparent")
        settings.pack(fill=tk.X, padx=20, pady=(4, 10))
        self.var_gap = ctk.StringVar(value="0.5")
        self._make_labeled_entry(settings, "音频间隔", self.var_gap, width=110, suffix="秒静音")

        actions = ctk.CTkFrame(right_panel, fg_color="transparent")
        actions.pack(side=tk.BOTTOM, fill=tk.X, padx=20, pady=20)
        self._make_button(actions, "合并并导出音频", self.process_merge, tone="success", image=self.icons.get("save")).pack(fill=tk.X, pady=(0, 10))
        self._make_button(actions, "导入字表自动排序", self.import_wordlist_for_sort, tone="primary", image=self.icons.get("list")).pack(fill=tk.X, pady=(0, 10))
        self._make_button(actions, "清空列表", self.clear_merge_list, tone="danger").pack(fill=tk.X)

        self._refresh_merge_ui()


    def build_split_tab(self):
        content = ctk.CTkFrame(self.tab_split, fg_color="transparent")
        content.pack(fill=tk.BOTH, expand=True, padx=20, pady=18)
        content.grid_columnconfigure(0, weight=1)
        content.grid_columnconfigure(1, weight=0)
        content.grid_rowconfigure(1, weight=1)

        source_card = self._make_card(content, fg_color=self.colors["surface_soft"])
        source_card.grid(row=0, column=0, sticky="ew", padx=(0, 16), pady=(0, 16))
        source_card.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(source_card, text="音频源", font=self.font_title, text_color=self.colors["text"]).grid(row=0, column=0, padx=20, pady=(16, 6), sticky="w")

        button_row = ctk.CTkFrame(source_card, fg_color="transparent")
        button_row.grid(row=0, column=2, padx=20, pady=16, sticky="e")
        self.btn_sel_source = self._make_button(button_row, "选择长音频", self.select_split_source, image=self.icons.get("audio"), width=142)
        self.btn_sel_source.pack(side=tk.LEFT, padx=(0, 10))
        self.btn_edit_segments = self._make_button(button_row, "段落编辑器", self.open_visual_splitter, tone="warning", image=self.icons.get("eye"), width=142)
        self.btn_edit_segments.pack(side=tk.LEFT)

        path_pill = ctk.CTkFrame(source_card, fg_color=self.colors["surface"], corner_radius=999, border_width=1, border_color=self.colors["border"])
        path_pill.grid(row=1, column=0, columnspan=3, sticky="ew", padx=20, pady=(0, 16))
        self.lbl_split_source = ctk.CTkLabel(path_pill, text="未选择音频文件", text_color=self.colors["muted"], font=self.font_small, anchor="w")
        self.lbl_split_source.pack(fill=tk.X, padx=14, pady=8)

        word_card = self._make_card(content)
        word_card.grid(row=1, column=0, sticky="nsew", padx=(0, 16))
        word_card.grid_rowconfigure(1, weight=1)
        word_card.grid_columnconfigure(0, weight=1)

        word_header = ctk.CTkFrame(word_card, fg_color="transparent")
        word_header.grid(row=0, column=0, sticky="ew", padx=20, pady=(18, 12))
        word_header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(word_header, text="字表文本", font=self.font_title, text_color=self.colors["text"]).grid(row=0, column=0, sticky="w")
        self._make_button(word_header, "同步编辑器", self.sync_wordlist_from_editor, tone="secondary", width=112).grid(row=0, column=1, sticky="e", padx=(0, 8))
        self._make_button(word_header, "导入字表", self.import_wordlist, tone="primary", image=self.icons.get("import_white"), width=126).grid(row=0, column=2, sticky="e")

        self.txt_wordlist = ctk.CTkTextbox(
            word_card,
            corner_radius=14,
            border_width=1,
            border_color=self.colors["border"],
            fg_color=self.colors["surface"],
            text_color=self.colors["text"],
            font=ctk.CTkFont(family=self.font_family, size=14),
        )
        self.txt_wordlist.grid(row=1, column=0, sticky="nsew", padx=20, pady=(0, 10))
        self.txt_wordlist.bind("<KeyRelease>", self.validate_wordlist)
        self.txt_wordlist.bind("<<Paste>>", lambda e: self.after(10, self.validate_wordlist))
        bind_text_context_menu(self.txt_wordlist)

        self.lbl_wordlist_status = ctk.CTkLabel(word_card, text="字表为空", font=self.font_small, text_color=self.colors["muted"])
        self.lbl_wordlist_status.grid(row=2, column=0, sticky="w", padx=22, pady=(0, 16))

        right_panel = self._make_card(content, fg_color=self.colors["surface_soft"], width=310)
        right_panel.grid(row=0, column=1, rowspan=2, sticky="ns")
        right_panel.grid_propagate(False)
        self._section_header(right_panel, "拆分设置", "自动检测有效发音段，也可以在段落编辑器里人工微调。", icon_text="02")

        settings = ctk.CTkFrame(right_panel, fg_color="transparent")
        settings.pack(fill=tk.X, padx=20, pady=(4, 8))

        switch_box = ctk.CTkFrame(settings, fg_color=self.colors["surface"], corner_radius=14, border_width=1, border_color=self.colors["border"])
        switch_box.pack(fill=tk.X, pady=(0, 14))
        self.var_trim = ctk.BooleanVar(value=True)
        switch_trim = ctk.CTkSwitch(
            switch_box,
            text="智能剔除边缘空白杂音",
            variable=self.var_trim,
            font=self.font_main,
            progress_color=self.colors["success"],
            button_color="#475569",
            button_hover_color="#334155",
        )
        switch_trim.pack(anchor="w", padx=14, pady=12)

        self.var_buffer = ctk.StringVar(value="0.1")
        self._make_labeled_entry(settings, "保存区段首尾缓冲", self.var_buffer, width=110, suffix="秒")

        actions = ctk.CTkFrame(right_panel, fg_color="transparent")
        actions.pack(side=tk.BOTTOM, fill=tk.X, padx=20, pady=20)
        self._make_button(actions, "匹配字表", self.match_segments_to_wordlist, tone="warning", image=self.icons.get("check")).pack(fill=tk.X, pady=(0, 10))
        self._make_button(actions, "保存到目录", self.process_split, tone="success", image=self.icons.get("save")).pack(fill=tk.X)


    def add_merge_files(self):
        files = filedialog.askopenfilenames(filetypes=[("Audio Files", "*.wav *.mp3")])
        for f in files:
            if f not in self.merge_files:
                self.merge_files.append(f)
                self._insert_merge_file(f)
        self.sync_merge_files_from_tree()


    def remove_merge_file(self, event=None):
        selected = self.tree_merge.selection()
        for item in selected:
            values = self.tree_merge.item(item, 'values')
            if values:
                full_path = values[0]
                if full_path in self.merge_files:
                    self.merge_files.remove(full_path)
            self.tree_merge.delete(item)
        self.sync_merge_files_from_tree()
        self._refresh_merge_ui()


    def clear_merge_list(self):
        self.merge_files.clear()
        self.tree_merge.delete(*self.tree_merge.get_children())
        self._refresh_merge_ui()


    def on_tree_drag_start(self, event):
        item = self.tree_merge.identify_row(event.y)
        if item:
            self._dragged_item = item
            self.tree_merge.selection_set(item)


    def on_tree_drag_motion(self, event):
        item = self.tree_merge.identify_row(event.y)
        if item:
            bbox = self.tree_merge.bbox(item)
            if bbox:
                mid_y = bbox[1] + bbox[3] // 2
                x = 0
                w = self.tree_merge.winfo_width()
                if event.y < mid_y:
                    y = bbox[1]
                else:
                    y = bbox[1] + bbox[3]
                self._drop_indicator.place(x=x, y=y, width=w)
        else:
            self._drop_indicator.place_forget()


    def on_tree_drag_drop(self, event):
        self._drop_indicator.place_forget()
        if hasattr(self, '_dragged_item'):
            target = self.tree_merge.identify_row(event.y)
            if target and target != self._dragged_item:
                bbox = self.tree_merge.bbox(target)
                if bbox:
                    mid_y = bbox[1] + bbox[3] // 2
                    if event.y < mid_y:
                        self.tree_merge.move(self._dragged_item, '', self.tree_merge.index(target))
                    else:
                        self.tree_merge.move(self._dragged_item, '', self.tree_merge.index(target) + 1)

                self.sync_merge_files_from_tree()
            del self._dragged_item


    def sync_merge_files_from_tree(self):
        """当用户通过拖拽改变顺序后，同步后台的 merge_files 列表"""
        new_list = []
        for child in self.tree_merge.get_children():
            path = self.tree_merge.item(child, 'values')[0]
            new_list.append(path)
        self.merge_files = new_list


    @staticmethod
    def _read_text_file_with_fallback(path, encodings=("utf-8-sig", "utf-8", "gbk")):
        return read_text_file_with_fallback(path, encodings=encodings, strict=True)


    @classmethod
    def _load_wordlist_source(cls, path):
        lower = path.lower()
        title = os.path.splitext(os.path.basename(path))[0] or "字表"
        if lower.endswith(".ptwl"):
            doc = load_wordlist_document(path)
        elif lower.endswith(".csv"):
            text = cls._read_text_file_with_fallback(path)
            doc = build_document_from_csv_text(text, title=title)
        else:
            text = cls._read_text_file_with_fallback(path)
            if looks_like_csv_wordlist_text(text):
                doc = build_document_from_csv_text(text, title=title)
            elif looks_like_structured_wordlist_text(text):
                doc = build_document_from_structured_text(text, title=title)
            else:
                doc = build_document_from_messy_text(text, title=title)

        _groups, flat_words, _records = flatten_wordlist_document(doc)
        return {
            "document": doc,
            "text": document_to_v1_text(doc),
            "flat_words": flat_words,
        }


    def _set_split_wordlist_document(self, doc, text):
        self.split_wordlist_document = doc
        self.split_wordlist_text_snapshot = (text or "").strip()


    def import_wordlist_for_sort(self):
        if not self.merge_files:
            return messagebox.showwarning("提示", "合并列表为空，请先添加音频文件")

        path = filedialog.askopenfilename(filetypes=[("Supported Files", "*.txt *.csv *.ptwl"), ("Text/CSV Files", "*.txt *.csv"), ("高级字表", "*.ptwl"), ("All Files", "*.*")])
        if not path:
            return

        try:
            loaded = self._load_wordlist_source(path)
            flat_words = loaded["flat_words"]
        except Exception as e:
            return messagebox.showerror("错误", f"读取字表失败：{e}")

        if not flat_words:
            return messagebox.showwarning("提示", "字表解析结果为空")

        sorted_paths = []
        available_paths = list(self.merge_files)
        used_indices = set()

        for word in flat_words:
            idx = fuzzy_match_word_to_path(word, available_paths, used_indices=list(used_indices))
            if idx is not None:
                sorted_paths.append(available_paths[idx])
                used_indices.add(idx)

        for i, p in enumerate(available_paths):
            if i not in used_indices:
                sorted_paths.append(p)

        self.merge_files = sorted_paths
        self.tree_merge.delete(*self.tree_merge.get_children())
        for p in self.merge_files:
            self._insert_merge_file(p)

        messagebox.showinfo("排序完成", f"已根据字表重新排序 {len(used_indices)} 个文件。")


    def select_split_source(self):
        f = filedialog.askopenfilename(filetypes=[("Audio Files", "*.wav *.mp3")])
        if f:
            self.split_source = f
            self.lbl_split_source.configure(text=self._short_path(f, max_parts=3), text_color=self.colors["text_soft"])
            self.custom_segments = None


    def import_wordlist(self):
        path = filedialog.askopenfilename(filetypes=[("Supported Files", "*.txt *.csv *.ptwl"), ("Text/CSV Files", "*.txt *.csv"), ("高级字表", "*.ptwl"), ("All Files", "*.*")])
        if not path: return
        try:
            loaded = self._load_wordlist_source(path)
            doc = loaded["document"]
            text = loaded["text"]
            if hasattr(self, "wordlist_editor"):
                self.wordlist_editor.set_document(doc, path=path if path.lower().endswith(".ptwl") else None)
            self._set_split_wordlist_document(doc, text)
        except Exception as e:
            return messagebox.showerror("错误", f"读取字表失败: {e}")
        self.txt_wordlist.delete("1.0", tk.END)
        self.txt_wordlist.insert("1.0", text)
        self.validate_wordlist()


    def validate_wordlist(self, event=None):
        text = self.txt_wordlist.get("1.0", tk.END).strip()
        self.wordlist = parse_wordlist(text)
        if self.split_wordlist_text_snapshot and text != self.split_wordlist_text_snapshot:
            self.split_wordlist_document = None
            self.split_wordlist_text_snapshot = ""
        count = len(self.wordlist)
        if count > 0:
            self.lbl_wordlist_status.configure(text=f"已加载 {count} 个词汇", text_color="#10B981")
        else:
            self.lbl_wordlist_status.configure(text="字表为空", text_color="#6B7280")


    def match_segments_to_wordlist(self):
        if not self.split_source:
            return messagebox.showwarning("提示", "请先选择长音频源文件")

        self.validate_wordlist()
        if not self.wordlist:
            return messagebox.showwarning("提示", "字表为空，请先输入或导入字表")

        def run():
            self.set_loading(True, "正在进行 VAD 检测与匹配...")
            try:
                snd = load_compatible_sound(self.split_source)
                vad_segs = macroscopic_vad(snd)
                self.custom_segments = vad_segs

                msg = f"匹配完成！\n检测到音频段落: {len(vad_segs)} 个\n字表词汇: {len(self.wordlist)} 个"
                if len(vad_segs) != len(self.wordlist):
                    msg += "\n\n注意：数量不一致，可能需要手动调整。"

                self.after(0, lambda: messagebox.showinfo("匹配结果", msg))
            except Exception as e:
                error_message = f"匹配失败: {e}"
                self.after(0, lambda msg=error_message: messagebox.showerror("错误", msg))
            finally:
                self.after(0, lambda: self.set_loading(False))

        start_safe_thread(run)


    def open_visual_splitter(self):
        if not self.split_source:
            return messagebox.showwarning("提示", "请先选择长音频源文件")

        def run():
            self.set_loading(True, "正在加载并检测音频区段...")
            try:
                snd = load_compatible_sound(self.split_source)

                if self.custom_segments:
                    existing_items = [{'id': i, 'label': f'#{i+1}', 'start': s, 'end': e} for i, (s, e) in enumerate(self.custom_segments)]
                    self.after(0, lambda: VisualSplitter(self, snd, {}, self.on_visual_split_confirm, existing_items=existing_items, wordlist=self.wordlist))
                else:
                    vad_segs = macroscopic_vad(snd, expected_count=len(self.wordlist) or None)
                    self.after(0, lambda: VisualSplitter(self, snd, {}, self.on_visual_split_confirm, vad_segments=vad_segs, wordlist=self.wordlist))
            except Exception as e:
                error_message = f"加载失败: {e}"
                self.after(0, lambda msg=error_message: messagebox.showerror("错误", msg))
            finally:
                self.after(0, lambda: self.set_loading(False))

        start_safe_thread(run)


    def on_visual_split_confirm(self, segments, is_update=False, deleted_count=0):
        parsed_segs = []
        for seg in segments:
            if isinstance(seg, tuple):
                parsed_segs.append(seg)
            else:
                parsed_segs.append((seg['start'], seg['end']))
        self.custom_segments = parsed_segs
        messagebox.showinfo("提示", f"已保存 {len(parsed_segs)} 个自定义切分段！\n请确保左侧字表数量与切分段数量一致。")


    def process_merge(self):
        if not self.merge_files: return messagebox.showwarning("提示", "请先添加音频文件")
        try: gap_sec = float(self.var_gap.get())
        except ValueError: return messagebox.showwarning("提示", "间隔时间必须为数字")

        out_path = filedialog.asksaveasfilename(defaultextension=".wav", filetypes=[("WAV Audio", "*.wav")], title="保存合并后的音频")
        if not out_path: return

        def run():
            self.set_loading(True, "正在合并音频...")
            try:
                total = len(self.merge_files)
                loaded_sounds = []
                for i, path in enumerate(self.merge_files):
                    loaded_sounds.append(load_compatible_sound(path))
                    self.update_progress(0.5 * (i + 1) / total)

                source_srs = [float(s.sampling_frequency) for s in loaded_sounds]
                first_sr = source_srs[0]
                target_sr = first_sr if all(abs(sr - first_sr) < 1e-3 for sr in source_srs) else max(source_srs)

                all_vals = []
                gap_samples = max(0, int(round(target_sr * gap_sec)))
                gap_array = np.zeros(gap_samples, dtype=np.float64)

                for i, snd in enumerate(loaded_sounds):
                    if abs(float(snd.sampling_frequency) - target_sr) >= 1e-3:
                        snd = snd.resample(target_sr)
                    mono = np.mean(snd.values, axis=0) if snd.values.shape[0] > 1 else snd.values[0]
                    all_vals.append(np.asarray(mono, dtype=np.float64))
                    all_vals.append(gap_array)
                    self.update_progress(0.5 + 0.5 * (i + 1) / total)

                if all_vals:
                    merged_vals = np.concatenate(all_vals[:-1])
                    merged_snd = parselmouth.Sound(np.array([merged_vals], dtype=np.float64), sampling_frequency=target_sr)
                    merged_snd.save(out_path, "WAV")

                self.after(0, lambda: messagebox.showinfo("成功", f"合并完成！\n保存在: {out_path}"))
            except Exception as e:
                error_message = f"合并失败:\n{e}"
                self.after(0, lambda msg=error_message: messagebox.showerror("错误", msg))
            finally:
                self.after(0, lambda: self.set_loading(False))

        start_safe_thread(run)


    def process_split(self):
        if not self.split_source: return messagebox.showwarning("提示", "请选择长音频源文件")

        raw_text = self.txt_wordlist.get("1.0", tk.END)
        current_text = raw_text.strip()
        if self.split_wordlist_document is not None and current_text == self.split_wordlist_text_snapshot:
            _groups, wordlist, _records = flatten_wordlist_document(self.split_wordlist_document)
        else:
            wordlist = parse_wordlist(raw_text)

        try: buffer_sec = float(self.var_buffer.get())
        except ValueError: return messagebox.showwarning("提示", "缓冲时间必须为数字")

        out_dir = filedialog.askdirectory(title="选择拆分后音频的保存文件夹")
        if not out_dir: return

        do_trim = self.var_trim.get()

        def run():
            self.set_loading(True, "正在分析并拆分音频...")
            try:
                snd = load_compatible_sound(self.split_source)

                if self.custom_segments:
                    segs = self.custom_segments
                elif wordlist:
                    natural_segs = macroscopic_vad(snd)
                    if len(natural_segs) > len(wordlist):
                        gaps = [float(natural_segs[k + 1][0] - natural_segs[k][1]) for k in range(len(natural_segs) - 1)]
                        inter_gaps = [g for g in gaps if g >= 0.30]
                        med_inter = float(np.median(inter_gaps)) if len(inter_gaps) >= 3 else 0.50
                        segs = macroscopic_vad(snd, expected_count=len(wordlist), max_merge_gap=min(0.28, max(0.20, 0.48 * med_inter)))
                    else:
                        segs = natural_segs
                else:
                    segs = macroscopic_vad(snd)

                if not segs:
                    self.after(0, lambda: messagebox.showwarning("警告", "未能在音频中检测到任何有效发音段！"))
                    return

                pad_width = max(3, len(str(max(len(segs), len(wordlist), 1))))
                saved_files = []
                extra_count = 0

                for i, (s, e) in enumerate(segs):
                    if do_trim:
                        part = snd.extract_part(from_time=s, to_time=e)
                        vals = part.values[0]
                        xs = part.xs()
                        s, e = trim_bounds_by_amplitude(s, e, xs, vals)

                    s = max(0, s - buffer_sec)
                    e = min(snd.get_total_duration(), e + buffer_sec)

                    if e > s:
                        extract = snd.extract_part(from_time=s, to_time=e)
                        idx_prefix = str(i + 1).zfill(pad_width)
                        if not wordlist:
                            out_file = os.path.join(out_dir, f"{idx_prefix}_segment.wav")
                        elif i < len(wordlist):
                            safe_word = re.sub(r'[\\/*?:"<>|]', "", wordlist[i]).strip() or "segment"
                            out_file = os.path.join(out_dir, f"{idx_prefix}_{safe_word}.wav")
                        else:
                            extra_count += 1
                            out_file = os.path.join(out_dir, f"{idx_prefix}_extra_{str(extra_count).zfill(2)}.wav")
                        extract.save(out_file, "WAV")
                        saved_files.append(out_file)

                    self.update_progress((i + 1) / len(segs))

                summary_msg = f"成功拆分 {len(saved_files)} 段音频并保存到:\n{out_dir}"
                if wordlist and len(segs) != len(wordlist):
                    summary_msg += f"\n\n[警告] 检出段数 ({len(segs)}) 与字表词数 ({len(wordlist)}) 不一致，多余片段已保留为 extra_* 文件，请人工复核。"
                self.after(0, lambda m=summary_msg: messagebox.showinfo("完成", m))

            except Exception as e:
                error_message = f"拆分失败:\n{e}"
                self.after(0, lambda msg=error_message: messagebox.showerror("错误", msg))
            finally:
                self.after(0, lambda: self.set_loading(False))

        start_safe_thread(run)
