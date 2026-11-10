"""
PhonTracer GUI widgets subpackage.
"""
from .ctk_patches import (
    PHONTRACER_THEME,
    install_dynamic_checkbox_patch,
    CTkReleaseButton,
    ToolTip,
    AutoScrollbar,
)
from .dropdown_popover import (
    _ACTIVE_DROPDOWN_POPOVER,
    _get_physical_screen_bounds,
    _close_active_dropdown_popover,
    _install_custom_dropdown_popover,
    _apply_custom_arrow,
    apply_custom_arrow,
    apply_custom_dropdown_arrow,
)
from .context_menu import (
    _ACTIVE_CONTEXT_MENU,
    _close_active_context_menu,
    ModernContextMenu,
    make_context_menu,
    post_context_menu,
)
from .text_context_menu import (
    bind_text_context_menu,
)

__all__ = [
    "PHONTRACER_THEME",
    "install_dynamic_checkbox_patch",
    "CTkReleaseButton",
    "ToolTip",
    "AutoScrollbar",
    "_ACTIVE_DROPDOWN_POPOVER",
    "_get_physical_screen_bounds",
    "_close_active_dropdown_popover",
    "_install_custom_dropdown_popover",
    "_apply_custom_arrow",
    "apply_custom_arrow",
    "apply_custom_dropdown_arrow",
    "_ACTIVE_CONTEXT_MENU",
    "_close_active_context_menu",
    "ModernContextMenu",
    "make_context_menu",
    "post_context_menu",
    "bind_text_context_menu",
]
