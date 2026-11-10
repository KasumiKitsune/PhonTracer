# -*- coding: utf-8 -*-
"""
Item inspection, parameter recovery, caching, WAV header validation, and anomaly detection.
Refactored from modules/report_generator.py (L13-L589).
"""
import hashlib
import io
import json
import os
import re
import time
import zipfile
from collections import Counter
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np

from ..dsp.anomaly_detector import detect_pitch_anomaly_points
from ..storage.archive_security import validate_project_archive_members
from ..core.text_io import split_into_syllables, _CJK_RE as re_cjk
from ..core.acoustic_math import hz_to_chao_t, _t_to_5deg_continuous, fit_chao_tone_code

__all__ = [
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
    "detect_pitch_anomaly_points",
    "validate_project_archive_members",
    "split_into_syllables",
    "re_cjk",
    "hz_to_chao_t",
    "_t_to_5deg_continuous",
    "fit_chao_tone_code",
]

SPLIT_WARNING_ZH = {
    "tiny_segment": "子段时长偏短",
    "imbalanced_duration": "子段时长比例不均",
    "no_clear_valley": "能量谷底不明显",
    "fallback_equal_split": "等分兜底切分",
    "low_f0_coverage": "有效基频帧偏少",
}

# Field Interpretation Layer
def get_item_override_param(item: Dict[str, Any], key: str, default: Any = None) -> Any:
    """优先从 item['param_overrides'] 读取覆盖参数，并兼容 recomputed_*_params 及条目顶层键。"""
    if not isinstance(item, dict):
        return default
    overrides = item.get("param_overrides")
    if isinstance(overrides, dict) and key in overrides and overrides[key] is not None:
        return overrides[key]
    recomp_pitch = item.get("recomputed_pitch_params")
    if isinstance(recomp_pitch, dict) and key in recomp_pitch and recomp_pitch[key] is not None:
        return recomp_pitch[key]
    recomp_formant = item.get("recomputed_formant_params")
    if isinstance(recomp_formant, dict) and key in recomp_formant and recomp_formant[key] is not None:
        return recomp_formant[key]
    alias_map = {
        "pitch_floor": ("f0_min",),
        "pitch_ceiling": ("f0_max",),
        "voicing_threshold": ("voicing_thresh",),
    }
    for alias in alias_map.get(key, ()):
        if isinstance(overrides, dict) and alias in overrides and overrides[alias] is not None:
            return overrides[alias]
        if isinstance(recomp_pitch, dict) and alias in recomp_pitch and recomp_pitch[alias] is not None:
            return recomp_pitch[alias]
        if alias in item and item[alias] is not None:
            return item[alias]
    if key in item and item[key] is not None:
        return item[key]
    return default

def get_pitch_floor(params: Dict[str, Any], default: float = 75.0) -> float:
    if not params:
        return default
    return get_item_override_param(params, "pitch_floor", params.get("pitch_floor") or params.get("f0_min") or default)

def get_pitch_ceiling(params: Dict[str, Any], default: float = 600.0) -> float:
    if not params:
        return default
    return get_item_override_param(params, "pitch_ceiling", params.get("pitch_ceiling") or params.get("f0_max") or default)

def get_voicing_threshold(params: Dict[str, Any], default: float = 0.25) -> float:
    if not params:
        return default
    return get_item_override_param(params, "voicing_threshold", params.get("voicing_threshold") or params.get("voicing_thresh") or default)

def _majority_value(values: List[Any], fallback: Any) -> Any:
    """返回出现次数最多的值；并列时优先使用发音人记录值。"""
    if not values:
        return fallback
    counts = Counter(values)
    highest_count = max(counts.values())
    candidates = [value for value, count in counts.items() if count == highest_count]
    if fallback in candidates:
        return fallback
    return candidates[0]

