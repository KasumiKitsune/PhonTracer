import json

from toolkit import ToolkitApp


def test_toolkit_loads_plain_csv_and_ptwl_wordlists(tmp_path):
    txt_path = tmp_path / "plain.txt"
    txt_path.write_text("【A组】\n妈 麻\n", encoding="utf-8")

    structured_path = tmp_path / "structured.txt"
    structured_path.write_text(
        "# title: 结构化示例\n@group A组 | tags=目标组\n@defaults | tags=目标词\n妈 | pinyin=ma1\n麻 | pinyin=ma2\n",
        encoding="utf-8",
    )

    csv_path = tmp_path / "table.csv"
    csv_path.write_text("group,label,tags,meta.condition\nA组,妈,目标词,快读\nA组,麻,填充词,慢读\n", encoding="utf-8")

    ptwl_path = tmp_path / "advanced.ptwl"
    ptwl_path.write_text(
        json.dumps(
            {
                "format": "phontracer.wordlist.v2",
                "title": "高级字表",
                "groups": [
                    {
                        "id": "",
                        "name": "A组",
                        "note": "",
                        "tags": [],
                        "meta": {},
                        "items": [
                            {"id": "", "label": "妈", "note": "", "tags": ["目标词"], "aliases": [], "meta": {}, "metadata_source": "人工复核"},
                            {"id": "", "label": "麻", "note": "", "tags": ["填充词"], "aliases": [], "meta": {}, "metadata_source": "人工复核"},
                        ],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    assert ToolkitApp._load_wordlist_source(str(txt_path))["flat_words"] == ["妈", "麻"]
    structured_loaded = ToolkitApp._load_wordlist_source(str(structured_path))
    assert structured_loaded["flat_words"] == ["妈", "麻"]
    assert structured_loaded["document"]["title"] == "结构化示例"
    assert structured_loaded["document"]["groups"][0]["items"][0]["meta"]["拼音"] == "ma1"

    csv_loaded = ToolkitApp._load_wordlist_source(str(csv_path))
    assert csv_loaded["flat_words"] == ["妈", "麻"]
    assert "【A组】" in csv_loaded["text"]

    ptwl_loaded = ToolkitApp._load_wordlist_source(str(ptwl_path))
    assert ptwl_loaded["flat_words"] == ["妈", "麻"]
    assert "【A组】" in ptwl_loaded["text"]
