# -*- coding: utf-8 -*-
"""
PhonTracer / Tone Extractor 自定义脚本 AI 提示词生成器
"""

from collections import Counter


def _format_counter(counter, empty="无"):
    if not counter:
        return empty
    return ", ".join(f"{name} ({count}条)" for name, count in sorted(counter.items(), key=lambda x: str(x[0])))


def _build_project_summary(project_data):
    spk_count = 0
    item_count = 0
    excluded_count = 0
    groups = Counter()
    modes = Counter()
    speaker_names = []
    speaker_lines = []
    pitch_cache_count = 0
    formant_cache_count = 0

    if project_data and isinstance(project_data, dict):
        speakers = project_data.get("speakers", {})
        spk_count = len(speakers)
        for spk_id, spk in speakers.items():
            name = spk.get("name", "未命名")
            speaker_names.append(name)
            items = spk.get("items", {})
            spk_groups = Counter()
            spk_modes = Counter()
            spk_excluded = 0

            for item in items.values():
                item_count += 1
                if item.get("is_excluded", False):
                    excluded_count += 1
                    spk_excluded += 1

                group_name = item.get("group") or "未分组"
                groups[group_name] += 1
                spk_groups[group_name] += 1

                mode = item.get("analysis_mode", spk.get("last_params", {}).get("analysis_mode", "f0"))
                modes[mode] += 1
                spk_modes[mode] += 1

                if item.get("pitch_data_file") or item.get("pitch_data") is not None:
                    pitch_cache_count += 1
                if item.get("formant_data_file") or item.get("formant_data") is not None:
                    formant_cache_count += 1

            speaker_lines.append(
                f"  - {name}: 条目 {len(items)}，排除 {spk_excluded}，"
                f"分组 {{{_format_counter(spk_groups)}}}，模式 {{{_format_counter(spk_modes)}}}"
            )

    included_count = item_count - excluded_count
    speaker_detail = "\n".join(speaker_lines[:12]) if speaker_lines else "  - 无"
    if len(speaker_lines) > 12:
        speaker_detail += f"\n  - 其余 {len(speaker_lines) - 12} 位发音人已省略"

    return (
        f"- 发音人数: {spk_count} (列表: {', '.join(speaker_names) if speaker_names else '无'})\n"
        f"- 条目总数: {item_count}；纳入分析: {included_count}；已排除: {excluded_count}\n"
        f"- 分组及条目数: {_format_counter(groups)}\n"
        f"- 分析模式分布: {_format_counter(modes)}\n"
        f"- 已缓存基频条目估计: {pitch_cache_count}；已缓存共振峰条目估计: {formant_cache_count}\n"
        f"- 发音人明细:\n{speaker_detail}"
    )


def generate_ai_prompt(project_data, selections):
    """
    根据用户在弹窗中选择的各项配置，以及当前工程摘要，生成给 AI 的提示词。

    :param project_data: 当前工程的 JSON 数据字典（可为空）
    :param selections: 包含用户选择偏好的字典
    :return: 格式化后的完整提示词字符串
    """
    # 1. 提取工程摘要信息
    project_summary = _build_project_summary(project_data)

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

项目数据说明与数据来源：
数据来自只读快照列表 ctx.dataset.items。脚本无法访问工程文件路径，也不应该读取任何本地文件；所有可用数据都已经被 PhonTracer 整理成 item 字典。

每个条目（item）代表一个被切分/分析的字词条目，包含以下字段：
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
- syl_data: F0 的音节级 11 点采样结果，list，每个元素形如 (duration, f0_values)
  - duration: 当前音节有效基频段持续时间
  - f0_values: 当前音节内对齐后的 11 个 F0 Hz 采样点
- syl_t_values: F0 的音节级 11 点五度标度结果，list，每个元素为当前音节内对齐后的 11 个 T 值
- syl_formants: 共振峰的音节级采样结果，list，每个元素为 dict，常见键包括：
  - syllable_index: 音节序号
  - char: 当前音节/字
  - bounds: 当前音节边界 [start, end]
  - times: 当前音节采样时间点
  - f1: 当前音节 F1 采样值
  - f2: 当前音节 F2 采样值

字段语义和常见陷阱：
- pitch.xs / pitch.freqs / pitch.t_values 是原始基频轨迹点，时间轴不保证已经按音节对齐，也不保证不同条目之间长度相同。
- 严禁为了比较声调/F0走势，直接把整段 pitch.xs 归一化到 0-100 后对不同条目求均值或画 KDE。这样会把多音节、停顿、断续基频点压扁到同一横轴，极容易画出每组都一样或锯齿严重的伪结果。
- 如果目标是比较声调、连读、分组 F0 走势，优先使用 syl_t_values（五度标度）或 syl_data（Hz），因为它们已经按每个音节 11 点对齐。
- 如果目标是比较原始 F0 Hz，用 syl_data；如果目标是跨发音人比较声调走势，用 syl_t_values 更稳。
- 如果目标是画元音空间或共振峰图，优先使用 syl_formants 或 ctx.dataset.formant_points(item)，并过滤非有限值、F1<=0、F2<=0、F2<=F1 的点。
- item.group 是分组真相；不要根据 label 末尾数字自行推断声调，除非用户明确要求。
- item.is_excluded 为 True 的条目默认不参与分析，除非用户明确选择“使用全部条目”。

