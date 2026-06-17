import csv
import json
import zipfile

from toolkit import ToolkitApp


def _write_project(path, project_data, files):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("project.json", json.dumps(project_data, ensure_ascii=False, indent=2))
        for name, content in files.items():
            zf.writestr(name, content)


def test_project_extract_keeps_raw_structure_and_adds_named_audio_view(tmp_path):
    uuid_name = "12345678-1234-1234-1234-123456789abc_spk1_item1.wav"
    project_data = {
        "version": "1.0",
        "speakers": {
            "spk1": {
                "id": "spk1",
                "name": "发音人A",
                "long_audio_path": None,
                "pending_batch_paths": [f"audio/{uuid_name}"],
                "items": {
                    "item1": {
                        "label": "妈妈",
                        "group": "实验组",
                        "path": f"audio/{uuid_name}",
                        "start": 0.0,
                        "end": 0.8,
                    }
                },
            }
        },
    }
    archive = tmp_path / "demo.teproj"
    _write_project(
        archive,
        project_data,
        {f"audio/{uuid_name}": b"wav-bytes"},
    )

    extract_root = tmp_path / "demo"
    raw_root = extract_root / "原始工程结构"
    named_root = extract_root / "按名称整理"
    index_path = extract_root / "文件索引.csv"

    extracted_count = ToolkitApp._safe_extract_project_archive(str(archive), str(raw_root))
    named_count, index_count = ToolkitApp._export_named_project_files(
        project_data,
        str(raw_root),
        str(named_root),
        str(index_path),
    )

    assert extracted_count == 2
    assert named_count == 1
    assert index_count == 1
    assert (raw_root / "audio" / uuid_name).read_bytes() == b"wav-bytes"

    named_file = named_root / "音频" / "01_发音人A" / "条目" / "001_实验组_妈妈.wav"
    assert named_file.read_bytes() == b"wav-bytes"

    with open(index_path, "r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    assert rows[0]["原始路径"] == f"audio/{uuid_name}"
    assert rows[0]["整理后路径"] == "按名称整理/音频/01_发音人A/条目/001_实验组_妈妈.wav"
    assert rows[0]["状态"] == "已复制"
