"""
Wordlist IO mixin (import/export v1, CSV, structured text, PTWL) for VisualWordlistEditor.
"""
import os
import tkinter as tk
from tkinter import filedialog, messagebox
import customtkinter as ctk
from modules.core.text_io import read_text_file_with_fallback
from modules.wordlist_v2 import (
    build_document_from_csv_text,
    build_document_from_structured_text,
    build_document_from_v1_text,
    document_to_csv_text,
    document_to_v1_text,
    load_wordlist_document,
    save_wordlist_document,
    summarize_wordlist_document,
    looks_like_csv_wordlist_text,
    looks_like_structured_wordlist_text,
)


class EditorIOMixin:

    def import_v1_text(self, text: str):
        self.set_document(build_document_from_v1_text(text or ""))

    def import_csv_text(self, text: str):
        self.set_document(build_document_from_csv_text(text or ""))

    def import_structured_text(self, text: str):
        self.set_document(build_document_from_structured_text(text or ""))

    @staticmethod
    def _read_text_file_with_fallback(path, encodings=("utf-8-sig", "utf-8", "gbk")):
        from modules.core.text_io import read_text_file_with_fallback
        return read_text_file_with_fallback(path, encodings=encodings, strict=True)

    def import_v1_dialog(self):
        path = filedialog.askopenfilename(filetypes=[("Text Files", "*.txt"), ("All Files", "*.*")])
        if not path:
            return None
        try:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    text = f.read()
            except UnicodeDecodeError:
                with open(path, "r", encoding="gbk") as f:
                    text = f.read()
            self.import_v1_text(text)
            return path
        except Exception as e:
            messagebox.showerror("错误", f"导入普通字表失败：{e}")
            return None

    def import_csv_dialog(self):
        parent = self.winfo_toplevel()
        popup = ctk.CTkToplevel(parent)
        popup.title("导入字表文本")
        popup.geometry("620x464")
        popup.configure(fg_color=self.colors.get("surface", "#FFFFFF"))
        popup.transient(parent)
        popup.grab_set()

        # 顶部 4px 品牌蓝装饰条
        ctk.CTkFrame(popup, height=4, fg_color=self.colors.get("primary", "#2563EB"), corner_radius=0).pack(fill=tk.X, side=tk.TOP)

        popup.update_idletasks()
        pw = parent.winfo_width()
        ph = parent.winfo_height()
        px = parent.winfo_rootx()
        py = parent.winfo_rooty()
        w, h = 620, 464
        x = px + max(0, (pw - w) // 2)
        y = py + max(0, (ph - h) // 2)
        popup.geometry(f"{w}x{h}+{x}+{y}")

        ctk.CTkLabel(
            popup,
            text="粘贴文本并转换为高级字表",
            font=ctk.CTkFont(family=self.font_family, size=16, weight="bold"),
            text_color=self.colors["text"],
        ).pack(anchor="w", padx=20, pady=(18, 4))
        ctk.CTkLabel(
            popup,
            text="支持结构化字表文本、CSV 和普通字表；系统会自动识别格式。",
            font=self.font_small,
            text_color=self.colors["muted"],
        ).pack(anchor="w", padx=20, pady=(0, 10))

        text_box = ctk.CTkTextbox(
            popup,
            font=ctk.CTkFont(family="Consolas", size=12),
            corner_radius=12,
            border_width=1,
            border_color=self.colors["border"],
            fg_color=self.colors.get("surface_soft", "#F8FAFC"),
            text_color=self.colors.get("text", "#17202A"),
            wrap="none",
        )
        text_box.pack(fill=tk.BOTH, expand=True, padx=20, pady=(0, 12))

        button_row = ctk.CTkFrame(popup, fg_color="transparent")
        button_row.pack(fill=tk.X, padx=20, pady=(0, 18))

        def import_text_auto(text):
            if looks_like_structured_wordlist_text(text):
                self.import_structured_text(text)
            elif looks_like_csv_wordlist_text(text):
                self.import_csv_text(text)
            else:
                self.import_v1_text(text)

        def import_from_file():
            path = filedialog.askopenfilename(
                parent=popup,
                filetypes=[("字表文件", "*.txt *.csv"), ("普通字表", "*.txt"), ("CSV 文件", "*.csv")],
            )
            if not path:
                return
            try:
                text = self._read_text_file_with_fallback(path)
                import_text_auto(text)
                popup.destroy()
            except Exception as e:
                messagebox.showerror("错误", f"导入字表文件失败：{e}", parent=popup)

        def do_import():
            text = text_box.get("1.0", tk.END).strip()
            if not text:
                messagebox.showwarning("提示", "请先粘贴字表文本。", parent=popup)
                return
            try:
                import_text_auto(text)
                popup.destroy()
            except Exception as e:
                messagebox.showerror("错误", f"导入字表文本失败：{e}", parent=popup)

        self._button(button_row, "选择文件", import_from_file, "primary", width=104).pack(side=tk.LEFT)
        self._button(button_row, "取消", popup.destroy, "secondary", width=92).pack(side=tk.LEFT, padx=(8, 0))
        self._button(button_row, "导入", do_import, "success", width=112).pack(side=tk.RIGHT)
        return None

    def load_ptwl_dialog(self):
        path = filedialog.askopenfilename(filetypes=[("PhonTracer 高级字表", "*.ptwl"), ("All Files", "*.*")])
        if not path:
            return None
        try:
            self.set_document(load_wordlist_document(path), path=path)
            return path
        except Exception as e:
            messagebox.showerror("错误", f"打开高级字表失败：{e}")
            return None

    def save_ptwl_dialog(self, save_as=False):
        doc = self.get_document()
        path = None if save_as else self.current_path
        if not path:
            path = filedialog.asksaveasfilename(
                defaultextension=".ptwl",
                filetypes=[("PhonTracer 高级字表", "*.ptwl"), ("All Files", "*.*")],
                initialfile=f"{doc.get('title', '高级字表')}.ptwl",
            )
        if not path:
            return None
        try:
            save_wordlist_document(doc, path)
            self.current_path = path
            messagebox.showinfo("完成", f"高级字表已保存：\n{os.path.basename(path)}")
            return path
        except Exception as e:
            messagebox.showerror("错误", f"保存高级字表失败：{e}")
            return None

    def export_v1_dialog(self):
        path = filedialog.asksaveasfilename(defaultextension=".txt", filetypes=[("Text Files", "*.txt"), ("All Files", "*.*")])
        if not path:
            return None
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(document_to_v1_text(self.get_document()))
            messagebox.showinfo("完成", f"普通字表已导出：\n{os.path.basename(path)}")
            return path
        except Exception as e:
            messagebox.showerror("错误", f"导出普通字表失败：{e}")
            return None

    def export_csv_dialog(self):
        path = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV Files", "*.csv"), ("All Files", "*.*")])
        if not path:
            return None
        try:
            with open(path, "w", encoding="utf-8-sig", newline="") as f:
                f.write(document_to_csv_text(self.get_document()))
            messagebox.showinfo("完成", f"CSV 字表已导出：\n{os.path.basename(path)}")
            return path
        except Exception as e:
            messagebox.showerror("错误", f"导出 CSV 失败：{e}")
            return None

