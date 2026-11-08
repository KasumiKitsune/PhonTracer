import tkinter as tk
import customtkinter as ctk

PHONTRACER_THEME = {
    "bg_app": ("#F1F5F9", "#0F172A"),
    "surface_card": ("#FFFFFF", "#1E293B"),
    "surface_subtle": ("#F8FAFC", "#263244"),
    "border_subtle": ("#E2E8F0", "#334155"),
    "border_strong": ("#CBD5E1", "#475569"),
    "text_primary": ("#0F172A", "#F8FAFC"),
    "text_secondary": ("#334155", "#E2E8F0"),
    "text_muted": ("#64748B", "#94A3B8"),
    "primary": ("#2563EB", "#3B82F6"),
    "primary_hover": ("#1D4ED8", "#2563EB"),
    "success": ("#10B981", "#059669"),
    "success_hover": ("#059669", "#047857"),
    "accent_soft": ("#EEF2FF", "#312E81"),
    "accent_text": ("#4338CA", "#A5B4FC"),
    "accent_hover": ("#E0E7FF", "#3730A3"),
    "warning": ("#F59E0B", "#D97706"),
    "warning_soft": ("#FFFBEB", "#451A03"),
    "danger_soft": ("#FEE2E2", "#450A0A"),
    "danger_text": ("#DC2626", "#FCA5A5"),
    "danger": ("#EF4444", "#DC2626"),
    "danger_hover": ("#DC2626", "#B91C1C"),
}


def _apply_custom_arrow(dropdown):
    """统一修复 Windows 高分屏下 CTkOptionMenu / CTkComboBox 下拉箭头绘制模糊或变形问题。"""
    try:
        orig_draw_arrow = dropdown._draw_engine.draw_dropdown_arrow

        def custom_draw_arrow(*args, **kwargs):
            old_method = dropdown._draw_engine.preferred_drawing_method
            try:
                dropdown._draw_engine.preferred_drawing_method = "polygon_shapes"
                res = orig_draw_arrow(*args, **kwargs)
                try:
                    dropdown._canvas.itemconfigure("dropdown_arrow", width=2)
                except Exception:
                    pass
                return res
            finally:
                dropdown._draw_engine.preferred_drawing_method = old_method

        dropdown._draw_engine.draw_dropdown_arrow = custom_draw_arrow
        dropdown._canvas.delete("dropdown_arrow")
        if hasattr(dropdown, "_draw"):
            dropdown._draw(no_color_updates=False)
    except Exception:
        pass


apply_custom_arrow = _apply_custom_arrow
apply_custom_dropdown_arrow = _apply_custom_arrow


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
        px, py = 0, 0
        try:
            px, py = self.widget.winfo_pointerxy()
            x = px + 12
            y = py + 16
        except Exception:
            x = self.widget.winfo_rootx() + 25
            y = self.widget.winfo_rooty() + 25

        self.tipwindow = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        label = tk.Label(tw, text=self.text, justify=tk.LEFT,
                         background="#374151", foreground="white", relief=tk.FLAT,
                         borderwidth=0, font=("Microsoft YaHei", 10), padx=8, pady=5)
        label.pack(ipadx=1)
        try:
            tw.update_idletasks()
            tip_w = tw.winfo_reqwidth()
            tip_h = tw.winfo_reqheight()
            screen_w = self.widget.winfo_screenwidth()
            screen_h = self.widget.winfo_screenheight()
            if x + tip_w + 8 > screen_w:
                x = max(8, screen_w - tip_w - 8)
            if y + tip_h + 8 > screen_h:
                y = max(8, (py - tip_h - 8) if py else (screen_h - tip_h - 8))
        except Exception:
            pass
        tw.wm_geometry(f"+{x}+{y}")

    def hidetip(self):
        tw = self.tipwindow
        self.tipwindow = None
        if tw:
            tw.destroy()
        if ToolTip.active_tip == self:
            ToolTip.active_tip = None


class AutoScrollbar(ctk.CTkScrollbar):
    """
    仅在内容溢出（需要滚动）时才显示的滚动条，同时兼容 grid 与 pack 布局管理器。
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._layout_manager = None
        self._pack_kwargs = {}

    def grid(self, *args, **kwargs):
        self._layout_manager = "grid"
        return super().grid(*args, **kwargs)

    def pack(self, *args, **kwargs):
        self._layout_manager = "pack"
        if kwargs:
            self._pack_kwargs = dict(kwargs)
        return super().pack(*args, **kwargs)

    def set(self, low, high):
        if not self.winfo_exists():
            return
        manager = self._layout_manager or self.winfo_manager()
        is_managed = bool(self.winfo_manager())
        if float(low) <= 0.0 and float(high) >= 1.0:
            if is_managed:
                if manager == "pack":
                    self._layout_manager = "pack"
                    if not self._pack_kwargs:
                        try:
                            info = self.pack_info()
                            self._pack_kwargs = {
                                k: info[k]
                                for k in ("side", "fill", "expand", "padx", "pady", "ipadx", "ipady", "anchor")
                                if k in info
                            }
                        except Exception:
                            pass
                    self.pack_forget()
                else:
                    self._layout_manager = "grid"
                    self.grid_remove()
        else:
            if not is_managed:
                if (self._layout_manager or manager) == "pack":
                    super().pack(**self._pack_kwargs)
                else:
                    super().grid()
        super().set(low, high)


def make_context_menu(parent, font_size=11):
    """
    统一创建右键菜单，带有系统统一的样式。
    """
    return tk.Menu(
        parent,
        tearoff=0,
        font=("Microsoft YaHei", font_size),
        bg="#FFFFFF",
        fg="#2C3E50",
        activebackground="#3B82F6",
        activeforeground="#FFFFFF",
        activeborderwidth=0,
        bd=1,
        relief="solid",
    )


def post_context_menu(menu, event):
    """
    统一弹出右键菜单。
    注意：在 Windows 下 tk_popup 是非阻塞的，严禁在 finally 中同步调用 grab_release，
    否则会导致右键菜单弹出瞬间失去鼠标捕获而闪退或失焦。
    """
    try:
        menu.tk_popup(event.x_root, event.y_root)
    except Exception:
        try:
            menu.grab_release()
        except Exception:
            pass
