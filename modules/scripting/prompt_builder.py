# -*- coding: utf-8 -*-
"""
PhonTracer 自定义脚本 AI 提示词动态组装器与工程画像摘要提取器。
"""

from collections import Counter

from .prompt_templates import (
    CORE_CHART_API_CONTRACT,
    REFERENCE_CHART_RULE,
    SCRIPT_PROMPT_KEY_ALIASES,
    SPECTROGRAM_SCRIPT_GUIDE,
    VALID_SCRIPT_PROMPT_KEYS,
    _build_agent_detail_appendix,
    _build_code_skeleton,
    _build_data_process_detail_appendix,
)


def _format_counter(counter, empty="无", limit=None):
    if not counter:
        return empty
    items = sorted(counter.items(), key=lambda x: str(x[0]))
    if limit is not None and len(items) > limit:
        shown = items[:limit]
        rest = len(items) - limit
        return ", ".join(f"{name} ({count}条)" for name, count in shown) + f", 其余 {rest} 类已省略"
    return ", ".join(f"{name} ({count}条)" for name, count in items)


def _format_sequence(values, empty="无", limit=None):
    if not values:
        return empty
    values = [str(v) for v in values]
    if limit is not None and len(values) > limit:
        shown = ", ".join(values[:limit])
        return f"{shown}, 其余 {len(values) - limit} 项已省略"
    return ", ".join(values)


def _truncate_text(value, limit=80):
    text = str(value or "").replace("\n", " ").replace("\r", " ").strip()
    if limit is not None and len(text) > limit:
        return text[: max(0, limit - 1)] + "…"
    return text


def _as_text_list(value, limit_each=32):
    if not value:
        return []
    if isinstance(value, (list, tuple, set)):
        raw_values = value
    else:
        raw_values = str(value).replace("，", ",").replace("；", ",").replace(";", ",").split(",")
    result = []
    for item in raw_values:
        text = _truncate_text(item, limit_each)
        if text:
            result.append(text)
    return result


def _format_value_list(values, empty="无", limit=8):
    clean_values = []
    seen = set()
    for value in values or []:
        text = _truncate_text(value, 32)
        if text and text not in seen:
            clean_values.append(text)
            seen.add(text)
    return _format_sequence(clean_values, empty=empty, limit=limit)


def _format_meta_pairs(meta, limit=8):
    if not isinstance(meta, dict) or not meta:
        return "无"
    pairs = []
    for key in sorted(meta.keys(), key=str):
        key_text = _truncate_text(key, 24)
        value_text = _truncate_text(meta.get(key), 40)
        if key_text and value_text:
            pairs.append(f"{key_text}={value_text}")
    return _format_sequence(pairs, limit=limit)


