# PhonRec × PhonTracer 后端联动实施方案

> 本文是可直接交给其他 AI agent 执行的实施合同。目标是完成以下五项能力：
>
> 1. 一键交给 PhonTracer 复核；
> 2. 共享统一声学分析内核；
> 3. 分析缓存直接写入工程；
> 4. 动态能力协商；
> 5. 数据完整性与操作日志。
>
> 本方案只覆盖本地 Tauri、回环 FastAPI 引擎、PhonTracer 主程序和 `.teproj` 工程之间的受控联动，不引入云端、账号系统或远程服务。

## 一、给执行 Agent 的总指令

请先完整阅读本方案，再检查当前工作树。已有改动属于用户或其他 agent，禁止覆盖、回滚或顺手重构不相关区域。实施时必须遵守以下原则：

1. 所有新代码注释、测试名称、错误提示、文档和提交说明均使用中文。
2. PhonRec 仍需保持轻量：Tauri 客户端不得打包 Python、NumPy、SciPy、Matplotlib 或 Parselmouth；完整分析继续由已安装的 PhonTracer 分析引擎提供。
3. 独立模式必须继续可用；新增完整模式功能必须由能力协商控制，不得导致独立模式退化。
4. 不得再复制一套 F0、共振峰或静音边界算法。后端和主程序必须共同调用同一共享内核。
5. 不破坏现有 `/api/*` 接口、协议版本 1、旧 `.teproj`、旧 PTWL 和现有工作区。新增字段必须具备向后兼容默认值。
6. 所有写入必须采用临时文件加原子替换；路径必须规范化并限制在受控工作区或受控交接目录内。
7. 日志哈希只能描述为“完整性校验”或“篡改/损坏检测线索”，不得宣传为数字签名、真实性证明或不可抵赖审计。
8. 不得把引擎初始化重新放回 Tauri 主线程；不得重新引入启动白屏。
9. 每完成一个阶段先运行该阶段测试，再进入下一阶段。发现需要扩大范围时先停下并报告，不得自行扩张到云同步、实时多人协作或自动发音评分。

## 二、当前真实架构与问题

### 2.1 现有组件

- `PhonRec/frontend/src-tauri/src/main.rs`
  - 发现并启动 `PhonTracerAnalysisEngine`；
  - 使用随机回环端口与会话 Bearer Token；
  - 管理完整模式、独立模式、录音设备和设置；
  - 当前只校验单一 `protocol_version`，未消费后端能力清单。
- `PhonRec/backend/main.py`
  - 提供健康检查、字表导入、工程导入导出、录音保存、质量检测和语谱图；
  - `/api/health` 已返回字符串能力列表；
  - 当前自行调用 Parselmouth 绘制 F0/F1/F2，和主程序存在重复算法；
  - `/api/audio/save` 和 `/api/audio/analyze` 只返回质量结果与语谱图图片，没有形成主程序可复用的结构化声学缓存。
- `modules/audio_core.py`
  - 已提供主程序使用的 `extract_f0()`、`extract_formants()`、静音边界、VOP/VAD 和自动切分基础能力；
  - 共振峰结果已经包含 F3。
- `modules/project_manager.py` 与 `modules/project_adaptor.py`
  - 已支持 `pitch_data_file`、`formant_data_file`、分析参数及工程资源校验；
  - `.npz` 缓存是主程序现有事实格式。
- `PhonRec/frontend/src/runtimeClient.js`
  - 完整模式和独立模式能力目前由常量硬编码；
  - 未使用引擎实际返回的能力版本。

### 2.2 必须解决的核心缺口

1. 后端声称支持 `pitch`、`formants`，但只把曲线画进 PNG，能力声明与可消费数据不完全一致。
2. 后端与主程序各算一遍 F0/共振峰，参数和算法未来容易漂移。
3. PhonRec 完整模式录完后，PhonTracer 打开工程仍可能需要重新分析。
4. 新旧 PhonRec、引擎和主程序之间只有“协议版本必须完全相等”的粗粒度门禁，无法渐进开放功能。
5. 缺少可验证的音频、缓存、导入导出和交接历史。
6. 没有安全、原子、可定位到具体条目的“一键复核”流程。

## 三、目标架构

