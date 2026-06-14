import numpy as np
import os
from typing import Tuple, List, Union, Dict, Any
import parselmouth

# 静音阈值：振幅 ≈ -50dB (10^(-50/20) ≈ 0.00316)
SILENCE_AMPLITUDE_THRESHOLD = 10 ** (-50 / 20)
VOP_BUFFER_SEC = 0.02
VOP_WIN_LEN_SEC = 0.010
VOP_HOP_LEN_SEC = 0.002
VAD_TIME_STEP = 0.01
VAD_MIN_DURATION = 0.1
VAD_CLEAR_GAP_THRESHOLD = 0.12

def trim_bounds_by_amplitude(start: float, end: float, xs, values, threshold: float = SILENCE_AMPLITUDE_THRESHOLD) -> Tuple[float, float]:
    """
    根据片段内相对时间轴和振幅，返回剔除首尾静音后的绝对时间边界。
    """
    try:
        xs_arr = np.asarray(xs, dtype=float)
        vals_arr = np.asarray(values, dtype=float)
        if xs_arr.ndim != 1 or vals_arr.ndim != 1 or len(xs_arr) == 0 or len(xs_arr) != len(vals_arr):
            return start, end
        valid_idx = np.where(np.abs(vals_arr) > threshold)[0]
        if len(valid_idx) == 0:
            return start, end
        base_start = float(start)
        trimmed_start = base_start + float(xs_arr[valid_idx[0]])
        trimmed_end = base_start + float(xs_arr[valid_idx[-1]])
        if trimmed_end <= trimmed_start:
            return start, end
        return trimmed_start, trimmed_end
    except Exception:
        return start, end

def detect_vowel_onset(snd: parselmouth.Sound, rough_start: float, rough_end: float) -> float:
    """
    智能元音起始点 (VOP) 检测：
    基于 短时能量(STE)突变 + 过零率(ZCR) 惩罚
    取代机械的固定时长裁切。
    """
    # 前后加 20ms 缓冲提取波形，确保能算出一阶导数
    buffer = VOP_BUFFER_SEC
    part_s = max(0, rough_start - buffer)
    part_e = min(snd.get_total_duration(), rough_end + buffer)

    if part_e <= part_s:
        return rough_start

    part = snd.extract_part(from_time=part_s, to_time=part_e)
    vals = part.values[0]
    sr = part.sampling_frequency

    # 设定 10ms 窗口，2ms 步长（高分辨率时间轴）
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
        frame = vals[i*hop_len : i*hop_len + win_len]
        # 1. 计算过零率 (Zero-Crossing Rate) - 清辅音(s, f)特征
        # 学术优化：对音频进行中心化处理，避免直流偏置(DC offset)导致过零率计算不准
        frame_centered = frame - np.mean(frame)
        crossings = np.sum(np.abs(np.diff(frame_centered > 0)))
        zcr[i] = crossings / max(1, (win_len - 1))

        # 2. 计算短时能量 (Short-Time Energy: RMS) - 韵母/元音特征
        ste[i] = np.sqrt(np.mean(frame**2))
        times[i] = part_s + (i * hop_len + win_len / 2) / sr


    max_ste = np.max(ste)
    if max_ste < 1e-5:  # 全是绝对静音
        return rough_start

    # 局部能量归一化
    ste_norm = ste / max_ste

    # 学术优化：对短时能量进行简单的平滑，消除瞬态噪声引起的伪峰
    window = np.ones(3)/3.0
    if len(ste_norm) > 3:
        ste_norm_smooth = np.convolve(ste_norm, window, mode='same')
    else:
        ste_norm_smooth = ste_norm

    # 3. 能量一阶导数（寻找能量暴涨的瞬间，对应浊辅音 m/n 的除阻，或韵母起振）
    ste_diff = np.diff(ste_norm_smooth, prepend=ste_norm_smooth[0])

    ste_diff[ste_diff < 0] = 0  # 只关注能量上升阶段

    # 4. 综合得分：能量增量越大越好，ZCR 越小越好。
    # 典型的清擦音 ZCR > 0.25，通过 clip 施加严重惩罚，让辅音段得分为 0
    zcr_penalty = np.clip(1.0 - (zcr / 0.25), 0.0, 1.0)
    vop_scores = ste_diff * zcr_penalty

    # 5. 限定在用户指定的区间内找最高得分
    valid_mask = (times >= rough_start) & (times <= rough_end)
    if not np.any(valid_mask):
        return rough_start

    valid_times = times[valid_mask]
    valid_scores = vop_scores[valid_mask]

    best_idx = np.argmax(valid_scores)
    # 如果整个区间都没有能量明显的突变，退回保守的 rough_start
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
            key=lambda idx: fitted[idx + 1][0] - fitted[idx][1]
        )
        fitted[merge_idx][1] = fitted[merge_idx + 1][1]
        fitted.pop(merge_idx + 1)
    return fitted


