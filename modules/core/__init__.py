# -*- coding: utf-8 -*-
"""
Layer 0: PhonTracer 基础设施与零依赖核心包。
包含统一路径寻址 (paths)、纯文本与音节处理 (text_io)、以及基础声学数学函数 (acoustic_math)。
严禁在本包内引入任何 GUI (tkinter/customtkinter) 或上层业务模块依赖。
"""
from modules.core.acoustic_math import (
    DEFAULT_F0_BOUNDS,
    _compute_2d_convex_hull_and_area,
    _compute_pillai_bartlett_trace,
    _safe_nanmax,
    _safe_nanmean,
    _safe_nanstd,
    _t_to_5deg_continuous,
    compute_speaker_f0_bounds,
    fit_chao_tone_code,
    hz_to_chao_t,
    hz_to_t_value,
)
from modules.core.paths import (
    get_asset_path,
    get_exe_dir,
    get_resource_root,
    is_frozen,
    resolve_sibling_command,
    resolve_suite_executable,
)
from modules.core.text_io import (
    _read_text_file_with_fallback,
    clean_str,
    fuzzy_match_word_to_path,
    has_cjk,
    make_textgrid_export_stem,
    parse_flat_wordlist,
    parse_wordlist,
    read_text_file_with_fallback,
    split_into_syllables,
)

__all__ = [
    "is_frozen",
    "get_resource_root",
    "get_exe_dir",
    "get_asset_path",
    "resolve_sibling_command",
    "resolve_suite_executable",
    "parse_wordlist",
    "parse_flat_wordlist",
    "has_cjk",
    "split_into_syllables",
    "make_textgrid_export_stem",
    "clean_str",
    "fuzzy_match_word_to_path",
    "read_text_file_with_fallback",
    "_read_text_file_with_fallback",
    "DEFAULT_F0_BOUNDS",
    "compute_speaker_f0_bounds",
    "hz_to_t_value",
    "hz_to_chao_t",
    "_t_to_5deg_continuous",
    "fit_chao_tone_code",
    "_safe_nanmean",
    "_safe_nanstd",
    "_safe_nanmax",
    "_compute_2d_convex_hull_and_area",
    "_compute_pillai_bartlett_trace",
]
