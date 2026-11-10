"""Layout Mixin: UI 布局构建、图标配置、全局快捷键与交互辅助."""
import os
import sys
import numpy as np
from PIL import Image
import tkinter as tk
import customtkinter as ctk

from modules.ui_widgets import CTkReleaseButton, ToolTip
from modules.project_tree import ProjectTreePanel
from modules.spectrogram_panel import SpectrogramPanel
from modules.core.paths import get_asset_path

# Monkey patch CTkScrollableFrame._mouse_wheel_all to increase scroll speed on Windows by a factor of 3.0
orig_mouse_wheel_all = ctk.CTkScrollableFrame._mouse_wheel_all
def patched_mouse_wheel_all(self, event):
    if self.check_if_master_is_canvas(event.widget):
        if sys.platform.startswith("win"):
            if self._shift_pressed:
                if self._parent_canvas.xview() != (0.0, 1.0):
                    self._parent_canvas.xview("scroll", -int(event.delta / 2), "units")
            else:
                if self._parent_canvas.yview() != (0.0, 1.0):
                    self._parent_canvas.yview("scroll", -int(event.delta / 2), "units")
        else:
            orig_mouse_wheel_all(self, event)
    else:
        orig_mouse_wheel_all(self, event)
ctk.CTkScrollableFrame._mouse_wheel_all = patched_mouse_wheel_all