def get_majority_item_params(items: Dict[str, Dict[str, Any]], speaker_params: Dict[str, Any]) -> Dict[str, Any]:
    """根据纳入分析的条目计算局部差异基准，不使用界面当前值。"""
    included_items = [item for item in items.values() if not item.get("is_excluded", False)]
    fallbacks = {
        "pitch_floor": get_pitch_floor(speaker_params),
        "pitch_ceiling": get_pitch_ceiling(speaker_params),
        "voicing_threshold": get_voicing_threshold(speaker_params),
        "formant_max_hz": speaker_params.get("formant_max_hz", 5500.0),
        "formant_count": speaker_params.get("formant_count", 5),
        "formant_window_length": speaker_params.get("formant_window_length", 0.025),
        "formant_pre_emphasis": speaker_params.get("formant_pre_emphasis", 50.0),
        "formant_sample_strategy": speaker_params.get("formant_sample_strategy", "整段11点"),
    }
    return {
        "pitch_floor": _majority_value(
            [get_pitch_floor(item, fallbacks["pitch_floor"]) for item in included_items],
            fallbacks["pitch_floor"]
        ),
        "pitch_ceiling": _majority_value(
            [get_pitch_ceiling(item, fallbacks["pitch_ceiling"]) for item in included_items],
            fallbacks["pitch_ceiling"]
        ),
        "voicing_threshold": _majority_value(
            [get_voicing_threshold(item, fallbacks["voicing_threshold"]) for item in included_items],
            fallbacks["voicing_threshold"]
        ),
        "formant_max_hz": _majority_value(
            [get_item_override_param(item, "formant_max_hz", fallbacks["formant_max_hz"]) for item in included_items],
            fallbacks["formant_max_hz"]
        ),
        "formant_count": _majority_value(
            [get_item_override_param(item, "formant_count", fallbacks["formant_count"]) for item in included_items],
            fallbacks["formant_count"]
        ),
        "formant_window_length": _majority_value(
            [get_item_override_param(item, "formant_window_length", fallbacks["formant_window_length"]) for item in included_items],
            fallbacks["formant_window_length"]
        ),
        "formant_pre_emphasis": _majority_value(
            [get_item_override_param(item, "formant_pre_emphasis", fallbacks["formant_pre_emphasis"]) for item in included_items],
            fallbacks["formant_pre_emphasis"]
        ),
        "formant_sample_strategy": _majority_value(
            [get_item_override_param(item, "formant_sample_strategy", fallbacks["formant_sample_strategy"]) for item in included_items],
            fallbacks["formant_sample_strategy"]
        ),
    }

def calculate_sha256(filepath: str) -> str:
    sha256_hash = hashlib.sha256()
    try:
        with open(filepath, "rb") as f:
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)
        return sha256_hash.hexdigest()
    except Exception:
        return "无法计算指纹"

# Note: re, re_cjk, split_into_syllables imported at module top level

def load_item_cache_if_any(item: Dict[str, Any], zip_ref: Optional[zipfile.ZipFile], spk: Optional[Dict[str, Any]] = None) -> Tuple[Any, Any, str, str]:
    pitch_data = None
    formant_data = None
    pitch_status = "无"
    formant_status = "无"

    p_file = item.get("pitch_data_file")
    if not p_file and isinstance(spk, dict):
        p_file = spk.get("long_pitch_data_file")
    if p_file and zip_ref:
        pitch_status = "损坏"
        try:
            p_file = p_file.replace("\\", "/")
            if p_file in zip_ref.namelist():
                data_bytes = zip_ref.read(p_file)
                with np.load(io.BytesIO(data_bytes)) as loaded:
                    pitch_data = {'xs': loaded['xs'].copy(), 'freqs': loaded['freqs'].copy()}
                pitch_status = "正常"
        except Exception:
            pass

    if pitch_data is None and isinstance(item.get("pitch_data"), dict):
        pd = item["pitch_data"]
        if "xs" in pd and "freqs" in pd and pd["xs"] is not None and pd["freqs"] is not None:
            pitch_data = {"xs": np.asarray(pd["xs"], dtype=float), "freqs": np.asarray(pd["freqs"], dtype=float)}
            pitch_status = "正常"

    f_file = item.get("formant_data_file")
    if f_file and zip_ref:
        formant_status = "损坏"
        try:
            f_file = f_file.replace("\\", "/")
            if f_file in zip_ref.namelist():
                data_bytes = zip_ref.read(f_file)
                with np.load(io.BytesIO(data_bytes)) as loaded:
                    formant_dict = {
                        'xs': loaded['xs'].copy(),
                        'f1': loaded['f1'].copy(),
                        'f2': loaded['f2'].copy()
                    }
                    if 'f3' in loaded:
                        formant_dict['f3'] = loaded['f3'].copy()
                    formant_data = formant_dict
                formant_status = "正常"
        except Exception:
            pass

    if formant_data is None:
        fd = item.get("formant_data") or item.get("preview_formants")
        if isinstance(fd, dict) and fd.get("f1") is not None and fd.get("f2") is not None:
            formant_dict = {
                "xs": np.asarray(fd.get("xs", []), dtype=float),
                "f1": np.asarray(fd["f1"], dtype=float),
                "f2": np.asarray(fd["f2"], dtype=float),
            }
            if "f3" in fd and fd["f3"] is not None:
                formant_dict["f3"] = np.asarray(fd["f3"], dtype=float)
            formant_data = formant_dict
            formant_status = "正常"

    return pitch_data, formant_data, pitch_status, formant_status


