import sys
import numpy as np
import pytest
import parselmouth

from modules.data_utils import get_export_text_for_item
from modules.audio_core import core_microscopic_vowel_nucleus, auto_split_inner_word, detect_vowel_onset

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
        return getattr(self, 'mock_pitch', MockPitch(np.ones(100) * 150.0))
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
    assert "0.100000\t150.000000" in result
    assert "0.500000\t150.000000" in result

def test_export_inner_split_length_mismatch():
    pitch = MockPitch(np.ones(100) * 150.0)
    snd = MockSound()
    snd.mock_pitch = pitch
    item = {
        'start': 0.1, 'end': 0.5,
        'label': '测试',
        'inner_splits': [0.2, 0.3, 0.4], # Too many splits for 2 chars
        'snd': snd,
        'pitch': pitch
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

def test_detect_vowel_onset_basic():
    # Create a synthetic sound: silence then a sine wave starting at 0.5s
    sr = 16000
    duration = 1.0
    t = np.linspace(0, duration, int(sr * duration))
    # 0 to 0.5 is silence, 0.5 to 1.0 is 440Hz sine wave (simulating vowel onset)
    vals = np.zeros_like(t)
    vals[int(sr * 0.5):] = 0.5 * np.sin(2 * np.pi * 440 * t[int(sr * 0.5):])
    
    # Inject Gaussian noise to avoid absolute zero issues
    vals += np.random.normal(0, 0.001, vals.shape)
    
    snd = parselmouth.Sound(vals, sampling_frequency=sr)
    
    # Detect VOP around 0.5s. We search in range [0.3, 0.7].
    vop = detect_vowel_onset(snd, 0.3, 0.7)
    
    # Expect VOP to be near 0.5s (within 50ms buffer due to windowing/smoothing)
    assert abs(vop - 0.5) < 0.05
