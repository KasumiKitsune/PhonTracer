import math
import time
import tkinter as tk
import tkinter.font as tkfont
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

_ACTIVE_DROPDOWN_POPOVER = {
    "window": None,
    "owner": None,
    "closed_owner": None,
    "closed_time": 0.0,
    "cleanup": None,
}


def _get_physical_screen_bounds(widget=None, scale=1.0, ref_x=None, ref_y=None):
    """
    获取当前屏幕（或目标坐标所在显示器）的物理像素边界 (left, top, right, bottom)。
    解决 Windows 高分屏（如 2560x1440 @ 150% 缩放）下 Tcl/Tk 在 DPI 感知切换前缓存逻辑分辨率
    (winfo_screenwidth=1707, winfo_screenheight=960) 导致右键菜单与下拉框坐标被错误截断左移/上移的问题。
    """
    tk_sw, tk_sh = 1920, 1080
    target_w = widget if (widget is not None and hasattr(widget, "winfo_screenwidth")) else getattr(tk, "_default_root", None)
    if target_w is not None:
        try:
            tk_sw = max(320, int(target_w.winfo_screenwidth()))
            tk_sh = max(240, int(target_w.winfo_screenheight()))
        except Exception:
            pass

    eff_scale = max(1.0, float(scale or 1.0))
    scaled_sw = int(round(tk_sw * eff_scale))
    scaled_sh = int(round(tk_sh * eff_scale))

    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        sm_w = int(user32.GetSystemMetrics(0))
        sm_h = int(user32.GetSystemMetrics(1))

        if ref_x is not None and ref_y is not None:
            class _MONITORINFO(ctypes.Structure):
                _fields_ = [
                    ("cbSize", wintypes.DWORD),
                    ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT),
                    ("dwFlags", wintypes.DWORD),
                ]

            pt = wintypes.POINT(int(ref_x), int(ref_y))
            hmon = user32.MonitorFromPoint(pt, 2)  # MONITOR_DEFAULTTONEAREST = 2
            if hmon:
                mi = _MONITORINFO()
                mi.cbSize = ctypes.sizeof(_MONITORINFO)
                if user32.GetMonitorInfoW(wintypes.HANDLE(hmon), ctypes.byref(mi)):
                    left = int(mi.rcMonitor.left)
                    top = int(mi.rcMonitor.top)
                    right = int(mi.rcMonitor.right)
                    bottom = int(mi.rcMonitor.bottom)
                    if right > left and bottom > top:
                        if left == 0 and top == 0:
                            right = max(right, sm_w, tk_sw, scaled_sw)
                            bottom = max(bottom, sm_h, tk_sh, scaled_sh)
                        return (left, top, right, bottom)

        return (0, 0, max(sm_w, tk_sw, scaled_sw), max(sm_h, tk_sh, scaled_sh))
    except Exception:
        return (0, 0, max(tk_sw, scaled_sw), max(tk_sh, scaled_sh))


def _close_active_dropdown_popover(mark_closed_owner=False):
    win = _ACTIVE_DROPDOWN_POPOVER.get("window")
    owner = _ACTIVE_DROPDOWN_POPOVER.get("owner")
    cleanup = _ACTIVE_DROPDOWN_POPOVER.get("cleanup")
    _ACTIVE_DROPDOWN_POPOVER["window"] = None
    _ACTIVE_DROPDOWN_POPOVER["owner"] = None
    _ACTIVE_DROPDOWN_POPOVER["cleanup"] = None
    if mark_closed_owner and owner is not None:
        _ACTIVE_DROPDOWN_POPOVER["closed_owner"] = owner
        _ACTIVE_DROPDOWN_POPOVER["closed_time"] = time.monotonic()
    if owner is not None:
        try:
            owner._custom_popover_window = None
        except Exception:
            pass
    if callable(cleanup):
        try:
            cleanup()
        except Exception:
            pass
    if win is not None:
        try:
            if win.winfo_exists():
                win.destroy()
        except Exception:
            pass


