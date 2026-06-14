from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import textgrid

from .data_utils import has_cjk, split_into_syllables


DEFAULT_GROUP_NAME = "导入内容"
NONE_TIER_LABEL = "无"
PAIR_MODE_NONE = "none"
PAIR_MODE_ADJACENT = "adjacent"
_EPS = 1e-7


@dataclass
class TextGridMapping:
    item_tier: str
    core_tier: str
    group_tier: Optional[str] = None
    pair_mode: str = PAIR_MODE_NONE


@dataclass
class ConvertedTextGridItem:
    id: str
    label: str
    group: str
    source_start: float
    source_end: float
    core_start: float
    core_end: float
    char_bounds: List[Tuple[float, float, str]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    source_ids: List[str] = field(default_factory=list)
    match_note: str = ""

    def to_row(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "group": self.group,
            "source_start": self.source_start,
            "source_end": self.source_end,
            "core_start": self.core_start,
            "core_end": self.core_end,
            "char_count": len(self.char_bounds),
            "warnings": list(self.warnings),
            "match_note": self.match_note,
        }


@dataclass
class ConversionPreview:
    items: List[ConvertedTextGridItem]
    warnings: List[str] = field(default_factory=list)
    tone_pair_report: Dict[str, Any] = field(default_factory=dict)

    def rows(self) -> List[Dict[str, Any]]:
        return [item.to_row() for item in self.items]


def inspect_textgrid(path: str) -> Dict[str, Any]:
    """读取 TextGrid 层级摘要，供 Toolkit 页面展示和自动推荐。"""
    tg = textgrid.TextGrid.fromFile(path)
    tiers = []
    for tier in getattr(tg, "tiers", []):
        is_interval = isinstance(tier, textgrid.IntervalTier)
        labels = []
        non_empty = 0
        if is_interval:
            for interval in tier:
                label = _clean_label(getattr(interval, "mark", ""))
                if label:
                    non_empty += 1
                    if label not in labels and len(labels) < 8:
                        labels.append(label)
        tiers.append({
            "name": getattr(tier, "name", ""),
            "type": "IntervalTier" if is_interval else type(tier).__name__,
            "count": len(tier) if hasattr(tier, "__len__") else 0,
            "non_empty_count": non_empty,
            "sample_labels": labels,
            "min_time": float(getattr(tier, "minTime", 0.0) or 0.0),
            "max_time": float(getattr(tier, "maxTime", getattr(tg, "maxTime", 0.0)) or 0.0),
            "supported": is_interval,
        })
    return {
        "path": path,
        "min_time": float(getattr(tg, "minTime", 0.0) or 0.0),
        "max_time": float(getattr(tg, "maxTime", 0.0) or 0.0),
        "tiers": tiers,
    }


def preview_textgrid_conversion(
    path: str,
    mapping: TextGridMapping | Dict[str, Any],
    group_overrides: Optional[Dict[str, str]] = None,
    wordlist_records: Optional[Sequence[Dict[str, Any]]] = None,
) -> ConversionPreview:
    """按用户指定层映射生成转换预览，不写文件。"""
    tg = textgrid.TextGrid.fromFile(path)
    normalized = _normalize_mapping(mapping)
    warnings = _validate_mapping(tg, normalized)
    item_tier = _interval_tier_by_name(tg, normalized.item_tier)
    core_tier = _interval_tier_by_name(tg, normalized.core_tier)
    group_tier = _interval_tier_by_name(tg, normalized.group_tier) if normalized.group_tier else None

    base_items = _build_base_items(
        item_tier=item_tier,
        core_tier=core_tier,
        group_tier=group_tier,
        group_overrides=group_overrides or {},
        wordlist_records=wordlist_records,
    )
    if normalized.pair_mode == PAIR_MODE_ADJACENT:
        items = _pair_adjacent_items(base_items, group_overrides or {}, wordlist_records=wordlist_records)
    else:
        items = base_items

    conversion_warnings = list(warnings)
    for item in items:
        conversion_warnings.extend(f"{item.label}: {msg}" for msg in item.warnings)

    return ConversionPreview(
        items=items,
        warnings=conversion_warnings,
        tone_pair_report=diagnose_tone_pair_support(items),
    )


def convert_textgrid(
    path: str,
    out_path: str,
    mapping: TextGridMapping | Dict[str, Any],
    group_overrides: Optional[Dict[str, str]] = None,
    wordlist_records: Optional[Sequence[Dict[str, Any]]] = None,
) -> ConversionPreview:
    """转换并写出 PhonTracer 标准 TextGrid，返回同一份预览/诊断结果。"""
    preview = preview_textgrid_conversion(path, mapping, group_overrides, wordlist_records=wordlist_records)
    tg_src = textgrid.TextGrid.fromFile(path)
    max_time = max(
        float(getattr(tg_src, "maxTime", 0.0) or 0.0),
        max((item.core_end for item in preview.items), default=0.0),
        1.0,
    )
    out_tg = build_standard_textgrid(preview.items, max_time=max_time)
    out_tg.write(out_path)
    return preview


def build_standard_textgrid(items: Sequence[ConvertedTextGridItem], max_time: Optional[float] = None):
    """根据转换条目构造标准 groups / words / chars 三层 TextGrid。"""
    sorted_items = sorted(items, key=lambda item: (item.core_start, item.core_end, item.label))
    resolved_max = max_time if max_time is not None else max((item.core_end for item in sorted_items), default=1.0)
    resolved_max = max(float(resolved_max or 0.0), 1.0)
    tg = textgrid.TextGrid(maxTime=resolved_max)

    groups_tier = textgrid.IntervalTier(name="groups", minTime=0.0, maxTime=resolved_max)
    words_tier = textgrid.IntervalTier(name="words", minTime=0.0, maxTime=resolved_max)
    chars_tier = textgrid.IntervalTier(name="chars", minTime=0.0, maxTime=resolved_max)

    _fill_item_tier(groups_tier, sorted_items, lambda item: item.group, resolved_max)
    _fill_item_tier(words_tier, sorted_items, lambda item: item.label, resolved_max)
    _fill_char_tier(chars_tier, sorted_items, resolved_max)

    tg.append(groups_tier)
    tg.append(words_tier)
    tg.append(chars_tier)
    return tg


def diagnose_tone_pair_support(items: Sequence[ConvertedTextGridItem]) -> Dict[str, Any]:
    """检查转换结果是否能触发现有二字组调类图表。"""
    eligible = []
    invalid_labels = []
    invalid_groups = []
    fronts = set()
    backs = set()
    combos = set()

    for item in items:
        syllables = split_into_syllables(item.label)
        if len(syllables) != 2:
            invalid_labels.append(item.label)
            continue
        front, back = split_tone_pair(item.group)
        if not front or not back:
            invalid_groups.append(item.group)
            continue
        eligible.append(item.id)
        fronts.add(front)
        backs.add(back)
        combos.add((front, back))

    supported = len(fronts) >= 2 and len(backs) >= 2 and len(combos) >= 4
    messages = []
    if not eligible:
        messages.append("当前没有可识别的二字组条目。")
    if invalid_labels:
        messages.append(f"{len(invalid_labels)} 个条目标签不能拆成两个字/音节。")
    if invalid_groups:
        messages.append(f"{len(invalid_groups)} 个条目的组名不符合“前字调类+后字调类”。")
    if eligible and not supported:
        messages.append("二字组数量或调类组合不足，后续调类效应图可能不会显示。")
    if supported:
        messages.append("二字组条件充足，可供后续调类效应图识别。")

    return {
        "supported": supported,
        "eligible_count": len(eligible),
        "front_count": len(fronts),
        "back_count": len(backs),
        "combo_count": len(combos),
        "invalid_label_count": len(invalid_labels),
        "invalid_group_count": len(invalid_groups),
        "messages": messages,
    }


def split_tone_pair(group: str) -> Tuple[Optional[str], Optional[str]]:
    text = str(group or "").replace("＋", "+").replace("/", "+").replace("、", "+")
    if "+" not in text:
        return None, None
    parts = [part.strip() for part in text.split("+") if part.strip()]
    if len(parts) < 2:
        return None, None
    return parts[0], parts[1]


def recommend_tier_names(summary: Dict[str, Any]) -> Dict[str, Optional[str]]:
    """根据层名和非空标签数量给 UI 一个可覆盖的默认层选择。"""
    tiers = [tier for tier in summary.get("tiers", []) if tier.get("supported")]
    by_lower = {str(tier.get("name", "")).strip().lower(): tier.get("name") for tier in tiers}

    def pick(candidates: Iterable[str], fallback_index: int = 0):
        for candidate in candidates:
            name = by_lower.get(candidate.lower())
            if name:
                return name
        if len(tiers) > fallback_index:
            return tiers[fallback_index].get("name")
        return None

    group_tier = None
    for candidate in ("groups", "group", "组", "组别"):
        group_tier = by_lower.get(candidate.lower())
        if group_tier:
            break

    return {
        "group_tier": group_tier,
        "item_tier": pick(("words", "word", "items", "item", "字", "词", "句"), fallback_index=0),
        "core_tier": pick(("core", "cores", "核心", "vowel", "vowels", "nucleus", "韵母"), fallback_index=1 if len(tiers) > 1 else 0),
    }


def _normalize_mapping(mapping: TextGridMapping | Dict[str, Any]) -> TextGridMapping:
    if isinstance(mapping, TextGridMapping):
        normalized = mapping
    else:
        normalized = TextGridMapping(
            item_tier=str(mapping.get("item_tier") or ""),
            core_tier=str(mapping.get("core_tier") or ""),
            group_tier=mapping.get("group_tier") or None,
            pair_mode=str(mapping.get("pair_mode") or PAIR_MODE_NONE),
        )
    if normalized.group_tier in ("", NONE_TIER_LABEL):
        normalized.group_tier = None
    if normalized.pair_mode not in {PAIR_MODE_NONE, PAIR_MODE_ADJACENT}:
        normalized.pair_mode = PAIR_MODE_NONE
    return normalized


def _validate_mapping(tg, mapping: TextGridMapping) -> List[str]:
    warnings = []
    if not mapping.item_tier:
        raise ValueError("必须指定条目层。")
    if not mapping.core_tier:
        raise ValueError("必须指定核心层。")
    _interval_tier_by_name(tg, mapping.item_tier)
    _interval_tier_by_name(tg, mapping.core_tier)
    if mapping.group_tier:
        _interval_tier_by_name(tg, mapping.group_tier)
    if mapping.item_tier == mapping.core_tier:
        warnings.append("条目层和核心层相同，转换会把条目区间直接作为分析边界。")
    return warnings


def _interval_tier_by_name(tg, name: Optional[str]):
    if not name:
        return None
    for tier in getattr(tg, "tiers", []):
        if getattr(tier, "name", "") == name:
            if not isinstance(tier, textgrid.IntervalTier):
                raise ValueError(f"层“{name}”不是 IntervalTier，暂不支持转换。")
            return tier
    raise ValueError(f"TextGrid 中找不到层“{name}”。")


def _build_wordlist_index(
    records: Optional[Sequence[Dict[str, Any]]],
    pair_only: bool = False,
) -> Dict[str, Dict[str, Any]]:
    index: Dict[str, Dict[str, Any]] = {}
    for record in records or []:
        if not isinstance(record, dict):
            continue
        main_label = _clean_label(record.get("label") or record.get("word") or record.get("词项"))
        if not main_label:
            continue
        group = _clean_label(record.get("group") or record.get("组名") or record.get("组别")) or DEFAULT_GROUP_NAME
        normalized = dict(record)
        normalized["label"] = main_label
        normalized["group"] = group

        labels = [main_label]
        aliases = record.get("item_aliases") or record.get("aliases") or record.get("别名") or []
        if isinstance(aliases, str):
            aliases = [part.strip() for part in re.split(r"[;；,，、\s]+", aliases) if part.strip()]
        for alias in aliases:
            text = _clean_label(alias)
            if text:
                labels.append(text)

        for label in labels:
            if pair_only and len(split_into_syllables(label)) != 2:
                continue
            for key in _wordlist_keys(label):
                index.setdefault(key, normalized)
    return index


def _match_wordlist_record(label: str, index: Dict[str, Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not label or not index:
        return None
    for key in _wordlist_keys(label):
        record = index.get(key)
        if record:
            return record
    return None


def _wordlist_keys(label: str) -> List[str]:
    text = _clean_label(label)
    if not text:
        return []
    keys = []
    syllables = split_into_syllables(text)
    if syllables:
        keys.append("".join(syllables))
        keys.append("/".join(syllables))
    compact = re.sub(r"[\s/_\-＋+、，,;；]+", "", text)
    if compact:
        keys.append(compact)
    keys.append(text)
    normalized = []
    for key in keys:
        cleaned = key.strip().lower()
        if cleaned and cleaned not in normalized:
            normalized.append(cleaned)
    return normalized


def _build_base_items(
    item_tier,
    core_tier,
    group_tier=None,
    group_overrides: Optional[Dict[str, str]] = None,
    wordlist_records: Optional[Sequence[Dict[str, Any]]] = None,
):
    group_overrides = group_overrides or {}
    wordlist_index = _build_wordlist_index(wordlist_records)
    core_intervals = [_interval_tuple(interval) for interval in core_tier if _clean_label(getattr(interval, "mark", ""))]
    group_intervals = [_interval_tuple(interval) for interval in group_tier] if group_tier else []
    converted = []

    for index, interval in enumerate(item_tier):
        label = _clean_label(getattr(interval, "mark", ""))
        if not label:
            continue
        item_id = str(index)
        source_start = float(interval.minTime)
        source_end = float(interval.maxTime)
        warnings = []
        matching_core = _overlapping_by_center(core_intervals, source_start, source_end)
        if not matching_core:
            matching_core = [(source_start, source_end, label)]
            warnings.append("未找到对应核心区间，已回退到条目层边界。")

        clipped_core = []
        for c_start, c_end, c_label in matching_core:
            clipped_start = max(source_start, float(c_start))
            clipped_end = min(source_end, float(c_end))
            if clipped_start > float(c_start) + _EPS or clipped_end < float(c_end) - _EPS:
                warnings.append("核心区间超出条目层边界，已裁剪。")
            if clipped_end > clipped_start:
                clipped_core.append((clipped_start, clipped_end, c_label))
        if not clipped_core:
            clipped_core = [(source_start, source_end, label)]
            warnings.append("核心区间裁剪后为空，已回退到条目层边界。")

        core_start = min(part[0] for part in clipped_core)
        core_end = max(part[1] for part in clipped_core)
        group = _resolve_group(group_intervals, source_start, source_end)
        match_note = ""
        if group == DEFAULT_GROUP_NAME:
            record = _match_wordlist_record(label, wordlist_index)
            if record and record.get("group"):
                group = str(record.get("group")).strip()
                match_note = "按辅助字表补组"
        group = _override_group(item_id, label, group, group_overrides)
        char_bounds = _build_char_bounds(label, core_start, core_end, clipped_core, warnings)
        converted.append(ConvertedTextGridItem(
            id=item_id,
            label=label,
            group=group,
            source_start=source_start,
            source_end=source_end,
            core_start=core_start,
            core_end=core_end,
            char_bounds=char_bounds,
            warnings=warnings,
            source_ids=[item_id],
            match_note=match_note,
        ))
    return converted


def _pair_adjacent_items(
    items: Sequence[ConvertedTextGridItem],
    group_overrides: Dict[str, str],
    wordlist_records: Optional[Sequence[Dict[str, Any]]] = None,
):
    wordlist_index = _build_wordlist_index(wordlist_records, pair_only=True)
    if wordlist_index:
        return _pair_items_by_wordlist(items, group_overrides, wordlist_index)

    grouped: Dict[str, List[ConvertedTextGridItem]] = {}
    order = []
    for item in sorted(items, key=lambda value: (value.group, value.core_start, value.core_end)):
        if item.group not in grouped:
            grouped[item.group] = []
            order.append(item.group)
        grouped[item.group].append(item)

    paired = []
    for group in order:
        values = sorted(grouped[group], key=lambda value: (value.core_start, value.core_end))
        index = 0
        while index < len(values):
            first = values[index]
            if index + 1 >= len(values):
                leftover = _copy_item(first)
                leftover.warnings.append("相邻两字合并模式下没有可配对的后一项，已保留为单字条目。")
                paired.append(leftover)
                break
            second = values[index + 1]
            pair_id = f"{first.id}+{second.id}"
            label = _join_pair_label(first.label, second.label)
            pair_group = _override_group(pair_id, label, group, group_overrides)
            warnings = list(first.warnings) + list(second.warnings)
            syllables = split_into_syllables(label)
            if len(syllables) != 2:
                warnings.append("合并后的标签不能拆成两个字/音节，二字组图表可能无法识别。")
            paired.append(ConvertedTextGridItem(
                id=pair_id,
                label=label,
                group=pair_group,
                source_start=min(first.source_start, second.source_start),
                source_end=max(first.source_end, second.source_end),
                core_start=min(first.core_start, second.core_start),
                core_end=max(first.core_end, second.core_end),
                char_bounds=[
                    (first.core_start, first.core_end, first.label),
                    (second.core_start, second.core_end, second.label),
                ],
                warnings=warnings,
                source_ids=list(first.source_ids) + list(second.source_ids),
            ))
            index += 2
    return sorted(paired, key=lambda value: (value.core_start, value.core_end))


def _pair_items_by_wordlist(
    items: Sequence[ConvertedTextGridItem],
    group_overrides: Dict[str, str],
    wordlist_index: Dict[str, Dict[str, Any]],
):
    has_meaningful_group = any(item.group and item.group != DEFAULT_GROUP_NAME for item in items)
    grouped: Dict[str, List[ConvertedTextGridItem]] = {}
    order = []
    for item in sorted(items, key=lambda value: (value.group if has_meaningful_group else "", value.core_start, value.core_end)):
        key = item.group if has_meaningful_group else "__all__"
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].append(item)

    paired = []
    for key in order:
        values = sorted(grouped[key], key=lambda value: (value.core_start, value.core_end))
        index = 0
        while index < len(values):
            first = values[index]
            if index + 1 >= len(values):
                leftover = _copy_item(first)
                leftover.warnings.append("辅助字表中没有可匹配的后一字二字组，已保留为单字条目。")
                paired.append(leftover)
                break

            second = values[index + 1]
            label = _join_pair_label(first.label, second.label)
            record = _match_wordlist_record(label, wordlist_index)
            if not record:
                leftover = _copy_item(first)
                leftover.warnings.append("相邻后一项未命中字表二字词项，已保留为单字条目。")
                paired.append(leftover)
                index += 1
                continue

            pair_id = f"{first.id}+{second.id}"
            pair_group = str(record.get("group") or first.group or DEFAULT_GROUP_NAME).strip() or DEFAULT_GROUP_NAME
            pair_group = _override_group(pair_id, record.get("label") or label, pair_group, group_overrides)
            warnings = list(first.warnings) + list(second.warnings)
            matched_label = str(record.get("label") or label).strip() or label
            syllables = split_into_syllables(matched_label)
            if len(syllables) != 2:
                matched_label = label
                syllables = split_into_syllables(matched_label)
            if len(syllables) != 2:
                warnings.append("字表匹配到的标签不能拆成两个字/音节，已使用相邻标签合并。")
            paired.append(ConvertedTextGridItem(
                id=pair_id,
                label=matched_label,
                group=pair_group,
                source_start=min(first.source_start, second.source_start),
                source_end=max(first.source_end, second.source_end),
                core_start=min(first.core_start, second.core_start),
                core_end=max(first.core_end, second.core_end),
                char_bounds=[
                    (first.core_start, first.core_end, first.label),
                    (second.core_start, second.core_end, second.label),
                ],
                warnings=warnings,
                source_ids=list(first.source_ids) + list(second.source_ids),
                match_note="按辅助字表合并",
            ))
            index += 2
    return sorted(paired, key=lambda value: (value.core_start, value.core_end))


def _copy_item(item: ConvertedTextGridItem):
    return ConvertedTextGridItem(
        id=item.id,
        label=item.label,
        group=item.group,
        source_start=item.source_start,
        source_end=item.source_end,
        core_start=item.core_start,
        core_end=item.core_end,
        char_bounds=list(item.char_bounds),
        warnings=list(item.warnings),
        source_ids=list(item.source_ids),
        match_note=item.match_note,
    )


def _join_pair_label(first: str, second: str) -> str:
    if has_cjk(first) and has_cjk(second):
        return f"{first}{second}"
    return f"{first}/{second}"


def _build_char_bounds(label: str, core_start: float, core_end: float, core_parts, warnings: List[str]):
    syllables = split_into_syllables(label)
    if not syllables:
        return [(core_start, core_end, label)]
    if len(syllables) == 1:
        return [(core_start, core_end, syllables[0])]

    if len(core_parts) == len(syllables):
        return [(float(start), float(end), syllables[index]) for index, (start, end, _mark) in enumerate(core_parts)]

    warnings.append("核心区间数量与字/音节数不一致，chars 层已在核心范围内等分。")
    splits = np.linspace(core_start, core_end, len(syllables) + 1).tolist()
    return [(splits[index], splits[index + 1], syllables[index]) for index in range(len(syllables))]


def _resolve_group(group_intervals, start: float, end: float) -> str:
    center = (float(start) + float(end)) / 2.0
    for g_start, g_end, label in group_intervals:
        if float(g_start) <= center <= float(g_end) and label:
            return label
    return DEFAULT_GROUP_NAME


def _override_group(item_id: str, label: str, current: str, group_overrides: Dict[str, str]) -> str:
    for key in (item_id, label):
        value = group_overrides.get(key)
        if value and str(value).strip():
            return str(value).strip()
    return current or DEFAULT_GROUP_NAME


def _overlapping_by_center(intervals, start: float, end: float):
    matched = []
    for c_start, c_end, label in intervals:
        center = (float(c_start) + float(c_end)) / 2.0
        if float(start) <= center <= float(end):
            matched.append((float(c_start), float(c_end), label))
    return sorted(matched, key=lambda value: (value[0], value[1]))


def _interval_tuple(interval):
    return (
        float(getattr(interval, "minTime", 0.0)),
        float(getattr(interval, "maxTime", 0.0)),
        _clean_label(getattr(interval, "mark", "")),
    )


def _clean_label(value: Any) -> str:
    return str(value or "").strip()


def _fill_item_tier(tier, items: Sequence[ConvertedTextGridItem], label_func, max_time: float):
    last_end = 0.0
    for item in items:
        start = max(0.0, float(item.core_start))
        end = min(max_time, float(item.core_end))
        if start > last_end + _EPS:
            tier.add(last_end, start, "")
            last_end = start
        if end <= last_end + _EPS:
            continue
        tier.add(last_end, end, str(label_func(item) or ""))
        last_end = end
    if max_time > last_end + _EPS:
        tier.add(last_end, max_time, "")


def _fill_char_tier(tier, items: Sequence[ConvertedTextGridItem], max_time: float):
    last_end = 0.0
    for item in items:
        for c_start, c_end, label in sorted(item.char_bounds, key=lambda value: (value[0], value[1])):
            start = max(0.0, float(c_start))
            end = min(max_time, float(c_end))
            if start > last_end + _EPS:
                tier.add(last_end, start, "")
                last_end = start
            if end <= last_end + _EPS:
                continue
            tier.add(last_end, end, str(label or ""))
            last_end = end
    if max_time > last_end + _EPS:
        tier.add(last_end, max_time, "")
