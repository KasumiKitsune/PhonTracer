# -*- coding: utf-8 -*-
"""
Acoustic feature aggregation and statistical analysis across groups, items, and speakers.
Refactored from modules/report_generator.py (L599-L1268).
"""
import os
import zipfile
from collections import Counter
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np

from ..core.acoustic_math import (
    _t_to_5deg_continuous,
    fit_chao_tone_code,
    hz_to_chao_t,
)
from ..core.text_io import split_into_syllables
from ..data_utils import compute_speaker_f0_bounds, get_item_syllable_bounds
from .item_inspector import analyze_item_anomalies, load_item_cache_if_any

__all__ = [
    "_extract_item_acoustic_record",
    "_round_or_none",
    "_aggregate_bucket_records",
    "compute_acoustic_statistics",
]

def _extract_item_acoustic_record(
    item_id: str,
    item: Dict[str, Any],
    spk_name: str,
    spk_params: Dict[str, Any],
    zip_ref: Optional[zipfile.ZipFile] = None,
    spk: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    label = str(item.get("label") or item.get("word") or item_id)
    group = str(item.get("group") or "默认组")
    t_s = item.get("start")
    t_e = item.get("end")
    pts = int(item.get("pts") or spk_params.get("pts", 11) or 11)

    duration_ms = None
    if t_s is not None and t_e is not None:
        try:
            dur_s = float(t_e) - float(t_s)
            if dur_s > 0:
                duration_ms = float(dur_s * 1000.0)
        except (TypeError, ValueError):
            duration_ms = None

    pitch_data, formant_data, p_status, f_status = load_item_cache_if_any(item, zip_ref, spk=spk)
    warnings_list = analyze_item_anomalies(item, pitch_data, formant_data, spk_params, p_status, f_status)
    if item.get("has_empty_data") and not any("无基频帧" in w for w in warnings_list):
        warnings_list.append("[复核] 采样点含无基频帧")
    for w in (item.get("warnings") or []):
        if w and w not in warnings_list:
            warnings_list.append(str(w))

    from ..data_utils import get_item_syllable_bounds, split_into_syllables

    bounds = get_item_syllable_bounds(item)
    syls = split_into_syllables(label)
    if not bounds:
        if t_s is not None and t_e is not None and float(t_e) > float(t_s):
            bounds = [[float(t_s), float(t_e)]]
        else:
            bounds = [[0.0, 0.0]]
    if not syls:
        syls = [label]

    p_xs = np.asarray(pitch_data.get("xs", []), dtype=float) if (pitch_data and "xs" in pitch_data) else np.array([], dtype=float)
    p_freqs = np.asarray(pitch_data.get("freqs", []), dtype=float) if (pitch_data and "freqs" in pitch_data) else np.array([], dtype=float)
    f_xs = np.asarray(formant_data.get("xs", []), dtype=float) if (formant_data and "xs" in formant_data) else np.array([], dtype=float)
    f1_arr = np.asarray(formant_data.get("f1", []), dtype=float) if (formant_data and "f1" in formant_data) else np.array([], dtype=float)
    f2_arr = np.asarray(formant_data.get("f2", []), dtype=float) if (formant_data and "f2" in formant_data) else np.array([], dtype=float)
    f3_arr = np.asarray(formant_data.get("f3", []), dtype=float) if (formant_data and formant_data.get("f3") is not None) else np.array([], dtype=float)

    def _sample_segment_f0(c_s: float, c_e: float, n_pts: int):
        seg_11 = np.full(n_pts, np.nan, dtype=float)
        seg_raw = np.array([], dtype=float)
        if p_xs.size > 0 and p_xs.size == p_freqs.size:
            if c_e > c_s:
                mask = (p_xs >= c_s) & (p_xs <= c_e) & np.isfinite(p_freqs) & (p_freqs > 0)
            else:
                mask = np.isfinite(p_freqs) & (p_freqs > 0)
            valid_idx = np.where(mask)[0]
            if valid_idx.size >= 2:
                seg_xs = p_xs[valid_idx]
                seg_ys = p_freqs[valid_idx]
                seg_raw = seg_ys.copy()
                v_s, v_e = float(seg_xs[0]), float(seg_xs[-1])
                if v_e > v_s:
                    sample_times = np.linspace(v_s, v_e, n_pts)
                    interp_vals = np.interp(sample_times, seg_xs, seg_ys)
                    for idx_t, st in enumerate(sample_times):
                        if np.min(np.abs(seg_xs - st)) > 0.035:
                            interp_vals[idx_t] = np.nan
                    if np.count_nonzero(np.isfinite(interp_vals) & (interp_vals > 0)) >= 2:
                        seg_11 = interp_vals
                    else:
                        seg_11 = np.interp(np.linspace(0.0, 1.0, n_pts), np.linspace(0.0, 1.0, seg_ys.size), seg_ys)
                else:
                    seg_11 = np.full(n_pts, float(np.mean(seg_ys)), dtype=float)
            elif valid_idx.size == 1:
                only_f0 = float(p_freqs[valid_idx[0]])
                seg_raw = np.array([only_f0], dtype=float)
                seg_11 = np.full(n_pts, only_f0, dtype=float)
        return seg_11, seg_raw

    def _sample_segment_formants(c_s: float, c_e: float):
        sf1 = sf2 = sf3 = None
        if f1_arr.size > 0 and f2_arr.size > 0:
            if f_xs.size == f1_arr.size and f_xs.size > 0 and c_e > c_s:
                f_mask = (f_xs >= c_s) & (f_xs <= c_e)
                if not np.any(f_mask):
                    f_mask = np.ones(f1_arr.size, dtype=bool)
            else:
                f_mask = np.ones(f1_arr.size, dtype=bool)
            s_f1 = f1_arr[f_mask]
            v_f1 = s_f1[np.isfinite(s_f1) & (s_f1 > 0)]
            if v_f1.size > 0:
                sf1 = float(np.mean(v_f1))
            s_f2 = f2_arr[f_mask[:f2_arr.size]] if f_mask.size >= f2_arr.size else f2_arr
            v_f2 = s_f2[np.isfinite(s_f2) & (s_f2 > 0)]
            if v_f2.size > 0:
                sf2 = float(np.mean(v_f2))
            if f3_arr.size > 0:
                s_f3 = f3_arr[f_mask[:f3_arr.size]] if f_mask.size >= f3_arr.size else f3_arr
                v_f3 = s_f3[np.isfinite(s_f3) & (s_f3 > 0)]
                if v_f3.size > 0:
                    sf3 = float(np.mean(v_f3))
        return sf1, sf2, sf3

    # Extract per-syllable F0 & Formants (BUG-M09)
    syllable_records = []
    syl_f0_11_list = []
    raw_valid_f0_parts = []
    for s_idx, (c_s, c_e) in enumerate(bounds):
        char_str = syls[s_idx] if s_idx < len(syls) else f"字{s_idx + 1}"
        s_f0_11, s_raw = _sample_segment_f0(float(c_s), float(c_e), pts)
        syl_f0_11_list.append(s_f0_11)
        if s_raw.size > 0:
            raw_valid_f0_parts.append(s_raw)
        s_valid_mask = np.isfinite(s_f0_11) & (s_f0_11 > 0)
        s_valid = s_f0_11[s_valid_mask]
        if s_valid.size > 0:
            s_start = float(s_f0_11[0]) if (np.isfinite(s_f0_11[0]) and s_f0_11[0] > 0) else float(s_valid[0])
            s_mid_i = len(s_f0_11) // 2
            s_mid = float(s_f0_11[s_mid_i]) if (np.isfinite(s_f0_11[s_mid_i]) and s_f0_11[s_mid_i] > 0) else float(s_valid[len(s_valid) // 2])
            s_end = float(s_f0_11[-1]) if (np.isfinite(s_f0_11[-1]) and s_f0_11[-1] > 0) else float(s_valid[-1])
            x_norm_s = np.linspace(0.0, 1.0, len(s_f0_11))
            if s_valid.size >= 2:
                s_5pt = np.interp([0.01, 0.25, 0.50, 0.75, 1.00], x_norm_s[s_valid_mask], s_f0_11[s_valid_mask])
            else:
                s_5pt = np.full(5, float(s_valid[0]), dtype=float)
        else:
            s_start = s_mid = s_end = None
            s_5pt = np.full(5, np.nan, dtype=float)
        sf1, sf2, sf3 = _sample_segment_formants(float(c_s), float(c_e))
        syllable_records.append({
            "syllable_index": s_idx,
            "char": char_str,
            "bounds": [float(c_s), float(c_e)],
            "f0_11": s_f0_11,
            "f0_5pt": s_5pt,
            "f0_start": s_start,
            "f0_mid": s_mid,
            "f0_end": s_end,
            "f1_mean": sf1,
            "f2_mean": sf2,
            "f3_mean": sf3,
        })

    raw_valid_f0 = np.concatenate(raw_valid_f0_parts) if raw_valid_f0_parts else np.array([], dtype=float)
    if len(syl_f0_11_list) == 1:
        f0_11 = syl_f0_11_list[0]
    elif syl_f0_11_list:
        # Concatenate per-syllable 11-point contours so all syllables are represented
        concat_11 = np.concatenate(syl_f0_11_list)
        valid_c = np.isfinite(concat_11) & (concat_11 > 0)
        if np.count_nonzero(valid_c) >= 2:
            x_src = np.linspace(0.0, 1.0, concat_11.size)
            f0_11 = np.interp(np.linspace(0.0, 1.0, pts), x_src[valid_c], concat_11[valid_c])
        else:
            f0_11 = np.full(pts, np.nan, dtype=float)
    else:
        f0_11 = np.full(pts, np.nan, dtype=float)

    if not np.any(np.isfinite(f0_11) & (f0_11 > 0)) and item.get("preview_f0"):
        prev = np.asarray(item.get("preview_f0"), dtype=float)
        valid_prev = np.where(np.isfinite(prev) & (prev > 0), prev, np.nan)
        if np.any(np.isfinite(valid_prev)):
            if valid_prev.size == pts:
                f0_11 = valid_prev
            else:
                vp = valid_prev[np.isfinite(valid_prev)]
                if vp.size >= 2:
                    f0_11 = np.interp(np.linspace(0.0, 1.0, pts), np.linspace(0.0, 1.0, vp.size), vp)
                elif vp.size == 1:
                    f0_11 = np.full(pts, float(vp[0]), dtype=float)
            raw_valid_f0 = valid_prev[np.isfinite(valid_prev)]
            if len(syllable_records) == 1:
                syllable_records[0]["f0_11"] = f0_11.copy()

    # Pool all valid per-syllable 11-point samples for accurate item statistics
    all_syl_valid = np.concatenate([
        sr["f0_11"][np.isfinite(sr["f0_11"]) & (sr["f0_11"] > 0)]
        for sr in syllable_records
        if np.any(np.isfinite(sr["f0_11"]) & (sr["f0_11"] > 0))
    ]) if any(np.any(np.isfinite(sr["f0_11"]) & (sr["f0_11"] > 0)) for sr in syllable_records) else f0_11[np.isfinite(f0_11) & (f0_11 > 0)]

    valid_11_mask = np.isfinite(f0_11) & (f0_11 > 0)
    valid_11 = f0_11[valid_11_mask]
    has_valid_f0 = bool(all_syl_valid.size > 0 or valid_11.size > 0)

    if has_valid_f0:
        ref_pts = all_syl_valid if all_syl_valid.size > 0 else valid_11
        f0_start = float(f0_11[0]) if (np.isfinite(f0_11[0]) and f0_11[0] > 0) else float(ref_pts[0])
        mid_i = len(f0_11) // 2
        f0_mid = float(f0_11[mid_i]) if (np.isfinite(f0_11[mid_i]) and f0_11[mid_i] > 0) else float(ref_pts[len(ref_pts) // 2])
        f0_end = float(f0_11[-1]) if (np.isfinite(f0_11[-1]) and f0_11[-1] > 0) else float(ref_pts[-1])
        pool_for_stats = raw_valid_f0 if raw_valid_f0.size >= ref_pts.size else ref_pts
        f0_min = float(np.min(ref_pts))
        f0_max = float(np.max(ref_pts))
        f0_range = float(f0_max - f0_min)
        f0_mean = float(np.mean(ref_pts))
        f0_sd = float(np.std(ref_pts))
        x_norm = np.linspace(0.0, 1.0, len(f0_11))
        if valid_11.size >= 2:
            f0_5pt = np.interp([0.01, 0.25, 0.50, 0.75, 1.00], x_norm[valid_11_mask], f0_11[valid_11_mask])
        else:
            f0_5pt = np.full(5, float(ref_pts[0]), dtype=float)
    else:
        f0_start = f0_mid = f0_end = f0_min = f0_max = f0_range = f0_mean = f0_sd = None
        f0_5pt = np.full(5, np.nan, dtype=float)
        pool_for_stats = np.array([], dtype=float)

    # Aggregate syllable formant means (equal weight across syllables)
    syl_f1s = [sr["f1_mean"] for sr in syllable_records if sr.get("f1_mean") is not None and np.isfinite(sr["f1_mean"])]
    syl_f2s = [sr["f2_mean"] for sr in syllable_records if sr.get("f2_mean") is not None and np.isfinite(sr["f2_mean"])]
    syl_f3s = [sr["f3_mean"] for sr in syllable_records if sr.get("f3_mean") is not None and np.isfinite(sr["f3_mean"])]
    f1_mean = float(np.mean(syl_f1s)) if syl_f1s else None
    f2_mean = float(np.mean(syl_f2s)) if syl_f2s else None
    f3_mean = float(np.mean(syl_f3s)) if syl_f3s else None

    return {
        "item_id": item_id,
        "speaker": spk_name,
        "group": group,
        "label": label,
        "duration_ms": duration_ms,
        "has_valid_f0": has_valid_f0,
        "f0_start": f0_start,
        "f0_mid": f0_mid,
        "f0_end": f0_end,
        "f0_min": f0_min,
        "f0_max": f0_max,
        "f0_range": f0_range,
        "f0_mean": f0_mean,
        "f0_sd": f0_sd,
        "f0_11": f0_11,
        "f0_5pt": f0_5pt,
        "f0_pool": pool_for_stats,
        "f1_mean": f1_mean,
        "f2_mean": f2_mean,
        "f3_mean": f3_mean,
        "syllable_records": syllable_records,
        "warnings": warnings_list,
        "warning_count": 1 if len(warnings_list) > 0 else 0,
    }


def _round_or_none(val: Optional[float], ndigits: int = 2) -> Optional[float]:
    if val is None or not np.isfinite(val):
        return None
    return round(float(val), ndigits)


def _aggregate_bucket_records(bucket_key: str, records: List[Dict[str, Any]], by: str) -> Dict[str, Any]:
    count = len(records)
    valid_f0_records = [r for r in records if r.get("has_valid_f0")]
    valid_f0_count = len(valid_f0_records)
    warning_count = sum(1 for r in records if r.get("warning_count", 0) > 0)

    durs = [float(r["duration_ms"]) for r in records if r.get("duration_ms") is not None and np.isfinite(r["duration_ms"])]
    dur_mean = _round_or_none(float(np.mean(durs)) if durs else None, 2)
    dur_sd = _round_or_none(float(np.std(durs)) if durs else None, 2)

    if valid_f0_records:
        f0_starts = [r["f0_start"] for r in valid_f0_records if r.get("f0_start") is not None]
        f0_mids = [r["f0_mid"] for r in valid_f0_records if r.get("f0_mid") is not None]
        f0_ends = [r["f0_end"] for r in valid_f0_records if r.get("f0_end") is not None]
        f0_mins = [r["f0_min"] for r in valid_f0_records if r.get("f0_min") is not None]
        f0_maxs = [r["f0_max"] for r in valid_f0_records if r.get("f0_max") is not None]
        f0_ranges = [r["f0_range"] for r in valid_f0_records if r.get("f0_range") is not None]
        f0_means = [r["f0_mean"] for r in valid_f0_records if r.get("f0_mean") is not None]

        pooled_f0 = []
        for r in valid_f0_records:
            v11 = r["f0_11"][np.isfinite(r["f0_11"]) & (r["f0_11"] > 0)]
            pooled_f0.extend(v11.tolist())

        f0_start_mean = _round_or_none(float(np.mean(f0_starts)) if f0_starts else None, 2)
        f0_mid_mean = _round_or_none(float(np.mean(f0_mids)) if f0_mids else None, 2)
        f0_end_mean = _round_or_none(float(np.mean(f0_ends)) if f0_ends else None, 2)
        f0_min_mean = _round_or_none(float(np.mean(f0_mins)) if f0_mins else None, 2)
        f0_max_mean = _round_or_none(float(np.mean(f0_maxs)) if f0_maxs else None, 2)
        f0_range_mean = _round_or_none(float(np.mean(f0_ranges)) if f0_ranges else None, 2)
        f0_mean = _round_or_none(float(np.mean(f0_means)) if f0_means else None, 2)
        f0_sd = _round_or_none(float(np.std(pooled_f0)) if pooled_f0 else 0.0, 2)

        t_starts = [r["t_start"] for r in valid_f0_records if r.get("t_start") is not None and np.isfinite(r["t_start"])]
        t_mids = [r["t_mid"] for r in valid_f0_records if r.get("t_mid") is not None and np.isfinite(r["t_mid"])]
        t_ends = [r["t_end"] for r in valid_f0_records if r.get("t_end") is not None and np.isfinite(r["t_end"])]

        t_start = _round_or_none(float(np.mean(t_starts)) if t_starts else None, 2)
        t_mid = _round_or_none(float(np.mean(t_mids)) if t_mids else None, 2)
        t_end = _round_or_none(float(np.mean(t_ends)) if t_ends else None, 2)

        t_5_matrix = np.array([r["t_contour"] for r in valid_f0_records if len(r.get("t_contour") or []) == 5], dtype=float)
        if t_5_matrix.size > 0:
            t_contour = []
            for col_i in range(5):
                col = t_5_matrix[:, col_i]
                valid_col = col[np.isfinite(col)]
                t_contour.append(round(float(np.mean(valid_col)), 2) if valid_col.size > 0 else 3.0)
        else:
            t_contour = []

        max_syl_len = max((len(r.get("syllable_records") or []) for r in valid_f0_records), default=1)
        if max_syl_len > 1:
            syl_codes = []
            for s_i in range(max_syl_len):
                s_contours = [
                    r["syllable_records"][s_i]["t_contour"]
                    for r in valid_f0_records
                    if len(r.get("syllable_records") or []) > s_i and len(r["syllable_records"][s_i].get("t_contour") or []) == 5
                ]
                s_starts = [
                    r["syllable_records"][s_i]["t_start"]
                    for r in valid_f0_records
                    if len(r.get("syllable_records") or []) > s_i and r["syllable_records"][s_i].get("t_start") is not None
                ]
                s_mids = [
                    r["syllable_records"][s_i]["t_mid"]
                    for r in valid_f0_records
                    if len(r.get("syllable_records") or []) > s_i and r["syllable_records"][s_i].get("t_mid") is not None
                ]
                s_ends = [
                    r["syllable_records"][s_i]["t_end"]
                    for r in valid_f0_records
                    if len(r.get("syllable_records") or []) > s_i and r["syllable_records"][s_i].get("t_end") is not None
                ]
                if s_contours:
                    mat_s = np.asarray(s_contours, dtype=float)
                    mean_s_contour = [
                        round(float(np.mean(mat_s[:, ci][np.isfinite(mat_s[:, ci])])), 2)
                        if np.any(np.isfinite(mat_s[:, ci])) else 2.5
                        for ci in range(5)
                    ]
                    code_s = fit_chao_tone_code(
                        mean_s_contour,
                        t_start=float(np.mean(s_starts)) if s_starts else None,
                        t_mid=float(np.mean(s_mids)) if s_mids else None,
                        t_end=float(np.mean(s_ends)) if s_ends else None,
                    )
                    if code_s:
                        syl_codes.append(code_s)
            t_code = "-".join(syl_codes) if syl_codes else fit_chao_tone_code(t_contour, t_start=t_start, t_mid=t_mid, t_end=t_end)
        else:
            t_code = fit_chao_tone_code(t_contour, t_start=t_start, t_mid=t_mid, t_end=t_end)
    else:
        f0_start_mean = f0_mid_mean = f0_end_mean = None
        f0_min_mean = f0_max_mean = f0_range_mean = None
        f0_mean = f0_sd = None
        t_start = t_mid = t_end = None
        t_contour = []
        t_code = ""

    f1s = [r["f1_mean"] for r in records if r.get("f1_mean") is not None and np.isfinite(r["f1_mean"])]
    f2s = [r["f2_mean"] for r in records if r.get("f2_mean") is not None and np.isfinite(r["f2_mean"])]
    f3s = [r["f3_mean"] for r in records if r.get("f3_mean") is not None and np.isfinite(r["f3_mean"])]
    f1_mean = _round_or_none(float(np.mean(f1s)) if f1s else None, 1)
    f2_mean = _round_or_none(float(np.mean(f2s)) if f2s else None, 1)
    f3_mean = _round_or_none(float(np.mean(f3s)) if f3s else None, 1)

    res = {
        "key": bucket_key,
        "name": bucket_key,
        "count": count,
        "valid_f0_count": valid_f0_count,
        "warning_count": warning_count,
        "duration_ms_mean": dur_mean,
        "duration_ms_sd": dur_sd,
        "f0_start_mean": f0_start_mean,
        "f0_mid_mean": f0_mid_mean,
        "f0_end_mean": f0_end_mean,
        "f0_min_mean": f0_min_mean,
        "f0_max_mean": f0_max_mean,
        "f0_range_mean": f0_range_mean,
        "f0_mean": f0_mean,
        "f0_sd": f0_sd,
        "t_start": t_start,
        "t_mid": t_mid,
        "t_end": t_end,
        "t_contour": t_contour,
        "t_code": t_code,
        "f1_mean": f1_mean,
        "f2_mean": f2_mean,
        "f3_mean": f3_mean,
    }
    if by == "group":
        res["group"] = bucket_key
    elif by == "item":
        res["label"] = bucket_key
        res["item"] = bucket_key
        if records:
            res["group"] = records[0].get("group", "默认组")
            res["speakers"] = list(dict.fromkeys(r.get("speaker", "") for r in records if r.get("speaker")))
    elif by == "speaker":
        res["speaker"] = bucket_key
    return res


def compute_acoustic_statistics(
    state: Dict[str, Any],
    zip_ref: Optional[zipfile.ZipFile] = None,
    by: str = "group",
    scope: str = "integrated",
    active_speaker_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    计算结构化声学统计摘要（支持 by=group|item|speaker 与 scope=active|integrated）。
    """
    by_norm = str(by or "group").strip().lower()
    if by_norm in ("word", "label", "items", "words"):
        by_norm = "item"
    elif by_norm in ("groups", "tone"):
        by_norm = "group"
    elif by_norm in ("speakers", "spk"):
        by_norm = "speaker"
    if by_norm not in ("group", "item", "speaker"):
        by_norm = "group"

    scope_norm = str(scope or "integrated").strip().lower()
    if scope_norm not in ("active", "integrated"):
        scope_norm = "integrated"

    speakers_dict = state.get("speakers", {}) if isinstance(state, dict) else {}
    target_active_id = active_speaker_id or (state.get("active_speaker_id") if isinstance(state, dict) else None)

    selected_speakers = []
    if scope_norm == "active" and target_active_id and target_active_id in speakers_dict:
        selected_speakers.append((target_active_id, speakers_dict[target_active_id]))
    elif scope_norm == "active" and speakers_dict:
        first_id = next(iter(speakers_dict.keys()))
        selected_speakers.append((first_id, speakers_dict[first_id]))
    else:
        for sid, spk in speakers_dict.items():
            selected_speakers.append((sid, spk))

    speaker_baselines = {}
    all_records = []

    for sid, spk in selected_speakers:
        if not isinstance(spk, dict):
            continue
        spk_name = str(spk.get("name") or sid)
        spk_params = spk.get("last_params", {}) or {}
        items = spk.get("items", {}) or {}
        if isinstance(items, list):
            items_iter = [(str(it.get("id", idx)), it) for idx, it in enumerate(items) if isinstance(it, dict)]
        elif isinstance(items, dict):
            items_iter = list(items.items())
        else:
            items_iter = []

        spk_records = []
        spk_f0_values = []
        for item_id, item in items_iter:
            if not isinstance(item, dict):
                continue
            if item.get("is_excluded", False) or item.get("missing", False) or not item.get("success", True):
                continue
            rec = _extract_item_acoustic_record(str(item_id), item, spk_name, spk_params, zip_ref=zip_ref, spk=spk)
            spk_records.append(rec)
            if rec["has_valid_f0"]:
                for sr in (rec.get("syllable_records") or []):
                    sv = sr["f0_11"][np.isfinite(sr["f0_11"]) & (sr["f0_11"] > 0)]
                    spk_f0_values.extend(sv.tolist())
                if not rec.get("syllable_records"):
                    v11 = rec["f0_11"][np.isfinite(rec["f0_11"]) & (rec["f0_11"] > 0)]
                    spk_f0_values.extend(v11.tolist())

        from ..data_utils import compute_speaker_f0_bounds
        bounds_method = (state.get("f0_bounds_method") if isinstance(state, dict) else None) or "p5_p95_iqr"
        if spk_f0_values:
            raw_min = float(np.min(spk_f0_values))
            raw_max = float(np.max(spk_f0_values))
            s_min, s_max = compute_speaker_f0_bounds(spk_f0_values, method=bounds_method)
            s_mean = float(np.mean(spk_f0_values))
            s_sd = float(np.std(spk_f0_values))
            s_p50 = float(np.median(spk_f0_values))
            s_p5 = float(np.percentile(spk_f0_values, 5))
            s_p95 = float(np.percentile(spk_f0_values, 95))
            s_q25, s_q75 = np.percentile(spk_f0_values, [25, 75])
            speaker_baselines[spk_name] = {
                "min_f0": round(raw_min, 2),
                "max_f0": round(raw_max, 2),
                "t_bounds_min_f0": round(s_min, 2),
                "t_bounds_max_f0": round(s_max, 2),
                "mean_f0": round(s_mean, 2),
                "sd_f0": round(s_sd, 2),
                "median_f0": round(s_p50, 2),
                "p50_f0": round(s_p50, 2),
                "p5_f0": round(s_p5, 2),
                "p95_f0": round(s_p95, 2),
                "iqr_f0": round(float(s_q75 - s_q25), 2),
                "count": len(spk_records),
                "valid_f0_count": sum(1 for r in spk_records if r["has_valid_f0"]),
            }
        else:
            s_min, s_max = 75.0, 600.0
            speaker_baselines[spk_name] = {
                "min_f0": None,
                "max_f0": None,
                "mean_f0": None,
                "sd_f0": None,
                "median_f0": None,
                "p50_f0": None,
                "p5_f0": None,
                "p95_f0": None,
                "iqr_f0": None,
                "count": len(spk_records),
                "valid_f0_count": 0,
            }

        for rec in spk_records:
            if rec["has_valid_f0"]:
                rec["t_start"] = _round_or_none(hz_to_chao_t(rec["f0_start"], s_min, s_max), 2)
                rec["t_mid"] = _round_or_none(hz_to_chao_t(rec["f0_mid"], s_min, s_max), 2)
                rec["t_end"] = _round_or_none(hz_to_chao_t(rec["f0_end"], s_min, s_max), 2)
                rec["t_contour"] = [
                    round(float(hz_to_chao_t(v, s_min, s_max) if hz_to_chao_t(v, s_min, s_max) is not None else 2.5), 2)
                    for v in rec["f0_5pt"]
                ]
                syl_recs = rec.get("syllable_records") or []
                syl_codes = []
                for sr in syl_recs:
                    sr_t_start = _round_or_none(hz_to_chao_t(sr.get("f0_start"), s_min, s_max), 2)
                    sr_t_mid = _round_or_none(hz_to_chao_t(sr.get("f0_mid"), s_min, s_max), 2)
                    sr_t_end = _round_or_none(hz_to_chao_t(sr.get("f0_end"), s_min, s_max), 2)
                    sr_5pt = sr.get("f0_5pt")
                    if sr_5pt is not None and np.any(np.isfinite(sr_5pt)):
                        sr_contour = [
                            round(float(hz_to_chao_t(v, s_min, s_max) if hz_to_chao_t(v, s_min, s_max) is not None else 2.5), 2)
                            for v in sr_5pt
                        ]
                    else:
                        sr_contour = []
                    sr_code = fit_chao_tone_code(sr_contour, sr_t_start, sr_t_mid, sr_t_end) if sr_contour else ""
                    sr["t_start"] = sr_t_start
                    sr["t_mid"] = sr_t_mid
                    sr["t_end"] = sr_t_end
                    sr["t_contour"] = sr_contour
                    sr["t_code"] = sr_code
                    if sr_code:
                        syl_codes.append(sr_code)
                if len(syl_codes) > 1:
                    rec["t_code"] = "-".join(syl_codes)
                elif len(syl_codes) == 1:
                    rec["t_code"] = syl_codes[0]
                else:
                    rec["t_code"] = fit_chao_tone_code(rec["t_contour"], rec["t_start"], rec["t_mid"], rec["t_end"])
            else:
                rec["t_start"] = None
                rec["t_mid"] = None
                rec["t_end"] = None
                rec["t_contour"] = []
                rec["t_code"] = ""
            all_records.append(rec)

    buckets: Dict[str, List[Dict[str, Any]]] = {}
    for rec in all_records:
        if by_norm == "group":
            bkey = rec["group"]
        elif by_norm == "item":
            bkey = rec["label"]
        else:
            bkey = rec["speaker"]
        buckets.setdefault(bkey, []).append(rec)

    summary_list = []
    keyed_map = {}
    for bkey, recs in buckets.items():
        agg = _aggregate_bucket_records(bkey, recs, by=by_norm)
        summary_list.append(agg)
        keyed_map[bkey] = agg

    serialized_items = []
    for rec in all_records:
        serialized_items.append({
            "item_id": rec["item_id"],
            "speaker": rec["speaker"],
            "group": rec["group"],
            "label": rec["label"],
            "count": 1,
            "valid_f0_count": 1 if rec["has_valid_f0"] else 0,
            "warning_count": rec["warning_count"],
            "duration_ms_mean": _round_or_none(rec["duration_ms"], 2),
            "duration_ms_sd": 0.0 if rec["duration_ms"] is not None else None,
            "f0_start_mean": _round_or_none(rec["f0_start"], 2),
            "f0_mid_mean": _round_or_none(rec["f0_mid"], 2),
            "f0_end_mean": _round_or_none(rec["f0_end"], 2),
            "f0_min_mean": _round_or_none(rec["f0_min"], 2),
            "f0_max_mean": _round_or_none(rec["f0_max"], 2),
            "f0_range_mean": _round_or_none(rec["f0_range"], 2),
            "f0_mean": _round_or_none(rec["f0_mean"], 2),
            "f0_sd": _round_or_none(rec["f0_sd"], 2),
            "t_start": rec["t_start"],
            "t_mid": rec["t_mid"],
            "t_end": rec["t_end"],
            "t_contour": rec["t_contour"],
            "t_code": rec["t_code"],
            "f1_mean": _round_or_none(rec["f1_mean"], 1),
            "f2_mean": _round_or_none(rec["f2_mean"], 1),
            "f3_mean": _round_or_none(rec["f3_mean"], 1),
        })

    # Compute one-way ANOVA across buckets (when >= 2 groups have >= 2 samples)
    anova_summary = {}
    if len(buckets) >= 2:
        try:
            from scipy.stats import f_oneway
            dur_groups = [
                [float(r["duration_ms"]) for r in recs if r.get("duration_ms") is not None and np.isfinite(r["duration_ms"])]
                for recs in buckets.values()
            ]
            dur_groups = [g for g in dur_groups if len(g) >= 2]
            if len(dur_groups) >= 2:
                f_val, p_val = f_oneway(*dur_groups)
                if np.isfinite(f_val) and np.isfinite(p_val):
                    anova_summary["duration_ms"] = {
                        "f_stat": round(float(f_val), 4),
                        "p_value": round(float(p_val), 6),
                        "significant_p05": bool(p_val < 0.05),
                    }

            f0_groups = [
                [float(r["f0_mean"]) for r in recs if r.get("f0_mean") is not None and np.isfinite(r["f0_mean"])]
                for recs in buckets.values()
            ]
            f0_groups = [g for g in f0_groups if len(g) >= 2]
            if len(f0_groups) >= 2:
                f_val, p_val = f_oneway(*f0_groups)
                if np.isfinite(f_val) and np.isfinite(p_val):
                    anova_summary["f0_mean_hz"] = {
                        "f_stat": round(float(f_val), 4),
                        "p_value": round(float(p_val), 6),
                        "significant_p05": bool(p_val < 0.05),
                    }

            rng_groups = [
                [float(r["f0_range"]) for r in recs if r.get("f0_range") is not None and np.isfinite(r["f0_range"])]
                for recs in buckets.values()
            ]
            rng_groups = [g for g in rng_groups if len(g) >= 2]
            if len(rng_groups) >= 2:
                f_val, p_val = f_oneway(*rng_groups)
                if np.isfinite(f_val) and np.isfinite(p_val):
                    anova_summary["f0_range_hz"] = {
                        "f_stat": round(float(f_val), 4),
                        "p_value": round(float(p_val), 6),
                        "significant_p05": bool(p_val < 0.05),
                    }
        except Exception:
            pass

    result = {
        "by": by_norm,
        "scope": scope_norm,
        "count": len(all_records),
        "valid_f0_count": sum(1 for r in all_records if r["has_valid_f0"]),
        "warning_count": sum(1 for r in all_records if r["warning_count"] > 0),
        "speaker_baselines": speaker_baselines,
        "anova_summary": anova_summary,
        "summary": summary_list,
        "stats": keyed_map,
        "item_records": serialized_items,
    }
    if by_norm == "group":
        result["groups"] = keyed_map
    elif by_norm == "item":
        result["items"] = keyed_map
    elif by_norm == "speaker":
        result["speakers"] = keyed_map
    return result