def _install_custom_dropdown_popover(dropdown):
    """用现代圆角卡片式浮层替换 CTkOptionMenu 原生灰暗 tk.Menu，并支持二次点击收起（Toggle）。"""
    if not hasattr(dropdown, "_open_dropdown_menu") or getattr(dropdown, "_has_custom_popover", False):
        return

    dropdown._has_custom_popover = True
    dropdown._custom_popover_window = None

    def _custom_open_dropdown_menu():
        try:
            state = str(dropdown.cget("state")).lower()
            if state == "disabled":
                return
        except Exception:
            pass

        # 1. 若当前正是该下拉框处于展开状态，二次触发直接关闭并退出（Toggle 取消）
        curr_win = _ACTIVE_DROPDOWN_POPOVER.get("window")
        curr_owner = _ACTIVE_DROPDOWN_POPOVER.get("owner")
        if curr_owner is dropdown and curr_win is not None:
            try:
                if curr_win.winfo_exists():
                    _close_active_dropdown_popover(mark_closed_owner=False)
                    return
            except Exception:
                _close_active_dropdown_popover(mark_closed_owner=False)
                return

        # 2. 若刚刚因全局按下事件在下拉框本体上关闭了浮层，抑制紧随其后的 _clicked 重开
        if (
            _ACTIVE_DROPDOWN_POPOVER.get("closed_owner") is dropdown
            and (time.monotonic() - float(_ACTIVE_DROPDOWN_POPOVER.get("closed_time", 0.0))) < 0.25
        ):
            _ACTIVE_DROPDOWN_POPOVER["closed_owner"] = None
            return

        _close_active_dropdown_popover(mark_closed_owner=False)
        try:
            _close_active_context_menu()
        except Exception:
            pass

        values = list(getattr(dropdown, "_values", []) or [])
        if not values:
            return

        try:
            dropdown.update_idletasks()
        except Exception:
            pass

        is_dark = str(ctk.get_appearance_mode()).lower() == "dark"
        bg_hex = "#1E293B" if is_dark else "#FFFFFF"
        border_hex = "#475569" if is_dark else "#CBD5E1"

        pop = tk.Toplevel(dropdown)
        pop.wm_overrideredirect(True)
        try:
            pop.attributes("-topmost", True)
        except Exception:
            pass
        pop.configure(bg=border_hex)

        # 获取当前 CustomTkinter DPI 缩放倍率（winfo_width/geometry 使用物理像素，而 CTk 控件内部尺寸会乘上 scaling）
        try:
            scale = float(dropdown._apply_widget_scaling(1.0))
            if scale <= 0:
                scale = 1.0
        except Exception:
            scale = 1.0

        try:
            font_obj = dropdown.cget("font")
        except Exception:
            font_obj = None

        row_h = 28
        use_scroll = len(values) > 10
        visible_count = min(len(values), 10)

        outer = tk.Frame(
            pop,
            bg=bg_hex,
            highlightbackground=border_hex,
            highlightcolor=border_hex,
            highlightthickness=1,
            bd=0,
        )
        outer.pack(fill=tk.BOTH, expand=True)

        pad_px = max(3, int(round(4 * scale)))
        if use_scroll:
            container = ctk.CTkScrollableFrame(
                outer,
                fg_color=bg_hex,
                bg_color=bg_hex,
                corner_radius=0,
            )
            container.pack(fill=tk.BOTH, expand=True, padx=2, pady=2)
        else:
            container = tk.Frame(outer, bg=bg_hex, bd=0, highlightthickness=0)
            container.pack(fill=tk.BOTH, expand=True, padx=pad_px, pady=pad_px)

        try:
            curr_val = dropdown.get()
        except Exception:
            curr_val = getattr(dropdown, "_current_value", None)

        def _select_value(val):
            _close_active_dropdown_popover(mark_closed_owner=False)
            if hasattr(dropdown, "_dropdown_callback"):
                dropdown._dropdown_callback(val)
            else:
                try:
                    dropdown.set(val)
                    cmd = dropdown.cget("command")
                    if callable(cmd):
                        cmd(val)
                except Exception:
                    pass

        btns = []
        row_pad_y = max(1, int(round(1 * scale)))
        for val in values:
            is_sel = (str(val) == str(curr_val))
            btn = ctk.CTkButton(
                container,
                text=str(val),
                width=60,
                height=row_h,
                corner_radius=6,
                anchor="w",
                font=font_obj,
                bg_color=bg_hex,
                fg_color=("#EFF6FF", "#1E3A8A") if is_sel else bg_hex,
                hover_color=("#DBEAFE", "#1E40AF") if is_sel else ("#F1F5F9", "#334155"),
                text_color=("#2563EB", "#93C5FD") if is_sel else ("#1E293B", "#E2E8F0"),
                command=lambda v=val: _select_value(v),
            )
            try:
                btn.grid_columnconfigure(0, weight=0, minsize=10)
                if getattr(btn, "_text_label", None) is not None:
                    btn._text_label.configure(anchor="w", justify="left")
            except Exception:
                pass
            btn.pack(fill=tk.X, padx=2, pady=row_pad_y)
            btns.append(btn)

        try:
            pop.update_idletasks()
        except Exception:
            pass

        # 计算菜单所需物理像素宽度与高度（直接读取已创建按钮的真实物理像素高度，杜绝高分屏截断）
        try:
            base_w = int(dropdown.winfo_width())
        except Exception:
            base_w = int(getattr(dropdown, "_current_width", 120) * scale)

        max_text_px = 0
        try:
            tk_f = tkfont.Font(font=font_obj) if font_obj is not None else tkfont.nametofont("TkDefaultFont")
            for v in values:
                max_text_px = max(max_text_px, int(tk_f.measure(str(v))))
        except Exception:
            max_text_px = max((len(str(v)) * 11 for v in values), default=80)

        scaled_text_w = int(math.ceil(max_text_px * scale)) + int(math.ceil((48 if use_scroll else 34) * scale))
        req_w = int(outer.winfo_reqwidth()) if hasattr(outer, "winfo_reqwidth") else 0
        pop_w = max(base_w, req_w, scaled_text_w, int(96 * scale))

        btn_phys_h = max((int(b.winfo_reqheight()) for b in btns), default=int(math.ceil(row_h * scale)))
        calc_h = visible_count * (btn_phys_h + 2 * row_pad_y) + 2 * pad_px + 4
        req_h = int(outer.winfo_reqheight()) if (not use_scroll and hasattr(outer, "winfo_reqheight")) else 0
        pop_h = max(calc_h, req_h)

        try:
            rx = int(dropdown.winfo_rootx())
            ry = int(dropdown.winfo_rooty())
            rh = max(int(dropdown.winfo_height()), int(getattr(dropdown, "_current_height", 28) * scale))
        except Exception:
            rx, ry, rh = 100, 100, int(30 * scale)

        mon_left, mon_top, mon_right, mon_bottom = _get_physical_screen_bounds(
            dropdown, scale=scale, ref_x=rx, ref_y=ry
        )

        pos_x = min(max(mon_left + 4, rx), max(mon_left + 4, mon_right - pop_w - 8))
        pos_y = ry + rh + 2
        if pos_y + pop_h > mon_bottom - 12 and ry - pop_h - 2 > mon_top + 4:
            pos_y = ry - pop_h - 2

        pop.geometry(f"{pop_w}x{pop_h}+{pos_x}+{pos_y}")
        pop._pop_bounds = (pos_x, pos_y, pop_w, pop_h)
        opened_time = time.monotonic()
        try:
            pop.update_idletasks()
        except Exception:
            pass

        top = None
        bound_ids = []
        init_top_rect = None
        try:
            top = dropdown.winfo_toplevel()
            init_top_rect = (
                int(top.winfo_rootx()),
                int(top.winfo_rooty()),
                int(top.winfo_width()),
                int(top.winfo_height()),
            )
        except Exception:
            top = None

        def _get_pop_rect():
            bx, by, bw, bh = getattr(pop, "_pop_bounds", (pos_x, pos_y, pop_w, pop_h))
            try:
                if pop.winfo_exists():
                    wx, wy = int(pop.winfo_rootx()), int(pop.winfo_rooty())
                    ww, wh = int(pop.winfo_width()), int(pop.winfo_height())
                    if ww > 10 and wh > 10 and (wx != 0 or wy != 0):
                        return (wx, wy, max(ww, bw), max(wh, bh))
            except Exception:
                pass
            return (bx, by, bw, bh)

        def _on_global_press(_event=None):
            if _ACTIVE_DROPDOWN_POPOVER.get("window") is not pop:
                return
            if (time.monotonic() - opened_time) < 0.08:
                return
            try:
                px, py = dropdown.winfo_pointerxy()
                # 点击在浮层内部：交由选项按钮或滚动条处理
                px0, py0, pw, ph = _get_pop_rect()
                if px0 <= px <= px0 + pw and py0 <= py <= py0 + ph:
                    return
                # 点击在下拉框本体上：关闭浮层并标记 closed_owner，防止同次点击触发重开
                dx0, dy0 = int(dropdown.winfo_rootx()), int(dropdown.winfo_rooty())
                dw, dh = int(dropdown.winfo_width()), int(dropdown.winfo_height())
                if dx0 <= px <= dx0 + dw and dy0 <= py <= dy0 + dh:
                    _close_active_dropdown_popover(mark_closed_owner=True)
                    return
            except Exception:
                pass
            _close_active_dropdown_popover(mark_closed_owner=False)

        def _on_escape(_event=None):
            _close_active_dropdown_popover(mark_closed_owner=False)

        def _on_top_configure(event=None):
            if event is not None and getattr(event, "widget", None) is not top:
                return
            if (time.monotonic() - opened_time) < 0.20:
                return
            if top is not None and init_top_rect is not None:
                try:
                    curr_rect = (
                        int(top.winfo_rootx()),
                        int(top.winfo_rooty()),
                        int(top.winfo_width()),
                        int(top.winfo_height()),
                    )
                    if (
                        abs(curr_rect[0] - init_top_rect[0]) <= 2
                        and abs(curr_rect[1] - init_top_rect[1]) <= 2
                        and abs(curr_rect[2] - init_top_rect[2]) <= 2
                        and abs(curr_rect[3] - init_top_rect[3]) <= 2
                    ):
                        return
                except Exception:
                    return
            _close_active_dropdown_popover(mark_closed_owner=False)

        if top is not None:
            for seq, fn in (
                ("<ButtonPress-1>", _on_global_press),
                ("<ButtonPress-3>", _on_global_press),
                ("<Escape>", _on_escape),
                ("<Configure>", _on_top_configure),
            ):
                try:
                    bid = top.bind(seq, fn, add="+")
                    if bid:
                        bound_ids.append((top, seq, bid))
                except Exception:
                    pass

        try:
            pop.bind("<Escape>", _on_escape)
        except Exception:
            pass

        poll_state = {"job": None, "seen_release": False}
        try:
            import ctypes
            _get_key_state = ctypes.windll.user32.GetAsyncKeyState
        except Exception:
            _get_key_state = None

        def _poll_mouse_outside():
            poll_state["job"] = None
            if _ACTIVE_DROPDOWN_POPOVER.get("window") is not pop or _get_key_state is None:
                return
            try:
                if not pop.winfo_exists():
                    return
                pressed = any((_get_key_state(vk) & 0x8000) != 0 for vk in (0x01, 0x02, 0x04))
                if not pressed:
                    poll_state["seen_release"] = True
                elif poll_state["seen_release"] and (time.monotonic() - opened_time) >= 0.12:
                    px, py = pop.winfo_pointerxy()
                    px0, py0, pw, ph = _get_pop_rect()
                    if not (px0 <= px <= px0 + pw and py0 <= py <= py0 + ph):
                        dx0, dy0 = int(dropdown.winfo_rootx()), int(dropdown.winfo_rooty())
                        dw, dh = int(dropdown.winfo_width()), int(dropdown.winfo_height())
                        if dx0 <= px <= dx0 + dw and dy0 <= py <= dy0 + dh:
                            _close_active_dropdown_popover(mark_closed_owner=True)
                        else:
                            _close_active_dropdown_popover(mark_closed_owner=False)
                        return
                poll_state["job"] = pop.after(40, _poll_mouse_outside)
            except Exception:
                pass

        if _get_key_state is not None:
            try:
                poll_state["job"] = pop.after(60, _poll_mouse_outside)
            except Exception:
                pass

        def _cleanup_bindings():
            if poll_state.get("job") is not None:
                try:
                    if pop.winfo_exists():
                        pop.after_cancel(poll_state["job"])
                except Exception:
                    pass
                poll_state["job"] = None
            for w, seq, bid in bound_ids:
                try:
                    if w.winfo_exists():
                        w.unbind(seq, bid)
                except Exception:
                    pass
            bound_ids.clear()

        _ACTIVE_DROPDOWN_POPOVER["window"] = pop
        _ACTIVE_DROPDOWN_POPOVER["owner"] = dropdown
        _ACTIVE_DROPDOWN_POPOVER["cleanup"] = _cleanup_bindings
        dropdown._custom_popover_window = pop

    dropdown._open_dropdown_menu = _custom_open_dropdown_menu


