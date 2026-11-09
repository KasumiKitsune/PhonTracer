import hashlib
import os
import stat


class ArchiveSecurityError(ValueError):
    """ZIP 工程归档安全校验不通过时抛出的异常（继承自 ValueError 以保持向后兼容）。"""


MAX_ARCHIVE_MEMBERS = 100000
MAX_ARCHIVE_MEMBER_COUNT = MAX_ARCHIVE_MEMBERS
MAX_SINGLE_MEMBER_BYTES = 2 * 1024 * 1024 * 1024  # 2 GiB
MAX_TOTAL_UNCOMPRESSED_BYTES = 4 * 1024 * 1024 * 1024  # 4 GiB
MAX_ARCHIVE_MEMBER_BYTES = MAX_SINGLE_MEMBER_BYTES
MAX_ARCHIVE_SINGLE_MEMBER_BYTES = MAX_SINGLE_MEMBER_BYTES
MAX_ARCHIVE_TOTAL_BYTES = MAX_TOTAL_UNCOMPRESSED_BYTES
MAX_ARCHIVE_TOTAL_UNCOMPRESSED_BYTES = MAX_TOTAL_UNCOMPRESSED_BYTES
MAX_ARCHIVE_EXPANSION_RATIO = 100.0
MAX_PROJECT_JSON_BYTES = 20 * 1024 * 1024


def safe_resource_token(value, fallback, max_length=96):
    """把工程内 ID 转成跨平台安全的资源文件名片段，但不改动逻辑 ID。"""
    raw = str(value or "").strip()
    safe = "".join(
        "_" if ord(char) < 32 or char in '<>:"/\\|?*' else char
        for char in raw
    ).rstrip(" .")
    safe = safe or fallback
    if safe.split(".", 1)[0].upper() in {
        "CON", "PRN", "AUX", "NUL",
        *(f"COM{index}" for index in range(1, 10)),
        *(f"LPT{index}" for index in range(1, 10)),
    }:
        safe = f"_{safe}"
    if safe != raw or len(safe) > max_length:
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:10]
        prefix_length = max(1, max_length - len(digest) - 1)
        safe = f"{safe[:prefix_length].rstrip(' .') or fallback[:prefix_length]}_{digest}"
    else:
        safe = safe[:max_length]
    return safe


def normalize_archive_member_name(filename):
    """规范化并校验 ZIP 成员路径，返回规范化后的相对路径字符串。"""
    normalized = str(filename or "").replace("\\", "/")
    if not normalized:
        raise ArchiveSecurityError("工程文件包含空路径成员")
    parts = [part for part in normalized.split("/") if part not in ("", ".")]
    if not parts or normalized.startswith("/") or any(part == ".." for part in parts):
        raise ArchiveSecurityError("工程文件包含非法路径")
    for part in parts:
        if (
            any(ord(char) < 32 or char in '<>:"|?*' for char in part)
            or part.endswith((" ", "."))
            or part.split(".", 1)[0].upper() in {
                "CON", "PRN", "AUX", "NUL",
                *(f"COM{index}" for index in range(1, 10)),
                *(f"LPT{index}" for index in range(1, 10)),
            }
        ):
            raise ArchiveSecurityError("工程文件包含跨平台不兼容路径")
    return "/".join(parts)


def validate_project_archive_members(zip_file):
    infos = zip_file.infolist()
    if len(infos) > MAX_ARCHIVE_MEMBERS:
        raise ArchiveSecurityError(f"工程文件包含过多成员：{len(infos)} 个")

    seen = set()
    total_size = 0
    for member in infos:
        normalized = member.filename.replace("\\", "/")
        canonical_rel = normalize_archive_member_name(normalized)
        parts = canonical_rel.split("/")

        # Windows 与默认配置的 macOS 文件系统通常不区分大小写；按折叠后的
        # 规范路径去重，避免同一成员在解压时被静默覆盖。
        portable_key = "/".join(parts).casefold()
        if portable_key in seen:
            raise ArchiveSecurityError(f"工程文件包含重复成员：{normalized}")
        seen.add(portable_key)

        file_type = (member.external_attr >> 16) & 0o170000
        if file_type == stat.S_IFLNK:
            raise ArchiveSecurityError("工程文件不能包含符号链接")
        if member.file_size > MAX_SINGLE_MEMBER_BYTES:
            raise ArchiveSecurityError(f"归档成员体积超过安全上限: {normalized}")
        total_size += member.file_size
        if total_size > MAX_TOTAL_UNCOMPRESSED_BYTES:
            raise ArchiveSecurityError("工程归档总解压体积超过安全上限")

    return infos


def safe_extract_zip(zip_ref, destination):
    destination_abs = os.path.abspath(destination)
    infos = validate_project_archive_members(zip_ref)
    total_written = 0
    extracted_paths = []
    try:
        for member in infos:
            normalized = member.filename.replace("\\", "/")
            member_path = os.path.abspath(os.path.join(destination_abs, *normalized.split("/")))
            if os.path.commonpath([member_path, destination_abs]) != destination_abs:
                raise ArchiveSecurityError("工程文件包含非法路径")
            if member.is_dir():
                os.makedirs(member_path, exist_ok=True)
                continue
            os.makedirs(os.path.dirname(member_path), exist_ok=True)
            file_written = 0
            with zip_ref.open(member) as source, open(member_path, "wb") as target:
                extracted_paths.append(member_path)
                while True:
                    chunk = source.read(64 * 1024)
                    if not chunk:
                        break
                    file_written += len(chunk)
                    total_written += len(chunk)
                    if file_written > MAX_SINGLE_MEMBER_BYTES or total_written > MAX_TOTAL_UNCOMPRESSED_BYTES:
                        raise ArchiveSecurityError("解压数据超出安全上限")
                    target.write(chunk)
    except Exception:
        for p in reversed(extracted_paths):
            try:
                if os.path.isfile(p):
                    os.remove(p)
            except OSError:
                pass
        raise


def resolve_workspace_path(path, workspace_dir):
    if not path:
        return path
    norm = str(path).replace("\\", "/")
    if norm.startswith("audio/") or norm.startswith("data/"):
        return os.path.join(workspace_dir, *norm.split("/"))
    if os.path.isabs(path):
        return path
    return os.path.join(workspace_dir, path)


__all__ = [
    "ArchiveSecurityError",
    "MAX_ARCHIVE_MEMBERS",
    "MAX_ARCHIVE_MEMBER_COUNT",
    "MAX_SINGLE_MEMBER_BYTES",
    "MAX_TOTAL_UNCOMPRESSED_BYTES",
    "MAX_ARCHIVE_MEMBER_BYTES",
    "MAX_ARCHIVE_SINGLE_MEMBER_BYTES",
    "MAX_ARCHIVE_TOTAL_BYTES",
    "MAX_ARCHIVE_TOTAL_UNCOMPRESSED_BYTES",
    "MAX_ARCHIVE_EXPANSION_RATIO",
    "MAX_PROJECT_JSON_BYTES",
    "safe_resource_token",
    "normalize_archive_member_name",
    "validate_project_archive_members",
    "safe_extract_zip",
    "resolve_workspace_path",
]