def check_item_has_empty_f0(item: Dict[str, Any], pitch_data: Any, pts: int) -> bool:
    if not item or item.get('start') is None:
        return False
    if item.get('preview_segment_mismatch'):
        return True
    if not pitch_data:
        if item.get('preview_f0'):
            return any(hz == 0 for hz in item['preview_f0'])
        return False

    t_s, t_e = item['start'], item['end']
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

    p_xs = pitch_data['xs']
    p_freqs = pitch_data['freqs']

    has_empty = False
    for c_s, c_e in bounds:
        if c_e <= c_s:
            continue
        valid_idx = np.where((p_xs >= c_s) & (p_xs <= c_e) & (p_freqs > 0))[0]
        if len(valid_idx) >= 2:
            v_s, v_e = p_xs[valid_idx[0]], p_xs[valid_idx[-1]]
            seg_xs = p_xs[valid_idx]
            seg_ys = p_freqs[valid_idx]
        else:
            has_empty = True
            break

        if v_e <= v_s:
            has_empty = True
            break

        times = np.linspace(v_s, v_e, pts)
        f0s = np.interp(times, seg_xs, seg_ys)
        for t, hz in zip(times, f0s):
            if np.min(np.abs(seg_xs - t)) > 0.025 or np.isnan(hz) or hz <= 0:
                has_empty = True
                break
        if has_empty:
            break

    return has_empty

def check_item_has_empty_formant(item: Dict[str, Any], formant_data: Any) -> bool:
    if not item or item.get('start') is None:
        return False
    if item.get('preview_segment_mismatch'):
        return True
    if not formant_data:
        return False

    f_xs = formant_data.get('xs', np.array([]))
    f1 = formant_data.get('f1', np.array([]))
    f2 = formant_data.get('f2', np.array([]))
    if len(f_xs) == 0:
        return True

    t_s, t_e = item['start'], item['end']
    label = item.get('label', '')
    syls = split_into_syllables(label)
    chars_bounds = item.get('chars_bounds', [])
    if chars_bounds and len(chars_bounds) == len(syls):
        bounds = chars_bounds
    else:
        inner_splits = item.get('inner_splits', [])
        splits = [t_s] + [s for s in inner_splits if t_s < s < t_e] + [t_e]
        if len(syls) > 1 and len(splits) != len(syls) + 1:
            splits = np.linspace(t_s, t_e, len(syls) + 1).tolist()
        elif len(syls) <= 1:
            splits = [t_s, t_e]
        bounds = [[splits[i], splits[i+1]] for i in range(len(splits)-1)]

    has_empty = False
    for idx, (c_s, c_e) in enumerate(bounds):
        dur = c_e - c_s
        if dur <= 0:
            has_empty = True
            break
        margin = dur * 0.125
        core_s = c_s + margin
        core_e = c_e - margin

        mask = (f_xs >= core_s) & (f_xs <= core_e)
        seg_xs = f_xs[mask]
        seg_f1 = f1[mask]
        seg_f2 = f2[mask]
        if len(seg_xs) == 0:
            has_empty = True
            break
        valid_mask = ~np.isnan(seg_f1) & ~np.isnan(seg_f2) & (seg_f2 > seg_f1)
        ratio = np.sum(valid_mask) / len(seg_xs)
        if ratio < 0.40:
            has_empty = True
            break

    return has_empty