def validate_and_normalize_script_prompt_params(raw_params):
    """
    校验并归一化 script_prompt 参数白名单。
    遇到未知的 key 时抛出 ValueError 并附带合法参数列表。
    """
    if not isinstance(raw_params, dict):
        raise ValueError(f"Invalid parameters dictionary. Valid parameters are: {', '.join(VALID_SCRIPT_PROMPT_KEYS)}")

    normalized = {
        "script_type": "chart",
        "prompt_mode": "参数选项",
        "goal": "绘制 F0 曲线图",
        "data_range": "只使用纳入分析的条目",
        "group_by": "按声调/分组",
        "chart_style": "折线图",
        "x_axis": "归一化时间 0-1",
        "y_axis": "F0 Hz",
        "stats": ["绘制均值", "绘制标准差阴影", "忽略 NaN"],
        "title": "自定义图表",
        "filename": "custom_chart.png",
        "img_format": "png",
        "output_table": False,
        "show_legend": True,
        "use_chinese": True,
        "custom_desc": "",
        "template": "",
        "output_type": "",
        "scope": "active",
        "analysis_mode": "",
        "user_request": "",
        "out": "",
    }

    explicit_goal = False
    explicit_axes = False

    for raw_k, raw_v in raw_params.items():
        k_clean = str(raw_k).strip().lower()
        canonical = SCRIPT_PROMPT_KEY_ALIASES.get(k_clean)
        if not canonical:
            raise ValueError(
                f"Unknown parameter: {raw_k}. Valid parameters are: {', '.join(VALID_SCRIPT_PROMPT_KEYS)}"
            )

        if canonical in ("purpose", "goal"):
            normalized["goal"] = str(raw_v)
            explicit_goal = True
        elif canonical in ("x_axis", "y_axis"):
            normalized[canonical] = str(raw_v)
            explicit_axes = True
        elif canonical == "stats":
            if isinstance(raw_v, (list, tuple)):
                normalized["stats"] = [str(s).strip() for s in raw_v if str(s).strip()]
            else:
                normalized["stats"] = [s.strip() for s in str(raw_v).split(",") if s.strip()]
        elif canonical in ("output_table", "show_legend", "agent_detailed_project_summary", "agent_include_project_summary"):
            if isinstance(raw_v, bool):
                normalized[canonical] = raw_v
            else:
                normalized[canonical] = str(raw_v).strip().lower() in ("true", "1", "yes", "y", "是")
        elif canonical == "use_chinese":
            if isinstance(raw_v, bool):
                normalized["use_chinese"] = raw_v
            else:
                v_l = str(raw_v).strip().lower()
                normalized["use_chinese"] = v_l not in ("false", "0", "no", "en", "english", "否")
        elif canonical in ("agent_chart_count", "agent_plan_count"):
            try:
                normalized[canonical] = int(raw_v)
            except (TypeError, ValueError):
                normalized[canonical] = raw_v
        elif canonical == "template":
            tpl = str(raw_v).strip()
            normalized["template"] = tpl
            tpl_l = tpl.lower()
            if tpl_l in ("data_process", "patch", "clean", "process"):
                normalized["script_type"] = "data_process"
            elif tpl_l in ("formant", "formant_space", "vowel_space", "f1_f2"):
                normalized["analysis_mode"] = normalized["analysis_mode"] or "formant"
                if not explicit_goal:
                    normalized["goal"] = "绘制 F1/F2 元音空间图"
                normalized["chart_style"] = "散点图 + 置信椭圆"
                if not explicit_axes:
                    normalized["x_axis"] = "F2 Hz"
                    normalized["y_axis"] = "F1 Hz"
            elif tpl_l in ("contour", "tone_contour", "f0_contour"):
                if not explicit_goal:
                    normalized["goal"] = "绘制声调五度值/基频轮廓图"
            elif tpl_l in ("agent", "interactive"):
                normalized["prompt_mode"] = "Agent协作"
        elif canonical == "output_type":
            ot = str(raw_v).strip()
            normalized["output_type"] = ot
            ot_l = ot.lower()
            if ot_l in ("data_process", "patch", "project", "teproj"):
                normalized["script_type"] = "data_process"
            elif ot_l in ("table", "csv", "both", "figure_and_table", "chart_and_table"):
                normalized["output_table"] = True
        elif canonical == "scope":
            sc = str(raw_v).strip()
            normalized["scope"] = sc
            sc_l = sc.lower()
            if sc_l in ("integrated", "all_speakers", "multi_speaker"):
                normalized["data_range"] = "跨发音人整合分析 (所有纳入分析的条目)"
            elif sc_l in ("active", "current"):
                normalized["data_range"] = "当前激活发音人纳入分析的条目"
            elif sc_l in ("all", "include_excluded"):
                normalized["data_range"] = "全部条目 (含已排除条目)"
            else:
                normalized["data_range"] = sc
        elif canonical == "analysis_mode":
            am = str(raw_v).strip().lower()
            normalized["analysis_mode"] = am
            if am == "formant":
                if not explicit_goal:
                    normalized["goal"] = "绘制 F1/F2 元音空间分布与轨迹图"
                if not explicit_axes:
                    normalized["x_axis"] = "F2 Hz"
                    normalized["y_axis"] = "F1 Hz"
        elif canonical == "user_request":
            req = str(raw_v).strip()
            normalized["user_request"] = req
            if req:
                if normalized["custom_desc"]:
                    normalized["custom_desc"] = f"{normalized['custom_desc']}\n{req}"
                else:
                    normalized["custom_desc"] = req
                if not explicit_goal:
                    normalized["goal"] = req
        else:
            normalized[canonical] = raw_v

    return normalized


def _extract_item_custom_fields(item):
    """合并提取词条上的自定义字段（兼容 item_meta, meta, fields, custom_fields）。"""
    if not isinstance(item, dict):
        return {}
    merged = {}
    for key in ("meta", "item_meta", "fields", "custom_fields"):
        val = item.get(key)
        if isinstance(val, dict):
            for mk, mv in val.items():
                if mk is not None and str(mk).strip() and mv is not None and str(mv).strip():
                    merged[str(mk).strip()] = str(mv).strip()
    return merged


def _wordlist_row_key(item):
    meta = _extract_item_custom_fields(item)
    return (
        _truncate_text(item.get("label") or item.get("text") or item.get("name") or "未命名词项", 48),
        tuple(_as_text_list(item.get("item_tags") or item.get("tags"))),
        tuple(_as_text_list(item.get("item_aliases") or item.get("aliases"))),
        tuple((str(k), str(v)) for k, v in sorted(meta.items(), key=lambda pair: str(pair[0]))),
        _truncate_text(item.get("item_note") or item.get("note"), 80),
        _truncate_text(item.get("metadata_source"), 48),
    )


