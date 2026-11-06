import os
import sys
import math
import struct
import tempfile
import zipfile
import json
import numpy as np
import pytest

def test_updater_user_agent_and_safe_after():
    from modules.updater import fetch_latest_release, check_for_updates_async
    import urllib.request

    # 验证 User-Agent 字符串设置
    original_request = urllib.request.Request
    recorded_headers = {}

    def mock_request(url, headers=None, **kwargs):
        if headers:
            recorded_headers.update(headers)
        return original_request("http://127.0.0.1:9", headers=headers, **kwargs)

    urllib.request.Request = mock_request
    try:
        fetch_latest_release(timeout=0.01)
    except Exception:
        pass
    finally:
        urllib.request.Request = original_request

    assert "PhonTracerUpdater/1.0" in recorded_headers.get("User-Agent", "")

    # 验证 safe_after 对已销毁/非法 root 的容错保护
    class DeadRoot:
        def winfo_exists(self):
            return False
        def after(self, *args, **kwargs):
            raise RuntimeError("Should not be called")

    # 不抛异常即为通过
    check_for_updates_async(DeadRoot())


def test_update_dialog_imports():
    import modules.update_dialog as ud
    from modules.version import APP_NAME, __version__
    assert hasattr(ud, "APP_NAME")
    assert hasattr(ud, "__version__")
    assert ud.APP_NAME == APP_NAME


def test_project_preview_none_boundaries():
    from toolkit import ToolkitApp

    project_data = {
        "version": "1.0",
        "active_speaker_id": "spk1",
        "speakers": {
            "spk1": {
                "name": "测试发音人",
                "tab_mode": "多条独立音频",
                "last_params": {"pitch_floor": 75, "pitch_ceiling": 300, "pts": 11, "method": "ac"},
                "items": {
                    "item1": {
                        "label": "未切分条目",
                        "start": None,
                        "end": None,
                    },
                    "item2": {
                        "label": "正常条目",
                        "start": 0.123,
                        "end": 0.456,
                    }
                }
            }
        }
    }

    # 测试 toolkit.py 中的 format_project_preview
    toolkit_instance = ToolkitApp.__new__(ToolkitApp)
    preview_toolkit = toolkit_instance.format_project_preview(project_data, [])
    assert "未切分" in preview_toolkit
    assert "0.123s ~   0.456s" in preview_toolkit


def test_make_safe_excel_sheet_name():
    from modules.data_utils import make_safe_excel_sheet_name

    # 1. 禁用字符过滤
    dirty = "发音人:1\\2/3?4*5[6]7"
    safe = make_safe_excel_sheet_name(dirty)
    assert not any(c in safe for c in r":\/?*[]")
    assert safe == "发音人_1_2_3_4_5_6_7"

    # 2. 首尾单引号去除
    assert make_safe_excel_sheet_name("'SpeakerA'") == "SpeakerA"

    # 3. 空名兜底
    assert make_safe_excel_sheet_name("") == "Sheet"
    assert make_safe_excel_sheet_name("   ") == "Sheet"
    assert make_safe_excel_sheet_name(None) == "Sheet"

    # 4. 长度限制 (<= 31)
    super_long = "这是一个超级超级超级长长长长长长长长长长长长长长长长的发音人名称"
    safe_long = make_safe_excel_sheet_name(super_long)
    assert len(safe_long) <= 31

    # 5. used_names 大小写不敏感判重
    used = set()
    n1 = make_safe_excel_sheet_name("Speaker", used_names=used)
    n2 = make_safe_excel_sheet_name("SPEAKER", used_names=used)
    n3 = make_safe_excel_sheet_name("speaker", used_names=used)
    assert n1 == "Speaker"
    assert n2 == "SPEAKER_2"
    assert n3 == "speaker_3"
    assert len(used) == 3

    # 6. 超长且去重仍不超过 31 字符
    used_long = set()
    l1 = make_safe_excel_sheet_name(super_long, used_names=used_long)
    l2 = make_safe_excel_sheet_name(super_long, used_names=used_long)
    assert len(l1) <= 31
    assert len(l2) <= 31
    assert l1 != l2


