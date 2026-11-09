# -*- coding: utf-8 -*-
"""
PhonTracer 语音端点检测 (VAD)、元音起始点 (VOP)、微观韵母核提取与字间边界切分模块 (modules.dsp.segmentation)
"""
import itertools
from typing import Any, Dict, List, Tuple, Union

import numpy as np

try:
    import parselmouth
except ImportError:
    from modules import _parselmouth_fallback as parselmouth
    import sys as _sys
    _sys.modules.setdefault("parselmouth", parselmouth)
    _sys.modules.setdefault("parselmouth.praat", parselmouth.praat)

from .sound_loader import (
    SILENCE_AMPLITUDE_THRESHOLD,
    _get_mono_values,
    load_compatible_sound,
    trim_bounds_by_amplitude,
)

VOP_BUFFER_SEC = 0.02
VOP_WIN_LEN_SEC = 0.010
VOP_HOP_LEN_SEC = 0.002
VAD_TIME_STEP = 0.01
VAD_MIN_DURATION = 0.1
VAD_CLEAR_GAP_THRESHOLD = 0.12


def detect_vowel_onset(snd: parselmouth.Sound, rough_start: float, rough_end: float) -> float:
    """
    智能元音起始点 (VOP) 检测：
    基于 短时能量(STE)突变 + 过零率(ZCR) 惩罚
    取代机械的固定时长裁切。
    """
    buffer = VOP_BUFFER_SEC
    part_s = max(0, rough_start - buffer)
    part_e = min(snd.get_total_duration(), rough_end + buffer)

    if part_e <= part_s:
        return rough_start

    part = snd.extract_part(from_time=part_s, to_time=part_e)
    vals = _get_mono_values(part)
    sr = part.sampling_frequency

    win_len = int(VOP_WIN_LEN_SEC * sr)
    hop_len = int(VOP_HOP_LEN_SEC * sr)

    if len(vals) < win_len:
        return rough_start

    num_frames = (len(vals) - win_len) // hop_len + 1
    if num_frames <= 0:
        return rough_start

    zcr = np.zeros(num_frames)
    ste = np.zeros(num_frames)
    times = np.zeros(num_frames)

    for i in range(num_frames):
        frame = vals[i * hop_len : i * hop_len + win_len]
        frame_centered = frame - np.mean(frame)
        crossings = np.sum(np.abs(np.diff(frame_centered > 0)))
        zcr[i] = crossings / max(1, (win_len - 1))

        ste[i] = np.sqrt(np.mean(frame ** 2))
        times[i] = part_s + (i * hop_len + win_len / 2) / sr

    max_ste = np.max(ste)
    if max_ste < 1e-5:
        return rough_start

    ste_norm = ste / max_ste

    window = np.ones(3) / 3.0
    if len(ste_norm) > 3:
        ste_norm_smooth = np.convolve(ste_norm, window, mode="same")
    else:
        ste_norm_smooth = ste_norm

    ste_diff = np.diff(ste_norm_smooth, prepend=ste_norm_smooth[0])
    ste_diff[ste_diff < 0] = 0

    zcr_penalty = np.clip(1.0 - (zcr / 0.25), 0.0, 1.0)
    vop_scores = ste_diff * zcr_penalty

    valid_mask = (times >= rough_start) & (times <= rough_end)
    if not np.any(valid_mask):
        return rough_start

    valid_times = times[valid_mask]
    valid_scores = vop_scores[valid_mask]

    best_idx = np.argmax(valid_scores)
    if valid_scores[best_idx] < 0.01:
        return rough_start

    return valid_times[best_idx]


def _merge_vad_segments(segs: List[List[float]], merge_threshold: float) -> List[List[float]]:
    merged = []
    for start, end in segs:
        if not merged or start - merged[-1][1] >= merge_threshold:
            merged.append([start, end])
        else:
            merged[-1][1] = end
    return merged


def _fit_vad_segments_to_expected_count(segs: List[List[float]], expected_count: int) -> List[List[float]]:
    """按停顿长度保留最可信的边界，使宏观段落尽量贴合字表数量。"""
    expected_count = max(1, int(expected_count))
    fitted = [list(seg) for seg in segs]
    while len(fitted) > expected_count:
        merge_idx = min(
            range(len(fitted) - 1),
            key=lambda idx: fitted[idx + 1][0] - fitted[idx][1],
        )
        fitted[merge_idx][1] = fitted[merge_idx + 1][1]
        fitted.pop(merge_idx + 1)
    return fitted


