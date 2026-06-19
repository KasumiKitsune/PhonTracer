import sys
import argparse
import base64
import csv
import io
import json
import os
import secrets
import shutil
import socket
import tempfile
import uuid
import zipfile
from pathlib import Path
from typing import List, Dict, Any, Optional
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
import numpy as np
from scipy.io import wavfile
import scipy.signal as signal

# Make sure parent directory of PhonRec/backend and workspace root are in sys.path
backend_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(backend_dir)
workspace_root = os.path.dirname(parent_dir)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)
if workspace_root not in sys.path:
    sys.path.insert(0, workspace_root)

from modules.project_adaptor import (
    safe_extract_zip,
    adapt_project_state,
    prune_unreferenced_resources,
    repair_wav_header,
)

ENGINE_VERSION = "1.3.0"
PROTOCOL_VERSION = 1
SESSION_TOKEN = os.environ.get("PHONTRACER_SESSION_TOKEN", "")

app = FastAPI(title="PhonTracer Analysis Engine", version=ENGINE_VERSION)

# Enable CORS for frontend integration
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "tauri://localhost",
        "http://tauri.localhost",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WORKSPACE_DIR = os.path.join(BASE_DIR, "workspace")
AUDIO_DIR = os.path.join(WORKSPACE_DIR, "audio")
DATA_DIR = os.path.join(WORKSPACE_DIR, "data")

WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


def sanitize_path_component(value: Any, fallback: str, max_length: int = 96) -> str:
    """生成可在 Windows、macOS 和 Linux 上安全使用的单个路径名称。"""
    text = str(value or "").strip()
    sanitized = "".join(
        "_" if ord(char) < 32 or char in '<>:"/\\|?*' else char
        for char in text
    ).rstrip(" .")
    sanitized = sanitized[:max_length].rstrip(" .")
    if not sanitized:
        sanitized = fallback
    if sanitized.split(".", 1)[0].upper() in WINDOWS_RESERVED_NAMES:
        sanitized = f"_{sanitized}"
    return sanitized


def configure_workspace(workspace_dir: str) -> str:
    """配置独立于安装目录的 PhonRec 工作区。"""
    global WORKSPACE_DIR, AUDIO_DIR, DATA_DIR
    resolved = os.path.abspath(os.path.expanduser(workspace_dir))
    WORKSPACE_DIR = resolved
    AUDIO_DIR = os.path.join(resolved, "audio")
    DATA_DIR = os.path.join(resolved, "data")
    init_workspace()
    return resolved


def configure_session_token(token: str) -> None:
    """设置本次前端会话使用的鉴权令牌。"""
    global SESSION_TOKEN
    SESSION_TOKEN = token

def init_workspace():
    os.makedirs(WORKSPACE_DIR, exist_ok=True)
    os.makedirs(AUDIO_DIR, exist_ok=True)
    os.makedirs(DATA_DIR, exist_ok=True)


def clear_workspace():
    if os.path.exists(WORKSPACE_DIR):
        shutil.rmtree(WORKSPACE_DIR)
    init_workspace()


@app.middleware("http")
async def require_session_token(request, call_next):
    """除健康检查和预检请求外，拒绝未携带会话令牌的访问。"""
    if request.url.path == "/api/health" or request.method == "OPTIONS":
        return await call_next(request)

    authorization = request.headers.get("authorization", "")
    expected = f"Bearer {SESSION_TOKEN}" if SESSION_TOKEN else ""
    if not expected or not secrets.compare_digest(authorization, expected):
        return JSONResponse(status_code=401, content={"detail": "无效或缺失的会话令牌"})
    return await call_next(request)


@app.get("/api/health")
async def api_health():
    """返回供 PhonRec 启动门禁校验的稳定协议信息。"""
    return {
        "status": "ok",
        "engine_version": ENGINE_VERSION,
        "protocol_version": PROTOCOL_VERSION,
        "capabilities": [
            "project-state",
            "project-import-export",
            "wordlist-import",
            "audio-storage",
            "audio-quality",
            "spectrogram",
            "pitch",
            "formants",
        ],
    }

# --- DSP / Quality Check Helper Functions ---

def check_clipping(y: np.ndarray, threshold: float = 0.99) -> tuple[bool, float]:
    """Check for audio clipping based on samples reaching the dynamic range limit."""
    abs_y = np.abs(y)
    clipped_samples = np.sum(abs_y >= threshold)
    fraction = float(clipped_samples / len(y)) if len(y) > 0 else 0.0
    # Flag if more than 0.01% of samples are clipped
    return fraction > 0.0001, fraction

def check_volume(y: np.ndarray, sr: int) -> tuple[str, float]:
    """Check if average volume is too quiet, too loud, or normal."""
    chunk_size = int(0.02 * sr)  # 20ms chunks
    rms_list = []
    for i in range(0, len(y), chunk_size):
        chunk = y[i:i+chunk_size]
        if len(chunk) > 0:
            rms = np.sqrt(np.mean(chunk**2))
            rms_list.append(rms)

    rms_list = np.array(rms_list)
    with np.errstate(divide='ignore'):
        rms_db = 20 * np.log10(rms_list + 1e-10)

    # Analyze speech chunks (energy above noise floor of -45 dB)
    speech_rms = rms_list[rms_db > -45]
    if len(speech_rms) == 0:
        return "too_quiet", -100.0

    avg_rms_db = float(20 * np.log10(np.mean(speech_rms) + 1e-10))

    if avg_rms_db < -35:
        return "too_quiet", avg_rms_db
    elif avg_rms_db > -3:
        return "too_loud", avg_rms_db
    return "normal", avg_rms_db

