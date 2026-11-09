# -*- coding: utf-8 -*-
"""
PhonTracer 统一基频 (F0) 与共振峰 (Formant) 提取及批处理/长音频并发工作函数模块 (modules.dsp.pitch_formant_unified)
"""
import os
from typing import Any, Dict, List, Tuple

import numpy as np

try:
    import parselmouth
except ImportError:
    from modules import _parselmouth_fallback as parselmouth
    import sys as _sys
    _sys.modules.setdefault("parselmouth", parselmouth)
    _sys.modules.setdefault("parselmouth.praat", parselmouth.praat)

from ..core.text_io import split_into_syllables
from .segmentation import (
    _normalize_locked_chars_bounds,
    auto_split_inner_word,
    auto_split_to_chars_bounds,
    core_microscopic_vowel_nucleus,
    find_minimum_intensity_valley,
)
from .sound_loader import load_compatible_sound


def _normalize_formant_params(params: Dict[str, Any]) -> Tuple[float, float, float, float]:
    """标准化共振峰提取参数 (formant_count, formant_max_hz, formant_window_length, formant_pre_emphasis)。"""
    formant_count = float(params.get("formant_count", 5))
    formant_max_hz = float(params.get("formant_max_hz", 5500.0))
    formant_window_length = float(params.get("formant_window_length", 0.025))
    formant_pre_emphasis = float(params.get("formant_pre_emphasis", 50.0))
    return formant_count, formant_max_hz, formant_window_length, formant_pre_emphasis


def _compute_segment_f0_coverage_warnings(
    p_xs: np.ndarray,
    p_freqs: np.ndarray,
    splits_with_bounds: List[float],
    threshold: float = 0.30,
) -> Tuple[List[str], float]:
    """检查各子音节区间内有效基频覆盖率，低于阈值时返回 low_f0_coverage 警告。"""
    warnings: List[str] = []
    confidence = 1.0
    if p_xs is None or p_freqs is None or len(p_xs) == 0:
        return warnings, confidence
    for k in range(len(splits_with_bounds) - 1):
        c_s, c_e = splits_with_bounds[k], splits_with_bounds[k + 1]
        mask = (p_xs >= c_s) & (p_xs <= c_e)
        seg_freqs = p_freqs[mask]
        active_ratio = float(np.sum(seg_freqs > 0) / len(seg_freqs)) if len(seg_freqs) > 0 else 0.0
        if active_ratio < threshold:
            if "low_f0_coverage" not in warnings:
                warnings.append("low_f0_coverage")
            confidence = min(confidence, 0.5)
    return warnings, confidence


def extract_f0(snd: parselmouth.Sound, params: Dict[str, Any]) -> Dict[str, Any]:
    """
    使用 Parselmouth (Praat) 提取基频。
    返回结构:
    {
        "xs": np.ndarray,      # 时间点的一维 Numpy 数组
        "freqs": np.ndarray,   # 基频值的一维 Numpy 数组 (0.0 表示无声段)
        "engine": "praat"
    }
    """
    pitch_floor = int(params.get("pitch_floor", 75))
    pitch_ceiling = int(params.get("pitch_ceiling", 600))
    voicing_threshold = float(params.get("voicing_threshold", 0.25))

    pitch = snd.to_pitch_ac(
        time_step=None,
        pitch_floor=pitch_floor,
        pitch_ceiling=pitch_ceiling,
        voicing_threshold=voicing_threshold,
        very_accurate=bool(params.get("very_accurate", True)),
        octave_jump_cost=0.9,
    )
    xs = pitch.xs()
    freqs = pitch.selected_array["frequency"]
    freqs_clean = np.where(np.isnan(freqs) | (freqs <= 0), 0.0, freqs)

    return {
        "xs": xs.astype(np.float64),
        "freqs": freqs_clean.astype(np.float64),
        "engine": "praat",
    }


