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
