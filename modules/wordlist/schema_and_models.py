# -*- coding: utf-8 -*-
"""
PhonTracer 高级字表 (v2) 核心数据模型、常量、归一化、合并、校验与文件持久化
"""
from __future__ import annotations

import json
import os
import re
from copy import deepcopy
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple, Union

from ..core.text_io import read_text_file_with_fallback

WORDLIST_SCHEMA = "phontracer.wordlist.v2"
WORDLIST_SCHEMA_VERSION = WORDLIST_SCHEMA
WORDLIST_FORMAT = "json"
WORDLIST_EXTENSION = ".ptwl"
DEFAULT_GROUP_NAME = "未分组"
DEFAULT_REVIEW_STATUS = "人工填写"
AI_REVIEW_STATUS = "AI推断，需人工复核"
AI_GENERATED_STATUS = AI_REVIEW_STATUS
REVIEWED_STATUS = "已人工复核"

META_PINYIN = "拼音"
META_TONE = "声调"
META_RHYME = "韵母"
CORE_META_FIELDS = (META_PINYIN, META_TONE, META_RHYME)

TITLE_DIRECTIVE_RE = re.compile(
    r"^(?:(?:#|@)\s*(?:title|标题)\s*[:：]?|(?:title|标题)\s*[:：])\s*(.+)$",
    flags=re.IGNORECASE,
)

STRUCTURED_KV_PIPE_RE = re.compile(r"\|\s*[^|=\s]+\s*=")


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


_clean_text = _as_text


def _read_text_file_with_fallback(
    path: str,
    encodings: Tuple[str, ...] = ("utf-8-sig", "utf-8", "gbk"),
) -> str:
    """wordlist 读取降级：所有编码失败后使用 utf-8 errors='replace' 兜底。"""
    return read_text_file_with_fallback(path, encodings=encodings, strict=False)


def _split_text_list(value: Any) -> List[str]:
    if isinstance(value, list):
        values = value
    elif isinstance(value, tuple):
        values = list(value)
    else:
        text = _as_text(value)
        if not text:
            return []
        values = re.split(r"[;；,，、\n\t]+", text)
    return [_as_text(v) for v in values if _as_text(v)]


_split_tokens = _split_text_list


def _normalize_meta(value: Any) -> Dict[str, str]:
    if not isinstance(value, dict):
        return {}
    meta = {}
    for key, val in value.items():
        k = _as_text(key)
        if k:
            meta[k] = _as_text(val)
    return meta


_normalize_meta_dict = _normalize_meta


def _normalize_item(item: Any, fallback_label: str = "", fallback_id: str = "") -> Dict[str, Any]:
    if isinstance(item, str):
        item = {"label": item}
    elif not isinstance(item, dict):
        item = {}

    meta: Dict[str, str] = {}
    for meta_field in ("custom_fields", "fields", "item_meta", "meta"):
        if isinstance(item.get(meta_field), dict):
            meta.update(_normalize_meta(item.get(meta_field)))

    for source_key, meta_key in (
        ("pinyin", META_PINYIN),
        ("拼音", META_PINYIN),
        ("tone", META_TONE),
        ("声调", META_TONE),
        ("rhyme", META_RHYME),
        ("韵母", META_RHYME),
    ):
        if source_key in item and _as_text(item.get(source_key)):
            meta[meta_key] = _as_text(item.get(source_key))

    item_id = _as_text(item.get("id", ""))
    if not item_id and fallback_id:
        item_id = fallback_id

    raw_tags = item.get("tags") if item.get("tags") else item.get("item_tags", [])
    raw_aliases = item.get("aliases") if item.get("aliases") else item.get("item_aliases", [])

    return {
        "id": item_id,
        "label": _as_text(item.get("label") or item.get("word") or fallback_label),
        "note": _as_text(item.get("note") or item.get("item_note")),
        "tags": _split_text_list(raw_tags),
        "aliases": _split_text_list(raw_aliases),
        "meta": meta,
        "metadata_source": _as_text(
            item.get("metadata_source")
            or item.get("auto_status")
            or item.get("review_status")
            or DEFAULT_REVIEW_STATUS
        ),
    }


