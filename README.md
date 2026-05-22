<div align="center">
  <img src="assets/logo.png" alt="PhonTracer Logo" width="180">
  <h1>PhonTracer</h1>
  <p><strong>一款专注、高效的语音声调特征批量提取工具</strong></p>

  <p>
    <img src="https://img.shields.io/badge/Python-3.10+-3776AB?logo=python&logoColor=white" alt="Python">
    <img src="https://img.shields.io/badge/Platform-macOS%20|%20Windows-000000" alt="Platform">
    <img src="https://img.shields.io/badge/UI-CustomTkinter-blue" alt="UI">
    <img src="https://img.shields.io/badge/Release-v1.0.0-10B981" alt="Release">
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
flowchart TD
    %% ================= 样式库 =================
    classDef input fill:#E3F2FD,stroke:#1E88E5,stroke-width:2px,color:#000
    classDef route fill:#FFF3E0,stroke:#FB8C00,stroke-width:2px,color:#000
    classDef core fill:#FCE4EC,stroke:#D81B60,stroke-width:2px,color:#000
    classDef ui fill:#E8F5E9,stroke:#43A047,stroke-width:2px,color:#000
    classDef export fill:#F3E5F5,stroke:#8E24AA,stroke-width:2px,color:#000
    classDef note fill:#FFFFE0,stroke:#FBC02D,stroke-width:1px,stroke-dasharray: 5 5,color:#000

    %% ================= 1. 输入与解析层 =================
    subgraph L1 ["一、 输入与环境初始化层"]
        direction LR
        S_Man["多发音人管理器<br/>Speaker Manager<br/>(隔离参数与缓存)"]:::input
        IN_A["音频输入<br/>WAV / MP3"]:::input
        IN_T["字表/文本输入<br/>TXT / CSV / 剪贴板 / TextGrid"]:::input
        
        Parser["智能文本解析器<br/>1. 识别【组别】标签<br/>2. CJK汉字/斜杠 强制单字拆分"]:::route
        
        IN_T --> Parser
        S_Man --> IN_A
    end

    %% ================= 2. 调度与宏观处理层 =================
    subgraph L2 ["二、 双轨宏观调度层 (支持并行处理)"]
        direction TB
        Mode{音频模式判断}:::route
        
        %% 长音频流
        L_VAD["全局F0缓存 + 宏观VAD静音切分<br/>(自适应底噪阈值提取粗略语音段)"]:::route
        L_VS["Visual Splitter 段落编辑器<br/>人工校对 / 增删切分线"]:::ui
        L_Map["音频段落与字表按序映射"]:::route
        
        %% 独立音频流
        B_Pool["多进程并行读取音频"]:::route
        B_Match["智能文本匹配策略<br/>(按文件名模糊匹配 / 按序匹配)"]:::route
        B_Map["独立音频与单个文本映射"]:::route

        Mode -- "长音频模式" --> L_VAD --> L_VS --> L_Map
        Mode -- "独立音频模式" --> B_Pool --> B_Match --> B_Map
    end

    IN_A --> Mode
    Parser --> L_Map & B_Match

    %% ================= 3. 核心声学微观引擎 =================
    subgraph L3 ["三、 核心声学处理引擎 Audio Core (底层黑盒)"]
        direction TB
        Engine{基频引擎选择}:::core
        
        E_Praat["Praat 引擎<br/>(自相关算法, 透传上下限与浊音阈值)"]:::core
        E_Reaper["REAPER 引擎<br/>(16kHz重采样, 浊音阈值反转映射, 高斯滤波消除阶跃)"]:::core
        
        VOP["VOP 元音起点智能检测算法<br/>(STE 能量一阶导数 + ZCR 过零率惩罚)<br/>精准避开 s/f/t 等清辅音干扰"]:::core
        Trim["-50dB 智能边缘静音收缩"]:::core
        
        InnerSplit{是否多音节词?}:::core
        Split_Y["词内字间切分<br/>对能量曲线平滑并寻找谷底 (生成蓝线)"]:::core
        Split_N["直接沿用宏观边界"]:::core
        
        Sampling["11点 F0 等距采样插值<br/>(若跨越静音区 > 25ms 强制归零, 防假数据桥接)"]:::core

        Engine -- "默认" --> E_Praat --> VOP
        Engine -- "强抗噪" --> E_Reaper --> VOP
        
        VOP --> Trim --> InnerSplit
        InnerSplit -- "是" --> Split_Y --> Sampling
        InnerSplit -- "否" --> Split_N --> Sampling
    end

    L_Map & B_Map --> Engine

    %% ================= 4. UI与人机交互层 =================
    subgraph L4 ["四、 所见即所得交互层 (Human-in-the-loop)"]
        direction LR
        UI_Spec["高精度时频谱图面板<br/>Spectrogram Panel"]:::ui
        
        Act_Drag["鼠标拖拽红蓝线<br/>人工微调字词边界"]:::ui
        Act_Erase["F0 橡皮擦<br/>涂抹擦除 Praat 误识别的杂音基频"]:::ui
        Act_Play["音段高亮与局部试听"]:::ui
        
        UI_Spec --- Act_Drag & Act_Erase & Act_Play
    end

    %% 核心与 UI 的双向数据流（闭环机制）
    Sampling == "首次渲染呈现" ==> UI_Spec
    Act_Drag -. "实时回传时间坐标重算有效基频" .-> VOP
    Act_Erase -. "实时回传修改底层 Cache 强制归零" .-> Sampling

    %% ================= 5. 输出与分析层 =================
    subgraph L5 ["五、 自动化分析与图表导出层"]
        direction TB
        Out_Scope{导出范围选择<br/>单发音人 vs 多发音人整合}:::export
        
        Out_Excel["Excel 深度公式化报告 .xlsx<br/>1. 原始Hz数据与时长写入<br/>2. 写入 AVERAGEIFS/MIN/MAX 原生公式<br/>3. 写入 T = 5*log 原生公式标调<br/>4. 内置生成真实时长的散点连线图"]:::export
        
        Out_Img["科研级图像导出 .png/.svg"]:::export
        Img_Line["声调格局连贯折线图"]:::export
        Img_KDE["SciPy KDE 时序密度热力图<br/>(反映发音集中区与游移区)"]:::export
        
        Out_TG["Praat TextGrid 标注导出<br/>(自动生成 Words/Chars/Groups 独立层级)"]:::export
        Out_Txt["纯文本数据流 .txt"]:::export
        
        Out_Scope -- "T值归一化算法" --> Out_Excel & Out_Img
        Out_Scope -- "单独导出" --> Out_TG & Out_Txt
        Out_Img --- Img_Line & Img_KDE
    end

    UI_Spec ==> Out_Scope
    
    %% 连接全局样式
    linkStyle default stroke:#666,stroke-width:2px;
    linkStyle 10,11,12 stroke:#D81B60,stroke-width:3px;
    linkStyle 19,20 stroke:#43A047,stroke-width:3px,stroke-dasharray: 5 5;