def _build_detailed_wordlist_summary(items, max_groups=8, max_items_per_group=5):
    """
    输出全局与各分组的字段 Schema 枚举分布 + 每组前 5 个代表词示例，
    避免在大工程下逐词展开导致 Token 膨胀。
    """
    buckets = {}
    v2_item_count = 0
    global_group_tags = Counter()
    global_item_tags = Counter()
    global_meta_key_values = {}
    global_statuses = Counter()

    for item in items:
        if not _looks_like_v2_wordlist_item(item):
            continue
        v2_item_count += 1
        title = _truncate_text(item.get("wordlist_title") or "未命名高级字表", 36)
        group_name = _truncate_text(item.get("group") or "未分组", 36)
        key = (title, group_name)
        if key not in buckets:
            buckets[key] = {
                "rows": {},
                "occurrences": 0,
                "group_notes": Counter(),
                "group_tags": Counter(),
            }
        bucket = buckets[key]
        bucket["occurrences"] += 1
        group_note = _truncate_text(item.get("group_note"), 48)
        if group_note:
            bucket["group_notes"][group_note] += 1
        for tag in _as_text_list(item.get("group_tags")):
            bucket["group_tags"][tag] += 1
            global_group_tags[tag] += 1
        for tag in _as_text_list(item.get("item_tags") or item.get("tags")):
            global_item_tags[tag] += 1
        meta = _extract_item_custom_fields(item)
        if isinstance(meta, dict):
            for mk, mv in meta.items():
                k_text = _truncate_text(mk, 20)
                v_text = _truncate_text(mv, 24)
                if k_text and v_text:
                    global_meta_key_values.setdefault(k_text, Counter())[v_text] += 1
        status_text = _truncate_text(item.get("metadata_source"), 32)
        if status_text:
            global_statuses[status_text] += 1

        row_key = _wordlist_row_key(item)
        if row_key not in bucket["rows"]:
            bucket["rows"][row_key] = {"item": item, "count": 0}
        bucket["rows"][row_key]["count"] += 1

    if not v2_item_count:
        return "- 详细字表信息: 当前工程没有可展开的 v2 高级字表词项。"

    schema_parts = []
    if global_group_tags:
        schema_parts.append(f"组标签枚举={{{_format_counter(global_group_tags, limit=10)}}}")
    if global_item_tags:
        schema_parts.append(f"词项标签枚举={{{_format_counter(global_item_tags, limit=12)}}}")
    if global_meta_key_values:
        meta_schema_strs = []
        for mk in sorted(global_meta_key_values.keys()):
            val_dist = _format_counter(global_meta_key_values[mk], limit=6)
            meta_schema_strs.append(f"{mk}: [{val_dist}]")
        schema_parts.append("自定义字段(item_meta)枚举={" + "；".join(meta_schema_strs[:8]) + "}")
    if global_statuses:
        schema_parts.append(f"复核状态分布={{{_format_counter(global_statuses, limit=4)}}}")

    lines = [
        "- 详细字表信息（Schema 枚举分布 + 每组前 5 个代表词示例；运行期请通过 `ctx.dataset.included_items()` 动态遍历）:"
    ]
    if schema_parts:
        lines.append("  - 全局字段 Schema 分布: " + "；".join(schema_parts))

    sorted_keys = sorted(buckets.keys(), key=lambda pair: (pair[0], pair[1]))
    for title, group_name in sorted_keys[:max_groups]:
        bucket = buckets[(title, group_name)]
        rows = list(bucket["rows"].values())
        item_tags = Counter()
        meta_values = Counter()

        for row in rows:
            item = row["item"]
            for tag in _as_text_list(item.get("item_tags") or item.get("tags")):
                item_tags[tag] += 1
            meta = _extract_item_custom_fields(item)
            if isinstance(meta, dict):
                for meta_key, meta_value in meta.items():
                    key_text = _truncate_text(meta_key, 20)
                    value_text = _truncate_text(meta_value, 24)
                    if key_text and value_text:
                        meta_values[f"{key_text}={value_text}"] += 1

        entries = []
        for row in rows[:max_items_per_group]:
            item = row["item"]
            label = _truncate_text(item.get("label") or item.get("text") or item.get("name") or "未命名词项", 20)
            tags = _as_text_list(item.get("item_tags") or item.get("tags"))
            meta = _extract_item_custom_fields(item)
            short_parts = []
            if tags:
                short_parts.append("/".join(tags[:2]))
            if isinstance(meta, dict) and meta:
                short_parts.append(_format_meta_pairs(meta, limit=2))
            entries.append(f"{label}({'; '.join(short_parts)})" if short_parts else label)

        omitted_items = len(rows) - min(len(rows), max_items_per_group)
        sample_str = ", ".join(entries) + (f" 等{len(rows)}词" if omitted_items > 0 else "")
        grp_parts = [f"n={bucket['occurrences']}"]
        if bucket["group_tags"]:
            grp_parts.append(f"组标签:{_format_value_list(bucket['group_tags'].keys(), limit=3)}")
        grp_parts.append(f"代表词:[{sample_str}]")
        lines.append(f"  - {group_name} ({' | '.join(grp_parts)})")

    omitted_groups = len(sorted_keys) - min(len(sorted_keys), max_groups)
    if omitted_groups > 0:
        lines.append(f"  - 其余 {omitted_groups} 个分组已省略（运行期通过 ctx.dataset.included_items() 读取全部组与词项）。")

    return "\n".join(lines)


def _extract_project_groups_and_title(project_data):
    if not project_data or not isinstance(project_data, dict):
        return {}, ""
    raw_groups = project_data.get("groups", [])
    if isinstance(raw_groups, dict):
        groups_map = dict(raw_groups)
    elif isinstance(raw_groups, list):
        groups_map = {
            (g.get("name") or g.get("group")): dict(g)
            for g in raw_groups
            if isinstance(g, dict) and (g.get("name") or g.get("group"))
        }
    else:
        groups_map = {}
    wl_meta = project_data.get("wordlist_meta", {})
    default_title = wl_meta.get("title", "") if isinstance(wl_meta, dict) else ""

    wl_doc = project_data.get("wordlist_doc")
    if isinstance(wl_doc, dict):
        if wl_doc.get("title") and not default_title:
            default_title = str(wl_doc.get("title"))
        for g in (wl_doc.get("groups") or []):
            if not isinstance(g, dict):
                continue
            gname = g.get("name") or g.get("group")
            if gname:
                merged_g = dict(groups_map.get(gname, {}))
                for gk, gv in g.items():
                    if gv and not merged_g.get(gk):
                        merged_g[gk] = gv
                groups_map[gname] = merged_g

    return groups_map, default_title