推荐分析范式：
1. F0/声调分组均值曲线：
   - items = ctx.dataset.included_items()
   - 过滤 analysis_mode == "f0"
   - 按 item["group"] 分组
   - 使用 item["syl_t_values"] 或 item["syl_data"]
   - 对每个条目，把每个音节的 11 点串接成一条曲线；不同音节数量不要混在同一个均值里，除非明确使用“时序展开”并在图上标出音节分隔线
   - 对每组计算 mean、std 或 95% CI，并标注样本数 n
   - 图形优先用均值曲线 + 阴影区间 + 少量浅色个体曲线，不要把热力图当作主要统计结论
2. F0 热力/密度感图：
   - 不要使用 scipy.stats.gaussian_kde
   - 可以用 ax.hist2d、ax.hexbin 或低分辨率二维直方图模拟密度
   - 如果比较多个分组，所有子图必须共享同一套颜色归一化范围；不要每个分组单独除以自己的最大值，否则颜色深浅不能横向比较
   - 热力背景只作为辅助，必须同时画均值曲线和样本数
3. 元音空间 / F1-F2 图：
   - 横轴通常用 F2，纵轴用 F1，传统元音空间图通常反转 x 轴和 y 轴
   - 只画有限且合理的点：np.isfinite(f1)、np.isfinite(f2)、f1>0、f2>0、f2>f1
   - 可以按 group 或 speaker 着色，推荐同时标出均值中心
4. 多发音人比较：
   - 如果跨男女声比较原始 Hz，必须谨慎；更推荐 T 值或分发音人子图
   - 如果使用 Hz，请至少按 speaker_name 分面或在图例中区分发音人
5. 输出格式：
   - 结构化选项里的文件名和格式优先级高于补充需求。
   - 如果输出文件名是 png 且用户只要求“一张清晰图表”，只返回一个 ctx.figure(...png)。
   - 只有当用户非常明确要求“同时导出 PNG 和 SVG”时，才返回同一 figure 的两个结果，并使用同一个基础文件名的 .png / .svg。
   - 不要无故返回多个重复图表。

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
- return [ctx.figure(fig, filename="xxx.png", title="..."), ctx.figure(fig, filename="xxx.svg", title="...")] 仅在用户明确要求双格式时使用

推荐代码骨架（请按目标改写，不要机械照抄无关部分）：

def run(ctx):
    np = ctx.np
    plt = ctx.plt
    items = ctx.dataset.included_items()
    # 如果用户明确要求全部条目，才改用 ctx.dataset.items

    groups = {{}}
    for item in items:
        if ctx.is_cancelled():
            ctx.log("用户取消，提前结束")
            break
        if item.get("analysis_mode") != "f0":
            continue
        group = item.get("group") or "未分组"

        # F0 声调比较优先使用 syl_t_values；如果要 Hz，则用 syl_data
        syl_t_values = item.get("syl_t_values") or []
        if not syl_t_values:
            continue

        curve = []
        for syl_vals in syl_t_values:
            arr = np.asarray(syl_vals, dtype=float)
            if arr.size != 11:
                continue
            curve.extend(arr.tolist())
        if len(curve) < 2 or np.all(np.isnan(curve)):
            continue

        groups.setdefault(group, []).append(curve)

    if not groups:
        fig, ax = plt.subplots(figsize=(6, 4))
        ax.text(0.5, 0.5, "没有可用数据", ha="center", va="center")
        ax.axis("off")
        return ctx.figure(fig, filename="{filename}", title="{title}")

    # 后续应按分组计算均值/标准差，绘制曲线，并标注每组样本数。
    # 不要直接对 raw pitch.xs 做整段归一化求均值。

当前工程摘要信息：
{project_summary}

用户自定义脚本需求配置：
{requirements_text}

额外设计准则：
- 如果没有数据或匹配不到任何条目，脚本不要崩溃报错。请生成一张包含“没有可用数据”说明文字的空 Matplotlib Figure 返回，或者调用 ctx.log 记录空说明。
- 图表的标题、坐标轴名称、图例文字必须使用中文。
- 图上必须标注关键统计口径，例如“使用 T 值/Hz”“每个音节 11 点对齐”“n=样本数”“阴影含义为标准差或置信区间”。
- 如果脚本存在较长循环，请在循环中定期调用 ctx.is_cancelled()，为 True 时记录日志并提前返回当前可用结果或空图。
- 避免高成本密度估计和大网格计算。不要使用 scipy.stats.gaussian_kde；如果需要密度感，请优先使用 ax.hexbin、ax.hist2d、低分辨率分箱均值、抽样散点或均值/置信区间曲线。
- 不要对每个分组构造 100x100 或更大的二维网格反复计算密度；每组绘图应尽量控制在几千个点以内，数据很多时先按时间或随机抽样。
- 不要生成“看起来每组一样”的图：如果每个分组曲线完全相同或高度相似，通常说明你把全体数据重复画进每个子图，或错误使用了未按 group 过滤的数据。
- 不要把每个子图的热力密度单独归一化后再用于横向比较；需要横向比较时必须共享颜色范围，或明确说明颜色只表示组内相对密度。
- 脚本应使用 ctx.log 输出数据诊断信息：纳入条目数、实际绘制分组、每组样本数、被跳过条目数、使用的数据字段。
- 代码应尽量简洁稳健，避免使用复杂的类结构，优先使用面向过程的清晰绘图逻辑。
"""
    return prompt.strip()
