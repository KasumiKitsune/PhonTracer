# PhonTracer 论文使用与写作指南

本文档旨在帮助研究者在学术论文中规范地表述 PhonTracer（当前版本 **v1.2.2**）的使用方法与参数设置，确保声学分析过程透明、可复现。

## 1. 核心声明：工具辅助，人工决策

在论文中使用 PhonTracer 时，最核心的写作原则是：**PhonTracer 仅为辅助工具，研究者需对最终数据和结论负责。**

* **推荐表述**：使用 PhonTracer 辅助提取声学参数（F0/F1/F2）、进行声谱图可视化复核、导出数据；其底层调用 Praat (Parselmouth) 进行声学测量；由研究者设定分析参数、进行人工复核与异常值剔除。
* **避免表述**：软件“自动分析/判断”了声调或音质，或软件直接得出了某种语言学结论。

## 2. 论文写作模板（推荐直接参考或修改）

以下提供了一段稳妥且完整的“研究方法”段落模板，涵盖了审稿人最关心的细节。请根据你的实际研究设计替换方括号内的内容：

> 本研究使用 PhonTracer (v1.2.2) 辅助完成录音材料的声学分析。该软件底层通过 Parselmouth 调用 Praat 功能提取 `[F0 / F1 / F2]`，并提供可视化界面供边界复核与异常检查。本文将 `[录音与字表 / TextGrid 标注]` 导入软件后，对目标条目的边界和声学轨迹进行了人工复核。
> 
> F0 分析参数设置为：pitch floor `[数值]` Hz、pitch ceiling `[数值]` Hz、voicing threshold `[数值]`；共振峰分析参数设置为：formant ceiling `[数值]` Hz、追踪数量 `[数值]`、window length `[数值]`。
> 
> 对于存在严重噪声、无稳定周期或声学追踪明显错误的条目，按预设标准予以排除，不纳入后续统计分析。原始 F0 Hz 值被进一步换算为 `[T 值等归一化指标]`。最终复核后的声学数据由 PhonTracer 导出，并在 `[统计软件名称，如 R / SPSS]` 中完成后续统计检验与模型拟合。

**针对特定场景的补充说明：**

* **外部标注导入**：目标音段边界初始由 `[Praat / MFA]` 生成的 TextGrid 提供，导入 PhonTracer 后由研究者复核调整。
* **图表生成说明**：本文部分声学图表由 PhonTracer 生成，图中的曲线表示 `[Hz / T 值]` 尺度下的 `[均值 / 样本轨迹]`，阴影表示 `[标准差 / 95% 置信区间]`。

## 3. 引用与参考文献格式

如需在脚注或参考文献中列出软件，可参考以下格式（正式投稿请根据目标期刊要求微调）：

**脚注简写：**

> 本文使用 PhonTracer (v1.2.2) 辅助完成声学参数提取与数据导出。项目地址：https://github.com/KasumiKitsune/PhonTracer

**参考文献（APA / 国标 / BibTeX）：**

> KasumiKitsune. (2026). *PhonTracer* (Version 1.2.2) [Computer software]. GitHub. https://github.com/KasumiKitsune/PhonTracer

```bibtex
@software{phontracer_2026,
  author = {KasumiKitsune},
  title = {PhonTracer},
  version = {1.2.2},
  year = {2026},
  url = {https://github.com/KasumiKitsune/PhonTracer},
  note = {Computer software}
}
```

*(注：如果项目后续发布了 Zenodo DOI，请优先引用包含 DOI 的版本信息。)*

## 4. 投稿前备忘录

为了保证研究的严谨性与可复现性，在提交论文前请务必核对以下清单：

### ✅ 必须在正文中明确交代的内容

- [ ] **软件版本**（如 v1.2.2）以及说明其依赖了 Praat/Parselmouth 算法。
- [ ] **核心参数记录**：包括基频的上下限（pitch floor/ceiling）、共振峰的搜索上限（formant ceiling）和采样点选取方式。不同性别或组别如果参数不同，需分别说明。
- [ ] **人工复核标准**：明确边界是否经过人工调整，以及剔除异常点/异常条目的原则是什么。
- [ ] **数据处理边界**：明确区分哪些是 PhonTracer 导出的图表与数据，哪些是外部软件完成的统计学推断。

### 📂 建议随附的开放材料 (Data Availability)

如果期刊要求开放数据，建议在附录或公开数据仓库（如 OSF, GitHub）中保留：

- `.teproj` 工程文件、TextGrid 标注文件以及导出的数据表格（XLSX / CSV）。
- 不涉及伦理或隐私争议的原始音频。
- 统计分析脚本（如 R 脚本、Python 代码）。

---

> **💡 总结**
> 论文中不要只笼统地写“使用 PhonTracer 分析”，而要写清楚“提取了什么参数、设置了什么阈值、如何进行人工复核、排除了哪些数据、最后在哪里做统计”。
