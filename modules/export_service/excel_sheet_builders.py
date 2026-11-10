"""
modules.export_service.excel_sheet_builders - Excel 报表构建与样式管理模块
包含 ExcelStyleManager、自适应列宽、原始逐点基频表、带公式声调/共振峰分析表、五度图及安全 Sheet 命名。
"""

import re
import math
from typing import List, Dict, Optional, Any, Tuple
import numpy as np

from ..core.acoustic_math import hz_to_t_value
from ..core.text_io import split_into_syllables
from .item_sampling import get_item_syllable_bounds

_INVALID_EXCEL_SHEET_CHARS_RE = re.compile(r'[:\\/?*\[\]\x00-\x1f\x7f]')

class ExcelStyleManager:
    """
    统一的 Excel 样式管理器 (基于 xlsxwriter)
    统一工作簿全域字体为 Microsoft YaHei、规范浅灰边框 (#CBD5E1) 及标准化数值/对齐格式。
    """
    FONT_FAMILY = "Microsoft YaHei"
    BORDER_COLOR = "#CBD5E1"

    def __init__(self, workbook):
        self.workbook = workbook
        self._init_formats()

    def _init_formats(self):
        # 表头格式 (10.5pt, 粗体, 优雅灰蓝底色, 细边框, 居中)
        self.header_fmt = self.workbook.add_format({
            'font_name': self.FONT_FAMILY,
            'font_size': 10.5,
            'bold': True,
            'bg_color': '#F1F5F9',
            'border': 1,
            'border_color': self.BORDER_COLOR,
            'align': 'center',
            'valign': 'vcenter',
        })
        # 章节大标题格式 (11pt, 粗体, 雅致浅蓝背景)
        self.section_fmt = self.workbook.add_format({
            'font_name': self.FONT_FAMILY,
            'font_size': 11,
            'bold': True,
            'bg_color': '#D9E2F3',
            'border': 1,
            'border_color': self.BORDER_COLOR,
            'valign': 'vcenter',
        })
        # 重点突出格式 (黄色背景, 用于极值等)
        self.highlight_fmt = self.workbook.add_format({
            'font_name': self.FONT_FAMILY,
            'font_size': 10,
            'bold': True,
            'bg_color': '#FFF2CC',
            'border': 1,
            'border_color': self.BORDER_COLOR,
            'align': 'right',
            'valign': 'vcenter',
            'num_format': '0.00',
        })
        # 基础文本格式 (左对齐)
        self.text_left = self.workbook.add_format({
            'font_name': self.FONT_FAMILY,
            'font_size': 10,
            'border': 1,
            'border_color': self.BORDER_COLOR,
            'align': 'left',
            'valign': 'vcenter',
        })
        # 基础文本格式 (居中, 常用于编号、状态、单字、发音人等)
        self.text_center = self.workbook.add_format({
            'font_name': self.FONT_FAMILY,
            'font_size': 10,
            'border': 1,
            'border_color': self.BORDER_COLOR,
            'align': 'center',
            'valign': 'vcenter',
        })
        # 基础文本格式 (右对齐)
        self.text_right = self.workbook.add_format({
            'font_name': self.FONT_FAMILY,
            'font_size': 10,
            'border': 1,
            'border_color': self.BORDER_COLOR,
            'align': 'right',
            'valign': 'vcenter',
        })
        # 时长格式 (3位小数 0.000)
        self.duration_fmt = self.workbook.add_format({
            'font_name': self.FONT_FAMILY,
            'font_size': 10,
            'border': 1,
            'border_color': self.BORDER_COLOR,
            'align': 'right',
            'valign': 'vcenter',
            'num_format': '0.000',
        })
        self.time_fmt = self.duration_fmt
        # 五度值 / T值 / 两位小数格式 (0.00)
        self.t_val_fmt = self.workbook.add_format({
            'font_name': self.FONT_FAMILY,
            'font_size': 10,
            'border': 1,
            'border_color': self.BORDER_COLOR,
            'align': 'right',
            'valign': 'vcenter',
            'num_format': '0.00',
        })
        self.decimal2_fmt = self.t_val_fmt
        # 赫兹值 / 一位小数格式 (0.0)
        self.hz_fmt = self.workbook.add_format({
            'font_name': self.FONT_FAMILY,
            'font_size': 10,
            'border': 1,
            'border_color': self.BORDER_COLOR,
            'align': 'right',
            'valign': 'vcenter',
            'num_format': '0.0',
        })
        self.decimal1_fmt = self.hz_fmt
        # 整数格式 (0)
        self.int_fmt = self.workbook.add_format({
            'font_name': self.FONT_FAMILY,
            'font_size': 10,
            'border': 1,
            'border_color': self.BORDER_COLOR,
            'align': 'right',
            'valign': 'vcenter',
            'num_format': '0',
        })
        self.integer_fmt = self.int_fmt
        # 千分位整数格式 (#,##0)
        self.thousands_fmt = self.workbook.add_format({
            'font_name': self.FONT_FAMILY,
            'font_size': 10,
            'border': 1,
            'border_color': self.BORDER_COLOR,
            'align': 'right',
            'valign': 'vcenter',
            'num_format': '#,##0',
        })
        self.size_fmt = self.thousands_fmt
        # 4位小数格式 (0.0000)
        self.decimal4_fmt = self.workbook.add_format({
            'font_name': self.FONT_FAMILY,
            'font_size': 10,
            'border': 1,
            'border_color': self.BORDER_COLOR,
            'align': 'right',
            'valign': 'vcenter',
            'num_format': '0.0000',
        })
        # 6位小数格式 (0.000000)
        self.decimal6_fmt = self.workbook.add_format({
            'font_name': self.FONT_FAMILY,
            'font_size': 10,
            'border': 1,
            'border_color': self.BORDER_COLOR,
            'align': 'right',
            'valign': 'vcenter',
            'num_format': '0.000000',
        })