```text
PhonRec React
  │
  ├─ 读取动态能力 ───────────────┐
  │                              │
  ├─ 保存录音 / 请求分析          │
  │                              ▼
  └─ 创建复核交接包 ──────> PhonTracerAnalysisEngine
                                  │
                    ┌─────────────┼─────────────┐
                    ▼             ▼             ▼
              统一声学内核     原子缓存写入    完整性/审计服务
                    │             │             │
                    └─────────────┴─────────────┘
                                  │
                                  ▼
                     工作区快照 `.teproj`
                                  │
                                  ▼
                   Tauri 启动 PhonTracer 主程序
                                  │
                                  ▼
                    直接定位发音人与目标词项
```

### 3.1 建议新增的共享模块

新增两个无 GUI 依赖的共享模块：

1. `modules/acoustic_analysis_service.py`
   - 封装声学参数规范化；
   - 调用 `modules.audio_core.extract_f0()` 与 `extract_formants()`；
   - 生成统一分析结果、摘要和缓存负载；
   - 不负责 Matplotlib 绘图、不读写全局工作区、不导入 Tk。
2. `modules/project_integrity.py`
   - 规范化 JSON；
   - 计算音频、缓存、工程状态和参数哈希；
   - 原子写入清单；
   - 追加哈希链日志；
   - 校验旧工程、完整工程和损坏工程。

主程序和 PhonRec 后端都必须调用这两个模块。不得让 `PhonRec/backend/main.py` 成为第二个声学算法事实源。

## 四、统一声学分析内核

### 4.1 公共数据结构

建议定义以下 Python 数据结构；可以使用 `dataclass`，但对外必须转换为普通字典：

```python
AnalysisRequest = {
    "requested": ["pitch", "formants", "summary"],
    "params": {
        "pitch_floor": 75,
        "pitch_ceiling": 600,
        "voicing_threshold": 0.25,
        "very_accurate": True,
        "formant_count": 5,
        "formant_max_hz": 5500.0,
        "formant_window_length": 0.025,
        "formant_pre_emphasis": 50.0,
        "formant_sample_strategy": "整段11点",
        "pts": 11,
        "show_f3": False
    }
}
```

统一分析结果内部结构：

```python
AnalysisBundle = {
    "schema": "phontracer.acoustic-analysis.v1",
    "analysis_id": "UUID",
    "algorithm_version": "audio-core-v1",
    "audio_sha256": "...",
    "params_sha256": "...",
    "params": {...},
    "duration_seconds": 1.23,
    "speech_bounds": {"start": 0.12, "end": 1.08},
    "pitch": {
        "xs": numpy_array,
        "freqs": numpy_array,
        "engine": "praat"
    },
    "formants": {
        "xs": numpy_array,
        "f1": numpy_array,
        "f2": numpy_array,
        "f3": numpy_array,
        "engine": "praat_burg"
    },
    "summary": {
        "voiced_ratio": 0.82,
        "f0_median_hz": 182.4,
        "f0_min_hz": 115.2,
        "f0_max_hz": 263.8,
        "f1_median_hz": 612.0,
        "f2_median_hz": 1428.0,
        "f3_median_hz": 2510.0,
        "warnings": []
    }
}
```

说明：

- NumPy 数组只在 Python 内部和 `.npz` 缓存中使用；HTTP 响应默认只返回摘要、缓存引用和可选的降采样预览，不返回无限长原始数组。
- 语谱图渲染必须消费同一个 `AnalysisBundle`，不能再次计算 Pitch 或 Formant。
- F3 始终可以写入缓存；前端是否展示由 `show_f3` 和能力协商决定。

### 4.2 参数规范化

新增一个统一函数：

```python
normalize_analysis_params(raw_params, speaker_params=None) -> dict
```

优先级固定为：

1. 当前条目显式参数；
2. 当前发音人的 `last_params`；
3. 统一默认值。

函数必须进行类型、上下限和参数关系校验，例如：

- `pitch_floor < pitch_ceiling`；
- `pitch_floor` 与 `pitch_ceiling` 限定在合理范围；
- `formant_count`、`formant_max_hz`、窗长和预加重必须在允许区间；
- 未知字段忽略或返回结构化 422，不能静默写入工程。

