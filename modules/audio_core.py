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
VAD_MERGE_THRESHOLD = 0.25

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


def macroscopic_vad(snd: parselmouth.Sound) -> List[List[float]]:
    """长音频宏观静音检测分割"""
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
    
    merged =[]
    for s in segs:
        if not merged: merged.append(s)
        else:
            if s[0] - merged[-1][1] < VAD_MERGE_THRESHOLD: merged[-1][1] = s[1]
            else: merged.append(s)
    return [s for s in merged if s[1]-s[0] > VAD_MIN_DURATION]


def core_microscopic_vowel_nucleus(snd: parselmouth.Sound, global_pitch_or_arrays: Union[parselmouth.Pitch, Tuple[np.ndarray, np.ndarray]], t_min: float, t_max: float, drop_db: float, skip_front: float, trim_silence: bool) -> Tuple[float, float, float, float]:
    """微观韵母提取核心算法"""
    try:
        if isinstance(global_pitch_or_arrays, tuple):
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
            valid_idx = np.where(np.abs(vals) > SILENCE_AMPLITUDE_THRESHOLD)[0]
            if len(valid_idx) > 0:
                final_s = temp_s + trim_xs[valid_idx[0]]
                final_e = temp_s + trim_xs[valid_idx[-1]]
                
        # --- 算法优化：严格收缩到有效基频区间，消除头尾 0 值 ---
        valid_pitch_xs = [x for x, f in zip(xs, freqs) if f > 0 and final_s <= x <= final_e]
        if len(valid_pitch_xs) >= 2:
            final_s = valid_pitch_xs[0]
            final_e = valid_pitch_xs[-1]
            
        return final_s, final_e, temp_s, temp_e
    except Exception:
        return t_min, t_max, t_min, t_max


def recalculate_bounds_fast(snd: parselmouth.Sound, global_pitch_or_arrays: Union[parselmouth.Pitch, Tuple[np.ndarray, np.ndarray]], temp_s: float, temp_e: float, trim_silence: bool) -> Tuple[float, float]:
    """仅重新计算静音裁切和基频收缩，不重新跑振幅分析"""
    try:
        if isinstance(global_pitch_or_arrays, tuple):
            xs, freqs = global_pitch_or_arrays
        else:
            xs = global_pitch_or_arrays.xs()
            freqs = global_pitch_or_arrays.selected_array['frequency']
            
        final_s, final_e = temp_s, temp_e
        
        if trim_silence:
            trim_part = snd.extract_part(from_time=temp_s, to_time=temp_e) if temp_s != 0 or temp_e != snd.get_total_duration() else snd
            vals = trim_part.values[0]
            trim_xs = trim_part.xs()
            valid_idx = np.where(np.abs(vals) > SILENCE_AMPLITUDE_THRESHOLD)[0]
            if len(valid_idx) > 0:
                final_s = temp_s + trim_xs[valid_idx[0]]
                final_e = temp_s + trim_xs[valid_idx[-1]]
                
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


