# -*- coding: utf-8 -*-
"""
modules.export_service.praat_collection_exporter - Praat .Collection 工程集合导出模块
支持将 PhonTracer 中的音频 (Sound 2)、三层标注 (TextGrid)、经橡皮擦/参数精修后的基频 (Pitch 1)
以及共振峰 (Formant 2) 一键打包为 Praat 原生 .Collection 文件（支持 ooBinaryFile 与 ooTextFile），
在 Praat / Praat_ZH 中打开即可自动解包到对象列表 (Objects list)。
"""

from __future__ import annotations

import io
import math
import os
import re
import struct
from typing import Any, BinaryIO, Dict, List, Optional, Sequence, Tuple, Union

import numpy as np
import textgrid

from ..core.text_io import make_textgrid_export_stem, split_into_syllables
from .textgrid_io import get_export_textgrid_for_item


def clean_praat_object_name(name: str, fallback: str = "untitled") -> str:
    """
    按 Praat `praat_cleanUpName` 规则清洗对象名称：
    将空格、点号、括号、斜杠等替换为下划线，确保在 Praat 对象列表中显示规范。
    """
    raw = str(name or "").strip()
    if not raw:
        return fallback
    cleaned = re.sub(r"[\s.()\[\]{}:/\\,;\"'<>|?*]+", "_", raw)
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    return cleaned or fallback


# ============================================================================
# Low-level Praat Binary (ooBinaryFile) & Text (ooTextFile) Primitives
# ============================================================================

def _binputu8(val: int) -> bytes:
    return struct.pack(">B", int(val) & 0xFF)


def _binputi16(val: int) -> bytes:
    return struct.pack(">h", int(val))


def _binputu16(val: int) -> bytes:
    return struct.pack(">H", int(val) & 0xFFFF)


def _binputi32(val: int) -> bytes:
    return struct.pack(">i", int(val))


def _binputr64(val: float) -> bytes:
    v = float(val)
    if math.isnan(v) or math.isinf(v):
        v = 0.0
    return struct.pack(">d", v)


def _binputw8(text: str) -> bytes:
    s = str(text or "")
    if len(s) > 254:
        s = s[:254]
    if s.isascii():
        raw = s.encode("ascii")
        return struct.pack(">B", len(raw)) + raw
    utf16_be = s.encode("utf-16-be")
    return struct.pack(">BB", 0xFF, len(s)) + utf16_be


def _binputw16(text: str) -> bytes:
    s = str(text or "")
    if len(s) > 65534:
        s = s[:65534]
    if s.isascii():
        raw = s.encode("ascii")
        return struct.pack(">H", len(raw)) + raw
    utf16_be = s.encode("utf-16-be")
    return struct.pack(">HH", 0xFFFF, len(s)) + utf16_be


def _escape_praat_text(text: str) -> str:
    return str(text or "").replace('"', '""')


# ============================================================================
# Object Serializers (Sound 2, TextGrid, Pitch 1, Formant 2)
# ============================================================================

def serialize_sound_binary(
    values: np.ndarray,
    sampling_frequency: float,
    xmin: float = 0.0,
    xmax: Optional[float] = None,
    x1: Optional[float] = None,
) -> bytes:
    """
    将波形数组序列化为 Praat `Sound 2` 的二进制载荷（不含 Collection item 头）。
    values 形状可为 (nx,) 或 (ny, nx)。
    """
    arr = np.asarray(values, dtype=np.float64)
    if arr.ndim == 1:
        arr = arr.reshape(1, -1)
    elif arr.ndim > 2:
        arr = arr.reshape(arr.shape[0], -1)

    ny, nx = int(arr.shape[0]), int(arr.shape[1])
    sf = float(sampling_frequency) if sampling_frequency and sampling_frequency > 0 else 44100.0
    dx = 1.0 / sf
    xmin_f = float(xmin)
    xmax_f = float(xmax) if xmax is not None else xmin_f + nx * dx
    if xmax_f <= xmin_f:
        xmax_f = xmin_f + max(nx, 1) * dx
    x1_f = float(x1) if x1 is not None else xmin_f + 0.5 * dx

    header = b"".join([
        _binputr64(xmin_f),         # Function.xmin
        _binputr64(xmax_f),         # Function.xmax
        _binputi32(nx),             # Sampled.nx
        _binputr64(dx),             # Sampled.dx
        _binputr64(x1_f),           # Sampled.x1
        _binputr64(1.0),            # SampledXY.ymin
        _binputr64(float(ny)),      # SampledXY.ymax
        _binputi32(ny),             # SampledXY.ny
        _binputr64(1.0),            # SampledXY.dy
        _binputr64(1.0),            # SampledXY.y1
    ])
    clean_arr = np.where(np.isfinite(arr), arr, 0.0)
    matrix_bytes = np.ascontiguousarray(clean_arr, dtype=">f8").tobytes()
    return header + matrix_bytes


def serialize_sound_text(
    values: np.ndarray,
    sampling_frequency: float,
    xmin: float = 0.0,
    xmax: Optional[float] = None,
    x1: Optional[float] = None,
    indent: str = "        ",
) -> str:
    """将波形数组序列化为 Praat `Sound 2` 的 ooTextFile 文本块。"""
    arr = np.asarray(values, dtype=np.float64)
    if arr.ndim == 1:
        arr = arr.reshape(1, -1)
    elif arr.ndim > 2:
        arr = arr.reshape(arr.shape[0], -1)

    ny, nx = int(arr.shape[0]), int(arr.shape[1])
    sf = float(sampling_frequency) if sampling_frequency and sampling_frequency > 0 else 44100.0
    dx = 1.0 / sf
    xmin_f = float(xmin)
    xmax_f = float(xmax) if xmax is not None else xmin_f + nx * dx
    if xmax_f <= xmin_f:
        xmax_f = xmin_f + max(nx, 1) * dx
    x1_f = float(x1) if x1 is not None else xmin_f + 0.5 * dx

    lines = [
        f"{indent}xmin = {xmin_f} ",
        f"{indent}xmax = {xmax_f} ",
        f"{indent}nx = {nx} ",
        f"{indent}dx = {dx} ",
        f"{indent}x1 = {x1_f} ",
        f"{indent}ymin = 1 ",
        f"{indent}ymax = {ny} ",
        f"{indent}ny = {ny} ",
        f"{indent}dy = 1 ",
        f"{indent}y1 = 1 ",
        f"{indent}z [] []: ",
    ]
    clean_arr = np.where(np.isfinite(arr), arr, 0.0)
    for ch_idx in range(ny):
        lines.append(f"{indent}    z [{ch_idx + 1}] []:")
        row = clean_arr[ch_idx]
        for s_idx in range(nx):
            lines.append(f"{indent}        z [{ch_idx + 1}] [{s_idx + 1}] = {float(row[s_idx])} ")
    return "\n".join(lines)


