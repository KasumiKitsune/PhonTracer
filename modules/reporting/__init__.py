# -*- coding: utf-8 -*-
"""
modules.reporting subpackage.
Refactored from modules/report_generator.py into modular reporting components:
- item_inspector: parameter inspection, cache recovery, anomaly detection, wav header parsing
- stats_aggregator: acoustic record extraction, bucket aggregation, ANOVA statistics
- markdown_builder: markdown research methodology report generation
- excel_archive_writer: Excel research archive and complete project export
"""

from .item_inspector import (
    SPLIT_WARNING_ZH,
    get_item_override_param,
    get_pitch_floor,
    get_pitch_ceiling,
    get_voicing_threshold,
    _majority_value,
    get_majority_item_params,
    calculate_sha256,
    load_item_cache_if_any,
    check_item_has_empty_f0,
    check_item_has_empty_formant,
    analyze_item_anomalies,
    format_speaker_name,
    parse_wav_header_from_bytes,
    extract_project_wav_params,
    get_consensus_and_deviations,
    format_wav_params,
    generate_natural_language_summary,
    detect_pitch_anomaly_points,
    validate_project_archive_members,
    split_into_syllables,
    re_cjk,
    hz_to_chao_t,
    _t_to_5deg_continuous,
    fit_chao_tone_code,
)
from .stats_aggregator import (
    _extract_item_acoustic_record,
    _round_or_none,
    _aggregate_bucket_records,
    compute_acoustic_statistics,
)
from .markdown_builder import (
    build_markdown_acoustic_summary_section,
    generate_markdown_report,
)
from .excel_archive_writer import (
    write_excel_archive,
    export_reports_from_teproj,
)

__all__ = [
    # Item Inspector
    "SPLIT_WARNING_ZH",
    "get_item_override_param",
    "get_pitch_floor",
    "get_pitch_ceiling",
    "get_voicing_threshold",
    "_majority_value",
    "get_majority_item_params",
    "calculate_sha256",
    "load_item_cache_if_any",
    "check_item_has_empty_f0",
    "check_item_has_empty_formant",
    "analyze_item_anomalies",
    "format_speaker_name",
    "parse_wav_header_from_bytes",
    "extract_project_wav_params",
    "get_consensus_and_deviations",
    "format_wav_params",
    "generate_natural_language_summary",
    # Stats Aggregator
    "_extract_item_acoustic_record",
    "_round_or_none",
    "_aggregate_bucket_records",
    "compute_acoustic_statistics",
    # Markdown Builder
    "build_markdown_acoustic_summary_section",
    "generate_markdown_report",
    # Excel Archive Writer
    "write_excel_archive",
    "export_reports_from_teproj",
    # Re-exported utility functions & types
    "detect_pitch_anomaly_points",
    "validate_project_archive_members",
    "split_into_syllables",
    "re_cjk",
    "hz_to_chao_t",
    "_t_to_5deg_continuous",
    "fit_chao_tone_code",
]
