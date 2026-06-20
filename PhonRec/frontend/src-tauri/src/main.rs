#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::{HashMap, HashSet};
use std::fs;
use std::io::{BufRead, BufReader, Cursor, Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::{mpsc, Arc, Mutex};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};
use tauri::{AppHandle, Emitter, Manager, RunEvent, State};
use uuid::Uuid;
use zip::write::SimpleFileOptions;

const ENGINE_PROTOCOL: u32 = 1;
const DOWNLOAD_URL: &str = "https://github.com/KasumiKitsune/PhonTracer/releases/latest";

#[derive(Clone, Debug, Serialize)]
struct EngineConnection {
    api_base: String,
    token: String,
    protocol_version: u32,
    engine_version: String,
}

#[derive(Clone, Debug, Serialize)]
struct EngineStatus {
    state: String,
    message: String,
    connection: Option<EngineConnection>,
    download_url: String,
}

impl EngineStatus {
    fn ready(connection: EngineConnection) -> Self {
        Self {
            state: "ready".into(),
            message: "分析引擎已就绪".into(),
            connection: Some(connection),
            download_url: DOWNLOAD_URL.into(),
        }
    }

    fn blocked(state: &str, message: impl Into<String>) -> Self {
        Self {
            state: state.into(),
            message: message.into(),
            connection: None,
            download_url: DOWNLOAD_URL.into(),
        }
    }
}

struct EngineRuntime {
    process: Option<Child>,
    status: EngineStatus,
}

impl Default for EngineRuntime {
    fn default() -> Self {
        Self {
            process: None,
            status: EngineStatus::blocked("starting", "正在检测 PhonTracer 分析引擎……"),
        }
    }
}

#[derive(Default)]
struct EngineState(Mutex<EngineRuntime>);

#[derive(Debug, Deserialize)]
struct EngineHandshake {
    event: String,
    port: u16,
    protocol_version: u32,
    engine_version: String,
}

#[derive(Debug, Deserialize)]
struct HealthResponse {
    status: String,
    protocol_version: u32,
    engine_version: String,
}

#[derive(Debug)]
struct EngineLocation {
    executable: PathBuf,
    declared_protocol: Option<u32>,
}

fn validate_protocol(protocol: u32) -> Result<(), String> {
    if protocol == ENGINE_PROTOCOL {
        Ok(())
    } else {
        Err(format!(
            "分析引擎协议不兼容：需要版本 {ENGINE_PROTOCOL}，实际为 {protocol}"
        ))
    }
}

#[cfg(windows)]
fn discover_engine() -> Result<EngineLocation, String> {
    use winreg::enums::{HKEY_CURRENT_USER, HKEY_LOCAL_MACHINE, KEY_READ, KEY_WOW64_64KEY};
    use winreg::RegKey;

    #[cfg(debug_assertions)]
    if let Some(path) = std::env::var_os("PHONTRACER_ENGINE_PATH") {
        let executable = PathBuf::from(path);
        if executable.is_file() {
            return Ok(EngineLocation {
                executable,
                declared_protocol: Some(ENGINE_PROTOCOL),
            });
        }
    }

    for root in [HKEY_CURRENT_USER, HKEY_LOCAL_MACHINE] {
        let root_key = RegKey::predef(root);
        let key = root_key.open_subkey_with_flags(
            r"Software\KasumiKitsune\PhonTracer",
            KEY_READ | KEY_WOW64_64KEY,
        );
        if let Ok(key) = key {
            let install_dir: String = key
                .get_value("InstallDir")
                .map_err(|_| "PhonTracer 安装信息缺少 InstallDir".to_string())?;
            let declared_protocol = key.get_value::<u32, _>("EngineProtocol").ok();
            let executable = PathBuf::from(install_dir).join("PhonTracerAnalysisEngine.exe");
            if executable.is_file() {
                return Ok(EngineLocation {
                    executable,
                    declared_protocol,
                });
            }
            return Err("已检测到 PhonTracer，但安装目录中缺少分析引擎；请更新主程序".into());
        }
    }
    Err("尚未检测到已安装的 PhonTracer".into())
}

#[cfg(target_os = "macos")]
fn discover_engine() -> Result<EngineLocation, String> {
    #[cfg(debug_assertions)]
    if let Some(path) = std::env::var_os("PHONTRACER_ENGINE_PATH") {
        let executable = PathBuf::from(path);
        if executable.is_file() {
            return Ok(EngineLocation {
                executable,
                declared_protocol: Some(ENGINE_PROTOCOL),
            });
        }
    }

    let mut bundles = Vec::new();
    if let Ok(output) = Command::new("/usr/bin/mdfind")
        .arg("kMDItemCFBundleIdentifier == 'com.kasumikitsune.phonetracer'")
        .output()
    {
        if output.status.success() {
            bundles.extend(
                String::from_utf8_lossy(&output.stdout)
                    .lines()
                    .map(PathBuf::from),
            );
        }
    }
    bundles.push(PathBuf::from("/Applications/PhonTracer.app"));
    if let Some(home) = std::env::var_os("HOME") {
        bundles.push(PathBuf::from(home).join("Applications/PhonTracer.app"));
    }

    for bundle in bundles {
        let executable = bundle
            .join("Contents")
            .join("MacOS")
            .join("PhonTracerAnalysisEngine");
        if executable.is_file() {
            return Ok(EngineLocation {
                executable,
                declared_protocol: None,
            });
        }
    }
    Err("尚未检测到已安装的 PhonTracer.app".into())
}

#[cfg(not(any(windows, target_os = "macos")))]
fn discover_engine() -> Result<EngineLocation, String> {
    Err("当前系统不在 PhonRec 支持范围内".into())
}

fn workspace_dir(_app: &AppHandle) -> Result<PathBuf, String> {
    #[cfg(windows)]
    {
        let local =
            std::env::var_os("LOCALAPPDATA").ok_or_else(|| "无法读取 LOCALAPPDATA".to_string())?;
        return Ok(PathBuf::from(local)
            .join("KasumiKitsune")
            .join("PhonRec")
            .join("workspace"));
    }

    #[cfg(target_os = "macos")]
    {
        let home = std::env::var_os("HOME").ok_or_else(|| "无法读取 HOME".to_string())?;
        return Ok(PathBuf::from(home)
            .join("Library")
            .join("Application Support")
            .join("com.kasumikitsune.phonrec")
            .join("workspace"));
    }

    #[allow(unreachable_code)]
    _app.path()
        .app_local_data_dir()
        .map(|path| path.join("workspace"))
        .map_err(|error| format!("无法确定工作区目录：{error}"))
}

fn directory_is_empty(path: &Path) -> bool {
    !path.exists()
        || fs::read_dir(path)
            .map(|mut entries| entries.next().is_none())
            .unwrap_or(true)
}

fn copy_directory(source: &Path, destination: &Path) -> Result<(), String> {
    fs::create_dir_all(destination).map_err(|error| format!("创建迁移目录失败：{error}"))?;
    for entry in fs::read_dir(source).map_err(|error| format!("读取旧工作区失败：{error}"))?
    {
        let entry = entry.map_err(|error| format!("读取旧工作区条目失败：{error}"))?;
        let file_type = entry
            .file_type()
            .map_err(|error| format!("读取旧工作区条目类型失败：{error}"))?;
        let target = destination.join(entry.file_name());
        if file_type.is_symlink() {
            continue;
        }
        if file_type.is_dir() {
            copy_directory(&entry.path(), &target)?;
        } else if file_type.is_file() {
            fs::copy(entry.path(), target)
                .map_err(|error| format!("复制旧工作区文件失败：{error}"))?;
        }
    }
    Ok(())
}

fn legacy_workspace_candidates() -> Vec<PathBuf> {
    let mut candidates = Vec::new();
    #[cfg(windows)]
    {
        if let Some(local) = std::env::var_os("LOCALAPPDATA") {
            let programs = PathBuf::from(local).join("Programs");
            for name in ["phonrec", "PhonRec"] {
                candidates.push(
                    programs
                        .join(name)
                        .join("resources")
                        .join("phonrec-backend")
                        .join("workspace"),
                );
            }
        }
        if let Some(program_files) = std::env::var_os("ProgramFiles") {
            candidates.push(
                PathBuf::from(program_files)
                    .join("PhonRec")
                    .join("resources")
                    .join("phonrec-backend")
                    .join("workspace"),
            );
        }
    }
    #[cfg(target_os = "macos")]
    {
        candidates.push(
            PathBuf::from("/Applications/PhonRec.app")
                .join("Contents")
                .join("Resources")
                .join("phonrec-backend")
                .join("workspace"),
        );
    }
    candidates
}

fn migrate_legacy_workspace(destination: &Path) -> Result<(), String> {
    if !directory_is_empty(destination) {
        return Ok(());
    }
    for candidate in legacy_workspace_candidates() {
        if candidate.is_dir() && !directory_is_empty(&candidate) {
            copy_directory(&candidate, destination)?;
            break;
        }
    }
    Ok(())
}

fn is_windows_reserved_component(value: &str) -> bool {
    let stem = value
        .split('.')
        .next()
        .unwrap_or_default()
        .to_ascii_uppercase();
    matches!(
        stem.as_str(),
        "CON"
            | "PRN"
            | "AUX"
            | "NUL"
            | "COM1"
            | "COM2"
            | "COM3"
            | "COM4"
            | "COM5"
            | "COM6"
            | "COM7"
            | "COM8"
            | "COM9"
            | "LPT1"
            | "LPT2"
            | "LPT3"
            | "LPT4"
            | "LPT5"
            | "LPT6"
            | "LPT7"
            | "LPT8"
            | "LPT9"
    )
}

fn stable_path_hash(value: &str) -> String {
    let mut hash = 0xcbf29ce484222325u64;
    for byte in value.as_bytes() {
        hash ^= u64::from(*byte);
        hash = hash.wrapping_mul(0x100000001b3);
    }
    format!("{:010x}", hash & 0xffffffffff)
}

fn sanitize_path_component(value: &str, fallback: &str) -> String {
    const MAX_CHARS: usize = 64;
    let raw = value.trim();
    let mut sanitized = String::with_capacity(raw.len());
    for character in raw.chars() {
        if character.is_control()
            || matches!(
                character,
                '<' | '>' | ':' | '"' | '/' | '\\' | '|' | '?' | '*'
            )
        {
            sanitized.push('_');
        } else {
            sanitized.push(character);
        }
    }
    let mut sanitized = sanitized
        .trim_matches(|character| character == ' ' || character == '.')
        .to_string();
    if sanitized.is_empty() {
        sanitized = fallback.to_string();
    }
    if is_windows_reserved_component(&sanitized) {
        sanitized.insert(0, '_');
    }
    if sanitized != raw || sanitized.chars().count() > MAX_CHARS {
        let digest = stable_path_hash(raw);
        let prefix: String = sanitized
            .chars()
            .take(MAX_CHARS - digest.len() - 1)
            .collect();
        sanitized = format!(
            "{}_{}",
            prefix.trim_end_matches(|character| character == ' ' || character == '.'),
            digest
        );
    }
    sanitized
}

fn sanitize_display_path_component(value: &str, fallback: &str) -> String {
    let mut sanitized: String = value
        .trim()
        .chars()
        .map(|character| {
            if character.is_control()
                || matches!(
                    character,
                    '<' | '>' | ':' | '"' | '/' | '\\' | '|' | '?' | '*'
                )
            {
                '_'
            } else {
                character
            }
        })
        .collect();
    sanitized = sanitized
        .trim_matches(|character| character == ' ' || character == '.')
        .chars()
        .take(64)
        .collect();
    if sanitized.is_empty() {
        sanitized = fallback.to_string();
    }
    if is_windows_reserved_component(&sanitized) {
        sanitized.insert(0, '_');
    }
    sanitized
}

fn resolve_managed_workspace_path(workspace: &Path, relative: &str) -> Result<PathBuf, String> {
    let normalized = relative.replace('\\', "/");
    let mut resolved = workspace.to_path_buf();
    for component in normalized.split('/') {
        if component.is_empty() || component == "." {
            continue;
        }
        if component == ".." || component.contains(':') {
            return Err("工作区资源路径不安全".to_string());
        }
        resolved.push(component);
    }
    if resolved == workspace {
        return Err("工作区资源路径为空".to_string());
    }
    Ok(resolved)
}

fn ensure_managed_parent(workspace: &Path, path: &Path) -> Result<(), String> {
    let parent = path
        .parent()
        .ok_or_else(|| "目标路径缺少父目录".to_string())?;
    fs::create_dir_all(parent).map_err(|error| format!("创建工作区目录失败：{error}"))?;
    let canonical_workspace = workspace
        .canonicalize()
        .map_err(|error| format!("验证工作区失败：{error}"))?;
    let canonical_parent = parent
        .canonicalize()
        .map_err(|error| format!("验证工作区子目录失败：{error}"))?;
    if !canonical_parent.starts_with(&canonical_workspace) {
        return Err("工作区资源路径越界".to_string());
    }
    Ok(())
}

