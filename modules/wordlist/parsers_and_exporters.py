# -*- coding: utf-8 -*-
"""
PhonTracer 高级字表 (v2) 多格式解析、导出与 AI Prompt 构造模块
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple, Union

from ..core.text_io import parse_messy_wordlist_data, parse_wordlist, read_text_file_with_fallback
from .schema_and_models import (
    AI_REVIEW_STATUS,
    DEFAULT_GROUP_NAME,
    DEFAULT_REVIEW_STATUS,
    META_PINYIN,
    META_RHYME,
    META_TONE,
    STRUCTURED_KV_PIPE_RE,
    TITLE_DIRECTIVE_RE,
    _as_text,
    _normalize_item,
    _normalize_meta,
    _read_text_file_with_fallback,
    _split_text_list,
    create_empty_wordlist_document,
    load_wordlist_document,
    normalize_wordlist_document,
)

ADVANCED_WORDLIST_AGENT_PROMPT = """你现在是 PhonTracer 高级字表整理 Agent。

你的任务不是立刻输出表格，而是先像研究助理一样和用户确认实验设计，再把材料整理成可导入 PhonTracer Toolkit 的高级字表内容。

工作流程默认分两阶段（快速直通规则除外）：
- 快速直通规则：如果用户明确要求“直接生成”，则无需提问，跳过第一阶段直接进入第二阶段，仅使用原始材料中已有的信息（如组名、词项、明显可识别的同义词或备注）直接生成结果，严禁编造原文不存在的自定义字段。

第一阶段：先提问，不要直接生成字表
请先用简短中文向用户确认以下信息。问题最多 6 个，优先问真正影响字表结构的问题：
1. 这份字表用于什么研究目的，例如声调对比、变调、元音空间、实验组/对照组、质量检查等。
2. 材料语言或方言是什么，词项是否有多音字、变调、轻声、儿化或特殊读法。
3. 希望怎样分组，例如按声调、实验条件、词长、结构、语义类、目标词/填充词。
4. 哪些信息是用户已经确定的，哪些允许你根据常识或材料自动推断。
5. 是否需要加入组备注、词项备注、标签、别名、自定义研究字段。
6. 最终导入格式使用哪一种：结构化字表文本（推荐，省 token，适合直接粘贴）还是 CSV（适合表格软件交换）。如果用户没有偏好，推荐结构化字表文本。

第二阶段：用户确认后再输出高级字表内容
- 最终只输出用户选择的格式，不要输出 JSON、Markdown 表格、代码块或额外解释。
- 默认推荐并输出结构化字表文本；只有用户明确选择 CSV 时才输出 CSV。

结构化字表文本模式：
- 第一行可以用 title: 字表标题（或 # title: 字表标题）设置标题。
- 用 `@group 组名 | tags=组标签 | note=组备注` 开始一个组。
- 用 `@defaults | source=AI推断，需人工复核 | tags=目标词` 设置后续词项默认字段。
- 普通词项行格式为：`词项 | tags=标签 | aliases=别名 | note=词项备注 | pinyin=拼音 | tone=声调 | rhyme=韵母 | 自定义字段=值`
- 未知字段会作为高级字表自定义字段保存，例如 `结构=双字组`、`实验条件=A`、`词频等级=高`。
- 同一个词项有多个标签或别名时，用中文分号“；”分隔。

CSV 模式：
- CSV 第一行必须是表头。
- 固定表头建议包含：组名,组备注,组标签,词项,词项备注,标签,别名,复核状态。
- 如果需要更多研究字段，直接在后面追加中文列名，例如：结构,词长,实验条件,语义类,词频等级,备注来源。
- 每一行代表一个词项；同一个词项有多个标签或别名时，用中文分号“；”分隔。

字段填写规则：
- 组名：用户最终在 PhonTracer 中看到的分组名，尽量短而清楚。
- 组备注：解释该组为什么这样分、用于什么比较、是否有特殊注意事项。
- 组标签：描述组级属性，例如 主测试；对照组；声调对比；变调；填充材料。
- 词项：真正参与音频匹配和提取的文本，必须简洁，不要把备注塞进词项。
- 词项备注：解释该词项的研究角色、读法注意、可能混淆点或排除理由。
- 标签：描述词项属性，例如 目标词；填充词；单字；双字组；阴平；阳平；需复核。
- 别名：可写常见别称、文件名中可能出现的写法或人工记忆名；没有就留空。
- 复核状态：只能使用 人工填写、AI推断，需人工复核、已人工复核 之一。

