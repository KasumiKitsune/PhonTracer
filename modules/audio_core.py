

import numpy as np
import os

# 静音阈值：振幅 ≈ -50dB (10^(-50/20) ≈ 0.00316)
SILENCE_AMPLITUDE_THRESHOLD = 10 ** (-50 / 20)

def detect_vowel_onset(snd, rough_start, rough_end):
    """
    智能元音起始点 (VOP) 检测：
    基于 短时能量(STE)突变 + 过零率(ZCR) 惩罚
    取代机械的固定时长裁切。
    """
    # 前后加 20ms 缓冲提取波形，确保能算出一阶导数
    buffer = 0.02  
    part_s = max(0, rough_start - buffer)
    part_e = min(snd.get_total_duration(), rough_end + buffer)
    
    if part_e <= part_s:
        return rough_start
        
    part = snd.extract_part(from_time=part_s, to_time=part_e)
    vals = part.values[0]
    sr = part.sampling_frequency
    
    # 设定 10ms 窗口，2ms 步长（高分辨率时间轴）
    win_len = int(0.010 * sr)  
    hop_len = int(0.002 * sr)  
    
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
        crossings = np.sum(np.abs(np.diff(frame > 0)))
        zcr[i] = crossings / max(1, (win_len - 1))
        
        # 2. 计算短时能量 (Short-Time Energy: RMS) - 韵母/元音特征
        ste[i] = np.sqrt(np.mean(frame**2))
        times[i] = part_s + (i * hop_len + win_len / 2) / sr
        
    max_ste = np.max(ste)
    if max_ste < 1e-5:  # 全是绝对静音
        return rough_start
        
    # 局部能量归一化
    ste_norm = ste / max_ste
    
    # 3. 能量一阶导数（寻找能量暴涨的瞬间，对应浊辅音 m/n 的除阻，或韵母起振）
    ste_diff = np.diff(ste_norm, prepend=ste_norm[0])
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


def macroscopic_vad(snd):
    """长音频宏观静音检测分割"""
    intensity = snd.to_intensity(time_step=0.01)
    vals = intensity.values[0]
    xs = intensity.xs()
    sorted_vals = np.sort(vals[~np.isnan(vals)])
    max_int = np.mean(sorted_vals[-int(len(sorted_vals)*0.05):]) if len(sorted_vals) > 20 else 70
    thresh = max_int - 25 
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
            if s[0] - merged[-1][1] < 0.25: merged[-1][1] = s[1]
            else: merged.append(s)
    return [s for s in merged if s[1]-s[0] > 0.1]


def core_microscopic_vowel_nucleus(snd, global_pitch_or_arrays, t_min, t_max, drop_db, skip_front, trim_silence):
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
        valid =[]
        for t in part.xs():
            idx = np.argmin(np.abs(xs - (t_min + t)))
            if idx < len(freqs) and freqs[idx] > 0:
                val = intensity.get_value(t)
                if val and not np.isnan(val) and val > thresh: valid.append(t)
                
        if len(valid) > 2:
            best_s, best_e = valid[0], valid[-1]
            
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


def recalculate_bounds_fast(snd, global_pitch_or_arrays, temp_s, temp_e, trim_silence):
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


def check_audio_segments(path):
    """在子进程中检查音频区段数量，避免主线程 GIL 冲突"""
    import parselmouth
    snd = parselmouth.Sound(path)
    return len(macroscopic_vad(snd))


def batch_process_worker(path, params, trim_silence):
    import parselmouth
    try:
        snd = parselmouth.Sound(path)
        pitch = snd.to_pitch(pitch_floor=params.get('pitch_floor', 75), pitch_ceiling=params.get('pitch_ceiling', 600))
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
        return {
            'label': name,
            'path': path,
            'macro_start': mac_s,
            'macro_end': mac_e,
            'start': mic_s,
            'end': mic_e,
            'raw_start': raw_s,
            'raw_end': raw_e,
            'preview_f0': preview_f0,
            'success': True,
            'has_empty_data': has_empty_data
        }
    except Exception as e:
        return {'success': False, 'error': str(e), 'path': path}


def long_process_worker(snd_values, snd_sf, pitch_xs, pitch_freqs, ms, me, params, trim_silence):
    import parselmouth
    try:
        snd_part = parselmouth.Sound(snd_values, sampling_frequency=snd_sf)
        shifted_xs = pitch_xs - ms
        
        mic_s, mic_e, raw_s, raw_e = core_microscopic_vowel_nucleus(
            snd_part, (shifted_xs, pitch_freqs), 0.0, snd_part.get_total_duration(), 
            params['db'], params['skip_front'], trim_silence
        )
        
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
            'has_empty_data': has_empty_data,
            'success': True
        }
    except Exception as e:
        return {'success': False, 'error': str(e), 'ms': ms, 'me': me}