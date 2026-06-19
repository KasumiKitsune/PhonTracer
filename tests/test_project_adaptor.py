import json
import math
import os
import struct
import zipfile
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from modules.project_adaptor import (
    validate_project_version,
    validate_project_archive_members,
    read_project_metadata_from_archive,
    safe_extract_zip,
    adapt_project_state,
    repair_wav_header,
    normalize_independent_item_boundaries,
    prune_unreferenced_resources,
    validate_project_resources,
)

def _write_test_wav(path, duration=0.2, frequency=220.0):
    parselmouth = pytest.importorskip("parselmouth")
    sample_rate = 8000
    sample_count = max(1, int(sample_rate * duration))
    xs = np.arange(sample_count, dtype=np.float64) / sample_rate
    samples = np.sin(2 * math.pi * frequency * xs)
    snd = parselmouth.Sound(np.array([samples]), sampling_frequency=sample_rate)
    snd.save(str(path), "WAV")
    return path


def _corrupt_wav_byte_rate(path):
    data = bytearray(path.read_bytes())
    fmt_offset = data.index(b"fmt ") + 8
    sample_rate = struct.unpack_from("<I", data, fmt_offset + 4)[0]
    block_align = struct.unpack_from("<H", data, fmt_offset + 12)[0]
    struct.pack_into("<I", data, fmt_offset + 8, sample_rate * block_align * 2)
    path.write_bytes(data)
    return sample_rate * block_align


def test_repair_legacy_phonrec_wav_header(tmp_path):
    wav_path = _write_test_wav(tmp_path / "legacy.wav")
    expected_byte_rate = _corrupt_wav_byte_rate(wav_path)

    assert repair_wav_header(wav_path) is True
    data = wav_path.read_bytes()
    fmt_offset = data.index(b"fmt ") + 8
    assert struct.unpack_from("<I", data, fmt_offset + 8)[0] == expected_byte_rate
    assert repair_wav_header(wav_path) is False


def test_normalize_independent_item_boundaries():
    item = {"label": "开来"}

    assert normalize_independent_item_boundaries(item, 1.2) is True
    assert item["start"] == 0.0
    assert item["end"] == 1.2
    assert item["chars_bounds"] == [[0.0, 0.6], [0.6, 1.2]]
    assert item["inner_splits"] == [0.6]


def test_project_manager_restores_phonrec_audio_with_valid_boundaries(tmp_path):
    from modules.project_manager import ProjectManager

    workspace = tmp_path / "workspace"
    audio_dir = workspace / "audio" / "spk1"
    audio_dir.mkdir(parents=True)
    wav_path = _write_test_wav(audio_dir / "spk1_item1.wav", duration=0.6)
    expected_byte_rate = _corrupt_wav_byte_rate(wav_path)
    state = {
        "version": "1.0",
        "active_speaker_id": "spk1",
        "speakers": {
            "spk1": {
                "id": "spk1",
                "name": "录音发音人",
                "tab_mode": "多条独立音频",
                "items": {
                    "item1": {
                        "id": "item1",
                        "label": "开来",
                        "path": "audio/spk1/spk1_item1.wav",
                    }
                },
            }
        },
    }

    validate_project_resources(state, str(workspace))
    wav_data = wav_path.read_bytes()
    fmt_offset = wav_data.index(b"fmt ") + 8
    assert struct.unpack_from("<I", wav_data, fmt_offset + 8)[0] == expected_byte_rate

    manager = ProjectManager.__new__(ProjectManager)
    manager.workspace_dir = str(workspace)
    manager.app = SimpleNamespace(speaker_manager=SimpleNamespace(speakers={}))
    restored, active_id = manager._build_restored_state(state, workspace_dir=str(workspace))

    item = restored["spk1"].items["item1"]
    assert active_id == "spk1"
    assert item["start"] == 0.0
    assert item["end"] == pytest.approx(0.6)
    assert len(item["chars_bounds"]) == 2
    assert item["chars_bounds"][-1][1] == pytest.approx(item["end"])

def test_validate_project_version():
    # Test valid version
    state_valid = {"version": "1.0"}
    assert validate_project_version(state_valid) == "1.0"

    # Test invalid version
    state_invalid = {"version": "2.0"}
    with pytest.raises(ValueError, match="不支持的工程版本"):
        validate_project_version(state_invalid)

