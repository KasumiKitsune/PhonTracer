import os
import sys
import threading
import re
import csv
import subprocess
import platform
import copy
import traceback
try:
    import parselmouth
except ImportError:
    from modules import _parselmouth_fallback as parselmouth
    import sys as _sys
    _sys.modules.setdefault("parselmouth", parselmouth)
    _sys.modules.setdefault("parselmouth.praat", parselmouth.praat)
_tcl_dir = os.path.join(getattr(sys, "base_prefix", sys.prefix), "tcl", "tcl8.6")
_tk_dir = os.path.join(getattr(sys, "base_prefix", sys.prefix), "tcl", "tk8.6")
if os.path.isdir(_tcl_dir):
    os.environ.setdefault("TCL_LIBRARY", _tcl_dir)
if os.path.isdir(_tk_dir):
    os.environ.setdefault("TK_LIBRARY", _tk_dir)
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import customtkinter as ctk
import encodings.cp437
import gc
import queue
from modules.audio_core import trim_bounds_by_amplitude, macroscopic_vad, SILENCE_AMPLITUDE_THRESHOLD
from modules.ui_widgets import (
    CTkReleaseButton,
    _apply_custom_arrow,
    bind_text_context_menu,
    install_dynamic_checkbox_patch,
)
from modules.visual_splitter import VisualSplitter

SCRIPT_EDITOR_DEFAULT_NAME = "新建自定义脚本"
SCRIPT_EDITOR_DEFAULT_DESC = "自定义分析说明"
SCRIPT_META_NAME_KEYS = ("脚本名称", "脚本名", "名称")
SCRIPT_META_DESC_KEYS = ("功能说明", "脚本说明", "功能", "说明")


def _parse_script_meta_comment_line(line):
    text = line.strip()
    if not text.startswith("#"):
        return None, ""
    text = text[1:].strip()
    if not text:
        return None, ""

    match = re.match(r"^([^:：\-—]+)\s*[:：\-—]\s*(.+)$", text)
    if not match:
        return None, ""

    key = match.group(1).strip()
    value = match.group(2).strip()
    if key in SCRIPT_META_NAME_KEYS:
        return "name", value
    if key in SCRIPT_META_DESC_KEYS:
        return "description", value
    return None, ""


def strip_markdown_code_fence(code):
    """自动剥离脚本首尾可能附带的 ```python ... ``` 或 ``` ... ``` 代码块围栏。
    若包含自然语言开场白且包含 ```python ... ``` 围栏块，亦自动安全提取。"""
    if not isinstance(code, str):
        return "" if code is None else str(code)
    text = code.strip()
    if not text:
        return ""
    lines = text.splitlines()
    if len(lines) >= 2 and lines[0].strip().startswith("```") and lines[-1].strip() == "```":
        return "\n".join(lines[1:-1]).strip("\r\n")
    if lines and lines[0].strip().startswith("```"):
        body = lines[1:]
        if body and body[-1].strip() == "```":
            body = body[:-1]
        return "\n".join(body).strip("\r\n")
    if lines and lines[-1].strip() == "```":
        return "\n".join(lines[:-1]).strip("\r\n")
    m = re.search(r"```(?:python)?\s*\r?\n(.*?)\r?\n```", text, re.DOTALL)
    if m:
        extracted = m.group(1).strip("\r\n")
        try:
            compile(extracted, "<extracted_check>", "exec")
            return extracted
        except SyntaxError:
            pass
    return code


def parse_script_metadata_comments(code):
    """
    从脚本头部注释中解析脚本名称和功能说明（自动剥离 Markdown 代码围栏，并兼容首行编码声明）。
    期望格式：
    # 脚本名称：声调均值曲线
    # 功能说明：按分组绘制 F0 均值和离散区间
    """
    metadata = {"name": "", "description": ""}
    if not code:
        return metadata

    clean_code = strip_markdown_code_fence(code)
    for idx, raw_line in enumerate(clean_code.splitlines()):
        if idx >= 15:
            break
        stripped = raw_line.strip()
        if not stripped:
            continue
        if not stripped.startswith("#"):
            break
        key, value = _parse_script_meta_comment_line(stripped)
        if key is None:
            continue
        if value and not metadata.get(key):
            metadata[key] = value
        if metadata["name"] and metadata["description"]:
            break

    return metadata


def extract_error_line_number(err_text):
    """从脚本报错日志或堆栈信息中解析 <custom_script> 的报错行号 line N。"""
    if not err_text:
        return None
    text = str(err_text)
    custom_matches = re.findall(r'File\s+"<(?:custom_script|unknown|string)>"\s*,\s*line\s+(\d+)', text, flags=re.IGNORECASE)
    if custom_matches:
        try:
            val = int(custom_matches[-1])
            if val > 0:
                return val
        except ValueError:
            pass
    line_matches = re.findall(r"(?:line\s+(\d+)|第\s*(\d+)\s*行)", text, flags=re.IGNORECASE)
    for m_a, m_b in reversed(line_matches):
        raw_n = m_a or m_b
        try:
            val = int(raw_n)
            if val > 0:
                return val
        except ValueError:
            pass
    return None


# CTkFont 别名（不再全局拦截或强行常驻引用，避免内存泄漏）
_keep_alive_fonts = []
SafeCTkFont = ctk.CTkFont

def start_safe_thread(target):
    # 安全启动后台守护线程，避免破坏全局垃圾回收（GC）机制
    def wrapped():
        try:
            target()
        except Exception:
            traceback.print_exc()
    threading.Thread(target=wrapped, daemon=True).start()

import numpy as np

from PIL import Image, ImageTk
import hashlib
import datetime
import uuid
import zipfile
import json
from modules.data_utils import fuzzy_match_word_to_path, make_textgrid_export_stem, parse_wordlist as parse_grouped_wordlist
from modules.project_manager import read_project_metadata_from_archive
from modules.report_generator import get_pitch_floor, get_pitch_ceiling
from modules.textgrid_converter import (
    DEFAULT_GROUP_NAME,
    NONE_TIER_LABEL,
    PAIR_MODE_ADJACENT,
    PAIR_MODE_NONE,
    TextGridMapping,
    convert_textgrid,
    inspect_textgrid,
    load_auxiliary_wordlist_records,
    preview_textgrid_conversion,
    recommend_tier_names,
)
from modules.wordlist_editor import RoundedGroupList, VisualWordlistEditor
from modules.wordlist_v2 import ADVANCED_WORDLIST_AGENT_PROMPT, build_document_from_csv_text, build_document_from_structured_text, build_document_from_v1_text, document_to_v1_text, flatten_wordlist_document, load_wordlist_document, looks_like_structured_wordlist_text

try:
    import sounddevice as sd
except ImportError:
    sd = None

# --- 核心修复：开启高 DPI 感知，防止图标和界面模糊 ---
if sys.platform == "win32":
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

# ==========================================
# UI 自定义组件
# ==========================================

