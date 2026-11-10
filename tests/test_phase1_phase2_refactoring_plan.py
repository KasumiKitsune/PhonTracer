# -*- coding: utf-8 -*-
"""
PhonTracer 阶段一（Phase 1）与阶段二（Phase 2）重构验收与回归测试套件
覆盖 00_重构总纲与推荐执行路线图.md、01_全量Bug与声学统计缺陷排查清单.md、03_工具箱自定义脚本自由度与预览交互升级方案.md 的全部细项。
"""
import copy
import io
import json
import math
import os
import tempfile
import time
import zipfile
from pathlib import Path
from types import SimpleNamespace

import matplotlib
matplotlib.use("Agg", force=True)
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import pytest

from cli import AcousticChartCLIAdapter
from modules.acoustic_exporter import AcousticChartExporter
from modules.data_utils import (
    build_five_point_chart,
    compute_speaker_f0_bounds,
    hz_to_t_value,
)
from modules.project_tree import ProjectTreePanel
from modules.report_generator import (
    _extract_item_acoustic_record,
    compute_acoustic_statistics,
    fit_chao_tone_code,
    hz_to_chao_t,
)
from modules.script_api import (
    DatasetSnapshot,
    FigureResult,
    ProjectPatchResult,
    ScriptContext,
    TableResult,
    build_dataset_snapshot,
    configure_matplotlib_chinese_font,
)
from modules.script_manager import DEFAULT_SCRIPTS, get_scripts_dir, load_all_scripts, reset_builtin_scripts, save_script
from modules.script_prompt import generate_ai_prompt
from modules.script_runner import check_script_safety, run_custom_script, validate_ast
import toolkit


# ==============================================================================
# Phase 1 Tests: P0/P1 Bug Fixes & Acoustic / Statistical Remediation
# ==============================================================================

def test_phase1_toolkit_imports_and_dedup_and_vector_preview(tmp_path):
    """验证 BUG-S01 (import copy), BUG-S02 (import traceback), BUG-S08 (seen_fig_ids 缩进与 SVG/PDF 内存预览)"""
    assert hasattr(toolkit, "copy")
    assert hasattr(toolkit, "traceback")

    # Verify BUG-S08: seen_fig_ids deduplication and SVG/PDF preview PNG rendering
    app = toolkit.ToolkitApp.__new__(toolkit.ToolkitApp)
    app.local_scripts = [{"id": "s1", "name": "测试脚本", "description": "测试", "type": "chart"}]
    app.selected_script_id = "s1"
    app.loaded_teproj_path = None
    app.project_data = None
    app.btn_open_script_output = None
    logs = []
    app.append_script_log = lambda msg: logs.append(msg)
    app.get_script_output_dir = lambda name: str(tmp_path / "out")
    app.get_script_preview_dir = lambda out_dir: str(tmp_path / "preview")
    os.makedirs(tmp_path / "out", exist_ok=True)
    os.makedirs(tmp_path / "preview", exist_ok=True)
    shown_indices = []
    app.show_script_figure_at = lambda idx: shown_indices.append(idx)

    fig, ax = plt.subplots(figsize=(3, 2))
    ax.plot([0, 1], [1, 2])
    # Return the exact same figure object twice (e.g. .svg and .pdf) to test both SVG/PDF preview & fig_id dedup
    res = [
        FigureResult(fig, filename="vec_chart.pdf", title="矢量图PDF"),
        FigureResult(fig, filename="vec_chart_dup.pdf", title="重复同Figure"),
    ]
    app.display_script_result(res)

    assert len(app.script_figure_results) == 1
    cand = app.script_figure_results[0]
    assert cand["ext"] == ".pdf"
    assert cand["preview_path"].endswith(".png")
    assert os.path.exists(cand["preview_path"])
    assert shown_indices == [0]


def test_phase1_cli_adapter_filters_is_excluded():
    """验证 BUG-M10: cli.py AcousticChartCLIAdapter._get_items_by_group_for_dict 过滤 is_excluded 词条"""
    dummy_cli = SimpleNamespace(params={"pts": 11}, items={})
    adapter = AcousticChartCLIAdapter(dummy_cli)
    items_dict = {
        "k1": {"label": "有效词", "group": "阴平", "is_excluded": False},
        "k2": {"label": "废片词", "group": "阴平", "is_excluded": True},
        "k3": {"label": "全排除组词", "group": "阳平", "is_excluded": True},
    }
    grouped = dict(adapter._get_items_by_group_for_dict(items_dict))
    assert grouped == {"阴平": ["k1"]}
    assert "阳平" not in grouped


def test_phase1_unified_t_value_formula_and_bounds():
    """验证 BUG-M02: compute_speaker_f0_bounds 与 hz_to_t_value 四处统一映射到 [0.0, 5.0]"""
    f_min, f_max = 100.0, 400.0
    # Geometric midpoint 200 Hz -> log10(200) is halfway between log10(100) and log10(400) -> T = 2.5
    assert hz_to_t_value(100.0, f_min, f_max) == pytest.approx(0.0)
    assert hz_to_t_value(200.0, f_min, f_max) == pytest.approx(2.5)
    assert hz_to_t_value(400.0, f_min, f_max) == pytest.approx(5.0)
    assert hz_to_t_value(0.0, f_min, f_max) is None
    assert hz_to_t_value(-10.0, f_min, f_max) is None
    assert hz_to_t_value(np.nan, f_min, f_max) is None

    # report_generator.hz_to_chao_t must now return [0.0, 5.0] instead of [1.0, 5.0]
    assert hz_to_chao_t(100.0, f_min, f_max) == pytest.approx(0.0)
    assert hz_to_chao_t(200.0, f_min, f_max) == pytest.approx(2.5)
    assert hz_to_chao_t(400.0, f_min, f_max) == pytest.approx(5.0)

    # Array conversion with 0.0 -> np.nan
    arr_t = hz_to_t_value([0.0, 100.0, 200.0, 400.0], f_min, f_max)
    assert np.isnan(arr_t[0])
    assert arr_t[1] == pytest.approx(0.0)
    assert arr_t[2] == pytest.approx(2.5)
    assert arr_t[3] == pytest.approx(5.0)

    # Robust speaker bounds with octave outlier rejection
    rng = np.random.default_rng(42)
    clean_pool = rng.uniform(120.0, 240.0, size=60).tolist()
    with_outliers = clean_pool + [50.0, 580.0]  # half-freq & double-freq glitches
    b_min, b_max = compute_speaker_f0_bounds(with_outliers, method="p5_p95_iqr")
    assert 115.0 <= b_min <= 140.0
    assert 220.0 <= b_max <= 245.0


def test_phase1_hz_mode_cleans_zeros_to_nan_and_cache_key_and_try_finally():
    """验证 BUG-M03 (Hz 模式清洗 <=0 为 np.nan)、BUG-M13 (复合缓存键)、BUG-M14 (try...finally 还原 items)"""
    mock_tree = SimpleNamespace(
        app_state_params={"analysis_mode": "f0", "pts": 5},
        items={"orig": {"label": "原始"}},
        _get_items_by_group_for_dict=lambda items: [("阴平", list(items.keys()))],
        _ensure_item_loaded=lambda item: None,
        _extract_syl_data=lambda item, n: (0.3, [(0.3, [200.0, 0.0, 210.0, 0.0, 220.0])]),
        _get_pitch_arrays_for_item=lambda item: (np.linspace(0.0, 0.3, 5), np.array([200.0, 0.0, 210.0, 0.0, 220.0])),
    )
    spk = SimpleNamespace(
        id="spk_1",
        name="女1",
        items={
            "item_1": {
                "label": "妈",
                "group": "阴平",
                "start": 0.0,
                "end": 0.3,
                "pitch_data": {"xs": [0.0, 0.1, 0.2], "freqs": [200.0, 210.0, 220.0]},
            }
        },
    )
    exporter = AcousticChartExporter(mock_tree, all_speakers=[spk])
    entries = exporter._extract_active_data([spk], filter_groups=False, apply_chart_grouping=False)
    assert len(entries) == 1
    dur, clean_hz = entries[0]["syl_data"][0]
    # 0.0 must be replaced with np.nan so Hz mode does not average 0 Hz!
    assert np.isnan(clean_hz[1]) and np.isnan(clean_hz[3])
    assert np.nanmean(clean_hz) == pytest.approx(210.0)

    # Verify composite cache key includes ('spk_1', 'f0', 5)
    assert ("spk_1", "f0", 5) in exporter._speaker_data_cache
    # Verify project_tree.items was restored
    assert "orig" in mock_tree.items


def test_phase1_lobanov_per_speaker_and_confidence_ellipse_and_offset_points():
    """验证 BUG-M01 (按发音人独立计算 Lobanov Z-score)、BUG-M06 (椭圆无硬裁剪+卡方半径)、BUG-M07 (offset points 标签)"""
    mock_tree = SimpleNamespace(app_state_params={"analysis_mode": "formant", "pts": 5}, items={})
    exporter = AcousticChartExporter(mock_tree)

    # Construct Male speaker (lower formants) and Female speaker (higher formants, 1.25x scaled)
    vowels_male = [("i", 300.0, 2200.0), ("a", 800.0, 1200.0), ("u", 350.0, 800.0)]
    ref_entries = []
    for v_char, f1_m, f2_m in vowels_male:
        ref_entries.append({
            "speaker_name": "男1",
            "group": v_char,
            "label": v_char,
            "raw_xs": np.linspace(0, 0.2, 5),
            "raw_f1": np.full(5, f1_m),
            "raw_f2": np.full(5, f2_m),
            "syl_formants": [{"syllable_index": 0, "char": v_char, "bounds": [0, 0.2], "f1": [f1_m] * 5, "f2": [f2_m] * 5}],
        })
        ref_entries.append({
            "speaker_name": "女1",
            "group": v_char,
            "label": v_char,
            "raw_xs": np.linspace(0, 0.2, 5),
            "raw_f1": np.full(5, f1_m * 1.25),
            "raw_f2": np.full(5, f2_m * 1.25),
            "syl_formants": [{"syllable_index": 0, "char": v_char, "bounds": [0, 0.2], "f1": [f1_m * 1.25] * 5, "f2": [f2_m * 1.25] * 5}],
        })

    norm_fn = exporter._build_formant_normalizer(ref_entries, "Lobanov z-score")
    z_m_f1, z_m_f2 = norm_fn([300.0, 800.0, 350.0], [2200.0, 1200.0, 800.0], speaker_name="男1")
    z_f_f1, z_f_f2 = norm_fn([300.0 * 1.25, 800.0 * 1.25, 350.0 * 1.25], [2200.0 * 1.25, 1200.0 * 1.25, 800.0 * 1.25], speaker_name="女1")

    # After per-speaker Lobanov normalization, each speaker's vowel centroid is (0, 0) and male/female normalized values match identically!
    assert np.mean(z_m_f1) == pytest.approx(0.0, abs=1e-6)
    assert np.mean(z_f_f1) == pytest.approx(0.0, abs=1e-6)
    assert np.allclose(z_m_f1, z_f_f1, atol=1e-5)
    assert np.allclose(z_m_f2, z_f_f2, atol=1e-5)

    # Test confidence ellipse and offset points annotation in _draw_formant_space_panel
    fig, ax = plt.subplots()
    exporter._draw_formant_space_panel(
        ax,
        ref_entries,
        groupby_val="group",
        label_mode="显示分组标签",
        ellipse_mode="95% 置信椭圆",
        show_raw=True,
        time_gradient=False,
        density_overlay=False,
        transform_fn=norm_fn,
    )
    annotations = [c for c in ax.get_children() if isinstance(c, matplotlib.text.Annotation)]
    assert len(annotations) >= 3
    for ann in annotations:
        assert ann.anncoords == "offset points"
    plt.close(fig)


