import json
import math
import zipfile

import numpy as np
import pytest

from modules.project_manager import read_project_metadata_from_archive
from modules.project_patch import ProjectPatchError, apply_project_patch_to_teproj, summarize_project_patch
from modules.script_api import FigureResult, ProjectPatchResult, TableResult, build_dataset_snapshot
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


def _write_test_wav(path, duration=0.08, frequency=220.0):
    parselmouth = pytest.importorskip("parselmouth")
    sample_rate = 8000
    sample_count = max(1, int(sample_rate * duration))
    xs = np.arange(sample_count, dtype=np.float64) / sample_rate
    samples = np.sin(2 * math.pi * frequency * xs)
    snd = parselmouth.Sound(np.array([samples]), sampling_frequency=sample_rate)
    snd.save(str(path), "WAV")
    return path.read_bytes()


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


def test_script_runner_allows_parselmouth_but_blocks_system_imports():
    code = """
import parselmouth

def run(ctx):
    return ctx.table([[ctx.parselmouth is parselmouth]], ["可用"], title="Parselmouth 导入")
"""

    result, logs, err = run_custom_script(code, [], timeout=5)

    assert err is None
    assert logs == []
    assert isinstance(result, TableResult)
    assert result.rows == [[True]]

    bad_code = """
import os

def run(ctx):
    return ctx.table([], [], title="不应运行")
"""
    result, logs, err = run_custom_script(bad_code, [], timeout=5)
    assert result is None
    assert err is not None
    assert "os" in err
    assert logs and "安全检查失败" in logs[0]

    bad_code = """
import subprocess

def run(ctx):
    return ctx.table([], [], title="不应运行")
"""
    result, logs, err = run_custom_script(bad_code, [], timeout=5)
    assert result is None
    assert err is not None
    assert "subprocess" in err
    assert logs and "安全检查失败" in logs[0]


def test_custom_script_can_draw_spectrogram_from_teproj_item_audio(tmp_path):
    src = tmp_path / "spectrogram_input.teproj"
    wav_path = tmp_path / "item.wav"
    project = _minimal_project()
    item = project["speakers"]["spk1"]["items"]["item1"]
    item["path"] = "audio/item.wav"
    item["end"] = 0.08
    _write_project(src, project, {"audio/item.wav": _write_test_wav(wav_path, duration=0.08)})
    items = build_dataset_snapshot(str(src))

    code = """
def run(ctx):
    item = ctx.dataset.items[0]
    snd = ctx.load_item_sound(item)
    spec = ctx.spectrogram_data(snd, max_frequency=3000.0)
    fig, ax = ctx.plt.subplots(figsize=(4, 3))
    ax.pcolormesh(spec["x"], spec["y"], spec["db"], vmin=spec["vmin"], vmax=spec["vmax"], cmap="Greys", shading="auto")
    ax.set_title("受控语谱图")
    return ctx.figure(fig, filename="spectrogram.png", title="受控语谱图")
"""

    result, logs, err = run_custom_script(code, items, timeout=5, teproj_path=str(src))

    assert err is None
    assert logs == []
    assert isinstance(result, FigureResult)
    assert result.title == "受控语谱图"


def test_custom_script_can_use_spectrogram_grid_helper(tmp_path):
    src = tmp_path / "spectrogram_grid_input.teproj"
    wav_path = tmp_path / "item.wav"
    project = _minimal_project()
    item = project["speakers"]["spk1"]["items"]["item1"]
    item["path"] = "audio/item.wav"
    item["end"] = 0.08
    item["item_meta"] = {"IPA": "i"}
    _write_project(src, project, {"audio/item.wav": _write_test_wav(wav_path, duration=0.08)})
    items = build_dataset_snapshot(str(src))

    code = """
def run(ctx):
    fig = ctx.plot_spectrogram_grid(ctx.dataset.items, columns=1, max_items=1, max_frequency=3000.0)
    return ctx.figure(fig, filename="spectrogram_grid.png", title="多宫格语谱图")
"""

    result, logs, err = run_custom_script(code, items, timeout=5, teproj_path=str(src))

    assert err is None
    assert logs == []
    assert isinstance(result, FigureResult)
    assert result.title == "多宫格语谱图"


def test_custom_script_loads_long_audio_item_segment(tmp_path):
    src = tmp_path / "long_audio_input.teproj"
    wav_path = tmp_path / "long.wav"
    project = _minimal_project()
    spk = project["speakers"]["spk1"]
    spk["tab_mode"] = "长音频切分"
    spk["long_audio_path"] = "audio/long.wav"
    item = spk["items"]["item1"]
    item.pop("path", None)
    item["start"] = 0.02
    item["end"] = 0.07
    _write_project(src, project, {"audio/long.wav": _write_test_wav(wav_path, duration=0.12)})
    items = build_dataset_snapshot(str(src))

    code = """
def run(ctx):
    item = ctx.dataset.items[0]
    snd = ctx.load_item_sound(item)
    return ctx.table([[round(snd.get_total_duration(), 3)]], ["时长"], title="长音频切片")
"""

    result, logs, err = run_custom_script(code, items, timeout=5, teproj_path=str(src))

    assert err is None
    assert logs == []
    assert isinstance(result, TableResult)
    assert 0.045 <= result.rows[0][0] <= 0.055


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


