import tkinter as tk
import customtkinter as ctk

class FormantDetectionDialog(ctk.CTkToplevel):
    def __init__(self, parent, app, p50, recommended_preset):
        super().__init__(parent)
        self.parent = parent
        self.app = app
        self.p50 = p50
        self.recommended_preset = recommended_preset

        self.title("估计共振峰分析参数与建议预设")
        self.resizable(False, False)

        # 居中显示
        width, height = 560, 600
        screen_width = self.winfo_screenwidth()
        screen_height = self.winfo_screenheight()
        x = (screen_width - width) // 2
        y = (screen_height - height) // 2
        self.geometry(f"{width}x{height}+{x}+{y}")

        self.configure(fg_color="#F3F4F6")  # 浅灰底色

        # 模态对话框
        self.transient(parent)
        self.grab_set()
        self.focus_set()

        # 顶部装饰线条
        accent_strip = ctk.CTkFrame(self, height=4, fg_color="#10B981", corner_radius=0)
        accent_strip.pack(fill="x", side="top")

        self.font_title = ctk.CTkFont(family="Microsoft YaHei", size=15, weight="bold")
        self.font_subtitle = ctk.CTkFont(family="Microsoft YaHei", size=13, weight="bold")
        self.font_main = ctk.CTkFont(family="Microsoft YaHei", size=13)
        self.font_small = ctk.CTkFont(family="Microsoft YaHei", size=12)

        self.setup_ui()

    def setup_ui(self):
        # 主卡片容器
        card = ctk.CTkFrame(self, fg_color="white", corner_radius=12, border_width=1, border_color="#E5E7EB")
        card.pack(fill="both", expand=True, padx=20, pady=(15, 15))

        # 头部标题
        lbl_title = ctk.CTkLabel(
            card,
            text="基于当前基频估计共振峰参数",
            font=self.font_title,
            text_color="#111827"
        )
        lbl_title.pack(pady=(12, 10))

        # F0 特征网格
        features_frame = ctk.CTkFrame(card, fg_color="#F9FAFB", corner_radius=8, border_width=1, border_color="#F3F4F6")
        features_frame.pack(fill="x", padx=15, pady=(0, 10))

        lbl_median_title = ctk.CTkLabel(features_frame, text="估计的基频中位数 (P50): ", font=self.font_small, text_color="#6B7280")
        lbl_median_title.pack(side="left", padx=(15, 5), pady=8)
        lbl_median_val = ctk.CTkLabel(features_frame, text=f"{self.p50:.1f} Hz", font=self.font_subtitle, text_color="#111827")
        lbl_median_val.pack(side="left", padx=5, pady=8)

        # 建议与简短理由横条
        reason_frame = ctk.CTkFrame(card, fg_color="#ECFDF5", corner_radius=8, border_width=1, border_color="#A7F3D0")
        reason_frame.pack(fill="x", padx=15, pady=(0, 15))
        
        reason_text = (
            f"发音人的基频中位数约为 {int(round(self.p50))} Hz。\n"
            f"根据声腔生理特征，系统推荐选择【{self.recommended_preset}】预设："
        )
        lbl_reason = ctk.CTkLabel(
            reason_frame,
            text=reason_text,
            font=self.font_main,
            text_color="#065F46",
            justify="left"
        )
        lbl_reason.pack(padx=15, pady=8, fill="x")

        # 三个预设选项
        options = [
            {
                "name": "成年男性 (Adult Male)",
                "max_hz": 5000.0,
                "win_len": 0.025,
                "pre_emphasis": 50.0,
                "desc": "最大频率: 5000Hz | 窗长: 0.025s | 预加重: 50Hz",
                "key": "成年男性"
            },
            {
                "name": "成年女性 (Adult Female)",
                "max_hz": 5500.0,
                "win_len": 0.025,
                "pre_emphasis": 50.0,
                "desc": "最大频率: 5500Hz | 窗长: 0.025s | 预加重: 50Hz",
                "key": "成年女性"
            },
            {
                "name": "儿童 / 高音 (Child / High Pitch)",
                "max_hz": 6500.0,
                "win_len": 0.025,
                "pre_emphasis": 50.0,
                "desc": "最大频率: 6500Hz | 窗长: 0.025s | 预加重: 50Hz",
                "key": "儿童"
            }
        ]

        for opt in options:
            is_rec = (opt["key"] == self.recommended_preset)
            opt_frame = ctk.CTkFrame(card, fg_color="white", corner_radius=10, border_width=1, border_color="#E5E7EB" if not is_rec else "#10B981")
            opt_frame.pack(fill="x", padx=15, pady=5)

            # 左侧：信息
            info_frame = ctk.CTkFrame(opt_frame, fg_color="transparent")
            info_frame.pack(side="left", fill="both", expand=True, padx=12, pady=10)

            title_text = opt["name"]
            if is_rec:
                title_text += " (系统推荐)"
            
            lbl_opt_title = ctk.CTkLabel(
                info_frame,
                text=title_text,
                font=self.font_subtitle,
                text_color="#1F2937" if not is_rec else "#059669"
            )
            lbl_opt_title.pack(anchor="w")

            lbl_opt_desc = ctk.CTkLabel(
                info_frame,
                text=opt["desc"],
                font=ctk.CTkFont(family="Consolas", size=12, weight="bold"),
                text_color="#111827"
            )
            lbl_opt_desc.pack(anchor="w", pady=(4, 2))

            # 右侧：应用按钮
            if is_rec:
                btn_color = "#10B981"
                hover_color = "#059669"
                text_color = "white"
                btn_text = "应用此设置"
            else:
                btn_color = "#F3F4F6"
                hover_color = "#E5E7EB"
                text_color = "#1F2937"
                btn_text = "应用"

            btn_apply = ctk.CTkButton(
                opt_frame,
                text=btn_text,
                font=self.font_subtitle,
                width=110,
                height=34,
                corner_radius=17,
                fg_color=btn_color,
                hover_color=hover_color,
                text_color=text_color,
                command=lambda m=opt["max_hz"], w=opt["win_len"], p=opt["pre_emphasis"]: self.apply_and_close(m, w, p)
            )
            btn_apply.pack(side="right", padx=15, pady=15)

        # 底部取消按钮
        btn_cancel = ctk.CTkButton(
            card,
            text="取消",
            font=self.font_main,
            width=90,
            height=32,
            corner_radius=16,
            fg_color="#F3F4F6",
            text_color="#4B5563",
            hover_color="#E5E7EB",
            command=self.destroy
        )
        btn_cancel.pack(pady=(12, 10))

    def apply_and_close(self, max_hz, win_len, pre_emphasis):
        self.destroy()
        # 延迟触发以确保对话框已完全销毁且恢复主窗口交互
        self.app.root.after(50, lambda: self.app.apply_formant_bounds(max_hz, win_len, pre_emphasis))
