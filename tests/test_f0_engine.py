import numpy as np
from unittest.mock import patch

from modules.audio_core import extract_f0, auto_split_to_chars_bounds
from modules.data_utils import get_export_text_for_item

class MockPitch:
    def __init__(self, freqs, xs_array=None):
        self.selected_array = {'frequency': freqs}
        if xs_array is None:
            self.xs_array = np.linspace(0.01, 1.0, len(freqs))
        else:
            self.xs_array = xs_array
    def xs(self):
        return self.xs_array

class MockSound:
    def __init__(self, duration=1.0, values=None, sampling_frequency=44100):
        self.duration = duration
        if values is None:
            self.values = np.zeros((1, 1000))
            # Put some dummy audio signal
            self.values[0] = np.sin(2 * np.pi * 100 * np.linspace(0, duration, 1000))
        else:
            self.values = values
        self.sampling_frequency = sampling_frequency

    def get_total_duration(self):
        return self.duration

    def extract_part(self, from_time=0.0, to_time=1.0, *args, **kwargs):
        sub_samples = int((to_time - from_time) * self.sampling_frequency)
        sub_values = np.zeros((1, sub_samples)) if sub_samples > 0 else np.zeros((1, 2))
        return MockSound(duration=to_time - from_time, values=sub_values, sampling_frequency=self.sampling_frequency)

    def to_pitch_ac(self, *args, **kwargs):
        # Return a deterministic mock pitch for Praat tests
        return MockPitch(np.array([120.0, 130.0, 0.0, 140.0, 150.0]))

def test_extract_f0_praat():
    snd = MockSound()
    params = {
        'pitch_floor': 75,
        'pitch_ceiling': 500,
        'voicing_threshold': 0.25
    }
    # Test standard praat mode (calls to_pitch_ac)
    res = extract_f0(snd, params)
    assert res['engine'] == 'praat'
    assert isinstance(res['xs'], np.ndarray)
    assert isinstance(res['freqs'], np.ndarray)
    assert len(res['xs']) == len(res['freqs'])
    # 0.0 or positive frequencies only, no NaNs
    assert not np.any(np.isnan(res['freqs']))
    assert np.all(res['freqs'] >= 0.0)

def test_extract_f0_ignores_removed_legacy_engine():
    snd = MockSound()
    params = {
        'f0_engine': 'reaper',
        'pitch_floor': 75,
        'pitch_ceiling': 500,
        'voicing_threshold': 0.25
    }

    res = extract_f0(snd, params)

    assert res['engine'] == 'praat'
    assert isinstance(res['xs'], np.ndarray)
    assert isinstance(res['freqs'], np.ndarray)

def test_auto_split_to_chars_bounds_praat():
    snd = MockSound(duration=1.0)
    params = {
        'pitch_floor': 75,
        'pitch_ceiling': 500,
        'voicing_threshold': 0.25
    }
    
    # In MockSound, to_pitch_ac returns MockPitch with valid F0 points at [120, 130, 0, 140, 150]
    # The xs are spaced from 0.01 to 1.0. Valid xs are around [0.01, 0.25, 0.75, 1.0]
    # Therefore we expect a successful boundary extraction
    bounds = auto_split_to_chars_bounds(snd, 0.0, 1.0, [0.5], 2, params)
    assert len(bounds) == 2
    # Boundaries should contain sub-bounds
    assert bounds[0][0] >= 0.0
    assert bounds[0][1] <= 1.0

def test_get_export_text_for_item_with_pitch_data():
    item = {
        'start': 0.1,
        'end': 0.9,
        'label': 'A',
        'snd': MockSound(duration=1.0),
        'pitch_data': {
            'xs': np.array([0.1, 0.3, 0.5, 0.7, 0.9]),
            'freqs': np.array([150.0, 155.0, 160.0, 0.0, 165.0]),  # Contains a voiced-unvoiced gap
            'engine': 'praat'
        },
        'pitch_floor': 75,
        'pitch_ceiling': 500,
        'voicing_threshold': 0.25,
    }

    # Verify standard export works with pitch_data
    result = get_export_text_for_item(item, 1, 11)
    assert "1.A" in result
    # Check that it interpolates correctly and converts unvoiced regions (>25ms gap) to 0.0
    lines = result.strip().split('\n')
    assert len(lines) >= 12  # Header + duration + 11 points
    
    # Check that middle values are present
    freq_values = []
    for line in lines[2:]:
        parts = line.split()
        if len(parts) == 2:
            freq_values.append(float(parts[1]))
            
    assert len(freq_values) == 11
    assert any(f > 0 for f in freq_values)

def test_get_export_text_for_item_with_eraser_sync():
    # Multi-word item with parent pitch_data representing manual edits
    parent_xs = np.linspace(0.1, 0.9, 10)
    parent_freqs = np.array([150.0, 152.0, 154.0, 0.0, 0.0, 156.0, 158.0, 160.0, 162.0, 164.0]) # Erasure at indices 3 and 4
    
    item = {
        'start': 0.1,
        'end': 0.9,
        'label': 'A/B',
        'snd': MockSound(duration=1.0),
        'pitch_data': {
            'xs': parent_xs,
            'freqs': parent_freqs,
            'engine': 'praat'
        },
        'chars_bounds': [[0.1, 0.5], [0.5, 0.9]],
        'pitch_floor': 75,
        'pitch_ceiling': 500,
        'voicing_threshold': 0.25,
    }

    # Mock extract_f0 inside get_export_text_for_item to return F0 points that would normally be non-zero
    # to prove that our erasure mapping sets them to 0.0
    mock_child_pitch = {
        'xs': np.linspace(0.01, 0.39, 10), # Will be offset by c_start
        'freqs': np.array([150.0] * 10),
        'engine': 'praat'
    }

    with patch('modules.audio_core.extract_f0', return_value=mock_child_pitch) as mock_extract:
        result = get_export_text_for_item(item, 1, 11)
        
    assert "1_1.A (A/B)" in result
    assert "1_2.B (A/B)" in result
    
    lines = result.strip().split('\n')
    # For A: bounds [0.1, 0.5].
    # Child xs (offset by 0.1): np.linspace(0.11, 0.49, 10)
    # The parent has erasures at parent_xs = [0.1, 0.188, 0.277, 0.366, 0.455, 0.544, ...]
    # So around 0.366 and 0.455 the parent_freqs are 0.0.
    # The child F0 array points close to these should be mapped to 0.0.
    zero_lines = [line for line in lines if "0.000000" in line]
    assert len(zero_lines) > 0, "Eraser synchronization should map erased parent F0 to 0.0 in exported text"