class AppLayoutMixin:
    """提供主界面组件布局构建、图标加载、状态栏与快捷键绑定."""

    def setup_icons(self):
        # 预加载所有图标
        icon_path = get_asset_path("icons")

        self.icons = {}
        icon_files = {
            "audio": "audio_file.png", "cut": "cut.png", "batch": "batch.png",
            "eye": "eye.png", "list": "list.png", "plus": "plus.png",
            "play": "play.png", "save": "save.png", "save_black": "save.png", "check": "check.png",
            "bulb": "bulb.png", "points": "points.png", "energy": "energy.png",
            "duration": "duration.png", "trim": "trim.png", "tag": "tag.png",
            "tab_single": "tab_single.png", "tab_batch": "tab_batch.png",
            "status_success": "status_success.png",
            "status_loading": "status_loading.png",
            "status_error": "status_error.png",
            "warning": "warning.png",
            "import": "import_file.png", "ai_prompt": "ai_prompt.png", "copy": "copy_icon.png",
            "import_white": "import_white.png", "copy_white": "copy_white.png", "check_white": "check_white.png",
            "pause": "pause.png", "eraser": "eraser.png",
            "blue_dot": "blue_dot.png",
            "gray_dot": "blue_dot.png",
            "folder_close": "folder_close.png",
            "folder_open": "folder_open.png",
            "audio_wave": "audio_wave.png",
            "filter_all_black": "filter_all_black.png",
            "filter_all_white": "filter_all_white.png",
            "filter_warning_black": "filter_warning_black.png",
            "filter_warning_white": "filter_warning_white.png",
            "filter_check_black": "filter_check_black.png",
            "filter_check_white": "filter_check_white.png"
        }
        from PIL import ImageTk
        self.tk_icons = {}
        for key, filename in icon_files.items():
            path = os.path.join(icon_path, filename)
            if os.path.exists(path):
                img = Image.open(path)

                # 将 自动识别 (bulb) 的黑色图标染色为智能辅助靛紫（浅色 #4338CA，深色 #A5B4FC），与按钮文字和主题统一
                if key == "bulb":
                    try:
                        img_rgba = img.convert("RGBA")
                        data_arr = np.array(img_rgba)
                        mask = data_arr[:, :, 3] > 0
                        # 浅色模式: #4338CA (67, 56, 202)
                        data_light = data_arr.copy()
                        data_light[mask, 0] = 67
                        data_light[mask, 1] = 56
                        data_light[mask, 2] = 202
                        img_light = Image.fromarray(data_light)
                        # 深色模式: #A5B4FC (165, 180, 252)
                        data_dark = data_arr.copy()
                        data_dark[mask, 0] = 165
                        data_dark[mask, 1] = 180
                        data_dark[mask, 2] = 252
                        img_dark = Image.fromarray(data_dark)

                        img = img_light
                        self.icons[key] = ctk.CTkImage(light_image=img_light, dark_image=img_dark, size=(20, 20))
                        self.tk_icons[key] = ImageTk.PhotoImage(img_light.resize((16, 16), Image.Resampling.LANCZOS))
                        continue
                    except Exception:
                        pass
                elif key in ["save_black", "gray_dot"]:
                    try:
                        img_rgba = img.convert("RGBA")
                        data = np.array(img_rgba)
                        if key == "save_black":
                            mask = data[:,:,3] > 0
                            data[mask, 0] = 31
                            data[mask, 1] = 41
                            data[mask, 2] = 55
                        elif key == "gray_dot":
                            mask = data[:,:,3] > 0
                            data[mask, 0] = 156
                            data[mask, 1] = 163
                            data[mask, 2] = 175
                        img = Image.fromarray(data)
                    except Exception:
                        pass

                if key in ["tab_single", "tab_batch"]:
                    try:
                        img_rgba = img.convert("RGBA")
                        data_dark = np.array(img_rgba)
                        mask = data_dark[:, :, 3] > 0
                        data_dark[mask, 0] = 51   # #334155
                        data_dark[mask, 1] = 65
                        data_dark[mask, 2] = 85
                        img_dark = Image.fromarray(data_dark)
                        img_tk_dark = img_dark.resize((16, 16), Image.Resampling.LANCZOS)
                        self.icons[f"{key}_dark"] = ctk.CTkImage(light_image=img_dark, dark_image=img, size=(20, 20))
                        self.icons[f"{key}_white"] = ctk.CTkImage(light_image=img, dark_image=img, size=(20, 20))
                        self.tk_icons[f"{key}_dark"] = ImageTk.PhotoImage(img_tk_dark)
                        self.tk_icons[f"{key}_white"] = ImageTk.PhotoImage(img.resize((16, 16), Image.Resampling.LANCZOS))
                    except Exception:
                        pass

                if "filter_" in key:
                    img_tk = img.resize((18, 18), Image.Resampling.LANCZOS)
                elif key in ["blue_dot", "gray_dot"]:
                    try:
                        # Resize the dot to 8x8
                        img_small = img.resize((8, 8), Image.Resampling.LANCZOS)
                        # Create a 16x16 transparent image
                        new_img = Image.new("RGBA", (16, 16), (0, 0, 0, 0))
                        # Paste the small blue dot in the center (4, 4)
                        new_img.paste(img_small, (4, 4))
                        img_tk = new_img
                    except Exception:
                        img_tk = img.resize((16, 16), Image.Resampling.LANCZOS)
                else:
                    img_tk = img.resize((16, 16), Image.Resampling.LANCZOS)

                if "filter_" in key:
                    self.icons[key] = ctk.CTkImage(light_image=img, dark_image=img, size=(18, 18))
                else:
                    self.icons[key] = ctk.CTkImage(light_image=img, dark_image=img, size=(20, 20))
                self.tk_icons[key] = ImageTk.PhotoImage(img_tk)
            else:
                self.icons[key] = None
                self.tk_icons[key] = None

        logo_path = get_asset_path("icon.png")
        if os.path.exists(logo_path):
            img = Image.open(logo_path)
            self.icons["logo"] = ctk.CTkImage(light_image=img, dark_image=img, size=(45, 45))

        brand_logo_path = get_asset_path("logo.png")
        if os.path.exists(brand_logo_path):
            img = Image.open(brand_logo_path)
            orig_w, orig_h = img.size
            target_h = 60
            target_w = int(orig_w * (target_h / orig_h))
            self.icons["brand_logo"] = ctk.CTkImage(light_image=img, dark_image=img, size=(target_w, target_h))


    def setup_ui(self):
        # --- Sidebar Container ---
        sidebar_frame = ctk.CTkFrame(self.root, width=336, fg_color="transparent")
        self.left_panel = sidebar_frame
        sidebar_frame.pack(side=tk.LEFT, fill=tk.Y, padx=10, pady=(10, 5))

        # --- Header (Fixed at top) ---
        header_frame = ctk.CTkFrame(sidebar_frame, fg_color="transparent")
        header_frame.pack(side=tk.TOP, fill=tk.X, pady=(5, 10))

        left_scrollable = ctk.CTkScrollableFrame(sidebar_frame, width=336, fg_color="transparent")
        left_scrollable.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        self._make_scrollable_auto(left_scrollable)

        btn_kwargs_primary = {"corner_radius": 20, "height": 38, "font": self.font_main,
                              "fg_color": "#2563EB", "hover_color": "#1D4ED8", "text_color": "white"}
        btn_kwargs_secondary = {"corner_radius": 20, "height": 38, "font": self.font_main,
                                "fg_color": "#E5E7EB", "text_color": "#1F2937", "hover_color": "#D1D5DB"}

        if self.icons.get("brand_logo"):
            self.logo_lbl = ctk.CTkLabel(header_frame, text="", image=self.icons.get("brand_logo"), cursor="hand2")
            self.logo_lbl.pack(side=tk.LEFT, padx=(10, 15))
        else:
            self.logo_lbl = ctk.CTkLabel(header_frame, text="PhonTracer", font=ctk.CTkFont(family="Segoe UI", size=26, weight="bold"), text_color="#1F2937", cursor="hand2")
            self.logo_lbl.pack(side=tk.LEFT, padx=(10, 15))

        # 绑定点击事件与悬停提示
        self.logo_lbl.bind("<Button-1>", lambda e: self.open_about_dialog())
        ToolTip(self.logo_lbl, "关于 PhonTracer")

        status_container = ctk.CTkFrame(header_frame, fg_color="transparent")
        status_container.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.lbl_status = ctk.CTkLabel(status_container, text=" 就绪", image=self.icons.get("status_success"), compound="left", text_color="#10B981", font=ctk.CTkFont(family="Microsoft YaHei", size=12), wraplength=120)
        self.lbl_status.pack(pady=(5, 5), expand=True)

        self.progress_bar = ctk.CTkProgressBar(status_container, height=6, corner_radius=10,
                                               progress_color="#2563EB", fg_color="#E5E7EB")
        self.progress_bar.set(0)


        self.speaker_frame = ctk.CTkFrame(left_scrollable, fg_color="white", corner_radius=10)
        self.speaker_frame.pack(fill=tk.X, pady=(0, 10))
        speaker_header = ctk.CTkFrame(self.speaker_frame, fg_color="transparent")
        speaker_header.pack(fill=tk.X, padx=15, pady=(10, 5))
        ctk.CTkLabel(speaker_header, text="发音人列表", font=self.font_title, text_color="#111827").pack(side=tk.LEFT)
        CTkReleaseButton(speaker_header, text="", image=self.icons.get("plus"), width=24, height=24, command=self.on_add_speaker, fg_color="transparent", hover_color="#E5E7EB").pack(side=tk.RIGHT)
        self.speaker_option_var = ctk.StringVar(value=self.active_speaker.name)
        self.speaker_dropdown = ctk.CTkOptionMenu(
            self.speaker_frame,
            variable=self.speaker_option_var,
            values=[s.name for s in self.speaker_manager.get_all_speakers()],
            command=self.on_speaker_changed,
            font=self.font_main,
            fg_color="#F3F4F6",
            text_color="#1F2937",
            button_color="#F3F4F6",             # 统一背景色，消除右侧色块，使其浑然一体
            button_hover_color="#E5E7EB",
            height=32,                          # 增加高度以优化 Canvas 箭头高 DPI 渲染
            corner_radius=16                    # 药丸型圆角
        )
        self.speaker_dropdown.pack(fill=tk.X, padx=15, pady=(0, 10))
        # 修复下拉菜单箭头在 Windows 高分屏上渲染出白色噪点，同时避免重绘背景时出现白色缝隙
        try:
            orig_draw_arrow = self.speaker_dropdown._draw_engine.draw_dropdown_arrow
            def custom_draw_arrow(*args, **kwargs):
                old_method = self.speaker_dropdown._draw_engine.preferred_drawing_method
                self.speaker_dropdown._draw_engine.preferred_drawing_method = "polygon_shapes"
                res = orig_draw_arrow(*args, **kwargs)
                self.speaker_dropdown._draw_engine.preferred_drawing_method = old_method
                try:
                    self.speaker_dropdown._canvas.itemconfigure("dropdown_arrow", width=2)
                except Exception:
                    pass
                return res
            self.speaker_dropdown._draw_engine.draw_dropdown_arrow = custom_draw_arrow
            self.speaker_dropdown._canvas.delete("dropdown_arrow")
            # 依靠 pack 后的 <Configure> 事件自动触发正常的 _draw，无需手动重绘
        except Exception:
            pass
        speaker_actions = ctk.CTkFrame(self.speaker_frame, fg_color="transparent")
        speaker_actions.pack(fill=tk.X, padx=15, pady=(0, 10))
        CTkReleaseButton(
            speaker_actions,
            text="重命名",
            command=self.on_rename_speaker,
            height=32,
            corner_radius=16,
            font=self.font_main,
            fg_color="#F3F4F6",
            text_color="#4B5563",
            hover_color="#E5E7EB"
        ).pack(side=tk.LEFT, expand=True, padx=(0, 5), fill=tk.X)
        CTkReleaseButton(
            speaker_actions,
            text="删除",
            command=self.on_delete_speaker,
            height=32,
            corner_radius=16,
            font=self.font_main,
            fg_color="#FEE2E2",
            text_color="#DC2626",
            hover_color="#FCA5A5"
        ).pack(side=tk.LEFT, expand=True, padx=(5, 0), fill=tk.X)

        switch_row = ctk.CTkFrame(self.speaker_frame, fg_color="transparent")
        switch_row.pack(fill=tk.X, padx=15, pady=(0, 10))
        self.switch_unified_wordlist = ctk.CTkSwitch(
            switch_row, text="统一字表", font=self.font_main, width=60,
            progress_color="#2563EB", button_hover_color="#1D4ED8"
        )
        self.switch_unified_wordlist.pack(side=tk.LEFT)
        self.switch_unified_wordlist.select()


        self.tabview = ctk.CTkTabview(
            left_scrollable, height=170, corner_radius=12, fg_color="white",
            segmented_button_selected_color="#2563EB",
            segmented_button_selected_hover_color="#1D4ED8",
            segmented_button_fg_color="#F3F4F6",
            command=self._update_tabview_colors_and_icons
        )
        self.tabview.pack(fill=tk.X, pady=(0, 10))
        self.tabview._segmented_button.configure(
            corner_radius=20,
            unselected_color="#F3F4F6",
            unselected_hover_color="#E5E7EB"
        )

        # 分析模式 (药丸型按钮)
        self.mode_frame = ctk.CTkFrame(left_scrollable, fg_color="white", corner_radius=10)
        self.mode_frame.pack(fill=tk.X, pady=(0, 10))

        lbl_mode_title = ctk.CTkLabel(self.mode_frame, text="分析模式", font=self.font_title, text_color="#111827")
        lbl_mode_title.pack(side=tk.LEFT, padx=(12, 4), pady=10)

        self.mode_button = ctk.CTkSegmentedButton(
            self.mode_frame,
            values=["声调/F0", "共振峰/F1-F2"],
            command=self.on_analysis_mode_change,
            selected_color="#2563EB",
            selected_hover_color="#1D4ED8",
            fg_color="#F3F4F6",
            unselected_color="#F3F4F6",
            unselected_hover_color="#E5E7EB",
            text_color="#1F2937",
            corner_radius=20,  # Pill shaped!
            height=32,
            font=ctk.CTkFont(family="Microsoft YaHei", size=11, weight="bold")
        )
        self.mode_button.pack(side=tk.RIGHT, padx=(4, 12), pady=10)
        initial_mode = "声调/F0" if self.last_params.get('analysis_mode', 'f0') == 'f0' else "共振峰/F1-F2"
        self.mode_button.set(initial_mode)
        self._update_mode_button_text_colors()
        tab_long = self.tabview.add("单条长音频")
        tab_batch = self.tabview.add("多条独立音频")

        orig_tabview_set = self.tabview.set
        def safe_tabview_set(name):
            orig_tabview_set(name)
            self._update_tabview_colors_and_icons()
        self.tabview.set = safe_tabview_set
        self._update_tabview_colors_and_icons()

        CTkReleaseButton(tab_long, text=" 导入长音频", image=self.icons.get("audio"), compound="left", command=self.load_long_audio, **btn_kwargs_primary).pack(fill=tk.X, padx=10, pady=(15, 2))
        self.lbl_long_file = ctk.CTkLabel(tab_long, text="未选择", font=self.font_main, text_color="#6B7280")
        self.lbl_long_file.pack(pady=(0, 10))
        row_mode1_btns = ctk.CTkFrame(tab_long, fg_color="transparent")
        row_mode1_btns.pack(fill=tk.X, padx=6, pady=(0, 15))
        CTkReleaseButton(row_mode1_btns, text=" 导入字表", image=self.icons.get("cut"), compound="left", command=lambda: self.open_text_dialog('long'), **btn_kwargs_secondary, width=110).pack(side=tk.LEFT, expand=True, padx=(0, 4))
        CTkReleaseButton(row_mode1_btns, text=" 段落编辑器", image=self.icons.get("eye"), compound="left", command=self.open_visual_splitter, **btn_kwargs_secondary, width=110).pack(side=tk.RIGHT, expand=True, padx=(4, 0))

        CTkReleaseButton(tab_batch, text=" 选择多个音频文件", image=self.icons.get("batch"), compound="left", command=self.load_batch_audio, **btn_kwargs_primary).pack(fill=tk.X, padx=10, pady=(15, 2))
        self.lbl_batch_files = ctk.CTkLabel(tab_batch, text="未选择", font=self.font_main, text_color="#6B7280")
        self.lbl_batch_files.pack(pady=(0, 10))
        row_mode2_btns = ctk.CTkFrame(tab_batch, fg_color="transparent")
        row_mode2_btns.pack(fill=tk.X, padx=6, pady=(0, 15))
        CTkReleaseButton(row_mode2_btns, text=" 文件名提取", image=self.icons.get("tag"), compound="left", command=self.process_batch_direct, **btn_kwargs_secondary, width=110).pack(side=tk.LEFT, expand=True, padx=(0, 4))
        CTkReleaseButton(row_mode2_btns, text=" 导入字表", image=self.icons.get("list"), compound="left", command=lambda: self.open_text_dialog('batch'), **btn_kwargs_secondary, width=110).pack(side=tk.RIGHT, expand=True, padx=(4, 0))

        card_params = ctk.CTkFrame(left_scrollable, fg_color="white", corner_radius=10)
        card_params.pack(fill=tk.X, pady=(0, 10))

        # 头部折叠栏（支持手势点击与手型悬停）
        header_frame = ctk.CTkFrame(card_params, fg_color="transparent", cursor="hand2")
        header_frame.pack(fill=tk.X, padx=15, pady=(12, 12))

        self.lbl_card_title = ctk.CTkLabel(header_frame, text="全局算法与导出参数", font=self.font_title, text_color="#111827", cursor="hand2")
        self.lbl_card_title.pack(side=tk.LEFT)

        self.lbl_card_toggle = ctk.CTkLabel(header_frame, text="▶", font=self.font_title, text_color="#6B7280", cursor="hand2")
        self.lbl_card_toggle.pack(side=tk.RIGHT)

        # 折叠容器
        self.params_content_frame = ctk.CTkFrame(card_params, fg_color="transparent")
        # 默认折叠，不进行pack

        self.params_expanded = False
        def toggle_params(event=None):
            if self.params_expanded:
                self.params_content_frame.pack_forget()
                self.lbl_card_toggle.configure(text="▶")
                header_frame.pack(fill=tk.X, padx=15, pady=(12, 12))
                self.params_expanded = False
            else:
                self.params_content_frame.pack(fill=tk.X)
                self.lbl_card_toggle.configure(text="▼")
                header_frame.pack(fill=tk.X, padx=15, pady=(12, 10))
                self.params_expanded = True

        header_frame.bind("<Button-1>", toggle_params)
        self.lbl_card_title.bind("<Button-1>", toggle_params)
        self.lbl_card_toggle.bind("<Button-1>", toggle_params)

        row_pts = ctk.CTkFrame(self.params_content_frame, fg_color="transparent")
        row_pts.pack(fill=tk.X, padx=15, pady=5)
        lbl_pts = ctk.CTkLabel(row_pts, text=" 等分点 (N):", image=self.icons.get("points"), compound="left", text_color="#374151", font=self.font_main)
        lbl_pts.pack(side=tk.LEFT)
        self.slider_pts = ctk.CTkSlider(row_pts, from_=5, to=20, number_of_steps=15, width=100, height=16,
                                        button_color="#2563EB", button_hover_color="#1D4ED8", progress_color="#2563EB",
                                        command=lambda v: self._on_slider_change(v, self.entry_points, 'pts'))
        self.slider_pts.set(self.last_params['pts'])
        self.slider_pts.pack(side=tk.LEFT, padx=10)
        self.entry_points = ctk.CTkEntry(row_pts, width=60, justify="center", corner_radius=20, height=26)
        self.entry_points.insert(0, str(self.last_params['pts']))
        self.entry_points.pack(side=tk.RIGHT)
        self.setup_entry_behavior(self.entry_points, 'pts')

        row_db = ctk.CTkFrame(self.params_content_frame, fg_color="transparent")
        row_db.pack(fill=tk.X, padx=15, pady=5)
        lbl_db = ctk.CTkLabel(row_db, text=" 能量落差:", image=self.icons.get("energy"), compound="left", text_color="#374151", font=self.font_main)
        lbl_db.pack(side=tk.LEFT)
        self.slider_db = ctk.CTkSlider(row_db, from_=10, to=100, number_of_steps=90, width=100, height=16,
                                       button_color="#2563EB", button_hover_color="#1D4ED8", progress_color="#2563EB",
                                       command=lambda v: self._on_slider_change(v, self.entry_drop_db, 'db'))
        self.slider_db.set(self.last_params['db'])
        self.slider_db.pack(side=tk.LEFT, padx=10)
        self.var_drop_db = ctk.StringVar(value=str(self.last_params['db']))
        self.entry_drop_db = ctk.CTkEntry(row_db, textvariable=self.var_drop_db, width=60, justify="center", corner_radius=20, height=26)
        self.entry_drop_db.pack(side=tk.RIGHT)
        self.setup_entry_behavior(self.entry_drop_db, 'db')

        row_dur = ctk.CTkFrame(self.params_content_frame, fg_color="transparent")
        row_dur.pack(fill=tk.X, padx=15, pady=5)
        lbl_dur = ctk.CTkLabel(row_dur, text=" 排除声母:", image=self.icons.get("duration"), compound="left", text_color="#374151", font=self.font_main)
        lbl_dur.pack(side=tk.LEFT)
        self.slider_dur = ctk.CTkSlider(row_dur, from_=0.00, to=0.15, number_of_steps=15, width=100, height=16,
                                        button_color="#2563EB", button_hover_color="#1D4ED8", progress_color="#2563EB",
                                        command=lambda v: self._on_slider_change(v, self.entry_min_dur, 'skip_front'))
        self.slider_dur.set(self.last_params['skip_front'])
        self.slider_dur.pack(side=tk.LEFT, padx=10)
        self.var_min_dur = ctk.StringVar(value=f"{self.last_params['skip_front']:.2f}")
        self.entry_min_dur = ctk.CTkEntry(row_dur, textvariable=self.var_min_dur, width=60, justify="center", corner_radius=20, height=26)
        self.entry_min_dur.pack(side=tk.RIGHT)
        self.setup_entry_behavior(self.entry_min_dur, 'skip_front')

        # F0 专属参数容器
        self.f0_params_container = ctk.CTkFrame(self.params_content_frame, fg_color="transparent")
        self.f0_params_container.pack(fill=tk.X)

        # Pitch 范围参数
        row_pitch = ctk.CTkFrame(self.f0_params_container, fg_color="transparent")
        row_pitch.pack(fill=tk.X, padx=15, pady=5)
        ctk.CTkLabel(row_pitch, text=" F0 范围 (Hz):", image=self.icons.get("points"), compound="left", text_color="#374151", font=self.font_main).pack(side=tk.LEFT)
        self.btn_detect_f0 = CTkReleaseButton(
            row_pitch,
            text="检测",
            command=self.on_detect_f0_clicked,
            width=50,
            height=22,
            font=self.font_main,
            corner_radius=11,
            fg_color="#F3F4F6",
            text_color="#2563EB",
            hover_color="#E5E7EB"
        )
        self.btn_detect_f0.pack(side=tk.LEFT, padx=(6, 8))
        self.entry_pitch_ceiling = ctk.CTkEntry(row_pitch, width=50, justify="center", corner_radius=20, height=26)
        self.entry_pitch_ceiling.insert(0, str(self.last_params['pitch_ceiling']))
        self.entry_pitch_ceiling.pack(side=tk.RIGHT)
        self.setup_entry_behavior(self.entry_pitch_ceiling, 'pitch_ceiling')
        self.setup_numeric_wheel_stepper(self.entry_pitch_ceiling, 'pitch_ceiling', step=25, value_type=int)
        ctk.CTkLabel(row_pitch, text="~", text_color="#6B7280").pack(side=tk.RIGHT, padx=2)
        self.entry_pitch_floor = ctk.CTkEntry(row_pitch, width=50, justify="center", corner_radius=20, height=26)
        self.entry_pitch_floor.insert(0, str(self.last_params['pitch_floor']))
        self.entry_pitch_floor.pack(side=tk.RIGHT)
        self.setup_entry_behavior(self.entry_pitch_floor, 'pitch_floor')
        self.setup_numeric_wheel_stepper(self.entry_pitch_floor, 'pitch_floor', step=25, value_type=int)

        # 浊音阈值参数
        row_voicing = ctk.CTkFrame(self.f0_params_container, fg_color="transparent")
        row_voicing.pack(fill=tk.X, padx=15, pady=5)
        ctk.CTkLabel(row_voicing, text=" 浊音阈值:", image=self.icons.get("points"), compound="left", text_color="#374151", font=self.font_main).pack(side=tk.LEFT)
        self.entry_voicing_threshold = ctk.CTkEntry(row_voicing, width=55, justify="center", corner_radius=20, height=26)
        self.entry_voicing_threshold.insert(0, f"{self.last_params['voicing_threshold']:.2f}")
        self.entry_voicing_threshold.pack(side=tk.RIGHT)
        self.setup_entry_behavior(self.entry_voicing_threshold, 'voicing_threshold')

        # 共振峰专属参数容器
        self.formant_params_container = ctk.CTkFrame(self.params_content_frame, fg_color="transparent")

        # 共振峰参数 row 1: Formant Count (5)
        row_formant_count = ctk.CTkFrame(self.formant_params_container, fg_color="transparent")
        row_formant_count.pack(fill=tk.X, padx=15, pady=4)
        ctk.CTkLabel(row_formant_count, text=" 数量 (N):", image=self.icons.get("points"), compound="left", text_color="#374151", font=self.font_main).pack(side=tk.LEFT)
        self.entry_formant_count = ctk.CTkEntry(row_formant_count, width=65, justify="center", corner_radius=20, height=26)
        self.entry_formant_count.insert(0, str(self.last_params.get('formant_count', 5)))
        self.entry_formant_count.pack(side=tk.RIGHT)
        self.setup_entry_behavior(self.entry_formant_count, 'formant_count')

        # 共振峰参数 row 2: Praat Burg 分析上限 (5500)
        row_formant_max = ctk.CTkFrame(self.formant_params_container, fg_color="transparent")
        row_formant_max.pack(fill=tk.X, padx=15, pady=4)
        ctk.CTkLabel(row_formant_max, text=" 分析上限 (Hz):", image=self.icons.get("points"), compound="left", text_color="#374151", font=self.font_main).pack(side=tk.LEFT)
        self.btn_detect_formant = CTkReleaseButton(
            row_formant_max,
            text="检测",
            command=self.on_detect_formant_clicked,
            width=50,
            height=22,
            font=self.font_main,
            corner_radius=11,
            fg_color="#F3F4F6",
            text_color="#2563EB",
            hover_color="#E5E7EB"
        )
        self.btn_detect_formant.pack(side=tk.LEFT, padx=(6, 8))
        self.entry_formant_max_hz = ctk.CTkEntry(row_formant_max, width=65, justify="center", corner_radius=20, height=26)
        self.entry_formant_max_hz.insert(0, str(self.last_params.get('formant_max_hz', 5500.0)))
        self.entry_formant_max_hz.pack(side=tk.RIGHT)
        self.setup_entry_behavior(self.entry_formant_max_hz, 'formant_max_hz')
        self.setup_numeric_wheel_stepper(self.entry_formant_max_hz, 'formant_max_hz', step=250, value_type=float, formatter=lambda v: str(float(v)))

        # 共振峰参数 row 3: Window Length (0.025)
        row_formant_win = ctk.CTkFrame(self.formant_params_container, fg_color="transparent")
        row_formant_win.pack(fill=tk.X, padx=15, pady=4)
        ctk.CTkLabel(row_formant_win, text=" 窗长 (s):", image=self.icons.get("duration"), compound="left", text_color="#374151", font=self.font_main).pack(side=tk.LEFT)
        self.entry_formant_window_length = ctk.CTkEntry(row_formant_win, width=65, justify="center", corner_radius=20, height=26)
        self.entry_formant_window_length.insert(0, str(self.last_params.get('formant_window_length', 0.025)))
        self.entry_formant_window_length.pack(side=tk.RIGHT)
        self.setup_entry_behavior(self.entry_formant_window_length, 'formant_window_length')

        # 共振峰参数 row 4: Pre-emphasis (50)
        row_formant_pre = ctk.CTkFrame(self.formant_params_container, fg_color="transparent")
        row_formant_pre.pack(fill=tk.X, padx=15, pady=4)
        ctk.CTkLabel(row_formant_pre, text=" 预加重 (Hz):", image=self.icons.get("duration"), compound="left", text_color="#374151", font=self.font_main).pack(side=tk.LEFT)
        self.entry_formant_pre_emphasis = ctk.CTkEntry(row_formant_pre, width=65, justify="center", corner_radius=20, height=26)
        self.entry_formant_pre_emphasis.insert(0, str(self.last_params.get('formant_pre_emphasis', 50.0)))
        self.entry_formant_pre_emphasis.pack(side=tk.RIGHT)
        self.setup_entry_behavior(self.entry_formant_pre_emphasis, 'formant_pre_emphasis')

        # 共振峰参数 row 5: Sample Strategy ("整段11点" / "中段均值")
        row_formant_strategy = ctk.CTkFrame(self.formant_params_container, fg_color="transparent")
        row_formant_strategy.pack(fill=tk.X, padx=15, pady=4)
        ctk.CTkLabel(row_formant_strategy, text=" 采样策略:", image=self.icons.get("tag"), compound="left", text_color="#374151", font=self.font_main).pack(side=tk.LEFT)

        self.option_formant_sample_strategy = ctk.CTkOptionMenu(
            row_formant_strategy,
            values=["整段11点", "中段均值"],
            command=self.on_formant_strategy_change,
            width=100,
            height=26,
            corner_radius=20,
            fg_color="#F3F4F6",
            text_color="#1F2937",
            button_color="#E5E7EB",
            button_hover_color="#D1D5DB"
        )
        self.option_formant_sample_strategy.set(self.last_params.get('formant_sample_strategy', '整段11点'))
        self.option_formant_sample_strategy.pack(side=tk.RIGHT)

        # 共振峰参数 row 6: Show F3 Toggle ("显示/导出 F3")
        row_formant_f3 = ctk.CTkFrame(self.formant_params_container, fg_color="transparent")
        row_formant_f3.pack(fill=tk.X, padx=15, pady=4)
        ctk.CTkLabel(row_formant_f3, text=" F3:", image=self.icons.get("tag"), compound="left", text_color="#374151", font=self.font_main).pack(side=tk.LEFT)

        self.var_show_f3 = ctk.BooleanVar(value=bool(self.last_params.get("show_f3", False)))
        self.switch_show_f3 = ctk.CTkSwitch(
            row_formant_f3,
            text="显示/导出",
            variable=self.var_show_f3,
            command=self.on_show_f3_toggle,
            font=self.font_main,
            progress_color="#10B981",
            text_color="#374151"
        )
        self.switch_show_f3.pack(side=tk.RIGHT)

        self.row_trim = ctk.CTkFrame(self.params_content_frame, fg_color="transparent")
        self.row_trim.pack(fill=tk.X, padx=15, pady=(10, 15))
        self.lbl_trim_icon = ctk.CTkLabel(self.row_trim, text="", image=self.icons.get("trim"))
        self.lbl_trim_icon.pack(side=tk.LEFT, padx=(0, 5))
        self.switch_trim_silence = ctk.CTkSwitch(self.row_trim, text="开启边缘静音裁切", font=self.font_main,
                                                 progress_color="#10B981", text_color="#374151", command=self.on_trim_silence_toggle)
        self.switch_trim_silence.pack(side=tk.LEFT)
        self.switch_trim_silence.select()

        self.row_export_rule = ctk.CTkFrame(self.params_content_frame, fg_color="transparent")
        self.row_export_rule.pack(fill=tk.X, padx=15, pady=(0, 15))
        ctk.CTkLabel(self.row_export_rule, text=" 导出编号:", image=self.icons.get("tag"), compound="left", text_color="#374151", font=self.font_main).pack(side=tk.LEFT)
        export_rule_opts = ctk.CTkFrame(self.row_export_rule, fg_color="transparent")
        export_rule_opts.pack(side=tk.LEFT, padx=(10, 0))

        def on_export_rule_change():
            if hasattr(self, 'tree_panel') and self.tree_panel:
                self.tree_panel.on_export_numbering_rule_changed()

        ctk.CTkRadioButton(
            export_rule_opts, text="全部连续", variable=self.export_numbering_rule_var,
            value="continuous", command=on_export_rule_change, font=self.font_main,
            radiobutton_width=18, radiobutton_height=18, width=0
        ).pack(side=tk.LEFT, padx=(0, 12))
        ctk.CTkRadioButton(
            export_rule_opts, text="每组重新标号", variable=self.export_numbering_rule_var,
            value="per_group", command=on_export_rule_change, font=self.font_main,
            radiobutton_width=18, radiobutton_height=18, width=0
        ).pack(side=tk.LEFT)

        # 全局应用按钮 (固定在底部)
        self.btn_apply_all = CTkReleaseButton(sidebar_frame, text="  全局应用", image=self.icons.get("check_white"), compound="left",
                                              command=self.recalculate_all_audio, corner_radius=20, height=44, font=self.font_title,
                                              fg_color="#2563EB", hover_color="#1D4ED8")
        self.btn_apply_all.pack(fill=tk.X, pady=(10, 15))

        # 实例化右侧树状面板 (先于中间面板初始化以确保正确的 pack 顺序)
        self.tree_panel = ProjectTreePanel(
            parent=self.root,
            icons=self.icons,
            tk_icons=self.tk_icons,
            items_dict=self.items,
            app_state_params=self.last_params,
            on_item_selected_callback=self.on_tree_item_selected,
            on_clear_canvas_callback=self.on_clear_canvas_callback,
            app=self
        )

        # 实例化中间画布面板 (最后初始化并 expand=True 以占据剩余空间)
        self.spectrogram_panel = SpectrogramPanel(
            parent=self.root,
            icons=self.icons,
            on_time_changed_callback=self.on_spectrogram_time_changed,
            on_auto_detect_callback=self.on_spectrogram_auto_detect,
            on_export_callback=self.on_export_callback,
            app=self
        )
        self.spectrogram_panel.switch_trim_silence = self.switch_trim_silence
        self._bind_global_shortcuts()


    def _is_text_input_focused(self, event=None):
        widget = getattr(event, "widget", None)
        if widget is None and hasattr(self, "root") and self.root:
            try:
                widget = self.root.focus_get()
            except Exception:
                widget = None
        while widget is not None:
            if isinstance(widget, (tk.Entry, tk.Text, ctk.CTkEntry, ctk.CTkTextbox)):
                return True
            try:
                wclass = str(widget.winfo_class())
                if wclass in ("Entry", "TEntry", "Text", "Spinbox"):
                    return True
            except Exception:
                pass
            widget = getattr(widget, "master", None)
        return False


    def _bind_global_shortcuts(self):
        if not hasattr(self, "root") or not self.root:
            return

        def on_space(event=None):
            if self._is_text_input_focused(event):
                return
            if hasattr(self, "spectrogram_panel") and self.spectrogram_panel and hasattr(self.spectrogram_panel, "play_selected"):
                self.spectrogram_panel.play_selected()
                return "break"

        def on_eraser(event=None):
            if self._is_text_input_focused(event):
                return
            if hasattr(self, "spectrogram_panel") and self.spectrogram_panel and hasattr(self.spectrogram_panel, "toggle_eraser_mode"):
                self.spectrogram_panel.toggle_eraser_mode()
                return "break"

        def on_undo(event=None):
            if self._is_text_input_focused(event):
                return
            if hasattr(self, "spectrogram_panel") and self.spectrogram_panel and hasattr(self.spectrogram_panel, "undo_last_action"):
                self.spectrogram_panel.undo_last_action()
                return "break"

        def on_redo(event=None):
            if self._is_text_input_focused(event):
                return
            if hasattr(self, "spectrogram_panel") and self.spectrogram_panel and hasattr(self.spectrogram_panel, "redo_last_action"):
                self.spectrogram_panel.redo_last_action()
                return "break"

        try:
            self.root.bind("<space>", on_space, add="+")
            self.root.bind("<KeyPress-e>", on_eraser, add="+")
            self.root.bind("<KeyPress-E>", on_eraser, add="+")
            self.root.bind("<Control-z>", on_undo, add="+")
            self.root.bind("<Control-Z>", on_undo, add="+")
            self.root.bind("<Control-y>", on_redo, add="+")
            self.root.bind("<Control-Y>", on_redo, add="+")
        except Exception:
            pass


    def set_status(self, text, color="#10B981", icon_key="status_success"):
        self.lbl_status.configure(text=f" {text}", text_color=color, image=self.icons.get(icon_key))


    def set_progress(self, val):
        self.progress_bar.set(val)


    def start_loading(self, text="正在处理..."):
        if getattr(self, '_progress_hide_timer', None) is not None:
            try:
                self.root.after_cancel(self._progress_hide_timer)
            except Exception:
                pass
            self._progress_hide_timer = None
        self.set_status(text, "#2563EB", "status_loading")
        self.progress_bar.set(0)
        if self.progress_bar.winfo_manager() == "":
            self.progress_bar.pack(fill=tk.X, padx=(0, 15), pady=(0, 8))


    def stop_loading(self, text="完成"):
        if getattr(self, '_progress_hide_timer', None) is not None:
            try:
                self.root.after_cancel(self._progress_hide_timer)
            except Exception:
                pass
            self._progress_hide_timer = None
        self.set_progress(1.0)
        self.set_status(text, "#10B981", "status_success")
        self._progress_hide_timer = self.root.after(1500, lambda: self.progress_bar.pack_forget())


    def _make_scrollable_auto(self, scrollable_frame):
        """
        使 CTkScrollableFrame 的滚动条仅在内容溢出时显示。
        """
        scrollbar = scrollable_frame._scrollbar
        orig_set = scrollbar.set

        def auto_set(low, high):
            if not scrollbar.winfo_exists():
                return
            if float(low) <= 0.0 and float(high) >= 1.0:
                if bool(scrollbar.winfo_manager()):
                    scrollbar.grid_remove()
            else:
                if not bool(scrollbar.winfo_manager()):
                    scrollbar.grid()
            orig_set(low, high)

        scrollbar.set = auto_set
        # 初始检查一次，增加 winfo_exists 保护
        def check_initial():
            if scrollbar.winfo_exists():
                try:
                    scrollbar.set(*scrollbar.get())
                except Exception:
                    pass
        self.root.after(100, check_initial)


    def setup_entry_behavior(self, entry, param_key):
        def on_enter(e): entry.configure(border_color="#2563EB", border_width=2)
        def on_leave(e):
            if self.root.focus_get() != entry:
                entry.configure(border_color=["#979DA2", "#565B5E"], border_width=1)
        def on_focus_in(e):
            entry.configure(border_color="#2563EB", border_width=2)
            entry._last_val = entry.get()
        def on_focus_out(e):
            entry.configure(border_color=["#979DA2", "#565B5E"], border_width=1)
            current_val = entry.get()
            if hasattr(entry, '_last_val') and current_val == entry._last_val: return
            if param_key in ['pts', 'db', 'skip_front', 'pitch_floor', 'pitch_ceiling', 'voicing_threshold', 'formant_max_hz', 'formant_count', 'formant_window_length', 'formant_pre_emphasis']: self.on_param_change()

        entry.bind("<Enter>", on_enter)
        entry.bind("<Leave>", on_leave)
        entry.bind("<FocusIn>", on_focus_in)
        entry.bind("<FocusOut>", on_focus_out)
        entry.bind("<Return>", lambda e: self.root.focus_set())


    def setup_numeric_wheel_stepper(self, entry, param_key, step, value_type=int, min_value=None, max_value=None, formatter=None):
        def on_wheel(event):
            if self.root.focus_get() not in (entry, getattr(entry, '_entry', None)):
                return
            event_num = getattr(event, "num", None)
            if event_num == 4:
                direction = 1
            elif event_num == 5:
                direction = -1
            else:
                direction = 1 if getattr(event, "delta", 0) > 0 else -1

            try:
                val = value_type(entry.get())
            except Exception:
                val = value_type(self.last_params[param_key])

            new_val = val + direction * step

            if param_key == 'pitch_floor':
                try:
                    ceiling = int(self.entry_pitch_ceiling.get())
                except Exception:
                    ceiling = int(self.last_params['pitch_ceiling'])
                effective_min = min_value if min_value is not None else 40
                effective_max = ceiling - 25
                if new_val < effective_min:
                    new_val = effective_min
                if new_val > effective_max:
                    new_val = effective_max
            elif param_key == 'pitch_ceiling':
                try:
                    floor = int(self.entry_pitch_floor.get())
                except Exception:
                    floor = int(self.last_params['pitch_floor'])
                effective_max = max_value if max_value is not None else 1000
                effective_min = floor + 25
                if new_val > effective_max:
                    new_val = effective_max
                if new_val < effective_min:
                    new_val = effective_min
            elif param_key == 'formant_max_hz':
                effective_min = min_value if min_value is not None else 3000
                effective_max = max_value if max_value is not None else 9000
                if new_val < effective_min:
                    new_val = effective_min
                if new_val > effective_max:
                    new_val = effective_max
            else:
                if min_value is not None and new_val < min_value:
                    new_val = min_value
                if max_value is not None and new_val > max_value:
                    new_val = max_value

            entry.delete(0, tk.END)
            formatted = formatter(new_val) if formatter else str(new_val)
            entry.insert(0, formatted)
            entry._last_val = formatted

            timer_attr = f"_wheel_timer_{param_key}"
            timer = getattr(self, timer_attr, None)
            if timer:
                self.root.after_cancel(timer)

            def do_apply():
                curr_item = getattr(self, 'spectrogram_panel', None) and self.spectrogram_panel.current_item
                if param_key in ['pitch_floor', 'pitch_ceiling']:
                    if curr_item:
                        try:
                            pitch_overrides = {
                                'pitch_floor': int(self.entry_pitch_floor.get()),
                                'pitch_ceiling': int(self.entry_pitch_ceiling.get()),
                                'voicing_threshold': float(self.entry_voicing_threshold.get())
                            }
                        except Exception:
                            pitch_overrides = None
                        self.recalculate_current_item(
                            recompute_pitch=True,
                            preserve_bounds=True,
                            completion_text="当前条目 F0 范围已应用",
                            param_overrides=pitch_overrides
                        )
                    else:
                        self.on_param_change(recalculate_current=False)
                        self.recalculate_all_audio(recompute_pitch=True, only_pitch_changed=True)
                elif param_key == 'formant_max_hz' and curr_item:
                    try:
                        formant_overrides = {
                            'formant_max_hz': float(self.entry_formant_max_hz.get()),
                            'formant_count': int(self.entry_formant_count.get()),
                            'formant_window_length': float(self.entry_formant_window_length.get()),
                            'formant_pre_emphasis': float(self.entry_formant_pre_emphasis.get()),
                            'formant_sample_strategy': curr_item.get('formant_sample_strategy', self.last_params.get('formant_sample_strategy', '整段11点')),
                            'analysis_mode': curr_item.get('analysis_mode', self.last_params.get('analysis_mode', 'f0'))
                        }
                    except Exception:
                        formant_overrides = None
                    self.recalculate_current_item(
                        recompute_formant_only=True,
                        completion_text="当前条目共振峰参数已应用",
                        param_overrides=formant_overrides
                    )
                else:
                    self.on_param_change()

            setattr(self, timer_attr, self.root.after(300, do_apply))
            return "break"

        entry.bind("<MouseWheel>", on_wheel)
        entry.bind("<Button-4>", on_wheel)
        entry.bind("<Button-5>", on_wheel)