标签设计规则：
- 标签用短中文词，直接写“目标词”“对照组”“单字”“变调”，不要写 role:target、set:main 这种冒号格式。
- 标签不要写成长句；长解释放到备注或自定义字段。
- 如果一个分类会用于筛选或画图，就适合做标签。
- 如果一个信息更像详细说明、判断依据或实验假设，就放备注。
- 如果一个信息有固定取值并可能用于分组统计，就做自定义字段，例如 结构、实验条件、词频等级。

AI 自动补全规则：
- 只要某个字段不是用户明确给出的，而是你推断、补全或整理出来的，该行复核状态必须写 AI推断，需人工复核。
- 不要伪装成人工确认。即使你很有把握，也必须标为 AI推断，需人工复核。
- 如果用户已经明确确认某些字段，可以写 人工填写 或 已人工复核。
- 对多音字、变调、方言读法、语义分类、词频等级等不确定内容，必须在词项备注中说明“不确定，需复核”。

质量检查规则：
- 不要输出空词项。
- 同一组内避免重复词项；若必须重复，请在词项备注说明原因。
- 组名、标签和自定义字段要保持命名一致，不要一会儿写“目标”，一会儿写“目标词”。
- 如果用户给的是乱序材料，你可以按研究逻辑重新分组，但要先征得用户同意。
- 如果用户明确要求“直接生成”，则无需提问，仅使用原始材料中已有的信息（如组名、词项、明显可识别的同义词或备注）直接生成结果，严禁编造原文不存在的自定义字段。

最终结构化字表文本示例，只作为格式参考，不要照抄内容：
# title: 普通话声调实验
@group 阴平目标词 | tags=主测试；声调对比 | note=阴平单字目标材料
@defaults | source=AI推断，需人工复核 | tags=目标词；单字；阴平 | 结构=单字 | 实验条件=声调基线
妈 | aliases=ma | note=阴平目标词；读音需按普通话复核 | pinyin=ma1

@group 阳平目标词 | tags=主测试；声调对比 | note=阳平单字目标材料
@defaults | source=AI推断，需人工复核 | tags=目标词；单字；阳平 | 结构=单字 | 实验条件=声调基线
麻 | aliases=ma | note=阳平目标词；读音需按普通话复核 | pinyin=ma2

最终 CSV 示例，只作为格式参考；仅当用户明确选择 CSV 时使用：
组名,组备注,组标签,词项,词项备注,标签,别名,复核状态,结构,实验条件
阴平目标词,阴平单字目标材料,主测试；声调对比,妈,阴平目标词；读音需按普通话复核,目标词；单字；阴平,ma,AI推断，需人工复核,单字,声调基线

