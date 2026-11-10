# -*- coding: utf-8 -*-
"""
PhonTracer CLI - Session Core and Lifecycle Mixin.
Handles CLI initialization, workspace isolation, property proxies, state change tracking,
command normalization, and line parsing.
"""
from __future__ import annotations

import cmd
import concurrent.futures
import copy
import glob
import inspect
import io
import json
import logging
import multiprocessing
import os
import re
import shlex
import shutil
import sys
import tempfile
import time
import zipfile
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

try:
    import parselmouth
except ImportError:
    from modules import _parselmouth_fallback as parselmouth
    sys.modules.setdefault("parselmouth", parselmouth)
    sys.modules.setdefault("parselmouth.praat", parselmouth.praat)
import textgrid

from modules.anomaly_detection import detect_pitch_anomaly_points
from modules.cli_app.io_utils import (
    COMMON_AUDIO_EXTENSIONS,
    LoggerOut,
    _natural_sort_key,
    dumps_utf8,
)
from modules.data_utils import (
    build_five_point_chart,
    fuzzy_match_word_to_path,
    get_export_text_for_item,
    make_textgrid_export_stem,
    parse_wordlist,
    split_into_syllables,
)
from modules.project_manager import ProjectManager
from modules.report_generator import check_item_has_empty_f0
from modules.speaker_manager import SpeakerManager
from modules.version import APP_NAME, __version__
from modules.wordlist_v2 import (
    DEFAULT_REVIEW_STATUS,
    WORDLIST_SCHEMA,
    apply_record_metadata,
    build_document_from_csv_text,
    build_document_from_messy_text,
    build_document_from_structured_text,
    build_document_from_v1_text,
    build_wordlist_document_from_items,
    create_empty_wordlist_document,
    flatten_wordlist_document,
    load_wordlist_document,
    load_wordlist_file,
    load_wordlist_from_csv_file,
    looks_like_csv_wordlist_text,
    looks_like_structured_wordlist_text,
    metadata_from_record,
    normalize_wordlist_document,
    save_wordlist_document,
    save_wordlist_file,
    summarize_wordlist_document,
    validate_wordlist_document,
)


