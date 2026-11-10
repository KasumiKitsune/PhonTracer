"""
Project tree dialogs, item properties, exclusion reason popups, and constants.
"""
import tkinter as tk
from tkinter import ttk, messagebox
import customtkinter as ctk
import math
from PIL import Image, ImageDraw, ImageTk
from modules.ui_widgets import CTkReleaseButton, AutoScrollbar, make_context_menu, post_context_menu

EXCLUSION_REASON_DETAIL_PLACEHOLDER = "可选：选择具体原因（默认使用大类）"
EXCLUSION_REASON_CATEGORY_PLACEHOLDER = "请先选择上方原因分类"
EXCLUSION_REASON_CUSTOM_CATEGORY = "其他原因"
EXCLUSION_REASON_CATEGORIES = {
    "录音质量问题": [
        "录音中断或不完整",
        "背景噪声过强",
        "爆音、削波或音量失真",
        "音量过低、信噪比不足",
        "混入他人声音",
        "串音、回声或设备异常",
    ],
    "发音内容问题": [
        "发音错误",
        "漏读、错读或多读",
        "重复发音",
        "目标音节不完整",
        "口误、犹豫或自我修正",
        "未按实验要求发音",
    ],
    "语音现象干扰": [
        "咳嗽、笑声、吸气或清嗓",
        "目标词与前后语音严重粘连",
        "意外停顿过长",
        "明显强调、拖长或唱读",
        "沙哑、气声或耳语",
    ],
    "声学分析困难": [
        "F0 无法可靠提取",
        "倍频、半频或跳点过多",
        "清浊判断异常",
        "共振峰轨迹无法可靠识别",
        "边界无法可靠确定",
        "多音节内部切分不明确",
        "有效分析区间过短",
    ],
    "实验与数据管理": [
        "重复条目，仅保留其中一次",
        "试读、练习或测试录音",
        "发音人主动重录，旧版本作废",
        "文件损坏或音频缺失",
        "标签、分组或录音对象不匹配",
    ],
}
EXCLUSION_REASON_LEGACY_ALIASES = {
    "录音中断": ("录音质量问题", "录音中断或不完整"),
    "背景噪声过强": ("录音质量问题", "背景噪声过强"),
    "发音错误": ("发音内容问题", "发音错误"),
}
TREE_INFO_MAX_CHARS = 8
EXCLUSION_REASON_SHORT_LABELS = {
    "录音质量问题": "录音质量",
    "录音中断或不完整": "录音中断",
    "背景噪声过强": "背景噪声",
    "爆音、削波或音量失真": "削波失真",
    "音量过低、信噪比不足": "信噪比低",
    "混入他人声音": "混入人声",
    "串音、回声或设备异常": "设备异常",
    "发音内容问题": "发音问题",
    "发音错误": "发音错误",
    "漏读、错读或多读": "漏错多读",
    "重复发音": "重复发音",
    "目标音节不完整": "音节不全",
    "口误、犹豫或自我修正": "口误犹豫",
    "未按实验要求发音": "要求不符",
    "语音现象干扰": "语音干扰",
    "咳嗽、笑声、吸气或清嗓": "非目标声",
    "目标词与前后语音严重粘连": "语音粘连",
    "意外停顿过长": "停顿过长",
    "明显强调、拖长或唱读": "拖长唱读",
    "沙哑、气声或耳语": "嗓音异常",
    "声学分析困难": "分析困难",
    "F0 无法可靠提取": "F0失效",
    "倍频、半频或跳点过多": "倍半频/跳点",
    "清浊判断异常": "清浊异常",
    "共振峰轨迹无法可靠识别": "共振峰异常",
    "边界无法可靠确定": "边界不稳",
    "多音节内部切分不明确": "切分不明",
    "有效分析区间过短": "区间过短",
    "实验与数据管理": "数据管理",
    "重复条目，仅保留其中一次": "重复条目",
    "试读、练习或测试录音": "试读练习",
    "发音人主动重录，旧版本作废": "重录作废",
    "文件损坏或音频缺失": "文件缺失",
    "标签、分组或录音对象不匹配": "标签不符",
}


