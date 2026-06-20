import datetime
import hashlib
import json
import os
from pathlib import PurePosixPath
from typing import Any, Dict, List, Tuple


ZERO_HASH = "0" * 64
MANIFEST_SCHEMA = "phontracer.integrity-manifest.v1"


def calculate_json_sha256(obj: Any) -> str:
    """计算规范化 JSON 的 SHA-256。"""
    serialized = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def calculate_file_sha256(filepath: str) -> str:
    """计算文件的 SHA-256 哈希。"""
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"文件不存在: {filepath}")
    sha256 = hashlib.sha256()
    with open(filepath, "rb") as file_obj:
        while True:
            chunk = file_obj.read(65536)
            if not chunk:
                break
            sha256.update(chunk)
    return sha256.hexdigest()


def _validate_hash(value: Any, label: str) -> str:
    if not isinstance(value, str) or len(value) != 64:
        raise ValueError(f"{label} 不是有效的 SHA-256 哈希")
    try:
        int(value, 16)
    except ValueError as exc:
        raise ValueError(f"{label} 不是有效的 SHA-256 哈希") from exc
    return value.lower()


def _resolve_manifest_path(workspace_dir: str, rel_path: Any) -> Tuple[str, str]:
    """验证清单路径并将其解析到工作区内。"""
    if not isinstance(rel_path, str) or not rel_path or "\\" in rel_path:
        raise ValueError(f"清单包含无效资源路径: {rel_path!r}")

    raw_parts = rel_path.split("/")
    if any(part in ("", ".", "..") for part in raw_parts):
        raise ValueError(f"清单资源路径越界: {rel_path}")

    normalized = PurePosixPath(rel_path).as_posix()
    parts = PurePosixPath(normalized).parts
    if normalized == "project.json":
        pass
    elif len(parts) < 2 or parts[0] not in {"audio", "data"}:
        raise ValueError(f"清单资源路径不在允许目录中: {rel_path}")

    workspace_real = os.path.realpath(workspace_dir)
    full_path = os.path.realpath(os.path.join(workspace_real, *parts))
    try:
        if os.path.commonpath([workspace_real, full_path]) != workspace_real:
            raise ValueError(f"清单资源路径越界: {rel_path}")
    except ValueError as exc:
        raise ValueError(f"清单资源路径越界: {rel_path}") from exc
    return normalized, full_path


def verify_audit_chain(workspace_dir: str) -> str:
    """验证完整审计哈希链并返回末尾哈希。"""
    audit_file = os.path.join(workspace_dir, "logs", "audit.jsonl")
    if not os.path.exists(audit_file) or os.path.getsize(audit_file) == 0:
        return ZERO_HASH

    previous_hash = ZERO_HASH
    with open(audit_file, "r", encoding="utf-8") as file_obj:
        for line_number, line in enumerate(file_obj, start=1):
            content = line.strip()
            if not content:
                continue
            try:
                entry = json.loads(content)
            except json.JSONDecodeError as exc:
                raise ValueError(f"审计日志第 {line_number} 行不是有效 JSON") from exc
            if not isinstance(entry, dict):
                raise ValueError(f"审计日志第 {line_number} 行不是对象")

            stored_hash = _validate_hash(entry.get("hash"), f"审计日志第 {line_number} 行 hash")
            recorded_previous = _validate_hash(
                entry.get("prev_hash"),
                f"审计日志第 {line_number} 行 prev_hash",
            )
            if recorded_previous != previous_hash:
                raise ValueError(f"审计日志第 {line_number} 行的前序哈希不匹配")

            unsigned_entry = dict(entry)
            unsigned_entry.pop("hash", None)
            calculated_hash = calculate_json_sha256(unsigned_entry)
            if stored_hash != calculated_hash:
                raise ValueError(f"审计日志第 {line_number} 行的内容哈希不匹配")
            previous_hash = stored_hash

    return previous_hash


def get_audit_tail_hash(workspace_dir: str) -> str:
    """验证审计链并返回最后一条记录的哈希。"""
    return verify_audit_chain(workspace_dir)


