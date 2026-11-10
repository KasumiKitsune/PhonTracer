"""
Dropdown popover and high-DPI custom popover rendering.
"""
import math
import time
import tkinter as tk
import tkinter.font as tkfont
import customtkinter as ctk

from .ctk_patches import PHONTRACER_THEME


def _close_active_context_menu():
    from .context_menu import _close_active_context_menu as _impl
    return _impl()


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
        pop.withdraw()
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
            pop.deiconify()
            pop.lift()
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

        def _on_global_scroll(_event=None):
            if _ACTIVE_DROPDOWN_POPOVER.get("window") is not pop:
                return
            try:
                if use_scroll:
                    px, py = dropdown.winfo_pointerxy()
                    px0, py0, pw, ph = _get_pop_rect()
                    if px0 <= px <= px0 + pw and py0 <= py <= py0 + ph:
                        return
            except Exception:
                pass
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
                ("<MouseWheel>", _on_global_scroll),
                ("<Button-4>", _on_global_scroll),
                ("<Button-5>", _on_global_scroll),
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
            if _ACTIVE_DROPDOWN_POPOVER.get("window") is not pop:
                return
            try:
                if not pop.winfo_exists() or not dropdown.winfo_exists() or not dropdown.winfo_ismapped():
                    _close_active_dropdown_popover(mark_closed_owner=False)
                    return
                curr_dx, curr_dy = int(dropdown.winfo_rootx()), int(dropdown.winfo_rooty())
                if abs(curr_dx - rx) > 2 or abs(curr_dy - ry) > 2:
                    _close_active_dropdown_popover(mark_closed_owner=False)
                    return
                if _get_key_state is not None:
                    pressed = any((_get_key_state(vk) & 0x8000) != 0 for vk in (0x01, 0x02, 0x04))
                    if not pressed:
                        poll_state["seen_release"] = True
                    elif poll_state["seen_release"] and (time.monotonic() - opened_time) >= 0.12:
                        px, py = pop.winfo_pointerxy()
                        px0, py0, pw, ph = _get_pop_rect()
                        if not (px0 <= px <= px0 + pw and py0 <= py <= py0 + ph):
                            dw, dh = int(dropdown.winfo_width()), int(dropdown.winfo_height())
                            if curr_dx <= px <= curr_dx + dw and curr_dy <= py <= curr_dy + dh:
                                _close_active_dropdown_popover(mark_closed_owner=True)
                            else:
                                _close_active_dropdown_popover(mark_closed_owner=False)
                            return
                poll_state["job"] = pop.after(40, _poll_mouse_outside)
            except Exception:
                pass

        try:
            poll_state["job"] = pop.after(40, _poll_mouse_outside)
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
