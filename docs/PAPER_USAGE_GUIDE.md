# PhonTracer 论文使用与写作指南

本文档旨在帮助研究者在学术论文中规范地表述 PhonTracer（当前版本 **v1.0.0**）的使用方法与参数设置，确保声学分析过程透明、可复现。

## 1. 论文方法学报告规范：客观说明软件版本、底层算法与参数配置

在论文方法学章节中，应客观阐明所用声学分析工具的算法实现、参数设置与质量控制流程：

* **推荐表述**：使用 PhonTracer 进行声学参数（基频 F0、共振峰 F1/F2 及可选 F3）提取、声谱图音段边界核对与轨迹检验；明确说明底层调用 Praat (Parselmouth) 算法；详细记录基频上下限、共振峰搜索上限、归一化方法等关键参数及人工复核剔除标准。
* **避免表述**：语焉不详地仅写“使用软件进行了分析”，或将声学参数提取过程表述为无需参数配置的完全黑盒输出。
* **免手抄核对建议**：可在 `Toolkit` 的「工程预览」页签（或使用 `PhonTracerCLI` 的 `export report` / `export_bundle` 命令）直接导出当前 `.teproj` 工程的**《研究方法报告》**，其中已自动汇总每位发音人的实际分析参数、有效样本数、人工边界/轨迹修订比例、组间统计摘要及工程文件 SHA-256 指纹，可直接对照填入正文或作为投稿补充材料。

## 2. 论文写作模板（推荐直接参考或修改）

以下提供了一段稳妥且完整的“研究方法”段落模板，涵盖了审稿人最关心的细节。请根据你的实际研究设计替换方括号内的内容：

> 本研究使用语音声学分析软件 PhonTracer (v1.0.0) 进行声学参数提取与标注复核。软件基于 Parselmouth (Praat) 提取 `[基频 F0 / 第一、第二共振峰 F1、F2]`，并在声谱图界面中完成音段边界核对与声学轨迹检验。本文将 `[录音与字表 / TextGrid 标注]` 导入软件后，对目标条目的边界和声学轨迹进行了人工复核。
>
> F0 分析参数设置为：pitch floor `[数值]` Hz、pitch ceiling `[数值]` Hz、voicing threshold `[数值]`，按韵母承载段等距采样 `[11]` 点；共振峰分析参数设置为：formant ceiling `[男 5000 / 女 5500]` Hz、共振峰追踪数量 `[5]`、window length `[0.025]` s、pre-emphasis `[50]` Hz，采用 `[整段 11 点轨迹 / 中段稳态均值]` 采样策略。
>
> 对于存在严重环境噪声、无稳定声带周期（如强嘎裂音或清化段）或声学追踪明显失真的条目，按预设标准剔除异常散点或排除整条样本，不纳入后续统计分析。原始 F0 Hz 值进一步按对数五度制公式换算为 `[五度 T 值 / 半音值]`；元音共振峰采用 `[Lobanov / Bark / ERB]` 方法完成跨发音人归一化。最终复核后的声学数据由 PhonTracer 导出，并在 `[统计软件名称，如 R / Python / SPSS]` 中完成后续统计检验与模型拟合。

**针对特定场景的补充说明：**

* **上下游工具协同采录**：若前期使用 [PhonAcq](https://github.com/KasumiKitsune/PhonAcq) 完成田野或实验室字表录音，可补充说明：“语料通过 PhonAcq 按统一词表独立建档采录（采样率 `[44.1 / 48]` kHz，16-bit PCM WAV），导出 `.teproj` 工程后载入 PhonTracer (v1.0.0) 完成边界精修与声学测量。”
* **外部标注导入**：目标音段边界初始由 `[Praat / Montreal Forced Aligner (MFA)]` 生成的 `TextGrid` 提供，导入 PhonTracer 后由研究者结合宽带语图与试听逐条复核调整。
* **图表生成说明**：本文声学图表由 PhonTracer 生成，图中曲线表示 `[Hz / 五度 T 值]` 标度下的 `[分组均值 / 单样本叠置轨迹]`，阴影区域表示 `[±1 标准差 / 95% 置信区间]`。

## 3. 引用与参考文献格式

如需在脚注或参考文献中列出软件，可按目标期刊体例选用以下标准格式（仓库根目录亦提供标准 `CITATION.cff`，可在 GitHub 页面右侧点击 **Cite this repository** 一键导出）：

**正文脚注简写：**

> 本研究使用 PhonTracer (v1.0.0) 完成声学参数提取、轨迹复核与数据导出。项目地址：https://github.com/KasumiKitsune/PhonTracer

**中国国家标准（GB/T 7714—2015，适用于中文语言学期刊）：**

> KasumiKitsune. PhonTracer: 面向语音学标注与声学分析的桌面工具套件 (v1.0.0)[CP/OL]. (2026-11-15)[引用日期]. https://github.com/KasumiKitsune/PhonTracer.

**国际期刊格式（APA 第 7 版）：**

> KasumiKitsune. (2026). *PhonTracer* (Version 1.0.0) [Computer software]. GitHub. https://github.com/KasumiKitsune/PhonTracer

**BibTeX 格式：**

```bibtex
@software{phontracer_2026,
  author  = {KasumiKitsune},
  title   = {PhonTracer},
  version = {1.0.0},
  year    = {2026},
  url     = {https://github.com/KasumiKitsune/PhonTracer},
  note    = {Computer software}
}
```

*(注：若底层声学计算涉及核心结论，建议同时按学术惯例一并引用 **Praat**（Boersma & Weenink）与 **Parselmouth**（Jadoul, Thompson, & de Boer, 2018）；若项目后续增补 Zenodo DOI，可优先采用含 DOI 的版本条目。)*

## 4. 投稿前核对清单

提交论文前建议对照核查以下事项：

### ✅ 正文“研究方法”应交代的要素

- [ ] **软件版本与底层引擎**：写明 `PhonTracer (v1.0.0)` 及底层调用 `Praat / Parselmouth` 算法。
- [ ] **核心声学参数**：基频上下限（`pitch floor / ceiling`）、共振峰搜索上限（`formant ceiling`，男女发音人不同时需分别列出）及归一化采样点数或稳态窗策略。
- [ ] **人工复核与排除标准**：说明音段边界是否经人工微调，以及倍频跳点修整、嘎裂声缺失保留或整词剔除的具体判定原则。
- [ ] **统计职责边界**：区分哪些描述性统计与图表由 PhonTracer 直接导出，哪些推断性模型（如混合效应模型 LMM、GAMM）在外部统计环境中完成。

### 📂 建议随附的开放材料 (Data Availability)

若目标期刊支持或要求开放研究材料，建议在公开数据仓库（如 OSF、Zenodo、GitHub）中归档：

- 复核定稿后的 `.teproj` 工程文件（或不含受试者隐私录音的 `TextGrid` 标注、`.ptwl` 高级字表与导出数据表 `XLSX / CSV`）；
- 由 Toolkit / CLI 导出的《研究方法报告》（含 SHA-256 工程指纹与参数审计记录）；
- 符合伦理知情同意范围的脱敏音频样本与后续统计脚本（R / Python）。

---

> **💡 总结**
> 论文中不要只笼统地写“使用 PhonTracer 分析”，而要写清楚“提取了什么参数、设置了什么阈值、如何进行人工复核、排除了哪些数据、最后在哪里做统计”。