def test_apply_project_patch_rejects_overwriting_source(tmp_path):
    src = tmp_path / "input.teproj"
    _write_project(src, _minimal_project())
    patch = ProjectPatchResult([
        {
            "op": "set_item_fields",
            "target": {"speaker_id": "spk1", "item_id": "item1"},
            "fields": {"group": "新组"},
        }
    ])

    with pytest.raises(ProjectPatchError, match="不能覆盖原"):
        apply_project_patch_to_teproj(str(src), patch, str(src))


def test_apply_project_patch_validates_missing_resources(tmp_path):
    src = tmp_path / "bad_input.teproj"
    dst = tmp_path / "bad_output.teproj"
    project = _minimal_project()
    project["speakers"]["spk1"]["items"]["item1"]["path"] = "audio/missing.wav"
    _write_project(src, project)
    patch = ProjectPatchResult([
        {
            "op": "set_item_fields",
            "target": {"speaker_id": "spk1", "item_id": "item1"},
            "fields": {"group": "新组"},
        }
    ])

    with pytest.raises(FileNotFoundError, match="missing.wav"):
        apply_project_patch_to_teproj(str(src), patch, str(dst))
    assert not dst.exists()


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


def test_chart_prompts_document_parselmouth_spectrogram_api():
    project = _minimal_project()
    agent_prompt = generate_ai_prompt(
        project,
        {
            "script_type": "chart",
            "prompt_mode": "Agent协作",
            "agent_detail_level": "精简",
            "agent_project_summary_mode": "包含精简工程摘要",
            "custom_desc": "参考图是多宫格语谱图",
        },
    )
    option_prompt = generate_ai_prompt(
        project,
        {
            "script_type": "chart",
            "prompt_mode": "参数选项",
            "goal": "画多宫格语谱图",
            "chart_style": "语谱图",
        },
    )

    for prompt in (agent_prompt, option_prompt):
        assert "parselmouth" in prompt
        assert "ctx.load_item_sound" in prompt
        assert "ctx.plot_spectrogram_grid" in prompt
        assert "脚本无法访问工程文件路径" not in prompt
        assert "不应该读取任何本地文件" not in prompt


def test_trim_item_audio_creates_new_managed_audio(tmp_path):
    src = tmp_path / "audio_input.teproj"
    dst = tmp_path / "audio_output.teproj"
    wav_path = tmp_path / "item.wav"

    project = _minimal_project()
    item = project["speakers"]["spk1"]["items"]["item1"]
    item["path"] = "audio/item.wav"
    item["end"] = 0.1
    _write_project(src, project, {"audio/item.wav": _write_test_wav(wav_path, duration=0.1)})

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


def test_split_project_prunes_unreferenced_audio_resources(tmp_path):
    src = tmp_path / "split_input.teproj"
    dst = tmp_path / "split_output.teproj"
    wav_a = tmp_path / "a.wav"
    wav_b = tmp_path / "b.wav"
    project = _minimal_project()
    spk = project["speakers"]["spk1"]
    spk["pending_batch_paths"] = ["audio/a.wav", "audio/b.wav"]
    spk["items"]["item1"]["path"] = "audio/a.wav"
    spk["items"]["item1"]["end"] = 0.08
    spk["items"]["item2"] = {
        "label": "ma2",
        "group": "旧组",
        "path": "audio/b.wav",
        "start": 0.0,
        "end": 0.08,
        "is_excluded": False,
    }
    _write_project(
        src,
        project,
        {
            "audio/a.wav": _write_test_wav(wav_a, duration=0.08, frequency=220.0),
            "audio/b.wav": _write_test_wav(wav_b, duration=0.08, frequency=330.0),
        },
    )

    patch = ProjectPatchResult([
        {
            "op": "split_project",
            "item_ids": ["item1"],
            "name": "只保留第一项",
        }
    ])

    apply_project_patch_to_teproj(str(src), patch, str(dst))
    state, names = read_project_metadata_from_archive(dst)
    spk_out = state["speakers"]["spk1"]

    assert set(spk_out["items"]) == {"item1"}
    assert spk_out["pending_batch_paths"] == ["audio/a.wav"]
    assert "audio/a.wav" in names
    assert "audio/b.wav" not in names