def extract_formants(snd: parselmouth.Sound, params: Dict[str, Any]) -> Dict[str, Any]:
    """
    使用 Parselmouth (Burg 方法) 提取共振峰数据。
    返回结构:
    {
        "xs": np.ndarray,      # 时间点的一维 Numpy 数组
        "f1": np.ndarray,      # F1 值的一维 Numpy 数组 (np.nan 表示无效/未检测到)
        "f2": np.ndarray,      # F2 值的一维 Numpy 数组 (np.nan 表示无效/未检测到)
        "f3": np.ndarray,      # F3 值的一维 Numpy 数组 (np.nan 表示无效/未检测到)
        "engine": "praat_burg",
        "params": dict
    }
    """
    formant_count, formant_max_hz, formant_window_length, formant_pre_emphasis = _normalize_formant_params(params)

    formant = snd.to_formant_burg(
        time_step=None,
        max_number_of_formants=formant_count,
        maximum_formant=formant_max_hz,
        window_length=formant_window_length,
        pre_emphasis_from=formant_pre_emphasis,
    )

    xs = formant.xs()
    num_frames = len(xs)
    f1 = np.zeros(num_frames, dtype=np.float64)
    f2 = np.zeros(num_frames, dtype=np.float64)
    f3 = np.zeros(num_frames, dtype=np.float64)

    for i, t in enumerate(xs):
        v1 = formant.get_value_at_time(1, t)
        v2 = formant.get_value_at_time(2, t)
        v3 = formant.get_value_at_time(3, t)

        if np.isnan(v1) or v1 <= 0 or v1 > formant_max_hz:
            v1 = np.nan
        if np.isnan(v2) or v2 <= 0:
            v2 = np.nan
        if np.isnan(v3) or v3 <= 0:
            v3 = np.nan

        if not np.isnan(v1) and not np.isnan(v2) and v2 <= v1:
            v1 = np.nan
            v2 = np.nan

        f1[i] = v1
        f2[i] = v2
        f3[i] = v3

    return {
        "xs": xs.astype(np.float64),
        "f1": f1,
        "f2": f2,
        "f3": f3,
        "engine": "praat_burg",
        "params": params,
    }


def _sample_preview_f0(
    p_xs: np.ndarray,
    p_freqs: np.ndarray,
    mic_s: float,
    mic_e: float,
    pts: int = 11,
) -> List[float]:
    """统一 11 点（或指定点数）基频采样逻辑：使用 np.interp 线性插值，并在有效点距离 > 25ms 时归零。"""
    preview_times = np.linspace(mic_s, mic_e, pts)
    p_xs_arr = np.asarray(p_xs, dtype=float)
    p_freqs_arr = np.asarray(p_freqs, dtype=float)
    p_freqs_clean = np.where(np.isnan(p_freqs_arr) | (p_freqs_arr <= 0), 0.0, p_freqs_arr)

    if len(p_xs_arr) == 0:
        return [0.0] * pts

    preview_f0 = np.interp(preview_times, p_xs_arr, p_freqs_clean).tolist()
    valid_xs = p_xs_arr[p_freqs_clean > 0]
    if len(valid_xs) == 0:
        return [0.0] * pts

    for j, t in enumerate(preview_times):
        if np.min(np.abs(valid_xs - t)) > 0.025:
            preview_f0[j] = 0.0
    return preview_f0


