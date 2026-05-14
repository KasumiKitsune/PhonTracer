import tkinter as tk
import customtkinter as ctk

class CTkReleaseButton(ctk.CTkButton):
    """
    自定义按钮类，将 command 触发时机从“按下”改为“松开且在按钮范围内”。
    """
    def __init__(self, master=None, **kwargs):
        # 1. 拦截 command 参数，防止父类在按下时触发
        self._release_command = kwargs.pop("command", None)
        # 2. 调用父类初始化
        super().__init__(master, **kwargs)
        # 3. 绑定左键松开事件
        if self._release_command:
            self.bind("<ButtonRelease-1>", self._on_release)
            
    def _on_release(self, event):
        # 如果按钮被禁用，则不响应
        if self.cget("state") == "disabled":
            return
            
        # 4. 核心：判断松开时，鼠标是否还在按钮的区域内
        x, y = self.winfo_pointerxy()
        btn_x = self.winfo_rootx()
        btn_y = self.winfo_rooty()
        btn_w = self.winfo_width()
        btn_h = self.winfo_height()
        
        if btn_x <= x <= btn_x + btn_w and btn_y <= y <= btn_y + btn_h:
            if self._release_command:
                self._release_command()

class ToolTip:
    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self.tipwindow = None
        self.id = None
        self.widget.bind("<Enter>", self.enter)
        self.widget.bind("<Leave>", self.leave)

    def enter(self, event=None):
        self.schedule()

    def leave(self, event=None):
        self.unschedule()
        self.hidetip()

    def schedule(self):
        self.unschedule()
        self.id = self.widget.after(500, self.showtip)

    def unschedule(self):
        id_ = self.id
        self.id = None
        if id_:
            self.widget.after_cancel(id_)

    def showtip(self, event=None):
        x, y, cx, cy = self.widget.bbox("insert") or (0,0,0,0)
        x += self.widget.winfo_rootx() + 25
        y += self.widget.winfo_rooty() + 25
        self.tipwindow = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        label = tk.Label(tw, text=self.text, justify=tk.LEFT,
                         background="#374151", foreground="white", relief=tk.FLAT,
                         borderwidth=0, font=("Microsoft YaHei", 10), padx=8, pady=5)
        label.pack(ipadx=1)

    def hidetip(self):
        tw = self.tipwindow
        self.tipwindow = None
        if tw:
            tw.destroy()