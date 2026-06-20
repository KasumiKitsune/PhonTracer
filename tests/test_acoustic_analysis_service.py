import pytest
import numpy as np
import parselmouth
import hashlib
from modules.acoustic_analysis_service import (
    normalize_analysis_params,
    analyze_audio_to_bundle,
    get_speech_bounds
)

def test_normalize_analysis_params_defaults():
    params = normalize_analysis_params(None)
    assert params["pitch_floor"] == 75
    assert params["pitch_ceiling"] == 600
    assert params["voicing_threshold"] == 0.25
    assert params["very_accurate"] is True

def test_normalize_analysis_params_priorities():
    raw_params = {"pitch_floor": 80, "voicing_threshold": 0.3}
    spk_params = {"pitch_floor": 90, "pitch_ceiling": 500}
    params = normalize_analysis_params(raw_params, spk_params)
    # raw_params 优先级最高
    assert params["pitch_floor"] == 80
    assert params["voicing_threshold"] == 0.3
    # spk_params 次之
    assert params["pitch_ceiling"] == 500
    # 其他为默认值
    assert params["very_accurate"] is True

def test_normalize_analysis_params_invalid_fields():
    with pytest.raises(ValueError, match="未知参数字段"):
        normalize_analysis_params({"unknown_field": 123})

def test_normalize_analysis_params_out_of_bounds():
    with pytest.raises(ValueError, match="pitch_floor 超出合理区间"):
        normalize_analysis_params({"pitch_floor": 20})

    with pytest.raises(ValueError, match="pitch_floor 必须小于 pitch_ceiling"):
        normalize_analysis_params({"pitch_floor": 500, "pitch_ceiling": 400})

def test_get_speech_bounds_basic():
    # 生成静音 -> 声音 -> 静音的数组
    sr = 8000
    y = np.zeros(sr * 2, dtype=np.float32)
    y[int(sr * 0.5):int(sr * 1.5)] = np.sin(2 * np.pi * 440 * np.arange(sr) / sr) * 0.5

    start_sample, end_sample = get_speech_bounds(y, sr)
    assert start_sample < int(sr * 0.5)
    assert end_sample > int(sr * 1.5)

def test_analyze_audio_to_bundle_integration():
    # 构造 Sound 对象
    sr = 8000
    xs = np.arange(sr, dtype=np.float64) / sr
    samples = np.sin(2 * np.pi * 220.0 * xs) * 0.5
    snd = parselmouth.Sound(np.array([samples]), sampling_frequency=sr)

    audio_sha256 = hashlib.sha256(samples.tobytes()).hexdigest()
    params = normalize_analysis_params({"pitch_floor": 75, "pitch_ceiling": 300})

    bundle = analyze_audio_to_bundle(snd, params, audio_sha256)

    assert bundle["schema"] == "phontracer.acoustic-analysis.v1"
    assert bundle["audio_sha256"] == audio_sha256
    assert bundle["duration_seconds"] == pytest.approx(1.0)
    assert bundle["summary"]["voiced_ratio"] > 0.5
    assert bundle["summary"]["f0_median_hz"] == pytest.approx(220.0, abs=5.0)
    assert len(bundle["pitch"]["xs"]) == len(bundle["pitch"]["freqs"])
    assert len(bundle["formants"]["xs"]) == len(bundle["formants"]["f1"])
