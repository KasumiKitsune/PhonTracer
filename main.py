import sys
import os
import json

def parse_handoff_arguments(args):
    """
    Parse command-line arguments to extract handoff manifest data and clean arguments.
    Returns: (cleaned_args, handoff_manifest_data)
    """
    manifest_data = None
    clean_args = []
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == '--handoff-manifest':
            if i + 1 < len(args):
                manifest_path = args[i+1]
                i += 2
                try:
                    manifest_path = os.path.abspath(manifest_path)
                    if os.path.exists(manifest_path):
                        with open(manifest_path, "r", encoding="utf-8") as f:
                            manifest_data = json.load(f)

                        # Check if project file is in the rest of args
                        has_project = False
                        for a in args:
                            if a != '--handoff-manifest' and a != manifest_path and not a.startswith('--handoff-manifest='):
                                if str(a).lower().endswith(('.teproj', '.zip')):
                                    has_project = True
                                    break
                        if not has_project:
                            archive_name = manifest_data.get("project_archive")
                            if archive_name:
                                manifest_dir = os.path.dirname(manifest_path)
                                archive_path = os.path.join(manifest_dir, archive_name)
                                if os.path.exists(archive_path):
                                    clean_args.append(archive_path)
                except Exception as e:
                    print(f"Error parsing handoff manifest: {e}")
            else:
                i += 1
        elif arg.startswith('--handoff-manifest='):
            manifest_path = arg.split('=', 1)[1]
            i += 1
            try:
                manifest_path = os.path.abspath(manifest_path)
                if os.path.exists(manifest_path):
                    with open(manifest_path, "r", encoding="utf-8") as f:
                        manifest_data = json.load(f)

                    has_project = False
                    for a in args:
                        if a != arg and not a.startswith('--handoff-manifest='):
                            if str(a).lower().endswith(('.teproj', '.zip')):
                                has_project = True
                                break
                    if not has_project:
                        archive_name = manifest_data.get("project_archive")
                        if archive_name:
                            manifest_dir = os.path.dirname(manifest_path)
                            archive_path = os.path.join(manifest_dir, archive_name)
                            if os.path.exists(archive_path):
                                clean_args.append(archive_path)
            except Exception as e:
                print(f"Error parsing handoff manifest: {e}")
        else:
            clean_args.append(arg)
            i += 1
    return clean_args, manifest_data

STARTUP_ARGS, HANDOFF_MANIFEST_DATA = parse_handoff_arguments(sys.argv[1:])

import os
import time
import tkinter as tk
import customtkinter as ctk
import logging

def _startup_debug(message):
    if os.environ.get("PHONTRACER_STARTUP_DEBUG") != "1":
        return
    try:
        log_dir = os.path.join(os.path.expanduser("~"), ".phon_tracer")
        os.makedirs(log_dir, exist_ok=True)
        with open(os.path.join(log_dir, "startup_debug.log"), "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} [main:{os.getpid()}] {message}\n")
    except Exception:
        pass

_startup_debug(f"捕获启动参数: {STARTUP_ARGS!r}")

# 全局打补丁：使所有 CTkCheckBox 在未勾选时为圆角矩形(6)，勾选后为圆形(1000)
_orig_checkbox_draw = ctk.CTkCheckBox._draw
def _patched_checkbox_draw(self, no_color_updates=False):
    if self._check_state:
        self._corner_radius = 1000
    else:
        self._corner_radius = 6
    _orig_checkbox_draw(self, no_color_updates)
ctk.CTkCheckBox._draw = _patched_checkbox_draw

from modules.version import APP_NAME, __version__

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def show_splash_and_load(startup_files=None):
    startup_files = list(STARTUP_ARGS if startup_files is None else startup_files)
    _startup_debug(f"show_splash_and_load startup_files={startup_files!r}")

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
            left_frame, text=APP_NAME.replace("Tracer", "\nTracer"),
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
        right_frame, text=f"PITCH EXTRACTION & ANALYSIS TOOL  v{__version__}",
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

    splash_alive = {"value": True}

    def render_wave():
        if not splash_alive["value"] or not splash.winfo_exists():
            return

        # 丝滑且响应迅速的声波填充缓动动画
        if progress_state["current"] < progress_state["target"]:
            progress_state["current"] += 0.06
            if progress_state["current"] > progress_state["target"]:
                progress_state["current"] = progress_state["target"]

            filled_count = int(progress_state["current"] * num_bars)
            for i in range(num_bars):
                if i < filled_count:
                    eq_canvas.itemconfig(bars[i], fill="#FF0000") # 纯红色填充
                else:
                    eq_canvas.itemconfig(bars[i], fill="#F3F4F6")

        if splash_alive["value"] and splash.winfo_exists():
            splash.after(16, render_wave) # 每 16ms 刷新 (60FPS)

    render_wave()

    def set_target_progress(val, text):
        progress_state["target"] = val
        status_lbl.configure(text=text)

    # 启动淡入动效（缩短等待时间）
    splash.attributes('-alpha', 0.0)
    for i in range(1, 11):
        splash.attributes('-alpha', i / 10.0)
        splash.update()
        time.sleep(0.005)

    # 开始加载流程（立即启动）
    root.after(10, lambda: _load_phase_1(root, splash, set_target_progress, startup_files, splash_alive))
    root.mainloop()

def _load_phase_1(root, splash, set_target_progress, startup_files, splash_alive):
    set_target_progress(0.25, "加载核心计算库 (Matplotlib)...")

    import matplotlib
    import warnings
    matplotlib.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'PingFang SC', 'Arial Unicode MS', 'sans-serif']
    matplotlib.rcParams['axes.unicode_minus'] = False
    warnings.filterwarnings("ignore", category=RuntimeWarning)

    # 缩短阶段转换的人为延迟
    root.after(10, lambda: _load_phase_2(root, splash, set_target_progress, startup_files, splash_alive))

def _load_phase_2(root, splash, set_target_progress, startup_files, splash_alive):
    set_target_progress(0.60, "构建声学分析界面...")
    try:
        from modules.app import PhoneticsApp
    except ImportError:
        PhoneticsApp = None

    # 缩短阶段转换的人为延迟
    root.after(10, lambda: _load_phase_3(root, splash, set_target_progress, PhoneticsApp, startup_files, splash_alive))

def _load_phase_3(root, splash, set_target_progress, PhoneticsApp, startup_files, splash_alive):
    set_target_progress(1.0, "就绪，准备开启...")

    app = None
    if PhoneticsApp:
        _startup_debug(f"创建 PhoneticsApp startup_files={startup_files!r}")
        app = PhoneticsApp(root, initial_files=startup_files, defer_startup_check=True, handoff_manifest=HANDOFF_MANIFEST_DATA)
        root._phontracer_app = app

    def finish():
        splash_alive["value"] = False
        # 退出淡出动效（缩短等待时间）
        for i in range(10, -1, -1):
            if splash.winfo_exists():
                splash.attributes('-alpha', i / 10.0)
                splash.update()
            time.sleep(0.005)
        if splash.winfo_exists():
            splash.destroy()

        root.deiconify() # 显示主窗口
        if app:
            _startup_debug("主窗口显示后调度 run_startup_check")
            root.after(50, app.run_startup_check)

    # 预留极少时间（100ms）以便用户感知到 100% 载入完成
    root.after(100, finish)

def main():
    show_splash_and_load(STARTUP_ARGS)

if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    main()
