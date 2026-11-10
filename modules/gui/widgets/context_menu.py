"""
Modern card-style context menu implementation.
"""
import math
import time
import tkinter as tk
import tkinter.font as tkfont
import customtkinter as ctk

from .ctk_patches import PHONTRACER_THEME
from .dropdown_popover import _get_physical_screen_bounds


def _close_active_dropdown_popover(mark_closed_owner=False):
    from .dropdown_popover import _close_active_dropdown_popover as _impl
    return _impl(mark_closed_owner=mark_closed_owner)


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
        pop.withdraw()
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
            pop.deiconify()
            pop.lift()
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