def _enrich_item_with_project_meta(item, groups_map, default_title):
    if not isinstance(item, dict):
        return item
    grp_name = item.get("group") or "默认组"
    grp_info = groups_map.get(grp_name, {}) if isinstance(groups_map, dict) else {}
    if not isinstance(grp_info, dict):
        grp_info = {}

    doc_item_match = {}
    for cand in (grp_info.get("items") or []):
        if isinstance(cand, dict) and (cand.get("label") or cand.get("word")) == (item.get("label") or item.get("word")):
            doc_item_match = cand
            break

    enriched = dict(item)
    if not enriched.get("group_note") and grp_info.get("note"):
        enriched["group_note"] = grp_info.get("note")
    if not enriched.get("group_tags") and grp_info.get("tags"):
        enriched["group_tags"] = list(grp_info.get("tags") or [])
    if not enriched.get("wordlist_title") and default_title:
        enriched["wordlist_title"] = default_title

    # Sync tags, note, and custom fields from item or wordlist_doc
    tags = _as_text_list(enriched.get("item_tags") or enriched.get("tags") or doc_item_match.get("tags"))
    if tags:
        enriched["tags"] = tags
        enriched["item_tags"] = tags
    note = enriched.get("item_note") or enriched.get("note") or doc_item_match.get("note") or ""
    if note:
        enriched["note"] = note
        enriched["item_note"] = note
    custom_fields = _extract_item_custom_fields(doc_item_match)
    custom_fields.update(_extract_item_custom_fields(enriched))
    if isinstance(grp_info.get("meta"), dict):
        for gk, gv in grp_info["meta"].items():
            if gk and gv and gk not in custom_fields:
                custom_fields[str(gk)] = str(gv)
    if custom_fields:
        enriched["item_meta"] = dict(custom_fields)
        enriched["fields"] = dict(custom_fields)
        enriched["custom_fields"] = dict(custom_fields)
    if (tags or note or custom_fields or default_title) and not enriched.get("wordlist_version"):
        enriched["wordlist_version"] = "v2"
    return enriched


def _iter_project_items(project_data):
    if not project_data or not isinstance(project_data, dict):
        return
    groups_map, default_title = _extract_project_groups_and_title(project_data)
    speakers = project_data.get("speakers", {})
    if not isinstance(speakers, dict):
        return
    for spk in speakers.values():
        if not isinstance(spk, dict):
            continue
        items = spk.get("items", {})
        if isinstance(items, dict):
            iterable = items.values()
        elif isinstance(items, list):
            iterable = items
        else:
            iterable = []
        for item in iterable:
            if isinstance(item, dict):
                yield _enrich_item_with_project_meta(item, groups_map, default_title)


def _looks_like_v2_wordlist_item(item):
    return (
        item.get("wordlist_version") == "v2"
        or bool(item.get("wordlist_title"))
        or bool(item.get("item_tags") or item.get("tags"))
        or bool(item.get("group_tags"))
        or bool(_extract_item_custom_fields(item))
        or bool(item.get("item_note") or item.get("note"))
        or bool(item.get("group_note"))
        or bool(item.get("item_aliases") or item.get("aliases"))
    )


def _build_wordlist_metadata_summary(items, limit=16):
    v2_count = 0
    titles = Counter()
    item_tags = Counter()
    group_tags = Counter()
    meta_keys = Counter()
    metadata_sources = Counter()
    item_note_count = 0
    group_note_count = 0
    alias_count = 0
    meta_item_count = 0

    for item in items:
        if not _looks_like_v2_wordlist_item(item):
            continue
        v2_count += 1
        title = item.get("wordlist_title")
        if title:
            titles[str(title)] += 1
        for tag in (item.get("item_tags") or item.get("tags") or []):
            if str(tag).strip():
                item_tags[str(tag).strip()] += 1
        for tag in (item.get("group_tags") or []):
            if str(tag).strip():
                group_tags[str(tag).strip()] += 1
        item_meta = _extract_item_custom_fields(item)
        if isinstance(item_meta, dict) and item_meta:
            meta_item_count += 1
            for key in item_meta.keys():
                if str(key).strip():
                    meta_keys[str(key).strip()] += 1
        if item.get("metadata_source"):
            metadata_sources[str(item.get("metadata_source"))] += 1
        if item.get("item_note") or item.get("note"):
            item_note_count += 1
        if item.get("group_note"):
            group_note_count += 1
        if item.get("item_aliases") or item.get("aliases"):
            alias_count += 1

    v2_guide_note = (
        "- v2 高级字表与 `wordlist_doc` 扩展属性说明: "
        "词条级支持 `tags` (`item_tags`, 如 `['目标词', '阴平']`)、"
        "`note` (`item_note`, 词项备注)、"
        "`fields` / `custom_fields` (`item_meta`, 自定义键值对字典，示例字段：`拼音`, `声调`, `韵母`, `结构`, `实验条件`, `词频等级`)、"
        "`aliases` (`item_aliases`) 及 `metadata_source`；"
        "工程级 `wordlist_doc` 包含 `schema` (`phontracer.wordlist.v2`), `title`, `note` 及 `groups` (含组级 `tags`, `note`, `meta` 与词项列表)。"
    )
    if not v2_count:
        return (
            "- 高级字表元数据: 当前工程未检测到已填充的 v2 字表实例字段；"
            "如果用户想按标签、结构或实验条件分析，请先确认工程是否使用高级字表导入。\n"
            f"{v2_guide_note}"
        )

    return (
        f"- 高级字表元数据: v2 条目 {v2_count}；"
        f"字表标题: {_format_counter(titles, limit=limit)}；"
        f"复核状态: {_format_counter(metadata_sources, limit=limit)}；"
        f"带词项备注(note) {item_note_count}；带组备注(group_note) {group_note_count}；"
        f"带别名(aliases) {alias_count}；带自定义字段(fields/custom_fields/item_meta) {meta_item_count}\n"
        f"- 可用于脚本筛选/分组的高级字表字段: "
        f"词项标签(tags/item_tags) {{{_format_counter(item_tags, limit=limit)}}}；"
        f"组标签(group_tags) {{{_format_counter(group_tags, limit=limit)}}}；"
        f"自定义字段(fields/custom_fields/item_meta) {{{_format_counter(meta_keys, limit=limit)}}}\n"
        f"{v2_guide_note}"
    )


