import sys
import numpy as np
import pytest
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
    def extract_part(self, from_time=0.0, to_time=1.0, *args, **kwargs):
        if hasattr(self, 'mock_pitch'):
            orig_xs = self.mock_pitch.xs()
            orig_freqs = self.mock_pitch.selected_array['frequency']
            mask = (orig_xs >= from_time) & (orig_xs <= to_time)
            new_freqs = orig_freqs[mask] if any(mask) else np.zeros(10)
            new_xs = orig_xs[mask] - from_time if any(mask) else np.linspace(0, 0.1, 10)
            new_sound = MockSound(duration=to_time - from_time)
            new_sound.mock_pitch = MockPitch(new_freqs, xs_array=new_xs)
            return new_sound
        return self
    def to_pitch_ac(self, *args, **kwargs):
        return getattr(self, 'mock_pitch', MockPitch(np.zeros(100))) # By default return zeros unless explicitly set, to prevent overriding the tests intention
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
    assert "\t0.000000" in result
    assert "150.000000" not in result

def test_export_zero_valid_f0_points():
    # 0 valid F0 points
    freqs = np.zeros(100)
    pitch = MockPitch(freqs)

    item = {'start': 0.1, 'end': 0.9, 'label': 'A', 'snd': MockSound(), 'pitch': pitch}
    result = get_export_text_for_item(item, 1, 11)

    # Should fallback to zeros
    assert "\t0.000000" in result

def test_word_mode_missing_f0():
    freqs = np.zeros(100)
    pitch = MockPitch(freqs)

    item = {'start': 0.1, 'end': 0.9, 'label': 'A/B', 'inner_splits': [0.5], 'snd': MockSound(), 'pitch': pitch}
    item['snd'].mock_pitch = pitch
    result = get_export_text_for_item(item, 1, 11)

    # In word mode, missing F0 should keep each detected sub-segment visible
    # instead of making the preview look like a segment disappeared.
    assert "1_1.A (A/B)" in result
    assert "1_2.B (A/B)" in result
    assert result.count("0.000000") >= 22

def test_word_mode_partial_missing_f0():
    freqs = np.zeros(100)
    freqs[10:40] = 150.0 # Valid F0 only in the first character part
    pitch = MockPitch(freqs)

    item = {'start': 0.1, 'end': 0.9, 'label': 'A/B', 'inner_splits': [0.5], 'snd': MockSound(), 'pitch': pitch}
    item['snd'].mock_pitch = pitch
    result = get_export_text_for_item(item, 1, 11)

    # Should include A and keep B visible with zero-filled data.
    assert "1_1.A (A/B)" in result
    assert "1_2.B (A/B)" in result

def test_word_mode_short_child_uses_parent_pitch_fallback():
    class ShortExtractionSound(MockSound):
        def extract_part(self, from_time=0.0, to_time=1.0, *args, **kwargs):
            child = MockSound(duration=to_time - from_time)
            child.mock_pitch = MockPitch(np.zeros(10), xs_array=np.linspace(0, max(0.001, to_time - from_time), 10))
            return child

    parent_xs = np.linspace(0, 1.0, 100)
    parent_freqs = np.zeros(100)
    parent_freqs[12:38] = 155.0
    parent_freqs[62:88] = 135.0
    pitch = MockPitch(parent_freqs, xs_array=parent_xs)

    item = {
        'start': 0.1, 'end': 0.9, 'label': 'A/B',
        'inner_splits': [0.5], 'snd': ShortExtractionSound(), 'pitch': pitch
    }
    result = get_export_text_for_item(item, 1, 11)

    assert "1_1.A (A/B)" in result
    assert "155.000000" in result
    assert "1_2.B (A/B)" in result
    assert "135.000000" in result