### 4.3 主程序接入

将主程序直接调用 `extract_f0()`、`extract_formants()` 的关键入口逐步改为调用统一服务。第一阶段至少覆盖：

- 单条重新分析；
- 批量分析工作线程；
- 导入后首次分析。

主程序的 UI 行为、擦除点逻辑和预览采样不在本次重写范围内。统一服务只替代“计算事实源”，不重构 GUI。

## 五、分析缓存直接写入工程

### 5.1 缓存格式

继续沿用现有主程序格式：

- `data/<speaker>_<item>.npz`
  - `xs`
  - `freqs`
- `data/<speaker>_<item>_formant.npz`
  - `xs`
  - `f1`
  - `f2`
  - `f3`

工程条目写入：

```json
{
  "pitch_data_file": "data/speaker_item.npz",
  "formant_data_file": "data/speaker_item_formant.npz",
  "analysis_params": {},
  "analysis_state": {
    "schema": "phontracer.analysis-state.v1",
    "status": "ready",
    "analysis_id": "UUID",
    "algorithm_version": "audio-core-v1",
    "audio_sha256": "...",
    "params_sha256": "...",
    "analyzed_at": "UTC ISO-8601",
    "warnings": []
  }
}
```

### 5.2 原子写入事务

一次录音分析必须按以下顺序提交：

1. 音频先写入临时文件并 `fsync`；
2. 验证 WAV 可读、采样率和时长；
3. 原子替换正式音频；
4. 计算音频 SHA-256；
5. 运行统一分析服务；
6. 将 Pitch 和 Formant 分别写入临时 `.npz`；
7. 校验临时缓存可重新读取；
8. 原子替换正式缓存；
9. 更新内存中的工程条目；
10. 原子写入 `project.json`；
11. 更新完整性清单；
12. 追加 `analysis_completed` 审计事件。

任何一步失败时：

- 已存在的上一版正式音频和缓存不得被破坏；
- 工程条目不得指向未提交的缓存；
- 写入 `analysis_failed` 日志；
- HTTP 返回结构化错误；
- 临时文件在 `finally` 中清理。

### 5.3 缓存命中与失效

只有同时满足以下条件才可命中缓存：

- 当前音频 SHA-256 等于 `analysis_state.audio_sha256`；
- 规范化参数 SHA-256 等于 `analysis_state.params_sha256`；
- 算法版本相同；
- `.npz` 文件存在且能读取；
- 完整性清单中的缓存哈希一致。

任一条件不满足时，将 `analysis_state.status` 标记为 `stale`，重新分析。不得继续展示旧缓存并伪装成最新结果。

### 5.4 HTTP 接口

保留现有接口并向后兼容：

- `POST /api/audio/save`
  - 保留现有字段；
  - 新增可选 `analysis_params`；
  - 响应新增 `analysis` 摘要、`cache_refs`、`integrity`。
- `POST /api/audio/analyze`
  - 支持可选参数与 `force`；
  - 命中缓存时返回 `cache_hit: true`；
  - 不再重复计算语谱图所需轨迹。
- 新增 `GET /api/audio/analysis?speaker_id=...&word_id=...`
  - 返回摘要、状态、参数、缓存命中信息和降采样预览；
  - 不直接暴露本地绝对路径。

## 六、动态能力协商

### 6.1 兼容策略

不得直接破坏旧 PhonRec。现有 `protocol_version: 1` 暂时保留，并新增可选字段：

```json
{
  "status": "ok",
  "engine_version": "1.3.x",
  "protocol_version": 1,
  "protocol_min": 1,
  "protocol_max": 1,
  "capabilities": ["project-state", "spectrogram", "pitch", "formants"],
  "capability_versions": {
    "project-state": 1,
    "project-archive": 1,
    "wordlist-import": 2,
    "audio-storage": 1,
    "audio-quality": 2,
    "spectrogram": 2,
    "acoustic-analysis": 1,
    "analysis-cache": 1,
    "integrity-manifest": 1,
    "audit-log": 1,
    "phontracer-handoff": 1
  }
}
```

旧客户端会忽略新增字段；新客户端优先读取 `capability_versions`，缺失时回退到旧字符串列表。

### 6.2 必需能力与可选能力