def _apply_custom_arrow(dropdown):
    """统一美化 CTkOptionMenu / CTkComboBox 样式、修复高分屏箭头，并挂载支持二次点击收起的现代下拉浮层。"""
    try:
        if isinstance(dropdown, ctk.CTkOptionMenu):
            dropdown.configure(
                fg_color=("#EEF2F6", "#334155"),
                button_color=("#EEF2F6", "#334155"),
                button_hover_color=("#E2E8F0", "#475569"),
                text_color=("#0F172A", "#F8FAFC"),
                dropdown_fg_color=("#FFFFFF", "#1E293B"),
                dropdown_hover_color=("#EFF6FF", "#334155"),
                dropdown_text_color=("#0F172A", "#F8FAFC"),
            )
    except Exception:
        pass

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

    try:
        _install_custom_dropdown_popover(dropdown)
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


_ACTIVE_CONTEXT_MENU = {
    "menus": [],
    "cleanup": None,
}


def _close_active_context_menu():
    cleanup = _ACTIVE_CONTEXT_MENU.get("cleanup")
    menus = list(_ACTIVE_CONTEXT_MENU.get("menus") or [])
    _ACTIVE_CONTEXT_MENU["cleanup"] = None
    _ACTIVE_CONTEXT_MENU["menus"] = []
    if callable(cleanup):
        try:
            cleanup()
        except Exception:
            pass
    for m in reversed(menus):
        try:
            m._destroy_popup_only()
        except Exception:
            pass


