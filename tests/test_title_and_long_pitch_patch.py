import os
import shutil
import tempfile
import numpy as np

from modules.speaker_manager import SpeakerManager
from modules.project_manager import ProjectManager
from modules.wordlist_v2 import (
    build_document_from_structured_text,
    looks_like_structured_wordlist_text,
    flatten_wordlist_document,
)


def test_title_directives_parsed_properly_in_structured_text():
    sample_text = """title: 普通话双字组声调基频格局与连读变调实验字表
@group 11_阴阴 | tags=主测试；声调对比；基频格局 | note=普通话阴平+阴平（55-55）双字组基频提取
@defaults | source=AI推断，需人工复核 | tags=目标词；双字组；阴阴；满调提取 | 结构=双字组 | 首字声调=阴平 | 末字声调=阴平 | tone=55-55
播音
高峰
"""
    assert looks_like_structured_wordlist_text(sample_text) is True
    doc = build_document_from_structured_text(sample_text)
    assert doc["title"] == "普通话双字组声调基频格局与连读变调实验字表"
    assert len(doc["groups"]) == 1
    assert doc["groups"][0]["name"] == "11_阴阴"
    items = [it["label"] for it in doc["groups"][0]["items"]]
    assert items == ["播音", "高峰"]

    groups, flat_words, records = flatten_wordlist_document(doc)
    assert flat_words == ["播音", "高峰"]
    assert "title" not in flat_words[0]


def test_various_title_formats():
    variants = [
        "title: 普通话实验字表\n@group 11\n词1",
        "title：普通话实验字表\n@group 11\n词1",
        "# title: 普通话实验字表\n@group 11\n词1",
        "#title: 普通话实验字表\n@group 11\n词1",
        "@title 普通话实验字表\n@group 11\n词1",
        "@title: 普通话实验字表\n@group 11\n词1",
        "标题: 普通话实验字表\n@group 11\n词1",
        "标题：普通话实验字表\n@group 11\n词1",
        "# 标题: 普通话实验字表\n@group 11\n词1",
    ]
    for var in variants:
        doc = build_document_from_structured_text(var)
        assert doc.get("title") == "普通话实验字表"
        items = [it["label"] for g in doc["groups"] for it in g["items"]]
        assert items == ["词1"]


class DummyApp:
    def __init__(self, ws_dir):
        self.speaker_manager = SpeakerManager()
        self.active_speaker = self.speaker_manager.get_active_speaker()
        self.export_numbering_rule_value = "continuous"
        self.custom_script_runs = []
        self._preserved_top_level = {}
        self.current_project_path = None


def test_long_pitch_data_file_persists_across_multiple_saves():
    tmp_dir = tempfile.mkdtemp(prefix="test_long_pitch_persist_")
    try:
        app = DummyApp(tmp_dir)
        pm = ProjectManager(app, workspace_dir=tmp_dir)

        spk = app.active_speaker
        spk.name = "女1"
        spk.tab_mode = "单条长音频"

        long_xs = np.linspace(0.0, 15.0, 1500)
        long_freqs = np.ones(1500) * 220.0

        spk.items = {
            "I001": {
                "label": "播音",
                "group": "11_阴阴",
                "macro_start": 0.5,
                "macro_end": 1.5,
                "start": 0.6,
                "end": 1.4,
                "pitch_data": {"xs": long_xs, "freqs": long_freqs},
            }
        }

        # 第一次保存
        pm.save_to_workspace()
        assert spk.long_pitch_data_file is not None
        assert "long_pitch.npz" in spk.long_pitch_data_file

        data_files_1 = os.listdir(os.path.join(tmp_dir, "data"))
        assert any("long_pitch" in f for f in data_files_1)

        # 第二次保存（各 item 已裁切，且未重新提取长基频）
        pm.save_to_workspace()
        assert spk.long_pitch_data_file is not None

        data_files_2 = os.listdir(os.path.join(tmp_dir, "data"))
        assert any("long_pitch" in f for f in data_files_2), "long_pitch.npz was pruned incorrectly!"

        import json
        with open(os.path.join(tmp_dir, "project.json"), "r", encoding="utf-8") as f:
            proj = json.load(f)
        assert proj["speakers"][spk.id].get("long_pitch_data_file") == spk.long_pitch_data_file
    finally:
        pm.release_workspace_lock()
        shutil.rmtree(tmp_dir, ignore_errors=True)
