import tkinter as tk
import customtkinter as ctk

class F0DetectionDialog(ctk.CTkToplevel):
    def __init__(self, parent, app, p5, p10, p50, p90, p95, stable_count, stable_duration, cons_range, reco_range, fine_range, apply_scope="global"):
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
        self.cons_range = cons_range
        self.reco_range = reco_range
        self.fine_range = fine_range
        self.apply_scope = apply_scope

        self.title("估计 F0 分布与建议范围")
        self.resizable(False, False)

        # 居中显示
        width, height = 600, 600
        screen_width = self.winfo_screenwidth()
        screen_height = self.winfo_screenheight()
        x = (screen_width - width) // 2
        y = (screen_height - height) // 2
        self.geometry(f"{width}x{height}+{x}+{y}")

        self.configure(fg_color=("#FFFFFF", "#1A1D24"))  # 浅色模式纯白，暗色模式深海蓝

        # 模态对话框
        self.transient(parent)
        self.grab_set()
        self.focus_set()

        # 顶部装饰线条
        accent_strip = ctk.CTkFrame(self, height=4, fg_color="#3B82F6", corner_radius=0)
        accent_strip.pack(fill="x", side="top")

        self.font_title = ctk.CTkFont(family="Microsoft YaHei", size=15, weight="bold")
        self.font_subtitle = ctk.CTkFont(family="Microsoft YaHei", size=13, weight="bold")
        self.font_main = ctk.CTkFont(family="Microsoft YaHei", size=13)
        self.font_small = ctk.CTkFont(family="Microsoft YaHei", size=12)

        self.setup_ui()

    def setup_ui(self):
        # 主卡片容器
        card = ctk.CTkFrame(self, fg_color="transparent")
        card.pack(fill="both", expand=True, padx=20, pady=(15, 15))

        # 头部标题
        lbl_title = ctk.CTkLabel(
            card,
            text="基于当前音频估计 F0 分布",
            font=self.font_title,
            text_color=("#111827", "#F9FAFB")
        )
        lbl_title.pack(pady=(12, 10))

        # F0 主要特征网格
        features_frame = ctk.CTkFrame(card, fg_color=("#F9FAFB", "#262930"), corner_radius=8, border_width=1, border_color=("#E5E7EB", "#374151"))
        features_frame.pack(fill="x", padx=15, pady=(0, 10))

        # 用两列排布基本统计数据
        # 左侧
        lbl_dur_title = ctk.CTkLabel(features_frame, text="有声数据总量: ", font=self.font_small, text_color=("#6B7280", "#9CA3AF"))
        lbl_dur_title.grid(row=0, column=0, padx=(15, 5), pady=8, sticky="w")
        lbl_dur_val = ctk.CTkLabel(features_frame, text=f"{self.stable_duration:.2f} 秒 ({self.stable_count} 帧)", font=self.font_small, text_color=("#374151", "#E5E7EB"))
        lbl_dur_val.grid(row=0, column=1, padx=5, pady=8, sticky="w")

        # 右侧
        lbl_median_title = ctk.CTkLabel(features_frame, text="中位数 F0 (P50): ", font=self.font_small, text_color=("#6B7280", "#9CA3AF"))
        lbl_median_title.grid(row=0, column=2, padx=(30, 5), pady=8, sticky="w")
        lbl_median_val = ctk.CTkLabel(features_frame, text=f"{self.p50:.1f} Hz", font=self.font_small, text_color=("#374151", "#E5E7EB"))
        lbl_median_val.grid(row=0, column=3, padx=5, pady=8, sticky="w")

        # 主要分布区间 (P5 ~ P95)
        lbl_range_title = ctk.CTkLabel(features_frame, text="主要稳定区间: ", font=self.font_small, text_color=("#6B7280", "#9CA3AF"))
        lbl_range_title.grid(row=1, column=0, padx=(15, 5), pady=(0, 8), sticky="w")
        lbl_range_val = ctk.CTkLabel(features_frame, text=f"{self.p5:.1f} ~ {self.p95:.1f} Hz (P5 ~ P95)", font=self.font_small, text_color=("#374151", "#E5E7EB"))
        lbl_range_val.grid(row=1, column=1, columnspan=3, padx=5, pady=(0, 8), sticky="w")

        # 建议与简短理由横条
        reason_frame = ctk.CTkFrame(card, fg_color=("#EFF6FF", "#1E293B"), corner_radius=8, border_width=1, border_color=("#DBEAFE", "#2563EB"))
        reason_frame.pack(fill="x", padx=15, pady=(0, 15))
        
        reason_text = (
            f"检测到该发音人稳定 F0 主要分布在 {int(round(self.p5))}~{int(round(self.p95))} Hz，\n"
            f"中位数 {int(round(self.p50))} Hz。基于此估计，系统已生成以下三档范围参数选项："
        )
        lbl_reason = ctk.CTkLabel(
            reason_frame,
            text=reason_text,
            font=self.font_main,
            text_color=("#1E40AF", "#93C5FD"),
            justify="left"
        )
        lbl_reason.pack(padx=15, pady=8, fill="x")

        # 三个范围档位选项
        options = [
            {
                "name": "保守范围",
                "range": self.cons_range,
                "desc": "不容易漏掉真实 F0，适合初次检查。",
                "is_primary": False
            },
            {
                "name": "推荐范围",
                "range": self.reco_range,
                "desc": "平衡准确性和时间分辨率，适合一般研究导出。",
                "is_primary": True
            },
            {
                "name": "精细范围",
                "range": self.fine_range,
                "desc": "下限更高，适合确认过没有低 F0 后使用。",
                "is_primary": False
            }
        ]

        for opt in options:
            floor, ceiling = opt["range"]
            opt_frame = ctk.CTkFrame(card, fg_color=("#FFFFFF", "#262930"), corner_radius=10, border_width=1, border_color=("#E5E7EB", "#374151"))
            opt_frame.pack(fill="x", padx=15, pady=5)

            # 配置网格列权重，确保右侧应用按钮有固定宽度不被挤压，左侧信息区自适应
            opt_frame.grid_columnconfigure(0, weight=1)
            opt_frame.grid_columnconfigure(1, weight=0)
            opt_frame.grid_rowconfigure(0, weight=1)

            # 左侧：信息，在网格第0列，占用剩下的自适应空间
            info_frame = ctk.CTkFrame(opt_frame, fg_color="transparent")
            info_frame.grid(row=0, column=0, sticky="nsew", padx=12, pady=10)

            title_text = opt["name"]
            if opt["is_primary"]:
                title_text += " (系统推荐)"
            
            lbl_opt_title = ctk.CTkLabel(
                info_frame,
                text=title_text,
                font=self.font_subtitle,
                text_color=("#1F2937", "#E5E7EB") if not opt["is_primary"] else ("#2563EB", "#60A5FA")
            )
            lbl_opt_title.pack(anchor="w")

            lbl_opt_val = ctk.CTkLabel(
                info_frame,
                text=f"{floor} ~ {ceiling} Hz",
                font=ctk.CTkFont(family="Consolas", size=14, weight="bold"),
                text_color=("#111827", "#F9FAFB")
            )
            lbl_opt_val.pack(anchor="w", pady=(2, 2))

            lbl_opt_desc = ctk.CTkLabel(
                info_frame,
                text=opt["desc"],
                font=self.font_small,
                text_color=("#6B7280", "#9CA3AF")
            )
            lbl_opt_desc.pack(anchor="w")

            # 右侧：应用按钮，在网格第1列
            if opt["is_primary"]:
                btn_color = "#3B82F6"
                hover_color = "#2563EB"
                text_color = "white"
                btn_text = "应用此范围"
            else:
                btn_color = ("#F3F4F6", "#374151")
                hover_color = ("#E5E7EB", "#4B5563")
                text_color = ("#1F2937", "#E5E7EB")
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
                command=lambda f=floor, c=ceiling: self.apply_and_close(f, c)
            )
            btn_apply.grid(row=0, column=1, sticky="e", padx=15, pady=15)

        # 底部取消按钮
        btn_cancel = ctk.CTkButton(
            card,
            text="取消",
            font=self.font_main,
            width=90,
            height=32,
            corner_radius=16,
            fg_color=("#F3F4F6", "#374151"),
            text_color=("#4B5563", "#D1D5DB"),
            hover_color=("#E5E7EB", "#4B5563"),
            command=self.destroy
        )
        btn_cancel.pack(pady=(12, 10))

    def apply_and_close(self, floor, ceiling):
        self.destroy()
        # 延迟一下触发，以便弹窗已完全销毁且恢复主窗口交互
        self.app.root.after(50, lambda: self.app.apply_f0_bounds(floor, ceiling, scope=self.apply_scope))