def compute_visual_width(text: Any) -> float:
    """
    计算文本在 Excel 中的视觉字符宽度：
    中文字符 (CJK) 计 2.0 字符宽，英文字母、数字和半角符号计 1.0 字符宽。
    """
    if text is None:
        return 0.0
    s = str(text)
    if not s:
        return 0.0
    width = 0.0
    for char in s:
        code = ord(char)
        if ('\u4e00' <= char <= '\u9fff' or
            '\u3400' <= char <= '\u4dbf' or
            '\uf900' <= char <= '\ufaff' or
            0xff00 <= code <= 0xffef):
            width += 2.0
        elif code > 127:
            width += 1.8
        else:
            width += 1.0
    return width


def autofit_worksheet_columns(ws, headers: List[str], rows: Optional[List[List[Any]]] = None,
                              extra_padding: float = 4.0, min_width: float = 10.0, max_width: float = 60.0):
    """
    按表头与数据的中英文字符视觉宽度自适应设置工作表列宽，
    并为表头额外预留下拉筛选箭头与边距空间。
    """
    num_cols = len(headers)
    col_widths = [compute_visual_width(h) + extra_padding for h in headers]

    if rows:
        sample_rows = rows[:100]  # 取前 100 行样本提升性能
        for r in sample_rows:
            for c_idx, val in enumerate(r):
                if c_idx < num_cols and val is not None and val != "":
                    w = compute_visual_width(val) + 2.0
                    if w > col_widths[c_idx]:
                        col_widths[c_idx] = w

    for c_idx, w in enumerate(col_widths):
        clamped_w = max(min_width, min(w, max_width))
        ws.set_column(c_idx, c_idx, round(clamped_w, 1))


def get_pitch_arrays_for_item(item: Dict[str, Any]) -> Tuple[Optional[np.ndarray], Optional[np.ndarray]]:
    """提取词条的基频时间点与频率数组。"""
    if item.get('pitch_data'):
        p_xs = item['pitch_data'].get('xs')
        p_freqs = item['pitch_data'].get('freqs')
        if p_xs is not None and p_freqs is not None:
            return np.asarray(p_xs), np.asarray(p_freqs)
    if item.get('pitch'):
        pitch = item['pitch']
        try:
            p_xs = np.asarray(pitch.xs())
            p_freqs = np.asarray(pitch.selected_array['frequency'])
            if p_xs.ndim == 1 and p_freqs.ndim == 1 and len(p_xs) == len(p_freqs):
                return p_xs, p_freqs
        except Exception:
            return None, None
    return None, None


