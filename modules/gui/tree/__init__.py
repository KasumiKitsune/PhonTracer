"""
Project tree package with mixin-based ProjectTreePanel.
"""
import tkinter as tk
import customtkinter as ctk

from .tree_dialogs import (
    EXCLUSION_REASON_DETAIL_PLACEHOLDER,
    EXCLUSION_REASON_CATEGORY_PLACEHOLDER,
    EXCLUSION_REASON_CUSTOM_CATEGORY,
    EXCLUSION_REASON_CATEGORIES,
    EXCLUSION_REASON_LEGACY_ALIASES,
    TREE_INFO_MAX_CHARS,
    EXCLUSION_REASON_SHORT_LABELS,
    format_exclusion_reason,
    parse_exclusion_reason,
    apply_custom_dropdown_arrow,
    CanvasButton,
    AnomalyWarningDialog,
    ExclusionReasonDialog,
    ItemPropertiesDialog,
)
from .panel_ui_mixin import TreePanelUIMixin
from .anomaly_stats_mixin import AnomalyStatsMixin
from .tree_export_mixin import TreeExportMixin


class ProjectTreePanel(TreeExportMixin, AnomalyStatsMixin, TreePanelUIMixin):
    def __init__(self, parent, icons, items_dict, app_state_params, on_item_selected_callback, on_clear_canvas_callback, tk_icons=None, app=None):
        self.parent = parent
        self.app = app
        self.icons = icons
        self.tk_icons = tk_icons or {}
        self.items = items_dict
        self.app_state_params = app_state_params
        self.on_item_selected = on_item_selected_callback
        self.on_clear_canvas = on_clear_canvas_callback
        if app and hasattr(app, 'export_numbering_rule_var'):
            self.num_rule_var = app.export_numbering_rule_var
        else:
            self.num_rule_var = ctk.StringVar(value="continuous")

        self.project_groups = []
        self.group_nodes = {}
        self.current_iid = None
        self.tree_drag_items = None
        self.last_hover = None

        self.warning_group_id = None
        self.warning_iids = {}
        self._rebuild_timer = None
        self._rebuilding = False

        try:
            self.font_title = ctk.CTkFont(family="Microsoft YaHei", size=15, weight="bold")
            self.font_main = ctk.CTkFont(family="Microsoft YaHei", size=13)
            self.font_code = ctk.CTkFont(family="Consolas", size=13)
        except Exception:
            self.font_title = ("Microsoft YaHei", 15, "bold")
            self.font_main = ("Microsoft YaHei", 13)
            self.font_code = ("Consolas", 13)

        self.setup_ui()



__all__ = [
    "EXCLUSION_REASON_DETAIL_PLACEHOLDER",
    "EXCLUSION_REASON_CATEGORY_PLACEHOLDER",
    "EXCLUSION_REASON_CUSTOM_CATEGORY",
    "EXCLUSION_REASON_CATEGORIES",
    "EXCLUSION_REASON_LEGACY_ALIASES",
    "TREE_INFO_MAX_CHARS",
    "EXCLUSION_REASON_SHORT_LABELS",
    "format_exclusion_reason",
    "parse_exclusion_reason",
    "apply_custom_dropdown_arrow",
    "CanvasButton",
    "AnomalyWarningDialog",
    "ExclusionReasonDialog",
    "ItemPropertiesDialog",
    "TreePanelUIMixin",
    "AnomalyStatsMixin",
    "TreeExportMixin",
    "ProjectTreePanel",
]
