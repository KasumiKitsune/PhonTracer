# PhonTracer：面向实验语音学与声调分析的学术可用性论证报告

<p style="font-family: sans-serif; color: #555; line-height: 1.6;">
<img src="https://img.icons8.com/material-rounded/24/000000/info.png" width="18" style="vertical-align: middle;"/> <b>核心论点</b>：PhonTracer 在算法底层、特征处理流程及数据产出格式上，具备与“人工操作 Praat + 经验证的 Praat 脚本”同等的学术有效性和严谨性。其自动化流程未引入任何损害数据真实性的伪像（Artifacts），且内置的安全机制能够满足主流语音学、方言学核心期刊对研究数据的标准要求。
</p>

---

## <img src="https://img.icons8.com/material-rounded/24/000000/microchip.png" width="22" style="vertical-align: middle;"/> 1. 底层声学特征提取的数学等效性

语音分析软件的学术根基在于其频域和时域转换算法的准确性。PhonTracer 并未“闭门造车”重写基础信号处理算法，而是采用了学术界最高标准的封装方案。

### 1.1 Praat 核心算法的无损透传 (Parselmouth)
本项目的默认基频引擎基于 `praat-parselmouth` 库。与通过命令行挂载 Praat 进程或使用纯 Python 逼近重写的库不同，Parselmouth 是直接通过 Python C-API 绑定和编译了 Praat 原始的 C/C++ 源码（Jadoul, Thompson, & de Boer, 2018）。
* **数学等效性**：软件调用的 `snd.to_pitch_ac()` 函数，其输入参数（Pitch floor, Pitch ceiling, Voicing threshold 等）与底层自相关算法（Autocorrelation）与在 Praat 图形界面中点击 *Analyze periodicity - To Pitch (ac)* 所生成的运算矩阵**在数学上是完全等效且一致的**（Boersma & Weenink, 2021）。

### 1.2 工业级抗噪补充引擎：REAPER
针对高底噪或 Praat 容易出现基频翻倍/减半跳跃（Octave jumps）的复杂田野语料，系统引入了 Google 开源的 REAPER（Robust Epoch And Pitch EstimatoR）引擎作为备选。
* **算法严谨性**：REAPER 采用的是基于 Epoch 提取（声带闭合瞬间提取）的算法（Talkin, 1995），该算法已被证明在极高采样率下对浊音段有极强的鲁棒性。PhonTracer 严格按 REAPER 规范将其内部重采样为 16kHz，并将无声段准确映射为 `0.0`，为复杂的自然口语提取提供了权威级的二次保障。

---

## <img src="https://img.icons8.com/material-rounded/24/000000/code.png" width="22" style="vertical-align: middle;"/> 2. 切分与元音起点 (VOP) 定位的信号处理规范

批量提取声调的核心难点在于避开首辅音（尤其是清擦音、送气音）的干扰，准确定位韵母的发音核心段。传统的自动化脚本多采用“固定比例裁切”（如裁去首尾 10%）这种粗暴且不严谨的方法。PhonTracer 在此引入了学术级信号处理方案。

### 2.1 基于 STE 与 ZCR 的联合检测
算法在 `detect_vowel_onset` 模块中，利用了短时能量（Short-Time Energy, STE）的一阶导数寻峰，并引入了过零率（Zero-Crossing Rate, ZCR）作为极强的惩罚项。
* 擦音（如 /s/, /f/）具有高频特性，体现为极高的 ZCR。系统算法施加了 `zcr_penalty`，将 ZCR 较高的清辅音候选点权重强制归零。
* **去除直流偏置（DC Offset Removal）**：算法在计算过零率前，严格执行了波形中心化（`frame - np.mean(frame)`），消除了录音设备由于电路问题导致的基线偏移，避免了由硬件缺陷引起的学术测量误差。

### 2.2 宏观自适应底噪估计
在长音频切分中，程序并非采用死板的绝对分贝阈值，而是通过排序提取出当前音频静音段的特征能量，计算自适应的 Noise floor，这保证了不同信噪比的录音在提取时保持客观一致的标准。

---

## <img src="https://img.icons8.com/material-rounded/24/000000/shield.png" width="22" style="vertical-align: middle;"/> 3. 坚守“拒绝伪造数据”的科研底线

