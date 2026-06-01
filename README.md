<div align="center">
  <img src="assets/logo.png" alt="PhonTracer Logo" width="180">
  <h1>PhonTracer</h1>
  <p><strong>面向语音学标注与声学分析的桌面工具套件</strong></p>

  <p>
    <a href="https://github.com/KasumiKitsune/PhonTracer/releases"><img src="https://img.shields.io/badge/release-v1.2.1-blue.svg" alt="Release"></a>
    <a href="https://www.python.org/"><img src="https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white" alt="Python"></a>
    <a href="#安装"><img src="https://img.shields.io/badge/platform-Windows%20%7C%20macOS-lightgrey.svg" alt="Platform"></a>
    <img src="https://img.shields.io/badge/UI-CustomTkinter-blue" alt="UI">
  </p>
</div>

---

### <img src="https://img.icons8.com/material-rounded/24/000000/info.png" width="20" style="vertical-align: middle;"/> 项目简介

> **PhonTracer** 是一个面向语音学标注与声学分析的桌面工具套件，支持批量提取和人工复核声调 `F0`、共振峰 `F1/F2`，并提供 `.teproj` 工程保存、异常提示、多说话人管理、科学图表导出和 Windows 命令行工作台。

项目底层通过 [Parselmouth](https://parselmouth.readthedocs.io/) 调用 **Praat** 的声学分析能力，适合需要“自动提取 + 可视化复核 + 批量导出”工作流的研究和教学场景。

---

### <img src="https://img.icons8.com/material-rounded/24/000000/star.png" width="20" style="vertical-align: middle;"/> 核心能力

- **双分析模式**：支持声调 `F0` 与共振峰 `F1/F2` 提取，可按任务切换分析模式。
- **自动分析与人工复核结合**：在声谱图中查看轮廓、试听音频、调整边界，并删除明显异常的分析点。
- **两类导入流程**：支持“长音频 + 字表”切分，也支持批量导入独立音频文件。
- **TextGrid 互操作**：支持导入和导出 TextGrid，便于与 Praat 工作流衔接。
- **多说话人管理**：每位说话人拥有独立的 F0 与共振峰参数，可按说话人导出结果。
- **异常提示**：在项目树中提示边界问题、分析点缺失、跳变异常和跨边界拆分风险，帮助用户定位需要复核的条目。
- **工程归档**：通过 `.teproj` 保存音频、分析结果、参数和人工修改，便于中断后继续工作。
- **科学图表导出**：支持 `PNG`、`SVG` 和 `PDF`，覆盖 F0 轮廓、分布、密度、热图以及共振峰空间和轨迹等图表。

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
    <td>主桌面程序：导入、分析、人工复核、异常检查、工程保存和导出</td>
    <td>Windows、macOS</td>
  </tr>
  <tr>
    <td><b>Toolkit</b></td>
    <td>独立工具箱：音频合并、长音频切分、批量整理和工程预览</td>
    <td>Windows、macOS</td>
  </tr>
  <tr>
    <td><b>PhonTracerCLI</b></td>
    <td>面向批处理与 AI 代理的命令行工作台</td>
    <td>当前随 Windows 套件发布</td>
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

    A["音频、字表或 TextGrid"]:::input --> B{"选择导入方式"}:::route
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
    J --> K["TXT、TextGrid、XLSX、图表导出"]:::export
    I --> L["保存 .teproj 工程"]:::export
```

**一个典型工作流如下：**

1. 创建或切换说话人，并设置适合该说话人的分析参数。
2. 导入长音频和字表、批量导入独立音频，或载入已有 TextGrid。
3. 选择声调 `F0` 或共振峰 `F1/F2` 模式并执行分析。
4. 在声谱图中试听音频、检查分段边界和分析点，必要时手动修正。
5. 根据项目树中的异常提示复核可疑条目。
6. 导出所需的数据报告、科学图表或 TextGrid。
7. 将当前工作保存为 `.teproj` 工程，便于后续继续分析。

---

### <img src="https://img.icons8.com/material-rounded/24/000000/settings.png" width="20" style="vertical-align: middle;"/> 分析模式

#### 声调 F0
- 默认通过 Parselmouth 调用 Praat 自相关音高分析。
- 支持设置音高下限、音高上限、静音阈值、前端跳过比例和浊音阈值。
- 支持按说话人估计推荐 F0 范围。
- 支持在声谱图中检查轮廓，并删除明显异常的 F0 点。
- 对无法可靠测量的片段保留缺失值，避免将嘎裂声等异常发声误当作稳定 F0。

#### 共振峰 F1/F2
- 通过 Parselmouth 调用 Praat 的 Burg 共振峰分析。
- 支持提取和显示 `F1`、`F2`，内部同时保留 `F3` 数据用于导出和检查。
- 支持在声谱图中检查共振峰轨迹，并删除明显异常的分析点。
- 支持根据当前说话人的样本生成参数建议。

**共振峰主要参数说明：**

| 参数 | 内部字段 | 默认值 | 说明 |
| --- | --- | --- | --- |
| 最大共振峰频率 | `formant_max_hz` | `5500` | 控制共振峰搜索范围 |
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
| **长音频 + 字表** | 连续录音按词条拆分 | 字表支持分组标题；程序根据音频分析和条目顺序生成切分结果 |
| **独立音频批量导入** | 每个词条已有单独音频文件 | 可批量导入 `WAV` 和 `MP3` 文件 |
| **TextGrid 导入** | 已有 Praat 标注或需要继续复核 | 支持载入已有分段信息，并在当前工程中继续分析 |

#### 人工复核与异常提示

自动提取不是最终结论。PhonTracer 将分析结果放回可试听、可编辑的声谱图界面，帮助用户完成复核：

- 播放当前音频并查看声谱图
- 调整词条和字符边界
- 检查 F0 轮廓或共振峰轨迹
- 删除明显异常的分析点
- 在项目树中定位“需要检查”的条目

> [!NOTE]
> 项目树会针对边界异常、分析点缺失、有效点比例不足、跳变异常以及跨边界拆分风险给出提示。提示用于辅助人工检查，不应被理解为自动质量认证。

---

### <img src="https://img.icons8.com/material-rounded/24/000000/save.png" width="20" style="vertical-align: middle;"/> 工程保存与恢复

PhonTracer 使用 `.teproj` 作为可移植工程归档格式。工程可保存：

- 说话人和参数设置
- 导入的音频
- 条目、分组和边界信息
- F0 与共振峰分析结果
- 人工删除或调整后的分析状态

导入已有工程时，程序会先显示预览；如果当前工作区已有内容，可选择覆盖或叠加导入。

> [!TIP]
> 启用自动保存后，程序会将当前状态写入内部恢复工作区，并在下次启动时提供恢复提示。需要跨设备传输、迁移或分享时，请显式导出 `.teproj` 工程。

---

### <img src="https://img.icons8.com/material-rounded/24/000000/export.png" width="20" style="vertical-align: middle;"/> 导出能力

| 类别 | 格式 | 适用场景 |
| --- | --- | --- |
| **文本数据** | `TXT` | 轻量查看和后续脚本处理；文本结果采用制表符分隔 |
| **Praat 标注** | `TextGrid` | 与 Praat 标注和复核流程无缝衔接 |
| **表格分析** | `XLSX` | 批量结果、原始数据和分析图表 |
| **科学图表** | `PNG`、`SVG`、`PDF` | 报告、论文制图和批量图表归档 |
| **工程归档** | `.teproj` | 保存完整分析状态，便于恢复、迁移和分享 |

#### 科学图表

图表导出器支持当前说话人、分说话人和综合视图，可导出 `PNG`、`SVG` 与 `PDF`。支持预览、分页和取消导出。

- **F0 图表**：F0 轮廓图、F0 分布图、F0 密度图、数据质量图、综合热图。
- **共振峰图表**：共振峰空间图、共振峰轨迹图、共振峰密度图、共振峰综合热图。

---

### <img src="https://img.icons8.com/material-rounded/24/000000/download.png" width="20" style="vertical-align: middle;"/> 安装与快速开始

#### 安装

请优先从 [GitHub Releases](https://github.com/KasumiKitsune/PhonTracer/releases) 下载已构建版本。

**Windows**
- 安装版：运行发布页中的安装程序。
- 便携版：解压发布页中的 Windows 压缩包后运行 `PhonTracer.exe`。
- 安装程序会注册 `.teproj` 文件关联，并提供 `PhonTracer`、`Toolkit` 和 `PhonTracerCLI` 快捷入口。

**macOS**
- 下载发布页中的 `DMG`。
- 打开镜像后，将应用拖入“应用程序”目录。

> 当前 Windows 套件包含命令行入口；macOS 主要提供图形界面应用。

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

打包后的命令行执行示例：
```powershell
PhonTracerCLI.exe status
PhonTracerCLI.exe help export
PhonTracerCLI.exe modify_params analysis_mode=f0
PhonTracerCLI.exe modify_params analysis_mode=formant
```

---

### <img src="https://img.icons8.com/material-rounded/24/000000/book.png" width="20" style="vertical-align: middle;"/> 详细手册与更多资源

README 只提供快速概览。完整操作步骤、参数说明和进阶工作流请查看：
- 📖 **[详细用户手册](assets/manual/manual.md)**

#### 验证与测试

```bash
python -m pytest -q
python -m compileall -q main.py cli.py toolkit.py modules tests
```
> 在 `v1.2.1` 对应代码上，使用 Python 3.12 执行测试：`167 passed, 4 warnings`。

#### 更新与发布
- 可在软件的“关于”窗口中手动检查 GitHub Releases 更新。
- GitHub Actions 会在手动触发或推送版本标签时基于 `ToneExtractor_Suite.spec` 构建发布套件。

---

### <img src="https://img.icons8.com/material-rounded/24/000000/error.png" width="20" style="vertical-align: middle;"/> 已知边界

- 自动分析结果会受到录音质量、说话人参数和分段边界影响，正式使用前仍应人工复核。
- F0 范围估计和共振峰参数建议用于辅助配置，不是最终分析结论。
- 主程序与 `Toolkit` 的拖拽行为不同：主程序支持窗口级拖拽，`Toolkit` 请使用界面按钮导入。
- 自动保存用于内部恢复；需要归档、迁移或分享时，请显式导出 `.teproj` 工程。
- 当前命令行入口随 Windows 套件发布。

---

<br>

<div align="center">
  <img src="assets/icon.png" alt="PhonTracer Icon" width="60">
  <p>© 2026 KasumiKitsune</p>
</div>