def analyze_item_anomalies(item: Dict[str, Any], pitch_data: Any, formant_data: Any, params: Dict[str, Any], pitch_status: str = "无", formant_status: str = "无") -> List[str]:
    if item and item.get('ignore_warnings'):
        return []
    warnings = []

    if pitch_status == "损坏":
        warnings.append("[错误] 缓存文件无法读取 (基频)")
    if formant_status == "损坏":
        warnings.append("[错误] 缓存文件无法读取 (共振峰)")

    if not item or item.get('start') is None:
        warnings.append("[错误] 时间边界无效或缺失")
        return warnings

    if item.get('preview_segment_mismatch'):
        warnings.append("[错误] 子段数量与预览不匹配")

    mode = item.get('analysis_mode') or params.get('analysis_mode', 'f0')
    pts = int(item.get('pts') or params.get('pts', 11))

    if mode == 'formant':
        if not formant_data:
            return warnings
        f_xs = formant_data.get('xs', np.array([]))
        f1 = formant_data.get('f1', np.array([]))
        f2 = formant_data.get('f2', np.array([]))
        if len(f_xs) == 0:
            warnings.append("[复核] 共振峰数据为空")
            return warnings
        if check_item_has_empty_formant(item, formant_data):
            warnings.append("[警告] 共振峰存在明显缺失帧或无效帧，建议复核边界与参数")

        t_s, t_e = item.get('start'), item.get('end')
        label = item.get('label', '')
        syls = split_into_syllables(label)
        chars_bounds = item.get('chars_bounds', [])
        if chars_bounds and len(chars_bounds) == len(syls):
            bounds = chars_bounds
        else:
            inner_splits = item.get('inner_splits', [])
            splits = [t_s] + [s for s in inner_splits if t_s < s < t_e] + [t_e]
            if len(syls) > 1 and len(splits) != len(syls) + 1:
                splits = np.linspace(t_s, t_e, len(syls) + 1).tolist()
            elif len(syls) <= 1:
                splits = [t_s, t_e]
            bounds = [[splits[i], splits[i+1]] for i in range(len(splits)-1)]

        for idx, (c_s, c_e) in enumerate(bounds):
            char = syls[idx] if idx < len(syls) else f"音节{idx+1}"
            dur = c_e - c_s
            if dur <= 0:
                warnings.append(f"[错误] 音节 [{char}] 时间边界无效")
                continue
            margin = dur * 0.125
            core_s = c_s + margin
            core_e = c_e - margin

            mask = (f_xs >= core_s) & (f_xs <= core_e)
            seg_xs = f_xs[mask]
            seg_f1 = f1[mask]
            seg_f2 = f2[mask]
            if len(seg_xs) == 0:
                warnings.append(f"[复核] 音节 [{char}] 核心区间无共振峰数据")
                continue
            valid_mask = ~np.isnan(seg_f1) & ~np.isnan(seg_f2) & (seg_f2 > seg_f1)
            ratio = np.sum(valid_mask) / len(seg_xs)
            if ratio < 0.30:
                warnings.append(f"[复核] 音节 [{char}] 有效共振峰帧比例较低 ({ratio:.1%} < 30%)")
            elif ratio < 0.55:
                warnings.append(f"[警告] 音节 [{char}] 共振峰有效帧比例偏低 ({ratio:.1%} < 55%)")

            finite_pair = np.isfinite(seg_f1) & np.isfinite(seg_f2)
            if np.any(finite_pair):
                bad_order_ratio = float(np.sum(seg_f2[finite_pair] <= seg_f1[finite_pair])) / float(np.sum(finite_pair))
                if bad_order_ratio >= 0.20:
                    warnings.append(f"[警告] 音节 [{char}] 出现较多 F2<=F1 的异常帧 ({bad_order_ratio:.1%})")

            if np.sum(valid_mask) >= 4:
                v_f1 = seg_f1[valid_mask]
                v_f2 = seg_f2[valid_mask]
                f2_diff = np.abs(np.diff(v_f2))
                med_f2 = float(np.nanmedian(v_f2)) if len(v_f2) > 0 else 0.0
                if med_f2 > 0:
                    f2_rel = f2_diff / max(med_f2, 1e-9)
                    if np.any((f2_diff > 260.0) & (f2_rel > 0.20)):
                        max_jump = float(np.max(f2_diff))
                        warnings.append(f"[警告] 音节 [{char}] F2 轨迹跳变异常 (最大跳变 {max_jump:.0f}Hz)")

                f1_diff = np.abs(np.diff(v_f1))
                med_f1 = float(np.nanmedian(v_f1)) if len(v_f1) > 0 else 0.0
                if med_f1 > 0:
                    f1_rel = f1_diff / max(med_f1, 1e-9)
                    if np.any((f1_diff > 180.0) & (f1_rel > 0.30)):
                        max_jump = float(np.max(f1_diff))
                        warnings.append(f"[提示] 音节 [{char}] F1 轨迹波动较大 (最大跳变 {max_jump:.0f}Hz)")

        split_warnings = item.get('split_warnings', [])
        for sw in split_warnings:
            if sw == 'tiny_segment':
                warnings.append("[复核] 音段时长较短 (< 80ms，常见于短促音节或切分偏窄)")
            elif sw == 'imbalanced_duration':
                warnings.append("[警告] 时长严重失衡 (子段时长比例不均)")
            elif sw == 'no_clear_valley':
                warnings.append("[警告] 未能识别到能量谷 (子音节切分谷底不明显)")
            elif sw == 'fallback_equal_split':
                warnings.append("[提示] 采用等分兜底切分")
        return list(dict.fromkeys(warnings))

    if check_item_has_empty_f0(item, pitch_data, pts):
        warnings.append("[复核] 采样点含无基频帧 (F0 = 0，常见于清化、嘎裂声或静音段)")

    if pitch_data:
        t_s, t_e = item.get('start'), item.get('end')
        p_xs = pitch_data['xs']
        p_freqs = pitch_data['freqs']
        mask = (p_xs >= t_s) & (p_xs <= t_e)
        p_xs_slice = p_xs[mask]
        p_freqs_slice = p_freqs[mask]

        syls = split_into_syllables(item.get('label', ''))
        chars_bounds = item.get('chars_bounds', [])
        if chars_bounds and len(chars_bounds) == len(syls):
            bounds = chars_bounds
        else:
            bounds = [[t_s, t_e]]

        anomaly_points = detect_pitch_anomaly_points(
            p_xs_slice, p_freqs_slice, bounds=bounds, start=t_s, end=t_e
        )
        if len(anomaly_points) > 0:
            jump_times = ", ".join([f"{t:.2f}s" for t, _ in anomaly_points[:5]])
            suffix = "..." if len(anomaly_points) > 5 else ""
            warnings.append(f"[警告] 疑似倍频/半频/噪声点 (发生在: {jump_times}{suffix})")

    split_warnings = item.get('split_warnings', [])
    for sw in split_warnings:
        if sw == 'tiny_segment':
            warnings.append("[复核] 音段时长较短 (< 80ms，常见于短促音节或切分偏窄)")
        elif sw == 'imbalanced_duration':
            warnings.append("[警告] 时长严重失衡 (子段时长比例不均)")
        elif sw == 'no_clear_valley':
            warnings.append("[警告] 未能识别到能量谷 (子音节切分谷底不明显)")
        elif sw == 'fallback_equal_split':
            warnings.append("[提示] 采用等分兜底切分")
        elif sw == 'low_f0_coverage':
            warnings.append("[复核] 有效基频帧比例较低 (< 30%)")

    return warnings

