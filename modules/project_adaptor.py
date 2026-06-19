import os
import json
import uuid
import zipfile
import shutil
import stat
import numpy as np
from scipy.io import wavfile

MAX_ARCHIVE_MEMBERS = 10000
MAX_ARCHIVE_MEMBER_BYTES = 2 * 1024 * 1024 * 1024
MAX_ARCHIVE_TOTAL_BYTES = 20 * 1024 * 1024 * 1024
MAX_PROJECT_JSON_BYTES = 20 * 1024 * 1024
SUPPORTED_PROJECT_VERSIONS = {"1.0"}

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
        if normalized in seen:
            raise ValueError(f"工程文件包含重复成员：{normalized}")
        seen.add(normalized)

        parts = [part for part in normalized.split("/") if part not in ("", ".")]
        if normalized.startswith("/") or any(part == ".." for part in parts):
            raise ValueError("工程文件包含非法路径")
        if len(normalized) >= 2 and normalized[1] == ":":
            raise ValueError("工程文件包含非法路径")

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
    for _owner, key, _index, rel_path in _iter_state_resource_refs(state):
        file_path = resolve_workspace_path(rel_path, workspace_dir=workspace_dir)
        if not os.path.isfile(file_path):
            raise FileNotFoundError(f"工程文件缺少资源：{rel_path}")
        if rel_path.startswith("audio/"):
            audio_paths.add(file_path)
        elif key == "pitch_data_file":
            cache_paths[file_path] = ("xs", "freqs")
        elif key == "formant_data_file":
            cache_paths[file_path] = ("xs", "f1", "f2")

    import parselmouth
    for audio_path in sorted(audio_paths):
        parselmouth.Sound(audio_path)

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
                        "group_tags": to_list(item_data.get("group_tags") or [])
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
            for slot in global_slots:
                if slot[0] == grp_name:
                    grp_note = slot_to_canonical[slot]["group_note"]
                    grp_tags = slot_to_canonical[slot]["group_tags"]
                    break
            groups.append({
                "id": f"grp_{uuid.uuid4().hex[:8]}",
                "name": grp_name,
                "note": grp_note,
                "tags": grp_tags,
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
                "group_name": grp_name
            }
            groups_slots_map[slot] = canonical_info
            groups_ids_map[item["id"]] = canonical_info

    # 2. 规范化并对齐每个发音人的 items，执行录音转换
    for spk_id, spk_data in list(state.get("speakers", {}).items()):
        spk_name = spk_data.get("name", spk_id)
        tab_mode = spk_data.get("tab_mode", "多条独立音频")
        is_long_mode = "单条" in tab_mode

        long_audio_abs = None
        if is_long_mode:
            long_audio_rel = spk_data.get("long_audio_path")
            if long_audio_rel:
                long_audio_abs = resolve_workspace_path(long_audio_rel, workspace_dir)

        old_items = spk_data.get("items", {})
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
                    if long_audio_abs and os.path.exists(long_audio_abs) and start is not None and end is not None:
                        try:
                            sr, y = wavfile.read(long_audio_abs)
                            duration = len(y) / sr
                            if start < 0 or end > duration or start >= end:
                                raise ValueError(f"切分边界无效 [{start}, {end}], 音频总长: {duration:.2f}s")
                            
                            start_idx = int(start * sr)
                            end_idx = int(end * sr)
                            
                            # Handle multi-channel audio
                            if y.ndim > 1:
                                slice_data = y[start_idx:end_idx, :]
                            else:
                                slice_data = y[start_idx:end_idx]
                            
                            dest_dir = os.path.join(workspace_dir, "audio", spk_id)
                            os.makedirs(dest_dir, exist_ok=True)
                            dest_filename = f"{spk_id}_{canonical_id}.wav"
                            dest_path = os.path.join(dest_dir, dest_filename)
                            wavfile.write(dest_path, sr, slice_data)

                            item_data["path"] = f"audio/{spk_id}/{dest_filename}"
                            # 边界转换到单项音频本地时间轴
                            duration_local = float(end - start)
                            item_data["start"] = 0.0
                            item_data["end"] = duration_local
                            item_data["macro_start"] = 0.0
                            item_data["macro_end"] = duration_local
                            
                            # 旧 F0、共振峰及预览缓存作废
                            for cache_key in ("pitch_data_file", "formant_data_file", "preview_f0", "has_empty_data"):
                                item_data.pop(cache_key, None)
                            
                            summary["sliced_items"] += 1
                            sliced_successfully = True
                        except Exception as e:
                            warnings.append(f"发音人 {spk_name} 词项 '{word_label}' (ID: {canonical_id}) 的长音频切片失败: {e}")
                            summary["downgraded_items"] += 1

                    if not sliced_successfully:
                        # 降级为未录制
                        item_data.pop("path", None)
                        for k in ("start", "end", "macro_start", "macro_end", "pitch_data_file", "formant_data_file", "preview_f0", "has_empty_data"):
                            item_data.pop(k, None)
                else:
                    # 独立音频重命名并映射
                    old_path_rel = item_data.get("path")
                    if old_path_rel:
                        # 寻找源 WAV 文件
                        src_wav = None
                        candidates = [
                            resolve_workspace_path(old_path_rel, workspace_dir),
                            os.path.join(workspace_dir, "audio", spk_id, f"{spk_id}_{old_id}.wav"),
                            os.path.join(workspace_dir, "audio", f"{spk_id}_{old_id}.wav"),
                        ]
                        for c in candidates:
                            if os.path.isfile(c):
                                src_wav = c
                                break

                        if not src_wav:
                            suffix = f"{spk_id}_{old_id}.wav".lower()
                            for r, _, files in os.walk(os.path.join(workspace_dir, "audio")):
                                for f in files:
                                    if f.lower().endswith(suffix):
                                        src_wav = os.path.join(r, f)
                                        break
                                if src_wav:
                                    break

                        if src_wav:
                            dest_rel = f"audio/{spk_id}/{spk_id}_{canonical_id}.wav"
                            dest_abs = os.path.join(workspace_dir, dest_rel)
                            if os.path.normpath(src_wav) != os.path.normpath(dest_abs):
                                os.makedirs(os.path.dirname(dest_abs), exist_ok=True)
                                shutil.copy2(src_wav, dest_abs)
                                try:
                                    os.remove(src_wav)
                                except OSError:
                                    pass
                            item_data["path"] = dest_rel

                            # 同样重命名 pitch and formantNPZ cache files
                            for cache_key, cache_suffix in (("pitch_data_file", ".npz"), ("formant_data_file", "_formant.npz")):
                                c_path = item_data.get(cache_key)
                                if c_path:
                                    c_abs = resolve_workspace_path(c_path, workspace_dir)
                                    if os.path.isfile(c_abs):
                                        dest_cache_rel = f"data/{spk_id}_{canonical_id}{cache_suffix}"
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
                    "group": canonical_item["group_name"]
                }
                summary["missing_items"] += 1

        spk_data["items"] = new_items
        
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
