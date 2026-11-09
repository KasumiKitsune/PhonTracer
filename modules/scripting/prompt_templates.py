# -*- coding: utf-8 -*-
"""
PhonTracer 自定义脚本 AI 提示词静态契约、参数键名白名单、进阶指南附录与代码骨架模板。
"""

VALID_SCRIPT_PROMPT_KEYS = (
    "template",
    "output_type",
    "scope",
    "analysis_mode",
    "user_request",
    "out",
    "format",
    "purpose",
    "goal",
    "script_type",
    "prompt_mode",
    "data_range",
    "group_by",
    "chart_style",
    "x_axis",
    "y_axis",
    "stats",
    "title",
    "filename",
    "img_format",
    "output_table",
    "show_legend",
    "use_chinese",
    "custom_desc",
    "agent_detail_level",
    "agent_chart_count",
    "agent_plan_count",
    "agent_project_summary_mode",
    "agent_detailed_project_summary",
    "agent_include_project_summary",
)

SCRIPT_PROMPT_KEY_ALIASES = {
    "template": "template",
    "tpl": "template",
    "output_type": "output_type",
    "out_type": "output_type",
    "scope": "scope",
    "target": "scope",
    "analysis_mode": "analysis_mode",
    "mode": "analysis_mode",
    "user_request": "user_request",
    "request": "user_request",
    "prompt": "user_request",
    "out": "out",
    "output": "out",
    "out_file": "out",
    "output_file": "out",
    "format": "format",
    "purpose": "purpose",
    "goal": "goal",
    "script_type": "script_type",
    "type": "script_type",
    "prompt_mode": "prompt_mode",
    "data_range": "data_range",
    "group_by": "group_by",
    "groupby": "group_by",
    "chart_style": "chart_style",
    "style": "chart_style",
    "x_axis": "x_axis",
    "y_axis": "y_axis",
    "stats": "stats",
    "title": "title",
    "filename": "filename",
    "img_format": "img_format",
    "output_table": "output_table",
    "show_legend": "show_legend",
    "use_chinese": "use_chinese",
    "lang": "use_chinese",
    "custom_desc": "custom_desc",
    "desc": "custom_desc",
    "description": "custom_desc",
    "agent_detail_level": "agent_detail_level",
    "detail_level": "agent_detail_level",
    "agent_chart_count": "agent_chart_count",
    "chart_count": "agent_chart_count",
    "agent_plan_count": "agent_plan_count",
    "plan_count": "agent_plan_count",
    "agent_project_summary_mode": "agent_project_summary_mode",
    "summary_mode": "agent_project_summary_mode",
    "agent_detailed_project_summary": "agent_detailed_project_summary",
    "agent_include_project_summary": "agent_include_project_summary",
}

REFERENCE_CHART_RULE = (
    "如果用户上传、粘贴或提到参考图表，理解为用户想使用当前 PhonTracer 工程数据，"
    "复刻参考图的图表类型、变量关系、统计口径、布局和视觉表达方式；"
    "如果参考图是语谱图，可复刻灰度语谱图、多宫格、标签和箭头标注风格；"
    "严禁照抄参考图里的数值、样本标签、数据点或结论，除非用户明确说明这些内容就是待分析数据。"
)

SPECTROGRAM_SCRIPT_GUIDE = """语谱图与 Parselmouth 受控接口：
- 可用 `ctx.parselmouth`（或 `import parselmouth`），但**严禁导入或调用 `parselmouth.praat`**（AST 安全检查会拦截）。
- 脚本不能自行读取本地音频路径；必须调用 `sound = ctx.load_item_sound(item, padding=0.0)` 读取当前 `.teproj` 内受控音频（返回 `parselmouth.Sound`）。
- 调用 `spec = ctx.spectrogram_data(sound, max_frequency=5000.0, window_length=0.005, dynamic_range_db=70.0)` 返回字典包含 **8 个精确键**：
  `x` (1D时间轴秒 `np.ndarray`)、`y` (1D频率轴Hz `np.ndarray`)、`db` (2D能量矩阵，形状 `(len(y), len(x))`)、`vmin`、`vmax`、`max_frequency`、`window_length`、`dynamic_range_db`。
- 调用 `ctx.plot_spectrogram_grid(items, columns=4, max_items=8, show_formant_arrows=True, label_field="auto", max_frequency=4000.0)` 直接返回多宫格灰度语谱图 `Figure`；其中 `label_field` 支持 `"auto"`、`"label"`、`"item_id"` 以及 `"item_meta.<字段名>"`（如 `"item_meta.IPA"` 或 `"item_meta.国际音标"`）。"""