def serialize_textgrid_binary(tg: textgrid.TextGrid) -> bytes:
    """将 `textgrid.TextGrid` 对象序列化为 Praat `TextGrid` 的二进制载荷。"""
    xmin_f = float(getattr(tg, "minTime", 0.0) or 0.0)
    xmax_f = float(getattr(tg, "maxTime", 1.0) or 1.0)
    if xmax_f <= xmin_f:
        xmax_f = xmin_f + 1.0

    tiers = list(getattr(tg, "tiers", []))
    chunks = [
        _binputr64(xmin_f),         # Function.xmin
        _binputr64(xmax_f),         # Function.xmax
        _binputu8(1),               # oo_OBJECT(FunctionList, 0, tiers) -> binputex(true)
        _binputi32(len(tiers)),     # FunctionList.size
    ]

    for tier in tiers:
        t_min = float(getattr(tier, "minTime", xmin_f) if getattr(tier, "minTime", None) is not None else xmin_f)
        t_max = float(getattr(tier, "maxTime", xmax_f) if getattr(tier, "maxTime", None) is not None else xmax_f)
        if t_max <= t_min:
            t_max = xmax_f
        t_name = str(getattr(tier, "name", "") or "tier")

        if isinstance(tier, textgrid.PointTier):
            chunks.append(_binputw8("TextTier"))
            chunks.append(_binputw16(t_name))
            chunks.append(_binputr64(t_min))
            chunks.append(_binputr64(t_max))
            points = list(tier)
            chunks.append(_binputi32(len(points)))
            for pt in points:
                chunks.append(_binputr64(float(pt.time)))
                chunks.append(_binputw16(str(pt.mark or "")))
        else:
            chunks.append(_binputw8("IntervalTier"))
            chunks.append(_binputw16(t_name))
            chunks.append(_binputr64(t_min))
            chunks.append(_binputr64(t_max))
            intervals = list(tier)
            if not intervals:
                chunks.append(_binputi32(1))
                chunks.append(_binputr64(t_min))
                chunks.append(_binputr64(t_max))
                chunks.append(_binputw16(""))
            else:
                chunks.append(_binputi32(len(intervals)))
                for iv in intervals:
                    iv_min = float(iv.minTime)
                    iv_max = float(iv.maxTime)
                    chunks.append(_binputr64(iv_min))
                    chunks.append(_binputr64(iv_max))
                    chunks.append(_binputw16(str(iv.mark or "")))

    return b"".join(chunks)


def serialize_textgrid_text(tg: textgrid.TextGrid, indent: str = "        ") -> str:
    """将 `textgrid.TextGrid` 对象序列化为 Praat `TextGrid` 的 ooTextFile 文本块。"""
    xmin_f = float(getattr(tg, "minTime", 0.0) or 0.0)
    xmax_f = float(getattr(tg, "maxTime", 1.0) or 1.0)
    if xmax_f <= xmin_f:
        xmax_f = xmin_f + 1.0

    tiers = list(getattr(tg, "tiers", []))
    lines = [
        f"{indent}xmin = {xmin_f} ",
        f"{indent}xmax = {xmax_f} ",
        f"{indent}tiers? <exists> ",
        f"{indent}size = {len(tiers)} ",
        f"{indent}item []: ",
    ]
    for idx, tier in enumerate(tiers, start=1):
        t_min = float(getattr(tier, "minTime", xmin_f) if getattr(tier, "minTime", None) is not None else xmin_f)
        t_max = float(getattr(tier, "maxTime", xmax_f) if getattr(tier, "maxTime", None) is not None else xmax_f)
        if t_max <= t_min:
            t_max = xmax_f
        t_name = _escape_praat_text(getattr(tier, "name", "") or "tier")
        lines.append(f"{indent}    item [{idx}]:")
        if isinstance(tier, textgrid.PointTier):
            points = list(tier)
            lines.append(f'{indent}        class = "TextTier" ')
            lines.append(f'{indent}        name = "{t_name}" ')
            lines.append(f"{indent}        xmin = {t_min} ")
            lines.append(f"{indent}        xmax = {t_max} ")
            lines.append(f"{indent}        points: size = {len(points)} ")
            for p_idx, pt in enumerate(points, start=1):
                lines.append(f"{indent}        points [{p_idx}]:")
                lines.append(f"{indent}            number = {float(pt.time)} ")
                lines.append(f'{indent}            mark = "{_escape_praat_text(pt.mark or "")}" ')
        else:
            intervals = list(tier)
            lines.append(f'{indent}        class = "IntervalTier" ')
            lines.append(f'{indent}        name = "{t_name}" ')
            lines.append(f"{indent}        xmin = {t_min} ")
            lines.append(f"{indent}        xmax = {t_max} ")
            if not intervals:
                lines.append(f"{indent}        intervals: size = 1 ")
                lines.append(f"{indent}        intervals [1]:")
                lines.append(f"{indent}            xmin = {t_min} ")
                lines.append(f"{indent}            xmax = {t_max} ")
                lines.append(f'{indent}            text = "" ')
            else:
                lines.append(f"{indent}        intervals: size = {len(intervals)} ")
                for iv_idx, iv in enumerate(intervals, start=1):
                    lines.append(f"{indent}        intervals [{iv_idx}]:")
                    lines.append(f"{indent}            xmin = {float(iv.minTime)} ")
                    lines.append(f"{indent}            xmax = {float(iv.maxTime)} ")
                    lines.append(f'{indent}            text = "{_escape_praat_text(iv.mark or "")}" ')
    return "\n".join(lines)


def _normalize_uniform_grid(
    xs: np.ndarray,
    xmin: float,
    xmax: float,
    default_dx: float = 0.01,
) -> Tuple[int, float, float, np.ndarray]:
    """确保时间轴满足 Praat Sampled 等步长要求 (nx, dx, x1, grid_xs)。"""
    xs_arr = np.asarray(xs, dtype=np.float64).ravel()
    if len(xs_arr) == 0:
        dx = float(default_dx)
        dur = max(float(xmax) - float(xmin), dx)
        nx = max(1, int(round(dur / dx)))
        x1 = float(xmin) + 0.5 * (dur - (nx - 1) * dx)
        if x1 <= xmin:
            x1 = float(xmin) + 0.5 * dx
        return nx, dx, x1, x1 + np.arange(nx, dtype=np.float64) * dx

    if len(xs_arr) == 1:
        dx = float(default_dx)
        x1 = float(xs_arr[0])
        if x1 < xmin:
            x1 = float(xmin) + 0.5 * dx
        return 1, dx, x1, np.array([x1], dtype=np.float64)

    diffs = np.diff(xs_arr)
    valid_diffs = diffs[diffs > 1e-6]
    dx = float(np.median(valid_diffs)) if len(valid_diffs) > 0 else float(default_dx)
    if dx <= 0:
        dx = float(default_dx)

    is_uniform = len(valid_diffs) == len(diffs) and np.allclose(diffs, dx, atol=1e-4, rtol=1e-2)
    if is_uniform:
        nx = len(xs_arr)
        x1 = float(xs_arr[0])
        return nx, dx, x1, xs_arr

    # 若由多段拼接导致非严格等间隔，则投射到统一的等间隔网格
    t_first = max(float(xmin) + 0.5 * dx, float(np.min(xs_arr)))
    t_last = min(float(xmax) - 0.5 * dx, float(np.max(xs_arr)))
    if t_last < t_first:
        t_first = float(xmin) + 0.5 * dx
        t_last = t_first
    nx = max(1, int(round((t_last - t_first) / dx)) + 1)
    grid_xs = t_first + np.arange(nx, dtype=np.float64) * dx
    return nx, dx, t_first, grid_xs


