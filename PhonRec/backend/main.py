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

from modules.acoustic_analysis_service import normalize_analysis_params, analyze_audio_to_bundle
from modules.project_integrity import append_audit_log, update_manifest, calculate_file_sha256
import datetime
from modules.project_adaptor import (
    safe_extract_zip,
    safe_resource_token,
    adapt_project_state,
    prune_unreferenced_resources,
    repair_wav_header,
    resolve_workspace_path,
    validate_project_resources,
    validate_project_version,
)
from modules.wordlist_v2 import (
    build_document_from_csv_text,
    build_document_from_v1_text,
    normalize_wordlist_document,
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

def sanitize_path_component(value: Any, fallback: str, max_length: int = 96) -> str:
    """生成可在 Windows、macOS 和 Linux 上安全使用的单个路径名称。"""
    return safe_resource_token(value, fallback, max_length)


def sanitize_display_path_component(value: Any, fallback: str, max_length: int = 96) -> str:
    """清理带编号的导出显示名；编号已负责消除同名碰撞。"""
    text = str(value or "").strip()
    sanitized = "".join(
        "_" if ord(char) < 32 or char in '<>:"/\\|?*' else char
        for char in text
    ).strip(" .")[:max_length].rstrip(" .") or fallback
    if sanitized.split(".", 1)[0].upper() in {
        "CON", "PRN", "AUX", "NUL",
        *(f"COM{index}" for index in range(1, 10)),
        *(f"LPT{index}" for index in range(1, 10)),
    }:
        sanitized = f"_{sanitized}"
    return sanitized


def normalize_imported_groups(groups: Any) -> List[Dict[str, Any]]:
    """规范化普通/高级字表并保证分组 ID、词项 ID 在整个字表内唯一。"""
    normalized = normalize_wordlist_document({"groups": groups}).get("groups", [])
    used_group_ids = set()
    used_item_ids = set()
    result = []
    for group in normalized:
        items = []
        for item in group.get("items", []):
            label = str(item.get("label") or "").strip()
            if not label:
                continue
            item_id = str(item.get("id") or "").strip()
            if not item_id or item_id in used_item_ids:
                item_id = uuid.uuid4().hex[:8]
                while item_id in used_item_ids:
                    item_id = uuid.uuid4().hex[:8]
            used_item_ids.add(item_id)
            item["id"] = item_id
            item["label"] = label
            items.append(item)
        if not items:
            continue
        group_id = str(group.get("id") or "").strip()
        if not group_id or group_id in used_group_ids:
            group_id = uuid.uuid4().hex[:8]
            while group_id in used_group_ids:
                group_id = uuid.uuid4().hex[:8]
        used_group_ids.add(group_id)
        group["id"] = group_id
        group["items"] = items
        result.append(group)
    if not result:
        raise ValueError("字表中没有可录制词项")
    return result


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


def commit_workspace_transaction(
    replacements: Dict[str, str],
    event_type: str,
    event_details: Dict[str, Any],
) -> None:
    """提交一组已暂存文件，并在任一步失败时恢复工程、审计与清单。"""
    transaction_id = uuid.uuid4().hex
    transaction_backup_dir = os.path.join(
        os.path.dirname(WORKSPACE_DIR),
        f".phonrec_transaction_{transaction_id}",
    )
    audit_path = os.path.join(WORKSPACE_DIR, "logs", "audit.jsonl")
    manifest_path = os.path.join(WORKSPACE_DIR, "integrity", "manifest.json")
    tracked_paths = list(dict.fromkeys([*replacements.keys(), audit_path, manifest_path]))
    backups: Dict[str, Optional[str]] = {}

    try:
        os.makedirs(transaction_backup_dir, exist_ok=False)
        for index, target_path in enumerate(tracked_paths):
            if os.path.exists(target_path):
                backup_path = os.path.join(transaction_backup_dir, f"{index}.backup")
                shutil.copy2(target_path, backup_path)
                backups[target_path] = backup_path
            else:
                backups[target_path] = None

        for target_path, staged_path in replacements.items():
            if not os.path.isfile(staged_path):
                raise FileNotFoundError(f"事务暂存文件不存在: {staged_path}")
            os.makedirs(os.path.dirname(target_path), exist_ok=True)
            os.replace(staged_path, target_path)

        append_audit_log(WORKSPACE_DIR, event_type, event_details)
        update_manifest(WORKSPACE_DIR)
    except Exception as exc:
        rollback_errors = []
        for target_path in reversed(tracked_paths):
            if target_path not in backups:
                continue
            backup_path = backups.get(target_path)
            try:
                if backup_path and os.path.exists(backup_path):
                    os.replace(backup_path, target_path)
                elif backup_path is None and os.path.exists(target_path):
                    os.remove(target_path)
            except OSError as rollback_exc:
                rollback_errors.append(f"{target_path}: {rollback_exc}")
        if rollback_errors:
            raise RuntimeError(
                "工作区事务失败且回滚不完整: " + "; ".join(rollback_errors)
            ) from exc
        raise
    finally:
        shutil.rmtree(transaction_backup_dir, ignore_errors=True)


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
            "handoff-review",
            "integrity-check"
        ],
        "capability_versions": {
            "project-state": "1.0",
            "project-import-export": "1.0",
            "wordlist-import": "1.0",
            "audio-storage": "1.0",
            "audio-quality": "1.0",
            "spectrogram": "1.0",
            "pitch": "1.0",
            "formants": "1.0",
            "handoff-review": "1.0",
            "integrity-check": "1.0"
        }
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