class ModernContextMenu:
    """
    现代卡片式右键菜单浮层，完全兼容 tk.Menu 常用接口（add_command / add_separator /
    add_checkbutton / add_cascade / tk_popup / post / unpost / grab_release / index / entrycget）。
    统一字号（Microsoft YaHei 12pt）、1px 细边框、圆角胶囊高亮，并在高分屏下按真实物理像素自适应宽高。
    """

    UNIFIED_FONT_SIZE = 12

    def __init__(self, parent, font_size=12, **kwargs):
        raw_parent = parent
        while isinstance(raw_parent, ModernContextMenu):
            raw_parent = raw_parent.parent
        self.parent_menu = parent if isinstance(parent, ModernContextMenu) else None
        self.parent = raw_parent
        self.requested_font_size = font_size
        self.font_size = self.UNIFIED_FONT_SIZE
        self._items = []
        self._popup_win = None
        self._active_submenu = None
        self._submenu_close_job = None
        self._pop_bounds = (0, 0, 0, 0)
        self._opened_monotonic = 0.0

    def add_command(self, label="", command=None, state="normal", **kwargs):
        self._items.append(
            {
                "type": "command",
                "label": str(label),
                "command": command,
                "state": str(state or "normal").lower(),
            }
        )

    def add_separator(self, **kwargs):
        self._items.append({"type": "separator"})

    def add_checkbutton(
        self,
        label="",
        variable=None,
        command=None,
        onvalue=True,
        offvalue=False,
        state="normal",
        **kwargs,
    ):
        self._items.append(
            {
                "type": "checkbutton",
                "label": str(label),
                "variable": variable,
                "command": command,
                "onvalue": onvalue,
                "offvalue": offvalue,
                "state": str(state or "normal").lower(),
            }
        )

    def add_cascade(self, label="", menu=None, state="normal", **kwargs):
        self._items.append(
            {
                "type": "cascade",
                "label": str(label),
                "menu": menu,
                "state": str(state or "normal").lower(),
            }
        )

    def index(self, idx):
        if not self._items:
            return None
        if idx in ("end", "last", tk.END):
            return len(self._items) - 1
        if isinstance(idx, int):
            if 0 <= idx < len(self._items):
                return idx
            return len(self._items) - 1
        return len(self._items) - 1

    def entrycget(self, index, option):
        idx = self.index(index)
        if idx is None or not (0 <= idx < len(self._items)):
            raise tk.TclError(f"bad menu entry index '{index}'")
        item = self._items[idx]
        opt = str(option).lstrip("-")
        if opt == "label":
            return item.get("label", "")
        if opt == "state":
            return item.get("state", "normal")
        if opt == "type":
            return item.get("type", "command")
        return item.get(opt, "")

    def entryconfigure(self, index, **kwargs):
        idx = self.index(index)
        if idx is not None and 0 <= idx < len(self._items):
            for k, v in kwargs.items():
                self._items[idx][str(k).lstrip("-")] = v

    entryconfig = entryconfigure

    def delete(self, first, last=None):
        if not self._items:
            return
        f_idx = 0 if first == 0 else self.index(first)
        if f_idx is None:
            return
        if last is None:
            if 0 <= f_idx < len(self._items):
                self._items.pop(f_idx)
        else:
            l_idx = self.index(last)
            if l_idx is not None and f_idx <= l_idx:
                del self._items[f_idx : l_idx + 1]

    def grab_release(self):
        # 兼容旧代码中 try: menu.tk_popup(...) finally: menu.grab_release() 的调用，防止非阻塞浮层被立即销毁
        return None

    def unpost(self):
        _close_active_context_menu()

    def destroy(self):
        self._destroy_popup_only()

    def winfo_exists(self):
        return bool(self._popup_win is not None and self._popup_win.winfo_exists())

    def _cancel_submenu_close_job(self):
        job = getattr(self, "_submenu_close_job", None)
        self._submenu_close_job = None
        if job is not None and self._popup_win is not None:
            try:
                if self._popup_win.winfo_exists():
                    self._popup_win.after_cancel(job)
            except Exception:
                pass

    def _destroy_popup_only(self):
        self._cancel_submenu_close_job()
        if self._active_submenu is not None:
            try:
                self._active_submenu._destroy_popup_only()
            except Exception:
                pass
            self._active_submenu = None
        win = self._popup_win
        self._popup_win = None
        if win is not None:
            try:
                if win.winfo_exists():
                    win.destroy()
            except Exception:
                pass

    def _get_menu_rect(self):
        bx, by, bw, bh = getattr(self, "_pop_bounds", (0, 0, 0, 0))
        win = self._popup_win
        if win is not None:
            try:
                if win.winfo_exists():
                    wx = int(win.winfo_rootx())
                    wy = int(win.winfo_rooty())
                    ww = int(win.winfo_width())
                    wh = int(win.winfo_height())
                    if ww > 10 and wh > 10 and (wx != 0 or wy != 0):
                        return (wx, wy, max(ww, bw), max(wh, bh))
            except Exception:
                pass
        return (bx, by, bw, bh)

    def _get_dpi_scale(self):
        w = self.parent
        while isinstance(w, ModernContextMenu):
            w = w.parent
        while w is not None:
            if hasattr(w, "_apply_widget_scaling"):
                try:
                    s = float(w._apply_widget_scaling(1.0))
                    if s > 0:
                        return s
                except Exception:
                    pass
            try:
                w = getattr(w, "master", None)
            except Exception:
                break
        try:
            from customtkinter.windows.widgets.scaling import ScalingTracker
            target_w = self.parent if hasattr(self.parent, "winfo_id") else getattr(tk, "_default_root", None)
            if target_w is not None:
                s = float(ScalingTracker.get_widget_scaling(target_w))
                if s > 0:
                    return s
        except Exception:
            pass
        return 1.0

    def post(self, x, y):
        return self._open_at(int(x), int(y), is_submenu=False)

    def tk_popup(self, x, y, entry=None):
        return self._open_at(int(x), int(y), is_submenu=False)

    def _close_submenu(self):
        self._cancel_submenu_close_job()
        if self._active_submenu is not None:
            try:
                self._active_submenu._destroy_popup_only()
            except Exception:
                pass
            if self._active_submenu in _ACTIVE_CONTEXT_MENU["menus"]:
                _ACTIVE_CONTEXT_MENU["menus"].remove(self._active_submenu)
            self._active_submenu = None

    def _close_submenu_if_pointer_outside(self):
        self._submenu_close_job = None
        sub = self._active_submenu
        if sub is None or not sub.winfo_exists():
            return
        try:
            px, py = self._popup_win.winfo_pointerxy()
            sx0, sy0, sw, sh = sub._get_menu_rect()
            if sx0 <= px <= sx0 + sw and sy0 <= py <= sy0 + sh:
                return
        except Exception:
            pass
        self._close_submenu()

    def _schedule_close_submenu(self):
        self._cancel_submenu_close_job()
        if self._active_submenu is None:
            return
        if self._popup_win is not None:
            try:
                if self._popup_win.winfo_exists():
                    self._submenu_close_job = self._popup_win.after(120, self._close_submenu_if_pointer_outside)
                    return
            except Exception:
                pass
        self._close_submenu()

    def _open_at(self, x, y, is_submenu=False, parent_rect=None):
        if not is_submenu:
            _close_active_dropdown_popover(mark_closed_owner=False)
            _close_active_context_menu()
        else:
            self._destroy_popup_only()

        if not self._items:
            return

        is_dark = str(ctk.get_appearance_mode()).lower() == "dark"
        bg_hex = "#1E293B" if is_dark else "#FFFFFF"
        border_hex = "#475569" if is_dark else "#CBD5E1"
        sep_hex = "#334155" if is_dark else "#E2E8F0"

        scale = self._get_dpi_scale()
        font_obj = ctk.CTkFont(family="Microsoft YaHei", size=self.UNIFIED_FONT_SIZE)

        master_widget = self.parent
        while isinstance(master_widget, ModernContextMenu):
            master_widget = master_widget.parent
        if master_widget is None or not hasattr(master_widget, "tk"):
            master_widget = getattr(tk, "_default_root", None)

        pop = tk.Toplevel(master_widget)
        pop.wm_overrideredirect(True)
        try:
            pop.attributes("-topmost", True)
        except Exception:
            pass
        pop.configure(bg=border_hex)
        self._popup_win = pop
        if self not in _ACTIVE_CONTEXT_MENU["menus"]:
            _ACTIVE_CONTEXT_MENU["menus"].append(self)

        outer = tk.Frame(
            pop,
            bg=bg_hex,
            highlightbackground=border_hex,
            highlightcolor=border_hex,
            highlightthickness=1,
            bd=0,
        )
        outer.pack(fill=tk.BOTH, expand=True)

        action_items = [it for it in self._items if it.get("type") != "separator"]
        use_scroll = len(action_items) > 12
        pad_px = max(4, int(round(4 * scale)))
        if use_scroll:
            container = ctk.CTkScrollableFrame(
                outer,
                fg_color=bg_hex,
                bg_color=bg_hex,
                corner_radius=0,
            )
            container.pack(fill=tk.BOTH, expand=True, padx=2, pady=2)
        else:
            container = tk.Frame(outer, bg=bg_hex, bd=0, highlightthickness=0)
            container.pack(fill=tk.BOTH, expand=True, padx=pad_px, pady=pad_px)

        row_h = 28
        row_pad_y = max(1, int(round(1 * scale)))
        sep_pad_y = max(3, int(round(4 * scale)))
        btns = []
        sep_count = 0
        display_labels = []

        def _invoke_command(item_dict):
            if item_dict.get("state", "normal") == "disabled":
                return
            _close_active_context_menu()
            cmd = item_dict.get("command")
            if callable(cmd):
                cmd()

        def _invoke_checkbutton(item_dict):
            if item_dict.get("state", "normal") == "disabled":
                return
            var = item_dict.get("variable")
            on_val = item_dict.get("onvalue", True)
            off_val = item_dict.get("offvalue", False)
            if var is not None:
                try:
                    curr = var.get()
                    var.set(off_val if curr == on_val else on_val)
                except Exception:
                    pass
            _close_active_context_menu()
            cmd = item_dict.get("command")
            if callable(cmd):
                cmd()

        def _open_cascade(item_dict, btn_widget):
            self._cancel_submenu_close_job()
            if item_dict.get("state", "normal") == "disabled":
                return
            sub = item_dict.get("menu")
            if not isinstance(sub, ModernContextMenu):
                return
            if self._active_submenu is sub and sub.winfo_exists():
                return
            self._close_submenu()
            self._active_submenu = sub
            px0, py0, pw, ph = self._get_menu_rect()
            sub_x = px0 + pw - 2
            try:
                by = int(btn_widget.winfo_rooty())
                sub_y = (by - pad_px) if by > 0 else py0
            except Exception:
                sub_y = py0
            sub._open_at(sub_x, sub_y, is_submenu=True, parent_rect=(px0, py0, pw, ph))

        for item in self._items:
            itype = item.get("type", "command")
            if itype == "separator":
                sep_count += 1
                sep = tk.Frame(
                    container,
                    bg=sep_hex,
                    height=max(1, int(round(1 * scale))),
                    bd=0,
                    highlightthickness=0,
                )
                sep.pack(fill=tk.X, padx=max(4, int(round(6 * scale))), pady=sep_pad_y)
                continue

            raw_label = str(item.get("label", ""))
            is_disabled = item.get("state", "normal") == "disabled"
            is_checked = False
            if itype == "checkbutton":
                var = item.get("variable")
                on_val = item.get("onvalue", True)
                if var is not None:
                    try:
                        is_checked = bool(var.get() == on_val)
                    except Exception:
                        is_checked = False
                disp_text = f"✓  {raw_label}" if is_checked else f"    {raw_label}"
            elif itype == "cascade":
                disp_text = f"{raw_label}   ▸"
            else:
                disp_text = raw_label

            display_labels.append(disp_text)
            is_danger = any(k in raw_label for k in ("删除", "清空", "移除")) and not is_disabled

            if is_disabled:
                fg_c = bg_hex
                hover_c = bg_hex
                txt_c = ("#94A3B8", "#64748B")
            elif is_danger:
                fg_c = bg_hex
                hover_c = ("#FEF2F2", "#450A0A")
                txt_c = ("#DC2626", "#FCA5A5")
            elif itype == "checkbutton" and is_checked:
                fg_c = ("#EFF6FF", "#1E3A8A")
                hover_c = ("#DBEAFE", "#1E40AF")
                txt_c = ("#2563EB", "#93C5FD")
            else:
                fg_c = bg_hex
                hover_c = ("#F1F5F9", "#334155")
                txt_c = ("#1E293B", "#F8FAFC")

            btn = ctk.CTkButton(
                container,
                text=disp_text,
                width=80,
                height=row_h,
                corner_radius=6,
                anchor="w",
                font=font_obj,
                bg_color=bg_hex,
                fg_color=fg_c,
                hover_color=hover_c,
                text_color=txt_c,
                text_color_disabled=("#94A3B8", "#64748B"),
                hover=(not is_disabled),
                state=("disabled" if is_disabled else "normal"),
            )
            try:
                btn.grid_columnconfigure(0, weight=0, minsize=10)
                if getattr(btn, "_text_label", None) is not None:
                    btn._text_label.configure(anchor="w", justify="left")
            except Exception:
                pass

            if itype == "command":
                btn.configure(command=lambda it=item: _invoke_command(it))
                btn.bind("<Enter>", lambda _e: self._schedule_close_submenu(), add="+")
            elif itype == "checkbutton":
                btn.configure(command=lambda it=item: _invoke_checkbutton(it))
                btn.bind("<Enter>", lambda _e: self._schedule_close_submenu(), add="+")
            elif itype == "cascade":
                btn.configure(command=lambda it=item, b=btn: _open_cascade(it, b))
                btn.bind("<Enter>", lambda _e, it=item, b=btn: _open_cascade(it, b), add="+")

            btn.pack(fill=tk.X, padx=2, pady=row_pad_y)
            btns.append(btn)

        try:
            pop.update_idletasks()
        except Exception:
            pass

        max_text_px = 0
        try:
            tk_f = tkfont.Font(family="Microsoft YaHei", size=self.UNIFIED_FONT_SIZE)
            for s_lbl in display_labels:
                max_text_px = max(max_text_px, int(tk_f.measure(s_lbl)))
        except Exception:
            max_text_px = max((len(s_lbl) * 12 for s_lbl in display_labels), default=100)

        scaled_text_w = int(math.ceil(max_text_px * scale)) + int(math.ceil((52 if use_scroll else 36) * scale))
        req_w = int(outer.winfo_reqwidth()) if hasattr(outer, "winfo_reqwidth") else 0
        pop_w = max(req_w, scaled_text_w, int(136 * scale))

        visible_btns = min(len(btns), 12) if use_scroll else len(btns)
        btn_phys_h = max((int(b.winfo_reqheight()) for b in btns), default=int(math.ceil(row_h * scale)))
        sep_phys_h = sep_count * (max(1, int(round(1 * scale))) + 2 * sep_pad_y)
        calc_h = visible_btns * (btn_phys_h + 2 * row_pad_y) + sep_phys_h + 2 * pad_px + 4
        req_h = int(outer.winfo_reqheight()) if (not use_scroll and hasattr(outer, "winfo_reqheight")) else 0
        pop_h = max(calc_h, req_h)

        pos_x = int(x)
        pos_y = int(y)
        mon_left, mon_top, mon_right, mon_bottom = _get_physical_screen_bounds(
            pop, scale=scale, ref_x=pos_x, ref_y=pos_y
        )
        if pos_x + pop_w > mon_right - 8:
            if is_submenu and parent_rect is not None:
                pos_x = max(mon_left + 4, int(parent_rect[0]) - pop_w + 2)
            else:
                pos_x = max(mon_left + 4, mon_right - pop_w - 8)
        if pos_x < mon_left + 4:
            pos_x = mon_left + 4
        if pos_y + pop_h > mon_bottom - 12:
            pos_y = max(mon_top + 4, mon_bottom - pop_h - 12)
        if pos_y < mon_top + 4:
            pos_y = mon_top + 4

        pop.geometry(f"{pop_w}x{pop_h}+{pos_x}+{pos_y}")
        self._pop_bounds = (pos_x, pos_y, pop_w, pop_h)
        self._opened_monotonic = time.monotonic()
        try:
            pop.update_idletasks()
        except Exception:
            pass

        if not is_submenu:
            top = None
            bound_ids = []
            init_top_rect = None
            try:
                top = master_widget.winfo_toplevel() if master_widget is not None else None
                if top is not None:
                    init_top_rect = (
                        int(top.winfo_rootx()),
                        int(top.winfo_rooty()),
                        int(top.winfo_width()),
                        int(top.winfo_height()),
                    )
            except Exception:
                top = None

            def _point_in_any_menu(px, py):
                for m in list(_ACTIVE_CONTEXT_MENU.get("menus") or []):
                    wx0, wy0, ww, wh = m._get_menu_rect()
                    if ww > 0 and wh > 0 and wx0 <= px <= wx0 + ww and wy0 <= py <= wy0 + wh:
                        return True
                return False

            def _on_global_press(_event=None):
                if not _ACTIVE_CONTEXT_MENU.get("menus"):
                    return
                if (time.monotonic() - self._opened_monotonic) < 0.15:
                    return
                try:
                    px, py = pop.winfo_pointerxy()
                    if _point_in_any_menu(px, py):
                        return
                except Exception:
                    pass
                _close_active_context_menu()

            def _on_escape(_event=None):
                _close_active_context_menu()

            def _on_configure(event=None):
                if event is not None and getattr(event, "widget", None) is not top:
                    return
                if (time.monotonic() - self._opened_monotonic) < 0.20:
                    return
                if top is not None and init_top_rect is not None:
                    try:
                        curr_rect = (
                            int(top.winfo_rootx()),
                            int(top.winfo_rooty()),
                            int(top.winfo_width()),
                            int(top.winfo_height()),
                        )
                        if (
                            abs(curr_rect[0] - init_top_rect[0]) <= 2
                            and abs(curr_rect[1] - init_top_rect[1]) <= 2
                            and abs(curr_rect[2] - init_top_rect[2]) <= 2
                            and abs(curr_rect[3] - init_top_rect[3]) <= 2
                        ):
                            return
                    except Exception:
                        return
                _close_active_context_menu()

            if top is not None:
                for seq, fn in (
                    ("<ButtonPress-1>", _on_global_press),
                    ("<ButtonPress-3>", _on_global_press),
                    ("<Escape>", _on_escape),
                    ("<Configure>", _on_configure),
                ):
                    try:
                        bid = top.bind(seq, fn, add="+")
                        if bid:
                            bound_ids.append((top, seq, bid))
                    except Exception:
                        pass

            poll_state = {"job": None, "seen_release": False}
            try:
                import ctypes
                _get_key_state = ctypes.windll.user32.GetAsyncKeyState
            except Exception:
                _get_key_state = None

            def _poll_mouse_outside():
                poll_state["job"] = None
                if not _ACTIVE_CONTEXT_MENU.get("menus") or _get_key_state is None:
                    return
                try:
                    if not pop.winfo_exists():
                        return
                    pressed = any((_get_key_state(vk) & 0x8000) != 0 for vk in (0x01, 0x02, 0x04))
                    if not pressed:
                        poll_state["seen_release"] = True
                    elif poll_state["seen_release"] and (time.monotonic() - self._opened_monotonic) >= 0.15:
                        px, py = pop.winfo_pointerxy()
                        if not _point_in_any_menu(px, py):
                            _close_active_context_menu()
                            return
                    poll_state["job"] = pop.after(40, _poll_mouse_outside)
                except Exception:
                    pass

            if _get_key_state is not None:
                try:
                    poll_state["job"] = pop.after(60, _poll_mouse_outside)
                except Exception:
                    pass

            def _cleanup_bindings():
                if poll_state.get("job") is not None:
                    try:
                        if pop.winfo_exists():
                            pop.after_cancel(poll_state["job"])
                    except Exception:
                        pass
                    poll_state["job"] = None
                for w, seq, bid in bound_ids:
                    try:
                        if w.winfo_exists():
                            w.unbind(seq, bid)
                    except Exception:
                        pass
                bound_ids.clear()

            _ACTIVE_CONTEXT_MENU["cleanup"] = _cleanup_bindings