CORE_CHART_API_CONTRACT = f"""### 运行沙箱与硬性 API 契约（违反任一条将直接报错）
1. **入口、声明式交互参数 (`PARAMS`) 与多产物联合返回**：
   - 脚本开头前两行必须固定为：`# 脚本名称：<简短中文名>` 与 `# 功能说明：<一句话说明>`。
   - **可选声明式交互参数 (`PARAMS`)**：推荐在脚本顶部定义 `PARAMS = [...]` 列表（每项为字面量字典，支持 `type` 为 `"choice"`, `"int"`, `"float"`, `"bool"`, `"str"`，包含 `key`, `label`, `default` 以及可选 `choices`, `min`, `max`）；工具箱会自动在运行栏渲染下拉框/复选框/输入框，脚本内通过 `ctx.params.get("key", default)` 读取当前参数值。
   - 必须定义 `def run(ctx):` 作为唯一入口。
   - **多格式同步导出与多产物联合返回**：
     - `ctx.figure(fig, filename="xxx.png", title="图表标题", formats=("png", "pdf", "svg"), dpi=300)` 支持通过 `formats` 单次运行同步导出多种位图/矢量图格式，并在画布内自动生成 200 DPI 高清预览。
     - `ctx.table(df_or_rows, columns=None, title="表格标题")` 支持直接传入 `pandas.DataFrame` 或 `(rows, columns)`，在工作台内置表格预览器中直接展示并支持排序与复制 TSV。
     - 支持返回单个结果或任意组合列表 `[ctx.figure(...), ctx.table(...), ctx.project_patch(...)]`（允许同时返回诊断图表、统计表格与工程修改补丁 `ProjectPatchResult`）。
2. **预置库与字体环境**：
   - 已注入 `ctx.np` (`numpy`)、`ctx.pd` (`pandas`)、`ctx.plt` (`matplotlib.pyplot`)、`ctx.scipy` (`scipy`)、`ctx.parselmouth` (`parselmouth`)；也可直接 `import pandas as pd`、`from scipy.stats import gaussian_kde` 或导入标准库 `math, statistics, collections, itertools, re, time, warnings`。
   - Matplotlib 中文字体、国际音标 (IPA) 回退链与 PDF Type 42 矢量字体已由系统自动预配置，**严禁脚本自行修改 `plt.rcParams['font.sans-serif']`**（以免覆盖为不存在的字体导致中文乱码）。
   - 沙箱中 `print(...)` 已自动重定向至 `ctx.log(...)`；长循环中请定期检查 `if ctx.is_cancelled(): break`。
3. **沙箱安全禁令（AST 静态拦截）**：
   - **禁止危险外部与系统库**：严禁导入 `seaborn, plotly, requests, os, sys, subprocess`（仅内置只读受控 `os.path`；内存级 `pandas` 与 `scipy.stats.gaussian_kde` 已放行）。
   - **禁止磁盘读写调用**：严禁调用 `open(), eval(), exec(), .savefig(), .to_csv(), .to_excel(), .to_parquet(), .to_file(), pd.read_csv(), pd.read_excel()` 等磁盘读写方法（请始终通过 `ctx.figure(fig, ...)` 或 `ctx.table(...)` 返回结果）。
   - **禁止反射与双下划线属性**：严禁使用 `getattr, hasattr, setattr, delattr, globals, locals, vars` 及访问 `__xxx__` 双下划线属性或 `ctx._*` 内部私有属性。
   - **禁止 `parselmouth.praat`**：请使用 `ctx.extract_voice_quality`、`ctx.extract_spectral_moments`、`ctx.load_item_sound` 和 `Sound.to_spectrogram` 等受控高阶接口。
4. **核心上下文辅助方法（优先推荐使用预展开长宽表 API，3行代码即可完成分组统计）**：
   - **【首选】`df_pts = ctx.dataset.to_point_table(included_only=True, as_df=True)`**：获取预展开的 **采样点级长表 DataFrame**（每行=某词条某音节的第 `point_idx` (1..11) 个采样点），已自动将 `<=0` Hz 清洗为 `np.nan`，预置列：`speaker_id, speaker_name, item_id, word, label, char, syl_idx, syl_count, group, point_idx, norm_time, phys_time_ms, f0_hz, t_value, semitone, f1_hz, f2_hz, f3_hz, bark_f1, bark_f2, lobanov_f1, lobanov_f2, tags` 及所有平铺自定义字段 `field_<字段名>`。
   - **【首选】`df_syl = ctx.dataset.to_syllable_table(included_only=True, as_df=True)`**：获取预展开的 **音节级汇总宽表 DataFrame**（每行=1个音节），预置列：`speaker_id, speaker_name, item_id, word, label, char, syl_idx, syl_count, group, dur_ms, f0_mean, f0_min, f0_max, f0_range, f0_slope, t_onset, t_mid, t_offset, t_range, t_code, f1_mid, f2_mid, f3_mid, f1_20, f2_20, f1_80, f2_80, tl_hz, tags` 及 `field_<字段名>`。
   - **`df_frm = ctx.dataset.to_frame_table(item_or_items=None, included_only=True, as_df=True)`**：获取原始物理时间帧表（`speaker_name, item_id, word, syl_idx, time_s, rel_time, f0_hz, f1_hz, f2_hz, f3_hz`）。
   - **高阶声学特征算子**：
     - `vq = ctx.extract_voice_quality(item, syl_idx=None)`：提取嗓音发声态指标 `{"jitter_local", "shimmer_local", "hnr_db", "cpp_db", "h1_h2_db", "h1_a1_db", "h1_a3_db"}`。
     - `sm = ctx.extract_spectral_moments(item, time_window=None, pre_emphasis=80.0)`：提取擦音/辅音频谱四阶矩 `{"cog_hz", "sd_hz", "skewness", "kurtosis"}`。
     - `df_norm = ctx.normalize_formants(df_or_items, method="lobanov" | "nearey1" | "bark" | "erb" | "watt_fabricius")`：跨发音人元音归一化。
   - `ctx.dataset.included_items()` / `ctx.dataset.groups()` / `ctx.dataset.speakers()` / `ctx.get_f0_bounds(f0_values)`。
   - {SPECTROGRAM_SCRIPT_GUIDE}

### 条目数据结构与关键防坑规则
每个 `item` 字典包含：
- **基础与边界信息**：`speaker_id`, `speaker_name`, `item_id`, `label`, `group`（分组真相，勿从 `label` 末尾数字猜声调）, `is_excluded`, `analysis_mode` (`"f0"` 或 `"formant"`), `start`, `end`, `duration`。
- **高级字表 v2 元数据与 `wordlist_doc` 扩展属性**：
  - 词条级字段：`tags` / `item_tags` (`list[str]`，词项标签，如 `["目标词", "双字组"]`)、`note` / `item_note` (`str`，词项备注)、`fields` / `custom_fields` / `item_meta` (`dict`，自定义扩展字段字典，可通过 `(item.get("fields") or item.get("custom_fields") or item.get("item_meta") or {{}}).get("结构")` 读取；常见示例字段：`拼音`, `声调`, `韵母`, `结构`, `实验条件`, `词频等级`)、`aliases` / `item_aliases` (`list[str]`)、`group_note` (`str`)、`group_tags` (`list[str]`)、`wordlist_version` (`"v2"`)、`wordlist_title`、`metadata_source`（如 `"人工填写"`、`"AI推断，需人工复核"`）。
  - 工程级 `wordlist_doc` 结构：`project_data.get("wordlist_doc")` 保存完整 v2 高级字表文档（含 `schema: "phontracer.wordlist.v2"`, `title`, `note`, 以及 `groups: [{{id, name, note, tags, meta, items: [{{label, note, tags, aliases, meta/fields/custom_fields, metadata_source}}]}}]`）。
- **声调/基频字段（必读区别）**：
  - `syl_t_values`：`list[list[float]]`，每个音节 11 个对齐后的五度值（`0~5`），**无效点已是 `np.nan`**（跨发音人声调比较首选）。
  - `syl_data`：`list[tuple[float, list[float]]]`，每个元素为 `(有声段时长秒, 11点Hz列表)`。**关键防坑：其中 `>25ms` 静音间隙被赋值为 `0.0`（而非 `np.nan`），对 `syl_data` 求均值/标准差前必须先将 `<= 0` 替换为 `np.nan`（如 `arr = np.where(arr > 0, arr, np.nan)`）！**
  - `pitch`：`{{"xs": [...], "freqs": [...], "t_values": [...]}}` 原始帧序列，不同条目长度不同且未按音节对齐，严禁直接整段线性插值求均值。
- **共振峰字段（必读区别）**：
  - `formant`（及 `ctx.dataset.formant_points(item)`）：**始终包含 4 个键** `{{"xs": [...], "f1": [...], "f2": [...], "f3": [...]}}`。
  - `syl_formants`：`list[dict]`，每个音节字典含 `syllable_index, char, bounds, times` 及 11 点 `f1, f2`（**仅当工程开启 `show_f3=True` 时包含 `"f3"`**）。绘图前需过滤 `np.isfinite(f1) & np.isfinite(f2) & (f1 > 0) & (f2 > f1)`；传统元音空间图横轴为 `F2`、纵轴为 `F1` 且双轴反转（`ax.invert_xaxis()`, `ax.invert_yaxis()`）。"""


