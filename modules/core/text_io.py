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

_TITLE_DIRECTIVE_RE = re.compile(
    r"^(?:(?:#|@)\s*(?:title|标题)\s*[:：]?|(?:title|标题)\s*[:：])\s*(.+)$",
    flags=re.IGNORECASE,
)
_PURE_DIVIDER_RE = re.compile(r"^[\s=\-_~*═─━·•—#+/\\|]{3,}$")
_SYM_BANNER_HEADER_RE = re.compile(
    r"^(?:[=═]{2,}|[-─━—]{2,}|[*]{2,}|[~]{2,})\s*(.+?)\s*(?:[=═]{2,}|[-─━—]{2,}|[*]{2,}|[~]{2,})$"
)
_ASYM_BANNER_HEADER_RE = re.compile(
    r"^(?P<lead>[=═]{2,}|[-─━—]{2,}|[*]{2,}|[~]{2,})\s*(?P<body>[^\r\n]+?)\s*$"
)
_BANNER_HEADER_RE = _SYM_BANNER_HEADER_RE


def _extract_banner_header(line: str) -> Optional[str]:
    """提取对称横幅（如 `=== 标题 ===`）或单侧横幅分组头（如 `=== 42_去阳`、`--- 第一组`）。"""
    if not line or _PURE_DIVIDER_RE.match(line):
        return None
    sym_m = _SYM_BANNER_HEADER_RE.match(line)
    if sym_m:
        inner = sym_m.group(1).strip()
        if inner and not _PURE_DIVIDER_RE.match(inner):
            return inner
        return None
    asym_m = _ASYM_BANNER_HEADER_RE.match(line)
    if asym_m:
        lead = asym_m.group("lead")
        body = asym_m.group("body")
        inner = re.sub(r"\s*(?:[=═]+|[-─━—]+|[*]+|[~]+)\s*$", "", body).strip()
        if not inner or _PURE_DIVIDER_RE.match(inner):
            return None
        if len(inner) > 40 or re.search(r"[,，、;；/／\t]", inner):
            return None
        if lead[0] in "-─━—*" and len(lead) == 2 and re.search(r"\s", inner):
            return None
        return inner
    return None
_BRACKET_GROUP_RE = re.compile(
    r"^(?:[-*+•·>→]+\s*)*(?:【([^】]+)】|\[([^\]]+)\]|［([^］]+)］|〖([^〗]+)〗|〈([^〉]+)〉)\s*[:：]?\s*(.*)$"
)
_HASH_COMMENT_KEYWORDS_RE = re.compile(
    r"^(?:注|说明|备注|注意|提示|用法|格式|说明事项|todo|fixme|note|comment)\s*[:：]?",
    flags=re.IGNORECASE,
)
_TITLE_HINT_RE = re.compile(r"(?:字表|词表|语料表|实验|调查表|录音表|草稿|wordlist)", flags=re.IGNORECASE)
_LEAD_ORDINAL_AND_BULLET_RE = re.compile(
    r"^(?:[-*+•·>→]+\s*)*(?:\(\d+\)|（\d+）|\([一二三四五六七八九十百]+\)|（[一二三四五六七八九十百]+）|\d+[.、)）]|[一二三四五六七八九十百]+[.、)）])\s*"
)
_CN_SECTION_HEADER_RE = re.compile(
    r"^(?:[-*+•·>→]+\s*)*(?:[一二三四五六七八九十百]+[.、]|[\(（][一二三四五六七八九十百]+[\)）])\s*([^\s,，、;；/／:：]{1,28})$"
)
_PURE_ITEM_ORDINAL_LEFT_RE = re.compile(
    r"^(?:[-*+•·>→]+\s*)*(?:\(\d+\)|（\d+）|#?\d+|[①-⑳])\s*$"
)