def test_validate_project_archive_members(tmp_path):
    zip_file_path = tmp_path / "test.zip"
    
    # Valid Zip
    with zipfile.ZipFile(zip_file_path, "w") as zf:
        zf.writestr("project.json", "{}")
        zf.writestr("audio/spk1/spk1_item1.wav", "")
    
    with zipfile.ZipFile(zip_file_path, "r") as zf:
        # Should not raise any error
        validate_project_archive_members(zf)

    # Traversal Zip
    with zipfile.ZipFile(zip_file_path, "w") as zf:
        zf.writestr("../outside.json", "{}")
    
    with zipfile.ZipFile(zip_file_path, "r") as zf:
        with pytest.raises(ValueError, match="非法路径"):
            validate_project_archive_members(zf)

    # Absolute path Zip
    with zipfile.ZipFile(zip_file_path, "w") as zf:
        zf.writestr("/absolute/path.json", "{}")
    
    with zipfile.ZipFile(zip_file_path, "r") as zf:
        with pytest.raises(ValueError, match="非法路径"):
            validate_project_archive_members(zf)

def test_adapt_project_state_basic_merging(tmp_path):
    # Setup test workspace
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()
    
    state = {
        "version": "1.0",
        "active_speaker_id": "spk1",
        "speakers": {
            "spk1": {
                "id": "spk1",
                "name": "发音人1",
                "tab_mode": "多条独立音频",
                "items": {
                    "item1": {
                        "label": "字1",
                        "group": "组1",
                        "note": "备注1",
                        "tags": ["A"],
                        "path": None,
                    },
                    "item2": {
                        "label": "字2",
                        "group": "组1",
                        "path": None,
                    }
                }
            },
            "spk2": {
                "id": "spk2",
                "name": "发音人2",
                "tab_mode": "多条独立音频",
                "items": {
                    "item1": {
                        "label": "字1",
                        "group": "组1",
                        "path": None,
                    },
                    "item3": {
                        "label": "字3",
                        "group": "组1",
                        "path": None,
                    }
                }
            }
        }
    }
    
    adapted, warnings, summary = adapt_project_state(state, str(workspace_dir))
    
    # Both speakers should now have exactly the same items list (union)
    spk1_items = adapted["speakers"]["spk1"]["items"]
    spk2_items = adapted["speakers"]["spk2"]["items"]
    
    assert len(spk1_items) == 3
    assert len(spk2_items) == 3
    
    # Same entry IDs
    assert set(spk1_items.keys()) == set(spk2_items.keys())
    
    # Verify warning and summary counts
    assert summary["merged_speakers"] == 2
    assert summary["missing_items"] > 0
    assert any("缺少" in w for w in warnings)

def test_adapt_project_state_slicing_long_audio(tmp_path):
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()
    
    audio_dir = workspace_dir / "audio"
    audio_dir.mkdir()
    
    long_audio = audio_dir / "long.wav"
    _write_test_wav(long_audio, duration=1.0)
    _corrupt_wav_byte_rate(long_audio)
    
    state = {
        "version": "1.0",
        "active_speaker_id": "spk1",
        "speakers": {
            "spk1": {
                "id": "spk1",
                "name": "发音人1",
                "tab_mode": "单条长音频逐项切分",
                "long_audio_path": "audio/long.wav",
                "items": {
                    "item1": {
                        "label": "切片1",
                        "group": "组1",
                        "macro_start": 0.0,
                        "macro_end": 0.4,
                        "pitch_data_file": "data/spk1/spk1_item1_pitch.npz",
                    },
                    "item2": {
                        "label": "切片2",
                        "group": "组1",
                        "macro_start": 0.4,
                        "macro_end": 0.8,
                        "formant_data_file": "data/spk1/spk1_item2_formant.npz",
                    }
                }
            }
        }
    }
    
    # Write mock cache files
    data_dir = workspace_dir / "data" / "spk1"
    data_dir.mkdir(parents=True)
    (data_dir / "spk1_item1_pitch.npz").write_bytes(b"")
    (data_dir / "spk1_item2_formant.npz").write_bytes(b"")
    
    adapted, warnings, summary = adapt_project_state(state, str(workspace_dir))
    
    spk1_items = adapted["speakers"]["spk1"]["items"]
    
    # Items should be updated with new paths and sliced boundaries
    for item_id, item_data in spk1_items.items():
        assert item_data["path"] is not None
        assert item_data["path"].startswith("audio/spk1/")
        assert os.path.exists(workspace_dir / item_data["path"])
        # cache keys should be popped
        assert "pitch_data_file" not in item_data
        assert "formant_data_file" not in item_data
        assert item_data["start"] == 0.0
        assert item_data["end"] > 0.0
        assert item_data["chars_bounds"]
    
    assert summary["sliced_items"] == 2
    
    # Run resource pruning to delete the unreferenced npz files
    prune_unreferenced_resources(adapted, str(workspace_dir))
    
    # Cached files deleted from disk
    assert not (data_dir / "spk1_item1_pitch.npz").exists()
    assert not (data_dir / "spk1_item2_formant.npz").exists()

