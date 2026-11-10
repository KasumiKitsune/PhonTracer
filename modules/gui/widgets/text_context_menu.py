"""
Context menu binding for text widgets.
"""
import tkinter as tk
import customtkinter as ctk

from .ctk_patches import PHONTRACER_THEME
from .context_menu import (
    ModernContextMenu,
    post_context_menu,
    make_context_menu,
    _ACTIVE_CONTEXT_MENU,
    _close_active_context_menu,
)

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

