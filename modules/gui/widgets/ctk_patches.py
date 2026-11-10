"""
CustomTkinter widget patches and theme constants.
"""
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



def install_dynamic_checkbox_patch():
    """全局补丁：使所有 CTkCheckBox 在未勾选时为圆角矩形(6)，勾选后为圆形(1000)。"""
    if getattr(ctk.CTkCheckBox, "_phontracer_dynamic_radius_patched", False):
        return
    orig_draw = getattr(ctk.CTkCheckBox, "_orig_unpatched_draw", ctk.CTkCheckBox._draw)
    ctk.CTkCheckBox._orig_unpatched_draw = orig_draw

    def _patched_checkbox_draw(self, no_color_updates=False):
        if getattr(self, "_check_state", False):
            self._corner_radius = 1000
        else:
            self._corner_radius = 6
        return orig_draw(self, no_color_updates)

    ctk.CTkCheckBox._draw = _patched_checkbox_draw
    ctk.CTkCheckBox._phontracer_dynamic_radius_patched = True


install_dynamic_checkbox_patch()


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
            self._bind_release_events()

    def _bind_release_events(self):
        widgets = [self]
        for attr in ("_canvas", "_text_label", "_image_label"):
            if hasattr(self, attr):
                w = getattr(self, attr)
                if w:
                    widgets.append(w)
        for w in widgets:
            if not getattr(w, "_release_events_bound", False):
                try:
                    w.bind("<ButtonPress-1>", self._on_press, add="+")
                    w.bind("<ButtonRelease-1>", self._on_release, add="+")
                    w._release_events_bound = True
                except Exception:
                    pass

    def configure(self, require_redraw=False, **kwargs):
        if "command" in kwargs:
            self._release_command = kwargs.pop("command")
        res = super().configure(require_redraw=require_redraw, **kwargs)
        if self._release_command:
            self._bind_release_events()
        return res

    config = configure

    def cget(self, attribute_name: str):
        if attribute_name == "command":
            return self._release_command
        return super().cget(attribute_name)

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



def _get_physical_screen_bounds(*args, **kwargs):
    from .dropdown_popover import _get_physical_screen_bounds as _impl
    return _impl(*args, **kwargs)


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
                try:
                    toplevel = self.widget.nametowidget(".")
                except Exception:
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
            try:
                scale = float(self.widget._apply_widget_scaling(1.0)) if hasattr(self.widget, "_apply_widget_scaling") else 1.0
            except Exception:
                scale = 1.0
            mon_left, mon_top, mon_right, mon_bottom = _get_physical_screen_bounds(
                self.widget, scale=scale, ref_x=x, ref_y=y
            )
            if x + tip_w + 8 > mon_right:
                x = max(mon_left + 8, mon_right - tip_w - 8)
            if y + tip_h + 8 > mon_bottom:
                y = max(mon_top + 8, (py - tip_h - 8) if py else (mon_bottom - tip_h - 8))
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

