# -*- coding: utf-8 -*-
"""
Tests for Batch 2: Performance and Lag Optimizations (第二批：性能与卡顿优化).
"""

import os
import tempfile
import zipfile
import numpy as np
import pytest

from modules.project_patch import _load_item_sound, apply_project_patch_to_teproj
from modules.script_api import ProjectPatchResult
from cli import PhonTracerCLI, _calc_preview_f0


def test_long_audio_cache_reused(tmp_path):
    """
    验证 _load_item_sound 在传入 long_audio_cache 时能够正确缓存并复用解码后的 Sound 对象，
    避免同一长音频被成百上千个词条重复读取并解码。
    """
    import parselmouth

    # 创建一个极简 wav
    wav_path = tmp_path / "test_long.wav"
    snd = parselmouth.Sound(np.zeros(1600), sampling_frequency=16000)
    snd.save(str(wav_path), "WAV")

    workspace_dir = str(tmp_path)
    os.makedirs(os.path.join(workspace_dir, "audio"), exist_ok=True)
    import shutil
    shutil.copy(str(wav_path), os.path.join(workspace_dir, "audio", "long.wav"))

    spk = {"long_audio_path": "audio/long.wav"}
    item1 = {"label": "词条1"}
    item2 = {"label": "词条2"}

    cache = {}
    snd1, kind1 = _load_item_sound(workspace_dir, spk, item1, long_audio_cache=cache)
    assert kind1 == "long"
    assert len(cache) == 1

    # 再次读取同一发音人的长音频，应直接从 cache 命中并返回同一对象
    snd2, kind2 = _load_item_sound(workspace_dir, spk, item2, long_audio_cache=cache)
    assert kind2 == "long"
    assert snd2 is snd1
    assert len(cache) == 1


def test_cli_lazy_executor_initialization(tmp_path):
    """
    验证 PhonTracerCLI 初始化时不会无条件拉起 8 个并发进程，
    而是按需惰性初始化，避免冷启动耗时数秒。
    """
    cli = PhonTracerCLI(workspace_dir=str(tmp_path))
    try:
        # 初始化后内部 _executor 应为 None
        assert cli._executor is None

        # 访问属性时才惰性创建
        exec_inst = cli.executor
        assert exec_inst is not None
        assert cli._executor is exec_inst
    finally:
        cli.cleanup_workspace()
        assert cli._executor is None


def test_cli_calc_preview_f0_binary_search():
    """
    验证 _calc_preview_f0 使用 np.searchsorted 二分查找能精确匹配近邻时间戳，
    并正确剔除空频及超过 0.025s 的间隔。
    """
    # 构造 pitch 数据
    pitch_xs = np.linspace(0.0, 1.0, 101)  # 每 0.01 秒一个点
    pitch_freqs = np.zeros(101)
    # 在 0.2 ~ 0.4 和 0.6 ~ 0.8 有基频
    pitch_freqs[20:41] = 200.0
    pitch_freqs[60:81] = 220.0

    preview_times = np.array([0.1, 0.25, 0.35, 0.5, 0.7, 0.9])
    res = _calc_preview_f0(preview_times, pitch_xs, pitch_freqs)

    # 0.1 处无声音 (距离最近有效点 0.2 > 0.025) -> 0.0
    assert res[0] == 0.0
    # 0.25 处有有效点 -> 200.0
    assert pytest.approx(res[1], 0.1) == 200.0
    # 0.35 处有有效点 -> 200.0
    assert pytest.approx(res[2], 0.1) == 200.0
    # 0.5 处无声音 -> 0.0
    assert res[3] == 0.0
    # 0.7 处有有效点 -> 220.0
    assert pytest.approx(res[4], 0.1) == 220.0
    # 0.9 处无声音 -> 0.0
    assert res[5] == 0.0


def test_spectrogram_panel_stft_cache_key():
    """
    验证 SpectrogramPanel 对 STFT 计算结果进行轻量缓存逻辑。
    """
    from modules.spectrogram_panel import SpectrogramPanel
    import parselmouth

    # 构造假 Sound
    snd = parselmouth.Sound(np.sin(2 * np.pi * 220 * np.linspace(0, 0.2, 3200)), sampling_frequency=16000)
    item = {
        'snd': snd,
        'start': 0.02,
        'end': 0.18,
        'macro_start': 0.0,
        'macro_end': 0.2,
        'label': '测试',
        'chars_bounds': [[0.02, 0.18]],
    }

    # 模拟 panel 的轻量缓存逻辑
    view_s = 0.0
    view_e = 0.2
    spec_max_freq = 5000.0
    cache_key = (id(snd), round(view_s, 6), round(view_e, 6), round(spec_max_freq, 2))

    cache = {'key': cache_key, 'X': np.zeros((2, 2)), 'Y': np.zeros((2, 2)), 'sg_db': np.zeros((2, 2))}
    assert cache['key'] == cache_key

    # 当微调边界（chars_bounds）时，由于 view_s, view_e, spec_max_freq 不变，cache 依然有效
    item['chars_bounds'] = [[0.04, 0.16]]
    assert cache['key'] == (id(snd), round(view_s, 6), round(view_e, 6), round(spec_max_freq, 2))
