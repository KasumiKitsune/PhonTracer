import argparse
import base64
import csv
import io
import json
import os
import secrets
import shutil
import socket
import stat
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

init_workspace()

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
    
    # Size in inches (900x375 pixels at 150 DPI)
    # Using white background for the spectrogram card to match Kasumi Light Theme
    fig = plt.figure(figsize=(6, 2.5), dpi=150, facecolor='#ffffff')
    ax = fig.add_axes([0, 0, 1, 1])
    ax.axis('off')
    
    # Plot spectrogram with gray_r colormap (white background for silence)
    ax.pcolormesh(t, f, Sxx_db, shading='gouraud', cmap='gray_r')
    ax.set_ylim(0, min(8000, sr / 2))
    
    # Draw F0 and formants using parselmouth
    try:
        sound = parselmouth.Sound(y, sampling_frequency=sr)
        
        # 1. Pitch (F0) -> Blue curve
        pitch = sound.to_pitch()
        pitch_values = pitch.selected_array['frequency']
        pitch_ts = pitch.xs()
        f0_plot = pitch_values.copy()
        f0_plot[f0_plot == 0] = np.nan
        ax.plot(pitch_ts, f0_plot, color='#3b82f6', linewidth=1.5, solid_capstyle='round')
        
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
        
        ax.plot(formant_ts, f1_plot, color='#ef4444', linewidth=1.2, linestyle='--')
        ax.plot(formant_ts, f2_plot, color='#10b981', linewidth=1.2, linestyle='--')
    except Exception as e:
        # Graceful fallback: print error and return base spectrogram
        print(f"[generate_spectrogram] F0/Formant analysis failed: {e}")
        
    buf = io.BytesIO()
    plt.savefig(buf, format='png', facecolor='#ffffff', bbox_inches='tight', pad_inches=0)
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
        with open(path, "r", encoding="utf-8") as f:
            state = json.load(f)
        return state
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read project state: {e}")

@app.post("/api/project/state")
async def api_save_project_state(state: Dict[str, Any]):
    """Save project.json state to workspace."""
    init_workspace()
    path = os.path.join(WORKSPACE_DIR, "project.json")
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save project state: {e}")

@app.post("/api/audio/save")
async def api_save_audio(
    file: UploadFile = File(...),
    speaker_id: str = Form(...),
    word_id: str = Form(...)
):
    """Save recorded audio blob and automatically analyze it in a single step."""
    init_workspace()
    # Ensure speaker audio directory exists
    speaker_dir = os.path.join(AUDIO_DIR, speaker_id)
    os.makedirs(speaker_dir, exist_ok=True)
    
    # Save file
    filename = f"{speaker_id}_{word_id}.wav"
    file_path = os.path.join(speaker_dir, filename)
    
    try:
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        
        rel_path = f"audio/{speaker_id}/{filename}"
        
        # Run analysis immediately to return quality and spectrogram
        sr, y_int = wavfile.read(file_path)
        if y_int.dtype == np.int16:
            y = y_int.astype(np.float32) / 32768.0
        elif y_int.dtype == np.int32:
            y = y_int.astype(np.float32) / 2147483648.0
        elif y_int.dtype == np.uint8:
            y = (y_int.astype(np.float32) - 128.0) / 128.0
        else:
            y = y_int.astype(np.float32)
            
        is_clipped, clip_ratio = check_clipping(y)
        vol_status, vol_db = check_volume(y, sr)
        is_creaky, creak_ratio = detect_creak(y, sr)
        
        spec_b64 = generate_spectrogram(y, sr)
        
        quality = {
            "clipping": {
                "abnormal": is_clipped,
                "score": clip_ratio,
                "label": "音频截断" if is_clipped else "正常"
            },
            "volume": {
                "status": vol_status,
                "score": vol_db,
                "label": "音量过小" if vol_status == "too_quiet" else ("音量过大" if vol_status == "too_loud" else "正常")
            },
            "creak": {
                "abnormal": is_creaky,
                "score": creak_ratio,
                "label": "有嘎裂声" if is_creaky else "正常"
            }
        }
        
        return {
            "status": "success",
            "path": rel_path,
            "quality": quality,
            "spectrogram": f"data:image/png;base64,{spec_b64}"
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Failed to save and analyze audio file: {e}")

@app.get("/api/audio/file")
async def api_get_audio_file(speaker_id: str, word_id: str):
    """Retrieve the WAV file for playback or decoding."""
    filename = f"{speaker_id}_{word_id}.wav"
    file_path = os.path.join(AUDIO_DIR, speaker_id, filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Audio file not found")
    return FileResponse(file_path, media_type="audio/wav")

@app.post("/api/audio/analyze")
async def api_analyze_audio(
    speaker_id: str = Form(...),
    word_id: str = Form(...)
):
    """Analyze a WAV audio file for quality parameters and generate spectrogram."""
    filename = f"{speaker_id}_{word_id}.wav"
    file_path = os.path.join(AUDIO_DIR, speaker_id, filename)
    
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Audio file not found")
        
    try:
        # Read WAV file
        sr, y_int = wavfile.read(file_path)
        
        # Normalize to float32 between -1.0 and 1.0
        if y_int.dtype == np.int16:
            y = y_int.astype(np.float32) / 32768.0
        elif y_int.dtype == np.int32:
            y = y_int.astype(np.float32) / 2147483648.0
        elif y_int.dtype == np.uint8:
            y = (y_int.astype(np.float32) - 128.0) / 128.0
        else:
            y = y_int.astype(np.float32)
            
        # Run checks
        is_clipped, clip_ratio = check_clipping(y)
        vol_status, vol_db = check_volume(y, sr)
        is_creaky, creak_ratio = detect_creak(y, sr)
        
        # Spectrogram
        spec_b64 = generate_spectrogram(y, sr)
        
        return {
            "status": "success",
            "quality": {
                "clipping": {
                    "abnormal": is_clipped,
                    "score": clip_ratio,
                    "label": "音频截断" if is_clipped else "正常"
                },
                "volume": {
                    "status": vol_status,
                    "score": vol_db,
                    "label": "音量过小" if vol_status == "too_quiet" else ("音量过大" if vol_status == "too_loud" else "正常")
                },
                "creak": {
                    "abnormal": is_creaky,
                    "score": creak_ratio,
                    "label": "有嘎裂声" if is_creaky else "正常"
                }
            },
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
            _safe_extract_zip(zip_ref, staging_dir)

        project_json_path = os.path.join(staging_dir, "project.json")
        if not os.path.exists(project_json_path):
            raise HTTPException(status_code=400, detail="工程无效：缺少 project.json")

        with open(project_json_path, "r", encoding="utf-8") as f:
            state = json.load(f)

        clear_workspace()
        shutil.copytree(staging_dir, WORKSPACE_DIR, dirs_exist_ok=True)
        return {"status": "success", "state": state}
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

def _safe_extract_zip(zip_ref: zipfile.ZipFile, destination: str) -> None:
    """拒绝目录穿越、绝对路径和符号链接后再展开工程。"""
    root = Path(destination).resolve()
    for member in zip_ref.infolist():
        mode = member.external_attr >> 16
        if stat.S_ISLNK(mode):
            raise HTTPException(status_code=400, detail="工程压缩包不得包含符号链接")

        normalized = member.filename.replace("\\", "/")
        target = (root / normalized).resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="工程压缩包包含非法路径") from exc

        if member.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with zip_ref.open(member, "r") as source, open(target, "wb") as output:
            shutil.copyfileobj(source, output)


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