请先开始第一阶段：向用户提出必要问题。"""


def _strip_markdown_code_fence(text: str) -> str:
    if not isinstance(text, str):
        return "" if text is None else str(text)
    t = text.strip()
    if t.startswith("```"):
        lines = t.splitlines()
        if len(lines) >= 2 and lines[-1].strip() == "```":
            return "\n".join(lines[1:-1]).strip()
        elif len(lines) >= 1:
            return "\n".join(lines[1:]).strip()
    return t


def looks_like_structured_wordlist_text(text: str) -> bool:
    has_directive_or_pipe = False
    has_only_title = False
    for raw_line in (text or "").lstrip("\ufeff").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("```"):
            continue
        lowered = line.lower()
        if (
            lowered.startswith("@group")
            or lowered.startswith("@defaults")
            or bool(STRUCTURED_KV_PIPE_RE.search(line))
        ):
            has_directive_or_pipe = True
            break
        if lowered.startswith("@title") or TITLE_DIRECTIVE_RE.match(line):
            has_only_title = True
    if has_directive_or_pipe:
        return True
    # 若仅含 `title: xxx` 而正文使用的是随手草稿格式（如 `//`、`===`、冒号分组、`1. 2.`），交由 messy 解析器统一兼容
    if has_only_title:
        body = (text or "")
        if "//" in body or "===" in body or re.search(r"(?m)^\s*(?:[-*+•]|[(（][^)\n]+[)）]|\S+\s*[:：]\s*\S+)", body):
            for rl in body.splitlines():
                s = rl.strip()
                if not s or TITLE_DIRECTIVE_RE.match(s) or s.lower().startswith("@title"):
                    continue
                if re.match(r"^(?:【[^】]+】|\[[^\]]+\]|［[^］]+］)\s*$", s):
                    return True
                return False
        return True
    return False


def looks_like_csv_wordlist_text(text: str) -> bool:
    clean_text = _strip_markdown_code_fence(text)
    word_headers = {"label", "词项", "字词", "word", "item"}
    meta_group_headers = {
        "group", "组名", "组别", "group_name", "组备注", "group_note",
        "tags", "tag", "标签", "词项标签", "组标签", "aliases", "别名",
        "pinyin", "拼音", "ipa", "国际音标", "tone", "声调", "rhyme", "韵母",
        "note", "备注", "词项备注",
    }
    for raw_line in (clean_text or "").splitlines():
        line = raw_line.strip().lstrip("\ufeff")
        if not line:
            continue
        if "," not in line:
            return False
        headers = [part.strip().strip('"').strip("'").lower() for part in line.split(",")]
        has_word = any(h in word_headers for h in headers)
        has_meta = any(h in meta_group_headers or h.startswith("meta.") for h in headers)
        return has_word and has_meta
    return False


def _parse_structured_attrs(parts: List[str]) -> Dict[str, str]:
    attrs: Dict[str, str] = {}
    for part in parts:
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        key = _as_text(key)
        value = _as_text(value)
        if key:
            attrs[key] = value
    return attrs


def _apply_structured_group_attrs(group: Dict[str, Any], attrs: Dict[str, str]) -> None:
    for key, value in attrs.items():
        key_norm = key.lower()
        if key_norm in ("tags", "tag") or key in ("组标签", "组tag", "标签"):
            group["tags"] = _split_text_list(value)
        elif key_norm in ("note", "group_note") or key in ("组备注", "备注"):
            group["note"] = value
        elif key_norm.startswith("meta."):
            meta_key = _as_text(key[5:])
            if meta_key:
                group.setdefault("meta", {})[meta_key] = value
        else:
            group.setdefault("meta", {})[key] = value


def _structured_item_from_attrs(label: str, attrs: Dict[str, str]) -> Dict[str, Any]:
    item: Dict[str, Any] = {
        "label": label,
        "note": "",
        "tags": [],
        "aliases": [],
        "meta": {},
        "metadata_source": DEFAULT_REVIEW_STATUS,
    }
    for key, value in attrs.items():
        key_norm = key.lower()
        if key_norm in ("tags", "tag") or key in ("标签", "词项标签"):
            item["tags"] = _split_text_list(value)
        elif key_norm in ("aliases", "alias") or key in ("别名",):
            item["aliases"] = _split_text_list(value)
        elif key_norm in ("note", "item_note") or key in ("备注", "词项备注"):
            item["note"] = value
        elif key_norm in ("source", "metadata_source", "review_status") or key in ("复核状态", "自动补全状态"):
            item["metadata_source"] = value or DEFAULT_REVIEW_STATUS
        elif key_norm in ("pinyin",) or key in ("拼音",):
            item.setdefault("meta", {})[META_PINYIN] = value
        elif key_norm in ("tone",) or key in ("声调",):
            item.setdefault("meta", {})[META_TONE] = value
        elif key_norm in ("rhyme",) or key in ("韵母",):
            item.setdefault("meta", {})[META_RHYME] = value
        elif key_norm.startswith("meta."):
            meta_key = _as_text(key[5:])
            if meta_key:
                item.setdefault("meta", {})[meta_key] = value
        else:
            item.setdefault("meta", {})[key] = value
    return _normalize_item(item)


def build_document_from_structured_text(text: str, title: str = "从结构化文本导入") -> Dict[str, Any]:
    doc = create_empty_wordlist_document(title=title)
    doc["groups"] = []
    current_group: Optional[Dict[str, Any]] = None
    defaults: Dict[str, str] = {}

    def ensure_group() -> Dict[str, Any]:
        nonlocal current_group
        if current_group is None:
            current_group = {
                "id": "",
                "name": DEFAULT_GROUP_NAME,
                "note": "",
                "tags": [],
                "meta": {},
                "items": [],
            }
            doc["groups"].append(current_group)
        return current_group

    bracket_re = re.compile(r"^(?:【([^】]+)】|\[([^\]]+)\]|［([^］]+)］)$")

    for raw_line in (text or "").lstrip("\ufeff").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("```"):
            continue
        if "//" in line and not re.search(r"https?://", line, flags=re.IGNORECASE):
            line = line.split("//", 1)[0].strip()
            if not line:
                continue
        title_match = TITLE_DIRECTIVE_RE.match(line)
        if title_match:
            doc["title"] = _as_text(title_match.group(1)) or doc["title"]
            continue
        if line.startswith("#"):
            continue

        if line.lower().startswith("@title"):
            title_val = _as_text(line[len("@title"):].lstrip(":： "))
            if title_val:
                doc["title"] = title_val
            continue

        parts = [part.strip() for part in line.split("|")]
        head = parts[0] if parts else ""
        attrs = _parse_structured_attrs(parts[1:])
        lowered = head.lower()

        if lowered.startswith("@group"):
            defaults = {}
            group_name = _as_text(head[len("@group"):]) or DEFAULT_GROUP_NAME
            current_group = {
                "id": "",
                "name": group_name,
                "note": "",
                "tags": [],
                "meta": {},
                "items": [],
            }
            _apply_structured_group_attrs(current_group, attrs)
            doc["groups"].append(current_group)
            continue

        b_match = bracket_re.match(head)
        if b_match:
            defaults = {}
            group_name = _as_text(b_match.group(1) or b_match.group(2) or b_match.group(3)) or DEFAULT_GROUP_NAME
            current_group = {
                "id": "",
                "name": group_name,
                "note": "",
                "tags": [],
                "meta": {},
                "items": [],
            }
            _apply_structured_group_attrs(current_group, attrs)
            doc["groups"].append(current_group)
            continue

        if lowered.startswith("@defaults"):
            defaults = dict(attrs)
            continue

        label = _as_text(head)
        if not label:
            continue
        merged_attrs = dict(defaults)
        merged_attrs.update(attrs)
        ensure_group()["items"].append(_structured_item_from_attrs(label, merged_attrs))

    if not doc["groups"]:
        doc["groups"] = create_empty_wordlist_document().get("groups", [])
    return normalize_wordlist_document(doc)


def build_document_from_v1_text(raw_text: str, title: str = "从普通字表导入") -> Dict[str, Any]:
    """兼容旧版入口，底层委托给统一的容错字表解析器。"""
    return build_document_from_messy_text(raw_text or "", title=title)


def build_document_from_messy_text(raw_text: str, title: str = "从普通字表导入") -> Dict[str, Any]:
    """
    将普通或随手编写的杂乱草稿字表文本解析并升级为标准 v2 高级字表文档。
    兼容：
    - 结构化指令或 CSV 文本（自动识别转交）
    - `//` 注释、`=== 标题 ===` 横幅与分隔线
    - 【组名】、[组名]、圆括号分组头 `(11_阴阴)` / `（阴平+阴平）`、中文序号章节头及冒号分组头 `第一组：词1 词2`
    - 行内编号 `1. 2.` / `1) 2)` / `(1) (2)`、` - ` 列表符、`/` 分隔词及词后括号别名/备注 `播音(boyin)`
    """
    clean_text = _strip_markdown_code_fence((raw_text or "").lstrip("\ufeff"))
    if looks_like_structured_wordlist_text(clean_text):
        return build_document_from_structured_text(clean_text, title=title)
    if looks_like_csv_wordlist_text(clean_text):
        return build_document_from_csv_text(clean_text, title=title)

    parsed = parse_messy_wordlist_data(clean_text, default_group=DEFAULT_GROUP_NAME)
    resolved_title = _as_text(parsed.get("title")) or _as_text(title) or "从普通字表导入"
    doc = create_empty_wordlist_document(title=resolved_title)
    doc["groups"] = []

    for grp in parsed.get("groups", []):
        g_name = _as_text(grp.get("group")) or DEFAULT_GROUP_NAME
        norm_items = [
            _normalize_item({
                "label": it.get("label", ""),
                "note": it.get("note", ""),
                "aliases": list(it.get("aliases") or []),
            })
            for it in grp.get("items", [])
            if _as_text(it.get("label"))
        ]
        if norm_items:
            doc["groups"].append({
                "id": "",
                "name": g_name,
                "note": "",
                "tags": [],
                "meta": {},
                "items": norm_items,
            })

    if not doc["groups"]:
        doc["groups"] = create_empty_wordlist_document().get("groups", [])
    return normalize_wordlist_document(doc)


def parse_any_wordlist_text(text: str, title: str = "从文本导入") -> Dict[str, Any]:
    """多模态自适应解析任意字表文本（支持结构化指令、CSV、随手记、传统【组名】格式）。"""
    return build_document_from_messy_text(text, title=title)


def build_document_from_csv_text(csv_text: str, title: str = "从表格导入") -> Dict[str, Any]:
    clean_csv = _strip_markdown_code_fence((csv_text or "").lstrip("\ufeff"))
    stream = io.StringIO(clean_csv or "")
    reader = csv.DictReader(stream)
    doc = create_empty_wordlist_document(title=title)
    doc["groups"] = []
    group_lookup: Dict[str, Dict[str, Any]] = {}

    for row in reader:
        group_name = _as_text(row.get("group") or row.get("组名") or row.get("组别") or row.get("group_name")) or DEFAULT_GROUP_NAME
        label = _as_text(row.get("label") or row.get("词项") or row.get("字词") or row.get("word") or row.get("item"))
        if not label:
            continue
        if group_name not in group_lookup:
            group = {
                "id": "",
                "name": group_name,
                "note": _as_text(row.get("group_note") or row.get("组备注")),
                "tags": _split_text_list(row.get("group_tags") or row.get("组tag") or row.get("组标签")),
                "meta": {},
                "items": [],
            }
            group_lookup[group_name] = group
            doc["groups"].append(group)
        group = group_lookup[group_name]
        if not group.get("note"):
            group["note"] = _as_text(row.get("group_note") or row.get("组备注"))
        for tag in _split_text_list(row.get("group_tags") or row.get("组tag") or row.get("组标签")):
            if tag not in group["tags"]:
                group["tags"].append(tag)

        meta: Dict[str, str] = {}
        consumed = {
            "group", "组名", "组别", "group_name", "group_note", "组备注",
            "group_tags", "组tag", "组标签", "label", "词项", "字词", "word", "item",
            "item_note", "词项备注", "备注", "note", "tags", "tag", "标签", "词项标签", "词项tag",
            "aliases", "别名", "pinyin", "拼音", "tone", "声调", "rhyme", "韵母",
            "metadata_source", "自动补全状态", "review_status", "复核状态",
        }
        for key, value in row.items():
            if key is None:
                continue
            key_text = _as_text(key)
            value_text = _as_text(value)
            if not value_text:
                continue
            if key_text.startswith("meta."):
                meta[key_text[5:].strip()] = value_text
            elif key_text not in consumed:
                meta[key_text] = value_text
        for csv_key, meta_key in (
            ("pinyin", META_PINYIN),
            ("拼音", META_PINYIN),
            ("tone", META_TONE),
            ("声调", META_TONE),
            ("rhyme", META_RHYME),
            ("韵母", META_RHYME),
        ):
            if _as_text(row.get(csv_key)):
                meta[meta_key] = _as_text(row.get(csv_key))

        group["items"].append(
            _normalize_item({
                "label": label,
                "note": row.get("item_note") or row.get("词项备注") or row.get("备注") or row.get("note"),
                "tags": row.get("tags") or row.get("tag") or row.get("词项标签") or row.get("词项tag") or row.get("标签"),
                "aliases": row.get("aliases") or row.get("别名"),
                "meta": meta,
                "metadata_source": (
                    row.get("metadata_source")
                    or row.get("自动补全状态")
                    or row.get("review_status")
                    or row.get("复核状态")
                    or DEFAULT_REVIEW_STATUS
                ),
            })
        )

    if not doc["groups"]:
        doc["groups"] = create_empty_wordlist_document().get("groups", [])
    return normalize_wordlist_document(doc)


def document_to_v1_text(doc: Dict[str, Any]) -> str:
    normalized = normalize_wordlist_document(doc)
    lines: List[str] = []
    for group in normalized.get("groups", []):
        labels = [_as_text(item.get("label")) for item in group.get("items", []) if _as_text(item.get("label"))]
        if not labels:
            continue
        lines.append(f"【{group.get('name', DEFAULT_GROUP_NAME)}】")
        lines.append(" ".join(labels))
    return "\n".join(lines).strip()


export_document_to_v1_text = document_to_v1_text


def document_to_csv_text(doc: Dict[str, Any]) -> str:
    normalized = normalize_wordlist_document(doc)
    meta_keys = []
    for group in normalized.get("groups", []):
        for key in (group.get("meta", {}) or {}).keys():
            if key not in meta_keys:
                meta_keys.append(key)
        for item in group.get("items", []):
            for key in (item.get("meta", {}) or {}).keys():
                if key not in meta_keys:
                    meta_keys.append(key)

    headers = [
        "组别", "组备注", "组标签", "词项", "词项备注", "词项标签",
        "别名", "复核状态",
    ] + meta_keys

    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=headers, lineterminator="\n")
    writer.writeheader()
    for group in normalized.get("groups", []):
        group_meta = dict(group.get("meta", {}) or {})
        for item in group.get("items", []):
            meta = dict(group_meta)
            meta.update(item.get("meta", {}) or {})
            row = {
                "组别": group.get("name", ""),
                "组备注": group.get("note", ""),
                "组标签": "；".join(group.get("tags", [])),
                "词项": item.get("label", ""),
                "词项备注": item.get("note", ""),
                "词项标签": "；".join(item.get("tags", [])),
                "别名": "；".join(item.get("aliases", [])),
                "复核状态": item.get("metadata_source", DEFAULT_REVIEW_STATUS),
            }
            for key in meta_keys:
                row[key] = meta.get(key, "")
            writer.writerow(row)
    return stream.getvalue()


export_document_to_csv_text = document_to_csv_text


def load_wordlist_file(path: str, title: Optional[str] = None) -> Dict[str, Any]:
    """加载 .ptwl（或自动回退解析 .csv / 结构化 .txt / 杂乱草稿 .txt）并规范化为 v2 字表文档。"""
    text = _read_text_file_with_fallback(path)
    default_title = _as_text(title) or os.path.splitext(os.path.basename(path))[0] or "未命名字表"
    doc: Optional[Dict[str, Any]] = None
    stripped = (text or "").lstrip("\ufeff").strip()
    if stripped.startswith("{") or str(path).lower().endswith(".ptwl"):
        try:
            raw = json.loads(stripped)
            if isinstance(raw, dict):
                doc = normalize_wordlist_document(raw)
        except Exception:
            if str(path).lower().endswith(".ptwl"):
                raise
    if doc is None:
        lower = str(path).lower()
        if lower.endswith(".csv") or looks_like_csv_wordlist_text(stripped):
            doc = build_document_from_csv_text(stripped, title=default_title)
        elif looks_like_structured_wordlist_text(stripped):
            doc = build_document_from_structured_text(stripped, title=default_title)
        else:
            doc = build_document_from_messy_text(stripped, title=default_title)
    if title and _as_text(title):
        doc["title"] = _as_text(title)
    return normalize_wordlist_document(doc)


load_any_wordlist_file = load_wordlist_file


def load_wordlist_from_csv_file(path: str, title: Optional[str] = None) -> Dict[str, Any]:
    """从 CSV 文件加载并构建标准 v2 高级字表文档。"""
    csv_text = _read_text_file_with_fallback(path)
    resolved_title = _as_text(title) or os.path.splitext(os.path.basename(path))[0] or "从表格导入"
    doc = build_document_from_csv_text(csv_text, title=resolved_title)
    if title and _as_text(title):
        doc["title"] = _as_text(title)
    return normalize_wordlist_document(doc)


def build_ai_conversion_prompt(raw_text: str = "") -> str:
    """构造用于大语言模型转换高级字表的系统 Prompt 及可选材料。"""
    if not raw_text:
        return ADVANCED_WORDLIST_AGENT_PROMPT
    return f"{ADVANCED_WORDLIST_AGENT_PROMPT}\n\n待整理材料：\n{raw_text}"


def extract_json_payload(response_text: str) -> Optional[Union[Dict[str, Any], List[Any]]]:
    """从大模型回复文本中抽取 JSON 内容（优先剥离代码块）。"""
    clean = _strip_markdown_code_fence(response_text or "")
    try:
        return json.loads(clean)
    except Exception:
        pass
    match = re.search(r"(\{.*\}|\[.*\])", clean, flags=re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except Exception:
            pass
    return None


def parse_ai_wordlist_response(response_text: str, title: str = "AI整理字表") -> Dict[str, Any]:
    """解析大模型生成的字表回复（自动兼容 JSON 格式与结构化/表格文本）。"""
    payload = extract_json_payload(response_text)
    if isinstance(payload, dict):
        return normalize_wordlist_document(payload)
    clean = _strip_markdown_code_fence(response_text or "")
    return build_document_from_messy_text(clean, title=title)