def _sample_formants_helper(
    snd: parselmouth.Sound,
    mic_s: float,
    mic_e: float,
    params: Dict[str, Any],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    formant_data = extract_formants(snd, params)
    pts = int(params.get("pts", 11))
    strategy = params.get("formant_sample_strategy", "整段11点")
    f_xs = formant_data["xs"]
    f1_arr = formant_data["f1"]
    f2_arr = formant_data["f2"]
    preview_times = np.linspace(mic_s, mic_e, pts)

    if strategy == "中段均值":
        duration = mic_e - mic_s
        m_start = mic_s + duration / 3.0
        m_end = mic_s + 2.0 * duration / 3.0
        mask = (f_xs >= m_start) & (f_xs <= m_end)
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
            if len(f1_valid_idx) == 0 or t < f_xs[0] or t > f_xs[-1]:
                preview_f1.append(np.nan)
            else:
                nearest_idx = np.argmin(np.abs(f_xs[f1_valid_idx] - t))
                if np.abs(f_xs[f1_valid_idx][nearest_idx] - t) > 0.04:
                    preview_f1.append(np.nan)
                else:
                    preview_f1.append(float(np.interp(t, f_xs[f1_valid_idx], f1_arr[f1_valid_idx])))
            if len(f2_valid_idx) == 0 or t < f_xs[0] or t > f_xs[-1]:
                preview_f2.append(np.nan)
            else:
                nearest_idx = np.argmin(np.abs(f_xs[f2_valid_idx] - t))
                if np.abs(f_xs[f2_valid_idx][nearest_idx] - t) > 0.04:
                    preview_f2.append(np.nan)
                else:
                    preview_f2.append(float(np.interp(t, f_xs[f2_valid_idx], f2_arr[f2_valid_idx])))
    return formant_data, {"f1": preview_f1, "f2": preview_f2}


def _extract_item_pitch_and_formant_payload(
    snd: parselmouth.Sound,
    p_xs: np.ndarray,
    p_freqs: np.ndarray,
    mic_s: float,
    mic_e: float,
    params: Dict[str, Any],
    pts: int = 11,
    formant_offset: float = 0.0,
) -> Tuple[List[float], bool, Any, Any]:
    """统一提取单条语音的 11 点预览基频、空值标记与可选共振峰载荷。"""
    preview_f0 = _sample_preview_f0(p_xs, p_freqs, mic_s, mic_e, pts)
    has_empty_data = any(f == 0.0 for f in preview_f0)
    formant_data = None
    preview_formants = None
    if params.get("analysis_mode") == "formant":
        rel_s = mic_s - formant_offset
        rel_e = mic_e - formant_offset
        formant_data, preview_formants = _sample_formants_helper(snd, rel_s, rel_e, params)
        if formant_offset != 0.0 and formant_data is not None and "xs" in formant_data:
            formant_data["xs"] = formant_data["xs"] + formant_offset
    return preview_f0, has_empty_data, formant_data, preview_formants


def batch_process_worker(
    path: str,
    params: Dict[str, float],
    trim_silence: bool,
    word_label: str = "",
) -> Dict[str, Any]:
    try:
        snd = load_compatible_sound(path)
        pitch_data = extract_f0(snd, params)
        mac_s, mac_e = 0.0, snd.get_total_duration()

        mic_s, mic_e, raw_s, raw_e = core_microscopic_vowel_nucleus(
            snd, pitch_data, mac_s, mac_e,
            params["db"], params["skip_front"], trim_silence,
        )

        name = os.path.splitext(os.path.basename(path))[0]
        label_for_split = word_label if word_label else name
        syls = split_into_syllables(label_for_split)

        inner_splits = []
        chars_bounds = []
        split_warnings = []
        split_confidence = 1.0
        if len(syls) > 1:
            meta: Dict[str, Any] = {}
            inner_splits = auto_split_inner_word(snd, raw_s, raw_e, len(syls), pitch_data=pitch_data, output_meta=meta)
            split_warnings = meta.get("split_warnings", [])
            split_confidence = meta.get("split_confidence", 1.0)
            chars_bounds = auto_split_to_chars_bounds(
                snd, raw_s, raw_e, inner_splits, len(syls), params, pitch_data=pitch_data
            )
            if chars_bounds:
                mic_s = chars_bounds[0][0]
                mic_e = chars_bounds[-1][1]
        else:
            chars_bounds = [[mic_s, mic_e]]

        p_xs = pitch_data["xs"]
        p_freqs = pitch_data["freqs"]
        preview_f0, has_empty_data, formant_data, preview_formants = _extract_item_pitch_and_formant_payload(
            snd, p_xs, p_freqs, mic_s, mic_e, params, pts=11, formant_offset=0.0
        )

        return {
            "label": name,
            "path": path,
            "macro_start": mac_s,
            "macro_end": mac_e,
            "start": mic_s,
            "end": mic_e,
            "raw_start": raw_s,
            "raw_end": raw_e,
            "inner_splits": inner_splits,
            "chars_bounds": chars_bounds,
            "preview_f0": preview_f0,
            "pitch_data": pitch_data,
            "formant_data": formant_data,
            "preview_formants": preview_formants,
            "success": True,
            "has_empty_data": has_empty_data,
            "split_warnings": split_warnings,
            "split_confidence": split_confidence,
        }
    except Exception as e:
        fallback_lbl = word_label if word_label else os.path.splitext(os.path.basename(str(path or "")))[0]
        return {"label": fallback_lbl or "未知条目", "group": "独立文件", "success": False, "error": str(e), "path": path}


def long_process_worker(
    snd_values: np.ndarray,
    snd_sf: float,
    pitch_xs: np.ndarray,
    pitch_freqs: np.ndarray,
    ms: float,
    me: float,
    params: Dict[str, float],
    trim_silence: bool,
    word_label: str = "",
    ref_splits: List[float] = None,
    ref_chars_bounds: List[List[float]] = None,
) -> Dict[str, Any]:
    try:
        snd_part = parselmouth.Sound(snd_values, sampling_frequency=snd_sf)
        shifted_xs = pitch_xs - ms
        locked_chars_bounds = _normalize_locked_chars_bounds(ref_chars_bounds or [], ms, snd_part.get_total_duration())

        if locked_chars_bounds:
            mic_s = locked_chars_bounds[0][0]
            mic_e = locked_chars_bounds[-1][1]
            raw_s, raw_e = mic_s, mic_e
        else:
            mic_s, mic_e, raw_s, raw_e = core_microscopic_vowel_nucleus(
                snd_part, (shifted_xs, pitch_freqs), 0.0, snd_part.get_total_duration(),
                params["db"], params["skip_front"], trim_silence,
            )

        inner_splits = []
        chars_bounds = []
        split_warnings = []
        split_confidence = 1.0
        syls = split_into_syllables(word_label) if word_label else []
        local_p_data = {"xs": shifted_xs, "freqs": pitch_freqs}
        if locked_chars_bounds:
            chars_bounds = [[s + ms, e + ms] for s, e in locked_chars_bounds]
            inner_splits = [end for _start, end in chars_bounds[:-1]]
            if syls and len(syls) != len(chars_bounds):
                split_warnings.append("TextGrid chars 层数量与标签音节数不一致，已按 TextGrid 原边界保留。")
                split_confidence = 0.0
        elif syls and len(syls) > 1:
            if ref_splits:
                local_ref_splits = [t - ms for t in ref_splits]
                splits = [find_minimum_intensity_valley(snd_part, t_ref) for t_ref in local_ref_splits]
            else:
                meta: Dict[str, Any] = {}
                splits = auto_split_inner_word(
                    snd_part, raw_s, raw_e, len(syls), pitch_data=local_p_data, output_meta=meta
                )
                split_warnings = meta.get("split_warnings", [])
                split_confidence = meta.get("split_confidence", 1.0)

            local_chars_bounds = auto_split_to_chars_bounds(
                snd_part, raw_s, raw_e, splits, len(syls), params, pitch_data=local_p_data
            )
            chars_bounds = [[s + ms, e + ms] for s, e in local_chars_bounds]
            inner_splits = [t + ms for t in splits]
            if local_chars_bounds:
                mic_s = local_chars_bounds[0][0]
                mic_e = local_chars_bounds[-1][1]
        else:
            chars_bounds = [[mic_s + ms, mic_e + ms]]

        mic_s += ms
        mic_e += ms
        raw_s += ms
        raw_e += ms

        preview_f0, has_empty_data, formant_data, preview_formants = _extract_item_pitch_and_formant_payload(
            snd_part, pitch_xs, pitch_freqs, mic_s, mic_e, params, pts=11, formant_offset=ms
        )

        return {
            "ms": ms,
            "me": me,
            "mis": mic_s,
            "mie": mic_e,
            "raw_s": raw_s,
            "raw_e": raw_e,
            "inner_splits": inner_splits,
            "chars_bounds": chars_bounds,
            "preview_f0": preview_f0,
            "formant_data": formant_data,
            "preview_formants": preview_formants,
            "has_empty_data": has_empty_data,
            "split_warnings": split_warnings,
            "split_confidence": split_confidence,
            "success": True,
        }
    except Exception as e:
        return {"success": False, "error": str(e), "ms": ms, "me": me}


def process_single_long_word(
    snd_values: np.ndarray,
    snd_sf: float,
    word: str,
    ms: float,
    me: float,
    params: Dict[str, float],
    trim: bool,
    pitch_xs: np.ndarray,
    pitch_freqs: np.ndarray,
    ref_splits: List[float] = None,
    ref_chars_bounds: List[List[float]] = None,
) -> Dict[str, Any]:
    """
    Import helper that processes a single word slice from a long audio,
    running long_process_worker and translating keys back to target format.
    """
    res = long_process_worker(
        snd_values, snd_sf, pitch_xs, pitch_freqs, ms, me, params, trim,
        word_label=word, ref_splits=ref_splits, ref_chars_bounds=ref_chars_bounds,
    )
    if res.get("success"):
        return {
            "label": word,
            "macro_start": ms,
            "macro_end": me,
            "start": res["mis"],
            "end": res["mie"],
            "raw_start": res["raw_s"],
            "raw_end": res["raw_e"],
            "inner_splits": res.get("inner_splits", []),
            "chars_bounds": res.get("chars_bounds", []),
            "preview_f0": res.get("preview_f0", []),
            "formant_data": res.get("formant_data"),
            "preview_formants": res.get("preview_formants"),
            "has_empty_data": res.get("has_empty_data", False),
            "split_warnings": res.get("split_warnings", []),
            "split_confidence": res.get("split_confidence", 1.0),
            "success": True,
        }
    return {
        "label": word,
        "success": False,
        "error": res.get("error", "Unknown error"),
    }


def batch_process_worker_with_textgrid(
    path: str,
    tg_path: str,
    params: Dict[str, float],
    trim_silence: bool,
) -> Dict[str, Any]:
    try:
        import textgrid
        tg = textgrid.TextGrid.fromFile(tg_path)

        words_tier = None
        chars_tier = None
        groups_tier = None
        for t in tg.tiers:
            name_lower = t.name.strip().lower()
            if name_lower in ["words", "word"] and words_tier is None:
                words_tier = t
            elif name_lower in ["chars", "char"] and chars_tier is None:
                chars_tier = t
            elif name_lower in ["groups", "group"] and groups_tier is None:
                groups_tier = t

        if not words_tier:
            for t in tg.tiers:
                if isinstance(t, textgrid.IntervalTier):
                    words_tier = t
                    break

        interval = None
        if words_tier:
            for iv in words_tier:
                if iv.mark.strip():
                    interval = iv
                    break

        snd = load_compatible_sound(path)
        total_dur = snd.get_total_duration()

        if not interval:
            lbl = os.path.splitext(os.path.basename(path))[0]
            t_s, t_e = 0.0, total_dur
            grp_name = "导入内容"
            inner_splits = []
            chars_bounds = [[0.0, total_dur]]
            has_locked_chars_bounds = False
        else:
            lbl = interval.mark.strip()
            t_s = max(0.0, interval.minTime)
            t_e = min(total_dur, interval.maxTime)

            grp_name = "导入内容"
            if groups_tier:
                center = (t_s + t_e) / 2.0
                for g_interval in groups_tier:
                    if g_interval.minTime <= center <= g_interval.maxTime:
                        g_lbl = g_interval.mark.strip()
                        if g_lbl:
                            grp_name = g_lbl
                            break

            chars_bounds = []
            inner_splits = []
            has_locked_chars_bounds = False
            if chars_tier:
                overlapping_chars = []
                for c_interval in chars_tier:
                    c_lbl = c_interval.mark.strip()
                    if c_lbl:
                        center = (c_interval.minTime + c_interval.maxTime) / 2.0
                        if t_s <= center <= t_e:
                            overlapping_chars.append(c_interval)
                overlapping_chars.sort(key=lambda c: c.minTime)
                if overlapping_chars:
                    for c in overlapping_chars:
                        chars_bounds.append([c.minTime, c.maxTime])
                    for j in range(len(overlapping_chars) - 1):
                        inner_splits.append(overlapping_chars[j].maxTime)
                    has_locked_chars_bounds = True

            if not chars_bounds:
                syls = split_into_syllables(lbl)
                w_len = len(syls)
                if w_len > 1:
                    splits = np.linspace(t_s, t_e, w_len + 1).tolist()
                    chars_bounds = [[splits[j], splits[j + 1]] for j in range(w_len)]
                    inner_splits = splits[1:-1]
                else:
                    chars_bounds = [[t_s, t_e]]
                    inner_splits = []

        pitch_data = extract_f0(snd, params)
        locked_chars_bounds = _normalize_locked_chars_bounds(chars_bounds, 0.0, total_dur) if has_locked_chars_bounds else []
        if locked_chars_bounds:
            chars_bounds = locked_chars_bounds
            mic_s = chars_bounds[0][0]
            mic_e = chars_bounds[-1][1]
            raw_s, raw_e = mic_s, mic_e
            inner_splits = [end for _start, end in chars_bounds[:-1]]
        else:
            mic_s, mic_e, raw_s, raw_e = core_microscopic_vowel_nucleus(
                snd, pitch_data, t_s, t_e,
                params["db"], params["skip_front"], trim_silence,
            )

        p_xs = pitch_data["xs"]
        p_freqs = pitch_data["freqs"]
        preview_f0, has_empty_data, formant_data, preview_formants = _extract_item_pitch_and_formant_payload(
            snd, p_xs, p_freqs, mic_s, mic_e, params, pts=11, formant_offset=0.0
        )

        return {
            "label": lbl,
            "path": path,
            "group": grp_name,
            "macro_start": t_s,
            "macro_end": t_e,
            "start": mic_s,
            "end": mic_e,
            "raw_start": raw_s,
            "raw_end": raw_e,
            "inner_splits": inner_splits,
            "chars_bounds": chars_bounds,
            "preview_f0": preview_f0,
            "pitch_data": pitch_data,
            "formant_data": formant_data,
            "preview_formants": preview_formants,
            "success": True,
            "has_empty_data": has_empty_data,
        }
    except Exception as e:
        fallback_lbl = os.path.splitext(os.path.basename(str(path or "")))[0]
        return {"label": fallback_lbl or "未知条目", "group": "导入内容", "success": False, "error": str(e), "path": path}


__all__ = [
    "_normalize_formant_params",
    "_compute_segment_f0_coverage_warnings",
    "extract_f0",
    "extract_formants",
    "_sample_preview_f0",
    "_sample_formants_helper",
    "_extract_item_pitch_and_formant_payload",
    "batch_process_worker",
    "long_process_worker",
    "process_single_long_word",
    "batch_process_worker_with_textgrid",
]
