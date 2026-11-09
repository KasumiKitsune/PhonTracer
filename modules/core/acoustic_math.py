# -*- coding: utf-8 -*-
"""
PhonTracer 基础声学数学与统计计算核心模块 (Layer 0 - 零业务与零 GUI 依赖)
包含五度值基频上下限计算、Hz<->T值换算、五度值整型编码拟合、安全 NaN 统计、2D 凸包面积与 Pillai-Bartlett 迹计算。
"""
from __future__ import annotations

import math
import warnings
from typing import Dict, Iterable, List, Optional, Tuple, Union

import numpy as np

try:
    from scipy.spatial import ConvexHull
except ImportError:
    ConvexHull = None

DEFAULT_F0_BOUNDS: Tuple[float, float] = (75.0, 600.0)


def compute_speaker_f0_bounds(
    f0_values: Union[Iterable[float], Dict[str, Iterable[float]]],
    method: str = "p5_p95_iqr",
) -> Tuple[float, float]:
    """
    权威统一的发音人五度值基频上下限 (f_min, f_max) 计算函数。

    Parameters
    ----------
    f0_values : Iterable[float] or Dict[str, Iterable[float]]
        发音人有效基频采样序列，或按调类分组的基频字典。
    method : str
        - "p5_p95_iqr" (默认): 样本数 >= 16 时先以 1.5*IQR 剔除八度跳变等离群点，再取 5%–95% 分位数；
          极小样本 (< 16) 回退为绝对极值 min/max。
        - "raw_minmax": 直接使用有效正值的绝对极值 (min, max)。
        - "group_mean_minmax": 按各组均值曲线的极值计算。
    """
    if method == "group_mean_minmax" and isinstance(f0_values, dict):
        group_vals = []
        for vals in f0_values.values():
            arr = np.asarray(list(vals), dtype=float).ravel()
            valid = arr[np.isfinite(arr) & (arr > 0.0)]
            if valid.size > 0:
                group_vals.append(float(np.min(valid)))
                group_vals.append(float(np.max(valid)))
        if len(group_vals) >= 2:
            f_min, f_max = float(np.min(group_vals)), float(np.max(group_vals))
            if f_max > f_min > 0.0:
                return f_min, f_max

    if isinstance(f0_values, dict):
        flat = []
        for vals in f0_values.values():
            flat.extend(vals)
        arr = np.asarray(flat, dtype=float).ravel()
    else:
        arr = np.asarray(
            list(f0_values) if not isinstance(f0_values, np.ndarray) else f0_values,
            dtype=float,
        ).ravel()

    arr = arr[np.isfinite(arr) & (arr > 0.0)]
    if arr.size == 0:
        return DEFAULT_F0_BOUNDS

    if method == "raw_minmax" or arr.size < 16:
        s_min = float(np.min(arr))
        s_max = float(np.max(arr))
        if s_max > s_min > 0.0:
            return s_min, s_max
        return DEFAULT_F0_BOUNDS

    robust_arr = arr
    if arr.size >= 8:
        q1, q3 = np.percentile(arr, [25.0, 75.0])
        iqr = q3 - q1
        if iqr > 0.0:
            low = q1 - 1.5 * iqr
            high = q3 + 1.5 * iqr
            filtered = arr[(arr >= low) & (arr <= high)]
            if filtered.size >= max(8, int(arr.size * 0.5)):
                robust_arr = filtered

    s_min = float(np.percentile(robust_arr, 5.0))
    s_max = float(np.percentile(robust_arr, 95.0))
    if s_max > s_min and s_min > 0.0:
        return s_min, s_max
    s_min = float(np.min(arr))
    s_max = float(np.max(arr))
    if s_max > s_min and s_min > 0.0:
        return s_min, s_max
    return DEFAULT_F0_BOUNDS


def hz_to_t_value(f0_hz, f_min, f_max, clip: bool = True):
    """
    权威统一的赵元任/石锋五度值 T 值换算公式（映射到 [0.0, 5.0] 区间）：
        T = 5.0 * (log10(f) - log10(f_min)) / (log10(f_max) - log10(f_min))

    支持标量或 numpy 数组输入；对 <= 0 或 NaN 的无效频率返回 None（标量）或 np.nan（数组）。
    """
    if f0_hz is None:
        return None

    valid_bounds = (
        f_min is not None
        and f_max is not None
        and np.isfinite(f_min)
        and np.isfinite(f_max)
        and float(f_min) > 0.0
        and float(f_max) > float(f_min)
    )
    if valid_bounds:
        log_min = math.log10(float(f_min))
        log_diff = math.log10(float(f_max)) - log_min
    else:
        log_min = 0.0
        log_diff = 1.0

    if np.isscalar(f0_hz):
        try:
            hz = float(f0_hz)
        except (TypeError, ValueError):
            return None
        if not np.isfinite(hz) or hz <= 0.0:
            return None
        if not valid_bounds or log_diff <= 1e-12:
            return 2.5
        t_val = 5.0 * (math.log10(hz) - log_min) / log_diff
        if clip:
            t_val = float(np.clip(t_val, 0.0, 5.0))
        return float(t_val)

    arr = np.asarray(f0_hz, dtype=float)
    out = np.full(arr.shape, np.nan, dtype=float)
    valid = np.isfinite(arr) & (arr > 0.0)
    if np.any(valid):
        if valid_bounds and log_diff > 1e-12:
            t_vals = 5.0 * (np.log10(arr[valid]) - log_min) / log_diff
            if clip:
                t_vals = np.clip(t_vals, 0.0, 5.0)
            out[valid] = t_vals
        else:
            out[valid] = 2.5
    return out