class CLISessionCoreMixin:
    """
    Session core mixin managing lifecycle, state properties, workspace isolation,
    and command dispatch infrastructure.
    """
    intro = f"""{APP_NAME}语音学标注与声学分析系统 V{__version__} - 命令行批处理工作台
你好，我是 PhonTracer 命令行控制台，支持自动化批处理与工程脚本交互。
Type 'help' or '?' to list commands. Type 'status' for workspace overview.
"""


    prompt = "(phontracer) "


    COMMON_AUDIO_EXTENSIONS = COMMON_AUDIO_EXTENSIONS


    def __init__(
        self,
        workspace_dir=None,
        shared_workspace=False,
        project_path=None,
        lang=None,
        quiet=False,
        json_mode=False,
    ):
        super().__init__()
        self.speaker_manager = SpeakerManager()
        self.lang = lang if lang in ('zh', 'en') else 'en'
        self.quiet = bool(quiet)
        self.json_mode = bool(json_mode)
        if self.lang == 'zh' and not self.quiet and not self.json_mode:
            self.prompt = "(phontracer)[中文] "
        if self.quiet or self.json_mode:
            self.intro = ""
            self.prompt = ""

        self._executor = None
        self._wordlist_doc = None
        self.global_wordlist_document = None
        self.log_file = None
        self.original_stdout = sys.stdout
        self._last_emit_success = True
        self._last_emit_payload = None
        self._session_dirty = False
        self._defer_persistence = False
        self._explicit_workspace_dir = os.path.abspath(workspace_dir) if workspace_dir else None
        self._bound_project_path = os.path.abspath(project_path) if project_path else None

        # 隔离工作区处理：默认分配专属临时工作区，避免冲毁 GUI 正在运行的主工作区
        self._temp_workspace_dir = None
        if workspace_dir:
            cli_ws = os.path.abspath(workspace_dir)
            os.makedirs(cli_ws, exist_ok=True)
        elif shared_workspace:
            cli_ws = None
        else:
            import tempfile
            self._temp_workspace_dir = tempfile.mkdtemp(prefix="phontracer_cli_ws_")
            cli_ws = self._temp_workspace_dir

        self.project_manager = ProjectManager(self, workspace_dir=cli_ws)

        # 如果显式指定了 --workspace 且存在 project.json，自动恢复上次保存的工作区状态
        if self._explicit_workspace_dir:
            ws_proj_json = os.path.join(self._explicit_workspace_dir, "project.json")
            if os.path.exists(ws_proj_json):
                try:
                    if self.project_manager.load_from_workspace():
                        self._sync_cli_state_after_project_load()
                except Exception:
                    pass

        # 如果指定了 --project 且文件存在，自动加载该工程归档
        if self._bound_project_path and os.path.exists(self._bound_project_path):
            try:
                if self.project_manager.load_project(self._bound_project_path):
                    self._sync_cli_state_after_project_load()
            except Exception:
                pass


    _natural_sort_key = staticmethod(_natural_sort_key)


    @property
    def wordlist_doc(self):
        spk = getattr(self, 'speaker_manager', None) and self.speaker_manager.get_active_speaker()
        if spk is not None and getattr(spk, 'wordlist_doc', None) is not None:
            return spk.wordlist_doc
        return getattr(self, '_wordlist_doc', None)


    @wordlist_doc.setter
    def wordlist_doc(self, val):
        norm_val = normalize_wordlist_document(val) if isinstance(val, dict) else val
        self._wordlist_doc = norm_val
        self.global_wordlist_document = norm_val
        spk = getattr(self, 'speaker_manager', None) and self.speaker_manager.get_active_speaker()
        if spk is not None:
            spk.wordlist_doc = norm_val
        pm = getattr(self, 'project_manager', None)
        if pm is not None and isinstance(norm_val, dict):
            preserved = getattr(pm, '_preserved_top_level', None)
            if not isinstance(preserved, dict):
                preserved = {}
                pm._preserved_top_level = preserved
            preserved['wordlist_doc'] = copy.deepcopy(norm_val)
            preserved['wordlist_version'] = 'v2'
            preserved['wordlist_title'] = norm_val.get('title', '')
            preserved['wordlist_note'] = norm_val.get('note', '')
            preserved['groups'] = copy.deepcopy(norm_val.get('groups', []))


    @staticmethod
    def _is_empty_default_speaker(spk):
        if spk is None:
            return False
        default_names = {"发音人 1", "Speaker 1", "默认发音人"}
        if getattr(spk, "name", "") not in default_names:
            return False
        has_items = bool(getattr(spk, "items", None))
        has_long = bool(
            getattr(spk, "long_audio_path", None)
            or getattr(spk, "cli_long_snd_path", None)
            or getattr(spk, "pending_long_snd", None) is not None
        )
        has_batch = bool(getattr(spk, "batch_paths", None) or getattr(spk, "pending_batch_paths", None))
        return not has_items and not has_long and not has_batch


    def _expand_batch_audio_inputs(self, raw_args):
        """
        Expand file paths, directory paths, or wildcard glob patterns into a naturally sorted list of audio file paths.
        Returns (valid_paths, error_message, hint_message).
        """
        syntax_hint = (
            "支持的语法：1) 单个或多个音频文件路径（如 load_batch a.wav b.wav）；"
            "2) 音频所在目录路径（如 load_batch ./female_isolated）；"
            "3) 通配符路径（如 load_batch ./male_isolated/*.wav）。"
            f"支持的常见音频后缀：{', '.join(self.COMMON_AUDIO_EXTENSIONS)}"
        )
        valid_paths = []
        seen = set()

        def add_path(p):
            key = os.path.normcase(os.path.abspath(p))
            if key not in seen:
                seen.add(key)
                valid_paths.append(p)

        for token in raw_args:
            p = str(token).strip()
            if not p:
                continue
            has_wildcard = any(ch in p for ch in ('*', '?', '['))
            if os.path.isdir(p):
                dir_audios = [
                    os.path.join(p, fn)
                    for fn in os.listdir(p)
                    if os.path.isfile(os.path.join(p, fn))
                    and fn.lower().endswith(self.COMMON_AUDIO_EXTENSIONS)
                ]
                dir_audios.sort(key=self._natural_sort_key)
                if not dir_audios:
                    return [], f"No supported audio files found in directory: {p}", syntax_hint
                for ap in dir_audios:
                    add_path(ap)
            elif has_wildcard:
                matched = glob.glob(p, recursive=('**' in p))
                matched_files = [
                    m for m in matched
                    if os.path.isfile(m)
                    and (
                        m.lower().endswith(self.COMMON_AUDIO_EXTENSIONS)
                        or os.path.splitext(p)[1] != ''
                    )
                ]
                matched_files.sort(key=self._natural_sort_key)
                if not matched_files:
                    return [], f"No audio files matched wildcard pattern: {p}", syntax_hint
                for ap in matched_files:
                    add_path(ap)
            elif os.path.isfile(p):
                add_path(p)
            else:
                return [], f"File or directory not found: {p}", syntax_hint

        if not valid_paths:
            return [], "No valid audio files found", syntax_hint
        return valid_paths, None, None


    def _load_wordlist_any(self, filepath, title=None):
        """
        Unified loader for .ptwl, .csv, structured .txt, and messy .txt wordlists.
        Returns (normalized_doc, groups, flat_words, records).
        """
        ext = os.path.splitext(str(filepath))[1].lower()
        default_title = title or os.path.splitext(os.path.basename(filepath))[0] or "未命名字表"

        if ext == ".ptwl":
            doc = load_wordlist_file(filepath, title=title)
        elif ext == ".csv":
            doc = load_wordlist_from_csv_file(filepath, title=default_title)
        else:
            last_err = None
            raw_text = None
            for enc in ("utf-8-sig", "utf-8", "gbk"):
                try:
                    with open(filepath, "r", encoding=enc) as f:
                        raw_text = f.read()
                    break
                except UnicodeDecodeError as exc:
                    last_err = exc
            if raw_text is None:
                with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                    raw_text = f.read()

            stripped = (raw_text or "").lstrip("﻿").strip()
            if stripped.startswith("{"):
                try:
                    doc = normalize_wordlist_document(json.loads(stripped))
                except Exception:
                    doc = None
            else:
                doc = None

            if doc is None:
                if looks_like_structured_wordlist_text(raw_text):
                    doc = build_document_from_structured_text(raw_text, title=default_title)
                elif looks_like_csv_wordlist_text(raw_text):
                    doc = build_document_from_csv_text(raw_text, title=default_title)
                else:
                    doc = build_document_from_messy_text(raw_text, title=default_title)

            if title:
                doc["title"] = str(title).strip()

        doc = normalize_wordlist_document(doc)
        groups, flat_words, records = flatten_wordlist_document(doc)
        return doc, groups, flat_words, records


    def _attach_wordlist_record_to_item(self, item, record=None, speaker=None):
        """
        Mount group, label, tags, note, and fields/custom_fields (plus v2 metadata and speaker info) onto an item dict.
        """
        spk = speaker or self.current_speaker
        apply_record_metadata(item, record)
        if record:
            grp_name = record.get("group") or item.get("group") or "未分组"
            lbl = record.get("label") or record.get("word") or item.get("label") or ""
            tags = list(record.get("item_tags") or record.get("tags") or item.get("tags") or [])
            note = str(record.get("item_note") or record.get("note") or item.get("note") or "")
            aliases = list(record.get("item_aliases") or record.get("aliases") or item.get("aliases") or [])
            fields = dict(
                record.get("item_meta")
                or record.get("fields")
                or record.get("custom_fields")
                or record.get("meta")
                or item.get("fields")
                or {}
            )
            group_tags = list(record.get("group_tags") or item.get("group_tags") or [])
            group_note = str(record.get("group_note") or item.get("group_note") or "")
            group_meta = dict(record.get("group_meta") or item.get("group_meta") or {})
            metadata_source = str(record.get("metadata_source") or item.get("metadata_source") or "人工填写")
            wl_title = str(record.get("wordlist_title") or item.get("wordlist_title") or "")
        else:
            grp_name = item.get("group") or "未分组"
            lbl = item.get("label") or item.get("word") or ""
            tags = list(item.get("tags") or item.get("item_tags") or [])
            note = str(item.get("note") or item.get("item_note") or "")
            aliases = list(item.get("aliases") or item.get("item_aliases") or [])
            fields = dict(item.get("fields") or item.get("custom_fields") or item.get("item_meta") or item.get("meta") or {})
            group_tags = list(item.get("group_tags") or [])
            group_note = str(item.get("group_note") or "")
            group_meta = dict(item.get("group_meta") or {})
            metadata_source = str(item.get("metadata_source") or "人工填写")
            wl_title = str(item.get("wordlist_title") or "")

        item["group"] = grp_name
        item["label"] = lbl
        item["word"] = lbl
        item["tags"] = list(tags)
        item["item_tags"] = list(tags)
        item["note"] = note
        item["item_note"] = note
        item["aliases"] = list(aliases)
        item["item_aliases"] = list(aliases)
        item["fields"] = dict(fields)
        item["custom_fields"] = dict(fields)
        item["meta"] = dict(fields)
        item["item_meta"] = dict(fields)
        item["group_tags"] = list(group_tags)
        item["group_note"] = group_note
        item["group_meta"] = dict(group_meta)
        item["metadata_source"] = metadata_source
        item["wordlist_version"] = "v2"
        if wl_title:
            item["wordlist_title"] = wl_title
        if spk is not None:
            item["speaker"] = getattr(spk, "name", "")
            item["speaker_id"] = getattr(spk, "id", "")
        return item


    def _build_integrated_summary(self, speakers):
        """
        Build multi-speaker integrated summary including merged speaker names,
        valid item counts per speaker, and F0 baseline ranges for 5-level normalization.
        """
        pts = int(self.params.get('pts', 11)) if hasattr(self, 'params') and isinstance(self.params, dict) else 11
        speaker_names = []
        speaker_item_counts = {}
        speaker_baselines = {}

        for s in speakers:
            if self._is_empty_default_speaker(s) and len(speakers) > 1:
                continue
            spk_name = getattr(s, 'name', '未命名')
            speaker_names.append(spk_name)
            valid_count = 0
            f0_vals = []

            for item in getattr(s, 'items', {}).values():
                if not isinstance(item, dict) or item.get('is_excluded', False) or item.get('missing', False):
                    continue
                if not item.get('success', True):
                    continue
                t_s, t_e = item.get('start'), item.get('end')
                if t_s is None or t_e is None or float(t_e) <= float(t_s):
                    continue
                valid_count += 1

                # Collect F0 points for speaker baseline
                try:
                    self._ensure_item_loaded(item)
                    total_dur, syl_data = self._extract_syl_data(item, pts)
                    if total_dur > 0 and syl_data:
                        for _dur, freqs in syl_data:
                            for f in freqs:
                                if f is not None and not np.isnan(f) and f > 0:
                                    f0_vals.append(float(f))
                        continue
                except Exception:
                    pass

                # Fallback to preview_f0 or pitch_data if _extract_syl_data returned nothing
                if isinstance(item.get('preview_f0'), (list, tuple)):
                    for f in item['preview_f0']:
                        if f is not None and not np.isnan(f) and f > 0:
                            f0_vals.append(float(f))
                elif isinstance(item.get('pitch_data'), dict):
                    p_xs = np.asarray(item['pitch_data'].get('xs', []), dtype=float)
                    p_freqs = np.asarray(item['pitch_data'].get('freqs', []), dtype=float)
                    if len(p_xs) == len(p_freqs) and len(p_xs) > 0:
                        mask = (p_xs >= float(t_s)) & (p_xs <= float(t_e)) & np.isfinite(p_freqs) & (p_freqs > 0)
                        f0_vals.extend(p_freqs[mask].tolist())

            speaker_item_counts[spk_name] = valid_count
            if f0_vals:
                min_hz = round(float(np.min(f0_vals)), 2)
                max_hz = round(float(np.max(f0_vals)), 2)
                mean_hz = round(float(np.mean(f0_vals)), 2)
                sd_hz = round(float(np.std(f0_vals)), 2)
            else:
                min_hz = 0.0
                max_hz = 0.0
                mean_hz = 0.0
                sd_hz = 0.0

            speaker_baselines[spk_name] = {
                "min_hz": min_hz,
                "max_hz": max_hz,
                "min_f0_hz": min_hz,
                "max_f0_hz": max_hz,
                "f0_min": min_hz,
                "f0_max": max_hz,
                "mean_hz": mean_hz,
                "sd_hz": sd_hz,
                "range_hz": [min_hz, max_hz],
            }

        return {
            "speakers": speaker_names,
            "speaker_count": len(speaker_names),
            "speaker_item_counts": speaker_item_counts,
            "speaker_baselines": speaker_baselines,
            "total_valid_items": sum(speaker_item_counts.values()),
        }


    def _build_wordlist_v2_doc_from_project(self, title=None):
        from modules.wordlist_v2 import normalize_wordlist_document, DEFAULT_REVIEW_STATUS
        doc_title = title or getattr(self, "project_title", None) or ""
        groups_dict = OrderedDict()

        for spk in self.speaker_manager.get_all_speakers():
            for item_id, item in spk.items.items():
                if not isinstance(item, dict):
                    continue
                if not doc_title and item.get("wordlist_title"):
                    doc_title = str(item.get("wordlist_title"))
                gname = str(item.get("group") or "默认组")
                if gname not in groups_dict:
                    groups_dict[gname] = {
                        "id": f"grp_{len(groups_dict) + 1}",
                        "name": gname,
                        "note": str(item.get("group_note") or ""),
                        "tags": list(item.get("group_tags") or []),
                        "meta": {},
                        "items_by_label": OrderedDict(),
                    }
                g_entry = groups_dict[gname]
                if not g_entry["note"] and item.get("group_note"):
                    g_entry["note"] = str(item.get("group_note"))
                for gt in (item.get("group_tags") or []):
                    if gt and gt not in g_entry["tags"]:
                        g_entry["tags"].append(gt)

                lbl = str(item.get("label") or item.get("word") or item_id).strip()
                if not lbl:
                    continue
                custom_meta = {}
                for mk_src in ("meta", "item_meta", "fields", "custom_fields"):
                    mval = item.get(mk_src)
                    if isinstance(mval, dict):
                        for mk, mv in mval.items():
                            if mk is not None and str(mk).strip() and mv is not None and str(mv).strip():
                                custom_meta[str(mk).strip()] = str(mv).strip()
                tags = list(item.get("item_tags") or item.get("tags") or [])
                aliases = list(item.get("item_aliases") or item.get("aliases") or [])
                note = str(item.get("item_note") or item.get("note") or "")
                source = str(item.get("metadata_source") or DEFAULT_REVIEW_STATUS)

                if lbl not in g_entry["items_by_label"]:
                    g_entry["items_by_label"][lbl] = {
                        "id": str(item_id),
                        "label": lbl,
                        "note": note,
                        "tags": tags,
                        "aliases": aliases,
                        "meta": custom_meta,
                        "metadata_source": source,
                    }
                else:
                    existing = g_entry["items_by_label"][lbl]
                    if not existing["note"] and note:
                        existing["note"] = note
                    for t in tags:
                        if t not in existing["tags"]:
                            existing["tags"].append(t)
                    for a in aliases:
                        if a not in existing["aliases"]:
                            existing["aliases"].append(a)
                    existing["meta"].update(custom_meta)

        groups_list = []
        for gname, g_entry in groups_dict.items():
            groups_list.append({
                "id": g_entry["id"],
                "name": gname,
                "note": g_entry["note"],
                "tags": g_entry["tags"],
                "meta": g_entry["meta"],
                "items": list(g_entry["items_by_label"].values()),
            })

        raw_doc = {
            "title": doc_title or "PhonTracer工程字表",
            "note": "",
            "groups": groups_list,
        }
        return normalize_wordlist_document(raw_doc)


    def _export_fallback_teproj_archive(self, teproj_path, project_data):
        """当内存中为纯缓存数据（无本地物理音频文件）时，直接生成合规的 .teproj 归档包。"""
        import zipfile
        import io
        import time
        import copy

        state_copy = copy.deepcopy(project_data)
        state_copy["version"] = "1.0"
        state_copy["format_version"] = "2.0"
        state_copy["software_version"] = __version__
        state_copy["save_time"] = time.strftime("%Y-%m-%d %H:%M:%S")
        state_copy["trim_silence"] = bool(self.params.get("trim_silence", True))
        state_copy["export_numbering_rule"] = "continuous"

        npz_blobs = {}
        for spk_id, spk in state_copy.get("speakers", {}).items():
            items = spk.get("items", {})
            for item_id, item in items.items():
                if not isinstance(item, dict):
                    continue
                pd = item.pop("pitch_data", None)
                if isinstance(pd, dict) and pd.get("xs") is not None and pd.get("freqs") is not None:
                    rel_p = f"data/{spk_id}_{item_id}_pitch.npz"
                    buf = io.BytesIO()
                    np.savez_compressed(buf, xs=np.asarray(pd["xs"], dtype=float), freqs=np.asarray(pd["freqs"], dtype=float))
                    npz_blobs[rel_p] = buf.getvalue()
                    item["pitch_data_file"] = rel_p

                fd = item.pop("formant_data", None)
                if isinstance(fd, dict) and fd.get("f1") is not None and fd.get("f2") is not None:
                    rel_f = f"data/{spk_id}_{item_id}_formant.npz"
                    buf = io.BytesIO()
                    save_kw = {
                        "xs": np.asarray(fd.get("xs", []), dtype=float),
                        "f1": np.asarray(fd["f1"], dtype=float),
                        "f2": np.asarray(fd["f2"], dtype=float),
                    }
                    if fd.get("f3") is not None:
                        save_kw["f3"] = np.asarray(fd["f3"], dtype=float)
                    np.savez_compressed(buf, **save_kw)
                    npz_blobs[rel_f] = buf.getvalue()
                    item["formant_data_file"] = rel_f

        os.makedirs(os.path.dirname(os.path.abspath(teproj_path)) or ".", exist_ok=True)
        with zipfile.ZipFile(teproj_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("project.json", json.dumps(state_copy, ensure_ascii=False, indent=2))
            for rel_name, blob in npz_blobs.items():
                zf.writestr(rel_name, blob)
        return True


    def _scan_item_warnings_detailed(self, item_id, item, spk_median_f0=None):
        """全面扫描单条目的异常类型（octave_jump, low_f0_coverage, no_clear_valley, tiny_segment），与 report_generator 保持 100% 同口径。"""
        from modules.anomaly_detection import detect_pitch_anomaly_points
        from modules.report_generator import check_item_has_empty_f0
        issues = []
        t_s, t_e = item.get("start"), item.get("end")
        dur = (float(t_e) - float(t_s)) if (t_s is not None and t_e is not None and float(t_e) > float(t_s)) else 0.0

        split_warns = list(item.get("split_warnings") or [])
        if "tiny_segment" in split_warns or (0 < dur < 0.08):
            issues.append("tiny_segment")
        if "no_clear_valley" in split_warns or "fallback_equal_split" in split_warns or "imbalanced_duration" in split_warns:
            issues.append("no_clear_valley")

        # Check F0 coverage and octave jumps
        p_data = item.get("pitch_data")
        has_empty = self._check_item_has_empty_data(item)
        pts = int(item.get("pts") or self.params.get("pts", 11))

        active_ratio = 1.0
        anomaly_pts = []
        report_empty_f0 = False
        if isinstance(p_data, dict) and p_data.get("xs") is not None and p_data.get("freqs") is not None:
            p_xs = np.asarray(p_data["xs"], dtype=float)
            p_freqs = np.asarray(p_data["freqs"], dtype=float)
            if t_s is not None and t_e is not None and float(t_e) > float(t_s):
                mask = (p_xs >= float(t_s)) & (p_xs <= float(t_e))
                xs_slice = p_xs[mask]
                freqs_slice = p_freqs[mask]
            else:
                xs_slice = p_xs
                freqs_slice = p_freqs

            try:
                report_empty_f0 = bool(check_item_has_empty_f0(item, {"xs": p_xs, "freqs": p_freqs}, pts))
            except Exception:
                report_empty_f0 = False

            if freqs_slice.size > 0:
                valid_mask = np.isfinite(freqs_slice) & (freqs_slice > 0)
                active_ratio = float(np.sum(valid_mask)) / float(freqs_slice.size)
                syls = split_into_syllables(item.get("label", ""))
                chars_bounds = item.get("chars_bounds") or []
                if chars_bounds and len(chars_bounds) == len(syls):
                    bounds = chars_bounds
                elif t_s is not None and t_e is not None:
                    bounds = [[float(t_s), float(t_e)]]
                else:
                    bounds = None
                anomaly_pts = detect_pitch_anomaly_points(xs_slice, freqs_slice, bounds=bounds, start=t_s, end=t_e)

                if not anomaly_pts and spk_median_f0 and spk_median_f0 > 0 and np.any(valid_mask):
                    vf = freqs_slice[valid_mask]
                    vx = xs_slice[valid_mask]
                    if len(vf) < 4:
                        extreme_mask = (vf >= spk_median_f0 * 1.75) | (vf <= spk_median_f0 / 1.75)
                        if np.any(extreme_mask):
                            anomaly_pts = [(float(t), float(f)) for t, f in zip(vx[extreme_mask], vf[extreme_mask])]

        if "low_f0_coverage" in split_warns or has_empty or report_empty_f0 or active_ratio < 0.35:
            issues.append("low_f0_coverage")
        if len(anomaly_pts) > 0:
            issues.append("octave_jump")

        return {
            "item_id": item_id,
            "label": item.get("label", ""),
            "group": item.get("group", "默认组"),
            "duration_s": round(dur, 4),
            "active_f0_ratio": round(active_ratio, 3),
            "issues": list(dict.fromkeys(issues)),
            "anomaly_points_count": len(anomaly_pts),
            "anomaly_points": [(round(t, 3), round(f, 1)) for t, f in anomaly_pts[:5]],
            "split_warnings": split_warns,
            "has_empty_data": bool(has_empty or report_empty_f0),
        }


    def _check_item_has_any_warning(self, item_id, item, spk_median_f0=None):
        """统一判断词条是否存在任何声学或切分警告（与 fix_warnings、stats、report_export 口径完全一致）。"""
        if self._check_item_has_empty_data(item):
            return True
        if isinstance(item, dict) and (item.get("pitch_data") is not None or item.get("split_warnings")):
            diag = self._scan_item_warnings_detailed(item_id, item, spk_median_f0=spk_median_f0)
            return bool(diag.get("issues"))
        return False


    @property
    def executor(self):
        """惰性初始化并发进程池，避免启动或单命令时无条件拉起 8 个进程造成冷启动卡顿"""
        if self._executor is None:
            self._executor = concurrent.futures.ProcessPoolExecutor(max_workers=min(os.cpu_count() or 4, 8))
        return self._executor


    @executor.setter
    def executor(self, val):
        self._executor = val


    def _has_meaningful_session_data(self) -> bool:
        if getattr(self, "_session_dirty", False):
            return True
        try:
            speakers = self.speaker_manager.get_all_speakers()
        except Exception:
            return False
        if len(speakers) > 1:
            return True
        for spk in speakers:
            if len(getattr(spk, "items", {})) > 0:
                return True
            if getattr(spk, "pending_long_snd", None) is not None or getattr(spk, "long_audio_path", None):
                return True
            if getattr(spk, "pending_batch_paths", None):
                return True
            if getattr(spk, "name", "发音人 1") != "发音人 1":
                return True
        return False


    def _persist_bound_session(self):
        """在命令或会话结束前自动回写 --workspace 与 --project 状态（若已由 _after_state_change 落盘且未再产生变更则跳过重复打包）"""
        if getattr(self, "_explicit_workspace_dir", None):
            ws_proj_json = os.path.join(self._explicit_workspace_dir, "project.json")
            if getattr(self, "_session_dirty", False) or (not os.path.exists(ws_proj_json) and self._has_meaningful_session_data()):
                try:
                    self.project_manager.save_to_workspace()
                except Exception:
                    pass

        if getattr(self, "_bound_project_path", None):
            if getattr(self, "_session_dirty", False) or (not os.path.exists(self._bound_project_path) and self._has_meaningful_session_data()):
                try:
                    orig_active_id = self.speaker_manager.active_speaker_id
                    try:
                        for spk in self.speaker_manager.get_all_speakers():
                            self.speaker_manager.set_active_speaker(spk.id)
                            for item in spk.items.values():
                                self._ensure_item_loaded(item)
                    finally:
                        if orig_active_id:
                            self.speaker_manager.set_active_speaker(orig_active_id)
                    export_fn = self.project_manager.export_project
                    if export_fn(self._bound_project_path):
                        self._session_dirty = False
                except Exception:
                    pass
        else:
            self._session_dirty = False


    def cleanup_workspace(self):
        """保存绑定会话并清理专属临时工作区（绝不删除用户显式指定的 --workspace 目录）"""
        try:
            self._persist_bound_session()
        except Exception:
            pass
        if getattr(self, "_executor", None) is not None:
            try:
                self._executor.shutdown(wait=False)
            except Exception:
                pass
            self._executor = None
        if getattr(self, "_explicit_workspace_dir", None):
            try:
                if hasattr(self, "project_manager"):
                    self.project_manager.release_workspace_lock()
            except Exception:
                pass
            return
        if getattr(self, "_temp_workspace_dir", None) and os.path.exists(self._temp_workspace_dir):
            try:
                if hasattr(self, "project_manager"):
                    self.project_manager.release_workspace_lock()
                shutil.rmtree(self._temp_workspace_dir, ignore_errors=True)
            except Exception:
                pass
            self._temp_workspace_dir = None


    @staticmethod
    def _clean_bom_and_whitespace(line: str) -> str:
        if not isinstance(line, str):
            return ""
        cleaned = line.lstrip('\ufeff\ufffe\u200b').strip()
        if cleaned:
            cleaned = cleaned.lstrip('\ufeff\ufffe\u200b').strip()
        return cleaned


    @staticmethod
    def _safe_normalize_cli_line(line: str) -> str:
        if not isinstance(line, str):
            return ""
        line = PhonTracerCLI._clean_bom_and_whitespace(line)
        if "\\" not in line:
            return line
        marker_esc_quote = "__ESCAPED_QUOTE_TOKEN__"
        marker_unc = "__UNC_PREFIX_TOKEN__"
        s = line.replace(r'\"', marker_esc_quote)
        s = re.sub(r'(^|[\s"\'])\\\\(?!\\)', r'\1' + marker_unc, s)
        s = s.replace('\\', '/')
        s = s.replace(marker_unc, r'\\')
        s = s.replace(marker_esc_quote, r'\"')
        return s


    def parseline(self, line):
        if isinstance(line, str):
            line = self._clean_bom_and_whitespace(line)
        cmd_name, arg, line = super().parseline(line)
        if isinstance(cmd_name, str):
            cmd_name = cmd_name.lstrip('\ufeff\ufffe\u200b').strip()
        return cmd_name, arg, line


    def onecmd(self, line):
        if isinstance(line, str):
            line = self._safe_normalize_cli_line(line)
            if not line or line.startswith("#"):
                return False
            # 拦截 `<cmd> --help` / `<cmd> -h` / `<cmd> ?` 细粒度子命令自省帮助
            try:
                tokens = shlex.split(line)
            except ValueError:
                tokens = line.split()
            if tokens:
                first_tok = tokens[0].lstrip('\ufeff\ufffe\u200b').strip()
                rest_toks = [t.strip() for t in tokens[1:]]
                if first_tok.lower() not in ("help", "?"):
                    if len(rest_toks) == 1 and rest_toks[0].lower() in ("--help", "-h", "?"):
                        if self._emit_subcommand_help(first_tok):
                            return False
        return super().onecmd(line)


    def _emit(self, success=True, message="", **payload):
        self._last_emit_success = bool(success)
        data = {"success": bool(success)}
        if message:
            data["message"] = message
        data.update(payload)
        self._last_emit_payload = data
        print(json.dumps(data, ensure_ascii=False, allow_nan=False))


    def _after_state_change(self, action="updated"):
        self._session_dirty = True
        # 在 --script-file 或管道批量执行期间延迟归档落盘，待批量队列结束时在 cleanup_workspace 中一次性保存
        if getattr(self, "_defer_persistence", False):
            return
        if getattr(self.project_manager, "auto_save_enabled", False):
            self.project_manager.trigger_auto_save()
        if getattr(self, "_explicit_workspace_dir", None):
            try:
                self.project_manager.save_to_workspace(incremental=True)
                if not getattr(self, "_bound_project_path", None):
                    self._session_dirty = False
            except Exception:
                pass
        if getattr(self, "_bound_project_path", None):
            try:
                orig_active_id = self.speaker_manager.active_speaker_id
                try:
                    for spk in self.speaker_manager.get_all_speakers():
                        self.speaker_manager.set_active_speaker(spk.id)
                        for item in spk.items.values():
                            self._ensure_item_loaded(item)
                finally:
                    if orig_active_id:
                        self.speaker_manager.set_active_speaker(orig_active_id)
                export_fn = self.project_manager.export_project
                if export_fn(self._bound_project_path):
                    self._session_dirty = False
            except Exception:
                pass


    def after(self, _delay, callback):
        callback()


    @property
    def current_speaker(self):
        return self.speaker_manager.get_active_speaker()


    @property
    def items(self):
        return self.current_speaker.items


    @property
    def params(self):
        if 'trim_silence' not in self.current_speaker.last_params:
            self.current_speaker.last_params['trim_silence'] = True
        if 'show_f3' not in self.current_speaker.last_params:
            self.current_speaker.last_params['show_f3'] = False
        return self.current_speaker.last_params


    @property
    def mode(self):
        tab = getattr(self.current_speaker, 'tab_mode', '多条独立音频')
        return 'long' if '单条' in tab else 'batch'


    @mode.setter
    def mode(self, value):
        if value in ('long', 'single', '单条长音频'):
            self.current_speaker.tab_mode = '单条长音频'
        else:
            self.current_speaker.tab_mode = '多条独立音频'


    @property
    def groups(self):
        if not hasattr(self.current_speaker, 'cli_groups'):
            seen = []
            for item in self.current_speaker.items.values():
                group = item.get('group', '导入内容')
                if group not in seen:
                    seen.append(group)
            self.current_speaker.cli_groups = seen
        return self.current_speaker.cli_groups


    @groups.setter
    def groups(self, val):
        self.current_speaker.cli_groups = val


    @property
    def long_snd(self):
        return self.current_speaker.pending_long_snd


    @long_snd.setter
    def long_snd(self, val):
        self.current_speaker.pending_long_snd = val


    @property
    def long_snd_path(self):
        if hasattr(self.current_speaker, 'cli_long_snd_path') and self.current_speaker.cli_long_snd_path:
            return self.current_speaker.cli_long_snd_path
        return getattr(self.current_speaker, 'long_audio_path', None)


    @long_snd_path.setter
    def long_snd_path(self, val):
        self.current_speaker.cli_long_snd_path = val
        self.current_speaker.long_audio_path = val


    @property
    def batch_paths(self):
        return self.current_speaker.pending_batch_paths


    @batch_paths.setter
    def batch_paths(self, val):
        self.current_speaker.pending_batch_paths = val


    @property
    def audio_cache(self):
        return self.current_speaker.audio_cache


    def precmd(self, line):
        if isinstance(line, str):
            line = self._clean_bom_and_whitespace(line)
        if self.log_file:
            self.log_file.write(f"> {line}\n")
            self.log_file.flush()
        return line


    def _is_external_command(self, line_text: str) -> bool:
        test_line = line_text.strip()
        if test_line.startswith('&'):
            test_line = test_line[1:].strip()

        banned_commands = {
            "python", "py", "powershell", "pwsh", "cmd", "bash", "sh",
            "node", "rscript", "praat", "parselmouth", "start", "call"
        }
        banned_suffixes = ('.bat', '.cmd', '.ps1', '.py', '.r', '.js', '.vbs')

        token_sets = []
        try:
            token_sets.append(shlex.split(test_line))
        except Exception:
            pass
        token_sets.append(test_line.split())

        for tokens in token_sets:
            if not tokens:
                continue
            first = tokens[0].strip().lower().strip('\'"')
            if first.startswith('.\\') or first.startswith('./'):
                first = first[2:]
            cmd_name = os.path.basename(first.replace('\\', '/'))
            if cmd_name.endswith('.exe'):
                cmd_name = cmd_name[:-4]
            if cmd_name in banned_commands:
                return True
            for t in tokens:
                t_clean = t.strip().lower().strip('\'"')
                if t_clean.endswith(banned_suffixes):
                    return True
        return False


    def default(self, line):
        line_stripped = self._clean_bom_and_whitespace(line)
        if not line_stripped or line_stripped.startswith("#"):
            return

        if self._is_external_command(line_stripped):
            self._emit(
                False,
                "Detected an external shell/script style command (External automation attempt). PhonTracerCLI does not execute external commands directly; please use built-in CLI commands.",
                error=f"Unknown CLI command: {line_stripped}",
                next_steps=["help", "agent_guide", "status"],
            )
        else:
            self._emit(
                False,
                "Use help or agent_guide to see supported PhonTracer CLI commands.",
                error=f"Unknown CLI command: {line_stripped}",
                hint="输入 help 或 agent_guide 查看内置命令列表，或使用 <cmd> --help 查看具体指令语法。",
            )


    def _check_item_has_empty_data(self, item):
        """Returns True if the item contains a 0Hz pitch in the 11-point preview."""
        if 'has_empty_data' in item:
            return item['has_empty_data']
        if item.get('preview_f0'):
            return any(hz == 0.0 for hz in item['preview_f0'])
        return False


    def do_set_params(self, arg):
        """
        Set analysis parameters.
        Usage: set_params key=value [key=value ...]
        Valid keys: pts, db, skip_front, pitch_floor, pitch_ceiling, voicing_threshold, trim_silence, analysis_mode, formant_max_hz, formant_count, formant_window_length, formant_pre_emphasis, formant_sample_strategy, show_f3
        Example: set_params db=50.0 trim_silence=False analysis_mode=formant formant_max_hz=5500
        """
        args = shlex.split(arg)
        if not args:
            self._emit(True, params=self.params)
            return

        param_aliases = {
            "f0_min": "pitch_floor",
            "f0_max": "pitch_ceiling",
            "voicing_thresh": "voicing_threshold",
            "max_formant": "formant_max_hz",
            "num_formants": "formant_count",
        }
        allowed_extra = {"despike", "silence_threshold"}
        updated = False
        for kv in args:
            if '=' in kv:
                k_raw, v = kv.split('=', 1)
                k = param_aliases.get(k_raw.strip(), k_raw.strip())
                if k in self.params or k in allowed_extra:
                    try:
                        if k in ('trim_silence', 'show_f3', 'despike'):
                            self.params[k] = v.lower() in ('true', '1', 'yes')
                        elif k in ('pts', 'pitch_floor', 'pitch_ceiling', 'formant_count'):
                            self.params[k] = int(float(v))
                        elif k == 'analysis_mode':
                            val_str = str(v).lower().strip()
                            if val_str not in ('f0', 'formant'):
                                self._emit(False, error="Invalid value for analysis_mode: must be 'f0' or 'formant'")
                                return
                            self.params[k] = val_str
                        elif k == 'formant_sample_strategy':
                            self.params[k] = str(v)
                        elif k in ('formant_max_hz', 'formant_window_length', 'formant_pre_emphasis', 'silence_threshold'):
                            self.params[k] = float(v)
                        else:
                            self.params[k] = float(v)
                        updated = True
                    except ValueError:
                        self._emit(False, error=f"Invalid value for {k_raw}")
                        return
                else:
                    self._emit(False, error=f"Unknown parameter {k_raw}")
                    return

        if updated:
            self._after_state_change("set_params")
        self._emit(True, "Parameters updated", params=self.params)


# Compatibility alias for internal static method references
PhonTracerCLI = CLISessionCoreMixin

__all__ = ["CLISessionCoreMixin"]