def detect_creak(y: np.ndarray, sr: int) -> tuple[bool, float]:
    """
    Lightweight creaky voice (vocal fry) detector using short-term autocorrelation.
    Detects low pitch (45Hz - 85Hz) and irregularity.
    """
    # DC offset removal
    y = y - np.mean(y)

    chunk_size = int(0.03 * sr)  # 30ms window
    step_size = int(0.01 * sr)   # 10ms step

    voiced_count = 0
    creak_frames = 0

    # Autocorrelation search ranges for pitch (50Hz to 400Hz)
    min_lag = int(sr / 400)
    max_lag = int(sr / 50)

    for i in range(0, len(y) - chunk_size, step_size):
        chunk = y[i:i+chunk_size]
        rms = np.sqrt(np.mean(chunk**2))
        if rms < 0.01:  # Ignore silent segments
            continue

        voiced_count += 1
        r = np.correlate(chunk, chunk, mode='full')
        center = len(chunk) - 1
        r = r[center:]

        if len(r) <= max_lag:
            continue

        lag_range = r[min_lag:max_lag]
        if len(lag_range) == 0:
            continue

        peak_lag = int(np.argmax(lag_range) + min_lag)
        peak_strength = float(r[peak_lag] / r[0]) if r[0] > 0 else 0.0

        if peak_strength > 0.35:  # Pitch found
            pitch = sr / peak_lag
            # Creaky voice region
            if 45 <= pitch <= 85:
                creak_frames += 1

    if voiced_count == 0:
        return False, 0.0

    creak_ratio = float(creak_frames / voiced_count)
    # Flag as creaky voice if > 15% of speech frames fall into vocal fry range
    return creak_ratio > 0.15, creak_ratio

def normalize_audio_samples(samples: np.ndarray) -> np.ndarray:
    """将各种 WAV 位深和声道布局统一为单声道 float32。"""
    if samples.dtype == np.int16:
        y = samples.astype(np.float32) / 32768.0
    elif samples.dtype == np.int32:
        y = samples.astype(np.float32) / 2147483648.0
    elif samples.dtype == np.uint8:
        y = (samples.astype(np.float32) - 128.0) / 128.0
    elif np.issubdtype(samples.dtype, np.integer):
        scale = max(abs(np.iinfo(samples.dtype).min), np.iinfo(samples.dtype).max)
        y = samples.astype(np.float32) / float(scale)
    else:
        y = samples.astype(np.float32)
    if y.ndim > 1:
        y = np.mean(y, axis=1, dtype=np.float32)
    return np.nan_to_num(y, nan=0.0, posinf=1.0, neginf=-1.0)

QUALITY_RULE_NAMES = ("speech", "volume", "clipping", "noise", "creak", "dc_offset")


def normalize_quality_config(config: Optional[dict] = None) -> dict:
    """兼容缺失或不完整的前端配置，并限制检测档位。"""
    source = config if isinstance(config, dict) else {}
    normalized = {}
    for name in QUALITY_RULE_NAMES:
        raw = source.get(name, {})
        enabled = raw.get("enabled", True) if isinstance(raw, dict) else bool(raw)
        level = raw.get("level", "medium") if isinstance(raw, dict) else "medium"
        normalized[name] = {
            "enabled": bool(enabled),
            "level": level if level in {"low", "medium", "high"} else "medium",
        }
    return normalized


def parse_quality_config(raw: str) -> dict:
    if not raw:
        return normalize_quality_config()
    try:
        parsed = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail="质量检测配置不是有效 JSON") from exc
    return normalize_quality_config(parsed)


