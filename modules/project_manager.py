import os
import json
import uuid
import zipfile
import shutil
import copy
import stat
import numpy as np
import threading
import traceback
import time

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

    state = json.loads(raw.decode("utf-8"))
    if not isinstance(state, dict):
        raise ValueError("工程文件损坏：project.json 顶层必须是对象")
    validate_project_version(state)
    return state, [info.filename.replace("\\", "/") for info in infos]

def to_json_serializable(val):
    if isinstance(val, dict):
        return {str(k): to_json_serializable(v) for k, v in val.items()}
    elif isinstance(val, (list, tuple, set)):
        return [to_json_serializable(v) for v in val]
    elif isinstance(val, np.integer):
        return int(val)
    elif isinstance(val, np.floating):
        return float(val)
    elif isinstance(val, np.bool_):
        return bool(val)
    elif isinstance(val, np.ndarray):
        return to_json_serializable(val.tolist())
    elif isinstance(val, (int, float, bool, str)) or val is None:
        return val
    else:
        try:
            json.dumps(val)
            return val
        except TypeError:
            return str(val)

def migrate_removed_f0_engine(state):
    """清理历史工程中已移除的 F0 引擎选项，同时保留已有分析缓存。"""
    for spk_data in state.get("speakers", {}).values():
        last_params = spk_data.get("last_params")
        if isinstance(last_params, dict):
            last_params.pop("f0_engine", None)
        for item in spk_data.get("items", {}).values():
            if isinstance(item, dict):
                item.pop("f0_engine", None)
    return state