def format_speaker_name(name: str) -> str:
    if name.startswith("发音人"):
        return name
    return f"发音人 {name}"

def parse_wav_header_from_bytes(data: bytes) -> Tuple[int, int, int]:
    """Parse sample rate, bit depth, and channels from a WAV header (first 512+ bytes)."""
    idx = data.find(b'fmt ')
    if idx == -1 or len(data) < idx + 24:
        return None, None, None
    try:
        channels = int.from_bytes(data[idx+10:idx+12], byteorder='little')
        sample_rate = int.from_bytes(data[idx+12:idx+16], byteorder='little')
        bits_per_sample = int.from_bytes(data[idx+22:idx+24], byteorder='little')
        return sample_rate, bits_per_sample, channels
    except Exception:
        return None, None, None

def extract_project_wav_params(zip_ref: zipfile.ZipFile) -> Dict[str, Tuple[int, int, int]]:
    """Scan the zip for audio files and extract their parameters."""
    wav_params = {}
    for name in zip_ref.namelist():
        norm_name = name.replace("\\", "/")
        if norm_name.lower().endswith(".wav") and (norm_name.startswith("audio/") or "/audio/" in norm_name or "audio/" in norm_name):
            try:
                with zip_ref.open(name) as f:
                    data = f.read(1024)
                sr, bits, ch = parse_wav_header_from_bytes(data)
                if sr is not None:
                    wav_params[norm_name] = (sr, bits, ch)
            except Exception:
                pass
    return wav_params
