# -*- coding: utf-8 -*-
"""
PhonTracer TextGrid 转换与层映射兼容门面模块

内部核心实现已拆分并迁移至 `modules.wordlist.textgrid_converter_core`。
"""
from __future__ import annotations

from .wordlist.textgrid_converter_core import (
    DEFAULT_GROUP_NAME,
    NONE_TIER_LABEL,
    PAIR_MODE_ADJACENT,
    PAIR_MODE_NONE,
    _EPS,
    _build_base_items,
    _build_char_bounds,
    _build_wordlist_index,
    _clean_label,
    _copy_item,
    _fill_char_tier,
    _fill_item_tier,
    _interval_tier_by_name,
    _interval_tuple,
    _join_pair_label,
    _match_wordlist_record,
    _normalize_mapping,
    _overlapping_by_center,
    _override_group,
    _pair_adjacent_items,
    _pair_items_by_wordlist,
    _read_text_file_with_fallback,
    _resolve_group,
    _validate_mapping,
    _wordlist_keys,
    ConversionPreview,
    ConvertedTextGridItem,
    TextGridMapping,
    build_standard_textgrid,
    convert_textgrid,
    diagnose_tone_pair_support,
    inspect_textgrid,
    load_auxiliary_wordlist_records,
    preview_textgrid_conversion,
    recommend_tier_names,
    split_tone_pair,
)

__all__ = [
    "DEFAULT_GROUP_NAME", "NONE_TIER_LABEL", "PAIR_MODE_NONE", "PAIR_MODE_ADJACENT",
    "TextGridMapping", "ConvertedTextGridItem", "ConversionPreview",
    "inspect_textgrid", "preview_textgrid_conversion", "convert_textgrid",
    "build_standard_textgrid", "diagnose_tone_pair_support", "split_tone_pair",
    "recommend_tier_names", "load_auxiliary_wordlist_records",
]
