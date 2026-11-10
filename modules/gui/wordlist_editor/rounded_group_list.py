"""
RoundedGroupList and token utilities for Wordlist Editor.
"""
import tkinter as tk
import customtkinter as ctk
import re

def _split_tokens(text: str):
    import re
    return [t.strip() for t in re.split(r"[;；,，、\n\t]+", text or "") if t.strip()]


def _join_tokens(values):
    return "；".join([str(v).strip() for v in values or [] if str(v).strip()])


class RoundedGroupList(ctk.CTkScrollableFrame):
    """圆角按钮式组列表，避免原生 Listbox 的割裂感。"""

    def __init__(self, master, colors, font_family="Microsoft YaHei", **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self.colors = colors
        self.font_family = font_family
        self.buttons = []
        self._selected_index = None
        self._select_callback = None
        self._context_callback = None
        self._bind_fast_scroll()

    def _bind_fast_scroll(self):
        def on_wheel(event):
            step = -1 if getattr(event, "delta", 0) > 0 else 1
            try:
                self._parent_canvas.yview_scroll(step * 12, "units")
                return "break"
            except Exception:
                return None
        self.bind("<MouseWheel>", on_wheel, add="+")
        if hasattr(self, "_parent_canvas"):
            self._parent_canvas.bind("<MouseWheel>", on_wheel, add="+")

    def bind(self, event_name, callback, add=None):
        if event_name == "<<ListboxSelect>>":
            self._select_callback = callback
            return None
        if event_name in ("<Button-3>", "<Button-2>"):
            self._context_callback = callback
            for idx, btn in enumerate(self.buttons):
                btn.bind(event_name, lambda event, i=idx: self._post_context(event, i), add=add)
            return super().bind(event_name, lambda event: self._post_context(event, None), add)
        return super().bind(event_name, callback, add)

    def delete(self, start, end=None):
        for btn in self.buttons:
            btn.destroy()
        self.buttons = []
        self._selected_index = None

    def insert(self, index, text):
        idx = len(self.buttons)
        btn = ctk.CTkButton(
            self,
            text=str(text),
            anchor="w",
            height=36,
            corner_radius=10,
            fg_color="transparent",
            text_color=self.colors.get("text", "#17202A"),
            hover_color=("#F1F5F9", "#262930"),
            font=ctk.CTkFont(family=self.font_family, size=13),
            command=lambda i=idx: self.selection_set(i, notify=True),
        )
        btn.grid_columnconfigure(0, weight=0, minsize=10)
        btn.grid_columnconfigure(1, weight=0, minsize=0)
        btn.grid_columnconfigure(2, weight=0, minsize=0)
        btn.grid_columnconfigure(3, weight=0)
        btn.grid_columnconfigure(4, weight=1, minsize=10)
        if getattr(btn, "_text_label", None) is not None:
            btn._text_label.configure(anchor="w", justify="left")
        btn.bind("<Button-3>", lambda event, i=idx: self._post_context(event, i), add="+")
        btn.bind("<Button-2>", lambda event, i=idx: self._post_context(event, i), add="+")
        btn.pack(fill=tk.X, padx=2, pady=2)
        self.buttons.append(btn)

    def _post_context(self, event, index):
        if index is not None:
            self.selection_set(index, notify=True)
        if self._context_callback:
            self._context_callback(event)
        return "break"

    def selection_clear(self, start, end=None):
        if self._selected_index is not None and 0 <= self._selected_index < len(self.buttons):
            self.buttons[self._selected_index].configure(
                fg_color="transparent",
                text_color=self.colors.get("text", "#17202A"),
                hover_color=("#F1F5F9", "#262930"),
                font=ctk.CTkFont(family=self.font_family, size=13),
            )
        self._selected_index = None

    def selection_set(self, index, notify=True):
        try:
            idx = int(index)
        except Exception:
            return
        if idx < 0 or idx >= len(self.buttons):
            return
        self.selection_clear(0, tk.END)
        self._selected_index = idx
        self.buttons[idx].configure(
            fg_color=self.colors.get("primary", "#2563EB"),
            text_color="#FFFFFF",
            hover_color=self.colors.get("primary_hover", "#1D4ED8"),
            font=ctk.CTkFont(family=self.font_family, size=13, weight="bold"),
        )
        if notify and self._select_callback:
            class Event:
                def __init__(self, widget):
                    self.widget = widget
            self._select_callback(Event(self))

    def activate(self, index):
        return None

    def curselection(self):
        return () if self._selected_index is None else (self._selected_index,)


