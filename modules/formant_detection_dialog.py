import tkinter as tk
import customtkinter as ctk

class FormantDetectionDialog(ctk.CTkToplevel):
    def __init__(self, parent, app, p5, p10, p50, p90, p95, stable_count, stable_duration, recommended_preset):
        super().__init__(parent)
        self.parent = parent
        self.app = app
        self.p5 = p5
        self.p10 = p10
        self.p50 = p50
        self.p90 = p90
        self.p95 = p95
        self.stable_count = stable_count
        self.stable_duration = stable_duration
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

        # 顶部装饰线条 - 绿色主题
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
            text="基于当前音频估计共振峰参数",
            font=self.font_title,
            text_color="#111827"
        )
        lbl_title.pack(pady=(12, 10))

        # 主要特征网格
        features_frame = ctk.CTkFrame(card, fg_color="#F9FAFB", corner_radius=8, border_width=1, border_color="#F3F4F6")
        features_frame.pack(fill="x", padx=15, pady=(0, 10))

        # 用两列排布基本统计数据
        # 左侧
        lbl_dur_title = ctk.CTkLabel(features_frame, text="有声数据总量: ", font=self.font_small, text_color="#6B7280")
        lbl_dur_title.grid(row=0, column=0, padx=(15, 5), pady=8, sticky="w")
        lbl_dur_val = ctk.CTkLabel(features_frame, text=f"{self.stable_duration:.2f} 秒 ({self.stable_count} 帧)", font=self.font_small, text_color="#374151")
        lbl_dur_val.grid(row=0, column=1, padx=5, pady=8, sticky="w")

        # 右侧
        lbl_median_title = ctk.CTkLabel(features_frame, text="中位数 F0 (P50): ", font=self.font_small, text_color="#6B7280")
        lbl_median_title.grid(row=0, column=2, padx=(30, 5), pady=8, sticky="w")
        lbl_median_val = ctk.CTkLabel(features_frame, text=f"{self.p50:.1f} Hz", font=self.font_small, text_color="#374151")
        lbl_median_val.grid(row=0, column=3, padx=5, pady=8, sticky="w")

        # 主要分布区间 (P5 ~ P95)
        lbl_range_title = ctk.CTkLabel(features_frame, text="主要稳定区间: ", font=self.font_small, text_color="#6B7280")
        lbl_range_title.grid(row=1, column=0, padx=(15, 5), pady=(0, 8), sticky="w")
        lbl_range_val = ctk.CTkLabel(features_frame, text=f"{self.p5:.1f} ~ {self.p95:.1f} Hz (P5 ~ P95)", font=self.font_small, text_color="#374151")
        lbl_range_val.grid(row=1, column=1, columnspan=3, padx=5, pady=(0, 8), sticky="w")

        # 建议与简短理由横条 - 绿色主题
        reason_frame = ctk.CTkFrame(card, fg_color="#ECFDF5", corner_radius=8, border_width=1, border_color="#A7F3D0")
        reason_frame.pack(fill="x", padx=15, pady=(0, 15))
        
        reason_text = (
            f"检测到该发音人稳定 F0 主要分布在 {int(round(self.p5))}~{int(round(self.p95))} Hz，\n"
            f"中位数 {int(round(self.p50))} Hz。基于此估计，系统已生成以下三档共振峰参数选项："
        )
        lbl_reason = ctk.CTkLabel(
            reason_frame,
            text=reason_text,
            font=self.font_main,
            text_color="#065F46",
            justify="left"
        )
        lbl_reason.pack(padx=15, pady=8, fill="x")

        # 三个预设档位选项 (越低越准确，越高越保守)
        options = [
            {
                "name": "精细范围",
                "max_hz": 5000.0,
                "win_len": 0.025,
                "pre_emphasis": 50.0,
                "desc": "分析上限较低，可极大压制共振峰错位与偏移，高元音识别更精准，适合中低音声部。",
                "key": "精细范围"
            },
            {
                "name": "推荐范围",
                "max_hz": 5500.0,
                "win_len": 0.025,
                "pre_emphasis": 50.0,
                "desc": "分析上限适中，平衡分析精度与声腔宽度，系统默认推荐，适合中高音声部。",
                "key": "推荐范围"
            },
            {
                "name": "保守范围",
                "max_hz": 6500.0,
                "win_len": 0.025,
                "pre_emphasis": 50.0,
                "desc": "分析上限较高，防止丢失高频共振峰，但低频带较易出现错位，适合极高音或儿童声部。",
                "key": "保守范围"
            }
        ]

        for opt in options:
            is_rec = (opt["key"] == self.recommended_preset)
            # 推荐项使用绿色高亮边框
            opt_frame = ctk.CTkFrame(card, fg_color="white", corner_radius=10, border_width=1, border_color="#10B981" if is_rec else "#E5E7EB")
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
                text_color="#059669" if is_rec else "#1F2937"
            )
            lbl_opt_title.pack(anchor="w")

            # 强调关键参数，使用 Consolas 字体
            lbl_opt_val = ctk.CTkLabel(
                info_frame,
                text=f"最大频率: {int(opt['max_hz'])} Hz",
                font=ctk.CTkFont(family="Consolas", size=14, weight="bold"),
                text_color="#111827"
            )
            lbl_opt_val.pack(anchor="w", pady=(2, 2))

            # 其它辅助参数和说明
            desc_text = f"窗长: {opt['win_len']}s | 预加重: {int(opt['pre_emphasis'])}Hz\n{opt['desc']}"
            lbl_opt_desc = ctk.CTkLabel(
                info_frame,
                text=desc_text,
                font=self.font_small,
                text_color="#6B7280",
                justify="left"
            )
            lbl_opt_desc.pack(anchor="w")

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
        # 延迟一下触发，以便弹窗已完全销毁且恢复主窗口交互
        self.app.root.after(50, lambda: self.app.apply_formant_bounds(max_hz, win_len, pre_emphasis))
