"""
modules.export_service.item_sampling - 词条采样与文本格式导出模块
提供逐点基频采样、共振峰多段采样与纯文本格式化导出。
"""

import os
from typing import List, Dict, Optional, Any, Tuple
import numpy as np

try:
    import parselmouth
except ImportError:
    from modules import _parselmouth_fallback as parselmouth
    import sys as _sys
    _sys.modules.setdefault("parselmouth", parselmouth)
    _sys.modules.setdefault("parselmouth.praat", parselmouth.praat)

from ..core.text_io import split_into_syllables
try:
    from ..dsp.pitch_formant_unified import extract_f0
except ImportError:
    from ..audio_core import extract_f0

def get_export_text_for_item(item: Dict[str, Any], real_index: int, num_points: int, pitch_floor: float = 75.0, pitch_ceiling: float = 600.0, voicing_threshold: float = 0.25) -> str:
    if item.get('start') is None or item.get('end') is None: return ""
    t_s, t_e = item['start'], item['end']
    duration = t_e - t_s

    label = item.get('label', '')
    inner_splits = item.get('inner_splits', [])
    syls = split_into_syllables(label)
    is_word_mode = len(syls) > 1

    # 优先使用 item 内部存储的个性化参数实现所见即所得
    p_floor = item.get('pitch_floor', pitch_floor)
    p_ceiling = item.get('pitch_ceiling', pitch_ceiling)
    v_thresh = item.get('voicing_threshold', voicing_threshold)

    try:
        from ..dsp.pitch_formant_unified import extract_f0
    except ImportError:
        from ..audio_core import extract_f0

    if (not item.get('snd') or (not item.get('pitch') and not item.get('pitch_data'))) and item.get('path'):
        if num_points == 11 and item.get('preview_f0') and not is_word_mode:
            output = f"{real_index}.{label}\n{duration:.3f}\n"
            times = np.linspace(t_s, t_e, 11)
            for i, t in enumerate(times):
                f0 = item['preview_f0'][i]
                f0_str = "0.000000" if f0 == 0 else f"{f0:.6f}"
                output += f"{t:.6f}\t{f0_str}\n"
            return output
        else:
            try:
                item['snd'] = parselmouth.Sound(item['path'])
                item['pitch_data'] = extract_f0(item['snd'], {'pitch_floor': p_floor, 'pitch_ceiling': p_ceiling, 'voicing_threshold': v_thresh})
            except Exception: return ""

    if duration <= 0 or not item.get('snd'): return ""

    output = ""
    if is_word_mode:
        chars_bounds = item.get('chars_bounds', [])
        if not chars_bounds:
            splits = [t_s] + [s for s in inner_splits if t_s < s < t_e] + [t_e]
            if len(splits) != len(syls) + 1:
                splits = np.linspace(t_s, t_e, len(syls) + 1).tolist()
            chars_bounds = [(splits[j], splits[j+1]) for j in range(len(splits)-1)]

        parent_xs = None
        parent_freqs = None
        if item.get('pitch_data'):
            parent_xs = item['pitch_data']['xs']
            parent_freqs = item['pitch_data']['freqs']
        elif item.get('pitch'):
            parent_xs = item['pitch'].xs()
            parent_freqs = item['pitch'].selected_array['frequency']

        for i in range(len(syls)):
            char = syls[i]
            if i < len(chars_bounds):
                c_start, c_end = chars_bounds[i]
            else:
                continue

            # 独立音频提取基频：避免全局连读造成的 Viterbi octave jump
            seg_xs = np.array([])
            seg_ys = np.array([])
            valid_idx = np.array([], dtype=int)
            try:
                if c_end - c_start <= 0.01:
                    raise ValueError("segment too short")
                c_snd = item['snd'].extract_part(from_time=c_start, to_time=c_end)
                c_pitch_data = extract_f0(c_snd, {'pitch_floor': p_floor, 'pitch_ceiling': p_ceiling, 'voicing_threshold': v_thresh})
                p_xs = c_pitch_data['xs'] + c_start
                p_freqs = c_pitch_data['freqs']

                # 如果有全局已编辑的 pitch_data，将对应的抹除点同步到独立提取的结果中
                if item.get('pitch_data'):
                    parent_xs = item['pitch_data']['xs']
                    parent_freqs = item['pitch_data']['freqs']
                    indices = np.searchsorted(parent_xs, p_xs)
                    indices = np.clip(indices, 0, len(parent_xs) - 1)
                    for idx, (t, p_idx) in enumerate(zip(p_xs, indices)):
                        best_idx = p_idx
                        if p_idx > 0 and abs(parent_xs[p_idx-1] - t) < abs(parent_xs[p_idx] - t):
                            best_idx = p_idx - 1
                        if abs(parent_xs[best_idx] - t) < 0.015 and parent_freqs[best_idx] == 0.0:
                            p_freqs[idx] = 0.0
                valid_idx = np.where(p_freqs > 0)[0]
            except Exception:
                p_xs = np.array([])
                p_freqs = np.array([])

            if len(valid_idx) < 2 and parent_xs is not None and parent_freqs is not None:
                mask = (parent_xs >= c_start) & (parent_xs <= c_end)
                p_xs = parent_xs[mask]
                p_freqs = parent_freqs[mask]
                valid_idx = np.where(p_freqs > 0)[0]

            if len(valid_idx) >= 2:
                v_start, v_end = p_xs[valid_idx[0]], p_xs[valid_idx[-1]]
                seg_xs = p_xs[valid_idx]
                seg_ys = p_freqs[valid_idx]
            else:
                v_start, v_end = c_start, c_end

            c_dur = v_end - v_start
            if c_dur <= 0:
                c_dur = max(0.0, c_end - c_start)

            times = np.linspace(v_start, v_end, num_points)
            output += f"{real_index}_{i+1}.{char} ({label})\n{c_dur:.3f}\n"

            # 修复点：抛弃 Praat 全局 Interpolate，使用 numpy 仅针对当前字的真实基频点进行内部插值
            if len(seg_xs) >= 2:
                f0_sampled = np.interp(times, seg_xs, seg_ys)
                for t, f0 in zip(times, f0_sampled):
                    # 修正：如果插值点距离真实基频点过远（跨越了静音区，如>25ms），强制归零，避免产生假数据桥接
                    if np.min(np.abs(seg_xs - t)) > 0.025:
                        f0 = 0.0
                    f0_str = f"{f0:.6f}" if f0 > 0 else "0.000000"
                    output += f"{t:.6f}\t{f0_str}\n"
            else:
                for t in times:
                    output += f"{t:.6f}\t0.000000\n"
    else:
        # 单字模式同样应用此逻辑
        if item.get('pitch_data'):
            p_xs = item['pitch_data']['xs']
            p_freqs = item['pitch_data']['freqs']
        else:
            pitch = item['pitch']
            p_xs = pitch.xs()
            p_freqs = pitch.selected_array['frequency']

        valid_idx = np.where((p_xs >= t_s) & (p_xs <= t_e) & (p_freqs > 0))[0]
        if len(valid_idx) >= 2:
            v_start, v_end = p_xs[valid_idx[0]], p_xs[valid_idx[-1]]
            seg_xs = p_xs[valid_idx]
            seg_ys = p_freqs[valid_idx]
            duration = v_end - v_start

            output += f"{real_index}.{label}\n{duration:.3f}\n"
            times = np.linspace(v_start, v_end, num_points)
            if len(seg_xs) >= 2:
                f0_sampled = np.interp(times, seg_xs, seg_ys)
                for t, f0 in zip(times, f0_sampled):
                    if np.min(np.abs(seg_xs - t)) > 0.025:
                        f0 = 0.0
                    f0_str = f"{f0:.6f}" if f0 > 0 else "0.000000"
                    output += f"{t:.6f}\t{f0_str}\n"
            else:
                for t in times:
                    output += f"{t:.6f}\t0.000000\n"
        else:
            output += f"{real_index}.{label}\n0.000\n"
            times = np.linspace(t_s, t_e, num_points)
            for t in times:
                output += f"{t:.6f}\t0.000000\n"

    return output