fn resolve_existing_managed_file(workspace: &Path, relative: &str) -> Result<PathBuf, String> {
    let path = resolve_managed_workspace_path(workspace, relative)?;
    let canonical_workspace = workspace
        .canonicalize()
        .map_err(|error| format!("验证工作区失败：{error}"))?;
    let canonical_path = path
        .canonicalize()
        .map_err(|error| format!("读取工作区资源失败：{error}"))?;
    if !canonical_path.starts_with(&canonical_workspace) || !canonical_path.is_file() {
        return Err("工作区资源路径越界或不是文件".to_string());
    }
    Ok(canonical_path)
}

fn write_bytes_atomic(path: &Path, bytes: &[u8]) -> Result<(), String> {
    let parent = path
        .parent()
        .ok_or_else(|| "目标路径缺少父目录".to_string())?;
    fs::create_dir_all(parent).map_err(|error| format!("创建目录失败：{error}"))?;
    let temp_path = path.with_extension("tmp");
    let backup_path = path.with_extension("bak");
    let mut file =
        fs::File::create(&temp_path).map_err(|error| format!("创建临时文件失败：{error}"))?;
    file.write_all(bytes)
        .map_err(|error| format!("写入临时文件失败：{error}"))?;
    file.sync_all()
        .map_err(|error| format!("同步临时文件失败：{error}"))?;
    if backup_path.exists() {
        fs::remove_file(&backup_path).map_err(|error| format!("清理旧备份失败：{error}"))?;
    }
    if path.exists() {
        fs::rename(path, &backup_path).map_err(|error| format!("备份旧文件失败：{error}"))?;
    }
    if let Err(error) = fs::rename(&temp_path, path) {
        if backup_path.exists() {
            let _ = fs::rename(&backup_path, path);
        }
        let _ = fs::remove_file(&temp_path);
        return Err(format!("提交文件失败：{error}"));
    }
    if backup_path.exists() {
        fs::remove_file(&backup_path).map_err(|error| format!("清理文件备份失败：{error}"))?;
    }
    Ok(())
}

fn write_project_state_atomic(workspace: &Path, state: &Value) -> Result<(), String> {
    if !state.is_object() {
        return Err("工程状态必须是 JSON 对象".to_string());
    }
    let bytes =
        serde_json::to_vec_pretty(state).map_err(|error| format!("序列化工程状态失败：{error}"))?;
    write_bytes_atomic(&workspace.join("project.json"), &bytes)
}

fn referenced_audio_paths(state: &Value, workspace: &Path) -> HashSet<PathBuf> {
    let mut referenced = HashSet::new();
    if let Some(speakers) = state.get("speakers").and_then(Value::as_object) {
        for speaker in speakers.values() {
            if let Some(items) = speaker.get("items").and_then(Value::as_object) {
                for item in items.values() {
                    if let Some(relative) = item.get("path").and_then(Value::as_str) {
                        if let Ok(path) = resolve_managed_workspace_path(workspace, relative) {
                            referenced.insert(path);
                        }
                    }
                }
            }
        }
    }
    referenced
}

fn prune_unreferenced_audio(state: &Value, workspace: &Path) -> Result<(), String> {
    let audio_root = workspace.join("audio");
    if !audio_root.exists() {
        return Ok(());
    }
    let referenced = referenced_audio_paths(state, workspace);
    for speaker_entry in
        fs::read_dir(&audio_root).map_err(|error| format!("读取录音目录失败：{error}"))?
    {
        let speaker_entry = speaker_entry.map_err(|error| format!("读取录音条目失败：{error}"))?;
        if speaker_entry
            .file_type()
            .map_err(|error| format!("读取录音条目类型失败：{error}"))?
            .is_symlink()
        {
            continue;
        }
        let speaker_path = speaker_entry.path();
        if speaker_path.is_dir() {
            for audio_entry in fs::read_dir(&speaker_path)
                .map_err(|error| format!("读取发音人录音目录失败：{error}"))?
            {
                let audio_entry =
                    audio_entry.map_err(|error| format!("读取录音文件失败：{error}"))?;
                if audio_entry
                    .file_type()
                    .map_err(|error| format!("读取录音文件类型失败：{error}"))?
                    .is_symlink()
                {
                    continue;
                }
                let audio_path = audio_entry.path();
                if audio_path.is_file() && !referenced.contains(&audio_path) {
                    fs::remove_file(&audio_path)
                        .map_err(|error| format!("清理无引用录音失败：{error}"))?;
                }
            }
            if directory_is_empty(&speaker_path) {
                fs::remove_dir(&speaker_path)
                    .map_err(|error| format!("清理空录音目录失败：{error}"))?;
            }
        }
    }
    Ok(())
}

#[derive(Debug)]
struct ParsedWav {
    sample_rate: u32,
    channels: u16,
    samples: Vec<f32>,
}

fn read_u16_le(bytes: &[u8], offset: usize) -> Result<u16, String> {
    let value = bytes
        .get(offset..offset + 2)
        .ok_or_else(|| "WAV 数据不完整".to_string())?;
    Ok(u16::from_le_bytes([value[0], value[1]]))
}

fn read_u32_le(bytes: &[u8], offset: usize) -> Result<u32, String> {
    let value = bytes
        .get(offset..offset + 4)
        .ok_or_else(|| "WAV 数据不完整".to_string())?;
    Ok(u32::from_le_bytes([value[0], value[1], value[2], value[3]]))
}

fn parse_pcm_wav(bytes: &[u8]) -> Result<ParsedWav, String> {
    if bytes.len() < 44 || bytes.get(0..4) != Some(b"RIFF") || bytes.get(8..12) != Some(b"WAVE") {
        return Err("仅支持标准 PCM WAV 录音".to_string());
    }
    let mut offset = 12usize;
    let mut format = None;
    let mut data = None;
    while offset + 8 <= bytes.len() {
        let chunk_id = &bytes[offset..offset + 4];
        let chunk_size = read_u32_le(bytes, offset + 4)? as usize;
        let chunk_start = offset + 8;
        let chunk_end = chunk_start
            .checked_add(chunk_size)
            .ok_or_else(|| "WAV 分块长度无效".to_string())?;
        if chunk_end > bytes.len() {
            return Err("WAV 分块超出文件范围".to_string());
        }
        if chunk_id == b"fmt " && chunk_size >= 16 {
            format = Some((
                read_u16_le(bytes, chunk_start)?,
                read_u16_le(bytes, chunk_start + 2)?,
                read_u32_le(bytes, chunk_start + 4)?,
                read_u16_le(bytes, chunk_start + 14)?,
            ));
        } else if chunk_id == b"data" {
            data = Some(&bytes[chunk_start..chunk_end]);
        }
        offset = chunk_end + (chunk_size % 2);
    }
    let (audio_format, channels, sample_rate, bits_per_sample) =
        format.ok_or_else(|| "WAV 缺少 fmt 分块".to_string())?;
    let data = data.ok_or_else(|| "WAV 缺少 data 分块".to_string())?;
    if audio_format != 1 || bits_per_sample != 16 || channels == 0 || sample_rate == 0 {
        return Err("独立模式仅支持 16 位 PCM WAV".to_string());
    }
    let frame_bytes = channels as usize * 2;
    if data.len() % frame_bytes != 0 {
        return Err("WAV 采样数据长度无效".to_string());
    }
    let frame_count = data.len() / frame_bytes;
    if frame_count > sample_rate as usize * 60 * 10 {
        return Err("录音时长超过独立模式限制".to_string());
    }
    let mut samples = Vec::with_capacity(frame_count);
    for frame in data.chunks_exact(frame_bytes) {
        let mut sum = 0.0f32;
        for channel in 0..channels as usize {
            let start = channel * 2;
            let sample = i16::from_le_bytes([frame[start], frame[start + 1]]);
            sum += sample as f32 / 32768.0;
        }
        samples.push(sum / channels as f32);
    }
    Ok(ParsedWav {
        sample_rate,
        channels,
        samples,
    })
}

fn quality_rule<'a>(rules: &'a Value, name: &str) -> (bool, &'a str) {
    let rule = rules.get(name);
    let enabled = rule
        .and_then(|value| value.get("enabled"))
        .and_then(Value::as_bool)
        .unwrap_or(true);
    let level = rule
        .and_then(|value| value.get("level"))
        .and_then(Value::as_str)
        .unwrap_or("medium");
    let level = if matches!(level, "low" | "medium" | "high") {
        level
    } else {
        "medium"
    };
    (enabled, level)
}

fn analyze_lightweight_quality(wav: &ParsedWav, rules: &Value) -> Value {
    let (volume_enabled, volume_level) = quality_rule(rules, "volume");
    let (clipping_enabled, clipping_level) = quality_rule(rules, "clipping");
    let frame_size = ((wav.sample_rate as f64 * 0.02).round() as usize).max(1);
    let mut active_rms = Vec::new();
    for frame in wav.samples.chunks(frame_size) {
        if frame.is_empty() {
            continue;
        }
        let square_sum: f64 = frame
            .iter()
            .map(|sample| (*sample as f64) * (*sample as f64))
            .sum();
        let rms = (square_sum / frame.len() as f64).sqrt();
        let db = 20.0 * (rms + 1e-10).log10();
        if db > -45.0 {
            active_rms.push(rms);
        }
    }
    let average_rms = if active_rms.is_empty() {
        0.0
    } else {
        active_rms.iter().sum::<f64>() / active_rms.len() as f64
    };
    let volume_db = if average_rms > 0.0 {
        20.0 * (average_rms + 1e-10).log10()
    } else {
        -100.0
    };
    let peak = wav
        .samples
        .iter()
        .map(|sample| sample.abs() as f64)
        .fold(0.0, f64::max);
    let peak_db = 20.0 * (peak + 1e-10).log10();
    let clipped_count = wav
        .samples
        .iter()
        .filter(|sample| sample.abs() >= 0.99)
        .count();
    let clipping_ratio = if wav.samples.is_empty() {
        0.0
    } else {
        clipped_count as f64 / wav.samples.len() as f64
    };

    let (quiet_threshold, loud_threshold) = match volume_level {
        "low" => (-40.0, -3.0),
        "high" => (-30.0, -9.0),
        _ => (-35.0, -6.0),
    };
    let (clip_review, clip_retry) = match clipping_level {
        "low" => (0.003, 0.01),
        "high" => (0.0, 0.001),
        _ => (0.0001, 0.003),
    };
    let too_quiet = volume_db < quiet_threshold;
    let too_loud = volume_db > loud_threshold;
    let clipped = clipping_ratio > clip_review;
    let severe_clipping = clipping_ratio >= clip_retry;
    let mut retry_issues = Vec::new();
    let mut review_issues = Vec::new();
    if volume_enabled && too_quiet {
        retry_issues.push("有效音量过小");
    }
    if volume_enabled && too_loud {
        retry_issues.push("有效音量过大");
    }
    if clipping_enabled && severe_clipping {
        retry_issues.push("严重截断");
    } else if clipping_enabled && clipped {
        review_issues.push("轻微截断");
    }
    let decision = if !retry_issues.is_empty() {
        "retry"
    } else if !review_issues.is_empty() {
        "review"
    } else {
        "accept"
    };
    let mut issues = retry_issues.clone();
    issues.extend(review_issues.iter().copied());
    let score = if !retry_issues.is_empty() {
        55
    } else {
        100usize.saturating_sub(review_issues.len() * 8)
    };
    let volume_status = if too_quiet {
        "too_quiet"
    } else if too_loud {
        "too_loud"
    } else {
        "normal"
    };
    let unavailable =
        |label: &str| json!({"enabled": false, "abnormal": false, "score": 0.0, "label": label});
    json!({
        "decision": decision,
        "grade": if decision == "retry" { "需重录" } else if decision == "review" { "建议复核" } else { "良好" },
        "score": score,
        "issues": issues,
        "recommendations": issues,
        "config": rules,
        "volume": {
            "enabled": volume_enabled,
            "status": if volume_enabled { volume_status } else { "disabled" },
            "score": volume_db,
            "label": if !volume_enabled { "未启用" } else if too_quiet { "音量过小" } else if too_loud { "音量过大" } else { "正常" }
        },
        "clipping": {
            "enabled": clipping_enabled,
            "abnormal": clipping_enabled && clipped,
            "score": clipping_ratio,
            "label": if !clipping_enabled { "未启用" } else if clipped { "音频截断" } else { "正常" }
        },
        "speech": unavailable("完整模式可用"),
        "noise": unavailable("完整模式可用"),
        "creak": unavailable("完整模式可用"),
        "dc_offset": unavailable("完整模式可用"),
        "metrics": {
            "duration_ms": if wav.sample_rate > 0 { wav.samples.len() as u64 * 1000 / wav.sample_rate as u64 } else { 0 },
            "active_rms_dbfs": volume_db,
            "peak_dbfs": peak_db,
            "clipping_ratio": clipping_ratio,
            "speech_ratio": 0.0,
            "snr_db": 0.0
        }
    })
}

#[derive(Serialize)]
struct StandaloneAudioResult {
    status: String,
    stored: bool,
    path: String,
    quality: Value,
    spectrogram: Option<String>,
    recorded_at: String,
    duration_ms: u64,
    sample_rate_hz: u32,
    channels: u16,
    format: String,
    source: String,
}

#[derive(Serialize)]
struct StandaloneExportResult {
    output_dir: String,
    exported: usize,
    skipped: usize,
}