def _split_outside_parens(fragment: str) -> List[str]:
    """按空白、逗号、顿号、分号、斜杠切分词条片段，同时保护圆括号内部的空格/分隔符（如 播音(bo yin)）。"""
    tokens: List[str] = []
    buf: List[str] = []
    depth = 0
    i = 0
    n = len(fragment)
    delims = set(" \t\r\n,，、;；/／")

    while i < n:
        ch = fragment[i]
        if ch in "(（":
            # 若括号紧贴在已有词干后（或空格后紧跟备注括号），进入括号保护态
            depth += 1
            buf.append(ch)
        elif ch in ")）" and depth > 0:
            depth -= 1
            buf.append(ch)
        elif depth == 0 and ch in delims:
            tok = "".join(buf).strip()
            if tok:
                tokens.append(tok)
            buf = []
        else:
            buf.append(ch)
        i += 1

    tail = "".join(buf).strip()
    if tail:
        tokens.append(tail)
    return tokens


def _extract_tokens_from_messy_fragment(fragment: str, line_note: str = "") -> List[Dict[str, Any]]:
    """从单行词条片段中清洗列表符、行内编号、括号备注/别名，提取纯净词项列表。"""
    if not fragment or not fragment.strip():
        return []

    s = fragment.strip()
    # 吸收词与备注圆括号之间的单个空格，如 "播音 (boyin)" -> "播音(boyin)"，但保留独立行首编号 "(1) 播音"
    s = re.sub(r"(?<=[^\s,，、;；/／(（\-*+•·>→0-9.])\s+(\((?!\d+\))[^)）]+\)|（(?!\d+）)[^)）]+）)", r"\1", s)
    # 剥离行首与分隔符后的列表符（如 "- 播音"、"* 高峰"、"• 资源"）
    s = re.sub(r"(?:^|(?<=[\s,，、;；/／]))[-*+•·>→]+(?=\s|$|[^\s\-*+•·>→])", " ", s)
    # 剥离行首与行内编号（如 "1. 播音 2. 高峰"、"1) 播音"、"(1) 播音"、"01: 播音"、"① 播音"）
    s = re.sub(
        r"(?:^|(?<=[\s,，、;；/／]))(?:\(\d+\)|（\d+）|#?\d+\s*(?:[.、)）]|[:：](?=\s|\S))|[①-⑳])\s*",
        " ",
        s,
    )

    raw_tokens = _split_outside_parens(s)
    items: List[Dict[str, Any]] = []
    for token in raw_tokens:
        cleaned = re.sub(
            r"^(?:[-*+•·>→]+|\(\d+\)|（\d+）|\d+[.、)）:：]|[①-⑳])\s*",
            "",
            token.strip(),
        ).strip()
        if not cleaned:
            continue
        if _PURE_DIVIDER_RE.match(cleaned) or re.match(
            r"^(?:\(\d+\)|（\d+）|\d+[.、)）:：]?|[-*+•·>→=/／_~#\\|]+|[①-⑳])$",
            cleaned,
        ):
            continue

        item_note = line_note
        aliases: List[str] = []
        label = cleaned

        paren_match = re.match(r"^([^\s(（]+?)[(（]([^)）]+)[)）]$", cleaned)
        if paren_match:
            label = paren_match.group(1).strip()
            inner = paren_match.group(2).strip()
            if inner:
                if re.match(r"^[a-zA-Z0-9_\-\s]+$", inner):
                    aliases.append(re.sub(r"\s+", " ", inner).strip())
                item_note = f"{inner}；{item_note}".strip("；") if item_note else inner

        label = label.strip(" \t-—·•")
        if not label or _PURE_DIVIDER_RE.match(label):
            continue

        items.append({
            "label": label,
            "note": item_note,
            "aliases": aliases,
        })
    return items


