# -*- coding: utf-8 -*-
"""
modules.chart_engine.grouping_and_data
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
声学图表数据提取、多级分组、分页与衍生指标计算混入类。
完全无头，仅依赖 matplotlib Agg 后端，不依赖任何 GUI 库。
"""
from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple
import numpy as np

import matplotlib
matplotlib.use("Agg", force=True)
import matplotlib.pyplot as plt

from modules.core.acoustic_math import hz_to_t_value
from modules.data_utils import split_into_syllables


class ChartDataGroupingMixin:
    """声学图表数据提取与分组混入类。"""

    def _extract_active_data(self, speakers_list, filter_groups=True, apply_chart_grouping=True):
        analysis_mode = self.project_tree.app_state_params.get('analysis_mode', 'f0')
        if analysis_mode == 'formant':
            return self._extract_active_formant_data(
                speakers_list,
                filter_groups=filter_groups,
                apply_chart_grouping=apply_chart_grouping,
            )
        num_points = self.project_tree.app_state_params.get('pts', 11)
        data_entries = []

        for speaker in speakers_list:
            if speaker is None:
                continue
            cache_key = (getattr(speaker, 'id', id(speaker)), 'f0', int(num_points))
            if not getattr(self, '_force_live_extract', False) and hasattr(self, '_speaker_data_cache') and cache_key in self._speaker_data_cache:
                data_entries.extend([self._attach_entry_group_defaults(e) for e in self._speaker_data_cache[cache_key]])
                continue

            orig_items = self.project_tree.items
            try:
                self.project_tree.items = speaker.items
                s_struct = self.project_tree._get_items_by_group_for_dict(speaker.items)

                speaker_f0_pool = []
                speaker_items_temp = []

                for grp_name, children in s_struct:
                    for child in children:
                        item = speaker.items[child]
                        self.project_tree._ensure_item_loaded(item)
                        if item.get('start') is None or (not item.get('pitch') and not item.get('pitch_data')):
                            continue

                        total_dur, syl_data = self.project_tree._extract_syl_data(item, num_points)
                        if total_dur <= 0:
                            continue

                        p_xs, p_freqs = self.project_tree._get_pitch_arrays_for_item(item)
                        if p_xs is None or p_freqs is None:
                            continue

                        valid_f0_mask = np.isfinite(p_freqs) & (p_freqs > 0)
                        active_ratio = np.mean(valid_f0_mask) if len(p_freqs) > 0 else 0.0

                        warnings = item.get('warnings', [])

                        speaker_items_temp.append({
                            'item_id': str(child),
                            'speaker_name': speaker.name,
                            'group': grp_name,
                            'wordlist_group': grp_name,
                            'chart_group': grp_name,
                            'label': item.get('label', ''),
                            'total_dur': total_dur,
                            'syl_data': syl_data,
                            'raw_xs': p_xs,
                            'raw_freqs': p_freqs,
                            'active_ratio': active_ratio,
                            'warnings': warnings,
                            'raw_item': item,
                            **self._item_wordlist_metadata(item),
                        })

                        speaker_f0_pool.extend([float(f) for f in p_freqs if np.isfinite(f) and f > 0])

                s_min, s_max = self._get_f0_normalization_bounds(speaker_f0_pool)

                def _normalize_freqs(f_arr):
                    return hz_to_t_value(f_arr, s_min, s_max, clip=True)

                speaker_data_entries = []
                for entry in speaker_items_temp:
                    cleaned_syl_data = []
                    normalized_syl_data = []
                    for s_dur, freqs in entry['syl_data']:
                        f_arr = np.asarray(freqs, dtype=float)
                        clean_freqs = np.where(np.isfinite(f_arr) & (f_arr > 0), f_arr, np.nan)
                        cleaned_syl_data.append((s_dur, clean_freqs.tolist()))
                        norm_freqs = _normalize_freqs(clean_freqs).tolist()
                        normalized_syl_data.append((s_dur, norm_freqs))

                    entry['syl_data'] = cleaned_syl_data
                    entry['normalized_syl_data'] = normalized_syl_data
                    entry['normalized_raw_freqs'] = _normalize_freqs(entry['raw_freqs'])
                    entry['speaker_f0_bounds'] = (s_min, s_max)
                    speaker_data_entries.append(self._attach_entry_group_defaults(entry))
            finally:
                self.project_tree.items = orig_items

            if hasattr(self, '_speaker_data_cache'):
                self._speaker_data_cache[cache_key] = speaker_data_entries
            data_entries.extend(speaker_data_entries)

        if apply_chart_grouping:
            data_entries = self._apply_chart_grouping(data_entries)
        else:
            data_entries = self._apply_chart_grouping(
                data_entries,
                rule=self._default_chart_group_rule(),
                preserve_original_group=True,
            )

        if filter_groups:
            selected_groups = self.get_param('selected_groups', None)
            if selected_groups is not None:
                if isinstance(selected_groups, str):
                    selected_groups = [g.strip() for g in selected_groups.split(',') if g.strip()]
                selected_groups = set(selected_groups)
                data_entries = [e for e in data_entries if e['group'] in selected_groups]
            elif hasattr(self, 'group_checkbox_vars') and self.group_checkbox_vars:
                selected_groups = {g for g, var in self.group_checkbox_vars.items() if var.get()}
                data_entries = [e for e in data_entries if e['group'] in selected_groups]

        return data_entries


    def _get_current_data_entries(self):
        scope = self.get_param('export_scope', 'active')
        if scope == "active" or not self.all_speakers:
            return self._extract_active_data([self.active_speaker])
        elif scope == "separate":
            idx = getattr(self, 'current_preview_page', 0)
            if idx < 0 or idx >= len(self.all_speakers):
                idx = 0
                self.current_preview_page = 0
            current_speaker = self.all_speakers[idx]
            return self._extract_active_data([current_speaker])
        else:
            return self._extract_active_data(self.all_speakers)


    def _resolve_color_by(self, fallback=None):
        raw = None
        runtime_params = getattr(getattr(self, "_render_runtime", None), "params", None)
        if runtime_params is not None:
            raw = runtime_params.get("color_by") or runtime_params.get("groupby")
        if raw is None and hasattr(self, "params") and isinstance(self.params, dict):
            raw = self.params.get("color_by") or self.params.get("groupby")
        if raw is None and fallback is not None:
            raw = fallback
        if raw is None:
            raw = self.get_param("color_by", None) or self.get_param("groupby", "group")
        return str(raw or "group")


    def _resolve_facet_by(self, chart_type=None, fallback=None):
        if fallback is None and chart_type in ("density", "formant_density"):
            fallback = "group"
        raw = None
        runtime_params = getattr(getattr(self, "_render_runtime", None), "params", None)
        if runtime_params is not None:
            raw = runtime_params.get("facet_by") or runtime_params.get("layout")
            if raw is None:
                if chart_type in ("contour", None):
                    raw = runtime_params.get("contour_facet")
                elif chart_type == "density":
                    raw = runtime_params.get("density_facet")
                elif chart_type in ("formant_space", "formant_density"):
                    raw = runtime_params.get("formant_density_facet")
        if raw is None and hasattr(self, "params") and isinstance(self.params, dict):
            raw = self.params.get("facet_by") or self.params.get("layout")
            if raw is None:
                if chart_type in ("contour", None):
                    raw = self.params.get("contour_facet")
                elif chart_type == "density":
                    raw = self.params.get("density_facet")
                elif chart_type in ("formant_space", "formant_density"):
                    raw = self.params.get("formant_density_facet")
        if raw is None:
            raw = self.get_param("facet_by", None)
            if raw is None:
                if chart_type in ("contour", None):
                    raw = self.get_param("contour_facet", None)
                elif chart_type == "density":
                    raw = self.get_param("density_facet", None)
                elif chart_type in ("formant_space", "formant_density"):
                    raw = self.get_param("formant_density_facet", None)
            if raw is None:
                raw = fallback or "none"

        text = str(raw or "none").strip()
        low = text.lower()
        if low in ("none", "single", "单图展示 (不分面)", "不分面 (单图叠加)", "不分面 (混合叠加)", "不分面"):
            return "none"
        if low in ("group", "按字表组别分面", "按声调类型分面", "按字表组分面", "声调类型分面 (默认)") or "组" in text or "声调" in text:
            return "group"
        if low in ("syllable_position", "syllable", "按音节位置分面") or "音节" in text:
            return "syllable_position"
        if low in ("speaker", "speaker_name", "按发音人分面") or "发音人" in text:
            return "speaker"
        if low in ("label", "word", "按词语分面") or "词" in text:
            return "label"
        return "none"


    def _get_group_key(self, groupby_val=None):
        val = str(groupby_val if groupby_val is not None else self._resolve_color_by())
        low = val.strip().lower()
        if low in ("按词语", "按词项", "label", "word") or "词语" in val or "词项" in val:
            return 'label'
        if low in ("按发音人", "speaker", "speaker_name") or "发音人" in val:
            return 'speaker_name'
        if low in ("按单字音节", "按单字/音节", "syl_char", "syllable", "char") or "单字" in val or "音节" in val:
            return 'syl_char'
        if low.startswith("custom") or low.startswith("item_meta") or "自定义字段" in val:
            return 'custom_field'
        return 'group'


    def _prepare_entries_for_color_by(self, data_entries, group_key):
        if not data_entries:
            return data_entries
        color_raw = self._resolve_color_by()
        custom_field_name = self.get_param("custom_field", None)
        if not custom_field_name and ":" in color_raw:
            custom_field_name = color_raw.split(":", 1)[1].strip()
        if not custom_field_name:
            rule = self._get_chart_group_rule()
            custom_field_name = rule.get("field_name", "")

        out = []
        for idx, entry in enumerate(data_entries):
            e = dict(entry)
            e.setdefault("item_id", str(entry.get("item_id", f"item_{idx}")))
            # Populate syl_char
            syls = split_into_syllables(e.get("label", ""))
            e.setdefault("syl_char", syls[0] if syls else (e.get("label") or e.get("group") or "未标注"))
            # Populate custom_field
            meta = e.get("item_meta") if isinstance(e.get("item_meta"), dict) else {}
            if custom_field_name and custom_field_name in meta:
                e["custom_field"] = str(meta[custom_field_name] or "未标注")
            elif meta:
                first_k = sorted(meta.keys())[0]
                e["custom_field"] = str(meta[first_k] or "未标注")
            else:
                e["custom_field"] = str(e.get("chart_group") or e.get("group") or "未标注")

            if group_key == "syl_char" and "syl_data" in e and len(e.get("syl_data", [])) > 1:
                syl_list = e.get("syl_data", [])
                norm_list = e.get("normalized_syl_data", [])
                for s_i, s_item in enumerate(syl_list):
                    sub_e = dict(e)
                    ch = syls[s_i] if s_i < len(syls) else f"{e.get('label', '')}_字{s_i+1}"
                    sub_e["syl_char"] = ch
                    sub_e["syl_data"] = [s_item]
                    if s_i < len(norm_list):
                        sub_e["normalized_syl_data"] = [norm_list[s_i]]
                    out.append(sub_e)
            else:
                out.append(e)
        return out


    def _is_overview_heatmap_chart(self, chart_type):
        return chart_type in ("overview_heatmap", "formant_overview_heatmap")


    def _ordered_unique(self, values):
        ordered = []
        seen = set()
        for value in values:
            if value in seen:
                continue
            seen.add(value)
            ordered.append(value)
        return ordered


    def _default_chart_group_rule(self):
        return {
            "source": "default",
            "tag_mode": "each",
            "selected_values": [],
            "field_name": "",
        }


    def _normalize_chart_group_rule(self, rule=None):
        base = self._default_chart_group_rule()
        if isinstance(rule, dict):
            base.update(rule)
        source = str(base.get("source") or "default")
        if source not in {
            "default", "item_tags", "group_tags", "item_meta",
            "metadata_source", "label", "speaker_name"
        }:
            source = "default"
        tag_mode = str(base.get("tag_mode") or "each")
        if tag_mode not in {"each", "contains", "filter_default"}:
            tag_mode = "each"
        selected_values = base.get("selected_values") or []
        if isinstance(selected_values, str):
            selected_values = [v.strip() for v in selected_values.split(",") if v.strip()]
        selected_values = [str(v).strip() for v in selected_values if str(v).strip()]
        return {
            "source": source,
            "tag_mode": tag_mode,
            "selected_values": self._ordered_unique(selected_values),
            "field_name": str(base.get("field_name") or "").strip(),
        }


    def _get_chart_group_rule(self):
        return self._normalize_chart_group_rule(self.get_param("chart_group_rule", None))


    def _entry_wordlist_group(self, entry):
        return entry.get("wordlist_group") or entry.get("group") or "未分组"


    def _item_wordlist_metadata(self, item):
        item_meta = item.get("item_meta") or {}
        if not isinstance(item_meta, dict):
            item_meta = {}
        return {
            "wordlist_version": item.get("wordlist_version", "v1"),
            "wordlist_title": item.get("wordlist_title", ""),
            "item_note": item.get("item_note", ""),
            "item_tags": list(item.get("item_tags", []) or []),
            "item_aliases": list(item.get("item_aliases", []) or []),
            "item_meta": dict(item_meta),
            "group_note": item.get("group_note", ""),
            "group_tags": list(item.get("group_tags", []) or []),
            "metadata_source": item.get("metadata_source", ""),
        }


    def _attach_entry_group_defaults(self, entry):
        copied = dict(entry)
        base_group = copied.get("wordlist_group") or copied.get("group") or "未分组"
        copied["wordlist_group"] = base_group
        copied.setdefault("chart_group", copied.get("group", base_group))
        copied.setdefault("item_tags", [])
        copied.setdefault("group_tags", [])
        copied.setdefault("item_meta", {})
        copied.setdefault("metadata_source", "")
        copied.setdefault("item_note", "")
        copied.setdefault("group_note", "")
        copied.setdefault("wordlist_version", "v1")
        copied.setdefault("wordlist_title", "")
        copied.setdefault("item_aliases", [])
        return copied


    def _entry_tag_values(self, entry, source):
        values = entry.get(source) or []
        if isinstance(values, str):
            values = [v.strip() for v in values.replace("；", ",").replace("、", ",").split(",")]
        return self._ordered_unique([str(v).strip() for v in values if str(v).strip()])


    def _resolve_chart_groups_for_entry(self, entry, rule=None):
        rule = self._normalize_chart_group_rule(rule)
        source = rule["source"]
        base_group = self._entry_wordlist_group(entry)
        missing = "未标注"

        if source == "default":
            return [base_group], True
        if source == "label":
            return [str(entry.get("label") or missing)], True
        if source == "speaker_name":
            return [str(entry.get("speaker_name") or missing)], True
        if source == "metadata_source":
            return [str(entry.get("metadata_source") or missing)], True
        if source == "item_meta":
            field_name = rule.get("field_name", "")
            if not field_name:
                return [base_group], True
            item_meta = entry.get("item_meta") or {}
            value = item_meta.get(field_name, "") if isinstance(item_meta, dict) else ""
            value = str(value).strip() if value is not None else ""
            return [value or missing], True

        if source in {"item_tags", "group_tags"}:
            tags = self._entry_tag_values(entry, source)
            selected = set(rule.get("selected_values") or [])
            tag_mode = rule.get("tag_mode", "each")
            if tag_mode == "contains":
                target = next(iter(rule.get("selected_values") or []), "")
                if not target:
                    return [base_group], True
                return ([f"包含：{target}"] if target in tags else [f"不包含：{target}"]), True
            if tag_mode == "filter_default":
                if selected and not selected.intersection(tags):
                    return [], False
                return [base_group], True

            values = [tag for tag in tags if not selected or tag in selected]
            if not values:
                if selected:
                    return [], False
                values = [missing]
            return values, True

        return [base_group], True


    def _chart_grouping_preserves_original_group(self):
        return str(self.get_param("chart_type", "")) in ("tone_effect_time", "tone_sandhi", "sandhi_grid")


    def _apply_chart_grouping(self, data_entries, rule=None, preserve_original_group=None):
        rule = self._get_chart_group_rule() if rule is None else self._normalize_chart_group_rule(rule)
        preserve_original_group = (
            self._chart_grouping_preserves_original_group()
            if preserve_original_group is None else bool(preserve_original_group)
        )
        grouped_entries = []
        for raw_entry in data_entries or []:
            entry = self._attach_entry_group_defaults(raw_entry)
            base_group = self._entry_wordlist_group(entry)
            group_values, include_entry = self._resolve_chart_groups_for_entry(entry, rule)
            if not include_entry:
                continue
            if preserve_original_group:
                group_values = [base_group]
            for group_value in group_values:
                copied = dict(entry)
                chart_group = str(group_value or "未标注")
                copied["wordlist_group"] = base_group
                copied["chart_group"] = chart_group
                copied["group"] = base_group if preserve_original_group else chart_group
                grouped_entries.append(copied)
        return grouped_entries


    def _collect_chart_grouping_options(self, data_entries=None):
        if data_entries is None:
            data_entries = getattr(self, "_all_base_group_filter_entries", None)
        if data_entries is None:
            try:
                data_entries = self._extract_active_data(
                    self.all_speakers if self.all_speakers else [self.active_speaker],
                    filter_groups=False,
                    apply_chart_grouping=False,
                )
            except Exception:
                data_entries = []

        item_tags = Counter()
        group_tags = Counter()
        meta_values = {}
        metadata_sources = Counter()
        for entry in data_entries or []:
            entry = self._attach_entry_group_defaults(entry)
            item_tags.update(self._entry_tag_values(entry, "item_tags"))
            group_tags.update(self._entry_tag_values(entry, "group_tags"))
            metadata_source = str(entry.get("metadata_source") or "").strip()
            if metadata_source:
                metadata_sources[metadata_source] += 1
            item_meta = entry.get("item_meta") or {}
            if isinstance(item_meta, dict):
                for key, value in item_meta.items():
                    key = str(key).strip()
                    value = str(value).strip() if value is not None else ""
                    if not key:
                        continue
                    meta_values.setdefault(key, Counter())[value or "未标注"] += 1
        return {
            "item_tags": item_tags,
            "group_tags": group_tags,
            "item_meta": meta_values,
            "metadata_source": metadata_sources,
        }


    def _chart_group_counts_for_rule(self, rule=None, data_entries=None, preserve_original_group=False):
        if data_entries is None:
            data_entries = getattr(self, "_all_base_group_filter_entries", [])
        grouped_entries = self._apply_chart_grouping(
            data_entries,
            rule=rule,
            preserve_original_group=preserve_original_group,
        )
        counts = Counter()
        for entry in grouped_entries:
            counts[entry.get("group") or "未标注"] += 1
        return counts


    def _format_chart_group_rule_summary(self, rule=None):
        rule = self._normalize_chart_group_rule(rule)
        source = rule["source"]
        selected = rule.get("selected_values") or []
        values_text = "、".join(selected[:3])
        if len(selected) > 3:
            values_text += f" 等 {len(selected)} 项"
        if source == "default":
            return "按默认组"
        if source == "item_tags":
            if rule.get("tag_mode") == "contains":
                return f"词项标签：包含/不包含 {values_text or '未选择'}"
            if rule.get("tag_mode") == "filter_default":
                return f"筛选词项标签：{values_text or '全部'}"
            return f"按词项标签：{values_text or '全部'}"
        if source == "group_tags":
            if rule.get("tag_mode") == "contains":
                return f"组标签：包含/不包含 {values_text or '未选择'}"
            if rule.get("tag_mode") == "filter_default":
                return f"筛选组标签：{values_text or '全部'}"
            return f"按组标签：{values_text or '全部'}"
        if source == "item_meta":
            return f"按自定义字段：{rule.get('field_name') or '未选择'}"
        if source == "metadata_source":
            return "按复核状态"
        if source == "label":
            return "按词项"
        if source == "speaker_name":
            return "按发音人"
        return "按默认组"


    def _split_tone_pair(self, group, entry=None):
        raw_text = str(group or "").strip()
        text = raw_text.replace("＋", "+").replace("/", "+").replace("、", "+").replace("－", "+").replace("-", "+")
        if "+" in text:
            parts = [part.strip() for part in text.split("+") if part.strip()]
            if len(parts) >= 2:
                return parts[0], parts[1]

        # Match patterns like "11_阴阴", "12_阴阳", "24_阳去"
        m = re.match(r"^(\d)(\d)[_\s]*(\S)(\S)$", raw_text)
        if m:
            return f"{m.group(1)}_{m.group(3)}", f"{m.group(2)}_{m.group(4)}"
        m2 = re.match(r"^(\d)(\d)$", raw_text)
        if m2:
            return m2.group(1), m2.group(2)
        m3 = re.match(r"^([阴平阳上入去甲乙丙丁ABCDT1-8]{1})([阴平阳上入去甲乙丙丁ABCDT1-8]{1})$", raw_text)
        if m3 and raw_text not in ("阴平", "阳平", "上声", "去声", "入声", "阴入", "阳入", "阴上", "阳上", "阴去", "阳去"):
            return m3.group(1), m3.group(2)

        if isinstance(entry, dict):
            meta = entry.get("item_meta")
            if isinstance(meta, dict):
                ft = meta.get("front_tone") or meta.get("前字调类") or meta.get("t1")
                bt = meta.get("back_tone") or meta.get("后字调类") or meta.get("t2")
                if ft and bt:
                    return str(ft).strip(), str(bt).strip()
        return None, None


    def _is_two_syllable_tone_entry(self, entry):
        if len(entry.get("syl_data", [])) != 2:
            return False
        front, back = self._split_tone_pair(self._entry_wordlist_group(entry), entry=entry)
        return bool(front and back)


    def _has_tone_effect_time_data(self, data_entries):
        fronts = set()
        backs = set()
        combos = set()
        for entry in data_entries or []:
            if not self._is_two_syllable_tone_entry(entry):
                continue
            front, back = self._split_tone_pair(self._entry_wordlist_group(entry), entry=entry)
            fronts.add(front)
            backs.add(back)
            combos.add((front, back))
        return len(fronts) >= 2 and len(backs) >= 2 and len(combos) >= 4


    def _get_tone_chart_type_values(self, data_entries=None, work_mode=None):
        mode = self._normalize_work_mode(work_mode)
        if mode == "qc":
            return [
                "1. 词条级逐行质检热图",
                "2. 组内个体原始曲线叠加图",
                "3. 检出率与发音人基频体检图",
            ]
        values = [
            "1. 声调格局与时序轮廓图",
            "2. 连读变调与协同发音图",
            "3. 声调声学特征分布雨云图",
            "声调轮廓图",
            "声调分布图",
            "时序密度图",
            "数据质量检查",
            "声调组别概览图",
        ]
        if data_entries is None:
            data_entries = getattr(self, "_all_group_filter_entries", [])
        if self._has_tone_effect_time_data(data_entries):
            values.append("二字组调类效应时间进程")
        return values


    def _get_formant_chart_type_values(self, work_mode=None):
        mode = self._normalize_work_mode(work_mode)
        if mode == "qc":
            return [
                "1. 词条级逐行质检热图",
                "2. 组内个体原始曲线叠加图",
                "3. 检出率与发音人共振峰体检图",
            ]
        return [
            "1. 元音声学空间图",
            "2. 共振峰时序轨迹图",
            "3. 元音对立重叠度与分布图",
            "元音共振峰空间图",
            "共振峰时序轨迹图",
            "共振峰时空密度图",
            "共振峰组别概览图",
        ]


    def _get_tone_overview_metric_values(self, is_integrated=False, data_entries=None):
        values = [
            "与组内均值的偏差 (Z-score / 残差热图)",
            "均值热图 (Mean Map)",
        ]
        if is_integrated:
            values.append("标准差热图 (SD Map)")
        if data_entries is None:
            data_entries = getattr(self, "_all_group_filter_entries", [])
        if self._has_tone_effect_time_data(data_entries):
            values.append("调类组合前后字均值热图")
        return values


    def _tone_effect_blank(self, message):
        fig, ax = plt.subplots(figsize=(7.0, 4.4))
        ax.text(0.5, 0.5, message, ha="center", va="center", fontsize=12, color="#374151")
        ax.axis("off")
        fig.tight_layout()
        return fig


    def _tone_effect_point_x(self, syl_index, point_index, point_count):
        if point_count <= 1:
            percent = 50.0
        else:
            percent = 5.0 + point_index * (90.0 / float(point_count - 1))
        return percent if syl_index == 0 else 100.0 + percent


    def _collect_tone_effect_time_rows(self, data_entries):
        rows = []
        for entry in data_entries:
            if not self._is_two_syllable_tone_entry(entry):
                continue
            front, back = self._split_tone_pair(self._entry_wordlist_group(entry), entry=entry)
            syl_list = entry.get("normalized_syl_data") or entry.get("syl_data", [])
            if len(syl_list) != 2:
                continue
            for syl_index in range(2):
                _dur, points = syl_list[syl_index]
                point_count = min(len(points), self.project_tree.app_state_params.get("pts", 11))
                for point_index in range(point_count):
                    try:
                        value = float(points[point_index])
                    except Exception:
                        continue
                    if not np.isfinite(value):
                        continue
                    rows.append({
                        "front": front,
                        "back": back,
                        "syl": syl_index + 1,
                        "x": self._tone_effect_point_x(syl_index, point_index, point_count),
                        "value": value,
                    })
        return rows


    def _residual_sse_for_tone_effect(self, y_values, columns):
        design_columns = [[1.0 for _ in y_values]]
        for values in columns:
            unique = []
            for value in values:
                if value not in unique:
                    unique.append(value)
            for value in unique[1:]:
                design_columns.append([1.0 if current == value else 0.0 for current in values])
        design = np.asarray(design_columns, dtype=float).T
        y = np.asarray(y_values, dtype=float)
        fitted = np.linalg.lstsq(design, y, rcond=None)[0]
        residual = y - design.dot(fitted)
        return float(np.sum(residual * residual))


    def _is_tone_combination_overview_metric(self):
        metric_val = str(self.get_param('overview_metric', 'mean'))
        return "调类组合" in metric_val or "combination" in metric_val.lower()


    def _build_overview_word_rows(self, data_entries):
        rows = []
        present_groups = self._ordered_unique(entry['group'] for entry in data_entries)
        preferred_order = list(getattr(self, 'available_groups', []) or [])
        group_order = [group_name for group_name in preferred_order if group_name in present_groups]
        for group_name in present_groups:
            if group_name not in group_order:
                group_order.append(group_name)

        for group_name in group_order:
            seen_labels = set()
            for entry in data_entries:
                if entry['group'] != group_name:
                    continue
                label = entry['label']
                if label in seen_labels:
                    continue
                seen_labels.add(label)
                rows.append({
                    'row_id': (group_name, label),
                    'group': group_name,
                    'label': label,
                })
        return rows


    def _build_overview_word_pages(self, data_entries, page_size=24):
        rows = self._build_overview_word_rows(data_entries)
        if not rows:
            return rows, []

        grouped_buckets = []
        current_group = None
        current_bucket = []
        for row in rows:
            if row['group'] != current_group:
                if current_bucket:
                    grouped_buckets.append(current_bucket)
                current_group = row['group']
                current_bucket = [row]
            else:
                current_bucket.append(row)
        if current_bucket:
            grouped_buckets.append(current_bucket)

        pages = []
        current_page = []
        for bucket in grouped_buckets:
            if len(bucket) > page_size:
                if current_page:
                    pages.append(current_page)
                    current_page = []
                for idx in range(0, len(bucket), page_size):
                    chunk = bucket[idx:idx + page_size]
                    if len(chunk) == page_size:
                        pages.append(chunk)
                    else:
                        current_page = list(chunk)
            else:
                if current_page and len(current_page) + len(bucket) > page_size:
                    pages.append(current_page)
                    current_page = list(bucket)
                else:
                    current_page.extend(bucket)
        if current_page:
            pages.append(current_page)

        return rows, pages


    def _get_group_pagination_state(self, data_entries, chart_type, groupby_val=None):
        chart_type = self._normalize_chart_type(chart_type)
        group_key = self._get_group_key(groupby_val)
        prepared = self._prepare_entries_for_color_by(data_entries, group_key)
        unique_groups = self._ordered_unique(entry.get(group_key, entry.get('group')) for entry in prepared)
        total_groups = len(unique_groups)
        is_overview_hm = (
            self._is_overview_heatmap_chart(chart_type)
            and not (chart_type == "overview_heatmap" and self._is_tone_combination_overview_metric())
        )
        rows, pages = ([], [])
        if is_overview_hm and group_key == "label":
            rows, pages = self._build_overview_word_pages(prepared, page_size=24)
        is_paginated_heatmap = bool(is_overview_hm and group_key == "label" and len(rows) > 24 and len(pages) > 1)

        state = {
            'group_key': group_key,
            'unique_groups': unique_groups,
            'total_groups': total_groups,
            'is_paginated_heatmap': is_paginated_heatmap,
            'total_pages': 1,
            'pages': pages if is_paginated_heatmap else [],
        }

        if is_paginated_heatmap:
            state['total_pages'] = len(pages) if pages else 1
        elif is_overview_hm and group_key != "label" and total_groups > 24:
            state['total_pages'] = math.ceil(total_groups / 24)
        elif self._is_overview_heatmap_chart(chart_type):
            state['total_pages'] = 1
        elif chart_type in ("formant_trajectory", "tone_effect_time", "tone_sandhi", "sandhi_grid", "formant_pillai", "pillai_matrix"):
            state['total_pages'] = 1
        else:
            state['total_pages'] = math.ceil(total_groups / 8) if total_groups > 0 else 1

        return state


    def _normalize_chart_type(self, raw_chart_type):
        text = str(raw_chart_type or "contour").strip()
        low = text.lower()
        mapping = {
            "contour": "contour",
            "1. 声调格局与时序轮廓图": "contour",
            "声调格局与时序轮廓图": "contour",
            "声调格局与轮廓图": "contour",
            "声调轮廓图": "contour",
            "tone_sandhi": "tone_sandhi",
            "sandhi_grid": "tone_sandhi",
            "2. 连读变调与协同发音图": "tone_sandhi",
            "连读变调与协同发音图": "tone_sandhi",
            "二字组连读变调网格图": "tone_sandhi",
            "distribution": "distribution",
            "3. 声调声学特征分布雨云图": "distribution",
            "声调声学特征分布雨云图": "distribution",
            "声调分布图": "distribution",
            "雨云图 (raincloud)": "distribution",
            "density": "density",
            "时序密度图": "density",
            "tone_effect_time": "tone_effect_time",
            "二字组调类效应时间进程": "tone_effect_time",
            "overview_heatmap": "overview_heatmap",
            "声调组别概览图": "overview_heatmap",
            "词条级逐行质检热图": "overview_heatmap",
            "formant_overview_heatmap": "formant_overview_heatmap",
            "共振峰组别概览图": "formant_overview_heatmap",
            "spaghetti_qc": "spaghetti_qc",
            "2. 组内个体原始曲线叠加图": "spaghetti_qc",
            "组内个体原始曲线叠加图": "spaghetti_qc",
            "quality": "quality",
            "数据质量检查": "quality",
            "3. 检出率与发音人基频体检图": "quality",
            "3. 检出率与发音人共振峰体检图": "quality",
            "检出率与发音人基频/共振峰体检图": "quality",
            "formant_space": "formant_space",
            "1. 元音声学空间图": "formant_space",
            "元音声学空间图": "formant_space",
            "元音共振峰空间图": "formant_space",
            "formant_trajectory": "formant_trajectory",
            "2. 共振峰时序轨迹图": "formant_trajectory",
            "共振峰时序轨迹图": "formant_trajectory",
            "formant_pillai": "formant_pillai",
            "pillai_matrix": "formant_pillai",
            "vowel_merger": "formant_pillai",
            "3. 元音对立重叠度与分布图": "formant_pillai",
            "元音对立重叠度与分布图": "formant_pillai",
            "元音对立重叠度矩阵 (pillai score)": "formant_pillai",
            "formant_density": "formant_density",
            "共振峰时空密度图": "formant_density",
        }
        if text in mapping:
            res = mapping[text]
        elif low in mapping:
            res = mapping[low]
        elif "词条级逐行质检热图" in text:
            res = "formant_overview_heatmap" if self._is_formant_mode() else "overview_heatmap"
        elif "原始曲线叠加" in text or "spaghetti" in low:
            res = "spaghetti_qc"
        elif "体检" in text:
            res = "quality"
        elif "变调" in text or "sandhi" in low:
            res = "tone_sandhi"
        elif "雨云" in text or "raincloud" in low:
            res = "distribution"
        elif "pillai" in low or "重叠度" in text:
            res = "formant_pillai"
        else:
            res = text
        if res == "overview_heatmap" and self._is_formant_mode() and text == "1. 词条级逐行质检热图":
            res = "formant_overview_heatmap"
        return res