def analyze_recording_quality(y: np.ndarray, sr: int, config: Optional[dict] = None) -> dict:
    """基于有效语音而非整段平均值进行分级，区分重录与人工复核。"""
    y = np.asarray(y, dtype=np.float32).reshape(-1)
    duration = len(y) / sr if sr > 0 else 0.0
    frame_size = max(1, int(sr * 0.02))
    frame_count = max(1, int(np.ceil(len(y) / frame_size)))
    rms = np.empty(frame_count, dtype=np.float64)
    for index in range(frame_count):
        frame = y[index * frame_size:(index + 1) * frame_size]
        rms[index] = np.sqrt(np.mean(frame * frame)) if len(frame) else 0.0
    rms_db = 20.0 * np.log10(rms + 1e-10)

    noise_db = float(np.percentile(rms_db, 20)) if len(rms_db) else -100.0
    # 没有前后静音的紧凑录音不能把整段语音误当作噪声底。
    if len(rms_db) and float(np.percentile(rms_db, 80) - np.percentile(rms_db, 20)) < 3.0 and float(np.median(rms_db)) > -45.0:
        noise_db = min(-55.0, float(np.median(rms_db) - 20.0))
    speech_threshold_db = float(np.clip(noise_db + 10.0, -45.0, -25.0))
    speech_mask = rms_db >= speech_threshold_db
    speech_ratio = float(np.mean(speech_mask)) if len(speech_mask) else 0.0
    speech_ms = int(np.sum(speech_mask) * 20)
    active_rms_db = float(20.0 * np.log10(np.mean(rms[speech_mask]) + 1e-10)) if np.any(speech_mask) else -100.0
    snr_db = float(active_rms_db - noise_db) if np.any(speech_mask) else 0.0
    peak = float(np.max(np.abs(y))) if len(y) else 0.0
    peak_db = float(20.0 * np.log10(peak + 1e-10))
    dc_offset = float(abs(np.mean(y))) if len(y) else 0.0
    _, clip_ratio = check_clipping(y)

    rules = normalize_quality_config(config)
    speech_thresholds = {
        "low": (100, 0.04), "medium": (180, 0.08), "high": (300, 0.15),
    }[rules["speech"]["level"]]
    volume_thresholds = {
        "low": (-40.0, -3.0), "medium": (-35.0, -6.0), "high": (-30.0, -9.0),
    }[rules["volume"]["level"]]
    clipping_thresholds = {
        "low": (0.003, 0.01), "medium": (0.0001, 0.003), "high": (0.0, 0.001),
    }[rules["clipping"]["level"]]
    noise_thresholds = {
        "low": (3.0, 8.0), "medium": (6.0, 12.0), "high": (10.0, 16.0),
    }[rules["noise"]["level"]]
    creak_threshold = {"low": 0.25, "medium": 0.15, "high": 0.08}[rules["creak"]["level"]]
    dc_threshold = {"low": 0.05, "medium": 0.03, "high": 0.015}[rules["dc_offset"]["level"]]

    clip_review_threshold, clip_retry_threshold = clipping_thresholds
    clipped = clip_ratio > clip_review_threshold
    severe_clipping = clip_ratio >= clip_retry_threshold
    no_speech = speech_ms < speech_thresholds[0] or speech_ratio < speech_thresholds[1]
    too_short = duration < 0.30
    too_quiet = active_rms_db < volume_thresholds[0]
    too_loud = active_rms_db > volume_thresholds[1] or peak_db > -0.15
    very_noisy = snr_db < noise_thresholds[0] and not no_speech
    moderate_noise = noise_thresholds[0] <= snr_db < noise_thresholds[1]
    _, creak_ratio = detect_creak(y, sr) if len(y) >= frame_size else (False, 0.0)
    creaky = creak_ratio > creak_threshold
    dc_abnormal = dc_offset > dc_threshold

    retry_issues = []
    review_issues = []
    if rules["speech"]["enabled"] and too_short:
        retry_issues.append("录音过短")
    if rules["speech"]["enabled"] and no_speech:
        retry_issues.append("未检测到足够语音")
    if rules["clipping"]["enabled"] and severe_clipping:
        retry_issues.append("严重截断")
    elif rules["clipping"]["enabled"] and clipped:
        review_issues.append("轻微截断")
    if rules["volume"]["enabled"] and too_quiet:
        retry_issues.append("有效语音音量过小")
    if rules["volume"]["enabled"] and too_loud:
        retry_issues.append("有效语音音量过大")
    if rules["noise"]["enabled"] and very_noisy:
        retry_issues.append("信噪比过低")
    elif rules["noise"]["enabled"] and moderate_noise:
        review_issues.append("背景噪声偏高")
    if rules["dc_offset"]["enabled"] and dc_abnormal:
        review_issues.append("直流偏移偏高")
    if rules["creak"]["enabled"] and creaky:
        review_issues.append("可能存在嘎裂声")

    decision = "retry" if retry_issues else ("review" if review_issues else "accept")
    score = 100
    score -= 45 if retry_issues else 0
    score -= min(30, 8 * len(review_issues))
    score = max(0, score)
    vol_status = "too_quiet" if too_quiet else ("too_loud" if too_loud else "normal")
    labels = retry_issues + review_issues
    return {
        "decision": decision,
        "grade": "需重录" if decision == "retry" else ("建议复核" if decision == "review" else "良好"),
        "score": score,
        "issues": labels,
        "recommendations": labels,
        "config": rules,
        "clipping": {"enabled": rules["clipping"]["enabled"], "abnormal": rules["clipping"]["enabled"] and clipped, "score": clip_ratio, "label": "未启用" if not rules["clipping"]["enabled"] else ("音频截断" if clipped else "正常")},
        "volume": {
            "enabled": rules["volume"]["enabled"],
            "status": vol_status if rules["volume"]["enabled"] else "disabled",
            "score": active_rms_db,
            "label": "未启用" if not rules["volume"]["enabled"] else ("音量过小" if too_quiet else ("音量过大" if too_loud else "正常")),
        },
        # 嘎裂声只作为复核提示，不再自动判定录音失败。
        "creak": {"enabled": rules["creak"]["enabled"], "abnormal": rules["creak"]["enabled"] and creaky, "score": creak_ratio, "label": "未启用" if not rules["creak"]["enabled"] else ("可能有嘎裂声" if creaky else "正常")},
        "speech": {"enabled": rules["speech"]["enabled"], "abnormal": rules["speech"]["enabled"] and no_speech, "score": speech_ratio, "label": "未启用" if not rules["speech"]["enabled"] else ("语音不足" if no_speech else "正常")},
        "noise": {"enabled": rules["noise"]["enabled"], "abnormal": rules["noise"]["enabled"] and very_noisy, "score": snr_db, "label": "未启用" if not rules["noise"]["enabled"] else ("噪声过高" if very_noisy else "正常")},
        "dc_offset": {"enabled": rules["dc_offset"]["enabled"], "abnormal": rules["dc_offset"]["enabled"] and dc_abnormal, "score": dc_offset, "label": "未启用" if not rules["dc_offset"]["enabled"] else ("偏移过高" if dc_abnormal else "正常")},
        "metrics": {
            "duration_ms": int(duration * 1000), "speech_ms": speech_ms, "speech_ratio": speech_ratio,
            "noise_floor_dbfs": noise_db, "speech_threshold_dbfs": speech_threshold_db,
            "active_rms_dbfs": active_rms_db, "peak_dbfs": peak_db, "snr_db": snr_db,
            "clipping_ratio": clip_ratio, "dc_offset": dc_offset,
        },
    }

