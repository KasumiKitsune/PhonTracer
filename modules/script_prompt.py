# -*- coding: utf-8 -*-
"""
PhonTracer / Tone Extractor 自定义脚本 AI 提示词生成器
"""

def generate_ai_prompt(project_data, selections):
    """
    根据用户在弹窗中选择的各项配置，以及当前工程摘要，生成给 AI 的提示词。

    :param project_data: 当前工程的 JSON 数据字典（可为空）
    :param selections: 包含用户选择偏好的字典
    :return: 格式化后的完整提示词字符串
    """
    # 1. 提取工程摘要信息
    spk_count = 0
    item_count = 0
    groups = set()
    modes = {}
    speaker_names = []

    if project_data and isinstance(project_data, dict):
        speakers = project_data.get("speakers", {})
        spk_count = len(speakers)
        for spk_id, spk in speakers.items():
            name = spk.get("name", "未命名")
            speaker_names.append(name)
            items = spk.get("items", {})
            item_count += len(items)
            for item_id, item in items.items():
                g = item.get("group")
                if g:
                    groups.add(g)
                m = item.get("analysis_mode", spk.get("last_params", {}).get("analysis_mode", "f0"))
                modes[m] = modes.get(m, 0) + 1

    groups_list = sorted(list(groups))
    modes_desc = ", ".join(f"{m} ({count}条)" for m, count in modes.items())

    project_summary = (
        f"- 发音人数: {spk_count} (列表: {', '.join(speaker_names) if speaker_names else '无'})\n"
        f"- 条目总数: {item_count}\n"
        f"- 分组列表: {', '.join(groups_list) if groups_list else '无'}\n"
        f"- 分析模式分布: {modes_desc if modes_desc else '无'}"
    )

    # 2. 整合用户偏好
    prompt_mode = selections.get("prompt_mode", "参数选项")
    user_goal = selections.get("goal", "自定义图表")
    data_range = selections.get("data_range", "只使用纳入分析的条目")
    group_by = selections.get("group_by", "按声调/分组")
    chart_style = selections.get("chart_style", "折线图")
    x_axis = selections.get("x_axis", "归一化时间 0-1")
    y_axis = selections.get("y_axis", "F0 Hz")

    stats_list = selections.get("stats", [])
    stats_desc = "、".join(stats_list) if stats_list else "无"

    title = selections.get("title", "自定义图表")
    filename = selections.get("filename", "custom_chart.png")
    img_format = selections.get("img_format", "png")
    output_table = "是" if selections.get("output_table", False) else "否"
    show_legend = "是" if selections.get("show_legend", True) else "否"
    use_chinese = "是" if selections.get("use_chinese", True) else "否"
    custom_desc = selections.get("custom_desc", "").strip()

    # 3. 组装自然语言需求描述
    requirements = [
        f"0. 生成模式: {prompt_mode}",
        f"1. 脚本用途: {user_goal}",
        f"2. 数据范围: {data_range}",
        f"3. 分组方式: {group_by}",
        f"4. 图表形式: {chart_style}",
        f"5. 横轴 (X 轴): {x_axis}",
        f"6. 纵轴 (Y 轴): {y_axis}",
        f"7. 统计处理: {stats_desc}",
        f"8. 输出要求:",
        f"   - 图表标题: {title}",
        f"   - 输出文件名: {filename} (格式: {img_format})",
        f"   - 是否同时返回数据表: {output_table}",
        f"   - 是否显示图例: {show_legend}",
        f"   - 是否使用中文标签: {use_chinese}"
    ]

    if custom_desc:
        requirements.append(f"9. 补充具体需求:\n{custom_desc}")

    requirements_text = "\n".join(requirements)

    # 4. 生成完整提示词
    prompt = f"""你正在为 PhonTracer / Tone Extractor 编写一个自定义图表脚本。

请只输出可以直接运行的 Python 代码，不要输出任何解释性的文字或 Markdown 标记（不要包裹在 ```python 中）。

脚本必须定义 run(ctx) 函数作为统一入口：

def run(ctx):
    # 你的代码实现
    ...
    return ctx.figure(fig, filename="{filename}", title="{title}")

可用库：
- ctx.np 是 numpy 库
- ctx.plt 是 matplotlib.pyplot
- ctx.scipy 是 scipy 库
- 可导入并使用 numpy、matplotlib、scipy
- 可导入并使用标准库 math、statistics、collections、itertools、re、time

禁止使用：
- pandas
- seaborn
- plotly
- requests
- os
- sys
- subprocess
- scipy.stats.gaussian_kde（第一版运行器会拦截；它在点数较多时容易长时间占用后台线程）
- 任何本机绝对路径
- 任何文件读写操作，除非通过 ctx.figure 或 ctx.table 返回结果

数据来源：
数据来自只读快照列表 ctx.dataset.items，其中每个条目（item）为一个字典，包含以下字段：
- speaker_id: 发音人 ID (str)
- speaker_name: 发音人姓名 (str)
- item_id: 条目唯一 ID (str)
- label: 音节/字词标签 (str，例如 'ma1')
- group: 所在声调组/分类 (str，例如 '阴平')
- is_excluded: 是否已被标记排除 (bool)
- analysis_mode: 分析模式 ('f0' 或 'formant')
- start: 采样起点时间 (float，单位: 秒)
- end: 采样终点时间 (float，单位: 秒)
- duration: 条目时长 (float，单位: 秒)
- pitch: 基频数据字典，包含：
  - xs: 采样时间点序列 (list of float)
  - freqs: 基频 F0 频率序列 (list of float)
  - t_values: 五度标度归一化 T 值序列 (list of float)
- formant: 共振峰数据字典，包含：
  - xs: 采样时间点序列 (list of float)
  - f1: F1 频率序列 (list of float)
  - f2: F2 频率序列 (list of float)

ctx 提供的辅助方法：
- ctx.dataset.items: 获取所有条目快照
- ctx.dataset.groups(): 获取当前所有不重复的分组列表
- ctx.dataset.speakers(): 获取当前所有发音人列表
- ctx.dataset.included_items(): 仅获取未排除的分析条目
- ctx.dataset.pitch_points(item): 获取指定条目的基频点数据
- ctx.dataset.formant_points(item): 获取指定条目的共振峰点数据
- ctx.log("日志内容"): 记录一条日志，方便在结果界面中查看
- ctx.is_cancelled(): 长循环中可定期检查，返回 True 时请尽快结束脚本
- ctx.figure(fig, filename="xxx.png", title="图表标题"): 包装并返回 Matplotlib figure 对象
- ctx.table(rows, columns, title="表格标题"): 包装并返回数据表格（rows 为二维列表，columns 为表头列表）

合法返回值：
- return ctx.figure(...)
- return ctx.table(...)
- return [ctx.figure(...), ctx.table(...)]

当前工程摘要信息：
{project_summary}

用户自定义脚本需求配置：
{requirements_text}

额外设计准则：
- 如果没有数据或匹配不到任何条目，脚本不要崩溃报错。请生成一张包含“没有可用数据”说明文字的空 Matplotlib Figure 返回，或者调用 ctx.log 记录空说明。
- 图表的标题、坐标轴名称、图例文字必须使用中文。
- 如果脚本存在较长循环，请在循环中定期调用 ctx.is_cancelled()，为 True 时记录日志并提前返回当前可用结果或空图。
- 避免高成本密度估计和大网格计算。不要使用 scipy.stats.gaussian_kde；如果需要密度感，请优先使用 ax.hexbin、ax.hist2d、低分辨率分箱均值、抽样散点或均值/置信区间曲线。
- 不要对每个分组构造 100x100 或更大的二维网格反复计算密度；每组绘图应尽量控制在几千个点以内，数据很多时先按时间或随机抽样。
- 代码应尽量简洁稳健，避免使用复杂的类结构，优先使用面向过程的清晰绘图逻辑。
"""
    return prompt.strip()
