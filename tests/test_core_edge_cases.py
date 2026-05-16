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

def test_export_f0_gap():
    # Test F0 gap interpolation logic
    freqs = np.ones(100) * 150.0
    freqs[40:60] = 0.0 # gap from x=0.4 to 0.6
    pitch = MockPitch(freqs)

    item = {'start': 0.1, 'end': 0.9, 'label': 'A', 'snd': MockSound(), 'pitch': pitch}
    result = get_export_text_for_item(item, 1, 11)

    # Check if the gap at 0.5 (index 5 of 11) is 0.0
    lines = result.split('\n')
    assert "0.000000" in lines[-7]  # Check a middle point in the gap

def test_export_short_f0():
    # Only 1 valid F0 point
    freqs = np.zeros(100)
    freqs[50] = 150.0
    pitch = MockPitch(freqs)

    item = {'start': 0.1, 'end': 0.9, 'label': 'A', 'snd': MockSound(), 'pitch': pitch}
    result = get_export_text_for_item(item, 1, 11)

    # Should fallback to zeros because we need at least 2 points to interpolate
    assert "   0.000000" in result
    assert "150.000000" not in result

def test_export_zero_valid_f0_points():
    # 0 valid F0 points
    freqs = np.zeros(100)
    pitch = MockPitch(freqs)

    item = {'start': 0.1, 'end': 0.9, 'label': 'A', 'snd': MockSound(), 'pitch': pitch}
    result = get_export_text_for_item(item, 1, 11)

    # Should fallback to zeros
    assert "   0.000000" in result

def test_word_mode_missing_f0():
    freqs = np.zeros(100)
    pitch = MockPitch(freqs)

    item = {'start': 0.1, 'end': 0.9, 'label': 'AB', 'inner_splits': [0.5], 'snd': MockSound(), 'pitch': pitch}
    result = get_export_text_for_item(item, 1, 11)

    # In word mode, missing F0 for a character simply skips that character
    assert result == ""

def test_word_mode_partial_missing_f0():
    freqs = np.zeros(100)
    freqs[10:40] = 150.0 # Valid F0 only in the first character part
    pitch = MockPitch(freqs)

    item = {'start': 0.1, 'end': 0.9, 'label': 'AB', 'inner_splits': [0.5], 'snd': MockSound(), 'pitch': pitch}
    result = get_export_text_for_item(item, 1, 11)

    # Should include A but not B
    assert "1_1.A (AB)" in result
    assert "1_2.B" not in result
