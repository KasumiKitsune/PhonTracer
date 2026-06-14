import textgrid

from modules.textgrid_converter import (
    PAIR_MODE_ADJACENT,
    TextGridMapping,
    convert_textgrid,
    inspect_textgrid,
    preview_textgrid_conversion,
)


def _write_textgrid(path, tiers, max_time=1.0):
    tg = textgrid.TextGrid(maxTime=max_time)
    for name, intervals in tiers:
        tier = textgrid.IntervalTier(name=name, minTime=0.0, maxTime=max_time)
        for start, end, label in intervals:
            tier.add(start, end, label)
        tg.append(tier)
    tg.write(str(path))
    return path


def _non_empty_intervals(tier):
    return [iv for iv in tier if iv.mark.strip()]


def _tier(tg, name):
    for tier in tg.tiers:
        if tier.name == name:
            return tier
    raise AssertionError(f"未找到层：{name}")


def test_convert_card_example_to_standard_textgrid(tmp_path):
    src = _write_textgrid(
        tmp_path / "card.TextGrid",
        [
            ("字", [(0.0, 1.0, "卡")]),
            ("音素", [(0.0, 0.25, "k"), (0.25, 1.0, "a")]),
            ("核心", [(0.30, 0.72, "a")]),
        ],
    )
    out = tmp_path / "card_converted.TextGrid"

    preview = convert_textgrid(
        str(src),
        str(out),
        TextGridMapping(item_tier="字", core_tier="核心"),
        group_overrides={"0": "实验组"},
    )

    assert len(preview.items) == 1
    assert preview.items[0].label == "卡"
    assert preview.items[0].group == "实验组"
    assert preview.items[0].char_bounds == [(0.30, 0.72, "卡")]

    converted = textgrid.TextGrid.fromFile(str(out))
    assert [tier.name for tier in converted.tiers] == ["groups", "words", "chars"]
    words = _non_empty_intervals(_tier(converted, "words"))
    groups = _non_empty_intervals(_tier(converted, "groups"))
    chars = _non_empty_intervals(_tier(converted, "chars"))
    assert [(iv.minTime, iv.maxTime, iv.mark) for iv in words] == [(0.30, 0.72, "卡")]
    assert [(iv.minTime, iv.maxTime, iv.mark) for iv in groups] == [(0.30, 0.72, "实验组")]
    assert [(iv.minTime, iv.maxTime, iv.mark) for iv in chars] == [(0.30, 0.72, "卡")]


def test_group_overrides_are_used_when_group_tier_is_missing(tmp_path):
    src = _write_textgrid(
        tmp_path / "ungrouped.TextGrid",
        [
            ("words", [(0.0, 0.4, "卡"), (0.4, 0.9, "达")]),
            ("core", [(0.1, 0.3, "a"), (0.5, 0.8, "a")]),
        ],
    )

    preview = preview_textgrid_conversion(
        str(src),
        {"item_tier": "words", "core_tier": "core"},
        group_overrides={"0": "阴平+去声", "1": "阳平+上声"},
    )

    assert [item.group for item in preview.items] == ["阴平+去声", "阳平+上声"]


def test_two_character_label_uses_two_core_intervals_for_chars(tmp_path):
    src = _write_textgrid(
        tmp_path / "pair_label.TextGrid",
        [
            ("groups", [(0.0, 1.0, "阴平+去声")]),
            ("words", [(0.0, 1.0, "卡达")]),
            ("core", [(0.15, 0.35, "a"), (0.62, 0.90, "a")]),
        ],
    )

    preview = preview_textgrid_conversion(
        str(src),
        TextGridMapping(item_tier="words", core_tier="core", group_tier="groups"),
    )

    assert len(preview.items) == 1
    item = preview.items[0]
    assert item.label == "卡达"
    assert item.core_start == 0.15
    assert item.core_end == 0.90
    assert item.char_bounds == [(0.15, 0.35, "卡"), (0.62, 0.90, "达")]
    assert preview.tone_pair_report["eligible_count"] == 1