def write_raw_pitch_sheet(workbook, rows: List[Dict[str, Any]], style_manager=None, include_speaker: bool = False):
    """写入逐点 Hz 原始基频数据到 '原始基频数据' Sheet。"""
    ws_raw = workbook.add_worksheet("原始基频数据")
    if style_manager is None:
        style_manager = ExcelStyleManager(workbook)
    style = style_manager

    headers = []
    if include_speaker:
        headers.append("发音人")
    headers.extend([
        "组别", "编号", "词语", "音节序号", "音节",
        "绝对时间(s)", "字内相对时间(s)", "基频Hz", "状态"
    ])
    for col, header in enumerate(headers):
        ws_raw.write(0, col, header, style.header_fmt)

    row_idx = 1
    sample_rows = []
    for entry in rows:
        item = entry.get('item') or entry.get('raw_item')
        if not item:
            continue
        if item.get('start') is None:
            continue

        p_xs, p_freqs = get_pitch_arrays_for_item(item)
        if p_xs is None or p_freqs is None:
            continue

        label = item.get('label', '')
        syls = split_into_syllables(label)
        if not syls and label:
            syls = [label]
        bounds = get_item_syllable_bounds(item)
        if not bounds:
            continue

        for syl_idx, (c_s, c_e) in enumerate(bounds, start=1):
            if c_e <= c_s:
                continue
            char = syls[syl_idx - 1] if syl_idx - 1 < len(syls) else ""
            mask = (p_xs >= c_s) & (p_xs <= c_e)
            indices = np.where(mask)[0]
            for p_idx in indices:
                t = float(p_xs[p_idx])
                hz = float(p_freqs[p_idx]) if np.isfinite(p_freqs[p_idx]) else 0.0
                status = "有效" if hz > 0 else "无声/已擦除"

                col = 0
                if include_speaker:
                    ws_raw.write(row_idx, col, entry.get('speaker', ''), style.text_center)
                    col += 1
                ws_raw.write(row_idx, col, entry.get('group', ''), style.text_center)
                col += 1
                ws_raw.write(row_idx, col, entry.get('index', ''), style.int_fmt)
                col += 1
                ws_raw.write(row_idx, col, item.get('label', ''), style.text_left)
                col += 1
                ws_raw.write(row_idx, col, syl_idx, style.int_fmt)
                col += 1
                ws_raw.write(row_idx, col, char, style.text_center)
                col += 1
                ws_raw.write(row_idx, col, round(t, 6), style.decimal6_fmt)
                col += 1
                ws_raw.write(row_idx, col, round(t - c_s, 6), style.decimal6_fmt)
                col += 1
                ws_raw.write(row_idx, col, round(hz, 1) if hz > 0 else 0.0, style.hz_fmt)
                col += 1
                ws_raw.write(row_idx, col, status, style.text_center)

                if row_idx <= 100:
                    sample_vals = []
                    if include_speaker:
                        sample_vals.append(entry.get('speaker', ''))
                    sample_vals.extend([
                        entry.get('group', ''), entry.get('index', ''), item.get('label', ''),
                        syl_idx, char, round(t, 6), round(t - c_s, 6), round(hz, 1), status
                    ])
                    sample_rows.append(sample_vals)

                row_idx += 1

    ws_raw.freeze_panes(1, 0)
    ws_raw.autofilter(0, 0, max(row_idx - 1, 1), len(headers) - 1)
    autofit_worksheet_columns(ws_raw, headers, sample_rows)
    return ws_raw


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

    # ────────── 统一格式管理器 ──────────
    style = ExcelStyleManager(workbook)

    # ══════════ Section A: 基频均值 (Hz) ══════════
    sec_a_header_row = 0
    ws_res.merge_range(sec_a_header_row, 0, sec_a_header_row,
                       max_syls * (num_points + 1),
                       '一、各组别平均基频 (Hz)  —— 由 AVERAGEIFS 公式自动从「数据」表计算', style.section_fmt)

    hz_header_row = 1
    ws_res.write(hz_header_row, 0, '组别', style.header_fmt)
    analysis_headers = ['组别']
    for k in range(max_syls):
        base_col = 1 + k * (num_points + 1)
        dur_hdr = f'字{k+1}_平均时长(s)'
        ws_res.write(hz_header_row, base_col, dur_hdr, style.header_fmt)
        analysis_headers.append(dur_hdr)
        for i in range(num_points):
            hz_hdr = f'字{k+1}_P{i+1}均值(Hz)'
            ws_res.write(hz_header_row, base_col + 1 + i, hz_hdr, style.header_fmt)
            analysis_headers.append(hz_hdr)

    hz_data_start = 2  # 0-indexed row where Hz averages begin
    for g_idx, grp in enumerate(group_list):
        r = hz_data_start + g_idx
        ws_res.write(r, 0, grp, style.text_center)

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
                f'=IFERROR(AVERAGEIFS({val_range},{grp_range},{criteria},{val_range},">0"),"")',
                style.duration_fmt
            )

            # ── 各测量点 Hz 均值 ──
            for i in range(num_points):
                hz_data_col = dur_data_col + 1 + i
                hc = xl_col_to_name(hz_data_col)
                hz_range = f'{ds}!${hc}$2:${hc}${lr}'
                ws_res.write_formula(
                    r, base_col + 1 + i,
                    f'=IFERROR(AVERAGEIFS({hz_range},{grp_range},{criteria},{hz_range},">0"),"")',
                    style.hz_fmt
                )

    # ══════════ Section B: 全局基频范围 ══════════
    gap1 = hz_data_start + num_groups  # blank row
    min_row = gap1 + 1
    max_row = gap1 + 2

    ws_res.write(min_row, 0, '全局最低Hz =', style.highlight_fmt)
    ws_res.write(max_row, 0, '全局最高Hz =', style.highlight_fmt)

    # 收集 Section A 中所有 Hz 均值单元格的范围
    hz_cell_ranges = []
    for k in range(max_syls):
        base_col = 1 + k * (num_points + 1)
        for i in range(num_points):
            col = base_col + 1 + i
            cl = xl_col_to_name(col)
            hz_cell_ranges.append(f'{cl}{hz_data_start + 1}:{cl}{hz_data_start + num_groups}')

    joined = ','.join(hz_cell_ranges)
    ws_res.write_formula(min_row, 1, f'=MIN({joined})', style.highlight_fmt)
    ws_res.write_formula(max_row, 1, f'=MAX({joined})', style.highlight_fmt)

    min_cell_abs = xl_rowcol_to_cell(min_row, 1, row_abs=True, col_abs=True)
    max_cell_abs = xl_rowcol_to_cell(max_row, 1, row_abs=True, col_abs=True)

    # ══════════ Section C: 五度标调 T 值 ══════════
    gap2 = max_row + 1
    sec_c_header_row = gap2 + 1
    ws_res.merge_range(sec_c_header_row, 0, sec_c_header_row,
                       max_syls * (num_points + 1),
                       '二、赵元任五度标调 T 值  —— 由上方 Hz 均值 + 全局极值经 LOG 公式换算', style.section_fmt)

    t_header_row = sec_c_header_row + 1
    ws_res.write(t_header_row, 0, '组别', style.header_fmt)
    for k in range(max_syls):
        base_col = 1 + k * (num_points + 1)
        ws_res.write(t_header_row, base_col, f'字{k+1}_平均时长(s)', style.header_fmt)
        for i in range(num_points):
            ws_res.write(t_header_row, base_col + 1 + i, f'字{k+1}_T{i+1}', style.header_fmt)

    t_data_start = t_header_row + 1
    for g_idx, grp in enumerate(group_list):
        t_r = t_data_start + g_idx
        ws_res.write(t_r, 0, grp, style.text_center)

        for k in range(max_syls):
            base_col = 1 + k * (num_points + 1)
            # 平均时长直接引用 Section A 的对应单元格
            dur_ref = xl_rowcol_to_cell(hz_data_start + g_idx, base_col)
            ws_res.write_formula(t_r, base_col, f'={dur_ref}', style.duration_fmt)

            for i in range(num_points):
                hz_ref = xl_rowcol_to_cell(hz_data_start + g_idx, base_col + 1 + i)
                # T = 5 * (LOG(Hz) - LOG(min)) / (LOG(max) - LOG(min))
                # 仅当 Hz>0 且 max>min 且 min>0 且均为有效数字时计算
                ws_res.write_formula(
                    t_r, base_col + 1 + i,
                    f'=IF(AND(ISNUMBER({hz_ref}),{hz_ref}>0,{max_cell_abs}>{min_cell_abs},{min_cell_abs}>0),'
                    f'5*(LOG({hz_ref})-LOG({min_cell_abs}))/(LOG({max_cell_abs})-LOG({min_cell_abs})),"")',
                    style.t_val_fmt
                )

    res_row = t_data_start + num_groups

    # 统一设置自适应列宽与冻结表头
    autofit_worksheet_columns(ws_res, analysis_headers, extra_padding=4.0, min_width=12.0)
    ws_res.freeze_panes(2, 0)
    return res_row, min_cell_abs, max_cell_abs