class ProjectManager:
    def __init__(self, app):
        self.app = app
        self.workspace_dir = os.path.join(os.path.expanduser("~"), ".phon_tracer", "workspace")
        self.backup_path = os.path.join(os.path.expanduser("~"), ".phon_tracer", "auto_save_backup.teproj")
        self.config_path = os.path.join(os.path.expanduser("~"), ".phon_tracer", "config.json")
        self.auto_save_enabled = False
        self._auto_save_timer = None
        self._auto_save_generation = 0
        self._timer_lock = threading.RLock()
        self._save_lock = threading.RLock()
        self.auto_save_delay = 2.0
        self.auto_save_interval = 30.0
        
        if not os.path.exists(self.workspace_dir):
            os.makedirs(self.workspace_dir)
            
        self.load_config()
            
    def _get_data_dir(self):
        d = os.path.join(self.workspace_dir, "data")
        if not os.path.exists(d):
            os.makedirs(d)
        return d
        
    def _get_audio_dir(self):
        d = os.path.join(self.workspace_dir, "audio")
        if not os.path.exists(d):
            os.makedirs(d)
        return d

    def _show_error(self, title, message):
        root = getattr(self.app, "root", None)
        if root and hasattr(root, "after"):
            from tkinter import messagebox
            root.after(0, lambda: messagebox.showerror(title, message))
        else:
            print(f"{title}: {message}")

    def _safe_token(self, value):
        raw = str(value or uuid.uuid4())
        safe = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in raw)
        return safe[:80] or uuid.uuid4().hex

    def _workspace_relpath(self, path):
        if not path:
            return None
        try:
            abs_path = os.path.abspath(path)
            abs_workspace = os.path.abspath(self.workspace_dir)
            rel = os.path.relpath(abs_path, abs_workspace)
            if os.path.commonpath([abs_path, abs_workspace]) == abs_workspace and rel != "." and not os.path.isabs(rel):
                return rel.replace(os.sep, "/")
        except (TypeError, ValueError):
            pass
        return None

    def _copy_to_workspace(self, src_path, subdir, token, copy_cache=None):
        if not src_path:
            return src_path

        cache_key = None
        if copy_cache is not None:
            try:
                cache_key = (subdir, os.path.abspath(src_path))
                if cache_key in copy_cache:
                    return copy_cache[cache_key]
            except (TypeError, ValueError):
                cache_key = None

        existing_rel = self._workspace_relpath(src_path)
        if existing_rel and os.path.exists(src_path):
            if copy_cache is not None and cache_key is not None:
                copy_cache[cache_key] = existing_rel
            return existing_rel

        if not os.path.exists(src_path):
            raise FileNotFoundError(f"工程资源不存在：{src_path}")

        target_dir = self._get_audio_dir() if subdir == "audio" else self._get_data_dir()
        base = os.path.basename(src_path)
        dest_name = f"{self._safe_token(token)}_{base}"
        dest = os.path.join(target_dir, dest_name)
        if os.path.abspath(src_path) != os.path.abspath(dest):
            if (not os.path.exists(dest) or 
                os.path.getsize(src_path) != os.path.getsize(dest) or 
                abs(os.path.getmtime(src_path) - os.path.getmtime(dest)) > 1e-4):
                shutil.copy2(src_path, dest)
        rel_path = os.path.join(subdir, dest_name).replace(os.sep, "/")
        if copy_cache is not None and cache_key is not None:
            copy_cache[cache_key] = rel_path
        return rel_path

    def _add_managed_ref(self, refs, rel_path):
        if not rel_path:
            return
        norm = str(rel_path).replace("\\", "/")
        if norm.startswith("audio/") or norm.startswith("data/"):
            refs.add(norm)

    def _collect_project_file_refs(self, state):
        refs = set()
        for spk_data in state.get("speakers", {}).values():
            self._add_managed_ref(refs, spk_data.get("long_audio_path"))
            for path in spk_data.get("pending_batch_paths", []):
                self._add_managed_ref(refs, path)
            for item in spk_data.get("items", {}).values():
                self._add_managed_ref(refs, item.get("path"))
                self._add_managed_ref(refs, item.get("pitch_data_file"))
                self._add_managed_ref(refs, item.get("formant_data_file"))
        return refs

    def _prune_unused_workspace_files(self, referenced_files):
        for subdir in ("audio", "data"):
            root_dir = os.path.join(self.workspace_dir, subdir)
            if not os.path.isdir(root_dir):
                continue

            for root, dirs, files in os.walk(root_dir, topdown=False):
                for file in files:
                    file_path = os.path.join(root, file)
                    rel_path = os.path.relpath(file_path, self.workspace_dir).replace(os.sep, "/")
                    if rel_path not in referenced_files:
                        try:
                            os.remove(file_path)
                        except OSError as e:
                            print(f"Failed to remove unused workspace file {file_path}: {e}")

                for dir_name in dirs:
                    dir_path = os.path.join(root, dir_name)
                    try:
                        if not os.listdir(dir_path):
                            os.rmdir(dir_path)
                    except OSError:
                        pass

    def _iter_project_archive_files(self):
        project_json = os.path.join(self.workspace_dir, "project.json")
        if not os.path.exists(project_json):
            return

        yield project_json, "project.json"

        with open(project_json, "r", encoding="utf-8") as f:
            state = json.load(f)

        for rel_path in sorted(self._collect_project_file_refs(state)):
            file_path = self._resolve_project_path(rel_path)
            if not os.path.isfile(file_path):
                raise FileNotFoundError(f"工程资源不存在：{rel_path}")
            yield file_path, rel_path

    def _write_project_archive(self, zip_path):
        abs_zip_path = os.path.abspath(zip_path)
        parent_dir = os.path.dirname(abs_zip_path)
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)
        temp_zip_path = f"{abs_zip_path}.{uuid.uuid4().hex}.tmp"
        try:
            with zipfile.ZipFile(temp_zip_path, "w", zipfile.ZIP_DEFLATED) as zip_file:
                for file_path, arcname in self._iter_project_archive_files():
                    zip_file.write(file_path, arcname)
            read_project_metadata_from_archive(temp_zip_path)
            os.replace(temp_zip_path, abs_zip_path)
        finally:
            if os.path.exists(temp_zip_path):
                try:
                    os.remove(temp_zip_path)
                except OSError:
                    pass

    def _make_import_workspace(self, prefix="workspace_import"):
        parent_dir = os.path.dirname(self.workspace_dir)
        os.makedirs(parent_dir, exist_ok=True)
        base_name = f"{prefix}_{os.getpid()}_{threading.get_ident()}_{int(time.time() * 1000)}"
        for idx in range(100):
            name = base_name if idx == 0 else f"{base_name}_{idx}"
            path = os.path.join(parent_dir, name)
            try:
                os.makedirs(path)
                return path
            except FileExistsError:
                continue
        raise RuntimeError("无法创建工程导入临时目录")

    def load_config(self):
        import sys
        if not getattr(sys, 'frozen', False):
            if any(k in sys.modules for k in ('unittest', 'pytest')):
                self.auto_save_enabled = False
                return

        config_path = self.config_path
        if os.path.exists(config_path):
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    config = json.load(f)
                self.auto_save_enabled = bool(config.get("auto_save_enabled", False))
            except Exception as e:
                print(f"Failed to load config: {e}")
        else:
            self.auto_save_enabled = False

    def save_config(self):
        import sys
        if not getattr(sys, 'frozen', False):
            if any(k in sys.modules for k in ('unittest', 'pytest')):
                return

        config_path = self.config_path
        try:
            os.makedirs(os.path.dirname(config_path), exist_ok=True)
            config = {
                "auto_save_enabled": self.auto_save_enabled
            }
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"Failed to save config: {e}")

    def capture_ui_state(self):
        if self.app and getattr(self.app, "active_speaker", None) and getattr(self.app, "tabview", None):
            try:
                self.app.active_speaker.tab_mode = self.app.tabview.get()
            except Exception:
                pass
        export_rule_var = getattr(self.app, "export_numbering_rule_var", None)
        if export_rule_var is not None:
            try:
                self.app.export_numbering_rule_value = export_rule_var.get()
            except Exception:
                pass

    def _ensure_timer_state(self):
        if not hasattr(self, "_auto_save_generation"):
            self._auto_save_generation = 0
        if not hasattr(self, "_timer_lock"):
            self._timer_lock = threading.RLock()

    def trigger_auto_save(self):
        if not self.auto_save_enabled:
            return
        self._ensure_timer_state()
        with self._timer_lock:
            self._auto_save_generation += 1
            generation = self._auto_save_generation
            if self._auto_save_timer:
                self._auto_save_timer.cancel()
            self._schedule_auto_save(self.auto_save_delay, generation)

    def cancel_auto_save(self):
        self._ensure_timer_state()
        with self._timer_lock:
            self._auto_save_generation += 1
            if self._auto_save_timer:
                self._auto_save_timer.cancel()
                self._auto_save_timer = None

    def _schedule_auto_save(self, delay, generation=None):
        if not self.auto_save_enabled:
            return

        self._ensure_timer_state()
        if generation is None:
            generation = self._auto_save_generation

        def run_save():
            with self._timer_lock:
                if generation != self._auto_save_generation or not self.auto_save_enabled:
                    return
                self._auto_save_timer = None
            try:
                self.save_autosave_snapshot()
            except Exception as e:
                print(f"Auto-save failed in timer: {e}")
                traceback.print_exc()
            finally:
                with self._timer_lock:
                    if (
                        self.auto_save_enabled
                        and generation == self._auto_save_generation
                        and self._auto_save_timer is None
                    ):
                        self._schedule_auto_save(self.auto_save_interval, generation)

        self._auto_save_timer = threading.Timer(delay, run_save)
        self._auto_save_timer.daemon = True
        self._auto_save_timer.start()

    def save_autosave_snapshot(self):
        self.save_to_workspace()
        self._create_backup()

    def save_to_workspace(self):
        with self._save_lock:
            state = {
                "version": "1.0",
                "active_speaker_id": self.app.speaker_manager.active_speaker_id,
                "export_numbering_rule": getattr(self.app, "export_numbering_rule_value", "continuous"),
                "speakers": {}
            }
            
            data_dir = self._get_data_dir()
            audio_dir = self._get_audio_dir()
            copy_cache = {}
            
            for spk_id, spk in self.app.speaker_manager.speakers.items():
                spk_data = {
                    "id": spk.id,
                    "name": spk.name,
                    "last_params": spk.last_params,
                    "tab_mode": getattr(spk, 'tab_mode', "多条独立音频"),
                    "long_audio_path": getattr(spk, 'long_audio_path', None),
                    "pending_batch_paths": spk.pending_batch_paths,
                    "current_macro_segments": spk.current_macro_segments,
                    "manual_segments": spk.manual_segments,
                    "last_selected_iid": getattr(spk, "last_selected_iid", None),
                    "items": {}
                }
                
                # Copy long audio to workspace if exists
                spk_data["long_audio_path"] = self._copy_to_workspace(
                    spk_data["long_audio_path"],
                    "audio",
                    f"{spk_id}_long",
                    copy_cache
                )
                
                # Copy batch audios to workspace
                new_batch = []
                for idx, p in enumerate(spk_data["pending_batch_paths"]):
                    new_batch.append(self._copy_to_workspace(p, "audio", f"{spk_id}_batch_{idx}", copy_cache))
                spk_data["pending_batch_paths"] = new_batch
                
                for item_id, item in spk.items.items():
                    item_dict = {}
                    for k, v in item.items():
                        if k in ['snd', 'pitch', 'formant']: continue
                        if k == 'pitch_data' and v is not None:
                            # Save numpy arrays to npz
                            npz_name = f"{self._safe_token(spk_id)}_{self._safe_token(item_id)}.npz"
                            npz_path = os.path.join(data_dir, npz_name)
                            if isinstance(v, dict) and 'xs' in v and 'freqs' in v:
                                np.savez(npz_path, xs=v['xs'], freqs=v['freqs'])
                                item_dict['pitch_data_file'] = os.path.join("data", npz_name).replace(os.sep, "/")
                            else:
                                item_dict[k] = v
                        elif k == 'formant_data' and v is not None:
                            npz_name = f"{self._safe_token(spk_id)}_{self._safe_token(item_id)}_formant.npz"
                            npz_path = os.path.join(data_dir, npz_name)
                            if isinstance(v, dict) and 'xs' in v and 'f1' in v and 'f2' in v:
                                save_kwargs = {'xs': v['xs'], 'f1': v['f1'], 'f2': v['f2']}
                                if 'f3' in v:
                                    save_kwargs['f3'] = v['f3']
                                np.savez(npz_path, **save_kwargs)
                                item_dict['formant_data_file'] = os.path.join("data", npz_name).replace(os.sep, "/")
                            else:
                                item_dict[k] = v
                        elif k == 'path':
                            item_dict['path'] = self._copy_to_workspace(v, "audio", f"{spk_id}_{item_id}", copy_cache)
                        else:
                            item_dict[k] = v
                    spk_data["items"][item_id] = item_dict
                
                state["speakers"][spk_id] = spk_data
                
            project_json = os.path.join(self.workspace_dir, "project.json")
            serializable_state = migrate_removed_f0_engine(to_json_serializable(state))
            tmp_json = project_json + ".tmp"
            with open(tmp_json, "w", encoding="utf-8") as f:
                json.dump(serializable_state, f, ensure_ascii=False, indent=2)
            os.replace(tmp_json, project_json)

            # Write recovery metadata to autosave_meta.json
            meta_path = os.path.join(self.workspace_dir, "autosave_meta.json")
            meta_data = {
                "current_project_path": getattr(self.app, 'current_project_path', None)
            }
            try:
                meta_tmp = meta_path + ".tmp"
                with open(meta_tmp, "w", encoding="utf-8") as f:
                    json.dump(meta_data, f, ensure_ascii=False, indent=2)
                os.replace(meta_tmp, meta_path)
            except Exception as e:
                print(f"Failed to write autosave meta: {e}")

            self._prune_unused_workspace_files(self._collect_project_file_refs(serializable_state))
            return True

    def _create_backup(self):
        try:
            self._write_project_archive(self.backup_path)
        except Exception as e:
            print(f"Failed to create backup: {e}")

    def export_project(self, zip_path):
        try:
            # Force a save to workspace first
            self.save_to_workspace()
        except Exception as e:
            traceback.print_exc()
            self._show_error("导出失败", f"无法准备工程数据：{e}")
            return False
        
        try:
            with self._save_lock:
                self._write_project_archive(zip_path)
            
            # Delete backup after successful export
            if os.path.exists(self.backup_path):
                try:
                    os.remove(self.backup_path)
                except:
                    pass
                    
            return True
        except Exception as e:
            traceback.print_exc()
            self._show_error("导出失败", str(e))
            return False

    def _normalize_managed_ref(self, path, field_name):
        if not path:
            return None
        normalized = str(path).replace("\\", "/")
        parts = normalized.split("/")
        if (
            len(parts) >= 2
            and parts[0] in ("audio", "data")
            and all(part not in ("", ".", "..") for part in parts)
        ):
            return normalized
        raise ValueError(f"工程文件损坏：{field_name} 不是工程内资源路径")

    def _iter_state_resource_refs(self, state):
        for spk_data in state.get("speakers", {}).values():
            long_audio_path = spk_data.get("long_audio_path")
            if long_audio_path:
                yield spk_data, "long_audio_path", None, self._normalize_managed_ref(long_audio_path, "long_audio_path")
            for idx, path in enumerate(spk_data.get("pending_batch_paths", [])):
                yield spk_data, "pending_batch_paths", idx, self._normalize_managed_ref(path, "pending_batch_paths")
            for item in spk_data.get("items", {}).values():
                for key in ("path", "pitch_data_file", "formant_data_file"):
                    path = item.get(key)
                    if path:
                        yield item, key, None, self._normalize_managed_ref(path, key)

    def _validate_project_resources(self, state, workspace_dir):
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
        for _owner, key, _index, rel_path in self._iter_state_resource_refs(state):
            file_path = self._resolve_project_path(rel_path, workspace_dir=workspace_dir)
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

    def _copy_overlay_resources(self, state, import_workspace, merged_workspace):
        remapped_state = copy.deepcopy(state)
        ref_mapping = {}
        namespace = f"import_{uuid.uuid4().hex[:12]}"

        for owner, key, index, rel_path in self._iter_state_resource_refs(remapped_state):
            mapped_path = ref_mapping.get(rel_path)
            if mapped_path is None:
                subdir, file_name = rel_path.split("/", 1)
                mapped_path = f"{subdir}/{namespace}_{uuid.uuid4().hex[:8]}_{os.path.basename(file_name)}"
                source_path = self._resolve_project_path(rel_path, workspace_dir=import_workspace)
                target_path = self._resolve_project_path(mapped_path, workspace_dir=merged_workspace)
                os.makedirs(os.path.dirname(target_path), exist_ok=True)
                shutil.copy2(source_path, target_path)
                ref_mapping[rel_path] = mapped_path

            if index is None:
                owner[key] = mapped_path
            else:
                owner[key][index] = mapped_path

        return remapped_state

    def _swap_workspace(self, staged_workspace):
        backup_workspace = None
        if os.path.exists(self.workspace_dir):
            backup_workspace = self._make_import_workspace(prefix="workspace_backup")
            os.rmdir(backup_workspace)
            os.replace(self.workspace_dir, backup_workspace)
        try:
            os.replace(staged_workspace, self.workspace_dir)
        except Exception:
            if backup_workspace and os.path.exists(backup_workspace):
                os.replace(backup_workspace, self.workspace_dir)
            raise
        return backup_workspace

    def _rollback_workspace(self, backup_workspace):
        if os.path.exists(self.workspace_dir):
            shutil.rmtree(self.workspace_dir, ignore_errors=True)
        if backup_workspace and os.path.exists(backup_workspace):
            os.replace(backup_workspace, self.workspace_dir)
        else:
            os.makedirs(self.workspace_dir, exist_ok=True)

    def _discard_workspace_backup(self, backup_workspace):
        if backup_workspace and os.path.exists(backup_workspace):
            shutil.rmtree(backup_workspace, ignore_errors=True)

    def _remap_restored_workspace_paths(self, restored, old_workspace, new_workspace):
        old_workspace = os.path.abspath(old_workspace)
        new_workspace = os.path.abspath(new_workspace)

        def remap(path):
            if not path:
                return path
            abs_path = os.path.abspath(path)
            rel_path = os.path.relpath(abs_path, old_workspace)
            if (
                os.path.commonpath([abs_path, old_workspace]) == old_workspace
                and not os.path.isabs(rel_path)
            ):
                return os.path.join(new_workspace, rel_path)
            return path

        for spk in restored.values():
            spk.long_audio_path = remap(getattr(spk, "long_audio_path", None))
            spk.pending_batch_paths = [remap(path) for path in spk.pending_batch_paths]
            for item in spk.items.values():
                if item.get("path"):
                    item["path"] = remap(item["path"])

    def load_project(self, zip_path, overlay=False):
        temp_workspace = None
        staged_workspace = None
        backup_workspace = None
        old_speakers = None
        old_active_speaker_id = None
        old_export_numbering_rule_value = None
        had_export_numbering_rule_value = False
        try:
            state, _namelist = read_project_metadata_from_archive(zip_path)
            temp_workspace = self._make_import_workspace()
            with zipfile.ZipFile(zip_path, 'r') as zf:
                self._safe_extract(zf, temp_workspace)
                
            project_json = os.path.join(temp_workspace, "project.json")
            if not os.path.exists(project_json):
                raise ValueError("工程文件损坏：未找到 project.json")
                
            self._validate_project_resources(state, temp_workspace)

            with self._save_lock:
                old_speakers = dict(self.app.speaker_manager.speakers)
                old_active_speaker_id = self.app.speaker_manager.active_speaker_id
                had_export_numbering_rule_value = hasattr(self.app, "export_numbering_rule_value")
                old_export_numbering_rule_value = getattr(self.app, "export_numbering_rule_value", None)

                if not overlay:
                    restored, active_id = self._build_restored_state(
                        state,
                        overlay=False,
                        workspace_dir=temp_workspace
                    )
                    staged_workspace = temp_workspace
                    temp_workspace = None
                    old_staged_workspace = staged_workspace
                    backup_workspace = self._swap_workspace(staged_workspace)
                    staged_workspace = None
                    self._remap_restored_workspace_paths(restored, old_staged_workspace, self.workspace_dir)
                    self._apply_restored_state(restored, active_id, overlay=False)
                    self._restore_app_state(state)
                    self._prune_unused_workspace_files(self._collect_project_file_refs(state))
                else:
                    staged_workspace = self._make_import_workspace(prefix="workspace_overlay")
                    if os.path.exists(self.workspace_dir):
                        shutil.copytree(self.workspace_dir, staged_workspace, dirs_exist_ok=True)
                    remapped_state = self._copy_overlay_resources(state, temp_workspace, staged_workspace)
                    restored, active_id = self._build_restored_state(
                        remapped_state,
                        overlay=True,
                        workspace_dir=staged_workspace
                    )
                    old_staged_workspace = staged_workspace
                    backup_workspace = self._swap_workspace(staged_workspace)
                    staged_workspace = None
                    self._remap_restored_workspace_paths(restored, old_staged_workspace, self.workspace_dir)
                    self._apply_restored_state(restored, active_id, overlay=True)
                    self.save_to_workspace()

                self._discard_workspace_backup(backup_workspace)
                backup_workspace = None
            
            return True
        except Exception as e:
            if backup_workspace is not None:
                self._rollback_workspace(backup_workspace)
            if old_speakers is not None:
                self.app.speaker_manager.speakers.clear()
                self.app.speaker_manager.speakers.update(old_speakers)
                self.app.speaker_manager.active_speaker_id = old_active_speaker_id
            if had_export_numbering_rule_value:
                self.app.export_numbering_rule_value = old_export_numbering_rule_value
            traceback.print_exc()
            self._show_error("导入失败", str(e))
            return False
        finally:
            if temp_workspace and os.path.exists(temp_workspace):
                shutil.rmtree(temp_workspace, ignore_errors=True)
            if staged_workspace and os.path.exists(staged_workspace):
                shutil.rmtree(staged_workspace, ignore_errors=True)

    def load_from_workspace(self):
        try:
            project_json = os.path.join(self.workspace_dir, "project.json")
            if not os.path.exists(project_json):
                raise ValueError("未找到 project.json")
            with open(project_json, "r", encoding="utf-8") as f:
                state = json.load(f)
            with self._save_lock:
                self._validate_project_resources(state, self.workspace_dir)
                self._restore_state(state, overlay=False)
                self._restore_app_state(state)
                # Restore the project path if autosave_meta.json exists
                meta_path = os.path.join(self.workspace_dir, "autosave_meta.json")
                if os.path.exists(meta_path):
                    try:
                        with open(meta_path, "r", encoding="utf-8") as f:
                            meta_data = json.load(f)
                        if hasattr(self.app, 'current_project_path'):
                            self.app.current_project_path = meta_data.get("current_project_path")
                    except Exception as e:
                        print(f"Failed to read autosave meta: {e}")
            return True
        except Exception as e:
            traceback.print_exc()
            self._show_error("恢复自动保存失败", str(e))
            return False

    def _safe_extract(self, zip_file, target_dir):
        target_dir_abs = os.path.abspath(target_dir)
        infos = validate_project_archive_members(zip_file)
        for member in infos:
            normalized = member.filename.replace("\\", "/")
            member_path = os.path.abspath(os.path.join(target_dir_abs, *normalized.split("/")))
            if os.path.commonpath([member_path, target_dir_abs]) != target_dir_abs:
                raise ValueError("工程文件包含非法路径")
            if member.is_dir():
                os.makedirs(member_path, exist_ok=True)
                continue
            os.makedirs(os.path.dirname(member_path), exist_ok=True)
            with zip_file.open(member) as source, open(member_path, "wb") as target:
                shutil.copyfileobj(source, target)

    def _resolve_project_path(self, path, workspace_dir=None):
        if not path:
            return path
        norm = str(path).replace("\\", "/")
        if norm == "audio" or norm.startswith("audio/") or norm == "data" or norm.startswith("data/"):
            return os.path.join(workspace_dir or self.workspace_dir, *norm.split("/"))
        return path

    def _restore_state(self, state, overlay=False, workspace_dir=None):
        restored, active_id = self._build_restored_state(
            state,
            overlay=overlay,
            workspace_dir=workspace_dir or self.workspace_dir
        )
        self._apply_restored_state(restored, active_id, overlay=overlay)

    def _build_restored_state(self, state, overlay=False, workspace_dir=None):
        import parselmouth
        from .speaker_manager import SpeakerState

        state = migrate_removed_f0_engine(copy.deepcopy(state))
        workspace_dir = workspace_dir or self.workspace_dir
        restored = {}
        id_mapping = {}
        
        current_speakers = self.app.speaker_manager.speakers
        existing_names = {s.name for s in current_speakers.values()} if overlay else set()

        for old_spk_id, spk_data in state.get("speakers", {}).items():
            name = spk_data.get("name", "发音人")
            if overlay:
                if name in existing_names:
                    base_name = name
                    counter = 2
                    while f"{base_name}_{counter}" in existing_names:
                        counter += 1
                    name = f"{base_name}_{counter}"
                existing_names.add(name)

            spk = SpeakerState(name)
            
            orig_id = spk_data.get("id", old_spk_id)
            if overlay and orig_id in current_speakers:
                spk.id = str(uuid.uuid4())
            else:
                spk.id = orig_id
                
            id_mapping[orig_id] = spk.id
            
            spk.last_params = spk_data.get("last_params", spk.last_params)
            spk.tab_mode = spk_data.get("tab_mode", "多条独立音频")
            if "单条" in spk.tab_mode:
                spk.tab_mode = "单条长音频"
            elif "独立" in spk.tab_mode or "多条" in spk.tab_mode:
                spk.tab_mode = "多条独立音频"

            long_audio_rel = spk_data.get("long_audio_path")
            if long_audio_rel:
                spk.long_audio_path = self._resolve_project_path(long_audio_rel, workspace_dir=workspace_dir)
                if os.path.exists(spk.long_audio_path):
                    spk.pending_long_snd = parselmouth.Sound(spk.long_audio_path)

            if spk.pending_long_snd:
                has_independent_paths = False
                for item_data in spk_data.get("items", {}).values():
                    if item_data.get('path'):
                        has_independent_paths = True
                        break
                if not has_independent_paths:
                    spk.tab_mode = "单条长音频"

            spk.pending_batch_paths = [
                self._resolve_project_path(p, workspace_dir=workspace_dir)
                for p in spk_data.get("pending_batch_paths", [])
            ]
            spk.current_macro_segments = spk_data.get("current_macro_segments", [])
            spk.manual_segments = spk_data.get("manual_segments", None)
            last_selected_iid = spk_data.get("last_selected_iid")
            if last_selected_iid in spk_data.get("items", {}):
                spk.last_selected_iid = last_selected_iid

            for item_id, item_data in spk_data.get("items", {}).items():
                item = dict(item_data)
                if 'path' in item:
                    item['path'] = self._resolve_project_path(item['path'], workspace_dir=workspace_dir)

                if spk.tab_mode == "多条独立音频" and item.get('path') and os.path.exists(item['path']):
                    item['snd'] = parselmouth.Sound(item['path'])
                elif spk.tab_mode == "单条长音频" and spk.pending_long_snd:
                    item['snd'] = spk.pending_long_snd

                if 'pitch_data_file' in item:
                    npz_path = self._resolve_project_path(item['pitch_data_file'], workspace_dir=workspace_dir)
                    if os.path.exists(npz_path):
                        with np.load(npz_path) as loaded:
                            item['pitch_data'] = {'xs': loaded['xs'].copy(), 'freqs': loaded['freqs'].copy()}
                    del item['pitch_data_file']

                if 'formant_data_file' in item:
                    npz_path = self._resolve_project_path(item['formant_data_file'], workspace_dir=workspace_dir)
                    if os.path.exists(npz_path):
                        with np.load(npz_path) as loaded:
                            formant_dict = {
                                'xs': loaded['xs'].copy(),
                                'f1': loaded['f1'].copy(),
                                'f2': loaded['f2'].copy()
                            }
                            if 'f3' in loaded:
                                formant_dict['f3'] = loaded['f3'].copy()
                            item['formant_data'] = formant_dict
                    del item['formant_data_file']

                spk.items[item_id] = item

            restored[spk.id] = spk

        if not overlay:
            active_id = state.get("active_speaker_id")
            if active_id not in restored:
                active_id = next(iter(restored)) if restored else None
        else:
            imported_active_id = state.get("active_speaker_id")
            active_id = id_mapping.get(imported_active_id)
            if not active_id and restored:
                active_id = next(iter(restored))

        return restored, active_id

    def _apply_restored_state(self, restored, active_id, overlay=False):
        if not overlay:
            self.app.speaker_manager.speakers.clear()
            self.app.speaker_manager.speakers.update(restored)
            self.app.speaker_manager.active_speaker_id = active_id
        else:
            self.app.speaker_manager.speakers.update(restored)
            if active_id:
                self.app.speaker_manager.active_speaker_id = active_id

    def _restore_app_state(self, state):
        if hasattr(self.app, "export_numbering_rule_value"):
            self.app.export_numbering_rule_value = state.get("export_numbering_rule", "continuous")