def macroscopic_vad(snd: parselmouth.Sound, expected_count: int = None) -> List[List[float]]:
    """长音频宏观静音检测分割，可按字表数量择优保留停顿。"""
    intensity = snd.to_intensity(time_step=VAD_TIME_STEP)
    vals = intensity.values[0]
    xs = intensity.xs()
    sorted_vals = np.sort(vals[~np.isnan(vals)])

    if len(sorted_vals) > 20:
        max_int = np.mean(sorted_vals[-int(len(sorted_vals)*0.05):])
        noise_floor = np.mean(sorted_vals[:int(len(sorted_vals)*0.1)])
        # Adaptive threshold: 15 dB above noise floor, or max - 25, whichever is lower (more inclusive)
        # However, to be robust, we use a balanced formula
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
        # 从原始候选段开始合并，等价于优先保留最长、最清晰的静音间隔。
        merged = _fit_vad_segments_to_expected_count(usable_segs, expected_count)
    else:
        # 明显静音应当保留为段落边界；更短的停顿仍视为词内波动。
        merged = _merge_vad_segments(usable_segs, VAD_CLEAR_GAP_THRESHOLD)
    return [s for s in merged if s[1] - s[0] > VAD_MIN_DURATION]


def core_microscopic_vowel_nucleus(snd: parselmouth.Sound, global_pitch_or_arrays: Union[parselmouth.Pitch, Tuple[np.ndarray, np.ndarray], Dict[str, Any]], t_min: float, t_max: float, drop_db: float, skip_front: float, trim_silence: bool) -> Tuple[float, float, float, float]:
    """微观韵母提取核心算法"""
    try:
        if isinstance(global_pitch_or_arrays, dict):
            xs = global_pitch_or_arrays['xs']
            freqs = global_pitch_or_arrays['freqs']
        elif isinstance(global_pitch_or_arrays, tuple):
            xs, freqs = global_pitch_or_arrays
        else:
            xs = global_pitch_or_arrays.xs()
            freqs = global_pitch_or_arrays.selected_array['frequency']

        part = snd.extract_part(from_time=t_min, to_time=t_max) if t_min != 0 or t_max != snd.get_total_duration() else snd
        intensity = part.to_intensity()


        try: max_int = np.nanmax(intensity.values)
        except Exception: return t_min, t_max, t_min, t_max

        best_s, best_e = 0.0, part.get_total_duration()
        thresh = max_int - drop_db
        int_xs = intensity.xs()
        int_vals = intensity.values[0]

        # 向量化寻找有效起始点：
        # int_xs 是相对于截取片段的时间 (0 到 duration)
        # 将其转换为绝对时间进行基频查找
        abs_int_xs = t_min + int_xs
        if len(xs) >= 2:
            valid_freqs_interp = np.interp(abs_int_xs, xs, freqs, left=0, right=0)
        else:
            valid_freqs_interp = np.zeros_like(abs_int_xs)

        # 2. 有效点条件：强度 > thresh 且 对应时间点附近有基频 (freq > 0)
        valid_mask = (int_vals > thresh) & (valid_freqs_interp > 0)
        valid_times = int_xs[valid_mask]

        if len(valid_times) > 2:
            best_s = valid_times[0]
            best_e = valid_times[-1]


            # --- 算法升级：替换原有的 best_s += skip_front ---
            if skip_front > 0.0:
                rough_s_abs = t_min + best_s
                # 将 UI 的 skip_front 视为“向后搜索 VOP 的最大窗口时间”
                search_end_abs = min(t_min + best_e, rough_s_abs + skip_front)
                # 稍微往前看 20ms，避免上一层基于阈值的切分切得过晚
                search_start_abs = max(t_min, rough_s_abs - 0.02)

                # 调用智能 VOP 提取
                refined_s_abs = detect_vowel_onset(snd, search_start_abs, search_end_abs)
                best_s = min(refined_s_abs - t_min, best_e - 0.01)

        temp_s, temp_e = t_min + best_s, t_min + best_e
        final_s, final_e = temp_s, temp_e

        if trim_silence:
            trim_part = snd.extract_part(from_time=temp_s, to_time=temp_e)
            vals = trim_part.values[0]
            trim_xs = trim_part.xs()
            final_s, final_e = trim_bounds_by_amplitude(temp_s, temp_e, trim_xs, vals)

        # --- 算法优化：严格收缩到有效基频区间，消除头尾 0 值 ---
        valid_pitch_xs = [x for x, f in zip(xs, freqs) if f > 0 and final_s <= x <= final_e]
        if len(valid_pitch_xs) >= 2:
            final_s = valid_pitch_xs[0]
            final_e = valid_pitch_xs[-1]

        return final_s, final_e, temp_s, temp_e
    except Exception:
        return t_min, t_max, t_min, t_max