def write_formant_analysis_sheet_with_formulas(
        workbook, ws_res, group_char_pairs, last_data_row,
        data_sheet_name='提取数据'):
    """
    在分析图表 Sheet 中写入 Excel 公式，引用提取数据 Sheet 的 F1/F2 值进行计算。

    布局:
      Section A (F1/F2 统计): AVERAGEIFS / COUNTIFS 公式，按 (组别, 单字) 计算均值与有效帧数
      Section B (F1/F2 中位数): MEDIAN(IF(...)) 数组公式
      Section C: 元音空间散点图 (Excel 原生图表，引用上方数据)

    Parameters
    ----------
    workbook : xlsxwriter.Workbook
    ws_res : xlsxwriter worksheet
    group_char_pairs : list of (group_name, char) tuples — 有序的 (组别, 单字) 唯一列表
    last_data_row : int — 提取数据 Sheet 中最后一行数据的 0-indexed 行号
    data_sheet_name : str

    Returns
    -------
    res_row : int — 数据区之后的下一行（用于在下方插入其他内容）
    """
    from xlsxwriter.utility import xl_rowcol_to_cell

    ds = data_sheet_name
    lr = last_data_row + 1  # 1-indexed last data row

    CHART_COLORS = [
        '#2563EB', '#DC2626', '#16A34A', '#9333EA',
        '#EA580C', '#0891B2', '#CA8A04', '#6366F1',
    ]

    # ── 统一样式管理器 ──
    style = ExcelStyleManager(workbook)

    # 提取数据 Sheet 列布局 (0-indexed):
    #   A=发音人  B=组别  C=编号  D=词语  E=音节序号
    #   F=音节(单字)  G=时间点序号  H=时间(s)  I=F1(Hz)  J=F2(Hz)
    grp_range = f"'{ds}'!$B$2:$B${lr}"
    char_range = f"'{ds}'!$F$2:$F${lr}"
    f1_range = f"'{ds}'!$I$2:$I${lr}"
    f2_range = f"'{ds}'!$J$2:$J${lr}"

    # ══════════ Section A: F1/F2 均值 + 中位数 + 有效帧数 ══════════
    sec_a_row = 0
    ws_res.merge_range(
        sec_a_row, 0, sec_a_row, 6,
        '各组别/音节 F1·F2 统计  —— 由 AVERAGEIFS / COUNTIFS / MEDIAN 公式自动从「提取数据」表计算',
        style.section_fmt,
    )

    header_row = 1
    headers = [
        '组别', '音节',
        'F1_均值(Hz)', 'F2_均值(Hz)',
        'F1_中位数(Hz)', 'F2_中位数(Hz)',
        '有效帧数',
    ]
    for col, h in enumerate(headers):
        ws_res.write(header_row, col, h, style.header_fmt)

    data_start = 2  # first formula-data row (0-indexed)

    for idx, (grp, char) in enumerate(group_char_pairs):
        r = data_start + idx
        ws_res.write(r, 0, grp, style.text_center)
        ws_res.write(r, 1, char, style.text_center)

        grp_ref = f'$A{r + 1}'
        char_ref = f'$B{r + 1}'

        # ── F1 均值 ──
        ws_res.write_formula(
            r, 2,
            f'=IFERROR(AVERAGEIFS({f1_range},{grp_range},{grp_ref},'
            f'{char_range},{char_ref},{f1_range},">0",{f2_range},">0"),"")',
            style.hz_fmt
        )

        # ── F2 均值 ──
        ws_res.write_formula(
            r, 3,
            f'=IFERROR(AVERAGEIFS({f2_range},{grp_range},{grp_ref},'
            f'{char_range},{char_ref},{f1_range},">0",{f2_range},">0"),"")',
            style.hz_fmt
        )

        # ── F1 中位数 (数组公式) ──
        median_f1 = (
            f'=IFERROR(MEDIAN(IF(({grp_range}={grp_ref})*({char_range}={char_ref})'
            f'*({f1_range}>0)*({f2_range}>0),{f1_range})),"")'
        )
        ws_res.write_array_formula(r, 4, r, 4, '{' + median_f1 + '}', style.hz_fmt)

        # ── F2 中位数 (数组公式) ──
        median_f2 = (
            f'=IFERROR(MEDIAN(IF(({grp_range}={grp_ref})*({char_range}={char_ref})'
            f'*({f1_range}>0)*({f2_range}>0),{f2_range})),"")'
        )
        ws_res.write_array_formula(r, 5, r, 5, '{' + median_f2 + '}', style.hz_fmt)

        # ── 有效帧数 ──
        ws_res.write_formula(
            r, 6,
            f'=COUNTIFS({grp_range},{grp_ref},{char_range},{char_ref},'
            f'{f1_range},">0",{f2_range},">0")',
            style.int_fmt
        )

    res_row = data_start + len(group_char_pairs)

    # 统一自适应列宽与冻结表头
    autofit_worksheet_columns(ws_res, headers, extra_padding=4.0, min_width=12.0)
    ws_res.freeze_panes(2, 0)

    # ══════════ Chart: 元音空间散点图 ══════════
    if group_char_pairs:
        ws_name = ws_res.name

        # 按组别分组，确定每组的起止行
        groups_ordered = []
        group_rows = {}
        for idx, (grp, _char) in enumerate(group_char_pairs):
            r = data_start + idx
            if grp not in group_rows:
                group_rows[grp] = []
                groups_ordered.append(grp)
            group_rows[grp].append((r, idx))

        chart = workbook.add_chart({'type': 'scatter'})

        for g_idx, grp in enumerate(groups_ordered):
            rows_info = group_rows[grp]
            first_r = rows_info[0][0]
            last_r = rows_info[-1][0]
            color = CHART_COLORS[g_idx % len(CHART_COLORS)]

            # 构建自定义数据标签（显示单字）
            custom_labels = []
            for _r, pair_idx in rows_info:
                custom_labels.append({'value': group_char_pairs[pair_idx][1]})

            chart.add_series({
                'name':       grp,
                'categories': [ws_name, first_r, 3, last_r, 3],  # F2 均值 (col 3)
                'values':     [ws_name, first_r, 2, last_r, 2],  # F1 均值 (col 2)
                'line':       {'none': True},
                'marker':     {
                    'type': 'circle', 'size': 8,
                    'fill': {'color': color},
                    'border': {'color': color},
                },
                'data_labels': {
                    'value': False,
                    'custom': custom_labels,
                    'font': {'size': 9, 'bold': True},
                },
            })

        chart.set_x_axis({
            'name':      'F2 (Hz)',
            'name_font': {'name': 'Microsoft YaHei', 'size': 10},
            'num_font':  {'name': 'Arial', 'size': 9},
            'reverse':   True,
        })
        chart.set_y_axis({
            'name':      'F1 (Hz)',
            'name_font': {'name': 'Microsoft YaHei', 'size': 10},
            'num_font':  {'name': 'Arial', 'size': 9},
            'reverse':   True,
        })
        chart.set_title({
            'name':      '元音空间散点图 (F1 × F2)',
            'name_font': {'name': 'Microsoft YaHei', 'size': 14, 'bold': True},
        })
        chart.set_legend({
            'position': 'right',
            'font':     {'name': 'Microsoft YaHei', 'size': 9},
        })
        chart.set_size({'width': 650, 'height': 450})
        chart.set_plotarea({'border': {'none': True}})

        ws_res.insert_chart(f'A{res_row + 2}', chart)

    return res_row


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
        x_vals = []
        y_vals = []
        has_valid = False
        acc_dur = 0.0
        grp_syl_avgs = avg_points_map.get(grp, [])
        n_syls_grp = min(max_syls, len(st.get('syl_counts', [])))
        for k in range(max(1, n_syls_grp)):
            cnt_dur = st['syl_counts'][k] if k < len(st['syl_counts']) and st['syl_counts'][k] > 0 else 0
            if cnt_dur <= 0:
                continue
            avg_dur = st['syl_dur_sums'][k] / cnt_dur
            syl_avgs = grp_syl_avgs[k] if k < len(grp_syl_avgs) else [0.0] * num_points
            for i in range(num_points):
                x_pt = acc_dur + ((i) * (avg_dur / (num_points - 1)) if num_points > 1 else 0.0)
                x_vals.append(round(x_pt, 6))
                avg_hz = syl_avgs[i] if i < len(syl_avgs) else 0.0
                t_val = hz_to_t_value(avg_hz, min_hz, max_hz, clip=False)
                if t_val is not None:
                    y_vals.append(round(float(t_val), 4))
                    has_valid = True
                else:
                    y_vals.append(None)
            acc_dur += avg_dur

        if acc_dur > max_avg_dur:
            max_avg_dur = acc_dur

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


