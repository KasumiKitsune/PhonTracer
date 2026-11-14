import copy
import json
import math
import os
import zipfile
from types import SimpleNamespace

import numpy as np
import pytest

from modules.app import PhoneticsApp
from modules.project_adaptor import (
    MAX_ARCHIVE_MEMBERS,
    adapt_project_state,
    normalize_independent_item_boundaries,
    validate_project_resources,
)
from modules.project_manager import ProjectManager, to_json_serializable
from modules.project_patch import (
    _append_or_update_script_run,
    _apply_trim_item_audio,
    _item_params,
    _save_project_state,
    _segment_sound_for_item,
)
from modules.report_generator import get_item_override_param, get_majority_item_params
from modules.speaker_manager import SpeakerManager, SpeakerState
from modules.wordlist_v2 import normalize_wordlist_document


def _make_dummy_sound(duration=1.0, freq=220.0, sr=8000):
    parselmouth = pytest.importorskip("parselmouth")
    n = max(1, int(duration * sr))
    xs = np.arange(n, dtype=np.float64) / sr
    samples = 0.5 * np.sin(2.0 * math.pi * freq * xs)
    return parselmouth.Sound(np.array([samples]), sampling_frequency=sr)


def test_d01_json_nan_and_inf_sanitized(tmp_path):
    """[1.1 / D-01] NaN 和 Infinity 在序列化为 JSON 时必须转为 null，且不输出非法字面量。"""
    payload = {
        "preview_f0": [120.0, float("nan"), float("inf"), float("-inf"), np.nan, np.float64(200.5)],
        "nested": {"val": np.float32("nan")},
    }
    serialized = to_json_serializable(payload)
    assert serialized["preview_f0"] == [120.0, None, None, None, None, 200.5]
    assert serialized["nested"]["val"] is None

    # Verify strict JSON dump without NaN
    dumped = json.dumps(serialized, allow_nan=False)
    assert "NaN" not in dumped
    assert "Infinity" not in dumped

    # Verify project_patch._save_project_state also enforces allow_nan=False
    ws = tmp_path / "patch_ws"
    ws.mkdir()
    _save_project_state(str(ws), payload)
    raw_text = (ws / "project.json").read_text(encoding="utf-8")
    assert "NaN" not in raw_text
    parsed = json.loads(raw_text)
    assert parsed["preview_f0"] == [120.0, None, None, None, None, 200.5]


def test_d02_mode_switch_preserves_manual_boundaries():
    """[1.2 / D-02] 切换 F0/共振峰模式时不得重置手动精修边界与内部切分线。"""
    app = PhoneticsApp.__new__(PhoneticsApp)
    item = {
        "label": "开来",
        "start": 0.12,
        "end": 0.78,
        "raw_start": 0.10,
        "raw_end": 0.80,
        "macro_start": 0.0,
        "macro_end": 1.0,
        "chars_bounds": [[0.12, 0.40], [0.45, 0.78]],
        "inner_splits": [0.42],
        "is_manual_edited": True,
        "analysis_mode": "f0",
    }
    app._save_item_mode_state(item, "f0")
    # First time switching to formant mode: no formant state yet, must preserve manual bounds
    restored = app._load_item_mode_state(item, "formant")
    assert restored is False

    # Simulate on_analysis_mode_change fallback preservation logic
    if not restored:
        has_valid_bounds = (
            item.get("start") is not None
            and item.get("end") is not None
            and item["end"] > item["start"]
        )
        assert item.get("is_manual_edited") or has_valid_bounds
        item["analysis_mode"] = "formant"

    assert item["start"] == pytest.approx(0.12)
    assert item["end"] == pytest.approx(0.78)
    assert item["inner_splits"] == [0.42]
    assert item["chars_bounds"] == [[0.12, 0.40], [0.45, 0.78]]
    assert item["is_manual_edited"] is True

    # Modify bounds in formant mode, save, and switch back to f0
    item["start"] = 0.15
    app._save_item_mode_state(item, "formant")
    assert app._load_item_mode_state(item, "f0") is True
    assert item["start"] == pytest.approx(0.12)
    assert app._load_item_mode_state(item, "formant") is True
    assert item["start"] == pytest.approx(0.15)