class CTkScriptList(ctk.CTkScrollableFrame):
    """
    A custom scrollable list utilizing CustomTkinter buttons for modern look-and-feel.
    Items have rounded corners, bolder text when selected, and smooth hover effects.
    """
    def __init__(self, master, font_family="Microsoft YaHei", colors=None, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self.font_family = font_family
        self.colors = colors or {
            "primary": "#2563EB",
            "primary_hover": "#1D4ED8",
            "text": "#17202A",
        }
        self.buttons = {} # iid -> button
        self._selected_id = None
        self._select_callback = None
        self._context_callback = None

        # 绑定空白区域右键菜单到 self 和 self._parent_canvas
        def on_bg_right_click(event):
            if self._context_callback:
                self._context_callback(event, None)
            return "break"

        self.bind("<Button-2>", on_bg_right_click, add="+")
        self.bind("<Button-3>", on_bg_right_click, add="+")
        if hasattr(self, "_parent_canvas") and self._parent_canvas:
            self._parent_canvas.bind("<Button-2>", on_bg_right_click, add="+")
            self._parent_canvas.bind("<Button-3>", on_bg_right_click, add="+")

    def get_children(self):
        return list(self.buttons.keys())

    def selection(self):
        if self._selected_id is not None:
            return (self._selected_id,)
        return ()

    def selection_set(self, iid):
        if iid not in self.buttons:
            return

        # Deselect old
        if self._selected_id in self.buttons:
            old_btn = self.buttons[self._selected_id]
            old_btn.configure(
                fg_color="transparent",
                text_color=self.colors.get("text", "#17202A"),
                hover_color=("#F3F4F6", "#262930"),
                font=ctk.CTkFont(family=self.font_family, size=13, weight="normal")
            )

        self._selected_id = iid

        # Select new
        new_btn = self.buttons[iid]
        new_btn.configure(
            fg_color=self.colors.get("primary", "#2563EB"),
            text_color="#FFFFFF",
            hover_color=self.colors.get("primary_hover", "#1D4ED8"),
            font=ctk.CTkFont(family=self.font_family, size=13, weight="bold")
        )

        if self._select_callback:
            class MockEvent:
                def __init__(self, widget):
                    self.widget = widget
            self._select_callback(MockEvent(self))

    def delete(self, iid):
        if iid in self.buttons:
            self.buttons[iid].destroy()
            del self.buttons[iid]
        if self._selected_id == iid:
            self._selected_id = None

    def exists(self, iid):
        return iid in self.buttons

    def selection_clear(self):
        if self._selected_id in self.buttons:
            old_btn = self.buttons[self._selected_id]
            old_btn.configure(
                fg_color="transparent",
                text_color=self.colors.get("text", "#17202A"),
                hover_color=("#F3F4F6", "#262930"),
                font=ctk.CTkFont(family=self.font_family, size=13, weight="normal")
            )
        self._selected_id = None

    def set_context_callback(self, callback):
        self._context_callback = callback

    def insert(self, parent, index, iid, text):
        btn = ctk.CTkButton(
            self,
            text=text,
            anchor="w",
            height=36,
            corner_radius=8,
            fg_color="transparent",
            text_color=self.colors.get("text", "#17202A"),
            hover_color=("#F1F5F9", "#262930"),
            font=ctk.CTkFont(family=self.font_family, size=13, weight="normal"),
            command=lambda: self.selection_set(iid)
        )
        # Force column configuration so the text always starts at a fixed left margin (12px)
        # and doesn't get centered or shifted due to empty column weights in CTkButton's default grid.
        btn.grid_columnconfigure(0, weight=0, minsize=12)
        btn.grid_columnconfigure(1, weight=0, minsize=0)
        btn.grid_columnconfigure(2, weight=0, minsize=0)
        btn.grid_columnconfigure(3, weight=0)
        btn.grid_columnconfigure(4, weight=1, minsize=12)

        # Configure inner label anchor to 'w' so that if the button is narrower than the text,
        # the text is clipped on the right and never on the left.
        if btn._text_label is not None:
            btn._text_label.configure(anchor="w", justify="left")

        btn.pack(fill=tk.X, pady=2, padx=2)
        self.buttons[iid] = btn

        def on_btn_right_click(event):
            if self._selected_id != iid:
                self.selection_set(iid)
            if self._context_callback:
                self._context_callback(event, iid)
            return "break"

        btn._on_right_click = on_btn_right_click
        btn.bind("<Button-2>", on_btn_right_click, add="+")
        btn.bind("<Button-3>", on_btn_right_click, add="+")

    def bind(self, event_name, callback, add=None):
        if event_name == "<<TreeviewSelect>>":
            self._select_callback = callback
        else:
            super().bind(event_name, callback, add)


# ==========================================
# 核心音频处理辅助
# ==========================================

def parse_wordlist(raw_text: str):
    """解析字表，兼容主程序的【组别】格式"""
    flat_words = []
    raw_text = raw_text.lstrip('\ufeff')
    for line in raw_text.split('\n'):
        line = line.strip()
        if not line: continue
        if line.startswith('【') or line.startswith('[') or line.startswith('［') or line.startswith('#'):
            continue
        words = [w.strip() for w in re.split(r'[,\s\t，、;；]+', line) if w.strip()]
        flat_words.extend(words)
    return flat_words

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


# ==========================================
# 主程序 App
# ==========================================

class ToolkitApp(ctk.CTk):
    def __getattr__(self, attr):
        tk_inst = self.__dict__.get("tk")
        if tk_inst is None:
            raise AttributeError(f"'{type(self).__name__}' object has no attribute '{attr}'")
        return getattr(tk_inst, attr)

    def __init__(self, startup_files=None):
        self._ui_thread_id = threading.get_ident()
        self.gui_queue = queue.Queue()
        self._startup_files_list = self._normalize_startup_files(sys.argv[1:] if startup_files is None else startup_files)
        super().__init__()
        self.process_gui_queue()
        ctk.set_appearance_mode("Light")
        try:
            ctk.set_default_color_theme("blue")
        except Exception:
            pass

        self.title("PhonTracer语音学标注与声学分析系统 V1.0")
        self.geometry("1180x780")
        self.minsize(1080, 720)

        self.colors = {
            "bg": "#EEF2F6",
            "surface": "#FFFFFF",
            "surface_soft": "#F8FAFC",
            "surface_warm": "#FAFAF8",
            "border": "#E2E8F0",
            "border_strong": "#CBD5E1",
            "text": "#17202A",
            "text_soft": "#334155",
            "muted": "#64748B",
            "muted_soft": "#94A3B8",
            "primary": "#2563EB",
            "primary_hover": "#1D4ED8",
            "primary_soft": "#DBEAFE",
            "success": "#10B981",
            "success_hover": "#059669",
            "success_soft": "#D1FAE5",
            "warning": "#F59E0B",
            "warning_hover": "#D97706",
            "warning_soft": "#FEF3C7",
            "danger": "#EF4444",
            "danger_hover": "#DC2626",
            "danger_soft": "#FEE2E2",
            "purple": "#6366F1",
            "purple_hover": "#4F46E5",
            "purple_soft": "#EDE9FE",
        }
        self.configure(fg_color=self.colors["bg"])

        self.font_family = "Microsoft YaHei"
        self.font_title = ctk.CTkFont(family=self.font_family, size=16, weight="bold")
        self.font_main = ctk.CTkFont(family=self.font_family, size=13)
        self.font_small = ctk.CTkFont(family=self.font_family, size=12)
        self.font_caption = ctk.CTkFont(family=self.font_family, size=11)
        self.font_heading = ctk.CTkFont(family=self.font_family, size=24, weight="bold")

        self.merge_files = []
        self.split_source = None
        self.wordlist = []
        self.split_wordlist_document = None
        self.split_wordlist_text_snapshot = ""
        self.custom_segments = None  # 存储用户微调后的分段数据
        self.textgrid_source_path = None
        self.textgrid_source_paths = []
        self.textgrid_file_list_paths = []
        self.textgrid_summary = None
        self.textgrid_group_overrides = {}
        self.textgrid_preview_items = []
        self.textgrid_wordlist_records = []
        self.textgrid_wordlist_path = None

        self.setup_icons()
        self.setup_ui()

        # 设置窗口图标
        ico_path = os.path.join(os.path.dirname(__file__), "assets", "toolkit.ico")
        png_path = os.path.join(os.path.dirname(__file__), "assets", "toolkit.png")

        if os.path.exists(ico_path) and sys.platform == "win32":
            try:
                self.iconbitmap(ico_path)
            except Exception:
                pass
        elif os.path.exists(png_path):
            try:
                img = Image.open(png_path)
                self.icon_photo = ImageTk.PhotoImage(img)
                self.iconphoto(True, self.icon_photo)  # True 会应用到子窗口
            except Exception:
                pass

        # 创建拖拽指示线 (在 setup_ui 之后，确保 self.tree_merge 已创建)
        self._drop_indicator = tk.Frame(self.tree_merge, bg=self.colors["primary"], height=2)
        self._drop_indicator.place_forget()

        # 关闭窗口级文件拖拽导入（windnd），规避 Windows + Tk 下偶发的 GIL 崩溃。
        self.window_drop_enabled = False
        self.after(80, self._open_startup_wordlist_if_any)

    @staticmethod
    def _normalize_startup_files(files):
        cleaned = []
        if not files:
            return None
        for raw in files:
            if not raw:
                continue
            path = str(raw).strip().strip('"\'')
            if path.lower().startswith("file://"):
                try:
                    from urllib.parse import unquote, urlparse
                    parsed = urlparse(path)
                    path = unquote(parsed.path or "")
                    if re.match(r"^/[A-Za-z]:", path):
                        path = path[1:]
                except Exception:
                    pass
            if path:
                cleaned.append(os.path.normpath(path))
        return cleaned or None

    @classmethod
    def _find_startup_wordlist_file(cls, files):
        for path in cls._normalize_startup_files(files) or []:
            abs_path = os.path.abspath(path)
            if abs_path.lower().endswith(".ptwl"):
                return abs_path
        return None

    def _open_startup_wordlist_if_any(self):
        path = self._find_startup_wordlist_file(getattr(self, "_startup_files_list", None))
        if not path or not os.path.exists(path):
            return
        try:
            doc = load_wordlist_document(path)
            self.wordlist_editor.set_document(doc, path=path)
            self.tabview.set(self.tab_wordlist_name)
        except Exception as e:
            messagebox.showerror("打开高级字表失败", f"无法打开启动传入的高级字表：\n{e}")

    def after(self, ms, func=None, *args):
        if func is not None and getattr(self, "_ui_thread_id", None) is not None and threading.get_ident() != self._ui_thread_id:
            if ms > 0:
                self.gui_queue.put(lambda: super(ToolkitApp, self).after(ms, func, *args))
            else:
                self.gui_queue.put(lambda: func(*args))
            return None
        return super().after(ms, func, *args)

    def process_gui_queue(self):
        has_tasks = False
        try:
            while True:
                task = self.gui_queue.get_nowait()
                has_tasks = True
                try:
                    task()
                except Exception as e:
                    print(f"Error in GUI thread execution: {e}")
                finally:
                    self.gui_queue.task_done()
        except queue.Empty:
            pass
        # 动态退避：空闲时降温至 150ms 轮询，消除 100Hz 空转占 CPU；有任务处理时加速至 15ms 保持流畅响应
        delay = 15 if has_tasks else 150
        super().after(delay, self.process_gui_queue)

    def setup_icons(self):
        icon_path = os.path.join("assets", "icons")
        if not os.path.exists(icon_path):
            icon_path = os.path.join(os.path.dirname(__file__), "assets", "icons")

        self.icons = {}
        self._icon_dark_map = {}
        icon_files = {
            "audio": "audio_file.png", "cut": "cut.png", "batch": "batch.png",
            "eye": "eye.png", "list": "list.png", "plus": "plus.png",
            "play": "play.png", "save": "save.png", "check": "check.png",
            "warning": "warning.png", "import": "import_file.png",
            "import_white": "import_white.png", "tab_batch": "tab_batch.png",
            "pause": "pause.png", "copy": "copy_icon.png",
            "folder": "folder_open.png", "refresh": "status_loading.png"
        }

        def make_white(img):
            img = img.convert("RGBA")
            data = np.array(img)
            alpha = data[:, :, 3]
            data[alpha > 0, 0:3] = 255
            return Image.fromarray(data)

        def make_dark(img):
            img = img.convert("RGBA")
            data = np.array(img)
            alpha = data[:, :, 3]
            data[alpha > 0, 0] = 51   # 0x33
            data[alpha > 0, 1] = 65   # 0x41
            data[alpha > 0, 2] = 85   # 0x55 -> #334155
            return Image.fromarray(data)

        for key, filename in icon_files.items():
            path = os.path.join(icon_path, filename)
            if os.path.exists(path):
                img = Image.open(path)
                img_white = make_white(img)
                img_dark = make_dark(img)
                ctk_white = ctk.CTkImage(light_image=img_white, dark_image=img_white, size=(20, 20))
                ctk_dark = ctk.CTkImage(light_image=img_dark, dark_image=img_dark, size=(20, 20))
                self.icons[key] = ctk_white
                self.icons[f"{key}_white"] = ctk_white
                self.icons[f"{key}_dark"] = ctk_dark
                self._icon_dark_map[ctk_white] = ctk_dark
            else:
                self.icons[key] = None
                self.icons[f"{key}_white"] = None
                self.icons[f"{key}_dark"] = None

        # 加载软件 Logo
        logo_path = os.path.join(os.path.dirname(__file__), "assets", "toolkit.png")
        if os.path.exists(logo_path):
            img = Image.open(logo_path)
            self.icons["logo"] = ctk.CTkImage(light_image=img, dark_image=img, size=(32, 32))

    # ==========================
    # 主界面视觉系统
    # ==========================

    def _button_colors(self, tone="primary"):
        palette = {
            "primary": (self.colors["primary"], self.colors["primary_hover"], "white"),
            "success": (self.colors["success"], self.colors["success_hover"], "white"),
            "warning": (self.colors["warning"], self.colors["warning_hover"], "white"),
            "danger": (self.colors["danger"], self.colors["danger_hover"], "white"),
            "purple": (self.colors["purple"], self.colors["purple_hover"], "white"),
            "secondary": ("#E2E8F0", "#CBD5E1", self.colors["text_soft"]),
            "ghost": (self.colors["surface"], "#F1F5F9", self.colors["text_soft"]),
        }
        return palette.get(tone, palette["primary"])

    def _make_button(self, parent, text, command, tone="primary", image=None, width=None, height=42, **kwargs):
        fg, hover, text_color = self._button_colors(tone)
        if tone in ("secondary", "ghost") and image is not None:
            if hasattr(self, "_icon_dark_map") and image in self._icon_dark_map:
                image = self._icon_dark_map[image]
        options = {
            "text": text,
            "command": command,
            "fg_color": fg,
            "hover_color": hover,
            "text_color": text_color,
            "corner_radius": 999,
            "height": height,
            "font": ctk.CTkFont(family=self.font_family, size=13, weight="bold"),
        }
        if width is not None:
            options["width"] = width
        if image is not None:
            options["image"] = image
            options["compound"] = "left"
        options.update(kwargs)
        return CTkReleaseButton(parent, **options)

    def _make_checkbox(self, parent, text, variable=None, command=None, **kwargs):
        options = {
            "text": text,
            "checkbox_width": 18,
            "checkbox_height": 18,
            "corner_radius": 1000,
            "border_width": 2,
            "fg_color": (self.colors.get("primary", "#2563EB"), self.colors.get("primary_hover", "#1D4ED8")),
            "hover_color": ("#9CA3AF", "#4B5563"),
            "border_color": ("#4B5563", "#9CA3AF"),
            "checkmark_color": "#FFFFFF",
            "text_color": ("#1F2937", "#E5E7EB"),
            "font": ctk.CTkFont(family=self.font_family, size=11),
        }
        if variable is not None:
            options["variable"] = variable
        if command is not None:
            options["command"] = command
        options.update(kwargs)
        return ctk.CTkCheckBox(parent, **options)

    def _make_card(self, parent, fg_color=None, width=None, height=None, corner_radius=18):
        kwargs = {
            "fg_color": fg_color or self.colors["surface"],
            "corner_radius": corner_radius,
            "border_width": 1,
            "border_color": self.colors["border"],
        }
        if width is not None:
            kwargs["width"] = width
        if height is not None:
            kwargs["height"] = height
        return ctk.CTkFrame(parent, **kwargs)

    def _section_header(self, parent, title, subtitle=None, icon_text=None, show_toggle=False, toggle_cmd=None):
        frame = ctk.CTkFrame(parent, fg_color="transparent")
        frame.pack(fill=tk.X, padx=20, pady=(18, 12))

        title_row = ctk.CTkFrame(frame, fg_color="transparent")
        title_row.pack(fill=tk.X)
        if icon_text:
            ctk.CTkLabel(
                title_row,
                text=icon_text,
                font=ctk.CTkFont(family=self.font_family, size=18, weight="bold"),
                text_color=self.colors["primary"],
            ).pack(side=tk.LEFT, padx=(0, 8))
        ctk.CTkLabel(title_row, text=title, font=self.font_title, text_color=self.colors["text"]).pack(side=tk.LEFT)

        if show_toggle and toggle_cmd:
            toggle_btn = self._make_button(
                title_row,
                "折叠",
                toggle_cmd,
                tone="ghost",
                height=26,
                width=50,
            )
            toggle_btn.pack(side=tk.RIGHT)
            frame.toggle_btn = toggle_btn

        if subtitle:
            ctk.CTkLabel(
                frame,
                text=subtitle,
                font=self.font_small,
                text_color=self.colors["muted"],
                justify="left",
                wraplength=620,
            ).pack(fill=tk.X, anchor="w", pady=(4, 0))
        return frame

    def _make_labeled_entry(self, parent, label, variable, width=120, suffix=None):
        wrap = ctk.CTkFrame(parent, fg_color="transparent")
        wrap.pack(fill=tk.X, pady=(4, 14))
        ctk.CTkLabel(wrap, text=label, font=self.font_small, text_color=self.colors["text_soft"]).pack(anchor="w")
        row = ctk.CTkFrame(wrap, fg_color="transparent")
        row.pack(fill=tk.X, pady=(6, 0))
        entry = ctk.CTkEntry(
            row,
            textvariable=variable,
            width=width,
            height=38,
            corner_radius=19,
            border_color=self.colors["border_strong"],
            fg_color=self.colors["surface"],
            text_color=self.colors["text"],
            font=self.font_main,
        )
        entry.pack(side=tk.LEFT)
        if suffix:
            ctk.CTkLabel(row, text=suffix, font=self.font_small, text_color=self.colors["muted"]).pack(side=tk.LEFT, padx=8)
        return entry

    def _short_path(self, path, max_parts=2):
        if not path:
            return "未选择"
        normalized = path.replace("\\", "/")
        parts = [p for p in normalized.split("/") if p]
        if len(parts) <= max_parts:
            return normalized
        return "/".join(parts[-max_parts:])

    def _bind_adaptive_wrap(self, label, container, reserved_width=0, min_wrap=180, extra_containers=None):
        def update_wrap(_event=None):
            try:
                width = container.winfo_width()
                if width <= 1:
                    return
                scale = 1.0
                if hasattr(label, "_apply_widget_scaling"):
                    try:
                        scale = max(float(label._apply_widget_scaling(1.0)), 1.0)
                    except Exception:
                        scale = 1.0
                logical_width = int(width / scale)
                wrap = max(min_wrap, logical_width - reserved_width)
                label.configure(wraplength=wrap)
            except Exception:
                pass

        container.bind("<Configure>", update_wrap, add="+")
        for extra in (extra_containers or ()):
            if extra is not None:
                try:
                    extra.bind("<Configure>", update_wrap, add="+")
                except Exception:
                    pass
        label._update_adaptive_wrap = update_wrap
        self.after(0, update_wrap)

    @staticmethod
    def _decode_drop_path(raw_path):
        if isinstance(raw_path, bytes):
            for enc in ("gbk", "utf-8"):
                try:
                    return raw_path.decode(enc)
                except UnicodeDecodeError:
                    continue
            return raw_path.decode(errors="ignore")
        return str(raw_path)

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

    def setup_ui(self):
        self.btn_kwargs = {
            "text_color": "white",
            "corner_radius": 999,
            "height": 42,
            "font": ctk.CTkFont(family=self.font_family, size=13, weight="bold"),
        }

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        style = ttk.Style()
        style.theme_use("default")
        try:
            style.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
        except Exception:
            pass
        style.configure(
            "Treeview",
            font=(self.font_family, 12),
            rowheight=38,
            background=self.colors["surface"],
            fieldbackground=self.colors["surface"],
            foreground=self.colors["text"],
            borderwidth=0,
            relief="flat",
        )
        style.configure(
            "Treeview.Heading",
            font=(self.font_family, 12, "bold"),
            background=self.colors["surface_soft"],
            foreground=self.colors["text_soft"],
            padding=(12, 10),
            borderwidth=0,
            relief="flat",
        )
        style.map(
            "Treeview",
            background=[("selected", self.colors["primary"])],
            foreground=[("selected", "#FFFFFF")],
        )

        style.configure(
            "Converter.Treeview",
            font=(self.font_family, 10),
            rowheight=24,
            background=self.colors["surface"],
            fieldbackground=self.colors["surface"],
            foreground=self.colors["text"],
            borderwidth=0,
            relief="flat",
        )
        style.configure(
            "Converter.Treeview.Heading",
            font=(self.font_family, 10, "bold"),
            background=self.colors["surface_soft"],
            foreground=self.colors["text_soft"],
            padding=(8, 4),
            borderwidth=0,
            relief="flat",
        )
        style.map(
            "Converter.Treeview",
            background=[("selected", self.colors["primary"])],
            foreground=[("selected", "#FFFFFF")],
        )

        self.main_shell = ctk.CTkFrame(self, fg_color="transparent")
        self.main_shell.grid(row=0, column=0, sticky="nsew", padx=24, pady=20)
        self.main_shell.grid_columnconfigure(0, weight=1)
        self.main_shell.grid_rowconfigure(0, weight=1)

        self.tab_merge_name = "音频合并（独立音频→长音频）"
        self.tab_split_name = "音频拆分（长音频→独立音频）"
        self.tab_textgrid_name = "TextGrid 转换器"
        self.tab_wordlist_name = "字表编辑器"
        self.tab_project_name = "工程预览"
        self.tab_script_name = "自定义脚本"

        self.tabview = ctk.CTkTabview(
            self.main_shell,
            corner_radius=20,
            fg_color=self.colors["surface"],
            border_width=1,
            border_color=self.colors["border"],
            segmented_button_fg_color="#E2E8F0",
            segmented_button_selected_color=self.colors["primary"],
            segmented_button_selected_hover_color=self.colors["primary_hover"],
            segmented_button_unselected_color="#E2E8F0",
            segmented_button_unselected_hover_color="#CBD5E1",
            text_color="#334155",
            text_color_disabled=self.colors["muted_soft"],
        )
        self.tabview.grid(row=0, column=0, sticky="nsew")

        self.tab_merge = self.tabview.add(self.tab_merge_name)
        self.tab_split = self.tabview.add(self.tab_split_name)
        self.tab_textgrid = self.tabview.add(self.tab_textgrid_name)
        self.tab_wordlist = self.tabview.add(self.tab_wordlist_name)
        self.tab_project = self.tabview.add(self.tab_project_name)
        self.tab_script = self.tabview.add(self.tab_script_name)

        for tab in (self.tab_merge, self.tab_split, self.tab_textgrid, self.tab_wordlist, self.tab_project, self.tab_script):
            tab.configure(fg_color=self.colors["surface"])

        # Patch segmented button: left-align, pill-shaped, wider, fix text colors
        sb = self.tabview._segmented_button
        # Configure overall size, pill shape (corner_radius), and doubled inner border (border_width=6)
        sb.configure(width=980, height=40, corner_radius=20, border_width=6)
        # Cleanly left-align the segmented button in the grid without stretching
        sb.grid_configure(sticky="w", padx=20, pady=(12, 6))

        orig_sel = sb._select_button_by_value
        orig_unsel = sb._unselect_button_by_value
        sb._select_button_by_value = lambda v: (orig_sel(v), sb._buttons_dict[v].configure(text_color="#FFFFFF") if v in sb._buttons_dict else None)
        sb._unselect_button_by_value = lambda v: (orig_unsel(v), sb._buttons_dict[v].configure(text_color="#334155") if v in sb._buttons_dict else None)

        # Ensure initial tab text is white
        curr_val = sb.get()
        if curr_val in sb._buttons_dict:
            sb._buttons_dict[curr_val].configure(text_color="#FFFFFF")

        self.build_merge_tab()
        self.build_split_tab()
        self.build_textgrid_tab()
        self.build_wordlist_tab()
        self.build_project_tab()
        self.build_script_tab()

        self.progress = ctk.CTkProgressBar(self, height=5, progress_color=self.colors["primary"], fg_color="#D8E0EA")
        self.progress.set(0)
        self.progress.grid(row=1, column=0, sticky="ew")
        self.progress.grid_remove()

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

    def _make_converter_option(self, parent, values, command=None, width=190):
        combo = ctk.CTkOptionMenu(
            parent,
            values=values or [NONE_TIER_LABEL],
            command=command,
            width=width,
            height=36,
            corner_radius=18,
            fg_color=self.colors["surface"],
            text_color=self.colors["text"],
            button_color=self.colors["surface"],
            button_hover_color="#E5E7EB",
            dropdown_fg_color=self.colors["surface"],
            dropdown_hover_color="#EFF6FF",
            dropdown_text_color=self.colors["text"],
            font=self.font_main,
            dropdown_font=self.font_main,
        )
        _apply_custom_arrow(combo)
        return combo

    def build_textgrid_tab(self):
        self.var_textgrid_group_tier = ctk.StringVar(value=NONE_TIER_LABEL)
        self.var_textgrid_item_tier = ctk.StringVar(value=NONE_TIER_LABEL)
        self.var_textgrid_core_tier = ctk.StringVar(value=NONE_TIER_LABEL)
        self.var_textgrid_pair_mode = ctk.BooleanVar(value=False)

        content = ctk.CTkFrame(self.tab_textgrid, fg_color="transparent")
        content.pack(fill=tk.BOTH, expand=True, padx=20, pady=18)
        content.grid_columnconfigure(0, weight=1)
        content.grid_columnconfigure(1, weight=0)
        content.grid_rowconfigure(0, weight=1)

        # 左栏：源文件摘要 + 转换预览
        left_panel = ctk.CTkFrame(content, fg_color="transparent")
        left_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 16))

        source_card = self._make_card(left_panel, fg_color=self.colors["surface_soft"])
        source_card.pack(fill=tk.X, side=tk.TOP, pady=(0, 12))
        source_card.grid_columnconfigure(0, weight=1)

        source_header = ctk.CTkFrame(source_card, fg_color="transparent")
        source_header.grid(row=0, column=0, sticky="ew", padx=20, pady=(8, 4))
        source_header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(source_header, text="源文件与层摘要", font=self.font_title, text_color=self.colors["text"]).grid(row=0, column=0, sticky="w")
        self._make_button(
            source_header,
            "导入 TextGrid",
            self.select_textgrid_source,
            tone="primary",
            image=self.icons.get("import_white"),
            width=148,
            height=36,
        ).grid(row=0, column=1, sticky="e")

        path_pill = ctk.CTkFrame(source_card, fg_color=self.colors["surface"], corner_radius=999, border_width=1, border_color=self.colors["border"])
        path_pill.grid(row=1, column=0, sticky="ew", padx=20, pady=(0, 4))
        self.lbl_textgrid_source = ctk.CTkLabel(path_pill, text="未选择 TextGrid 文件", text_color=self.colors["muted"], font=self.font_small, anchor="w")
        self.lbl_textgrid_source.pack(fill=tk.X, padx=14, pady=5)

        source_body = ctk.CTkFrame(source_card, fg_color="transparent")
        source_body.grid(row=2, column=0, sticky="ew", padx=20, pady=(0, 10))
        source_body.grid_columnconfigure(0, weight=0, minsize=245)
        source_body.grid_columnconfigure(1, weight=1)
        source_body.grid_rowconfigure(0, weight=1)

        file_container = ctk.CTkFrame(source_body, fg_color=self.colors["surface"], corner_radius=8, border_width=1, border_color=self.colors["border"], width=245, height=150)
        file_container.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        file_container.grid_propagate(False)
        file_container.grid_columnconfigure(0, weight=1)
        file_container.grid_rowconfigure(1, weight=1)
        ctk.CTkLabel(file_container, text="待转换文件", font=self.font_title, text_color=self.colors["text"]).grid(row=0, column=0, sticky="w", padx=12, pady=(10, 4))
        self.textgrid_file_list = RoundedGroupList(file_container, colors=self.colors, font_family=self.font_family)
        self.textgrid_file_list.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 10))
        self.textgrid_file_list.bind("<<ListboxSelect>>", self.on_textgrid_file_selected)
        self.textgrid_files_empty_state = ctk.CTkFrame(file_container, fg_color="transparent")
        ctk.CTkLabel(self.textgrid_files_empty_state, text="可一次选择多个 TextGrid", font=self.font_caption, text_color=self.colors["muted"]).pack()
        self.textgrid_files_empty_state.place(relx=0.5, rely=0.5, anchor="center")

        tier_container = ctk.CTkFrame(source_body, fg_color=self.colors["surface"], corner_radius=14, border_width=1, border_color=self.colors["border"], height=150)
        tier_container.grid(row=0, column=1, sticky="nsew")
        tier_container.grid_propagate(False)
        tier_container.grid_columnconfigure(0, weight=1)
        tier_container.grid_rowconfigure(0, weight=1)
        self.tree_textgrid_tiers = ttk.Treeview(
            tier_container,
            columns=("name", "type", "count", "samples"),
            show="headings",
            height=3,
            selectmode="browse",
        )
        self.tree_textgrid_tiers.heading("name", text="层名")
        self.tree_textgrid_tiers.heading("type", text="类型")
        self.tree_textgrid_tiers.heading("count", text="非空/总数")
        self.tree_textgrid_tiers.heading("samples", text="示例标签")
        self.tree_textgrid_tiers.column("name", width=130, anchor="w")
        self.tree_textgrid_tiers.column("type", width=110, anchor="center")
        self.tree_textgrid_tiers.column("count", width=90, anchor="center")
        self.tree_textgrid_tiers.column("samples", width=420, anchor="w")
        self.tree_textgrid_tiers.configure(style="Converter.Treeview", takefocus=False)
        self.textgrid_tier_scroll = ctk.CTkScrollbar(tier_container, orientation="vertical", command=self.tree_textgrid_tiers.yview, width=12, height=120)
        self.tree_textgrid_tiers.configure(yscrollcommand=self.textgrid_tier_scroll.set)
        self.tree_textgrid_tiers.grid(row=0, column=0, sticky="nsew", padx=(1, 0), pady=1)
        self.textgrid_tier_scroll.grid(row=0, column=1, sticky="ns", padx=(0, 1), pady=1)
        self.textgrid_tier_empty_state = ctk.CTkFrame(tier_container, fg_color="transparent")
        ctk.CTkLabel(self.textgrid_tier_empty_state, text="选择文件后显示 IntervalTier 摘要", font=self.font_main, text_color=self.colors["muted"]).pack()
        self.textgrid_tier_empty_state.place(relx=0.5, rely=0.5, anchor="center")

        preview_card = self._make_card(left_panel)
        preview_card.pack(fill=tk.BOTH, expand=True, side=tk.TOP)
        preview_card.grid_columnconfigure(0, weight=1)
        preview_card.grid_rowconfigure(1, weight=1)

        preview_header = ctk.CTkFrame(preview_card, fg_color="transparent")
        preview_header.grid(row=0, column=0, sticky="ew", padx=20, pady=(18, 12))
        preview_header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(preview_header, text="转换预览", font=self.font_title, text_color=self.colors["text"]).grid(row=0, column=0, sticky="w")
        self.lbl_textgrid_preview_count = ctk.CTkLabel(preview_header, text="0 个条目", font=self.font_small, text_color=self.colors["muted"])
        self.lbl_textgrid_preview_count.grid(row=0, column=1, sticky="e")
        ctk.CTkLabel(
            preview_header,
            text="可多选条目并移动到右侧输入的临时组；导出时写入 groups / words / chars 三层。",
            font=self.font_small,
            text_color=self.colors["muted"],
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 0))

        preview_container = ctk.CTkFrame(preview_card, fg_color=self.colors["surface"], corner_radius=14, border_width=1, border_color=self.colors["border"])
        preview_container.grid(row=1, column=0, sticky="nsew", padx=20, pady=(0, 12))
        preview_container.grid_columnconfigure(0, weight=1)
        preview_container.grid_rowconfigure(0, weight=1)
        self.tree_textgrid_preview = ttk.Treeview(
            preview_container,
            columns=("label", "group", "core", "chars", "status"),
            show="headings",
            height=12,
            selectmode="extended",
        )
        self.tree_textgrid_preview.heading("label", text="条目")
        self.tree_textgrid_preview.heading("group", text="组名")
        self.tree_textgrid_preview.heading("core", text="核心边界")
        self.tree_textgrid_preview.heading("chars", text="字区间")
        self.tree_textgrid_preview.heading("status", text="状态")
        self.tree_textgrid_preview.column("label", width=120, anchor="w")
        self.tree_textgrid_preview.column("group", width=150, anchor="w")
        self.tree_textgrid_preview.column("core", width=130, anchor="center")
        self.tree_textgrid_preview.column("chars", width=70, anchor="center")
        self.tree_textgrid_preview.column("status", width=330, anchor="w")
        self.tree_textgrid_preview.configure(style="Converter.Treeview", takefocus=False)
        self.tree_textgrid_preview.tag_configure("warning", background="#FEF3C7")
        self.textgrid_preview_scroll = ctk.CTkScrollbar(preview_container, orientation="vertical", command=self.tree_textgrid_preview.yview, width=12)
        self.tree_textgrid_preview.configure(yscrollcommand=self.textgrid_preview_scroll.set)
        self.tree_textgrid_preview.grid(row=0, column=0, sticky="nsew", padx=(1, 0), pady=1)
        self.textgrid_preview_scroll.grid(row=0, column=1, sticky="ns", padx=(0, 1), pady=1)
        self.textgrid_preview_empty_state = ctk.CTkFrame(preview_container, fg_color="transparent")
        ctk.CTkLabel(self.textgrid_preview_empty_state, text="选择 TextGrid 并确认层映射后显示预览", font=self.font_main, text_color=self.colors["muted"]).pack()
        self.textgrid_preview_empty_state.place(relx=0.5, rely=0.5, anchor="center")

        preview_footer = ctk.CTkFrame(preview_card, fg_color="transparent")
        preview_footer.grid(row=2, column=0, sticky="ew", padx=20, pady=(0, 16))

        self.lbl_textgrid_status = ctk.CTkLabel(preview_footer, text="等待选择源文件。", font=self.font_small, text_color=self.colors["muted"], anchor="w")
        self.lbl_textgrid_status.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 10))

        self.btn_export_textgrid = self._make_button(
            preview_footer,
            "导出标准 TextGrid",
            self.export_converted_textgrid,
            tone="success",
            image=self.icons.get("save"),
            height=36,
        )
        self.btn_export_textgrid.pack(side=tk.RIGHT)
        self.btn_batch_export_textgrid = self._make_button(
            preview_footer,
            "批量导出",
            self.export_converted_textgrids_batch,
            tone="purple",
            height=36,
            width=96,
        )
        self.btn_batch_export_textgrid.pack(side=tk.RIGHT, padx=(0, 8))

        # 右栏：层映射 + 导出设置
        right_panel = ctk.CTkFrame(content, fg_color="transparent", width=340)
        right_panel.grid(row=0, column=1, sticky="nsew")
        right_panel.grid_propagate(False)
        right_panel.pack_propagate(False)

        mapping_card = self._make_card(right_panel, fg_color=self.colors["surface_soft"], width=340)
        mapping_card.pack(fill=tk.X, side=tk.TOP, pady=(0, 16))

        mapping_body = ctk.CTkFrame(mapping_card, fg_color="transparent")
        self.mapping_body = mapping_body

        self.mapping_is_collapsed = False
        self.export_is_collapsed = True

        def toggle_mapping():
            if self.mapping_is_collapsed:
                self.mapping_body.pack(fill=tk.X, padx=20, pady=(0, 10))
                self.header_mapping.toggle_btn.configure(text="折叠")
                self.mapping_is_collapsed = False

                if not self.export_is_collapsed:
                    self.export_body.pack_forget()
                    self.export_card.pack(fill=tk.X, side=tk.TOP, pady=(0, 16))
                    self.header_export.toggle_btn.configure(text="展开")
                    self.export_is_collapsed = True
            else:
                self.mapping_body.pack_forget()
                self.header_mapping.toggle_btn.configure(text="展开")
                self.mapping_is_collapsed = True

        def toggle_export():
            if self.export_is_collapsed:
                self.export_card.pack(fill=tk.BOTH, expand=True, side=tk.TOP)
                self.export_body.pack(fill=tk.BOTH, expand=True, padx=20, pady=(0, 12))
                self.header_export.toggle_btn.configure(text="折叠")
                self.export_is_collapsed = False

                if not self.mapping_is_collapsed:
                    self.mapping_body.pack_forget()
                    self.header_mapping.toggle_btn.configure(text="展开")
                    self.mapping_is_collapsed = True
            else:
                self.export_body.pack_forget()
                self.export_card.pack(fill=tk.X, side=tk.TOP, pady=(0, 16))
                self.header_export.toggle_btn.configure(text="展开")
                self.export_is_collapsed = True

        self.header_mapping = self._section_header(
            mapping_card,
            "层映射",
            "条目层给词项标签，核心层给实际分析边界。",
            icon_text="03",
            show_toggle=True,
            toggle_cmd=toggle_mapping,
        )

        self.mapping_body.pack(fill=tk.X, padx=20, pady=(0, 10))

        # 采用 Grid 布局横向排列下拉菜单，合并组层和条目层在同一行
        dropdowns_frame = ctk.CTkFrame(mapping_body, fg_color="transparent")
        dropdowns_frame.pack(fill=tk.X, pady=(0, 8))
        dropdowns_frame.grid_columnconfigure(0, weight=0)
        dropdowns_frame.grid_columnconfigure(1, weight=1)
        dropdowns_frame.grid_columnconfigure(2, weight=0)
        dropdowns_frame.grid_columnconfigure(3, weight=1)

        # 组层
        lbl_group = ctk.CTkLabel(dropdowns_frame, text="组层", font=self.font_small, text_color=self.colors["text_soft"], anchor="w")
        lbl_group.grid(row=0, column=0, sticky="w", padx=(0, 6), pady=4)
        self.combo_textgrid_group = self._make_converter_option(
            dropdowns_frame,
            [NONE_TIER_LABEL],
            command=lambda _value: self.refresh_textgrid_preview(),
            width=90,
        )
        self.combo_textgrid_group.configure(variable=self.var_textgrid_group_tier)
        self.combo_textgrid_group.grid(row=0, column=1, sticky="ew", padx=(0, 12), pady=4)

        # 条目层
        lbl_item = ctk.CTkLabel(dropdowns_frame, text="条目层", font=self.font_small, text_color=self.colors["text_soft"], anchor="w")
        lbl_item.grid(row=0, column=2, sticky="w", padx=(0, 6), pady=4)
        self.combo_textgrid_item = self._make_converter_option(
            dropdowns_frame,
            [NONE_TIER_LABEL],
            command=lambda _value: self.refresh_textgrid_preview(),
            width=90,
        )
        self.combo_textgrid_item.configure(variable=self.var_textgrid_item_tier)
        self.combo_textgrid_item.grid(row=0, column=3, sticky="ew", pady=4)

        # 核心层
        lbl_core = ctk.CTkLabel(dropdowns_frame, text="核心层", font=self.font_small, text_color=self.colors["text_soft"], anchor="w")
        lbl_core.grid(row=1, column=0, sticky="w", padx=(0, 6), pady=4)
        self.combo_textgrid_core = self._make_converter_option(
            dropdowns_frame,
            [NONE_TIER_LABEL],
            command=lambda _value: self.refresh_textgrid_preview(),
            width=190,
        )
        self.combo_textgrid_core.configure(variable=self.var_textgrid_core_tier)
        self.combo_textgrid_core.grid(row=1, column=1, columnspan=3, sticky="ew", pady=4)

        # 辅助字表栏：按钮并排，布局更紧凑
        wordlist_box = ctk.CTkFrame(mapping_body, fg_color=self.colors["surface"], corner_radius=12, border_width=1, border_color=self.colors["border"])
        wordlist_box.pack(fill=tk.X, pady=(0, 8))

        wl_header = ctk.CTkFrame(wordlist_box, fg_color="transparent")
        wl_header.pack(fill=tk.X, padx=12, pady=(8, 4))

        ctk.CTkLabel(
            wl_header,
            text="辅助字表",
            font=ctk.CTkFont(family=self.font_family, size=13, weight="bold"),
            text_color=self.colors["text"],
        ).pack(side=tk.LEFT)

        self.btn_wordlist_clear = self._make_button(
            wl_header,
            "清除",
            self.clear_textgrid_aux_wordlist,
            tone="ghost",
            height=26,
            width=50,
        )
        self.btn_wordlist_clear.pack(side=tk.RIGHT, padx=(4, 0))

        self.btn_wordlist_import = self._make_button(
            wl_header,
            "导入",
            self.import_textgrid_aux_wordlist,
            tone="secondary",
            height=26,
            width=50,
        )
        self.btn_wordlist_import.pack(side=tk.RIGHT)

        self.lbl_textgrid_wordlist = ctk.CTkLabel(
            wordlist_box,
            text="未导入；二字组将按时间顺序判断。",
            font=self.font_caption,
            text_color=self.colors["muted"],
            wraplength=260,
            justify="left",
            anchor="w",
        )
        self.lbl_textgrid_wordlist.pack(fill=tk.X, padx=12, pady=(0, 8))

        # 智能合并开关：缩短说明文本，取消不必要的高度占用
        switch_box = ctk.CTkFrame(mapping_body, fg_color=self.colors["surface"], corner_radius=12, border_width=1, border_color=self.colors["border"])
        switch_box.pack(fill=tk.X, pady=(2, 0))

        self.switch_textgrid_pair = ctk.CTkSwitch(
            switch_box,
            text="启用二字组智能合并",
            variable=self.var_textgrid_pair_mode,
            command=self.refresh_textgrid_preview,
            font=self.font_main,
            progress_color=self.colors["purple"],
            button_color="#475569",
            button_hover_color="#334155",
        )
        self.switch_textgrid_pair.pack(anchor="w", padx=12, pady=(8, 4))

        ctk.CTkLabel(
            switch_box,
            text="根据字表或时间顺序合并二字组",
            font=self.font_caption,
            text_color=self.colors["muted"],
            anchor="w",
        ).pack(fill=tk.X, padx=12, pady=(0, 8))

        self.export_card = self._make_card(right_panel, fg_color=self.colors["surface_soft"], width=340)
        self.export_card.pack(fill=tk.X, side=tk.TOP, pady=(0, 16))
        self.export_card.grid_propagate(False)

        self.header_export = self._section_header(
            self.export_card,
            "临时分组与导出",
            "无组层时可在这里把条目分入临时组。",
            icon_text="04",
            show_toggle=True,
            toggle_cmd=toggle_export,
        )
        self.header_export.toggle_btn.configure(text="展开")

        self.export_body = ctk.CTkFrame(self.export_card, fg_color="transparent")

        group_body = ctk.CTkFrame(self.export_body, fg_color="transparent")
        group_body.pack(fill=tk.X, padx=20, pady=(0, 12))
        ctk.CTkLabel(group_body, text="目标组名", font=self.font_small, text_color=self.colors["text_soft"]).pack(anchor="w")
        self.entry_textgrid_group = ctk.CTkEntry(
            group_body,
            placeholder_text=DEFAULT_GROUP_NAME,
            height=38,
            corner_radius=19,
            border_color=self.colors["border_strong"],
            fg_color=self.colors["surface"],
            text_color=self.colors["text"],
            font=self.font_main,
        )
        self.entry_textgrid_group.pack(fill=tk.X, pady=(6, 10))

        assign_row = ctk.CTkFrame(group_body, fg_color="transparent")
        assign_row.pack(fill=tk.X, pady=(0, 10))
        self._make_button(assign_row, "应用到选中", self.add_textgrid_group, tone="purple", height=34).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 8))
        self._make_button(assign_row, "清除选中", self.clear_textgrid_group_override, tone="secondary", height=34).pack(side=tk.LEFT, expand=True, fill=tk.X)

        diagnosis_box = ctk.CTkFrame(self.export_body, fg_color=self.colors["surface"], corner_radius=14, border_width=1, border_color=self.colors["border"])
        diagnosis_box.pack(fill=tk.BOTH, expand=True, padx=20, pady=(0, 12))
        ctk.CTkLabel(
            diagnosis_box,
            text="二字组体检",
            font=ctk.CTkFont(family=self.font_family, size=13, weight="bold"),
            text_color=self.colors["text"],
        ).pack(anchor="w", padx=14, pady=(12, 4))
        self.lbl_textgrid_diagnosis = ctk.CTkLabel(
            diagnosis_box,
            text="尚无可诊断内容。",
            font=self.font_small,
            text_color=self.colors["muted"],
            wraplength=278,
            justify="left",
            anchor="w",
        )
        self.lbl_textgrid_diagnosis.pack(fill=tk.BOTH, expand=True, padx=14, pady=(0, 12), anchor="nw")

        actions = ctk.CTkFrame(self.export_body, fg_color="transparent")
        actions.pack(side=tk.BOTTOM, fill=tk.X, padx=20, pady=20)
        self._make_button(actions, " 刷新预览", self.refresh_textgrid_preview, tone="secondary", image=self.icons.get("refresh_dark"), height=36).pack(fill=tk.X)

    def select_textgrid_source(self):
        paths = filedialog.askopenfilenames(
            filetypes=[
                ("Praat TextGrid", "*.TextGrid *.textgrid"),
                ("All Files", "*.*"),
            ]
        )
        if paths:
            self.load_textgrid_sources(list(paths))

    def load_textgrid_sources(self, paths):
        cleaned = []
        seen = set()
        for path in paths or []:
            normalized = os.path.normpath(str(path))
            seen_key = os.path.normcase(os.path.abspath(normalized)) if normalized else ""
            if normalized and normalized.lower().endswith(".textgrid") and seen_key not in seen:
                cleaned.append(normalized)
                seen.add(seen_key)
        if not cleaned:
            return
        self.textgrid_source_paths = cleaned
        self._fill_textgrid_files_tree(cleaned)
        self.load_textgrid_source(cleaned[0], update_sources=False)

    def load_textgrid_source(self, path, update_sources=True):
        if not path:
            return
        try:
            summary = inspect_textgrid(path)
        except Exception as e:
            messagebox.showerror("读取失败", f"无法读取 TextGrid：\n{e}")
            return

        if update_sources:
            normalized = os.path.normpath(str(path))
            self.textgrid_source_paths = [normalized]
            self._fill_textgrid_files_tree(self.textgrid_source_paths)
            path = normalized
        self.textgrid_source_path = path
        self.textgrid_summary = summary
        self.textgrid_group_overrides = {}
        self.textgrid_preview_items = []
        if hasattr(self, "lbl_textgrid_source"):
            total = len(getattr(self, "textgrid_source_paths", []) or [])
            if total > 1:
                current = (self.textgrid_source_paths.index(path) + 1) if path in self.textgrid_source_paths else 1
                text = f"当前 {current}/{total}：{self._short_path(path, max_parts=3)}"
            else:
                text = self._short_path(path, max_parts=4)
            self.lbl_textgrid_source.configure(text=text, text_color=self.colors["text_soft"])
        self._select_textgrid_file_in_tree(path)
        self._fill_textgrid_tiers_tree(summary.get("tiers", []))
        self.refresh_textgrid_tier_options()
        self.tabview.set(self.tab_textgrid_name)
        self._textgrid_status(f"已读取 {len(getattr(self, 'textgrid_source_paths', []) or [path])} 个 TextGrid，请确认层映射。", "muted")

    def _fill_textgrid_files_tree(self, paths):
        if not hasattr(self, "textgrid_file_list"):
            return
        self.textgrid_file_list_paths = list(paths or [])
        self.textgrid_file_list.delete(0, tk.END)
        basename_counts = {}
        for path in self.textgrid_file_list_paths:
            name = os.path.basename(path)
            basename_counts[name] = basename_counts.get(name, 0) + 1
        for idx, path in enumerate(paths or []):
            basename = os.path.basename(path)
            label = self._short_path(path, max_parts=2) if basename_counts.get(basename, 0) > 1 else basename
            self.textgrid_file_list.insert(tk.END, f"{idx + 1:02d}  {label}")
        if paths:
            self.textgrid_files_empty_state.place_forget()
        else:
            self.textgrid_files_empty_state.place(relx=0.5, rely=0.5, anchor="center")

    def _select_textgrid_file_in_tree(self, path):
        if not hasattr(self, "textgrid_file_list"):
            return
        target = os.path.normcase(os.path.abspath(path))
        for index, item_path in enumerate(getattr(self, "textgrid_file_list_paths", []) or []):
            if os.path.normcase(os.path.abspath(item_path)) == target:
                self.textgrid_file_list.selection_set(index, notify=False)
                self.textgrid_file_list.activate(index)
                break

    def on_textgrid_file_selected(self, _event=None):
        if not hasattr(self, "textgrid_file_list"):
            return
        selected = self.textgrid_file_list.curselection()
        if not selected:
            return
        index = selected[0]
        paths = getattr(self, "textgrid_file_list_paths", []) or []
        if index < 0 or index >= len(paths):
            return
        path = paths[index]
        if path and os.path.normpath(path) != os.path.normpath(getattr(self, "textgrid_source_path", "") or ""):
            self.load_textgrid_source(path, update_sources=False)

    def import_textgrid_aux_wordlist(self):
        path = filedialog.askopenfilename(
            filetypes=[
                ("支持的字表", "*.txt *.csv *.ptwl"),
                ("普通字表", "*.txt *.csv"),
                ("PhonTracer 高级字表", "*.ptwl"),
                ("All Files", "*.*"),
            ]
        )
        if not path:
            return
        try:
            records, groups_count = self._load_textgrid_aux_wordlist_records(path)
        except Exception as e:
            messagebox.showerror("读取失败", f"无法读取辅助字表：\n{e}")
            return
        if not records:
            messagebox.showwarning("提示", "辅助字表中没有可用词项。")
            return

        self.textgrid_wordlist_records = records
        self.textgrid_wordlist_path = path
        if hasattr(self, "lbl_textgrid_wordlist"):
            self.lbl_textgrid_wordlist.configure(
                text=f"已导入 {self._short_path(path, max_parts=2)}；{groups_count} 组，{len(records)} 项。",
                text_color=self.colors["success"],
            )
        self.refresh_textgrid_preview()

    def clear_textgrid_aux_wordlist(self):
        self.textgrid_wordlist_records = []
        self.textgrid_wordlist_path = None
        if hasattr(self, "lbl_textgrid_wordlist"):
            self.lbl_textgrid_wordlist.configure(
                text="未导入；二字组将按时间顺序判断。",
                text_color=self.colors["muted"],
            )
        self.refresh_textgrid_preview()

    def _load_textgrid_aux_wordlist_records(self, path):
        return load_auxiliary_wordlist_records(path)

    def refresh_textgrid_tier_options(self):
        summary = self.textgrid_summary or {}
        tiers = [tier for tier in summary.get("tiers", []) if tier.get("supported")]
        names = [str(tier.get("name", "")).strip() for tier in tiers if str(tier.get("name", "")).strip()]
        if not names:
            names = [NONE_TIER_LABEL]

        group_values = [NONE_TIER_LABEL] + [name for name in names if name != NONE_TIER_LABEL]
        recommendations = recommend_tier_names(summary) if summary else {}
        defaults = {
            "group": recommendations.get("group_tier") or NONE_TIER_LABEL,
            "item": recommendations.get("item_tier") or names[0],
            "core": recommendations.get("core_tier") or (names[1] if len(names) > 1 else names[0]),
        }

        for combo, values in (
            (getattr(self, "combo_textgrid_group", None), group_values),
            (getattr(self, "combo_textgrid_item", None), names),
            (getattr(self, "combo_textgrid_core", None), names),
        ):
            if combo is not None:
                combo.configure(values=values)

        self.var_textgrid_group_tier.set(defaults["group"] if defaults["group"] in group_values else NONE_TIER_LABEL)
        self.var_textgrid_item_tier.set(defaults["item"] if defaults["item"] in names else names[0])
        self.var_textgrid_core_tier.set(defaults["core"] if defaults["core"] in names else names[0])
        self.refresh_textgrid_preview()

    def _selected_textgrid_mapping(self):
        item_tier = (self.var_textgrid_item_tier.get() or "").strip()
        core_tier = (self.var_textgrid_core_tier.get() or "").strip()
        group_tier = (self.var_textgrid_group_tier.get() or "").strip()
        if item_tier in ("", NONE_TIER_LABEL) or core_tier in ("", NONE_TIER_LABEL):
            raise ValueError("请选择条目层和核心层。")
        if group_tier in ("", NONE_TIER_LABEL):
            group_tier = None
        return TextGridMapping(
            item_tier=item_tier,
            core_tier=core_tier,
            group_tier=group_tier,
            pair_mode=PAIR_MODE_ADJACENT if self.var_textgrid_pair_mode.get() else PAIR_MODE_NONE,
        )

    def refresh_textgrid_preview(self, *_args):
        if not getattr(self, "textgrid_source_path", None):
            self._fill_textgrid_preview_tree([])
            self._textgrid_status("等待选择源文件。", "muted")
            return
        try:
            mapping = self._selected_textgrid_mapping()
            preview = preview_textgrid_conversion(
                self.textgrid_source_path,
                mapping,
                group_overrides=self.textgrid_group_overrides,
                wordlist_records=self.textgrid_wordlist_records,
            )
        except ValueError as e:
            self.textgrid_preview_items = []
            self._fill_textgrid_preview_tree([])
            self.lbl_textgrid_diagnosis.configure(text="尚无可诊断内容。")
            self._textgrid_status(str(e), "warning")
            return
        except Exception as e:
            self.textgrid_preview_items = []
            self._fill_textgrid_preview_tree([])
            self.lbl_textgrid_diagnosis.configure(text=f"预览失败：{e}")
            self._textgrid_status(f"预览失败：{e}", "danger")
            return

        self.textgrid_preview_items = preview.items
        self._fill_textgrid_preview_tree(preview.items)
        self.lbl_textgrid_diagnosis.configure(text=self._format_textgrid_diagnosis(preview))
        warn_count = sum(len(item.warnings) for item in preview.items)
        if warn_count:
            self._textgrid_status(f"预览完成，发现 {warn_count} 条需要确认的提示。", "warning")
        else:
            self._textgrid_status("预览完成，可以导出。", "success")

    def _fill_textgrid_tiers_tree(self, tiers):
        if not hasattr(self, "tree_textgrid_tiers"):
            return
        self.tree_textgrid_tiers.delete(*self.tree_textgrid_tiers.get_children())
        for idx, tier in enumerate(tiers or []):
            samples = "，".join(tier.get("sample_labels", [])[:6]) or "空"
            count_text = f"{tier.get('non_empty_count', 0)}/{tier.get('count', 0)}"
            self.tree_textgrid_tiers.insert(
                "",
                tk.END,
                iid=f"tier_{idx}",
                values=(tier.get("name", ""), tier.get("type", ""), count_text, samples),
            )
        if tiers:
            self.textgrid_tier_empty_state.place_forget()
        else:
            self.textgrid_tier_empty_state.place(relx=0.5, rely=0.5, anchor="center")

    def _fill_textgrid_preview_tree(self, items):
        if not hasattr(self, "tree_textgrid_preview"):
            return
        previous_selection = set(self.tree_textgrid_preview.selection())
        self.tree_textgrid_preview.delete(*self.tree_textgrid_preview.get_children())
        for item in items or []:
            core_text = f"{item.core_start:.3f} - {item.core_end:.3f}"
            char_text = str(len(item.char_bounds))
            notes = []
            if item.match_note:
                notes.append(item.match_note)
            notes.extend(item.warnings[:2])
            status = "；".join(notes) if notes else "正常"
            tags = ("warning",) if item.warnings else ()
            self.tree_textgrid_preview.insert(
                "",
                tk.END,
                iid=item.id,
                values=(item.label, item.group, core_text, char_text, status),
                tags=tags,
            )
        if hasattr(self, "lbl_textgrid_preview_count"):
            self.lbl_textgrid_preview_count.configure(text=f"{len(items or [])} 个条目")
        if items:
            self.textgrid_preview_empty_state.place_forget()
        else:
            self.textgrid_preview_empty_state.place(relx=0.5, rely=0.5, anchor="center")
        for item_id in previous_selection:
            if self.tree_textgrid_preview.exists(item_id):
                self.tree_textgrid_preview.selection_add(item_id)

    def _textgrid_selected_item_ids(self):
        if not hasattr(self, "tree_textgrid_preview"):
            return []
        return list(self.tree_textgrid_preview.selection())

    def add_textgrid_group(self):
        self.assign_textgrid_group()

    def assign_textgrid_group(self):
        selected_ids = self._textgrid_selected_item_ids()
        if not selected_ids:
            messagebox.showwarning("提示", "请先在转换预览中选择一个或多个条目。")
            return
        group_name = self.entry_textgrid_group.get().strip() or DEFAULT_GROUP_NAME
        item_map = {item.id: item for item in self.textgrid_preview_items}
        for item_id in selected_ids:
            self.textgrid_group_overrides[item_id] = group_name
            item = item_map.get(item_id)
            if item:
                for source_id in item.source_ids:
                    self.textgrid_group_overrides[source_id] = group_name
        self.refresh_textgrid_preview()

    def clear_textgrid_group_override(self):
        selected_ids = self._textgrid_selected_item_ids()
        if not selected_ids:
            messagebox.showwarning("提示", "请先在转换预览中选择一个或多个条目。")
            return
        item_map = {item.id: item for item in self.textgrid_preview_items}
        for item_id in selected_ids:
            self.textgrid_group_overrides.pop(item_id, None)
            item = item_map.get(item_id)
            if item:
                for source_id in item.source_ids:
                    self.textgrid_group_overrides.pop(source_id, None)
        self.refresh_textgrid_preview()

    def export_converted_textgrid(self):
        if not getattr(self, "textgrid_source_path", None):
            messagebox.showwarning("提示", "请先选择一个 TextGrid 文件。")
            return
        try:
            mapping = self._selected_textgrid_mapping()
        except ValueError as e:
            messagebox.showwarning("提示", str(e))
            return

        source_dir = os.path.dirname(self.textgrid_source_path)
        source_name = os.path.splitext(os.path.basename(self.textgrid_source_path))[0]
        out_path = filedialog.asksaveasfilename(
            defaultextension=".TextGrid",
            initialdir=source_dir,
            initialfile=f"{source_name}_converted.TextGrid",
            filetypes=[
                ("Praat TextGrid", "*.TextGrid"),
                ("All Files", "*.*"),
            ],
        )
        if not out_path:
            return
        try:
            preview = convert_textgrid(
                self.textgrid_source_path,
                out_path,
                mapping,
                group_overrides=self.textgrid_group_overrides,
                wordlist_records=self.textgrid_wordlist_records,
            )
            self.textgrid_preview_items = preview.items
            self._fill_textgrid_preview_tree(preview.items)
            self.lbl_textgrid_diagnosis.configure(text=self._format_textgrid_diagnosis(preview))
            self._textgrid_status(f"已导出：{self._short_path(out_path, max_parts=3)}", "success")
            messagebox.showinfo("导出完成", f"已导出标准 TextGrid：\n{out_path}")
        except Exception as e:
            self._textgrid_status(f"导出失败：{e}", "danger")
            messagebox.showerror("导出失败", f"无法导出 TextGrid：\n{e}")

    def export_converted_textgrids_batch(self):
        paths = list(getattr(self, "textgrid_source_paths", []) or [])
        if not paths and getattr(self, "textgrid_source_path", None):
            paths = [self.textgrid_source_path]
        if not paths:
            messagebox.showwarning("提示", "请先导入一个或多个 TextGrid 文件。")
            return
        try:
            mapping = self._selected_textgrid_mapping()
        except ValueError as e:
            messagebox.showwarning("提示", str(e))
            return

        initialdir = os.path.dirname(paths[0]) if paths else os.getcwd()
        out_dir = filedialog.askdirectory(title="选择批量导出目录", initialdir=initialdir)
        if not out_dir:
            return

        batch_overrides = self._textgrid_group_overrides_for_batch(len(paths))
        exported = []
        failed = []
        current_preview = None
        current_source = os.path.normcase(os.path.abspath(getattr(self, "textgrid_source_path", "") or ""))
        for path in paths:
            try:
                source_name = os.path.splitext(os.path.basename(path))[0]
                out_path = self._unique_textgrid_output_path(out_dir, f"{source_name}_converted.TextGrid")
                preview = convert_textgrid(
                    path,
                    out_path,
                    mapping,
                    group_overrides=batch_overrides,
                    wordlist_records=self.textgrid_wordlist_records,
                )
                exported.append((out_path, preview))
                if os.path.normcase(os.path.abspath(path)) == current_source:
                    current_preview = preview
            except Exception as e:
                failed.append((path, str(e)))

        if exported:
            shown_preview = current_preview or exported[-1][1]
            self.textgrid_preview_items = shown_preview.items
            self._fill_textgrid_preview_tree(shown_preview.items)
            self.lbl_textgrid_diagnosis.configure(text=self._format_textgrid_diagnosis(shown_preview))
            self._textgrid_status(
                f"批量导出完成：成功 {len(exported)} 个，失败 {len(failed)} 个。",
                "warning" if failed else "success",
            )
        else:
            self._textgrid_status(f"批量导出失败：{len(failed)} 个文件未能转换。", "danger")

        lines = [f"成功：{len(exported)} 个", f"失败：{len(failed)} 个"]
        if len(paths) > 1 and self.textgrid_group_overrides and not batch_overrides:
            lines.append("提示：当前预览中的临时分组只适用于单文件导出，批量导出已按层映射和辅助字表处理。")
        if exported:
            lines.append(f"输出目录：{out_dir}")
        if failed:
            lines.append("")
            lines.append("失败文件：")
            for path, error in failed[:5]:
                lines.append(f"- {os.path.basename(path)}：{error}")
            if len(failed) > 5:
                lines.append(f"- 还有 {len(failed) - 5} 个失败文件未列出")

        if exported:
            messagebox.showinfo("批量导出完成", "\n".join(lines))
        else:
            messagebox.showerror("批量导出失败", "\n".join(lines))

    def _textgrid_group_overrides_for_batch(self, source_count):
        if source_count <= 1:
            return dict(self.textgrid_group_overrides)
        current_item_keys = set()
        for item in self.textgrid_preview_items:
            current_item_keys.add(item.id)
            current_item_keys.update(item.source_ids)
        return {
            key: value
            for key, value in self.textgrid_group_overrides.items()
            if key not in current_item_keys
        }

    def _unique_textgrid_output_path(self, out_dir, filename):
        base, ext = os.path.splitext(filename)
        ext = ext or ".TextGrid"
        candidate = os.path.join(out_dir, f"{base}{ext}")
        counter = 2
        while os.path.exists(candidate):
            candidate = os.path.join(out_dir, f"{base}_{counter}{ext}")
            counter += 1
        return candidate

    def _format_textgrid_diagnosis(self, preview):
        report = preview.tone_pair_report or {}
        lines = [f"转换预览：{len(preview.items)} 个条目。"]
        wordlist_matched = sum(1 for item in preview.items if getattr(item, "match_note", ""))
        if self.textgrid_wordlist_records:
            lines.append(f"辅助字表：{len(self.textgrid_wordlist_records)} 项；当前命中 {wordlist_matched} 个条目/二字组。")
        lines.append(
            f"二字组：可识别 {report.get('eligible_count', 0)} 个；"
            f"前调类 {report.get('front_count', 0)} 个，后调类 {report.get('back_count', 0)} 个，组合 {report.get('combo_count', 0)} 个。"
        )
        for message in report.get("messages", [])[:3]:
            lines.append(message)
        unique_warnings = []
        for warning in preview.warnings:
            if warning not in unique_warnings:
                unique_warnings.append(warning)
            if len(unique_warnings) >= 3:
                break
        if unique_warnings:
            lines.append("转换提示：" + "；".join(unique_warnings))
        return "\n".join(lines)

    def _textgrid_status(self, text, tone="muted"):
        if not hasattr(self, "lbl_textgrid_status"):
            return
        colors = {
            "muted": self.colors["muted"],
            "success": self.colors["success"],
            "warning": self.colors["warning_hover"],
            "danger": self.colors["danger"],
        }
        self.lbl_textgrid_status.configure(text=text, text_color=colors.get(tone, self.colors["muted"]))

    # ==========================
    # 交互回调与工具函数
    # ==========================

    def on_files_dropped(self, files):
        paths = [self._decode_drop_path(f) for f in files]
        self.after(0, lambda p=paths: self._handle_dropped_files(p))

    def _handle_dropped_files(self, paths):
        if not paths:
            return

        textgrid_files = [p for p in paths if p.lower().endswith(".textgrid")]
        if textgrid_files:
            self.tabview.set(self.tab_textgrid_name)
            self.load_textgrid_sources(textgrid_files)
            return

        # Check if there is any .teproj file dropped
        teproj_files = [p for p in paths if p.lower().endswith(('.teproj', '.zip'))]
        if teproj_files:
            self.tabview.set(self.tab_project_name)
            self.load_project_file(teproj_files[0])
            return

        audio_paths = [p for p in paths if p.lower().endswith(('.wav', '.mp3'))]
        if not audio_paths:
            return

        current_tab = self.tabview.get()
        if current_tab == self.tab_merge_name:
            added = False
            for p in audio_paths:
                if p not in self.merge_files:
                    self.merge_files.append(p)
                    self._insert_merge_file(p)
                    added = True
            if added:
                self.sync_merge_files_from_tree()
        else:
            self.split_source = audio_paths[0]
            self.lbl_split_source.configure(text=self._short_path(audio_paths[0], max_parts=3), text_color=self.colors["text_soft"])
            self.custom_segments = None  # 更换文件时重置编辑数据

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
                # x 从 0 开始，宽度为 Treeview 宽度
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
        last_error = None
        for encoding in encodings:
            try:
                with open(path, "r", encoding=encoding) as f:
                    return f.read()
            except UnicodeDecodeError as e:
                last_error = e
        if last_error:
            raise last_error
        with open(path, "r", encoding=encodings[0]) as f:
            return f.read()

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
            if looks_like_structured_wordlist_text(text):
                doc = build_document_from_structured_text(text, title=title)
            else:
                doc = build_document_from_v1_text(text, title=title)

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

        # 模糊匹配排序
        sorted_paths = []
        available_paths = list(self.merge_files)
        used_indices = set()

        for word in flat_words:
            idx = fuzzy_match_word_to_path(word, available_paths, used_indices=list(used_indices))
            if idx is not None:
                sorted_paths.append(available_paths[idx])
                used_indices.add(idx)

        # 将未匹配到的文件追加到末尾
        for i, p in enumerate(available_paths):
            if i not in used_indices:
                sorted_paths.append(p)

        # 更新 UI
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
                snd = parselmouth.Sound(self.split_source)
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

    def set_loading(self, state, msg="", indeterminate=False):
        if getattr(self, "_ui_thread_id", None) is not None and threading.get_ident() != self._ui_thread_id:
            self.after(0, lambda: self.set_loading(state, msg, indeterminate))
            return

        if state:
            if hasattr(self, 'lbl_status'):
                self.lbl_status.configure(text=msg or "处理中...", text_color="#1D4ED8")
            self.progress.grid()
            self.progress.stop()
            self.progress.configure(mode="indeterminate" if indeterminate else "determinate")
            if indeterminate:
                self.progress.start()
            else:
                self.progress.set(0.06)
            self.update_idletasks()
        else:
            if hasattr(self, 'lbl_status'):
                self.lbl_status.configure(text="就绪" if not msg else msg, text_color="#047857")
            self.progress.stop()
            self.progress.configure(mode="determinate")
            self.progress.set(0)
            self.progress.grid_remove()

    def update_progress(self, val, msg=None):
        def apply_update():
            if msg and hasattr(self, 'lbl_status'):
                self.lbl_status.configure(text=msg, text_color="#1D4ED8")
            self.progress.stop()
            self.progress.configure(mode="determinate")
            self.progress.set(val)

        self.after(0, apply_update)

    def update_report_progress(self, _val, msg):
        def apply_update():
            if hasattr(self, 'lbl_status'):
                self.lbl_status.configure(text=msg, text_color="#1D4ED8")

        self.after(0, apply_update)

    # ==========================
    # 可视化编辑器调用
    # ==========================

    def open_visual_splitter(self):
        if not self.split_source:
            return messagebox.showwarning("提示", "请先选择长音频源文件")

        def run():
            self.set_loading(True, "正在加载并检测音频区段...")
            try:
                snd = parselmouth.Sound(self.split_source)

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

    # ==========================
    # 核心任务：合并
    # ==========================

    def process_merge(self):
        if not self.merge_files: return messagebox.showwarning("提示", "请先添加音频文件")
        try: gap_sec = float(self.var_gap.get())
        except ValueError: return messagebox.showwarning("提示", "间隔时间必须为数字")

        out_path = filedialog.asksaveasfilename(defaultextension=".wav", filetypes=[("WAV Audio", "*.wav")], title="保存合并后的音频")
        if not out_path: return

        def run():
            self.set_loading(True, "正在合并音频...")
            try:
                target_sr = 44100
                all_vals = []
                gap_samples = int(target_sr * gap_sec)
                gap_array = np.zeros(gap_samples)

                total = len(self.merge_files)
                for i, path in enumerate(self.merge_files):
                    snd = parselmouth.Sound(path)
                    if snd.sampling_frequency != target_sr:
                        snd = snd.resample(target_sr)
                    all_vals.append(snd.values[0])
                    all_vals.append(gap_array)
                    self.update_progress((i+1)/total)

                if all_vals:
                    merged_vals = np.concatenate(all_vals[:-1])
                    merged_snd = parselmouth.Sound(np.array([merged_vals]), sampling_frequency=target_sr)
                    merged_snd.save(out_path, "WAV")

                self.after(0, lambda: messagebox.showinfo("成功", f"合并完成！\n保存在: {out_path}"))
            except Exception as e:
                error_message = f"合并失败:\n{e}"
                self.after(0, lambda msg=error_message: messagebox.showerror("错误", msg))
            finally:
                self.after(0, lambda: self.set_loading(False))

        start_safe_thread(run)

    # ==========================
    # 核心任务：拆分
    # ==========================

    def process_split(self):
        if not self.split_source: return messagebox.showwarning("提示", "请选择长音频源文件")

        raw_text = self.txt_wordlist.get("1.0", tk.END)
        current_text = raw_text.strip()
        if self.split_wordlist_document is not None and current_text == self.split_wordlist_text_snapshot:
            _groups, wordlist, _records = flatten_wordlist_document(self.split_wordlist_document)
        else:
            wordlist = parse_wordlist(raw_text)
        if not wordlist: return messagebox.showwarning("提示", "字表为空，请粘贴字表。")

        try: buffer_sec = float(self.var_buffer.get())
        except ValueError: return messagebox.showwarning("提示", "缓冲时间必须为数字")

        out_dir = filedialog.askdirectory(title="选择拆分后音频的保存文件夹")
        if not out_dir: return

        do_trim = self.var_trim.get()

        def run():
            self.set_loading(True, "正在分析并拆分音频...")
            try:
                snd = parselmouth.Sound(self.split_source)

                # 优先使用可视化编辑器微调后的边界，否则跑自动 VAD
                if self.custom_segments:
                    segs = self.custom_segments
                else:
                    segs = macroscopic_vad(snd, expected_count=len(wordlist))

                if not segs:
                    self.after(0, lambda: messagebox.showwarning("警告", "未能在音频中检测到任何有效发音段！"))
                    return

                if len(segs) != len(wordlist):
                    pass # 容错机制：数量不匹配时，只提取匹配的最小部分

                total = min(len(segs), len(wordlist))
                saved_files = []

                for i in range(total):
                    s, e = segs[i]
                    word = wordlist[i]

                    if do_trim:
                        part = snd.extract_part(from_time=s, to_time=e)
                        vals = part.values[0]
                        xs = part.xs()
                        s, e = trim_bounds_by_amplitude(s, e, xs, vals)

                    s = max(0, s - buffer_sec)
                    e = min(snd.get_total_duration(), e + buffer_sec)

                    if e > s:
                        extract = snd.extract_part(from_time=s, to_time=e)
                        safe_word = re.sub(r'[\\/*?:"<>|]', "", word)
                        out_file = os.path.join(out_dir, f"{str(i+1).zfill(3)}_{safe_word}.wav")
                        extract.save(out_file, "WAV")
                        saved_files.append(out_file)

                    self.update_progress((i+1)/total)

                self.after(0, lambda: messagebox.showinfo("成功", f"成功拆分 {total} 段音频并保存到:\n{out_dir}"))

            except Exception as e:
                error_message = f"拆分失败:\n{e}"
                self.after(0, lambda msg=error_message: messagebox.showerror("错误", msg))
            finally:
                self.after(0, lambda: self.set_loading(False))

        start_safe_thread(run)

    def build_wordlist_tab(self):
        content = ctk.CTkFrame(self.tab_wordlist, fg_color="transparent")
        content.pack(fill=tk.BOTH, expand=True, padx=20, pady=18)
        content.grid_columnconfigure(0, weight=1)
        content.grid_rowconfigure(0, weight=1)

        editor_card = self._make_card(content)
        editor_card.grid(row=0, column=0, sticky="nsew")
        editor_card.grid_rowconfigure(0, weight=1)
        editor_card.grid_columnconfigure(0, weight=1)
        self.wordlist_editor = VisualWordlistEditor(
            editor_card,
            colors=self.colors,
            font_family=self.font_family,
            bottom_actions=[
                ("导入文本并转换为高级字表", self.import_csv_into_wordlist_editor, "secondary"),
                ("复制Agent提示词", self.copy_wordlist_agent_prompt, "warning"),
            ],
            extra_actions=[
                ("同步到音频拆分", self.sync_wordlist_from_editor, "purple"),
            ],
        )
        self.wordlist_editor.grid(row=0, column=0, sticky="nsew", padx=14, pady=14)

    def copy_wordlist_agent_prompt(self):
        prompt = ADVANCED_WORDLIST_AGENT_PROMPT
        try:
            self.clipboard_clear()
            self.clipboard_append(prompt)
            messagebox.showinfo("成功", "高级字表 Agent 提示词已复制。")
        except Exception as e:
            messagebox.showerror("错误", f"复制失败：{e}")

    def open_wordlist_editor_ptwl(self):
        path = self.wordlist_editor.load_ptwl_dialog()
        if path:
            self.tabview.set(self.tab_wordlist_name)

    def import_v1_into_wordlist_editor(self):
        path = filedialog.askopenfilename(filetypes=[("Text Files", "*.txt"), ("All Files", "*.*")])
        if not path:
            return
        try:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    text = f.read()
            except UnicodeDecodeError:
                with open(path, "r", encoding="gbk") as f:
                    text = f.read()
            self.wordlist_editor.import_v1_text(text)
            self.tabview.set(self.tab_wordlist_name)
        except Exception as e:
            messagebox.showerror("错误", f"导入普通字表失败：{e}")

    def import_csv_into_wordlist_editor(self):
        try:
            self.wordlist_editor.import_csv_dialog()
            self.tabview.set(self.tab_wordlist_name)
        except Exception as e:
            messagebox.showerror("错误", f"导入文本失败：{e}")

    def export_wordlist_editor_v1(self):
        self.wordlist_editor.export_v1_dialog()

    def sync_wordlist_from_editor(self):
        if not hasattr(self, "wordlist_editor"):
            return messagebox.showwarning("提示", "字表编辑器尚未初始化。")
        doc = self.wordlist_editor.get_document()
        _groups, flat_words, _records = flatten_wordlist_document(doc)
        if not flat_words:
            return messagebox.showwarning("提示", "字表编辑器中没有可同步的词项。")
        text = document_to_v1_text(doc)
        self.txt_wordlist.delete("1.0", tk.END)
        self.txt_wordlist.insert("1.0", text)
        self._set_split_wordlist_document(doc, text)
        self.validate_wordlist()
        self.tabview.set(self.tab_split_name)
        messagebox.showinfo("完成", f"已同步 {len(flat_words)} 个词项到拆分页。")

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


