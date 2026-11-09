# -*- coding: utf-8 -*-
"""
PhonTracer语音学标注与声学分析系统 V1.0 自定义脚本 API 上下文定义
"""

import os
import zipfile
import json
import math
import tempfile
import numpy as np
try:
    import parselmouth
except ImportError:
    from modules import _parselmouth_fallback as parselmouth
    import sys as _sys
    _sys.modules.setdefault("parselmouth", parselmouth)
    _sys.modules.setdefault("parselmouth.praat", parselmouth.praat)


def configure_matplotlib_chinese_font():
    """
    为脚本生成的 Matplotlib 图表配置中文字体与国际音标 (IPA) 回退链，并开启 PDF Type 42 矢量字体内嵌。
    """
    try:
        import matplotlib
        from matplotlib import font_manager

        candidates = [
            "Microsoft YaHei",
            "SimHei",
            "SimSun",
            "DengXian",
            "Noto Sans CJK SC",
            "Noto Sans CJK JP",
            "PingFang SC",
            "WenQuanYi Micro Hei",
            "Charis SIL",
            "Doulos SIL",
            "Segoe UI",
            "Arial Unicode MS",
        ]
        installed = {font.name for font in font_manager.fontManager.ttflist}
        preferred = [name for name in candidates if name in installed]
        fallback_tail = ["Charis SIL", "Doulos SIL", "Segoe UI", "Arial Unicode MS", "DejaVu Sans", "sans-serif"]
        if preferred:
            matplotlib.rcParams["font.family"] = "sans-serif"
            matplotlib.rcParams["font.sans-serif"] = preferred + [f for f in fallback_tail if f not in preferred]
        else:
            matplotlib.rcParams["font.sans-serif"] = candidates + ["DejaVu Sans", "sans-serif"]
        matplotlib.rcParams["axes.unicode_minus"] = False
        matplotlib.rcParams["pdf.fonttype"] = 42
        matplotlib.rcParams["ps.fonttype"] = 42
    except Exception:
        pass


def get_f0_normalization_bounds(f0_values):
    from modules.data_utils import compute_speaker_f0_bounds
    return compute_speaker_f0_bounds(f0_values, method="p5_p95_iqr")


class FigureResult:
    """
    表示脚本生成的图表结果。
    支持 formats=("png", "pdf", "svg") 多格式同步导出与自定义 dpi。
    """
    def __init__(self, fig, filename="custom_chart.png", title="自定义图表", formats=None, dpi=None):
        self.fig = fig
        self.filename = filename
        self.title = title
        if formats is not None:
            if isinstance(formats, str):
                formats = [formats]
            clean_fmts = []
            for fmt in formats:
                f_str = str(fmt or "").strip().lower().lstrip(".")
                if f_str in ("png", "pdf", "svg", "jpg", "jpeg") and f_str not in clean_fmts:
                    clean_fmts.append(f_str)
            self.formats = tuple(clean_fmts) if clean_fmts else None
        else:
            self.formats = None
        if dpi is not None:
            try:
                val = int(dpi)
                self.dpi = val if val > 0 else None
            except (TypeError, ValueError):
                self.dpi = None
        else:
            self.dpi = None

class TableResult:
    """
    表示脚本生成的数据表格结果。
    """
    def __init__(self, rows, columns, title="自定义表格"):
        self.rows = rows
        self.columns = columns
        self.title = title


class ProjectPatchResult:
    """
    表示脚本生成的工程数据处理操作清单。
    脚本不直接修改 .teproj，只返回受控操作，由 Toolkit 统一校验和写回。
    """
    ALLOWED_OPS = {
        "set_item_fields",
        "recompute_pitch",
        "recompute_formant",
        "trim_item_audio",
        "split_project",
        "import_csv_metadata",
    }

    def __init__(self, operations, title="数据处理脚本结果", description=""):
        if operations is None:
            operations = []
        if not isinstance(operations, list):
            raise ValueError("project_patch 的 operations 必须是列表。")

        clean_ops = []
        for idx, op in enumerate(operations, start=1):
            if not isinstance(op, dict):
                raise ValueError(f"第 {idx} 个数据处理操作不是字典。")
            op_type = op.get("op")
            if op_type not in self.ALLOWED_OPS:
                raise ValueError(f"第 {idx} 个数据处理操作类型不受支持：{op_type}")
            clean_ops.append(dict(op))

        self.operations = clean_ops
        self.title = title
        self.description = description

    @property
    def operation_count(self):
        return len(self.operations)


def _hz_to_bark_arr(arr):
    a = np.asarray(arr, dtype=float)
    out = np.full(a.shape, np.nan, dtype=float)
    v = np.isfinite(a) & (a > 0)
    if np.any(v):
        out[v] = (26.81 * a[v]) / (1960.0 + a[v]) - 0.53
    return out


def _hz_to_erb_arr(arr):
    a = np.asarray(arr, dtype=float)
    out = np.full(a.shape, np.nan, dtype=float)
    v = np.isfinite(a) & (a > 0)
    if np.any(v):
        out[v] = 21.4 * np.log10(1.0 + 0.00437 * a[v])
    return out