def macroscopic_vad(
    snd: parselmouth.Sound,
    expected_count: int = None,
    min_dur: float = VAD_MIN_DURATION,
    merge_thresh: float = VAD_CLEAR_GAP_THRESHOLD,
) -> List[List[float]]:
    """长音频宏观静音检测分割，可按字表数量择优保留停顿。"""
    intensity = snd.to_intensity(time_step=VAD_TIME_STEP)
    vals = intensity.values[0]
    xs = intensity.xs()
    sorted_vals = np.sort(vals[~np.isnan(vals)])

    if len(sorted_vals) > 20:
        max_int = np.mean(sorted_vals[-int(len(sorted_vals) * 0.05) :])
        noise_floor = np.mean(sorted_vals[: int(len(sorted_vals) * 0.1)])
        thresh = max(noise_floor + 15, max_int - 25)
    else:
        thresh = 50.0

    is_sp = vals > thresh

    starts_idx = np.where(np.diff(is_sp.astype(int), prepend=0) == 1)[0]
    ends_idx = np.where(np.diff(is_sp.astype(int), append=0) == -1)[0]

    segs = []
    for s_idx, e_idx in zip(starts_idx, ends_idx):
        if s_idx < len(xs) and e_idx < len(xs):
            segs.append([xs[s_idx], xs[e_idx]])

    usable_segs = [s for s in segs if s[1] - s[0] > VAD_TIME_STEP * 2]
    if expected_count:
        merged = _fit_vad_segments_to_expected_count(usable_segs, expected_count)
    else:
        merged = _merge_vad_segments(usable_segs, merge_thresh)
    return [s for s in merged if s[1] - s[0] > min_dur]


def core_microscopic_vowel_nucleus(
    snd: parselmouth.Sound,
    global_pitch_or_arrays: Union[parselmouth.Pitch, Tuple[np.ndarray, np.ndarray], Dict[str, Any]],
    t_min: float,
    t_max: float,
    drop_db: float,
    skip_front: float,
    trim_silence: bool,
) -> Tuple[float, float, float, float]:
    """微观韵母提取核心算法"""
    try:
        if isinstance(global_pitch_or_arrays, dict):
            xs = global_pitch_or_arrays["xs"]
            freqs = global_pitch_or_arrays["freqs"]
        elif isinstance(global_pitch_or_arrays, tuple):
            xs, freqs = global_pitch_or_arrays
        else:
            xs = global_pitch_or_arrays.xs()
            freqs = global_pitch_or_arrays.selected_array["frequency"]

        part = (
            snd.extract_part(from_time=t_min, to_time=t_max)
            if t_min != 0 or t_max != snd.get_total_duration()
            else snd
        )
        intensity = part.to_intensity()

        try:
            max_int = np.nanmax(intensity.values)
        except Exception:
            return t_min, t_max, t_min, t_max

        best_s, best_e = 0.0, part.get_total_duration()
        thresh = max_int - drop_db
        int_xs = intensity.xs()
        int_vals = intensity.values[0]

        abs_int_xs = t_min + int_xs
        if len(xs) >= 2:
            valid_freqs_interp = np.interp(abs_int_xs, xs, freqs, left=0, right=0)
        else:
            valid_freqs_interp = np.zeros_like(abs_int_xs)

        valid_mask = (int_vals > thresh) & (valid_freqs_interp > 0)
        valid_times = int_xs[valid_mask]

        if len(valid_times) > 2:
            best_s = valid_times[0]
            best_e = valid_times[-1]

            if skip_front > 0.0:
                rough_s_abs = t_min + best_s
                search_end_abs = min(t_min + best_e, rough_s_abs + skip_front)
                search_start_abs = max(t_min, rough_s_abs - 0.02)
                refined_s_abs = detect_vowel_onset(snd, search_start_abs, search_end_abs)
                best_s = min(refined_s_abs - t_min, best_e - 0.01)

        temp_s, temp_e = t_min + best_s, t_min + best_e
        final_s, final_e = temp_s, temp_e

        if trim_silence:
            trim_part = snd.extract_part(from_time=temp_s, to_time=temp_e)
            vals = _get_mono_values(trim_part)
            trim_xs = trim_part.xs()
            final_s, final_e = trim_bounds_by_amplitude(temp_s, temp_e, trim_xs, vals)

        valid_pitch_xs = [x for x, f in zip(xs, freqs) if f > 0 and final_s <= x <= final_e]
        if len(valid_pitch_xs) >= 2:
            final_s = valid_pitch_xs[0]
            final_e = valid_pitch_xs[-1]

        return final_s, final_e, temp_s, temp_e
    except Exception:
        return t_min, t_max, t_min, t_max