def test_load_compatible_sound(tmp_path):
    from modules.audio_core import load_compatible_sound
    import parselmouth

    # 1. 创建标准 WAV
    sample_rate = 16000
    duration = 0.2
    xs = np.linspace(0, duration, int(sample_rate * duration))
    samples = np.sin(2 * np.pi * 440 * xs)
    snd_orig = parselmouth.Sound(samples, sampling_frequency=sample_rate)

    wav_path = str(tmp_path / "test.wav")
    snd_orig.save(wav_path, "WAV")

    snd_loaded = load_compatible_sound(wav_path)
    assert abs(snd_loaded.get_total_duration() - duration) < 1e-3

    # 2. 篡改 byte_rate 测试修复
    data = bytearray(open(wav_path, "rb").read())
    fmt_offset = data.index(b"fmt ") + 8
    sr = struct.unpack_from("<I", data, fmt_offset + 4)[0]
    block_align = struct.unpack_from("<H", data, fmt_offset + 12)[0]
    struct.pack_into("<I", data, fmt_offset + 8, sr * block_align * 2)
    open(wav_path, "wb").write(data)

    snd_repaired = load_compatible_sound(wav_path)
    assert abs(snd_repaired.get_total_duration() - duration) < 1e-3


def test_project_patch_non_split_prune_orphans(tmp_path):
    """验证非拆分 patch 会调用 prune 清理孤儿文件"""
    from modules.project_patch import apply_project_patch_to_teproj

    teproj_path = str(tmp_path / "test.teproj")
    output_path = str(tmp_path / "out.teproj")

    state = {
        "version": "1.0",
        "active_speaker_id": "spk1",
        "speakers": {
            "spk1": {
                "name": "测试发音人",
                "tab_mode": "多条独立音频",
                "items": {
                    "item1": {
                        "id": "item1",
                        "label": "开来",
                        "path": "audio/spk1/active.wav",
                        "pitch_data_file": "data/spk1/active.npz"
                    }
                }
            }
        }
    }

    # 创建真实 wav 与 npz
    import parselmouth
    sample_rate = 8000
    xs = np.linspace(0, 0.1, 800)
    samples = np.sin(2 * np.pi * 440 * xs)
    snd = parselmouth.Sound(samples, sampling_frequency=sample_rate)
    real_wav_path = str(tmp_path / "real.wav")
    snd.save(real_wav_path, "WAV")
    real_wav_bytes = open(real_wav_path, "rb").read()

    real_npz_path = str(tmp_path / "real.npz")
    np.savez_compressed(real_npz_path, xs=np.array([0.0, 0.1]), freqs=np.array([440.0, 440.0]))
    real_npz_bytes = open(real_npz_path, "rb").read()

    # 打包 teproj，多放一个孤儿文件 audio/spk1/orphan.wav
    with zipfile.ZipFile(teproj_path, "w") as z:
        z.writestr("project.json", json.dumps(state, ensure_ascii=False))
        z.writestr("audio/spk1/active.wav", real_wav_bytes)
        z.writestr("audio/spk1/orphan.wav", real_wav_bytes)
        z.writestr("data/spk1/active.npz", real_npz_bytes)
        z.writestr("data/spk1/orphan.npz", real_npz_bytes)

    patch = {
        "operations": [
            {
                "op": "set_item_fields",
                "target": {"item_id": "item1"},
                "fields": {
                    "label": "新词条标签"
                }
            }
        ]
    }

    res = apply_project_patch_to_teproj(teproj_path, patch, output_path=output_path)
    assert os.path.exists(output_path)

    with zipfile.ZipFile(output_path, "r") as z:
        names = z.namelist()
        assert "audio/spk1/active.wav" in names
        assert "data/spk1/active.npz" in names
        assert "audio/spk1/orphan.wav" not in names
        assert "data/spk1/orphan.npz" not in names


