import sys
import os
import time
import tkinter as tk
import customtkinter as ctk

def show_splash_and_load():
    # 强制使用明亮主题，配合干净的排版
    ctk.set_appearance_mode("Light")
    ctk.set_default_color_theme("blue")

    root = ctk.CTk()
    root.withdraw() # 隐藏主窗口

    # --- 创建启动画面 (Splash Screen) ---
    splash = ctk.CTkToplevel(root)
    splash.overrideredirect(True) # 无系统边框
    splash.attributes('-topmost', True)

    # 1. 获取正确缩放并居中
    splash.update_idletasks() 
    width = 580  # 【再变宽】以容纳超长波形
    height = 240
    screen_width = splash.winfo_screenwidth()
    screen_height = splash.winfo_screenheight()
    x = (screen_width // 2) - (width // 2)
    y = (screen_height // 2) - (height // 2)
    splash.geometry(f"{width}x{height}+{x}+{y}")

    # 2. 最外层容器：纯白背景、无圆角扁平风、细深灰描边
    main_frame = ctk.CTkFrame(
        splash, 
        fg_color="#FFFFFF",       
        corner_radius=0,          
        border_width=1,           
        border_color="#555555"    
    )
    main_frame.pack(fill="both", expand=True)

    # 3. 顶部鲜艳装饰色带
    accent_strip = ctk.CTkFrame(main_frame, height=5, fg_color="#FF2A6D", corner_radius=0)
    accent_strip.pack(fill="x", side="top")

    # 4. 内容区：左右分栏布局
    content_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
    content_frame.pack(fill="both", expand=True, padx=35, pady=25)

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
        logo_fallback = ctk.CTkLabel(
            left_frame, text="Phon\nTracer", 
            font=ctk.CTkFont(family="Segoe UI Black", size=36, weight="bold"), 
            text_color="#FF2A6D", justify="left"
        )
        logo_fallback.pack(expand=True)

    # === 右半边：文字与动态声波进度区 ===
    right_frame = ctk.CTkFrame(content_frame, fg_color="transparent")
    right_frame.pack(side="left", fill="both", expand=True)

    # 中文主标题
    title_lbl = ctk.CTkLabel(
        right_frame, text="声调提取与分析工具", 
        font=ctk.CTkFont(family="Microsoft YaHei", size=18, weight="bold"), 
        text_color="#00C4FF", # 鲜艳的亮青色
        anchor="w"
    )
    title_lbl.pack(fill="x", pady=(5, 0))
    
    # 英文副标题
    en_subtitle = ctk.CTkLabel(
        right_frame, text="PITCH EXTRACTION & ANALYSIS TOOL", 
        font=ctk.CTkFont(family="Arial", size=10, weight="bold"), 
        text_color="#A3A3A3",
        anchor="w"
    )
    en_subtitle.pack(fill="x", pady=(0, 20))

    # --- 核心创意：嵌入式的声波等效器进度条 (超长版) ---
    wave_frame = ctk.CTkFrame(right_frame, fg_color="transparent")
    wave_frame.pack(fill="x", pady=(0, 10))

    # 【超长波形参数】
    bar_width = 4  # 稍微缩减一点宽度以容纳更多根
    spacing = 2
    
    # 扩展后的波形数组，总计 80 根，呈现更宏大的起伏感
    wave_heights = [
        4, 5, 8, 14, 22, 18, 28, 38, 42, 30, 
        24, 32, 40, 44, 35, 22, 18, 25, 38, 42, 
        34, 24, 18, 22, 35, 42, 38, 26, 18, 15, 
        24, 35, 40, 32, 22, 18, 24, 30, 26, 18, 
        14, 20, 24, 18, 12, 10, 8, 6, 5, 8, 14,
        22, 30, 35, 40, 44, 38, 30, 22, 15, 12,
        18, 25, 32, 38, 42, 45, 38, 30, 24, 18,
        14, 10, 8, 6, 5, 4, 4, 4, 4
    ]
    
    num_bars = len(wave_heights)
    canvas_w = num_bars * (bar_width + spacing) 
    canvas_h = 45
    
    eq_canvas = tk.Canvas(wave_frame, width=canvas_w, height=canvas_h, bg="#FFFFFF", highlightthickness=0, bd=0)
    eq_canvas.pack(anchor="w") 

    bars = []

    # 绘制初始波形 (极浅灰)
    for i, h in enumerate(wave_heights):
        x0 = i * (bar_width + spacing)
        y0 = canvas_h - h
        x1 = x0 + bar_width
        y1 = canvas_h
        bar = eq_canvas.create_rectangle(x0, y0, x1, y1, fill="#F3F4F6", outline="")
        bars.append(bar)

    # 状态文字
    status_lbl = ctk.CTkLabel(
        right_frame, text="正在准备启动环境...", 
        font=ctk.CTkFont(family="Microsoft YaHei", size=11), 
        text_color="#555555",
        anchor="w"
    )
    status_lbl.pack(fill="x")

    # --- 动画与状态管理系统 ---
    progress_state = {"current": 0.0, "target": 0.0}

    def render_wave():
        # 丝滑的声波填充缓动动画
        if progress_state["current"] < progress_state["target"]:
            progress_state["current"] += 0.015
            if progress_state["current"] > progress_state["target"]:
                progress_state["current"] = progress_state["target"]
            
            filled_count = int(progress_state["current"] * num_bars)
            for i in range(num_bars):
                if i < filled_count:
                    eq_canvas.itemconfig(bars[i], fill="#FF0000") # 纯红色填充
                else:
                    eq_canvas.itemconfig(bars[i], fill="#F3F4F6")
        
        splash.after(16, render_wave) # 每 16ms 刷新 (60FPS)

    render_wave()

    def set_target_progress(val, text):
        progress_state["target"] = val
        status_lbl.configure(text=text)

    # 启动淡入动效
    splash.attributes('-alpha', 0.0) 
    for i in range(1, 16):
        splash.attributes('-alpha', i / 15.0)
        splash.update()
        time.sleep(0.01)

    # 开始模拟加载流程
    root.after(200, lambda: _load_phase_1(root, splash, set_target_progress))
    root.mainloop()

def _load_phase_1(root, splash, set_target_progress):
    set_target_progress(0.25, "加载核心计算库 (Matplotlib)...")
    
    import matplotlib
    import warnings
    matplotlib.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'PingFang SC', 'Arial Unicode MS', 'sans-serif']
    matplotlib.rcParams['axes.unicode_minus'] = False
    warnings.filterwarnings("ignore", category=RuntimeWarning)
    
    root.after(400, lambda: _load_phase_2(root, splash, set_target_progress))

def _load_phase_2(root, splash, set_target_progress):
    set_target_progress(0.60, "构建声学分析界面...")
    try:
        from modules.app import PhoneticsApp
    except ImportError:
        PhoneticsApp = None 
    
    root.after(400, lambda: _load_phase_3(root, splash, set_target_progress, PhoneticsApp))

def _load_phase_3(root, splash, set_target_progress, PhoneticsApp):
    set_target_progress(1.0, "就绪，准备开启...")
    
    if PhoneticsApp:
        app = PhoneticsApp(root, initial_files=sys.argv[1:])
    
    def finish():
        # 退出淡出动效
        for i in range(15, -1, -1):
            splash.attributes('-alpha', i / 15.0)
            splash.update()
            time.sleep(0.01)
        splash.destroy()
        
        root.deiconify() # 显示主窗口

    # 预留 800ms 让波形动画有时间彻底填满并展示
    root.after(800, finish)

def main():
    show_splash_and_load()

if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    main()