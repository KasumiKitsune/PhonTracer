# -*- coding: utf-8 -*-
"""
无原生 praat-parselmouth 环境下的高保真声学计算回退层 (_parselmouth_fallback.py)。

设计契约：
1. 当生产环境已安装原生 C++ `parselmouth` 时，各模块 100% 优先使用原生 `parselmouth`，本模块不介入。
2. 当测试沙盒 VM 未安装 `parselmouth` 时，提供基于 NumPy / SciPy 的真实声学特征计算实现：
   - Sound: 支持 8/16/24/32-bit PCM 及 IEEE Float WAV 读取（兼容修复异常 nAvgBytesPerSec 头）、切片、保存及声学转换。
   - Intensity: 基于 Praat Kaiser-Bessel 窗（6.4 / minimum_pitch）的短时能量 dB SPL 计算。
   - Pitch: 基于 Boersma (1993) 窗归一化自相关 + 抛物线插值 + Viterbi 动态规划路径搜索的真实 F0 提取。
   - Formant: 基于重采样 (2 * maximum_formant) + 一阶预加重 + 高斯窗 + Levinson-Durbin LPC 多项式求根的共振峰提取。
   - Spectrogram: 基于高斯窗短时傅里叶变换 (STFT) 的宽带语谱图功率谱密度计算。
   - praat: 提供子模块占位以兼容模块属性访问与 AST 安全拦截测试。
"""

from __future__ import annotations

import importlib.util
import io
import math
import os
import re
import struct
import sys
import types
import wave
from pathlib import Path
from typing import Any, List, Optional, Sequence, Tuple, Union

import numpy as np
import scipy.io.wavfile
import scipy.linalg
import scipy.signal


__version__ = "0.4.4.fallback"
IS_FALLBACK = True


# ============================================================================
# 1. 基础矩阵与声学结果对象 (Matrix, Intensity, Pitch, Formant, Spectrogram)
# ============================================================================

class Matrix:
    """兼容 parselmouth.Matrix 的二维网格对象。"""

    def __init__(
        self,
        values: np.ndarray,
        xmin: float = 0.0,
        xmax: float = 1.0,
        nx: int = 1,
        dx: float = 1.0,
        x1: float = 0.5,
        ymin: float = 1.0,
        ymax: float = 1.0,
        ny: int = 1,
        dy: float = 1.0,
        y1: float = 1.0,
    ):
        arr = np.asarray(values, dtype=np.float64)
        if arr.ndim == 1:
            arr = arr[None, :]
        self.values = arr
        self.ny, self.nx = arr.shape
        self.xmin = float(xmin)
        self.xmax = float(xmax)
        self.dx = float(dx)
        self.dt = float(dx)
        self.x1 = float(x1)
        self.ymin = float(ymin)
        self.ymax = float(ymax)
        self.dy = float(dy)
        self.y1 = float(y1)
        self.n_frames = int(self.nx)

    def xs(self) -> np.ndarray:
        if self.nx <= 0:
            return np.zeros(0, dtype=np.float64)
        return self.x1 + np.arange(self.nx, dtype=np.float64) * self.dx

    def ys(self) -> np.ndarray:
        if self.ny <= 0:
            return np.zeros(0, dtype=np.float64)
        return self.y1 + np.arange(self.ny, dtype=np.float64) * self.dy

    def x_grid(self) -> np.ndarray:
        return np.linspace(self.xmin, self.xmax, self.nx + 1, dtype=np.float64)

    def y_grid(self) -> np.ndarray:
        return np.linspace(self.ymin, self.ymax, self.ny + 1, dtype=np.float64)


class Intensity:
    """兼容 parselmouth.Intensity 的短时声强对象。"""

    def __init__(self, xs: np.ndarray, values_1d: np.ndarray, xmin: float, xmax: float, dt: float):
        self._xs = np.asarray(xs, dtype=np.float64).reshape(-1)
        vals = np.asarray(values_1d, dtype=np.float64).reshape(1, -1)
        self.values = vals
        self.xmin = float(xmin)
        self.xmax = float(xmax)
        self.dt = float(dt)
        self.dx = float(dt)
        self.n_frames = int(self._xs.size)
        self.x1 = float(self._xs[0]) if self.n_frames > 0 else float(xmin)

    def xs(self) -> np.ndarray:
        return self._xs.copy()

    def get_value(self, time: float, interpolation: str = "cubic") -> float:
        t = float(time)
        if self.n_frames == 0 or t < self.xmin or t > self.xmax:
            return float(np.nan)
        if self.n_frames == 1:
            return float(self.values[0, 0])
        return float(np.interp(t, self._xs, self.values[0]))

    def get_average(self, from_time: Optional[float] = None, to_time: Optional[float] = None, averaging_method: str = "energy") -> float:
        if self.n_frames == 0:
            return float(np.nan)
        t0 = self.xmin if from_time is None or from_time <= 0 else float(from_time)
        t1 = self.xmax if to_time is None or to_time <= 0 else float(to_time)
        mask = (self._xs >= t0) & (self._xs <= t1) & np.isfinite(self.values[0])
        if not np.any(mask):
            return float(np.nan)
        sub = self.values[0, mask]
        if str(averaging_method).lower() == "db":
            return float(np.mean(sub))
        energy = np.mean(10.0 ** (sub / 10.0))
        return float(10.0 * np.log10(max(energy, 1e-20)))


class Pitch:
    """兼容 parselmouth.Pitch 的基频轨迹对象。"""

    def __init__(
        self,
        xs: np.ndarray,
        frequencies: np.ndarray,
        strengths: np.ndarray,
        xmin: float,
        xmax: float,
        dt: float,
        ceiling: float = 600.0,
    ):
        self._xs = np.asarray(xs, dtype=np.float64).reshape(-1)
        freqs = np.asarray(frequencies, dtype=np.float64).reshape(-1)
        strs = np.asarray(strengths, dtype=np.float64).reshape(-1)
        freqs = np.where(np.isnan(freqs) | (freqs <= 0.0), 0.0, freqs)
        strs = np.where(np.isnan(strs), 0.0, strs)

        n = self._xs.size
        self.selected_array = np.zeros(n, dtype=[("frequency", np.float64), ("strength", np.float64)])
        if n > 0:
            self.selected_array["frequency"] = freqs[:n]
            self.selected_array["strength"] = strs[:n]

        self.xmin = float(xmin)
        self.xmax = float(xmax)
        self.dt = float(dt)
        self.dx = float(dt)
        self.n_frames = int(n)
        self.x1 = float(self._xs[0]) if n > 0 else float(xmin)
        self.ceiling = float(ceiling)

    def xs(self) -> np.ndarray:
        return self._xs.copy()

    def get_value_at_time(self, time: float, unit: str = "Hertz", interpolation: str = "linear") -> float:
        t = float(time)
        if self.n_frames == 0 or t < self.xmin or t > self.xmax:
            return float(np.nan)
        freqs = self.selected_array["frequency"]
        if self.n_frames == 1:
            f0 = float(freqs[0])
            if f0 > 0.0 and abs(t - self._xs[0]) <= max(self.dt, 0.02):
                return f0
            return float(np.nan)

        idx_right = int(np.searchsorted(self._xs, t, side="left"))
        if idx_right == 0:
            if abs(self._xs[0] - t) <= 0.5 * self.dt and freqs[0] > 0.0:
                return float(freqs[0])
            return float(np.nan)
        if idx_right >= self.n_frames:
            if abs(t - self._xs[-1]) <= 0.5 * self.dt and freqs[-1] > 0.0:
                return float(freqs[-1])
            return float(np.nan)

        i0 = idx_right - 1
        i1 = idx_right
        t0, t1 = float(self._xs[i0]), float(self._xs[i1])
        f0, f1 = float(freqs[i0]), float(freqs[i1])

        if f0 > 0.0 and f1 > 0.0 and (t1 - t0) <= 1.5 * self.dt:
            alpha = (t - t0) / max(t1 - t0, 1e-12)
            return float(f0 + alpha * (f1 - f0))
        if f0 > 0.0 and abs(t - t0) <= 0.5 * self.dt:
            return f0
        if f1 > 0.0 and abs(t1 - t) <= 0.5 * self.dt:
            return f1
        return float(np.nan)

    def to_matrix(self) -> Matrix:
        vals = self.selected_array["frequency"][None, :].copy()
        return Matrix(
            vals,
            xmin=self.xmin,
            xmax=self.xmax,
            nx=self.n_frames,
            dx=self.dt,
            x1=self.x1,
        )

    def count_voiced_frames(self) -> int:
        return int(np.sum(self.selected_array["frequency"] > 0.0))


