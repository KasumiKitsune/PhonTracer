import sys
import customtkinter as ctk
import matplotlib
import warnings
from modules.app import PhoneticsApp

def main():
    # 解决中文字体显示问题
    matplotlib.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'PingFang SC', 'Arial Unicode MS', 'sans-serif']
    matplotlib.rcParams['axes.unicode_minus'] = False
    warnings.filterwarnings("ignore", category=RuntimeWarning)

    # 初始化 CustomTkinter 全局主题配置
    ctk.set_appearance_mode("Light")
    ctk.set_default_color_theme("blue")

    root = ctk.CTk()
    app = PhoneticsApp(root, initial_files=sys.argv[1:])
    root.mainloop()

if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    main()