def recalculate_bounds_fast(snd: parselmouth.Sound, global_pitch_or_arrays: Union[parselmouth.Pitch, Tuple[np.ndarray, np.ndarray], Dict[str, Any]], temp_s: float, temp_e: float, trim_silence: bool) -> Tuple[float, float]:
    """仅重新计算静音裁切和基频收缩，不重新跑振幅分析"""
    try:
        if isinstance(global_pitch_or_arrays, dict):
            xs = global_pitch_or_arrays['xs']
            freqs = global_pitch_or_arrays['freqs']
        elif isinstance(global_pitch_or_arrays, tuple):
            xs, freqs = global_pitch_or_arrays
        else:
            xs = global_pitch_or_arrays.xs()
            freqs = global_pitch_or_arrays.selected_array['frequency']

        final_s, final_e = temp_s, temp_e

        if trim_silence:
            trim_part = snd.extract_part(from_time=temp_s, to_time=temp_e) if temp_s != 0 or temp_e != snd.get_total_duration() else snd
            vals = trim_part.values[0]
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
    snd = parselmouth.Sound(path)
    return len(macroscopic_vad(snd))


def auto_split_inner_word(snd: parselmouth.Sound, t_min: float, t_max: float, word_len: int, pitch_data: Dict[str, Any] = None, output_meta: Dict[str, Any] = None) -> List[float]:
    """
    词语模式内部子音节切分算法 (自动识别蓝线)：
    基于组合成本最优搜索能量谷底点，作为字与字之间的切分点。如果失败自动退化为等比例划分。
    """
    n_splits = word_len - 1
    if n_splits <= 0:
        if output_meta is not None:
            output_meta['split_warnings'] = []
            output_meta['split_confidence'] = 1.0
        return []

    # 兜底：等距离均分点
    fallback_splits = [t_min + (t_max - t_min) * (i / word_len) for i in range(1, word_len)]

    if t_max - t_min < 0.1: # 小于 100ms 太短，不具备检测价值
        if output_meta is not None:
            output_meta['split_warnings'] = ['tiny_segment']
            output_meta['split_confidence'] = 0.3
        return fallback_splits

    try:
        part = snd.extract_part(from_time=t_min, to_time=t_max)
        intensity = part.to_intensity(time_step=0.01)
        vals = intensity.values[0]
        xs = intensity.xs() + t_min

        # 平滑能量曲线
        window = np.ones(5) / 5.0
        if len(vals) < 5:
            if output_meta is not None:
                output_meta['split_warnings'] = ['fallback_equal_split', 'no_clear_valley']
                output_meta['split_confidence'] = 0.3
            return fallback_splits
        smoothed = np.convolve(vals, window, mode='same')

        # 寻找能量谷底
        import scipy.signal
        valleys, _ = scipy.signal.find_peaks(-smoothed, distance=8) # 限制字与字最小跨度为约 80ms

        # 过滤掉深度不足 2dB 的伪谷底
        if len(smoothed) > 0:
            max_val = np.max(smoothed)
            valleys = [v for v in valleys if max_val - smoothed[v] >= 2.0]

        candidates = [float(xs[v]) for v in valleys]

        import itertools
        best_combination = None
        best_cost = float('inf')

        # 过滤候选点，防止组合爆炸
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

            # 时长与比例惩罚
            splits_with_bounds = [t_min] + combo + [t_max]
            segment_dur_penalty = 0.0
            for k in range(len(splits_with_bounds) - 1):
                d = splits_with_bounds[k+1] - splits_with_bounds[k]
                ratio = d / (t_max - t_min)
                if d < 0.08:
                    segment_dur_penalty += 10000.0 # 极其严重的惩罚，排除此选择
                elif ratio < (0.4 / word_len):
                    segment_dur_penalty += 500.0
                elif ratio < (0.5 / word_len):
                    segment_dur_penalty += 100.0

            cost += segment_dur_penalty

            # 各切点个别成本
            for i, s in enumerate(combo):
                t_ref = t_min + (t_max - t_min) * ((i + 1) / word_len)
                # 距离成本
                dist_cost = 100.0 * ((s - t_ref) / (t_max - t_min)) ** 2
                cost += dist_cost

                # 能量成本
                idx = np.argmin(np.abs(xs - s))
                cost += smoothed[idx]

                # F0 成本：如果是 voiced（有声），惩罚
                if pitch_data is not None:
                    p_xs = pitch_data.get('xs')
                    p_freqs = pitch_data.get('freqs')
                    if p_xs is not None and p_freqs is not None and len(p_xs) > 0:
                        p_idx = np.argmin(np.abs(p_xs - s))
                        if abs(p_xs[p_idx] - s) < 0.015 and p_freqs[p_idx] > 0:
                            cost += 15.0 # voiced 惩罚

            if cost < best_cost:
                best_cost = cost
                best_combination = combo

        # 结果生成与警告收集
        warnings = []
        confidence = 1.0

        if best_combination is None or best_cost >= 5000.0:
            splits = fallback_splits
            warnings.append('fallback_equal_split')
            warnings.append('no_clear_valley')
            confidence = 0.3
        else:
            splits = best_combination
            splits_with_bounds = [t_min] + splits + [t_max]
            for k in range(len(splits_with_bounds) - 1):
                d = splits_with_bounds[k+1] - splits_with_bounds[k]
                ratio = d / (t_max - t_min)
                if d < 0.08:
                    warnings.append('tiny_segment')
                    confidence = min(confidence, 0.4)
                if word_len == 2 and ratio < 0.20:
                    warnings.append('imbalanced_duration')
                    confidence = min(confidence, 0.5)
                elif word_len > 2 and ratio < (0.4 / word_len):
                    warnings.append('imbalanced_duration')
                    confidence = min(confidence, 0.5)

            if pitch_data is not None:
                p_xs = pitch_data.get('xs')
                p_freqs = pitch_data.get('freqs')
                if p_xs is not None and p_freqs is not None and len(p_xs) > 0:
                    for k in range(len(splits_with_bounds) - 1):
                        c_s, c_e = splits_with_bounds[k], splits_with_bounds[k+1]
                        mask = (p_xs >= c_s) & (p_xs <= c_e)
                        seg_freqs = p_freqs[mask]
                        if len(seg_freqs) > 0:
                            active_ratio = np.sum(seg_freqs > 0) / len(seg_freqs)
                        else:
                            active_ratio = 0.0
                        if active_ratio < 0.30:
                            warnings.append('low_f0_coverage')
                            confidence = min(confidence, 0.5)

        if output_meta is not None:
            output_meta['split_warnings'] = list(set(warnings))
            output_meta['split_confidence'] = confidence

        return splits
    except Exception:
        if output_meta is not None:
            output_meta['split_warnings'] = ['fallback_equal_split']
            output_meta['split_confidence'] = 0.1
        return fallback_splits


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
    pitch_floor = int(params.get('pitch_floor', 75))
    pitch_ceiling = int(params.get('pitch_ceiling', 600))
    voicing_threshold = float(params.get('voicing_threshold', 0.25))

    pitch = snd.to_pitch_ac(
        time_step=None,
        pitch_floor=pitch_floor,
        pitch_ceiling=pitch_ceiling,
        voicing_threshold=voicing_threshold,
        very_accurate=bool(params.get('very_accurate', True)),
        octave_jump_cost=0.9
    )
    xs = pitch.xs()
    freqs = pitch.selected_array['frequency']
    # 确保无声段是 0.0
    freqs_clean = np.where(np.isnan(freqs) | (freqs <= 0), 0.0, freqs)

    return {
        "xs": xs.astype(np.float64),
        "freqs": freqs_clean.astype(np.float64),
        "engine": "praat"
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
    formant_count = float(params.get('formant_count', 5))
    formant_max_hz = float(params.get('formant_max_hz', 5500.0))
    formant_window_length = float(params.get('formant_window_length', 0.025))
    formant_pre_emphasis = float(params.get('formant_pre_emphasis', 50.0))

    formant = snd.to_formant_burg(
        time_step=None,
        max_number_of_formants=formant_count,
        maximum_formant=formant_max_hz,
        window_length=formant_window_length,
        pre_emphasis_from=formant_pre_emphasis
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
        "params": params
    }


def _sample_formants_helper(snd: parselmouth.Sound, mic_s: float, mic_e: float, params: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    formant_data = extract_formants(snd, params)
    pts = int(params.get('pts', 11))
    strategy = params.get('formant_sample_strategy', '整段11点')
    f_xs = formant_data['xs']
    f1_arr = formant_data['f1']
    f2_arr = formant_data['f2']
    preview_times = np.linspace(mic_s, mic_e, pts)

    if strategy == '中段均值':
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


def auto_split_to_chars_bounds(snd: parselmouth.Sound, mic_s: float, mic_e: float, inner_splits: List[float], label_len: int, params: Dict[str, float]) -> List[List[float]]:
    splits = [mic_s] + [s for s in inner_splits if mic_s < s < mic_e] + [mic_e]
    if len(splits) != label_len + 1:
        splits = np.linspace(mic_s, mic_e, label_len + 1).tolist()

    chars_bounds = []
    for i in range(len(splits) - 1):
        c_s, c_e = splits[i], splits[i+1]
        try:
            if c_e - c_s > 0.01:
                c_snd = snd.extract_part(from_time=c_s, to_time=c_e)
                c_pitch_data = extract_f0(c_snd, params)
                p_xs = c_pitch_data['xs'] + c_s
                p_freqs = c_pitch_data['freqs']
                valid_idx = np.where(p_freqs > 0)[0]
                if len(valid_idx) >= 2:
                    v_start, v_end = p_xs[valid_idx[0]], p_xs[valid_idx[-1]]
                    chars_bounds.append([v_start, v_end])
                else:
                    chars_bounds.append([c_s, c_e])
            else:
                chars_bounds.append([c_s, c_e])
        except Exception:
            chars_bounds.append([c_s, c_e])
    return chars_bounds


def batch_process_worker(path: str, params: Dict[str, float], trim_silence: bool, word_label: str = "") -> Dict[str, Any]:
    try:
        snd = parselmouth.Sound(path)
        pitch_data = extract_f0(snd, params)
        mac_s, mac_e = 0.0, snd.get_total_duration()

        mic_s, mic_e, raw_s, raw_e = core_microscopic_vowel_nucleus(
            snd, pitch_data, mac_s, mac_e,
            params['db'], params['skip_front'], trim_silence
        )

        name = os.path.splitext(os.path.basename(path))[0]

        label_for_split = word_label if word_label else name
        from .data_utils import split_into_syllables
        syls = split_into_syllables(label_for_split)

        # 检测是否进入词语模式，预初始化蓝线
        inner_splits = []
        chars_bounds = []
        split_warnings = []
        split_confidence = 1.0
        if len(syls) > 1:
            meta = {}
            inner_splits = auto_split_inner_word(snd, raw_s, raw_e, len(syls), pitch_data=pitch_data, output_meta=meta)
            split_warnings = meta.get('split_warnings', [])
            split_confidence = meta.get('split_confidence', 1.0)
            chars_bounds = auto_split_to_chars_bounds(snd, raw_s, raw_e, inner_splits, len(syls), params)
            if chars_bounds:
                mic_s = chars_bounds[0][0]
                mic_e = chars_bounds[-1][1]
        else:
            chars_bounds = [[mic_s, mic_e]]

        preview_times = np.linspace(mic_s, mic_e, 11)
        p_xs = pitch_data['xs']
        p_freqs = pitch_data['freqs']
        preview_f0 = np.interp(preview_times, p_xs, p_freqs).tolist()

        # 修正：跨越静音区（>25ms）时强制归零，避免产生假数据桥接
        for j, t in enumerate(preview_times):
            valid_indices = np.where(p_freqs > 0)[0]
            if len(valid_indices) == 0:
                preview_f0[j] = 0.0
                continue
            valid_xs = p_xs[valid_indices]
            if np.min(np.abs(valid_xs - t)) > 0.025:
                preview_f0[j] = 0.0

        has_empty_data = any(f == 0.0 for f in preview_f0)

        formant_data = None
        preview_formants = None
        if params.get('analysis_mode') == 'formant':
            formant_data, preview_formants = _sample_formants_helper(snd, mic_s, mic_e, params)

        return {
            'label': name,
            'path': path,
            'macro_start': mac_s,
            'macro_end': mac_e,
            'start': mic_s,
            'end': mic_e,
            'raw_start': raw_s,
            'raw_end': raw_e,
            'inner_splits': inner_splits,
            'chars_bounds': chars_bounds,
            'preview_f0': preview_f0,
            'pitch_data': pitch_data,
            'formant_data': formant_data,
            'preview_formants': preview_formants,
            'success': True,
            'has_empty_data': has_empty_data,
            'split_warnings': split_warnings,
            'split_confidence': split_confidence
        }
    except Exception as e:
        return {'success': False, 'error': str(e), 'path': path}


def find_minimum_intensity_valley(snd: parselmouth.Sound, t_ref: float, search_window: float = 0.08) -> float:
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


def _normalize_locked_chars_bounds(ref_chars_bounds: List[List[float]], ms: float, duration: float) -> List[List[float]]:
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


def long_process_worker(snd_values: np.ndarray, snd_sf: float, pitch_xs: np.ndarray, pitch_freqs: np.ndarray, ms: float, me: float, params: Dict[str, float], trim_silence: bool, word_label: str = "", ref_splits: List[float] = None, ref_chars_bounds: List[List[float]] = None) -> Dict[str, Any]:
    try:
        snd_part = parselmouth.Sound(snd_values, sampling_frequency=snd_sf)
        shifted_xs = pitch_xs - ms
        locked_chars_bounds = _normalize_locked_chars_bounds(ref_chars_bounds or [], ms, snd_part.get_total_duration())

        if locked_chars_bounds:
            mic_s = locked_chars_bounds[0][0]
            mic_e = locked_chars_bounds[-1][1]
            raw_s, raw_e = mic_s, mic_e
        else:
            # 提取微观红线边界
            mic_s, mic_e, raw_s, raw_e = core_microscopic_vowel_nucleus(
                snd_part, (shifted_xs, pitch_freqs), 0.0, snd_part.get_total_duration(),
                params['db'], params['skip_front'], trim_silence
            )

        # 提取内部蓝线边界
        inner_splits = []
        chars_bounds = []
        split_warnings = []
        split_confidence = 1.0
        from .data_utils import split_into_syllables
        syls = split_into_syllables(word_label) if word_label else []
        if locked_chars_bounds:
            chars_bounds = [[s + ms, e + ms] for s, e in locked_chars_bounds]
            inner_splits = [end for _start, end in chars_bounds[:-1]]
            if syls and len(syls) != len(chars_bounds):
                split_warnings.append("TextGrid chars 层数量与标签音节数不一致，已按 TextGrid 原边界保留。")
                split_confidence = 0.0
        elif syls and len(syls) > 1:
            if ref_splits:
                # 寻找在空白处（TextGrid分割线附近）的音量最低值点
                local_ref_splits = [t - ms for t in ref_splits]
                splits = [find_minimum_intensity_valley(snd_part, t_ref) for t_ref in local_ref_splits]
            else:
                meta = {}
                p_data = {'xs': shifted_xs, 'freqs': pitch_freqs}
                splits = auto_split_inner_word(snd_part, raw_s, raw_e, len(syls), pitch_data=p_data, output_meta=meta)
                split_warnings = meta.get('split_warnings', [])
                split_confidence = meta.get('split_confidence', 1.0)

            local_chars_bounds = auto_split_to_chars_bounds(snd_part, raw_s, raw_e, splits, len(syls), params)
            chars_bounds = [[s + ms, e + ms] for s, e in local_chars_bounds]
            inner_splits = [t + ms for t in splits]  # 复原到全局时间轴
            if local_chars_bounds:
                mic_s = local_chars_bounds[0][0]
                mic_e = local_chars_bounds[-1][1]
        else:
            chars_bounds = [[mic_s + ms, mic_e + ms]]

        mic_s += ms
        mic_e += ms
        raw_s += ms
        raw_e += ms

        preview_times = np.linspace(mic_s, mic_e, 11)
        preview_f0 = []
        for t in preview_times:
            idx = np.argmin(np.abs(pitch_xs - t))
            if np.abs(pitch_xs[idx] - t) < 0.1:
                f = pitch_freqs[idx]
            else:
                f = 0.0
            preview_f0.append(0.0 if np.isnan(f) else f)

        has_empty_data = any(f == 0.0 for f in preview_f0)

        formant_data = None
        preview_formants = None
        if params.get('analysis_mode') == 'formant':
            rel_s = mic_s - ms
            rel_e = mic_e - ms
            formant_data, preview_formants = _sample_formants_helper(snd_part, rel_s, rel_e, params)
            formant_data['xs'] = formant_data['xs'] + ms

        return {
            'ms': ms, 'me': me,
            'mis': mic_s, 'mie': mic_e,
            'raw_s': raw_s, 'raw_e': raw_e,
            'inner_splits': inner_splits,
            'chars_bounds': chars_bounds,
            'formant_data': formant_data,
            'preview_formants': preview_formants,
            'has_empty_data': has_empty_data,
            'split_warnings': split_warnings,
            'split_confidence': split_confidence,
            'success': True
        }
    except Exception as e:
        return {'success': False, 'error': str(e), 'ms': ms, 'me': me}


def process_single_long_word(snd_values: np.ndarray, snd_sf: float, word: str, ms: float, me: float, params: Dict[str, float], trim: bool, pitch_xs: np.ndarray, pitch_freqs: np.ndarray, ref_splits: List[float] = None, ref_chars_bounds: List[List[float]] = None) -> Dict[str, Any]:
    """
    Import helper that processes a single word slice from a long audio,
    running long_process_worker and translating keys back to target format.
    """
    res = long_process_worker(snd_values, snd_sf, pitch_xs, pitch_freqs, ms, me, params, trim, word_label=word, ref_splits=ref_splits, ref_chars_bounds=ref_chars_bounds)
    if res.get('success'):
        return {
            'label': word,
            'macro_start': ms,
            'macro_end': me,
            'start': res['mis'],
            'end': res['mie'],
            'raw_start': res['raw_s'],
            'raw_end': res['raw_e'],
            'inner_splits': res.get('inner_splits', []),
            'chars_bounds': res.get('chars_bounds', []),
            'formant_data': res.get('formant_data'),
            'preview_formants': res.get('preview_formants'),
            'has_empty_data': res.get('has_empty_data', False),
            'split_warnings': res.get('split_warnings', []),
            'split_confidence': res.get('split_confidence', 1.0),
            'success': True
        }
    else:
        return {
            'label': word,
            'success': False,
            'error': res.get('error', 'Unknown error')
        }


def batch_process_worker_with_textgrid(path: str, tg_path: str, params: Dict[str, float], trim_silence: bool) -> Dict[str, Any]:
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

        snd = parselmouth.Sound(path)
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
                from .data_utils import split_into_syllables
                syls = split_into_syllables(lbl)
                w_len = len(syls)
                if w_len > 1:
                    splits = np.linspace(t_s, t_e, w_len + 1).tolist()
                    chars_bounds = [[splits[j], splits[j+1]] for j in range(w_len)]
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
                params['db'], params['skip_front'], trim_silence
            )

        preview_times = np.linspace(mic_s, mic_e, 11)
        p_xs = pitch_data['xs']
        p_freqs = pitch_data['freqs']
        preview_f0 = np.interp(preview_times, p_xs, p_freqs).tolist()

        for j, t in enumerate(preview_times):
            valid_indices = np.where(p_freqs > 0)[0]
            if len(valid_indices) == 0:
                preview_f0[j] = 0.0
                continue
            valid_xs = p_xs[valid_indices]
            if np.min(np.abs(valid_xs - t)) > 0.025:
                preview_f0[j] = 0.0

        has_empty_data = any(f == 0.0 for f in preview_f0)

        formant_data = None
        preview_formants = None
        if params.get('analysis_mode') == 'formant':
            formant_data, preview_formants = _sample_formants_helper(snd, mic_s, mic_e, params)

        return {
            'label': lbl,
            'path': path,
            'group': grp_name,
            'macro_start': t_s,
            'macro_end': t_e,
            'start': mic_s,
            'end': mic_e,
            'raw_start': raw_s,
            'raw_end': raw_e,
            'inner_splits': inner_splits,
            'chars_bounds': chars_bounds,
            'preview_f0': preview_f0,
            'pitch_data': pitch_data,
            'formant_data': formant_data,
            'preview_formants': preview_formants,
            'success': True,
            'has_empty_data': has_empty_data
        }
    except Exception as e:
        return {'success': False, 'error': str(e), 'path': path}
