# -*- coding: utf-8 -*-
"""
PhonTracer语音学标注与声学分析系统 V1.0 自定义脚本 API 上下文定义（薄门面重导出）
"""

from .scripting.acoustic_operators import (
    _draw_formant_arrows,
    _extract_waveform_for_analysis,
    extract_spectral_moments,
    extract_voice_quality,
    normalize_formants,
    plot_spectrogram_grid,
    spectrogram_data,
)
from .scripting.context import ScriptContext, parselmouth
from .scripting.models import (
    FigureResult,
    ProjectPatchResult,
    TableResult,
    _build_zip_member_lookup,
    _coerce_time,
    _hz_to_bark_arr,
    _hz_to_erb_arr,
    _item_display_label,
    _normalize_audio_resource_path,
    configure_matplotlib_chinese_font,
    get_f0_normalization_bounds,
)
from .scripting.snapshot import (
    DatasetSnapshot,
    _GLOBAL_PROJECT_JSON_CACHE,
    _GLOBAL_SOUND_LRU_CACHE,
    _MAX_GLOBAL_SOUND_CACHE,
    _SOUND_LRU_MAX_ENTRIES,
    build_dataset_snapshot,
)

__all__ = [
    "DatasetSnapshot",
    "FigureResult",
    "ProjectPatchResult",
    "ScriptContext",
    "TableResult",
    "_GLOBAL_PROJECT_JSON_CACHE",
    "_GLOBAL_SOUND_LRU_CACHE",
    "_MAX_GLOBAL_SOUND_CACHE",
    "_SOUND_LRU_MAX_ENTRIES",
    "_build_zip_member_lookup",
    "_coerce_time",
    "_draw_formant_arrows",
    "_extract_waveform_for_analysis",
    "_hz_to_bark_arr",
    "_hz_to_erb_arr",
    "_item_display_label",
    "_normalize_audio_resource_path",
    "build_dataset_snapshot",
    "configure_matplotlib_chinese_font",
    "extract_spectral_moments",
    "extract_voice_quality",
    "get_f0_normalization_bounds",
    "normalize_formants",
    "parselmouth",
    "plot_spectrogram_grid",
    "spectrogram_data",
]