def _build_project_summary(
    project_data,
    max_speaker_names=8,
    max_speakers=8,
    max_groups=16,
    max_speaker_groups=8,
    wordlist_detail="compact",
):
    spk_count = 0
    item_count = 0
    excluded_count = 0
    groups = Counter()
    modes = Counter()
    speaker_names = []
    speaker_lines = []
    pitch_cache_count = 0
    formant_cache_count = 0
    all_items = []

    if project_data and isinstance(project_data, dict):
        groups_map, default_title = _extract_project_groups_and_title(project_data)
        speakers = project_data.get("speakers", {})
        spk_count = len(speakers)
        for spk_id, spk in speakers.items():
            name = spk.get("name", "未命名")
            speaker_names.append(name)
            items = spk.get("items", {})
            spk_groups = Counter()
            spk_modes = Counter()
            spk_excluded = 0

            if isinstance(items, dict):
                item_values = items.values()
                item_len = len(items)
            elif isinstance(items, list):
                item_values = items
                item_len = len(items)
            else:
                item_values = []
                item_len = 0

            for item in item_values:
                if not isinstance(item, dict):
                    continue
                all_items.append(_enrich_item_with_project_meta(item, groups_map, default_title))
                item_count += 1
                if item.get("is_excluded", False):
                    excluded_count += 1
                    spk_excluded += 1

                group_name = item.get("group") or "未分组"
                groups[group_name] += 1
                spk_groups[group_name] += 1

                mode = item.get("analysis_mode", spk.get("last_params", {}).get("analysis_mode", "f0"))
                modes[mode] += 1
                spk_modes[mode] += 1

                if item.get("pitch_data_file") or item.get("pitch_data") is not None:
                    pitch_cache_count += 1
                if item.get("formant_data_file") or item.get("formant_data") is not None:
                    formant_cache_count += 1

            speaker_lines.append(
                f"  - {name}: 条目 {item_len}，排除 {spk_excluded}，"
                f"分组 {{{_format_counter(spk_groups, limit=max_speaker_groups)}}}，模式 {{{_format_counter(spk_modes)}}}"
            )

    included_count = item_count - excluded_count
    speaker_limit = max_speakers if max_speakers is not None else len(speaker_lines)
    speaker_detail = "\n".join(speaker_lines[:speaker_limit]) if speaker_lines else "  - 无"
    if len(speaker_lines) > speaker_limit:
        speaker_detail += f"\n  - 其余 {len(speaker_lines) - speaker_limit} 位发音人已省略"

    wordlist_summary = _build_wordlist_metadata_summary(all_items)
    if wordlist_detail == "detailed":
        wordlist_summary = f"{wordlist_summary}\n{_build_detailed_wordlist_summary(all_items)}"

    return (
        f"- 发音人数: {spk_count} (列表: {_format_sequence(speaker_names, limit=max_speaker_names)})\n"
        f"- 条目总数: {item_count}；纳入分析: {included_count}；已排除: {excluded_count}\n"
        f"- 分组及条目数: {_format_counter(groups, limit=max_groups)}\n"
        f"- 分析模式分布: {_format_counter(modes)}\n"
        f"- 已缓存基频条目估计: {pitch_cache_count}；已缓存共振峰条目估计: {formant_cache_count}\n"
        f"{wordlist_summary}\n"
        f"- 发音人明细:\n{speaker_detail}"
    )


