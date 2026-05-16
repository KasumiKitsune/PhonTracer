import sys
from unittest.mock import MagicMock
import numpy as np
import pytest

# Mock modules
sys.modules['parselmouth'] = MagicMock()
import parselmouth

from modules.data_utils import get_export_text_for_item
from modules.audio_core import core_microscopic_vowel_nucleus, auto_split_inner_word

class MockSound:
    def __init__(self, duration=1.0):
        self.values = np.zeros((1, 100))
        self.duration = duration
    def get_total_duration(self):
        return self.duration
    def extract_part(self, *args, **kwargs):
        raise ValueError("Simulated extract error")

class MockPitch:
    def __init__(self, freqs):
        self.selected_array = {'frequency': freqs}
    def xs(self):
        return np.linspace(0, 1.0, len(self.selected_array['frequency']))

def test_core_micro_bounds_exception():
    snd = MockSound()
    pitch = MockPitch(np.ones(100) * 150.0)

    # Should fallback to initial bounds if extraction fails
    final_s, final_e, temp_s, temp_e = core_microscopic_vowel_nucleus(
        snd, pitch, 0.1, 0.9, 10.0, 0.0, False
    )
    assert final_s == 0.1
    assert final_e == 0.9
    assert temp_s == 0.1
    assert temp_e == 0.9

def test_auto_split_inner_word_exception():
    snd = MockSound()

    # Should return fallback splits (equal division) if intensity fails
    splits = auto_split_inner_word(snd, 0.0, 1.0, 3)
    assert len(splits) == 2
    assert abs(splits[0] - 0.333) < 0.01
    assert abs(splits[1] - 0.666) < 0.01

def test_auto_split_inner_word_short_duration():
    snd = MockSound()

    # Should fallback immediately if duration < 0.1
    splits = auto_split_inner_word(snd, 0.0, 0.05, 2)
    assert len(splits) == 1
    assert splits[0] == 0.025
