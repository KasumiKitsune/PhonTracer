import os
import textgrid
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

def has_cjk(word: str) -> bool:
    if not word: return False
    return bool(re.search(r'[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff]', word))

def split_into_syllables(word: str) -> List[str]:
    if not word: return []
    if '/' in word:
        return [s.strip() for s in word.split('/') if s.strip()]
    if has_cjk(word):
        return re.findall(r'[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff]', word)
    cleaned = word.strip()
    return [cleaned] if cleaned else []

def fuzzy_match_word_to_path(word: str, available_paths: List[str], used_indices: Optional[List[int]] = None) -> Optional[int]:
    is_cjk_mode = has_cjk(word)
    
    def clean_str(s):
        if not s: return ""
        import unicodedata
        s = s.replace('\ufeff', '')
        s = unicodedata.normalize('NFC', s)
        if is_cjk_mode:
            # 只保留 CJK 字符
            s = "".join(re.findall(r'[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff]', s))
        else:
            # 只保留字母(包括带声调的拉丁字母)，无视数字、斜杠、下划线及其他特殊字符
            s = "".join(re.findall(r'[^\W\d_]', s))
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
    syls = split_into_syllables(label)
    is_word_mode = len(syls) > 1
    
    # 优先使用 item 内部存储的个性化参数实现所见即所得
    p_floor = item.get('pitch_floor', pitch_floor)
    p_ceiling = item.get('pitch_ceiling', pitch_ceiling)
    v_thresh = item.get('voicing_threshold', voicing_threshold)
    engine = item.get('f0_engine', 'praat')
 
    from .audio_core import extract_f0
 
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
                item['pitch_data'] = extract_f0(item['snd'], {'f0_engine': engine, 'pitch_floor': p_floor, 'pitch_ceiling': p_ceiling, 'voicing_threshold': v_thresh})
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
                c_pitch_data = extract_f0(c_snd, {'f0_engine': engine, 'pitch_floor': p_floor, 'pitch_ceiling': p_ceiling, 'voicing_threshold': v_thresh})
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

