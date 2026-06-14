import json
import shlex

import textgrid

from cli import PhonTracerCLI


def _写_textgrid(path, tiers, max_time=1.0):
    tg = textgrid.TextGrid(maxTime=max_time)
    for name, intervals in tiers:
        tier = textgrid.IntervalTier(name=name, minTime=0.0, maxTime=max_time)
        for start, end, label in intervals:
            tier.add(start, end, label)
        tg.append(tier)
    tg.write(str(path))
    return path


def _非空(tier):
    return [(round(iv.minTime, 4), round(iv.maxTime, 4), iv.mark) for iv in tier if iv.mark.strip()]


def _层(tg, name):
    for tier in tg.tiers:
        if tier.name == name:
            return tier
    raise AssertionError(f"未找到层：{name}")


def _引号(path):
    return shlex.quote(str(path).replace("\\", "/"))


def _运行_cli(capsys, command_name, arg):
    cli = object.__new__(PhonTracerCLI)
    getattr(cli, command_name)(arg)
    output = capsys.readouterr().out.strip().splitlines()[-1]
    return json.loads(output)


def test_cli_textgrid_inspect_returns_recommendations(tmp_path, capsys):
    src = _写_textgrid(
        tmp_path / "层摘要.TextGrid",
        [
            ("组别", [(0.0, 1.0, "阴平+去声")]),
            ("词项", [(0.0, 1.0, "卡达")]),
            ("核心", [(0.1, 0.3, "a"), (0.6, 0.8, "a")]),
        ],
    )

    result = _运行_cli(capsys, "do_textgrid_inspect", _引号(src))

    assert result["success"] is True
    assert result["recommendations"]["group_tier"] == "组别"
    assert result["recommendations"]["item_tier"] == "词项"
    assert result["recommendations"]["core_tier"] == "核心"


def test_cli_textgrid_preview_supports_wordlist_guided_pairing(tmp_path, capsys):
    src = _写_textgrid(
        tmp_path / "预览.TextGrid",
        [
            ("words", [(0.0, 0.4, "卡"), (0.4, 1.0, "达")]),
            ("core", [(0.1, 0.3, "a"), (0.6, 0.8, "a")]),
        ],
    )
    wordlist = tmp_path / "字表.txt"
    wordlist.write_text("【阴平+去声】\n卡达\n", encoding="utf-8")

    result = _运行_cli(
        capsys,
        "do_textgrid_preview",
        f"{_引号(src)} item=words core=core pair=adjacent wordlist={_引号(wordlist)} limit=all",
    )

    assert result["success"] is True
    assert result["total_items"] == 1
    assert result["items"][0]["label"] == "卡达"
    assert result["items"][0]["group"] == "阴平+去声"
    assert result["items"][0]["match_note"] == "按辅助字表合并"


def test_cli_textgrid_convert_single_uses_override_and_writes_standard_tiers(tmp_path, capsys):
    src = _写_textgrid(
        tmp_path / "单文件.TextGrid",
        [
            ("words", [(0.0, 1.0, "卡")]),
            ("core", [(0.2, 0.7, "a")]),
        ],
    )
    out = tmp_path / "标准.TextGrid"

    result = _运行_cli(
        capsys,
        "do_textgrid_convert",
        f"{_引号(src)} out={_引号(out)} item=words core=core group=无 override=卡:实验组",
    )

    assert result["success"] is True
    assert result["converted_count"] == 1
    converted = textgrid.TextGrid.fromFile(str(out))
    assert [tier.name for tier in converted.tiers] == ["groups", "words", "chars"]
    assert _非空(_层(converted, "groups")) == [(0.2, 0.7, "实验组")]
    assert _非空(_层(converted, "words")) == [(0.2, 0.7, "卡")]
    assert _非空(_层(converted, "chars")) == [(0.2, 0.7, "卡")]


def test_cli_textgrid_convert_batch_directory_pairs_adjacent_items(tmp_path, capsys):
    src_dir = tmp_path / "来源"
    src_dir.mkdir()
    for name, first, second in [("001.TextGrid", "卡", "达"), ("002.TextGrid", "巴", "飞")]:
        _写_textgrid(
            src_dir / name,
            [
                ("groups", [(0.0, 1.0, "阴平+去声")]),
                ("words", [(0.0, 0.45, first), (0.45, 1.0, second)]),
                ("core", [(0.1, 0.3, "a"), (0.6, 0.8, "a")]),
            ],
        )
    out_dir = tmp_path / "输出"

    result = _运行_cli(
        capsys,
        "do_textgrid_convert",
        f"{_引号(src_dir)} out={_引号(out_dir)} item=words core=core group=groups pair=adjacent",
    )

    assert result["success"] is True
    assert result["converted_count"] == 2
    outputs = sorted(out_dir.glob("*.TextGrid"))
    assert [path.name for path in outputs] == ["001_converted.TextGrid", "002_converted.TextGrid"]

    first = textgrid.TextGrid.fromFile(str(outputs[0]))
    assert _非空(_层(first, "words")) == [(0.1, 0.8, "卡达")]
    assert _非空(_层(first, "chars")) == [(0.1, 0.3, "卡"), (0.6, 0.8, "达")]
