# -*- coding: utf-8 -*-
"""
Excel research archive writer and comprehensive multi-sheet project exporter.
Refactored from modules/report_generator.py (L1789-L2426).
"""
import hashlib
import io
import json
import os
import time
import zipfile
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
import xlsxwriter

from ..core.text_io import split_into_syllables
from ..data_utils import make_safe_excel_sheet_name
from ..storage.archive_security import validate_project_archive_members
from ..project_manager import read_project_metadata_from_archive
from .item_inspector import (
    SPLIT_WARNING_ZH,
    analyze_item_anomalies,
    calculate_sha256,
    extract_project_wav_params,
    format_wav_params,
    generate_natural_language_summary,
    get_consensus_and_deviations,
    get_item_override_param,
    get_majority_item_params,
    get_pitch_ceiling,
    get_pitch_floor,
    get_voicing_threshold,
    load_item_cache_if_any,
)
from .markdown_builder import generate_markdown_report

__all__ = [
    "write_excel_archive",
    "export_reports_from_teproj",
]

def write_excel_archive(teproj_path: str, state: Dict[str, Any], output_xlsx_path: str, include_cache_details: bool = False):
    import xlsxwriter

    # Start preparing metadata and statistics
    speakers = state.get("speakers", {})
    total_items = 0
    manually_adjusted = 0

    overview_rows = []
    speaker_params_rows = []
    item_detail_rows = []
    word_boundary_rows = []
    char_boundary_rows = []
    param_exception_rows = []
    audit_warning_rows = []
    excluded_rows = []
    resource_rows = []
    f0_cache_rows = []
    formant_cache_rows = []
    group_counts = {}
    group_included_counts = {}
    analysis_mode_counts = {}
    file_hash = calculate_sha256(teproj_path)

    custom_script_runs = state.get("custom_script_runs", [])
    script_records = []
    script_sources = []
    for run in custom_script_runs:
        outputs_str = ", ".join(out.get("filename", "") for out in run.get("outputs", []))
        script_records.append([
            run.get("script_name", "未命名"),
            run.get("script_type", "chart"),
            run.get("used_at", "未知"),
            run.get("user_goal", "未说明"),
            run.get("code_sha256", "无"),
            run.get("status", "成功"),
            outputs_str,
            run.get("software_version", "未知"),
            run.get("api_version", "1")
        ])
        script_sources.append([
            run.get("script_name", "未命名"),
            run.get("code_sha256", "无"),
            run.get("code", "")
        ])

    # Track cache status
    cache_statuses = {}

    def numeric_values_differ(left: Any, right: Any, tolerance: float = 1e-9) -> bool:
        try:
            return abs(float(left) - float(right)) > tolerance
        except (TypeError, ValueError):
            return left != right

    with zipfile.ZipFile(teproj_path, "r") as z:
        validate_project_archive_members(z)
        save_time = state.get("save_time")
        if not save_time or save_time == "未记录":
            try:
                info = z.getinfo("project.json")
                dt = info.date_time
                save_time_str = f"{dt[0]:04d}-{dt[1]:02d}-{dt[2]:02d} {dt[3]:02d}:{dt[4]:02d}:{dt[5]:02d} (基于文件修改时间推断)"
            except Exception:
                save_time_str = "未记录"
        else:
            save_time_str = save_time

        wav_params = extract_project_wav_params(z)
        consensus, deviations = get_consensus_and_deviations(wav_params)
        consensus_str = format_wav_params(consensus[0], consensus[1], consensus[2]) if consensus[0] is not None else "无音频文件"

        for spk_id, spk in speakers.items():
            spk_name = spk.get("name", "发音人")
            items = spk.get("items", {})
            total_items += len(items)

            # Speaker params
            params = spk.get("last_params", {})
            p_floor = get_pitch_floor(params)
            p_ceiling = get_pitch_ceiling(params)
            v_thresh = get_voicing_threshold(params)
            pts = params.get("pts", 11)
            mode = params.get("analysis_mode", "f0")

            # Formant defaults
            f_max = params.get("formant_max_hz", 5500.0)
            f_count = params.get("formant_count", 5)
            f_win = params.get("formant_window_length", 0.025)
            f_pre = params.get("formant_pre_emphasis", 50.0)
            f_strat = params.get("formant_sample_strategy", "整段11点")
            majority_params = get_majority_item_params(items, params)

            speaker_params_rows.append([
                spk_name, mode, pts, p_floor, p_ceiling, v_thresh,
                params.get("db", 60.0), params.get("skip_front", 0.0),
                f_max, f_count, f_win, f_pre, f_strat
            ])

            for item_id, item in items.items():
                label = item.get("label", "无")
                group = item.get("group", "默认组")
                group_counts[group] = group_counts.get(group, 0) + 1

                is_excluded = item.get("is_excluded", False)
                item_mode = item.get("analysis_mode") or mode
                if not is_excluded:
                    group_included_counts[group] = group_included_counts.get(group, 0) + 1
                    analysis_mode_counts[item_mode] = analysis_mode_counts.get(item_mode, 0) + 1
                if is_excluded:
                    b_str = f"{item.get('start', 0.0):.3f} - {item.get('end', 0.0):.3f}" if item.get('start') is not None else "-"
                    excluded_rows.append([
                        spk_name, group, item_id, label, b_str,
                        item.get("exclusion_reason", "未说明原因") or "未说明原因",
                        item.get("excluded_at", "未知时间") or "未知时间"
                    ])

                # 使用纳入分析条目的多数值比较，不使用界面当前值。
                majority_floor = majority_params["pitch_floor"]
                majority_ceiling = majority_params["pitch_ceiling"]
                majority_thresh = majority_params["voicing_threshold"]
                majority_f_max = majority_params["formant_max_hz"]
                majority_f_count = majority_params["formant_count"]
                majority_f_win = majority_params["formant_window_length"]
                majority_f_pre = majority_params["formant_pre_emphasis"]
                majority_f_strat = majority_params["formant_sample_strategy"]
                item_floor = get_pitch_floor(item, majority_floor)
                item_ceiling = get_pitch_ceiling(item, majority_ceiling)
                item_thresh = get_voicing_threshold(item, majority_thresh)
                item_f_max = get_item_override_param(item, "formant_max_hz", majority_f_max)
                item_f_count = get_item_override_param(item, "formant_count", majority_f_count)
                item_f_win = get_item_override_param(item, "formant_window_length", majority_f_win)
                item_f_pre = get_item_override_param(item, "formant_pre_emphasis", majority_f_pre)
                item_f_strat = get_item_override_param(item, "formant_sample_strategy", majority_f_strat)

                diffs = []
                if item_floor != majority_floor: diffs.append(f"基频下限: {item_floor:.0f}Hz (多数值: {majority_floor:.0f}Hz)")
                if item_ceiling != majority_ceiling: diffs.append(f"基频上限: {item_ceiling:.0f}Hz (多数值: {majority_ceiling:.0f}Hz)")
                if item_thresh != majority_thresh: diffs.append(f"清浊阈值: {item_thresh:.2f} (多数值: {majority_thresh:.2f})")
                if item_f_max != majority_f_max: diffs.append(f"共振峰分析上限: {item_f_max:.0f}Hz (多数值: {majority_f_max:.0f}Hz)")
                if item_f_count != majority_f_count: diffs.append(f"共振峰追踪个数: {item_f_count} (多数值: {majority_f_count})")
                if item_f_win != majority_f_win: diffs.append(f"共振峰窗长: {item_f_win:.3f}s (多数值: {majority_f_win:.3f}s)")
                if item_f_pre != majority_f_pre: diffs.append(f"共振峰预加重: {item_f_pre:.1f}Hz (多数值: {majority_f_pre:.1f}Hz)")
                if item_f_strat != majority_f_strat: diffs.append(f"共振峰采样策略: {item_f_strat} (多数值: {majority_f_strat})")

                if diffs and not is_excluded:
                    param_exception_rows.append([spk_name, group, item_id, label, ", ".join(diffs)])

                item_meta = item.get("item_meta") or item.get("meta") or {}
                item_meta_text = "；".join([f"{k}={v}" for k, v in item_meta.items()]) if isinstance(item_meta, dict) else str(item_meta)
                item_detail_rows.append([spk_name, group, item_id, label, item.get("path", "无"), item_mode,
                                         "是" if is_excluded else "否",
                                         item_floor if item_floor != majority_floor else "同多数",
                                         item_ceiling if item_ceiling != majority_ceiling else "同多数",
                                         item_thresh if item_thresh != majority_thresh else "同多数",
                                         "已排除" if is_excluded else ("局部参数偏离" if diffs else "同发音人多数值"),
                                         item.get("wordlist_version", "v1"),
                                         item.get("wordlist_title", ""),
                                         item.get("group_note", ""),
                                         "；".join(str(v) for v in (item.get("group_tags", []) or [])),
                                         item.get("item_note") or item.get("note", ""),
                                         "；".join(str(v) for v in (item.get("item_tags") or item.get("tags") or [])),
                                         "；".join(str(v) for v in (item.get("item_aliases") or item.get("aliases") or [])),
                                         item.get("metadata_source", ""),
                                         item_meta_text])

                macro_start = item.get("macro_start", 0.0)
                macro_end = item.get("macro_end", 0.0)
                raw_start = item.get("raw_start", 0.0)
                raw_end = item.get("raw_end", 0.0)
                sample_start = item.get("start", 0.0)
                sample_end = item.get("end", 0.0)
                boundary_flags = []
                if item.get("is_manual_edited"):
                    boundary_flags.append("人工复核调整")
                if "raw_start" not in item or "raw_end" not in item or item.get("raw_start") is None or item.get("raw_end") is None:
                    boundary_flags.append("原始边界未记录")
                elif numeric_values_differ(sample_start, raw_start) or numeric_values_differ(sample_end, raw_end):
                    boundary_flags.append("最终边界与原始边界不同")
                if item.get("inner_splits"):
                    boundary_flags.append("含词内切分点")
                word_boundary_rows.append([spk_name, group, item_id, label, "是" if is_excluded else "否", macro_start, macro_end,
                                           raw_start, raw_end, sample_start, sample_end,
                                           (sample_end - sample_start) if sample_start is not None and sample_end is not None else 0.0,
                                           "；".join(boundary_flags) if boundary_flags else "未标记特殊状态"])

                syls = split_into_syllables(label)
                chars_bounds = item.get("chars_bounds", [])
                inner_splits = item.get("inner_splits", [])
                for idx_syl, syl in enumerate(syls):
                    c_s, c_e = chars_bounds[idx_syl] if idx_syl < len(chars_bounds) else (0.0, 0.0)
                    split_pt = inner_splits[idx_syl - 1] if (idx_syl > 0 and idx_syl - 1 < len(inner_splits)) else ""
                    char_status = "字级边界缺失" if c_s == 0.0 and c_e == 0.0 else ("含词内切分点" if split_pt != "" else "正常")
                    char_boundary_rows.append([spk_name, group, item_id, label, "是" if is_excluded else "否", idx_syl + 1, syl, c_s, c_e, split_pt, char_status])

                pitch_data, formant_data, p_status, f_status = load_item_cache_if_any(item, z, spk=spk)
                p_file = item.get("pitch_data_file")
                if p_file: cache_statuses[p_file.replace("\\", "/")] = p_status
                f_file = item.get("formant_data_file")
                if f_file: cache_statuses[f_file.replace("\\", "/")] = f_status

                item_warnings = analyze_item_anomalies(item, pitch_data, formant_data, params, p_status, f_status)
                if not is_excluded and item.get("is_manual_edited"): manually_adjusted += 1
                mapped_split_warnings = [SPLIT_WARNING_ZH.get(w, w) for w in item.get("split_warnings", [])]
                split_warn_str = "；".join(mapped_split_warnings)
                audit_warning_rows.append([spk_name, group, item_id, label, "是" if is_excluded else "否", "是" if item.get("is_manual_edited") else "否",
                                           item.get("split_confidence", 1.0), split_warn_str,
                                           "；".join(item_warnings) if item_warnings else "合格无警告"])

                if include_cache_details:
                    if pitch_data:
                        for x_val, f_val in zip(pitch_data['xs'], pitch_data['freqs']):
                            f0_cache_rows.append([spk_name, group, item_id, label, x_val, f_val, "是" if item_mode == "f0" else "否"])
                    if formant_data:
                        f1_arr = formant_data.get('f1', [])
                        f2_arr = formant_data.get('f2', [])
                        f3_arr = formant_data.get('f3')
                        for idx_f, (x_val, f1, f2) in enumerate(zip(formant_data['xs'], f1_arr, f2_arr)):
                            f3 = f3_arr[idx_f] if f3_arr is not None and idx_f < len(f3_arr) else np.nan
                            formant_cache_rows.append([
                                spk_name, group, item_id, label, x_val,
                                f1 if not np.isnan(f1) else "",
                                f2 if not np.isnan(f2) else "",
                                f3 if not np.isnan(f3) else "",
                                "是" if item_mode == "formant" else "否"
                            ])

        # Resource listing
        for member in z.infolist():
            normalized_name = member.filename.replace("\\", "/")
            res_type = "其他"
            if normalized_name.startswith("audio/"): res_type = "音频物理文件"
            elif normalized_name.startswith("data/"): res_type = "共振峰数据缓存" if "formant" in normalized_name else "基频(F0)数据缓存"
            elif normalized_name == "project.json": res_type = "工程元数据配置文件"

            dig_param_str = format_wav_params(*wav_params[normalized_name]) if normalized_name.startswith("audio/") and normalized_name in wav_params else "-"
            resource_status = cache_statuses.get(normalized_name, "正常")
            if normalized_name in wav_params and wav_params[normalized_name] != consensus:
                resource_status = "录音参数偏离（相对多数）"
            resource_rows.append([res_type, normalized_name, member.file_size, dig_param_str, resource_status])

    # Build Overview
    sw_ver = state.get("software_version")
    sw_ver_str = sw_ver if sw_ver and sw_ver != "1.2.0 (或更早版本，未记录)" else "未记录 (推断为早于 1.2.0)"

    sheet_descriptions = {
        "工程概览": "工程级元数据与归档校验信息。异常计数大于 0 时会高亮。",
        "发音人参数": "发音人默认分析参数。条目级覆盖值请查看“条目明细”和“局部参数差异”。",
        "条目明细": "每条记录的路径、模式和条目级参数状态。橙色表示参数偏离发音人多数值。",
        "词级边界": "保留宏范围、自动检测原始边界和最终采用边界。黄色表示最终边界与原始边界不同。",
        "字级边界": "多音节词的字级边界及词内切分点。黄色表示存在切分点，红色表示边界缺失。",
        "局部参数差异": "集中列出纳入分析条目中的局部参数偏离，便于方法透明性审计。",
        "质量控制与复核记录": "集中列出人工修改、切分置信度、声学复核提示与声学复核标记。",
        "排除与忽略条目": "保留已排除条目的原因和时间，确保清洗过程可追溯。",
        "资源清单": "归档内资源列表及校验状态。红色表示缓存损坏或录音参数偏离。",
        "字段说明": "工作表字段的用途和解释。",
        "F0缓存明细": "可选的 F0 缓存平铺数据。0 值或负值会高亮。",
        "共振峰缓存明细": "可选的共振峰缓存平铺数据。缺失值会高亮。",
        "自定义脚本记录": "本工程实际使用过的自定义脚本历史记录快照。",
        "自定义脚本源码": "已归档自定义脚本的完整 Python 源代码，便于复现与审计。",
    }

    width_overrides = {
        "路径": 42, "差异详情": 62, "声学复核提示": 32, "声学复核标记": 68, "排除原因": 28,
        "排除时间": 20, "数字化参数": 28, "校验状态": 24, "定义": 54, "概览指标": 28,
        "指标取值": 68, "参数状态": 20, "边界状态": 36, "切分状态": 18, "类型": 20,
        "条目ID": 20, "词项": 18, "发音人": 16, "组别": 18, "音节序号": 12,
        "组标签": 20, "词项标签": 20, "自动检测起点(s)": 20, "自动检测终点(s)": 20,
        "运行时间": 20, "用户需求": 42, "完整源码": 80, "代码 SHA-256": 42, "输出文件": 28,
    }

    trim_silence_str = "是" if state.get("trim_silence") else "否"
    export_rule_val = state.get("export_numbering_rule")
    export_rule_excel = "全部连续" if export_rule_val == "continuous" else ("按分组重新标号" if export_rule_val in ("per_group", "by_group") else "未记录")

    excluded_count = len(excluded_rows)
    included_count = total_items - excluded_count
    warning_item_count = sum(1 for row in audit_warning_rows if row[7] != "")
    review_item_count = sum(1 for row in audit_warning_rows if row[8] != "合格无警告")
    resource_issue_count = sum(1 for row in resource_rows if row[4] != "正常")
    group_summary_rows = [
        [group, group_included_counts.get(group, 0), count - group_included_counts.get(group, 0)]
        for group, count in sorted(group_counts.items())
    ]
    mode_summary = "、".join(f"{mode}: {count} 条" for mode, count in sorted(analysis_mode_counts.items())) or "无纳入分析条目"

    overview_rows.extend([["工程格式版本", state.get("version", "1.0")], ["PhonTracer 软件版本", sw_ver_str], ["工程最后保存时间", save_time_str],
                          ["归档文件 SHA-256 校验码", file_hash], ["发音人数量", len(speakers)], ["条目总数", total_items],
                          ["已忽略/排除条目数", excluded_count], ["实际分析条目数", included_count],
                          ["人工微调条目数", manually_adjusted],
                          ["局部参数偏离条目数", len(param_exception_rows)], ["自动切分提示条目数", warning_item_count],
                          ["含声学复核标记条目数", review_item_count], ["资源校验异常数", resource_issue_count],
                          ["录音数字化参数偏离文件数", len(deviations)], ["纳入分析模式分布", mode_summary],
                          ["边缘静音裁切是否启用", trim_silence_str], ["词内标号策略", export_rule_excel], ["总体录音数字化参数", consensus_str]])

    wb = xlsxwriter.Workbook(output_xlsx_path, {'strings_to_formulas': False})
    wb.set_properties({
        "title": "PhonTracer 实验方法与数据审计归档",
        "subject": "声学实验方法、边界状态与质量风险审计",
        "author": "PhonTracer",
        "comments": "由 toolkit 实验方法导出功能生成",
    })

    font_name = "Microsoft YaHei"
    title_fmt = wb.add_format({'bold': True, 'font_name': font_name, 'font_size': 15, 'font_color': 'white', 'bg_color': '#1E3A8A', 'align': 'left', 'valign': 'vcenter'})
    subtitle_fmt = wb.add_format({'font_name': font_name, 'font_size': 9, 'font_color': '#475569', 'bg_color': '#F8FAFC', 'align': 'left', 'valign': 'vcenter'})
    empty_fmt = wb.add_format({'font_name': font_name, 'font_size': 10, 'italic': True, 'font_color': '#64748B', 'bg_color': '#F8FAFC', 'align': 'left', 'border': 1, 'border_color': '#CBD5E1'})
    red_fmt = wb.add_format({'font_name': font_name, 'font_size': 10, 'bg_color': '#FEE2E2', 'font_color': '#991B1B', 'border': 1, 'border_color': '#CBD5E1'})
    orange_fmt = wb.add_format({'font_name': font_name, 'font_size': 10, 'bg_color': '#FFEDD5', 'font_color': '#9A3412', 'border': 1, 'border_color': '#CBD5E1'})
    yellow_fmt = wb.add_format({'font_name': font_name, 'font_size': 10, 'bg_color': '#FEF3C7', 'font_color': '#92400E', 'border': 1, 'border_color': '#CBD5E1'})
    green_fmt = wb.add_format({'font_name': font_name, 'font_size': 10, 'bg_color': '#DCFCE7', 'font_color': '#166534', 'border': 1, 'border_color': '#CBD5E1'})
    section_fmt = wb.add_format({'bold': True, 'font_name': font_name, 'font_size': 11, 'font_color': 'white', 'bg_color': '#2563EB', 'align': 'left', 'valign': 'vcenter'})
    summary_title_fmt = wb.add_format({'bold': True, 'font_name': font_name, 'font_size': 18, 'font_color': 'white', 'bg_color': '#1E3A8A', 'align': 'left', 'valign': 'vcenter'})
    summary_subtitle_fmt = wb.add_format({'font_name': font_name, 'font_size': 10, 'font_color': '#334155', 'bg_color': '#DBEAFE', 'align': 'left', 'valign': 'vcenter'})
    summary_text_fmt = wb.add_format({'font_name': font_name, 'font_size': 10, 'font_color': '#1E293B', 'bg_color': '#EFF6FF', 'border': 1, 'border_color': '#BFDBFE', 'text_wrap': True, 'valign': 'top'})
    label_fmt = wb.add_format({'bold': True, 'font_name': font_name, 'font_size': 10, 'font_color': '#334155', 'bg_color': '#F1F5F9', 'border': 1, 'border_color': '#CBD5E1'})
    value_fmt = wb.add_format({'font_name': font_name, 'font_size': 10, 'font_color': '#1E293B', 'bg_color': 'white', 'border': 1, 'border_color': '#CBD5E1'})
    link_fmt = wb.add_format({'font_name': font_name, 'font_size': 10, 'font_color': '#2563EB', 'underline': True, 'bg_color': 'white', 'border': 1, 'border_color': '#CBD5E1'})
    card_label_fmt = wb.add_format({'bold': True, 'font_name': font_name, 'font_size': 9, 'font_color': '#475569', 'bg_color': '#F8FAFC', 'border': 1, 'border_color': '#CBD5E1', 'align': 'center', 'valign': 'vcenter'})
    card_value_fmt = wb.add_format({'bold': True, 'font_name': font_name, 'font_size': 16, 'font_color': '#1E3A8A', 'bg_color': 'white', 'border': 1, 'border_color': '#CBD5E1', 'align': 'center', 'valign': 'vcenter'})
    time_fmt = wb.add_format({'num_format': '0.000', 'font_name': font_name, 'font_size': 10, 'border': 1, 'border_color': '#CBD5E1'})
    decimal_fmt = wb.add_format({'num_format': '0.00', 'font_name': font_name, 'font_size': 10, 'border': 1, 'border_color': '#CBD5E1'})
    integer_fmt = wb.add_format({'num_format': '0', 'font_name': font_name, 'font_size': 10, 'border': 1, 'border_color': '#CBD5E1'})
    size_fmt = wb.add_format({'num_format': '#,##0', 'font_name': font_name, 'font_size': 10, 'border': 1, 'border_color': '#CBD5E1'})
    code_fmt = wb.add_format({'font_name': 'Consolas', 'font_size': 9, 'text_wrap': True, 'valign': 'top', 'border': 1, 'border_color': '#CBD5E1'})
    default_data_fmt = wb.add_format({'font_name': font_name, 'font_size': 10, 'border': 1, 'border_color': '#CBD5E1'})
    header_fmt = wb.add_format({'bold': True, 'font_name': font_name, 'font_size': 10, 'font_color': 'white', 'bg_color': '#2563EB', 'border': 1, 'border_color': '#DBEAFE', 'align': 'center', 'valign': 'vcenter'})

    # sheet_descriptions and width_overrides are defined above with script sheets support

    def column_format_for_header(header: str):
        if any(token in header for token in ("起点", "终点", "时长", "切分点", "时间点", "窗长")):
            return time_fmt
        if any(token in header for token in ("阈值", "置信度")):
            return decimal_fmt
        if header in ("F0下限", "F0上限", "共振峰分析上限", "追踪个数", "大小", "基频F0(Hz)", "F1共振峰(Hz)", "F2共振峰(Hz)", "F3共振峰(Hz)", "音节序号"):
            return size_fmt if header == "大小" else integer_fmt
        if header == "完整源码":
            return code_fmt
        return default_data_fmt

    def visual_len(text: Any) -> int:
        if text is None:
            return 0
        s = str(text)
        length = 0
        for ch in s:
            if ord(ch) > 127:
                length += 2
            else:
                length += 1
        return length

    def column_width(header: str, rows: List[List[Any]], col_idx: int) -> int:
        if header in width_overrides:
            return width_overrides[header]
        longest = visual_len(header) + 5
        for row in rows[:300]:
            if col_idx < len(row):
                longest = max(longest, visual_len(row[col_idx]) + 2)
        return min(max(longest, 12), 48)

    def add_formula_highlight(ws, last_row: int, first_col: int, last_col: int, formula: str, fmt, stop_if_true: bool = False):
        if last_row >= 3:
            ws.conditional_format(3, first_col, last_row, last_col, {
                'type': 'formula',
                'criteria': formula,
                'format': fmt,
                'stop_if_true': stop_if_true,
            })

    def apply_sheet_highlights(ws, category: str, headers: List[str], last_row: int):
        if last_row < 3:
            return
        index = {header: idx for idx, header in enumerate(headers)}
        last_col = len(headers) - 1
        if "是否排除" in index:
            col = xlsxwriter.utility.xl_col_to_name(index["是否排除"])
            add_formula_highlight(ws, last_row, 0, last_col, f'=${col}4="是"', red_fmt, True)
        if category == "工程概览":
            add_formula_highlight(ws, last_row, 0, last_col, '=AND(ISNUMBER($B4),OR($A4="已忽略/排除条目数",$A4="人工微调条目数",$A4="局部参数偏离条目数",$A4="自动切分提示条目数",$A4="含声学复核标记条目数",$A4="资源校验异常数",$A4="录音数字化参数偏离文件数"),$B4>0)', orange_fmt)
        elif category == "条目明细":
            for header in ("定制下限", "定制上限", "定制阈值"):
                col_idx = index[header]
                col = xlsxwriter.utility.xl_col_to_name(col_idx)
                add_formula_highlight(ws, last_row, col_idx, col_idx, f'=${col}4<>"同多数"', orange_fmt)
        elif category == "词级边界":
            for header, baseline in (("采样起点", "自动检测起点(s)"), ("采样终点", "自动检测终点(s)")):
                col_idx = index[header]
                col = xlsxwriter.utility.xl_col_to_name(col_idx)
                base_col = xlsxwriter.utility.xl_col_to_name(index[baseline])
                add_formula_highlight(ws, last_row, col_idx, col_idx, f'=ABS(${col}4-${base_col}4)>0.000001', yellow_fmt)
            status_col = xlsxwriter.utility.xl_col_to_name(index["边界状态"])
            add_formula_highlight(ws, last_row, index["边界状态"], index["边界状态"], f'=${status_col}4<>"未标记特殊状态"', yellow_fmt)
        elif category == "字级边界":
            split_col = xlsxwriter.utility.xl_col_to_name(index["切分点"])
            status_col = xlsxwriter.utility.xl_col_to_name(index["切分状态"])
            add_formula_highlight(ws, last_row, index["切分点"], index["切分点"], f'=${split_col}4<>""', yellow_fmt)
            add_formula_highlight(ws, last_row, index["切分状态"], index["切分状态"], f'=${status_col}4="字级边界缺失"', red_fmt)
        elif category == "局部参数差异":
            pass  # 移除全表涂橙，保持清爽可读
        elif category == "质量控制与复核记录":
            manual_col = xlsxwriter.utility.xl_col_to_name(index["人工修改"])
            confidence_col = xlsxwriter.utility.xl_col_to_name(index["置信度"])
            warning_col = xlsxwriter.utility.xl_col_to_name(index["声学复核提示"])
            fatal_col = xlsxwriter.utility.xl_col_to_name(index["声学复核标记"])
            add_formula_highlight(ws, last_row, index["人工修改"], index["人工修改"], f'=${manual_col}4="是"', yellow_fmt)
            add_formula_highlight(ws, last_row, index["置信度"], index["置信度"], f'=${confidence_col}4<0.6', orange_fmt)
            add_formula_highlight(ws, last_row, index["声学复核提示"], index["声学复核提示"], f'=${warning_col}4<>""', yellow_fmt)
            add_formula_highlight(ws, last_row, index["声学复核标记"], index["声学复核标记"], f'=OR(ISNUMBER(SEARCH("[错误]",${fatal_col}4)),ISNUMBER(SEARCH("[致命]",${fatal_col}4)))', red_fmt, True)
            add_formula_highlight(ws, last_row, index["声学复核标记"], index["声学复核标记"], f'=${fatal_col}4<>"合格无警告"', orange_fmt)
        elif category == "排除与忽略条目":
            pass  # 移除全表涂红，保持清爽可读
        elif category == "资源清单":
            status_col = xlsxwriter.utility.xl_col_to_name(index["校验状态"])
            add_formula_highlight(ws, last_row, index["校验状态"], index["校验状态"], f'=${status_col}4<>"正常"', red_fmt)
        elif category == "F0缓存明细":
            value_col = xlsxwriter.utility.xl_col_to_name(index["基频F0(Hz)"])
            add_formula_highlight(ws, last_row, index["基频F0(Hz)"], index["基频F0(Hz)"], f'=${value_col}4<=0', red_fmt)
        elif category == "共振峰缓存明细":
            for header in ("F1共振峰(Hz)", "F2共振峰(Hz)"):
                value_col = xlsxwriter.utility.xl_col_to_name(index[header])
                add_formula_highlight(ws, last_row, index[header], index[header], f'=${value_col}4=""', red_fmt)

    from ..data_utils import make_safe_excel_sheet_name
    used_sheet_names = {"论文方法摘要"}

    # Helper to write sheets
    def write_sheet(name, headers, rows, category=None, empty_message="当前无记录"):
        category = category or name
        safe_sheet_name = make_safe_excel_sheet_name(name, used_names=used_sheet_names)
        ws = wb.add_worksheet(safe_sheet_name)
        last_col = len(headers) - 1
        last_row = len(rows) + 2
        ws.hide_gridlines(2)
        ws.set_tab_color('#60A5FA')
        ws.merge_range(0, 0, 0, last_col, f"{name} 明细数据", title_fmt)
        ws.merge_range(1, 0, 1, last_col, sheet_descriptions.get(category, "实验方法归档明细。"), subtitle_fmt)
        ws.set_row(0, 26)
        ws.set_row(1, 20)
        ws.set_row(2, 24)
        ws.freeze_panes(3, 0)
        for row_idx, r in enumerate(rows):
            ws.write_row(row_idx + 3, 0, r)
        if rows:
            ws.add_table(2, 0, last_row, last_col, {
                'style': 'Table Style Medium 2',
                'columns': [{'header': header} for header in headers],
            })
        else:
            for col_idx, header in enumerate(headers):
                ws.write(2, col_idx, header, header_fmt)
            ws.merge_range(3, 0, 3, last_col, empty_message, empty_fmt)
        for col_idx, header in enumerate(headers):
            ws.set_column(col_idx, col_idx, column_width(header, rows, col_idx), column_format_for_header(header))
        apply_sheet_highlights(ws, category, headers, last_row)
        ws.set_landscape()
        ws.fit_to_pages(1, 0)
        ws.repeat_rows(0, 2)

    def write_split_sheet(name, headers, rows):
        limit = 1000000
        chunks = [rows[i:i + limit] for i in range(0, len(rows), limit)] or [[]]
        for chunk_idx, chunk in enumerate(chunks):
            write_sheet(f"{name}_{chunk_idx + 1}" if len(rows) > limit else name, headers, chunk, category=name)

    # Write sheets
    ws_summary = wb.add_worksheet("论文方法摘要")
    ws_summary.hide_gridlines(2)
    ws_summary.set_tab_color('#1D4ED8')
    ws_summary.set_column("A:A", 20)
    ws_summary.set_column("B:B", 15)
    ws_summary.set_column("C:C", 18)
    ws_summary.set_column("D:D", 16)
    ws_summary.set_column("E:E", 16)
    ws_summary.set_column("F:F", 16)
    ws_summary.set_column("G:G", 18)
    ws_summary.set_column("H:H", 16)
    ws_summary.set_column("I:I", 16)
    ws_summary.set_column("J:J", 18)
    ws_summary.merge_range("A1:J2", "PhonTracer 实验方法与数据审计归档", summary_title_fmt)
    ws_summary.merge_range("A3:J3", "本页用于快速理解研究规模、方法参数和需优先复核的特殊值；详细记录请通过右侧导航进入对应工作表。", summary_subtitle_fmt)
    for col_start, label, value in (
        ("A", "发音人数", len(speakers)),
        ("C", "条目总数", total_items),
        ("E", "纳入分析", included_count),
        ("G", "排除条目", excluded_count),
        ("I", "人工微调", manually_adjusted),
    ):
        start_col_idx = xlsxwriter.utility.xl_cell_to_rowcol(f"{col_start}1")[1]
        start_idx = xlsxwriter.utility.xl_col_to_name(start_col_idx)
        end_idx = xlsxwriter.utility.xl_col_to_name(start_col_idx + 1)
        ws_summary.merge_range(f"{start_idx}5:{end_idx}5", label, card_label_fmt)
        ws_summary.merge_range(f"{start_idx}6:{end_idx}7", value, card_value_fmt)
    ws_summary.merge_range("A9:J9", "研究方法摘要", section_fmt)
    summary_text = generate_natural_language_summary(state)
    ws_summary.merge_range("A10:J12", summary_text, summary_text_fmt)
    ws_summary.merge_range("A14:D14", "质量审计概览", section_fmt)
    ws_summary.write_row("A15", ["关注指标", "数量", "建议", "跳转"], header_fmt)
    risk_rows = [
        ["排除条目", excluded_count, "核对排除原因", "排除与忽略条目"],
        ["局部参数偏离", len(param_exception_rows), "确认条目级覆盖值", "局部参数差异"],
        ["自动切分提示", warning_item_count, "核对子音节切分提示", "质量控制与复核记录"],
        ["声学复核标记", review_item_count, "含缺失帧或短音段标记条目", "质量控制与复核记录"],
        ["资源校验异常", resource_issue_count, "检查缓存或录音参数", "资源清单"],
    ]
    for row_idx, row in enumerate(risk_rows, start=15):
        ws_summary.write(row_idx, 0, row[0], value_fmt)
        ws_summary.write(row_idx, 1, row[1], green_fmt if row[1] == 0 else (red_fmt if row[0] == "资源校验异常" and any(r[4] != "正常" for r in resource_rows) else orange_fmt))
        ws_summary.write(row_idx, 2, row[2], value_fmt)
        ws_summary.write_url(row_idx, 3, f"internal:'{row[3]}'!A1", link_fmt, row[3])
    ws_summary.merge_range("F14:J14", "颜色图例与使用顺序", section_fmt)
    legend_rows = [
        ["红色", "排除条目、缓存损坏或关键错误", red_fmt],
        ["橙色", "局部参数偏离、声学复核标记或低置信度", orange_fmt],
        ["黄色", "人工调整、最终边界变化或词内切分点", yellow_fmt],
        ["建议顺序", "先看红色，再看橙色与黄色；颜色仅辅助，状态列保留可机读说明。", value_fmt],
        ["模式分布", mode_summary, value_fmt],
    ]
    for row_idx, (label, description, fmt) in enumerate(legend_rows, start=15):
        ws_summary.write(row_idx, 5, label, fmt)
        ws_summary.merge_range(row_idx, 6, row_idx, 9, description, value_fmt)
    ws_summary.merge_range("A22:C22", "分组统计", section_fmt)
    ws_summary.write_row("A23", ["组别", "纳入分析", "排除"], header_fmt)
    summary_groups = group_summary_rows or [["无分组", 0, 0]]
    for row_idx, row in enumerate(summary_groups, start=23):
        ws_summary.write_row(row_idx, 0, row, value_fmt)
    chart = wb.add_chart({'type': 'bar', 'subtype': 'stacked'})
    first_group_row = 24
    last_group_row = first_group_row + len(summary_groups) - 1
    chart.add_series({'name': '纳入分析', 'categories': f"='论文方法摘要'!$A${first_group_row}:$A${last_group_row}", 'values': f"='论文方法摘要'!$B${first_group_row}:$B${last_group_row}", 'fill': {'color': '#60A5FA'}})
    chart.add_series({'name': '排除', 'categories': f"='论文方法摘要'!$A${first_group_row}:$A${last_group_row}", 'values': f"='论文方法摘要'!$C${first_group_row}:$C${last_group_row}", 'fill': {'color': '#F87171'}})
    chart.set_title({'name': '各组纳入与排除条目'})
    chart.set_x_axis({'name': '条目数', 'major_gridlines': {'visible': False}})
    chart.set_y_axis({'num_font': {'size': 9}})
    chart.set_legend({'position': 'bottom'})
    chart.set_style(10)
    ws_summary.insert_chart("E22", chart, {'x_scale': 1.25, 'y_scale': 1.0})
    archive_row = max(39, 25 + len(summary_groups))
    ws_summary.merge_range(archive_row, 0, archive_row, 9, "归档身份信息", section_fmt)
    archive_rows = [
        ["工程格式版本", state.get("version", "1.0")],
        ["软件版本", sw_ver_str],
        ["最后保存时间", save_time_str],
        ["录音数字化共识参数", consensus_str],
        ["SHA-256", file_hash],
    ]
    for offset, row in enumerate(archive_rows, start=1):
        ws_summary.write(archive_row + offset, 0, row[0], label_fmt)
        ws_summary.merge_range(archive_row + offset, 1, archive_row + offset, 9, row[1], value_fmt)
    ws_summary.set_row(0, 26)
    ws_summary.set_row(1, 18)
    ws_summary.set_row(2, 22)
    approx_lines = max(3, int(np.ceil(visual_len(summary_text) / 140.0)))
    row_h = max(22, int(np.ceil(approx_lines * 18.0 / 3.0)))
    ws_summary.set_row(9, row_h)
    ws_summary.set_row(10, row_h)
    ws_summary.set_row(11, row_h)
    ws_summary.freeze_panes(3, 0)
    ws_summary.set_landscape()
    ws_summary.fit_to_pages(1, 2)
    write_sheet("工程概览", ["概览指标", "指标取值"], overview_rows)
    write_sheet("发音人参数", ["发音人", "分析模式", "时序点数", "F0下限", "F0上限", "清浊阈值", "声能跌落", "排除声母", "共振峰分析上限", "追踪个数", "窗长", "预加重", "策略"], speaker_params_rows)
    write_sheet("条目明细", ["发音人", "组别", "条目ID", "词项", "路径", "模式", "是否排除", "定制下限", "定制上限", "定制阈值", "参数状态", "字表版本", "字表名称", "组备注", "组标签", "词项备注", "词项标签", "别名", "元数据来源", "自定义字段"], item_detail_rows)
    write_sheet("词级边界", ["发音人", "组别", "条目ID", "词项", "是否排除", "宏起点", "宏终点", "自动检测起点(s)", "自动检测终点(s)", "采样起点", "采样终点", "时长", "边界状态"], word_boundary_rows)
    write_sheet("字级边界", ["发音人", "组别", "条目ID", "词项", "是否排除", "音节序号", "音节", "字起点", "字终点", "切分点", "切分状态"], char_boundary_rows)
    write_sheet("局部参数差异", ["发音人", "组别", "条目ID", "词项", "差异详情"], param_exception_rows)
    write_sheet("质量控制与复核记录", ["发音人", "组别", "条目ID", "词项", "是否排除", "人工修改", "置信度", "声学复核提示", "声学复核标记"], audit_warning_rows)
    write_sheet("排除与忽略条目", ["发音人", "组别", "条目ID", "词项", "时间边界", "排除原因", "排除时间"], excluded_rows)
    write_sheet("资源清单", ["类型", "路径", "大小", "数字化参数", "校验状态"], resource_rows)
    write_sheet("自定义脚本记录", ["脚本名", "类型", "运行时间", "用户需求", "代码 SHA-256", "运行状态", "输出文件", "软件版本", "API 版本"], script_records, empty_message="本工程未归档自定义脚本")
    write_sheet("自定义脚本源码", ["脚本名", "代码 SHA-256", "完整源码"], script_sources, empty_message="本工程未归档自定义脚本")
    write_sheet("字段说明", ["工作表", "字段", "定义"], [
        ["工程概览", "SHA-256", "归档文件唯一性校验码，用于确认报告对应的工程版本。"],
        ["条目明细", "参数状态", "说明条目是否沿用发音人多数参数，或存在局部参数偏离。"],
        ["条目明细", "字表元数据字段", "高级字表 v2 导入时保存的组备注、词项备注、标签、别名和自定义字段。"],
        ["词级边界", "宏起点/宏终点", "待分析音频宏范围。"],
        ["词级边界", "自动检测起点(s)/自动检测终点(s)", "自动检测后的原始边界。"],
        ["词级边界", "采样起点/采样终点", "最终进入声学采样的采用边界。"],
        ["词级边界", "边界状态", "明确记录人工调整、最终边界变化和词内切分点。"],
        ["字级边界", "切分状态", "明确记录词内切分点或字级边界缺失。"],
        ["质量控制与复核记录", "声学复核标记", "算法审计结果；合格条目显示“合格无警告”。"],
        ["质量控制与复核记录", "声学复核提示", "子音节智能切分过程中的算法提示与置信度说明。"],
        ["资源清单", "校验状态", "归档缓存可解析性以及录音参数是否偏离多数值。"],
        ["自定义脚本记录", "代码 SHA-256", "已归档自定义脚本的完整性校验哈希值。"],
        ["自定义脚本源码", "完整源码", "自定义脚本的完整 Python 源代码内容。"],
    ])

    if include_cache_details:
        if f0_cache_rows:
            write_sheet("F0缓存明细", ["发音人", "组别", "条目ID", "词项", "时间点(s)", "基频F0(Hz)", "是否当前模式"], f0_cache_rows)
        if formant_cache_rows:
            write_sheet("共振峰缓存明细", ["发音人", "组别", "条目ID", "词项", "时间点(s)", "F1共振峰(Hz)", "F2共振峰(Hz)", "F3共振峰(Hz)", "是否当前模式"], formant_cache_rows)

    wb.close()


