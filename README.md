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

### 📖 项目简介

> **PhonTracer** 是一个专为语音学、方言学和声调声学分析设计的轻量级桌面工具。
> 其**核心功能**是：将输入的语音音频，自动化、批量地转换为提取好核心声调（基频）特征的结构化数据格式。

无论你是处理整段的长录音，还是已经切分好的独立字音文件，本工具都能帮助你快速定位有效发音段，提取基频（F0）数据，并导出为标准格式。

---

### 🚀 核心工作流程

工具提供两种导入模式，以适应不同的语料整理习惯：

<table width="100%">
  <tr>
    <td width="50%" valign="top">
      <h4>📂 模式一：单条长音频切分</h4>
      <p><i>适用于一次性录制了整张字表、未做后期剪辑的超长音频。</i></p>
      <ol>
        <li><b>导入长音频</b>：加载完整录音文件 (wav/mp3)</li>
        <li><b>文本匹配</b>：粘贴字表文本，程序执行自动端点检测 (VAD)</li>
        <li><b>可视化微调</b>：通过交互界面手动微调切分边界</li>
        <li><b>精准提取</b>：应用算法精准定位元音核心并导出数据</li>
      </ol>
    </td>
    <td width="50%" valign="top">
      <h4>🗂️ 模式二：多条独立音频提取</h4>
      <p><i>适用于已经将每个字/词剪辑为独立文件的语料库。</i></p>
      <ol>
        <li><b>批量导入</b>：一键选中所有单字音频文件</li>
        <li><b>智能映射</b>：
          <ul>
            <li>模糊匹配：自动根据文件名识别文字</li>
            <li>顺序匹配：按文件导入顺序强制对应</li>
          </ul>
        </li>
        <li><b>并行提取</b>：利用多进程加速，同步处理所有语料</li>
      </ol>
    </td>
  </tr>
</table>

---

### ⚙️ 核心参数控制

为了保证提取出的声调数据具有严谨的声学参考价值，工具提供了直观的参数调优：

- 🎯 **等分点 (N)**：在元音核心段内均匀采样基频的次数（默认为 11 点），是归一化声调曲线的基础。
- 📉 **能量落差 (dB)**：基于最大能量点向两侧寻找边界，设定允许的能量下降阈值。
- ⏱️ **最短时长**：自动过滤极短的突发噪声，确保数据有效性。
- ✂️ **边缘静音裁切**：自动忽略两端低于 -50dB 的绝对静音，提高波形聚焦度。

---

### 📊 输出数据

所有流程的最终目的都是输出高度标准化的数据，包含：
- **元数据**：单字名称、所属声调组别
- **时间对齐**：在原始音频中的起止时间点
- **声学特征**：等分采样提取出的 Pitch (F0) 数值序列

> 该格式可无缝导入 **Excel**、**R**、**Python (Pandas)** 中，用于声学统计分析和传统五度值声调格局图的绘制。

---

### 🛠️ 本地运行

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
  <p>© 2026 PhonTracer Team</p>
</div>
