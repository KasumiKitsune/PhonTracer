import os
import json
import uuid
import zipfile
import shutil
import copy
import stat
import math
import hashlib
import logging
import numpy as np
try:
    import parselmouth
except ImportError:
    from modules import _parselmouth_fallback as parselmouth
    import sys as _sys
    _sys.modules.setdefault('parselmouth', parselmouth)
    _sys.modules.setdefault('parselmouth.praat', parselmouth.praat)
import threading
import traceback
import time
from .version import __version__

logger = logging.getLogger(__name__)

from .project_adaptor import (
    MAX_ARCHIVE_MEMBERS,
    MAX_ARCHIVE_MEMBER_BYTES,
    MAX_ARCHIVE_TOTAL_BYTES,
    MAX_SINGLE_MEMBER_BYTES,
    MAX_TOTAL_UNCOMPRESSED_BYTES,
    MAX_PROJECT_JSON_BYTES,
    SUPPORTED_PROJECT_VERSIONS,
    validate_project_version,
    validate_project_archive_members,
    read_project_metadata_from_archive,
    safe_extract_zip,
    resolve_workspace_path,
    prune_unreferenced_resources,
    validate_project_resources,
    normalize_independent_item_boundaries,
    _iter_state_resource_refs
)

def to_json_serializable(val):
    if isinstance(val, dict):
        return {str(k): to_json_serializable(v) for k, v in val.items()}
    elif isinstance(val, (list, tuple, set)):
        return [to_json_serializable(v) for v in val]
    elif isinstance(val, np.integer):
        return int(val)
    elif isinstance(val, (float, np.floating)):
        fval = float(val)
        if math.isnan(fval) or math.isinf(fval):
            return None
        return fval
    elif isinstance(val, (bool, np.bool_)):
        return bool(val)
    elif isinstance(val, np.ndarray):
        return to_json_serializable(val.tolist())
    elif isinstance(val, (int, str)) or val is None:
        return val
    else:
        try:
            json.dumps(val)
            return val
        except TypeError:
            return str(val)

RUNTIME_EXCLUDED_KEYS = {
    'snd', 'pitch', 'formant',
    'preview_f0', 'preview_formants',
    'warnings', 'has_empty_data', 'preview_segment_mismatch',
    '_mode_state_f0', '_mode_state_formant',
    '_dirty_pitch', '_dirty_formant',
}

