import uuid
import hashlib
import json
import numpy as np
import parselmouth
from typing import Dict, Any, List, Optional, Tuple
from modules.audio_core import extract_f0, extract_formants

DEFAULT_PARAMS = {
    "pitch_floor": 75,
    "pitch_ceiling": 600,
    "voicing_threshold": 0.25,
    "very_accurate": True,
    "formant_count": 5,
    "formant_max_hz": 5500.0,
    "formant_window_length": 0.025,
    "formant_pre_emphasis": 50.0,
    "formant_sample_strategy": "整段11点",
    "pts": 11,
    "show_f3": False,
    "db": 60.0,
    "skip_front": 0.0,
    "analysis_mode": "f0"
}

def normalize_analysis_params(raw_params: Optional[Dict[str, Any]], speaker_params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    规范化声学分析参数，优先级为：
    1. raw_params 中的显式值
    2. speaker_params (发音人的 last_params)
    3. 默认值
    """
    normalized = {}

    # 建立合并的基础参数
    base_params = {}
    base_params.update(DEFAULT_PARAMS)
    if speaker_params and isinstance(speaker_params, dict):
        # 仅合并合法键值，过滤掉非法的未知字段
        for k, v in speaker_params.items():
            if k in DEFAULT_PARAMS:
                base_params[k] = v

    if raw_params and isinstance(raw_params, dict):
        for k, v in raw_params.items():
            if k in DEFAULT_PARAMS:
                base_params[k] = v
            else:
                # 按照方案要求，未知字段必须报错 422 (我们抛出 ValueError 由上层统一处理成 422 响应)
                raise ValueError(f"未知参数字段: {k}")

    # 进行类型转换与取值范围校验
    try:
        normalized["pitch_floor"] = int(base_params["pitch_floor"])
        normalized["pitch_ceiling"] = int(base_params["pitch_ceiling"])
        normalized["voicing_threshold"] = float(base_params["voicing_threshold"])
        normalized["very_accurate"] = bool(base_params["very_accurate"])
        normalized["formant_count"] = int(base_params["formant_count"])
        normalized["formant_max_hz"] = float(base_params["formant_max_hz"])
        normalized["formant_window_length"] = float(base_params["formant_window_length"])
        normalized["formant_pre_emphasis"] = float(base_params["formant_pre_emphasis"])
        normalized["formant_sample_strategy"] = str(base_params["formant_sample_strategy"])
        normalized["pts"] = int(base_params["pts"])
        normalized["show_f3"] = bool(base_params["show_f3"])
        normalized["db"] = float(base_params["db"])
        normalized["skip_front"] = float(base_params["skip_front"])
        normalized["analysis_mode"] = str(base_params["analysis_mode"])
    except (TypeError, ValueError) as e:
        raise ValueError(f"参数类型不符合规范: {e}")

    # 边界限制校验
    if not (30 <= normalized["pitch_floor"] <= 1000):
        raise ValueError(f"pitch_floor 超出合理区间 (30-1000): {normalized['pitch_floor']}")
    if not (200 <= normalized["pitch_ceiling"] <= 3000):
        raise ValueError(f"pitch_ceiling 超出合理区间 (200-3000): {normalized['pitch_ceiling']}")
    if normalized["pitch_floor"] >= normalized["pitch_ceiling"]:
        raise ValueError(f"pitch_floor 必须小于 pitch_ceiling")
    if not (0.0 <= normalized["voicing_threshold"] <= 1.0):
        raise ValueError(f"voicing_threshold 必须在 0.0 到 1.0 之间: {normalized['voicing_threshold']}")
    if not (1 <= normalized["formant_count"] <= 10):
        raise ValueError(f"formant_count 必须在 1 到 10 之间: {normalized['formant_count']}")
    if not (1000.0 <= normalized["formant_max_hz"] <= 10000.0):
        raise ValueError(f"formant_max_hz 必须在 1000.0 到 10000.0 之间: {normalized['formant_max_hz']}")
    if not (0.005 <= normalized["formant_window_length"] <= 0.2):
        raise ValueError(f"formant_window_length 必须在 0.005 到 0.2 之间: {normalized['formant_window_length']}")
    if normalized["formant_pre_emphasis"] < 0.0:
        raise ValueError(f"formant_pre_emphasis 不能为负数: {normalized['formant_pre_emphasis']}")
    if normalized["formant_sample_strategy"] not in ("整段11点", "中段均值"):
        raise ValueError(f"不合法的采样策略: {normalized['formant_sample_strategy']}")
    if normalized["pts"] <= 0:
        raise ValueError(f"pts 采样点数必须大于 0: {normalized['pts']}")
    if normalized["analysis_mode"] not in ("f0", "formant"):
        raise ValueError(f"不合法的分析模式: {normalized['analysis_mode']}")

    return normalized

def get_speech_bounds(y: np.ndarray, sr: int) -> Tuple[int, int]:
    """计算非静音边界，复用原 PhonRec 后端的 get_speech_bounds 算法"""
    y = np.asarray(y, dtype=np.float32).reshape(-1)
    if len(y) == 0:
        return 0, 0
    frame_size = max(1, int(sr * 0.02))
    frame_count = max(1, int(np.ceil(len(y) / frame_size)))
    rms = np.empty(frame_count, dtype=np.float64)
    for index in range(frame_count):
        frame = y[index * frame_size:(index + 1) * frame_size]
        rms[index] = np.sqrt(np.mean(frame * frame)) if len(frame) else 0.0
    rms_db = 20.0 * np.log10(rms + 1e-10)

    noise_db = float(np.percentile(rms_db, 20)) if len(rms_db) else -100.0
    if len(rms_db) and float(np.percentile(rms_db, 80) - np.percentile(rms_db, 20)) < 3.0 and float(np.median(rms_db)) > -45.0:
        noise_db = min(-55.0, float(np.median(rms_db) - 20.0))
    speech_threshold_db = float(np.clip(noise_db + 10.0, -45.0, -25.0))
    speech_mask = rms_db >= speech_threshold_db

    if not np.any(speech_mask):
        return 0, len(y)

    speech_indices = np.where(speech_mask)[0]
    first_frame = speech_indices[0]
    last_frame = speech_indices[-1]

    start_sample = first_frame * frame_size
    end_sample = min(len(y), (last_frame + 1) * frame_size)

    # 150ms 边际
    margin_samples = int(sr * 0.15)
    start_sample = max(0, start_sample - margin_samples)
    end_sample = min(len(y), end_sample + margin_samples)

    return start_sample, end_sample

def analyze_audio_to_bundle(snd: parselmouth.Sound, params: Dict[str, Any], audio_sha256: str) -> Dict[str, Any]:
    """
    根据已规范化的参数进行声学参数提取，并返回 AnalysisBundle。
    """
    # 提取基频与共振峰
    pitch_data = extract_f0(snd, params)
    formant_data = extract_formants(snd, params)

    # 序列化规范参数的 SHA-256
    params_serialized = json.dumps(params, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    params_sha256 = hashlib.sha256(params_serialized.encode('utf-8')).hexdigest()

    # 音频特征计算
    y = snd.values[0]
    sr = int(snd.sampling_frequency)
    duration = snd.get_total_duration()

    # 计算非静音边界
    start_sample, end_sample = get_speech_bounds(y, sr)
    speech_bounds = {
        "start": float(start_sample / sr),
        "end": float(end_sample / sr)
    }

    # 统计基频摘要
    freqs = pitch_data["freqs"]
    voiced_freqs = freqs[freqs > 0.0]
    voiced_ratio = float(len(voiced_freqs) / len(freqs)) if len(freqs) > 0 else 0.0

    if len(voiced_freqs) > 0:
        f0_median = float(np.median(voiced_freqs))
        f0_min = float(np.min(voiced_freqs))
        f0_max = float(np.max(voiced_freqs))
    else:
        f0_median = 0.0
        f0_min = 0.0
        f0_max = 0.0

    # 统计共振峰摘要 (过滤无效的 NaN 值)
    f1_vals = formant_data["f1"][~np.isnan(formant_data["f1"])]
    f2_vals = formant_data["f2"][~np.isnan(formant_data["f2"])]
    f3_vals = formant_data["f3"][~np.isnan(formant_data["f3"])]

    f1_median = float(np.median(f1_vals)) if len(f1_vals) > 0 else 0.0
    f2_median = float(np.median(f2_vals)) if len(f2_vals) > 0 else 0.0
    f3_median = float(np.median(f3_vals)) if len(f3_vals) > 0 else 0.0

    warnings = []
    if voiced_ratio < 0.1:
        warnings.append("voicing_ratio_too_low")
    if len(f1_vals) < 5 or len(f2_vals) < 5:
        warnings.append("too_few_valid_formant_points")

    bundle = {
        "schema": "phontracer.acoustic-analysis.v1",
        "analysis_id": str(uuid.uuid4()),
        "algorithm_version": "audio-core-v1",
        "audio_sha256": audio_sha256,
        "params_sha256": params_sha256,
        "params": params,
        "duration_seconds": float(duration),
        "speech_bounds": speech_bounds,
        "pitch": {
            "xs": pitch_data["xs"],
            "freqs": pitch_data["freqs"],
            "engine": pitch_data["engine"]
        },
        "formants": {
            "xs": formant_data["xs"],
            "f1": formant_data["f1"],
            "f2": formant_data["f2"],
            "f3": formant_data["f3"],
            "engine": formant_data["engine"]
        },
        "summary": {
            "voiced_ratio": voiced_ratio,
            "f0_median_hz": f0_median,
            "f0_min_hz": f0_min,
            "f0_max_hz": f0_max,
            "f1_median_hz": f1_median,
            "f2_median_hz": f2_median,
            "f3_median_hz": f3_median,
            "warnings": warnings
        }
    }

    return bundle