def write_analysis_sheet_with_formulas(workbook, ws_res, group_list, num_points, max_syls,
                                       last_data_row, data_sheet_name='数据', speaker_col=None):
    """
    在分析结果 Sheet 中写入 Excel 公式，引用数据 Sheet 的原始 Hz 值进行计算。

    布局:
      Section A (Hz 均值): AVERAGEIFS 公式，按组别从数据表中计算各测量点的平均 Hz
      Section B (全局基频范围): MIN / MAX 公式
      Section C (五度标调 T 值): LOG 转换公式，引用 Section A 与 B

    Parameters
    ----------
    workbook : xlsxwriter.Workbook
    ws_res : xlsxwriter worksheet
    group_list : list[str] — 有序的组别名列表
    num_points : int — 采样点数（如 11）
    max_syls : int — 最大音节数
    last_data_row : int — 数据 Sheet 中最后一行数据的 0-indexed 行号
    data_sheet_name : str
    speaker_col : str, optional — 如果指定，表示是多发音人整合模式，首列将是发音人，数据列将右移

    Returns
    -------
    (res_row, min_cell_abs, max_cell_abs) :
        res_row — T 值数据区之后的下一行（用于插入图表）
        min_cell_abs — 全局最低 Hz 单元格绝对引用字符串
        max_cell_abs — 全局最高 Hz 单元格绝对引用字符串
    """
    from xlsxwriter.utility import xl_col_to_name, xl_rowcol_to_cell

    ds = data_sheet_name  # shorthand
    num_groups = len(group_list)
    # Excel row of last data (1-indexed) = last_data_row + 1
    lr = last_data_row + 1

    # 如果有 speaker_col，表示是多发音人整合导出，此时数据列会右移1列（首列为发音人，组别列为B）
    is_integrated = (speaker_col is not None)
    grp_col_letter = 'B' if is_integrated else 'A'
    col_offset = 5 if is_integrated else 4

    # ────────── 格式 ──────────
    bold_fmt = workbook.add_format({'bold': True, 'bg_color': '#F2F2F2'})
    section_fmt = workbook.add_format({'bold': True, 'font_size': 12, 'bg_color': '#D9E2F3'})
    highlight_fmt = workbook.add_format({'bold': True, 'bg_color': '#FFF2CC', 'num_format': '0.00'})

    # 设置列宽以防显示 ######
    ws_res.set_column(0, 0, 16)  # 声调类型
    ws_res.set_column(1, 1, 16)  # 字1_平均时长 / 全局最低最高Hz 所在列
    for k in range(max_syls):
        base_col = 1 + k * (num_points + 1)
        ws_res.set_column(base_col, base_col, 16)  # 时长列
        ws_res.set_column(base_col + 1, base_col + num_points, 10)  # T值或Hz值列

    # ══════════ Section A: 基频均值 (Hz) ══════════
    sec_a_header_row = 0
    ws_res.merge_range(sec_a_header_row, 0, sec_a_header_row,
                       max_syls * (num_points + 1),
                       '一、各声调平均基频 (Hz)  —— 由 AVERAGEIFS 公式自动从「数据」表计算', section_fmt)

    hz_header_row = 1
    ws_res.write(hz_header_row, 0, '声调类型', bold_fmt)
    for k in range(max_syls):
        base_col = 1 + k * (num_points + 1)
        ws_res.write(hz_header_row, base_col, f'字{k+1}_平均时长(s)', bold_fmt)
        for i in range(num_points):
            ws_res.write(hz_header_row, base_col + 1 + i, f'字{k+1}_T{i+1}均值(Hz)', bold_fmt)

    hz_data_start = 2  # 0-indexed row where Hz averages begin
    for g_idx, grp in enumerate(group_list):
        r = hz_data_start + g_idx
        ws_res.write(r, 0, grp)

        # 数据 Sheet 组别列 条件范围固定
        grp_range = f'{ds}!${grp_col_letter}$2:${grp_col_letter}${lr}'
        # 引用本行 A 列作为条件值（绝对列）
        criteria = f'$A{r + 1}'

        for k in range(max_syls):
            base_col = 1 + k * (num_points + 1)
            # ── 平均时长 ──
            dur_data_col = col_offset + k * (num_points + 1)  # 数据表中该音节时长列
            dc = xl_col_to_name(dur_data_col)
            val_range = f'{ds}!${dc}$2:${dc}${lr}'
            ws_res.write_formula(
                r, base_col,
                f'=IFERROR(AVERAGEIFS({val_range},{grp_range},{criteria},{val_range},">0"),"")')

            # ── 各测量点 Hz 均值 ──
            for i in range(num_points):
                hz_data_col = dur_data_col + 1 + i
                hc = xl_col_to_name(hz_data_col)
                hz_range = f'{ds}!${hc}$2:${hc}${lr}'
                ws_res.write_formula(
                    r, base_col + 1 + i,
                    f'=IFERROR(AVERAGEIFS({hz_range},{grp_range},{criteria},{hz_range},">0"),"")')

    # ══════════ Section B: 全局基频范围 ══════════
    gap1 = hz_data_start + num_groups  # blank row
    min_row = gap1 + 1
    max_row = gap1 + 2

    ws_res.write(min_row, 0, '全局最低Hz =', highlight_fmt)
    ws_res.write(max_row, 0, '全局最高Hz =', highlight_fmt)

    # 收集 Section A 中所有 Hz 均值单元格的范围
    hz_cell_ranges = []
    for k in range(max_syls):
        base_col = 1 + k * (num_points + 1)
        for i in range(num_points):
            col = base_col + 1 + i
            cl = xl_col_to_name(col)
            hz_cell_ranges.append(f'{cl}{hz_data_start + 1}:{cl}{hz_data_start + num_groups}')

    joined = ','.join(hz_cell_ranges)
    ws_res.write_formula(min_row, 1, f'=MIN({joined})', highlight_fmt)
    ws_res.write_formula(max_row, 1, f'=MAX({joined})', highlight_fmt)

    min_cell_abs = xl_rowcol_to_cell(min_row, 1, row_abs=True, col_abs=True)
    max_cell_abs = xl_rowcol_to_cell(max_row, 1, row_abs=True, col_abs=True)

    # ══════════ Section C: 五度标调 T 值 ══════════
    gap2 = max_row + 1
    sec_c_header_row = gap2 + 1
    ws_res.merge_range(sec_c_header_row, 0, sec_c_header_row,
                       max_syls * (num_points + 1),
                       '二、赵元任五度标调 T 值  —— 由上方 Hz 均值 + 全局极值经 LOG 公式换算', section_fmt)

    t_header_row = sec_c_header_row + 1
    ws_res.write(t_header_row, 0, '声调类型', bold_fmt)
    for k in range(max_syls):
        base_col = 1 + k * (num_points + 1)
        ws_res.write(t_header_row, base_col, f'字{k+1}_平均时长(s)', bold_fmt)
        for i in range(num_points):
            ws_res.write(t_header_row, base_col + 1 + i, f'字{k+1}_T{i+1}', bold_fmt)

    t_data_start = t_header_row + 1
    for g_idx, grp in enumerate(group_list):
        t_r = t_data_start + g_idx
        ws_res.write(t_r, 0, grp)

        for k in range(max_syls):
            base_col = 1 + k * (num_points + 1)
            # 平均时长直接引用 Section A 的对应单元格
            dur_ref = xl_rowcol_to_cell(hz_data_start + g_idx, base_col)
            ws_res.write_formula(t_r, base_col, f'={dur_ref}')

            for i in range(num_points):
                hz_ref = xl_rowcol_to_cell(hz_data_start + g_idx, base_col + 1 + i)
                # T = 5 * (LOG(Hz) - LOG(min)) / (LOG(max) - LOG(min))
                # 仅当 Hz>0 且 max>min 且 min>0 且均为有效数字时计算
                ws_res.write_formula(
                    t_r, base_col + 1 + i,
                    f'=IF(AND(ISNUMBER({hz_ref}),{hz_ref}>0,{max_cell_abs}>{min_cell_abs},{min_cell_abs}>0),'
                    f'5*(LOG({hz_ref})-LOG({min_cell_abs}))/(LOG({max_cell_abs})-LOG({min_cell_abs})),"")')

    res_row = t_data_start + num_groups
    return res_row, min_cell_abs, max_cell_abs