def test_phase1_multisyllable_report_t_code_and_five_point_chart(tmp_path):
    """验证 BUG-M09 (报告多音节按音节分段拟合 t_code 如 55-51) 与 BUG-M04 (五度图多音节绘制)"""
    xs = np.linspace(0.0, 0.4, 40)
    # First syllable (0.0~0.2s): high level 280 Hz; Second syllable (0.2~0.4s): falling 280 -> 110 Hz
    freqs = np.concatenate([np.full(20, 280.0), np.linspace(280.0, 110.0, 20)])
    state = {
        "active_speaker_id": "s1",
        "speakers": {
            "s1": {
                "id": "s1",
                "name": "发音人1",
                "last_params": {"pts": 11},
                "items": {
                    "i1": {
                        "label": "高降",
                        "group": "阴去",
                        "start": 0.0,
                        "end": 0.4,
                        "chars_bounds": [[0.0, 0.2], [0.2, 0.4]],
                        "pitch_data": {"xs": xs.tolist(), "freqs": freqs.tolist()},
                        "formant_data": {
                            "xs": xs.tolist(),
                            "f1": ([400.0] * 20) + ([700.0] * 20),
                            "f2": ([2000.0] * 20) + ([1200.0] * 20),
                        },
                    }
                },
            }
        },
    }
    stats = compute_acoustic_statistics(state, by="group", scope="integrated")
    grp_row = stats["summary"][0]
    assert "-" in grp_row["t_code"], f"Expected hyphenated multi-syllable t_code, got {grp_row['t_code']}"
    parts = grp_row["t_code"].split("-")
    assert len(parts) == 2

    # Check build_five_point_chart writes 22 points (2 syllables * 11 points)
    import xlsxwriter
    xlsx_file = str(tmp_path / "five_pt.xlsx")
    wb = xlsxwriter.Workbook(xlsx_file)
    ws = wb.add_worksheet("分析结果")
    dict_data = {
        "阴去": {
            "syl_dur_sums": [0.2, 0.25],
            "syl_counts": [1, 1],
            "f0_sums": [[280.0] * 11, np.linspace(280.0, 110.0, 11).tolist()],
            "f0_counts": [[1] * 11, [1] * 11],
        }
    }
    avg_points_map = {"阴去": [[280.0] * 11, np.linspace(280.0, 110.0, 11).tolist()]}
    build_five_point_chart(wb, ws, dict_data, avg_points_map, 11, 2, 110.0, 280.0)
    wb.close()
    assert os.path.exists(xlsx_file)


def test_phase1_script_manager_preserves_user_edits_to_builtin(monkeypatch, tmp_path):
    """验证 BUG-S05: load_all_scripts 不会无条件覆写用户修改过的 builtin_* 脚本"""
    monkeypatch.setattr("modules.script_manager.get_scripts_dir", lambda: str(tmp_path))
    scripts_1 = load_all_scripts()
    builtin_id = DEFAULT_SCRIPTS[0]["id"]
    # Simulate user modifying the builtin script
    custom_code = "# 脚本名称：用户修改版\n# 功能说明：保留修改\ndef run(ctx):\n    return None\n"
    save_script(builtin_id, "用户修改版", "保留修改", "chart", custom_code)

    # Reloading scripts must NOT overwrite user's edit
    scripts_2 = load_all_scripts()
    matched = [s for s in scripts_2 if s["id"] == builtin_id][0]
    assert matched["name"] == "用户修改版"
    assert "用户修改版" in matched["code"]

    # Explicit reset_builtin_scripts restores default
    reset_builtin_scripts()
    scripts_3 = load_all_scripts()
    matched_reset = [s for s in scripts_3 if s["id"] == builtin_id][0]
    assert matched_reset["name"] == DEFAULT_SCRIPTS[0]["name"]


# ==============================================================================
# Phase 2 Tests: Pre-Expanded Tables, High-Order Phonetic Ops, LRU Cache & Sandbox
# ==============================================================================

def test_phase2_dataset_snapshot_tables_and_v2_metadata_alignment():
    """验证 Phase 2 DatasetSnapshot.to_point_table / to_syllable_table / to_frame_table 与 BUG-S03 v2 字段对齐"""
    root_dir = Path(__file__).resolve().parents[1]
    teproj_path = root_dir / "Example" / "wordlist_tone16" / "普通话双字组16种声调实验.teproj"
    items = build_dataset_snapshot(str(teproj_path))
    assert len(items) == 96

    # Check BUG-S03 v2 field alignment on snapshot items
    first = items[0]
    for key in ("tags", "item_tags", "fields", "custom_fields", "item_meta", "note", "item_note", "word", "label"):
        assert key in first

    snap = DatasetSnapshot(items)

    # 1. to_point_table performance & columns
    t0 = time.perf_counter()
    df_pts = snap.to_point_table(included_only=True, as_df=True)
    t_first = time.perf_counter() - t0

    t1 = time.perf_counter()
    df_pts_cached = snap.to_point_table(included_only=True, as_df=True)
    t_cached = time.perf_counter() - t1

    assert t_cached < 0.05, f"Cached to_point_table took {t_cached:.4f}s (> 50ms)"
    assert len(df_pts) == 96 * 2 * 11  # 96 disyllabic items * 2 syllables * 11 points = 2112 rows
    assert len(df_pts_cached) == len(df_pts)
    for col in (
        "speaker_id", "speaker_name", "item_id", "word", "char", "syl_idx", "syl_count",
        "group", "point_idx", "norm_time", "phys_time_ms", "f0_hz", "t_value", "semitone",
        "f1_hz", "f2_hz", "f3_hz", "bark_f1", "bark_f2", "lobanov_f1", "lobanov_f2", "tags",
    ):
        assert col in df_pts.columns
    # Verify point_idx is 1..11 and no <= 0 Hz values exist
    assert set(df_pts["point_idx"].unique()) == set(range(1, 12))
    valid_f0 = df_pts["f0_hz"].dropna()
    assert (valid_f0 > 0).all()

    # 2. to_syllable_table
    df_syl = snap.to_syllable_table(included_only=True, as_df=True)
    assert len(df_syl) == 96 * 2  # 192 syllables
    for col in (
        "speaker_id", "speaker_name", "item_id", "word", "char", "syl_idx", "syl_count",
        "group", "dur_ms", "f0_mean", "f0_min", "f0_max", "f0_range", "f0_slope",
        "t_onset", "t_mid", "t_offset", "t_range", "t_code",
        "f1_mid", "f2_mid", "f3_mid", "f1_20", "f2_20", "f1_80", "f2_80", "tl_hz", "tags",
    ):
        assert col in df_syl.columns

    # 3. to_frame_table
    df_frm = snap.to_frame_table(items[:2], as_df=True)
    assert len(df_frm) > 20
    for col in ("speaker_name", "item_id", "word", "syl_idx", "time_s", "rel_time", "f0_hz", "f1_hz", "f2_hz", "f3_hz"):
        assert col in df_frm.columns


def test_phase2_long_audio_lru_cache_speedup():
    """验证 Phase 2 BUG-S06: 8 宫格长音频语谱图二次运行耗时显著降低 (>80% 加速)"""
    root_dir = Path(__file__).resolve().parents[1]
    teproj_path = root_dir / "Example" / "wordlist_tone16" / "普通话双字组16种声调实验.teproj"
    items = build_dataset_snapshot(str(teproj_path))

    from modules.script_api import _GLOBAL_PROJECT_JSON_CACHE, _GLOBAL_SOUND_LRU_CACHE
    _GLOBAL_PROJECT_JSON_CACHE.clear()
    _GLOBAL_SOUND_LRU_CACHE.clear()

    ctx1 = ScriptContext(items, teproj_path=str(teproj_path))
    t0 = time.perf_counter()
    for it in items[:8]:
        snd = ctx1.load_item_sound(it)
        assert snd.get_total_duration() > 0.1
    first_8_time = time.perf_counter() - t0

    ctx2 = ScriptContext(items, teproj_path=str(teproj_path))
    t1 = time.perf_counter()
    for it in items[:8]:
        snd = ctx2.load_item_sound(it)
        assert snd.get_total_duration() > 0.1
    second_8_time = time.perf_counter() - t1

    # Second run hits the in-memory Sound cache without unzipping/writing temp WAVs
    assert second_8_time < first_8_time * 0.2, f"Expected >80% speedup, got {first_8_time:.4f}s -> {second_8_time:.4f}s"


def test_phase2_high_order_phonetic_operators_and_sandbox():
    """验证 Phase 2 高阶声学特征算子 (extract_voice_quality / extract_spectral_moments / normalize_formants) 与 AST 沙箱规则"""
    root_dir = Path(__file__).resolve().parents[1]
    teproj_path = root_dir / "Example" / "wordlist_tone16" / "普通话双字组16种声调实验.teproj"
    items = build_dataset_snapshot(str(teproj_path))

    script_code = """
import pandas as pd
from scipy.stats import gaussian_kde
from collections import namedtuple

Pt = namedtuple("Pt", ["x", "y"])

def run(ctx):
    # 1. Verify single-underscore _asdict() is allowed (BUG-S04)
    p = Pt(1.0, 2.0)
    d = p._asdict()
    assert d["x"] == 1.0

    # 2. Verify to_point_table and pandas groupby
    df = ctx.dataset.to_point_table()
    grp_summary = df.groupby(["group", "point_idx"])["t_value"].mean().reset_index()
    assert len(grp_summary) > 0

    # 3. Verify extract_voice_quality & extract_spectral_moments
    item0 = ctx.dataset.included_items()[0]
    vq = ctx.extract_voice_quality(item0, syl_idx=0)
    sm = ctx.extract_spectral_moments(item0, pre_emphasis=80.0)
    for k in ("jitter_local", "shimmer_local", "hnr_db", "cpp_db", "h1_h2_db", "h1_a1_db", "h1_a3_db"):
        assert k in vq
    for k in ("cog_hz", "sd_hz", "skewness", "kurtosis"):
        assert k in sm
    assert sm["cog_hz"] > 0

    # 4. Verify normalize_formants across 5 methods
    mock_df = pd.DataFrame([
        {"speaker_name": "S1", "f1_hz": 300.0, "f2_hz": 2200.0},
        {"speaker_name": "S1", "f1_hz": 750.0, "f2_hz": 1200.0},
        {"speaker_name": "S1", "f1_hz": 350.0, "f2_hz": 850.0},
    ])
    for m in ("lobanov", "nearey1", "bark", "erb", "watt_fabricius"):
        normed = ctx.normalize_formants(mock_df, method=m)
        assert "f1_norm" in normed.columns and "f2_norm" in normed.columns

    return ctx.table(grp_summary.head(5), title="汇总前5行")
"""
    res, logs, err = run_custom_script(script_code, items, timeout=15, teproj_path=str(teproj_path))
    assert err is None, f"Unexpected script error: {err}"
    assert isinstance(res, TableResult)
    assert len(res.rows) == 5

    # Verify prompt template includes new Phase 2 APIs
    prompt_txt = generate_ai_prompt({}, {"script_type": "chart", "prompt_mode": "Agent协作"})
    assert "to_point_table" in prompt_txt
    assert "to_syllable_table" in prompt_txt
    assert "extract_voice_quality" in prompt_txt
    assert "extract_spectral_moments" in prompt_txt


# ============================================================================
# Phase 3 & 4 (Exporter Refactoring & Publication Styles)
# ============================================================================
# -*- coding: utf-8 -*-
"""
PhonTracer 阶段三 & 阶段四：主程序图表导出器重构与学术样式规范 单元测试套件
覆盖：
1. 顶层双模式分流 (work_mode: publication / qc) 与三套一键期刊出版样式预设 (chinese_journal / intl_journal / presentation)
2. 正交分组参数 (color_by / facet_by) 与向后兼容映射，以及三步式左侧面板 + 底部独立导出控制栏
3. 模式 B【数据质检与异常排查】三大图表强化：词条级逐行偏差热图、组内个体原始曲线叠加图 (2σ离群高亮标注)、双联综合体检面板
4. 模式 A【学术论文图表】“3 + 3”核心图表升级：
   - 声调 1：五度标尺与自动五度值标注、Spaghetti + KDE 背景
   - 声调 2：二字组连读变调分面网格图、组合矩阵图、η²效应图
   - 声调 3：声调声学特征分布雨云图 (Raincloud) 与 8 项声学指标提取
   - 共振峰 1：元音空间外围凸包与面积 (VSA)、复合元音 20%->80% 方向箭头与动程长度 (TL)
   - 共振峰 2：共振峰时序轨迹图 F3 第三共振峰叠加、音节位置筛选、多声学单位切换 (Hz/Bark/ERB/Lobanov)
   - 共振峰 3：元音对立重叠度矩阵 (Pillai Score & 欧氏距离) 与动程/时长雨云图
"""
import os
from types import SimpleNamespace

import matplotlib
matplotlib.use("Agg", force=True)
import matplotlib.colors as mcolors
import matplotlib.pyplot as plt
import numpy as np
import pytest

from modules.acoustic_exporter import (
    PUBLICATION_PRESETS,
    AcousticChartExportDialog,
    AcousticChartExporter,
    _compute_2d_convex_hull_and_area,
    _compute_pillai_bartlett_trace,
)


# ==============================================================================
# Fixtures & Synthetic Dataset Builders
# ==============================================================================

