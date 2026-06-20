import asyncio
import json

import numpy as np
from scipy.io import wavfile

from PhonRec.backend import main as phonrec_backend
from PhonRec.backend.main import analyze_recording_quality, normalize_audio_samples, normalize_quality_config


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


def test_quality_rules_allow_all_checks_to_be_disabled():
    disabled = {
        name: {"enabled": False, "level": "medium"}
        for name in ("speech", "volume", "clipping", "noise", "creak", "dc_offset")
    }
    quality = analyze_recording_quality(np.zeros(16000, dtype=np.float32), 16000, disabled)
    assert quality["decision"] == "accept"
    assert quality["issues"] == []
    assert quality["speech"]["label"] == "未启用"


def test_volume_level_changes_detection_strength():
    signal = _tone(amplitude=0.02)
    low = analyze_recording_quality(signal, 16000, {"volume": {"enabled": True, "level": "low"}})
    high = analyze_recording_quality(signal, 16000, {"volume": {"enabled": True, "level": "high"}})
    assert low["volume"]["status"] == "normal"
    assert high["volume"]["status"] == "too_quiet"


def test_invalid_quality_level_falls_back_to_medium():
    config = normalize_quality_config({"noise": {"enabled": True, "level": "极端"}})
    assert config["noise"]["level"] == "medium"


def test_audio_file_and_reanalysis_endpoints_use_quality_config(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    monkeypatch.setattr(phonrec_backend, "WORKSPACE_DIR", str(workspace))
    monkeypatch.setattr(phonrec_backend, "AUDIO_DIR", str(workspace / "audio"))
    monkeypatch.setattr(phonrec_backend, "DATA_DIR", str(workspace / "data"))
    audio_dir = workspace / "audio" / "spk"
    audio_dir.mkdir(parents=True)
    wavfile.write(audio_dir / "spk_item.wav", 16000, (_tone() * 32767).astype(np.int16))
    monkeypatch.setattr(phonrec_backend, "generate_spectrogram", lambda _y, _sr, *a, **kw: "")

    response = asyncio.run(phonrec_backend.api_get_audio_file("spk", "item"))
    assert response.path.endswith("spk_item.wav")

    disabled = {
        name: {"enabled": False, "level": "medium"}
        for name in ("speech", "volume", "clipping", "noise", "creak", "dc_offset")
    }
    analyzed = asyncio.run(phonrec_backend.api_analyze_audio("spk", "item", json.dumps(disabled)))
    assert analyzed["quality"]["decision"] == "accept"
    assert analyzed["quality"]["volume"]["label"] == "未启用"


def test_get_speech_bounds():
    from PhonRec.backend.main import get_speech_bounds
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