def _resample_nearest_within_tolerance(
    src_xs: np.ndarray,
    src_vals: np.ndarray,
    grid_xs: np.ndarray,
    max_tol: float,
    fill_value: float = 0.0,
) -> np.ndarray:
    """将 (src_xs, src_vals) 映射到等步长 grid_xs，保留无声段 0/NaN 不被错误插值桥接。"""
    src_xs_arr = np.asarray(src_xs, dtype=np.float64).ravel()
    src_vals_arr = np.asarray(src_vals, dtype=np.float64).ravel()
    if len(src_xs_arr) == 0 or len(src_vals_arr) == 0:
        return np.full(len(grid_xs), fill_value, dtype=np.float64)

    if len(src_xs_arr) == len(grid_xs) and np.allclose(src_xs_arr, grid_xs, atol=1e-4, rtol=1e-2):
        return src_vals_arr.copy()

    order = np.argsort(src_xs_arr)
    src_xs_sorted = src_xs_arr[order]
    src_vals_sorted = src_vals_arr[order]

    idx_right = np.searchsorted(src_xs_sorted, grid_xs, side="left")
    idx_left = np.clip(idx_right - 1, 0, len(src_xs_sorted) - 1)
    idx_right = np.clip(idx_right, 0, len(src_xs_sorted) - 1)

    dist_left = np.abs(grid_xs - src_xs_sorted[idx_left])
    dist_right = np.abs(grid_xs - src_xs_sorted[idx_right])
    use_right = dist_right < dist_left
    nearest_idx = np.where(use_right, idx_right, idx_left)
    nearest_dist = np.minimum(dist_left, dist_right)

    out = src_vals_sorted[nearest_idx].copy()
    out[nearest_dist > max_tol] = fill_value
    return out


def serialize_pitch_binary(
    xs: np.ndarray,
    freqs: np.ndarray,
    xmin: float = 0.0,
    xmax: Optional[float] = None,
    ceiling: float = 600.0,
) -> bytes:
    """
    将 (xs, freqs) 基频数据（含橡皮擦置零后的点）序列化为 Praat `Pitch 1` 二进制载荷。
    """
    xs_arr = np.asarray(xs, dtype=np.float64).ravel()
    freqs_arr = np.asarray(freqs, dtype=np.float64).ravel()
    xmin_f = float(xmin)
    xmax_f = float(xmax) if xmax is not None else (float(xs_arr[-1]) + 0.01 if len(xs_arr) > 0 else xmin_f + 1.0)
    if xmax_f <= xmin_f:
        xmax_f = xmin_f + 1.0
    ceiling_f = float(ceiling) if ceiling and ceiling > 0 else 600.0

    nx, dx, x1, grid_xs = _normalize_uniform_grid(xs_arr, xmin_f, xmax_f, default_dx=0.01)
    grid_freqs = _resample_nearest_within_tolerance(xs_arr, freqs_arr, grid_xs, max_tol=dx * 0.75, fill_value=0.0)
    grid_freqs = np.where(np.isfinite(grid_freqs) & (grid_freqs > 0) & (grid_freqs < ceiling_f), grid_freqs, 0.0)

    header = b"".join([
        _binputr64(xmin_f),         # Function.xmin
        _binputr64(xmax_f),         # Function.xmax
        _binputi32(nx),             # Sampled.nx
        _binputr64(dx),             # Sampled.dx
        _binputr64(x1),             # Sampled.x1
        _binputr64(ceiling_f),      # Pitch.ceiling
        _binputi16(2),              # Pitch.maxnCandidates (voiced + unvoiced or unvoiced)
    ])

    # 向量化构建全部帧二进制流:
    # 每个 Pitch_Frame 固定为 1 个候选: intensity (r64) + nCandidates=1 (i32) + frequency (r64) + strength (r64)
    frame_dtype = np.dtype([
        ("intensity", ">f8"),
        ("nCandidates", ">i4"),
        ("frequency", ">f8"),
        ("strength", ">f8"),
    ])
    frames = np.empty(nx, dtype=frame_dtype)
    voiced = grid_freqs > 0.0
    frames["intensity"] = np.where(voiced, 1.0, 0.0)
    frames["nCandidates"] = 1
    frames["frequency"] = grid_freqs
    frames["strength"] = np.where(voiced, 0.9, 0.0)

    return header + frames.tobytes()


def serialize_pitch_text(
    xs: np.ndarray,
    freqs: np.ndarray,
    xmin: float = 0.0,
    xmax: Optional[float] = None,
    ceiling: float = 600.0,
    indent: str = "        ",
) -> str:
    """将 (xs, freqs) 基频数据序列化为 Praat `Pitch 1` 的 ooTextFile 文本块。"""
    xs_arr = np.asarray(xs, dtype=np.float64).ravel()
    freqs_arr = np.asarray(freqs, dtype=np.float64).ravel()
    xmin_f = float(xmin)
    xmax_f = float(xmax) if xmax is not None else (float(xs_arr[-1]) + 0.01 if len(xs_arr) > 0 else xmin_f + 1.0)
    if xmax_f <= xmin_f:
        xmax_f = xmin_f + 1.0
    ceiling_f = float(ceiling) if ceiling and ceiling > 0 else 600.0

    nx, dx, x1, grid_xs = _normalize_uniform_grid(xs_arr, xmin_f, xmax_f, default_dx=0.01)
    grid_freqs = _resample_nearest_within_tolerance(xs_arr, freqs_arr, grid_xs, max_tol=dx * 0.75, fill_value=0.0)
    grid_freqs = np.where(np.isfinite(grid_freqs) & (grid_freqs > 0) & (grid_freqs < ceiling_f), grid_freqs, 0.0)

    lines = [
        f"{indent}xmin = {xmin_f} ",
        f"{indent}xmax = {xmax_f} ",
        f"{indent}nx = {nx} ",
        f"{indent}dx = {dx} ",
        f"{indent}x1 = {x1} ",
        f"{indent}ceiling = {ceiling_f} ",
        f"{indent}maxnCandidates = 2 ",
        f"{indent}frames []: ",
    ]
    for i in range(nx):
        f_val = float(grid_freqs[i])
        voiced = f_val > 0.0
        intensity = 1.0 if voiced else 0.0
        strength = 0.9 if voiced else 0.0
        lines.extend([
            f"{indent}    frames [{i + 1}]:",
            f"{indent}        intensity = {intensity} ",
            f"{indent}        nCandidates = 1 ",
            f"{indent}        candidates []: ",
            f"{indent}            candidates [1]:",
            f"{indent}                frequency = {f_val} ",
            f"{indent}                strength = {strength} ",
        ])
    return "\n".join(lines)