def get_item_syllable_bounds(item: Dict[str, Any]) -> List[List[float]]:
    """
    Get the syllable boundaries for an item. Fallback to linear segmentation if chars_bounds is invalid.
    """
    t_s, t_e = item['start'], item['end']
    label = item.get('label', '')
    syls = split_into_syllables(label)
    chars_bounds = item.get('chars_bounds', [])
    if chars_bounds and len(chars_bounds) == len(syls):
        return chars_bounds
    else:
        inner_splits = item.get('inner_splits', [])
        splits = [t_s] + [s for s in inner_splits if t_s < s < t_e] + [t_e]
        if len(syls) > 1 and len(splits) != len(syls) + 1:
            splits = np.linspace(t_s, t_e, len(syls) + 1).tolist()
        elif len(syls) <= 1:
            splits = [t_s, t_e]
        return [[splits[i], splits[i+1]] for i in range(len(splits)-1)]


def sample_formant_points_by_bounds_extended(
    item: Dict[str, Any], bounds: List[List[float]], pts: int = 11, strategy: str = '整段11点', include_f3: bool = False
) -> Dict[str, List[float]]:
    """
    扩展的共振峰采样工具。按 bounds (音节/字边界列表) 采样共振峰曲线。
    返回: {
        "times": [...],
        "f1": [...],
        "f2": [...],
        "f3": [...]
    }
    """
    f_data = item.get('formant_data')
    total_pts = len(bounds) * pts
    if not f_data or 'xs' not in f_data or 'f1' not in f_data or 'f2' not in f_data:
        dummy_times = [0.0] * total_pts
        dummy_nans = [np.nan] * total_pts
        return {
            "times": dummy_times,
            "f1": dummy_nans,
            "f2": dummy_nans,
            "f3": dummy_nans
        }

    xs = np.asarray(f_data['xs'], dtype=float)
    f1_arr = np.asarray(f_data['f1'], dtype=float)
    f2_arr = np.asarray(f_data['f2'], dtype=float)
    f3_raw = f_data.get('f3') if include_f3 else None
    f3_arr = np.asarray(f3_raw, dtype=float) if f3_raw is not None else None

    all_times = []
    all_f1 = []
    all_f2 = []
    all_f3 = []

    # 强制在提取时使用成对过滤的掩码以保证每一帧数据都成对有效
    valid_mask = ~np.isnan(f1_arr) & ~np.isnan(f2_arr) & (f2_arr > f1_arr)
    valid_indices = np.where(valid_mask)[0]

    if f3_arr is not None:
        f3_valid_mask = ~np.isnan(f3_arr)
        f3_valid_indices = np.where(f3_valid_mask)[0]
    else:
        f3_valid_indices = np.array([])

    for c_s, c_e in bounds:
        times = np.linspace(c_s, c_e, pts)
        all_times.extend(times.tolist())

        if strategy == '中段均值':
            duration = c_e - c_s
            m_start = c_s + duration / 3.0
            m_end = c_s + 2.0 * duration / 3.0

            mask = (xs >= m_start) & (xs <= m_end) & valid_mask
            f1_slice = f1_arr[mask]
            f2_slice = f2_arr[mask]

            mean_f1 = np.nanmean(f1_slice) if len(f1_slice) > 0 else np.nan
            mean_f2 = np.nanmean(f2_slice) if len(f2_slice) > 0 else np.nan

            all_f1.extend([mean_f1] * pts)
            all_f2.extend([mean_f2] * pts)

            if f3_arr is not None:
                mask_f3 = (xs >= m_start) & (xs <= m_end) & f3_valid_mask
                f3_slice = f3_arr[mask_f3]
                mean_f3 = np.nanmean(f3_slice) if len(f3_slice) > 0 else np.nan
                all_f3.extend([mean_f3] * pts)
            else:
                all_f3.extend([np.nan] * pts)
        else:
            for t in times:
                # F1 & F2
                if len(valid_indices) == 0 or t < xs[0] or t > xs[-1]:
                    all_f1.append(np.nan)
                    all_f2.append(np.nan)
                else:
                    nearest_idx = np.argmin(np.abs(xs[valid_indices] - t))
                    if np.abs(xs[valid_indices][nearest_idx] - t) > 0.04:
                        all_f1.append(np.nan)
                        all_f2.append(np.nan)
                    else:
                        val_f1 = float(np.interp(t, xs[valid_indices], f1_arr[valid_indices]))
                        val_f2 = float(np.interp(t, xs[valid_indices], f2_arr[valid_indices]))
                        if val_f2 > val_f1:
                            all_f1.append(val_f1)
                            all_f2.append(val_f2)
                        else:
                            all_f1.append(np.nan)
                            all_f2.append(np.nan)

                # F3
                if f3_arr is not None and len(f3_valid_indices) > 0 and xs[0] <= t <= xs[-1]:
                    nearest_idx_f3 = np.argmin(np.abs(xs[f3_valid_indices] - t))
                    if np.abs(xs[f3_valid_indices][nearest_idx_f3] - t) > 0.04:
                        all_f3.append(np.nan)
                    else:
                        val_f3 = float(np.interp(t, xs[f3_valid_indices], f3_arr[f3_valid_indices]))
                        all_f3.append(val_f3)
                else:
                    all_f3.append(np.nan)

    return {
        "times": all_times,
        "f1": all_f1,
        "f2": all_f2,
        "f3": all_f3
    }


