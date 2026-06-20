import os
import json
import uuid
import zipfile
import shutil
import stat
import struct
import math
import hashlib
import numpy as np

MAX_ARCHIVE_MEMBERS = 10000
MAX_ARCHIVE_MEMBER_BYTES = 2 * 1024 * 1024 * 1024
MAX_ARCHIVE_TOTAL_BYTES = 20 * 1024 * 1024 * 1024
MAX_PROJECT_JSON_BYTES = 20 * 1024 * 1024
SUPPORTED_PROJECT_VERSIONS = {"1.0"}


def safe_resource_token(value, fallback, max_length=96):
    """把工程内 ID 转成跨平台安全的资源文件名片段，但不改动逻辑 ID。"""
    raw = str(value or "").strip()
    safe = "".join(
        "_" if ord(char) < 32 or char in '<>:"/\\|?*' else char
        for char in raw
    ).rstrip(" .")
    safe = safe or fallback
    if safe.split(".", 1)[0].upper() in {
        "CON", "PRN", "AUX", "NUL",
        *(f"COM{index}" for index in range(1, 10)),
        *(f"LPT{index}" for index in range(1, 10)),
    }:
        safe = f"_{safe}"
    if safe != raw or len(safe) > max_length:
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:10]
        prefix_length = max(1, max_length - len(digest) - 1)
        safe = f"{safe[:prefix_length].rstrip(' .') or fallback[:prefix_length]}_{digest}"
    else:
        safe = safe[:max_length]
    return safe

def validate_project_version(state):
    version = str(state.get("version", "1.0"))
    if version not in SUPPORTED_PROJECT_VERSIONS:
        raise ValueError(f"不支持的工程版本：{version}")
    return version

def validate_project_archive_members(zip_file):
    infos = zip_file.infolist()
    if len(infos) > MAX_ARCHIVE_MEMBERS:
        raise ValueError(f"工程文件包含过多成员：{len(infos)} 个")

    seen = set()
    total_size = 0
    for member in infos:
        normalized = member.filename.replace("\\", "/")
        if not normalized:
            raise ValueError("工程文件包含空路径成员")
        parts = [part for part in normalized.split("/") if part not in ("", ".")]
        if not parts or normalized.startswith("/") or any(part == ".." for part in parts):
            raise ValueError("工程文件包含非法路径")
        for part in parts:
            if (
                any(ord(char) < 32 or char in '<>:"|?*' for char in part)
                or part.endswith((" ", "."))
                or part.split(".", 1)[0].upper() in {
                    "CON", "PRN", "AUX", "NUL",
                    *(f"COM{index}" for index in range(1, 10)),
                    *(f"LPT{index}" for index in range(1, 10)),
                }
            ):
                raise ValueError("工程文件包含跨平台不兼容路径")

        # Windows 与默认配置的 macOS 文件系统通常不区分大小写；按折叠后的
        # 规范路径去重，避免同一成员在解压时被静默覆盖。
        portable_key = "/".join(parts).casefold()
        if portable_key in seen:
            raise ValueError(f"工程文件包含重复成员：{normalized}")
        seen.add(portable_key)

        file_type = (member.external_attr >> 16) & 0o170000
        if file_type == stat.S_IFLNK:
            raise ValueError("工程文件不能包含符号链接")
        if member.file_size > MAX_ARCHIVE_MEMBER_BYTES:
            raise ValueError(f"工程文件成员过大：{normalized}")
        total_size += member.file_size
        if total_size > MAX_ARCHIVE_TOTAL_BYTES:
            raise ValueError("工程文件解压后的总大小超过限制")

    return infos

def read_project_metadata_from_archive(zip_path):
    if not zipfile.is_zipfile(zip_path):
        raise ValueError("该文件不是有效的工程压缩包 (ZIP/teproj)")

    with zipfile.ZipFile(zip_path, "r") as zip_file:
        infos = validate_project_archive_members(zip_file)
        info_by_name = {info.filename.replace("\\", "/"): info for info in infos}
        project_info = info_by_name.get("project.json")
        if project_info is None:
            raise ValueError("工程文件损坏：未找到 project.json")
        if project_info.file_size > MAX_PROJECT_JSON_BYTES:
            raise ValueError("工程文件中的 project.json 过大")

        with zip_file.open(project_info) as project_file:
            raw = project_file.read(MAX_PROJECT_JSON_BYTES + 1)
        if len(raw) > MAX_PROJECT_JSON_BYTES:
            raise ValueError("工程文件中的 project.json 过大")

    try:
        state = json.loads(raw.decode("utf-8"))
    except UnicodeDecodeError:
        state = json.loads(raw.decode("gb18030"))

    if not isinstance(state, dict):
        raise ValueError("工程文件损坏：project.json 顶层必须是对象")
    validate_project_version(state)
    return state, [info.filename.replace("\\", "/") for info in infos]

