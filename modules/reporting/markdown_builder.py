# -*- coding: utf-8 -*-
"""
Markdown research method report generator and summary table formatter.
Refactored from modules/report_generator.py (L1271-L1787).
"""
import io
import os
import time
import zipfile
from typing import Any, Dict, List, Optional

from ..storage.archive_security import validate_project_archive_members
from .item_inspector import (
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
from .stats_aggregator import compute_acoustic_statistics

__all__ = [
    "build_markdown_acoustic_summary_section",
    "generate_markdown_report",
]

def build_markdown_acoustic_summary_section(state: Dict[str, Any], zip_ref: Optional[zipfile.ZipFile] = None) -> List[str]:
    """构建 Markdown 报告中的 `## 核心声学指标分组统计摘要表` 与各词项核心声学数据速览表。"""
    stats_group = compute_acoustic_statistics(state, zip_ref=zip_ref, by="group", scope="integrated")
    md_lines = []
    md_lines.append("## 核心声学指标分组统计摘要表")
    md_lines.append("")
    md_lines.append("本节自动汇总各声调/实验分组与各词项的实测声学均值、标准差、五度值（Chao's T-value, 1–5）拟合轨迹与编码建议，可直接用于学术论文结果报告。")
    md_lines.append("")

    baselines = stats_group.get("speaker_baselines", {})
    if baselines:
        md_lines.append("### 发音人基频基线 (Speaker F0 Baselines)")
        md_lines.append("")
        md_lines.append("| 发音人 | 样本数 (有效F0) | F0 极小值 (Hz) | F0 极大值 (Hz) | F0 中位数 P50 (Hz) | P5~P95 分布区间 (Hz) | F0 均值 ± 标准差 (Hz) |")
        md_lines.append("| :--- | :--- | :--- | :--- | :--- | :--- | :--- |")
        for spk_name, bl in baselines.items():
            cnt_str = f"{bl.get('count', 0)} ({bl.get('valid_f0_count', 0)})"
            min_s = f"{bl['min_f0']:.1f}" if bl.get("min_f0") is not None else "-"
            max_s = f"{bl['max_f0']:.1f}" if bl.get("max_f0") is not None else "-"
            p50_s = f"{bl['median_f0']:.1f}" if bl.get("median_f0") is not None else "-"
            p5_p95_s = (
                f"{bl['p5_f0']:.1f} ~ {bl['p95_f0']:.1f}"
                if bl.get("p5_f0") is not None and bl.get("p95_f0") is not None
                else "-"
            )
            mean_sd_s = (
                f"{bl['mean_f0']:.1f} ± {bl['sd_f0']:.1f}"
                if bl.get("mean_f0") is not None and bl.get("sd_f0") is not None
                else "-"
            )
            md_lines.append(f"| {spk_name} | {cnt_str} | {min_s} | {max_s} | {p50_s} | {p5_p95_s} | {mean_sd_s} |")
        md_lines.append("")

    def _fmt_row(label_col: str, row: Dict[str, Any]) -> str:
        cnt = f"{row.get('count', 0)} (有效{row.get('valid_f0_count', 0)}, 警告{row.get('warning_count', 0)})"
        dur = (
            f"{row['duration_ms_mean']:.1f} ± {row['duration_ms_sd']:.1f}"
            if row.get("duration_ms_mean") is not None and row.get("duration_ms_sd") is not None
            else (f"{row['duration_ms_mean']:.1f}" if row.get("duration_ms_mean") is not None else "-")
        )
        f_s = f"{row['f0_start_mean']:.1f}" if row.get("f0_start_mean") is not None else "-"
        f_m = f"{row['f0_mid_mean']:.1f}" if row.get("f0_mid_mean") is not None else "-"
        f_e = f"{row['f0_end_mean']:.1f}" if row.get("f0_end_mean") is not None else "-"
        f_msd = (
            f"{row['f0_mean']:.1f} ± {row['f0_sd']:.1f}"
            if row.get("f0_mean") is not None and row.get("f0_sd") is not None
            else (f"{row['f0_mean']:.1f}" if row.get("f0_mean") is not None else "-")
        )
        f_rng = f"{row['f0_range_mean']:.1f}" if row.get("f0_range_mean") is not None else "-"
        if row.get("t_code"):
            tc_pts = "/".join(f"{v:.1f}" for v in (row.get("t_contour") or []))
            t_str = f"`{row['t_code']}` ({tc_pts})" if tc_pts else f"`{row['t_code']}`"
        else:
            t_str = "-"
        if any(row.get(k) is not None for k in ("f1_mean", "f2_mean", "f3_mean")):
            f1_s = f"{row['f1_mean']:.0f}" if row.get("f1_mean") is not None else "-"
            f2_s = f"{row['f2_mean']:.0f}" if row.get("f2_mean") is not None else "-"
            f3_s = f"{row['f3_mean']:.0f}" if row.get("f3_mean") is not None else "-"
            fmt_str = f"{f1_s} / {f2_s} / {f3_s}"
        else:
            fmt_str = "-"
        return f"| {label_col} | {cnt} | {dur} | {f_s} | {f_m} | {f_e} | {f_msd} | {f_rng} | {t_str} | {fmt_str} |"

    summary_rows = stats_group.get("summary", [])
    md_lines.append("### 分组核心声学指标汇总表")
    md_lines.append("")
    if summary_rows:
        md_lines.append("| 分组/发音人 | 样本数 | 平均时长(ms) | 起点F0(Hz) | 中点F0(Hz) | 终点F0(Hz) | F0均值±标准差(Hz) | F0极差(Hz) | 拟合五度值(T值) | F1/F2/F3均值(Hz) |")
        md_lines.append("| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |")
        speakers_dict = state.get("speakers", {}) if isinstance(state, dict) else {}
        multi_spk = len(speakers_dict) > 1
        for row in summary_rows:
            grp_label = f"{row['key']} (全体合并)" if multi_spk else str(row["key"])
            md_lines.append(_fmt_row(grp_label, row))

        if multi_spk:
            for sid, spk in speakers_dict.items():
                if not isinstance(spk, dict) or not spk.get("items"):
                    continue
                spk_stats = compute_acoustic_statistics(state, zip_ref=zip_ref, by="group", scope="active", active_speaker_id=sid)
                spk_name = spk.get("name", sid)
                for srow in spk_stats.get("summary", []):
                    md_lines.append(_fmt_row(f"{spk_name} / {srow['key']}", srow))
    else:
        md_lines.append("当前工程暂无有效声学数据可供统计汇总。")
    md_lines.append("")

    anova_info = stats_group.get("anova_summary", {})
    if anova_info:
        md_lines.append("### 组间单因素方差分析 (One-Way ANOVA)")
        md_lines.append("")
        md_lines.append("| 检验维度 | F 统计量 (F-statistic) | p 值 (p-value) | 显著性判定 (α=0.05) |")
        md_lines.append("| :--- | :--- | :--- | :--- |")
        dim_labels = {
            "duration_ms": "音段时长 (Duration, ms)",
            "f0_mean_hz": "平均基频 (Mean F0, Hz)",
            "f0_range_hz": "基频极差 (F0 Range, Hz)",
        }
        for dk, dlabel in dim_labels.items():
            if dk in anova_info:
                ainfo = anova_info[dk]
                sig_str = "显著 (p < 0.05)" if ainfo.get("significant_p05") else "不显著 (p ≥ 0.05)"
                md_lines.append(f"| {dlabel} | F = {ainfo['f_stat']:.4f} | p = {ainfo['p_value']:.6f} | {sig_str} |")
        md_lines.append("")

    item_records = stats_group.get("item_records", [])
    md_lines.append("### 各词项核心声学数据速览表")
    md_lines.append("")
    if item_records:
        md_lines.append("| 发音人 | 分组 | 词项 | 时长(ms) | 起点F0(Hz) | 中点F0(Hz) | 终点F0(Hz) | F0均值±标准差(Hz) | F0极差(Hz) | 拟合五度值(T值) | F1/F2/F3均值(Hz) |")
        md_lines.append("| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |")
        for rec in item_records:
            dur_s = f"{rec['duration_ms_mean']:.1f}" if rec.get("duration_ms_mean") is not None else "-"
            fs = f"{rec['f0_start_mean']:.1f}" if rec.get("f0_start_mean") is not None else "-"
            fm = f"{rec['f0_mid_mean']:.1f}" if rec.get("f0_mid_mean") is not None else "-"
            fe = f"{rec['f0_end_mean']:.1f}" if rec.get("f0_end_mean") is not None else "-"
            fmsd = (
                f"{rec['f0_mean']:.1f} ± {rec['f0_sd']:.1f}"
                if rec.get("f0_mean") is not None and rec.get("f0_sd") is not None
                else "-"
            )
            frng = f"{rec['f0_range_mean']:.1f}" if rec.get("f0_range_mean") is not None else "-"
            if rec.get("t_code"):
                t_pts = "/".join(f"{v:.1f}" for v in (rec.get("t_contour") or []))
                t_cell = f"`{rec['t_code']}` ({t_pts})" if t_pts else f"`{rec['t_code']}`"
            else:
                t_cell = "-"
            if any(rec.get(k) is not None for k in ("f1_mean", "f2_mean", "f3_mean")):
                f1_s = f"{rec['f1_mean']:.0f}" if rec.get("f1_mean") is not None else "-"
                f2_s = f"{rec['f2_mean']:.0f}" if rec.get("f2_mean") is not None else "-"
                f3_s = f"{rec['f3_mean']:.0f}" if rec.get("f3_mean") is not None else "-"
                fmt_cell = f"{f1_s} / {f2_s} / {f3_s}"
            else:
                fmt_cell = "-"
            md_lines.append(
                f"| {rec['speaker']} | {rec['group']} | {rec['label']} | {dur_s} | {fs} | {fm} | {fe} | {fmsd} | {frng} | {t_cell} | {fmt_cell} |"
            )
    else:
        md_lines.append("当前工程暂无有效词项声学记录。")
    md_lines.append("")
    return md_lines


def generate_markdown_report(teproj_path: str, state: Dict[str, Any], zip_ref: zipfile.ZipFile) -> str:
    from modules.project_adaptor import validate_project_archive_members
    if zip_ref:
        validate_project_archive_members(zip_ref)
    teproj_filename = os.path.basename(teproj_path)
    file_hash = calculate_sha256(teproj_path)
    current_time = time.strftime("%Y-%m-%d %H:%M:%S")

    # 1. Natural language summary
    nat_lang = generate_natural_language_summary(state)

    # 2. General metadata
    version = state.get("version", "1.0")
    sw_ver = state.get("software_version")
    if not sw_ver or sw_ver == "1.2.0 (或更早版本，未记录)":
        sw_ver_str = "未记录 (推断为早于 1.2.0)"
    else:
        sw_ver_str = sw_ver

    # Fallback save_time using project.json zip modified time
    save_time = state.get("save_time")
    if not save_time or save_time == "未记录":
        try:
            info = zip_ref.getinfo("project.json")
            dt = info.date_time
            save_time_str = f"{dt[0]:04d}-{dt[1]:02d}-{dt[2]:02d} {dt[3]:02d}:{dt[4]:02d}:{dt[5]:02d} (基于文件修改时间推断)"
        except Exception:
            save_time_str = "未记录"
    else:
        save_time_str = save_time

    trim_silence_val = state.get("trim_silence")
    if trim_silence_val is None:
        trim_silence_str = "未记录 (旧版本默认：开启)"
    else:
        trim_silence_str = "开启" if trim_silence_val else "关闭"

    export_rule = state.get("export_numbering_rule")
    if export_rule == "continuous":
        export_rule_str = "全部连续标号"
    elif export_rule in ("per_group", "by_group"):
        export_rule_str = "按分组重新标号"
    else:
        export_rule_str = "未记录"

    speakers = state.get("speakers", {})

    # WAV digitization parameters
    wav_params = extract_project_wav_params(zip_ref)
    consensus, deviations = get_consensus_and_deviations(wav_params)
    consensus_str = format_wav_params(consensus[0], consensus[1], consensus[2]) if consensus[0] is not None else "无音频文件"

    # Global flag for F0 and Formant presence
    has_f0 = False
    has_formant = False
    for spk in speakers.values():
        spk_params = spk.get("last_params", {})
        spk_mode = spk_params.get("analysis_mode", "f0")
        items = spk.get("items", {})
        if not items:
            if spk_mode == "f0":
                has_f0 = True
            elif spk_mode == "formant":
                has_formant = True
            else:
                has_f0 = True
                has_formant = True
        for item in items.values():
            imode = item.get("analysis_mode") or spk_mode
            if imode == "f0":
                has_f0 = True
            elif imode == "formant":
                has_formant = True
    if not has_f0 and not has_formant:
        has_f0 = True

    # Count variables
    total_items = 0
    excluded_count = 0
    manually_adjusted = 0
    warning_list = []
    excluded_list = []

    # Build usage map to map deviating files to speakers/items
    wav_usage = {}
    for spk_id, spk in speakers.items():
        name = spk.get("name", "发音人")
        items = spk.get("items", {})
        total_items += len(items)
        params = spk.get("last_params", {})

        long_path = spk.get("long_audio_path")
        if long_path:
            wav_usage.setdefault(long_path.replace("\\", "/"), []).append((name, "长音频", "整段"))
        for path in spk.get("pending_batch_paths", []):
            wav_usage.setdefault(path.replace("\\", "/"), []).append((name, "批处理列表", "未关联条目"))

        for item_id, item in items.items():
            if item.get("is_excluded", False):
                excluded_count += 1
                excluded_list.append({
                    "speaker": name,
                    "group": item.get("group", "默认组"),
                    "id": item_id,
                    "label": item.get("label", "无"),
                    "start": item.get("start"),
                    "end": item.get("end"),
                    "reason": item.get("exclusion_reason", "未说明原因") or "未说明原因",
                    "excluded_at": item.get("excluded_at", "未知时间") or "未知时间"
                })
                continue

            item_path = item.get("path")
            if item_path:
                wav_usage.setdefault(item_path.replace("\\", "/"), []).append((name, item_id, item.get("label", "无")))

            pitch_data, formant_data, p_status, f_status = load_item_cache_if_any(item, zip_ref, spk=spk)
            item_warnings = analyze_item_anomalies(item, pitch_data, formant_data, params, p_status, f_status)
            if item.get("is_manual_edited"):
                manually_adjusted += 1
            if item_warnings:
                warning_list.append({
                    "id": item_id,
                    "label": item.get("label", "无"),
                    "group": item.get("group", "默认组"),
                    "speaker": name,
                    "warnings": item_warnings
                })

    total_valid = total_items - excluded_count
    adjusted_ratio = (manually_adjusted / total_valid * 100.0) if total_valid > 0 else 0.0

    # Start building MD
    lines = []
    lines.append("# PhonTracer 声学分析研究方法报告与数据审计档案")
    lines.append("")
    lines.append("本报告由 PhonTracer 自动生成，旨在为语言学研究和方言调查提供完整的、可审计的声学分析记录与可复现的方法学说明。")
    lines.append("")

    lines.append("## 1. 工程概览与元数据")
    lines.append("")
    lines.append("| 元数据字段 | 字段记录值 |")
    lines.append("| :--- | :--- |")
    lines.append(f"| **工程文件名称** | `{teproj_filename}` |")
    lines.append(f"| **工程格式版本** | `{version}` |")
    lines.append(f"| **PhonTracer 软件版本** | `{sw_ver_str}` |")
    lines.append(f"| **本报告导出时间** | `{current_time}` |")
    lines.append(f"| **工程最后保存时间** | `{save_time_str}` |")
    lines.append(f"| **归档文件指纹 (SHA-256)** | `{file_hash}` |")
    lines.append(f"| **发音人总数** | {len(speakers)} |")
    lines.append(f"| **保存条目总数** | {total_items} |")
    lines.append(f"| **已忽略/排除条目数** | {excluded_count} |")
    lines.append(f"| **最终分析条目数** | {total_items - excluded_count} |")
    lines.append(f"| **录音数字化共识参数** | {consensus_str} |")
    lines.append(f"| **边缘静音裁切** | {trim_silence_str} (参与边界修正计算) |")
    lines.append(f"| **音节标号规则** | {export_rule_str} |")
    lines.append("")

    if deviations:
        lines.append("### 录音数字化参数偏离详情")
        lines.append("以下文件在录音采样率、位深或声道数上与总体共识参数不一致：")
        lines.append("")
        lines.append("| 音频文件路径 | 关联发音人/条目 | 实际数字化参数 |")
        lines.append("| :--- | :--- | :--- |")
        for dev_file, dev_sr, dev_bits, dev_ch in deviations:
            assoc_strs = []
            if dev_file in wav_usage:
                for spk_name, item_id, item_lbl in wav_usage[dev_file]:
                    if item_id == "长音频":
                        assoc_strs.append(f"{spk_name} (长音频)")
                    elif item_id == "批处理列表":
                        assoc_strs.append(f"{spk_name} (待分析音频列表)")
                    else:
                        assoc_strs.append(f"{spk_name} (条目 {item_id}: [{item_lbl}])")
            assoc_desc = "、".join(assoc_strs) if assoc_strs else "未关联"
            lines.append(f"| `{dev_file}` | {assoc_desc} | {format_wav_params(dev_sr, dev_bits, dev_ch)} |")
        lines.append("")

    lines.append("## 2. 研究方法摘要 (自然语言)")
    lines.append("")
    lines.append("> [!NOTE]")
    lines.append(f"> {nat_lang}")
    lines.append("")

    lines.append("## 3. 发音人级算法配置参数")
    lines.append("")
    lines.append("分别列出各个发音人在分析时采用的通用声学参数：")
    lines.append("")

    for spk_id, spk in speakers.items():
        name = spk.get("name", "发音人")
        params = spk.get("last_params", {})
        tab_mode = spk.get("tab_mode", "多条独立音频")
        mode = params.get("analysis_mode", "f0")

        spk_has_f0 = False
        spk_has_formant = False
        for item in spk.get("items", {}).values():
            imode = item.get("analysis_mode") or mode
            if imode == "f0":
                spk_has_f0 = True
            elif imode == "formant":
                spk_has_formant = True
        if not spk.get("items"):
            if mode == "f0":
                spk_has_f0 = True
            else:
                spk_has_formant = True

        if spk_has_f0 and spk_has_formant:
            mode_str = "声调/基频(F0) 与 共振峰 混合模式"
        elif spk_has_formant:
            mode_str = "共振峰(F1-F2)模式"
        else:
            mode_str = "声调/基频(F0)模式"

        p_floor = get_pitch_floor(params)
        p_ceiling = get_pitch_ceiling(params)
        v_thresh = get_voicing_threshold(params)

        lines.append(f"### 发音人: {name}")
        lines.append(f"- **分析模式**: {mode_str}")
        lines.append(f"- **音频管理模式**: {tab_mode}")
        if tab_mode == "单条长音频":
            long_path = spk.get("long_audio_path") or "无"
            long_name = os.path.basename(long_path) if long_path else "无"
            lines.append(f"  - 长音频源文件: `{long_name}`")
            lines.append(f"  - 自动分段数: {len(spk.get('current_macro_segments') or [])}")
        else:
            lines.append(f"  - 独立音频文件数: {len(spk.get('pending_batch_paths') or [])}")

        if spk_has_f0:
            lines.append(f"- **核心基频算法与阈值**:")
            lines.append(f"  - 采样等分点数 (Pts): {params.get('pts', 11)}")
            lines.append(f"  - 基频下限 (Pitch Floor): {p_floor:.1f} Hz")
            lines.append(f"  - 基频上限 (Pitch Ceiling): {p_ceiling:.1f} Hz")
            lines.append(f"  - 清浊音判定阈值 (Voicing Threshold): {v_thresh:.2f}")
            lines.append(f"  - 声能跌落门限 (Energy Drop DB): {params.get('db', 60.0):.1f} dB")
            lines.append(f"  - 排除开头声母时长 (Skip Front): {params.get('skip_front', 0.0):.3f} s")

        if spk_has_formant:
            lines.append(f"- **共振峰配置**:")
            if not spk_has_f0:
                lines.append(f"  - 采样等分点数 (Pts): {params.get('pts', 11)}")
            lines.append(f"  - 共振峰分析上限 (Formant Ceiling): {params.get('formant_max_hz', 5500.0):.1f} Hz")
            lines.append(f"  - 共振峰追踪个数 (Formant Count): {params.get('formant_count', 5)}")
            lines.append(f"  - 采样窗长 (Window Length): {params.get('formant_window_length', 0.025):.3f} s")
            lines.append(f"  - 预加重高通截止频率 (Pre-emphasis Hz): {params.get('formant_pre_emphasis', 50.0):.1f} Hz")
            lines.append(f"  - 共振峰采样策略 (Strategy): {params.get('formant_sample_strategy', '整段11点')}")
        lines.append("")

    lines.append("## 4. 条目级参数偏离（以多数条目为基准）")
    lines.append("")
    lines.append("以下差异以各发音人最终纳入分析条目的多数参数为基准，而不是以导出报告时界面中最后停留的设置值为基准。")
    lines.append("")
    exceptions = []
    for spk_id, spk in speakers.items():
        name = spk.get("name", "发音人")
        params = spk.get("last_params", {})
        items = spk.get("items", {})
        majority_params = get_majority_item_params(items, params)
        p_floor = majority_params["pitch_floor"]
        p_ceiling = majority_params["pitch_ceiling"]
        v_thresh = majority_params["voicing_threshold"]

        # 纳入分析条目的多数值
        f_max = majority_params["formant_max_hz"]
        f_count = majority_params["formant_count"]
        f_win = majority_params["formant_window_length"]
        f_pre = majority_params["formant_pre_emphasis"]
        f_strat = majority_params["formant_sample_strategy"]

        for item_id, item in items.items():
            if item.get("is_excluded", False):
                continue
            diffs = []
            item_floor = get_pitch_floor(item, p_floor)
            item_ceiling = get_pitch_ceiling(item, p_ceiling)
            item_thresh = get_voicing_threshold(item, v_thresh)

            if item_floor != p_floor:
                diffs.append(f"基频下限: {item_floor:.0f} Hz (多数值 {p_floor:.0f} Hz)")
            if item_ceiling != p_ceiling:
                diffs.append(f"基频上限: {item_ceiling:.0f} Hz (多数值 {p_ceiling:.0f} Hz)")
            if item_thresh != v_thresh:
                diffs.append(f"浊音阈值: {item_thresh:.2f} (多数值 {v_thresh:.2f})")

            item_f_max = get_item_override_param(item, "formant_max_hz", f_max)
            item_f_count = get_item_override_param(item, "formant_count", f_count)
            item_f_win = get_item_override_param(item, "formant_window_length", f_win)
            item_f_pre = get_item_override_param(item, "formant_pre_emphasis", f_pre)
            item_f_strat = get_item_override_param(item, "formant_sample_strategy", f_strat)

            if item_f_max != f_max:
                diffs.append(f"共振峰分析上限: {item_f_max:.0f} Hz (多数值 {f_max:.0f} Hz)")
            if item_f_count != f_count:
                diffs.append(f"共振峰追踪个数: {item_f_count} (多数值 {f_count})")
            if item_f_win != f_win:
                diffs.append(f"共振峰分析窗长: {item_f_win:.3f} s (多数值 {f_win:.3f} s)")
            if item_f_pre != f_pre:
                diffs.append(f"共振峰预加重: {item_f_pre:.1f} Hz (多数值 {f_pre:.1f} Hz)")
            if item_f_strat != f_strat:
                diffs.append(f"共振峰采样策略: {item_f_strat} (多数值 {f_strat})")

            if diffs:
                exceptions.append({
                    "speaker": name,
                    "id": item_id,
                    "label": item.get("label", "无"),
                    "diffs": ", ".join(diffs)
                })

    if exceptions:
        lines.append("| 发音人 | 条目ID | 词项 | 局部定制参数偏离详情 |")
        lines.append("| :--- | :--- | :--- | :--- |")
        for exc in exceptions:
            lines.append(f"| {exc['speaker']} | {exc['id']} | {exc['label']} | {exc['diffs']} |")
    else:
        lines.append("工程中所有纳入分析的条目均与所属发音人的多数参数一致，无任何局部例外。")
    lines.append("")

    lines.append("## 5. 音段边界提取与切分规则")
    lines.append("")
    lines.append("PhonTracer 支持细粒度音段分析与边界审计。工程中的时间边界按如下方式流转：")
    lines.append("1. **宏观音段范围 (`macro_start` 至 `macro_end`)**：指示音频切割的初始粗区间。")
    lines.append("2. **算法检测边界 (`raw_start` 至 `raw_end`)**：指示算法根据声能分布、共振峰或清浊突变自动检测到的元音核边界。")
    lines.append("3. **最终采用边界 (`start` 至 `end`)**：实际作为点数等分采样的起止时间。如果人工调整过，将偏离算法检测边界。")
    lines.append("4. **字级精确边界 (`chars_bounds` 与 `inner_splits`)**：对于多音节词，标记词内每个单字的精确起止范围。")
    lines.append("")

    lines.append("## 6. 人工复核与质量审计摘要")
    lines.append("")
    other_ratio = (100.0 - adjusted_ratio) if total_valid > 0 else 0.0
    other_count = total_valid - manually_adjusted
    lines.append(f"- **边界与轨迹修订统计**：纳入分析的条目中，有 **{manually_adjusted}** 个条目（**{adjusted_ratio:.1f}%**）在声谱图复核过程中进行了边界微调或轨迹点修正；其余 **{other_count}** 个条目（**{other_ratio:.1f}%**）保持自动检测边界与原始提取轨迹。")
    lines.append("- **声学特征复核记录**：以下条目包含短时音段、清音段间断或基频跳变等声学特征标记，供数据分析时参考：")
    lines.append("")

    if warning_list:
        lines.append("| 发音人 | 分组 | 词项 | 触发的声学复核标记 |")
        lines.append("| :--- | :--- | :--- | :--- |")
        for warn in warning_list:
            warning_joined = "; ".join(warn["warnings"])
            lines.append(f"| {warn['speaker']} | {warn['group']} | {warn['label']} | {warning_joined} |")
    else:
        lines.append("所有纳入分析的条目均通过基频与共振峰连续性检验，未检出缺失段或跳变标记。")
    lines.append("")

    lines.append("## 7. 实验背景与方法学补充清单")
    lines.append("")
    lines.append("撰写论文的方法（Methods）章节时，除本报告已记录的声学提取参数外，建议结合实验设计补充说明以下背景信息：")
    lines.append("1. **发音人人口学信息**：性别、年龄分布、方言/母语背景及语言使用情况。")
    lines.append("2. **录音硬件与声学环境**：麦克风型号、录音接口、声学环境（如消音室或安静室内）及本底噪声水平（采样率、位深与声道数见第 1 节）。")
    lines.append("3. **实验材料与录音流程**：字表排列方式（如随机化或分组呈现）、发音指导语、重复次数及发音疲劳控制措施。")
    lines.append("4. **外部标注溯源（如适用）**：若分段边界导入自外部 TextGrid（如 MFA 强制对齐或 Praat 人工标注），建议说明对齐模型或标注规范。")
    lines.append("")

    lines.append("## 8. 数据清洗与排除条目清单")
    lines.append("")
    if excluded_list:
        lines.append("以下条目已被研究者标记为“忽略（不参与分析与导出）”，但在工程归档中完整保留了其原始录音、时间边界与状态，以便溯源和复核：")
        lines.append("")
        lines.append("| 发音人 | 分组 | 条目ID | 词项 | 时间边界(s) | 排除原因 | 排除操作时间 |")
        lines.append("| :--- | :--- | :--- | :--- | :--- | :--- | :--- |")
        for ex in excluded_list:
            b_str = f"{ex['start']:.3f}–{ex['end']:.3f}" if ex['start'] is not None and ex['end'] is not None else "-"
            lines.append(f"| {ex['speaker']} | {ex['group']} | `{ex['id']}` | {ex['label']} | {b_str} | {ex['reason']} | {ex['excluded_at']} |")
    else:
        lines.append("工程中无任何被忽略或排除的分析条目。")
    lines.append("")

    lines.extend(build_markdown_acoustic_summary_section(state, zip_ref=zip_ref))

    return "\n".join(lines)