#[tauri::command]
fn standalone_project_load(app: AppHandle) -> Result<Value, String> {
    let workspace = workspace_dir(&app)?;
    fs::create_dir_all(workspace.join("audio"))
        .map_err(|error| format!("创建独立工作区失败：{error}"))?;
    let path = workspace.join("project.json");
    if !path.exists() {
        return Ok(json!({"version": "1.0", "speakers": {}, "groups": []}));
    }
    let bytes = fs::read(&path).map_err(|error| format!("读取独立工作区失败：{error}"))?;
    serde_json::from_slice(&bytes).map_err(|error| format!("解析独立工作区失败：{error}"))
}

#[tauri::command]
fn standalone_project_save(app: AppHandle, state: Value) -> Result<Value, String> {
    let workspace = workspace_dir(&app)?;
    fs::create_dir_all(workspace.join("audio"))
        .map_err(|error| format!("创建独立工作区失败：{error}"))?;
    write_project_state_atomic(&workspace, &state)?;
    prune_unreferenced_audio(&state, &workspace)?;
    Ok(state)
}

#[tauri::command]
fn standalone_project_clear(app: AppHandle) -> Result<(), String> {
    let workspace = workspace_dir(&app)?;
    if workspace.exists() {
        fs::remove_dir_all(&workspace).map_err(|error| format!("清空独立工作区失败：{error}"))?;
    }
    fs::create_dir_all(workspace.join("audio"))
        .map_err(|error| format!("重建独立工作区失败：{error}"))
}

fn compatible_text(primary: Option<&Value>, legacy: Option<&Value>) -> Value {
    for candidate in [primary, legacy].into_iter().flatten() {
        if candidate
            .as_str()
            .map(|value| !value.is_empty())
            .unwrap_or(false)
        {
            return candidate.clone();
        }
    }
    json!("")
}

fn merge_compatible_arrays(primary: Option<&Value>, legacy: Option<&Value>) -> Value {
    let mut merged = Vec::new();
    for candidate in [primary, legacy].into_iter().flatten() {
        if let Some(values) = candidate.as_array() {
            for value in values {
                if !merged.contains(value) {
                    merged.push(value.clone());
                }
            }
        }
    }
    Value::Array(merged)
}

fn merge_compatible_objects(primary: Option<&Value>, legacy: Option<&Value>) -> Value {
    let mut merged = serde_json::Map::new();
    for candidate in [primary, legacy].into_iter().flatten() {
        if let Some(values) = candidate.as_object() {
            for (key, value) in values {
                merged.insert(key.clone(), value.clone());
            }
        }
    }
    Value::Object(merged)
}

fn ensure_project_groups(state: &mut Value) -> Result<(), String> {
    let has_groups = state
        .get("groups")
        .and_then(Value::as_array)
        .map(|groups| {
            groups.iter().any(|group| {
                group
                    .get("items")
                    .and_then(Value::as_array)
                    .map(|items| !items.is_empty())
                    .unwrap_or(false)
            })
        })
        .unwrap_or(false);
    if has_groups {
        return Ok(());
    }

    let speakers = state
        .get("speakers")
        .and_then(Value::as_object)
        .cloned()
        .ok_or_else(|| "工程缺少发音人数据".to_string())?;
    let mut speaker_ids: Vec<String> = speakers.keys().cloned().collect();
    if let Some(active) = state.get("active_speaker_id").and_then(Value::as_str) {
        if let Some(index) = speaker_ids.iter().position(|id| id == active) {
            let active_id = speaker_ids.remove(index);
            speaker_ids.insert(0, active_id);
        }
    }

    let mut groups: Vec<Value> = Vec::new();
    let mut group_indices: HashMap<String, usize> = HashMap::new();
    let mut used_item_ids = HashSet::new();
    let mut slot_map: HashMap<(String, String, usize), (usize, usize, String)> = HashMap::new();
    let mut speaker_remaps: HashMap<String, HashMap<String, String>> = HashMap::new();
    for speaker_id in &speaker_ids {
        let Some(items) = speakers
            .get(speaker_id)
            .and_then(|speaker| speaker.get("items"))
            .and_then(Value::as_object)
        else {
            continue;
        };
        let mut occurrences: HashMap<(String, String), usize> = HashMap::new();
        let mut remap = HashMap::new();
        for (item_key, item) in items {
            let label = item
                .get("label")
                .or_else(|| item.get("word"))
                .and_then(Value::as_str)
                .unwrap_or("未命名")
                .to_string();
            let group_name = item
                .get("group")
                .or_else(|| item.get("group_name"))
                .and_then(Value::as_str)
                .unwrap_or("默认组")
                .to_string();
            let occurrence = occurrences
                .entry((group_name.clone(), label.clone()))
                .or_insert(0);
            let slot = (group_name.clone(), label.clone(), *occurrence);
            *occurrence += 1;
            let group_index = *group_indices.entry(group_name.clone()).or_insert_with(|| {
                groups.push(json!({
                    "id": format!("grp_{}", Uuid::new_v4().simple()),
                    "name": group_name,
                    "note": item.get("group_note").cloned().unwrap_or_else(|| json!("")),
                    "tags": item.get("group_tags").cloned().unwrap_or_else(|| json!([])),
                    "meta": item.get("group_meta").cloned().unwrap_or_else(|| json!({})),
                    "items": []
                }));
                groups.len() - 1
            });
            if let Some(group) = groups[group_index].as_object_mut() {
                if group
                    .get("note")
                    .and_then(Value::as_str)
                    .map(|value| value.is_empty())
                    .unwrap_or(true)
                {
                    group.insert(
                        "note".to_string(),
                        compatible_text(item.get("group_note"), None),
                    );
                }
                let merged_tags =
                    merge_compatible_arrays(group.get("tags"), item.get("group_tags"));
                group.insert("tags".to_string(), merged_tags);
                let merged_meta =
                    merge_compatible_objects(group.get("meta"), item.get("group_meta"));
                group.insert("meta".to_string(), merged_meta);
            }
            let canonical_id = if let Some((existing_group, item_index, canonical_id)) =
                slot_map.get(&slot).cloned()
            {
                let canonical = groups[existing_group]
                    .get_mut("items")
                    .and_then(Value::as_array_mut)
                    .and_then(|values| values.get_mut(item_index))
                    .and_then(Value::as_object_mut)
                    .expect("规范词项必须是对象");
                let source_note = compatible_text(item.get("note"), item.get("item_note"));
                canonical.insert(
                    "note".to_string(),
                    compatible_text(canonical.get("note"), Some(&source_note)),
                );
                let source_tags = merge_compatible_arrays(item.get("tags"), item.get("item_tags"));
                let tags = merge_compatible_arrays(canonical.get("tags"), Some(&source_tags));
                canonical.insert("tags".to_string(), tags);
                let source_aliases =
                    merge_compatible_arrays(item.get("aliases"), item.get("item_aliases"));
                let aliases =
                    merge_compatible_arrays(canonical.get("aliases"), Some(&source_aliases));
                canonical.insert("aliases".to_string(), aliases);
                let source_meta = merge_compatible_objects(item.get("meta"), item.get("item_meta"));
                let meta = merge_compatible_objects(canonical.get("meta"), Some(&source_meta));
                canonical.insert("meta".to_string(), meta);
                canonical_id
            } else {
                let requested_id = item.get("id").and_then(Value::as_str).unwrap_or(item_key);
                let canonical_id = if used_item_ids.insert(requested_id.to_string()) {
                    requested_id.to_string()
                } else {
                    let generated = format!("item_{}", Uuid::new_v4().simple());
                    used_item_ids.insert(generated.clone());
                    generated
                };
                let canonical = json!({
                    "id": canonical_id,
                    "label": label,
                    "note": compatible_text(item.get("note"), item.get("item_note")),
                    "tags": merge_compatible_arrays(item.get("tags"), item.get("item_tags")),
                    "aliases": merge_compatible_arrays(item.get("aliases"), item.get("item_aliases")),
                    "meta": merge_compatible_objects(item.get("meta"), item.get("item_meta")),
                    "metadata_source": item.get("metadata_source").cloned().unwrap_or_else(|| json!("导入工程"))
                });
                let group_items = groups[group_index]
                    .get_mut("items")
                    .and_then(Value::as_array_mut)
                    .expect("新建分组必须包含 items");
                let item_index = group_items.len();
                group_items.push(canonical);
                slot_map.insert(slot, (group_index, item_index, canonical_id.clone()));
                canonical_id
            };
            remap.insert(item_key.clone(), canonical_id);
        }
        speaker_remaps.insert(speaker_id.clone(), remap);
    }

    let mut canonical_by_id: HashMap<String, (Value, String, Value, Value, Value)> = HashMap::new();
    for group in &groups {
        let group_name = group
            .get("name")
            .and_then(Value::as_str)
            .unwrap_or("默认组")
            .to_string();
        let group_note = group.get("note").cloned().unwrap_or_else(|| json!(""));
        let group_tags = group.get("tags").cloned().unwrap_or_else(|| json!([]));
        let group_meta = group.get("meta").cloned().unwrap_or_else(|| json!({}));
        if let Some(items) = group.get("items").and_then(Value::as_array) {
            for item in items {
                if let Some(item_id) = item.get("id").and_then(Value::as_str) {
                    canonical_by_id.insert(
                        item_id.to_string(),
                        (
                            item.clone(),
                            group_name.clone(),
                            group_note.clone(),
                            group_tags.clone(),
                            group_meta.clone(),
                        ),
                    );
                }
            }
        }
    }

    let mut updated_speakers = speakers.clone();
    for (speaker_id, speaker) in &mut updated_speakers {
        let Some(original_items) = speakers
            .get(speaker_id)
            .and_then(|value| value.get("items"))
            .and_then(Value::as_object)
        else {
            continue;
        };
        let remap = speaker_remaps.get(speaker_id).cloned().unwrap_or_default();
        let mut normalized_items = serde_json::Map::new();
        for (old_id, original_item) in original_items {
            let Some(canonical_id) = remap.get(old_id) else {
                continue;
            };
            let Some((canonical, group_name, group_note, group_tags, group_meta)) =
                canonical_by_id.get(canonical_id)
            else {
                continue;
            };
            let mut normalized = original_item.as_object().cloned().unwrap_or_default();
            let canonical_object = canonical.as_object().expect("规范词项必须是对象");
            for key in [
                "id",
                "label",
                "note",
                "tags",
                "aliases",
                "meta",
                "metadata_source",
            ] {
                if let Some(value) = canonical_object.get(key) {
                    normalized.insert(key.to_string(), value.clone());
                }
            }
            normalized.insert("item_note".to_string(), canonical["note"].clone());
            normalized.insert("item_tags".to_string(), canonical["tags"].clone());
            normalized.insert("item_aliases".to_string(), canonical["aliases"].clone());
            normalized.insert("item_meta".to_string(), canonical["meta"].clone());
            normalized.insert("group".to_string(), Value::String(group_name.clone()));
            normalized.insert("group_note".to_string(), group_note.clone());
            normalized.insert("group_tags".to_string(), group_tags.clone());
            normalized.insert("group_meta".to_string(), group_meta.clone());
            normalized_items.insert(canonical_id.clone(), Value::Object(normalized));
        }
        if let Some(speaker_object) = speaker.as_object_mut() {
            speaker_object.insert("items".to_string(), Value::Object(normalized_items));
        }
    }
    state["speakers"] = Value::Object(updated_speakers);
    state["groups"] = Value::Array(groups);
    Ok(())
}

fn archive_relative_path(name: &str) -> Result<PathBuf, String> {
    let normalized = name.replace('\\', "/");
    if normalized.is_empty() || normalized.starts_with('/') {
        return Err("工程归档包含非法路径".to_string());
    }
    let mut path = PathBuf::new();
    for component in normalized.split('/') {
        if component.is_empty() || component == "." {
            continue;
        }
        if component == ".." {
            return Err("工程归档包含路径穿越".to_string());
        }
        if component.chars().any(|character| {
            character.is_control() || matches!(character, '<' | '>' | ':' | '"' | '|' | '?' | '*')
        }) || component.ends_with(' ')
            || component.ends_with('.')
            || is_windows_reserved_component(component)
        {
            return Err("工程归档包含跨平台不兼容路径".to_string());
        }
        path.push(component);
    }
    if path.as_os_str().is_empty() {
        return Err("工程归档包含空路径".to_string());
    }
    Ok(path)
}

fn supports_project_version(state: &Value) -> bool {
    match state.get("version") {
        None => true,
        Some(Value::String(version)) => version == "1.0",
        Some(Value::Number(version)) => version.as_f64() == Some(1.0),
        _ => false,
    }
}

fn portable_archive_key(path: &Path) -> String {
    path.to_string_lossy().replace('\\', "/").to_lowercase()
}

