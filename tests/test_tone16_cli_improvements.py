# -*- coding: utf-8 -*-
"""
基于 Example/wordlist_tone16 语料的五大 CLI 体验与功能改进端到端自动化测试套件：
1. tool_split 切分对齐（男1.wav 词内短暂停顿、.ptwl 字表、无字表纯拆音）与段数不符告警/保留 extra 片段；
2. tool_merge 及 export merged_wav 保持原采样率（24 kHz）一致性；
3. 杂乱草稿 .txt 字表统一容错解析（// 注释、=== 标题 ===、圆括号/冒号组头、1. 2. 编号、- 列表符、/ 分隔词）；
4. help all / help tools / agent_guide 完整性与防截断体积控制、export 参数一致性、export_bundle 双字调专项图表与 .Collection 一键导出闭环；
5. 分页图表不生成无后缀重复首页图，以及 fix_warnings 清零后与 quality 质控图提示口径一致。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pytest

try:
    import parselmouth
except ImportError:
    from modules import _parselmouth_fallback as parselmouth

from cli import PhonTracerCLI
from modules.wordlist_v2 import load_wordlist_file

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_DIR = ROOT / "Example" / "wordlist_tone16"
MALE_WAV = EXAMPLE_DIR / "男1.wav"
FEMALE1_WAV = EXAMPLE_DIR / "女1.wav"
FEMALE2_WAV = EXAMPLE_DIR / "女2.wav"
EXAMPLE_PTWL = EXAMPLE_DIR / "普通话双字组16种声调实验字表.ptwl"


def run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "cli.py", *args],
        cwd=ROOT,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )


def _build_messy_draft_from_ptwl(ptwl_path: Path, out_txt: Path) -> list[str]:
    """根据标准 16 组双字调 .ptwl 构造包含各种人类随手草稿噪声的 .txt 字表。"""
    doc = load_wordlist_file(str(ptwl_path))
    expected_words: list[str] = []
    lines = [
        "// 普通话双字组 16 种声调组合录音草稿（随手记录版）",
        "=== 普通话双字组16种声调实验字表 ===",
        "/* 录音提示：每词自然连读，词间停顿半秒 */",
        "----------------------------------------",
    ]
    for idx, grp in enumerate(doc["groups"]):
        gname = grp["name"]
        w0, w1 = grp["items"][0]["label"], grp["items"][1]["label"]
        expected_words.extend([w0, w1])
        style = idx % 4
        if style == 0:
            lines.append(f"({gname})")
            lines.append(f"1. {w0}   2. {w1} // 第一组方式")
        elif style == 1:
            lines.append(f"（{gname}）：")
            lines.append(f"- {w0} / {w1}")
        elif style == 2:
            lines.append(f"{gname}:")
            lines.append(f"* 01) {w0} ／ 02) {w1}(正式词)")
        else:
            lines.append(f"=== {gname} ===")
            lines.append(f"• (1) {w0}")
            lines.append(f"• (2) {w1}  # 行尾备注")
        if idx == 7:
            lines.append("================ 中场休息分隔线 ================")
    out_txt.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return expected_words


def test_1_tool_split_ptwl_unlabeled_and_mismatch_handling(tmp_path: Path):
    """验证改进 1：tool_split 支持 .ptwl、无字表纯拆音、男1.wav 精准 32 词对齐，且段数不符时显式告警并保留 extra 片段。"""
    # 1) .ptwl 字表切分 男1.wav（含词内清塞擦音短暂停顿）应准确对齐 32 个词且保持 24kHz
    split_ptwl_dir = tmp_path / "split_ptwl"
    res_ptwl = run_cli(f'tool_split "{MALE_WAV}" "{EXAMPLE_PTWL}" "{split_ptwl_dir}"')
    assert res_ptwl.returncode == 0, res_ptwl.stderr
    p_ptwl = json.loads(res_ptwl.stdout.strip())
    assert p_ptwl["success"] is True
    assert p_ptwl["mismatch"] is False
    assert p_ptwl["detected_segments"] == 32
    assert p_ptwl["expected_words"] == 32
    assert p_ptwl["count"] == 32
    assert p_ptwl["sampling_frequency"] == 24000
    wav_files = sorted(split_ptwl_dir.glob("*.wav"))
    assert len(wav_files) == 32
    first_snd = parselmouth.Sound(str(wav_files[0]))
    assert int(round(first_snd.sampling_frequency)) == 24000

    # 2) 无字表纯拆音（直接省略字表参数）
    split_raw_dir = tmp_path / "split_unlabeled"
    res_raw = run_cli(f'tool_split "{MALE_WAV}" "{split_raw_dir}"')
    assert res_raw.returncode == 0, res_raw.stderr
    p_raw = json.loads(res_raw.stdout.strip())
    assert p_raw["success"] is True
    assert p_raw["mode"] == "unlabeled"
    assert p_raw["wordlist_mode"] == "none"
    assert p_raw["detected_segments"] == 32
    assert p_raw["count"] == 32
    raw_wavs = sorted(split_raw_dir.glob("*_segment.wav"))
    assert len(raw_wavs) == 32

    # 3) 检出段数 > 字表词数（字表仅 30 词，音频实有 32 词）：严禁静默截断，必须显式告警并保留 2 个 extra 片段
    short_wl = tmp_path / "short_30.txt"
    doc = load_wordlist_file(str(EXAMPLE_PTWL))
    all_32_words = [it["label"] for g in doc["groups"] for it in g["items"]]
    short_wl.write_text("\n".join(all_32_words[:30]) + "\n", encoding="utf-8")

    split_extra_dir = tmp_path / "split_mismatch_extra"
    res_extra = run_cli(f'tool_split "{MALE_WAV}" "{short_wl}" "{split_extra_dir}"')
    assert res_extra.returncode == 0, res_extra.stderr
    p_extra = json.loads(res_extra.stdout.strip())
    assert p_extra["success"] is True
    assert p_extra["status"] == "warning"
    assert p_extra["mismatch"] is True
    assert p_extra["detected_segments"] == 32
    assert p_extra["expected_words"] == 30
    assert p_extra["matched_count"] == 30
    assert p_extra["count"] == 32
    assert len(p_extra["extra_files"]) == 2
    assert len(p_extra["warnings"]) >= 1
    assert "警告" in p_extra["message"]
    saved_extra_wavs = sorted(split_extra_dir.glob("*extra*.wav"))
    assert len(saved_extra_wavs) == 2
    assert len(list(split_extra_dir.glob("*.wav"))) == 32

    # 4) 检出段数 < 字表词数（字表 35 词，音频 32 词）：必须显式告警并列出未匹配末尾词条
    long_wl = tmp_path / "long_35.txt"
    long_wl.write_text("\n".join(all_32_words + ["补充一", "补充二", "补充三"]) + "\n", encoding="utf-8")
    split_short_dir = tmp_path / "split_mismatch_missing"
    res_missing = run_cli(f'tool_split "{MALE_WAV}" "{long_wl}" "{split_short_dir}"')
    p_missing = json.loads(res_missing.stdout.strip())
    assert p_missing["success"] is True
    assert p_missing["status"] == "warning"
    assert p_missing["mismatch"] is True
    assert p_missing["unmatched_words"] == ["补充一", "补充二", "补充三"]
    assert len(p_missing["warnings"]) >= 1


    # 5) 支持显式传入 min_dur= / db= / merge_thresh= 及颠倒 <out_dir> <wordlist> 顺序，且未知 key=val 给出明确报错
    swapped_dir = tmp_path / "split_swapped_order"
    res_swapped = run_cli(
        f'tool_split "{MALE_WAV}" "{swapped_dir}" "{EXAMPLE_PTWL}" min_dur=0.10 db=25 merge_thresh=0.18 buffer=0.10'
    )
    p_swapped = json.loads(res_swapped.stdout.strip())
    assert p_swapped["success"] is True
    assert p_swapped["mismatch"] is False
    assert p_swapped["count"] == 32

    res_bad_opt = run_cli(f'tool_split "{MALE_WAV}" "{tmp_path / "bad_opt"}" unknown_opt=123')
    p_bad_opt = json.loads(res_bad_opt.stdout.strip())
    assert p_bad_opt["success"] is False
    assert "Unknown option" in p_bad_opt["error"]


def test_2_tool_merge_preserves_native_sample_rate(tmp_path: Path):
    """验证改进 2：tool_merge 合并 24kHz 音频时保持 24kHz 原采样率，不硬编码重采样为 44.1kHz。"""
    split_dir = tmp_path / "male_24k_parts"
    res_split = run_cli(f'tool_split "{MALE_WAV}" "{EXAMPLE_PTWL}" "{split_dir}"')
    assert json.loads(res_split.stdout.strip())["success"] is True

    part_files = sorted(split_dir.glob("*.wav"))[:6]
    merged_24k = tmp_path / "merged_24k.wav"
    cmd_files = " ".join(f'"{p}"' for p in part_files)
    res_merge = run_cli(f'tool_merge "{merged_24k}" 0.3 {cmd_files}')
    assert res_merge.returncode == 0, res_merge.stderr
    p_merge = json.loads(res_merge.stdout.strip())
    assert p_merge["success"] is True
    assert p_merge["sampling_frequency"] == 24000
    assert p_merge["resampled"] is False

    snd_merged = parselmouth.Sound(str(merged_24k))
    assert int(round(snd_merged.sampling_frequency)) == 24000

    # 支持显式指定 sr=16000 重采样
    merged_16k = tmp_path / "merged_16k.wav"
    res_16k = run_cli(f'tool_merge "{merged_16k}" 0.2 "{part_files[0]}" "{part_files[1]}" sr=16000')
    p_16k = json.loads(res_16k.stdout.strip())
    assert p_16k["success"] is True
    assert p_16k["sampling_frequency"] == 16000
    assert int(round(parselmouth.Sound(str(merged_16k)).sampling_frequency)) == 16000


def test_3_messy_txt_wordlist_unified_parsing_across_all_entrypoints(tmp_path: Path):
    """验证改进 3：杂乱草稿 .txt 在 tool_wordlist_convert、tool_split、tool_sort_batch 与 apply_wordlist 下结果完全一致。"""
    messy_txt = tmp_path / "messy_draft_tone16.txt"
    expected_words = _build_messy_draft_from_ptwl(EXAMPLE_PTWL, messy_txt)
    assert len(expected_words) == 32

    # 入口 A: tool_wordlist_convert
    converted_ptwl = tmp_path / "converted_from_messy.ptwl"
    res_conv = run_cli(f'tool_wordlist_convert "{messy_txt}" "{converted_ptwl}" title="草稿转正"')
    p_conv = json.loads(res_conv.stdout.strip())
    assert p_conv["success"] is True
    assert p_conv["group_count"] == 16
    assert p_conv["item_count"] == 32
    conv_doc = load_wordlist_file(str(converted_ptwl))
    assert [it["label"] for g in conv_doc["groups"] for it in g["items"]] == expected_words

    # 入口 B: tool_split 直接接收杂乱 .txt
    split_messy_dir = tmp_path / "split_from_messy"
    res_split = run_cli(f'tool_split "{MALE_WAV}" "{messy_txt}" "{split_messy_dir}"')
    p_split = json.loads(res_split.stdout.strip())
    assert p_split["success"] is True
    assert p_split["mismatch"] is False
    assert p_split["count"] == 32

    # 入口 C & D: apply_wordlist (长音频 + 短音频) 与 tool_sort_batch
    cli = PhonTracerCLI(quiet=True)
    try:
        cli.onecmd('add_speaker "F01_女1" gender=female --switch')
        cli.onecmd(f'load_long "{FEMALE1_WAV}"')
        cli.onecmd(f'apply_wordlist "{messy_txt}"')
        assert cli._last_emit_success is True
        assert cli._last_emit_payload["total"] == 32
        assert cli._last_emit_payload["group_count"] == 16
        f_labels = [it["label"] for it in cli.current_speaker.items.values()]
        assert f_labels == expected_words

        cli.onecmd('add_speaker "M01_男1" gender=male --switch')
        cli.onecmd(f'tool_sort_batch "{messy_txt}" "{split_messy_dir}" --apply')
        assert cli._last_emit_success is True
        assert cli._last_emit_payload["matched_count"] == 32
        cli.onecmd(f'apply_wordlist "{messy_txt}" order')
        assert cli._last_emit_success is True
        m_labels = [it["label"] for it in cli.current_speaker.items.values()]
        assert m_labels == expected_words
    finally:
        cli.cleanup_workspace()

    # 入口 E: 覆盖原实测极限手打草稿（含单侧不对称横幅 `=== 42_去阳`、紧贴 `#24_阳去`、独立冒号行 `23_阳上：`、`- 演讲`）
    devil_txt = tmp_path / "devil_zibiao.txt"
    devil_txt.write_text(
        "// 16组双字调录音词表（草稿）\n\n"
        "# 11_阴阴\n播音 ，   高峰  \n\n"
        "[ 12_阴阳 ]\n  资源、发明\n\n"
        "【13_阴上】\n发展 ； 音响\n\n\n"
        "=== 14_阴去 ===\n音乐\t方案\n\n"
        "［21_阳阴］\n1. 农村   2. 房间\n\n"
        "(22_阳阳)\n繁荣 , 人民\n\n"
        "23_阳上：\n联想\n门锁\n\n"
        "#24_阳去\n研制 / 决定  \n\n"
        "[31_上阴]\n首都    北京\n\n\n"
        "【 32_上阳 】\n改革、  语言\n\n"
        "（33_上上）\n- 演讲\n- 领导\n\n"
        "# 34_上去\n广大；讨论\n\n"
        "[41_去阴]\n认真 ，变通\n\n"
        "=== 42_去阳\n进行   复习 \n\n"
        "【43_去上】\n物理、汉语\n\n"
        "44_去去:\n变化 ，  创造\n",
        encoding="utf-8",
    )
    devil_ptwl = tmp_path / "devil_converted.ptwl"
    res_devil = run_cli(f'tool_wordlist_convert "{devil_txt}" "{devil_ptwl}"')
    p_devil = json.loads(res_devil.stdout.strip())
    assert p_devil["success"] is True
    assert p_devil["group_count"] == 16
    assert p_devil["item_count"] == 32
    devil_doc = load_wordlist_file(str(devil_ptwl))
    assert [g["name"] for g in devil_doc["groups"]][13] == "42_去阳"
    assert [it["label"] for g in devil_doc["groups"] for it in g["items"]] == expected_words

    # 首行即为横幅分组头（其下直接跟词条）时不得被误吞为总标题
    from modules.core.text_io import parse_messy_wordlist_data

    first_banner = parse_messy_wordlist_data("=== 11_阴阴 ===\n播音 高峰\n[12_阴阳]\n资源 发明\n")
    assert first_banner["title"] is None
    assert [g["group"] for g in first_banner["groups"]] == ["11_阴阴", "12_阴阳"]


def test_4_help_completeness_compactness_and_blackbox_tone16_bundle(tmp_path: Path):
    """验证改进 4：help all / agent_guide 补全 tool_* 且体积紧凑；黑盒跑批一键生成双字调图表与 .Collection。"""
    # 1) help all 与 help tools 包含全部 tool_* 命令，且输出紧凑不超终端截断线
    res_help_all = run_cli("help all")
    assert res_help_all.returncode == 0
    assert len(res_help_all.stdout.encode("utf-8")) < 14000
    h_all = json.loads(res_help_all.stdout.strip())
    for cmd in ("tool_split", "tool_merge", "tool_sort_batch", "tool_wordlist_convert", "export", "export_bundle"):
        assert cmd in h_all["commands"]

    res_help_tools = run_cli("help tools")
    h_tools = json.loads(res_help_tools.stdout.strip())
    assert set(h_tools["commands"].keys()) >= {"tool_split", "tool_merge", "tool_sort_batch", "tool_wordlist_convert"}

    res_guide = run_cli("agent_guide")
    assert len(res_guide.stdout.encode("utf-8")) < 13000
    guide = json.loads(res_guide.stdout.strip())
    for cmd in ("tool_split", "tool_merge", "tool_sort_batch", "tool_wordlist_convert", "export_bundle"):
        assert cmd in guide["core_command_cheatsheet"]

    # 2) export 非法 scope 参数明确报错（不静默回退）
    res_bad_scope = run_cli(f'export xlsx "{tmp_path / "x.xlsx"}" scope=invalid_xyz')
    p_bad_scope = json.loads(res_bad_scope.stdout.strip())
    assert p_bad_scope["success"] is False
    assert "invalid_xyz" in p_bad_scope["error"]

    # 3) 模拟真实 AI 子代理全黑盒单次 --script-file 处理 Example/wordlist_tone16
    messy_txt = tmp_path / "draft_wordlist.txt"
    _build_messy_draft_from_ptwl(EXAMPLE_PTWL, messy_txt)
    male_split_dir = tmp_path / "male_short_wavs"
    bundle_dir = tmp_path / "final_bundle"
    proj_path = bundle_dir / "tone16_blackbox.teproj"
    script_path = tmp_path / "pipeline.txt"

    script_lines = [
        f'tool_split "{MALE_WAV}" "{messy_txt}" "{male_split_dir}"',
        'add_speaker "F01_女1" gender=female --switch',
        f'load_long "{FEMALE1_WAV}"',
        f'apply_wordlist "{messy_txt}"',
        "detect_f0 recommended",
        'add_speaker "F02_女2" gender=female --switch',
        f'load_long "{FEMALE2_WAV}"',
        f'apply_wordlist "{messy_txt}"',
        "detect_f0 recommended",
        'add_speaker "M01_男1" gender=male --switch',
        f'load_batch "{male_split_dir}"',
        f'apply_wordlist "{messy_txt}" fuzzy',
        "detect_f0 recommended",
        "fix_warnings --apply scope=all",
        "stats by=group scope=integrated",
        f'export merged_wav out="{tmp_path / "male_remerged.wav"}" gap=0.3 target=active',
        f'export_bundle "{bundle_dir}" scope=integrated format=png title="普通话双字调黑盒交付"',
    ]
    script_path.write_text("\n".join(script_lines) + "\n", encoding="utf-8")

    res_pipe = run_cli("--project", str(proj_path), "--script-file", str(script_path))
    assert res_pipe.returncode == 0, f"stderr={res_pipe.stderr}\nstdout={res_pipe.stdout}"
    records = [json.loads(ln) for ln in res_pipe.stdout.strip().splitlines() if ln.strip()]
    assert all(r["success"] is True for r in records)

    # export merged_wav 也保持 24kHz 原采样率
    assert int(round(parselmouth.Sound(str(tmp_path / "male_remerged.wav")).sampling_frequency)) == 24000

    # 检查一站式闭环产物：工程、字表、Praat .Collection、双字调图表、分页图表无重复基础文件、报告
    col_files = list(bundle_dir.glob("*.Collection"))
    assert len(col_files) == 1
    loaded_col = parselmouth.praat.call("Read from file...", str(col_files[0]))
    assert len(loaded_col) >= 9  # 3 speakers * (Sound + TextGrid + Pitch)

    charts_dir = bundle_dir / "charts"
    assert (charts_dir / "duration.png").exists()
    assert (charts_dir / "tone_sandhi.png").exists()
    assert (charts_dir / "tone_effect_time.png").exists()
    assert (charts_dir / "quality.png").exists()
    assert (charts_dir / "overview_heatmap.png").exists()
    assert (charts_dir / "contour_第1页.png").exists()
    assert (charts_dir / "contour_第2页.png").exists()
    assert not (charts_dir / "contour.png").exists()
    assert (charts_dir / "distribution_第1页.png").exists()
    assert (charts_dir / "distribution_第2页.png").exists()
    assert not (charts_dir / "distribution.png").exists()
    assert (bundle_dir / "report" / "研究方法报告.md").exists()


def test_5_pagination_no_duplicate_and_fix_warnings_qc_alignment(tmp_path: Path):
    """验证改进 5：单项 export 分页返回精确文件列表且无重复同名首页图；fix_warnings 清零后与 quality 质控图口径一致。"""
    example_proj = EXAMPLE_DIR / "普通话双字组16种声调实验.teproj"
    work_proj = tmp_path / "qc_check.teproj"
    work_proj.write_bytes(example_proj.read_bytes())

    cli = PhonTracerCLI(project_path=str(work_proj), quiet=True)
    try:
        cli.onecmd("fix_warnings --apply scope=all")
        fw_res = cli._last_emit_payload
        assert fw_res["success"] is True
        assert fw_res["warnings_after"] == 0
        assert "qc_note" in fw_res
        assert "quality" in fw_res["qc_note"]

        # 单项导出多页 contour 图：返回 paginated=True, files=[第1页, 第2页]，且不存在无后缀 contour.png
        contour_out = tmp_path / "single_contour.png"
        cli.onecmd(f'export contour "{contour_out}" scope=integrated')
        exp_res = cli._last_emit_payload
        assert exp_res["success"] is True
        assert exp_res["paginated"] is True
        assert exp_res["page_count"] == 2
        assert len(exp_res["files"]) == 2
        assert not contour_out.exists()
        assert (tmp_path / "single_contour_第1页.png").exists()
        assert (tmp_path / "single_contour_第2页.png").exists()

        # 直接验证 quality 图渲染对象：工程警告清零后，红色“声学/切分警告”计数必须为 0，副标题包含统计离散说明
        from modules.acoustic_exporter import AcousticChartExporter
        from modules.cli_app.io_utils import AcousticChartCLIAdapter

        adapter = AcousticChartCLIAdapter(cli)
        all_speakers = cli.speaker_manager.get_all_speakers()
        exporter = AcousticChartExporter(project_tree=adapter, app=cli, all_speakers=all_speakers)
        exporter.params = {"chart_type": "quality", "export_scope": "integrated", "scale": "t_value"}

        entries = exporter._get_current_data_entries()
        assert len(entries) == 96
        assert all(
            not any(str(w).startswith(("[警告]", "[致命]", "[复核]", "[错误]")) for w in (e.get("warnings") or []))
            for e in entries
        )

        fig = exporter._plot_quality_check(entries)
        try:
            ax = fig.axes[0]
            legend = ax.get_legend()
            legend_labels = [t.get_text() for t in legend.get_texts()] if legend else []
            assert any("声学/切分警告轨迹 (0 项，已全部通过)" in lbl for lbl in legend_labels)
            assert all("需复核" not in lbl for lbl in legend_labels)
            sub_texts = [t.get_text() for t in ax.texts]
            assert any("口径说明：红实线 = 声学提取/切分警告" in s for s in sub_texts)
        finally:
            plt.close(fig)

        # 同步验证 spaghetti_qc：工程警告清零后，统计偏离曲线统一使用琥珀色虚线 (#D97706)，不得出现红色实线 (#DC2626) 声学警告线
        import matplotlib.colors as mcolors

        exporter.params = {"chart_type": "spaghetti_qc", "export_scope": "integrated", "scale": "T 值 (五度标调)"}
        fig_sp = exporter._plot_spaghetti_qc_overlay(entries[:18])
        try:
            for ax_s in [a for a in fig_sp.axes if a.get_visible()]:
                hex_colors = [mcolors.to_hex(ln.get_color()).upper() for ln in ax_s.get_lines()]
                assert "#DC2626" not in hex_colors
        finally:
            plt.close(fig_sp)
    finally:
        cli.cleanup_workspace()


def test_6_no_phonrec_residuals_and_release_packaging_integrity():
    """验证清理与打包配置：PhonRec 已彻底移除，pandas 已纳入打包依赖且未被排除，README/docs 无死链。"""
    import ast
    import re

    assert not (ROOT / "PhonRec").exists()
    assert not (ROOT / "docs" / "PHONREC_OPERATION_GUIDE.md").exists()
    assert not (ROOT / ".github" / "workflows" / "package-phonrec-windows.yml").exists()
    assert not (ROOT / ".github" / "workflows" / "package-phonrec-macos.yml").exists()
    assert not (ROOT / "tests" / "test_phonrec_quality.py").exists()

    # 检查全仓源码、脚本、打包配置、CI 与文档零 PhonRec 残留（排除本测试文件自身断言与构建输出目录）
    pat = re.compile(r"phon_?rec", re.IGNORECASE)
    this_file = Path(__file__).resolve()
    skip_dirs = {".git", "__pycache__", ".pytest_cache", "build", "dist", "_archive"}
    for p in sorted(ROOT.rglob("*")):
        if any(part in skip_dirs for part in p.parts) or p.resolve() == this_file:
            continue
        assert not pat.search(p.name), f"Residual PhonRec filename: {p}"
        if p.is_file() and p.suffix.lower() in {".py", ".md", ".yml", ".yaml", ".json", ".iss", ".spec", ".txt", ""}:
            txt = p.read_text(encoding="utf-8", errors="ignore")
            assert not pat.search(txt), f"Residual PhonRec reference inside {p}"

    # 检查 README.md 与 docs/*.md 中引用的本地相对资源/文档链接全部真实存在
    link_re = re.compile(r'(?:src="([^"]+)"|\[[^\]]+\]\(([^)#]+)(?:#[^)]*)?\))')
    for md_file in [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md"))]:
        content = md_file.read_text(encoding="utf-8")
        for m in link_re.finditer(content):
            rel = (m.group(1) or m.group(2) or "").strip()
            if not rel or rel.startswith(("http://", "https://", "mailto:")):
                continue
            target = (md_file.parent / rel).resolve()
            assert target.exists(), f"Broken relative link '{rel}' in {md_file}"

    # 检查 requirements.txt 与 PyInstaller spec 已正确打包 pandas（自定义脚本运行依赖）
    req_lines = [
        ln.split("#", 1)[0].split(";", 1)[0].strip().lower()
        for ln in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]
    assert any(r == "pandas" or r.startswith(("pandas>", "pandas=", "pandas<", "pandas~")) for r in req_lines)

    spec_code = (ROOT / "PhonTracer_Suite.spec").read_text(encoding="utf-8")
    compile(spec_code, "PhonTracer_Suite.spec", "exec")
    spec_tree = ast.parse(spec_code, filename="PhonTracer_Suite.spec")
    excluded_vals: list[str] = []
    hidden_vals: list[str] = []
    for node in spec_tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "excluded_modules" and isinstance(node.value, ast.List):
                    excluded_vals = [elt.value for elt in node.value.elts if isinstance(elt, ast.Constant) and isinstance(elt.value, str)]
                elif isinstance(t, ast.Name) and t.id == "base_hidden_imports" and isinstance(node.value, ast.List):
                    hidden_vals = [elt.value for elt in node.value.elts if isinstance(elt, ast.Constant) and isinstance(elt.value, str)]
    assert "pandas" not in excluded_vals, "pandas must NOT be excluded in PhonTracer_Suite.spec"
    assert "pandas" in hidden_vals, "pandas must be explicitly included in base_hidden_imports"

    res_ver = subprocess.run(
        [sys.executable, "scripts/check_release_version.py"],
        cwd=ROOT,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )
    assert res_ver.returncode == 0, res_ver.stderr
