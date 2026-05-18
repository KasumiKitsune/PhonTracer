import os
from typing import List, Dict, Optional, Any, Tuple
import numpy as np
import parselmouth
import re  # 新增正则库用于支持多种分隔符拆分

def parse_wordlist(raw_text: str) -> Tuple[List[Dict[str, Any]], List[str]]:
    groups = []
    flat_words =[]
    curr_group = "未分组"
    curr_items =[]
    for line in raw_text.split('\n'):
        line = line.strip()
        if not line: continue
        if line.startswith('【') or line.startswith('[') or line.startswith('［') or line.startswith('#'):
            if curr_items:
                groups.append({"group": curr_group, "items": curr_items})
                curr_items = []
            curr_group = line.replace('【', '').replace('】', '').replace('[', '').replace(']', '').replace('［', '').replace('］', '').replace('#', '').strip()
        else:
            words = [w.strip() for w in re.split(r'[,\s\t，、]+', line) if w.strip()]
            curr_items.extend(words)
            flat_words.extend(words)
            
    if curr_items: groups.append({"group": curr_group, "items": curr_items})
    return groups, flat_words

def fuzzy_match_word_to_path(word: str, available_paths: List[str], used_indices: Optional[List[int]] = None) -> Optional[int]:
    def clean_str(s):
        if not s: return ""
        import re
        import unicodedata
        s = s.replace('\ufeff', '')
        s = unicodedata.normalize('NFC', s)
        s = re.sub(r'[^\w\u4e00-\u9fa5]|_', '', s)
        return s.lower().strip()
        
    if used_indices is None: used_indices = []
    word_clean = clean_str(word)
    if not word_clean: return None
        
    exact_matches = []
    substring_matches = []
    
    for i, p in enumerate(available_paths):
        fname_raw = os.path.splitext(os.path.basename(p))[0]
        fname_clean = clean_str(fname_raw)
        
        if fname_clean == word_clean:
            exact_matches.append(i)
        elif word_clean in fname_clean or fname_clean in word_clean:
            substring_matches.append(i)
    
    def sort_key(idx):
        is_used = 1 if idx in used_indices else 0
        len_diff = abs(len(os.path.splitext(os.path.basename(available_paths[idx]))[0]) - len(word))
        return (is_used, len_diff)

    if exact_matches:
        exact_matches.sort(key=sort_key)
        return exact_matches[0]
        
    if substring_matches:
        substring_matches.sort(key=sort_key)
        return substring_matches[0]
        
    return None

def get_export_text_for_item(item: Dict[str, Any], real_index: int, num_points: int, pitch_floor: float = 75.0, pitch_ceiling: float = 600.0, voicing_threshold: float = 0.25) -> str:
    if item.get('start') is None or item.get('end') is None: return ""
    t_s, t_e = item['start'], item['end']
    duration = t_e - t_s
    
    label = item.get('label', '')
    inner_splits = item.get('inner_splits', [])
    is_word_mode = len(label) > 1
    
    # 优先使用 item 内部存储的个性化参数实现所见即所得
    p_floor = item.get('pitch_floor', pitch_floor)
    p_ceiling = item.get('pitch_ceiling', pitch_ceiling)
    v_thresh = item.get('voicing_threshold', voicing_threshold)

    if (not item.get('snd') or not item.get('pitch')) and item.get('path'):
        if num_points == 11 and item.get('preview_f0') and not is_word_mode:
            output = f"{real_index}.{label}\n{duration:.3f}\n"
            times = np.linspace(t_s, t_e, 11)
            for i, t in enumerate(times):
                f0 = item['preview_f0'][i]
                f0_str = "0.000000" if f0 == 0 else f"{f0:.6f}"
                output += f"{t:.6f}   {f0_str}\n"
            return output
        else:
            try:
                item['snd'] = parselmouth.Sound(item['path'])
                item['pitch'] = item['snd'].to_pitch_ac(time_step=None, pitch_floor=p_floor, pitch_ceiling=p_ceiling, voicing_threshold=v_thresh, very_accurate=True, octave_jump_cost=0.9)
            except Exception: return ""

    if duration <= 0 or not item.get('snd'): return ""
    
    output = ""
    if is_word_mode:
        chars_bounds = item.get('chars_bounds', [])
        if not chars_bounds:
            splits = [t_s] + [s for s in inner_splits if t_s < s < t_e] + [t_e]
            if len(splits) != len(label) + 1:
                splits = np.linspace(t_s, t_e, len(label) + 1).tolist()
            chars_bounds = [(splits[j], splits[j+1]) for j in range(len(splits)-1)]
            
        pitch = item['pitch']
        p_xs = pitch.xs()
        p_freqs = pitch.selected_array['frequency']
            
        for i in range(len(label)):
            char = label[i]
            if i < len(chars_bounds):
                c_start, c_end = chars_bounds[i]
            else:
                continue
            
            # 独立音频提取基频：避免全局连读造成的 Viterbi octave jump
            try:
                if c_end - c_start <= 0.01: continue
                c_snd = item['snd'].extract_part(from_time=c_start, to_time=c_end)
                c_pitch = c_snd.to_pitch_ac(time_step=None, pitch_floor=p_floor, pitch_ceiling=p_ceiling, voicing_threshold=v_thresh, very_accurate=True, octave_jump_cost=0.9)
                p_xs = c_pitch.xs() + c_start
                p_freqs = c_pitch.selected_array['frequency']
            except Exception:
                continue
            
            valid_idx = np.where(p_freqs > 0)[0]
            if len(valid_idx) >= 2:
                v_start, v_end = p_xs[valid_idx[0]], p_xs[valid_idx[-1]]
                seg_xs = p_xs[valid_idx]
                seg_ys = p_freqs[valid_idx]
            else:
                continue # 没有有效基频，跳过该字
                
            c_dur = v_end - v_start
            if c_dur <= 0: continue
            
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
                    output += f"{t:.6f}   {f0_str}\n"
            else:
                for t in times:
                    output += f"{t:.6f}   0.000000\n"
    else:
        # 单字模式同样应用此逻辑
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
                    output += f"{t:.6f}   {f0_str}\n"
            else:
                for t in times:
                    output += f"{t:.6f}   0.000000\n"
        else:
            output += f"{real_index}.{label}\n0.000\n"
            times = np.linspace(t_s, t_e, num_points)
            for t in times:
                output += f"{t:.6f}   0.000000\n"
            
    return output