import os
import sys
import webbrowser
import tkinter as tk
import customtkinter as ctk
from PIL import Image
from modules.version import __version__, APP_NAME
from modules.core.paths import get_asset_path, get_exe_dir, resolve_sibling_command

class AboutDialog(ctk.CTkToplevel):
    def __init__(self, parent, check_update_callback):
        super().__init__(parent)
        self.parent = parent
        self.check_update_callback = check_update_callback
        
        self.title(f"{APP_NAME}语音学标注与声学分析系统 V{__version__} - 关于")
        self.resizable(False, False)
        
        # 居中显示
        width, height = 460, 390
        screen_width = self.winfo_screenwidth()
        screen_height = self.winfo_screenheight()
        x = (screen_width - width) // 2
        y = (screen_height - height) // 2
        self.geometry(f"{width}x{height}+{x}+{y}")
        
        self.configure(fg_color=("#F8FAFC", "#0F172A"))  # 浅色模式纯白，暗色模式深海蓝
        
        # 模态对话框
        self.transient(parent)
        self.grab_set()
        self.focus_set()
        
        # 顶部装饰线条
        accent_strip = ctk.CTkFrame(self, height=4, fg_color="#2563EB", corner_radius=0)
        accent_strip.pack(fill="x", side="top")
        
        self.setup_ui()

    def setup_ui(self):
        # 1. 主卡片容器
        card = ctk.CTkFrame(self, fg_color=("#FFFFFF", "#262930"), corner_radius=12, border_width=1, border_color=("#E5E7EB", "#374151"))
        card.pack(fill="both", expand=True, padx=20, pady=(15, 20))
        
        # 2. Logo 显示
        logo_w, logo_h = 75, 75
        logo_img = None
        
        logo_path = get_asset_path("icon.png")
        if not os.path.exists(logo_path):
            logo_path = get_asset_path("logo.png")
            
        if os.path.exists(logo_path):
            try:
                img = Image.open(logo_path)
                logo_img = ctk.CTkImage(light_image=img, dark_image=img, size=(logo_w, logo_h))
            except Exception:
                pass
                
        if logo_img:
            lbl_logo = ctk.CTkLabel(card, text="", image=logo_img)
            lbl_logo.pack(pady=(15, 5))
        else:
            lbl_logo = ctk.CTkLabel(
                card, 
                text=APP_NAME[0], 
                font=ctk.CTkFont(family="Segoe UI Black", size=48, weight="bold"), 
                text_color="#2563EB"
            )
            lbl_logo.pack(pady=(15, 5))
            
        # 3. 软件名称与版本
        lbl_name = ctk.CTkLabel(
            card, 
            text=APP_NAME, 
            font=ctk.CTkFont(family="Arial", size=24, weight="bold"),
            text_color=("#111827", "#F9FAFB")
        )
        lbl_name.pack()
        
        lbl_desc = ctk.CTkLabel(
            card, 
            text="语音学标注与声学分析系统 | Phonetic Annotation & Acoustic Analysis System",
            font=ctk.CTkFont(family="Microsoft YaHei", size=12),
            text_color=("#6B7280", "#9CA3AF")
        )
        lbl_desc.pack(pady=(2, 5))
        
        # 版本号气泡
        ver_frame = ctk.CTkFrame(card, fg_color=("#EFF6FF", "#1E293B"), corner_radius=12, height=24)
        ver_frame.pack(pady=(0, 10))
        lbl_ver = ctk.CTkLabel(
            ver_frame, 
            text=f"Version V{__version__}",
            font=ctk.CTkFont(family="Consolas", size=11, weight="bold"),
            text_color=("#2563EB", "#60A5FA"),
            padx=10,
            pady=2
        )
        lbl_ver.pack()

        # 简介文本
        lbl_intro = ctk.CTkLabel(
            card,
            text="PhonTracer 是一款专为语言学研究和方言调查设计的语音学标注与声学分析系统。\n支持多音轨提取、基频参数等分点运算及声谱图交互式调整。",
            font=ctk.CTkFont(family="Microsoft YaHei", size=12),
            text_color=("#4B5563", "#D1D5DB"),
            justify="center"
        )
        lbl_intro.pack(padx=20, pady=(0, 10))

        # 扩展套件快速启动入口
        suite_frame = ctk.CTkFrame(card, fg_color="transparent")
        suite_frame.pack(fill="x", padx=30, pady=(0, 5))
        
        btn_toolkit = ctk.CTkButton(
            suite_frame,
            text="🛠️ 启动工具箱",
            height=32,
            corner_radius=16,
            fg_color=("#F3F4F6", "#374151"),
            text_color=("#1F2937", "#E5E7EB"),
            hover_color=("#E5E7EB", "#4B5563"),
            font=ctk.CTkFont(family="Microsoft YaHei", size=12),
            command=self.launch_toolkit
        )
        btn_toolkit.pack(side="left", expand=True, padx=5, fill="x")
        
        btn_cli = ctk.CTkButton(
            suite_frame,
            text="💻 启动命令行界面",
            height=32,
            corner_radius=16,
            fg_color=("#F3F4F6", "#374151"),
            text_color=("#1F2937", "#E5E7EB"),
            hover_color=("#E5E7EB", "#4B5563"),
            font=ctk.CTkFont(family="Microsoft YaHei", size=12),
            command=self.launch_cli
        )
        btn_cli.pack(side="left", expand=True, padx=5, fill="x")

        # 分割线
        sep = ctk.CTkFrame(card, height=1, fg_color=("#E5E7EB", "#374151"))
        sep.pack(fill="x", padx=30, pady=5)

        # 4. 底部动作区
        actions_frame = ctk.CTkFrame(card, fg_color="transparent")
        actions_frame.pack(fill="x", padx=20, pady=(5, 15))
        
        # 手册按钮 (图标化或高亮)
        btn_manual = ctk.CTkButton(
            actions_frame,
            text="📖 使用手册",
            width=110,
            height=32,
            corner_radius=16,
            fg_color=("#F3F4F6", "#374151"),
            text_color=("#1F2937", "#E5E7EB"),
            hover_color=("#E5E7EB", "#4B5563"),
            font=ctk.CTkFont(family="Microsoft YaHei", size=12),
            command=self.open_manual
        )
        btn_manual.pack(side="left", expand=True, padx=5)
        
        # Github 按钮
        btn_github = ctk.CTkButton(
            actions_frame,
            text="🌐 GitHub 项目",
            width=110,
            height=32,
            corner_radius=16,
            fg_color=("#F3F4F6", "#374151"),
            text_color=("#1F2937", "#E5E7EB"),
            hover_color=("#E5E7EB", "#4B5563"),
            font=ctk.CTkFont(family="Microsoft YaHei", size=12),
            command=self.open_github
        )
        btn_github.pack(side="left", expand=True, padx=5)
        
        # 检查更新按钮
        btn_update = ctk.CTkButton(
            actions_frame,
            text="🔄 检查更新",
            width=110,
            height=32,
            corner_radius=16,
            fg_color="#2563EB",
            text_color="white",
            hover_color="#1D4ED8",
            font=ctk.CTkFont(family="Microsoft YaHei", size=12, weight="bold"),
            command=self.check_update
        )
        btn_update.pack(side="left", expand=True, padx=5)

    def open_github(self):
        webbrowser.open("https://github.com/KasumiKitsune/PhonTracer")

    def check_update(self):
        # 关闭当前关于窗口并触发更新检测
        self.destroy()
        if self.check_update_callback:
            # 稍作延迟以待当前模态窗口销毁并恢复主窗口交互
            self.parent.after(100, lambda: self.check_update_callback(is_manual=True))

    def open_manual(self):
        manual_path = get_asset_path("manual", "manual.html")
        if os.path.exists(manual_path):
            webbrowser.open(f"file:///{manual_path.replace(os.sep, '/')}")
        else:
            # 如果不存在，尝试给出友好警告
            tk.messagebox.showwarning("提示", "使用手册正在编写中，敬请期待！", parent=self)

    def launch_toolkit(self):
        import subprocess
        cmd = resolve_sibling_command("Toolkit")
        if cmd:
            subprocess.Popen(cmd)
        else:
            expected = os.path.join(str(get_exe_dir()), "Toolkit.exe" if getattr(sys, "frozen", False) else "toolkit.py")
            tk.messagebox.showerror("错误", f"未找到工具箱程序：{expected}", parent=self)

    def launch_cli(self):
        import subprocess
        cmd = resolve_sibling_command("PhonTracerCLI")
        if cmd:
            kwargs = {}
            if sys.platform == 'win32':
                kwargs['creationflags'] = subprocess.CREATE_NEW_CONSOLE
            subprocess.Popen(cmd, **kwargs)
        else:
            expected = os.path.join(str(get_exe_dir()), "PhonTracerCLI.exe" if getattr(sys, "frozen", False) else "cli.py")
            tk.messagebox.showerror("错误", f"未找到命令行程序：{expected}", parent=self)
