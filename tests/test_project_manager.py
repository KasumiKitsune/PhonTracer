import os
import json
import shutil
import tempfile
import threading
import time
import wave
import zipfile
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from modules.project_manager import ProjectManager, migrate_removed_f0_engine, read_project_metadata_from_archive


def _make_project_manager(app, workspace_dir):
    manager = ProjectManager.__new__(ProjectManager)
    manager.app = app
    manager.workspace_dir = workspace_dir
    manager.backup_path = os.path.join(os.path.dirname(workspace_dir), "auto_save_backup.teproj")
    manager.auto_save_enabled = False
    manager._auto_save_timer = None
    manager._save_lock = threading.RLock()
    manager.auto_save_delay = 2.0
    manager.auto_save_interval = 30.0
    os.makedirs(workspace_dir, exist_ok=True)
    return manager


def _write_test_wav(path, sample_value=100):
    with wave.open(path, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(8000)
        wav_file.writeframes(int(sample_value).to_bytes(2, "little", signed=True) * 800)


def test_project_archive_prunes_unreferenced_audio_and_data():
    temp_dir = tempfile.mkdtemp()
    try:
        workspace_dir = os.path.join(temp_dir, "workspace")
        source_dir = os.path.join(temp_dir, "source")
        os.makedirs(source_dir)

        source_audio = os.path.join(source_dir, "sample.wav")
        with open(source_audio, "wb") as f:
            f.write(b"fake wav bytes")

        os.makedirs(os.path.join(workspace_dir, "audio"), exist_ok=True)
        os.makedirs(os.path.join(workspace_dir, "data"), exist_ok=True)
        stale_audio = os.path.join(workspace_dir, "audio", "old.wav")
        stale_data = os.path.join(workspace_dir, "data", "old.npz")
        with open(stale_audio, "wb") as f:
            f.write(b"old audio")
        with open(stale_data, "wb") as f:
            f.write(b"old data")

        speaker = SimpleNamespace(
            id="sp1",
            name="Speaker 1",
            last_params={"pts": 11},
            tab_mode="多条独立音频",
            long_audio_path=None,
            pending_batch_paths=[source_audio],
            current_macro_segments=[],
            manual_segments=None,
            items={
                "item1": {
                    "label": "sample",
                    "path": source_audio,
                    "pitch_data": {
                        "xs": np.array([0.0, 0.1, 0.2]),
                        "freqs": np.array([100.0, 110.0, 120.0]),
                    },
                }
            },
        )
        app = SimpleNamespace(
            root=None,
            speaker_manager=SimpleNamespace(active_speaker_id="sp1", speakers={"sp1": speaker}),
        )
        manager = _make_project_manager(app, workspace_dir)

        assert manager.save_to_workspace() is True
        assert not os.path.exists(stale_audio)
        assert not os.path.exists(stale_data)

        project_json = os.path.join(workspace_dir, "project.json")
        with open(project_json, "r", encoding="utf-8") as f:
            saved_state = json.load(f)
        saved_speaker = saved_state["speakers"]["sp1"]
        saved_item = saved_speaker["items"]["item1"]
        assert saved_item["path"] == saved_speaker["pending_batch_paths"][0]
        expected_runtime_path = manager._resolve_project_path(saved_item["path"])
        assert speaker.pending_batch_paths == [expected_runtime_path]
        assert speaker.items["item1"]["path"] == expected_runtime_path

        extra_root_file = os.path.join(workspace_dir, "debug.txt")
        with open(extra_root_file, "w", encoding="utf-8") as f:
            f.write("not part of the project")

        archive_path = os.path.join(temp_dir, "project.teproj")
        assert manager.export_project(archive_path) is True

        with zipfile.ZipFile(archive_path) as zf:
            names = set(zf.namelist())

        assert names == {
            "project.json",
            "audio/sp1_batch_0_sample.wav",
            "data/sp1_item1.npz",
        }
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_project_manager_overlay():
    import uuid
    from modules.speaker_manager import SpeakerState

    temp_dir = tempfile.mkdtemp()
    try:
        # Create Project A
        workspace_a = os.path.join(temp_dir, "workspace_a")
        os.makedirs(os.path.join(workspace_a, "audio"), exist_ok=True)
        os.makedirs(os.path.join(workspace_a, "data"), exist_ok=True)
        
        sp_a = SpeakerState("发音人 A")
        sp_a.id = "sp_a_id"
        sp_a.tab_mode = "多条独立音频"
        sp_a.pending_batch_paths = []
        sp_a.items = {}
        
        app_a = SimpleNamespace(
            root=None,
            speaker_manager=SimpleNamespace(active_speaker_id="sp_a_id", speakers={"sp_a_id": sp_a})
        )
        manager_a = _make_project_manager(app_a, workspace_a)
        
        dummy_audio_a = os.path.join(workspace_a, "audio", "sp_a_id_batch_0_sample.wav")
        _write_test_wav(dummy_audio_a, sample_value=100)
        sp_a.pending_batch_paths = [dummy_audio_a]
        
        project_a_path = os.path.join(temp_dir, "project_a.teproj")
        assert manager_a.save_to_workspace() is True
        assert manager_a.export_project(project_a_path) is True

        # Create Project B (with duplicate speaker name and colliding speaker ID)
        workspace_b = os.path.join(temp_dir, "workspace_b")
        os.makedirs(os.path.join(workspace_b, "audio"), exist_ok=True)
        os.makedirs(os.path.join(workspace_b, "data"), exist_ok=True)
        
        sp_b = SpeakerState("发音人 A")
        sp_b.id = "sp_a_id"
        sp_b.tab_mode = "多条独立音频"
        sp_b.pending_batch_paths = []
        sp_b.items = {}
        
        app_b = SimpleNamespace(
            root=None,
            speaker_manager=SimpleNamespace(active_speaker_id="sp_a_id", speakers={"sp_a_id": sp_b})
        )
        manager_b = _make_project_manager(app_b, workspace_b)
        
        dummy_audio_b = os.path.join(workspace_b, "audio", "sp_a_id_batch_0_sample.wav")
        _write_test_wav(dummy_audio_b, sample_value=200)
        sp_b.pending_batch_paths = [dummy_audio_b]
        
        project_b_path = os.path.join(temp_dir, "project_b.teproj")
        assert manager_b.save_to_workspace() is True
        assert manager_b.export_project(project_b_path) is True

        # Load Project A and Project B (Overlay) in a destination manager
        workspace_dest = os.path.join(temp_dir, "workspace_dest")
        os.makedirs(workspace_dest, exist_ok=True)
        
        class MockSpeakerManager:
            def __init__(self):
                self.speakers = {}
                self.active_speaker_id = None
                
            def get_all_speakers(self):
                return list(self.speakers.values())
                
        mock_sm = MockSpeakerManager()
        app_dest = SimpleNamespace(root=None, speaker_manager=mock_sm)
        manager_dest = _make_project_manager(app_dest, workspace_dest)
        
        # Load project A normally
        assert manager_dest.load_project(project_a_path, overlay=False) is True
        assert len(mock_sm.speakers) == 1
        loaded_spk_a_id = list(mock_sm.speakers.keys())[0]
        assert mock_sm.speakers[loaded_spk_a_id].name == "发音人 A"
        assert mock_sm.active_speaker_id == loaded_spk_a_id
        
        # Load project B with overlay=True
        assert manager_dest.load_project(project_b_path, overlay=True) is True
        
        # Verify merged state
        assert len(mock_sm.speakers) == 2
        
        spk_a = mock_sm.speakers[loaded_spk_a_id]
        other_spk_id = [sid for sid in mock_sm.speakers if sid != loaded_spk_a_id][0]
        spk_b = mock_sm.speakers[other_spk_id]
        
        assert spk_a.name == "发音人 A"
        assert spk_b.name == "发音人 A_2"
        assert mock_sm.active_speaker_id == other_spk_id
        
        # Verify workspace files are copied
        expected_audio_rel = spk_a.pending_batch_paths[0]
        assert os.path.exists(expected_audio_rel)
        with wave.open(spk_a.pending_batch_paths[0], "rb") as wav_file:
            assert int.from_bytes(wav_file.readframes(1), "little", signed=True) == 100
        with wave.open(spk_b.pending_batch_paths[0], "rb") as wav_file:
            assert int.from_bytes(wav_file.readframes(1), "little", signed=True) == 200
        assert spk_a.pending_batch_paths[0] != spk_b.pending_batch_paths[0]
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_export_rejects_missing_audio_resource():
    temp_dir = tempfile.mkdtemp()
    try:
        workspace_dir = os.path.join(temp_dir, "workspace")
        missing_audio = os.path.join(temp_dir, "missing.wav")
        speaker = SimpleNamespace(
            id="sp1",
            name="发音人",
            last_params={},
            tab_mode="多条独立音频",
            long_audio_path=None,
            pending_batch_paths=[missing_audio],
            current_macro_segments=[],
            manual_segments=None,
            items={"item1": {"label": "缺失", "path": missing_audio}},
        )
        app = SimpleNamespace(
            root=None,
            speaker_manager=SimpleNamespace(active_speaker_id="sp1", speakers={"sp1": speaker}),
        )
        manager = _make_project_manager(app, workspace_dir)
        archive_path = os.path.join(temp_dir, "missing.teproj")

        assert manager.export_project(archive_path) is False
        assert not os.path.exists(archive_path)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_save_repairs_relocated_overlay_long_audio_reference():
    temp_dir = tempfile.mkdtemp()
    try:
        workspace_dir = os.path.join(temp_dir, "workspace")
        audio_dir = os.path.join(workspace_dir, "audio")
        os.makedirs(audio_dir, exist_ok=True)

        stale_name = "sp1_long_男.wav"
        stale_path = os.path.join(audio_dir, stale_name)
        relocated_path = os.path.join(audio_dir, f"import_abc123_{stale_name}")
        _write_test_wav(relocated_path)

        speaker = SimpleNamespace(
            id="sp1",
            name="男",
            last_params={},
            tab_mode="单条长音频",
            long_audio_path=stale_path,
            pending_batch_paths=[],
            current_macro_segments=[],
            manual_segments=None,
            items={},
        )
        app = SimpleNamespace(
            root=None,
            speaker_manager=SimpleNamespace(active_speaker_id="sp1", speakers={"sp1": speaker}),
        )
        manager = _make_project_manager(app, workspace_dir)

        assert manager.save_to_workspace() is True
        assert speaker.long_audio_path == relocated_path

        with open(os.path.join(workspace_dir, "project.json"), "r", encoding="utf-8") as f:
            saved_state = json.load(f)
        assert saved_state["speakers"]["sp1"]["long_audio_path"] == f"audio/import_abc123_{stale_name}"

        archive_path = os.path.join(temp_dir, "repaired.teproj")
        assert manager.export_project(archive_path) is True
        with zipfile.ZipFile(archive_path) as zip_file:
            assert f"audio/import_abc123_{stale_name}" in zip_file.namelist()
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_save_canonicalizes_external_long_audio_reference():
    temp_dir = tempfile.mkdtemp()
    try:
        workspace_dir = os.path.join(temp_dir, "workspace")
        source_path = os.path.join(temp_dir, "source.wav")
        _write_test_wav(source_path)

        speaker = SimpleNamespace(
            id="sp1",
            name="发音人",
            last_params={},
            tab_mode="单条长音频",
            long_audio_path=source_path,
            pending_batch_paths=[],
            current_macro_segments=[],
            manual_segments=None,
            items={},
        )
        app = SimpleNamespace(
            root=None,
            speaker_manager=SimpleNamespace(active_speaker_id="sp1", speakers={"sp1": speaker}),
        )
        manager = _make_project_manager(app, workspace_dir)

        assert manager.save_to_workspace() is True
        assert speaker.long_audio_path == os.path.join(workspace_dir, "audio", "sp1_long_source.wav")
        assert os.path.exists(speaker.long_audio_path)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_import_rejects_missing_resource_without_replacing_workspace():
    temp_dir = tempfile.mkdtemp()
    try:
        workspace_dir = os.path.join(temp_dir, "workspace")
        os.makedirs(os.path.join(workspace_dir, "audio"), exist_ok=True)
        old_file = os.path.join(workspace_dir, "audio", "keep.wav")
        _write_test_wav(old_file)

        speaker = SimpleNamespace(id="old", name="旧工程", items={})
        app = SimpleNamespace(
            root=None,
            speaker_manager=SimpleNamespace(active_speaker_id="old", speakers={"old": speaker}),
        )
        manager = _make_project_manager(app, workspace_dir)

        archive_path = os.path.join(temp_dir, "broken.teproj")
        state = {
            "version": "1.0",
            "active_speaker_id": "new",
            "speakers": {
                "new": {
                    "id": "new",
                    "name": "新工程",
                    "tab_mode": "多条独立音频",
                    "pending_batch_paths": ["audio/missing.wav"],
                    "items": {},
                }
            },
        }
        with zipfile.ZipFile(archive_path, "w") as zip_file:
            zip_file.writestr("project.json", json.dumps(state, ensure_ascii=False))

        assert manager.load_project(archive_path) is False
        assert os.path.exists(old_file)
        assert list(app.speaker_manager.speakers) == ["old"]
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_atomic_archive_write_preserves_existing_file_on_failure():
    temp_dir = tempfile.mkdtemp()
    try:
        workspace_dir = os.path.join(temp_dir, "workspace")
        os.makedirs(workspace_dir, exist_ok=True)
        with open(os.path.join(workspace_dir, "project.json"), "w", encoding="utf-8") as f:
            json.dump({"version": "1.0", "speakers": {}}, f)

        manager = _make_project_manager(SimpleNamespace(root=None), workspace_dir)
        archive_path = os.path.join(temp_dir, "existing.teproj")
        original = b"original project bytes"
        with open(archive_path, "wb") as f:
            f.write(original)

        with patch.object(zipfile.ZipFile, "write", side_effect=RuntimeError("模拟写入失败")):
            try:
                manager._write_project_archive(archive_path)
            except RuntimeError:
                pass

        with open(archive_path, "rb") as f:
            assert f.read() == original
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_auto_save_overlap_keeps_single_periodic_chain():
    manager = ProjectManager.__new__(ProjectManager)
    manager.auto_save_enabled = True
    manager._auto_save_timer = None
    manager._auto_save_generation = 0
    manager._timer_lock = threading.RLock()
    manager.auto_save_delay = 0.01
    manager.auto_save_interval = 0.04

    started = threading.Event()
    release = threading.Event()
    save_times = []

    def save_snapshot():
        save_times.append(time.monotonic())
        if len(save_times) == 1:
            started.set()
            release.wait(1)

    manager.save_autosave_snapshot = save_snapshot
    manager.trigger_auto_save()
    assert started.wait(1)
    manager.trigger_auto_save()
    release.set()
    time.sleep(0.15)
    manager.auto_save_enabled = False
    manager.cancel_auto_save()

    assert 3 <= len(save_times) <= 5


def test_import_rejects_internal_path_traversal_without_replacing_workspace():
    temp_dir = tempfile.mkdtemp()
    try:
        workspace_dir = os.path.join(temp_dir, "workspace")
        os.makedirs(os.path.join(workspace_dir, "audio"), exist_ok=True)
        old_file = os.path.join(workspace_dir, "audio", "keep.wav")
        _write_test_wav(old_file)

        speaker = SimpleNamespace(id="old", name="旧工程", items={})
        app = SimpleNamespace(
            root=None,
            speaker_manager=SimpleNamespace(active_speaker_id="old", speakers={"old": speaker}),
        )
        manager = _make_project_manager(app, workspace_dir)

        archive_path = os.path.join(temp_dir, "traversal.teproj")
        state = {
            "version": "1.0",
            "active_speaker_id": "new",
            "speakers": {
                "new": {
                    "id": "new",
                    "name": "路径穿越工程",
                    "tab_mode": "多条独立音频",
                    "pending_batch_paths": ["audio/../outside.wav"],
                    "items": {},
                }
            },
        }
        with zipfile.ZipFile(archive_path, "w") as zip_file:
            zip_file.writestr("project.json", json.dumps(state, ensure_ascii=False))

        assert manager.load_project(archive_path) is False
        assert os.path.exists(old_file)
        assert list(app.speaker_manager.speakers) == ["old"]
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_project_round_trip_restores_ui_state_and_last_selection():
    from modules.speaker_manager import SpeakerState

    temp_dir = tempfile.mkdtemp()
    try:
        source_workspace = os.path.join(temp_dir, "workspace_source")
        os.makedirs(os.path.join(source_workspace, "audio"), exist_ok=True)
        speaker = SpeakerState("发音人")
        speaker.id = "sp1"
        speaker.tab_mode = "多条独立音频"
        speaker.last_selected_iid = "item1"
        audio_path = os.path.join(source_workspace, "audio", "sample.wav")
        _write_test_wav(audio_path)
        speaker.pending_batch_paths = [audio_path]
        speaker.items = {"item1": {"label": "测试", "path": audio_path}}
        source_app = SimpleNamespace(
            root=None,
            export_numbering_rule_value="per_group",
            speaker_manager=SimpleNamespace(active_speaker_id="sp1", speakers={"sp1": speaker}),
        )
        source_manager = _make_project_manager(source_app, source_workspace)
        archive_path = os.path.join(temp_dir, "round_trip.teproj")

        assert source_manager.export_project(archive_path) is True

        target_app = SimpleNamespace(
            root=None,
            export_numbering_rule_value="continuous",
            speaker_manager=SimpleNamespace(active_speaker_id=None, speakers={}),
        )
        target_manager = _make_project_manager(target_app, os.path.join(temp_dir, "workspace_target"))

        assert target_manager.load_project(archive_path) is True
        restored = target_app.speaker_manager.speakers["sp1"]
        assert target_app.export_numbering_rule_value == "per_group"
        assert restored.last_selected_iid == "item1"
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_project_preview_rejects_unsupported_version():
    temp_dir = tempfile.mkdtemp()
    try:
        archive_path = os.path.join(temp_dir, "future.teproj")
        with zipfile.ZipFile(archive_path, "w") as zip_file:
            zip_file.writestr("project.json", json.dumps({"version": "99.0", "speakers": {}}))

        try:
            read_project_metadata_from_archive(archive_path)
        except ValueError as error:
            assert "不支持的工程版本" in str(error)
        else:
            raise AssertionError("未知版本工程应被拒绝")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_removed_f0_engine_is_migrated_without_touching_cached_results():
    state = {
        "speakers": {
            "sp1": {
                "last_params": {"f0_engine": "reaper", "pitch_floor": 75},
                "items": {
                    "item1": {
                        "f0_engine": "reaper",
                        "preview_f0": [100.0, 110.0],
                    }
                },
            }
        }
    }

    migrated = migrate_removed_f0_engine(state)

    assert "f0_engine" not in migrated["speakers"]["sp1"]["last_params"]
    assert "f0_engine" not in migrated["speakers"]["sp1"]["items"]["item1"]
    assert migrated["speakers"]["sp1"]["items"]["item1"]["preview_f0"] == [100.0, 110.0]

