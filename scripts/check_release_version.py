#!/usr/bin/env python3
"""检查发布版本在各组件事实源中是否一致。"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SEMVER_PATTERN = re.compile(
    r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)


def _read_text(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def _match_version(relative_path: str, pattern: str) -> str:
    match = re.search(pattern, _read_text(relative_path), flags=re.MULTILINE)
    if not match:
        raise ValueError(f"无法从 {relative_path} 读取版本号")
    return match.group(1).removeprefix("v")


def collect_versions() -> dict[str, str]:
    """返回所有需要在发布前保持一致的版本源。"""
    package_json = json.loads(_read_text("PhonRec/frontend/package.json"))
    tauri_json = json.loads(_read_text("PhonRec/frontend/src-tauri/tauri.conf.json"))
    readme = _read_text("README.md")
    readme_asset_versions = set(
        re.findall(
            r"PhonRec_(\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?)[_-]",
            readme,
        )
    )
    if len(readme_asset_versions) != 1:
        raise ValueError("README.md 中的 PhonRec 示例资产版本缺失或不唯一")
    return {
        "主程序 modules/version.py": _match_version(
            "modules/version.py", r'^__version__\s*=\s*["\']([^"\']+)["\']'
        ),
        "PhonRec 前端 package.json": str(package_json["version"]),
        "PhonRec Rust Cargo.toml": _match_version(
            "PhonRec/frontend/src-tauri/Cargo.toml", r'^version\s*=\s*"([^"]+)"'
        ),
        "PhonRec Tauri 配置": str(tauri_json["version"]),
        "PhonRec 分析引擎": _match_version(
            "PhonRec/backend/main.py", r'^ENGINE_VERSION\s*=\s*"([^"]+)"'
        ),
        "Windows 安装脚本默认值": _match_version(
            "installer.iss", r'^\s*#define\s+MyAppVersion\s+"([^"]+)"'
        ),
        "README 发布徽章": _match_version(
            "README.md", r"img\.shields\.io/badge/release-v(.+?)-blue\.svg"
        ),
        "README PhonRec 示例资产": next(iter(readme_asset_versions)),
    }


def collect_protocol_version() -> int:
    value = _match_version(
        "PhonRec/backend/main.py", r"^PROTOCOL_VERSION\s*=\s*(\d+)"
    )
    return int(value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="检查 PhonTracer 发布版本是否一致")
    parser.add_argument(
        "--expected",
        help="额外要求的版本号，可写成 1.3.0 或 v1.3.0",
    )
    args = parser.parse_args(argv)

    try:
        versions = collect_versions()
        protocol_version = collect_protocol_version()
    except (OSError, KeyError, ValueError, json.JSONDecodeError) as error:
        print(f"版本检查失败：{error}", file=sys.stderr)
        return 2

    invalid = {name: value for name, value in versions.items() if not SEMVER_PATTERN.fullmatch(value)}
    if invalid:
        for name, value in invalid.items():
            print(f"版本格式无效：{name} = {value}", file=sys.stderr)
        return 1
    if protocol_version < 1:
        print(f"引擎协议版本无效：{protocol_version}", file=sys.stderr)
        return 1

    expected_arg = args.expected
    if not expected_arg and os.environ.get("GITHUB_REF_TYPE") == "tag":
        expected_arg = os.environ.get("GITHUB_REF_NAME")
    expected = expected_arg.removeprefix("v") if expected_arg else next(iter(versions.values()))
    mismatches = {name: value for name, value in versions.items() if value != expected}
    if mismatches:
        print(f"版本不一致，期望版本为 {expected}：", file=sys.stderr)
        for name, value in versions.items():
            marker = "不一致" if name in mismatches else "一致"
            print(f"- [{marker}] {name}: {value}", file=sys.stderr)
        return 1

    print(
        f"版本一致：{expected}（共核对 {len(versions)} 个事实源；"
        f"引擎协议版本：{protocol_version}）"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
