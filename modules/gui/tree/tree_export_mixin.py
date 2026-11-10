"""
Data export mixin (Excel, TextGrid, KDE, Charts) for ProjectTreePanel.
"""
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import customtkinter as ctk
import csv
import os
try:
    import parselmouth
except ImportError:
    from modules import _parselmouth_fallback as parselmouth
    import sys as _sys
    _sys.modules.setdefault("parselmouth", parselmouth)
    _sys.modules.setdefault("parselmouth.praat", parselmouth.praat)
import numpy as np
import math
import matplotlib
matplotlib.use("Agg", force=True)
import matplotlib.pyplot as plt
import logging
import threading
from PIL import Image, ImageTk
from modules.data_utils import (
    get_export_text_for_item,
    build_five_point_chart,
    write_analysis_sheet_with_formulas,
    write_formant_analysis_sheet_with_formulas,
    split_into_syllables,
    make_textgrid_export_stem,
    ExcelStyleManager,
    autofit_worksheet_columns,
    clean_str,
    get_item_syllable_bounds,
    sample_formant_points_by_bounds,
    sample_formant_points_by_bounds_extended,
    get_formant_export_text_for_item,
    compute_speaker_f0_bounds,
    hz_to_t_value,
    write_raw_pitch_sheet,
)
from modules.export_service import UnifiedExportService
from modules.anomaly_detection import detect_pitch_anomaly_points
from modules.ui_widgets import CTkReleaseButton, AutoScrollbar, make_context_menu, post_context_menu
from .tree_dialogs import (
    AnomalyWarningDialog,
    ExclusionReasonDialog,
    apply_custom_dropdown_arrow,
)

logger = logging.getLogger(__name__)