#[tauri::command]
fn standalone_project_import(app: AppHandle, archive_bytes: Vec<u8>) -> Result<Value, String> {
    const MAX_MEMBERS: usize = 10_000;
    const MAX_MEMBER_BYTES: u64 = 2 * 1024 * 1024 * 1024;
    const MAX_TOTAL_BYTES: u64 = 20 * 1024 * 1024 * 1024;
    let workspace = workspace_dir(&app)?;
    let parent = workspace
        .parent()
        .ok_or_else(|| "工作区缺少父目录".to_string())?;
    fs::create_dir_all(parent).map_err(|error| format!("创建工作区父目录失败：{error}"))?;
    let staging = parent.join(format!("workspace_import_{}", Uuid::new_v4().simple()));
    let backup = parent.join(format!("workspace_backup_{}", Uuid::new_v4().simple()));
    fs::create_dir_all(&staging).map_err(|error| format!("创建工程暂存区失败：{error}"))?;

    let result = (|| -> Result<Value, String> {
        let cursor = Cursor::new(archive_bytes);
        let mut archive =
            zip::ZipArchive::new(cursor).map_err(|error| format!("工程不是有效 ZIP：{error}"))?;
        if archive.len() > MAX_MEMBERS {
            return Err("工程归档成员过多".to_string());
        }
        let mut total_size = 0u64;
        let mut seen = HashSet::new();
        for index in 0..archive.len() {
            let mut entry = archive
                .by_index(index)
                .map_err(|error| format!("读取工程成员失败：{error}"))?;
            if entry.size() > MAX_MEMBER_BYTES {
                return Err(format!("工程成员过大：{}", entry.name()));
            }
            total_size = total_size.saturating_add(entry.size());
            if total_size > MAX_TOTAL_BYTES {
                return Err("工程解压后总大小超过限制".to_string());
            }
            if entry
                .unix_mode()
                .map(|mode| mode & 0o170000 == 0o120000)
                .unwrap_or(false)
            {
                return Err("工程归档不能包含符号链接".to_string());
            }
            let relative = archive_relative_path(entry.name())?;
            if !seen.insert(portable_archive_key(&relative)) {
                return Err(format!("工程归档包含重复成员：{}", entry.name()));
            }
            let target = staging.join(&relative);
            if entry.is_dir() {
                fs::create_dir_all(&target)
                    .map_err(|error| format!("创建工程目录失败：{error}"))?;
            } else {
                if let Some(parent) = target.parent() {
                    fs::create_dir_all(parent)
                        .map_err(|error| format!("创建工程目录失败：{error}"))?;
                }
                let mut output = fs::File::create(&target)
                    .map_err(|error| format!("创建工程文件失败：{error}"))?;
                std::io::copy(&mut entry, &mut output)
                    .map_err(|error| format!("解压工程文件失败：{error}"))?;
                output
                    .sync_all()
                    .map_err(|error| format!("同步工程文件失败：{error}"))?;
            }
        }

        let project_path = staging.join("project.json");
        let mut state: Value = serde_json::from_slice(
            &fs::read(&project_path).map_err(|_| "工程缺少 project.json".to_string())?,
        )
        .map_err(|error| format!("project.json 无效：{error}"))?;
        if !state.is_object() || !supports_project_version(&state) {
            return Err("工程版本不受支持".to_string());
        }
        ensure_project_groups(&mut state)?;
        if let Some(speakers) = state.get("speakers").and_then(Value::as_object) {
            for speaker in speakers.values() {
                if let Some(items) = speaker.get("items").and_then(Value::as_object) {
                    for item in items.values() {
                        if let Some(relative) = item.get("path").and_then(Value::as_str) {
                            resolve_existing_managed_file(&staging, relative)?;
                        }
                    }
                }
            }
        }
        write_project_state_atomic(&staging, &state)?;

        if workspace.exists() {
            fs::rename(&workspace, &backup)
                .map_err(|error| format!("备份当前工作区失败：{error}"))?;
        }
        if let Err(error) = fs::rename(&staging, &workspace) {
            if backup.exists() {
                let _ = fs::rename(&backup, &workspace);
            }
            return Err(format!("提交导入工程失败：{error}"));
        }
        if backup.exists() {
            fs::remove_dir_all(&backup).map_err(|error| format!("清理旧工作区失败：{error}"))?;
        }
        Ok(json!({
            "status": "success",
            "state": state,
            "warnings": [],
            "summary": {"merged_speakers": 0, "sliced_items": 0, "missing_items": 0, "downgraded_items": 0}
        }))
    })();

    if staging.exists() {
        let _ = fs::remove_dir_all(&staging);
    }
    if result.is_err() && backup.exists() && !workspace.exists() {
        let _ = fs::rename(&backup, &workspace);
    }
    result
}

fn add_archive_directory(
    writer: &mut zip::ZipWriter<Cursor<Vec<u8>>>,
    root: &Path,
    current: &Path,
) -> Result<(), String> {
    for entry in fs::read_dir(current).map_err(|error| format!("读取工作区失败：{error}"))?
    {
        let entry = entry.map_err(|error| format!("读取工作区条目失败：{error}"))?;
        let file_type = entry
            .file_type()
            .map_err(|error| format!("读取工作区条目类型失败：{error}"))?;
        if file_type.is_symlink() {
            continue;
        }
        let path = entry.path();
        if file_type.is_dir() {
            add_archive_directory(writer, root, &path)?;
        } else if file_type.is_file() {
            let file_name = entry.file_name().to_string_lossy().to_string();
            if file_name.ends_with(".tmp") || file_name.ends_with(".bak") {
                continue;
            }
            let relative = path
                .strip_prefix(root)
                .map_err(|_| "工作区资源路径越界".to_string())?
                .to_string_lossy()
                .replace('\\', "/");
            writer
                .start_file(
                    relative,
                    SimpleFileOptions::default()
                        .compression_method(zip::CompressionMethod::Deflated),
                )
                .map_err(|error| format!("创建工程成员失败：{error}"))?;
            let mut source =
                fs::File::open(&path).map_err(|error| format!("读取工作区资源失败：{error}"))?;
            std::io::copy(&mut source, writer)
                .map_err(|error| format!("写入工程成员失败：{error}"))?;
        }
    }
    Ok(())
}

#[tauri::command]
fn standalone_project_export(app: AppHandle) -> Result<Vec<u8>, String> {
    let workspace = workspace_dir(&app)?;
    let state = standalone_project_load(app)?;
    if state
        .get("speakers")
        .and_then(Value::as_object)
        .map(|value| value.is_empty())
        .unwrap_or(true)
    {
        return Err("未添加发音人，无法导出工程".to_string());
    }
    let cursor = Cursor::new(Vec::new());
    let mut writer = zip::ZipWriter::new(cursor);
    add_archive_directory(&mut writer, &workspace, &workspace)?;
    writer
        .finish()
        .map(|cursor| cursor.into_inner())
        .map_err(|error| format!("完成工程归档失败：{error}"))
}

#[tauri::command]
fn standalone_audio_save(
    app: AppHandle,
    wav_bytes: Vec<u8>,
    speaker_id: String,
    word_id: String,
    source: String,
    quality_rules: Value,
) -> Result<StandaloneAudioResult, String> {
    let parsed = parse_pcm_wav(&wav_bytes)?;
    let workspace = workspace_dir(&app)?;
    let safe_speaker = sanitize_path_component(&speaker_id, "speaker");
    let safe_word = sanitize_path_component(&word_id, "item");
    let relative = format!("audio/{safe_speaker}/{safe_speaker}_{safe_word}.wav");
    let path = resolve_managed_workspace_path(&workspace, &relative)?;
    ensure_managed_parent(&workspace, &path)?;
    let duration_ms = parsed.samples.len() as u64 * 1000 / parsed.sample_rate as u64;
    let (year, month, day, hour, minute, second) = utc_date_time_parts();
    let recorded_at = format!("{year:04}-{month:02}-{day:02}T{hour:02}:{minute:02}:{second:02}Z");
    let quality = analyze_lightweight_quality(&parsed, &quality_rules);
    let stored = quality.get("decision").and_then(Value::as_str) != Some("retry");

    if stored {
        let mut state = standalone_project_load(app.clone())?;
        let speaker = state
            .get_mut("speakers")
            .and_then(Value::as_object_mut)
            .and_then(|speakers| speakers.get_mut(&speaker_id))
            .and_then(Value::as_object_mut)
            .ok_or_else(|| "录音目标发音人已不存在".to_string())?;
        let items = speaker
            .entry("items")
            .or_insert_with(|| json!({}))
            .as_object_mut()
            .ok_or_else(|| "录音目标条目数据无效".to_string())?;
        let item = items
            .get_mut(&word_id)
            .and_then(Value::as_object_mut)
            .ok_or_else(|| "录音目标词项已不存在或数据无效".to_string())?;
        item.insert("path".to_string(), Value::String(relative.clone()));
        item.insert("quality".to_string(), quality.clone());
        item.insert(
            "recorded_at".to_string(),
            Value::String(recorded_at.clone()),
        );
        item.insert("duration_ms".to_string(), json!(duration_ms));
        item.insert("sample_rate_hz".to_string(), json!(parsed.sample_rate));
        item.insert("channels".to_string(), json!(parsed.channels));
        item.insert("format".to_string(), Value::String("wav".to_string()));
        item.insert("source".to_string(), Value::String(source.clone()));

        let previous_audio = fs::read(&path).ok();
        write_bytes_atomic(&path, &wav_bytes)?;
        if let Err(error) = write_project_state_atomic(&workspace, &state) {
            match previous_audio {
                Some(previous) => {
                    let _ = write_bytes_atomic(&path, &previous);
                }
                None => {
                    let _ = fs::remove_file(&path);
                }
            }
            return Err(error);
        }
    }
    Ok(StandaloneAudioResult {
        status: "success".to_string(),
        stored,
        path: relative,
        quality,
        spectrogram: None,
        recorded_at,
        duration_ms,
        sample_rate_hz: parsed.sample_rate,
        channels: parsed.channels,
        format: "wav".to_string(),
        source,
    })
}

#[tauri::command]
fn standalone_audio_read(
    app: AppHandle,
    speaker_id: String,
    word_id: String,
) -> Result<Vec<u8>, String> {
    let workspace = workspace_dir(&app)?;
    let state = standalone_project_load(app)?;
    if let Some(relative) = state
        .get("speakers")
        .and_then(Value::as_object)
        .and_then(|speakers| speakers.get(&speaker_id))
        .and_then(|speaker| speaker.get("items"))
        .and_then(Value::as_object)
        .and_then(|items| items.get(&word_id))
        .and_then(|item| item.get("path"))
        .and_then(Value::as_str)
    {
        let path = resolve_existing_managed_file(&workspace, relative)?;
        return fs::read(path).map_err(|error| format!("读取录音失败：{error}"));
    }
    let safe_speaker = sanitize_path_component(&speaker_id, "speaker");
    let safe_word = sanitize_path_component(&word_id, "item");
    let relative = format!("audio/{safe_speaker}/{safe_speaker}_{safe_word}.wav");
    let path = resolve_existing_managed_file(&workspace, &relative)?;
    fs::read(path).map_err(|error| format!("读取录音失败：{error}"))
}

fn utc_date_time_parts() -> (i64, i64, i64, i64, i64, i64) {
    let total_seconds = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs() as i64;
    let days = total_seconds.div_euclid(86_400);
    let seconds = total_seconds.rem_euclid(86_400);
    let shifted = days + 719_468;
    let era = if shifted >= 0 {
        shifted
    } else {
        shifted - 146_096
    } / 146_097;
    let day_of_era = shifted - era * 146_097;
    let year_of_era =
        (day_of_era - day_of_era / 1_460 + day_of_era / 36_524 - day_of_era / 146_096) / 365;
    let mut year = year_of_era + era * 400;
    let day_of_year = day_of_era - (365 * year_of_era + year_of_era / 4 - year_of_era / 100);
    let month_prime = (5 * day_of_year + 2) / 153;
    let day = day_of_year - (153 * month_prime + 2) / 5 + 1;
    let month = month_prime + if month_prime < 10 { 3 } else { -9 };
    year += if month <= 2 { 1 } else { 0 };
    let hour = seconds / 3_600;
    let minute = seconds % 3_600 / 60;
    let second = seconds % 60;
    (year, month, day, hour, minute, second)
}

fn timestamp_folder_name() -> String {
    let (year, month, day, hour, minute, second) = utc_date_time_parts();
    format!("PhonRec_WAV_{year:04}{month:02}{day:02}_{hour:02}{minute:02}{second:02}")
}

fn create_unique_export_directory(destination: &Path, base_name: &str) -> Result<PathBuf, String> {
    fs::create_dir_all(destination).map_err(|error| format!("创建导出目录失败：{error}"))?;
    let mut output = destination.join(base_name);
    let mut suffix = 2usize;
    while output.exists() {
        output = destination.join(format!("{base_name}_{suffix}"));
        suffix += 1;
    }
    fs::create_dir_all(&output).map_err(|error| format!("创建 WAV 导出目录失败：{error}"))?;
    Ok(output)
}

