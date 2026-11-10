"""
Wordlist Editor package.
"""
import tkinter as tk
from typing import Any, Dict, Optional
import customtkinter as ctk
from modules.wordlist_v2 import DEFAULT_REVIEW_STATUS, normalize_wordlist_document

from .rounded_group_list import RoundedGroupList, _split_tokens, _join_tokens
from .editor_table_mixin import EditorTableMixin
from .editor_io_mixin import EditorIOMixin


class VisualWordlistEditor(EditorTableMixin, EditorIOMixin, ctk.CTkFrame):

    def __init__(self, master, colors: Optional[Dict[str, str]] = None, font_family: str = "Microsoft YaHei", on_change=None, extra_actions=None, bottom_actions=None, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self.colors = colors or {
            "surface": "#FFFFFF",
            "surface_soft": "#F8FAFC",
            "border": "#E2E8F0",
            "text": "#17202A",
            "text_soft": "#334155",
            "muted": "#64748B",
            "primary": "#2563EB",
            "primary_hover": "#1D4ED8",
            "success": "#10B981",
            "warning": "#F59E0B",
            "danger": "#EF4444",
        }
        self.font_family = font_family
        self.font_main = ctk.CTkFont(family=font_family, size=12)
        self.font_small = ctk.CTkFont(family=font_family, size=11)
        self.font_title = ctk.CTkFont(family=font_family, size=14, weight="bold")
        self.on_change = on_change
        self.extra_actions = extra_actions or []

        self.document = normalize_wordlist_document({})
        self.current_group_index = 0
        self.current_item_index = None
        self.current_path = None
        self._refreshing = False
        self._item_tree_hover = None
        self.bottom_actions = bottom_actions or []

        self.title_var = tk.StringVar()
        self.group_name_var = tk.StringVar()
        self.group_note_var = tk.StringVar()
        self.group_tags_var = tk.StringVar()
        self.item_label_var = tk.StringVar()
        self.item_note_var = tk.StringVar()
        self.item_tags_var = tk.StringVar()
        self.item_aliases_var = tk.StringVar()
        self.item_source_var = tk.StringVar(value=DEFAULT_REVIEW_STATUS)

        self._build_ui()
        self.set_document(self.document)



__all__ = [
    "RoundedGroupList",
    "_split_tokens",
    "_join_tokens",
    "EditorTableMixin",
    "EditorIOMixin",
    "VisualWordlistEditor",
]
