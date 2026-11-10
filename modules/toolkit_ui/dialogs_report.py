"""
modules.toolkit_ui.dialogs_report: 导出研究方法报告与数据档案配置弹窗。
"""
import tkinter as tk
from tkinter import ttk
import customtkinter as ctk

class ExportReportDialog(ctk.CTkToplevel):
    def __init__(self, parent, callback):
        super().__init__(parent)
        self.parent = parent
        self.callback = callback

        self.title("PhonTracer语音学标注与声学分析系统 V1.0 - 导出研究方法报告与数据档案")
        self.resizable(False, False)

        # Center the dialog
        width, height = 450, 320
        screen_width = self.winfo_screenwidth()
        screen_height = self.winfo_screenheight()
        x = (screen_width - width) // 2
        y = (screen_height - height) // 2
        self.geometry(f"{width}x{height}+{x}+{y}")

        self.configure(fg_color=("#FFFFFF", "#1A1D24"))

        # Modal configuration
        self.transient(parent)
        self.grab_set()
        self.focus_set()

        # Accent strip
        accent_strip = ctk.CTkFrame(self, height=4, fg_color="#10B981", corner_radius=0)
        accent_strip.pack(fill="x", side="top")

        self.setup_ui()

    def setup_ui(self):
        # Card container
        card = ctk.CTkFrame(self, fg_color=("#FFFFFF", "#262930"), corner_radius=12, border_width=1, border_color=("#E5E7EB", "#374151"))
        card.pack(fill="both", expand=True, padx=20, pady=(15, 20))

        # Title label
        lbl_title = ctk.CTkLabel(
            card,
            text="📑 导出配置",
            font=ctk.CTkFont(family="Microsoft YaHei", size=16, weight="bold"),
            text_color=("#111827", "#F9FAFB")
        )
        lbl_title.pack(anchor="w", padx=20, pady=(15, 10))

        # Radio variable for export format selection
        self.export_format_var = ctk.StringVar(value="both")

        # Options frame
        options_frame = ctk.CTkFrame(card, fg_color="transparent")
        options_frame.pack(fill="x", padx=20, pady=5)

        # Radio buttons
        r_both = ctk.CTkRadioButton(
            options_frame,
            text="同时导出 Markdown 与 Excel (推荐)",
            variable=self.export_format_var,
            value="both",
            font=ctk.CTkFont(family="Microsoft YaHei", size=12)
        )
        r_both.pack(anchor="w", pady=6)

        r_md = ctk.CTkRadioButton(
            options_frame,
            text="仅导出 Markdown 报告 (*.md)",
            variable=self.export_format_var,
            value="md",
            font=ctk.CTkFont(family="Microsoft YaHei", size=12)
        )
        r_md.pack(anchor="w", pady=6)

        r_excel = ctk.CTkRadioButton(
            options_frame,
            text="仅导出 Excel 数据归档 (*.xlsx)",
            variable=self.export_format_var,
            value="excel",
            font=ctk.CTkFont(family="Microsoft YaHei", size=12)
        )
        r_excel.pack(anchor="w", pady=6)

        # Divider line
        divider = ctk.CTkFrame(card, height=1, fg_color=("#E5E7EB", "#374151"))
        divider.pack(fill="x", padx=20, pady=10)

        # Checkbox for cache details
        self.include_cache_var = ctk.BooleanVar(value=False)
        self.cb_cache = ctk.CTkCheckBox(
            card,
            text="Excel 中附带完整 F0 / 共振峰缓存明细",
            variable=self.include_cache_var,
            checkbox_width=18,
            checkbox_height=18,
            corner_radius=1000,
            border_width=2,
            fg_color=("#2563EB", "#1D4ED8"),
            hover_color=("#9CA3AF", "#4B5563"),
            border_color=("#4B5563", "#9CA3AF"),
            checkmark_color="#FFFFFF",
            text_color=("#1F2937", "#E5E7EB"),
            font=ctk.CTkFont(family="Microsoft YaHei", size=12),
        )
        self.cb_cache.pack(anchor="w", padx=20, pady=5)

        # If user selects only markdown, disable the cache details checkbox
        def on_format_change(*args):
            if self.export_format_var.get() == "md":
                self.cb_cache.configure(state="disabled")
                self.include_cache_var.set(False)
            else:
                self.cb_cache.configure(state="normal")

        self.export_format_var.trace_add("write", on_format_change)

        # Buttons frame
        buttons_frame = ctk.CTkFrame(card, fg_color="transparent")
        buttons_frame.pack(fill="x", side="bottom", padx=20, pady=15)

        btn_cancel = ctk.CTkButton(
            buttons_frame,
            text="取消",
            width=100,
            height=32,
            corner_radius=16,
            fg_color=("#F3F4F6", "#374151"),
            text_color=("#1F2937", "#E5E7EB"),
            hover_color=("#E5E7EB", "#4B5563"),
            font=ctk.CTkFont(family="Microsoft YaHei", size=12),
            command=self.destroy
        )
        btn_cancel.pack(side="left")

        btn_ok = ctk.CTkButton(
            buttons_frame,
            text="确定",
            width=100,
            height=32,
            corner_radius=16,
            fg_color=("#10B981", "#059669"),
            hover_color=("#059669", "#047857"),
            text_color="white",
            font=ctk.CTkFont(family="Microsoft YaHei", size=12, weight="bold"),
            command=self.on_confirm
        )
        btn_ok.pack(side="right")

    def on_confirm(self):
        fmt = self.export_format_var.get()
        include_cache = self.include_cache_var.get()
        self.destroy()
        self.callback(fmt, include_cache)