def recalculate_bounds_fast(
    snd: parselmouth.Sound,
    global_pitch_or_arrays: Union[parselmouth.Pitch, Tuple[np.ndarray, np.ndarray], Dict[str, Any]],
    temp_s: float,
    temp_e: float,
    trim_silence: bool,
) -> Tuple[float, float]:
    """仅重新计算静音裁切和基频收缩，不重新跑振幅分析"""
    try:
        if isinstance(global_pitch_or_arrays, dict):
            xs = global_pitch_or_arrays["xs"]
            freqs = global_pitch_or_arrays["freqs"]
        elif isinstance(global_pitch_or_arrays, tuple):
            xs, freqs = global_pitch_or_arrays
        else:
            xs = global_pitch_or_arrays.xs()
            freqs = global_pitch_or_arrays.selected_array["frequency"]

        final_s, final_e = temp_s, temp_e

        if trim_silence:
            trim_part = (
                snd.extract_part(from_time=temp_s, to_time=temp_e)
                if temp_s != 0 or temp_e != snd.get_total_duration()
                else snd
            )
            vals = _get_mono_values(trim_part)
            trim_xs = trim_part.xs()
            final_s, final_e = trim_bounds_by_amplitude(temp_s, temp_e, trim_xs, vals)

        valid_pitch_xs = [x for x, f in zip(xs, freqs) if f > 0 and final_s <= x <= final_e]
        if len(valid_pitch_xs) >= 2:
            final_s = valid_pitch_xs[0]
            final_e = valid_pitch_xs[-1]

        return final_s, final_e
    except Exception:
        return temp_s, temp_e


def check_audio_segments(path: str) -> int:
    """在子进程中检查音频区段数量，避免主线程 GIL 冲突"""
    snd = load_compatible_sound(path)
    return len(macroscopic_vad(snd))


