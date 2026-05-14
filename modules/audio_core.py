import numpy as np
import os

# 静音阈值：振幅 ≈ -50dB (10^(-50/20) ≈ 0.00316)
SILENCE_AMPLITUDE_THRESHOLD = 10 ** (-50 / 20)

def macroscopic_vad(snd):
    """长音频宏观静音检测分割"""
    intensity = snd.to_intensity(time_step=0.01)
    vals = intensity.values[0]
    xs = intensity.xs()
    sorted_vals = np.sort(vals[~np.isnan(vals)])
    max_int = np.mean(sorted_vals[-int(len(sorted_vals)*0.05):]) if len(sorted_vals) > 20 else 70
    thresh = max_int - 25 
    is_sp = vals > thresh
    segs, start =[], None
    for i, s in enumerate(is_sp):
        if s and start is None: start = xs[i]
        elif not s and start is not None:
            segs.append([start, xs[i]])
            start = None
    if start is not None: segs.append([start, xs[-1]])
    
    merged =[]
    for s in segs:
        if not merged: merged.append(s)
        else:
            if s[0] - merged[-1][1] < 0.25: merged[-1][1] = s[1]
            else: merged.append(s)
    return [s for s in merged if s[1]-s[0] > 0.1]

def core_microscopic_vowel_nucleus(snd, global_pitch, t_min, t_max, drop_db, min_dur, trim_silence):
    """微观韵母提取核心算法"""
    try:
        part = snd.extract_part(from_time=t_min, to_time=t_max)
        intensity = part.to_intensity()
        xs = global_pitch.xs()
        freqs = global_pitch.selected_array['frequency']
        
        try: max_int = np.nanmax(intensity.values)
        except Exception: return t_min, t_max
            
        best_s, best_e = 0.0, part.get_total_duration()
        thresh = max_int - drop_db
        valid =[]
        for t in part.xs():
            idx = np.argmin(np.abs(xs - (t_min + t)))
            if idx < len(freqs) and freqs[idx] > 0:
                val = intensity.get_value(t)
                if val and not np.isnan(val) and val > thresh: valid.append(t)
                
        if len(valid) > 2 and (valid[-1] - valid[0]) > min_dur:
            best_s, best_e = valid[0], valid[-1]
            
        temp_s, temp_e = t_min + best_s, t_min + best_e
        
        if trim_silence:
            trim_part = snd.extract_part(from_time=temp_s, to_time=temp_e)
            vals = trim_part.values[0]
            trim_xs = trim_part.xs()
            valid_idx = np.where(np.abs(vals) > SILENCE_AMPLITUDE_THRESHOLD)[0]
            if len(valid_idx) > 0:
                return temp_s + trim_xs[valid_idx[0]], temp_s + trim_xs[valid_idx[-1]]
                
        return temp_s, temp_e
    except Exception:
        return t_min, t_max

def batch_process_worker(path, params, trim_silence):
    """独立文件处理工人。不返回 parselmouth 对象以避免 Windows 下的 Pickle 错误"""
    import parselmouth
    try:
        snd = parselmouth.Sound(path)
        pitch = snd.to_pitch(pitch_floor=params.get('pitch_floor', 75), pitch_ceiling=params.get('pitch_ceiling', 600))
        mac_s, mac_e = 0.0, snd.get_total_duration()
        
        mic_s, mic_e = core_microscopic_vowel_nucleus(
            snd, pitch, mac_s, mac_e, 
            params['db'], params['dur'], trim_silence
        )
        
        # 预计算一些导出预览所需的数据点 (11点 F0)，避免主线程反复读取
        preview_times = np.linspace(mic_s, mic_e, 11)
        preview_f0 = [pitch.get_value_at_time(t) for t in preview_times]
        preview_f0 = [0.0 if np.isnan(f) else f for f in preview_f0]
        
        name = os.path.splitext(os.path.basename(path))[0]
        return {
            'label': name,
            'path': path,
            'macro_start': mac_s,
            'macro_end': mac_e,
            'start': mic_s,
            'end': mic_e,
            'preview_f0': preview_f0, # 传递数值而非对象
            'success': True
        }
    except Exception as e:
        return {'success': False, 'error': str(e), 'path': path}