def _make_tone_exporter_and_entries():
    """构建包含单字调与二字组连读调（含正常词条、离群八度跳变词条、缺失点词条）的测试数据集。"""
    num_pts = 11
    xs = np.linspace(0.0, 0.4, 40)

    def _extract_syl(item, n):
        syls = item.get("_syl_contours", [(0.2, np.full(n, 200.0).tolist())])
        tot = sum(s[0] for s in syls)
        return tot, syls

    def _get_pitch(item):
        return np.asarray(item["pitch_data"]["xs"], dtype=float), np.asarray(item["pitch_data"]["freqs"], dtype=float)

    def _get_syl_bounds(item):
        b = item.get("chars_bounds") or [[0.0, 0.2]]
        return [item.get("label", "字")], b

    def _extract_kde(p_xs, p_freqs, c_s, c_e, n_dense):
        return np.linspace(float(np.nanmean(p_freqs)), float(np.nanmean(p_freqs)) + 10.0, n_dense)

    items_spk1 = {
        "item_0": {
            "label": "播音",
            "group": "11_阴阴",
            "start": 0.0,
            "end": 0.4,
            "chars_bounds": [[0.0, 0.2], [0.2, 0.4]],
            "pitch_data": {"xs": xs, "freqs": np.full(40, 260.0)},
            "_syl_contours": [(0.2, np.full(num_pts, 260.0).tolist()), (0.2, np.full(num_pts, 262.0).tolist())],
            "item_meta": {"调类": "高平", "韵母": "o"},
            "warnings": [],
        },
        "item_1": {
            "label": "高峰",
            "group": "11_阴阴",
            "start": 0.0,
            "end": 0.4,
            "chars_bounds": [[0.0, 0.2], [0.2, 0.4]],
            "pitch_data": {"xs": xs, "freqs": np.full(40, 258.0)},
            "_syl_contours": [(0.2, np.full(num_pts, 258.0).tolist()), (0.2, np.full(num_pts, 260.0).tolist())],
            "item_meta": {"调类": "高平", "韵母": "ao"},
            "warnings": [],
        },
        "item_2": {
            "label": "飞机",
            "group": "11_阴阴",
            "start": 0.0,
            "end": 0.4,
            "chars_bounds": [[0.0, 0.2], [0.2, 0.4]],
            "pitch_data": {"xs": xs, "freqs": np.full(40, 261.0)},
            "_syl_contours": [(0.2, np.full(num_pts, 261.0).tolist()), (0.2, np.full(num_pts, 259.0).tolist())],
            "item_meta": {"调类": "高平", "韵母": "ei"},
            "warnings": [],
        },
        "item_outlier": {
            "label": "跳变词",
            "group": "11_阴阴",
            "start": 0.0,
            "end": 0.4,
            "chars_bounds": [[0.0, 0.2], [0.2, 0.4]],
            "pitch_data": {"xs": xs, "freqs": np.full(40, 120.0)},
            "_syl_contours": [(0.2, np.full(num_pts, 120.0).tolist()), (0.2, np.full(num_pts, 125.0).tolist())],
            "item_meta": {"调类": "异常", "韵母": "iao"},
            "warnings": ["[警告] 疑似半频跳变"],
        },
        "item_3": {
            "label": "资源",
            "group": "12_阴阳",
            "start": 0.0,
            "end": 0.4,
            "chars_bounds": [[0.0, 0.2], [0.2, 0.4]],
            "pitch_data": {"xs": xs, "freqs": np.linspace(255.0, 180.0, 40)},
            "_syl_contours": [
                (0.2, np.full(num_pts, 255.0).tolist()),
                (0.2, np.linspace(160.0, 245.0, num_pts).tolist()),
            ],
            "item_meta": {"调类": "平升", "韵母": "i"},
            "warnings": [],
        },
        "item_4": {
            "label": "人民",
            "group": "21_阳阴",
            "start": 0.0,
            "end": 0.4,
            "chars_bounds": [[0.0, 0.2], [0.2, 0.4]],
            "pitch_data": {"xs": xs, "freqs": np.linspace(150.0, 255.0, 40)},
            "_syl_contours": [
                (0.2, np.linspace(150.0, 240.0, num_pts).tolist()),
                (0.2, np.full(num_pts, 255.0).tolist()),
            ],
            "item_meta": {"调类": "升平", "韵母": "en"},
            "warnings": [],
        },
        "item_5": {
            "label": "繁荣",
            "group": "22_阳阳",
            "start": 0.0,
            "end": 0.4,
            "chars_bounds": [[0.0, 0.2], [0.2, 0.4]],
            "pitch_data": {"xs": xs, "freqs": np.linspace(150.0, 240.0, 40)},
            "_syl_contours": [
                (0.2, np.linspace(150.0, 235.0, num_pts).tolist()),
                (0.2, np.linspace(155.0, 240.0, num_pts).tolist()),
            ],
            "item_meta": {"调类": "升升", "韵母": "an"},
            "warnings": [],
        },
    }

    mock_tree = SimpleNamespace(
        app_state_params={"analysis_mode": "f0", "pts": num_pts},
        items=items_spk1,
        _get_items_by_group_for_dict=lambda items: [
            (g, [k for k, v in items.items() if v["group"] == g])
            for g in ["11_阴阴", "12_阴阳", "21_阳阴", "22_阳阳"]
        ],
        _ensure_item_loaded=lambda item: None,
        _extract_syl_data=_extract_syl,
        _get_pitch_arrays_for_item=_get_pitch,
        _get_syllables_and_bounds=_get_syl_bounds,
        _extract_kde_contour=_extract_kde,
    )
    spk1 = SimpleNamespace(id="spk_f1", name="女1", items=items_spk1)
    exporter = AcousticChartExporter(mock_tree, all_speakers=[spk1])
    entries = exporter._extract_active_data([spk1], filter_groups=False)
    return exporter, entries


def _make_formant_exporter_and_entries():
    """构建包含单元音 /i, a, u/、合并元音 /i_near/ 及双元音滑动轨迹 (含 F1/F2/F3) 的测试数据集。"""
    num_pts = 11
    xs = np.linspace(0.0, 0.4, 44)
    rng = np.random.default_rng(123)

    items = {}
    vowel_specs = [
        ("i", "衣", 310.0, 315.0, 2350.0, 2340.0, 3100.0),
        ("a", "阿", 820.0, 810.0, 1250.0, 1260.0, 2650.0),
        ("u", "乌", 340.0, 345.0, 780.0, 790.0, 2450.0),
        ("ai", "哀", 800.0, 350.0, 1300.0, 2200.0, 2800.0),
        ("i_near", "伊", 312.0, 316.0, 2345.0, 2338.0, 3095.0),
    ]

    idx = 0
    for grp, lbl_pre, f1_s, f1_e, f2_s, f2_e, f3_m in vowel_specs:
        for rep in range(4):
            jitter_f1 = rng.normal(0.0, 8.0)
            jitter_f2 = rng.normal(0.0, 15.0)
            if grp == "i" and rep == 3:
                jitter_f1 = 380.0
                jitter_f2 = -600.0
            f1_curve = np.linspace(f1_s + jitter_f1, f1_e + jitter_f1, 44)
            f2_curve = np.linspace(f2_s + jitter_f2, f2_e + jitter_f2, 44)
            f3_curve = np.linspace(f3_m, f3_m + 40.0, 44)
            items[f"f_item_{idx}"] = {
                "label": f"{lbl_pre}{rep+1}字",
                "group": grp,
                "start": 0.0,
                "end": 0.4,
                "chars_bounds": [[0.0, 0.2], [0.2, 0.4]],
                "formant_data": {
                    "xs": xs,
                    "f1": f1_curve,
                    "f2": f2_curve,
                    "f3": f3_curve,
                },
                "warnings": ["[警告] 共振峰跳变"] if (grp == "i" and rep == 3) else [],
            }
            idx += 1

    groups_order = ["i", "a", "u", "ai", "i_near"]
    mock_tree = SimpleNamespace(
        app_state_params={"analysis_mode": "formant", "pts": num_pts, "show_f3": True},
        items=items,
        _get_items_by_group_for_dict=lambda d: [
            (g, [k for k, v in d.items() if v["group"] == g]) for g in groups_order
        ],
        _ensure_item_loaded=lambda item: None,
    )
    spk = SimpleNamespace(id="spk_v1", name="女1", items=items)
    exporter = AcousticChartExporter(mock_tree, all_speakers=[spk])
    entries = exporter._extract_active_formant_data([spk], filter_groups=False)
    return exporter, entries


# ==============================================================================
# 1. 顶层双模式分流与三套一键期刊出版样式预设测试
# ==============================================================================

def test_work_mode_routing_and_three_publication_presets():
    exporter, entries = _make_tone_exporter_and_entries()

    # 1) Work mode routing: publication vs qc
    pub_tone_charts = exporter._get_tone_chart_type_values(entries, work_mode="publication")
    qc_tone_charts = exporter._get_tone_chart_type_values(entries, work_mode="qc")
    assert "1. 声调格局与时序轮廓图" in pub_tone_charts
    assert "2. 连读变调与协同发音图" in pub_tone_charts
    assert "3. 声调声学特征分布雨云图" in pub_tone_charts
    assert qc_tone_charts == [
        "1. 词条级逐行质检热图",
        "2. 组内个体原始曲线叠加图",
        "3. 检出率与发音人基频体检图",
    ]

    pub_formant_charts = exporter._get_formant_chart_type_values(work_mode="publication")
    qc_formant_charts = exporter._get_formant_chart_type_values(work_mode="qc")
    assert "1. 元音声学空间图" in pub_formant_charts
    assert "2. 共振峰时序轨迹图" in pub_formant_charts
    assert "3. 元音对立重叠度与分布图" in pub_formant_charts
    assert "1. 词条级逐行质检热图" in qc_formant_charts

    # 2) Preset 1: chinese_journal
    p_cn = exporter.apply_publication_preset("chinese_journal")
    assert p_cn["name"] == "chinese_journal"
    assert p_cn["fig_facecolor"] == "#FFFFFF"
    assert p_cn["triple_redundant_encoding"] is True
    assert p_cn["tick_direction"] == "in"
    assert p_cn["show_title"] is False
    assert p_cn["single_col_mm"] == 80.0 and p_cn["double_col_mm"] == 150.0
    assert plt.rcParams["pdf.fonttype"] == 42
    assert any("SimSun" in f or "Songti" in f for f in plt.rcParams["font.sans-serif"])
    assert "Times New Roman" in plt.rcParams["font.sans-serif"]
    assert "Charis SIL" in plt.rcParams["font.sans-serif"]

    exporter.params = {"chart_type": "contour", "preset_theme": "chinese_journal", "show_chao_five_level_axis": False}
    fig_cn = exporter.generate_plot(entries, is_preview=False)
    ax_cn = fig_cn.axes[0]
    assert mcolors.to_hex(fig_cn.patch.get_facecolor()).upper() == "#FFFFFF"
    assert mcolors.to_hex(ax_cn.get_facecolor()).upper() == "#FFFFFF"
    assert ax_cn.get_title() == ""
    assert all(ax_cn.spines[s].get_visible() for s in ("top", "right", "bottom", "left"))
    data_lines = [ln for ln in ax_cn.get_lines() if ln.get_label() and not ln.get_label().startswith("_")]
    assert len(data_lines) >= 2
    assert len({ln.get_linestyle() for ln in data_lines}) >= 2
    assert len({ln.get_marker() for ln in data_lines}) >= 2
    plt.close(fig_cn)

    # Multi-subplot (a), (b) auto-labeling in chinese_journal
    exporter.params = {"chart_type": "contour", "preset_theme": "chinese_journal", "facet_by": "group"}
    fig_cn_multi = exporter.generate_plot(entries, is_preview=False)
    vis_axes = [a for a in fig_cn_multi.axes if a.get_visible() and not getattr(a, "_is_chao_axis", False)]
    assert len(vis_axes) >= 2
    assert vis_axes[0].get_title(loc="left").startswith("(a)")
    assert vis_axes[1].get_title(loc="left").startswith("(b)")
    plt.close(fig_cn_multi)

    # 3) Preset 2: intl_journal (Okabe-Ito palette + Despine + frameless legend with error band annotation)
    p_intl = exporter.apply_publication_preset("intl_journal")
    assert p_intl["colors"] == [
        "#0072B2", "#D55E00", "#009E73", "#CC79A7",
        "#F0E442", "#56B4E9", "#E69F00", "#000000",
    ]
    exporter.params = {
        "chart_type": "contour",
        "preset_theme": "intl_journal",
        "contour_content": "95% CI",
        "show_chao_five_level_axis": False,
    }
    fig_intl = exporter.generate_plot(entries, is_preview=False)
    ax_intl = fig_intl.axes[0]
    assert ax_intl.spines["top"].get_visible() is False
    assert ax_intl.spines["right"].get_visible() is False
    assert ax_intl.get_title() == ""
    leg = ax_intl.get_legend()
    assert leg is not None
    assert leg.get_frame_on() is False
    assert "Mean ± 95% CI" in leg.get_title().get_text()
    plt.close(fig_intl)

    # 4) Preset 3: presentation (16:9 widescreen, lw >= 2.5, 13-14pt font, retains top title)
    p_pres = exporter.apply_publication_preset("presentation")
    assert p_pres["show_title"] is True
    assert p_pres["linewidth"] == pytest.approx(2.5)
    assert 13.0 <= p_pres["base_fontsize"] <= 14.5
    exporter.params = {"chart_type": "contour", "preset_theme": "presentation"}
    fig_pres = exporter.generate_plot(entries, is_preview=False)
    w_in, h_in = fig_pres.get_size_inches()
    assert (w_in / h_in) == pytest.approx(16.0 / 9.0, rel=0.05)
    ax_pres = fig_pres.axes[0]
    assert ax_pres.get_title() != ""
    main_lines = [ln for ln in ax_pres.get_lines() if ln.get_label() and not ln.get_label().startswith("_")]
    assert all(ln.get_linewidth() >= 2.5 for ln in main_lines)
    plt.close(fig_pres)