def generate_spectrogram(y: np.ndarray, sr: int) -> str:
    """Generate a clean colormapped spectrogram image base64 string with F0/F1/F2 curves."""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import parselmouth

    # Compute STFT spectrogram
    nperseg = 512
    noverlap = 384
    f, t, Sxx = signal.spectrogram(y, sr, nperseg=nperseg, noverlap=noverlap)

    # Convert power to dB
    Sxx_db = 10 * np.log10(Sxx + 1e-10)

    # Size in inches (900x450 pixels at 150 DPI) to achieve 2.0 aspect ratio
    # Using white background for the spectrogram card to match Kasumi Light Theme
    fig = plt.figure(figsize=(6, 3), dpi=150, facecolor='#ffffff')
    ax = fig.add_axes([0, 0, 1, 1])
    ax.axis('off')

    # Plot spectrogram with gray_r colormap (white background for silence)
    ax.pcolormesh(t, f, Sxx_db, shading='gouraud', cmap='gray_r')

    # Draw F0 and formants using parselmouth
    try:
        sound = parselmouth.Sound(y, sampling_frequency=sr)

        # 1. Pitch (F0) -> Blue curve
        pitch = sound.to_pitch()
        pitch_values = pitch.selected_array['frequency']
        pitch_ts = pitch.xs()
        f0_plot = pitch_values.copy()
        f0_plot[f0_plot == 0] = np.nan

        # 2. Formants (F1, F2) -> Red and Green dashed curves
        formants = sound.to_formant_burg(time_step=0.005, max_number_of_formants=5)
        formant_ts = formants.xs()
        f1_vals, f2_vals = [], []
        for time_pt in formant_ts:
            f1 = formants.get_value_at_time(1, time_pt)
            f2 = formants.get_value_at_time(2, time_pt)
            f1_vals.append(f1 if not np.isnan(f1) else 0.0)
            f2_vals.append(f2 if not np.isnan(f2) else 0.0)

        f1_plot = np.array(f1_vals)
        f1_plot[f1_plot == 0.0] = np.nan
        f2_plot = np.array(f2_vals)
        f2_plot[f2_plot == 0.0] = np.nan

        # Dynamically determine y-limit for the spectrogram based on F2 values
        visible_f2 = f2_plot[~np.isnan(f2_plot)] if len(f2_plot) else np.array([])
        if len(visible_f2) > 0:
            max_f2 = float(np.max(visible_f2))
            spec_max = max(3000.0, max_f2 + 500.0)
        else:
            spec_max = 3500.0
        spec_max = min(spec_max, sr / 2.0)

        # Plot Formants on the main axes
        ax.plot(formant_ts, f1_plot, color='#ef4444', linewidth=2.5, linestyle='--')
        ax.plot(formant_ts, f2_plot, color='#10b981', linewidth=2.5, linestyle='--')

        # Create a twin y-axis for F0 to show it with custom limits
        ax2 = ax.twinx()
        ax2.axis('off')

        visible_f0 = f0_plot[~np.isnan(f0_plot)] if len(f0_plot) else np.array([])
        if len(visible_f0) > 0:
            min_f0 = float(np.min(visible_f0))
            max_f0 = float(np.max(visible_f0))
            y_min = max(0.0, min_f0 - 30.0)
            y_max = max_f0 + 30.0
            y_max = max(y_max, y_min + 100.0)
        else:
            y_min = 50.0
            y_max = 500.0

        # Plot F0 on the twin axes
        ax2.plot(pitch_ts, f0_plot, color='#3b82f6', linewidth=3.0, solid_capstyle='round')

        # Strictly set limits after all plots to override autoscale
        ax.set_ylim(0, spec_max)
        ax.set_xlim(0, len(y) / sr)
        ax2.set_ylim(y_min, y_max)
        ax2.set_xlim(0, len(y) / sr)
    except Exception as e:
        # Graceful fallback: print error and return base spectrogram
        print(f"[generate_spectrogram] F0/Formant analysis failed: {e}")
        ax.set_ylim(0, min(4000, sr / 2))

    buf = io.BytesIO()
    plt.savefig(buf, format='png', facecolor='#ffffff')
    plt.close(fig)
    buf.seek(0)

    return base64.b64encode(buf.read()).decode('utf-8')

# --- API Endpoints ---

@app.post("/api/project/clear")
async def api_clear_project():
    """Clear active workspace."""
    try:
        clear_workspace()
        return {"status": "success", "message": "Workspace cleared"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/project/state")
async def api_get_project_state():
    """Load project.json state from workspace."""
    path = os.path.join(WORKSPACE_DIR, "project.json")
    if not os.path.exists(path):
        return {"version": "1.0", "speakers": {}}
    try:
        with open(path, "rb") as f:
            raw_bytes = f.read()
        try:
            state = json.loads(raw_bytes.decode("utf-8"))
        except UnicodeDecodeError:
            state = json.loads(raw_bytes.decode("gb18030"))
        return state
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read project state: {e}")

@app.post("/api/project/state")
async def api_save_project_state(state: Dict[str, Any]):
    """Save project.json state to workspace."""
    init_workspace()
    try:
        state, warnings, summary = adapt_project_state(state, WORKSPACE_DIR)
        path = os.path.join(WORKSPACE_DIR, "project.json")
        tmp_path = path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)
        prune_unreferenced_resources(state, WORKSPACE_DIR)
        return {"status": "success", "state": state, "warnings": warnings, "summary": summary}
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Failed to save project state: {e}")