def safe_extract_zip(zip_ref, destination):
    destination_abs = os.path.abspath(destination)
    infos = validate_project_archive_members(zip_ref)
    for member in infos:
        normalized = member.filename.replace("\\", "/")
        member_path = os.path.abspath(os.path.join(destination_abs, *normalized.split("/")))
        if os.path.commonpath([member_path, destination_abs]) != destination_abs:
            raise ValueError("工程文件包含非法路径")
        if member.is_dir():
            os.makedirs(member_path, exist_ok=True)
            continue
        os.makedirs(os.path.dirname(member_path), exist_ok=True)
        with zip_ref.open(member) as source, open(member_path, "wb") as target:
            shutil.copyfileobj(source, target)

def resolve_workspace_path(path, workspace_dir):
    if not path:
        return path
    norm = str(path).replace("\\", "/")
    if norm.startswith("audio/") or norm.startswith("data/"):
        return os.path.join(workspace_dir, *norm.split("/"))
    if os.path.isabs(path):
        return path
    return os.path.join(workspace_dir, path)


def repair_wav_header(path):
    """修复旧版 PhonRec 写错的 PCM WAV 平均字节率字段。

    旧版录音文件可能把单声道 16-bit WAV 的 ``nAvgBytesPerSec`` 写成
    采样率的四倍。音频数据本身没有损坏，但 libsndfile/Praat 会拒绝读取。
    这里只在 RIFF/WAVE、fmt 块完整且其他关键字段可信时修正该字段。
    """
    if not path or os.path.splitext(str(path))[1].lower() not in {".wav", ".wave"}:
        return False

    try:
        with open(path, "r+b") as wav_file:
            header = wav_file.read(12)
            if len(header) != 12 or header[:4] not in {b"RIFF", b"RF64"} or header[8:12] != b"WAVE":
                return False

            while True:
                chunk_header = wav_file.read(8)
                if len(chunk_header) != 8:
                    return False
                chunk_id, chunk_size = struct.unpack("<4sI", chunk_header)
                chunk_data_offset = wav_file.tell()

                if chunk_id == b"fmt ":
                    if chunk_size < 16:
                        return False
                    fmt_data = wav_file.read(16)
                    if len(fmt_data) != 16:
                        return False
                    audio_format, channels, sample_rate, byte_rate, block_align, bits_per_sample = struct.unpack(
                        "<HHIIHH", fmt_data
                    )
                    if audio_format not in {1, 3, 0xFFFE}:
                        return False
                    if channels <= 0 or sample_rate <= 0 or block_align <= 0 or bits_per_sample <= 0:
                        return False
                    expected_block_align = channels * ((bits_per_sample + 7) // 8)
                    if block_align != expected_block_align:
                        return False

                    expected_byte_rate = sample_rate * block_align
                    if expected_byte_rate > 0xFFFFFFFF or byte_rate == expected_byte_rate:
                        return False

                    wav_file.seek(chunk_data_offset + 8)
                    wav_file.write(struct.pack("<I", expected_byte_rate))
                    wav_file.flush()
                    return True

                wav_file.seek(chunk_data_offset + chunk_size + (chunk_size % 2))
    except (OSError, ValueError, OverflowError, struct.error):
        return False


def load_compatible_sound(path):
    """读取音频，并兼容修复旧版 PhonRec 的 WAV 头。"""
    repair_wav_header(path)
    import parselmouth
    return parselmouth.Sound(path)


def normalize_independent_item_boundaries(item, duration, label=None):
    """为独立音频补齐并校正本地时间轴边界，返回是否发生修改。"""
    try:
        duration = float(duration)
    except (TypeError, ValueError):
        return False
    if not math.isfinite(duration) or duration <= 0:
        return False

    changed = False

    def valid_pair(start, end, lower=0.0, upper=duration):
        try:
            start = float(start)
            end = float(end)
        except (TypeError, ValueError):
            return None
        tolerance = max(1e-6, duration * 1e-7)
        if not math.isfinite(start) or not math.isfinite(end):
            return None
        if start < lower - tolerance or end > upper + tolerance or start >= end:
            return None
        return max(lower, start), min(upper, end)

    local_pair = valid_pair(item.get("start"), item.get("end"))
    if local_pair is None:
        local_pair = (0.0, duration)
        changed = True
    start, end = local_pair
    if item.get("start") != start or item.get("end") != end:
        changed = True
    item["start"], item["end"] = start, end

    for start_key, end_key, fallback in (
        ("raw_start", "raw_end", (start, end)),
        ("macro_start", "macro_end", (0.0, duration)),
    ):
        pair = valid_pair(item.get(start_key), item.get(end_key))
        if pair is None:
            pair = fallback
            changed = True
        if item.get(start_key) != pair[0] or item.get(end_key) != pair[1]:
            changed = True
        item[start_key], item[end_key] = pair

    from .data_utils import split_into_syllables
    syllable_count = max(1, len(split_into_syllables(label if label is not None else item.get("label", ""))))
    old_bounds = item.get("chars_bounds")
    normalized_bounds = []
    if isinstance(old_bounds, (list, tuple)) and len(old_bounds) == syllable_count:
        previous_end = start
        for pair in old_bounds:
            if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                normalized_bounds = []
                break
            valid = valid_pair(pair[0], pair[1], start, end)
            if valid is None or valid[0] < previous_end - 1e-6:
                normalized_bounds = []
                break
            normalized_bounds.append([valid[0], valid[1]])
            previous_end = valid[1]

    if len(normalized_bounds) != syllable_count:
        edges = np.linspace(start, end, syllable_count + 1).tolist()
        normalized_bounds = [[float(edges[i]), float(edges[i + 1])] for i in range(syllable_count)]
        changed = True
    if not isinstance(old_bounds, (list, tuple)) or old_bounds != normalized_bounds:
        changed = True
    item["chars_bounds"] = normalized_bounds

    inner_splits = [bound[1] for bound in normalized_bounds[:-1]]
    if item.get("inner_splits") != inner_splits:
        changed = True
    item["inner_splits"] = inner_splits
    return changed

def _iter_state_resource_refs(state):
    for spk_id, spk_data in state.get("speakers", {}).items():
        if not isinstance(spk_data, dict):
            continue
        long_audio_rel = spk_data.get("long_audio_path")
        if long_audio_rel:
            yield spk_data, "long_audio_path", None, long_audio_rel

        for idx, p in enumerate(spk_data.get("pending_batch_paths", [])):
            if p:
                yield spk_data["pending_batch_paths"], idx, None, p

        for item_id, item in spk_data.get("items", {}).items():
            if not isinstance(item, dict):
                continue
            for key in ("path", "pitch_data_file", "formant_data_file"):
                path = item.get(key)
                if path:
                    yield item, key, None, path

def validate_project_resources(state, workspace_dir):
    speakers = state.get("speakers")
    if not isinstance(speakers, dict) or not speakers:
        raise ValueError("工程文件损坏：未找到发音人数据")

    speaker_ids = []
    for old_spk_id, spk_data in speakers.items():
        if not isinstance(spk_data, dict):
            raise ValueError(f"工程文件损坏：发音人 {old_spk_id} 数据格式错误")
        speaker_id = spk_data.get("id", old_spk_id)
        if not isinstance(speaker_id, str) or not speaker_id:
            raise ValueError(f"工程文件损坏：发音人 {old_spk_id} 的 ID 格式错误")
        speaker_ids.append(speaker_id)
        pending_batch_paths = spk_data.get("pending_batch_paths", [])
        if not isinstance(pending_batch_paths, list):
            raise ValueError(f"工程文件损坏：发音人 {old_spk_id} 的批量音频路径格式错误")
        items = spk_data.get("items", {})
        if not isinstance(items, dict):
            raise ValueError(f"工程文件损坏：发音人 {old_spk_id} 的条目格式错误")
        if any(not isinstance(item, dict) for item in items.values()):
            raise ValueError(f"工程文件损坏：发音人 {old_spk_id} 的条目内容格式错误")
    if len(set(speaker_ids)) != len(speaker_ids):
        raise ValueError("工程文件损坏：存在重复的发音人 ID")

    audio_paths = set()
    cache_paths = {}
    workspace_abs = os.path.abspath(workspace_dir)
    for _owner, key, _index, rel_path in _iter_state_resource_refs(state):
        normalized = str(rel_path).replace("\\", "/")
        if not normalized.startswith(("audio/", "data/")):
            raise ValueError(f"工程资源路径必须位于 audio/ 或 data/：{rel_path}")
        file_path = resolve_workspace_path(rel_path, workspace_dir=workspace_dir)
        file_path_abs = os.path.abspath(file_path)
        try:
            if os.path.commonpath([file_path_abs, workspace_abs]) != workspace_abs:
                raise ValueError(f"工程资源路径越界：{rel_path}")
        except ValueError:
            raise ValueError(f"工程资源路径越界：{rel_path}")
        if not os.path.isfile(file_path):
            raise FileNotFoundError(f"工程文件缺少资源：{rel_path}")
        if normalized.startswith("audio/"):
            audio_paths.add(file_path)
        elif key == "pitch_data_file":
            cache_paths[file_path] = ("xs", "freqs")
        elif key == "formant_data_file":
            cache_paths[file_path] = ("xs", "f1", "f2")

    for audio_path in sorted(audio_paths):
        load_compatible_sound(audio_path)

    for cache_path, required_keys in cache_paths.items():
        with np.load(cache_path) as loaded:
            missing = [key for key in required_keys if key not in loaded]
            if missing:
                raise ValueError(f"工程缓存损坏：{os.path.basename(cache_path)} 缺少 {', '.join(missing)}")

def adapt_project_state(state: dict, workspace_dir: str):
    """确保工程状态符合规范，处理字表合并、ID对齐、长音频切片、音频重命名与缓存失效。"""
    warnings = []
    summary = {
        "merged_speakers": 0,
        "sliced_items": 0,
        "missing_items": 0,
        "downgraded_items": 0
    }

    # 1. 识别或重构 groups
    groups = state.get("groups", [])
    has_groups = isinstance(groups, list) and len(groups) > 0 and any(len(g.get("items", [])) > 0 for g in groups)

    global_slots = []
    groups_slots_map = {}

    if not has_groups:
        # 重构：旧 PhonTracer 工程以发音人条目为源进行合并
        active_spk_id = state.get("active_speaker_id")
        speakers = state.get("speakers", {})
        spk_ids = list(speakers.keys())
        
        ordered_spk_ids = []
        if active_spk_id in speakers:
            ordered_spk_ids.append(active_spk_id)
        for sid in spk_ids:
            if sid not in ordered_spk_ids:
                ordered_spk_ids.append(sid)

        slot_to_canonical = {}

        # 遍历所有发音人，收集词项语义并集并按原始顺序合并
        for spk_id in ordered_spk_ids:
            spk_data = speakers[spk_id]
            occ_counts = {}
            spk_slots = []
            for item_id, item_data in spk_data.get("items", {}).items():
                grp_name = item_data.get("group") or item_data.get("group_name") or "默认组"
                word_label = item_data.get("label") or item_data.get("word") or "未命名"
                key = (grp_name, word_label)
                occ = occ_counts.get(key, 0)
                occ_counts[key] = occ + 1
                slot = (grp_name, word_label, occ)
                spk_slots.append(slot)

                def to_list(val):
                    if isinstance(val, list):
                        return val
                    if isinstance(val, str):
                        return [t.strip() for t in val.split(",") if t.strip()]
                    return []

                def to_dict(val):
                    if isinstance(val, dict):
                        return val
                    return {}

                if slot not in slot_to_canonical:
                    global_slots.append(slot)
                    slot_to_canonical[slot] = {
                        "id": item_data.get("id") or item_id,
                        "label": word_label,
                        "note": item_data.get("item_note") or item_data.get("note") or "",
                        "tags": to_list(item_data.get("item_tags") or item_data.get("tags") or []),
                        "aliases": to_list(item_data.get("item_aliases") or item_data.get("aliases") or []),
                        "meta": to_dict(item_data.get("item_meta") or item_data.get("meta") or {}),
                        "metadata_source": item_data.get("metadata_source") or "导入工程",
                        "group_name": grp_name,
                        "group_note": item_data.get("group_note") or "",
                        "group_tags": to_list(item_data.get("group_tags") or []),
                        "group_meta": to_dict(item_data.get("group_meta") or {}),
                    }
                else:
                    # 语义并集
                    canon = slot_to_canonical[slot]
                    # 合并标签与别名
                    for t_val in to_list(item_data.get("item_tags") or item_data.get("tags") or []):
                        if t_val not in canon["tags"]:
                            canon["tags"].append(t_val)
                    for a_val in to_list(item_data.get("item_aliases") or item_data.get("aliases") or []):
                        if a_val not in canon["aliases"]:
                            canon["aliases"].append(a_val)
                    # 合并 meta
                    other_meta = to_dict(item_data.get("item_meta") or item_data.get("meta") or {})
                    for mk, mv in other_meta.items():
                        if mk not in canon["meta"]:
                            canon["meta"][mk] = mv
                    other_group_meta = to_dict(item_data.get("group_meta") or {})
                    for mk, mv in other_group_meta.items():
                        if mk not in canon["group_meta"]:
                            canon["group_meta"][mk] = mv
                    # 检查备注冲突
                    other_note = item_data.get("item_note") or item_data.get("note") or ""
                    if other_note and other_note != canon["note"]:
                        warnings.append(f"词项 '{word_label}' (分组: '{grp_name}') 在发音人 {spk_data.get('name', spk_id)} 中的备注与主发音人不同")

            # 检查顺序差异
            indices = [global_slots.index(slot) for slot in spk_slots if slot in global_slots]
            if indices != sorted(indices):
                warnings.append(f"发音人 {spk_data.get('name', spk_id)} 词项顺序与规范顺序存在差异")

        # 检查发音人缺项 (缺失词项)
        for spk_id in ordered_spk_ids:
            spk_data = speakers[spk_id]
            spk_slots_set = set()
            occ_counts = {}
            for item_id, item_data in spk_data.get("items", {}).items():
                grp_name = item_data.get("group") or item_data.get("group_name") or "默认组"
                word_label = item_data.get("label") or item_data.get("word") or "未命名"
                key = (grp_name, word_label)
                occ = occ_counts.get(key, 0)
                occ_counts[key] = occ + 1
                spk_slots_set.add((grp_name, word_label, occ))

            for slot in global_slots:
                if slot not in spk_slots_set:
                    warnings.append(f"发音人 {spk_data.get('name', spk_id)} 缺少词项 '{slot[1]}' (分组: '{slot[0]}')")
                    summary["missing_items"] += 1

        # 构建规范 groups
        group_order = []
        grouped_items = {}
        for slot in global_slots:
            grp_name = slot[0]
            canon = slot_to_canonical[slot]
            if grp_name not in grouped_items:
                grouped_items[grp_name] = []
                group_order.append(grp_name)
            grouped_items[grp_name].append({
                "id": canon["id"],
                "label": canon["label"],
                "note": canon["note"],
                "tags": canon["tags"],
                "aliases": canon["aliases"],
                "meta": canon["meta"],
                "metadata_source": canon["metadata_source"]
            })

        groups = []
        for grp_name in group_order:
            grp_note = ""
            grp_tags = []
            grp_meta = {}
            for slot in global_slots:
                if slot[0] == grp_name:
                    canonical = slot_to_canonical[slot]
                    if not grp_note and canonical["group_note"]:
                        grp_note = canonical["group_note"]
                    for tag in canonical["group_tags"]:
                        if tag not in grp_tags:
                            grp_tags.append(tag)
                    for key, value in canonical["group_meta"].items():
                        if key not in grp_meta:
                            grp_meta[key] = value
            groups.append({
                "id": f"grp_{uuid.uuid4().hex[:8]}",
                "name": grp_name,
                "note": grp_note,
                "tags": grp_tags,
                "meta": grp_meta,
                "items": grouped_items[grp_name]
            })

        state["groups"] = groups
        summary["merged_speakers"] = len(ordered_spk_ids)

    # 建立 global_slots & groups_slots_map 从事实源 groups
    global_slots = []
    groups_slots_map = {}
    groups_ids_map = {}
    for group in state["groups"]:
        grp_name = group.get("name", "默认组")
        grp_note = group.get("note", "")
        grp_tags = group.get("tags", []) if isinstance(group.get("tags", []), list) else []
        grp_meta = group.get("meta", {}) if isinstance(group.get("meta", {}), dict) else {}
        occ_counts = {}
        for item in group.get("items", []):
            word_label = item.get("label") or item.get("word") or "未命名"
            occ = occ_counts.get(word_label, 0)
            occ_counts[word_label] = occ + 1
            slot = (grp_name, word_label, occ)
            global_slots.append(slot)
            
            canonical_info = {
                "id": item["id"],
                "label": word_label,
                "note": item.get("note", ""),
                "tags": item.get("tags", []),
                "aliases": item.get("aliases", []),
                "meta": item.get("meta", {}),
                "metadata_source": item.get("metadata_source", "导入字表"),
                "group_name": grp_name,
                "group_note": grp_note,
                "group_tags": grp_tags,
                "group_meta": grp_meta,
            }
            groups_slots_map[slot] = canonical_info
            groups_ids_map[item["id"]] = canonical_info

    # 2. 规范化并对齐每个发音人的 items，执行录音转换
    for spk_id, spk_data in list(state.get("speakers", {}).items()):
        spk_name = spk_data.get("name", spk_id)
        safe_spk_id = safe_resource_token(spk_id, "speaker", 64)
        tab_mode = spk_data.get("tab_mode", "多条独立音频")
        old_items = spk_data.get("items", {})
        has_item_audio = any(
            isinstance(item, dict) and item.get("path")
            for item in old_items.values()
        )
        # 旧工程的 tab_mode 曾出现错误值；长音频存在且条目没有独立音频时仍按长音频转换。
        is_long_mode = "单条" in tab_mode or bool(spk_data.get("long_audio_path") and not has_item_audio)

        long_audio_abs = None
        long_source_sound = None
        long_audio_error = None
        if is_long_mode:
            long_audio_rel = spk_data.get("long_audio_path")
            if long_audio_rel:
                long_audio_abs = resolve_workspace_path(long_audio_rel, workspace_dir)
                if os.path.isfile(long_audio_abs):
                    try:
                        # 长音频只读取一次，避免按条目重复解析大文件。
                        long_source_sound = load_compatible_sound(long_audio_abs)
                    except Exception as exc:
                        long_audio_error = exc

        new_items = {}

        # 扫描发音人的条目，映射到规范字表 slot
        spk_occ = {}
        for old_id, item_data in old_items.items():
            grp_name = item_data.get("group") or item_data.get("group_name") or "默认组"
            word_label = item_data.get("label") or item_data.get("word") or "未命名"
            key = (grp_name, word_label)
            occ = spk_occ.get(key, 0)
            spk_occ[key] = occ + 1
            slot = (grp_name, word_label, occ)

            canonical_item = None
            if old_id in groups_ids_map:
                canonical_item = groups_ids_map[old_id]
            elif slot in groups_slots_map:
                canonical_item = groups_slots_map[slot]

            if canonical_item is not None:
                canonical_id = canonical_item["id"]

                # 转换已有录音
                if is_long_mode:
                    # 长音频切片
                    start = item_data.get("macro_start")
                    end = item_data.get("macro_end")
                    if start is None or end is None:
                        start = item_data.get("start")
                        end = item_data.get("end")

                    sliced_successfully = False
                    if long_source_sound is not None and start is not None and end is not None:
                        try:
                            source_sound = long_source_sound
                            duration = source_sound.duration
                            if start < 0 or end > duration or start >= end:
                                raise ValueError(f"切分边界无效 [{start}, {end}], 音频总长: {duration:.2f}s")

                            safe_canonical_id = safe_resource_token(canonical_id, "item", 64)
                            dest_dir = os.path.join(workspace_dir, "audio", safe_spk_id)
                            os.makedirs(dest_dir, exist_ok=True)
                            dest_filename = f"{safe_spk_id}_{safe_canonical_id}.wav"
                            dest_path = os.path.join(dest_dir, dest_filename)
                            source_sound.extract_part(
                                from_time=float(start),
                                to_time=float(end),
                                preserve_times=False,
                            ).save(dest_path, "WAV")

                            item_data["path"] = f"audio/{safe_spk_id}/{dest_filename}"
                            # 边界转换到单项音频本地时间轴
                            duration_local = float(end - start)
                            item_data["start"] = 0.0
                            item_data["end"] = duration_local
                            item_data["macro_start"] = 0.0
                            item_data["macro_end"] = duration_local
                            
                            # 时间轴改变后，所有分析结果均作废；保留来源边界便于追溯。
                            item_data["source_segment"] = {
                                "path": spk_data.get("long_audio_path"),
                                "start": float(start),
                                "end": float(end),
                            }
                            for cache_key in (
                                "pitch_data_file", "formant_data_file", "pitch_data", "formant_data",
                                "preview_f0", "preview_formants", "has_empty_data", "raw_start", "raw_end",
                                "chars_bounds", "inner_splits", "analysis_params", "analysis_state",
                            ):
                                item_data.pop(cache_key, None)
                            normalize_independent_item_boundaries(
                                item_data,
                                duration_local,
                                label=canonical_item["label"],
                            )
                            
                            summary["sliced_items"] += 1
                            sliced_successfully = True
                        except Exception as e:
                            warnings.append(f"发音人 {spk_name} 词项 '{word_label}' (ID: {canonical_id}) 的长音频切片失败: {e}")
                            summary["downgraded_items"] += 1

                    elif long_audio_error is not None:
                        warnings.append(
                            f"发音人 {spk_name} 词项 '{word_label}' (ID: {canonical_id}) 的长音频切片失败: {long_audio_error}"
                        )
                        summary["downgraded_items"] += 1

                    if not sliced_successfully:
                        # 降级为未录制
                        item_data.pop("path", None)
                        for k in (
                            "start", "end", "macro_start", "macro_end", "pitch_data_file", "formant_data_file",
                            "pitch_data", "formant_data", "preview_f0", "preview_formants", "has_empty_data",
                            "raw_start", "raw_end", "chars_bounds", "inner_splits", "analysis_params", "analysis_state",
                        ):
                            item_data.pop(k, None)
                else:
                    # 独立音频重命名并映射
                    old_path_rel = item_data.get("path")
                    if old_path_rel:
                        safe_old_id = safe_resource_token(old_id, "item", 64)
                        safe_canonical_id = safe_resource_token(canonical_id, "item", 64)
                        # 寻找源 WAV 文件
                        src_wav = None
                        candidates = [
                            resolve_workspace_path(old_path_rel, workspace_dir),
                            os.path.join(workspace_dir, "audio", safe_spk_id, f"{safe_spk_id}_{safe_old_id}.wav"),
                            os.path.join(workspace_dir, "audio", f"{safe_spk_id}_{safe_old_id}.wav"),
                        ]
                        for c in candidates:
                            if os.path.isfile(c):
                                src_wav = c
                                break

                        if not src_wav:
                            suffix = f"{safe_spk_id}_{safe_old_id}.wav".lower()
                            for r, _, files in os.walk(os.path.join(workspace_dir, "audio")):
                                for f in files:
                                    if f.lower().endswith(suffix):
                                        src_wav = os.path.join(r, f)
                                        break
                                if src_wav:
                                    break

                        if src_wav:
                            dest_rel = f"audio/{safe_spk_id}/{safe_spk_id}_{safe_canonical_id}.wav"
                            dest_abs = os.path.join(workspace_dir, dest_rel)
                            if os.path.normpath(src_wav) != os.path.normpath(dest_abs):
                                os.makedirs(os.path.dirname(dest_abs), exist_ok=True)
                                shutil.copy2(src_wav, dest_abs)
                                try:
                                    os.remove(src_wav)
                                except OSError:
                                    pass
                            item_data["path"] = dest_rel

                            try:
                                independent_sound = load_compatible_sound(dest_abs)
                                normalize_independent_item_boundaries(
                                    item_data,
                                    independent_sound.duration,
                                    label=canonical_item["label"],
                                )
                            except Exception as exc:
                                item_data.pop("path", None)
                                for cache_key in (
                                    "pitch_data_file", "formant_data_file", "pitch_data", "formant_data",
                                    "preview_f0", "preview_formants", "has_empty_data",
                                ):
                                    item_data.pop(cache_key, None)
                                warnings.append(
                                    f"发音人 {spk_name} 词项 '{word_label}' (ID: {canonical_id}) 的独立音频读取失败: {exc}"
                                )
                                summary["downgraded_items"] += 1

                            # 同样重命名 pitch and formantNPZ cache files
                            for cache_key, cache_suffix in (("pitch_data_file", ".npz"), ("formant_data_file", "_formant.npz")):
                                c_path = item_data.get(cache_key)
                                if c_path:
                                    c_abs = resolve_workspace_path(c_path, workspace_dir)
                                    if os.path.isfile(c_abs):
                                        dest_cache_rel = f"data/{safe_spk_id}_{safe_canonical_id}{cache_suffix}"
                                        dest_cache_abs = os.path.join(workspace_dir, dest_cache_rel)
                                        if os.path.normpath(c_abs) != os.path.normpath(dest_cache_abs):
                                            os.makedirs(os.path.dirname(dest_cache_abs), exist_ok=True)
                                            shutil.copy2(c_abs, dest_cache_abs)
                                            try:
                                                os.remove(c_abs)
                                            except OSError:
                                                pass
                                        item_data[cache_key] = dest_cache_rel
                        else:
                            # 缺失音频降级为未录制
                            item_data.pop("path", None)
                            for k in ("pitch_data_file", "formant_data_file", "preview_f0", "has_empty_data"):
                                item_data.pop(k, None)
                            warnings.append(f"发音人 {spk_name} 词项 '{word_label}' (ID: {canonical_id}) 的独立音频文件缺失，已降级为未录制")
                            summary["downgraded_items"] += 1

                # 对齐元数据字段
                item_data["id"] = canonical_id
                item_data["label"] = canonical_item["label"]
                item_data["note"] = canonical_item["note"]
                item_data["tags"] = canonical_item["tags"]
                item_data["aliases"] = canonical_item["aliases"]
                item_data["meta"] = canonical_item["meta"]
                item_data["metadata_source"] = canonical_item["metadata_source"]
                item_data["group"] = grp_name
                # PhonRec 使用简洁字段，主程序使用 item_*/group_* 字段；双写保证
                # .teproj 在两端反复打开和保存后仍保留完整高级字表元数据。
                item_data["item_note"] = canonical_item["note"]
                item_data["item_tags"] = list(canonical_item["tags"])
                item_data["item_aliases"] = list(canonical_item["aliases"])
                item_data["item_meta"] = dict(canonical_item["meta"])
                item_data["group_note"] = canonical_item.get("group_note", "")
                item_data["group_tags"] = list(canonical_item.get("group_tags", []))
                item_data["group_meta"] = dict(canonical_item.get("group_meta", {}))

                new_items[canonical_id] = item_data
            else:
                # 多余条目丢弃
                warnings.append(f"发音人 {spk_name} 的多余录音条目 {old_id} 已删除")

        # 补全缺项
        for slot in global_slots:
            canonical_item = groups_slots_map[slot]
            canonical_id = canonical_item["id"]
            if canonical_id not in new_items:
                new_items[canonical_id] = {
                    "id": canonical_id,
                    "label": canonical_item["label"],
                    "note": canonical_item["note"],
                    "tags": canonical_item["tags"],
                    "aliases": canonical_item["aliases"],
                    "meta": canonical_item["meta"],
                    "metadata_source": canonical_item["metadata_source"],
                    "group": canonical_item["group_name"],
                    "item_note": canonical_item["note"],
                    "item_tags": list(canonical_item["tags"]),
                    "item_aliases": list(canonical_item["aliases"]),
                    "item_meta": dict(canonical_item["meta"]),
                    "group_note": canonical_item.get("group_note", ""),
                    "group_tags": list(canonical_item.get("group_tags", [])),
                    "group_meta": dict(canonical_item.get("group_meta", {})),
                }
                summary["missing_items"] += 1

        spk_data["items"] = new_items
        spk_data["pending_batch_paths"] = list(dict.fromkeys(
            item["path"] for item in new_items.values()
            if isinstance(item, dict) and item.get("path")
        ))
        
        # 强制转换为多条独立音频
        spk_data["tab_mode"] = "多条独立音频"
        spk_data.pop("long_audio_path", None)
        spk_data.pop("pending_long_snd", None)
        spk_data.pop("current_macro_segments", None)
        spk_data.pop("manual_segments", None)

    # 3. 记录转换摘要和来源信息
    state["phonrec"] = {
        "version": "1.0",
        "source": "compatibility_converter",
        "warnings": warnings,
        "summary": summary
    }

    return state, warnings, summary

def prune_unreferenced_resources(state: dict, workspace_dir: str):
    """扫描 audio/ 与 data/ 文件夹，事务化删除未引用的音频及缓存文件，最后清理空文件夹。"""
    referenced = set()
    for spk_id, spk_data in state.get("speakers", {}).items():
        if not isinstance(spk_data, dict):
            continue
        long_audio = spk_data.get("long_audio_path")
        if long_audio:
            referenced.add(os.path.normpath(resolve_workspace_path(long_audio, workspace_dir)))
        for p in spk_data.get("pending_batch_paths", []):
            if p:
                referenced.add(os.path.normpath(resolve_workspace_path(p, workspace_dir)))
        for item_id, item_data in spk_data.get("items", {}).items():
            if not isinstance(item_data, dict):
                continue
            path = item_data.get("path")
            if path:
                referenced.add(os.path.normpath(resolve_workspace_path(path, workspace_dir)))
            pitch = item_data.get("pitch_data_file")
            if pitch:
                referenced.add(os.path.normpath(resolve_workspace_path(pitch, workspace_dir)))
            formant = item_data.get("formant_data_file")
            if formant:
                referenced.add(os.path.normpath(resolve_workspace_path(formant, workspace_dir)))

    for subdir in ("audio", "data"):
        dir_path = os.path.join(workspace_dir, subdir)
        if not os.path.exists(dir_path):
            continue
        for root, dirs, files in os.walk(dir_path, topdown=False):
            for file in files:
                file_path = os.path.normpath(os.path.join(root, file))
                if file.endswith(".tmp") or file.endswith(".temp") or file == ".phonrec-project.json":
                    continue
                if file_path not in referenced:
                    try:
                        os.remove(file_path)
                    except OSError:
                        pass
            for d in dirs:
                sub_dir_path = os.path.join(root, d)
                try:
                    if not os.listdir(sub_dir_path):
                        os.rmdir(sub_dir_path)
                except OSError:
                    pass
