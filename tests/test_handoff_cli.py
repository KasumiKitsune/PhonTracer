import os
import json
import pytest
import shutil
import tempfile
import sys
import tkinter as tk
import customtkinter as ctk
from unittest.mock import MagicMock

# 确保 modules 能被导入
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import parse_handoff_arguments
from modules.app import PhoneticsApp

@pytest.fixture
def temp_dir():
    d = tempfile.mkdtemp()
    yield d
    shutil.rmtree(d)

def test_parse_handoff_arguments_no_manifest():
    args = ["project.teproj", "other_file.wav"]
    cleaned, manifest = parse_handoff_arguments(args)
    assert cleaned == args
    assert manifest is None

def test_parse_handoff_arguments_with_manifest(temp_dir):
    manifest_path = os.path.join(temp_dir, "handoff.json")
    teproj_path = os.path.join(temp_dir, "review_snapshot.teproj")

    # Create dummy teproj file
    with open(teproj_path, "w") as f:
        f.write("dummy zip content")

    manifest_data = {
        "handoff_id": "test-uuid",
        "speaker_id": "spk-1",
        "word_id": "word-123",
        "project_archive": "review_snapshot.teproj"
    }
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest_data, f)

    # Test format: --handoff-manifest <path>
    args = ["--handoff-manifest", manifest_path]
    cleaned, manifest = parse_handoff_arguments(args)
    assert len(cleaned) == 1
    assert os.path.abspath(cleaned[0]) == os.path.abspath(teproj_path)
    assert manifest == manifest_data

    # Test format: --handoff-manifest=<path>
    args = [f"--handoff-manifest={manifest_path}"]
    cleaned, manifest = parse_handoff_arguments(args)
    assert len(cleaned) == 1
    assert os.path.abspath(cleaned[0]) == os.path.abspath(teproj_path)
    assert manifest == manifest_data

def test_parse_handoff_arguments_override_teproj(temp_dir):
    manifest_path = os.path.join(temp_dir, "handoff.json")
    teproj_path = os.path.join(temp_dir, "review_snapshot.teproj")

    # Create dummy teproj file
    with open(teproj_path, "w") as f:
        f.write("dummy zip content")

    manifest_data = {
        "handoff_id": "test-uuid",
        "speaker_id": "spk-1",
        "word_id": "word-123",
        "project_archive": "review_snapshot.teproj"
    }
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest_data, f)

    # Explicit teproj path provided on command line - should not append review_snapshot.teproj
    args = ["other_project.teproj", "--handoff-manifest", manifest_path]
    cleaned, manifest = parse_handoff_arguments(args)
    assert cleaned == ["other_project.teproj"]
    assert manifest == manifest_data

@pytest.mark.filterwarnings("ignore::DeprecationWarning")
def test_phonetics_app_title_suffix():
    # Mock tkinter root and other necessary UI configurations
    root = ctk.CTk()
    root.withdraw()

    manifest_data = {
        "speaker_id": "spk-1",
        "word_id": "word-123"
    }

    # Instantiate PhoneticsApp with handoff manifest data
    # We pass defer_startup_check=True so that it doesn't trigger recovery dialogs during test
    app = PhoneticsApp(root, initial_files=[], defer_startup_check=True, handoff_manifest=manifest_data)

    title = root.title()
    assert "复核副本" in title

    root.destroy()