def build_five_point_chart(workbook, target_sheet, dict_data, avg_points_map,
                           num_points, max_syls, min_hz, max_hz,
                           insert_cell='A1', chart_title='各声调平均基频五度标调图（保留真实时长）'):
    """
    在 xlsxwriter Workbook 中创建赵元任五度标调散点连线图。

    复刻 VBA 宏 DrawFivePointPitchScale 的完整效果：
    - 图表类型：带标记的 XY 散点连线 (scatter with straight lines and markers)
    - X 轴：各声调的真实平均时长（秒），保留物理含义
    - Y 轴：0~5 五度标调 T 值
    - 自动按声调名称（阴平/阳平/上声/去声）上色
    - 隐藏辅助系列在 Y 轴左侧标注区间数字 1~5

    Parameters
    ----------
    workbook : xlsxwriter.Workbook
    target_sheet : xlsxwriter worksheet  —— 图表插入到的目标 Sheet
    dict_data : dict
        { grp_name: { 'syl_dur_sums': [...], 'syl_counts': [...],
                      'f0_sums': [[...]*max_syls], 'f0_counts': [[...]*max_syls] } }
    avg_points_map : dict
        { grp_name: [[avg_hz_per_point]*num_points  for each syl] }
    num_points : int
    max_syls : int
    min_hz, max_hz : float
    insert_cell : str
    chart_title : str
    """
    import math

    if max_hz <= min_hz or min_hz <= 0:
        return  # 无有效数据，无法绘制

    # ── 声调自动配色表 ──
    TONE_COLORS = {
        '阴平': '#0072BD',   # RGB(0,114,189)
        '阳平': '#D95319',   # RGB(217,83,25)
        '上声': '#77AC30',   # RGB(119,172,48)
        '去声': '#7E2F8E',   # RGB(126,47,142)
    }
    FALLBACK_COLORS = [
        '#0072BD', '#D95319', '#77AC30', '#7E2F8E',
        '#EDB120', '#4DBEEE', '#A2142F', '#72B7B2',
    ]

    def _tone_color(group_name, idx):
        """根据组名中的声调关键词返回颜色，无匹配则用轮换色。"""
        for key, color in TONE_COLORS.items():
            if key in group_name:
                return color
        return FALLBACK_COLORS[idx % len(FALLBACK_COLORS)]

    # ── 隐藏的图表数据 Sheet ──
    ws_cd = workbook.add_worksheet('五度图数据')
    ws_cd.hide()

    # 计算每组的平均时长和 T 值序列
    max_avg_dur = 0.0
    group_series = []  # [(name, [x_vals], [y_vals]), ...]

    for g_idx, (grp, st) in enumerate(dict_data.items()):
        cnt_dur = st['syl_counts'][0] if st['syl_counts'][0] > 0 else 1
        avg_dur = st['syl_dur_sums'][0] / cnt_dur
        if avg_dur > max_avg_dur:
            max_avg_dur = avg_dur

        x_vals = []
        y_vals = []
        has_valid = False
        syl_avgs = avg_points_map[grp][0] if grp in avg_points_map else [0.0] * num_points
        for i in range(num_points):
            x_vals.append((i) * (avg_dur / (num_points - 1)) if num_points > 1 else 0)
            avg_hz = syl_avgs[i]
            if avg_hz > 0 and max_hz > min_hz and min_hz > 0:
                t_val = 5 * (math.log(avg_hz) - math.log(min_hz)) / (math.log(max_hz) - math.log(min_hz))
                y_vals.append(round(t_val, 4))
                has_valid = True
            else:
                y_vals.append(None)

        if has_valid:
            group_series.append((grp, x_vals, y_vals))

    if not group_series:
        return

    # ── 写入隐藏 Sheet ──
    # 每组占 2 行 (X 行 + Y 行)，从第 0 行开始
    row = 0
    series_refs = []
    for name, x_vals, y_vals in group_series:
        ws_cd.write(row, 0, f'{name}_X')
        ws_cd.write(row + 1, 0, f'{name}_Y')
        for c, (xv, yv) in enumerate(zip(x_vals, y_vals)):
            ws_cd.write(row, c + 1, xv)
            if yv is not None:
                ws_cd.write(row + 1, c + 1, yv)
            else:
                ws_cd.write(row + 1, c + 1, '')
        series_refs.append((name, row, row + 1, len(x_vals)))
        row += 2

    # 额外写入隐形标签辅助系列 (Y 轴区间数字 1~5)
    dummy_row_x = row
    dummy_row_y = row + 1
    ws_cd.write(dummy_row_x, 0, 'label_x')
    ws_cd.write(dummy_row_y, 0, 'label_y')
    for i in range(5):
        x_pos = -0.03 * max_avg_dur if max_avg_dur > 0 else -0.01
        ws_cd.write(dummy_row_x, i + 1, x_pos)
        ws_cd.write(dummy_row_y, i + 1, i + 0.5)  # 0.5, 1.5, 2.5, 3.5, 4.5

    # ── 创建散点连线图 ──
    chart = workbook.add_chart({'type': 'scatter', 'subtype': 'straight_with_markers'})

    for idx, (name, rx, ry, count) in enumerate(series_refs):
        color = _tone_color(name, idx)
        chart.add_series({
            'name':       name,
            'categories': ['五度图数据', rx, 1, rx, count],
            'values':     ['五度图数据', ry, 1, ry, count],
            'line':       {'color': color, 'width': 2.5},
            'marker':     {
                'type': 'circle',
                'size': 6,
                'fill': {'color': color},
                'border': {'color': color},
            },
        })

    # 添加隐形辅助系列用于 Y 轴区间数字
    chart.add_series({
        'name':       'Y_Labels',
        'categories': ['五度图数据', dummy_row_x, 1, dummy_row_x, 5],
        'values':     ['五度图数据', dummy_row_y, 1, dummy_row_y, 5],
        'line':       {'none': True},
        'marker':     {'type': 'none'},
        'data_labels': {
            'value':      False,
            'category':   False,
            'series_name': False,
            'custom':     [
                {'value': '1'},
                {'value': '2'},
                {'value': '3'},
                {'value': '4'},
                {'value': '5'},
            ],
            'position':   'left',
            'font':       {'size': 12, 'bold': True, 'color': 'black'},
        },
    })

    # ── 坐标轴设定 ──
    x_min = -0.05 * max_avg_dur if max_avg_dur > 0 else 0
    x_max = max_avg_dur * 1.05 if max_avg_dur > 0 else 1

    chart.set_x_axis({
        'name': '平均时长 Time (s)',
        'name_font': {'name': 'Microsoft YaHei', 'size': 10},
        'num_font':  {'name': 'Arial', 'size': 9},
        'min':  x_min,
        'max':  x_max,
        'crossing': 0,
    })

    chart.set_y_axis({
        'name': '赵元任五度标调法',
        'name_font':  {'name': 'Microsoft YaHei', 'size': 10},
        'num_font':   {'name': 'Arial', 'size': 1, 'color': 'white'},  # 隐藏默认数字
        'min':  0,
        'max':  5,
        'major_unit':     1,
        'major_gridlines': {'visible': True, 'line': {'color': '#D0D0D0', 'width': 0.5}},
        'major_tick_mark': 'none',
    })

    # ── 图例：删除最后一个辅助标签系列的图例条目 ──
    chart.set_legend({
        'position': 'right',
        'font':     {'name': 'Microsoft YaHei', 'size': 9},
        'delete_series': [len(group_series)],  # 最后一个是 Y_Labels
    })

    chart.set_title({
        'name':      chart_title,
        'name_font': {'name': 'Microsoft YaHei', 'size': 14, 'bold': True},
    })

    chart.set_size({'width': 650, 'height': 450})
    chart.set_plotarea({'border': {'none': True}})

    target_sheet.insert_chart(insert_cell, chart)


import textgrid
 
def get_export_textgrid_for_item(item, max_time=None):
    if item.get('start') is None or item.get('end') is None: return None
    t_s, t_e = item['start'], item['end']
 
    label = item.get('label', '')
    inner_splits = item.get('inner_splits', [])
    syls = split_into_syllables(label)
    is_word_mode = len(syls) > 1
 
    tg = textgrid.TextGrid(maxTime=max_time if max_time else t_e)
 
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
 
    if is_word_mode:
        char_tier = textgrid.IntervalTier(name="chars", minTime=0.0, maxTime=max_time if max_time else t_e)
        if t_s > 0:
            char_tier.add(0.0, t_s, "")
 
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
        tg.append(char_tier)

    return tg