def test_layout_size_mm_and_praat_axes_orientation(tmp_path):
    """测试单栏 80mm / 双栏 150mm / 170mm 物理尺寸换算及元音空间图 Praat 方位坐标轴。"""
    f_exporter, f_entries = _make_formant_exporter_and_entries()
    f_exporter.params = {
        "chart_type": "formant_space",
        "preset_theme": "intl_journal",
        "praat_axes": True,
        "layout_size": "单栏 80mm",
    }
    fig = f_exporter.generate_plot(f_entries, is_preview=False)
    w_in, _ = fig.get_size_inches()
    assert w_in == pytest.approx(80.0 / 25.4, rel=0.02)
    ax = fig.axes[0]
    assert ax.spines["top"].get_visible() is True
    assert ax.spines["right"].get_visible() is True
    assert ax.spines["bottom"].get_visible() is False
    assert ax.spines["left"].get_visible() is False

    out_pdf = tmp_path / "vowel_space_80mm.pdf"
    f_exporter._save_figure(fig, str(out_pdf))
    assert out_pdf.exists() and out_pdf.stat().st_size > 1000
    plt.close(fig)


# ==============================================================================
# 2. 正交分组参数 (color_by / facet_by) 与向后兼容及三步式 GUI 结构测试
# ==============================================================================

def test_orthogonal_color_by_and_facet_by_with_backward_compatibility():
    exporter, entries = _make_tone_exporter_and_entries()

    # Test color_by: group, label, syl_char, speaker, custom field
    for c_by in ("按字表组别", "按词语", "按单字音节", "按发音人", "按自定义字段:调类"):
        exporter.params = {
            "chart_type": "contour",
            "color_by": c_by,
            "facet_by": "不分面 (单图叠加)",
            "preset_theme": "presentation",
        }
        fig = exporter.generate_plot(entries, is_preview=False)
        assert fig is not None and len(fig.axes) >= 1
        plt.close(fig)

    # Test facet_by: group, syllable_position, speaker
    for f_by, expected_min_facets in [
        ("按字表组别分面", 4),
        ("按音节位置分面", 2),
        ("按发音人分面", 1),
    ]:
        exporter.params = {
            "chart_type": "contour",
            "color_by": "按字表组别",
            "facet_by": f_by,
        }
        fig = exporter.generate_plot(entries, is_preview=False)
        vis_axes = [a for a in fig.axes if a.get_visible() and not getattr(a, "_is_chao_axis", False)]
        assert len(vis_axes) >= expected_min_facets
        plt.close(fig)

    # Test backward compatibility with legacy groupby, contour_facet, and chart_group_rule
    exporter.params = {
        "chart_type": "contour",
        "groupby": "按词语",
        "contour_facet": "按音节位置分面",
        "chart_group_rule": {"source": "item_meta", "tag_mode": "each", "selected_values": [], "field_name": "调类"},
    }
    assert exporter._get_group_key(exporter.get_param("groupby")) == "label"
    assert exporter._resolve_facet_by("contour") == "syllable_position"
    fig_compat = exporter.generate_plot(entries, is_preview=False)
    plt.close(fig_compat)

    # Verify AcousticChartExportDialog methods for 3-step left panel, top mode switch, and bottom export bar
    for method_name in (
        "_on_work_mode_changed",
        "_on_preset_theme_changed",
        "_toggle_adv_drawer",
        "copy_figure_to_clipboard",
        "export_chart_table_data",
        "_bind_canvas_hover_metadata",
    ):
        assert hasattr(AcousticChartExportDialog, method_name), f"Missing {method_name} on AcousticChartExportDialog"


# ==============================================================================
# 3. 模式 B：【数据质检与异常排查】三大图表强化测试
# ==============================================================================

def test_qc_mode_item_level_heatmaps_and_anomaly_sorting_and_hover_metadata():
    # 3.1 Tone QC Heatmap: Z-score / deviation, RdBu_r, sort_by_anomaly, [!], NaN handling, _qc_hover_metadata
    exporter, entries = _make_tone_exporter_and_entries()
    nan_entry = dict(entries[0])
    nan_entry["label"] = "全缺失词"
    nan_entry["item_id"] = "item_nan"
    nan_entry["syl_data"] = [(0.2, [np.nan] * 11), (0.2, [np.nan] * 11)]
    nan_entry["normalized_syl_data"] = [(0.2, [np.nan] * 11), (0.2, [np.nan] * 11)]
    entries_with_nan = entries + [nan_entry]

    exporter.params = {
        "work_mode": "qc",
        "chart_type": "overview_heatmap",
        "color_by": "按词语",
        "overview_show_deviation": True,
        "sort_by_anomaly": True,
    }
    fig_hm = exporter.generate_plot(entries_with_nan, is_preview=False)
    ax_hm = fig_hm.axes[0]
    im = ax_hm.images[0]
    assert "RdBu_r" in im.get_cmap().name

    hover_meta = getattr(fig_hm, "_qc_hover_metadata", None)
    assert hover_meta is not None and len(hover_meta) >= 5
    assert hover_meta[0]["label"] == "跳变词"
    assert hover_meta[0]["is_anomaly"] is True
    assert hover_meta[0]["y_label"].startswith("[!]")

    arr_data = np.ma.filled(im.get_array(), np.nan)
    nan_row_idx = next(r["row_index"] for r in hover_meta if r["label"] == "全缺失词")
    assert np.all(np.isnan(arr_data[nan_row_idx]))
    plt.close(fig_hm)

    # 3.2 Formant QC Heatmap: Z-score deviation, RdBu_r, sort_by_anomaly, [!], _qc_hover_metadata
    f_exporter, f_entries = _make_formant_exporter_and_entries()
    f_exporter.params = {
        "work_mode": "qc",
        "chart_type": "formant_overview_heatmap",
        "color_by": "按词语",
        "overview_show_deviation": True,
        "sort_by_anomaly": True,
    }
    fig_fhm = f_exporter.generate_plot(f_entries, is_preview=False)
    f_hover = getattr(fig_fhm, "_qc_hover_metadata", None)
    assert f_hover is not None and len(f_hover) >= 5
    assert f_hover[0]["label"] == "衣4字"
    assert f_hover[0]["is_anomaly"] is True
    assert f_hover[0]["y_label"].startswith("[!]")
    assert "RdBu_r" in fig_fhm.axes[0].images[0].get_cmap().name
    plt.close(fig_fhm)


def test_qc_mode_spaghetti_overlay_and_dual_health_check_panel():
    exporter, entries = _make_tone_exporter_and_entries()

    # 3.2 Spaghetti Raw Curve Overlay: normal curves (#94A3B8, lw=1.0, alpha=0.45) vs >2σ outlier (#DC2626, lw=2.2 + annotation)
    exporter.params = {
        "work_mode": "qc",
        "chart_type": "spaghetti_qc",
        "facet_by": "按字表组别分面",
    }
    fig_sp = exporter.generate_plot(entries, is_preview=False)
    outliers = getattr(fig_sp, "_spaghetti_outliers", None)
    assert outliers is not None and len(outliers) >= 1
    outlier_labels = [o["tag_text"] for o in outliers]
    assert any("女1: 跳变词 #item_outlier" in lbl for lbl in outlier_labels)

    ax_11 = fig_sp.axes[0]
    line_colors_lws = [(mcolors.to_hex(ln.get_color()).upper(), round(ln.get_linewidth(), 1)) for ln in ax_11.get_lines()]
    assert ("#94A3B8", 1.0) in line_colors_lws
    assert ("#DC2626", 2.2) in line_colors_lws
    ann_texts = [t.get_text() for t in ax_11.texts]
    assert any("女1: 跳变词 #item_outlier" in t for t in ann_texts)
    plt.close(fig_sp)

    # 3.3 Dual Health Check Panel (Detection Rate Boxplot + Speaker Range Scatter)
    exporter.params = {
        "work_mode": "qc",
        "chart_type": "quality",
        "qc_view": "dual_check",
    }
    fig_qc = exporter.generate_plot(entries, is_preview=False)
    vis_axes = [a for a in fig_qc.axes if a.get_visible()]
    assert len(vis_axes) == 2
    assert "检出率" in vis_axes[0].get_ylabel()
    assert "Mean F0" in vis_axes[1].get_ylabel()
    plt.close(fig_qc)


# ==============================================================================
# 4. 模式 A：【学术论文图表】“3 + 3”核心图表升级测试
# ==============================================================================

def test_publication_tone_charts_chao_axis_sandhi_grid_and_raincloud():
    exporter, entries = _make_tone_exporter_and_entries()

    # Tone 1: Tone Contour with right-side Chao 5-level axis, automatic [55]/[44-44] fitting, spaghetti & KDE bg
    exporter.params = {
        "work_mode": "publication",
        "chart_type": "1. 声调格局与时序轮廓图",
        "scale": "T 值 (五度标调)",
        "show_chao_five_level_axis": True,
        "show_spaghetti": True,
        "show_kde_bg": True,
    }
    fig_t1 = exporter.generate_plot(entries, is_preview=False)
    chao_axes = [a for a in fig_t1.axes if getattr(a, "_is_chao_axis", False)]
    assert len(chao_axes) >= 1
    tick_lbls = [t.get_text() for t in chao_axes[0].get_yticklabels()]
    assert "1度 [0-1]" in tick_lbls and "5度 [4-5]" in tick_lbls
    tone_codes = getattr(fig_t1, "_chao_tone_codes", {})
    assert "11_阴阴" in tone_codes and "-" in tone_codes["11_阴阴"]
    plt.close(fig_t1)

    # Tone 2: Disyllabic Tone Sandhi Grid (grid, matrix, eta2 subviews)
    exporter.params = {
        "work_mode": "publication",
        "chart_type": "2. 连读变调与协同发音图",
        "sandhi_subview": "grid",
    }
    fig_sandhi = exporter.generate_plot(entries, is_preview=False)
    sandhi_summary = getattr(fig_sandhi, "_sandhi_grid_summary", {})
    assert len(sandhi_summary) >= 4
    plt.close(fig_sandhi)

    for subview in ("matrix", "eta2"):
        exporter.params = {"chart_type": "tone_sandhi", "sandhi_subview": subview}
        fig_sv = exporter.generate_plot(entries, is_preview=False)
        assert fig_sv is not None
        plt.close(fig_sv)

    # Tone 3: Tone Distribution Raincloud Plot & 8 Acoustic Metrics
    for metric_name in ("全调均值", "起点值 (Onset)", "中点值 (Mid)", "终点值 (Offset)", "拐点极值", "调域跨度 (Range)", "斜率 (Slope)", "音节时长 (Duration ms)"):
        exporter.params = {
            "work_mode": "publication",
            "chart_type": "3. 声调声学特征分布雨云图",
            "dist_metric": metric_name,
            "dist_style": "雨云图 (Raincloud)",
        }
        fig_dist = exporter.generate_plot(entries, is_preview=False)
        metrics_dict = getattr(fig_dist, "_tone_distribution_metrics", {})
        assert "11_阴阴" in metrics_dict
        for key_8 in ("mean", "onset", "mid", "offset", "inflection", "range", "slope", "duration_ms"):
            assert len(metrics_dict["11_阴阴"][key_8]) >= 1
        plt.close(fig_dist)