class TreeExportMixin:

    def export_project(self):
        if self.app and hasattr(self.app, 'flush_eraser_changes'):
            self.app.flush_eraser_changes()
        sm = getattr(self.app, 'speaker_manager', None)
        if not self.items and (not sm or len(sm.get_all_speakers()) <= 1):
            return messagebox.showwarning("提示", "没有可导出的数据。")
        if sm and len(sm.get_all_speakers()) > 1:
            self._show_multi_speaker_export_dialog(sm)
        else:
            self._do_export_preparation(None)

    def _show_multi_speaker_export_dialog(self, sm):
        dlg = ctk.CTkToplevel(self.parent)
        dlg.title("PhonTracer语音学标注与声学分析系统 V1.0 - 导出范围选择")
        dlg.configure(fg_color=("#FFFFFF", "#1A1D24"))
        dlg.geometry("420x430")
        dlg.resizable(False, False)
        dlg.transient(self.parent)
        dlg.grab_set()
        dlg.update_idletasks()
        main_win = self.parent.winfo_toplevel()
        x = main_win.winfo_rootx() + (main_win.winfo_width() - 420) // 2
        y = main_win.winfo_rooty() + (main_win.winfo_height() - 430) // 2
        dlg.geometry(f"+{x}+{y}")

        font_small = ctk.CTkFont(family="Microsoft YaHei", size=11)

        # 1. Instantiate the main container frames first
        header_frame = ctk.CTkFrame(dlg, fg_color="transparent")
        btn_frame = ctk.CTkFrame(dlg, fg_color="transparent")
        opt_card = ctk.CTkFrame(dlg, corner_radius=8, border_width=1, border_color=("#E5E7EB", "#374151"), fg_color=("#F9FAFB", "#161A22"))
        util_frame = ctk.CTkFrame(dlg, fg_color="transparent")
        scroll_frame = ctk.CTkScrollableFrame(dlg, height=100, corner_radius=8, border_width=1, border_color=("#E5E7EB", "#374151"), fg_color=("#F9FAFB", "#161A22"))
        scroll_frame._scrollbar.grid_configure(padx=(0, 9))

        # 2. Pack the frames in vertical layout order (Top & Bottom first, then Middle expand)
        header_frame.pack(side=tk.TOP, fill=tk.X, padx=25, pady=(12, 4))
        btn_frame.pack(side=tk.BOTTOM, fill=tk.X, padx=25, pady=(12, 25))
        opt_card.pack(side=tk.BOTTOM, fill=tk.X, padx=25, pady=4)
        util_frame.pack(side=tk.BOTTOM, fill=tk.X, padx=25, pady=(2, 4))
        scroll_frame.pack(fill=tk.BOTH, expand=True, padx=25, pady=4)

        # 3. Populate Header banner
        ctk.CTkLabel(header_frame, text="👥 选择数据源", font=self.font_title, text_color=("#111827", "#F9FAFB")).pack(anchor="w")
        ctk.CTkLabel(header_frame, text="请勾选本次要导出的发音人数据：", font=font_small, text_color=("#6B7280", "#9CA3AF")).pack(anchor="w", pady=(1, 0))

        # 4. Populate Scrollable container with speakers
        all_speakers = sm.get_all_speakers()
        active_speaker = sm.get_active_speaker()

        checkboxes = {}
        for spk in all_speakers:
            is_active = (spk.id == active_speaker.id)
            val = ctk.BooleanVar(value=is_active)
            row = ctk.CTkFrame(scroll_frame, fg_color="transparent")
            row.pack(fill=tk.X, padx=5, pady=2)
            cb = ctk.CTkCheckBox(row, text=f"{spk.name} ({len(spk.items)}项)", variable=val, font=self.font_main,
                                 checkbox_width=18, checkbox_height=18, border_width=2,
                                 corner_radius=1000, checkmark_color="#FFFFFF",
                                 fg_color=("#2563EB", "#1D4ED8"), hover_color=("#9CA3AF", "#4B5563"), border_color=("#4B5563", "#9CA3AF"))
            cb.pack(side=tk.LEFT, padx=10, pady=1)
            checkboxes[spk] = val

        # 5. Populate Util Frame (Select All / None buttons)
        def select_all():
            for val in checkboxes.values():
                val.set(True)
        def select_none():
            for val in checkboxes.values():
                val.set(False)

        ctk.CTkButton(util_frame, text="全选", width=75, height=26, corner_radius=13, font=font_small,
                      fg_color=("#E5E7EB", "#374151"), text_color=("#2563EB", "#60A5FA"), hover_color=("#D1D5DB", "#4B5563"),
                      border_width=1, border_color=("#D1D5DB", "#475569"), command=select_all).pack(side=tk.LEFT, padx=(0, 5))
        ctk.CTkButton(util_frame, text="全不选", width=75, height=26, corner_radius=13, font=font_small,
                      fg_color=("#E5E7EB", "#374151"), text_color=("#4B5563", "#D1D5DB"), hover_color=("#D1D5DB", "#4B5563"),
                      border_width=1, border_color=("#D1D5DB", "#475569"), command=select_none).pack(side=tk.LEFT, padx=5)

        # 6. Populate Integration option card
        integrate_var = ctk.BooleanVar(value=False)
        cb_integrate = ctk.CTkCheckBox(opt_card, text="整合选中发音人的结果", variable=integrate_var, font=self.font_main,
                                       checkbox_width=18, checkbox_height=18, border_width=2,
                                       corner_radius=1000, checkmark_color="#FFFFFF",
                                       fg_color=("#2563EB", "#1D4ED8"), hover_color=("#9CA3AF", "#4B5563"), border_color=("#4B5563", "#9CA3AF"))
        cb_integrate.pack(anchor="w", padx=15, pady=(8, 2))

        ctk.CTkLabel(opt_card, text="※ 将采用 T值归一化整合不同发音人数据进行对比分析", font=ctk.CTkFont(family="Microsoft YaHei", size=10),
                     text_color=("#6B7280", "#9CA3AF")).pack(anchor="w", padx=15, pady=(0, 8))

        # 7. Populate Action Buttons
        def on_confirm():
            selected_speakers = [spk for spk, var in checkboxes.items() if var.get()]
            if not selected_speakers:
                return messagebox.showwarning("提示", "请至少勾选一个发音人。", parent=dlg)

            do_integrate = integrate_var.get()
            dlg.destroy()
            self._do_custom_export_preparation(selected_speakers, do_integrate)

        ctk.CTkButton(btn_frame, text="取消", width=95, height=36, corner_radius=18,
                      fg_color=("#F3F4F6", "#374151"), text_color=("#4B5563", "#D1D5DB"), hover_color=("#E5E7EB", "#4B5563"),
                      border_width=1, border_color=("#D1D5DB", "#475569"), font=self.font_main, command=dlg.destroy).pack(side=tk.LEFT)
        ctk.CTkButton(btn_frame, text="下一步 ➔", width=95, height=36, corner_radius=18,
                      fg_color=("#3B82F6", "#2563EB"), text_color="#FFFFFF", hover_color=("#2563EB", "#1D4ED8"), font=self.font_main, command=on_confirm).pack(side=tk.RIGHT)

    def _do_custom_export_preparation(self, selected_speakers, do_integrate):
        anomalous_labels = []
        for s in selected_speakers:
            for grp_name, children in self._get_items_by_group_for_dict(s.items):
                for child in children:
                    item = s.items[child]
                    warnings = item.get('warnings')
                    if warnings is None:
                        warnings = self.analyze_item_anomalies(item)

                    has_anomalies = any(w.startswith("[致命]") or w.startswith("[警告]") for w in warnings)
                    if has_anomalies:
                        descriptions = []
                        for w in warnings:
                            if w.startswith("[致命]") or w.startswith("[警告]"):
                                parts = w.split("] ", 1)
                                desc = parts[1] if len(parts) > 1 else w
                                descriptions.append(desc)

                        anomaly_desc = ", ".join(descriptions)
                        anomalous_labels.append(f"[{s.name}] {item['label']} ({anomaly_desc})")

        if anomalous_labels:
            dialog = AnomalyWarningDialog(self.parent, anomalous_labels, font_title=self.font_title, font_main=self.font_main)
            self.parent.wait_window(dialog)
            if not dialog.result:
                return

        if len(selected_speakers) == 1 and not do_integrate:
            s = selected_speakers[0]
            orig_items = self.items
            self.items = s.items
            tree_structure = self._get_all_items_by_group()
            self.items = orig_items
            self._show_export_menu(tree_structure=tree_structure, mode='single', all_speakers=selected_speakers)
        else:
            if do_integrate:
                self._show_export_menu(mode='integrated', all_speakers=selected_speakers)
            else:
                self._show_export_menu(mode='separate', all_speakers=selected_speakers)

    def _do_export_preparation(self, multi_speaker_mode):
        self._do_custom_export_preparation([self.app.speaker_manager.get_active_speaker()], False)

    def _get_items_by_group_for_dict(self, items_dict):
        from modules.export_service import UnifiedExportService
        return UnifiedExportService.get_items_by_group_for_dict(items_dict)

    def _check_empty_and_show_menu(self, tree_structure, mode='single', all_speakers=None):
        empty_labels = []
        items_dict = all_speakers[0].items if (all_speakers and len(all_speakers) == 1) else self.items
        for grp_name, children in tree_structure:
            for child in children:
                item = items_dict.get(child)
                if item and self._check_item_has_empty_data(item):
                    empty_labels.append(f"[{grp_name}] {item['label']}")
        if empty_labels:
            desc = "以下条目基频（F0）包含较多无声或擦除帧（F0 = 0，常见于清声母、清化段或静音）："
            dialog = AnomalyWarningDialog(self.parent, empty_labels, title="部分数据包含无基频帧提示", description=desc, font_title=self.font_title, font_main=self.font_main)
            self.parent.wait_window(dialog)
            if not dialog.result:
                return
        self._show_export_menu(tree_structure=tree_structure, mode=mode, all_speakers=all_speakers)

    def _show_export_menu(self, tree_structure=None, mode='single', all_speakers=None):
        dlg = ctk.CTkToplevel(self.parent)
        dlg.title("PhonTracer语音学标注与声学分析系统 V1.0 - 选择导出格式")
        dlg.configure(fg_color=("#FFFFFF", "#1A1D24"))
        dlg.geometry("340x438")
        dlg.resizable(False, False)
        dlg.transient(self.parent)
        dlg.grab_set()
        dlg.update_idletasks()
        main_win = self.parent.winfo_toplevel()
        x = main_win.winfo_rootx() + (main_win.winfo_width() - 340) // 2
        y = main_win.winfo_rooty() + (main_win.winfo_height() - 438) // 2
        dlg.geometry(f"+{x}+{y}")
        ctk.CTkLabel(dlg, text="请选择导出格式", font=self.font_title, text_color=("#111827", "#F9FAFB")).pack(pady=(20, 15))
        btn_kwargs = {"corner_radius": 8, "height": 44, "font": self.font_main, "anchor": "w", "compound": "left", "border_width": 1.5}

        def do_export(format_mode):
            if format_mode == 'line_chart':
                existing_dialog = getattr(self.app, 'active_chart_dialog', None)
                if existing_dialog is not None:
                    try:
                        if existing_dialog.winfo_exists():
                            dlg.destroy()
                            existing_dialog.deiconify()
                            existing_dialog.lift()
                            existing_dialog.focus_force()
                            return
                    except Exception:
                        self.app.active_chart_dialog = None
                dlg.destroy()
                from modules.acoustic_exporter import AcousticChartExportDialog
                AcousticChartExportDialog(self.parent, app=self.app, project_tree=self, mode=mode, all_speakers=all_speakers)
                return
            if format_mode == 'kde':
                dlg.destroy()
                self._show_kde_params_dialog(mode=mode, tree_structure=tree_structure, all_speakers=all_speakers)
                return
            dlg.destroy()
            def execute_export(out_path, inc_chart=False):
                try:
                    analysis_mode = getattr(self, 'app_state_params', {}).get('analysis_mode', 'f0')
                    if analysis_mode == 'formant':
                        if format_mode == 'xlsx':
                            if mode == 'single':
                                self._export_formant_table(out_path, all_speakers, include_chart=inc_chart, show_popup=False)
                            elif mode == 'separate':
                                import os
                                for s in all_speakers:
                                    if os.path.isdir(out_path): s_out = os.path.join(out_path, f"{s.name}.xlsx")
                                    else:
                                        base, ext = os.path.splitext(out_path)
                                        s_out = f"{base}_{s.name}{ext}"
                                    self._export_formant_table(s_out, [s], include_chart=inc_chart, show_popup=False)
                            elif mode == 'integrated':
                                self._export_formant_table(out_path, all_speakers, include_chart=inc_chart, show_popup=False)
                            return True
                        elif format_mode == 'txt':
                            if mode == 'single':
                                self._export_txt(out_path, tree_structure=tree_structure)
                            elif mode == 'separate':
                                import os
                                for s in all_speakers:
                                    if os.path.isdir(out_path): s_out = os.path.join(out_path, f"{s.name}.txt")
                                    else:
                                        base, ext = os.path.splitext(out_path)
                                        s_out = f"{base}_{s.name}{ext}"
                                    s_struct = self._get_items_by_group_for_dict(s.items)
                                    orig_items = self.items
                                    self.items = s.items
                                    try:
                                        self._export_txt(s_out, tree_structure=s_struct)
                                    finally:
                                        self.items = orig_items
                            elif mode == 'integrated':
                                self._export_formant_table_txt(out_path, all_speakers)
                            return True

                    if mode == 'single':
                        orig_items = self.items
                        single_spk = all_speakers[0] if (all_speakers and len(all_speakers) == 1) else None
                        if single_spk is not None:
                            self.items = single_spk.items
                        try:
                            if format_mode == 'txt': self._export_txt(out_path, tree_structure=tree_structure)
                            elif format_mode == 'xlsx': self._export_xlsx(out_path, include_chart=inc_chart, tree_structure=tree_structure)
                            elif format_mode == 'textgrid':
                                is_batch = False
                                if self.app and hasattr(self.app, 'tabview'): is_batch = (self.app.tabview.get() == "多条独立音频")
                                elif hasattr(self.parent, 'tabview'): is_batch = (self.parent.tabview.get() == "多条独立音频")
                                if is_batch: self._export_textgrid_batch(out_path, tree_structure=tree_structure)
                                else: self._export_textgrid_long(out_path, tree_structure=tree_structure)
                            elif format_mode == 'collection':
                                self._export_praat_collection(out_path, tree_structure=tree_structure, speaker=single_spk)
                            elif format_mode == 'line_chart': self._export_line_chart(out_path, tree_structure=tree_structure)
                            elif format_mode == 'kde': self._export_kde_heatmap(out_path, tree_structure=tree_structure)
                        finally:
                            self.items = orig_items
                    elif mode == 'separate':
                        import os
                        for s in all_speakers:
                            s_struct = self._get_items_by_group_for_dict(s.items)
                            orig_items = self.items
                            self.items = s.items
                            try:
                                if format_mode == 'textgrid':
                                    s_out = os.path.join(out_path, s.name)
                                    os.makedirs(s_out, exist_ok=True)
                                    is_batch = False
                                    if getattr(s, 'tab_mode', None) == "多条独立音频": is_batch = True
                                    if is_batch: self._export_textgrid_batch(s_out, tree_structure=s_struct)
                                    else: self._export_textgrid_long(os.path.join(s_out, f"{s.name}.TextGrid"), tree_structure=s_struct)
                                elif format_mode == 'collection':
                                    if os.path.isdir(out_path):
                                        s_out = os.path.join(out_path, f"{s.name}.Collection")
                                    else:
                                        base, ext = os.path.splitext(out_path)
                                        s_out = f"{base}_{s.name}{ext or '.Collection'}"
                                    self._export_praat_collection(s_out, tree_structure=s_struct, speaker=s)
                                else:
                                    if os.path.isdir(out_path): s_out = os.path.join(out_path, f"{s.name}.{'txt' if format_mode=='txt' else 'xlsx' if format_mode=='xlsx' else 'png'}")
                                    else:
                                        base, ext = os.path.splitext(out_path)
                                        s_out = f"{base}_{s.name}{ext}"
                                    if format_mode == 'txt': self._export_txt(s_out, tree_structure=s_struct)
                                    elif format_mode == 'xlsx': self._export_xlsx(s_out, include_chart=inc_chart, tree_structure=s_struct)
                                    elif format_mode == 'line_chart': self._export_line_chart(s_out, tree_structure=s_struct)
                                    elif format_mode == 'kde': self._export_kde_heatmap(s_out, tree_structure=s_struct)
                            finally:
                                self.items = orig_items
                    elif mode == 'integrated':
                        if format_mode in ('txt', 'xlsx'): self._export_integrated(out_path, format_mode, inc_chart, all_speakers)
                        elif format_mode == 'collection': self._export_praat_collection_integrated(out_path, all_speakers)
                        elif format_mode == 'line_chart': self._export_line_chart_integrated(out_path, all_speakers)
                        elif format_mode == 'kde': self._export_kde_heatmap_integrated(out_path, all_speakers)
                        else:
                            messagebox.showwarning("提示", "未知的整合导出格式。")
                            return False
                    return True
                except Exception as e:
                    messagebox.showerror("错误", str(e))
                    import logging
                    logging.getLogger(__name__).error(f"Export error: {e}", exc_info=True)
                    return False
            if format_mode == 'txt':
                out = filedialog.askdirectory(title="选择导出文件夹") if mode == 'separate' else filedialog.asksaveasfilename(title="导出文本", defaultextension=".txt", initialfile="tone_export_data", filetypes=[("文本文件", "*.txt")])
                if out and execute_export(out): messagebox.showinfo("成功", f"数据已导出至:\n{out}")
            elif format_mode == 'textgrid':
                if mode == 'integrated': return messagebox.showwarning("提示", "不支持整合导出 TextGrid。")
                out = filedialog.askdirectory(title="选择TextGrid导出文件夹") if mode == 'separate' else None
                if mode != 'separate':
                    is_batch = False
                    if self.app and hasattr(self.app, 'tabview'): is_batch = (self.app.tabview.get() == "多条独立音频")
                    elif hasattr(self.parent, 'tabview'): is_batch = (self.parent.tabview.get() == "多条独立音频")
                    out = filedialog.askdirectory(title="选择TextGrid导出文件夹") if is_batch else filedialog.asksaveasfilename(title="导出 TextGrid", defaultextension=".TextGrid", initialfile="tone_export_data", filetypes=[("TextGrid 文件", "*.TextGrid")])
                if out and execute_export(out): messagebox.showinfo("成功", f"TextGrid 已导出至:\n{out}")
            elif format_mode == 'collection':
                out = filedialog.askdirectory(title="选择 Praat 集合导出文件夹") if mode == 'separate' else filedialog.asksaveasfilename(title="导出 Praat 工程集合 (.Collection)", defaultextension=".Collection", initialfile="phontracer_workspace.Collection", filetypes=[("Praat 集合文件 (*.Collection)", "*.Collection"), ("所有文件 (*.*)", "*.*")])
                if out and execute_export(out):
                    messagebox.showinfo("成功", f"Praat 工程集合已导出至:\n{out}\n\n可直接用 Praat 打开以恢复音频、TextGrid 与精修后的基频/共振峰对象。")
            elif format_mode == 'xlsx':
                out = filedialog.askdirectory(title="选择导出文件夹") if mode == 'separate' else filedialog.asksaveasfilename(title="导出Excel", defaultextension=".xlsx", initialfile="tone_export_data", filetypes=[("Excel 表格", "*.xlsx")])
                if out:
                    inc_chart = False if mode == 'integrated' else messagebox.askyesno("导出设置", "是否在 Excel 中包含分析图表？", default=messagebox.NO)
                    if execute_export(out, inc_chart): messagebox.showinfo("成功", f"数据已导出至:\n{out}")
            elif format_mode == 'line_chart':
                out = filedialog.askdirectory(title="选择图表导出文件夹") if mode == 'separate' else filedialog.asksaveasfilename(title="导出折线图", defaultextension=".png", initialfile="tone_line_chart", filetypes=[("PNG 图片", "*.png"), ("SVG 矢量图", "*.svg"), ("PDF 文档", "*.pdf")])
                if out and execute_export(out): messagebox.showinfo("成功", f"图表已导出至:\n{out}")
            elif format_mode == 'kde':
                out = filedialog.askdirectory(title="选择热力图导出文件夹") if mode == 'separate' else filedialog.asksaveasfilename(title="导出热力图", defaultextension=".png", initialfile="tone_heatmap", filetypes=[("PNG 图片", "*.png")])
                if out and execute_export(out): messagebox.showinfo("成功", f"热力图已导出至:\n{out}")

        ctk.CTkButton(dlg, text="  📄  文本文件 (.txt)", command=lambda: do_export('txt'),
                      fg_color=("#F3F4F6", "#374151"), text_color=("#374151", "#E5E7EB"), hover_color=("#E5E7EB", "#4B5563"), border_color=("#D1D5DB", "#475569"), **btn_kwargs).pack(fill=tk.X, padx=25, pady=4)
        ctk.CTkButton(dlg, text="  🏷  TextGrid 标注文件 (.TextGrid)", command=lambda: do_export('textgrid'),
                      fg_color=("#F3E8FF", "#3B0764"), text_color=("#6B21A8", "#E9D5FF"), hover_color=("#E9D5FF", "#5B21B6"), border_color=("#C084FC", "#A855F7"), **btn_kwargs).pack(fill=tk.X, padx=25, pady=4)
        ctk.CTkButton(dlg, text="  📦  Praat 工程集合 (.Collection)", command=lambda: do_export('collection'),
                      fg_color=("#FFF7ED", "#431407"), text_color=("#C2410C", "#FFEDD5"), hover_color=("#FFEDD5", "#7C2D12"), border_color=("#FB923C", "#F97316"), **btn_kwargs).pack(fill=tk.X, padx=25, pady=4)
        ctk.CTkButton(dlg, text="  📊  Excel 表格 (.xlsx)", command=lambda: do_export('xlsx'),
                      fg_color=("#ECFDF5", "#022C22"), text_color=("#047857", "#D1FAE5"), hover_color=("#D1FAE5", "#065F46"), border_color=("#34D399", "#10B981"), **btn_kwargs).pack(fill=tk.X, padx=25, pady=4)
        ctk.CTkButton(dlg, text="  📈  声学图表可视化导出", command=lambda: do_export('line_chart'),
                      fg_color=("#EFF6FF", "#172554"), text_color=("#1E40AF", "#DBEAFE"), hover_color=("#DBEAFE", "#1E40AF"), border_color=("#60A5FA", "#3B82F6"), **btn_kwargs).pack(fill=tk.X, padx=25, pady=4)

        btn_cancel = ctk.CTkButton(
            dlg,
            text="取消",
            height=36,
            corner_radius=18,
            fg_color=("#F3F4F6", "#374151"),
            text_color=("#4B5563", "#D1D5DB"),
            hover_color=("#E5E7EB", "#4B5563"),
            border_width=1,
            border_color=("#D1D5DB", "#475569"),
            font=self.font_main,
            command=dlg.destroy
        )
        btn_cancel.pack(fill=tk.X, padx=25, pady=(10, 20))
        # 时序密度热力图已整合至声学图表导出中

    def _ensure_item_loaded(self, item):
        """确保 item.snd 和 item.pitch / item.pitch_data 已正确加载或计算"""
        if not item: return

        long_snd = getattr(self.app, 'long_sound', None) or getattr(self.app, 'pending_long_snd', None) if self.app else None
        has_path = bool(item.get('path') and os.path.exists(item['path']))
        is_long = (not has_path) and (long_snd is not None) and (item.get('macro_start') is not None)

        if not has_path and not is_long:
            return

        has_snd = item.get('snd') is not None
        has_pitch = (item.get('pitch') is not None) or (item.get('pitch_data') is not None)

        if not has_snd or not has_pitch:
            try:
                if not has_snd:
                    if has_path:
                        item['snd'] = parselmouth.Sound(item['path'])
                    elif is_long:
                        item['snd'] = long_snd
                if not has_pitch:
                    from modules.audio_core import extract_f0
                    pf = item.get('pitch_floor', self.app_state_params.get('pitch_floor', 75))
                    pc = item.get('pitch_ceiling', self.app_state_params.get('pitch_ceiling', 600))
                    vt = item.get('voicing_threshold', self.app_state_params.get('voicing_threshold', 0.25))
                    if has_path:
                        global_pitch = item['snd'].to_pitch_ac(time_step=None, pitch_floor=pf, pitch_ceiling=pc, voicing_threshold=vt, very_accurate=True, octave_jump_cost=0.9)
                        item['pitch'] = global_pitch
                        item['pitch_data'] = {
                            'xs': global_pitch.xs().copy(),
                            'freqs': global_pitch.selected_array['frequency'].copy()
                        }
                    elif is_long:
                        global_pitch = item['snd'].to_pitch_ac(time_step=None, pitch_floor=pf, pitch_ceiling=pc, voicing_threshold=vt, very_accurate=True, octave_jump_cost=0.9)
                        item['pitch'] = global_pitch
                        pxs = global_pitch.xs()
                        pfreqs = global_pitch.selected_array['frequency']
                        ms = max(0.0, float(item.get('macro_start', 0.0)) - 0.05)
                        me = float(item.get('macro_end', item['snd'].get_total_duration())) + 0.05
                        mask = (pxs >= ms) & (pxs <= me)
                        item['pitch_data'] = {
                            'xs': pxs[mask].copy(),
                            'freqs': pfreqs[mask].copy()
                        }
            except Exception as e:
                logger.error(f"Error lazy loading sound/pitch for {item.get('path') or item.get('label')}: {e}", exc_info=True)

    def _extract_syl_data(self, item, num_points):
        """提取项目中每个字的真实发音段(收缩后)的 11 点 F0 数据和时长。返回 (总时长, [(字时长, [F0数组]), ...])"""
        if item.get('start') is None or not item.get('snd') or (not item.get('pitch') and not item.get('pitch_data')): return 0, []
        t_s, t_e = item['start'], item['end']
        if t_e <= t_s: return 0, []

        label = item.get('label', '')
        inner_splits = item.get('inner_splits', [])
        if item.get('pitch_data'):
            p_xs = item['pitch_data']['xs']
            p_freqs = item['pitch_data']['freqs']
        else:
            pitch = item['pitch']
            p_xs = pitch.xs()
            p_freqs = pitch.selected_array['frequency']

        syls = split_into_syllables(label)
        chars_bounds = item.get('chars_bounds', [])
        if chars_bounds and len(chars_bounds) == len(syls):
            bounds = chars_bounds
        else:
            splits = [t_s] + [s for s in inner_splits if t_s < s < t_e] + [t_e]
            if len(syls) > 1 and len(splits) != len(syls) + 1:
                splits = np.linspace(t_s, t_e, len(syls) + 1).tolist()
            elif len(syls) <= 1:
                splits = [t_s, t_e]
            bounds = [[splits[i], splits[i+1]] for i in range(len(splits)-1)]

        syl_data = []
        for c_s, c_e in bounds:
            if c_e <= c_s:
                syl_data.append((0.0, [0.0]*num_points))
                continue

            # 智能收缩！找到真正发声的核（有效基频段）
            valid_idx = np.where((p_xs >= c_s) & (p_xs <= c_e) & (p_freqs > 0))[0]
            if len(valid_idx) >= 2:
                v_s, v_e = p_xs[valid_idx[0]], p_xs[valid_idx[-1]]
                seg_xs = p_xs[valid_idx]     # 取出该字的真实时间轴
                seg_ys = p_freqs[valid_idx]  # 取出该字的真实F0值
            else:
                syl_data.append((0.0, [0.0]*num_points))
                continue

            dur = v_e - v_s
            if dur <= 0:
                syl_data.append((0.0, [0.0]*num_points))
                continue

            times = np.linspace(v_s, v_e, num_points)
            # 修复点：改用 numpy 局部插值，杜绝抓取界外的清辅音假象
            if len(seg_xs) >= 2:
                f0s = np.interp(times, seg_xs, seg_ys).tolist()
                # 修正：跨越静音区（>25ms）时强制归零，避免产生假数据桥接
                for j, t in enumerate(times):
                    if np.min(np.abs(seg_xs - t)) > 0.025:
                        f0s[j] = 0.0
                syl_data.append((dur, f0s))
            else:
                syl_data.append((dur, [0.0]*num_points))

        return t_e - t_s, syl_data

    def _get_pitch_arrays_for_item(self, item):
        from modules.export_service import UnifiedExportService
        return UnifiedExportService.get_pitch_arrays_for_item(item)

    def _write_raw_pitch_sheet(self, workbook, rows, include_speaker=False):
        """写入逐点 Hz 原始基频数据。这里保留 pitch_data 当前状态，包括橡皮擦置零后的点。"""
        from modules.data_utils import write_raw_pitch_sheet
        return write_raw_pitch_sheet(workbook, rows, include_speaker=include_speaker)

    def _get_syllables_and_bounds(self, item):
        """返回与当前编辑边界一致的子段列表，优先使用 chars_bounds。"""
        from modules.export_service import UnifiedExportService
        return UnifiedExportService.get_syllables_and_bounds(item)

    def _extract_kde_contour(self, p_xs, p_freqs, c_s, c_e, n_dense):
        """提取 KDE 用的连续 F0 轮廓；橡皮擦/无声缺口保留为 NaN，后续绘制会跳过。"""
        from modules.export_service import UnifiedExportService
        return UnifiedExportService.extract_kde_contour(p_xs, p_freqs, c_s, c_e, n_dense)

    def _export_xlsx(self, out_file, include_chart=False, tree_structure=None):
        try:
            import xlsxwriter
        except ImportError:
            messagebox.showerror("错误", "缺少 xlsxwriter 库，请先安装：pip install xlsxwriter")
            return

        is_continuous = (self.num_rule_var.get() == "continuous")
        num_points = self.app_state_params['pts']
        if tree_structure is None: tree_structure = self._get_all_items_by_group()

        max_syls = 1
        for grp_name, children in tree_structure:
            for child in children:
                lbl = self.items[child].get('label', '')
                if len(lbl) > max_syls: max_syls = len(lbl)

        workbook = xlsxwriter.Workbook(out_file)
        from modules.data_utils import ExcelStyleManager, autofit_worksheet_columns, write_analysis_sheet_with_formulas, build_five_point_chart
        style = ExcelStyleManager(workbook)

        ws_data = workbook.add_worksheet("数据")
        ws_res = workbook.add_worksheet("分析结果")
        raw_pitch_rows = []

        headers = ["组别", "编号", "词语", "总时长(s)"]
        for k in range(1, max_syls + 1):
            headers.append(f"字{k}_时长(s)")
            for i in range(1, num_points + 1):
                headers.append(f"字{k}_P{i}(Hz)")
        for col, header in enumerate(headers):
            ws_data.write(0, col, header, style.header_fmt)

        global_idx = 1
        row_idx = 1

        dict_data = {}
        data_sample_rows = []

        for grp_name, children in tree_structure:
            if not is_continuous: global_idx = 1
            for child in children:
                item = self.items[child]
                self._ensure_item_loaded(item)
                if not item.get('snd') or (not item.get('pitch') and not item.get('pitch_data')):
                    continue

                total_dur, syl_data = self._extract_syl_data(item, num_points)
                if total_dur <= 0: continue
                raw_pitch_rows.append({
                    'group': grp_name,
                    'index': global_idx,
                    'item': item
                })

                if grp_name not in dict_data:
                    dict_data[grp_name] = {
                        'syl_dur_sums': [0.0]*max_syls, 'syl_counts': [0]*max_syls,
                        'f0_sums': [[0.0]*num_points for _ in range(max_syls)],
                        'f0_counts': [[0]*num_points for _ in range(max_syls)]
                    }

                col = 0
                ws_data.write(row_idx, col, grp_name, style.text_center); col += 1
                ws_data.write(row_idx, col, global_idx, style.int_fmt); col += 1
                ws_data.write(row_idx, col, item['label'], style.text_left); col += 1
                ws_data.write(row_idx, col, round(total_dur, 6), style.duration_fmt); col += 1

                sample_row = [grp_name, global_idx, item['label'], round(total_dur, 6)]
                for k in range(max_syls):
                    if k < len(syl_data):
                        dur, f0s = syl_data[k]
                        ws_data.write(row_idx, col, round(dur, 6), style.duration_fmt); col += 1
                        sample_row.append(round(dur, 6))
                        dict_data[grp_name]['syl_dur_sums'][k] += dur
                        dict_data[grp_name]['syl_counts'][k] += 1
                        for i, f0 in enumerate(f0s):
                            if not np.isnan(f0) and f0 > 0:
                                ws_data.write(row_idx, col, round(f0, 6), style.hz_fmt); col += 1
                                sample_row.append(round(f0, 6))
                                dict_data[grp_name]['f0_sums'][k][i] += f0
                                dict_data[grp_name]['f0_counts'][k][i] += 1
                            else:
                                ws_data.write(row_idx, col, "", style.text_center); col += 1
                                sample_row.append("")
                    else:
                        ws_data.write(row_idx, col, "", style.text_center); col += 1
                        sample_row.append("")
                        for _ in range(num_points):
                            ws_data.write(row_idx, col, "", style.text_center); col += 1
                            sample_row.append("")

                if len(data_sample_rows) < 100:
                    data_sample_rows.append(sample_row)

                row_idx += 1
                global_idx += 1

        ws_data.freeze_panes(1, 0)
        ws_data.autofilter(0, 0, max(row_idx - 1, 1), len(headers) - 1)
        autofit_worksheet_columns(ws_data, headers, data_sample_rows)

        self._write_raw_pitch_sheet(workbook, raw_pitch_rows, include_speaker=False)

        all_avg_hz = []
        avg_points_map = {}

        for grp, st in dict_data.items():
            avg_points_map[grp] = []
            for k in range(max_syls):
                syl_avgs = []
                for i in range(num_points):
                    cnt = st['f0_counts'][k][i]
                    avg_hz = st['f0_sums'][k][i] / cnt if cnt > 0 else 0
                    syl_avgs.append(avg_hz)
                    if avg_hz > 0: all_avg_hz.append(avg_hz)
                avg_points_map[grp].append(syl_avgs)

        if not all_avg_hz:
            workbook.close()
            return

        min_hz, max_hz = min(all_avg_hz), max(all_avg_hz)

        # 写入分析结果 Sheet（全部使用 Excel 公式引用数据表）
        group_list = list(dict_data.keys())
        last_data_row = row_idx - 1  # 0-indexed
        res_row, _, _ = write_analysis_sheet_with_formulas(
            workbook, ws_res, group_list, num_points, max_syls, last_data_row
        )

        if include_chart:
            try:
                build_five_point_chart(
                    workbook, ws_res, dict_data, avg_points_map,
                    num_points, max_syls, min_hz, max_hz,
                    insert_cell=f'A{res_row + 3}',
                    chart_title='各声调平均基频五度标调图（保留真实时长）'
                )
            except Exception as chart_err:
                logger.error(f"Error generating Excel chart: {chart_err}", exc_info=True)

        workbook.close()

    def _export_integrated(self, out_file, format_mode, include_chart, all_speakers):
        import math
        try: import xlsxwriter
        except ImportError:
            if format_mode == 'xlsx': return messagebox.showerror("错误", "缺少 xlsxwriter 库，请先安装：pip install xlsxwriter")
        is_continuous = (self.num_rule_var.get() == "continuous")
        num_points = self.app_state_params['pts']
        speaker_stats = {}
        speaker_rows = {}
        max_syls = 1
        from modules.data_utils import compute_speaker_f0_bounds, hz_to_t_value
        bounds_method = getattr(self, 'f0_bounds_method', 'p5_p95_iqr')
        for speaker in all_speakers:
            s_struct = self._get_items_by_group_for_dict(speaker.items)
            rows = []
            f0_values = []
            orig_items = self.items
            try:
                self.items = speaker.items
                for grp_name, children in s_struct:
                    for child in children:
                        item = self.items[child]
                        lbl = item.get('label', '')
                        if len(lbl) > max_syls: max_syls = len(lbl)
                        self._ensure_item_loaded(item)
                        if not item.get('snd') or (not item.get('pitch') and not item.get('pitch_data')):
                            continue
                        total_dur, syl_data = self._extract_syl_data(item, num_points)
                        if total_dur <= 0: continue
                        rows.append({'group': grp_name, 'label': lbl, 'total_dur': total_dur, 'syl_data': syl_data, 'raw_item': item})
                        for _, freqs in syl_data:
                            for f in freqs:
                                if f > 0: f0_values.append(f)
            finally:
                self.items = orig_items
            if f0_values:
                speaker_stats[speaker.id] = compute_speaker_f0_bounds(f0_values, method=bounds_method)
            else:
                speaker_stats[speaker.id] = (0.0, 0.0)
            speaker_rows[speaker.id] = rows

        if format_mode == 'xlsx':
            workbook = xlsxwriter.Workbook(out_file)
            from modules.data_utils import ExcelStyleManager, autofit_worksheet_columns
            style = ExcelStyleManager(workbook)

            ws_data = workbook.add_worksheet("整合数据(T值)")
            ws_res = workbook.add_worksheet("分析结果")
            headers = ["发音人", "组别", "编号", "词语", "总时长(s)"]
            for k in range(1, max_syls + 1):
                headers.append(f"字{k}_时长(s)")
                for i in range(1, num_points + 1): headers.append(f"字{k}_T{i}")
            for col, header in enumerate(headers):
                ws_data.write(0, col, header, style.header_fmt)

            group_stats = {}
            raw_pitch_rows = []
            int_data_sample_rows = []
            row_idx = 1
            for speaker in all_speakers:
                rows = speaker_rows.get(speaker.id, [])
                s_min, s_max = speaker_stats.get(speaker.id, (0.0, 0.0))
                has_valid_range = (s_min > 0 and s_max > s_min)
                log_min = math.log10(s_min) if s_min > 0 else 0.0
                log_max = math.log10(s_max) if s_max > 0 else 0.0
                log_diff = log_max - log_min if log_max > log_min else 1.0

                current_group = None
                group_item_idx = 0
                global_idx = 0
                for r in rows:
                    if r['group'] != current_group:
                        current_group = r['group']
                        group_item_idx = 1
                    else:
                        group_item_idx += 1
                    global_idx += 1
                    display_idx = global_idx if is_continuous else group_item_idx

                    raw_pitch_rows.append({
                        'speaker': speaker.name,
                        'group': r['group'],
                        'index': display_idx,
                        'item': r['raw_item']
                    })
                    ws_data.write(row_idx, 0, speaker.name, style.text_center)
                    ws_data.write(row_idx, 1, r['group'], style.text_center)
                    ws_data.write(row_idx, 2, display_idx, style.int_fmt)
                    ws_data.write(row_idx, 3, r['label'], style.text_left)
                    ws_data.write(row_idx, 4, round(r['total_dur'], 4), style.duration_fmt)
                    col_idx = 5

                    sample_r = [speaker.name, r['group'], display_idx, r['label'], round(r['total_dur'], 4)]

                    grp_name = r['group']
                    if grp_name not in group_stats:
                        group_stats[grp_name] = {
                            'syl_dur_sums': [0.0] * max_syls,
                            'syl_counts': [0] * max_syls,
                            't_sums': [[0.0] * num_points for _ in range(max_syls)],
                            't_counts': [[0] * num_points for _ in range(max_syls)]
                        }
                    stats = group_stats[grp_name]

                    for k, (s_dur, freqs) in enumerate(r['syl_data']):
                        ws_data.write(row_idx, col_idx, round(s_dur, 4), style.duration_fmt)
                        sample_r.append(round(s_dur, 4))
                        col_idx += 1
                        if k < max_syls:
                            stats['syl_dur_sums'][k] += s_dur
                            stats['syl_counts'][k] += 1
                        for i, f in enumerate(freqs):
                            if f > 0 and has_valid_range:
                                t_val = round(float(hz_to_t_value(f, s_min, s_max, clip=True)), 2)
                                ws_data.write(row_idx, col_idx, t_val, style.t_val_fmt)
                                sample_r.append(t_val)
                                if k < max_syls:
                                    stats['t_sums'][k][i] += t_val
                                    stats['t_counts'][k][i] += 1
                            else:
                                ws_data.write(row_idx, col_idx, "", style.text_center)
                                sample_r.append("")
                            col_idx += 1

                    fill_count = max_syls - len(r['syl_data'])
                    for _ in range(fill_count):
                        ws_data.write(row_idx, col_idx, "", style.text_center)
                        sample_r.append("")
                        col_idx += 1
                        for _ in range(num_points):
                            ws_data.write(row_idx, col_idx, "", style.text_center)
                            sample_r.append("")
                            col_idx += 1
                    if len(int_data_sample_rows) < 100:
                        int_data_sample_rows.append(sample_r)
                    global_idx += 1
                    row_idx += 1

            ws_data.freeze_panes(1, 0)
            ws_data.autofilter(0, 0, max(row_idx - 1, 1), len(headers) - 1)
            autofit_worksheet_columns(ws_data, headers, int_data_sample_rows)

            res_headers = ["组别"]
            for k in range(1, max_syls + 1):
                res_headers.append(f"字{k}_平均时长(s)")
                for i in range(1, num_points + 1): res_headers.append(f"字{k}_T{i}")
            for col, header in enumerate(res_headers):
                ws_res.write(0, col, header, style.header_fmt)

            res_row = 1
            int_res_sample_rows = []
            for grp, st in group_stats.items():
                ws_res.write(res_row, 0, grp, style.text_center)
                col = 1
                sample_res = [grp]
                for k in range(max_syls):
                    cnt = st['syl_counts'][k]
                    if cnt > 0:
                        avg_dur = st['syl_dur_sums'][k] / cnt
                        ws_res.write(res_row, col, round(avg_dur, 4), style.duration_fmt)
                        sample_res.append(round(avg_dur, 4))
                    else:
                        ws_res.write(res_row, col, "", style.text_center)
                        sample_res.append("")
                    col += 1
                    for i in range(num_points):
                        t_cnt = st['t_counts'][k][i]
                        if t_cnt > 0:
                            ws_res.write(res_row, col, round(st['t_sums'][k][i] / t_cnt, 2), style.t_val_fmt)
                            sample_res.append(round(st['t_sums'][k][i] / t_cnt, 2))
                        else:
                            ws_res.write(res_row, col, "", style.text_center)
                            sample_res.append("")
                        col += 1
                if len(int_res_sample_rows) < 100:
                    int_res_sample_rows.append(sample_res)
                res_row += 1

            ws_res.freeze_panes(1, 0)
            ws_res.autofilter(0, 0, max(res_row - 1, 1), len(res_headers) - 1)
            autofit_worksheet_columns(ws_res, res_headers, int_res_sample_rows)

            self._write_raw_pitch_sheet(workbook, raw_pitch_rows, include_speaker=True)

            if include_chart and group_stats:
                try:
                    # 将 group_stats 的 t_sums/t_counts 转换为 avg_points_map 格式（Hz 平均值）
                    # 注意：整合模式下 t_sums/t_counts 里存的已经是 T 值而非 Hz，
                    # 但 build_five_point_chart 需要 Hz 形式的 avg_points_map 和 min_hz/max_hz。
                    # 这里直接复用 speaker_stats 中各发音人的原始 Hz 汇总来构建。
                    # 由于整合模式比较特殊（跨发音人归一化），
                    # 此处使用独立的折线图保持兼容。
                    ws_chart_data = workbook.add_worksheet("图表数据")
                    ws_chart_data.hide()
                    ws_chart_data.write(0, 0, "组别")
                    for p in range(1, max_syls * num_points + 1):
                        ws_chart_data.write(0, p, p)
                    chart_row = 1
                    for grp, st in group_stats.items():
                        ws_chart_data.write(chart_row, 0, grp)
                        col_idx = 1
                        for k in range(max_syls):
                            for i in range(num_points):
                                t_cnt = st['t_counts'][k][i]
                                if t_cnt > 0:
                                    ws_chart_data.write(chart_row, col_idx, round(st['t_sums'][k][i] / t_cnt, 2))
                                else:
                                    ws_chart_data.write(chart_row, col_idx, "")
                                col_idx += 1
                        chart_row += 1
                    chart = workbook.add_chart({'type': 'scatter', 'subtype': 'straight_with_markers'})
                    for r in range(1, len(group_stats) + 1):
                        chart.add_series({
                            'name':       ['图表数据', r, 0],
                            'categories': ['图表数据', 0, 1, 0, max_syls * num_points],
                            'values':     ['图表数据', r, 1, r, max_syls * num_points],
                            'line':       {'width': 2.5},
                            'marker':     {'type': 'circle', 'size': 6},
                        })
                    chart.set_title({
                        'name': '多发音人整合声调格局图',
                        'name_font': {'name': 'Microsoft YaHei', 'size': 14, 'bold': True}
                    })
                    chart.set_x_axis({
                        'name': '测量点 (时序展开)',
                        'name_font': {'name': 'Microsoft YaHei', 'size': 10},
                        'num_font': {'name': 'Arial', 'size': 9}
                    })
                    chart.set_y_axis({
                        'name': '赵元任五度标调法',
                        'name_font': {'name': 'Microsoft YaHei', 'size': 10},
                        'num_font': {'name': 'Arial', 'size': 1, 'color': 'white'},
                        'min': 0, 'max': 5,
                        'major_unit': 1,
                        'major_gridlines': {'visible': True, 'line': {'color': '#D0D0D0', 'width': 0.5}},
                        'major_tick_mark': 'none',
                    })
                    chart.set_legend({
                        'position': 'right',
                        'font': {'name': 'Microsoft YaHei', 'size': 9}
                    })
                    chart.set_size({'width': 650, 'height': 450})
                    ws_res.insert_chart(f'A{res_row + 3}', chart)
                except Exception as chart_err:
                    logger.error(f"Error generating integrated Excel chart: {chart_err}", exc_info=True)
            workbook.close()
        elif format_mode == 'txt':
            with open(out_file, 'w', encoding='utf-8-sig') as f_out:
                headers = ["发音人", "组别", "编号", "词语", "总时长(s)"]
                for k in range(1, max_syls + 1):
                    headers.append(f"字{k}_时长(s)")
                    for i in range(1, num_points + 1): headers.append(f"字{k}_T{i}")
                f_out.write("\t".join(headers) + "\n")
                for speaker in all_speakers:
                    rows = speaker_rows.get(speaker.id, [])
                    s_min, s_max = speaker_stats.get(speaker.id, (0.0, 0.0))
                    has_valid_range = (s_min > 0 and s_max > s_min)
                    log_min = math.log10(s_min) if s_min > 0 else 0.0
                    log_max = math.log10(s_max) if s_max > 0 else 0.0
                    log_diff = log_max - log_min if log_max > log_min else 1.0

                    current_group = None
                    group_item_idx = 0
                    global_idx = 0
                    for r in rows:
                        if r['group'] != current_group:
                            current_group = r['group']
                            group_item_idx = 1
                        else:
                            group_item_idx += 1
                        global_idx += 1
                        display_idx = global_idx if is_continuous else group_item_idx
                        line_parts = [speaker.name, r['group'], str(display_idx), r['label'], f"{r['total_dur']:.4f}"]
                        for s_dur, freqs in r['syl_data']:
                            line_parts.append(f"{s_dur:.4f}")
                            for f in freqs:
                                if f > 0 and has_valid_range:
                                    t_v = ((math.log10(f) - log_min) / log_diff) * 5.0
                                    line_parts.append(f"{t_v:.2f}")
                                else:
                                    line_parts.append("")
                        fill_count = max_syls - len(r['syl_data'])
                        for _ in range(fill_count):
                            line_parts.append("")
                            for _ in range(num_points): line_parts.append("")
                        f_out.write("\t".join(line_parts) + "\n")
                        global_idx += 1

    def _export_txt(self, out_file, tree_structure=None):
        is_continuous = (self.num_rule_var.get() == "continuous")
        if tree_structure is None: tree_structure = self._get_all_items_by_group()

        mode = getattr(self, 'app_state_params', {}).get('analysis_mode', 'f0')

        with open(out_file, "w", encoding="utf-8-sig") as f:
            global_idx = 1
            for grp_name, children in tree_structure:
                if not is_continuous: global_idx = 1
                if grp_name and grp_name.strip() and grp_name not in ("未分组", "导入内容"):
                    f.write(f"{grp_name}\n")
                for child in children:
                    item = self.items[child]
                    if item['start'] is not None:
                        item_mode = self._get_item_analysis_mode(item)
                        if item_mode == 'formant':
                            from modules.data_utils import get_formant_export_text_for_item
                            txt_data = get_formant_export_text_for_item(item, global_idx, self.app_state_params['pts'])
                        else:
                            f0_params = self._get_item_f0_export_params(item)
                            txt_data = get_export_text_for_item(item, global_idx, self.app_state_params['pts'], **f0_params)
                        f.write(txt_data)
                        global_idx += 1

    def _collect_group_avg_data(self, tree_structure=None):
        num_points = self.app_state_params['pts']
        if tree_structure is None: tree_structure = self._get_all_items_by_group()

        max_syls = 1
        dict_data = {}
        for grp_name, children in tree_structure:
            for child in children:
                lbl = self.items[child].get('label', '')
                if len(lbl) > max_syls: max_syls = len(lbl)
                item = self.items[child]
                self._ensure_item_loaded(item)
                if not item.get('snd') or (not item.get('pitch') and not item.get('pitch_data')):
                    continue
                total_dur, syl_data = self._extract_syl_data(item, num_points)
                if total_dur <= 0: continue

                if grp_name not in dict_data:
                    dict_data[grp_name] = { 'f0_sums': [[0.0]*num_points for _ in range(20)], 'f0_counts': [[0]*num_points for _ in range(20)] }
                for k, (dur, f0s) in enumerate(syl_data):
                    for i, f0 in enumerate(f0s):
                        if not np.isnan(f0) and f0 > 0:
                            dict_data[grp_name]['f0_sums'][k][i] += f0
                            dict_data[grp_name]['f0_counts'][k][i] += 1

        all_avg_hz = []
        avg_points_map = {}
        for grp, st in dict_data.items():
            avg_points_map[grp] = []
            for k in range(max_syls):
                syl_avgs = []
                for i in range(num_points):
                    cnt = st['f0_counts'][k][i]
                    hz = st['f0_sums'][k][i] / cnt if cnt > 0 else 0
                    syl_avgs.append(hz)
                    if hz > 0: all_avg_hz.append(hz)
                avg_points_map[grp].append(syl_avgs)

        if not all_avg_hz: return None, 1
        min_hz, max_hz = min(all_avg_hz), max(all_avg_hz)

        result = {}
        for grp, syl_avgs_list in avg_points_map.items():
            flat_t_vals = []
            for syl_avgs in syl_avgs_list:
                for h in syl_avgs:
                    if h > 0 and max_hz > min_hz and min_hz > 0:
                        flat_t_vals.append(5 * (math.log10(h) - math.log10(min_hz)) / (math.log10(max_hz) - math.log10(min_hz)))
                    else: flat_t_vals.append(None)
            result[grp] = flat_t_vals

        return result, max_syls

    def _export_line_chart(self, out_file, tree_structure=None):
        data, max_syls = self._collect_group_avg_data(tree_structure=tree_structure)
        if not data: return messagebox.showwarning("提示", "没有有效数据可供绘图。")
        self._draw_line_chart(data, max_syls, out_file)

    def _draw_line_chart(self, data, max_syls, out_file):
        num_points = self.app_state_params['pts']
        plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'Arial Unicode MS']
        plt.rcParams['axes.unicode_minus'] = False

        fig, ax = plt.subplots(figsize=(6 + 4 * max_syls, 6))
        total_points = max_syls * num_points
        x_vals = list(range(1, total_points + 1))

        colors = ['#2563EB', '#DC2626', '#16A34A', '#9333EA', '#EA580C', '#0891B2', '#CA8A04', '#6366F1']

        for i, (name, t_vals) in enumerate(data.items()):
            color = colors[i % len(colors)]
            label_added = False
            for k in range(max_syls):
                s_start = k * num_points
                s_end = (k + 1) * num_points
                s_t_vals = t_vals[s_start:s_end]
                s_x_vals = x_vals[s_start:s_end]

                valid_x = [x for x, v in zip(s_x_vals, s_t_vals) if v is not None]
                valid_y = [v for v in s_t_vals if v is not None]
                if valid_x:
                    lbl = name if not label_added else None
                    ax.plot(valid_x, valid_y, '-o', color=color, linewidth=2, markersize=5, label=lbl)
                    label_added = True

        ax.set_ylim(0, 5)
        ax.set_xlim(0.5, total_points + 0.5)
        ax.set_yticks([0, 0.5, 1, 1.5, 2, 2.5, 3, 3.5, 4, 4.5, 5])

        ax.set_xticks(range(1, total_points + 1))
        ax.set_xticklabels([(idx % num_points) + 1 for idx in range(total_points)])

        for k in range(1, max_syls):
            div_x = k * num_points + 0.5
            ax.axvline(div_x, color='gray', linestyle='--', alpha=0.5)
            ax.text(div_x - num_points/2, 5.1, f"第 {k} 字", ha='center', va='bottom', fontsize=12, fontweight='bold', color='#4B5563')
            if k == max_syls - 1:
                ax.text(div_x + num_points/2, 5.1, f"第 {k+1} 字", ha='center', va='bottom', fontsize=12, fontweight='bold', color='#4B5563')

        ax.set_xlabel('测量点 (沿时序展开)', fontsize=12)
        ax.set_ylabel('T 值 (0-5 标度)', fontsize=12)
        ax.set_title('连读变调声调格局图', fontsize=16, fontweight='bold', pad=25)
        ax.legend(loc='best', fontsize=10)
        ax.grid(True, alpha=0.3)

        fig.tight_layout()
        fig.savefig(out_file, dpi=300, bbox_inches='tight')
        plt.close(fig)

    def _export_line_chart_integrated(self, out_file, all_speakers):
        num_points = self.app_state_params['pts']
        max_syls = 1
        aggregated_t_sums = {}
        aggregated_t_counts = {}

        for speaker in all_speakers:
            s_struct = self._get_items_by_group_for_dict(speaker.items)
            orig_items = self.items
            self.items = speaker.items

            dict_data = {}
            for grp_name, children in s_struct:
                for child in children:
                    item = self.items[child]
                    lbl = item.get('label', '')
                    if len(lbl) > max_syls: max_syls = len(lbl)
                    self._ensure_item_loaded(item)
                    if not item.get('snd') or (not item.get('pitch') and not item.get('pitch_data')):
                        continue
                    total_dur, syl_data = self._extract_syl_data(item, num_points)
                    if total_dur <= 0: continue

                    if grp_name not in dict_data:
                        dict_data[grp_name] = { 'f0_sums': [[0.0]*num_points for _ in range(20)], 'f0_counts': [[0]*num_points for _ in range(20)] }
                    for k, (dur, f0s) in enumerate(syl_data):
                        for i, f0 in enumerate(f0s):
                            if not np.isnan(f0) and f0 > 0:
                                dict_data[grp_name]['f0_sums'][k][i] += f0
                                dict_data[grp_name]['f0_counts'][k][i] += 1

            all_hz = []
            avg_points_map = {}
            for grp, st in dict_data.items():
                avg_points_map[grp] = []
                for k in range(max_syls):
                    syl_avgs = []
                    for i in range(num_points):
                        cnt = st['f0_counts'][k][i]
                        hz = st['f0_sums'][k][i] / cnt if cnt > 0 else 0
                        syl_avgs.append(hz)
                        if hz > 0: all_hz.append(hz)
                    avg_points_map[grp].append(syl_avgs)

            if all_hz:
                min_hz, max_hz = min(all_hz), max(all_hz)
                for grp, syl_avgs_list in avg_points_map.items():
                    if grp not in aggregated_t_sums:
                        aggregated_t_sums[grp] = [[0.0]*num_points for _ in range(max_syls)]
                        aggregated_t_counts[grp] = [[0]*num_points for _ in range(max_syls)]
                    for k, syl_avgs in enumerate(syl_avgs_list):
                        for i, h in enumerate(syl_avgs):
                            if h > 0 and max_hz > min_hz and min_hz > 0:
                                t_val = 5 * (math.log10(h) - math.log10(min_hz)) / (math.log10(max_hz) - math.log10(min_hz))
                                aggregated_t_sums[grp][k][i] += t_val
                                aggregated_t_counts[grp][k][i] += 1

            self.items = orig_items

        data = {}
        for grp in aggregated_t_sums:
            flat_t_vals = []
            for k in range(max_syls):
                for i in range(num_points):
                    cnt = aggregated_t_counts[grp][k][i]
                    if cnt > 0:
                        flat_t_vals.append(aggregated_t_sums[grp][k][i] / cnt)
                    else:
                        flat_t_vals.append(None)
            data[grp] = flat_t_vals

        if not data: return messagebox.showwarning("提示", "没有有效数据可供绘图。")
        self._draw_line_chart(data, max_syls, out_file)

    def _show_kde_params_dialog(self, mode='single', tree_structure=None, all_speakers=None):
        param_dlg = ctk.CTkToplevel(self.parent)
        param_dlg.title("PhonTracer语音学标注与声学分析系统 V1.0 - 时序密度图参数设置")
        param_dlg.geometry("450x380")
        param_dlg.resizable(False, False)
        param_dlg.transient(self.parent)
        param_dlg.grab_set()
        param_dlg.configure(fg_color=("#FFFFFF", "#1A1D24"))

        param_dlg.update_idletasks()
        main_win = self.parent.winfo_toplevel()
        x = main_win.winfo_rootx() + (main_win.winfo_width() - 450) // 2
        y = main_win.winfo_rooty() + (main_win.winfo_height() - 380) // 2
        param_dlg.geometry(f"+{x}+{y}")

        ctk.CTkLabel(param_dlg, text="词语时序密度热力图参数设置", font=self.font_title, text_color=("#111827", "#F9FAFB")).pack(pady=(15, 10))

        bw_frame = ctk.CTkFrame(param_dlg, fg_color="transparent")
        bw_frame.pack(fill=tk.X, padx=30, pady=5)

        ctk.CTkLabel(bw_frame, text="核密度带宽 (Bandwidth):", font=self.font_main, text_color=("#374151", "#E5E7EB")).pack(side=tk.LEFT)

        bw_val_lbl = ctk.CTkLabel(bw_frame, text="0.15", font=self.font_main, width=40, text_color=("#374151", "#E5E7EB"))

        def on_slider_change(val):
            bw_val_lbl.configure(text=f"{float(val):.2f}")

        bw_slider = ctk.CTkSlider(bw_frame, from_=0.05, to=0.50, number_of_steps=45, command=on_slider_change)
        bw_slider.set(0.15)
        bw_slider.pack(side=tk.RIGHT, padx=(10, 0))
        bw_val_lbl.pack(side=tk.RIGHT)

        f0_frame = ctk.CTkFrame(param_dlg, fg_color="transparent")
        f0_frame.pack(fill=tk.X, padx=30, pady=10)

        ctk.CTkLabel(f0_frame, text="基频 T值归一化范围:", font=self.font_main, text_color=("#374151", "#E5E7EB")).pack(anchor="w", pady=(0, 5))

        f0_mode_var = ctk.StringVar(value="percentile")

        pct_frame = ctk.CTkFrame(param_dlg, fg_color="transparent")
        manual_frame = ctk.CTkFrame(param_dlg, fg_color="transparent")

        def update_f0_mode_ui():
            val = f0_mode_var.get()
            if val == "percentile":
                pct_frame.pack(fill=tk.X, padx=50, pady=2)
                manual_frame.pack_forget()
            elif val == "manual":
                manual_frame.pack(fill=tk.X, padx=50, pady=2)
                pct_frame.pack_forget()
            else:
                pct_frame.pack_forget()
                manual_frame.pack_forget()

        r_pct = ctk.CTkRadioButton(f0_frame, text="分位数自动截断 (推荐, 消除极端值压缩)", variable=f0_mode_var, value="percentile", font=self.font_main, text_color=("#374151", "#E5E7EB"), command=update_f0_mode_ui)
        r_pct.pack(anchor="w", pady=2)

        r_minmax = ctk.CTkRadioButton(f0_frame, text="极值自动范围 (Min ~ Max)", variable=f0_mode_var, value="minmax", font=self.font_main, text_color=("#374151", "#E5E7EB"), command=update_f0_mode_ui)
        r_minmax.pack(anchor="w", pady=2)

        r_manual = ctk.CTkRadioButton(f0_frame, text="手动指定范围 (Hz)", variable=f0_mode_var, value="manual", font=self.font_main, text_color=("#374151", "#E5E7EB"), command=update_f0_mode_ui)
        r_manual.pack(anchor="w", pady=2)

        ctk.CTkLabel(pct_frame, text="分位区间 (Low % ~ High %):", font=self.font_main, text_color=("#374151", "#E5E7EB")).pack(side=tk.LEFT)
        pct_low_ent = ctk.CTkEntry(pct_frame, width=50, font=self.font_main)
        pct_low_ent.insert(0, "5")
        pct_low_ent.pack(side=tk.LEFT, padx=5)
        ctk.CTkLabel(pct_frame, text="~", font=self.font_main, text_color=("#374151", "#E5E7EB")).pack(side=tk.LEFT)
        pct_high_ent = ctk.CTkEntry(pct_frame, width=50, font=self.font_main)
        pct_high_ent.insert(0, "95")
        pct_high_ent.pack(side=tk.LEFT, padx=5)

        ctk.CTkLabel(manual_frame, text="基频范围 (Min Hz ~ Max Hz):", font=self.font_main, text_color=("#374151", "#E5E7EB")).pack(side=tk.LEFT)
        min_hz_ent = ctk.CTkEntry(manual_frame, width=60, font=self.font_main)
        min_hz_ent.insert(0, "75")
        min_hz_ent.pack(side=tk.LEFT, padx=5)
        ctk.CTkLabel(manual_frame, text="~", font=self.font_main, text_color=("#374151", "#E5E7EB")).pack(side=tk.LEFT)
        max_hz_ent = ctk.CTkEntry(manual_frame, width=60, font=self.font_main)
        max_hz_ent.insert(0, "600")
        max_hz_ent.pack(side=tk.LEFT, padx=5)

        update_f0_mode_ui()

        def on_confirm():
            bw = float(bw_slider.get())
            f0_mode = f0_mode_var.get()

            p_low = 5.0
            p_high = 95.0
            m_min = 75.0
            m_max = 600.0

            if f0_mode == 'percentile':
                try:
                    p_low = float(pct_low_ent.get())
                    p_high = float(pct_high_ent.get())
                    if not (0 <= p_low < p_high <= 100): raise ValueError
                except ValueError:
                    return messagebox.showerror("错误", "请输入有效的百分比 (0 到 100) 且低分位数必须小于高分位数。")
            elif f0_mode == 'manual':
                try:
                    m_min = float(min_hz_ent.get())
                    m_max = float(max_hz_ent.get())
                    if not (0 < m_min < m_max): raise ValueError
                except ValueError:
                    return messagebox.showerror("错误", "请输入有效的基频范围 (Hz)。")

            param_dlg.destroy()

            out = filedialog.askdirectory(title="选择热力图导出文件夹") if mode == 'separate' else filedialog.asksaveasfilename(title="导出热力图", defaultextension=".png", initialfile="tone_heatmap", filetypes=[("PNG 图片", "*.png")])
            if not out: return

            params = {
                'bw_method': bw,
                'f0_mode': f0_mode,
                'percentile_low': p_low,
                'percentile_high': p_high,
                'manual_min': m_min,
                'manual_max': m_max
            }

            try:
                if mode == 'single':
                    success = self._export_kde_heatmap(out, tree_structure=tree_structure, params=params)
                elif mode == 'separate':
                    import os
                    success = True
                    for s in all_speakers:
                        s_struct = self._get_items_by_group_for_dict(s.items)
                        orig_items = self.items
                        self.items = s.items
                        if os.path.isdir(out):
                            s_out = os.path.join(out, f"{s.name}.png")
                        else:
                            base, ext = os.path.splitext(out)
                            s_out = f"{base}_{s.name}{ext}"

                        ret = self._export_kde_heatmap(s_out, tree_structure=s_struct, params=params)
                        self.items = orig_items
                        if not ret: success = False
                else:
                    success = self._export_kde_heatmap_integrated(out, all_speakers, params=params)

                if success:
                    messagebox.showinfo("成功", f"热力图已导出至:\n{out}")
            except Exception as e:
                messagebox.showerror("错误", f"导出热力图失败: {e}")
                import logging
                logging.getLogger(__name__).error(f"KDE Heatmap Export error: {e}", exc_info=True)

        btn_frame = ctk.CTkFrame(param_dlg, fg_color="transparent")
        btn_frame.pack(side=tk.BOTTOM, pady=(15, 25))

        ctk.CTkButton(btn_frame, text="取消", width=90, corner_radius=14, fg_color=("#E5E7EB", "#374151"), text_color=("#374151", "#D1D5DB"), hover_color=("#D1D5DB", "#4B5563"), command=param_dlg.destroy).pack(side=tk.LEFT, padx=10)
        ctk.CTkButton(btn_frame, text="确定并选择路径", width=120, corner_radius=14, command=on_confirm).pack(side=tk.LEFT, padx=10)

    def _draw_kde_heatmap(self, group_norm_points, max_syls, out_file, prog_dlg, pbar, lbl_status, bw_method=0.15, cancel_event=None):
        import math
        from scipy.stats import gaussian_kde
        plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'Arial Unicode MS']
        plt.rcParams['axes.unicode_minus'] = False

        groups_with_data = [g for g in group_norm_points.keys() if len(group_norm_points[g][0]) > 0]
        n_groups = len(groups_with_data)
        if n_groups == 0:
            prog_dlg.destroy()
            return messagebox.showwarning("提示", "没有有效数据可供绘制热力图。")

        n_cols = min(2, n_groups)
        n_rows = math.ceil(n_groups / n_cols)
        fig, axes = plt.subplots(n_rows, n_cols, figsize=(5 * max_syls * n_cols, 5 * n_rows), squeeze=False, sharex=True, sharey=True)
        axes_flat = axes.flatten()

        for idx, grp_name in enumerate(groups_with_data):
            if cancel_event is not None and cancel_event.is_set():
                prog_dlg.destroy()
                return False
            lbl_status.configure(text=f"正在绘制 {grp_name} ({idx+1}/{n_groups})...")
            pbar.set(0.8 + 0.2 * (idx / n_groups))
            prog_dlg.update()

            ax = axes_flat[idx]
            X_all, Y_all = group_norm_points[grp_name]

            xmin, xmax = 0, max_syls * 100
            ymin, ymax = -1, 6

            x_arr = np.asarray(X_all, dtype=float)
            y_arr = np.asarray(Y_all, dtype=float)
            max_kde_points = 12000
            if len(x_arr) > max_kde_points:
                sample_idx = np.linspace(0, len(x_arr) - 1, max_kde_points, dtype=int)
                x_arr = x_arr[sample_idx]
                y_arr = y_arr[sample_idx]
            positions = np.vstack([x_arr, y_arr])
            try:
                kernel = gaussian_kde(positions, bw_method=bw_method)
                grid_x = max(120, min(240, int(80 * max_syls)))
                grid_y = 90
                xi, yi = np.mgrid[xmin:xmax:complex(0, grid_x), ymin:ymax:complex(0, grid_y)]
                zi = kernel(np.vstack([xi.flatten(), yi.flatten()]))
                zi = zi.reshape(xi.shape)

                vmax = zi.max()
                if vmax > 0:
                    levels = np.linspace(vmax * 0.05, vmax, 30)
                    ax.contourf(xi, yi, zi, levels=levels, cmap="YlOrRd", extend='neither')
            except Exception as e:
                import logging
                logging.getLogger(__name__).error(f"KDE drawing failed for {grp_name}: {e}")

            for k in range(1, max_syls):
                ax.axvline(k * 100, color='gray', linestyle='--', alpha=0.8)

            ax.set_title(grp_name, fontsize=16)
            ax.set_ylim(-1, 6)
            ax.set_xlim(0, max_syls * 100)
            ax.set_yticks([-1, 0, 1, 2, 3, 4, 5, 6])

            ticks, labels = [], []
            for k in range(max_syls):
                ticks.append(k * 100 + 50)
                labels.append(f"第 {k+1} 字\n(0-100%)")
            ax.set_xticks(ticks)
            ax.set_xticklabels(labels)

            if idx % n_cols == 0: ax.set_ylabel('T 值', fontsize=12)

        for idx in range(n_groups, len(axes_flat)): axes_flat[idx].set_visible(False)

        fig.suptitle('词语时序密度热力图 (连读变调)', fontsize=20, fontweight='bold', y=1.05)
        fig.tight_layout()
        fig.savefig(out_file, dpi=300, bbox_inches='tight')
        plt.close(fig)
        prog_dlg.destroy()
        return True

    def _export_kde_heatmap(self, out_file, tree_structure=None, params=None):
        import os

        if tree_structure is None:
            tree_structure = self._get_all_items_by_group()

        N_DENSE = 100
        max_syls = 1
        aggregated_syl_contours = {}

        prog_dlg = ctk.CTkToplevel(self.parent)
        prog_dlg.title("正在导出热力图")
        prog_dlg.configure(fg_color=("#FFFFFF", "#1A1D24"))
        prog_dlg.geometry("300x130")
        prog_dlg.attributes('-topmost', True)
        prog_dlg.resizable(False, False)
        prog_dlg.update_idletasks()
        main_win = self.parent.winfo_toplevel()
        prog_dlg.geometry(f"+{main_win.winfo_rootx() + (main_win.winfo_width() - 300) // 2}+{main_win.winfo_rooty() + (main_win.winfo_height() - 130) // 2}")

        lbl_status = ctk.CTkLabel(prog_dlg, text="正在处理数据，请稍候...", font=self.font_main, text_color=("#374151", "#E5E7EB"))
        lbl_status.pack(pady=(20, 5))
        pbar = ctk.CTkProgressBar(prog_dlg, width=250)
        pbar.pack()
        pbar.set(0)
        cancel_event = threading.Event()
        ctk.CTkButton(prog_dlg, text="取消", width=80, corner_radius=14, fg_color=("#E5E7EB", "#374151"), text_color=("#374151", "#D1D5DB"), hover_color=("#D1D5DB", "#4B5563"), command=cancel_event.set).pack(pady=(8, 25))
        prog_dlg.update()

        pbar.set(0.2)
        prog_dlg.update()

        speaker_contours = {}
        for grp_name, children in tree_structure:
            if cancel_event.is_set():
                prog_dlg.destroy()
                return False
            speaker_contours[grp_name] = {}
            for child in children:
                if cancel_event.is_set():
                    prog_dlg.destroy()
                    return False
                item = self.items[child]
                syls, bounds = self._get_syllables_and_bounds(item)
                if syls:
                    max_syls = max(max_syls, len(syls))
                self._ensure_item_loaded(item)
                if item.get('start') is None or not item.get('snd') or (not item.get('pitch') and not item.get('pitch_data')):
                    continue

                if item.get('pitch_data'):
                    p_xs = item['pitch_data']['xs']
                    p_freqs = item['pitch_data']['freqs']
                else:
                    pitch = item['pitch']
                    p_xs = pitch.xs()
                    p_freqs = pitch.selected_array['frequency']

                for k, (c_s, c_e) in enumerate(bounds):
                    y_dense = self._extract_kde_contour(p_xs, p_freqs, c_s, c_e, N_DENSE)
                    if y_dense is None:
                        continue
                    if k not in speaker_contours[grp_name]: speaker_contours[grp_name][k] = []
                    speaker_contours[grp_name][k].append(y_dense)

        all_mean_vals = []
        for name, syls_dict in speaker_contours.items():
            for k, y_arrays in syls_dict.items():
                if y_arrays:
                    finite_vals = np.asarray(y_arrays, dtype=float).ravel()
                    finite_vals = finite_vals[np.isfinite(finite_vals)]
                    all_mean_vals.extend(finite_vals.tolist())
        if all_mean_vals:
            if params:
                f0_mode = params.get('f0_mode', 'minmax')
                p_low = params.get('percentile_low', 5.0)
                p_high = params.get('percentile_high', 95.0)
                m_min = params.get('manual_min', 75.0)
                m_max = params.get('manual_max', 600.0)
            else:
                f0_mode = 'minmax'
                p_low, p_high, m_min, m_max = 5.0, 95.0, 75.0, 600.0

            if f0_mode == 'percentile':
                min_f0 = np.percentile(all_mean_vals, p_low)
                max_f0 = np.percentile(all_mean_vals, p_high)
            elif f0_mode == 'manual':
                min_f0 = m_min
                max_f0 = m_max
            else:
                min_f0 = min(all_mean_vals)
                max_f0 = max(all_mean_vals)

            def hz_to_5_scale_s(hz):
                if max_f0 == min_f0: return 3.0
                hz_val = np.clip(hz, min_f0, max_f0) if min_f0 > 0 else hz
                if min_f0 <= 0 or max_f0 <= min_f0: return 3.0
                return 5 * (np.log(hz_val) - np.log(min_f0)) / (np.log(max_f0) - np.log(min_f0))

            for name, syls_dict in speaker_contours.items():
                if name not in aggregated_syl_contours: aggregated_syl_contours[name] = {}
                for k, y_arrays in syls_dict.items():
                    if k not in aggregated_syl_contours[name]: aggregated_syl_contours[name][k] = []
                    for y_arr in y_arrays:
                        t_arr = [hz_to_5_scale_s(h) for h in y_arr]
                        aggregated_syl_contours[name][k].append(t_arr)

        pbar.set(0.7)
        lbl_status.configure(text="正在汇总数据...")
        prog_dlg.update()

        group_norm_points = {}
        for name, syls_dict in aggregated_syl_contours.items():
            if cancel_event.is_set():
                prog_dlg.destroy()
                return False
            X_all, Y_all = [], []
            for k, y_arrays in syls_dict.items():
                x_dense = np.linspace(k * 100, (k + 1) * 100, N_DENSE)
                for y_arr in y_arrays:
                    y_arr = np.asarray(y_arr, dtype=float)
                    valid = np.isfinite(y_arr)
                    X_all.extend(x_dense[valid].tolist())
                    Y_all.extend(y_arr[valid].tolist())
            group_norm_points[name] = (np.array(X_all), np.array(Y_all))

        pbar.set(0.8)
        prog_dlg.update()

        bw_method = params.get('bw_method', 0.15) if params else 0.15
        return self._draw_kde_heatmap(group_norm_points, max_syls, out_file, prog_dlg, pbar, lbl_status, bw_method=bw_method, cancel_event=cancel_event)

    def _export_kde_heatmap_integrated(self, out_file, all_speakers, params=None):
        N_DENSE = 100
        max_syls = 1
        aggregated_syl_contours = {}

        prog_dlg = ctk.CTkToplevel(self.parent)
        prog_dlg.title("正在导出整合热力图")
        prog_dlg.configure(fg_color=("#FFFFFF", "#1A1D24"))
        prog_dlg.geometry("300x130")
        prog_dlg.attributes('-topmost', True)
        prog_dlg.resizable(False, False)
        prog_dlg.update_idletasks()
        main_win = self.parent.winfo_toplevel()
        prog_dlg.geometry(f"+{main_win.winfo_rootx() + (main_win.winfo_width() - 300) // 2}+{main_win.winfo_rooty() + (main_win.winfo_height() - 130) // 2}")

        lbl_status = ctk.CTkLabel(prog_dlg, text="正在处理数据，请稍候...", font=self.font_main, text_color=("#374151", "#E5E7EB"))
        lbl_status.pack(pady=(20, 5))
        pbar = ctk.CTkProgressBar(prog_dlg, width=250)
        pbar.pack()
        pbar.set(0)
        cancel_event = threading.Event()
        ctk.CTkButton(prog_dlg, text="取消", width=80, corner_radius=14, fg_color=("#E5E7EB", "#374151"), text_color=("#374151", "#D1D5DB"), hover_color=("#D1D5DB", "#4B5563"), command=cancel_event.set).pack(pady=(8, 25))
        prog_dlg.update()

        total_speakers = len(all_speakers)
        for s_idx, speaker in enumerate(all_speakers):
            if cancel_event.is_set():
                prog_dlg.destroy()
                return False
            lbl_status.configure(text=f"正在处理 {speaker.name} ({s_idx+1}/{total_speakers})...")
            pbar.set(0.6 * (s_idx / total_speakers))
            prog_dlg.update()

            s_struct = self._get_items_by_group_for_dict(speaker.items)
            orig_items = self.items
            self.items = speaker.items

            speaker_contours = {}
            for grp_name, children in s_struct:
                if cancel_event.is_set():
                    self.items = orig_items
                    prog_dlg.destroy()
                    return False
                speaker_contours[grp_name] = {}
                for child in children:
                    if cancel_event.is_set():
                        self.items = orig_items
                        prog_dlg.destroy()
                        return False
                    item = self.items[child]
                    syls, bounds = self._get_syllables_and_bounds(item)
                    if syls:
                        max_syls = max(max_syls, len(syls))
                    self._ensure_item_loaded(item)
                    if item.get('start') is None or not item.get('snd') or (not item.get('pitch') and not item.get('pitch_data')):
                        continue

                    if item.get('pitch_data'):
                        p_xs = item['pitch_data']['xs']
                        p_freqs = item['pitch_data']['freqs']
                    else:
                        pitch = item['pitch']
                        p_xs = pitch.xs()
                        p_freqs = pitch.selected_array['frequency']

                    for k, (c_s, c_e) in enumerate(bounds):
                        y_dense = self._extract_kde_contour(p_xs, p_freqs, c_s, c_e, N_DENSE)
                        if y_dense is None:
                            continue
                        if k not in speaker_contours[grp_name]: speaker_contours[grp_name][k] = []
                        speaker_contours[grp_name][k].append(y_dense)

            for name, syls_dict in speaker_contours.items():
                if name not in aggregated_syl_contours: aggregated_syl_contours[name] = {}
                for k, y_arrays in syls_dict.items():
                    if k not in aggregated_syl_contours[name]: aggregated_syl_contours[name][k] = []
                    aggregated_syl_contours[name][k].extend(y_arrays)
            self.items = orig_items

        pbar.set(0.7)
        lbl_status.configure(text="正在汇总数据...")
        prog_dlg.update()

        all_hz_vals = []
        for syls_dict in aggregated_syl_contours.values():
            for y_arrays in syls_dict.values():
                finite_vals = np.asarray(y_arrays, dtype=float).ravel()
                finite_vals = finite_vals[np.isfinite(finite_vals)]
                all_hz_vals.extend(finite_vals.tolist())

        if all_hz_vals:
            if params:
                f0_mode = params.get('f0_mode', 'minmax')
                p_low = params.get('percentile_low', 5.0)
                p_high = params.get('percentile_high', 95.0)
                m_min = params.get('manual_min', 75.0)
                m_max = params.get('manual_max', 600.0)
            else:
                f0_mode = 'minmax'
                p_low, p_high, m_min, m_max = 5.0, 95.0, 75.0, 600.0

            if f0_mode == 'percentile':
                min_f0 = np.percentile(all_hz_vals, p_low)
                max_f0 = np.percentile(all_hz_vals, p_high)
            elif f0_mode == 'manual':
                min_f0 = m_min
                max_f0 = m_max
            else:
                min_f0 = min(all_hz_vals)
                max_f0 = max(all_hz_vals)

            for name, syls_dict in aggregated_syl_contours.items():
                for k, y_arrays in syls_dict.items():
                    normalized_arrays = []
                    for y_arr in y_arrays:
                        y_arr = np.asarray(y_arr, dtype=float)
                        t_arr = np.full_like(y_arr, np.nan, dtype=float)
                        valid = np.isfinite(y_arr)
                        if min_f0 > 0 and max_f0 > min_f0 and np.any(valid):
                            hz_vals = np.clip(y_arr[valid], min_f0, max_f0)
                            t_arr[valid] = 5 * (np.log(hz_vals) - np.log(min_f0)) / (np.log(max_f0) - np.log(min_f0))
                        elif np.any(valid):
                            t_arr[valid] = 3.0
                        normalized_arrays.append(t_arr)
                    syls_dict[k] = normalized_arrays

        group_norm_points = {}
        for name, syls_dict in aggregated_syl_contours.items():
            if cancel_event.is_set():
                prog_dlg.destroy()
                return False
            X_all, Y_all = [], []
            for k, y_arrays in syls_dict.items():
                x_dense = np.linspace(k * 100, (k + 1) * 100, N_DENSE)
                for y_arr in y_arrays:
                    y_arr = np.asarray(y_arr, dtype=float)
                    valid = np.isfinite(y_arr)
                    X_all.extend(x_dense[valid].tolist())
                    Y_all.extend(y_arr[valid].tolist())
            group_norm_points[name] = (np.array(X_all), np.array(Y_all))

        pbar.set(0.8)
        prog_dlg.update()

        bw_method = params.get('bw_method', 0.15) if params else 0.15
        return self._draw_kde_heatmap(group_norm_points, max_syls, out_file, prog_dlg, pbar, lbl_status, bw_method=bw_method, cancel_event=cancel_event)


    def _export_textgrid_long(self, out_file, tree_structure=None):
        import textgrid
        if tree_structure is None: tree_structure = self._get_all_items_by_group()

        max_time = 0
        if self.app and getattr(self.app, 'pending_long_snd', None):
            try:
                max_time = self.app.pending_long_snd.get_total_duration()
            except Exception:
                pass

        for grp_name, children in tree_structure:
            for child in children:
                item = self.items[child]
                if item.get('end') is not None and item['end'] > max_time:
                    max_time = item['end']

        if max_time == 0:
            max_time = 1.0 # default if empty

        tg = textgrid.TextGrid(maxTime=max_time)
        word_tier = textgrid.IntervalTier(name="words", minTime=0.0, maxTime=max_time)
        char_tier = textgrid.IntervalTier(name="chars", minTime=0.0, maxTime=max_time)
        group_tier = textgrid.IntervalTier(name="groups", minTime=0.0, maxTime=max_time)

        has_chars = False

        last_word_end = 0.0
        last_char_end = 0.0
        last_group_end = 0.0

        flat_items = []
        item_to_group = {}
        for grp_name, children in tree_structure:
            for child in children:
                item = self.items[child]
                if item.get('start') is not None and item.get('end') is not None:
                    flat_items.append(item)
                    item_to_group[id(item)] = grp_name

        flat_items.sort(key=lambda x: x['start'])

        for item in flat_items:
            t_s, t_e = item['start'], item['end']
            t_s = min(t_s, max_time)
            t_e = min(t_e, max_time)
            label = item.get('label', '')
            inner_splits = item.get('inner_splits', [])
            grp_name = item_to_group.get(id(item), "导入内容")

            # Word tier
            w_s = max(t_s, last_word_end)
            if t_e > w_s:
                if w_s > last_word_end:
                    word_tier.add(last_word_end, w_s, "")
                word_tier.add(w_s, t_e, label)
                last_word_end = t_e

            # Group tier
            g_s = max(t_s, last_group_end)
            if t_e > g_s:
                if g_s > last_group_end:
                    group_tier.add(last_group_end, g_s, "")
                group_tier.add(g_s, t_e, grp_name)
                last_group_end = t_e

            # Char tier
            syls = split_into_syllables(label)
            if len(syls) > 1:
                has_chars = True

                c_s_start = max(t_s, last_char_end)
                if c_s_start > last_char_end:
                    char_tier.add(last_char_end, c_s_start, "")

                chars_bounds = item.get('chars_bounds', [])
                if not chars_bounds:
                    import numpy as np
                    splits = [t_s] + [s for s in inner_splits if t_s < s < t_e] + [t_e]
                    if len(splits) != len(syls) + 1:
                        splits = np.linspace(t_s, t_e, len(syls) + 1).tolist()
                    chars_bounds = [(splits[j], splits[j+1]) for j in range(len(splits)-1)]

                local_last = c_s_start
                for i in range(len(syls)):
                    if i < len(chars_bounds):
                        c_s, c_e = chars_bounds[i]
                        c_s = min(c_s, max_time)
                        c_e = min(c_e, max_time)
                        c_s = max(c_s, local_last)
                        if c_e > c_s:
                            char_tier.add(c_s, c_e, syls[i])
                            local_last = c_e

                if local_last < t_e:
                    char_tier.add(local_last, t_e, "")
                    local_last = t_e
                last_char_end = local_last
            else:
                c_s = max(t_s, last_char_end)
                if t_e > c_s:
                    if c_s > last_char_end:
                        char_tier.add(last_char_end, c_s, "")
                    char_tier.add(c_s, t_e, label)
                    last_char_end = t_e

        if max_time > last_word_end:
            word_tier.add(last_word_end, max_time, "")
        if max_time > last_char_end:
            char_tier.add(last_char_end, max_time, "")
        if max_time > last_group_end:
            group_tier.add(last_group_end, max_time, "")

        tg.append(group_tier)
        tg.append(word_tier)
        if has_chars:
            tg.append(char_tier)

        tg.write(out_file)

    def _export_textgrid_batch(self, out_dir, tree_structure=None):
        import textgrid
        import os
        if tree_structure is None: tree_structure = self._get_all_items_by_group()

        # Group items by source file path
        path_to_items = {}
        item_to_group = {}
        for grp_name, children in tree_structure:
            for child in children:
                item = self.items[child]
                item_to_group[id(item)] = grp_name
                if item.get('path'):
                    path = item['path']
                    if path not in path_to_items:
                        path_to_items[path] = []
                    path_to_items[path].append(item)

        out_subdir = os.path.join(out_dir, "Textgrid_export")
        os.makedirs(out_subdir, exist_ok=True)

        used_stems = {}
        for path, items in path_to_items.items():
            base_name = make_textgrid_export_stem(path, items[0].get('label', '') if items else '')
            stem_count = used_stems.get(base_name, 0) + 1
            used_stems[base_name] = stem_count
            if stem_count > 1:
                base_name = f"{base_name}_{stem_count}"
            tg_path = os.path.join(out_subdir, f"{base_name}.TextGrid")

            # Since it's batch mode, usually each item corresponds to the full file.
            # but if multiple items share the same file, we'll combine them based on their time ranges
            max_time = 0
            for item in items:
                # If batch mode, the audio length might be available via snd object
                if item.get('snd'):
                    dur = item['snd'].get_total_duration()
                    if dur > max_time: max_time = dur
                if item.get('end') is not None and item['end'] > max_time:
                    max_time = item['end']

            if max_time == 0: max_time = 1.0

            tg = textgrid.TextGrid(maxTime=max_time)
            word_tier = textgrid.IntervalTier(name="words", minTime=0.0, maxTime=max_time)
            char_tier = textgrid.IntervalTier(name="chars", minTime=0.0, maxTime=max_time)
            group_tier = textgrid.IntervalTier(name="groups", minTime=0.0, maxTime=max_time)

            items.sort(key=lambda x: x.get('start', 0))

            last_word_end = 0.0
            last_char_end = 0.0
            last_group_end = 0.0
            has_chars = False

            for item in items:
                if item.get('start') is None or item.get('end') is None: continue
                t_s, t_e = item['start'], item['end']
                t_s = min(t_s, max_time)
                t_e = min(t_e, max_time)
                label = item.get('label', '')
                inner_splits = item.get('inner_splits', [])
                grp_name = item_to_group.get(id(item), "导入内容")

                # Word tier
                w_s = max(t_s, last_word_end)
                if t_e > w_s:
                    if w_s > last_word_end:
                        word_tier.add(last_word_end, w_s, "")
                    word_tier.add(w_s, t_e, label)
                    last_word_end = t_e

                # Group tier
                g_s = max(t_s, last_group_end)
                if t_e > g_s:
                    if g_s > last_group_end:
                        group_tier.add(last_group_end, g_s, "")
                    group_tier.add(g_s, t_e, grp_name)
                    last_group_end = t_e

                # Char tier
                syls = split_into_syllables(label)
                if len(syls) > 1:
                    has_chars = True

                    c_s_start = max(t_s, last_char_end)
                    if c_s_start > last_char_end:
                        char_tier.add(last_char_end, c_s_start, "")

                    chars_bounds = item.get('chars_bounds', [])
                    if not chars_bounds:
                        import numpy as np
                        splits = [t_s] + [s for s in inner_splits if t_s < s < t_e] + [t_e]
                        if len(splits) != len(syls) + 1:
                            splits = np.linspace(t_s, t_e, len(syls) + 1).tolist()
                        chars_bounds = [(splits[j], splits[j+1]) for j in range(len(splits)-1)]

                    local_last = c_s_start
                    for i in range(len(syls)):
                        if i < len(chars_bounds):
                            c_s, c_e = chars_bounds[i]
                            c_s = min(c_s, max_time)
                            c_e = min(c_e, max_time)
                            c_s = max(c_s, local_last)
                            if c_e > c_s:
                                char_tier.add(c_s, c_e, syls[i])
                                local_last = c_e

                    if local_last < t_e:
                        char_tier.add(local_last, t_e, "")
                        local_last = t_e
                    last_char_end = local_last
                else:
                    c_s = max(t_s, last_char_end)
                    if t_e > c_s:
                        if c_s > last_char_end:
                            char_tier.add(last_char_end, c_s, "")
                        char_tier.add(c_s, t_e, label)
                        last_char_end = t_e

            if max_time > last_word_end:
                word_tier.add(last_word_end, max_time, "")
            if max_time > last_char_end:
                char_tier.add(last_char_end, max_time, "")
            if max_time > last_group_end:
                group_tier.add(last_group_end, max_time, "")

            tg.append(group_tier)
            tg.append(word_tier)
            if has_chars:
                tg.append(char_tier)

            tg.write(tg_path)

    def _export_praat_collection(self, out_file, tree_structure=None, speaker=None):
        from modules.export_service import export_speaker_to_praat_collection
        if tree_structure is None:
            tree_structure = self._get_all_items_by_group()

        is_batch = False
        if speaker is not None and getattr(speaker, 'tab_mode', None):
            is_batch = (speaker.tab_mode == "多条独立音频")
        elif self.app and hasattr(self.app, 'tabview'):
            is_batch = (self.app.tabview.get() == "多条独立音频")
        elif hasattr(self.parent, 'tabview'):
            is_batch = (self.parent.tabview.get() == "多条独立音频")

        long_snd = None
        if speaker is not None and getattr(speaker, 'pending_long_snd', None) is not None:
            long_snd = speaker.pending_long_snd
        elif self.app:
            long_snd = getattr(self.app, 'long_sound', None) or getattr(self.app, 'pending_long_snd', None)

        default_ceiling = float(self.app_state_params.get('pitch_ceiling', 600.0))
        spk_name = getattr(speaker, 'name', '') if speaker is not None else ''
        export_speaker_to_praat_collection(
            out_path=out_file,
            items_dict=self.items,
            tree_structure=tree_structure,
            is_batch_mode=is_batch,
            long_snd=long_snd,
            speaker_name=spk_name,
            ensure_loaded_fn=self._ensure_item_loaded,
            default_ceiling=default_ceiling,
            binary=True,
        )

    def _export_praat_collection_integrated(self, out_file, all_speakers):
        from modules.export_service import build_speaker_collection_objects, write_praat_collection
        all_objects = []
        default_ceiling = float(self.app_state_params.get('pitch_ceiling', 600.0))
        orig_items = self.items
        try:
            for s in all_speakers:
                self.items = s.items
                s_struct = self._get_items_by_group_for_dict(s.items)
                is_batch = (getattr(s, 'tab_mode', None) == "多条独立音频")
                long_snd = getattr(s, 'pending_long_snd', None)
                if long_snd is None and self.app:
                    long_snd = getattr(self.app, 'long_sound', None) or getattr(self.app, 'pending_long_snd', None)
                objs = build_speaker_collection_objects(
                    items_dict=s.items,
                    tree_structure=s_struct,
                    is_batch_mode=is_batch,
                    long_snd=long_snd,
                    speaker_name=s.name,
                    ensure_loaded_fn=self._ensure_item_loaded,
                    default_ceiling=default_ceiling,
                )
                all_objects.extend(objs)
        finally:
            self.items = orig_items

        write_praat_collection(out_file, all_objects, binary=True)

    def _export_single_item_collection(self, real_iid):
        from modules.export_service import (
            build_single_item_collection_objects,
            clean_praat_object_name,
            write_praat_collection,
        )
        if self.app and hasattr(self.app, 'flush_eraser_changes'):
            self.app.flush_eraser_changes()
        item = self.items.get(real_iid)
        if not item:
            return
        self._ensure_item_loaded(item)
        default_name = f"{clean_praat_object_name(item.get('label', 'item'))}.Collection"
        out_path = filedialog.asksaveasfilename(
            title="导出此项为 Praat 集合 (.Collection)",
            defaultextension=".Collection",
            initialfile=default_name,
            filetypes=[("Praat 集合文件 (*.Collection)", "*.Collection"), ("所有文件 (*.*)", "*.*")],
        )
        if not out_path:
            return
        try:
            long_snd = getattr(self.app, 'long_sound', None) or getattr(self.app, 'pending_long_snd', None) if self.app else None
            default_ceiling = float(item.get('pitch_ceiling', self.app_state_params.get('pitch_ceiling', 600.0)))
            objs = build_single_item_collection_objects(item, long_snd=long_snd, default_ceiling=default_ceiling)
            write_praat_collection(out_path, objs, binary=True)
            messagebox.showinfo(
                "成功",
                f"词条【{item.get('label', '')}】已导出为 Praat 集合:\n{out_path}\n\n在 Praat 中打开即可查看其音频、三层标注与修补后的基频/共振峰。",
            )
        except Exception as e:
            messagebox.showerror("错误", f"导出 Praat 集合失败:\n{e}")
            logger.error(f"Single item collection export failed: {e}", exc_info=True)

    def _show_formant_export_menu(self, mode='single', all_speakers=None):
        dlg = ctk.CTkToplevel(self.parent)
        dlg.title("PhonTracer语音学标注与声学分析系统 V1.0 - 选择共振峰导出内容")
        dlg.configure(fg_color=("#FFFFFF", "#1A1D24"))
        dlg.geometry("320x300")
        dlg.resizable(False, False)
        dlg.transient(self.parent)
        dlg.grab_set()
        dlg.update_idletasks()
        main_win = self.parent.winfo_toplevel()
        x = main_win.winfo_rootx() + (main_win.winfo_width() - 320) // 2
        y = main_win.winfo_rooty() + (main_win.winfo_height() - 300) // 2
        dlg.geometry(f"+{x}+{y}")

        ctk.CTkLabel(dlg, text="请选择共振峰导出内容", font=self.font_title, text_color=("#111827", "#F9FAFB")).pack(pady=(20, 15))
        btn_kwargs = {"corner_radius": 8, "height": 44, "font": self.font_main, "anchor": "w", "compound": "left", "border_width": 1.5}

        btn_table = CTkReleaseButton(
            dlg,
            text=" 导出共振峰数据表 (.xlsx)",
            image=self.icons.get("excel"),
            command=lambda: [dlg.destroy(), self._export_formant_table_dialog(all_speakers)],
            fg_color="#FFFFFF",
            text_color="#10B981",
            hover_color="#F0FDF4",
            border_color="#10B981",
            **btn_kwargs
        )
        btn_table.pack(fill=tk.X, padx=30, pady=8)

        btn_space = CTkReleaseButton(
            dlg,
            text=" 共振峰可视化工具箱...",
            image=self.icons.get("chart"),
            command=lambda: [dlg.destroy(), self._open_formant_visualization_dialog(mode, all_speakers)],
            fg_color="#FFFFFF",
            text_color="#3B82F6",
            hover_color="#EFF6FF",
            border_color="#3B82F6",
            **btn_kwargs
        )
        btn_space.pack(fill=tk.X, padx=30, pady=8)

        btn_cancel = ctk.CTkButton(
            dlg,
            text="取消",
            height=36,
            corner_radius=18,
            fg_color=("#F3F4F6", "#374151"),
            text_color=("#4B5563", "#D1D5DB"),
            hover_color=("#E5E7EB", "#4B5563"),
            border_width=1,
            border_color=("#D1D5DB", "#475569"),
            font=self.font_main,
            command=dlg.destroy
        )
        btn_cancel.pack(fill=tk.X, padx=30, pady=(15, 30))

    def _export_formant_table_dialog(self, all_speakers):
        path = filedialog.asksaveasfilename(
            defaultextension=".xlsx",
            filetypes=[("Excel Spreadsheet", "*.xlsx")],
            title="保存共振峰数据表"
        )
        if path:
            self._export_formant_table(path, all_speakers)

    def _open_formant_visualization_dialog(self, mode, all_speakers):
        existing_dialog = getattr(self.app, 'active_chart_dialog', None)
        if existing_dialog is not None:
            try:
                if existing_dialog.winfo_exists():
                    existing_dialog.deiconify()
                    existing_dialog.deiconify()
                    existing_dialog.lift()
                    existing_dialog.focus_force()
                    return
            except Exception:
                self.app.active_chart_dialog = None

        from modules.acoustic_exporter import AcousticChartExportDialog
        AcousticChartExportDialog(self.parent, app=self.app, project_tree=self, mode=mode, all_speakers=all_speakers)

    def _export_formant_table(self, out_file, all_speakers=None, include_chart=False, show_popup=True):
        try:
            import xlsxwriter
        except ImportError:
            messagebox.showerror("错误", "缺少 xlsxwriter 库，请先安装：pip install xlsxwriter")
            return

        if all_speakers is None:
            all_speakers = [self.app.speaker_manager.get_active_speaker()]

        workbook = xlsxwriter.Workbook(out_file)
        from modules.data_utils import ExcelStyleManager, autofit_worksheet_columns
        style = ExcelStyleManager(workbook)

        ws_data = workbook.add_worksheet("提取数据")
        ws_analysis = workbook.add_worksheet("分析图表")
        ws_raw = workbook.add_worksheet("原始数据")

        # ══════════ Sheet 1: 提取数据 ══════════
        show_f3 = bool(self.app.last_params.get("show_f3", False))
        data_headers = ["发音人", "组别", "编号", "词语", "音节序号", "音节", "时间点序号", "时间(s)", "F1(Hz)", "F2(Hz)"]
        if show_f3:
            data_headers.append("F3(Hz)")
        for col, h in enumerate(data_headers):
            ws_data.write(0, col, h, style.header_fmt)

        data_row = 1
        formant_sample_rows = []
        group_char_pairs = []  # 有序的 (组别, 音节) 唯一列表 —— 用于分析图表页
        seen_pairs = set()
        raw_formant_entries = []  # 收集原始帧数据入口信息

        for spk in all_speakers:
            is_continuous = (self.num_rule_var.get() == "continuous")
            pts = int(self.app_state_params.get('pts', 11))
            strategy = self.app_state_params.get('formant_sample_strategy', '整段11点')

            groups = {}
            for item_id, item in spk.items.items():
                if item.get('is_excluded', False):
                    continue
                g = item.get('group', '导入内容')
                if g not in groups:
                    groups[g] = []
                groups[g].append(item)

            from modules.data_utils import clean_str, get_item_syllable_bounds, sample_formant_points_by_bounds, split_into_syllables
            sorted_groups = sorted(groups.keys(), key=clean_str)

            global_idx = 1
            for grp_name in sorted_groups:
                if not is_continuous:
                    global_idx = 1
                items_in_grp = groups[grp_name]
                items_in_grp = sorted(items_in_grp, key=lambda x: clean_str(x.get('label', '')))

                for item in items_in_grp:
                    self._ensure_item_loaded(item)
                    if not item.get('snd') or not item.get('formant_data'):
                        continue

                    bounds = get_item_syllable_bounds(item)
                    syls = split_into_syllables(item.get('label', ''))
                    from modules.data_utils import sample_formant_points_by_bounds_extended
                    res_samp = sample_formant_points_by_bounds_extended(item, bounds, pts, strategy, include_f3=show_f3)
                    preview_times = res_samp["times"]
                    f1_vals = res_samp["f1"]
                    f2_vals = res_samp["f2"]
                    f3_vals = res_samp["f3"]

                    # 记录原始帧数据入口
                    raw_formant_entries.append({
                        'speaker': spk.name,
                        'group': grp_name,
                        'index': global_idx,
                        'item': item,
                    })

                    for idx_syl, (c_s, c_e) in enumerate(bounds):
                        char = syls[idx_syl] if idx_syl < len(syls) else f"字{idx_syl+1}"
                        flat_start = idx_syl * pts

                        # 收集唯一的 (组别, 音节) 对
                        pair_key = (grp_name, char)
                        if pair_key not in seen_pairs:
                            seen_pairs.add(pair_key)
                            group_char_pairs.append(pair_key)

                        for idx_pt in range(pts):
                            ws_data.write(data_row, 0, spk.name, style.text_center)
                            ws_data.write(data_row, 1, grp_name, style.text_center)
                            ws_data.write(data_row, 2, global_idx, style.int_fmt)
                            ws_data.write(data_row, 3, item.get('label', ''), style.text_left)
                            ws_data.write(data_row, 4, idx_syl + 1, style.int_fmt)
                            ws_data.write(data_row, 5, char, style.text_center)
                            ws_data.write(data_row, 6, idx_pt + 1, style.int_fmt)
                            ws_data.write(data_row, 7, round(preview_times[flat_start + idx_pt], 6), style.duration_fmt)

                            f1_v = f1_vals[flat_start + idx_pt]
                            f2_v = f2_vals[flat_start + idx_pt]

                            # 使用空单元格代替 "--"，以便分析图表页的公式能正确引用
                            if np.isnan(f1_v):
                                ws_data.write(data_row, 8, "", style.text_center)
                            else:
                                ws_data.write(data_row, 8, round(f1_v, 1), style.hz_fmt)

                            if np.isnan(f2_v):
                                ws_data.write(data_row, 9, "", style.text_center)
                            else:
                                ws_data.write(data_row, 9, round(f2_v, 1), style.hz_fmt)

                            sample_r = [
                                spk.name, grp_name, global_idx, item.get('label', ''),
                                idx_syl + 1, char, idx_pt + 1, round(preview_times[flat_start + idx_pt], 6),
                                round(f1_v, 1) if not np.isnan(f1_v) else "",
                                round(f2_v, 1) if not np.isnan(f2_v) else ""
                            ]

                            if show_f3:
                                f3_v = f3_vals[flat_start + idx_pt]
                                if np.isnan(f3_v):
                                    ws_data.write(data_row, 10, "", style.text_center)
                                    sample_r.append("")
                                else:
                                    ws_data.write(data_row, 10, round(f3_v, 1), style.hz_fmt)
                                    sample_r.append(round(f3_v, 1))

                            if len(formant_sample_rows) < 100:
                                formant_sample_rows.append(sample_r)

                            data_row += 1

                    global_idx += 1

        ws_data.freeze_panes(1, 0)
        ws_data.autofilter(0, 0, max(data_row - 1, 1), len(data_headers) - 1)
        autofit_worksheet_columns(ws_data, data_headers, formant_sample_rows)

        # ══════════ Sheet 2: 分析图表（全部通过 Excel 公式引用提取数据表） ══════════
        last_data_row = data_row - 1  # 0-indexed
        write_formant_analysis_sheet_with_formulas(
            workbook, ws_analysis, group_char_pairs, last_data_row,
            data_sheet_name='提取数据',
        )

        # ══════════ Sheet 3: 原始数据（逐帧共振峰帧数据） ══════════
        raw_headers = [
            "发音人", "组别", "编号", "词语", "音节序号", "音节",
            "绝对时间(s)", "字内相对时间(s)", "F1(Hz)", "F2(Hz)"
        ]
        if show_f3:
            raw_headers.append("F3(Hz)")
        raw_headers.append("状态")
        for col, h in enumerate(raw_headers):
            ws_raw.write(0, col, h, style.header_fmt)

        raw_row = 1
        raw_sample_rows = []
        for entry in raw_formant_entries:
            item = entry['item']
            f_data = item.get('formant_data')
            if not f_data or 'xs' not in f_data:
                continue

            xs = f_data['xs']
            f1_arr = f_data.get('f1', np.array([]))
            f2_arr = f_data.get('f2', np.array([]))

            bounds = get_item_syllable_bounds(item)
            syls = split_into_syllables(item.get('label', ''))

            f3_arr = f_data.get('f3') if show_f3 else None
            for syl_idx, (c_s, c_e) in enumerate(bounds):
                char = syls[syl_idx] if syl_idx < len(syls) else f"字{syl_idx+1}"
                mask = (xs >= c_s) & (xs <= c_e)
                indices = np.where(mask)[0]

                for frame_idx in indices:
                    t = float(xs[frame_idx])
                    f1 = float(f1_arr[frame_idx]) if frame_idx < len(f1_arr) and np.isfinite(f1_arr[frame_idx]) else 0.0
                    f2 = float(f2_arr[frame_idx]) if frame_idx < len(f2_arr) and np.isfinite(f2_arr[frame_idx]) else 0.0
                    if show_f3:
                        f3 = float(f3_arr[frame_idx]) if f3_arr is not None and frame_idx < len(f3_arr) and np.isfinite(f3_arr[frame_idx]) else 0.0

                    status = "有效" if (f1 > 0 and f2 > 0 and f2 > f1) else "无效"

                    ws_raw.write(raw_row, 0, entry['speaker'], style.text_center)
                    ws_raw.write(raw_row, 1, entry['group'], style.text_center)
                    ws_raw.write(raw_row, 2, entry['index'], style.int_fmt)
                    ws_raw.write(raw_row, 3, item.get('label', ''), style.text_left)
                    ws_raw.write(raw_row, 4, syl_idx + 1, style.int_fmt)
                    ws_raw.write(raw_row, 5, char, style.text_center)
                    ws_raw.write(raw_row, 6, round(t, 6), style.decimal6_fmt)
                    ws_raw.write(raw_row, 7, round(t - c_s, 6), style.decimal6_fmt)
                    ws_raw.write(raw_row, 8, round(f1, 1) if f1 > 0 else 0.0, style.hz_fmt)
                    ws_raw.write(raw_row, 9, round(f2, 1) if f2 > 0 else 0.0, style.hz_fmt)
                    if show_f3:
                        ws_raw.write(raw_row, 10, round(f3, 1) if f3 > 0 else 0.0, style.hz_fmt)
                        ws_raw.write(raw_row, 11, status, style.text_center)
                    else:
                        ws_raw.write(raw_row, 10, status, style.text_center)

                    if len(raw_sample_rows) < 100:
                        raw_sample_rows.append([
                            entry['speaker'], entry['group'], entry['index'], item.get('label', ''),
                            syl_idx + 1, char, round(t, 6), round(t - c_s, 6),
                            round(f1, 1) if f1 > 0 else 0.0, round(f2, 1) if f2 > 0 else 0.0,
                            *( [round(f3, 1) if f3 > 0 else 0.0] if show_f3 else [] ),
                            status
                        ])
                    raw_row += 1

        ws_raw.freeze_panes(1, 0)
        ws_raw.autofilter(0, 0, max(raw_row - 1, 1), len(raw_headers) - 1)
        autofit_worksheet_columns(ws_raw, raw_headers, raw_sample_rows)

        workbook.close()

        if show_popup:
            messagebox.showinfo("成功", f"数据已成功导出至：\n{out_file}")

    def _export_formant_table_txt(self, out_file, all_speakers=None):
        if all_speakers is None:
            all_speakers = [self.app.speaker_manager.get_active_speaker()]

        show_f3 = bool(self.app.last_params.get("show_f3", False))
        with open(out_file, "w", encoding="utf-8-sig") as f:
            raw_headers = ["发音人", "组别", "编号", "词语", "音节序号", "音节", "时间点序号", "时间(s)", "F1(Hz)", "F2(Hz)"]
            if show_f3:
                raw_headers.append("F3(Hz)")
            f.write("\t".join(raw_headers) + "\n")

            for spk in all_speakers:
                is_continuous = (self.num_rule_var.get() == "continuous")
                pts = int(self.app_state_params.get('pts', 11))
                strategy = self.app_state_params.get('formant_sample_strategy', '整段11点')

                groups = {}
                for item_id, item in spk.items.items():
                    if item.get('is_excluded', False):
                        continue
                    g = item.get('group', '导入内容')
                    if g not in groups:
                        groups[g] = []
                    groups[g].append(item)

                from modules.data_utils import clean_str, get_item_syllable_bounds, sample_formant_points_by_bounds, split_into_syllables
                sorted_groups = sorted(groups.keys(), key=clean_str)

                global_idx = 1
                for grp_name in sorted_groups:
                    if not is_continuous:
                        global_idx = 1
                    items_in_grp = groups[grp_name]
                    items_in_grp = sorted(items_in_grp, key=lambda x: clean_str(x.get('label', '')))

                    for item in items_in_grp:
                        self._ensure_item_loaded(item)
                        if not item.get('snd') or not item.get('formant_data'):
                            continue

                        bounds = get_item_syllable_bounds(item)
                        syls = split_into_syllables(item.get('label', ''))
                        from modules.data_utils import sample_formant_points_by_bounds_extended
                        res_samp = sample_formant_points_by_bounds_extended(item, bounds, pts, strategy, include_f3=show_f3)
                        preview_times = res_samp["times"]
                        f1_vals = res_samp["f1"]
                        f2_vals = res_samp["f2"]
                        f3_vals = res_samp["f3"]

                        for idx_syl, (c_s, c_e) in enumerate(bounds):
                            char = syls[idx_syl] if idx_syl < len(syls) else f"字{idx_syl+1}"
                            flat_start = idx_syl * pts
                            flat_end = flat_start + pts

                            f1_slice = f1_vals[flat_start:flat_end]
                            f2_slice = f2_vals[flat_start:flat_end]
                            f3_slice = f3_vals[flat_start:flat_end] if show_f3 else []

                            for idx_pt in range(pts):
                                f1_v = f1_slice[idx_pt]
                                f2_v = f2_slice[idx_pt]

                                f1_str = "" if np.isnan(f1_v) else f"{f1_v:.1f}"
                                f2_str = "" if np.isnan(f2_v) else f"{f2_v:.1f}"

                                line_parts = [
                                    spk.name,
                                    grp_name,
                                    str(global_idx),
                                    item.get('label', ''),
                                    str(idx_syl + 1),
                                    char,
                                    str(idx_pt + 1),
                                    f"{preview_times[flat_start + idx_pt]:.6f}",
                                    f1_str,
                                    f2_str
                                ]
                                if show_f3:
                                    f3_v = f3_slice[idx_pt]
                                    f3_str = "" if np.isnan(f3_v) else f"{f3_v:.1f}"
                                    line_parts.append(f3_str)
                                f.write("\t".join(line_parts) + "\n")
                        global_idx += 1