@app.post("/api/audio/save")
async def api_save_audio(
    file: UploadFile = File(...),
    speaker_id: str = Form(...),
    word_id: str = Form(...),
    source: str = Form("系统默认麦克风"),
    quality_config: str = Form("")
):
    """Save recorded audio blob and automatically analyze it in a single step."""
    init_workspace()
    resolved_quality_config = parse_quality_config(quality_config)
    safe_speaker_id = sanitize_path_component(speaker_id, "speaker", 64)
    safe_word_id = sanitize_path_component(word_id, "item", 64)
    # Ensure speaker audio directory exists
    speaker_dir = os.path.join(AUDIO_DIR, safe_speaker_id)
    os.makedirs(speaker_dir, exist_ok=True)

    # Save file
    filename = f"{safe_speaker_id}_{safe_word_id}.wav"
    file_path = os.path.join(speaker_dir, filename)

    try:
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        # 兼容旧版录音端写错的 WAV 平均字节率，避免错误继续进入工程包。
        repair_wav_header(file_path)

        rel_path = f"audio/{safe_speaker_id}/{filename}"

        # Run analysis immediately to return quality and spectrogram
        sr, y_int = wavfile.read(file_path)
        y = normalize_audio_samples(y_int)

        spec_b64 = generate_spectrogram(y, sr)

        quality = analyze_recording_quality(y, sr, resolved_quality_config)

        import datetime
        recorded_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
        duration_ms = int((len(y_int) / sr) * 1000) if sr > 0 else 0

        return {
            "status": "success",
            "path": rel_path,
            "quality": quality,
            "spectrogram": f"data:image/png;base64,{spec_b64}",
            "recorded_at": recorded_at,
            "duration_ms": duration_ms,
            "sample_rate_hz": int(sr),
            "channels": int(y_int.shape[1]) if y_int.ndim > 1 else 1,
            "format": "wav",
            "source": source
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
        except OSError:
            pass
        raise HTTPException(status_code=500, detail=f"Failed to save and analyze audio file: {e}")

@app.get("/api/audio/file")
async def api_get_audio_file(speaker_id: str, word_id: str):
    """Retrieve the WAV file for playback or decoding."""
    safe_speaker_id = sanitize_path_component(speaker_id, "speaker", 64)
    safe_word_id = sanitize_path_component(word_id, "item", 64)
    filename = f"{safe_speaker_id}_{safe_word_id}.wav"
    file_path = os.path.join(AUDIO_DIR, safe_speaker_id, filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Audio file not found")
    return FileResponse(file_path, media_type="audio/wav")

@app.post("/api/audio/analyze")
async def api_analyze_audio(
    speaker_id: str = Form(...),
    word_id: str = Form(...),
    quality_config: str = Form("")
):
    """Analyze a WAV audio file for quality parameters and generate spectrogram."""
    safe_speaker_id = sanitize_path_component(speaker_id, "speaker", 64)
    safe_word_id = sanitize_path_component(word_id, "item", 64)
    filename = f"{safe_speaker_id}_{safe_word_id}.wav"
    file_path = os.path.join(AUDIO_DIR, safe_speaker_id, filename)

    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Audio file not found")
    resolved_quality_config = parse_quality_config(quality_config)

    try:
        # Read WAV file
        repair_wav_header(file_path)
        sr, y_int = wavfile.read(file_path)

        y = normalize_audio_samples(y_int)

        # Spectrogram
        spec_b64 = generate_spectrogram(y, sr)

        return {
            "status": "success",
            "quality": analyze_recording_quality(y, sr, resolved_quality_config),
            "spectrogram": f"data:image/png;base64,{spec_b64}"
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Audio analysis failed: {e}")

@app.post("/api/wordlist/import")
async def api_import_wordlist(file: UploadFile = File(...)):
    """Parse uploaded word list file (.ptwl, .txt, .csv) into standardized group structure."""
    filename = file.filename.lower()
    content_bytes = await file.read()

    groups = []

    try:
        if filename.endswith(".ptwl"):
            # Advanced JSON Wordlist
            data = json.loads(content_bytes.decode("utf-8"))
            if data.get("schema") != "phontracer.wordlist.v2":
                # Fallback to general JSON structure or continue parsing groups
                pass

            raw_groups = data.get("groups", [])
            for rg in raw_groups:
                items = []
                for item in rg.get("items", []):
                    items.append({
                        "id": item.get("id") or str(uuid.uuid4())[:8],
                        "label": item.get("label"),
                        "note": item.get("note", ""),
                        "tags": item.get("tags", []),
                        "aliases": item.get("aliases", []),
                        "meta": item.get("meta", {}),
                        "metadata_source": item.get("metadata_source", "导入字表")
                    })
                if items:
                    groups.append({
                        "id": rg.get("id") or str(uuid.uuid4())[:8],
                        "name": rg.get("name", "未命名组"),
                        "note": rg.get("note", ""),
                        "tags": rg.get("tags", []),
                        "items": items
                    })

        elif filename.endswith(".csv"):
            # CSV Wordlist
            content_str = content_bytes.decode("utf-8-sig") # Handles BOM
            reader = csv.reader(io.StringIO(content_str))
            headers = next(reader, None)

            # Map columns by name
            # Columns: 组名, 组备注, 组标签, 词项, 词项备注, 标签, 别名...
            col_map = {}
            if headers:
                for idx, h in enumerate(headers):
                    col_map[h.strip()] = idx

            # Helper to safely retrieve value by column header
            def get_val(row, name, default=""):
                idx = col_map.get(name)
                if idx is not None and idx < len(row):
                    return row[idx].strip()
                return default

            group_dict = {}
            for row in reader:
                if not row or len(row) == 0:
                    continue
                group_name = get_val(row, "组名", "默认组")
                group_note = get_val(row, "组备注", "")
                group_tags = [t for t in get_val(row, "组标签", "").split("；") if t]
                if not group_tags:
                    group_tags = [t for t in get_val(row, "组标签", "").split(";") if t]

                item_label = get_val(row, "词项") or row[0] # Fallback to first col
                if not item_label:
                    continue

                item_note = get_val(row, "词项备注", "")
                item_tags = [t for t in get_val(row, "标签", "").split("；") if t]
                if not item_tags:
                    item_tags = [t for t in get_val(row, "标签", "").split(";") if t]
                aliases_str = get_val(row, "别名", "")
                aliases = [aliases_str] if aliases_str else []

                # Gather extra meta fields
                meta = {}
                for h, idx in col_map.items():
                    if h not in ("组名", "组备注", "组标签", "词项", "词项备注", "标签", "别名"):
                        meta[h] = row[idx].strip() if idx < len(row) else ""

                item = {
                    "id": str(uuid.uuid4())[:8],
                    "label": item_label,
                    "note": item_note,
                    "tags": item_tags,
                    "aliases": aliases,
                    "meta": meta,
                    "metadata_source": "导入CSV"
                }

                if group_name not in group_dict:
                    group_dict[group_name] = {
                        "id": str(uuid.uuid4())[:8],
                        "name": group_name,
                        "note": group_note,
                        "tags": group_tags,
                        "items": []
                    }
                group_dict[group_name]["items"].append(item)

            groups = list(group_dict.values())

        else:
            # Plain Text (.txt) Wordlist
            content_str = content_bytes.decode("utf-8")
            lines = content_str.splitlines()
            current_group = None

            for line in lines:
                line = line.strip()
                if not line:
                    continue

                # Check for group headers like 【组名】 or [组名]
                if (line.startswith("【") and line.endswith("】")) or (line.startswith("[") and line.endswith("]")):
                    group_name = line[1:-1].strip()
                    current_group = {
                        "id": str(uuid.uuid4())[:8],
                        "name": group_name,
                        "note": "",
                        "tags": [],
                        "items": []
                    }
                    groups.append(current_group)
                else:
                    # Split words by space or tab
                    words = line.split()
                    if not current_group:
                        current_group = {
                            "id": str(uuid.uuid4())[:8],
                            "name": "默认组",
                            "note": "",
                            "tags": [],
                            "items": []
                        }
                        groups.append(current_group)

                    for w in words:
                        current_group["items"].append({
                            "id": str(uuid.uuid4())[:8],
                            "label": w,
                            "note": "",
                            "tags": [],
                            "aliases": [],
                            "meta": {},
                            "metadata_source": "导入TXT"
                        })

        return {"status": "success", "groups": groups}
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Failed to parse wordlist: {e}")

def normalize_workspace_after_import(state: dict) -> dict:
    """标准化解压后的工作区，重建缺失的字表并标准化音频位置。"""
    state, warnings, summary = adapt_project_state(state, WORKSPACE_DIR)

    project_json_path = os.path.join(WORKSPACE_DIR, "project.json")
    tmp_path = project_json_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, project_json_path)

    prune_unreferenced_resources(state, WORKSPACE_DIR)

    state["_warnings"] = warnings
    state["_summary"] = summary
    return state

@app.post("/api/project/import")
async def api_import_project(file: UploadFile = File(...)):
    """安全校验并导入 .teproj，失败时保留当前工作区。"""
    workspace_parent = os.path.dirname(WORKSPACE_DIR)
    os.makedirs(workspace_parent, exist_ok=True)
    temp_zip_handle = tempfile.NamedTemporaryFile(
        prefix="phonrec_import_", suffix=".zip", dir=workspace_parent, delete=False
    )
    temp_zip = temp_zip_handle.name
    temp_zip_handle.close()
    staging_dir = tempfile.mkdtemp(prefix="phonrec_staging_", dir=workspace_parent)
    try:
        with open(temp_zip, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        with zipfile.ZipFile(temp_zip, "r") as zip_ref:
            safe_extract_zip(zip_ref, staging_dir)

        project_json_path = os.path.join(staging_dir, "project.json")
        if not os.path.exists(project_json_path):
            raise HTTPException(status_code=400, detail="工程无效：缺少 project.json")

        with open(project_json_path, "rb") as f:
            raw_bytes = f.read()
        try:
            state = json.loads(raw_bytes.decode("utf-8"))
        except UnicodeDecodeError:
            state = json.loads(raw_bytes.decode("gb18030"))

        # 备份当前工作区以实现安全回滚
        backup_dir = os.path.join(workspace_parent, "workspace_backup")
        if os.path.exists(backup_dir):
            shutil.rmtree(backup_dir)
        if os.path.exists(WORKSPACE_DIR):
            shutil.copytree(WORKSPACE_DIR, backup_dir)

        try:
            clear_workspace()
            shutil.copytree(staging_dir, WORKSPACE_DIR, dirs_exist_ok=True)

            # 运行工作区标准化
            state = normalize_workspace_after_import(state)
            warnings = state.pop("_warnings", [])
            summary = state.pop("_summary", {})

            # 清理备份
            if os.path.exists(backup_dir):
                shutil.rmtree(backup_dir)

            return {"status": "success", "state": state, "warnings": warnings, "summary": summary}
        except Exception as e:
            # 回滚当前工作区
            clear_workspace()
            if os.path.exists(backup_dir):
                shutil.copytree(backup_dir, WORKSPACE_DIR, dirs_exist_ok=True)
                shutil.rmtree(backup_dir)
            raise e

    except HTTPException:
        raise
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"导入工程失败：{e}")
    finally:
        for path in (temp_zip, staging_dir):
            try:
                if os.path.isdir(path):
                    shutil.rmtree(path)
                elif os.path.exists(path):
                    os.remove(path)
            except OSError:
                pass

@app.get("/api/project/export")
async def api_export_project():
    """Package the active workspace into a .teproj ZIP file."""
    project_json_path = os.path.join(WORKSPACE_DIR, "project.json")
    if not os.path.exists(project_json_path):
        raise HTTPException(status_code=400, detail="No active project state to export")

    try:
        with open(project_json_path, "r", encoding="utf-8") as f:
            state = json.load(f)
        if not state.get("speakers"):
            raise HTTPException(status_code=400, detail="未添加发音人，禁止导出兼容的工程")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取工程状态失败: {e}")

    workspace_parent = os.path.dirname(WORKSPACE_DIR)
    os.makedirs(workspace_parent, exist_ok=True)
    temp_export = os.path.join(workspace_parent, f"export_{uuid.uuid4().hex}.teproj")
    try:
        # Create ZIP
        with zipfile.ZipFile(temp_export, "w", zipfile.ZIP_DEFLATED) as zip_file:
            # Walk workspace directory and add files to zip
            for root, dirs, files in os.walk(WORKSPACE_DIR):
                for file in files:
                    file_path = os.path.join(root, file)
                    rel_path = os.path.relpath(file_path, WORKSPACE_DIR).replace(os.sep, "/")
                    # Do not package temporary files
                    if file.endswith(".tmp") or file.endswith(".temp"):
                        continue
                    zip_file.write(file_path, rel_path)

        # Return ZIP as stream and delete it after sending
        def iterfile():
            with open(temp_export, "rb") as f:
                yield from f
            try:
                os.remove(temp_export)
            except:
                pass

        headers = {
            "Content-Disposition": "attachment; filename=PhonRec_Project.teproj"
        }
        return StreamingResponse(iterfile(), media_type="application/zip", headers=headers)
    except Exception as e:
        if os.path.exists(temp_export):
            try:
                os.remove(temp_export)
            except:
                pass
        raise HTTPException(status_code=500, detail=f"Failed to export project: {e}")

def is_target_folder_valid(path_str: str) -> bool:
    path = Path(path_str)
    if not path.exists():
        return True
    if not path.is_dir():
        return False
    if not os.listdir(path_str):
        return True
    marker_file = path / ".phonrec-project.json"
    if marker_file.exists():
        try:
            with open(marker_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                if data.get("marker") == "phonrec.folder-project.v1":
                    return True
        except:
            pass
    return False

@app.post("/api/project/export_folder")
async def api_export_project_folder(payload: Dict[str, Any]):
    folder_path = payload.get("folder_path")
    if not folder_path:
        raise HTTPException(status_code=400, detail="缺少文件夹路径")

    if not is_target_folder_valid(folder_path):
        raise HTTPException(status_code=400, detail="目标文件夹非空且不是有效的 PhonRec 工程目录")

    project_json_path = os.path.join(WORKSPACE_DIR, "project.json")
    if not os.path.exists(project_json_path):
        raise HTTPException(status_code=400, detail="当前工作区无工程数据，无法导出")

    try:
        with open(project_json_path, "r", encoding="utf-8") as f:
            state = json.load(f)
        if not state.get("speakers"):
            raise HTTPException(status_code=400, detail="未添加发音人，禁止导出兼容的工程")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取工作区 project.json 失败: {e}")

    target_path = Path(folder_path).resolve()
    parent_dir = target_path.parent
    os.makedirs(parent_dir, exist_ok=True)
    temp_dir = parent_dir / f"{target_path.name}_tmp_{uuid.uuid4().hex}"

    try:
        os.makedirs(temp_dir, exist_ok=True)
        audio_dir = temp_dir / "audio"
        wordlist_dir = temp_dir / "wordlist"
        logs_dir = temp_dir / "logs"
        os.makedirs(audio_dir, exist_ok=True)
        os.makedirs(wordlist_dir, exist_ok=True)
        os.makedirs(logs_dir, exist_ok=True)

        with open(temp_dir / ".phonrec-project.json", "w", encoding="utf-8") as f:
            json.dump({"marker": "phonrec.folder-project.v1", "version": 1}, f, ensure_ascii=False, indent=2)

        groups = state.get("groups", [])
        wordlist_ptwl = {
            "schema": "phontracer.wordlist.v2",
            "groups": groups
        }
        with open(wordlist_dir / "wordlist.ptwl", "w", encoding="utf-8") as f:
            json.dump(wordlist_ptwl, f, ensure_ascii=False, indent=2)

        item_index_map = {}
        idx = 1
        for g in groups:
            for item in g.get("items", []):
                item_index_map[item["id"]] = idx
                idx += 1

        csv_rows = []

        for spk_id, spk_data in list(state.get("speakers", {}).items()):
            spk_name = spk_data.get("name", spk_id)
            safe_spk_name = sanitize_path_component(spk_name, "未命名发音人", 64)
            safe_spk_id = sanitize_path_component(spk_id, "speaker", 48)
            safe_spk_dir_name = f"{safe_spk_name}__{safe_spk_id}"
            dest_spk_audio_dir = audio_dir / safe_spk_dir_name
            os.makedirs(dest_spk_audio_dir, exist_ok=True)

            for word_id, item_data in list(spk_data.get("items", {}).items()):
                source_wav_path = os.path.join(WORKSPACE_DIR, "audio", spk_id, f"{spk_id}_{word_id}.wav")
                if not os.path.exists(source_wav_path):
                    source_wav_path = os.path.join(WORKSPACE_DIR, "audio", f"{spk_id}_{word_id}.wav")

                if os.path.exists(source_wav_path):
                    item_label = item_data.get("label", word_id)
                    item_idx = item_index_map.get(word_id, 0)
                    safe_item_label = sanitize_path_component(item_label, "未命名词项", 72)
                    safe_word_id = sanitize_path_component(word_id, "item", 48)
                    dest_filename = f"{item_idx}_{safe_item_label}__{safe_word_id}.wav"
                    dest_file_path = dest_spk_audio_dir / dest_filename
                    shutil.copy2(source_wav_path, dest_file_path)

                    item_data["path"] = f"audio/{safe_spk_dir_name}/{dest_filename}"

                    grp_name = "默认组"
                    for g in groups:
                        if any(item["id"] == word_id for item in g.get("items", [])):
                            grp_name = g.get("name", grp_name)
                            break

                    quality = item_data.get("quality", {})
                    issues = []
                    if quality.get("clipping", {}).get("abnormal"):
                        issues.append("音频截断")
                    if quality.get("volume", {}).get("status") in ("too_quiet", "too_loud"):
                        issues.append(quality["volume"]["label"])
                    if quality.get("creak", {}).get("abnormal"):
                        issues.append("有嘎裂声")
                    quality_str = ",".join(issues) if issues else "正常"

                    csv_rows.append({
                        "发音人": spk_name,
                        "发音人ID": spk_id,
                        "分组": grp_name,
                        "词项": item_label,
                        "词项ID": word_id,
                        "音频相对路径": f"audio/{safe_spk_dir_name}/{dest_filename}",
                        "录制时间": item_data.get("recorded_at", ""),
                        "时长(ms)": item_data.get("duration_ms", ""),
                        "采样率(Hz)": item_data.get("sample_rate_hz", ""),
                        "来源设备": item_data.get("source", ""),
                        "质量检测": quality_str
                    })

        csv_headers = ["发音人", "发音人ID", "分组", "词项", "词项ID", "音频相对路径", "录制时间", "时长(ms)", "采样率(Hz)", "来源设备", "质量检测"]
        with open(logs_dir / "recordings.csv", "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=csv_headers)
            writer.writeheader()
            for r in csv_rows:
                writer.writerow(r)

        with open(temp_dir / "project.json", "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        with open(logs_dir / "export.json", "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)

        backup_dir = parent_dir / f"{target_path.name}_bak_{uuid.uuid4().hex}"
        if target_path.exists():
            os.rename(target_path, backup_dir)
        try:
            os.rename(temp_dir, target_path)
            if backup_dir.exists():
                shutil.rmtree(backup_dir)
        except Exception as e:
            if backup_dir.exists():
                if target_path.exists():
                    shutil.rmtree(target_path)
                os.rename(backup_dir, target_path)
            raise e

        return {"status": "success", "message": "工程已成功导出到文件夹"}
    except Exception as e:
        if temp_dir.exists():
            shutil.rmtree(temp_dir)
        raise HTTPException(status_code=500, detail=f"导出到目录失败: {e}")

@app.post("/api/project/import_folder")
async def api_import_project_folder(payload: Dict[str, Any]):
    folder_path = payload.get("folder_path")
    if not folder_path:
        raise HTTPException(status_code=400, detail="缺少文件夹路径")

    src_path = Path(folder_path).resolve()
    if not src_path.exists() or not src_path.is_dir():
        raise HTTPException(status_code=400, detail="指定的文件夹不存在或不是目录")

    marker_file = src_path / ".phonrec-project.json"
    if not marker_file.exists():
        raise HTTPException(status_code=400, detail="不是合法的 PhonRec 文件夹工程：缺少 .phonrec-project.json")
    try:
        with open(marker_file, "r", encoding="utf-8") as f:
            marker_data = json.load(f)
        if marker_data.get("marker") != "phonrec.folder-project.v1":
            raise HTTPException(status_code=400, detail="不兼容的工程文件夹标记")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"读取文件夹工程标记文件失败: {e}")

    project_json_file = src_path / "project.json"
    if not project_json_file.exists():
        raise HTTPException(status_code=400, detail="缺少 project.json 配置文件")

    try:
        with open(project_json_file, "rb") as f:
            raw_bytes = f.read()
        try:
            state = json.loads(raw_bytes.decode("utf-8"))
        except UnicodeDecodeError:
            state = json.loads(raw_bytes.decode("gb18030"))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"解析 project.json 失败: {e}")

    for root, dirs, files in os.walk(str(src_path)):
        for d in dirs:
            dir_path = Path(root) / d
            if dir_path.is_symlink():
                raise HTTPException(status_code=400, detail="工程文件夹中不得包含符号链接")
        for file in files:
            file_path = Path(root) / file
            if file_path.is_symlink():
                raise HTTPException(status_code=400, detail="工程文件夹中不得包含符号链接")

    for spk_id, spk_data in state.get("speakers", {}).items():
        for word_id, item_data in spk_data.get("items", {}).items():
            rel_path_str = item_data.get("path")
            if rel_path_str:
                if os.path.isabs(rel_path_str) or ".." in rel_path_str:
                    raise HTTPException(status_code=400, detail="音频相对路径含有非法穿越字符")
                actual_file = src_path / rel_path_str
                if not actual_file.exists() or not actual_file.is_file():
                    raise HTTPException(status_code=400, detail=f"音频文件不存在: {rel_path_str}")

    workspace_parent = os.path.dirname(WORKSPACE_DIR)
    staging_dir = tempfile.mkdtemp(prefix="phonrec_staging_", dir=workspace_parent)

    try:
        shutil.copytree(src_path, staging_dir, dirs_exist_ok=True, symlinks=False)

        staging_audio_root = Path(staging_dir) / "audio"
        new_state = json.loads(json.dumps(state))
        temp_audio_dir = Path(staging_dir) / "audio_temp"
        os.makedirs(temp_audio_dir, exist_ok=True)

        for spk_id, spk_data in list(new_state.get("speakers", {}).items()):
            spk_name = spk_data.get("name", spk_id)
            safe_spk_dir_name = f"{spk_name}__{spk_id}".replace("/", "_").replace("\\", "_")
            target_spk_dir = temp_audio_dir / spk_id
            os.makedirs(target_spk_dir, exist_ok=True)

            for word_id, item_data in list(spk_data.get("items", {}).items()):
                rel_path = item_data.get("path")
                if rel_path:
                    src_file = Path(staging_dir) / rel_path
                    if src_file.exists():
                        target_filename = f"{spk_id}_{word_id}.wav"
                        shutil.copy2(src_file, target_spk_dir / target_filename)
                        item_data["path"] = f"audio/{spk_id}/{target_filename}"

        if staging_audio_root.exists():
            shutil.rmtree(staging_audio_root)
        os.rename(temp_audio_dir, staging_audio_root)

        for p in [Path(staging_dir) / "wordlist", Path(staging_dir) / "logs", Path(staging_dir) / ".phonrec-project.json"]:
            if p.is_dir():
                shutil.rmtree(p)
            elif p.exists():
                os.remove(p)

        with open(Path(staging_dir) / "project.json", "w", encoding="utf-8") as f:
            json.dump(new_state, f, ensure_ascii=False, indent=2)

        backup_dir = os.path.join(workspace_parent, "workspace_backup")
        if os.path.exists(backup_dir):
            shutil.rmtree(backup_dir)
        if os.path.exists(WORKSPACE_DIR):
            shutil.copytree(WORKSPACE_DIR, backup_dir)

        try:
            clear_workspace()
            shutil.copytree(staging_dir, WORKSPACE_DIR, dirs_exist_ok=True)
            state = normalize_workspace_after_import(new_state)
            warnings = state.pop("_warnings", [])
            summary = state.pop("_summary", {})
            if os.path.exists(backup_dir):
                shutil.rmtree(backup_dir)
            return {"status": "success", "state": state, "warnings": warnings, "summary": summary}
        except Exception as e:
            clear_workspace()
            if os.path.exists(backup_dir):
                shutil.copytree(backup_dir, WORKSPACE_DIR, dirs_exist_ok=True)
                shutil.rmtree(backup_dir)
            raise e

    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"导入文件夹工程失败: {e}")
    finally:
        if os.path.exists(staging_dir):
            shutil.rmtree(staging_dir)


def create_server_socket(port: int = 0) -> socket.socket:
    """在回环地址上创建监听套接字；端口为 0 时由系统分配。"""
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_socket.bind(("127.0.0.1", port))
    server_socket.listen(2048)
    return server_socket


def run_engine(argv: Optional[List[str]] = None) -> None:
    """启动由 PhonRec 管理生命周期的本地分析引擎。"""
    parser = argparse.ArgumentParser(description="PhonTracer PhonRec 分析引擎")
    parser.add_argument("--workspace", required=True, help="PhonRec 工作区目录")
    parser.add_argument("--port", type=int, default=0, help="监听端口，0 表示自动分配")
    args = parser.parse_args(argv)

    token = os.environ.get("PHONTRACER_SESSION_TOKEN", "")
    if not token:
        raise SystemExit("缺少 PHONTRACER_SESSION_TOKEN，拒绝启动分析引擎")

    configure_session_token(token)
    configure_workspace(args.workspace)
    server_socket = create_server_socket(args.port)
    assigned_port = server_socket.getsockname()[1]
    print(
        json.dumps(
            {
                "event": "ready",
                "port": assigned_port,
                "protocol_version": PROTOCOL_VERSION,
                "engine_version": ENGINE_VERSION,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )

    import uvicorn

    config = uvicorn.Config(app, log_level="warning", access_log=False)
    server = uvicorn.Server(config)
    server.run(sockets=[server_socket])


if __name__ == "__main__":
    run_engine()
