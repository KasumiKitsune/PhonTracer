"""
modules.toolkit_ui.script_list_widget: 自定义脚本滚动列表组件。
"""
import tkinter as tk
import customtkinter as ctk

class CTkScriptList(ctk.CTkScrollableFrame):
    """
    A custom scrollable list utilizing CustomTkinter buttons for modern look-and-feel.
    Items have rounded corners, bolder text when selected, and smooth hover effects.
    """
    def __init__(self, master, font_family="Microsoft YaHei", colors=None, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self.font_family = font_family
        self.colors = colors or {
            "primary": "#2563EB",
            "primary_hover": "#1D4ED8",
            "text": "#17202A",
        }
        self.buttons = {} # iid -> button
        self._selected_id = None
        self._select_callback = None
        self._context_callback = None

        # 绑定空白区域右键菜单到 self 和 self._parent_canvas
        def on_bg_right_click(event):
            if self._context_callback:
                self._context_callback(event, None)
            return "break"

        self.bind("<Button-2>", on_bg_right_click, add="+")
        self.bind("<Button-3>", on_bg_right_click, add="+")
        if hasattr(self, "_parent_canvas") and self._parent_canvas:
            self._parent_canvas.bind("<Button-2>", on_bg_right_click, add="+")
            self._parent_canvas.bind("<Button-3>", on_bg_right_click, add="+")

    def get_children(self):
        return list(self.buttons.keys())

    def selection(self):
        if self._selected_id is not None:
            return (self._selected_id,)
        return ()

    def selection_set(self, iid):
        if iid not in self.buttons:
            return

        # Deselect old
        if self._selected_id in self.buttons:
            old_btn = self.buttons[self._selected_id]
            old_btn.configure(
                fg_color="transparent",
                text_color=self.colors.get("text", "#17202A"),
                hover_color=("#F3F4F6", "#262930"),
                font=ctk.CTkFont(family=self.font_family, size=13, weight="normal")
            )

        self._selected_id = iid

        # Select new
        new_btn = self.buttons[iid]
        new_btn.configure(
            fg_color=self.colors.get("primary", "#2563EB"),
            text_color="#FFFFFF",
            hover_color=self.colors.get("primary_hover", "#1D4ED8"),
            font=ctk.CTkFont(family=self.font_family, size=13, weight="bold")
        )

        if self._select_callback:
            class MockEvent:
                def __init__(self, widget):
                    self.widget = widget
            self._select_callback(MockEvent(self))

    def delete(self, iid):
        if iid in self.buttons:
            self.buttons[iid].destroy()
            del self.buttons[iid]
        if self._selected_id == iid:
            self._selected_id = None

    def exists(self, iid):
        return iid in self.buttons

    def selection_clear(self):
        if self._selected_id in self.buttons:
            old_btn = self.buttons[self._selected_id]
            old_btn.configure(
                fg_color="transparent",
                text_color=self.colors.get("text", "#17202A"),
                hover_color=("#F3F4F6", "#262930"),
                font=ctk.CTkFont(family=self.font_family, size=13, weight="normal")
            )
        self._selected_id = None

    def set_context_callback(self, callback):
        self._context_callback = callback

    def insert(self, parent, index, iid, text):
        btn = ctk.CTkButton(
            self,
            text=text,
            anchor="w",
            height=36,
            corner_radius=8,
            fg_color="transparent",
            text_color=self.colors.get("text", "#17202A"),
            hover_color=("#F1F5F9", "#262930"),
            font=ctk.CTkFont(family=self.font_family, size=13, weight="normal"),
            command=lambda: self.selection_set(iid)
        )
        # Force column configuration so the text always starts at a fixed left margin (12px)
        # and doesn't get centered or shifted due to empty column weights in CTkButton's default grid.
        btn.grid_columnconfigure(0, weight=0, minsize=12)
        btn.grid_columnconfigure(1, weight=0, minsize=0)
        btn.grid_columnconfigure(2, weight=0, minsize=0)
        btn.grid_columnconfigure(3, weight=0)
        btn.grid_columnconfigure(4, weight=1, minsize=12)

        # Configure inner label anchor to 'w' so that if the button is narrower than the text,
        # the text is clipped on the right and never on the left.
        if btn._text_label is not None:
            btn._text_label.configure(anchor="w", justify="left")

        btn.pack(fill=tk.X, pady=2, padx=2)
        self.buttons[iid] = btn

        def on_btn_right_click(event):
            if self._selected_id != iid:
                self.selection_set(iid)
            if self._context_callback:
                self._context_callback(event, iid)
            return "break"

        btn._on_right_click = on_btn_right_click
        btn.bind("<Button-2>", on_btn_right_click, add="+")
        btn.bind("<Button-3>", on_btn_right_click, add="+")

    def bind(self, event_name, callback, add=None):
        if event_name == "<<TreeviewSelect>>":
            self._select_callback = callback
        else:
            super().bind(event_name, callback, add)

