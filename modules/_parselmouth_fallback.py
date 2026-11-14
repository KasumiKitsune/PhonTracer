# -*- coding: utf-8 -*-
"""
无原生 praat-parselmouth 环境下的高保真声学计算回退层门面 (_parselmouth_fallback.py)。

向后兼容门面：所有具体实现已拆分并下沉至 modules._fallbacks 子包。
"""

from __future__ import annotations

import sys

from modules._fallbacks import (
    IS_FALLBACK,
    Data,
    Formant,
    Intensity,
    Matrix,
    Pitch,
    Sound,
    Spectrogram,
    TextGrid,
    _PraatSubmodule,
    __version__,
    _decode_pcm_bytes,
    _install_headless_gui_and_audio_stubs_if_missing,
    _install_pytest_fallback_if_missing,
    _install_textgrid_fallback_if_missing,
    _read_wav_riff_fallback,
    _read_wav_robust,
    install_all_fallbacks,
    praat,
)


def register_as_parselmouth() -> None:
    """将当前回退模块注册到 sys.modules['parselmouth'] 及 'parselmouth.praat'。"""
    current_mod = sys.modules.get(__name__)
    if current_mod is not None:
        sys.modules.setdefault("parselmouth", current_mod)
        sys.modules.setdefault("parselmouth.praat", praat)
    _install_textgrid_fallback_if_missing()
    _install_headless_gui_and_audio_stubs_if_missing()
    _install_pytest_fallback_if_missing()


# 顶层自动执行逻辑
_install_textgrid_fallback_if_missing()
_install_headless_gui_and_audio_stubs_if_missing()
_install_pytest_fallback_if_missing()

try:
    import parselmouth  # type: ignore
except ImportError:
    register_as_parselmouth()


__all__ = [
    "Data",
    "TextGrid",
    "Sound",
    "Intensity",
    "Pitch",
    "Formant",
    "Spectrogram",
    "Matrix",
    "praat",
    "IS_FALLBACK",
    "__version__",
    "_PraatSubmodule",
    "_decode_pcm_bytes",
    "_read_wav_riff_fallback",
    "_read_wav_robust",
    "_install_textgrid_fallback_if_missing",
    "_install_headless_gui_and_audio_stubs_if_missing",
    "_install_pytest_fallback_if_missing",
    "register_as_parselmouth",
    "install_all_fallbacks",
]
