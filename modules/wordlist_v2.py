import csv
import io
import json
import re
from copy import deepcopy
from typing import Any, Dict, List, Optional, Tuple

from .data_utils import parse_wordlist


WORDLIST_SCHEMA = "phontracer.wordlist.v2"
DEFAULT_REVIEW_STATUS = "人工填写"
AI_REVIEW_STATUS = "AI推断，需人工复核"
REVIEWED_STATUS = "已人工复核"

META_PINYIN = "拼音"
META_TONE = "声调"
META_RHYME = "韵母"
CORE_META_FIELDS = (META_PINYIN, META_TONE, META_RHYME)

ADVANCED_WORDLIST_AGENT_PROMPT = """你现在是 PhonTracer 高级字表整理 Agent。

你的任务不是立刻输出表格，而是先像研究助理一样和用户确认实验设计，再把材料整理成可导入 PhonTracer Toolkit 的高级字表内容。

工作流程必须分两阶段：

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
- 第一行可以用 `# title: 字表标题` 设置标题。
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
- 如果用户要求你“直接生成”，但信息明显不足，仍然先问 2-4 个关键问题。

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


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


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


def _normalize_meta(value: Any) -> Dict[str, str]:
    if not isinstance(value, dict):
        return {}
    meta = {}
    for key, val in value.items():
        k = _as_text(key)
        if k:
            meta[k] = _as_text(val)
    return meta


def _normalize_item(item: Any, fallback_label: str = "") -> Dict[str, Any]:
    if isinstance(item, str):
        item = {"label": item}
    elif not isinstance(item, dict):
        item = {}

    meta = _normalize_meta(item.get("meta", {}))
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

    return {
        "id": _as_text(item.get("id", "")),
        "label": _as_text(item.get("label") or item.get("word") or fallback_label),
        "note": _as_text(item.get("note") or item.get("item_note")),
        "tags": _split_text_list(item.get("tags", [])),
        "aliases": _split_text_list(item.get("aliases", [])),
        "meta": meta,
        "metadata_source": _as_text(item.get("metadata_source") or item.get("auto_status") or item.get("review_status") or DEFAULT_REVIEW_STATUS),
    }


def _normalize_group(group: Any, index: int = 0) -> Dict[str, Any]:
    if isinstance(group, str):
        group = {"name": group}
    elif not isinstance(group, dict):
        group = {}

    name = _as_text(group.get("name") or group.get("group") or f"组{index + 1}")
    items = [_normalize_item(item) for item in group.get("items", [])]
    return {
        "id": _as_text(group.get("id", "")),
        "name": name or "未分组",
        "note": _as_text(group.get("note") or group.get("group_note")),
        "tags": _split_text_list(group.get("tags", [])),
        "meta": _normalize_meta(group.get("meta", {})),
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
                "name": "未分组",
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
        groups = create_empty_wordlist_document().get("groups", [])
    return {
        "schema": WORDLIST_SCHEMA,
        "title": _as_text(raw.get("title") or "未命名字表"),
        "note": _as_text(raw.get("note")),
        "groups": groups,
    }


def build_document_from_v1_text(raw_text: str, title: str = "从普通字表导入") -> Dict[str, Any]:
    groups, _flat_words = parse_wordlist(raw_text or "")
    doc = create_empty_wordlist_document(title=title)
    doc["groups"] = []
    for group in groups:
        doc["groups"].append({
            "id": "",
            "name": _as_text(group.get("group")) or "未分组",
            "note": "",
            "tags": [],
            "meta": {},
            "items": [_normalize_item({"label": word}) for word in group.get("items", [])],
        })
    if not doc["groups"]:
        doc["groups"] = create_empty_wordlist_document().get("groups", [])
    return doc


def looks_like_structured_wordlist_text(text: str) -> bool:
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        lowered = line.lower()
        if lowered.startswith("@group") or lowered.startswith("@defaults") or re.match(r"^#\s*title\s*[:：]", line, flags=re.IGNORECASE):
            return True
    return False


def looks_like_csv_wordlist_text(text: str) -> bool:
    for raw_line in (text or "").splitlines():
        line = raw_line.strip().lstrip("\ufeff")
        if not line:
            continue
        lower = line.lower()
        if "," not in line:
            return False
        headers = [part.strip().strip('"').strip("'").lower() for part in line.split(",")]
        known_headers = {
            "group", "组名", "组别", "group_name",
            "label", "词项", "字词", "word", "item",
            "tags", "tag", "标签", "aliases", "别名",
            "pinyin", "拼音", "tone", "声调", "rhyme", "韵母",
        }
        return any(header in known_headers or header.startswith("meta.") for header in headers) or "label" in lower or "词项" in line
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
                "name": "未分组",
                "note": "",
                "tags": [],
                "meta": {},
                "items": [],
            }
            doc["groups"].append(current_group)
        return current_group

    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        title_match = re.match(r"^#\s*title\s*[:：]\s*(.+)$", line, flags=re.IGNORECASE)
        if title_match:
            doc["title"] = _as_text(title_match.group(1)) or doc["title"]
            continue
        if line.startswith("#"):
            continue

        parts = [part.strip() for part in line.split("|")]
        head = parts[0] if parts else ""
        attrs = _parse_structured_attrs(parts[1:])
        lowered = head.lower()

        if lowered.startswith("@group"):
            group_name = _as_text(head[len("@group"):]) or "未分组"
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


def load_wordlist_document(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    return normalize_wordlist_document(raw)


def save_wordlist_document(doc: Dict[str, Any], path: str) -> None:
    normalized = normalize_wordlist_document(doc)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(normalized, f, ensure_ascii=False, indent=2)


def flatten_wordlist_document(doc: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[str], List[Dict[str, Any]]]:
    normalized = normalize_wordlist_document(doc)
    groups: List[Dict[str, Any]] = []
    flat_words: List[str] = []
    records: List[Dict[str, Any]] = []
    for group in normalized.get("groups", []):
        group_items = []
        for item in group.get("items", []):
            label = _as_text(item.get("label"))
            if not label:
                continue
            group_items.append(label)
            flat_words.append(label)
            records.append({
                "word": label,
                "label": label,
                "group": group.get("name", "未分组"),
                "group_note": group.get("note", ""),
                "group_tags": list(group.get("tags", [])),
                "item_note": item.get("note", ""),
                "item_tags": list(item.get("tags", [])),
                "item_aliases": list(item.get("aliases", [])),
                "item_meta": dict(item.get("meta", {})),
                "metadata_source": item.get("metadata_source", DEFAULT_REVIEW_STATUS),
                "wordlist_version": "v2",
                "wordlist_title": normalized.get("title", ""),
            })
        if group_items:
            groups.append({
                "group": group.get("name", "未分组"),
                "items": group_items,
                "note": group.get("note", ""),
                "tags": list(group.get("tags", [])),
            })
    return groups, flat_words, records


def metadata_from_record(record: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not record:
        return {"wordlist_version": "v1"}
    return {
        "wordlist_version": "v2",
        "wordlist_title": record.get("wordlist_title", ""),
        "item_note": record.get("item_note", ""),
        "item_tags": list(record.get("item_tags", [])),
        "item_aliases": list(record.get("item_aliases", [])),
        "item_meta": dict(record.get("item_meta", {})),
        "group_note": record.get("group_note", ""),
        "group_tags": list(record.get("group_tags", [])),
        "metadata_source": record.get("metadata_source", DEFAULT_REVIEW_STATUS),
    }


def apply_record_metadata(item: Dict[str, Any], record: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    item.update(metadata_from_record(record))
    return item


def document_to_v1_text(doc: Dict[str, Any]) -> str:
    normalized = normalize_wordlist_document(doc)
    lines: List[str] = []
    for group in normalized.get("groups", []):
        labels = [_as_text(item.get("label")) for item in group.get("items", []) if _as_text(item.get("label"))]
        if not labels:
            continue
        lines.append(f"【{group.get('name', '未分组')}】")
        lines.append(" ".join(labels))
    return "\n".join(lines).strip()


def build_document_from_csv_text(csv_text: str, title: str = "从表格导入") -> Dict[str, Any]:
    stream = io.StringIO(csv_text or "")
    reader = csv.DictReader(stream)
    doc = create_empty_wordlist_document(title=title)
    doc["groups"] = []
    group_lookup: Dict[str, Dict[str, Any]] = {}

    for row in reader:
        group_name = _as_text(row.get("group") or row.get("组名") or row.get("组别") or row.get("group_name")) or "未分组"
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

        meta: Dict[str, str] = {}
        consumed = {
            "group", "组名", "组别", "group_name", "group_note", "组备注",
            "group_tags", "组tag", "组标签", "label", "词项", "字词", "word", "item",
            "item_note", "词项备注", "备注", "note", "tags", "tag", "标签",
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
        for csv_key, meta_key in (("pinyin", META_PINYIN), ("拼音", META_PINYIN), ("tone", META_TONE), ("声调", META_TONE), ("rhyme", META_RHYME), ("韵母", META_RHYME)):
            if _as_text(row.get(csv_key)):
                meta[meta_key] = _as_text(row.get(csv_key))

        group["items"].append(_normalize_item({
            "label": label,
            "note": row.get("item_note") or row.get("词项备注") or row.get("备注") or row.get("note"),
            "tags": row.get("tags") or row.get("tag") or row.get("标签"),
            "aliases": row.get("aliases") or row.get("别名"),
            "meta": meta,
            "metadata_source": row.get("metadata_source") or row.get("自动补全状态") or row.get("review_status") or row.get("复核状态") or DEFAULT_REVIEW_STATUS,
        }))

    if not doc["groups"]:
        doc["groups"] = create_empty_wordlist_document().get("groups", [])
    return normalize_wordlist_document(doc)


def document_to_csv_text(doc: Dict[str, Any]) -> str:
    normalized = normalize_wordlist_document(doc)
    meta_keys = []
    for group in normalized.get("groups", []):
        for item in group.get("items", []):
            for key in item.get("meta", {}).keys():
                if key not in meta_keys:
                    meta_keys.append(key)

    headers = [
        "组名", "组备注", "组标签", "词项", "词项备注", "标签",
        "别名", "复核状态",
    ] + meta_keys

    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=headers, lineterminator="\n")
    writer.writeheader()
    for group in normalized.get("groups", []):
        for item in group.get("items", []):
            meta = item.get("meta", {})
            row = {
                "组名": group.get("name", ""),
                "组备注": group.get("note", ""),
                "组标签": ";".join(group.get("tags", [])),
                "词项": item.get("label", ""),
                "词项备注": item.get("note", ""),
                "标签": ";".join(item.get("tags", [])),
                "别名": ";".join(item.get("aliases", [])),
                "复核状态": item.get("metadata_source", DEFAULT_REVIEW_STATUS),
            }
            for key in meta_keys:
                row[key] = meta.get(key, "")
            writer.writerow(row)
    return stream.getvalue()


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
        group_name = group.get("name", "未分组")
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
