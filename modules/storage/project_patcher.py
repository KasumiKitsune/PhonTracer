# -*- coding: utf-8 -*-
"""
PhonTracer语音学标注与声学分析系统 V1.0 工程数据处理脚本写回器。

该模块只执行脚本返回的受控操作清单，不执行脚本源码，也不允许脚本直接修改 .teproj。
"""

import copy
import json
import math
import os
import re
import shutil
import tempfile
import uuid
import zipfile

import numpy as np
try:
    import parselmouth
except ImportError:
    from modules import _parselmouth_fallback as parselmouth
    import sys as _sys
    _sys.modules.setdefault("parselmouth", parselmouth)
    _sys.modules.setdefault("parselmouth.praat", parselmouth.praat)

from ..dsp.sound_loader import load_compatible_sound
from ..dsp.pitch_formant_unified import extract_f0, extract_formants
from .archive_security import validate_project_archive_members
from .schema_migrator import read_project_metadata_from_archive
from .project_repository import ProjectManager, to_json_serializable


SUPPORTED_PATCH_OPS = {
    "set_item_fields",
    "recompute_pitch",
    "recompute_formant",
    "trim_item_audio",
    "split_project",
    "import_csv_metadata",
}

ALLOWED_ITEM_FIELDS = {
    "label",
    "group",
    "group_note",
    "group_tags",
    "item_note",
    "item_tags",
    "item_aliases",
    "item_meta",
    "note",
    "tags",
    "aliases",
    "meta",
    "metadata_source",
    "is_excluded",
    "exclusion_reason",
    "start",
    "end",
    "inner_splits",
    "chars_bounds",
}

ANALYSIS_CACHE_FIELDS = {
    "pitch_data_file",
    "formant_data_file",
    "preview_f0",
    "preview_formants",
    "has_empty_data",
    "split_warnings",
    "split_confidence",
}


class ProjectPatchError(ValueError):
    """工程补丁操作无法安全应用时抛出的错误。"""


def _same_file_path(left, right):
    if not left or not right:
        return False
    left_path = os.path.normcase(os.path.abspath(left))
    right_path = os.path.normcase(os.path.abspath(right))
    return left_path == right_path


def _safe_token(value, fallback="item"):
    text = str(value or fallback)
    text = re.sub(r"[^\w\u4e00-\u9fff.-]+", "_", text, flags=re.UNICODE)
    text = text.strip("._ ")
    return text[:60] or fallback


def _normalize_rel_path(path):
    if not path:
        return None
    norm = str(path).replace("\\", "/")
    parts = norm.split("/")
    if len(parts) < 2 or parts[0] not in ("audio", "data"):
        raise ProjectPatchError(f"工程资源路径不受支持：{path}")
    if any(part in ("", ".", "..") for part in parts):
        raise ProjectPatchError(f"工程资源路径非法：{path}")
    return norm


def _resolve_resource(workspace_dir, rel_path):
    norm = _normalize_rel_path(rel_path)
    return os.path.join(workspace_dir, *norm.split("/"))


def _coerce_patch_result(patch_result):
    operations = getattr(patch_result, "operations", None)
    title = getattr(patch_result, "title", "数据处理脚本结果")
    description = getattr(patch_result, "description", "")
    if operations is None and isinstance(patch_result, dict):
        operations = patch_result.get("operations")
        title = patch_result.get("title", title)
        description = patch_result.get("description", description)
    if operations is None:
        raise ProjectPatchError("数据处理脚本没有返回 operations。")
    if not isinstance(operations, list):
        raise ProjectPatchError("数据处理脚本返回的 operations 必须是列表。")
    for idx, op in enumerate(operations, start=1):
        if not isinstance(op, dict):
            raise ProjectPatchError(f"第 {idx} 个操作不是字典。")
        if op.get("op") not in SUPPORTED_PATCH_OPS:
            raise ProjectPatchError(f"第 {idx} 个操作类型不受支持：{op.get('op')}")
    return {"title": title, "description": description, "operations": operations}


def summarize_project_patch(patch_result):
    patch = _coerce_patch_result(patch_result)
    counts = {}
    affected = set()
    for op in patch["operations"]:
        op_type = op.get("op")
        counts[op_type] = counts.get(op_type, 0) + 1
        target = op.get("target") or {}
        if target.get("speaker_id") and target.get("item_id"):
            affected.add((target.get("speaker_id"), target.get("item_id")))
    return {
        "title": patch["title"],
        "description": patch["description"],
        "operation_count": len(patch["operations"]),
        "affected_item_count": len(affected),
        "operation_counts": counts,
    }


