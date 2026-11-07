import os
import sys
import json
import time
import zipfile
import threading
import numpy as np
import pytest
from types import SimpleNamespace

from modules.script_runner import (
    validate_ast,
    check_script_safety,
    run_custom_script,
    SAFE_BUILTINS,
    ALLOWED_BUILTINS,
    _SafeOSPathNamespace,
    _SafeOSNamespace,
)
from modules.project_adaptor import (
    MAX_ARCHIVE_MEMBERS,
    MAX_SINGLE_MEMBER_BYTES,
    MAX_TOTAL_UNCOMPRESSED_BYTES,
    validate_project_archive_members,
    safe_extract_zip,
)
from modules.project_manager import ProjectManager, RUNTIME_EXCLUDED_KEYS
from modules.speaker_manager import SpeakerManager


def test_phase_5_2_ast_blocks_dunder_and_private_attributes():
    """验证 AST 检查直接拦截一切以 _ 开头的私有或双下划线属性访问"""
    # Test dunder attribute access on object
    code1 = """
def run(ctx):
    x = ().__class__.__bases__[0].__subclasses__()
    return None
"""
    with pytest.raises(ValueError, match="禁止访问私有或双下划线属性"):
        validate_ast(code1)

    with pytest.raises(ValueError, match="禁止访问私有或双下划线属性"):
        check_script_safety(code1)

    code2 = """
def run(ctx):
    secret = ctx._cancel_event
    return None
"""
    with pytest.raises(ValueError, match="禁止访问私有或双下划线属性"):
        validate_ast(code2)


def test_phase_5_2_getattr_removed_from_safe_builtins():
    """验证 getattr 已从 SAFE_BUILTINS / ALLOWED_BUILTINS 移除"""
    assert "getattr" not in SAFE_BUILTINS
    assert "getattr" not in ALLOWED_BUILTINS

    code = """
def run(ctx):
    c = getattr((), "__class__")
    return None
"""
    # AST will catch getattr or check_script_safety will catch getattr as forbidden call
    with pytest.raises(ValueError):
        validate_ast(code)


def test_phase_5_2_safe_os_path_namespace_sandbox_escape_blocked():
    """验证 _SafeOSPathNamespace 仅暴露安全纯函数，无法通过 os.path.os 逃逸"""
    safe_path = _SafeOSPathNamespace()
    safe_os = _SafeOSNamespace()

    # Safe operations should work
    assert safe_path.join("a", "b").replace("\\", "/") == "a/b"
    assert safe_path.basename("foo/bar.txt") == "bar.txt"
    assert safe_path.splitext("bar.txt")[1] == ".txt"
    assert safe_os.path.join("x", "y").replace("\\", "/") == "x/y"

    # Attempting to access underlying os / sys must raise AttributeError
    with pytest.raises(AttributeError):
        _ = safe_path.os

    with pytest.raises(AttributeError):
        _ = safe_path.sys

    with pytest.raises(AttributeError):
        _ = safe_os.system

    with pytest.raises(AttributeError):
        _ = safe_path._anything


def test_phase_5_2_script_runner_with_safe_os():
    """验证自定义脚本中可安全使用 os.path.join 但无法访问 os.system"""
    code_safe = """
def run(ctx):
    p = os.path.join("audio", "sample.wav")
    return ctx.table([[p]], ["路径"], title="测试")
"""
    res, logs, err = run_custom_script(code_safe, [], timeout=5)
    assert err is None
    assert res.rows[0][0].replace("\\", "/") == "audio/sample.wav"

    code_malicious = """
def run(ctx):
    os.system("echo hacked")
    return None
"""
    res, logs, err = run_custom_script(code_malicious, [], timeout=5)
    assert err is not None
    assert "安全限制" in err or "AttributeError" in err


def test_phase_5_2_zip_bomb_constants_and_validation(tmp_path):
    """验证 Zip 炸弹防护常量及体积校验"""
    assert MAX_ARCHIVE_MEMBERS == 100000
    assert MAX_SINGLE_MEMBER_BYTES == 2 * 1024 * 1024 * 1024
    assert MAX_TOTAL_UNCOMPRESSED_BYTES == 4 * 1024 * 1024 * 1024

    zip_file = tmp_path / "bomb.zip"
    with zipfile.ZipFile(zip_file, "w") as zf:
        zf.writestr("project.json", "{}")

    with zipfile.ZipFile(zip_file, "r") as zf:
        # Mock member with oversized single member
        info = zf.getinfo("project.json")
        orig_size = info.file_size
        info.file_size = MAX_SINGLE_MEMBER_BYTES + 1
        with pytest.raises(ValueError, match="归档成员体积超过安全上限"):
            validate_project_archive_members(zf)

        info.file_size = orig_size
        validate_project_archive_members(zf)