def get_consensus_and_deviations(wav_params: Dict[str, Tuple[int, int, int]]) -> Tuple[Tuple[int, int, int], List[Tuple[str, int, int, int]]]:
    """Returns (consensus_tuple, list_of_deviations)"""
    if not wav_params:
        return (None, None, None), []
    counter = Counter(wav_params.values())
    consensus = counter.most_common(1)[0][0]

    deviations = []
    for name, params in sorted(wav_params.items()):
        if params != consensus:
            deviations.append((name, params[0], params[1], params[2]))
    return consensus, deviations

def format_wav_params(sr: int, bits: int, channels: int) -> str:
    if sr is None or bits is None or channels is None:
        return "未记录"
    sr_str = f"{sr / 1000:.1f}".rstrip('0').rstrip('.') + " kHz"
    bits_str = f"{bits}-bit"
    ch_str = "单声道" if channels == 1 else ("双声道" if channels == 2 else f"{channels}声道")
    return f"{sr_str} 采样率，{bits_str}，{ch_str}"

def generate_natural_language_summary(state: Dict[str, Any]) -> str:
    speakers = state.get("speakers", {})
    total_spk = len(speakers)

    total_items = 0
    excluded_count = 0
    manual_adjusted_count = 0

    spk_descriptions = []

    for spk_id, spk in speakers.items():
        name = spk.get("name", "发音人")
        items = spk.get("items", {})
        total_items += len(items)
        excluded_count += sum(1 for it in items.values() if it.get("is_excluded", False))

        # Analyze parameters
        params = spk.get("last_params", {})
        mode = params.get("analysis_mode", "f0")

        # Check speaker actual analysis modes
        spk_has_f0 = False
        spk_has_formant = False
        for item in items.values():
            item_mode = item.get("analysis_mode") or mode
            if item_mode == "f0":
                spk_has_f0 = True
            elif item_mode == "formant":
                spk_has_formant = True

        # If no items, fallback to speaker default mode
        if not items:
            if mode == "f0":
                spk_has_f0 = True
            elif mode == "formant":
                spk_has_formant = True
            else:
                spk_has_f0 = True
                spk_has_formant = True

        p_floor = get_pitch_floor(params)
        p_ceiling = get_pitch_ceiling(params)
        v_thresh = get_voicing_threshold(params)
        pts = params.get("pts", 11)

        # Check adjustments
        for item_id, item in items.items():
            if item.get("is_manual_edited"):
                manual_adjusted_count += 1

        disp_name = format_speaker_name(name)

        desc_parts = []
        if spk_has_f0:
            desc_parts.append(f"声调分析采用 {p_floor:.0f}–{p_ceiling:.0f} Hz 的基频搜索范围，清浊判断阈值为 {v_thresh:.2f}")
        if spk_has_formant:
            desc_parts.append(f"共振峰分析采用 Burg 分析上限 {params.get('formant_max_hz', 5500.0):.0f} Hz，共振峰追踪个数 {params.get('formant_count', 5)}，分析窗长 {params.get('formant_window_length', 0.025):.3f} s，采样策略为 {params.get('formant_sample_strategy', '整段11点')}")

        mode_desc = "与".join(desc_parts)
        spk_desc = f"{disp_name} 的{mode_desc}，时序分析等分点数为 {pts} 点"
        spk_descriptions.append(spk_desc)

    spk_combined = "；".join(spk_descriptions) + "。" if spk_descriptions else ""

    if excluded_count > 0:
        ex_str = f"本工程共保存了 {total_items} 条记录，其中 {excluded_count} 条被标记为不参与分析，最终纳入 {total_items - excluded_count} 条。{spk_combined}"
    else:
        ex_str = f"本工程共包含 {total_spk} 名发音人的 {total_items} 个分析条目。{spk_combined}"

    summary = (
        f"{ex_str}"
        f"工程保留了自动检测后的原始边界、最终采用边界及多音节词内部切分点。"
        f"其中 {manual_adjusted_count} 个条目经过人工复核调整。"
    )
    return summary
