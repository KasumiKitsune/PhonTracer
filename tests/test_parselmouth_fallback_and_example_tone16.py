# -*- coding: utf-8 -*-
"""
针对 modules/_parselmouth_fallback.py 接口契约与 Example/wordlist_tone16 真实声学数据的核验测试。
"""

import json
import math
import os
import struct
import tempfile
import zipfile
from pathlib import Path

import numpy as np
import pytest

import parselmouth
from modules.audio_core import (
    auto_split_inner_word,
    auto_split_to_chars_bounds,
    core_microscopic_vowel_nucleus,
    extract_f0,
    extract_formants,
    load_compatible_sound,
    macroscopic_vad,
)
from modules.project_patch import apply_project_patch_to_teproj
from modules.script_api import ProjectPatchResult, ScriptContext, build_dataset_snapshot
from modules.script_runner import check_script_safety, validate_ast
from modules.wordlist_v2 import flatten_wordlist_document, load_wordlist_document


ROOT_DIR = Path(__file__).resolve().parents[1]
EXAMPLE_DIR = ROOT_DIR / "Example" / "wordlist_tone16"


def test_sound_contract_from_arrays_and_wav_formats(tmp_path):
    """验证 Sound 支持 1D/2D 数组、PCM 16/24/32-bit 与 Float WAV 加载、保存与切片。"""
    sr = 16000.0
    dur = 0.25
    n_samples = int(sr * dur)
    t = np.arange(n_samples, dtype=np.float64) / sr
    sig = 0.6 * np.sin(2.0 * np.pi * 220.0 * t)

    # 1. 1D array
    snd_1d = parselmouth.Sound(sig, sampling_frequency=sr, start_time=0.5)
    assert snd_1d.values.shape == (1, n_samples)
    assert snd_1d.values.dtype == np.float64
    assert snd_1d.sampling_frequency == sr
    assert snd_1d.xmin == pytest.approx(0.5)
    assert snd_1d.xmax == pytest.approx(0.75)
    assert snd_1d.duration == pytest.approx(dur)
    assert snd_1d.get_total_duration() == pytest.approx(dur)
    assert snd_1d.n_samples == n_samples
    assert snd_1d.n_channels == 1
    assert snd_1d.dt == pytest.approx(1.0 / sr)
    assert len(snd_1d.xs()) == n_samples

    # 2. 2D stereo array
    sig_2d = np.vstack([sig, 0.5 * sig])
    snd_2d = parselmouth.Sound(sig_2d, sampling_frequency=sr)
    assert snd_2d.values.shape == (2, n_samples)
    assert snd_2d.n_channels == 2
    assert snd_2d.n_samples == n_samples

    # 3. Save & reload 16-bit WAV
    wav16 = tmp_path / "pcm16.wav"
    snd_1d.save(str(wav16), "WAV")
    snd_loaded16 = parselmouth.Sound(str(wav16))
    assert snd_loaded16.duration == pytest.approx(dur, abs=1e-4)
    assert np.max(np.abs(snd_loaded16.values[0] - sig)) < 1e-3

    # 4. 24-bit PCM WAV
    wav24 = tmp_path / "pcm24.wav"
    import wave
    pcm24_ints = np.clip(np.round(sig * 8388607.0), -8388608, 8388607).astype(np.int32)
    b0 = (pcm24_ints & 0xFF).astype(np.uint8)
    b1 = ((pcm24_ints >> 8) & 0xFF).astype(np.uint8)
    b2 = ((pcm24_ints >> 16) & 0xFF).astype(np.uint8)
    raw24 = np.column_stack([b0, b1, b2]).tobytes()
    with wave.open(str(wav24), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(3)
        wf.setframerate(int(sr))
        wf.writeframes(raw24)
    snd_loaded24 = parselmouth.Sound(str(wav24))
    assert snd_loaded24.duration == pytest.approx(dur, abs=1e-4)
    assert np.max(np.abs(snd_loaded24.values[0] - sig)) < 1e-4

    # 5. Float32 WAV
    wav_f32 = tmp_path / "float32.wav"
    import scipy.io.wavfile
    scipy.io.wavfile.write(str(wav_f32), int(sr), sig.astype(np.float32))
    snd_loaded_f32 = parselmouth.Sound(str(wav_f32))
    assert snd_loaded_f32.duration == pytest.approx(dur, abs=1e-4)
    assert np.max(np.abs(snd_loaded_f32.values[0] - sig)) < 1e-5

    # 6. extract_part (preserve_times=False and True)
    part_rel = snd_loaded16.extract_part(from_time=0.05, to_time=0.15, preserve_times=False)
    assert part_rel.xmin == pytest.approx(0.0)
    assert part_rel.duration == pytest.approx(0.10, abs=1e-3)

    part_abs = snd_loaded16.extract_part(from_time=0.05, to_time=0.15, preserve_times=True)
    assert part_abs.xmin == pytest.approx(0.05)
    assert part_abs.xmax == pytest.approx(0.15, abs=1e-3)


def test_intensity_pitch_formant_spectrogram_and_praat_contracts():
    """验证 Intensity、Pitch、Formant、Spectrogram 及 parselmouth.praat 接口与 AST 拦截。"""
    sr = 16000.0
    dur = 0.5
    t = np.arange(int(sr * dur), dtype=np.float64) / sr
    # 合成带谐波与共振峰包络的周期语音信号 (F0 = 200 Hz)
    f0_true = 200.0
    glottal = np.zeros_like(t)
    for k in range(1, 25):
        if k * f0_true < 0.45 * sr:
            glottal += (1.0 / k) * np.sin(2.0 * np.pi * k * f0_true * t)
    glottal /= np.max(np.abs(glottal))

    # 通过二阶谐振器施加 F1=500 Hz, F2=1500 Hz, F3=2500 Hz
    import scipy.signal
    y = glottal.copy()
    for f_res, bw in [(500.0, 80.0), (1500.0, 120.0), (2500.0, 160.0)]:
        r = math.exp(-math.pi * bw / sr)
        theta = 2.0 * math.pi * f_res / sr
        a = [1.0, -2.0 * r * math.cos(theta), r * r]
        b = [1.0 - r]
        y = scipy.signal.lfilter(b, a, y)
    y = 0.5 * y / np.max(np.abs(y))

    snd = parselmouth.Sound(y, sampling_frequency=sr)

    # 1. Intensity
    intensity = snd.to_intensity(minimum_pitch=100.0, time_step=0.01, subtract_mean=True)
    assert intensity.values.ndim == 2
    assert intensity.values.shape[0] == 1
    assert len(intensity.xs()) == intensity.values.shape[1]
    mid_db = intensity.get_value(0.25)
    assert np.isfinite(mid_db) and 50.0 < mid_db < 100.0
    assert math.isnan(intensity.get_value(-0.5))

    # 2. Pitch (to_pitch_ac & to_pitch)
    pitch = snd.to_pitch_ac(pitch_floor=75.0, pitch_ceiling=500.0, very_accurate=True)
    assert len(pitch.xs()) == len(pitch.selected_array["frequency"])
    assert len(pitch.xs()) == len(pitch.selected_array["strength"])
    f0_mid = pitch.get_value_at_time(0.25)
    assert f0_mid == pytest.approx(f0_true, abs=2.0)
    mat = pitch.to_matrix()
    assert mat.values.shape == (1, len(pitch.xs()))

    pitch2 = snd.to_pitch(pitch_floor=75.0, pitch_ceiling=500.0)
    assert pitch2.get_value_at_time(0.25) == pytest.approx(f0_true, abs=2.0)

    # 3. Formant (to_formant_burg)
    formant = snd.to_formant_burg(max_number_of_formants=3.0, maximum_formant=5000.0, window_length=0.025)
    assert len(formant.xs()) > 10
    f1_est = formant.get_value_at_time(1, 0.25)
    f2_est = formant.get_value_at_time(2, 0.25)
    f3_est = formant.get_value_at_time(3, 0.25)
    b1_est = formant.get_bandwidth_at_time(1, 0.25)
    assert f1_est == pytest.approx(500.0, abs=60.0)
    assert f2_est == pytest.approx(1500.0, abs=100.0)
    assert f3_est == pytest.approx(2500.0, abs=150.0)
    assert np.isfinite(b1_est) and b1_est > 0.0

    # 4. Spectrogram
    spec = snd.to_spectrogram(window_length=0.005, maximum_frequency=5000.0, time_step=0.002, frequency_step=20.0)
    assert spec.values.shape == (len(spec.ys()), len(spec.xs()))
    assert len(spec.x_grid()) == len(spec.xs()) + 1
    assert len(spec.y_grid()) == len(spec.ys()) + 1

    # 5. parselmouth.praat 子模块及 AST 安全拦截
    assert hasattr(parselmouth, "praat")
    assert callable(parselmouth.praat.call)
    assert parselmouth.praat.call(snd, "Get total duration") == pytest.approx(dur)

    bad_script = """
from parselmouth.praat import call
def run(ctx):
    return None
"""
    with pytest.raises(ValueError, match="不允许导入 parselmouth.praat"):
        validate_ast(bad_script)
    with pytest.raises(ValueError, match="不允许导入 parselmouth.praat"):
        check_script_safety(bad_script)


def test_example_wordlist_tone16_structure_and_real_acoustic_pipeline(tmp_path):
    """核验 Example/wordlist_tone16 的 .ptwl、.teproj 与真实男女声 WAV 的 VAD、F0、共振峰及重算闭环。"""
    ptwl_path = EXAMPLE_DIR / "普通话双字组16种声调实验字表.ptwl"
    teproj_path = EXAMPLE_DIR / "普通话双字组16种声调实验.teproj"
    wav_f1 = EXAMPLE_DIR / "女1.wav"
    wav_f2 = EXAMPLE_DIR / "女2.wav"
    wav_m1 = EXAMPLE_DIR / "男1.wav"

    assert ptwl_path.is_file()
    assert teproj_path.is_file()
    assert wav_f1.is_file() and wav_f2.is_file() and wav_m1.is_file()

    # 1. 核验 .ptwl 字表结构
    wl_doc = load_wordlist_document(str(ptwl_path))
    groups, flat_words, records = flatten_wordlist_document(wl_doc)
    assert "普通话双字组" in wl_doc["title"]
    assert len(groups) == 16
    assert len(flat_words) == 32
    assert len(records) == 32
    assert records[0]["label"] == "播音"
    assert records[0]["group"] == "11_阴阴"
    assert records[0]["wordlist_version"] == "v2"

    # 2. 核验 .teproj 内部结构
    with zipfile.ZipFile(teproj_path, "r") as zf:
        proj_state = json.loads(zf.read("project.json").decode("utf-8"))
        speakers = proj_state["speakers"]
        assert len(speakers) == 3

        for spk_id, spk in speakers.items():
            spk_name = spk["name"]
            wav_stem = spk_name.split("_")[-1]
            assert wav_stem in ("女1", "女2", "男1")
            assert len(spk["items"]) == 32

            wav_path = EXAMPLE_DIR / f"{wav_stem}.wav"
            snd = load_compatible_sound(str(wav_path))
            assert snd.sampling_frequency == 24000.0
            assert snd.duration > 40.0

            # 宏观 VAD 切分核验：传入 expected_count=32 应精准切出 32 个双字组语音段，且与 .teproj 高度吻合
            vad_segs = macroscopic_vad(snd, expected_count=32)
            assert len(vad_segs) == 32
            gt_segs = spk["current_macro_segments"]
            assert len(gt_segs) == 32
            start_diffs = [abs(vad_segs[i][0] - gt_segs[i][0]) for i in range(5)]
            assert max(start_diffs) < 0.02

            # F0 提取核验：对前 3 个词条切片提取 F0 并与 .teproj 内置 Praat 基准对比
            lp = spk["last_params"]
            for item_id, item in list(spk["items"].items())[:3]:
                win_s = max(0.0, float(item["macro_start"]) - 0.05)
                win_e = min(snd.duration, float(item["macro_end"]) + 0.05)
                part_snd = snd.extract_part(from_time=win_s, to_time=win_e, preserve_times=True)
                p_res = extract_f0(part_snd, lp)

                with zf.open(item["pitch_data_file"]) as pf:
                    with np.load(pf) as gt_npz:
                        gt_xs = gt_npz["xs"]
                        gt_freqs = gt_npz["freqs"]

                interp_f0 = np.interp(gt_xs, p_res["xs"], p_res["freqs"])
                both_voiced = (gt_freqs > 0) & (interp_f0 > 0)
                assert np.sum(both_voiced) >= 20
                median_err = float(np.median(np.abs(gt_freqs[both_voiced] - interp_f0[both_voiced])))
                assert median_err < 2.0, f"{spk_name} {item['label']} F0 median error too large: {median_err}"

    # 3. 核验对真实词条的微观韵母边界、字间蓝线切分与共振峰提取
    snd_f1 = load_compatible_sound(str(wav_f1))
    part_boyin = snd_f1.extract_part(from_time=0.285, to_time=0.915, preserve_times=False)
    p_boyin = extract_f0(part_boyin, {"pitch_floor": 75, "pitch_ceiling": 600, "voicing_threshold": 0.25})
    mic_s, mic_e, raw_s, raw_e = core_microscopic_vowel_nucleus(
        part_boyin, p_boyin, 0.0, part_boyin.duration, 60.0, 0.0, True
    )
    assert 0.0 <= mic_s < mic_e <= part_boyin.duration
    splits = auto_split_inner_word(part_boyin, raw_s, raw_e, 2, pitch_data=p_boyin)
    assert len(splits) == 1 and raw_s < splits[0] < raw_e
    c_bounds = auto_split_to_chars_bounds(part_boyin, raw_s, raw_e, splits, 2, {"pitch_floor": 75, "pitch_ceiling": 600})
    assert len(c_bounds) == 2

    fmt_res = extract_formants(part_boyin, {"formant_count": 5, "formant_max_hz": 5500.0})
    valid_f1 = fmt_res["f1"][np.isfinite(fmt_res["f1"])]
    valid_f2 = fmt_res["f2"][np.isfinite(fmt_res["f2"])]
    assert len(valid_f1) > 10 and 200.0 < float(np.median(valid_f1)) < 900.0
    assert len(valid_f2) > 10 and 800.0 < float(np.median(valid_f2)) < 3200.0

    # 4. 核验 .teproj 快照加载、语谱图生成与 recompute_pitch / recompute_formant 补丁写回
    snapshot_items = build_dataset_snapshot(str(teproj_path))
    assert len(snapshot_items) == 96
    ctx = ScriptContext(snapshot_items, teproj_path=str(teproj_path))
    item0 = snapshot_items[0]
    item0_snd = ctx.load_item_sound(item0)
    spec_data = ctx.spectrogram_data(item0_snd, max_frequency=4000.0)
    assert spec_data["db"].shape == (len(spec_data["y"]) - 1, len(spec_data["x"]) - 1)

    out_teproj = tmp_path / "recomputed_tone16.teproj"
    patch = ProjectPatchResult(
        [
            ctx.recompute_pitch(item0, params={"pitch_floor": 80, "pitch_ceiling": 550}, reason="沙盒回归重算F0"),
            ctx.recompute_formant(item0, params={"formant_max_hz": 5500.0}, reason="沙盒回归重算共振峰"),
        ],
        title="Example 重算测试",
    )
    patch_summary = apply_project_patch_to_teproj(str(teproj_path), patch, str(out_teproj))
    assert patch_summary["operation_count"] == 2
    assert out_teproj.is_file()


def test_cli_workflow_with_example_wordlist_tone16(tmp_path):
    """验证 CLI 在回退层下可执行 load_long、project_import、export xlsx 与 report_export。"""
    from cli import PhonTracerCLI

    cli = PhonTracerCLI(workspace_dir=str(tmp_path / "cli_ws"))
    try:
        wav_f1 = str(EXAMPLE_DIR / "女1.wav").replace("\\", "/")
        teproj = str(EXAMPLE_DIR / "普通话双字组16种声调实验.teproj").replace("\\", "/")

        cli.onecmd(f'load_long "{wav_f1}"')
        assert cli.long_snd is not None
        assert cli.long_snd.duration == pytest.approx(42.84, abs=0.05)

        cli.onecmd(f'project_import "{teproj}"')
        assert len(cli.speaker_manager.get_all_speakers()) == 3
        assert len(cli.items) == 32

        out_xlsx = str(tmp_path / "tone16_export.xlsx").replace("\\", "/")
        cli.onecmd(f'export xlsx "{out_xlsx}"')
        assert os.path.isfile(out_xlsx)

        rep_dir = str(tmp_path / "report_out").replace("\\", "/")
        cli.onecmd(f'report_export "{teproj}" "{rep_dir}" both')
        assert os.path.isdir(rep_dir)
        assert any(f.endswith(".md") for f in os.listdir(rep_dir))
        assert any(f.endswith(".xlsx") for f in os.listdir(rep_dir))
    finally:
        cli.cleanup_workspace()
