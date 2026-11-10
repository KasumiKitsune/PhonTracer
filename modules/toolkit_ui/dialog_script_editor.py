"""
modules.toolkit_ui.dialog_script_editor: 自定义脚本新建/编辑对话框。
"""
import tkinter as tk
from tkinter import messagebox
import customtkinter as ctk

from modules.ui_widgets import _apply_custom_arrow, bind_text_context_menu
from modules.scripting.meta_parser import (
    SCRIPT_EDITOR_DEFAULT_DESC,
    SCRIPT_EDITOR_DEFAULT_NAME,
    parse_script_metadata_comments,
    strip_markdown_code_fence,
)

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