def get_speech_bounds(y: np.ndarray, sr: int) -> tuple[int, int]:
    """Find the start and end sample indices of the non-silent portion of the audio."""
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

    # 150ms margin
    margin_samples = int(sr * 0.15)
    start_sample = max(0, start_sample - margin_samples)
    end_sample = min(len(y), end_sample + margin_samples)

    return start_sample, end_sample

def generate_spectrogram(y: np.ndarray, sr: int, bundle: Optional[Dict[str, Any]] = None) -> str:
    """Generate a clean colormapped spectrogram image base64 string with F0/F1/F2 curves."""
    start_sample, end_sample = get_speech_bounds(y, sr)
    y_trimmed = y[start_sample:end_sample]
    if len(y_trimmed) > 0:
        y = y_trimmed

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
    fig = plt.figure(figsize=(6, 3), dpi=150, facecolor='#ffffff')
    ax = fig.add_axes([0, 0, 1, 1])
    ax.axis('off')

    # Plot spectrogram with gray_r colormap (white background for silence)
    ax.pcolormesh(t, f, Sxx_db, shading='gouraud', cmap='gray_r')

    # Draw F0 and formants
    try:
        if bundle is not None:
            # Directly extract from bundle
            pitch_ts = np.array(bundle["pitch"]["xs"])
            pitch_values = np.array(bundle["pitch"]["freqs"])
            speech_start = bundle["speech_bounds"]["start"]
            pitch_ts = pitch_ts - speech_start
            f0_plot = pitch_values.copy()
            f0_plot[f0_plot == 0] = np.nan

            formant_ts = np.array(bundle["formants"]["xs"]) - speech_start
            f1_plot = np.array(bundle["formants"]["f1"])
            f2_plot = np.array(bundle["formants"]["f2"])
            f1_plot[f1_plot == 0.0] = np.nan
            f2_plot[f2_plot == 0.0] = np.nan
        else:
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
        if not isinstance(state, dict):
            raise ValueError("project.json 顶层必须是对象")
        validate_project_version(state)
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

    speaker_dir = os.path.join(AUDIO_DIR, safe_speaker_id)
    os.makedirs(speaker_dir, exist_ok=True)
    os.makedirs(DATA_DIR, exist_ok=True)

    filename = f"{safe_speaker_id}_{safe_word_id}.wav"
    file_path = os.path.join(speaker_dir, filename)

    # Paths for temp files
    temp_path = f"{file_path}.{uuid.uuid4().hex}.tmp.wav"
    pitch_npz_path = os.path.join(DATA_DIR, f"{safe_speaker_id}_{safe_word_id}.npz")
    formant_npz_path = os.path.join(DATA_DIR, f"{safe_speaker_id}_{safe_word_id}_formant.npz")

    temp_pitch_npz = pitch_npz_path + f".{uuid.uuid4().hex}.tmp.npz"
    temp_formant_npz = formant_npz_path + f".{uuid.uuid4().hex}.tmp.npz"

    project_path = os.path.join(WORKSPACE_DIR, "project.json")
    project_tmp = None

    try:
        # 1. Write uploaded WAV to temp file
        with open(temp_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        # 2. Repair header
        repair_wav_header(temp_path)

        # 3. Read and normalize WAV
        sr, y_int = wavfile.read(temp_path)
        y = normalize_audio_samples(y_int)

        # 4. Load project state and get speaker params
        if not os.path.exists(project_path):
            raise ValueError("当前工作区尚未建立工程状态")
        with open(project_path, "r", encoding="utf-8") as project_file:
            state = json.load(project_file)

        speaker = state.setdefault("speakers", {}).get(speaker_id)
        if not isinstance(speaker, dict):
            raise ValueError("录音目标发音人已不存在")
        items = speaker.setdefault("items", {})
        item = items.get(word_id)
        if not isinstance(item, dict):
            raise ValueError("录音目标词项已不存在")

        spk_params = speaker.get("last_params", {})

        # 5. Extract bundle (shared kernel)
        import parselmouth

        norm_params = normalize_analysis_params(spk_params)
        audio_sha256 = calculate_file_sha256(temp_path)
        snd = parselmouth.Sound(temp_path)

        bundle = analyze_audio_to_bundle(snd, norm_params, audio_sha256)

        # 6. Generate spectrogram and run quality check
        spec_b64 = generate_spectrogram(y, sr, bundle)
        quality = analyze_recording_quality(y, sr, resolved_quality_config)

        recorded_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
        duration_ms = int((len(y_int) / sr) * 1000) if sr > 0 else 0
        stored = quality.get("decision") != "retry"

        if stored:
            # 7. Write npz cache files to temp files
            np.savez(temp_pitch_npz, xs=bundle["pitch"]["xs"], freqs=bundle["pitch"]["freqs"])
            np.savez(temp_formant_npz, xs=bundle["formants"]["xs"], f1=bundle["formants"]["f1"], f2=bundle["formants"]["f2"], f3=bundle["formants"]["f3"])

            pitch_cache_sha256 = calculate_file_sha256(temp_pitch_npz)
            formant_cache_sha256 = calculate_file_sha256(temp_formant_npz)

            # 8. Update item in project state dict
            rel_path = f"audio/{safe_speaker_id}/{filename}"
            item.update({
                "path": rel_path,
                "pitch_data_file": f"data/{safe_speaker_id}_{safe_word_id}.npz",
                "formant_data_file": f"data/{safe_speaker_id}_{safe_word_id}_formant.npz",
                "analysis_state": {
                    "algorithm_version": "audio-core-v1",
                    "audio_sha256": audio_sha256,
                    "params_sha256": bundle["params_sha256"],
                    "pitch_cache_sha256": pitch_cache_sha256,
                    "formant_cache_sha256": formant_cache_sha256
                },
                "quality": quality,
                "recorded_at": recorded_at,
                "duration_ms": duration_ms,
                "sample_rate_hz": int(sr),
                "channels": int(y_int.shape[1]) if y_int.ndim > 1 else 1,
                "format": "wav",
                "source": source,
            })

            # 9. 暂存 project.json，统一事务最后提交
            project_tmp = project_path + f".{uuid.uuid4().hex}.tmp.json"
            with open(project_tmp, "w", encoding="utf-8") as project_file:
                json.dump(state, project_file, ensure_ascii=False, indent=2)

            # 10. 音频、缓存、工程状态、审计和清单作为同一事务提交
            commit_workspace_transaction(
                {
                    file_path: temp_path,
                    pitch_npz_path: temp_pitch_npz,
                    formant_npz_path: temp_formant_npz,
                    project_path: project_tmp,
                },
                "audio_saved",
                {
                    "speaker_id": speaker_id,
                    "word_id": word_id,
                    "audio_sha256": audio_sha256,
                },
            )
        else:
            # If not stored, remove temp WAV
            if os.path.exists(temp_path):
                os.remove(temp_path)

        return {
            "status": "success",
            "stored": stored,
            "path": rel_path if stored else "",
            "quality": quality,
            "spectrogram": f"data:image/png;base64,{spec_b64}",
            "recorded_at": recorded_at if stored else "",
            "duration_ms": duration_ms if stored else 0,
            "sample_rate_hz": int(sr) if stored else 0,
            "channels": (int(y_int.shape[1]) if y_int.ndim > 1 else 1) if stored else 1,
            "format": "wav" if stored else "",
            "source": source if stored else ""
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        # Rollback/Clean up temp files
        for p in (temp_path, temp_pitch_npz, temp_formant_npz, project_tmp):
            if p and os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass
        raise HTTPException(status_code=500, detail=f"Failed to save audio: {e}")

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

    pitch_npz_path = os.path.join(DATA_DIR, f"{safe_speaker_id}_{safe_word_id}.npz")
    formant_npz_path = os.path.join(DATA_DIR, f"{safe_speaker_id}_{safe_word_id}_formant.npz")

    temp_pitch_npz = pitch_npz_path + f".{uuid.uuid4().hex}.tmp.npz"
    temp_formant_npz = formant_npz_path + f".{uuid.uuid4().hex}.tmp.npz"

    project_path = os.path.join(WORKSPACE_DIR, "project.json")
    project_tmp = None

    try:
        # Read WAV file
        repair_wav_header(file_path)
        sr, y_int = wavfile.read(file_path)
        y = normalize_audio_samples(y_int)

        # Load project state and get speaker params
        has_project = os.path.exists(project_path)
        spk_params = {}
        if has_project:
            with open(project_path, "r", encoding="utf-8") as project_file:
                state = json.load(project_file)

            speaker = state.setdefault("speakers", {}).get(speaker_id)
            if not isinstance(speaker, dict):
                raise ValueError("分析目标发音人已不存在")
            items = speaker.setdefault("items", {})
            item = items.get(word_id)
            if not isinstance(item, dict):
                raise ValueError("分析目标词项已不存在")

            spk_params = speaker.get("last_params", {})

        # Extract bundle
        import parselmouth

        norm_params = normalize_analysis_params(spk_params)
        audio_sha256 = calculate_file_sha256(file_path)
        snd = parselmouth.Sound(file_path)

        bundle = analyze_audio_to_bundle(snd, norm_params, audio_sha256)

        # Generate spectrogram and run quality check
        spec_b64 = generate_spectrogram(y, sr, bundle)
        quality = analyze_recording_quality(y, sr, resolved_quality_config)

        if has_project:
            # Write cache files atomically
            np.savez(temp_pitch_npz, xs=bundle["pitch"]["xs"], freqs=bundle["pitch"]["freqs"])
            np.savez(temp_formant_npz, xs=bundle["formants"]["xs"], f1=bundle["formants"]["f1"], f2=bundle["formants"]["f2"], f3=bundle["formants"]["f3"])

            pitch_cache_sha256 = calculate_file_sha256(temp_pitch_npz)
            formant_cache_sha256 = calculate_file_sha256(temp_formant_npz)

            # Update project state
            item.update({
                "pitch_data_file": f"data/{safe_speaker_id}_{safe_word_id}.npz",
                "formant_data_file": f"data/{safe_speaker_id}_{safe_word_id}_formant.npz",
                "analysis_state": {
                    "algorithm_version": "audio-core-v1",
                    "audio_sha256": audio_sha256,
                    "params_sha256": bundle["params_sha256"],
                    "pitch_cache_sha256": pitch_cache_sha256,
                    "formant_cache_sha256": formant_cache_sha256
                },
                "quality": quality
            })

            project_tmp = project_path + f".{uuid.uuid4().hex}.tmp.json"
            with open(project_tmp, "w", encoding="utf-8") as project_file:
                json.dump(state, project_file, ensure_ascii=False, indent=2)

            commit_workspace_transaction(
                {
                    pitch_npz_path: temp_pitch_npz,
                    formant_npz_path: temp_formant_npz,
                    project_path: project_tmp,
                },
                "analysis_completed",
                {
                    "speaker_id": speaker_id,
                    "word_id": word_id,
                    "audio_sha256": audio_sha256,
                },
            )

        return {
            "status": "success",
            "quality": quality,
            "spectrogram": f"data:image/png;base64,{spec_b64}"
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        for p in (temp_pitch_npz, temp_formant_npz, project_tmp):
            if p and os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass
        raise HTTPException(status_code=500, detail=f"Audio analysis failed: {e}")

@app.get("/api/audio/file")
async def api_get_audio_file(speaker_id: str, word_id: str):
    """Serve the WAV audio file for a given speaker and item."""
    safe_speaker_id = sanitize_path_component(speaker_id, "speaker", 64)
    safe_word_id = sanitize_path_component(word_id, "item", 64)
    filename = f"{safe_speaker_id}_{safe_word_id}.wav"
    file_path = os.path.join(AUDIO_DIR, safe_speaker_id, filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Audio file not found")
    return FileResponse(file_path, media_type="audio/wav")

@app.get("/api/audio/analysis")
async def api_get_audio_analysis(speaker_id: str, word_id: str):
    safe_speaker_id = sanitize_path_component(speaker_id, "speaker", 64)
    safe_word_id = sanitize_path_component(word_id, "item", 64)

    project_path = os.path.join(WORKSPACE_DIR, "project.json")
    if not os.path.exists(project_path):
        raise HTTPException(status_code=404, detail="Project not found")

    with open(project_path, "r", encoding="utf-8") as f:
        state = json.load(f)

    speaker = state.get("speakers", {}).get(speaker_id)
    if not speaker:
        raise HTTPException(status_code=404, detail="Speaker not found")
    item = speaker.get("items", {}).get(word_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")

    audio_rel = item.get("path")
    if not audio_rel:
        return {
            "status": "not_recorded",
            "cache_hit": False,
            "message": "Audio file not recorded yet"
        }

    audio_path = os.path.join(WORKSPACE_DIR, *audio_rel.split("/"))
    if not os.path.exists(audio_path):
        raise HTTPException(status_code=404, detail=f"Audio file missing: {audio_rel}")

    # Check cache hit status
    cache_hit = False
    analysis_state = item.get("analysis_state", {})
    pitch_file = item.get("pitch_data_file")
    formant_file = item.get("formant_data_file")

    pitch_data = None
    formant_data = None

    if pitch_file and formant_file and analysis_state:
        pitch_abs = os.path.join(WORKSPACE_DIR, *pitch_file.split("/"))
        formant_abs = os.path.join(WORKSPACE_DIR, *formant_file.split("/"))
        if os.path.exists(pitch_abs) and os.path.exists(formant_abs):
            try:
                # Validate hashes
                curr_audio_sha = calculate_file_sha256(audio_path)
                curr_pitch_sha = calculate_file_sha256(pitch_abs)
                curr_formant_sha = calculate_file_sha256(formant_abs)

                if (curr_audio_sha == analysis_state.get("audio_sha256") and
                    curr_pitch_sha == analysis_state.get("pitch_cache_sha256") and
                    curr_formant_sha == analysis_state.get("formant_cache_sha256")):

                    # Cache hit!
                    with np.load(pitch_abs) as loaded:
                        pitch_data = {
                            "xs": loaded["xs"].tolist(),
                            "freqs": loaded["freqs"].tolist(),
                            "engine": "praat_ac"
                        }
                    with np.load(formant_abs) as loaded:
                        formant_data = {
                            "xs": loaded["xs"].tolist(),
                            "f1": loaded["f1"].tolist(),
                            "f2": loaded["f2"].tolist(),
                            "f3": loaded["f3"].tolist() if "f3" in loaded else [0.0]*len(loaded["xs"]),
                            "engine": "praat_burg"
                        }
                    cache_hit = True
            except Exception:
                pass

    if not cache_hit:
        # Cache miss, run analysis dynamically to rebuild the cache!
        spk_params = speaker.get("last_params", {})
        import parselmouth
        temporary_paths = []

        try:
            norm_params = normalize_analysis_params(spk_params)
            snd = parselmouth.Sound(audio_path)
            audio_sha256 = calculate_file_sha256(audio_path)
            bundle = analyze_audio_to_bundle(snd, norm_params, audio_sha256)

            pitch_data = bundle["pitch"]
            formant_data = bundle["formants"]

            # Write cache files atomically
            pitch_npz_path = os.path.join(DATA_DIR, f"{safe_speaker_id}_{safe_word_id}.npz")
            formant_npz_path = os.path.join(DATA_DIR, f"{safe_speaker_id}_{safe_word_id}_formant.npz")
            os.makedirs(os.path.dirname(pitch_npz_path), exist_ok=True)

            temp_pitch = pitch_npz_path + f".{uuid.uuid4().hex}.tmp.npz"
            temp_formant = formant_npz_path + f".{uuid.uuid4().hex}.tmp.npz"
            temporary_paths.extend([temp_pitch, temp_formant])

            np.savez(temp_pitch, xs=pitch_data["xs"], freqs=pitch_data["freqs"])
            np.savez(temp_formant, xs=formant_data["xs"], f1=formant_data["f1"], f2=formant_data["f2"], f3=formant_data["f3"])

            pitch_cache_sha256 = calculate_file_sha256(temp_pitch)
            formant_cache_sha256 = calculate_file_sha256(temp_formant)

            # Update project.json
            item.update({
                "pitch_data_file": f"data/{safe_speaker_id}_{safe_word_id}.npz",
                "formant_data_file": f"data/{safe_speaker_id}_{safe_word_id}_formant.npz",
                "analysis_state": {
                    "algorithm_version": "audio-core-v1",
                    "audio_sha256": audio_sha256,
                    "params_sha256": bundle["params_sha256"],
                    "pitch_cache_sha256": pitch_cache_sha256,
                    "formant_cache_sha256": formant_cache_sha256
                }
            })

            temp_project = project_path + f".{uuid.uuid4().hex}.tmp.json"
            temporary_paths.append(temp_project)
            with open(temp_project, "w", encoding="utf-8") as pf:
                json.dump(state, pf, ensure_ascii=False, indent=2)

            commit_workspace_transaction(
                {
                    pitch_npz_path: temp_pitch,
                    formant_npz_path: temp_formant,
                    project_path: temp_project,
                },
                "analysis_completed",
                {
                    "speaker_id": speaker_id,
                    "word_id": word_id,
                    "audio_sha256": audio_sha256,
                },
            )
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Dynamic analysis failed: {e}")
        finally:
            for temporary_path in temporary_paths:
                if os.path.exists(temporary_path):
                    try:
                        os.remove(temporary_path)
                    except OSError:
                        pass

    # Calculate summary stats for response
    voiced_freqs = [f for f in pitch_data["freqs"] if f > 0]
    voiced_ratio = len(voiced_freqs) / len(pitch_data["freqs"]) if len(pitch_data["freqs"]) > 0 else 0.0
    f0_median = float(np.median(voiced_freqs)) if len(voiced_freqs) > 0 else 0.0

    return {
        "status": "success",
        "cache_hit": cache_hit,
        "pitch": pitch_data,
        "formants": formant_data,
        "summary": {
            "voiced_ratio": voiced_ratio,
            "f0_median_hz": f0_median
        }
    }

@app.post("/api/handoff/create")
async def api_handoff_create(payload: Dict[str, Any]):
    speaker_id = payload.get("speaker_id")
    word_id = payload.get("word_id")
    if not isinstance(speaker_id, str) or not speaker_id or not isinstance(word_id, str) or not word_id:
        raise HTTPException(status_code=400, detail="Missing speaker_id or word_id")

    project_json_path = os.path.join(WORKSPACE_DIR, "project.json")
    if not os.path.exists(project_json_path):
        raise HTTPException(status_code=400, detail="No active project state")

    try:
        with open(project_json_path, "r", encoding="utf-8") as project_file:
            project_state = json.load(project_file)
        speaker = project_state.get("speakers", {}).get(speaker_id)
        if not isinstance(speaker, dict):
            raise HTTPException(status_code=404, detail="Speaker not found")
        item = speaker.get("items", {}).get(word_id)
        if not isinstance(item, dict):
            raise HTTPException(status_code=404, detail="Item not found")

        handoff_id = str(uuid.uuid4())
        workspace_parent = os.path.dirname(WORKSPACE_DIR)
        handoff_dir = os.path.join(workspace_parent, "handoffs", handoff_id)
        os.makedirs(handoff_dir, exist_ok=True)

        # 1. Update manifest and audit log in current workspace before snapshot
        append_audit_log(WORKSPACE_DIR, "handoff_snapshot_created", {
            "handoff_id": handoff_id,
            "speaker_id": speaker_id,
            "word_id": word_id
        })
        update_manifest(WORKSPACE_DIR)

        # 2. Package current workspace into review_snapshot.teproj
        teproj_name = "review_snapshot.teproj"
        teproj_path = os.path.join(handoff_dir, teproj_name)
        with zipfile.ZipFile(teproj_path, "w", zipfile.ZIP_DEFLATED) as zip_file:
            for root, dirs, files in os.walk(WORKSPACE_DIR):
                for file in files:
                    file_path = os.path.join(root, file)
                    rel_path = os.path.relpath(file_path, WORKSPACE_DIR).replace(os.sep, "/")
                    if ".tmp." in file or file.endswith((".tmp", ".temp", ".rollback")):
                        continue
                    zip_file.write(file_path, rel_path)

        # 3. Create handoff.json
        handoff_data = {
            "handoff_id": handoff_id,
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "speaker_id": speaker_id,
            "word_id": word_id,
            "project_archive": teproj_name
        }
        handoff_json_path = os.path.join(handoff_dir, "handoff.json")
        with open(handoff_json_path, "w", encoding="utf-8") as f:
            json.dump(handoff_data, f, ensure_ascii=False, indent=2)

        # 4. Set both files to read-only
        import stat
        os.chmod(teproj_path, stat.S_IREAD)
        os.chmod(handoff_json_path, stat.S_IREAD)

        return {
            "status": "success",
            "handoff_id": handoff_id,
            "archive_path": os.path.abspath(teproj_path),
            "manifest_path": os.path.abspath(handoff_json_path)
        }
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Failed to create handoff snapshot: {e}")

@app.post("/api/wordlist/import")
async def api_import_wordlist(file: UploadFile = File(...)):
    """Parse uploaded word list file (.ptwl, .txt, .csv) into standardized group structure."""
    filename = file.filename.lower()
    content_bytes = await file.read()

    groups = []

    try:
        if filename.endswith(".ptwl"):
            # Advanced JSON Wordlist
            data = json.loads(content_bytes.decode("utf-8-sig"))
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
                        "meta": rg.get("meta", {}),
                        "items": items
                    })

        elif filename.endswith(".csv"):
            content_str = content_bytes.decode("utf-8-sig")
            groups = build_document_from_csv_text(content_str).get("groups", [])

        else:
            content_str = content_bytes.decode("utf-8-sig")
            groups = build_document_from_v1_text(content_str).get("groups", [])
            for group in groups:
                for item in group.get("items", []):
                    item["metadata_source"] = "导入TXT"

        return {"status": "success", "groups": normalize_imported_groups(groups)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"字表格式无效：{e}")
    except HTTPException:
        raise
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
        if not isinstance(state, dict):
            raise ValueError("project.json 顶层必须是对象")
        validate_project_version(state)
        validate_project_resources(state, staging_dir)

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
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"导入工程失败：{e}")
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
            safe_spk_name = sanitize_display_path_component(spk_name, "未命名发音人", 64)
            safe_spk_id = sanitize_display_path_component(spk_id, "speaker", 48)
            safe_spk_dir_name = f"{safe_spk_name}__{safe_spk_id}"
            dest_spk_audio_dir = audio_dir / safe_spk_dir_name
            os.makedirs(dest_spk_audio_dir, exist_ok=True)

            for word_id, item_data in list(spk_data.get("items", {}).items()):
                stored_path = item_data.get("path")
                source_wav_path = resolve_workspace_path(stored_path, WORKSPACE_DIR) if stored_path else ""
                if not os.path.exists(source_wav_path):
                    safe_spk_token = sanitize_path_component(spk_id, "speaker", 64)
                    safe_word_token = sanitize_path_component(word_id, "item", 64)
                    source_wav_path = os.path.join(
                        WORKSPACE_DIR, "audio", safe_spk_token,
                        f"{safe_spk_token}_{safe_word_token}.wav",
                    )

                if os.path.exists(source_wav_path):
                    item_label = item_data.get("label", word_id)
                    item_idx = item_index_map.get(word_id, 0)
                    safe_item_label = sanitize_display_path_component(item_label, "未命名词项", 72)
                    safe_word_id = sanitize_display_path_component(word_id, "item", 48)
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