def _build_agent_detail_appendix():
    """
    详细模式的增量附录：绝不重复正文中已有的 API 契约与禁令，仅补充进阶分析范式与诊断建议。
    """
    return """### 进阶声学分析与绘图设计指南（详细模式增量参考）
1. **多音节连读与时序拼接范式**：
   - 单音节与多音节条目不宜混入同一条 `0~1` 归一化均值曲线；应按音节数（`len(item.get("syl_t_values") or [])`）分面或筛选。
   - 多音节连读声调图可将各音节的 11 点串接为 `11 * N` 点序列，并在音节交界处（`x = 10.5, 21.5...`）绘制虚线分隔线，同时利用 `syl_data` 中的有声段时长标注平均音节时长比例。
2. **二维密度背景与多子图色阶统一**：
   - 使用 `np.histogram2d` + `scipy.ndimage.gaussian_filter(H, sigma=1.0)` 生成平滑声调轨迹热力背景时，若存在多个分组子图，必须先汇总计算全局统一的 `vmax`，禁止每个子图各自除以自身最大值，确保颜色深浅具备跨组可比性。
3. **跨发音人元音空间归一化参考**：
   - 当跨男女声比较元音空间时，除原始 Hz 外，可在脚本中使用 Lobanov (发音人内 Z-score) 归一化或 Bark 标度转换（`bark = 26.81 * f / (1960.0 + f) - 0.53`），并在均值中心外绘制 1 倍标准差协方差椭圆。
4. **工程质量与元数据复核诊断**：
   - 若工程含 `metadata_source == "AI推断，需人工复核"`，建议在 `ctx.log(...)` 或统计表中单独汇报已复核与待复核样本占比。
   - 空数据兜底：若过滤后无有效样本，请勿抛出异常，应返回一张带居中提示文字 `"没有匹配当前筛选条件的可用数据"` 的空 `Figure` 并通过 `ctx.log` 说明原因。"""