def _build_agent_prompt(project_data, selections):
    detail_level = selections.get("agent_detail_level", "精简")
    chart_count = selections.get("agent_chart_count", "5")
    summary_mode = selections.get("agent_project_summary_mode")
    if summary_mode not in ("包含精简工程摘要", "包含详细工程摘要", "不附带工程摘要"):
        if selections.get("agent_detailed_project_summary", False):
            summary_mode = "包含详细工程摘要"
        elif selections.get("agent_include_project_summary", True):
            summary_mode = "包含精简工程摘要"
        else:
            summary_mode = "不附带工程摘要"
    custom_desc = selections.get("custom_desc", "").strip()

    try:
        chart_count_int = int(chart_count)
    except (TypeError, ValueError):
        chart_count_int = 5
    chart_count_int = max(3, min(6, chart_count_int))

    if summary_mode != "不附带工程摘要":
        project_summary = _build_project_summary(
            project_data,
            max_speaker_names=12,
            max_speakers=8,
            max_groups=20,
            max_speaker_groups=8,
            wordlist_detail="detailed" if summary_mode == "包含详细工程摘要" else "compact",
        )
    else:
        project_summary = "- 用户选择不附带当前工程摘要。请在首轮询问用户希望分析的数据范围。"

    extra_section = f"\n### 用户额外要求\n{custom_desc}\n" if custom_desc else ""
    is_detailed = detail_level == "详细"
    detail_appendix = f"\n\n{_build_agent_detail_appendix()}" if is_detailed else ""

    return f"""你现在是 PhonTracer 语音学标注与声学分析系统 V1.0 的自定义图表脚本 Agent。

### 一、 协作工作流协议
- **快速直通规则（Fast-Track）**：如果用户在下方的【用户额外要求】中已经明确指定了具体图表方案并要求直接生成代码，请**跳过阶段 1 提问，直接进入阶段 2** 输出纯 Python 代码。
- **阶段 1（澄清与方案推荐，默认首轮）**：若用户尚未敲定具体图表：
  1. 第一轮回复**不要直接输出代码**；先用 1~2 句话概括你从【当前工程摘要】中看到的数据特征。
  2. 提出最多 3 个简短精准的确认问题（如研究焦点、分组/对比维度、是否按高级字表标签或 `item_meta` 筛选、统计口径）。
  3. 推荐 **{chart_count_int}** 个编号候选图表方案，每个方案写明：适用研究问题、使用字段、统计口径、潜在误用风险。
- **阶段 2（代码生成）**：当用户选定方案或要求直接生成后，**只输出可直接运行的纯 Python 代码**（不要输出任何解释文字，不要使用 ```python 代码块包裹）。代码前两行必须为 `# 脚本名称：...` 和 `# 功能说明：...`。
- **参考图复刻准则**：{REFERENCE_CHART_RULE}

### 二、 当前工程摘要
{project_summary}
{extra_section}
{CORE_CHART_API_CONTRACT}
{detail_appendix}""".strip()