def serialize_formant_binary(
    xs: np.ndarray,
    f1: np.ndarray,
    f2: np.ndarray,
    f3: Optional[np.ndarray] = None,
    xmin: float = 0.0,
    xmax: Optional[float] = None,
    maxn_formants: int = 5,
) -> bytes:
    """
    将共振峰数据（含橡皮擦置为 NaN 的点）序列化为 Praat `Formant 2` 二进制载荷。
    """
    xs_arr = np.asarray(xs, dtype=np.float64).ravel()
    f1_arr = np.asarray(f1, dtype=np.float64).ravel()
    f2_arr = np.asarray(f2, dtype=np.float64).ravel()
    f3_arr = np.asarray(f3, dtype=np.float64).ravel() if f3 is not None else np.full_like(f1_arr, np.nan)

    xmin_f = float(xmin)
    xmax_f = float(xmax) if xmax is not None else (float(xs_arr[-1]) + 0.01 if len(xs_arr) > 0 else xmin_f + 1.0)
    if xmax_f <= xmin_f:
        xmax_f = xmin_f + 1.0

    nx, dx, x1, grid_xs = _normalize_uniform_grid(xs_arr, xmin_f, xmax_f, default_dx=0.00625)
    g_f1 = _resample_nearest_within_tolerance(xs_arr, f1_arr, grid_xs, max_tol=dx * 0.85, fill_value=np.nan)
    g_f2 = _resample_nearest_within_tolerance(xs_arr, f2_arr, grid_xs, max_tol=dx * 0.85, fill_value=np.nan)
    g_f3 = _resample_nearest_within_tolerance(xs_arr, f3_arr, grid_xs, max_tol=dx * 0.85, fill_value=np.nan)

    chunks = [
        _binputr64(xmin_f),                     # Function.xmin
        _binputr64(xmax_f),                     # Function.xmax
        _binputi32(nx),                         # Sampled.nx
        _binputr64(dx),                         # Sampled.dx
        _binputr64(x1),                         # Sampled.x1
        _binputi16(max(int(maxn_formants), 3)), # Formant.maxnFormants
    ]

    for i in range(nx):
        ok1 = bool(np.isfinite(g_f1[i]) and g_f1[i] > 0)
        ok2 = bool(np.isfinite(g_f2[i]) and g_f2[i] > 0)
        ok3 = bool(np.isfinite(g_f3[i]) and g_f3[i] > 0)

        # 若核心共振峰 F1 与 F2 均为空/已被橡皮擦擦除，则整帧记为空帧
        if not ok1 and not ok2:
            chunks.append(_binputr64(0.0))
            chunks.append(_binputi16(0))
            continue

        raw_slots = [(ok1, g_f1[i]), (ok2, g_f2[i]), (ok3, g_f3[i])]
        n_slots = 3 if ok3 else (2 if ok2 else 1)
        chunks.append(_binputr64(1.0))
        chunks.append(_binputi16(n_slots))
        for ok_flag, val in raw_slots[:n_slots]:
            if ok_flag:
                freq = float(val)
                bw = max(50.0, round(freq * 0.10, 2))
                chunks.append(_binputr64(freq))
                chunks.append(_binputr64(bw))
            else:
                chunks.append(struct.pack(">dd", float("nan"), float("nan")))

    return b"".join(chunks)


def serialize_formant_text(
    xs: np.ndarray,
    f1: np.ndarray,
    f2: np.ndarray,
    f3: Optional[np.ndarray] = None,
    xmin: float = 0.0,
    xmax: Optional[float] = None,
    maxn_formants: int = 5,
    indent: str = "        ",
) -> str:
    """将共振峰数据序列化为 Praat `Formant 2` 的 ooTextFile 文本块。"""
    xs_arr = np.asarray(xs, dtype=np.float64).ravel()
    f1_arr = np.asarray(f1, dtype=np.float64).ravel()
    f2_arr = np.asarray(f2, dtype=np.float64).ravel()
    f3_arr = np.asarray(f3, dtype=np.float64).ravel() if f3 is not None else np.full_like(f1_arr, np.nan)

    xmin_f = float(xmin)
    xmax_f = float(xmax) if xmax is not None else (float(xs_arr[-1]) + 0.01 if len(xs_arr) > 0 else xmin_f + 1.0)
    if xmax_f <= xmin_f:
        xmax_f = xmin_f + 1.0

    nx, dx, x1, grid_xs = _normalize_uniform_grid(xs_arr, xmin_f, xmax_f, default_dx=0.00625)
    g_f1 = _resample_nearest_within_tolerance(xs_arr, f1_arr, grid_xs, max_tol=dx * 0.85, fill_value=np.nan)
    g_f2 = _resample_nearest_within_tolerance(xs_arr, f2_arr, grid_xs, max_tol=dx * 0.85, fill_value=np.nan)
    g_f3 = _resample_nearest_within_tolerance(xs_arr, f3_arr, grid_xs, max_tol=dx * 0.85, fill_value=np.nan)

    lines = [
        f"{indent}xmin = {xmin_f} ",
        f"{indent}xmax = {xmax_f} ",
        f"{indent}nx = {nx} ",
        f"{indent}dx = {dx} ",
        f"{indent}x1 = {x1} ",
        f"{indent}maxnFormants = {max(int(maxn_formants), 3)} ",
        f"{indent}frames []: ",
    ]
    for i in range(nx):
        ok1 = bool(np.isfinite(g_f1[i]) and g_f1[i] > 0)
        ok2 = bool(np.isfinite(g_f2[i]) and g_f2[i] > 0)
        ok3 = bool(np.isfinite(g_f3[i]) and g_f3[i] > 0)

        lines.append(f"{indent}    frames [{i + 1}]:")
        if not ok1 and not ok2:
            lines.append(f"{indent}        intensity = 0.0 ")
            lines.append(f"{indent}        numberOfFormants = 0 ")
            lines.append(f"{indent}        formant []: (empty)")
            continue

        raw_slots = [(ok1, g_f1[i]), (ok2, g_f2[i]), (ok3, g_f3[i])]
        n_slots = 3 if ok3 else (2 if ok2 else 1)
        lines.append(f"{indent}        intensity = 1.0 ")
        lines.append(f"{indent}        numberOfFormants = {n_slots} ")
        lines.append(f"{indent}        formant []: ")
        for k, (ok_flag, val) in enumerate(raw_slots[:n_slots], start=1):
            lines.append(f"{indent}            formant [{k}]:")
            if ok_flag:
                freq = float(val)
                bw = max(50.0, round(freq * 0.10, 2))
                lines.append(f"{indent}                frequency = {freq} ")
                lines.append(f"{indent}                bandwidth = {bw} ")
            else:
                lines.append(f"{indent}                frequency = --undefined-- ")
                lines.append(f"{indent}                bandwidth = --undefined-- ")
    return "\n".join(lines)


