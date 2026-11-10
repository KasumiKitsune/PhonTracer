# -*- coding: utf-8 -*-
"""
无头沙盒及 CLI 打包环境下的 GUI (tkinter/customtkinter) 与音频 (sounddevice) 存根注入。
"""

from __future__ import annotations

import importlib.util
import sys
import types
from typing import Any, Dict, Optional, Tuple


def _install_headless_gui_and_audio_stubs_if_missing() -> None:
    if "sounddevice" not in sys.modules and importlib.util.find_spec("sounddevice") is None:
        sd_mod = types.ModuleType("sounddevice")
        sd_mod.play = lambda *a, **kw: None
        sd_mod.stop = lambda *a, **kw: None
        sd_mod.wait = lambda *a, **kw: None
        sd_mod.query_devices = lambda *a, **kw: []
        sys.modules["sounddevice"] = sd_mod

    if "tkinter" not in sys.modules and importlib.util.find_spec("tkinter") is None:
        tk_mod = types.ModuleType("tkinter")

        class _DummyWidget:
            def _draw(self, *args: Any, **kwargs: Any) -> None:
                pass

            def __call__(self, *args: Any, **kwargs: Any) -> Any:
                return None

            def __iter__(self):
                return iter(())

            def __contains__(self, item: Any) -> bool:
                return False

            def items(self):
                return ()

            def values(self):
                return ()

            def keys(self):
                return ()

            def theme_use(self, *args: Any, **kwargs: Any) -> str:
                return "default"

            def map(self, *args: Any, **kwargs: Any) -> None:
                pass

            def __getattr__(self, name: str) -> Any:
                if name.startswith("_"):
                    if name in ("_segmented_button", "_text_label", "_image_label", "_canvas", "_entry", "_textbox", "_dropdown_menu"):
                        child = _DummyWidget()
                        object.__setattr__(self, name, child)
                        return child
                    if name in ("_select_button_by_value", "_unselect_button_by_value", "_draw", "_create_grid"):
                        return lambda *args, **kwargs: None
                    raise AttributeError(name)
                return lambda *args, **kwargs: None
            def __init__(self, *args: Any, **kwargs: Any):
                self._cfg = dict(kwargs)
                self.children = {}
                self.master = args[0] if args else kwargs.get("master")
                self._manager = ""
                self._grid_info = {}
                self._bindings = {}
                self._check_state = False
                self._corner_radius = kwargs.get("corner_radius", 6)
                self.tk = "tk"
                self._after_depth = 0
                self._segmented_button = self
                self._buttons_dict = {}
                self._values = kwargs.get("values", [])
                self._current_value = self._values[0] if self._values else ""
                self._geom = "200x120+0+0" 

            def _mouse_wheel_all(self, *args: Any, **kwargs: Any) -> None:
                pass

            def __call__(self, *args: Any, **kwargs: Any) -> Any:
                return self

            def pack(self, *args: Any, **kwargs: Any) -> None:
                self._manager = "pack"

            def pack_forget(self, *args: Any, **kwargs: Any) -> None:
                self._manager = ""

            def pack_propagate(self, *args: Any, **kwargs: Any) -> None:
                pass

            def grid(self, *args: Any, **kwargs: Any) -> None:
                self._manager = "grid"
                self._grid_info = dict(kwargs) if kwargs else {"row": 0}

            def grid_forget(self, *args: Any, **kwargs: Any) -> None:
                self._manager = ""
                self._grid_info = {}

            def grid_remove(self, *args: Any, **kwargs: Any) -> None:
                self._manager = ""
                self._grid_info = {}

            def grid_info(self) -> Dict[str, Any]:
                return dict(self._grid_info)

            def winfo_manager(self) -> str:
                return self._manager

            def grid_propagate(self, *args: Any, **kwargs: Any) -> None:
                pass

            def grid_rowconfigure(self, *args: Any, **kwargs: Any) -> None:
                pass

            def grid_columnconfigure(self, *args: Any, **kwargs: Any) -> None:
                pass

            def rowconfigure(self, *args: Any, **kwargs: Any) -> None:
                pass

            def columnconfigure(self, *args: Any, **kwargs: Any) -> None:
                pass

            def place(self, *args: Any, **kwargs: Any) -> None:
                pass

            def place_forget(self, *args: Any, **kwargs: Any) -> None:
                pass

            def bind(self, sequence: Any = None, func: Any = None, add: Any = None) -> Any:
                if sequence is None:
                    return tuple(self._bindings.keys())
                self._bindings[str(sequence)] = func
                if str(sequence) == "<Control-c>":
                    self._bindings["<Control-Key-c>"] = func
                return "bind_id"

            def unbind(self, *args: Any, **kwargs: Any) -> None:
                pass

            def configure(self, *args: Any, **kwargs: Any) -> None:
                self._cfg.update(kwargs)

            config = configure

            def cget(self, key: str) -> Any:
                return self._cfg.get(key, "")

            def destroy(self, *args: Any, **kwargs: Any) -> None:
                pass

            def winfo_exists(self) -> bool:
                return True

            def winfo_width(self) -> int:
                return 800

            def winfo_height(self) -> int:
                return 600

            def winfo_screenwidth(self) -> int:
                return 1920

            def winfo_screenheight(self) -> int:
                return 1080

            def winfo_toplevel(self) -> "_DummyWidget":
                return self

            def focus_get(self) -> None:
                return None

            def focus_set(self) -> None:
                pass

            def focus_force(self) -> None:
                pass

            def lift(self) -> None:
                pass

            def title(self, *args: Any) -> None:
                pass

            def geometry(self, *args: Any) -> Any:
                if args:
                    self._geom = str(args[0])
                return getattr(self, "_geom", "200x120+0+0")

            def minsize(self, *args: Any, **kwargs: Any) -> None:
                pass

            def maxsize(self, *args: Any, **kwargs: Any) -> None:
                pass

            def resizable(self, *args: Any, **kwargs: Any) -> None:
                pass

            def withdraw(self, *args: Any, **kwargs: Any) -> None:
                pass

            def deiconify(self, *args: Any, **kwargs: Any) -> None:
                pass

            def iconbitmap(self, *args: Any, **kwargs: Any) -> None:
                pass

            def iconphoto(self, *args: Any, **kwargs: Any) -> None:
                pass

            def wm_iconphoto(self, *args: Any, **kwargs: Any) -> None:
                pass

            def grab_set(self, *args: Any, **kwargs: Any) -> None:
                pass

            def grab_release(self, *args: Any, **kwargs: Any) -> None:
                pass

            def wait_window(self, *args: Any, **kwargs: Any) -> None:
                pass

            def nametowidget(self, *args: Any, **kwargs: Any) -> "_DummyWidget":
                return self

            def itemconfigure(self, *args: Any, **kwargs: Any) -> None:
                pass

            def create_image(self, *args: Any, **kwargs: Any) -> int:
                return 1

            def bind_all(self, *args: Any, **kwargs: Any) -> None:
                pass

            def unbind_all(self, *args: Any, **kwargs: Any) -> None:
                pass

            def winfo_children(self):
                return list(self.children.values())

            def winfo_rootx(self) -> int:
                return 0

            def winfo_rooty(self) -> int:
                return 0

            def winfo_reqwidth(self) -> int:
                return 100

            def winfo_reqheight(self) -> int:
                return 30

            def winfo_pointerxy(self) -> Tuple[int, int]:
                return (100, 100)

            def transient(self, *args: Any) -> None:
                pass

            def protocol(self, *args: Any) -> None:
                pass

            def after(self, _ms: int, func: Any = None, *args: Any) -> str:
                if callable(func) and getattr(func, "__name__", "") != "process_gui_queue":
                    depth = getattr(self, "_after_depth", 0)
                    if depth < 2:
                        self._after_depth = depth + 1
                        try:
                            func(*args)
                        finally:
                            self._after_depth = depth
                return "after_id"

            def after_cancel(self, *args: Any) -> None:
                pass

            def update_idletasks(self) -> None:
                pass

            def update(self) -> None:
                pass

            def mainloop(self) -> None:
                pass

            def get(self, *args: Any) -> Any:
                return self._cfg.get("value", "")

            def set(self, val: Any = "") -> None:
                self._cfg["value"] = val

            def select(self) -> None:
                self._check_state = True
                self._cfg["value"] = 1
                self._draw()

            def deselect(self) -> None:
                self._check_state = False
                self._cfg["value"] = 0
                self._draw()

            def insert(self, *args: Any, **kwargs: Any) -> str:
                return "item"

            def delete(self, *args: Any, **kwargs: Any) -> None:
                pass

            def get_children(self, *args: Any) -> Tuple[str, ...]:
                return ()

            def selection(self) -> Tuple[str, ...]:
                return ()

            def selection_set(self, *args: Any, **kwargs: Any) -> None:
                pass

            def selection_add(self, *args: Any, **kwargs: Any) -> None:
                pass

            def heading(self, *args: Any, **kwargs: Any) -> None:
                pass

            def column(self, *args: Any, **kwargs: Any) -> None:
                pass

            def tag_configure(self, *args: Any, **kwargs: Any) -> None:
                pass

            def yview(self, *args: Any, **kwargs: Any) -> None:
                pass

            def xview(self, *args: Any, **kwargs: Any) -> None:
                pass

            def add(self, name: str, *args: Any, **kwargs: Any) -> "_DummyWidget":
                return _DummyWidget()

        class _DummyVar:
            def __init__(self, master: Any = None, value: Any = None, name: Optional[str] = None):
                self._val = value

            def get(self) -> Any:
                return self._val

            def set(self, val: Any) -> None:
                self._val = val

            def trace_add(self, *args: Any, **kwargs: Any) -> str:
                return "trace"

        for const_name, const_val in {
            "NO": False,
            "YES": True,
            "TRUE": True,
            "FALSE": False,
            "WORD": "word",
            "FLAT": "flat",
            "SOLID": "solid",
            "RAISED": "raised",
            "SUNKEN": "sunken",
            "GROOVE": "groove",
            "RIDGE": "ridge",
            "LEFT": "left",
            "RIGHT": "right",
            "TOP": "top",
            "BOTTOM": "bottom",
            "BOTH": "both",
            "X": "x",
            "Y": "y",
            "NONE": "none",
            "END": "end",
            "NORMAL": "normal",
            "DISABLED": "disabled",
            "ACTIVE": "active",
            "HIDDEN": "hidden",
            "NW": "nw",
            "NE": "ne",
            "SW": "sw",
            "SE": "se",
            "N": "n",
            "S": "s",
            "E": "e",
            "W": "w",
            "CENTER": "center",
            "HORIZONTAL": "horizontal",
            "VERTICAL": "vertical",
            "WORD": "word",
            "CHAR": "char",
            "FLAT": "flat",
            "SOLID": "solid",
            "SUNKEN": "sunken",
            "RAISED": "raised",
            "GROOVE": "groove",
            "RIDGE": "ridge",
            "SINGLE": "single",
            "BROWSE": "browse",
            "MULTIPLE": "multiple",
            "EXTENDED": "extended",
        }.items():
            setattr(tk_mod, const_name, const_val)

        tk_mod.Tk = _DummyWidget
        tk_mod.Toplevel = _DummyWidget
        tk_mod.Frame = _DummyWidget
        tk_mod.Label = _DummyWidget
        tk_mod.Button = _DummyWidget
        tk_mod.Entry = _DummyWidget
        tk_mod.Text = _DummyWidget
        tk_mod.Canvas = _DummyWidget
        tk_mod.Listbox = _DummyWidget
        tk_mod.Scrollbar = _DummyWidget
        tk_mod.Menu = _DummyWidget
        tk_mod.PanedWindow = _DummyWidget
        tk_mod.Widget = _DummyWidget
        tk_mod.Event = _DummyWidget
        tk_mod.Variable = _DummyVar
        tk_mod.StringVar = lambda master=None, value="", name=None: _DummyVar(master, value, name)
        tk_mod.IntVar = lambda master=None, value=0, name=None: _DummyVar(master, int(value or 0), name)
        tk_mod.DoubleVar = lambda master=None, value=0.0, name=None: _DummyVar(master, float(value or 0.0), name)
        tk_mod.BooleanVar = lambda master=None, value=False, name=None: _DummyVar(master, bool(value), name)
        tk_mod.TclError = RuntimeError

        ttk_mod = types.ModuleType("tkinter.ttk")
        ttk_mod.Treeview = _DummyWidget
        ttk_mod.Scrollbar = _DummyWidget
        ttk_mod.Style = _DummyWidget
        ttk_mod.Frame = _DummyWidget
        ttk_mod.Label = _DummyWidget
        ttk_mod.Button = _DummyWidget
        ttk_mod.Combobox = _DummyWidget
        ttk_mod.Notebook = _DummyWidget
        ttk_mod.Progressbar = _DummyWidget
        ttk_mod.Separator = _DummyWidget

        mb_mod = types.ModuleType("tkinter.messagebox")
        mb_mod.showinfo = lambda *a, **kw: "ok"
        mb_mod.showwarning = lambda *a, **kw: "ok"
        mb_mod.showerror = lambda *a, **kw: "ok"
        mb_mod.askyesno = lambda *a, **kw: True
        mb_mod.askokcancel = lambda *a, **kw: True
        mb_mod.askyesnocancel = lambda *a, **kw: True

        fd_mod = types.ModuleType("tkinter.filedialog")
        fd_mod.askopenfilename = lambda *a, **kw: ""
        fd_mod.askopenfilenames = lambda *a, **kw: ()
        fd_mod.asksaveasfilename = lambda *a, **kw: ""
        fd_mod.askdirectory = lambda *a, **kw: ""

        sd_dlg_mod = types.ModuleType("tkinter.simpledialog")
        sd_dlg_mod.askstring = lambda *a, **kw: None
        sd_dlg_mod.askinteger = lambda *a, **kw: None
        sd_dlg_mod.askfloat = lambda *a, **kw: None

        font_mod = types.ModuleType("tkinter.font")
        class _DummyFont(_DummyWidget):
            def measure(self, text=""):
                return len(str(text)) * 8
            def metrics(self, *a, **kw):
                return {"ascent": 10, "descent": 2, "linespace": 14}
        font_mod.Font = _DummyFont
        font_mod.nametofont = lambda *a, **kw: _DummyFont()
        font_mod.families = lambda *a, **kw: ("Microsoft YaHei", "Consolas", "Arial")

        tk_mod.__path__ = []
        tk_mod.ttk = ttk_mod
        tk_mod.font = font_mod
        tk_mod.messagebox = mb_mod
        tk_mod.filedialog = fd_mod
        tk_mod.simpledialog = sd_dlg_mod

        sys.modules["tkinter"] = tk_mod
        sys.modules["tkinter.ttk"] = ttk_mod
        sys.modules["tkinter.font"] = font_mod
        sys.modules["tkinter.messagebox"] = mb_mod
        sys.modules["tkinter.filedialog"] = fd_mod
        sys.modules["tkinter.simpledialog"] = sd_dlg_mod

        if "customtkinter" not in sys.modules and importlib.util.find_spec("customtkinter") is None:
            ctk_mod = types.ModuleType("customtkinter")
            for attr_name in (
                "CTk",
                "CTkToplevel",
                "CTkFrame",
                "CTkScrollableFrame",
                "CTkLabel",
                "CTkButton",
                "CTkEntry",
                "CTkTextbox",
                "CTkOptionMenu",
                "CTkComboBox",
                "CTkCheckBox",
                "CTkRadioButton",
                "CTkSwitch",
                "CTkSlider",
                "CTkProgressBar",
                "CTkSegmentedButton",
                "CTkTabview",
                "CTkScrollbar",
                "CTkCanvas",
                "CTkFont",
                "CTkImage",
                "CTkInputDialog",
            ):
                setattr(ctk_mod, attr_name, _DummyWidget)
            ctk_mod.StringVar = tk_mod.StringVar
            ctk_mod.IntVar = tk_mod.IntVar
            ctk_mod.DoubleVar = tk_mod.DoubleVar
            ctk_mod.BooleanVar = tk_mod.BooleanVar
            ctk_mod.set_appearance_mode = lambda *a, **kw: None
            ctk_mod.get_appearance_mode = lambda *a, **kw: "Light"
            ctk_mod.set_default_color_theme = lambda *a, **kw: None
            for const_name in ("LEFT", "RIGHT", "TOP", "BOTTOM", "BOTH", "X", "Y", "END", "NORMAL", "DISABLED"):
                setattr(ctk_mod, const_name, getattr(tk_mod, const_name))
            sys.modules["customtkinter"] = ctk_mod

        try:
            import PIL
            if not hasattr(PIL, "ImageTk"):
                imgtk_mod = types.ModuleType("PIL.ImageTk")
                imgtk_mod.PhotoImage = _DummyWidget
                PIL.ImageTk = imgtk_mod
                sys.modules["PIL.ImageTk"] = imgtk_mod
        except Exception:
            pass

        try:
            import matplotlib.backends.backend_tkagg as _tkagg
        except Exception:
            tkagg_mod = types.ModuleType("matplotlib.backends.backend_tkagg")

            class _DummyFigureCanvasTkAgg:
                def __init__(self, figure: Any = None, master: Any = None):
                    self.figure = figure
                    self.master = master
                    self._tk_widget = _DummyWidget()

                def get_tk_widget(self) -> _DummyWidget:
                    return self._tk_widget

                def draw(self) -> None:
                    pass

                def draw_idle(self) -> None:
                    pass

                def mpl_connect(self, *args: Any, **kwargs: Any) -> int:
                    return 1

                def copy_from_bbox(self, *args: Any, **kwargs: Any) -> Any:
                    return None

                def restore_region(self, *args: Any, **kwargs: Any) -> None:
                    pass

                def blit(self, *args: Any, **kwargs: Any) -> None:
                    pass

            tkagg_mod.FigureCanvasTkAgg = _DummyFigureCanvasTkAgg
            sys.modules["matplotlib.backends.backend_tkagg"] = tkagg_mod

__all__ = ["_install_headless_gui_and_audio_stubs_if_missing"]
