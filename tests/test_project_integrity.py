import os
import json
import pytest
import shutil
import tempfile
from modules.project_integrity import (
    calculate_json_sha256,
    calculate_file_sha256,
    append_audit_log,
    update_manifest,
    verify_audit_chain,
    verify_project_integrity,
)

@pytest.fixture
def temp_workspace():
    # 创建临时工作区
    workspace = tempfile.mkdtemp()
    yield workspace
    shutil.rmtree(workspace)

def test_calculate_json_sha256():
    # 测试 JSON 规范化哈希
    obj1 = {"b": 2, "a": 1}
    obj2 = {"a": 1, "b": 2}
    assert calculate_json_sha256(obj1) == calculate_json_sha256(obj2)

def test_calculate_file_sha256(temp_workspace):
    # 测试文件哈希计算
    test_file = os.path.join(temp_workspace, "test.txt")
    with open(test_file, "w", encoding="utf-8") as f:
        f.write("hello world")

    h1 = calculate_file_sha256(test_file)
    assert len(h1) == 64

    # 修改内容后哈希应改变
    with open(test_file, "w", encoding="utf-8") as f:
        f.write("hello world!")
    h2 = calculate_file_sha256(test_file)
    assert h1 != h2

def test_audit_log_hash_chain(temp_workspace):
    # 测试审计日志哈希链
    h1 = append_audit_log(temp_workspace, "project_created", {"user": "test"})
    h2 = append_audit_log(temp_workspace, "audio_saved", {"filename": "1.wav"})
    h3 = append_audit_log(temp_workspace, "project_saved", {})

    assert h1 != h2
    assert h2 != h3

    # 验证日志文件的哈希链结构
    audit_file = os.path.join(temp_workspace, "logs", "audit.jsonl")
    entries = []
    with open(audit_file, "r", encoding="utf-8") as f:
        for line in f:
            entries.append(json.loads(line.strip()))

    assert len(entries) == 3
    assert entries[0]["prev_hash"] == "0000000000000000000000000000000000000000000000000000000000000000"
    assert entries[1]["prev_hash"] == entries[0]["hash"]
    assert entries[2]["prev_hash"] == entries[1]["hash"]
    assert entries[0]["hash"] == h1
    assert entries[1]["hash"] == h2
    assert entries[2]["hash"] == h3
    assert verify_audit_chain(temp_workspace) == h3

def test_update_and_verify_manifest(temp_workspace):
    # 初始化一个虚拟项目
    proj_json = os.path.join(temp_workspace, "project.json")
    with open(proj_json, "w", encoding="utf-8") as f:
        json.dump({"name": "test"}, f)

    audio_dir = os.path.join(temp_workspace, "audio")
    os.makedirs(audio_dir)
    audio_file = os.path.join(audio_dir, "1.wav")
    with open(audio_file, "w") as f:
        f.write("fake wav data")

    data_dir = os.path.join(temp_workspace, "data")
    os.makedirs(data_dir)
    cache_file = os.path.join(data_dir, "1.npz")
    with open(cache_file, "w") as f:
        f.write("fake cache data")

    # 无清单验证 -> legacy_unverified
    status, warnings, details = verify_project_integrity(temp_workspace)
    assert status == "legacy_unverified"

    # 更新清单并追加日志
    append_audit_log(temp_workspace, "project_saved", {})
    update_manifest(temp_workspace)

    # 有清单且一致 -> verified
    status, warnings, details = verify_project_integrity(temp_workspace)
    assert status == "verified"
    assert len(warnings) == 0

    # 破坏缓存文件 -> stale_caches
    with open(cache_file, "w") as f:
        f.write("tampered cache data")
    status, warnings, details = verify_project_integrity(temp_workspace)
    assert status == "stale_caches"
    assert "data/1.npz" in details["corrupt_caches"]

    # 破坏音频文件 -> corrupt
    with open(audio_file, "w") as f:
        f.write("tampered wav data")
    status, warnings, details = verify_project_integrity(temp_workspace)
    assert status == "corrupt"
    assert "audio/1.wav" in details["corrupt_audio"]

    # 缺失音频文件 -> corrupt
    os.remove(audio_file)
    status, warnings, details = verify_project_integrity(temp_workspace)
    assert status == "corrupt"
    assert "audio/1.wav" in details["missing_audio"]


def test_project_json_hash_mismatch_is_corrupt(temp_workspace):
    project_file = os.path.join(temp_workspace, "project.json")
    with open(project_file, "w", encoding="utf-8") as file_obj:
        json.dump({"name": "原始工程"}, file_obj)
    update_manifest(temp_workspace)

    with open(project_file, "w", encoding="utf-8") as file_obj:
        json.dump({"name": "已篡改工程"}, file_obj)

    status, warnings, details = verify_project_integrity(temp_workspace)
    assert status == "corrupt"
    assert "project_json_invalid" in details["integrity_errors"]
    assert any("project.json" in warning for warning in warnings)


def test_tampered_audit_chain_is_corrupt_and_cannot_be_extended(temp_workspace):
    project_file = os.path.join(temp_workspace, "project.json")
    with open(project_file, "w", encoding="utf-8") as file_obj:
        json.dump({"name": "测试工程"}, file_obj)
    append_audit_log(temp_workspace, "project_created", {})
    update_manifest(temp_workspace)

    audit_file = os.path.join(temp_workspace, "logs", "audit.jsonl")
    with open(audit_file, "w", encoding="utf-8") as file_obj:
        file_obj.write("{broken json}\n")

    status, _, details = verify_project_integrity(temp_workspace)
    assert status == "corrupt"
    assert "audit_chain_invalid" in details["integrity_errors"]
    with pytest.raises(ValueError, match="不是有效 JSON"):
        append_audit_log(temp_workspace, "should_not_append", {})


def test_manifest_path_cannot_escape_workspace(temp_workspace):
    project_file = os.path.join(temp_workspace, "project.json")
    with open(project_file, "w", encoding="utf-8") as file_obj:
        json.dump({"name": "测试工程"}, file_obj)
    outside_file = os.path.join(os.path.dirname(temp_workspace), "outside.bin")
    with open(outside_file, "wb") as file_obj:
        file_obj.write(b"outside")
    try:
        update_manifest(temp_workspace)
        manifest_file = os.path.join(temp_workspace, "integrity", "manifest.json")
        with open(manifest_file, "r", encoding="utf-8") as file_obj:
            manifest = json.load(file_obj)
        manifest["files"]["../outside.bin"] = calculate_file_sha256(outside_file)
        with open(manifest_file, "w", encoding="utf-8") as file_obj:
            json.dump(manifest, file_obj)

        status, _, details = verify_project_integrity(temp_workspace)
        assert status == "corrupt"
        assert "../outside.bin" in details["unsafe_paths"]
    finally:
        if os.path.exists(outside_file):
            os.remove(outside_file)
