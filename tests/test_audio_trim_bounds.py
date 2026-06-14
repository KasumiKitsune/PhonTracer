import numpy as np

from modules.audio_core import trim_bounds_by_amplitude


def test_trim_bounds_uses_original_start_for_end():
    """静音裁剪时，终点应基于原始片段起点计算。"""
    xs = np.array([0.0, 0.1, 0.2, 0.3])
    values = np.array([0.0, 0.02, 0.03, 0.0])

    start, end = trim_bounds_by_amplitude(1.5, 2.0, xs, values, threshold=0.01)

    assert start == 1.6
    assert end == 1.7


def test_trim_bounds_keeps_original_bounds_without_voiced_samples():
    """没有有效振幅点时，应保留原始边界。"""
    xs = np.array([0.0, 0.1, 0.2])
    values = np.array([0.0, 0.001, 0.0])

    assert trim_bounds_by_amplitude(1.5, 2.0, xs, values, threshold=0.01) == (1.5, 2.0)