def test_long_audio_is_detected_even_if_legacy_tab_mode_is_wrong(tmp_path):
    workspace_dir = tmp_path / "workspace"
    (workspace_dir / "audio").mkdir(parents=True)
    _write_test_wav(workspace_dir / "audio" / "long.wav", duration=0.8)
    state = {
        "version": "1.0",
        "speakers": {
            "spk1": {
                "id": "spk1",
                "name": "旧工程发音人",
                "tab_mode": "多条独立音频",
                "long_audio_path": "audio/long.wav",
                "pending_batch_paths": ["audio/old_missing.wav"],
                "items": {
                    "item1": {
                        "label": "字1", "group": "组1", "macro_start": 0.1, "macro_end": 0.5,
                        "pitch_data": {"xs": [0.1], "freqs": [220]},
                        "preview_formants": {"f1": [500]}, "chars_bounds": [[0.1, 0.2]],
                    }
                },
            }
        },
    }

    adapted, warnings, summary = adapt_project_state(state, str(workspace_dir))
    item = next(iter(adapted["speakers"]["spk1"]["items"].values()))
    assert warnings == []
    assert summary["sliced_items"] == 1
    assert item["path"].endswith(".wav")
    assert item["source_segment"]["start"] == 0.1
    assert "pitch_data" not in item
    assert "preview_formants" not in item
    assert item["chars_bounds"][0][0] == 0.0
    assert item["chars_bounds"][0][1] == pytest.approx(0.4)
    assert adapted["speakers"]["spk1"]["pending_batch_paths"] == [item["path"]]

def test_prune_unreferenced_resources(tmp_path):
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()
    
    audio_dir = workspace_dir / "audio" / "spk1"
    audio_dir.mkdir(parents=True)
    
    # Referenced file
    ref_audio = audio_dir / "spk1_item1.wav"
    ref_audio.write_bytes(b"123")
    
    # Unreferenced file
    unref_audio = audio_dir / "spk1_unref.wav"
    unref_audio.write_bytes(b"456")
    
    state = {
        "version": "1.0",
        "active_speaker_id": "spk1",
        "speakers": {
            "spk1": {
                "id": "spk1",
                "name": "发音人1",
                "tab_mode": "多条独立音频",
                "items": {
                    "item1": {
                        "label": "字1",
                        "group": "组1",
                        "path": "audio/spk1/spk1_item1.wav"
                    }
                }
            }
        }
    }
    
    # Call prune
    prune_unreferenced_resources(state, str(workspace_dir))
    
    assert ref_audio.exists()
    assert not unref_audio.exists()


def test_project_manager_collect_file_refs():
    from modules.project_manager import ProjectManager

    state = {
        "version": "1.0",
        "speakers": {
            "spk1": {
                "id": "spk1",
                "name": "发音人1",
                "tab_mode": "多条独立音频",
                "long_audio_path": "audio/spk1/long_audio.wav",
                "pending_batch_paths": ["audio/spk1/batch1.wav", "audio/spk1/batch2.wav"],
                "items": {
                    "item1": {
                        "label": "字1",
                        "group": "组1",
                        "path": "audio/spk1/spk1_item1.wav",
                        "pitch_data_file": "data/spk1/spk1_item1_pitch.npz",
                        "formant_data_file": "data/spk1/spk1_item1_formant.npz",
                    }
                }
            }
        }
    }

    refs = ProjectManager._collect_project_file_refs(None, state)
    expected_refs = {
        "audio/spk1/long_audio.wav",
        "audio/spk1/batch1.wav",
        "audio/spk1/batch2.wav",
        "audio/spk1/spk1_item1.wav",
        "data/spk1/spk1_item1_pitch.npz",
        "data/spk1/spk1_item1_formant.npz",
    }
    assert refs == expected_refs