def append_audit_log(workspace_dir: str, event_type: str, details: Dict[str, Any]) -> str:
    """向 logs/audit.jsonl 追加一条审计日志并返回其哈希。"""
    logs_dir = os.path.join(workspace_dir, "logs")
    os.makedirs(logs_dir, exist_ok=True)
    audit_file = os.path.join(logs_dir, "audit.jsonl")
    previous_hash = get_audit_tail_hash(workspace_dir)

    entry = {
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "event_type": event_type,
        "details": details,
        "prev_hash": previous_hash,
    }
    entry["hash"] = calculate_json_sha256(entry)

    with open(audit_file, "a", encoding="utf-8") as file_obj:
        file_obj.write(json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n")
        file_obj.flush()
        os.fsync(file_obj.fileno())
    return entry["hash"]


def update_manifest(workspace_dir: str) -> Dict[str, str]:
    """原子更新 integrity/manifest.json 清单。"""
    integrity_dir = os.path.join(workspace_dir, "integrity")
    os.makedirs(integrity_dir, exist_ok=True)
    manifest_file = os.path.join(integrity_dir, "manifest.json")

    files_map: Dict[str, str] = {}
    project_json = os.path.join(workspace_dir, "project.json")
    if os.path.exists(project_json):
        files_map["project.json"] = calculate_file_sha256(project_json)

    for subdirectory in ("audio", "data"):
        root_directory = os.path.join(workspace_dir, subdirectory)
        if not os.path.exists(root_directory):
            continue
        for root, _, files in os.walk(root_directory):
            for filename in files:
                full_path = os.path.join(root, filename)
                relative_path = os.path.relpath(full_path, workspace_dir).replace("\\", "/")
                files_map[relative_path] = calculate_file_sha256(full_path)

    manifest_data = {
        "schema": MANIFEST_SCHEMA,
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "audit_tail_hash": get_audit_tail_hash(workspace_dir),
        "files": files_map,
    }

    temporary_manifest = f"{manifest_file}.{os.getpid()}.tmp"
    try:
        with open(temporary_manifest, "w", encoding="utf-8") as file_obj:
            json.dump(manifest_data, file_obj, ensure_ascii=False, indent=2)
            file_obj.flush()
            os.fsync(file_obj.fileno())
        os.replace(temporary_manifest, manifest_file)
    finally:
        if os.path.exists(temporary_manifest):
            os.remove(temporary_manifest)
    return files_map


def verify_project_integrity(workspace_dir: str) -> Tuple[str, List[str], Dict[str, Any]]:
    """验证工程文件、审计链和清单边界。"""
    warnings: List[str] = []
    details: Dict[str, Any] = {
        "missing_audio": [],
        "corrupt_audio": [],
        "missing_caches": [],
        "corrupt_caches": [],
        "integrity_errors": [],
        "unsafe_paths": [],
    }

    manifest_file = os.path.join(workspace_dir, "integrity", "manifest.json")
    if not os.path.exists(manifest_file):
        warnings.append("工程缺少完整性清单 manifest.json，标记为 legacy_unverified")
        return "legacy_unverified", warnings, details

    project_json = os.path.join(workspace_dir, "project.json")
    if not os.path.exists(project_json):
        warnings.append("未找到 project.json 文件")
        details["integrity_errors"].append("project.json_missing")
        return "corrupt", warnings, details

    try:
        with open(manifest_file, "r", encoding="utf-8") as file_obj:
            manifest = json.load(file_obj)
    except Exception as exc:
        warnings.append(f"解析 manifest.json 失败: {exc}")
        details["integrity_errors"].append("manifest_invalid")
        return "corrupt", warnings, details

    if not isinstance(manifest, dict) or manifest.get("schema") != MANIFEST_SCHEMA:
        warnings.append("manifest.json 的结构版本无效")
        details["integrity_errors"].append("manifest_schema_invalid")
        return "corrupt", warnings, details

    files_manifest = manifest.get("files")
    if not isinstance(files_manifest, dict):
        warnings.append("manifest.json 的 files 字段无效")
        details["integrity_errors"].append("manifest_files_invalid")
        return "corrupt", warnings, details

    has_integrity_error = False
    has_audio_error = False
    has_cache_error = False

    try:
        expected_tail = _validate_hash(manifest.get("audit_tail_hash"), "manifest audit_tail_hash")
        actual_tail = verify_audit_chain(workspace_dir)
        if actual_tail != expected_tail:
            raise ValueError("审计日志末尾哈希与清单不匹配")
    except Exception as exc:
        warnings.append(f"审计日志校验失败: {exc}")
        details["integrity_errors"].append("audit_chain_invalid")
        has_integrity_error = True

    expected_project_hash = files_manifest.get("project.json")
    try:
        expected_project_hash = _validate_hash(expected_project_hash, "project.json 清单哈希")
        if calculate_file_sha256(project_json) != expected_project_hash:
            raise ValueError("project.json 的哈希与清单记录不匹配")
    except Exception as exc:
        warnings.append(str(exc))
        details["integrity_errors"].append("project_json_invalid")
        has_integrity_error = True

    for rel_path, expected_hash_value in files_manifest.items():
        if rel_path == "project.json":
            continue
        try:
            normalized_path, full_path = _resolve_manifest_path(workspace_dir, rel_path)
            expected_hash = _validate_hash(expected_hash_value, f"{normalized_path} 清单哈希")
        except Exception as exc:
            warnings.append(str(exc))
            details["unsafe_paths"].append(str(rel_path))
            has_integrity_error = True
            continue

        is_audio = normalized_path.startswith("audio/")
        is_cache = normalized_path.startswith("data/")
        if not os.path.isfile(full_path):
            if is_audio:
                details["missing_audio"].append(normalized_path)
                warnings.append(f"音频文件缺失: {normalized_path}")
                has_audio_error = True
            elif is_cache:
                details["missing_caches"].append(normalized_path)
                warnings.append(f"缓存文件缺失: {normalized_path}")
                has_cache_error = True
            continue

        try:
            current_hash = calculate_file_sha256(full_path)
        except Exception as exc:
            current_hash = None
            warnings.append(f"读取资源文件失败: {normalized_path}, 错误: {exc}")

        if current_hash != expected_hash:
            if is_audio:
                details["corrupt_audio"].append(normalized_path)
                warnings.append(f"音频文件损坏 (哈希不匹配): {normalized_path}")
                has_audio_error = True
            elif is_cache:
                details["corrupt_caches"].append(normalized_path)
                warnings.append(f"缓存文件损坏 (哈希不匹配): {normalized_path}")
                has_cache_error = True

    if has_integrity_error or has_audio_error:
        return "corrupt", warnings, details
    if has_cache_error:
        return "stale_caches", warnings, details
    return "verified", warnings, details
