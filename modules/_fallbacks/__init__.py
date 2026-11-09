# -*- coding: utf-8 -*-
"""
PhonTracer 回退层子包 (_fallbacks)。

集中管理 praat/parselmouth、textgrid、GUI/音频与 pytest 的高保真回退实现与存根。
"""

from __future__ import annotations

import sys
import types
from typing import Optional

from .headless_gui_stubs import _install_headless_gui_and_audio_stubs_if_missing
from .praat_fallback import (
    IS_FALLBACK,
    Formant,
    Intensity,
    Matrix,
    Pitch,
    Sound,
    Spectrogram,
    _PraatSubmodule,
    __version__,
    _decode_pcm_bytes,
    _read_wav_riff_fallback,
    _read_wav_robust,
    praat,
)
from .pytest_stubs import _install_pytest_fallback_if_missing
from .textgrid_fallback import _install_textgrid_fallback_if_missing


def install_all_fallbacks() -> None:
    """按需安装所有回退模块与桩 (textgrid, headless gui, pytest)。"""
    _install_textgrid_fallback_if_missing()
    _install_headless_gui_and_audio_stubs_if_missing()
    _install_pytest_fallback_if_missing()


def register_as_parselmouth(mod: Optional[types.ModuleType] = None) -> None:
    """将回退模块注册到 sys.modules['parselmouth'] 及 'parselmouth.praat'。"""
    if mod is None:
        mod = sys.modules.get("modules._parselmouth_fallback") or sys.modules.get(__name__)
    if mod is not None:
        sys.modules.setdefault("parselmouth", mod)
        sys.modules.setdefault("parselmouth.praat", praat)
    install_all_fallbacks()


__all__ = [
    "IS_FALLBACK",
    "__version__",
    "Matrix",
    "Intensity",
    "Pitch",
    "Formant",
    "Spectrogram",
    "Sound",
    "_PraatSubmodule",
    "praat",
    "_decode_pcm_bytes",
    "_read_wav_riff_fallback",
    "_read_wav_robust",
    "_install_textgrid_fallback_if_missing",
    "_install_headless_gui_and_audio_stubs_if_missing",
    "_install_pytest_fallback_if_missing",
    "install_all_fallbacks",
    "register_as_parselmouth",
]
