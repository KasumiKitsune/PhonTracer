import numpy as np
import pytest
from unittest.mock import MagicMock
from modules.anomaly_detection import detect_pitch_anomaly_points
from modules.project_tree import ProjectTreePanel


def make_panel():
    panel = MagicMock(spec=ProjectTreePanel)
    panel.analyze_item_anomalies = ProjectTreePanel.analyze_item_anomalies
    panel._get_syllables_and_bounds = lambda item: ProjectTreePanel._get_syllables_and_bounds(panel, item)
    panel._check_item_has_empty_data = MagicMock(return_value=False)
    return panel


def test_point_level_anomaly_detection():
    # Setup mock ProjectTreePanel
    panel = make_panel()

    # 1. Test case: no jump
    xs = np.linspace(0, 1.0, 100)
    freqs_normal = np.array([150.0] * 100)
    item_normal = {
        'start': 0.0,
        'end': 1.0,
        'label': 'a',
        'pitch_data': {
            'xs': xs,
            'freqs': freqs_normal
        }
    }
    panel._get_pitch_arrays_for_item = MagicMock(return_value=(item_normal['pitch_data']['xs'], item_normal['pitch_data']['freqs']))
    panel._extract_item_features = MagicMock(return_value={
        'duration': 1.0, 'mean_f0': 150.0, 'f0_range': 0.0, 'active_ratio': 1.0
    })

    warnings = panel.analyze_item_anomalies(panel, item_normal)
    assert not any("疑似倍频/半频/噪声点" in w for w in warnings)

    # 2. Test case: octave jump (point-level anomaly)
    freqs_jump = np.array([150.0] * 100)
    freqs_jump[50] = 300.0 # Jump to double frequency at index 50
    item_jump = {
        'start': 0.0,
        'end': 1.0,
        'label': 'a',
        'pitch_data': {
            'xs': xs,
            'freqs': freqs_jump
        }
    }
    panel._get_pitch_arrays_for_item = MagicMock(return_value=(item_jump['pitch_data']['xs'], item_jump['pitch_data']['freqs']))

    warnings = panel.analyze_item_anomalies(panel, item_jump)
    assert any("疑似倍频/半频/噪声点" in w for w in warnings)

def test_pitch_jump_detection_does_not_cross_word_internal_boundaries():
    panel = make_panel()

    xs = np.array([0.10, 0.20, 0.30, 0.34])
    freqs = np.array([100.0, 100.0, 155.0, 155.0])
    item = {
        'start': 0.0,
        'end': 0.5,
        'label': '运动',
        'chars_bounds': [[0.0, 0.22], [0.28, 0.5]],
        'pitch_data': {
            'xs': xs,
            'freqs': freqs
        }
    }
    panel._get_pitch_arrays_for_item = MagicMock(return_value=(xs, freqs))
    panel._extract_item_features = MagicMock(return_value={
        'duration': 0.5, 'mean_f0': 127.5, 'f0_range': 55.0, 'active_ratio': 1.0
    })

    warnings = panel.analyze_item_anomalies(panel, item)
    assert not any("疑似倍频/半频/噪声点" in w for w in warnings)


def test_moderate_pitch_onset_inside_syllable_is_not_hard_anomaly():
    panel = make_panel()

    xs = np.array([0.10, 0.11, 0.12, 0.13, 0.14, 0.30, 0.31, 0.32, 0.33, 0.34])
    freqs = np.array([90.0, 145.0, 140.0, 135.0, 130.0, 95.0, 100.0, 105.0, 110.0, 115.0])
    item = {
        'start': 0.0,
        'end': 0.5,
        'label': '洋山',
        'chars_bounds': [[0.0, 0.20], [0.28, 0.5]],
        'pitch_data': {
            'xs': xs,
            'freqs': freqs
        }
    }
    panel._get_pitch_arrays_for_item = MagicMock(return_value=(xs, freqs))
    panel._extract_item_features = MagicMock(return_value={
        'duration': 0.5, 'mean_f0': 116.5, 'f0_range': 55.0, 'active_ratio': 1.0
    })

    warnings = panel.analyze_item_anomalies(panel, item)
    assert not any("疑似倍频/半频/噪声点" in w for w in warnings)


def test_anomaly_detection_marks_all_points_in_short_bad_run():
    xs = np.array([0.00, 0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.07, 0.08, 0.09, 0.10])
    freqs = np.array([150.0, 150.0, 148.0, 151.0, 150.0, 300.0, 305.0, 295.0, 150.0, 149.0, 151.0])

    points = detect_pitch_anomaly_points(xs, freqs, bounds=[[0.0, 0.10]], start=0.0, end=0.10)

    assert [round(t, 2) for t, _ in points] == [0.05, 0.06, 0.07]
    assert [round(f) for _, f in points] == [300, 305, 295]


def test_statistical_outlier_is_soft_tip():
    panel = make_panel()
    panel._get_pitch_arrays_for_item = MagicMock(return_value=(None, None))

    group_stats = {
        ('一组', 1): {
            'duration': (0.5, 0.15),
            'mean_f0': (150.0, 35.0),
            'f0_range': (20.0, 40.0),
            'active_ratio': (0.9, 0.20)
        }
    }

    item_outlier = {
        'start': 0.0,
        'end': 1.3,
        'group': '一组',
        'chars_bounds': [[0.0, 1.3]]
    }
    panel._extract_item_features = MagicMock(return_value={
        'duration': 1.3, 'mean_f0': 150.0, 'f0_range': 20.0, 'active_ratio': 0.9
    })

    warnings = panel.analyze_item_anomalies(panel, item_outlier, group_stats=group_stats)
    assert any("时长明显偏离" in w for w in warnings)
    assert not any(w.startswith("[警告]") and "时长" in w for w in warnings)

def test_moderate_duration_or_f0_difference_is_not_flagged():
    panel = make_panel()
    panel._get_pitch_arrays_for_item = MagicMock(return_value=(None, None))

    group_stats = {
        ('一组', 1): {
            'duration': (0.5, 0.15),
            'mean_f0': (150.0, 35.0),
            'f0_range': (20.0, 40.0),
            'active_ratio': (0.9, 0.20)
        }
    }
    item = {
        'start': 0.0,
        'end': 0.75,
        'group': '一组',
        'chars_bounds': [[0.0, 0.75]]
    }
    panel._extract_item_features = MagicMock(return_value={
        'duration': 0.75, 'mean_f0': 190.0, 'f0_range': 35.0, 'active_ratio': 0.85
    })

    warnings = panel.analyze_item_anomalies(panel, item, group_stats=group_stats)
    assert not any("明显偏离" in w or "有效点比例偏低" in w for w in warnings)