fn export_wav_folder_from_state(
    workspace: &Path,
    state: &Value,
    destination: &Path,
) -> Result<StandaloneExportResult, String> {
    let base_name = timestamp_folder_name();
    let output = create_unique_export_directory(destination, &base_name)?;

    let speakers = state
        .get("speakers")
        .and_then(Value::as_object)
        .cloned()
        .unwrap_or_default();
    let groups = state
        .get("groups")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    let mut exported = 0usize;
    let mut skipped = 0usize;
    for (speaker_index, (speaker_id, speaker)) in speakers.iter().enumerate() {
        let speaker_name = speaker
            .get("name")
            .and_then(Value::as_str)
            .unwrap_or(speaker_id);
        let speaker_dir = output.join(format!(
            "{:02}_{}",
            speaker_index + 1,
            sanitize_display_path_component(speaker_name, "发音人")
        ));
        let records = speaker.get("items").and_then(Value::as_object);
        for (group_index, group) in groups.iter().enumerate() {
            let group_name = group
                .get("name")
                .and_then(Value::as_str)
                .unwrap_or("未分组");
            let group_dir = speaker_dir.join(format!(
                "{:02}_{}",
                group_index + 1,
                sanitize_display_path_component(group_name, "未分组")
            ));
            if let Some(items) = group.get("items").and_then(Value::as_array) {
                for (item_index, item) in items.iter().enumerate() {
                    let item_id = item.get("id").and_then(Value::as_str).unwrap_or_default();
                    let label = item.get("label").and_then(Value::as_str).unwrap_or("词项");
                    let relative = records
                        .and_then(|records| records.get(item_id))
                        .and_then(|record| record.get("path"))
                        .and_then(Value::as_str);
                    let Some(relative) = relative else {
                        skipped += 1;
                        continue;
                    };
                    let source = match resolve_existing_managed_file(&workspace, relative) {
                        Ok(source) => source,
                        Err(_) => {
                            skipped += 1;
                            continue;
                        }
                    };
                    fs::create_dir_all(&group_dir)
                        .map_err(|error| format!("创建导出分组目录失败：{error}"))?;
                    let target = group_dir.join(format!(
                        "{:03}_{}.wav",
                        item_index + 1,
                        sanitize_display_path_component(label, "词项")
                    ));
                    fs::copy(&source, &target).map_err(|error| format!("导出录音失败：{error}"))?;
                    exported += 1;
                }
            }
        }
    }
    Ok(StandaloneExportResult {
        output_dir: output.to_string_lossy().to_string(),
        exported,
        skipped,
    })
}

#[tauri::command]
fn standalone_export_wav_folder(
    app: AppHandle,
    destination: String,
) -> Result<StandaloneExportResult, String> {
    let workspace = workspace_dir(&app)?;
    let state = standalone_project_load(app)?;
    export_wav_folder_from_state(&workspace, &state, &PathBuf::from(destination))
}

fn read_handshake(child: &mut Child) -> Result<EngineHandshake, String> {
    let stdout = child
        .stdout
        .take()
        .ok_or_else(|| "无法读取分析引擎启动信息".to_string())?;
    let (sender, receiver) = mpsc::channel();
    std::thread::spawn(move || {
        let mut reader = BufReader::new(stdout);
        let mut line = String::new();
        let result = reader
            .read_line(&mut line)
            .map(|_| line)
            .map_err(|error| error.to_string());
        let _ = sender.send(result);

        // 就绪握手之后继续排空标准输出，避免引擎后续诊断信息写入已关闭管道。
        let mut discarded = String::new();
        loop {
            discarded.clear();
            match reader.read_line(&mut discarded) {
                Ok(0) | Err(_) => break,
                Ok(_) => {}
            }
        }
    });

    let line = receiver
        .recv_timeout(Duration::from_secs(15))
        .map_err(|_| "等待分析引擎启动超时".to_string())?
        .map_err(|error| format!("读取分析引擎启动信息失败：{error}"))?;
    let handshake: EngineHandshake = serde_json::from_str(line.trim())
        .map_err(|error| format!("分析引擎启动信息无效：{error}"))?;
    if handshake.event != "ready" {
        return Err("分析引擎未返回 ready 状态".into());
    }
    Ok(handshake)
}

fn fetch_health(port: u16) -> Result<HealthResponse, String> {
    let deadline = Instant::now() + Duration::from_secs(5);
    let address = SocketAddr::from(([127, 0, 0, 1], port));
    loop {
        match TcpStream::connect_timeout(&address, Duration::from_millis(500)) {
            Ok(mut stream) => {
                stream
                    .set_read_timeout(Some(Duration::from_secs(2)))
                    .map_err(|error| error.to_string())?;
                stream
                    .write_all(
                        format!(
                            "GET /api/health HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\n\r\n"
                        )
                        .as_bytes(),
                    )
                    .map_err(|error| format!("健康检查写入失败：{error}"))?;
                let mut response = String::new();
                stream
                    .read_to_string(&mut response)
                    .map_err(|error| format!("健康检查读取失败：{error}"))?;
                if !response.starts_with("HTTP/1.1 200") {
                    return Err("分析引擎健康检查未返回 200".into());
                }
                let body = response
                    .split_once("\r\n\r\n")
                    .map(|(_, body)| body)
                    .ok_or_else(|| "分析引擎健康检查响应无效".to_string())?;
                return serde_json::from_str(body)
                    .map_err(|error| format!("分析引擎健康检查内容无效：{error}"));
            }
            Err(error) if Instant::now() < deadline => {
                let _ = error;
                std::thread::sleep(Duration::from_millis(100));
            }
            Err(error) => return Err(format!("无法连接分析引擎：{error}")),
        }
    }
}

fn stop_engine(runtime: &mut EngineRuntime) {
    if let Some(mut child) = runtime.process.take() {
        let _ = child.kill();
        let _ = child.wait();
    }
}

fn refresh_process_status(runtime: &mut EngineRuntime) {
    if let Some(child) = runtime.process.as_mut() {
        if let Ok(Some(exit_status)) = child.try_wait() {
            runtime.process = None;
            runtime.status =
                EngineStatus::blocked("failed", format!("分析引擎已意外退出：{exit_status}"));
        }
    }
}

fn start_engine(app: &AppHandle, runtime: &mut EngineRuntime) -> EngineStatus {
    stop_engine(runtime);
    let location = match discover_engine() {
        Ok(location) => location,
        Err(message) => return EngineStatus::blocked("missing", message),
    };
    if let Some(protocol) = location.declared_protocol {
        if let Err(message) = validate_protocol(protocol) {
            return EngineStatus::blocked("incompatible", message);
        }
    }

    let workspace = match workspace_dir(app) {
        Ok(path) => path,
        Err(message) => return EngineStatus::blocked("failed", message),
    };
    if let Err(message) = fs::create_dir_all(&workspace)
        .map_err(|error| format!("创建 PhonRec 工作区失败：{error}"))
        .and_then(|_| migrate_legacy_workspace(&workspace))
    {
        return EngineStatus::blocked("failed", message);
    }

    let token = Uuid::new_v4().simple().to_string();
    let mut command = Command::new(&location.executable);
    command
        .arg("--workspace")
        .arg(&workspace)
        .arg("--port")
        .arg("0")
        .env("PHONTRACER_SESSION_TOKEN", &token)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x08000000);
    }

    let mut child = match command.spawn() {
        Ok(child) => child,
        Err(error) => {
            return EngineStatus::blocked("failed", format!("分析引擎启动失败：{error}"));
        }
    };
    let result: Result<EngineConnection, String> = (|| {
        let handshake = read_handshake(&mut child)?;
        validate_protocol(handshake.protocol_version)?;
        let health = fetch_health(handshake.port)?;
        if health.status != "ok" {
            return Err("分析引擎健康状态异常".into());
        }
        validate_protocol(health.protocol_version)?;
        if health.engine_version != handshake.engine_version {
            return Err("分析引擎版本信息不一致".into());
        }
        Ok(EngineConnection {
            api_base: format!("http://127.0.0.1:{}/api", handshake.port),
            token,
            protocol_version: handshake.protocol_version,
            engine_version: handshake.engine_version,
        })
    })();

    match result {
        Ok(connection) => {
            runtime.process = Some(child);
            EngineStatus::ready(connection)
        }
        Err(message) => {
            let _ = child.kill();
            let _ = child.wait();
            EngineStatus::blocked("failed", message)
        }
    }
}

#[tauri::command]
fn get_engine_status(state: State<'_, EngineState>) -> EngineStatus {
    let mut runtime = state.0.lock().expect("分析引擎状态锁已损坏");
    refresh_process_status(&mut runtime);
    runtime.status.clone()
}

#[tauri::command]
fn retry_engine(app: AppHandle, state: State<'_, EngineState>) -> EngineStatus {
    let mut runtime = state.0.lock().expect("分析引擎状态锁已损坏");
    let status = start_engine(&app, &mut runtime);
    runtime.status = status.clone();
    status
}

#[tauri::command]
fn quit_app(app: AppHandle) {
    app.exit(0);
}

// --- Native Local Settings ---
#[derive(Clone, Debug, Serialize, Deserialize)]
struct QualityRule {
    enabled: bool,
    level: String,
}

impl Default for QualityRule {
    fn default() -> Self {
        Self {
            enabled: true,
            level: "medium".to_string(),
        }
    }
}

fn default_enabled_rule() -> QualityRule {
    QualityRule::default()
}

fn default_disabled_rule() -> QualityRule {
    QualityRule {
        enabled: false,
        level: "medium".to_string(),
    }
}

#[derive(Clone, Debug, Serialize, Deserialize)]
struct QualityRules {
    #[serde(default = "default_disabled_rule")]
    speech: QualityRule,
    #[serde(default = "default_enabled_rule")]
    volume: QualityRule,
    #[serde(default = "default_enabled_rule")]
    clipping: QualityRule,
    #[serde(default = "default_disabled_rule")]
    noise: QualityRule,
    #[serde(default = "default_enabled_rule")]
    creak: QualityRule,
    #[serde(default = "default_disabled_rule")]
    dc_offset: QualityRule,
}

impl Default for QualityRules {
    fn default() -> Self {
        Self {
            speech: default_disabled_rule(),
            volume: default_enabled_rule(),
            clipping: default_enabled_rule(),
            noise: default_disabled_rule(),
            creak: default_enabled_rule(),
            dc_offset: default_disabled_rule(),
        }
    }
}

impl QualityRules {
    fn set_all_enabled(&mut self, enabled: bool) {
        self.speech.enabled = enabled;
        self.volume.enabled = enabled;
        self.clipping.enabled = enabled;
        self.noise.enabled = enabled;
        self.creak.enabled = enabled;
        self.dc_offset.enabled = enabled;
    }
}

fn default_theme() -> String {
    "light".to_string()
}
fn default_ui_scale() -> String {
    "100%".to_string()
}
fn default_ui_density() -> String {
    "standard".to_string()
}
fn default_animations_enabled() -> bool {
    true
}
fn default_primary_meta_key() -> String {
    "拼音".to_string()
}
fn default_badge_meta_key() -> String {
    "拼音".to_string()
}
fn default_char_font_size() -> u32 {
    120
}
fn default_vad_preset() -> String {
    "standard".to_string()
}
fn default_shortcut_preset() -> String {
    "standard".to_string()
}
fn default_live_input_monitor() -> bool {
    true
}
fn default_default_project_name() -> String {
    "PhonRec_Project".to_string()
}
fn default_show_shortcut_hints() -> bool {
    true
}
fn default_accent_color() -> String {
    "blue".to_string()
}

#[derive(Clone, Debug, Serialize, Deserialize)]
struct LocalSettings {
    version: u32,
    realtime_quality: bool,
    #[serde(default)]
    quality_rules: QualityRules,
    default_plot: String,  // "waveform" or "spectrogram"
    record_order: String,  // "wordlist" or "random"
    record_mode: String,   // "click" or "hold" or "vad"
    record_source: String, // "default" or device name / ID
    sample_rate: u32,      // 16000, 44100, 48000
    channels: u32,         // 1 (read-only)
    format: String,        // "wav" (read-only)
    save_format: String,   // "teproj" or "folder"
    folder_path: String,   // folder path
    #[serde(default)]
    wav_export_path: String, // 独立模式 WAV 导出目录
    #[serde(default = "default_theme")]
    theme: String,
    #[serde(default = "default_ui_scale")]
    ui_scale: String,
    #[serde(default = "default_ui_density")]
    ui_density: String,
    #[serde(default = "default_animations_enabled")]
    animations_enabled: bool,
    #[serde(default = "default_primary_meta_key")]
    primary_meta_key: String,
    #[serde(default = "default_badge_meta_key")]
    badge_meta_key: String,
    #[serde(default = "default_char_font_size")]
    char_font_size: u32,
    #[serde(default = "default_vad_preset")]
    vad_preset: String,
    #[serde(default = "default_shortcut_preset")]
    shortcut_preset: String,
    #[serde(default = "default_live_input_monitor")]
    live_input_monitor: bool,
    #[serde(default = "default_default_project_name")]
    default_project_name: String,
    #[serde(default = "default_show_shortcut_hints")]
    show_shortcut_hints: bool,
    #[serde(default = "default_accent_color")]
    accent_color: String,
}

impl Default for LocalSettings {
    fn default() -> Self {
        Self {
            version: 1,
            realtime_quality: true,
            quality_rules: QualityRules::default(),
            default_plot: "waveform".to_string(),
            record_order: "wordlist".to_string(),
            record_mode: "click".to_string(),
            record_source: "default".to_string(),
            sample_rate: 48000,
            channels: 1,
            format: "wav".to_string(),
            save_format: "teproj".to_string(),
            folder_path: "".to_string(),
            wav_export_path: "".to_string(),
            theme: default_theme(),
            ui_scale: default_ui_scale(),
            ui_density: default_ui_density(),
            animations_enabled: default_animations_enabled(),
            primary_meta_key: default_primary_meta_key(),
            badge_meta_key: default_badge_meta_key(),
            char_font_size: default_char_font_size(),
            vad_preset: default_vad_preset(),
            shortcut_preset: default_shortcut_preset(),
            live_input_monitor: default_live_input_monitor(),
            default_project_name: default_default_project_name(),
            show_shortcut_hints: default_show_shortcut_hints(),
            accent_color: default_accent_color(),
        }
    }
}