def test_d04_and_b01_b02_b04_b05_workspace_save_and_restore(tmp_path):
    """[1.4 / D-04 & 2.1 / B-01 & 2.2 / B-02, B-04, B-05] 验证顶层字段保留、长音频基频切片、运行态剥离与参数覆盖。"""
    ws = tmp_path / "ws"
    ws.mkdir()

    snd = _make_dummy_sound(duration=15.0)
    long_wav = tmp_path / "long_source.wav"
    snd.save(str(long_wav), "WAV")

    app = SimpleNamespace()
    app.speaker_manager = SpeakerManager()
    spk = app.speaker_manager.speakers[app.speaker_manager.active_speaker_id]
    spk.name = "发音人甲"
    spk.tab_mode = "单条长音频"
    spk.long_audio_path = str(long_wav)
    spk.pending_long_snd = snd
    spk.project_groups = ["第一组", "空分组"]
    spk.last_params = {
        "pitch_floor": 75,
        "pitch_ceiling": 600,
        "voicing_threshold": 0.25,
        "formant_max_hz": 5500.0,
        "formant_count": 5,
        "formant_window_length": 0.025,
        "formant_pre_emphasis": 50.0,
        "formant_sample_strategy": "整段11点",
        "show_f3": False,
        "analysis_mode": "f0",
    }

    # Global 15-second pitch array (1500 frames)
    full_xs = np.linspace(0.0, 15.0, 1500)
    full_freqs = np.full_like(full_xs, 220.0)

    spk.items["item1"] = {
        "id": "item1",
        "label": "测试词",
        "group": "第一组",
        "macro_start": 1.0,
        "macro_end": 1.5,
        "start": 1.05,
        "end": 1.45,
        "pitch_data": {"xs": full_xs.copy(), "freqs": full_freqs.copy()},
        # Default param (should be stripped) + overridden param (should go to param_overrides)
        "pitch_floor": 75,
        "pitch_ceiling": 450,
        # Runtime keys (should be stripped from project.json)
        "preview_f0": [220.0, float("nan")],
        "warnings": ["temp_warn"],
        "has_empty_data": False,
        # Redundant item_* keys (should be normalized to note/tags)
        "item_note": "条目备注",
        "item_tags": ["标签1"],
    }

    app._preserved_top_level = {
        "groups": [
            {
                "id": "grp_1",
                "name": "第一组",
                "items": [
                    {"id": "item1", "label": "测试词", "note": ""},
                    {"id": "item_unrecorded", "label": "未录音词", "note": "保留"},
                ],
            },
            {"id": "grp_empty", "name": "空分组", "items": []},
        ],
        "meta": {"session_id": "exp_123"},
    }
    app.tree_panel = SimpleNamespace(project_groups=["第一组", "空分组"])
    app.custom_script_runs = []

    pm = ProjectManager(app, workspace_dir=str(ws))
    try:
        assert pm.save_to_workspace() is True
        proj_json_path = ws / "project.json"
        saved_state = json.loads(proj_json_path.read_text(encoding="utf-8"))

        # D-04: Top-level groups and custom meta preserved, and item note synced to groups
        assert "groups" in saved_state
        assert saved_state["meta"] == {"session_id": "exp_123"}
        assert len(saved_state["groups"]) == 2
        assert saved_state["groups"][0]["items"][0]["note"] == "条目备注"
        assert saved_state["groups"][0]["items"][1]["id"] == "item_unrecorded"

        saved_spk = saved_state["speakers"][spk.id]
        assert saved_spk["project_groups"] == ["第一组", "空分组"]
        assert "long_pitch_data_file" in saved_spk

        saved_item = saved_spk["items"]["item1"]
        # B-04: Runtime keys excluded
        for rk in ("preview_f0", "preview_formants", "warnings", "has_empty_data", "preview_segment_mismatch"):
            assert rk not in saved_item
        # B-05: Default acoustic params stripped, overridden param stored in param_overrides
        assert "pitch_floor" not in saved_item
        assert "pitch_ceiling" not in saved_item
        assert saved_item["param_overrides"] == {"pitch_ceiling": 450}
        # B-02: Redundant item_note normalized to note
        assert "item_note" not in saved_item
        assert saved_item["note"] == "条目备注"

        # B-01: Per-item npz is sliced to [macro_start - 0.05, macro_end + 0.05], not 15s
        item_npz = ws / saved_item["pitch_data_file"]
        with np.load(item_npz) as loaded_npz:
            xs_slice = loaded_npz["xs"]
            assert len(xs_slice) < 100
            assert xs_slice[0] >= 0.94
            assert xs_slice[-1] <= 1.56

        # Restore and verify
        restored, active_id = pm._build_restored_state(saved_state, workspace_dir=str(ws))
        r_spk = restored[active_id]
        assert r_spk.project_groups == ["第一组", "空分组"]
        r_item = r_spk.items["item1"]
        assert r_item["pitch_ceiling"] == 450
        assert r_item["pitch_floor"] == 75
    finally:
        pm.release_workspace_lock()