def test_adjacent_single_char_items_can_be_paired_for_two_character_groups(tmp_path):
    src = _write_textgrid(
        tmp_path / "adjacent_chars.TextGrid",
        [
            ("groups", [(0.0, 1.0, "阴平+去声")]),
            ("words", [(0.0, 0.45, "卡"), (0.45, 1.0, "达")]),
            ("core", [(0.10, 0.35, "a"), (0.58, 0.86, "a")]),
        ],
    )

    preview = preview_textgrid_conversion(
        str(src),
        {
            "item_tier": "words",
            "core_tier": "core",
            "group_tier": "groups",
            "pair_mode": PAIR_MODE_ADJACENT,
        },
    )

    assert len(preview.items) == 1
    item = preview.items[0]
    assert item.id == "0+1"
    assert item.label == "卡达"
    assert item.char_bounds == [(0.10, 0.35, "卡"), (0.58, 0.86, "达")]
    assert preview.tone_pair_report["eligible_count"] == 1


def test_wordlist_records_make_pairing_selective_and_apply_group(tmp_path):
    src = _write_textgrid(
        tmp_path / "wordlist_guided.TextGrid",
        [
            ("words", [(0.0, 0.2, "卡"), (0.2, 0.5, "达"), (0.5, 0.7, "妈")]),
            ("core", [(0.05, 0.16, "a"), (0.28, 0.42, "a"), (0.55, 0.66, "a")]),
        ],
        max_time=0.8,
    )

    preview = preview_textgrid_conversion(
        str(src),
        {
            "item_tier": "words",
            "core_tier": "core",
            "pair_mode": PAIR_MODE_ADJACENT,
        },
        wordlist_records=[{"label": "卡/达", "group": "阴平+去声"}],
    )

    assert [item.label for item in preview.items] == ["卡/达", "妈"]
    assert preview.items[0].group == "阴平+去声"
    assert preview.items[0].match_note == "按辅助字表合并"
    assert preview.items[1].label == "妈"
    assert any("辅助字表" in msg or "字表" in msg for msg in preview.items[1].warnings)


def test_wordlist_records_fill_group_when_group_tier_is_missing(tmp_path):
    src = _write_textgrid(
        tmp_path / "wordlist_group.TextGrid",
        [
            ("words", [(0.0, 0.5, "巴"), (0.5, 1.0, "飞")]),
            ("core", [(0.1, 0.3, "a"), (0.6, 0.8, "ei")]),
        ],
    )

    preview = preview_textgrid_conversion(
        str(src),
        TextGridMapping(item_tier="words", core_tier="core"),
        wordlist_records=[
            {"label": "巴", "group": "a韵母组"},
            {"label": "飞", "group": "ei韵母组"},
        ],
    )

    assert [item.group for item in preview.items] == ["a韵母组", "ei韵母组"]
    assert all(item.match_note == "按辅助字表补组" for item in preview.items)


def test_wordlist_records_fill_group_when_group_tier_is_missing_ungrouped(tmp_path):
    src = _write_textgrid(
        tmp_path / "wordlist_group_ungrouped.TextGrid",
        [
            ("words", [(0.0, 0.5, "巴"), (0.5, 1.0, "飞")]),
            ("core", [(0.1, 0.3, "a"), (0.6, 0.8, "ei")]),
        ],
    )

    preview = preview_textgrid_conversion(
        str(src),
        TextGridMapping(item_tier="words", core_tier="core"),
        wordlist_records=[
            {"label": "巴", "group": "未分组"},
            {"label": "飞", "group": "ei韵母组"},
        ],
    )

    assert [item.group for item in preview.items] == ["未分组", "ei韵母组"]
    assert all(item.match_note == "按辅助字表补组" for item in preview.items)


def test_missing_core_and_invalid_tone_pair_report_warn_without_crashing(tmp_path):
    src = _write_textgrid(
        tmp_path / "warnings.TextGrid",
        [
            ("words", [(0.0, 0.5, "卡达"), (0.5, 1.0, "妈")]),
            ("core", [(0.0, 1.0, "")]),
        ],
    )

    summary = inspect_textgrid(str(src))
    assert [tier["name"] for tier in summary["tiers"]] == ["words", "core"]

    preview = preview_textgrid_conversion(
        str(src),
        TextGridMapping(item_tier="words", core_tier="core"),
    )

    assert len(preview.items) == 2
    assert any("未找到对应核心区间" in msg for msg in preview.items[0].warnings)
    assert any("核心区间数量与字/音节数不一致" in msg for msg in preview.items[0].warnings)
    assert preview.tone_pair_report["supported"] is False
    assert preview.tone_pair_report["invalid_group_count"] >= 1
