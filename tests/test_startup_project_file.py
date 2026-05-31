import os
import json
import sys
import tempfile
from types import SimpleNamespace

from modules.app import PhoneticsApp


def test_启动参数接受带引号工程路径():
    fd, path = tempfile.mkstemp(suffix=".teproj")
    os.close(fd)
    try:
        found = PhoneticsApp._find_startup_project_file([f'"{path}"'])
        assert found == os.path.normpath(path)
    finally:
        os.remove(path)


def test_启动参数规范化_windows_file_uri():
    files = PhoneticsApp._normalize_startup_files(["file:///C:/Users/Sager/Desktop/project.teproj"])
    # Adjust for file URI logic
    expected_path = os.path.normpath("/C:/Users/Sager/Desktop/project.teproj")
    if os.name == "nt":
        expected_path = os.path.normpath("C:/Users/Sager/Desktop/project.teproj")
    assert files == [expected_path]


def test_启动损坏工程前保留自动保存工作区(monkeypatch):
    temp_dir = tempfile.mkdtemp()
    try:
        workspace_dir = os.path.join(temp_dir, "workspace")
        audio_dir = os.path.join(workspace_dir, "audio")
        os.makedirs(audio_dir)
        old_asset = os.path.join(audio_dir, "keep.wav")
        with open(old_asset, "wb") as f:
            f.write(b"keep")
        with open(os.path.join(workspace_dir, "project.json"), "w", encoding="utf-8") as f:
            json.dump(
                {
                    "version": "1.0",
                    "speakers": {
                        "sp": {
                            "id": "sp",
                            "name": "自动保存工程",
                            "items": {"item": {}},
                            "pending_batch_paths": [],
                        }
                    },
                },
                f,
                ensure_ascii=False,
            )

        broken_project = os.path.join(temp_dir, "broken.teproj")
        with open(broken_project, "wb") as f:
            f.write(b"not a zip")

        imported = []
        app = SimpleNamespace(
            project_manager=SimpleNamespace(workspace_dir=workspace_dir, auto_save_enabled=True),
            _initial_files_list=[broken_project],
            _normalize_startup_files=PhoneticsApp._normalize_startup_files,
            _find_startup_project_file=lambda files: PhoneticsApp._find_startup_project_file(files),
            execute_project_import=lambda path, overlay: imported.append((path, overlay)),
        )
        monkeypatch.setattr(sys, "frozen", True, raising=False)

        PhoneticsApp._check_autosave_recovery(app)

        assert imported == [(os.path.abspath(broken_project), False)]
        assert os.path.exists(old_asset)
    finally:
        import shutil
        shutil.rmtree(temp_dir, ignore_errors=True)
