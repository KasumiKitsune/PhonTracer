"""
modules.export_service.textgrid_io - TextGrid 导出与逆向解析模块
支持词条级 TextGrid 层级组装与从标注文件提取字表。
"""

from typing import List, Dict, Optional, Any, Tuple
import numpy as np
import textgrid

from ..core.text_io import split_into_syllables

def get_export_textgrid_for_item(item, max_time=None):
    if item.get('start') is None or item.get('end') is None: return None
    t_s, t_e = item['start'], item['end']

    label = item.get('label', '')
    inner_splits = item.get('inner_splits', [])
    syls = split_into_syllables(label)
    is_word_mode = len(syls) > 1

    tg = textgrid.TextGrid(maxTime=max_time if max_time else t_e)

    # Create Groups tier
    group_name = item.get('group', '导入内容')
    group_tier = textgrid.IntervalTier(name="groups", minTime=0.0, maxTime=max_time if max_time else t_e)
    if t_s > 0:
        group_tier.add(0.0, t_s, "")
    group_tier.add(t_s, t_e, group_name)
    if (max_time and max_time > t_e) or (not max_time):
        end_time = max_time if max_time else t_e
        if end_time > t_e:
            group_tier.add(t_e, end_time, "")
    tg.append(group_tier)

    # Create Words tier
    word_tier = textgrid.IntervalTier(name="words", minTime=0.0, maxTime=max_time if max_time else t_e)
    if t_s > 0:
        word_tier.add(0.0, t_s, "")
    word_tier.add(t_s, t_e, label)
    if (max_time and max_time > t_e) or (not max_time):
        end_time = max_time if max_time else t_e
        if end_time > t_e:
            word_tier.add(t_e, end_time, "")
    tg.append(word_tier)

    char_tier = textgrid.IntervalTier(name="chars", minTime=0.0, maxTime=max_time if max_time else t_e)
    if t_s > 0:
        char_tier.add(0.0, t_s, "")

    if is_word_mode:
        chars_bounds = item.get('chars_bounds', [])
        if not chars_bounds:
            import numpy as np
            splits = [t_s] + [s for s in inner_splits if t_s < s < t_e] + [t_e]
            if len(splits) != len(syls) + 1:
                splits = np.linspace(t_s, t_e, len(syls) + 1).tolist()
            chars_bounds = [(splits[j], splits[j+1]) for j in range(len(splits)-1)]

        last_e = t_s
        for i in range(len(syls)):
            char = syls[i]
            if i < len(chars_bounds):
                c_start, c_end = chars_bounds[i]
                if c_start > last_e:
                    char_tier.add(last_e, c_start, "")
                char_tier.add(c_start, c_end, char)
                last_e = c_end

        if last_e < t_e:
            char_tier.add(last_e, t_e, "")

        end_time = max_time if max_time else t_e
        if end_time > t_e:
            char_tier.add(t_e, end_time, "")
    else:
        char_tier.add(t_s, t_e, label)
        end_time = max_time if max_time else t_e
        if end_time > t_e:
            char_tier.add(t_e, end_time, "")

    tg.append(char_tier)

    return tg


def extract_wordlist_from_textgrid(path):
    import textgrid
    tg = textgrid.TextGrid.fromFile(path)

    words_tier = None
    groups_tier = None
    for t in tg.tiers:
        name_lower = t.name.strip().lower()
        if name_lower in ["words", "word"] and words_tier is None:
            words_tier = t
        elif name_lower in ["groups", "group"] and groups_tier is None:
            groups_tier = t

    if not words_tier:
        for t in tg.tiers:
            if isinstance(t, textgrid.IntervalTier):
                words_tier = t
                break

    if not words_tier:
        raise ValueError("TextGrid 中没有找到有效的区间层 (words)。")

    # Group words by group name
    group_to_words = {}
    for interval in words_tier:
        w_lbl = interval.mark.strip()
        if not w_lbl:
            continue
        # Find group name
        grp_name = "导入内容"
        if groups_tier:
            center = (interval.minTime + interval.maxTime) / 2.0
            for g_interval in groups_tier:
                if g_interval.minTime <= center <= g_interval.maxTime:
                    g_lbl = g_interval.mark.strip()
                    if g_lbl:
                        grp_name = g_lbl
                        break
        if grp_name not in group_to_words:
            group_to_words[grp_name] = []
        group_to_words[grp_name].append(w_lbl)

    # Format as wordlist text
    lines = []
    for grp_name, words in group_to_words.items():
        lines.append(f"【{grp_name}】")
        lines.append(" ".join(words))
    return "\n".join(lines)
