<div align="center">
  <img src="assets/logo.png" alt="PhonTracer Logo" width="180">
  <h1>PhonTracer</h1>
  <p><strong>面向语音学标注与声学分析的桌面工具套件</strong></p>

  <p>
    <a href="https://github.com/KasumiKitsune/PhonTracer/releases"><img src="https://img.shields.io/badge/release-v1.0.0-blue.svg" alt="Release"></a>
    <a href="https://www.python.org/"><img src="https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white" alt="Python"></a>
    <a href="#安装"><img src="https://img.shields.io/badge/platform-Windows%20%7C%20macOS-lightgrey.svg" alt="Platform"></a>
    <img src="https://img.shields.io/badge/UI-CustomTkinter-blue" alt="UI">
  </p>
</div>

---

### <img src="https://img.icons8.com/material-rounded/24/000000/info.png" width="20" style="vertical-align: middle;"/> 项目简介

> **PhonTracer** 是一个面向语音学标注与声学分析的桌面工具套件，支持批量提取和人工复核声调 `F0`、共振峰 `F1/F2`，并提供 `.teproj` 工程保存、异常提示、多说话人管理、科学图表导出和命令行工作台。

项目底层通过 [Parselmouth](https://parselmouth.readthedocs.io/) 调用 **Praat** 的声学分析能力，适合需要“自动提取 + 可视化复核 + 批量导出”工作流的研究和教学场景。

---

### <img src="https://img.icons8.com/material-rounded/24/000000/camera.png" width="20" style="vertical-align: middle;"/> 界面预览

<table width="100%">
  <tr>
    <td width="50%" align="center">
      <img src="assets/screenshots/01_main_gui_f0.png" alt="主程序 F0 分析与人工复核界面" width="100%"><br>
      <b>主程序 F0 分析与人工复核界面</b>
    </td>
    <td width="50%" align="center">
      <img src="assets/screenshots/04_toolkit_wordlist_editor.png" alt="Toolkit 高级字表 (.ptwl) 编辑器" width="100%"><br>
      <b>Toolkit 高级字表 (.ptwl) 编辑器</b>
    </td>
  </tr>
  <tr>
    <td width="50%" align="center">
      <img src="assets/screenshots/02_visualization_toolbox_f0.png" alt="声学科学可视化工具箱" width="100%"><br>
      <b>声学科学可视化工具箱</b>
    </td>
    <td width="50%" align="center">
      <img src="assets/screenshots/05_toolkit_custom_script_sandbox.png" alt="自定义脚本沙箱与 AI 协作面板" width="100%"><br>
      <b>自定义脚本沙箱与 AI 协作面板</b>
    </td>
  </tr>
  <tr>
    <td width="50%" align="center">
      <img src="assets/screenshots/03_f0_estimation_dialog.png" alt="发音人 F0 估计与参数推荐对话框" width="100%"><br>
      <b>发音人 F0 估计与参数推荐对话框</b>
    </td>
    <td width="50%" align="center">
      <img src="assets/screenshots/06_cli_agent_mode.png" alt="PhonTracerCLI 交互式命令行控制台" width="100%"><br>
      <b>PhonTracerCLI 交互式命令行控制台</b>
    </td>
  </tr>
</table>

---

### <img src="https://img.icons8.com/material-rounded/24/000000/star.png" width="20" style="vertical-align: middle;"/> 核心能力

- **双分析模式**：支持声调 `F0` 与共振峰 `F1/F2` 提取，可按任务切换分析模式。
- **自动分析与人工复核结合**：在声谱图中查看轮廓、试听音频、调整边界，并使用橡皮擦工具删除明显异常的分析点。
- **两类导入流程**：支持“长音频 + 字表”切分，也支持批量导入独立音频文件。
- **Praat 生态互操作**：支持导入和导出 `TextGrid` 及一键导出包含音频、标注、基频与共振峰的 `.Collection` 集合文件。
- **高级字表 (`.ptwl`) 与容错草稿解析**：支持带结构的 JSON 格式高级字表及人类随手记录的草稿 `.txt`/`.csv` 字表。导入后，组名、组标签、组备注、词项别名、自定义科研字段和人工复核状态将随 `.teproj` 穿透并附加于每个切分项，可在画图、导出或自定义脚本中调用。
- **发音人多维隔离**：每位发音人拥有独立的工作区、F0 与共振峰参数；支持单条目参数隔离（局部参数微调不污染同发音人的其他词项），并提供右键属性窗口核对参数来源。
- **异常提示**：在项目树中提示边界问题、分析点缺失、跳变异常和跨边界拆分风险，并提供项目树右侧状态短标签（如 `+N` 等）辅助定位需复核条目。
- **科学图表分组规则**：支持按“词项标签”、“组标签”、“复核状态”或“自定义科研字段”临时对图表重组上色；对符合特定格式的二字组数据自动支持连读变调方阵、调类效应时程图、音节时长对比图和均值热图。
- **自定义脚本沙箱与 AI 协作**：Toolkit 内置受控 Python 执行沙箱（可使用 numpy, scipy, matplotlib，禁用系统访问和未允许的库），支持一键生成工程概要 AI 提示词，由 AI 协作生成绘图代码，并支持将执行记录写入 `.teproj` 以供归档。
- **工程归档与报告生成**：支持将完整状态保存为 `.teproj`。在 Toolkit 或 CLI 中可直接导出包含工程 SHA-256 文件指纹、统计摘要、ANOVA 分析与脚本记录的研究方法报告。

---

### <img src="https://img.icons8.com/material-rounded/24/000000/layers.png" width="20" style="vertical-align: middle;"/> 套件组成

<table width="100%">
  <tr>
    <th width="20%">入口</th>
    <th width="50%">用途</th>
    <th width="30%">平台说明</th>
  </tr>
  <tr>
    <td><b>PhonTracer</b></td>
    <td>主桌面程序：导入、分析、人工复核、异常检查、参数隔离、工程保存和导出。</td>
    <td>Windows x64 / ARM64、macOS</td>
  </tr>
  <tr>
    <td><b>Toolkit</b></td>
    <td>独立处理工作台：音频合并、长音频切分、高级字表 (.ptwl) 编辑、TextGrid 转换、自定义 Python 脚本沙箱运行及研究方法报告生成。</td>
    <td>Windows x64 / ARM64、macOS</td>
  </tr>
  <tr>
    <td><b>PhonTracerCLI</b></td>
    <td>面向批处理与 AI 代理的命令行交互式/静默执行工作台，支持全流程 `--script-file` 跑批、`export_bundle` 一站式交付与自定义脚本库指令。</td>
    <td>随 Windows 套件发布（源码跨平台运行）</td>
  </tr>
</table>

---

### <img src="https://img.icons8.com/material-rounded/24/000000/process.png" width="20" style="vertical-align: middle;"/> 工作流程

```mermaid
flowchart LR
    %% ================= 样式库 =================
    classDef input fill:#E3F2FD,stroke:#1E88E5,stroke-width:2px,color:#000
    classDef route fill:#FFF3E0,stroke:#FB8C00,stroke-width:2px,color:#000
    classDef core fill:#FCE4EC,stroke:#D81B60,stroke-width:2px,color:#000
    classDef ui fill:#E8F5E9,stroke:#43A047,stroke-width:2px,color:#000
    classDef export fill:#F3E5F5,stroke:#8E24AA,stroke-width:2px,color:#000

    A["音频、字表(TXT/.ptwl)或 TextGrid"]:::input --> B{"选择导入方式"}:::route
    B --> C["长音频切分"]:::route
    B --> D["独立音频批量导入"]:::route
    B --> E["TextGrid 导入"]:::route
    C --> F{"选择分析模式"}:::core
    D --> F
    E --> F
    F --> G["声调 F0"]:::core
    F --> H["共振峰 F1/F2"]:::core
    G --> I["声谱图试听与人工复核"]:::ui
    H --> I
    I --> J["异常提示与条目检查"]:::ui
    J --> K["TXT、TextGrid、Collection、XLSX、图表导出"]:::export
    I --> L["保存 .teproj 工程"]:::export
```

**一个典型工作流如下：**

1. 创建或切换发音人，并设置适合该发音人的分析参数。
2. 导入长音频和字表（普通/草稿字表或 `.ptwl` 高级字表）、批量导入独立音频，或载入已有 TextGrid。
3. 选择声调 `F0` 或共振峰 `F1/F2` 模式并执行分析。
4. 在声谱图中试听音频、检查分段边界和分析点，必要时通过橡皮擦/剔除点工具手动修正。
5. 根据项目树中的异常提示或右键属性窗口复核可疑条目的底层配置。
6. 导出所需的数据报告、自定义分组科学图表、Praat `.Collection` 或 `TextGrid`。
7. 将当前工作保存为 `.teproj` 工程，或载入 Toolkit 进行高级字表编辑、沙盒脚本绘图和生成归档报告。

---

### <img src="https://img.icons8.com/material-rounded/24/000000/settings.png" width="20" style="vertical-align: middle;"/> 分析模式

#### 声调 F0
- 默认通过 Parselmouth 调用 Praat 自相关音高分析。
- 支持设置音高下限、音高上限、静音阈值、前端跳过比例和浊音阈值。
- 支持按发音人估计推荐 F0 范围（提供保守、推荐、精细三档）。
- 支持在声谱图中检查轮廓，并删除明显异常的 F0 点。
- 对无法可靠测量的片段保留缺失值，避免将嘎裂声等异常发声误当作稳定 F0。

#### 共振峰 F1/F2
- 通过 Parselmouth 调用 Praat 的 Burg 共振峰分析。
- 支持提取和显示 `F1`、`F2`，内部同时保留 `F3` 数据用于导出和检查。
- 支持在声谱图中检查共振峰轨迹，并删除明显异常的分析点（采用点定位算法）。
- 支持根据当前发音人的样本生成参数建议。

**共振峰主要参数说明：**

| 参数 | 内部字段 | 默认值 | 说明 |
| --- | --- | --- | --- |
| 共振峰分析上限 | `formant_max_hz` | `5500` | 控制 Praat Burg 搜索范围，并非 F2 数值上限 |
| 共振峰数量 | `formant_count` | `5` | Praat Burg 分析参数 |
| 窗长 | `formant_window_length` | `0.025` | 单位为秒 |
| 预加重 | `formant_pre_emphasis` | `50` | 单位为赫兹 |
| 采样策略 | `formant_sample_strategy` | `整段11点` | 控制导出时的采样位置 |

---

### <img src="https://img.icons8.com/material-rounded/24/000000/cursor.png" width="20" style="vertical-align: middle;"/> 输入与复核

#### 输入方式

PhonTracer 支持三类常见输入流程：

| 输入方式 | 适用场景 | 说明 |
| --- | --- | --- |
| **长音频 + 字表** | 连续录音按词条拆分 | 支持普通/草稿文本（分组名/词项）、CSV 与高级字表 `.ptwl` 格式 |
| **独立音频批量导入** | 每个词条已有单独音频文件 | 支持 `WAV` 和 `MP3` 文件，提供模糊匹配和顺序匹配模式 |
| **TextGrid 导入** | 已有 Praat 标注或需要继续复核 | 支持载入已有分段信息，并在当前工程中继续分析 |

#### 人工复核与异常提示

自动提取不是最终结论。PhonTracer 将分析结果放回可试听、可编辑的声谱图界面，帮助用户完成复核：

- 播放当前音频并查看声谱图
- 调整词条和字符边界（鼠标左右拖拽红蓝虚线）
- 检查 F0 轮廓或共振峰轨迹，利用滚轮调节十字准星感应半径进行异常点剔除
- 在项目树中定位“需要检查”的条目（根据状态短标签快速定位）

> [!NOTE]
> 项目树会针对边界异常、分析点缺失、有效点比例不足、跳变异常以及跨边界拆分风险给出提示。提示用于辅助人工检查，不应被理解为自动质量认证。

---

### <img src="https://img.icons8.com/material-rounded/24/000000/save.png" width="20" style="vertical-align: middle;"/> 工程保存与恢复

PhonTracer 使用 `.teproj` 作为可移植工程归档格式。工程可保存：

- 发音人和参数设置
- 导入的音频副本
- 条目、分组、边界信息及高级字表元数据
- F0 与共振峰分析结果
- 人工删除或调整后的分析状态
- 自定义脚本的执行记录、源码及运行日志

导入已有工程时，程序会先显示预览；如果当前工作区已有内容，可选择覆盖或叠加导入。

> [!TIP]
> 启用自动保存后，程序会将当前状态写入内部恢复工作区，并在下次启动时提供恢复提示。需要跨设备传输、迁移或分享时，请显式导出 `.teproj` 工程。

---

### <img src="https://img.icons8.com/material-rounded/24/000000/export.png" width="20" style="vertical-align: middle;"/> 导出能力

| 类别 | 格式 | 适用场景 |
| --- | --- | --- |
| **文本数据** | `TXT` | 轻量查看和后续脚本处理；文本结果采用制表符分隔 |
| **Praat 互操作** | `TextGrid`、`.Collection` | 导出标准三层 `groups / words / chars` TextGrid 或一键打包音频、标注、Pitch 与 Formant 的 `.Collection` |
| **表格分析** | `XLSX` | 包含数据和分析结果页，自动在 Excel 底层注入基频 T 值活公式和共振峰均值/离散度活公式，保留高级字表元数据 |
| **科学图表** | `PNG`、`SVG`、`PDF` | 支持当前/分发音人/整合导出，支持自定义分组重新上色与自动分页 |
| **工程归档** | `.teproj` | 保存完整分析状态、缓存、指纹及脚本运行历史 |

---

### <img src="https://img.icons8.com/material-rounded/24/000000/download.png" width="20" style="vertical-align: middle;"/> 安装与快速开始

#### 安装

请优先从 [GitHub Releases](https://github.com/KasumiKitsune/PhonTracer/releases) 下载对应平台与用途的已构建发布资产：

| 用途 | 平台 | 发布资产 | 内容与说明 |
|---|---|---|---|
| 主套件安装 | Windows x64 | `PhonTracer_Setup_Windows_x64.exe` | PhonTracer、Toolkit 与 PhonTracerCLI；注册 `.teproj`、`.ptwl` 文件关联 |
| 主套件便携 | Windows x64 | `PhonTracer_Suite-Windows-x64.zip` | 同上，解压后直接运行 |
| 主套件安装 | Windows ARM64 | `PhonTracer_Setup_Windows_ARM64.exe` | 原生 ARM64 安装包 |
| 主套件便携 | Windows ARM64 | `PhonTracer_Suite-Windows-ARM64.zip` | 原生 ARM64 便携压缩包 |
| 主套件 | macOS Apple Silicon | `PhonTracer_Suite-macOS.dmg` | PhonTracer 与 Toolkit（ARM64） |

Windows 便携版解压后可直接运行 `PhonTracer.exe`、`Toolkit.exe` 或 `PhonTracerCLI.exe`。macOS 用户打开 DMG 后将 `.app` 拖入“应用程序”目录即可使用。

#### 从源码运行

建议使用 Python 3.12 环境：

```bash
git clone https://github.com/KasumiKitsune/PhonTracer.git
cd PhonTracer
python -m pip install -r requirements.txt
python main.py
```

其他入口的启动方式：
```bash
python toolkit.py
python cli.py
```

#### Windows CLI 示例

打包后的命令行执行示例（支持 `--script-file` 全流程跑批、单项执行回显与受控脚本管理）：
```powershell
# 查询状态与获取帮助
PhonTracerCLI.exe --version
PhonTracerCLI.exe agent_guide
PhonTracerCLI.exe help all
PhonTracerCLI.exe help tools

# 单次剧本跑批与一站式闭环导出
PhonTracerCLI.exe --project session.teproj --script-file pipeline.txt
```

---

### <img src="https://img.icons8.com/material-rounded/24/000000/book.png" width="20" style="vertical-align: middle;"/> 详细手册与更多资源

README 只提供快速概览。完整操作步骤、参数说明和进阶工作流请查看：
- 📖 **[详细用户手册](assets/manual/manual.md)**
- 📄 **[详细 HTML 用户手册](assets/manual/manual.html)**

#### 验证与测试

```bash
python -m pytest -q tests
python -m compileall -q main.py cli.py toolkit.py modules tests
python scripts/check_release_version.py --expected v1.0.0
```

#### 更新与发布
- 可在软件的“关于”窗口中手动检查 GitHub Releases 更新。
- GitHub Actions 会在推送版本标签时基于 `PhonTracer_Suite.spec` 自动构建跨平台发布套件。

#### 核心文档与工程格式

- [架构与共享事实源](docs/ARCHITECTURE.md)
- [`.teproj` 工程格式 1.0](docs/PROJECT_FORMAT_V1.md)
- [论文使用与写作指南](docs/PAPER_USAGE_GUIDE.md)

---

### <img src="https://img.icons8.com/material-rounded/24/000000/error.png" width="20" style="vertical-align: middle;"/> 已知边界

- 自动分析结果会受到录音质量、发音人参数和分段边界影响，正式使用前仍应人工复核。
- F0 范围估计和共振峰参数建议用于辅助配置，不是最终分析结论。
- 主程序与 `Toolkit` 的拖拽行为不同：主程序支持窗口级拖拽，`Toolkit` 为规避 GIL 锁冲突限制了外部文件拖入，请使用界面按钮导入。
- 自动保存用于内部恢复；需要归档、迁移或分享时，请显式导出 `.teproj` 工程。
- 本软件纯单机离线运行，零遥测、不上传任何录音与工程数据（主程序本地工作区与设置默认位于 `~/.phon_tracer/`）；Toolkit 自定义脚本为进程内执行，请仅运行可信脚本。

---

<br>

<div align="center">
  <img src="assets/icon.png" alt="PhonTracer Icon" width="60">
  <p>© 2026 KasumiKitsune</p>
</div>
