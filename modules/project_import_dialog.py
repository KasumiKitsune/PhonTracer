import os
import queue
import threading
import tkinter as tk
import customtkinter as ctk
from .project_manager import read_project_metadata_from_archive

class ProjectImportPreviewDialog(ctk.CTkToplevel):
    def __init__(self, parent, app, zip_path):
        super().__init__(parent)
        self.parent = parent
        self.app = app
        self.zip_path = zip_path
        
        self.title("导入工程")
        self.resizable(True, True)
        
        self.configure(fg_color=("#FFFFFF", "#1A1D24"))
        
        # Modal setup
        self.transient(parent)
        self.grab_set()
        self.focus_set()
        
        # Top Accent Strip (Blue)
        accent_strip = ctk.CTkFrame(self, height=4, fg_color="#3B82F6", corner_radius=0)
        accent_strip.pack(fill="x", side="top")
        
        self.font_title = ctk.CTkFont(family="Microsoft YaHei", size=18, weight="bold")
        self.font_subtitle = ctk.CTkFont(family="Microsoft YaHei", size=14, weight="bold")
        self.font_main = ctk.CTkFont(family="Microsoft YaHei", size=13)
        self.font_small = ctk.CTkFont(family="Microsoft YaHei", size=12)
        self.font_mono = ctk.CTkFont(family="Consolas", size=12)
        
        self._metadata_queue = queue.Queue()
        self._show_loading_ui()
        threading.Thread(target=self._load_metadata, daemon=True).start()
        self.after(50, self._poll_metadata_result)

    def read_metadata(self, zip_path):
        metadata, _namelist = read_project_metadata_from_archive(zip_path)
        return metadata

    def _show_loading_ui(self):
        self._loading_frame = ctk.CTkFrame(self, fg_color="transparent")
        self._loading_frame.pack(fill="both", expand=True, padx=24, pady=24)
        ctk.CTkLabel(
            self._loading_frame,
            text="正在解析工程文件...",
            font=self.font_subtitle,
            text_color=("#374151", "#E5E7EB")
        ).pack(expand=True)
        self.geometry("460x180")

    def _load_metadata(self):
        try:
            metadata = self.read_metadata(self.zip_path)
            self._metadata_queue.put(("ok", metadata))
        except Exception as e:
            self._metadata_queue.put(("error", str(e)))

    def _poll_metadata_result(self):
        if not self.winfo_exists():
            return
        try:
            status, payload = self._metadata_queue.get_nowait()
        except queue.Empty:
            self.after(50, self._poll_metadata_result)
            return

        if status == "error":
            from tkinter import messagebox
            messagebox.showerror("解析工程失败", f"解析工程文件失败:\n{payload}", parent=self.parent)
            self.destroy()
            return

        self.metadata = payload
        self.is_empty = self.app.is_project_empty()
        self._loading_frame.destroy()
        self.setup_ui()

    def setup_ui(self):
        # Main container
        main_frame = ctk.CTkFrame(self, fg_color="transparent")
        main_frame.pack(fill="both", expand=True, padx=24, pady=(10, 16))
        
        # Title
        lbl_title = ctk.CTkLabel(
            main_frame,
            text="PhonTracer 工程数据导入预览",
            font=self.font_title,
            text_color=("#111827", "#F9FAFB")
        )
        lbl_title.pack(pady=(2, 6))
        
        # Basic information card
        info_card = ctk.CTkFrame(main_frame, fg_color=("#F9FAFB", "#262930"), corner_radius=10, border_width=1, border_color=("#E5E7EB", "#374151"))
        info_card.pack(fill="x", padx=5, pady=3)
        
        filename = os.path.basename(self.zip_path)
        version = self.metadata.get("version", "1.0")
        speakers = self.metadata.get("speakers", {})
        speaker_count = len(speakers)
        
        total_items = 0
        for spk in speakers.values():
            total_items += len(spk.get("items", {}))
            
        info_card.grid_columnconfigure((0, 1, 2, 3), weight=1)
        
        # Row 0: File Name
        lbl_fn_title = ctk.CTkLabel(info_card, text="工程名称: ", font=self.font_small, text_color=("#6B7280", "#9CA3AF"))
        lbl_fn_title.grid(row=0, column=0, padx=(15, 5), pady=8, sticky="w")
        lbl_fn_val = ctk.CTkLabel(info_card, text=filename, font=self.font_subtitle, text_color=("#111827", "#F9FAFB"))
        lbl_fn_val.grid(row=0, column=1, columnspan=3, padx=5, pady=8, sticky="w")
        
        # Row 1: Version and Speaker Count
        lbl_ver_title = ctk.CTkLabel(info_card, text="工程版本: ", font=self.font_small, text_color=("#6B7280", "#9CA3AF"))
        lbl_ver_title.grid(row=1, column=0, padx=(15, 5), pady=4, sticky="w")
        lbl_ver_val = ctk.CTkLabel(info_card, text=version, font=self.font_mono, text_color=("#374151", "#E5E7EB"))
        lbl_ver_val.grid(row=1, column=1, padx=5, pady=4, sticky="w")
        
        lbl_spk_title = ctk.CTkLabel(info_card, text="发音人总数: ", font=self.font_small, text_color=("#6B7280", "#9CA3AF"))
        lbl_spk_title.grid(row=1, column=2, padx=(15, 5), pady=4, sticky="w")
        lbl_spk_val = ctk.CTkLabel(info_card, text=str(speaker_count), font=self.font_mono, text_color=("#374151", "#E5E7EB"))
        lbl_spk_val.grid(row=1, column=3, padx=5, pady=4, sticky="w")
        
        # Row 2: Total Items Count
        lbl_items_title = ctk.CTkLabel(info_card, text="已析出项数: ", font=self.font_small, text_color=("#6B7280", "#9CA3AF"))
        lbl_items_title.grid(row=2, column=0, padx=(15, 5), pady=(4, 10), sticky="w")
        lbl_items_val = ctk.CTkLabel(info_card, text=str(total_items), font=self.font_mono, text_color=("#374151", "#E5E7EB"))
        lbl_items_val.grid(row=2, column=1, padx=5, pady=(4, 10), sticky="w")
        
        # Speakers detail list
        lbl_list_title = ctk.CTkLabel(main_frame, text="包含发音人及音频详情: ", font=self.font_subtitle, text_color=("#4B5563", "#9CA3AF"))
        lbl_list_title.pack(anchor="w", padx=5, pady=(4, 2))
        
        scroll_frame = ctk.CTkScrollableFrame(main_frame, height=90, fg_color=("#F9FAFB", "#20232A"), corner_radius=8, border_width=1, border_color=("#E5E7EB", "#2D3139"))
        scroll_frame.pack(fill="x", padx=5, pady=2)
        
        scroll_frame.grid_columnconfigure((0, 1, 2), weight=1)
        
        # Headers
        h_name = ctk.CTkLabel(scroll_frame, text="发音人名称", font=self.font_small, text_color="#A3A3A3", anchor="w")
        h_name.grid(row=0, column=0, padx=8, pady=2, sticky="ew")
        h_mode = ctk.CTkLabel(scroll_frame, text="音频工作模式", font=self.font_small, text_color="#A3A3A3", anchor="w")
        h_mode.grid(row=0, column=1, padx=8, pady=2, sticky="ew")
        h_detail = ctk.CTkLabel(scroll_frame, text="详情信息 (分析项 / 音频)", font=self.font_small, text_color="#A3A3A3", anchor="w")
        h_detail.grid(row=0, column=2, padx=8, pady=2, sticky="ew")
        
        sep = ctk.CTkFrame(scroll_frame, height=1, fg_color=("#E5E7EB", "#374151"))
        sep.grid(row=1, column=0, columnspan=3, sticky="ew", pady=4)
        
        row_idx = 2
        for spk_id, spk in speakers.items():
            name = spk.get("name", "发音人")
            mode = spk.get("tab_mode", "多条独立音频")
            item_cnt = len(spk.get("items", {}))
            
            if "单条" in mode:
                mode_str = "单条长音频"
                audios_str = "1 个长音频"
            else:
                mode_str = "多条独立音频"
                audios_str = f"{len(spk.get('pending_batch_paths', []))} 个短音频"
                
            lbl_name = ctk.CTkLabel(scroll_frame, text=name, font=self.font_main, text_color=("#111827", "#E5E7EB"), anchor="w")
            lbl_name.grid(row=row_idx, column=0, padx=8, pady=3, sticky="w")
            
            lbl_mode = ctk.CTkLabel(scroll_frame, text=mode_str, font=self.font_main, text_color=("#4B5563", "#9CA3AF"), anchor="w")
            lbl_mode.grid(row=row_idx, column=1, padx=8, pady=3, sticky="w")
            
            lbl_detail = ctk.CTkLabel(scroll_frame, text=f"{item_cnt} 项 / {audios_str}", font=self.font_mono, text_color=("#10B981", "#34D399"), anchor="w")
            lbl_detail.grid(row=row_idx, column=2, padx=8, pady=3, sticky="w")
            
            row_idx += 1
            
        self.import_mode_var = ctk.StringVar(value="overwrite")
        
        if not self.is_empty:
            # Mode selector panel - only shown when current project has data
            mode_panel = ctk.CTkFrame(main_frame, fg_color="transparent")
            mode_panel.pack(fill="x", padx=5, pady=(8, 4))
            
            self.import_mode_var.set("overlay")
            
            lbl_mode_title = ctk.CTkLabel(mode_panel, text="选择导入冲突解决方式:", font=self.font_subtitle, text_color=("#1F2937", "#E5E7EB"))
            lbl_mode_title.pack(anchor="w", pady=(0, 4))
            
            radio_overwrite = ctk.CTkRadioButton(
                mode_panel, 
                text="覆盖导入 (清除当前所有数据并完全载入新工程)", 
                variable=self.import_mode_var, 
                value="overwrite",
                font=self.font_main,
                command=self._on_mode_changed
            )
            radio_overwrite.pack(anchor="w", padx=10, pady=3)
            
            radio_overlay = ctk.CTkRadioButton(
                mode_panel, 
                text="叠加导入 (合并到当前工程中，同名发音人自动重命名)", 
                variable=self.import_mode_var, 
                value="overlay",
                font=self.font_main,
                command=self._on_mode_changed
            )
            radio_overlay.pack(anchor="w", padx=10, pady=3)
            
            # Warning Card - only for non-empty projects
            self.warning_card = ctk.CTkFrame(main_frame, fg_color=("#EFF6FF", "#1E293B"), corner_radius=8, border_width=1, border_color=("#DBEAFE", "#2563EB"))
            self.warning_card.pack(fill="x", padx=5, pady=(6, 4))
            
            self.lbl_warning = ctk.CTkLabel(
                self.warning_card,
                text="",
                font=self.font_main,
                text_color=("#1E40AF", "#60A5FA"),
                justify="left",
                wraplength=500
            )
            self.lbl_warning.pack(padx=15, pady=8, fill="x")
            
            self._update_warning_text()
        else:
            # Empty project: show a simple friendly info banner
            info_banner = ctk.CTkFrame(main_frame, fg_color=("#ECFDF5", "#022C22"), corner_radius=8, border_width=1, border_color=("#A7F3D0", "#059669"))
            info_banner.pack(fill="x", padx=5, pady=(8, 4))
            
            lbl_info = ctk.CTkLabel(
                info_banner,
                text="✅ 当前工程为空，将直接导入新工程数据。",
                font=self.font_main,
                text_color=("#059669", "#34D399"),
                justify="left"
            )
            lbl_info.pack(padx=15, pady=8, fill="x")
        
        # Footer Action Buttons
        btn_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
        btn_frame.pack(side="bottom", fill="x", padx=5, pady=(10, 0))
        
        btn_cancel = ctk.CTkButton(
            btn_frame,
            text="取消",
            font=self.font_subtitle,
            width=100,
            height=34,
            corner_radius=17,
            fg_color=("#F3F4F6", "#374151"),
            text_color=("#4B5563", "#D1D5DB"),
            hover_color=("#E5E7EB", "#4B5563"),
            command=self.destroy
        )
        btn_cancel.pack(side="right", padx=(8, 0))

        btn_confirm = ctk.CTkButton(
            btn_frame,
            text="确认导入",
            font=self.font_subtitle,
            width=120,
            height=34,
            corner_radius=17,
            fg_color="#3B82F6",
            hover_color="#2563EB",
            text_color="white",
            command=self.confirm_import
        )
        btn_confirm.pack(side="right")
        
        # Calculate and apply final window size after all widgets are laid out
        self.after(10, self._fit_window_size)

    def _on_mode_changed(self):
        self._update_warning_text()

    def _update_warning_text(self):
        mode = self.import_mode_var.get()
        if mode == "overwrite":
            self.warning_card.configure(fg_color=("#FEF2F2", "#450A0A"), border_color=("#FCA5A5", "#EF4444"))
            self.lbl_warning.configure(
                text="⚠️ 注意：覆盖导入将完全清除当前已加载的所有数据（包括所有发音人、音频和字表数据），此操作不可逆！",
                text_color=("#B91C1C", "#F87171")
            )
        else:
            self.warning_card.configure(fg_color=("#EFF6FF", "#1E293B"), border_color=("#DBEAFE", "#2563EB"))
            self.lbl_warning.configure(
                text='ℹ️ 提示：叠加导入将把导入工程中的所有发音人合并至当前已加载的工程中。当前工程中的所有数据均会被妥善保留。\n如果有同名发音人，导入后将自动重命名（例如："发音人1_2"）以避免覆盖冲突。',
                text_color=("#1E40AF", "#60A5FA")
            )
        # Re-fit after warning text changes
        self.after(10, self._fit_window_size)

    def _fit_window_size(self):
        """Apply window size and allow resizing."""
        width = 580
        if self.is_empty:
            height = 560
        else:
            mode = self.import_mode_var.get()
            if mode == "overwrite":
                height = 680
            else:
                height = 700
        
        screen_width = self.winfo_screenwidth()
        screen_height = self.winfo_screenheight()
        x = (screen_width - width) // 2
        y = (screen_height - height) // 2
        self.geometry(f"{width}x{height}+{x}+{y}")

    def confirm_import(self):
        overlay = (self.import_mode_var.get() == "overlay")
        self.destroy()
        # Trigger actual load on app thread
        self.app.root.after(50, lambda: self.app.execute_project_import(self.zip_path, overlay))
