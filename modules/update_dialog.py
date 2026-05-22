import tkinter as tk
import customtkinter as ctk
import webbrowser
from modules.updater import save_ignored_version

class UpdateDialog(ctk.CTkToplevel):
    def __init__(self, parent, info, is_manual=False):
        super().__init__(parent)
        self.parent = parent
        self.info = info
        self.latest_version = info.get("latest_version", "")
        self.download_url = info.get("download_url", "")
        self.changelog = info.get("changelog", "")
        self.publish_date = info.get("publish_date", "")
        self.is_manual = is_manual

        self.title("检查更新")
        self.resizable(False, False)
        
        # 居中显示
        width, height = 480, 420
        screen_width = self.winfo_screenwidth()
        screen_height = self.winfo_screenheight()
        x = (screen_width - width) // 2
        y = (screen_height - height) // 2
        self.geometry(f"{width}x{height}+{x}+{y}")
        
        self.configure(fg_color="#F3F4F6")  # 与主程序一致的浅灰底色
        
        # 设为临时窗口并聚焦
        self.transient(parent)
        self.grab_set()  # 模态对话框
        self.focus_set()
        
        # 顶部装饰线条（主程序用的红/蓝偏现代科技风）
        accent_strip = ctk.CTkFrame(self, height=4, fg_color="#FF2A6D", corner_radius=0)
        accent_strip.pack(fill="x", side="top")
        
        self.setup_ui()

    def setup_ui(self):
        # 1. 顶部标题区域
        title_frame = ctk.CTkFrame(self, fg_color="transparent")
        title_frame.pack(fill="x", padx=25, pady=(20, 10))
        
        lbl_title = ctk.CTkLabel(
            title_frame, 
            text="发现新版本！", 
            font=ctk.CTkFont(family="Microsoft YaHei", size=18, weight="bold"),
            text_color="#111827",
            anchor="w"
        )
        lbl_title.pack(fill="x")
        
        # 版本比对信息
        from modules.version import __version__
        version_text = f"当前版本：v{__version__}   ➔   最新版本：{self.latest_version}"
        if self.publish_date:
            version_text += f" ({self.publish_date})"
            
        lbl_version = ctk.CTkLabel(
            title_frame,
            text=version_text,
            font=ctk.CTkFont(family="Microsoft YaHei", size=12),
            text_color="#4B5563",
            anchor="w"
        )
        lbl_version.pack(fill="x", pady=(5, 0))

        # 2. 更新日志显示区
        log_frame = ctk.CTkFrame(self, fg_color="white", corner_radius=8, border_width=1, border_color="#E5E7EB")
        log_frame.pack(fill="both", expand=True, padx=25, pady=10)
        
        lbl_log_title = ctk.CTkLabel(
            log_frame,
            text=" 更新日志：",
            font=ctk.CTkFont(family="Microsoft YaHei", size=12, weight="bold"),
            text_color="#374151",
            anchor="w"
        )
        lbl_log_title.pack(fill="x", padx=15, pady=(10, 5))
        
        txt_changelog = ctk.CTkTextbox(
            log_frame,
            font=ctk.CTkFont(family="Microsoft YaHei", size=12),
            fg_color="transparent",
            text_color="#4B5563",
            wrap="word",
            activate_scrollbars=True
        )
        txt_changelog.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        
        # 格式化并填入更新日志
        changelog_content = self.changelog.strip() if self.changelog else "无详细更新日志说明。"
        txt_changelog.insert("1.0", changelog_content)
        txt_changelog.configure(state="disabled") # 只读

        # 3. 底部按钮区域
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(fill="x", padx=25, pady=(15, 20))
        
        # 忽略此版本按钮 (只有在非手动触发时才显示“忽略”选项才有意义，或者手动也可以提供)
        btn_ignore = ctk.CTkButton(
            btn_frame,
            text="忽略此版本",
            width=100,
            height=36,
            corner_radius=18,
            fg_color="#E5E7EB",
            text_color="#4B5563",
            hover_color="#D1D5DB",
            font=ctk.CTkFont(family="Microsoft YaHei", size=13),
            command=self.ignore_version
        )
        btn_ignore.pack(side="left", padx=(0, 10))
        
        # 稍后更新按钮
        btn_later = ctk.CTkButton(
            btn_frame,
            text="稍后再说",
            width=100,
            height=36,
            corner_radius=18,
            fg_color="#E5E7EB",
            text_color="#4B5563",
            hover_color="#D1D5DB",
            font=ctk.CTkFont(family="Microsoft YaHei", size=13),
            command=self.destroy
        )
        btn_later.pack(side="left")
        
        # 立即更新按钮 (加粗高亮)
        btn_update = ctk.CTkButton(
            btn_frame,
            text=" 立即前往下载",
            width=150,
            height=36,
            corner_radius=18,
            fg_color="#3B82F6",
            text_color="white",
            hover_color="#2563EB",
            font=ctk.CTkFont(family="Microsoft YaHei", size=13, weight="bold"),
            command=self.open_download_url
        )
        btn_update.pack(side="right")

    def open_download_url(self):
        """调用默认浏览器打开下载地址"""
        if self.download_url:
            webbrowser.open(self.download_url)
        self.destroy()

    def ignore_version(self):
        """保存被忽略的版本，关闭弹窗"""
        if self.latest_version:
            save_ignored_version(self.latest_version)
        self.destroy()