```

---

### <img src="https://img.icons8.com/material-rounded/24/000000/cursor.png" width="20" style="vertical-align: middle;"/> 人机交互

为了保证数据提取的高精确度，PhonTracer 设计了完整的所见即所得交互闭环，支持对声学特征和切分边界的实时人工微调：

```mermaid
sequenceDiagram
    autonumber
    actor User as 语言学研究者
    participant UI as 主界面 (PhoneticsApp)
    participant Spec as 频谱面板 (SpectrogramPanel)
    participant Core as 声学核心 (audio_core)
    participant Cache as 数据管理器 (SpeakerState)

    User->>UI: 拖拽导入长音频与字表
    UI->>Core: 提交宏观切分任务 (VAD)
    Note over UI,Core: 开启后台并发线程池 (ProcessPoolExecutor)
    Core-->>UI: 返回粗略段落 (Macro Segments)
    
    UI->>User: 弹出可视化段落编辑器 (Visual Splitter)
    User->>UI: 确认段落划分无误
    
    UI->>Core: 并行计算微观韵母 (F0, VOP, 字间切分)
    Core-->>Cache: 缓存 11点F0、时长、红蓝线边界
    
    UI->>Spec: 通知渲染波形与频谱图
    Spec-->>User: 呈现所见即所得界面
    
    %% 人机交互微调阶段
    rect rgb(240, 248, 255)
    Note right of User: 【用户微调阶段】
    User->>Spec: 鼠标拖拽微观红线/蓝线边界
    Spec->>Cache: 实时更新边界时间点 (start/end)
    Spec->>Core: 请求快速收缩静音并验证有效基频
    Core-->>Spec: 返回重算后的有效边界
    Spec-->>User: 画面实时跟随鼠标重绘，更新刻度
    end
    
    %% 橡皮擦功能
    rect rgb(255, 240, 245)
    Note right of User: 【F0 橡皮擦阶段】
    User->>Spec: 开启橡皮擦模式，涂抹异常基频
    Spec->>Cache: 将对应范围的 Hz 值置为 0.0
    Spec-->>User: 频谱图上对应的蓝色圆点消失
    Spec->>UI: 触发警告刷新 (检测是否全为空)
    end
    
    User->>UI: 点击导出按钮
    UI->>Cache: 拉取最新修改的缓存数据
    UI-->>User: 导出 Excel、TextGrid 与图表
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

