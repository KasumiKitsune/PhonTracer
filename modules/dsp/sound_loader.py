# -*- coding: utf-8 -*-
"""
PhonTracer 音频加载、WAV 头修复与静音振幅裁剪底层模块 (modules.dsp.sound_loader)
"""
import os
import shutil
import struct
import tempfile
from typing import Tuple

import numpy as np

try:
    import parselmouth
except ImportError:
    from modules import _parselmouth_fallback as parselmouth
    import sys as _sys
    _sys.modules.setdefault("parselmouth", parselmouth)
    _sys.modules.setdefault("parselmouth.praat", parselmouth.praat)

# 静音阈值：振幅 ≈ -50dB (10^(-50/20) ≈ 0.00316)
SILENCE_AMPLITUDE_THRESHOLD = 10 ** (-50 / 20)


def repair_wav_header(path) -> bool:
    """修复旧版 PhonRec 写错的 PCM WAV 平均字节率字段。

    旧版录音文件可能把单声道 16-bit WAV 的 ``nAvgBytesPerSec`` 写成
    采样率的四倍。音频数据本身没有损坏，但 libsndfile/Praat 会拒绝读取。
    这里只在 RIFF/WAVE、fmt 块完整且其他关键字段可信时修正该字段。
    """
    if not path or os.path.splitext(str(path))[1].lower() not in {".wav", ".wave"}:
        return False

    try:
        with open(path, "r+b") as wav_file:
            header = wav_file.read(12)
            if len(header) != 12 or header[:4] not in {b"RIFF", b"RF64"} or header[8:12] != b"WAVE":
                return False

            while True:
                chunk_header = wav_file.read(8)
                if len(chunk_header) != 8:
                    return False
                chunk_id, chunk_size = struct.unpack("<4sI", chunk_header)
                chunk_data_offset = wav_file.tell()

                if chunk_id == b"fmt ":
                    if chunk_size < 16:
                        return False
                    fmt_data = wav_file.read(16)
                    if len(fmt_data) != 16:
                        return False
                    audio_format, channels, sample_rate, byte_rate, block_align, bits_per_sample = struct.unpack(
                        "<HHIIHH", fmt_data
                    )
                    if audio_format not in {1, 3, 0xFFFE}:
                        return False
                    if channels <= 0 or sample_rate <= 0 or block_align <= 0 or bits_per_sample <= 0:
                        return False
                    expected_block_align = channels * ((bits_per_sample + 7) // 8)
                    if block_align != expected_block_align:
                        return False

                    expected_byte_rate = sample_rate * block_align
                    if expected_byte_rate > 0xFFFFFFFF or byte_rate == expected_byte_rate:
                        return False

                    wav_file.seek(chunk_data_offset + 8)
                    wav_file.write(struct.pack("<I", expected_byte_rate))
                    wav_file.flush()
                    return True

                wav_file.seek(chunk_data_offset + chunk_size + (chunk_size % 2))
    except (OSError, ValueError, OverflowError, struct.error):
        return False


def load_compatible_sound(path: str) -> parselmouth.Sound:
    """
    鲁棒加载音频为 parselmouth.Sound 对象。
    0. 若为旧版 PhonRec 错误 WAV 头，优先原地修正 4 字节 byte_rate。
    1. 尝试原生 parselmouth.Sound 加载。
    2. 若原生加载失败，尝试用 scipy.io.wavfile 在内存中解码并转换为 Sound 对象（零临时文件）。
    3. 若仍然失败，尝试拷贝至临时文件执行 WAV 头修复，并在加载后立即清理临时文件。
    """
    try:
        repair_wav_header(path)
    except Exception:
        pass

    try:
        return parselmouth.Sound(str(path))
    except Exception:
        pass

    try:
        import scipy.io.wavfile
        sr, data = scipy.io.wavfile.read(str(path))
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

        if values.ndim > 1:
            values = values.T
        return parselmouth.Sound(values, sampling_frequency=float(sr))
    except Exception:
        pass

    tmp_path = None
    try:
        tmp_file = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        tmp_path = tmp_file.name
        tmp_file.close()
        shutil.copyfile(str(path), tmp_path)

        try:
            repair_wav_header(tmp_path)
        except Exception:
            pass
        return parselmouth.Sound(tmp_path)
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass


def _get_mono_values(part: parselmouth.Sound) -> np.ndarray:
    """将多声道音频均值混合为单声道一维数组，避免右声道录音被误判为静音。"""
    if part.values.ndim > 1 and part.values.shape[0] > 1:
        return np.mean(part.values, axis=0)
    return part.values[0]


def trim_bounds_by_amplitude(
    start: float,
    end: float,
    xs,
    values,
    threshold: float = SILENCE_AMPLITUDE_THRESHOLD,
) -> Tuple[float, float]:
    """
    根据片段内相对时间轴和振幅，返回剔除首尾静音后的绝对时间边界。
    """
    try:
        xs_arr = np.asarray(xs, dtype=float)
        vals_arr = np.asarray(values, dtype=float)
        if xs_arr.ndim != 1 or vals_arr.ndim != 1 or len(xs_arr) == 0 or len(xs_arr) != len(vals_arr):
            return start, end
        valid_idx = np.where(np.abs(vals_arr) > threshold)[0]
        if len(valid_idx) == 0:
            return start, end
        base_start = float(start)
        trimmed_start = base_start + float(xs_arr[valid_idx[0]])
        trimmed_end = base_start + float(xs_arr[valid_idx[-1]])
        if trimmed_end <= trimmed_start:
            return start, end
        return trimmed_start, trimmed_end
    except Exception:
        return start, end


__all__ = [
    "SILENCE_AMPLITUDE_THRESHOLD",
    "repair_wav_header",
    "load_compatible_sound",
    "_get_mono_values",
    "trim_bounds_by_amplitude",
]
