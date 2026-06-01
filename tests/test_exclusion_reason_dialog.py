from modules.project_tree import (
    EXCLUSION_REASON_CATEGORIES,
    EXCLUSION_REASON_CUSTOM_CATEGORY,
    EXCLUSION_REASON_DETAIL_PLACEHOLDER,
    format_exclusion_reason,
    parse_exclusion_reason,
)


def test_忽略原因包含五类预设和其他原因():
    assert list(EXCLUSION_REASON_CATEGORIES) == [
        "录音质量问题",
        "发音内容问题",
        "语音现象干扰",
        "声学分析困难",
        "实验与数据管理",
    ]
    assert EXCLUSION_REASON_CUSTOM_CATEGORY == "其他原因"


def test_未选择二级原因时保存大类名称():
    assert format_exclusion_reason(
        "录音质量问题",
        EXCLUSION_REASON_DETAIL_PLACEHOLDER,
    ) == "录音质量问题"


def test_选择二级原因时保存大类和具体原因():
    assert format_exclusion_reason(
        "声学分析困难",
        "F0 无法可靠提取",
    ) == "声学分析困难：F0 无法可靠提取"


def test_其他原因保留用户输入():
    assert format_exclusion_reason(
        EXCLUSION_REASON_CUSTOM_CATEGORY,
        custom_reason="  特殊实验记录  ",
    ) == "特殊实验记录"


def test_旧版原因可以恢复到对应大类():
    assert parse_exclusion_reason("录音中断") == (
        "录音质量问题",
        "录音中断或不完整",
        "",
    )
    assert parse_exclusion_reason("发音错误") == (
        "发音内容问题",
        "发音错误",
        "",
    )


def test_新版原因可以恢复到对应大类和二级原因():
    assert parse_exclusion_reason("实验与数据管理：文件损坏或音频缺失") == (
        "实验与数据管理",
        "文件损坏或音频缺失",
        "",
    )


def test_未知旧原因按其他原因恢复():
    assert parse_exclusion_reason("研究者手动剔除") == (
        EXCLUSION_REASON_CUSTOM_CATEGORY,
        EXCLUSION_REASON_DETAIL_PLACEHOLDER,
        "研究者手动剔除",
    )
