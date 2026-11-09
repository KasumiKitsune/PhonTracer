# -*- coding: utf-8 -*-
"""
PhonTracer语音学标注与声学分析系统 V1.0 自定义脚本管理器
"""

import os
import json
import uuid
import shutil
from modules.script_runner import (
    extract_script_params_schema,
    get_default_script_params,
    coerce_script_params,
)
from .scripting.builtin_scripts_catalog import (
    DEFAULT_SCRIPTS,
    _LEGACY_BUILTIN_SIGNATURES,
)


def get_scripts_dir():
    """获取脚本存储目录路径"""
    path = os.path.join(os.path.expanduser("~"), ".phon_tracer", "scripts")
    if not os.path.exists(path):
        os.makedirs(path)
    return path


def reset_builtin_scripts():
    """重置所有内置示例脚本为默认版本"""
    for ds in DEFAULT_SCRIPTS:
        save_script(ds["id"], ds["name"], ds["description"], ds["type"], ds["code"])


def load_all_scripts(force_reset_builtins=False):
    """加载本地所有脚本（仅当磁盘上不存在内置脚本文件、为旧版未修改内置模板或显式请求重置时才写入默认内置脚本，避免覆盖用户修改）"""
    s_dir = get_scripts_dir()
    os.makedirs(s_dir, exist_ok=True)

    for ds in DEFAULT_SCRIPTS:
        builtin_path = os.path.join(s_dir, f"{ds['id']}.json")
        should_write = force_reset_builtins or not os.path.exists(builtin_path)
        if not should_write and ds["id"] in _LEGACY_BUILTIN_SIGNATURES:
            try:
                with open(builtin_path, "r", encoding="utf-8") as bf:
                    existing = json.load(bf)
                existing_code = str(existing.get("code") or "")
                if (
                    existing.get("name") == ds["name"]
                    and "PARAMS" not in existing_code
                    and _LEGACY_BUILTIN_SIGNATURES[ds["id"]] in existing_code
                ):
                    should_write = True
            except Exception:
                pass
        if should_write:
            save_script(ds["id"], ds["name"], ds["description"], ds["type"], ds["code"])

    scripts = []

    # 获取目录下的所有 .json 文件
    files = [f for f in os.listdir(s_dir) if f.endswith(".json")]

    for f in files:
        f_path = os.path.join(s_dir, f)
        try:
            with open(f_path, 'r', encoding='utf-8') as file:
                s_dict = json.load(file)
                if "id" in s_dict:
                    scripts.append(s_dict)
        except Exception as e:
            print(f"Error loading script file {f}: {e}")

    # 按名字排序
    scripts.sort(key=lambda x: x.get("name", ""))
    return scripts


def save_script(script_id, name, description, script_type, code):
    """保存或更新脚本"""
    s_dir = get_scripts_dir()
    os.makedirs(s_dir, exist_ok=True)
    if not script_id:
        script_id = str(uuid.uuid4())

    s_dict = {
        "id": script_id,
        "name": name,
        "description": description,
        "type": script_type,
        "code": code
    }

    f_path = os.path.join(s_dir, f"{script_id}.json")
    with open(f_path, 'w', encoding='utf-8') as file:
        json.dump(s_dict, file, ensure_ascii=False, indent=2)

    return script_id


def delete_script(script_id):
    """删除脚本"""
    s_dir = get_scripts_dir()
    os.makedirs(s_dir, exist_ok=True)
    f_path = os.path.join(s_dir, f"{script_id}.json")
    if os.path.exists(f_path):
        os.remove(f_path)
        return True
    return False


def copy_script(script_id):
    """复制现有脚本"""
    s_dir = get_scripts_dir()
    os.makedirs(s_dir, exist_ok=True)
    f_path = os.path.join(s_dir, f"{script_id}.json")
    if not os.path.exists(f_path):
        raise FileNotFoundError("找不到要复制的脚本。")

    with open(f_path, 'r', encoding='utf-8') as file:
        s_dict = json.load(file)

    new_id = str(uuid.uuid4())
    s_dict["id"] = new_id
    s_dict["name"] = f"{s_dict['name']} - 副本"

    new_f_path = os.path.join(s_dir, f"{new_id}.json")
    with open(new_f_path, 'w', encoding='utf-8') as file:
        json.dump(s_dict, file, ensure_ascii=False, indent=2)

    return s_dict


def import_script(file_path):
    """导入脚本文件"""
    with open(file_path, 'r', encoding='utf-8') as file:
        s_dict = json.load(file)

    # 简单校验字段
    if not isinstance(s_dict, dict) or "name" not in s_dict or "code" not in s_dict:
        raise ValueError("非法的脚本文件格式：必须包含 'name' 和 'code' 属性。")

    new_id = str(uuid.uuid4())
    s_dict["id"] = new_id

    s_dir = get_scripts_dir()
    os.makedirs(s_dir, exist_ok=True)
    new_f_path = os.path.join(s_dir, f"{new_id}.json")
    with open(new_f_path, 'w', encoding='utf-8') as file:
        json.dump(s_dict, file, ensure_ascii=False, indent=2)

    return s_dict


def export_script(script_id, dest_file_path):
    """导出脚本到目标路径"""
    s_dir = get_scripts_dir()
    os.makedirs(s_dir, exist_ok=True)
    f_path = os.path.join(s_dir, f"{script_id}.json")
    if not os.path.exists(f_path):
        raise FileNotFoundError("找不到要导出的脚本。")

    shutil.copy2(f_path, dest_file_path)
    return True