def test_phase_5_1_dirty_tracking_in_runtime_excluded_keys():
    """验证 _dirty_pitch 与 _dirty_formant 被计入运行时排除列表"""
    assert "_dirty_pitch" in RUNTIME_EXCLUDED_KEYS
    assert "_dirty_formant" in RUNTIME_EXCLUDED_KEYS


def test_phase_5_1_incremental_save_and_dirty_tracking(tmp_path):
    """验证 incremental=True 时仅重新写入脏标记条目，且写完后清除脏标记"""
    ws = tmp_path / "ws"
    ws.mkdir()

    app = SimpleNamespace()
    app.speaker_manager = SpeakerManager()
    spk = app.speaker_manager.speakers[app.speaker_manager.active_speaker_id]
    spk.name = "发音人1"
    spk.tab_mode = "多条独立音频"
    spk.pending_batch_paths = []
    spk.last_params = {"analysis_mode": "f0", "pitch_floor": 75, "pitch_ceiling": 500}

    # Item with initial pitch & formant
    xs = np.linspace(0.0, 0.5, 50)
    freqs = np.full_like(xs, 200.0)
    spk.items["item1"] = {
        "id": "item1",
        "label": "词1",
        "pitch_data": {"xs": xs, "freqs": freqs},
        "formant_data": {"xs": xs, "f1": freqs, "f2": freqs * 2},
        "_dirty_pitch": True,
        "_dirty_formant": True,
    }

    pm = ProjectManager(app, workspace_dir=str(ws))

    # Initial non-incremental save
    pm.save_to_workspace(incremental=False)

    # After save, dirty flags must be cleared
    assert spk.items["item1"].get("_dirty_pitch") is False
    assert spk.items["item1"].get("_dirty_formant") is False

    # Check project.json does NOT contain _dirty_pitch or _dirty_formant
    with open(ws / "project.json", "r", encoding="utf-8") as f:
        pdata = json.load(f)
    saved_item = pdata["speakers"][spk.id]["items"]["item1"]
    assert "_dirty_pitch" not in saved_item
    assert "_dirty_formant" not in saved_item

    # Check npz files exist
    npz_pitch = ws / "data" / f"{spk.id}_item1.npz"
    npz_formant = ws / "data" / f"{spk.id}_item1_formant.npz"
    assert npz_pitch.exists()
    assert npz_formant.exists()

    mtime_pitch = npz_pitch.stat().st_mtime_ns
    mtime_formant = npz_formant.stat().st_mtime_ns

    # Small delay
    time.sleep(0.05)

    # Call incremental save when item is NOT dirty -> npz must NOT be touched
    pm.save_to_workspace(incremental=True)
    assert npz_pitch.stat().st_mtime_ns == mtime_pitch
    assert npz_formant.stat().st_mtime_ns == mtime_formant

    # Now dirty only pitch
    spk.items["item1"]["_dirty_pitch"] = True
    time.sleep(0.05)
    pm.save_to_workspace(incremental=True)

    # Pitch was rewritten, formant was not
    assert spk.items["item1"]["_dirty_pitch"] is False
    assert npz_pitch.stat().st_mtime_ns > mtime_pitch
    assert npz_formant.stat().st_mtime_ns == mtime_formant


def test_phase_5_3_isolated_workspace_allocation(tmp_path):
    """验证当工作区锁冲突时，allocate_isolated_workspace 能安全分配独立工作区"""
    base_dir = tmp_path / "app_data"
    base_dir.mkdir()
    shared_ws = str(base_dir / "workspace")

    pm1 = ProjectManager(None, workspace_dir=shared_ws)
    assert pm1._lock_acquired is True

    # pm2 on same workspace cannot acquire lock
    pm2 = ProjectManager(None, workspace_dir=shared_ws)
    assert pm2._lock_acquired is False

    # pm2 allocates isolated workspace
    isolated_dir = pm2.allocate_isolated_workspace()
    assert os.path.isdir(isolated_dir)
    assert f"workspace_{os.getpid()}" in isolated_dir
    assert pm2._lock_acquired is True
    assert pm2.auto_save_enabled is False

    # Clean up
    pm2.release_workspace_lock()
    pm1.release_workspace_lock()