def test_d06_long_audio_segment_and_trim_preserves_multisyllable_bounds(tmp_path):
    """[2.3.1 & 2.3.2 / D-06] _segment_sound_for_item 使用宏观边界，且 _apply_trim_item_audio 平移保留多音节字界。"""
    snd = _make_dummy_sound(duration=2.0)
    item = {
        "label": "北京大学",
        "macro_start": 0.2,
        "macro_end": 1.2,
        "start": 0.3,
        "end": 1.1,
        "chars_bounds": [[0.3, 0.65], [0.7, 1.1]],
        "inner_splits": [0.68],
    }
    part, offset = _segment_sound_for_item(snd, "long", item, with_offset=True)
    assert offset == pytest.approx(0.2)
    assert part.get_total_duration() == pytest.approx(1.0, abs=1e-2)

    # Test _apply_trim_item_audio shifts bounds instead of wiping them
    ws = tmp_path / "trim_ws"
    (ws / "audio" / "spk1").mkdir(parents=True)
    long_wav = ws / "audio" / "spk1" / "long.wav"
    snd.save(str(long_wav), "WAV")

    state = {
        "version": "1.0",
        "speakers": {
            "spk1": {
                "id": "spk1",
                "name": "发音人",
                "tab_mode": "单条长音频",
                "long_audio_path": "audio/spk1/long.wav",
                "items": {
                    "item1": copy.deepcopy(item),
                    "item2": {
                        "label": "另一词",
                        "macro_start": 1.2,
                        "macro_end": 1.8,
                        "start": 1.25,
                        "end": 1.75,
                    },
                },
            }
        },
    }
    records = []
    _apply_trim_item_audio(
        state,
        str(ws),
        {"target": {"item_id": "item1"}, "start": 0.2, "end": 1.2},
        records,
    )
    trimmed = state["speakers"]["spk1"]["items"]["item1"]
    assert trimmed["start"] == pytest.approx(0.1, abs=1e-2)
    assert trimmed["end"] == pytest.approx(0.9, abs=1e-2)
    assert len(trimmed["chars_bounds"]) == 2
    assert trimmed["chars_bounds"][0] == pytest.approx([0.1, 0.45], abs=1e-2)
    assert trimmed["chars_bounds"][1] == pytest.approx([0.5, 0.9], abs=1e-2)
    assert trimmed["inner_splits"] == pytest.approx([0.48], abs=1e-2)

    # Verify project_manager._build_restored_state does not break item2 when item1 has its own path
    app = SimpleNamespace(speaker_manager=SimpleNamespace(speakers={}))
    pm = ProjectManager.__new__(ProjectManager)
    pm.workspace_dir = str(ws)
    pm.app = app
    restored, _ = pm._build_restored_state(state, workspace_dir=str(ws))
    r_spk = restored["spk1"]
    assert r_spk.tab_mode == "单条长音频"
    assert r_spk.items["item1"]["snd"].get_total_duration() == pytest.approx(1.0, abs=1e-2)
    assert r_spk.items["item2"]["snd"].get_total_duration() == pytest.approx(2.0, abs=1e-2)