def _build_data_process_detail_appendix():
    """
    数据处理 Agent 详细模式的增量附录：补充边界修改后的声学重算流水线与多算子组合示例。
    """
    return """### 进阶算子组合与声学重算流程指南（详细模式增量参考）
1. **边界微调/裁剪后的声学缓存重算闭环（极其重要）**：
   - 底层写回器规定：只要对某条目执行了修改 `start, end, inner_splits, chars_bounds` 的 `ctx.set_item_fields`，或执行了 `ctx.trim_item_audio`，系统会**自动清空该条目的基频与共振峰缓存**。
   - 因此，任何调整边界或裁剪音频的脚本，必须在同一条目的后续操作中紧接着追加 `ctx.recompute_pitch(item, params=..., reason="边界更新后同步重算F0")` 和/或 `ctx.recompute_formant(item, params=..., reason="边界更新后同步重算共振峰")`，确保生成的新 `.teproj` 打开即有完整声学曲线。
2. **发音人自适应重算参数范式**：
   - 可先遍历每位发音人的有效基频池，调用 `s_min, s_max = ctx.get_f0_bounds(f0_pool)` 评估其稳健调域；对高频女声/童声或低频男声自动构造定制化 `params={"pitch_floor": ..., "pitch_ceiling": ...}` 并调用 `ctx.recompute_pitch`。
3. **单次批量拆分多个独立 `.teproj` 子工程示例**：
   - 当用户需要按发音人、声调组或实验条件拆分工程时，只需在同一个 `operations` 列表中放入多个 `ctx.split_project`：
     ```python
     for spk_id in sorted_speaker_ids:
         operations.append(ctx.split_project(
             name=f"speaker_{spk_id}",
             speaker_ids=[spk_id],
             reason=f"拆出发音人 {spk_id} 独立子工程"
         ))
     ```
     Toolkit 会在保存目录一次性导出全部独立子工程文件。"""


