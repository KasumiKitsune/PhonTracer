"""
modules.export_service.unified_exporter - 统一无头导出与采样服务
提供 cli.py 与 modules/project_tree.py 共用的无头采样与导出核心静态/实例方法。
解耦 CLI 日志与 GUI 弹窗交互（通过 ExportCallbacks）。
顶层严禁导入 tkinter 或 customtkinter。
"""

import math
from dataclasses import dataclass
from typing import Callable, Optional, Any, Dict, List, Tuple
import numpy as np

from ..core.text_io import split_into_syllables
from ..core.acoustic_math import hz_to_t_value
from .item_sampling import (
    get_export_text_for_item,
    get_item_syllable_bounds,
    sample_formant_points_by_bounds,
    sample_formant_points_by_bounds_extended,
    get_formant_export_text_for_item,
)
from .excel_sheet_builders import (
    ExcelStyleManager,
    compute_visual_width,
    autofit_worksheet_columns,
    get_pitch_arrays_for_item as excel_get_pitch_arrays_for_item,
    write_raw_pitch_sheet,
    write_analysis_sheet_with_formulas,
    write_formant_analysis_sheet_with_formulas,
    build_five_point_chart,
    make_safe_excel_sheet_name,
)
from .textgrid_io import (
    get_export_textgrid_for_item,
    extract_wordlist_from_textgrid,
)
from .praat_collection_exporter import (
    clean_praat_object_name,
    write_praat_collection,
    build_combined_textgrid_from_items,
    build_collection_items_for_audio_group,
    build_single_item_collection_objects,
    build_speaker_collection_objects,
    export_speaker_to_praat_collection,
)


@dataclass
class ExportCallbacks:
    """导出生命周期与 UI 交互解耦回调接口。"""
    on_progress: Optional[Callable[[float, str], None]] = None
    on_message: Optional[Callable[[str, str], None]] = None
    on_warning: Optional[Callable[[str, str], bool]] = None
    on_error: Optional[Callable[[str, str], None]] = None
    ask_confirmation: Optional[Callable[[str, str], bool]] = None

    def notify_progress(self, ratio: float, text: str = "") -> None:
        if self.on_progress:
            self.on_progress(ratio, text)

    def warn(self, title: str, msg: str) -> bool:
        if self.on_warning:
            return bool(self.on_warning(title, msg))
        return True

    def message(self, title: str, msg: str) -> None:
        if self.on_message:
            self.on_message(title, msg)

    def error(self, title: str, msg: str) -> None:
        if self.on_error:
            self.on_error(title, msg)

    def confirm(self, title: str, msg: str) -> bool:
        if self.ask_confirmation:
            return bool(self.ask_confirmation(title, msg))
        return True