ACOUSTIC_PARAM_KEYS = {
    'pitch_floor', 'pitch_ceiling', 'voicing_threshold',
    'formant_max_hz', 'formant_count', 'formant_window_length',
    'formant_pre_emphasis', 'formant_sample_strategy',
    'show_f3', 'analysis_mode'
}

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
    @staticmethod
    def _is_pid_alive(pid):
        """只读探测目标 PID 是否存活。
        注意：在 Windows 下绝对禁止调用 os.kill(pid, 0)，因为 0 即 CTRL_C_EVENT，
        Windows CPython 的 os.kill 会向控制台广播信号或调用 TerminateProcess 强杀进程！
        """
        try:
            pid = int(pid)
        except (TypeError, ValueError):
            return False
        if pid <= 0:
            return False
        if os.name == "nt":
            try:
                import ctypes
                PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
                ERROR_ACCESS_DENIED = 5
                STILL_ACTIVE = 259
                kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
                handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
                if not handle or handle == 0:
                    return ctypes.get_last_error() == ERROR_ACCESS_DENIED
                try:
                    exit_code = ctypes.c_ulong()
                    if kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                        return exit_code.value == STILL_ACTIVE
                    return False
                finally:
                    kernel32.CloseHandle(handle)
            except Exception:
                return False
        else:
            try:
                os.kill(pid, 0)
                return True
            except PermissionError:
                return True
            except (OSError, ProcessLookupError):
                return False

    _pid_exists = _is_pid_alive

    def allocate_isolated_workspace(self):
        """当默认工作区被其他存活实例占用时，分配独立工作区目录（workspace_{pid}）并获取锁。"""
        self._default_workspace_contended = True
        new_ws = os.path.join(self.base_dir, f"workspace_{os.getpid()}")
        self._fallback_workspace_dir = new_ws
        self.workspace_dir = new_ws
        self.backup_path = os.path.join(self.workspace_dir, "auto_save_backup.teproj")
        os.makedirs(self.workspace_dir, exist_ok=True)
        self._lock_acquired = self.acquire_workspace_lock()
        self.auto_save_enabled = False
        return self.workspace_dir

    def acquire_workspace_lock(self):
        """尝试获取工作区锁文件。若被其他存活进程或实例占用则返回 False，否则写入当前 PID 与实例 ID 并返回 True。"""
        if not os.path.exists(self.workspace_dir):
            try:
                os.makedirs(self.workspace_dir, exist_ok=True)
            except Exception:
                pass
        lock_path = os.path.join(self.workspace_dir, "workspace.lock")
        if os.path.exists(lock_path):
            try:
                with open(lock_path, "r", encoding="utf-8") as f:
                    info = json.load(f)
                existing_pid = info.get("pid")
                existing_inst = info.get("instance_id")
                current_inst = getattr(self, "_instance_id", None)
                if existing_pid:
                    if existing_pid != os.getpid():
                        if self._is_pid_alive(existing_pid):
                            return False
                    else:
                        if existing_inst and current_inst and existing_inst != current_inst:
                            return False
            except Exception:
                pass
        try:
            with open(lock_path, "w", encoding="utf-8") as f:
                json.dump({
                    "pid": os.getpid(),
                    "instance_id": getattr(self, "_instance_id", None),
                    "timestamp": time.time(),
                    "time_str": time.strftime("%Y-%m-%d %H:%M:%S")
                }, f)
            self._lock_acquired = True
            return True
        except Exception:
            return False

    def release_workspace_lock(self):
        """释放当前进程与实例的工作区锁，并清理回退隔离工作区（若有）。"""
        lock_path = os.path.join(self.workspace_dir, "workspace.lock")
        if os.path.exists(lock_path):
            try:
                with open(lock_path, "r", encoding="utf-8") as f:
                    info = json.load(f)
                current_inst = getattr(self, "_instance_id", None)
                if info.get("pid") == os.getpid() and (not current_inst or not info.get("instance_id") or info.get("instance_id") == current_inst):
                    os.remove(lock_path)
            except Exception:
                pass
        fallback_dir = getattr(self, "_fallback_workspace_dir", None)
        if fallback_dir and os.path.exists(fallback_dir):
            try:
                shutil.rmtree(fallback_dir, ignore_errors=True)
            except Exception:
                pass
            self._fallback_workspace_dir = None

    def __init__(self, app, workspace_dir=None):
        import atexit
        self.app = app
        self._instance_id = uuid.uuid4().hex
        self._fallback_workspace_dir = None
        if workspace_dir:
            self.workspace_dir = os.path.abspath(workspace_dir)
            self.base_dir = os.path.dirname(self.workspace_dir)
            self.backup_path = os.path.join(self.workspace_dir, "auto_save_backup.teproj")
            self.is_custom_workspace = True
        else:
            self.base_dir = os.path.join(os.path.expanduser("~"), ".phon_tracer")
            self.workspace_dir = os.path.join(self.base_dir, "workspace")
            self.backup_path = os.path.join(self.base_dir, "auto_save_backup.teproj")
            self.is_custom_workspace = False

        self.config_path = os.path.join(os.path.expanduser("~"), ".phon_tracer", "config.json")
        self.auto_save_enabled = False
        self._auto_save_timer = None
        self._auto_save_generation = 0
        self._timer_lock = threading.RLock()
        self._save_lock = threading.RLock()
        self.auto_save_delay = 2.0
        self.auto_save_interval = 30.0

        if not os.path.exists(self.workspace_dir):
            try:
                os.makedirs(self.workspace_dir, exist_ok=True)
            except Exception:
                pass

        self._lock_acquired = self.acquire_workspace_lock()
        self._default_workspace_contended = False
        if not self._lock_acquired and not self.is_custom_workspace:
            self.allocate_isolated_workspace()
            print(
                f"Warning: Default workspace is locked by another running process. "
                f"Switched to isolated workspace {self.workspace_dir} and disabled auto-save."
            )

        atexit.register(self.release_workspace_lock)
        self.load_config()
        if not self._lock_acquired or self._default_workspace_contended:
            self.auto_save_enabled = False

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
        if len(safe) > 80:
            sha_suffix = hashlib.sha1(str(value).encode('utf-8')).hexdigest()[:12]
            return f"{safe[:64]}_{sha_suffix}"
        return safe or uuid.uuid4().hex

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

    def _find_relocated_workspace_resource(self, src_path, subdir):
        if not self._workspace_relpath(src_path):
            return None

        target_dir = os.path.join(self.workspace_dir, subdir)
        if not os.path.isdir(target_dir):
            return None

        base_name = os.path.basename(src_path)
        candidates = []
        for name in os.listdir(target_dir):
            path = os.path.join(target_dir, name)
            if os.path.isfile(path) and (name == base_name or name.endswith(f"_{base_name}")):
                candidates.append(path)

        if len(candidates) == 1:
            return candidates[0]
        if len(candidates) > 1:
            raise FileNotFoundError(f"工程资源路径失效且存在多个候选资源，无法安全恢复：{src_path}")
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
            relocated_path = self._find_relocated_workspace_resource(src_path, subdir)
            if relocated_path:
                relocated_rel = self._workspace_relpath(relocated_path)
                if copy_cache is not None and cache_key is not None:
                    copy_cache[cache_key] = relocated_rel
                return relocated_rel
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


    def _collect_project_file_refs(self, state):
        refs = set()
        for _owner, _key, _index, rel_path in _iter_state_resource_refs(state):
            if rel_path:
                norm = str(rel_path).replace("\\", "/")
                if norm.startswith("audio/") or norm.startswith("data/"):
                    refs.add(norm)
        for spk in state.get("speakers", {}).values():
            if isinstance(spk, dict) and spk.get("long_pitch_data_file"):
                norm = str(spk["long_pitch_data_file"]).replace("\\", "/")
                if norm.startswith("data/"):
                    refs.add(norm)
        return refs

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

    def _sync_speakers_to_groups(self, state, speakers_dict):
        groups = state.get("groups")
        if not isinstance(groups, list):
            return
        items_by_id = {}
        items_by_slot = {}
        groups_by_name = {}
        for group in groups:
            gname = group.get("name") or group.get("group") or "默认组"
            groups_by_name[gname] = group
            for g_item in group.get("items", []):
                if g_item.get("id"):
                    items_by_id[g_item["id"]] = g_item
                label = g_item.get("label") or g_item.get("word")
                if label:
                    items_by_slot[(gname, label)] = g_item

        for spk_data in speakers_dict.values():
            for item_id, item in spk_data.get("items", {}).items():
                g_item = items_by_id.get(item_id)
                gname = item.get("group") or item.get("group_name") or "默认组"
                label = item.get("label") or item.get("word")
                if not g_item and label:
                    g_item = items_by_slot.get((gname, label))
                if g_item:
                    for k, default_empty in (("note", ""), ("tags", []), ("aliases", []), ("meta", {})):
                        g_item[k] = copy.deepcopy(item.get(k, default_empty))
                    if "label" in item and item["label"]:
                        g_item["label"] = item["label"]
                        if "word" in g_item:
                            g_item["word"] = item["label"]
                else:
                    target_grp = groups_by_name.get(gname)
                    if not target_grp:
                        target_grp = {
                            "id": f"grp_{uuid.uuid4().hex[:8]}",
                            "name": gname,
                            "note": "",
                            "tags": [],
                            "meta": {},
                            "items": []
                        }
                        groups.append(target_grp)
                        groups_by_name[gname] = target_grp
                    new_g_item = {
                        "id": item_id or f"slot_{len(target_grp.get('items', []))}",
                        "label": label or "",
                        "note": copy.deepcopy(item.get("note", "")),
                        "tags": copy.deepcopy(item.get("tags", [])),
                        "aliases": copy.deepcopy(item.get("aliases", [])),
                        "meta": copy.deepcopy(item.get("meta", {}))
                    }
                    target_grp.setdefault("items", []).append(new_g_item)
                    items_by_id[new_g_item["id"]] = new_g_item
                    if label:
                        items_by_slot[(gname, label)] = new_g_item

        # Ensure any groups defined on the UI tree are present in groups and preserve UI order
        tree_groups = getattr(self.app, 'tree_panel', None) and getattr(self.app.tree_panel, 'project_groups', None)
        if tree_groups:
            for gname in tree_groups:
                if gname and gname not in groups_by_name:
                    new_grp = {
                        "id": f"grp_{uuid.uuid4().hex[:8]}",
                        "name": gname,
                        "note": "",
                        "tags": [],
                        "meta": {},
                        "items": []
                    }
                    groups.append(new_grp)
                    groups_by_name[gname] = new_grp
            order_map = {name: i for i, name in enumerate(tree_groups)}
            groups.sort(key=lambda g: order_map.get(g.get("name") or g.get("group"), 9999))

    def _prune_workspace_resources(self, state, workspace_dir):
        patched_state = copy.deepcopy(state)
        for spk_id, spk_data in patched_state.get("speakers", {}).items():
            if isinstance(spk_data, dict) and spk_data.get("long_pitch_data_file"):
                items = spk_data.setdefault("items", {})
                dummy_key = f"__preserve_long_pitch_{spk_id}__"
                items[dummy_key] = {"pitch_data_file": spk_data["long_pitch_data_file"]}
        prune_unreferenced_resources(patched_state, workspace_dir)

    def _trigger_async_backup(self):
        if not hasattr(self, "_backup_thread_lock"):
            self._backup_thread_lock = threading.Lock()
        if not hasattr(self, "_backup_in_progress"):
            self._backup_in_progress = False

        with self._backup_thread_lock:
            if self._backup_in_progress:
                return  # 节流：已有后台备份线程在执行，避免并发重复压缩
            self._backup_in_progress = True

        def backup_worker():
            try:
                with self._save_lock:
                    self._create_backup()
            except Exception as e:
                logger.warning(f"后台自动保存备份失败: {e}")
            finally:
                with self._backup_thread_lock:
                    self._backup_in_progress = False

        t = threading.Thread(target=backup_worker, daemon=True)
        t.start()

    def save_autosave_snapshot(self):
        self.save_to_workspace(incremental=True)
        self._trigger_async_backup()

    def save_to_workspace(self, incremental=False):
        with self._save_lock:
            preserved = getattr(self, "_preserved_top_level", {}) or getattr(self.app, "_preserved_top_level", {})
            state = copy.deepcopy(preserved) if preserved else {}
            state["version"] = state.get("version", "1.0")
            state["software_version"] = __version__
            state["report_format_version"] = state.get("report_format_version", "1.0")
            state["save_time"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
            state["active_speaker_id"] = self.app.speaker_manager.active_speaker_id
            state["export_numbering_rule"] = getattr(self.app, "export_numbering_rule_value", "continuous")
            state["trim_silence"] = self.app.switch_trim_silence.get() if hasattr(self.app, "switch_trim_silence") else True
            state["speakers"] = {}
            state["custom_script_runs"] = getattr(self.app, "custom_script_runs", [])

            data_dir = self._get_data_dir()
            audio_dir = self._get_audio_dir()
            copy_cache = {}
            runtime_attr_updates = []
            runtime_item_path_updates = []

            for spk_id, spk in self.app.speaker_manager.speakers.items():
                tree_groups = getattr(self.app, 'tree_panel', None) and getattr(self.app.tree_panel, 'project_groups', None)
                spk_groups = getattr(spk, 'project_groups', None)
                if spk_id == self.app.speaker_manager.active_speaker_id and tree_groups:
                    spk_project_groups = list(tree_groups)
                elif spk_groups and isinstance(spk_groups, list):
                    spk_project_groups = list(spk_groups)
                else:
                    seen = set()
                    spk_project_groups = []
                    for it in spk.items.values():
                        g = it.get('group', '导入内容')
                        if g not in seen:
                            seen.add(g)
                            spk_project_groups.append(g)

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
                    "project_groups": spk_project_groups,
                    "items": {}
                }
                spk_wl_doc = getattr(spk, "wordlist_doc", None)
                if isinstance(spk_wl_doc, dict):
                    spk_data["wordlist_doc"] = copy.deepcopy(spk_wl_doc)
                existing_long_pitch = getattr(spk, 'long_pitch_data_file', None)
                if existing_long_pitch:
                    spk_data["long_pitch_data_file"] = existing_long_pitch
                    runtime_attr_updates.append((spk, "long_pitch_data_file", existing_long_pitch))

                # Copy long audio to workspace if exists
                spk_data["long_audio_path"] = self._copy_to_workspace(
                    spk_data["long_audio_path"],
                    "audio",
                    f"{spk_id}_long",
                    copy_cache
                )
                runtime_attr_updates.append((
                    spk,
                    "long_audio_path",
                    self._resolve_project_path(spk_data["long_audio_path"])
                ))

                # Copy batch audios to workspace
                new_batch = []
                for idx, p in enumerate(spk_data["pending_batch_paths"]):
                    new_batch.append(self._copy_to_workspace(p, "audio", f"{spk_id}_batch_{idx}", copy_cache))
                spk_data["pending_batch_paths"] = new_batch
                runtime_attr_updates.append((
                    spk,
                    "pending_batch_paths",
                    [self._resolve_project_path(path) for path in new_batch]
                ))

                long_pitch_saved = bool(spk_data.get("long_pitch_data_file"))
                for item_id, item in list(spk.items.items()):
                    item_dict = {}
                    overrides = copy.deepcopy(item.get('param_overrides', {})) if isinstance(item.get('param_overrides'), dict) else {}

                    for k, v in list(item.items()):
                        if k in RUNTIME_EXCLUDED_KEYS:
                            continue
                        if k in ('item_note', 'item_tags', 'item_aliases', 'item_meta'):
                            canonical_k = k[5:]
                            if v:
                                item_dict[canonical_k] = v
                            continue
                        if k in ('note', 'tags', 'aliases', 'meta'):
                            item_key = 'item_' + k
                            if item_key in item:
                                if item[item_key]:
                                    item_dict[k] = item[item_key]
                                continue
                        if k in ACOUSTIC_PARAM_KEYS:
                            spk_default = spk.last_params.get(k)
                            if spk_default is not None and v != spk_default:
                                overrides[k] = v
                            continue
                        if k == 'param_overrides':
                            continue
                        if k == 'pitch_data' and v is not None:
                            macro_s = item.get('macro_start')
                            macro_e = item.get('macro_end')
                            if isinstance(v, dict) and 'xs' in v and 'freqs' in v:
                                xs = v['xs']
                                freqs = v['freqs']
                                if macro_s is not None and macro_e is not None and len(xs) > 0:
                                    win_s = max(0.0, float(macro_s) - 0.05)
                                    win_e = float(macro_e) + 0.05
                                    if (xs[-1] - xs[0]) > (win_e - win_s + 2.0) and (xs[-1] - xs[0]) > 10.0:
                                        if not long_pitch_saved:
                                            long_npz_name = f"{self._safe_token(spk_id)}_long_pitch.npz"
                                            long_npz_path = os.path.join(data_dir, long_npz_name)
                                            if not incremental or not os.path.exists(long_npz_path):
                                                np.savez_compressed(long_npz_path, xs=xs, freqs=freqs)
                                            rel_long_pitch = os.path.join("data", long_npz_name).replace(os.sep, "/")
                                            spk_data['long_pitch_data_file'] = rel_long_pitch
                                            long_pitch_saved = True
                                            runtime_attr_updates.append((
                                                spk,
                                                "long_pitch_data_file",
                                                rel_long_pitch
                                            ))
                                        mask = (xs >= win_s) & (xs <= win_e)
                                        xs = xs[mask]
                                        freqs = freqs[mask]
                                        v = {'xs': xs, 'freqs': freqs}
                                        item['pitch_data'] = v

                                npz_name = f"{self._safe_token(spk_id)}_{self._safe_token(item_id)}.npz"
                                npz_path = os.path.join(data_dir, npz_name)
                                should_write = (
                                    not incremental
                                    or item.get('_dirty_pitch', False)
                                    or not os.path.exists(npz_path)
                                )
                                if should_write:
                                    np.savez_compressed(npz_path, xs=xs, freqs=freqs)
                                    item['_dirty_pitch'] = False
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
                                should_write = (
                                    not incremental
                                    or item.get('_dirty_formant', False)
                                    or not os.path.exists(npz_path)
                                )
                                if should_write:
                                    np.savez_compressed(npz_path, **save_kwargs)
                                    item['_dirty_formant'] = False
                                item_dict['formant_data_file'] = os.path.join("data", npz_name).replace(os.sep, "/")
                            else:
                                item_dict[k] = v
                        elif k == 'path':
                            item_dict['path'] = self._copy_to_workspace(v, "audio", f"{spk_id}_{item_id}", copy_cache)
                            runtime_item_path_updates.append((
                                item,
                                'path',
                                self._resolve_project_path(item_dict['path'])
                            ))
                        else:
                            item_dict[k] = v

                    overrides = {pk: pv for pk, pv in overrides.items() if pk in ACOUSTIC_PARAM_KEYS and pv != spk.last_params.get(pk)}
                    if overrides:
                        item_dict['param_overrides'] = overrides
                    spk_data["items"][item_id] = item_dict

                state["speakers"][spk_id] = spk_data

            if "groups" in state and isinstance(state["groups"], list):
                self._sync_speakers_to_groups(state, state["speakers"])

            project_json = os.path.join(self.workspace_dir, "project.json")
            serializable_state = migrate_removed_f0_engine(to_json_serializable(state))
            tmp_json = project_json + ".tmp"
            with open(tmp_json, "w", encoding="utf-8") as f:
                json.dump(serializable_state, f, ensure_ascii=False, indent=2, allow_nan=False)
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

            self._prune_workspace_resources(serializable_state, self.workspace_dir)
            for obj, attr_name, value in runtime_attr_updates:
                setattr(obj, attr_name, value)
            for item, key, value in runtime_item_path_updates:
                item[key] = value
            return True

    def _create_backup(self):
        try:
            self._write_project_archive(self.backup_path)
        except Exception as e:
            print(f"Failed to create backup: {e}")

    def export_project(self, zip_path):
        if not self.app.speaker_manager.speakers:
            self._show_error("导出失败", "未添加发音人，禁止导出兼容的工程归档")
            return False
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


    def _validate_project_resources(self, state, workspace_dir):
        validate_project_resources(state, workspace_dir)

    def _copy_overlay_resources(self, state, import_workspace, merged_workspace):
        remapped_state = copy.deepcopy(state)
        ref_mapping = {}
        namespace = f"import_{uuid.uuid4().hex[:12]}"

        for owner, key, index, rel_path in _iter_state_resource_refs(remapped_state):
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
            self.acquire_workspace_lock()
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
            self.acquire_workspace_lock()
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
                safe_extract_zip(zf, temp_workspace)

            # PhonTracer 保留自身工程模型；面向 PhonRec 的转换只在 PhonRec 导入端执行。
            validate_project_resources(state, temp_workspace)

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
                    prune_unreferenced_resources(state, self.workspace_dir)
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
                validate_project_resources(state, self.workspace_dir)
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
        safe_extract_zip(zip_file, target_dir)

    _safe_extract_zip = _safe_extract

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

        preserved_keys = [k for k in state.keys() if k not in ("speakers", "active_speaker_id", "save_time")]
        if not overlay or not getattr(self, "_preserved_top_level", None):
            self._preserved_top_level = {k: copy.deepcopy(state[k]) for k in preserved_keys}
        else:
            for k in preserved_keys:
                if k not in self._preserved_top_level:
                    self._preserved_top_level[k] = copy.deepcopy(state[k])
        if self.app:
            self.app._preserved_top_level = self._preserved_top_level

        state = migrate_removed_f0_engine(copy.deepcopy(state))
        workspace_dir = workspace_dir or self.workspace_dir
        restored = {}
        id_mapping = {}
        raw_groups = state.get("groups", []) if isinstance(state, dict) else []
        if isinstance(raw_groups, list):
            groups_lookup = {
                (g.get("name") or g.get("group")): g
                for g in raw_groups
                if isinstance(g, dict) and (g.get("name") or g.get("group"))
            }
        elif isinstance(raw_groups, dict):
            groups_lookup = raw_groups
        else:
            groups_lookup = {}
        wl_meta = state.get("wordlist_meta", {}) if isinstance(state, dict) else {}
        default_wl_title = wl_meta.get("title", "") if isinstance(wl_meta, dict) else ""

        current_speakers = self.app.speaker_manager.speakers
        existing_names = {s.name for s in current_speakers.values()} if overlay else set()
        seen_speaker_names = set(existing_names) if overlay else set()

        for old_spk_id, spk_data in state.get("speakers", {}).items():
            name = spk_data.get("name", "发音人")
            if name in seen_speaker_names:
                base_name = name
                counter = 2
                suffix = f"_{counter}" if overlay else f" ({counter})"
                while f"{base_name}{suffix}" in seen_speaker_names:
                    counter += 1
                    suffix = f"_{counter}" if overlay else f" ({counter})"
                name = f"{base_name}{suffix}"
            seen_speaker_names.add(name)

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
                spk.tab_mode = "单条长音频"

            spk_groups = spk_data.get("project_groups")
            if spk_groups and isinstance(spk_groups, list):
                spk.project_groups = list(spk_groups)
            else:
                seen_g = set()
                spk.project_groups = []
                for it in spk_data.get("items", {}).values():
                    g = it.get("group", "导入内容")
                    if g not in seen_g:
                        seen_g.add(g)
                        spk.project_groups.append(g)

            long_pitch_data = None
            long_pitch_file = spk_data.get("long_pitch_data_file")
            if long_pitch_file:
                spk.long_pitch_data_file = long_pitch_file
                long_pitch_path = self._resolve_project_path(long_pitch_file, workspace_dir=workspace_dir)
                if os.path.exists(long_pitch_path):
                    try:
                        with np.load(long_pitch_path) as loaded:
                            long_pitch_data = {'xs': loaded['xs'].copy(), 'freqs': loaded['freqs'].copy()}
                    except Exception as e:
                        logger.warning(f"Failed to load long_pitch_data: {e}")

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

                item_path = item.get('path')
                if item_path and os.path.exists(item_path):
                    item['snd'] = parselmouth.Sound(item_path)
                    normalize_independent_item_boundaries(
                        item,
                        item['snd'].duration,
                        label=item.get('label') or item.get('word', ''),
                    )
                elif spk.pending_long_snd:
                    item['snd'] = spk.pending_long_snd

                if 'pitch_data_file' in item:
                    npz_path = self._resolve_project_path(item['pitch_data_file'], workspace_dir=workspace_dir)
                    if os.path.exists(npz_path):
                        try:
                            with np.load(npz_path) as loaded:
                                item['pitch_data'] = {'xs': loaded['xs'].copy(), 'freqs': loaded['freqs'].copy()}
                        except Exception as e:
                            logger.warning(f"Failed to load pitch_data from {npz_path}: {e}")
                    del item['pitch_data_file']

                if ('pitch_data' not in item or item['pitch_data'] is None) and long_pitch_data is not None:
                    macro_s = item.get('macro_start')
                    macro_e = item.get('macro_end')
                    if macro_s is not None and macro_e is not None:
                        win_s = max(0.0, float(macro_s) - 0.05)
                        win_e = float(macro_e) + 0.05
                        l_xs = long_pitch_data['xs']
                        l_freqs = long_pitch_data['freqs']
                        mask = (l_xs >= win_s) & (l_xs <= win_e)
                        item['pitch_data'] = {'xs': l_xs[mask].copy(), 'freqs': l_freqs[mask].copy()}
                elif 'pitch_data' in item and item['pitch_data'] is not None:
                    macro_s = item.get('macro_start')
                    macro_e = item.get('macro_end')
                    if macro_s is not None and macro_e is not None:
                        cur_xs = item['pitch_data']['xs']
                        win_s = max(0.0, float(macro_s) - 0.05)
                        win_e = float(macro_e) + 0.05
                        if len(cur_xs) > 0 and (cur_xs[-1] - cur_xs[0]) > (win_e - win_s + 2.0) and (cur_xs[-1] - cur_xs[0]) > 10.0:
                            mask = (cur_xs >= win_s) & (cur_xs <= win_e)
                            item['pitch_data'] = {
                                'xs': cur_xs[mask].copy(),
                                'freqs': item['pitch_data']['freqs'][mask].copy()
                            }

                if 'formant_data_file' in item:
                    npz_path = self._resolve_project_path(item['formant_data_file'], workspace_dir=workspace_dir)
                    if os.path.exists(npz_path):
                        try:
                            with np.load(npz_path) as loaded:
                                formant_dict = {
                                    'xs': loaded['xs'].copy(),
                                    'f1': loaded['f1'].copy(),
                                    'f2': loaded['f2'].copy()
                                }
                                if 'f3' in loaded:
                                    formant_dict['f3'] = loaded['f3'].copy()
                                item['formant_data'] = formant_dict
                        except Exception as e:
                            logger.warning(f"Failed to load formant_data from {npz_path}: {e}")
                    del item['formant_data_file']

                if 'preview_f0' in item and isinstance(item['preview_f0'], list):
                    item['preview_f0'] = [np.nan if v is None else v for v in item['preview_f0']]
                if 'preview_formants' in item and isinstance(item['preview_formants'], dict):
                    for fk in ('f1', 'f2', 'f3'):
                        if fk in item['preview_formants'] and isinstance(item['preview_formants'][fk], list):
                            item['preview_formants'][fk] = [np.nan if v is None else v for v in item['preview_formants'][fk]]

                overrides = item.get('param_overrides') or {}
                for pk in ACOUSTIC_PARAM_KEYS:
                    if pk in overrides:
                        item[pk] = overrides[pk]
                    elif pk not in item and pk in spk.last_params:
                        item[pk] = spk.last_params[pk]

                if 'note' in item and 'item_note' not in item:
                    item['item_note'] = item['note']
                if 'tags' in item and 'item_tags' not in item:
                    item['item_tags'] = item['tags']
                if 'aliases' in item and 'item_aliases' not in item:
                    item['item_aliases'] = item['aliases']
                if 'meta' in item and 'item_meta' not in item:
                    item['item_meta'] = item['meta']
                if 'item_meta' in item and isinstance(item['item_meta'], dict):
                    item.setdefault('fields', copy.deepcopy(item['item_meta']))
                    item.setdefault('custom_fields', copy.deepcopy(item['item_meta']))
                if 'item_tags' in item and 'tags' not in item:
                    item['tags'] = copy.deepcopy(item['item_tags'])
                if 'item_note' in item and 'note' not in item:
                    item['note'] = item['item_note']
                grp_info = groups_lookup.get(item.get('group', '默认组'), {})
                if isinstance(grp_info, dict):
                    if 'group_note' not in item and grp_info.get('note'):
                        item['group_note'] = grp_info['note']
                    if 'group_tags' not in item and grp_info.get('tags'):
                        item['group_tags'] = copy.deepcopy(grp_info['tags'])
                if 'wordlist_title' not in item and default_wl_title:
                    item['wordlist_title'] = default_wl_title
                if 'wordlist_version' not in item and (
                    default_wl_title or item.get('item_tags') or item.get('item_meta')
                    or item.get('item_note') or item.get('item_aliases')
                    or item.get('group_tags') or item.get('group_note')
                ):
                    item['wordlist_version'] = 'v2'

                spk.items[item_id] = item

            if isinstance(spk_data.get("wordlist_doc"), dict):
                spk.wordlist_doc = copy.deepcopy(spk_data["wordlist_doc"])
            elif isinstance(state.get("wordlist_doc"), dict):
                spk.wordlist_doc = copy.deepcopy(state["wordlist_doc"])
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
        if hasattr(self.app, "switch_trim_silence") and "trim_silence" in state:
            trim_val = state.get("trim_silence", True)
            if trim_val:
                self.app.switch_trim_silence.select()
            else:
                self.app.switch_trim_silence.deselect()
        self.app.custom_script_runs = state.get("custom_script_runs", [])

ProjectManager.import_project = ProjectManager.load_project
ProjectManager.export_project_archive = ProjectManager.export_project