def _build_code_skeleton(title, user_goal, filename, is_formant=False):
    if is_formant:
        return f"""# 脚本名称：{title}
# 功能说明：{user_goal}
def run(ctx):
    np = ctx.np
    plt = ctx.plt
    items = ctx.dataset.included_items()

    groups = {{}}
    for item in items:
        if ctx.is_cancelled():
            ctx.log("用户取消，提前结束")
            break
        if item.get("analysis_mode") != "formant":
            continue
        group = item.get("group") or "未分组"
        syl_formants = item.get("syl_formants") or []
        for syl in syl_formants:
            f1 = np.asarray(syl.get("f1", []), dtype=float)
            f2 = np.asarray(syl.get("f2", []), dtype=float)
            if f1.size == 11 and f2.size == 11:
                # 取元音中段 (索引 4:7) 稳健均值
                m_f1 = float(np.nanmean(f1[4:7]))
                m_f2 = float(np.nanmean(f2[4:7]))
                if np.isfinite(m_f1) and np.isfinite(m_f2) and m_f1 > 0 and m_f2 > m_f1:
                    groups.setdefault(group, []).append((m_f2, m_f1))

    fig, ax = plt.subplots(figsize=(6.5, 5.5))
    if not groups:
        ax.text(0.5, 0.5, "没有可用共振峰数据", ha="center", va="center")
        ax.axis("off")
        return ctx.figure(fig, filename="{filename}", title="{title}")

    # 后续按分组绘制 F2-F1 散点/均值椭圆，并反转坐标轴：
    # ax.invert_xaxis()
    # ax.invert_yaxis()
    return ctx.figure(fig, filename="{filename}", title="{title}")"""

    return f"""# 脚本名称：{title}
# 功能说明：{user_goal}
def run(ctx):
    np = ctx.np
    plt = ctx.plt
    items = ctx.dataset.included_items()

    groups = {{}}
    for item in items:
        if ctx.is_cancelled():
            ctx.log("用户取消，提前结束")
            break
        if item.get("analysis_mode") != "f0":
            continue
        group = item.get("group") or "未分组"

        # 跨发音人声调比较优先用 syl_t_values（无效点为 np.nan）；
        # 若用 syl_data (Hz)，需先将 <= 0 的静音点替换为 np.nan
        syl_t_values = item.get("syl_t_values") or []
        if not syl_t_values:
            continue

        curve = []
        for syl_vals in syl_t_values:
            arr = np.asarray(syl_vals, dtype=float)
            if arr.size == 11:
                curve.extend(arr.tolist())
        if len(curve) < 2 or np.all(np.isnan(curve)):
            continue
        groups.setdefault(group, []).append(curve)

    fig, ax = plt.subplots(figsize=(7, 4.5))
    if not groups:
        ax.text(0.5, 0.5, "没有可用基频数据", ha="center", va="center")
        ax.axis("off")
        return ctx.figure(fig, filename="{filename}", title="{title}")

    # 后续按分组计算均值与标准差/置信区间并绘图，标注样本量 n
    return ctx.figure(fig, filename="{filename}", title="{title}")"""
