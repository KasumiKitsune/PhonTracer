import numpy as np
import parselmouth
import pytest
from modules.audio_core import auto_split_inner_word

def test_auto_split_cost_based_valid():
    # Create a parselmouth sound with two distinct peaks and a valley in between
    # Duration: 1.0 second
    sr = 16000
    t = np.linspace(0, 1.0, sr)
    # First syllable peak around 0.25, valley around 0.5, second syllable peak around 0.75
    # Let's shape the amplitude to have a clear valley around 0.5
    env = 0.5 + 0.5 * np.cos(2 * np.pi * 2 * t - np.pi) # Peak at 0.0, 0.5, 1.0 ?
    # Let's define a clean envelope:
    # Vowel 1: 0.1s to 0.4s
    # Vowel 2: 0.6s to 0.9s
    # Valley at 0.5s
    env = np.zeros_like(t)
    env[int(sr*0.1):int(sr*0.4)] = 0.8
    env[int(sr*0.4):int(sr*0.6)] = 0.05 # Valley
    env[int(sr*0.6):int(sr*0.9)] = 0.8

    # Sine wave
    sig = env * np.sin(2 * np.pi * 440 * t)
    # Add a bit of noise
    sig += np.random.normal(0, 0.001, sig.shape)

    snd = parselmouth.Sound(sig, sampling_frequency=sr)

    meta = {}
    splits = auto_split_inner_word(snd, 0.0, 1.0, 2, output_meta=meta)

    # Valley should be around 0.5
    assert len(splits) == 1
    assert abs(splits[0] - 0.5) < 0.08
    assert 'fallback_equal_split' not in meta.get('split_warnings', [])
    assert meta.get('split_confidence', 0) > 0.6

def test_auto_split_duration_constraints():
    # If the valley is at 0.05 (too close to left boundary, segment < 80ms)
    sr = 16000
    t = np.linspace(0, 1.0, sr)
    env = np.zeros_like(t)
    env[0:int(sr*0.05)] = 0.8
    env[int(sr*0.05):int(sr*0.07)] = 0.05 # Valley very close to start
    env[int(sr*0.07):] = 0.8
    sig = env * np.sin(2 * np.pi * 440 * t)
    snd = parselmouth.Sound(sig, sampling_frequency=sr)

    meta = {}
    splits = auto_split_inner_word(snd, 0.0, 1.0, 2, output_meta=meta)

    # Because splitting at ~0.05 violates duration constraint (< 80ms)
    # It should fallback to equal split (0.5)
    assert len(splits) == 1
    assert abs(splits[0] - 0.5) < 0.05
    assert 'fallback_equal_split' in meta.get('split_warnings', [])
    assert 'no_clear_valley' in meta.get('split_warnings', [])
    assert meta.get('split_confidence', 1.0) <= 0.4