def export_reports_from_teproj(
    teproj_path: str,
    output_dir: str,
    export_markdown: bool = True,
    export_excel: bool = True,
    include_cache_details: bool = False,
    progress_callback: Optional[Callable[[float, str], None]] = None,
) -> Tuple[List[str], str]:
    def report_progress(value: float, message: str) -> None:
        if progress_callback:
            progress_callback(value, message)

    if not os.path.exists(teproj_path):
        raise FileNotFoundError(f"工程文件不存在：{teproj_path}")

    os.makedirs(output_dir, exist_ok=True)

    report_progress(0.08, "正在读取工程元数据...")
    from modules.project_manager import read_project_metadata_from_archive
    state, namelist = read_project_metadata_from_archive(teproj_path)

    base_name = os.path.splitext(os.path.basename(teproj_path))[0]
    exported_files = []

    with zipfile.ZipFile(teproj_path, "r") as z:
        if export_markdown:
            report_progress(0.18, "正在生成 Markdown 研究方法报告...")
            md_content = generate_markdown_report(teproj_path, state, z)
            md_path = os.path.join(output_dir, f"{base_name}_研究方法报告.md")
            with open(md_path, "w", encoding="utf-8") as f:
                f.write(md_content)
            exported_files.append(md_path)

        if export_excel:
            report_progress(0.58 if export_markdown else 0.22, "正在生成 Excel 数据档案...")
            xlsx_path = os.path.join(output_dir, f"{base_name}_研究档案.xlsx")
            write_excel_archive(teproj_path, state, xlsx_path, include_cache_details)
            exported_files.append(xlsx_path)

    report_progress(1.0, "报告导出完成")
    return exported_files, base_name
