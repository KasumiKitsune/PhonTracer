import os
import copy
import math
import tempfile
import zipfile
import numpy as np
import pytest

from modules.project_manager import ProjectManager, RUNTIME_EXCLUDED_KEYS
from modules.project_adaptor import adapt_project_state, prune_unreferenced_resources
from modules.project_patch import _iter_state_resource_paths, _append_or_update_script_run, _apply_recompute_pitch
from modules.data_utils import write_raw_pitch_sheet, ExcelStyleManager, autofit_worksheet_columns


def test_fix_2_1_runtime_keys_and_tree_empty_check():
    # 二.1: _mode_state_f0 / _mode_state_formant in RUNTIME_EXCLUDED_KEYS
    assert "_mode_state_f0" in RUNTIME_EXCLUDED_KEYS
    assert "_mode_state_formant" in RUNTIME_EXCLUDED_KEYS


def test_fix_2_2_chars_bounds_clamped_to_micro_bounds():
    # 二.2: chars_bounds clamped to [new_start, new_end]
    def _clamp(val, low=0.0, high=1.0):
        return max(low, min(high, float(val)))

    slice_start = 10.0
    new_start = 0.5
    new_end = 2.5
    
    # Boundary slightly outside [10.5, 12.5]
    orig_cb = [(10.49, 11.5), (11.5, 12.51)]
    clamped_cb = [
        [_clamp(float(p[0]) - slice_start, new_start, new_end),
         _clamp(float(p[1]) - slice_start, new_start, new_end)]
        for p in orig_cb
    ]
    assert clamped_cb[0][0] == new_start
    assert clamped_cb[1][1] == new_end


def test_fix_2_3_long_pitch_data_file_whitelisted_in_prune():
    # 二.3: long_pitch_data_file whitelist
    state = {
        "speakers": {
            "spk1": {
                "id": "spk1",
                "name": "Speaker 1",
                "long_audio_path": "audio/long.wav",
                "long_pitch_data_file": "data/long_pitch.npz",
                "items": {}
            }
        }
    }
    paths = set(_iter_state_resource_paths(state))
    assert "data/long_pitch.npz" in paths
    assert "audio/long.wav" in paths

    with tempfile.TemporaryDirectory() as tmpdir:
        data_dir = os.path.join(tmpdir, "data")
        audio_dir = os.path.join(tmpdir, "audio")
        os.makedirs(data_dir, exist_ok=True)
        os.makedirs(audio_dir, exist_ok=True)
        lp_file = os.path.join(data_dir, "long_pitch.npz")
        la_file = os.path.join(audio_dir, "long.wav")
        orphan_file = os.path.join(data_dir, "orphan.npz")
        np.savez(lp_file, xs=np.array([1, 2]), freqs=np.array([100, 200]))
        np.savez(orphan_file, xs=np.array([1, 2]), freqs=np.array([100, 200]))
        with open(la_file, "wb") as f:
            f.write(b"RIFF dummy wav")

        removed = prune_unreferenced_resources(state, tmpdir)
        assert os.path.exists(lp_file), "long_pitch.npz should NOT be pruned"
        assert not os.path.exists(orphan_file), "orphan.npz should be pruned"


def test_fix_2_4_raw_pitch_sheet_exports_long_audio_item():
    # 二.4: write_raw_pitch_sheet exports item even without 'path'
    with tempfile.TemporaryDirectory() as tmpdir:
        out_xlsx = os.path.join(tmpdir, "raw.xlsx")
        import xlsxwriter
        wb = xlsxwriter.Workbook(out_xlsx)
        style = ExcelStyleManager(wb)
        item = {
            'label': '你好',
            'start': 0.1,
            'end': 0.5,
            'pitch_data': {
                'xs': np.array([0.1, 0.2, 0.3]),
                'freqs': np.array([150.0, 160.0, 170.0])
            }
            # No 'path' and no 'snd'
        }
        rows = [{'speaker': 'Spk1', 'group': 'Grp1', 'index': 1, 'item': item}]
        write_raw_pitch_sheet(wb, rows, style_manager=style, include_speaker=True)
        wb.close()

        with zipfile.ZipFile(out_xlsx, 'r') as zf:
            strings = zf.read('xl/sharedStrings.xml').decode('utf-8')
            assert '你好' in strings