def hz_to_chao_t(hz_val: Optional[float], s_min: float, s_max: float) -> Optional[float]:
    """将 Hz 转换为统一五度值（T-value, 0.0 ~ 5.0 对数标度，全工程统一调用 hz_to_t_value）。"""
    return hz_to_t_value(hz_val, s_min, s_max, clip=True)


def _t_to_5deg_continuous(val: float) -> float:
    """将 [0.0, 5.0] 连续 T 值映射为 [1.0, 5.0] 连续度值（[0,1]->1度, ..., [4,5]->5度）。"""
    v = float(np.clip(val, 0.0, 5.0))
    return 1.0 + 0.8 * v


def fit_chao_tone_code(
    t_contour: List[float],
    t_start: Optional[float] = None,
    t_mid: Optional[float] = None,
    t_end: Optional[float] = None,
) -> str:
    """根据 5 点代表性 T 值轨迹（[0.0, 5.0] 标度）与首/中/尾 T 值拟合整型声调五度值编码建议（如 "55", "35", "214", "51"）。"""
    raw_valid = [float(v) for v in (t_contour or []) if v is not None and np.isfinite(v)]
    if not raw_valid:
        if t_start is not None and t_end is not None and np.isfinite(t_start) and np.isfinite(t_end):
            raw_valid = [
                float(t_start),
                float(t_mid) if (t_mid is not None and np.isfinite(t_mid)) else float((t_start + t_end) / 2.0),
                float(t_end),
            ]
        else:
            return ""

    valid = [_t_to_5deg_continuous(v) for v in raw_valid]
    t0 = _t_to_5deg_continuous(float(t_start)) if (t_start is not None and np.isfinite(t_start)) else valid[0]
    t2 = _t_to_5deg_continuous(float(t_mid)) if (t_mid is not None and np.isfinite(t_mid)) else valid[len(valid) // 2]
    t4 = _t_to_5deg_continuous(float(t_end)) if (t_end is not None and np.isfinite(t_end)) else valid[-1]

    d_start = int(np.clip(round(t0), 1, 5))
    d_end = int(np.clip(round(t4), 1, 5))

    span = float(max(valid) - min(valid))
    if span < 0.60 and abs(t4 - t0) < 0.50:
        d_avg = int(np.clip(round(float(np.mean(valid))), 1, 5))
        return f"{d_avg}{d_avg}"

    inner = valid[1:-1] if len(valid) >= 3 else [t2]
    inner_min = float(min(inner))
    inner_max = float(max(inner))
    dip_depth = min(t0, t4) - inner_min
    peak_height = inner_max - max(t0, t4)

    if dip_depth >= 0.45 and dip_depth >= peak_height:
        d_turn = int(np.clip(round(inner_min), 1, 5))
        if d_turn == d_start and (t0 - inner_min) >= 0.55 and d_turn > 1:
            d_turn -= 1
        if d_turn == d_end and (t4 - inner_min) >= 0.55 and d_turn > 1:
            d_turn -= 1
        if d_turn < d_start and d_turn < d_end:
            return f"{d_start}{d_turn}{d_end}"

    if peak_height >= 0.45:
        d_turn = int(np.clip(round(inner_max), 1, 5))
        if d_turn == d_start and (inner_max - t0) >= 0.55 and d_turn < 5:
            d_turn += 1
        if d_turn == d_end and (inner_max - t4) >= 0.55 and d_turn < 5:
            d_turn += 1
        if d_turn > d_start and d_turn > d_end:
            return f"{d_start}{d_turn}{d_end}"

    if d_start == d_end:
        if (t4 - t0) >= 0.55:
            if d_end < 5:
                d_end += 1
            elif d_start > 1:
                d_start -= 1
        elif (t0 - t4) >= 0.55:
            if d_end > 1:
                d_end -= 1
            elif d_start < 5:
                d_start += 1

    return f"{d_start}{d_end}"


def _safe_nanmean(arr, axis=None, default=np.nan):
    a = np.asarray(arr, dtype=float)
    if a.size == 0:
        return default
    if axis is None:
        valid = a[np.isfinite(a)]
        return float(np.mean(valid)) if valid.size > 0 else default
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)
        if a.ndim == 2 and axis == 0:
            out = np.full(a.shape[1], default, dtype=float)
            for col_idx in range(a.shape[1]):
                col = a[:, col_idx]
                valid = col[np.isfinite(col)]
                if valid.size > 0:
                    out[col_idx] = float(np.mean(valid))
            return out
        return np.nanmean(a, axis=axis)