新客户端只把以下能力视为完整模式最低要求：

- `project-state >= 1`
- `audio-storage >= 1`

其他能力均为可选：

- 没有 `spectrogram`：隐藏语谱图页签；
- 没有 `acoustic-analysis`：允许录音，但不显示结构化分析；
- 没有 `analysis-cache`：仍可分析，但标记为不可直接复核；
- 没有 `phontracer-handoff`：隐藏“一键交给 PhonTracer 复核”；
- 没有 `integrity-manifest`：工程显示为“旧版/未校验”，不得判为损坏。

### 6.3 Rust 与前端改动

`PhonRec/frontend/src-tauri/src/main.rs`：

- `EngineHandshake`、`HealthResponse`、`EngineConnection` 增加能力字段并提供默认值；
- 握手和健康检查的能力取交集；
- 若握手未返回能力，以健康检查为准；
- 能力缺失不得导致反序列化失败；
- 保留会话 Token，不允许前端绕过引擎连接对象自行访问其他端口。

`PhonRec/frontend/src/runtimeClient.js`：

- 删除完整模式能力的绝对硬编码；
- 新增 `resolveEngineCapabilities(connection)`；
- 保留独立模式静态能力；
- 对旧引擎提供最小保守回退，不得假定旧引擎支持新功能。

`EngineGate.jsx`：

- 仅在最低必需能力缺失或协议无交集时阻止完整模式；
- 可选能力缺失只显示简短降级提示；
- 错误信息要明确区分“版本不兼容”和“某功能不可用”。

## 七、一键交给 PhonTracer 复核

### 7.1 用户流程

1. 用户在 PhonRec 选择当前录音条目；
2. 点击“交给 PhonTracer 复核”；
3. 后端完成当前工程保存、缓存提交、完整性清单更新；
4. 后端在工作区外创建只读时间点快照 `.teproj`；
5. 返回交接包路径、工程哈希、发音人 ID、词项 ID 和交接 ID；
6. Tauri 校验路径后启动 PhonTracer；
7. PhonTracer 导入快照并自动定位到目标发音人与条目；
8. PhonRec 记录 `handoff_opened`，界面提示“已在 PhonTracer 中打开复核副本”。

本阶段是单向、快照式交接。不得让 PhonRec 和 PhonTracer 同时直接写同一个活动工作区，也不得承诺复核结果自动回写。

### 7.2 交接目录

交接包必须放在工作区同级的受控目录，而不是工作区内部，避免导出自身：

```text
<PhonRec 数据目录>/handoffs/<handoff_id>/
  project.teproj
  handoff.json
```

`handoff.json`：

```json
{
  "schema": "phontracer.handoff.v1",
  "handoff_id": "UUID",
  "created_at": "UTC ISO-8601",
  "source": "PhonRec",
  "project_sha256": "...",
  "speaker_id": "...",
  "item_id": "...",
  "mode": "review",
  "read_only_snapshot": true
}
```

### 7.3 后端接口

新增：

```http
POST /api/handoff/create
Content-Type: application/json

{
  "speaker_id": "...",
  "item_id": "..."
}
```

响应：

```json
{
  "status": "ready",
  "handoff_id": "...",
  "archive_path": "受控绝对路径",
  "manifest_path": "受控绝对路径",
  "project_sha256": "..."
}
```

要求：

- 发音人和条目必须真实存在；
- 当前项目必须先原子保存；
- 所有引用资源必须通过 `validate_project_resources()`；
- 快照必须能重新打开并验证完整性；
- 绝对路径只允许返回给当前 Bearer Token 会话；
- 失败时不得残留半成品目录。

### 7.4 Tauri 启动命令

新增命令：

```rust
open_phontracer_review(archive_path, manifest_path) -> Result<(), String>
```

安全要求：

- `canonicalize()` 两个路径；
- 必须位于受控 `handoffs` 根目录；
- 后缀必须分别为 `.teproj` 和 `.json`；
- 拒绝符号链接、父目录穿越和不存在文件；
- Windows 从安装注册信息取得 PhonTracer 安装目录，启动 `PhonTracer.exe`；
- macOS 定位 `PhonTracer.app`，使用 `/usr/bin/open -a ... --args`；
- 参数必须逐项传递，禁止拼接 shell 命令字符串。