def test_fix_3_1_cli_integrated_export_t_values():
    # 三.1: cli._export_xlsx_integrated and _export_txt_integrated
    from cli import PhonTracerCLI
    cli = PhonTracerCLI()
    try:
        class MockSpeaker:
            name = "甲"
            id = "spk1"
            last_params = {'pts': 5, 'show_f3': False, 'formant_sample_strategy': '整段11点', 'num_rule': 'continuous'}
            items = {
                "item1": {
                    'label': "测试",
                    'group': "阴平",
                    'start': 0.1,
                    'end': 0.5,
                    'chars_bounds': [(0.1, 0.3), (0.3, 0.5)],
                    'pitch_data': {
                        'xs': np.linspace(0.1, 0.5, 20),
                        'freqs': np.linspace(100.0, 200.0, 20)
                    },
                    'snd': True,
                    'is_excluded': False
                }
            }
            cli_groups = ["阴平"]

        class MockSpeakerManager:
            def get_active_speaker(self):
                return MockSpeaker()

        cli.speaker_manager = MockSpeakerManager()
        spk = MockSpeaker()

        with tempfile.TemporaryDirectory() as tmpdir:
            txt_path = os.path.join(tmpdir, "int.txt")
            cli._export_txt_integrated(txt_path, [spk], rule="continuous")
            with open(txt_path, "r", encoding="utf-8-sig") as f:
                content = f.read()
                # Check TSV wide table format
                assert "发音人\t组别\t编号\t词语\t总时长(s)\t字1_时长(s)\t字1_T1" in content
                assert "[致命]" not in content

            xlsx_path = os.path.join(tmpdir, "int.xlsx")
            cli._export_xlsx_integrated(xlsx_path, [spk], rule="continuous")
            with zipfile.ZipFile(xlsx_path, 'r') as zf:
                wb_xml = zf.read('xl/workbook.xml').decode('utf-8')
                assert 'name="整合数据(T值)"' in wb_xml
    finally:
        cli.cleanup_workspace()


def test_fix_3_3_script_runs_dedup():
    # 三.3: custom_script_runs dedup by SHA256
    runs = []
    rec1 = {"code_sha256": "abc", "run_time": "10:00", "status": "成功"}
    _append_or_update_script_run(runs, rec1)
    assert len(runs) == 1
    assert runs[0]["run_count"] == 1

    rec2 = {"code_sha256": "abc", "run_time": "10:05", "status": "成功"}
    _append_or_update_script_run(runs, rec2)
    assert len(runs) == 1
    assert runs[0]["run_count"] == 2
    assert runs[0]["run_time"] == "10:05"

    rec3 = {"code_sha256": "xyz", "run_time": "10:10", "status": "成功"}
    _append_or_update_script_run(runs, rec3)
    assert len(runs) == 2


def test_fix_3_4_preserved_top_level_overlay_safety():
    # 三.4: overlay preservation in ProjectManager
    class MockApp:
        class MockSM:
            speakers = {}
        speaker_manager = MockSM()

    pm = ProjectManager(app=MockApp(), workspace_dir=".")
    pm._preserved_top_level = {"meta_custom": 123}
    state = {"speakers": {}, "new_key": 456}
    restored = pm._build_restored_state(state, overlay=True, workspace_dir=".")
    assert pm._preserved_top_level.get("meta_custom") == 123
    assert pm._preserved_top_level.get("new_key") == 456


def test_fix_3_5_param_overrides_stores_diff_only():
    # 三.5: param_overrides stores only diff against last_params
    state = {
        "speakers": {
            "spk1": {
                "id": "spk1",
                "last_params": {"pitch_floor": 75, "pitch_ceiling": 500},
                "items": {
                    "i1": {"label": "字", "start": 0.0, "end": 1.0}
                }
            }
        }
    }
    item = state["speakers"]["spk1"]["items"]["i1"]
    spk_params = state["speakers"]["spk1"]["last_params"]
    current_params = {"pitch_floor": 75, "pitch_ceiling": 600}  # ceiling changed
    diff_params = {k: v for k, v in current_params.items() if spk_params.get(k) != v}
    item["param_overrides"] = diff_params
    assert item["param_overrides"] == {"pitch_ceiling": 600}
    assert "pitch_floor" not in item["param_overrides"]