所有流程的最终目的都是输出高度标准化的特征与图表，目前支持以下 5 种数据/格式导出：

<table width="100%">
  <tr>
    <td width="50%" valign="top">
      <h4>1. Excel 深度公式化报告 (.xlsx)</h4>
      <p><i>面向数据统计与公式化建模的深度报告。</i></p>
      <ul>
        <li>自动写入标准汇总数据、分析公式与逐点原始基频数据</li>
        <li>第三个工作表保留当前编辑后的 Hz 轨迹，包含橡皮擦置零点</li>
        <li>内置 <code>AVERAGEIFS/MIN/MAX</code> 原生公式，便于二次筛选</li>
        <li>应用 <code>T = 5*log</code> 经典归一化公式自动完成标调</li>
        <li>内置生成基于真实发音时长的散点连线图</li>
      </ul>
    </td>
    <td width="50%" valign="top">
      <h4>2. 声调格局折线图 (.png)</h4>
      <p><i>直观展示声调在声学空间中的时序演变。</i></p>
      <ul>
        <li>基于归一化 T 值或原始赫兹 (Hz) 生成格局折线图</li>
        <li>支持多发音人、多声调类型的对比与均值聚合</li>
        <li>可导出科研级的高分辨率位图 (PNG)</li>
      </ul>
    </td>
  </tr>
  <tr>
    <td width="50%" valign="top">
      <h4>3. KDE 时序密度热力图 (.png / .svg)</h4>
      <p><i>基于 SciPy 二维核密度估计 (KDE) 的可视化。</i></p>
      <ul>
        <li>直观反映样本在不同发音时序段的基频游移规律</li>
        <li>使用最终字段边界与当前基频缓存，忠实反映手动擦除后的数据缺口</li>
        <li>以热力图斑点深度揭示发音最集中的核心区</li>
        <li>极佳的科研配图，利于展示声调变体的离散度</li>
      </ul>
    </td>
    <td width="50%" valign="top">
      <h4>4. Praat TextGrid 标注文件 (.TextGrid)</h4>
      <p><i>方便在 Praat 中进行二次校验或联动分析。</i></p>
      <ul>
        <li>自动将切分结果生成为标准 TextGrid 标注文件</li>
        <li>包含独立的 Words (词)、Chars (字) 和 Groups (组别) 标记层</li>
        <li>可直接在 Praat 中打开与原始音频无缝对齐</li>
      </ul>
    </td>
  </tr>
  <tr>
    <td colspan="2" valign="top">
      <h4>5. 原始特征纯文本数据 (.txt)</h4>
      <p><i>最轻量、通用的原始文本数据流。</i></p>
      <ul>
        <li>以纯文本/CSV 格式输出每个切分音段的基频时序轨迹</li>
        <li>非常适合直接导入 R、Python (Pandas)、SPSS 等第三方软件进行深度自定义建模</li>
      </ul>
    </td>
  </tr>
</table>

> 以上导出的所有数据与图像，均可在应用内部直接一键生成，完美契合从“原始语料”到“科研级可视化与统计表”的完整分析闭环。


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