fn settings_file_path(_app: &AppHandle) -> Result<PathBuf, String> {
    #[cfg(windows)]
    {
        let local =
            std::env::var_os("LOCALAPPDATA").ok_or_else(|| "无法读取 LOCALAPPDATA".to_string())?;
        return Ok(PathBuf::from(local)
            .join("KasumiKitsune")
            .join("PhonRec")
            .join("settings.json"));
    }
    #[cfg(target_os = "macos")]
    {
        let home = std::env::var_os("HOME").ok_or_else(|| "无法读取 HOME".to_string())?;
        return Ok(PathBuf::from(home)
            .join("Library")
            .join("Application Support")
            .join("com.kasumikitsune.phonrec")
            .join("settings.json"));
    }
    #[allow(unreachable_code)]
    _app.path()
        .app_local_data_dir()
        .map(|path: PathBuf| path.join("settings.json"))
        .map_err(|error| format!("无法确定设置目录：{error}"))
}

#[tauri::command]
fn load_settings(app: AppHandle) -> Result<LocalSettings, String> {
    let path = settings_file_path(&app)?;
    let backup_path = path.with_extension("bak");
    if !path.exists() {
        if backup_path.exists() {
            fs::rename(&backup_path, &path).map_err(|e| format!("恢复设置备份失败：{e}"))?;
        } else {
            return Ok(LocalSettings::default());
        }
    }
    let content = fs::read_to_string(&path).map_err(|e| format!("无法读取设置文件：{e}"))?;
    let parse_settings = |raw: &str| -> Option<(LocalSettings, bool)> {
        let value: serde_json::Value = serde_json::from_str(raw).ok()?;
        let has_quality_rules = value.get("quality_rules").is_some();
        serde_json::from_value(value)
            .ok()
            .map(|settings| (settings, has_quality_rules))
    };
    let (mut settings, has_quality_rules) = parse_settings(&content)
        .or_else(|| {
            fs::read_to_string(&backup_path)
                .ok()
                .and_then(|backup| parse_settings(&backup))
        })
        .unwrap_or_else(|| (LocalSettings::default(), true));
    if !has_quality_rules {
        settings
            .quality_rules
            .set_all_enabled(settings.realtime_quality);
    }
    if settings.version != 1 {
        return Ok(LocalSettings::default());
    }
    Ok(settings)
}

fn write_settings_file(path: &Path, settings: &LocalSettings) -> Result<(), String> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|e| format!("无法创建设置目录：{e}"))?;
    }

    let temp_path = path.with_extension("tmp");
    let backup_path = path.with_extension("bak");
    let content =
        serde_json::to_string_pretty(settings).map_err(|e| format!("序列化设置失败：{e}"))?;

    let mut temp_file =
        fs::File::create(&temp_path).map_err(|e| format!("创建临时设置文件失败：{e}"))?;
    temp_file
        .write_all(content.as_bytes())
        .map_err(|e| format!("写入临时设置文件失败：{e}"))?;
    temp_file
        .sync_all()
        .map_err(|e| format!("同步临时设置文件失败：{e}"))?;

    if backup_path.exists() {
        fs::remove_file(&backup_path).map_err(|e| format!("清理旧设置备份失败：{e}"))?;
    }
    if path.exists() {
        fs::rename(path, &backup_path).map_err(|e| format!("备份旧设置失败：{e}"))?;
    }

    if let Err(error) = fs::rename(&temp_path, path) {
        if backup_path.exists() {
            let _ = fs::rename(&backup_path, path);
        }
        let _ = fs::remove_file(&temp_path);
        return Err(format!("更新设置文件失败：{error}"));
    }

    if backup_path.exists() {
        fs::remove_file(&backup_path).map_err(|e| format!("清理设置备份失败：{e}"))?;
    }
    Ok(())
}

#[tauri::command]
fn save_settings(app: AppHandle, settings: LocalSettings) -> Result<(), String> {
    let path = settings_file_path(&app)?;
    write_settings_file(&path, &settings)
}

#[tauri::command]
fn reset_settings(app: AppHandle) -> Result<LocalSettings, String> {
    let defaults = LocalSettings::default();
    save_settings(app, defaults.clone())?;
    Ok(defaults)
}

// --- Audio Recording & Device Management ---
#[derive(Serialize, Clone, Debug)]
struct AudioDevice {
    id: String,
    name: String,
    is_loopback: bool,
}

#[tauri::command]
fn list_audio_devices() -> Result<Vec<AudioDevice>, String> {
    use cpal::traits::{DeviceTrait, HostTrait};
    let host = cpal::default_host();
    let mut devices = Vec::new();

    devices.push(AudioDevice {
        id: "default".to_string(),
        name: "系统默认麦克风".to_string(),
        is_loopback: false,
    });

    // 麦克风必须由 WebView 的 MediaDevices API 枚举，CPAL 设备名称不能作为 deviceId。
    #[cfg(windows)]
    {
        if let Ok(output_devices) = host.output_devices() {
            for device in output_devices {
                if let Ok(name) = device.name() {
                    devices.push(AudioDevice {
                        id: format!("loopback:{}", name),
                        name: format!("系统声音回环 ({})", name),
                        is_loopback: true,
                    });
                }
            }
        }
    }

    Ok(devices)
}

struct AudioRuntime {
    stop_sender: Option<mpsc::Sender<()>>,
    recording_buffer: Arc<Mutex<Vec<f32>>>,
    is_recording: Arc<Mutex<bool>>,
    source_sample_rate: u32,
}

impl Default for AudioRuntime {
    fn default() -> Self {
        Self {
            stop_sender: None,
            recording_buffer: Arc::new(Mutex::new(Vec::new())),
            is_recording: Arc::new(Mutex::new(false)),
            source_sample_rate: 48_000,
        }
    }
}

#[derive(Default)]
struct AudioState(Mutex<AudioRuntime>);

fn write_wav_header(sample_rate: u32, num_samples: usize) -> Vec<u8> {
    let mut header = Vec::with_capacity(44);
    header.extend_from_slice(b"RIFF");
    let file_size = 36 + (num_samples * 2) as u32;
    header.extend_from_slice(&file_size.to_le_bytes());
    header.extend_from_slice(b"WAVE");
    header.extend_from_slice(b"fmt ");
    header.extend_from_slice(&16u32.to_le_bytes());
    header.extend_from_slice(&1u16.to_le_bytes());
    header.extend_from_slice(&1u16.to_le_bytes());
    header.extend_from_slice(&sample_rate.to_le_bytes());
    let byte_rate = sample_rate * 2;
    header.extend_from_slice(&byte_rate.to_le_bytes());
    header.extend_from_slice(&2u16.to_le_bytes());
    header.extend_from_slice(&16u16.to_le_bytes());
    header.extend_from_slice(b"data");
    let data_size = (num_samples * 2) as u32;
    header.extend_from_slice(&data_size.to_le_bytes());
    header
}

fn resample_audio(input: &[f32], source_rate: u32, target_rate: u32) -> Vec<f32> {
    if input.is_empty() || source_rate == target_rate {
        return input.to_vec();
    }
    let output_len = ((input.len() as u64 * target_rate as u64) / source_rate as u64) as usize;
    let mut output = Vec::with_capacity(output_len);
    let ratio = source_rate as f64 / target_rate as f64;
    for i in 0..output_len {
        let pos = i as f64 * ratio;
        let left = pos.floor() as usize;
        let right = (left + 1).min(input.len() - 1);
        let frac = pos - left as f64;
        if left < input.len() {
            let val = input[left] as f64 * (1.0 - frac) + input[right] as f64 * frac;
            output.push(val as f32);
        }
    }
    output
}

fn buffer_to_wav(samples: &[f32], sample_rate: u32) -> Vec<u8> {
    let mut wav = Vec::new();
    let header = write_wav_header(sample_rate, samples.len());
    wav.extend_from_slice(&header);
    for &sample in samples {
        let clamped = sample.clamp(-1.0, 1.0);
        let int_sample = if clamped < 0.0 {
            (clamped * 32768.0) as i16
        } else {
            (clamped * 32767.0) as i16
        };
        wav.extend_from_slice(&int_sample.to_le_bytes());
    }
    wav
}

fn handle_input_data<R: tauri::Runtime>(
    data: &[f32],
    channels: u16,
    is_recording: &Arc<Mutex<bool>>,
    buffer: &Arc<Mutex<Vec<f32>>>,
    app: &AppHandle<R>,
    last_emit: &Arc<Mutex<Instant>>,
    temp_samples: &Arc<Mutex<Vec<f32>>>,
) {
    if data.is_empty() {
        return;
    }
    let num_frames = data.len() / channels as usize;
    let mut mono = Vec::with_capacity(num_frames);
    for i in 0..num_frames {
        let mut sum = 0.0;
        for c in 0..channels {
            sum += data[i * channels as usize + c as usize];
        }
        mono.push(sum / channels as f32);
    }

    let recording = is_recording.lock().unwrap();
    if *recording {
        let mut buf = buffer.lock().unwrap();
        buf.extend_from_slice(&mono);
    }
    drop(recording);

    let mut temp = temp_samples.lock().unwrap();
    temp.extend_from_slice(&mono);

    let mut last = last_emit.lock().unwrap();
    if last.elapsed() >= Duration::from_millis(100) {
        if temp.is_empty() {
            *last = Instant::now();
            return;
        }
        let mut sum_sq = 0.0;
        for &s in temp.iter() {
            sum_sq += s * s;
        }
        let rms = (sum_sq / temp.len() as f32).sqrt();
        let volume = (rms * 400.0).min(100.0) as u32;

        let num_points = 20;
        let mut preview_waveform = Vec::new();
        if temp.len() >= num_points {
            let chunk_size = temp.len() / num_points;
            for chunk in temp.chunks(chunk_size).take(num_points) {
                if !chunk.is_empty() {
                    let mut max_val = chunk[0];
                    let mut min_val = chunk[0];
                    for &v in chunk.iter() {
                        if v > max_val {
                            max_val = v;
                        }
                        if v < min_val {
                            min_val = v;
                        }
                    }
                    preview_waveform.push((max_val + min_val) / 2.0);
                }
            }
        } else {
            preview_waveform = temp.clone();
            while preview_waveform.len() < num_points {
                preview_waveform.push(0.0);
            }
        }

        #[derive(Serialize, Clone)]
        struct PreviewData {
            volume: u32,
            waveform: Vec<f32>,
        }

        let _ = app.emit(
            "loopback-preview",
            PreviewData {
                volume,
                waveform: preview_waveform,
            },
        );

        temp.clear();
        *last = Instant::now();
    }
}

