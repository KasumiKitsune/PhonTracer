"""
modules/data_utils.py - 兼容门面层 (Facade)
该模块核心逻辑已拆分至 modules.export_service、modules.core.acoustic_math 与 modules.core.text_io。
本文件仅保留符号重导出，以保证 100% 向后兼容。
"""

from .core.acoustic_math import (
    DEFAULT_F0_BOUNDS,
    compute_speaker_f0_bounds,
    hz_to_t_value,
)
from .core.text_io import (
    _UUID_TOKEN_RE,
    clean_str,
    fuzzy_match_word_to_path,
    has_cjk,
    make_textgrid_export_stem,
    parse_wordlist,
    split_into_syllables,
)
from .export_service import (
    get_export_text_for_item,
    get_item_syllable_bounds,
    sample_formant_points_by_bounds_extended,
    sample_formant_points_by_bounds,
    get_formant_export_text_for_item,
    ExcelStyleManager,
    compute_visual_width,
    autofit_worksheet_columns,
    get_pitch_arrays_for_item,
    write_raw_pitch_sheet,
    write_analysis_sheet_with_formulas,
    write_formant_analysis_sheet_with_formulas,
    build_five_point_chart,
    get_export_textgrid_for_item,
    extract_wordlist_from_textgrid,
    make_safe_excel_sheet_name,
    ExportCallbacks,
    UnifiedExportService,
)

__all__ = [
    # acoustic_math
    "DEFAULT_F0_BOUNDS",
    "compute_speaker_f0_bounds",
    "hz_to_t_value",
    # text_io
    "_UUID_TOKEN_RE",
    "clean_str",
    "fuzzy_match_word_to_path",
    "has_cjk",
    "make_textgrid_export_stem",
    "parse_wordlist",
    "split_into_syllables",
    # export_service / item_sampling
    "get_export_text_for_item",
    "get_item_syllable_bounds",
    "sample_formant_points_by_bounds_extended",
    "sample_formant_points_by_bounds",
    "get_formant_export_text_for_item",
    # export_service / excel_sheet_builders
    "ExcelStyleManager",
    "compute_visual_width",
    "autofit_worksheet_columns",
    "get_pitch_arrays_for_item",
    "write_raw_pitch_sheet",
    "write_analysis_sheet_with_formulas",
    "write_formant_analysis_sheet_with_formulas",
    "build_five_point_chart",
    "make_safe_excel_sheet_name",
    # export_service / textgrid_io
    "get_export_textgrid_for_item",
    "extract_wordlist_from_textgrid",
    # export_service / unified_exporter
    "ExportCallbacks",
    "UnifiedExportService",
]