def auto_split_inner_word(snd: parselmouth.Sound, t_min: float, t_max: float, word_len: int) -> List[float]:
    """
    词语模式内部子音节切分算法 (自动识别蓝线)：
    基于平滑后的短时能量寻找谷底，作为字与字之间的切分点。如果失败自动退化为等比例划分。
    """
    n_splits = word_len - 1
    if n_splits <= 0: return []
    
    # 兜底：等距离均分点
    fallback_splits = [t_min + (t_max - t_min) * (i / word_len) for i in range(1, word_len)]
    
    if t_max - t_min < 0.1: # 小于 100ms 太短，不具备检测价值
        return fallback_splits
        
    try:
        part = snd.extract_part(from_time=t_min, to_time=t_max)
        intensity = part.to_intensity(time_step=0.01)
        vals = intensity.values[0]
        xs = intensity.xs() + t_min
        
        # 平滑能量曲线
        window = np.ones(5) / 5.0
        if len(vals) < 5: return fallback_splits
        smoothed = np.convolve(vals, window, mode='same')
        
        # 寻找能量谷底 (即负向能量的峰值)
        import scipy.signal
        valleys, _ = scipy.signal.find_peaks(-smoothed, distance=8) # 限制字与字最小跨度为约 80ms
        
        if len(valleys) >= n_splits:
            # 优先选择能量最低的核心谷底
            sorted_valleys = sorted(valleys, key=lambda idx: smoothed[idx])
            best_valleys = sorted_valleys[:n_splits]
            best_valleys.sort() # 按时间流重排
            return [float(xs[v]) for v in best_valleys]
    except Exception:
        pass
        
    return fallback_splits

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
                c_pitch = c_snd.to_pitch_ac(time_step=None, pitch_floor=params.get('pitch_floor', 75), pitch_ceiling=params.get('pitch_ceiling', 600), voicing_threshold=params.get('voicing_threshold', 0.25), very_accurate=True, octave_jump_cost=0.9)
                p_xs = c_pitch.xs() + c_s
                p_freqs = c_pitch.selected_array['frequency']
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
        pitch = snd.to_pitch_ac(time_step=None, pitch_floor=params.get('pitch_floor', 75), pitch_ceiling=params.get('pitch_ceiling', 600), voicing_threshold=params.get('voicing_threshold', 0.25), very_accurate=True, octave_jump_cost=0.9)
        mac_s, mac_e = 0.0, snd.get_total_duration()
        
        mic_s, mic_e, raw_s, raw_e = core_microscopic_vowel_nucleus(
            snd, pitch, mac_s, mac_e, 
            params['db'], params['skip_front'], trim_silence
        )
        
        preview_times = np.linspace(mic_s, mic_e, 11)
        preview_f0 = [pitch.get_value_at_time(t) for t in preview_times]
        preview_f0 = [0.0 if np.isnan(f) else f for f in preview_f0]
        has_empty_data = any(f == 0.0 for f in preview_f0)
        
        name = os.path.splitext(os.path.basename(path))[0]
        
        label_for_split = word_label if word_label else name
        
        # 检测是否进入词语模式，预初始化蓝线
        inner_splits = []
        chars_bounds = []
        if len(label_for_split) > 1:
            inner_splits = auto_split_inner_word(snd, mic_s, mic_e, len(label_for_split))
            chars_bounds = auto_split_to_chars_bounds(snd, mic_s, mic_e, inner_splits, len(label_for_split), params)
        else:
            chars_bounds = [[mic_s, mic_e]]
            
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
            'success': True,
            'has_empty_data': has_empty_data
        }
    except Exception as e:
        return {'success': False, 'error': str(e), 'path': path}


def long_process_worker(snd_values: np.ndarray, snd_sf: float, pitch_xs: np.ndarray, pitch_freqs: np.ndarray, ms: float, me: float, params: Dict[str, float], trim_silence: bool, word_label: str = "") -> Dict[str, Any]:
    try:
        snd_part = parselmouth.Sound(snd_values, sampling_frequency=snd_sf)
        shifted_xs = pitch_xs - ms
        
        # 提取微观红线边界
        mic_s, mic_e, raw_s, raw_e = core_microscopic_vowel_nucleus(
            snd_part, (shifted_xs, pitch_freqs), 0.0, snd_part.get_total_duration(), 
            params['db'], params['skip_front'], trim_silence
        )
        
        # 提取内部蓝线边界
        inner_splits = []
        chars_bounds = []
        if word_label and len(word_label) > 1:
            splits = auto_split_inner_word(snd_part, mic_s, mic_e, len(word_label))
            local_chars_bounds = auto_split_to_chars_bounds(snd_part, mic_s, mic_e, splits, len(word_label), params)
            chars_bounds = [[s + ms, e + ms] for s, e in local_chars_bounds]
            inner_splits = [t + ms for t in splits]  # 复原到全局时间轴
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
        
        return {
            'ms': ms, 'me': me,
            'mis': mic_s, 'mie': mic_e,
            'raw_s': raw_s, 'raw_e': raw_e,
            'inner_splits': inner_splits,
            'chars_bounds': chars_bounds,
            'has_empty_data': has_empty_data,
            'success': True
        }
    except Exception as e:
        return {'success': False, 'error': str(e), 'ms': ms, 'me': me}