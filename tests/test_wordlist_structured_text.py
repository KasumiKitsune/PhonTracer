from modules.wordlist_v2 import build_document_from_structured_text, flatten_wordlist_document, looks_like_csv_wordlist_text, looks_like_structured_wordlist_text


def test_structured_text_builds_advanced_wordlist_fields():
    doc = build_document_from_structured_text(
        """
# title: 普通话声调实验
@group 阴平-去声 | tags=双字组；目标组 | note=前阴后去 | meta.比较口径=声调组合
@defaults | source=AI推断，需人工复核 | tags=目标词 | aliases=默认别名 | 结构=双字组

妈骂 | pinyin=ma1 ma4 | condition=A | note=核心材料
花化 | tags=填充词 | aliases=hua | pinyin=hua1 hua4 | rhyme=ua
""",
        title="默认标题",
    )

    assert doc["title"] == "普通话声调实验"
    group = doc["groups"][0]
    assert group["name"] == "阴平-去声"
    assert group["tags"] == ["双字组", "目标组"]
    assert group["note"] == "前阴后去"
    assert group["meta"]["比较口径"] == "声调组合"

    first = group["items"][0]
    assert first["label"] == "妈骂"
    assert first["tags"] == ["目标词"]
    assert first["aliases"] == ["默认别名"]
    assert first["note"] == "核心材料"
    assert first["metadata_source"] == "AI推断，需人工复核"
    assert first["meta"]["拼音"] == "ma1 ma4"
    assert first["meta"]["结构"] == "双字组"
    assert first["meta"]["condition"] == "A"

    second = group["items"][1]
    assert second["tags"] == ["填充词"]
    assert second["aliases"] == ["hua"]
    assert second["meta"]["拼音"] == "hua1 hua4"
    assert second["meta"]["韵母"] == "ua"


def test_structured_text_uses_ungrouped_when_no_group_declared():
    doc = build_document_from_structured_text("妈 | tone=阴平\n麻 | tone=阳平\n")
    groups, flat_words, records = flatten_wordlist_document(doc)

    assert groups[0]["group"] == "未分组"
    assert flat_words == ["妈", "麻"]
    assert records[0]["item_meta"]["声调"] == "阴平"


def test_wordlist_text_auto_detection_helpers():
    assert looks_like_structured_wordlist_text("# title: 示例\n@group A\n妈\n")
    assert looks_like_csv_wordlist_text("组名,词项,标签\nA,妈,目标词\n")
    assert not looks_like_csv_wordlist_text("妈,麻\n花,化\n")
