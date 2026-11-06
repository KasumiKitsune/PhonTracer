import numpy as np
import pytest

try:
    from _archive.legacy_phonrec_backend.quality_check import (
        analyze_recording_quality,
        normalize_audio_samples,
        normalize_quality_config,
        get_speech_bounds,
    )
    HAS_LEGACY_QUALITY = True
except ImportError:
    HAS_LEGACY_QUALITY = False

from modules.app import PhoneticsApp


def _tone(sr=16000, duration=1.0, amplitude=0.2):
    xs = np.arange(int(sr * duration), dtype=np.float32) / sr
    return amplitude * np.sin(2 * np.pi * 220 * xs)


@pytest.mark.skipif(not HAS_LEGACY_QUALITY, reason="Legacy quality check archive not in environment")
def test_quality_accepts_clear_speech_level_signal():
    quality = analyze_recording_quality(_tone(), 16000)
    assert quality["decision"] == "accept"
    assert quality["volume"]["status"] == "normal"
    assert quality["metrics"]["speech_ms"] >= 900


@pytest.mark.skipif(not HAS_LEGACY_QUALITY, reason="Legacy quality check archive not in environment")
def test_quality_retries_silence_and_severe_clipping():
    silence = analyze_recording_quality(np.zeros(16000, dtype=np.float32), 16000)
    assert silence["decision"] == "retry"
    assert silence["speech"]["abnormal"] is True

    clipped = np.ones(16000, dtype=np.float32)
    clipping = analyze_recording_quality(clipped, 16000)
    assert clipping["decision"] == "retry"
    assert clipping["clipping"]["abnormal"] is True


@pytest.mark.skipif(not HAS_LEGACY_QUALITY, reason="Legacy quality check archive not in environment")
def test_normalize_stereo_int16_to_mono_float():
    samples = np.array([[32767, -32768], [16384, 16384]], dtype=np.int16)
    normalized = normalize_audio_samples(samples)
    assert normalized.shape == (2,)
    assert normalized.dtype == np.float32
    assert abs(float(normalized[0])) < 0.001
    assert 0.49 < float(normalized[1]) < 0.51


@pytest.mark.skipif(not HAS_LEGACY_QUALITY, reason="Legacy quality check archive not in environment")
def test_quality_rules_allow_all_checks_to_be_disabled():
    disabled = {
        name: {"enabled": False, "level": "medium"}
        for name in ("speech", "volume", "clipping", "noise", "creak", "dc_offset")
    }
    quality = analyze_recording_quality(np.zeros(16000, dtype=np.float32), 16000, disabled)
    assert quality["decision"] == "accept"
    assert quality["issues"] == []
    assert quality["speech"]["label"] == "未启用"


@pytest.mark.skipif(not HAS_LEGACY_QUALITY, reason="Legacy quality check archive not in environment")
def test_volume_level_changes_detection_strength():
    signal = _tone(amplitude=0.02)
    low = analyze_recording_quality(signal, 16000, {"volume": {"enabled": True, "level": "low"}})
    high = analyze_recording_quality(signal, 16000, {"volume": {"enabled": True, "level": "high"}})
    assert low["volume"]["status"] == "normal"
    assert high["volume"]["status"] == "too_quiet"


@pytest.mark.skipif(not HAS_LEGACY_QUALITY, reason="Legacy quality check archive not in environment")
def test_invalid_quality_level_falls_back_to_medium():
    config = normalize_quality_config({"noise": {"enabled": True, "level": "极端"}})
    assert config["noise"]["level"] == "medium"


@pytest.mark.skipif(not HAS_LEGACY_QUALITY, reason="Legacy quality check archive not in environment")
def test_get_speech_bounds():
    sr = 16000
    silence_start = np.zeros(int(sr * 0.5), dtype=np.float32)
    tone = _tone(sr=sr, duration=1.0, amplitude=0.2)
    silence_end = np.zeros(int(sr * 0.5), dtype=np.float32)
    y = np.concatenate([silence_start, tone, silence_end])

    start_sample, end_sample = get_speech_bounds(y, sr)

    expected_start = int((0.5 - 0.15) * sr)
    expected_end = int((1.5 + 0.15) * sr)

    # Assert within 50ms tolerance (frame size is 20ms)
    assert abs(start_sample - expected_start) <= int(sr * 0.05)
    assert abs(end_sample - expected_end) <= int(sr * 0.05)


def test_phonrec_cli_handoff_arguments():
    # Test static normalization
    raw_args = ["--speaker", "spk_123", "handoff.teproj", "--item", "item_456"]
    cleaned = PhoneticsApp._normalize_startup_files(raw_args)
    assert cleaned == ["handoff.teproj"]

    # Test with dummy instance capturing speaker and item
    class DummyApp:
        _startup_target_speaker = None
        _startup_target_item = None

        def _sync_ui_after_project_load(self):
            pass

    dummy = DummyApp()
    cleaned_dummy = PhoneticsApp._normalize_startup_files(dummy, ["--speaker=spk_abc", "handoff.teproj", "--item=item_xyz"])
    assert cleaned_dummy == ["handoff.teproj"]
    assert dummy._startup_target_speaker == "spk_abc"
    assert dummy._startup_target_item == "item_xyz"
