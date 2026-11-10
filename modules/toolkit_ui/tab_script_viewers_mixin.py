"""
modules.toolkit_ui.tab_script_viewers_mixin: Tab 6 自定义脚本工作台下半部（画布视口、缩放/对比、产物胶囊、表格/图表查看器等） Mixin。
"""
import os
import sys
import io
import csv
import math
import uuid
import hashlib
import datetime
import shutil
import tempfile
import subprocess
import traceback
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import customtkinter as ctk
import numpy as np
from PIL import Image, ImageTk

from modules.ui_widgets import bind_text_context_menu
from modules.toolkit_ui.tk_utils import start_safe_thread


class ScriptResultViewersMixin:
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

