"""
modules.toolkit_ui.tab_project_tools_mixin: Tab 5 工程工具（预览、解包提取、综合报告导出） Mixin。
"""
import os
import sys
import re
import csv
import json
import zipfile
import datetime
import shutil
import traceback
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import customtkinter as ctk

from modules.data_utils import make_textgrid_export_stem
from modules.project_manager import read_project_metadata_from_archive
from modules.report_generator import get_pitch_floor, get_pitch_ceiling
from modules.toolkit_ui.dialogs_report import ExportReportDialog
from modules.toolkit_ui.tk_utils import start_safe_thread


class ProjectToolsTabMixin:
    def build_project_tab(self):
        content = ctk.CTkFrame(self.tab_project, fg_color="transparent")
        content.pack(fill=tk.BOTH, expand=True, padx=20, pady=18)
        content.grid_columnconfigure(0, weight=0)
        content.grid_columnconfigure(1, weight=1)
        content.grid_rowconfigure(0, weight=1)

        left_panel = self._make_card(content, fg_color=self.colors["surface_soft"], width=310)
        left_panel.grid(row=0, column=0, sticky="ns", padx=(0, 16))
        left_panel.grid_propagate(False)
        self._section_header(left_panel, "工程操作", "打开 .teproj 后可预览结构，也可提取内部文件。", icon_text="04")

        actions = ctk.CTkFrame(left_panel, fg_color="transparent")
        actions.pack(fill=tk.X, padx=20, pady=(4, 18))
        self.btn_open_project = self._make_button(
            actions,
            "选择工程文件",
            self.select_project_file,
            tone="primary",
            image=self.icons.get("import_white"),
        )
        self.btn_open_project.pack(fill=tk.X, pady=(0, 10))

        self.btn_extract_project = self._make_button(
            actions,
            "提取工程文件",
            self.extract_project_files,
            tone="purple",
            image=self.icons.get("tab_batch"),
        )
        self.btn_extract_project.pack(fill=tk.X, pady=(0, 10))
        self.btn_extract_project.configure(state="disabled")  # Disabled until a project is loaded

        self.btn_export_report = self._make_button(
            actions,
            "导出研究方法报告",
            self.show_export_report_dialog,
            tone="success",
            image=self.icons.get("tab_batch"),
        )
        self.btn_export_report.pack(fill=tk.X)
        self.btn_export_report.configure(state="disabled")

        self.btn_clear_script_runs = self._make_button(
            actions,
            "清理脚本运行记录",
            self.clear_project_script_runs,
            tone="warning",
        )
        self.btn_clear_script_runs.pack(fill=tk.X, pady=(10, 0))
        self.btn_clear_script_runs.configure(state="disabled")

        info_box = ctk.CTkFrame(left_panel, fg_color=self.colors["surface"], corner_radius=14, border_width=1, border_color=self.colors["border"])
        info_box.pack(fill=tk.X, padx=20, pady=(0, 18))
        ctk.CTkLabel(info_box, text="文件信息", font=ctk.CTkFont(family=self.font_family, size=13, weight="bold"), text_color=self.colors["text"]).pack(anchor="w", padx=14, pady=(12, 4))
        self.lbl_proj_file = ctk.CTkLabel(
            info_box,
            text="未加载任何工程文件",
            font=self.font_small,
            text_color=self.colors["muted"],
            wraplength=248,
            justify="left",
        )
        self.lbl_proj_file.pack(fill=tk.X, padx=14, pady=(0, 14), anchor="w")



        right_panel = self._make_card(content)
        right_panel.grid(row=0, column=1, sticky="nsew")
        right_panel.grid_rowconfigure(1, weight=1)
        right_panel.grid_columnconfigure(0, weight=1)

        header = ctk.CTkFrame(right_panel, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=20, pady=(18, 12))
        ctk.CTkLabel(header, text="工程内容预览", font=self.font_title, text_color=self.colors["text"]).pack(anchor="w")
        ctk.CTkLabel(header, text="读取 project.json、发音人、条目、音频资源和缓存数据。", font=self.font_small, text_color=self.colors["muted"]).pack(anchor="w", pady=(4, 0))

        self.preview_container = ctk.CTkFrame(right_panel, fg_color="transparent")
        self.preview_container.grid(row=1, column=0, sticky="nsew", padx=20, pady=(0, 20))
        self.show_placeholder()


    def clear_preview_container(self):
        for widget in self.preview_container.winfo_children():
            widget.destroy()


    def show_placeholder(self):
        self.clear_preview_container()
        card = ctk.CTkFrame(self.preview_container, fg_color=self.colors["surface_soft"], corner_radius=16, border_width=1, border_color=self.colors["border"])
        card.pack(fill=tk.BOTH, expand=True)

        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.place(relx=0.5, rely=0.5, anchor="center")

        ctk.CTkLabel(inner, text="▣", font=ctk.CTkFont(family=self.font_family, size=54, weight="bold"), text_color=self.colors["muted_soft"]).pack(pady=(0, 12))
        ctk.CTkLabel(inner, text="暂无工程文件数据", font=ctk.CTkFont(family=self.font_family, size=20, weight="bold"), text_color=self.colors["text"]).pack(pady=4)
        ctk.CTkLabel(inner, text="请在左侧点击“选择工程文件”并打开 .teproj。", font=self.font_small, text_color=self.colors["muted"], justify="center").pack(pady=4)


    def show_loading_placeholder(self):
        self.clear_preview_container()
        card = ctk.CTkFrame(self.preview_container, fg_color=self.colors["surface_soft"], corner_radius=16, border_width=1, border_color=self.colors["border"])
        card.pack(fill=tk.BOTH, expand=True)

        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.place(relx=0.5, rely=0.5, anchor="center")

        ctk.CTkLabel(inner, text="正在读取工程数据...", font=ctk.CTkFont(family=self.font_family, size=18, weight="bold"), text_color=self.colors["text_soft"]).pack(pady=(0, 8))
        ctk.CTkLabel(inner, text="如果工程包含大量音频资源，预览生成可能需要几秒。", font=self.font_small, text_color=self.colors["muted"]).pack()


    def show_error_placeholder(self, error_msg):
        self.clear_preview_container()
        card = ctk.CTkFrame(self.preview_container, fg_color="#FFF7F7", corner_radius=16, border_width=1, border_color="#FCA5A5")
        card.pack(fill=tk.BOTH, expand=True)

        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.place(relx=0.5, rely=0.5, anchor="center")

        ctk.CTkLabel(inner, text="无法解析工程文件", font=ctk.CTkFont(family=self.font_family, size=20, weight="bold"), text_color=self.colors["danger"]).pack(pady=(0, 8))
        ctk.CTkLabel(inner, text="请确认它是有效的 PhonTracer .teproj 文件。", font=self.font_small, text_color="#991B1B").pack(pady=(0, 10))

        err_box = ctk.CTkTextbox(inner, width=460, height=120, corner_radius=12, border_width=1, border_color="#FCA5A5", fg_color="#FEF2F2", text_color="#991B1B")
        err_box.pack(pady=8)
        err_box.insert("1.0", error_msg)
        err_box.configure(state="disabled")


    def create_detail_row(self, parent, row, label_text, val_text):
        lbl = ctk.CTkLabel(parent, text=label_text, font=ctk.CTkFont(family=self.font_family, size=12, weight="bold"), text_color=self.colors["muted"])
        lbl.grid(row=row, column=0, sticky="w", padx=10, pady=3)
        val = ctk.CTkLabel(parent, text=val_text, font=ctk.CTkFont(family="Consolas", size=12), text_color=self.colors["text"])
        val.grid(row=row, column=1, sticky="w", padx=5, pady=3)


    def display_project_preview(self, project_data, namelist):
        self.clear_preview_container()

        # Configure a custom smaller style for the project preview treeview
        style = ttk.Style()
        style.configure("Proj.Treeview",
                        font=("Microsoft YaHei", 10),
                        rowheight=26,
                        background="#FFFFFF",
                        fieldbackground="#FFFFFF",
                        foreground="#1F2937",
                        borderwidth=0,
                        relief="flat")
        style.configure("Proj.Treeview.Heading",
                        font=("Microsoft YaHei", 10, "bold"),
                        background="#F3F4F6",
                        foreground="#374151")
        style.map("Proj.Treeview", background=[('selected', '#3B82F6')], foreground=[('selected', '#FFFFFF')])

        # Main scrollable content frame inside self.preview_container
        scroll_content = ctk.CTkScrollableFrame(self.preview_container, fg_color="transparent")
        scroll_content.pack(fill=tk.BOTH, expand=True)
        try:
            scroll_content._parent_canvas.configure(yscrollincrement=8)
        except Exception:
            pass

        # 1. Summary Card
        summary_frame = ctk.CTkFrame(scroll_content, fg_color="#FFFFFF", corner_radius=12, border_width=1, border_color="#E5E7EB")
        summary_frame.pack(fill=tk.X, padx=5, pady=5)

        ctk.CTkLabel(summary_frame, text="📊 工程概览与资源统计", font=ctk.CTkFont(family="Microsoft YaHei", size=14, weight="bold"), text_color="#1E3A8A").pack(anchor="w", padx=15, pady=(10, 5))

        sub_grid = ctk.CTkFrame(summary_frame, fg_color="transparent")
        sub_grid.pack(fill=tk.X, padx=5, pady=(0, 10))
        sub_grid.columnconfigure(0, weight=1)
        sub_grid.columnconfigure(1, weight=1)

        # Left summary col (Meta)
        meta_frame = ctk.CTkFrame(sub_grid, fg_color="transparent")
        meta_frame.grid(row=0, column=0, sticky="nsew", padx=5, pady=5)
        ctk.CTkLabel(meta_frame, text="【基本信息】", font=ctk.CTkFont(family="Microsoft YaHei", size=12, weight="bold"), text_color="#374151").grid(row=0, column=0, columnspan=2, sticky="w", padx=10, pady=5)

        version = project_data.get("version", "未知")
        speakers = project_data.get("speakers", {})
        active_speaker_id = project_data.get("active_speaker_id", "无")
        trunc_active_id = (active_speaker_id[:12] + "...") if len(active_speaker_id) > 15 else active_speaker_id

        self.create_detail_row(meta_frame, 1, "工程格式版本:", version)
        self.create_detail_row(meta_frame, 2, "发音人数量:", str(len(speakers)))
        self.create_detail_row(meta_frame, 3, "默认选中发音人 ID:", trunc_active_id)
        self.create_detail_row(meta_frame, 4, "脚本运行记录:", str(len(project_data.get("custom_script_runs", []) or [])))

        # Right summary col (Files)
        files_frame = ctk.CTkFrame(sub_grid, fg_color="transparent")
        files_frame.grid(row=0, column=1, sticky="nsew", padx=5, pady=5)
        ctk.CTkLabel(files_frame, text="【物理文件统计】", font=ctk.CTkFont(family="Microsoft YaHei", size=12, weight="bold"), text_color="#374151").grid(row=0, column=0, columnspan=2, sticky="w", padx=10, pady=5)

        audio_files = [f for f in namelist if f.startswith("audio/")]
        data_files = [f for f in namelist if f.startswith("data/")]

        self.create_detail_row(files_frame, 1, "压缩包内文件总数:", str(len(namelist)))
        self.create_detail_row(files_frame, 2, "音频文件数 (audio/):", str(len(audio_files)))
        self.create_detail_row(files_frame, 3, "缓存数据数 (data/):", str(len(data_files)))

        # 2. Speakers Detail Section
        ctk.CTkLabel(scroll_content, text="👥 发音人及数据分析明细", font=ctk.CTkFont(family="Microsoft YaHei", size=14, weight="bold"), text_color="#1F2937").pack(anchor="w", pady=(15, 5), padx=5)

        if not speakers:
            no_spk_card = ctk.CTkFrame(scroll_content, fg_color="#FFFFFF", corner_radius=12, border_width=1, border_color="#E5E7EB")
            no_spk_card.pack(fill=tk.X, padx=5, pady=5)
            ctk.CTkLabel(no_spk_card, text="暂无发音人数据", font=self.font_main, text_color="#6B7280").pack(pady=20)
            return

        spk_tabview = ctk.CTkTabview(scroll_content, corner_radius=12, border_width=1, border_color="#E5E7EB", fg_color="#FFFFFF")
        spk_tabview.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        for s_id, spk in speakers.items():
            name = spk.get("name", "未命名")
            tab_title = name
            if s_id == active_speaker_id:
                tab_title += " (当前选中)"

            spk_tabview.add(tab_title)
            tab_frame = spk_tabview.tab(tab_title)

            # Sub layout within each speaker tab
            top_row = ctk.CTkFrame(tab_frame, fg_color="transparent")
            top_row.pack(fill=tk.X, anchor="n", pady=5)

            info_frame = ctk.CTkFrame(top_row, fg_color="#F3F4F6", corner_radius=8)
            info_frame.pack(fill=tk.X, expand=True, pady=(0, 5))

            ctk.CTkLabel(info_frame, text="发音人与音频", font=ctk.CTkFont(family="Microsoft YaHei", size=12, weight="bold"), text_color="#111827").grid(row=0, column=0, columnspan=2, sticky="w", padx=10, pady=5)
            tab_mode = spk.get("tab_mode", "未知模式")
            self.create_detail_row(info_frame, 1, "唯一标识符 (ID):", s_id)
            self.create_detail_row(info_frame, 2, "音频管理模式:", tab_mode)

            if tab_mode == "单条长音频":
                long_audio_path = spk.get("long_audio_path", "")
                mac_segs = spk.get("current_macro_segments", [])
                man_segs = spk.get("manual_segments", [])
                self.create_detail_row(info_frame, 3, "长音频文件:", os.path.basename(long_audio_path) if long_audio_path else "无")
                self.create_detail_row(info_frame, 4, "自动分段 / 手动微调:", f"{len(mac_segs)} 段 / {len(man_segs) if man_segs is not None else '未进行微调'}")
            else:
                pending_batch_paths = spk.get("pending_batch_paths", [])
                self.create_detail_row(info_frame, 3, "待导入音频数量:", str(len(pending_batch_paths)))

            # Engine params frame
            param_frame = ctk.CTkFrame(top_row, fg_color="#F3F4F6", corner_radius=8)
            param_frame.pack(fill=tk.X, expand=True, pady=(5, 0))

            ctk.CTkLabel(param_frame, text="分析引擎参数", font=ctk.CTkFont(family="Microsoft YaHei", size=12, weight="bold"), text_color="#111827").grid(row=0, column=0, columnspan=2, sticky="w", padx=10, pady=5)
            last_params = spk.get("last_params", {})
            if last_params:
                self.create_detail_row(param_frame, 1, "基频范围 (F0 Range):", f"{get_pitch_floor(last_params):.0f} Hz ~ {get_pitch_ceiling(last_params):.0f} Hz")
                self.create_detail_row(param_frame, 2, "时序分析点数 (Pts):", str(last_params.get('pts', 11)))
                self.create_detail_row(param_frame, 3, "分析算法:", last_params.get('method', 'ac'))
            else:
                ctk.CTkLabel(param_frame, text="无已存引擎参数", font=self.font_main, text_color="#6B7280").grid(row=1, column=0, columnspan=2, sticky="w", padx=10, pady=5)

            # Bottom row: Word items Treeview
            items = spk.get("items", {})

            table_frame = ctk.CTkFrame(tab_frame, fg_color="transparent")
            table_frame.pack(fill=tk.BOTH, expand=True, pady=(10, 5))

            ctk.CTkLabel(table_frame, text=f"📋 解析出的字词条目 (共 {len(items)} 条)", font=ctk.CTkFont(family="Microsoft YaHei", size=13, weight="bold"), text_color="#374151").pack(anchor="w", pady=(0, 5))

            if not items:
                no_items_card = ctk.CTkFrame(table_frame, fg_color="#F3F4F6", corner_radius=8)
                no_items_card.pack(fill=tk.X, pady=5)
                ctk.CTkLabel(no_items_card, text="暂无提取的字词数据", font=self.font_main, text_color="#6B7280").pack(pady=15)
            else:
                tree_container = ctk.CTkFrame(table_frame, fg_color="transparent")
                tree_container.pack(fill=tk.BOTH, expand=True)

                cols = ("idx", "word", "time", "cache")
                tree = ttk.Treeview(tree_container, columns=cols, show="headings", height=8)

                tree.heading("idx", text="序号")
                tree.heading("word", text="音节/字词")
                tree.heading("time", text="时间区间 (s)")
                tree.heading("cache", text="F0 缓存状态")

                tree.column("idx", width=60, anchor="center")
                tree.column("word", width=120, anchor="center")
                tree.column("time", width=220, anchor="center")
                tree.column("cache", width=120, anchor="center")

                tree.configure(style="Proj.Treeview", takefocus=False)

                scrollbar = ctk.CTkScrollbar(tree_container, orientation="vertical", command=tree.yview)
                tree.configure(yscrollcommand=scrollbar.set)

                tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
                scrollbar.pack(side=tk.RIGHT, fill=tk.Y, padx=1, pady=1)

                # 滚动隔离：当鼠标悬浮在列表容器（包括列表和滚动条）上时，禁用父 scroll_content 的滚动绑定，允许 Treeview 完全原生滚动；离开时重新启用。
                def on_enter_tree(e):
                    method = getattr(scroll_content, "_disable_all_bindings", None)
                    if callable(method):
                        try:
                            method()
                        except Exception:
                            pass

                def on_leave_tree(e):
                    x = tree_container.winfo_pointerx() - tree_container.winfo_rootx()
                    y = tree_container.winfo_pointery() - tree_container.winfo_rooty()
                    w = tree_container.winfo_width()
                    h = tree_container.winfo_height()
                    if not (0 <= x < w and 0 <= y < h):
                        method = getattr(scroll_content, "_enable_all_bindings", None)
                        if callable(method):
                            try:
                                method()
                            except Exception:
                                pass

                tree_container.bind("<Enter>", on_enter_tree, add="+")
                tree_container.bind("<Leave>", on_leave_tree, add="+")
                tree.bind("<Enter>", on_enter_tree, add="+")
                scrollbar.bind("<Enter>", on_enter_tree, add="+")

                for idx, (item_id, item) in enumerate(items.items(), 1):
                    label = item.get("label", "无")
                    start = item.get("start")
                    end = item.get("end")
                    if start is None or end is None:
                        time_str = "未切分"
                    else:
                        time_str = f"{float(start):.3f}s ~ {float(end):.3f}s"

                    pitch_file = item.get("pitch_data_file", "")
                    has_pitch = "无"
                    if pitch_file and pitch_file in namelist:
                        has_pitch = "已缓存"
                    elif item.get("pitch_data") is not None:
                        has_pitch = "已缓存"

                    tree.insert("", tk.END, values=(idx, label, time_str, has_pitch))


    def load_project_file(self, path):
        if not os.path.exists(path):
            messagebox.showerror("错误", "工程文件不存在。")
            return

        self.loaded_teproj_path = path
        if hasattr(self, "lbl_proj_file"):
            self.lbl_proj_file.configure(
                text=f"已加载工程:\n{os.path.basename(path)}\n\n大小: {os.path.getsize(path) / (1024 * 1024):.2f} MB",
                text_color=self.colors["success"]
            )
        self._set_script_project_card_loading(path)

        self.show_loading_placeholder()
        if hasattr(self, "btn_clear_script_runs"):
            self.btn_clear_script_runs.configure(state="disabled")
        def run():
            try:
                project_data, namelist = read_project_metadata_from_archive(path)
                self.project_data = project_data
                self.project_namelist = namelist
                self.after(0, lambda: self._finish_project_preview(project_data, namelist))
            except Exception as e:
                self.after(0, lambda message=str(e): self._show_project_preview_error(message))

        start_safe_thread(run)


    def _finish_project_preview(self, project_data, namelist):
        self.display_project_preview(project_data, namelist)
        self.btn_extract_project.configure(state="normal")
        self.btn_export_report.configure(state="normal")
        if hasattr(self, "btn_clear_script_runs"):
            self.btn_clear_script_runs.configure(state="normal")
        self._set_script_project_card_summary(project_data)
        self.update_script_tab_project_summary()


    def _show_project_preview_error(self, message):
        err_msg = f"❌ 无法解析工程文件: {message}"
        self.show_error_placeholder(err_msg)
        self.btn_extract_project.configure(state="disabled")
        self.btn_export_report.configure(state="disabled")
        if hasattr(self, "btn_clear_script_runs"):
            self.btn_clear_script_runs.configure(state="disabled")
        if hasattr(self, "lbl_proj_file"):
            self.lbl_proj_file.configure(text="解析失败", text_color=self.colors["danger"])
        self._set_script_project_card_error(message)
        messagebox.showerror("错误", f"解析 .teproj 文件失败:\n{message}")


    def format_project_preview(self, project_data, namelist):
        def pad_chinese(text, width):
            text_len = len(text)
            chinese_len = sum(1 for char in text if ord(char) > 127)
            padding = width - text_len - chinese_len
            if padding <= 0:
                return text
            return text + " " * padding

        lines = []
        lines.append("=" * 60)
        lines.append("               PHONTRACER 工程文件 (.teproj) 数据预览")
        lines.append("=" * 60)
        lines.append("")

        # 1. Basic Info
        version = project_data.get("version", "未知")
        active_speaker_id = project_data.get("active_speaker_id", "无")
        speakers = project_data.get("speakers", {})

        lines.append("【基本信息】")
        lines.append(f"  • 工程格式版本: {version}")
        lines.append(f"  • 发音人数量: {len(speakers)}")
        lines.append("")

        # 2. Speakers & Detailed structure
        lines.append("【发音人及音频明细】")
        for s_id, spk in speakers.items():
            name = spk.get("name", "未命名")
            tab_mode = spk.get("tab_mode", "未知模式")
            last_params = spk.get("last_params", {})
            items = spk.get("items", {})
            pending_batch_paths = spk.get("pending_batch_paths", [])
            long_audio_path = spk.get("long_audio_path", "")

            is_active = " (当前选中)" if s_id == active_speaker_id else ""
            lines.append(f"  ■ 发音人: {name}{is_active}")
            lines.append(f"    • 唯一标识符: {s_id}")
            lines.append(f"    • 音频管理模式: {tab_mode}")

            if tab_mode == "单条长音频":
                lines.append(f"    • 长音频文件: {long_audio_path or '无'}")
                mac_segs = spk.get("current_macro_segments", [])
                man_segs = spk.get("manual_segments", [])
                lines.append(f"    • 自动分段数量: {len(mac_segs)}")
                lines.append(f"    • 手动微调段数: {len(man_segs) if man_segs is not None else '未进行微调'}")
            else:
                lines.append(f"    • 待导入音频数: {len(pending_batch_paths)}")

            # Engine parameters
            if last_params:
                lines.append("    • 分析引擎参数配置:")
                lines.append(f"      - 基频范围 (F0 Range): {get_pitch_floor(last_params):.0f} Hz ~ {get_pitch_ceiling(last_params):.0f} Hz")
                lines.append(f"      - 时序分析点数 (Points): {last_params.get('pts', 11)}")
                lines.append(f"      - 分析算法: {last_params.get('method', 'ac')}")

            lines.append(f"    • 解析出的字词条目 (共 {len(items)} 条):")
            if not items:
                lines.append("      (暂无提取 of 字词条目数据)")
            else:
                item_header = f"      {'序号':<4} | {pad_chinese('音节/字词', 12)} | {'时间区间 (s)':<22} | {pad_chinese('F0缓存状态', 10)}"
                lines.append(item_header)
                lines.append("      " + "-" * 56)

                for idx, (item_id, item) in enumerate(items.items(), 1):
                    label = item.get("label", "无")
                    start = item.get("start")
                    end = item.get("end")

                    pitch_file = item.get("pitch_data_file", "")
                    has_pitch = "无"
                    if pitch_file and pitch_file in namelist:
                        has_pitch = "已缓存"
                    elif item.get("pitch_data") is not None:
                        has_pitch = "已缓存"

                    idx_str = f"{idx:<4}"
                    label_col = pad_chinese(label, 12)
                    if start is None or end is None:
                        time_col = pad_chinese("未切分", 22)
                    else:
                        time_col = f"{float(start):7.3f}s ~ {float(end):7.3f}s"
                    has_pitch_col = pad_chinese(has_pitch, 10)

                    lines.append(f"      {idx_str} | {label_col} | {time_col} | {has_pitch_col}")
            lines.append("")

        # 3. Archive file list overview
        lines.append("【工程压缩包物理文件清单】")
        lines.append(f"  • 压缩包内文件总数: {len(namelist)}")

        audio_files = [f for f in namelist if f.startswith("audio/")]
        data_files = [f for f in namelist if f.startswith("data/")]

        lines.append(f"  • 音频资源文件数 (audio/): {len(audio_files)}")
        lines.append(f"  • 基频数据缓存数 (data/): {len(data_files)}")
        lines.append("")
        lines.append("=" * 60)

        return "\n".join(lines)


    def _make_project_extract_dir(self, output_dir):
        stem = os.path.splitext(os.path.basename(self.loaded_teproj_path))[0].strip()
        safe_stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", stem).strip(" ._")
        if not safe_stem:
            safe_stem = "extracted_project"

        candidate = os.path.join(output_dir, safe_stem)
        if not os.path.exists(candidate):
            return candidate

        counter = 2
        while True:
            numbered = os.path.join(output_dir, f"{safe_stem}_{counter}")
            if not os.path.exists(numbered):
                return numbered
            counter += 1


    @staticmethod
    def _safe_extract_project_archive(archive_path, target_dir):
        from modules.project_adaptor import (
            validate_project_archive_members,
            MAX_SINGLE_MEMBER_BYTES,
            MAX_TOTAL_UNCOMPRESSED_BYTES,
        )

        target_root = os.path.abspath(target_dir)
        os.makedirs(target_root, exist_ok=False)
        extracted_count = 0
        total_written = 0

        try:
            with zipfile.ZipFile(archive_path, "r") as archive:
                infos = validate_project_archive_members(archive)
                for member in infos:
                    member_name = member.filename.replace("\\", "/")
                    parts = [part for part in member_name.split("/") if part not in ("", ".")]
                    if not parts or any(part == ".." for part in parts):
                        raise ValueError(f"工程包中存在非法路径：{member.filename}")

                    dest_path = os.path.abspath(os.path.join(target_root, *parts))
                    if os.path.commonpath([target_root, dest_path]) != target_root:
                        raise ValueError(f"工程包中存在越界路径：{member.filename}")

                    if member.is_dir() or member_name.endswith("/"):
                        os.makedirs(dest_path, exist_ok=True)
                        continue

                    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
                    file_written = 0
                    with archive.open(member, "r") as src, open(dest_path, "wb") as dst:
                        while True:
                            chunk = src.read(64 * 1024)
                            if not chunk:
                                break
                            file_written += len(chunk)
                            total_written += len(chunk)
                            if file_written > MAX_SINGLE_MEMBER_BYTES or total_written > MAX_TOTAL_UNCOMPRESSED_BYTES:
                                raise ValueError("解压数据超出安全上限")
                            dst.write(chunk)
                    extracted_count += 1
        except Exception:
            try:
                import shutil
                shutil.rmtree(target_root, ignore_errors=True)
            except Exception:
                pass
            raise

        return extracted_count


    @staticmethod
    def _safe_export_name(value, default="未命名"):
        text = str(value or default).strip()
        text = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", text).strip(" ._")
        return text or default


    @staticmethod
    def _unique_output_path(folder, filename):
        stem, ext = os.path.splitext(filename)
        candidate = os.path.join(folder, filename)
        counter = 2
        while os.path.exists(candidate):
            candidate = os.path.join(folder, f"{stem}_{counter}{ext}")
            counter += 1
        return candidate


    @staticmethod
    def _resolve_extracted_resource(raw_root, rel_path):
        rel = str(rel_path or "").replace("\\", "/").strip("/")
        if not rel:
            return None
        parts = [part for part in rel.split("/") if part not in ("", ".")]
        if not parts or any(part == ".." for part in parts):
            return None
        candidate = os.path.abspath(os.path.join(raw_root, *parts))
        raw_root_abs = os.path.abspath(raw_root)
        if os.path.commonpath([raw_root_abs, candidate]) != raw_root_abs:
            return None
        return candidate if os.path.exists(candidate) else None


    @classmethod
    def _copy_named_resource(cls, raw_root, rel_path, dest_folder, filename, index_rows, row_info, index_base=None):
        source_path = cls._resolve_extracted_resource(raw_root, rel_path)
        if not source_path:
            index_rows.append({
                **row_info,
                "原始路径": str(rel_path or ""),
                "整理后路径": "",
                "状态": "源文件缺失",
            })
            return 0

        os.makedirs(dest_folder, exist_ok=True)
        dest_path = cls._unique_output_path(dest_folder, filename)
        import shutil
        shutil.copy2(source_path, dest_path)
        index_rows.append({
            **row_info,
            "原始路径": str(rel_path or ""),
            "整理后路径": os.path.relpath(dest_path, index_base or os.path.dirname(dest_folder)).replace("\\", "/"),
            "状态": "已复制",
        })
        return 1


    @classmethod
    def _export_named_project_files(cls, project_data, raw_root, named_root, index_path):
        speakers = project_data.get("speakers", {}) if isinstance(project_data, dict) else {}
        index_rows = []
        copied_count = 0

        for spk_order, (spk_id, spk) in enumerate(speakers.items(), 1):
            spk_name = cls._safe_export_name(spk.get("name") or spk_id or f"发音人{spk_order}", f"发音人{spk_order}")
            spk_folder = os.path.join(named_root, "音频", f"{spk_order:02d}_{spk_name}")
            items = spk.get("items", {}) if isinstance(spk.get("items", {}), dict) else {}
            item_paths = set()

            long_audio_path = spk.get("long_audio_path")
            if long_audio_path:
                ext = os.path.splitext(str(long_audio_path))[1] or ".wav"
                base = make_textgrid_export_stem(long_audio_path, "长音频", "长音频")
                filename = cls._safe_export_name(f"长音频_{base}", "长音频") + ext
                copied_count += cls._copy_named_resource(
                    raw_root,
                    long_audio_path,
                    os.path.join(spk_folder, "长音频"),
                    filename,
                    index_rows,
                    {
                        "类别": "长音频",
                        "发音人": spk_name,
                        "组别": "",
                        "条目": "",
                        "开始秒": "",
                        "结束秒": "",
                    },
                    os.path.dirname(named_root),
                )

            for item_order, (item_id, item) in enumerate(items.items(), 1):
                rel_path = item.get("path")
                if not rel_path:
                    continue
                item_paths.add(str(rel_path).replace("\\", "/"))
                label = cls._safe_export_name(item.get("label") or make_textgrid_export_stem(rel_path, item_id, item_id), f"条目{item_order}")
                group = cls._safe_export_name(item.get("group") or "未分组", "未分组")
                ext = os.path.splitext(str(rel_path))[1] or ".wav"
                filename = cls._safe_export_name(f"{item_order:03d}_{group}_{label}", f"{item_order:03d}_条目") + ext
                copied_count += cls._copy_named_resource(
                    raw_root,
                    rel_path,
                    os.path.join(spk_folder, "条目"),
                    filename,
                    index_rows,
                    {
                        "类别": "条目音频",
                        "发音人": spk_name,
                        "组别": group,
                        "条目": label,
                        "开始秒": item.get("start", ""),
                        "结束秒": item.get("end", ""),
                    },
                    os.path.dirname(named_root),
                )

            for batch_order, rel_path in enumerate(spk.get("pending_batch_paths", []) or [], 1):
                norm_rel = str(rel_path or "").replace("\\", "/")
                if norm_rel in item_paths:
                    continue
                ext = os.path.splitext(norm_rel)[1] or ".wav"
                base = make_textgrid_export_stem(norm_rel, f"批量音频{batch_order}", f"批量音频{batch_order}")
                filename = cls._safe_export_name(f"{batch_order:03d}_{base}", f"{batch_order:03d}_批量音频") + ext
                copied_count += cls._copy_named_resource(
                    raw_root,
                    rel_path,
                    os.path.join(spk_folder, "批量音频"),
                    filename,
                    index_rows,
                    {
                        "类别": "批量音频",
                        "发音人": spk_name,
                        "组别": "",
                        "条目": "",
                        "开始秒": "",
                        "结束秒": "",
                    },
                    os.path.dirname(named_root),
                )

        os.makedirs(os.path.dirname(index_path), exist_ok=True)
        columns = ["类别", "发音人", "组别", "条目", "开始秒", "结束秒", "原始路径", "整理后路径", "状态"]
        with open(index_path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=columns)
            writer.writeheader()
            for row in index_rows:
                writer.writerow({key: row.get(key, "") for key in columns})

        return copied_count, len(index_rows)


    def extract_project_files(self):
        if not hasattr(self, 'loaded_teproj_path') or not self.loaded_teproj_path:
            return messagebox.showwarning("提示", "请先选择并加载 .teproj 文件")

        output_dir = filedialog.askdirectory(title="选择工程文件提取到的目录")
        if not output_dir:
            return

        target_dir = self._make_project_extract_dir(output_dir)
        self.set_loading(True, "正在提取工程文件与解压资源...", indeterminate=True)

        def do_extract():
            try:
                raw_dir = os.path.join(target_dir, "原始工程结构")
                named_dir = os.path.join(target_dir, "按名称整理")
                index_path = os.path.join(target_dir, "文件索引.csv")
                project_data, _namelist = read_project_metadata_from_archive(self.loaded_teproj_path)
                extracted_count = self._safe_extract_project_archive(self.loaded_teproj_path, raw_dir)
                named_count, index_count = self._export_named_project_files(project_data, raw_dir, named_dir, index_path)
                msg = (
                    f"工程文件已提取到：\n{target_dir}\n\n"
                    f"原始工程结构：{extracted_count} 个文件\n"
                    f"按名称整理：{named_count} 个文件\n"
                    f"文件索引：{index_count} 条记录"
                )
                self.after(0, lambda: messagebox.showinfo("提取成功", msg))
            except Exception as e:
                try:
                    import shutil
                    shutil.rmtree(target_dir, ignore_errors=True)
                except Exception:
                    pass
                self.after(0, lambda err=str(e): messagebox.showerror("错误", f"提取工程文件失败：\n{err}"))
            finally:
                self.after(0, lambda: self.set_loading(False))

        start_safe_thread(do_extract)


    def show_export_report_dialog(self):
        if not hasattr(self, 'loaded_teproj_path') or not self.loaded_teproj_path:
            return messagebox.showwarning("提示", "请先选择并加载 .teproj 文件")

        ExportReportDialog(self, self.execute_export_report)


    def execute_export_report(self, export_format, include_cache):
        output_dir = filedialog.askdirectory(title="选择报告导出保存目录")
        if not output_dir:
            return

        from modules.report_generator import export_reports_from_teproj

        def run():
            self.set_loading(True, "正在生成研究报告与数据档案...", indeterminate=True)
            try:
                export_markdown = (export_format in ("both", "md"))
                export_excel = (export_format in ("both", "excel"))

                exported_files, base_name = export_reports_from_teproj(
                    self.loaded_teproj_path,
                    output_dir,
                    export_markdown=export_markdown,
                    export_excel=export_excel,
                    include_cache_details=include_cache,
                    progress_callback=self.update_report_progress,
                )

                filenames = [os.path.basename(f) for f in exported_files]
                msg = f"报告已成功导出至以下文件：\n" + "\n".join([f"• {name}" for name in filenames])

                self.after(0, lambda: messagebox.showinfo("导出成功", msg))
            except Exception as e:
                import traceback
                traceback.print_exc()
                self.after(0, lambda err_msg=str(e): messagebox.showerror("错误", f"导出失败：\n{err_msg}"))
            finally:
                self.after(0, lambda: self.set_loading(False))

        start_safe_thread(run)

