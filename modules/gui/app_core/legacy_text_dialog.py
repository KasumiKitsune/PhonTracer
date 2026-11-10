"""Legacy Text Dialog & Rename Speaker: 传统文本字表导入弹窗与发音人重命名对话框."""
import os
import numpy as np
import tkinter as tk
from tkinter import messagebox, filedialog
import customtkinter as ctk

from modules.ui_widgets import CTkReleaseButton
from modules.version import APP_NAME, __version__
from modules.data_utils import parse_wordlist, split_into_syllables, has_cjk
from modules.wordlist_v2 import (
    ADVANCED_WORDLIST_AGENT_PROMPT,
    build_document_from_csv_text,
    build_document_from_structured_text,
    document_to_v1_text,
    flatten_wordlist_document,
    load_wordlist_document,
    looks_like_csv_wordlist_text,
    looks_like_structured_wordlist_text,
    validate_wordlist_document,
)


class AppLegacyTextDialogMixin:
    """提供字表导入弹窗 (v2/legacy) 及 AI 提示词复制调度."""

    def _copy_v2_agent_prompt(self, parent):
        prompt = ADVANCED_WORDLIST_AGENT_PROMPT
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(prompt)
            messagebox.showinfo("成功", "高级字表 Agent 提示词已复制。", parent=parent)
        except Exception as e:
            messagebox.showerror("错误", f"复制失败：{e}", parent=parent)


    def _open_wordlist_dialog_v2(self, mode):
        return self.open_text_dialog_legacy(mode)


    def open_text_dialog(self, mode):
        if self._has_active_chart_dialog():
            messagebox.showwarning("提示", "图表编辑器已打开，修改图表期间禁止更改/导入字表。")
            return
        if mode == 'long' and not self.pending_long_snd:
            return messagebox.showwarning("提示", "请先导入一条长音频。")
        if mode == 'batch' and not self.pending_batch_paths:
            return messagebox.showwarning("提示", "请先选择独立音频。")
        return self.open_text_dialog_legacy(mode)


    def open_text_dialog_legacy(self, mode):
        if self._has_active_chart_dialog():
            messagebox.showwarning("提示", "图表编辑器已打开，修改图表期间禁止更改/导入字表。")
            return
        if mode == 'long' and not self.pending_long_snd:
            return messagebox.showwarning("提示", "请先导入一条长音频。")
        if mode == 'batch' and not self.pending_batch_paths:
            return messagebox.showwarning("提示", "请先选择独立音频。")

        dlg = ctk.CTkToplevel(self.root)
        dlg.title(f"{APP_NAME}语音学标注与声学分析系统 V{__version__} - 导入字表")
        w, h = 540, (700 if mode == 'batch' else 620)
        # 居中计算
        sw = dlg.winfo_screenwidth()
        sh = dlg.winfo_screenheight()
        x = (sw - w) // 2
        y = (sh - h) // 2
        dlg.geometry(f"{w}x{h}+{x}+{y}")
        dlg.configure(fg_color=("#FFFFFF", "#1A1D24"))

        dlg.transient(self.root)
        dlg.focus_set()

        # 1. 顶部工具栏 (导入文件 / 复制AI提示词)
        toolbar = ctk.CTkFrame(dlg, fg_color="transparent")
        toolbar.pack(fill=tk.X, padx=20, pady=(15, 0))
        active_wordlist_document = {"doc": None}

        def load_txt():
            path = filedialog.askopenfilename(filetypes=[
                ("支持的文件", "*.txt *.csv *.ptwl *.TextGrid"),
                ("文本/CSV 文件", "*.txt *.csv"),
                ("PhonTracer 高级字表", "*.ptwl"),
                ("TextGrid 文件", "*.TextGrid"),
                ("所有文件", "*.*")
            ])
            if not path: return
            if path.lower().endswith('.ptwl'):
                load_v2_wordlist(path)
                return
            active_wordlist_document["doc"] = None
            try:
                if path.lower().endswith('.textgrid'):
                    from modules.data_utils import extract_wordlist_from_textgrid
                    text = extract_wordlist_from_textgrid(path)
                else:
                    try:
                        with open(path, 'r', encoding='utf-8') as f: text = f.read()
                    except UnicodeDecodeError:
                        with open(path, 'r', encoding='gbk') as f: text = f.read()
            except Exception as e:
                return messagebox.showerror("错误", f"读取文件失败: {e}", parent=dlg)
            text_box.delete("1.0", tk.END)
            text_box.insert("1.0", text)
            update_stats()

        def load_v2_wordlist(path=None):
            if path is None:
                path = filedialog.askopenfilename(filetypes=[
                    ("PhonTracer 高级字表", "*.ptwl"),
                    ("所有文件", "*.*")
                ])
            if not path:
                return
            try:
                doc = load_wordlist_document(path)
                _groups, flat_words, _records = flatten_wordlist_document(doc)
                if not flat_words:
                    return messagebox.showwarning("提示", "高级字表中没有可导入的词项。", parent=dlg)
                text = document_to_v1_text(doc)
            except Exception as e:
                return messagebox.showerror("错误", f"读取高级字表失败: {e}", parent=dlg)
            active_wordlist_document["doc"] = doc
            text_box.delete("1.0", tk.END)
            text_box.insert("1.0", text)
            update_stats()

        def copy_prompt():
            prompt = (
                "请帮我将下方的原始字表整理为 PhonTracer 标准字表格式，严格遵守以下规则：\n"
                "1. 组别行：每个组别名称用【】包裹并独占一行，例如：【阴平】。\n"
                "2. 词项行：组别下方的词或字可以一行一个，也可以用空格、逗号或分号分隔。\n"
                "3. 多音节非汉字词：纯汉字词无需加分隔符；若包含英语、拼音或国际音标等多音节非汉字词，请用斜杠 / 标注音节分界（例如：bro/ther、ma1/ma0）。\n"
                "4. 清理杂项：去除所有序号、题号、解释性说明及多余空行。\n"
                "5. 纯文本输出：直接输出整理后的字表正文，不要输出任何开场白、解释文字，也不要使用 ``` 代码块包裹。\n\n"
                "示例输出：\n"
                "【阴平】\n"
                "八 扒 吧\n"
                "【双音节】\n"
                "音频 视频\n\n"
                "以下是我的原始字表：\n"
                "[在此处粘贴你的字表]"
            )
            self.root.clipboard_clear()
            self.root.clipboard_append(prompt)
            messagebox.showinfo("成功", "AI 整理提示词已复制！\n您可以前往 ChatGPT / 豆包 / DeepSeek 等平台粘贴使用。", parent=dlg)

        btn_import = ctk.CTkButton(toolbar, text=" 导入词表文件", image=self.icons.get("import_white"), compound="left",
                                   width=120, height=28, corner_radius=14, fg_color="#2563EB", text_color="white",
                                   hover_color="#1D4ED8", command=load_txt)
        btn_import.pack(side=tk.LEFT)

        def load_textgrid():
            active_wordlist_document["doc"] = None
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
                    name_lower = t.name.strip().lower()
                    if name_lower in ["words", "word"] and words_tier is None:
                        words_tier = t
                    elif name_lower in ["chars", "char"] and chars_tier is None:
                        chars_tier = t
                    elif name_lower in ["groups", "group"] and groups_tier is None:
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



        btn_prompt = ctk.CTkButton(toolbar, text=" 复制整理提示词", image=self.icons.get("copy_white"), compound="left",
                                   width=132, height=28, corner_radius=14, fg_color="#F59E0B", text_color="white",
                                   hover_color="#D97706", command=copy_prompt)
        btn_prompt.pack(side=tk.LEFT, padx=6)

        btn_v2_prompt = ctk.CTkButton(toolbar, text=" 高级字表提示词", image=self.icons.get("copy_white"), compound="left",
                                      width=132, height=28, corner_radius=14, fg_color="#0D9488", text_color="white",
                                      hover_color="#0F766E", command=lambda: self._copy_v2_agent_prompt(dlg))
        btn_v2_prompt.pack(side=tk.LEFT)

        # 2. 文本输入区
        # 创建一个容器来包裹 Textbox 和 浮动占位符
        text_frame = ctk.CTkFrame(dlg, fg_color="transparent")
        text_frame.pack(padx=20, pady=(10, 5), fill=tk.BOTH, expand=True)

        text_box = ctk.CTkTextbox(text_frame, width=380, height=180, corner_radius=8, border_width=1, border_color=("#D1D5DB", "#374151"), fg_color=("#FFFFFF", "#1E1E1E"))
        text_box.pack(fill=tk.BOTH, expand=True)

        placeholder_text = "请在此处粘贴字表文本，或点击下方按钮导入文件。\n\n格式规范：\n1. 组别名称：使用 【】、[] 或 # 开头（如：【一组】）。\n2. 字/词项：组别下方的行即为字词，支持空格、逗号、分号或 Tab 分隔。\n3. 音节切分：可使用斜杠“/”手动分割音节，如 bro/ther 或 北/京。\n\n示例格式：\n【一组】\n妈 麻 马 骂\n#双音节\n北/京  bro/ther\n[三字项]\n录音笔；笔记本"

        # 创建浮动占位符标签
        placeholder_label = ctk.CTkLabel(text_box, text=placeholder_text, text_color="#9CA3AF",
                                         justify=tk.LEFT, font=("Microsoft YaHei", 12), anchor="nw")
        placeholder_label.place(x=10, y=10)

        # 点击占位符时聚焦输入框
        placeholder_label.bind("<Button-1>", lambda e: text_box.focus_set())

        # 3. 实时统计栏
        lbl_stats = ctk.CTkLabel(dlg, text="实时统计：已识别 0 个组别 | 0 个项", text_color=("#6B7280", "#9CA3AF"), font=("Microsoft YaHei", 12))
        lbl_stats.pack(pady=(0, 5), padx=20, anchor="w")

        # 3.5 规则说明 (折叠面板)
        rule_frame = ctk.CTkFrame(dlg, fg_color=("#EFF6FF", "#172554"), corner_radius=8, border_width=1, border_color=("#BFDBFE", "#1E3A8A"), cursor="hand2")
        rule_frame.pack(padx=20, pady=(5, 5), fill=tk.X)
        rule_title = ctk.CTkLabel(rule_frame, text="▶ 💡 匹配与音节拆分规则说明", text_color="#1E40AF", font=("Microsoft YaHei", 12, "bold"), cursor="hand2")
        rule_title.pack(anchor=tk.W, padx=10, pady=5)
        rule_text = (
            "1. CJK(汉字)匹配：模糊匹配时无视数字、英文与特殊字符，仅比对汉字字符。\n"
            "2. 拉丁字母/非CJK：无视数字与特殊字符，保留英文字母进行比对。\n"
            "3. 多音节拆分：默认汉字逐字拆分，非汉字整体为一个音节。若字词中包含斜杠“/”\n"
            "   (如 bro/ther 或 北/京)，则强制按斜杠分割音节，不再按字拆分。"
        )
        rule_lbl = ctk.CTkLabel(rule_frame, text=rule_text, text_color=("#1E40AF", "#93C5FD"), font=("Microsoft YaHei", 11), justify=tk.LEFT)

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
            if active_wordlist_document.get("doc"):
                doc = active_wordlist_document["doc"]
                groups, flat_words, _ = flatten_wordlist_document(doc)
            elif looks_like_structured_wordlist_text(raw_text):
                try:
                    doc = build_document_from_structured_text(raw_text)
                    groups, flat_words, _ = flatten_wordlist_document(doc)
                except Exception:
                    groups, flat_words = parse_wordlist(raw_text)
            elif looks_like_csv_wordlist_text(raw_text):
                try:
                    doc = build_document_from_csv_text(raw_text)
                    groups, flat_words, _ = flatten_wordlist_document(doc)
                except Exception:
                    groups, flat_words = parse_wordlist(raw_text)
            else:
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
            text_box.tag_config("separator", foreground="#2563EB")
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
                    words = [w for w in re.split(r'[\s,，、;；]+', stripped) if w]
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

        def on_text_changed(event=None):
            active_wordlist_document["doc"] = None
            update_stats(event)

        text_box.bind("<KeyRelease>", on_text_changed)
        text_box.bind("<<Paste>>", lambda event: (active_wordlist_document.__setitem__("doc", None), self.root.after(10, update_stats)))


        # 4. 匹配参数区
        match_mode_var = ctk.StringVar(value="fuzzy")
        if mode == 'batch':
            frame_match = ctk.CTkFrame(dlg, fg_color=("#F3F4F6", "#1E293B"), corner_radius=8)
            frame_match.pack(padx=20, pady=10, fill=tk.X)
            ctk.CTkLabel(frame_match, text="匹配方式", text_color=("#4B5563", "#9CA3AF")).pack(anchor=tk.W, padx=10, pady=(5, 0))
            ctk.CTkRadioButton(frame_match, text="模糊匹配 (按文件名自动识别)", variable=match_mode_var, value="fuzzy").pack(anchor=tk.W, padx=15, pady=5)
            ctk.CTkRadioButton(frame_match, text="顺序匹配 (按字表顺序依次对应)", variable=match_mode_var, value="order").pack(anchor=tk.W, padx=15, pady=(0, 10))

        # 5. 执行处理与防错预检
        def process():
            raw_text = text_box.get("1.0", tk.END)
            wordlist_document = active_wordlist_document.get("doc")
            if wordlist_document is None and looks_like_structured_wordlist_text(raw_text):
                try:
                    wordlist_document = build_document_from_structured_text(raw_text)
                    active_wordlist_document["doc"] = wordlist_document
                except Exception:
                    wordlist_document = None
            elif wordlist_document is None and looks_like_csv_wordlist_text(raw_text):
                try:
                    wordlist_document = build_document_from_csv_text(raw_text)
                    active_wordlist_document["doc"] = wordlist_document
                except Exception:
                    wordlist_document = None
            if wordlist_document is not None:
                groups, flat_words, _ = flatten_wordlist_document(wordlist_document)
            else:
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

            if wordlist_document is not None:
                expected_count = None
                if mode == 'batch':
                    expected_count = len(self.pending_batch_paths)
                elif mode == 'long' and hasattr(self, 'manual_segments') and self.manual_segments:
                    expected_count = len(self.manual_segments)
                warnings = validate_wordlist_document(wordlist_document, expected_count=expected_count)
                if warnings and not messagebox.askyesno("高级字表检查提醒", "\n".join(warnings[:12]) + ("\n..." if len(warnings) > 12 else "") + "\n\n是否继续导入？", parent=dlg):
                    return

            self.global_wordlist_document = wordlist_document
            self.global_wordlist_text = raw_text
            if mode == 'batch': self.global_wordlist_match_mode = match_mode_var.get()
            dlg.destroy()
            if mode == 'long': self.process_long_with_wordlist(raw_text, wordlist_document=wordlist_document)
            else: self.process_batch_with_wordlist(raw_text, match_mode=match_mode_var.get(), wordlist_document=wordlist_document)

        # 底部按钮栏
        btn_row = ctk.CTkFrame(dlg, fg_color="transparent")
        btn_row.pack(fill=tk.X, padx=20, pady=(5, 15))

        btn_import_tg = ctk.CTkButton(btn_row, text="导入 .TextGrid",
                                      width=120, height=40, corner_radius=20, fg_color="#8B5CF6", text_color="white",
                                      hover_color="#7C3AED", command=load_textgrid, font=self.font_main)
        btn_import_tg.pack(side=tk.LEFT)

        CTkReleaseButton(
            btn_row,
            text="开始匹配提取",
            command=process,
            corner_radius=20,
            height=40,
            font=self.font_main,
            fg_color="#2563EB",
            hover_color="#1D4ED8",
            text_color="white"
        ).pack(side=tk.RIGHT)

        # 初始触发一次统计
        update_stats()

        self.active_import_dlg = dlg
        self.active_import_textbox = text_box
        self.active_import_update_stats = update_stats
        self.active_import_mode = mode
        self.active_import_wordlist_document = active_wordlist_document

        try:
            import windnd
            try:
                windnd.hook_dropfiles(dlg, func=lambda files: self.drop_queue.put(('dlg', files)), force_unicode=True)
            except TypeError:
                windnd.hook_dropfiles(dlg, func=lambda files: self.drop_queue.put(('dlg', files)))
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"Failed to hook dropfiles on import dialog: {e}")