class UnifiedExportService:
    """
    统一导出服务。
    汇聚无头环境（CLI / 批处理）与 GUI 工程树（ProjectTreePanel）复用的采样、分析、表格与 TextGrid 导出核心能力。
    全部基础算法同时支持静态调用与实例调用。
    """

    def __init__(self, callbacks: Optional[ExportCallbacks] = None):
        self.callbacks = callbacks or ExportCallbacks()

    @staticmethod
    def get_items_by_group_for_dict(items_dict: Dict[str, Any]) -> List[Tuple[str, List[str]]]:
        """按 group 属性对词条分组，排除标记为 is_excluded 的词条。"""
        groups: Dict[str, List[str]] = {}
        for k, v in items_dict.items():
            if isinstance(v, dict) and v.get('is_excluded', False):
                continue
            g = v.get('group', '导入内容') if isinstance(v, dict) else '导入内容'
            if g not in groups:
                groups[g] = []
            groups[g].append(k)
        return [(g, groups[g]) for g in groups]

    @staticmethod
    def get_pitch_arrays_for_item(item: Dict[str, Any]) -> Tuple[Optional[np.ndarray], Optional[np.ndarray]]:
        """从 item 中提取时间数组 xs 与 F0 频率数组 freqs。"""
        if not item:
            return None, None
        if item.get('pitch_data'):
            p_xs = item['pitch_data'].get('xs')
            p_freqs = item['pitch_data'].get('freqs')
            if p_xs is None or p_freqs is None:
                return None, None
            return np.asarray(p_xs), np.asarray(p_freqs)
        if item.get('pitch'):
            pitch = item['pitch']
            try:
                p_xs = np.asarray(pitch.xs())
                p_freqs = np.asarray(pitch.selected_array['frequency'])
            except (TypeError, KeyError, AttributeError):
                return None, None
            if p_xs.ndim != 1 or p_freqs.ndim != 1 or len(p_xs) != len(p_freqs):
                return None, None
            return p_xs, p_freqs
        return None, None

    @staticmethod
    def get_syllables_and_bounds(item: Dict[str, Any]) -> Tuple[List[str], List[List[float]]]:
        """返回与当前编辑边界一致的音节及边界列表，优先使用 chars_bounds。"""
        t_s, t_e = item.get('start'), item.get('end')
        if t_s is None or t_e is None or t_e <= t_s:
            return [], []

        label = item.get('label', '')
        syls = split_into_syllables(label)
        if not syls and label:
            syls = [label]

        chars_bounds = item.get('chars_bounds', [])
        if chars_bounds and len(chars_bounds) == len(syls):
            return syls, [[float(s), float(e)] for s, e in chars_bounds]

        inner_splits = item.get('inner_splits', [])
        splits = [t_s] + [s for s in inner_splits if t_s < s < t_e] + [t_e]
        if len(syls) > 1 and len(splits) != len(syls) + 1:
            splits = np.linspace(t_s, t_e, len(syls) + 1).tolist()
        elif len(syls) <= 1:
            splits = [t_s, t_e]
            if not syls:
                syls = [label]

        return syls, [[splits[i], splits[i + 1]] for i in range(len(splits) - 1)]

    @staticmethod
    def extract_kde_contour(
        p_xs: np.ndarray,
        p_freqs: np.ndarray,
        c_s: float,
        c_e: float,
        n_dense: int = 50
    ) -> Optional[np.ndarray]:
        """提取 KDE 用的连续 F0 轮廓；橡皮擦/无声缺口保留为 NaN。"""
        if c_e <= c_s:
            return None

        valid_idx = np.where((p_xs >= c_s) & (p_xs <= c_e) & np.isfinite(p_freqs) & (p_freqs > 0))[0]
        if len(valid_idx) < 3:
            return None

        seg_xs = np.asarray(p_xs[valid_idx], dtype=float)
        seg_ys = np.asarray(p_freqs[valid_idx], dtype=float)
        order = np.argsort(seg_xs)
        seg_xs = seg_xs[order]
        seg_ys = seg_ys[order]

        v_s, v_e = seg_xs[0], seg_xs[-1]
        if v_e <= v_s:
            return None

        gap_threshold = 0.025
        smoothed = seg_ys.copy()
        try:
            from scipy.signal import savgol_filter
            breaks = np.where(np.diff(seg_xs) > gap_threshold)[0] + 1
            run_ranges = np.split(np.arange(len(seg_xs)), breaks)
            for run in run_ranges:
                run_len = len(run)
                if run_len < 5:
                    continue
                win = min(9, run_len if run_len % 2 == 1 else run_len - 1)
                if win >= 5:
                    smoothed[run] = savgol_filter(seg_ys[run], win, 2)
        except Exception:
            pass

        dense_times = np.linspace(v_s, v_e, n_dense)
        y_dense = np.interp(dense_times, seg_xs, smoothed)

        nearest_right = np.searchsorted(seg_xs, dense_times, side='left')
        nearest_left = np.clip(nearest_right - 1, 0, len(seg_xs) - 1)
        nearest_right = np.clip(nearest_right, 0, len(seg_xs) - 1)
        nearest_dist = np.minimum(
            np.abs(dense_times - seg_xs[nearest_left]),
            np.abs(dense_times - seg_xs[nearest_right])
        )
        y_dense[nearest_dist > gap_threshold] = np.nan
        return y_dense

    @staticmethod
    def extract_syl_data(
        item: Dict[str, Any],
        num_points: int,
        pitch_floor: float = 75.0,
        pitch_ceiling: float = 600.0,
        voicing_threshold: float = 0.25,
    ) -> Tuple[float, List[Tuple[float, List[float]]]]:
        """提取词条中各音节发音核的 num_points 点 F0 数据与时长。返回 (总时长, [(音节时长, [F0数组]), ...])。"""
        if not item or item.get('start') is None:
            return 0.0, []

        p_xs, p_freqs = UnifiedExportService.get_pitch_arrays_for_item(item)
        if p_xs is None or p_freqs is None:
            if item.get('snd') or item.get('path'):
                try:
                    snd = item.get('snd')
                    if snd is None and item.get('path'):
                        try:
                            import parselmouth
                        except ImportError:
                            from modules import _parselmouth_fallback as parselmouth
                        snd = parselmouth.Sound(item['path'])
                        item['snd'] = snd
                    if snd is not None:
                        try:
                            from ..dsp.pitch_formant_unified import extract_f0
                        except ImportError:
                            from ..audio_core import extract_f0
                        p_fl = item.get('pitch_floor', pitch_floor)
                        p_cl = item.get('pitch_ceiling', pitch_ceiling)
                        v_th = item.get('voicing_threshold', voicing_threshold)
                        item['pitch_data'] = extract_f0(snd, {'pitch_floor': p_fl, 'pitch_ceiling': p_cl, 'voicing_threshold': v_th})
                        p_xs, p_freqs = UnifiedExportService.get_pitch_arrays_for_item(item)
                except Exception:
                    pass

        if p_xs is None or p_freqs is None:
            return 0.0, []

        t_s, t_e = item.get('start', 0.0), item.get('end', 0.0)
        if t_e <= t_s:
            return 0.0, []

        syls, bounds = UnifiedExportService.get_syllables_and_bounds(item)
        syl_data: List[Tuple[float, List[float]]] = []

        for c_s, c_e in bounds:
            if c_e <= c_s:
                syl_data.append((0.0, [0.0] * num_points))
                continue

            valid_idx = np.where((p_xs >= c_s) & (p_xs <= c_e) & (p_freqs > 0))[0]
            if len(valid_idx) >= 2:
                v_s, v_e = p_xs[valid_idx[0]], p_xs[valid_idx[-1]]
                seg_xs = p_xs[valid_idx]
                seg_ys = p_freqs[valid_idx]
            else:
                syl_data.append((0.0, [0.0] * num_points))
                continue

            dur = v_e - v_s
            if dur <= 0:
                syl_data.append((0.0, [0.0] * num_points))
                continue

            times = np.linspace(v_s, v_e, num_points)
            if len(seg_xs) >= 2:
                f0s = np.interp(times, seg_xs, seg_ys).tolist()
                for j, t in enumerate(times):
                    if np.min(np.abs(seg_xs - t)) > 0.025:
                        f0s[j] = 0.0
                syl_data.append((dur, f0s))
            else:
                syl_data.append((dur, [0.0] * num_points))

        return t_e - t_s, syl_data

    @staticmethod
    def collect_group_avg_data(
        groups_dict: Any,
        num_points: int,
        extract_syl_fn: Optional[Callable] = None,
    ) -> Tuple[Optional[Dict[str, List[Optional[float]]]], int]:
        """聚合各分组音节的平均 F0 并换算为五度制 T 值。返回 (result_dict, max_syls)。"""
        if extract_syl_fn is None:
            extract_syl_fn = UnifiedExportService.extract_syl_data

        if isinstance(groups_dict, dict):
            structure = list(groups_dict.items())
        else:
            structure = list(groups_dict)

        max_syls = 1
        dict_data: Dict[str, Dict[str, Any]] = {}
        for grp_name, items in structure:
            for item in items:
                lbl = item.get('label', '') if isinstance(item, dict) else ''
                if len(lbl) > max_syls:
                    max_syls = len(lbl)
                try:
                    total_dur, syl_data = extract_syl_fn(item, num_points)
                except Exception:
                    continue
                if total_dur <= 0 or not syl_data:
                    continue

                if grp_name not in dict_data:
                    dict_data[grp_name] = {
                        'f0_sums': [[0.0] * num_points for _ in range(max(20, len(syl_data)))],
                        'f0_counts': [[0] * num_points for _ in range(max(20, len(syl_data)))]
                    }
                for k, (dur, f0s) in enumerate(syl_data):
                    while k >= len(dict_data[grp_name]['f0_sums']):
                        dict_data[grp_name]['f0_sums'].append([0.0] * num_points)
                        dict_data[grp_name]['f0_counts'].append([0] * num_points)
                    for i, f0 in enumerate(f0s):
                        if not np.isnan(f0) and f0 > 0:
                            dict_data[grp_name]['f0_sums'][k][i] += f0
                            dict_data[grp_name]['f0_counts'][k][i] += 1

        all_avg_hz = []
        avg_points_map = {}
        for grp, st in dict_data.items():
            avg_points_map[grp] = []
            for k in range(max_syls):
                syl_avgs = []
                for i in range(num_points):
                    cnt = st['f0_counts'][k][i] if k < len(st['f0_counts']) else 0
                    hz = st['f0_sums'][k][i] / cnt if cnt > 0 else 0.0
                    syl_avgs.append(hz)
                    if hz > 0:
                        all_avg_hz.append(hz)
                avg_points_map[grp].append(syl_avgs)

        if not all_avg_hz:
            return None, 1
        min_hz, max_hz = min(all_avg_hz), max(all_avg_hz)

        result: Dict[str, List[Optional[float]]] = {}
        for grp, syl_avgs_list in avg_points_map.items():
            flat_t_vals: List[Optional[float]] = []
            for syl_avgs in syl_avgs_list:
                for h in syl_avgs:
                    if h > 0 and max_hz > min_hz and min_hz > 0:
                        flat_t_vals.append(5.0 * (math.log10(h) - math.log10(min_hz)) / (math.log10(max_hz) - math.log10(min_hz)))
                    else:
                        flat_t_vals.append(None)
            result[grp] = flat_t_vals

        return result, max_syls

    @staticmethod
    def check_item_has_empty_data(
        item: Dict[str, Any],
        pts: int = 11,
        default_mode: str = 'f0',
    ) -> bool:
        """精准检测词条在其音节区间内是否含有 0/NaN 空数据或缺损数据。"""
        if not item or item.get('start') is None:
            return False
        if item.get('preview_segment_mismatch'):
            item['has_empty_data'] = True
            return True

        mode = item.get('analysis_mode', default_mode)
        if mode == 'formant':
            f_data = item.get('formant_data')
            if not f_data:
                item['has_empty_data'] = False
                return False
            f_xs = np.asarray(f_data.get('xs', np.array([])), dtype=float)
            f1 = np.asarray(f_data.get('f1', np.array([])), dtype=float)
            f2 = np.asarray(f_data.get('f2', np.array([])), dtype=float)
            if len(f_xs) == 0:
                item['has_empty_data'] = True
                return True

            syls, bounds = UnifiedExportService.get_syllables_and_bounds(item)
            has_empty = False
            for idx, (c_s, c_e) in enumerate(bounds):
                dur = c_e - c_s
                if dur <= 0:
                    has_empty = True
                    break
                margin = dur * 0.125
                core_s = c_s + margin
                core_e = c_e - margin

                mask = (f_xs >= core_s) & (f_xs <= core_e)
                seg_xs = f_xs[mask]
                seg_f1 = f1[mask]
                seg_f2 = f2[mask]
                if len(seg_xs) == 0:
                    has_empty = True
                    break
                valid_mask = ~np.isnan(seg_f1) & ~np.isnan(seg_f2) & (seg_f2 > seg_f1)
                ratio = np.sum(valid_mask) / len(seg_xs)
                if ratio < 0.40:
                    has_empty = True
                    break

            item['has_empty_data'] = has_empty
            return has_empty

        p_xs, p_freqs = UnifiedExportService.get_pitch_arrays_for_item(item)
        if p_xs is not None and p_freqs is not None:
            syls, bounds = UnifiedExportService.get_syllables_and_bounds(item)
            has_empty = False
            for c_s, c_e in bounds:
                if c_e <= c_s:
                    continue

                valid_idx = np.where((p_xs >= c_s) & (p_xs <= c_e) & (p_freqs > 0))[0]
                if len(valid_idx) >= 2:
                    v_s, v_e = p_xs[valid_idx[0]], p_xs[valid_idx[-1]]
                    seg_xs = p_xs[valid_idx]
                    seg_ys = p_freqs[valid_idx]
                else:
                    has_empty = True
                    break

                if v_e <= v_s:
                    has_empty = True
                    break

                times = np.linspace(v_s, v_e, pts)
                f0s = np.interp(times, seg_xs, seg_ys)
                for t, hz in zip(times, f0s):
                    if np.min(np.abs(seg_xs - t)) > 0.025 or np.isnan(hz) or hz <= 0:
                        has_empty = True
                        break
                if has_empty:
                    break

            item['has_empty_data'] = has_empty
            return has_empty

        if 'has_empty_data' in item:
            return bool(item['has_empty_data'])

        if item.get('preview_f0'):
            return any(hz == 0 or np.isnan(hz) for hz in item['preview_f0'])

        return False

    # 导出委托接口
    export_text_for_item = staticmethod(get_export_text_for_item)
    export_formant_text_for_item = staticmethod(get_formant_export_text_for_item)
    export_textgrid_for_item = staticmethod(get_export_textgrid_for_item)
    extract_wordlist_from_textgrid = staticmethod(extract_wordlist_from_textgrid)
    write_raw_pitch_sheet = staticmethod(write_raw_pitch_sheet)
    write_analysis_sheet_with_formulas = staticmethod(write_analysis_sheet_with_formulas)
    write_formant_analysis_sheet_with_formulas = staticmethod(write_formant_analysis_sheet_with_formulas)
    build_five_point_chart = staticmethod(build_five_point_chart)
    write_praat_collection = staticmethod(write_praat_collection)
    build_single_item_collection_objects = staticmethod(build_single_item_collection_objects)
    build_speaker_collection_objects = staticmethod(build_speaker_collection_objects)
    export_speaker_to_praat_collection = staticmethod(export_speaker_to_praat_collection)