def _safe_nanstd(arr, axis=None, default=np.nan):
    a = np.asarray(arr, dtype=float)
    if a.size == 0:
        return default
    if axis is None:
        valid = a[np.isfinite(a)]
        return float(np.std(valid)) if valid.size > 0 else default
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)
        if a.ndim == 2 and axis == 0:
            out = np.full(a.shape[1], default, dtype=float)
            for col_idx in range(a.shape[1]):
                col = a[:, col_idx]
                valid = col[np.isfinite(col)]
                if valid.size > 0:
                    out[col_idx] = float(np.std(valid))
            return out
        return np.nanstd(a, axis=axis)


def _safe_nanmax(arr, default=np.nan):
    a = np.asarray(arr, dtype=float)
    valid = a[np.isfinite(a)]
    return float(np.max(valid)) if valid.size > 0 else default


def _compute_2d_convex_hull_and_area(points_xy):
    """Compute 2D convex hull polygon vertices and enclosed area (VSA)."""
    pts = np.asarray(points_xy, dtype=float)
    if pts.ndim != 2 or pts.shape[1] < 2:
        return np.empty((0, 2)), 0.0
    valid = np.isfinite(pts[:, 0]) & np.isfinite(pts[:, 1])
    pts = pts[valid, :2]
    if len(pts) < 3:
        return pts, 0.0

    unique_pts = np.unique(np.round(pts, 6), axis=0)
    if len(unique_pts) < 3:
        return unique_pts, 0.0

    if ConvexHull is not None:
        try:
            hull = ConvexHull(unique_pts)
            verts = unique_pts[hull.vertices]
            area = float(hull.volume)
            return verts, area
        except Exception:
            pass

    sorted_pts = unique_pts[np.lexsort((unique_pts[:, 1], unique_pts[:, 0]))]

    def _cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower = []
    for p in sorted_pts:
        while len(lower) >= 2 and _cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper = []
    for p in reversed(sorted_pts):
        while len(upper) >= 2 and _cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    hull_pts = np.asarray(lower[:-1] + upper[:-1], dtype=float)
    if len(hull_pts) < 3:
        return hull_pts, 0.0
    x = hull_pts[:, 0]
    y = hull_pts[:, 1]
    area = 0.5 * float(np.abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))))
    return hull_pts, area


def _compute_pillai_bartlett_trace(pts_a, pts_b):
    """
    Compute two-group MANOVA Pillai-Bartlett Trace (Pillai score in [0, 1])
    and centroid Euclidean distance in F1-F2 (or F1-F2-F3) acoustic space.
    """
    a = np.asarray(pts_a, dtype=float)
    b = np.asarray(pts_b, dtype=float)
    if a.ndim == 1:
        a = a.reshape(-1, 1)
    if b.ndim == 1:
        b = b.reshape(-1, 1)
    a = a[np.all(np.isfinite(a), axis=1)]
    b = b[np.all(np.isfinite(b), axis=1)]
    if len(a) == 0 or len(b) == 0:
        return np.nan, np.nan

    mean_a = np.mean(a, axis=0)
    mean_b = np.mean(b, axis=0)
    diff = mean_a - mean_b
    euclid_dist = float(np.linalg.norm(diff))
    if euclid_dist < 1e-10 and a.shape == b.shape and np.allclose(a, b, atol=1e-8):
        return 0.0, 0.0

    n_a = float(len(a))
    n_b = float(len(b))
    p = a.shape[1]
    B = (n_a * n_b / (n_a + n_b)) * np.outer(diff, diff)
    da = a - mean_a
    db = b - mean_b
    W = da.T @ da + db.T @ db
    T = B + W
    reg = 1e-6 * max(1.0, float(np.trace(T)) / float(p)) * np.eye(p)
    try:
        mat = np.linalg.solve(T + reg, B)
        pillai = float(np.trace(mat))
    except Exception:
        pillai = float(np.trace(B) / max(1e-9, np.trace(T)))
    pillai = float(np.clip(pillai, 0.0, 1.0))
    return pillai, euclid_dist