### 7.5 PhonTracer 启动入口

根目录 `main.py` 增加向后兼容参数解析：

```text
PhonTracer.exe <project.teproj> --handoff-manifest <handoff.json>
```

要求：

- 保留原有双击 `.teproj` 与普通路径参数行为；
- 清楚区分用户输入文件和内部参数；
- 导入完成后再根据 manifest 定位发音人与词项；
- 找不到条目时仍打开工程并给出非阻断提示；
- 不信任 manifest 中的工程路径，只使用命令行已验证的 `.teproj`；
- 项目窗口标题或提示中标记“复核副本”，避免用户误以为正在直接修改 PhonRec 工作区。

## 八、数据完整性与操作日志

### 8.1 完整性清单

工作区新增：

```text
integrity/manifest.json
logs/audit.jsonl
```

`manifest.json` 建议结构：

```json
{
  "schema": "phontracer.integrity-manifest.v1",
  "generated_at": "UTC ISO-8601",
  "project_sha256": "project.json 规范化内容哈希",
  "resources": {
    "audio/...wav": {"sha256": "...", "size": 12345},
    "data/...npz": {"sha256": "...", "size": 6789}
  },
  "audit_tail_hash": "..."
}
```

JSON 哈希前必须：

- UTF-8；
- 键排序；
- 固定分隔符；
- 禁止把 `generated_at` 等易变字段计入 `project_sha256`；
- 浮点数和 NaN 必须使用稳定规则处理。

### 8.2 审计日志

每行一个事件，使用哈希链：

```json
{
  "schema": "phontracer.audit-event.v1",
  "event_id": "UUID",
  "timestamp": "UTC ISO-8601",
  "session_id": "UUID",
  "actor": "phonrec|analysis-engine|phontracer",
  "event": "audio_saved",
  "speaker_id": "...",
  "item_id": "...",
  "details": {},
  "prev_hash": "...",
  "event_hash": "..."
}
```

首批必须记录的事件：

- `project_created`
- `project_imported`
- `project_exported`
- `audio_saved`
- `audio_replaced`
- `analysis_started`
- `analysis_completed`
- `analysis_failed`
- `cache_invalidated`
- `integrity_verified`
- `integrity_failed`
- `handoff_created`
- `handoff_opened`
- `workspace_cleared`

日志不得写入 Bearer Token、绝对用户目录、完整录音内容或其他敏感字段。

### 8.3 导入校验策略

- 旧工程没有清单：允许导入，状态为 `legacy_unverified`，首次保存时生成清单。
- 清单存在且全部匹配：正常导入并记录 `integrity_verified`。
- 音频哈希不匹配：默认阻止导入，返回明确的损坏资源列表；不得静默接受。
- 分析缓存哈希不匹配：允许导入音频和工程元数据，但丢弃对应缓存并标记 `stale`，等待重算。
- 审计链断裂：允许只读预览，正式导入前要求用户确认；不得把链断裂等同于恶意篡改。
- 清单自身无法解析或包含路径穿越：拒绝导入。

### 8.4 清理与保留

- 覆盖录音后旧缓存必须失效并清理；
- 审计日志保留替换事件，但不得保留已删除音频副本；
- 默认仅保留最近 20 个交接包或最近 30 天，取先达到者；
- 删除旧交接包要记录清理数量，不记录已删除文件的绝对路径；
- 清空工作区前追加事件，并在新工作区创建新的日志链。

## 九、分阶段实施顺序

### 阶段 A：建立共享内核与契约

改动：

- 新增 `modules/acoustic_analysis_service.py`；
- 新增参数规范化、分析 Bundle、摘要和序列化函数；
- 后端语谱图改为复用 Bundle；
- 主程序关键分析入口改用同一服务；
- 暂不写缓存、不改 UI。

完成标准：同一 WAV 和同一参数经主程序入口与后端入口得到数值等价的 F0/F1/F2/F3。

### 阶段 B：原子缓存与完整性服务

改动：

- 新增 `modules/project_integrity.py`；
- 后端写入主程序格式 `.npz`；
- 写入 `analysis_state`；
- 增加清单和审计日志；
- 完善导入、导出、清理和失败回滚。