# ============================================================================
# Collection Writer
# ============================================================================

def write_praat_collection(
    out_path: str,
    items: Sequence[Dict[str, Any]],
    binary: bool = True,
) -> str:
    """
    将对象列表写入 `.Collection` 文件。
    每个 item 字典结构:
      - {"class": "Sound 2", "name": "...", "values": ..., "sampling_frequency": ..., "xmin": ..., "xmax": ...}
      - {"class": "TextGrid", "name": "...", "textgrid": tg}
      - {"class": "Pitch 1", "name": "...", "xs": ..., "freqs": ..., "xmin": ..., "xmax": ..., "ceiling": ...}
      - {"class": "Formant 2", "name": "...", "xs": ..., "f1": ..., "f2": ..., "f3": ..., "xmin": ..., "xmax": ...}
    """
    if not items:
        raise ValueError("没有可写入 Praat .Collection 的有效对象。")

    abs_path = os.path.abspath(out_path)
    os.makedirs(os.path.dirname(abs_path) or ".", exist_ok=True)

    if binary:
        with open(abs_path, "wb") as f:
            f.write(b"ooBinaryFile")
            f.write(_binputw8("Collection"))
            f.write(_binputi32(len(items)))
            for entry in items:
                cls_name = str(entry["class"])
                obj_name = clean_praat_object_name(entry.get("name", "untitled"))
                f.write(_binputw8(cls_name))
                f.write(_binputw16(obj_name))
                if cls_name == "Sound 2":
                    f.write(
                        serialize_sound_binary(
                            entry["values"],
                            entry["sampling_frequency"],
                            xmin=entry.get("xmin", 0.0),
                            xmax=entry.get("xmax"),
                            x1=entry.get("x1"),
                        )
                    )
                elif cls_name == "TextGrid":
                    f.write(serialize_textgrid_binary(entry["textgrid"]))
                elif cls_name == "Pitch 1":
                    f.write(
                        serialize_pitch_binary(
                            entry["xs"],
                            entry["freqs"],
                            xmin=entry.get("xmin", 0.0),
                            xmax=entry.get("xmax"),
                            ceiling=entry.get("ceiling", 600.0),
                        )
                    )
                elif cls_name == "Formant 2":
                    f.write(
                        serialize_formant_binary(
                            entry["xs"],
                            entry["f1"],
                            entry["f2"],
                            f3=entry.get("f3"),
                            xmin=entry.get("xmin", 0.0),
                            xmax=entry.get("xmax"),
                            maxn_formants=entry.get("maxn_formants", 5),
                        )
                    )
                else:
                    raise ValueError(f"不支持的 Praat 对象类型: {cls_name}")
    else:
        lines = [
            'File type = "ooTextFile"',
            'Object class = "Collection"',
            "",
            f"size = {len(items)} ",
            "item []: ",
        ]
        for idx, entry in enumerate(items, start=1):
            cls_name = str(entry["class"])
            obj_name = _escape_praat_text(clean_praat_object_name(entry.get("name", "untitled")))
            lines.append(f"    item [{idx}]:")
            lines.append(f'        class = "{cls_name}" ')
            lines.append(f'        name = "{obj_name}" ')
            if cls_name == "Sound 2":
                lines.append(
                    serialize_sound_text(
                        entry["values"],
                        entry["sampling_frequency"],
                        xmin=entry.get("xmin", 0.0),
                        xmax=entry.get("xmax"),
                        x1=entry.get("x1"),
                    )
                )
            elif cls_name == "TextGrid":
                lines.append(serialize_textgrid_text(entry["textgrid"]))
            elif cls_name == "Pitch 1":
                lines.append(
                    serialize_pitch_text(
                        entry["xs"],
                        entry["freqs"],
                        xmin=entry.get("xmin", 0.0),
                        xmax=entry.get("xmax"),
                        ceiling=entry.get("ceiling", 600.0),
                    )
                )
            elif cls_name == "Formant 2":
                lines.append(
                    serialize_formant_text(
                        entry["xs"],
                        entry["f1"],
                        entry["f2"],
                        f3=entry.get("f3"),
                        xmin=entry.get("xmin", 0.0),
                        xmax=entry.get("xmax"),
                        maxn_formants=entry.get("maxn_formants", 5),
                    )
                )
            else:
                raise ValueError(f"不支持的 Praat 对象类型: {cls_name}")
        with open(abs_path, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(lines) + "\n")

    return abs_path


# ============================================================================
# High-Level Builders from PhonTracer Items & Speakers
# ============================================================================

