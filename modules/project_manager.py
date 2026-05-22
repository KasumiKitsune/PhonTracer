import os
import json
import uuid
import zipfile
import shutil
import numpy as np
import threading
import traceback
import time

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

class ProjectManager:
    def __init__(self, app):
        self.app = app
        self.workspace_dir = os.path.join(os.path.expanduser("~"), ".phon_tracer", "workspace")
        self.backup_path = os.path.join(os.path.expanduser("~"), ".phon_tracer", "auto_save_backup.teproj")
        self.auto_save_enabled = False
        self._auto_save_timer = None
        self._save_lock = threading.RLock()
        self.auto_save_delay = 2.0
        self.auto_save_interval = 30.0
        
        if not os.path.exists(self.workspace_dir):
            os.makedirs(self.workspace_dir)
            
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
            if not rel.startswith("..") and rel != "." and not os.path.isabs(rel):
                return rel.replace(os.sep, "/")
        except (TypeError, ValueError):
            pass
        return None

    def _copy_to_workspace(self, src_path, subdir, token):
        if not src_path:
            return src_path

        existing_rel = self._workspace_relpath(src_path)
        if existing_rel and os.path.exists(src_path):
            return existing_rel

        if not os.path.exists(src_path):
            return src_path

        target_dir = self._get_audio_dir() if subdir == "audio" else self._get_data_dir()
        base = os.path.basename(src_path)
        dest_name = f"{self._safe_token(token)}_{base}"
        dest = os.path.join(target_dir, dest_name)
        if os.path.abspath(src_path) != os.path.abspath(dest):
            shutil.copy2(src_path, dest)
        return os.path.join(subdir, dest_name).replace(os.sep, "/")

    def _make_import_workspace(self):
        parent_dir = os.path.dirname(self.workspace_dir)
        os.makedirs(parent_dir, exist_ok=True)
        base_name = f"workspace_import_{os.getpid()}_{threading.get_ident()}_{int(time.time() * 1000)}"
        for idx in range(100):
            name = base_name if idx == 0 else f"{base_name}_{idx}"
            path = os.path.join(parent_dir, name)
            try:
                os.makedirs(path)
                return path
            except FileExistsError:
                continue
        raise RuntimeError("无法创建工程导入临时目录")

    def trigger_auto_save(self):
        if not self.auto_save_enabled:
            return
        if self._auto_save_timer:
            self._auto_save_timer.cancel()
        self._schedule_auto_save(self.auto_save_delay)

    def cancel_auto_save(self):
        if self._auto_save_timer:
            self._auto_save_timer.cancel()
            self._auto_save_timer = None

    def _schedule_auto_save(self, delay):
        if not self.auto_save_enabled:
            return
            
        def run_save():
            try:
                self.save_to_workspace()
            except Exception as e:
                print(f"Auto-save failed in timer: {e}")
                traceback.print_exc()
            finally:
                if self.auto_save_enabled:
                    self._schedule_auto_save(self.auto_save_interval)
                
        self._auto_save_timer = threading.Timer(delay, run_save)
        self._auto_save_timer.daemon = True
        self._auto_save_timer.start()

    def save_to_workspace(self):
        with self._save_lock:
            state = {
                "version": "1.0",
                "active_speaker_id": self.app.speaker_manager.active_speaker_id,
                "speakers": {}
            }
            
            data_dir = self._get_data_dir()
            audio_dir = self._get_audio_dir()
            
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
                    "items": {}
                }
                
                # Copy long audio to workspace if exists
                spk_data["long_audio_path"] = self._copy_to_workspace(
                    spk_data["long_audio_path"],
                    "audio",
                    f"{spk_id}_long"
                )
                
                # Copy batch audios to workspace
                new_batch = []
                for idx, p in enumerate(spk_data["pending_batch_paths"]):
                    new_batch.append(self._copy_to_workspace(p, "audio", f"{spk_id}_batch_{idx}"))
                spk_data["pending_batch_paths"] = new_batch
                
                for item_id, item in spk.items.items():
                    item_dict = {}
                    for k, v in item.items():
                        if k in ['snd', 'pitch']: continue
                        if k == 'pitch_data' and v is not None:
                            # Save numpy arrays to npz
                            npz_name = f"{self._safe_token(spk_id)}_{self._safe_token(item_id)}.npz"
                            npz_path = os.path.join(data_dir, npz_name)
                            if isinstance(v, dict) and 'xs' in v and 'freqs' in v:
                                np.savez_compressed(npz_path, xs=v['xs'], freqs=v['freqs'])
                                item_dict['pitch_data_file'] = os.path.join("data", npz_name).replace(os.sep, "/")
                            else:
                                item_dict[k] = v
                        elif k == 'path':
                            item_dict['path'] = self._copy_to_workspace(v, "audio", f"{spk_id}_{item_id}")
                        else:
                            item_dict[k] = v
                    spk_data["items"][item_id] = item_dict
                
                state["speakers"][spk_id] = spk_data
                
            project_json = os.path.join(self.workspace_dir, "project.json")
            serializable_state = to_json_serializable(state)
            tmp_json = project_json + ".tmp"
            with open(tmp_json, "w", encoding="utf-8") as f:
                json.dump(serializable_state, f, ensure_ascii=False, indent=2)
            os.replace(tmp_json, project_json)
                
            if self.auto_save_enabled:
                self._create_backup()
            return True

    def _create_backup(self):
        try:
            with zipfile.ZipFile(self.backup_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                for root, _, files in os.walk(self.workspace_dir):
                    for file in files:
                        file_path = os.path.join(root, file)
                        arcname = os.path.relpath(file_path, self.workspace_dir)
                        zf.write(file_path, arcname)
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
                with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                    for root, _, files in os.walk(self.workspace_dir):
                        for file in files:
                            file_path = os.path.join(root, file)
                            arcname = os.path.relpath(file_path, self.workspace_dir)
                            zf.write(file_path, arcname)
            
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

    def load_project(self, zip_path):
        temp_workspace = None
        try:
            if not zipfile.is_zipfile(zip_path):
                raise ValueError("工程文件格式不正确")
            
            temp_workspace = self._make_import_workspace()
            with zipfile.ZipFile(zip_path, 'r') as zf:
                self._safe_extract(zf, temp_workspace)
                
            project_json = os.path.join(temp_workspace, "project.json")
            if not os.path.exists(project_json):
                raise ValueError("工程文件损坏：未找到 project.json")
                
            with open(project_json, "r", encoding="utf-8") as f:
                state = json.load(f)
                
            speakers = state.get("speakers", {})
            if not speakers:
                raise ValueError("工程文件损坏：未找到发音人数据")

            with self._save_lock:
                if os.path.exists(self.workspace_dir):
                    shutil.rmtree(self.workspace_dir)
                shutil.move(temp_workspace, self.workspace_dir)
                temp_workspace = None
                self._restore_state(state)
            
            return True
        except Exception as e:
            traceback.print_exc()
            self._show_error("导入失败", str(e))
            return False
        finally:
            if temp_workspace and os.path.exists(temp_workspace):
                shutil.rmtree(temp_workspace, ignore_errors=True)

    def _safe_extract(self, zip_file, target_dir):
        target_dir_abs = os.path.abspath(target_dir)
        for member in zip_file.infolist():
            member_path = os.path.abspath(os.path.join(target_dir_abs, member.filename))
            if not member_path.startswith(target_dir_abs + os.sep) and member_path != target_dir_abs:
                raise ValueError("工程文件包含非法路径")
        zip_file.extractall(target_dir_abs)

    def _resolve_project_path(self, path):
        if not path:
            return path
        norm = str(path).replace("\\", "/")
        if norm == "audio" or norm.startswith("audio/") or norm == "data" or norm.startswith("data/"):
            return os.path.join(self.workspace_dir, *norm.split("/"))
        return path

    def _restore_state(self, state):
        import parselmouth
        from .speaker_manager import SpeakerState

        restored = {}
        for spk_id, spk_data in state.get("speakers", {}).items():
            spk = SpeakerState(spk_data.get("name", "发音人"))
            spk.id = spk_data.get("id", spk_id)
            spk.last_params = spk_data.get("last_params", spk.last_params)
            spk.tab_mode = spk_data.get("tab_mode", "多条独立音频")

            long_audio_rel = spk_data.get("long_audio_path")
            if long_audio_rel:
                spk.long_audio_path = self._resolve_project_path(long_audio_rel)
                if os.path.exists(spk.long_audio_path):
                    spk.pending_long_snd = parselmouth.Sound(spk.long_audio_path)

            spk.pending_batch_paths = [
                self._resolve_project_path(p)
                for p in spk_data.get("pending_batch_paths", [])
            ]
            spk.current_macro_segments = spk_data.get("current_macro_segments", [])
            spk.manual_segments = spk_data.get("manual_segments", None)

            for item_id, item_data in spk_data.get("items", {}).items():
                item = dict(item_data)
                if 'path' in item:
                    item['path'] = self._resolve_project_path(item['path'])

                if spk.tab_mode == "多条独立音频" and item.get('path') and os.path.exists(item['path']):
                    item['snd'] = parselmouth.Sound(item['path'])
                elif spk.tab_mode == "单条长音频" and spk.pending_long_snd:
                    item['snd'] = spk.pending_long_snd

                if 'pitch_data_file' in item:
                    npz_path = self._resolve_project_path(item['pitch_data_file'])
                    if os.path.exists(npz_path):
                        with np.load(npz_path) as loaded:
                            item['pitch_data'] = {'xs': loaded['xs'].copy(), 'freqs': loaded['freqs'].copy()}
                    del item['pitch_data_file']

                spk.items[item_id] = item

            restored[spk.id] = spk

        self.app.speaker_manager.speakers.clear()
        self.app.speaker_manager.speakers.update(restored)
        active_id = state.get("active_speaker_id")
        if active_id not in restored:
            active_id = next(iter(restored))
        self.app.speaker_manager.active_speaker_id = active_id
