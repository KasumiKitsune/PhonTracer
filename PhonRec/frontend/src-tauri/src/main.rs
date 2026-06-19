#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use serde::{Deserialize, Serialize};
use std::fs;
use std::io::{BufRead, BufReader, Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::{mpsc, Arc, Mutex};
use std::time::{Duration, Instant};
use tauri::{AppHandle, Emitter, Manager, RunEvent, State, WebviewWindow};
use uuid::Uuid;

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

#[derive(Clone, Debug, Default, Serialize, Deserialize)]
struct QualityRules {
    #[serde(default)]
    speech: QualityRule,
    #[serde(default)]
    volume: QualityRule,
    #[serde(default)]
    clipping: QualityRule,
    #[serde(default)]
    noise: QualityRule,
    #[serde(default)]
    creak: QualityRule,
    #[serde(default)]
    dc_offset: QualityRule,
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
            sample_rate: 16000,
            channels: 1,
            format: "wav".to_string(),
            save_format: "teproj".to_string(),
            folder_path: "".to_string(),
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

    if *is_recording.lock().unwrap() {
        let mut buf = buffer.lock().unwrap();
        buf.extend_from_slice(&mono);
    }

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
    {
        let mut buf = runtime.recording_buffer.lock().unwrap();
        buf.clear();
    }
    *runtime.is_recording.lock().unwrap() = true;
    Ok(())
}

#[tauri::command]
fn stop_loopback_recording(
    state: State<'_, AudioState>,
    sample_rate: u32,
) -> Result<Vec<u8>, String> {
    let runtime = state.0.lock().map_err(|e| format!("锁损坏: {e}"))?;
    *runtime.is_recording.lock().unwrap() = false;

    let raw_samples = {
        let buf = runtime.recording_buffer.lock().unwrap();
        buf.clone()
    };

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
fn reset_microphone_permission(window: WebviewWindow) -> Result<(), String> {
    window
        .clear_all_browsing_data()
        .map_err(|error| format!("清除 WebView 权限记录失败：{error}"))
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
            let state = app.state::<EngineState>();
            let mut runtime = state.0.lock().expect("分析引擎状态锁已损坏");
            let status = start_engine(&handle, &mut runtime);
            runtime.status = status;
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            get_engine_status,
            retry_engine,
            quit_app,
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
        assert!(loaded.quality_rules.speech.enabled);
        assert!(!path.with_extension("tmp").exists());
        assert!(!path.with_extension("bak").exists());

        fs::write(&path, "corrupted { json").unwrap();
        let fallback: LocalSettings = fs::read_to_string(&path)
            .map(|content| {
                serde_json::from_str::<LocalSettings>(&content)
                    .unwrap_or_else(|_| LocalSettings::default())
            })
            .unwrap_or_else(|_| LocalSettings::default());
        assert_eq!(fallback.sample_rate, 16_000);

        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn 旧版设置缺少质量规则时仍可读取() {
        let mut legacy = serde_json::to_value(LocalSettings::default()).unwrap();
        legacy["realtime_quality"] = serde_json::Value::Bool(false);
        legacy.as_object_mut().unwrap().remove("quality_rules");
        let mut loaded: LocalSettings = serde_json::from_value(legacy).unwrap();
        loaded
            .quality_rules
            .set_all_enabled(loaded.realtime_quality);
        assert!(!loaded.quality_rules.volume.enabled);
        assert_eq!(loaded.quality_rules.noise.level, "medium");
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
}