def build_combined_textgrid_from_items(
    items: Sequence[Dict[str, Any]],
    item_to_group: Optional[Dict[int, str]] = None,
    max_time: float = 0.0,
) -> textgrid.TextGrid:
    """
    将一组处于同一时间轴上的词条合并为包含 `groups` / `words` / `chars` 的标准三层 `TextGrid`。
    """
    valid_items = [it for it in items if it.get("start") is not None and it.get("end") is not None]
    valid_items.sort(key=lambda x: float(x.get("start", 0.0)))

    computed_max = float(max_time or 0.0)
    for it in valid_items:
        if it.get("snd") is not None:
            try:
                dur = float(it["snd"].get_total_duration())
                if dur > computed_max:
                    computed_max = dur
            except Exception:
                pass
        if float(it["end"]) > computed_max:
            computed_max = float(it["end"])
    if computed_max <= 0.0:
        computed_max = 1.0

    tg = textgrid.TextGrid(minTime=0.0, maxTime=computed_max)
    word_tier = textgrid.IntervalTier(name="words", minTime=0.0, maxTime=computed_max)
    char_tier = textgrid.IntervalTier(name="chars", minTime=0.0, maxTime=computed_max)
    group_tier = textgrid.IntervalTier(name="groups", minTime=0.0, maxTime=computed_max)

    last_word_end = 0.0
    last_char_end = 0.0
    last_group_end = 0.0

    for item in valid_items:
        t_s = min(max(0.0, float(item["start"])), computed_max)
        t_e = min(max(t_s, float(item["end"])), computed_max)
        label = str(item.get("label", "") or "")
        inner_splits = item.get("inner_splits", []) or []
        grp_name = (
            item_to_group.get(id(item))
            if item_to_group and id(item) in item_to_group
            else item.get("group", "导入内容")
        ) or "导入内容"

        # Words tier
        w_s = max(t_s, last_word_end)
        if t_e > w_s:
            if w_s > last_word_end:
                word_tier.add(last_word_end, w_s, "")
            word_tier.add(w_s, t_e, label)
            last_word_end = t_e

        # Groups tier
        g_s = max(t_s, last_group_end)
        if t_e > g_s:
            if g_s > last_group_end:
                group_tier.add(last_group_end, g_s, "")
            group_tier.add(g_s, t_e, str(grp_name))
            last_group_end = t_e

        # Chars tier
        syls = split_into_syllables(label)
        if len(syls) > 1:
            c_s_start = max(t_s, last_char_end)
            if c_s_start > last_char_end:
                char_tier.add(last_char_end, c_s_start, "")

            chars_bounds = item.get("chars_bounds", [])
            if not chars_bounds:
                splits = [t_s] + [float(s) for s in inner_splits if t_s < float(s) < t_e] + [t_e]
                if len(splits) != len(syls) + 1:
                    splits = np.linspace(t_s, t_e, len(syls) + 1).tolist()
                chars_bounds = [(splits[j], splits[j + 1]) for j in range(len(splits) - 1)]

            local_last = c_s_start
            for i, syl in enumerate(syls):
                if i < len(chars_bounds):
                    c_s, c_e = float(chars_bounds[i][0]), float(chars_bounds[i][1])
                    c_s = min(max(c_s, local_last), computed_max)
                    c_e = min(c_e, computed_max)
                    if c_e > c_s:
                        if c_s > local_last:
                            char_tier.add(local_last, c_s, "")
                        char_tier.add(c_s, c_e, syl)
                        local_last = c_e

            if local_last < t_e:
                char_tier.add(local_last, t_e, "")
                local_last = t_e
            last_char_end = local_last
        else:
            c_s = max(t_s, last_char_end)
            if t_e > c_s:
                if c_s > last_char_end:
                    char_tier.add(last_char_end, c_s, "")
                char_tier.add(c_s, t_e, label)
                last_char_end = t_e

    if computed_max > last_word_end:
        word_tier.add(last_word_end, computed_max, "")
    if computed_max > last_char_end:
        char_tier.add(last_char_end, computed_max, "")
    if computed_max > last_group_end:
        group_tier.add(last_group_end, computed_max, "")

    tg.append(group_tier)
    tg.append(word_tier)
    tg.append(char_tier)
    return tg


def _extract_sound_payload(snd: Any) -> Optional[Dict[str, Any]]:
    """从 parselmouth.Sound（或兼容 Sound 对象）中提取波形矩阵、采样率与起止时间。"""
    if snd is None:
        return None
    try:
        vals = np.asarray(snd.values, dtype=np.float64)
        sf = float(snd.sampling_frequency)
        xmin = float(getattr(snd, "xmin", 0.0) or 0.0)
        xmax = float(getattr(snd, "xmax", xmin + vals.shape[-1] / sf) or (xmin + vals.shape[-1] / sf))
        x1 = float(getattr(snd, "x1", xmin + 0.5 / sf) or (xmin + 0.5 / sf))
        return {
            "values": vals,
            "sampling_frequency": sf,
            "xmin": xmin,
            "xmax": xmax,
            "x1": x1,
        }
    except Exception:
        return None


def _extract_item_pitch_arrays(item: Dict[str, Any]) -> Tuple[Optional[np.ndarray], Optional[np.ndarray]]:
    if item.get("pitch_data") and "xs" in item["pitch_data"] and "freqs" in item["pitch_data"]:
        xs = np.asarray(item["pitch_data"]["xs"], dtype=np.float64).ravel()
        freqs = np.asarray(item["pitch_data"]["freqs"], dtype=np.float64).ravel()
        if len(xs) > 0 and len(xs) == len(freqs):
            return xs, freqs
    if item.get("pitch") is not None:
        try:
            pitch = item["pitch"]
            xs = np.asarray(pitch.xs(), dtype=np.float64).ravel()
            freqs = np.asarray(pitch.selected_array["frequency"], dtype=np.float64).ravel()
            if len(xs) > 0 and len(xs) == len(freqs):
                return xs, freqs
        except Exception:
            pass
    return None, None