def test_workspace_lock_and_cli_isolation(tmp_path):
    from modules.project_manager import ProjectManager
    from cli import PhonTracerCLI

    # 验证 _is_pid_alive 在 Windows/POSIX 下只读探测当前进程且不杀进程
    assert ProjectManager._is_pid_alive(os.getpid()) is True
    assert ProjectManager._is_pid_alive(99999999) is False
    assert ProjectManager._is_pid_alive(-1) is False

    ws_dir = str(tmp_path / "shared_ws")
    pm1 = ProjectManager(None, workspace_dir=ws_dir)
    assert pm1._lock_acquired is True
    assert os.path.exists(os.path.join(ws_dir, "workspace.lock"))

    # 第二个进程/实例尝试挂载同一工作区，应检测到锁冲突
    pm2 = ProjectManager(None, workspace_dir=ws_dir)
    assert pm2._lock_acquired is False

    # pm1 释放锁后，pm2 可重新获取
    pm1.release_workspace_lock()
    assert pm2.acquire_workspace_lock() is True
    pm2.release_workspace_lock()

    # CLI 默认分配独立临时工作区
    cli = PhonTracerCLI()
    try:
        assert cli._temp_workspace_dir is not None
        assert os.path.isdir(cli._temp_workspace_dir)
        default_user_ws = os.path.join(os.path.expanduser("~"), ".phon_tracer", "workspace")
        assert os.path.abspath(cli.project_manager.workspace_dir) != os.path.abspath(default_user_ws)
    finally:
        cli.cleanup_workspace()
        assert not os.path.exists(cli._temp_workspace_dir or "")


def test_toolkit_font_and_thread_gc():
    import gc
    import time
    from toolkit import start_safe_thread, SafeCTkFont, _keep_alive_fonts

    assert gc.isenabled() is True
    done = []

    def background_worker():
        done.append(True)

    start_safe_thread(background_worker)
    time.sleep(0.05)
    assert len(done) == 1
    # GC 绝不应被全局关闭
    assert gc.isenabled() is True


def test_cli_do_run_script_project_patch(tmp_path):
    from cli import PhonTracerCLI
    from modules.project_adaptor import read_project_metadata_from_archive

    teproj_path = str(tmp_path / "cli_test.teproj")
    out_teproj = str(tmp_path / "cli_patched.teproj")
    script_file = str(tmp_path / "patch_script.py")

    # 构建基础测试工程
    state = {
        "version": "1.0",
        "active_speaker_id": "spk1",
        "speakers": {
            "spk1": {
                "name": "CLI测试发音人",
                "tab_mode": "多条独立音频",
                "items": {
                    "item1": {
                        "id": "item1",
                        "label": "原始词条",
                        "start": 0.0,
                        "end": 0.5
                    }
                }
            }
        }
    }
    with zipfile.ZipFile(teproj_path, "w") as z:
        z.writestr("project.json", json.dumps(state, ensure_ascii=False))

    # 编写返回 ProjectPatchResult 的脚本
    script_code = """
def run(ctx):
    return ctx.project_patch([
        {
            "op": "set_item_fields",
            "target": {"item_id": "item1"},
            "fields": {"label": "CLI修改成功"}
        }
    ], title="CLI自动化补丁")
"""
    with open(script_file, "w", encoding="utf-8") as f:
        f.write(script_code)

    cli = PhonTracerCLI()
    try:
        # 加载工程
        cli.project_manager.load_project(teproj_path)
        cli._sync_cli_state_after_project_load()
        # 运行脚本并启用 reload=true
        cli.do_run_script(f"{script_file} out_project={out_teproj} reload=true")
        assert os.path.exists(out_teproj)
        assert cli.items["item1"]["label"] == "CLI修改成功"

        with zipfile.ZipFile(out_teproj, "r") as z:
            patched_state = json.loads(z.read("project.json").decode("utf-8"))
            item1 = patched_state["speakers"]["spk1"]["items"]["item1"]
            assert item1["label"] == "CLI修改成功"
            runs = patched_state.get("custom_script_runs", [])
            assert len(runs) == 1
            assert runs[0]["script_type"] == "data_process"
    finally:
        cli.cleanup_workspace()