class Formant:
    """兼容 parselmouth.Formant 的共振峰对象。"""

    def __init__(
        self,
        xs: np.ndarray,
        freqs_matrix: np.ndarray,
        bandwidths_matrix: np.ndarray,
        xmin: float,
        xmax: float,
        dt: float,
    ):
        self._xs = np.asarray(xs, dtype=np.float64).reshape(-1)
        self._freqs = np.asarray(freqs_matrix, dtype=np.float64)
        self._bandwidths = np.asarray(bandwidths_matrix, dtype=np.float64)
        if self._freqs.ndim == 1:
            self._freqs = self._freqs[:, None]
        if self._bandwidths.ndim == 1:
            self._bandwidths = self._bandwidths[:, None]
        self.xmin = float(xmin)
        self.xmax = float(xmax)
        self.dt = float(dt)
        self.dx = float(dt)
        self.n_frames = int(self._xs.size)
        self.x1 = float(self._xs[0]) if self.n_frames > 0 else float(xmin)
        self.max_number_of_formants = int(self._freqs.shape[1]) if self._freqs.ndim == 2 else 0

    def xs(self) -> np.ndarray:
        return self._xs.copy()

    def _interp_column(self, col: np.ndarray, time: float) -> float:
        t = float(time)
        if self.n_frames == 0 or t < self.xmin or t > self.xmax:
            return float(np.nan)
        if self.n_frames == 1:
            v = float(col[0])
            return v if (np.isfinite(v) and v > 0.0) else float(np.nan)

        idx_right = int(np.searchsorted(self._xs, t, side="left"))
        if idx_right == 0:
            v = float(col[0])
            if abs(self._xs[0] - t) <= self.dt and np.isfinite(v) and v > 0.0:
                return v
            return float(np.nan)
        if idx_right >= self.n_frames:
            v = float(col[-1])
            if abs(t - self._xs[-1]) <= self.dt and np.isfinite(v) and v > 0.0:
                return v
            return float(np.nan)

        i0 = idx_right - 1
        i1 = idx_right
        t0, t1 = float(self._xs[i0]), float(self._xs[i1])
        v0, v1 = float(col[i0]), float(col[i1])
        ok0 = np.isfinite(v0) and v0 > 0.0
        ok1 = np.isfinite(v1) and v1 > 0.0
        if ok0 and ok1:
            alpha = (t - t0) / max(t1 - t0, 1e-12)
            return float(v0 + alpha * (v1 - v0))
        if ok0 and abs(t - t0) <= self.dt:
            return v0
        if ok1 and abs(t1 - t) <= self.dt:
            return v1
        return float(np.nan)

    def get_value_at_time(
        self,
        formant_number: int,
        time: float,
        unit: str = "Hertz",
        interpolation: str = "linear",
    ) -> float:
        f_idx = int(formant_number) - 1
        if f_idx < 0 or f_idx >= self.max_number_of_formants:
            return float(np.nan)
        return self._interp_column(self._freqs[:, f_idx], time)

    def get_bandwidth_at_time(
        self,
        formant_number: int,
        time: float,
        unit: str = "Hertz",
        interpolation: str = "linear",
    ) -> float:
        f_idx = int(formant_number) - 1
        if f_idx < 0 or f_idx >= self.max_number_of_formants:
            return float(np.nan)
        return self._interp_column(self._bandwidths[:, f_idx], time)

    def to_matrix(self, formant_number: int = 1) -> Matrix:
        f_idx = max(0, min(self.max_number_of_formants - 1, int(formant_number) - 1))
        col = self._freqs[:, f_idx] if self.max_number_of_formants > 0 else np.zeros(self.n_frames)
        return Matrix(
            col[None, :].copy(),
            xmin=self.xmin,
            xmax=self.xmax,
            nx=self.n_frames,
            dx=self.dt,
            x1=self.x1,
        )


class Spectrogram:
    """兼容 parselmouth.Spectrogram 的二维时频谱对象。"""

    def __init__(
        self,
        xs: np.ndarray,
        ys: np.ndarray,
        values: np.ndarray,
        xmin: float,
        xmax: float,
        ymin: float,
        ymax: float,
        dt: float,
        df: float,
    ):
        self._xs = np.asarray(xs, dtype=np.float64).reshape(-1)
        self._ys = np.asarray(ys, dtype=np.float64).reshape(-1)
        vals = np.asarray(values, dtype=np.float64)
        if vals.ndim != 2:
            vals = vals.reshape(self._ys.size, self._xs.size)
        self.values = vals
        self.xmin = float(xmin)
        self.xmax = float(xmax)
        self.ymin = float(ymin)
        self.ymax = float(ymax)
        self.dt = float(dt)
        self.dx = float(dt)
        self.df = float(df)
        self.dy = float(df)
        self.nx = int(self._xs.size)
        self.ny = int(self._ys.size)
        self.n_frames = int(self.nx)
        self.x1 = float(self._xs[0]) if self.nx > 0 else float(xmin)
        self.y1 = float(self._ys[0]) if self.ny > 0 else float(ymin)

    def xs(self) -> np.ndarray:
        return self._xs.copy()

    def ys(self) -> np.ndarray:
        return self._ys.copy()

    def x_grid(self) -> np.ndarray:
        return np.linspace(self.xmin, self.xmax, self.nx + 1, dtype=np.float64)

    def y_grid(self) -> np.ndarray:
        return np.linspace(self.ymin, self.ymax, self.ny + 1, dtype=np.float64)


# ============================================================================
# 2. WAV 音频鲁棒解码辅助函数
# ============================================================================

def _decode_pcm_bytes(raw_bytes: bytes, sampwidth: int, n_channels: int, audio_format: int = 1) -> np.ndarray:
    """将原始音频字节解码为 [-1.0, 1.0] 的 float64 数组，形状为 (n_channels, n_samples)。"""
    if len(raw_bytes) == 0:
        return np.zeros((max(1, n_channels), 0), dtype=np.float64)

    if audio_format == 3:  # IEEE Float
        if sampwidth == 4:
            arr = np.frombuffer(raw_bytes, dtype="<f4").astype(np.float64)
        elif sampwidth == 8:
            arr = np.frombuffer(raw_bytes, dtype="<f8").astype(np.float64)
        else:
            raise ValueError(f"Unsupported IEEE float sample width: {sampwidth}")
    else:
        if sampwidth == 1:
            arr = (np.frombuffer(raw_bytes, dtype=np.uint8).astype(np.float64) - 128.0) / 128.0
        elif sampwidth == 2:
            arr = np.frombuffer(raw_bytes, dtype="<i2").astype(np.float64) / 32768.0
        elif sampwidth == 3:
            n_Triplets = len(raw_bytes) // 3
            b = np.frombuffer(raw_bytes[: n_Triplets * 3], dtype=np.uint8).reshape(-1, 3)
            signed = (
                b[:, 0].astype(np.int32)
                | (b[:, 1].astype(np.int32) << 8)
                | (b[:, 2].astype(np.int32) << 16)
            )
            neg_mask = (signed & 0x800000) != 0
            signed[neg_mask] -= 1 << 24
            arr = signed.astype(np.float64) / 8388608.0
        elif sampwidth == 4:
            arr = np.frombuffer(raw_bytes, dtype="<i4").astype(np.float64) / 2147483648.0
        else:
            raise ValueError(f"Unsupported PCM sample width: {sampwidth}")

    n_ch = max(1, int(n_channels))
    n_frames = len(arr) // n_ch
    arr = arr[: n_frames * n_ch]
    if n_ch == 1:
        return arr.reshape(1, n_frames)
    return arr.reshape(n_frames, n_ch).T.copy()


