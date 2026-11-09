# -*- coding: utf-8 -*-
"""
只读数据集快照（DatasetSnapshot）、预展开长宽表构建器与全局工程/音频缓存。
"""

import json
import math
import os
import zipfile
from collections import OrderedDict

import numpy as np

from .models import (
    _build_zip_member_lookup,
    _coerce_time,
    _hz_to_bark_arr,
    _hz_to_erb_arr,
    _item_display_label,
    _normalize_audio_resource_path,
    get_f0_normalization_bounds,
)

# 全局共享缓存引用（必须与 modules/script_api.py 门面保持同一 OrderedDict 实例）
_GLOBAL_PROJECT_JSON_CACHE = OrderedDict()
_GLOBAL_SOUND_LRU_CACHE = OrderedDict()
_MAX_GLOBAL_SOUND_CACHE = 12
_SOUND_LRU_MAX_ENTRIES = _MAX_GLOBAL_SOUND_CACHE


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
            from .prompt_builder import _extract_project_groups_and_title, _enrich_item_with_project_meta
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
                            "inner_splits": item.get("inner_splits", []),
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
                                    syl_data.append((0.0, [0.0] * 11))
                                    syl_t_values.append([np.nan] * 11)
                                    continue
                                valid_idx = np.where((p_xs_arr >= c_s) & (p_xs_arr <= c_e) & (p_freqs_arr > 0))[0]
                                if len(valid_idx) >= 2:
                                    v_s, v_e = p_xs_arr[valid_idx[0]], p_xs_arr[valid_idx[-1]]
                                    seg_xs = p_xs_arr[valid_idx]
                                    seg_ys = p_freqs_arr[valid_idx]
                                    seg_ts = p_t_arr[valid_idx]
                                else:
                                    syl_data.append((0.0, [0.0] * 11))
                                    syl_t_values.append([np.nan] * 11)
                                    continue
                                dur = v_e - v_s
                                if dur <= 0:
                                    syl_data.append((0.0, [0.0] * 11))
                                    syl_t_values.append([np.nan] * 11)
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
                                "f2": np.asarray(f2_clean),
                            }
                            if f3_clean:
                                f_data_mock["f3"] = np.asarray(f3_clean)

                            mock_item = {
                                "start": start,
                                "end": end,
                                "label": item.get("label", ""),
                                "chars_bounds": item.get("chars_bounds", []),
                                "inner_splits": item.get("inner_splits", []),
                                "formant_data": f_data_mock,
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
                                    "f2": s_f2,
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
                            "t_values": t_values,
                        },
                        "formant": {
                            "xs": xs_f,
                            "f1": f1_clean,
                            "f2": f2_clean,
                            "f3": f3_clean,
                        },
                        "syl_data": syl_data,
                        "syl_t_values": syl_t_values,
                        "syl_formants": syl_formants,
                    }
                    items_snapshot.append(snapshot_item)

            return items_snapshot
    except Exception as e:
        print(f"Error building dataset snapshot: {e}")
        return []