完成标准：PhonRec 录制的工程在 PhonTracer 中打开后可直接读取缓存，不触发重复分析。

### 阶段 C：动态能力协商

改动：

- 扩展健康检查与握手结构；
- Rust 保存能力版本；
- 前端根据能力生成运行时能力；
- 新增降级 UI 和兼容测试。

完成标准：新客户端连接旧引擎时仍可使用基础功能；缺少可选能力只降级，不白屏、不崩溃。

### 阶段 D：一键复核交接

改动：

- 新增交接快照接口；
- 新增 Tauri 安全启动命令；
- 主程序解析交接 manifest 并定位条目；
- 前端新增按钮、状态提示和错误处理；
- 接入审计事件。

完成标准：从 PhonRec 当前条目点击一次，可打开 PhonTracer 复核副本并定位到同一发音人和词项。

### 阶段 E：回归、文档与打包

改动：

- 更新 `README.md`、`PhonRec/frontend/README.md` 和必要的用户提示；
- 检查 PyInstaller 收集新共享模块；
- 检查 Windows x64、Windows ARM64、macOS ARM64 工作流；
- 不改变当前平台支持范围。

## 十、文件级改动清单

### 必改

- `modules/acoustic_analysis_service.py`：新增统一分析服务。
- `modules/project_integrity.py`：新增完整性与审计服务。
- `modules/audio_core.py`：只在确有必要时增加纯函数，不复制算法。
- `modules/app.py`：关键分析入口接入统一服务；支持交接定位。
- `modules/project_manager.py`：缓存与完整性字段持久化兼容。
- `modules/project_adaptor.py`：导入校验、旧工程兼容和资源哈希处理。
- `PhonRec/backend/main.py`：API、缓存、清单、日志和交接快照。
- `PhonRec/frontend/src-tauri/src/main.rs`：能力反序列化、安全启动 PhonTracer。
- `PhonRec/frontend/src/runtimeClient.js`：动态能力映射和交接客户端方法。
- `PhonRec/frontend/src/runtimeContext.js` 或 `RuntimeProvider.jsx`：向 UI 暴露能力版本。
- `PhonRec/frontend/src/EngineGate.jsx`：最低能力判断与降级提示。
- `PhonRec/frontend/src/App.jsx`：复核按钮、分析状态、缓存和完整性提示。
- `main.py`：向后兼容的交接参数解析。
- `installer.iss`：确认 Windows 可发现主程序可执行文件；如现有 `InstallDir` 足够则不新增注册项。
- `ToneExtractor_Suite.spec`：确认新增共享模块被收集。

### 必测

- `PhonRec/backend/test_backend.py`
- `tests/test_acoustic_analysis_service.py`（新增）
- `tests/test_project_integrity.py`（新增）
- `tests/test_project_persistence.py` 或现有对应持久化测试
- `PhonRec/frontend/src/runtimeClient.test.js`
- `PhonRec/frontend/src/EngineGate.test.jsx`
- `PhonRec/frontend/src/App.*.test.jsx`
- `PhonRec/frontend/src-tauri/src/main.rs` 内 Rust 单元测试，或拆出可测试模块

## 十一、测试矩阵

### 11.1 Python 单元测试

1. 相同音频、相同参数，统一服务结果稳定且主/后端一致。
2. F0 空值、全静音、短录音、立体声、不同采样率不会崩溃。
3. F1/F2/F3 数组长度与时间轴一致。
4. 参数规范化拒绝非法上下限与未知枚举。
5. 缓存命中、参数变化失效、音频变化失效。
6. 两个 `.npz` 中途写入失败时保留旧缓存。
7. 旧工程无清单可导入并升级。
8. 音频损坏、缓存损坏、日志断链分别执行规定策略。
9. 清单拒绝绝对路径、`..`、符号链接和重复大小写路径。
10. 审计日志哈希链可验证，且不包含 Token。
11. 交接包在工作区外生成，不递归包含自身。
12. 交接包重新导入后资源和缓存均有效。

### 11.2 前端测试

