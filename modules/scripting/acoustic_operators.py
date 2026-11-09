# -*- coding: utf-8 -*-
"""
高阶声学算子（语谱图提取与多宫格绘图、嗓音发声态、频谱四阶矩、跨发音人共振峰归一化）。
"""

import math
import os
import numpy as np

from .models import (
    _coerce_time,
    _hz_to_bark_arr,
    _hz_to_erb_arr,
    _item_display_label,
)
from .snapshot import DatasetSnapshot


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


def normalize_formants(self, df_or_items=None, method="lobanov"):
    """
    跨发音人元音共振峰归一化工具函数。
    支持 method = "lobanov" | "nearey1" | "bark" | "erb" | "watt_fabricius"。
    返回带有 f1_norm, f2_norm (及 <method>_f1, <method>_f2) 列的 pandas.DataFrame。
    兼容通过 ctx.normalize_formants(df, method=...) 或直接 normalize_formants(df, method=...) 调用。
    """
    import pandas as pd
    if df_or_items is None and isinstance(self, (pd.DataFrame, DatasetSnapshot, list)):
        df_or_items = self
    elif isinstance(df_or_items, str) and isinstance(self, (pd.DataFrame, DatasetSnapshot, list)):
        method = df_or_items
        df_or_items = self

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