def _normalize_group(group: Any, index: int = 0) -> Dict[str, Any]:
    if isinstance(group, str):
        group = {"name": group}
    elif not isinstance(group, dict):
        group = {}

    grp_id = _as_text(group.get("id", "")) or f"grp_{index + 1}"
    name = _as_text(group.get("name") or group.get("group") or f"组{index + 1}")
    raw_tags = group.get("tags") if group.get("tags") else group.get("group_tags", [])
    raw_meta = group.get("meta") if isinstance(group.get("meta"), dict) else group.get("group_meta", {})
    items = [
        _normalize_item(item, fallback_id=f"slot_{index + 1}_{item_idx + 1}")
        for item_idx, item in enumerate(group.get("items", []))
    ]
    return {
        "id": grp_id,
        "name": name or DEFAULT_GROUP_NAME,
        "note": _as_text(group.get("note") or group.get("group_note")),
        "tags": _split_text_list(raw_tags),
        "meta": _normalize_meta(raw_meta),
        "items": items,
    }


def create_empty_wordlist_document(title: str = "未命名字表") -> Dict[str, Any]:
    return {
        "schema": WORDLIST_SCHEMA,
        "title": title,
        "note": "",
        "groups": [
            {
                "id": "",
                "name": DEFAULT_GROUP_NAME,
                "note": "",
                "tags": [],
                "meta": {},
                "items": [],
            }
        ],
    }


