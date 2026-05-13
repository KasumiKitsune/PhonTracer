<div align="center">
  <img src="assets/logo.png" alt="PhonTracer Logo" width="180">
  <h1>PhonTracer</h1>
  <p><strong>一款专注、高效的语音声调特征批量提取工具</strong></p>

  <p>
    <img src="https://img.shields.io/badge/Python-3.10+-3776AB?logo=python&logoColor=white" alt="Python">
    <img src="https://img.shields.io/badge/Platform-macOS%20|%20Windows-000000" alt="Platform">
    <img src="https://img.shields.io/badge/UI-CustomTkinter-blue" alt="UI">
  </p>
</div>

---

### <img src="https://img.icons8.com/material-rounded/24/000000/info.png" width="20" style="vertical-align: middle;"/> 项目简介

> **PhonTracer** 是一个专为语音学、方言学和声调声学分析设计的轻量级桌面工具。
> 其**核心功能**是：将输入的语音音频，自动化、批量地转换为提取好核心声调（基频）特征的结构化数据格式。

无论你是处理整段的长录音，还是已经切分好的独立字音文件，本工具都能帮助你快速定位有效发音段，提取基频（F0）数据，并导出为标准格式。

---

### <img src="https://img.icons8.com/material-rounded/24/000000/process.png" width="20" style="vertical-align: middle;"/> 声学处理流与可靠性验证

本项目在底层深度集成了 **Praat** 的核心算法（通过 `parselmouth` 调用），确保所提取的 F0 数据与在 Praat 软件中手动测量的结果具有**同等的可靠性**。

以下是程序将生语料转化为标准化数据的详细算法流程：

```mermaid
graph TD
    A[原始音频信号 .wav/.mp3] --> B[Praat Parselmouth 引擎]
    
    subgraph 宏观处理 Macro-Level
    B --> C[Intensity 强度提取]
    C --> D{VAD 语音端点检测}
    D -- "粗切分" --> E[音节候选段落]
    end
    
    subgraph 微观定位 Micro-Level (元音核心)
    E --> F[Praat 基频算法 To_Pitch]
    E --> G[最大能量点 Peak 定位]
    G --> H{双向能量阈值截断\n根据 Drop dB 向两侧寻找边界}
    H -- "排除边缘绝对静音\n约束: 最短有效时长" --> I[精确定位元音核心\nVowel Nucleus]
    end
    
    subgraph 归一化与输出 Normalization
    F --> J
    I --> J[指定 N 等分点\n进行时间轴等距采样]
    J --> K[时间点与 F0 对齐]
    K --> L[(标准化数据表格)]
    end
    
    style B fill:#e1f5fe,stroke:#0288d1,stroke-width:2px
    style F fill:#e1f5fe,stroke:#0288d1,stroke-width:2px
    style I fill:#f3e5f5,stroke:#8e24aa,stroke-width:2px
    style L fill:#e8f5e9,stroke:#388e3c,stroke-width:2px
```

---

### <img src="https://img.icons8.com/material-rounded/24/000000/layers.png" width="20" style="vertical-align: middle;"/> 核心工作流程

工具提供两种导入模式，以适应不同的语料整理习惯：

<table width="100%">
  <tr>
    <td width="50%" valign="top">
      <h4><img src="https://img.icons8.com/material-rounded/20/000000/folder-invoices.png" width="18" style="vertical-align: middle;"/> 模式一：单条长音频切分</h4>
      <p><i>适用于一次性录制了整张字表、未做后期剪辑的超长音频。</i></p>
      <ul>
        <li><b>导入长音频</b>：加载完整录音文件</li>
        <li><b>文本匹配</b>：粘贴字表，执行自动 VAD 切分</li>
        <li><b>可视化微调</b>：通过交互界面手动修正边界</li>
        <li><b>精准提取</b>：定位元音核心并导出数据</li>
      </ul>
    </td>
    <td width="50%" valign="top">
      <h4><img src="https://img.icons8.com/material-rounded/20/000000/documents.png" width="18" style="vertical-align: middle;"/> 模式二：多条独立音频提取</h4>
      <p><i>适用于已经将每个字/词剪辑为独立文件的语料库。</i></p>
      <ul>
        <li><b>批量导入</b>：一键选中所有单字音频文件</li>
        <li><b>智能映射</b>：
          <ul>
            <li>模糊匹配：自动根据文件名识别文字</li>
            <li>顺序匹配：按文件导入顺序对应</li>
          </ul>
        </li>
        <li><b>并行提取</b>：多进程加速，同步处理所有语料</li>
      </ul>
    </td>
  </tr>
</table>

---

### <img src="https://img.icons8.com/material-rounded/24/000000/settings.png" width="20" style="vertical-align: middle;"/> 核心参数控制

- **等分点 (N)**：在元音核心段内均匀采样基频的次数（默认 11 点），是归一化声调曲线的基础。
- **能量落差 (dB)**：算法基于最大能量点向两侧寻找边界，设定允许的能量下降阈值。
- **最短时长**：自动过滤极短的突发噪声，确保数据有效性。
- **边缘静音裁切**：自动忽略两端低于 -50dB 的绝对静音，提高波形聚焦度。

---

### <img src="https://img.icons8.com/material-rounded/24/000000/database.png" width="20" style="vertical-align: middle;"/> 输出数据

所有流程的最终目的都是输出高度标准化的数据，包含：
- **元数据**：单字名称、所属声调组别
- **时间对齐**：在原始音频中的起止时间点
- **声学特征**：等分采样提取出的 Pitch (F0) 数值序列

> 该格式可无缝导入 **Excel**、**R**、**Python (Pandas)** 中，用于声学统计分析和传统五度值声调格局图的绘制。

---

### <img src="https://img.icons8.com/material-rounded/24/000000/console.png" width="20" style="vertical-align: middle;"/> 本地运行

```bash
# 1. 克隆仓库
git clone https://github.com/KasumiKitsune/Tone_extractor.git
cd Tone_extractor

# 2. 安装依赖 (建议在虚拟环境下执行)
pip install -r requirements.txt

# 3. 启动程序
python main.py
```

<br>

<div align="center">
  <img src="assets/icon.png" alt="PhonTracer Icon" width="60">
  <p>© 2026 KasumiKitsune</p>
</div>
