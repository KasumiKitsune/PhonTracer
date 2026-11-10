"""
modules.toolkit_ui.app_shell_mixin: Toolkit 外壳、顶栏布局、图标管理、队列与文件拖拽路由 Mixin。
"""
import os
import sys
import threading
import queue
import re
from urllib.parse import unquote, urlparse
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import customtkinter as ctk
import numpy as np
from PIL import Image, ImageTk

from modules.core.paths import get_asset_path
from modules.ui_widgets import (
    CTkReleaseButton,
    _apply_custom_arrow,
    install_dynamic_checkbox_patch,
)
from modules.wordlist_v2 import load_wordlist_document
from modules.toolkit_ui.tk_utils import SafeCTkFont


class ToolkitShellMixin:
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
        ico_path = get_asset_path("toolkit.ico")
        png_path = get_asset_path("toolkit.png")

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
                self.gui_queue.put(lambda: super(ToolkitShellMixin, self).after(ms, func, *args))
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
        icon_path = get_asset_path("icons")

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
        logo_path = get_asset_path("toolkit.png")
        if os.path.exists(logo_path):
            img = Image.open(logo_path)
            self.icons["logo"] = ctk.CTkImage(light_image=img, dark_image=img, size=(32, 32))


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
            import locale
            candidates = []
            for enc in ("utf-8", locale.getpreferredencoding(False) or "utf-8", "gbk", "gb18030"):
                try:
                    s = raw_path.decode(enc).strip().strip('"\'')
                    if s and s not in candidates:
                        candidates.append(s)
                except (UnicodeDecodeError, LookupError):
                    continue
            for s in candidates:
                if os.path.exists(s):
                    return s
            if candidates:
                return candidates[0]
            return raw_path.decode("utf-8", errors="ignore").strip().strip('"\'')
        return str(raw_path).strip().strip('"\'')


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

