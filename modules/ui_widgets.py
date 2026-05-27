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
        # 3. 绑定左键按下与松开事件至控件及其子控件，以防止下拉菜单等浮层关闭时产生的释放穿透问题
        self._is_pressed = False
        if self._release_command:
            widgets = [self]
            for attr in ("_canvas", "_text_label", "_image_label"):
                if hasattr(self, attr):
                    w = getattr(self, attr)
                    if w:
                        widgets.append(w)
            for w in widgets:
                w.bind("<ButtonPress-1>", self._on_press, add="+")
                w.bind("<ButtonRelease-1>", self._on_release, add="+")
            
    def _on_press(self, event):
        if self.cget("state") == "disabled":
            return
        self._is_pressed = True
        
    def _on_release(self, event):
        if not getattr(self, "_is_pressed", False):
            return
        self._is_pressed = False
        
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
    active_tip = None       # 类变量：追踪当前正在显示的 ToolTip 实例，确保全局同时仅有一个 ToolTip 处于显示状态
    _global_bound = False   # 类变量：标记是否已绑定全局事件

    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self.tipwindow = None
        self.id = None
        
        # 递归绑定事件到所有内部子控件上，确保 100% 灵敏触发
        self._bind_widget(self.widget)
        
        # 仅全局绑定一次，当发生任何鼠标滚动或点击时，立即消失当前 ToolTip
        if not ToolTip._global_bound:
            try:
                toplevel = self.widget.winfo_toplevel()
                toplevel.bind_all("<MouseWheel>", ToolTip.hide_current_tip, add="+")
                toplevel.bind_all("<ButtonPress>", ToolTip.hide_current_tip, add="+")
                toplevel.bind_all("<Button-4>", ToolTip.hide_current_tip, add="+")
                toplevel.bind_all("<Button-5>", ToolTip.hide_current_tip, add="+")
                ToolTip._global_bound = True
            except Exception:
                pass

    @classmethod
    def hide_current_tip(cls, event=None):
        if cls.active_tip:
            cls.active_tip.unschedule()
            cls.active_tip.hidetip()
            cls.active_tip = None

    def _bind_widget(self, w):
        try:
            w.bind("<Enter>", self.enter, add="+")
            w.bind("<Leave>", self.leave, add="+")
        except Exception:
            pass
        
        # CustomTkinter 控件可能包含的内部原生子控件
        for attr in ("_canvas", "_text_label", "_image_label", "_entry", "_check_box"):
            if hasattr(w, attr):
                sub = getattr(w, attr)
                if sub:
                    try:
                        sub.bind("<Enter>", self.enter, add="+")
                        sub.bind("<Leave>", self.leave, add="+")
                    except Exception:
                        pass
                        
        # 递归遍历子控件并绑定，使得容器类控件（如 CTkFrame）子项也能触发 Tooltip
        try:
            for child in w.winfo_children():
                self._bind_widget(child)
        except Exception:
            pass

    def enter(self, event=None):
        # 进入新控件时，立即关掉并取消排队中的其他 ToolTip
        if ToolTip.active_tip and ToolTip.active_tip != self:
            ToolTip.active_tip.hidetip()
            ToolTip.active_tip = None
        self.schedule()

    def leave(self, event=None):
        # 检查鼠标是否真的离开了主控件的物理范围，防止子控件之间移动导致闪烁或隐藏
        try:
            x, y = self.widget.winfo_pointerxy()
            rx = self.widget.winfo_rootx()
            ry = self.widget.winfo_rooty()
            rw = self.widget.winfo_width()
            rh = self.widget.winfo_height()
            if rx <= x <= rx + rw and ry <= y <= ry + rh:
                return
        except Exception:
            pass
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
        # 双重保险：显示前确认没有其他 ToolTip
        if ToolTip.active_tip and ToolTip.active_tip != self:
            ToolTip.active_tip.hidetip()
            
        ToolTip.active_tip = self
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
        if ToolTip.active_tip == self:
            ToolTip.active_tip = None

class AutoScrollbar(ctk.CTkScrollbar):
    """
    仅在内容溢出（需要滚动）时才显示的滚动条。
    """
    def set(self, low, high):
        if float(low) <= 0.0 and float(high) >= 1.0:
            if self.winfo_ismapped():
                self.grid_remove()
        else:
            if not self.winfo_ismapped():
                self.grid()
        super().set(low, high)