def auto_split_inner_word(
    snd: parselmouth.Sound,
    t_min: float,
    t_max: float,
    word_len: int,
    pitch_data: Dict[str, Any] = None,
    output_meta: Dict[str, Any] = None,
) -> List[float]:
    """
    词语模式内部子音节切分算法 (自动识别蓝线)：
    基于组合成本最优搜索能量谷底点，作为字与字之间的切分点。如果失败自动退化为等比例划分。
    """
    n_splits = word_len - 1
    if n_splits <= 0:
        if output_meta is not None:
            output_meta["split_warnings"] = []
            output_meta["split_confidence"] = 1.0
        return []

    fallback_splits = [t_min + (t_max - t_min) * (i / word_len) for i in range(1, word_len)]

    if t_max - t_min < 0.1:
        if output_meta is not None:
            output_meta["split_warnings"] = ["tiny_segment"]
            output_meta["split_confidence"] = 0.3
        return fallback_splits

    try:
        part = snd.extract_part(from_time=t_min, to_time=t_max)
        intensity = part.to_intensity(time_step=0.01)
        vals = intensity.values[0]
        xs = intensity.xs() + t_min

        window = np.ones(5) / 5.0
        if len(vals) < 5:
            if output_meta is not None:
                output_meta["split_warnings"] = ["fallback_equal_split", "no_clear_valley"]
                output_meta["split_confidence"] = 0.3
            return fallback_splits
        smoothed = np.convolve(vals, window, mode="same")

        import scipy.signal
        valleys, _ = scipy.signal.find_peaks(-smoothed, distance=8)

        if len(smoothed) > 0:
            max_val = np.max(smoothed)
            valleys = [v for v in valleys if max_val - smoothed[v] >= 2.0]

        candidates = [float(xs[v]) for v in valleys]

        best_combination = None
        best_cost = float("inf")

        if len(candidates) > 15:
            def local_candidate_score(c):
                dists = [abs(c - (t_min + (t_max - t_min) * (i / word_len))) for i in range(1, word_len)]
                min_dist = min(dists)
                idx = np.argmin(np.abs(xs - c))
                energy = smoothed[idx]
                return energy + 50.0 * (min_dist / (t_max - t_min))

            candidates = sorted(candidates, key=local_candidate_score)[:15]
            candidates.sort()

        for combo in itertools.combinations(candidates, n_splits):
            combo = list(combo)
            cost = 0.0

            splits_with_bounds = [t_min] + combo + [t_max]
            segment_dur_penalty = 0.0
            for k in range(len(splits_with_bounds) - 1):
                d = splits_with_bounds[k + 1] - splits_with_bounds[k]
                ratio = d / (t_max - t_min)
                if d < 0.08:
                    segment_dur_penalty += 10000.0
                elif ratio < (0.4 / word_len):
                    segment_dur_penalty += 500.0
                elif ratio < (0.5 / word_len):
                    segment_dur_penalty += 100.0

            cost += segment_dur_penalty

            for i, s in enumerate(combo):
                t_ref = t_min + (t_max - t_min) * ((i + 1) / word_len)
                dist_cost = 100.0 * ((s - t_ref) / (t_max - t_min)) ** 2
                cost += dist_cost

                idx = np.argmin(np.abs(xs - s))
                cost += smoothed[idx]

                if pitch_data is not None:
                    p_xs = pitch_data.get("xs")
                    p_freqs = pitch_data.get("freqs")
                    if p_xs is not None and p_freqs is not None and len(p_xs) > 0:
                        p_idx = np.argmin(np.abs(p_xs - s))
                        if abs(p_xs[p_idx] - s) < 0.015 and p_freqs[p_idx] > 0:
                            cost += 15.0

            if cost < best_cost:
                best_cost = cost
                best_combination = combo

        warnings = []
        confidence = 1.0

        if best_combination is None or best_cost >= 5000.0:
            splits = fallback_splits
            warnings.append("fallback_equal_split")
            warnings.append("no_clear_valley")
            confidence = 0.3
        else:
            splits = best_combination
            splits_with_bounds = [t_min] + splits + [t_max]
            for k in range(len(splits_with_bounds) - 1):
                d = splits_with_bounds[k + 1] - splits_with_bounds[k]
                ratio = d / (t_max - t_min)
                if d < 0.08:
                    warnings.append("tiny_segment")
                    confidence = min(confidence, 0.4)
                if word_len == 2 and ratio < 0.20:
                    warnings.append("imbalanced_duration")
                    confidence = min(confidence, 0.5)
                elif word_len > 2 and ratio < (0.4 / word_len):
                    warnings.append("imbalanced_duration")
                    confidence = min(confidence, 0.5)

            if pitch_data is not None:
                p_xs = pitch_data.get("xs")
                p_freqs = pitch_data.get("freqs")
                if p_xs is not None and p_freqs is not None and len(p_xs) > 0:
                    for k in range(len(splits_with_bounds) - 1):
                        c_s, c_e = splits_with_bounds[k], splits_with_bounds[k + 1]
                        mask = (p_xs >= c_s) & (p_xs <= c_e)
                        seg_freqs = p_freqs[mask]
                        if len(seg_freqs) > 0:
                            active_ratio = np.sum(seg_freqs > 0) / len(seg_freqs)
                        else:
                            active_ratio = 0.0
                        if active_ratio < 0.30:
                            warnings.append("low_f0_coverage")
                            confidence = min(confidence, 0.5)

        if output_meta is not None:
            output_meta["split_warnings"] = list(set(warnings))
            output_meta["split_confidence"] = confidence

        return splits
    except Exception:
        if output_meta is not None:
            output_meta["split_warnings"] = ["fallback_equal_split"]
            output_meta["split_confidence"] = 0.1
        return fallback_splits