许多开源的自动化 F0 提取工具为求“曲线平滑好看”，会肆意使用样条插值跨越长静音段，这在严谨的语言学研究中是严重的造假行为。

### 3.1 严苛的基频采样与插值回退机制 (Interpolation Fallback)
声调格局研究通常需要提取 11 点等距 F0 数据。PhonTracer 在实现 `np.interp` 线性插值时加入了严格的时间跨度校验：
* **25ms 断崖阻断规则**：如果提取时间点附近有效数据缺失超过 25ms（即该时段声带确实未振动），系统会立刻拒绝插值，强制将该点数值退回 `0.0`。绝不利用插值算法去“凭空搭桥”无声段的数据。
* **数据空值预警**：一旦 11 点数据中存在 `0.0`，程序将在 UI 与数据层抛出 `has_empty_data` 警告，交由研究者最终裁决。

### 3.2 Human-in-the-loop 人机协同验证
完全的“黑盒自动化”是不受实验语音学信任的。PhonTracer 保留了高精度的时频谱图（Spectrogram），研究者能够所见即所得地拖拽红蓝线，以及使用“F0 橡皮擦”功能直接擦除 Praat 误判的微小噪音基频。这种设计将**批处理的计算效率**与**人工复核的严谨性**实现了完美平衡。

---

## <img src="https://img.icons8.com/material-rounded/24/000000/database.png" width="22" style="vertical-align: middle;"/> 4. 后处理与格局建模的数据合规性

### 4.1 T 值归一化模型的准确复现
声调研究中，为了消除发音人个体的生理差异（如男女声频区不同），普遍采用石锋（1990）等学者倡导的 T 值归一化（T-value Normalization）。
* PhonTracer 在导出模块严格部署了该公式：$T = 5 \times \frac{\log(x) - \log(\min)}{\log(\max) - \log(\min)}$。
* 在多发音人联合分析时，算法会先独立计算每个发音人的全量统计数据（整体 F0 极值），再分别归一化计算，彻底避免了跨说话人数据池污染。

### 4.2 TextGrid 文件的合法输出
导出的 TextGrid 文件严格遵循 Praat `.ooTextFile` 面向对象的拓扑规范。为满足高标准的研究需求，生成的 `IntervalTier` 中的时间切片是**严格连续**的，所有非语音段（空白间隙）均已使用空字符串 `""` 闭合。这确保了生成的 TextGrid 在导入 R 语言、Python 脚本或 Praat 进行二次处理时，不会报任何格式非法错误。

---

## <img src="https://img.icons8.com/material-rounded/24/000000/checked.png" width="22" style="vertical-align: middle;"/> 5. 结论

通过对核心代码逻辑、依赖库机制及数据惩罚策略的分析可见，**PhonTracer 在声学特征抓取的精准度上等同于原生 Praat**。同时，其通过 STE/ZCR 惩罚算法实现的自动化切分，大幅优于传统的固定比例裁切脚本。项目对“伪造平滑数据”的零容忍机制与规范的 T 值/TextGrid 输出流，确保了产出数据的高保真性。

因此，在研究生教学及实验室级的研究流程中，**PhonTracer 完全具备替代“手工 Praat 切分 + 传统宏脚本批处理”工作流的学术可用性和可靠性**。

---

## <img src="https://img.icons8.com/material-rounded/24/000000/quote.png" width="22" style="vertical-align: middle;"/> 参考文献

* Boersma, P., & Weenink, D. (2021). *Praat: doing phonetics by computer* [Computer program]. Version 6.1.51. http://www.praat.org/
* Jadoul, Y., Thompson, B., & de Boer, B. (2018). Introducing Parselmouth: A Python interface to Praat. *Journal of Phonetics*, 71, 1-15.
* Talkin, D. (1995). A robust algorithm for pitch tracking (RAPT). In *Speech coding and synthesis* (pp. 495-518). Elsevier.
* 石锋. (1990). 汉语声调的声学格局. *南开语言学刊*.
* Rose, P. (1987). Acoustics and phonology of complex tone sandhi. *Phonetica*, 44(4), 207-227.
