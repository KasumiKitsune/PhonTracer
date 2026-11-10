# -*- coding: utf-8 -*-
"""
PhonTracer CLI - Acoustic Data and Media Export Mixin.
Provides commands for exporting tabular datasets, publication charts, TextGrids,
segmented audio, and integrated multi-speaker packages.
"""
from __future__ import annotations

import copy
import json
import logging
import math
import os
import re
import shlex
import shutil
import sys
import warnings
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

try:
    import parselmouth
except ImportError:
    from modules import _parselmouth_fallback as parselmouth
    sys.modules.setdefault("parselmouth", parselmouth)
    sys.modules.setdefault("parselmouth.praat", parselmouth.praat)
import textgrid

from modules.audio_core import (
    extract_f0,
    extract_formants,
    load_compatible_sound,
    trim_bounds_by_amplitude,
)
from modules.cli_app.io_utils import (
    COMMON_AUDIO_EXTENSIONS,
    AcousticChartCLIAdapter,
    _natural_sort_key,
    dumps_utf8,
)
from modules.data_utils import (
    build_five_point_chart,
    clean_str,
    get_export_text_for_item,
    get_item_syllable_bounds,
    make_textgrid_export_stem,
    sample_formant_points_by_bounds_extended,
    split_into_syllables,
)
from modules.report_generator import (
    calculate_sha256,
    compute_acoustic_statistics,
    export_reports_from_teproj,
)
from modules.version import APP_NAME, __version__
from modules.wordlist_v2 import (
    WORDLIST_SCHEMA,
    build_wordlist_document_from_items,
    save_wordlist_document,
    save_wordlist_file,
    summarize_wordlist_document,
)