def _extract_project(teproj_path, workspace_dir):
    with zipfile.ZipFile(teproj_path, "r") as zf:
        infos = validate_project_archive_members(zf)
        for member in infos:
            normalized = member.filename.replace("\\", "/")
            target = os.path.abspath(os.path.join(workspace_dir, *normalized.split("/")))
            if os.path.commonpath([target, os.path.abspath(workspace_dir)]) != os.path.abspath(workspace_dir):
                raise ProjectPatchError("工程文件包含非法路径。")
            if member.is_dir():
                os.makedirs(target, exist_ok=True)
                continue
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with zf.open(member) as source, open(target, "wb") as dest:
                shutil.copyfileobj(source, dest)


def _project_manager_for_validation(workspace_dir=None):
    return ProjectManager(app=None, workspace_dir=workspace_dir)


def _validate_project_workspace_for_import(workspace_dir):
    state = _load_project_state(workspace_dir)
    _project_manager_for_validation(workspace_dir)._validate_project_resources(state, workspace_dir)
    return state


def _validate_project_archive_for_import(teproj_path):
    read_project_metadata_from_archive(teproj_path)
    temp_dir = tempfile.mkdtemp(prefix="phontracer_validate_")
    try:
        _extract_project(teproj_path, temp_dir)
        _validate_project_workspace_for_import(temp_dir)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def _write_project_archive(workspace_dir, output_path):
    project_json = os.path.join(workspace_dir, "project.json")
    if not os.path.isfile(project_json):
        raise ProjectPatchError("临时工程目录缺少 project.json。")

    abs_output = os.path.abspath(output_path)
    parent = os.path.dirname(abs_output)
    if parent:
        os.makedirs(parent, exist_ok=True)
    temp_zip = f"{abs_output}.{uuid.uuid4().hex}.tmp"
    try:
        with zipfile.ZipFile(temp_zip, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, _dirs, files in os.walk(workspace_dir):
                for filename in files:
                    file_path = os.path.join(root, filename)
                    rel_path = os.path.relpath(file_path, workspace_dir).replace(os.sep, "/")
                    zf.write(file_path, rel_path)
        # 完整性自检，避免全量重新解包
        with zipfile.ZipFile(temp_zip, "r") as zf:
            bad_member = zf.testzip()
            if bad_member is not None:
                raise ProjectPatchError(f"生成的工程压缩包校验损坏：{bad_member}")
        # 快速核验元数据，无需解包整个工程
        read_project_metadata_from_archive(temp_zip)
        os.replace(temp_zip, abs_output)
    finally:
        if os.path.exists(temp_zip):
            try:
                os.remove(temp_zip)
            except OSError:
                pass


def _load_project_state(workspace_dir):
    project_json = os.path.join(workspace_dir, "project.json")
    with open(project_json, "r", encoding="utf-8") as f:
        state = json.load(f)
    if not isinstance(state, dict):
        raise ProjectPatchError("project.json 顶层不是对象。")
    return state


def _sanitize_for_json(val):
    if isinstance(val, dict):
        return {str(k): _sanitize_for_json(v) for k, v in val.items()}
    elif isinstance(val, (list, tuple, set)):
        return [_sanitize_for_json(v) for v in val]
    elif isinstance(val, (np.floating, float)):
        if math.isnan(val) or math.isinf(val):
            return None
        return float(val)
    elif isinstance(val, (np.integer, int)):
        if isinstance(val, bool):
            return bool(val)
        return int(val)
    elif isinstance(val, (np.bool_, bool)):
        return bool(val)
    elif isinstance(val, np.ndarray):
        return _sanitize_for_json(val.tolist())
    elif isinstance(val, str) or val is None:
        return val
    else:
        try:
            val = to_json_serializable(val)
            if isinstance(val, (float, np.floating)) and (math.isnan(val) or math.isinf(val)):
                return None
            return val
        except Exception:
            return str(val)


def _save_project_state(workspace_dir, state):
    if isinstance(state.get("groups"), list) and isinstance(state.get("speakers"), dict):
        try:
            _project_manager_for_validation(workspace_dir)._sync_speakers_to_groups(state, state["speakers"])
        except Exception:
            pass
    project_json = os.path.join(workspace_dir, "project.json")
    tmp_path = project_json + ".tmp"
    sanitized = _sanitize_for_json(to_json_serializable(state))
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(sanitized, f, ensure_ascii=False, indent=2, allow_nan=False)
    os.replace(tmp_path, project_json)


def _iter_state_resource_paths(state):
    for spk in state.get("speakers", {}).values():
        if not isinstance(spk, dict):
            continue
        for path in (spk.get("long_audio_path"), spk.get("long_pitch_data_file")):
            if path:
                yield _normalize_rel_path(path)
        for path in spk.get("pending_batch_paths", []) or []:
            if path:
                yield _normalize_rel_path(path)
        for item in (spk.get("items", {}) or {}).values():
            if not isinstance(item, dict):
                continue
            for key in ("path", "pitch_data_file", "formant_data_file"):
                path = item.get(key)
                if path:
                    yield _normalize_rel_path(path)


def _prune_workspace_resources_for_state(workspace_dir, state):
    keep_paths = set(_iter_state_resource_paths(state))
    for root_name in ("audio", "data"):
        root_dir = os.path.join(workspace_dir, root_name)
        if not os.path.isdir(root_dir):
            continue
        for root, _dirs, files in os.walk(root_dir):
            for filename in files:
                file_path = os.path.join(root, filename)
                rel_path = os.path.relpath(file_path, workspace_dir).replace(os.sep, "/")
                if rel_path not in keep_paths:
                    try:
                        os.remove(file_path)
                    except OSError:
                        pass
        for root, dirs, _files in os.walk(root_dir, topdown=False):
            for dirname in dirs:
                dir_path = os.path.join(root, dirname)
                try:
                    os.rmdir(dir_path)
                except OSError:
                    pass


def _find_item(state, target):
    if not isinstance(target, dict):
        raise ProjectPatchError("操作缺少 target。")
    speaker_id = target.get("speaker_id")
    item_id = target.get("item_id")
    speakers = state.get("speakers", {})
    if speaker_id in speakers:
        spk = speakers[speaker_id]
        items = spk.get("items", {})
        if item_id in items:
            return speaker_id, spk, item_id, items[item_id]

    for spk_id, spk in speakers.items():
        items = spk.get("items", {})
        if item_id in items:
            return spk_id, spk, item_id, items[item_id]

    label = target.get("label")
    raise ProjectPatchError(f"找不到目标条目：speaker_id={speaker_id}, item_id={item_id}, label={label}")


def _clear_analysis_cache_refs(item, include_pitch=True, include_formant=True):
    if include_pitch:
        item.pop("pitch_data_file", None)
        item.pop("preview_f0", None)
    if include_formant:
        item.pop("formant_data_file", None)
        item.pop("preview_formants", None)
    for key in ("has_empty_data", "split_warnings", "split_confidence"):
        item.pop(key, None)


def _resource_name(spk_id, item_id, suffix):
    return f"data/{_safe_token(spk_id)}_{_safe_token(item_id)}_{uuid.uuid4().hex[:8]}{suffix}"


def _audio_name(spk_id, item_id):
    return f"audio/{_safe_token(spk_id)}_{_safe_token(item_id)}_trim_{uuid.uuid4().hex[:8]}.wav"


def _item_params(spk, item, override):
    params = {}
    if isinstance(spk.get("last_params"), dict):
        params.update(spk.get("last_params"))
    # 兼容条目顶层平铺的声学参数
    param_keys = {
        "pitch_floor", "pitch_ceiling", "voicing_threshold", "pts", "method",
        "time_step", "max_candidates", "silence_threshold", "f0_min", "f0_max",
        "voicing_thresh", "analysis_mode", "formant_max_hz", "formant_count",
        "formant_window_length", "formant_pre_emphasis", "formant_sample_strategy",
    }
    for k in param_keys:
        if k in item and item[k] is not None:
            params[k] = item[k]
    if isinstance(item.get("recomputed_pitch_params"), dict):
        params.update(item["recomputed_pitch_params"])
    if isinstance(item.get("recomputed_formant_params"), dict):
        params.update(item["recomputed_formant_params"])
    if isinstance(item.get("param_overrides"), dict):
        params.update(item.get("param_overrides"))
    if isinstance(override, dict):
        params.update(override)
    return params


def _load_item_sound(workspace_dir, spk, item, long_audio_cache=None):
    item_path = item.get("path")
    if item_path:
        res_path = _resolve_resource(workspace_dir, item_path)
        if long_audio_cache is not None and res_path in long_audio_cache:
            return long_audio_cache[res_path], "item"
        snd = load_compatible_sound(res_path)
        if long_audio_cache is not None:
            long_audio_cache[res_path] = snd
        return snd, "item"

    long_audio_path = spk.get("long_audio_path")
    if long_audio_path:
        res_path = _resolve_resource(workspace_dir, long_audio_path)
        if long_audio_cache is not None and res_path in long_audio_cache:
            return long_audio_cache[res_path], "long"
        snd = load_compatible_sound(res_path)
        if long_audio_cache is not None:
            long_audio_cache[res_path] = snd
        return snd, "long"

    raise ProjectPatchError(f"条目 {item.get('label', '')} 缺少可用音频资源。")


def _segment_sound_for_item(snd, source_kind, item, start=None, end=None, padding=0.0, with_offset=False):
    total = float(snd.get_total_duration())
    if source_kind == "long":
        if start is not None:
            base_start = float(start)
        elif item.get("macro_start") is not None:
            base_start = float(item["macro_start"])
        else:
            base_start = float(item.get("start", 0.0))

        if end is not None:
            base_end = float(end)
        elif item.get("macro_end") is not None:
            base_end = float(item["macro_end"])
        else:
            base_end = float(item.get("end", total))
    else:
        base_start = float(0.0 if start is None else start)
        base_end = float(total if end is None else end)

    from_time = max(0.0, base_start - float(padding or 0.0))
    to_time = min(total, base_end + float(padding or 0.0))
    if to_time <= from_time:
        raise ProjectPatchError("裁剪或重算音频的时间范围无效。")
    if from_time <= 0.0 and to_time >= total:
        part = snd
    else:
        part = snd.extract_part(from_time=from_time, to_time=to_time)
    if with_offset:
        return part, from_time
    return part


def _restore_global_xs(data, source_kind, offset, segment_duration):
    if source_kind != "long" or not data or "xs" not in data:
        return data
    xs = np.asarray(data["xs"], dtype=np.float64)
    if xs.size and offset > 0 and float(np.nanmax(xs)) <= float(segment_duration) + 0.25:
        data = dict(data)
        data["xs"] = xs + float(offset)
    return data


def _apply_set_item_fields(state, _workspace_dir, op, records, **kwargs):
    _spk_id, _spk, _item_id, item = _find_item(state, op.get("target"))
    fields = op.get("fields")
    if not isinstance(fields, dict):
        raise ProjectPatchError("set_item_fields 操作缺少 fields 字典。")
    bad_fields = sorted(set(fields) - ALLOWED_ITEM_FIELDS)
    if bad_fields:
        raise ProjectPatchError(f"set_item_fields 包含不允许修改的字段：{', '.join(bad_fields)}")

    boundary_changed = bool(set(fields) & {"start", "end", "inner_splits", "chars_bounds"})
    for key, value in fields.items():
        item[key] = copy.deepcopy(value)
        if key in ("item_note", "item_tags", "item_aliases", "item_meta"):
            item[key[5:]] = copy.deepcopy(value)
        elif key in ("note", "tags", "aliases", "meta"):
            item["item_" + key] = copy.deepcopy(value)
    if boundary_changed:
        _clear_analysis_cache_refs(item)
    records.append({"op": "set_item_fields", "label": item.get("label", ""), "field_count": len(fields)})


def _apply_recompute_pitch(state, workspace_dir, op, records, long_audio_cache=None, **kwargs):
    spk_id, spk, item_id, item = _find_item(state, op.get("target"))
    snd, source_kind = _load_item_sound(workspace_dir, spk, item, long_audio_cache=long_audio_cache)
    part, offset = _segment_sound_for_item(snd, source_kind, item, with_offset=True)
    params = _item_params(spk, item, op.get("params"))
    pitch_data = extract_f0(part, params)
    pitch_data = _restore_global_xs(pitch_data, source_kind, offset, part.get_total_duration())
    rel_name = _resource_name(spk_id, item_id, "_pitch.npz")
    out_path = _resolve_resource(workspace_dir, rel_name)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    np.savez(out_path, xs=pitch_data["xs"], freqs=pitch_data["freqs"])
    item["pitch_data_file"] = rel_name
    item["analysis_mode"] = item.get("analysis_mode") or params.get("analysis_mode", "f0")
    item["recomputed_pitch_params"] = copy.deepcopy(params)
    spk_params = spk.get("last_params", {}) if isinstance(spk, dict) else {}
    diff_params = {k: v for k, v in params.items() if spk_params.get(k) != v}
    item.setdefault("param_overrides", {})
    if isinstance(item["param_overrides"], dict):
        item["param_overrides"].update(diff_params)
    else:
        item["param_overrides"] = diff_params
    records.append({"op": "recompute_pitch", "label": item.get("label", ""), "resource": rel_name})


def _apply_recompute_formant(state, workspace_dir, op, records, long_audio_cache=None, **kwargs):
    spk_id, spk, item_id, item = _find_item(state, op.get("target"))
    snd, source_kind = _load_item_sound(workspace_dir, spk, item, long_audio_cache=long_audio_cache)
    part, offset = _segment_sound_for_item(snd, source_kind, item, with_offset=True)
    params = _item_params(spk, item, op.get("params"))
    formant_data = extract_formants(part, params)
    formant_data = _restore_global_xs(formant_data, source_kind, offset, part.get_total_duration())
    rel_name = _resource_name(spk_id, item_id, "_formant.npz")
    out_path = _resolve_resource(workspace_dir, rel_name)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    save_kwargs = {"xs": formant_data["xs"], "f1": formant_data["f1"], "f2": formant_data["f2"]}
    if "f3" in formant_data:
        save_kwargs["f3"] = formant_data["f3"]
    np.savez(out_path, **save_kwargs)
    item["formant_data_file"] = rel_name
    item["analysis_mode"] = "formant"
    item["recomputed_formant_params"] = copy.deepcopy(params)
    spk_params = spk.get("last_params", {}) if isinstance(spk, dict) else {}
    diff_params = {k: v for k, v in params.items() if spk_params.get(k) != v}
    item.setdefault("param_overrides", {})
    if isinstance(item["param_overrides"], dict):
        item["param_overrides"].update(diff_params)
    else:
        item["param_overrides"] = diff_params
    records.append({"op": "recompute_formant", "label": item.get("label", ""), "resource": rel_name})


def _apply_trim_item_audio(state, workspace_dir, op, records, long_audio_cache=None, **kwargs):
    spk_id, spk, item_id, item = _find_item(state, op.get("target"))
    snd, source_kind = _load_item_sound(workspace_dir, spk, item, long_audio_cache=long_audio_cache)
    part, from_time = _segment_sound_for_item(
        snd,
        source_kind,
        item,
        start=op.get("start"),
        end=op.get("end"),
        padding=op.get("padding", 0.0),
        with_offset=True,
    )
    dur = float(part.get_total_duration())
    rel_name = _audio_name(spk_id, item_id)
    out_path = _resolve_resource(workspace_dir, rel_name)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    part.save(out_path, "WAV")
    if long_audio_cache is not None:
        long_audio_cache[out_path] = part

    old_item_path = item.get("path")
    item["path"] = rel_name
    if isinstance(spk.get("pending_batch_paths"), list):
        spk["pending_batch_paths"] = [
            rel_name if path == old_item_path else path
            for path in spk.get("pending_batch_paths", [])
        ]

    def _clamp(val, low=0.0, high=dur):
        return max(low, min(high, float(val)))

    orig_s = item.get("start")
    orig_e = item.get("end")
    if orig_s is not None:
        new_start = _clamp(float(orig_s) - from_time)
    else:
        new_start = 0.0
    if orig_e is not None:
        new_end = _clamp(float(orig_e) - from_time)
    else:
        new_end = dur
    if new_start >= new_end:
        new_start, new_end = 0.0, dur

    item["start"] = new_start
    item["end"] = new_end
    item["macro_start"] = 0.0
    item["macro_end"] = dur
    item["raw_start"] = new_start
    item["raw_end"] = new_end

    orig_bounds = item.get("chars_bounds")
    if isinstance(orig_bounds, (list, tuple)) and orig_bounds:
        shifted_bounds = []
        for pair in orig_bounds:
            if isinstance(pair, (list, tuple)) and len(pair) >= 2:
                bs = _clamp(float(pair[0]) - from_time)
                be = _clamp(float(pair[1]) - from_time)
                if be > bs:
                    shifted_bounds.append([bs, be])
        item["chars_bounds"] = shifted_bounds if shifted_bounds else [[0.0, dur]]
    else:
        item["chars_bounds"] = [[0.0, dur]]

    orig_splits = item.get("inner_splits")
    if isinstance(orig_splits, (list, tuple)) and orig_splits:
        shifted_splits = []
        for s in orig_splits:
            try:
                sv = _clamp(float(s) - from_time)
                if 0.0 <= sv <= dur:
                    shifted_splits.append(sv)
            except (TypeError, ValueError):
                pass
        item["inner_splits"] = sorted(shifted_splits)
    else:
        item["inner_splits"] = []

    _clear_analysis_cache_refs(item)
    records.append({"op": "trim_item_audio", "label": item.get("label", ""), "resource": rel_name})


def _apply_split_project(state, _workspace_dir, op, records, **kwargs):
    item_ids = {str(v) for v in op.get("item_ids", []) if str(v)}
    speaker_ids = {str(v) for v in op.get("speaker_ids", []) if str(v)}
    if not item_ids and not speaker_ids:
        raise ProjectPatchError("split_project 至少需要 item_ids 或 speaker_ids。")

    speakers = state.get("speakers", {})
    new_speakers = {}
    kept_items = 0
    for spk_id, spk in speakers.items():
        if speaker_ids and spk_id not in speaker_ids:
            continue
        new_spk = copy.deepcopy(spk)
        old_items = spk.get("items", {}) or {}
        if item_ids:
            new_spk["items"] = {iid: copy.deepcopy(item) for iid, item in old_items.items() if iid in item_ids}
        else:
            new_spk["items"] = copy.deepcopy(old_items)
        if new_spk["items"] or (speaker_ids and spk_id in speaker_ids):
            item_audio_paths = {
                _normalize_rel_path(item.get("path"))
                for item in new_spk["items"].values()
                if item.get("path")
            }
            new_spk["pending_batch_paths"] = [
                path
                for path in (new_spk.get("pending_batch_paths", []) or [])
                if path and _normalize_rel_path(path) in item_audio_paths
            ]
            uses_long_audio = any(not item.get("path") for item in new_spk["items"].values())
            if not uses_long_audio:
                new_spk["long_audio_path"] = None
                new_spk["current_macro_segments"] = []
                new_spk["manual_segments"] = []
            kept_items += len(new_spk["items"])
            new_speakers[spk_id] = new_spk

    if not new_speakers:
        raise ProjectPatchError("split_project 没有匹配到任何发音人或条目。")
    state["speakers"] = new_speakers
    if state.get("active_speaker_id") not in new_speakers:
        state["active_speaker_id"] = next(iter(new_speakers.keys()))
    records.append({"op": "split_project", "name": op.get("name") or "拆分工程", "kept_items": kept_items})


def _split_csv_list_value(value):
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    text = str(value or "").strip()
    if not text:
        return []
    return [v.strip() for v in re.split(r"[,，;；]+", text) if v.strip()]


def _apply_mapped_field(item, field_name, value):
    if field_name.startswith("item_meta.") or field_name.startswith("meta."):
        meta_key = field_name.split(".", 1)[1].strip()
        if not meta_key:
            return
        item.setdefault("item_meta", {})
        item.setdefault("meta", {})
        if isinstance(item["item_meta"], dict):
            item["item_meta"][meta_key] = value
        if isinstance(item.get("meta"), dict):
            item["meta"][meta_key] = value
        return
    if field_name in ("item_tags", "group_tags", "item_aliases"):
        val_list = _split_csv_list_value(value)
        item[field_name] = val_list
        if field_name.startswith("item_"):
            item[field_name[5:]] = copy.deepcopy(val_list)
        return
    if field_name in ("tags", "aliases"):
        val_list = _split_csv_list_value(value)
        item[field_name] = val_list
        item["item_" + field_name] = copy.deepcopy(val_list)
        return
    if field_name in ("item_note", "note"):
        s = "" if value is None else str(value).strip()
        item["item_note"] = s
        item["note"] = s
        return
    if field_name == "group_note":
        item["group_note"] = "" if value is None else str(value).strip()
        return
    if field_name in ALLOWED_ITEM_FIELDS:
        item[field_name] = value


def _apply_import_csv_metadata(state, _workspace_dir, op, records, **kwargs):
    rows = op.get("rows") or []
    if not isinstance(rows, list):
        raise ProjectPatchError("import_csv_metadata 的 rows 必须是列表。")
    match_on = str(op.get("match_on") or "label")
    field_map = op.get("field_map") or {}
    if not isinstance(field_map, dict):
        raise ProjectPatchError("import_csv_metadata 的 field_map 必须是字典。")
    if not field_map:
        raise ProjectPatchError("import_csv_metadata 必须提供 field_map。")

    index = {}
    for spk_id, spk in (state.get("speakers", {}) or {}).items():
        if not isinstance(spk, dict):
            continue
        spk_name = str(spk.get("name") or spk.get("speaker_name") or spk_id).strip()
        for item_id, item in (spk.get("items", {}) or {}).items():
            if not isinstance(item, dict):
                continue
            if match_on == "item_id":
                raw_val = item.get("item_id") or item_id
            elif match_on == "speaker_name":
                raw_val = item.get("speaker_name") or spk_name
            elif match_on == "speaker_id":
                raw_val = item.get("speaker_id") or spk_id
            else:
                raw_val = item.get(match_on, "")
            key = str(raw_val or "").strip()
            if key:
                index.setdefault(key, []).append(item)

    matched = 0
    for row in rows:
        if not isinstance(row, dict):
            raise ProjectPatchError("import_csv_metadata 的每一行必须是字典。")
        row_key = str(row.get(match_on, "")).strip()
        if not row_key or row_key not in index:
            continue
        for item in index[row_key]:
            for csv_key, target_field in field_map.items():
                if csv_key in row:
                    _apply_mapped_field(item, str(target_field), row[csv_key])
            matched += 1
    records.append({"op": "import_csv_metadata", "matched_items": matched})


APPLIERS = {
    "set_item_fields": _apply_set_item_fields,
    "recompute_pitch": _apply_recompute_pitch,
    "recompute_formant": _apply_recompute_formant,
    "trim_item_audio": _apply_trim_item_audio,
    "split_project": _apply_split_project,
    "import_csv_metadata": _apply_import_csv_metadata,
}


def _append_or_update_script_run(runs_list, new_record):
    if not new_record:
        return
    rec = copy.deepcopy(new_record)
    new_sha = rec.get("code_sha256")
    if runs_list and new_sha and runs_list[-1].get("code_sha256") == new_sha:
        last = runs_list[-1]
        last["run_time"] = rec.get("run_time", last.get("run_time"))
        last["run_count"] = last.get("run_count", 1) + 1
        if "summary" in rec:
            last["summary"] = rec["summary"]
        if "outputs" in rec:
            last["outputs"] = rec["outputs"]
        if "title" in rec:
            last["title"] = rec["title"]
        if "description" in rec:
            last["description"] = rec["description"]
    else:
        if "run_count" not in rec:
            rec["run_count"] = 1
        runs_list.append(rec)


def apply_project_patch_to_teproj(teproj_path, patch_result, output_path, run_record=None):
    """
    将数据处理脚本返回的受控操作应用到 .teproj，并另存为 output_path。
    返回执行摘要字典。
    """
    if not teproj_path or not os.path.exists(teproj_path):
        raise ProjectPatchError("找不到输入 .teproj 工程文件。")
    if not output_path:
        raise ProjectPatchError("必须提供输出 .teproj 路径。")
    if _same_file_path(teproj_path, output_path):
        raise ProjectPatchError("输出工程不能覆盖原 .teproj，请另存为新文件。")

    patch = _coerce_patch_result(patch_result)
    summary = summarize_project_patch(patch_result)
    if not patch["operations"]:
        return {
            **summary,
            "output_path": None,
            "applied_operations": [],
            "message": "脚本没有返回任何工程修改操作。",
        }

    # 分离 split_project 操作和非 split_project 操作
    non_split_ops = [op for op in patch["operations"] if op.get("op") != "split_project"]
    split_ops = [op for op in patch["operations"] if op.get("op") == "split_project"]

    temp_dir = tempfile.mkdtemp(prefix="phontracer_patch_")
    records = []
    try:
        read_project_metadata_from_archive(teproj_path)
        _extract_project(teproj_path, temp_dir)
        base_state = _validate_project_workspace_for_import(temp_dir)

        # 1. 先生效所有的非拆分操作，得到基础状态（带长音频解码缓存）
        long_audio_cache = {}
        for idx, op in enumerate(non_split_ops, start=1):
            applier = APPLIERS.get(op.get("op"))
            if applier is None:
                raise ProjectPatchError(f"操作类型不受支持：{op.get('op')}")
            try:
                applier(base_state, temp_dir, op, records, long_audio_cache=long_audio_cache)
            except TypeError:
                applier(base_state, temp_dir, op, records)

        # 2. 根据拆分操作数量决定写入逻辑
        if not split_ops:
            # 没有拆分，直接写入 output_path
            state = base_state
            state.setdefault("custom_script_runs", [])
            if run_record:
                _append_or_update_script_run(state["custom_script_runs"], run_record)
            _save_project_state(temp_dir, state)
            _prune_workspace_resources_for_state(temp_dir, state)
            _validate_project_workspace_for_import(temp_dir)
            _write_project_archive(temp_dir, output_path)
            return {
                **summary,
                "output_path": output_path,
                "applied_operations": records,
                "message": f"已生成数据处理后的工程：{output_path}",
            }
        elif len(split_ops) == 1:
            # 只有一个拆分操作，也直接写入 output_path
            state = base_state
            op = split_ops[0]
            _apply_split_project(state, temp_dir, op, records)
            state.setdefault("custom_script_runs", [])
            if run_record:
                _append_or_update_script_run(state["custom_script_runs"], run_record)
            _save_project_state(temp_dir, state)
            _prune_workspace_resources_for_state(temp_dir, state)
            _validate_project_workspace_for_import(temp_dir)
            _write_project_archive(temp_dir, output_path)
            return {
                **summary,
                "output_path": output_path,
                "applied_operations": records,
                "message": f"已生成数据处理后的工程：{output_path}",
            }
        else:
            # 存在多个拆分操作，为每一个拆分操作生成一个独立的工程文件
            base_dir = os.path.dirname(output_path)
            base_filename = os.path.basename(output_path)
            name_part, ext_part = os.path.splitext(base_filename)

            output_paths = []
            used_output_paths = set()
            _save_project_state(temp_dir, base_state)
            for op in split_ops:
                split_dir = tempfile.mkdtemp(prefix="phontracer_split_")
                state_copy = copy.deepcopy(base_state)
                try:
                    shutil.copytree(temp_dir, split_dir, dirs_exist_ok=True)
                    _apply_split_project(state_copy, split_dir, op, records)

                    safe_suffix = _safe_token(op.get("name") or "split")
                    sub_filename = f"{name_part}_{safe_suffix}{ext_part}"
                    sub_output_path = os.path.join(base_dir, sub_filename)
                    counter = 2
                    while os.path.normcase(os.path.abspath(sub_output_path)) in used_output_paths:
                        sub_filename = f"{name_part}_{safe_suffix}_{counter}{ext_part}"
                        sub_output_path = os.path.join(base_dir, sub_filename)
                        counter += 1
                    if _same_file_path(teproj_path, sub_output_path):
                        raise ProjectPatchError("拆分工程的输出路径不能覆盖原 .teproj。")
                    used_output_paths.add(os.path.normcase(os.path.abspath(sub_output_path)))

                    state_copy.setdefault("custom_script_runs", [])
                    if run_record:
                        sub_record = copy.deepcopy(run_record)
                        if sub_record.get("outputs"):
                            sub_record["outputs"][0]["saved_path"] = sub_output_path
                            sub_record["outputs"][0]["filename"] = sub_filename
                        _append_or_update_script_run(state_copy["custom_script_runs"], sub_record)

                    _save_project_state(split_dir, state_copy)
                    _prune_workspace_resources_for_state(split_dir, state_copy)
                    _validate_project_workspace_for_import(split_dir)
                    _write_project_archive(split_dir, sub_output_path)
                    output_paths.append(sub_output_path)
                finally:
                    shutil.rmtree(split_dir, ignore_errors=True)

            return {
                **summary,
                "output_path": output_paths[0],
                "output_paths": output_paths,
                "applied_operations": records,
                "message": f"已成功拆分为 {len(output_paths)} 个子工程文件，保存在同级目录下。\n首个子工程：{output_paths[0]}",
            }
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


__all__ = [
    "SUPPORTED_PATCH_OPS",
    "ALLOWED_ITEM_FIELDS",
    "ANALYSIS_CACHE_FIELDS",
    "ProjectPatchError",
    "_same_file_path",
    "_safe_token",
    "_normalize_rel_path",
    "_resolve_resource",
    "_coerce_patch_result",
    "summarize_project_patch",
    "_extract_project",
    "_project_manager_for_validation",
    "_validate_project_workspace_for_import",
    "_validate_project_archive_for_import",
    "_write_project_archive",
    "_load_project_state",
    "_sanitize_for_json",
    "_save_project_state",
    "_iter_state_resource_paths",
    "_prune_workspace_resources_for_state",
    "_find_item",
    "_clear_analysis_cache_refs",
    "_resource_name",
    "_audio_name",
    "_item_params",
    "_load_item_sound",
    "_segment_sound_for_item",
    "_restore_global_xs",
    "_apply_set_item_fields",
    "_apply_recompute_pitch",
    "_apply_recompute_formant",
    "_apply_trim_item_audio",
    "_apply_split_project",
    "_split_csv_list_value",
    "_apply_mapped_field",
    "_apply_import_csv_metadata",
    "APPLIERS",
    "_append_or_update_script_run",
    "apply_project_patch_to_teproj",
]
