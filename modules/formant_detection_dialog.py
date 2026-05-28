import tkinter as tk
import customtkinter as ctk

class FormantDetectionDialog(ctk.CTkToplevel):
    def __init__(self, parent, app, voiced_duration, insufficient_data, reco_params, anti_params, fine_params):
        super().__init__(parent)
        self.parent = parent
        self.app = app
        self.voiced_duration = voiced_duration
        self.insufficient_data = insufficient_data
        self.reco_params = reco_params
        self.anti_params = anti_params
        self.fine_params = fine_params

        self.title("估计共振峰最佳参数与建议范围")
        self.resizable(False, False)

        # 居中显示
        width, height = 620, 580
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
            text="基于样本发音评估共振峰最佳参数",
            font=self.font_title,
            text_color="#111827"
        )
        lbl_title.pack(pady=(12, 10))

        # 基本统计数据
        features_frame = ctk.CTkFrame(card, fg_color="#F9FAFB", corner_radius=8, border_width=1, border_color="#F3F4F6")
        features_frame.pack(fill="x", padx=15, pady=(0, 10))

        lbl_dur_title = ctk.CTkLabel(features_frame, text="有效分析段时长: ", font=self.font_small, text_color="#6B7280")
        lbl_dur_title.grid(row=0, column=0, padx=(15, 5), pady=8, sticky="w")
        lbl_dur_val = ctk.CTkLabel(features_frame, text=f"{self.voiced_duration:.2f} 秒", font=self.font_small, text_color="#374151")
        lbl_dur_val.grid(row=0, column=1, padx=5, pady=8, sticky="w")

        # 建议与简短理由横条
        reason_frame = ctk.CTkFrame(card, fg_color="#ECFDF5", corner_radius=8, border_width=1, border_color="#A7F3D0")
        reason_frame.pack(fill="x", padx=15, pady=(0, 15))
        
        if self.insufficient_data:
            reason_text = "【警告】有效发音时长不足（低于 0.5s），以下推荐仅供参考。"
            reason_color = "#D97706"
            reason_frame.configure(fg_color="#FFFBEB", border_color="#FDE68A")
        else:
            reason_text = "系统已对发音片段的不同参数网格进行提取质量评分，\n现生成以下三档优化共振峰追踪参数："
            reason_color = "#047857"

        lbl_reason = ctk.CTkLabel(
            reason_frame,
            text=reason_text,
            font=self.font_main,
            text_color=reason_color,
            justify="left"
        )
        lbl_reason.pack(padx=15, pady=8, fill="x")

        # 三档推荐
        options = [
            {
                "id": "anti",
                "name": "抗错位档 (优先避免 F1/F2 swap)",
                "params": self.anti_params,
                "desc": "稍长的窗长与偏低上限频率，适合排查/抑制 F1 抢占 F2 问题。",
                "is_primary": False
            },
            {
                "id": "reco",
                "name": "系统推荐档 (综合评分最高)",
                "params": self.reco_params,
                "desc": "最均衡的质量评分与提取率，适合大部分元音绘图研究。",
                "is_primary": True
            },
            {
                "id": "fine",
                "name": "高分辨率档 (保留更多时间细节)",
                "params": self.fine_params,
                "desc": "较短窗长或略高频率，能在评分无显著退化下提供更多瞬态细节。",
                "is_primary": False
            }
        ]

        for opt in options:
            p_max_hz, p_count, p_win, p_pre, p_score = opt["params"]
            
            opt_frame = ctk.CTkFrame(card, fg_color="white", corner_radius=10, border_width=1, border_color="#E5E7EB")
            opt_frame.pack(fill="x", padx=15, pady=5)

            # 配置网格列权重，确保右侧应用按钮有固定宽度不被挤压，左侧信息区自适应
            opt_frame.grid_columnconfigure(0, weight=1)
            opt_frame.grid_columnconfigure(1, weight=0)
            opt_frame.grid_rowconfigure(0, weight=1)

            # 左侧信息区：在网格第0列，占用剩下的自适应空间
            info_frame = ctk.CTkFrame(opt_frame, fg_color="transparent")
            info_frame.grid(row=0, column=0, sticky="nsew", padx=12, pady=10)

            lbl_opt_title = ctk.CTkLabel(
                info_frame,
                text=opt["name"],
                font=self.font_subtitle,
                text_color="#1F2937" if not opt["is_primary"] else "#10B981"
            )
            lbl_opt_title.pack(anchor="w")

            param_text = f"最大频率: {int(p_max_hz)} Hz | 窗长: {p_win:.3f} s | 预加重: {int(p_pre)} Hz"
            lbl_opt_val = ctk.CTkLabel(
                info_frame,
                text=param_text,
                font=ctk.CTkFont(family="Consolas", size=12, weight="bold"),
                text_color="#111827"
            )
            lbl_opt_val.pack(anchor="w", pady=(2, 2))

            lbl_opt_desc = ctk.CTkLabel(
                info_frame,
                text=f"{opt['desc']} (质量得分: {p_score:.2f})",
                font=self.font_small,
                text_color="#6B7280",
                wraplength=380,
                justify="left"
            )
            lbl_opt_desc.pack(anchor="w")

            # 右侧应用按钮：在网格第1列
            if opt["is_primary"]:
                btn_color = "#10B981"
                hover_color = "#059669"
                text_color = "white"
                btn_text = "应用此参数"
            else:
                btn_color = "#F3F4F6"
                hover_color = "#E5E7EB"
                text_color = "#1F2937"
                btn_text = "应用"

            btn_apply = ctk.CTkButton(
                opt_frame,
                text=btn_text,
                font=self.font_subtitle,
                width=120,
                height=34,
                corner_radius=17,
                fg_color=btn_color,
                hover_color=hover_color,
                text_color=text_color,
                command=lambda h=p_max_hz, c=p_count, w=p_win, p=p_pre: self.apply_and_close(h, c, w, p)
            )
            btn_apply.grid(row=0, column=1, sticky="e", padx=15, pady=15)



    def apply_and_close(self, max_hz, count, window_length, pre_emphasis):
        self.destroy()
        # 异步应用参数，刷新界面
        self.app.root.after(50, lambda: self.app.apply_formant_params(max_hz, count, window_length, pre_emphasis))