def test_publication_formant_charts_vsa_diphthong_arrows_f3_and_pillai_matrix():
    f_exporter, f_entries = _make_formant_exporter_and_entries()

    # Formant 1: Vowel Acoustic Space with Convex Hull (VSA) & Diphthong 20%->80% Vector Arrows (TL)
    f_exporter.params = {
        "work_mode": "publication",
        "chart_type": "1. 元音声学空间图",
        "show_convex_hull": True,
        "show_diphthong_arrows": True,
        "formant_normalization": "原始频率 (Hz)",
    }
    fig_vs = f_exporter.generate_plot(f_entries, is_preview=False)
    vsa_area = getattr(fig_vs, "_vsa_area", 0.0)
    traj_lengths = getattr(fig_vs, "_trajectory_lengths", {})
    assert vsa_area > 10000.0
    assert "ai" in traj_lengths
    assert traj_lengths["ai"] > traj_lengths["u"]
    plt.close(fig_vs)

    # Formant 2: Formant Trajectories with F3 overlay, syllable filter, and unit conversions (Hz / Bark / ERB / Lobanov)
    for unit_mode in ("原始频率 (Hz)", "Bark (听觉巴克)", "ERB (等效矩形带宽)", "Lobanov z-score"):
        f_exporter.params = {
            "work_mode": "publication",
            "chart_type": "2. 共振峰时序轨迹图",
            "show_f3": True,
            "formant_syllable_filter": "第 1 音节",
            "formant_normalization": unit_mode,
        }
        fig_tr = f_exporter.generate_plot(f_entries, is_preview=False)
        ax_tr = fig_tr.axes[0]
        leg_labels = [t.get_text() for t in ax_tr.get_legend().get_texts()]
        assert any("F3" in lbl for lbl in leg_labels)
        plt.close(fig_tr)

    # Formant 3: Vowel Merger Pillai Score Matrix & Euclidean Distance Matrix + Raincloud subview
    f_exporter.params = {
        "work_mode": "publication",
        "chart_type": "3. 元音对立重叠度与分布图",
        "pillai_subview": "matrix",
        "formant_normalization": "原始频率 (Hz)",
    }
    fig_pil = f_exporter.generate_plot(f_entries, is_preview=False)
    pillai_mat = getattr(fig_pil, "_pillai_matrix", None)
    euclid_mat = getattr(fig_pil, "_euclidean_matrix", None)
    cats = getattr(fig_pil, "_pillai_categories", [])
    assert pillai_mat is not None and euclid_mat is not None
    assert pillai_mat.shape == (len(cats), len(cats))
    assert np.allclose(np.diag(pillai_mat), 0.0)
    idx_a = cats.index("a")
    idx_u = cats.index("u")
    idx_i = cats.index("i")
    idx_in = cats.index("i_near")
    assert pillai_mat[idx_a, idx_u] > 0.80
    assert pillai_mat[idx_i, idx_in] < pillai_mat[idx_a, idx_u]
    assert euclid_mat[idx_a, idx_u] > 400.0
    plt.close(fig_pil)

    # Raincloud subview of Formant 3
    f_exporter.params = {
        "work_mode": "publication",
        "chart_type": "3. 元音对立重叠度与分布图",
        "pillai_subview": "raincloud",
    }
    fig_pil_rc = f_exporter.generate_plot(f_entries, is_preview=False)
    assert len([a for a in fig_pil_rc.axes if a.get_visible()]) == 2
    plt.close(fig_pil_rc)


# ============================================================================
# Phase 4 & 5 (Toolkit Custom Script Sandbox & Interactive Canvas)
# ============================================================================
# -*- coding: utf-8 -*-
"""
PhonTracer 阶段四/五：工具箱自定义脚本自由度、预览交互工作台与高阶内置脚本生态单元测试
"""

import copy
import json
import os
import shutil
import zipfile
from pathlib import Path
from types import SimpleNamespace

import matplotlib
matplotlib.use("Agg", force=True)
import matplotlib.pyplot as plt
import numpy as np
import pytest
from PIL import Image

import toolkit
from modules.project_manager import read_project_metadata_from_archive
from modules.script_api import (
    FigureResult,
    ProjectPatchResult,
    ScriptContext,
    TableResult,
    build_dataset_snapshot,
)
from modules.script_manager import (
    DEFAULT_SCRIPTS,
    coerce_script_params,
    extract_script_params_schema,
    get_default_script_params,
    load_all_scripts,
)
from modules.script_prompt import generate_ai_prompt
from modules.script_runner import (
    extract_script_params_schema as runner_extract_schema,
    get_default_script_params as runner_get_defaults,
    run_custom_script,
)


def _make_headless_toolkit_app(tmp_path):
    """构造无需真实 X11 窗口即可完整测试工作台逻辑的轻量 ToolkitApp 实例。"""
    app = toolkit.ToolkitApp.__new__(toolkit.ToolkitApp)
    app.font_family = "Microsoft YaHei"
    app.colors = {
        "primary": "#2563EB",
        "primary_hover": "#1D4ED8",
        "primary_soft": "#DBEAFE",
        "purple": "#7C3AED",
        "purple_soft": "#EDE9FE",
        "success": "#059669",
        "success_soft": "#D1FAE5",
        "warning": "#D97706",
        "warning_soft": "#FEF3C7",
        "danger": "#DC2626",
        "text": "#111827",
        "muted": "#6B7280",
        "surface_soft": "#F8FAFC",
        "border": "#E5E7EB",
        "border_strong": "#D1D5DB",
    }
    app.local_scripts = list(DEFAULT_SCRIPTS)
    app.selected_script_id = DEFAULT_SCRIPTS[0]["id"]
    app.loaded_teproj_path = None
    app.project_data = None
    app.project_namelist = []
    app.script_output_dir = None
    app.script_figure_results = []
    app.previous_run_figure_results = []
    app.current_script_figure_index = 0
    app.script_table_results = []
    app.current_script_table_index = 0
    app.pending_project_patch = None
    app.last_script_run_record = None
    app.last_error_line_no = None
    app.current_script_params_schema = []
    app.script_param_widgets = {}
    app.code_drawer_expanded = False
    app.log_pane_collapsed = False
    app.active_artifact_mode = "figure"
    app.active_artifact_key = ("figure", 0)
    app.canvas_zoom_scale = 1.0
    app.canvas_fit_mode = True
    app.canvas_pan_offset_x = 0.0
    app.canvas_pan_offset_y = 0.0
    app.canvas_split_compare_mode = False
    app.current_canvas_pil_image = None
    app.compare_canvas_pil_image = None
    app._canvas_resize_after_id = None
    app._canvas_drag_start_pos = None
    app._table_sort_state = {}
    app._clipboard_image_buffer = None
    app.btn_open_script_output = None
    app._log_messages = []

    orig_append = toolkit.ToolkitApp.append_script_log
    def _capture_log(msg):
        app._log_messages.append(str(msg))
        orig_append(app, msg)

    app.append_script_log = _capture_log
    out_root = tmp_path / "script_outputs"
    prev_root = tmp_path / "script_previews"
    out_root.mkdir(parents=True, exist_ok=True)
    prev_root.mkdir(parents=True, exist_ok=True)
    app.get_script_output_dir = lambda name: str(out_root)
    app.get_script_preview_dir = lambda out_dir: str(prev_root)
    return app


# ==============================================================================
# 1. PARAMS Schema AST 解析、默认参数提取与 run_custom_script 传参联动测试
# ==============================================================================

def test_params_schema_extraction_and_runner_injection():
    code = '''# 脚本名称：参数化测试脚本
# 功能说明：测试五种参数类型与边界约束
PARAMS = [
    {"key": "max_freq", "label": "最大频率(Hz)", "type": "choice", "choices": [4000, 5000, 6000, 8000], "default": 5000},
    {"key": "max_items", "label": "最多展示词数", "type": "int", "default": 8, "min": 1, "max": 24},
    {"key": "alpha", "label": "透明度", "type": "float", "default": 0.65, "min": 0.1, "max": 1.0},
    {"key": "show_f0", "label": "叠加基频蓝线", "type": "bool", "default": True},
    {"key": "custom_tag", "label": "标题后缀", "type": "str", "default": "实验A"},
]

def run(ctx):
    return ctx.table(
        [[
            ctx.params.get("max_freq"),
            ctx.params.get("max_items"),
            round(float(ctx.params.get("alpha")), 2),
            ctx.params.get("show_f0"),
            ctx.params.get("custom_tag"),
        ]],
        ["max_freq", "max_items", "alpha", "show_f0", "custom_tag"],
        title="参数回显表",
    )
'''
    schema = extract_script_params_schema(code)
    assert schema == runner_extract_schema(code)
    assert len(schema) == 5
    assert [p["key"] for p in schema] == ["max_freq", "max_items", "alpha", "show_f0", "custom_tag"]
    assert [p["type"] for p in schema] == ["choice", "int", "float", "bool", "str"]

    defaults = get_default_script_params(code)
    assert defaults == runner_get_defaults(code)
    assert defaults == {
        "max_freq": 5000,
        "max_items": 8,
        "alpha": 0.65,
        "show_f0": True,
        "custom_tag": "实验A",
    }

    # 1) 不传 params 时使用默认值
    res_def, logs_def, err_def = run_custom_script(code, [], timeout=5)
    assert err_def is None
    assert isinstance(res_def, TableResult)
    assert res_def.rows[0] == [5000, 8, 0.65, True, "实验A"]

    # 2) 传入 UI 字符串/越界数值时自动按 schema 类型转换与 min/max 钳制
    res_custom, _, err_custom = run_custom_script(
        code,
        [],
        timeout=5,
        params={
            "max_freq": "8000",   # string from OptionMenu -> coerced to int 8000 from choices
            "max_items": "99",    # clamped to max=24
            "alpha": "0.01",      # clamped to min=0.1
            "show_f0": "false",   # coerced to False
            "custom_tag": "批次B",
        },
    )
    assert err_custom is None
    assert res_custom.rows[0] == [8000, 24, 0.1, False, "批次B"]


def test_toolkit_params_collect_and_reset_workflow(tmp_path):
    app = _make_headless_toolkit_app(tmp_path)
    code = '''
PARAMS = [
    {"key": "mode", "label": "模式", "type": "choice", "choices": ["Hz", "T值"], "default": "Hz"},
    {"key": "pts", "label": "点数", "type": "int", "default": 11, "min": 3, "max": 21},
    {"key": "smooth", "label": "平滑", "type": "bool", "default": False},
]
def run(ctx):
    return None
'''
    schema = app.rebuild_script_params_ui(code)
    assert len(schema) == 3

    # Simulate UI widget variables in headless mode
    class _DummyVar:
        def __init__(self, v):
            self._v = v
        def get(self):
            return self._v
        def set(self, v):
            self._v = v

    for item in schema:
        app.script_param_widgets[item["key"]] = {
            "schema": item,
            "type": item["type"],
            "var": _DummyVar(item["default"]),
            "widget": None,
        }

    assert app.collect_script_params_from_ui() == {"mode": "Hz", "pts": 11, "smooth": False}

    # Change values via set_script_param_ui_value
    app.set_script_param_ui_value("mode", "T值")
    app.set_script_param_ui_value("pts", "15")
    app.set_script_param_ui_value("smooth", True)
    assert app.collect_script_params_from_ui() == {"mode": "T值", "pts": 15, "smooth": True}

    # Reset to default
    reset_vals = app.reset_script_params_to_default()
    assert reset_vals == {"mode": "Hz", "pts": 11, "smooth": False}


# ==============================================================================
# 2. 多格式同步导出 (formats + dpi) 与 图表 + 表格 + 工程补丁联合返回测试
# ==============================================================================

