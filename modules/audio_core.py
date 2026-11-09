# -*- coding: utf-8 -*-
"""
Thin facade for modules.audio_core.
All implementations live in modules.dsp.{sound_loader, segmentation, pitch_formant_unified}.
"""

from .dsp.sound_loader import (
    SILENCE_AMPLITUDE_THRESHOLD,
    _get_mono_values,
    repair_wav_header,
    load_compatible_sound,
    trim_bounds_by_amplitude,
)
from .dsp.segmentation import (
    VOP_BUFFER_SEC,
    VOP_WIN_LEN_SEC,
    VOP_HOP_LEN_SEC,
    VAD_TIME_STEP,
    VAD_MIN_DURATION,
    VAD_CLEAR_GAP_THRESHOLD,
    detect_vowel_onset,
    _merge_vad_segments,
    _fit_vad_segments_to_expected_count,
    macroscopic_vad,
    core_microscopic_vowel_nucleus,
    recalculate_bounds_fast,
    check_audio_segments,
    auto_split_inner_word,
    auto_split_to_chars_bounds,
    find_minimum_intensity_valley,
    _normalize_locked_chars_bounds,
)
from .dsp.pitch_formant_unified import (
    _normalize_formant_params,
    _compute_segment_f0_coverage_warnings,
    extract_f0,
    extract_formants,
    _sample_preview_f0,
    _sample_formants_helper,
    _extract_item_pitch_and_formant_payload,
    batch_process_worker,
    long_process_worker,
    process_single_long_word,
    batch_process_worker_with_textgrid,
)
from .core.text_io import split_into_syllables

__all__ = [
    "SILENCE_AMPLITUDE_THRESHOLD",
    "_get_mono_values",
    "repair_wav_header",
    "load_compatible_sound",
    "trim_bounds_by_amplitude",
    "VOP_BUFFER_SEC",
    "VOP_WIN_LEN_SEC",
    "VOP_HOP_LEN_SEC",
    "VAD_TIME_STEP",
    "VAD_MIN_DURATION",
    "VAD_CLEAR_GAP_THRESHOLD",
    "detect_vowel_onset",
    "_merge_vad_segments",
    "_fit_vad_segments_to_expected_count",
    "macroscopic_vad",
    "core_microscopic_vowel_nucleus",
    "recalculate_bounds_fast",
    "check_audio_segments",
    "auto_split_inner_word",
    "auto_split_to_chars_bounds",
    "find_minimum_intensity_valley",
    "_normalize_locked_chars_bounds",
    "_normalize_formant_params",
    "_compute_segment_f0_coverage_warnings",
    "extract_f0",
    "extract_formants",
    "_sample_preview_f0",
    "_sample_formants_helper",
    "_extract_item_pitch_and_formant_payload",
    "batch_process_worker",
    "long_process_worker",
    "process_single_long_word",
    "batch_process_worker_with_textgrid",
    "split_into_syllables",
]
