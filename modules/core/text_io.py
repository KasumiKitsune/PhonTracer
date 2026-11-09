# -*- coding: utf-8 -*-
"""
PhonTracer 纯文本、音节切分与多编码文件读取核心模块 (Layer 0 - 零业务与零 GUI 依赖)
"""
from __future__ import annotations

import os
import re
import unicodedata
from typing import Any, Dict, List, Optional, Tuple

_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff]")
_UUID_TOKEN_RE = re.compile(
    r"^(?:[0-9a-fA-F]{32}|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})_+"
)


def parse_wordlist(raw_text: str) -> Tuple[List[Dict[str, Any]], List[str]]:
    """解析普通字表文本，返回 (groups, flat_words)。"""
    groups: List[Dict[str, Any]] = []
    flat_words: List[str] = []
    curr_group = "未分组"
    curr_items: List[str] = []
    raw_text = (raw_text or "").lstrip("\ufeff")
    for line in raw_text.split("\n"):
        line = line.strip()
        if not line or line.startswith("```"):
            continue
        if line.startswith("【") or line.startswith("[") or line.startswith("［") or line.startswith("#"):
            if curr_items:
                groups.append({"group": curr_group, "items": curr_items})
                curr_items = []
            curr_group = (
                line.replace("【", "")
                .replace("】", "")
                .replace("[", "")
                .replace("]", "")
                .replace("［", "")
                .replace("］", "")
                .replace("#", "")
                .strip()
            )
        else:
            words = [w.strip() for w in re.split(r"[,\s\t，、;；]+", line) if w.strip()]
            curr_items.extend(words)
            flat_words.extend(words)

    if curr_items:
        groups.append({"group": curr_group, "items": curr_items})
    return groups, flat_words


def parse_flat_wordlist(raw_text: str) -> List[str]:
    """解析字表并返回扁平词项列表，兼容主程序的【组别】格式（供 Toolkit 使用）。"""
    flat_words: List[str] = []
    raw_text = (raw_text or "").lstrip("\ufeff")
    for line in raw_text.split("\n"):
        line = line.strip()
        if not line:
            continue
        if line.startswith("【") or line.startswith("[") or line.startswith("［") or line.startswith("#"):
            continue
        words = [w.strip() for w in re.split(r"[,\s\t，、;；]+", line) if w.strip()]
        flat_words.extend(words)
    return flat_words


def has_cjk(word: str) -> bool:
    """判断字符串中是否包含中日韩统一表意文字（CJK）。"""
    if not word:
        return False
    return bool(_CJK_RE.search(word))


def split_into_syllables(word: str) -> List[str]:
    """将词条标签切分为单字/音节列表，支持 '/' 显式分隔与 CJK 单字拆分，并自动剥离 '(缺失)'/'(未匹配)' 后缀。"""
    if not word:
        return []
    clean_lbl = word.replace(" (缺失)", "").replace(" (未匹配)", "").strip()
    if not clean_lbl:
        return []
    if "/" in clean_lbl:
        return [s.strip() for s in clean_lbl.split("/") if s.strip()]
    if has_cjk(clean_lbl):
        return _CJK_RE.findall(clean_lbl)
    return [clean_lbl]


def make_textgrid_export_stem(path: str = "", fallback_label: str = "", default: str = "audio") -> str:
    """生成导出 TextGrid 时的安全文件名前缀（剥离工程内部 UUID / batch_ 前缀）。"""
    name = os.path.splitext(os.path.basename(str(path or "")))[0].strip()
    if not name or name.lower() == "unknown":
        name = str(fallback_label or "").strip()

    norm_path = str(path or "").replace("\\", "/").lower()
    is_project_managed_audio = (
        norm_path.startswith("audio/")
        or "/.phon_tracer/workspace/audio/" in norm_path
    )

    if is_project_managed_audio:
        batch_match = re.match(r"^.+?_batch_\d+_(.+)$", name)
        if batch_match and batch_match.group(1):
            name = batch_match.group(1)
        else:
            previous = None
            while previous != name:
                previous = name
                name = _UUID_TOKEN_RE.sub("", name)
                name = re.sub(r"^batch_\d+_", "", name)

    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", name).strip(" ._")
    return name or str(default or "audio")


def clean_str(s: str, is_cjk_mode: Optional[bool] = None) -> str:
    """清洗词项字符串用于文件名模糊匹配。"""
    if not s:
        return ""
    s = s.replace("\ufeff", "")
    s = unicodedata.normalize("NFC", s)
    if is_cjk_mode is None:
        is_cjk_mode = has_cjk(s)
    if is_cjk_mode:
        s = "".join(_CJK_RE.findall(s))
    else:
        s = "".join(re.findall(r"[^\W\d_]", s))
    return s.lower().strip()


def fuzzy_match_word_to_path(
    word: str,
    available_paths: List[str],
    used_indices: Optional[List[int]] = None,
) -> Optional[int]:
    """将词表中的词项模糊匹配到可用音频文件路径列表中的最佳未占用索引。"""
    is_cjk_mode = has_cjk(word)
    if used_indices is None:
        used_indices = []
    word_clean = clean_str(word, is_cjk_mode)
    if not word_clean:
        return None

    exact_matches = []
    substring_matches = []

    for i, p in enumerate(available_paths):
        fname_raw = os.path.splitext(os.path.basename(p))[0]
        fname_clean = clean_str(fname_raw, is_cjk_mode)

        if fname_clean == word_clean:
            exact_matches.append(i)
        elif word_clean in fname_clean or fname_clean in word_clean:
            substring_matches.append(i)

    def sort_key(idx: int):
        is_used = 1 if idx in used_indices else 0
        len_diff = abs(len(os.path.splitext(os.path.basename(available_paths[idx]))[0]) - len(word))
        return (is_used, len_diff)

    if exact_matches:
        exact_matches.sort(key=sort_key)
        return exact_matches[0]

    if substring_matches:
        substring_matches.sort(key=sort_key)
        return substring_matches[0]

    return None


def read_text_file_with_fallback(
    path: str,
    encodings: Tuple[str, ...] = ("utf-8-sig", "utf-8", "gbk"),
    strict: bool = True,
) -> str:
    """
    按编码优先级依次尝试读取文本文件：
    - strict=True (默认): 所有编码均失败时抛出最后一个 UnicodeDecodeError
    - strict=False: 所有编码均失败时使用 utf-8 errors='replace' 兜底读取
    """
    last_error = None
    for encoding in encodings:
        try:
            with open(path, "r", encoding=encoding) as f:
                return f.read()
        except UnicodeDecodeError as e:
            last_error = e
    if strict:
        if last_error:
            raise last_error
        with open(path, "r", encoding=encodings[0]) as f:
            return f.read()
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


_read_text_file_with_fallback = read_text_file_with_fallback