def parse_messy_wordlist_data(raw_text: str, default_group: str = "未分组") -> Dict[str, Any]:
    """
    统一解析普通字表与人类随手编写的草稿 .txt 文本，返回：
    {
        "title": Optional[str],
        "groups": [{"group": str, "items": [{"label": str, "note": str, "aliases": List[str]}, ...]}, ...],
        "flat_words": List[str],
    }
    兼容：
    - `//` 行注释与行尾注释、`/* ... */` 块注释、`# 注：...` 说明行
    - `=== 标题 ===`、`--- 分隔线 ---`、`title: ...`、`# 标题`
    - `【组名】`、`[组名]`、`（组名）`/`(组名)` 圆括号分组头、`一、组名`、`组名：词1 词2` 冒号分组头（含独立 `组名：` 行与带 `- ` 前缀分组头）
    - `1. 2.` / `1) 2)` / `(1) (2)` / `① ②` 行内编号、`- ` / `* ` / `• ` 列表符、`/` 与 `／` 分隔词
    """
    text = (raw_text or "").lstrip("\ufeff").strip()
    if text.startswith("```"):
        fence_lines = text.splitlines()
        if len(fence_lines) >= 2 and fence_lines[-1].strip() == "```":
            text = "\n".join(fence_lines[1:-1]).strip()
        elif len(fence_lines) >= 1:
            text = "\n".join(fence_lines[1:]).strip()

    # 移除 /* ... */ 块注释
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)

    lines_raw = text.splitlines()

    def _is_group_header_probe(s_ln: str) -> bool:
        if not s_ln or _PURE_DIVIDER_RE.match(s_ln) or _TITLE_DIRECTIVE_RE.match(s_ln) or s_ln.lower().startswith("@title"):
            return False
        b_in = _extract_banner_header(s_ln)
        if b_in is not None:
            return not bool(_TITLE_HINT_RE.search(b_in))
        if _BRACKET_GROUP_RE.match(s_ln) or re.match(r"^[(（][^)）\d]+[^)）]*[)）]\s*[:：]?\s*$", s_ln) or _CN_SECTION_HEADER_RE.match(s_ln):
            return True
        if s_ln.startswith("#"):
            gc = s_ln.lstrip("#").strip()
            return bool(gc and not _PURE_DIVIDER_RE.match(gc) and not _HASH_COMMENT_KEYWORDS_RE.match(gc) and not _TITLE_HINT_RE.search(gc))
        if ("：" in s_ln or ":" in s_ln) and "|" not in s_ln:
            sep = "：" if "：" in s_ln else ":"
            l_part = s_ln.split(sep, 1)[0].strip()
            if l_part and not _PURE_ITEM_ORDINAL_LEFT_RE.match(l_part) and len(l_part) <= 32:
                return True
        return False

    # 预判后续是否存在显式分组头（用于区分首行 `# 实验字表` / `=== 标题 ===` 是总标题还是首个分组）
    explicit_group_count = 0
    for r_ln in lines_raw:
        s_ln = r_ln.split("//", 1)[0].strip()
        if _is_group_header_probe(s_ln):
            explicit_group_count += 1

    def _next_active_line_is_header(start_idx: int) -> bool:
        for nxt in lines_raw[start_idx:]:
            s_nxt = nxt.split("//", 1)[0].strip()
            if not s_nxt or s_nxt.startswith("```") or _PURE_DIVIDER_RE.match(s_nxt):
                continue
            if s_nxt.startswith("#") and _HASH_COMMENT_KEYWORDS_RE.match(s_nxt.lstrip("#").strip()):
                continue
            return _is_group_header_probe(s_nxt)
        return False

    detected_title: Optional[str] = None
    group_map: Dict[str, List[Dict[str, Any]]] = {}
    group_order: List[str] = []
    curr_group = default_group
    total_items = 0

    def ensure_group(g_name: str) -> List[Dict[str, Any]]:
        g_clean = (g_name or "").strip() or default_group
        if g_clean not in group_map:
            group_map[g_clean] = []
            group_order.append(g_clean)
        return group_map[g_clean]

    def add_items(fragment: str, g_name: str, line_note: str = "") -> None:
        nonlocal total_items
        parsed_items = _extract_tokens_from_messy_fragment(fragment, line_note=line_note)
        if not parsed_items:
            return
        bucket = ensure_group(g_name)
        bucket.extend(parsed_items)
        total_items += len(parsed_items)

    for line_idx, raw_line in enumerate(lines_raw):
        line = raw_line.strip()
        if not line or line.startswith("```"):
            continue

        # 1. 处理 // 与行尾 # 注释（整行或行尾）
        line_note = ""
        if "//" in line and not re.search(r"https?://", line, flags=re.IGNORECASE):
            line, comment_part = line.split("//", 1)
            line = line.strip()
            line_note = comment_part.strip()
            if not line:
                continue
        if not line.startswith("#"):
            hash_inline = re.search(r"\s+#(?!\d)\s*(.*)$", line)
            if hash_inline:
                h_note = hash_inline.group(1).strip()
                line = line[:hash_inline.start()].strip()
                if h_note:
                    line_note = f"{line_note}；{h_note}".strip("；") if line_note else h_note
                if not line:
                    continue

        # 2. 跳过纯分隔线（如 ========, --------, ********）
        if _PURE_DIVIDER_RE.match(line):
            continue

        # 3. 显式标题指令（title: xxx / # title: xxx / @title xxx）
        t_dir = _TITLE_DIRECTIVE_RE.match(line)
        if t_dir:
            t_val = t_dir.group(1).strip()
            if t_val:
                detected_title = t_val
            continue
        if line.lower().startswith("@title"):
            t_val = line[len("@title"):].lstrip(":： ").strip()
            if t_val:
                detected_title = t_val
            continue

        # 4. 横幅标题/分隔标题（如 === 普通话双字调字表 ===、=== 42_去阳、--- 第一部分 ---）
        inner_banner = _extract_banner_header(line)
        if inner_banner is not None:
            is_doc_title = (
                total_items == 0
                and detected_title is None
                and (
                    bool(_TITLE_HINT_RE.search(inner_banner))
                    or (explicit_group_count >= 1 and _next_active_line_is_header(line_idx + 1))
                )
            )
            if is_doc_title:
                detected_title = inner_banner
            else:
                curr_group = inner_banner
                ensure_group(curr_group)
            continue

        # 5. 方括号/书名号分组头（【组名】、[组名]、［组名］，兼容前缀 `- ` 及同行词条）
        b_match = _BRACKET_GROUP_RE.match(line)
        if b_match:
            grp_raw = next((g for g in b_match.groups()[:-1] if g is not None), "")
            curr_group = grp_raw.strip() or default_group
            ensure_group(curr_group)
            trailing = (b_match.groups()[-1] or "").strip()
            if trailing:
                add_items(trailing, curr_group, line_note=line_note)
            continue

        # 6. Markdown `#` 标题或 `#` 注释
        if line.startswith("#"):
            grp_candidate = line.lstrip("#").strip()
            if not grp_candidate or _PURE_DIVIDER_RE.match(grp_candidate) or _HASH_COMMENT_KEYWORDS_RE.match(grp_candidate):
                continue
            if total_items == 0 and detected_title is None and explicit_group_count >= 1 and _TITLE_HINT_RE.search(grp_candidate):
                detected_title = grp_candidate
                continue
            curr_group = grp_candidate
            ensure_group(curr_group)
            continue

        # 7. 圆括号分组头：如 `(11_阴阴)`、`（11_阴阴）`、`（阴平+阴平） 播音 高峰`、`(一) 阴阴`、`一、阴阴`
        paren_hdr = re.match(r"^(?:[-*+•·>→]+\s*)*[(（]([^)）]+)[)）]\s*[:：]?\s*(.*)$", line)
        if paren_hdr:
            inner_p = paren_hdr.group(1).strip()
            rest_p = paren_hdr.group(2).strip()
            is_arabic_num = bool(re.match(r"^\d+$", inner_p))
            is_cn_num = bool(re.match(r"^[一二三四五六七八九十百]+$", inner_p))
            if not is_arabic_num and not is_cn_num:
                if inner_p and len(inner_p) <= 32 and not re.search(r"[,，、;；/／]", inner_p):
                    curr_group = inner_p
                    ensure_group(curr_group)
                    if rest_p:
                        add_items(rest_p, curr_group, line_note=line_note)
                    continue
            elif is_cn_num and rest_p and ("：" not in rest_p and ":" not in rest_p):
                # 如 `(一) 阴阴组` 独立分组标题行（不含多词分隔符）
                if len(rest_p) <= 28 and not re.search(r"[,，、;；/／\s]", rest_p):
                    curr_group = rest_p
                    ensure_group(curr_group)
                    continue

        cn_sec = _CN_SECTION_HEADER_RE.match(line)
        if cn_sec:
            sec_name = cn_sec.group(1).strip()
            if sec_name:
                curr_group = sec_name
                ensure_group(curr_group)
                continue

        # 8. 冒号分组头：如 `11_阴阴：播音 / 高峰`、`1. 阴阴组: 播音 高峰`、`- 11_阴阴：`
        if ("：" in line or ":" in line) and "|" not in line:
            sep = "：" if "：" in line else ":"
            left, right = line.split(sep, 1)
            left_stripped = left.strip()
            # 排除 `1: 播音` 这类纯序号词条，以及左侧已含多词分隔符的情况
            if (
                left_stripped
                and not _PURE_ITEM_ORDINAL_LEFT_RE.match(left_stripped)
                and not re.search(r"[,，、;；/／]", left_stripped)
                and not re.search(r"\d+[.、)）]\s*\S+\s+\d+", left_stripped)
            ):
                left_clean = _LEAD_ORDINAL_AND_BULLET_RE.sub("", left_stripped).strip()
                # 若外层整块包了圆括号，如 `(11_阴阴):`，剥去外层圆括号
                outer_p = re.match(r"^[(（]([^)）]+)[)）]$", left_clean)
                if outer_p:
                    left_clean = outer_p.group(1).strip()
                if left_clean and len(left_clean) <= 32:
                    curr_group = left_clean
                    ensure_group(curr_group)
                    if right.strip():
                        add_items(right.strip(), curr_group, line_note=line_note)
                    continue

        # 9. 普通词条行
        add_items(line, curr_group, line_note=line_note)

    # 若存在有效词条，剔除无词条的空占位章节头（如顶层大类标题行）
    has_non_empty = any(len(group_map[g]) > 0 for g in group_order)
    groups_out: List[Dict[str, Any]] = []
    flat_words: List[str] = []
    for g in group_order:
        g_items = group_map[g]
        if has_non_empty and not g_items:
            continue
        groups_out.append({"group": g, "items": g_items})
        flat_words.extend(it["label"] for it in g_items)

    return {
        "title": detected_title,
        "groups": groups_out,
        "flat_words": flat_words,
    }


def parse_wordlist(raw_text: str) -> Tuple[List[Dict[str, Any]], List[str]]:
    """解析普通或杂乱草稿字表文本，返回 (groups, flat_words)，其中 groups[i] = {'group': name, 'items': [word, ...]}。"""
    parsed = parse_messy_wordlist_data(raw_text or "")
    groups = [
        {"group": g["group"], "items": [it["label"] for it in g["items"]]}
        for g in parsed["groups"]
        if g["items"]
    ]
    return groups, list(parsed["flat_words"])


def parse_flat_wordlist(raw_text: str) -> List[str]:
    """解析字表并返回扁平词项列表，兼容主程序【组别】与各类随手草稿格式（供 Toolkit 等使用）。"""
    _groups, flat_words = parse_wordlist(raw_text or "")
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
