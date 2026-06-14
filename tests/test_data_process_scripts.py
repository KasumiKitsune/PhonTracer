import json
import math
import zipfile

import numpy as np
import pytest

from modules.project_manager import read_project_metadata_from_archive
from modules.project_patch import apply_project_patch_to_teproj, summarize_project_patch
from modules.script_api import ProjectPatchResult
from modules.script_prompt import generate_ai_prompt
from modules.script_runner import run_custom_script


def _write_project(path, project_data, extra_files=None):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("project.json", json.dumps(project_data, ensure_ascii=False, indent=2).encode("utf-8"))
        for name, content in (extra_files or {}).items():
            zf.writestr(name, content)


def _minimal_project():
    return {
        "version": "1.0",
        "active_speaker_id": "spk1",
        "speakers": {
            "spk1": {
                "id": "spk1",
                "name": "发音人1",
                "last_params": {"pitch_floor": 75, "pitch_ceiling": 500, "analysis_mode": "f0"},
                "tab_mode": "多条独立音频",
                "long_audio_path": None,
                "pending_batch_paths": [],
                "current_macro_segments": [],
                "manual_segments": [],
                "items": {
                    "item1": {
                        "label": "ma1",
                        "group": "旧组",
                        "start": 0.0,
                        "end": 0.4,
                        "is_excluded": False,
                    }
                },
            }
        },
    }


def test_project_patch_result_helpers_and_runner():
    code = """
def run(ctx):
    item = ctx.dataset.items[0]
    op = ctx.set_item_fields(item, {"group": "新组", "item_note": "已整理"}, reason="测试")
    return ctx.project_patch([op], title="整理工程", description="字段更新")
"""
    items = [{
        "speaker_id": "spk1",
        "speaker_name": "发音人1",
        "item_id": "item1",
        "label": "ma1",
        "group": "旧组",
    }]

    result, logs, err = run_custom_script(code, items, timeout=5)

    assert err is None
    assert logs == []
    assert isinstance(result, ProjectPatchResult)
    assert result.operation_count == 1
    assert result.operations[0]["op"] == "set_item_fields"
    assert result.operations[0]["fields"]["group"] == "新组"


def test_data_process_script_still_uses_safety_check():
    code = """
def run(ctx):
    open("x.txt", "w")
    return ctx.project_patch([])
"""

    result, logs, err = run_custom_script(code, [], timeout=5)

    assert result is None
    assert err is not None
    assert "open" in err
    assert logs and "安全检查失败" in logs[0]


def test_apply_set_item_fields_to_teproj(tmp_path):
    src = tmp_path / "input.teproj"
    dst = tmp_path / "output.teproj"
    _write_project(src, _minimal_project())
    patch = ProjectPatchResult([
        {
            "op": "set_item_fields",
            "target": {"speaker_id": "spk1", "item_id": "item1", "label": "ma1"},
            "fields": {"group": "新组", "item_meta": {"条件": "A"}},
            "reason": "测试字段写回",
        }
    ], title="字段写回")

    result = apply_project_patch_to_teproj(
        str(src),
        patch,
        str(dst),
        run_record={"script_name": "字段整理", "script_type": "data_process", "outputs": []},
    )

    assert result["output_path"] == str(dst)
    state, _names = read_project_metadata_from_archive(dst)
    item = state["speakers"]["spk1"]["items"]["item1"]
    assert item["group"] == "新组"
    assert item["item_meta"] == {"条件": "A"}
    assert state["custom_script_runs"][0]["script_type"] == "data_process"


def test_project_patch_summary_and_empty_result():
    patch = ProjectPatchResult([], title="空处理")
    summary = summarize_project_patch(patch)

    assert summary["operation_count"] == 0
    assert summary["affected_item_count"] == 0


def test_data_process_prompt_uses_project_patch_documentation():
    prompt = generate_ai_prompt(
        _minimal_project(),
        {
            "script_type": "data_process",
            "prompt_mode": "Agent协作",
            "agent_detail_level": "详细",
            "agent_plan_count": "4",
            "agent_project_summary_mode": "包含精简工程摘要",
            "custom_desc": "优先重算失败条目",
        },
    )

    assert "ctx.project_patch" in prompt
    assert "数据处理脚本" in prompt
    assert "另存为新的 `.teproj`" in prompt
    assert "横轴" not in prompt
    assert "纵轴" not in prompt
    assert "推荐图表数量" not in prompt


def test_trim_item_audio_creates_new_managed_audio(tmp_path):
    parselmouth = pytest.importorskip("parselmouth")

    src = tmp_path / "audio_input.teproj"
    dst = tmp_path / "audio_output.teproj"
    samples = np.sin(np.linspace(0, math.pi * 8, 800, dtype=np.float64))
    snd = parselmouth.Sound(np.array([samples]), sampling_frequency=8000)
    wav_path = tmp_path / "item.wav"
    snd.save(str(wav_path), "WAV")

    project = _minimal_project()
    item = project["speakers"]["spk1"]["items"]["item1"]
    item["path"] = "audio/item.wav"
    item["end"] = 0.1
    _write_project(src, project, {"audio/item.wav": wav_path.read_bytes()})

    patch = ProjectPatchResult([
        {
            "op": "trim_item_audio",
            "target": {"speaker_id": "spk1", "item_id": "item1", "label": "ma1"},
            "start": 0.01,
            "end": 0.05,
            "padding": 0.0,
            "reason": "裁剪测试",
        }
    ], title="裁剪音频")

    apply_project_patch_to_teproj(str(src), patch, str(dst))
    state, names = read_project_metadata_from_archive(dst)
    new_item = state["speakers"]["spk1"]["items"]["item1"]

    assert new_item["path"].startswith("audio/")
    assert new_item["path"] != "audio/item.wav"
    assert new_item["path"] in names
    assert new_item["start"] == 0.0
    assert 0.0 < new_item["end"] < 0.1