# ==========================================
# ToolkitApp 自定义脚本相关方法
# ==========================================

    def build_script_tab(self):
        # 初始化私有变量与交互式工作台状态
        self.project_data = None
        self.project_namelist = []
        self.selected_script_id = None
        self.is_script_running = False
        self.run_cancel_event = None
        self.local_scripts = []
        self.script_output_dir = None
        self.script_figure_results = []
        self.previous_run_figure_results = []
        self.current_script_figure_index = 0
        self.script_table_results = []
        self.current_script_table_index = 0
        self.pending_project_patch = None
        self.last_script_run_record = None
        self.last_error_line_no = None
        self.active_script_editor_dialog = None
        self.current_script_params_schema = []
        self.script_param_widgets = {}
        self.code_drawer_expanded = False
        self.log_pane_collapsed = False
        self.active_artifact_mode = "figure"
        self.active_artifact_key = ("figure", 0)
        self.canvas_zoom_scale = 1.0
        self.canvas_fit_mode = True
        self.canvas_pan_offset_x = 0.0
        self.canvas_pan_offset_y = 0.0
        self.canvas_split_compare_mode = False
        self.current_canvas_pil_image = None
        self.compare_canvas_pil_image = None
        self._canvas_tk_img = None
        self._canvas_compare_tk_img = None
        self._canvas_resize_after_id = None
        self._canvas_drag_start_pos = None
        self._table_sort_state = {}
        self._clipboard_image_buffer = None
        self.var_auto_archive_script_run = tk.BooleanVar(value=False)
        self.var_auto_apply_patch = tk.BooleanVar(value=False)

        # 生成白色的 play 和 pause 图标
        for icon_key, filename in [("play_white", "play.png"), ("pause_white", "pause.png")]:
            try:
                icon_path = os.path.join("assets", "icons", filename)
                if not os.path.exists(icon_path):
                    icon_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets", "icons", filename)
                if os.path.exists(icon_path):
                    from PIL import Image
                    img = Image.open(icon_path).convert("RGBA")
                    data = np.array(img)
                    data[:, :, 0] = 255
                    data[:, :, 1] = 255
                    data[:, :, 2] = 255
                    img_white = Image.fromarray(data)
                    self.icons[icon_key] = ctk.CTkImage(light_image=img_white, dark_image=img_white, size=(20, 20))
            except Exception:
                pass

        content = ctk.CTkFrame(self.tab_script, fg_color="transparent")
        content.pack(fill=tk.BOTH, expand=True, padx=16, pady=12)
        content.grid_columnconfigure(0, weight=0)
        content.grid_columnconfigure(1, weight=1)
        content.grid_rowconfigure(0, weight=1)

        # ----------------------------------------------------
        # 左栏：脚本列表与管理
        # ----------------------------------------------------
        left_panel = self._make_card(content, width=285)
        left_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        left_panel.pack_propagate(False)

        self._section_header(left_panel, "脚本库", subtitle=None, icon_text="05")

        # 搜索框
        search_frame = ctk.CTkFrame(left_panel, fg_color="transparent")
        search_frame.pack(fill=tk.X, padx=12, pady=(0, 4))
        self.entry_script_search = ctk.CTkEntry(
            search_frame,
            placeholder_text="搜索脚本...",
            height=34,
            corner_radius=17,
            font=ctk.CTkFont(family="Microsoft YaHei", size=12),
            fg_color=("#F9FAFB", "#262930"),
            border_color=("#D1D5DB", "#374151"),
            text_color=("#111827", "#F9FAFB")
        )
        self.entry_script_search.pack(fill=tk.X)
        self.entry_script_search.bind("<KeyRelease>", lambda e: self.load_scripts_to_tree())

        # 类型过滤
        self.combo_script_type = ctk.CTkOptionMenu(
            left_panel,
            values=["全部类型", "图表脚本", "数据处理脚本"],
            command=self.on_script_filter_changed,
            fg_color=("#F3F4F6", "#374151"),
            text_color=("#1F2937", "#E5E7EB"),
            button_color=("#F3F4F6", "#374151"),
            button_hover_color=("#E5E7EB", "#4B5563"),
            height=30,
            corner_radius=15
        )
        self.combo_script_type.pack(fill=tk.X, padx=12, pady=4)
        _apply_custom_arrow(self.combo_script_type)

        # 列表框
        tree_frame = ctk.CTkFrame(left_panel, fg_color="transparent")
        tree_frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=4)

        self.script_tree = CTkScriptList(tree_frame, font_family=self.font_family, colors=self.colors)
        self.script_tree.pack(fill=tk.BOTH, expand=True)
        self.script_tree.bind("<<TreeviewSelect>>", self.on_script_selected)
        self.script_tree.set_context_callback(self.show_script_context_menu)

        # 常用操作按钮
        act_frame1 = ctk.CTkFrame(left_panel, fg_color="transparent")
        act_frame1.pack(fill=tk.X, padx=12, pady=4)
        self._make_button(act_frame1, "新建", self.on_new_script, tone="primary", height=30).pack(
            side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 4)
        )
        self._make_button(act_frame1, "编辑", self.on_edit_script, tone="secondary", height=30).pack(
            side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 4)
        )
        self._make_button(
            act_frame1,
            "删除",
            self.on_delete_script,
            tone="danger",
            height=30,
            fg_color=self.colors["danger_soft"],
            hover_color="#FECACA",
            text_color=self.colors["danger_hover"],
        ).pack(side=tk.LEFT, expand=True, fill=tk.X)

        act_frame2 = ctk.CTkFrame(left_panel, fg_color="transparent")
        act_frame2.pack(fill=tk.X, padx=12, pady=(4, 14))
        self._make_button(act_frame2, "导入脚本", self.on_import_script, tone="secondary", height=30).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 4))
        self._make_button(act_frame2, "导出脚本", self.on_export_script, tone="secondary", height=30).pack(side=tk.LEFT, expand=True, fill=tk.X)

        # ----------------------------------------------------
        # 右栏：紧凑控制条 + 动态参数 + 交互式预览工作台 (占 >75% 空间)
        # ----------------------------------------------------
        right_panel = self._make_card(content)
        right_panel.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        self.script_right_panel = right_panel

        # 1. 顶部紧凑工具条（整合脚本名称、类型标签、独立按钮行、描述、工程状态与切换）
        top_bar = ctk.CTkFrame(
            right_panel,
            fg_color=self.colors["surface_soft"],
            corner_radius=10,
            border_width=1,
            border_color=self.colors["border"],
        )
        top_bar.pack(fill=tk.X, padx=14, pady=(10, 4))
        top_bar.grid_columnconfigure(0, weight=3)
        top_bar.grid_columnconfigure(1, weight=2, minsize=270)
        self.script_top_bar = top_bar

        script_meta_box = ctk.CTkFrame(top_bar, fg_color="transparent")
        script_meta_box.grid(row=0, column=0, sticky="nsew", padx=(12, 8), pady=6)

        # 第一行：脚本标题 + 类型徽章
        title_line = ctk.CTkFrame(script_meta_box, fg_color="transparent")
        title_line.pack(fill=tk.X)

        self.lbl_selected_script_name = ctk.CTkLabel(
            title_line,
            text="当前脚本：未选择",
            font=ctk.CTkFont(family=self.font_family, size=13, weight="bold"),
            text_color=self.colors["text"],
            anchor="w",
        )
        self.lbl_selected_script_name.pack(side=tk.LEFT, padx=(0, 6))

        self.lbl_selected_script_type_tag = ctk.CTkLabel(
            title_line,
            text="图表脚本",
            font=ctk.CTkFont(family=self.font_family, size=11, weight="bold"),
            fg_color=self.colors["primary_soft"],
            text_color=self.colors["primary"],
            corner_radius=8,
            padx=8,
            pady=1,
        )
        self.lbl_selected_script_type_tag.pack(side=tk.LEFT, padx=(0, 6))

        # 第二行：独立操作按钮行（▸ 源码预览 + ✨ AI提示词 另起一行，不与标题同行）
        meta_action_line = ctk.CTkFrame(script_meta_box, fg_color="transparent")
        meta_action_line.pack(fill=tk.X, pady=(4, 2))
        self.script_meta_action_line = meta_action_line

        self.btn_toggle_code_drawer = ctk.CTkButton(
            meta_action_line,
            text="▸ 源码预览",
            width=88,
            height=24,
            corner_radius=8,
            font=ctk.CTkFont(family=self.font_family, size=11),
            fg_color="#FFFFFF",
            border_width=1,
            border_color=self.colors["border"],
            text_color=self.colors["text_soft"],
            hover_color="#F1F5F9",
            command=self.toggle_code_preview_drawer,
        )
        self.btn_toggle_code_drawer.pack(side=tk.LEFT, padx=(0, 6))

        # 保留实例属性兼容外部访问，不在顶栏重复 pack（左栏底部已有 [编辑]）
        self.btn_quick_edit_script = ctk.CTkButton(
            meta_action_line,
            text="编辑代码",
            width=72,
            height=24,
            corner_radius=8,
            font=ctk.CTkFont(family=self.font_family, size=11),
            fg_color=self.colors["purple_soft"],
            text_color=self.colors["purple"],
            hover_color=self.colors["border_strong"],
            command=self.on_edit_script,
        )

        self.btn_copy_prompt = ctk.CTkButton(
            meta_action_line,
            text="✨ AI提示词",
            width=88,
            height=24,
            corner_radius=8,
            font=ctk.CTkFont(family=self.font_family, size=11),
            fg_color=self.colors["primary_soft"],
            text_color=self.colors["primary"],
            hover_color=self.colors["border_strong"],
            command=self.show_prompt_dialog,
        )
        self.btn_copy_prompt.pack(side=tk.LEFT)

        # 第三行：脚本功能描述（DPI 自适应换行，绝不截断）
        self.lbl_selected_script_desc = ctk.CTkLabel(
            script_meta_box,
            text="描述：请从左侧列表中选择一个脚本，或者点击“新建”创建新脚本。",
            font=self.font_caption,
            text_color=self.colors["muted"],
            anchor="w",
            justify="left",
            wraplength=360,
        )
        self.lbl_selected_script_desc.pack(fill=tk.X, pady=(2, 0))
        self._bind_adaptive_wrap(
            self.lbl_selected_script_desc,
            script_meta_box,
            reserved_width=16,
            min_wrap=180,
            extra_containers=(top_bar,),
        )

        # 右侧紧凑工程卡片（左文字状态自适应换行 + 右固定宽度切换按钮，杜绝挤压）
        project_card = ctk.CTkFrame(top_bar, fg_color="transparent")
        project_card.grid(row=0, column=1, sticky="nsew", padx=(6, 12), pady=6)
        project_card.grid_columnconfigure(0, weight=1)
        project_card.grid_columnconfigure(1, weight=0)

        proj_info_box = ctk.CTkFrame(project_card, fg_color="transparent")
        proj_info_box.grid(row=0, column=0, sticky="ew", padx=(0, 8))

        self.lbl_script_project_status = ctk.CTkLabel(
            proj_info_box,
            text="未加载工程",
            font=ctk.CTkFont(family=self.font_family, size=12, weight="bold"),
            text_color=self.colors["muted"],
            anchor="w",
            justify="left",
            wraplength=220,
        )
        self.lbl_script_project_status.pack(fill=tk.X, anchor="w")
        self._bind_adaptive_wrap(
            self.lbl_script_project_status,
            proj_info_box,
            reserved_width=10,
            min_wrap=150,
            extra_containers=(top_bar,),
        )

        self.lbl_script_project_detail = ctk.CTkLabel(
            proj_info_box,
            text="可在此处或“工程预览”页选择同一个 .teproj 工程。",
            font=self.font_caption,
            text_color=self.colors["muted"],
            anchor="w",
            justify="left",
            wraplength=220,
        )
        self.lbl_script_project_detail.pack(fill=tk.X, pady=(1, 0), anchor="w")
        self._bind_adaptive_wrap(
            self.lbl_script_project_detail,
            proj_info_box,
            reserved_width=10,
            min_wrap=150,
            extra_containers=(top_bar,),
        )

        # 保留实例属性兼容外部访问，不在顶栏重复 pack（顶部 Tab 栏已有 [工程预览]）
        self.btn_script_show_project = self._make_button(
            project_card,
            "查看工程预览",
            lambda: self.tabview.set(self.tab_project_name),
            tone="secondary",
            height=26,
            width=92,
        )

        self.btn_script_load_project = self._make_button(
            project_card,
            "选择工程",
            self.select_project_file,
            tone="primary",
            height=30,
            width=96,
        )
        self.btn_script_load_project.grid(row=0, column=1, sticky="e")

        # 1b. 默认收起的可折叠源码预览抽屉（保留 self.txt_script_code_preview 引用）
        self.code_preview_drawer = ctk.CTkFrame(
            right_panel,
            fg_color=self.colors["surface_soft"],
            corner_radius=8,
            border_width=1,
            border_color=self.colors["border"],
        )
        self.txt_script_code_preview = ctk.CTkTextbox(
            self.code_preview_drawer,
            height=110,
            font=ctk.CTkFont(family="Consolas", size=11),
            wrap="none",
            border_width=1,
            border_color=("#D1D5DB", "#374151"),
            fg_color=("#FFFFFF", "#262930"),
            text_color=("#111827", "#F9FAFB")
        )
        self.txt_script_code_preview.pack(fill=tk.X, padx=10, pady=6)
        self.txt_script_code_preview.configure(state="disabled")
        bind_text_context_menu(
            self.txt_script_code_preview,
            readonly=True,
            extra_items=[("编辑脚本代码...", self.on_edit_script)],
        )
        # 默认收起抽屉
        self.code_preview_drawer.pack_forget()

        def speed_up_preview_scroll(event):
            scroll_units = int(-1 * (event.delta / 120) * 4)
            self.txt_script_code_preview.yview_scroll(scroll_units, "units")
            return "break"
        self.txt_script_code_preview.bind("<MouseWheel>", speed_up_preview_scroll)
        if hasattr(self.txt_script_code_preview, "_textbox"):
            self.txt_script_code_preview._textbox.bind("<MouseWheel>", speed_up_preview_scroll)

        # 2. 运行控制栏 & 动态 PARAMS 参数控件栏 & 工程补丁按钮
        self.script_control_bar = ctk.CTkFrame(right_panel, fg_color="transparent")
        self.script_control_bar.pack(fill=tk.X, padx=14, pady=(2, 4))

        self.btn_run_script = self._make_button(
            self.script_control_bar, " 运行脚本", self.run_current_script,
            tone="primary", image=self.icons.get("play_white"), height=32, width=96
        )
        self.btn_run_script.pack(side=tk.LEFT, padx=(0, 6))

        self.btn_stop_script = self._make_button(
            self.script_control_bar, " 停止", self.stop_current_script,
            tone="danger", image=self.icons.get("pause_white"), height=32, width=72
        )
        self.btn_stop_script.pack(side=tk.LEFT, padx=(0, 6))
        self.btn_stop_script.configure(state="disabled")

        self.btn_open_script_output = self._make_button(
            self.script_control_bar, " 打开目录", self.open_script_output_dir,
            tone="secondary", image=self.icons.get("folder_dark"), height=32, width=88
        )
        self.btn_open_script_output.pack(side=tk.LEFT, padx=(0, 8))
        self.btn_open_script_output.configure(state="disabled")

        # 工程补丁操作条（仅在选中数据处理脚本或返回 ProjectPatchResult 时显示）
        self.project_patch_bar = ctk.CTkFrame(self.script_control_bar, fg_color="transparent")

        self.btn_apply_project_patch = ctk.CTkButton(
            self.project_patch_bar,
            text="应用补丁并另存工程 (.teproj)",
            height=30,
            corner_radius=15,
            font=ctk.CTkFont(family=self.font_family, size=12, weight="bold"),
            fg_color="#059669",
            hover_color="#047857",
            text_color="#FFFFFF",
            state="disabled",
            command=self.apply_pending_project_patch,
        )
        self.btn_apply_project_patch.pack(side=tk.LEFT, padx=(0, 8))

        self.chk_auto_apply_patch = self._make_checkbox(
            self.project_patch_bar,
            text="自动应用补丁",
            variable=self.var_auto_apply_patch,
            width=96,
        )
        self.chk_auto_apply_patch.pack(side=tk.LEFT)
        self.project_patch_bar.pack_forget()

        # 动态 PARAMS 参数行（选中含 PARAMS 的脚本时自动生成）
        self.script_params_frame = ctk.CTkFrame(
            right_panel,
            fg_color=self.colors["surface_soft"],
            corner_radius=8,
            border_width=1,
            border_color=self.colors["border"],
        )

        # 3. 输出预览工作台 & 运行日志区（使用 tk.PanedWindow 将 75%+ 空间交给预览工作台）
        output_frame = ctk.CTkFrame(right_panel, fg_color="transparent")
        output_frame.pack(fill=tk.BOTH, expand=True, padx=14, pady=(2, 12))
        self.script_output_frame = output_frame

        paned_bg = "#E5E7EB" if ctk.get_appearance_mode().lower() != "dark" else "#374151"
        self.script_workspace_paned = tk.PanedWindow(
            output_frame,
            orient=tk.HORIZONTAL,
            sashwidth=6,
            sashrelief=tk.FLAT,
            bg=paned_bg,
            bd=0,
            opaqueresize=True,
        )
        self.script_workspace_paned.pack(fill=tk.BOTH, expand=True)

        # 左侧主预览工作台（占 75% 以上空间）
        preview_box = ctk.CTkFrame(
            self.script_workspace_paned,
            fg_color=self.colors["surface_soft"],
            corner_radius=10,
            border_width=1,
            border_color=self.colors["border"],
        )
        self.script_preview_box = preview_box
        preview_box.grid_rowconfigure(2, weight=1)
        preview_box.grid_columnconfigure(0, weight=1)

        # Row 0: 多产物胶囊标签栏 + 翻页导航 + 收起/展开日志按钮
        top_artifact_bar = ctk.CTkFrame(preview_box, fg_color="transparent", height=28)
        top_artifact_bar.grid(row=0, column=0, sticky="ew", padx=8, pady=(6, 2))
        top_artifact_bar.grid_columnconfigure(0, weight=1)

        self.artifact_tabs_frame = ctk.CTkFrame(top_artifact_bar, fg_color="transparent", height=28)
        self.artifact_tabs_frame.grid(row=0, column=0, sticky="w")
        self.artifact_tabs_frame.grid_remove()
        self.artifact_tab_buttons = []

        # 兼容保留 script_preview_nav / btn_script_prev_fig / lbl_script_fig_page / btn_script_next_fig
        self.script_preview_nav = ctk.CTkFrame(top_artifact_bar, fg_color="transparent", height=28)
        self.script_preview_nav.grid(row=0, column=1, sticky="e", padx=(6, 6))
        self.btn_script_prev_fig = ctk.CTkButton(
            self.script_preview_nav, text="上一张", width=62, height=24,
            corner_radius=8, fg_color="#FFFFFF", border_width=1,
            border_color=self.colors["border"], text_color=self.colors["text_soft"],
            hover_color="#F1F5F9", command=self.show_prev_script_figure
        )
        self.btn_script_prev_fig.grid(row=0, column=0, padx=(0, 4))
        self.lbl_script_fig_page = ctk.CTkLabel(
            self.script_preview_nav, text="图表: 0/0", font=self.font_caption,
            text_color=self.colors["muted"], anchor="center"
        )
        self.lbl_script_fig_page.grid(row=0, column=1, padx=4)
        self.btn_script_next_fig = ctk.CTkButton(
            self.script_preview_nav, text="下一张", width=62, height=24,
            corner_radius=8, fg_color="#FFFFFF", border_width=1,
            border_color=self.colors["border"], text_color=self.colors["text_soft"],
            hover_color="#F1F5F9", command=self.show_next_script_figure
        )
        self.btn_script_next_fig.grid(row=0, column=2, padx=(4, 0))
        self.script_preview_nav.grid_remove()

        self.btn_toggle_log_pane = ctk.CTkButton(
            top_artifact_bar,
            text="收起日志 ▸",
            width=82,
            height=26,
            corner_radius=8,
            font=ctk.CTkFont(family=self.font_family, size=11),
            fg_color="#FFFFFF",
            border_width=1,
            border_color=self.colors["border"],
            text_color=self.colors["text_soft"],
            hover_color="#F1F5F9",
            command=self.toggle_script_log_pane,
        )
        self.btn_toggle_log_pane.grid(row=0, column=2, sticky="e")

        # Row 1: 画布交互工具栏（仅在 figure 模式下显示，空状态/占位态自动隐藏）
        self.canvas_toolbar = ctk.CTkFrame(preview_box, fg_color="transparent", height=30)
        self.canvas_toolbar.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 4))

        def _make_tb_btn(text, cmd, width=68):
            b = ctk.CTkButton(
                self.canvas_toolbar,
                text=text,
                width=width,
                height=26,
                corner_radius=8,
                font=ctk.CTkFont(family=self.font_family, size=11),
                fg_color="#FFFFFF",
                border_width=1,
                border_color=self.colors["border"],
                text_color=self.colors["text_soft"],
                hover_color="#F1F5F9",
                command=cmd,
            )
            b.pack(side=tk.LEFT, padx=(0, 4))
            return b

        self.btn_canvas_fit = _make_tb_btn("适应窗口", self.canvas_zoom_fit, width=68)
        self.btn_canvas_original = _make_tb_btn("1:1 原始", self.canvas_zoom_original, width=68)
        self.btn_canvas_zoom_in = _make_tb_btn("放大+", self.canvas_zoom_in, width=54)
        self.btn_canvas_zoom_out = _make_tb_btn("缩小-", self.canvas_zoom_out, width=54)
        self.lbl_canvas_zoom = ctk.CTkLabel(
            self.canvas_toolbar,
            text="100%",
            font=ctk.CTkFont(family="Consolas", size=11, weight="bold"),
            text_color=self.colors["muted"],
            width=52,
        )
        self.lbl_canvas_zoom.pack(side=tk.LEFT, padx=(2, 6))

        self.btn_canvas_split_compare = _make_tb_btn("双图对比", self.toggle_canvas_split_compare, width=72)
        self.btn_canvas_copy_img = _make_tb_btn("复制图片", self.copy_current_chart_to_clipboard, width=72)
        self.btn_canvas_save_as = _make_tb_btn("另存为...", self.save_current_chart_as, width=72)

        # Row 2: 预览视口容器（容纳交互式画布、内置表格预览器、文件列表与占位文本）
        self.preview_viewport = ctk.CTkFrame(preview_box, fg_color="transparent")
        self.preview_viewport.grid(row=2, column=0, sticky="nsew", padx=8, pady=(0, 8))
        self.preview_viewport.grid_rowconfigure(0, weight=1)
        self.preview_viewport.grid_columnconfigure(0, weight=1)

        # 交互式高清预览画布 (tk.Canvas)
        self.canvas_chart_preview = tk.Canvas(
            self.preview_viewport,
            bg="#F8FAFC",
            highlightthickness=1,
            highlightbackground="#E2E8F0",
            cursor="fleur",
        )
        self.canvas_chart_preview.grid(row=0, column=0, sticky="nsew")
        self.canvas_chart_preview.bind("<Configure>", self._on_canvas_configure)
        self.canvas_chart_preview.bind("<Enter>", lambda _e: self.canvas_chart_preview.focus_set())
        self.canvas_chart_preview.bind("<MouseWheel>", self.on_canvas_mousewheel)
        self.canvas_chart_preview.bind("<Button-4>", self.on_canvas_mousewheel)
        self.canvas_chart_preview.bind("<Button-5>", self.on_canvas_mousewheel)
        self.canvas_chart_preview.bind("<ButtonPress-1>", self.on_canvas_drag_start)
        self.canvas_chart_preview.bind("<B1-Motion>", self.on_canvas_drag_move)
        self.canvas_chart_preview.bind("<Double-Button-1>", self.on_canvas_double_click)
        self.canvas_chart_preview.bind("<Button-3>", self.show_canvas_context_menu)
        self.canvas_chart_preview.bind("<Button-2>", self.show_canvas_context_menu)
        self.canvas_chart_preview.bind("<Control-c>", lambda _e: self.copy_current_chart_to_clipboard())
        self.canvas_chart_preview.bind("<Control-C>", lambda _e: self.copy_current_chart_to_clipboard())

        # 保留 self.lbl_chart_preview 兼容无头测试与纯文本占位（不再绑定单击弹出系统外部看图器）
        self.lbl_chart_preview = ctk.CTkLabel(
            self.preview_viewport,
            text="暂无图表预览\n点击上方 [运行脚本] 生成图表或数据表预览",
            font=ctk.CTkFont(family=self.font_family, size=13),
            text_color=self.colors["muted"],
            justify="center",
        )
        self.lbl_chart_preview.grid(row=0, column=0, sticky="nsew")

        # 内置表格预览器（ttk.Treeview）
        self.table_preview_container = ctk.CTkFrame(self.preview_viewport, fg_color="transparent")
        self.table_preview_container.grid(row=0, column=0, sticky="nsew")
        self.table_preview_container.grid_rowconfigure(1, weight=1)
        self.table_preview_container.grid_columnconfigure(0, weight=1)

        table_bar = ctk.CTkFrame(self.table_preview_container, fg_color="transparent")
        table_bar.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 4))

        self.lbl_table_summary = ctk.CTkLabel(
            table_bar,
            text="数据表预览 (点击表头可按列排序)",
            font=ctk.CTkFont(family=self.font_family, size=11, weight="bold"),
            text_color=self.colors["muted"],
        )
        self.lbl_table_summary.pack(side=tk.LEFT)

        self.btn_save_table_as = ctk.CTkButton(
            table_bar,
            text="另存为 CSV/XLSX",
            width=116,
            height=25,
            corner_radius=12,
            font=ctk.CTkFont(family=self.font_family, size=11),
            fg_color=self.colors["primary_soft"],
            text_color=self.colors["primary"],
            hover_color=self.colors["border_strong"],
            command=self.save_current_table_as,
        )
        self.btn_save_table_as.pack(side=tk.RIGHT, padx=(4, 0))

        self.btn_copy_table_tsv = ctk.CTkButton(
            table_bar,
            text="复制全表 (TSV)",
            width=108,
            height=25,
            corner_radius=12,
            font=ctk.CTkFont(family=self.font_family, size=11),
            fg_color=self.colors["success_soft"],
            text_color=self.colors["success"],
            hover_color=self.colors["border_strong"],
            command=self.copy_current_table_tsv_to_clipboard,
        )
        self.btn_copy_table_tsv.pack(side=tk.RIGHT)

        self.script_table_tree = ttk.Treeview(self.table_preview_container, show="headings", selectmode="extended")
        self.script_table_tree.grid(row=1, column=0, sticky="nsew")
        tbl_vsb = ttk.Scrollbar(self.table_preview_container, orient="vertical", command=self.script_table_tree.yview)
        tbl_vsb.grid(row=1, column=1, sticky="ns")
        tbl_hsb = ttk.Scrollbar(self.table_preview_container, orient="horizontal", command=self.script_table_tree.xview)
        tbl_hsb.grid(row=2, column=0, sticky="ew")
        self.script_table_tree.configure(yscrollcommand=tbl_vsb.set, xscrollcommand=tbl_hsb.set)
        self.table_preview_container.grid_remove()

        # 生成文件列表的滚动区域（默认隐藏）
        self.script_file_list_frame = ctk.CTkScrollableFrame(
            self.preview_viewport,
            fg_color="transparent",
            label_text="已生成的文件列表 (点击打开文件夹)",
            label_font=ctk.CTkFont(family=self.font_family, size=11, weight="bold"),
            label_text_color=self.colors["muted"]
        )
        self.script_file_list_frame.grid(row=0, column=0, sticky="nsew")
        self.script_file_list_frame.grid_remove()

        if hasattr(self.script_file_list_frame, "_label") and self.script_file_list_frame._label:
            self.script_file_list_frame._label.configure(cursor="hand2")
            self.script_file_list_frame._label.bind("<Button-1>", lambda e: self.open_script_output_dir())
            if hasattr(self.script_file_list_frame._label, "_label") and self.script_file_list_frame._label._label:
                self.script_file_list_frame._label._label.configure(cursor="hand2")
                self.script_file_list_frame._label._label.bind("<Button-1>", lambda e: self.open_script_output_dir())

        # 右侧运行日志与归档分栏
        log_box = ctk.CTkFrame(
            self.script_workspace_paned,
            fg_color=self.colors["surface_soft"],
            corner_radius=10,
            border_width=1,
            border_color=self.colors["border"],
        )
        self.script_log_box = log_box
        log_box.grid_rowconfigure(1, weight=1)
        log_box.grid_columnconfigure(0, weight=1)

        log_header_frame = ctk.CTkFrame(log_box, fg_color="transparent")
        log_header_frame.grid(row=0, column=0, sticky="ew", padx=8, pady=(6, 2))

        log_title_lbl = ctk.CTkLabel(
            log_header_frame, text="运行日志:",
            font=ctk.CTkFont(family=self.font_family, size=11, weight="bold"),
            text_color=self.colors["muted"]
        )
        log_title_lbl.pack(side=tk.LEFT, anchor="w")

        self.btn_copy_log = ctk.CTkButton(
            log_header_frame,
            text="复制",
            width=48,
            height=22,
            font=ctk.CTkFont(family=self.font_family, size=11),
            corner_radius=11,
            fg_color=self.colors["primary_soft"],
            text_color=self.colors["primary"],
            hover_color=self.colors["border_strong"],
            command=self.copy_log_to_clipboard
        )
        self.btn_copy_log.pack(side=tk.RIGHT, padx=(4, 0))

        self.btn_goto_error_line = ctk.CTkButton(
            log_header_frame,
            text="跳转到报错行",
            width=96,
            height=22,
            font=ctk.CTkFont(family=self.font_family, size=11, weight="bold"),
            corner_radius=11,
            fg_color="#FEE2E2",
            text_color="#DC2626",
            hover_color="#FECACA",
            command=lambda: self.jump_to_script_error_line(self.last_error_line_no),
        )
        self.btn_goto_error_line.pack(side=tk.RIGHT, padx=(4, 0))
        self.btn_goto_error_line.pack_forget()

        self.txt_script_log = ctk.CTkTextbox(log_box, font=ctk.CTkFont(family="Consolas", size=11), fg_color="transparent")
        self.txt_script_log.grid(row=1, column=0, sticky="nsew", padx=4, pady=(0, 4))
        self.txt_script_log.insert(tk.END, "数据状态: 未加载工程\n")
        self.txt_script_log.configure(state="disabled")
        bind_text_context_menu(self.txt_script_log, readonly=True)

        # 绑定日志点击报错行号跳转
        log_tb = getattr(self.txt_script_log, "_textbox", self.txt_script_log)
        try:
            log_tb.tag_config("error_line_link", foreground="#DC2626", underline=True)
            log_tb.tag_bind("error_line_link", "<Button-1>", self._on_error_log_clicked)
        except Exception:
            pass

        def speed_up_log_scroll(event):
            scroll_units = int(-1 * (event.delta / 120) * 4)
            self.txt_script_log.yview_scroll(scroll_units, "units")
            return "break"
        self.txt_script_log.bind("<MouseWheel>", speed_up_log_scroll)
        if hasattr(self.txt_script_log, "_textbox"):
            self.txt_script_log._textbox.bind("<MouseWheel>", speed_up_log_scroll)

        # 日志底部：无弹窗工程归档控制区（紧凑单行水平排布）
        archive_footer = ctk.CTkFrame(log_box, fg_color="transparent", height=28)
        archive_footer.grid(row=2, column=0, sticky="ew", padx=8, pady=(2, 6))

        self.chk_auto_archive_script_run = self._make_checkbox(
            archive_footer,
            text="自动归档",
            variable=self.var_auto_archive_script_run,
            width=84,
        )
        self.chk_auto_archive_script_run.pack(side=tk.LEFT)

        self.btn_archive_script_run = ctk.CTkButton(
            archive_footer,
            text="写入工程",
            width=76,
            height=24,
            corner_radius=8,
            font=ctk.CTkFont(family=self.font_family, size=11),
            fg_color=self.colors["primary_soft"],
            text_color=self.colors["primary"],
            hover_color=self.colors["border_strong"],
            state="disabled",
            command=self.archive_last_script_run_to_project,
        )
        self.btn_archive_script_run.pack(side=tk.RIGHT)

        # 加入 PanedWindow（默认左 4 : 右 1 比例，确保预览区占 >75% 空间）
        self.script_workspace_paned.add(preview_box, minsize=380, stretch="always")
        self.script_workspace_paned.add(log_box, minsize=185, width=230, stretch="never")

        # 初始切换为空白占位模式（隐藏画布交互按钮栏，居中展示空状态提示）
        self._switch_preview_viewport_mode("placeholder")

        # 刷新列表并选择第一个
        self.load_scripts_to_tree()
        children = self.script_tree.get_children()
        if children:
            self.script_tree.selection_set(children[0])

    # ----------------------------------------------------
    # 抽屉与日志分栏折叠控制
    # ----------------------------------------------------
    def toggle_code_preview_drawer(self, expand=None):
        current = bool(getattr(self, "code_drawer_expanded", False))
        target = (not current) if expand is None else bool(expand)
        self.code_drawer_expanded = target
        drawer = getattr(self, "code_preview_drawer", None)
        btn = getattr(self, "btn_toggle_code_drawer", None)
        if drawer is not None:
            if target:
                ctrl_bar = getattr(self, "script_control_bar", None)
                if ctrl_bar is not None:
                    drawer.pack(fill=tk.X, padx=14, pady=(0, 4), before=ctrl_bar)
                else:
                    drawer.pack(fill=tk.X, padx=14, pady=(0, 4))
            else:
                drawer.pack_forget()
        if btn is not None:
            btn.configure(text="▾ 收起源码" if target else "▸ 源码预览")
        return self.code_drawer_expanded

    toggle_script_code_drawer = toggle_code_preview_drawer

    def toggle_script_log_pane(self, collapse=None):
        current = bool(getattr(self, "log_pane_collapsed", False))
        target = (not current) if collapse is None else bool(collapse)
        self.log_pane_collapsed = target
        paned = getattr(self, "script_workspace_paned", None)
        log_box = getattr(self, "script_log_box", None)
        btn = getattr(self, "btn_toggle_log_pane", None)
        if paned is not None and log_box is not None:
            try:
                panes = list(paned.panes())
                if target and str(log_box) in [str(p) for p in panes]:
                    paned.forget(log_box)
                elif (not target) and str(log_box) not in [str(p) for p in panes]:
                    paned.add(log_box, minsize=185, width=230, stretch="never")
            except Exception:
                pass
        if btn is not None:
            btn.configure(text="◂ 展开日志" if target else "收起日志 ▸")
        return self.log_pane_collapsed

    def _update_project_patch_bar_visibility(self, script_type=None):
        bar = getattr(self, "project_patch_bar", None)
        if bar is None:
            return
        if script_type is None:
            script = next(
                (s for s in getattr(self, "local_scripts", []) if s.get("id") == getattr(self, "selected_script_id", None)),
                None,
            )
            script_type = script.get("type", "chart") if script else "chart"
        has_patch = getattr(self, "pending_project_patch", None) is not None
        if script_type == "data_process" or has_patch:
            try:
                bar.pack(side=tk.RIGHT, padx=(6, 0))
            except Exception:
                pass
        else:
            try:
                bar.pack_forget()
            except Exception:
                pass

    # ----------------------------------------------------
    # 声明式交互参数 (PARAMS) 动态表单与取值
    # ----------------------------------------------------
    def rebuild_script_params_ui(self, code):
        from modules.script_runner import extract_script_params_schema
        schema = extract_script_params_schema(code or "")
        self.current_script_params_schema = list(schema)
        self.script_param_widgets = {}

        frame = getattr(self, "script_params_frame", None)
        if frame is None:
            return schema

        for child in frame.winfo_children():
            child.destroy()

        if not schema:
            frame.pack_forget()
            return schema

        out_frame = getattr(self, "script_output_frame", None)
        if out_frame is not None:
            frame.pack(fill=tk.X, padx=14, pady=(0, 4), before=out_frame)
        else:
            frame.pack(fill=tk.X, padx=14, pady=(0, 4))

        inner = ctk.CTkFrame(frame, fg_color="transparent")
        inner.pack(fill=tk.X, padx=10, pady=5)

        ctk.CTkLabel(
            inner,
            text="脚本参数:",
            font=ctk.CTkFont(family=self.font_family, size=11, weight="bold"),
            text_color=self.colors["muted"],
        ).pack(side=tk.LEFT, padx=(0, 8))

        for item in schema:
            key = item["key"]
            label = item.get("label") or key
            p_type = item.get("type", "str")
            default_v = item.get("default")
            choices = item.get("choices") or []

            cell = ctk.CTkFrame(inner, fg_color="transparent")
            cell.pack(side=tk.LEFT, padx=(0, 10))

            if p_type == "bool":
                var = tk.BooleanVar(value=bool(default_v))
                widget = self._make_checkbox(
                    cell,
                    text=label,
                    variable=var,
                )
                widget.pack(side=tk.LEFT)
                self.script_param_widgets[key] = {
                    "schema": item,
                    "type": p_type,
                    "var": var,
                    "widget": widget,
                }
            elif p_type == "choice":
                ctk.CTkLabel(
                    cell,
                    text=f"{label}:",
                    font=ctk.CTkFont(family=self.font_family, size=11),
                    text_color=self.colors["text"],
                ).pack(side=tk.LEFT, padx=(0, 4))
                str_choices = [str(c) for c in choices] if choices else [str(default_v)]
                var = tk.StringVar(value=str(default_v))
                widget = ctk.CTkOptionMenu(
                    cell,
                    values=str_choices,
                    variable=var,
                    height=26,
                    width=max(90, min(160, max(len(s) for s in str_choices) * 11 + 36)),
                    corner_radius=8,
                    font=ctk.CTkFont(family=self.font_family, size=11),
                    fg_color="#FFFFFF",
                    text_color=self.colors["text"],
                    button_color="#F8FAFC",
                    button_hover_color="#E2E8F0",
                    dropdown_fg_color="#FFFFFF",
                    dropdown_text_color=self.colors["text"],
                    dropdown_hover_color="#EFF6FF",
                )
                widget.pack(side=tk.LEFT)
                _apply_custom_arrow(widget)
                self.script_param_widgets[key] = {
                    "schema": item,
                    "type": p_type,
                    "var": var,
                    "widget": widget,
                }
            else:
                ctk.CTkLabel(
                    cell,
                    text=f"{label}:",
                    font=ctk.CTkFont(family=self.font_family, size=11),
                    text_color=self.colors["text"],
                ).pack(side=tk.LEFT, padx=(0, 4))
                var = tk.StringVar(value="" if default_v is None else str(default_v))
                width_px = 68 if p_type in ("int", "float") else 110
                widget = ctk.CTkEntry(
                    cell,
                    textvariable=var,
                    width=width_px,
                    height=26,
                    corner_radius=8,
                    border_width=1,
                    font=ctk.CTkFont(family="Consolas", size=11),
                    fg_color="#FFFFFF",
                    border_color=self.colors.get("border_strong", "#CBD5E1"),
                    text_color=self.colors["text"],
                )
                widget.pack(side=tk.LEFT)
                self.script_param_widgets[key] = {
                    "schema": item,
                    "type": p_type,
                    "var": var,
                    "widget": widget,
                }

        self.btn_reset_script_params = ctk.CTkButton(
            inner,
            text="重置默认",
            width=68,
            height=24,
            corner_radius=8,
            font=ctk.CTkFont(family=self.font_family, size=11),
            fg_color="#FFFFFF",
            border_width=1,
            border_color=self.colors.get("border_strong", "#CBD5E1"),
            text_color=self.colors.get("text_soft", "#334155"),
            hover_color="#F1F5F9",
            command=self.reset_script_params_to_default,
        )
        self.btn_reset_script_params.pack(side=tk.RIGHT, padx=(6, 0))
        return schema

    def set_script_param_ui_value(self, key, value):
        info = (getattr(self, "script_param_widgets", None) or {}).get(key)
        if not info:
            return False
        p_type = info.get("type")
        var = info.get("var")
        widget = info.get("widget")
        if p_type == "bool":
            b_val = bool(value)
            if var is not None:
                var.set(b_val)
            elif widget is not None:
                if b_val:
                    widget.select()
                else:
                    widget.deselect()
        elif p_type == "choice":
            s_val = str(value)
            if var is not None:
                var.set(s_val)
            if widget is not None and hasattr(widget, "set"):
                widget.set(s_val)
        else:
            s_val = "" if value is None else str(value)
            if var is not None:
                var.set(s_val)
            elif widget is not None:
                widget.delete(0, tk.END)
                widget.insert(0, s_val)
        return True

    def reset_script_params_to_default(self):
        schema = getattr(self, "current_script_params_schema", None) or []
        for item in schema:
            self.set_script_param_ui_value(item["key"], item.get("default"))
        return self.collect_script_params_from_ui()

    def collect_script_params_from_ui(self):
        from modules.script_runner import coerce_script_params
        schema = getattr(self, "current_script_params_schema", None) or []
        widgets = getattr(self, "script_param_widgets", None) or {}
        raw_values = {}
        for item in schema:
            key = item["key"]
            info = widgets.get(key)
            if not info:
                raw_values[key] = item.get("default")
                continue
            var = info.get("var")
            widget = info.get("widget")
            if var is not None:
                raw_values[key] = var.get()
            elif widget is not None and hasattr(widget, "get"):
                raw_values[key] = widget.get()
            else:
                raw_values[key] = item.get("default")
        return coerce_script_params(schema, raw_values)

    def copy_log_to_clipboard(self):
        try:
            log_text = self.txt_script_log.get("1.0", tk.END).strip()
            self.clipboard_clear()
            self.clipboard_append(log_text)
            self.update()

            self.btn_copy_log.configure(
                text="✔已复制",
                fg_color=self.colors["success_soft"],
                text_color=self.colors["success"]
            )
            def reset():
                try:
                    self.btn_copy_log.configure(
                        text="复制",
                        fg_color=self.colors["primary_soft"],
                        text_color=self.colors["primary"]
                    )
                except Exception:
                    pass
            self.after(1000, reset)
        except Exception as e:
            messagebox.showerror("错误", f"复制失败：{e}")

    def load_scripts_to_tree(self):
        from modules.script_manager import load_all_scripts
        self.local_scripts = load_all_scripts()

        for item in self.script_tree.get_children():
            self.script_tree.delete(item)

        search_term = self.entry_script_search.get().strip().lower()
        filter_type = self.combo_script_type.get()

        for s in self.local_scripts:
            name = s.get("name", "未命名")
            desc = s.get("description", "")
            type_str = s.get("type", "chart")

            if search_term and search_term not in name.lower() and search_term not in desc.lower():
                continue

            if filter_type == "图表脚本" and type_str != "chart":
                continue
            elif filter_type == "数据处理脚本" and type_str != "data_process":
                continue

            self.script_tree.insert("", tk.END, iid=s["id"], text=name)

        if self.selected_script_id and self.script_tree.exists(self.selected_script_id):
            self.script_tree.selection_set(self.selected_script_id)
        else:
            self.script_tree.selection_clear()
            self.on_script_selected()

    def show_script_context_menu(self, event, iid):
        import os
        from modules.ui_widgets import make_context_menu, post_context_menu

        menu = make_context_menu(self)

        if iid is not None:
            if getattr(self, "is_script_running", False):
                menu.add_command(label="停止运行", command=lambda: self.after(10, self.stop_current_script))
            else:
                menu.add_command(label="运行脚本", command=lambda: self.after(10, self.run_current_script))

            menu.add_command(label="编辑脚本", command=lambda: self.after(10, self.on_edit_script))
            menu.add_command(label="导出脚本...", command=lambda: self.after(10, self.on_export_script))
            menu.add_command(label="复制 AI 提示词...", command=lambda: self.after(10, self.show_prompt_dialog))

            menu.add_separator()

            folder = getattr(self, "script_output_dir", None)
            has_output = folder and os.path.isdir(folder)
            state = "normal" if has_output else "disabled"
            menu.add_command(label="打开结果目录", command=lambda: self.after(10, self.open_script_output_dir), state=state)

            menu.add_separator()
            menu.add_command(label="删除脚本...", command=lambda: self.after(10, self.on_delete_script))
        else:
            menu.add_command(label="新建脚本", command=lambda: self.after(10, self.on_new_script))
            menu.add_command(label="导入脚本...", command=lambda: self.after(10, self.on_import_script))
            menu.add_command(label="复制 AI 提示词...", command=lambda: self.after(10, self.show_prompt_dialog))

        post_context_menu(menu, event)

    def on_script_selected(self, event=None):
        selected = self.script_tree.selection() if hasattr(self, "script_tree") else ()
        if not selected:
            self.selected_script_id = None
            if hasattr(self, "lbl_selected_script_name"):
                self.lbl_selected_script_name.configure(text="当前脚本：未选择")
            if hasattr(self, "lbl_selected_script_desc"):
                self.lbl_selected_script_desc.configure(text="描述：请从左侧列表中选择一个脚本，或者点击“新建”创建新脚本。")
            if hasattr(self, "txt_script_code_preview"):
                self.txt_script_code_preview.configure(state="normal")
                self.txt_script_code_preview.delete("1.0", tk.END)
                self.txt_script_code_preview.configure(state="disabled")
            self._update_project_patch_bar_visibility("chart")
            self.rebuild_script_params_ui("")
            return
        s_id = selected[0]
        self.selected_script_id = s_id

        script = next((s for s in self.local_scripts if s["id"] == s_id), None)
        if script:
            s_type = script.get("type", "chart")
            if hasattr(self, "lbl_selected_script_name"):
                self.lbl_selected_script_name.configure(text=f"当前脚本：{script.get('name', '')}")
            if hasattr(self, "lbl_selected_script_type_tag"):
                if s_type == "data_process":
                    self.lbl_selected_script_type_tag.configure(
                        text="数据处理",
                        fg_color=self.colors["warning_soft"],
                        text_color=self.colors["warning"],
                    )
                else:
                    self.lbl_selected_script_type_tag.configure(
                        text="图表脚本",
                        fg_color=self.colors["primary_soft"],
                        text_color=self.colors["primary"],
                    )
            if hasattr(self, "lbl_selected_script_desc"):
                self.lbl_selected_script_desc.configure(text=f"描述：{script.get('description', '')}")
                wrap_fn = getattr(self.lbl_selected_script_desc, "_update_adaptive_wrap", None)
                if callable(wrap_fn):
                    wrap_fn()
                    self.after(20, wrap_fn)

            code = script.get("code", "")
            if hasattr(self, "txt_script_code_preview"):
                self.txt_script_code_preview.configure(state="normal")
                self.txt_script_code_preview.delete("1.0", tk.END)
                self.txt_script_code_preview.insert("1.0", code)
                self.txt_script_code_preview.configure(state="disabled")
            self._update_project_patch_bar_visibility(s_type)
            self.rebuild_script_params_ui(code)

    def on_script_filter_changed(self, val):
        self.load_scripts_to_tree()

    def on_new_script(self):
        default_type = "data_process" if self.combo_script_type.get() == "数据处理脚本" else "chart"
        dlg = ScriptEditorDialog(
            self,
            script_id=None,
            script_name=SCRIPT_EDITOR_DEFAULT_NAME,
            script_desc=SCRIPT_EDITOR_DEFAULT_DESC,
            script_code="",
            script_type=default_type,
        )
        self.active_script_editor_dialog = dlg
        return dlg

    def on_edit_script(self, initial_line=None):
        selected = self.script_tree.selection() if hasattr(self, "script_tree") else ()
        if not selected and not getattr(self, "selected_script_id", None):
            return messagebox.showwarning("提示", "请先在左侧列表中选择要编辑的脚本。")
        s_id = selected[0] if selected else self.selected_script_id
        script = next((s for s in self.local_scripts if s["id"] == s_id), None)
        if script:
            dlg = ScriptEditorDialog(
                self,
                script_id=script["id"],
                script_name=script.get("name", ""),
                script_desc=script.get("description", ""),
                script_code=script.get("code", ""),
                script_type=script.get("type", "chart"),
                initial_line=initial_line,
            )
            self.active_script_editor_dialog = dlg
            return dlg
        return None

    def jump_to_script_error_line(self, line_no=None):
        target_line = line_no if line_no is not None else getattr(self, "last_error_line_no", None)
        if not target_line:
            return None
        try:
            target_line = int(target_line)
        except (TypeError, ValueError):
            return None
        dlg = getattr(self, "active_script_editor_dialog", None)
        try:
            if dlg is not None and dlg.winfo_exists():
                dlg.lift()
                dlg.goto_line(target_line)
                return dlg
        except Exception:
            pass
        return self.on_edit_script(initial_line=target_line)

    def _on_error_log_clicked(self, _event=None):
        if getattr(self, "last_error_line_no", None):
            self.jump_to_script_error_line(self.last_error_line_no)

    def on_delete_script(self):
        selected = self.script_tree.selection()
        if not selected:
            return messagebox.showwarning("提示", "请先在列表中选择一个要删除的脚本。")
        s_id = selected[0]

        script = next((s for s in self.local_scripts if s["id"] == s_id), None)
        if not script:
            return

        ans = messagebox.askyesno("确认删除", f"确认要删除脚本“{script['name']}”吗？")
        if ans:
            from modules.script_manager import delete_script
            delete_script(s_id)
            self.selected_script_id = None
            self.lbl_selected_script_name.configure(text="当前脚本：未选择")
            self.lbl_selected_script_desc.configure(text="描述：请从左侧列表中选择一个脚本，或者点击“新建”创建新脚本。")
            self.txt_script_code_preview.configure(state="normal")
            self.txt_script_code_preview.delete("1.0", tk.END)
            self.txt_script_code_preview.configure(state="disabled")
            self.rebuild_script_params_ui("")
            self.load_scripts_to_tree()

    def on_import_script(self):
        file_path = filedialog.askopenfilename(filetypes=[("JSON Files", "*.json")])
        if file_path:
            from modules.script_manager import import_script
            try:
                new_s = import_script(file_path)
                messagebox.showinfo("成功", f"成功导入脚本：{new_s['name']}")
                self.load_scripts_to_tree()
                self.script_tree.selection_set(new_s["id"])
            except Exception as e:
                messagebox.showerror("错误", f"导入失败：{e}")

    def on_export_script(self):
        selected = self.script_tree.selection()
        if not selected:
            return messagebox.showwarning("提示", "请先在列表中选择一个要导出的脚本。")
        s_id = selected[0]

        script = next((s for s in self.local_scripts if s["id"] == s_id), None)
        if not script:
            return

        dest_path = filedialog.asksaveasfilename(
            defaultextension=".json",
            initialfile=f"{script['name']}.json",
            filetypes=[("JSON Files", "*.json")]
        )
        if dest_path:
            from modules.script_manager import export_script
            try:
                export_script(s_id, dest_path)
                messagebox.showinfo("成功", f"脚本已成功导出至：\n{dest_path}")
            except Exception as e:
                messagebox.showerror("错误", f"导出失败：{e}")

    def run_current_script(self):
        code = strip_markdown_code_fence(self.txt_script_code_preview.get("1.0", tk.END))
        teproj_path = getattr(self, "loaded_teproj_path", None)
        ui_params = self.collect_script_params_from_ui()
        self.last_run_params = dict(ui_params)

        if getattr(self, "script_figure_results", None):
            self.previous_run_figure_results = list(self.script_figure_results)

        self.is_script_running = True
        self.last_error_line_no = None
        if hasattr(self, "btn_goto_error_line") and self.btn_goto_error_line:
            self.btn_goto_error_line.pack_forget()
        self.btn_run_script.configure(state="disabled")
        self.btn_stop_script.configure(state="normal")
        self._update_chart_preview("正在运行脚本，等待输出...", None)
        self.script_output_dir = None
        self.script_figure_results = []
        self.current_script_figure_index = 0
        self.script_table_results = []
        self.current_script_table_index = 0
        self.pending_project_patch = None
        if hasattr(self, "btn_apply_project_patch") and self.btn_apply_project_patch:
            self.btn_apply_project_patch.configure(state="disabled", text="应用补丁并另存工程 (.teproj)")
        self._update_project_patch_bar_visibility()
        if hasattr(self, "btn_open_script_output") and self.btn_open_script_output:
            self.btn_open_script_output.configure(state="disabled")
        if hasattr(self, "script_preview_nav") and self.script_preview_nav:
            self.script_preview_nav.grid_remove()
        self._rebuild_artifact_capsule_tabs()

        self.txt_script_log.configure(state="normal")
        self.txt_script_log.delete("1.0", tk.END)
        self.txt_script_log.insert("1.0", "正在构建工程数据快照并运行自定义脚本...\n")
        if ui_params:
            self.txt_script_log.insert(tk.END, f"运行参数: {ui_params}\n")
        self.txt_script_log.configure(state="disabled")

        self.run_cancel_event = threading.Event()

        def execute():
            try:
                if teproj_path:
                    from modules.script_api import build_dataset_snapshot
                    dataset_items = build_dataset_snapshot(teproj_path)
                else:
                    dataset_items = []

                from modules.script_runner import run_custom_script
                import matplotlib.pyplot as plt
                plt.close('all')

                res, logs, err = run_custom_script(
                    code,
                    dataset_items,
                    timeout=30,
                    cancel_event=self.run_cancel_event,
                    teproj_path=teproj_path,
                    params=ui_params,
                )
                self.after(0, lambda res=res, logs=logs, err=err: self.on_script_finished(res, logs, err))
            except Exception as e:
                import traceback
                tb = traceback.format_exc()
                err_msg = f"发生意外错误：\n{e}\n\n堆栈信息：\n{tb}"
                self.after(0, lambda err_msg=err_msg: self.on_script_finished(None, [], err_msg))

        start_safe_thread(execute)

    def stop_current_script(self):
        if getattr(self, "run_cancel_event", None):
            self.run_cancel_event.set()
            self.txt_script_log.configure(state="normal")
            self.txt_script_log.insert(tk.END, "\n已请求中止脚本运行，脚本会在下一次检查取消状态时结束。\n")
            self.txt_script_log.configure(state="disabled")

    def on_script_finished(self, res, logs, err):
        self.is_script_running = False
        if hasattr(self, "btn_run_script") and self.btn_run_script:
            self.btn_run_script.configure(state="normal")
        if hasattr(self, "btn_stop_script") and self.btn_stop_script:
            self.btn_stop_script.configure(state="disabled")

        if hasattr(self, "txt_script_log") and self.txt_script_log:
            self.txt_script_log.configure(state="normal")
            self.txt_script_log.delete("1.0", tk.END)
            if logs:
                self.txt_script_log.insert("1.0", "=== 运行日志 ===\n" + "\n".join(logs) + "\n\n")
            self.txt_script_log.configure(state="disabled")

        if err:
            self.append_script_log(f"运行失败：\n{err}\n")
            self._update_chart_preview("运行失败", None)
            self.script_output_dir = None
            self.script_figure_results = []
            self.current_script_figure_index = 0
            self.script_table_results = []
            self.current_script_table_index = 0
            if hasattr(self, "btn_open_script_output") and self.btn_open_script_output:
                self.btn_open_script_output.configure(state="disabled")
            if hasattr(self, "script_preview_nav") and self.script_preview_nav:
                self.script_preview_nav.grid_remove()
            self._rebuild_artifact_capsule_tabs()
            return

        self.append_script_log("运行成功结束。\n")
        self.display_script_result(res)

    def append_script_log(self, text):
        msg = str(text or "")
        line_no = extract_error_line_number(msg)
        if line_no:
            self.last_error_line_no = line_no
            btn_err = getattr(self, "btn_goto_error_line", None)
            if btn_err is not None:
                try:
                    btn_err.configure(text=f"跳转到报错行 (Line {line_no})")
                    btn_err.pack(side=tk.RIGHT, padx=(4, 0))
                except Exception:
                    pass

        if not hasattr(self, "txt_script_log") or not self.txt_script_log:
            return
        self.txt_script_log.configure(state="normal")
        start_idx = self.txt_script_log.index("end-1c")
        self.txt_script_log.insert(tk.END, msg)
        end_idx = self.txt_script_log.index("end-1c")
        if line_no:
            log_tb = getattr(self.txt_script_log, "_textbox", self.txt_script_log)
            try:
                log_tb.tag_config("error_line_link", foreground="#DC2626", underline=True)
                log_tb.tag_add("error_line_link", start_idx, end_idx)
            except Exception:
                pass
        self.txt_script_log.see(tk.END)
        self.txt_script_log.configure(state="disabled")

    def _safe_script_output_name(self, name, fallback="output"):
        raw = str(name or fallback).strip()
        raw = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", raw)
        raw = re.sub(r"\s+", "_", raw)
        raw = raw.strip("._ ")
        return raw[:80] or fallback

    def _unique_path(self, folder, filename):
        base, ext = os.path.splitext(filename)
        path = os.path.join(folder, filename)
        index = 2
        while os.path.exists(path):
            path = os.path.join(folder, f"{base}_{index}{ext}")
            index += 1
        return path

    def get_script_output_dir(self, script_name):
        base_dir = os.path.join(os.path.expanduser("~"), ".phon_tracer", "script_outputs")
        safe_name = self._safe_script_output_name(script_name, "script")
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        folder = os.path.join(base_dir, f"{stamp}_{safe_name}")
        suffix = 2
        unique_folder = folder
        while os.path.exists(unique_folder):
            unique_folder = f"{folder}_{suffix}"
            suffix += 1
        os.makedirs(unique_folder, exist_ok=True)
        return unique_folder

    def get_script_preview_dir(self, output_dir):
        base_dir = os.path.join(os.path.expanduser("~"), ".phon_tracer", "temp", "script_previews")
        run_name = os.path.basename(os.path.normpath(output_dir)) if output_dir else "script_run"
        preview_dir = os.path.join(base_dir, run_name)
        os.makedirs(preview_dir, exist_ok=True)
        return preview_dir

    def open_script_output_dir(self):
        folder = getattr(self, "script_output_dir", None)
        if not folder or not os.path.isdir(folder):
            messagebox.showinfo("结果目录", "当前还没有可打开的脚本输出目录。请先运行脚本并生成结果。")
            return
        try:
            if sys.platform == "win32":
                os.startfile(folder)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", folder])
            else:
                subprocess.Popen(["xdg-open", folder])
        except Exception as e:
            messagebox.showerror("打开失败", f"无法打开结果目录：\n{e}")

    def _open_file_safely(self, path):
        if not path or not os.path.exists(path):
            messagebox.showerror("错误", f"找不到文件：\n{path}")
            return
        try:
            if sys.platform == "win32":
                os.startfile(path)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception as e:
            messagebox.showerror("打开失败", f"无法打开文件：\n{e}")

    # ----------------------------------------------------
    # 交互式高清预览画布 (Interactive Canvas Viewer) 与双图对比
    # ----------------------------------------------------
    def _switch_preview_viewport_mode(self, mode):
        self.active_artifact_mode = mode
        canvas = getattr(self, "canvas_chart_preview", None)
        lbl = getattr(self, "lbl_chart_preview", None)
        tbl_box = getattr(self, "table_preview_container", None)
        file_box = getattr(self, "script_file_list_frame", None)
        toolbar = getattr(self, "canvas_toolbar", None)

        if tbl_box is not None:
            if mode == "table":
                tbl_box.grid()
            else:
                tbl_box.grid_remove()
        if file_box is not None:
            if mode == "files":
                file_box.grid()
            else:
                file_box.grid_remove()
        if canvas is not None:
            if mode == "figure":
                canvas.grid()
            else:
                canvas.grid_remove()
        if lbl is not None:
            if mode == "placeholder":
                lbl.grid()
            else:
                lbl.grid_remove()
        if toolbar is not None:
            if mode == "figure":
                toolbar.grid()
            else:
                toolbar.grid_remove()

    def _update_chart_preview(self, text, image=None):
        try:
            if image is None and text:
                self.current_canvas_pil_image = None
                self._switch_preview_viewport_mode("placeholder")
            if hasattr(self, "lbl_chart_preview") and self.lbl_chart_preview:
                try:
                    self.lbl_chart_preview._label.configure(image="")
                except Exception:
                    pass
                self.lbl_chart_preview.configure(text=text, image=image)
                self.lbl_chart_preview.image = image
            if hasattr(self, "canvas_chart_preview") and self.canvas_chart_preview and image is None:
                self.canvas_chart_preview.delete("all")
                if text:
                    cw = max(240, self.canvas_chart_preview.winfo_width())
                    ch = max(160, self.canvas_chart_preview.winfo_height())
                    self.canvas_chart_preview.create_text(
                        cw // 2, ch // 2, text=text, fill="#64748B", font=("Microsoft YaHei", 11)
                    )
        except Exception:
            pass

    def _on_canvas_configure(self, _event=None):
        if getattr(self, "_canvas_resize_after_id", None):
            try:
                self.after_cancel(self._canvas_resize_after_id)
            except Exception:
                pass
        try:
            self._canvas_resize_after_id = self.after(80, self._render_canvas_image)
        except Exception:
            self._render_canvas_image()

    def _resolve_compare_pil_image(self):
        figures = getattr(self, "script_figure_results", None) or []
        curr_idx = int(getattr(self, "current_script_figure_index", 0))
        if len(figures) >= 2:
            other_idx = (curr_idx - 1) if curr_idx > 0 else 1
            other_item = figures[other_idx]
            return self._load_pil_for_figure_item(other_item), other_item.get("title") or f"图{other_idx + 1}"
        prev_figs = getattr(self, "previous_run_figure_results", None) or []
        if prev_figs:
            prev_item = prev_figs[min(curr_idx, len(prev_figs) - 1)]
            return self._load_pil_for_figure_item(prev_item), f"上次运行: {prev_item.get('title') or '图表'}"
        if figures:
            curr_item = figures[min(curr_idx, len(figures) - 1)]
            return self._load_pil_for_figure_item(curr_item), f"基准: {curr_item.get('title') or '图表'}"
        return None, ""

    def _load_pil_for_figure_item(self, item):
        if not isinstance(item, dict):
            return None
        if item.get("pil_image") is not None:
            return item["pil_image"]
        preview_p = item.get("preview_path") or item.get("output_path")
        if preview_p and os.path.exists(preview_p):
            ext = os.path.splitext(preview_p)[1].lower()
            if ext not in (".svg", ".pdf"):
                try:
                    with Image.open(preview_p) as src_im:
                        im = src_im.convert("RGBA")
                        im.load()
                    item["pil_image"] = im
                    return im
                except Exception:
                    return None
        return None

    def _render_canvas_image(self):
        self._canvas_resize_after_id = None
        src_img = getattr(self, "current_canvas_pil_image", None)
        canvas = getattr(self, "canvas_chart_preview", None)
        if src_img is None:
            return None

        cw = 640
        ch = 420
        if canvas is not None:
            try:
                w_real = int(canvas.winfo_width())
                h_real = int(canvas.winfo_height())
                if w_real > 40:
                    cw = w_real
                if h_real > 40:
                    ch = h_real
            except Exception:
                pass

        orig_w, orig_h = src_img.size
        if orig_w <= 0 or orig_h <= 0:
            return None

        split_mode = bool(getattr(self, "canvas_split_compare_mode", False))
        avail_w = max(80, (cw // 2 - 16) if split_mode else (cw - 16))
        avail_h = max(80, ch - (36 if split_mode else 16))

        fit_ratio = min(avail_w / float(orig_w), avail_h / float(orig_h))
        fit_ratio = max(0.05, fit_ratio)

        if getattr(self, "canvas_fit_mode", True):
            effective_scale = fit_ratio
            self.canvas_zoom_scale = 1.0
            self.canvas_pan_offset_x = 0.0
            self.canvas_pan_offset_y = 0.0
            zoom_pct = 100
        else:
            z = float(np.clip(getattr(self, "canvas_zoom_scale", 1.0), 0.25, 4.0))
            self.canvas_zoom_scale = z
            effective_scale = fit_ratio * z
            zoom_pct = int(round(z * 100))

        target_w = max(24, int(round(orig_w * effective_scale)))
        target_h = max(24, int(round(orig_h * effective_scale)))

        resized = src_img.resize((target_w, target_h), Image.Resampling.LANCZOS)
        self.last_rendered_canvas_size = (target_w, target_h)

        if hasattr(self, "lbl_canvas_zoom") and self.lbl_canvas_zoom:
            try:
                self.lbl_canvas_zoom.configure(text=f"{zoom_pct}%")
            except Exception:
                pass

        # 同步更新 lbl_chart_preview 以兼容现有测试
        if hasattr(self, "lbl_chart_preview") and self.lbl_chart_preview:
            try:
                ctk_img = ctk.CTkImage(light_image=resized, dark_image=resized, size=resized.size)
                self.lbl_chart_preview.configure(text="", image=ctk_img)
                self.lbl_chart_preview.image = ctk_img
            except Exception:
                pass

        if canvas is None:
            return resized

        try:
            canvas.delete("all")
            pan_x = float(getattr(self, "canvas_pan_offset_x", 0.0))
            pan_y = float(getattr(self, "canvas_pan_offset_y", 0.0))

            if split_mode:
                comp_img, comp_title = self._resolve_compare_pil_image()
                self.compare_canvas_pil_image = comp_img
                mid_x = cw // 2
                if comp_img is not None:
                    c_w, c_h = comp_img.size
                    c_ratio = min(avail_w / float(max(1, c_w)), avail_h / float(max(1, c_h)))
                    c_scale = c_ratio * (1.0 if getattr(self, "canvas_fit_mode", True) else self.canvas_zoom_scale)
                    ct_w = max(24, int(round(c_w * c_scale)))
                    ct_h = max(24, int(round(c_h * c_scale)))
                    comp_resized = comp_img.resize((ct_w, ct_h), Image.Resampling.LANCZOS)
                    self._canvas_compare_tk_img = ImageTk.PhotoImage(comp_resized)
                    canvas.create_image(
                        int(mid_x // 2 + pan_x),
                        int(ch // 2 + 10 + pan_y),
                        image=self._canvas_compare_tk_img,
                        anchor="center",
                    )
                    canvas.create_text(
                        mid_x // 2, 14, text=f"对比图: {comp_title}", fill="#475569", font=("Microsoft YaHei", 9, "bold")
                    )

                self._canvas_tk_img = ImageTk.PhotoImage(resized)
                canvas.create_image(
                    int(mid_x + mid_x // 2 + pan_x),
                    int(ch // 2 + 10 + pan_y),
                    image=self._canvas_tk_img,
                    anchor="center",
                )
                curr_figs = getattr(self, "script_figure_results", None) or []
                curr_idx = int(getattr(self, "current_script_figure_index", 0))
                curr_title = curr_figs[curr_idx].get("title") if 0 <= curr_idx < len(curr_figs) else "当前图"
                canvas.create_text(
                    mid_x + mid_x // 2, 14, text=f"当前图: {curr_title}", fill="#1D4ED8", font=("Microsoft YaHei", 9, "bold")
                )
                canvas.create_line(mid_x, 0, mid_x, ch, fill="#94A3B8", dash=(4, 3), width=1.5)
            else:
                self._canvas_tk_img = ImageTk.PhotoImage(resized)
                cx = int(cw // 2 + pan_x)
                cy = int(ch // 2 + pan_y)
                canvas.create_image(cx, cy, image=self._canvas_tk_img, anchor="center")
        except Exception:
            pass
        return resized

    def canvas_zoom_fit(self):
        self.canvas_fit_mode = True
        self.canvas_zoom_scale = 1.0
        self.canvas_pan_offset_x = 0.0
        self.canvas_pan_offset_y = 0.0
        self._render_canvas_image()

    def canvas_zoom_original(self):
        self.canvas_fit_mode = False
        self.canvas_zoom_scale = 1.0
        self.canvas_pan_offset_x = 0.0
        self.canvas_pan_offset_y = 0.0
        self._render_canvas_image()

    def canvas_zoom_in(self):
        self.canvas_fit_mode = False
        z = float(getattr(self, "canvas_zoom_scale", 1.0)) * 1.25
        self.canvas_zoom_scale = float(np.clip(z, 0.25, 4.0))
        self._render_canvas_image()

    def canvas_zoom_out(self):
        self.canvas_fit_mode = False
        z = float(getattr(self, "canvas_zoom_scale", 1.0)) / 1.25
        self.canvas_zoom_scale = float(np.clip(z, 0.25, 4.0))
        self._render_canvas_image()

    def on_canvas_mousewheel(self, event):
        if getattr(self, "current_canvas_pil_image", None) is None:
            return
        delta = getattr(event, "delta", 0)
        num = getattr(event, "num", None)
        if delta > 0 or num == 4:
            factor = 1.15
        elif delta < 0 or num == 5:
            factor = 1.0 / 1.15
        else:
            factor = 1.0

        old_z = float(getattr(self, "canvas_zoom_scale", 1.0))
        new_z = float(np.clip(old_z * factor, 0.25, 4.0))
        ratio = new_z / max(old_z, 1e-6)

        canvas = getattr(self, "canvas_chart_preview", None)
        cw = int(canvas.winfo_width()) if canvas is not None and canvas.winfo_width() > 40 else 640
        ch = int(canvas.winfo_height()) if canvas is not None and canvas.winfo_height() > 40 else 420
        ex = float(getattr(event, "x", cw / 2.0))
        ey = float(getattr(event, "y", ch / 2.0))

        dx = ex - (cw / 2.0 + float(getattr(self, "canvas_pan_offset_x", 0.0)))
        dy = ey - (ch / 2.0 + float(getattr(self, "canvas_pan_offset_y", 0.0)))
        self.canvas_pan_offset_x = float(getattr(self, "canvas_pan_offset_x", 0.0)) - dx * (ratio - 1.0)
        self.canvas_pan_offset_y = float(getattr(self, "canvas_pan_offset_y", 0.0)) - dy * (ratio - 1.0)
        self.canvas_fit_mode = False
        self.canvas_zoom_scale = new_z
        self._render_canvas_image()
        return "break"

    def on_canvas_drag_start(self, event):
        canvas = getattr(self, "canvas_chart_preview", None)
        if canvas is not None:
            try:
                canvas.focus_set()
            except Exception:
                pass
        self._canvas_drag_start_pos = (float(getattr(event, "x", 0.0)), float(getattr(event, "y", 0.0)))

    def on_canvas_drag_move(self, event):
        start_pos = getattr(self, "_canvas_drag_start_pos", None)
        if start_pos is None:
            return
        ex = float(getattr(event, "x", 0.0))
        ey = float(getattr(event, "y", 0.0))
        dx = ex - start_pos[0]
        dy = ey - start_pos[1]
        self._canvas_drag_start_pos = (ex, ey)
        self.canvas_fit_mode = False
        self.canvas_pan_offset_x = float(getattr(self, "canvas_pan_offset_x", 0.0)) + dx
        self.canvas_pan_offset_y = float(getattr(self, "canvas_pan_offset_y", 0.0)) + dy
        self._render_canvas_image()

    def on_canvas_double_click(self, _event=None):
        self.canvas_zoom_fit()

    def toggle_canvas_split_compare(self, enable=None):
        curr = bool(getattr(self, "canvas_split_compare_mode", False))
        self.canvas_split_compare_mode = (not curr) if enable is None else bool(enable)
        btn = getattr(self, "btn_canvas_split_compare", None)
        if btn is not None:
            try:
                if self.canvas_split_compare_mode:
                    btn.configure(
                        fg_color=self.colors["primary"],
                        text_color="#FFFFFF",
                        border_color=self.colors["primary"],
                    )
                else:
                    btn.configure(
                        fg_color="#FFFFFF",
                        text_color=self.colors.get("text_soft", "#334155"),
                        border_color=self.colors.get("border", "#E2E8F0"),
                    )
            except Exception:
                pass
        self._render_canvas_image()
        return self.canvas_split_compare_mode

    def open_current_chart_externally(self):
        figures = getattr(self, "script_figure_results", []) or []
        if not figures:
            return
        idx = int(getattr(self, "current_script_figure_index", 0))
        if 0 <= idx < len(figures):
            path = figures[idx].get("output_path")
            if path and os.path.exists(path):
                self._open_file_safely(path)

    def on_chart_preview_clicked(self, event=None):
        """保留向后兼容方法名（现由右键菜单或工具栏触发打开外部查看器）。"""
        self.open_current_chart_externally()

    def copy_current_chart_to_clipboard(self):
        """将当前预览画布的高清图像复制到剪贴板（支持 Windows PowerShell/win32 及跨平台内存缓冲回退）。"""
        pil_img = getattr(self, "current_canvas_pil_image", None)
        if pil_img is None:
            figures = getattr(self, "script_figure_results", []) or []
            idx = int(getattr(self, "current_script_figure_index", 0))
            if 0 <= idx < len(figures):
                pil_img = self._load_pil_for_figure_item(figures[idx])
        if pil_img is None:
            return False

        import io
        bio = io.BytesIO()
        pil_img.convert("RGB").save(bio, format="PNG")
        png_bytes = bio.getvalue()
        self._clipboard_image_buffer = png_bytes

        copied_os = False
        if sys.platform == "win32":
            try:
                import tempfile
                fd, tmp_png = tempfile.mkstemp(prefix="phontracer_clip_", suffix=".png")
                os.close(fd)
                with open(tmp_png, "wb") as f:
                    f.write(png_bytes)
                ps_cmd = (
                    "Add-Type -AssemblyName System.Windows.Forms; "
                    "Add-Type -AssemblyName System.Drawing; "
                    f"$img = [System.Drawing.Image]::FromFile('{tmp_png}'); "
                    "[System.Windows.Forms.Clipboard]::SetImage($img); "
                    "$img.Dispose();"
                )
                subprocess.run(["powershell", "-NoProfile", "-Command", ps_cmd], check=True, timeout=5)
                copied_os = True
            except Exception:
                copied_os = False
            finally:
                try:
                    if "tmp_png" in locals() and os.path.exists(tmp_png):
                        os.remove(tmp_png)
                except Exception:
                    pass
        elif sys.platform == "darwin":
            try:
                p = subprocess.Popen(["pbcopy"], stdin=subprocess.PIPE)
                p.communicate(input=png_bytes, timeout=3)
                copied_os = (p.returncode == 0)
            except Exception:
                copied_os = False
        else:
            for cmd in (["xclip", "-selection", "clipboard", "-t", "image/png"], ["wl-copy", "--type", "image/png"]):
                try:
                    p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
                    p.communicate(input=png_bytes, timeout=2)
                    if p.returncode == 0:
                        copied_os = True
                        break
                except Exception:
                    continue

        if not copied_os:
            try:
                figures = getattr(self, "script_figure_results", []) or []
                idx = int(getattr(self, "current_script_figure_index", 0))
                fallback_text = figures[idx].get("output_path", "") if 0 <= idx < len(figures) else "PNG_IMAGE_IN_BUFFER"
                self.clipboard_clear()
                self.clipboard_append(fallback_text)
            except Exception:
                pass

        self.append_script_log("已将当前图表复制到剪贴板。\n")
        return True

    def save_current_chart_as(self, dest_path=None):
        figures = getattr(self, "script_figure_results", []) or []
        if not figures:
            return None
        idx = int(getattr(self, "current_script_figure_index", 0))
        if not (0 <= idx < len(figures)):
            return None
        item = figures[idx]
        default_name = item.get("filename") or "chart.png"
        if dest_path is None:
            dest_path = filedialog.asksaveasfilename(
                title="另存图表为...",
                initialfile=default_name,
                defaultextension=os.path.splitext(default_name)[1] or ".png",
                filetypes=[
                    ("PNG 高清位图", "*.png"),
                    ("PDF 矢量图", "*.pdf"),
                    ("SVG 矢量图", "*.svg"),
                    ("JPEG 图像", "*.jpg"),
                ],
            )
        if not dest_path:
            return None

        target_ext = os.path.splitext(dest_path)[1].lower().lstrip(".") or "png"
        saved_map = item.get("saved_paths_by_format") or {}
        if target_ext in saved_map and os.path.exists(saved_map[target_ext]):
            import shutil
            shutil.copy2(saved_map[target_ext], dest_path)
            self.append_script_log(f"已另存图表至：{dest_path}\n")
            return dest_path

        pil_img = self._load_pil_for_figure_item(item)
        if pil_img is not None:
            if target_ext in ("jpg", "jpeg"):
                pil_img.convert("RGB").save(dest_path, format="JPEG", quality=95)
            elif target_ext == "pdf":
                pil_img.convert("RGB").save(dest_path, format="PDF", resolution=300.0)
            else:
                pil_img.save(dest_path, format="PNG")
            self.append_script_log(f"已另存图表至：{dest_path}\n")
            return dest_path
        return None

    def show_canvas_context_menu(self, event):
        from modules.ui_widgets import make_context_menu, post_context_menu
        menu = make_context_menu(self)
        has_fig = bool(getattr(self, "script_figure_results", None))
        state_fig = "normal" if has_fig else "disabled"
        folder = getattr(self, "script_output_dir", None)
        state_dir = "normal" if (folder and os.path.isdir(folder)) else "disabled"

        menu.add_command(label="复制图片到剪贴板 (Ctrl+C)", command=self.copy_current_chart_to_clipboard, state=state_fig)
        menu.add_command(label="另存为 PNG / PDF / SVG...", command=self.save_current_chart_as, state=state_fig)
        menu.add_separator()
        menu.add_command(label="适应窗口 (双击复位)", command=self.canvas_zoom_fit, state=state_fig)
        menu.add_command(label="1:1 原始尺寸", command=self.canvas_zoom_original, state=state_fig)
        menu.add_command(label="切换双图并排对比", command=self.toggle_canvas_split_compare, state=state_fig)
        menu.add_separator()
        menu.add_command(label="在系统默认查看器中打开", command=self.open_current_chart_externally, state=state_fig)
        menu.add_command(label="在资源管理器中显示输出目录", command=self.open_script_output_dir, state=state_dir)
        post_context_menu(menu, event)
        return menu

    # ----------------------------------------------------
    # 多产物胶囊标签栏 & 内置表格预览器 (ttk.Treeview)
    # ----------------------------------------------------
    def _rebuild_artifact_capsule_tabs(self):
        tabs_frame = getattr(self, "artifact_tabs_frame", None)
        if tabs_frame is None:
            return
        for child in tabs_frame.winfo_children():
            child.destroy()
        self.artifact_tab_buttons = []

        figures = getattr(self, "script_figure_results", None) or []
        tables = getattr(self, "script_table_results", None) or []
        patch = getattr(self, "pending_project_patch", None)

        active_key = getattr(self, "active_artifact_key", ("figure", 0))

        for idx, fig_item in enumerate(figures):
            short_title = str(fig_item.get("title") or fig_item.get("filename") or f"图表{idx + 1}")
            if len(short_title) > 16:
                short_title = short_title[:15] + "…"
            is_active = (active_key == ("figure", idx) and getattr(self, "active_artifact_mode", "figure") == "figure")
            btn = ctk.CTkButton(
                tabs_frame,
                text=f"图{idx + 1}: {short_title}",
                height=26,
                width=max(84, len(short_title) * 11 + 42),
                corner_radius=13,
                font=ctk.CTkFont(family=self.font_family, size=11, weight="bold" if is_active else "normal"),
                fg_color=self.colors["primary"] if is_active else ("#E5E7EB", "#374151"),
                text_color="#FFFFFF" if is_active else ("#1F2937", "#E5E7EB"),
                hover_color=self.colors["primary_hover"] if is_active else ("#D1D5DB", "#4B5563"),
                command=lambda i=idx: self.select_script_artifact("figure", i),
            )
            btn.pack(side=tk.LEFT, padx=(0, 5))
            self.artifact_tab_buttons.append(btn)

        for idx, tbl_item in enumerate(tables):
            short_title = str(tbl_item.get("title") or f"数据表{idx + 1}")
            if len(short_title) > 16:
                short_title = short_title[:15] + "…"
            is_active = (active_key == ("table", idx) and getattr(self, "active_artifact_mode", "") == "table")
            btn = ctk.CTkButton(
                tabs_frame,
                text=f"表{idx + 1}: {short_title}",
                height=26,
                width=max(84, len(short_title) * 11 + 42),
                corner_radius=13,
                font=ctk.CTkFont(family=self.font_family, size=11, weight="bold" if is_active else "normal"),
                fg_color=self.colors["purple"] if is_active else ("#E5E7EB", "#374151"),
                text_color="#FFFFFF" if is_active else ("#1F2937", "#E5E7EB"),
                hover_color=self.colors["border_strong"],
                command=lambda i=idx: self.select_script_artifact("table", i),
            )
            btn.pack(side=tk.LEFT, padx=(0, 5))
            self.artifact_tab_buttons.append(btn)

        if patch is not None and getattr(patch, "operation_count", 0) > 0:
            btn = ctk.CTkButton(
                tabs_frame,
                text=f"补丁: {patch.operation_count}项操作 (点击应用)",
                height=26,
                corner_radius=13,
                font=ctk.CTkFont(family=self.font_family, size=11, weight="bold"),
                fg_color="#059669",
                text_color="#FFFFFF",
                hover_color="#047857",
                command=self.apply_pending_project_patch,
            )
            btn.pack(side=tk.LEFT, padx=(4, 0))
            self.artifact_tab_buttons.append(btn)

        if self.artifact_tab_buttons:
            tabs_frame.grid(row=0, column=0, sticky="w")
        else:
            tabs_frame.grid_remove()

    _rebuild_artifact_tabs = _rebuild_artifact_capsule_tabs

    def select_script_artifact(self, kind, index):
        self.active_artifact_key = (kind, int(index))
        if kind == "figure":
            self.show_script_figure_at(int(index))
        elif kind == "table":
            self.show_script_table_at(int(index))
        self._rebuild_artifact_capsule_tabs()

    def show_script_table_at(self, index):
        tables = getattr(self, "script_table_results", []) or []
        if not tables:
            return
        index = max(0, min(int(index), len(tables) - 1))
        self.current_script_table_index = index
        self.active_artifact_key = ("table", index)
        self._switch_preview_viewport_mode("table")

        tbl_item = tables[index]
        columns = [str(c) for c in (tbl_item.get("columns") or [])]
        rows = list(tbl_item.get("rows") or [])
        if not columns and rows:
            columns = [f"列{i + 1}" for i in range(len(rows[0]))]

        if hasattr(self, "lbl_table_summary") and self.lbl_table_summary:
            self.lbl_table_summary.configure(
                text=f"{tbl_item.get('title') or '数据表'} — 共 {len(rows)} 行 × {len(columns)} 列 (点击表头可排序)"
            )

        tree = getattr(self, "script_table_tree", None)
        if tree is not None:
            for item_id in tree.get_children():
                tree.delete(item_id)
            col_ids = [f"col_{i}" for i in range(len(columns))]
            tree["columns"] = col_ids
            for i, col_name in enumerate(columns):
                cid = col_ids[i]
                tree.heading(cid, text=col_name, command=lambda c_idx=i: self.sort_script_table_by_column(c_idx))
                tree.column(cid, width=max(85, min(220, len(col_name) * 14 + 32)), anchor="center")
            for r_idx, row in enumerate(rows):
                vals = ["" if v is None else str(v) for v in row]
                tree.insert("", tk.END, iid=f"row_{r_idx}", values=vals)

        self._rebuild_artifact_capsule_tabs()

    def sort_script_table_by_column(self, col_index, reverse=None):
        tables = getattr(self, "script_table_results", []) or []
        t_idx = int(getattr(self, "current_script_table_index", 0))
        if not (0 <= t_idx < len(tables)):
            return []
        tbl_item = tables[t_idx]
        rows = list(tbl_item.get("rows") or [])
        if not rows:
            return []

        col_idx = int(col_index)
        state_key = (t_idx, col_idx)
        if reverse is None:
            reverse = not bool(self._table_sort_state.get(state_key, False))
        self._table_sort_state[state_key] = bool(reverse)

        def _sort_key(row):
            val = row[col_idx] if col_idx < len(row) else None
            if val is None or val == "":
                return (1, 0.0, "")
            try:
                f_val = float(val)
                if np.isnan(f_val):
                    return (1, 0.0, "")
                return (0, f_val, "")
            except (TypeError, ValueError):
                return (0, 0.0, str(val))

        sorted_rows = sorted(rows, key=_sort_key, reverse=bool(reverse))
        tbl_item["rows"] = sorted_rows
        self.show_script_table_at(t_idx)
        return sorted_rows

    def copy_current_table_tsv_to_clipboard(self):
        tables = getattr(self, "script_table_results", []) or []
        t_idx = int(getattr(self, "current_script_table_index", 0))
        if not (0 <= t_idx < len(tables)):
            return ""
        tbl_item = tables[t_idx]
        cols = [str(c) for c in (tbl_item.get("columns") or [])]
        rows = tbl_item.get("rows") or []
        lines = []
        if cols:
            lines.append("\t".join(cols))
        for r in rows:
            lines.append("\t".join("" if v is None else str(v) for v in r))
        tsv_text = "\n".join(lines)
        self._last_copied_tsv = tsv_text
        try:
            self.clipboard_clear()
            self.clipboard_append(tsv_text)
            self.update()
        except Exception:
            pass
        self.append_script_log(f"已复制全表 ({len(rows)} 行 TSV) 到剪贴板，可直接粘贴至 Excel。\n")
        return tsv_text

    def save_current_table_as(self, dest_path=None):
        tables = getattr(self, "script_table_results", []) or []
        t_idx = int(getattr(self, "current_script_table_index", 0))
        if not (0 <= t_idx < len(tables)):
            return None
        tbl_item = tables[t_idx]
        default_name = f"{self._safe_script_output_name(tbl_item.get('title') or 'table', 'table')}.csv"
        if dest_path is None:
            dest_path = filedialog.asksaveasfilename(
                title="另存数据表为...",
                initialfile=default_name,
                defaultextension=".csv",
                filetypes=[("CSV 表格 (UTF-8 BOM)", "*.csv"), ("Excel 工作簿", "*.xlsx")],
            )
        if not dest_path:
            return None

        cols = list(tbl_item.get("columns") or [])
        rows = list(tbl_item.get("rows") or [])
        ext = os.path.splitext(dest_path)[1].lower()
        if ext == ".xlsx":
            import pandas as pd
            df = pd.DataFrame(rows, columns=cols if cols else None)
            df.to_excel(dest_path, index=False)
        else:
            with open(dest_path, "w", encoding="utf-8-sig", newline="") as f:
                writer = csv.writer(f)
                if cols:
                    writer.writerow(cols)
                writer.writerows(rows)
        self.append_script_log(f"已另存数据表至：{dest_path}\n")
        return dest_path

    def show_prev_script_figure(self):
        self.show_script_figure_at(getattr(self, "current_script_figure_index", 0) - 1)

    def show_next_script_figure(self):
        self.show_script_figure_at(getattr(self, "current_script_figure_index", 0) + 1)

    def show_script_figure_at(self, index):
        figures = getattr(self, "script_figure_results", []) or []
        if not figures:
            if hasattr(self, "script_preview_nav") and self.script_preview_nav:
                self.script_preview_nav.grid_remove()
            self._update_chart_preview("暂无图表预览", None)
            return

        index = max(0, min(int(index), len(figures) - 1))
        self.current_script_figure_index = index
        self.active_artifact_key = ("figure", index)
        item = figures[index]

        self._switch_preview_viewport_mode("figure")
        try:
            pil_img = self._load_pil_for_figure_item(item)
            if pil_img is None:
                ext = os.path.splitext(item.get("preview_path") or "")[1].lower()
                self._update_chart_preview(f"无法加载图表预览 ({ext})", None)
            else:
                self.current_canvas_pil_image = pil_img
                self.canvas_fit_mode = True
                self.canvas_zoom_scale = 1.0
                self.canvas_pan_offset_x = 0.0
                self.canvas_pan_offset_y = 0.0
                self._render_canvas_image()
        except Exception as e:
            self._update_chart_preview(f"无法加载图像预览：{e}", None)

        total = len(figures)
        title = item.get("title") or item.get("filename") or "自定义图表"
        if len(title) > 36:
            title = title[:33] + "..."
        if hasattr(self, "lbl_script_fig_page") and self.lbl_script_fig_page:
            self.lbl_script_fig_page.configure(text=f"图表: {index + 1}/{total}  {title}")
        if hasattr(self, "btn_script_prev_fig") and self.btn_script_prev_fig:
            self.btn_script_prev_fig.configure(state="normal" if index > 0 else "disabled")
        if hasattr(self, "btn_script_next_fig") and self.btn_script_next_fig:
            self.btn_script_next_fig.configure(state="normal" if index < total - 1 else "disabled")
        if hasattr(self, "script_preview_nav") and self.script_preview_nav:
            if total > 1:
                self.script_preview_nav.grid()
            else:
                self.script_preview_nav.grid_remove()
        self._rebuild_artifact_capsule_tabs()

    # ----------------------------------------------------
    # 结果展示：多格式同步导出 + 图表/表格/工程补丁联合返回 + 无弹窗归档
    # ----------------------------------------------------
    def display_script_result(self, res):
        import io
        from modules.script_api import FigureResult, TableResult, ProjectPatchResult, configure_matplotlib_chinese_font

        if res is None:
            results = []
        elif isinstance(res, (list, tuple)):
            results = [r for r in res if r is not None]
        else:
            results = [res]

        figure_results = [r for r in results if isinstance(r, FigureResult)]
        table_results = [r for r in results if isinstance(r, TableResult)]
        project_patch_results = [r for r in results if isinstance(r, ProjectPatchResult)]

        if len(project_patch_results) > 1:
            self.append_script_log("脚本不能同时返回多个 ctx.project_patch(...) 对象。\n")
            self._update_chart_preview("数据处理脚本返回值不合法（存在多个补丁对象）。", None)
            return

        script_meta = next((s for s in getattr(self, "local_scripts", []) if s.get("id") == getattr(self, "selected_script_id", None)), {}) or {}
        script_name = script_meta.get("name") or "未命名脚本"
        script_desc = script_meta.get("description") or "自定义分析"
        script_type = script_meta.get("type") or ("data_process" if project_patch_results else "chart")

        output_dir = None
        output_records = []
        self.script_figure_results = []
        self.current_script_figure_index = 0
        self.script_table_results = []
        self.current_script_table_index = 0
        self.pending_project_patch = project_patch_results[0] if project_patch_results else None
        if hasattr(self, "_update_project_patch_bar_visibility"):
            self._update_project_patch_bar_visibility(script_type)

        if figure_results or table_results:
            output_dir = self.get_script_output_dir(script_name)
            self.script_output_dir = output_dir
            btn_out = self.__dict__.get("btn_open_script_output")
            if btn_out is not None:
                btn_out.configure(state="normal")
            self.append_script_log(f"\n输出目录：{output_dir}\n")
        else:
            self.script_output_dir = None
            btn_out = self.__dict__.get("btn_open_script_output")
            if btn_out is not None:
                btn_out.configure(state="disabled")

        configure_matplotlib_chinese_font()

        preview_candidates = []
        for idx, fig_res in enumerate(figure_results, start=1):
            raw_fname = fig_res.filename or f"custom_chart_{idx}.png"
            safe_fname = self._safe_script_output_name(raw_fname, f"custom_chart_{idx}.png")
            root, ext = os.path.splitext(safe_fname)
            if not root:
                root = f"custom_chart_{idx}"
            if not ext or ext.lower() not in {".png", ".jpg", ".jpeg", ".svg", ".pdf"}:
                ext = ".png"

            decl_formats = getattr(fig_res, "formats", None)
            if decl_formats:
                target_exts = []
                for fmt in decl_formats:
                    e_clean = f".{str(fmt).strip().lower().lstrip('.')}"
                    if e_clean in {".png", ".jpg", ".jpeg", ".svg", ".pdf"} and e_clean not in target_exts:
                        target_exts.append(e_clean)
                if not target_exts:
                    target_exts = [ext.lower()]
            else:
                target_exts = [ext.lower()]

            save_dpi = int(getattr(fig_res, "dpi", None) or 300)
            saved_paths = []
            saved_paths_by_format = {}

            for t_ext in target_exts:
                out_file = self._unique_path(output_dir, f"{root}{t_ext}")
                try:
                    fig_res.fig.savefig(out_file, dpi=save_dpi, bbox_inches="tight")
                    saved_paths.append(out_file)
                    saved_paths_by_format[t_ext.lstrip(".")] = out_file
                    output_records.append({
                        "type": "figure",
                        "title": fig_res.title,
                        "filename": os.path.basename(out_file),
                        "format": t_ext.lstrip("."),
                        "dpi": save_dpi,
                        "saved_path": out_file,
                    })
                    self.append_script_log(f"图表 {idx} ({t_ext.upper().lstrip('.')}): {out_file}\n")
                except Exception as e:
                    self.append_script_log(f"图表 {idx} ({t_ext}) 保存失败：{e}\n")

            if not saved_paths:
                continue

            primary_output_path = saved_paths[0]
            _, primary_ext = os.path.splitext(os.path.basename(primary_output_path))
            root_name = os.path.splitext(os.path.basename(primary_output_path))[0]

            # 在内存中渲染 200 DPI PNG 供交互式画布高清缩放预览，同时若主文件为 SVG/PDF 则落盘一份预览 PNG
            pil_preview = None
            preview_path = saved_paths_by_format.get("png") or saved_paths_by_format.get("jpg") or primary_output_path
            try:
                bio = io.BytesIO()
                fig_res.fig.savefig(bio, format="png", dpi=200, bbox_inches="tight")
                png_bytes = bio.getvalue()
                bio.seek(0)
                with Image.open(bio) as im_mem:
                    pil_preview = im_mem.convert("RGBA")
                    pil_preview.load()
                if primary_ext.lower() in (".svg", ".pdf") and "png" not in saved_paths_by_format:
                    preview_dir = self.get_script_preview_dir(output_dir)
                    preview_png = os.path.join(preview_dir, f"{root_name}_preview_{idx}.png")
                    with open(preview_png, "wb") as pf:
                        pf.write(png_bytes)
                    preview_path = preview_png
            except Exception:
                pass

            fig_id = id(fig_res.fig)
            preview_candidates.append({
                "result": fig_res,
                "output_path": primary_output_path,
                "saved_paths": saved_paths,
                "saved_paths_by_format": saved_paths_by_format,
                "preview_path": preview_path,
                "pil_image": pil_preview,
                "title": fig_res.title,
                "filename": os.path.basename(primary_output_path),
                "basename": root_name,
                "ext": primary_ext.lower(),
                "dpi": save_dpi,
                "fig_id": fig_id,
            })

        try:
            import matplotlib.pyplot as plt
            for fig_res in figure_results:
                try:
                    plt.close(fig_res.fig)
                except Exception:
                    pass
        except Exception:
            pass

        png_basenames = {c["basename"] for c in preview_candidates if c["ext"] in (".png", ".jpg", ".jpeg")}
        filtered_results = []
        seen_fig_ids = set()
        for c in preview_candidates:
            if c["ext"] == ".svg" and c["basename"] in png_basenames and len(c.get("saved_paths", [])) == 1:
                continue
            if c["fig_id"] not in seen_fig_ids:
                seen_fig_ids.add(c["fig_id"])
                filtered_results.append(c)

        self.script_figure_results = filtered_results

        for idx, tbl_res in enumerate(table_results, start=1):
            base_name = self._safe_script_output_name(tbl_res.title or f"custom_table_{idx}", f"custom_table_{idx}")
            table_path = self._unique_path(output_dir, f"{base_name}.csv")
            cols_list = list(tbl_res.columns or [])
            rows_list = [list(r) for r in (tbl_res.rows or [])]
            try:
                with open(table_path, "w", encoding="utf-8-sig", newline="") as f:
                    writer = csv.writer(f)
                    if cols_list:
                        writer.writerow(cols_list)
                    writer.writerows(rows_list)
                output_records.append({
                    "type": "table",
                    "title": tbl_res.title,
                    "filename": os.path.basename(table_path),
                    "saved_path": table_path,
                })
                self.append_script_log(f"表格 {idx}：{table_path}\n")
            except Exception as e:
                self.append_script_log(f"表格 {idx} 保存失败：{e}\n")
            self.script_table_results.append({
                "result": tbl_res,
                "title": tbl_res.title or f"数据表 {idx}",
                "columns": cols_list,
                "rows": rows_list,
                "output_path": table_path,
            })

        # 构造运行记录供无弹窗归档使用
        try:
            from modules.version import __version__
        except Exception:
            __version__ = "1.0.0"

        code_text = ""
        if hasattr(self, "txt_script_code_preview") and self.txt_script_code_preview:
            try:
                code_text = self.txt_script_code_preview.get("1.0", tk.END).strip()
            except Exception:
                code_text = ""
        code_sha256 = hashlib.sha256(code_text.encode("utf-8")).hexdigest()
        self.last_script_run_record = {
            "script_id": getattr(self, "selected_script_id", None) or str(uuid.uuid4()),
            "script_name": script_name,
            "script_type": script_type,
            "api_version": "2",
            "software_version": __version__,
            "code_sha256": code_sha256,
            "code": code_text,
            "params": dict(getattr(self, "last_run_params", None) or {}),
            "used_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "user_goal": script_desc,
            "status": "成功",
            "outputs": list(output_records),
        }

        if hasattr(self, "btn_archive_script_run") and self.btn_archive_script_run:
            has_proj = bool(getattr(self, "loaded_teproj_path", None) and getattr(self, "project_data", None))
            self.btn_archive_script_run.configure(state="normal" if has_proj else "disabled")

        # 展示图表与表格预览
        if self.script_figure_results:
            self.show_script_figure_at(0)
        elif self.script_table_results:
            self.show_script_table_at(0)
        elif not project_patch_results:
            self._update_chart_preview("暂无图表或表格预览", None)
            if hasattr(self, "script_preview_nav") and self.script_preview_nav:
                self.script_preview_nav.grid_remove()

        # 处理工程补丁 (ProjectPatchResult) —— 支持与图表/表格联合返回！
        if project_patch_results:
            patch_res = project_patch_results[0]
            self.pending_project_patch = patch_res
            btn_patch = getattr(self, "btn_apply_project_patch", None)
            if btn_patch is not None:
                if patch_res.operation_count > 0:
                    btn_patch.configure(
                        state="normal",
                        text=f"应用补丁并另存工程 ({patch_res.operation_count}项)",
                    )
                else:
                    btn_patch.configure(state="disabled", text="补丁无待执行操作")
            self._rebuild_artifact_capsule_tabs()

            has_visual_artifacts = bool(self.script_figure_results or self.script_table_results)
            auto_apply = bool(getattr(self, "var_auto_apply_patch", None) and self.var_auto_apply_patch.get())

            if has_visual_artifacts:
                self.append_script_log(
                    f"\n[工程补丁就绪] {patch_res.title}：共生成 {patch_res.operation_count} 项工程修改操作。\n"
                    "您可先在左侧预览对比图与明细表，确认无误后点击上方 [应用补丁并另存工程] 按钮完成落盘。\n"
                )
                if auto_apply and patch_res.operation_count > 0:
                    self.apply_pending_project_patch()
            else:
                # 仅返回 ProjectPatchResult 时直接触发应用流程
                self.apply_pending_project_patch()
                return

        # 非阻塞式归档（不再每次弹出模态 messagebox.askyesno 打断看图）
        if getattr(self, "loaded_teproj_path", None) and getattr(self, "project_data", None):
            auto_archive = bool(getattr(self, "var_auto_archive_script_run", None) and self.var_auto_archive_script_run.get())
            if auto_archive:
                self.archive_last_script_run_to_project()
            else:
                self.append_script_log("提示：如需将本次运行写入工程记录，可点击右下角 [归档本次运行到工程] 或勾选 [自动归档到当前工程]。\n")

    def archive_last_script_run_to_project(self):
        if not getattr(self, "loaded_teproj_path", None) or not getattr(self, "project_data", None):
            self.append_script_log("当前未加载工程，无法归档脚本运行记录。\n")
            return False
        run_record = getattr(self, "last_script_run_record", None)
        if not run_record:
            self.append_script_log("当前没有可归档的脚本运行记录。\n")
            return False

        if "custom_script_runs" not in self.project_data:
            self.project_data["custom_script_runs"] = []

        script_runs = self.project_data["custom_script_runs"]
        prev_runs = copy.deepcopy(script_runs)
        from modules.project_patch import _append_or_update_script_run
        _append_or_update_script_run(script_runs, copy.deepcopy(run_record))

        if self.save_project_data_back_to_teproj():
            self.append_script_log("本次脚本运行已成功写入当前工程记录。\n")
            return True
        else:
            self.project_data["custom_script_runs"] = prev_runs
            self.append_script_log("本次脚本运行写入工程记录失败。\n")
            return False

    def apply_pending_project_patch(self, output_path=None, run_synchronously=False):
        from modules.project_patch import apply_project_patch_to_teproj

        patch_res = getattr(self, "pending_project_patch", None)
        if patch_res is None:
            self.append_script_log("当前没有待应用的工程补丁。\n")
            return None

        if not getattr(self, "loaded_teproj_path", None):
            self.append_script_log("未加载任何工程，无法应用数据处理结果。\n")
            if not (getattr(self, "script_figure_results", None) or getattr(self, "script_table_results", None)):
                self._update_chart_preview("未加载工程，操作未应用。", None)
            messagebox.showwarning("警告", "当前未加载工程，无法应用数据处理操作。")
            return None

        if not patch_res.operations:
            self.append_script_log("脚本没有返回任何工程修改操作。\n")
            if not (getattr(self, "script_figure_results", None) or getattr(self, "script_table_results", None)):
                self._update_chart_preview("脚本没有返回任何工程修改操作。", None)
            return None

        if not output_path:
            orig_dir, orig_name = os.path.split(self.loaded_teproj_path)
            name_part, ext_part = os.path.splitext(orig_name)
            default_output_name = f"patched_{name_part}{ext_part}"
            output_path = filedialog.asksaveasfilename(
                title="保存修改后的工程",
                initialdir=orig_dir,
                initialfile=default_output_name,
                defaultextension=".teproj",
                filetypes=[("PhonTracer Project", "*.teproj")]
            )
        if not output_path:
            self.append_script_log("用户取消了保存，工程补丁未应用。\n")
            return None

        if os.path.normcase(os.path.abspath(output_path)) == os.path.normcase(os.path.abspath(self.loaded_teproj_path)):
            self.append_script_log("输出路径与原工程相同，操作未应用。请另存为新的 .teproj 文件。\n")
            messagebox.showwarning("警告", "数据处理脚本不会覆盖原工程，请另存为新的 .teproj 文件。")
            return None

        run_record = copy.deepcopy(getattr(self, "last_script_run_record", None) or {})
        run_record.setdefault("script_name", patch_res.title or "数据处理脚本")
        run_record.setdefault("script_type", "data_process")
        outputs_list = list(run_record.get("outputs") or [])
        outputs_list.append({
            "type": "project",
            "title": patch_res.title or "数据处理后的工程",
            "filename": os.path.basename(output_path),
            "saved_path": output_path,
        })
        run_record["outputs"] = outputs_list

        self.append_script_log("\n正在后台解压工程、应用修改并重新打包，请稍候...\n")
        if hasattr(self, "set_loading") and hasattr(self, "progress"):
            try:
                self.set_loading(True, "正在应用数据处理并重新生成工程归档...", indeterminate=True)
            except Exception:
                pass
        if hasattr(self, "btn_run_script") and self.btn_run_script:
            self.btn_run_script.configure(state="disabled")

        if run_synchronously:
            try:
                summary = apply_project_patch_to_teproj(
                    self.loaded_teproj_path,
                    patch_res,
                    output_path,
                    run_record=run_record,
                )
                self._on_project_patch_complete(summary, output_path, notify_modal=False)
                return summary
            except Exception as e:
                tb = traceback.format_exc()
                self._on_project_patch_failed(e, tb, notify_modal=False)
                raise
            finally:
                self._on_project_patch_cleanup()

        def do_apply_patch():
            try:
                summary = apply_project_patch_to_teproj(
                    self.loaded_teproj_path,
                    patch_res,
                    output_path,
                    run_record=run_record
                )
                self.after(0, lambda: self._on_project_patch_complete(summary, output_path))
            except Exception as e:
                tb = traceback.format_exc()
                self.after(0, lambda err=e, trace=tb: self._on_project_patch_failed(err, trace))
            finally:
                self.after(0, lambda: self._on_project_patch_cleanup())

        start_safe_thread(do_apply_patch)
        return output_path

    def _on_project_patch_complete(self, summary, output_path, notify_modal=True):
        self.append_script_log("\n=== 数据处理应用结果 ===\n")
        self.append_script_log(f"操作名称: {summary.get('title') or '未命名'}\n")
        if summary.get('description'):
            self.append_script_log(f"操作说明: {summary.get('description')}\n")

        self.append_script_log(f"包含操作总数: {summary.get('operation_count', 0)}\n")

        applied_ops = summary.get("applied_operations", [])
        self.append_script_log(f"实际执行细节 (共 {len(applied_ops)} 项):\n")
        for r in applied_ops:
            self.append_script_log(f"  - {r.get('message') or r.get('op')}\n")

        output_paths = summary.get("output_paths", [])
        if output_paths:
            self.append_script_log("\n已成功拆分为以下子工程文件：\n")
            for path in output_paths:
                self.append_script_log(f"  - {path}\n")
        else:
            self.append_script_log(f"\n已生成工程文件: {summary.get('output_path')}\n")

        if not getattr(self, "script_output_dir", None):
            self.script_output_dir = os.path.dirname(output_path)
        if hasattr(self, "btn_open_script_output") and self.btn_open_script_output:
            self.btn_open_script_output.configure(state="normal")

        has_visuals = bool(getattr(self, "script_figure_results", None) or getattr(self, "script_table_results", None))
        if not has_visuals:
            self._update_chart_preview("数据处理脚本运行成功，无图表输出。", None)
            if hasattr(self, "script_preview_nav") and self.script_preview_nav:
                self.script_preview_nav.grid_remove()

            if not output_paths:
                single_path = summary.get("output_path")
                if single_path:
                    output_paths = [single_path]

            if output_paths and hasattr(self, "script_file_list_frame") and self.script_file_list_frame:
                self._switch_preview_viewport_mode("files")
                for widget in self.script_file_list_frame.winfo_children():
                    widget.destroy()

                for path in output_paths:
                    file_name = os.path.basename(path)
                    row_frame = ctk.CTkFrame(self.script_file_list_frame, fg_color="transparent")
                    row_frame.pack(fill=tk.X, padx=5, pady=2)

                    btn = ctk.CTkButton(
                        row_frame,
                        text=f"工程文件：{file_name}",
                        anchor="w",
                        fg_color="transparent",
                        text_color=self.colors["primary"],
                        hover_color=self.colors["surface_soft"],
                        font=ctk.CTkFont(family=self.font_family, size=11),
                        height=28,
                        command=lambda p=path: self._open_file_safely(p)
                    )
                    btn.pack(side=tk.LEFT, fill=tk.X, expand=True)

        if notify_modal:
            messagebox.showinfo("成功", summary.get("message") or "操作成功应用！")

    def _on_project_patch_failed(self, e, tb, notify_modal=True):
        self.append_script_log(f"应用数据处理操作失败：\n{e}\n\n{tb}\n")
        if not (getattr(self, "script_figure_results", None) or getattr(self, "script_table_results", None)):
            self._update_chart_preview("数据处理应用失败。", None)
            if hasattr(self, "script_preview_nav") and self.script_preview_nav:
                self.script_preview_nav.grid_remove()
        if notify_modal:
            messagebox.showerror("错误", f"应用数据处理操作失败：{e}")

    def _on_project_patch_cleanup(self):
        if hasattr(self, 'set_loading') and hasattr(self, 'progress'):
            try:
                self.set_loading(False)
            except Exception:
                pass
        if hasattr(self, 'btn_run_script') and self.btn_run_script:
            self.btn_run_script.configure(state="normal")

    def save_project_data_back_to_teproj(self):
        if not hasattr(self, 'loaded_teproj_path') or not self.loaded_teproj_path:
            return False
        import tempfile
        import zipfile
        import json

        zip_path = self.loaded_teproj_path
        zip_dir = os.path.dirname(os.path.abspath(zip_path)) or "."
        temp_fd, temp_path = tempfile.mkstemp(prefix=".script_update_", suffix=".teproj", dir=zip_dir)
        os.close(temp_fd)
        try:
            import shutil
            with zipfile.ZipFile(zip_path, 'r') as yin:
                with zipfile.ZipFile(temp_path, 'w', zipfile.ZIP_DEFLATED) as yout:
                    for item in yin.infolist():
                        if item.filename == 'project.json':
                            continue
                        if item.is_dir():
                            yout.writestr(item, b'')
                            continue
                        with yin.open(item, 'r') as src, yout.open(item, 'w') as dst:
                            shutil.copyfileobj(src, dst, length=1024 * 1024)
                    json_bytes = json.dumps(self.project_data, ensure_ascii=False, indent=2).encode('utf-8')
                    yout.writestr('project.json', json_bytes)
            read_project_metadata_from_archive(temp_path)
            os.replace(temp_path, zip_path)
            return True
        except Exception as e:
            messagebox.showerror("错误", f"更新工程文件归档失败：{e}")
            return False
        finally:
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except Exception:
                    pass

    def update_script_tab_project_summary(self):
        self.txt_script_log.configure(state="normal")
        self.txt_script_log.delete("1.0", tk.END)
        if not hasattr(self, 'project_data') or not self.project_data:
            self.txt_script_log.insert(tk.END, "工程数据状态: 未加载工程\n")
            self.txt_script_log.configure(state="disabled")
            if hasattr(self, "btn_archive_script_run") and self.btn_archive_script_run:
                self.btn_archive_script_run.configure(state="disabled")
            return

        spk_count = len(self.project_data.get("speakers", {}))
        item_count = 0
        groups = set()
        for spk in self.project_data.get("speakers", {}).values():
            items = spk.get("items", {})
            item_count += len(items)
            for item in items.values():
                g = item.get("group")
                if g:
                    groups.add(g)

        summary_text = f"工程: {os.path.basename(self.loaded_teproj_path)}\n发音人数: {spk_count} | 条目总数: {item_count}\n声调分组数: {len(groups)} ({', '.join(sorted(list(groups))) if groups else '无'})"
        self.txt_script_log.insert(tk.END, "=== 工程数据状态 ===\n" + summary_text + "\n")
        self.txt_script_log.configure(state="disabled")
        if hasattr(self, "btn_archive_script_run") and self.btn_archive_script_run and getattr(self, "last_script_run_record", None):
            self.btn_archive_script_run.configure(state="normal")

    def _refresh_top_bar_adaptive_wraps(self):
        for attr in ("lbl_selected_script_desc", "lbl_script_project_status", "lbl_script_project_detail"):
            lbl = getattr(self, attr, None)
            wrap_fn = getattr(lbl, "_update_adaptive_wrap", None) if lbl is not None else None
            if callable(wrap_fn):
                try:
                    wrap_fn()
                    self.after(20, wrap_fn)
                except Exception:
                    pass

    def _set_script_project_card_loading(self, path):
        if not hasattr(self, "lbl_script_project_status"):
            return
        file_name = os.path.basename(path) if path else "未选择"
        self.lbl_script_project_status.configure(text=f"正在加载：{file_name}", text_color=self.colors["primary"])
        self.lbl_script_project_detail.configure(text="正在读取工程元数据，并同步刷新工程预览页。", text_color=self.colors["muted"])
        self._refresh_top_bar_adaptive_wraps()

    def _set_script_project_card_error(self, message):
        if not hasattr(self, "lbl_script_project_status"):
            return
        self.lbl_script_project_status.configure(text="工程解析失败", text_color=self.colors["danger"])
        self.lbl_script_project_detail.configure(text=message, text_color=self.colors["danger"])
        self._refresh_top_bar_adaptive_wraps()

    def _set_script_project_card_summary(self, project_data=None):
        if not hasattr(self, "lbl_script_project_status"):
            return
        project_data = project_data or getattr(self, "project_data", None)
        path = getattr(self, "loaded_teproj_path", None)
        if not path or not project_data:
            self.lbl_script_project_status.configure(text="未加载工程", text_color=self.colors["muted"])
            self.lbl_script_project_detail.configure(
                text="可在此处或“工程预览”页选择同一个 .teproj 工程。",
                text_color=self.colors["muted"],
            )
            if hasattr(self, "btn_script_load_project") and self.btn_script_load_project:
                self.btn_script_load_project.configure(text="选择工程")
            self._refresh_top_bar_adaptive_wraps()
            return

        speakers = project_data.get("speakers", {})
        item_count = sum(len(spk.get("items", {})) for spk in speakers.values())
        self.lbl_script_project_status.configure(
            text=f"已加载：{os.path.basename(path)}",
            text_color=self.colors["success"],
        )
        self.lbl_script_project_detail.configure(
            text=f"发音人数：{len(speakers)}；条目总数：{item_count}。工程预览页已同步。",
            text_color=self.colors["muted"],
        )
        if hasattr(self, "btn_script_load_project") and self.btn_script_load_project:
            self.btn_script_load_project.configure(text="切换工程")
        self._refresh_top_bar_adaptive_wraps()

    def show_prompt_dialog(self):
        script_type = "chart"
        script = next((s for s in self.local_scripts if s.get("id") == self.selected_script_id), None)
        if script:
            script_type = script.get("type", "chart")
        elif hasattr(self, "combo_script_type") and self.combo_script_type.get() == "数据处理脚本":
            script_type = "data_process"
        AIPromptDialog(self, self.project_data if hasattr(self, 'project_data') else None, script_type=script_type)

    def select_project_file(self):
        path = filedialog.askopenfilename(filetypes=[("PhonTracer Project", "*.teproj")])
        if path:
            self.load_project_file(path)

    def clear_project_script_runs(self):
        if not hasattr(self, "loaded_teproj_path") or not self.loaded_teproj_path:
            messagebox.showinfo("提示", "请先打开一个工程文件。")
            return
        if not hasattr(self, "project_data") or not self.project_data:
            messagebox.showinfo("提示", "当前没有可清理的工程数据。")
            return

        count = len(self.project_data.get("custom_script_runs", []) or [])
        if count <= 0:
            messagebox.showinfo("提示", "当前工程没有脚本运行记录。")
            return

        ok = messagebox.askyesno(
            "确认清理",
            f"确认清理当前工程中的 {count} 条脚本运行记录吗？\n\n"
            "这只会移除工程里的脚本运行历史和源码归档，不会删除本地脚本库、音频、条目、缓存数据或已导出的图表文件。",
        )
        if not ok:
            return

        self.project_data.pop("custom_script_runs", None)
        if not self.save_project_data_back_to_teproj():
            return
        self.display_project_preview(self.project_data, getattr(self, "project_namelist", []))
        self.update_script_tab_project_summary()
        messagebox.showinfo("完成", "已清理当前工程中的脚本运行记录。")

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


class ScriptEditorDialog(ctk.CTkToplevel):
    """
    新建/编辑自定义脚本的弹出式对话框。
    """
    def __init__(self, parent, script_id=None, script_name="", script_desc="", script_code="", script_type="chart", initial_line=None):
        super().__init__(parent)
        self.parent = parent
        self.script_id = script_id
        self.script_type = script_type
        self._auto_filled_name = ""
        self._auto_filled_desc = ""
        self._metadata_parse_after_id = None
        self._line_number_after_id = None

        self.title("新建自定义脚本" if not script_id else "编辑自定义脚本")
        self.geometry("720x640")

        # 居中显示
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        x = (sw - 720) // 2
        y = (sh - 640) // 2
        self.geometry(f"720x640+{x}+{y}")
        self.transient(parent)
        self.grab_set()

        self.configure(fg_color=("#F9FAFB", "#1A1D24"))

        # 顶边条
        accent_strip = ctk.CTkFrame(self, height=4, fg_color="#6366F1", corner_radius=0)
        accent_strip.pack(fill="x", side="top")

        content = ctk.CTkFrame(self, fg_color="transparent")
        content.pack(fill="both", expand=True, padx=24, pady=18)

        # 脚本名称
        row_name = ctk.CTkFrame(content, fg_color="transparent")
        row_name.pack(fill="x", pady=6)
        ctk.CTkLabel(row_name, text="脚本名称:", font=parent.font_main, text_color=parent.colors["text"], width=70, anchor="w").pack(side="left")
        self.entry_name = ctk.CTkEntry(
            row_name,
            height=36,
            corner_radius=18,
            font=ctk.CTkFont(family="Microsoft YaHei", size=13),
            fg_color=("#F9FAFB", "#262930"),
            border_color=("#D1D5DB", "#374151"),
            text_color=("#111827", "#F9FAFB")
        )
        self.entry_name.insert(0, script_name)
        self.entry_name.pack(side="left", fill="x", expand=True, padx=(5, 0))
        bind_text_context_menu(self.entry_name)

        # 功能说明
        row_desc = ctk.CTkFrame(content, fg_color="transparent")
        row_desc.pack(fill="x", pady=6)
        ctk.CTkLabel(row_desc, text="功能说明:", font=parent.font_main, text_color=parent.colors["text"], width=70, anchor="w").pack(side="left")
        self.entry_desc = ctk.CTkEntry(
            row_desc,
            height=36,
            corner_radius=18,
            font=ctk.CTkFont(family="Microsoft YaHei", size=13),
            fg_color=("#F9FAFB", "#262930"),
            border_color=("#D1D5DB", "#374151"),
            text_color=("#111827", "#F9FAFB")
        )
        self.entry_desc.insert(0, script_desc)
        self.entry_desc.pack(side="left", fill="x", expand=True, padx=(5, 0))
        bind_text_context_menu(self.entry_desc)

        # 脚本类型
        row_type = ctk.CTkFrame(content, fg_color="transparent")
        row_type.pack(fill="x", pady=6)
        ctk.CTkLabel(row_type, text="脚本类型:", font=parent.font_main, text_color=parent.colors["text"], width=70, anchor="w").pack(side="left")
        self.var_script_type = ctk.StringVar(value="数据处理脚本" if script_type == "data_process" else "图表脚本")
        self.combo_script_type = ctk.CTkOptionMenu(
            row_type,
            values=["图表脚本", "数据处理脚本"],
            variable=self.var_script_type,
            fg_color=("#F3F4F6", "#374151"),
            text_color=("#1F2937", "#E5E7EB"),
            button_color=("#F3F4F6", "#374151"),
            button_hover_color=("#E5E7EB", "#4B5563"),
            height=32,
            corner_radius=16,
        )
        self.combo_script_type.pack(side="left", fill="x", expand=True, padx=(5, 0))
        _apply_custom_arrow(self.combo_script_type)

        # 代码编辑区
        ctk.CTkLabel(content, text="代码编辑区 (Python 3):", font=parent.font_main, text_color=parent.colors["text"]).pack(anchor="w", pady=(10, 4))
        editor_frame = ctk.CTkFrame(
            content,
            fg_color=("#FFFFFF", "#262930"),
            corner_radius=8,
            border_width=1,
            border_color=("#D1D5DB", "#374151"),
        )
        editor_frame.pack(fill="both", expand=True, pady=(0, 10))
        editor_frame.grid_columnconfigure(1, weight=1)
        editor_frame.grid_rowconfigure(0, weight=1)

        self.txt_code_lines = ctk.CTkTextbox(
            editor_frame,
            width=48,
            font=ctk.CTkFont(family="Consolas", size=13),
            wrap="none",
            border_width=0,
            activate_scrollbars=False,
            fg_color=("#F3F4F6", "#1F2937"),
            text_color=("#64748B", "#94A3B8"),
        )
        self.txt_code_lines.grid(row=0, column=0, sticky="nsw")
        self.txt_code = ctk.CTkTextbox(
            editor_frame,
            font=ctk.CTkFont(family="Consolas", size=13),
            wrap="none",
            border_width=0,
            fg_color=("#FFFFFF", "#262930"),
            text_color=("#111827", "#F9FAFB"),
            scrollbar_button_color=("#9CA3AF", "#6B7280"),
            scrollbar_button_hover_color=("#6B7280", "#9CA3AF"),
        )
        self.txt_code.insert("1.0", script_code)
        self.txt_code.grid(row=0, column=1, sticky="nsew")
        self.highlighted_line_no = None
        self._setup_code_editor_widgets()
        self._bind_metadata_autofill()
        self._refresh_code_line_numbers()
        self._schedule_script_metadata_parse()
        if initial_line is not None:
            self.after(50, lambda ln=initial_line: self.goto_line(ln))

        # 加速滚动
        def speed_up_edit_scroll(event):
            scroll_units = int(-1 * (event.delta / 120) * 4)
            self.txt_code.yview_scroll(scroll_units, "units")
            self._sync_code_line_numbers()
            return "break"
        self.txt_code.bind("<MouseWheel>", speed_up_edit_scroll)
        if hasattr(self.txt_code, "_textbox"):
            self.txt_code._textbox.bind("<MouseWheel>", speed_up_edit_scroll)

        # 底部按钮区
        btn_frame = ctk.CTkFrame(self, height=60, fg_color="transparent")
        btn_frame.pack(fill="x", side="bottom", padx=24, pady=10)

        btn_cancel = ctk.CTkButton(
            btn_frame, text="取消", fg_color="#F3F4F6", text_color="#1F2937", hover_color="#E5E7EB",
            font=ctk.CTkFont(family="Microsoft YaHei", size=13, weight="bold"), height=36, corner_radius=18,
            command=self.destroy
        )
        btn_cancel.pack(side="left")

        btn_save = ctk.CTkButton(
            btn_frame, text="保存脚本", fg_color="#10B981", text_color="white", hover_color="#059669",
            font=ctk.CTkFont(family="Microsoft YaHei", size=13, weight="bold"), height=36, corner_radius=18,
            command=self.save_script
        )
        btn_save.pack(side="right")

    def _setup_code_editor_widgets(self):
        for textbox in (self.txt_code, self.txt_code_lines):
            if hasattr(textbox, "_textbox"):
                textbox._textbox.configure(
                    padx=8,
                    pady=8,
                    insertwidth=2,
                    spacing1=1,
                    spacing3=1,
                )
        if hasattr(self.txt_code, "_textbox"):
            try:
                self.txt_code._textbox.configure(undo=True, maxundo=-1, autoseparators=True)
            except Exception:
                pass
        bind_text_context_menu(self.txt_code)
        if hasattr(self.txt_code_lines, "_textbox"):
            self.txt_code_lines._textbox.configure(takefocus=0, cursor="arrow")
        self.txt_code_lines.configure(state="disabled")
        if hasattr(self.txt_code, "_textbox") and hasattr(self.txt_code, "_y_scrollbar"):
            self.txt_code._textbox.configure(yscrollcommand=self._on_code_y_scroll)
        for event_name in ("<ButtonRelease-1>", "<KeyRelease>", "<<Paste>>", "<<Cut>>", "<Configure>"):
            self.txt_code.bind(event_name, self._schedule_code_line_number_refresh, add="+")
            if hasattr(self.txt_code, "_textbox"):
                self.txt_code._textbox.bind(event_name, self._schedule_code_line_number_refresh, add="+")

    def _on_code_y_scroll(self, first, last):
        if hasattr(self.txt_code, "_y_scrollbar"):
            self.txt_code._y_scrollbar.set(first, last)
        self._sync_code_line_numbers(first)

    def _sync_code_line_numbers(self, first=None):
        if not hasattr(self, "txt_code_lines"):
            return
        if first is None:
            try:
                first = self.txt_code.yview()[0]
            except Exception:
                return
        try:
            self.txt_code_lines.yview_moveto(first)
        except Exception:
            pass

    def _schedule_code_line_number_refresh(self, _event=None):
        if getattr(self, "_line_number_after_id", None):
            try:
                self.after_cancel(self._line_number_after_id)
            except Exception:
                pass
        self._line_number_after_id = self.after(30, self._refresh_code_line_numbers)

    def _refresh_code_line_numbers(self):
        self._line_number_after_id = None
        try:
            line_count = int(self.txt_code.index("end-1c").split(".")[0])
        except Exception:
            line_count = 1
        line_count = max(1, line_count)
        width = max(2, len(str(line_count)))
        numbers = "\n".join(f"{idx:>{width}}" for idx in range(1, line_count + 1))
        self.txt_code_lines.configure(state="normal")
        self.txt_code_lines.delete("1.0", tk.END)
        self.txt_code_lines.insert("1.0", numbers)
        self.txt_code_lines.configure(state="disabled")
        self._sync_code_line_numbers()

    def _bind_metadata_autofill(self):
        events = ("<<Paste>>", "<KeyRelease>")
        for event_name in events:
            self.txt_code.bind(event_name, self._schedule_script_metadata_parse, add="+")
            if hasattr(self.txt_code, "_textbox"):
                self.txt_code._textbox.bind(event_name, self._schedule_script_metadata_parse, add="+")

    def _schedule_script_metadata_parse(self, _event=None):
        if self._metadata_parse_after_id:
            try:
                self.after_cancel(self._metadata_parse_after_id)
            except Exception:
                pass
        self._metadata_parse_after_id = self.after(80, self._autofill_metadata_from_code)

    def _replace_entry_text(self, entry, text):
        entry.delete(0, tk.END)
        entry.insert(0, text)

    def _can_autofill_field(self, entry, default_text, last_auto_text):
        current = entry.get().strip()
        return current in ("", default_text, last_auto_text)

    def _autofill_metadata_from_code(self):
        self._metadata_parse_after_id = None
        code = self.txt_code.get("1.0", tk.END)
        stripped_code = strip_markdown_code_fence(code)
        metadata = parse_script_metadata_comments(stripped_code)

        name = metadata.get("name", "").strip()
        if name and self._can_autofill_field(self.entry_name, SCRIPT_EDITOR_DEFAULT_NAME, self._auto_filled_name):
            self._replace_entry_text(self.entry_name, name)
            self._auto_filled_name = name

        desc = metadata.get("description", "").strip()
        if desc and self._can_autofill_field(self.entry_desc, SCRIPT_EDITOR_DEFAULT_DESC, self._auto_filled_desc):
            self._replace_entry_text(self.entry_desc, desc)
            self._auto_filled_desc = desc

    def save_script(self):
        name = self.entry_name.get().strip()
        desc = self.entry_desc.get().strip()
        code = strip_markdown_code_fence(self.txt_code.get("1.0", tk.END))

        if not name:
            return messagebox.showwarning("提示", "请输入脚本名称")

        from modules.script_manager import save_script, load_all_scripts
        self.script_type = "data_process" if self.var_script_type.get() == "数据处理脚本" else "chart"
        new_id = save_script(self.script_id, name, desc, self.script_type, code)

        self.parent.local_scripts = load_all_scripts()
        self.parent.load_scripts_to_tree()
        self.parent.script_tree.selection_set(new_id)
        if hasattr(self.parent, "on_script_selected"):
            self.parent.on_script_selected()

        messagebox.showinfo("成功", "脚本保存成功！")
        self.destroy()

    def goto_line(self, line_no):
        """滚动并高亮定位到代码编辑器的第 line_no 行（1-based）。"""
        try:
            line_no = int(line_no)
        except (TypeError, ValueError):
            return False
        if line_no < 1:
            line_no = 1
        try:
            total_lines = int(self.txt_code.index("end-1c").split(".")[0])
        except Exception:
            total_lines = max(1, len(self.txt_code.get("1.0", tk.END).splitlines()))
        line_no = min(line_no, max(1, total_lines))
        self.highlighted_line_no = line_no
        target_start = f"{line_no}.0"
        target_end = f"{line_no}.end"
        tb = getattr(self.txt_code, "_textbox", self.txt_code)
        try:
            tb.tag_remove("error_highlight", "1.0", tk.END)
            tb.tag_config("error_highlight", background="#FEE2E2", foreground="#991B1B")
            tb.tag_add("error_highlight", target_start, target_end)
        except Exception:
            pass
        try:
            self.txt_code.mark_set("insert", target_start)
            self.txt_code.see(target_start)
            self.txt_code.focus_set()
        except Exception:
            pass
        self._refresh_code_line_numbers()
        return True

    def destroy(self):
        for attr in ("_metadata_parse_after_id", "_line_number_after_id"):
            job = getattr(self, attr, None)
            setattr(self, attr, None)
            if job is not None:
                try:
                    self.after_cancel(job)
                except Exception:
                    pass
        try:
            self.grab_release()
        except Exception:
            pass
        super().destroy()


class AIPromptDialog(ctk.CTkToplevel):
    """
    生成 AI 提示词的引导式对话框。
    """
    def __init__(self, parent, project_data, script_type="chart"):
        super().__init__(parent)
        self.parent = parent
        self.project_data = project_data
        self.script_type = script_type or "chart"
        self.colors = parent.colors
        self.font_family = getattr(parent, "font_family", "Microsoft YaHei")

        self.title("PhonTracer语音学标注与声学分析系统 V1.0 - 生成 AI 数据处理脚本提示词" if self.script_type == "data_process" else "PhonTracer语音学标注与声学分析系统 V1.0 - 生成 AI 脚本提示词")
        self.geometry("620x620")
        self.resizable(True, True)

        # 居中显示
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        x = (sw - 620) // 2
        y = (sh - 620) // 2
        self.geometry(f"620x620+{x}+{y}")
        self.transient(parent)
        self.grab_set()

        self.configure(fg_color=("#F9FAFB", "#1A1D24"))

        # 顶边条
        accent_strip = ctk.CTkFrame(self, height=4, fg_color="#6366F1", corner_radius=0)
        accent_strip.pack(fill="x", side="top")

        self.setup_ui()

    def _section_label(self, parent, text):
        lbl = ctk.CTkLabel(parent, text=text, font=ctk.CTkFont(family="Microsoft YaHei", size=13, weight="bold"), text_color="#374151")
        lbl.pack(anchor="w", padx=10, pady=(15, 2))

    def _make_option_menu(self, parent, values, variable, command=None):
        combo = ctk.CTkOptionMenu(
            parent,
            values=values,
            variable=variable,
            command=command,
            fg_color=("#F3F4F6", "#374151"),
            text_color=("#1F2937", "#E5E7EB"),
            button_color=("#F3F4F6", "#374151"),
            button_hover_color=("#E5E7EB", "#4B5563"),
            height=32,
            corner_radius=16
        )
        combo.pack(fill="x", pady=2)
        _apply_custom_arrow(combo)
        return combo

    def _make_textbox(self, parent, height=80, placeholder=None):
        if placeholder:
            ctk.CTkLabel(
                parent,
                text=placeholder,
                font=ctk.CTkFont(family="Microsoft YaHei", size=11),
                text_color="#64748B",
                wraplength=520,
                justify="left"
            ).pack(anchor="w", padx=10, pady=(0, 2))
        box = ctk.CTkTextbox(
            parent,
            height=height,
            border_width=1,
            border_color=("#D1D5DB", "#374151"),
            fg_color=("#FFFFFF", "#262930"),
            text_color=("#111827", "#F9FAFB")
        )
        box.pack(fill="x", padx=10, pady=5)
        bind_text_context_menu(box)
        return box

    def _textbox_value_without_placeholder(self, textbox, placeholder):
        value = textbox.get("1.0", tk.END).strip()
        if value == (placeholder or "").strip():
            return ""
        return value

    def _on_prompt_tab_changed(self):
        self.after(1, self._update_prompt_tab_text_colors)

    def _update_prompt_tab_text_colors(self):
        segmented = getattr(self.prompt_tabview, "_segmented_button", None)
        buttons = getattr(segmented, "_buttons_dict", {}) if segmented is not None else {}
        current = self.prompt_tabview.get() if hasattr(self, "prompt_tabview") else None
        unselected_text = "#F9FAFB" if ctk.get_appearance_mode().lower() == "dark" else "#111827"
        for name, button in buttons.items():
            try:
                button.configure(text_color="#FFFFFF" if name == current else unselected_text)
            except Exception:
                pass

    def _speed_up_scroll_recursive(self, scroll):
        def _wheel_steps(delta, multiplier):
            if delta == 0:
                return 0
            if abs(delta) >= 120:
                return -int(delta / 120) * multiplier
            return -1 * multiplier if delta > 0 else multiplier

        def speed_up_main_scroll(event):
            canvas = getattr(scroll, "_parent_canvas", None)
            if canvas is not None:
                delta = getattr(event, "delta", 0)
                if delta != 0:
                    try:
                        canvas.configure(yscrollincrement=8)
                    except Exception:
                        pass
                    steps = _wheel_steps(delta, 12)
                    canvas.yview_scroll(steps, "units")
                return "break"

        def safe_mousewheel_bind(widget, handler):
            try:
                widget.bind("<MouseWheel>", handler, add="+")
            except (NotImplementedError, tk.TclError, AttributeError):
                for attr in ("_canvas", "_text_label", "_image_label"):
                    child = getattr(widget, attr, None)
                    if child is not None:
                        try:
                            child.bind("<MouseWheel>", handler, add="+")
                        except (NotImplementedError, tk.TclError, AttributeError):
                            pass

        def bind_scroll_recursive(w):
            if isinstance(w, ctk.CTkTextbox):
                def speed_up_tb_scroll(event):
                    delta = getattr(event, "delta", 0)
                    if delta != 0:
                        steps = _wheel_steps(delta, 8)
                        w.yview_scroll(steps, "units")
                    return "break"
                safe_mousewheel_bind(w, speed_up_tb_scroll)
                if hasattr(w, "_textbox"):
                    safe_mousewheel_bind(w._textbox, speed_up_tb_scroll)
                return

            safe_mousewheel_bind(w, speed_up_main_scroll)
            for child in w.winfo_children():
                bind_scroll_recursive(child)

        bind_scroll_recursive(scroll)

    def _setup_data_process_ui(self):
        bottom_frame = ctk.CTkFrame(self, height=56, fg_color=("#F9FAFB", "#1A1D24"), corner_radius=0)
        bottom_frame.pack(side="bottom", fill="x", pady=0)

        scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        scroll.pack(fill="both", expand=True, padx=20, pady=(15, 6))

        self.prompt_tabview = ctk.CTkTabview(
            scroll,
            fg_color=("#FFFFFF", "#1F232B"),
            segmented_button_fg_color=("#E5E7EB", "#2F3541"),
            segmented_button_selected_color=("#6366F1", "#6366F1"),
            segmented_button_selected_hover_color=("#4F46E5", "#4F46E5"),
            segmented_button_unselected_color=("#E5E7EB", "#2F3541"),
            segmented_button_unselected_hover_color=("#D1D5DB", "#374151"),
            text_color=("#111827", "#F9FAFB"),
            corner_radius=14,
            command=self._on_prompt_tab_changed,
        )
        self.prompt_tabview.pack(fill="x", padx=0, pady=(0, 10))
        agent_tab = self.prompt_tabview.add("Agent协作")
        agent_tab.configure(fg_color="transparent")
        self._build_agent_tab(agent_tab)
        self._update_prompt_tab_text_colors()

        preview_header = ctk.CTkFrame(scroll, fg_color="transparent")
        preview_header.pack(fill="x", padx=10, pady=(15, 2))
        ctk.CTkLabel(preview_header, text="提示词生成预览", font=ctk.CTkFont(family="Microsoft YaHei", size=13, weight="bold"), text_color="#374151").pack(side="left", anchor="w")
        self.btn_copy_prompt_preview = ctk.CTkButton(
            preview_header,
            text="复制",
            width=54,
            height=24,
            font=ctk.CTkFont(family=self.font_family, size=11),
            corner_radius=12,
            fg_color=self.colors["primary_soft"],
            text_color=self.colors["primary"],
            hover_color=self.colors["border_strong"],
            command=self.copy_prompt_preview
        )
        self.btn_copy_prompt_preview.pack(side="right", padx=(5, 0))

        self.txt_preview = ctk.CTkTextbox(
            scroll, height=190, font=ctk.CTkFont(family="Consolas", size=11),
            border_width=1,
            border_color=("#D1D5DB", "#374151"),
            fg_color=("#FFFFFF", "#262930"),
            text_color=("#111827", "#F9FAFB")
        )
        self.txt_preview.pack(fill="x", padx=10, pady=5)
        bind_text_context_menu(self.txt_preview)

        btn_cancel = ctk.CTkButton(
            bottom_frame, text="取消", fg_color="#F3F4F6", text_color="#1F2937", hover_color="#E5E7EB",
            font=ctk.CTkFont(family="Microsoft YaHei", size=13, weight="bold"), height=36, corner_radius=18,
            command=self.destroy
        )
        btn_cancel.pack(side="left", padx=20, pady=10)

        self.btn_copy = ctk.CTkButton(
            bottom_frame, text="生成提示词", fg_color="#6366F1", text_color="white", hover_color="#4F46E5",
            font=ctk.CTkFont(family="Microsoft YaHei", size=13, weight="bold"), height=36, corner_radius=18,
            command=self.generate_and_copy
        )
        self.btn_copy.pack(side="right", padx=20, pady=10)

        self._speed_up_scroll_recursive(scroll)

    def setup_ui(self):
        if self.script_type == "data_process":
            self._setup_data_process_ui()
            return
        # 底部固定按钮区（设为透明以融合背景，去掉白色块）
        bottom_frame = ctk.CTkFrame(self, height=56, fg_color=("#F9FAFB", "#1A1D24"), corner_radius=0)
        bottom_frame.pack(side="bottom", fill="x", pady=0)

        # 滚动内容区
        scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        scroll.pack(fill="both", expand=True, padx=20, pady=(15, 6))

        self.prompt_tabview = ctk.CTkTabview(
            scroll,
            fg_color=("#FFFFFF", "#1F232B"),
            segmented_button_fg_color=("#E5E7EB", "#2F3541"),
            segmented_button_selected_color=("#6366F1", "#6366F1"),
            segmented_button_selected_hover_color=("#4F46E5", "#4F46E5"),
            segmented_button_unselected_color=("#E5E7EB", "#2F3541"),
            segmented_button_unselected_hover_color=("#D1D5DB", "#374151"),
            text_color=("#111827", "#F9FAFB"),
            corner_radius=14,
            command=self._on_prompt_tab_changed,
        )
        self.prompt_tabview.pack(fill="x", padx=0, pady=(0, 10))
        manual_tab = self.prompt_tabview.add("参数选项")
        target_tab = self.prompt_tabview.add("目标导向")
        agent_tab = self.prompt_tabview.add("Agent协作")
        for tab in (manual_tab, target_tab, agent_tab):
            tab.configure(fg_color="transparent")
        self._update_prompt_tab_text_colors()

        # 1-6. 下拉选单配置区（一行两个，共三行，节省空间）
        # Row 1: 1. 脚本用途 & 2. 数据范围
        row1 = ctk.CTkFrame(manual_tab, fg_color="transparent")
        row1.pack(fill="x", pady=6)
        row1.columnconfigure(0, weight=1, uniform="row1")
        row1.columnconfigure(1, weight=1, uniform="row1")

        # Col 1: 脚本用途
        col1_1 = ctk.CTkFrame(row1, fg_color="transparent")
        col1_1.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        ctk.CTkLabel(col1_1, text="1. 脚本用途", font=ctk.CTkFont(family="Microsoft YaHei", size=13, weight="bold"), text_color="#374151").pack(anchor="w", pady=(0, 2))
        self.var_purpose = ctk.StringVar(value="绘制 F0 曲线图")
        purposes = ["绘制 F0 曲线图", "绘制 F0 分布图", "绘制 F1/F2 元音空间图", "绘制共振峰轨迹图", "绘制异常/质量检查图", "自定义图表"]
        self._make_option_menu(col1_1, purposes, self.var_purpose)

        # Col 2: 数据范围
        col1_2 = ctk.CTkFrame(row1, fg_color="transparent")
        col1_2.grid(row=0, column=1, sticky="nsew", padx=(10, 0))
        ctk.CTkLabel(col1_2, text="2. 数据范围", font=ctk.CTkFont(family="Microsoft YaHei", size=13, weight="bold"), text_color="#374151").pack(anchor="w", pady=(0, 2))
        self.var_scope = ctk.StringVar(value="只使用纳入分析的条目")
        scopes = ["只使用纳入分析的条目", "包含已排除条目", "只使用当前发音人", "使用全部发音人", "只使用当前选中分组", "手动指定分组"]
        self._make_option_menu(col1_2, scopes, self.var_scope, command=self.on_scope_change)

        self.frame_custom_scope = ctk.CTkFrame(col1_2, fg_color="transparent")
        self.entry_custom_scope = ctk.CTkEntry(
            self.frame_custom_scope,
            placeholder_text="输入指定的分组名称，多个以逗号分隔...",
            height=36,
            corner_radius=18,
            font=ctk.CTkFont(family="Microsoft YaHei", size=13),
            fg_color=("#F9FAFB", "#262930"),
            border_color=("#D1D5DB", "#374151"),
            text_color=("#111827", "#F9FAFB")
        )
        self.entry_custom_scope.pack(fill="x", pady=2)

        # Row 2: 3. 分组方式 & 4. 图表形式
        row2 = ctk.CTkFrame(manual_tab, fg_color="transparent")
        row2.pack(fill="x", pady=6)
        row2.columnconfigure(0, weight=1, uniform="row2")
        row2.columnconfigure(1, weight=1, uniform="row2")

        # Col 1: 分组方式
        col2_1 = ctk.CTkFrame(row2, fg_color="transparent")
        col2_1.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        ctk.CTkLabel(col2_1, text="3. 分组方式", font=ctk.CTkFont(family="Microsoft YaHei", size=13, weight="bold"), text_color="#374151").pack(anchor="w", pady=(0, 2))
        self.var_grouping = ctk.StringVar(value="按声调/分组")
        groupings = ["按声调/分组", "按发音人", "按音节位置", "按分析模式", "不分组", "自定义分组字段"]
        self._make_option_menu(col2_1, groupings, self.var_grouping, command=self.on_grouping_change)

        self.frame_custom_grouping = ctk.CTkFrame(col2_1, fg_color="transparent")
        self.entry_custom_grouping = ctk.CTkEntry(
            self.frame_custom_grouping,
            placeholder_text="输入自定义的分组字典键名...",
            height=36,
            corner_radius=18,
            font=ctk.CTkFont(family="Microsoft YaHei", size=13),
            fg_color=("#F9FAFB", "#262930"),
            border_color=("#D1D5DB", "#374151"),
            text_color=("#111827", "#F9FAFB")
        )
        self.entry_custom_grouping.pack(fill="x", pady=2)

        # Col 2: 图表形式
        col2_2 = ctk.CTkFrame(row2, fg_color="transparent")
        col2_2.grid(row=0, column=1, sticky="nsew", padx=(10, 0))
        ctk.CTkLabel(col2_2, text="4. 图表形式", font=ctk.CTkFont(family="Microsoft YaHei", size=13, weight="bold"), text_color="#374151").pack(anchor="w", pady=(0, 2))
        self.var_chart_style = ctk.StringVar(value="折线图")
        styles = ["折线图", "散点图", "箱线图", "小提琴图", "热力图", "轨迹图", "多子图分面", "自定义"]
        self._make_option_menu(col2_2, styles, self.var_chart_style)

        # Row 3: 5. 横轴 & 6. 纵轴
        row3 = ctk.CTkFrame(manual_tab, fg_color="transparent")
        row3.pack(fill="x", pady=6)
        row3.columnconfigure(0, weight=1, uniform="row3")
        row3.columnconfigure(1, weight=1, uniform="row3")

        # Col 1: 横轴
        col3_1 = ctk.CTkFrame(row3, fg_color="transparent")
        col3_1.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        ctk.CTkLabel(col3_1, text="5. 横轴", font=ctk.CTkFont(family="Microsoft YaHei", size=13, weight="bold"), text_color="#374151").pack(anchor="w", pady=(0, 2))
        self.var_xaxis = ctk.StringVar(value="归一化时间 0-1")
        xaxes = ["归一化时间 0-1", "真实时间 秒", "采样点序号", "音节位置", "自定义"]
        self._make_option_menu(col3_1, xaxes, self.var_xaxis)

        # Col 2: 纵轴
        col3_2 = ctk.CTkFrame(row3, fg_color="transparent")
        col3_2.grid(row=0, column=1, sticky="nsew", padx=(10, 0))
        ctk.CTkLabel(col3_2, text="6. 纵轴", font=ctk.CTkFont(family="Microsoft YaHei", size=13, weight="bold"), text_color="#374151").pack(anchor="w", pady=(0, 2))
        self.var_yaxis = ctk.StringVar(value="F0 Hz")
        yaxes = ["F0 Hz", "F0 T 值", "F1 Hz", "F2 Hz", "时长", "自定义"]
        self._make_option_menu(col3_2, yaxes, self.var_yaxis)

        # 7. 统计处理
        self._section_label(manual_tab, "7. 统计处理")
        stats_frame = ctk.CTkFrame(manual_tab, fg_color="transparent")
        stats_frame.pack(fill="x", padx=10, pady=5)

        stats_options = ["绘制个体曲线", "绘制均值", "绘制中位数", "绘制标准差阴影", "绘制置信区间", "忽略 NaN", "过滤明显异常值"]
        self.var_stats = {}
        for idx, opt in enumerate(stats_options):
            self.var_stats[opt] = ctk.BooleanVar(value=True if opt in ["绘制均值", "绘制标准差阴影", "忽略 NaN"] else False)
            cb = ctk.CTkCheckBox(
                stats_frame, text=opt, variable=self.var_stats[opt], height=24,
                checkbox_width=18, checkbox_height=18,
                corner_radius=1000, border_width=2,
                fg_color=("#2563EB", "#1D4ED8"), hover_color=("#9CA3AF", "#4B5563"), border_color=("#4B5563", "#9CA3AF"),
                checkmark_color="#FFFFFF", text_color=("#1F2937", "#E5E7EB"),
                font=ctk.CTkFont(family="Microsoft YaHei", size=12)
            )
            cb.grid(row=idx//2, column=idx%2, sticky="w", padx=5, pady=4)

        # 8. 输出要求
        self._section_label(manual_tab, "8. 输出要求")
        out_frame = ctk.CTkFrame(manual_tab, fg_color="transparent")
        out_frame.pack(fill="x", padx=10, pady=5)

        # 标题
        ctk.CTkLabel(out_frame, text="图表标题:", font=ctk.CTkFont(family="Microsoft YaHei", size=12)).grid(row=0, column=0, sticky="w", pady=4)
        self.entry_title = ctk.CTkEntry(
            out_frame,
            placeholder_text="例如：F0 均值图",
            height=36,
            corner_radius=18,
            font=ctk.CTkFont(family="Microsoft YaHei", size=13),
            fg_color=("#F9FAFB", "#262930"),
            border_color=("#D1D5DB", "#374151"),
            text_color=("#111827", "#F9FAFB")
        )
        self.entry_title.insert(0, "F0 分组均值折线图")
        self.entry_title.grid(row=0, column=1, sticky="ew", padx=(10, 0), pady=4)

        # 文件名
        ctk.CTkLabel(out_frame, text="文件名:", font=ctk.CTkFont(family="Microsoft YaHei", size=12)).grid(row=1, column=0, sticky="w", pady=4)
        self.entry_filename = ctk.CTkEntry(
            out_frame,
            placeholder_text="例如：contour.png",
            height=36,
            corner_radius=18,
            font=ctk.CTkFont(family="Microsoft YaHei", size=13),
            fg_color=("#F9FAFB", "#262930"),
            border_color=("#D1D5DB", "#374151"),
            text_color=("#111827", "#F9FAFB")
        )
        self.entry_filename.insert(0, "contour.png")
        self.entry_filename.grid(row=1, column=1, sticky="ew", padx=(10, 0), pady=4)

        # 复选框样式修改为一致的圆形
        self.var_output_table = ctk.BooleanVar(value=False)
        self.var_show_legend = ctk.BooleanVar(value=True)
        self.var_use_chinese = ctk.BooleanVar(value=True)

        cb_table = ctk.CTkCheckBox(
            out_frame, text="同时输出数据表", variable=self.var_output_table, height=24,
            checkbox_width=18, checkbox_height=18,
            corner_radius=1000, border_width=2,
            fg_color=("#2563EB", "#1D4ED8"), hover_color=("#9CA3AF", "#4B5563"), border_color=("#4B5563", "#9CA3AF"),
            checkmark_color="#FFFFFF", text_color=("#1F2937", "#E5E7EB"),
            font=ctk.CTkFont(family="Microsoft YaHei", size=12)
        )
        cb_table.grid(row=2, column=0, columnspan=2, sticky="w", pady=4)

        cb_legend = ctk.CTkCheckBox(
            out_frame, text="显示图例", variable=self.var_show_legend, height=24,
            checkbox_width=18, checkbox_height=18,
            corner_radius=1000, border_width=2,
            fg_color=("#2563EB", "#1D4ED8"), hover_color=("#9CA3AF", "#4B5563"), border_color=("#4B5563", "#9CA3AF"),
            checkmark_color="#FFFFFF", text_color=("#1F2937", "#E5E7EB"),
            font=ctk.CTkFont(family="Microsoft YaHei", size=12)
        )
        cb_legend.grid(row=3, column=0, sticky="w", pady=4)

        cb_chinese = ctk.CTkCheckBox(
            out_frame, text="图中使用中文标签", variable=self.var_use_chinese, height=24,
            checkbox_width=18, checkbox_height=18,
            corner_radius=1000, border_width=2,
            fg_color=("#2563EB", "#1D4ED8"), hover_color=("#9CA3AF", "#4B5563"), border_color=("#4B5563", "#9CA3AF"),
            checkmark_color="#FFFFFF", text_color=("#1F2937", "#E5E7EB"),
            font=ctk.CTkFont(family="Microsoft YaHei", size=12)
        )
        cb_chinese.grid(row=3, column=1, sticky="w", pady=4)

        out_frame.grid_columnconfigure(1, weight=1)

        # 9. 用户自定义需求
        self._section_label(manual_tab, "9. 补充具体需求")
        self.txt_custom = ctk.CTkTextbox(
            manual_tab, height=80,
            border_width=1,
            border_color=("#D1D5DB", "#374151"),
            fg_color=("#FFFFFF", "#262930"),
            text_color=("#111827", "#F9FAFB")
        )
        self.txt_custom.pack(fill="x", padx=10, pady=5)
        bind_text_context_menu(self.txt_custom)

        self._build_goal_oriented_tab(target_tab)
        self._build_agent_tab(agent_tab)

        # 10. 提示词预览
        preview_header = ctk.CTkFrame(scroll, fg_color="transparent")
        preview_header.pack(fill="x", padx=10, pady=(15, 2))

        preview_title = ctk.CTkLabel(preview_header, text="10. 提示词生成预览", font=ctk.CTkFont(family="Microsoft YaHei", size=13, weight="bold"), text_color="#374151")
        preview_title.pack(side="left", anchor="w")

        # 复制按钮
        self.btn_copy_prompt_preview = ctk.CTkButton(
            preview_header,
            text="复制",
            width=54,
            height=24,
            font=ctk.CTkFont(family=self.font_family, size=11),
            corner_radius=12,
            fg_color=self.colors["primary_soft"],
            text_color=self.colors["primary"],
            hover_color=self.colors["border_strong"],
            command=self.copy_prompt_preview
        )
        self.btn_copy_prompt_preview.pack(side="right", padx=(5, 0))

        self.txt_preview = ctk.CTkTextbox(
            scroll, height=150, font=ctk.CTkFont(family="Consolas", size=11),
            border_width=1,
            border_color=("#D1D5DB", "#374151"),
            fg_color=("#FFFFFF", "#262930"),
            text_color=("#111827", "#F9FAFB")
        )
        self.txt_preview.pack(fill="x", padx=10, pady=5)
        bind_text_context_menu(self.txt_preview)

        self._speed_up_scroll_recursive(scroll)

        # 底部按钮 (padding 调小为 10，贴合底部并美化布局)
        btn_cancel = ctk.CTkButton(
            bottom_frame, text="取消", fg_color="#F3F4F6", text_color="#1F2937", hover_color="#E5E7EB",
            font=ctk.CTkFont(family="Microsoft YaHei", size=13, weight="bold"), height=36, corner_radius=18,
            command=self.destroy
        )
        btn_cancel.pack(side="left", padx=20, pady=10)

        self.btn_copy = ctk.CTkButton(
            bottom_frame, text="生成提示词", fg_color="#6366F1", text_color="white", hover_color="#4F46E5",
            font=ctk.CTkFont(family="Microsoft YaHei", size=13, weight="bold"), height=36, corner_radius=18,
            command=self.generate_and_copy
        )
        self.btn_copy.pack(side="right", padx=20, pady=10)

        # 初始打包隐藏
        self.on_scope_change(self.var_scope.get())
        self.on_grouping_change(self.var_grouping.get())

    def _build_goal_oriented_tab(self, target_tab):
        intro = ctk.CTkLabel(
            target_tab,
            text="这个页面按研究目标生成提示词。你只需要说明想解决什么问题，AI 会自己选择合适的图表形式和统计处理。",
            font=ctk.CTkFont(family="Microsoft YaHei", size=12),
            text_color="#64748B",
            wraplength=520,
            justify="left"
        )
        intro.pack(anchor="w", padx=10, pady=(8, 10))

        row1 = ctk.CTkFrame(target_tab, fg_color="transparent")
        row1.pack(fill="x", pady=6)
        row1.columnconfigure(0, weight=1, uniform="target1")
        row1.columnconfigure(1, weight=1, uniform="target1")

        col_goal = ctk.CTkFrame(row1, fg_color="transparent")
        col_goal.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        ctk.CTkLabel(col_goal, text="1. 想实现的目标", font=ctk.CTkFont(family="Microsoft YaHei", size=13, weight="bold"), text_color="#374151").pack(anchor="w", pady=(0, 2))
        self.var_target_goal = ctk.StringVar(value="比较不同分组的声学差异")
        target_goals = [
            "比较不同分组的声学差异",
            "比较不同发音人的差异",
            "展示单个发音人的整体趋势",
            "展示一个词或一组词的轨迹",
            "寻找异常或质量问题",
            "制作论文用汇总图",
            "探索数据中的模式",
            "自定义目标",
        ]
        self._make_option_menu(col_goal, target_goals, self.var_target_goal)

        col_data = ctk.CTkFrame(row1, fg_color="transparent")
        col_data.grid(row=0, column=1, sticky="nsew", padx=(10, 0))
        ctk.CTkLabel(col_data, text="2. 关注的数据", font=ctk.CTkFont(family="Microsoft YaHei", size=13, weight="bold"), text_color="#374151").pack(anchor="w", pady=(0, 2))
        self.var_target_data = ctk.StringVar(value="使用全部纳入分析条目")
        target_data = [
            "使用全部纳入分析条目",
            "只看某些分组",
            "只看某些发音人",
            "比较两个或多个条件",
            "包含已排除条目做质量检查",
            "自定义范围",
        ]
        self._make_option_menu(col_data, target_data, self.var_target_data)

        row2 = ctk.CTkFrame(target_tab, fg_color="transparent")
        row2.pack(fill="x", pady=6)
        row2.columnconfigure(0, weight=1, uniform="target2")
        row2.columnconfigure(1, weight=1, uniform="target2")

        col_measure = ctk.CTkFrame(row2, fg_color="transparent")
        col_measure.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        ctk.CTkLabel(col_measure, text="3. 主要观察指标", font=ctk.CTkFont(family="Microsoft YaHei", size=13, weight="bold"), text_color="#374151").pack(anchor="w", pady=(0, 2))
        self.var_target_measure = ctk.StringVar(value="由 AI 根据目标选择")
        measures = ["由 AI 根据目标选择", "F0 走势", "F0 分布", "F0 T 值", "F1/F2 空间", "共振峰轨迹", "时长", "多个指标组合"]
        self._make_option_menu(col_measure, measures, self.var_target_measure)

        col_output = ctk.CTkFrame(row2, fg_color="transparent")
        col_output.grid(row=0, column=1, sticky="nsew", padx=(10, 0))
        ctk.CTkLabel(col_output, text="4. 输出风格", font=ctk.CTkFont(family="Microsoft YaHei", size=13, weight="bold"), text_color="#374151").pack(anchor="w", pady=(0, 2))
        self.var_target_output = ctk.StringVar(value="优先生成一张清晰图表")
        target_outputs = ["优先生成一张清晰图表", "图表和统计表都要", "多张分面图", "论文风格高分辨率图", "教学展示风格", "自定义输出"]
        self._make_option_menu(col_output, target_outputs, self.var_target_output)

        self._section_label(target_tab, "5. 具体目标")
        self.placeholder_target_goal = "例如：我想比较阴平、阳平、上声、去声四组在归一化时间上的 F0 走势差异，最好能看出均值和组内离散程度。"
        self.txt_target_goal = self._make_textbox(
            target_tab,
            height=90,
            placeholder=self.placeholder_target_goal
        )

        self._section_label(target_tab, "6. 指定对象或筛选条件")
        self.placeholder_target_scope = "例如：只看张三和李四；只看“实验组A”；排除已经标记忽略的条目。没有特殊限制可以留空。"
        self.txt_target_scope = self._make_textbox(
            target_tab,
            height=70,
            placeholder=self.placeholder_target_scope
        )

        self._section_label(target_tab, "7. 输出偏好")
        self.placeholder_target_constraints = "例如：颜色要区分清楚；适合论文插图；图例放在右上；如果数据不足要在图里说明。"
        self.txt_target_constraints = self._make_textbox(
            target_tab,
            height=70,
            placeholder=self.placeholder_target_constraints
        )

    def _build_agent_tab(self, agent_tab):
        is_data_process = self.script_type == "data_process"
        intro = ctk.CTkLabel(
            agent_tab,
            text=(
                "这个页面会把 AI 设定成数据处理 Agent：先询问工程再加工目的，推荐重分析、重组、音频处理或元数据合并方案，等你确认后再输出 Toolkit 可运行代码。"
                if is_data_process else
                "这个页面会把 AI 设定成协作 Agent：先询问研究目的并推荐图表，等你确认后再输出 Toolkit 可运行代码。"
            ),
            font=ctk.CTkFont(family="Microsoft YaHei", size=12),
            text_color="#64748B",
            wraplength=520,
            justify="left"
        )
        intro.pack(anchor="w", padx=10, pady=(8, 10))

        row1 = ctk.CTkFrame(agent_tab, fg_color="transparent")
        row1.pack(fill="x", pady=6)
        row1.columnconfigure(0, weight=1, uniform="agent1")
        row1.columnconfigure(1, weight=1, uniform="agent1")

        col_detail = ctk.CTkFrame(row1, fg_color="transparent")
        col_detail.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        ctk.CTkLabel(col_detail, text="1. 提示词详略", font=ctk.CTkFont(family="Microsoft YaHei", size=13, weight="bold"), text_color="#374151").pack(anchor="w", pady=(0, 2))
        self.var_agent_detail = ctk.StringVar(value="详细" if is_data_process else "精简")
        self._make_option_menu(col_detail, ["精简", "详细"], self.var_agent_detail)

        col_count = ctk.CTkFrame(row1, fg_color="transparent")
        col_count.grid(row=0, column=1, sticky="nsew", padx=(10, 0))
        self.var_agent_chart_count = ctk.StringVar(value="5")
        if is_data_process:
            ctk.CTkLabel(col_count, text="2. 方案数量", font=ctk.CTkFont(family="Microsoft YaHei", size=13, weight="bold"), text_color="#374151").pack(anchor="w", pady=(0, 2))
            self.var_agent_plan_count = ctk.StringVar(value="4")
            self._make_option_menu(col_count, ["3", "4", "5"], self.var_agent_plan_count)
        else:
            ctk.CTkLabel(col_count, text="2. 推荐图表数量", font=ctk.CTkFont(family="Microsoft YaHei", size=13, weight="bold"), text_color="#374151").pack(anchor="w", pady=(0, 2))
            self._make_option_menu(col_count, ["3", "5", "6"], self.var_agent_chart_count)

        row2 = ctk.CTkFrame(agent_tab, fg_color="transparent")
        row2.pack(fill="x", pady=6)
        row2.columnconfigure(0, weight=1)

        col_summary = ctk.CTkFrame(row2, fg_color="transparent")
        col_summary.grid(row=0, column=0, sticky="nsew")
        ctk.CTkLabel(col_summary, text="3. 工程摘要", font=ctk.CTkFont(family="Microsoft YaHei", size=13, weight="bold"), text_color="#374151").pack(anchor="w", pady=(0, 2))
        self.var_agent_summary = ctk.StringVar(value="包含精简工程摘要")
        self._make_option_menu(col_summary, ["包含精简工程摘要", "包含详细工程摘要", "不附带工程摘要"], self.var_agent_summary)

        self._section_label(agent_tab, "4. 给 Agent 的额外提示")
        self.placeholder_agent_extra = (
            "可选。例如：优先帮我重算失败条目；按词项标签拆成干净工程；裁剪每个条目音频；把 CSV 里的实验条件合并进 item_meta。"
            if is_data_process else
            "可选。例如：优先考虑论文图；先帮我判断哪些图适合当前工程；不要推荐结构类图表，除非数据里真的有结构字段。"
        )
        self.txt_agent_extra = self._make_textbox(
            agent_tab,
            height=110,
            placeholder=self.placeholder_agent_extra
        )

    def on_scope_change(self, val):
        if val == "手动指定分组":
            self.frame_custom_scope.pack(fill="x")
        else:
            self.frame_custom_scope.pack_forget()

    def on_grouping_change(self, val):
        if val == "自定义分组字段":
            self.frame_custom_grouping.pack(fill="x")
        else:
            self.frame_custom_grouping.pack_forget()

    def generate_prompt_dict(self):
        active_tab = self.prompt_tabview.get() if hasattr(self, "prompt_tabview") else "参数选项"
        if active_tab == "Agent协作":
            extra = self._textbox_value_without_placeholder(self.txt_agent_extra, self.placeholder_agent_extra)
            if self.script_type == "data_process":
                return {
                    "script_type": "data_process",
                    "prompt_mode": "Agent协作",
                    "goal": "让 AI 先询问工程再加工目的、推荐数据处理方案、再写 Toolkit 数据处理脚本",
                    "custom_desc": extra,
                    "agent_detail_level": self.var_agent_detail.get(),
                    "agent_plan_count": self.var_agent_plan_count.get() if hasattr(self, "var_agent_plan_count") else "4",
                    "agent_project_summary_mode": self.var_agent_summary.get(),
                    "agent_include_project_summary": self.var_agent_summary.get() != "不附带工程摘要",
                    "agent_detailed_project_summary": self.var_agent_summary.get() == "包含详细工程摘要",
                }
            return {
                "script_type": "chart",
                "prompt_mode": "Agent协作",
                "goal": "让 AI 先询问目的、推荐图表、再写 Toolkit 脚本",
                "data_range": "由用户与 Agent 对话确认",
                "group_by": "由 Agent 根据目标推荐",
                "chart_style": "由 Agent 推荐多种候选",
                "x_axis": "由 Agent 根据图表类型决定",
                "y_axis": "由 Agent 根据图表类型决定",
                "stats": ["由 Agent 推荐并在用户确认后落实"],
                "title": "Agent 协作脚本",
                "filename": "agent_chart.png",
                "output_table": False,
                "show_legend": True,
                "use_chinese": True,
                "custom_desc": extra,
                "agent_detail_level": self.var_agent_detail.get(),
                "agent_chart_count": self.var_agent_chart_count.get(),
                "agent_project_summary_mode": self.var_agent_summary.get(),
                "agent_include_project_summary": self.var_agent_summary.get() != "不附带工程摘要",
                "agent_detailed_project_summary": self.var_agent_summary.get() == "包含详细工程摘要",
            }

        if active_tab == "目标导向":
            goal_detail = self._textbox_value_without_placeholder(self.txt_target_goal, self.placeholder_target_goal)
            scope_detail = self._textbox_value_without_placeholder(self.txt_target_scope, self.placeholder_target_scope)
            constraints = self._textbox_value_without_placeholder(self.txt_target_constraints, self.placeholder_target_constraints)
            target_goal = self.var_target_goal.get()
            target_measure = self.var_target_measure.get()
            target_output = self.var_target_output.get()
            custom_lines = [
                f"目标导向模式：{target_goal}",
                f"关注数据：{self.var_target_data.get()}",
                f"主要观察指标：{target_measure}",
                f"输出风格：{target_output}",
            ]
            if goal_detail:
                custom_lines.append(f"用户具体目标：{goal_detail}")
            if scope_detail:
                custom_lines.append(f"筛选条件或指定对象：{scope_detail}")
            if constraints:
                custom_lines.append(f"输出偏好：{constraints}")
            return {
                "script_type": "chart",
                "prompt_mode": "目标导向",
                "goal": target_goal,
                "data_range": self.var_target_data.get(),
                "group_by": "由 AI 根据目标自动选择",
                "chart_style": "由 AI 根据目标自动选择",
                "x_axis": "由 AI 根据目标自动选择",
                "y_axis": target_measure,
                "stats": ["由 AI 根据目标选择合适统计处理"],
                "title": target_goal,
                "filename": "goal_oriented_chart.png",
                "output_table": target_output == "图表和统计表都要",
                "show_legend": True,
                "use_chinese": True,
                "custom_desc": "\n".join(custom_lines),
                "target_goal_detail": goal_detail,
                "target_scope_detail": scope_detail,
                "target_constraints": constraints,
            }

        stats_selected = [k for k, v in self.var_stats.items() if v.get()]

        scope = self.var_scope.get()
        if scope == "手动指定分组":
            custom_grp = self.entry_custom_scope.get().strip()
            scope = f"手动指定分组 ({custom_grp or '未指定'})"

        grouping = self.var_grouping.get()
        if grouping == "自定义分组字段":
            custom_key = self.entry_custom_grouping.get().strip()
            grouping = f"自定义分组字段 ({custom_key or '未指定'})"

        selections = {
            "script_type": "chart",
            "prompt_mode": "参数选项",
            "goal": self.var_purpose.get(),
            "data_range": scope,
            "group_by": grouping,
            "chart_style": self.var_chart_style.get(),
            "x_axis": self.var_xaxis.get(),
            "y_axis": self.var_yaxis.get(),
            "stats": stats_selected,
            "title": self.entry_title.get().strip() or "自定义图表",
            "filename": self.entry_filename.get().strip() or "custom_chart.png",
            "output_table": self.var_output_table.get(),
            "show_legend": self.var_show_legend.get(),
            "use_chinese": self.var_use_chinese.get(),
            "custom_desc": self.txt_custom.get("1.0", tk.END).strip()
        }
        return selections

    def copy_prompt_preview(self):
        try:
            prompt_text = self.txt_preview.get("1.0", tk.END).strip()
            self.clipboard_clear()
            self.clipboard_append(prompt_text)
            self.update()

            if hasattr(self, "btn_copy_prompt_preview"):
                self.btn_copy_prompt_preview.configure(
                    text="✔已复制",
                    fg_color=self.colors.get("success_soft", "#D1FAE5"),
                    text_color=self.colors.get("success", "#10B981")
                )
                def reset():
                    try:
                        self.btn_copy_prompt_preview.configure(
                            text="复制",
                            fg_color=self.colors.get("primary_soft", "#DBEAFE"),
                            text_color=self.colors.get("primary", "#2563EB")
                        )
                    except Exception:
                        pass
                self.after(1000, reset)
        except Exception as e:
            messagebox.showerror("错误", f"复制失败：{e}")

    def generate_preview(self):
        from modules.script_prompt import generate_ai_prompt
        selections = self.generate_prompt_dict()
        prompt_text = generate_ai_prompt(self.project_data, selections)

        self.txt_preview.delete("1.0", tk.END)
        self.txt_preview.insert("1.0", prompt_text)

    def generate_and_copy(self):
        from modules.script_prompt import generate_ai_prompt
        selections = self.generate_prompt_dict()
        prompt_text = generate_ai_prompt(self.project_data, selections)

        self.txt_preview.delete("1.0", tk.END)
        self.txt_preview.insert("1.0", prompt_text)

        try:
            self.clipboard_clear()
            self.clipboard_append(prompt_text)
            self.update()

            if hasattr(self, "btn_copy"):
                self.btn_copy.configure(
                    text="✔已生成并复制",
                    fg_color=self.colors.get("success", "#10B981"),
                    hover_color=self.colors.get("success_hover", "#059669")
                )
                def reset():
                    try:
                        self.btn_copy.configure(
                            text="生成提示词",
                            fg_color="#6366F1",
                            hover_color="#4F46E5"
                        )
                    except Exception:
                        pass
                self.after(1000, reset)
        except Exception:
            pass


if __name__ == "__main__":
    app = ToolkitApp()
    app.mainloop()