def auto_split_to_chars_bounds(
    snd: parselmouth.Sound,
    mic_s: float,
    mic_e: float,
    inner_splits: List[float],
    label_len: int,
    params: Dict[str, float],
    pitch_data: Union[Dict[str, Any], Tuple[np.ndarray, np.ndarray], parselmouth.Pitch] = None,
) -> List[List[float]]:
    from .pitch_formant_unified import extract_f0

    splits = [mic_s] + [s for s in inner_splits if mic_s < s < mic_e] + [mic_e]
    if len(splits) != label_len + 1:
        splits = np.linspace(mic_s, mic_e, label_len + 1).tolist()

    cached_xs = None
    cached_freqs = None
    if pitch_data is not None:
        try:
            if isinstance(pitch_data, dict):
                cached_xs = np.asarray(pitch_data["xs"], dtype=float)
                cached_freqs = np.asarray(pitch_data["freqs"], dtype=float)
            elif isinstance(pitch_data, tuple) and len(pitch_data) == 2:
                cached_xs = np.asarray(pitch_data[0], dtype=float)
                cached_freqs = np.asarray(pitch_data[1], dtype=float)
            elif hasattr(pitch_data, "xs"):
                cached_xs = np.asarray(pitch_data.xs(), dtype=float)
                cached_freqs = np.asarray(pitch_data.selected_array["frequency"], dtype=float)
                cached_freqs = np.where(np.isnan(cached_freqs) | (cached_freqs <= 0), 0.0, cached_freqs)
        except Exception:
            cached_xs = None
            cached_freqs = None

    chars_bounds = []
    for i in range(len(splits) - 1):
        c_s, c_e = splits[i], splits[i + 1]
        try:
            if c_e - c_s > 0.01:
                if cached_xs is not None and cached_freqs is not None and len(cached_xs) > 0:
                    mask = (cached_xs >= c_s) & (cached_xs <= c_e) & (cached_freqs > 0)
                    valid_xs = cached_xs[mask]
                    if len(valid_xs) >= 2:
                        v_start, v_end = float(valid_xs[0]), float(valid_xs[-1])
                        chars_bounds.append([v_start, v_end])
                    else:
                        chars_bounds.append([c_s, c_e])
                else:
                    c_snd = snd.extract_part(from_time=c_s, to_time=c_e)
                    c_pitch_data = extract_f0(c_snd, params)
                    p_xs = c_pitch_data["xs"] + c_s
                    p_freqs = c_pitch_data["freqs"]
                    valid_idx = np.where(p_freqs > 0)[0]
                    if len(valid_idx) >= 2:
                        v_start, v_end = float(p_xs[valid_idx[0]]), float(p_xs[valid_idx[-1]])
                        chars_bounds.append([v_start, v_end])
                    else:
                        chars_bounds.append([c_s, c_e])
            else:
                chars_bounds.append([c_s, c_e])
        except Exception:
            chars_bounds.append([c_s, c_e])
    return chars_bounds


def find_minimum_intensity_valley(
    snd: parselmouth.Sound,
    t_ref: float,
    search_window: float = 0.08,
) -> float:
    """
    Search in a local window [t_ref - search_window, t_ref + search_window]
    for the exact point of minimum intensity (lowest volume).
    """
    dur = snd.get_total_duration()
    t_min = max(0.0, t_ref - search_window)
    t_max = min(dur, t_ref + search_window)
    if t_max <= t_min:
        return t_ref
    try:
        part = snd.extract_part(from_time=t_min, to_time=t_max)
        intensity = part.to_intensity(time_step=0.005)
        vals = intensity.values[0]
        xs = intensity.xs() + t_min
        if len(vals) > 0:
            min_idx = np.argmin(vals)
            return float(xs[min_idx])
    except Exception:
        pass
    return t_ref


def _normalize_locked_chars_bounds(
    ref_chars_bounds: List[List[float]],
    ms: float,
    duration: float,
) -> List[List[float]]:
    locked_bounds = []
    for bound in ref_chars_bounds or []:
        if not bound or len(bound) < 2:
            continue
        try:
            start = max(0.0, min(duration, float(bound[0]) - ms))
            end = max(0.0, min(duration, float(bound[1]) - ms))
        except (TypeError, ValueError):
            continue
        if end > start:
            locked_bounds.append([start, end])
    locked_bounds.sort(key=lambda value: (value[0], value[1]))
    return locked_bounds


__all__ = [
    "VOP_BUFFER_SEC",
    "VOP_WIN_LEN_SEC",
    "VOP_HOP_LEN_SEC",
    "VAD_TIME_STEP",
    "VAD_MIN_DURATION",
    "VAD_CLEAR_GAP_THRESHOLD",
    "detect_vowel_onset",
    "_merge_vad_segments",
    "_fit_vad_segments_to_expected_count",
    "macroscopic_vad",
    "core_microscopic_vowel_nucleus",
    "recalculate_bounds_fast",
    "check_audio_segments",
    "auto_split_inner_word",
    "auto_split_to_chars_bounds",
    "find_minimum_intensity_valley",
    "_normalize_locked_chars_bounds",
]