def sample_formant_points_by_bounds(
    item: dict, bounds: list, pts: int = 11, strategy: str = '整段11点'
) -> tuple:
    """
    统一的共振峰采样工具。按 bounds (音节/字边界列表) 采样共振峰曲线。
    返回: (times, f1_sampled, f2_sampled)
    """
    res = sample_formant_points_by_bounds_extended(item, bounds, pts, strategy, include_f3=False)
    return res["times"], res["f1"], res["f2"]


def get_formant_export_text_for_item(item: Dict[str, Any], real_index: int, num_points: int) -> str:
    if item.get('start') is None or item.get('end') is None: return ""
    bounds = get_item_syllable_bounds(item)
    label = item.get('label', '')
    syls = split_into_syllables(label)

    strategy = '整段11点'
    if 'formant_sample_strategy' in item:
        strategy = item['formant_sample_strategy']
    elif hasattr(item, 'app') and item.app:
        strategy = item.app.last_params.get('formant_sample_strategy', '整段11点')

    times, f1_vals, f2_vals = sample_formant_points_by_bounds(item, bounds, num_points, strategy)

    output = ""
    for idx_syl, (c_s, c_e) in enumerate(bounds):
        char = syls[idx_syl] if idx_syl < len(syls) else f"字{idx_syl+1}"
        duration = c_e - c_s
        if len(bounds) > 1:
            output += f"{real_index}_{idx_syl+1}.{char} ({label})\n{duration:.3f}\n"
        else:
            output += f"{real_index}.{label}\n{duration:.3f}\n"

        output += "时间(s)\tF1(Hz)\tF2(Hz)\n"
        for i in range(num_points):
            flat_idx = idx_syl * num_points + i
            t = times[flat_idx]
            f1_v = f1_vals[flat_idx]
            f2_v = f2_vals[flat_idx]

            f1_str = "--" if np.isnan(f1_v) else f"{f1_v:.1f}"
            f2_str = "--" if np.isnan(f2_v) else f"{f2_v:.1f}"
            output += f"{t:.6f}\t{f1_str}\t{f2_str}\n"

    return output