def build_collection_items_for_audio_group(
    obj_base_name: str,
    items: Sequence[Dict[str, Any]],
    item_to_group: Optional[Dict[int, str]] = None,
    long_snd: Any = None,
    default_ceiling: float = 600.0,
) -> List[Dict[str, Any]]:
    """
    针对共享同一段音频（长音频或单条独立音频）的一组词条，构建对应的
    `Sound 2` + `TextGrid` + `Pitch 1` (+ `Formant 2` 若有共振峰数据) 对象列表。
    """
    clean_name = clean_praat_object_name(obj_base_name)
    valid_items = [it for it in items if it.get("start") is not None and it.get("end") is not None]
    if not valid_items:
        return []

    # 1. 获取主音频 Sound
    primary_snd = long_snd
    if primary_snd is None:
        for it in valid_items:
            if it.get("snd") is not None:
                primary_snd = it["snd"]
                break
            if it.get("path") and os.path.exists(str(it["path"])):
                try:
                    from ..dsp.sound_loader import load_compatible_sound
                    primary_snd = load_compatible_sound(str(it["path"]))
                    it["snd"] = primary_snd
                    break
                except Exception:
                    pass

    snd_payload = _extract_sound_payload(primary_snd)
    max_time = float(snd_payload["xmax"]) if snd_payload else max(float(it["end"]) for it in valid_items)
    if max_time <= 0.0:
        max_time = 1.0

    result_objects: List[Dict[str, Any]] = []
    if snd_payload is not None:
        result_objects.append({
            "class": "Sound 2",
            "name": clean_name,
            **snd_payload,
        })

    # 2. 构建 TextGrid
    tg = build_combined_textgrid_from_items(valid_items, item_to_group=item_to_group, max_time=max_time)
    result_objects.append({
        "class": "TextGrid",
        "name": clean_name,
        "textgrid": tg,
    })

    # 3. 构建合并了橡皮擦擦除和逐词参数微调的 Pitch 1
    dx_pitch = 0.01
    nx_pitch = max(1, int(round(max_time / dx_pitch)))
    x1_pitch = 0.5 * dx_pitch
    grid_xs = x1_pitch + np.arange(nx_pitch, dtype=np.float64) * dx_pitch
    merged_freqs = np.zeros(nx_pitch, dtype=np.float64)
    has_any_pitch = False
    max_ceiling = float(default_ceiling or 600.0)

    # 若单条或长音频本身已有全局 pitch，先铺底
    if len(valid_items) == 1:
        p_xs, p_freqs = _extract_item_pitch_arrays(valid_items[0])
        if p_xs is not None and p_freqs is not None:
            has_any_pitch = True
            item_ceil = float(valid_items[0].get("pitch_ceiling", max_ceiling) or max_ceiling)
            max_ceiling = max(max_ceiling, item_ceil)
            result_objects.append({
                "class": "Pitch 1",
                "name": clean_name,
                "xs": p_xs,
                "freqs": p_freqs,
                "xmin": 0.0,
                "xmax": max_time,
                "ceiling": max_ceiling,
            })
    else:
        for it in valid_items:
            p_xs, p_freqs = _extract_item_pitch_arrays(it)
            if p_xs is None or p_freqs is None:
                continue
            has_any_pitch = True
            item_ceil = float(it.get("pitch_ceiling", max_ceiling) or max_ceiling)
            max_ceiling = max(max_ceiling, item_ceil)

            # 确定该词条覆盖区间 (优先 macro_start..macro_end，否则 start..end)
            seg_s = float(it.get("macro_start") if it.get("macro_start") is not None else it.get("start", 0.0))
            seg_e = float(it.get("macro_end") if it.get("macro_end") is not None else it.get("end", max_time))
            seg_s = min(seg_s, float(np.min(p_xs)))
            seg_e = max(seg_e, float(np.max(p_xs)))

            grid_mask = (grid_xs >= seg_s) & (grid_xs <= seg_e)
            if np.any(grid_mask):
                sub_grid = grid_xs[grid_mask]
                sub_freqs = _resample_nearest_within_tolerance(p_xs, p_freqs, sub_grid, max_tol=dx_pitch * 0.85, fill_value=0.0)
                merged_freqs[grid_mask] = sub_freqs

        if has_any_pitch:
            result_objects.append({
                "class": "Pitch 1",
                "name": clean_name,
                "xs": grid_xs,
                "freqs": merged_freqs,
                "xmin": 0.0,
                "xmax": max_time,
                "ceiling": max_ceiling,
            })

    # 4. 构建合并了橡皮擦擦除的 Formant 2（若存在 formant_data）
    items_with_formant = [it for it in valid_items if it.get("formant_data") and "xs" in it["formant_data"]]
    if items_with_formant:
        if len(valid_items) == 1 and len(items_with_formant) == 1:
            fd = items_with_formant[0]["formant_data"]
            result_objects.append({
                "class": "Formant 2",
                "name": clean_name,
                "xs": np.asarray(fd["xs"], dtype=np.float64),
                "f1": np.asarray(fd.get("f1", []), dtype=np.float64),
                "f2": np.asarray(fd.get("f2", []), dtype=np.float64),
                "f3": np.asarray(fd["f3"], dtype=np.float64) if fd.get("f3") is not None else None,
                "xmin": 0.0,
                "xmax": max_time,
                "maxn_formants": 5,
            })
        else:
            dx_fmt = 0.00625
            nx_fmt = max(1, int(round(max_time / dx_fmt)))
            x1_fmt = 0.5 * dx_fmt
            fmt_grid_xs = x1_fmt + np.arange(nx_fmt, dtype=np.float64) * dx_fmt
            merged_f1 = np.full(nx_fmt, np.nan, dtype=np.float64)
            merged_f2 = np.full(nx_fmt, np.nan, dtype=np.float64)
            merged_f3 = np.full(nx_fmt, np.nan, dtype=np.float64)

            for it in items_with_formant:
                fd = it["formant_data"]
                f_xs = np.asarray(fd["xs"], dtype=np.float64).ravel()
                if len(f_xs) == 0:
                    continue
                f1_arr = np.asarray(fd.get("f1", []), dtype=np.float64).ravel()
                f2_arr = np.asarray(fd.get("f2", []), dtype=np.float64).ravel()
                f3_arr = np.asarray(fd["f3"], dtype=np.float64).ravel() if fd.get("f3") is not None else np.full_like(f1_arr, np.nan)

                seg_s = float(np.min(f_xs))
                seg_e = float(np.max(f_xs))
                grid_mask = (fmt_grid_xs >= seg_s) & (fmt_grid_xs <= seg_e)
                if np.any(grid_mask):
                    sub_grid = fmt_grid_xs[grid_mask]
                    merged_f1[grid_mask] = _resample_nearest_within_tolerance(f_xs, f1_arr, sub_grid, max_tol=dx_fmt * 0.85, fill_value=np.nan)
                    merged_f2[grid_mask] = _resample_nearest_within_tolerance(f_xs, f2_arr, sub_grid, max_tol=dx_fmt * 0.85, fill_value=np.nan)
                    merged_f3[grid_mask] = _resample_nearest_within_tolerance(f_xs, f3_arr, sub_grid, max_tol=dx_fmt * 0.85, fill_value=np.nan)

            result_objects.append({
                "class": "Formant 2",
                "name": clean_name,
                "xs": fmt_grid_xs,
                "f1": merged_f1,
                "f2": merged_f2,
                "f3": merged_f3,
                "xmin": 0.0,
                "xmax": max_time,
                "maxn_formants": 5,
            })

    return result_objects