def _build_data_process_agent_prompt(project_data, selections):
    detail_level = selections.get("agent_detail_level", "详细")
    plan_count = selections.get("agent_plan_count", "4")
    summary_mode = selections.get("agent_project_summary_mode")
    if summary_mode not in ("包含精简工程摘要", "包含详细工程摘要", "不附带工程摘要"):
        if selections.get("agent_detailed_project_summary", False):
            summary_mode = "包含详细工程摘要"
        elif selections.get("agent_include_project_summary", True):
            summary_mode = "包含精简工程摘要"
        else:
            summary_mode = "不附带工程摘要"
    custom_desc = selections.get("custom_desc", "").strip()

    try:
        plan_count_int = int(plan_count)
    except (TypeError, ValueError):
        plan_count_int = 4
    plan_count_int = max(3, min(5, plan_count_int))

    if summary_mode != "不附带工程摘要":
        project_summary = _build_project_summary(
            project_data,
            max_speaker_names=12,
            max_speakers=8,
            max_groups=20,
            max_speaker_groups=8,
            wordlist_detail="detailed" if summary_mode == "包含详细工程摘要" else "compact",
        )
    else:
        project_summary = "- 用户选择不附带当前工程摘要。请在首轮询问用户希望处理哪类工程数据。"

    extra_section = f"\n### 用户额外要求\n{custom_desc}\n" if custom_desc else ""
    is_detailed = detail_level == "详细"
    detail_appendix = f"\n\n{_build_data_process_detail_appendix()}" if is_detailed else ""

    return f"""你现在是 PhonTracer 语音学标注与声学分析系统 V1.0 的数据处理脚本 Agent。

### 一、 协作工作流协议
- **快速直通规则（Fast-Track）**：如果用户在下方的【用户额外要求】中已经明确指定了具体数据处理方案并要求直接生成代码，请**跳过阶段 1 提问，直接进入阶段 2** 输出纯 Python 代码。
- **阶段 1（澄清与方案推荐，默认首轮）**：若用户尚未敲定具体方案：
  1. 第一轮回复**不要直接输出代码**；先用 1~2 句话概括工程摘要中的数据状态。
  2. 主动判断用户可能需要的工程再加工类型（批量重算声学参数、按条件拆分独立子工程、边界微调与音频裁剪、外部表格元数据合并、异常质检与标记），提出最多 3 个确认问题。
  3. 推荐 **{plan_count_int}** 个编号候选处理方案，说明：解决什么问题、读取哪些字段、返回哪些受控算子、会产生哪些可复核变化。
- **阶段 2（代码生成）**：当用户确认方案后，**只输出可直接运行的纯 Python 代码**（不要解释文字，不要使用 ```python 代码块包裹）。第一行必须是 `# 脚本名称：...`，第二行必须是 `# 功能说明：...`，并定义 `def run(ctx):`。

### 二、 当前工程摘要
{project_summary}
{extra_section}
### 三、 数据处理沙箱、声明式参数 (`PARAMS`) 与受控算子契约
- **核心定位与多产物联合返回**：脚本不直接修改本地 `.teproj` 或读写文件，而是返回 `ctx.project_patch(operations, title="...", description="...")`；由 Toolkit 统一校验并另存为新的 `.teproj`；**强烈推荐联合返回 `[ctx.figure(...), ctx.table(...), ctx.project_patch(...)]`**，让研究者先在工作台预览“清洗前后对比诊断图”与“异常词条明细表”，再点击 `[应用补丁并另存工程 (.teproj)]` 确认落盘。
- **声明式交互参数 (`PARAMS`)**：可在脚本顶部定义 `PARAMS = [{{"key": "...", "label": "...", "type": "float|int|bool|choice|str", "default": ...}}]`，并在 `run(ctx)` 中通过 `ctx.params.get("key", default)` 读取阈值或开关参数。
- **预展开表与沙箱安全边界**：
  - 可直接使用 `ctx.dataset.to_point_table()`、`ctx.dataset.to_syllable_table()`、`ctx.extract_voice_quality()`、`ctx.extract_spectral_moments()` 以及内存级 `pandas` (`ctx.pd`) 与 `scipy` (`ctx.scipy`)。
  - 严禁导入 `seaborn, plotly, requests, os, sys, subprocess`；严禁调用 `.save(), .savefig(), .to_csv(), .write(), open(), eval(), exec()`；严禁使用 `getattr, hasattr`（已放行 `isinstance, type, callable, chr, ord, format, iter, next`）及 `parselmouth.praat`。
  - `print(...)` 已自动重定向至 `ctx.log(...)`；长循环中请定期检查 `ctx.is_cancelled()`。
- **6 大受控算子（放入 `operations` 列表）**：
  1. `ctx.set_item_fields(item, fields, reason="...")`
     - **完整合法字段白名单**：`label`, `group`, `group_note`, `group_tags`, `item_note`, `item_tags`, `item_aliases`, `item_meta`, `metadata_source`, `is_excluded`, `exclusion_reason`, `start`, `end`, `inner_splits`, `chars_bounds`（严禁传入白名单外的未知键）。
     - **清空缓存副作用警告**：只要修改了 `start, end, inner_splits, chars_bounds` 中任一边界字段，系统会**自动清空该条目的基频与共振峰缓存**！若需保留声学数据，必须紧接着对该条目追加 `ctx.recompute_pitch(item, ...)` 和/或 `ctx.recompute_formant(item, ...)`。
  2. `ctx.recompute_pitch(item, params={{...}}, reason="...")`
     - 重算条目基频 F0 缓存。支持覆盖的 `params` 参数：`pitch_floor` (默认 75.0), `pitch_ceiling` (默认 600.0), `voicing_threshold` (默认 0.45), `very_accurate` (bool), `time_step`, `max_candidates`, `silence_threshold`, `method` (`"ac"` 或 `"cc"`), `pts` (默认 11)。
  3. `ctx.recompute_formant(item, params={{...}}, reason="...")`
     - 重算条目共振峰缓存。支持覆盖的 `params` 参数：`formant_max_hz` (默认 5500.0), `formant_count` (默认 5.0), `formant_window_length` (默认 0.025), `formant_pre_emphasis` (默认 50.0), `formant_sample_strategy` (`"整段11点"` 或 `"中段均值"`).
  4. `ctx.trim_item_audio(item, start=..., end=..., padding=0.0, reason="...")`
     - 裁剪条目音频并替换工程内引用。**同样会自动清空该条目的基频与共振峰缓存**，如需保留声学曲线请紧接着追加 `recompute_pitch` / `recompute_formant`。
  5. `ctx.split_project(name, item_ids=[...], speaker_ids=[...], reason="...")`
     - 从当前工程拆出干净子工程。**支持单次批量拆分为多个子工程**：`operations` 列表中可以包含**多个** `ctx.split_project(...)` 操作（例如按每位发音人或每个实验组各生成一个 `split_project`），Toolkit 会在目标目录一次性生成多个带不同后缀的独立 `.teproj` 文件。
  6. `ctx.import_csv_metadata(rows, match_on="label", field_map={{...}}, reason="...")`
     - 将结构化字典列表 `rows` 按 `match_on` 匹配合并进条目元数据（如 `field_map={{"实验条件": "item_meta.实验条件", "标签": "item_tags", "复核状态": "metadata_source"}}`）。

### 四、 推荐代码骨架
# 脚本名称：批量工程再加工
# 功能说明：根据质量规则生成可复核的数据处理操作
def run(ctx):
    operations = []
    items = ctx.dataset.included_items()
    for item in items:
        if ctx.is_cancelled():
            ctx.log("用户取消，提前结束")
            break
        duration = item.get("duration") or 0.0
        if 0 < duration < 0.12:
            operations.append(ctx.set_item_fields(item, {{
                "is_excluded": True,
                "exclusion_reason": "时长过短(<0.12s)，建议人工复核"
            }}, reason="时长过短"))
    ctx.log(f"扫描条目 {{len(items)}} 个，生成操作 {{len(operations)}} 项。")
    return ctx.project_patch(operations, title="批量工程再加工", description="由 Toolkit 校验并另存为新 .teproj")
{detail_appendix}""".strip()