class DatasetSnapshot:
    """
    只读数据快照包装器（支持预展开标准长宽表 to_point_table / to_syllable_table / to_frame_table 与内存缓存）。
    """
    def __init__(self, items):
        self.items = list(items or [])  # list of dicts
        self._point_table_cache = {}
        self._syllable_table_cache = {}
        self._frame_table_cache = {}

    def clear_cache(self):
        """清空预展开表格缓存"""
        self._point_table_cache.clear()
        self._syllable_table_cache.clear()
        self._frame_table_cache.clear()

    def groups(self):
        """返回所有不重复且非空的分组列表（排序后）"""
        grps = set()
        for item in self.items:
            g = item.get("group")
            if g:
                grps.add(g)
        return sorted(list(grps))

    def speakers(self):
        """返回所有不重复的发音人姓名列表（排序后）"""
        spks = set()
        for item in self.items:
            s = item.get("speaker_name")
            if s:
                spks.add(s)
        return sorted(list(spks))

    def included_items(self):
        """返回所有未排除的分析条目"""
        return [item for item in self.items if not item.get("is_excluded", False)]

    def pitch_points(self, item):
        """获取指定条目的基频点数据"""
        return item.get("pitch", {"xs": [], "freqs": [], "t_values": []})

    def formant_points(self, item):
        """获取指定条目的共振峰点数据"""
        return item.get("formant", {"xs": [], "f1": [], "f2": [], "f3": []})

    def _collect_speaker_f0_bounds_map(self, target_items):
        from modules.data_utils import compute_speaker_f0_bounds
        spk_bounds = {}
        spk_pools = {}
        for it in target_items:
            spk = it.get("speaker_name") or it.get("speaker_id") or "默认发音人"
            if it.get("speaker_f0_bounds"):
                spk_bounds[spk] = tuple(it["speaker_f0_bounds"])
                continue
            spk_pools.setdefault(spk, [])
            p_freqs = (it.get("pitch") or {}).get("freqs") or []
            for f in p_freqs:
                if f is not None and np.isfinite(f) and f > 0:
                    spk_pools[spk].append(float(f))
            for _, f0s in (it.get("syl_data") or []):
                for f in (f0s or []):
                    if f is not None and np.isfinite(f) and f > 0:
                        spk_pools[spk].append(float(f))
        for spk, pool in spk_pools.items():
            if spk not in spk_bounds:
                spk_bounds[spk] = compute_speaker_f0_bounds(pool, method="p5_p95_iqr")
        return spk_bounds

    def _collect_speaker_lobanov_stats(self, target_items):
        spk_tokens = {}
        for it in target_items:
            spk = it.get("speaker_name") or it.get("speaker_id") or "默认发音人"
            spk_tokens.setdefault(spk, ([], []))
            for syl_f in (it.get("syl_formants") or []):
                f1_arr = np.asarray(syl_f.get("f1") or [], dtype=float)
                f2_arr = np.asarray(syl_f.get("f2") or [], dtype=float)
                n_p = len(f1_arr)
                if n_p > 0 and len(f2_arr) == n_p:
                    i0 = int(math.floor(0.2 * n_p)) if n_p >= 5 else 0
                    i1 = max(i0 + 1, int(math.ceil(0.8 * n_p))) if n_p >= 5 else n_p
                    sub1, sub2 = f1_arr[i0:i1], f2_arr[i0:i1]
                    v = np.isfinite(sub1) & np.isfinite(sub2) & (sub1 > 0) & (sub2 > 0)
                    if np.any(v):
                        spk_tokens[spk][0].append(float(np.mean(sub1[v])))
                        spk_tokens[spk][1].append(float(np.mean(sub2[v])))
        stats = {}
        for spk, (f1s, f2s) in spk_tokens.items():
            if len(f1s) >= 2:
                m1, s1 = float(np.mean(f1s)), float(np.std(f1s, ddof=1))
                m2, s2 = float(np.mean(f2s)), float(np.std(f2s, ddof=1))
                if s1 < 1e-3:
                    s1 = 100.0
                if s2 < 1e-3:
                    s2 = 250.0
                stats[spk] = (m1, s1, m2, s2)
        return stats

    def to_point_table(self, included_only=True, as_df=True):
        """
        生成采样点级预展开长表（每行 = 1 个词条某音节的第 k 个归一化采样点，1..11）。
        自动完成 syl_data <= 0 -> np.nan 清洗、T 值/半音/Bark/按发音人 Lobanov Z-score 预计算及 field_* 展开。
        """
        import copy as _copy
        flag = bool(included_only)
        if flag not in self._point_table_cache:
            from modules.data_utils import split_into_syllables, hz_to_t_value
            target_items = self.included_items() if flag else list(self.items)
            spk_bounds_map = self._collect_speaker_f0_bounds_map(target_items)
            lobanov_stats = self._collect_speaker_lobanov_stats(target_items)

            all_field_keys = []
            seen_fk = set()
            for it in target_items:
                meta = it.get("fields") or it.get("custom_fields") or it.get("item_meta") or {}
                if isinstance(meta, dict):
                    for k in meta.keys():
                        if k not in seen_fk:
                            seen_fk.add(k)
                            all_field_keys.append(str(k))

            rows = []
            for it in target_items:
                spk_id = str(it.get("speaker_id") or "")
                spk_name = str(it.get("speaker_name") or spk_id or "默认发音人")
                item_id = str(it.get("item_id") or "")
                word = str(it.get("label") or it.get("word") or "")
                group = str(it.get("group") or "默认组")
                tags_list = list(it.get("tags") or it.get("item_tags") or [])
                meta = it.get("fields") or it.get("custom_fields") or it.get("item_meta") or {}
                if not isinstance(meta, dict):
                    meta = {}
                flat_fields = {f"field_{k}": meta.get(k) for k in all_field_keys}

                s_min, s_max = spk_bounds_map.get(spk_name, (75.0, 600.0))
                f_ref = s_min if (np.isfinite(s_min) and s_min > 0) else 100.0
                lob_m1, lob_s1, lob_m2, lob_s2 = lobanov_stats.get(spk_name, (np.nan, np.nan, np.nan, np.nan))

                syl_data = it.get("syl_data") or []
                syl_t_values = it.get("syl_t_values") or []
                syl_formants = it.get("syl_formants") or []
                chars = split_into_syllables(word)
                syl_count = max(len(syl_data), len(syl_formants), len(chars), 1)

                acc_dur_ms = 0.0
                for s_idx in range(syl_count):
                    char_str = chars[s_idx] if s_idx < len(chars) else (
                        syl_formants[s_idx].get("char") if s_idx < len(syl_formants) else f"字{s_idx + 1}"
                    )
                    if s_idx < len(syl_data):
                        s_dur_s, raw_f0_pts = syl_data[s_idx]
                        f0_arr = np.asarray(raw_f0_pts, dtype=float)
                        f0_arr = np.where(np.isfinite(f0_arr) & (f0_arr > 0), f0_arr, np.nan)
                    else:
                        s_dur_s = 0.0
                        f0_arr = np.full(11, np.nan, dtype=float)

                    n_pts = len(f0_arr) if len(f0_arr) > 0 else 11
                    if s_idx < len(syl_t_values) and len(syl_t_values[s_idx]) == n_pts:
                        t_arr = np.asarray(syl_t_values[s_idx], dtype=float)
                        # 若存在无效但 f0_arr 有效的点则用统一公式补齐
                        recalc_t = hz_to_t_value(f0_arr, s_min, s_max, clip=True)
                        t_arr = np.where(np.isfinite(t_arr), t_arr, recalc_t)
                    else:
                        t_arr = hz_to_t_value(f0_arr, s_min, s_max, clip=True)

                    st_arr = np.where(np.isfinite(f0_arr) & (f0_arr > 0), 12.0 * np.log2(f0_arr / f_ref), np.nan)

                    if s_idx < len(syl_formants):
                        sf = syl_formants[s_idx]
                        f1_arr = np.asarray(sf.get("f1") or [], dtype=float)
                        f2_arr = np.asarray(sf.get("f2") or [], dtype=float)
                        f3_arr = np.asarray(sf.get("f3") or [], dtype=float)
                        if s_dur_s <= 0 and sf.get("bounds"):
                            b0, b1 = sf["bounds"]
                            if b1 > b0:
                                s_dur_s = float(b1 - b0)
                    else:
                        f1_arr = np.full(n_pts, np.nan, dtype=float)
                        f2_arr = np.full(n_pts, np.nan, dtype=float)
                        f3_arr = np.full(n_pts, np.nan, dtype=float)

                    def _pad_or_trim(a, length):
                        a = np.asarray(a, dtype=float)
                        a = np.where(np.isfinite(a) & (a > 0), a, np.nan)
                        if len(a) == length:
                            return a
                        if len(a) == 0:
                            return np.full(length, np.nan, dtype=float)
                        return np.pad(a[:length], (0, max(0, length - len(a))), constant_values=np.nan)

                    f1_arr = _pad_or_trim(f1_arr, n_pts)
                    f2_arr = _pad_or_trim(f2_arr, n_pts)
                    f3_arr = _pad_or_trim(f3_arr, n_pts)
                    bark1_arr = _hz_to_bark_arr(f1_arr)
                    bark2_arr = _hz_to_bark_arr(f2_arr)
                    if np.isfinite(lob_m1) and np.isfinite(lob_s1) and lob_s1 > 0:
                        lob1_arr = (f1_arr - lob_m1) / lob_s1
                        lob2_arr = (f2_arr - lob_m2) / lob_s2
                    else:
                        lob1_arr = np.full(n_pts, np.nan, dtype=float)
                        lob2_arr = np.full(n_pts, np.nan, dtype=float)

                    dur_ms = float(s_dur_s) * 1000.0 if s_dur_s and s_dur_s > 0 else np.nan
                    for p_i in range(n_pts):
                        norm_t = float(p_i) / float(max(1, n_pts - 1))
                        phys_ms = (acc_dur_ms + norm_t * dur_ms) if np.isfinite(dur_ms) else np.nan
                        row = {
                            "speaker_id": spk_id,
                            "speaker_name": spk_name,
                            "item_id": item_id,
                            "word": word,
                            "label": word,
                            "char": str(char_str),
                            "syl_idx": int(s_idx),
                            "syl_count": int(syl_count),
                            "group": group,
                            "point_idx": int(p_i + 1),
                            "norm_time": round(norm_t, 4),
                            "phys_time_ms": round(float(phys_ms), 3) if np.isfinite(phys_ms) else np.nan,
                            "f0_hz": float(f0_arr[p_i]),
                            "t_value": float(t_arr[p_i]),
                            "semitone": float(st_arr[p_i]),
                            "f1_hz": float(f1_arr[p_i]),
                            "f2_hz": float(f2_arr[p_i]),
                            "f3_hz": float(f3_arr[p_i]),
                            "bark_f1": float(bark1_arr[p_i]),
                            "bark_f2": float(bark2_arr[p_i]),
                            "lobanov_f1": float(lob1_arr[p_i]),
                            "lobanov_f2": float(lob2_arr[p_i]),
                            "tags": list(tags_list),
                        }
                        row.update(flat_fields)
                        rows.append(row)
                    if np.isfinite(dur_ms):
                        acc_dur_ms += dur_ms

            self._point_table_cache[flag] = rows

        cached_rows = self._point_table_cache[flag]
        if as_df:
            import pandas as pd
            return pd.DataFrame(cached_rows).copy()
        return _copy.deepcopy(cached_rows)

    def to_syllable_table(self, included_only=True, as_df=True):
        """
        生成音节级汇总宽表（每行 = 1 个词条中的 1 个音节）。
        包含时长、基频均值/极值/调域/斜率、五度值首中尾与自动拟合调值 t_code、共振峰稳态均值与 20%->80% 动程长度 tl_hz。
        """
        import copy as _copy
        flag = bool(included_only)
        if flag not in self._syllable_table_cache:
            from modules.data_utils import split_into_syllables, hz_to_t_value
            from modules.report_generator import fit_chao_tone_code
            target_items = self.included_items() if flag else list(self.items)
            spk_bounds_map = self._collect_speaker_f0_bounds_map(target_items)

            all_field_keys = []
            seen_fk = set()
            for it in target_items:
                meta = it.get("fields") or it.get("custom_fields") or it.get("item_meta") or {}
                if isinstance(meta, dict):
                    for k in meta.keys():
                        if k not in seen_fk:
                            seen_fk.add(k)
                            all_field_keys.append(str(k))

            rows = []
            for it in target_items:
                spk_id = str(it.get("speaker_id") or "")
                spk_name = str(it.get("speaker_name") or spk_id or "默认发音人")
                item_id = str(it.get("item_id") or "")
                word = str(it.get("label") or it.get("word") or "")
                group = str(it.get("group") or "默认组")
                tags_list = list(it.get("tags") or it.get("item_tags") or [])
                meta = it.get("fields") or it.get("custom_fields") or it.get("item_meta") or {}
                if not isinstance(meta, dict):
                    meta = {}
                flat_fields = {f"field_{k}": meta.get(k) for k in all_field_keys}

                s_min, s_max = spk_bounds_map.get(spk_name, (75.0, 600.0))
                syl_data = it.get("syl_data") or []
                syl_t_values = it.get("syl_t_values") or []
                syl_formants = it.get("syl_formants") or []
                chars = split_into_syllables(word)
                syl_count = max(len(syl_data), len(syl_formants), len(chars), 1)

                for s_idx in range(syl_count):
                    char_str = chars[s_idx] if s_idx < len(chars) else (
                        syl_formants[s_idx].get("char") if s_idx < len(syl_formants) else f"字{s_idx + 1}"
                    )
                    if s_idx < len(syl_data):
                        s_dur_s, raw_f0_pts = syl_data[s_idx]
                        f0_arr = np.asarray(raw_f0_pts, dtype=float)
                        f0_arr = np.where(np.isfinite(f0_arr) & (f0_arr > 0), f0_arr, np.nan)
                    else:
                        s_dur_s = 0.0
                        f0_arr = np.full(11, np.nan, dtype=float)

                    n_pts = len(f0_arr) if len(f0_arr) > 0 else 11
                    if s_idx < len(syl_t_values) and len(syl_t_values[s_idx]) == n_pts:
                        t_arr = np.asarray(syl_t_values[s_idx], dtype=float)
                        recalc_t = hz_to_t_value(f0_arr, s_min, s_max, clip=True)
                        t_arr = np.where(np.isfinite(t_arr), t_arr, recalc_t)
                    else:
                        t_arr = hz_to_t_value(f0_arr, s_min, s_max, clip=True)

                    valid_f0 = np.isfinite(f0_arr)
                    if np.any(valid_f0):
                        vf = f0_arr[valid_f0]
                        f0_mean = float(np.mean(vf))
                        f0_min_v = float(np.min(vf))
                        f0_max_v = float(np.max(vf))
                        f0_range = float(f0_max_v - f0_min_v)
                    else:
                        f0_mean = f0_min_v = f0_max_v = f0_range = np.nan

                    if np.sum(valid_f0) >= 2:
                        norm_x = np.linspace(0.0, 1.0, n_pts)[valid_f0]
                        slope, _ = np.polyfit(norm_x, f0_arr[valid_f0], 1)
                        f0_slope = float(slope)
                    else:
                        f0_slope = np.nan

                    valid_t = np.isfinite(t_arr)
                    if np.any(valid_t):
                        vt = t_arr[valid_t]
                        t_onset = float(t_arr[0]) if np.isfinite(t_arr[0]) else float(vt[0])
                        mid_i = n_pts // 2
                        t_mid = float(t_arr[mid_i]) if np.isfinite(t_arr[mid_i]) else float(vt[len(vt) // 2])
                        t_offset = float(t_arr[-1]) if np.isfinite(t_arr[-1]) else float(vt[-1])
                        t_range = float(np.max(vt) - np.min(vt))
                        x_n = np.linspace(0.0, 1.0, n_pts)
                        if vt.size >= 2:
                            t_5pt = np.interp([0.01, 0.25, 0.50, 0.75, 1.00], x_n[valid_t], t_arr[valid_t]).tolist()
                        else:
                            t_5pt = [float(vt[0])] * 5
                        t_code = fit_chao_tone_code(t_5pt, t_start=t_onset, t_mid=t_mid, t_end=t_offset)
                    else:
                        t_onset = t_mid = t_offset = t_range = np.nan
                        t_code = ""

                    if s_idx < len(syl_formants):
                        sf = syl_formants[s_idx]
                        f1_a = np.asarray(sf.get("f1") or [], dtype=float)
                        f2_a = np.asarray(sf.get("f2") or [], dtype=float)
                        f3_a = np.asarray(sf.get("f3") or [], dtype=float)
                        f1_a = np.where(np.isfinite(f1_a) & (f1_a > 0), f1_a, np.nan)
                        f2_a = np.where(np.isfinite(f2_a) & (f2_a > 0), f2_a, np.nan)
                        f3_a = np.where(np.isfinite(f3_a) & (f3_a > 0), f3_a, np.nan)
                        if s_dur_s <= 0 and sf.get("bounds"):
                            b0, b1 = sf["bounds"]
                            if b1 > b0:
                                s_dur_s = float(b1 - b0)
                    else:
                        f1_a = np.full(11, np.nan, dtype=float)
                        f2_a = np.full(11, np.nan, dtype=float)
                        f3_a = np.full(11, np.nan, dtype=float)

                    def _steady_mid(arr_f):
                        if len(arr_f) >= 5:
                            i0 = int(math.floor(0.2 * len(arr_f)))
                            i1 = max(i0 + 1, int(math.ceil(0.8 * len(arr_f))))
                            sub = arr_f[i0:i1]
                            v = sub[np.isfinite(sub)]
                            if v.size > 0:
                                return float(np.mean(v))
                        v_all = arr_f[np.isfinite(arr_f)]
                        return float(np.mean(v_all)) if v_all.size > 0 else np.nan

                    f1_mid = _steady_mid(f1_a)
                    f2_mid = _steady_mid(f2_a)
                    f3_mid = _steady_mid(f3_a)
                    idx20 = min(max(0, int(round(0.2 * (len(f1_a) - 1)))), max(0, len(f1_a) - 1)) if len(f1_a) > 0 else 0
                    idx80 = min(max(0, int(round(0.8 * (len(f1_a) - 1)))), max(0, len(f1_a) - 1)) if len(f1_a) > 0 else 0
                    f1_20 = float(f1_a[idx20]) if len(f1_a) > idx20 else np.nan
                    f2_20 = float(f2_a[idx20]) if len(f2_a) > idx20 else np.nan
                    f1_80 = float(f1_a[idx80]) if len(f1_a) > idx80 else np.nan
                    f2_80 = float(f2_a[idx80]) if len(f2_a) > idx80 else np.nan

                    # Trajectory Length (TL in Hz across 20%..80% or valid points)
                    if len(f1_a) >= 2 and len(f2_a) == len(f1_a):
                        sub1 = f1_a[idx20 : idx80 + 1] if idx80 > idx20 else f1_a
                        sub2 = f2_a[idx20 : idx80 + 1] if idx80 > idx20 else f2_a
                        vm = np.isfinite(sub1) & np.isfinite(sub2)
                        if np.sum(vm) >= 2:
                            tl_hz = float(np.sum(np.hypot(np.diff(sub1[vm]), np.diff(sub2[vm]))))
                        else:
                            tl_hz = np.nan
                    else:
                        tl_hz = np.nan

                    dur_ms = float(s_dur_s) * 1000.0 if (s_dur_s and s_dur_s > 0) else np.nan
                    row = {
                        "speaker_id": spk_id,
                        "speaker_name": spk_name,
                        "item_id": item_id,
                        "word": word,
                        "label": word,
                        "char": str(char_str),
                        "syl_idx": int(s_idx),
                        "syl_count": int(syl_count),
                        "group": group,
                        "dur_ms": round(dur_ms, 3) if np.isfinite(dur_ms) else np.nan,
                        "f0_mean": f0_mean,
                        "f0_min": f0_min_v,
                        "f0_max": f0_max_v,
                        "f0_range": f0_range,
                        "f0_slope": f0_slope,
                        "t_onset": t_onset,
                        "t_mid": t_mid,
                        "t_offset": t_offset,
                        "t_range": t_range,
                        "t_code": t_code,
                        "f1_mid": f1_mid,
                        "f2_mid": f2_mid,
                        "f3_mid": f3_mid,
                        "f1_20": f1_20,
                        "f2_20": f2_20,
                        "f1_80": f1_80,
                        "f2_80": f2_80,
                        "tl_hz": tl_hz,
                        "tags": list(tags_list),
                    }
                    row.update(flat_fields)
                    rows.append(row)

            self._syllable_table_cache[flag] = rows

        cached_rows = self._syllable_table_cache[flag]
        if as_df:
            import pandas as pd
            return pd.DataFrame(cached_rows).copy()
        return _copy.deepcopy(cached_rows)

    def to_frame_table(self, item_or_items=None, included_only=True, as_df=True):
        """
        生成原始帧级声学表（每行 = 1 个原始物理时间帧）。
        包含字段: speaker_name, item_id, word, label, syl_idx, time_s, rel_time, f0_hz, f1_hz, f2_hz, f3_hz, intensity_db。
        """
        from modules.data_utils import get_item_syllable_bounds
        if item_or_items is None:
            target_items = self.included_items() if included_only else list(self.items)
        elif isinstance(item_or_items, dict):
            target_items = [item_or_items]
        else:
            target_items = list(item_or_items)

        rows = []
        for it in target_items:
            spk_name = str(it.get("speaker_name") or it.get("speaker_id") or "默认发音人")
            item_id = str(it.get("item_id") or "")
            word = str(it.get("label") or it.get("word") or "")
            pitch_d = it.get("pitch") or {}
            formant_d = it.get("formant") or {}

            p_xs = np.asarray(pitch_d.get("xs") or [], dtype=float)
            p_f0 = np.asarray(pitch_d.get("freqs") or [], dtype=float)
            p_f0 = np.where(np.isfinite(p_f0) & (p_f0 > 0), p_f0, np.nan)

            f_xs = np.asarray(formant_d.get("xs") or [], dtype=float)
            f1_a = np.asarray(formant_d.get("f1") or [], dtype=float)
            f2_a = np.asarray(formant_d.get("f2") or [], dtype=float)
            f3_a = np.asarray(formant_d.get("f3") or [], dtype=float)

            bounds = get_item_syllable_bounds({
                "start": it.get("start", 0.0),
                "end": it.get("end", 0.0),
                "label": word,
                "chars_bounds": it.get("chars_bounds") or [],
                "inner_splits": it.get("inner_splits") or [],
            })
            if not bounds:
                s0 = float(it.get("start") or 0.0)
                e0 = float(it.get("end") or (p_xs[-1] if p_xs.size else 0.0))
                bounds = [[s0, e0]]

            base_xs = p_xs if p_xs.size > 0 else f_xs
            if base_xs.size == 0:
                continue

            for s_idx, (c_s, c_e) in enumerate(bounds):
                mask = (base_xs >= c_s) & (base_xs <= c_e) if c_e > c_s else np.ones(base_xs.size, dtype=bool)
                sub_xs = base_xs[mask]
                if sub_xs.size == 0:
                    continue
                span = (c_e - c_s) if c_e > c_s else max(1e-6, float(sub_xs[-1] - sub_xs[0]))

                if p_xs.size == base_xs.size:
                    sub_f0 = p_f0[mask]
                elif p_xs.size >= 2 and np.any(np.isfinite(p_f0)):
                    vp = np.isfinite(p_f0)
                    sub_f0 = np.interp(sub_xs, p_xs[vp], p_f0[vp], left=np.nan, right=np.nan)
                else:
                    sub_f0 = np.full(sub_xs.size, np.nan, dtype=float)

                def _align_formant(arr_f):
                    if arr_f.size == 0:
                        return np.full(sub_xs.size, np.nan, dtype=float)
                    clean_f = np.where(np.isfinite(arr_f) & (arr_f > 0), arr_f, np.nan)
                    if f_xs.size == base_xs.size and clean_f.size == base_xs.size:
                        return clean_f[mask]
                    vf = np.isfinite(clean_f)
                    if f_xs.size == clean_f.size and np.sum(vf) >= 2:
                        return np.interp(sub_xs, f_xs[vf], clean_f[vf], left=np.nan, right=np.nan)
                    return np.full(sub_xs.size, np.nan, dtype=float)

                sub_f1 = _align_formant(f1_a)
                sub_f2 = _align_formant(f2_a)
                sub_f3 = _align_formant(f3_a)

                for i_t, t_val in enumerate(sub_xs):
                    rel_t = float(np.clip((t_val - c_s) / span, 0.0, 1.0)) if c_e > c_s else float(i_t) / max(1, sub_xs.size - 1)
                    rows.append({
                        "speaker_name": spk_name,
                        "item_id": item_id,
                        "word": word,
                        "label": word,
                        "syl_idx": int(s_idx),
                        "time_s": float(t_val),
                        "rel_time": round(rel_t, 4),
                        "f0_hz": float(sub_f0[i_t]),
                        "f1_hz": float(sub_f1[i_t]),
                        "f2_hz": float(sub_f2[i_t]),
                        "f3_hz": float(sub_f3[i_t]),
                        "intensity_db": np.nan,
                    })

        if as_df:
            import pandas as pd
            cols = ["speaker_name", "item_id", "word", "label", "syl_idx", "time_s", "rel_time", "f0_hz", "f1_hz", "f2_hz", "f3_hz", "intensity_db"]
            return pd.DataFrame(rows, columns=cols) if rows else pd.DataFrame(columns=cols)
        return rows


def _build_zip_member_lookup(zf):
    lookup = {}
    for name in zf.namelist():
        try:
            decoded = name.encode('cp437').decode('gbk')
        except Exception:
            try:
                decoded = name.encode('cp437').decode('utf-8')
            except Exception:
                decoded = name
        lookup[decoded.replace("\\", "/")] = name
        lookup[name.replace("\\", "/")] = name
    return lookup


def _normalize_audio_resource_path(path):
    if not path:
        return None
    norm = str(path).replace("\\", "/")
    parts = norm.split("/")
    if len(parts) < 2 or parts[0] != "audio":
        raise ValueError(f"工程音频资源路径不受支持：{path}")
    if any(part in ("", ".", "..") for part in parts):
        raise ValueError(f"工程音频资源路径非法：{path}")
    return norm


def _coerce_time(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _item_display_label(item, label_field="auto"):
    if not isinstance(item, dict):
        return ""
    meta = item.get("item_meta") or {}
    if not isinstance(meta, dict):
        meta = {}

    if label_field and label_field != "auto":
        if label_field.startswith("item_meta."):
            value = meta.get(label_field.split(".", 1)[1])
        else:
            value = item.get(label_field)
        if value:
            return str(value)

    for key in ("IPA", "ipa", "音标", "国际音标"):
        value = meta.get(key)
        if value:
            return str(value)

    aliases = item.get("item_aliases") or []
    if isinstance(aliases, (list, tuple)) and aliases:
        return str(aliases[0])
    if isinstance(aliases, str) and aliases.strip():
        return aliases.strip()

    return str(item.get("label") or item.get("item_id") or "")


from collections import OrderedDict

_GLOBAL_PROJECT_JSON_CACHE = OrderedDict()
_GLOBAL_SOUND_LRU_CACHE = OrderedDict()
_MAX_GLOBAL_SOUND_CACHE = 12


class ScriptContext:
    """
    传递给脚本 run(ctx) 函数的上下文对象。
    """
    def __init__(self, dataset_items, cancel_event=None, teproj_path=None, params=None):
        import numpy as np
        import matplotlib.pyplot as plt
        import scipy
        import parselmouth
        try:
            import pandas as pd
        except ImportError:
            pd = None

        configure_matplotlib_chinese_font()
        self.dataset = DatasetSnapshot(dataset_items) if not isinstance(dataset_items, DatasetSnapshot) else dataset_items
        self.np = np
        self.pd = pd
        self.plt = plt
        self.scipy = scipy
        self.parselmouth = parselmouth
        self.params = dict(params or {})
        self._teproj_path = teproj_path
        self._cancel_event = cancel_event
        self._logs = []
        self._project_json_cache = {}
        self._long_sound_cache = OrderedDict()

    def log(self, message):
        """记录一条运行日志"""
        self._logs.append(str(message))

    def figure(self, fig, filename="custom_chart.png", title="自定义图表", formats=None, dpi=None):
        """返回图表结果对象（支持 formats=("png", "pdf", "svg") 多格式同步导出与自定义 dpi）"""
        return FigureResult(fig, filename=filename, title=title, formats=formats, dpi=dpi)

    def table(self, rows, columns=None, title="自定义表格"):
        """返回表格结果对象（支持直接传入 pandas.DataFrame 或 (rows, columns)）"""
        if columns is None and hasattr(rows, "columns") and hasattr(rows, "values"):
            df = rows
            return TableResult(df.values.tolist(), list(df.columns), title)
        return TableResult(rows, columns or [], title)

    def to_point_table(self, included_only=True, as_df=True):
        """快捷调用 ctx.dataset.to_point_table"""
        return self.dataset.to_point_table(included_only=included_only, as_df=as_df)

    def to_syllable_table(self, included_only=True, as_df=True):
        """快捷调用 ctx.dataset.to_syllable_table"""
        return self.dataset.to_syllable_table(included_only=included_only, as_df=as_df)

    def to_frame_table(self, item_or_items=None, included_only=True, as_df=True):
        """快捷调用 ctx.dataset.to_frame_table"""
        return self.dataset.to_frame_table(item_or_items=item_or_items, included_only=included_only, as_df=as_df)

    def get_f0_bounds(self, f0_values):
        """计算发音人稳健五度值调域上下限 [s_min, s_max]（1.5×IQR 剔除离群值 + P5/P95 分位数）"""
        s_min, s_max = get_f0_normalization_bounds(f0_values)
        return [s_min, s_max]

    def _get_teproj_cache_token(self):
        if not self._teproj_path or not os.path.exists(self._teproj_path):
            return None
        abs_p = os.path.abspath(self._teproj_path)
        try:
            st = os.stat(abs_p)
            return (abs_p, st.st_mtime_ns, st.st_size)
        except OSError:
            return (abs_p, 0, 0)

    def _read_audio_resource(self, rel_path):
        if not self._teproj_path or not os.path.exists(self._teproj_path):
            raise ValueError("当前脚本上下文没有可用工程文件，无法读取受控音频资源。")

        norm_path = _normalize_audio_resource_path(rel_path)
        with zipfile.ZipFile(self._teproj_path, "r") as zf:
            member_lookup = _build_zip_member_lookup(zf)
            member = member_lookup.get(norm_path)
            if not member:
                raise FileNotFoundError(f"工程中找不到音频资源：{norm_path}")
            return zf.read(member)

    def _load_sound_from_resource(self, rel_path):
        norm_path = _normalize_audio_resource_path(rel_path)
        tok = self._get_teproj_cache_token()
        cache_key = (tok, norm_path) if tok is not None else (self._teproj_path, norm_path)

        if cache_key in self._long_sound_cache:
            self._long_sound_cache.move_to_end(cache_key)
            return self._long_sound_cache[cache_key]

        if tok is not None and cache_key in _GLOBAL_SOUND_LRU_CACHE:
            _GLOBAL_SOUND_LRU_CACHE.move_to_end(cache_key)
            snd = _GLOBAL_SOUND_LRU_CACHE[cache_key]
            self._long_sound_cache[cache_key] = snd
            return snd

        audio_bytes = self._read_audio_resource(norm_path)
        suffix = os.path.splitext(str(norm_path))[1] or ".wav"
        fd, tmp_path = tempfile.mkstemp(prefix="phontracer_script_audio_", suffix=suffix)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(audio_bytes)
            snd = self.parselmouth.Sound(tmp_path)
        finally:
            try:
                os.remove(tmp_path)
            except OSError:
                pass

        self._long_sound_cache[cache_key] = snd
        while len(self._long_sound_cache) > _MAX_GLOBAL_SOUND_CACHE:
            self._long_sound_cache.popitem(last=False)

        if tok is not None:
            _GLOBAL_SOUND_LRU_CACHE[cache_key] = snd
            while len(_GLOBAL_SOUND_LRU_CACHE) > _MAX_GLOBAL_SOUND_CACHE:
                _GLOBAL_SOUND_LRU_CACHE.popitem(last=False)

        return snd

    def _project_item_for_snapshot(self, item):
        if not isinstance(item, dict):
            raise ValueError("load_item_sound 需要传入 ctx.dataset.items 中的条目字典。")
        if not self._teproj_path or not os.path.exists(self._teproj_path):
            raise ValueError("当前脚本上下文没有可用工程文件，无法读取受控音频资源。")

        tok = self._get_teproj_cache_token()
        project_data = self._project_json_cache.get(tok) if tok is not None else None
        if project_data is None and tok is not None and tok in _GLOBAL_PROJECT_JSON_CACHE:
            project_data = _GLOBAL_PROJECT_JSON_CACHE[tok]
            self._project_json_cache[tok] = project_data

        if project_data is None:
            with zipfile.ZipFile(self._teproj_path, "r") as zf:
                try:
                    raw = zf.read("project.json")
                except KeyError as exc:
                    raise ValueError("工程文件缺少 project.json，无法读取音频。") from exc
            project_data = json.loads(raw.decode("utf-8"))
            if tok is not None:
                self._project_json_cache[tok] = project_data
                _GLOBAL_PROJECT_JSON_CACHE[tok] = project_data
                while len(_GLOBAL_PROJECT_JSON_CACHE) > 16:
                    _GLOBAL_PROJECT_JSON_CACHE.popitem(last=False)

        speakers = project_data.get("speakers", {})
        speaker_id = item.get("speaker_id")
        item_id = item.get("item_id")

        if speaker_id in speakers:
            spk = speakers[speaker_id]
            items = spk.get("items", {}) or {}
            if item_id in items:
                return spk, items[item_id]

        for spk in speakers.values():
            if not isinstance(spk, dict):
                continue
            items = spk.get("items", {}) or {}
            if item_id in items:
                return spk, items[item_id]

        label = item.get("label", "")
        raise ValueError(f"工程中找不到目标条目：speaker_id={speaker_id}, item_id={item_id}, label={label}")

    def load_item_sound(self, item, padding=0.0):
        """
        从当前 .teproj 中受控读取条目音频，返回 parselmouth.Sound。
        独立音频优先读取条目 path；长音频条目会按 start/end 提取当前片段。
        """
        spk, project_item = self._project_item_for_snapshot(item)
        item_path = project_item.get("path")
        padding = max(0.0, _coerce_time(padding, 0.0))

        if item_path:
            snd = self._load_sound_from_resource(item_path)
            total = float(snd.get_total_duration())
            start = _coerce_time(project_item.get("start"), 0.0)
            end = _coerce_time(project_item.get("end"), total)
            if end > start and (start > 0.0 or end < total):
                from_time = max(0.0, start - padding)
                to_time = min(total, end + padding)
                if to_time > from_time:
                    return snd.extract_part(from_time=from_time, to_time=to_time)
            return snd

        long_audio_path = spk.get("long_audio_path")
        if not long_audio_path:
            raise ValueError(f"条目 {project_item.get('label', '')} 缺少可用音频资源。")

        snd = self._load_sound_from_resource(long_audio_path)
        total = float(snd.get_total_duration())
        start = _coerce_time(project_item.get("start"), 0.0)
        end = _coerce_time(project_item.get("end"), total)
        from_time = max(0.0, start - padding)
        to_time = min(total, end + padding)
        if to_time <= from_time:
            raise ValueError(f"条目 {project_item.get('label', '')} 的音频时间范围无效。")
        return snd.extract_part(from_time=from_time, to_time=to_time)

    def spectrogram_data(self, sound, max_frequency=5000.0, window_length=0.005, dynamic_range_db=50.0):
        """
        使用 Parselmouth 生成 dB 语谱图矩阵，返回 x/y/db/vmin/vmax 等绘图数据。
        """
        if sound is None:
            raise ValueError("spectrogram_data 需要传入 parselmouth.Sound。")
        max_frequency = _coerce_time(max_frequency, 5000.0)
        window_length = _coerce_time(window_length, 0.005)
        dynamic_range_db = max(1.0, _coerce_time(dynamic_range_db, 50.0))
        try:
            nyquist = float(sound.sampling_frequency) / 2.0
            if nyquist > 0:
                max_frequency = min(max_frequency, nyquist)
        except Exception:
            pass
        if max_frequency <= 0:
            max_frequency = 5000.0
        if window_length <= 0:
            window_length = 0.005

        spectrogram = sound.to_spectrogram(window_length=window_length, maximum_frequency=max_frequency)
        x_grid = spectrogram.x_grid()
        y_grid = spectrogram.y_grid()
        values = np.where(spectrogram.values > 0, spectrogram.values, 1e-10)
        db_values = 10 * np.log10(values)
        finite = db_values[np.isfinite(db_values)]
        vmax = float(np.max(finite)) if finite.size else 0.0
        return {
            "x": x_grid,
            "y": y_grid,
            "db": db_values,
            "vmin": vmax - dynamic_range_db,
            "vmax": vmax,
            "max_frequency": float(max_frequency),
            "window_length": float(window_length),
            "dynamic_range_db": float(dynamic_range_db),
        }

    def plot_spectrogram_grid(
        self,
        items,
        columns=4,
        max_items=8,
        show_formant_arrows=True,
        label_field="auto",
        max_frequency=4000.0,
    ):
        """
        生成参考图式多宫格灰度语谱图，返回 Matplotlib Figure。
        """
        items = list(items or [])[: max(1, int(max_items or 8))]
        columns = max(1, int(columns or 4))
        rows = max(1, int(math.ceil(len(items) / columns))) if items else 1
        fig, axes = self.plt.subplots(
            rows,
            columns,
            figsize=(max(3.0, columns * 2.2), max(2.2, rows * 1.8)),
            squeeze=False,
        )
        axes_flat = [ax for row in axes for ax in row]

        if not items:
            ax = axes_flat[0]
            ax.text(0.5, 0.5, "没有可用音频条目", ha="center", va="center")
            ax.axis("off")
            for extra_ax in axes_flat[1:]:
                extra_ax.axis("off")
            fig.tight_layout()
            return fig

        for ax, item in zip(axes_flat, items):
            if self.is_cancelled():
                ax.axis("off")
                continue
            try:
                sound = self.load_item_sound(item)
                spec = self.spectrogram_data(sound, max_frequency=max_frequency)
                ax.pcolormesh(
                    spec["x"],
                    spec["y"],
                    spec["db"],
                    vmin=spec["vmin"],
                    vmax=spec["vmax"],
                    cmap="Greys",
                    shading="auto",
                )
                duration = float(sound.get_total_duration())
                ax.set_xlim(0.0, duration)
                ax.set_ylim(0.0, spec["max_frequency"])
                ax.set_xticks([0.0, duration])
                ax.tick_params(labelsize=7, length=2)
                if show_formant_arrows:
                    self._draw_formant_arrows(ax, item, duration, spec["max_frequency"])
            except Exception as exc:
                ax.text(0.5, 0.5, f"无法绘制\n{exc}", ha="center", va="center", fontsize=8)
                ax.set_xticks([])
                ax.set_yticks([])

            label = _item_display_label(item, label_field=label_field)
            ax.set_xlabel(f"[ {label} ]" if label else "", fontsize=10)
            ax.grid(True, color="#D1D5DB", linewidth=0.45, alpha=0.65)

        for ax in axes_flat[len(items):]:
            ax.axis("off")

        for row_idx in range(rows):
            axes[row_idx][0].set_ylabel("Hz", fontsize=8)

        fig.tight_layout()
        return fig

    def _draw_formant_arrows(self, ax, item, duration, max_frequency):
        formant = item.get("formant") or {}
        values = []
        for key in ("f1", "f2", "f3"):
            arr = np.asarray(formant.get(key, []), dtype=float)
            arr = arr[np.isfinite(arr) & (arr > 0) & (arr <= max_frequency)]
            if arr.size:
                values.append(float(np.nanmedian(arr)))
        if not values:
            return
        x0 = max(0.0, duration * 0.02)
        x1 = max(duration * 0.16, x0 + 0.001)
        for freq in values[:3]:
            ax.annotate(
                "",
                xy=(x1, freq),
                xytext=(x0, freq),
                arrowprops={"arrowstyle": "-|>", "color": "black", "lw": 1.0},
                zorder=8,
            )

    def _item_target(self, item):
        if not isinstance(item, dict):
            raise ValueError("数据处理操作需要传入工程条目 item 字典。")
        target = {
            "speaker_id": item.get("speaker_id"),
            "speaker_name": item.get("speaker_name"),
            "item_id": item.get("item_id"),
            "label": item.get("label", ""),
        }
        if not target["speaker_id"] or not target["item_id"]:
            raise ValueError("条目缺少 speaker_id 或 item_id，无法生成可写回操作。")
        return target

    def set_item_fields(self, item, fields, reason=""):
        """生成条目字段修改操作。"""
        if not isinstance(fields, dict):
            raise ValueError("set_item_fields 的 fields 必须是字典。")
        return {
            "op": "set_item_fields",
            "target": self._item_target(item),
            "fields": dict(fields),
            "reason": str(reason or ""),
        }

    def recompute_pitch(self, item, params=None, reason=""):
        """生成重算条目 F0 缓存的操作。"""
        return {
            "op": "recompute_pitch",
            "target": self._item_target(item),
            "params": dict(params or {}),
            "reason": str(reason or ""),
        }

    def recompute_formant(self, item, params=None, reason=""):
        """生成重算条目共振峰缓存的操作。"""
        return {
            "op": "recompute_formant",
            "target": self._item_target(item),
            "params": dict(params or {}),
            "reason": str(reason or ""),
        }

    def trim_item_audio(self, item, start=None, end=None, padding=0.0, reason=""):
        """生成裁剪条目音频并替换引用的操作。"""
        return {
            "op": "trim_item_audio",
            "target": self._item_target(item),
            "start": start,
            "end": end,
            "padding": float(padding or 0.0),
            "reason": str(reason or ""),
        }

    def split_project(self, name, item_ids=None, speaker_ids=None, reason=""):
        """生成按发音人或条目拆出新工程的操作。"""
        return {
            "op": "split_project",
            "name": str(name or "拆分工程"),
            "item_ids": list(item_ids or []),
            "speaker_ids": list(speaker_ids or []),
            "reason": str(reason or ""),
        }

    def import_csv_metadata(self, rows, match_on="label", field_map=None, reason=""):
        """
        生成外部表格元数据合并操作。
        第一版不允许脚本读取文件；rows 必须是已经结构化的字典列表。
        """
        if not isinstance(rows, list):
            raise ValueError("import_csv_metadata 的 rows 必须是字典列表。")
        clean_rows = []
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("import_csv_metadata 的每一行必须是字典。")
            clean_rows.append(dict(row))
        return {
            "op": "import_csv_metadata",
            "rows": clean_rows,
            "match_on": str(match_on or "label"),
            "field_map": dict(field_map or {}),
            "reason": str(reason or ""),
        }

    def project_patch(self, operations, title="数据处理脚本结果", description=""):
        """返回工程数据处理结果对象。"""
        return ProjectPatchResult(operations, title=title, description=description)

    def is_cancelled(self):
        """脚本长循环中可调用，用于协作式响应用户取消。"""
        return bool(self._cancel_event and self._cancel_event.is_set())

    def _extract_waveform_for_analysis(self, item, syl_idx=None, time_window=None):
        """从 Sound 对象、条目字典或缓存轨迹中获取用于高阶声学分析的 (samples, sr, f0_hint, f1_hint, f3_hint)。"""
        from modules.data_utils import get_item_syllable_bounds
        f0_hint = f1_hint = f3_hint = np.nan

        if hasattr(item, "values") and hasattr(item, "sampling_frequency"):
            vals = np.asarray(item.values, dtype=float)
            samples = vals.mean(axis=0) if vals.ndim > 1 else vals.ravel()
            sr = float(item.sampling_frequency)
            if time_window is not None and len(time_window) == 2:
                t0, t1 = float(time_window[0]), float(time_window[1])
                xmin = float(getattr(item, "xmin", 0.0))
                i0 = max(0, int(round((t0 - xmin) * sr)))
                i1 = min(samples.size, int(round((t1 - xmin) * sr)))
                if i1 > i0:
                    samples = samples[i0:i1]
            return samples, sr, f0_hint, f1_hint, f3_hint

        if not isinstance(item, dict):
            raise ValueError("高阶声学算子需要传入条目字典 item 或 parselmouth.Sound 对象。")

        # Extract F0/F1/F3 hints from item tracks
        syl_data = item.get("syl_data") or []
        syl_formants = item.get("syl_formants") or []
        if syl_idx is not None and 0 <= int(syl_idx) < len(syl_data):
            f0_pts = np.asarray(syl_data[int(syl_idx)][1], dtype=float)
            vf0 = f0_pts[np.isfinite(f0_pts) & (f0_pts > 0)]
            if vf0.size > 0:
                f0_hint = float(np.median(vf0))
        elif syl_data:
            all_f0 = [f for _, pts in syl_data for f in pts if f is not None and np.isfinite(f) and f > 0]
            if all_f0:
                f0_hint = float(np.median(all_f0))
        elif (item.get("pitch") or {}).get("freqs"):
            pf = np.asarray(item["pitch"]["freqs"], dtype=float)
            vp = pf[np.isfinite(pf) & (pf > 0)]
            if vp.size > 0:
                f0_hint = float(np.median(vp))

        if syl_idx is not None and 0 <= int(syl_idx) < len(syl_formants):
            sf = syl_formants[int(syl_idx)]
            f1_pts = np.asarray(sf.get("f1") or [], dtype=float)
            f3_pts = np.asarray(sf.get("f3") or [], dtype=float)
            vf1 = f1_pts[np.isfinite(f1_pts) & (f1_pts > 0)]
            vf3 = f3_pts[np.isfinite(f3_pts) & (f3_pts > 0)]
            if vf1.size > 0:
                f1_hint = float(np.median(vf1))
            if vf3.size > 0:
                f3_hint = float(np.median(vf3))
        elif (item.get("formant") or {}).get("f1"):
            ff1 = np.asarray(item["formant"].get("f1") or [], dtype=float)
            ff3 = np.asarray(item["formant"].get("f3") or [], dtype=float)
            vf1 = ff1[np.isfinite(ff1) & (ff1 > 0)]
            vf3 = ff3[np.isfinite(ff3) & (ff3 > 0)]
            if vf1.size > 0:
                f1_hint = float(np.median(vf1))
            if vf3.size > 0:
                f3_hint = float(np.median(vf3))

        snd = None
        if self._teproj_path and os.path.exists(self._teproj_path):
            try:
                snd = self.load_item_sound(item)
            except Exception:
                snd = None

        if snd is not None:
            vals = np.asarray(snd.values, dtype=float)
            samples = vals.mean(axis=0) if vals.ndim > 1 else vals.ravel()
            sr = float(snd.sampling_frequency)
            item_start = float(item.get("start") or 0.0)
            if time_window is not None and len(time_window) == 2:
                w0, w1 = float(time_window[0]), float(time_window[1])
                # Support either relative [0, dur] or absolute [start, end] time_window
                if w0 >= item_start and item_start > 0 and w1 <= float(item.get("end") or (item_start + snd.get_total_duration())) + 1e-3:
                    w0 -= item_start
                    w1 -= item_start
                i0 = max(0, int(round(w0 * sr)))
                i1 = min(samples.size, int(round(w1 * sr)))
                if i1 > i0:
                    samples = samples[i0:i1]
            elif syl_idx is not None:
                bounds = get_item_syllable_bounds(item)
                if 0 <= int(syl_idx) < len(bounds):
                    b0, b1 = bounds[int(syl_idx)]
                    w0 = max(0.0, float(b0) - item_start)
                    w1 = max(w0, float(b1) - item_start)
                    i0 = max(0, int(round(w0 * sr)))
                    i1 = min(samples.size, int(round(w1 * sr)))
                    if i1 > i0:
                        samples = samples[i0:i1]
            return samples, sr, f0_hint, f1_hint, f3_hint

        # Fallback: synthesize harmonic signal from item tracks when .teproj audio is not attached
        sr = 16000.0
        dur = float(item.get("duration") or 0.25)
        if dur <= 0:
            dur = 0.25
        n_samp = max(256, int(round(dur * sr)))
        t = np.arange(n_samp, dtype=float) / sr
        f0_use = f0_hint if (np.isfinite(f0_hint) and f0_hint > 0) else 150.0
        f1_use = f1_hint if (np.isfinite(f1_hint) and f1_hint > 0) else 550.0
        f3_use = f3_hint if (np.isfinite(f3_hint) and f3_hint > 0) else 2550.0
        sig = np.zeros(n_samp, dtype=float)
        for h in range(1, 20):
            hf = h * f0_use
            if hf >= sr * 0.45:
                break
            r1 = 1.0 / (1.0 + ((hf - f1_use) / 90.0) ** 2)
            r3 = 0.5 / (1.0 + ((hf - f3_use) / 180.0) ** 2)
            sig += (1.0 / (h ** 1.1)) * (1.0 + 2.0 * r1 + 1.2 * r3) * np.sin(2.0 * np.pi * hf * t)
        return sig, sr, f0_use, f1_use, f3_use

    def extract_voice_quality(self, item, syl_idx=None):
        """
        提取嗓音发声态与频谱倾斜指标：
        返回 {"jitter_local": ..., "shimmer_local": ..., "hnr_db": ..., "cpp_db": ..., "h1_h2_db": ..., "h1_a1_db": ..., "h1_a3_db": ...}
        """
        from scipy import fft as sp_fft, signal as sp_signal
        samples, sr, f0_hint, f1_hint, f3_hint = self._extract_waveform_for_analysis(item, syl_idx=syl_idx)
        if samples is None or samples.size < 64:
            return {
                "jitter_local": np.nan,
                "shimmer_local": np.nan,
                "hnr_db": np.nan,
                "cpp_db": np.nan,
                "h1_h2_db": np.nan,
                "h1_a1_db": np.nan,
                "h1_a3_db": np.nan,
            }

        x = np.asarray(samples, dtype=float).ravel()
        x = x - np.mean(x)
        if float(np.std(x)) <= 1e-12:
            return {
                "jitter_local": np.nan,
                "shimmer_local": np.nan,
                "hnr_db": np.nan,
                "cpp_db": np.nan,
                "h1_h2_db": np.nan,
                "h1_a1_db": np.nan,
                "h1_a3_db": np.nan,
            }

        f_min, f_max = 65.0, 500.0
        if isinstance(item, dict) and item.get("speaker_f0_bounds"):
            b0, b1 = item["speaker_f0_bounds"]
            if np.isfinite(b0) and np.isfinite(b1) and b1 > b0 > 0:
                f_min, f_max = max(50.0, float(b0) * 0.75), min(600.0, float(b1) * 1.25)

        # 1. Autocorrelation for F0 & HNR (Boersma 1993)
        n_fft_acf = int(2 ** math.ceil(math.log2(2 * x.size - 1)))
        spec_acf = sp_fft.rfft(x, n=n_fft_acf)
        acf = sp_fft.irfft(spec_acf * np.conj(spec_acf), n=n_fft_acf)[: x.size]
        acf = acf / max(float(acf[0]), 1e-12)

        min_lag = max(2, int(math.floor(sr / max(f_max, 80.0))))
        max_lag = min(x.size - 2, int(math.ceil(sr / max(f_min, 45.0))))
        if max_lag > min_lag:
            sub_acf = acf[min_lag : max_lag + 1]
            best_rel = int(np.argmax(sub_acf))
            best_lag = min_lag + best_rel
            r_max = float(np.clip(sub_acf[best_rel], 1e-4, 0.9999))
            est_f0 = float(sr / best_lag) if best_lag > 0 else f0_hint
            hnr_db = float(10.0 * math.log10(r_max / (1.0 - r_max)))
        else:
            est_f0 = f0_hint
            hnr_db = np.nan

        if not (np.isfinite(est_f0) and 45.0 <= est_f0 <= 600.0):
            est_f0 = f0_hint if (np.isfinite(f0_hint) and f0_hint > 0) else 150.0

        # 2. Pitch-synchronous cycle peaks for Jitter (local %) & Shimmer (local %)
        exp_period = max(4, int(round(sr / max(est_f0, 50.0))))
        min_dist = max(3, int(round(0.65 * exp_period)))
        peaks, _ = sp_signal.find_peaks(x, distance=min_dist, prominence=0.05 * float(np.std(x)))
        if peaks.size >= 3:
            periods = np.diff(peaks).astype(float) / float(sr)
            med_p = float(np.median(periods))
            valid_p = periods[(periods >= 0.5 * med_p) & (periods <= 1.8 * med_p)]
            if valid_p.size >= 2:
                jitter_local = float(100.0 * np.mean(np.abs(np.diff(valid_p))) / max(float(np.mean(valid_p)), 1e-9))
            else:
                jitter_local = np.nan
            amps = []
            for p_i in range(peaks.size - 1):
                p0, p1 = int(peaks[p_i]), int(peaks[p_i + 1])
                if p1 > p0 + 1:
                    amps.append(float(x[p0] - np.min(x[p0:p1])))
            amps_arr = np.asarray(amps, dtype=float)
            if amps_arr.size >= 2 and float(np.mean(amps_arr)) > 1e-9:
                shimmer_local = float(100.0 * np.mean(np.abs(np.diff(amps_arr))) / float(np.mean(amps_arr)))
            else:
                shimmer_local = np.nan
        else:
            jitter_local = np.nan
            shimmer_local = np.nan

        # 3. Cepstral Peak Prominence (CPP in dB, Hillenbrand et al. 1994)
        win = np.hanning(x.size)
        n_fft = max(2048, int(2 ** math.ceil(math.log2(x.size * 2))))
        spec = sp_fft.rfft(x * win, n=n_fft)
        power = np.abs(spec) ** 2
        log_power_db = 10.0 * np.log10(np.maximum(power, 1e-16))
        cepstrum = np.real(sp_fft.irfft(log_power_db, n=n_fft))
        half_n = n_fft // 2
        quefrency_s = np.arange(half_n, dtype=float) / float(sr)
        cep_db = 20.0 * np.log10(np.maximum(np.abs(cepstrum[:half_n]), 1e-12))
        q_min_reg = max(1, int(round(0.001 * sr)))
        q_max_reg = min(half_n - 1, int(round(0.025 * sr)))
        if q_max_reg > q_min_reg + 2:
            slope, intercept = np.polyfit(quefrency_s[q_min_reg:q_max_reg], cep_db[q_min_reg:q_max_reg], 1)
            q_low = max(q_min_reg, int(math.floor(sr / max(f_max, 60.0))))
            q_high = min(q_max_reg, int(math.ceil(sr / max(f_min, 50.0))))
            if q_high > q_low:
                pk_idx = q_low + int(np.argmax(cep_db[q_low : q_high + 1]))
                cpp_db = float(cep_db[pk_idx] - (slope * quefrency_s[pk_idx] + intercept))
            else:
                cpp_db = np.nan
        else:
            cpp_db = np.nan

        # 4. Harmonic Spectral Tilt (H1-H2, H1-A1, H1-A3 in dB)
        freqs = sp_fft.rfftfreq(n_fft, d=1.0 / float(sr))
        mag_db = 20.0 * np.log10(np.maximum(np.abs(spec), 1e-12))

        def _peak_around(center_hz, half_bw):
            if not (np.isfinite(center_hz) and center_hz > 0):
                return np.nan
            lo = max(20.0, center_hz - half_bw)
            hi = min(sr * 0.49, center_hz + half_bw)
            m = (freqs >= lo) & (freqs <= hi)
            if not np.any(m):
                return float(mag_db[int(np.argmin(np.abs(freqs - center_hz)))])
            return float(np.max(mag_db[m]))

        bw_h = max(18.0, 0.22 * est_f0)
        h1_db = _peak_around(est_f0, bw_h)
        h2_db = _peak_around(2.0 * est_f0, bw_h)

        if not (np.isfinite(f1_hint) and f1_hint > 0):
            m_f1 = (freqs >= 250.0) & (freqs <= 950.0)
            f1_hint = float(freqs[m_f1][int(np.argmax(mag_db[m_f1]))]) if np.any(m_f1) else 500.0
        if not (np.isfinite(f3_hint) and f3_hint > 0):
            m_f3 = (freqs >= 2200.0) & (freqs <= 3600.0)
            f3_hint = float(freqs[m_f3][int(np.argmax(mag_db[m_f3]))]) if np.any(m_f3) else 2500.0

        a1_db = _peak_around(f1_hint, max(60.0, 0.6 * est_f0))
        a3_db = _peak_around(f3_hint, max(100.0, 0.8 * est_f0))

        return {
            "jitter_local": float(jitter_local),
            "shimmer_local": float(shimmer_local),
            "hnr_db": float(hnr_db),
            "cpp_db": float(cpp_db),
            "h1_h2_db": float(h1_db - h2_db),
            "h1_a1_db": float(h1_db - a1_db),
            "h1_a3_db": float(h1_db - a3_db),
        }

    def extract_spectral_moments(self, item, time_window=None, pre_emphasis=80.0):
        """
        提取辅音/擦音频谱前四阶中心矩（Forrest et al. 1988; Jongman et al. 2000）：
        返回 {"cog_hz": ..., "sd_hz": ..., "skewness": ..., "kurtosis": ...}
        """
        from scipy import fft as sp_fft
        samples, sr, _, _, _ = self._extract_waveform_for_analysis(item, time_window=time_window)
        if samples is None or samples.size < 16:
            return {"cog_hz": np.nan, "sd_hz": np.nan, "skewness": np.nan, "kurtosis": np.nan}

        x = np.asarray(samples, dtype=float).ravel()
        x = x - np.mean(x)
        if float(np.std(x)) <= 1e-12:
            return {"cog_hz": np.nan, "sd_hz": np.nan, "skewness": np.nan, "kurtosis": np.nan}

        if pre_emphasis is not None and float(pre_emphasis) > 0.0:
            alpha = math.exp(-2.0 * math.pi * float(pre_emphasis) / float(sr))
            x = np.append(x[0], x[1:] - alpha * x[:-1])

        win = np.hanning(x.size)
        n_fft = max(2048, int(2 ** math.ceil(math.log2(x.size))))
        fft_vals = sp_fft.rfft(x * win, n=n_fft)
        freqs = sp_fft.rfftfreq(n_fft, d=1.0 / float(sr))
        weights = np.abs(fft_vals) ** 2

        if freqs.size > 1:
            freqs = freqs[1:]
            weights = weights[1:]

        total_w = float(np.sum(weights))
        if total_w <= 1e-16:
            return {"cog_hz": np.nan, "sd_hz": np.nan, "skewness": np.nan, "kurtosis": np.nan}

        probs = weights / total_w
        cog_hz = float(np.sum(freqs * probs))
        dev = freqs - cog_hz
        var = float(np.sum(probs * (dev ** 2)))
        sd_hz = math.sqrt(max(0.0, var))
        if sd_hz <= 1e-9:
            return {"cog_hz": cog_hz, "sd_hz": 0.0, "skewness": 0.0, "kurtosis": 0.0}

        m3 = float(np.sum(probs * (dev ** 3)))
        m4 = float(np.sum(probs * (dev ** 4)))
        skewness = float(m3 / (sd_hz ** 3))
        kurtosis = float((m4 / (sd_hz ** 4)) - 3.0)
        return {
            "cog_hz": cog_hz,
            "sd_hz": sd_hz,
            "skewness": skewness,
            "kurtosis": kurtosis,
        }

    def normalize_formants(self, df_or_items, method="lobanov"):
        """
        跨发音人元音共振峰归一化工具函数。
        支持 method = "lobanov" | "nearey1" | "bark" | "erb" | "watt_fabricius"。
        返回带有 f1_norm, f2_norm (及 <method>_f1, <method>_f2) 列的 pandas.DataFrame。
        """
        import pandas as pd
        m = str(method or "lobanov").strip().lower()
        allowed = {"lobanov", "nearey1", "bark", "erb", "watt_fabricius"}
        if m not in allowed:
            raise ValueError(f"不支持的共振峰归一化方法：{method}（可选：{sorted(allowed)}）")

        if isinstance(df_or_items, pd.DataFrame):
            df = df_or_items.copy()
        elif isinstance(df_or_items, DatasetSnapshot):
            df = df_or_items.to_syllable_table(included_only=True, as_df=True)
        elif isinstance(df_or_items, list):
            if df_or_items and isinstance(df_or_items[0], dict) and ("f1_hz" in df_or_items[0] or "f1_mid" in df_or_items[0]):
                df = pd.DataFrame(df_or_items)
            else:
                df = DatasetSnapshot(df_or_items).to_syllable_table(included_only=True, as_df=True)
        else:
            raise ValueError("normalize_formants 需要传入 DataFrame、DatasetSnapshot 或条目列表。")

        f1_col = next((c for c in ("f1_hz", "f1_mid", "f1", "F1") if c in df.columns), None)
        f2_col = next((c for c in ("f2_hz", "f2_mid", "f2", "F2") if c in df.columns), None)
        if not f1_col or not f2_col:
            raise ValueError("输入数据缺少 F1/F2 列（如 f1_hz/f2_hz 或 f1_mid/f2_mid）。")

        spk_col = next((c for c in ("speaker_name", "speaker_id", "speaker") if c in df.columns), None)
        f1_vals = pd.to_numeric(df[f1_col], errors="coerce").to_numpy(dtype=float)
        f2_vals = pd.to_numeric(df[f2_col], errors="coerce").to_numpy(dtype=float)
        f1_vals = np.where(np.isfinite(f1_vals) & (f1_vals > 0), f1_vals, np.nan)
        f2_vals = np.where(np.isfinite(f2_vals) & (f2_vals > 0), f2_vals, np.nan)

        if m == "bark":
            f1_n = _hz_to_bark_arr(f1_vals)
            f2_n = _hz_to_bark_arr(f2_vals)
        elif m == "erb":
            f1_n = _hz_to_erb_arr(f1_vals)
            f2_n = _hz_to_erb_arr(f2_vals)
        else:
            f1_n = np.full_like(f1_vals, np.nan)
            f2_n = np.full_like(f2_vals, np.nan)
            spk_arr = df[spk_col].fillna("默认发音人").astype(str).to_numpy() if spk_col else np.full(len(df), "默认发音人")
            for spk in pd.unique(spk_arr):
                idx = np.where(spk_arr == spk)[0]
                s1 = f1_vals[idx]
                s2 = f2_vals[idx]
                v1 = np.isfinite(s1)
                v2 = np.isfinite(s2)
                if m == "lobanov":
                    if np.sum(v1) >= 2:
                        sd1 = float(np.std(s1[v1], ddof=1))
                        f1_n[idx] = (s1 - float(np.mean(s1[v1]))) / (sd1 if sd1 > 1e-6 else 1.0)
                    if np.sum(v2) >= 2:
                        sd2 = float(np.std(s2[v2], ddof=1))
                        f2_n[idx] = (s2 - float(np.mean(s2[v2]))) / (sd2 if sd2 > 1e-6 else 1.0)
                elif m == "nearey1":
                    if np.any(v1):
                        l1 = np.log(s1)
                        f1_n[idx] = l1 - float(np.nanmean(l1))
                    if np.any(v2):
                        l2 = np.log(s2)
                        f2_n[idx] = l2 - float(np.nanmean(l2))
                elif m == "watt_fabricius":
                    if np.any(v1) and np.any(v2):
                        f1_min, f1_max = float(np.nanmin(s1)), float(np.nanmax(s1))
                        f2_min, f2_max = float(np.nanmin(s2)), float(np.nanmax(s2))
                        s_f1 = (2.0 * f1_min + f1_max) / 3.0
                        s_f2 = (f2_min + f2_max + (f2_min + f2_max) * 0.5) / 3.0
                        f1_n[idx] = s1 / max(s_f1, 1e-6)
                        f2_n[idx] = s2 / max(s_f2, 1e-6)

        df["f1_norm"] = f1_n
        df["f2_norm"] = f2_n
        df[f"{m}_f1"] = f1_n
        df[f"{m}_f2"] = f2_n
        return df


def build_dataset_snapshot(teproj_path):
    """
    解析 .teproj 文件（ZIP格式）中的 project.json 和缓存数据，
    构造脚本所需的只读快照数据。
    """
    if not teproj_path or not os.path.exists(teproj_path):
        return []

    try:
        with zipfile.ZipFile(teproj_path, 'r') as zf:
            try:
                raw = zf.read("project.json")
            except KeyError:
                return []
            project_data = json.loads(raw.decode("utf-8"))
            from modules.script_prompt import _extract_project_groups_and_title, _enrich_item_with_project_meta
            groups_map, default_wl_title = _extract_project_groups_and_title(project_data if isinstance(project_data, dict) else {})

            # Create a lookup mapping from decoded (correct) filenames to original ZIP members
            namelist_map = {}
            for name in zf.namelist():
                try:
                    decoded = name.encode('cp437').decode('gbk')
                except Exception:
                    try:
                        decoded = name.encode('cp437').decode('utf-8')
                    except Exception:
                        decoded = name
                decoded_norm = decoded.replace("\\", "/")
                namelist_map[decoded_norm] = name
                namelist_map[name.replace("\\", "/")] = name

            speakers = project_data.get("speakers", {})
            items_snapshot = []

            # 1. 遍历发音人，计算发音人级别的 F0 分布（用于计算 T 值）
            for spk_id, spk in speakers.items():
                spk_name = spk.get("name", "发音人")
                items = spk.get("items", {})

                # 收集当前发音人所有有效的 F0 频率，用于计算 5% 与 95% 分位数
                f0_pool = []
                loaded_pitches = {}
                loaded_formants = {}

                # 批量读取并预载 npz 缓存
                for item_id, item in items.items():
                    pitch_file = item.get("pitch_data_file")
                    if pitch_file:
                        pitch_file_norm = pitch_file.replace("\\", "/")
                        real_pitch_file = namelist_map.get(pitch_file_norm)
                        if real_pitch_file:
                            try:
                                with zf.open(real_pitch_file) as pf:
                                    with np.load(pf) as data:
                                        xs = data["xs"].tolist()
                                        freqs = data["freqs"].tolist()
                                        loaded_pitches[item_id] = (xs, freqs)
                                        f0_pool.extend([f for f in freqs if f > 0])
                            except Exception:
                                pass

                    formant_file = item.get("formant_data_file")
                    if formant_file:
                        formant_file_norm = formant_file.replace("\\", "/")
                        real_formant_file = namelist_map.get(formant_file_norm)
                        if real_formant_file:
                            try:
                                with zf.open(real_formant_file) as ff:
                                    with np.load(ff) as data:
                                        xs = data["xs"].tolist()
                                        f1 = data["f1"].tolist()
                                        f2 = data["f2"].tolist()
                                        f3 = data["f3"].tolist() if "f3" in data else []
                                        loaded_formants[item_id] = (xs, f1, f2, f3)
                            except Exception:
                                pass

                # 计算基准
                s_min, s_max = get_f0_normalization_bounds(f0_pool)

                if s_max > s_min and s_min > 0:
                    log_s_min = math.log10(s_min)
                    log_s_max_min = math.log10(s_max) - log_s_min
                else:
                    log_s_min = 0.0
                    log_s_max_min = 1.0

                # 2. 填充条目明细快照
                for item_id, item in items.items():
                    start = item.get("start", 0.0)
                    end = item.get("end", 0.0)
                    duration = end - start

                    # 计算 F0 的 T 值
                    xs_p, freqs = loaded_pitches.get(item_id, ([], []))
                    t_values = []
                    for f in freqs:
                        if f > 0:
                            if s_max > s_min and s_min > 0:
                                t = 5.0 * (math.log10(f) - log_s_min) / log_s_max_min
                                t = max(0.0, min(5.0, t))
                            else:
                                t = 2.5
                            t_values.append(t)
                        else:
                            t_values.append(np.nan)

                    # 共振峰数据
                    loaded_val = loaded_formants.get(item_id, ([], [], [], []))
                    if len(loaded_val) == 3:
                        xs_f, f1, f2 = loaded_val
                        f3 = []
                    else:
                        xs_f, f1, f2, f3 = loaded_val

                    f1_clean = [f if not np.isnan(f) else np.nan for f in f1]
                    f2_clean = [f if not np.isnan(f) else np.nan for f in f2]
                    f3_clean = [f if not np.isnan(f) else np.nan for f in f3]

                    # 提取每个音节的 F0 数据 (syl_data)、T值数据 (syl_t_values) 与共振峰数据 (syl_formants)
                    syl_data = []
                    syl_t_values = []
                    syl_formants = []
                    try:
                        from modules.data_utils import get_item_syllable_bounds, split_into_syllables, sample_formant_points_by_bounds_extended
                        
                        # 构造临时字典用于获取音节边界和单字
                        tmp_item = {
                            "start": start,
                            "end": end,
                            "label": item.get("label", ""),
                            "chars_bounds": item.get("chars_bounds", []),
                            "inner_splits": item.get("inner_splits", [])
                        }
                        bounds = get_item_syllable_bounds(tmp_item)
                        syls = split_into_syllables(tmp_item["label"])

                        # 1. 提取 F0 syl_data & syl_t_values
                        if xs_p and freqs:
                            p_xs_arr = np.asarray(xs_p)
                            p_freqs_arr = np.asarray(freqs)
                            p_t_arr = np.asarray(t_values)
                            for c_s, c_e in bounds:
                                if c_e <= c_s:
                                    syl_data.append((0.0, [0.0]*11))
                                    syl_t_values.append([np.nan]*11)
                                    continue
                                valid_idx = np.where((p_xs_arr >= c_s) & (p_xs_arr <= c_e) & (p_freqs_arr > 0))[0]
                                if len(valid_idx) >= 2:
                                    v_s, v_e = p_xs_arr[valid_idx[0]], p_xs_arr[valid_idx[-1]]
                                    seg_xs = p_xs_arr[valid_idx]
                                    seg_ys = p_freqs_arr[valid_idx]
                                    seg_ts = p_t_arr[valid_idx]
                                else:
                                    syl_data.append((0.0, [0.0]*11))
                                    syl_t_values.append([np.nan]*11)
                                    continue
                                dur = v_e - v_s
                                if dur <= 0:
                                    syl_data.append((0.0, [0.0]*11))
                                    syl_t_values.append([np.nan]*11)
                                    continue
                                times_p = np.linspace(v_s, v_e, 11)
                                f0s = np.interp(times_p, seg_xs, seg_ys).tolist()
                                ts_vals = np.interp(times_p, seg_xs, seg_ts).tolist()
                                for j, t in enumerate(times_p):
                                    if np.min(np.abs(seg_xs - t)) > 0.025:
                                        f0s[j] = 0.0
                                        ts_vals[j] = np.nan
                                syl_data.append((dur, f0s))
                                syl_t_values.append(ts_vals)

                        # 2. 提取共振峰 syl_formants
                        if xs_f and f1_clean and f2_clean:
                            f_data_mock = {
                                "xs": np.asarray(xs_f),
                                "f1": np.asarray(f1_clean),
                                "f2": np.asarray(f2_clean)
                            }
                            if f3_clean:
                                f_data_mock["f3"] = np.asarray(f3_clean)

                            mock_item = {
                                "start": start,
                                "end": end,
                                "label": item.get("label", ""),
                                "chars_bounds": item.get("chars_bounds", []),
                                "inner_splits": item.get("inner_splits", []),
                                "formant_data": f_data_mock
                            }
                            strategy = item.get("formant_sample_strategy", spk.get("last_params", {}).get("formant_sample_strategy", "整段11点"))
                            show_f3 = bool(item.get("show_f3", spk.get("last_params", {}).get("show_f3", False)))
                            res_samp = sample_formant_points_by_bounds_extended(mock_item, bounds, 11, strategy, include_f3=show_f3)
                            times_f = res_samp["times"]
                            f1_vals = res_samp["f1"]
                            f2_vals = res_samp["f2"]
                            f3_vals = res_samp["f3"]

                            for idx_syl, (c_s, c_e) in enumerate(bounds):
                                char = syls[idx_syl] if idx_syl < len(syls) else f"字{idx_syl+1}"
                                s_idx = idx_syl * 11
                                e_idx = s_idx + 11
                                s_times = times_f[s_idx:e_idx]
                                s_f1 = f1_vals[s_idx:e_idx]
                                s_f2 = f2_vals[s_idx:e_idx]
                                s_dict = {
                                    "syllable_index": idx_syl,
                                    "char": char,
                                    "bounds": [c_s, c_e],
                                    "times": s_times,
                                    "f1": s_f1,
                                    "f2": s_f2
                                }
                                if show_f3:
                                    s_dict["f3"] = f3_vals[s_idx:e_idx]
                                syl_formants.append(s_dict)
                    except Exception as ex:
                        print(f"Error extracting syllable data in snapshot: {ex}")

                    enriched = _enrich_item_with_project_meta(item, groups_map, default_wl_title)
                    grp_name = enriched.get("group", "默认组")
                    grp_info = groups_map.get(grp_name, {}) if isinstance(groups_map, dict) else {}
                    item_tags_list = list(enriched.get("item_tags") or enriched.get("tags") or [])
                    item_note_str = str(enriched.get("item_note") or enriched.get("note") or "")
                    item_aliases_list = list(enriched.get("item_aliases") or enriched.get("aliases") or [])
                    item_meta_dict = dict(enriched.get("fields") or enriched.get("custom_fields") or enriched.get("item_meta") or enriched.get("meta") or {})
                    snapshot_item = {
                        "speaker_id": spk_id,
                        "speaker_name": spk_name,
                        "speaker_f0_bounds": (s_min, s_max),
                        "item_id": item_id,
                        "label": enriched.get("label", ""),
                        "word": enriched.get("label", ""),
                        "group": grp_name,
                        "wordlist_version": "v2" if (
                            enriched.get("wordlist_version") == "v2"
                            or enriched.get("wordlist_title")
                            or default_wl_title
                            or item_tags_list
                            or item_meta_dict
                            or item_note_str
                            or item_aliases_list
                            or enriched.get("group_tags") or grp_info.get("tags")
                            or enriched.get("group_note") or grp_info.get("note")
                        ) else enriched.get("wordlist_version", "v1"),
                        "wordlist_title": enriched.get("wordlist_title") or default_wl_title or "",
                        "note": item_note_str,
                        "item_note": item_note_str,
                        "tags": list(item_tags_list),
                        "item_tags": list(item_tags_list),
                        "aliases": list(item_aliases_list),
                        "item_aliases": list(item_aliases_list),
                        "fields": dict(item_meta_dict),
                        "custom_fields": dict(item_meta_dict),
                        "item_meta": dict(item_meta_dict),
                        "group_note": enriched.get("group_note") or grp_info.get("note") or "",
                        "group_tags": list(enriched.get("group_tags") or grp_info.get("tags") or []),
                        "metadata_source": enriched.get("metadata_source", ""),
                        "is_excluded": item.get("is_excluded", False),
                        "exclusion_reason": item.get("exclusion_reason", ""),
                        "param_overrides": dict(item.get("param_overrides", {}) or {}),
                        "inner_splits": list(item.get("inner_splits", []) or []),
                        "chars_bounds": list(item.get("chars_bounds", []) or []),
                        "split_warnings": list(item.get("split_warnings", []) or []),
                        "split_confidence": float(item.get("split_confidence", 1.0)),
                        "analysis_mode": item.get("analysis_mode", spk.get("last_params", {}).get("analysis_mode", "f0")),
                        "start": start,
                        "end": end,
                        "duration": duration,
                        "pitch": {
                            "xs": xs_p,
                            "freqs": freqs,
                            "t_values": t_values
                        },
                        "formant": {
                            "xs": xs_f,
                            "f1": f1_clean,
                            "f2": f2_clean,
                            "f3": f3_clean
                        },
                        "syl_data": syl_data,
                        "syl_t_values": syl_t_values,
                        "syl_formants": syl_formants
                    }
                    items_snapshot.append(snapshot_item)

            return items_snapshot
    except Exception as e:
        print(f"Error building dataset snapshot: {e}")
        return []
