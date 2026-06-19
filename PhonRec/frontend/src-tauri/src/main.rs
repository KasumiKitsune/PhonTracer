#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use serde::{Deserialize, Serialize};
use std::fs;
use std::io::{BufRead, BufReader, Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::{mpsc, Mutex};
use std::time::{Duration, Instant};
use tauri::{AppHandle, Manager, RunEvent, State};
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

fn main() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_fs::init())
        .plugin(tauri_plugin_opener::init())
        .manage(EngineState::default())
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
            quit_app
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
}