def make_context_menu(parent, font_size=12):
    """
    统一创建现代卡片式右键菜单（忽略旧版各处不一致的 10/11/15 字号，全局统一使用 12pt 雅黑字体与圆角浅色高亮）。
    """
    return ModernContextMenu(parent, font_size=font_size)


def post_context_menu(menu, event):
    """
    统一弹出右键菜单。支持 ModernContextMenu 与原生 tk.Menu。
    """
    try:
        x_root = int(getattr(event, "x_root", 0) or 0)
        y_root = int(getattr(event, "y_root", 0) or 0)
        ev_widget = getattr(event, "widget", None)
        if x_root <= 0 and y_root <= 0 and ev_widget is not None:
            try:
                x_root = int(ev_widget.winfo_rootx()) + int(getattr(event, "x", 0) or 0)
                y_root = int(ev_widget.winfo_rooty()) + int(getattr(event, "y", 0) or 0)
            except Exception:
                pass
        menu.tk_popup(x_root, y_root)
    except Exception:
        try:
            menu.grab_release()
        except Exception:
            pass


def bind_text_context_menu(widget, readonly=False, extra_items=None):
    """
    为 CTkTextbox / CTkEntry / tk.Text / tk.Entry 绑定统一的现代右键菜单（剪切/复制/粘贴/复制全部/全选/清空内容）。
    """
    if widget is None:
        return

    inner = getattr(widget, "_textbox", None) or getattr(widget, "_entry", None) or widget
    is_text = isinstance(inner, tk.Text) or hasattr(widget, "_textbox")

    def _get_selected_text():
        try:
            if is_text:
                if inner.tag_ranges("sel"):
                    return str(inner.get("sel.first", "sel.last") or "")
                return ""
            if hasattr(inner, "selection_present") and inner.selection_present():
                s0 = int(inner.index("sel.first"))
                s1 = int(inner.index("sel.last"))
                return str(inner.get() or "")[s0:s1]
        except Exception:
            pass
        return ""

    def _get_all_text():
        try:
            if is_text:
                return str(inner.get("1.0", "end-1c") or "")
            return str(inner.get() or "")
        except Exception:
            return ""

    def _is_currently_readonly():
        if readonly:
            return True
        for w_chk in (inner, widget):
            try:
                if str(w_chk.cget("state")).lower() == "disabled":
                    return True
            except Exception:
                pass
        return False

    def _notify_modified(include_paste=False, include_cut=False):
        targets = [inner]
        if widget is not inner:
            targets.append(widget)
        seqs = ["<KeyRelease>"]
        if include_paste:
            seqs.insert(0, "<<Paste>>")
        if include_cut:
            seqs.insert(0, "<<Cut>>")
        for t in targets:
            for seq in seqs:
                try:
                    t.event_generate(seq)
                except Exception:
                    pass

    def _do_copy():
        txt = _get_selected_text()
        if not txt:
            return
        try:
            inner.clipboard_clear()
            inner.clipboard_append(txt)
            inner.update_idletasks()
        except Exception:
            pass

    def _do_copy_all():
        txt = _get_all_text()
        if not txt:
            return
        try:
            inner.clipboard_clear()
            inner.clipboard_append(txt)
            inner.update_idletasks()
        except Exception:
            pass

    def _do_cut():
        if _is_currently_readonly():
            return
        txt = _get_selected_text()
        if not txt:
            return
        try:
            inner.clipboard_clear()
            inner.clipboard_append(txt)
            inner.delete("sel.first", "sel.last")
            inner.update_idletasks()
            _notify_modified(include_cut=True)
        except Exception:
            pass

    def _do_paste():
        if _is_currently_readonly():
            return
        try:
            clip = str(inner.clipboard_get() or "")
        except Exception:
            clip = ""
        if not clip:
            return
        try:
            inner.focus_set()
        except Exception:
            pass
        try:
            if is_text:
                if inner.tag_ranges("sel"):
                    inner.delete("sel.first", "sel.last")
                inner.insert("insert", clip)
                inner.see("insert")
            else:
                if hasattr(inner, "selection_present") and inner.selection_present():
                    inner.delete("sel.first", "sel.last")
                inner.insert("insert", clip)
            _notify_modified(include_paste=True)
        except Exception:
            pass

    def _do_select_all():
        try:
            inner.focus_set()
        except Exception:
            pass
        try:
            if is_text:
                inner.tag_remove("sel", "1.0", tk.END)
                inner.tag_add("sel", "1.0", "end-1c")
                inner.mark_set("insert", "end-1c")
            else:
                inner.selection_range(0, tk.END)
                inner.icursor(tk.END)
        except Exception:
            pass

    def _do_clear_all():
        if _is_currently_readonly():
            return
        try:
            if is_text:
                inner.delete("1.0", tk.END)
            else:
                inner.delete(0, tk.END)
            _notify_modified()
        except Exception:
            pass

    def _on_right_click(event):
        try:
            inner.focus_set()
        except Exception:
            pass

        is_ro = _is_currently_readonly()
        sel_text = _get_selected_text()
        all_text = _get_all_text()
        has_sel = bool(sel_text)
        has_all = bool(all_text.strip() if is_ro else all_text)
        try:
            clip_text = str(inner.clipboard_get() or "")
            has_clip = bool(clip_text)
        except Exception:
            has_clip = False

        menu = make_context_menu(inner)
        if is_ro:
            menu.add_command(
                label="复制选中内容",
                command=_do_copy,
                state="normal" if has_sel else "disabled",
            )
            menu.add_command(
                label="复制全部内容",
                command=_do_copy_all,
                state="normal" if has_all else "disabled",
            )
            menu.add_command(
                label="全选",
                command=_do_select_all,
                state="normal" if has_all else "disabled",
            )
        else:
            menu.add_command(
                label="剪切",
                command=_do_cut,
                state="normal" if has_sel else "disabled",
            )
            menu.add_command(
                label="复制",
                command=_do_copy,
                state="normal" if has_sel else "disabled",
            )
            menu.add_command(
                label="粘贴",
                command=_do_paste,
                state="normal" if has_clip else "disabled",
            )
            menu.add_separator()
            menu.add_command(
                label="复制全部",
                command=_do_copy_all,
                state="normal" if has_all else "disabled",
            )
            menu.add_command(
                label="全选",
                command=_do_select_all,
                state="normal" if has_all else "disabled",
            )
            menu.add_command(
                label="清空内容",
                command=_do_clear_all,
                state="normal" if has_all else "disabled",
            )

        if extra_items:
            menu.add_separator()
            for extra in extra_items:
                if not extra:
                    continue
                lbl = extra[0]
                cb = extra[1] if len(extra) > 1 else None
                st = extra[2] if len(extra) > 2 else "normal"
                menu.add_command(label=lbl, command=cb, state=st)

        post_context_menu(menu, event)
        return "break"

    widget._on_text_right_click = _on_right_click
    if inner is not widget:
        inner._on_text_right_click = _on_right_click

    bind_targets = [inner]
    canvas = getattr(widget, "_canvas", None)
    if canvas is not None and canvas is not inner:
        bind_targets.append(canvas)

    for target in bind_targets:
        try:
            target.bind("<Button-3>", _on_right_click, add="+")
            target.bind("<Button-2>", _on_right_click, add="+")
        except Exception:
            pass