#[tauri::command]
fn start_loopback_listener(
    app: AppHandle,
    state: State<'_, AudioState>,
    device_name: String,
) -> Result<(), String> {
    #[cfg(target_os = "macos")]
    {
        let _ = app;
        let _ = state;
        let _ = device_name;
        return Err("系统音频录制当前仅支持 Windows".to_string());
    }
    #[cfg(windows)]
    {
        use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};

        let mut runtime = state.0.lock().map_err(|e| format!("锁损坏: {e}"))?;
        if let Some(stop_sender) = runtime.stop_sender.take() {
            let _ = stop_sender.send(());
        }
        let buffer_clone = runtime.recording_buffer.clone();
        let is_recording_clone = runtime.is_recording.clone();
        let (stop_sender, stop_receiver) = mpsc::channel();
        let (ready_sender, ready_receiver) = mpsc::sync_channel(1);

        std::thread::spawn(move || {
            let result = (|| -> Result<(cpal::Stream, u32), String> {
                let host = cpal::default_host();
                let device = if device_name == "default" {
                    host.default_output_device()
                        .ok_or_else(|| "未找到默认输出设备".to_string())?
                } else {
                    let clean_name = device_name
                        .strip_prefix("loopback:")
                        .unwrap_or(&device_name);
                    host.output_devices()
                        .map_err(|e| e.to_string())?
                        .find(|device| {
                            device
                                .name()
                                .map(|name| name == clean_name)
                                .unwrap_or(false)
                        })
                        .ok_or_else(|| format!("未找到输出设备: {clean_name}"))?
                };

                let config = device
                    .default_output_config()
                    .map_err(|e| format!("获取默认输出配置失败: {e}"))?;
                let source_sample_rate = config.sample_rate().0;
                let channels = config.channels();
                let last_emit = Arc::new(Mutex::new(Instant::now()));
                let temp_samples = Arc::new(Mutex::new(Vec::new()));

                let build_stream = |sample_format| {
                    let stream_err_app = app.clone();
                    let error_callback = move |err| {
                        let _ = stream_err_app.emit("loopback-error", format!("音频流错误：{err}"));
                    };

                    match sample_format {
                        cpal::SampleFormat::F32 => {
                            let recording = is_recording_clone.clone();
                            let buffer = buffer_clone.clone();
                            let app_handle = app.clone();
                            let last = last_emit.clone();
                            let temp = temp_samples.clone();
                            device.build_input_stream(
                                &config.clone().into(),
                                move |data: &[f32], _| {
                                    handle_input_data(
                                        data,
                                        channels,
                                        &recording,
                                        &buffer,
                                        &app_handle,
                                        &last,
                                        &temp,
                                    );
                                },
                                error_callback,
                                None,
                            )
                        }
                        cpal::SampleFormat::I16 => {
                            let recording = is_recording_clone.clone();
                            let buffer = buffer_clone.clone();
                            let app_handle = app.clone();
                            let last = last_emit.clone();
                            let temp = temp_samples.clone();
                            device.build_input_stream(
                                &config.clone().into(),
                                move |data: &[i16], _| {
                                    let converted: Vec<f32> = data
                                        .iter()
                                        .map(|&sample| sample as f32 / 32768.0)
                                        .collect();
                                    handle_input_data(
                                        &converted,
                                        channels,
                                        &recording,
                                        &buffer,
                                        &app_handle,
                                        &last,
                                        &temp,
                                    );
                                },
                                error_callback,
                                None,
                            )
                        }
                        cpal::SampleFormat::U16 => {
                            let recording = is_recording_clone.clone();
                            let buffer = buffer_clone.clone();
                            let app_handle = app.clone();
                            let last = last_emit.clone();
                            let temp = temp_samples.clone();
                            device.build_input_stream(
                                &config.clone().into(),
                                move |data: &[u16], _| {
                                    let converted: Vec<f32> = data
                                        .iter()
                                        .map(|&sample| (sample as f32 - 32768.0) / 32768.0)
                                        .collect();
                                    handle_input_data(
                                        &converted,
                                        channels,
                                        &recording,
                                        &buffer,
                                        &app_handle,
                                        &last,
                                        &temp,
                                    );
                                },
                                error_callback,
                                None,
                            )
                        }
                        _ => return Err(cpal::BuildStreamError::StreamConfigNotSupported),
                    }
                };

                let stream = build_stream(config.sample_format())
                    .map_err(|e| format!("构建回环音频流失败：{e}"))?;
                stream
                    .play()
                    .map_err(|e| format!("开始回环音频流失败：{e}"))?;
                Ok((stream, source_sample_rate))
            })();

            match result {
                Ok((stream, source_sample_rate)) => {
                    let _ = ready_sender.send(Ok(source_sample_rate));
                    let _ = stop_receiver.recv();
                    drop(stream);
                }
                Err(error) => {
                    let _ = ready_sender.send(Err(error));
                }
            }
        });

        let source_sample_rate = ready_receiver
            .recv_timeout(Duration::from_secs(5))
            .map_err(|_| "启动回环音频流超时".to_string())??;
        runtime.stop_sender = Some(stop_sender);
        runtime.source_sample_rate = source_sample_rate;
        Ok(())
    }
}

#[tauri::command]
fn stop_loopback_listener(state: State<'_, AudioState>) -> Result<(), String> {
    let mut runtime = state.0.lock().map_err(|e| format!("锁损坏: {e}"))?;
    if let Some(stop_sender) = runtime.stop_sender.take() {
        let _ = stop_sender.send(());
    }
    *runtime.is_recording.lock().unwrap() = false;
    Ok(())
}

#[tauri::command]
fn start_loopback_recording(state: State<'_, AudioState>) -> Result<(), String> {
    let runtime = state.0.lock().map_err(|e| format!("锁损坏: {e}"))?;
    let mut recording = runtime.is_recording.lock().unwrap();
    {
        let mut buf = runtime.recording_buffer.lock().unwrap();
        buf.clear();
    }
    *recording = true;
    Ok(())
}

#[tauri::command]
fn stop_loopback_recording(
    state: State<'_, AudioState>,
    sample_rate: u32,
) -> Result<Vec<u8>, String> {
    let runtime = state.0.lock().map_err(|e| format!("锁损坏: {e}"))?;
    let mut recording = runtime.is_recording.lock().unwrap();
    *recording = false;

    let raw_samples = {
        let buf = runtime.recording_buffer.lock().unwrap();
        buf.clone()
    };
    drop(recording);

    let actual_sr = runtime.source_sample_rate;

    let resampled = resample_audio(&raw_samples, actual_sr, sample_rate);
    let wav_bytes = buffer_to_wav(&resampled, sample_rate);
    Ok(wav_bytes)
}

#[tauri::command]
fn open_system_permission_settings() -> Result<(), String> {
    #[cfg(windows)]
    {
        Command::new("cmd")
            .args(["/C", "start ms-settings:privacy-microphone"])
            .spawn()
            .map_err(|e| e.to_string())?;
    }
    #[cfg(target_os = "macos")]
    {
        Command::new("open")
            .arg("x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone")
            .spawn()
            .map_err(|e| e.to_string())?;
    }
    Ok(())
}

#[tauri::command]
fn reset_microphone_permission(app: AppHandle) -> Result<(), String> {
    #[cfg(windows)]
    {
        let exe_path = std::env::current_exe()
            .map_err(|e| format!("无法获取当前程序路径：{e}"))?;
        let local_data_dir = app.path().app_local_data_dir()
            .map_err(|e| format!("无法获取数据目录：{e}"))?;
        let ebwebview_dir = local_data_dir.join("EBWebView");

        let exe_str = exe_path.to_string_lossy().replace('\'', "''");
        let ebwebview_str = ebwebview_dir.to_string_lossy().replace('\'', "''");

        // 构建独立的 PowerShell 脚本，在后台等待旧进程释放文件锁，清除目录后重新启动
        let script = format!(
            "Start-Sleep -s 1; \
             for ($i=0; $i -lt 10; $i++) {{ \
                 try {{ \
                     if (Test-Path -Path '{ebwebview_str}') {{ \
                         Remove-Item -Recurse -Force -ErrorAction Stop '{ebwebview_str}'; \
                     }} \
                     break; \
                 }} catch {{ \
                     Start-Sleep -s 1; \
                 }} \
             }}; \
             Start-Process '{exe_str}'",
            ebwebview_str = ebwebview_str,
            exe_str = exe_str
        );

        // 启动隐藏的 PowerShell 进程
        Command::new("powershell")
            .arg("-NoProfile")
            .arg("-WindowStyle")
            .arg("Hidden")
            .arg("-Command")
            .arg(&script)
            .spawn()
            .map_err(|e| format!("无法启动重置脚本：{e}"))?;

        // 退出当前应用进程
        app.exit(0);
        Ok(())
    }
    #[cfg(not(windows))]
    {
        if let Some(window) = app.get_webview_window("main") {
            let _ = window.clear_all_browsing_data();
        }
        Ok(())
    }
}

