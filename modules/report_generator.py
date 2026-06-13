import os
import io
import json
import time
import zipfile
import hashlib
import numpy as np
from typing import Dict, Any, Callable, List, Optional, Tuple
from collections import Counter

# Field Interpretation Layer
def get_pitch_floor(params: Dict[str, Any], default: float = 75.0) -> float:
    if not params:
        return default
    return params.get("pitch_floor") or params.get("f0_min") or default

def get_pitch_ceiling(params: Dict[str, Any], default: float = 600.0) -> float:
    if not params:
        return default
    return params.get("pitch_ceiling") or params.get("f0_max") or default

def get_voicing_threshold(params: Dict[str, Any], default: float = 0.25) -> float:
    if not params:
        return default
    return params.get("voicing_threshold") or params.get("voicing_thresh") or default

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
            [item.get("formant_max_hz", fallbacks["formant_max_hz"]) for item in included_items],
            fallbacks["formant_max_hz"]
        ),
        "formant_count": _majority_value(
            [item.get("formant_count", fallbacks["formant_count"]) for item in included_items],
            fallbacks["formant_count"]
        ),
        "formant_window_length": _majority_value(
            [item.get("formant_window_length", fallbacks["formant_window_length"]) for item in included_items],
            fallbacks["formant_window_length"]
        ),
        "formant_pre_emphasis": _majority_value(
            [item.get("formant_pre_emphasis", fallbacks["formant_pre_emphasis"]) for item in included_items],
            fallbacks["formant_pre_emphasis"]
        ),
        "formant_sample_strategy": _majority_value(
            [item.get("formant_sample_strategy", fallbacks["formant_sample_strategy"]) for item in included_items],
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

def split_into_syllables(label: str) -> List[str]:
    if not label:
        return []
    # Strip (缺失) or other suffixes
    clean_lbl = label.replace(" (缺失)", "").replace(" (未匹配)", "").strip()
    if "/" in clean_lbl:
        return [s.strip() for s in clean_lbl.split("/") if s.strip()]

    # Check if CJK
    has_cjk = bool(re_cjk.search(clean_lbl)) if 're_cjk' in globals() else bool(any(ord(c) > 127 for c in clean_lbl))
    if has_cjk:
        import re
        return re.findall(r'[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff]', clean_lbl)
    return [clean_lbl]

import re
re_cjk = re.compile(r'[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff]')

def load_item_cache_if_any(item: Dict[str, Any], zip_ref: zipfile.ZipFile) -> Tuple[Any, Any, str, str]:
    pitch_data = None
    formant_data = None
    pitch_status = "无"
    formant_status = "无"

    p_file = item.get("pitch_data_file")
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

def detect_pitch_anomaly_points(xs, freqs, bounds, start, end):
    anomalies = []
    if len(xs) < 2:
        return anomalies
    for i in range(1, len(xs)):
        if freqs[i] > 0 and freqs[i-1] > 0:
            ratio = freqs[i] / freqs[i-1]
            if ratio > 1.4 or ratio < 0.7:  # Pitch jump
                anomalies.append((xs[i], freqs[i]))
    return anomalies

def analyze_item_anomalies(item: Dict[str, Any], pitch_data: Any, formant_data: Any, params: Dict[str, Any], pitch_status: str = "无", formant_status: str = "无") -> List[str]:
    if item and item.get('ignore_warnings'):
        return []
    warnings = []

    if pitch_status == "损坏":
        warnings.append("[致命] 基频缓存文件已损坏或无法解析")
    if formant_status == "损坏":
        warnings.append("[致命] 共振峰缓存文件已损坏或无法解析")

    if not item or item.get('start') is None:
        warnings.append("[致命] 时间边界无效或缺失")
        return warnings

    if item.get('preview_segment_mismatch'):
        warnings.append("[致命] 子段数量与预览不匹配")

    mode = item.get('analysis_mode') or params.get('analysis_mode', 'f0')
    pts = int(item.get('pts') or params.get('pts', 11))

    if mode == 'formant':
        if not formant_data:
            return warnings
        f_xs = formant_data.get('xs', np.array([]))
        f1 = formant_data.get('f1', np.array([]))
        f2 = formant_data.get('f2', np.array([]))
        if len(f_xs) == 0:
            warnings.append("[致命] 共振峰数据为空")
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
                warnings.append(f"[致命] 音节 [{char}] 时间边界无效")
                continue
            margin = dur * 0.125
            core_s = c_s + margin
            core_e = c_e - margin

            mask = (f_xs >= core_s) & (f_xs <= core_e)
            seg_xs = f_xs[mask]
            seg_f1 = f1[mask]
            seg_f2 = f2[mask]
            if len(seg_xs) == 0:
                warnings.append(f"[致命] 音节 [{char}] 核心区间无共振峰数据")
                continue
            valid_mask = ~np.isnan(seg_f1) & ~np.isnan(seg_f2) & (seg_f2 > seg_f1)
            ratio = np.sum(valid_mask) / len(seg_xs)
            if ratio < 0.30:
                warnings.append(f"[致命] 音节 [{char}] 共振峰有效帧比例过低 ({ratio:.1%} < 30%)")
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
                warnings.append("[致命] 边界过短 (某个子段短于 80ms)")
            elif sw == 'imbalanced_duration':
                warnings.append("[警告] 时长严重失衡 (子段时长比例不均)")
            elif sw == 'no_clear_valley':
                warnings.append("[警告] 未能识别到能量谷 (子音节切分谷底不明显)")
            elif sw == 'fallback_equal_split':
                warnings.append("[提示] 采用等分兜底切割")
        return list(dict.fromkeys(warnings))

    if check_item_has_empty_f0(item, pitch_data, pts):
        warnings.append("[致命] 基频数据含有0值 (F0 缺失)")

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
            warnings.append("[致命] 边界过短 (某个子段短于 80ms)")
        elif sw == 'imbalanced_duration':
            warnings.append("[警告] 时长严重失衡 (子段时长比例不均)")
        elif sw == 'no_clear_valley':
            warnings.append("[警告] 未能识别到能量谷 (子音节切分谷底不明显)")
        elif sw == 'fallback_equal_split':
            warnings.append("[提示] 采用等分兜底切割")
        elif sw == 'low_f0_coverage':
            warnings.append("[致命] F0 覆盖率低 (某子段有效基频点比例低于 30%)")

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

def generate_markdown_report(teproj_path: str, state: Dict[str, Any], zip_ref: zipfile.ZipFile) -> str:
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
    elif export_rule == "by_group":
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

            pitch_data, formant_data, p_status, f_status = load_item_cache_if_any(item, zip_ref)
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

    adjusted_ratio = (manually_adjusted / total_items * 100) if total_items > 0 else 0.0

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

            item_f_max = item.get("formant_max_hz", f_max)
            item_f_count = item.get("formant_count", f_count)
            item_f_win = item.get("formant_window_length", f_win)
            item_f_pre = item.get("formant_pre_emphasis", f_pre)
            item_f_strat = item.get("formant_sample_strategy", f_strat)

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
        lines.append("| 发音人 | 条目ID | 音节标签 | 局部定制参数偏离详情 |")
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
    lines.append(f"- **人工微调统计**：共有 **{manually_adjusted}** 个条目经过了人工复核微调或橡皮擦擦除修正，占全部条目的 **{adjusted_ratio:.1f}%**。这表明大部分数据基于自动检测，其余部分根据声谱图人工微调。")
    lines.append("- **潜在异常条目列表 (需特别注意)**：")
    lines.append("  以下条目在分析时因信号微弱、跳变或能量谷底不明显，触发了算法警告。建议在撰写论文前复核以下条目：")
    lines.append("")

    if warning_list:
        lines.append("| 发音人 | 分组 | 条目标签 | 触发的审计/切分警告详情 |")
        lines.append("| :--- | :--- | :--- | :--- |")
        for warn in warning_list:
            warning_joined = "; ".join(warn["warnings"])
            lines.append(f"| {warn['speaker']} | {warn['group']} | {warn['label']} | {warning_joined} |")
    else:
        lines.append("（好消息：工程中所有分析条目均未触发明显的算法警告或清浊点缺失）")
    lines.append("")

    lines.append("## 7. 学术透明性与未记录信息公开说明")
    lines.append("")
    lines.append("> [!WARNING]")
    lines.append("> **本工程文件中无法确认的论文方法信息**：")
    lines.append("> 为了确保科学研究的严谨性，以下研究所需的**非物理声学参数**由于不保存在工程结构中，**无法**自动包含。研究者必须在论文的方法或附录中进行人工补充说明：")
    lines.append("> 1. **发音人人口学背景**：发音人的性别比例、年龄区间、出生及常住地、双语/双言环境、方言流利度评估。")
    lines.append("> 2. **录音设备与声学环境**：录音室本底噪声水平（dB）、采样麦克风型号与方向性特征（录音数字化参数如采样率、位深和声道数已从物理文件头部自动提取，见第1节）。")
    lines.append("> 3. **实验程序与刺激控制**：字表呈现的随机化设计、发音指导语、录音重复次数、发音疲兴控制策略等等。")
    lines.append("> 4. **TextGrid 的外部导入溯源**：若边界来自外部 MFA (Montreal Forced Aligner) 或 Praat 标注的 TextGrid 导入，请在此处补充说明原始标注的标准和对齐信度。")
    lines.append("")

    lines.append("## 8. 数据清洗与排除条目清单")
    lines.append("")
    if excluded_list:
        lines.append("以下条目已被研究者标记为“忽略（不参与分析与导出）”，但在工程归档中完整保留了其原始录音、时间边界与状态，以便溯源和复核：")
        lines.append("")
        lines.append("| 发音人 | 分组 | 条目 ID | 标签 | 时间边界(s) | 排除原因 | 排除操作时间 |")
        lines.append("| :--- | :--- | :--- | :--- | :--- | :--- | :--- |")
        for ex in excluded_list:
            b_str = f"{ex['start']:.3f}–{ex['end']:.3f}" if ex['start'] is not None and ex['end'] is not None else "-"
            lines.append(f"| {ex['speaker']} | {ex['group']} | `{ex['id']}` | {ex['label']} | {b_str} | {ex['reason']} | {ex['excluded_at']} |")
    else:
        lines.append("工程中无任何被忽略或排除的分析条目。")
    lines.append("")

    return "\n".join(lines)

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
                item_f_max = item.get("formant_max_hz", majority_f_max)
                item_f_count = item.get("formant_count", majority_f_count)
                item_f_win = item.get("formant_window_length", majority_f_win)
                item_f_pre = item.get("formant_pre_emphasis", majority_f_pre)
                item_f_strat = item.get("formant_sample_strategy", majority_f_strat)

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

                item_meta = item.get("item_meta") or {}
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
                                         item.get("item_note", ""),
                                         "；".join(str(v) for v in (item.get("item_tags", []) or [])),
                                         "；".join(str(v) for v in (item.get("item_aliases", []) or [])),
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

                pitch_data, formant_data, p_status, f_status = load_item_cache_if_any(item, z)
                p_file = item.get("pitch_data_file")
                if p_file: cache_statuses[p_file.replace("\\", "/")] = p_status
                f_file = item.get("formant_data_file")
                if f_file: cache_statuses[f_file.replace("\\", "/")] = f_status

                item_warnings = analyze_item_anomalies(item, pitch_data, formant_data, params, p_status, f_status)
                if not is_excluded and item.get("is_manual_edited"): manually_adjusted += 1
                audit_warning_rows.append([spk_name, group, item_id, label, "是" if is_excluded else "否", "是" if item.get("is_manual_edited") else "否",
                                           item.get("split_confidence", 1.0), ", ".join(item.get("split_warnings", [])),
                                           ", ".join(item_warnings) if item_warnings else "合格无警告"])

                if include_cache_details:
                    if pitch_data:
                        for x_val, f_val in zip(pitch_data['xs'], pitch_data['freqs']):
                            f0_cache_rows.append([spk_name, group, item_id, label, x_val, f_val, "是" if item_mode == "f0" else "否"])
                    if formant_data:
                        for x_val, f1, f2 in zip(formant_data['xs'], formant_data['f1'], formant_data['f2']):
                            formant_cache_rows.append([spk_name, group, item_id, label, x_val, f1 if not np.isnan(f1) else "",
                                                       f2 if not np.isnan(f2) else "", "", "是" if item_mode == "formant" else "否"])

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
        "人工复核与风险": "集中列出人工修改、切分置信度、算法警告和致命异常。",
        "排除与忽略条目": "保留已排除条目的原因和时间，确保清洗过程可追溯。",
        "资源清单": "归档内资源列表及校验状态。红色表示缓存损坏或录音参数偏离。",
        "字段说明": "工作表字段的用途和解释。",
        "F0缓存明细": "可选的 F0 缓存平铺数据。0 值或负值会高亮。",
        "共振峰缓存明细": "可选的共振峰缓存平铺数据。缺失值会高亮。",
        "自定义脚本记录": "本工程实际使用过的自定义脚本历史记录快照。",
        "自定义脚本源码": "已归档自定义脚本的完整 Python 源代码，便于复现与审计。",
    }

    width_overrides = {
        "路径": 42, "差异详情": 62, "警告": 32, "致命异常": 68, "排除原因": 28,
        "排除时间": 20, "数字化参数": 28, "校验状态": 24, "定义": 54, "概览指标": 28,
        "指标取值": 68, "参数状态": 20, "边界状态": 36, "切分状态": 18, "类型": 20,
        "条目ID": 20, "ID": 20, "标签": 18, "发音人": 16, "发音人姓名": 16, "组别": 18,
        "运行时间": 20, "用户需求": 42, "完整源码": 80, "代码 SHA-256": 42, "输出文件": 28,
    }

    trim_silence_str = "是" if state.get("trim_silence") else "否"
    export_rule_val = state.get("export_numbering_rule")
    export_rule_excel = "全部连续" if export_rule_val == "continuous" else ("按分组重新标号" if export_rule_val == "by_group" else "未记录")

    excluded_count = len(excluded_rows)
    included_count = total_items - excluded_count
    warning_item_count = sum(1 for row in audit_warning_rows if row[8] != "合格无警告")
    fatal_item_count = sum(1 for row in audit_warning_rows if "[致命]" in row[8])
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
                          ["局部参数偏离条目数", len(param_exception_rows)], ["自动审计警告条目数", warning_item_count],
                          ["含致命异常条目数", fatal_item_count], ["资源校验异常数", resource_issue_count],
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
    empty_fmt = wb.add_format({'font_name': font_name, 'italic': True, 'font_color': '#64748B', 'bg_color': '#F8FAFC', 'align': 'left'})
    red_fmt = wb.add_format({'font_name': font_name, 'bg_color': '#FEE2E2', 'font_color': '#991B1B'})
    orange_fmt = wb.add_format({'font_name': font_name, 'bg_color': '#FFEDD5', 'font_color': '#9A3412'})
    yellow_fmt = wb.add_format({'font_name': font_name, 'bg_color': '#FEF3C7', 'font_color': '#92400E'})
    green_fmt = wb.add_format({'font_name': font_name, 'bg_color': '#DCFCE7', 'font_color': '#166534'})
    section_fmt = wb.add_format({'bold': True, 'font_name': font_name, 'font_size': 11, 'font_color': 'white', 'bg_color': '#2563EB', 'align': 'left', 'valign': 'vcenter'})
    summary_title_fmt = wb.add_format({'bold': True, 'font_name': font_name, 'font_size': 18, 'font_color': 'white', 'bg_color': '#1E3A8A', 'align': 'left', 'valign': 'vcenter'})
    summary_subtitle_fmt = wb.add_format({'font_name': font_name, 'font_size': 10, 'font_color': '#334155', 'bg_color': '#DBEAFE', 'align': 'left', 'valign': 'vcenter'})
    summary_text_fmt = wb.add_format({'font_name': font_name, 'font_size': 10, 'font_color': '#1E293B', 'bg_color': '#EFF6FF', 'border': 1, 'border_color': '#BFDBFE', 'text_wrap': True, 'valign': 'top'})
    label_fmt = wb.add_format({'bold': True, 'font_name': font_name, 'font_color': '#334155', 'bg_color': '#F1F5F9', 'border': 1, 'border_color': '#CBD5E1'})
    value_fmt = wb.add_format({'font_name': font_name, 'font_color': '#1E293B', 'bg_color': 'white', 'border': 1, 'border_color': '#CBD5E1'})
    link_fmt = wb.add_format({'font_name': font_name, 'font_color': '#2563EB', 'underline': True, 'bg_color': 'white', 'border': 1, 'border_color': '#CBD5E1'})
    card_label_fmt = wb.add_format({'bold': True, 'font_name': font_name, 'font_size': 9, 'font_color': '#475569', 'bg_color': '#F8FAFC', 'border': 1, 'border_color': '#CBD5E1', 'align': 'center', 'valign': 'vcenter'})
    card_value_fmt = wb.add_format({'bold': True, 'font_name': font_name, 'font_size': 16, 'font_color': '#1E3A8A', 'bg_color': 'white', 'border': 1, 'border_color': '#CBD5E1', 'align': 'center', 'valign': 'vcenter'})
    time_fmt = wb.add_format({'num_format': '0.000'})
    decimal_fmt = wb.add_format({'num_format': '0.00'})
    integer_fmt = wb.add_format({'num_format': '0'})
    size_fmt = wb.add_format({'num_format': '#,##0'})
    header_fmt = wb.add_format({'bold': True, 'font_name': font_name, 'font_color': 'white', 'bg_color': '#2563EB', 'border': 1, 'border_color': '#DBEAFE', 'align': 'center', 'valign': 'vcenter'})

    # sheet_descriptions and width_overrides are defined above with script sheets support

    def column_format_for_header(header: str):
        if any(token in header for token in ("起点", "终点", "时长", "切分点", "时间点", "窗长")):
            return time_fmt
        if any(token in header for token in ("阈值", "置信度")):
            return decimal_fmt
        if header in ("F0下限", "F0上限", "共振峰分析上限", "追踪个数", "大小", "基频F0(Hz)", "F1共振峰(Hz)", "F2共振峰(Hz)", "F3共振峰(Hz)"):
            return size_fmt if header == "大小" else integer_fmt
        return None

    def column_width(header: str, rows: List[List[Any]], col_idx: int) -> int:
        if header in width_overrides:
            return width_overrides[header]
        longest = len(str(header))
        for row in rows[:300]:
            if col_idx < len(row):
                longest = max(longest, len(str(row[col_idx])))
        return min(max(longest + 3, 11), 24)

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
            add_formula_highlight(ws, last_row, 0, last_col, '=AND(ISNUMBER($B4),OR($A4="已忽略/排除条目数",$A4="人工微调条目数",$A4="局部参数偏离条目数",$A4="自动审计警告条目数",$A4="含致命异常条目数",$A4="资源校验异常数",$A4="录音数字化参数偏离文件数"),$B4>0)', orange_fmt)
        elif category == "条目明细":
            for header in ("定制下限", "定制上限", "定制阈值"):
                col_idx = index[header]
                col = xlsxwriter.utility.xl_col_to_name(col_idx)
                add_formula_highlight(ws, last_row, col_idx, col_idx, f'=${col}4<>"同多数"', orange_fmt)
        elif category == "词级边界":
            for header, baseline in (("采样起点", "自起点"), ("采样终点", "自终点")):
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
            add_formula_highlight(ws, last_row, 0, last_col, '=$A4<>""', orange_fmt)
        elif category == "人工复核与风险":
            manual_col = xlsxwriter.utility.xl_col_to_name(index["人工修改"])
            confidence_col = xlsxwriter.utility.xl_col_to_name(index["置信度"])
            warning_col = xlsxwriter.utility.xl_col_to_name(index["警告"])
            fatal_col = xlsxwriter.utility.xl_col_to_name(index["致命异常"])
            add_formula_highlight(ws, last_row, index["人工修改"], index["人工修改"], f'=${manual_col}4="是"', yellow_fmt)
            add_formula_highlight(ws, last_row, index["置信度"], index["置信度"], f'=${confidence_col}4<0.6', orange_fmt)
            add_formula_highlight(ws, last_row, index["警告"], index["警告"], f'=${warning_col}4<>""', yellow_fmt)
            add_formula_highlight(ws, last_row, index["致命异常"], index["致命异常"], f'=ISNUMBER(SEARCH("[致命]",${fatal_col}4))', red_fmt, True)
            add_formula_highlight(ws, last_row, index["致命异常"], index["致命异常"], f'=${fatal_col}4<>"合格无警告"', orange_fmt)
        elif category == "排除与忽略条目":
            add_formula_highlight(ws, last_row, 0, last_col, '=$A4<>""', red_fmt)
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

    # Helper to write sheets
    def write_sheet(name, headers, rows, category=None, empty_message="当前无记录"):
        category = category or name
        ws = wb.add_worksheet(name)
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
        ws.autofit()
        for col_idx, header in enumerate(headers):
            ws.set_column(col_idx, col_idx, column_width(header, rows, col_idx), column_format_for_header(header))
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
    ws_summary.merge_range("A10:J12", generate_natural_language_summary(state), summary_text_fmt)
    ws_summary.merge_range("A14:D14", "质量审计概览", section_fmt)
    ws_summary.write_row("A15", ["关注指标", "数量", "建议", "跳转"], header_fmt)
    risk_rows = [
        ["排除条目", excluded_count, "核对排除原因", "排除与忽略条目"],
        ["局部参数偏离", len(param_exception_rows), "确认条目级覆盖值", "局部参数差异"],
        ["自动审计警告", warning_item_count, "优先复核算法提示", "人工复核与风险"],
        ["致命异常", fatal_item_count, "导出分析前必须处理", "人工复核与风险"],
        ["资源校验异常", resource_issue_count, "检查缓存或录音参数", "资源清单"],
    ]
    for row_idx, row in enumerate(risk_rows, start=15):
        ws_summary.write(row_idx, 0, row[0], value_fmt)
        ws_summary.write(row_idx, 1, row[1], green_fmt if row[1] == 0 else (red_fmt if row[0] == "致命异常" else orange_fmt))
        ws_summary.write(row_idx, 2, row[2], value_fmt)
        ws_summary.write_url(row_idx, 3, f"internal:'{row[3]}'!A1", link_fmt, row[3])
    ws_summary.merge_range("F14:J14", "颜色图例与使用顺序", section_fmt)
    legend_rows = [
        ["红色", "排除、致命异常、缓存损坏或关键缺失值", red_fmt],
        ["橙色", "局部参数偏离、低置信度或需复核警告", orange_fmt],
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
    ws_summary.set_row(9, 22)
    ws_summary.set_row(10, 22)
    ws_summary.set_row(11, 22)
    ws_summary.freeze_panes(4, 0)
    ws_summary.set_landscape()
    ws_summary.fit_to_pages(1, 2)
    write_sheet("工程概览", ["概览指标", "指标取值"], overview_rows)
    write_sheet("发音人参数", ["发音人姓名", "分析模式", "时序点数", "F0下限", "F0上限", "清浊阈值", "声能跌落", "排除声母", "共振峰分析上限", "追踪个数", "窗长", "预加重", "策略"], speaker_params_rows)
    write_sheet("条目明细", ["发音人", "组别", "条目ID", "标签", "路径", "模式", "是否排除", "定制下限", "定制上限", "定制阈值", "参数状态", "字表版本", "字表名称", "组备注", "组tag", "词项备注", "词项tag", "别名", "元数据来源", "自定义字段"], item_detail_rows)
    write_sheet("词级边界", ["发音人", "组别", "ID", "标签", "是否排除", "宏起点", "宏终点", "自起点", "自终点", "采样起点", "采样终点", "时长", "边界状态"], word_boundary_rows)
    write_sheet("字级边界", ["发音人", "组别", "ID", "标签", "是否排除", "序号", "音节", "字起点", "字终点", "切分点", "切分状态"], char_boundary_rows)
    write_sheet("局部参数差异", ["发音人", "组别", "ID", "标签", "差异详情"], param_exception_rows)
    write_sheet("人工复核与风险", ["发音人", "组别", "ID", "标签", "是否排除", "人工修改", "置信度", "警告", "致命异常"], audit_warning_rows)
    write_sheet("排除与忽略条目", ["发音人", "组别", "ID", "标签", "时间边界", "排除原因", "排除时间"], excluded_rows)
    write_sheet("资源清单", ["类型", "路径", "大小", "数字化参数", "校验状态"], resource_rows)
    write_sheet("自定义脚本记录", ["脚本名", "类型", "运行时间", "用户需求", "代码 SHA-256", "运行状态", "输出文件", "软件版本", "API 版本"], script_records, empty_message="本工程未归档自定义脚本")
    write_sheet("自定义脚本源码", ["脚本名", "代码 SHA-256", "完整源码"], script_sources, empty_message="本工程未归档自定义脚本")
    write_sheet("字段说明", ["工作表", "字段", "定义"], [
        ["工程概览", "SHA-256", "归档文件唯一性校验码，用于确认报告对应的工程版本。"],
        ["条目明细", "参数状态", "说明条目是否沿用发音人多数参数，或存在局部参数偏离。"],
        ["条目明细", "字表元数据字段", "高级字表 v2 导入时保存的组备注、词项备注、tag、别名和自定义字段。"],
        ["词级边界", "宏起点/宏终点", "待分析音频宏范围。"],
        ["词级边界", "自起点/自终点", "自动检测后的原始边界。"],
        ["词级边界", "采样起点/采样终点", "最终进入声学采样的采用边界。"],
        ["词级边界", "边界状态", "明确记录人工调整、最终边界变化和词内切分点。"],
        ["字级边界", "切分状态", "明确记录词内切分点或字级边界缺失。"],
        ["人工复核与风险", "致命异常", "算法审计结果；合格条目显示“合格无警告”。"],
        ["资源清单", "校验状态", "归档缓存可解析性以及录音参数是否偏离多数值。"],
        ["自定义脚本记录", "代码 SHA-256", "已归档自定义脚本的完整性校验哈希值。"],
        ["自定义脚本源码", "完整源码", "自定义脚本的完整 Python 源代码内容。"],
    ])

    if include_cache_details:
        if f0_cache_rows:
            write_sheet("F0缓存明细", ["发音人", "组别", "条目ID", "标签", "时间点(s)", "基频F0(Hz)"], f0_cache_rows)
        if formant_cache_rows:
            write_sheet("共振峰缓存明细", ["发音人", "组别", "条目ID", "标签", "时间点(s)", "F1共振峰(Hz)", "F2共振峰(Hz)", "F3共振峰(Hz)"], formant_cache_rows)

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
