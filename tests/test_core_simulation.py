import sys
from unittest.mock import MagicMock
import numpy as np
import pytest

# Mock modules
sys.modules['parselmouth'] = MagicMock()
import parselmouth

from modules.data_utils import get_export_text_for_item
from modules.audio_core import core_microscopic_vowel_nucleus, auto_split_inner_word

class MockPitch:
    def __init__(self, freqs, xs_array=None):
        self.selected_array = {'frequency': freqs}
        if xs_array is None:
            self.xs_array = np.linspace(0, 1.0, len(freqs))
        else:
            self.xs_array = xs_array
    def xs(self):
        return self.xs_array

class MockSound:
    def __init__(self, duration=1.0):
        self.values = np.zeros((1, 100))
        self.duration = duration
    def get_total_duration(self):
        return self.duration
    def extract_part(self, *args, **kwargs):
        return self
    def to_intensity(self, *args, **kwargs):
        class MockIntensity:
            def __init__(self):
                self.values = np.zeros((1, 100))
                self.values[0][50] = 100 # give it a peak
            def xs(self):
                return np.linspace(0, 1.0, 100)
        return MockIntensity()

def test_export_zero_duration():
    item = {
        'start': 0.1, 'end': 0.1,
        'label': 'A',
        'snd': MockSound(),
        'pitch': MockPitch(np.ones(100) * 150.0)
    }
    result = get_export_text_for_item(item, 1, 11)
    assert result == ""

def test_export_negative_duration():
    item = {
        'start': 0.5, 'end': 0.1,
        'label': 'A',
        'snd': MockSound(),
        'pitch': MockPitch(np.ones(100) * 150.0)
    }
    result = get_export_text_for_item(item, 1, 11)
    assert result == ""

def test_export_missing_snd_pitch():
    item = {'start': 0.1, 'end': 0.5, 'label': 'A'}
    result = get_export_text_for_item(item, 1, 11)
    assert result == ""

def test_export_preview_f0_fallback():
    item = {
        'start': 0.1, 'end': 0.5, 'label': 'A', 'path': 'fake.wav',
        'preview_f0': [150.0] * 11
    }
    # For num_points=11, it should use preview_f0 if snd/pitch are missing
    result = get_export_text_for_item(item, 1, 11)
    assert "0.100000   150.000000" in result
    assert "0.500000   150.000000" in result

def test_export_inner_split_length_mismatch():
    item = {
        'start': 0.1, 'end': 0.5,
        'label': '测试',
        'inner_splits': [0.2, 0.3, 0.4], # Too many splits for 2 chars
        'snd': MockSound(),
        'pitch': MockPitch(np.ones(100) * 150.0)
    }
    result = get_export_text_for_item(item, 1, 11)
    assert "1_1.测" in result
    assert "1_2.试" in result

def test_core_micro_bounds():
    snd = MockSound()
    pitch = MockPitch(np.ones(100) * 150.0)

    final_s, final_e, temp_s, temp_e = core_microscopic_vowel_nucleus(
        snd, pitch, 0.0, 1.0, 10.0, 0.0, False
    )
    assert final_e >= final_s
