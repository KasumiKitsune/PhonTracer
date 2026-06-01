import os
import json
import shutil
import tempfile
import zipfile
import pytest

from modules.report_generator import (
    export_reports_from_teproj,
    calculate_sha256,
    format_speaker_name,
    generate_markdown_report,
    get_majority_item_params,
    parse_wav_header_from_bytes,
    write_excel_archive,
)

def test_speaker_name_formatting():
    assert format_speaker_name("发音人 1") == "发音人 1"
    assert format_speaker_name("发音人1") == "发音人1"
    assert format_speaker_name("张三") == "发音人 张三"

def test_parse_wav_header():
    assert parse_wav_header_from_bytes(b"invalid header data") == (None, None, None)
    # Valid WAV fmt chunk (24 bytes total starting from 'fmt ')
    # Format PCM (1), channels 1, sample rate 44100, bits 16
    fmt_data = b"fmt \x10\x00\x00\x00\x01\x00\x01\x00D\xac\x00\x00\xd8X\x01\x00\x02\x00\x10\x00"
    assert parse_wav_header_from_bytes(fmt_data) == (44100, 16, 1)

def test_report_generation_from_sample():
    teproj_path = os.path.join(os.path.dirname(__file__), "..", "Example", "02_普通话单字与双字调", "普通话单字与双字调.teproj")
    assert os.path.exists(teproj_path), f"Example project not found at {teproj_path}"
    
    # Create temp directory for output
    temp_dir = tempfile.mkdtemp()
    try:
        progress_updates = []
        files, base_name = export_reports_from_teproj(
            teproj_path,
            temp_dir,
            export_markdown=True,
            export_excel=True,
            include_cache_details=True,
            progress_callback=lambda value, message: progress_updates.append((value, message)),
        )
        
        assert len(files) == 2
        assert progress_updates[0] == (0.08, "正在读取工程元数据...")
        assert progress_updates[-1] == (1.0, "报告导出完成")
        md_file = files[0] if files[0].endswith(".md") else files[1]
        xlsx_file = files[1] if files[1].endswith(".xlsx") else files[0]
        
        # Verify markdown content
        assert os.path.exists(md_file)
        with open(md_file, "r", encoding="utf-8") as f:
            md_content = f.read()
            
        assert "# PhonTracer 声学分析研究方法报告与数据审计档案" in md_content
        assert "## 1. 工程概览与元数据" in md_content
        assert "## 2. 研究方法摘要 (自然语言)" in md_content
        
        # Verify speaker formatting and actual parameters
        assert "发音人: 混合-男" in md_content
        assert "发音人: 混合-女" in md_content
        assert "发音人 混合-男 的声调分析采用 70–300 Hz" in md_content
        assert "发音人 混合-女 的声调分析采用 75–600 Hz" in md_content
        
        # Check digitisation parameters are parsed and consensus found
        assert "录音数字化共识参数" in md_content
        assert "24 kHz 采样率" in md_content
        assert "16-bit" in md_content
        assert "单声道" in md_content
        
        # Check conditional formatting (this is an F0 project, Formant block should be hidden)
        assert "- **核心基频算法与阈值**:" in md_content
        assert "- **共振峰配置**:" not in md_content
        
        # Check save_time fallback (should not be '未记录', should be a valid fallback timestamp)
        assert "工程最后保存时间" in md_content
        assert "2026-05-24 19:11:56" in md_content
        
        # Verify Excel file exists and has size
        assert os.path.exists(xlsx_file)
        assert os.path.getsize(xlsx_file) > 0
        
        # Verify SHA-256 computation
        calculated_hash = calculate_sha256(teproj_path)
        assert len(calculated_hash) == 64
        assert calculated_hash in md_content
        
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_local_param_differences_use_majority_included_items():
    state = {
        "version": "1.0",
        "speakers": {
            "sp1": {
                "name": "测试者",
                "last_params": {
                    "analysis_mode": "f0",
                    "pitch_floor": 200,
                    "pitch_ceiling": 500,
                    "voicing_threshold": 0.40,
                },
                "items": {
                    "common_1": {
                        "label": "甲",
                        "group": "测试组",
                        "pitch_floor": 75,
                        "pitch_ceiling": 300,
                        "voicing_threshold": 0.25,
                        "start": 0.1,
                        "end": 0.4,
                    },
                    "common_2": {
                        "label": "乙",
                        "group": "测试组",
                        "pitch_floor": 75,
                        "pitch_ceiling": 300,
                        "voicing_threshold": 0.25,
                        "start": 0.5,
                        "end": 0.8,
                    },
                    "minority": {
                        "label": "丙",
                        "group": "测试组",
                        "pitch_floor": 120,
                        "pitch_ceiling": 300,
                        "voicing_threshold": 0.25,
                        "start": 0.9,
                        "end": 1.2,
                    },
                    "excluded": {
                        "label": "丁",
                        "group": "测试组",
                        "pitch_floor": 999,
                        "pitch_ceiling": 999,
                        "voicing_threshold": 0.99,
                        "start": 1.3,
                        "end": 1.6,
                        "is_excluded": True,
                        "exclusion_reason": "发音错误",
                    },
                },
            }
        },
    }

    majority = get_majority_item_params(
        state["speakers"]["sp1"]["items"],
        state["speakers"]["sp1"]["last_params"],
    )
    assert majority["pitch_floor"] == 75
    assert majority["pitch_ceiling"] == 300
    assert majority["voicing_threshold"] == 0.25

    temp_dir = tempfile.mkdtemp()
    try:
        teproj_path = os.path.join(temp_dir, "majority.teproj")
        with zipfile.ZipFile(teproj_path, "w") as z:
            z.writestr("project.json", json.dumps(state, ensure_ascii=False))

        with zipfile.ZipFile(teproj_path, "r") as z:
            md_content = generate_markdown_report(teproj_path, state, z)

        assert "## 4. 条目级参数偏离（以多数条目为基准）" in md_content
        assert "基频下限: 120 Hz (多数值 75 Hz)" in md_content
        assert "基频下限: 75 Hz (多数值 200 Hz)" not in md_content
        assert "基频下限: 999 Hz" not in md_content

        xlsx_path = os.path.join(temp_dir, "majority.xlsx")
        write_excel_archive(teproj_path, state, xlsx_path)
        with zipfile.ZipFile(xlsx_path, "r") as z:
            shared_strings = z.read("xl/sharedStrings.xml").decode("utf-8")

        assert "基频下限: 120Hz (多数值: 75Hz)" in shared_strings
        assert "基频下限: 999Hz" not in shared_strings
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
