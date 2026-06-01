import os
import sys
import webbrowser
import tkinter as tk
import customtkinter as ctk
from PIL import Image
from modules.version import __version__, APP_NAME

class AboutDialog(ctk.CTkToplevel):
    def __init__(self, parent, check_update_callback):
        super().__init__(parent)
        self.parent = parent
        self.check_update_callback = check_update_callback
        
        self.title(f"关于 {APP_NAME}")
        self.resizable(False, False)
        
        # 居中显示
        width, height = 460, 390
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
        
        self.setup_ui()

    def setup_ui(self):
        # 1. 主卡片容器
        card = ctk.CTkFrame(self, fg_color=("#FFFFFF", "#262930"), corner_radius=12, border_width=1, border_color=("#E5E7EB", "#374151"))
        card.pack(fill="both", expand=True, padx=20, pady=(15, 20))
        
        # 2. Logo 显示
        logo_w, logo_h = 75, 75
        logo_img = None
        
        if hasattr(sys, '_MEIPASS'):
            base_dir = sys._MEIPASS
        else:
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        logo_path = os.path.join(base_dir, "assets", "icon.png")
        if not os.path.exists(logo_path):
            logo_path = os.path.join(base_dir, "assets", "logo.png")
            
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
                text_color="#FF2A6D"
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
            text="声调提取与分析工具 | Pitch Extraction & Analysis Tool", 
            font=ctk.CTkFont(family="Microsoft YaHei", size=12),
            text_color=("#6B7280", "#9CA3AF")
        )
        lbl_desc.pack(pady=(2, 5))
        
        # 版本号气泡
        ver_frame = ctk.CTkFrame(card, fg_color=("#EFF6FF", "#1E293B"), corner_radius=12, height=24)
        ver_frame.pack(pady=(0, 10))
        lbl_ver = ctk.CTkLabel(
            ver_frame, 
            text=f"Version {__version__}", 
            font=ctk.CTkFont(family="Consolas", size=11, weight="bold"),
            text_color=("#2563EB", "#60A5FA"),
            padx=10,
            pady=2
        )
        lbl_ver.pack()

        # 简介文本
        lbl_intro = ctk.CTkLabel(
            card,
            text="PhonTracer 是一款专为语言学研究和方言调查设计的声调分析软件。\n支持多音轨提取、基频参数等分点运算及声谱图交互式调整。",
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
            fg_color=("#3B82F6", "#2563EB"),
            text_color="white",
            hover_color=("#2563EB", "#1D4ED8"),
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
        if hasattr(sys, '_MEIPASS'):
            base_dir = sys._MEIPASS
        else:
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        manual_path = os.path.join(base_dir, "assets", "manual", "manual.html")
        if os.path.exists(manual_path):
            webbrowser.open(f"file:///{manual_path.replace(os.sep, '/')}")
        else:
            # 如果不存在，尝试给出友好警告
            tk.messagebox.showwarning("提示", "使用手册正在编写中，敬请期待！", parent=self)

    def launch_toolkit(self):
        import subprocess
        # 检查是否是在打包后的环境中运行
        if getattr(sys, 'frozen', False):
            base_dir = os.path.dirname(sys.executable)
            exe_path = os.path.join(base_dir, "Toolkit.exe")
            if os.path.exists(exe_path):
                subprocess.Popen([exe_path])
            else:
                tk.messagebox.showerror("错误", f"未找到工具箱可执行程序：{exe_path}", parent=self)
        else:
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            script_path = os.path.join(base_dir, "toolkit.py")
            if os.path.exists(script_path):
                subprocess.Popen([sys.executable, script_path])
            else:
                tk.messagebox.showerror("错误", f"未找到工具箱脚本：{script_path}", parent=self)

    def launch_cli(self):
        import subprocess
        # 检查是否是在打包后的环境中运行
        if getattr(sys, 'frozen', False):
            base_dir = os.path.dirname(sys.executable)
            exe_path = os.path.join(base_dir, "PhonTracerCLI.exe")
            if os.path.exists(exe_path):
                kwargs = {}
                if sys.platform == 'win32':
                    kwargs['creationflags'] = subprocess.CREATE_NEW_CONSOLE
                subprocess.Popen([exe_path], **kwargs)
            else:
                tk.messagebox.showerror("错误", f"未找到命令行接口可执行程序：{exe_path}", parent=self)
        else:
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            script_path = os.path.join(base_dir, "cli.py")
            if os.path.exists(script_path):
                python_exe = sys.executable
                if python_exe.endswith("pythonw.exe"):
                    python_exe = python_exe.replace("pythonw.exe", "python.exe")
                kwargs = {}
                if sys.platform == 'win32':
                    kwargs['creationflags'] = subprocess.CREATE_NEW_CONSOLE
                subprocess.Popen([python_exe, script_path], **kwargs)
            else:
                tk.messagebox.showerror("错误", f"未找到命令行脚本：{script_path}", parent=self)