def build_single_item_collection_objects(
    item: Dict[str, Any],
    long_snd: Any = None,
    default_ceiling: float = 600.0,
) -> List[Dict[str, Any]]:
    """
    针对单个词条构建 Praat Collection 对象列表（支持独立音频词条或从长音频中切出该词条的独立片段并归零时间轴）。
    """
    if not item or item.get("start") is None or item.get("end") is None:
        return []

    has_own_path = bool(item.get("path") and os.path.exists(str(item["path"])))
    snd = item.get("snd") or long_snd
    if has_own_path:
        base_name = make_textgrid_export_stem(str(item["path"]), item.get("label", ""))
        return build_collection_items_for_audio_group(
            obj_base_name=base_name,
            items=[item],
            long_snd=snd,
            default_ceiling=default_ceiling,
        )

    # 长音频中的单个词条：切取 [macro_start, macro_end] 并平移时间轴到 0..dur
    t_s = float(item["start"])
    t_e = float(item["end"])
    mac_s = float(item.get("macro_start") if item.get("macro_start") is not None else max(0.0, t_s - 0.05))
    mac_e = float(item.get("macro_end") if item.get("macro_end") is not None else t_e + 0.05)
    mac_s = min(mac_s, t_s)
    mac_e = max(mac_e, t_e)
    dur = max(mac_e - mac_s, 0.01)

    shifted_item = dict(item)
    shifted_item["start"] = t_s - mac_s
    shifted_item["end"] = t_e - mac_s
    if item.get("inner_splits"):
        shifted_item["inner_splits"] = [float(s) - mac_s for s in item["inner_splits"]]
    if item.get("chars_bounds"):
        shifted_item["chars_bounds"] = [[float(cs) - mac_s, float(ce) - mac_s] for cs, ce in item["chars_bounds"]]

    p_xs, p_freqs = _extract_item_pitch_arrays(item)
    if p_xs is not None and p_freqs is not None:
        mask = (p_xs >= mac_s) & (p_xs <= mac_e)
        shifted_item["pitch_data"] = {
            "xs": (p_xs[mask] - mac_s).copy(),
            "freqs": p_freqs[mask].copy(),
        }

    if item.get("formant_data") and "xs" in item["formant_data"]:
        fd = item["formant_data"]
        f_xs = np.asarray(fd["xs"], dtype=np.float64)
        mask = (f_xs >= mac_s) & (f_xs <= mac_e)
        shifted_fd = dict(fd)
        shifted_fd["xs"] = (f_xs[mask] - mac_s).copy()
        for k in ("f1", "f2", "f3"):
            if fd.get(k) is not None:
                arr = np.asarray(fd[k], dtype=np.float64)
                if len(arr) == len(f_xs):
                    shifted_fd[k] = arr[mask].copy()
        shifted_item["formant_data"] = shifted_fd

    sliced_snd = None
    if snd is not None:
        try:
            sliced_snd = snd.extract_part(from_time=mac_s, to_time=mac_e, preserve_times=False)
        except Exception:
            sliced_snd = None

    shifted_item["snd"] = sliced_snd
    base_name = clean_praat_object_name(item.get("label", "item"))
    return build_collection_items_for_audio_group(
        obj_base_name=base_name,
        items=[shifted_item],
        long_snd=sliced_snd,
        default_ceiling=default_ceiling,
    )


def build_speaker_collection_objects(
    items_dict: Dict[str, Dict[str, Any]],
    tree_structure: Sequence[Tuple[str, Sequence[str]]],
    is_batch_mode: bool = False,
    long_snd: Any = None,
    speaker_name: str = "",
    fallback_stem: str = "phontracer_workspace",
    ensure_loaded_fn: Optional[Any] = None,
    default_ceiling: float = 600.0,
) -> List[Dict[str, Any]]:
    """
    将单个发音人（长音频模式或批量短音频模式）构建为 Praat Collection 对象字典列表。
    """
    flat_items: List[Dict[str, Any]] = []
    item_to_group: Dict[int, str] = {}

    for grp_name, children in tree_structure:
        for child_iid in children:
            item = items_dict.get(child_iid)
            if not item or item.get("is_excluded", False):
                continue
            if ensure_loaded_fn is not None:
                try:
                    ensure_loaded_fn(item)
                except Exception:
                    pass
            if item.get("start") is not None and item.get("end") is not None:
                flat_items.append(item)
                item_to_group[id(item)] = grp_name

    if not flat_items:
        return []

    collection_objects: List[Dict[str, Any]] = []
    prefix = f"{clean_praat_object_name(speaker_name)}_" if speaker_name else ""

    # 判断是否为独立短音频模式：显式指定 is_batch_mode 或词条带有独立 path
    has_distinct_paths = any(bool(it.get("path")) for it in flat_items)
    if is_batch_mode or (has_distinct_paths and long_snd is None):
        path_to_items: Dict[str, List[Dict[str, Any]]] = {}
        for idx, it in enumerate(flat_items, start=1):
            p = str(it.get("path") or f"item_{idx}")
            path_to_items.setdefault(p, []).append(it)

        used_stems: Dict[str, int] = {}
        for p, p_items in path_to_items.items():
            raw_stem = make_textgrid_export_stem(p, p_items[0].get("label", "") if p_items else "")
            stem = clean_praat_object_name(f"{prefix}{raw_stem}")
            cnt = used_stems.get(stem, 0) + 1
            used_stems[stem] = cnt
            if cnt > 1:
                stem = f"{stem}_{cnt}"

            objs = build_collection_items_for_audio_group(
                obj_base_name=stem,
                items=p_items,
                item_to_group=item_to_group,
                long_snd=p_items[0].get("snd") if p_items else None,
                default_ceiling=default_ceiling,
            )
            collection_objects.extend(objs)
    else:
        base_stem = clean_praat_object_name(
            speaker_name or fallback_stem or "phontracer_workspace"
        )
        collection_objects.extend(
            build_collection_items_for_audio_group(
                obj_base_name=base_stem,
                items=flat_items,
                item_to_group=item_to_group,
                long_snd=long_snd,
                default_ceiling=default_ceiling,
            )
        )

    return collection_objects


def export_speaker_to_praat_collection(
    out_path: str,
    items_dict: Dict[str, Dict[str, Any]],
    tree_structure: Sequence[Tuple[str, Sequence[str]]],
    is_batch_mode: bool = False,
    long_snd: Any = None,
    speaker_name: str = "",
    ensure_loaded_fn: Optional[Any] = None,
    default_ceiling: float = 600.0,
    binary: bool = True,
) -> str:
    """
    将单个发音人（长音频模式或批量短音频模式）导出为 `.Collection` 文件。
    """
    fallback_stem = os.path.splitext(os.path.basename(out_path))[0] or "phontracer_workspace"
    collection_objects = build_speaker_collection_objects(
        items_dict=items_dict,
        tree_structure=tree_structure,
        is_batch_mode=is_batch_mode,
        long_snd=long_snd,
        speaker_name=speaker_name,
        fallback_stem=fallback_stem,
        ensure_loaded_fn=ensure_loaded_fn,
        default_ceiling=default_ceiling,
    )
    if not collection_objects:
        raise ValueError("没有可导出的有效标注词条。")

    return write_praat_collection(out_path, collection_objects, binary=binary)


__all__ = [
    "clean_praat_object_name",
    "serialize_sound_binary",
    "serialize_sound_text",
    "serialize_textgrid_binary",
    "serialize_textgrid_text",
    "serialize_pitch_binary",
    "serialize_pitch_text",
    "serialize_formant_binary",
    "serialize_formant_text",
    "write_praat_collection",
    "build_combined_textgrid_from_items",
    "build_collection_items_for_audio_group",
    "build_single_item_collection_objects",
    "build_speaker_collection_objects",
    "export_speaker_to_praat_collection",
]