class CLIExportMixin:
    """
    Export mixin handling publication chart generation, data matrix compilation,
    audio slicing, and comprehensive export bundles.
    """
    SUPPORTED_EXPORT_FORMATS = [
        "xlsx",
        "txt",
        "textgrid",
        "collection",
        "ptwl",
        "contour",
        "tone_sandhi",
        "tone_effect_time",
        "density",
        "distribution",
        "duration",
        "quality",
        "spaghetti_qc",
        "overview_heatmap",
        "formant_space",
        "f1_f2",
        "formant_trajectory",
        "formant_density",
        "formant_overview_heatmap",
        "formant_pillai",
        "formant_table",
        "trend",
        "line_chart",
        "kde",
        "wav",
        "merged_wav",
    ]


    def do_export(self, arg):
        """
        Export data to various formats.
        Usage: export <format> <output_file> [rule] [target] [key=val ...]
        Advanced charts: export <chart_type> <output_file_or_dir> [target] [key=val ...]
        Formats: xlsx, txt, textgrid, ptwl, contour, density, distribution, quality, overview_heatmap,
                 formant_space, f1_f2, duration, trend, line_chart, kde, wav, merged_wav,
                 formant_table, formant_trajectory, formant_density, formant_overview_heatmap
        Rule: continuous (default) or per_group (For 'wav' and 'merged_wav', rule can also be buffer_sec or gap_sec like 0.5)
        Target (Multi-speaker): active (default), separate (multiple files per speaker), integrated (merged T-value calculation)
        """
        try:
            args = shlex.split(arg)
        except ValueError as e:
            self._emit(False, error=f"Invalid arguments: {e}")
            return

        if len(args) == 1 and args[0].strip().lower() in ("?", "--help", "-h", "help"):
            self._emit_subcommand_help("export")
            return

        if len(args) < 2:
            self._emit(
                False,
                error="Requires format and output_file",
                supported_formats=self.SUPPORTED_EXPORT_FORMATS,
                hint="用法: export <格式> <输出路径> [continuous|per_group] [active|integrated|separate] [key=value ...]。输入 `export ?` 查看完整格式与参数列表。",
            )
            return

        fmt_raw = args[0].strip()
        fmt_low = fmt_raw.lower().lstrip(".")
        out_file = args[1]

        fmt_aliases = {
            "f1_f2": "formant_space",
            "f1f2": "formant_space",
            "vowel_space": "formant_space",
            "vowel_chart": "formant_space",
            "wordlist": "ptwl",
            "praat_collection": "collection",
            "praat": "collection",
            "tone_sandhi": "tone_sandhi",
            "sandhi_grid": "tone_sandhi",
            "formant_pillai": "formant_pillai",
            "pillai_matrix": "formant_pillai",
            "vowel_merger": "formant_pillai",
            "spaghetti_qc": "spaghetti_qc",
            "raincloud": "distribution",
        }
        valid_formats_set = {f.lower() for f in self.SUPPORTED_EXPORT_FORMATS} | set(fmt_aliases.keys())

        # 1. 格式合法性校验（严格优先于空数据检查！）
        if fmt_low not in valid_formats_set:
            self._emit(
                False,
                error=f"Unknown format: {fmt_raw}",
                supported_formats=self.SUPPORTED_EXPORT_FORMATS,
                hint=f"不支持的导出格式 '{fmt_raw}'。合法格式包括: {', '.join(self.SUPPORTED_EXPORT_FORMATS)}。输入 `export ?` 查看各格式参数说明。",
            )
            return

        empty_data_hint = "请先运行 load_long / load_batch + apply_wordlist，或通过 project_import / --project 载入已有工程"

        # 2. 高级字表 (.ptwl) 导出支持
        if fmt_low in ("ptwl", "wordlist"):
            custom_title = None
            for extra in args[2:]:
                if "=" in extra:
                    k, v = extra.split("=", 1)
                    if k.strip().lower() in ("title", "标题"):
                        custom_title = v.strip().strip('"').strip("'")

            spk = self.current_speaker
            base_doc = self.wordlist_doc
            target_items = spk.items if spk and spk.items else {}
            target_groups = list(self.groups) if self.groups else []

            if not target_items and base_doc is None:
                for other_spk in self.speaker_manager.get_all_speakers():
                    if other_spk.items or getattr(other_spk, "wordlist_doc", None) is not None:
                        target_items = other_spk.items
                        base_doc = getattr(other_spk, "wordlist_doc", None)
                        target_groups = getattr(other_spk, "cli_groups", []) or getattr(other_spk, "project_groups", []) or []
                        break

            has_base_items = bool(
                isinstance(base_doc, dict)
                and any(g.get("items") for g in base_doc.get("groups", []) if isinstance(g, dict))
            )
            if not target_items and not has_base_items:
                self._emit(
                    False,
                    error="No items to export",
                    hint=empty_data_hint,
                )
                return

            try:
                doc = build_wordlist_document_from_items(
                    target_items,
                    groups_order=target_groups,
                    base_doc=base_doc,
                    title=custom_title,
                )
                normalized = save_wordlist_file(doc, out_file)
                self.wordlist_doc = normalized
                summary = summarize_wordlist_document(normalized)
                self._emit(
                    True,
                    f"Exported ptwl to {out_file}",
                    path=out_file,
                    output_file=out_file,
                    schema=normalized.get("schema", WORDLIST_SCHEMA),
                    title=normalized.get("title", ""),
                    groups=summary["groups"],
                    group_count=summary["groups"],
                    item_count=summary["items"],
                )
            except Exception as e:
                self._emit(False, error=str(e))
            return

        # 3. 归一化高级科研图表别名
        chart_preset_overrides = {}
        if fmt_low in fmt_aliases:
            fmt = fmt_aliases[fmt_low]
            if fmt_low == "f1_f2":
                chart_preset_overrides["formant_show_raw"] = True
                chart_preset_overrides["formant_ellipse"] = "不显示"
                chart_preset_overrides["_f1_f2_scatter_mode"] = True
        elif fmt_low == "line_chart":
            fmt = "contour"
            chart_preset_overrides["contour_content"] = "average"
        elif fmt_low == "trend":
            fmt = "tone_effect_time"
        else:
            fmt = fmt_low

        # 4. 高级科研图表工具箱集成
        scientific_charts = {
            "contour",
            "distribution",
            "duration",
            "density",
            "quality",
            "overview_heatmap",
            "tone_effect_time",
            "tone_sandhi",
            "spaghetti_qc",
            "formant_space",
            "formant_trajectory",
            "formant_density",
            "formant_overview_heatmap",
            "formant_pillai",
        }
        if fmt in scientific_charts:
            params = {
                "chart_type": fmt,
                "export_scope": "active",
                **chart_preset_overrides,
            }

            def parse_chart_group_rule(value):
                text = str(value or "").strip()
                lowered = text.lower()
                if lowered in ("default", "group", "原始组", "默认组", "按字表组别", "按组别"):
                    return {"source": "default", "tag_mode": "each", "selected_values": [], "field_name": ""}
                if lowered in ("review", "metadata_source", "复核状态", "按复核状态"):
                    return {"source": "metadata_source", "tag_mode": "each", "selected_values": [], "field_name": ""}
                if lowered in ("label", "word", "词项", "词语", "按词语", "按词项"):
                    return {"source": "label", "tag_mode": "each", "selected_values": [], "field_name": ""}
                if lowered in ("speaker", "speaker_name", "发音人", "按发音人"):
                    return {"source": "speaker_name", "tag_mode": "each", "selected_values": [], "field_name": ""}
                if lowered in ("syl_char", "char", "syllable", "按单字音节", "按单字/音节"):
                    return {"source": "default", "tag_mode": "each", "selected_values": [], "field_name": ""}

                if ":" in text:
                    prefix, rest = text.split(":", 1)
                else:
                    prefix, rest = text, ""
                prefix = prefix.strip().lower().replace("-", "_")
                rest = rest.strip()
                values = [v.strip() for v in rest.replace("；", ",").replace("、", ",").split(",") if v.strip()]

                source_aliases = {
                    "item_tag": "item_tags",
                    "item_tags": "item_tags",
                    "tag": "item_tags",
                    "tags": "item_tags",
                    "词项标签": "item_tags",
                    "标签": "item_tags",
                    "group_tag": "group_tags",
                    "group_tags": "group_tags",
                    "组标签": "group_tags",
                    "meta": "item_meta",
                    "field": "item_meta",
                    "item_meta": "item_meta",
                    "custom": "item_meta",
                    "按自定义字段": "item_meta",
                    "自定义字段": "item_meta",
                    "字段": "item_meta",
                    "contains_item_tag": "item_tags",
                    "contains_group_tag": "group_tags",
                    "filter_item_tag": "item_tags",
                    "filter_group_tag": "group_tags",
                }
                source = source_aliases.get(prefix)
                if not source:
                    raise ValueError(f"Unsupported chart_group rule: {text}")

                tag_mode = "each"
                if prefix.startswith("contains_"):
                    tag_mode = "contains"
                elif prefix.startswith("filter_"):
                    tag_mode = "filter_default"

                if source == "item_meta":
                    field_name = rest or (values[0] if values else "")
                    return {"source": "item_meta", "tag_mode": "each", "selected_values": [], "field_name": field_name}

                return {"source": source, "tag_mode": tag_mode, "selected_values": values, "field_name": ""}

            formant_norm_aliases = {
                "hz": "原始频率 (Hz)",
                "raw": "原始频率 (Hz)",
                "原始频率": "原始频率 (Hz)",
                "原始频率 (hz)": "原始频率 (Hz)",
                "lobanov": "Lobanov (发音人 Z-score)",
                "lobanov z": "Lobanov (发音人 Z-score)",
                "lobanov z-score": "Lobanov (发音人 Z-score)",
                "l-归一化": "Lobanov (发音人 Z-score)",
                "z-score": "Lobanov (发音人 Z-score)",
                "zscore": "Lobanov (发音人 Z-score)",
                "z": "Lobanov (发音人 Z-score)",
                "bark": "Bark (听觉巴克)",
                "erb": "ERB (等效矩形带宽)",
                "mel": "Mel (美尔)",
            }

            pos_args = []
            explicit_chart_group_rule = False
            for item in args[2:]:
                if "=" in item:
                    k, v = item.split("=", 1)
                    k = k.strip().lower()
                    v = v.strip()
                    if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
                        v = v[1:-1]
                    if k in ("target", "scope", "export_scope"):
                        params["export_scope"] = v
                        continue
                    elif k == "facet":
                        params["facet_by"] = v
                        if fmt == "density":
                            params["density_facet"] = v
                        elif fmt == "contour":
                            params["contour_facet"] = v
                        elif fmt in ("formant_space", "formant_density"):
                            params["formant_density_facet"] = v
                        continue
                    elif k in ("chart_group", "chart_group_rule", "group_rule"):
                        try:
                            rule_obj = parse_chart_group_rule(v)
                        except ValueError as exc:
                            self._emit(False, error=str(exc))
                            return
                        params["chart_group_rule"] = rule_obj
                        explicit_chart_group_rule = True
                        if "groupby" not in params:
                            if rule_obj["source"] == "label":
                                params["groupby"] = "label"
                            elif rule_obj["source"] == "speaker_name":
                                params["groupby"] = "speaker"
                            elif rule_obj["source"] == "default":
                                params["groupby"] = "group"
                        continue
                    elif k in ("groupby", "color_by"):
                        params["groupby"] = v
                        params["color_by"] = v
                        if not explicit_chart_group_rule:
                            try:
                                params["chart_group_rule"] = parse_chart_group_rule(v)
                            except ValueError as exc:
                                self._emit(False, error=str(exc))
                                return
                        continue
                    elif k in ("normalization", "norm", "formant_norm", "formant_normalization"):
                        v_low = v.strip().lower()
                        if k in ("formant_norm", "formant_normalization"):
                            params["formant_normalization"] = formant_norm_aliases.get(v_low, v)
                        else:
                            params["normalization"] = v
                            if v_low in formant_norm_aliases:
                                params["formant_normalization"] = formant_norm_aliases[v_low]
                            elif fmt.startswith("formant_") and v_low in ("speaker", "发音人", "per_speaker"):
                                params["formant_normalization"] = "Lobanov (发音人 Z-score)"
                            elif any(tok in v_low for tok in ("lobanov", "bark", "erb", "mel")):
                                params["formant_normalization"] = v
                        continue
                    params[k] = v
                else:
                    pos_args.append(item)

            for pos_arg in pos_args:
                scope_candidate = pos_arg.lower()
                if scope_candidate in ("active", "separate", "integrated"):
                    params["export_scope"] = scope_candidate
                    break

            scope = params.get("export_scope", "active")

            bool_params = {
                "sort_by_anomaly", "show_chao_five_level_axis", "show_spaghetti", "show_kde_bg",
                "show_convex_hull", "show_diphthong_arrows", "show_f3", "overview_show_deviation",
                "show_title", "legend_outside", "formant_axis_lock", "praat_axes",
            }
            for bp in bool_params:
                if bp in params and isinstance(params[bp], str):
                    params[bp] = params[bp].strip().lower() in ("true", "1", "yes", "on", "是")
            float_params = {"density_bw", "density_p_low", "density_p_high", "density_m_min", "density_m_max"}
            for fp in float_params:
                if fp in params:
                    try:
                        params[fp] = float(params[fp])
                    except ValueError:
                        pass

            if scope == "separate":
                ext = "." + params.get("format", "png").lower()
            else:
                _, ext = os.path.splitext(out_file)
                if not ext:
                    ext_param = params.get("format", "png").lower()
                    ext = f".{ext_param}"
                    out_file = out_file + ext
                else:
                    params["format"] = ext[1:]

            from modules.acoustic_exporter import AcousticChartExporter
            adapter = AcousticChartCLIAdapter(self)
            all_speakers = self.speaker_manager.get_all_speakers()
            exporter = AcousticChartExporter(project_tree=adapter, app=self, all_speakers=all_speakers)
            exporter.params = params

            speakers_to_process = [self.current_speaker] if scope == "active" else all_speakers
            if not any(len(s.items) > 0 for s in speakers_to_process):
                self._emit(False, error="No items to export", hint=empty_data_hint)
                return

            old_mode = self.params.get("analysis_mode")
            old_spk_modes = {s.id: s.last_params.get("analysis_mode") for s in all_speakers}
            try:
                if fmt.startswith("formant_"):
                    self.params["analysis_mode"] = "formant"
                    params["analysis_mode"] = "formant"
                    for s in speakers_to_process:
                        s.last_params["analysis_mode"] = "formant"
                        for it in s.items.values():
                            if not it.get("is_excluded", False):
                                self._ensure_item_loaded(it, speaker=s)
                elif fmt == "duration":
                    for s in speakers_to_process:
                        for it in s.items.values():
                            if not it.get("is_excluded", False):
                                self._ensure_item_loaded(it, speaker=s)

                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", category=RuntimeWarning)
                    if scope == "separate":
                        os.makedirs(out_file, exist_ok=True)
                        for speaker in all_speakers:
                            data = exporter._extract_active_data([speaker])
                            if not data and fmt == "duration":
                                data = exporter._extract_duration_only_entries([speaker])
                            if data:
                                out_path = os.path.join(out_file, f"{speaker.name}_{fmt}{ext}")
                                exporter._export_dataset(data, out_path, ext)
                        self._emit(True, f"Exported {fmt} for multiple speakers separately to {out_file}", path=out_file, format=fmt)
                    else:
                        os.makedirs(os.path.dirname(os.path.abspath(out_file)) or ".", exist_ok=True)
                        data = exporter._get_current_data_entries()
                        if not data and fmt == "duration":
                            data = exporter._extract_duration_only_entries(speakers_to_process)
                        if not data and fmt == "formant_space":
                            ok = self._export_vowel_space_chart(out_file, speakers_to_process, emit_error=False)
                            if ok:
                                extra_payload = {"path": out_file, "format": fmt}
                                if scope == "integrated":
                                    extra_payload["integrated_summary"] = self._build_integrated_summary(speakers_to_process)
                                self._emit(True, f"Exported {fmt} ({scope}) to {out_file}", **extra_payload)
                                return
                        if not data:
                            self._emit(False, error="No valid data found for export", hint=empty_data_hint)
                            return
                        exporter._export_dataset(data, out_file, ext)
                        extra_payload = {"path": out_file, "format": fmt}
                        if scope == "integrated":
                            extra_payload["integrated_summary"] = self._build_integrated_summary(speakers_to_process)
                        self._emit(True, f"Exported {fmt} ({scope}) to {out_file}", **extra_payload)
            except Exception as e:
                self._emit(False, error=str(e))
            finally:
                if old_mode is not None:
                    self.params["analysis_mode"] = old_mode
                elif "analysis_mode" in self.params:
                    del self.params["analysis_mode"]
                for s in all_speakers:
                    prev_m = old_spk_modes.get(s.id)
                    if prev_m is not None:
                        s.last_params["analysis_mode"] = prev_m
                    elif "analysis_mode" in s.last_params:
                        del s.last_params["analysis_mode"]
            return

        # 5. 常规数据表与音频/TextGrid/Collection 导出
        rule = "continuous"
        target = "active"
        use_binary = True
        pos_rest = []
        for token in args[2:]:
            if "=" in token:
                k, v = token.split("=", 1)
                k_low = k.strip().lower()
                v_clean = v.strip().strip('"').strip("'").lower()
                if k_low in ("target", "scope", "export_scope"):
                    target = v_clean
                elif k_low in ("rule", "num_rule"):
                    rule = v.strip().strip('"').strip("'")
                elif k_low == "binary":
                    use_binary = v_clean not in ("false", "0", "no", "off", "text")
            else:
                pos_rest.append(token)

        for tok in pos_rest:
            if tok.lower() in ("active", "separate", "integrated"):
                target = tok.lower()
            elif tok.lower() == "text" and fmt == "collection":
                use_binary = False
            elif tok.lower() == "binary" and fmt == "collection":
                use_binary = True
            else:
                rule = tok

        speakers_to_process = [self.current_speaker] if target == "active" else self.speaker_manager.get_all_speakers()

        if not any(len(s.items) > 0 for s in speakers_to_process):
            self._emit(False, error="No items to export", hint=empty_data_hint)
            return

        try:
            if fmt == "formant_table":
                self._export_formant_table(out_file, speakers_to_process)
                extra_payload = {"path": out_file, "format": fmt}
                if target == "integrated":
                    extra_payload["integrated_summary"] = self._build_integrated_summary(speakers_to_process)
                self._emit(True, f"Exported formant_table to {out_file}", **extra_payload)
                return

            if target == "separate":
                os.makedirs(out_file, exist_ok=True)
                for s in speakers_to_process:
                    s_struct = [
                        (grp, [iid for iid, item in s.items.items() if item.get("group") == grp and not item.get("is_excluded", False)])
                        for grp in getattr(s, "cli_groups", [])
                    ]
                    s_out = os.path.join(out_file, f"{s.name}_{fmt}")

                    if fmt == "txt":
                        self._export_txt(f"{s_out}.txt", s_struct, rule, s)
                    elif fmt == "xlsx":
                        self._export_xlsx(f"{s_out}.xlsx", s_struct, rule, s)
                    elif fmt == "line_chart":
                        self._export_line_chart(f"{s_out}.png", s_struct, s)
                    elif fmt == "kde":
                        self._export_kde_heatmap(f"{s_out}.png", s_struct, s)
                    elif fmt == "wav":
                        self._export_wav(s_out, s_struct, rule, s)
                    elif fmt == "merged_wav":
                        self._export_merged_wav(f"{s_out}.wav", s_struct, rule, s)
                    elif fmt == "textgrid":
                        if s.tab_mode == "多条独立音频":
                            self._export_textgrid(s_out, s_struct, s)
                        else:
                            self._export_textgrid(f"{s_out}.TextGrid", s_struct, s)
                    elif fmt == "collection":
                        self._export_collection(os.path.join(out_file, f"{s.name}.Collection"), s_struct, s, binary=use_binary)
                    else:
                        self._emit(False, error=f"Unknown format: {fmt}", supported_formats=self.SUPPORTED_EXPORT_FORMATS)
                        return
                self._emit(True, f"Exported {fmt} for multiple speakers to {out_file}", path=out_file, format=fmt)

            elif target == "integrated":
                if fmt not in ("txt", "xlsx", "line_chart", "kde", "collection"):
                    self._emit(
                        False,
                        error=f"Format {fmt} does not support integrated multi-speaker export",
                        supported_formats=self.SUPPORTED_EXPORT_FORMATS,
                    )
                    return
                if fmt == "txt":
                    self._export_txt_integrated(out_file, speakers_to_process, rule)
                elif fmt == "xlsx":
                    self._export_xlsx_integrated(out_file, speakers_to_process, rule)
                elif fmt == "line_chart":
                    self._export_line_chart_integrated(out_file, speakers_to_process)
                elif fmt == "kde":
                    self._export_kde_heatmap_integrated(out_file, speakers_to_process)
                elif fmt == "collection":
                    self._export_collection_integrated(out_file, speakers_to_process, binary=use_binary)
                integrated_summary = self._build_integrated_summary(speakers_to_process)
                self._emit(
                    True,
                    f"Exported integrated {fmt} to {out_file}",
                    path=out_file,
                    format=fmt,
                    integrated_summary=integrated_summary,
                )

            else:  # active
                s = self.current_speaker
                structure = [
                    (grp, [iid for iid, item in s.items.items() if item.get("group") == grp and not item.get("is_excluded", False)])
                    for grp in self.groups
                ]
                if fmt == "txt":
                    self._export_txt(out_file, structure, rule, s)
                elif fmt == "xlsx":
                    self._export_xlsx(out_file, structure, rule, s)
                elif fmt == "line_chart":
                    self._export_line_chart(out_file, structure, s)
                elif fmt == "kde":
                    self._export_kde_heatmap(out_file, structure, s)
                elif fmt == "wav":
                    self._export_wav(out_file, structure, rule, s)
                elif fmt == "merged_wav":
                    self._export_merged_wav(out_file, structure, rule, s)
                elif fmt == "textgrid":
                    self._export_textgrid(out_file, structure, s)
                elif fmt == "collection":
                    self._export_collection(out_file, structure, s, binary=use_binary)
                else:
                    self._emit(False, error=f"Unknown format: {fmt}", supported_formats=self.SUPPORTED_EXPORT_FORMATS)
                    return
                self._emit(True, f"Exported to {out_file}", path=out_file, format=fmt)
        except Exception as e:
            self._emit(False, error=str(e))


    def do_export_bundle(self, arg):
        """
        One-stop research artifact bundle export.
        Usage: export_bundle <out_dir> [scope=active|integrated|separate] [format=png|svg] [title=<project_title>]
        Generates under <out_dir>:
          - <project_title>.teproj
          - <project_title>.ptwl
          - acoustic_data.xlsx & acoustic_points.txt
          - acoustic_stats.json
          - charts/ (contour, distribution, quality, overview_heatmap, and formant_space if formant data exists)
          - report/ (研究方法报告.md, 研究档案.xlsx, provenance_meta.json)
        """
        try:
            args = shlex.split(arg)
        except ValueError as e:
            self._emit(False, error=f"Argument parse error: {e}")
            return

        if not args or (len(args) == 1 and args[0].strip().lower() in ("?", "--help", "-h", "help")):
            self._emit(
                 len(args) == 1,
                error=None if len(args) == 1 else "Usage: export_bundle <out_dir> [scope=active|integrated|separate] [format=png|svg]",
                usage="export_bundle <out_dir> [scope=active|integrated|separate] [format=png|svg]",
            )
            return

        out_dir = args[0]
        scope = None
        img_fmt = "png"
        custom_title = None

        for token in args[1:]:
            if "=" in token:
                k, v = token.split("=", 1)
                k = k.strip().lower()
                v = v.strip()
                if k in ("scope", "target", "export_scope"):
                    scope = v.lower()
                elif k in ("format", "fmt", "img_format"):
                    img_fmt = v.lower().lstrip(".")
                elif k in ("title", "project_title", "name"):
                    custom_title = v
                else:
                    self._emit(
                        False,
                        error=f"Unknown parameter for export_bundle: {k}. Supported: scope=active|integrated|separate, format=png|svg, title=<name>",
                    )
                    return
            else:
                tl = token.strip().lower().lstrip(".")
                if tl in ("active", "integrated", "separate"):
                    scope = tl
                elif tl in ("png", "svg", "pdf"):
                    img_fmt = tl

        all_speakers = self.speaker_manager.get_all_speakers()
        speakers_with_items = [s for s in all_speakers if len(s.items) > 0]
        if not speakers_with_items:
            self._emit(False, error="No items to export. Please load audio and apply wordlist/TextGrid or import a project first.")
            return

        if scope is None:
            scope = "integrated" if len(speakers_with_items) > 1 else "active"
        if scope not in ("active", "integrated", "separate"):
            self._emit(False, error=f"Invalid scope: {scope}. Must be active, integrated, or separate")
            return
        if img_fmt not in ("png", "svg", "pdf"):
            self._emit(False, error=f"Invalid format: {img_fmt}. Must be png or svg")
            return

        abs_out_dir = os.path.abspath(out_dir)
        charts_dir = os.path.join(abs_out_dir, "charts")
        report_dir = os.path.join(abs_out_dir, "report")
        os.makedirs(abs_out_dir, exist_ok=True)
        os.makedirs(charts_dir, exist_ok=True)
        os.makedirs(report_dir, exist_ok=True)

        # Ensure all items across speakers are loaded
        orig_active_id = self.speaker_manager.active_speaker_id
        try:
            for spk in all_speakers:
                self.speaker_manager.set_active_speaker(spk.id)
                if not getattr(spk, "cli_groups", None):
                    seen_g = []
                    for it in spk.items.values():
                        g = it.get("group", "默认组")
                        if g not in seen_g:
                            seen_g.append(g)
                    spk.cli_groups = seen_g
                for item in spk.items.values():
                    if not item.get("is_excluded", False):
                        self._ensure_item_loaded(item, speaker=spk)
        finally:
            if orig_active_id:
                self.speaker_manager.set_active_speaker(orig_active_id)

        # Resolve project title (if --project points into abs_out_dir and no custom_title is forced, reuse bound .teproj name to prevent duplicate .teproj archives)
        bound_in_out_dir = False
        if self._bound_project_path:
            abs_bound = os.path.abspath(self._bound_project_path)
            if os.path.dirname(abs_bound) == abs_out_dir and abs_bound.lower().endswith(".teproj"):
                bound_in_out_dir = True

        wl_doc = self._build_wordlist_v2_doc_from_project(title=custom_title)
        if bound_in_out_dir and not custom_title:
            safe_title = os.path.splitext(os.path.basename(self._bound_project_path))[0]
        else:
            raw_title = custom_title or wl_doc.get("title") or ""
            if not raw_title or raw_title in ("未命名字表", "PhonTracer工程字表", "从普通字表导入"):
                if self._bound_project_path:
                    raw_title = os.path.splitext(os.path.basename(self._bound_project_path))[0]
                else:
                    dir_base = os.path.basename(abs_out_dir.rstrip("/\\"))
                    raw_title = dir_base if dir_base and dir_base not in (".", "output", "out", "bundle") else "phontracer_project"
            safe_title = re.sub(r'[\\/*?:"<>|\s]+', "_", str(raw_title)).strip("._") or "phontracer_project"

        bundle_files = []
        errors = []

        # 1. Export <project_title>.teproj
        teproj_path = os.path.abspath(self._bound_project_path) if (bound_in_out_dir and not custom_title) else os.path.join(abs_out_dir, f"{safe_title}.teproj")
        project_data = self._build_project_data_dict()
        try:
            ok = self.project_manager.export_project(teproj_path)
            if not ok or not os.path.exists(teproj_path):
                self._export_fallback_teproj_archive(teproj_path, project_data)
            if os.path.exists(teproj_path):
                bundle_files.append(teproj_path)
                if self._bound_project_path:
                    abs_bound = os.path.abspath(self._bound_project_path)
                    if abs_bound == os.path.abspath(teproj_path):
                        self._session_dirty = False
                    else:
                        import shutil as _shutil
                        os.makedirs(os.path.dirname(abs_bound) or ".", exist_ok=True)
                        _shutil.copy2(teproj_path, abs_bound)
                        self._session_dirty = False
        except Exception:
            try:
                self._export_fallback_teproj_archive(teproj_path, project_data)
                if os.path.exists(teproj_path):
                    bundle_files.append(teproj_path)
                    if self._bound_project_path and os.path.abspath(self._bound_project_path) == os.path.abspath(teproj_path):
                        self._session_dirty = False
            except Exception as e2:
                errors.append(f"teproj export failed: {e2}")

        # 2. Export <project_title>.ptwl
        ptwl_path = os.path.join(abs_out_dir, f"{safe_title}.ptwl")
        try:
            from modules.wordlist_v2 import save_wordlist_document
            save_wordlist_document(wl_doc, ptwl_path)
            if os.path.exists(ptwl_path):
                bundle_files.append(ptwl_path)
        except Exception as e:
            errors.append(f"ptwl export failed: {e}")

        # 3. Export acoustic_data.xlsx & acoustic_points.txt
        xlsx_path = os.path.join(abs_out_dir, "acoustic_data.xlsx")
        txt_path = os.path.join(abs_out_dir, "acoustic_points.txt")
        target_spks = [self.current_speaker] if scope == "active" else speakers_with_items
        try:
            if scope in ("integrated", "separate") and len(speakers_with_items) > 1:
                self._export_xlsx_integrated(xlsx_path, speakers_with_items, "continuous")
                self._export_txt_integrated(txt_path, speakers_with_items, "continuous")
            else:
                spk_for_table = self.current_speaker if len(self.current_speaker.items) > 0 else speakers_with_items[0]
                g_list = getattr(spk_for_table, "cli_groups", None) or list(dict.fromkeys(it.get("group", "默认组") for it in spk_for_table.items.values()))
                struct = [
                    (grp, [iid for iid, it in spk_for_table.items.items() if it.get("group", "默认组") == grp and not it.get("is_excluded", False)])
                    for grp in g_list
                ]
                self._export_xlsx(xlsx_path, struct, "continuous", spk_for_table)
                self._export_txt(txt_path, struct, "continuous", spk_for_table)
            if os.path.exists(xlsx_path):
                bundle_files.append(xlsx_path)
            if os.path.exists(txt_path):
                bundle_files.append(txt_path)
        except Exception as e:
            errors.append(f"xlsx/txt export failed: {e}")

        # 4. Export acoustic_stats.json
        stats_path = os.path.join(abs_out_dir, "acoustic_stats.json")
        try:
            from modules.report_generator import compute_acoustic_statistics
            stats_scope = "active" if scope == "active" else "integrated"
            stats_group = compute_acoustic_statistics(
                project_data, zip_ref=None, by="group", scope=stats_scope, active_speaker_id=self.speaker_manager.active_speaker_id
            )
            stats_item = compute_acoustic_statistics(
                project_data, zip_ref=None, by="item", scope=stats_scope, active_speaker_id=self.speaker_manager.active_speaker_id
            )
            stats_speaker = compute_acoustic_statistics(
                project_data, zip_ref=None, by="speaker", scope=stats_scope, active_speaker_id=self.speaker_manager.active_speaker_id
            )
            combined_stats = dict(stats_group)
            combined_stats["by_group"] = stats_group.get("summary", [])
            combined_stats["by_item"] = stats_item.get("summary", [])
            combined_stats["by_speaker"] = stats_speaker.get("summary", [])
            with open(stats_path, "w", encoding="utf-8") as f:
                json.dump(combined_stats, f, ensure_ascii=False, indent=2)
            if os.path.exists(stats_path):
                bundle_files.append(stats_path)
        except Exception as e:
            errors.append(f"stats export failed: {e}")

        # 5. Export charts/ (contour, distribution, quality, overview_heatmap, + formant_space if formant data exists)
        has_any_formant = (
            self.params.get("analysis_mode") == "formant"
            or any(
                it.get("formant_data") is not None or it.get("preview_formants") is not None or it.get("analysis_mode") == "formant"
                for s in target_spks
                for it in s.items.values()
                if not it.get("is_excluded", False)
            )
        )
        chart_types = ["contour", "distribution", "density", "quality", "overview_heatmap"]
        if has_any_formant:
            chart_types.append("formant_space")

        from modules.acoustic_exporter import AcousticChartExporter
        adapter = AcousticChartCLIAdapter(self)
        ext = f".{img_fmt}"
        chart_scope = "integrated" if (scope != "active" and len(speakers_with_items) > 1) else "active"
        for ctype in chart_types:
            c_out = os.path.join(charts_dir, f"{ctype}{ext}")
            try:
                exporter = AcousticChartExporter(project_tree=adapter, app=self, all_speakers=all_speakers)
                c_params = {
                    "chart_type": ctype,
                    "export_scope": chart_scope,
                    "format": img_fmt,
                    "scale": "t_value" if chart_scope == "integrated" else "t_value",
                }
                exporter.params = c_params
                old_m = self.params.get("analysis_mode")
                try:
                    if ctype.startswith("formant_"):
                        self.params["analysis_mode"] = "formant"
                        c_params["analysis_mode"] = "formant"
                    else:
                        self.params["analysis_mode"] = "f0"
                    data = exporter._get_current_data_entries()
                    if data:
                        exporter._export_dataset(data, c_out, ext)
                    elif ctype == "formant_space":
                        self._export_vowel_space_chart(c_out, target_spks, emit_error=False)
                finally:
                    if old_m is not None:
                        self.params["analysis_mode"] = old_m
                if os.path.exists(c_out):
                    bundle_files.append(c_out)
                for p_fn in sorted(os.listdir(charts_dir)):
                    if p_fn.startswith(f"{ctype}_第") and p_fn.endswith(ext):
                        p_full = os.path.join(charts_dir, p_fn)
                        if p_full not in bundle_files:
                            bundle_files.append(p_full)
                if scope == "separate" and len(speakers_with_items) > 1:
                    for spk in speakers_with_items:
                        sep_out = os.path.join(charts_dir, f"{spk.name}_{ctype}{ext}")
                        sep_data = exporter._extract_active_data([spk])
                        if sep_data:
                            exporter._export_dataset(sep_data, sep_out, ext)
                            if os.path.exists(sep_out):
                                bundle_files.append(sep_out)
            except Exception as e:
                errors.append(f"chart {ctype} export failed: {e}")

        # 6. Export report/ (研究方法报告.md, 研究档案.xlsx, provenance_meta.json)
        try:
            from modules.report_generator import export_reports_from_teproj, calculate_sha256
            import shutil
            import time
            if os.path.exists(teproj_path):
                rep_files, _ = export_reports_from_teproj(
                    teproj_path,
                    report_dir,
                    export_markdown=True,
                    export_excel=True,
                    include_cache_details=False,
                )
                for rf in rep_files:
                    if os.path.exists(rf):
                        bundle_files.append(rf)
                        if rf.endswith("_研究方法报告.md"):
                            canonical_md = os.path.join(report_dir, "研究方法报告.md")
                            if os.path.abspath(rf) != os.path.abspath(canonical_md):
                                shutil.copy2(rf, canonical_md)
                                bundle_files.append(canonical_md)
                prov_path = os.path.join(report_dir, "provenance_meta.json")
                prov_data = {
                    "project_title": safe_title,
                    "teproj_file": os.path.basename(teproj_path),
                    "teproj_sha256": calculate_sha256(teproj_path),
                    "software_version": __version__,
                    "exported_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "scope": scope,
                    "chart_format": img_fmt,
                    "speaker_count": len(speakers_with_items),
                    "bundle_files": [os.path.relpath(p, abs_out_dir) for p in bundle_files],
                }
                with open(prov_path, "w", encoding="utf-8") as f:
                    json.dump(prov_data, f, ensure_ascii=False, indent=2)
                bundle_files.append(prov_path)
        except Exception as e:
            errors.append(f"report export failed: {e}")

        self._emit(
            True,
            f"科研产物包已一站式导出至 {abs_out_dir}（共 {len(bundle_files)} 个文件）。",
            out_dir=abs_out_dir,
            project_title=safe_title,
            scope=scope,
            format=img_fmt,
            bundle_files=bundle_files,
            warnings=errors,
        )


    def _export_merged_wav(self, out_file, structure, rule, speaker=None):
        speaker = speaker or self.current_speaker
        import numpy as np

        if ('batch' if '多条' in speaker.tab_mode else 'long') != 'batch':
            raise Exception("merged_wav export is only supported for batch audio mode")

        gap_sec = 0.5
        try:
            if rule and rule not in ('continuous', 'per_group'):
                gap_sec = float(rule)
        except ValueError:
            pass

        target_sr = 44100
        all_vals = []
        gap_samples = int(target_sr * gap_sec)
        gap_array = np.zeros(gap_samples)

        for grp_name, children in structure:
            for child in children:
                item = speaker.items[child]
                if item.get('missing') or not item.get('success', True) or not item.get('path'):
                    continue

                try:
                    snd = parselmouth.Sound(item['path'])
                    if snd.sampling_frequency != target_sr:
                        snd = snd.resample(target_sr)
                    all_vals.append(snd.values[0])
                    all_vals.append(gap_array)
                except Exception:
                    continue

        if not all_vals:
            raise Exception("No valid audio files found to merge")

        merged_vals = np.concatenate(all_vals[:-1])
        merged_snd = parselmouth.Sound(np.array([merged_vals]), sampling_frequency=target_sr)
        merged_snd.save(out_file, "WAV")


    def _export_wav(self, out_dir, structure, rule, speaker=None):
        speaker = speaker or self.current_speaker
        import re
        if ('batch' if '多条' in speaker.tab_mode else 'long') != 'long' or not speaker.pending_long_snd:
            raise Exception("WAV export is only supported for long audio mode")

        if not os.path.exists(out_dir):
            os.makedirs(out_dir, exist_ok=True)

        buffer_sec = 0.1
        try:
            if rule and rule not in ('continuous', 'per_group'):
                buffer_sec = float(rule)
        except ValueError:
            pass

        snd = speaker.pending_long_snd
        do_trim = speaker.last_params.get('trim_silence', True)

        global_idx = 1
        for grp_name, children in structure:
            for child in children:
                item = speaker.items[child]
                if item.get('missing') or not item.get('success', True): continue
                if 'macro_start' not in item or 'macro_end' not in item: continue

                s, e = item['macro_start'], item['macro_end']
                word = item['label']

                if do_trim:
                    part = snd.extract_part(from_time=s, to_time=e)
                    vals = part.values[0]
                    xs = part.xs()
                    s, e = trim_bounds_by_amplitude(s, e, xs, vals)

                s = max(0, s - buffer_sec)
                e = min(snd.get_total_duration(), e + buffer_sec)

                if e > s:
                    extract = snd.extract_part(from_time=s, to_time=e)
                    safe_word = re.sub(r'[\\/*?:"<>|]', "", word)
                    out_file = os.path.join(out_dir, f"{str(global_idx).zfill(3)}_{safe_word}.wav")
                    extract.save(out_file, "WAV")
                    global_idx += 1


    def _export_txt(self, out_file, structure, rule, speaker=None):
        speaker = speaker or self.current_speaker
        is_continuous = (rule == "continuous")
        with open(out_file, "w", encoding="utf-8-sig") as f:
            global_idx = 1
            for grp_name, children in structure:
                if not is_continuous: global_idx = 1
                if grp_name and grp_name.strip() and grp_name not in ("未分组", "导入内容"):
                    f.write(f"{grp_name}\n")
                for child in children:
                    item = speaker.items[child]
                    if item.get('start') is not None:
                        txt_data = get_export_text_for_item(item, global_idx, speaker.last_params['pts'], pitch_floor=speaker.last_params['pitch_floor'], pitch_ceiling=speaker.last_params['pitch_ceiling'], voicing_threshold=speaker.last_params.get('voicing_threshold', 0.25))
                        f.write(txt_data)
                        global_idx += 1


    def _ensure_item_loaded(self, item, speaker=None):
        if not item:
            return
        spk = speaker
        if spk is None:
            for cand_spk in (self.speaker_manager.get_all_speakers() if hasattr(self.speaker_manager, "get_all_speakers") else []):
                if item in cand_spk.items.values():
                    spk = cand_spk
                    break
            if spk is None:
                spk = self.current_speaker

        has_snd = item.get('snd') is not None
        has_pitch = (item.get('pitch') is not None) or (item.get('pitch_data') is not None)

        if not has_snd and item.get('path') and os.path.exists(str(item['path'])):
            try:
                from modules.audio_core import load_compatible_sound
                item['snd'] = load_compatible_sound(item['path'])
                has_snd = item['snd'] is not None
            except Exception:
                pass

        if not has_snd and spk is not None:
            if getattr(spk, 'pending_long_snd', None) is not None:
                item['snd'] = spk.pending_long_snd
                has_snd = True
            else:
                long_p = getattr(spk, 'cli_long_snd_path', None) or getattr(spk, 'long_audio_path', None)
                if long_p and os.path.exists(str(long_p)):
                    try:
                        from modules.audio_core import load_compatible_sound
                        spk.pending_long_snd = load_compatible_sound(str(long_p))
                        item['snd'] = spk.pending_long_snd
                        has_snd = item['snd'] is not None
                    except Exception:
                        pass

        if not item.get('formant_data') and isinstance(item.get('preview_formants'), dict):
            pf_dict = item['preview_formants']
            if pf_dict.get('f1') is not None and pf_dict.get('f2') is not None:
                f1_a = np.asarray(pf_dict['f1'], dtype=float)
                f2_a = np.asarray(pf_dict['f2'], dtype=float)
                if 'xs' in pf_dict and pf_dict['xs'] is not None:
                    xs_a = np.asarray(pf_dict['xs'], dtype=float)
                elif item.get('start') is not None and item.get('end') is not None:
                    xs_a = np.linspace(float(item['start']), float(item['end']), len(f1_a))
                else:
                    xs_a = np.linspace(0.0, 1.0, len(f1_a))
                f_dict = {'xs': xs_a, 'f1': f1_a, 'f2': f2_a}
                if pf_dict.get('f3') is not None:
                    f_dict['f3'] = np.asarray(pf_dict['f3'], dtype=float)
                item['formant_data'] = f_dict

        if not has_snd:
            return

        eff_params = dict(self.params)
        if spk is not None and getattr(spk, 'last_params', None):
            for pk, pv in spk.last_params.items():
                if pk not in eff_params:
                    eff_params[pk] = pv

        if not has_pitch:
            try:
                from modules.audio_core import extract_f0
                pf = item.get('pitch_floor', eff_params.get('pitch_floor', 75))
                pc = item.get('pitch_ceiling', eff_params.get('pitch_ceiling', 600))
                vt = item.get('voicing_threshold', eff_params.get('voicing_threshold', 0.25))
                item['pitch_data'] = extract_f0(item['snd'], {'pitch_floor': pf, 'pitch_ceiling': pc, 'voicing_threshold': vt})
            except Exception:
                pass

        want_formant = (
            self.params.get('analysis_mode') == 'formant'
            or eff_params.get('analysis_mode') == 'formant'
            or item.get('analysis_mode') == 'formant'
        )
        if want_formant and not item.get('formant_data'):
            try:
                from modules.audio_core import extract_formants
                total_dur = item['snd'].get_total_duration()
                s_ref = item.get('macro_start') if item.get('macro_start') is not None else item.get('start')
                e_ref = item.get('macro_end') if item.get('macro_end') is not None else item.get('end')
                if s_ref is not None and e_ref is not None and total_dur > 5.0 and float(e_ref) > float(s_ref):
                    padding = 0.5
                    seg_start = max(0.0, float(s_ref) - padding)
                    seg_end = min(total_dur, float(e_ref) + padding)
                    part_snd = item['snd'].extract_part(from_time=seg_start, to_time=seg_end)
                    part_formant_data = extract_formants(part_snd, eff_params)
                    part_formant_data['xs'] = part_formant_data['xs'] + seg_start
                    item['formant_data'] = part_formant_data
                else:
                    item['formant_data'] = extract_formants(item['snd'], eff_params)
            except Exception:
                pass


    def _extract_syl_data(self, item, num_points):
        if item.get('start') is None or (not item.get('snd') and not item.get('pitch_data') and not item.get('pitch')): return 0, []

        pitch_data = item.get('pitch_data')
        if pitch_data:
            p_xs = pitch_data['xs']
            p_freqs = pitch_data['freqs']
        else:
            pitch = item.get('pitch')
            if not pitch: return 0, []
            p_xs = pitch.xs()
            p_freqs = pitch.selected_array['frequency']

        t_s, t_e = item['start'], item['end']
        if t_e <= t_s: return 0, []

        from modules.data_utils import split_into_syllables
        label = item.get('label', '')
        inner_splits = item.get('inner_splits', [])
        syls = split_into_syllables(label)
        chars_bounds = item.get('chars_bounds', [])
        if chars_bounds and len(chars_bounds) == len(syls):
            bounds = chars_bounds
        else:
            splits = [t_s] + [s for s in inner_splits if t_s < s < t_e] + [t_e]
            if len(syls) > 1 and len(splits) != len(syls) + 1:
                splits = np.linspace(t_s, t_e, len(syls) + 1).tolist()
            elif len(syls) <= 1:
                splits = [t_s, t_e]
            bounds = [[splits[i], splits[i+1]] for i in range(len(splits)-1)]

        syl_data = []
        for c_s, c_e in bounds:
            if c_e <= c_s:
                syl_data.append((0.0, [0.0]*num_points))
                continue

            valid_idx = np.where((p_xs >= c_s) & (p_xs <= c_e) & (p_freqs > 0))[0]
            if len(valid_idx) >= 2:
                v_s, v_e = p_xs[valid_idx[0]], p_xs[valid_idx[-1]]
                seg_xs = p_xs[valid_idx]
                seg_ys = p_freqs[valid_idx]
            else:
                syl_data.append((0.0, [0.0]*num_points))
                continue

            dur = v_e - v_s
            if dur <= 0:
                syl_data.append((0.0, [0.0]*num_points))
                continue

            times = np.linspace(v_s, v_e, num_points)
            if len(seg_xs) >= 2:
                f0s = np.interp(times, seg_xs, seg_ys).tolist()
                for j, t in enumerate(times):
                    if np.min(np.abs(seg_xs - t)) > 0.025:
                        f0s[j] = 0.0
                syl_data.append((dur, f0s))
            else:
                syl_data.append((dur, [0.0]*num_points))

        return t_e - t_s, syl_data


    def _export_xlsx(self, out_file, structure, rule, speaker=None):
        speaker = speaker or self.current_speaker
        import xlsxwriter
        import numpy as np
        from modules.data_utils import (
            ExcelStyleManager, autofit_worksheet_columns,
            write_analysis_sheet_with_formulas, build_five_point_chart,
            write_raw_pitch_sheet
        )
        is_continuous = (rule == "continuous")
        num_points = speaker.last_params['pts']

        max_syls = 1
        for grp_name, children in structure:
            for child in children:
                lbl = speaker.items[child].get('label', '')
                if len(lbl) > max_syls: max_syls = len(lbl)

        workbook = xlsxwriter.Workbook(out_file)
        style = ExcelStyleManager(workbook)
        ws_data = workbook.add_worksheet("数据")
        ws_res = workbook.add_worksheet("分析结果")
        raw_pitch_rows = []

        headers = ["组别", "编号", "词语", "总时长(s)"]
        for k in range(1, max_syls + 1):
            headers.append(f"字{k}_时长(s)")
            for i in range(1, num_points + 1):
                headers.append(f"字{k}_P{i}(Hz)")
        for col, header in enumerate(headers):
            ws_data.write(0, col, header, style.header_fmt)

        global_idx = 1
        row_idx = 1
        dict_data = {}
        data_sample_rows = []

        for grp_name, children in structure:
            if not is_continuous: global_idx = 1
            for child in children:
                item = speaker.items[child]
                self._ensure_item_loaded(item)
                total_dur, syl_data = self._extract_syl_data(item, num_points)
                if total_dur <= 0: continue

                raw_pitch_rows.append({
                    'group': grp_name,
                    'index': global_idx,
                    'item': item
                })

                if grp_name not in dict_data:
                    dict_data[grp_name] = {
                        'syl_dur_sums': [0.0]*max_syls, 'syl_counts': [0]*max_syls,
                        'f0_sums': [[0.0]*num_points for _ in range(max_syls)],
                        'f0_counts': [[0]*num_points for _ in range(max_syls)]
                    }

                col = 0
                ws_data.write(row_idx, col, grp_name, style.text_center); col += 1
                ws_data.write(row_idx, col, global_idx, style.int_fmt); col += 1
                ws_data.write(row_idx, col, item['label'], style.text_left); col += 1
                ws_data.write(row_idx, col, round(total_dur, 6), style.duration_fmt); col += 1

                sample_r = [grp_name, global_idx, item['label'], round(total_dur, 6)]

                for k in range(max_syls):
                    if k < len(syl_data):
                        dur, f0s = syl_data[k]
                        ws_data.write(row_idx, col, round(dur, 6), style.duration_fmt); col += 1
                        sample_r.append(round(dur, 6))
                        dict_data[grp_name]['syl_dur_sums'][k] += dur
                        dict_data[grp_name]['syl_counts'][k] += 1
                        for i, f0 in enumerate(f0s):
                            if not np.isnan(f0) and f0 > 0:
                                ws_data.write(row_idx, col, round(f0, 6), style.hz_fmt); col += 1
                                sample_r.append(round(f0, 6))
                                dict_data[grp_name]['f0_sums'][k][i] += f0
                                dict_data[grp_name]['f0_counts'][k][i] += 1
                            else:
                                ws_data.write(row_idx, col, "", style.text_center); col += 1
                                sample_r.append("")
                    else:
                        ws_data.write(row_idx, col, "", style.text_center); col += 1
                        sample_r.append("")
                        for _ in range(num_points):
                            ws_data.write(row_idx, col, "", style.text_center); col += 1
                            sample_r.append("")

                if len(data_sample_rows) < 100:
                    data_sample_rows.append(sample_r)

                row_idx += 1
                global_idx += 1

        ws_data.freeze_panes(1, 0)
        ws_data.autofilter(0, 0, max(row_idx - 1, 1), len(headers) - 1)
        autofit_worksheet_columns(ws_data, headers, data_sample_rows)

        write_raw_pitch_sheet(workbook, raw_pitch_rows, style_manager=style, include_speaker=False)

        all_avg_hz = []
        avg_points_map = {}

        for grp, st in dict_data.items():
            avg_points_map[grp] = []
            for k in range(max_syls):
                syl_avgs = []
                for i in range(num_points):
                    cnt = st['f0_counts'][k][i]
                    avg_hz = st['f0_sums'][k][i] / cnt if cnt > 0 else 0
                    syl_avgs.append(avg_hz)
                    if avg_hz > 0: all_avg_hz.append(avg_hz)
                avg_points_map[grp].append(syl_avgs)
        if not all_avg_hz:
            workbook.close()
            return

        min_hz, max_hz = min(all_avg_hz), max(all_avg_hz)

        # 写入分析结果 Sheet（全部使用 Excel 公式引用数据表）
        group_list = list(dict_data.keys())
        last_data_row = row_idx - 1  # 0-indexed
        res_row, _, _ = write_analysis_sheet_with_formulas(
            workbook, ws_res, group_list, num_points, max_syls, last_data_row
        )

        # 自动嵌入五度标调散点连线图
        try:
            build_five_point_chart(
                workbook, ws_res, dict_data, avg_points_map,
                num_points, max_syls, min_hz, max_hz,
                insert_cell=f'A{res_row + 3}',
                chart_title='各声调平均基频五度标调图（保留真实时长）'
            )
        except Exception as e:
            logging.getLogger(__name__).warning("Failed to generate five point chart in xlsx export: %s", e)

        workbook.close()


    def _collect_group_avg_data(self, structure, speaker=None):
        speaker = speaker or self.current_speaker
        num_points = speaker.last_params['pts']
        max_syls = 1
        dict_data = {}
        for grp_name, children in structure:
            for child in children:
                lbl = speaker.items[child].get('label', '')
                if len(lbl) > max_syls: max_syls = len(lbl)
                item = speaker.items[child]
                total_dur, syl_data = self._extract_syl_data(item, num_points)
                if total_dur <= 0: continue

                if grp_name not in dict_data:
                    dict_data[grp_name] = { 'f0_sums': [[0.0]*num_points for _ in range(20)], 'f0_counts': [[0]*num_points for _ in range(20)] }
                for k, (dur, f0s) in enumerate(syl_data):
                    for i, f0 in enumerate(f0s):
                        if not np.isnan(f0) and f0 > 0:
                            dict_data[grp_name]['f0_sums'][k][i] += f0
                            dict_data[grp_name]['f0_counts'][k][i] += 1

        all_avg_hz = []
        avg_points_map = {}
        for grp, st in dict_data.items():
            avg_points_map[grp] = []
            for k in range(max_syls):
                syl_avgs = []
                for i in range(num_points):
                    cnt = st['f0_counts'][k][i]
                    hz = st['f0_sums'][k][i] / cnt if cnt > 0 else 0
                    syl_avgs.append(hz)
                    if hz > 0: all_avg_hz.append(hz)
                avg_points_map[grp].append(syl_avgs)

        if not all_avg_hz: return None, 1
        min_hz, max_hz = min(all_avg_hz), max(all_avg_hz)

        import math
        result = {}
        for grp, syl_avgs_list in avg_points_map.items():
            flat_t_vals = []
            for syl_avgs in syl_avgs_list:
                for h in syl_avgs:
                    if h > 0 and max_hz > min_hz and min_hz > 0:
                        flat_t_vals.append(5 * (math.log10(h) - math.log10(min_hz)) / (math.log10(max_hz) - math.log10(min_hz)))
                    else: flat_t_vals.append(None)
            result[grp] = flat_t_vals

        return result, max_syls


    def _export_line_chart(self, out_file, structure, speaker=None):
        speaker = speaker or self.current_speaker
        import matplotlib.pyplot as plt
        from modules.acoustic_exporter import AcousticChartExporter, PUBLICATION_PRESETS
        from modules.core.acoustic_math import fit_chao_tone_code

        # Prefer unified AcousticChartExporter contour pipeline when speaker items are available
        if speaker is not None and getattr(speaker, "items", None):
            adapter = AcousticChartCLIAdapter(self)
            exporter = AcousticChartExporter(project_tree=adapter, app=self, all_speakers=[speaker])
            exporter.active_speaker = speaker
            exporter.params = {
                "chart_type": "contour",
                "export_scope": "active",
                "scale": "t_value",
                "groupby": "group",
                "contour_facet": "none",
                "contour_content": "average",
            }
            entries = exporter._extract_active_data([speaker])
            if entries:
                _, ext = os.path.splitext(out_file)
                exporter._export_dataset(entries, out_file, ext or ".png")
                return

        data, max_syls = self._collect_group_avg_data(structure, speaker)
        if not data:
            raise Exception("No valid data for charting")

        preset = PUBLICATION_PRESETS["chinese_journal"]
        num_points = speaker.last_params['pts']
        plt.rcParams['font.sans-serif'] = list(preset["font_sans"])
        plt.rcParams['axes.unicode_minus'] = False

        fig, ax = plt.subplots(figsize=(5.6 + 2.2 * max_syls, 4.8))
        fig.patch.set_facecolor("#FFFFFF")
        ax.set_facecolor("#FFFFFF")
        total_points = max_syls * num_points
        x_vals = list(range(1, total_points + 1))

        colors = preset["colors"]
        linestyles = preset["linestyles"]
        markers = preset["markers"]

        for i, (name, t_vals) in enumerate(data.items()):
            color = colors[i % len(colors)]
            ls = linestyles[i % len(linestyles)]
            mk = markers[i % len(markers)]
            syl_codes = []
            for k in range(max_syls):
                seg = [float(v) for v in t_vals[k * num_points:(k + 1) * num_points] if v is not None]
                if seg:
                    c = fit_chao_tone_code(seg, t_start=seg[0], t_mid=seg[len(seg) // 2], t_end=seg[-1])
                    if c:
                        syl_codes.append(c)
            t_code = "-".join(syl_codes)
            lbl_text = f"{name} [{t_code}]" if t_code else str(name)
            label_added = False
            for k in range(max_syls):
                s_start = k * num_points
                s_end = (k + 1) * num_points
                s_t_vals = t_vals[s_start:s_end]
                s_x_vals = x_vals[s_start:s_end]
                valid_x = [x for x, v in zip(s_x_vals, s_t_vals) if v is not None]
                valid_y = [v for v in s_t_vals if v is not None]
                if valid_x:
                    lbl = lbl_text if not label_added else None
                    ax.plot(valid_x, valid_y, linestyle=ls, marker=mk, color=color, linewidth=1.8, markersize=5.0, label=lbl)
                    label_added = True

        ax.set_ylim(-0.2, 5.2)
        ax.set_xlim(0.4, total_points + 0.8)
        ax.set_yticks([0, 1, 2, 3, 4, 5])
        ax.set_xticks(range(1, total_points + 1))
        ax.set_xticklabels([(idx % num_points) + 1 for idx in range(total_points)], fontsize=8.5)
        ax.tick_params(axis="both", direction="in", top=True, right=False)

        for deg in (1, 2, 3, 4, 5):
            ax.axhline(float(deg), color="#9CA3AF", linestyle=":", linewidth=0.75, alpha=0.55)
        for k in range(1, max_syls):
            ax.axvline(k * num_points + 0.5, color='gray', linestyle='--', alpha=0.55)

        ax_right = ax.twinx()
        ax_right.set_yticks([0.5, 1.5, 2.5, 3.5, 4.5])
        ax_right.set_yticklabels(["1度 [0-1]", "2度 [1-2]", "3度 [2-3]", "4度 [3-4]", "5度 [4-5]"], fontsize=8)
        ax_right.set_ylabel("赵元任五度标尺", fontsize=9)
        ax_right.set_autoscale_on(False)
        ax_right.set_ylim(ax.get_ylim())
        ax_right.grid(False)

        ax.set_xlabel('音节测量点 (时序展开)')
        ax.set_ylabel('T 值 (0-5 标度)')
        ax.legend(loc='upper left', bbox_to_anchor=(1.18, 1.0), fontsize=8.5, frameon=True)
        ax.grid(True, linestyle='--', alpha=0.25)

        fig.tight_layout()
        ax_right.set_ylim(ax.get_ylim())
        fig.savefig(out_file, dpi=600, bbox_inches='tight')
        plt.close(fig)


    def _export_kde_heatmap(self, out_file, structure, speaker=None):
        speaker = speaker or self.current_speaker
        import matplotlib.pyplot as plt
        from scipy.interpolate import interp1d
        from scipy.signal import savgol_filter
        from scipy.stats import gaussian_kde
        import math

        N_DENSE = 100
        group_syl_contours = {}

        max_syls = 1
        for grp_name, children in structure:
            group_syl_contours[grp_name] = {}
            for child in children:
                lbl = speaker.items[child].get('label', '')
                if len(lbl) > max_syls: max_syls = len(lbl)

        for grp_name, children in structure:
            for child in children:
                item = speaker.items[child]
                if item.get('start') is None or not item.get('snd'): continue

                pitch_data = item.get('pitch_data')
                if pitch_data:
                    p_xs = pitch_data['xs']
                    p_freqs = pitch_data['freqs']
                else:
                    pitch = item.get('pitch')
                    if not pitch: continue
                    p_xs, p_freqs = pitch.xs(), pitch.selected_array['frequency']

                t_s, t_e = item['start'], item['end']
                label = item.get('label', '')
                inner_splits = item.get('inner_splits', [])

                splits = [t_s] + [s for s in inner_splits if t_s < s < t_e] + [t_e]
                if len(splits) != len(label) + 1: splits = np.linspace(t_s, t_e, len(label) + 1).tolist()
                if len(label) <= 1: splits = [t_s, t_e]

                for k in range(len(splits) - 1):
                    c_s, c_e = splits[k], splits[k+1]
                    valid_idx = np.where((p_xs >= c_s) & (p_xs <= c_e) & (p_freqs > 0))[0]
                    if len(valid_idx) >= 2:
                        v_s, v_e = p_xs[valid_idx[0]], p_xs[valid_idx[-1]]
                        mask = (p_xs >= v_s) & (p_xs <= v_e) & (p_freqs > 0)
                        valid_freqs = p_freqs[mask]
                        if len(valid_freqs) < 3: continue

                        win = len(valid_freqs) // 3
                        if win % 2 == 0: win += 1
                        win = max(win, 3)
                        smoothed = savgol_filter(valid_freqs, win, 2) if len(valid_freqs) > win else valid_freqs

                        x_orig = np.linspace(0, 1, len(smoothed))
                        f_interp = interp1d(x_orig, smoothed, kind='linear')
                        y_dense = f_interp(np.linspace(0, 1, N_DENSE))

                        if k not in group_syl_contours[grp_name]: group_syl_contours[grp_name][k] = []
                        group_syl_contours[grp_name][k].append(y_dense)

        all_mean_vals = []
        for name, syls_dict in group_syl_contours.items():
            for k, y_arrays in syls_dict.items():
                if y_arrays:
                    mean_contour = np.mean(y_arrays, axis=0)
                    all_mean_vals.extend(mean_contour.tolist())

        if not all_mean_vals:
            self._emit(False, error="No valid data to plot")
            return

        min_f0, max_f0 = min(all_mean_vals), max(all_mean_vals)

        def hz_to_5_scale(hz):
            if max_f0 == min_f0: return 3.0
            return 5 * (np.log(hz) - np.log(min_f0)) / (np.log(max_f0) - np.log(min_f0))

        group_norm_points = {}
        for name, syls_dict in group_syl_contours.items():
            X_all, Y_all = [], []
            for k, y_arrays in syls_dict.items():
                x_dense = np.linspace(k * 100, (k + 1) * 100, N_DENSE)
                for y_arr in y_arrays:
                    X_all.extend(x_dense.tolist())
                    Y_all.extend([hz_to_5_scale(h) for h in y_arr])
            group_norm_points[name] = (np.array(X_all), np.array(Y_all))

        plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'Arial Unicode MS']
        plt.rcParams['axes.unicode_minus'] = False

        groups_with_data = [g for g in getattr(speaker, 'cli_groups', []) if group_norm_points.get(g) and len(group_norm_points[g][0]) > 0]
        n_groups = len(groups_with_data)
        if n_groups == 0:
            raise Exception("No valid data for KDE Heatmap")

        n_cols = min(2, n_groups)
        n_rows = math.ceil(n_groups / n_cols)
        fig, axes = plt.subplots(n_rows, n_cols, figsize=(5 * max_syls * n_cols, 5 * n_rows), squeeze=False, sharex=True, sharey=True)
        axes_flat = axes.flatten()

        for idx, grp_name in enumerate(groups_with_data):
            ax = axes_flat[idx]
            X_all, Y_all = group_norm_points[grp_name]

            xmin, xmax = 0, max_syls * 100
            ymin, ymax = -1, 6

            positions = np.vstack([X_all, Y_all])
            try:
                kernel = gaussian_kde(positions, bw_method=0.15)
                xi, yi = np.mgrid[xmin:xmax:200j, ymin:ymax:100j]
                zi = kernel(np.vstack([xi.flatten(), yi.flatten()]))
                zi = zi.reshape(xi.shape)

                vmax = zi.max()
                if vmax > 0:
                    levels = np.linspace(vmax * 0.05, vmax, 30)
                    ax.contourf(xi, yi, zi, levels=levels, cmap="YlOrRd", extend='neither')
            except Exception:
                pass

            for k in range(1, max_syls):
                ax.axvline(k * 100, color='gray', linestyle='--', alpha=0.8)

            ax.set_title(grp_name, fontsize=16)
            ax.set_ylim(-1, 6)
            ax.set_xlim(0, max_syls * 100)

        for idx in range(n_groups, len(axes_flat)): axes_flat[idx].set_visible(False)

        fig.suptitle('KDE Heatmap', fontsize=20, fontweight='bold', y=1.05)
        fig.tight_layout()
        fig.savefig(out_file, dpi=300, bbox_inches='tight')
        plt.close(fig)


    def _export_txt_integrated(self, out_file, speakers, rule):
        import math
        import numpy as np
        is_continuous = (rule == "continuous")
        num_points = self.current_speaker.last_params['pts']
        speaker_stats = {}
        speaker_rows = {}
        max_syls = 1

        for s in speakers:
            spk_id = getattr(s, 'id', s.name)
            s_struct = [(grp, [iid for iid, item in s.items.items() if item.get('group') == grp and not item.get('is_excluded', False)]) for grp in getattr(s, 'cli_groups', [])]
            rows = []
            f0_values = []
            for grp_name, children in s_struct:
                for child in children:
                    item = s.items[child]
                    lbl = item.get('label', '')
                    if len(lbl) > max_syls: max_syls = len(lbl)
                    self._ensure_item_loaded(item)
                    if not item.get('pitch') and not item.get('pitch_data'):
                        continue
                    total_dur, syl_data = self._extract_syl_data(item, num_points)
                    if total_dur <= 0: continue
                    rows.append({'group': grp_name, 'label': lbl, 'total_dur': total_dur, 'syl_data': syl_data, 'raw_item': item})
                    for _, freqs in syl_data:
                        for f in freqs:
                            if f > 0: f0_values.append(f)
            if f0_values:
                speaker_stats[spk_id] = (float(np.min(f0_values)), float(np.max(f0_values)))
            else:
                speaker_stats[spk_id] = (0.0, 0.0)
            speaker_rows[spk_id] = rows

        with open(out_file, "w", encoding="utf-8-sig") as f_out:
            headers = ["发音人", "组别", "编号", "词语", "总时长(s)"]
            for k in range(1, max_syls + 1):
                headers.append(f"字{k}_时长(s)")
                for i in range(1, num_points + 1): headers.append(f"字{k}_T{i}")
            f_out.write("\t".join(headers) + "\n")

            for s in speakers:
                spk_id = getattr(s, 'id', s.name)
                rows = speaker_rows.get(spk_id, [])
                s_min, s_max = speaker_stats.get(spk_id, (0.0, 0.0))
                has_valid_range = (s_min > 0 and s_max > s_min)
                log_min = math.log10(s_min) if s_min > 0 else 0.0
                log_max = math.log10(s_max) if s_max > 0 else 0.0
                log_diff = log_max - log_min if log_max > log_min else 1.0

                current_group = None
                group_item_idx = 0
                global_idx = 0
                for r in rows:
                    if r['group'] != current_group:
                        current_group = r['group']
                        group_item_idx = 1
                    else:
                        group_item_idx += 1
                    global_idx += 1
                    display_idx = global_idx if is_continuous else group_item_idx

                    line_parts = [s.name, r['group'], str(display_idx), r['label'], f"{r['total_dur']:.4f}"]
                    for s_dur, freqs in r['syl_data']:
                        line_parts.append(f"{s_dur:.4f}")
                        for f in freqs:
                            if f > 0 and has_valid_range:
                                t_v = ((math.log10(f) - log_min) / log_diff) * 5.0
                                line_parts.append(f"{t_v:.2f}")
                            else:
                                line_parts.append("")
                    fill_count = max_syls - len(r['syl_data'])
                    for _ in range(fill_count):
                        line_parts.append("")
                        for _ in range(num_points): line_parts.append("")
                    f_out.write("\t".join(line_parts) + "\n")


    def _export_xlsx_integrated(self, out_file, speakers, rule):
        import xlsxwriter
        import numpy as np
        import math
        from modules.data_utils import (
            ExcelStyleManager, autofit_worksheet_columns,
            write_raw_pitch_sheet
        )
        is_continuous = (rule == "continuous")
        num_points = self.current_speaker.last_params['pts']
        speaker_stats = {}
        speaker_rows = {}
        max_syls = 1

        for s in speakers:
            spk_id = getattr(s, 'id', s.name)
            s_struct = [(grp, [iid for iid, item in s.items.items() if item.get('group') == grp and not item.get('is_excluded', False)]) for grp in getattr(s, 'cli_groups', [])]
            rows = []
            f0_values = []
            for grp_name, children in s_struct:
                for child in children:
                    item = s.items[child]
                    lbl = item.get('label', '')
                    if len(lbl) > max_syls: max_syls = len(lbl)
                    self._ensure_item_loaded(item)
                    if not item.get('pitch') and not item.get('pitch_data'):
                        continue
                    total_dur, syl_data = self._extract_syl_data(item, num_points)
                    if total_dur <= 0: continue
                    rows.append({'group': grp_name, 'label': lbl, 'total_dur': total_dur, 'syl_data': syl_data, 'raw_item': item})
                    for _, freqs in syl_data:
                        for f in freqs:
                            if f > 0: f0_values.append(f)
            if f0_values:
                speaker_stats[spk_id] = (float(np.min(f0_values)), float(np.max(f0_values)))
            else:
                speaker_stats[spk_id] = (0.0, 0.0)
            speaker_rows[spk_id] = rows

        workbook = xlsxwriter.Workbook(out_file)
        style = ExcelStyleManager(workbook)

        ws_data = workbook.add_worksheet("整合数据(T值)")
        ws_res = workbook.add_worksheet("分析结果")
        headers = ["发音人", "组别", "编号", "词语", "总时长(s)"]
        for k in range(1, max_syls + 1):
            headers.append(f"字{k}_时长(s)")
            for i in range(1, num_points + 1): headers.append(f"字{k}_T{i}")
        for col, header in enumerate(headers):
            ws_data.write(0, col, header, style.header_fmt)

        group_stats = {}
        raw_pitch_rows = []
        int_data_sample_rows = []
        row_idx = 1
        for s in speakers:
            spk_id = getattr(s, 'id', s.name)
            rows = speaker_rows.get(spk_id, [])
            s_min, s_max = speaker_stats.get(spk_id, (0.0, 0.0))
            has_valid_range = (s_min > 0 and s_max > s_min)
            log_min = math.log10(s_min) if s_min > 0 else 0.0
            log_max = math.log10(s_max) if s_max > 0 else 0.0
            log_diff = log_max - log_min if log_max > log_min else 1.0

            current_group = None
            group_item_idx = 0
            global_idx = 0
            for r in rows:
                if r['group'] != current_group:
                    current_group = r['group']
                    group_item_idx = 1
                else:
                    group_item_idx += 1
                global_idx += 1
                display_idx = global_idx if is_continuous else group_item_idx

                raw_pitch_rows.append({
                    'speaker': s.name,
                    'group': r['group'],
                    'index': display_idx,
                    'item': r['raw_item']
                })
                ws_data.write(row_idx, 0, s.name, style.text_center)
                ws_data.write(row_idx, 1, r['group'], style.text_center)
                ws_data.write(row_idx, 2, display_idx, style.int_fmt)
                ws_data.write(row_idx, 3, r['label'], style.text_left)
                ws_data.write(row_idx, 4, round(r['total_dur'], 4), style.duration_fmt)
                col_idx = 5

                sample_r = [s.name, r['group'], display_idx, r['label'], round(r['total_dur'], 4)]

                grp_name = r['group']
                if grp_name not in group_stats:
                    group_stats[grp_name] = {
                        'syl_dur_sums': [0.0] * max_syls,
                        'syl_counts': [0] * max_syls,
                        't_sums': [[0.0] * num_points for _ in range(max_syls)],
                        't_counts': [[0] * num_points for _ in range(max_syls)]
                    }
                stats = group_stats[grp_name]

                for k, (s_dur, freqs) in enumerate(r['syl_data']):
                    ws_data.write(row_idx, col_idx, round(s_dur, 4), style.duration_fmt)
                    sample_r.append(round(s_dur, 4))
                    col_idx += 1
                    if k < max_syls:
                        stats['syl_dur_sums'][k] += s_dur
                        stats['syl_counts'][k] += 1
                    for i, f in enumerate(freqs):
                        if f > 0 and has_valid_range:
                            t_val = round(((math.log10(f) - log_min) / log_diff) * 5.0, 2)
                            ws_data.write(row_idx, col_idx, t_val, style.t_val_fmt)
                            sample_r.append(t_val)
                            if k < max_syls:
                                stats['t_sums'][k][i] += t_val
                                stats['t_counts'][k][i] += 1
                        else:
                            ws_data.write(row_idx, col_idx, "", style.text_center)
                            sample_r.append("")
                        col_idx += 1

                fill_count = max_syls - len(r['syl_data'])
                for _ in range(fill_count):
                    ws_data.write(row_idx, col_idx, "", style.text_center)
                    sample_r.append("")
                    col_idx += 1
                    for _ in range(num_points):
                        ws_data.write(row_idx, col_idx, "", style.text_center)
                        sample_r.append("")
                        col_idx += 1
                if len(int_data_sample_rows) < 100:
                    int_data_sample_rows.append(sample_r)
                row_idx += 1

        ws_data.freeze_panes(1, 0)
        ws_data.autofilter(0, 0, max(row_idx - 1, 1), len(headers) - 1)
        autofit_worksheet_columns(ws_data, headers, int_data_sample_rows)

        res_headers = ["组别"]
        for k in range(1, max_syls + 1):
            res_headers.append(f"字{k}_平均时长(s)")
            for i in range(1, num_points + 1): res_headers.append(f"字{k}_T{i}")
        for col, header in enumerate(res_headers):
            ws_res.write(0, col, header, style.header_fmt)

        res_row = 1
        int_res_sample_rows = []
        for grp, st in group_stats.items():
            ws_res.write(res_row, 0, grp, style.text_center)
            col = 1
            sample_res = [grp]
            for k in range(max_syls):
                cnt = st['syl_counts'][k]
                if cnt > 0:
                    avg_dur = st['syl_dur_sums'][k] / cnt
                    ws_res.write(res_row, col, round(avg_dur, 4), style.duration_fmt)
                    sample_res.append(round(avg_dur, 4))
                else:
                    ws_res.write(res_row, col, "", style.text_center)
                    sample_res.append("")
                col += 1
                for i in range(num_points):
                    t_cnt = st['t_counts'][k][i]
                    if t_cnt > 0:
                        ws_res.write(res_row, col, round(st['t_sums'][k][i] / t_cnt, 2), style.t_val_fmt)
                        sample_res.append(round(st['t_sums'][k][i] / t_cnt, 2))
                    else:
                        ws_res.write(res_row, col, "", style.text_center)
                        sample_res.append("")
                    col += 1
            if len(int_res_sample_rows) < 100:
                int_res_sample_rows.append(sample_res)
            res_row += 1

        ws_res.freeze_panes(1, 0)
        ws_res.autofilter(0, 0, max(res_row - 1, 1), len(res_headers) - 1)
        autofit_worksheet_columns(ws_res, res_headers, int_res_sample_rows)

        write_raw_pitch_sheet(workbook, raw_pitch_rows, style_manager=style, include_speaker=True)

        if group_stats:
            try:
                ws_chart_data = workbook.add_worksheet("图表数据")
                ws_chart_data.hide()
                ws_chart_data.write(0, 0, "组别")
                for p in range(1, max_syls * num_points + 1):
                    ws_chart_data.write(0, p, p)
                chart_row = 1
                for grp, st in group_stats.items():
                    ws_chart_data.write(chart_row, 0, grp)
                    col_idx = 1
                    for k in range(max_syls):
                        for i in range(num_points):
                            t_cnt = st['t_counts'][k][i]
                            if t_cnt > 0:
                                ws_chart_data.write(chart_row, col_idx, round(st['t_sums'][k][i] / t_cnt, 2))
                            else:
                                ws_chart_data.write(chart_row, col_idx, "")
                            col_idx += 1
                    chart_row += 1
                chart = workbook.add_chart({'type': 'scatter', 'subtype': 'straight_with_markers'})
                for r in range(1, len(group_stats) + 1):
                    chart.add_series({
                        'name':       ['图表数据', r, 0],
                        'categories': ['图表数据', 0, 1, 0, max_syls * num_points],
                        'values':     ['图表数据', r, 1, r, max_syls * num_points],
                        'line':       {'width': 2.5},
                        'marker':     {'type': 'circle', 'size': 6},
                    })
                chart.set_title({
                    'name': '多发音人整合声调格局图',
                    'name_font': {'name': 'Microsoft YaHei', 'size': 14, 'bold': True}
                })
                chart.set_x_axis({
                    'name': '测量点 (时序展开)',
                    'name_font': {'name': 'Microsoft YaHei', 'size': 10},
                    'num_font': {'name': 'Arial', 'size': 9}
                })
                chart.set_y_axis({
                    'name': '赵元任五度标调法',
                    'name_font': {'name': 'Microsoft YaHei', 'size': 10},
                    'num_font': {'name': 'Arial', 'size': 1, 'color': 'white'},
                    'min': 0, 'max': 5,
                    'major_unit': 1,
                    'major_gridlines': {'visible': True, 'line': {'color': '#D0D0D0', 'width': 0.5}},
                    'major_tick_mark': 'none',
                })
                chart.set_legend({
                    'position': 'right',
                    'font': {'name': 'Microsoft YaHei', 'size': 9}
                })
                chart.set_size({'width': 650, 'height': 450})
                ws_res.insert_chart(f'A{res_row + 3}', chart)
            except Exception as e:
                logging.getLogger(__name__).warning("Failed to insert integrated xlsx chart: %s", e)

        workbook.close()


    def _export_line_chart_integrated(self, out_file, speakers):
        from modules.acoustic_exporter import AcousticChartExporter
        adapter = AcousticChartCLIAdapter(self)
        exporter = AcousticChartExporter(project_tree=adapter, app=self, all_speakers=list(speakers))
        exporter.params = {
            "chart_type": "contour",
            "export_scope": "integrated",
            "scale": "t_value",
            "groupby": "group",
            "contour_facet": "none",
            "contour_content": "average",
        }
        entries = exporter._extract_active_data(list(speakers))
        if not entries:
            raise Exception("No valid data for charting across speakers")
        _, ext = os.path.splitext(out_file)
        exporter._export_dataset(entries, out_file, ext or ".png")


    def _process_kde_item(self, item, N_DENSE):
        from scipy.interpolate import interp1d
        from scipy.signal import savgol_filter
        import numpy as np

        if item.get('start') is None or not item.get('snd'): return None

        pitch_data = item.get('pitch_data')
        if pitch_data:
            p_xs = pitch_data['xs']
            p_freqs = pitch_data['freqs']
        else:
            pitch = item.get('pitch')
            if not pitch: return None
            p_xs, p_freqs = pitch.xs(), pitch.selected_array['frequency']

        t_s, t_e = item['start'], item['end']
        label = item.get('label', '')
        inner_splits = item.get('inner_splits', [])

        splits = [t_s] + [s_split for s_split in inner_splits if t_s < s_split < t_e] + [t_e]
        if len(splits) != len(label) + 1: splits = np.linspace(t_s, t_e, len(label) + 1).tolist()
        if len(label) <= 1: splits = [t_s, t_e]

        y_denses = {}
        for k in range(len(splits) - 1):
            c_s, c_e = splits[k], splits[k+1]
            valid_idx = np.where((p_xs >= c_s) & (p_xs <= c_e) & (p_freqs > 0))[0]
            if len(valid_idx) >= 2:
                v_s, v_e = p_xs[valid_idx[0]], p_xs[valid_idx[-1]]
                mask = (p_xs >= v_s) & (p_xs <= v_e) & (p_freqs > 0)
                valid_freqs = p_freqs[mask]
                if len(valid_freqs) < 3: continue

                win = len(valid_freqs) // 3
                if win % 2 == 0: win += 1
                win = max(win, 3)
                smoothed = savgol_filter(valid_freqs, win, 2) if len(valid_freqs) > win else valid_freqs

                x_orig = np.linspace(0, 1, len(smoothed))
                f_interp = interp1d(x_orig, smoothed, kind='linear')
                y_dense = f_interp(np.linspace(0, 1, N_DENSE))

                y_denses[k] = y_dense
        return y_denses


    def _get_kde_max_syls(self, speakers):
        max_syls = 1
        for s in speakers:
            for item in s.items.values():
                lbl = item.get('label', '')
                if len(lbl) > max_syls: max_syls = len(lbl)
        return max_syls


    def _extract_kde_heatmap_contours(self, speakers, N_DENSE):
        group_syl_contours = {}
        for s in speakers:
            s_struct = [(grp, [iid for iid, item in s.items.items() if item.get('group') == grp and not item.get('is_excluded', False)]) for grp in getattr(s, 'cli_groups', [])]
            for grp_name, children in s_struct:
                if grp_name not in group_syl_contours:
                    group_syl_contours[grp_name] = {}
                for child in children:
                    item = s.items[child]
                    y_denses = self._process_kde_item(item, N_DENSE)
                    if not y_denses: continue
                    for k, y_dense in y_denses.items():
                        if k not in group_syl_contours[grp_name]: group_syl_contours[grp_name][k] = []
                        group_syl_contours[grp_name][k].append(y_dense)
        return group_syl_contours


    def _calculate_kde_f0_bounds(self, group_syl_contours):
        import numpy as np
        all_mean_vals = []
        for name, syls_dict in group_syl_contours.items():
            for k, y_arrays in syls_dict.items():
                if y_arrays:
                    mean_contour = np.mean(y_arrays, axis=0)
                    all_mean_vals.extend(mean_contour.tolist())

        if not all_mean_vals:
            return None, None
        return min(all_mean_vals), max(all_mean_vals)


    def _normalize_kde_points(self, group_syl_contours, min_f0, max_f0, N_DENSE):
        import numpy as np
        def hz_to_5_scale(hz):
            if max_f0 == min_f0: return 3.0
            return 5 * (np.log(hz) - np.log(min_f0)) / (np.log(max_f0) - np.log(min_f0))

        group_norm_points = {}
        for name, syls_dict in group_syl_contours.items():
            X_all, Y_all = [], []
            for k, y_arrays in syls_dict.items():
                x_dense = np.linspace(k * 100, (k + 1) * 100, N_DENSE)
                for y_arr in y_arrays:
                    X_all.extend(x_dense.tolist())
                    Y_all.extend([hz_to_5_scale(h) for h in y_arr])
            group_norm_points[name] = (np.array(X_all), np.array(Y_all))
        return group_norm_points


    def _draw_kde_heatmap_integrated(self, out_file, speakers, group_norm_points, max_syls):
        import matplotlib.pyplot as plt
        from scipy.stats import gaussian_kde
        import numpy as np
        import math

        plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'Arial Unicode MS']
        plt.rcParams['axes.unicode_minus'] = False

        all_groups_with_data = []
        for s in speakers:
            for g in getattr(s, 'cli_groups', []):
                if g not in all_groups_with_data and group_norm_points.get(g) and len(group_norm_points[g][0]) > 0:
                    all_groups_with_data.append(g)

        n_groups = len(all_groups_with_data)
        if n_groups == 0:
            raise Exception("No valid data for KDE Heatmap")

        n_cols = min(2, n_groups)
        n_rows = math.ceil(n_groups / n_cols)
        fig, axes = plt.subplots(n_rows, n_cols, figsize=(5 * max_syls * n_cols, 5 * n_rows), squeeze=False, sharex=True, sharey=True)
        axes_flat = axes.flatten()

        for idx, grp_name in enumerate(all_groups_with_data):
            ax = axes_flat[idx]
            X_all, Y_all = group_norm_points[grp_name]

            xmin, xmax = 0, max_syls * 100
            ymin, ymax = -1, 6

            positions = np.vstack([X_all, Y_all])
            try:
                kernel = gaussian_kde(positions, bw_method=0.15)
                xi, yi = np.mgrid[xmin:xmax:200j, ymin:ymax:100j]
                zi = kernel(np.vstack([xi.flatten(), yi.flatten()]))
                zi = zi.reshape(xi.shape)

                vmax = zi.max()
                if vmax > 0:
                    levels = np.linspace(vmax * 0.05, vmax, 30)
                    ax.contourf(xi, yi, zi, levels=levels, cmap="YlOrRd", extend='neither')
            except Exception:
                pass

            for k in range(1, max_syls):
                ax.axvline(k * 100, color='gray', linestyle='--', alpha=0.8)

            ax.set_title(grp_name, fontsize=16)
            ax.set_ylim(-1, 6)
            ax.set_xlim(0, max_syls * 100)

        for idx in range(n_groups, len(axes_flat)): axes_flat[idx].set_visible(False)

        fig.suptitle(f'Integrated KDE Heatmap ({len(speakers)} Speakers)', fontsize=20, fontweight='bold', y=1.05)
        fig.tight_layout()
        fig.savefig(out_file, dpi=300, bbox_inches='tight')
        plt.close(fig)


    def _export_kde_heatmap_integrated(self, out_file, speakers):
        N_DENSE = 100

        max_syls = self._get_kde_max_syls(speakers)
        group_syl_contours = self._extract_kde_heatmap_contours(speakers, N_DENSE)
        min_f0, max_f0 = self._calculate_kde_f0_bounds(group_syl_contours)

        if min_f0 is None or max_f0 is None:
            self._emit(False, error="No valid data to plot")
            return

        group_norm_points = self._normalize_kde_points(group_syl_contours, min_f0, max_f0, N_DENSE)
        self._draw_kde_heatmap_integrated(out_file, speakers, group_norm_points, max_syls)


    def _export_formant_table(self, out_file, speakers_to_process):
        try:
            import xlsxwriter
        except ImportError:
            self._emit(False, error="Missing xlsxwriter library. Please install via: pip install xlsxwriter")
            return

        from modules.data_utils import (
            ExcelStyleManager, autofit_worksheet_columns,
            write_formant_analysis_sheet_with_formulas,
            clean_str, get_item_syllable_bounds, split_into_syllables,
            sample_formant_points_by_bounds_extended
        )

        workbook = xlsxwriter.Workbook(out_file)
        style = ExcelStyleManager(workbook)

        ws_data = workbook.add_worksheet("提取数据")
        ws_analysis = workbook.add_worksheet("分析图表")
        ws_raw = workbook.add_worksheet("原始数据")

        show_f3 = bool(self.params.get('show_f3', False))
        data_headers = ["发音人", "组别", "编号", "词语", "音节序号", "音节", "时间点序号", "时间(s)", "F1(Hz)", "F2(Hz)"]
        if show_f3:
            data_headers.append("F3(Hz)")
        for col, h in enumerate(data_headers):
            ws_data.write(0, col, h, style.header_fmt)

        data_row = 1
        formant_sample_rows = []
        group_char_pairs = []
        seen_pairs = set()
        raw_formant_entries = []

        for spk in speakers_to_process:
            is_continuous = (self.params.get('num_rule', 'continuous') == 'continuous')
            pts = int(self.params.get('pts', 11))
            strategy = self.params.get('formant_sample_strategy', '整段11点')

            groups = {}
            for item_id, item in spk.items.items():
                if item.get('is_excluded', False):
                    continue
                g = item.get('group', '导入内容')
                if g not in groups:
                    groups[g] = []
                groups[g].append(item)

            sorted_groups = sorted(groups.keys(), key=clean_str)

            global_idx = 1
            for grp_name in sorted_groups:
                if not is_continuous:
                    global_idx = 1
                items_in_grp = groups[grp_name]
                items_in_grp = sorted(items_in_grp, key=lambda x: clean_str(x.get('label', '')))

                for item in items_in_grp:
                    self._ensure_item_loaded(item)
                    if not item.get('formant_data'):
                        continue

                    bounds = get_item_syllable_bounds(item)
                    syls = split_into_syllables(item.get('label', ''))
                    res_samp = sample_formant_points_by_bounds_extended(item, bounds, pts, strategy, include_f3=show_f3)
                    preview_times = res_samp["times"]
                    f1_vals = res_samp["f1"]
                    f2_vals = res_samp["f2"]
                    f3_vals = res_samp["f3"]

                    raw_formant_entries.append({
                        'speaker': spk.name,
                        'group': grp_name,
                        'index': global_idx,
                        'item': item,
                    })

                    for idx_syl, (c_s, c_e) in enumerate(bounds):
                        char = syls[idx_syl] if idx_syl < len(syls) else f"字{idx_syl+1}"
                        flat_start = idx_syl * pts

                        pair_key = (grp_name, char)
                        if pair_key not in seen_pairs:
                            seen_pairs.add(pair_key)
                            group_char_pairs.append(pair_key)

                        for idx_pt in range(pts):
                            ws_data.write(data_row, 0, spk.name, style.text_center)
                            ws_data.write(data_row, 1, grp_name, style.text_center)
                            ws_data.write(data_row, 2, global_idx, style.int_fmt)
                            ws_data.write(data_row, 3, item.get('label', ''), style.text_left)
                            ws_data.write(data_row, 4, idx_syl + 1, style.int_fmt)
                            ws_data.write(data_row, 5, char, style.text_center)
                            ws_data.write(data_row, 6, idx_pt + 1, style.int_fmt)
                            ws_data.write(data_row, 7, round(preview_times[flat_start + idx_pt], 6), style.duration_fmt)

                            f1_v = f1_vals[flat_start + idx_pt]
                            f2_v = f2_vals[flat_start + idx_pt]

                            if np.isnan(f1_v):
                                ws_data.write(data_row, 8, "", style.text_center)
                            else:
                                ws_data.write(data_row, 8, round(f1_v, 1), style.hz_fmt)

                            if np.isnan(f2_v):
                                ws_data.write(data_row, 9, "", style.text_center)
                            else:
                                ws_data.write(data_row, 9, round(f2_v, 1), style.hz_fmt)

                            sample_r = [
                                spk.name, grp_name, global_idx, item.get('label', ''),
                                idx_syl + 1, char, idx_pt + 1, round(preview_times[flat_start + idx_pt], 6),
                                round(f1_v, 1) if not np.isnan(f1_v) else "",
                                round(f2_v, 1) if not np.isnan(f2_v) else ""
                            ]

                            if show_f3:
                                f3_v = f3_vals[flat_start + idx_pt]
                                if np.isnan(f3_v):
                                    ws_data.write(data_row, 10, "", style.text_center)
                                    sample_r.append("")
                                else:
                                    ws_data.write(data_row, 10, round(f3_v, 1), style.hz_fmt)
                                    sample_r.append(round(f3_v, 1))

                            if len(formant_sample_rows) < 100:
                                formant_sample_rows.append(sample_r)

                            data_row += 1

                    global_idx += 1

        ws_data.freeze_panes(1, 0)
        ws_data.autofilter(0, 0, max(data_row - 1, 1), len(data_headers) - 1)
        autofit_worksheet_columns(ws_data, data_headers, formant_sample_rows)

        last_data_row = data_row - 1
        write_formant_analysis_sheet_with_formulas(
            workbook, ws_analysis, group_char_pairs, last_data_row,
            data_sheet_name='提取数据',
        )

        raw_headers = [
            "发音人", "组别", "编号", "词语", "音节序号", "音节",
            "绝对时间(s)", "字内相对时间(s)", "F1(Hz)", "F2(Hz)"
        ]
        if show_f3:
            raw_headers.append("F3(Hz)")
        raw_headers.append("状态")
        for col, h in enumerate(raw_headers):
            ws_raw.write(0, col, h, style.header_fmt)

        raw_row = 1
        raw_sample_rows = []
        for entry in raw_formant_entries:
            item = entry['item']
            f_data = item.get('formant_data')
            if not f_data or 'xs' not in f_data:
                continue

            xs = f_data['xs']
            f1_arr = f_data.get('f1', np.array([]))
            f2_arr = f_data.get('f2', np.array([]))

            bounds = get_item_syllable_bounds(item)
            syls = split_into_syllables(item.get('label', ''))

            f3_arr = f_data.get('f3') if show_f3 else None
            for syl_idx, (c_s, c_e) in enumerate(bounds):
                char = syls[syl_idx] if syl_idx < len(syls) else f"字{syl_idx+1}"
                mask = (xs >= c_s) & (xs <= c_e)
                indices = np.where(mask)[0]

                for frame_idx in indices:
                    t = float(xs[frame_idx])
                    f1 = float(f1_arr[frame_idx]) if frame_idx < len(f1_arr) and np.isfinite(f1_arr[frame_idx]) else 0.0
                    f2 = float(f2_arr[frame_idx]) if frame_idx < len(f2_arr) and np.isfinite(f2_arr[frame_idx]) else 0.0
                    if show_f3:
                        f3 = float(f3_arr[frame_idx]) if f3_arr is not None and frame_idx < len(f3_arr) and np.isfinite(f3_arr[frame_idx]) else 0.0

                    status = "有效" if (f1 > 0 and f2 > 0 and f2 > f1) else "无效"

                    ws_raw.write(raw_row, 0, entry['speaker'], style.text_center)
                    ws_raw.write(raw_row, 1, entry['group'], style.text_center)
                    ws_raw.write(raw_row, 2, entry['index'], style.int_fmt)
                    ws_raw.write(raw_row, 3, item.get('label', ''), style.text_left)
                    ws_raw.write(raw_row, 4, syl_idx + 1, style.int_fmt)
                    ws_raw.write(raw_row, 5, char, style.text_center)
                    ws_raw.write(raw_row, 6, round(t, 6), style.decimal6_fmt)
                    ws_raw.write(raw_row, 7, round(t - c_s, 6), style.decimal6_fmt)
                    ws_raw.write(raw_row, 8, round(f1, 1) if f1 > 0 else 0.0, style.hz_fmt)
                    ws_raw.write(raw_row, 9, round(f2, 1) if f2 > 0 else 0.0, style.hz_fmt)
                    if show_f3:
                        ws_raw.write(raw_row, 10, round(f3, 1) if f3 > 0 else 0.0, style.hz_fmt)
                        ws_raw.write(raw_row, 11, status, style.text_center)
                    else:
                        ws_raw.write(raw_row, 10, status, style.text_center)

                    if len(raw_sample_rows) < 100:
                        raw_sample_rows.append([
                            entry['speaker'], entry['group'], entry['index'], item.get('label', ''),
                            syl_idx + 1, char, round(t, 6), round(t - c_s, 6),
                            round(f1, 1) if f1 > 0 else 0.0, round(f2, 1) if f2 > 0 else 0.0,
                            *( [round(f3, 1) if f3 > 0 else 0.0] if show_f3 else [] ),
                            status
                        ])
                    raw_row += 1

        ws_raw.freeze_panes(1, 0)
        ws_raw.autofilter(0, 0, max(raw_row - 1, 1), len(raw_headers) - 1)
        autofit_worksheet_columns(ws_raw, raw_headers, raw_sample_rows)

        workbook.close()


    def _cli_sample_formant_points(self, item, pts=11, strategy='整段11点'):
        start = item['start']
        end = item['end']
        preview_times = np.linspace(start, end, pts)
        
        f_data = item.get('formant_data')
        if not f_data or 'xs' not in f_data or 'f1' not in f_data or 'f2' not in f_data:
            nan_list = [np.nan] * pts
            return preview_times, nan_list, nan_list
            
        xs = f_data['xs']
        f1_arr = f_data['f1']
        f2_arr = f_data['f2']
        
        if strategy == '中段均值':
            duration = end - start
            m_start = start + duration / 3.0
            m_end = start + 2.0 * duration / 3.0
            
            mask = (xs >= m_start) & (xs <= m_end)
            f1_slice = f1_arr[mask]
            f2_slice = f2_arr[mask]
            
            f1_vals = f1_slice[~np.isnan(f1_slice)]
            f2_vals = f2_slice[~np.isnan(f2_slice)]
            
            mean_f1 = np.nanmean(f1_vals) if len(f1_vals) > 0 else np.nan
            mean_f2 = np.nanmean(f2_vals) if len(f2_vals) > 0 else np.nan
            
            preview_f1 = [mean_f1] * pts
            preview_f2 = [mean_f2] * pts
        else:
            preview_f1 = []
            preview_f2 = []
            
            f1_valid_idx = np.where(~np.isnan(f1_arr))[0]
            f2_valid_idx = np.where(~np.isnan(f2_arr))[0]
            
            for t in preview_times:
                if len(f1_valid_idx) == 0 or t < xs[0] or t > xs[-1]:
                    preview_f1.append(np.nan)
                else:
                    nearest_idx = np.argmin(np.abs(xs[f1_valid_idx] - t))
                    if np.abs(xs[f1_valid_idx][nearest_idx] - t) > 0.04:
                        preview_f1.append(np.nan)
                    else:
                        preview_f1.append(float(np.interp(t, xs[f1_valid_idx], f1_arr[f1_valid_idx])))
                if len(f2_valid_idx) == 0 or t < xs[0] or t > xs[-1]:
                    preview_f2.append(np.nan)
                else:
                    nearest_idx = np.argmin(np.abs(xs[f2_valid_idx] - t))
                    if np.abs(xs[f2_valid_idx][nearest_idx] - t) > 0.04:
                        preview_f2.append(np.nan)
                    else:
                        preview_f2.append(float(np.interp(t, xs[f2_valid_idx], f2_arr[f2_valid_idx])))
                        
        return preview_times.tolist(), preview_f1, preview_f2


    def _export_vowel_space_chart(self, out_file, speakers_to_process, emit_error=True):
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        
        data_points = []
        for spk in speakers_to_process:
            for item_id, item in spk.items.items():
                if item.get('is_excluded', False):
                    continue
                self._ensure_item_loaded(item, speaker=spk)
                if not item.get('formant_data'):
                    continue
                    
                f_data = item['formant_data']
                f1_arr = f_data['f1']
                f2_arr = f_data['f2']
                
                t_s, t_e = item['start'], item['end']
                xs = f_data['xs']
                mask = (xs >= t_s) & (xs <= t_e)
                
                valid_f1 = f1_arr[mask][~np.isnan(f1_arr[mask])]
                valid_f2 = f2_arr[mask][~np.isnan(f2_arr[mask])]
                
                if len(valid_f1) > 0 and len(valid_f2) > 0:
                    med_f1 = np.nanmedian(valid_f1)
                    med_f2 = np.nanmedian(valid_f2)
                    
                    data_points.append({
                        'spk': spk.name,
                        'label': item.get('label', ''),
                        'f1': med_f1,
                        'f2': med_f2
                    })
                    
        if not data_points:
            if emit_error:
                self._emit(False, error="No valid formant data found")
            return False
            
        fig, ax = plt.subplots(figsize=(8, 7), dpi=300)
        
        unique_labels = sorted(list(set(pt['label'] for pt in data_points)))
        cmap = plt.get_cmap('tab10')
        colors = {lbl: cmap(i % 10) for i, lbl in enumerate(unique_labels)}
        
        for pt in data_points:
            ax.scatter(pt['f2'], pt['f1'], color=colors[pt['label']], s=60, alpha=0.8, edgecolors='none', zorder=4)
            ax.text(pt['f2'] - 15, pt['f1'] + 10, pt['label'], fontsize=9, fontweight='bold', color='#374151', zorder=5)
            
        if len(unique_labels) >= 3:
            mean_points = {}
            for lbl in unique_labels:
                lbl_pts = [pt for pt in data_points if pt['label'] == lbl]
                mean_f1 = np.mean([pt['f1'] for pt in lbl_pts])
                mean_f2 = np.mean([pt['f2'] for pt in lbl_pts])
                mean_points[lbl] = (mean_f2, mean_f1)
            
            for lbl, (m_f2, m_f1) in mean_points.items():
                ax.scatter(m_f2, m_f1, color=colors[lbl], s=150, marker='*', edgecolors='black', linewidth=1, zorder=6, label=f"{lbl} 均值")
        
        ax.invert_xaxis()
        ax.invert_yaxis()
        
        ax.set_xlabel("F2 (Hz)  [← Front / Back →]", fontsize=12, fontweight='bold', labelpad=10)
        ax.set_ylabel("F1 (Hz)  [← High / Low →]", fontsize=12, fontweight='bold', labelpad=10)
        
        spk_str = speakers_to_process[0].name if len(speakers_to_process) == 1 else "多发音人"
        ax.set_title(f"Vowel Space Chart ({spk_str}) - PhonTracer", fontsize=14, fontweight='bold', pad=15)
        
        ax.grid(True, linestyle='--', alpha=0.5, zorder=1)
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        ax.spines['left'].set_color('#9CA3AF')
        ax.spines['bottom'].set_color('#9CA3AF')
        
        plt.tight_layout()
        os.makedirs(os.path.dirname(os.path.abspath(out_file)) or ".", exist_ok=True)
        fig.savefig(out_file, bbox_inches='tight')
        plt.close(fig)
        return True


    def _export_textgrid(self, out_path, structure, speaker=None):
        speaker = speaker or self.current_speaker
        import textgrid
        import os

        flat_items = []
        for grp_name, children in structure:
            for child in children:
                if speaker.items[child].get('start') is not None and speaker.items[child].get('end') is not None:
                    flat_items.append(speaker.items[child])

        if not flat_items:
            self._emit(False, error="No valid items to export")
            return

        # Simple heuristic: if we have paths, it might be batch mode, but CLI might not have "long audio" vs "batch" mode
        # If output is a directory or ends in a slash, treat as batch export
        if os.path.isdir(out_path) or not out_path.endswith('.TextGrid'):
            out_subdir = os.path.join(out_path, "Textgrid_export")
            os.makedirs(out_subdir, exist_ok=True)

            path_to_items = {}
            for item in flat_items:
                path = item.get('path', 'unknown')
                if path not in path_to_items:
                    path_to_items[path] = []
                path_to_items[path].append(item)

            used_stems = {}
            for path, items in path_to_items.items():
                base_name = make_textgrid_export_stem(path, items[0].get('label', '') if items else '')
                stem_count = used_stems.get(base_name, 0) + 1
                used_stems[base_name] = stem_count
                if stem_count > 1:
                    base_name = f"{base_name}_{stem_count}"
                tg_path = os.path.join(out_subdir, f"{base_name}.TextGrid")
                self._write_textgrid(tg_path, items)

            self._emit(True, message=f"Exported batch TextGrids to {out_subdir}")
        else:
            self._write_textgrid(out_path, flat_items)
            self._emit(True, message=f"Exported TextGrid to {out_path}")


    def _write_textgrid(self, tg_path, items):
        import textgrid
        import numpy as np
        max_time = 0
        for item in items:
            if item.get('snd'):
                dur = item['snd'].get_total_duration()
                if dur > max_time: max_time = dur
            elif item['end'] > max_time:
                max_time = item['end']

        if max_time == 0: max_time = 1.0
        tg = textgrid.TextGrid(maxTime=max_time)
        word_tier = textgrid.IntervalTier(name="words", minTime=0.0, maxTime=max_time)
        char_tier = textgrid.IntervalTier(name="chars", minTime=0.0, maxTime=max_time)
        group_tier = textgrid.IntervalTier(name="groups", minTime=0.0, maxTime=max_time)

        items.sort(key=lambda x: x.get('start', 0))

        last_word_end = 0.0
        last_char_end = 0.0
        last_group_end = 0.0

        for item in items:
            t_s, t_e = item['start'], item['end']
            label = item.get('label', '')
            inner_splits = item.get('inner_splits', [])
            grp_name = item.get('group', '导入内容')

            if t_s > last_word_end:
                word_tier.add(last_word_end, t_s, "")
            word_tier.add(t_s, t_e, label)
            last_word_end = t_e

            if t_s > last_group_end:
                group_tier.add(last_group_end, t_s, "")
            group_tier.add(t_s, t_e, grp_name)
            last_group_end = t_e

            syls = split_into_syllables(label)
            if len(syls) > 1:
                if t_s > last_char_end:
                    char_tier.add(last_char_end, t_s, "")

                chars_bounds = item.get('chars_bounds', [])
                if not chars_bounds:
                    splits = [t_s] + [s for s in inner_splits if t_s < s < t_e] + [t_e]
                    if len(splits) != len(syls) + 1:
                        splits = np.linspace(t_s, t_e, len(syls) + 1).tolist()
                    chars_bounds = [(splits[j], splits[j+1]) for j in range(len(splits)-1)]

                local_last = t_s
                for i in range(len(syls)):
                    if i < len(chars_bounds):
                        c_s, c_e = chars_bounds[i]
                        if c_s > local_last:
                            char_tier.add(local_last, c_s, "")
                        char_tier.add(c_s, c_e, syls[i])
                        local_last = c_e
                if local_last < t_e:
                    char_tier.add(local_last, t_e, "")
                last_char_end = t_e
            else:
                if t_s > last_char_end:
                    char_tier.add(last_char_end, t_s, "")
                char_tier.add(t_s, t_e, label)
                last_char_end = t_e

        if max_time > last_word_end:
            word_tier.add(last_word_end, max_time, "")
        if max_time > last_char_end:
            char_tier.add(last_char_end, max_time, "")
        if max_time > last_group_end:
            group_tier.add(last_group_end, max_time, "")

        tg.append(group_tier)
        tg.append(word_tier)
        tg.append(char_tier)

        tg.write(tg_path)

    def _export_collection(self, out_path, structure, speaker=None, binary=True):
        speaker = speaker or self.current_speaker
        from modules.export_service import export_speaker_to_praat_collection

        if not structure or not any(children for _, children in structure):
            structure = [
                (grp, [iid for iid, item in speaker.items.items() if item.get("group", "默认组") == grp and not item.get("is_excluded", False)])
                for grp in dict.fromkeys(it.get("group", "默认组") for it in speaker.items.values())
            ]

        is_batch = ("多条" in str(getattr(speaker, "tab_mode", "")))
        long_snd = getattr(speaker, "pending_long_snd", None)
        default_ceiling = float(speaker.last_params.get("pitch_ceiling", 600.0)) if getattr(speaker, "last_params", None) else 600.0
        export_speaker_to_praat_collection(
            out_path=out_path,
            items_dict=speaker.items,
            tree_structure=structure,
            is_batch_mode=is_batch,
            long_snd=long_snd,
            speaker_name=getattr(speaker, "name", ""),
            ensure_loaded_fn=lambda it: self._ensure_item_loaded(it, speaker=speaker),
            default_ceiling=default_ceiling,
            binary=binary,
        )

    def _export_collection_integrated(self, out_path, speakers, binary=True):
        from modules.export_service import build_speaker_collection_objects, write_praat_collection

        all_objects = []
        for s in speakers:
            s_groups = getattr(s, "cli_groups", None) or list(dict.fromkeys(it.get("group", "默认组") for it in s.items.values()))
            s_struct = [
                (grp, [iid for iid, item in s.items.items() if item.get("group", "默认组") == grp and not item.get("is_excluded", False)])
                for grp in s_groups
            ]
            is_batch = ("多条" in str(getattr(s, "tab_mode", "")))
            long_snd = getattr(s, "pending_long_snd", None)
            default_ceiling = float(s.last_params.get("pitch_ceiling", 600.0)) if getattr(s, "last_params", None) else 600.0
            objs = build_speaker_collection_objects(
                items_dict=s.items,
                tree_structure=s_struct,
                is_batch_mode=is_batch,
                long_snd=long_snd,
                speaker_name=getattr(s, "name", ""),
                ensure_loaded_fn=lambda it, spk=s: self._ensure_item_loaded(it, speaker=spk),
                default_ceiling=default_ceiling,
            )
            all_objects.extend(objs)

        write_praat_collection(out_path, all_objects, binary=binary)


__all__ = ["CLIExportMixin"]
