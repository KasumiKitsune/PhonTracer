import os
import json
import shutil
import tempfile
import threading
import zipfile
from types import SimpleNamespace

import numpy as np

from modules.project_manager import ProjectManager


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
        with open(dummy_audio_a, "wb") as f:
            f.write(b"wav A")
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
        with open(dummy_audio_b, "wb") as f:
            f.write(b"wav B")
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
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

