import os
import numpy as np
import parselmouth
import re  # 新增正则库用于支持多种分隔符拆分

def parse_wordlist(raw_text):
    groups = []
    flat_words =[]
    curr_group = "未分组"
    curr_items =[]
    for line in raw_text.split('\n'):
        line = line.strip()
        if not line: continue
        if line.startswith('【') or line.startswith('[') or line.startswith('#'):
            if curr_items:
                groups.append({"group": curr_group, "items": curr_items})
                curr_items = []
            curr_group = line.replace('【', '').replace('】', '').replace('[', '').replace(']', '').replace('#', '').strip()
        else:
            # 核心修改：支持一行多个字，通过空格、制表符、中英文逗号、顿号灵活拆分
            words = [w.strip() for w in re.split(r'[,\s\t，、]+', line) if w.strip()]
            curr_items.extend(words)
            flat_words.extend(words)
            
    if curr_items: groups.append({"group": curr_group, "items": curr_items})
    return groups, flat_words

def fuzzy_match_word_to_path(word, available_paths, used_indices=None):
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
        # 优先级权重：是否已被使用 (已使用则权重+1000) + 长度差异
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

def get_export_text_for_item(item, real_index, num_points):
    if item.get('start') is None or item.get('end') is None: return ""
    t_s, t_e = item['start'], item['end']
    duration = t_e - t_s
    
    # 性能优化：如果数据未加载，但请求点数是 11 且有预计算数据，直接使用
    if (not item.get('snd') or not item.get('pitch')) and item.get('path'):
        if num_points == 11 and item.get('preview_f0'):
            output = f"{real_index}.{item['label']}\n{duration:.3f}\n"
            times = np.linspace(t_s, t_e, 11)
            for i, t in enumerate(times):
                f0 = item['preview_f0'][i]
                f0_str = "0.000000" if f0 == 0 else f"{f0:.6f}"
                output += f"{t:.6f}   {f0_str}\n"
            return output
        else:
            # 否则静默加载（用于导出全表等场景）
            try:
                item['snd'] = parselmouth.Sound(item['path'])
                item['pitch'] = item['snd'].to_pitch()
            except Exception: return ""

    if duration <= 0 or not item.get('snd'): return ""
    
    times = np.linspace(t_s, t_e, num_points)
    output = f"{real_index}.{item['label']}\n{duration:.3f}\n"
    for t in times:
        f0 = item['pitch'].get_value_at_time(t)
        f0_str = "0.000000" if np.isnan(f0) else f"{f0:.6f}"
        output += f"{t:.6f}   {f0_str}\n"
    return output