def _read_wav_riff_fallback(filepath: str) -> Tuple[float, np.ndarray]:
    """自定义 RIFF WAV 块解析器，兼容异常 nAvgBytesPerSec 与扩展 fmt 块。"""
    with open(filepath, "rb") as f:
        header = f.read(12)
        if len(header) < 12 or header[:4] not in (b"RIFF", b"RIFX") or header[8:12] != b"WAVE":
            raise ValueError(f"Not a valid RIFF WAVE file: {filepath}")

        audio_format = 1
        n_channels = 1
        sample_rate = 44100
        bits_per_sample = 16
        data_bytes = b""

        while True:
            chunk_hdr = f.read(8)
            if len(chunk_hdr) < 8:
                break
            chunk_id, chunk_size = struct.unpack("<4sI", chunk_hdr)
            chunk_start = f.tell()
            if chunk_id == b"fmt ":
                fmt_raw = f.read(min(chunk_size, 40))
                if len(fmt_raw) >= 16:
                    audio_format, n_channels, sample_rate, _byte_rate, _block_align, bits_per_sample = struct.unpack(
                        "<HHIIHH", fmt_raw[:16]
                    )
                    if audio_format == 0xFFFE and len(fmt_raw) >= 26:
                        sub_format = struct.unpack("<H", fmt_raw[24:26])[0]
                        if sub_format in (1, 3):
                            audio_format = sub_format
            elif chunk_id == b"data":
                data_bytes = f.read(chunk_size)
                break
            f.seek(chunk_start + chunk_size + (chunk_size % 2))

        sampwidth = max(1, (bits_per_sample + 7) // 8)
        values = _decode_pcm_bytes(data_bytes, sampwidth, n_channels, audio_format=audio_format)
        return float(sample_rate), values


def _read_wav_robust(filepath: Union[str, os.PathLike]) -> Tuple[float, np.ndarray]:
    """
    鲁棒读取 WAV 文件：
    1. 优先尝试 scipy.io.wavfile.read
    2. 若因旧版 PhonRec 头部 nAvgBytesPerSec 异常或 24-bit 失败，回退至 wave 模块
    3. 若仍失败，回退至底层 RIFF chunk 解析
    """
    path_str = os.fspath(filepath)
    if not os.path.isfile(path_str):
        raise FileNotFoundError(f"Audio file not found: {path_str}")

    try:
        sr, data = scipy.io.wavfile.read(path_str)
        if np.issubdtype(data.dtype, np.floating):
            values = data.astype(np.float64)
        elif data.dtype == np.uint8:
            values = (data.astype(np.float64) - 128.0) / 128.0
        elif np.issubdtype(data.dtype, np.integer):
            info = np.iinfo(data.dtype)
            scale = float(-info.min if info.min < 0 else info.max)
            values = data.astype(np.float64) / scale
        else:
            values = data.astype(np.float64)

        if values.ndim == 1:
            values = values.reshape(1, -1)
        else:
            values = values.T.copy()
        return float(sr), values
    except Exception:
        pass

    try:
        with wave.open(path_str, "rb") as wf:
            n_ch = wf.getnchannels()
            sampwidth = wf.getsampwidth()
            sr = wf.getframerate()
            n_frames = wf.getnframes()
            raw = wf.readframes(n_frames)
        values = _decode_pcm_bytes(raw, sampwidth, n_ch, audio_format=1)
        return float(sr), values
    except Exception:
        pass

    return _read_wav_riff_fallback(path_str)


# ============================================================================
# 3. 核心 Sound 类实现
# ============================================================================

class Sound:
    """兼容 parselmouth.Sound 的音频与声学分析核心类。"""

    def __init__(
        self,
        filepath_or_values: Union[str, os.PathLike, np.ndarray, Sequence[Any], "Sound"],
        sampling_frequency: Optional[float] = None,
        start_time: float = 0.0,
    ):
        if isinstance(filepath_or_values, Sound):
            self.values = filepath_or_values.values.copy()
            self.sampling_frequency = float(
                sampling_frequency if sampling_frequency is not None else filepath_or_values.sampling_frequency
            )
            self.xmin = float(start_time)
        elif isinstance(filepath_or_values, (str, os.PathLike, Path)):
            sr, vals = _read_wav_robust(filepath_or_values)
            self.values = vals
            self.sampling_frequency = float(sampling_frequency if sampling_frequency is not None else sr)
            self.xmin = float(start_time)
        else:
            arr = np.asarray(filepath_or_values, dtype=np.float64)
            if arr.ndim == 0:
                arr = arr.reshape(1, 1)
            elif arr.ndim == 1:
                arr = arr.reshape(1, -1)
            elif arr.ndim > 2:
                arr = arr.reshape(arr.shape[0], -1)
            self.values = np.ascontiguousarray(arr, dtype=np.float64)
            sr_val = 44100.0 if sampling_frequency is None else float(sampling_frequency)
            if sr_val <= 0:
                raise ValueError("sampling_frequency must be positive")
            self.sampling_frequency = sr_val
            self.xmin = float(start_time)

        self.n_channels = int(self.values.shape[0])
        self.n_samples = int(self.values.shape[1])
        self.dt = 1.0 / self.sampling_frequency
        self.dx = self.dt
        self.duration = float(self.n_samples) / self.sampling_frequency
        self.total_duration = self.duration
        self.xmax = self.xmin + self.duration
        self.x1 = self.xmin + 0.5 * self.dt

    def xs(self) -> np.ndarray:
        if self.n_samples <= 0:
            return np.zeros(0, dtype=np.float64)
        return self.xmin + (np.arange(self.n_samples, dtype=np.float64) + 0.5) * self.dt

    def get_total_duration(self) -> float:
        return float(self.duration)

    def save(self, filepath: Union[str, os.PathLike], format: str = "WAV") -> None:
        path_str = os.fspath(filepath)
        parent = os.path.dirname(os.path.abspath(path_str))
        if parent:
            os.makedirs(parent, exist_ok=True)
        sr_int = max(1, int(round(self.sampling_frequency)))
        clipped = np.clip(self.values, -1.0, 1.0)
        pcm = np.round(clipped * 32767.0).astype(np.int16)
        out_arr = pcm[0] if self.n_channels == 1 else pcm.T
        scipy.io.wavfile.write(path_str, sr_int, out_arr)

    def extract_part(
        self,
        from_time: Optional[float] = 0.0,
        to_time: Optional[float] = None,
        preserve_times: bool = False,
        window_shape: str = "rectangular",
        relative_width: float = 1.0,
    ) -> "Sound":
        t0 = self.xmin if from_time is None else float(from_time)
        t1 = self.xmax if (to_time is None or float(to_time) <= 0.0) else float(to_time)

        s_idx = max(0, int(np.floor((t0 - self.xmin) * self.sampling_frequency)))
        e_idx = min(self.n_samples, int(np.ceil((t1 - self.xmin) * self.sampling_frequency)))
        if e_idx <= s_idx and self.n_samples > 0:
            s_idx = min(max(0, s_idx), self.n_samples - 1)
            e_idx = min(self.n_samples, s_idx + 1)

        part_vals = self.values[:, s_idx:e_idx].copy()
        new_start = t0 if preserve_times else 0.0
        return Sound(part_vals, sampling_frequency=self.sampling_frequency, start_time=new_start)

    def _mono_signal(self) -> np.ndarray:
        if self.n_channels == 1:
            return self.values[0]
        return np.mean(self.values, axis=0)

    def to_intensity(
        self,
        minimum_pitch: float = 100.0,
        time_step: Optional[float] = None,
        subtract_mean: bool = True,
    ) -> Intensity:
        """
        基于 Praat Kaiser-Bessel 窗 (物理窗长 6.4 / minimum_pitch) 计算短时声强 (dB SPL)。
        """
        y = self._mono_signal()
        sr = self.sampling_frequency
        dur = self.duration
        min_p = max(10.0, float(minimum_pitch))
        dt = float(time_step) if (time_step is not None and float(time_step) > 0.0) else (0.8 / min_p)

        if self.n_samples == 0 or dur <= 0.0:
            return Intensity(np.zeros(0), np.zeros(0), self.xmin, self.xmax, dt)

        win_dur = 6.4 / min_p
        win_len = max(4, int(round(win_dur * sr)))
        if win_len % 2 == 0:
            win_len += 1

        if dur > win_dur:
            n_frames = int(np.floor((dur - win_dur) / dt)) + 1
            x1 = self.xmin + 0.5 * (dur - (n_frames - 1) * dt)
            xs = x1 + np.arange(n_frames, dtype=np.float64) * dt
        else:
            n_frames = max(1, int(np.floor(dur / dt)) + 1) if dur > dt else 1
            x1 = self.xmin + 0.5 * (dur - (n_frames - 1) * dt)
            xs = x1 + np.arange(n_frames, dtype=np.float64) * dt

        half = win_len // 2
        t_rel = np.linspace(-1.0, 1.0, win_len)
        w = (np.i0(20.24 * np.sqrt(np.maximum(0.0, 1.0 - t_rel ** 2))) / np.i0(20.24)) ** 2
        w_sum = np.sum(w)
        if w_sum > 0:
            w = w / w_sum

        centers = np.clip(np.round((xs - self.xmin) * sr).astype(int), 0, max(0, len(y) - 1))
        padded = np.pad(y, (half, half), mode="edge")

        db_out = np.empty(len(xs), dtype=np.float64)
        chunk_size = 2048
        win_offsets = np.arange(win_len)[None, :]
        for start_f in range(0, len(xs), chunk_size):
            end_f = min(len(xs), start_f + chunk_size)
            idx = centers[start_f:end_f, None] + win_offsets
            frames = padded[idx]
            if subtract_mean:
                means = frames @ w
                frames = frames - means[:, None]
            ms = (frames ** 2) @ w
            rms = np.sqrt(np.maximum(ms, 1e-20))
            db_out[start_f:end_f] = 20.0 * np.log10(rms / 2e-5)

        return Intensity(xs, db_out, self.xmin, self.xmax, dt)

    def to_pitch_ac(
        self,
        time_step: Optional[float] = None,
        pitch_floor: float = 75.0,
        max_number_of_candidates: int = 15,
        very_accurate: bool = False,
        silence_threshold: float = 0.03,
        voicing_threshold: float = 0.45,
        octave_cost: float = 0.01,
        octave_jump_cost: float = 0.35,
        voiced_unvoiced_cost: float = 0.14,
        pitch_ceiling: float = 600.0,
    ) -> Pitch:
        """
        基于 Boersma (1993) 窗归一化自相关 + 抛物线插值 + Viterbi 路径搜索提取基频。
        """
        y = self._mono_signal()
        sr = self.sampling_frequency
        dur = self.duration
        p_floor = max(20.0, float(pitch_floor))
        p_ceil = max(p_floor + 10.0, min(float(pitch_ceiling), 0.5 * sr))
        dt = float(time_step) if (time_step is not None and float(time_step) > 0.0) else (0.75 / p_floor)

        if self.n_samples == 0 or dur <= 0.0:
            return Pitch(np.zeros(0), np.zeros(0), np.zeros(0), self.xmin, self.xmax, dt, ceiling=p_ceil)

        periods = 6.0 if very_accurate else 3.0
        win_dur = periods / p_floor
        win_len = max(8, int(round(win_dur * sr)))
        if win_len % 2 == 0:
            win_len += 1

        if dur > win_dur:
            n_frames = int(np.floor((dur - win_dur) / dt)) + 1
            x1 = self.xmin + 0.5 * (dur - (n_frames - 1) * dt)
            xs = x1 + np.arange(n_frames, dtype=np.float64) * dt
        else:
            n_frames = max(1, int(np.floor(dur / dt)) + 1) if dur > dt else 1
            x1 = self.xmin + 0.5 * (dur - (n_frames - 1) * dt)
            xs = x1 + np.arange(n_frames, dtype=np.float64) * dt

        y_centered = y - np.mean(y)
        global_peak = float(np.max(np.abs(y_centered))) if len(y_centered) > 0 else 0.0
        if global_peak <= 1e-12:
            return Pitch(
                xs,
                np.zeros(n_frames, dtype=np.float64),
                np.zeros(n_frames, dtype=np.float64),
                self.xmin,
                self.xmax,
                dt,
                ceiling=p_ceil,
            )

        t_rel = np.linspace(-0.5, 0.5, win_len)
        if very_accurate:
            e0 = math.exp(-12.0 * 0.25)
            w = (np.exp(-12.0 * (t_rel ** 2)) - e0) / (1.0 - e0)
        else:
            w = 0.5 - 0.5 * np.cos(2.0 * np.pi * np.linspace(0.0, 1.0, win_len))
        w = np.maximum(w, 0.0)

        n_fft = 1 << int(np.ceil(np.log2(2 * win_len)))
        W_fft = np.fft.rfft(w, n=n_fft)
        r_w = np.fft.irfft(W_fft * np.conj(W_fft), n=n_fft)[:win_len]
        r_w = np.maximum(r_w / max(float(r_w[0]), 1e-12), 1e-6)

        lag_min = max(2, int(np.floor(sr / p_ceil)))
        lag_max = min(win_len - 2, int(np.ceil(sr / p_floor)))

        half = win_len // 2
        centers = np.clip(np.round((xs - self.xmin) * sr).astype(int), 0, max(0, len(y) - 1))
        padded = np.pad(y, (half, half), mode="constant")

        # 保留前 K 个最强候选（含 1 个无声候选）以加速 Viterbi
        max_cand = min(max(2, int(max_number_of_candidates)), 6)
        cand_freqs = np.zeros((n_frames, max_cand), dtype=np.float64)
        cand_strengths = np.full((n_frames, max_cand), -1e9, dtype=np.float64)
        cand_raw_r = np.zeros((n_frames, max_cand), dtype=np.float64)

        sil_thresh = max(float(silence_threshold), 1e-6)
        voic_thresh = float(voicing_threshold)
        oct_cost = float(octave_cost)

        chunk_size = 512
        win_offsets = np.arange(win_len)[None, :]
        for start_f in range(0, n_frames, chunk_size):
            end_f = min(n_frames, start_f + chunk_size)
            c_idx = centers[start_f:end_f, None] + win_offsets
            frames = padded[c_idx]
            frames = frames - np.mean(frames, axis=1, keepdims=True)
            local_peaks = np.max(np.abs(frames), axis=1)

            intensity_rel = local_peaks / global_peak
            uv_strength = voic_thresh + np.maximum(
                0.0,
                2.0 - intensity_rel / (sil_thresh / (1.0 + voic_thresh)),
            )
            cand_freqs[start_f:end_f, 0] = 0.0
            cand_strengths[start_f:end_f, 0] = uv_strength

            active = np.where(intensity_rel >= sil_thresh * 0.5)[0]
            if len(active) == 0 or lag_max <= lag_min:
                continue

            xw = frames[active] * w[None, :]
            F = np.fft.rfft(xw, n=n_fft, axis=1)
            rx = np.fft.irfft(F * np.conj(F), n=n_fft, axis=1)[:, : lag_max + 2]
            r0 = np.maximum(rx[:, 0:1], 1e-12)
            rnorm = (rx / r0) / r_w[: lag_max + 2][None, :]

            mid = rnorm[:, lag_min : lag_max + 1]
            left = rnorm[:, lag_min - 1 : lag_max]
            right = rnorm[:, lag_min + 1 : lag_max + 2]
            is_peak = (mid > left) & (mid >= right) & (mid > 0.1 * voic_thresh)

            for idx_in_active, f_rel in enumerate(active):
                f_abs = start_f + int(f_rel)
                pk_offsets = np.where(is_peak[idx_in_active])[0]
                if len(pk_offsets) == 0:
                    continue
                lags = lag_min + pk_offsets
                r_m = rnorm[idx_in_active, lags]
                r_l = rnorm[idx_in_active, lags - 1]
                r_r = rnorm[idx_in_active, lags + 1]
                denom = r_l - 2.0 * r_m + r_r
                delta = np.where(np.abs(denom) > 1e-12, 0.5 * (r_l - r_r) / denom, 0.0)
                delta = np.clip(delta, -0.5, 0.5)
                refined_lags = lags + delta
                val_interp = np.clip(r_m - 0.25 * (r_l - r_r) * delta, 0.0, 1.0)
                freqs_c = sr / refined_lags
                valid_f = (freqs_c >= p_floor) & (freqs_c <= p_ceil)
                if not np.any(valid_f):
                    continue
                freqs_c = freqs_c[valid_f]
                val_interp = val_interp[valid_f]
                strengths_c = val_interp - oct_cost * np.log2(np.maximum(p_ceil / freqs_c, 1.0))
                order = np.argsort(strengths_c)[::-1][: max_cand - 1]
                k = len(order)
                cand_freqs[f_abs, 1 : 1 + k] = freqs_c[order]
                cand_strengths[f_abs, 1 : 1 + k] = strengths_c[order]
                cand_raw_r[f_abs, 1 : 1 + k] = val_interp[order]

        # Viterbi 动态规划全局最优基频轨迹搜索（向量化候选转移矩阵）
        time_corr = 0.01 / max(dt, 1e-4)
        oj_cost = float(octave_jump_cost) * time_corr
        vu_cost = float(voiced_unvoiced_cost) * time_corr

        dp = np.full((n_frames, max_cand), -1e12, dtype=np.float64)
        back = np.zeros((n_frames, max_cand), dtype=np.int32)
        dp[0] = cand_strengths[0]

        log2_freqs = np.where(cand_freqs > 0.0, np.log2(np.maximum(cand_freqs, 1e-6)), 0.0)
        voiced_mask = cand_freqs > 0.0

        for i in range(1, n_frames):
            v_prev = voiced_mask[i - 1][:, None]
            v_curr = voiced_mask[i][None, :]
            both_v = v_prev & v_curr
            either_uv = v_prev ^ v_curr
            trans = np.where(
                both_v,
                oj_cost * np.abs(log2_freqs[i - 1][:, None] - log2_freqs[i][None, :]),
                np.where(either_uv, vu_cost, 0.0),
            )
            scores = dp[i - 1][:, None] - trans
            best_prev = np.argmax(scores, axis=0)
            dp[i] = scores[best_prev, np.arange(max_cand)] + cand_strengths[i]
            back[i] = best_prev

        best_idx = int(np.argmax(dp[-1]))
        chosen_f = np.zeros(n_frames, dtype=np.float64)
        chosen_s = np.zeros(n_frames, dtype=np.float64)
        for i in range(n_frames - 1, -1, -1):
            chosen_f[i] = cand_freqs[i, best_idx]
            chosen_s[i] = cand_raw_r[i, best_idx] if chosen_f[i] > 0.0 else cand_strengths[i, 0]
            best_idx = int(back[i, best_idx])

        return Pitch(xs, chosen_f, chosen_s, self.xmin, self.xmax, dt, ceiling=p_ceil)

    def to_pitch(
        self,
        time_step: Optional[float] = None,
        pitch_floor: float = 75.0,
        pitch_ceiling: float = 600.0,
        **kwargs: Any,
    ) -> Pitch:
        return self.to_pitch_ac(
            time_step=time_step,
            pitch_floor=pitch_floor,
            pitch_ceiling=pitch_ceiling,
            **kwargs,
        )

    def to_formant_burg(
        self,
        time_step: Optional[float] = None,
        max_number_of_formants: float = 5.0,
        maximum_formant: float = 5500.0,
        window_length: float = 0.025,
        pre_emphasis_from: float = 50.0,
    ) -> Formant:
        """
        基于 Praat 风格重采样 (2 * maximum_formant) + 预加重 + 高斯窗 + LPC 多项式求根提取共振峰与带宽。
        """
        y = self._mono_signal()
        sr = self.sampling_frequency
        dur = self.duration
        win_len_param = max(0.005, float(window_length))
        dt = float(time_step) if (time_step is not None and float(time_step) > 0.0) else (0.25 * win_len_param)
        phys_win = 2.0 * win_len_param
        max_f = max(1, int(round(float(max_number_of_formants))))
        max_hz = max(1000.0, float(maximum_formant))

        if self.n_samples == 0 or dur <= 0.0:
            return Formant(
                np.zeros(0),
                np.zeros((0, max_f)),
                np.zeros((0, max_f)),
                self.xmin,
                self.xmax,
                dt,
            )

        if dur > phys_win:
            n_frames = int(np.floor((dur - phys_win) / dt)) + 1
            x1 = self.xmin + 0.5 * (dur - (n_frames - 1) * dt)
            xs = x1 + np.arange(n_frames, dtype=np.float64) * dt
        else:
            n_frames = max(1, int(np.floor(dur / dt)) + 1) if dur > dt else 1
            x1 = self.xmin + 0.5 * (dur - (n_frames - 1) * dt)
            xs = x1 + np.arange(n_frames, dtype=np.float64) * dt

        p_order = 2 * max_f
        sr_target = 2.0 * max_hz
        if abs(sr - sr_target) > 1.0 and len(y) > 8:
            up = int(round(sr_target))
            down = int(round(sr))
            g = math.gcd(up, down)
            up //= g
            down //= g
            if up <= 2000 and down <= 2000:
                y_rs = scipy.signal.resample_poly(y, up, down)
            else:
                n_target = max(p_order + 4, int(round(len(y) * sr_target / sr)))
                y_rs = scipy.signal.resample(y, n_target)
        else:
            y_rs = y.copy()
            sr_target = float(sr)

        if pre_emphasis_from is not None and float(pre_emphasis_from) > 0.0 and len(y_rs) > 1:
            alpha = math.exp(-2.0 * math.pi * float(pre_emphasis_from) / sr_target)
            y_rs = np.append(y_rs[0], y_rs[1:] - alpha * y_rs[:-1])

        w_samples = max(p_order + 4, int(round(phys_win * sr_target)))
        if w_samples % 2 == 0:
            w_samples += 1
        half = w_samples // 2
        t_rel = np.linspace(-1.0, 1.0, w_samples)
        w = np.exp(-0.5 * (2.5 * t_rel) ** 2)

        padded = np.pad(y_rs, (half, half), mode="edge")
        centers = np.clip(np.round((xs - self.xmin) * sr_target).astype(int), 0, max(0, len(y_rs) - 1))

        freqs_mat = np.full((n_frames, max_f), np.nan, dtype=np.float64)
        bw_mat = np.full((n_frames, max_f), np.nan, dtype=np.float64)

        for i, c in enumerate(centers):
            frame = padded[c : c + w_samples]
            frame = (frame - np.mean(frame)) * w
            if np.max(np.abs(frame)) < 1e-6:
                continue

            r = np.array([np.dot(frame[: w_samples - k], frame[k:]) for k in range(p_order + 1)], dtype=np.float64)
            if r[0] <= 1e-12:
                continue

            # Levinson-Durbin 递推求解全极点 LPC 系数
            a = np.zeros(p_order + 1, dtype=np.float64)
            a[0] = 1.0
            err = float(r[0])
            ok = True
            for k in range(1, p_order + 1):
                lam = -float(np.dot(a[:k], r[k:0:-1])) / err
                if not math.isfinite(lam) or abs(lam) >= 1.0:
                    ok = False
                    break
                a_new = a[: k + 1].copy()
                a_new[1:k] = a[1:k] + lam * a[k - 1 : 0 : -1]
                a_new[k] = lam
                a[: k + 1] = a_new
                err *= 1.0 - lam * lam
                if err <= 1e-15:
                    break
            if not ok:
                continue

            roots = np.roots(a)
            pos_roots = roots[np.imag(roots) > 1e-6]
            if len(pos_roots) == 0:
                continue

            ang = np.angle(pos_roots)
            f_cand = ang * (sr_target / (2.0 * math.pi))
            b_cand = -np.log(np.maximum(np.abs(pos_roots), 1e-6)) * (sr_target / math.pi)
            valid = (f_cand >= 50.0) & (f_cand <= max_hz - 30.0) & (b_cand > 0.0) & (b_cand < 1200.0)
            f_cand = f_cand[valid]
            b_cand = b_cand[valid]
            if len(f_cand) == 0:
                continue

            order = np.argsort(f_cand)
            k_keep = min(max_f, len(order))
            freqs_mat[i, :k_keep] = f_cand[order[:k_keep]]
            bw_mat[i, :k_keep] = b_cand[order[:k_keep]]

        return Formant(xs, freqs_mat, bw_mat, self.xmin, self.xmax, dt)

    def to_spectrogram(
        self,
        window_length: float = 0.005,
        maximum_frequency: float = 5000.0,
        time_step: float = 0.002,
        frequency_step: float = 20.0,
        window_shape: str = "Gaussian",
    ) -> Spectrogram:
        """
        基于短时傅里叶变换 (STFT) 生成宽带语谱图对象。
        """
        y = self._mono_signal()
        sr = self.sampling_frequency
        dur = self.duration
        win_len_param = max(0.001, float(window_length))
        phys_win = 2.0 * win_len_param
        dt = float(time_step) if (time_step is not None and float(time_step) > 0.0) else 0.002
        df_step = float(frequency_step) if (frequency_step is not None and float(frequency_step) > 0.0) else 20.0
        max_freq = min(max(100.0, float(maximum_frequency)), 0.5 * sr)

        if self.n_samples == 0 or dur <= 0.0:
            xs = np.array([self.xmin], dtype=np.float64)
            ys = np.array([0.0, max_freq], dtype=np.float64)
            return Spectrogram(xs, ys, np.zeros((2, 1)), self.xmin, self.xmax, 0.0, max_freq, dt, max_freq)

        if dur > phys_win:
            n_times = max(1, int(np.floor((dur - phys_win) / dt)) + 1)
            x1 = self.xmin + 0.5 * (dur - (n_times - 1) * dt)
            xs = x1 + np.arange(n_times, dtype=np.float64) * dt
        else:
            n_times = max(1, int(np.ceil(dur / max(dt, 1e-4))))
            xs = np.linspace(self.xmin + 0.1 * dur, self.xmin + 0.9 * dur, n_times, dtype=np.float64)

        w_samples = max(8, int(round(phys_win * sr)))
        if w_samples % 2 == 0:
            w_samples += 1
        half = w_samples // 2
        t_rel = np.linspace(-1.0, 1.0, w_samples)
        if str(window_shape).lower().startswith("hann"):
            w = 0.5 - 0.5 * np.cos(2.0 * np.pi * np.linspace(0.0, 1.0, w_samples))
        else:
            w = np.exp(-0.5 * (2.5 * t_rel) ** 2)

        n_fft = max(256, 1 << int(np.ceil(np.log2(max(w_samples, sr / df_step)))))
        n_fft = min(n_fft, 4096)

        padded = np.pad(y, (half, half), mode="edge")
        centers = np.clip(np.round((xs - self.xmin) * sr).astype(int), 0, max(0, len(y) - 1))

        f_all = np.fft.rfftfreq(n_fft, d=1.0 / sr)
        f_mask = f_all <= max_freq + 1e-6
        if np.sum(f_mask) < 2:
            f_mask[: min(2, len(f_all))] = True
        ys = f_all[f_mask]
        df_actual = float(ys[1] - ys[0]) if len(ys) > 1 else max_freq

        norm_factor = max(sr * float(np.sum(w ** 2)), 1e-12)
        spec_vals = np.empty((len(ys), n_times), dtype=np.float64)

        chunk_size = 512
        win_offsets = np.arange(w_samples)[None, :]
        for start_f in range(0, n_times, chunk_size):
            end_f = min(n_times, start_f + chunk_size)
            c_idx = centers[start_f:end_f, None] + win_offsets
            frames = padded[c_idx]
            frames = (frames - np.mean(frames, axis=1, keepdims=True)) * w[None, :]
            F = np.fft.rfft(frames, n=n_fft, axis=1)[:, f_mask]
            psd = (np.abs(F) ** 2) / norm_factor
            spec_vals[:, start_f:end_f] = psd.T

        return Spectrogram(xs, ys, spec_vals, self.xmin, self.xmax, 0.0, float(ys[-1]), dt, df_actual)


# ============================================================================
# 4. parselmouth.praat 子模块占位
# ============================================================================

class _PraatSubmodule(types.ModuleType):
    """parselmouth.praat 兼容子模块占位（同时供 AST 拦截与基本对象方法转发测试）。"""

    def __init__(self):
        super().__init__("parselmouth.praat")

    @staticmethod
    def call(obj: Any, command: str, *args: Any, **kwargs: Any) -> Any:
        cmd = str(command or "").strip().lower()
        if isinstance(obj, Sound):
            if "total duration" in cmd:
                return obj.get_total_duration()
            if "to intensity" in cmd:
                min_p = float(args[0]) if len(args) >= 1 else 100.0
                t_step = float(args[1]) if len(args) >= 2 and args[1] else None
                sub_m = bool(args[2]) if len(args) >= 3 else True
                return obj.to_intensity(minimum_pitch=min_p, time_step=t_step, subtract_mean=sub_m)
            if "to pitch" in cmd:
                t_step = float(args[0]) if len(args) >= 1 and args[0] else None
                p_floor = float(args[1]) if len(args) >= 2 else 75.0
                p_ceil = float(args[2]) if len(args) >= 3 else 600.0
                return obj.to_pitch_ac(time_step=t_step, pitch_floor=p_floor, pitch_ceiling=p_ceil)
            if "to formant" in cmd:
                t_step = float(args[0]) if len(args) >= 1 and args[0] else None
                max_f = float(args[1]) if len(args) >= 2 else 5.0
                max_hz = float(args[2]) if len(args) >= 3 else 5500.0
                w_len = float(args[3]) if len(args) >= 4 else 0.025
                pre_emp = float(args[4]) if len(args) >= 5 else 50.0
                return obj.to_formant_burg(
                    time_step=t_step,
                    max_number_of_formants=max_f,
                    maximum_formant=max_hz,
                    window_length=w_len,
                    pre_emphasis_from=pre_emp,
                )
        raise NotImplementedError(f"parselmouth.praat.call fallback does not support command: {command}")

    @staticmethod
    def run_file(*args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError("parselmouth.praat.run_file is not supported in fallback mode")


praat = _PraatSubmodule()


# ============================================================================
# 5. 无头沙盒环境的轻量可选依赖兜底 (仅当系统未安装对应库时按需注册)
# ============================================================================

def _install_textgrid_fallback_if_missing() -> None:
    if "textgrid" in sys.modules or importlib.util.find_spec("textgrid") is not None:
        return

    tg_mod = types.ModuleType("textgrid")

    class Interval:
        def __init__(self, minTime: float, maxTime: float, mark: str = ""):
            self.minTime = float(minTime)
            self.maxTime = float(maxTime)
            self.mark = "" if mark is None else str(mark)

        def __repr__(self) -> str:
            return f"Interval({self.minTime}, {self.maxTime}, {self.mark!r})"

    class Point:
        def __init__(self, time: float, mark: str = ""):
            self.time = float(time)
            self.mark = "" if mark is None else str(mark)

    class IntervalTier:
        def __init__(self, name: str = "", minTime: float = 0.0, maxTime: float = 1.0):
            self.name = str(name or "")
            self.minTime = float(minTime or 0.0)
            self.maxTime = float(maxTime or 0.0)
            self.intervals: List[Interval] = []

        def add(self, minTime: float, maxTime: float, mark: str = "") -> None:
            iv = Interval(minTime, maxTime, mark)
            self.intervals.append(iv)
            self.intervals.sort(key=lambda x: (x.minTime, x.maxTime))
            if iv.maxTime > self.maxTime:
                self.maxTime = iv.maxTime

        def addInterval(self, interval: Interval) -> None:
            self.add(interval.minTime, interval.maxTime, interval.mark)

        def __iter__(self):
            return iter(self.intervals)

        def __len__(self) -> int:
            return len(self.intervals)

        def __getitem__(self, idx: int) -> Interval:
            return self.intervals[idx]

    class PointTier:
        def __init__(self, name: str = "", minTime: float = 0.0, maxTime: float = 1.0):
            self.name = str(name or "")
            self.minTime = float(minTime or 0.0)
            self.maxTime = float(maxTime or 0.0)
            self.points: List[Point] = []

        def add(self, time: float, mark: str = "") -> None:
            self.points.append(Point(time, mark))
            self.points.sort(key=lambda p: p.time)

        def __iter__(self):
            return iter(self.points)

        def __len__(self) -> int:
            return len(self.points)

        def __getitem__(self, idx: int) -> Point:
            return self.points[idx]

    class TextGrid:
        def __init__(self, name: Optional[str] = None, minTime: float = 0.0, maxTime: float = 1.0):
            self.name = name
            self.minTime = float(minTime or 0.0)
            self.maxTime = float(maxTime or 0.0)
            self.tiers: List[Any] = []

        def append(self, tier: Any) -> None:
            self.tiers.append(tier)
            t_max = getattr(tier, "maxTime", None)
            if t_max is not None and float(t_max) > self.maxTime:
                self.maxTime = float(t_max)

        def __iter__(self):
            return iter(self.tiers)

        def __len__(self) -> int:
            return len(self.tiers)

        def __getitem__(self, idx: int) -> Any:
            return self.tiers[idx]

        def write(self, f: Union[str, os.PathLike, io.TextIOBase]) -> None:
            lines = [
                'File type = "ooTextFile"',
                'Object class = "TextGrid"',
                "",
                f"xmin = {self.minTime}",
                f"xmax = {self.maxTime}",
                "tiers? <exists> ",
                f"size = {len(self.tiers)} ",
                "item []: ",
            ]
            for idx, tier in enumerate(self.tiers, start=1):
                lines.append(f"    item [{idx}]:")
                if isinstance(tier, IntervalTier):
                    lines.append('        class = "IntervalTier" ')
                    t_name = str(tier.name).replace('"', '""')
                    lines.append(f'        name = "{t_name}" ')
                    lines.append(f"        xmin = {tier.minTime} ")
                    lines.append(f"        xmax = {tier.maxTime} ")
                    lines.append(f"        intervals: size = {len(tier.intervals)} ")
                    for j, iv in enumerate(tier.intervals, start=1):
                        mark_esc = str(iv.mark).replace('"', '""')
                        lines.append(f"        intervals [{j}]:")
                        lines.append(f"            xmin = {iv.minTime} ")
                        lines.append(f"            xmax = {iv.maxTime} ")
                        lines.append(f'            text = "{mark_esc}" ')
                else:
                    lines.append('        class = "TextTier" ')
                    t_name = str(tier.name).replace('"', '""')
                    lines.append(f'        name = "{t_name}" ')
                    lines.append(f"        xmin = {tier.minTime} ")
                    lines.append(f"        xmax = {tier.maxTime} ")
                    pts = getattr(tier, "points", [])
                    lines.append(f"        points: size = {len(pts)} ")
                    for j, pt in enumerate(pts, start=1):
                        mark_esc = str(pt.mark).replace('"', '""')
                        lines.append(f"        points [{j}]:")
                        lines.append(f"            number = {pt.time} ")
                        lines.append(f'            mark = "{mark_esc}" ')
            content = "\n".join(lines) + "\n"
            if hasattr(f, "write"):
                f.write(content)
            else:
                with open(os.fspath(f), "w", encoding="utf-8") as fp:
                    fp.write(content)

        @classmethod
        def fromFile(cls, f: Union[str, os.PathLike, io.TextIOBase]) -> "TextGrid":
            if hasattr(f, "read"):
                raw_text = f.read()
            else:
                raw_bytes = Path(os.fspath(f)).read_bytes()
                for enc in ("utf-8-sig", "utf-16", "utf-8", "gb18030", "latin-1"):
                    try:
                        raw_text = raw_bytes.decode(enc)
                        break
                    except Exception:
                        continue
            return cls._parse_textgrid_text(raw_text)

        @classmethod
        def _parse_textgrid_text(cls, text: str) -> "TextGrid":
            # 提取所有字符串字面量或数值 token，兼容长格式与短格式 Praat TextGrid
            token_re = re.compile(r'"((?:[^"]|"")*)"|([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)')
            lines = text.splitlines()
            cleaned_lines = []
            for line in lines:
                s = line.strip()
                if not s or s.startswith("File type") or s.startswith("Object class"):
                    continue
                if "tiers?" in s or "item []:" in s:
                    continue
                cleaned_lines.append(s)

            body = "\n".join(cleaned_lines)
            # 移除 item [n]: / intervals [n]: / points [n]: 中的索引用括号以免干扰数字提取
            body = re.sub(r"\b(?:item|intervals|points)\s*\[\s*\d+\s*\]\s*:", "", body)

            tokens: List[Tuple[str, Any]] = []
            for m in token_re.finditer(body):
                if m.group(1) is not None:
                    tokens.append(("str", m.group(1).replace('""', '"')))
                elif m.group(2) is not None:
                    tokens.append(("num", float(m.group(2))))

            if len(tokens) < 3:
                return cls()

            pos = 0

            def next_num(default: float = 0.0) -> float:
                nonlocal pos
                while pos < len(tokens):
                    kind, val = tokens[pos]
                    pos += 1
                    if kind == "num":
                        return float(val)
                return default

            def next_str(default: str = "") -> str:
                nonlocal pos
                while pos < len(tokens):
                    kind, val = tokens[pos]
                    pos += 1
                    if kind == "str":
                        return str(val)
                return default

            tg_min = next_num(0.0)
            tg_max = next_num(1.0)
            n_tiers = int(round(next_num(0.0)))
            tg = cls(minTime=tg_min, maxTime=tg_max)

            for _ in range(max(0, n_tiers)):
                tier_class = next_str("IntervalTier")
                tier_name = next_str("")
                t_min = next_num(tg_min)
                t_max = next_num(tg_max)
                n_items = int(round(next_num(0.0)))
                if "interval" in tier_class.lower():
                    tier = IntervalTier(name=tier_name, minTime=t_min, maxTime=t_max)
                    for _j in range(max(0, n_items)):
                        iv_min = next_num(0.0)
                        iv_max = next_num(0.0)
                        iv_txt = next_str("")
                        tier.intervals.append(Interval(iv_min, iv_max, iv_txt))
                    tg.append(tier)
                else:
                    tier = PointTier(name=tier_name, minTime=t_min, maxTime=t_max)
                    for _j in range(max(0, n_items)):
                        pt_time = next_num(0.0)
                        pt_txt = next_str("")
                        tier.points.append(Point(pt_time, pt_txt))
                    tg.append(tier)

            return tg

    tg_mod.TextGrid = TextGrid
    tg_mod.IntervalTier = IntervalTier
    tg_mod.PointTier = PointTier
    tg_mod.Interval = Interval
    tg_mod.Point = Point
    sys.modules["textgrid"] = tg_mod


def _install_headless_gui_and_audio_stubs_if_missing() -> None:
    if "sounddevice" not in sys.modules and importlib.util.find_spec("sounddevice") is None:
        sd_mod = types.ModuleType("sounddevice")
        sd_mod.play = lambda *a, **kw: None
        sd_mod.stop = lambda *a, **kw: None
        sd_mod.wait = lambda *a, **kw: None
        sd_mod.query_devices = lambda *a, **kw: []
        sys.modules["sounddevice"] = sd_mod

    if "tkinter" not in sys.modules and importlib.util.find_spec("tkinter") is None:
        tk_mod = types.ModuleType("tkinter")

        class _DummyWidget:
            def _draw(self, *args: Any, **kwargs: Any) -> None:
                pass

            def __call__(self, *args: Any, **kwargs: Any) -> Any:
                return None

            def __iter__(self):
                return iter(())

            def __contains__(self, item: Any) -> bool:
                return False

            def items(self):
                return ()

            def values(self):
                return ()

            def keys(self):
                return ()

            def theme_use(self, *args: Any, **kwargs: Any) -> str:
                return "default"

            def map(self, *args: Any, **kwargs: Any) -> None:
                pass

            def __getattr__(self, name: str) -> Any:
                if name.startswith("_"):
                    if name in ("_segmented_button", "_text_label", "_image_label", "_canvas", "_entry", "_textbox", "_dropdown_menu"):
                        child = _DummyWidget()
                        object.__setattr__(self, name, child)
                        return child
                    if name in ("_select_button_by_value", "_draw", "_create_grid"):
                        return lambda *args, **kwargs: None
                    raise AttributeError(name)
                return lambda *args, **kwargs: None
            def __init__(self, *args: Any, **kwargs: Any):
                self._cfg = dict(kwargs)
                self.children = {}
                self.master = args[0] if args else kwargs.get("master")
                self._manager = ""
                self._grid_info = {}
                self._bindings = {}
                self._check_state = False
                self._corner_radius = kwargs.get("corner_radius", 6)
                self.tk = "tk"
                self._after_depth = 0
                self._segmented_button = self
                self._buttons_dict = {}
                self._values = kwargs.get("values", [])
                self._current_value = self._values[0] if self._values else ""
                self._geom = "200x120+0+0" 

            def _mouse_wheel_all(self, *args: Any, **kwargs: Any) -> None:
                pass

            def __call__(self, *args: Any, **kwargs: Any) -> Any:
                return self

            def pack(self, *args: Any, **kwargs: Any) -> None:
                self._manager = "pack"

            def pack_forget(self, *args: Any, **kwargs: Any) -> None:
                self._manager = ""

            def pack_propagate(self, *args: Any, **kwargs: Any) -> None:
                pass

            def grid(self, *args: Any, **kwargs: Any) -> None:
                self._manager = "grid"
                self._grid_info = dict(kwargs) if kwargs else {"row": 0}

            def grid_forget(self, *args: Any, **kwargs: Any) -> None:
                self._manager = ""
                self._grid_info = {}

            def grid_remove(self, *args: Any, **kwargs: Any) -> None:
                self._manager = ""
                self._grid_info = {}

            def grid_info(self) -> Dict[str, Any]:
                return dict(self._grid_info)

            def winfo_manager(self) -> str:
                return self._manager

            def grid_propagate(self, *args: Any, **kwargs: Any) -> None:
                pass

            def grid_rowconfigure(self, *args: Any, **kwargs: Any) -> None:
                pass

            def grid_columnconfigure(self, *args: Any, **kwargs: Any) -> None:
                pass

            def rowconfigure(self, *args: Any, **kwargs: Any) -> None:
                pass

            def columnconfigure(self, *args: Any, **kwargs: Any) -> None:
                pass

            def place(self, *args: Any, **kwargs: Any) -> None:
                pass

            def place_forget(self, *args: Any, **kwargs: Any) -> None:
                pass

            def bind(self, sequence: Any = None, func: Any = None, add: Any = None) -> Any:
                if sequence is None:
                    return tuple(self._bindings.keys())
                self._bindings[str(sequence)] = func
                if str(sequence) == "<Control-c>":
                    self._bindings["<Control-Key-c>"] = func
                return "bind_id"

            def unbind(self, *args: Any, **kwargs: Any) -> None:
                pass

            def configure(self, *args: Any, **kwargs: Any) -> None:
                self._cfg.update(kwargs)

            config = configure

            def cget(self, key: str) -> Any:
                return self._cfg.get(key, "")

            def destroy(self, *args: Any, **kwargs: Any) -> None:
                pass

            def winfo_exists(self) -> bool:
                return True

            def winfo_width(self) -> int:
                return 800

            def winfo_height(self) -> int:
                return 600

            def winfo_screenwidth(self) -> int:
                return 1920

            def winfo_screenheight(self) -> int:
                return 1080

            def winfo_toplevel(self) -> "_DummyWidget":
                return self

            def focus_get(self) -> None:
                return None

            def focus_set(self) -> None:
                pass

            def focus_force(self) -> None:
                pass

            def lift(self) -> None:
                pass

            def title(self, *args: Any) -> None:
                pass

            def geometry(self, *args: Any) -> Any:
                if args:
                    self._geom = str(args[0])
                return getattr(self, "_geom", "200x120+0+0")

            def minsize(self, *args: Any, **kwargs: Any) -> None:
                pass

            def maxsize(self, *args: Any, **kwargs: Any) -> None:
                pass

            def resizable(self, *args: Any, **kwargs: Any) -> None:
                pass

            def withdraw(self, *args: Any, **kwargs: Any) -> None:
                pass

            def deiconify(self, *args: Any, **kwargs: Any) -> None:
                pass

            def iconbitmap(self, *args: Any, **kwargs: Any) -> None:
                pass

            def iconphoto(self, *args: Any, **kwargs: Any) -> None:
                pass

            def wm_iconphoto(self, *args: Any, **kwargs: Any) -> None:
                pass

            def grab_set(self, *args: Any, **kwargs: Any) -> None:
                pass

            def grab_release(self, *args: Any, **kwargs: Any) -> None:
                pass

            def wait_window(self, *args: Any, **kwargs: Any) -> None:
                pass

            def nametowidget(self, *args: Any, **kwargs: Any) -> "_DummyWidget":
                return self

            def itemconfigure(self, *args: Any, **kwargs: Any) -> None:
                pass

            def create_image(self, *args: Any, **kwargs: Any) -> int:
                return 1

            def bind_all(self, *args: Any, **kwargs: Any) -> None:
                pass

            def unbind_all(self, *args: Any, **kwargs: Any) -> None:
                pass

            def winfo_rootx(self) -> int:
                return 0

            def winfo_rooty(self) -> int:
                return 0

            def winfo_reqwidth(self) -> int:
                return 100

            def winfo_reqheight(self) -> int:
                return 30

            def winfo_pointerxy(self) -> Tuple[int, int]:
                return (100, 100)

            def transient(self, *args: Any) -> None:
                pass

            def protocol(self, *args: Any) -> None:
                pass

            def after(self, _ms: int, func: Any = None, *args: Any) -> str:
                if callable(func) and getattr(func, "__name__", "") != "process_gui_queue":
                    depth = getattr(self, "_after_depth", 0)
                    if depth < 2:
                        self._after_depth = depth + 1
                        try:
                            func(*args)
                        finally:
                            self._after_depth = depth
                return "after_id"

            def after_cancel(self, *args: Any) -> None:
                pass

            def update_idletasks(self) -> None:
                pass

            def update(self) -> None:
                pass

            def mainloop(self) -> None:
                pass

            def get(self, *args: Any) -> Any:
                return self._cfg.get("value", "")

            def set(self, val: Any = "") -> None:
                self._cfg["value"] = val

            def select(self) -> None:
                self._check_state = True
                self._cfg["value"] = 1
                self._draw()

            def deselect(self) -> None:
                self._check_state = False
                self._cfg["value"] = 0
                self._draw()

            def insert(self, *args: Any, **kwargs: Any) -> str:
                return "item"

            def delete(self, *args: Any, **kwargs: Any) -> None:
                pass

            def get_children(self, *args: Any) -> Tuple[str, ...]:
                return ()

            def selection(self) -> Tuple[str, ...]:
                return ()

            def selection_set(self, *args: Any, **kwargs: Any) -> None:
                pass

            def selection_add(self, *args: Any, **kwargs: Any) -> None:
                pass

            def heading(self, *args: Any, **kwargs: Any) -> None:
                pass

            def column(self, *args: Any, **kwargs: Any) -> None:
                pass

            def tag_configure(self, *args: Any, **kwargs: Any) -> None:
                pass

            def yview(self, *args: Any, **kwargs: Any) -> None:
                pass

            def xview(self, *args: Any, **kwargs: Any) -> None:
                pass

            def add(self, name: str, *args: Any, **kwargs: Any) -> "_DummyWidget":
                return _DummyWidget()

        class _DummyVar:
            def __init__(self, master: Any = None, value: Any = None, name: Optional[str] = None):
                self._val = value

            def get(self) -> Any:
                return self._val

            def set(self, val: Any) -> None:
                self._val = val

            def trace_add(self, *args: Any, **kwargs: Any) -> str:
                return "trace"

        for const_name, const_val in {
            "NO": False,
            "YES": True,
            "TRUE": True,
            "FALSE": False,
            "WORD": "word",
            "FLAT": "flat",
            "SOLID": "solid",
            "RAISED": "raised",
            "SUNKEN": "sunken",
            "GROOVE": "groove",
            "RIDGE": "ridge",
            "LEFT": "left",
            "RIGHT": "right",
            "TOP": "top",
            "BOTTOM": "bottom",
            "BOTH": "both",
            "X": "x",
            "Y": "y",
            "NONE": "none",
            "END": "end",
            "NORMAL": "normal",
            "DISABLED": "disabled",
            "ACTIVE": "active",
            "HIDDEN": "hidden",
            "NW": "nw",
            "NE": "ne",
            "SW": "sw",
            "SE": "se",
            "N": "n",
            "S": "s",
            "E": "e",
            "W": "w",
            "CENTER": "center",
            "HORIZONTAL": "horizontal",
            "VERTICAL": "vertical",
            "WORD": "word",
            "CHAR": "char",
            "FLAT": "flat",
            "SOLID": "solid",
            "SUNKEN": "sunken",
            "RAISED": "raised",
            "GROOVE": "groove",
            "RIDGE": "ridge",
            "SINGLE": "single",
            "BROWSE": "browse",
            "MULTIPLE": "multiple",
            "EXTENDED": "extended",
        }.items():
            setattr(tk_mod, const_name, const_val)

        tk_mod.Tk = _DummyWidget
        tk_mod.Toplevel = _DummyWidget
        tk_mod.Frame = _DummyWidget
        tk_mod.Label = _DummyWidget
        tk_mod.Button = _DummyWidget
        tk_mod.Entry = _DummyWidget
        tk_mod.Text = _DummyWidget
        tk_mod.Canvas = _DummyWidget
        tk_mod.Listbox = _DummyWidget
        tk_mod.Scrollbar = _DummyWidget
        tk_mod.Menu = _DummyWidget
        tk_mod.PanedWindow = _DummyWidget
        tk_mod.Widget = _DummyWidget
        tk_mod.Event = _DummyWidget
        tk_mod.Variable = _DummyVar
        tk_mod.StringVar = lambda master=None, value="", name=None: _DummyVar(master, value, name)
        tk_mod.IntVar = lambda master=None, value=0, name=None: _DummyVar(master, int(value or 0), name)
        tk_mod.DoubleVar = lambda master=None, value=0.0, name=None: _DummyVar(master, float(value or 0.0), name)
        tk_mod.BooleanVar = lambda master=None, value=False, name=None: _DummyVar(master, bool(value), name)
        tk_mod.TclError = RuntimeError

        ttk_mod = types.ModuleType("tkinter.ttk")
        ttk_mod.Treeview = _DummyWidget
        ttk_mod.Scrollbar = _DummyWidget
        ttk_mod.Style = _DummyWidget
        ttk_mod.Frame = _DummyWidget
        ttk_mod.Label = _DummyWidget
        ttk_mod.Button = _DummyWidget
        ttk_mod.Combobox = _DummyWidget
        ttk_mod.Notebook = _DummyWidget
        ttk_mod.Progressbar = _DummyWidget
        ttk_mod.Separator = _DummyWidget

        mb_mod = types.ModuleType("tkinter.messagebox")
        mb_mod.showinfo = lambda *a, **kw: "ok"
        mb_mod.showwarning = lambda *a, **kw: "ok"
        mb_mod.showerror = lambda *a, **kw: "ok"
        mb_mod.askyesno = lambda *a, **kw: True
        mb_mod.askokcancel = lambda *a, **kw: True
        mb_mod.askyesnocancel = lambda *a, **kw: True

        fd_mod = types.ModuleType("tkinter.filedialog")
        fd_mod.askopenfilename = lambda *a, **kw: ""
        fd_mod.askopenfilenames = lambda *a, **kw: ()
        fd_mod.asksaveasfilename = lambda *a, **kw: ""
        fd_mod.askdirectory = lambda *a, **kw: ""

        sd_dlg_mod = types.ModuleType("tkinter.simpledialog")
        sd_dlg_mod.askstring = lambda *a, **kw: None
        sd_dlg_mod.askinteger = lambda *a, **kw: None
        sd_dlg_mod.askfloat = lambda *a, **kw: None

        font_mod = types.ModuleType("tkinter.font")
        class _DummyFont(_DummyWidget):
            def measure(self, text=""):
                return len(str(text)) * 8
            def metrics(self, *a, **kw):
                return {"ascent": 10, "descent": 2, "linespace": 14}
        font_mod.Font = _DummyFont
        font_mod.nametofont = lambda *a, **kw: _DummyFont()
        font_mod.families = lambda *a, **kw: ("Microsoft YaHei", "Consolas", "Arial")

        tk_mod.__path__ = []
        tk_mod.ttk = ttk_mod
        tk_mod.font = font_mod
        tk_mod.messagebox = mb_mod
        tk_mod.filedialog = fd_mod
        tk_mod.simpledialog = sd_dlg_mod

        sys.modules["tkinter"] = tk_mod
        sys.modules["tkinter.ttk"] = ttk_mod
        sys.modules["tkinter.font"] = font_mod
        sys.modules["tkinter.messagebox"] = mb_mod
        sys.modules["tkinter.filedialog"] = fd_mod
        sys.modules["tkinter.simpledialog"] = sd_dlg_mod

        if "customtkinter" not in sys.modules and importlib.util.find_spec("customtkinter") is None:
            ctk_mod = types.ModuleType("customtkinter")
            for attr_name in (
                "CTk",
                "CTkToplevel",
                "CTkFrame",
                "CTkScrollableFrame",
                "CTkLabel",
                "CTkButton",
                "CTkEntry",
                "CTkTextbox",
                "CTkOptionMenu",
                "CTkComboBox",
                "CTkCheckBox",
                "CTkRadioButton",
                "CTkSwitch",
                "CTkSlider",
                "CTkProgressBar",
                "CTkSegmentedButton",
                "CTkTabview",
                "CTkScrollbar",
                "CTkCanvas",
                "CTkFont",
                "CTkImage",
                "CTkInputDialog",
            ):
                setattr(ctk_mod, attr_name, _DummyWidget)
            ctk_mod.StringVar = tk_mod.StringVar
            ctk_mod.IntVar = tk_mod.IntVar
            ctk_mod.DoubleVar = tk_mod.DoubleVar
            ctk_mod.BooleanVar = tk_mod.BooleanVar
            ctk_mod.set_appearance_mode = lambda *a, **kw: None
            ctk_mod.get_appearance_mode = lambda *a, **kw: "Light"
            ctk_mod.set_default_color_theme = lambda *a, **kw: None
            for const_name in ("LEFT", "RIGHT", "TOP", "BOTTOM", "BOTH", "X", "Y", "END", "NORMAL", "DISABLED"):
                setattr(ctk_mod, const_name, getattr(tk_mod, const_name))
            sys.modules["customtkinter"] = ctk_mod

        try:
            import PIL
            if not hasattr(PIL, "ImageTk"):
                imgtk_mod = types.ModuleType("PIL.ImageTk")
                imgtk_mod.PhotoImage = _DummyWidget
                PIL.ImageTk = imgtk_mod
                sys.modules["PIL.ImageTk"] = imgtk_mod
        except Exception:
            pass

        try:
            import matplotlib.backends.backend_tkagg as _tkagg
        except Exception:
            tkagg_mod = types.ModuleType("matplotlib.backends.backend_tkagg")

            class _DummyFigureCanvasTkAgg:
                def __init__(self, figure: Any = None, master: Any = None):
                    self.figure = figure
                    self.master = master
                    self._tk_widget = _DummyWidget()

                def get_tk_widget(self) -> _DummyWidget:
                    return self._tk_widget

                def draw(self) -> None:
                    pass

                def draw_idle(self) -> None:
                    pass

                def mpl_connect(self, *args: Any, **kwargs: Any) -> int:
                    return 1

                def copy_from_bbox(self, *args: Any, **kwargs: Any) -> Any:
                    return None

                def restore_region(self, *args: Any, **kwargs: Any) -> None:
                    pass

                def blit(self, *args: Any, **kwargs: Any) -> None:
                    pass

            tkagg_mod.FigureCanvasTkAgg = _DummyFigureCanvasTkAgg
            sys.modules["matplotlib.backends.backend_tkagg"] = tkagg_mod


def _install_pytest_fallback_if_missing() -> None:
    if "pytest" in sys.modules or importlib.util.find_spec("pytest") is not None:
        return

    pytest_mod = types.ModuleType("pytest")

    class Skipped(Exception):
        pass

    class _Approx:
        def __init__(self, expected: Any, rel: Optional[float] = None, abs: Optional[float] = None):
            self.expected = expected
            self.rel = 1e-6 if rel is None else float(rel)
            self.abs = 1e-12 if abs is None else float(abs)

        def __eq__(self, actual: Any) -> bool:
            try:
                a = np.asarray(actual, dtype=np.float64)
                e = np.asarray(self.expected, dtype=np.float64)
                return bool(np.allclose(a, e, rtol=self.rel, atol=self.abs, equal_nan=True))
            except Exception:
                return False

        def __repr__(self) -> str:
            return f"approx({self.expected!r}, rel={self.rel}, abs={self.abs})"

    class _RaisesContext:
        def __init__(self, expected_exc: Any, match: Optional[str] = None):
            self.expected_exc = expected_exc
            self.match = match
            self.value = None

        def __enter__(self) -> "_RaisesContext":
            return self

        def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> bool:
            if exc_type is None:
                raise AssertionError(f"DID NOT RAISE {self.expected_exc}")
            if not issubclass(exc_type, self.expected_exc):
                return False
            self.value = exc_val
            if self.match is not None and not re.search(self.match, str(exc_val)):
                raise AssertionError(f"Regex pattern {self.match!r} did not match {str(exc_val)!r}")
            return True

    class _Mark:
        @staticmethod
        def skipif(condition: bool, reason: str = ""):
            def decorator(fn):
                fn._pytest_skipif = (bool(condition), reason)
                return fn
            return decorator

        @staticmethod
        def parametrize(*args: Any, **kwargs: Any):
            def decorator(fn):
                return fn
            return decorator

    def importorskip(modname: str, minversion: Optional[str] = None, reason: Optional[str] = None) -> Any:
        try:
            return __import__(modname)
        except ImportError as exc:
            raise Skipped(reason or f"could not import {modname!r}: {exc}")

    def skip(reason: str = "") -> None:
        raise Skipped(reason)

    def fail(reason: str = "") -> None:
        raise AssertionError(reason)

    pytest_mod.approx = lambda expected, rel=None, abs=None: _Approx(expected, rel=rel, abs=abs)
    pytest_mod.raises = lambda expected_exc, match=None: _RaisesContext(expected_exc, match=match)
    pytest_mod.importorskip = importorskip
    pytest_mod.skip = skip
    pytest_mod.fail = fail
    pytest_mod.mark = _Mark()
    pytest_mod.Skipped = Skipped
    sys.modules["pytest"] = pytest_mod


def register_as_parselmouth() -> None:
    """将当前回退模块注册到 sys.modules['parselmouth'] 及 'parselmouth.praat'。"""
    current_mod = sys.modules.get(__name__)
    if current_mod is not None:
        sys.modules.setdefault("parselmouth", current_mod)
        sys.modules.setdefault("parselmouth.praat", praat)
    _install_textgrid_fallback_if_missing()
    _install_headless_gui_and_audio_stubs_if_missing()
    _install_pytest_fallback_if_missing()


register_as_parselmouth()


__all__ = [
    "Sound",
    "Intensity",
    "Pitch",
    "Formant",
    "Spectrogram",
    "Matrix",
    "praat",
    "IS_FALLBACK",
    "register_as_parselmouth",
]