fn main() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_fs::init())
        .plugin(tauri_plugin_opener::init())
        .manage(EngineState::default())
        .manage(AudioState::default())
        .setup(|app| {
            let handle = app.handle().clone();
            // 引擎发现、进程启动和健康检查都可能耗时。放到后台线程，
            // 避免阻塞 Tauri 主循环 and WebView 的首次绘制。
            std::thread::spawn(move || {
                let state = handle.state::<EngineState>();
                let mut runtime = state.0.lock().expect("分析引擎状态锁已损坏");
                let status = start_engine(&handle, &mut runtime);
                runtime.status = status;
            });
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            get_engine_status,
            retry_engine,
            quit_app,
            standalone_project_load,
            standalone_project_save,
            standalone_project_clear,
            standalone_project_import,
            standalone_project_export,
            standalone_audio_save,
            standalone_audio_read,
            standalone_export_wav_folder,
            load_settings,
            save_settings,
            reset_settings,
            list_audio_devices,
            start_loopback_listener,
            stop_loopback_listener,
            start_loopback_recording,
            stop_loopback_recording,
            open_system_permission_settings,
            reset_microphone_permission
        ])
        .build(tauri::generate_context!())
        .expect("无法创建 PhonRec Tauri 应用");

    app.run(|app_handle, event| {
        if matches!(event, RunEvent::Exit | RunEvent::ExitRequested { .. }) {
            let state = app_handle.state::<EngineState>();
            let mut runtime = state.0.lock().expect("分析引擎状态锁已损坏");
            stop_engine(&mut runtime);
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    fn 立即退出的测试进程() -> Child {
        #[cfg(windows)]
        return Command::new("cmd").args(["/C", "exit 7"]).spawn().unwrap();

        #[cfg(not(windows))]
        return Command::new("/bin/sh")
            .args(["-c", "exit 7"])
            .spawn()
            .unwrap();
    }

    fn 长时间运行的测试进程() -> Child {
        #[cfg(windows)]
        return Command::new("cmd")
            .args(["/C", "ping -n 30 127.0.0.1 >NUL"])
            .spawn()
            .unwrap();

        #[cfg(not(windows))]
        return Command::new("/bin/sh")
            .args(["-c", "sleep 30"])
            .spawn()
            .unwrap();
    }

    #[test]
    fn 协议版本必须完全匹配() {
        assert!(validate_protocol(ENGINE_PROTOCOL).is_ok());
        assert!(validate_protocol(ENGINE_PROTOCOL + 1).is_err());
    }

    #[test]
    fn 复制旧工作区时跳过符号链接并保留文件() {
        let root = std::env::temp_dir().join(format!("phonrec_rust_test_{}", Uuid::new_v4()));
        let source = root.join("source");
        let destination = root.join("destination");
        fs::create_dir_all(source.join("audio")).unwrap();
        fs::write(source.join("project.json"), b"{}").unwrap();
        fs::write(source.join("audio").join("sample.wav"), b"wav").unwrap();

        copy_directory(&source, &destination).unwrap();
        assert_eq!(fs::read(destination.join("project.json")).unwrap(), b"{}");
        assert_eq!(
            fs::read(destination.join("audio").join("sample.wav")).unwrap(),
            b"wav"
        );
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn 非空目录不会被视为空工作区() {
        let root = std::env::temp_dir().join(format!("phonrec_empty_test_{}", Uuid::new_v4()));
        fs::create_dir_all(&root).unwrap();
        assert!(directory_is_empty(&root));
        fs::write(root.join("project.json"), b"{}").unwrap();
        assert!(!directory_is_empty(&root));
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn 引擎崩溃后状态会变为失败() {
        let child = 立即退出的测试进程();
        let connection = EngineConnection {
            api_base: "http://127.0.0.1:1/api".into(),
            token: "test".into(),
            protocol_version: ENGINE_PROTOCOL,
            engine_version: "test".into(),
        };
        let mut runtime = EngineRuntime {
            process: Some(child),
            status: EngineStatus::ready(connection),
        };
        for _ in 0..50 {
            refresh_process_status(&mut runtime);
            if runtime.process.is_none() {
                break;
            }
            std::thread::sleep(Duration::from_millis(10));
        }
        assert!(runtime.process.is_none());
        assert_eq!(runtime.status.state, "failed");
    }

    #[test]
    fn 退出清理会回收分析引擎子进程() {
        let child = 长时间运行的测试进程();
        let mut runtime = EngineRuntime {
            process: Some(child),
            status: EngineStatus::blocked("starting", "测试"),
        };
        stop_engine(&mut runtime);
        assert!(runtime.process.is_none());
    }

    #[test]
    fn test_settings_can_be_saved_repeatedly() {
        let root = std::env::temp_dir().join(format!("phonrec_settings_test_{}", Uuid::new_v4()));
        fs::create_dir_all(&root).unwrap();
        let path = root.join("settings.json");

        let first = LocalSettings::default();
        write_settings_file(&path, &first).unwrap();

        let mut second = LocalSettings::default();
        second.sample_rate = 48_000;
        second.realtime_quality = false;
        write_settings_file(&path, &second).unwrap();

        let loaded_content = fs::read_to_string(&path).unwrap();
        let loaded: LocalSettings = serde_json::from_str(&loaded_content).unwrap();
        assert_eq!(loaded.sample_rate, 48_000);
        assert!(!loaded.realtime_quality);
        assert!(!loaded.quality_rules.speech.enabled);
        assert!(loaded.quality_rules.volume.enabled);
        assert!(loaded.quality_rules.clipping.enabled);
        assert!(!loaded.quality_rules.noise.enabled);
        assert!(loaded.quality_rules.creak.enabled);
        assert!(!loaded.quality_rules.dc_offset.enabled);
        assert!(!path.with_extension("tmp").exists());
        assert!(!path.with_extension("bak").exists());

        fs::write(&path, "corrupted { json").unwrap();
        let fallback: LocalSettings = fs::read_to_string(&path)
            .map(|content| {
                serde_json::from_str::<LocalSettings>(&content)
                    .unwrap_or_else(|_| LocalSettings::default())
            })
            .unwrap_or_else(|_| LocalSettings::default());
        assert_eq!(fallback.sample_rate, 48_000);

        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn 旧版设置缺少质量规则时仍可读取() {
        let mut legacy = serde_json::to_value(LocalSettings::default()).unwrap();
        legacy["realtime_quality"] = serde_json::Value::Bool(false);
        legacy.as_object_mut().unwrap().remove("quality_rules");
        legacy.as_object_mut().unwrap().remove("theme");
        legacy.as_object_mut().unwrap().remove("ui_scale");
        legacy.as_object_mut().unwrap().remove("wav_export_path");
        let mut loaded: LocalSettings = serde_json::from_value(legacy).unwrap();
        loaded
            .quality_rules
            .set_all_enabled(loaded.realtime_quality);
        assert!(!loaded.quality_rules.volume.enabled);
        assert_eq!(loaded.quality_rules.noise.level, "medium");
        assert_eq!(loaded.theme, "light");
        assert_eq!(loaded.ui_scale, "100%");
        assert_eq!(loaded.wav_export_path, "");
    }

    #[test]
    fn test_channel_downmixing() {
        let input_stereo = vec![0.5, -0.5, 1.0, 0.0, -0.2, -0.8];
        let num_frames = input_stereo.len() / 2;
        let mut mono = Vec::with_capacity(num_frames);
        for i in 0..num_frames {
            mono.push((input_stereo[i * 2] + input_stereo[i * 2 + 1]) / 2.0);
        }
        assert_eq!(mono, vec![0.0, 0.5, -0.5]);
    }

    #[test]
    fn test_resampling() {
        let input = vec![1.0, 2.0, 3.0, 4.0];
        let output = resample_audio(&input, 4000, 2000);
        assert_eq!(output.len(), 2);
        assert_eq!(output[0], 1.0);
        assert_eq!(output[1], 3.0);
    }

    #[test]
    fn test_wav_header_generation() {
        let header = write_wav_header(16000, 100);
        assert_eq!(header.len(), 44);
        assert_eq!(&header[0..4], b"RIFF");
        assert_eq!(&header[8..12], b"WAVE");
        assert_eq!(&header[12..16], b"fmt ");
        assert_eq!(&header[36..40], b"data");

        let sr = u32::from_le_bytes([header[24], header[25], header[26], header[27]]);
        assert_eq!(sr, 16000);

        let channels = u16::from_le_bytes([header[22], header[23]]);
        assert_eq!(channels, 1);

        let byte_rate = u32::from_le_bytes([header[28], header[29], header[30], header[31]]);
        assert_eq!(byte_rate, 32000);

        let block_align = u16::from_le_bytes([header[32], header[33]]);
        assert_eq!(block_align, 2);
    }

    #[test]
    fn test_recording_state_machine() {
        let recording_buffer = Arc::new(Mutex::new(Vec::new()));
        let is_recording = Arc::new(Mutex::new(false));
        let last_emit = Arc::new(Mutex::new(Instant::now()));
        let temp_samples = Arc::new(Mutex::new(Vec::new()));

        let data = vec![0.1, -0.1, 0.2, -0.2];

        let mock_app = tauri::test::mock_app();
        let app_handle = mock_app.handle();
        handle_input_data(
            &data,
            2,
            &is_recording,
            &recording_buffer,
            app_handle,
            &last_emit,
            &temp_samples,
        );
        assert!(recording_buffer.lock().unwrap().is_empty());

        *is_recording.lock().unwrap() = true;
        handle_input_data(
            &data,
            2,
            &is_recording,
            &recording_buffer,
            app_handle,
            &last_emit,
            &temp_samples,
        );
        assert_eq!(recording_buffer.lock().unwrap().len(), 2);
        assert_eq!(*recording_buffer.lock().unwrap(), vec![0.0, 0.0]);
    }

    fn 构造_pcm_wav(samples: &[i16], sample_rate: u32) -> Vec<u8> {
        let mut bytes = write_wav_header(sample_rate, samples.len());
        for sample in samples {
            bytes.extend_from_slice(&sample.to_le_bytes());
        }
        bytes
    }

    #[test]
    fn 独立模式路径清洗会阻止穿越和保留字() {
        assert!(sanitize_path_component("../甲:乙\\丙", "词项").starts_with("_甲_乙_丙_"));
        assert!(sanitize_path_component("CON", "词项").starts_with("_CON_"));
        assert!(sanitize_path_component("...", "词项").starts_with("词项_"));
        assert_ne!(
            sanitize_path_component("甲:乙", "词项"),
            sanitize_path_component("甲?乙", "词项")
        );

        let workspace = PathBuf::from("C:/受控工作区");
        assert!(resolve_managed_workspace_path(&workspace, "audio/甲/录音.wav").is_ok());
        assert!(resolve_managed_workspace_path(&workspace, "../越界.wav").is_err());
        assert!(resolve_managed_workspace_path(&workspace, "C:/越界.wav").is_err());
    }

    #[test]
    fn 独立工程状态可重复原子替换() {
        let root =
            std::env::temp_dir().join(format!("phonrec_standalone_atomic_{}", Uuid::new_v4()));
        fs::create_dir_all(&root).unwrap();
        write_project_state_atomic(&root, &json!({"version": "1.0", "groups": [1]})).unwrap();
        write_project_state_atomic(&root, &json!({"version": "1.0", "groups": [2]})).unwrap();

        let loaded: Value =
            serde_json::from_slice(&fs::read(root.join("project.json")).unwrap()).unwrap();
        assert_eq!(loaded["groups"], json!([2]));
        assert!(!root.join("project.tmp").exists());
        assert!(!root.join("project.bak").exists());
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn 独立模式只接受有效的十六位_pcm_wav() {
        let bytes = 构造_pcm_wav(&[0, 1200, -1200, 32767], 16_000);
        let parsed = parse_pcm_wav(&bytes).unwrap();
        assert_eq!(parsed.sample_rate, 16_000);
        assert_eq!(parsed.channels, 1);
        assert_eq!(parsed.samples.len(), 4);
        assert!(parse_pcm_wav(b"not wav").is_err());

        let mut unsupported = bytes;
        unsupported[34..36].copy_from_slice(&24u16.to_le_bytes());
        assert!(parse_pcm_wav(&unsupported).is_err());
    }

    #[test]
    fn 轻量质量检测区分音量和轻重削波() {
        let rules = json!({
            "volume": {"enabled": true, "level": "medium"},
            "clipping": {"enabled": true, "level": "medium"}
        });
        let quiet = ParsedWav {
            sample_rate: 16_000,
            channels: 1,
            samples: vec![0.001; 16_000],
        };
        let quiet_result = analyze_lightweight_quality(&quiet, &rules);
        assert_eq!(quiet_result["decision"], "retry");
        assert_eq!(quiet_result["volume"]["status"], "too_quiet");

        let mut mild_samples = vec![0.1; 10_000];
        mild_samples[..10].fill(1.0);
        let mild = ParsedWav {
            sample_rate: 16_000,
            channels: 1,
            samples: mild_samples,
        };
        let mild_result = analyze_lightweight_quality(&mild, &rules);
        assert_eq!(mild_result["decision"], "review");
        assert_eq!(mild_result["clipping"]["abnormal"], true);

        let mut severe_samples = vec![0.1; 10_000];
        severe_samples[..40].fill(1.0);
        let severe = ParsedWav {
            sample_rate: 16_000,
            channels: 1,
            samples: severe_samples,
        };
        let severe_result = analyze_lightweight_quality(&severe, &rules);
        assert_eq!(severe_result["decision"], "retry");
        assert!(severe_result["issues"]
            .as_array()
            .unwrap()
            .contains(&json!("严重截断")));
        assert_eq!(severe_result["speech"]["label"], "完整模式可用");
    }

    #[test]
    fn 保存工程后会清理失去引用的托管录音() {
        let root =
            std::env::temp_dir().join(format!("phonrec_standalone_prune_{}", Uuid::new_v4()));
        let audio = root.join("audio").join("speaker");
        fs::create_dir_all(&audio).unwrap();
        let kept = audio.join("kept.wav");
        let orphan = audio.join("orphan.wav");
        fs::write(&kept, b"kept").unwrap();
        fs::write(&orphan, b"orphan").unwrap();
        let state = json!({
            "speakers": {
                "speaker": {"items": {"word": {"path": "audio/speaker/kept.wav"}}}
            }
        });

        prune_unreferenced_audio(&state, &root).unwrap();
        assert!(kept.exists());
        assert!(!orphan.exists());
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn wav_导出目录带时间戳且绝不覆盖() {
        let root =
            std::env::temp_dir().join(format!("phonrec_standalone_export_{}", Uuid::new_v4()));
        let first = create_unique_export_directory(&root, "PhonRec_WAV_20260620_120000").unwrap();
        fs::write(first.join("existing.wav"), b"original").unwrap();
        let second = create_unique_export_directory(&root, "PhonRec_WAV_20260620_120000").unwrap();

        assert_eq!(first.file_name().unwrap(), "PhonRec_WAV_20260620_120000");
        assert_eq!(second.file_name().unwrap(), "PhonRec_WAV_20260620_120000_2");
        assert_eq!(fs::read(first.join("existing.wav")).unwrap(), b"original");
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn wav_批量导出按发音人分组和序号组织并跳过未录制项() {
        let root =
            std::env::temp_dir().join(format!("phonrec_standalone_layout_{}", Uuid::new_v4()));
        let workspace = root.join("workspace");
        let destination = root.join("exports");
        let source = workspace.join("audio").join("speaker").join("recorded.wav");
        fs::create_dir_all(source.parent().unwrap()).unwrap();
        fs::write(&source, b"wav-content").unwrap();
        let state = json!({
            "speakers": {
                "speaker": {
                    "name": "张/三",
                    "items": {"word-1": {"path": "audio/speaker/recorded.wav"}}
                }
            },
            "groups": [{
                "name": "声:调",
                "items": [
                    {"id": "word-1", "label": "妈/1"},
                    {"id": "word-2", "label": "麻"}
                ]
            }]
        });

        let result = export_wav_folder_from_state(&workspace, &state, &destination).unwrap();
        let output = PathBuf::from(result.output_dir);
        let exported = output
            .join("01_张_三")
            .join("01_声_调")
            .join("001_妈_1.wav");
        assert_eq!(result.exported, 1);
        assert_eq!(result.skipped, 1);
        assert_eq!(fs::read(exported).unwrap(), b"wav-content");
        assert!(!output
            .join("01_张_三")
            .join("01_声_调")
            .join("002_麻.wav")
            .exists());
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn 主程序工程缺少顶层字表时可从高级条目无损重建() {
        let mut state = json!({
            "version": "1.0",
            "active_speaker_id": "speaker",
            "speakers": {
                "speaker": {
                    "items": {
                        "word": {
                            "id": "word",
                            "label": "妈",
                            "group": "实验组",
                            "group_note": "",
                            "group_tags": ["对照"],
                            "group_meta": {"实验条件": "A"},
                            "tags": [],
                            "item_note": "词项备注",
                            "item_tags": ["目标词"],
                            "item_aliases": ["ma1"],
                            "item_meta": {"拼音": "mā"},
                            "metadata_source": "人工复核"
                        },
                        "word-2": {
                            "id": "word-2",
                            "label": "麻",
                            "group": "实验组",
                            "group_note": "组备注",
                            "group_tags": ["复核"]
                        }
                    }
                },
                "speaker-2": {
                    "items": {
                        "other-id": {
                            "id": "other-id",
                            "label": "妈",
                            "group": "实验组",
                            "group_note": "组备注",
                            "group_tags": ["复核"],
                            "group_meta": {"批次": "二"},
                            "path": "audio/speaker-2/original.wav"
                        }
                    }
                }
            }
        });
        ensure_project_groups(&mut state).unwrap();
        assert_eq!(state["groups"][0]["name"], "实验组");
        assert_eq!(state["groups"][0]["note"], "组备注");
        assert_eq!(state["groups"][0]["tags"], json!(["对照", "复核"]));
        assert_eq!(
            state["groups"][0]["meta"],
            json!({"实验条件": "A", "批次": "二"})
        );
        assert_eq!(state["groups"][0]["items"][0]["note"], "词项备注");
        assert_eq!(state["groups"][0]["items"][0]["tags"], json!(["目标词"]));
        assert_eq!(state["groups"][0]["items"][0]["meta"]["拼音"], "mā");
        assert_eq!(
            state["speakers"]["speaker-2"]["items"]["word"]["path"],
            "audio/speaker-2/original.wav"
        );
        assert!(state["speakers"]["speaker-2"]["items"]
            .get("other-id")
            .is_none());
    }

    #[test]
    fn teproj_归档路径拒绝穿越和绝对路径() {
        assert_eq!(
            archive_relative_path("audio/甲/录音.wav").unwrap(),
            PathBuf::from("audio").join("甲").join("录音.wav")
        );
        assert!(archive_relative_path("../outside.wav").is_err());
        assert!(archive_relative_path("/absolute.wav").is_err());
        assert!(archive_relative_path("C:/absolute.wav").is_err());
        assert!(archive_relative_path("audio/CON.wav").is_err());
        assert!(archive_relative_path("audio/尾随点./word.wav").is_err());
    }

    #[test]
    fn teproj_版本和跨平台重复路径校验严格() {
        assert!(supports_project_version(&json!({})));
        assert!(supports_project_version(&json!({"version": "1.0"})));
        assert!(supports_project_version(&json!({"version": 1})));
        assert!(!supports_project_version(&json!({"version": 2})));
        assert!(!supports_project_version(&json!({"version": true})));
        assert_eq!(
            portable_archive_key(Path::new("Audio/Speaker/Word.wav")),
            portable_archive_key(Path::new("audio/speaker/word.WAV"))
        );
    }
}
