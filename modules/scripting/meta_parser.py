# -*- coding: utf-8 -*-
"""
脚本头部注释元数据解析、Markdown 代码围栏剥离与报错行号提取工具。
"""

import re

SCRIPT_EDITOR_DEFAULT_NAME = "新建自定义脚本"
SCRIPT_EDITOR_DEFAULT_DESC = "自定义分析说明"
SCRIPT_META_NAME_KEYS = ("脚本名称", "脚本名", "名称")
SCRIPT_META_DESC_KEYS = ("功能说明", "脚本说明", "功能", "说明")


def _parse_script_meta_comment_line(line):
    text = line.strip()
    if not text.startswith("#"):
        return None, ""
    text = text[1:].strip()
    if not text:
        return None, ""

    match = re.match(r"^([^:：\-—]+)\s*[:：\-—]\s*(.+)$", text)
    if not match:
        return None, ""

    key = match.group(1).strip()
    value = match.group(2).strip()
    if key in SCRIPT_META_NAME_KEYS:
        return "name", value
    if key in SCRIPT_META_DESC_KEYS:
        return "description", value
    return None, ""


def strip_markdown_code_fence(code: str) -> str:
    """自动剥离脚本首尾可能附带的 ```python ... ``` 或 ``` ... ``` 代码块围栏。
    若包含自然语言开场白且包含 ```python ... ``` 围栏块，亦自动安全提取。"""
    if not isinstance(code, str):
        return "" if code is None else str(code)
    text = code.strip()
    if not text:
        return ""
    lines = text.splitlines()
    if len(lines) >= 2 and lines[0].strip().startswith("```") and lines[-1].strip() == "```":
        return "\n".join(lines[1:-1]).strip("\r\n")
    if lines and lines[0].strip().startswith("```"):
        body = lines[1:]
        if body and body[-1].strip() == "```":
            body = body[:-1]
        return "\n".join(body).strip("\r\n")
    if lines and lines[-1].strip() == "```":
        return "\n".join(lines[:-1]).strip("\r\n")
    m = re.search(r"```(?:python)?\s*\r?\n(.*?)\r?\n```", text, re.DOTALL)
    if m:
        extracted = m.group(1).strip("\r\n")
        try:
            compile(extracted, "<extracted_check>", "exec")
            return extracted
        except SyntaxError:
            pass
    return code


def parse_script_metadata_comments(code):
    """
    从脚本头部注释中解析脚本名称和功能说明（自动剥离 Markdown 代码围栏，并兼容首行编码声明）。
    期望格式：
    # 脚本名称：声调均值曲线
    # 功能说明：按分组绘制 F0 均值和离散区间
    """
    metadata = {"name": "", "description": ""}
    if not code:
        return metadata

    clean_code = strip_markdown_code_fence(code)
    for idx, raw_line in enumerate(clean_code.splitlines()):
        if idx >= 15:
            break
        stripped = raw_line.strip()
        if not stripped:
            continue
        if not stripped.startswith("#"):
            break
        key, value = _parse_script_meta_comment_line(stripped)
        if key is None:
            continue
        if value and not metadata.get(key):
            metadata[key] = value
        if metadata["name"] and metadata["description"]:
            break

    return metadata


def extract_error_line_number(err_text):
    """从脚本报错日志或堆栈信息中解析 <custom_script> 的报错行号 line N。"""
    if not err_text:
        return None
    text = str(err_text)
    custom_matches = re.findall(r'File\s+"<(?:custom_script|unknown|string)>"\s*,\s*line\s+(\d+)', text, flags=re.IGNORECASE)
    if custom_matches:
        try:
            val = int(custom_matches[-1])
            if val > 0:
                return val
        except ValueError:
            pass
    line_matches = re.findall(r"(?: line\s+(\d+)|第\s*(\d+)\s*行)", text, flags=re.IGNORECASE)
    for m_a, m_b in reversed(line_matches):
        raw_n = m_a or m_b
        try:
            val = int(raw_n)
            if val > 0:
                return val
        except ValueError:
            pass
    return None