1. 新引擎能力完整时显示“一键复核”。
2. 旧引擎没有能力版本时只启用保守功能。
3. 缺少语谱图能力时自动回退波形页签。
4. 缺少交接能力时不显示按钮。
5. 创建交接失败时保留当前工程和录音状态。
6. 分析缓存命中与重算状态正确展示。
7. 完整性状态区分“已验证、旧版未验证、缓存需重算、工程损坏”。
8. 独立模式行为保持不变。

### 11.3 Rust 测试

1. 旧健康响应缺少能力字段仍可解析。
2. 能力版本取交集并正确传给前端。
3. 交接路径不在受控目录时拒绝。
4. 符号链接和错误扩展名拒绝。
5. Windows 与 macOS 启动参数构造正确。
6. 单元测试不得真正打开外部程序，使用纯函数或可替换启动器。

### 11.4 端到端验收

1. 启动 PhonRec 完整模式，首屏加载动画仍能及时出现。
2. 新建发音人和字表，完成一条录音。
3. 验证工程产生音频、Pitch 缓存、Formant 缓存、清单和审计日志。
4. 重启 PhonRec 后缓存命中。
5. 点击“一键交给 PhonTracer 复核”。
6. PhonTracer 打开复核副本并定位到正确条目。
7. 主程序直接展示已有 F0/F1/F2/F3，不重新计算。
8. 在旧引擎或能力缺失模拟环境中，PhonRec 正常降级。
9. 独立模式仍能录音、保存、导入导出和批量导出 WAV。

## 十二、必须执行的验证命令

Windows 上优先使用稳定的 Python 3.12：

```powershell
C:\Users\Sager\AppData\Local\Programs\Python\Python312\python.exe -m pytest -q tests
C:\Users\Sager\AppData\Local\Programs\Python\Python312\python.exe -m pytest -q PhonRec/backend/test_backend.py
```

前端：

```powershell
cd PhonRec/frontend
npm run lint
npm test -- --run
npm run build
```

Rust/Tauri：

```powershell
cd PhonRec/frontend/src-tauri
cargo fmt --check
cargo test
```

仓库检查：

```powershell
git diff --check
git status --short
git check-ignore -v <每个新增文件>
```

注意：`cargo test` 前必须先完成前端 `npm run build`，以满足 Tauri `frontendDist`。

## 十三、最终验收标准

只有同时满足以下条件才算完成：

- 主程序与后端不再各自实现一套 Pitch/Formant 计算。
- 同一音频、同一参数在两个入口得到数值等价结果。
- PhonRec 完整模式录音后直接产生主程序可读取的 F0/F1/F2/F3 缓存。
- 缓存具备音频、参数、算法版本三重失效判断。
- 新客户端能连接旧引擎并保守降级；可选能力缺失不阻断录音。
- 一键复核使用受控快照，不让两个程序并发写同一工作区。
- PhonTracer 能打开快照并定位到目标条目。
- 旧 `.teproj` 无清单时仍可导入。
- 音频损坏、缓存损坏和日志链断裂有不同且明确的处理策略。
- 审计日志不泄露 Token 和绝对敏感路径。
- 启动白屏修复、独立模式、录音、工程导入导出没有回归。
- Python、前端、Rust 测试及 `git diff --check` 全部通过。
- 文档只声明已经通过测试的能力，不把完整性哈希夸大为真实性证明。

## 十四、建议提交拆分

请不要把全部工作压成一个巨大提交。建议按以下顺序提交：

1. `重构：建立共享声学分析服务`
2. `功能：写入统一分析缓存与完整性清单`
3. `功能：增加引擎动态能力协商`
4. `功能：增加 PhonTracer 复核交接`
5. `测试：补全联动与兼容回归`
6. `文档：说明 PhonRec 与 PhonTracer 联动能力`

每个提交都必须保持可构建、可测试，不得在中间提交留下无法启动的状态。

## 十五、完成后的交付说明模板

执行 Agent 最终应报告：

1. 实际修改的文件和新增模块；
2. 五项功能分别如何落地；
3. 是否存在与本方案不同的设计，以及原因；
4. 旧 PhonRec、旧引擎、旧 `.teproj` 的兼容结果；
5. Windows 和 macOS 的一键复核验证情况；
6. 所有测试命令与通过数量；
7. 工作区是否干净、是否提交、是否推送；
8. 尚未解决的真实限制，不得用“理论支持”代替验证。