def test_multi_format_export_and_combined_figure_table_patch_return(tmp_path):
    app = _make_headless_toolkit_app(tmp_path)

    # 准备测试工程 .teproj
    teproj_src = tmp_path / "source.teproj"
    teproj_patched = tmp_path / "patched_out.teproj"
    project_state = {
        "version": "1.0",
        "active_speaker_id": "spk1",
        "speakers": {
            "spk1": {
                "id": "spk1",
                "name": "发音人甲",
                "last_params": {"pitch_floor": 75, "pitch_ceiling": 500, "analysis_mode": "f0"},
                "tab_mode": "多条独立音频",
                "items": {
                    "item1": {
                        "label": "妈",
                        "group": "阴平",
                        "start": 0.0,
                        "end": 0.3,
                        "is_excluded": False,
                    }
                },
            }
        },
    }
    with zipfile.ZipFile(teproj_src, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("project.json", json.dumps(project_state, ensure_ascii=False))

    app.loaded_teproj_path = str(teproj_src)
    app.project_data = copy.deepcopy(project_state) if hasattr(toolkit, "copy") else dict(project_state)

    # 运行同时返回 FigureResult(多格式 png/pdf/svg + dpi=240) + TableResult + ProjectPatchResult 的脚本
    code = '''
def run(ctx):
    fig, ax = ctx.plt.subplots(figsize=(4, 3))
    ax.plot([0, 1, 2], [150, 210, 180], "-o")
    ax.set_title("联合返回诊断图")
    fig_res = ctx.figure(
        fig,
        filename="joint_diag.png",
        title="联合诊断图",
        formats=("png", "pdf", "svg"),
        dpi=240,
    )
    tbl_res = ctx.table(
        [["item1", "妈", 210.0], ["item2", "麻", 165.5]],
        ["词条ID", "词项", "F0均值"],
        title="联合诊断明细表",
    )
    op = ctx.set_item_fields(
        {"speaker_id": "spk1", "speaker_name": "发音人甲", "item_id": "item1", "label": "妈"},
        {"group": "阴平_已复核", "item_note": "经联合脚本诊断通过"},
        reason="联合返回补丁测试",
    )
    patch_res = ctx.project_patch([op], title="联合返回工程补丁", description="测试三合一返回")
    return [fig_res, tbl_res, patch_res]
'''
    res, logs, err = run_custom_script(code, [], timeout=10)
    assert err is None
    assert isinstance(res, list) and len(res) == 3

    app.display_script_result(res)

    # 1) 验证多格式同步导出 (png, pdf, svg 全部落盘且内存生成了 200 DPI PIL 预览图)
    assert len(app.script_figure_results) == 1
    fig_entry = app.script_figure_results[0]
    assert set(fig_entry["saved_paths_by_format"].keys()) == {"png", "pdf", "svg"}
    for fmt, p in fig_entry["saved_paths_by_format"].items():
        assert os.path.exists(p), f"Missing exported format {fmt}: {p}"
    assert isinstance(fig_entry["pil_image"], Image.Image)
    assert fig_entry["dpi"] == 240

    # 2) 验证表格同时被保存并加载到内置表格预览结果中
    assert len(app.script_table_results) == 1
    tbl_entry = app.script_table_results[0]
    assert tbl_entry["title"] == "联合诊断明细表"
    assert len(tbl_entry["rows"]) == 2
    assert os.path.exists(tbl_entry["output_path"])

    # 3) 验证 ProjectPatchResult 已挂起就绪且可应用并另存为新 .teproj
    assert app.pending_project_patch is not None
    assert app.pending_project_patch.operation_count == 1
    summary = app.apply_pending_project_patch(output_path=str(teproj_patched), run_synchronously=True)
    assert summary is not None
    assert teproj_patched.exists()

    patched_state, _ = read_project_metadata_from_archive(str(teproj_patched))
    patched_item = patched_state["speakers"]["spk1"]["items"]["item1"]
    assert patched_item["group"] == "阴平_已复核"
    assert patched_item["note"] == "经联合脚本诊断通过"


# ==============================================================================
# 3. 交互式看图画布 (缩放/平移/双击复位/双图对比/剪贴板/另存) 与内置表格排序测试
# ==============================================================================

def test_interactive_canvas_zoom_pan_split_compare_and_table_operations(tmp_path):
    app = _make_headless_toolkit_app(tmp_path)

    # 构造两张测试图表与一张数据表
    fig1, ax1 = plt.subplots(figsize=(4, 3))
    ax1.plot([0, 1], [100, 200])
    fig2, ax2 = plt.subplots(figsize=(4, 3))
    ax2.plot([0, 1], [200, 100])
    tbl = TableResult(
        [
            ["词B", 240.5, 3.2],
            ["词A", 120.0, 4.8],
            ["词C", 180.2, 1.5],
        ],
        ["词项", "F0均值", "T值跨度"],
        title="排序与导出测试表",
    )

    app.display_script_result([
        FigureResult(fig1, filename="chart1.png", title="图一"),
        FigureResult(fig2, filename="chart2.png", title="图二"),
        tbl,
    ])

    # 1) 验证初始处于适应窗口模式，且已渲染高分辨率 PIL 图像
    assert app.canvas_fit_mode is True
    assert app.canvas_zoom_scale == pytest.approx(1.0)
    assert app.current_canvas_pil_image is not None

    # 2) 验证鼠标滚轮以光标为中心无级缩放 (25% ~ 400%)
    app.on_canvas_mousewheel(SimpleNamespace(delta=120, x=200, y=150, num=None))
    assert app.canvas_fit_mode is False
    assert app.canvas_zoom_scale == pytest.approx(1.15)

    # 连续放大不会超过 400% (4.0)
    for _ in range(25):
        app.canvas_zoom_in()
    assert app.canvas_zoom_scale == pytest.approx(4.0)

    # 连续缩小不会低于 25% (0.25)
    for _ in range(40):
        app.canvas_zoom_out()
    assert app.canvas_zoom_scale == pytest.approx(0.25)

    # 3) 验证左键拖拽平移与双击复位适应窗口
    app.canvas_zoom_original()
    app.on_canvas_drag_start(SimpleNamespace(x=100.0, y=100.0))
    app.on_canvas_drag_move(SimpleNamespace(x=145.0, y=70.0))
    assert app.canvas_pan_offset_x == pytest.approx(45.0)
    assert app.canvas_pan_offset_y == pytest.approx(-30.0)

    app.on_canvas_double_click()
    assert app.canvas_fit_mode is True
    assert app.canvas_zoom_scale == pytest.approx(1.0)
    assert app.canvas_pan_offset_x == pytest.approx(0.0)
    assert app.canvas_pan_offset_y == pytest.approx(0.0)

    # 4) 验证双图并排对比模式 (Split Compare)
    assert app.toggle_canvas_split_compare(True) is True
    comp_img, comp_title = app._resolve_compare_pil_image()
    assert comp_img is not None
    assert "图二" in comp_title
    assert app.toggle_canvas_split_compare(False) is False

    # 5) 验证复制图像到剪贴板与另存为
    assert app.copy_current_chart_to_clipboard() is True
    assert app._clipboard_image_buffer is not None and len(app._clipboard_image_buffer) > 100
    saved_copy = tmp_path / "manual_save.png"
    assert app.save_current_chart_as(str(saved_copy)) == str(saved_copy)
    assert saved_copy.exists()

    # 6) 验证内置表格预览器的按列排序、TSV 复制与另存为 CSV/XLSX
    app.select_script_artifact("table", 0)
    assert app.active_artifact_mode == "table"

    # 按第 1 列 (F0均值, 数值列) 升序排序
    sorted_asc = app.sort_script_table_by_column(1, reverse=False)
    assert [r[0] for r in sorted_asc] == ["词A", "词C", "词B"]
    # 再次点击切换为降序排序
    sorted_desc = app.sort_script_table_by_column(1)
    assert [r[0] for r in sorted_desc] == ["词B", "词C", "词A"]

    tsv_str = app.copy_current_table_tsv_to_clipboard()
    assert "词项\tF0均值\tT值跨度" in tsv_str
    assert "词B\t240.5\t3.2" in tsv_str

    csv_out = tmp_path / "exported_table.csv"
    xlsx_out = tmp_path / "exported_table.xlsx"
    assert app.save_current_table_as(str(csv_out)) == str(csv_out)
    assert app.save_current_table_as(str(xlsx_out)) == str(xlsx_out)
    assert csv_out.exists() and xlsx_out.exists()


# ==============================================================================
# 4. 无弹窗工程归档、报错行号解析与 ScriptEditorDialog.goto_line 测试
# ==============================================================================

def test_non_blocking_archive_and_error_line_goto(tmp_path, monkeypatch):
    app = _make_headless_toolkit_app(tmp_path)

    # 若 display_script_result 仍调用 messagebox.askyesno 则直接失败
    monkeypatch.setattr(
        toolkit.messagebox,
        "askyesno",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("messagebox.askyesno should NOT be called!")),
    )

    teproj_path = tmp_path / "archive_test.teproj"
    project_state = {"version": "1.0", "speakers": {}}
    with zipfile.ZipFile(teproj_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("project.json", json.dumps(project_state))

    app.loaded_teproj_path = str(teproj_path)
    app.project_data = dict(project_state)
    app.var_auto_archive_script_run = SimpleNamespace(get=lambda: False)

    fig, ax = plt.subplots(figsize=(3, 2))
    ax.plot([0, 1], [1, 2])
    app.display_script_result(FigureResult(fig, filename="no_modal.png", title="无弹窗测试"))

    # 默认未勾选自动归档时不写入 custom_script_runs，也不弹窗
    assert "custom_script_runs" not in app.project_data

    # 手动点击 [归档本次运行到工程] 按钮后成功写入工程
    assert app.archive_last_script_run_to_project() is True
    assert len(app.project_data.get("custom_script_runs", [])) == 1

    # 测试报错行号提取与日志记录
    bad_code = "def run(ctx):\n    x = 1\n    y = 2\n    return 1 / 0\n"
    _, _, err_msg = run_custom_script(bad_code, [], timeout=5)
    assert err_msg is not None
    line_no = toolkit.extract_error_line_number(err_msg)
    assert line_no == 4

    app.on_script_finished(None, [], err_msg)
    assert app.last_error_line_no == 4

    # 测试 ScriptEditorDialog.goto_line(line_no)
    dlg = toolkit.ScriptEditorDialog.__new__(toolkit.ScriptEditorDialog)
    dlg._line_number_after_id = None
    dlg._refresh_code_line_numbers = lambda: None
    tags_applied = {}

    class _FakeTextbox:
        def index(self, spec):
            return "10.0"
        def get(self, s, e):
            return "\n".join(f"line {i}" for i in range(1, 11))
        def tag_remove(self, *args):
            pass
        def tag_config(self, tag, **kw):
            tags_applied["config"] = (tag, kw)
        def tag_add(self, tag, start, end):
            tags_applied["range"] = (tag, start, end)
        def mark_set(self, mark, pos):
            tags_applied["insert"] = pos
        def see(self, pos):
            tags_applied["see"] = pos
        def focus_set(self):
            tags_applied["focused"] = True

    dlg.txt_code = _FakeTextbox()
    assert dlg.goto_line(4) is True
    assert dlg.highlighted_line_no == 4
    assert tags_applied["range"] == ("error_highlight", "4.0", "4.end")
    assert tags_applied["see"] == "4.0"


# ==============================================================================
# 5. 内置高阶实战脚本库与 AI 提示词模板完整验证
# ==============================================================================

def test_builtin_high_order_scripts_and_prompt_contract(monkeypatch, tmp_path):
    monkeypatch.setattr("modules.script_manager.get_scripts_dir", lambda: str(tmp_path / "scripts"))
    scripts = load_all_scripts(force_reset_builtins=True)
    script_map = {s["id"]: s for s in scripts}

    required_ids = {
        "builtin_f0_group_mean",
        "builtin_vowel_space",
        "builtin_voice_quality_profile",
        "builtin_fricative_spectral_moments",
        "builtin_octave_jump_auto_clean",
    }
    assert required_ids.issubset(set(script_map.keys()))

    # 验证所有内置脚本均定义了合法的 PARAMS Schema
    for sid in required_ids:
        schema = extract_script_params_schema(script_map[sid]["code"])
        assert len(schema) >= 2, f"Builtin script {sid} should declare PARAMS"

    # 使用示例工程数据实际运行 3 个新增高阶内置脚本
    root_dir = Path(__file__).resolve().parents[1]
    teproj_path = root_dir / "Example" / "wordlist_tone16" / "普通话双字组16种声调实验.teproj"
    items = build_dataset_snapshot(str(teproj_path))
    subset_items = items[:12]

    # 1) builtin_voice_quality_profile
    res_vq, _, err_vq = run_custom_script(
        script_map["builtin_voice_quality_profile"]["code"],
        subset_items,
        timeout=20,
        teproj_path=str(teproj_path),
        params={"max_items": 8, "export_vector": True},
    )
    assert err_vq is None, f"builtin_voice_quality_profile error: {err_vq}"
    assert isinstance(res_vq, list) and len(res_vq) == 2
    assert isinstance(res_vq[0], FigureResult)
    assert res_vq[0].formats == ("png", "pdf", "svg")
    assert isinstance(res_vq[1], TableResult)
    plt.close(res_vq[0].fig)

    # 2) builtin_fricative_spectral_moments
    res_sm, _, err_sm = run_custom_script(
        script_map["builtin_fricative_spectral_moments"]["code"],
        subset_items,
        timeout=20,
        teproj_path=str(teproj_path),
        params={"max_items": 8, "pre_emphasis": 80.0},
    )
    assert err_sm is None, f"builtin_fricative_spectral_moments error: {err_sm}"
    assert isinstance(res_sm, list) and len(res_sm) == 2
    assert isinstance(res_sm[0], FigureResult)
    assert isinstance(res_sm[1], TableResult)
    plt.close(res_sm[0].fig)

    # 3) builtin_octave_jump_auto_clean (FigureResult + TableResult + ProjectPatchResult 三合一)
    res_oj, _, err_oj = run_custom_script(
        script_map["builtin_octave_jump_auto_clean"]["code"],
        subset_items,
        timeout=20,
        teproj_path=str(teproj_path),
    )
    assert err_oj is None, f"builtin_octave_jump_auto_clean error: {err_oj}"
    assert isinstance(res_oj, list) and len(res_oj) == 3
    assert isinstance(res_oj[0], FigureResult)
    assert isinstance(res_oj[1], TableResult)
    assert isinstance(res_oj[2], ProjectPatchResult)
    assert res_oj[2].operation_count >= 1
    plt.close(res_oj[0].fig)

    # 4) 验证 script_prompt.py 提示词包含 PARAMS、多格式导出与三合一联合返回规范
    chart_prompt = generate_ai_prompt({}, {"script_type": "chart", "prompt_mode": "Agent协作"})
    dp_prompt = generate_ai_prompt({}, {"script_type": "data_process", "prompt_mode": "Agent协作"})
    for p_txt in (chart_prompt, dp_prompt):
        assert "PARAMS" in p_txt
        assert "ctx.params.get" in p_txt
        assert "ctx.project_patch" in p_txt
    assert "formats=" in chart_prompt


# ==============================================================================
# 6. UI/UX 精细化打磨回归测试（默认仅平均曲线、质检分页、KDE默认分面、Toolkit预览与动态栏）
# ==============================================================================

def test_exporter_default_mean_only_and_kde_group_faceting():
    exporter, entries = _make_tone_exporter_and_entries()

    # 1) 声调格局图默认不带置信区间阴影带（仅组别平均曲线）
    exporter.params = {
        "work_mode": "publication",
        "chart_type": "contour",
        "scale": "T 值 (五度标调)",
    }
    fig_c = exporter.generate_plot(entries, is_preview=False)
    main_ax = [a for a in fig_c.axes if not getattr(a, "_is_chao_axis", False)][0]
    assert len(main_ax.collections) == 0, "Default tone contour should not render error band PolyCollections"
    plt.close(fig_c)

    # 2) 声调时空密度图 (density) 默认按字表组别分面
    exporter.params = {
        "work_mode": "publication",
        "chart_type": "density",
    }
    assert exporter._resolve_facet_by("density") == "group"
    fig_d = exporter.generate_plot(entries, is_preview=False)
    vis_d_axes = [a for a in fig_d.axes if a.get_visible() and (a.get_title() or a.get_title(loc="left"))]
    assert all(a.get_title(loc="center") == "" for a in vis_d_axes)
    assert len(vis_d_axes) >= 4
    plt.close(fig_d)

    # 3) 共振峰轨迹图默认仅平均曲线，共振峰时空密度图 (formant_density) 默认按字表组别分面并支持多子图
    f_exporter, f_entries = _make_formant_exporter_and_entries()
    f_exporter.params = {
        "work_mode": "publication",
        "chart_type": "formant_trajectory",
    }
    fig_ft = f_exporter.generate_plot(f_entries, is_preview=False)
    assert len(fig_ft.axes[0].collections) == 0, "Default formant trajectory should not render SD/CI band"
    plt.close(fig_ft)

    f_exporter.params = {
        "work_mode": "publication",
        "chart_type": "formant_density",
    }
    assert f_exporter._resolve_facet_by("formant_density") == "group"
    fig_fd = f_exporter.generate_plot(f_entries, is_preview=False)
    vis_fd_axes = [a for a in fig_fd.axes if a.get_visible() and (a.get_title() or a.get_title(loc="left"))]
    assert all(a.get_title(loc="center") == "" for a in vis_fd_axes)
    assert len(vis_fd_axes) >= 5
    plt.close(fig_fd)


def test_qc_heatmap_and_spaghetti_pagination():
    exporter, base_entries = _make_tone_exporter_and_entries()

    # 构造 16 组 × 6 词 = 96 词条的合成数据集
    entries_96 = []
    for g_idx in range(16):
        g_name = f"G{g_idx+1:02d}_组"
        for w_idx in range(6):
            clone = dict(base_entries[0])
            clone["group"] = g_name
            clone["chart_group"] = g_name
            clone["label"] = f"{g_name}_词{w_idx+1}"
            clone["item_id"] = f"it_{g_idx}_{w_idx}"
            entries_96.append(clone)

    # 1) 验证 _build_overview_word_pages 将 16×6=96 词按每页 <=24 词打包为 4 页（每页 4 组 24 词）
    ordered_words, pages = exporter._build_overview_word_pages(entries_96, page_size=24)
    assert len(ordered_words) == 96
    assert len(pages) == 4
    assert all(len(p) == 24 for p in pages)

    # 2) 验证词条级逐行质检热图 (overview_heatmap) 在 QC 模式下自动开启分页（不再受 intention 限制）
    exporter.params = {
        "work_mode": "qc",
        "chart_type": "overview_heatmap",
        "color_by": "按词语",
        "facet_by": "按字表组别分面",
        "preview_group_page": 2,
    }
    state_hm = exporter._get_group_pagination_state(entries_96, "overview_heatmap")
    assert state_hm["is_paginated_heatmap"] is True
    assert state_hm["total_pages"] == 4
    assert len(state_hm["pages"]) == 4
    assert len(state_hm["pages"][1]) == 24

    fig_hm_p2 = exporter.generate_plot(entries_96, is_preview=True)
    hover_meta = getattr(fig_hm_p2, "_qc_hover_metadata", [])
    assert len(hover_meta) == 24
    plt.close(fig_hm_p2)

    # 3) 验证组内个体原始曲线叠加图 (spaghetti_qc) 在 >8 组时自动按每页 8 组分页
    exporter.params = {
        "work_mode": "qc",
        "chart_type": "spaghetti_qc",
        "color_by": "按字表组别",
        "facet_by": "按字表组别分面",
        "preview_group_page": 1,
    }
    state_sp = exporter._get_group_pagination_state(entries_96, "spaghetti_qc")
    assert state_sp["total_groups"] == 16
    assert state_sp["total_pages"] == 2

    fig_sp_p1 = exporter.generate_plot(entries_96, is_preview=True)
    vis_sp_axes = [a for a in fig_sp_p1.axes if a.get_visible() and (a.get_title() or a.get_title(loc="left"))]
    assert all(a.get_title(loc="center") == "" for a in vis_sp_axes)
    assert len(vis_sp_axes) == 8
    plt.close(fig_sp_p1)


def test_qc_item_locator_in_project_tree_and_double_click_closed_loop():
    """验证阶段 A 质检交互闭环：悬停提示 (双击定位到工程树词条) 与双击自动选中工程树对应词条。"""
    exporter, entries = _make_tone_exporter_and_entries()

    selected_nodes = []
    seen_nodes = []
    opened_groups = []
    tree_select_calls = []

    class _MockTreeWidget:
        def exists(self, iid):
            return iid in exporter.project_tree.items or str(iid).startswith("group_node_")
        def parent(self, iid):
            grp = exporter.project_tree.items.get(iid, {}).get("group", "11_阴阴")
            return f"group_node_{grp}"
        def item(self, iid, **kw):
            if kw.get("open"):
                opened_groups.append(iid)
        def selection_set(self, iid):
            selected_nodes.append(iid)
        def see(self, iid):
            seen_nodes.append(iid)
        def focus(self, iid):
            pass

    exporter.project_tree.tree = _MockTreeWidget()
    exporter.project_tree.on_tree_select = lambda ev: tree_select_calls.append(exporter.project_tree.current_iid)

    # 1) 直接调用 locate_qc_item_in_project_tree
    assert exporter.locate_qc_item_in_project_tree("item_outlier", speaker_name="女1", label="跳变词") is True
    assert exporter.project_tree.current_iid == "item_outlier"
    assert selected_nodes[-1] == "item_outlier"
    assert seen_nodes[-1] == "item_outlier"
    assert "group_node_11_阴阴" in opened_groups
    assert tree_select_calls[-1] == "item_outlier"

    # 2) 验证 _bind_canvas_hover_metadata 悬停状态栏文本与双击事件触发工程树定位
    exporter.params = {
        "work_mode": "qc",
        "chart_type": "overview_heatmap",
        "color_by": "按词语",
        "overview_show_deviation": True,
        "sort_by_anomaly": True,
    }
    fig_hm = exporter.generate_plot(entries, is_preview=False)
    ax_hm = fig_hm.axes[0]

    callbacks = {}
    class _DummyMplCanvas:
        def mpl_connect(self, evt_name, fn):
            callbacks[evt_name] = fn

    status_updates = {}
    exporter.lbl_qc_hover_status = SimpleNamespace(
        configure=lambda **kw: status_updates.update(kw)
    )
    AcousticChartExportDialog._bind_canvas_hover_metadata(exporter, _DummyMplCanvas(), fig_hm)
    assert "motion_notify_event" in callbacks
    assert "button_press_event" in callbacks

    # Row 0 is the top anomaly ("跳变词", item_outlier)
    hover_evt = SimpleNamespace(inaxes=ax_hm, xdata=2.0, ydata=0.0, dblclick=False)
    callbacks["motion_notify_event"](hover_evt)
    assert "跳变词" in status_updates.get("text", "")
    assert "(双击定位到工程树词条)" in status_updates.get("text", "")

    selected_nodes.clear()
    dbl_evt = SimpleNamespace(inaxes=ax_hm, xdata=2.0, ydata=0.0, dblclick=True, button=1)
    callbacks["button_press_event"](dbl_evt)
    assert selected_nodes == ["item_outlier"]
    assert "已在工程树中定位词条" in status_updates.get("text", "")
    plt.close(fig_hm)


def test_toolkit_ui_layout_and_contextual_bars():
    app = toolkit.ToolkitApp()
    try:
        assert hasattr(app, "_make_checkbox")
        assert hasattr(app, "_update_project_patch_bar_visibility")

        # 0) 顶栏排版验证：源码预览与AI提示词按钮位于独立的 script_meta_action_line 行（不与脚本标题同行），描述标签具备 DPI 自适应 wraplength
        assert hasattr(app, "script_meta_action_line")
        assert app.btn_toggle_code_drawer.master is app.script_meta_action_line
        assert app.btn_copy_prompt.master is app.script_meta_action_line
        assert app.lbl_selected_script_name.master is not app.script_meta_action_line
        assert int(app.lbl_selected_script_desc.cget("wraplength")) >= 180
        assert callable(getattr(app.lbl_selected_script_desc, "_update_adaptive_wrap", None))
        assert callable(getattr(app.lbl_script_project_status, "_update_adaptive_wrap", None))
        assert callable(getattr(app.lbl_script_project_detail, "_update_adaptive_wrap", None))

        # 1) 初始空状态下，canvas_toolbar 被 grid_remove() 隐藏，不会悬空在画布中央
        assert app.canvas_toolbar.grid_info() == {}

        # 2) 画布快捷键 Ctrl+C 绑定验证
        bound_seqs = app.canvas_chart_preview.bind()
        assert "<Control-Key-c>" in bound_seqs or "<Control-c>" in bound_seqs

        # 3) 选中图表脚本 (chart) 时，工程补丁栏隐藏；选中数据处理脚本 (data_process) 时显示
        chart_script = next(s for s in app.local_scripts if s.get("type") == "chart")
        dp_script = next(s for s in app.local_scripts if s.get("type") == "data_process")

        app.script_tree.selection_set(chart_script["id"])
        app.on_script_selected()
        assert app.project_patch_bar.winfo_manager() == ""

        app.script_tree.selection_set(dp_script["id"])
        app.on_script_selected()
        assert app.project_patch_bar.winfo_manager() == "pack"

        # 4) 验证自定义下拉浮层二次点击自动收起 (Toggle 取消)、复选框动态形状 (未勾选6/勾选1000) 与现代右键菜单
        import customtkinter as ctk
        from modules.acoustic_exporter import AcousticChartExportDialog
        from modules.ui_widgets import (
            ModernContextMenu,
            _ACTIVE_CONTEXT_MENU,
            _ACTIVE_DROPDOWN_POPOVER,
            _apply_custom_arrow,
            _close_active_context_menu,
            _close_active_dropdown_popover,
            _get_physical_screen_bounds,
            make_context_menu,
            post_context_menu,
        )

        assert getattr(ctk.CTkCheckBox, "_phontracer_dynamic_radius_patched", False) is True
        cb_test = app._make_checkbox(app, "动态圆角测试")
        cb_test.deselect()
        assert cb_test.get() == 0
        assert cb_test._corner_radius == 6
        cb_test.select()
        assert cb_test.get() == 1
        assert cb_test._corner_radius == 1000
        cb_test.deselect()
        assert cb_test._corner_radius == 6
        cb_test.destroy()

        # 验证 ModernContextMenu 统一字号、级联子菜单 parent 解包 (make_context_menu(menu))、API 兼容与弹窗生命周期
        ctx_menu = make_context_menu(app, font_size=15)
        assert isinstance(ctx_menu, ModernContextMenu)
        assert ctx_menu.font_size == 12
        chk_var = ctk.BooleanVar(value=True)
        # 模拟 project_tree.py:1957 传入父菜单对象 make_context_menu(ctx_menu)
        sub_menu = make_context_menu(ctx_menu)
        assert not isinstance(sub_menu.parent, ModernContextMenu)
        assert hasattr(sub_menu.parent, "tk")
        sub_menu.add_command(label="子菜单项1")
        ctx_menu.add_command(label="普通操作", command=lambda: None)
        ctx_menu.add_checkbutton(label="实时刷新", variable=chk_var)
        ctx_menu.add_separator()
        ctx_menu.add_cascade(label="高级操作", menu=sub_menu)
        ctx_menu.add_command(label="删除选中项", state="normal")
        assert ctx_menu.index("end") == 4
        assert ctx_menu.entrycget(0, "label") == "普通操作"
        post_context_menu(ctx_menu, SimpleNamespace(x_root=120, y_root=120))
        ctx_menu.grab_release()
        assert ctx_menu.winfo_exists() is True
        # 展开级联子菜单：验证不抛 AttributeError 且父子菜单同时存活
        sub_menu._open_at(260, 140, is_submenu=True)
        assert ctx_menu.winfo_exists() is True
        assert sub_menu.winfo_exists() is True
        ctx_menu.unpost()
        assert ctx_menu.winfo_exists() is False
        assert sub_menu.winfo_exists() is False
        _close_active_context_menu()

        # 验证高分屏 (如 2560x1440 @ 150% DPI, winfo_screenwidth=1707) 下右键坐标 (x=2100, y=780) 不会被错误左移截断到 <1707
        ctx_menu._get_dpi_scale = lambda: 1.5
        mon_l, mon_t, mon_r, mon_b = _get_physical_screen_bounds(app, scale=1.5, ref_x=2100, ref_y=780)
        assert mon_r >= int(round(int(app.winfo_screenwidth()) * 1.5))
        assert mon_b >= int(round(int(app.winfo_screenheight()) * 1.5))
        post_context_menu(ctx_menu, SimpleNamespace(x_root=2100, y_root=780))
        assert ctx_menu._pop_bounds[0] == 2100
        assert ctx_menu._pop_bounds[1] == 780
        _close_active_context_menu()

        # 验证代码编辑器 (ScriptEditorDialog.txt_code) 与源码预览框支持右键复制/粘贴/全选菜单
        assert callable(getattr(app.txt_script_code_preview, "_on_text_right_click", None))
        assert callable(getattr(app.txt_script_log, "_on_text_right_click", None))
        app.txt_script_code_preview._on_text_right_click(SimpleNamespace(x_root=240, y_root=240))
        preview_menus = list(_ACTIVE_CONTEXT_MENU.get("menus") or [])
        assert len(preview_menus) == 1
        preview_labels = [it.get("label") for it in preview_menus[0]._items if it.get("type") == "command"]
        assert "复制选中内容" in preview_labels
        assert "复制全部内容" in preview_labels
        assert "全选" in preview_labels
        assert "编辑脚本代码..." in preview_labels
        _close_active_context_menu()

        editor_dlg = app.on_edit_script()
        assert editor_dlg is not None
        try:
            assert callable(getattr(editor_dlg.txt_code, "_on_text_right_click", None))
            assert callable(getattr(editor_dlg.entry_name, "_on_text_right_click", None))
            assert callable(getattr(editor_dlg.entry_desc, "_on_text_right_click", None))
            res_ed = editor_dlg.txt_code._on_text_right_click(SimpleNamespace(x_root=300, y_root=300))
            assert res_ed == "break"
            ed_menus = list(_ACTIVE_CONTEXT_MENU.get("menus") or [])
            assert len(ed_menus) == 1
            ed_labels = [it.get("label") for it in ed_menus[0]._items if it.get("type") == "command"]
            assert "剪切" in ed_labels
            assert "复制" in ed_labels
            assert "粘贴" in ed_labels
            assert "复制全部" in ed_labels
            assert "全选" in ed_labels
            assert "清空内容" in ed_labels
            _close_active_context_menu()
        finally:
            editor_dlg.destroy()

        # 验证 Toolkit 内置脚本右键菜单：切换选中脚本触发子控件重排时不会误关菜单
        for builtin_id in ("builtin_f0_group_mean", "builtin_vowel_space"):
            assert builtin_id in app.script_tree.buttons
            btn_item = app.script_tree.buttons[builtin_id]
            res = btn_item._on_right_click(SimpleNamespace(x_root=180, y_root=220))
            assert res == "break"
            app.update_idletasks()
            active_menus = list(_ACTIVE_CONTEXT_MENU.get("menus") or [])
            assert len(active_menus) == 1
            active_m = active_menus[0]
            assert active_m.winfo_exists() is True
            labels = [it.get("label") for it in active_m._items if it.get("type") == "command"]
            assert "运行脚本" in labels
            assert "编辑脚本" in labels
            assert "导出脚本..." in labels
            _close_active_context_menu()

        dropdown = ctk.CTkOptionMenu(app, values=["选项A", "选项B", "选项C"])
        _apply_custom_arrow(dropdown)
        app.update_idletasks()

        assert getattr(dropdown, "_has_custom_popover", False) is True
        assert getattr(dropdown, "_custom_popover_window", None) is None

        # 第一次调用：展开自定义浮层
        dropdown._open_dropdown_menu()
        pop_win = getattr(dropdown, "_custom_popover_window", None)
        assert pop_win is not None
        assert pop_win.winfo_exists()
        assert _ACTIVE_DROPDOWN_POPOVER.get("owner") is dropdown
        pop_win.update_idletasks()
        # 验证浮层物理像素高度充足，不会在高分屏下截断底部选项
        assert int(pop_win.winfo_reqheight()) <= int(pop_win.geometry().split("+")[0].split("x")[1])

        # 第二次调用：关闭自定义浮层（Toggle 取消）
        dropdown._open_dropdown_menu()
        assert getattr(dropdown, "_custom_popover_window", None) is None
        assert _ACTIVE_DROPDOWN_POPOVER.get("window") is None
        dropdown.destroy()

        # 5) 验证 AcousticChartExportDialog 中 lbl_qc_hover_status 默认隐藏、combo_dpi 未 pack、var_legend_outside 默认为 True、复选框形状与自定义比例内联控件
        spk = SimpleNamespace(
            id="s1",
            name="女1",
            items={
                "i1": {
                    "label": "天",
                    "group": "11_阴平",
                    "start": 0.0,
                    "end": 0.25,
                    "pitch_data": {"xs": [0.0, 0.1, 0.2], "freqs": [210.0, 215.0, 220.0]},
                }
            },
        )
        dlg_tree = SimpleNamespace(
            app_state_params={"analysis_mode": "f0", "pts": 11},
            items=spk.items,
            _get_items_by_group_for_dict=lambda d: [("11_阴平", list(d.keys()))],
            _ensure_item_loaded=lambda item: None,
            _extract_syl_data=lambda item, n: (0.25, [(0.25, [210.0] * n)]),
            _get_pitch_arrays_for_item=lambda item: (np.linspace(0.0, 0.25, 11), np.full(11, 210.0)),
        )
        orig_update_preview = AcousticChartExportDialog.update_preview
        AcousticChartExportDialog.update_preview = lambda self: None
        try:
            dlg = AcousticChartExportDialog(app, project_tree=dlg_tree, all_speakers=[spk])
            try:
                assert dlg.var_legend_outside.get() is True
                assert dlg.lbl_qc_hover_status.grid_info() == {}
                assert dlg.combo_dpi.winfo_manager() == ""
                assert getattr(dlg.combo_layout_size, "_has_custom_popover", False) is True
                assert getattr(dlg.combo_format, "_has_custom_popover", False) is True
                # 未勾选复选框为圆角矩形(6)，已勾选复选框为圆形(1000)
                assert dlg.cb_show_title.get() == 0
                assert dlg.cb_show_title._corner_radius == 6
                assert dlg.cb_legend_outside.get() == 1
                assert dlg.cb_legend_outside._corner_radius == 1000
                # 自定义比例内联控件：默认隐藏，切换至“自定义”后显示并支持滑块与 W:H 文本输入
                assert dlg.custom_ratio_inline_frame.winfo_manager() == ""
                dlg.combo_ratio_mode.set("自定义")
                dlg._on_ratio_mode_changed("自定义")
                assert dlg.custom_ratio_inline_frame.winfo_manager() == "pack"
                dlg._on_ratio_slider_change(2.0)
                assert dlg.var_image_ratio_custom.get() == pytest.approx(2.0)
                assert dlg.entry_ratio_custom.get() == "2.00"
                dlg.entry_ratio_custom.delete(0, "end")
                dlg.entry_ratio_custom.insert(0, "16:9")
                dlg._on_custom_ratio_entry_commit()
                assert dlg.var_image_ratio_custom.get() == pytest.approx(16.0 / 9.0, rel=1e-3)
            finally:
                dlg._cancel_preview_render()
                dlg.destroy()
                _close_active_dropdown_popover()
                _close_active_context_menu()
        finally:
            AcousticChartExportDialog.update_preview = orig_update_preview
    finally:
        app.destroy()


def test_chao_axis_legend_non_overlap_and_custom_dropdown_popover():
    """验证声调格局图开启赵元任五度标尺且含 8 个调类组时，外置图例 bbox 与右侧五度标尺 bbox 完全不重叠，且不超出画布右边界。"""
    mock_tree = SimpleNamespace(app_state_params={"analysis_mode": "f0", "pts": 11}, items={})
    exporter = AcousticChartExporter(mock_tree)
    groups = ["11_阴平", "12_阳平", "13_阴上", "14_阴去", "21_阳平", "22_阳上", "24_阳去", "31_入声"]
    entries = []
    for g_idx, g_name in enumerate(groups):
        base_t = 0.6 + 0.52 * g_idx
        t_curve = np.clip(np.linspace(base_t, base_t + 0.35, 11), 0.1, 4.9)
        hz_curve = 120.0 * (2.0 ** (t_curve / 2.5))
        entries.append(
            {
                "item_id": f"it_{g_idx}",
                "speaker_name": "女1",
                "group": g_name,
                "label": f"词{g_idx}",
                "syl_data": [(0.28, hz_curve)],
                "normalized_syl_data": [(0.28, t_curve)],
                "raw_xs": np.linspace(0.0, 0.28, 11),
                "raw_freqs": hz_curve,
                "raw_t": t_curve,
            }
        )

    exporter.params = {
        "work_mode": "publication",
        "chart_type": "contour",
        "scale": "T 值 (五度值)",
        "show_chao_five_level_axis": True,
        "legend_loc": "右上",
        "legend_outside": True,
    }
    fig = exporter.generate_plot(entries, is_preview=False)
    try:
        fig.canvas.draw()
        renderer = fig.canvas.get_renderer()
        primary_ax = next(a for a in fig.axes if not getattr(a, "_is_chao_axis", False))
        chao_ax = next(a for a in fig.axes if getattr(a, "_is_chao_axis", False))
        leg = primary_ax.get_legend()
        assert leg is not None
        leg_bbox = leg.get_window_extent(renderer)
        chao_bbox = chao_ax.get_tightbbox(renderer)
        assert chao_bbox is not None
        assert float(leg_bbox.x0) >= float(chao_bbox.x1) + 4.0
        assert float(leg_bbox.x1) <= float(fig.bbox.x1) + 1.0
    finally:
        plt.close(fig)

    # 验证自定义宽高比 ("自定义", 如 2.0 或 "5:4") 在 generate_plot 中生效
    exporter.params = {
        "work_mode": "publication",
        "chart_type": "contour",
        "scale": "T 值 (五度值)",
        "layout_size": "双栏通栏 150mm",
        "image_ratio_mode": "自定义",
        "image_ratio_custom": "2:1",
    }
    fig_r = exporter.generate_plot(entries, is_preview=False)
    try:
        w_in, h_in = fig_r.get_size_inches()
        assert (float(w_in) / float(h_in)) == pytest.approx(2.0, rel=1e-2)
    finally:
        plt.close(fig_r)


