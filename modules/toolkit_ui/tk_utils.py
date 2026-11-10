"""
modules.toolkit_ui.tk_utils: 字体安全代理与后台线程辅助工具。
"""
import os
import sys
import threading
import traceback
import customtkinter as ctk

_tcl_dir = os.path.join(getattr(sys, "base_prefix", sys.prefix), "tcl", "tcl8.6")
_tk_dir = os.path.join(getattr(sys, "base_prefix", sys.prefix), "tcl", "tk8.6")
if os.path.isdir(_tcl_dir):
    os.environ.setdefault("TCL_LIBRARY", _tcl_dir)
if os.path.isdir(_tk_dir):
    os.environ.setdefault("TK_LIBRARY", _tk_dir)

# CTkFont 别名（不再全局拦截或强行常驻引用，避免内存泄漏）
_keep_alive_fonts = []
SafeCTkFont = ctk.CTkFont

def start_safe_thread(target):
    # 安全启动后台守护线程，避免破坏全局垃圾回收（GC）机制
    def wrapped():
        try:
            target()
        except Exception:
            traceback.print_exc()
    threading.Thread(target=wrapped, daemon=True).start()
