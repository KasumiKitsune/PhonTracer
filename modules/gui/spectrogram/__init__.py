"""
Spectrogram panel package with mixin-based SpectrogramPanel.
"""
import copy
import time
import tkinter as tk
from tkinter import messagebox
import customtkinter as ctk
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import numpy as np
import sounddevice as sd
try:
    import parselmouth
except ImportError:
    from modules import _parselmouth_fallback as parselmouth
    import sys as _sys
    _sys.modules.setdefault("parselmouth", parselmouth)
    _sys.modules.setdefault("parselmouth.praat", parselmouth.praat)
from modules.audio_core import SILENCE_AMPLITUDE_THRESHOLD
from modules.anomaly_detection import detect_pitch_anomaly_points
from modules.ui_widgets import CTkReleaseButton

from .toolbar_nav_mixin import ToolbarNavMixin
from .canvas_renderer_mixin import CanvasRendererMixin
from .interaction_mixin import InteractionMixin
from .audio_player_mixin import AudioPlayerMixin
from .eraser_mixin import EraserMixin


class SpectrogramPanel(
    ToolbarNavMixin,
    CanvasRendererMixin,
    InteractionMixin,
    AudioPlayerMixin,
    EraserMixin,
):
    def __init__(self, parent, icons, on_time_changed_callback, on_auto_detect_callback, on_export_callback, app=None):
        self.parent = parent
        self.icons = icons
        self.on_time_changed = on_time_changed_callback
        self.on_auto_detect_callback = on_auto_detect_callback
        self.on_export_callback = on_export_callback
        self.app = app

        self.current_item = None
        self.current_item_iid = None
        self.dragging = None  # 取值: ('start', idx), ('end', idx), ('shared', idx), ('overlap_pending', idx, px), 'cursor', 'play_select', 'nav_pan'
        self.ax_nav = None
        self.ax = None
        self.ax2 = None
        self.nav_lens_patch = None
        self.nav_bound_lines = []
        self.nav_span_fills = []
        self.switch_trim_silence = None
        self.bound_lines = []
        self.span_fills = []
        self.char_texts = []
        self.pitch_main_line = None
        self.anomaly_line = None
        self._pitch_view_indices = None
        self._audition_flash_patches = []
        self._audition_flash_job = None

        try:
            self.font_title = ctk.CTkFont(family="Microsoft YaHei", size=13, weight="bold")
        except Exception:
            self.font_title = ("Microsoft YaHei", 13, "bold")

        # Cursor and playback state
        self.is_playing = False
        self.play_rate = 1.0
        self.play_start_sys_time = 0
        self.play_start_audio_time = 0
        self.play_end_audio_time = 0
        self.cursor_x = None
        self.cursor_char_index = None
        self.cursor_line = None
        self.cursor_text = None
        self._playback_job = None
        self.playback_selection = None
        self.selection_patch = None
        self.selection_anchor_x = None
        self.selection_drag_started = False
        self.playback_loop_enabled = False
        self.playback_range_mode = "自动"
        self.playback_status_var = None
        self.playback_range_var = None
        self.playback_loop_var = None
        self.playback_mode_buttons = {}
        self.playback_domain_start = None
        self.playback_domain_end = None

        # Undo / Redo state
        self.undo_stack = []
        self.redo_stack = []
        self._drag_pre_snapshot = None

        # Right-click pan & context menu disambiguation
        self._pan_start_x = None
        self._pan_start_xlim = None
        self._pan_moved = False
        self._suppress_next_context_menu = False

        # Eraser mode state
        self.eraser_mode = False
        self.erasing = False
        self.restoring_points = False
        self.erase_radius = 15.0  # Default pixel radius
        self.eraser_circle = None  # Matplotlib patch for displaying the eraser scope
        self.background = None
        self.session_erased_pitch_indices = set()
        self.session_erased_formant_indices = {"f1": set(), "f2": set(), "f3": set()}
        self.session_erased_pitch_points = {}
        self.session_erased_formant_points = {"f1": {}, "f2": {}, "f3": {}}
        self.erased_pitch_layer = None
        self.erased_f1_layer = None
        self.erased_f2_layer = None
        self.erased_f3_layer = None

        self.setup_ui()



__all__ = [
    "ToolbarNavMixin",
    "CanvasRendererMixin",
    "InteractionMixin",
    "AudioPlayerMixin",
    "EraserMixin",
    "SpectrogramPanel",
]