def normalize_wordlist_document(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raw = {}
    groups_raw = raw.get("groups", [])
    if not isinstance(groups_raw, list):
        groups_raw = []
    groups = [_normalize_group(group, idx) for idx, group in enumerate(groups_raw)]
    if not groups:
        groups = [_normalize_group(g, idx) for idx, g in enumerate(create_empty_wordlist_document().get("groups", []))]
    return {
        "schema": WORDLIST_SCHEMA,
        "title": _as_text(raw.get("title") or raw.get("wordlist_title") or "未命名字表"),
        "note": _as_text(raw.get("note") or raw.get("wordlist_note")),
        "groups": groups,
    }


def is_wordlist_v2_data(raw: Any) -> bool:
    """判断数据对象是否符合 PhonTracer v2 字表规范结构。"""
    if not isinstance(raw, dict):
        return False
    return raw.get("schema") == WORDLIST_SCHEMA or "groups" in raw


def merge_wordlist_documents(*docs: Any, title: Optional[str] = None) -> Dict[str, Any]:
    """合并多份字表文档，同名分组自动合并其标签、元数据与词项。"""
    merged_title = _as_text(title) or "合并字表"
    merged_groups: List[Dict[str, Any]] = []
    seen_groups: Dict[str, Dict[str, Any]] = {}
    for raw in docs:
        if not raw:
            continue
        doc = normalize_wordlist_document(raw)
        if not title and not merged_title and doc.get("title"):
            merged_title = doc.get("title")
        for group in doc.get("groups", []):
            g_name = _as_text(group.get("name")) or DEFAULT_GROUP_NAME
            if g_name not in seen_groups:
                new_group = {
                    "id": group.get("id", ""),
                    "name": g_name,
                    "note": group.get("note", ""),
                    "tags": list(group.get("tags", [])),
                    "meta": dict(group.get("meta", {})),
                    "items": list(group.get("items", [])),
                }
                seen_groups[g_name] = new_group
                merged_groups.append(new_group)
            else:
                target = seen_groups[g_name]
                for tag in group.get("tags", []):
                    if tag not in target["tags"]:
                        target["tags"].append(tag)
                target["meta"].update(group.get("meta", {}))
                target["items"].extend(group.get("items", []))
    if not merged_groups:
        merged_groups = create_empty_wordlist_document().get("groups", [])
    return normalize_wordlist_document({
        "schema": WORDLIST_SCHEMA,
        "title": merged_title,
        "note": "",
        "groups": merged_groups,
    })


def build_wordlist_document_from_items(
    items: Any,
    groups_order: Optional[List[str]] = None,
    base_doc: Optional[Dict[str, Any]] = None,
    title: Optional[str] = None,
) -> Dict[str, Any]:
    """从当前词条字典/列表（及可选的已有 wordlist_doc）构建规范化的 v2 .ptwl 文档。"""
    norm_base = normalize_wordlist_document(base_doc) if isinstance(base_doc, dict) else None

    if isinstance(items, dict):
        item_list = list(items.values())
    elif isinstance(items, (list, tuple)):
        item_list = list(items)
    else:
        item_list = []

    if not item_list and norm_base:
        if title and _as_text(title):
            norm_base = deepcopy(norm_base)
            norm_base["title"] = _as_text(title)
        return normalize_wordlist_document(norm_base)

    doc_title = _as_text(title)
    if not doc_title and norm_base:
        doc_title = _as_text(norm_base.get("title"))
    if not doc_title:
        for it in item_list:
            if isinstance(it, dict) and _as_text(it.get("wordlist_title")):
                doc_title = _as_text(it.get("wordlist_title"))
                break
    doc_title = doc_title or "未命名字表"
    doc_note = _as_text(norm_base.get("note")) if norm_base else ""

    base_group_lookup: Dict[str, Dict[str, Any]] = {}
    base_item_lookup: Dict[Tuple[str, str], Dict[str, Any]] = {}
    if norm_base:
        for grp in norm_base.get("groups", []):
            gname = _as_text(grp.get("name")) or DEFAULT_GROUP_NAME
            base_group_lookup[gname] = grp
            for git in grp.get("items", []):
                lbl = _as_text(git.get("label"))
                if lbl and (gname, lbl) not in base_item_lookup:
                    base_item_lookup[(gname, lbl)] = git

    groups_dict: Dict[str, Dict[str, Any]] = {}
    ordered_group_names: List[str] = []

    for gname in (groups_order or []):
        g_clean = _as_text(gname)
        if g_clean and g_clean not in ordered_group_names:
            ordered_group_names.append(g_clean)

    for it in item_list:
        if not isinstance(it, dict):
            continue
        g_clean = _as_text(it.get("group")) or DEFAULT_GROUP_NAME
        if g_clean not in ordered_group_names:
            ordered_group_names.append(g_clean)

    for g_clean in ordered_group_names:
        base_grp = base_group_lookup.get(g_clean, {})
        groups_dict[g_clean] = {
            "id": _as_text(base_grp.get("id", "")),
            "name": g_clean,
            "note": _as_text(base_grp.get("note", "")),
            "tags": list(base_grp.get("tags", [])),
            "meta": dict(base_grp.get("meta", {})),
            "items": [],
        }

    for idx, it in enumerate(item_list):
        if not isinstance(it, dict):
            continue
        label = _as_text(it.get("label") or it.get("word"))
        if not label:
            continue
        g_clean = _as_text(it.get("group")) or DEFAULT_GROUP_NAME
        grp = groups_dict.setdefault(
            g_clean,
            {"id": "", "name": g_clean, "note": "", "tags": [], "meta": {}, "items": []},
        )
        if not grp["note"] and _as_text(it.get("group_note")):
            grp["note"] = _as_text(it.get("group_note"))
        for gtag in _split_text_list(it.get("group_tags", [])):
            if gtag not in grp["tags"]:
                grp["tags"].append(gtag)
        if isinstance(it.get("group_meta"), dict):
            for gk, gv in _normalize_meta(it.get("group_meta")).items():
                grp["meta"].setdefault(gk, gv)

        base_it = base_item_lookup.get((g_clean, label), {})
        merged_meta: Dict[str, str] = {}
        if isinstance(base_it.get("meta"), dict):
            merged_meta.update(_normalize_meta(base_it.get("meta")))
        for mf in ("meta", "item_meta", "fields", "custom_fields"):
            if isinstance(it.get(mf), dict):
                merged_meta.update(_normalize_meta(it.get(mf)))

        item_tags = _split_text_list(it.get("tags") or it.get("item_tags") or base_it.get("tags") or [])
        item_aliases = _split_text_list(it.get("aliases") or it.get("item_aliases") or base_it.get("aliases") or [])
        item_note = _as_text(it.get("note") or it.get("item_note") or base_it.get("note") or "")
        metadata_source = _as_text(
            it.get("metadata_source")
            or it.get("review_status")
            or base_it.get("metadata_source")
            or DEFAULT_REVIEW_STATUS
        )

        grp["items"].append(
            _normalize_item(
                {
                    "id": _as_text(it.get("id") or base_it.get("id") or f"slot_{idx + 1}"),
                    "label": label,
                    "note": item_note,
                    "tags": item_tags,
                    "aliases": item_aliases,
                    "meta": merged_meta,
                    "metadata_source": metadata_source,
                }
            )
        )

    final_groups = [g for g in groups_dict.values() if g.get("items") or len(groups_dict) == 1]
    return normalize_wordlist_document(
        {
            "schema": WORDLIST_SCHEMA,
            "title": doc_title,
            "note": doc_note,
            "groups": final_groups,
        }
    )


def flatten_wordlist_document(doc: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[str], List[Dict[str, Any]]]:
    normalized = normalize_wordlist_document(doc)
    groups: List[Dict[str, Any]] = []
    flat_words: List[str] = []
    records: List[Dict[str, Any]] = []
    for group in normalized.get("groups", []):
        group_items = []
        group_meta = dict(group.get("meta", {}) or {})
        for item in group.get("items", []):
            label = _as_text(item.get("label"))
            if not label:
                continue
            group_items.append(label)
            flat_words.append(label)
            merged_meta = dict(group_meta)
            merged_meta.update(item.get("meta", {}) or {})
            records.append({
                "word": label,
                "label": label,
                "group": group.get("name", DEFAULT_GROUP_NAME),
                "group_note": group.get("note", ""),
                "group_tags": list(group.get("tags", [])),
                "group_meta": dict(group_meta),
                "item_note": item.get("note", ""),
                "item_tags": list(item.get("tags", [])),
                "item_aliases": list(item.get("aliases", [])),
                "item_meta": merged_meta,
                "note": item.get("note", ""),
                "tags": list(item.get("tags", [])),
                "aliases": list(item.get("aliases", [])),
                "meta": dict(merged_meta),
                "fields": dict(merged_meta),
                "custom_fields": dict(merged_meta),
                "metadata_source": item.get("metadata_source", DEFAULT_REVIEW_STATUS),
                "wordlist_version": "v2",
                "wordlist_title": normalized.get("title", ""),
            })
        if group_items:
            groups.append({
                "group": group.get("name", DEFAULT_GROUP_NAME),
                "items": group_items,
                "note": group.get("note", ""),
                "tags": list(group.get("tags", [])),
                "meta": dict(group_meta),
            })
    return groups, flat_words, records


def metadata_from_record(record: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not record:
        return {"wordlist_version": "v1"}
    item_note = _as_text(record.get("item_note") or record.get("note") or "")
    item_tags = list(record.get("item_tags") or record.get("tags") or [])
    item_aliases = list(record.get("item_aliases") or record.get("aliases") or [])
    item_meta = dict(
        record.get("item_meta")
        or record.get("meta")
        or record.get("fields")
        or record.get("custom_fields")
        or {}
    )
    group_note = _as_text(record.get("group_note") or "")
    group_tags = list(record.get("group_tags") or [])
    group_meta = dict(record.get("group_meta") or {})
    metadata_source = _as_text(record.get("metadata_source") or DEFAULT_REVIEW_STATUS)
    result = {
        "wordlist_version": "v2",
        "wordlist_title": record.get("wordlist_title", ""),
        "item_note": item_note,
        "item_tags": list(item_tags),
        "item_aliases": list(item_aliases),
        "item_meta": dict(item_meta),
        "group_note": group_note,
        "group_tags": list(group_tags),
        "group_meta": dict(group_meta),
        "metadata_source": metadata_source,
        "note": item_note,
        "tags": list(item_tags),
        "aliases": list(item_aliases),
        "meta": dict(item_meta),
        "fields": dict(item_meta),
        "custom_fields": dict(item_meta),
    }
    if record.get("group"):
        result["group"] = record["group"]
    if record.get("label") or record.get("word"):
        result["label"] = record.get("label") or record.get("word")
    return result


def apply_record_metadata(item: Dict[str, Any], record: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    item.update(metadata_from_record(record))
    return item


def load_wordlist_document(path: str) -> Dict[str, Any]:
    text = _read_text_file_with_fallback(path)
    raw = json.loads(text)
    return normalize_wordlist_document(raw)


def save_wordlist_file(doc_or_path: Any, path_or_doc: Any = None) -> Dict[str, Any]:
    """保存标准 v2 高级字表文档（支持 (doc, path) 或 (path, doc) 参数顺序）。"""
    if isinstance(doc_or_path, (str, os.PathLike)) and isinstance(path_or_doc, dict):
        path = os.fspath(doc_or_path)
        doc = path_or_doc
    else:
        doc = doc_or_path
        path = os.fspath(path_or_doc)
    normalized = normalize_wordlist_document(doc)
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(normalized, f, ensure_ascii=False, indent=2)
    return normalized


def save_wordlist_document(doc: Dict[str, Any], path: str) -> Dict[str, Any]:
    return save_wordlist_file(doc, path)


def summarize_wordlist_document(doc: Dict[str, Any]) -> Dict[str, int]:
    normalized = normalize_wordlist_document(doc)
    tag_set = set()
    ai_count = 0
    item_count = 0
    for group in normalized.get("groups", []):
        tag_set.update(group.get("tags", []))
        for item in group.get("items", []):
            if _as_text(item.get("label")):
                item_count += 1
            tag_set.update(item.get("tags", []))
            if item.get("metadata_source") == AI_REVIEW_STATUS:
                ai_count += 1
    return {
        "groups": len(normalized.get("groups", [])),
        "items": item_count,
        "tags": len(tag_set),
        "ai_pending": ai_count,
    }


def validate_wordlist_document(doc: Dict[str, Any], expected_count: Optional[int] = None) -> List[str]:
    normalized = normalize_wordlist_document(doc)
    warnings: List[str] = []
    seen_global = set()
    item_total = 0

    for group in normalized.get("groups", []):
        group_name = group.get("name", DEFAULT_GROUP_NAME)
        labels_in_group = set()
        if not group.get("items"):
            warnings.append(f"组“{group_name}”没有词项。")
        for tag in group.get("tags", []):
            if " " in tag:
                warnings.append(f"组“{group_name}”的标签“{tag}”包含空格，建议改成短标签。")
        for item in group.get("items", []):
            label = _as_text(item.get("label"))
            if not label:
                warnings.append(f"组“{group_name}”存在空词项。")
                continue
            item_total += 1
            if label in labels_in_group:
                warnings.append(f"组“{group_name}”内重复词项：“{label}”。")
            labels_in_group.add(label)
            if label in seen_global:
                warnings.append(f"整份字表存在重复词项：“{label}”。")
            seen_global.add(label)
            for tag in item.get("tags", []):
                if " " in tag:
                    warnings.append(f"词项“{label}”的标签“{tag}”包含空格，建议改成短标签。")
            if item.get("metadata_source") == AI_REVIEW_STATUS:
                warnings.append(f"词项“{label}”包含 AI 推断信息，导入前建议人工复核。")

    if expected_count is not None and item_total != expected_count:
        warnings.append(f"字表词项数为 {item_total}，当前音频/片段数为 {expected_count}，数量不一致。")
    return warnings


def mark_ai_fields_reviewed(doc: Dict[str, Any]) -> Dict[str, Any]:
    normalized = normalize_wordlist_document(deepcopy(doc))
    for group in normalized.get("groups", []):
        for item in group.get("items", []):
            if item.get("metadata_source") == AI_REVIEW_STATUS:
                item["metadata_source"] = REVIEWED_STATUS
    return normalized


def get_document_groups_for_runtime(doc: Dict[str, Any]) -> List[Dict[str, Any]]:
    """提取运行时所需分组列表（包含 group 与 items 字段）。"""
    groups, _, _ = flatten_wordlist_document(doc)
    return groups


def get_document_flat_words(doc: Dict[str, Any]) -> List[str]:
    """提取字表扁平词条列表。"""
    _, flat_words, _ = flatten_wordlist_document(doc)
    return flat_words


def iter_document_items(doc: Dict[str, Any]) -> Iterator[Tuple[Dict[str, Any], Dict[str, Any]]]:
    """迭代字表中所有的 (group, item) 二元组。"""
    normalized = normalize_wordlist_document(doc)
    for group in normalized.get("groups", []):
        for item in group.get("items", []):
            yield group, item


def find_first_matching_item(doc: Dict[str, Any], label: str) -> Optional[Dict[str, Any]]:
    """按词条主标签或别名检索首个匹配项。"""
    clean = _as_text(label)
    if not clean:
        return None
    for _, item in iter_document_items(doc):
        if item.get("label") == clean or clean in item.get("aliases", []):
            return item
    return None


find_First_matching_item = find_first_matching_item


def find_item_by_id(doc: Dict[str, Any], item_id: str) -> Optional[Dict[str, Any]]:
    """按词条唯一 ID 检索匹配项。"""
    clean_id = _as_text(item_id)
    if not clean_id:
        return None
    for _, item in iter_document_items(doc):
        if item.get("id") == clean_id:
            return item
    return None


def describe_wordlist_item(item: Dict[str, Any]) -> str:
    """生成词条的简要单行描述。"""
    label = _as_text(item.get("label", ""))
    note = _as_text(item.get("note", ""))
    tags = "；".join(item.get("tags", []))
    parts = [label]
    if tags:
        parts.append(f"[{tags}]")
    if note:
        parts.append(f"({note})")
    return " ".join(parts)