class RenameSpeakerDialog(ctk.CTkToplevel):
    def __init__(self, parent, current_name=None):
        super().__init__(parent)
        self.parent = parent
        self.result = None
        self.current_name = current_name or ""

        self.title(f"{APP_NAME}语音学标注与声学分析系统 V{__version__} - 重命名发音人")
        self.resizable(False, False)
        self.configure(fg_color=("#FFFFFF", "#1A1D24"))

        # Geometry and centering (extremely compact vertical size)
        width, height = 360, 110
        screen_width = self.winfo_screenwidth()
        screen_height = self.winfo_screenheight()
        x = (screen_width - width) // 2
        y = (screen_height - height) // 2
        self.geometry(f"{width}x{height}+{x}+{y}")

        self.transient(parent)
        self.grab_set()

        # Top stripe
        accent_strip = ctk.CTkFrame(self, height=4, fg_color="#2563EB", corner_radius=0)
        accent_strip.pack(fill="x", side="top")

        # Content frame
        content_frame = ctk.CTkFrame(self, fg_color="transparent")
        content_frame.pack(fill="both", expand=True, padx=20, pady=(15, 15))

        # Label
        lbl = ctk.CTkLabel(
            content_frame,
            text="请输入新的名称：",
            font=ctk.CTkFont(family="Microsoft YaHei", size=13, weight="bold"),
            text_color=("#111827", "#F9FAFB")
        )
        lbl.pack(anchor="w", pady=(0, 8))

        # Row frame (Entry + Buttons on the same line)
        row_frame = ctk.CTkFrame(content_frame, fg_color="transparent")
        row_frame.pack(fill="x")

        # Pill-shaped Entry (height 36, corner_radius 18)
        self.entry = ctk.CTkEntry(
            row_frame,
            height=36,
            corner_radius=18,
            font=ctk.CTkFont(family="Microsoft YaHei", size=13),
            fg_color=("#F9FAFB", "#262930"),
            border_color=("#D1D5DB", "#374151"),
            text_color=("#111827", "#F9FAFB")
        )
        self.entry.insert(0, self.current_name)
        self.entry.pack(side="left", fill="x", expand=True, padx=(0, 10))
        self.entry.focus_set()

        # Select all text in entry
        self.entry.select_range(0, tk.END)
        self.entry.icursor(tk.END)

        # Circular Cancel button (✕, height 36, corner_radius 18)
        btn_cancel = ctk.CTkButton(
            row_frame,
            text="✕",
            width=36,
            height=36,
            corner_radius=18,
            border_spacing=0,
            fg_color=("#F3F4F6", "#374151"),
            text_color=("#EF4444", "#F87171"),
            hover_color=("#E5E7EB", "#4B5563"),
            font=ctk.CTkFont(family="Microsoft YaHei", size=14, weight="bold"),
            command=self.on_cancel
        )
        btn_cancel.pack(side="left", padx=(0, 6))

        # Circular OK button (✓, height 36, corner_radius 18)
        btn_ok = ctk.CTkButton(
            row_frame,
            text="✓",
            width=36,
            height=36,
            corner_radius=18,
            border_spacing=0,
            fg_color=("#10B981", "#059669"),
            text_color="white",
            hover_color=("#059669", "#047857"),
            font=ctk.CTkFont(family="Microsoft YaHei", size=14, weight="bold"),
            command=self.on_ok
        )
        btn_ok.pack(side="left")

        # Bind Enter and Escape
        self.bind("<Return>", lambda e: self.on_ok())
        self.bind("<Escape>", lambda e: self.on_cancel())

        self.parent.wait_window(self)

    def on_ok(self):
        val = self.entry.get().strip()
        if val:
            self.result = val
            self.destroy()
        else:
            messagebox.showwarning("提示", "名称不能为空！", parent=self)

    def on_cancel(self):
        self.result = None
        self.destroy()

