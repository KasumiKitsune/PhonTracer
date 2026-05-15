import sys
import os
import time
import customtkinter as ctk

def show_splash_and_load():
    # 强制使用明亮主题
    ctk.set_appearance_mode("Light")
    ctk.set_default_color_theme("blue")

    root = ctk.CTk()
    root.withdraw() # 隐藏主窗口

    # --- 创建启动画面 (Splash Screen) ---
    splash = ctk.CTkToplevel(root)
    splash.overrideredirect(True) # 无边框、无系统阴影
    splash.attributes('-topmost', True)

    # 1. 修复居中问题：先 update_idletasks 获取正确缩放
    splash.update_idletasks() 
    width = 540  # 加宽窗口以适应左右分栏布局
    height = 230
    screen_width = splash.winfo_screenwidth()
    screen_height = splash.winfo_screenheight()
    x = (screen_width // 2) - (width // 2)
    y = (screen_height // 2) - (height // 2)
    splash.geometry(f"{width}x{height}+{x}+{y}")

    # 2. 最外层容器：纯白背景、无圆角、细深灰描边
    main_frame = ctk.CTkFrame(
        splash, 
        fg_color="#FFFFFF",       # 纯白明亮背景
        corner_radius=0,          # 零圆角，锋利扁平风
        border_width=1,           # 1像素边框
        border_color="#555555"    # 细细的深灰色描边
    )
    main_frame.pack(fill="both", expand=True)

    # 3. 顶部鲜艳装饰色带 (增加一抹鲜艳的设计感)
    accent_strip = ctk.CTkFrame(main_frame, height=5, fg_color="#FF2A6D", corner_radius=0)
    accent_strip.pack(fill="x", side="top")

    # 4. 内容区：左右分栏布局
    content_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
    content_frame.pack(fill="both", expand=True, padx=30, pady=25)

    # === 左半边：Logo 展示区 ===
    left_frame = ctk.CTkFrame(content_frame, fg_color="transparent")
    left_frame.pack(side="left", fill="y", padx=(0, 25))

    try:
        from PIL import Image
        logo_path = os.path.join(os.path.dirname(__file__), "assets", "logo.png")
        if not os.path.exists(logo_path):
            logo_path = os.path.join(os.path.dirname(__file__), "assets", "icon.png")
        
        if os.path.exists(logo_path):
            img = Image.open(logo_path)
            img_w, img_h = img.size
            target_h = 110 # 左侧放大的 Logo
            target_w = int(img_w * (target_h / img_h))
            logo_img = ctk.CTkImage(light_image=img, dark_image=img, size=(target_w, target_h))
            logo_lbl = ctk.CTkLabel(left_frame, text="", image=logo_img)
            logo_lbl.pack(expand=True)
        else:
            raise FileNotFoundError
    except Exception:
        # 如果没有图，生成一个鲜艳的文字Logo替代
        logo_fallback = ctk.CTkLabel(
            left_frame, text="Phon\nTracer", 
            font=ctk.CTkFont(family="Segoe UI Black", size=36, weight="bold"), 
            text_color="#FF2A6D", justify="left"
        )
        logo_fallback.pack(expand=True)

    # === 右半边：文字与进度区 ===
    right_frame = ctk.CTkFrame(content_frame, fg_color="transparent")
    right_frame.pack(side="left", fill="both", expand=True)

    # 中文主标题 (采用鲜艳的青蓝色)
    title_lbl = ctk.CTkLabel(
        right_frame, text="声调提取与分析工具", 
        font=ctk.CTkFont(family="Microsoft YaHei", size=18, weight="bold"), 
        text_color="#00C4FF", # 鲜艳的亮蓝/青色
        anchor="w" # 靠左对齐
    )
    title_lbl.pack(fill="x", pady=(15, 0))
    
    # 英文副标题 (极小字号排版，提升现代感和专业感)
    en_subtitle = ctk.CTkLabel(
        right_frame, text="PITCH EXTRACTION & ANALYSIS TOOL", 
        font=ctk.CTkFont(family="Arial", size=10, weight="bold"), 
        text_color="#A3A3A3",
        anchor="w"
    )
    en_subtitle.pack(fill="x", pady=(0, 25))

    # 直角进度条 (极简、亮色)
    progress = ctk.CTkProgressBar(
        right_frame, 
        height=8, 
        progress_color="#FF2A6D", # 鲜艳的亮玫红
        fg_color="#F3F4F6",       # 极浅灰轨道底色
        corner_radius=0           # 无圆角，呼应整体锋利风格
    )
    progress.pack(fill="x", pady=(10, 8))
    progress.set(0.0)

    # 状态文字 (深灰色)
    status_lbl = ctk.CTkLabel(
        right_frame, text="正在准备启动环境...", 
        font=ctk.CTkFont(family="Microsoft YaHei", size=11), 
        text_color="#555555",
        anchor="w"
    )
    status_lbl.pack(fill="x")

    # --- 启动淡入动效 ---
    splash.attributes('-alpha', 0.0) 
    splash.update()
    
    for i in range(1, 16):
        splash.attributes('-alpha', i / 15.0)
        splash.update()
        time.sleep(0.01) # 加快淡入速度，显得干脆利落

    def update_progress(val, text):
        progress.set(val)
        status_lbl.configure(text=text)
        splash.update()

    # 分阶段加载以保持 UI 响应
    root.after(100, lambda: _load_phase_1(root, splash, update_progress))
    root.mainloop()

def _load_phase_1(root, splash, update_progress):
    update_progress(0.25, "加载核心计算库 (Matplotlib)...")
    import matplotlib
    import warnings
    matplotlib.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'PingFang SC', 'Arial Unicode MS', 'sans-serif']
    matplotlib.rcParams['axes.unicode_minus'] = False
    warnings.filterwarnings("ignore", category=RuntimeWarning)
    
    root.after(100, lambda: _load_phase_2(root, splash, update_progress))

def _load_phase_2(root, splash, update_progress):
    update_progress(0.55, "加载应用界面模块...")
    try:
        from modules.app import PhoneticsApp
    except ImportError:
        PhoneticsApp = None 
    
    root.after(100, lambda: _load_phase_3(root, splash, update_progress, PhoneticsApp))

def _load_phase_3(root, splash, update_progress, PhoneticsApp):
    update_progress(0.85, "初始化工作区...")
    
    if PhoneticsApp:
        app = PhoneticsApp(root, initial_files=sys.argv[1:])
    
    update_progress(1.0, "就绪")
    
    def finish():
        # 退出时的淡出动效
        for i in range(15, -1, -1):
            splash.attributes('-alpha', i / 15.0)
            splash.update()
            time.sleep(0.01)
        splash.destroy()
        
        root.deiconify() # 显示主窗口

    root.after(300, finish) # 停留片刻

def main():
    show_splash_and_load()

if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    main()