def make_safe_excel_sheet_name(base_name: str, suffix: str = "", used_names: set = None) -> str:
    """
    清洗并生成合法的 Excel 工作表（Sheet）名称，确保唯一且不超过 31 个字符。

    规则：
    1. 替换 Excel 禁用的 7 个字符 (: \\ / ? * [ ]) 以及 ASCII 控制字符（< 32）为下划线 '_'。
    2. 移除首尾的单引号 '\''。
    3. 为空时兜底为 'Sheet'。
    4. 结合 suffix 严格限制总长度不超过 31 个字符。
    5. 若提供 used_names 集合，进行大小写不敏感判重；冲突时通过截断前缀追加 '_2', '_3' 等后缀保证唯一，
       并自动将生成的新名称的小写形式添加到 used_names 中。
    """
    raw_base = "" if base_name is None else str(base_name).strip()
    raw_suffix = "" if suffix is None else str(suffix).strip()

    clean_base = re.sub(r'[:\\/?*\[\]\x00-\x1f\x7f]', '_', raw_base)
    clean_suffix = re.sub(r'[:\\/?*\[\]\x00-\x1f\x7f]', '_', raw_suffix)

    clean_base = clean_base.strip("'")
    clean_suffix = clean_suffix.strip("'")

    if not clean_base:
        clean_base = "Sheet"

    max_len = 31

    def combine(base_part: str, num_suffix: str = "") -> str:
        fixed_tail = f"{num_suffix}{clean_suffix}"
        avail = max(1, max_len - len(fixed_tail))
        truncated_base = base_part[:avail].strip("'")
        if not truncated_base:
            truncated_base = "_"
        cand = f"{truncated_base}{fixed_tail}".strip("'")
        if not cand:
            cand = "Sheet"
        return cand[:max_len]

    candidate = combine(clean_base, "")

    if used_names is not None:
        used_lowers = {str(n).lower() for n in used_names}
        counter = 2
        while candidate.lower() in used_lowers:
            candidate = combine(clean_base, f"_{counter}")
            counter += 1
        used_names.add(candidate.lower())

    return candidate