def format_exclusion_reason(category, detail="", custom_reason=""):
    """将界面选择转换为可读、可回填的忽略原因文本。"""
    if category == EXCLUSION_REASON_CUSTOM_CATEGORY:
        return custom_reason.strip()
    if category not in EXCLUSION_REASON_CATEGORIES:
        return ""
    detail = detail.strip()
    if detail and detail != EXCLUSION_REASON_DETAIL_PLACEHOLDER:
        return f"{category}：{detail}"
    return category


def parse_exclusion_reason(reason):
    """从新旧版本的原因文本中恢复大类、二级原因和自定义文本。"""
    reason = (reason or "").strip()
    if not reason:
        return "", EXCLUSION_REASON_DETAIL_PLACEHOLDER, ""
    if reason in EXCLUSION_REASON_CATEGORIES:
        return reason, EXCLUSION_REASON_DETAIL_PLACEHOLDER, ""
    if reason in EXCLUSION_REASON_LEGACY_ALIASES:
        category, detail = EXCLUSION_REASON_LEGACY_ALIASES[reason]
        return category, detail, ""
    for category, details in EXCLUSION_REASON_CATEGORIES.items():
        for separator in ("：", ":"):
            prefix = f"{category}{separator}"
            if reason.startswith(prefix):
                detail = reason[len(prefix):].strip()
                if detail in details:
                    return category, detail, ""
        if reason in details:
            return category, reason, ""
    return EXCLUSION_REASON_CUSTOM_CATEGORY, EXCLUSION_REASON_DETAIL_PLACEHOLDER, reason


def apply_custom_dropdown_arrow(dropdown):
    """复用发音人列表的 Windows 高分屏下拉箭头修复。"""
    try:
        orig_draw_arrow = dropdown._draw_engine.draw_dropdown_arrow

        def custom_draw_arrow(*args, **kwargs):
            old_method = dropdown._draw_engine.preferred_drawing_method
            dropdown._draw_engine.preferred_drawing_method = "polygon_shapes"
            try:
                result = orig_draw_arrow(*args, **kwargs)
            finally:
                dropdown._draw_engine.preferred_drawing_method = old_method
            try:
                dropdown._canvas.itemconfigure("dropdown_arrow", width=2)
            except Exception:
                pass
            return result

        dropdown._draw_engine.draw_dropdown_arrow = custom_draw_arrow
        dropdown._canvas.delete("dropdown_arrow")
    except Exception:
        pass


