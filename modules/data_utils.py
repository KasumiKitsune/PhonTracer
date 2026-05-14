import os
import numpy as np
import parselmouth

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
            curr_items.append(line)
            flat_words.append(line)
    if curr_items: groups.append({"group": curr_group, "items": curr_items})
    return groups, flat_words

def fuzzy_match_word_to_path(word, available_paths):
    word_lower = word.lower()
    exact_matches, contains_matches = [],[]
    for i, p in enumerate(available_paths):
        fname = os.path.splitext(os.path.basename(p))[0].lower()
        if fname == word_lower: exact_matches.append(i)
        elif word_lower in fname or fname in word_lower: contains_matches.append(i)
    
    if exact_matches: return exact_matches[0]
    if contains_matches:
        contains_matches.sort(key=lambda i: len(os.path.basename(available_paths[i])))
        return contains_matches[0]
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