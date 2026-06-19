import numpy as np

from PhonRec.backend.main import analyze_recording_quality, normalize_audio_samples


def _tone(sr=16000, duration=1.0, amplitude=0.2):
    xs = np.arange(int(sr * duration), dtype=np.float32) / sr
    return amplitude * np.sin(2 * np.pi * 220 * xs)


def test_quality_accepts_clear_speech_level_signal():
    quality = analyze_recording_quality(_tone(), 16000)
    assert quality["decision"] == "accept"
    assert quality["volume"]["status"] == "normal"
    assert quality["metrics"]["speech_ms"] >= 900


def test_quality_retries_silence_and_severe_clipping():
    silence = analyze_recording_quality(np.zeros(16000, dtype=np.float32), 16000)
    assert silence["decision"] == "retry"
    assert silence["speech"]["abnormal"] is True

    clipped = np.ones(16000, dtype=np.float32)
    clipping = analyze_recording_quality(clipped, 16000)
    assert clipping["decision"] == "retry"
    assert clipping["clipping"]["abnormal"] is True


def test_normalize_stereo_int16_to_mono_float():
    samples = np.array([[32767, -32768], [16384, 16384]], dtype=np.int16)
    normalized = normalize_audio_samples(samples)
    assert normalized.shape == (2,)
    assert normalized.dtype == np.float32
    assert abs(float(normalized[0])) < 0.001
    assert 0.49 < float(normalized[1]) < 0.51