class CanvasButton(tk.Canvas):
    def __init__(self, parent, size=36, image=None, bg_color="white", active_bg="#3B82F6", hover_bg="#F3F4F6", active_hover="#2563EB", border_color="#E5E7EB", is_active=False, command=None):
        super().__init__(parent, width=size, height=size, bg=bg_color, highlightthickness=0, borderwidth=0, cursor="hand2")
        self.size = size
        self.image = image
        self.bg_color = bg_color
        self.active_bg = active_bg
        self.hover_bg = hover_bg
        self.active_hover = active_hover
        self.border_color = border_color
        self.is_active = is_active
        self.command = command

        self.hovered = False
        self._bg_images = {}  # Cache pre-rendered anti-aliased backgrounds

        # Pre-cache backgrounds for states to avoid rendering on the fly
        self.precache_backgrounds()

        self.draw()

        self.bind("<Enter>", self.on_enter)
        self.bind("<Leave>", self.on_leave)
        self.bind("<ButtonPress-1>", self.on_press)
        self.bind("<ButtonRelease-1>", self.on_release)

    def precache_backgrounds(self):
        # We render circular backgrounds using PIL draw supersampling for perfect anti-aliasing
        scale = 4
        canvas_size = self.size * scale

        states = [
            ("normal", "white", self.border_color),
            ("hover", self.hover_bg, self.border_color),
            ("active", self.active_bg, None),
            ("active_hover", self.active_hover, None)
        ]

        for name, fill_color, border_color in states:
            img = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)

            x0, y0 = 2, 2
            x1, y1 = canvas_size - 2, canvas_size - 2

            if border_color:
                b_width = 1 * scale
                draw.ellipse([x0, y0, x1, y1], fill=fill_color)
                draw.ellipse([x0, y0, x1, y1], outline=border_color, width=b_width)
            else:
                draw.ellipse([x0, y0, x1, y1], fill=fill_color)

            resized_img = img.resize((self.size, self.size), Image.Resampling.LANCZOS)
            self._bg_images[name] = ImageTk.PhotoImage(resized_img)

    def draw(self):
        # Determine which pre-cached background image to use
        if self.is_active:
            bg_name = "active_hover" if self.hovered else "active"
        else:
            bg_name = "hover" if self.hovered else "normal"

        bg_img = self._bg_images.get(bg_name)
        if bg_img:
            if getattr(self, "_bg_item_id", None) is None:
                self._bg_item_id = self.create_image(self.size // 2, self.size // 2, image=bg_img)
            else:
                self.itemconfig(self._bg_item_id, image=bg_img)

        # Draw the icon in the center
        if self.image:
            if getattr(self, "_icon_item_id", None) is None:
                self._icon_item_id = self.create_image(self.size // 2, self.size // 2, image=self.image)
            else:
                self.itemconfig(self._icon_item_id, image=self.image)

    def configure_button(self, image=None, is_active=None):
        if image is not None:
            self.image = image
        if is_active is not None:
            self.is_active = is_active
        self.draw()

    def update_active_colors(self, active_bg, active_hover):
        self.active_bg = active_bg
        self.active_hover = active_hover
        self.precache_backgrounds()
        self.draw()

    def on_enter(self, event):
        if self.hovered:
            return
        self.hovered = True
        self.draw()

    def on_leave(self, event):
        if not self.hovered:
            return
        self.hovered = False
        self.draw()

    def on_press(self, event):
        pass

    def on_release(self, event):
        if self.hovered and self.command:
            self.command()


class AnomalyWarningDialog(ctk.CTkToplevel):
    def __init__(self, parent, anomalous_labels, title="导出前数据复核提示", description=None, font_title=None, font_main=None):
        super().__init__(parent)
        self.parent = parent
        self.anomalous_labels = anomalous_labels
        self.result = False  # True if user ignores and continues, False if they return to edit

        self.title("导出前数据复核提示")
        self.configure(fg_color=("#FFFFFF", "#1A1D24"))
        self.geometry("540x440")
        self.resizable(True, True)
        self.transient(parent)
        self.grab_set()

        # Center the window
        self.update_idletasks()
        main_win = parent.winfo_toplevel()
        x = main_win.winfo_rootx() + (main_win.winfo_width() - 540) // 2
        y = main_win.winfo_rooty() + (main_win.winfo_height() - 440) // 2
        self.geometry(f"+{x}+{y}")

        # Fonts
        self.font_title = font_title or ctk.CTkFont(family="Microsoft YaHei", size=13, weight="bold")
        self.font_main = font_main or ctk.CTkFont(family="Microsoft YaHei", size=12)
        font_small = ctk.CTkFont(family="Microsoft YaHei", size=11)

        # Header banner
        header_frame = ctk.CTkFrame(self, fg_color="transparent")
        header_frame.pack(fill=tk.X, padx=30, pady=(15, 5))

        # Title in soft primary / neutral color
        ctk.CTkLabel(
            header_frame, text=title,
            font=self.font_title, text_color=("#1E293B", "#F1F5F9")
        ).pack(anchor="w")

        if description is None:
            description = (
                "以下条目检测到需复核的声学特征（如时间边界无效、短时音段或采样点含无基频帧等）。"
                "建议您在导出前确认这些条目，或保持现状直接继续导出："
            )
        ctk.CTkLabel(
            header_frame, text=description, font=font_small, text_color=("#4B5563", "#9CA3AF"),
            wraplength=480, justify="left"
        ).pack(anchor="w", pady=(4, 0))

        # Scrollable Warning List Frame with subtle border
        scroll_frame = ctk.CTkScrollableFrame(
            self, height=160, corner_radius=8, border_width=1,
            border_color=("#E2E8F0", "#374151"), fg_color=("#F8FAFC", "#1E293B")
        )
        scroll_frame._scrollbar.grid_configure(padx=(0, 9))
        scroll_frame.pack(fill=tk.BOTH, expand=True, padx=30, pady=5)

        for warning in self.anomalous_labels:
            row_frame = ctk.CTkFrame(scroll_frame, fg_color="transparent")
            row_frame.pack(fill=tk.X, padx=5, pady=3)

            # Bullet
            ctk.CTkLabel(
                row_frame, text="•", font=ctk.CTkFont(family="Microsoft YaHei", size=14, weight="bold"),
                text_color=("#64748B", "#94A3B8")
            ).pack(side=tk.LEFT, padx=(5, 5), anchor="n")

            ctk.CTkLabel(
                row_frame, text=warning, font=font_small, text_color=("#334155", "#E2E8F0"),
                wraplength=420, justify="left", anchor="w"
            ).pack(side=tk.LEFT, fill=tk.X, expand=True, anchor="w")

        # Count warning bar
        count_bar = ctk.CTkFrame(self, fg_color="transparent")
        count_bar.pack(fill=tk.X, padx=30, pady=(6, 0))
        ctk.CTkLabel(
            count_bar, text=f"共检出 {len(self.anomalous_labels)} 处需复核条目。",
            font=font_small, text_color=("#475569", "#94A3B8")
        ).pack(side=tk.LEFT)

        # Action Buttons Card
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(fill=tk.X, padx=30, pady=(10, 30))

        def on_ignore():
            self.result = True
            self.destroy()

        def on_cancel():
            self.result = False
            self.destroy()

        # Left button: Return to main window to fix anomalies
        ctk.CTkButton(
            btn_frame, text="返回修改", width=120, height=36, corner_radius=18,
            fg_color=("#3B82F6", "#2563EB"), text_color="#FFFFFF", hover_color=("#2563EB", "#1D4ED8"),
            font=self.font_main, command=on_cancel
        ).pack(side=tk.LEFT)

        # Right button: Keep status quo and continue export (secondary button style)
        ctk.CTkButton(
            btn_frame, text="保持现状并继续导出", width=160, height=36, corner_radius=18,
            fg_color=("#F3F4F6", "#374151"), text_color=("#374151", "#E5E7EB"), hover_color=("#E5E7EB", "#4B5563"),
            border_width=1, border_color=("#CBD5E1", "#4B5563"),
            font=self.font_main, command=on_ignore
        ).pack(side=tk.RIGHT)


class ExclusionReasonDialog(ctk.CTkToplevel):
    def __init__(self, parent, current_reason, font_title=None, font_main=None):
        super().__init__(parent)
        self.title("PhonTracer语音学标注与声学分析系统 V1.0 - 填写忽略原因")
        self.geometry("520x300")
        self.resizable(False, False)
        self.configure(fg_color=("#F9FAFB", "#1A1D24"))
        self.transient(parent)
        self.grab_set()

        self.result = None

        # Center the window
        self.update_idletasks()
        main_win = parent.winfo_toplevel()
        x = main_win.winfo_rootx() + (main_win.winfo_width() - 520) // 2
        y = main_win.winfo_rooty() + (main_win.winfo_height() - 300) // 2
        self.geometry(f"+{x}+{y}")

        self.font_title = font_title or ctk.CTkFont(family="Microsoft YaHei", size=13, weight="bold")
        self.font_main = font_main or ctk.CTkFont(family="Microsoft YaHei", size=12)

        # Top accent stripe
        accent = ctk.CTkFrame(self, height=4, corner_radius=0, fg_color="#3B82F6")
        accent.pack(fill=tk.X, side=tk.TOP)

        # Title
        ctk.CTkLabel(
            self, text="请选择忽略原因分类，并可补充具体原因：",
            font=self.font_title, text_color=("#111827", "#F9FAFB")
        ).pack(anchor="w", padx=24, pady=(14, 4))
        ctk.CTkLabel(
            self, text="二级原因可以不选；未选择时将直接使用上方分类名称。",
            font=self.font_main, text_color=("#6B7280", "#9CA3AF")
        ).pack(anchor="w", padx=24, pady=(0, 8))

        self.reason_var = ctk.StringVar(value="")
        self.detail_var = ctk.StringVar(value=EXCLUSION_REASON_DETAIL_PLACEHOLDER)
        common_reasons = list(EXCLUSION_REASON_CATEGORIES) + [EXCLUSION_REASON_CUSTOM_CATEGORY]

        # 2×3 radio button grid
        radio_outer = ctk.CTkFrame(self, fg_color="transparent")
        radio_outer.pack(fill=tk.X, padx=24, pady=(0, 8))
        radio_outer.columnconfigure(0, weight=1)
        radio_outer.columnconfigure(1, weight=1)

        rb_kwargs = dict(
            variable=self.reason_var,
            font=self.font_main,
            text_color=("#374151", "#D1D5DB"),
        )
        for idx, reason in enumerate(common_reasons):
            row, col = divmod(idx, 2)
            ctk.CTkRadioButton(
                radio_outer, text=reason, value=reason, **rb_kwargs
            ).grid(row=row, column=col, sticky="w", padx=8, pady=5)

        reason_input_frame = ctk.CTkFrame(self, fg_color="transparent")
        reason_input_frame.pack(fill=tk.X, padx=24, pady=(0, 14))
        self.detail_menu = ctk.CTkOptionMenu(
            reason_input_frame,
            values=[EXCLUSION_REASON_CATEGORY_PLACEHOLDER],
            variable=self.detail_var,
            font=self.font_main,
            height=36,
            corner_radius=18,
            fg_color=("#F3F4F6", "#262930"),
            button_color=("#F3F4F6", "#262930"),
            button_hover_color=("#E5E7EB", "#374151"),
            text_color=("#111827", "#F9FAFB"),
            dropdown_fg_color=("#FFFFFF", "#262930"),
            dropdown_text_color=("#111827", "#F9FAFB"),
            dropdown_hover_color=("#EFF6FF", "#374151"),
        )
        apply_custom_dropdown_arrow(self.detail_menu)
        self.entry_custom = ctk.CTkEntry(
            reason_input_frame,
            font=self.font_main,
            height=36,
            corner_radius=18,
            placeholder_text="请输入自定义原因...",
            fg_color=("#FFFFFF", "#262930"),
            border_color=("#D1D5DB", "#374151"),
            text_color=("#111827", "#F9FAFB"),
        )

        def update_reason_input(*_):
            category = self.reason_var.get()
            if category == EXCLUSION_REASON_CUSTOM_CATEGORY:
                self.detail_menu.pack_forget()
                self.entry_custom.pack(fill=tk.X)
                return
            self.entry_custom.pack_forget()
            self.detail_menu.pack(fill=tk.X)
            if category in EXCLUSION_REASON_CATEGORIES:
                values = [EXCLUSION_REASON_DETAIL_PLACEHOLDER] + EXCLUSION_REASON_CATEGORIES[category]
                self.detail_menu.configure(values=values, state="normal")
                if self.detail_var.get() not in values:
                    self.detail_var.set(EXCLUSION_REASON_DETAIL_PLACEHOLDER)
            else:
                self.detail_menu.configure(values=[EXCLUSION_REASON_CATEGORY_PLACEHOLDER], state="disabled")
                self.detail_var.set(EXCLUSION_REASON_CATEGORY_PLACEHOLDER)

        self.reason_var.trace_add("write", update_reason_input)
        initial_category, initial_detail, initial_custom = parse_exclusion_reason(current_reason)
        self.reason_var.set(initial_category)
        if initial_category in EXCLUSION_REASON_CATEGORIES:
            self.detail_var.set(initial_detail)
        elif initial_category == EXCLUSION_REASON_CUSTOM_CATEGORY:
            self.entry_custom.insert(0, initial_custom)
        else:
            update_reason_input()

        # Buttons row  (cancel left, confirm right — both pill, both blue)
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(fill=tk.X, padx=24, pady=(4, 20))

        def on_confirm():
            self.result = format_exclusion_reason(
                self.reason_var.get(),
                self.detail_var.get(),
                self.entry_custom.get(),
            )
            self.destroy()

        def on_cancel():
            self.result = None
            self.destroy()

        _cancel_kw = dict(
            height=38, corner_radius=19, font=self.font_main,
            fg_color=("#F1F5F9", "#334155"),
            text_color=("#475569", "#E2E8F0"),
            hover_color=("#E2E8F0", "#475569"),
            border_width=1, border_color=("#CBD5E1", "#475569"),
        )
        ctk.CTkButton(
            btn_frame, text="取消", width=100, command=on_cancel, **_cancel_kw
        ).pack(side=tk.LEFT)
        ctk.CTkButton(
            btn_frame, text="确定", width=100, command=on_confirm,
            height=38, corner_radius=19, font=self.font_main,
            fg_color=("#3B82F6", "#2563EB"),
            text_color="#FFFFFF",
            hover_color=("#2563EB", "#1D4ED8"),
        ).pack(side=tk.RIGHT)


class ItemPropertiesDialog(ctk.CTkToplevel):
    def __init__(self, parent, item_id, item, app_state_params, font_title=None, font_main=None):
        super().__init__(parent)
        self.parent = parent
        self.item_id = item_id
        self.item = item
        self.app_state_params = app_state_params

        self.title("条目属性")
        self.geometry("700x580")
        self.resizable(True, True)
        self.configure(fg_color=("#F9FAFB", "#1A1D24"))
        self.transient(parent)
        self.grab_set()

        # Center the window
        self.update_idletasks()
        main_win = parent.winfo_toplevel()
        x = main_win.winfo_rootx() + (main_win.winfo_width() - 700) // 2
        y = main_win.winfo_rooty() + (main_win.winfo_height() - 580) // 2
        self.geometry(f"+{x}+{y}")

        self.font_title = font_title or ctk.CTkFont(family="Microsoft YaHei", size=13, weight="bold")
        self.font_main = font_main or ctk.CTkFont(family="Microsoft YaHei", size=12)

        # Top accent stripe
        accent = ctk.CTkFrame(self, height=4, corner_radius=0, fg_color="#3B82F6")
        accent.pack(fill=tk.X, side=tk.TOP)

        # Scrollable container for properties
        scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        scroll.pack(fill=tk.BOTH, expand=True, padx=22, pady=(14, 10))

        # Add sections
        self._add_section(scroll, "基本信息", self._data_summary_rows())
        self._add_section(scroll, "字表元数据", self._wordlist_rows())
        self._add_section(scroll, "边界与切分", self._boundary_rows())
        self._add_section(scroll, "声学参数", self._acoustic_rows())

        # Buttons frame
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(fill=tk.X, padx=22, pady=(0, 18))

        # Close button on the right
        ctk.CTkButton(
            btn_frame,
            text="关闭",
            width=96,
            height=34,
            corner_radius=17,
            font=self.font_main,
            fg_color=("#E5E7EB", "#374151"),
            hover_color=("#D1D5DB", "#4B5563"),
            text_color=("#374151", "#E5E7EB"),
            command=self.destroy
        ).pack(side=tk.RIGHT)

        # Copy button to the left of Close button
        self.btn_copy = ctk.CTkButton(
            btn_frame,
            text="复制参数",
            width=96,
            height=34,
            corner_radius=17,
            font=self.font_main,
            fg_color=("#3B82F6", "#2563EB"),
            hover_color=("#2563EB", "#1D4ED8"),
            text_color="#FFFFFF",
            command=self._copy_to_clipboard
        )
        self.btn_copy.pack(side=tk.RIGHT, padx=(0, 10))

    def _add_section(self, parent, title, rows):
        card = ctk.CTkFrame(parent, fg_color=("#F9FAFB", "#262930"), corner_radius=8, border_width=1, border_color=("#E5E7EB", "#374151"))
        card.pack(fill=tk.X, pady=7)
        ctk.CTkLabel(card, text=title, font=self.font_title, text_color=("#111827", "#F9FAFB")).pack(anchor="w", padx=14, pady=(10, 4))

        grid_frame = ctk.CTkFrame(card, fg_color="transparent")
        grid_frame.pack(fill=tk.X, padx=14, pady=(0, 6))
        grid_frame.grid_columnconfigure(0, weight=0)
        grid_frame.grid_columnconfigure(1, weight=1, uniform="val_cols")
        grid_frame.grid_columnconfigure(2, weight=0)
        grid_frame.grid_columnconfigure(3, weight=1, uniform="val_cols")

        val_labels = []
        for i, (label, value) in enumerate(rows):
            r = i // 2
            c = (i % 2) * 2

            lbl_padx = (10, 5) if c == 2 else (0, 5)
            lbl = ctk.CTkLabel(grid_frame, text=label, width=110, anchor="w", font=self.font_main, text_color=("#6B7280", "#9CA3AF"))
            lbl.grid(row=r, column=c, sticky="w", padx=lbl_padx, pady=2)

            val_text = self._empty_to_dash(value)
            val = ctk.CTkLabel(grid_frame, text=val_text, anchor="w", justify="left", font=self.font_main, text_color=("#1F2937", "#E5E7EB"), wraplength=200)
            val.grid(row=r, column=c+1, sticky="ew", padx=(0, 10), pady=2)
            val_labels.append(val)

        last_col_width = [None]

        def on_configure(event):
            if event.widget == grid_frame:
                col_width = (event.width - 220 - 30) // 2
                if col_width < 100:
                    col_width = 100
                if col_width == last_col_width[0]:
                    return
                last_col_width[0] = col_width
                for v_lbl in val_labels:
                    v_lbl.configure(wraplength=col_width)

        grid_frame.bind("<Configure>", on_configure)
        ctk.CTkFrame(card, height=6, fg_color="transparent").pack(fill=tk.X)

    def _format_path(self, path):
        if not path:
            return ""
        return str(path).replace("\\", "\\​").replace("/", "/​")

    def _data_summary_rows(self):
        return [
            ("内部 ID", self.item_id),
            ("标签", self.item.get('label', '')),
            ("组别", self.item.get('group', '')),
            ("文件路径", self._format_path(self.item.get('path', ''))),
            ("分析模式", self._value_with_source('analysis_mode', 'f0')),
            ("忽略导出", self._yes_no(self.item.get('is_excluded', False))),
            ("忽略原因", self.item.get('exclusion_reason', '')),
            ("忽略异常提示", self._yes_no(self.item.get('ignore_warnings', False))),
            ("手动修改", self._yes_no(self.item.get('is_manual_edited', False))),
        ]

    def _boundary_rows(self):
        return [
            ("开始时间", f"{self._fmt_num(self.item.get('start'))} s" if self.item.get('start') is not None else ""),
            ("结束时间", f"{self._fmt_num(self.item.get('end'))} s" if self.item.get('end') is not None else ""),
            ("持续时长", self._duration_text()),
            ("内部切分点", self._fmt_sequence(self.item.get('inner_splits'))),
            ("字符边界", self._fmt_sequence(self.item.get('chars_bounds'))),
        ]

    def _wordlist_rows(self):
        meta = self.item.get('item_meta') or {}
        meta_text = "；".join([f"{k}={v}" for k, v in meta.items()]) if isinstance(meta, dict) else str(meta)
        return [
            ("字表版本", self.item.get('wordlist_version', 'v1')),
            ("字表名称", self.item.get('wordlist_title', '')),
            ("组备注", self.item.get('group_note', '')),
            ("组 tag", "；".join(str(v) for v in (self.item.get('group_tags', []) or []))),
            ("词项备注", self.item.get('item_note', '')),
            ("词项 tag", "；".join(str(v) for v in (self.item.get('item_tags', []) or []))),
            ("别名", "；".join(str(v) for v in (self.item.get('item_aliases', []) or []))),
            ("元数据来源", self.item.get('metadata_source', '')),
            ("自定义字段", meta_text),
        ]

    def _acoustic_rows(self):
        return [
            ("基频下限", self._value_with_source('pitch_floor', 75.0)),
            ("基频上限", self._value_with_source('pitch_ceiling', 600.0)),
            ("发音阈值", self._value_with_source('voicing_threshold', 0.25)),
            ("共振峰最大值", self._value_with_source('formant_max_hz', 5500.0)),
            ("共振峰个数", self._value_with_source('formant_count', 5)),
            ("共振峰窗长", self._value_with_source('formant_window_length', 0.025)),
            ("预加重系数", self._value_with_source('formant_pre_emphasis', 50.0)),
            ("显示/导出 F3", self._value_with_source_bool('show_f3', False)),
        ]

    def _value_with_source_bool(self, key, default):
        val = self.item.get(key) if key in self.item else self.app_state_params.get(key, default)
        source = " (条目自定)" if key in self.item else " (发音人默认)"
        return f"{'是' if bool(val) else '否'}{source}"

    def _empty_to_dash(self, value):
        text = str(value) if value is not None else ""
        return text if text else "--"

    def _yes_no(self, value):
        return "是" if bool(value) else "否"

    def _fmt_num(self, value):
        if value is None:
            return ""
        try:
            return f"{float(value):.6f}"
        except Exception:
            return str(value)

    def _fmt_sequence(self, value, max_items=6):
        if value is None:
            return ""
        try:
            seq = value.tolist() if hasattr(value, "tolist") else list(value)
        except Exception:
            return str(value)
        shown = seq[:max_items]
        suffix = f" ... 共 {len(seq)} 项" if len(seq) > max_items else ""
        return f"{shown}{suffix}"

    def _value_with_source(self, key, default):
        if key in self.item:
            return f"{self.item.get(key)} (条目自定)"
        return f"{self.app_state_params.get(key, default)} (发音人默认)"

    def _duration_text(self):
        start = self.item.get('start')
        end = self.item.get('end')
        if start is None or end is None:
            return ""
        return f"{self._fmt_num(end - start)} s"

    def _copy_to_clipboard(self):
        sections = [
            ("【基本信息】", self._data_summary_rows()),
            ("【字表元数据】", self._wordlist_rows()),
            ("【边界与切分】", self._boundary_rows()),
            ("【声学参数】", self._acoustic_rows())
        ]

        lines = []
        for sec_title, rows in sections:
            lines.append(sec_title)
            for label, value in rows:
                lines.append(f"{label}: {self._empty_to_dash(value)}")
            lines.append("")

        text = "\n".join(lines).strip()

        self.clipboard_clear()
        self.clipboard_append(text)
        self.update()

        old_text = self.btn_copy.cget("text")
        self.btn_copy.configure(text="已复制")
        self.after(1500, lambda: self.btn_copy.configure(text=old_text))