def test_d07_and_d08_empty_id_and_boundary_normalization(tmp_path):
    """[2.3.3 / D-08 & 2.3.4 / D-07] 空 ID 防坍缩，且 normalize_independent_item_boundaries 保留儿化音与自定义分割线。"""
    # 1. Empty ID wordlist normalization and adapt_project_state
    doc = {
        "groups": [
            {
                "name": "测试组",
                "items": [
                    {"id": "", "label": "词A"},
                    {"id": "", "label": "词B"},
                    {"id": "", "label": "词C"},
                ],
            }
        ]
    }
    norm_doc = normalize_wordlist_document(doc)
    ids = [it["id"] for it in norm_doc["groups"][0]["items"]]
    assert len(set(ids)) == 3
    assert "" not in ids

    ws = tmp_path / "adapt_ws"
    ws.mkdir()
    state = {
        "version": "1.0",
        "groups": [
            {
                "name": "测试组",
                "items": [
                    {"id": "", "label": "词A"},
                    {"id": "", "label": "词B"},
                ],
            }
        ],
        "speakers": {
            "spk1": {
                "id": "spk1",
                "name": "发音人",
                "items": {
                    "k1": {"id": "", "label": "词A", "group": "测试组"},
                    "k2": {"id": "", "label": "词B", "group": "测试组"},
                },
            }
        },
    }
    adapted, _, _ = adapt_project_state(state, str(ws))
    assert len(adapted["speakers"]["spk1"]["items"]) == 2
    assert "" not in adapted["speakers"]["spk1"]["items"]
    assert MAX_ARCHIVE_MEMBERS >= 100000

    # 2. D-08: Erhua 2-character label with 1 valid segment and custom inner_splits in gap
    erhua_item = {
        "word": "花儿",
        "start": 0.05,
        "end": 0.55,
        "chars_bounds": [[0.05, 0.55]],
        "inner_splits": [],
    }
    normalize_independent_item_boundaries(erhua_item, 0.6)
    assert len(erhua_item["chars_bounds"]) == 1
    assert erhua_item["chars_bounds"] == [[0.05, 0.55]]

    disyllable_item = {
        "word": "妈妈",
        "start": 0.1,
        "end": 0.9,
        "chars_bounds": [[0.1, 0.4], [0.5, 0.9]],
        "inner_splits": [0.46],  # Inside voiceless gap [0.4, 0.5], not equal to 0.4!
    }
    normalize_independent_item_boundaries(disyllable_item, 1.0)
    assert disyllable_item["inner_splits"] == [pytest.approx(0.46)]


def test_d10_d11_d12_b06_cache_hash_overrides_and_script_dedup():
    """[D-10, D-11, D-12, B-06] 音频缓存深拷贝与参数键、_safe_token 哈希防碰撞、param_overrides 契约与脚本运行去重。"""
    # D-10: Audio cache key includes params and deep-copies arrays
    app = PhoneticsApp.__new__(PhoneticsApp)
    app.speaker_manager = SpeakerManager()
    arr = np.array([100.0, 200.0])
    app._set_audio_cache("dummy.wav", {"pitch_data": {"freqs": arr}}, {"pitch_floor": 75})
    cached_75 = app._get_audio_cache("dummy.wav", {"pitch_floor": 75})
    cached_75["pitch_data"]["freqs"][0] = 999.0
    # Original cache must remain unpolluted
    assert app._get_audio_cache("dummy.wav", {"pitch_floor": 75})["pitch_data"]["freqs"][0] == 100.0
    # Different params must miss the cache instead of returning stale results
    assert app._get_audio_cache("dummy.wav", {"pitch_floor": 120}) is None

    # D-11: _safe_token hash suffix for long strings
    pm = ProjectManager.__new__(ProjectManager)
    prefix = "batch_wl_" + ("长句测试词条" * 15)
    t1 = pm._safe_token(prefix + "_1001")
    t2 = pm._safe_token(prefix + "_1002")
    assert len(t1) <= 80
    assert len(t2) <= 80
    assert t1 != t2

    # D-12: param_overrides unified across project_patch and report_generator
    spk = {"last_params": {"pitch_floor": 75, "formant_max_hz": 5500.0}}
    item = {"param_overrides": {"pitch_floor": 110, "formant_max_hz": 4800.0}}
    merged = _item_params(spk, item, None)
    assert merged["pitch_floor"] == 110
    assert merged["formant_max_hz"] == 4800.0
    assert get_item_override_param(item, "pitch_floor", 75) == 110
    assert get_item_override_param(item, "formant_max_hz", 5500.0) == 4800.0
    maj = get_majority_item_params({"i1": item}, spk["last_params"])
    assert maj["pitch_floor"] == 110
    assert maj["formant_max_hz"] == 4800.0

    # B-06: custom_script_runs SHA-256 deduplication
    runs = []
    _append_or_update_script_run(runs, {"code_sha256": "abc123", "code": "print(1)", "run_time": "t1", "summary": "s1"})
    _append_or_update_script_run(runs, {"code_sha256": "abc123", "code": "print(1)", "run_time": "t2", "summary": "s2"})
    assert len(runs) == 1
    assert runs[0]["run_count"] == 2
    assert runs[0]["run_time"] == "t2"
    assert runs[0]["summary"] == "s2"