def generate_ai_prompt(project_data, selections):
    """
    根据用户在弹窗或 CLI 中选择的各项配置，以及当前工程摘要，生成给 AI 的提示词。
    """
    selections = validate_and_normalize_script_prompt_params(selections or {})
    script_type = selections.get("script_type", "chart")
    prompt_mode = selections.get("prompt_mode", "参数选项")
    if script_type == "data_process":
        return _build_data_process_agent_prompt(project_data, selections)
    if str(prompt_mode).replace(" ", "") in ("Agent协作", "Agent模式", "代理协作"):
        return _build_agent_prompt(project_data, selections)

    # 1. 提取工程摘要信息（带默认截断上限，防止大工程 Token 膨胀）
    project_summary = _build_project_summary(
        project_data,
        max_speaker_names=8,
        max_speakers=8,
        max_groups=16,
        max_speaker_groups=8,
    )

    # 2. 整合用户偏好
    user_goal = selections.get("goal", "自定义图表")
    data_range = selections.get("data_range", "只使用纳入分析的条目")
    group_by = selections.get("group_by", "按声调/分组")
    chart_style = selections.get("chart_style", "折线图")
    x_axis = selections.get("x_axis", "归一化时间 0-1")
    y_axis = selections.get("y_axis", "F0 Hz")

    stats_list = selections.get("stats", [])
    stats_desc = "、".join(stats_list) if stats_list else "无"

    title = selections.get("title", "自定义图表")
    filename = selections.get("filename", "custom_chart.png")
    img_format = selections.get("img_format", "png")
    output_table_bool = bool(selections.get("output_table", False))
    output_table = "是" if output_table_bool else "否"
    show_legend = "是" if selections.get("show_legend", True) else "否"

    raw_use_chinese = selections.get("use_chinese", True)
    if isinstance(raw_use_chinese, str):
        use_chinese_bool = raw_use_chinese.strip() not in ("否", "False", "false", "0", "no")
    else:
        use_chinese_bool = bool(raw_use_chinese)
    use_chinese = "是" if use_chinese_bool else "否"
    custom_desc = selections.get("custom_desc", "").strip()

    language_rule = (
        "图表的标题、坐标轴名称、图例文字必须使用中文。"
        if use_chinese_bool
        else "图表的标题、坐标轴名称、图例文字必须使用英文（English labels for publication），请勿在图面硬编码中文标签。"
    )

    combined_intent = f"{user_goal} {x_axis} {y_axis} {custom_desc}".lower()
    is_formant_task = any(k in combined_intent for k in ("共振峰", "元音", "formant", "f1", "f2", "f3", "vowel"))
    code_skeleton = _build_code_skeleton(title, user_goal, filename, is_formant=is_formant_task)

    # 3. 组装自然语言需求描述
    requirements = [
        f"0. 生成模式: {prompt_mode}",
        f"1. 脚本用途: {user_goal}",
        f"2. 数据范围: {data_range}",
        f"3. 分组方式: {group_by}",
        f"4. 图表形式: {chart_style}",
        f"5. 横轴 (X 轴): {x_axis}",
        f"6. 纵轴 (Y 轴): {y_axis}",
        f"7. 统计处理: {stats_desc}",
        f"8. 输出要求:",
        f"   - 图表标题: {title}",
        f"   - 输出文件名: {filename} (格式: {img_format})",
        f"   - 是否同时返回数据表: {output_table}",
        f"   - 是否显示图例: {show_legend}",
        f"   - 是否使用中文标签: {use_chinese}",
    ]

    if selections.get("template"):
        requirements.append(f"   - 预设模板 (template): {selections['template']}")
    if selections.get("output_type"):
        requirements.append(f"   - 输出类型 (output_type): {selections['output_type']}")
    if selections.get("scope"):
        requirements.append(f"   - 作用范围 (scope): {selections['scope']}")
    if selections.get("analysis_mode"):
        requirements.append(f"   - 分析模式 (analysis_mode): {selections['analysis_mode']}")
    if custom_desc:
        requirements.append(f"9. 补充具体需求 (user_request):\n{custom_desc}")

    requirements_text = "\n".join(requirements)

    # 4. 生成高密度无冗余提示词
    prompt = f"""你正在为 PhonTracer 语音学标注与声学分析系统 V1.0 编写一个自定义图表脚本。
请只输出可以直接运行的纯 Python 代码，不要输出任何解释文字，不要使用 ```python 代码块包裹。

{CORE_CHART_API_CONTRACT}

### 当前工程摘要信息
{project_summary}

### 用户自定义脚本需求配置
{requirements_text}

### 推荐代码骨架（请按目标改写）
{code_skeleton}

### 绘图与设计准则
- **语言要求**：{language_rule}
- **空数据安全兜底**：若筛选后没有匹配数据，请勿崩溃报错，返回一张居中写明无可用数据的空 `Figure` 并调用 `ctx.log` 说明。
- **统计可复现性**：图面或图例应清晰标注统计口径（如 `T值/Hz`、`11点对齐`、每组样本量 `n=...`、阴影代表标准差或 95% CI）；并通过 `ctx.log` 输出纳入条目数、跳过条目数与各组样本量。
- **参考图复刻**：{REFERENCE_CHART_RULE}"""
    return prompt.strip()
